from datetime import datetime, timezone

import trading.positions_manager as positions_manager


def test_calculate_activation_price_uses_k_act_when_defined(monkeypatch) -> None:
    monkeypatch.setattr(
        positions_manager,
        "TRADING_PARAMS",
        {"XBTEUR": {"sell": {"K_ACT": 2.0, "MIN_MARGIN": 0.01}, "buy": {"K_ACT": 2.0, "MIN_MARGIN": 0.01}}},
    )

    result = positions_manager.calculate_activation_price("XBTEUR", "sell", 100.0, 5.0)

    assert result == 110.0


def test_calculate_activation_price_uses_k_stop_and_margin_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        positions_manager,
        "TRADING_PARAMS",
        {"XBTEUR": {"sell": {"K_ACT": None, "MIN_MARGIN": 0.1}, "buy": {"K_ACT": None, "MIN_MARGIN": 0.1}}},
    )
    monkeypatch.setattr(positions_manager, "get_k_stop", lambda pair, side, atr: 2.0)

    result = positions_manager.calculate_activation_price("XBTEUR", "buy", 100.0, 5.0)

    # distance = 2*5 + 0.1*100 = 20
    assert result == 80.0


def test_update_stop_price_updates_position_fields(monkeypatch) -> None:
    monkeypatch.setattr(positions_manager, "get_k_stop", lambda pair, side, atr: 1.5)

    pos = {"side": "sell"}
    positions_manager.update_stop_price("XBTEUR", pos, trailing_price=120.0, atr_val=4.0)

    assert pos["trailing_price"] == 120.0
    assert pos["stop_price"] == 114.0
    assert pos["stop_atr"] == 4.0


def test_create_position_builds_state_from_calculated_values(monkeypatch) -> None:
    monkeypatch.setattr(positions_manager, "MIN_VALUE", 10.0)
    monkeypatch.setattr(
        positions_manager,
        "calculate_position",
        lambda pair, balance, prices, state: ("buy", 100.0),
    )
    monkeypatch.setattr(positions_manager, "calculate_activation_price", lambda *args: 85.0)
    _now = datetime(2026, 1, 1, tzinfo=timezone.utc)
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


def test_close_position_updates_position_on_success(monkeypatch) -> None:
    _now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(positions_manager, "place_limit_order", lambda *args: "ORDER123")
    monkeypatch.setattr(positions_manager, "now_utc", lambda: _now)

    pos = {"side": "sell", "entry_price": 100.0, "stop_price": 95.0, "volume": 1.0}
    prices = {"XBTEUR": 90.0}

    positions_manager.close_position("XBTEUR", pos, prices)

    assert pos["closing_order_id"] == "ORDER123"
    assert pos["closing_requested_at"] == _now
    assert pos["closing_price"] == 90.0
    assert pos["pnl_percent"] == -10.0


def test_update_activation_price_updates_position_fields(monkeypatch) -> None:
    monkeypatch.setattr(positions_manager, "calculate_activation_price", lambda *_args: 87.5)

    pos = {"side": "buy", "entry_price": 100.0}
    positions_manager.update_activation_price("XBTEUR", pos, atr_val=3.0)

    assert pos["activation_price"] == 87.5
    assert pos["activation_atr"] == 3.0


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
        "XBTEUR", pos,
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
        "XBTEUR", pos,
        balance={"ZEUR": 100.0},
        last_prices={"XBTEUR": 100.0},
        trailing_state=trailing_state,
    )

    assert result is False
    assert "XBTEUR" not in trailing_state
