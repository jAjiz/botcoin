from typing import Any

import numpy as np
import pandas as pd
from scipy.signal import argrelextrema

import core.database as db
import core.logging as logging
from core.config import ATR_PERIOD, CANDLE_TIMEFRAME, MARKET_ANALYZER
from exchange.kraken import fetch_ohlc_data

DEFAULT_ORDER = MARKET_ANALYZER["DEFAULT_ORDER"]
MINIMUM_CHANGE_PCT = MARKET_ANALYZER["MINIMUM_CHANGE_PCT"]


def get_current_atr(pair: str) -> float | None:
    try:
        control_key = f"ohlc_last_{pair}_{CANDLE_TIMEFRAME}"

        stored_since = db.get_control_value(control_key)
        since_ts = int(stored_since) if stored_since is not None else None

        fetch_result = fetch_ohlc_data(pair, CANDLE_TIMEFRAME, since_ts)
        if fetch_result is None:
            return _latest_db_atr(pair)

        xchange_ohlc, last_ts = fetch_result

        if xchange_ohlc.empty:
            db.set_control_value(control_key, str(last_ts))
            return _latest_db_atr(pair)

        xchange_ohlc = xchange_ohlc.sort_values("time").reset_index(drop=True)
        earliest_fetched = int(xchange_ohlc.iloc[0]["time"])

        seed = db.load_ohlc_data(pair, CANDLE_TIMEFRAME, before_time=earliest_fetched, limit=1)
        if not seed.empty and pd.notna(seed.iloc[0]["atr"]):
            xchange_ohlc["atr"] = _wilder_atr_incremental(
                xchange_ohlc,
                prev_close=float(seed.iloc[0]["close"]),
                prev_atr=float(seed.iloc[0]["atr"]),
                period=ATR_PERIOD,
            )
        else:
            xchange_ohlc["atr"] = _wilder_atr_from_scratch(xchange_ohlc, ATR_PERIOD)

        db.save_ohlc_data(pair, CANDLE_TIMEFRAME, xchange_ohlc.sort_values("time", ascending=False))
        db.set_control_value(control_key, str(last_ts))

        match = xchange_ohlc[xchange_ohlc["time"] == last_ts]
        if not match.empty and pd.notna(match["atr"].iloc[0]):
            return float(match["atr"].iloc[0])
        return _latest_db_atr(pair)
    except Exception as e:
        logging.error(f"Error getting ATR for {pair}: {e}")
        return None


def _latest_db_atr(pair: str) -> float | None:
    latest = db.load_ohlc_data(pair, CANDLE_TIMEFRAME, limit=1)
    if not latest.empty and pd.notna(latest.iloc[0]["atr"]):
        return float(latest.iloc[0]["atr"])
    return None


def _wilder_atr_incremental(df: pd.DataFrame, prev_close: float, prev_atr: float, period: int) -> list[float]:
    atrs: list[float] = []
    close = prev_close
    atr = prev_atr
    for high, low, c in zip(df["high"], df["low"], df["close"], strict=True):
        tr = max(high - low, abs(high - close), abs(low - close))
        atr = (atr * (period - 1) + tr) / period
        atrs.append(atr)
        close = c
    return atrs


def _wilder_atr_from_scratch(df: pd.DataFrame, period: int) -> list[float | None]:
    n = len(df)
    result: list[float | None] = [None] * n
    if n <= period:
        return result

    highs = df["high"].tolist()
    lows = df["low"].tolist()
    closes = df["close"].tolist()

    trs = [0.0]
    for i in range(1, n):
        trs.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )

    atr = sum(trs[1 : period + 1]) / period
    result[period] = atr
    for i in range(period + 1, n):
        atr = (atr * (period - 1) + trs[i]) / period
        result[i] = atr

    return result


