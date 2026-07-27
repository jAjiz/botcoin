# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

BoTCoin is an autonomous EUR-based crypto trading bot for Kraken. It runs a trailing-stop strategy driven by ATR volatility classification and persists all state in PostgreSQL. Four Docker services: `botc` (trading engine + FastAPI on :8000), `telegram` (Telegram bot + notify webhook on :8001), `postgres` (PostgreSQL, all state), and `grafana` (observability dashboard on :3000).

This project has three concurrent goals: (1) run as a profitable bot, (2) serve as a portfolio piece reviewed by other engineers, (3) be a vehicle for the author to learn production-grade Python. That changes how to collaborate here: prefer clarity over cleverness, surface non-obvious "why" in PR descriptions, and treat all code under `trading/`, `core/`, and `api/` as load-bearing — held to the testing/coverage bar. (`trading/backtest.py` and the optimizer were once manually-run research scripts; they are now tested library code behind the `/backtest` and `/optimizer/jobs` endpoints.) When introducing a non-obvious design choice, add it to the **Design choices** section below.

Service entry points: `api/app.py` (botc — also starts the APScheduler via FastAPI `lifespan`) and `services/telegram/app.py` (telegram). Both started via `uvicorn` in `docker-compose.yml`.

## Commands

All commands assume `PYTHONPATH=.` (set automatically by `docker-compose.test.yml`).

```bash
# Run all unit tests with coverage (local venv)
PYTHONPATH=. pytest tests/unit/

# Run a single test
PYTHONPATH=. pytest tests/unit/trading/test_positions_manager.py::test_is_closing_complete_returns_false_without_closing_order -v

# Run integration tests (require live DB + Kraken credentials)
RUN_DB_INTEGRATION=true PYTHONPATH=. pytest tests/integration/

# Run all unit tests + ruff (the full local test pass)
PYTHONPATH=. pytest tests/unit/ && python -m ruff check . && python -m ruff format --check .

# Lint + format check
python -m ruff check .
python -m ruff format --check .

# Auto-fix
python -m ruff check . --fix
python -m ruff format .

# Docker: full test suite
docker compose -f docker-compose.test.yml run --rm test pytest tests/

# Docker: dev stack
docker compose up -d --build

# Database migration (runs automatically on container start via entrypoint.sh)
alembic upgrade head

# New migration
alembic revision -m "describe change"
```

The coverage gate is **80%**. Only `scripts/migrations/versions/` is excluded from coverage measurement; all application code under `api/`, `core/`, `exchange/`, `services/`, and `trading/` is measured. (`core/scheduler.py` is measured but its per-pair loop is still under-tested — see the TODO in `tests/unit/core/test_scheduler.py`.)

Pin all dependencies with `==` in `requirements.txt`. Resolve the exact version with `pip show <package>` before adding.

## Architecture

### Trading loop (`core/scheduler.py`)

`trading_session()` runs every `SLEEPING_INTERVAL` seconds via APScheduler. Per session, per pair:

1. Reload `trailing_state` from DB
2. Recalculate trading parameters every `PARAM_SESSIONS` ticks (`calculate_trading_parameters`)
3. **If closing order is filled** → `is_closing_complete` fetches real execution price from Kraken, writes `closing_price` and `pnl_percent` into the position dict, then `record_position_closed` atomically inserts the closed position and deletes its `trailing_state` row in one transaction
4. **Else if a closing order is still outstanding** → `reprice_closing_order` cancels it and re-places the limit at the current market price (only when the order is `open` and untouched — a partial fill is left alone). This is an `elif` on purpose: a terminated order has already had its closing fields cleared by step 3, so it is never repriced
5. **If no active position** → `create_position`
6. **If position is open** (no `closing_order_id`) → `tick_position` (recalibrate, check activation, update trailing stop, trigger close if stop is hit)
7. Persist updated state → `save_trailing_state` (in a `finally`; see below)

Steps 3–7 (the position block) run only when `TRADING_ENABLED` is true. When it is false the per-pair loop `continue`s after recording market data, so the instance ingests OHLC, calibrates and records sessions but never trades. Steps 1–2 and the runtime/`pair_data` updates always run.

