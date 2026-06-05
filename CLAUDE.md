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
3. **If closing order is filled** → `is_closing_complete` fetches real execution price from Kraken, writes `closing_price` and `pnl_percent` into the position dict, then `save_closed_position` + `delete_trailing_state`
4. **If no active position** → `create_position`
5. **If position is open** (no `closing_order_id`) → `tick_position` (recalibrate, check activation, update trailing stop, trigger close if stop is hit)
6. Persist updated state → `save_trailing_state`

`core/runtime.py` holds thread-safe shared state so the FastAPI routes can read live prices/ATR without touching the DB.

**Invariants — do not break without explicit discussion:**

- The trailing stop is the **only** exit mechanism. There is no global stop-loss, no max-loss-per-position, no panic kill switch in code. Adding one is a strategy change, not a refactor.
- `closing_price` is written **twice**: first by `close_position` (approximate, at order placement) and then by `is_closing_complete` (real fill from Kraken). PnL is computed only after the second write. Any code that reads `closing_price` before `is_closing_complete` returns `True` is reading an estimate.
- A position with `closing_order_id` set is **not** open — `tick_position` must not run on it. Step 3 of the loop checks this before step 5.
- `_safe_call` in `exchange/kraken.py` swallows errors and returns `None`. Callers that don't handle `None` will silently corrupt state.

### Position lifecycle (`trading/positions_manager.py`)

- **create_position**: Calculates `activation_price` using either `K_ACT × ATR` (if `K_ACT` is set) or `K_STOP × ATR + MIN_MARGIN × entry_price`. Stores an inactive position.
- **tick_position**: Activates on price cross of `activation_price`, then tracks trailing price and updates stop. Recalibrates activation/stop when ATR drifts beyond `ATR_DESV_LIMIT`.
- **close_position**: Places a limit order at current market price, records `closing_price` (approximate, at order placement) and `closing_order_id`. Does NOT compute PnL.
- **is_closing_complete**: Calls `get_order_closing_price` (Kraken `QueryOrders` → `price` field). If filled, overwrites `closing_price` with the real fill and computes `pnl_percent`. Returns `True` only when the fill is confirmed.

### Volatility classification (`trading/market_analyzer.py` + `trading/parameters_manager.py`)

ATR is computed from OHLC stored in `ohlc_data`. `get_volatility_level` classifies the current ATR into five levels (LL/LV/MV/HV/HH) using precomputed percentile boundaries. `K_STOP` for each level comes from `PAIR_STOP_PCT_<LEVEL>` — the percentile of historically observed K-values (structural noise analysis via pivot detection).

### Trading tools — backtest & optimizer (`trading/engine.py`, `trading/backtest.py`, `trading/optimizer/`)

Offline analysis tools exposed as authenticated HTTP endpoints on the `botc` service. They read stored OHLC and the live calibration cache but **never mutate trading state**.

- **`trading/engine.py`** — the pure simulation engine (`simulate_operations`). A leaf module: it imports nothing from `core.config` or `parameters_manager`; all configuration is passed in via `EngineConfig`, so the same simulator runs against live state, a backtest request, or an optimizer candidate. It mirrors the live `positions_manager` logic (activation, trailing stop, ATR re-anchoring) so simulations behave like production.
- **`trading/backtest.py`** — `run_backtest(req) -> BacktestResult`. Pure library (no CLI, no prints). Builds an `EngineConfig` from cached or recomputed calibration and runs the engine. Behind synchronous `POST /backtest`.
- **`trading/optimizer/search.py`** — `run_optimize` runs two **independent** Optuna TPE studies per search (a `K_ACT` activation branch and a `MIN_MARGIN` branch), each over per-level stop percentiles, then merges and ranks candidates by `robust_pnl = min(train_pnl, test_pnl)`. The train/test split is evaluated in a single continuous run over the full dataset (CONTINUE-only). Modes: `OPTIMIZE` (TPE search), `CURRENT` (evaluate the live `.env` config, 1 trial), `AUTO` (`run_auto_optimize`: multi-seed convergence loop that escalates `n_trials` until `min_agree` of `n_seeds` agree, then compares against `CURRENT`). `mode` is required.
- **`trading/optimizer/jobs.py`** — `JobStore`, a single-slot async job manager. `try_start` inserts an `optimizer_jobs` row and submits the work to a `ProcessPoolExecutor(max_workers=1, spawn)`; `supervise` awaits the future and persists the result; a second submission while one is running raises `OptimizerBusyError` (→ `409`). Telegram is notified on start, completion, and failure. `worker.py` is the picklable child entry point.
- **Calibration cache**: the live `core/runtime.py` holds the snapshot of structural events + ATR percentiles. The spawned worker starts with an empty runtime, so `try_start` snapshots the calibration in the parent and passes it explicitly; a sliced request passes `None` and the worker recomputes from the slice.
- **API**: `api/routes/backtest.py` and `api/routes/optimizer.py`; request/response models in `api/schemas.py`. All endpoints require the `X-Api-Token` header.

