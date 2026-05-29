# Phase 11 – Auto-Lookback Window for K_STOP Calibration

## Context

- Branch: `feature/phase-11-auto-lookback-window` (to be created from `main`, after Phase 10 has merged).
- **This phase changes live trading behavior.** Today `parameters_manager.calculate_trading_parameters` calibrates K_STOP on *all* available OHLC history. This phase makes the lookback window data-driven, which shifts the K_STOP ladder per pair, hence stop distances, hence which exits fire. Per `CLAUDE.md`, this is a strategy change, not a refactor — it is deliberately isolated in its own phase with explicit before/after validation, and must not be folded into a tooling phase.
- **Depends on Phase 10** (`plan/phase-10-trading-tools-integration.md`):
  - The pure engine (`trading/engine.py`) and the `run_backtest` / `run_optimize` entry points exist.
  - The calibration cache in `core/runtime.py` exists, storing `up_events` / `down_events` / ATR percentiles / `row_count`. This phase **extends** that cache entry with `window_days` + `window_sweep`.
  - The backtest/optimizer endpoints exist and are used to validate that the calibration change is non-regressive on historical PnL.
- Relevant files to read before starting:
  - `trading/parameters_manager.py` — `calculate_trading_parameters`, `calculate_k_stops`, `get_k_stop`. The selector and the percentile-argument refactor land here.
  - `trading/market_analyzer.py` — `analyze_structural_noise(df)` (already library-only after Phase 10); the dominant cost, run once per candidate window.
  - `core/runtime.py` — `update_pair_calibration` / `get_pair_calibration` from Phase 10; gains two fields.
  - `core/config.py` — `CANDLE_TIMEFRAME`, `STOP_PERCENTILES`, `VOLATILITY_LEVELS` (`LEVELS`).
  - `trading/backtest.py`, `trading/optimizer.py` — consumers that surface `window_days` in their responses.

## Architectural decisions

