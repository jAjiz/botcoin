# Phase 10 – Trading Tools Integration: Backtest + Optimizer as API endpoints

## Context

- Branch: `feature/phase-10-trading-tools-integration` (to be created from `main`).
- Prior phases delivered: Docker (Phase 1), APScheduler (Phase 2), pytest (Phase 3), PostgreSQL via SQLAlchemy + Alembic (Phase 4), FastAPI + Telegram split (Phase 5), `ruff` lint + format (Phase 6), unified CI/CD with image-based deploy (Phase 7).
- The two analysis scripts shipped with V1 — `trading/backtest.py` and `trading/optimize_params.py` — are still **CLI-only** (`sys.argv` parsing, `print()` output, `sys.exit()` error paths). They are the only modules in the codebase that mutate the global `TRADING_PARAMS` / `PAIRS` / `STOP_PERCENTILES` dicts at runtime — a hazard the live trading loop is currently isolated from only because the scripts are invoked out-of-process.
- This phase folds both into the FastAPI service so they can be invoked as JSON endpoints, with the two scripts no longer mutating live state and the optimizer running in a child process so it cannot stall the live trading loop via the GIL.
- Relevant files to read before starting:
  - `ROADMAP.md` — Phase 10 scope (to be added; placeholder OK).
  - `trading/backtest.py` — current CLI script; engine candidate for refactor.
  - `trading/optimize_params.py` — current CLI script; renamed to `trading/optimizer.py`, exhaustive grid replaced by Optuna TPE.
  - `trading/parameters_manager.py` — `calculate_trading_parameters` and `get_k_stop`; both must keep working for the live bot after the engine refactor.
  - `trading/market_analyzer.py` — `analyze_structural_noise` is the dominant cost for both consumers; receives the same CLI cleanup treatment in the same phase.
  - `core/database.py` — SQLAlchemy models + DAL pattern. New `OptimizerJob` model lives here.
  - `core/runtime.py` — in-memory shared cache pattern; new `pair_calibration` cache is added here.
  - `core/scheduler.py` — `trading_session()` calls `calculate_trading_parameters` every `PARAM_SESSIONS`; the dual-write (globals + cache) goes through that same call site.
  - `api/app.py` — FastAPI app factory + `lifespan`; new optimizer router and orphan-cleanup hook are wired here.
  - `api/schemas.py`, `api/routes/` — schema and router conventions.
  - `scripts/migrations/versions/20260414_01_phase4_initial_schema.py` — Alembic migration style reference.
- Architectural decisions:
  - **Single process for bot + API + backtest. Optimizer runs in a child process.** Backtest on the pure-Python engine is fast enough (sub-second on 60d of 15-min data) to run in the FastAPI request threadpool alongside other sync endpoints. The optimizer is CPU-bound for tens of seconds; running it in-process would steal time from the trading loop via the GIL. `multiprocessing.Process` with the `spawn` context is the worker model. No new infrastructure (no Redis, no Celery, no separate worker container).
  - **Single-slot enforcement.** Only one optimizer job in flight at a time. An in-memory lock (`JobStore._active`) is the authority; the Postgres `optimizer_jobs.status='running'` row records the state for observability and crash recovery, but it does not by itself prevent a concurrent start. New requests while one is running return `409 Conflict`.
  - **Engine refactor as the first commit.** A new `trading/engine.py` with config-as-argument pure functions decouples simulation from module-level globals. The live bot's `parameters_manager.calculate_trading_parameters` keeps writing the same global dicts so `get_k_stop` and `positions_manager` keep working — but it **also** writes a `PairCalibration` (the structural events + ATR percentiles it already computes) into a thread-safe cache in `core.runtime`. Backtest builds its `EngineConfig` from this cache; the optimizer receives a **snapshot of the same calibration through its request payload** (see next point). No global mutation by tuner code, ever.
  - **The optimizer worker is fed the calibration through its request, not the in-process cache.** The optimizer runs in a `spawn`ed child process, which starts with a *fresh, empty* `core.runtime` — the parent's calibration cache is not shared across the process boundary. So `JobStore.try_start` (parent) snapshots `runtime.get_pair_calibration(pair)` and passes it into the worker alongside the request. The worker builds `EngineConfig` from that snapshot. This guarantees the live bot, backtest, and optimizer all evaluate against **identical** calibration data (events + ATR percentiles) for non-sliced requests; date-sliced requests recompute from the slice inside the worker. (Reading the cache directly in the child would silently return `None` and is the bug this design avoids.)
  - **Backtest is sync; optimizer is async with Postgres persistence.** Backtest returns the result inline. Optimizer returns a `job_id`; the result is persisted to a new `optimizer_jobs` table and retrieved via `GET /optimizer/jobs/{id}`. Postgres persistence (instead of in-memory only) is chosen so results survive restarts and run history is queryable for diffing across tuning sessions.
  - **Calibration cache reuse.** `analyze_structural_noise` is the dominant cost for both consumers (~hundreds of ms on 60d of 15-min candles). The live bot already runs it every `PARAM_SESSIONS` (12h) and writes the result to the cache. Backtest reads the cache when its request scope matches (no `start`/`end` slicing); the optimizer receives the same data via its request snapshot. Date-sliced requests recompute fresh. **This phase calibrates on full OHLC history, matching current live-bot behavior** — the auto-lookback window selector is deferred to Phase 11 so this phase introduces no change to live trading behavior.
  - **Orphan cleanup on startup.** Any `optimizer_jobs` row left in `status='running'` after a crash is marked `failed` with `error='interrupted by restart'` during the FastAPI lifespan, before the scheduler starts.
  - **Search algorithm: Optuna TPE, exhaustive grid dropped.** ~300 trials reach the same quality as 130K exhaustive candidates for this search space. The exhaustive path is removed; `STOP_PCT_CHOICES` / `K_ACT_CHOICES` / `MIN_MARGIN_CHOICES` are deleted.
  - **CLI is dropped entirely.** `_parse_args`, `if __name__ == "__main__"`, and `print_*` integrations are removed from `backtest.py`, `optimizer.py` (renamed from `optimize_params.py`), and `market_analyzer.py`. The print helpers in `core/utils.py` (`print_pair_argument_error`, `print_statistics`, `print_events_detail`, `print_structural_noise_results`) lose their last call sites and are deleted with them.
  - **Engine ships as pure Python first; Numba is an optional speedup gated on a benchmark.** Dropping the exhaustive grid (130K → ~300 trials, a ~400× reduction in simulations) may already make both endpoints fast enough. The engine is therefore built and shipped as pure Python (Step 1), then **benchmarked** (Appendix A). Numba JIT is added **only if** the benchmark shows the optimizer exceeds an acceptable wall-clock budget — it is not a baseline dependency, because it pulls a compiled LLVM toolchain, adds cold-start latency, forces a bit-identical-equivalence burden, and is invisible to coverage. Optuna is pinned exactly from the start; Numba is pinned only if Appendix A adopts it.

## Target outcome

```
HTTP layer (single FastAPI process)
┌────────────────────────────────────────────────────────────────┐
│  POST /backtest                  (sync, runs in API threadpool)│
│  POST /optimizer/jobs            (returns job_id immediately)  │
│  GET  /optimizer/jobs/{id}                                     │
│  GET  /optimizer/jobs                                          │
└────────────────┬───────────────────────────────────────────────┘
                 │
                 │ try_start (in-memory lock + INSERT optimizer_jobs)
                 ▼
       ┌─────────────────┐  spawn (req + calibration snapshot)  ┌───────────────────────┐
       │ optimizer.jobs  │────────────────────────────────────► │ optimizer.worker      │
       │   JobStore      │                                      │ run_optimize(req,cal) │
       │   supervisor    │◄───── ProcessPoolExecutor future (result dict | exception)
       └────────┬────────┘                                      └───────────────────────┘
                │
                │ complete/fail + Telegram notify
                ▼
       ┌──────────────────┐
       │  optimizer_jobs  │  (Postgres)
       └──────────────────┘

Live trading loop (in the same process)
   trading_session() every SLEEPING_INTERVAL
     └─ calculate_trading_parameters() every PARAM_SESSIONS
        ├─ analyze_structural_noise(df)         (full history, unchanged behavior)
        ├─ writes TRADING_PARAMS / PAIRS         (live bot read path, unchanged)
        └─ writes runtime.pair_calibration       (events + ATR percentiles; read by backtest, snapshotted for optimizer)
```

