# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

BoTCoin is an autonomous EUR-based crypto trading bot for Kraken. It runs a trailing-stop strategy driven by ATR volatility classification and persists all state in PostgreSQL. Two Docker services: `botc` (trading engine + FastAPI on :8000) and `telegram` (Telegram bot + notify webhook on :8001).

This project has three concurrent goals: (1) run as a profitable bot, (2) serve as a portfolio piece reviewed by other engineers, (3) be a vehicle for the author to learn production-grade Python. That changes how to collaborate here: prefer clarity over cleverness, surface non-obvious "why" in PR descriptions, and treat code under `trading/` and `core/` as load-bearing — held to the testing/coverage bar — while `trading/backtest.py` and `trading/optimize_params.py` are research scripts (run manually, excluded from coverage, lower bar). When introducing a non-obvious design choice, add it to the **Design choices** section below.

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

The coverage gate is **80%**. `core/scheduler.py`, `trading/backtest.py`, and `trading/optimize_params.py` are excluded from coverage measurement.

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

### Database (`core/database.py`)

Four ORM models: `OHLCData`, `TrailingState`, `ClosedPosition`, `BotControl`. Direct SQLAlchemy (no async). All DAL functions are at module level (not a class). Migrations live in `scripts/migrations/versions/` managed by Alembic (`alembic.ini` points there).

`TrailingState` captures the full active position dict. Fields are optional during the open phase (`trailing_price`, `stop_price`, `closing_order_id`, etc.) and populated progressively as the position advances.

`BotControl` is a generic key/value table (`control_key` → `control_value`) accessed via `get_control_value` / `set_control_value`. Intended for runtime flags that should survive restarts and be toggled without redeploy; **currently has no production callers** — the table and DAL exist but no feature uses it yet.

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
- **`backtest.py` and `optimize_params.py` excluded from coverage.** Research code — run manually, iterated on freely, not part of the production path. Holding them to the same testing bar would slow exploration without protecting anything that ships.
- **No global stop-loss.** Risk is bounded by the trailing-stop distance only. This is a deliberate strategy choice (early exits during normal volatility hurt expected value more than tail losses cost). Adding a hard floor is a strategy decision and must be discussed, not introduced as a "safety improvement."

## Testing conventions

- Unit tests live in `tests/unit/` and never call external APIs. Kraken and DB calls are monkeypatched at the module level where the name is imported (e.g. `monkeypatch.setattr(positions_manager, "get_order_closing_price", ...)`).
- Integration tests in `tests/integration/` require `RUN_DB_INTEGRATION=true` and are skipped otherwise.
- `pytest-asyncio` is used for async FastAPI route tests.