### Database (`core/database.py`)

Six ORM models: `OHLCData`, `TrailingState`, `ClosedPosition`, `BotControl`, `OptimizerJob`, `SessionRecord`. Direct SQLAlchemy (no async). All DAL functions are at module level (not a class). Migrations live in `scripts/migrations/versions/` managed by Alembic (`alembic.ini` points there).

When changing an ORM model's table constraints, update **both** the model in `core/database.py` and the corresponding Alembic migration — they are not auto-synced, and CI builds the schema from migrations (a drift between the two recently allowed an invalid `mode` to pass the model but fail the migration's check constraint).

`TrailingState` captures the full active position dict. Fields are optional during the open phase (`trailing_price`, `stop_price`, `closing_order_id`, etc.) and populated progressively as the position advances.

`BotControl` is a generic key/value table (`control_key` → `control_value`) accessed via `get_control_value` / `set_control_value`. Intended for runtime flags that should survive restarts and be toggled without redeploy; **currently has no production callers** — the table and DAL exist but no feature uses it yet.

`SessionRecord` is written once at the start of every `trading_session()` call (status `running`) and updated in the `finally` block with the final status, balance snapshot, per-pair market data, and captured log lines. It is the primary data source for the Grafana Sessions row.

`OptimizerJob` backs the async optimizer (`optimizer_jobs` table). A row is inserted `running` by `JobStore.try_start` and updated to `completed` (with the JSONB result) or `failed`. A `ck_opt_jobs_mode_valid` check constraint restricts `mode` to `OPTIMIZE`/`CURRENT`/`AUTO`; `ck_opt_jobs_status_valid` restricts `status` to `running`/`completed`/`failed`.

### Exchange wrapper (`exchange/kraken.py`)

Rate-limited to 1 call/second via a module-level lock. `_safe_call` wraps every API call: returns `result` on success, logs and returns `None` on any error. Callers must always handle `None`.

### Services

`services/telegram/` is an independent FastAPI app. It communicates with the trading engine exclusively through the REST API (`services/telegram/client.py` → `http://botc:8000`). The `/notify` endpoint receives Telegram messages posted by `core/logging.py` when `to_telegram=True`.

## Configuration

Per-pair parameters are loaded from env vars by `core/config.py` into the `TRADING_PARAMS` dict. The key pattern:

- `PAIR_TARGET_PCT` / `PAIR_HODL_PCT`: Portfolio allocation (inventory manager)
- `PAIR_K_ACT` (or `PAIR_SELL_K_ACT` / `PAIR_BUY_K_ACT`): Activation ATR multiplier; `0` = immediate activation
- `PAIR_MIN_MARGIN`: Minimum price margin from entry, expressed as fraction of entry price
- `PAIR_STOP_PCT_LL` … `_HH`: K-stop percentile per volatility level

## Design choices

Non-obvious decisions a reviewer would otherwise question. Update this list when adding another.

