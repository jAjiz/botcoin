from datetime import UTC, datetime
from typing import Any

import pytest

import trading.positions_manager as positions_manager
from exchange.kraken import OrderState

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
    monkeypatch.setattr(positions_manager, "get_k_stop", lambda pair, side, atr: 2.0)

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
    monkeypatch.setattr(positions_manager, "get_k_stop", lambda pair, side, atr: 1.5)

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
    monkeypatch.setattr(positions_manager, "get_k_stop", lambda pair, side, atr: 0.5)

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


# ============================================================================
# close_position
# ============================================================================


def test_close_position_updates_position_on_success(monkeypatch) -> None:
    _now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(positions_manager, "place_limit_order", lambda *args: "ORDER123")
    monkeypatch.setattr(positions_manager, "now_utc", lambda: _now)

    pos = {"side": "sell", "entry_price": 100.0, "stop_price": 95.0, "volume": 1.0}
    prices = {"XBTEUR": 90.0}

    positions_manager.close_position("XBTEUR", pos, prices)

    assert pos["closing_order_id"] == "ORDER123"
    assert pos["closing_requested_at"] == _now
    assert pos["closing_price"] == 90.0
    assert "pnl_percent" not in pos


def test_close_position_leaves_position_untouched_when_place_order_fails(monkeypatch) -> None:
    monkeypatch.setattr(positions_manager, "place_limit_order", lambda *args: None)

    pos = {"side": "sell", "entry_price": 100.0, "stop_price": 95.0, "volume": 1.0}
    prices = {"XBTEUR": 90.0}

    positions_manager.close_position("XBTEUR", pos, prices)
    assert "closing_order_id" not in pos


def test_close_position_leaves_position_untouched_on_unexpected_error(monkeypatch) -> None:
    def boom(*_args):
        raise Exception("kraken exploded")

    monkeypatch.setattr(positions_manager, "place_limit_order", boom)

    pos = {"side": "sell", "entry_price": 100.0, "stop_price": 95.0, "volume": 1.0}
    prices = {"XBTEUR": 90.0}

    # Must not raise: the scheduler has to keep ticking the other pairs.
    positions_manager.close_position("XBTEUR", pos, prices)
    assert "closing_order_id" not in pos


# ============================================================================
# is_open
# ============================================================================


@pytest.mark.parametrize("pos", [None, {}])
def test_is_open_returns_false_for_falsy_pos(pos) -> None:
    assert positions_manager.is_open(pos) is False


def test_is_open_returns_false_when_closing_order_present() -> None:
    assert positions_manager.is_open({"side": "sell", "closing_order_id": "ORD001"}) is False


def test_is_open_returns_true_when_no_closing_order() -> None:
    assert positions_manager.is_open({"side": "sell", "activation_price": 100.0}) is True


# ============================================================================
# is_closing_complete
# ============================================================================


@pytest.mark.parametrize("pos", [None, {"side": "sell"}])
def test_is_closing_complete_returns_false_without_closing_order(pos) -> None:
    assert positions_manager.is_closing_complete(pos) is False


def test_is_closing_complete_returns_false_on_api_error(monkeypatch) -> None:
    monkeypatch.setattr(positions_manager, "get_order_state", lambda _: None)

    assert positions_manager.is_closing_complete({"closing_order_id": "ORD001"}) is False


@pytest.mark.parametrize("status", ["pending", "open"])
def test_is_closing_complete_returns_false_while_order_in_flight(monkeypatch, status) -> None:
    monkeypatch.setattr(
        positions_manager,
        "get_order_state",
        lambda _: OrderState(status=status, avg_price=None, vol_exec=0.0),
    )

    pos = {"closing_order_id": "ORD001"}
    assert positions_manager.is_closing_complete(pos) is False
    assert pos["closing_order_id"] == "ORD001"


def test_is_closing_complete_returns_true_and_updates_pos_when_order_filled(monkeypatch) -> None:
    monkeypatch.setattr(
        positions_manager,
        "get_order_state",
        lambda _: OrderState(status="closed", avg_price=69099.7, vol_exec=0.01),
    )

    pos = {"closing_order_id": "ORD001", "entry_price": 68000.0, "side": "sell"}
    assert positions_manager.is_closing_complete(pos) is True
    assert pos["closing_price"] == 69099.7
    assert pos["pnl_percent"] == round((69099.7 - 68000.0) / 68000.0 * 100, 4)