The whole per-pair body is wrapped in `try/except/finally`: one pair's failure is logged, recorded in `failed_pairs`, and skipped, so the remaining pairs still trade. A pair skipped for a missing price or ATR is recorded the same way — an unpriced pair is an *unmanaged* pair (its trailing stop is frozen), so it must not pass as success. Any non-empty `failed_pairs` makes the session `failed` with reason `pair errors: <PAIRS>`, which feeds the consecutive-failure Telegram alert.

Step 7 lives in the `finally`, and is the **only** place the position block writes state (`_persist_pair_state`): a closing order placed just before a failure must still reach the DB, or the order lives on at Kraken with its id lost (A5). That is also why `positions_manager` never touches `core.database` — persistence is the scheduler's job, and the strategy code only mutates the position dict.

`core/runtime.py` holds thread-safe shared state so the FastAPI routes can read live prices/ATR without touching the DB.

**Invariants — do not break without explicit discussion:**

- The trailing stop is the **only** exit mechanism. There is no global stop-loss, no max-loss-per-position, no panic kill switch in code. Adding one is a strategy change, not a refactor.
- `closing_price` is an **estimate** until `is_closing_complete` confirms the fill: `close_position` writes it first (at order placement), and each `reprice_closing_order` chase overwrites it again (still an estimate, at the new limit price) while the order remains unfilled. `is_closing_complete` performs the final write with the real fill from Kraken and computes `pnl_percent`. Any code that reads `closing_price` before `is_closing_complete` returns `True` is reading an estimate.
- A position with `closing_order_id` set is **not** open — `tick_position` must not run on it. Steps 3–4 of the loop resolve the closing order before step 6 checks `is_open`.
- `_safe_call` in `exchange/kraken.py` swallows errors and returns `None`. Callers that don't handle `None` will silently corrupt state.

### Position lifecycle (`trading/positions_manager.py`)

- **create_position**: Calculates `activation_price` using either `K_ACT × ATR` (if `K_ACT` is set) or `K_STOP × ATR + MIN_MARGIN × entry_price`. Stores an inactive position.
- **tick_position**: Activates on price cross of `activation_price`, then tracks trailing price and updates stop. Recalibrates activation/stop when ATR drifts beyond `ATR_DESV_LIMIT`.
- **close_position**: Places a limit order at current market price, records `closing_price` (approximate, at order placement) and `closing_order_id`. Does NOT compute PnL.
- **reprice_closing_order**: Chases the fill of a still-`open` closing order — cancels it and re-places the limit at the current market price. Returns early (leaving the order alone) on an API error, any status other than `open` (a `pending` order is not on the book yet, so cancel/replace is pure churn), any executed volume, an unchanged rounded price, or a cancel that Kraken did not confirm as definitive. If the cancel succeeds but the replacement fails, the dead `closing_order_id` is deliberately kept so `is_open` stays `False` and the next tick's terminal-status branch clears it.
- **is_closing_complete**: Calls `get_order_state` (Kraken `QueryOrders`) and branches on `status` explicitly. A `closed` order with a positive average fill price is finalized: `closing_price` is overwritten with the real fill and `pnl_percent` is computed. **Every** other terminal outcome — `canceled`, `expired`, or any status that cannot be finalized, including `closed` without a usable average price — clears `closing_order_id`/`closing_price`/`closing_requested_at` so the position resumes management on the same tick. There is deliberately no branch that leaves a terminal order's fields in place: the status can never change again, so the position would be frozen forever with `is_open` `False`. Returns `True` only when the fill is confirmed.

  A partial fill is **not** reconciled. `refresh_position` resizes the position from the pair's target allocation against the current balance on the next tick, so the bot converges on a correct size rather than tracking the unfilled remainder; if what is left falls below `MIN_VALUE` the position is dropped and the residual base amount stays untracked.

### Volatility classification (`trading/market_analyzer.py` + `trading/parameters_manager.py`)

