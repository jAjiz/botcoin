import trading.inventory_manager as inventory_manager


def _setup_config(monkeypatch) -> None:
    monkeypatch.setattr(inventory_manager, "PAIRS", {"XBTEUR": {"base": "XXBT"}})
    monkeypatch.setattr(
        inventory_manager,
        "ASSET_ALLOCATION",
        {"XBTEUR": {"TARGET_PCT": 50, "HODL_PCT": 20}},
    )


def test_get_portfolio_value_includes_assets_and_fiat(monkeypatch) -> None:
    _setup_config(monkeypatch)

    balance = {"XXBT": 2.0, "ZEUR": 1000.0}
    prices = {"XBTEUR": 100.0}

    assert inventory_manager.get_portfolio_value(balance, prices) == 1200.0


def test_get_available_fiat_respects_reserved_buy_positions(monkeypatch) -> None:
    _setup_config(monkeypatch)

    balance = {"ZEUR": 1000.0}
    prices = {"XBTEUR": 100.0}
    trailing_state = {"XBTEUR": {"side": "buy", "volume": 3.0}}

    assert inventory_manager.get_available_fiat(balance, prices, trailing_state) == 700.0


def test_calculate_position_prefers_buy_when_gap_is_larger(monkeypatch) -> None:
    _setup_config(monkeypatch)

    balance = {"XXBT": 2.0, "ZEUR": 1000.0}
    prices = {"XBTEUR": 100.0}

    side, value = inventory_manager.calculate_position("XBTEUR", balance, prices, trailing_state={})

    assert side == "buy"
    assert value == 400.0


def test_calculate_position_with_force_side(monkeypatch) -> None:
    _setup_config(monkeypatch)

    balance = {"XXBT": 2.0, "ZEUR": 1000.0}
    prices = {"XBTEUR": 100.0}

    side, value = inventory_manager.calculate_position(
        "XBTEUR", balance, prices, trailing_state={}, force_side="sell"
    )

    assert side == "sell"
    assert value == 80.0
