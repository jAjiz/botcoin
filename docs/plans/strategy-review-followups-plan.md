# Strategy Review Follow-ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the six follow-ups from the 2026-07-06 trading-strategy review: make order state match the exchange by construction, record and net the real Kraken fee, fix the cumulative PnL panel, switch volatility classification to relative ATR, and consolidate the documentation.

**Architecture:** Five independent stages. Stage A changes the Kraken order boundary so callers learn what was actually submitted and what the exchange will accept. Stage B carries the real fee through the same boundary into `finalize_close` and the schema. Stage C is a Grafana SQL fix. Stage D is the one live behaviour change, isolated so its effect on trading is legible. Stage E is documentation only. Each stage is independently shippable and each leaves the suite green.

**Tech Stack:** Python 3.13, SQLAlchemy 2 (sync), Alembic, pytest + monkeypatch, krakenex, pandas/numpy, Grafana (provisioned JSON dashboard).

**Spec:** [`../specs/strategy-review-followups-design.md`](../specs/strategy-review-followups-design.md)

## Global Constraints

- All commands assume `PYTHONPATH=.`. The local interpreter is `./venv/Scripts/python.exe` (Windows); in Docker it is plain `pytest`.
- The full local pass is `PYTHONPATH=. pytest tests/unit/ && python -m ruff check . && python -m ruff format --check .`. All three must be clean before any commit.
- Coverage gate is **80%**, measured over `api/`, `core/`, `exchange/`, `services/`, `trading/`. Current total is 91%; do not regress it.
- Unit tests never call external APIs. Monkeypatch Kraken and DB functions **at the module where the name is imported** (e.g. `monkeypatch.setattr(positions_manager, "get_order_state", ...)`), not at the definition site.
- When changing an ORM model's table shape, update **both** `core/db/models.py` and a new Alembic migration. They are not auto-synced and CI builds the schema from migrations.
- `_safe_call` returns `None` on every error and that means **"outcome unknown"**, never "failed". Do not convert a `None` into a decision.
- The trailing stop stays the only exit mechanism. No task here adds a stop-loss, a max-loss, or a kill switch.
- Prices and ATR stay at full float precision in state and the DB. Rounding happens only at the order boundary and at display. Task 9 must not round ATR into state.
- Inline comments stay to one line; longer rationale belongs in the docstring or the spec.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `exchange/kraken.py` | Modify | The anti-corruption boundary: capture `ordermin`, return submitted amounts, read the fill fee. |
| `trading/positions_manager.py` | Modify | Store what was submitted; enforce `ordermin`; net the fee into `pnl_percent`. |
| `core/db/models.py` | Modify | `ClosedPosition.fee_eur`. |
| `core/db/positions.py` | Modify | Persist `fee_eur`. |
| `scripts/migrations/versions/20260820_01_closed_position_fee.py` | Create | The `fee_eur` column. |
| `services/grafana/dashboards/botc.json` | Modify | Cumulative PnL in EUR. |
| `trading/parameters_manager.py` | Modify | Classify `ATR/close`; percentiles over the ratio series. |
| `trading/engine.py` | Modify | The same classification, so simulation matches production. |
| `core/runtime.py` | Modify | Calibration field names now carry a ratio, not a price. |
| `core/scheduler.py` | Modify | Pass the close price to the classifier. |
| `docs/trading-strategy.md` | Modify | The three undocumented strategy facts; drop stale `is_closing_complete`. |
| `CLAUDE.md`, `docs/operations.md` | Modify | Remove obsolete claims and duplication. |

---

# Stage A — Exchange-synchronized order amounts and `ordermin` (spec §1)

### Task 1: Capture `ordermin` in the pairs map

**Files:**
- Modify: `exchange/kraken.py:69-88` (`build_pairs_map`)
- Test: `tests/unit/exchange/test_kraken.py:61-89`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `config.PAIRS[pair]["ordermin"]` — a `float`, or `None` when Kraken omits it. Tasks 3 and 4 read it.

- [ ] **Step 1: Update the existing test to expect the new key**

In `tests/unit/exchange/test_kraken.py`, add `"ordermin": "0.0001"` to the mocked `XXBTZEUR` dict in `test_build_pairs_map_populates_metadata_and_decimals`, and add `"ordermin": 0.0001` to the asserted result dict.

- [ ] **Step 2: Add a test for the missing-`ordermin` case**

```python
def test_build_pairs_map_ordermin_absent_reads_none(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken,
        "get_asset_pairs",
        lambda: {
            "XXBTZEUR": {
                "altname": "XBTEUR",
                "wsname": "XBT/EUR",
                "base": "XXBT",
                "quote": "ZEUR",
                "pair_decimals": 1,
                "lot_decimals": 8,
                "cost_decimals": 5,
            }
        },
    )
    pairs_dict: dict[str, dict] = {"XBTEUR": {}}

    kraken.build_pairs_map(pairs_dict)

    assert pairs_dict["XBTEUR"]["ordermin"] is None
```

- [ ] **Step 3: Run both tests to verify they fail**

Run: `PYTHONPATH=. ./venv/Scripts/python.exe -m pytest tests/unit/exchange/test_kraken.py -k build_pairs_map -v`
Expected: FAIL — `KeyError: 'ordermin'` on the new test, and an assertion mismatch on the updated one.

- [ ] **Step 4: Capture `ordermin`**

In `build_pairs_map`, add one entry to the dict built for each pair, after `"cost_decimals"`:

```python
                "ordermin": float(info["ordermin"]) if info.get("ordermin") is not None else None,
```

Kraken returns `ordermin` as a string, so it is converted here — the boundary is where Kraken's vocabulary stops.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTHONPATH=. ./venv/Scripts/python.exe -m pytest tests/unit/exchange/test_kraken.py -k build_pairs_map -v`
Expected: PASS (both).

- [ ] **Step 6: Commit**

```bash
git add exchange/kraken.py tests/unit/exchange/test_kraken.py
git commit -m "feat(kraken): capture ordermin in the pairs map"
```

---

### Task 2: `place_limit_order` returns the amounts it submitted

