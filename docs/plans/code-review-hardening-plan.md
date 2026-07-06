# Code-review hardening implementation plan

> **For agentic workers:** Implement this plan task-by-task using TDD (write the failing test first, watch it fail, then implement). Steps use checkbox (`- [ ]`) syntax for tracking. Code blocks for the critical fixes (Phase 1) are complete and paste-ready; later phases give precise directives instead, to keep the plan scannable.

**Goal:** Fix the defects and close the hardening gaps found in the 2026-07-06 full code review — most urgently the three failure modes that leave the bot permanently inoperative without an alert (pivot-detection infinite loop, canceled-closing-order corruption, non-transactional close), plus the agreed reprice-to-market behaviour for closing orders that never fill.

**Spec:** [`../specs/code-review-hardening-design.md`](../specs/code-review-hardening-design.md)

**Branches:** one implementation branch per phase, cut from `main`:
`fix/critical-close-lifecycle` (Phase 1), `fix/hardening` (Phase 2),
`chore/review-cleanups` (Phase 3). Each phase is independently shippable; Phase 1
must land first.

**Tech stack:** Python 3.12, FastAPI, APScheduler, SQLAlchemy 2 + PostgreSQL (psycopg3), krakenex, Optuna, pytest.

## Commands (run from repo root; `PYTHONPATH=.` required)

- Single test: `PYTHONPATH=. pytest tests/unit/path/test_file.py::test_name -v --no-cov`
- Full unit suite: `PYTHONPATH=. pytest tests/unit/`
- Lint + format: `python -m ruff check . && python -m ruff format --check .`
- Integration (A3 test): `RUN_DB_INTEGRATION=true PYTHONPATH=. pytest tests/integration/`

---

# Phase 1 — Critical defects (A1, A2, A3, A4, A5, B1, B2/D10)

## Task 1 — A1: terminate the `detect_pivots` loop on equal-price pivots

- [ ] **Step 1.1: failing test in `tests/unit/trading/test_market_analyzer.py`**

```python
def test_detect_pivots_terminates_on_flat_data():
    """Different-type pivots with exactly equal prices previously made the
    false-pivot removal loop spin forever (no branch advanced `i`)."""
    n = 60
    df = pd.DataFrame(
        {
            "high": [100.0] * n,
            "low": [100.0] * n,
            "dtime": pd.date_range("2026-01-01", periods=n, freq="15min"),
        }
    )
    pivots = market_analyzer.detect_pivots(df, order=5)
    assert isinstance(pivots, list)
```

Run it with a pytest timeout guard if available; on current code it hangs — kill
it manually after confirming, or run under `timeout 30 pytest ...`.

- [ ] **Step 1.2: fix `trading/market_analyzer.py` (false-pivot removal)**

Replace the `elif` branch of the removal loop with:

```python
        elif curr_price == next_price or abs(curr_price - next_price) / curr_price < MINIMUM_CHANGE_PCT:
            # Equal prices (a zero-amplitude swing) or a change below the noise
            # threshold: drop the second pivot. The equality case previously fell
            # through both branches and looped forever on flat candles.
            del pivots[i + 1]
        else:
            i += 1
```

Run the test → green. Full `test_market_analyzer.py` → green.

**Commit:** `fix(market-analyzer): terminate pivot cleanup on equal-price pivots`

## Task 2 — A2: explicit order state; canceled/expired closes self-heal

- [ ] **Step 2.1: failing tests in `tests/unit/exchange/test_kraken.py`**

Cover `get_order_state` returning `(status, avg_price, vol_exec)` for: closed,
open (price ignored), canceled with `price="0.00000"`, missing order id, API
error → `None`.

- [ ] **Step 2.2: implement `get_order_state` in `exchange/kraken.py`**

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class OrderState:
    status: str
    avg_price: float | None
    vol_exec: float


def get_order_state(order_id: str) -> OrderState | None:
    """Status + average fill price + executed volume of an order, or None on
    API error / unknown order. Callers must branch on ``status`` explicitly —
    never infer completion from a bare price (a canceled order reports 0.0)."""
    result = _safe_call(
        "order state",
        lambda: api.query_private("QueryOrders", {"txid": order_id}, timeout=KRAKEN_HTTP_TIMEOUT),
    )
    if result is None:
        return None
    order = result.get(order_id)
    if not order or not order.get("status"):
        return None
    price = order.get("price")
    return OrderState(
        status=order["status"],
        avg_price=float(price) if price is not None else None,
        vol_exec=float(order.get("vol_exec") or 0.0),
    )
