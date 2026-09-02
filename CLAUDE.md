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
PYTHONPATH=. pytest tests/unit/trading/test_positions_manager.py::test_finalize_close_returns_true_and_updates_pos_when_order_filled -v

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
3. **If the stop has fired (`is_closing`)** → `manage_close_position` drives the owed exit from breach to fill and returns a `ClosingState` the scheduler reacts to:
   - `FILLED` — the position dict carries the real `closing_price`/`pnl_percent`, so `record_position_closed` atomically inserts the closed position and deletes its `trailing_state` row in one transaction; the pair is dropped from `trailing_state`
   - `UNMANAGED` — the pair is left latched with nothing resting at Kraken; the scheduler adds it to `failed_pairs`
   - `PENDING` — nothing for the scheduler to do
4. **If no active position** → `create_position`
5. **If position is open** (`stop_at` not set) → `tick_position` (recalibrate, check activation, update trailing stop, trigger close if stop is hit)
6. Persist updated state → `save_trailing_state` (in a `finally`; see below)

Steps 3–6 (the position block) run only when `TRADING_ENABLED` is true. When it is false the per-pair loop `continue`s after recording market data, so the instance ingests OHLC, calibrates and records sessions but never trades. Steps 1–2 and the runtime/`pair_data` updates always run.

The whole per-pair body is wrapped in `try/except/finally`: one pair's failure is logged, recorded in `failed_pairs`, and skipped, so the remaining pairs still trade. A pair skipped for a missing price or ATR is recorded the same way — an unpriced pair is an *unmanaged* pair (its trailing stop is frozen), so it must not pass silently. A non-empty `failed_pairs` makes the session `pair_error`, **not** `failed`: the session did complete its work for every other pair. `failed` is reserved for what stops the session itself (balance or prices unavailable, an unhandled exception). Each failing pair feeds its own edge-triggered alert — see the alerting design choice below.

Step 6 lives in the `finally`, and is the **only** place the position block writes state (`_persist_pair_state`): a closing order placed just before a failure must still reach the DB, or the order lives on at Kraken with its id lost (A5). That is also why `positions_manager` never touches `core.database` — persistence is the scheduler's job, and the strategy code only mutates the position dict.

`core/runtime.py` holds thread-safe shared state so the FastAPI routes can read live prices/ATR without touching the DB.

**Invariants — do not break without explicit discussion:**

- The trailing stop is the **only** exit mechanism. There is no global stop-loss, no max-loss-per-position, no panic kill switch in code. Adding one is a strategy change, not a refactor.
- `closing_price` is an **estimate** until `finalize_close` confirms the fill: `close_position` writes it first (at order placement), and each `reprice_closing_order` chase overwrites it again (still an estimate, at the new limit price) while the order remains unfilled. `finalize_close` performs the final write with the real fill from Kraken and computes `pnl_percent`. Any code that reads `closing_price` before `finalize_close` returns `True` is reading an estimate.
- A position whose stop has fired is **not** open — `is_open` is `not stop_at`, and `tick_position` must not run on it, whether or not a closing order was placed. Step 3 of the loop resolves the exit before step 5 checks `is_open`.
- `_safe_call` in `exchange/kraken.py` swallows errors and returns `None`. Callers that don't handle `None` will silently corrupt state.

### Position lifecycle (`trading/positions_manager.py`)

