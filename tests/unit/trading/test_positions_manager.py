from datetime import UTC, datetime
from typing import Any

import pytest

import exchange.kraken as kraken
import trading.positions_manager as positions_manager
from core import config
from core.utils import now_utc
from exchange.kraken import OrderLookup, OrderState, OrderStatus


def _capture_telegram(monkeypatch) -> list[tuple[str, bool]]:
    """Record every log call as (message, to_telegram)."""
    sent: list[tuple[str, bool]] = []
    for level in ("info", "warning", "error"):
        monkeypatch.setattr(
            positions_manager.logging,
            level,
            lambda msg, to_telegram=False: sent.append((msg, to_telegram)),
        )
    return sent


# ============================================================================
# Activation price
# ============================================================================


def test_calculate_activation_price_uses_k_act_when_defined(monkeypatch) -> None:
    monkeypatch.setattr(
        positions_manager,
        "TRADING_PARAMS",
        {"XBTEUR": {"K_ACT": 2.0, "MIN_MARGIN": 0.01}},
    )

    result = positions_manager.calculate_activation_price("XBTEUR", "sell", 100.0, 5.0)

    assert result == 110.0


def test_calculate_activation_price_uses_k_stop_and_margin_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        positions_manager,
        "TRADING_PARAMS",
        {"XBTEUR": {"K_ACT": None, "MIN_MARGIN": 0.1}},
    )
    monkeypatch.setattr(positions_manager, "get_k_stop", lambda pair, side, atr, close: 2.0)

    result = positions_manager.calculate_activation_price("XBTEUR", "buy", 100.0, 5.0)

    # distance = 2*5 + 0.1*100 = 20
    assert result == 80.0


def test_update_activation_price_updates_position_fields(monkeypatch) -> None:
    monkeypatch.setattr(positions_manager, "calculate_activation_price", lambda *_args: 87.5)

    pos = {"side": "buy", "entry_price": 100.0}
    positions_manager.update_activation_price("XBTEUR", pos, atr_val=3.0)

    assert pos["activation_price"] == 87.5
    assert pos["activation_atr"] == 3.0


def test_reanchor_activation_price_returns_false_when_gap_within_expected(monkeypatch) -> None:
    monkeypatch.setattr(positions_manager, "calculate_activation_distance", lambda *_: 20.0)

    pos: dict[str, Any] = {"side": "sell", "activation_price": 110.0, "entry_price": 100.0, "activation_atr": 5.0}
    # gap = 110 - 100 = 10, expected = 20 → gap <= expected → no re-anchor
    result = positions_manager.reanchor_activation_price("XBTEUR", pos, current_price=100.0)

    assert result is False
    assert pos["activation_price"] == 110.0


def test_reanchor_activation_price_updates_sell_when_gap_exceeds_expected(monkeypatch) -> None:
    monkeypatch.setattr(positions_manager, "calculate_activation_distance", lambda *_: 5.0)
    monkeypatch.setattr(positions_manager, "calculate_activation_price", lambda *_: 115.0)

    pos: dict[str, Any] = {"side": "sell", "activation_price": 130.0, "entry_price": 90.0, "activation_atr": 5.0}
    # gap = 130 - 110 = 20, expected = 5 → gap > expected → re-anchor
    result = positions_manager.reanchor_activation_price("XBTEUR", pos, current_price=110.0)

    assert result is True
    assert pos["entry_price"] == 90.0
    assert pos["activation_atr"] == 5.0
    assert pos["activation_price"] == 115.0


def test_reanchor_activation_price_updates_buy_when_gap_exceeds_expected(monkeypatch) -> None:
    monkeypatch.setattr(positions_manager, "calculate_activation_distance", lambda *_: 5.0)
    monkeypatch.setattr(positions_manager, "calculate_activation_price", lambda *_: 85.0)

    pos: dict[str, Any] = {"side": "buy", "activation_price": 70.0, "entry_price": 110.0, "activation_atr": 5.0}
    # gap = 90 - 70 = 20, expected = 5 → gap > expected → re-anchor
    result = positions_manager.reanchor_activation_price("XBTEUR", pos, current_price=90.0)

    assert result is True
    assert pos["entry_price"] == 110.0
    assert pos["activation_atr"] == 5.0
    assert pos["activation_price"] == 85.0


# ============================================================================
# Stop price
# ============================================================================


def test_update_stop_price_updates_position_fields(monkeypatch) -> None:
    monkeypatch.setattr(positions_manager, "get_k_stop", lambda pair, side, atr, close: 1.5)

    pos = {"side": "sell"}
    positions_manager.update_stop_price("XBTEUR", pos, trailing_price=120.0, atr_val=4.0)

    assert pos["trailing_price"] == 120.0
    assert pos["stop_price"] == 114.0
    assert pos["stop_atr"] == 4.0


# ============================================================================
# create_position
# ============================================================================


def test_create_position_builds_state_from_calculated_values(monkeypatch) -> None:
    monkeypatch.setattr(positions_manager, "MIN_VALUE", 10.0)
    monkeypatch.setattr(
        positions_manager,
        "calculate_position",
        lambda pair, balance, prices, state: ("buy", 100.0),
    )
    monkeypatch.setattr(positions_manager, "calculate_activation_price", lambda *args: 85.0)
    _now = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(positions_manager, "now_utc", lambda: _now)

    trailing_state = {}
    positions_manager.create_position(
        pair="XBTEUR",
        balance={"ZEUR": 1000.0},
        last_prices={"XBTEUR": 100.0},
        atr_val=2.0,
        trailing_state=trailing_state,
    )

    assert "XBTEUR" in trailing_state
    assert trailing_state["XBTEUR"]["side"] == "buy"
    assert trailing_state["XBTEUR"]["volume"] == 1.0
    assert trailing_state["XBTEUR"]["entry_price"] == 100.0
    assert trailing_state["XBTEUR"]["activation_price"] == 85.0
    assert trailing_state["XBTEUR"]["created_at"] == _now


def test_create_position_skipped_when_volume_below_ordermin(monkeypatch, caplog) -> None:
    monkeypatch.setitem(config.PAIRS, "XBTEUR", {"ordermin": 0.0001})
    monkeypatch.setattr(positions_manager, "calculate_position", lambda *a, **k: ("sell", 5000.0))
    trailing_state: dict = {}

    positions_manager.create_position("XBTEUR", {}, {"XBTEUR": 100_000_000.0}, 1.0, trailing_state)

    assert trailing_state == {}


# ============================================================================
# Full precision (small-value pairs like USDCEUR)
# ============================================================================


def test_create_position_preserves_full_precision(monkeypatch) -> None:
    monkeypatch.setattr(positions_manager, "MIN_VALUE", 1.0)
    monkeypatch.setattr(
        positions_manager,
        "calculate_position",
        lambda pair, balance, prices, state: ("sell", 100.0),
    )
    monkeypatch.setattr(positions_manager, "calculate_activation_price", lambda *args: 0.99876543)
    monkeypatch.setattr(positions_manager, "now_utc", lambda: datetime(2026, 1, 1, tzinfo=UTC))

    trailing_state: dict[str, Any] = {}
    positions_manager.create_position(
        pair="USDCEUR",
        balance={"ZEUR": 1000.0},
        last_prices={"USDCEUR": 1.0001},
        atr_val=0.00081234,
        trailing_state=trailing_state,
    )

    assert trailing_state["USDCEUR"]["activation_atr"] == 0.00081234
    assert trailing_state["USDCEUR"]["activation_price"] == 0.99876543


def test_update_activation_price_preserves_full_precision(monkeypatch) -> None:
    monkeypatch.setattr(positions_manager, "calculate_activation_price", lambda *_args: 1.0234567)

    pos = {"side": "buy", "entry_price": 1.0001}
    positions_manager.update_activation_price("USDCEUR", pos, atr_val=0.00081234)

    assert pos["activation_price"] == 1.0234567
    assert pos["activation_atr"] == 0.00081234


def test_reanchor_activation_price_preserves_full_precision(monkeypatch) -> None:
    monkeypatch.setattr(positions_manager, "calculate_activation_distance", lambda *_: 0.001)
    monkeypatch.setattr(positions_manager, "calculate_activation_price", lambda *_: 1.0234567)

    pos: dict[str, Any] = {"side": "sell", "activation_price": 1.5, "entry_price": 1.0, "activation_atr": 0.0008}
    result = positions_manager.reanchor_activation_price("USDCEUR", pos, current_price=1.0)

    assert result is True
    assert pos["activation_price"] == 1.0234567