def test_is_closing_complete_pnl_for_buy_side(monkeypatch) -> None:
    monkeypatch.setattr(
        positions_manager,
        "get_order_state",
        lambda _: OrderState(status="closed", avg_price=67000.0, vol_exec=0.01),
    )

    pos = {"closing_order_id": "ORD001", "entry_price": 68000.0, "side": "buy"}
    assert positions_manager.is_closing_complete(pos) is True
    assert pos["pnl_percent"] == round((68000.0 - 67000.0) / 68000.0 * 100, 4)


@pytest.mark.parametrize("status", ["canceled", "expired"])
def test_is_closing_complete_clears_fields_and_reopens_position_when_order_dead(monkeypatch, status) -> None:
    monkeypatch.setattr(
        positions_manager,
        "get_order_state",
        lambda _: OrderState(status=status, avg_price=0.0, vol_exec=0.0),
    )

    pos = {
        "side": "sell",
        "entry_price": 68000.0,
        "closing_order_id": "ORD001",
        "closing_price": 68500.0,
        "closing_requested_at": "2026-07-26T00:00:00+00:00",
    }
    assert positions_manager.is_closing_complete(pos) is False
    assert "closing_order_id" not in pos
    assert "closing_price" not in pos
    assert "closing_requested_at" not in pos
    assert positions_manager.is_open(pos) is True


def test_is_closing_complete_dead_order_alerts_with_the_order_id_and_status(monkeypatch) -> None:
    monkeypatch.setattr(
        positions_manager,
        "get_order_state",
        lambda _: OrderState(status="canceled", avg_price=68200.0, vol_exec=0.00512345),
    )
    captured: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        positions_manager.logging, "warning", lambda msg, to_telegram=False: captured.append((msg, to_telegram))
    )

    pos = {"side": "sell", "entry_price": 68000.0, "closing_order_id": "ORD001", "closing_price": 68500.0}
    assert positions_manager.is_closing_complete(pos) is False
    assert len(captured) == 1
    msg, to_telegram = captured[0]
    assert "ORD001" in msg
    assert "canceled" in msg
    assert to_telegram is True


def test_is_closing_complete_finalizes_a_terminal_order_that_was_fully_executed(monkeypatch) -> None:
    """A cancel can race a complete fill: Kraken labels the order `canceled` but
    nothing is left to manage. That is a finished trade — finalize it instead of
    resuming management, which would lose the trade from the PnL history."""
    monkeypatch.setattr(
        positions_manager,
        "get_order_state",
        lambda _: OrderState(status="canceled", avg_price=69099.7, vol=0.01, vol_exec=0.01),
    )

    pos = {"side": "sell", "entry_price": 68000.0, "volume": 0.0123, "closing_order_id": "ORD001"}
    assert positions_manager.is_closing_complete(pos) is True
    assert pos["closing_price"] == 69099.7
    assert pos["pnl_percent"] == round((69099.7 - 68000.0) / 68000.0 * 100, 4)
    # Recorded against what actually traded, not our own stale bookkeeping.
    assert pos["volume"] == 0.01


def test_is_closing_complete_reopens_position_when_terminal_order_left_a_remainder(monkeypatch) -> None:
    """A partial fill still leaves a position to manage, so the closing fields are
    cleared as before — only a fully executed order is finalizable."""
    monkeypatch.setattr(
        positions_manager,
        "get_order_state",
        lambda _: OrderState(status="canceled", avg_price=69099.7, vol=0.02, vol_exec=0.01),
    )

    pos = {"side": "sell", "entry_price": 68000.0, "volume": 0.02, "closing_order_id": "ORD001"}
    assert positions_manager.is_closing_complete(pos) is False
    assert "closing_order_id" not in pos
    assert "pnl_percent" not in pos


def test_is_closing_complete_does_not_finalize_fully_executed_order_without_usable_price(monkeypatch) -> None:
    monkeypatch.setattr(
        positions_manager,
        "get_order_state",
        lambda _: OrderState(status="canceled", avg_price=0.0, vol=0.01, vol_exec=0.01),
    )

    pos = {"side": "sell", "entry_price": 68000.0, "volume": 0.01, "closing_order_id": "ORD001"}
    assert positions_manager.is_closing_complete(pos) is False
    assert "closing_order_id" not in pos
    assert "pnl_percent" not in pos


def test_is_closing_complete_announces_a_finalized_terminal_order(monkeypatch) -> None:
    monkeypatch.setattr(
        positions_manager,
        "get_order_state",
        lambda _: OrderState(status="canceled", avg_price=69099.7, vol=0.01, vol_exec=0.01),
    )
    captured: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        positions_manager.logging, "warning", lambda msg, to_telegram=False: captured.append((msg, to_telegram))
    )

    pos = {"side": "sell", "entry_price": 68000.0, "volume": 0.01, "closing_order_id": "ORD001"}
    positions_manager.is_closing_complete(pos)

    assert len(captured) == 1
    msg, to_telegram = captured[0]
    assert "ORD001" in msg
    assert "canceled" in msg
    assert to_telegram is True