After this phase:

- `POST /backtest` returns a populated `BacktestResult` in well under a second on 60d of 15-min data.
- `POST /optimizer/jobs` returns a `job_id` immediately; the optimizer runs in a child process; results persist to Postgres; Telegram notifies on start, finish, and failure.
- A second optimizer submit while one is running returns `409`.
- A crash mid-run leaves the row marked `failed` after the next startup, never `running` indefinitely.
- The two scripts in `trading/` no longer have CLI entry points and never mutate global config.

---

## Step 0 — Dependencies

Add to `requirements.txt` (pinned exactly per project convention; resolve the concrete version via `pip show optuna` after the first build and replace the placeholder):

```
optuna==<resolved>
```

Numba is **not** added here. It is a conditional dependency introduced only if Appendix A's benchmark shows it is needed.

Rebuild the dev and prod images:

```
docker compose -f docker-compose.test.yml build
docker compose build
```

**Commit:** `chore(deps): pin optuna for optimizer search`.

---

## Step 1 — Engine refactor (`trading/engine.py`)

Create `trading/engine.py`. This is the foundation for everything else; ship it as one focused commit before any other code change.

### 1.1 Dataclasses

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class PairCalibration:
    atr_p20: float
    atr_p50: float
    atr_p80: float
    atr_p95: float
    k_stop_buy: dict[str, float | None]   # {level: k}
    k_stop_sell: dict[str, float | None]

@dataclass(frozen=True)
class SidePolicy:
    k_act: float | None
    min_margin: float

@dataclass(frozen=True)
class EngineConfig:
    pair: str
    calibration: PairCalibration
    buy: SidePolicy
    sell: SidePolicy
    atr_desv_limit: float
```

### 1.2 Move pure helpers into `trading/engine.py`

Move from `backtest.py` (delete from there in Step 4):

- `_vol_level_from_atr(atr, p20, p50, p80, p95) -> str`
- `_pnl_abs(prev_side, prev_price, curr_price) -> float`
- The `Operation` dataclass (shared with the new endpoints).

Add new pure helpers (replace the global-reading versions in `backtest.py:65-84`):

```python
def lookup_k_stop(cfg: EngineConfig, side: str, atr_val: float) -> float | None: ...
def activation_price(cfg: EngineConfig, side: str, entry_price: float, atr_val: float) -> float: ...
def stop_price(cfg: EngineConfig, side: str, trailing_price: float, atr_val: float) -> float: ...
```

`lookup_k_stop` reproduces the existing fallback logic from `parameters_manager.get_k_stop:91-113` (try same-side, opposite-side, then neighbor levels) but reads from `cfg.calibration.k_stop_buy/sell` instead of `TRADING_PARAMS`.

### 1.3 New `simulate_operations` signature

```python
def simulate_operations(
    df: pd.DataFrame,
    cfg: EngineConfig,
    fee_rate: float = 0.0,
    max_ops: int | None = None,
) -> list[Operation]:
    ...
```

Logic identical to `backtest.py:108-292`, except:

- ATR thresholds come from `cfg.calibration.atr_p20/50/80/95`.
- `k_act` / `min_margin` come from `cfg.buy` / `cfg.sell` (per side).
- All `get_k_stop` calls become `lookup_k_stop(cfg, side, atr)`.
- No imports from `core.config` or `trading.parameters_manager` — `engine.py` is a leaf module.

### 1.4 Adapter for the live bot

Keep `parameters_manager.calculate_trading_parameters` and `parameters_manager.get_k_stop` as the live-bot read path. Add an adapter in `parameters_manager.py`:

```python
def build_calibration(pair: str) -> PairCalibration:
    """Build a PairCalibration from current globals. Used by the API to seed
    EngineConfig from live state without re-running analyze_structural_noise."""
    return PairCalibration(
        atr_p20=float(PAIRS[pair]["atr_20pct"]),
        atr_p50=float(PAIRS[pair]["atr_50pct"]),
        atr_p80=float(PAIRS[pair]["atr_80pct"]),
        atr_p95=float(PAIRS[pair]["atr_95pct"]),
        k_stop_buy=dict(TRADING_PARAMS[pair]["buy"].get("K_STOP") or {}),
        k_stop_sell=dict(TRADING_PARAMS[pair]["sell"].get("K_STOP") or {}),
    )
```

`get_k_stop` keeps its current global-reading body — **do not** change its public signature in this phase. The live bot path is preserved bit-for-bit; only the new tuner consumers go through `engine.lookup_k_stop`.

### 1.5 Equivalence test

Add `tests/unit/trading/test_engine_equivalence.py`. Build a small fixed OHLC DataFrame (50 rows, hand-checked), build a `PairCalibration` matching the current globals for one pair, run both:

- `simulate_operations(df, cfg, ...)` (new pure path)
- The current `simulate_operations` from `backtest.py` (still there at this point — equivalence test runs against the *pre-refactor* version)

Assert the two return identical `list[Operation]`. The test guards the refactor; once Step 4 deletes the old function, replace this with a frozen-output golden file (pickle the operations from one canonical run and assert structural equality on subsequent runs).

**Commit:** `feat(engine): add trading/engine.py with PairCalibration, EngineConfig, pure simulate_operations`.

---

## Step 2 — Benchmark the pure-Python engine (Numba gate)

**Deferred / conditional.** The engine ships as pure Python (Step 1). Before considering any JIT work, benchmark the real workload — see **Appendix A**. Numba is added only if the optimizer's wall-clock under realistic settings (`n_trials=300`, `split_method="BOTH"`, 60–365d of 15-min data) exceeds the acceptable budget. If the benchmark passes, this step is a no-op and the phase has zero Numba surface.

The benchmark, the JIT design, the equivalence requirements, and the coverage-exclusion handling all live in Appendix A so they don't clutter the mainline steps.

---

## Step 3 — `market_analyzer.py` cleanup + calibration cache

> **Auto-lookback window selection has been split out into Phase 11** (`plan/phase-11-auto-lookback-window.md`). This phase calibrates on full OHLC history, exactly as the live bot does today, so it introduces **no change to live trading behavior**. The calibration cache added here stores events + ATR percentiles; Phase 11 later extends it with `window_days` + sweep metadata.

### 3.1 Drop CLI from `market_analyzer.py`

Delete from `trading/market_analyzer.py`:

- The `import sys` line.
- `from core.utils import print_pair_argument_error, print_structural_noise_results`.
- `get_args()` (lines 211–228) and the `if __name__ == "__main__":` block (lines 231–239).
- The `print_results`, `show_events`, `volatility_level` parameters from `analyze_structural_noise` (lines 170–207) and the `if print_results:` branch.

The final signature is `analyze_structural_noise(df: pd.DataFrame, order: int = DEFAULT_ORDER) -> tuple[list[dict], list[dict]]`.

### 3.2 Drop unused print helpers from `core/utils.py`

After Step 3.1, `print_pair_argument_error`, `print_statistics`, `print_events_detail`, `print_structural_noise_results` have no remaining call sites. Delete them. `now_utc` is the only public function that survives.

Verify with `grep -rn "print_pair_argument_error\|print_statistics\|print_events_detail\|print_structural_noise_results" .` returning nothing in `api/`, `core/`, `trading/`, `services/`, `tests/`.

### 3.3 Add a calibration cache to `core/runtime.py`

Add to the `_shared_data` dict:

```python
"pair_calibration": {},  # {pair: {
                         #   "up_events": list[dict],
                         #   "down_events": list[dict],
                         #   "atr_p20": float, "atr_p50": float, "atr_p80": float, "atr_p95": float,
                         #   "row_count": int,        # rows in the df used to compute these
                         #   "computed_at": datetime,
                         # }}
                         # Phase 11 extends this entry with "window_days" + "window_sweep".