- **create_position**: Calculates `activation_price` using either `K_ACT × ATR` (if `K_ACT` is set) or `K_STOP × ATR + MIN_MARGIN × entry_price`. Stores an inactive position.
- **tick_position**: Activates on price cross of `activation_price`, then tracks trailing price and updates stop. Recalibrates activation/stop when ATR drifts beyond `ATR_DESV_LIMIT`. On a stop breach it latches `stop_at` and logs the breach *before* calling `close_position` — same pattern as `activated_at` — so both survive a rejected or lost placement.
- **close_position**: Places a limit order at the current market price for a position whose `stop_at` is already latched by the caller. Mints a per-attempt `cl_ord_id` (`core/utils.new_cl_ord_id`) and writes it to `closing_request_id`, alongside the estimated `closing_price`, **before** calling `place_limit_order` — if the response is lost, the persisted state must already describe the attempt. Records `closing_order_id` on success. Returns `False` when `place_limit_order` returns `None`, which now means "outcome unknown", not "failed": the log line says "not confirmed", and `closing_request_id`/`closing_price` are left set for the next tick to resolve. Does NOT compute PnL. Announces a successful placement to Telegram on every attempt — the breach message says the stop fired, this one says an order actually rests at Kraken — while placement failures are logged only, since the manager retries every tick and the per-pair failure streak is what alerts.
- **manage_close_position**: The single entry point for a closing position; routes on **which id is present**, not on whether an order exists. `closing_order_id` set (**Confirmed** — Kraken gave us this id) → fetches the order by txid and hands the state to `_drive_closing_order`; a non-`None` outcome returns directly. `closing_order_id` absent but `closing_request_id` set (**Unconfirmed** — an `AddOrder` went out and its outcome was never learned) → resolves the id via `find_order_by_cl_ord_id`: a lookup failure (`None`) returns `UNMANAGED` with every field untouched — could not ask, decide nothing; a resolved txid is adopted onto `closing_order_id` (logged, not sent to Telegram — the recovery is the machine doing its job, not an operator alert) and driven through `_drive_closing_order` on the **same** tick, so an already-filled recovered order returns `FILLED` immediately instead of a tick late; an absent result (both `ClosedOrders` and `OpenOrders` answered and neither had it) is genuine evidence nothing landed, so `_clear_closing_fields` runs and `stop_at` survives. Neither id set, or just cleared by either branch above → `refresh_position` (a latched position never reaches `tick_position`, so nothing else resizes it, and a stale volume may be why the previous attempt was rejected) then `close_position`. Returns `FILLED` / `PENDING` / `UNMANAGED`; `FILLED` still leaves the DB write to the **scheduler** (persistence stays out of `positions_manager`).
- **_drive_closing_order**: The only place that branches on `OrderStatus` for a closing order. `state is None` (API error) or `PENDING` (accepted, not yet on the book) → `ClosingState.PENDING`, untouched. `NOT_FOUND`/`UNKNOWN` → logs and returns `UNMANAGED`, fields untouched — never guessed at (see the unresolvable-order design choice). `OPEN` → delegates to `reprice_closing_order`, mapping its `bool` to `PENDING`/`UNMANAGED`. Otherwise (a terminal status) → `finalize_close`; a confirmed fill returns `FILLED`, and a terminal-but-unusable order has its fields cleared (`_clear_closing_fields`) and returns `None` so `manage_close_position` falls through to re-place on the **same** tick rather than a full interval later.
- **finalize_close**: Interprets an order **already known to be terminal** — no API call (the caller already fetched `state`), no field clearing (that is `_drive_closing_order`'s job). Two outcomes are finalized — `closing_price` overwritten with the real fill, `pnl_percent` computed: a `CLOSED` order with a positive average fill price, and a `CANCELED` order that turned out to be fully executed (`vol_exec >= vol`, with a usable average price). The second case is a cancel that raced a complete fill: Kraken confirms the cancellation but nothing is left to manage, so it is a finished trade, and it records `volume` as the order's real `vol_exec`. Fullness is measured against the order's own `vol`, never `pos["volume"]`, which can drift from what actually rests at Kraken; when Kraken omits `vol` it reads `0.0` and the check fails closed. Anything else returns `False` and leaves every field as it found them.
- **reprice_closing_order**: Chases the fill of an order **already known to be `OPEN`** (the caller has already handled `None`, the unresolvable statuses, `PENDING`, and any terminal status). Cancels and replaces at the current price once the old one has gone stale, sizing the replacement from the order's own `vol - vol_exec` (see the remainder-sizing design choice); on replacement it **drops** `closing_order_id` and writes a fresh `closing_request_id`, so a lost placement response resolves via the Unconfirmed sub-state next tick instead of freezing. Returns `False` only when the previous order is off the book and no replacement was placed. See [`docs/operations.md` § Closing order repricing](docs/operations.md#closing-order-repricing) for the operator-facing account.

  A partial fill is **not** reconciled. `refresh_position` resizes the position from the pair's target allocation against the current balance on the next tick, so the bot converges on a correct size rather than tracking the unfilled remainder; if what is left falls below `MIN_VALUE` the position is dropped and the residual base amount stays untracked. Its fee is not reconciled either — `finalize_close` records the fee of the order it finalizes, and nothing else. Charging that fee while its proceeds go uncounted would bias `pnl_percent`, not correct it; the warning names the amount so the operator can see it.

### Volatility classification (`trading/market_analyzer.py` + `trading/parameters_manager.py`)

ATR is computed from OHLC stored in `ohlc_data`. `get_volatility_level` classifies `ATR/close` (a dimensionless ratio, so the levels survive price drift) into five levels (LL/LV/MV/HV/HH) using precomputed percentile boundaries of that ratio. `K_STOP` for each level comes from `PAIR_STOP_PCT_<LEVEL>` — the percentile of historically observed K-values (structural noise analysis via pivot detection), where each historical candle is bucketed by that same `ATR/close` ratio. `market_analyzer.atr_ratio_percentiles` is the single source of those boundaries, shared by the classifier, the calibration, the backtest and the optimizer, so the partition that groups K-values cannot drift away from the one that selects them.

### Trading tools — backtest & optimizer (`trading/engine.py`, `trading/backtest.py`, `trading/optimizer/`)

Offline analysis tools exposed as authenticated HTTP endpoints on the `botc` service. They read stored OHLC and the live calibration cache but **never mutate trading state**.

- **`trading/engine.py`** — the pure simulation engine (`simulate_operations`). A leaf module: it imports nothing from `core.config` or `parameters_manager`; all configuration is passed in via `EngineConfig`, so the same simulator runs against live state, a backtest request, or an optimizer candidate. It mirrors the live `positions_manager` logic (activation, trailing stop, ATR re-anchoring) so simulations behave like production.
- **`trading/backtest.py`** — `run_backtest(req) -> BacktestResult`. Pure library (no CLI, no prints). Builds an `EngineConfig` from cached or recomputed calibration and runs the engine. Behind synchronous `POST /backtest`.
- **`trading/optimizer/search.py`** — `run_optimize` runs two **independent** Optuna TPE studies per search (a `K_ACT` activation branch and a `MIN_MARGIN` branch), each over per-level stop percentiles, then merges and ranks candidates by `robust_pnl = min(train_pnl, test_pnl)`. The train/test split is evaluated in a single continuous run over the full dataset (CONTINUE-only). Modes: `OPTIMIZE` (TPE search), `CURRENT` (evaluate the live `.env` config, 1 trial), `AUTO` (`run_auto_optimize`: multi-seed convergence loop that escalates `n_trials` until `min_agree` of `n_seeds` agree, then compares against `CURRENT`). In AUTO the per-seed studies are kept alive across escalation levels and only the *delta* of trials is run each level (warm-start, see Design choices); OHLC + calibration are loaded once via `_build_eval_context` and shared by every seed/level. `mode` is required.
- **`trading/optimizer/jobs.py`** — `JobStore`, an N-slot async job manager (capacity set by `MAX_CONCURRENT_JOBS`). `try_start` inserts an `optimizer_jobs` row and submits the work to a `ProcessPoolExecutor(max_workers=N, spawn)`; `supervise(job_id)` awaits that job's future and persists the result; a submission when all slots are full raises `OptimizerBusyError` (→ `409`); `MAX_CONCURRENT_JOBS=0` disables the optimizer entirely (→ `503`). Telegram is notified on start, completion, and failure. `worker.py` is the picklable child entry point.
- **Calibration cache**: the live `core/runtime.py` holds the snapshot of structural events + ATR percentiles. The spawned worker starts with an empty runtime, so `try_start` snapshots the calibration in the parent and passes it explicitly; a sliced request passes `None` and the worker recomputes the calibration **from full history up to the window `end`** (not from the slice itself — see the Design choice below).
- **API**: `api/routes/backtest.py` and `api/routes/optimizer.py`; request/response models in `api/schemas.py`. All endpoints require the `X-Api-Token` header.

### Database (`core/database.py` + `core/db/`)

Seven ORM models: `OHLCData`, `TrailingState`, `ClosedPosition`, `BotControl`, `OptimizerJob`, `SessionRecord`, `PairConfig`. Direct SQLAlchemy (no async). All DAL functions are at module level (not a class). Migrations live in `scripts/migrations/versions/` managed by Alembic (`alembic.ini` points there).

`core/database.py` is the **facade**: it owns the engine, the sessionmaker and `get_session`, and re-exports the whole DAL, so every call site keeps importing `core.database` and using `db.<function>`. The queries live under `core/db/` split by domain — `models.py` (declarative `Base` + the seven models + the `Decimal` converters), `ohlc.py`, `positions.py` (closed positions + trailing state), `control.py` (`bot_control` + `pair_config` + session telemetry) and `jobs.py`. The domain modules never import `core.database` at module scope; they call `core/db/session.py::open_session`, which resolves `core.database.get_session` at *call* time. That keeps the facade→domain import one-directional and keeps `core.database.get_session` the single seam for the transaction scope.

When changing an ORM model's table constraints, update **both** the model in `core/db/models.py` and the corresponding Alembic migration — they are not auto-synced, and CI builds the schema from migrations (a drift between the two recently allowed an invalid `mode` to pass the model but fail the migration's check constraint).