```

Delete `get_order_closing_price` (only caller is `is_closing_complete`, updated
next).

- [ ] **Step 2.3: failing tests in `tests/unit/trading/test_positions_manager.py`**

`is_closing_complete` with monkeypatched `get_order_state`:
closed+price → `True` with PnL; open/pending → `False`; API error → `False`;
canceled (vol_exec 0) → `False` **and** `closing_order_id`/`closing_price`/
`closing_requested_at` removed and `is_open(pos)` is `True`; closed with
`avg_price=0` → `False`, fields untouched, error logged.

- [ ] **Step 2.4: rewrite `is_closing_complete` in `trading/positions_manager.py`**

```python
ORDER_DEAD_STATUSES = ("canceled", "expired")


def is_closing_complete(pos: dict[str, Any] | None) -> bool:
    """Check if the closing order is filled. If so, update pos with the real fill
    price and PnL. A canceled/expired order clears the closing fields so the
    position is managed again next tick (a partial fill self-heals: the volume is
    recomputed from the real balance by refresh_position)."""
    if not pos:
        return False
    closing_order = pos.get("closing_order_id")
    if not closing_order:
        return False
    state = get_order_state(closing_order)
    if state is None or state.status in ("pending", "open"):
        return False
    if state.status in ORDER_DEAD_STATUSES:
        logging.warning(
            f"Closing order {closing_order} is {state.status} "
            f"(executed {state.vol_exec:.8f}); resuming position management.",
            to_telegram=True,
        )
        for key in ("closing_order_id", "closing_price", "closing_requested_at"):
            pos.pop(key, None)
        return False
    if state.status != "closed" or not state.avg_price or state.avg_price <= 0:
        logging.error(f"Closing order {closing_order} in unexpected state {state.status!r}; not finalizing.")
        return False
    closing_price = state.avg_price
    entry = pos["entry_price"]
    side = pos["side"]
    pnl = (closing_price - entry) / entry * 100 if side == "sell" else (entry - closing_price) / entry * 100
    pos["closing_price"] = closing_price
    pos["pnl_percent"] = round(pnl, 4)
    logging.info(f"💸 Position closed: {pnl:+.2f}% result", to_telegram=True)
    return True
```

(Import `get_order_state` instead of `get_order_closing_price`.) Note for the
scheduler: when the dead-status branch clears fields, the end-of-iteration state
diff persists the change — no scheduler edit needed for A2.

**Commit:** `fix(positions): handle canceled/expired closing orders explicitly`

## Task 3 — A3: transactional, idempotent close persistence

- [ ] **Step 3.1: failing unit test in `tests/unit/core/test_database.py`** —
  `record_position_closed` exists and issues insert + delete via one session
  (assert with a mocked session); plus an integration test in
  `tests/integration/` that calls it twice with the same `closing_order_id` and
  asserts one `closed_positions` row and no `trailing_state` row.

- [ ] **Step 3.2: implement in `core/database.py`**

```python
def record_position_closed(pair: str, position_data: dict[str, Any]) -> None:
    """Persist a completed close atomically: insert into closed_positions and
    delete the pair's trailing_state in ONE transaction. The insert is idempotent
    on closing_order_id so a crash-retry converges instead of violating the
    unique constraint (which previously wedged the session loop)."""
    values = {
        "pair": pair,
        "side": position_data["side"],
        "volume": _to_decimal_required(position_data["volume"]),
        "entry_price": _to_decimal_required(position_data["entry_price"]),
        "activation_atr": _to_decimal(position_data.get("activation_atr")),
        "activation_price": _to_decimal(position_data.get("activation_price")),
        "created_at": position_data["created_at"],
        "activated_at": position_data.get("activated_at"),
        "trailing_price": _to_decimal(position_data.get("trailing_price")),
        "stop_price": _to_decimal(position_data.get("stop_price")),
        "stop_atr": _to_decimal(position_data.get("stop_atr")),
        "closing_price": _to_decimal_required(position_data["closing_price"]),
        "closing_order_id": position_data["closing_order_id"],
        "closed_at": datetime.now(UTC),
        "pnl_percent": _to_decimal_required(position_data["pnl_percent"]),
    }
    with get_session() as session:
        session.execute(
            pg_insert(ClosedPosition).values(values).on_conflict_do_nothing(index_elements=["closing_order_id"])
        )
        session.query(TrailingState).filter(TrailingState.pair == pair).delete()
    logger.debug(f"Recorded closed position for {pair} order {position_data['closing_order_id']}")