```

Add thread-safe getter/setter following the existing module style:

```python
def update_pair_calibration(
    pair: str,
    up_events: list[dict[str, Any]],
    down_events: list[dict[str, Any]],
    atr_p20: float,
    atr_p50: float,
    atr_p80: float,
    atr_p95: float,
    row_count: int,
) -> None:
    with _lock:
        _shared_data["pair_calibration"][pair] = {
            "up_events": up_events,
            "down_events": down_events,
            "atr_p20": atr_p20, "atr_p50": atr_p50,
            "atr_p80": atr_p80, "atr_p95": atr_p95,
            "row_count": row_count,
            "computed_at": now_utc(),
        }

def get_pair_calibration(pair: str) -> dict[str, Any] | None:
    with _lock:
        entry = _shared_data["pair_calibration"].get(pair)
        return None if entry is None else dict(entry)  # shallow copy, matches existing pattern
```

The shallow copy is sufficient — events are lists of dicts of primitives; the immutable read pattern from Step 2.1.2 of the Phase-5 plan applies here too.

### 3.4 Dual-write the calibration cache from `calculate_trading_parameters`

Do **not** change the calculation logic of `parameters_manager.calculate_trading_parameters` (`parameters_manager.py:38-75`) — it keeps calibrating on full OHLC history and keeps writing the same globals (`PAIRS[pair]` ATR percentiles, `TRADING_PARAMS[pair][side]["K_STOP"]`). Add **one** thing at the end: a write to the calibration cache, reusing the values it already computed.

The events are already in hand (`uptrend_events`, `downtrend_events` from the existing `analyze_structural_noise(df)` call at line 60). The ATR percentiles are already in `PAIRS[pair]`. So the dual-write is:

```python
runtime.update_pair_calibration(
    pair,
    up_events=uptrend_events,
    down_events=downtrend_events,
    atr_p20=float(PAIRS[pair]["atr_20pct"]),
    atr_p50=float(PAIRS[pair]["atr_50pct"]),
    atr_p80=float(PAIRS[pair]["atr_80pct"]),
    atr_p95=float(PAIRS[pair]["atr_95pct"]),
    row_count=len(df),
)
```

This is a dual-write (globals **and** cache) on purpose, and it is purely additive: the live bot's read path is byte-for-byte unchanged, and a new in-process consumer (backtest) can now read the calibration without re-running `analyze_structural_noise`. `import core.runtime as runtime` at the top of the module.

### 3.5 Tests

Add `tests/unit/trading/test_parameters_manager_cache.py`: monkeypatch `db.load_ohlc_data` to return a fixture with synthetic candles; call `calculate_trading_parameters("XBTEUR", infoLog=False)`; assert `runtime.get_pair_calibration("XBTEUR")` is populated, the events lists are non-empty, the four ATR percentiles match `PAIRS["XBTEUR"]`, and `row_count == len(df)`. A second test asserts `get_pair_calibration("UNKNOWN")` returns `None`.

**Commit:** `feat(runtime): structural events calibration cache; refactor market_analyzer to library-only`.

---

## Step 4 — Pure `run_backtest`

### 4.1 Strip `trading/backtest.py`

Delete from `trading/backtest.py`:

- `_parse_args` (lines 11–41).
- `_print_summary`, `_print_operations`, `main` (lines 295–368).
- `if __name__ == "__main__":` block.
- All `import sys`.
- The local copies of helpers moved to `engine.py` in Step 1.

What remains: the file becomes a thin module exporting one public function.

### 4.2 New surface

```python
# trading/backtest.py
from dataclasses import dataclass
import pandas as pd

import core.database as db
import core.runtime as runtime
from core.config import ATR_DESV_LIMIT, CANDLE_TIMEFRAME, TRADING_PARAMS
from trading.engine import EngineConfig, PairCalibration, SidePolicy, simulate_operations, Operation
from trading.market_analyzer import analyze_structural_noise
from trading.parameters_manager import calculate_k_stops

@dataclass(frozen=True)
class BacktestRequest:
    pair: str
    fee_pct: float = 0.0
    start: str | None = None
    end: str | None = None
    max_ops: int | None = None
    use_live_config: bool = False  # if True, read events + ATR percentiles from the calibration cache; skip recompute

@dataclass(frozen=True)
class BacktestResult:
    pair: str
    fee_pct: float
    summary: dict   # {ops_count, pnl_samples, win_rate_pct, total_pnl_eur, total_fees_eur,
                    #  best_op_pnl_eur, worst_op_pnl_eur, avg_op_pnl_eur, median_op_pnl_eur,
                    #  row_count, source: "cache" | "recompute" | "slice"}
    operations: list[Operation]

def run_backtest(req: BacktestRequest) -> BacktestResult:
    ...
```

### 4.3 `EngineConfig` builder for the request

Logic in `run_backtest`:

1. Load full OHLC: `df_full = db.load_ohlc_data(req.pair, CANDLE_TIMEFRAME).dropna(subset=["atr"])`.
2. Determine the working dataframe `df` and the calibration *events + ATR percentiles*, recording `source`:
   - **`"slice"`** — `req.start` or `req.end` is set → date-slice `df_full` and recompute from the slice (cache miss by design).
   - **`"cache"`** — `req.use_live_config` and `runtime.get_pair_calibration(req.pair)` is populated → use the cached `up_events`, `down_events`, and the four cached ATR percentiles. `df = df_full` (full history, matching what the live bot calibrated on).
   - **`"recompute"`** — otherwise → recompute from `df_full`: run `analyze_structural_noise(df_full)` for events **and** compute the four ATR percentiles explicitly:
     ```python
     atr = df_full["atr"].to_numpy(dtype=float)
     atr_p20, atr_p50, atr_p80, atr_p95 = (float(np.percentile(atr, p)) for p in (20, 50, 80, 95))
     ```
     These four percentiles are mandatory for `PairCalibration` and are **not** returned by `analyze_structural_noise` — it discards the ones it computes internally (`market_analyzer.py:221-226`). The `"slice"` path computes them the same way on the sliced frame.
3. Build `PairCalibration`:
   - `atr_p20…p95` come from step 2 (cache fields on the cache path; freshly computed on the slice/recompute paths).
   - `k_stop_buy` / `k_stop_sell` come from `calculate_k_stops(req.pair, down_events)` / `calculate_k_stops(req.pair, up_events)` — i.e. per-pair `STOP_PERCENTILES[req.pair]` applied at this point regardless of source.
4. Build the `EngineConfig` (entry-policy from current globals):
   ```python
   def _coerce_float(v) -> float | None:
       try:
           return float(v) if v is not None and str(v).strip() != "" else None
       except (TypeError, ValueError):
           return None

   side_buy = SidePolicy(
       k_act=_coerce_float(TRADING_PARAMS[req.pair]["buy"].get("K_ACT")),
       min_margin=float(TRADING_PARAMS[req.pair]["buy"].get("MIN_MARGIN") or 0.0),
   )
   side_sell = SidePolicy(...)
   cfg = EngineConfig(req.pair, calibration, buy=side_buy, sell=side_sell, atr_desv_limit=ATR_DESV_LIMIT)
   ```
5. Run `simulate_operations(df, cfg, fee_rate=req.fee_pct/100.0, max_ops=req.max_ops)`.
6. Compute `summary` from the returned operations (translate the existing `_print_summary` body into a dict-returning helper); include `row_count = len(df)` and `source`.
7. Return `BacktestResult`. `source` + `row_count` in the summary let the caller see whether the cache was hit and how much data was simulated.

> **`use_live_config` before the first session.** The cache is empty until the scheduler runs `calculate_trading_parameters` at least once. If `req.use_live_config` is set but the cache is empty, fall through to the `"recompute"` path rather than raising — the result is identical data (full-history calibration), just computed inline. The route never 500s on a cold cache.

### 4.4 Tests

`tests/unit/trading/test_backtest.py`:

- `test_run_backtest_uses_cache_when_no_slicing` — populate `runtime.update_pair_calibration` directly, monkeypatch `analyze_structural_noise` to raise, assert `run_backtest` returns a result without calling `analyze_structural_noise`.
- `test_run_backtest_recomputes_when_sliced` — same setup, but pass `start=…`; assert `analyze_structural_noise` is called.
- `test_run_backtest_summary_shape` — assert the summary dict has the documented keys with correct types.

**Commit:** `feat(backtest): replace CLI with pure run_backtest(req) library entry point`.

---

## Step 5 — Pure `run_optimize` with Optuna

### 5.1 Rename and strip `trading/optimize_params.py` → `trading/optimizer.py`

```
git mv trading/optimize_params.py trading/optimizer.py
```

Delete from the renamed file:

- `_parse_args`, `if __name__ == "__main__":`, `main`.
- `import sys`.
- `STOP_PCT_CHOICES`, `K_ACT_CHOICES`, `MIN_MARGIN_CHOICES` constants.
- `_iter_exhaustive_candidates`.
- All `print(...)` calls and the suggestions-formatting `print` block.
- The `_apply_candidate_mode` and `_apply_current_config` global-mutating helpers.

Keep, then refactor:

- `_quantile_ceiled`, `_k_values_by_level` — pure, keep.
- `_set_pair_atr_thresholds` — convert to `_compute_atr_thresholds(df) -> tuple[float, float, float, float]` returning a tuple instead of mutating `PAIRS`.
- `Candidate`, `Score`, `_robust_key`, `_overall_robust_key`, `_score_run`, `_split_scores_from_single_run` — pure, keep.
- `_format_env_lines` — keep, but now called only from the result-formatter.

### 5.0 Source of events: calibration passed in, never read from the cache

The optimizer runs in a `spawn`ed child process whose `core.runtime` is **empty** — it cannot read the parent's calibration cache. So `run_optimize` takes the calibration as an explicit argument:

```python
def run_optimize(req: OptimizerRequest, calibration: dict | None) -> OptimizerResult: ...
```

- For **non-sliced** requests, `JobStore.try_start` (in the parent) snapshots `runtime.get_pair_calibration(req.pair)` and passes it through to the worker, which forwards it here. The events (`up_events` / `down_events`) and ATR percentiles fed into every Optuna trial are exactly the live bot's, so live bot, backtest, and optimizer all evaluate against identical calibration data.
- For **sliced** requests (`req.start`/`req.end`), or if the snapshot is `None` (cold cache), `calibration` is `None` and the worker recomputes events + ATR percentiles from the slice (or from full history) using the same `analyze_structural_noise` + `np.percentile` pipeline as `run_backtest`'s recompute path.

The search varies only `stop_pcts` per level and (per mode) `k_act` or `min_margin`. The K-value arrays per level (`up_k` / `down_k`, via `_k_values_by_level`) are derived once from those events before the study starts, then reused across all trials.

### 5.2 New `Candidate` → `EngineConfig` adapter

```python
def _build_engine_config(
    pair: str,
    cand: Candidate,
    atr_thresholds: tuple[float, float, float, float],
    up_k: dict[str, np.ndarray],
    down_k: dict[str, np.ndarray],
    atr_desv_limit: float,
) -> EngineConfig:
    sell_k_stop = {lvl: _quantile_ceiled(up_k[lvl], cand.stop_pcts[lvl]) for lvl in LEVELS}
    buy_k_stop = {lvl: _quantile_ceiled(down_k[lvl], cand.stop_pcts[lvl]) for lvl in LEVELS}
    calibration = PairCalibration(
        atr_p20=atr_thresholds[0], atr_p50=atr_thresholds[1],
        atr_p80=atr_thresholds[2], atr_p95=atr_thresholds[3],
        k_stop_buy=buy_k_stop, k_stop_sell=sell_k_stop,
    )
    side = SidePolicy(k_act=cand.k_act, min_margin=cand.min_margin or 0.0)
    return EngineConfig(pair=pair, calibration=calibration, buy=side, sell=side, atr_desv_limit=atr_desv_limit)
