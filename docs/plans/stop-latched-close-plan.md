# Stop-latched close — implementation plan

> **Executed.** Kept as the record of intent; the spec is the living document.
> Four deviations, all decided during implementation or the code review that
> followed — read the spec, not the code blocks below, for what shipped:
>
> - **Task 2** — the latch is a plain assignment in `tick_position`, not a
>   `setdefault` in `close_position`. `tick_position` only runs while `is_open`,
>   and the assignment is what makes it false, so `setdefault` guarded a path
>   that cannot happen. It also keeps `close_position` a placement primitive that
>   every retry reuses.
> - **Task 4** — shipped as `manage_close_position` returning a three-value
>   `ClosingState` instead of `manage_closing_order` returning a `bool`. `FILLED`
>   absorbed the finalize branch, so the scheduler still performs every DB write.
> - **Task 5** — the scheduler gates on `is_closing(pos)` and dispatches on the
>   `ClosingState` with `match`/`case`.
> - **Untasked work.** The code review on PR #67 added two changes this plan
>   never anticipated: the alerting redesign (spec §9 — `pair_error`, three
>   independent streaks, no failure reason) and the Kraken `OrderStatus` enum
>   (spec §10 — an unresolvable order reports `UNMANAGED` instead of freezing the
>   pair in silence).

> **For agentic workers:** implement task-by-task using TDD (write the failing test first, watch it fail, then implement). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** make a breached trailing stop an irrevocable decision, so a failed
`place_limit_order` can no longer let the position slip back into `tick_position`
and have its stop widened or its trail re-armed.

**Spec:** [`../specs/stop-latched-close-design.md`](../specs/stop-latched-close-design.md)

**Architecture:** a new latch field `stop_at` (a rename of
`closing_requested_at`, written *before* the placement attempt instead of after a
successful one) makes `is_open` false from the moment the stop is hit. A single
`manage_closing_order` owns every state between the breach and the fill, running
after `is_closing_complete` in the scheduler's per-pair loop.

**Shape:** one PR, `feat/stop-latched-close`. **Task order is load-bearing:**
tasks 1–5 are individually behaviour-preserving and task 6 is the switch. Do not
reorder — tightening `is_open` before `manage_closing_order` is wired would
freeze a latched position with nothing able to place its exit.

**Depends on:** nothing outstanding. PR #64 (post-cancel remainder sizing) and
PR #65 (the `core/db/` split) are merged into `main`; branch from `main`.

**Tech stack:** Python 3.12, SQLAlchemy 2 + Alembic, krakenex, pytest.

## Global Constraints

- Coverage gate is **80%**; the full unit suite and ruff must pass before each commit.
- `trading/positions_manager.py` must never import `core.database` — persistence
  is the scheduler's job (A5). This plan adds no DB access to it.
- Model and Alembic migration change **together**; CI builds the schema from migrations.
- Round only at boundaries. This plan adds no rounding.
- No new exit trigger, no global stop-loss, no change to any stop distance.

## Commands (run from repo root; `PYTHONPATH=.` required)

- Single test: `PYTHONPATH=. pytest tests/unit/path/test_file.py::test_name -v --no-cov`
- Full unit suite: `PYTHONPATH=. pytest tests/unit/`
- Lint + format: `python -m ruff check . && python -m ruff format --check .`
- Migration against a fresh DB: `docker compose -f docker-compose.test.yml run --rm test alembic upgrade head`

---

## Task 1 — rename `closing_requested_at` to `stop_at`

Pure rename: column, dict key, API field, dashboard, tests. **No semantics
change** — the field is still written on a successful placement and still cleared
by `is_closing_complete`. Task 2 changes when it is written.

**Files:**
- Modify: `core/db/models.py:150` (column), `core/db/models.py:185` (`to_dict`)
- Modify: `core/db/positions.py:32`, `core/db/positions.py:57-58`
- Create: `scripts/migrations/versions/20260812_01_stop_at.py`
- Modify: `api/schemas.py:72`
- Modify: `services/grafana/dashboards/botc.json:779`
- Modify: `trading/positions_manager.py:157` (clear tuple), `:334` (write)
- Test: `tests/unit/core/test_database.py:754`, `tests/unit/api/test_api.py:31`,
  `tests/unit/trading/test_positions_manager.py:265,379,384,499,504,542,551`

**Interfaces:**
- Produces: the position-dict key `stop_at` and the column `trailing_state.stop_at`,
  used by every later task.

- [ ] **Step 1: Update the failing tests first**

Rename the key in every existing assertion. In
`tests/unit/trading/test_positions_manager.py`:

