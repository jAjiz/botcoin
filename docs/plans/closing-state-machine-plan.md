# Closing state machine — implementation plan

> **For agentic workers:** implement task by task using TDD — write the failing
> test first, run it and watch it fail for the right reason, then implement.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** give every order the bot places a client-chosen `cl_ord_id` so a lost
`AddOrder` response is recoverable instead of silently becoming a second exit,
and collapse the closing path into one selector with a single `OrderStatus`
dispatch.

**Spec:** [`../specs/closing-state-machine-design.md`](../specs/closing-state-machine-design.md)

**Shape:** one PR, ten tasks. An earlier version staged this in two with a live
check between them, because the resolver's "Kraken doesn't have it ⇒ nothing
landed" inference is only sound if the endpoints really filter by `cl_ord_id`.
That has been verified directly on the account (operator, 2026-08-17), so there
is nothing left to gate on.

Tasks 1–5 are additive and 6–9 carry the behaviour change. Keep that order: the
resolver in task 9 reads an id that tasks 4 and 5 must already be minting.

**Base:** `main` at or after `4fc9712` (the `stop_at` latch). The spec lives on
`docs/closing-state-machine`; cut `feat/closing-state-machine` from `main`.

**Tech stack:** Python 3.12, SQLAlchemy 2 + Alembic, krakenex, pytest.

**Disposable:** this plan is scaffolding for one implementation pass. Delete it
when the PR merges — the spec is the durable record.

## Commands (from repo root; `PYTHONPATH=.` required)

- Single test: `PYTHONPATH=. pytest tests/unit/path/test_file.py::test_name -v --no-cov`
- Full unit suite: `PYTHONPATH=. pytest tests/unit/`
- Lint + format: `python -m ruff check . && python -m ruff format --check .`
- Migration against a fresh DB: `docker compose -f docker-compose.test.yml run --rm test alembic upgrade head`

Note: the system `pytest` is not on PATH in this environment; use the venv
(`venv/Scripts/python.exe -m pytest ...` on Windows).

---

# Tasks 1–5 — Placement

Every change here is additive: the bot sends and stores an id and logs it, and
the routing in `manage_close_position` is untouched. Land them first so the
resolver has an id to find.

## Task 1 — `new_cl_ord_id()`

- [x] **Test** (`tests/unit/core/test_utils.py`): returns 32 lowercase hex
  characters; two calls differ.
- [x] **Implement** in `core/utils.py`:

```python
def new_cl_ord_id() -> str:
    """A client order id for one placement attempt (Kraken's 'short UUID' form)."""
    return uuid.uuid4().hex
```

Kraken's free-text form caps at 18 ASCII chars — too small for pair + timestamp +
entropy — so the id is opaque and correlation comes from the log line and the DB
row.

**Commit:** `feat(utils): add new_cl_ord_id for order placement`

## Task 2 — `place_limit_order` sends the id

- [x] **Tests** (`tests/unit/exchange/test_kraken.py`, capturing the payload the
  way `test_place_limit_order_rounds_to_pair_precision` already does): the
  `AddOrder` payload contains `cl_ord_id` when one is passed, and the key is
  **absent entirely** (not `None`) when it is not.
- [x] **Implement** in `exchange/kraken.py`: add
  `cl_ord_id: str | None = None` to the signature and merge
  `{"cl_ord_id": cl_ord_id}` into the payload only when not `None`.

Optional rather than required so existing tests and any future non-idempotent
call site stay valid; both production call sites always pass one.

**Commit:** `feat(kraken): accept a client order id on limit orders`

## Task 3 — `closing_request_id` column

- [x] **Test** (`tests/unit/core/test_database.py`): `closing_request_id`
  round-trips through `save_trailing_state` / `load_trailing_state`, and is
  **absent from the dict** (not `None`) when the column is `NULL`, matching how
  the other optional fields behave.
- [x] **Implement**:
  - `core/db/models.py` — `TrailingState.closing_request_id = Column(Text, nullable=True)`,
    plus the key in `TrailingState.to_dict()`.
  - `core/db/positions.py` — `closing_request_id=position_data.get("closing_request_id")`
    in `_state_entry_to_trailing_record`, and the `if record.closing_request_id is not None`
    guard in `_trailing_record_to_state_entry`.
  - `scripts/migrations/versions/20260817_01_closing_request_id.py` —
    `revision = "20260817_01"`, `down_revision = "20260812_01"`, a single
    `op.add_column("trailing_state", sa.Column("closing_request_id", sa.Text(), nullable=True))`
    and the matching `drop_column` in `downgrade`.

