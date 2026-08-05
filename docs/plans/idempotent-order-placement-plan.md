# Idempotent order placement — implementation plan

> **For agentic workers:** implement task-by-task using TDD (write the failing test first, watch it fail, then implement). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** give every order the bot places a client-chosen id (`cl_ord_id`) so a
lost `AddOrder` response can be resolved — "did *my* order land?" — instead of
silently becoming a second exit for the same position.

**Spec:** [`../specs/idempotent-order-placement-design.md`](../specs/idempotent-order-placement-design.md)

**Shape:** two ordered PRs with a live check between them.
`feat/cl-ord-id-placement` (PR 1) sends and persists the id and changes **no
behaviour**; `feat/cl-ord-id-recovery` (PR 2) adds the resolver and the behaviour
changes that depend on it. Tightening `is_open` before the resolver exists would
freeze a pending position with nothing able to clear it, so the order is not
negotiable.

**Depends on:** PR #64 (post-cancel remainder sizing) and PR #65 (the
`core/database.py` → `core/db/` split) being merged first. Task 3 below touches
`core/db/models.py` and `core/db/positions.py`, which only exist after #65.

**Tech stack:** Python 3.12, SQLAlchemy 2 + Alembic, krakenex, pytest.

## Commands (run from repo root; `PYTHONPATH=.` required)

- Single test: `PYTHONPATH=. pytest tests/unit/path/test_file.py::test_name -v --no-cov`
- Full unit suite: `PYTHONPATH=. pytest tests/unit/`
- Lint + format: `python -m ruff check . && python -m ruff format --check .`
- Migration against a fresh DB: `docker compose -f docker-compose.test.yml run --rm test alembic upgrade head`

---

# PR 1 — Placement (`feat/cl-ord-id-placement`)

Every change here is additive. After this PR the bot sends and stores an id and
logs it; nothing reads it yet.

## Task 1 — `new_cl_ord_id()`

- [ ] **Test** (`tests/unit/core/test_utils.py`): returns 32 lowercase hex
  characters; two calls differ.
- [ ] **Implement** in `core/utils.py`:

```python
def new_cl_ord_id() -> str:
    """A client order id for one placement attempt (Kraken's 'short UUID' form)."""
    return uuid.uuid4().hex
```

Kraken's free-text form caps at 18 ASCII chars, too small for pair + timestamp +
entropy, so the id is opaque and correlation comes from the log line and the DB
row.

**Commit:** `feat(utils): add new_cl_ord_id for order placement`

## Task 2 — `place_limit_order` sends the id

- [ ] **Tests** (`tests/unit/exchange/test_kraken.py`, capturing the payload the
  way `test_place_limit_order_rounds_to_pair_precision` already does): the
  `AddOrder` payload contains `cl_ord_id` when one is passed, and the key is
  **absent** (not `None`) when it is not.
- [ ] **Implement:** add `cl_ord_id: str | None = None` as the last parameter;
  build the payload dict, then `if cl_ord_id: data["cl_ord_id"] = cl_ord_id`.
  Include the id in the existing success log line.

Optional rather than required so existing call sites and tests stay valid; both
production call sites always pass one.

**Commit:** `feat(kraken): send cl_ord_id with AddOrder`

## Task 3 — persist `closing_cl_ord_id`

- [ ] **Tests** (`tests/unit/core/test_database.py`): the field round-trips
  through `save_trailing_state` / `load_trailing_state`, and is absent from the
  loaded dict when the column is `NULL`.
- [ ] **Model** — `core/db/models.py`: `closing_cl_ord_id = Column(Text, nullable=True)`
  on `TrailingState`, plus the key in `to_dict`.
- [ ] **DAL** — `core/db/positions.py`: the field in
  `_state_entry_to_trailing_record` and, only when not `None`, in
  `_trailing_record_to_state_entry` (matching the other optional closing fields).
- [ ] **Migration** — `scripts/migrations/versions/20260805_01_closing_cl_ord_id.py`,
  `down_revision = "20260616_01"`: one `op.add_column("trailing_state",
  sa.Column("closing_cl_ord_id", sa.Text(), nullable=True))` and the matching
  `drop_column`. No index (rows are read by the `pair` primary key), no
  constraint. Per CLAUDE.md the model and the migration change together — CI
  builds the schema from migrations.
- [ ] Verify on a fresh DB with the compose command above.

`closed_positions` is **not** extended: its audit key is the unique
`closing_order_id`, which exists for every recorded close by definition.

**Commit:** `feat(db): persist closing_cl_ord_id on trailing_state`

## Task 4 — both placements carry an id