@pytest.mark.parametrize(
    "state",
    [
        OrderState(status="closed", avg_price=0.0, vol_exec=0.0),
        OrderState(status="closed", avg_price=None, vol_exec=0.0),
        OrderState(status="some-status-kraken-invented", avg_price=68200.0, vol_exec=0.5),
    ],
)
def test_is_closing_complete_reopens_position_on_any_unfinalizable_terminal_state(monkeypatch, state) -> None:
    """A terminal status we cannot finalize must clear the closing fields like a
    cancel does. Leaving them set would freeze the position forever: the status
    can never change again, reprice declines a non-open order, and is_open stays
    False, so nothing would ever exit."""
    monkeypatch.setattr(positions_manager, "get_order_state", lambda _: state)
    captured: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        positions_manager.logging, "warning", lambda msg, to_telegram=False: captured.append((msg, to_telegram))
    )

    pos = {
        "side": "sell",
        "entry_price": 68000.0,
        "closing_order_id": "ORD001",
        "closing_price": 68500.0,
        "closing_requested_at": "2026-07-26T00:00:00+00:00",
    }
    assert positions_manager.is_closing_complete(pos) is False
    assert "closing_order_id" not in pos
    assert "closing_price" not in pos
    assert "closing_requested_at" not in pos
    assert "pnl_percent" not in pos
    assert positions_manager.is_open(pos) is True
    # Must reach Telegram: an exit that neither filled nor cancelled cleanly is
    # not something any other signal would surface to the operator.
    assert captured and captured[0][1] is True


# ============================================================================
# reprice_closing_order
# ============================================================================


def test_reprice_closing_order_reprices_on_price_move(monkeypatch) -> None:
    _requested_at = datetime(2026, 1, 2, 11, 0, tzinfo=UTC)
    monkeypatch.setattr(positions_manager, "now_utc", lambda: datetime(2026, 1, 2, 12, 0, tzinfo=UTC))
    monkeypatch.setattr(
        positions_manager,
        "get_order_state",
        lambda _: OrderState(status="open", avg_price=None, vol_exec=0.0),
    )
    cancel_calls: list[str] = []
    monkeypatch.setattr(positions_manager, "cancel_order", lambda order_id: cancel_calls.append(order_id) or True)
    place_calls: list[tuple] = []
    monkeypatch.setattr(
        positions_manager,
        "place_limit_order",
        lambda pair, side, price, volume: place_calls.append((pair, side, price, volume)) or "NEWORDER1",
    )

    pos = {
        "side": "sell",
        "volume": 0.5,
        "closing_order_id": "OLDORDER",
        "closing_price": 100.0,
        "closing_requested_at": _requested_at,
    }
    positions_manager.reprice_closing_order("XBTEUR", pos, last_prices={"XBTEUR": 105.0})

    assert cancel_calls == ["OLDORDER"]
    assert place_calls == [("XBTEUR", "sell", 105.0, 0.5)]
    assert pos["closing_order_id"] == "NEWORDER1"
    assert pos["closing_price"] == 105.0
    # A reprice must not overwrite when the exit was first requested.
    assert pos["closing_requested_at"] == _requested_at


def test_reprice_closing_order_skips_when_partially_filled(monkeypatch) -> None:
    monkeypatch.setattr(
        positions_manager,
        "get_order_state",
        lambda _: OrderState(status="open", avg_price=100.0, vol_exec=0.2),
    )
    monkeypatch.setattr(
        positions_manager, "cancel_order", lambda _order_id: pytest.fail("must not cancel a partial fill")
    )
    monkeypatch.setattr(
        positions_manager, "place_limit_order", lambda *a, **k: pytest.fail("must not place a new order")
    )

    pos = {"side": "sell", "volume": 0.5, "closing_order_id": "OLDORDER", "closing_price": 100.0}
    positions_manager.reprice_closing_order("XBTEUR", pos, last_prices={"XBTEUR": 105.0})

    assert pos["closing_order_id"] == "OLDORDER"
    assert pos["closing_price"] == 100.0