```python
# line 265, in test_close_position_updates_position_on_success
    assert pos["stop_at"] == _now

# lines 379/384, in test_is_closing_complete_clears_fields_and_reopens_position_when_order_dead
        "stop_at": "2026-07-26T00:00:00+00:00",
    ...
    assert "stop_at" not in pos

# lines 499/504, in test_is_closing_complete_reopens_position_on_any_unfinalizable_terminal_state
        "stop_at": "2026-07-26T00:00:00+00:00",
    ...
    assert "stop_at" not in pos

# lines 542/551, in test_reprice_closing_order_reprices_on_price_move
        "stop_at": _requested_at,
    ...
    # A reprice must not overwrite when the stop was hit.
    assert pos["stop_at"] == _requested_at
```

In `tests/unit/core/test_database.py:754`: `stop_at=datetime(2026, 4, 1, 11, 15, 0, tzinfo=UTC),`
In `tests/unit/api/test_api.py:31`: `"stop_at": None,`

Add the round-trip test next to it in `tests/unit/core/test_database.py`, since
the loader only emits optional keys when the column is non-`NULL`:

```python
def test_trailing_record_round_trips_stop_at(monkeypatch):
    _breach = datetime(2026, 4, 1, 11, 15, 0, tzinfo=UTC)
    record = _state_entry_to_trailing_record("XBTEUR", _make_trailing_state_entry(stop_at=_breach))
    assert record.stop_at == _breach
    assert _trailing_record_to_state_entry(record)["stop_at"] == _breach


def test_trailing_record_omits_stop_at_when_null(monkeypatch):
    record = _state_entry_to_trailing_record("XBTEUR", _make_trailing_state_entry())
    assert "stop_at" not in _trailing_record_to_state_entry(record)
```

Import both helpers from `core.db.positions` at the top of the file if they are
not already imported there.

- [ ] **Step 2: Run the suite to verify it fails**

Run: `PYTHONPATH=. pytest tests/unit/ -q --no-cov`
Expected: FAIL — `KeyError: 'stop_at'` / `TypeError: unexpected keyword argument 'stop_at'`.

- [ ] **Step 3: Rename the column and the mapping**

`core/db/models.py:150`:

```python
    stop_at = Column(DateTime(timezone=True), nullable=True)
```

`core/db/models.py:185`, inside `to_dict`:

```python
            "stop_at": self.stop_at,
```

`core/db/positions.py:32`:

```python
        stop_at=position_data.get("stop_at"),
```

`core/db/positions.py:57-58`:

```python
    if record.stop_at is not None:
        state_entry["stop_at"] = record.stop_at
```

- [ ] **Step 4: Rename in `trading/positions_manager.py`**

Line 157, the clearing tuple (still clears it in this task):

```python
        for key in ("closing_order_id", "closing_price", "stop_at"):
```

Line 334, inside `close_position`'s success `pos.update({...})`:

```python
                "stop_at": now_utc(),
```

- [ ] **Step 5: Rename in the API schema**

`api/schemas.py:72`:

```python
    stop_at: datetime | None = None
```

- [ ] **Step 6: Write the migration**

Create `scripts/migrations/versions/20260812_01_stop_at.py`:

```python
"""Rename trailing_state.closing_requested_at to stop_at.

The field now latches the stop breach (written before the placement attempt),
not the successful close request. Values carry over: the old timestamp is the
same event for every row written before this change.

Revision ID: 20260812_01
Revises: 20260616_01
Create Date: 2026-08-12 00:00:00
"""

from __future__ import annotations

from alembic import op

revision = "20260812_01"
down_revision = "20260616_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("trailing_state", "closing_requested_at", new_column_name="stop_at")


def downgrade() -> None:
    op.alter_column("trailing_state", "stop_at", new_column_name="closing_requested_at")
```

- [ ] **Step 7: Update the Grafana panel**

In `services/grafana/dashboards/botc.json:779`, inside the `rawSql` string,
replace `closing_requested_at AS \"Close Requested\"` with
`stop_at AS \"Stop Hit\"`. Change nothing else on that line.

- [ ] **Step 8: Run the suite and the migration**

Run: `PYTHONPATH=. pytest tests/unit/ && python -m ruff check . && python -m ruff format --check .`
Expected: PASS, coverage ≥ 80%.

Run: `docker compose -f docker-compose.test.yml run --rm test alembic upgrade head`
Expected: succeeds on a fresh DB.

- [ ] **Step 9: Commit**

```bash
git add core/db/ trading/positions_manager.py api/schemas.py services/grafana/dashboards/botc.json scripts/migrations/versions/20260812_01_stop_at.py tests/
git commit -m "refactor: rename closing_requested_at to stop_at"
```

---

## Task 2 — `close_position` latches before placing and returns a result