No index and no check constraint. `trailing_state` already carries
`ix_trailing_state_closing_order_id`, but that one exists for the closed-position
dedup path; this column is only ever read on a row already fetched by its `pair`
primary key.

`closed_positions` is deliberately **not** extended — its audit key is the unique
`closing_order_id`, which exists for every recorded close by construction.

- [x] Run the migration against a fresh DB (command above) — CI builds the schema
  from migrations, and model/migration drift has bitten this repo before.

**Commit:** `feat(db): persist the closing request id on trailing state`

## Task 4 — `close_position` mints the id

- [x] **Test** (`tests/unit/trading/test_positions_manager.py`): assert from
  *inside* the fake `place_limit_order` that `pos["closing_request_id"]` is
  already set and equals the `cl_ord_id` argument it received — the ordering is
  the whole point, so a test that only checks the value afterwards does not cover
  it.
- [x] **Test**: a `None` return leaves `closing_request_id` set and
  `closing_order_id` absent.
- [x] **Implement** in `trading/positions_manager.py`:

```python
cl_ord_id = new_cl_ord_id()
current_price = last_prices[pair]
pos.update({"closing_request_id": cl_ord_id, "closing_price": current_price})
closing_order = place_limit_order(pair, side, current_price, volume, cl_ord_id=cl_ord_id)
if not closing_order:
    logging.error(f"[{pair}] Closing order not confirmed; it remains owed and will be resolved next tick.")
    return False
pos["closing_order_id"] = closing_order
```

Two things move: `closing_price` is now written before the call rather than on
success, and the error message says "not confirmed" instead of "failed to place",
because that is what a `None` now means. `closing_price` keeps its meaning
exactly — an estimate until the fill is confirmed.

This is the one place tasks 1–5 leave state behind that today they would not (a failed
placement now persists `closing_price` and `closing_request_id`). It is still
inert: nothing reads `closing_price` unless a `closing_order_id` is set, and
nothing reads `closing_request_id` at all until task 9.

**Commit:** `feat(positions): mint a client order id before placing the exit`

## Task 5 — `reprice_closing_order` mints the id for the replacement

- [ ] **Test**: on the placement path the replacement's `cl_ord_id` is a **new**
  id, different from the one `close_position` used, and it is set on `pos` before
  the call.
- [ ] **Implement**: mint immediately before `place_limit_order`, write it to
  `pos`, pass it through.

Do **not** drop `closing_order_id` here and do **not** move `pos["volume"]`
yet — both belong to task 9, where the resolver exists to cover the state they
create.

**Commit:** `feat(positions): mint a client order id for the replacement order`

---

# Tasks 6–9 — The state machine

All the behaviour change lands here. After task 5 the suite should still be green
with no *behavioural* test change — only additions. If one was needed, something
in tasks 1–5 was not additive; find it before going on.

## Task 6 — `find_order_by_cl_ord_id`

- [ ] **Tests** (`tests/unit/exchange/test_kraken.py`, monkeypatching
  `kraken.api.query_private` and recording which methods were called):
  - hit in `ClosedOrders` → txid + state returned, and **`OpenOrders` is never
    called**;
  - miss in `ClosedOrders`, hit in `OpenOrders`;
  - miss in both → `OrderLookup(txid=None, state=None)`;
  - `ClosedOrders` errors → `None` (never "absent");
  - `ClosedOrders` empty + `OpenOrders` errors → `None` — **the load-bearing
    one**: an error read as absence is the path to a double sell;
  - a returned order whose `cl_ord_id` does not match is **not** adopted.
- [ ] **Implement** in `exchange/kraken.py`: `OrderLookup`, the lookup, and
  extract the raw-order → `OrderState` construction out of `get_order_state` into
  a shared helper so both paths build it identically.

`ClosedOrders` first: the resolver runs on the tick after a lost response, on a
limit placed at the market price, which most often has already filled. Pure cost
choice — a conclusive "absent" needs both legs to succeed either way.

⚠️ If the two legs are built with a loop, bind the method name as a default
argument in the lambda (`lambda m=method: ...`). A closure over the loop variable
inside `_safe_call` would send the second method's name on both calls.

**Commit:** `feat(kraken): resolve an order by its client order id`

## Task 7 — the dispatch, `finalize_close`, and the reprice signature

One commit, deliberately. `finalize_close` cannot take an `OrderState` until
someone fetches it, `reprice_closing_order` cannot drop its guards until the
caller owns them, and `_drive_closing_order` cannot exist until both. Splitting
this produces intermediate states that double-query or fail the suite; the pieces
land together.

