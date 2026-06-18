import pytest

import core.runtime as runtime
import trading.market_analyzer as market_analyzer
import trading.parameters_manager as parameters_manager


def test_calculate_trading_parameters_populates_calibration_cache(monkeypatch, sample_dataframe) -> None:
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
        {pair: {"LL": 0.5, "LV": 0.5, "MV": 0.5, "HV": 0.5, "HH": 0.5}},
    )
    monkeypatch.setattr(parameters_manager, "PAIRS", {pair: {}})
    monkeypatch.setattr(
        parameters_manager,
        "TRADING_PARAMS",
        {pair: {"K_STOP": {"sell": {}, "buy": {}}}},
    )

    parameters_manager.calculate_trading_parameters(pair, infoLog=False)

    cal = runtime.get_pair_calibration(pair)
    assert cal is not None

    # Events captured from the structural-noise analysis.
    assert len(cal["up_events"]) > 0
    assert len(cal["down_events"]) > 0

    # ATR percentiles match what was written to the globals.
    assert cal["atr_p20"] == pytest.approx(parameters_manager.PAIRS[pair]["atr_20pct"])
    assert cal["atr_p50"] == pytest.approx(parameters_manager.PAIRS[pair]["atr_50pct"])
    assert cal["atr_p80"] == pytest.approx(parameters_manager.PAIRS[pair]["atr_80pct"])
    assert cal["atr_p95"] == pytest.approx(parameters_manager.PAIRS[pair]["atr_95pct"])

    assert cal["row_count"] == len(sample_dataframe)
    assert cal["computed_at"] is not None


def test_get_pair_calibration_returns_none_for_unknown_pair() -> None:
    assert runtime.get_pair_calibration("UNKNOWN_PAIR_XYZ") is None