def test_update_stop_price_preserves_full_precision(monkeypatch) -> None:
    monkeypatch.setattr(positions_manager, "get_k_stop", lambda pair, side, atr, close: 0.5)

    pos = {"side": "sell"}
    positions_manager.update_stop_price("USDCEUR", pos, trailing_price=1.23456, atr_val=0.0008)

    # stop = 1.23456 - 0.5 * 0.0008 = 1.23416
    assert pos["stop_price"] == pytest.approx(1.23416)
    assert pos["stop_atr"] == 0.0008


# ============================================================================
# refresh_position
# ============================================================================


def test_refresh_position_updates_volume_and_returns_true(monkeypatch) -> None:
    monkeypatch.setattr(positions_manager, "MIN_VALUE", 10.0)
    monkeypatch.setattr(
        positions_manager,
        "calculate_position",
        lambda pair, balance, prices, state, force_side: (force_side, 200.0),
    )

    pos = {"side": "sell", "volume": 0.0}
    trailing_state = {"XBTEUR": pos}
    result = positions_manager.refresh_position(
        "XBTEUR",
        pos,
        balance={"ZEUR": 1000.0},
        last_prices={"XBTEUR": 100.0},
        trailing_state=trailing_state,
    )

    # volume = 200 / 100 = 2.0
    assert result is True
    assert pos["volume"] == 2.0
    assert "XBTEUR" in trailing_state


def test_refresh_position_drops_position_and_returns_false_when_below_min_value(monkeypatch) -> None:
    monkeypatch.setattr(positions_manager, "MIN_VALUE", 50.0)
    monkeypatch.setattr(
        positions_manager,
        "calculate_position",
        lambda pair, balance, prices, state, force_side: (force_side, 10.0),
    )

    pos = {"side": "buy"}
    trailing_state = {"XBTEUR": pos}
    result = positions_manager.refresh_position(
        "XBTEUR",
        pos,
        balance={"ZEUR": 100.0},
        last_prices={"XBTEUR": 100.0},
        trailing_state=trailing_state,
    )

    assert result is False
    assert "XBTEUR" not in trailing_state


def test_refresh_position_drops_when_remaining_below_ordermin(monkeypatch) -> None:
    monkeypatch.setitem(config.PAIRS, "XBTEUR", {"ordermin": 0.0001})
    monkeypatch.setattr(positions_manager, "calculate_position", lambda *a, **k: ("sell", 5000.0))
    pos = {"side": "sell", "volume": 0.00001, "stop_at": now_utc()}
    trailing_state = {"XBTEUR": pos}

    result = positions_manager.refresh_position("XBTEUR", pos, {}, {"XBTEUR": 100_000_000.0}, trailing_state)

    assert result is False
    assert "XBTEUR" not in trailing_state


# ============================================================================
# close_position
# ============================================================================


def test_close_position_updates_position_on_success(monkeypatch) -> None:
    monkeypatch.setattr(
        positions_manager,
        "place_limit_order",
        lambda *args, **kwargs: kraken.PlacedOrder(txid="ORDER123", price=90.0, volume=1.0),
    )

    pos = {"side": "sell", "entry_price": 100.0, "stop_price": 95.0, "volume": 1.0, "stop_at": "already-latched"}
    prices = {"XBTEUR": 90.0}

    positions_manager.close_position("XBTEUR", pos, prices)

    assert pos["closing_order_id"] == "ORDER123"
    assert pos["closing_price"] == 90.0
    assert "pnl_percent" not in pos


def test_close_position_mints_and_writes_the_request_id_before_placing(monkeypatch) -> None:
    """The ordering is the whole point: if the response is lost but the order
    landed, the persisted state must already describe the attempt."""
    captured: dict[str, Any] = {}

    def fake_place_limit_order(pair, side, price, volume, cl_ord_id=None):
        captured["pos_closing_request_id"] = pos.get("closing_request_id")
        captured["cl_ord_id"] = cl_ord_id
        return kraken.PlacedOrder(txid="ORDER123", price=price, volume=volume)

    monkeypatch.setattr(positions_manager, "place_limit_order", fake_place_limit_order)

    pos = {"side": "sell", "entry_price": 100.0, "stop_price": 95.0, "volume": 1.0, "stop_at": "already-latched"}
    positions_manager.close_position("XBTEUR", pos, {"XBTEUR": 90.0})

    assert captured["cl_ord_id"] is not None
    assert captured["pos_closing_request_id"] == captured["cl_ord_id"]


def test_close_position_leaves_position_untouched_when_place_order_fails(monkeypatch) -> None:
    monkeypatch.setattr(positions_manager, "place_limit_order", lambda *args, **kwargs: None)

    pos = {"side": "sell", "entry_price": 100.0, "stop_price": 95.0, "volume": 1.0, "stop_at": "already-latched"}
    prices = {"XBTEUR": 90.0}

    assert positions_manager.close_position("XBTEUR", pos, prices) is False
    assert "closing_order_id" not in pos
    # The request id and estimated price were written before the call, so a lost response still records the attempt.
    assert pos["closing_request_id"] is not None
    assert pos["closing_price"] == 90.0