`TrailingState` captures the full active position dict. Fields are optional during the open phase (`trailing_price`, `stop_price`, `closing_order_id`, `closing_request_id`, etc.) and populated progressively as the position advances. `closing_request_id` holds the client-chosen id (`cl_ord_id`) of a placement whose outcome is unknown — set by `close_position`/`reprice_closing_order` before every placement attempt, resolved by `find_order_by_cl_ord_id` — and is the one field that can be set while `closing_order_id` is absent (the Unconfirmed sub-state).

`BotControl` is a generic key/value table (`control_key` → `control_value`, both `Text`) accessed via `get_control_value` / `set_control_value`. It holds runtime state that must survive restarts without a redeploy or a schema change. Current keys: `bot_paused` (the pause flag), `latest_balance` and `latest_pair_data` (written by `finalize_session`, read by Grafana), and a per-pair OHLC backfill watermark written by `market_analyzer`.

`SessionRecord` is written once at the start of every `trading_session()` call (status `running`) and updated in the `finally` block with the final status and captured log lines. Statuses are `running`, `completed`, `pair_error` (completed, but one or more pairs were skipped), `failed` (the session itself could not do its work) and `paused`. It is the primary data source for the Grafana Sessions row. `status` is a plain `String(16)` with no check constraint, so adding a value needs no migration — only a `sessions` panel update in `services/grafana/dashboards/botc.json`.