def detect_pivots(df: pd.DataFrame, order: int = DEFAULT_ORDER) -> list[tuple[int, str, float, pd.Timestamp]]:
    ilocs_min = argrelextrema(df["low"].values, np.less_equal, order=order)[0]
    ilocs_max = argrelextrema(df["high"].values, np.greater_equal, order=order)[0]

    pivots = []
    for i in ilocs_min:
        pivots.append((i, "min", df["low"].iloc[i], df.iloc[i]["dtime"]))
    for i in ilocs_max:
        pivots.append((i, "max", df["high"].iloc[i], df.iloc[i]["dtime"]))

    pivots.sort(key=lambda x: x[0])

    i = 0
    while i < len(pivots) - 1:
        _, curr_type, curr_price, _ = pivots[i]
        _, next_type, next_price, _ = pivots[i + 1]

        if curr_type == next_type:
            if (curr_type == "max" and curr_price >= next_price) or (curr_type == "min" and curr_price <= next_price):
                del pivots[i + 1]
            else:
                del pivots[i]
        elif curr_price != next_price:
            if abs(curr_price - next_price) / curr_price < MINIMUM_CHANGE_PCT:
                del pivots[i + 1]
            else:
                i += 1

    return pivots


def calculate_noise_between_pivots(
    df: pd.DataFrame,
    pivot_pair: tuple[tuple[int, str, float, pd.Timestamp], tuple[int, str, float, pd.Timestamp]],
    atr_percentiles: dict[str, float],
) -> dict[str, Any]:
    start_idx, start_type, start_price, start_dtime = pivot_pair[0]
    end_idx, end_type, end_price, end_dtime = pivot_pair[1]

    price_change_pct = abs((end_price - start_price) / start_price)
    segment = df.iloc[start_idx + 1 : end_idx]

    if len(segment) == 0:
        return {}

    if start_type == "min" and end_type == "max":
        rolling_max = segment["high"].expanding().max()
        drawdowns = rolling_max - segment["low"]
        segment_copy = segment.copy()
        segment_copy["k_values"] = drawdowns / segment_copy["atr"].replace(0, np.nan)
    elif start_type == "max" and end_type == "min":
        rolling_min = segment["low"].expanding().min()
        bounces = segment["high"] - rolling_min
        segment_copy = segment.copy()
        segment_copy["k_values"] = bounces / segment_copy["atr"].replace(0, np.nan)
    else:
        return {}

    volatility_levels = {}
    vol_ranges = {
        "LL": (0, atr_percentiles["p20"]),
        "LV": (atr_percentiles["p20"], atr_percentiles["p50"]),
        "MV": (atr_percentiles["p50"], atr_percentiles["p80"]),
        "HV": (atr_percentiles["p80"], atr_percentiles["p95"]),
        "HH": (atr_percentiles["p95"], float("inf")),
    }

    for vol_level, (min_atr, max_atr) in vol_ranges.items():
        mask = (segment_copy["atr"] >= min_atr) & (segment_copy["atr"] < max_atr)
        if not mask.any():
            continue

        vol_segment = segment_copy[mask]
        idx_max = vol_segment["k_values"].idxmax()

        if start_type == "min" and end_type == "max":
            max_value = rolling_max.loc[idx_max] - segment.loc[idx_max, "low"]
        else:
            max_value = segment.loc[idx_max, "high"] - rolling_min.loc[idx_max]

        k_value = vol_segment["k_values"].loc[idx_max]
        atr_at_max = segment.loc[idx_max, "atr"]

        volatility_levels[vol_level] = {"max_value": max_value, "atr_at_max": atr_at_max, "k_value": k_value}

    event = {
        "type": "uptrend" if start_type == "min" else "downtrend",
        "start_dtime": start_dtime,
        "end_dtime": end_dtime,
        "price_change_pct": price_change_pct,
        "volatility_levels": volatility_levels,
    }

    return event


def analyze_structural_noise(
    df: pd.DataFrame,
    order: int = DEFAULT_ORDER,
) -> tuple[list[dict], list[dict]]:
    pivots = detect_pivots(df, order)

    atr_percentiles = {
        "p20": np.percentile(df["atr"], 20),
        "p50": np.percentile(df["atr"], 50),
        "p80": np.percentile(df["atr"], 80),
        "p95": np.percentile(df["atr"], 95),
    }

    uptrend_events = []
    downtrend_events = []
    for i in range(1, len(pivots)):
        event = calculate_noise_between_pivots(df, (pivots[i - 1], pivots[i]), atr_percentiles)
        if event and event["volatility_levels"]:
            if event["type"] == "uptrend":
                uptrend_events.append(event)
            else:
                downtrend_events.append(event)

    return uptrend_events, downtrend_events
