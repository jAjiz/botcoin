import pandas as pd
import pytest

import trading.market_analyzer as market_analyzer
from trading.market_analyzer import (
    analyze_structural_noise,
    calculate_noise_between_pivots,
    detect_pivots,
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
    ratio_percentiles = {"p20": 0.012, "p50": 0.02, "p80": 0.028, "p95": 0.035}

    event = calculate_noise_between_pivots(df, (start, end), ratio_percentiles)

    assert event["type"] == "uptrend"
    assert event["price_change_pct"] > 0
    assert isinstance(event["volatility_levels"], dict)


def test_calculate_noise_between_pivots_buckets_by_the_atr_close_ratio() -> None:
    # Both segment candles carry the same absolute ATR, so only the ratio separates them.
    df = pd.DataFrame(
        {
            "dtime": pd.date_range("2026-01-01", periods=4, freq="15min"),
            "high": [100.0, 104.0, 52.0, 60.0],
            "low": [98.0, 100.0, 48.0, 58.0],
            "close": [99.0, 100.0, 50.0, 59.0],
            "atr": [1.0, 1.0, 1.0, 1.0],
        }
    )
    start = (0, "min", df.loc[0, "low"], df.loc[0, "dtime"])
    end = (3, "max", df.loc[3, "high"], df.loc[3, "dtime"])
    # Row 1 has a ratio of 0.01 (LL); row 2 has 0.02 (LV).
    ratio_percentiles = {"p20": 0.015, "p50": 0.05, "p80": 0.08, "p95": 0.1}

    event = calculate_noise_between_pivots(df, (start, end), ratio_percentiles)

    assert set(event["volatility_levels"]) == {"LL", "LV"}


def test_analyze_structural_noise_returns_two_event_lists(sample_dataframe: pd.DataFrame) -> None:
    df = sample_dataframe

    uptrend_events, downtrend_events = analyze_structural_noise(df, order=1)

    assert isinstance(uptrend_events, list)
    assert isinstance(downtrend_events, list)


def test_get_current_atr_warm_path_uses_seed_and_increments_wilder_atr(monkeypatch) -> None:
    # Two fetched rows: the new last closed and the current open.
    fetched_df = pd.DataFrame(
        {
            "time": [1767229200, 1767228300],
            "open": [104.0, 103.5],
            "high": [106.0, 105.5],
            "low": [103.5, 103.0],
            "close": [104.5, 105.0],
            "vwap": [104.4, 104.2],
            "volume": [14.0, 13.0],
            "count": [1, 1],
        }
    )
    fetched_last = 1767228300

    seed_df = pd.DataFrame([{"time": 1767227400, "close": 103.0, "atr": 2.0}])

    calls = {"load": None, "saved_df": None, "set_ctrl": None}

    def fake_load(pair, timeframe, since_time=None, before_time=None, limit=None):
        calls["load"] = {
            "pair": pair,
            "timeframe": timeframe,
            "since_time": since_time,
            "before_time": before_time,
            "limit": limit,
        }
        return seed_df.copy()

    def fake_fetch(pair, timeframe, since_param):
        return fetched_df.copy(), fetched_last

    def fake_save(pair, timeframe, df):
        calls["saved_df"] = df.copy()

    def fake_set_ctrl(key, value, updated_by=None):
        calls["set_ctrl"] = {"key": key, "value": value}

    monkeypatch.setattr(market_analyzer.db, "load_ohlc_data", fake_load)
    monkeypatch.setattr(market_analyzer, "fetch_ohlc_data", fake_fetch)
    monkeypatch.setattr(market_analyzer.db, "save_ohlc_data", fake_save)
    monkeypatch.setattr(market_analyzer.db, "get_control_value", lambda *_a, **_k: str(1767227400))
    monkeypatch.setattr(market_analyzer.db, "set_control_value", fake_set_ctrl)
    monkeypatch.setattr(market_analyzer, "ATR_PERIOD", 4)

    current_atr = get_current_atr("XBTEUR")

    assert calls["load"] == {
        "pair": "XBTEUR",
        "timeframe": market_analyzer.CANDLE_TIMEFRAME,
        "since_time": None,
        "before_time": 1767228300,
        "limit": 1,
    }
    # Wilder ATR walked forward from seed (prev_close=103.0, prev_atr=2.0).
    # Row 1 (t=1767228300, H=105.5, L=103.0, C=105.0): TR = max(2.5, 2.5, 0) = 2.5
    #   ATR = (2.0*3 + 2.5)/4 = 8.5/4 = 2.125
    # Row 2 (t=1767229200, H=106.0, L=103.5, C=104.5): TR = max(2.5, 1.0, 1.5) = 2.5
    #   ATR = (2.125*3 + 2.5)/4 = 8.875/4 = 2.21875
    assert calls["saved_df"] is not None
    saved = calls["saved_df"].set_index("time")
    assert saved.loc[1767228300, "atr"] == pytest.approx(2.125)
    assert saved.loc[1767229200, "atr"] == pytest.approx(2.21875)
    # Returned ATR is the one at `last` (the new last closed candle).
    assert current_atr == pytest.approx(2.125)
    assert calls["set_ctrl"]["value"] == str(fetched_last)


def test_get_current_atr_cold_start_seeds_wilder_from_scratch(monkeypatch) -> None:
    # 6 fetched rows, ATR_PERIOD=3 means the first 3 rows have NULL ATR.
    fetched_df = pd.DataFrame(
        {
            "time": [1000, 1900, 2800, 3700, 4600, 5500],
            "open": [100.0, 100.5, 101.0, 101.5, 102.0, 102.5],
            "high": [101.0, 101.5, 102.0, 102.5, 103.0, 103.5],
            "low": [99.5, 100.0, 100.5, 101.0, 101.5, 102.0],
            "close": [100.5, 101.0, 101.5, 102.0, 102.5, 103.0],
            "vwap": [100.3, 100.7, 101.2, 101.7, 102.2, 102.7],
            "volume": [10.0, 11.0, 12.0, 13.0, 14.0, 15.0],
            "count": [1, 1, 1, 1, 1, 1],
        }
    )
    fetched_last = 4600

    saved_df = {}

    def fake_save(pair, timeframe, df):
        saved_df["df"] = df.copy()

    monkeypatch.setattr(market_analyzer.db, "load_ohlc_data", lambda *_a, **_k: pd.DataFrame())
    monkeypatch.setattr(market_analyzer, "fetch_ohlc_data", lambda *_a, **_k: (fetched_df.copy(), fetched_last))
    monkeypatch.setattr(market_analyzer.db, "save_ohlc_data", fake_save)
    monkeypatch.setattr(market_analyzer.db, "get_control_value", lambda *_a, **_k: None)
    monkeypatch.setattr(market_analyzer.db, "set_control_value", lambda *_a, **_k: None)
    monkeypatch.setattr(market_analyzer, "ATR_PERIOD", 3)

    current_atr = get_current_atr("XBTEUR")

    saved = saved_df["df"].set_index("time").sort_index()
    # First 3 rows have no ATR (need ATR_PERIOD candles before seed).
    assert pd.isna(saved.loc[1000, "atr"])
    assert pd.isna(saved.loc[1900, "atr"])
    assert pd.isna(saved.loc[2800, "atr"])
    # Seed at index 3 (t=3700) = mean of TR[1..3]. With these rows TR is 1.5 each.
    assert saved.loc[3700, "atr"] == pytest.approx(1.5)
    # Incremental afterwards.
    assert saved.loc[4600, "atr"] == pytest.approx(1.5)
    assert current_atr == pytest.approx(1.5)


def test_get_current_atr_first_run_passes_none_since(monkeypatch) -> None:
    captured = {}

    def fake_fetch(pair, timeframe, since_param):
        captured["since"] = since_param
        return pd.DataFrame(), 1767228300

    monkeypatch.setattr(market_analyzer.db, "load_ohlc_data", lambda *_a, **_k: pd.DataFrame())
    monkeypatch.setattr(market_analyzer, "fetch_ohlc_data", fake_fetch)
    monkeypatch.setattr(market_analyzer.db, "save_ohlc_data", lambda *_a, **_k: None)
    monkeypatch.setattr(market_analyzer.db, "get_control_value", lambda *_a, **_k: None)
    monkeypatch.setattr(market_analyzer.db, "set_control_value", lambda *_a, **_k: None)

    get_current_atr("XBTEUR")

    assert captured["since"] is None


def test_get_current_atr_returns_last_db_atr_when_fetch_returns_empty(monkeypatch) -> None:
    latest_df = pd.DataFrame(
        {
            "time": [1767226500],
            "dtime": pd.to_datetime([1767226500], unit="s", utc=True),
            "open": [101.0],
            "high": [102.0],
            "low": [100.0],
            "close": [101.5],
            "atr": [1.9],
        }
    )

    set_calls = {}

    def fake_set_ctrl(key, value, updated_by=None):
        set_calls["key"] = key
        set_calls["value"] = value

    monkeypatch.setattr(market_analyzer.db, "load_ohlc_data", lambda *_a, **_k: latest_df.copy())
    monkeypatch.setattr(market_analyzer, "fetch_ohlc_data", lambda *_a, **_k: (pd.DataFrame(), 1767227400))
    monkeypatch.setattr(market_analyzer.db, "get_control_value", lambda *_a, **_k: "1767226500")
    monkeypatch.setattr(market_analyzer.db, "set_control_value", fake_set_ctrl)

    current_atr = get_current_atr("XBTEUR")

    assert current_atr == 1.9
    # `last` is still persisted even when no candle rows are returned.
    assert set_calls["value"] == "1767227400"


def test_get_current_atr_returns_last_db_atr_when_fetch_returns_none(monkeypatch) -> None:
    latest_df = pd.DataFrame(
        {
            "time": [1767226500],
            "dtime": pd.to_datetime([1767226500], unit="s", utc=True),
            "open": [101.0],
            "high": [102.0],
            "low": [100.0],
            "close": [101.5],
            "atr": [1.9],
        }
    )

    monkeypatch.setattr(market_analyzer.db, "load_ohlc_data", lambda *_a, **_k: latest_df.copy())
    monkeypatch.setattr(market_analyzer, "fetch_ohlc_data", lambda *_a, **_k: None)
    monkeypatch.setattr(market_analyzer.db, "get_control_value", lambda *_a, **_k: "1767226500")

    current_atr = get_current_atr("XBTEUR")

    assert current_atr == 1.9


@pytest.mark.timeout(10)
def test_detect_pivots_terminates_on_flat_data():
    """Different-type pivots with exactly equal prices previously made the
    false-pivot removal loop spin forever (no branch advanced `i`)."""
    n = 60
    df = pd.DataFrame(
        {
            "high": [100.0] * n,
            "low": [100.0] * n,
            "dtime": pd.date_range("2026-01-01", periods=n, freq="15min"),
        }
    )
    pivots = market_analyzer.detect_pivots(df, order=5)
    assert isinstance(pivots, list)
    assert len(pivots) <= 1


# --- calibration schedule ---------------------------------------------------


def _frame(n: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "dtime": pd.date_range("2026-01-01", periods=n, freq="15min"),
            "close": [100.0 + i for i in range(n)],
            "high": [101.0 + i for i in range(n)],
            "low": [99.0 + i for i in range(n)],
            "atr": [2.0] * n,
        }
    )