def test_reprice_closing_order_skips_when_formatted_price_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(
        positions_manager,
        "get_order_state",
        lambda _: OrderState(status="open", avg_price=None, vol_exec=0.0),
    )
    monkeypatch.setattr(
        positions_manager, "cancel_order", lambda _order_id: pytest.fail("must not cancel an unchanged price")
    )
    monkeypatch.setattr(
        positions_manager, "place_limit_order", lambda *a, **k: pytest.fail("must not place a new order")
    )

    # No pair metadata loaded -> round_price falls back to 2 decimals; both prices
    # round to the same 100.0 limit, so re-placing would only lose queue priority.
    pos = {"side": "sell", "volume": 0.5, "closing_order_id": "OLDORDER", "closing_price": 100.001}
    positions_manager.reprice_closing_order("XBTEUR", pos, last_prices={"XBTEUR": 100.004})

    assert pos["closing_order_id"] == "OLDORDER"
    assert pos["closing_price"] == 100.001


def test_reprice_closing_order_noop_when_cancel_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        positions_manager,
        "get_order_state",
        lambda _: OrderState(status="open", avg_price=None, vol_exec=0.0),
    )
    monkeypatch.setattr(positions_manager, "cancel_order", lambda _order_id: False)
    monkeypatch.setattr(
        positions_manager, "place_limit_order", lambda *a, **k: pytest.fail("must not place a new order")
    )

    pos = {"side": "sell", "volume": 0.5, "closing_order_id": "OLDORDER", "closing_price": 100.0}
    positions_manager.reprice_closing_order("XBTEUR", pos, last_prices={"XBTEUR": 105.0})

    assert pos["closing_order_id"] == "OLDORDER"
    assert pos["closing_price"] == 100.0


def test_reprice_closing_order_noop_on_api_error(monkeypatch) -> None:
    monkeypatch.setattr(positions_manager, "get_order_state", lambda _: None)
    monkeypatch.setattr(positions_manager, "cancel_order", lambda _order_id: pytest.fail("must not cancel"))
    monkeypatch.setattr(positions_manager, "place_limit_order", lambda *a, **k: pytest.fail("must not place"))

    pos = {"side": "sell", "volume": 0.5, "closing_order_id": "OLDORDER", "closing_price": 100.0}
    positions_manager.reprice_closing_order("XBTEUR", pos, last_prices={"XBTEUR": 105.0})

    assert pos["closing_order_id"] == "OLDORDER"
    assert pos["closing_price"] == 100.0


def test_reprice_closing_order_noop_when_new_placement_fails_after_cancel(monkeypatch) -> None:
    monkeypatch.setattr(
        positions_manager,
        "get_order_state",
        lambda _: OrderState(status="open", avg_price=None, vol_exec=0.0),
    )
    monkeypatch.setattr(positions_manager, "cancel_order", lambda _order_id: True)
    monkeypatch.setattr(positions_manager, "place_limit_order", lambda *a, **k: None)
    captured: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        positions_manager.logging, "error", lambda msg, to_telegram=False: captured.append((msg, to_telegram))
    )

    pos = {"side": "sell", "volume": 0.5, "closing_order_id": "OLDORDER", "closing_price": 100.0}
    positions_manager.reprice_closing_order("XBTEUR", pos, last_prices={"XBTEUR": 105.0})

    # Position keeps the old, now-canceled order id: the dead-status branch of
    # is_closing_complete recovers it next tick.
    assert pos["closing_order_id"] == "OLDORDER"
    assert pos["closing_price"] == 100.0
    assert len(captured) == 1
    msg, to_telegram = captured[0]
    assert "re-place" in msg.lower()
    assert to_telegram is True


def test_reprice_closing_order_returns_when_no_closing_order() -> None:
    pos = {"side": "sell", "volume": 0.5}
    # Should not raise even without get_order_state/cancel_order/place_limit_order patched.
    positions_manager.reprice_closing_order("XBTEUR", pos, last_prices={"XBTEUR": 105.0})
    assert "closing_order_id" not in pos


@pytest.mark.parametrize("status", ["closed", "canceled", "expired", "pending"])
def test_reprice_closing_order_only_touches_an_open_order(monkeypatch, status) -> None:
    """Terminal statuses are is_closing_complete's job; a `pending` order has not
    reached the book yet, so cancel/replace would be pure churn."""
    monkeypatch.setattr(
        positions_manager,
        "get_order_state",
        lambda _: OrderState(status=status, avg_price=100.0, vol_exec=0.5),
    )
    monkeypatch.setattr(positions_manager, "cancel_order", lambda _order_id: pytest.fail("must not cancel"))
    monkeypatch.setattr(positions_manager, "place_limit_order", lambda *a, **k: pytest.fail("must not place"))

    pos = {"side": "sell", "volume": 0.5, "closing_order_id": "OLDORDER", "closing_price": 100.0}
    positions_manager.reprice_closing_order("XBTEUR", pos, last_prices={"XBTEUR": 105.0})

    assert pos["closing_order_id"] == "OLDORDER"