def test_close_position_leaves_position_untouched_on_unexpected_error(monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise Exception("kraken exploded")

    monkeypatch.setattr(positions_manager, "place_limit_order", boom)

    pos = {"side": "sell", "entry_price": 100.0, "stop_price": 95.0, "volume": 1.0, "stop_at": "already-latched"}
    prices = {"XBTEUR": 90.0}

    # Must not raise: the scheduler has to keep ticking the other pairs.
    assert positions_manager.close_position("XBTEUR", pos, prices) is False
    assert "closing_order_id" not in pos


def test_close_position_announces_a_successful_placement(monkeypatch) -> None:
    """Announced on every attempt, including the first after the breach."""
    monkeypatch.setattr(
        positions_manager,
        "place_limit_order",
        lambda *args, **kwargs: kraken.PlacedOrder(txid="ORDER123", price=90.0, volume=1.0),
    )
    sent = _capture_telegram(monkeypatch)

    pos = {"side": "sell", "entry_price": 100.0, "stop_price": 95.0, "volume": 1.0, "stop_at": "already-latched"}
    positions_manager.close_position("XBTEUR", pos, {"XBTEUR": 90.0})

    assert [m for m, tg in sent if tg and "Placed closing" in m]


def test_close_position_never_sends_failure_detail_to_telegram(monkeypatch) -> None:
    """Failure detail stays in the logs; the per-pair streak does the alerting."""
    monkeypatch.setattr(positions_manager, "place_limit_order", lambda *args, **kwargs: None)
    sent = _capture_telegram(monkeypatch)

    pos = {"side": "sell", "entry_price": 100.0, "stop_price": 95.0, "volume": 1.0, "stop_at": "already-latched"}
    for _ in range(3):
        assert positions_manager.close_position("XBTEUR", pos, {"XBTEUR": 90.0}) is False

    assert [m for m, tg in sent if tg] == []


def test_close_position_never_sends_a_raised_error_to_telegram(monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise IndexError("list index out of range")

    monkeypatch.setattr(positions_manager, "place_limit_order", boom)
    sent = _capture_telegram(monkeypatch)

    pos = {"side": "sell", "entry_price": 100.0, "stop_price": 95.0, "volume": 1.0, "stop_at": "already-latched"}
    for _ in range(3):
        assert positions_manager.close_position("XBTEUR", pos, {"XBTEUR": 90.0}) is False

    assert [m for m, tg in sent if tg] == []


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


# ============================================================================
# is_open
# ============================================================================


@pytest.mark.parametrize("pos", [None, {}])
def test_is_open_returns_false_for_falsy_pos(pos) -> None:
    assert positions_manager.is_open(pos) is False


def test_is_open_returns_false_when_stop_was_hit() -> None:
    """A position whose stop fired is not open, whether or not an order was placed."""
    assert positions_manager.is_open({"stop_at": "2026-07-26T00:00:00+00:00"}) is False


def test_is_open_returns_false_while_a_closing_order_rests() -> None:
    pos = {"stop_at": "2026-07-26T00:00:00+00:00", "closing_order_id": "ORD001"}
    assert positions_manager.is_open(pos) is False


def test_is_open_returns_true_when_the_stop_has_not_fired() -> None:
    assert positions_manager.is_open({"side": "sell", "activation_price": 100.0}) is True


# ============================================================================
# is_closing
# ============================================================================


@pytest.mark.parametrize("pos", [None, {}])
def test_is_closing_returns_false_for_falsy_pos(pos) -> None:
    assert positions_manager.is_closing(pos) is False


def test_is_closing_returns_false_for_an_open_position() -> None:
    """is_closing is the exact complement of is_open over a stored position."""
    pos = {"side": "sell", "activation_price": 100.0}
    assert positions_manager.is_closing(pos) is False
    assert positions_manager.is_open(pos) is True


def test_is_closing_returns_true_once_the_stop_latched_before_any_order() -> None:
    """The owed exit starts at the latch, not at the placement: a breach whose
    order was rejected or lost must still route into the closing flow."""
    assert positions_manager.is_closing({"stop_at": "2026-07-26T00:00:00+00:00"}) is True


def test_is_closing_returns_true_while_a_closing_order_rests() -> None:
    pos = {"stop_at": "2026-07-26T00:00:00+00:00", "closing_order_id": "ORD001"}
    assert positions_manager.is_closing(pos) is True


# ============================================================================
# finalize_close
# ============================================================================


def test_finalize_close_makes_no_api_call(monkeypatch) -> None:
    monkeypatch.setattr(
        positions_manager, "get_order_state", lambda _: pytest.fail("finalize_close must not query Kraken")
    )

    state = OrderState(status=OrderStatus.CLOSED, avg_price=69099.7, vol_exec=0.01)
    pos = {"closing_order_id": "ORD001", "entry_price": 68000.0, "side": "sell"}
    assert positions_manager.finalize_close(pos, state) is True


def test_finalize_close_returns_true_and_updates_pos_when_order_filled() -> None:
    state = OrderState(status=OrderStatus.CLOSED, avg_price=69099.7, vol_exec=0.01)
    pos = {"closing_order_id": "ORD001", "entry_price": 68000.0, "side": "sell"}
    assert positions_manager.finalize_close(pos, state) is True
    assert pos["closing_price"] == 69099.7
    assert pos["pnl_percent"] == round((69099.7 - 68000.0) / 68000.0 * 100, 4)


def test_finalize_close_pnl_for_buy_side() -> None:
    state = OrderState(status=OrderStatus.CLOSED, avg_price=67000.0, vol_exec=0.01)
    pos = {"closing_order_id": "ORD001", "entry_price": 68000.0, "side": "buy"}
    assert positions_manager.finalize_close(pos, state) is True
    assert pos["pnl_percent"] == round((68000.0 - 67000.0) / 68000.0 * 100, 4)


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


def test_finalize_close_finalizes_a_terminal_order_that_was_fully_executed() -> None:
    """A cancel can race a complete fill: Kraken labels the order `canceled` but
    nothing is left to manage. That is a finished trade — finalize it instead of
    resuming management, which would lose the trade from the PnL history."""
    state = OrderState(status=OrderStatus.CANCELED, avg_price=69099.7, vol=0.01, vol_exec=0.01)
    pos = {"side": "sell", "entry_price": 68000.0, "volume": 0.0123, "closing_order_id": "ORD001"}
    assert positions_manager.finalize_close(pos, state) is True
    assert pos["closing_price"] == 69099.7
    assert pos["pnl_percent"] == round((69099.7 - 68000.0) / 68000.0 * 100, 4)
    # Recorded against what actually traded, not our own stale bookkeeping.
    assert pos["volume"] == 0.01


def test_finalize_close_announces_a_finalized_terminal_order(monkeypatch) -> None:
    captured: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        positions_manager.logging, "warning", lambda msg, to_telegram=False: captured.append((msg, to_telegram))
    )

    state = OrderState(status=OrderStatus.CANCELED, avg_price=69099.7, vol=0.01, vol_exec=0.01)
    pos = {"side": "sell", "entry_price": 68000.0, "volume": 0.01, "closing_order_id": "ORD001"}
    positions_manager.finalize_close(pos, state)

    assert len(captured) == 1
    msg, to_telegram = captured[0]
    assert "ORD001" in msg
    assert "canceled" in msg
    assert to_telegram is True


def test_finalize_close_returns_false_when_terminal_order_left_a_remainder() -> None:
    """A partial fill still leaves a position to manage — only a fully executed
    order is finalizable. Clearing the dead fields is _drive_closing_order's job,
    so finalize_close on its own leaves them untouched."""
    state = OrderState(status=OrderStatus.CANCELED, avg_price=69099.7, vol=0.02, vol_exec=0.01)
    pos = {"side": "sell", "entry_price": 68000.0, "volume": 0.02, "closing_order_id": "ORD001"}
    assert positions_manager.finalize_close(pos, state) is False
    assert pos["closing_order_id"] == "ORD001"
    assert "pnl_percent" not in pos


def test_finalize_close_does_not_finalize_fully_executed_order_without_usable_price() -> None:
    state = OrderState(status=OrderStatus.CANCELED, avg_price=0.0, vol=0.01, vol_exec=0.01)
    pos = {"side": "sell", "entry_price": 68000.0, "volume": 0.01, "closing_order_id": "ORD001"}
    assert positions_manager.finalize_close(pos, state) is False
    assert pos["closing_order_id"] == "ORD001"
    assert "pnl_percent" not in pos


@pytest.mark.parametrize(
    "state",
    [
        OrderState(status=OrderStatus.CLOSED, avg_price=0.0, vol_exec=0.0),
        OrderState(status=OrderStatus.CLOSED, avg_price=None, vol_exec=0.0),
    ],
)
def test_finalize_close_returns_false_on_any_unfinalizable_terminal_state(state) -> None:
    pos = {"side": "sell", "entry_price": 68000.0, "closing_order_id": "ORD001", "closing_price": 68500.0}
    assert positions_manager.finalize_close(pos, state) is False
    assert pos["closing_order_id"] == "ORD001"
    assert pos["closing_price"] == 68500.0
    assert "pnl_percent" not in pos


# ============================================================================
# _drive_closing_order
# ============================================================================


def test_drive_closing_order_returns_pending_when_state_is_none() -> None:
    """`state is None` mirrors an API error from get_order_state: could not ask."""
    pos = {"closing_order_id": "ORD001"}
    outcome = positions_manager._drive_closing_order("XBTEUR", pos, None, {"XBTEUR": 100.0})
    assert outcome is positions_manager.ClosingState.PENDING
    assert pos["closing_order_id"] == "ORD001"


def test_drive_closing_order_returns_pending_while_order_is_pending() -> None:
    state = OrderState(status=OrderStatus.PENDING, avg_price=None, vol_exec=0.0)
    pos = {"closing_order_id": "ORD001"}
    outcome = positions_manager._drive_closing_order("XBTEUR", pos, state, {"XBTEUR": 100.0})
    assert outcome is positions_manager.ClosingState.PENDING
    assert pos["closing_order_id"] == "ORD001"


@pytest.mark.parametrize("status", [OrderStatus.NOT_FOUND, OrderStatus.UNKNOWN])
def test_drive_closing_order_reports_unmanaged_when_kraken_cannot_resolve_the_order(monkeypatch, status) -> None:
    """The freeze this closes: an order that stops resolving used to look like a
    resting one, so the pair sat latched forever with no alert. Nothing is touched
    (re-placing blind risks two live exits) but the pair is reported unmanaged."""
    sent = _capture_telegram(monkeypatch)
    state = OrderState(status=status, avg_price=68200.0, vol=0.5, vol_exec=0.5)
    pos = {"side": "sell", "entry_price": 68000.0, "closing_order_id": "ORD001", "closing_price": 68500.0}

    outcome = positions_manager._drive_closing_order("XBTEUR", pos, state, {"XBTEUR": 67000.0})

    assert outcome is positions_manager.ClosingState.UNMANAGED
    assert pos["closing_order_id"] == "ORD001"
    assert pos["closing_price"] == 68500.0
    assert len(sent) == 1
    msg, to_telegram = sent[0]
    assert "ORD001" in msg
    assert str(status) in msg
    # Logged only: the per-pair failure streak owns the alerting.
    assert to_telegram is False


def test_drive_closing_order_dispatches_open_to_reprice(monkeypatch) -> None:
    state = OrderState(status=OrderStatus.OPEN, avg_price=None, vol_exec=0.0)
    calls: list = []
    monkeypatch.setattr(positions_manager, "reprice_closing_order", lambda *a: calls.append(a) or True)

    pos = {"closing_order_id": "ORD001"}
    outcome = positions_manager._drive_closing_order("XBTEUR", pos, state, {"XBTEUR": 105.0})

    assert outcome is positions_manager.ClosingState.PENDING
    assert calls == [("XBTEUR", pos, state, {"XBTEUR": 105.0})]


def test_drive_closing_order_reports_unmanaged_when_reprice_fails(monkeypatch) -> None:
    state = OrderState(status=OrderStatus.OPEN, avg_price=None, vol_exec=0.0)
    monkeypatch.setattr(positions_manager, "reprice_closing_order", lambda *a: False)

    pos = {"closing_order_id": "ORD001"}
    outcome = positions_manager._drive_closing_order("XBTEUR", pos, state, {"XBTEUR": 105.0})

    assert outcome is positions_manager.ClosingState.UNMANAGED


def test_drive_closing_order_returns_filled_when_finalize_close_succeeds(monkeypatch) -> None:
    state = OrderState(status=OrderStatus.CLOSED, avg_price=69099.7, vol_exec=0.01)
    monkeypatch.setattr(positions_manager, "finalize_close", lambda *a: True)

    pos = {"closing_order_id": "ORD001"}
    outcome = positions_manager._drive_closing_order("XBTEUR", pos, state, {"XBTEUR": 105.0})

    assert outcome is positions_manager.ClosingState.FILLED


def test_drive_closing_order_clears_fields_and_returns_none_on_terminal_unusable_order(monkeypatch) -> None:
    """A terminal status that cannot be finalized clears the dead order's fields
    and returns None, so the caller falls through to re-place on the same tick."""
    state = OrderState(status=OrderStatus.CANCELED, avg_price=0.0, vol_exec=0.0)
    monkeypatch.setattr(positions_manager, "finalize_close", lambda *a: False)

    pos = {
        "side": "sell",
        "entry_price": 68000.0,
        "closing_order_id": "ORD001",
        "closing_request_id": "some-request-id",
        "closing_price": 68500.0,
        "stop_at": "2026-07-26T00:00:00+00:00",
    }
    outcome = positions_manager._drive_closing_order("XBTEUR", pos, state, {"XBTEUR": 67000.0})

    assert outcome is None
    assert "closing_order_id" not in pos
    assert "closing_request_id" not in pos
    assert "closing_price" not in pos
    # The exit is still owed: only the dead order's own fields are cleared.
    assert pos["stop_at"] == "2026-07-26T00:00:00+00:00"


def test_drive_closing_order_dead_order_alerts_with_the_order_id_and_status(monkeypatch) -> None:
    """The txid is the only handle an operator has to check this order at Kraken,
    so it must survive into the message — the fields are cleared right after."""
    state = OrderState(status=OrderStatus.CANCELED, avg_price=0.0, vol_exec=0.0)
    monkeypatch.setattr(positions_manager, "finalize_close", lambda *a: False)
    captured: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        positions_manager.logging, "warning", lambda msg, to_telegram=False: captured.append((msg, to_telegram))
    )

    pos = {"side": "sell", "closing_order_id": "ORD001", "stop_at": "2026-07-26T00:00:00+00:00"}
    positions_manager._drive_closing_order("XBTEUR", pos, state, {"XBTEUR": 67000.0})

    assert len(captured) == 1
    msg, to_telegram = captured[0]
    assert "ORD001" in msg
    assert "canceled" in msg
    assert to_telegram is False