**Files:**
- Modify: `exchange/kraken.py:228-248` (`place_limit_order`), and the `OrderState` region around `:122-138` for the new dataclass
- Modify: `trading/positions_manager.py:328` (`reprice_closing_order`), `:379-383` (`close_position`)
- Test: `tests/unit/exchange/test_kraken.py`, `tests/unit/trading/test_positions_manager.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `PlacedOrder(txid: str, price: float, volume: float)`, frozen dataclass exported from `exchange/kraken.py`. `place_limit_order(...) -> PlacedOrder | None`. `None` keeps meaning *outcome unknown*. Task 4 reads `PlacedOrder.volume`.

**Why this matters:** `place_limit_order` formats `price`/`volume` to the pair's Kraken precision, sends those strings, and throws them away. Callers keep the unrounded float, so `pos["volume"]` drifts from the order resting at Kraken by up to one lot tick, and that drifted value reaches the DB.

**Migration cost, read before starting:** roughly 40 references to `place_limit_order` exist across the test suite, mostly stubs in `tests/unit/trading/test_positions_manager.py` returning a bare string. Every stub must return a `PlacedOrder` (or `None`). Expect this task to touch many test lines; that is the bulk of the work, not the implementation.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/exchange/test_kraken.py`:

```python
def test_place_limit_order_returns_the_submitted_amounts(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken.api,
        "query_private",
        lambda *args, **kwargs: {"error": [], "result": {"txid": ["ORDER456"]}},
    )
    monkeypatch.setitem(config.PAIRS, "USDCEUR", {"pair_decimals": 4, "lot_decimals": 8})

    placed = kraken.place_limit_order("USDCEUR", "sell", 1.031274, 12.123456789)

    assert placed.txid == "ORDER456"
    assert placed.price == 1.0313
    assert placed.volume == 12.12345679


def test_place_limit_order_returns_none_when_txid_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        kraken.api,
        "query_private",
        lambda *args, **kwargs: {"error": [], "result": {}},
    )

    assert kraken.place_limit_order("XBTEUR", "buy", 80000.0, 0.001) is None
```

The second test pins a hazard the change introduces: a `PlacedOrder` is always truthy, so a missing txid must return `None` or every `if not placed` guard downstream silently breaks.

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=. ./venv/Scripts/python.exe -m pytest tests/unit/exchange/test_kraken.py -k "submitted_amounts or txid_is_missing" -v`
Expected: FAIL — `AttributeError: 'str' object has no attribute 'txid'`, and the second returns `PlacedOrder`-less `None`-vs-`None` only by accident.

- [ ] **Step 3: Add the dataclass and change the return**

Next to `OrderState` in `exchange/kraken.py`:

```python
@dataclass(frozen=True)
class PlacedOrder:
    """What Kraken accepted: the txid, and the price/volume as actually submitted."""

    txid: str
    price: float
    volume: float
```

Then rewrite the tail of `place_limit_order`:

```python
def place_limit_order(
    pair: str, side: str, price: float, volume: float, cl_ord_id: str | None = None
) -> PlacedOrder | None:
    meta = config.PAIRS.get(pair, {})
    price_str = _format_amount(price, meta.get("pair_decimals"))
    volume_str = _format_amount(volume, meta.get("lot_decimals"))
    payload = {
        "pair": pair,
        "type": side,
        "ordertype": "limit",
        "price": price_str,
        "volume": volume_str,
    }
    if cl_ord_id is not None:
        payload["cl_ord_id"] = cl_ord_id
    result = _safe_call(
        f"{side.upper()} limit order",
        lambda: api.query_private("AddOrder", payload, timeout=KRAKEN_HTTP_TIMEOUT),
    )
    if result is None:
        return None
    new_order = result.get("txid", [None])[0]
    if not new_order:
        # A PlacedOrder is always truthy; without this an id-less response reads as success.
        logging.error(f"AddOrder for {pair} returned no txid; treating the outcome as unknown.")
        return None
    logging.info(f"Created LIMIT {side.upper()} order {new_order} | {volume_str} @ {price_str}€")
    return PlacedOrder(txid=new_order, price=float(price_str), volume=float(volume_str))
```

- [ ] **Step 4: Run the kraken tests**

Run: `PYTHONPATH=. ./venv/Scripts/python.exe -m pytest tests/unit/exchange/test_kraken.py -v`
Expected: the two new tests PASS. Pre-existing tests asserting `result == "ORDER456"` now FAIL — fix them in the next step.

- [ ] **Step 5: Update the pre-existing kraken assertions**

`test_place_limit_order_returns_order_id_on_success` asserts `result == "ORDER456"`; change it to `result.txid == "ORDER456"`. Leave `test_place_limit_order_rounds_to_pair_precision` and `test_place_limit_order_without_known_decimals_sends_unrounded` alone — they assert on the captured payload, not the return.

- [ ] **Step 6: Store the submitted amounts at both call sites**

In `close_position` (`trading/positions_manager.py:379-383`):

```python
        placed = place_limit_order(pair, side, current_price, volume, cl_ord_id=cl_ord_id)
        if placed is None:
            logging.error(f"[{pair}] Closing order not confirmed; it remains owed and will be resolved next tick.")
            return False
        pos.update({"closing_order_id": placed.txid, "closing_price": placed.price, "volume": placed.volume})
```

In `reprice_closing_order` (`trading/positions_manager.py:328`):

```python
    placed = place_limit_order(pair, side, current_price, remaining, cl_ord_id=cl_ord_id)
    if placed is None:
        logging.error(f"[{pair}] Closing order replacement not confirmed after cancel; the next tick will resolve it.")