**Files:**
- Modify: `trading/positions_manager.py:313-337` (`close_position`)
- Test: `tests/unit/trading/test_positions_manager.py` (the `close_position` section, ~line 249)

**Interfaces:**
- Consumes: the `stop_at` key from Task 1.
- Produces: `close_position(pair, pos, last_prices) -> bool` — `True` when an
  order is resting at Kraken, `False` otherwise. Sets `pos["stop_at"]` on the
  first attempt and never overwrites it.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/trading/test_positions_manager.py`, in the `close_position`
section:

```python
def test_close_position_latches_stop_at_before_placing(monkeypatch) -> None:
    """The latch must be durable before the order goes out: a lost or rejected
    placement still means the exit is owed."""
    _now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(positions_manager, "now_utc", lambda: _now)
    seen: list = []
    monkeypatch.setattr(
        positions_manager,
        "place_limit_order",
        lambda *args: seen.append(pos.get("stop_at")) or "ORDER123",
    )

    pos = {"side": "sell", "entry_price": 100.0, "stop_price": 95.0, "volume": 1.0}

    assert positions_manager.close_position("XBTEUR", pos, {"XBTEUR": 90.0}) is True
    assert seen == [_now]


def test_close_position_latches_stop_at_when_placement_fails(monkeypatch) -> None:
    _now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(positions_manager, "now_utc", lambda: _now)
    monkeypatch.setattr(positions_manager, "place_limit_order", lambda *args: None)

    pos = {"side": "sell", "entry_price": 100.0, "stop_price": 95.0, "volume": 1.0}

    assert positions_manager.close_position("XBTEUR", pos, {"XBTEUR": 90.0}) is False
    assert pos["stop_at"] == _now
    assert "closing_order_id" not in pos


def test_close_position_latches_stop_at_when_placement_raises(monkeypatch) -> None:
    _now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(positions_manager, "now_utc", lambda: _now)

    def boom(*_args):
        raise Exception("kraken exploded")

    monkeypatch.setattr(positions_manager, "place_limit_order", boom)

    pos = {"side": "sell", "entry_price": 100.0, "stop_price": 95.0, "volume": 1.0}

    assert positions_manager.close_position("XBTEUR", pos, {"XBTEUR": 90.0}) is False
    assert pos["stop_at"] == _now


def test_close_position_does_not_overwrite_an_existing_stop_at(monkeypatch) -> None:
    """A retry records the first breach, not the attempt that finally landed."""
    _breach = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(positions_manager, "now_utc", lambda: datetime(2026, 1, 1, 13, 0, tzinfo=UTC))
    monkeypatch.setattr(positions_manager, "place_limit_order", lambda *args: "ORDER123")

    pos = {"side": "sell", "entry_price": 100.0, "stop_price": 95.0, "volume": 1.0, "stop_at": _breach}

    assert positions_manager.close_position("XBTEUR", pos, {"XBTEUR": 90.0}) is True
    assert pos["stop_at"] == _breach