```

Pure — never mutates globals.

### 5.3 Optuna search

```python
import optuna
from optuna.samplers import TPESampler

def _build_study(seed: int) -> optuna.Study:
    return optuna.create_study(direction="maximize", sampler=TPESampler(seed=seed))

def _suggest_candidate(trial: optuna.Trial, mode: str) -> Candidate:
    stop_pcts = {
        lvl: trial.suggest_float(f"stop_pct_{lvl}", 0.20, 0.95, step=0.05)
        for lvl in LEVELS
    }
    if mode == "AGGRESSIVE":
        return Candidate(k_act=trial.suggest_float("k_act", 0.0, 3.0, step=0.5),
                         min_margin=None, stop_pcts=stop_pcts)
    return Candidate(k_act=None,
                     min_margin=trial.suggest_float("min_margin", 0.0, 0.01, step=0.001),
                     stop_pcts=stop_pcts)
```

Before the study starts (once per `run_optimize` call): resolve events + ATR percentiles from the `calibration` argument if provided, else recompute from `df` (see §5.0); derive `atr_thresholds` and `up_k`/`down_k` (`_k_values_by_level`) from those events. These are closed over by the objective.

Inside the objective:

1. Build `EngineConfig` from the suggested `Candidate` via `_build_engine_config(...)` using the pre-derived `atr_thresholds`, `up_k`, `down_k`.
2. Run `simulate_operations(df, cfg, ...)` for the in-sample full dataset.
3. Run train/test splits per `req.split_method` (RESET / CONTINUE / BOTH) — reuse `_split_scores_from_single_run` for CONTINUE.
4. Apply `min_ops` / `min_test_ops` constraints — `raise optuna.TrialPruned()` when not met.
5. Return the robust score (`_robust_key` for RESET/CONTINUE, `_overall_robust_key` for BOTH).

### 5.4 New surface

```python
@dataclass(frozen=True)
class OptimizerRequest:
    pair: str
    mode: str                 # "CONSERVATIVE" | "AGGRESSIVE" | "CURRENT"
    fee_pct: float = 0.0
    start: str | None = None
    end: str | None = None
    train_split: float = 1.0
    split_method: str = "RESET"   # "RESET" | "CONTINUE" | "BOTH"
    min_ops: int = 0
    min_test_ops: int = 0
    n_trials: int = 300
    seed: int = 42

@dataclass(frozen=True)
class OptimizerResult:
    pair: str
    mode: str
    split_method: str
    best_candidate: dict      # {k_act, min_margin, stop_pcts: {LL:..., LV:..., ...}}
    scores: dict              # {in_sample_pnl_pct, train_pnl_pct, test_pnl_pct, robust_pnl_pct, ...}
    top_candidates: list[dict]   # top 5
    suggested_env_lines: list[str]
    n_trials_run: int
    n_trials_pruned: int

def run_optimize(req: OptimizerRequest, calibration: dict | None) -> OptimizerResult: ...
```

`calibration` is the snapshot dict from `runtime.get_pair_calibration` (keys `up_events`, `down_events`, `atr_p20…p95`) or `None` (sliced / cold-cache → recompute from `df`). See §5.0.

`mode == "CURRENT"` builds a single Candidate from current `.env`-derived globals (`STOP_PERCENTILES`, `TRADING_PARAMS`), runs one evaluation against the same events, and returns it as the only result — no Optuna search.

### 5.5 Tests

`tests/unit/trading/test_optimizer.py` (pass `calibration=None` so the tests recompute from the fixture df and need no runtime cache):

- `test_run_optimize_smoke_aggressive` — small fixture (200 rows), `n_trials=10`, `mode="AGGRESSIVE"`; assert result has the documented shape and `n_trials_run == 10`.
- `test_run_optimize_no_global_mutation` — snapshot `TRADING_PARAMS["XBTEUR"]` and `PAIRS["XBTEUR"]` before, run a 5-trial search, assert no key changed.
- `test_run_optimize_current_mode` — `mode="CURRENT"`; assert one trial result, `n_trials_run == 1`.
- `test_run_optimize_uses_passed_calibration` — pass a `calibration` dict and monkeypatch `analyze_structural_noise` to raise; assert `run_optimize` completes without recomputing events.

**Commit:** `feat(optimizer): rename and rewrite as Optuna-based pure run_optimize(req)`.

---

## Step 5b — Port `reanchor_activation_price` into the engine

> **Added after review.** The engine shipped in Step 1 is a faithful port of the *old* `backtest.py`, which predates two live features added to `trading/positions_manager.py`: `reanchor_activation_price` and `refresh_position`. Neither was simulated. This step closes the gap for **`reanchor_activation_price`** only — it is pure price/ATR math and ports cleanly. `refresh_position` is intentionally **out of scope**: it re-sizes/drops a position from the live balance + inventory model, which the backtest has no representation of; modeling it is a separate, larger fidelity change.

### Live behavior to reproduce (`positions_manager.py:74-83`)

While a position is **not yet active**, each tick: if the activation price has drifted further from the current price than the expected activation distance, re-anchor the activation price to the current price. It uses the position's stored `activation_atr` (not the live bar ATR) for the distance, and anchors the new activation price off the current price. In `tick_position` it runs **after** the ATR-drift activation recalibration and **before** the activation cross check.

### 5b.1 Add an `activation_distance` helper to `trading/engine.py`

Factor the distance out of `activation_price` so the re-anchor gap check can reuse it:

```python
def activation_distance(cfg: EngineConfig, side: str, reference_price: float, atr_val: float) -> float:
    policy = cfg.sell if side == "sell" else cfg.buy
    k_act = policy.k_act
    if k_act is not None:
        return float(k_act) * atr_val
    k_stop = lookup_k_stop(cfg, side, atr_val) or 0.0
    return float(k_stop) * atr_val + (policy.min_margin * reference_price)