```

and where it previously assigned the txid, use `pos.update({"closing_order_id": placed.txid, "closing_price": placed.price, "volume": placed.volume})`.

Note the ordering that must be preserved: `pos["closing_request_id"]` and the estimated `closing_price` are still written **before** the call, because a lost response must leave state describing the attempt. The post-call update overwrites the estimate with the submitted value; it does not replace the pre-call write.

- [ ] **Step 7: Add a positions test pinning the stored volume**

In `tests/unit/trading/test_positions_manager.py`:

```python
def test_close_position_stores_the_submitted_volume(monkeypatch) -> None:
    monkeypatch.setattr(
        positions_manager,
        "place_limit_order",
        lambda *a, **k: kraken.PlacedOrder(txid="TX1", price=1.0313, volume=12.12345679),
    )
    pos = {"side": "sell", "volume": 12.123456789, "entry_price": 1.0, "stop_at": now_utc()}

    assert positions_manager.close_position("USDCEUR", pos, {"USDCEUR": 1.031274}) is True
    assert pos["volume"] == 12.12345679
    assert pos["closing_price"] == 1.0313
```

Import `kraken` and `now_utc` in the test module if they are not already imported.

- [ ] **Step 8: Update every remaining `place_limit_order` stub**

Run: `PYTHONPATH=. ./venv/Scripts/python.exe -m pytest tests/unit/trading/test_positions_manager.py -v`
Every failure is a stub returning a bare string. Change each to return `kraken.PlacedOrder(txid="<the same id>", price=<the price the test passes>, volume=<the volume the test passes>)`. Stubs that return `None` to simulate a lost response stay as they are.

- [ ] **Step 9: Run the full suite**

Run: `PYTHONPATH=. ./venv/Scripts/python.exe -m pytest tests/unit/ -q`
Expected: all pass, coverage ≥ 80%.

- [ ] **Step 10: Commit**

```bash
git add exchange/kraken.py trading/positions_manager.py tests/
git commit -m "feat(kraken): return the submitted price and volume from place_limit_order"
```

---

### Task 3: Enforce `ordermin` when sizing and when re-placing an owed exit

**Files:**
- Modify: `trading/positions_manager.py:32-60` (`create_position`), `:119-146` (`refresh_position`)
- Test: `tests/unit/trading/test_positions_manager.py`

**Interfaces:**
- Consumes: `config.PAIRS[pair]["ordermin"]` from Task 1.
- Produces: a shared helper `_below_ordermin(pair: str, volume: float) -> bool`, used by both call sites.

**Why `refresh_position` is the right choke point for the owed exit:** `manage_close_position` calls `refresh_position` at `:363` before every re-place, and already treats a `False` return as "dropped: a resolved pair, not a failure" (`ClosingState.PENDING`). So the sub-`ordermin` drop composes with the existing structure instead of needing a new path — and `refresh_position` is the only one of the two that holds `trailing_state` and can actually drop the position.

- [ ] **Step 1: Write the failing tests**

```python
def test_create_position_skipped_when_volume_below_ordermin(monkeypatch, caplog) -> None:
    monkeypatch.setitem(config.PAIRS, "XBTEUR", {"ordermin": 0.0001})
    monkeypatch.setattr(positions_manager, "calculate_position", lambda *a, **k: ("sell", 5000.0))
    trailing_state: dict = {}

    positions_manager.create_position("XBTEUR", {}, {"XBTEUR": 100_000_000.0}, 1.0, trailing_state)

    assert trailing_state == {}


def test_refresh_position_drops_when_remaining_below_ordermin(monkeypatch) -> None:
    monkeypatch.setitem(config.PAIRS, "XBTEUR", {"ordermin": 0.0001})
    monkeypatch.setattr(positions_manager, "calculate_position", lambda *a, **k: ("sell", 5000.0))
    pos = {"side": "sell", "volume": 0.00001, "stop_at": now_utc()}
    trailing_state = {"XBTEUR": pos}

    result = positions_manager.refresh_position(
        "XBTEUR", pos, {}, {"XBTEUR": 100_000_000.0}, trailing_state
    )

    assert result is False
    assert "XBTEUR" not in trailing_state
```

Both cases size a position worth 5 000€ against a 100 000 000€ price, so the value clears `MIN_VALUE` but the volume (0.00005) sits below `ordermin`. That is the gap `MIN_VALUE` does not cover and the whole reason for the task.

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=. ./venv/Scripts/python.exe -m pytest tests/unit/trading/test_positions_manager.py -k ordermin -v`
Expected: FAIL — the position is created / kept, because nothing checks `ordermin` yet.

- [ ] **Step 3: Add the helper**

In `trading/positions_manager.py`, near the other module-level helpers:

```python
def _below_ordermin(pair: str, volume: float) -> bool:
    """True when Kraken would reject this size. Unknown ordermin never blocks a trade."""
    ordermin = config.PAIRS.get(pair, {}).get("ordermin")
    return ordermin is not None and volume < ordermin
```

Import `config` from `core` if `positions_manager` does not already import it (it currently imports named constants from `core.config`; add `from core import config`).

Failing open when `ordermin` is unknown matches how `_format_amount` treats unknown decimals: metadata that did not load must not silently change behaviour.

- [ ] **Step 4: Enforce it in `create_position`**

After the existing `volume <= 0` guard:

```python
    if _below_ordermin(pair, volume):
        logging.info(
            f"Cannot create {side.upper()} position: volume {volume:.8f} < Kraken ordermin "
            f"{config.PAIRS[pair]['ordermin']:.8f}"
        )
        return
```

- [ ] **Step 5: Enforce it in `refresh_position`**

After the existing `volume <= 0` drop:

```python
    if _below_ordermin(pair, volume):
        # A latched exit below ordermin can never be placed; the residual is untradeable by definition.
        _drop_position(f"volume {volume:.8f} < Kraken ordermin {config.PAIRS[pair]['ordermin']:.8f}")
        return False
```

- [ ] **Step 6: Run the tests**

Run: `PYTHONPATH=. ./venv/Scripts/python.exe -m pytest tests/unit/trading/test_positions_manager.py -k ordermin -v`
Expected: PASS.

- [ ] **Step 7: Run the full suite and commit**

Run: `PYTHONPATH=. ./venv/Scripts/python.exe -m pytest tests/unit/ -q && ./venv/Scripts/python.exe -m ruff check . && ./venv/Scripts/python.exe -m ruff format --check .`

```bash
git add trading/positions_manager.py tests/unit/trading/test_positions_manager.py
git commit -m "feat(positions): enforce Kraken ordermin when sizing and re-placing"
```