`OptimizerJob` backs the async optimizer (`optimizer_jobs` table). A row is inserted `running` by `JobStore.try_start` and updated to `completed` (with the JSONB result) or `failed`. A `ck_opt_jobs_mode_valid` check constraint restricts `mode` to `OPTIMIZE`/`CURRENT`/`AUTO`; `ck_opt_jobs_status_valid` restricts `status` to `running`/`completed`/`failed`.

`PairConfig` (`pair_config` table) — DB-authoritative per-pair config, seeded once from `.env` on first boot. Holds `target_pct`, `hodl_pct`, `k_act`, `min_margin`, and the five `stop_pct_<level>` values per pair. Managed by `core/config_store.py`; editable at runtime via `PATCH /config/{pair}` and Telegram `/setconfig`.

### Exchange wrapper (`exchange/kraken.py`)

Rate-limited to 1 call/second via a module-level lock. `_safe_call` wraps every API call: returns `result` on success, logs and returns `None` on any error. Callers must always handle `None`.

This module is also the anti-corruption boundary for Kraken's vocabulary. `OrderStatus` is the normalized order-status enum and `map_order_status` the public translator (reusable by any future order lookup): Kraken's `expired` folds into `CANCELED` — both mean off the book with no further fills — and anything unmodelled becomes `UNKNOWN`. Two members have no Kraken counterpart: `NOT_FOUND` (Kraken answered but does not know the txid) and `UNKNOWN`. `get_order_state` returns `None` only for "could not ask" (API error), so a transient outage is distinguishable from an order that will never resolve. No code outside this module compares raw status strings.