- **Window selection is a calibration pre-step, not a search dimension.** It is owned by `parameters_manager` and runs once per recalibration. The optimizer never tunes the window — mixing window length with stop-percentile choice would conflate two distinct objectives (recent-data informativeness vs. stop sizing) and inflate the search space. The optimizer consumes the already-selected window via the calibration snapshot it receives (Phase 10's mechanism).
- **Single window per pair, not per volatility level.** Data is too thin at HV/HH for per-level stability to be meaningful; a per-level window would add a tuning surface no consumer benefits from.
- **Plateau heuristic over elbow detection.** A simple "agree within a relative tolerance across the next N longer windows" check is sufficient and adds no dependency (`kneed` and friends are out of scope).
- **Fixed plateau tolerance.** `LOOKBACK_PLATEAU_TOL = 0.10` is a module-level constant; making it pair-specific or self-tuning is a future concern.
- **Neutral percentile for the stability check.** The sweep compares K_STOP computed at a fixed neutral percentile (`0.90` across all levels), so the stability signal is independent of the per-pair `STOP_PERCENTILES` choice. Otherwise window-driven variance and percentile-driven variance would be mixed and the plateau signal muddied. The *final* K_STOP that gets written to globals still uses the real per-pair percentiles, applied to events from the selected window.

## Target outcome

```
calculate_trading_parameters(pair) every PARAM_SESSIONS
  ├─ df_full = load_ohlc_data(pair).dropna(atr)
  ├─ window_days, sweep = _select_lookback_window(df_full)     ← NEW (K_STOP stability sweep)
  ├─ df = df_full.tail(window_days * candles_per_day)          ← NEW (slice before pipeline)
  ├─ percentiles + analyze_structural_noise + calculate_k_stops  ON df  (was: on df_full)
  ├─ writes TRADING_PARAMS / PAIRS                              (same dicts, now from the sliced df)
  └─ runtime.update_pair_calibration(..., window_days, window_sweep)  ← cache gains 2 fields
```

After this phase, with ≥ 1 year of OHLC for a pair, the chosen `window_days` is one of the candidate values (a plateau was found) or a logged fallback to the longest feasible window. Live bot, backtest, and optimizer all consume the same selected window via the calibration cache / snapshot, so they cannot disagree on what "recent" means.

---

## Step 1 — Percentile-argument refactor of `calculate_k_stops`

`calculate_k_stops` (`parameters_manager.py:14-35`) currently reads `STOP_PERCENTILES[pair]` from globals. Split it so the percentile dict is an argument; the sweep needs to call it with a neutral percentile while the live path keeps using the per-pair one.

```python
def calculate_k_stops_for_events(
    events: list[dict[str, Any]],
    percentiles: dict[str, float],
) -> dict[str, float | None]:
    """Pure: percentile of observed K-values per level for the given events,
    using the provided per-level percentile dict. Returns None for empty levels."""
    # (body = current calculate_k_stops, with STOP_PERCENTILES[pair][lvl] replaced by percentiles[lvl])

def calculate_k_stops(pair: str, events: list[dict[str, Any]]) -> dict[str, float | None]:
    return calculate_k_stops_for_events(events, STOP_PERCENTILES[pair])
```

Add the neutral percentile constant:

```python
STOP_PERCENTILES_NEUTRAL: dict[str, float] = {lvl: 0.90 for lvl in LEVELS}
```

The live read path (`get_k_stop`, `positions_manager`) is untouched — `calculate_k_stops` keeps the same signature and behavior.

**Commit:** `refactor(parameters): extract calculate_k_stops_for_events with percentile argument`.

---

## Step 2 — The lookback window selector

Add module-level constants and pure helpers to `trading/parameters_manager.py`:

```python
# Candidate lookback windows in days, ascending. Translated into candles via CANDLE_TIMEFRAME.
LOOKBACK_CANDIDATE_DAYS: tuple[int, ...] = (30, 45, 60, 90, 120, 180, 240, 365)

# Relative tolerance for K_STOP plateau detection (10%).
LOOKBACK_PLATEAU_TOL: float = 0.10

# How many longer windows must agree with the candidate to declare a plateau.
LOOKBACK_PLATEAU_LOOKAHEAD: int = 2


def _k_stop_for_window(df_full: pd.DataFrame, window_days: int) -> dict[str, dict[str, float | None]]:
    """K_STOP ladder per side for the most recent `window_days` of `df_full`,
    computed at the neutral percentile. Returns None for missing levels."""
    candles_per_day = (24 * 60) // CANDLE_TIMEFRAME
    df_w = df_full.tail(window_days * candles_per_day).reset_index(drop=True)
    if df_w.empty or df_w["atr"].dropna().empty:
        return {"buy": {lvl: None for lvl in LEVELS}, "sell": {lvl: None for lvl in LEVELS}}
    up_events, down_events = analyze_structural_noise(df_w)
    return {
        "sell": calculate_k_stops_for_events(up_events, STOP_PERCENTILES_NEUTRAL),
        "buy":  calculate_k_stops_for_events(down_events, STOP_PERCENTILES_NEUTRAL),
    }


def _all_levels_agree(entry: dict, peers: list[dict], tol: float) -> bool:
    for side in ("buy", "sell"):
        for lvl in LEVELS:
            base = entry[f"k_stop_{side}"].get(lvl)
            if base is None or base == 0:
                continue  # no signal / degenerate → skip, don't count as disagreement
            for peer in peers:
                other = peer[f"k_stop_{side}"].get(lvl)
                if other is None:
                    continue
                if abs(other - base) / abs(base) > tol:
                    return False
    return True


def _select_lookback_window(df_full: pd.DataFrame) -> tuple[int, list[dict[str, Any]]]:
    """Sweep candidate windows; return (selected_window_days, sweep_metadata).

    A window `w` is stable iff for every (side, level) the K_STOP at `w` agrees
    within LOOKBACK_PLATEAU_TOL with the K_STOP at the next
    LOOKBACK_PLATEAU_LOOKAHEAD longer windows. Returns the smallest stable window.
    Falls back to the longest feasible window (logging a warning) if no plateau.
    """
    candles_per_day = (24 * 60) // CANDLE_TIMEFRAME
    rows_available = len(df_full)
    feasible = [w for w in LOOKBACK_CANDIDATE_DAYS if w * candles_per_day <= rows_available]
    if not feasible:
        # Less than 30 days of data — use everything.
        return rows_available // candles_per_day, []

    sweep: list[dict[str, Any]] = []
    for w in feasible:
        k = _k_stop_for_window(df_full, w)
        sweep.append({"window_days": w, "k_stop_buy": k["buy"], "k_stop_sell": k["sell"]})

    for i, entry in enumerate(sweep):
        peers = sweep[i + 1 : i + 1 + LOOKBACK_PLATEAU_LOOKAHEAD]
        if len(peers) < LOOKBACK_PLATEAU_LOOKAHEAD:
            break  # not enough longer windows to compare against
        if _all_levels_agree(entry, peers, LOOKBACK_PLATEAU_TOL):
            return entry["window_days"], sweep

    logging.warning(f"No K_STOP plateau detected across {feasible}; falling back to {feasible[-1]} days.")
    return feasible[-1], sweep
```

Corner cases (all tested in Step 4):

- **Insufficient data** (`rows_available < 30 days`): bypass the sweep, use everything. `window_days = rows_available // candles_per_day`, `sweep = []`.
- **Some candidates feasible, no plateau**: fall back to the longest feasible window, log a warning.
- **All-`None` K_STOP at a level** (no events at that level for the candidate window): treat as "no signal", skip from comparison rather than counting as disagreement.
- **Zero base value** (degenerate K_STOP=0): skip from comparison to avoid division by zero.

### Cost note

`_select_lookback_window` runs `analyze_structural_noise` once per feasible candidate (up to 8) on each recalibration. `analyze_structural_noise` is the dominant cost (~hundreds of ms on long history); with multiple pairs this adds a few seconds to the `calculate_trading_parameters` call. That call runs only every `PARAM_SESSIONS` (12h) on the scheduler's single worker thread, so the added latency is acceptable — but note it in the PR, and confirm a session tick still completes comfortably within `SLEEPING_INTERVAL`.

**Commit:** `feat(parameters): K_STOP stability sweep to select the lookback window`.

---

## Step 3 — Wire the selector into `calculate_trading_parameters` + extend the cache

### 3.1 Extend the calibration cache (`core/runtime.py`)

Add `window_days` and `window_sweep` to the Phase-10 cache entry and to `update_pair_calibration`'s signature (both optional with safe defaults so any other caller from Phase 10 keeps working):

```python
def update_pair_calibration(
    pair, up_events, down_events,
    atr_p20, atr_p50, atr_p80, atr_p95,
    row_count,
    window_days: int | None = None,
    window_sweep: list[dict[str, Any]] | None = None,
) -> None:
    with _lock:
        _shared_data["pair_calibration"][pair] = {
            "up_events": up_events, "down_events": down_events,
            "atr_p20": atr_p20, "atr_p50": atr_p50, "atr_p80": atr_p80, "atr_p95": atr_p95,
            "row_count": row_count,
            "window_days": window_days,
            "window_sweep": window_sweep or [],
            "computed_at": now_utc(),
        }
```

### 3.2 Rewrite the order of operations in `calculate_trading_parameters`

1. `df_full = db.load_ohlc_data(pair, CANDLE_TIMEFRAME).dropna(subset=["atr"])`.
2. `window_days, sweep = _select_lookback_window(df_full)`.
3. `candles_per_day = (24 * 60) // CANDLE_TIMEFRAME`; `df = df_full.tail(window_days * candles_per_day).reset_index(drop=True)`.
4. Run the existing percentile + `analyze_structural_noise` + `calculate_k_stops` pipeline on **`df`** (not `df_full`) — i.e. the four `np.percentile(df["atr"], …)` writes to `PAIRS[pair]`, then `calculate_k_stops(pair, uptrend_events)` / `(pair, downtrend_events)` on the sliced events.
5. Write to globals (existing behavior, unchanged signatures).
6. Dual-write the cache, now including `window_days` and `sweep`.

One `INFO` per pair per recalibration:

```
Selected lookback window for XBTEUR: 90 days (8 candidates evaluated, plateau threshold 10%)
```

The fallback (no plateau) is already covered by the `WARNING` inside `_select_lookback_window`.

### 3.3 Surface `window_days` in the consumers

- `run_backtest`: include `window_days` in the summary dict (cache path reads it from the snapshot; recompute/slice path sets it from the window it selected, or `None`/row-count for a date slice).
- `run_optimize`: echo `window_days` in `OptimizerResult.scores` for traceability.

**Commit:** `feat(parameters): auto-select lookback window in calculate_trading_parameters; cache window + sweep`.

---

## Step 4 — Tests

`tests/unit/trading/test_lookback_selection.py`:

- `test_selects_smallest_stable_window` — fabricate a sweep where `90d` and longer agree within 10% but `60d` differs; assert the selector returns 90.
- `test_falls_back_to_longest_when_no_plateau` — sweep with no agreement; assert the longest feasible candidate is returned and a warning is logged.
- `test_handles_insufficient_data` — `df_full` with 20 days only; assert `window_days = 20`, `sweep = []`.
- `test_skips_none_levels_in_comparison` — one level `None` across windows; assert it does not block plateau detection at other levels.
- `test_skips_zero_base_value` — degenerate K_STOP=0 at a level; assert no ZeroDivisionError and the level is ignored.

`tests/unit/trading/test_parameters_manager_window.py`: monkeypatch `db.load_ohlc_data` to return ≥ 365 days of synthetic candles; call `calculate_trading_parameters("XBTEUR", infoLog=False)`; assert `runtime.get_pair_calibration("XBTEUR")["window_days"]` is one of `LOOKBACK_CANDIDATE_DAYS`, the events lists are non-empty, and `row_count == window_days × candles_per_day`.

Update the Phase-10 cache test if it asserted `window_days is None`.

**Commit:** `test(parameters): cover lookback selection and windowed calibration`.

---

## Step 5 — Before/after validation + documentation

Because this changes live stop sizing, validate before enabling on production:

1. For each configured pair, record the K_STOP ladder and (implicit) full-history calibration **before** this phase, then the selected `window_days` and resulting ladder **after**. Capture the delta.
2. Use `POST /optimizer/jobs` (mode `CURRENT`) and `POST /backtest` over historical data to compare PnL of full-history vs. windowed calibration. The change should be non-regressive (or its trade-off understood and accepted).
3. Document the candidate set, tolerance, neutral percentile, and the per-pair selected windows + PnL comparison in `docs/trading-strategy.md`.

**Commit:** `docs(strategy): document auto-lookback window selection and its PnL validation`.

---

## Acceptance checklist

- [ ] `calculate_k_stops_for_events(events, percentiles)` exists; `calculate_k_stops(pair, events)` is a thin wrapper preserving the old signature/behavior.
- [ ] `_select_lookback_window` exists and is covered by the five unit tests in Step 4.
- [ ] `calculate_trading_parameters` selects the window first, slices `df_full.tail(window_days × candles_per_day)`, runs the percentile + `analyze_structural_noise` + `calculate_k_stops` pipeline on the slice, and dual-writes globals + cache (now with `window_days` + `window_sweep`).
- [ ] With ≥ 1 year of OHLC, each pair's chosen `window_days` is one of `LOOKBACK_CANDIDATE_DAYS` (plateau) or a logged fallback.
- [ ] Log line `Selected lookback window for {pair}: {N} days (...)` appears after the first session per pair.
- [ ] `core/runtime` cache entry exposes `window_days` + `window_sweep`; backtest summary and optimizer scores surface the selected window.
- [ ] A session tick still completes within `SLEEPING_INTERVAL` with the added sweep cost.
- [ ] Before/after K_STOP deltas and PnL comparison documented in `docs/trading-strategy.md`.
- [ ] `pytest tests/unit` passes the 80% coverage gate.

---

## Non-goals for this phase

- **Per-volatility-level lookback windows.** Single window per pair (data scarcity at HV/HH).
- **Treating window length as an Optuna search dimension.** It is a calibration pre-step, owned by `parameters_manager`.
- **`kneed` or other elbow-detection libraries.** The plateau heuristic is sufficient and dependency-free.
- **Adaptive / pair-specific plateau threshold.** `LOOKBACK_PLATEAU_TOL = 0.10` is fixed.
- **Changing the activation/trailing/exit logic.** Only the K_STOP calibration input window changes; the trailing stop remains the sole exit mechanism.