# ============================================================================
# reprice_closing_order
# ============================================================================


def test_reprice_closing_order_reprices_on_price_move(monkeypatch) -> None:
    _requested_at = datetime(2026, 1, 2, 11, 0, tzinfo=UTC)
    monkeypatch.setattr(positions_manager, "now_utc", lambda: datetime(2026, 1, 2, 12, 0, tzinfo=UTC))
    get_order_state_calls: list[str] = []

    def _get_order_state(order_id):
        get_order_state_calls.append(order_id)
        return OrderState(status=OrderStatus.CANCELED, avg_price=None, vol=0.5, vol_exec=0.0)  # post-cancel re-query

    monkeypatch.setattr(positions_manager, "get_order_state", _get_order_state)
    cancel_calls: list[str] = []
    monkeypatch.setattr(positions_manager, "cancel_order", lambda order_id: cancel_calls.append(order_id) or True)
    place_calls: list[tuple] = []
    monkeypatch.setattr(
        positions_manager,
        "place_limit_order",
        lambda pair, side, price, volume, cl_ord_id=None: (
            place_calls.append((pair, side, price, volume, cl_ord_id))
            or kraken.PlacedOrder(txid="NEWORDER1", price=price, volume=volume)
        ),
    )

    state = OrderState(status=OrderStatus.OPEN, avg_price=None, vol_exec=0.0)
    pos = {
        "side": "sell",
        "volume": 0.5,
        "closing_order_id": "OLDORDER",
        "closing_price": 100.0,
        "stop_at": _requested_at,
    }
    assert positions_manager.reprice_closing_order("XBTEUR", pos, state, last_prices={"XBTEUR": 105.0}) is True

    # Exactly the post-cancel re-query: the pre-cancel query is the caller's job now.
    assert get_order_state_calls == ["OLDORDER"]
    assert cancel_calls == ["OLDORDER"]
    assert [c[:4] for c in place_calls] == [("XBTEUR", "sell", 105.0, 0.5)]
    assert pos["closing_order_id"] == "NEWORDER1"
    assert pos["closing_price"] == 105.0
    # A reprice must not overwrite when the stop was hit.
    assert pos["stop_at"] == _requested_at


def test_reprice_closing_order_mints_a_new_id_for_the_replacement(monkeypatch) -> None:
    """The replacement is a new attempt, so it gets its own id — never the
    original request's — and it must be on `pos` before the call, same as
    `close_position`."""
    monkeypatch.setattr(
        positions_manager,
        "get_order_state",
        lambda _: OrderState(status=OrderStatus.CANCELED, avg_price=None, vol=0.5, vol_exec=0.0),
    )
    monkeypatch.setattr(positions_manager, "cancel_order", lambda _order_id: True)

    captured: dict[str, Any] = {}

    def fake_place_limit_order(pair, side, price, volume, cl_ord_id=None):
        captured["pos_closing_request_id"] = pos.get("closing_request_id")
        captured["cl_ord_id"] = cl_ord_id
        return kraken.PlacedOrder(txid="NEWORDER1", price=price, volume=volume)

    monkeypatch.setattr(positions_manager, "place_limit_order", fake_place_limit_order)

    state = OrderState(status=OrderStatus.OPEN, avg_price=None, vol_exec=0.0)
    pos = {
        "side": "sell",
        "volume": 0.5,
        "closing_order_id": "OLDORDER",
        "closing_price": 100.0,
        "closing_request_id": "original-request-id",
    }
    assert positions_manager.reprice_closing_order("XBTEUR", pos, state, last_prices={"XBTEUR": 105.0}) is True

    assert captured["cl_ord_id"] is not None
    assert captured["cl_ord_id"] != "original-request-id"
    assert captured["pos_closing_request_id"] == captured["cl_ord_id"]


def test_reprice_closing_order_skips_when_partially_filled(monkeypatch) -> None:
    monkeypatch.setattr(
        positions_manager, "cancel_order", lambda _order_id: pytest.fail("must not cancel a partial fill")
    )
    monkeypatch.setattr(
        positions_manager, "place_limit_order", lambda *a, **k: pytest.fail("must not place a new order")
    )

    state = OrderState(status=OrderStatus.OPEN, avg_price=100.0, vol_exec=0.2)
    pos = {"side": "sell", "volume": 0.5, "closing_order_id": "OLDORDER", "closing_price": 100.0}
    # The order still rests and is executing: nothing was lost, so not a failure.
    assert positions_manager.reprice_closing_order("XBTEUR", pos, state, last_prices={"XBTEUR": 105.0}) is True

    assert pos["closing_order_id"] == "OLDORDER"
    assert pos["closing_price"] == 100.0


def test_reprice_closing_order_skips_when_formatted_price_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(
        positions_manager, "cancel_order", lambda _order_id: pytest.fail("must not cancel an unchanged price")
    )
    monkeypatch.setattr(
        positions_manager, "place_limit_order", lambda *a, **k: pytest.fail("must not place a new order")
    )

    # No pair metadata loaded -> round_price falls back to 2 decimals; both prices
    # round to the same 100.0 limit, so re-placing would only lose queue priority.
    state = OrderState(status=OrderStatus.OPEN, avg_price=None, vol_exec=0.0)
    pos = {"side": "sell", "volume": 0.5, "closing_order_id": "OLDORDER", "closing_price": 100.001}
    assert positions_manager.reprice_closing_order("XBTEUR", pos, state, last_prices={"XBTEUR": 100.004}) is True

    assert pos["closing_order_id"] == "OLDORDER"
    assert pos["closing_price"] == 100.001


def test_reprice_closing_order_noop_when_cancel_fails(monkeypatch) -> None:
    monkeypatch.setattr(positions_manager, "cancel_order", lambda _order_id: False)
    monkeypatch.setattr(
        positions_manager, "place_limit_order", lambda *a, **k: pytest.fail("must not place a new order")
    )

    state = OrderState(status=OrderStatus.OPEN, avg_price=None, vol_exec=0.0)
    pos = {"side": "sell", "volume": 0.5, "closing_order_id": "OLDORDER", "closing_price": 100.0}
    # The cancel was not confirmed, so the order most likely still rests: not a failure.
    assert positions_manager.reprice_closing_order("XBTEUR", pos, state, last_prices={"XBTEUR": 105.0}) is True

    assert pos["closing_order_id"] == "OLDORDER"
    assert pos["closing_price"] == 100.0


