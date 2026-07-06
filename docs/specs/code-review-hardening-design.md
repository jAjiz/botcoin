# Code-review hardening design

**Date:** 2026-07-06
**Branch:** `docs/code-review-hardening` (documentation only; implementation branches per phase are proposed in the plan)

## Problem

A full review of the application code (`core/`, `trading/`, `api/`, `exchange/`,
`services/`, Docker and migrations — ~5,100 lines) surfaced a set of defects,
security gaps and improvement opportunities. Three of the defects can leave the
bot **permanently inoperative without raising any alert** (an infinite loop in
pivot detection, and two failure modes in the closing-order lifecycle that put a
session into an endless failure loop). Several others degrade resilience,
reproducibility or least-privilege posture.

This spec records every finding with its location, failure scenario and agreed
remediation, so the fixes can be implemented incrementally (see the companion
plan) without re-deriving the analysis. Findings that touch trading strategy
were resolved with the operator; the decision for the stuck-closing-order case
(A4) is recorded below.

## Goal

- Eliminate the three "silent permanent failure" defects (A1–A3).
- Make the closing-order lifecycle robust to real Kraken outcomes: canceled,
  expired, partially filled, and never-filled orders (A2, A4).
- Harden the process boundaries (event loop, process pool, per-pair isolation)
  and the deployment surface (secret scoping, migrations) — B and C findings.
- Record the smaller refactors and doc-drift fixes (D findings) so they can be
  picked up opportunistically.

**Non-goals:** no change to the trading strategy itself. The trailing stop
remains the only exit mechanism; there is still no global stop-loss. A4 changes
*how* an already-decided exit gets executed, not *when* an exit is decided.

---

## Findings — A: critical defects

### A1. Infinite loop in `detect_pivots` freezes the scheduler thread

**Location:** `trading/market_analyzer.py:122-136` (false-pivot removal loop).

**Defect:** the `while` loop handles two cases — same pivot type (always deletes
one pivot) and different type with *different* price (deletes one or advances
`i`). When two consecutive pivots have **different types and exactly equal
prices**, neither branch runs: `i` never advances, nothing is deleted, and the
loop never terminates.

**Failure scenario:** flat candles (`high == low`, common on low-volatility
pairs or quiet stablecoin periods) make `argrelextrema` with
`less_equal`/`greater_equal` emit min and max pivots at the same price. The loop
runs inside `calculate_trading_parameters` on the single APScheduler worker
thread — the exact hang class that was previously closed for I/O, reintroduced
via CPU: **every future tick is skipped with "max instances reached", no
exception, no alert**. The same code path can hang backtest requests and
optimizer workers.

**Fix:** treat an equal-price, different-type pivot pair as a zero-amplitude
swing (it is strictly smaller than `MINIMUM_CHANGE_PCT`) and delete the second
pivot:

```python
if curr_type == next_type:
    ...  # unchanged
elif curr_price == next_price or abs(curr_price - next_price) / curr_price < MINIMUM_CHANGE_PCT:
    del pivots[i + 1]
else:
    i += 1
```

Regression test: run `detect_pivots` on a fully flat OHLC frame (constant
`high == low`) — before the fix this hangs; after it must return.

### A2. Canceled/expired closing orders corrupt state and wedge the pair

**Location:** `exchange/kraken.py:97-111` (`get_order_closing_price`) and
`trading/positions_manager.py:133-149` (`is_closing_complete`).

**Defect:** `get_order_closing_price` only treats `None/pending/open` as "not
done". A `canceled` or `expired` order with no fill has Kraken `price`
`"0.00000"` → the function returns `0.0` (not `None`) → `is_closing_complete`
computes a −100% PnL and writes `closing_price = 0` → `save_closed_position`
violates the `ck_closed_positions_closing_price_positive` check constraint →
the session raises. The trailing state is never deleted, so **every subsequent
session fails the same way, forever** (and pairs after it in the loop are never
processed). A partially-filled-then-canceled order is even worse: it would
record the partial average price as the full close with the full volume.

**Fix:** make order-state handling explicit.