ATR is computed from OHLC stored in `ohlc_data`. `get_volatility_level` classifies the current ATR into five levels (LL/LV/MV/HV/HH) using precomputed percentile boundaries. `K_STOP` for each level comes from `PAIR_STOP_PCT_<LEVEL>` — the percentile of historically observed K-values (structural noise analysis via pivot detection).

### Trading tools — backtest & optimizer (`trading/engine.py`, `trading/backtest.py`, `trading/optimizer/`)

Offline analysis tools exposed as authenticated HTTP endpoints on the `botc` service. They read stored OHLC and the live calibration cache but **never mutate trading state**.

- **`trading/engine.py`** — the pure simulation engine (`simulate_operations`). A leaf module: it imports nothing from `core.config` or `parameters_manager`; all configuration is passed in via `EngineConfig`, so the same simulator runs against live state, a backtest request, or an optimizer candidate. It mirrors the live `positions_manager` logic (activation, trailing stop, ATR re-anchoring) so simulations behave like production.
- **`trading/backtest.py`** — `run_backtest(req) -> BacktestResult`. Pure library (no CLI, no prints). Builds an `EngineConfig` from cached or recomputed calibration and runs the engine. Behind synchronous `POST /backtest`.
- **`trading/optimizer/search.py`** — `run_optimize` runs two **independent** Optuna TPE studies per search (a `K_ACT` activation branch and a `MIN_MARGIN` branch), each over per-level stop percentiles, then merges and ranks candidates by `robust_pnl = min(train_pnl, test_pnl)`. The train/test split is evaluated in a single continuous run over the full dataset (CONTINUE-only). Modes: `OPTIMIZE` (TPE search), `CURRENT` (evaluate the live `.env` config, 1 trial), `AUTO` (`run_auto_optimize`: multi-seed convergence loop that escalates `n_trials` until `min_agree` of `n_seeds` agree, then compares against `CURRENT`). In AUTO the per-seed studies are kept alive across escalation levels and only the *delta* of trials is run each level (warm-start, see Design choices); OHLC + calibration are loaded once via `_build_eval_context` and shared by every seed/level. `mode` is required.
- **`trading/optimizer/jobs.py`** — `JobStore`, an N-slot async job manager (capacity set by `MAX_CONCURRENT_JOBS`). `try_start` inserts an `optimizer_jobs` row and submits the work to a `ProcessPoolExecutor(max_workers=N, spawn)`; `supervise(job_id)` awaits that job's future and persists the result; a submission when all slots are full raises `OptimizerBusyError` (→ `409`); `MAX_CONCURRENT_JOBS=0` disables the optimizer entirely (→ `503`). Telegram is notified on start, completion, and failure. `worker.py` is the picklable child entry point.
- **Calibration cache**: the live `core/runtime.py` holds the snapshot of structural events + ATR percentiles. The spawned worker starts with an empty runtime, so `try_start` snapshots the calibration in the parent and passes it explicitly; a sliced request passes `None` and the worker recomputes the calibration **from full history up to the window `end`** (not from the slice itself — see the Design choice below).
- **API**: `api/routes/backtest.py` and `api/routes/optimizer.py`; request/response models in `api/schemas.py`. All endpoints require the `X-Api-Token` header.

### Database (`core/database.py`)

Seven ORM models: `OHLCData`, `TrailingState`, `ClosedPosition`, `BotControl`, `OptimizerJob`, `SessionRecord`, `PairConfig`. Direct SQLAlchemy (no async). All DAL functions are at module level (not a class). Migrations live in `scripts/migrations/versions/` managed by Alembic (`alembic.ini` points there).

When changing an ORM model's table constraints, update **both** the model in `core/database.py` and the corresponding Alembic migration — they are not auto-synced, and CI builds the schema from migrations (a drift between the two recently allowed an invalid `mode` to pass the model but fail the migration's check constraint).

`TrailingState` captures the full active position dict. Fields are optional during the open phase (`trailing_price`, `stop_price`, `closing_order_id`, etc.) and populated progressively as the position advances.