- [ ] **Tests** (`tests/unit/trading/test_positions_manager.py`):
  - `close_position` sets `pos["closing_cl_ord_id"]` **before** calling
    `place_limit_order` — assert from inside the fake that the dict already
    holds the same value passed as `cl_ord_id`.
  - A `None` return leaves `closing_cl_ord_id` set and `closing_order_id` absent.
  - `reprice_closing_order` passes a **new** id, different from the one the
    position already carries.
  - `is_closing_complete` clears `closing_cl_ord_id` on every path that clears
    the other closing fields.
- [ ] **Implement** in `trading/positions_manager.py`:
  - `close_position`: generate the id and write `closing_cl_ord_id`,
    `closing_price` and `closing_requested_at` into `pos` *before*
    `place_limit_order`; on success add `closing_order_id` as today. Moving
    `closing_requested_at` earlier changes nothing observable — it already means
    "when we asked to close".
  - `reprice_closing_order`: generate a fresh id per attempt and pass it. Leave
    the rest of the function exactly as PR #64 left it.
  - `is_closing_complete`: add `closing_cl_ord_id` to the tuple of keys cleared
    on a terminal outcome.

**Behaviour must be unchanged by this task.** `is_open` is untouched, so a
pending id does not block management: exactly as today, a failed placement is
retried next tick (with a new id). The stored id is diagnostics only until PR 2.

**Commit:** `feat(positions): tag every closing order with a cl_ord_id`

### PR 1 acceptance checklist

- [ ] `PYTHONPATH=. pytest tests/unit/` — passes, coverage ≥ 80%.
- [ ] `python -m ruff check . && python -m ruff format --check .` — exit 0.
- [ ] `alembic upgrade head` on a fresh DB — succeeds.
- [ ] Diff review confirms no behavioural change: `is_open` untouched, no early
      return added or removed, no call site reads `closing_cl_ord_id`.

### Live gate — do not start PR 2 until this passes

- [ ] Deploy PR 1 and let the bot place one real closing order.
- [ ] Confirm the order carries the `cl_ord_id` (Kraken UI or a one-off
      `OpenOrders` call) and that `OpenOrders` **filtered by that id** returns it.
- [ ] Confirm the same for `ClosedOrders` once the order is no longer open.

If either filter does not behave as documented, stop: the resolver in PR 2 rests
entirely on those two lookups.

---

# PR 2 — Recovery (`feat/cl-ord-id-recovery`)

## Task 5 — `find_order_by_cl_ord_id`

- [ ] **Tests** (`tests/unit/exchange/test_kraken.py`): hit in `OpenOrders`;
  miss in `OpenOrders` then hit in `ClosedOrders`; miss in both →
  `OrderLookup(txid=None)`; `OpenOrders` errors → `None`; `OpenOrders` empty and
  `ClosedOrders` errors → `None`. The last two are the load-bearing ones: an
  error must never read as "absent".
- [ ] **Implement** in `exchange/kraken.py`:

```python
@dataclass(frozen=True)
class OrderLookup:
    txid: str | None      # None when the order provably does not exist
    status: str | None


def find_order_by_cl_ord_id(cl_ord_id: str) -> OrderLookup | None:
    """Resolve a client order id to Kraken's txid.

    None means the lookup failed — the caller must treat that as 'unknown',
    never as 'absent'. OrderLookup(txid=None) is returned only when BOTH
    OpenOrders and ClosedOrders answered and neither held the id."""
```

`QueryOrders` cannot serve this: it requires `txid`, which is exactly what was
lost. **Pass no `start`/`end`** to `ClosedOrders` — `start` is exclusive and
would exclude the order when the second boundary coincides, producing a false
"absent" (see the spec).

**Commit:** `feat(kraken): look up orders by client order id`

## Task 6 — `resolve_unconfirmed_closing_order`

- [ ] **Tests** (`tests/unit/trading/test_positions_manager.py`): no-op and **no
  API call** when nothing is pending; txid adopted on a hit; the closing fields
  cleared on a proven miss; `False` returned and state untouched on a lookup
  error.
- [ ] **Implement** in `trading/positions_manager.py`:

```python
def resolve_unconfirmed_closing_order(pair: str, pos: dict[str, Any] | None) -> bool:
    """Resolve a placement whose response was lost. True when the position is
    safe to manage this tick; False when the lookup failed and the position must
    be left untouched."""
```

- Nothing pending (no `pos`, no `closing_cl_ord_id`, or `closing_order_id`
  already set) → `True`, no API call.
- Hit → write `pos["closing_order_id"] = txid`, log to Telegram (a recovered
  order is operator-relevant), return `True`. Deliberately **do not** inspect the
  status: the same tick then runs `is_closing_complete`, whose existing branches
  already finalize a fill or clear any other terminal outcome. No new
  terminal-state logic, no wasted tick.