`place_limit_order` accepts an optional `cl_ord_id`, merged into the `AddOrder` payload only when not `None` (key absent, not `null`, when omitted — both production call sites always pass one). `find_order_by_cl_ord_id(cl_ord_id) -> OrderLookup | None` resolves a client id to Kraken's txid and order state when the txid itself was never received (a lost `AddOrder` response): `None` means the lookup itself failed (treat as "unknown", never "absent"); `OrderLookup(txid=None, state=None)` means both `ClosedOrders` and `OpenOrders` answered and neither had it. Tries `OpenOrders` first, then `ClosedOrders`: the loop returns on the first endpoint that matches, so their order *is* the precedence rule, and a resting order must win over a terminal one carrying the same id (adopting the dead txid would finalize the trade and orphan a live exit). The cost is a second call in the common case — a limit placed at the market price has usually already filled by the time the resolver runs — which is cheap on a path that only runs after a lost response. No `start`/`end` bound on either call, since any bound computed from our clock could exclude the very order being resolved. Each returned order's own `cl_ord_id` is checked before its txid is adopted — if Kraken ever ignored the filter, this is the difference between failing loudly and adopting a stranger's txid. Two kinds of unreadable answer are tracked in one `unresolved` flag: an endpoint that errored, and an endpoint that returned rows *none* of which echo the id ("not ours" and "the echo is gone" are indistinguishable). Either makes the whole lookup return `None` rather than the absence that would license a re-place — but only after both endpoints have been tried, so one failing endpoint never hides a clean hit on the other. Both are private calls, so neither is covered by the public-path rate limiter; they run only on the rare Unconfirmed path. `get_order_state` and `find_order_by_cl_ord_id` share a `_build_order_state` helper so a raw Kraken order object is interpreted identically by both paths.

### Services

`services/telegram/` is an independent FastAPI app. It communicates with the trading engine exclusively through the REST API (`services/telegram/client.py` → `http://botc:8000`). The `/notify` endpoint receives Telegram messages posted by `core/logging.py` when `to_telegram=True`.

## Configuration

Per-pair parameters are loaded from env vars by `core/config.py` into the `TRADING_PARAMS` dict on startup. Since Phase 1, these values are also persisted in the `pair_config` DB table (seeded from `.env` on first boot via `config_store.load_or_seed()`); the DB is now the authoritative source and parameters can be changed at runtime via `PATCH /config/{pair}` without a restart. The key pattern:

- `PAIR_TARGET_PCT` / `PAIR_HODL_PCT`: Portfolio allocation (inventory manager)
- `PAIR_K_ACT`: Activation ATR multiplier; `0` = immediate activation (single per pair — per-side `PAIR_SELL_K_ACT` / `PAIR_BUY_K_ACT` variants have been removed)
- `PAIR_MIN_MARGIN`: Minimum price margin from entry, expressed as fraction of entry price (single per pair — per-side `PAIR_SELL_MIN_MARGIN` / `PAIR_BUY_MIN_MARGIN` variants have been removed)
- `PAIR_STOP_PCT_LL` … `_HH`: K-stop percentile per volatility level
- `SESSION_FAILURE_ALERT_THRESHOLD` (default 3): consecutive failures before one edge-triggered Telegram alert fires; a single recovery message follows the next success. Shared by all three alerting streaks — session, per-pair, and session-overrun (see Design choices).

## Design choices

Non-obvious decisions a reviewer would otherwise question. Update this list when adding another.