def activation_price(cfg: EngineConfig, side: str, entry_price: float, atr_val: float) -> float:
    distance = activation_distance(cfg, side, entry_price, atr_val)
    if side == "sell":
        return entry_price + distance
    return entry_price - distance
```

This is a pure refactor — `activation_price` keeps the same result.

### 5b.2 Track a per-bar price in `simulate_operations`

The live bot evaluates the re-anchor against `current_price` (a single tick price). The bar-based engine uses intrabar extremes (`high`/`low`) for the activation/stop **crosses** — that is a pre-existing backtest convention and is left unchanged. For the re-anchor's "current price" we use the **bar close** (with the same `close → open → midpoint` fallback already used for the first row), as the single-price analog of the live tick. Inside the bar loop, alongside the existing `high`/`low`/`dtime` extraction:

```python
if "close" in row:
    price = float(row["close"])
elif "open" in row:
    price = float(row["open"])
else:
    price = (high + low) / 2.0
```

### 5b.3 Apply the re-anchor in the not-active branch

In the `if not active:` block, **after** the ATR-drift activation recalibration and **before** the activation cross check, add:

```python
# Re-anchor activation toward current price if it has drifted too far
# (mirrors positions_manager.reanchor_activation_price; uses the stored
# activation_atr, not the current bar ATR).
exp_dist = activation_distance(cfg, side, price, activation_atr)
gap = (activation_px - price) if side == "sell" else (price - activation_px)
if gap > exp_dist:
    activation_px = activation_price(cfg, side, price, activation_atr)
```

`activation_px` and `activation_atr` are always set by this point (the `if activation_px is None:` seed runs earlier in the loop body). No rounding is applied — the engine is unrounded throughout; rounding in the live bot is a persistence detail, not a strategy difference.

### 5b.4 Tests

Add to `tests/unit/trading/test_engine.py`:

- `test_reanchor_pulls_activation_toward_price` — a `k_act`-based config where, while inactive, the price drifts away from the activation target by more than the activation distance; assert the position activates (and exits) on a later bar where, **without** re-anchoring, it would not. Build the no-reanchor baseline by asserting the pre-step behavior is different (or by constructing a frame whose only activation path is via the re-anchored, closer target).
- `test_reanchor_noop_when_within_distance` — a frame where the gap never exceeds the activation distance; assert the operations are identical to the immediate-activation path (re-anchor never fires).
- Re-confirm the existing behavioral tests still pass (the `k_act=0` immediate-activation tests are unaffected because the gap never exceeds a zero distance before the cross fires).

The Step 1 design constraint still holds: `engine.py` stays a leaf module and reads no globals; `simulate_operations` remains pure.

**Commit:** `feat(engine): port reanchor_activation_price into the simulation`.

---

## Step 6 — Postgres `optimizer_jobs` table

### 6.1 SQLAlchemy model

Add to `core/database.py` after `BotControl`:

```python
from sqlalchemy.dialects.postgresql import JSONB, UUID
import uuid

class OptimizerJob(Base):
    __tablename__ = "optimizer_jobs"

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pair         = Column(Text, nullable=False)
    mode         = Column(Text, nullable=False)
    split_method = Column(Text, nullable=False)
    status       = Column(Text, nullable=False)
    request      = Column(JSONB, nullable=False)
    result       = Column(JSONB, nullable=True)
    error        = Column(Text, nullable=True)
    created_at   = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    started_at   = Column(DateTime(timezone=True), nullable=True)
    finished_at  = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('running','completed','failed')", name="ck_opt_jobs_status_valid"),
        CheckConstraint("mode IN ('CONSERVATIVE','AGGRESSIVE','CURRENT')", name="ck_opt_jobs_mode_valid"),
        Index("ix_opt_jobs_created_at_desc", desc(created_at)),
        Index("ix_opt_jobs_status_running", status, postgresql_where=text("status = 'running'")),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "pair": self.pair,
            "mode": self.mode,
            "split_method": self.split_method,
            "status": self.status,
            "request": self.request,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
```

### 6.2 DAL helpers

```python
def create_optimizer_job(pair: str, mode: str, split_method: str, request: dict[str, Any]) -> str:
    """Insert a new job row with status='running' and started_at=now(). Returns job_id."""

def complete_optimizer_job(job_id: str, result: dict[str, Any]) -> None: ...
def fail_optimizer_job(job_id: str, error: str) -> None: ...
def get_optimizer_job(job_id: str) -> dict[str, Any] | None: ...
def list_optimizer_jobs(limit: int = 20) -> list[dict[str, Any]]: ...
def cleanup_orphaned_optimizer_jobs() -> int:
    """Mark every status='running' row as failed with error='interrupted by restart',
    finished_at=now(). Return the row count."""
```

All follow the existing `with get_session() as session:` pattern. Mutating writes propagate (`raise`); reads return `None`/`[]` on error.

### 6.3 Alembic migration

Create `scripts/migrations/versions/<YYYYMMDD>_02_optimizer_jobs.py`:

```python
revision = "<YYYYMMDD>_02"
down_revision = "20260414_01"

def upgrade() -> None:
    op.create_table(
        "optimizer_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("pair", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("split_method", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("request", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('running','completed','failed')", name="ck_opt_jobs_status_valid"),
        sa.CheckConstraint("mode IN ('CONSERVATIVE','AGGRESSIVE','CURRENT')", name="ck_opt_jobs_mode_valid"),
    )
    op.create_index("ix_opt_jobs_created_at_desc", "optimizer_jobs", [sa.text("created_at DESC")])
    op.create_index("ix_opt_jobs_status_running", "optimizer_jobs", ["status"], postgresql_where=sa.text("status = 'running'"))

def downgrade() -> None:
    op.drop_index("ix_opt_jobs_status_running", table_name="optimizer_jobs")
    op.drop_index("ix_opt_jobs_created_at_desc", table_name="optimizer_jobs")
    op.drop_table("optimizer_jobs")
```

`gen_random_uuid()` requires the `pgcrypto` extension. If `pgcrypto` is not already enabled in the deployed Postgres, add `op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")` at the top of `upgrade()`.

### 6.4 Tests

`tests/integration/test_optimizer_jobs_dal.py` (gated by `RUN_DB_INTEGRATION`):

- `test_create_then_complete` — insert, complete, read back; assert status transition.
- `test_create_then_fail` — insert, fail with traceback; assert error column populated.
- `test_cleanup_orphaned` — insert two `running` rows directly, call `cleanup_orphaned_optimizer_jobs`, assert both flipped to `failed` with the canned error and `finished_at` set.

**Commit:** `feat(database): add optimizer_jobs model, DAL helpers, and Alembic migration`.

---

## Step 7 — `optimizer/` package: JobStore + worker + supervisor

New top-level package `optimizer/` (sibling of `trading/`, `core/`, `api/`, `services/`).

### 7.1 `optimizer/worker.py`

Subprocess entry point — must be self-contained because `spawn` re-imports the module fresh in the child:

```python
from dataclasses import asdict

from trading.optimizer import OptimizerRequest, run_optimize

def _worker_func(req_dict: dict, calibration: dict | None) -> dict:
    req = OptimizerRequest(**req_dict)
    result = run_optimize(req, calibration)
    return asdict(result)
```