- **Synchronous SQLAlchemy under async FastAPI.** The trading loop ticks once per `SLEEPING_INTERVAL` (order of seconds), not per request. There is no concurrent DB load to justify async. Sync code is easier to read and test; the FastAPI routes that touch the DB are few and short.
- **Module-level DAL functions, not a repository class.** Single database, no swappable storage backend, no benefit from a class wrapper. Free functions keep call sites readable (`db.save_trailing_state(...)`) without forcing dependency injection.
- **Module-level lock + 1 call/sec in `exchange/kraken.py`.** Kraken's tier-0 limit allows more, but the bot has no latency budget that would benefit from optimizing it. A simple lock is correct, obvious, and cheap; a token bucket would add code without solving a real problem.
- **APScheduler started from the FastAPI `lifespan`.** Co-locating the scheduler with the API means one process, one health endpoint, one set of logs. The alternative — a separate worker container — would double the deployment surface for no operational gain at this scale.
- **`_safe_call` returns `None` on every error instead of raising.** Kraken outages, rate-limit hits, and transient network errors are *expected* during a long-running session; a missed tick is recoverable, a crashed bot is not. Callers must handle `None`; the trade-off is verbosity in callers vs. resilience overall.
- **Backtest and optimizer share one pure engine (`trading/engine.py`).** Configuration is passed in via `EngineConfig`, never read from module globals, so the same simulator runs against live state, a backtest request, and an optimizer candidate. This keeps both tools faithful to production behavior without duplicating the trailing-stop logic — at the cost of threading config through every call instead of reaching for globals.
- **The optimizer runs as a single-slot spawned process.** The Optuna search is CPU-bound and would block the event loop and starve the scheduler if run inline. `ProcessPoolExecutor(max_workers=1, spawn)` isolates it, one job at a time (a second submission returns `409`), with job state persisted in `optimizer_jobs` so a restart marks an interrupted job `failed` rather than leaving it `running`.
- **Two independent Optuna studies per search (`K_ACT` vs `MIN_MARGIN`), merged and ranked.** A single mixed study was highly seed-sensitive — different seeds found radically different optima. Splitting by activation type and merging globally is far more stable. Candidates rank by `robust_pnl = min(train_pnl, test_pnl)` so configs that overfit one half don't win.
- **Optimizer split is CONTINUE-only.** The simulation runs once over the full dataset and is partitioned at the train/test boundary, matching production where the bot never resets mid-history. (Earlier `RESET`/`BOTH` split methods were removed because they penalized the realistic continuous path.)
- **No global stop-loss.** Risk is bounded by the trailing-stop distance only. This is a deliberate strategy choice (early exits during normal volatility hurt expected value more than tail losses cost). Adding a hard floor is a strategy decision and must be discussed, not introduced as a "safety improvement."
- **`telegram` runs as a separate service, not inside `botc`.** PTB's `Application.run_polling()` blocks its thread indefinitely. Co-locating it with the scheduler would risk a dropped Telegram connection stalling the trading loop. A separate service means the trading engine is entirely unaffected by Telegram's availability.
- **`_SessionLogCollector` attaches to the root logger rather than threading a context object.** The alternative — passing a log buffer through the call graph (`trading_session` → `positions_manager` → `market_analyzer` → …) — would require modifying every function signature. The root-logger approach captures records from every module called during the session with zero changes to any call site.
- **`sessions.log_messages` is `Text` (JSON string), not `JSONB`.** `balance` and `pair_data` use `JSONB` because Grafana queries them with SQL operators (`->>`, `jsonb_array_elements`). `log_messages` is always fetched as a whole array, never queried by individual entry — `Text` avoids `JSONB` parse overhead with no query trade-off at this access pattern.

## Testing conventions

- Unit tests live in `tests/unit/` and never call external APIs. Kraken and DB calls are monkeypatched at the module level where the name is imported (e.g. `monkeypatch.setattr(positions_manager, "get_order_closing_price", ...)`).
- Integration tests in `tests/integration/` require `RUN_DB_INTEGRATION=true` and are skipped otherwise.
- `pytest-asyncio` is used for async FastAPI route tests.