- `exchange/kraken.py`: replace `get_order_closing_price` with
  `get_order_state(order_id) -> OrderState | None` returning
  `status`, `avg_price` (`float`), `vol_exec` (`float`) — `None` only on API
  error. Callers stop inferring state from a bare price.
- `trading/positions_manager.py::is_closing_complete`:
  - `status in (pending, open)` → return `False` (unchanged behaviour).
  - `status == "closed"` and `avg_price > 0` → real fill: overwrite
    `closing_price`, compute `pnl_percent`, return `True` (unchanged behaviour).
  - `status in ("canceled", "expired")` → the closing attempt is dead: log a
    warning (`to_telegram=True`), remove `closing_order_id`, `closing_price` and
    `closing_requested_at` from the position, return `False`. The position
    becomes *open* again (`is_open` → `True`) and is managed on the same tick;
    `refresh_position` already recomputes the volume from the **real** balance,
    so a partial fill self-heals (the remainder keeps trading, the filled part
    is reflected in the balance).
- Defensive: `status == "closed"` with `avg_price <= 0` should be impossible;
  log an error and return `False` rather than writing a zero price.

### A3. Crash between `save_closed_position` and `delete_trailing_state` wedges the pair

**Location:** `core/scheduler.py:138-145` (the existing TODO underestimates the
impact).

**Defect:** the close-completion path runs two separate transactions: INSERT
into `closed_positions`, then DELETE from `trailing_state`. If the process dies
between them, the next session re-detects the completed close and re-inserts —
violating the `closing_order_id` UNIQUE constraint → exception → **permanent
session-failure loop** (not the "double-record" the TODO describes; the unique
constraint turns it into a wedge).

**Fix:** one DAL function, one transaction, idempotent insert:

```python
def record_position_closed(pair: str, position_data: dict[str, Any]) -> None:
    """Insert the closed position and delete the pair's trailing state in a
    single transaction. The insert is idempotent (ON CONFLICT (closing_order_id)
    DO NOTHING) so a retry after a crash between the two steps converges."""
```

Implemented with `pg_insert(ClosedPosition).on_conflict_do_nothing(index_elements=["closing_order_id"])`
plus the `TrailingState` delete inside one `get_session()` block (the pattern
already exists in `save_ohlc_data`). The scheduler replaces the two calls with
this one; `save_closed_position` is removed (the scheduler was its only
production caller) and `delete_trailing_state` stays (still used for dropped
positions).

### A4. Closing limit order that never fills — reprice to market

**Location:** `trading/positions_manager.py:216-242` (`close_position`),
`core/scheduler.py:138` (`is_closing_complete` gate).

**Defect:** `close_position` places a limit order at the current price. If the
market moves away before it fills, the order sits open indefinitely:
`is_closing_complete` returns `False`, `is_open` returns `False`, and the
position is in limbo — no trailing, no exit — while the market runs against it.
There is no timeout or reprice.

**Decision (operator, 2026-07-06):** once the trading logic has decided to close
a position, the intent is *execution*. If the closing order has not filled by
the next session tick, **update its price to the current market price**. This is
not a strategy change — the exit decision was already made by the trailing stop;
repricing only chases the fill.

**Design:**

- New Kraken wrapper `cancel_order(order_id) -> bool` (`CancelOrder`, private,
  same `_safe_call` + timeout conventions).
- New `positions_manager.reprice_closing_order(pair, pos, last_prices)` called
  from the scheduler when a position has a `closing_order_id` and
  `is_closing_complete` returned `False`:
  1. Fetch the order state (`get_order_state`, shared with A2). API error →
     do nothing this tick (recoverable missed tick, as everywhere else).
  2. `status` not `open`/`pending` → do nothing; the A2 path handles
     closed/canceled/expired on the next `is_closing_complete` call.
  3. **Skip if partially filled** (`vol_exec > 0`): the order is executing at
     its price; canceling mid-fill and re-placing would fragment fills and
     complicate PnL. Revisit only if partially-stuck orders are observed in
     practice.
  4. Skip if the current price, formatted to the pair's `pair_decimals`, equals
     the order's limit price (re-placing an identical order would only lose
     queue priority).
  5. Otherwise: `cancel_order`; if the cancel fails (e.g. the order filled in
     the race window) → do nothing, next tick resolves it. On success, place a
     new limit at the current market price for the position's volume and update
     `closing_order_id`, `closing_price` (estimate, as today) and
     `closing_requested_at`. The state diff is persisted by the existing
     end-of-pair-iteration save.