- **Synchronous SQLAlchemy under async FastAPI.** The trading loop ticks once per `SLEEPING_INTERVAL`, not per request, so there is no concurrent DB load to justify async — sync code is easier to read and test.
- **Module-level DAL functions, not a repository class.** Single database, no swappable backend, so free functions (`db.save_trailing_state(...)`) keep call sites readable without forcing dependency injection.
- **Module-level lock + 1 call/sec in `exchange/kraken.py`.** Kraken's tier-0 limit allows more, but the bot has no latency budget to justify a token bucket over a simple lock.
- **APScheduler started from the FastAPI `lifespan`.** One process, one health endpoint, one set of logs — a separate worker container would double the deployment surface for no gain at this scale.
- **`_safe_call` returns `None` on every error instead of raising.** A missed tick from a transient Kraken/network error is recoverable; a crashed bot is not — callers must handle `None`.
- **Every blocking I/O call in the scheduler loop is time-bounded.** The loop's single-worker executor means one hung tick blocks all future ticks — this happened when a Kraken call inherited krakenex's default `timeout=None` and stalled forever. Both Kraken (`KRAKEN_HTTP_TIMEOUT`) and PostgreSQL (`_build_connect_args()`) are now capped, turning a stall into a recoverable missed tick; `cleanup_orphaned_sessions()` reconciles any leftover `running` sessions at startup.
- **Alerting is three independent edge-triggered streaks (session, per-pair, session-overrun), and no alert carries a reason.** A shared streak would let one pair's recovery silence another's ongoing failure, a per-tick alert would spam, and a reason captured at the crossing would go stale under a streak that never resets. See [`docs/specs/session-failure-alerts-design.md`](docs/specs/session-failure-alerts-design.md).
- **Session-overrun early-warning is derived from session duration, not skipped-tick events.** A skip-based signal only observes the gaps *between* sessions, so it flaps (false "recovered" pings mid-incident); measuring every completed session's wall-clock against `SLEEPING_INTERVAL` is stable, and was added after the July 2026 `e2-micro` CPU-starvation outage, which the failure-streak alert alone caught only hours late. Its own recovery message is suppressed when a failure-recovery fires on the same tick (`core/scheduler.py`'s `elif ... and not failure_recovered:`), since the failure-recovery message already signals normal operation and a second ping would be redundant.
- **Prices/ATR are kept at full float precision internally; rounding happens only at the order boundary and at display, per the pair's Kraken precision.** A hardcoded `round(x, 1)` used to be invisible on XBTEUR but destroyed low-value pairs (USDCEUR's ATR ~0.0008 rounded to `0.0`, collapsing the stop distance); `round_price`/`round_volume` in `core/utils.py` now round only at `place_limit_order` and at display (logs, API) — ATR itself is never rounded into state/DB, since it drives ATR-drift detection at finer-than-`pair_decimals` precision. `round_price` reads `config.PAIRS`, so it only works in the trading-engine process; the Telegram process has empty metadata and must rely on the API's pre-rounding, never call `round_price` itself.
- **A simulated run is scored with its open position valued, not only what it realized.** A leg is booked only when it closes, so a run that ends mid-position (every run does — the engine always flips to the opposite side after an exit) dropped the move since its last operation. `trading/engine.py::mark_to_market` values that leg at the run's final price; `_score_run` and `_split_scores_from_single_run` mark each half at the price where that half ends (a valuation, not a liquidation: the position carries on, so no exit fee is charged), and `/backtest` reports `marked_pnl_pct`/`unrealized_pnl_pct` beside the realized `total_pnl_pct`. The omission was worth ~13 points over 11 weekly windows of XBTEUR, always against the bot, and it grows as the window shortens.
- **The engine takes a calibration *schedule*, not one fixed calibration.** Production recalibrates every `PARAM_SESSIONS` ticks over all history up to that moment, so `K_STOP` and the level thresholds move as a run advances; `EngineConfig.calibration_schedule` replays that as a step function over bar indices. `trading/backtest.py` and `trading/optimizer/` both build one at `core.config.RECALIBRATION_BARS`, from `market_analyzer.build_calibration_inputs`. `recalibration_bars` on either request overrides the cadence, and `0` restores the old single-calibration behaviour. This is not a refinement: on XBTEUR over 2025-01-01..2026-03-31 a single calibration scored `mm=0.030/0.9` at -28.2% where the schedule scores it at -1.0%, and it reorders candidates rather than shifting them all.
- **Backtest and optimizer share one pure engine (`trading/engine.py`).** Configuration is passed in via `EngineConfig`, never read from module globals, so the same simulator runs against live state, a backtest request, and an optimizer candidate without duplicating the trailing-stop logic.
- **The optimizer runs in a `ProcessPoolExecutor(spawn)` sized by `MAX_CONCURRENT_JOBS`.** The Optuna search is CPU-bound and would block the event loop if run inline; job state persists in Postgres so a restart marks interrupted jobs `failed`, never `running`.
- **Two independent Optuna studies per search (`K_ACT` vs `MIN_MARGIN`), merged and ranked.** A single mixed study was highly seed-sensitive; splitting by activation type is far more stable, and ranking by `robust_pnl = min(train_pnl, test_pnl)` keeps configs that overfit one half from winning.
- **AUTO warm-starts the escalation instead of restarting.** Each seed's studies stay alive across escalation levels, so raising the trial budget only runs the *delta* and continues the TPE search — equivalent-or-better search quality, measured ~2–9× cheaper than rebuilding from scratch.
- **The `K_ACT` and `MIN_MARGIN` branches run in parallel (2-process pool).** Optuna in-memory studies pickle cleanly, so each branch's study round-trips through a worker process; gated by `_PARALLEL_MIN_TRIALS` so small runs (and unit tests) skip the spawn overhead.
- **Optimizer split is CONTINUE-only.** The simulation runs once over the full dataset, partitioned at the train/test boundary, matching production where the bot never resets mid-history.
- **Sliced jobs simulate `[start, end]` but calibrate over `[T0, end]`, not over the slice.** Calibrating from the slice alone made K_STOP depend on window length — unstable enough that a one-day shift of `end` could flip a result's sign on the identical trades — so the *base* calibration always uses full history up to `end`. That base did carry look-ahead for every bar before `end`; the schedule is what removes it, since each of its points sees only history up to its own bar. The base now only applies before the schedule's first entry, and entry 0 always exists.
- **Building the schedule costs real time, and no coarser cadence is safe.** Each point re-runs `analyze_structural_noise` over history up to its own bar: about 380 s for a 15-month XBTEUR window (907 points). It is paid once per run and shared by every trial, which an async optimizer job absorbs and a synchronous `/backtest` on a long window does not — hence `recalibration_bars`. Coarsening is not a free approximation: at 192 bars instead of 48, `mm=0.035/0.9` moves from +34.3% to -1.4%, because a recalibration landing either side of a bar changes whether a stop fires.
- **Search grids are supplied per request (`SearchSpace`), not hardcoded.** Grid coarseness is an experiment input (coarser grids let seeds agree; varying capacity probes overfitting), and every job stores its `search_space` in `optimizer_jobs.request` so runs are self-documenting.
- **AUTO convergence is judged on the config (param signature), not the score.** Two seeds reaching the same `robust_pnl` via different configs is a flat/noisy landscape, not a stable optimum, and deployment ships one config — so agreeing on *which* config is the honest robustness test.
- **No global stop-loss.** Risk is bounded by the trailing-stop distance only — a deliberate strategy choice, since early exits during normal volatility hurt expected value more than tail losses cost; adding a hard floor is a strategy decision, not a "safety improvement."
- **`TRADING_ENABLED` is a deploy-time mode flag, not a runtime risk control.** It lets the full stack run as a non-trading replica (OHLC ingestion, calibration, optimizer, Telegram) without a bespoke script; it must stay `true` in production and never be flipped on an instance holding open positions, or their trailing stop freezes. This does not contradict the "no panic kill switch" invariant above: that invariant forbids an in-flight risk override on a *trading* instance, while this flag is decided up front, before the instance ever starts trading — a different kind of thing.
- **`telegram` runs as a separate service, not inside `botc`.** PTB's `Application.run_polling()` blocks its thread indefinitely — co-locating it would risk a dropped Telegram connection stalling the trading loop.
- **The telegram container is isolated at both the env and the import boundary.** An explicit `environment:` allowlist keeps `KRAKEN_API_*`/`POSTGRES_PASSWORD` out of the internet-polling bot, and `build_pairs_map` is imported inside a function (not at module level) so `exchange.kraken` (pandas/numpy/krakenex) never loads into a process that never trades.
- **`_SessionLogCollector` attaches to the `botc` logger rather than threading a context object.** A log buffer threaded through every call signature would require touching every function; attaching a handler to the `botc` logger (not root) captures every module's session activity with zero call-site changes, at the cost of missing the DAL's own stdlib-logger errors (which the surrounding code still logs through `core.logging`).
- **`JSONB` is reserved for payloads the application reads back as structured data; everything else stores JSON as `Text`.** Only `optimizer_jobs.request`/`result` are `JSONB` (read back as dicts); `sessions.log_messages` and `bot_control.control_value` stay `Text` since they are fetched whole or cast at read time, so `JSONB` would add parse overhead with no query benefit.
- **Dynamic pair config is DB-authoritative, seeded once from `.env`.** A dedicated typed `pair_config` table beats the generic `BotControl` store because typed columns enable schema-level validation and clean `PATCH` semantics; `.env` is only the first-boot seed. See [`docs/specs/dynamic-pair-config-design.md`](docs/specs/dynamic-pair-config-design.md).
- **`stop_pct` changes recalc `K_STOP` at the next session via a runtime dirty flag.** Keeps heavy calibration (pivot detection, ATR percentiles) inside the scheduler thread and off the `PATCH` request path.
- **The level reference is always the current price; the distance anchor is a separate argument.** `get_k_stop` classifies `ATR/close`, and the ATR handed to it is always the current one, so a stale denominator — the entry price of a position that has waited days, or the favourable extreme since activation — would make the ratio describe no moment at all. `calculate_activation_distance` and `calculate_stop_price` therefore take the anchor (`entry_price`, `trailing_price`) and the classification reference as two parameters, and `trading/engine.py` mirrors both.
- **`k_act`/`min_margin` are single per pair; `K_STOP` stays per-side.** The per-side `k_act`/`min_margin` variants added config complexity with no observable benefit; `K_STOP` is genuinely derived per-side from pivot analysis, so it cannot collapse the same way.
- **Hitting the trailing stop is a latched, irrevocable decision (`stop_at`).** Written the moment the breach is detected, before the placement attempt, so a rejected or lost order still records the exit as owed instead of letting the next tick re-enter `tick_position` and revoke the decision. See [`docs/specs/stop-latched-close-design.md`](docs/specs/stop-latched-close-design.md).
- **`is_open` is `not stop_at`.** `closing_order_id`/`closing_request_id` are only ever written once `stop_at` is already latched, so `stop_at` alone is the single choke point — a position whose stop has fired is not open, whether or not an order was ever placed for it.
- **`manage_close_position` is the single entry point for a closing position; the scheduler owns none of that state machine.** Consolidating a chain of scheduler-side checks into one three-value `ClosingState` return removed several places where `pos is None`/`stop_at`/`closing_order_id` were each re-derived; `FILLED` still leaves every DB write to the scheduler (single-writer rule, A5). See [`docs/specs/closing-state-machine-design.md`](docs/specs/closing-state-machine-design.md).
- **An order Kraken cannot resolve is reported unmanaged, never guessed at.** `NOT_FOUND`/`UNKNOWN` leave `closing_order_id`/`closing_price` untouched rather than clearing them and re-placing, since a double sell is unrecoverable while a frozen-but-alerted pair is not.
- **Finalizing runs before managing, inside the single dispatch.** A terminal order that cannot be finalized is cleared before the placement branch, so `_drive_closing_order` re-places a replacement the *same* tick rather than a tick late.
- **Every order the bot places carries a per-attempt `cl_ord_id`, so a lost `AddOrder` response is recoverable.** One id per attempt (not per position, not `userref`), because Kraken requires uniqueness among open orders and `QueryOrders` can only look up by `txid` — exactly what is missing in the lost-response case.
- **A closing position routes on whether the placement was *confirmed*, not on whether an order exists.** The same "not found" lookup answer means opposite things depending on which id is set — `UNMANAGED` if `closing_order_id` was already confirmed, genuine absence if only `closing_request_id` was ever set — so the two ids are kept separate rather than collapsed.
- **`_drive_closing_order` is the only place that branches on `OrderStatus` for the management decision.** `finalize_close` and `reprice_closing_order` each interpret a status class the selector has already established, never re-deriving it; `None` from the dispatch means "gone, place a new one this tick."
- **The reprice remainder is sized from the order's `vol - vol_exec`, not from `pos["volume"]`.** `place_limit_order` rounds to `lot_decimals` before sending, so the two can drift by a lot tick — the earlier subtraction turned a fully executed order into an unplaceable dust remainder and lost a finished trade.
- **`SLEEPING_INTERVAL` is bounded from below by Kraken's trading rate limiter, not the REST call counter.** `CancelOrder`'s penalty scales inversely with how long the order rested, so a shorter interval raises the per-cancel penalty while shrinking the decay window from both ends at once — fine at 60 s, check this before lowering it.

## Testing conventions

- Unit tests live in `tests/unit/` and never call external APIs. Kraken and DB calls are monkeypatched at the module level where the name is imported (e.g. `monkeypatch.setattr(positions_manager, "get_order_state", ...)`).
- Integration tests in `tests/integration/` require `RUN_DB_INTEGRATION=true` and are skipped otherwise.
- `pytest-asyncio` is used for async FastAPI route tests.