`BotControl` is a generic key/value table (`control_key` → `control_value`, both `Text`) accessed via `get_control_value` / `set_control_value`. It holds runtime state that must survive restarts without a redeploy or a schema change. Current keys: `bot_paused` (the pause flag), `latest_balance` and `latest_pair_data` (written by `finalize_session`, read by Grafana), and a per-pair OHLC backfill watermark written by `market_analyzer`.

`SessionRecord` is written once at the start of every `trading_session()` call (status `running`) and updated in the `finally` block with the final status and captured log lines. It is the primary data source for the Grafana Sessions row.

`OptimizerJob` backs the async optimizer (`optimizer_jobs` table). A row is inserted `running` by `JobStore.try_start` and updated to `completed` (with the JSONB result) or `failed`. A `ck_opt_jobs_mode_valid` check constraint restricts `mode` to `OPTIMIZE`/`CURRENT`/`AUTO`; `ck_opt_jobs_status_valid` restricts `status` to `running`/`completed`/`failed`.

`PairConfig` (`pair_config` table) — DB-authoritative per-pair config, seeded once from `.env` on first boot. Holds `target_pct`, `hodl_pct`, `k_act`, `min_margin`, and the five `stop_pct_<level>` values per pair. Managed by `core/config_store.py`; editable at runtime via `PATCH /config/{pair}` and Telegram `/setconfig`.

### Exchange wrapper (`exchange/kraken.py`)

Rate-limited to 1 call/second via a module-level lock. `_safe_call` wraps every API call: returns `result` on success, logs and returns `None` on any error. Callers must always handle `None`.

### Services

`services/telegram/` is an independent FastAPI app. It communicates with the trading engine exclusively through the REST API (`services/telegram/client.py` → `http://botc:8000`). The `/notify` endpoint receives Telegram messages posted by `core/logging.py` when `to_telegram=True`.

## Configuration

Per-pair parameters are loaded from env vars by `core/config.py` into the `TRADING_PARAMS` dict on startup. Since Phase 1, these values are also persisted in the `pair_config` DB table (seeded from `.env` on first boot via `config_store.load_or_seed()`); the DB is now the authoritative source and parameters can be changed at runtime via `PATCH /config/{pair}` without a restart. The key pattern:

- `PAIR_TARGET_PCT` / `PAIR_HODL_PCT`: Portfolio allocation (inventory manager)
- `PAIR_K_ACT`: Activation ATR multiplier; `0` = immediate activation (single per pair — per-side `PAIR_SELL_K_ACT` / `PAIR_BUY_K_ACT` variants have been removed)
- `PAIR_MIN_MARGIN`: Minimum price margin from entry, expressed as fraction of entry price (single per pair — per-side `PAIR_SELL_MIN_MARGIN` / `PAIR_BUY_MIN_MARGIN` variants have been removed)
- `PAIR_STOP_PCT_LL` … `_HH`: K-stop percentile per volatility level
- `SESSION_FAILURE_ALERT_THRESHOLD` (default 3): consecutive failed sessions before one edge-triggered Telegram alert fires; a single recovery message follows the next successful session. The same threshold also gates the session-overrun early-warning (see Design choices).

## Design choices

Non-obvious decisions a reviewer would otherwise question. Update this list when adding another.