```

Remove `save_closed_position` (scheduler was the only production caller; port
its tests to the new function).

- [ ] **Step 3.3: update `core/scheduler.py`** — replace the two calls (and
  delete the stale TODO):

```python
            if is_closing_complete(trailing_state.get(pair)):
                db.record_position_closed(pair, trailing_state[pair])
                del trailing_state[pair]
                logging.info(f"Trailing position removed for {pair}.")
```

**Commit:** `fix(db): make close persistence transactional and idempotent`

## Task 4 — A4: reprice unfilled closing orders to market

- [ ] **Step 4.1: failing tests in `tests/unit/exchange/test_kraken.py`** —
  `cancel_order` returns `True` on success, `False` on API error.

- [ ] **Step 4.2: implement `cancel_order` in `exchange/kraken.py`**

```python
def cancel_order(order_id: str) -> bool:
    """Cancel an open order. False on API error (including already-filled races —
    the caller treats a failed cancel as 'do nothing this tick')."""
    result = _safe_call(
        "cancel order",
        lambda: api.query_private("CancelOrder", {"txid": order_id}, timeout=KRAKEN_HTTP_TIMEOUT),
    )
    return result is not None
```

- [ ] **Step 4.3: failing tests in `tests/unit/trading/test_positions_manager.py`**
  for `reprice_closing_order` (monkeypatch `get_order_state`, `cancel_order`,
  `place_limit_order`): reprices on price move (new `closing_order_id`, new
  `closing_price` estimate, new `closing_requested_at`); skips when the order is
  partially filled; skips when the formatted price is unchanged; no-op when the
  cancel fails; no-op on API error; no-op when the new placement fails after a
  successful cancel (position keeps the old — now canceled — order id, and the
  A2 dead-status branch recovers it next tick).

- [ ] **Step 4.4: implement `reprice_closing_order` in `trading/positions_manager.py`**

```python
def reprice_closing_order(pair: str, pos: dict[str, Any], last_prices: dict[str, float]) -> None:
    """Chase the fill of a still-open closing order: cancel it and re-place the
    limit at the current market price. The exit decision was already made by the
    trailing stop — this only updates the execution price (operator decision,
    2026-07-06). Partial fills are left alone: the order is executing."""
    order_id = pos.get("closing_order_id")
    if not order_id:
        return
    state = get_order_state(order_id)
    if state is None or state.status not in ("open", "pending"):
        return  # error or terminal state: is_closing_complete handles it next tick
    if state.vol_exec > 0:
        return  # executing at its price; don't fragment the fill
    current_price = last_prices[pair]
    if round_price(pair, current_price) == round_price(pair, pos.get("closing_price")):
        return  # identical limit; re-placing would only lose queue priority
    if not cancel_order(order_id):
        return  # likely filled in the race window; next tick resolves it
    side = pos["side"]
    volume = float(pos.get("volume", 0.0))
    new_order = place_limit_order(pair, side, current_price, volume)
    if not new_order:
        logging.error("Failed to re-place closing order after cancel.", to_telegram=True)
        return
    pos.update(
        {
            "closing_price": current_price,
            "closing_order_id": new_order,
            "closing_requested_at": now_utc(),
        }
    )
    logging.info(
        f"[{pair}] 🔁 Repriced closing {side.upper()} order to {round_price(pair, current_price):,}€",
        to_telegram=True,
    )
```

- [ ] **Step 4.5: wire into `core/scheduler.py`** — after the
  `is_closing_complete` block, before the create/tick logic:

```python
            elif trailing_state.get(pair, {}).get("closing_order_id"):
                reprice_closing_order(pair, trailing_state[pair], last_prices)