def test_reprice_closing_order_noop_when_new_placement_fails_after_cancel(monkeypatch) -> None:
    """The response for the replacement itself is lost. `closing_order_id` is
    dropped (the canceled txid carries no more information) and the position is
    left in the unconfirmed sub-state, which `manage_close_position`'s branch 2
    resolves next tick — recoverable, unlike a permanently unmanaged pair."""
    monkeypatch.setattr(
        positions_manager,
        "get_order_state",
        lambda _: OrderState(status=OrderStatus.CANCELED, avg_price=None, vol=0.5, vol_exec=0.0),
    )
    monkeypatch.setattr(positions_manager, "cancel_order", lambda _order_id: True)
    monkeypatch.setattr(positions_manager, "place_limit_order", lambda *a, **k: None)
    captured: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        positions_manager.logging, "error", lambda msg, to_telegram=False: captured.append((msg, to_telegram))
    )

    state = OrderState(status=OrderStatus.OPEN, avg_price=None, vol_exec=0.0)
    pos = {"side": "sell", "volume": 0.5, "closing_order_id": "OLDORDER", "closing_price": 100.0}
    assert positions_manager.reprice_closing_order("XBTEUR", pos, state, last_prices={"XBTEUR": 105.0}) is False

    assert "closing_order_id" not in pos
    assert pos["closing_price"] == 105.0
    assert pos["volume"] == 0.5
    assert pos["closing_request_id"] is not None
    assert len(captured) == 1
    msg, to_telegram = captured[0]
    assert "not confirmed" in msg.lower()
    assert to_telegram is False


def test_reprice_closing_order_sizes_replacement_to_remainder_on_fill_in_cancel_window(monkeypatch) -> None:
    """A fill landing between the pre-cancel check and the cancel call still leaves
    a cancellable remainder. The post-cancel re-query must catch it so the
    replacement is sized at the order's own vol - vol_exec, not pos["volume"]."""
    monkeypatch.setattr(
        positions_manager,
        "get_order_state",
        lambda _: OrderState(status=OrderStatus.CANCELED, avg_price=100.0, vol=0.5, vol_exec=0.2),
    )
    monkeypatch.setattr(positions_manager, "cancel_order", lambda _order_id: True)
    place_calls: list[tuple] = []
    monkeypatch.setattr(
        positions_manager,
        "place_limit_order",
        lambda pair, side, price, volume, cl_ord_id=None: (
            place_calls.append((pair, side, price, volume))
            or kraken.PlacedOrder(txid="NEWORDER2", price=price, volume=volume)
        ),
    )

    state = OrderState(status=OrderStatus.OPEN, avg_price=None, vol_exec=0.0)
    pos = {"side": "sell", "volume": 0.5, "closing_order_id": "OLDORDER", "closing_price": 100.0}
    assert positions_manager.reprice_closing_order("XBTEUR", pos, state, last_prices={"XBTEUR": 105.0}) is True

    assert place_calls == [("XBTEUR", "sell", 105.0, pytest.approx(0.3))]
    assert pos["volume"] == pytest.approx(0.3)
    assert pos["closing_order_id"] == "NEWORDER2"
    assert pos["closing_price"] == 105.0


def test_reprice_closing_order_noop_when_post_cancel_requery_fails(monkeypatch) -> None:
    """If the post-cancel get_order_state call errors, the executed amount is
    unknown: placing a replacement could over-sell. Leave closing_order_id set
    to the canceled id so the next tick's terminal-status branch self-heals.
    The cancel was confirmed, so the exit is gone with no replacement: False."""
    monkeypatch.setattr(positions_manager, "get_order_state", lambda _: None)
    monkeypatch.setattr(positions_manager, "cancel_order", lambda _order_id: True)
    monkeypatch.setattr(
        positions_manager, "place_limit_order", lambda *a, **k: pytest.fail("must not place a new order")
    )

    state = OrderState(status=OrderStatus.OPEN, avg_price=None, vol_exec=0.0)
    pos = {"side": "sell", "volume": 0.5, "closing_order_id": "OLDORDER", "closing_price": 100.0}
    assert positions_manager.reprice_closing_order("XBTEUR", pos, state, last_prices={"XBTEUR": 105.0}) is False

    assert pos["closing_order_id"] == "OLDORDER"
    assert pos["closing_price"] == 100.0
    assert pos["volume"] == 0.5


@pytest.mark.parametrize("status", [OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.NOT_FOUND, OrderStatus.UNKNOWN])
def test_reprice_closing_order_noop_when_post_cancel_status_is_not_terminal(monkeypatch, status) -> None:
    """A confirmed cancel does not guarantee the very next QueryOrders already
    reflects the final state. A non-terminal status means the executed volume is
    not definitive, and sizing a replacement from it would over-sell — exactly the
    bug the post-cancel re-query exists to prevent."""
    monkeypatch.setattr(
        positions_manager, "get_order_state", lambda _: OrderState(status=status, avg_price=None, vol_exec=0.0)
    )
    monkeypatch.setattr(positions_manager, "cancel_order", lambda _order_id: True)
    monkeypatch.setattr(
        positions_manager, "place_limit_order", lambda *a, **k: pytest.fail("must not place a new order")
    )

    state = OrderState(status=OrderStatus.OPEN, avg_price=None, vol_exec=0.0)
    pos = {"side": "sell", "volume": 0.5, "closing_order_id": "OLDORDER", "closing_price": 100.0}
    # Confirmed cancel, no replacement placed: the pair is latched and unmanaged.
    assert positions_manager.reprice_closing_order("XBTEUR", pos, state, last_prices={"XBTEUR": 105.0}) is False

    assert pos["closing_order_id"] == "OLDORDER"
    assert pos["closing_price"] == 100.0
    assert pos["volume"] == 0.5


def test_reprice_closing_order_alert_names_the_unconfirmed_status(monkeypatch) -> None:
    monkeypatch.setattr(
        positions_manager,
        "get_order_state",
        lambda _: OrderState(status=OrderStatus.OPEN, avg_price=None, vol_exec=0.0),
    )
    monkeypatch.setattr(positions_manager, "cancel_order", lambda _order_id: True)
    monkeypatch.setattr(positions_manager, "place_limit_order", lambda *a, **k: None)
    captured: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        positions_manager.logging, "warning", lambda msg, to_telegram=False: captured.append((msg, to_telegram))
    )

    state = OrderState(status=OrderStatus.OPEN, avg_price=None, vol_exec=0.0)
    pos = {"side": "sell", "volume": 0.5, "closing_order_id": "OLDORDER", "closing_price": 100.0}
    assert positions_manager.reprice_closing_order("XBTEUR", pos, state, last_prices={"XBTEUR": 105.0}) is False

    assert len(captured) == 1
    msg, to_telegram = captured[0]
    assert "OLDORDER" in msg
    assert "open" in msg
    assert to_telegram is False


def test_reprice_closing_order_noop_when_remaining_is_not_positive(monkeypatch) -> None:
    """The canceled order turns out to have consumed the whole position (or more,
    from stale state): there is nothing left to replace, so nothing is owed and
    branch 1 finalizes the trade next tick — True, not a failure."""
    monkeypatch.setattr(
        positions_manager,
        "get_order_state",
        lambda _: OrderState(status=OrderStatus.CANCELED, avg_price=100.0, vol_exec=0.5),
    )
    monkeypatch.setattr(positions_manager, "cancel_order", lambda _order_id: True)
    monkeypatch.setattr(
        positions_manager, "place_limit_order", lambda *a, **k: pytest.fail("must not place a new order")
    )

    state = OrderState(status=OrderStatus.OPEN, avg_price=None, vol_exec=0.0)
    pos = {"side": "sell", "volume": 0.5, "closing_order_id": "OLDORDER", "closing_price": 100.0}
    assert positions_manager.reprice_closing_order("XBTEUR", pos, state, last_prices={"XBTEUR": 105.0}) is True

    assert pos["closing_order_id"] == "OLDORDER"
    assert pos["closing_price"] == 100.0
    assert pos["volume"] == 0.5


def test_reprice_closing_order_sizes_remainder_from_the_order_not_pos_volume(monkeypatch) -> None:
    """`place_limit_order` rounds to `lot_decimals` before sending, so
    `pos["volume"]` can drift from the order's own `vol` by up to one lot tick.
    An order fully executed against its own `vol` (vol_exec == vol) must leave no
    remainder, even when `pos["volume"]` is stale and larger — sizing from
    `pos["volume"]` would place a dust-sized (or here, oversized) replacement
    instead of letting branch 1 finalize the completed trade next tick."""
    monkeypatch.setattr(
        positions_manager,
        "get_order_state",
        lambda _: OrderState(status=OrderStatus.CANCELED, avg_price=100.0, vol=0.01, vol_exec=0.01),
    )
    monkeypatch.setattr(positions_manager, "cancel_order", lambda _order_id: True)
    monkeypatch.setattr(
        positions_manager, "place_limit_order", lambda *a, **k: pytest.fail("must not place a new order")
    )

    state = OrderState(status=OrderStatus.OPEN, avg_price=None, vol_exec=0.0)
    # pos["volume"] has drifted well above the order's own vol.
    pos = {"side": "sell", "volume": 0.5, "closing_order_id": "OLDORDER", "closing_price": 100.0}
    assert positions_manager.reprice_closing_order("XBTEUR", pos, state, last_prices={"XBTEUR": 105.0}) is True

    assert pos["closing_order_id"] == "OLDORDER"
    assert pos["volume"] == 0.5