- Proven miss → clear `closing_cl_ord_id`, `closing_order_id`, `closing_price`,
  `closing_requested_at`, warn to Telegram, return `True`. The position is open
  again and may legitimately re-close on the same tick — nothing was placed.
- Lookup failed → return `False`, touching nothing.

**Commit:** `feat(positions): resolve unconfirmed closing orders`

## Task 7 — `is_open` accounts for the pending state

- [ ] **Test:** `is_open` is `False` while only `closing_cl_ord_id` is set.
- [ ] **Implement:**

```python
def is_open(pos: dict[str, Any] | None) -> bool:
    return bool(pos) and not pos.get("closing_order_id") and not pos.get("closing_cl_ord_id")
```

This is the single choke point that stops `tick_position` placing the second
exit the whole design exists to prevent.

**Commit:** `fix(positions): a pending client order id means the position is not open`

## Task 8 — `reprice_closing_order` writes its state before placing

- [ ] **Tests:** on the placement path the old `closing_order_id` is dropped, the
  new id is set and `volume == remaining` — all before `place_limit_order` is
  called; on success the new txid is written; on a lost response the pending
  state remains. PR #64's early returns (post-cancel re-query failed,
  `remaining <= 0`) still keep the old `closing_order_id` — keep those as
  regression tests.
- [ ] **Implement:** after PR #64's post-cancel re-query and the `remaining > 0`
  check, write `volume = remaining`, `closing_price`, the new
  `closing_cl_ord_id`, and `pos.pop("closing_order_id", None)` *before* placing.

Two consequences to keep in mind while reviewing: the volume must be the
remainder *before* the call (if the response is lost but the order landed, the
persisted size has to be right), and dropping the confirmed-canceled txid is what
keeps the state unambiguous — otherwise `is_closing_complete` would resolve the
*old* order and wipe the pending id with it.

**Commit:** `fix(positions): make a lost reprice response recoverable`

## Task 9 — scheduler wiring

- [ ] **Tests** (`tests/unit/core/test_scheduler.py`): a pending pair whose
  lookup fails lands in `failed_pairs`, skips the rest of the position block and
  is still persisted by the `finally`; resolution followed by
  `is_closing_complete` finalizing on the **same** tick.
- [ ] **Implement** as step 3a of the per-pair loop, *before*
  `is_closing_complete`:

```python
            if not resolve_unconfirmed_closing_order(pair, trailing_state.get(pair)):
                logging.error(f"[{pair}] Could not resolve an unconfirmed closing order; skipping pair.")
                failed_pairs.append(pair)
                continue
```

Marking the pair failed **is** the alerting mechanism: it routes into the
existing edge-triggered consecutive-failure alert (one message per episode)
instead of a new, floodable channel. Same reasoning already applied to a pair
with no price — an unresolved pair is an unmanaged pair.

**Commit:** `feat(scheduler): resolve unconfirmed closing orders before managing a pair`

## Task 10 — docs

- [ ] `CLAUDE.md`:
  - Position-lifecycle section: `close_position` writes the pending fields before
    placing; `reprice_closing_order` no longer keeps the dead `closing_order_id`
    after a failed replacement — **this supersedes the current documented
    behaviour**, and the pending client id now provides that protection.
  - Invariant: a position with `closing_order_id` **or** `closing_cl_ord_id` set
    is not open.
  - Design choices: one entry for the per-attempt `cl_ord_id` — why per attempt
    (Kraken's open-order uniqueness + cancel/replace), why not `userref` (32-bit,
    non-unique, groups orders, and `QueryOrders` needs a txid anyway), and why
    resolution goes through `OpenOrders`/`ClosedOrders`.
- [ ] `docs/BACKLOG.md`: move the `cl_ord_id` bullet out of the 💤 Deferred card
  into a short ✅ Shipped card. Keep the `get_order_state` bullet where it is.

**Commit:** `docs: record the cl_ord_id placement design`

### PR 2 acceptance checklist

- [ ] `PYTHONPATH=. pytest tests/unit/` — passes, coverage ≥ 80%.
- [ ] `python -m ruff check . && python -m ruff format --check .` — exit 0.
- [ ] `grep -rn "closing_cl_ord_id" trading/ core/` — every clearing site clears
      it alongside `closing_order_id`.
- [ ] Manual smoke: `docker compose up -d --build`, one full session in the logs,
      `/health` OK.

---

## Non-goals

- The opening order — `create_position` places none.
- Partial-fill PnL reconciliation beyond PR #64's remainder sizing.
- A periodic orphan sweep for orders left by a killed process (different
  mechanism, different trigger; the residual risk is stated in the spec).
- Gating the `ClosedOrders` fallback on rate-limit cost — exhausting the counter
  already degrades safely into "unknown" + a failed pair.
- Passing the `OrderState` down to avoid the double `get_order_state` per closing
  tick (separate deferred card).