- [ ] **Tests — migrate, do not rewrite.** The existing coverage moves to its new
  owner:
  - `is_closing_complete_*` interpretation tests (filled, buy-side PnL,
    fully-executed cancel, no usable price, `vol` vs `pos["volume"]` fullness) →
    `finalize_close`, now passed an `OrderState` and asserting **no API call**.
  - `is_closing_complete_*` guard tests (no order, API error, in flight,
    unresolvable) → `_drive_closing_order`.
  - `reprice_closing_order_*` tests that stub the pre-cancel `get_order_state` →
    pass the state in instead; assert the pre-cancel query is **not** made.
- [ ] **New test**: `_drive_closing_order` returns `None` and clears the fields on
  a terminal-but-unusable order, and the same call re-places on that tick.
- [ ] **Implement** in `trading/positions_manager.py`, per spec §4–§7:
  `_clear_closing_fields`, `finalize_close(pos, state)`, `_drive_closing_order`,
  `reprice_closing_order(pair, pos, state, last_prices)`, and
  `manage_close_position` branch 1.

Carry the interpretation logic over **unchanged**. It is the most hard-won code
in the module: the fully-executed cancel, the fullness measured against the
order's own `vol`, and the rule that no branch leaves a terminal order's fields
in place except the unresolvable statuses.

**Commit:** `refactor(positions): dispatch a closing order's status in one place`

## Task 8 — size the remainder from the order

- [ ] **Test**: the dust case — `post_cancel.vol_exec == post_cancel.vol` with a
  **larger** `pos["volume"]` — places nothing and returns `True`, so the next tick
  finalizes it instead of trying to sell dust.
- [ ] **Test**: Kraken omitting `vol` (reads `0.0`) places nothing.
- [ ] **Implement**: `remaining = post_cancel.vol - post_cancel.vol_exec`.

`place_limit_order` rounds to `lot_decimals` before sending, so `pos["volume"]`
and the order's `vol` differ by up to one lot tick. The old subtraction turned an
order that executed *completely* into a ~1e-8 remainder, the replacement was
rejected below `ordermin`, and a finished trade was never recorded.

**Commit:** `fix(positions): size the reprice remainder from the order's own volume`

## Task 9 — branch 2: the unconfirmed path

- [ ] **Tests** — the three outcomes:
  - **adopted**: txid written to `closing_order_id`, driven on the **same** tick
    (an adopted-and-already-filled order returns `FILLED` immediately);
  - **absent**: closing fields cleared, `stop_at` **kept**, and a new order placed
    with a **new** id on the same tick;
  - **lookup error**: `UNMANAGED`, every field untouched, **no placement**.
- [ ] **Test**: routing — confirmed sub-state calls `get_order_state` and never
  the lookup; unconfirmed calls the lookup and never `get_order_state`.
- [ ] **Test**: `refresh_position` is never reached while a lookup is unresolved.
  It can *drop* the position, and dropping one that may have a live order at
  Kraken orphans that order.
- [ ] **Implement**: the `elif` branch of `manage_close_position` (spec §4), and
  in `reprice_closing_order` write `volume` / `closing_price` /
  `closing_request_id` before the call and **drop** `closing_order_id`.

Dropping the dead id is only correct here. Keeping it would leave the position in
the Confirmed sub-state while a `closing_request_id` for a possibly-live new order
sat unresolved, and branch 1 would never look for it. What it used to protect is
now protected better: a replacement that fails after a confirmed cancel is
recoverable rather than permanently unmanaged.

**Commit:** `feat(positions): resolve a placement whose response was lost`

## Task 10 — documentation

- [ ] `CLAUDE.md`: add the five design choices from the spec's last section, and
  update the `is_closing_complete` / `reprice_closing_order` / `manage_close_position`
  lifecycle text in *Position lifecycle*, plus the `TrailingState` note in
  *Database*.
- [ ] `docs/BACKLOG.md`: move **Closing State Machine & Idempotent Placement**
  from 📋 Planned to ✅ Shipped.
- [ ] `docs/CHANGELOG.md`: an entry under `[Unreleased]` — this one ships
  behaviour, unlike the doc-only PRs that skipped it.
- [ ] Delete this plan, and its `- Plan:` link from the backlog card.

**Commit:** `docs: record the closing state machine design choices`

---

## Definition of done

- [ ] Full unit suite green, ruff clean, coverage above 80%.
- [ ] `manage_close_position` is the only entry point, and `_drive_closing_order`
  the only place branching on `OrderStatus` for a closing order.
- [ ] `positions_manager` still never imports `core.database`.
- [ ] The scheduler is unchanged: three `ClosingState` values, all DB writes on
  its side.
- [ ] `is_open` is still `not stop_at` — no `closing_request_id` clause.
- [ ] A reprice tick makes two private Kraken calls, not three.