def test_reprice_closing_order_places_nothing_when_kraken_omits_the_orders_vol(monkeypatch) -> None:
    """`vol` reads 0.0 when Kraken omits it (see OrderState), so the remainder
    must fail closed rather than sizing from pos["volume"]."""
    monkeypatch.setattr(
        positions_manager,
        "get_order_state",
        lambda _: OrderState(status=OrderStatus.CANCELED, avg_price=100.0, vol_exec=0.2),  # vol omitted -> 0.0
    )
    monkeypatch.setattr(positions_manager, "cancel_order", lambda _order_id: True)
    monkeypatch.setattr(
        positions_manager, "place_limit_order", lambda *a, **k: pytest.fail("must not place a new order")
    )

    state = OrderState(status=OrderStatus.OPEN, avg_price=None, vol_exec=0.0)
    pos = {"side": "sell", "volume": 0.5, "closing_order_id": "OLDORDER", "closing_price": 100.0}
    assert positions_manager.reprice_closing_order("XBTEUR", pos, state, last_prices={"XBTEUR": 105.0}) is True

    assert pos["closing_order_id"] == "OLDORDER"
    assert pos["volume"] == 0.5


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


# ============================================================================
# tick_position
# ============================================================================


def test_tick_position_returns_early_when_refresh_fails(monkeypatch) -> None:
    monkeypatch.setattr(positions_manager, "refresh_position", lambda *_: False)
    monkeypatch.setattr(positions_manager, "ATR_DESV_LIMIT", 0.2)

    pos: dict[str, Any] = {"side": "sell", "activation_atr": 5.0, "activation_price": 110.0}
    trailing_state: dict[str, Any] = {"XBTEUR": pos}
    positions_manager.tick_position(
        "XBTEUR", pos, balance={}, last_prices={"XBTEUR": 100.0}, atr_val=5.0, trailing_state=trailing_state
    )

    assert "activated_at" not in pos


def test_tick_position_recalibrates_activation_when_atr_out_of_range(monkeypatch) -> None:
    monkeypatch.setattr(positions_manager, "refresh_position", lambda *_: True)
    monkeypatch.setattr(positions_manager, "ATR_DESV_LIMIT", 0.1)
    monkeypatch.setattr(positions_manager, "reanchor_activation_price", lambda *_: False)

    def fake_update_activation(pair, pos, atr) -> None:
        pos["activation_price"] = 80.0
        pos["activation_atr"] = atr

    monkeypatch.setattr(positions_manager, "update_activation_price", fake_update_activation)

    # activation_atr=2.0, current atr=5.0: 2.0 < 5.0*(1-0.1)=4.5 → out of range
    pos: dict[str, Any] = {"side": "buy", "activation_atr": 2.0, "activation_price": 85.0}
    trailing_state: dict[str, Any] = {"XBTEUR": pos}
    positions_manager.tick_position(
        "XBTEUR", pos, balance={}, last_prices={"XBTEUR": 90.0}, atr_val=5.0, trailing_state=trailing_state
    )

    assert pos["activation_price"] == 80.0
    assert pos["activation_atr"] == 5.0


def test_tick_position_recalibrates_then_reanchors_when_both_conditions_met(monkeypatch) -> None:
    monkeypatch.setattr(positions_manager, "refresh_position", lambda *_: True)
    monkeypatch.setattr(positions_manager, "ATR_DESV_LIMIT", 0.1)

    update_order: list[str] = []

    def fake_update_activation(pair, pos, atr) -> None:
        update_order.append("atr_recalib")
        pos["activation_price"] = 88.0
        pos["activation_atr"] = atr

    monkeypatch.setattr(positions_manager, "update_activation_price", fake_update_activation)

    def fake_reanchor(pair, pos, current_price) -> bool:
        update_order.append("reanchor")
        pos["activation_price"] = 92.0
        return True

    monkeypatch.setattr(positions_manager, "reanchor_activation_price", fake_reanchor)

    # activation_atr=2.0, current atr=5.0: out of range → recalib fires; reanchor also fires
    # current_price=80 stays below final activation_price=92 (sell) so activation does not trigger
    pos: dict[str, Any] = {"side": "sell", "activation_atr": 2.0, "activation_price": 130.0}
    trailing_state: dict[str, Any] = {"XBTEUR": pos}
    positions_manager.tick_position(
        "XBTEUR", pos, balance={}, last_prices={"XBTEUR": 80.0}, atr_val=5.0, trailing_state=trailing_state
    )

    assert update_order == ["atr_recalib", "reanchor"]
    assert pos["activation_price"] == 92.0
    assert pos["activation_atr"] == 5.0


def test_tick_position_activates_sell_when_price_reaches_activation(monkeypatch) -> None:
    _now = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(positions_manager, "refresh_position", lambda *_: True)
    monkeypatch.setattr(positions_manager, "ATR_DESV_LIMIT", 0.2)
    monkeypatch.setattr(positions_manager, "reanchor_activation_price", lambda *_: False)
    monkeypatch.setattr(
        positions_manager,
        "update_stop_price",
        lambda pair, pos, price, atr: pos.update({"trailing_price": price, "stop_price": price - 5}),
    )
    monkeypatch.setattr(positions_manager, "now_utc", lambda: _now)

    # ATR in range (activation_atr=5.0 within [4.0, 6.0]); price meets activation threshold
    pos: dict[str, Any] = {"side": "sell", "activation_atr": 5.0, "activation_price": 100.0}
    trailing_state: dict[str, Any] = {"XBTEUR": pos}
    positions_manager.tick_position(
        "XBTEUR", pos, balance={}, last_prices={"XBTEUR": 100.0}, atr_val=5.0, trailing_state=trailing_state
    )

    assert pos["activated_at"] == _now
    assert pos["trailing_price"] == 100.0


def test_tick_position_closes_buy_when_stop_hit(monkeypatch) -> None:
    monkeypatch.setattr(positions_manager, "refresh_position", lambda *_: True)
    monkeypatch.setattr(positions_manager, "ATR_DESV_LIMIT", 0.2)

    closed: list[str] = []
    monkeypatch.setattr(
        positions_manager,
        "close_position",
        lambda pair, pos, prices: closed.append(pair),
    )

    # buy: close when current_price >= stop_price; stop_atr in range
    pos: dict[str, Any] = {"side": "buy", "volume": 1.0, "trailing_price": 80.0, "stop_price": 95.0, "stop_atr": 5.0}
    trailing_state: dict[str, Any] = {"XBTEUR": pos}
    positions_manager.tick_position(
        "XBTEUR", pos, balance={}, last_prices={"XBTEUR": 96.0}, atr_val=5.0, trailing_state=trailing_state
    )

    assert closed == ["XBTEUR"]
    assert pos["stop_at"] is not None


def test_tick_position_latches_stop_at_and_announces_the_breach_before_closing(monkeypatch) -> None:
    """Mirrors ``activated_at``: the latch and the operator-facing announcement
    both happen here, before ``close_position`` is ever called, so a rejected or
    lost placement still leaves the exit recorded as owed."""
    _now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(positions_manager, "refresh_position", lambda *_: True)
    monkeypatch.setattr(positions_manager, "ATR_DESV_LIMIT", 0.2)
    monkeypatch.setattr(positions_manager, "now_utc", lambda: _now)

    seen_stop_at: list = []
    captured: list[bool] = []
    monkeypatch.setattr(positions_manager.logging, "info", lambda msg, to_telegram=False: captured.append(to_telegram))
    monkeypatch.setattr(
        positions_manager,
        "close_position",
        lambda pair, pos, prices: seen_stop_at.append(pos.get("stop_at")),
    )

    pos: dict[str, Any] = {"side": "sell", "volume": 1.0, "trailing_price": 100.0, "stop_price": 95.0, "stop_atr": 5.0}
    trailing_state: dict[str, Any] = {"XBTEUR": pos}
    positions_manager.tick_position(
        "XBTEUR", pos, balance={}, last_prices={"XBTEUR": 90.0}, atr_val=5.0, trailing_state=trailing_state
    )

    assert pos["stop_at"] == _now
    assert seen_stop_at == [_now]  # latched before close_position runs
    assert captured == [True]  # the breach line is the operator's only signal