---

### Task 4: A sub-`ordermin` reprice remainder is nothing to place

**Files:**
- Modify: `trading/positions_manager.py` (`reprice_closing_order`, the `remaining <= 0` guard)
- Test: `tests/unit/trading/test_positions_manager.py`

**Interfaces:**
- Consumes: `_below_ordermin` from Task 3.
- Produces: nothing new.

`reprice_closing_order` sizes its replacement from the canceled order's own `vol - vol_exec` and places it directly, without going through `refresh_position`. A remainder above zero but below `ordermin` would be rejected by Kraken on every tick.

- [ ] **Step 1: Write the failing test**

```python
def test_reprice_does_not_place_a_remainder_below_ordermin(monkeypatch) -> None:
    monkeypatch.setitem(config.PAIRS, "XBTEUR", {"ordermin": 0.0001, "pair_decimals": 1})
    placed: list = []
    monkeypatch.setattr(positions_manager, "cancel_order", lambda *a, **k: True)
    monkeypatch.setattr(
        positions_manager,
        "get_order_state",
        lambda *a, **k: OrderState(status=OrderStatus.CANCELED, avg_price=100.0, vol_exec=0.99995, vol=1.0),
    )
    monkeypatch.setattr(positions_manager, "place_limit_order", lambda *a, **k: placed.append(a))
    pos = {"side": "sell", "volume": 1.0, "closing_order_id": "TX1", "closing_price": 90.0}

    result = positions_manager.reprice_closing_order(
        "XBTEUR", pos, OrderState(status=OrderStatus.OPEN, avg_price=90.0, vol_exec=0.0, vol=1.0), {"XBTEUR": 100.0}
    )

    assert placed == []
    assert result is True
```

The remainder is 0.00005 — positive, so the existing `remaining <= 0` guard does not catch it, and below `ordermin`. Check the exact signature of `reprice_closing_order` in the file before writing this; adapt the call if it differs.

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=. ./venv/Scripts/python.exe -m pytest tests/unit/trading/test_positions_manager.py -k below_ordermin -v`
Expected: FAIL — `place_limit_order` was called.

- [ ] **Step 3: Widen the guard**

Change the existing `if remaining <= 0:` branch to `if remaining <= 0 or _below_ordermin(pair, remaining):` and extend its log line to say which of the two it was. Keep the existing `return True` — the previous order is off the book and there is genuinely nothing placeable left, which is the same outcome the zero case already reports.

- [ ] **Step 4: Run the test**

Run: `PYTHONPATH=. ./venv/Scripts/python.exe -m pytest tests/unit/trading/test_positions_manager.py -k below_ordermin -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite and commit**

```bash
git add trading/positions_manager.py tests/unit/trading/test_positions_manager.py
git commit -m "fix(positions): treat a sub-ordermin reprice remainder as unplaceable"
```

---

# Stage B — Record the real fee and net it into `pnl_percent` (spec §2)

### Task 5: Read the fill fee at the Kraken boundary

**Files:**
- Modify: `exchange/kraken.py:122-138` (`OrderState`, `_build_order_state`)
- Test: `tests/unit/exchange/test_kraken.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `OrderState.fee: float`, defaulting to `0.0`. Task 6 reads it. Both `get_order_state` and `find_order_by_cl_ord_id` get it for free — they already share `_build_order_state`.

- [ ] **Step 1: Write the failing test**

```python
def test_build_order_state_reads_the_fee() -> None:
    state = kraken._build_order_state(
        {"status": "closed", "price": "100.0", "vol": "1.0", "vol_exec": "1.0", "fee": "0.26"}
    )

    assert state.fee == 0.26


def test_build_order_state_without_fee_reads_zero() -> None:
    state = kraken._build_order_state({"status": "closed", "price": "100.0", "vol": "1.0", "vol_exec": "1.0"})

    assert state.fee == 0.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=. ./venv/Scripts/python.exe -m pytest tests/unit/exchange/test_kraken.py -k build_order_state -v`
Expected: FAIL — `AttributeError: 'OrderState' object has no attribute 'fee'`.

- [ ] **Step 3: Add the field**

```python
@dataclass(frozen=True)
class OrderState:
    status: OrderStatus
    avg_price: float | None
    vol_exec: float
    # The order's own size. 0.0 when Kraken omits it, so a remainder check fails closed.
    vol: float = 0.0
    # The fee actually charged, in quote currency. 0.0 when Kraken omits it.
    fee: float = 0.0
```

and in `_build_order_state`, add `fee=float(order.get("fee") or 0.0),`.

`fee` is added last with a default so the many existing `OrderState(...)` constructions in tests keep working positionally.

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=. ./venv/Scripts/python.exe -m pytest tests/unit/exchange/test_kraken.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add exchange/kraken.py tests/unit/exchange/test_kraken.py
git commit -m "feat(kraken): expose the fill fee on OrderState"
```

---

### Task 6: `finalize_close` nets the fee into `pnl_percent`

**Files:**
- Modify: `trading/positions_manager.py:164-186` (`finalize_close`)
- Test: `tests/unit/trading/test_positions_manager.py`

**Interfaces:**
- Consumes: `OrderState.fee` from Task 5.
- Produces: `pos["fee_eur"]` (float) alongside the existing `pos["pnl_percent"]`. Task 7 persists it.

**The ordering that matters:** the `CANCELED`-but-fully-executed branch overwrites `pos["volume"]` with `state.vol_exec` *before* the PnL is computed. The fee percentage must be computed from the **final** volume, so it goes after that overwrite, not before.

- [ ] **Step 1: Write the failing tests**

