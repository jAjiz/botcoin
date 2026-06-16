import pytest

import trading.market_analyzer as market_analyzer
import trading.parameters_manager as parameters_manager


def test_calculate_k_stops_uses_percentiles_and_rounds_up(monkeypatch) -> None:
    monkeypatch.setattr(parameters_manager, "LEVELS", ("LL", "LV"))
    monkeypatch.setattr(parameters_manager, "STOP_PERCENTILES", {"XBTEUR": {"LL": 0.5, "LV": 0.5}})

    events = [
        {"volatility_levels": {"LL": {"k_value": 1.01}, "LV": {"k_value": 2.01}}},
        {"volatility_levels": {"LL": {"k_value": 1.09}, "LV": {"k_value": 2.09}}},
    ]

    result = parameters_manager.calculate_k_stops("XBTEUR", events)

    assert result["LL"] == 1.1
    assert result["LV"] == 2.1


def test_get_volatility_level_maps_atr_to_expected_bucket(monkeypatch) -> None:
    monkeypatch.setattr(
        parameters_manager,
        "PAIRS",
        {
            "XBTEUR": {
                "atr_20pct": 10,
                "atr_50pct": 20,
                "atr_80pct": 30,
                "atr_95pct": 40,
            }
        },
    )

    assert parameters_manager.get_volatility_level("XBTEUR", 5) == "LL"
    assert parameters_manager.get_volatility_level("XBTEUR", 15) == "LV"
    assert parameters_manager.get_volatility_level("XBTEUR", 25) == "MV"
    assert parameters_manager.get_volatility_level("XBTEUR", 35) == "HV"
    assert parameters_manager.get_volatility_level("XBTEUR", 45) == "HH"


def test_get_k_stop_uses_fallbacks_when_current_level_missing(monkeypatch) -> None:
    monkeypatch.setattr(parameters_manager, "LEVELS", ("LL", "LV", "MV", "HV", "HH"))
    monkeypatch.setattr(
        parameters_manager,
        "PAIRS",
        {
            "XBTEUR": {
                "atr_20pct": 10,
                "atr_50pct": 20,
                "atr_80pct": 30,
                "atr_95pct": 40,
            }
        },
    )
    monkeypatch.setattr(
        parameters_manager,
        "TRADING_PARAMS",
        {
            "XBTEUR": {
                "K_STOP": {
                    "sell": {"LL": None, "LV": None, "MV": None, "HV": None, "HH": 5.5},
                    "buy": {"LL": None, "LV": None, "MV": None, "HV": None, "HH": 4.4},
                }
            }
        },
    )

    # ATR=35 => HV. Missing on sell side, so it should fallback to HH (neighbor).
    value = parameters_manager.get_k_stop("XBTEUR", "sell", 35)

    assert value == 5.5


def test_calculate_trading_parameters_updates_atr_and_k_stops(monkeypatch, sample_dataframe) -> None:
    pair = "XBTEUR"

    monkeypatch.setattr(parameters_manager.db, "load_ohlc_data", lambda _pair, _tf: sample_dataframe.copy())
    real_analyze = market_analyzer.analyze_structural_noise
    monkeypatch.setattr(
        parameters_manager,
        "analyze_structural_noise",
        lambda df: real_analyze(df, order=1),
    )
    monkeypatch.setattr(parameters_manager, "LEVELS", ("LL", "LV", "MV", "HV", "HH"))
    monkeypatch.setattr(
        parameters_manager,
        "STOP_PERCENTILES",
        {
            pair: {
                "LL": 0.5,
                "LV": 0.5,
                "MV": 0.5,
                "HV": 0.5,
                "HH": 0.5,
            }
        },
    )
    monkeypatch.setattr(parameters_manager, "PAIRS", {pair: {}})
    monkeypatch.setattr(
        parameters_manager,
        "TRADING_PARAMS",
        {pair: {"K_STOP": {"sell": {}, "buy": {}}}},
    )

    parameters_manager.calculate_trading_parameters(pair, infoLog=False)

    # ATR percentiles from the fixture's atr column
    assert parameters_manager.PAIRS[pair]["atr_20pct"] == pytest.approx(1.5)
    assert parameters_manager.PAIRS[pair]["atr_50pct"] == pytest.approx(2.5)
    assert parameters_manager.PAIRS[pair]["atr_80pct"] == pytest.approx(3.5)
    assert parameters_manager.PAIRS[pair]["atr_95pct"] == pytest.approx(4.5)

    # K_STOP sell side (from real uptrend events, 2 events X 5 vol levels)
    sell = parameters_manager.TRADING_PARAMS[pair]["K_STOP"]["sell"]
    assert sell["LL"] == 10.0
    assert sell["LV"] == 3.4
    assert sell["MV"] == 2.0
    assert sell["HV"] == 1.5
    assert sell["HH"] == 1.2

    # K_STOP buy side (from real downtrend events, 2 events X 5 vol levels)
    buy = parameters_manager.TRADING_PARAMS[pair]["K_STOP"]["buy"]
    assert buy["LL"] == 11.0
    assert buy["LV"] == 3.4
    assert buy["MV"] == 2.0
    assert buy["HV"] == 1.5
    assert buy["HH"] == 1.2
