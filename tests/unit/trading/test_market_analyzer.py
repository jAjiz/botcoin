import pandas as pd
import pytest
from datetime import datetime, timezone

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


def test_get_current_atr_uses_db_slice_fetches_new_data_and_saves_only_new_closed_rows(monkeypatch) -> None:
    existing_df = pd.DataFrame(
        {
            "time": [1767225600, 1767226500, 1767227400],
            "dtime": pd.to_datetime([1767225600, 1767226500, 1767227400], unit="s", utc=True),
            "open": [100.0, 101.0, 102.0],
            "high": [101.0, 102.5, 104.0],
            "low": [99.0, 100.0, 101.5],
            "close": [100.5, 102.0, 103.5],
            "vwap": [100.2, 101.5, 102.8],
            "volume": [10.0, 11.0, 12.0],
            "count": [1, 1, 1],
            "atr": [1.0, 1.2, 1.3],
        }
    )

    fetched_df = pd.DataFrame(
        {
            "time": [1767227400, 1767228300, 1767229200],
            "open": [102.0, 103.5, 104.0],
            "high": [104.0, 105.5, 106.0],
            "low": [101.5, 103.0, 103.5],
            "close": [103.5, 105.0, 104.5],
            "vwap": [102.8, 104.2, 104.4],
            "volume": [12.0, 13.0, 14.0],
            "count": [1, 1, 1],
        },
        index=pd.to_datetime([1767227400, 1767228300, 1767229200], unit="s", utc=True),
    )

    calls = {"load": None, "fetch": None, "saved_df": None}

    def fake_load(pair, timeframe, since_time=None, limit=None):
        calls["load"] = {
            "pair": pair,
            "timeframe": timeframe,
            "since_time": since_time,
            "limit": limit,
        }
        return existing_df.copy()

    def fake_fetch(pair, timeframe, since_param):
        calls["fetch"] = {
            "pair": pair,
            "timeframe": timeframe,
            "since": since_param,
        }
        return fetched_df.copy()

    def fake_save(pair, timeframe, df):
        calls["saved_df"] = df.copy()

    monkeypatch.setattr(market_analyzer.db, "load_ohlc_data", fake_load)
    monkeypatch.setattr(market_analyzer, "fetch_ohlc_data", fake_fetch)
    monkeypatch.setattr(market_analyzer.db, "save_ohlc_data", fake_save)
    class _FixedDatetime:
        @staticmethod
        def now(tz=None):
            return datetime.fromtimestamp(1767229500, tz=timezone.utc)

    monkeypatch.setattr(market_analyzer, "datetime", _FixedDatetime)
    monkeypatch.setattr(market_analyzer, "ATR_PERIOD", 3)

    current_atr = get_current_atr("XBTEUR")

    assert calls["load"]["pair"] == "XBTEUR"
    assert calls["load"]["limit"] == 4
    assert calls["fetch"]["since"] == int(existing_df.iloc[-1]["time"]) + 1
    assert calls["saved_df"] is not None
    assert list(calls["saved_df"]["time"]) == [1767228300]
    assert pd.notna(calls["saved_df"]["atr"].iloc[-1])
    assert current_atr == calls["saved_df"]["atr"].iloc[-1]


def test_get_current_atr_returns_last_db_atr_when_fetch_returns_empty(monkeypatch) -> None:
    existing_df = pd.DataFrame(
        {
            "time": [1767225600, 1767226500],
            "dtime": pd.to_datetime([1767225600, 1767226500], unit="s", utc=True),
            "open": [100.0, 101.0],
            "high": [101.0, 102.0],
            "low": [99.0, 100.0],
            "close": [100.5, 101.5],
            "atr": [1.1, 1.9],
        }
    )

    monkeypatch.setattr(
        market_analyzer.db,
        "load_ohlc_data",
        lambda *_args, **_kwargs: existing_df.copy(),
    )
    monkeypatch.setattr(
        market_analyzer,
        "fetch_ohlc_data",
        lambda *_args, **_kwargs: pd.DataFrame(),
    )

    current_atr = get_current_atr("XBTEUR")

    assert current_atr == 1.9


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