def test_tick_position_updates_trailing_when_sell_price_moves_up(monkeypatch) -> None:
    monkeypatch.setattr(positions_manager, "refresh_position", lambda *_: True)
    monkeypatch.setattr(positions_manager, "ATR_DESV_LIMIT", 0.2)

    updated_prices: list[float] = []
    monkeypatch.setattr(
        positions_manager,
        "update_stop_price",
        lambda pair, pos, price, atr: updated_prices.append(price),
    )

    # sell: update trailing when current_price > trailing_price; stop_atr in range, stop not hit
    pos: dict[str, Any] = {"side": "sell", "trailing_price": 100.0, "stop_price": 90.0, "stop_atr": 5.0}
    trailing_state: dict[str, Any] = {"XBTEUR": pos}
    positions_manager.tick_position(
        "XBTEUR", pos, balance={}, last_prices={"XBTEUR": 110.0}, atr_val=5.0, trailing_state=trailing_state
    )

    assert updated_prices == [110.0]


def test_tick_position_recalibrates_stop_when_stop_atr_out_of_range(monkeypatch) -> None:
    monkeypatch.setattr(positions_manager, "refresh_position", lambda *_: True)
    monkeypatch.setattr(positions_manager, "ATR_DESV_LIMIT", 0.1)

    def fake_update_stop(pair, pos, price, atr) -> None:
        pos["stop_price"] = 70.0
        pos["stop_atr"] = atr

    monkeypatch.setattr(positions_manager, "update_stop_price", fake_update_stop)

    # stop_atr=2.0, current atr=5.0: 2.0 < 5.0*(1-0.1)=4.5 → out of range
    # current_price=90 does not hit recalibrated stop (70) and does not exceed trailing (100)
    pos: dict[str, Any] = {"side": "sell", "trailing_price": 100.0, "stop_price": 85.0, "stop_atr": 2.0}
    trailing_state: dict[str, Any] = {"XBTEUR": pos}
    positions_manager.tick_position(
        "XBTEUR", pos, balance={}, last_prices={"XBTEUR": 90.0}, atr_val=5.0, trailing_state=trailing_state
    )

    # recalibration passes trailing_price as the reference, not current_price
    assert pos["stop_price"] == 70.0
    assert pos["stop_atr"] == 5.0


# ============================================================================
# manage_close_position
# ============================================================================


def test_manage_close_position_reports_filled_when_the_fill_is_confirmed(monkeypatch) -> None:
    """FILLED is the scheduler's cue to write the closed row; nothing else runs."""
    monkeypatch.setattr(positions_manager, "get_order_state", lambda _txid: None)
    monkeypatch.setattr(positions_manager, "_drive_closing_order", lambda *a: positions_manager.ClosingState.FILLED)
    monkeypatch.setattr(positions_manager, "close_position", lambda *a, **k: pytest.fail("nothing to place"))

    pos = {"side": "sell", "volume": 0.5, "stop_at": "2026-07-26T00:00:00+00:00", "closing_order_id": "ORD001"}
    outcome = positions_manager.manage_close_position("XBTEUR", pos, {}, {"XBTEUR": 105.0}, {})
    assert outcome is positions_manager.ClosingState.FILLED


def test_manage_close_position_reprices_a_live_order(monkeypatch) -> None:
    """The order's state is fetched by the confirmed txid and threaded straight
    into the single dispatch."""
    calls: list = []
    monkeypatch.setattr(positions_manager, "get_order_state", lambda txid: txid)
    monkeypatch.setattr(
        positions_manager,
        "_drive_closing_order",
        lambda pair, pos, state, last_prices: (
            calls.append((pair, pos, state, last_prices)) or positions_manager.ClosingState.PENDING
        ),
    )
    monkeypatch.setattr(positions_manager, "close_position", lambda *a, **k: pytest.fail("must not re-place"))

    pos = {"side": "sell", "volume": 0.5, "stop_at": "2026-07-26T00:00:00+00:00", "closing_order_id": "ORD001"}
    outcome = positions_manager.manage_close_position("XBTEUR", pos, {}, {"XBTEUR": 105.0}, {})
    assert outcome is positions_manager.ClosingState.PENDING
    assert calls == [("XBTEUR", pos, "ORD001", {"XBTEUR": 105.0})]


def test_manage_close_position_propagates_an_unmanaged_outcome(monkeypatch) -> None:
    """Whatever `_drive_closing_order` reports flows straight through — the pair
    reaches `failed_pairs` exactly when the closing order is left unmanaged."""
    monkeypatch.setattr(positions_manager, "get_order_state", lambda _txid: None)
    monkeypatch.setattr(positions_manager, "_drive_closing_order", lambda *a: positions_manager.ClosingState.UNMANAGED)
    monkeypatch.setattr(positions_manager, "close_position", lambda *a, **k: pytest.fail("must not re-place"))

    pos = {"side": "sell", "volume": 0.5, "stop_at": "2026-07-26T00:00:00+00:00", "closing_order_id": "ORD001"}
    outcome = positions_manager.manage_close_position("XBTEUR", pos, {}, {"XBTEUR": 105.0}, {})
    assert outcome is positions_manager.ClosingState.UNMANAGED


def test_manage_close_position_refreshes_then_replaces_when_no_order_rests(monkeypatch) -> None:
    """A latched position never reaches tick_position, so nothing else resizes it
    — and a stale volume may be exactly why the last attempt was rejected."""
    order: list[str] = []
    monkeypatch.setattr(positions_manager, "refresh_position", lambda *a: order.append("refresh") or True)
    monkeypatch.setattr(positions_manager, "close_position", lambda *a, **k: order.append("close") or True)

    pos = {"side": "sell", "volume": 0.5, "stop_at": "2026-07-26T00:00:00+00:00"}
    outcome = positions_manager.manage_close_position("XBTEUR", pos, {}, {"XBTEUR": 100.0}, {})
    assert outcome is positions_manager.ClosingState.PENDING
    assert order == ["refresh", "close"]


def test_manage_close_position_reports_a_failed_replacement(monkeypatch) -> None:
    monkeypatch.setattr(positions_manager, "refresh_position", lambda *a: True)
    monkeypatch.setattr(positions_manager, "close_position", lambda *a, **k: False)

    pos = {"side": "sell", "volume": 0.5, "stop_at": "2026-07-26T00:00:00+00:00"}
    outcome = positions_manager.manage_close_position("XBTEUR", pos, {}, {"XBTEUR": 100.0}, {})
    assert outcome is positions_manager.ClosingState.UNMANAGED


def test_manage_close_position_is_pending_when_the_position_is_dropped(monkeypatch) -> None:
    """A drop is a resolved pair, not a failure: there is nothing left to place,
    and it is the natural end of an otherwise endless retry loop."""
    monkeypatch.setattr(positions_manager, "refresh_position", lambda *a: False)
    monkeypatch.setattr(positions_manager, "close_position", lambda *a, **k: pytest.fail("nothing to place"))

    pos = {"side": "sell", "volume": 0.5, "stop_at": "2026-07-26T00:00:00+00:00"}
    outcome = positions_manager.manage_close_position("XBTEUR", pos, {}, {"XBTEUR": 100.0}, {})
    assert outcome is positions_manager.ClosingState.PENDING


def test_manage_close_position_clears_a_dead_order_and_re_places_it_in_one_call(monkeypatch) -> None:
    """A terminal order that cannot be finalized has its fields cleared and a fresh
    exit placed on the SAME tick, never a full interval later."""
    monkeypatch.setattr(
        positions_manager,
        "get_order_state",
        lambda _: OrderState(status=OrderStatus.CANCELED, avg_price=0.0, vol_exec=0.0),
    )
    monkeypatch.setattr(positions_manager, "refresh_position", lambda *a: True)
    monkeypatch.setattr(
        positions_manager,
        "place_limit_order",
        lambda *a, **k: kraken.PlacedOrder(txid="ORD002", price=67000.0, volume=0.5),
    )

    pos = {
        "side": "sell",
        "volume": 0.5,
        "entry_price": 68000.0,
        "stop_at": "2026-07-26T00:00:00+00:00",
        "closing_order_id": "ORD001",
        "closing_price": 68500.0,
    }
    outcome = positions_manager.manage_close_position("XBTEUR", pos, {}, {"XBTEUR": 67000.0}, {})
    assert outcome is positions_manager.ClosingState.PENDING
    assert pos["closing_order_id"] == "ORD002"
    assert pos["closing_price"] == 67000.0


