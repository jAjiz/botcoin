import pandas as pd
import pytest

import trading.market_analyzer as market_analyzer

from trading.market_analyzer import (
    analyze_structural_noise,
    calculate_noise_between_pivots,
    detect_pivots,
    get_args,
    get_current_atr,
)


def test_detect_pivots_returns_min_and_max_points(sample_dataframe: pd.DataFrame) -> None:
    df = sample_dataframe

    pivots = detect_pivots(df, order=1)

    assert pivots
    assert any(p[1] == "min" for p in pivots)
    assert any(p[1] == "max" for p in pivots)


def test_calculate_noise_between_pivots_returns_event_for_uptrend(sample_dataframe: pd.DataFrame) -> None:
    df = sample_dataframe

    start = (1, "min", df.loc[1, "low"], df.loc[1, "dtime"])
    end = (5, "max", df.loc[5, "high"], df.loc[5, "dtime"])
    atr_percentiles = {"p20": 1.1, "p50": 1.8, "p80": 2.6, "p95": 3.2}

    event = calculate_noise_between_pivots(df, (start, end), atr_percentiles)

    assert event["type"] == "uptrend"
    assert event["price_change_pct"] > 0
    assert isinstance(event["volatility_levels"], dict)


def test_analyze_structural_noise_returns_two_event_lists(sample_dataframe: pd.DataFrame) -> None:
    df = sample_dataframe

    uptrend_events, downtrend_events = analyze_structural_noise(
        df, order=1, print_results=True, show_events=True, volatility_level="ALL")

    assert isinstance(uptrend_events, list)
    assert isinstance(downtrend_events, list)


def test_get_current_atr_returns_value_with_mocked_ohlc(monkeypatch, sample_dataframe: pd.DataFrame) -> None:
    df = sample_dataframe.set_index("dtime")

    monkeypatch.setattr(market_analyzer.os.path, "exists", lambda _path: False)
    monkeypatch.setattr(market_analyzer, "fetch_ohlc_data", lambda *_args, **_kwargs: df.copy())
    monkeypatch.setattr(market_analyzer, "ATR_PERIOD", 3)
    monkeypatch.setattr(market_analyzer, "MARKET_DATA_DAYS", 9999)
    monkeypatch.setattr(pd.DataFrame, "to_csv", lambda self, *args, **kwargs: None)

    current_atr = get_current_atr("XBTEUR")

    assert current_atr is not None
    assert pd.notna(current_atr)


def test_get_args_parses_cli_values(monkeypatch) -> None:
    monkeypatch.setattr(
        market_analyzer.sys,
        "argv",
        ["market_analyzer.py", "PAIR=XBTEUR", "ORDER=10", "SHOW_EVENTS", "Volatility=hv"],
    )

    args = get_args()

    assert args["pair"] == "XBTEUR"
    assert args["order"] == 10
    assert args["show_events"] is True
    assert args["volatility_level"] == "HV"


def test_get_args_exits_when_pair_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(market_analyzer.sys, "argv", ["market_analyzer.py", "ORDER=10"])

    with pytest.raises(SystemExit):
        get_args()