```python
def test_finalize_close_nets_the_fee_into_pnl(monkeypatch) -> None:
    pos = {"side": "sell", "entry_price": 100.0, "volume": 1.0, "closing_order_id": "TX1"}
    state = OrderState(status=OrderStatus.CLOSED, avg_price=110.0, vol_exec=1.0, vol=1.0, fee=2.0)

    assert positions_manager.finalize_close(pos, state) is True
    # gross +10.00%, fee 2.0 EUR on a 100.0 EUR entry notional = 2.00%
    assert pos["pnl_percent"] == 8.0
    assert pos["fee_eur"] == 2.0


def test_finalize_close_without_a_fee_leaves_pnl_gross() -> None:
    pos = {"side": "sell", "entry_price": 100.0, "volume": 1.0, "closing_order_id": "TX1"}
    state = OrderState(status=OrderStatus.CLOSED, avg_price=110.0, vol_exec=1.0, vol=1.0)

    assert positions_manager.finalize_close(pos, state) is True
    assert pos["pnl_percent"] == 10.0
    assert pos["fee_eur"] == 0.0


def test_finalize_close_fee_uses_the_executed_volume_on_a_raced_cancel() -> None:
    pos = {"side": "sell", "entry_price": 100.0, "volume": 1.0, "closing_order_id": "TX1"}
    state = OrderState(status=OrderStatus.CANCELED, avg_price=110.0, vol_exec=2.0, vol=2.0, fee=4.0)

    assert positions_manager.finalize_close(pos, state) is True
    # volume is overwritten to 2.0 first, so the notional is 200.0 and the fee is 2.00%
    assert pos["volume"] == 2.0
    assert pos["pnl_percent"] == 8.0
```

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=. ./venv/Scripts/python.exe -m pytest tests/unit/trading/test_positions_manager.py -k finalize_close_ -v`
Expected: FAIL — `pnl_percent` is 10.0 in the first test and `fee_eur` is missing.

- [ ] **Step 3: Net the fee**

Replace the PnL block at the end of `finalize_close`:

```python
    closing_price = state.avg_price
    entry = pos["entry_price"]
    side = pos["side"]
    pnl = (closing_price - entry) / entry * 100 if side == "sell" else (entry - closing_price) / entry * 100
    # One fee per position: the closing order is the only real exchange order in its life.
    entry_notional = entry * float(pos.get("volume") or 0.0)
    fee_pct = (state.fee / entry_notional * 100) if entry_notional > 0 else 0.0
    pos["closing_price"] = closing_price
    pos["fee_eur"] = state.fee
    pos["pnl_percent"] = round(pnl - fee_pct, 4)
    logging.info(f"💸 Position closed: {pnl - fee_pct:+.2f}% result (fee {state.fee:.2f}€)", to_telegram=True)
    return True
```

The `entry_notional > 0` guard is not defensive padding: `pos["volume"]` can be absent on a malformed state, and a division by zero here would kill the pair block.

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=. ./venv/Scripts/python.exe -m pytest tests/unit/trading/test_positions_manager.py -k finalize_close -v`
Expected: PASS, including the pre-existing `finalize_close` tests (they construct `OrderState` without a fee, so they get `fee=0.0` and keep their gross expectations).

- [ ] **Step 5: Run the full suite and commit**

```bash
git add trading/positions_manager.py tests/unit/trading/test_positions_manager.py
git commit -m "feat(positions): net the real Kraken fee into pnl_percent"
```

---

### Task 7: Persist `fee_eur` on `closed_positions`

**Files:**
- Modify: `core/db/models.py` (`ClosedPosition`, both the columns and `to_dict`)
- Modify: `core/db/positions.py:70-86` (`record_position_closed`)
- Create: `scripts/migrations/versions/20260820_01_closed_position_fee.py`
- Test: `tests/unit/core/` (the DAL test module for positions)

**Interfaces:**
- Consumes: `pos["fee_eur"]` from Task 6.
- Produces: `closed_positions.fee_eur`, `Numeric(20, 10)`, **nullable**.

Nullable is load-bearing: historical rows have no fee and there is no backfill, so `NULL` is how a reader tells a gross row from a net one.

- [ ] **Step 1: Write the failing test**

In the existing positions DAL test module, add a test asserting `record_position_closed` passes `fee_eur` through. Follow the module's existing pattern for capturing the inserted values (find an existing `record_position_closed` test and mirror its setup rather than inventing a new one).

```python
def test_record_position_closed_persists_the_fee(monkeypatch) -> None:
    captured: dict = {}
    # Mirror the capture/monkeypatch setup used by the neighbouring
    # record_position_closed tests in this module.
    ...
    assert captured["values"]["fee_eur"] is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=. ./venv/Scripts/python.exe -m pytest tests/unit/core/ -k record_position_closed -v`
Expected: FAIL — `KeyError: 'fee_eur'`.

- [ ] **Step 3: Add the column to the model**

In `core/db/models.py`, inside `ClosedPosition`, after `pnl_percent`:

```python
    fee_eur = Column(Numeric(20, 10), nullable=True)
```

and in `to_dict`, after the `pnl_percent` entry:

```python
            "fee_eur": float(self.fee_eur) if self.fee_eur is not None else None,
```

- [ ] **Step 4: Persist it in the DAL**

In `record_position_closed`, add to `values` after `"pnl_percent"`:

```python
        "fee_eur": _to_decimal(position_data.get("fee_eur")),
```

`_to_decimal` (not `_to_decimal_required`) because the field is nullable and an older in-flight position dict may not carry it.

- [ ] **Step 5: Write the migration**

Create `scripts/migrations/versions/20260820_01_closed_position_fee.py`:

```python
"""Record the real Kraken fee of each closed position.

Nullable with no backfill: rows written before this migration are gross, rows after are net.

Revision ID: 20260820_01
Revises: 20260817_01
Create Date: 2026-08-20 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260820_01"
down_revision = "20260817_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("closed_positions", sa.Column("fee_eur", sa.Numeric(20, 10), nullable=True))


def downgrade() -> None:
    op.drop_column("closed_positions", "fee_eur")
```

Confirm `20260817_01` is still the head before committing: `./venv/Scripts/python.exe -m alembic heads`. If another migration landed first, set `down_revision` to that one instead.

- [ ] **Step 6: Run the test and the migration**

Run: `PYTHONPATH=. ./venv/Scripts/python.exe -m pytest tests/unit/ -q`
Expected: PASS.

Then verify the migration applies against a real database:
Run: `docker compose -f docker-compose.test.yml run --rm test alembic upgrade head`
Expected: no error; the column exists.