```

(`elif` on the `is_closing_complete` check; the state diff at the end of the
iteration persists any reprice.) Add scheduler tests: closing-order position →
reprice called, tick not called; completed close → reprice not called.

- [ ] **Step 4.6: docs** — update the `closing_price` invariant in `CLAUDE.md`
  (the estimate may now be rewritten by each reprice before the final fill) and
  add the behaviour to `docs/operations.md`.

**Commit:** `feat(positions): reprice unfilled closing orders to market`

## Task 5 — A5: persist state immediately after placing a closing order

- [ ] **Step 5.1: failing test** — after a successful `close_position`, the
  trailing state was saved before the function returned (monkeypatch
  `db.save_trailing_state`).
- [ ] **Step 5.2:** in `close_position` (and at the end of a successful
  `reprice_closing_order`), call `db.save_trailing_state(pair, pos)` right after
  `pos.update(...)`, wrapped in the existing `try/except` (a failed save is a
  recoverable missed persist; the end-of-iteration save retries). `positions_manager`
  gains an `import core.database as db`.

**Commit:** `fix(positions): persist closing-order state immediately after placement`

## Task 6 — B1 + B2/D10: per-pair resilience

- [ ] **Step 6.1 (B1):** rewrite the result loop of `get_last_prices` to skip
  pairs missing from the Ticker response (log each), returning `None` only when
  nothing resolved. Failing test: response missing one pair → other pair priced.
- [ ] **Step 6.2 (B2/D10):** wrap the per-pair body of `trading_session` in
  `try/except Exception` — `logging.exception(f"Error processing {pair}...")`,
  append to `failed_pairs`, continue. After the loop: if `failed_pairs`, set
  `status = "failed"` and `failure_reason = f"pair errors: {', '.join(failed_pairs)}"`;
  else `status = "completed"`. Failing tests: first pair raises → second pair
  processed; session status/reason reflect the pair failure.

**Commit:** `fix(scheduler): isolate per-pair failures; harden ticker parsing`

### Phase 1 acceptance checklist

- [ ] `PYTHONPATH=. pytest tests/unit/` — passes, coverage ≥ 80%.
- [ ] `RUN_DB_INTEGRATION=true PYTHONPATH=. pytest tests/integration/` — passes (A3 idempotency).
- [ ] `python -m ruff check . && python -m ruff format --check .` — exit 0.
- [ ] `grep -rn "get_order_closing_price" --include="*.py" .` — no matches (fully replaced by `get_order_state`).
- [ ] `grep -n "save_closed_position" core/ trading/ api/` — no production callers remain.
- [ ] Manual smoke: `docker compose up -d --build`, one full session in logs, `/health` OK.

---

# Phase 2 — Hardening (B3, B4, B5, B6, B7, C1, C2, C5)

## Task 7 — B3/B4/B5: optimizer job store off the event loop

- [ ] `api/routes/optimizer.py::submit`: `job_id = await asyncio.to_thread(JOB_STORE.try_start, DTORequest(**req.model_dump()))`.
- [ ] Retain supervise tasks: module-level `_supervise_tasks: set[asyncio.Task]`; `task = asyncio.create_task(...)`, `_supervise_tasks.add(task)`, `task.add_done_callback(_supervise_tasks.discard)`; drop the `# noqa: RUF006`.
- [ ] `JobStore.supervise`: run `self._finalize(...)` via `await asyncio.to_thread(...)` (both branches).
- [ ] `JobStore.try_start`: wrap `_EXECUTOR.submit` in `try/except` → `db.fail_optimizer_job(job_id, f"failed to submit: {exc}")`, re-raise.
- [ ] Tests: submit-failure marks the row failed; route awaits `to_thread` (seam-mock); task set drains after completion.

**Commit:** `fix(optimizer): keep blocking job-store work off the event loop`

## Task 8 — B6/B7: config parsing + reproducible AUTO

- [ ] `core/config.py`: `PAIRS = {p: {} for p in (s.strip() for s in os.getenv("PAIRS", "").split(",")) if p}` + test (`"XBTEUR, ETHEUR"` → both keys clean).
- [ ] `trading/optimizer/search.py::run_auto_optimize`: `seeds = random.Random(req.seed).sample(range(1, 9999), auto.n_seeds)` + determinism test (same seed → same `seeds_used`).

**Commit:** `fix(config,optimizer): strip PAIRS entries; seed AUTO seed selection`

## Task 9 — C1: least-privilege env for the telegram service

- [ ] `docker-compose.yml` `telegram` service: remove `env_file`; add `entrypoint: []` (migrations run only in `botc`); add explicit `environment:` — `API_BASE_URL`, `TELEGRAM_ENABLED`, `TELEGRAM_TOKEN`, `TELEGRAM_USER_ID`, `TELEGRAM_POLL_INTERVAL`, `API_SECRET_TOKEN`, `ALLOW_NO_AUTH`, `PAIRS` (all `${VAR}` interpolations from the root `.env`).
- [ ] Verify: `docker compose up -d --build` → telegram healthy, `/status` command works, `docker compose exec telegram env | grep -c KRAKEN` → 0.
- [ ] Document in `docs/operations.md` (the telegram container no longer receives Kraken/DB credentials and no longer runs migrations).

**Commit:** `sec(compose): stop passing Kraken/DB secrets to the telegram service`

## Task 10 — C2: remove the DO-block interpolation in the Grafana-role migration