def _sequenced_order_states(*states: OrderState):
    """Return a fake get_order_state that yields `states` in call order."""
    calls = iter(states)

    def _fake(_order_id):
        return next(calls)

    return _fake


def test_reprice_closing_order_sizes_replacement_to_remainder_on_fill_in_cancel_window(monkeypatch) -> None:
    """A fill landing between the pre-cancel check and the cancel call still leaves
    a cancellable remainder. The post-cancel re-query must catch it so the
    replacement is sized at volume - vol_exec, not the full position."""
    monkeypatch.setattr(
        positions_manager,
        "get_order_state",
        _sequenced_order_states(
            OrderState(status="open", avg_price=None, vol_exec=0.0),  # pre-cancel check
            OrderState(status="canceled", avg_price=100.0, vol_exec=0.2),  # post-cancel re-query
        ),
    )
    monkeypatch.setattr(positions_manager, "cancel_order", lambda _order_id: True)
    place_calls: list[tuple] = []
    monkeypatch.setattr(
        positions_manager,
        "place_limit_order",
        lambda pair, side, price, volume: place_calls.append((pair, side, price, volume)) or "NEWORDER2",
    )

    pos = {"side": "sell", "volume": 0.5, "closing_order_id": "OLDORDER", "closing_price": 100.0}
    positions_manager.reprice_closing_order("XBTEUR", pos, last_prices={"XBTEUR": 105.0})

    assert place_calls == [("XBTEUR", "sell", 105.0, pytest.approx(0.3))]
    assert pos["volume"] == pytest.approx(0.3)
    assert pos["closing_order_id"] == "NEWORDER2"
    assert pos["closing_price"] == 105.0


def test_reprice_closing_order_noop_when_post_cancel_requery_fails(monkeypatch) -> None:
    """If the post-cancel get_order_state call errors, the executed amount is
    unknown: placing a replacement could over-sell. Leave closing_order_id set
    to the canceled id so the next tick's terminal-status branch self-heals."""
    monkeypatch.setattr(
        positions_manager,
        "get_order_state",
        _sequenced_order_states(
            OrderState(status="open", avg_price=None, vol_exec=0.0),
            None,
        ),
    )
    monkeypatch.setattr(positions_manager, "cancel_order", lambda _order_id: True)
    monkeypatch.setattr(
        positions_manager, "place_limit_order", lambda *a, **k: pytest.fail("must not place a new order")
    )

    pos = {"side": "sell", "volume": 0.5, "closing_order_id": "OLDORDER", "closing_price": 100.0}
    positions_manager.reprice_closing_order("XBTEUR", pos, last_prices={"XBTEUR": 105.0})

    assert pos["closing_order_id"] == "OLDORDER"
    assert pos["closing_price"] == 100.0
    assert pos["volume"] == 0.5


def test_reprice_closing_order_noop_when_remaining_is_not_positive(monkeypatch) -> None:
    """The canceled order turns out to have consumed the whole position (or more,
    from stale state): there is nothing left to replace."""
    monkeypatch.setattr(
        positions_manager,
        "get_order_state",
        _sequenced_order_states(
            OrderState(status="open", avg_price=None, vol_exec=0.0),
            OrderState(status="canceled", avg_price=100.0, vol_exec=0.5),
        ),
    )
    monkeypatch.setattr(positions_manager, "cancel_order", lambda _order_id: True)
    monkeypatch.setattr(
        positions_manager, "place_limit_order", lambda *a, **k: pytest.fail("must not place a new order")
    )

    pos = {"side": "sell", "volume": 0.5, "closing_order_id": "OLDORDER", "closing_price": 100.0}
    positions_manager.reprice_closing_order("XBTEUR", pos, last_prices={"XBTEUR": 105.0})

    assert pos["closing_order_id"] == "OLDORDER"
    assert pos["closing_price"] == 100.0
    assert pos["volume"] == 0.5


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
    monkeypatch.setattr(positions_manager, "close_position", lambda pair, pos, prices: closed.append(pair))

    # buy: close when current_price >= stop_price; stop_atr in range
    pos: dict[str, Any] = {"side": "buy", "trailing_price": 80.0, "stop_price": 95.0, "stop_atr": 5.0}
    trailing_state: dict[str, Any] = {"XBTEUR": pos}
    positions_manager.tick_position(
        "XBTEUR", pos, balance={}, last_prices={"XBTEUR": 96.0}, atr_val=5.0, trailing_state=trailing_state
    )

    assert closed == ["XBTEUR"]


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