- [ ] **Step 7: Commit**

```bash
git add core/db/models.py core/db/positions.py scripts/migrations/versions/20260820_01_closed_position_fee.py tests/
git commit -m "feat(db): persist the closing fee on closed_positions"
```

---

# Stage C — Cumulative PnL in EUR (spec §3)

### Task 8: Weight the cumulative PnL panel by notional

**Files:**
- Modify: `services/grafana/dashboards/botc.json` (the "Cumulative PnL" panel target)

**Interfaces:**
- Consumes: `pnl_percent` as written by Task 6 (already net of the fee).
- Produces: nothing consumed by later tasks.

**Do not also subtract `fee_eur`.** After Task 6 `pnl_percent` is already net; subtracting the fee again here would double-count it. `fee_eur` exists for attribution — how much went to Kraken — not as a second subtraction.

- [ ] **Step 1: Find the panel**

Run: `grep -n "cumulative_pnl" services/grafana/dashboards/botc.json`
The current SQL is:

```sql
SELECT closed_at AS time, SUM(pnl_percent::float8) OVER (ORDER BY closed_at) AS cumulative_pnl FROM closed_positions WHERE pair = '$pair' UNION ALL SELECT now() AS time, SUM(pnl_percent::float8) AS cumulative_pnl FROM closed_positions WHERE pair = '$pair' HAVING COUNT(*) > 0 ORDER BY time
```

- [ ] **Step 2: Replace both halves of the union**

```sql
SELECT closed_at AS time, SUM(pnl_percent::float8 / 100.0 * entry_price::float8 * volume::float8) OVER (ORDER BY closed_at) AS cumulative_pnl_eur FROM closed_positions WHERE pair = '$pair' UNION ALL SELECT now() AS time, SUM(pnl_percent::float8 / 100.0 * entry_price::float8 * volume::float8) AS cumulative_pnl_eur FROM closed_positions WHERE pair = '$pair' HAVING COUNT(*) > 0 ORDER BY time
```

Both halves must change together — the second is the "carry the line to now" point and would otherwise be in different units from the series it extends.

- [ ] **Step 3: Update the panel's unit and title**

In the same panel object, set the title to `Cumulative PnL (€)` and the field unit to `currencyEUR`. Leave the "PnL per close" panel alone — per trade, a percentage is the comparable figure.

- [ ] **Step 4: Verify the JSON is still valid**

Run: `./venv/Scripts/python.exe -c "import json; json.load(open('services/grafana/dashboards/botc.json')); print('valid')"`
Expected: `valid`.

- [ ] **Step 5: Verify the panel renders**

Run: `docker compose up -d --build grafana` and open `http://localhost:3000`. Confirm the panel shows EUR and that the value matches a hand-computed total for a few known closes:

```sql
SELECT SUM(pnl_percent / 100.0 * entry_price * volume) FROM closed_positions WHERE pair = 'XBTEUR';
```

- [ ] **Step 6: Commit**

```bash
git add services/grafana/dashboards/botc.json
git commit -m "fix(grafana): accumulate PnL in EUR instead of summing percentages"
```

---

# Stage D — Relative-ATR volatility classification (spec §4)

### Task 9: Classify `ATR/close` in both the live path and the engine

**Files:**
- Modify: `trading/parameters_manager.py:50-54` (percentiles), `:111-121` (`get_volatility_level`), `:124-127` (`get_k_stop`)
- Modify: `trading/engine.py:49-58` (`_vol_level_from_atr`), `:67-73` (`lookup_k_stop`), `:204`, `:241`
- Modify: `core/scheduler.py:104`
- Modify: `trading/positions_manager.py:74`, `:106`
- Modify: `core/runtime.py:69-72` and `trading/engine.py:14-21` (field names)
- Test: `tests/unit/trading/test_parameters_manager.py`, `tests/unit/trading/test_engine.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `get_volatility_level(pair: str, atr_val: float, close: float) -> str` and `get_k_stop(pair: str, side: str, atr_val: float, close: float) -> float | None`; `_vol_level_from_atr(atr_val, close, p20, p50, p80, p95) -> str`. Calibration fields rename `atr_p20…atr_p95` → `atr_ratio_p20…atr_ratio_p95`.

**This is the only task that changes live trading behaviour.** Ship it on its own so its effect is legible. It needs no migration: the boundaries live only in `config.PAIRS` and the runtime calibration snapshot, never in the database, so they recompute on the next `calculate_trading_parameters`.

- [ ] **Step 1: Write the failing parity test**

This is the property that matters — the live classifier and the simulator must agree, or backtest stops predicting production:

```python
def test_live_and_engine_classify_identically(monkeypatch) -> None:
    monkeypatch.setitem(
        parameters_manager.PAIRS,
        "XBTEUR",
        {"atr_ratio_p20": 0.001, "atr_ratio_p50": 0.002, "atr_ratio_p80": 0.004, "atr_ratio_p95": 0.008},
    )
    cal = PairCalibration(
        atr_ratio_p20=0.001,
        atr_ratio_p50=0.002,
        atr_ratio_p80=0.004,
        atr_ratio_p95=0.008,
        k_stop_buy={},
        k_stop_sell={},
    )

    for atr, close in [(50.0, 100_000.0), (250.0, 100_000.0), (500.0, 100_000.0), (1000.0, 100_000.0)]:
        assert parameters_manager.get_volatility_level("XBTEUR", atr, close) == engine._vol_level_from_atr(
            atr, close, cal.atr_ratio_p20, cal.atr_ratio_p50, cal.atr_ratio_p80, cal.atr_ratio_p95
        )
```

- [ ] **Step 2: Write the failing scale-invariance test**

```python
def test_same_relative_volatility_classifies_the_same_at_any_price(monkeypatch) -> None:
    monkeypatch.setitem(
        parameters_manager.PAIRS,
        "XBTEUR",
        {"atr_ratio_p20": 0.001, "atr_ratio_p50": 0.002, "atr_ratio_p80": 0.004, "atr_ratio_p95": 0.008},
    )

    # 0.3% of price in both cases: the same market condition at two price levels.
    assert parameters_manager.get_volatility_level("XBTEUR", 30.0, 10_000.0) == parameters_manager.get_volatility_level(
        "XBTEUR", 300.0, 100_000.0
    )