def test_close_position_announces_the_breach_only_on_the_first_attempt(monkeypatch) -> None:
    """Retries must not re-send the breach line every tick during an outage."""
    monkeypatch.setattr(positions_manager, "now_utc", lambda: datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    monkeypatch.setattr(positions_manager, "place_limit_order", lambda *args: "ORDER123")
    captured: list[bool] = []
    monkeypatch.setattr(
        positions_manager.logging, "info", lambda msg, to_telegram=False: captured.append(to_telegram)
    )

    retry = {
        "side": "sell",
        "entry_price": 100.0,
        "stop_price": 95.0,
        "volume": 1.0,
        "stop_at": datetime(2026, 1, 1, 11, 0, tzinfo=UTC),
    }
    positions_manager.close_position("XBTEUR", retry, {"XBTEUR": 90.0})

    assert captured == [False]
```

Also update the existing failure tests to assert the new return value:

```python
# test_close_position_leaves_position_untouched_when_place_order_fails
    assert positions_manager.close_position("XBTEUR", pos, prices) is False
    assert "closing_order_id" not in pos

# test_close_position_leaves_position_untouched_on_unexpected_error
    assert positions_manager.close_position("XBTEUR", pos, prices) is False
    assert "closing_order_id" not in pos
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/unit/trading/test_positions_manager.py -k close_position -v --no-cov`
Expected: FAIL — `assert None is True`, and `KeyError: 'stop_at'` on the failure paths.

- [ ] **Step 3: Implement**

Replace `close_position` in `trading/positions_manager.py`:

```python
def close_position(pair: str, pos: dict[str, Any], last_prices: dict[str, float]) -> bool:
    """Place the exit order for a position whose stop was hit.

    ``stop_at`` is latched first, before anything that can fail, so a rejected or
    lost placement still records that an exit is owed — otherwise the next tick
    would re-enter ``tick_position`` and could widen the stop past the breach.
    Returns True only when an order is resting at Kraken."""
    first_attempt = "stop_at" not in pos
    try:
        pos.setdefault("stop_at", now_utc())
        side = pos["side"]
        stop_price = pos["stop_price"]
        current_price = last_prices[pair]
        volume = float(pos.get("volume", 0.0))
        logging.info(
            f"[{pair}] ⛔ Stop price {round_price(pair, stop_price):,}€ hitted: placing LIMIT {side.upper()} order | {volume:.8f} @ {round_price(pair, current_price):,}€",
            to_telegram=first_attempt,
        )

        closing_order = place_limit_order(pair, side, current_price, volume)
        if not closing_order:
            logging.error(f"[{pair}] Failed to place the closing order; the exit stays owed and is retried next tick.")
            return False

        pos.update(
            {
                "volume": round(volume, 8),
                "closing_price": current_price,
                "closing_order_id": closing_order,
            }
        )
        return True
    except Exception as e:
        # Recoverable: scheduler must keep ticking; surface failure via Telegram.
        logging.error(f"Failed to close trailing position: {e}", to_telegram=True)
        return False
```

Note the two deliberate changes beyond the latch: the placement error loses
`to_telegram=True` (repetition is reported once per episode by the pair-failure
alert, wired in Task 5), and `stop_at` is gone from the success `update` because
`setdefault` already wrote it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/unit/trading/test_positions_manager.py -v --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading/positions_manager.py tests/unit/trading/test_positions_manager.py
git commit -m "feat(positions): latch stop_at before the closing order is placed"
```

---

## Task 3 — `is_closing_complete` keeps the latch

**Files:**
- Modify: `trading/positions_manager.py:150-159`
- Test: `tests/unit/trading/test_positions_manager.py:367-385`, `:483-509`

- [ ] **Step 1: Update the failing tests**

In `test_is_closing_complete_clears_fields_and_reopens_position_when_order_dead`
and `test_is_closing_complete_reopens_position_on_any_unfinalizable_terminal_state`,
replace the `stop_at` assertions (leave the `is_open` assertions alone — Task 6
flips those):

```python
    assert "closing_order_id" not in pos
    assert "closing_price" not in pos
    # The exit is still owed: only the dead order's own fields are cleared.
    assert pos["stop_at"] == "2026-07-26T00:00:00+00:00"
```

Rename both tests, replacing `reopens_position` with `keeps_the_exit_owed`.

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. pytest tests/unit/trading/test_positions_manager.py -k is_closing_complete -v --no-cov`
Expected: FAIL — `KeyError: 'stop_at'`.

- [ ] **Step 3: Implement**

`trading/positions_manager.py`, in the unfinalizable-terminal branch:

```python
        logging.warning(
            f"Closing order {closing_order} ended as {state.status} with no usable fill price; "
            "re-placing the exit.",
            to_telegram=True,
        )
        for key in ("closing_order_id", "closing_price"):
            pos.pop(key, None)
        return False
```

Update the function's docstring: "Any terminal outcome that cannot be finalized
instead clears the dead order's fields, keeping `stop_at` so the same tick
re-places the exit."

- [ ] **Step 4: Run to verify pass**

Run: `PYTHONPATH=. pytest tests/unit/trading/test_positions_manager.py -v --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading/positions_manager.py tests/unit/trading/test_positions_manager.py
git commit -m "feat(positions): keep the stop latch when a closing order dies"
```

---

## Task 4 — `manage_closing_order`

**Files:**
- Modify: `trading/positions_manager.py` (add after `reprice_closing_order`, ~line 311)
- Test: `tests/unit/trading/test_positions_manager.py` (new section at the end)

**Interfaces:**
- Consumes: `close_position(...) -> bool` (Task 2), the existing
  `reprice_closing_order(pair, pos, last_prices) -> None` and
  `refresh_position(pair, pos, balance, last_prices, trailing_state) -> bool`.
- Produces: `manage_closing_order(pair, pos, balance, last_prices, trailing_state) -> bool`
  — `False` **only** when a placement was attempted and failed.

`reprice_closing_order` keeps its name and its tests; it becomes one branch of
the dispatcher rather than a scheduler entry point.

- [ ] **Step 1: Write the failing tests**

```python
# ============================================================================
# manage_closing_order
# ============================================================================


def test_manage_closing_order_is_a_noop_for_an_open_position(monkeypatch) -> None:
    monkeypatch.setattr(positions_manager, "get_order_state", lambda _: pytest.fail("no API call"))
    monkeypatch.setattr(positions_manager, "place_limit_order", lambda *a: pytest.fail("no placement"))

    pos = {"side": "sell", "volume": 0.5, "entry_price": 100.0}
    assert positions_manager.manage_closing_order("XBTEUR", pos, {}, {"XBTEUR": 100.0}, {}) is True


def test_manage_closing_order_reprices_a_live_order(monkeypatch) -> None:
    calls: list = []
    monkeypatch.setattr(positions_manager, "reprice_closing_order", lambda *a: calls.append(a))
    monkeypatch.setattr(positions_manager, "close_position", lambda *a: pytest.fail("must not re-place"))

    pos = {"side": "sell", "volume": 0.5, "stop_at": "2026-07-26T00:00:00+00:00", "closing_order_id": "ORD001"}
    assert positions_manager.manage_closing_order("XBTEUR", pos, {}, {"XBTEUR": 105.0}, {}) is True
    assert calls == [("XBTEUR", pos, {"XBTEUR": 105.0})]


def test_manage_closing_order_refreshes_then_replaces_when_no_order_rests(monkeypatch) -> None:
    """A latched position never reaches tick_position, so nothing else resizes it
    — and a stale volume may be exactly why the last attempt was rejected."""
    order: list[str] = []
    monkeypatch.setattr(
        positions_manager, "refresh_position", lambda *a: order.append("refresh") or True
    )
    monkeypatch.setattr(positions_manager, "close_position", lambda *a: order.append("close") or True)

    pos = {"side": "sell", "volume": 0.5, "stop_at": "2026-07-26T00:00:00+00:00"}
    assert positions_manager.manage_closing_order("XBTEUR", pos, {}, {"XBTEUR": 100.0}, {}) is True
    assert order == ["refresh", "close"]


def test_manage_closing_order_reports_a_failed_replacement(monkeypatch) -> None:
    monkeypatch.setattr(positions_manager, "refresh_position", lambda *a: True)
    monkeypatch.setattr(positions_manager, "close_position", lambda *a: False)

    pos = {"side": "sell", "volume": 0.5, "stop_at": "2026-07-26T00:00:00+00:00"}
    assert positions_manager.manage_closing_order("XBTEUR", pos, {}, {"XBTEUR": 100.0}, {}) is False


def test_manage_closing_order_succeeds_when_the_position_is_dropped(monkeypatch) -> None:
    """A drop is a resolved pair, not a failure: there is nothing left to place,
    and it is the natural end of an otherwise endless retry loop."""
    monkeypatch.setattr(positions_manager, "refresh_position", lambda *a: False)
    monkeypatch.setattr(positions_manager, "close_position", lambda *a: pytest.fail("nothing to place"))

    pos = {"side": "sell", "volume": 0.5, "stop_at": "2026-07-26T00:00:00+00:00"}
    assert positions_manager.manage_closing_order("XBTEUR", pos, {}, {"XBTEUR": 100.0}, {}) is True
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. pytest tests/unit/trading/test_positions_manager.py -k manage_closing_order -v --no-cov`
Expected: FAIL — `AttributeError: module has no attribute 'manage_closing_order'`.

- [ ] **Step 3: Implement**

Add to `trading/positions_manager.py`, immediately after `reprice_closing_order`:

```python
def manage_closing_order(
    pair: str,
    pos: dict[str, Any],
    balance: dict[str, Any],
    last_prices: dict[str, float],
    trailing_state: dict[str, Any],
) -> bool:
    """Drive an owed exit toward a resting order.

    Owns every state between the stop breach and the fill: chase the price of a
    live order, or place one when none rests. Returns False only when a placement
    was attempted and failed, so the scheduler can mark the pair failed — a
    position dropped by ``refresh_position`` is a resolved pair, not a failure."""
    if not pos or not pos.get("stop_at"):
        return True

    if pos.get("closing_order_id"):
        reprice_closing_order(pair, pos, last_prices)
        return True

    if not refresh_position(pair, pos, balance, last_prices, trailing_state):
        return True
    return close_position(pair, pos, last_prices)
```

- [ ] **Step 4: Run to verify pass**

Run: `PYTHONPATH=. pytest tests/unit/trading/test_positions_manager.py -v --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading/positions_manager.py tests/unit/trading/test_positions_manager.py
git commit -m "feat(positions): add manage_closing_order to drive an owed exit"
```

---

## Task 5 — scheduler wiring

**Files:**
- Modify: `core/scheduler.py:15-21` (import), `core/scheduler.py:187-188` (the `elif`)
- Test: `tests/unit/core/test_scheduler.py:150-233`

**Interfaces:**
- Consumes: `manage_closing_order(pair, pos, balance, last_prices, trailing_state) -> bool`.

- [ ] **Step 1: Update the failing tests**

In `tests/unit/core/test_scheduler.py`, rename
`test_trading_session_reprices_closing_order_and_does_not_tick` to
`test_trading_session_manages_a_closing_order_and_does_not_tick`, patch the new
name, and add `stop_at` to the stored state (the branch now keys on it):

```python
def test_trading_session_manages_a_closing_order_and_does_not_tick(monkeypatch):
    _setup_one_pair_loop(
        monkeypatch,
        trailing_state={
            "side": "sell",
            "entry_price": 50000.0,
            "stop_at": datetime(2026, 5, 12, 9, 0, 0, tzinfo=UTC),
            "closing_order_id": "ORD001",
        },
    )
    monkeypatch.setattr(scheduler, "TRADING_ENABLED", True)
    monkeypatch.setattr(scheduler, "is_closing_complete", lambda _s: False)
    managed: list = []
    monkeypatch.setattr(scheduler, "manage_closing_order", lambda *a, **k: managed.append(a) or True)
    monkeypatch.setattr(scheduler, "tick_position", lambda *a, **k: pytest.fail("must not tick a closing position"))
    monkeypatch.setattr(scheduler, "create_position", lambda *a, **k: pytest.fail("must not create a new position"))
    calls = _patch_finalize(monkeypatch)

    scheduler.trading_session()

    assert len(managed) == 1
    assert managed[0][0] == "XBTEUR"
    assert calls[0]["status"] == "completed"
```

Add the two new cases:

```python
def test_trading_session_manages_a_latched_position_with_no_resting_order(monkeypatch):
    """The stop fired but no order rests — the exit is owed and must be placed."""
    _setup_one_pair_loop(
        monkeypatch,
        trailing_state={
            "side": "sell",
            "entry_price": 50000.0,
            "stop_at": datetime(2026, 5, 12, 9, 0, 0, tzinfo=UTC),
        },
    )
    monkeypatch.setattr(scheduler, "TRADING_ENABLED", True)
    monkeypatch.setattr(scheduler, "is_closing_complete", lambda _s: False)
    managed: list = []
    monkeypatch.setattr(scheduler, "manage_closing_order", lambda *a, **k: managed.append(a) or True)
    monkeypatch.setattr(scheduler, "create_position", lambda *a, **k: pytest.fail("position still exists"))
    calls = _patch_finalize(monkeypatch)

    scheduler.trading_session()

    assert len(managed) == 1
    assert calls[0]["status"] == "completed"


def test_trading_session_fails_the_pair_when_the_owed_exit_cannot_be_placed(monkeypatch):
    """An owed exit with no resting order is an unmanaged pair: it must not pass
    as a successful session, so the consecutive-failure alert can fire."""
    _setup_one_pair_loop(
        monkeypatch,
        trailing_state={
            "side": "sell",
            "entry_price": 50000.0,
            "stop_at": datetime(2026, 5, 12, 9, 0, 0, tzinfo=UTC),
        },
    )
    monkeypatch.setattr(scheduler, "TRADING_ENABLED", True)
    monkeypatch.setattr(scheduler, "is_closing_complete", lambda _s: False)
    monkeypatch.setattr(scheduler, "manage_closing_order", lambda *a, **k: False)
    saved: list = []
    monkeypatch.setattr(db, "save_trailing_state", lambda pair, state: saved.append((pair, state)))
    calls = _patch_finalize(monkeypatch)

    scheduler.trading_session()

    assert calls[0]["status"] == "failed"
    assert "XBTEUR" in calls[0]["failure_reason"]


def test_trading_session_replaces_a_dead_closing_order_on_the_same_tick(monkeypatch):
    """The reason manage_closing_order runs *after* is_closing_complete: a terminal
    order with a remainder is cleared and re-placed within one tick, instead of
    leaving a breached position with nothing on the book for a full interval."""
    stored = {
        "side": "sell",
        "entry_price": 50000.0,
        "stop_at": datetime(2026, 5, 12, 9, 0, 0, tzinfo=UTC),
        "closing_order_id": "ORD001",
    }
    _setup_one_pair_loop(monkeypatch, trailing_state=stored)
    monkeypatch.setattr(scheduler, "TRADING_ENABLED", True)

    def _clear_dead_order(pos):
        pos.pop("closing_order_id", None)
        return False

    monkeypatch.setattr(scheduler, "is_closing_complete", _clear_dead_order)
    managed: list = []
    monkeypatch.setattr(scheduler, "manage_closing_order", lambda *a, **k: managed.append(a[1]) or True)
    monkeypatch.setattr(scheduler, "create_position", lambda *a, **k: pytest.fail("position still exists"))
    monkeypatch.setattr(db, "save_trailing_state", lambda *a: None)
    calls = _patch_finalize(monkeypatch)

    scheduler.trading_session()

    # Same tick: the dead order is gone and the manager still saw the latched position.
    assert len(managed) == 1
    assert "closing_order_id" not in managed[0]
    assert calls[0]["status"] == "completed"
```

In the two persistence tests around lines 195-233, rename the local `_reprice`
helper to `_manage`, give it the five-argument signature, return `True`, and
patch `scheduler.manage_closing_order`; add `"stop_at": datetime(2026, 5, 12, 9, 0, 0, tzinfo=UTC)`
to their `stored` dicts and to the expected saved state:

```python
    def _manage(_pair, pos, _balance, _prices, _state):
        pos["closing_order_id"] = "ORD002"
        return True

    monkeypatch.setattr(scheduler, "manage_closing_order", _manage)
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. pytest tests/unit/core/test_scheduler.py -v --no-cov`
Expected: FAIL — `AttributeError: <module 'core.scheduler'> has no attribute 'manage_closing_order'`.

- [ ] **Step 3: Implement the import**

`core/scheduler.py:15-21`:

```python
from trading.positions_manager import (
    create_position,
    is_closing_complete,
    is_open,
    manage_closing_order,
    tick_position,
)
```

- [ ] **Step 4: Implement the branch**

Replace `core/scheduler.py:187-188`:

```python
                elif (trailing_state.get(pair) or {}).get("stop_at"):
                    # An owed exit with no resting order is an unmanaged pair, so a
                    # failure here routes into the existing consecutive-failure alert
                    # rather than a new per-tick Telegram message.
                    if not manage_closing_order(
                        pair, trailing_state[pair], current_balance, last_prices, trailing_state
                    ):
                        logging.error(f"[{pair}] Could not place the owed exit order; marking the pair failed.")
                        failed_pairs.append(pair)
```

Leave the `if is_closing_complete(...)` branch above it untouched: finalizing
first is what lets a dead order be cleared and re-placed on the same tick.

- [ ] **Step 5: Run to verify pass**

Run: `PYTHONPATH=. pytest tests/unit/ && python -m ruff check . && python -m ruff format --check .`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add core/scheduler.py tests/unit/core/test_scheduler.py
git commit -m "feat(scheduler): drive owed exits through manage_closing_order"
```

---

## Task 6 — `is_open` accounts for the latch

**This is the behaviour switch.** Everything before it was behaviour-preserving.

**Files:**
- Modify: `trading/positions_manager.py:127-128`
- Test: `tests/unit/trading/test_positions_manager.py:299-312`, `:385`, `:506`;
  `tests/unit/core/test_scheduler.py`

- [ ] **Step 1: Write the failing tests**

Replace `test_is_open_returns_false_when_closing_order_present` and add the latch
cases:

```python
def test_is_open_returns_false_when_stop_was_hit() -> None:
    """A position whose stop fired is not open, whether or not an order was placed."""
    assert positions_manager.is_open({"stop_at": "2026-07-26T00:00:00+00:00"}) is False


def test_is_open_returns_false_while_a_closing_order_rests() -> None:
    pos = {"stop_at": "2026-07-26T00:00:00+00:00", "closing_order_id": "ORD001"}
    assert positions_manager.is_open(pos) is False
```

Flip the two `is_closing_complete` assertions (lines 385 and 506) — a dead order
no longer reopens the position:

```python
    assert positions_manager.is_open(pos) is False
```

Add the scheduler regression that this whole spec exists for:

```python
def test_trading_session_never_ticks_a_latched_position(monkeypatch):
    """The defect this replaces: a failed placement left the position open, so the
    next tick could widen the stop past the breach or re-arm the trail."""
    _setup_one_pair_loop(
        monkeypatch,
        trailing_state={
            "side": "sell",
            "entry_price": 50000.0,
            "stop_at": datetime(2026, 5, 12, 9, 0, 0, tzinfo=UTC),
        },
    )
    monkeypatch.setattr(scheduler, "TRADING_ENABLED", True)
    monkeypatch.setattr(scheduler, "is_closing_complete", lambda _s: False)
    monkeypatch.setattr(scheduler, "manage_closing_order", lambda *a, **k: True)
    monkeypatch.setattr(scheduler, "tick_position", lambda *a, **k: pytest.fail("must not manage a latched position"))
    calls = _patch_finalize(monkeypatch)

    scheduler.trading_session()

    assert calls[0]["status"] == "completed"
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. pytest tests/unit/trading/test_positions_manager.py -k is_open -v --no-cov`
Expected: FAIL — `assert True is False`.

- [ ] **Step 3: Implement**

`trading/positions_manager.py:127-128`:

```python
def is_open(pos: dict[str, Any] | None) -> bool:
    """A position is open only until its stop fires. ``closing_order_id`` needs no
    clause here: it can only be set by ``close_position``, which latches
    ``stop_at`` first."""
    return bool(pos) and not pos.get("stop_at")
```

- [ ] **Step 4: Run the full suite**

Run: `PYTHONPATH=. pytest tests/unit/ && python -m ruff check . && python -m ruff format --check .`
Expected: PASS, coverage ≥ 80%.

- [ ] **Step 5: Commit**

```bash
git add trading/positions_manager.py tests/
git commit -m "fix(positions): a position whose stop fired is not open"
```

---

## Task 7 — documentation

**Files:**
- Modify: `CLAUDE.md` (trading-loop steps, invariants, position lifecycle, Design choices)
- Modify: `docs/BACKLOG.md`

- [ ] **Step 1: Update the CLAUDE.md invariant**

Replace the first invariant bullet:

> - A position whose stop has fired is **not** open — `is_open` is `not stop_at`,
>   and `tick_position` must not run on it, whether or not a closing order was
>   placed. Steps 3–4 of the loop resolve the exit before step 6 checks `is_open`.

- [ ] **Step 2: Update the position-lifecycle entries**

Replace the `close_position` bullet with:

> - **close_position**: Latches `stop_at` (first attempt only) *before* placing a
>   limit order at the current market price, so a rejected or lost placement still
>   records that the exit is owed; records `closing_price` (approximate, at order
>   placement) and `closing_order_id` on success. Returns `False` when no order
>   rests at Kraken. Does NOT compute PnL.

Rename the `reprice_closing_order` bullet to **manage_closing_order**, opening
with the three branches (no `stop_at` → no-op; live `closing_order_id` → the
existing reprice behaviour, whose current text is kept verbatim as that branch;
otherwise `refresh_position` then `close_position`), and add: "Returns `False`
only when a placement was attempted and failed — a position dropped by
`refresh_position` is a resolved pair, not a failure."

In the `is_closing_complete` bullet, change the clearing tuple to
`closing_order_id`/`closing_price` and replace "so the position resumes
management on the same tick" with "keeping `stop_at`, so the same tick re-places
the exit rather than resuming management".

In the trading-loop section, update step 4 to name `manage_closing_order` and
note that it also covers a latched position with no resting order.

- [ ] **Step 3: Add the Design choices entries**

Add the four bullets listed under *Design choices to record in CLAUDE.md* in the
spec, verbatim in substance: the latch and why it is written before the attempt,
`is_open` being `not stop_at`, why `manage_closing_order` runs after
`is_closing_complete`, and the superseded reprice/`is_closing_complete` text.

- [ ] **Step 4: Add the backlog card**

In `docs/BACKLOG.md`, under `## ✅ Shipped`, add a short card (keep it to a few
plain sentences per the repo's style):

```markdown
### Stop-Latched Close

A failed `place_limit_order` used to leave no trace, so the next tick re-entered
`tick_position` and could widen the stop past the breach or re-arm the trail: an
API failure revoked a strategy decision. `stop_at` now latches the breach before
the placement attempt, `is_open` is `not stop_at`, and `manage_closing_order`
owns everything between the breach and the fill.

- Spec: [`specs/stop-latched-close-design.md`](specs/stop-latched-close-design.md)
- Plan: [`plans/stop-latched-close-plan.md`](plans/stop-latched-close-plan.md)
```

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md docs/
git commit -m "docs: record the stop-latched close"
```

---

## Acceptance checklist

- [ ] `PYTHONPATH=. pytest tests/unit/` — passes, coverage ≥ 80%.
- [ ] `python -m ruff check . && python -m ruff format --check .` — exit 0.
- [ ] `alembic upgrade head` **and** `alembic downgrade -1` on a fresh DB — both succeed.
- [ ] `grep -rn "closing_requested_at" --include=*.py --include=*.json --include=*.md .`
      — no hits outside `docs/plans/` history and the migration's own docstring.
- [ ] Diff review: no stop distance, activation rule, or `MIN_VALUE` threshold changed.
- [ ] Manual smoke: `docker compose up -d --build`, one full session in the logs, `/health` OK.
- [ ] `GET /positions` renders `stop_at`, and the Grafana trailing-state panel
      shows the "Stop Hit" column.

## Non-goals

- The `cl_ord_id` idempotency work — separate spec, layered on this one.
- Recording `stop_at` in `closed_positions` for breach-to-fill analytics.
- A periodic orphan sweep for orders left by a killed process.
- Partial-fill reconciliation beyond PR #64's remainder sizing.
- Reducing the three `get_order_state` calls per closing tick.