- [ ] `scripts/migrations/versions/20260512_01_phase8_observability.py`: check `pg_roles` client-side (`SELECT 1 FROM pg_roles WHERE rolname = 'grafana_reader'`); build the statement server-side with `SELECT format('CREATE ROLE grafana_reader LOGIN PASSWORD %L', :pw)` (or `ALTER ROLE ... WITH LOGIN PASSWORD %L`) using a bound parameter, then execute the returned string. Remove `_escape_literal` if now unused. No schema change; already-applied databases unaffected.
- [ ] Verify on a fresh DB: `docker compose -f docker-compose.test.yml run --rm test alembic upgrade head` with a password containing `'` and `$$`.

**Commit:** `sec(migrations): parameterize grafana_reader password quoting`

## Task 11 — C5: telegram service validates its own config

- [ ] Failing test in `tests/unit/services/test_telegram.py`: lifespan raises `RuntimeError` when `TELEGRAM_ENABLED` and token/user-id missing or non-numeric.
- [ ] Implement a small `_validate_telegram_config()` called at the top of the lifespan (mirror the rules of `validate_common_params`).

**Commit:** `fix(telegram): validate own config at startup`

### Phase 2 acceptance checklist

- [ ] Unit suite + ruff green; compose smoke test per Task 9.
- [ ] `docker compose exec telegram env | grep -E "KRAKEN|POSTGRES_PASSWORD"` — empty.

---

# Phase 3 — Cleanups and refactors (D1, D2, D4, D5, D6, D7, D9)

Ordered by value; each is an independent commit. C3/C4/D3/D8 remain recorded
suggestions (see spec) and are intentionally not scheduled.

- [ ] **Task 12 (D1):** `trading/engine.py` — extract the duplicated sell/buy
  stop-hit body into one helper parameterised by side; switch `iterrows()` →
  `itertuples()`. **Gate:** a regression test that runs `simulate_operations` on
  a recorded OHLC fixture before/after and asserts the identical operation list
  (already-existing engine tests plus one golden-file comparison).
  Commit: `perf(engine): deduplicate side branches and drop iterrows`
- [ ] **Task 13 (D2):** split `core/database.py` into
  `core/db/{models,ohlc,positions,control,jobs}.py`, re-export everything from
  `core/database.py` so no call site changes. Pure move; suite must stay green
  untouched. Commit: `refactor(db): split database module by domain`
- [ ] **Task 14 (D4+D5):** telegram polish — `_pnl_percent` drops the unused
  parameter and the message labels the value `PnL @stop`; `/notify` returns
  `{"accepted": false}` when the send fails. Update `test_telegram.py`.
  Commit: `fix(telegram): honest notify result; label stop-based PnL`
- [ ] **Task 15 (D6):** remove the duplicate `logger.error` in
  `core/database.py::get_session` (DAL functions and the API handler already
  log). Commit: `chore(db): drop duplicate session-error logging`
- [ ] **Task 16 (D7):** CLAUDE.md corrections — `BotControl` *does* have
  production callers (`bot_paused`, `latest_balance`, `latest_pair_data`,
  `ohlc_last_*`); session balance/pair_data live in `bot_control` since the
  phase-9 migration; `_SessionLogCollector` attaches to the `botc` logger (not
  the root logger) so `core.database` stdlib logs are not captured — document
  this as intended. Commit: `docs: fix CLAUDE.md drift on BotControl and session logging`
- [ ] **Task 17 (D9):** `api/schemas.py` — `field_validator("start", "end")` on
  `BacktestRequest` and `OptimizerRequest` requiring `datetime.fromisoformat`;
  tests for 422 on garbage input. Note in the commit body that historical job
  echoes with non-ISO dates (unlikely) would fail to render. Commit:
  `fix(api): validate start/end as ISO datetimes`

### Phase 3 acceptance checklist

- [ ] Unit suite + ruff green after every task (each is an independent commit).
- [ ] Task 12 golden-file regression proves identical simulation output.

---

## Execution order (commits)

Phase 1: Tasks 1 → 2 → 3 → 4 → 5 → 6 (Task 2 before 3/4 — both build on `get_order_state`).
Phase 2: Tasks 7 → 8 → 9 → 10 → 11.
Phase 3: Tasks 12 → 17 in any order (13 last is easiest to review).

## Non-goals

- No strategy changes: activation, trailing distance, allocation and the
  "trailing stop is the only exit" invariant are untouched. A4 changes execution
  of an already-decided exit only.
- No global stop-loss.
- No `cl_ord_id`-based idempotent order placement yet (backlog; A5 ships the
  narrower state-persistence mitigation).
- No optimizer seed parallelization, no auth on `/docs`, no CI `pip-audit`
  (recorded as suggestions in the spec).