```

This is the bug being fixed: under absolute ATR these two return different levels.

- [ ] **Step 3: Run to verify they fail**

Run: `PYTHONPATH=. ./venv/Scripts/python.exe -m pytest tests/unit/trading/ -k "classify" -v`
Expected: FAIL — `get_volatility_level() takes 2 positional arguments but 3 were given`.

- [ ] **Step 4: Compute the percentiles over the ratio series**

In `calculate_trading_parameters`, replace the four percentile lines:

```python
    atr_ratio = df["atr"] / df["close"]
    PAIRS[pair]["atr_ratio_p20"] = np.percentile(atr_ratio, 20)
    PAIRS[pair]["atr_ratio_p50"] = np.percentile(atr_ratio, 50)
    PAIRS[pair]["atr_ratio_p80"] = np.percentile(atr_ratio, 80)
    PAIRS[pair]["atr_ratio_p95"] = np.percentile(atr_ratio, 95)
```

Update the log line below it: the values are ratios now, not euros, so drop the `€` and `round_price` and format as percentages (`f"{v:.3%}"`). Update the `runtime.update_pair_calibration(...)` and `build_calibration(...)` calls to the new key and argument names.

- [ ] **Step 5: Change the classifiers**

`trading/parameters_manager.py`:

```python
def get_volatility_level(pair: str, atr_val: float, close: float) -> str:
    """Classify ATR relative to price, so the level means the same thing at any price level."""
    ratio = atr_val / close if close else 0.0
    if ratio < PAIRS[pair]["atr_ratio_p20"]:
        return "LL"
    elif ratio < PAIRS[pair]["atr_ratio_p50"]:
        return "LV"
    elif ratio < PAIRS[pair]["atr_ratio_p80"]:
        return "MV"
    elif ratio < PAIRS[pair]["atr_ratio_p95"]:
        return "HV"

    return "HH"


def get_k_stop(pair: str, side: str, atr_val: float, close: float) -> float | None:
    ...
    vol = get_volatility_level(pair, atr_val, close)
```

`trading/engine.py`:

```python
def _vol_level_from_atr(atr_val: float, close: float, p20: float, p50: float, p80: float, p95: float) -> str:
    ratio = atr_val / close if close else 0.0
    if ratio < p20:
        return "LL"
    if ratio < p50:
        return "LV"
    if ratio < p80:
        return "MV"
    if ratio < p95:
        return "HV"
    return "HH"