- **Synchronous SQLAlchemy under async FastAPI.** The trading loop ticks once per `SLEEPING_INTERVAL` (order of seconds), not per request. There is no concurrent DB load to justify async. Sync code is easier to read and test; the FastAPI routes that touch the DB are few and short.
- **Module-level DAL functions, not a repository class.** Single database, no swappable storage backend, no benefit from a class wrapper. Free functions keep call sites readable (`db.save_trailing_state(...)`) without forcing dependency injection.
- **Module-level lock + 1 call/sec in `exchange/kraken.py`.** Kraken's tier-0 limit allows more, but the bot has no latency budget that would benefit from optimizing it. A simple lock is correct, obvious, and cheap; a token bucket would add code without solving a real problem.
- **APScheduler started from the FastAPI `lifespan`.** Co-locating the scheduler with the API means one process, one health endpoint, one set of logs. The alternative — a separate worker container — would double the deployment surface for no operational gain at this scale.
- **`_safe_call` returns `None` on every error instead of raising.** Kraken outages, rate-limit hits, and transient network errors are *expected* during a long-running session; a missed tick is recoverable, a crashed bot is not. Callers must handle `None`; the trade-off is verbosity in callers vs. resilience overall.
- **Every blocking I/O call in the scheduler loop is time-bounded so a stall can never freeze the single worker thread.** The loop runs on an APScheduler `ThreadPoolExecutor(max_workers=1)` with `max_instances=1`, so one hung tick blocks *all* future ticks. This actually happened: a Kraken call inherited krakenex's default `timeout=None`, a stalled socket blocked forever, the session stayed `running`, and every later tick was skipped with a "max instances reached" warning. Both I/O sources are now capped: Kraken via `KRAKEN_HTTP_TIMEOUT` (`(connect, read)` passed to every krakenex call in `exchange/kraken.py`), and PostgreSQL via `_build_connect_args()` in `core/database.py` (`connect_timeout` for the connect phase, TCP keepalives to catch a peer that dies mid-query across a network partition, and a server-side `statement_timeout` for slow/lock-blocked queries). A stall now raises instead of hanging, which `_safe_call` / `get_session` convert into a recoverable missed tick. Leftover `running` sessions from a past hang/crash are reconciled at startup by `cleanup_orphaned_sessions()`.
- **Session-failure alerts are edge-triggered from an in-memory streak, not per-failure.** Once the hang class was closed, the residual risk became *sessions that keep running but fail in a row* (Kraken down, DB flapping), which the operator would otherwise only notice via stale prices/positions. `trading_session`'s `finally` always runs with a definitive status, so `_notify_session_outcome` counts consecutive `failed` sessions in `core/runtime.py` and sends **one** Telegram alert when the streak reaches `SESSION_FAILURE_ALERT_THRESHOLD` (default 3) and **one** recovery message on the next success — never one message per failed tick. It runs before `finalize_session` and outside the `session_id` guard, so the alert still fires when the DB itself is down (Telegram is HTTP, DB-independent). No APScheduler event listeners are needed; the streak is in-memory, so a restart resets it.
- **Session-overrun early-warning is derived from session duration, not from skipped-tick events.** The July 2026 `e2-micro` outage was CPU starvation: sessions slowed from ~4s to 100–950s for ~48h (blocking ticks, then failing) while the operator only found out hours later via the DB-failure alert. The fix is a second edge-triggered streak alongside the failure streak: `trading_session` measures its own wall-clock (`started_at` captured before the `try`, reused for the `create_session` row; `elapsed` computed in the `finally` and reused for `finalize_session`'s `ended_at`), and `_notify_session_outcome` counts consecutive **completed** sessions whose `elapsed ≥ SLEEPING_INTERVAL` — the exact point a session overruns its interval and skips the next tick — via `register_session_overrun`/`register_session_ontime` in `core/runtime.py`. Three design decisions a reviewer would question: (1) **duration-vs-interval, not a `max_instances`-skip listener** — a skip-based signal only observes the *gaps between* sessions, so a completed session always resets it and it **flaps** (false "recovered" pings mid-incident); measuring every completed session's duration is stable and holds the alert until sessions genuinely finish under the interval. (2) **Threshold is `SLEEPING_INTERVAL`, not a new tunable** — it's the operational harm boundary ("did this session block the next tick"), so no pair-count-dependent magic number and no extra env var. (3) **The overrun streak is independent of the failure streak** (a `failed`/`paused` session touches neither overrun counter); when both were alerted, the overrun-recovery message is suppressed if a failure-recovery fires the same tick, since the latter already signals normal operation. Wall-clock (`now_utc`) is fine over `monotonic` here because both timestamps are in-process (DB-independent) and the 3-in-a-row streak absorbs any single NTP step.
- **Prices/ATR are kept at full float precision internally; rounding happens only at the order boundary, per the pair's Kraken precision.** `positions_manager` stores `activation_price`/`stop_price`/`*_atr` unrounded (matching `engine.py`, which never rounded), and `place_limit_order` formats the submitted `price`/`volume` to the pair's `pair_decimals`/`lot_decimals` (captured by `build_pairs_map` from `AssetPairs`, alongside `cost_decimals` which the current limit-order flow doesn't need). The earlier hardcoded `round(x, 1)` was invisible on XBTEUR (~68 000€) but destroyed low-value pairs: USDCEUR's ATR (~0.0008) rounded to `0.0`, collapsing the activation/stop distance, and order prices (~1.03) rounded to `1.0`. Storage fits without migration — the `Numeric(_, 10)` price/ATR columns hold 10 decimals, finer than any pair's `pair_decimals`. When `pair_decimals`/`lot_decimals` are unknown (metadata not loaded) the value is sent unrounded rather than coarsened. **Round only at boundaries; state and the DB stay full precision.** The two boundaries are order submission (above) and display. `core/utils.py` exposes two rounding helpers over a shared `_round_to_pair_decimals`: `round_price(pair, value)` (uses `pair_decimals`) and `round_volume(pair, value)` (uses `lot_decimals`), both reading `config.PAIRS`; call sites add the thousands separator (`f"{round_price(pair, x):,}€"`). `round_price` is used for **logs** (scheduler, `positions_manager`, `parameters_manager`) and the **API** routes. `/market` and `/positions` are consumed only by the Telegram process (a BFF), so the routes round the displayed fields there (`MarketItem.last_price`/`atr`, `PositionDetail` price fields via `round_price`, `PositionDetail.volume` via `round_volume`) and Telegram just prints with a thousands separator (`{value:,}`) — no precision is threaded over the API. `round_price` reads `config.PAIRS`, so it only works in the trading-engine process (which loads pair metadata); the Telegram process has empty metadata and must rely on the API's pre-rounding, never call `round_price` itself. EUR notionals and balances stay at fixed `.2f`. **ATR fields (`atr` in `/market` aside, plus `activation_atr`/`stop_atr`) are never rounded into state/DB**: ATR is a computed volatility measure finer than `pair_decimals` and drives ATR-drift detection, so rounding it (e.g. USDCEUR's ~0.0008) would degrade recalibration — the same class of bug the round-removal fixed. Prices from the Kraken ticker (`c[0]`) already arrive at `pair_decimals`, which is why rounding price fields is lossless.
- **Backtest and optimizer share one pure engine (`trading/engine.py`).** Configuration is passed in via `EngineConfig`, never read from module globals, so the same simulator runs against live state, a backtest request, and an optimizer candidate. This keeps both tools faithful to production behavior without duplicating the trailing-stop logic — at the cost of threading config through every call instead of reaching for globals.
- **The optimizer runs in a `ProcessPoolExecutor(spawn)` sized by `MAX_CONCURRENT_JOBS`.** The Optuna search is CPU-bound and would block the event loop and starve the scheduler if run inline. Spawned workers isolate it; capacity is configurable (`MAX_CONCURRENT_JOBS=0` disables, `≥1` allows N concurrent jobs, `409` when full). `optimizer_jobs.id` is a sequential integer so jobs are easy to refer to (`#1`, `#2`, …). Job state persists in Postgres so a restart marks interrupted jobs `failed`, never `running`.
- **Two independent Optuna studies per search (`K_ACT` vs `MIN_MARGIN`), merged and ranked.** A single mixed study was highly seed-sensitive — different seeds found radically different optima. Splitting by activation type and merging globally is far more stable. Candidates rank by `robust_pnl = min(train_pnl, test_pnl)` so configs that overfit one half don't win.
- **AUTO warm-starts the escalation instead of restarting.** Each seed's studies are kept alive across escalation levels; raising the budget from N to N+step runs only the `step` extra trials and *continues* the TPE search, rather than building fresh N+step studies from scratch. This is equivalent-or-better in search quality (TPE keeps its full history) and far cheaper: the worst-case trial count drops from `seeds × Σ(levels)` to `seeds × max_trials` (measured ~2× faster at 3 levels, ~9× at the default 17). `_build_eval_context` loads OHLC + calibration once per search so the heavy setup isn't repeated per seed/level.
- **The `K_ACT` and `MIN_MARGIN` branches run in parallel (2-process pool).** Each branch's study is shipped to a worker process, advanced, and shipped back (Optuna in-memory studies pickle cleanly, so warm-start survives the round trip). Gated by `_PARALLEL_MIN_TRIALS` so small runs (and unit tests) stay sequential and don't pay the spawn/df-pickle overhead; measured ~1.8× on a 400-trial run. Only the two branches are parallelized — seeds stay sequential (a full seeds-across-shared-storage parallelization was deliberately deferred as too large). The pool is nested safely inside the `jobs.py` worker (`ProcessPoolExecutor` workers aren't daemonic, so they may spawn children — verified). Each concurrent `AUTO` job implies ~3 worker processes total; keep `MAX_CONCURRENT_JOBS` low on small hosts.
- **Optimizer split is CONTINUE-only.** The simulation runs once over the full dataset and is partitioned at the train/test boundary, matching production where the bot never resets mid-history. (Earlier `RESET`/`BOTH` split methods were removed because they penalized the realistic continuous path.)
- **Sliced jobs simulate `[start, end]` but calibrate over `[T0, end]` (full history up to the window end), not over the slice.** The K_STOP/ATR-percentile calibration is structural and slow-moving; the live bot always recomputes it over *all* available history, never a window. The earlier "recompute from the slice" made calibration depend on the window length, so a short backtest/optimizer slice resolved K_STOP from only a few hundred candles — unstable to the point that a **one-day shift of `end` could flip the result's sign with the identical set of trades** (pure calibration noise, measured on ETHEUR June-2026 data), and systematically diverging from the live bot's full-history stops. Calibrating over `[T0, end]` makes the engine faithful to live and stabilises the Stage-D temporal-window and weekly walk-forward probes (their `robust_pnl` now reflects config quality, not recalibration). Capping at `end` (rather than using the whole loaded frame) keeps a sliced run from ever seeing candles after its own window — no look-ahead. The simulation window stays `[start, end]`; only the calibration source changed. (The regime ER thresholds are still resolved inside `simulate_operations` from the working frame — a separate "recompute philosophy" left untouched here.)
- **Search grids are supplied per request (`SearchSpace`), not hardcoded.** Each dimension (stop percentiles, `K_ACT`, `MIN_MARGIN`) is a uniform `GridSpec{start, end, step}` — kept as start/end/step rather than an arbitrary value list so the optimizer keeps using `suggest_float`/`suggest_int` and preserves TPE's ordinal structure. There are **no built-in defaults** (the grids must be informed; required for OPTIMIZE/AUTO, ignored by CURRENT), and a `null` activation grid disables that whole branch (`K_ACT` null → only the `MIN_MARGIN` branch runs; at least one required; `start == end` *fixes* a dimension instead). This makes grid coarseness an experiment input — coarser grids shrink the space so seeds can actually agree, and varying capacity probes overfitting via the train/test gap — and every job stores its `search_space` in `optimizer_jobs.request`, so runs are self-documenting and reproducible. `jobs.py` persists/ships `asdict(req)` (not `req.__dict__`) so the nested `SearchSpace` is JSONB-serializable and picklable for the spawn worker.
- **AUTO convergence is judged on the config (param signature), not the score.** Seeds are grouped by their top candidate's params, not by rounded `robust_pnl`. Two seeds reaching the same `robust_pnl` via *different* configs is a flat/noisy landscape, not a stable optimum — and since deployment ships one config, agreeing on *which* config is the honest robustness test (`K_ACT` vs `MIN_MARGIN` candidates never collide — disjoint keys). Match is exact for now; a tolerance/clustering relaxation was deliberately deferred until observed necessary on ≥60-day data.
- **No global stop-loss.** Risk is bounded by the trailing-stop distance only. This is a deliberate strategy choice (early exits during normal volatility hurt expected value more than tail losses cost). Adding a hard floor is a strategy decision and must be discussed, not introduced as a "safety improvement."
- **`TRADING_ENABLED` is a deploy-time mode flag, not a runtime risk control.** When false, the scheduler skips the entire position block (open/manage/close) but keeps ingesting OHLC, calibrating, recording sessions and serving the API/optimizer. It exists so the full stack can run as a non-trading replica (e.g. a beefy local box driving the optimizer with Telegram, cache and history intact) instead of a bespoke standalone script. This does **not** contradict the "no panic kill switch" invariant: that invariant forbids in-flight risk overrides on a *trading* instance; this flag decides up front whether an instance trades at all. It must stay `true` in production and must not be flipped on an instance holding open positions — their trailing stop would freeze (the loop warns loudly if it finds a stored position while disabled).
- **`telegram` runs as a separate service, not inside `botc`.** PTB's `Application.run_polling()` blocks its thread indefinitely. Co-locating it with the scheduler would risk a dropped Telegram connection stalling the trading loop. A separate service means the trading engine is entirely unaffected by Telegram's availability.
- **`_SessionLogCollector` attaches to the root logger rather than threading a context object.** The alternative — passing a log buffer through the call graph (`trading_session` → `positions_manager` → `market_analyzer` → …) — would require modifying every function signature. The root-logger approach captures records from every module called during the session with zero changes to any call site.
- **`JSONB` is reserved for payloads the application reads back as structured data; everything else stores JSON as `Text`.** Only `optimizer_jobs.request`/`result` are `JSONB` — they are written and re-read as dicts by the optimizer itself. `sessions.log_messages` is `Text` because it is always fetched as a whole array, never queried by individual entry, so `JSONB` would add parse overhead with no query benefit. `bot_control.control_value` is `Text` too, even though Grafana *does* query into `latest_balance`/`latest_pair_data` structurally — it casts at read time (`control_value::jsonb ->> 'ZEUR'`), which keeps the key/value table generic enough to hold a plain flag like `bot_paused` in the same column.
- **Dynamic pair config is DB-authoritative, seeded once from `.env`.** A dedicated typed `pair_config` table was chosen over the generic `BotControl` store because typed columns enable schema-level validation, clean ORM access, and straightforward `PATCH` semantics. `.env` remains the deployment-time seed for new installs; after first boot it is no longer read for these parameters. This lets an operator tune live via the API or Telegram without touching `.env` or restarting the container.
- **`stop_pct` changes recalc `K_STOP` at the next session via a runtime dirty flag.** `apply_patch` in `core/config_store.py` sets a per-pair dirty flag in `core/runtime.py` when any `stop_pct` field changes. The scheduler checks the flag at the start of the next `trading_session()` and re-runs `calculate_trading_parameters` before the position block. This keeps heavy calibration (pivot detection, ATR percentiles) inside the scheduler thread and off the request path, avoiding any latency spike on the `PATCH` endpoint.
- **`k_act`/`min_margin` are single per pair; `K_STOP` stays per-side.** The per-side `PAIR_SELL_K_ACT` / `PAIR_BUY_K_ACT` and `PAIR_SELL_MIN_MARGIN` / `PAIR_BUY_MIN_MARGIN` env vars were removed because the optimizer already treated these as single values and per-side tuning added config complexity without observable benefit. `K_STOP` is kept per-side (buy/sell) because it is a *derived* structural parameter (computed from pivot analysis), not a directly configured one, and the buy/sell paths naturally produce different stop distances.

## Testing conventions

- Unit tests live in `tests/unit/` and never call external APIs. Kraken and DB calls are monkeypatched at the module level where the name is imported (e.g. `monkeypatch.setattr(positions_manager, "get_order_state", ...)`).
- Integration tests in `tests/integration/` require `RUN_DB_INTEGRATION=true` and are skipped otherwise.
- `pytest-asyncio` is used for async FastAPI route tests.