- Ordering in the scheduler per pair: `is_closing_complete` → (if `False` and a
  closing order exists) `reprice_closing_order` → existing create/tick logic.
- No new env tunable: repricing happens on the natural session cadence
  (`SLEEPING_INTERVAL`), which is the same granularity every other decision
  uses.

### A5. Duplicate closing order after a crash (residual risk, mitigation only)

**Location:** `trading/positions_manager.py:216-242` + `core/scheduler.py:150-159`.

**Defect:** if the process dies after `AddOrder` succeeds but before the state
save a few lines later, the restarted bot doesn't know the order exists and
places a second one (a real double sell/buy on the exchange). The window is
milliseconds, but the impact is monetary.

**Mitigation (this phase):** persist the trailing state **immediately** after a
successful `close_position` (an explicit `db.save_trailing_state` call in the
close path) instead of waiting for the end of the pair iteration, shrinking the
window to the AddOrder→save gap only.

**Deferred:** full idempotency via Kraken's client order id (`cl_ord_id`) once
its dedup semantics are verified against the current krakenex version; noted in
the backlog card, not implemented now.

---

## Findings — B: medium defects

### B1. `get_last_prices` crashes the whole session on a missing pair

`exchange/kraken.py:114-124`: `result[info["primary"]]["c"][0]` raises
`KeyError` if Kraken's Ticker response omits a pair (or metadata is stale). The
exception escapes `_safe_call` (it happens after it) and fails the session.
**Fix:** build the dict defensively — skip and log pairs missing from the
response; the scheduler already handles a per-pair missing price
(`last_prices.get(pair)` → skip pair). Return `None` only when *no* pair
resolved.

### B2. One pair's failure aborts all remaining pairs (+ D10)

`core/scheduler.py:102-159`: any exception inside the per-pair body (e.g. a DB
write) propagates and ends the session; later pairs are never processed.
**Fix:** wrap the per-pair body in `try/except Exception` — log with
`logging.exception`, collect the pair name, continue. After the loop, if any
pair failed: `status = "failed"`, `failure_reason = "pair errors: <pairs>"` (so
the D10 issue — a session where every pair failed still reporting `completed` —
is fixed by the same change, and the failure-streak alerting keeps working).

### B3. Blocking calls on the FastAPI event loop

- `api/routes/optimizer.py::submit` is `async def` but calls
  `JOB_STORE.try_start` directly: a DB INSERT + a synchronous Telegram HTTP call
  (2 s timeout) + a process-pool submit, all on the event loop.
- `JobStore.supervise/_finalize` (`trading/optimizer/jobs.py:77-99`) runs DB
  updates and the synchronous Telegram notify on the event loop.

**Fix:** `await asyncio.to_thread(...)` around `try_start` in the route and
around `_finalize` in `supervise`. Keep `JobStore`'s lock (it now only contends
between worker threads).

### B4. `supervise` task not retained (GC risk)

`api/routes/optimizer.py`: `asyncio.create_task(...)` with `# noqa: RUF006` —
the task may be garbage-collected mid-flight, leaving the job row `running`
until the next restart. **Fix:** module-level `set[asyncio.Task]`, `add` +
`add_done_callback(set.discard)`.

### B5. Orphaned `running` row when the executor submit fails

`trading/optimizer/jobs.py::try_start`: the job row is inserted before
`_EXECUTOR.submit`; if the submit raises (broken pool), the row stays `running`
until restart cleanup. **Fix:** wrap the submit in `try/except`, call
`db.fail_optimizer_job(job_id, ...)` and re-raise.

### B6. `PAIRS` env parsing doesn't strip whitespace

`core/config.py:53`: `PAIRS=XBTEUR, ETHEUR` creates the key `" ETHEUR"`, which
`build_pairs_map` silently deletes → the bot would trade only XBTEUR with no
clear error (per-pair validation catches it only incidentally).
**Fix:** `{p: {} for p in (s.strip() for s in os.getenv("PAIRS", "").split(",")) if p}`.