```

and rename the `PairCalibration` fields to `atr_ratio_p20…atr_ratio_p95`. A float still named `atr_p20` that no longer holds an ATR is a trap for the next reader.

- [ ] **Step 6: Thread the close price to every call site**

- `core/scheduler.py:104` — `get_volatility_level(pair, current_atr, current_price)`; `current_price` is already in scope.
- `trading/positions_manager.py:74` and `:106` — both are inside `calculate_stop_price`/`calculate_activation_distance`, which receive a price. Pass it through; if a helper does not already take one, add the parameter and update its callers.
- `trading/engine.py:204` and `:241` — `_price_of(row, has_close, has_open)` gives the bar's reference price. At `:241`, `price` is computed on the line **after** the `vol` call; move the `price = _price_of(...)` line above it so the level can use it.
- `trading/engine.py:67-73` (`lookup_k_stop`) — add a `close` parameter and pass it through from both of its call sites.

- [ ] **Step 7: Run the tests**

Run: `PYTHONPATH=. ./venv/Scripts/python.exe -m pytest tests/unit/ -q`
Expected: the two new tests PASS. Existing tests constructing `PairCalibration(atr_p20=...)` or calling `get_volatility_level(pair, atr)` FAIL — update each to the new names and signature.

The old `PAIRS[pair]["atr_<n>pct"]` keys appear in exactly four files; renaming them is mechanical but must be complete or a `KeyError` surfaces only at runtime:

```
trading/parameters_manager.py
tests/unit/trading/test_parameters_manager.py
tests/unit/trading/test_parameters_manager_cache.py
tests/unit/optimizer/test_search.py   (atr_50pct only)
```

- [ ] **Step 8: Verify the engine still runs end to end**

Run: `PYTHONPATH=. ./venv/Scripts/python.exe -m pytest tests/unit/trading/test_engine.py tests/unit/trading/test_backtest.py tests/unit/optimizer/ -v`
Expected: PASS. These exercise `simulate_operations` over real frames and will catch a missed call site that the unit tests above do not.

- [ ] **Step 9: Run the full pass and commit**

Run: `PYTHONPATH=. ./venv/Scripts/python.exe -m pytest tests/unit/ -q && ./venv/Scripts/python.exe -m ruff check . && ./venv/Scripts/python.exe -m ruff format --check .`

```bash
git add trading/ core/ tests/
git commit -m "feat(strategy): classify volatility by relative ATR (ATR/close)"
```

- [ ] **Step 10: Record the follow-ups this creates**

Two consequences of this change are invisible from the code and will be lost unless written down. Add both to `docs/BACKLOG.md` under the Auto-Lookback card, or to the optimizer grid study if that branch has merged:

1. The July 2026 search grids were derived under absolute-ATR classification and need re-validation before any config derived under them is deployed.
2. `closed_positions` rows written before this change were classified under the old scheme, so volatility-level comparisons across the cutover are not like-for-like.

---

# Stage E — Documentation (spec §5 and §6)

### Task 10: Document the three unstated strategy facts

**Files:**
- Modify: `docs/trading-strategy.md:62-66` (Position closure), `:123-128` (Constraints and invariants), and a new section

**Interfaces:**
- Consumes: nothing.
- Produces: nothing.

- [ ] **Step 1: Fix the two stale `is_closing_complete` references**

`docs/trading-strategy.md:64` and `:127` describe a function that no longer exists. Replace with `finalize_close`, and correct the behaviour: it does **not** poll `QueryOrders`. It interprets an order the caller has already fetched and already established as terminal; `_drive_closing_order` does the fetching and the status dispatch.

- [ ] **Step 2: Document the MIN_MARGIN guarantee**

Add to the "Activation price" section, after the MIN_MARGIN strategy block:

> Under MIN_MARGIN activation the activation distance is `K_STOP × ATR + MIN_MARGIN × entry_price`, while the stop trails `K_STOP × ATR` behind the best price seen since activation. An activated position therefore cannot exit worse than `MIN_MARGIN × entry_price` away from entry: **MIN_MARGIN is a minimum profit floor on activated trades**, not merely a distance parameter. Under K_ACT activation (`K_ACT × ATR`) no such floor exists — the stop can trail back through the entry price — which is why the two modes are not interchangeable.

- [ ] **Step 3: Document what `pnl_percent` actually measures**

Add to the "Position closure" section:

> `pnl_percent` is **timing alpha, not economic profit**. It measures the execution price against `entry_price` — the price when the rebalance plan was created — so it reports how much better the trailing layer did than rebalancing immediately. `entry_price` is a reference, not a cost basis: nothing was ever bought at it. From the fee change onward it is **net** of the real Kraken fee (recorded in `closed_positions.fee_eur`); rows closed before that are gross, so the series is not homogeneous across that point.

- [ ] **Step 4: Document the re-anchoring trade-off**

Add to the "Recalibration" section:

> After a strong adverse move, a plan re-anchors its activation toward the current price and executes into the first bounce, recording a large negative `pnl_percent` against the original reference. This is deliberate: for a rebalancer, executing late beats never executing. It is also the main source of the worst recorded per-trade numbers, so those are the mechanism working as designed rather than a defect.

- [ ] **Step 5: Verify links and commit**

Run: `./venv/Scripts/python.exe -c "print('manual read-through')"` — then actually read the file top to bottom once; it is short.

```bash
git add docs/trading-strategy.md
git commit -m "docs(strategy): document the MIN_MARGIN floor, pnl_percent semantics and re-anchoring"
```

---

### Task 11: Remove the obsolete claims and the duplication

**Files:**
- Modify: `docs/operations.md:160-193`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing.

Apply the one-owner rule from spec §6: `CLAUDE.md` owns architecture, invariants and the *why*; `trading-strategy.md` owns what the bot decides; `operations.md` owns running and troubleshooting it; `configuration.md` owns the env reference. **Shipped specs under `docs/specs/` are dated records and are not rewritten.**

- [ ] **Step 1: Fix the five wrong claims in `operations.md`**

Each was verified against the current code:

1. §Closing order repricing says the position "keeps the old, now-canceled order id" and `is_closing_complete` clears it next tick. The opposite is true: `reprice_closing_order` **drops** `closing_order_id` and writes a fresh `closing_request_id`, which the next tick resolves via `find_order_by_cl_ord_id`.
2. It promises a Telegram alert `Failed to re-place closing order after cancel.` — that message no longer exists. The path logs `replacement not confirmed after cancel; the next tick will resolve it` and does **not** notify.
3. §Per-pair failure isolation says a failed pair makes the session `failed`. It is `pair_error` (`core/scheduler.py:156`).
4. It says the alert includes `pair errors: <PAIR1>, <PAIR2>`. No alert carries a reason by design; the real text is `⚠️ [PAIR] has failed in N sessions in a row and is not being managed.`
5. The session-overrun alert is missing entirely. Add it: three consecutive sessions whose wall-clock meets or exceeds `SLEEPING_INTERVAL` fire one warning, and one recovery message follows.

- [ ] **Step 2: Remove the ER-thresholds claim from `CLAUDE.md`**

`CLAUDE.md` states that regime ER thresholds are resolved inside `simulate_operations`. Verify once more, then delete the sentence:

Run: `grep -rn "efficiency\|_ER\|er_threshold" trading/`
Expected: no matches — there is no ER logic anywhere in `trading/`.

- [ ] **Step 3: Collapse the duplicated facts to one owner each**

- **Repricing behaviour** — described in `operations.md`, `trading-strategy.md` and `CLAUDE.md`. Keep the operator-facing account in `operations.md`; reduce the other two to a sentence and a link.
- **Invariants** — near-verbatim in `trading-strategy.md` and `CLAUDE.md`. Keep `CLAUDE.md` as the owner; `trading-strategy.md` links to it.
- **Per-pair alerting** — in `operations.md` and `CLAUDE.md`. `CLAUDE.md` keeps the design rationale (why three independent streaks, why no reason is carried); `operations.md` keeps what an operator sees.
- **Test commands** — in `operations.md` and `CLAUDE.md`. Keep `CLAUDE.md`'s (agents read it first); `operations.md` links to it.

- [ ] **Step 4: Compress the Design choices section**

Each entry keeps its decision and a one-line why; the full reasoning stays in the spec it came from, linked. Do not delete a decision because it reads as obvious — the section exists so a reviewer stops re-litigating settled choices.

- [ ] **Step 5: Verify no links broke**

```bash
cd docs && for f in *.md specs/*.md; do d=$(dirname "$f"); grep -o '](\([^)#]*\.md\)' "$f" | sed 's/](//' | while read -r l; do [ -f "$d/$l" ] || echo "BROKEN in $f -> $l"; done; done
```
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md docs/operations.md docs/trading-strategy.md
git commit -m "docs: remove obsolete claims and collapse duplicated documentation"
```

---

## Definition of done

- [ ] `PYTHONPATH=. pytest tests/unit/` passes with coverage ≥ 80%.
- [ ] `ruff check .` and `ruff format --check .` are clean.
- [ ] `docker compose -f docker-compose.test.yml run --rm test alembic upgrade head` applies cleanly.
- [ ] The Grafana cumulative panel reads in EUR and matches a hand-computed total.
- [ ] No markdown link in `docs/` is broken.
- [ ] `docs/BACKLOG.md` records that the optimizer grids need re-validation after Stage D.
- [ ] This plan file is deleted when the card ships, per the backlog convention.