@pytest.mark.parametrize("status", [OrderStatus.NOT_FOUND, OrderStatus.UNKNOWN])
def test_manage_close_position_reports_unmanaged_when_the_order_cannot_be_resolved(monkeypatch, status) -> None:
    """End to end: an unresolvable order used to report PENDING and freeze the pair
    in silence. It now reports UNMANAGED, so the per-pair streak alerts on it."""
    monkeypatch.setattr(
        positions_manager,
        "get_order_state",
        lambda _: OrderState(status=status, avg_price=None, vol_exec=0.0),
    )
    monkeypatch.setattr(positions_manager, "cancel_order", lambda _order_id: pytest.fail("must not cancel"))
    monkeypatch.setattr(positions_manager, "place_limit_order", lambda *a: pytest.fail("must not place"))

    pos = {
        "side": "sell",
        "volume": 0.5,
        "entry_price": 68000.0,
        "stop_at": "2026-07-26T00:00:00+00:00",
        "closing_order_id": "ORD001",
        "closing_price": 68500.0,
    }
    outcome = positions_manager.manage_close_position("XBTEUR", pos, {}, {"XBTEUR": 67000.0}, {})
    assert outcome is positions_manager.ClosingState.UNMANAGED
    assert pos["closing_order_id"] == "ORD001"


# ============================================================================
# manage_close_position — unconfirmed sub-state (closing_request_id)
# ============================================================================


def test_manage_close_position_adopts_a_recovered_order_and_drives_it_same_tick(monkeypatch) -> None:
    """An id whose AddOrder response was lost resolves via the lookup; if it
    turns out to already be filled, that is reported on the SAME tick, not a
    tick later."""
    monkeypatch.setattr(positions_manager, "get_order_state", lambda _txid: pytest.fail("must not query by txid"))
    recovered_state = OrderState(status=OrderStatus.CLOSED, avg_price=69099.7, vol_exec=0.01)
    monkeypatch.setattr(
        positions_manager,
        "find_order_by_cl_ord_id",
        lambda cl_ord_id: OrderLookup(txid="RECOVERED1", state=recovered_state),
    )
    monkeypatch.setattr(positions_manager, "refresh_position", lambda *a: pytest.fail("must not refresh"))
    monkeypatch.setattr(positions_manager, "close_position", lambda *a, **k: pytest.fail("must not re-place"))
    sent = _capture_telegram(monkeypatch)

    pos = {
        "side": "sell",
        "entry_price": 68000.0,
        "volume": 0.01,
        "stop_at": "2026-07-26T00:00:00+00:00",
        "closing_request_id": "REQ1",
    }
    outcome = positions_manager.manage_close_position("XBTEUR", pos, {}, {"XBTEUR": 69000.0}, {})

    assert outcome is positions_manager.ClosingState.FILLED
    assert pos["closing_order_id"] == "RECOVERED1"
    # Logged for the session narrative only: the recovery is not an operator alert.
    assert [(msg, to_telegram) for msg, to_telegram in sent if "RECOVERED1" in msg] == [
        ("[XBTEUR] Recovered closing order RECOVERED1 from its client id.", False)
    ]


def test_manage_close_position_adopts_a_recovered_order_that_is_still_open(monkeypatch) -> None:
    """A recovered order that is not yet terminal drives through to reprice on
    the same tick, exactly like a confirmed order would."""
    monkeypatch.setattr(positions_manager, "get_order_state", lambda _txid: pytest.fail("must not query by txid"))
    recovered_state = OrderState(status=OrderStatus.OPEN, avg_price=None, vol_exec=0.0)
    monkeypatch.setattr(
        positions_manager,
        "find_order_by_cl_ord_id",
        lambda cl_ord_id: OrderLookup(txid="RECOVERED2", state=recovered_state),
    )
    calls: list = []
    monkeypatch.setattr(positions_manager, "reprice_closing_order", lambda *a: calls.append(a) or True)
    monkeypatch.setattr(positions_manager, "refresh_position", lambda *a: pytest.fail("must not refresh"))
    monkeypatch.setattr(positions_manager, "close_position", lambda *a, **k: pytest.fail("must not re-place"))

    pos = {"side": "sell", "volume": 0.5, "stop_at": "2026-07-26T00:00:00+00:00", "closing_request_id": "REQ1"}
    outcome = positions_manager.manage_close_position("XBTEUR", pos, {}, {"XBTEUR": 105.0}, {})

    assert outcome is positions_manager.ClosingState.PENDING
    assert pos["closing_order_id"] == "RECOVERED2"
    assert calls == [("XBTEUR", pos, recovered_state, {"XBTEUR": 105.0})]


def test_manage_close_position_clears_and_replaces_when_the_request_never_landed(monkeypatch) -> None:
    """Genuine evidence nothing landed: the closing fields are cleared, stop_at
    survives, and a NEW order is placed with a NEW id on the same tick."""
    monkeypatch.setattr(
        positions_manager,
        "find_order_by_cl_ord_id",
        lambda cl_ord_id: OrderLookup(txid=None, state=None),
    )
    monkeypatch.setattr(positions_manager, "refresh_position", lambda *a: True)
    place_calls: list = []
    monkeypatch.setattr(
        positions_manager,
        "place_limit_order",
        lambda pair, side, price, volume, cl_ord_id=None: (
            place_calls.append(cl_ord_id) or kraken.PlacedOrder(txid="NEWORDER9", price=price, volume=volume)
        ),
    )

    pos = {
        "side": "sell",
        "entry_price": 68000.0,
        "volume": 0.01,
        "stop_at": "2026-07-26T00:00:00+00:00",
        "closing_request_id": "OLDREQ",
    }
    outcome = positions_manager.manage_close_position("XBTEUR", pos, {}, {"XBTEUR": 69000.0}, {})

    assert outcome is positions_manager.ClosingState.PENDING
    assert pos["closing_order_id"] == "NEWORDER9"
    assert pos["closing_request_id"] != "OLDREQ"
    assert place_calls == [pos["closing_request_id"]]
    # The exit stays owed even though nothing landed for the previous attempt.
    assert pos["stop_at"] == "2026-07-26T00:00:00+00:00"


def test_manage_close_position_reports_unmanaged_on_lookup_error(monkeypatch) -> None:
    """A lookup failure decides nothing: every field stays exactly as it was, and
    no placement is attempted."""
    monkeypatch.setattr(positions_manager, "find_order_by_cl_ord_id", lambda cl_ord_id: None)
    monkeypatch.setattr(positions_manager, "refresh_position", lambda *a: pytest.fail("must not refresh"))
    monkeypatch.setattr(positions_manager, "close_position", lambda *a, **k: pytest.fail("must not place"))

    pos = {
        "side": "sell",
        "entry_price": 68000.0,
        "volume": 0.01,
        "stop_at": "2026-07-26T00:00:00+00:00",
        "closing_request_id": "REQ1",
    }
    outcome = positions_manager.manage_close_position("XBTEUR", pos, {}, {"XBTEUR": 69000.0}, {})

    assert outcome is positions_manager.ClosingState.UNMANAGED
    assert pos["closing_request_id"] == "REQ1"
    assert "closing_order_id" not in pos


def test_manage_close_position_routes_confirmed_without_the_lookup(monkeypatch) -> None:
    monkeypatch.setattr(
        positions_manager,
        "get_order_state",
        lambda txid: OrderState(status=OrderStatus.OPEN, avg_price=None, vol_exec=0.0),
    )
    monkeypatch.setattr(
        positions_manager, "find_order_by_cl_ord_id", lambda *_: pytest.fail("must not look up when confirmed")
    )
    monkeypatch.setattr(positions_manager, "reprice_closing_order", lambda *a: True)

    pos = {"side": "sell", "volume": 0.5, "stop_at": "2026-07-26T00:00:00+00:00", "closing_order_id": "ORD001"}
    outcome = positions_manager.manage_close_position("XBTEUR", pos, {}, {"XBTEUR": 105.0}, {})
    assert outcome is positions_manager.ClosingState.PENDING


def test_manage_close_position_routes_unconfirmed_without_get_order_state(monkeypatch) -> None:
    monkeypatch.setattr(
        positions_manager, "get_order_state", lambda *_: pytest.fail("must not query by txid when unconfirmed")
    )
    monkeypatch.setattr(
        positions_manager,
        "find_order_by_cl_ord_id",
        lambda cl_ord_id: OrderLookup(txid=None, state=None),
    )
    monkeypatch.setattr(positions_manager, "refresh_position", lambda *a: True)
    monkeypatch.setattr(positions_manager, "close_position", lambda *a, **k: True)

    pos = {"side": "sell", "volume": 0.5, "stop_at": "2026-07-26T00:00:00+00:00", "closing_request_id": "REQ1"}
    outcome = positions_manager.manage_close_position("XBTEUR", pos, {}, {"XBTEUR": 105.0}, {})
    assert outcome is positions_manager.ClosingState.PENDING