`_worker_func` returns the result dict directly; exceptions propagate through the `ProcessPoolExecutor` future automatically — no queue, no traceback formatting needed. `calibration` is the snapshot the parent passed in (see §5.0). It must be picklable — the cache entry is plain dicts/lists/floats and a `datetime`, all of which pickle cleanly across the `spawn` boundary. (Drop the `computed_at` datetime before sending if you prefer to keep the payload to pure JSON-native types; `run_optimize` doesn't read it.)

### 7.2 `optimizer/jobs.py` — `JobStore`

```python
import asyncio
import threading
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import dataclass
from multiprocessing import get_context

import core.database as db
import core.logging as logging
import core.runtime as runtime
from optimizer.worker import _worker_func

_EXECUTOR = ProcessPoolExecutor(max_workers=1, mp_context=get_context("spawn"))

@dataclass
class _ActiveJob:
    job_id: str
    future: Future
    pair: str

class OptimizerBusyError(Exception):
    """Raised when a new submission arrives while an optimization is already running."""

class JobStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: _ActiveJob | None = None

    def try_start(self, req) -> str:
        """Atomically: confirm slot is free, INSERT optimizer_jobs row, submit worker.
        Returns job_id. Raises OptimizerBusyError if another job is running.

        Snapshots the live calibration here (in the parent) and passes it to the
        worker, because the spawned child starts with an empty core.runtime and
        cannot read the cache itself (see §5.0). Sliced requests get None and the
        worker recomputes from the slice."""
        with self._lock:
            if self._active is not None and not self._active.future.done():
                raise OptimizerBusyError(f"Optimizer job {self._active.job_id} is already running")
            calibration = None
            if not req.start and not req.end:
                calibration = runtime.get_pair_calibration(req.pair)  # may be None on a cold cache
            job_id = db.create_optimizer_job(
                pair=req.pair, mode=req.mode, split_method=req.split_method,
                request=req.__dict__,
            )
            future = _EXECUTOR.submit(_worker_func, req.__dict__, calibration)
            self._active = _ActiveJob(job_id=job_id, future=future, pair=req.pair)
            return job_id

    async def supervise(self) -> None:
        """Awaits the active job's future and persists the result. Called once
        per job via asyncio.create_task() immediately after try_start()."""
        active = self._snapshot_active()
        if active is None:
            return
        try:
            result = await asyncio.wrap_future(active.future)
            self._finalize(active, "ok", result)
        except Exception as exc:
            self._finalize(active, "error", str(exc))

    def _snapshot_active(self) -> _ActiveJob | None:
        with self._lock:
            return self._active

    def _finalize(self, active: _ActiveJob, kind: str, payload) -> None:
        try:
            if kind == "ok":
                db.complete_optimizer_job(active.job_id, payload)
                logging.info(
                    f"✅ [Optimizer] Completed for {active.pair} (job={active.job_id}). "
                    f"Best: pnl={payload['scores'].get('robust_pnl_pct', 0):.2f}%",
                    to_telegram=True,
                )
            else:
                db.fail_optimizer_job(active.job_id, str(payload))
                logging.error(
                    f"❌ [Optimizer] Failed for {active.pair} (job={active.job_id})",
                    to_telegram=True,
                )
        finally:
            with self._lock:
                self._active = None

    def shutdown(self) -> None:
        """Called from FastAPI lifespan finally block. Cancel pending work and
        mark any running job as failed in the DB; process cleanup is left to Docker."""
        with self._lock:
            active = self._active
        if active is None:
            return
        _EXECUTOR.shutdown(wait=False, cancel_futures=True)
        db.fail_optimizer_job(active.job_id, "interrupted by shutdown")
        with self._lock:
            self._active = None

JOB_STORE = JobStore()
```

**Design rationale:** `ProcessPoolExecutor` with `mp_context=get_context("spawn")` replaces the `mp.Process` + `mp.Queue` pair. The worker returns its result directly; the future carries success or exception with no queue-drain race condition. `supervise()` is a one-shot coroutine (no loop) launched via `asyncio.create_task()` in the route handler immediately after `try_start()` — its lifetime matches the job, not the app. `shutdown()` calls `_EXECUTOR.shutdown(cancel_futures=True)` for pending work; running workers are left to Docker's container-level cleanup (all processes in the namespace are killed when the container stops).

### 7.3 Telegram start-notification

The start notification fires from `try_start`, immediately after `create_optimizer_job` returns:

```python
logging.info(
    f"🔧 [Optimizer] Started for {req.pair} (mode={req.mode}, split={req.split_method}, job={job_id})",
    to_telegram=True,
)
```

### 7.4 Tests

`tests/unit/optimizer/test_jobs.py`:

- `test_try_start_inserts_row_and_returns_id` — monkeypatch `db.create_optimizer_job` and patch `optimizer.jobs._EXECUTOR` to record the `submit` call; assert call shape and returned job_id.
- `test_try_start_busy_raises` — populate `JobStore._active` with a `MagicMock` future whose `.done()` returns `False`; assert second `try_start` raises `OptimizerBusyError`.
- `test_finalize_completes_job` — drive `_finalize` directly with `("ok", payload)`; assert `complete_optimizer_job` is called and the slot is cleared.
- `test_finalize_failed_job` — drive with `("error", "boom")`; assert `fail_optimizer_job` is called.
- `test_supervise_ok` — set `_active` to an `_ActiveJob` with an already-resolved `concurrent.futures.Future`; `asyncio.run(store.supervise())`; assert `complete_optimizer_job` was called and slot is cleared.
- `test_supervise_error` — same with a future whose exception is set; assert `fail_optimizer_job` was called.

Helpers: `_resolved_future(result)` and `_failed_future(exc)` build pre-settled `concurrent.futures.Future` objects, making test intent explicit without mocking queues.

**Commit:** `feat(optimizer): JobStore with multiprocessing.spawn worker, supervisor, and Telegram hooks`.

---

## Step 8 — API endpoints + lifespan integration

### 8.1 New schemas in `api/schemas.py`

```python
class BacktestRequest(BaseModel):
    pair: str
    fee_pct: float = 0.0
    start: str | None = None
    end: str | None = None
    max_ops: int | None = None
    use_live_config: bool = False

class OperationDTO(BaseModel):
    idx: int
    time: str
    side: str
    price: float
    vol: str
    k_stop: float
    fee_abs: float
    pnl_abs: float | None
    pnl_pct: float | None
    cum_pnl: float | None

class BacktestResponse(BaseModel):
    pair: str
    fee_pct: float
    summary: dict[str, float | int]
    operations: list[OperationDTO]

class OptimizerRequest(BaseModel):
    pair: str
    mode: Literal["CONSERVATIVE", "AGGRESSIVE", "CURRENT"]
    fee_pct: float = 0.0
    start: str | None = None
    end: str | None = None
    train_split: float = Field(default=1.0, ge=0.5, le=1.0)
    split_method: Literal["RESET", "CONTINUE", "BOTH"] = "RESET"
    min_ops: int = 0
    min_test_ops: int = 0
    n_trials: int = Field(default=300, ge=1, le=10_000)
    seed: int = 42

class OptimizerJobAcceptedResponse(BaseModel):
    job_id: str
    status: Literal["running"] = "running"

class OptimizerJobStatusResponse(BaseModel):
    job_id: str
    pair: str
    mode: str
    split_method: str
    status: Literal["running", "completed", "failed"]
    request: dict
    result: dict | None = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
```

Pydantic validators (`Literal`, `Field(ge=…, le=…)`) replace the manual checks from `optimize_params._parse_args:70-77`. Pair existence is validated at the route level (`pair not in PAIRS → 400`).

### 8.2 New routes

`api/routes/backtest.py`:

```python
from fastapi import APIRouter, HTTPException
from api.schemas import BacktestRequest, BacktestResponse
from core.config import PAIRS
from trading.backtest import run_backtest, BacktestRequest as DTORequest

router = APIRouter(tags=["backtest"])

@router.post("/backtest", response_model=BacktestResponse)
def post_backtest(req: BacktestRequest) -> BacktestResponse:
    if req.pair not in PAIRS:
        raise HTTPException(status_code=400, detail=f"Unknown pair: {req.pair}")
    result = run_backtest(DTORequest(**req.model_dump()))
    return BacktestResponse(
        pair=result.pair, fee_pct=result.fee_pct,
        summary=result.summary,
        operations=[OperationDTO(**op.__dict__) for op in result.operations],
    )
```

`api/routes/optimizer.py`:

```python
from fastapi import APIRouter, HTTPException, Query
from api.schemas import OptimizerRequest, OptimizerJobAcceptedResponse, OptimizerJobStatusResponse
from core.config import PAIRS
import core.database as db
from optimizer.jobs import JOB_STORE, OptimizerBusyError
from trading.optimizer import OptimizerRequest as DTORequest

router = APIRouter(prefix="/optimizer", tags=["optimizer"])

@router.post("/jobs", response_model=OptimizerJobAcceptedResponse, status_code=202)
async def submit(req: OptimizerRequest) -> OptimizerJobAcceptedResponse:
    if req.pair not in PAIRS:
        raise HTTPException(status_code=400, detail=f"Unknown pair: {req.pair}")
    try:
        job_id = JOB_STORE.try_start(DTORequest(**req.model_dump()))
    except OptimizerBusyError as e:
        raise HTTPException(status_code=409, detail=str(e))
    asyncio.create_task(JOB_STORE.supervise())
    return OptimizerJobAcceptedResponse(job_id=job_id)

@router.get("/jobs/{job_id}", response_model=OptimizerJobStatusResponse)
def get_job(job_id: str) -> OptimizerJobStatusResponse:
    row = db.get_optimizer_job(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    return OptimizerJobStatusResponse(**row)

@router.get("/jobs", response_model=list[OptimizerJobStatusResponse])
def list_jobs(limit: int = Query(default=20, ge=1, le=100)) -> list[OptimizerJobStatusResponse]:
    return [OptimizerJobStatusResponse(**row) for row in db.list_optimizer_jobs(limit=limit)]
```

### 8.3 Wire into `api/app.py`

Two edits to `api/app.py`:

1. Inside `lifespan`, before `scheduler.start()`:

   ```python
   cleaned = db.cleanup_orphaned_optimizer_jobs()
   if cleaned:
       logging.warning(f"Cleaned up {cleaned} orphaned optimizer jobs from previous run.")
   ```

2. Register the `JobStore` shutdown in the lifespan finally block:

   ```python
   from optimizer.jobs import JOB_STORE
   try:
       yield
   finally:
       JOB_STORE.shutdown()
       scheduler.shutdown(wait=True)
   ```

   `supervise()` is no longer a long-lived task started at boot. It is spawned per-job via `asyncio.create_task()` in the route handler immediately after `try_start()`, so no task handle is needed here.

3. Register the routers in the `for _r in (...)` loop:

   ```python
   from api.routes import backtest as backtest_route, optimizer as optimizer_route
   for _r in (balance, control, market, positions, status, backtest_route, optimizer_route):
       app.include_router(_r.router, dependencies=_auth)
   ```

### 8.4 Tests

`tests/unit/api/test_backtest_route.py`:

- `test_post_backtest_unknown_pair_returns_400` — assert 400 + detail.
- `test_post_backtest_returns_summary_and_operations` — monkeypatch `run_backtest` to return a fixed `BacktestResult`; assert response shape matches schema.

`tests/unit/api/test_optimizer_route.py`:

- `test_submit_unknown_pair_returns_400`.
- `test_submit_returns_202_with_job_id` — monkeypatch `JOB_STORE.try_start` to return a known UUID.
- `test_submit_busy_returns_409` — monkeypatch `try_start` to raise `OptimizerBusyError`.
- `test_get_job_404_when_unknown`.
- `test_get_job_returns_status` — monkeypatch `db.get_optimizer_job` to return three different statuses; assert correct serialization for each.

**Commit:** `feat(api): /backtest sync endpoint + /optimizer/jobs async endpoints`.

---

## Step 9 — Documentation

Update `README.md`:

- Add a new subsection under the existing API documentation section listing the four new endpoints, the request/response shapes, and a note that `/optimizer/jobs` returns 409 when an optimization is already running.
- Only if Appendix A adopted Numba: add a one-line note under "Code quality" explaining that `numba` requires LLVM; the dev image already ships it and no host install is needed.

`ROADMAP.md` already carries the Phase 10 entry; reconcile its scope/success-criteria bullets with what actually shipped (in particular, drop the auto-lookback bullet — that moved to Phase 11 — and reflect whether Numba was adopted).

**Commit:** `docs: add Phase 10 trading-tools API to README and ROADMAP`.

---

## Step 10 — Final verification

Inside Docker:

```
docker compose -f docker-compose.test.yml run --rm test ruff check .
docker compose -f docker-compose.test.yml run --rm test ruff format --check .
docker compose -f docker-compose.test.yml run --rm test pytest tests/unit
docker compose -f docker-compose.test.yml run --rm test pytest tests/integration   # if Kraken creds + DB
```

End-to-end smoke against a running stack:

```
docker compose up -d
sleep 90   # let the scheduler tick at least once and run calculate_trading_parameters

# Backtest — should respond in well under a second on 60d of 15-min data.
curl -X POST http://localhost:8000/backtest \
  -H "X-Api-Token: $API_SECRET_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"pair":"XBTEUR","fee_pct":0.26}'

# Optimizer — submit, then poll.
JOB=$(curl -s -X POST http://localhost:8000/optimizer/jobs \
  -H "X-Api-Token: $API_SECRET_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"pair":"XBTEUR","mode":"AGGRESSIVE","split_method":"BOTH","train_split":0.7,"n_trials":50}' \
  | jq -r .job_id)
echo "job=$JOB"
curl -s http://localhost:8000/optimizer/jobs/$JOB -H "X-Api-Token: $API_SECRET_TOKEN" | jq .status
# Submitting a second job while the first is running:
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8000/optimizer/jobs \
  -H "X-Api-Token: $API_SECRET_TOKEN" -H "Content-Type: application/json" \
  -d '{"pair":"XBTEUR","mode":"CURRENT","split_method":"RESET","n_trials":1}'
# Expect: 409.

# Telegram should have received: "Started", then "Completed".
docker compose down
```

Failure-mode check: while an optimizer job is running, `docker compose restart botc`. After restart, `GET /optimizer/jobs/$JOB` must return `status: "failed"` with `error: "interrupted by restart"` (or "interrupted by shutdown" if the lifespan finally block won the race).

---

## Execution order (commits)

Each bullet is one focused commit. After each, run `pytest tests/unit` and `ruff check .` inside Docker.

1. `chore(deps): pin optuna for optimizer search`
2. `feat(engine): add trading/engine.py with PairCalibration, EngineConfig, pure simulate_operations`
3. `feat(runtime): structural events calibration cache; refactor market_analyzer to library-only`
4. `feat(backtest): replace CLI with pure run_backtest(req) library entry point`
5. `feat(optimizer): rename and rewrite as Optuna-based pure run_optimize(req)`
6. `feat(engine): port reanchor_activation_price into the simulation`
7. `feat(database): add optimizer_jobs model, DAL helpers, and Alembic migration`
8. `feat(optimizer): JobStore with multiprocessing.spawn worker, supervisor, and Telegram hooks`
9. `feat(api): /backtest sync endpoint + /optimizer/jobs async endpoints`
10. `docs: add Phase 10 trading-tools API to README`

Optional, only if Appendix A's benchmark fails the budget:

11. `perf(engine): JIT-compile simulate_operations core with Numba` (adds `numba==<resolved>` to `requirements.txt` and the coverage exclusion)

---

## Acceptance checklist

Run all of these before opening the PR:

- [ ] `requirements.txt` pins `optuna==<resolved>` with a concrete version resolved via `pip show`. (`numba` is present **only** if Appendix A adopted it.)
- [ ] `trading/engine.py` exists; its `simulate_operations` is pure Python (or `@njit`-backed only if Appendix A applied) and reads no module-level globals.
- [ ] `trading/backtest.py` exports `run_backtest(req) -> BacktestResult` and contains no `sys.argv`, no `print`, no `__main__` block. Its recompute/slice paths compute the four ATR percentiles explicitly (not via `analyze_structural_noise`).
- [ ] `trading/optimizer.py` exists (renamed from `optimize_params.py`), exports `run_optimize(req, calibration) -> OptimizerResult`, uses Optuna TPE, contains no exhaustive grid constants.
- [ ] `trading/market_analyzer.py` no longer takes `print_results` / `show_events` / `volatility_level` parameters and has no `__main__` block.
- [ ] `core/utils.py` no longer contains `print_pair_argument_error`, `print_statistics`, `print_events_detail`, `print_structural_noise_results`. Only `now_utc` remains as a public function.
- [ ] `core/runtime.py` exposes `update_pair_calibration` and `get_pair_calibration` and the underlying `_shared_data["pair_calibration"]` slot (events + ATR percentiles + `row_count`). No `window_days` field in this phase — that arrives in Phase 11.
- [ ] `parameters_manager.calculate_trading_parameters` is unchanged in its calculation logic (still full-history calibration) and adds **only** the additive `runtime.update_pair_calibration(...)` dual-write. No `_select_lookback_window`, no slicing, no behavior change to live K_STOP values.
- [ ] The optimizer never reads `runtime.get_pair_calibration` from inside the worker; the parent snapshots it in `JobStore.try_start` and passes it through `_entrypoint` → `run_optimize(req, calibration)`. Sliced/cold-cache requests pass `None` and recompute.
- [ ] `optimizer_jobs` table exists; the Alembic upgrade and downgrade both run cleanly against an empty Postgres.
- [ ] `optimizer/jobs.py:JobStore` enforces single-slot via the in-memory lock; the `optimizer_jobs.status` row records state for observability and crash recovery.
- [ ] FastAPI lifespan calls `cleanup_orphaned_optimizer_jobs()` before scheduler start and `JOB_STORE.shutdown()` on exit.
- [ ] `POST /optimizer/jobs` returns `202` with a `job_id` and `409` when busy. `GET /optimizer/jobs/{id}` returns 404 for unknown IDs and the right status otherwise.
- [ ] `POST /backtest` returns a populated `BacktestResponse` in under 1 s on 60 days of 15-min OHLC for a typical pair.
- [ ] The optimizer route handler calls `asyncio.create_task(JOB_STORE.supervise())` immediately after `try_start()`. `supervise()` is a one-shot coroutine — no polling loop, no long-lived task at boot.
- [ ] Telegram receives a "Started" message at submit and a "Completed" or "Failed" message when the worker finishes.
- [ ] Crash test: kill `botc` mid-optimization; after restart, the affected row is `failed` with the documented `error` text. No row is left as `running` after startup.
- [ ] `grep -rn "TRADING_PARAMS\[" trading/backtest.py trading/optimizer.py` returns nothing — neither file mutates global trading config.
- [ ] `grep -rn 'if __name__ == "__main__"' trading/` returns nothing.
- [ ] `pytest tests/unit` passes with the existing 80% coverage gate (new modules contribute their share).

---

## Non-goals for this phase

Explicitly out of scope — do not add any of these:

- **A separate `tuner` container.** The single-process model is intentional; revisit only if optimizer runs become frequent enough to noticeably impact live trading latency.
- **Live application of optimization results.** The result endpoint returns suggested `.env` lines; the operator copies them and redeploys. Hot-reload of `TRADING_PARAMS` from the DB is a future phase.
- **Progress streaming (SSE / WebSockets / polling progress %).** Submit-then-final is the contract for this phase.
- **Multi-pair batch optimization.** One pair per submission. Iterate from the client if you need a sweep.
- **Optuna persistent storage** (its built-in SQLite/Postgres study backend). The in-memory study is sufficient because results land in our own `optimizer_jobs` table.
- **Generalizing the JobStore for other long-running jobs.** It is optimizer-specific; if a second consumer ever needs a job queue, extract then.
- **Replacing the in-memory calibration cache with a Postgres table.** The cache is rebuilt every `PARAM_SESSIONS` by the live bot; persistence buys nothing.
- **Webhook-style callback URLs on job completion.** Telegram is the only notification channel.
- **Email delivery of results.** Mentioned as a future enhancement only — out of scope here.
- **`mypy` / `pyright` enforcement on the new modules.** Phase-level convention from Phase 6 stands.
- **Backtest `CONTINUE` / `RESET` / `BOTH` split selection.** Backtest is single-shot; only the optimizer takes a `split_method`.
- **Auto-lookback window selection / any change to live-bot K_STOP calibration.** This phase keeps full-history calibration. The K_STOP stability sweep, the candidate-window set, the plateau heuristic, and per-level vs single window questions all live in **Phase 11** (`plan/phase-11-auto-lookback-window.md`).
- **Numba as a baseline dependency.** Pure-Python engine first; Numba only via Appendix A's benchmark gate.
- **CHANGELOG entry** (Phase 9 owns the changelog introduction).

---

## Appendix A — Numba speedup (conditional, gated on a benchmark)

The engine ships as pure Python. Numba is added **only if** the benchmark below shows the optimizer is too slow. Do this measurement before writing any JIT code.

### A.1 Benchmark

After Step 5 lands, on a representative dataset (one liquid pair, 60d / 180d / 365d of 15-min candles), time `run_optimize` at the realistic worst case: `mode="AGGRESSIVE"`, `split_method="BOTH"`, `n_trials=300`. Record wall-clock per run.

**Budget:** the optimizer should finish in **≤ ~60 s** on 180d. (It runs in a child process and does not block the live loop, so this is a UX bound on "submit and poll," not a hard latency requirement.) If pure Python is already under budget, **stop — no Numba.** Note the measured numbers in the PR description either way.

### A.2 If the budget fails — split the engine into a JIT core

Add `numba==<resolved>` to `requirements.txt` (resolve via `pip show numba` after the first build; verify `python -c "import numba; print(numba.__version__)"` inside the dev image — Numba pulls a compiled LLVM toolchain and is the most likely dependency to break the build). Then split `simulate_operations` into:

**Pure-Python wrapper** (same signature): extract `df["high"]`, `df["low"]`, `df["atr"]` to `np.float64` arrays once; pull `dtime` to a `list[str]`; build length-5 `k_stop_buy`/`k_stop_sell` float arrays indexed by level (`LL=0…HH=4`, `None`→`NaN`); call the JIT core; reconstruct `list[Operation]` from the returned arrays + time indices.

**JIT core** `@njit(cache=True)`:

```python
from numba import njit

@njit(cache=True)
def _simulate_core(
    highs, lows, atrs,                 # float64 arrays
    atr_p20, atr_p50, atr_p80, atr_p95,
    k_stop_buy, k_stop_sell,           # length-5 float64, NaN == missing
    k_act_buy, k_act_sell,             # NaN == use min_margin path
    min_margin_buy, min_margin_sell,
    atr_desv_limit, fee_rate,
    max_ops,                           # 0 == unbounded
):  # -> (result_rows, time_indices)
    ...
```

Logic mirrors the current Python loop (`backtest.py:163-290`). Numba does not support `dict`, `pd.Series`, or string ops — keep the body to scalars, NumPy arrays, and integer codes. The level-fallback in `lookup_k_stop` becomes a small JIT helper scanning the `k_stop_*` arrays for the nearest non-`NaN` entry. Use default `fastmath=False` so scalar ops match the Python reference.

### A.3 Equivalence + coverage

- The Step 1.5 equivalence test must still pass against the JIT path. Add a parametrize axis over several `(fee_rate, max_ops)` combinations. Run the equivalence test with `NUMBA_DISABLE_JIT=1` so it exercises the real Python body, then once more JIT-enabled to confirm the compiled path agrees with the reference (tolerance comparison if any drift appears; with `fastmath=False` it should be exact).
- **Coverage:** add the JIT core to the coverage-exclusion list in `pyproject.toml` (alongside `core/scheduler.py`, `trading/backtest.py`, `trading/optimize_params.py`). Once compiled, `coverage.py` can't trace the `@njit` body, so it would otherwise read as uncovered and threaten the 80% gate.

### A.4 Cold-start

Numba's first call on a fresh `__pycache__` compiles (~1–3 s); `cache=True` persists the artefact, but the first call after a fresh image build pays the cost. Acceptable: the live bot never calls the engine — only backtest/optimizer do — so the warm-up lands on the first request after deploy.

**Commit (only if adopted):** `perf(engine): JIT-compile simulate_operations core with Numba`.