### B7. AUTO optimizer runs are not reproducible

`trading/optimizer/search.py::run_auto_optimize`: `random.sample(range(1, 9999),
n_seeds)` uses the unseeded global RNG; `req.seed` is ignored in AUTO mode.
**Fix:** `random.Random(req.seed).sample(...)` — the stored request then fully
determines the run (`seeds_used` already records the outcome).

---

## Findings — C: security

Overall posture is good: constant-time token comparison, startup refusal
without a token unless `ALLOW_NO_AUTH`, all host ports bound to `127.0.0.1`,
non-root container + `no-new-privileges`, `.env` excluded from the image, ORM
throughout (no raw SQL with user data), read-only Grafana role.

### C1. Secrets over-shared across services

`docker-compose.yml` passes the full `.env` to both `botc` and `telegram`: the
Telegram container receives `KRAKEN_API_KEY/SECRET` and `POSTGRES_PASSWORD` it
never uses. A compromise of the (internet-polling) Telegram bot would expose the
exchange keys. **Fix:** drop `env_file` from the `telegram` service and pass an
explicit `environment:` allowlist (`TELEGRAM_*`, `API_SECRET_TOKEN`,
`ALLOW_NO_AUTH`, `API_BASE_URL`, `PAIRS`). Because the shared image entrypoint
runs `alembic upgrade head` (which needs DB credentials), the telegram service
also overrides `entrypoint: []` — migrations belong to `botc` only, which also
removes a redundant concurrent migration run at stack start.

### C2. DDL injection surface in the Grafana-role migration

`scripts/migrations/versions/20260512_01_phase8_observability.py:52-64`: the
escaped password is interpolated inside a `DO $$...$$` block; a password
containing `$$` breaks out of the dollar-quoting and executes arbitrary SQL as
the migration user. Operator-controlled input, so low severity — but cheap to
close. **Fix:** drop the `DO` block; check `pg_roles` client-side and build the
`CREATE/ALTER ROLE` statement with server-side quoting
(`SELECT format('ALTER ROLE grafana_reader WITH LOGIN PASSWORD %L', :pw)` via a
bound parameter, then execute the returned statement). No schema change — safe
to edit in place; already-migrated databases are unaffected.

### C3. `/docs`, `/openapi.json`, `/health` are unauthenticated

Acceptable while bound to localhost, but the full API schema becomes public the
day the port is proxied. **Suggestion (not scheduled):** `docs_url=None` /
`openapi_url=None` in production, or put them behind `verify_token`.

### C4. No dependency vulnerability auditing

Versions are pinned (good) but nothing detects newly published CVEs.
**Suggestion:** add `pip-audit` to CI (non-blocking at first), optionally
Dependabot/Renovate for bumps.

### C5. Telegram service doesn't validate its own config

`services/telegram/app.py` calls `int(TELEGRAM_USER_ID)` in the lifespan with no
prior validation (startup validation lives only in `botc`). **Fix:** a minimal
startup check in the telegram lifespan — when `TELEGRAM_ENABLED`, require
`TELEGRAM_TOKEN` and a positive-integer `TELEGRAM_USER_ID`, failing with a clear
`RuntimeError` (mirrors `validate_common_params`).

---

## Findings — D: refactors, improvements, doc drift