def test_k_values_by_level_keeps_every_level_even_when_no_event_reached_it() -> None:
    events = [{"volatility_levels": {"MV": {"k_value": 3.0}, "HV": {"k_value": None}}}]

    by_level = market_analyzer.k_values_by_level(events)

    assert set(by_level) == {"LL", "LV", "MV", "HV", "HH"}
    assert by_level["MV"].tolist() == [3.0]
    assert by_level["HV"].size == 0


def test_build_calibration_inputs_places_a_point_every_cadence_starting_at_bar_zero() -> None:
    df = _frame(20)

    points = market_analyzer.build_calibration_inputs(df, df, recalib_bars=5)

    assert [p.at for p in points] == [0, 5, 10, 15]


def test_build_calibration_inputs_calibrates_each_point_from_the_past_only(monkeypatch) -> None:
    df_full = _frame(20)
    df = df_full.iloc[10:].reset_index(drop=True)
    seen: list[int] = []
    monkeypatch.setattr(market_analyzer, "analyze_structural_noise", lambda d: (seen.append(len(d)), ([], []))[1])

    points = market_analyzer.build_calibration_inputs(df_full, df, recalib_bars=5)

    # A slice starting at bar 10 still calibrates over all 11, then 16 bars of history up to its own bar.
    assert [p.at for p in points] == [0, 5]
    assert seen == [11, 16]


@pytest.mark.parametrize("recalib_bars", [0, -1])
def test_build_calibration_inputs_is_empty_when_recalibration_is_disabled(recalib_bars: int) -> None:
    df = _frame(10)

    assert market_analyzer.build_calibration_inputs(df, df, recalib_bars) == ()


def test_build_calibration_inputs_is_empty_for_an_empty_window() -> None:
    df = _frame(10)

    assert market_analyzer.build_calibration_inputs(df, df.iloc[0:0], recalib_bars=5) == ()
