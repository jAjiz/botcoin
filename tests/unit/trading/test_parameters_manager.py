import pytest

import trading.engine as engine
import trading.market_analyzer as market_analyzer
import trading.parameters_manager as parameters_manager
from trading.engine import PairCalibration


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
                "atr_ratio_p20": 0.010,
                "atr_ratio_p50": 0.020,
                "atr_ratio_p80": 0.030,
                "atr_ratio_p95": 0.040,
            }
        },
    )

    close = 1000.0
    assert parameters_manager.get_volatility_level("XBTEUR", 5, close) == "LL"
    assert parameters_manager.get_volatility_level("XBTEUR", 15, close) == "LV"
    assert parameters_manager.get_volatility_level("XBTEUR", 25, close) == "MV"
    assert parameters_manager.get_volatility_level("XBTEUR", 35, close) == "HV"
    assert parameters_manager.get_volatility_level("XBTEUR", 45, close) == "HH"


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


def test_get_k_stop_uses_fallbacks_when_current_level_missing(monkeypatch) -> None:
    monkeypatch.setattr(parameters_manager, "LEVELS", ("LL", "LV", "MV", "HV", "HH"))
    monkeypatch.setattr(
        parameters_manager,
        "PAIRS",
        {
            "XBTEUR": {
                "atr_ratio_p20": 0.010,
                "atr_ratio_p50": 0.020,
                "atr_ratio_p80": 0.030,
                "atr_ratio_p95": 0.040,
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

    # ATR=35 at close=1000 => ratio 0.035 => HV. Missing on sell side, so it should fallback to HH (neighbor).
    value = parameters_manager.get_k_stop("XBTEUR", "sell", 35, 1000.0)

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

    # ATR/close percentiles from the fixture's atr and close columns
    assert parameters_manager.PAIRS[pair]["atr_ratio_p20"] == pytest.approx(0.0146493, rel=1e-5)
    assert parameters_manager.PAIRS[pair]["atr_ratio_p50"] == pytest.approx(0.0255102, rel=1e-5)
    assert parameters_manager.PAIRS[pair]["atr_ratio_p80"] == pytest.approx(0.0361801, rel=1e-5)
    assert parameters_manager.PAIRS[pair]["atr_ratio_p95"] == pytest.approx(0.0463853, rel=1e-5)

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