| # | Finding | Location | Remedy |
|---|---------|----------|--------|
| D1 | `simulate_operations` duplicates ~90 lines between sell/buy branches and iterates with `iterrows()` — the optimizer's hot loop (thousands of trials × full history) | `trading/engine.py:246-329` | Extract the common stop-hit/record/reset body parameterised by side; switch to `itertuples()`. Expected 5-10× per trial. **Must** ship with a before/after regression backtest proving identical operations. |
| D2 | `core/database.py` is 938 lines (own TODO acknowledges it) | `core/database.py:94` | Split into `core/db/{models,ohlc,positions,control,jobs}.py` with re-exports from `core.database` so no call site changes. |
| D3 | `POST /backtest` runs a CPU-bound simulation on the request path; competes with the scheduler thread for the GIL | `api/routes/backtest.py` | Document the limit; consider reusing the optimizer job pattern if it becomes a problem. Not scheduled. |
| D4 | Telegram `_pnl_percent` ignores its `last_price` parameter and shows PnL-at-stop labeled as "PnL" | `services/telegram/polling.py:47-55` | Drop the unused parameter; label the value "PnL @stop". |
| D5 | `/notify` returns `accepted: true` even when the Telegram send failed | `services/telegram/app.py:70-77` | Return `{"accepted": false, "reason": ...}` in the except branch. |
| D6 | DB errors double-logged (`get_session` logs + every DAL function logs) | `core/database.py:395-407` | Remove the log in `get_session` (rollback + raise stay). |
| D7 | CLAUDE.md drift: says `BotControl` has "no production callers" (it backs `bot_paused`, `latest_balance`, `latest_pair_data`, `ohlc_last_*`); says `SessionRecord` stores balance/pair_data (moved to `bot_control` in the phase-9 migration); `_SessionLogCollector` docstring says "root logger" but it attaches to the `botc` logger (so `core.database`'s stdlib logger is *not* captured in session logs) | `CLAUDE.md`, `core/scheduler.py:26` | Correct the three statements; decide/document whether DB-module logs should be captured. |
| D8 | Kraken rate limit only covers public endpoints; `Balance`/`QueryOrders`/`AddOrder`/`CancelOrder` are unthrottled | `exchange/kraken.py:44-48` | Fine at current call rates; either document the asymmetry or route private calls through the same limiter for consistency with the stated design choice. |
| D9 | `start`/`end` on backtest/optimizer requests are free-form strings; an unparsable value surfaces as a 500 from pandas instead of a 422 | `api/schemas.py` | `field_validator` with `datetime.fromisoformat`. Note: the job-status echo path re-validates stored requests — historical jobs with non-ISO dates (unlikely) would fail to render; accepted. |
| D10 | Session with every pair failed still reports `completed` | `core/scheduler.py:109-111` | Fixed by B2 (failed-pairs tracking). |

---

## Testing

Every A/B fix lands with a regression test that fails on the current code:

- **A1:** `detect_pivots` on a flat frame returns (would previously hang — the
  test itself is the timeout guard in CI).
- **A2:** `is_closing_complete` for `closed`, `canceled` (no fill), `canceled`
  (partial fill), `expired`, API error; asserts the canceled path clears the
  closing fields and the position is open again.
- **A3:** `record_position_closed` inserts + deletes in one transaction; calling
  it twice with the same `closing_order_id` is a no-op the second time
  (idempotency); integration-level test under `RUN_DB_INTEGRATION`.
- **A4:** `reprice_closing_order` — repricing on price move, skip on equal
  price, skip on partial fill, cancel-failure race is a no-op.
- **A5:** state is persisted immediately after a successful close (monkeypatched
  `save_trailing_state` called before the scheduler's end-of-iteration save).
- **B1:** Ticker response missing one pair → other pairs still priced.
- **B2:** first pair raises → second pair still processed; session `failed` with
  a pair-errors reason.
- **B3–B5:** route submits off the loop (`to_thread` seam), task set retention,
  failed submit marks the job row `failed`.
- **B6/B7:** config parsing and seeded AUTO determinism (same `req.seed` → same
  `seeds_used`).
- **C5:** telegram lifespan refuses to start with a missing/invalid user id.

Suite gate unchanged: `PYTHONPATH=. pytest tests/unit/` ≥ 80% coverage, plus
`ruff check` / `ruff format --check`.

## Out of scope

- Any change to entry/exit strategy (activation, trailing distance, allocation).
- A global stop-loss (explicit invariant — strategy decision, not a safety fix).
- Kraken `cl_ord_id` idempotent order placement (deferred until semantics are
  verified; A5 ships the state-persistence mitigation only).
- Parallelizing optimizer seeds; C3/C4/D3/D8 (recorded as suggestions).

## Documentation

- Update `CLAUDE.md` (D7 corrections; new closing-order lifecycle description —
  the "closing_price is written twice" invariant gains the reprice case).
- `docs/operations.md`: note the reprice behaviour and the canceled-order
  self-heal (operator-visible Telegram messages change slightly).
- `docs/BACKLOG.md`: add this work as a Planned card linking spec + plan.
