import math
from typing import Any

import numpy as np
import pandas as pd

import core.database as db
import core.logging as logging
import core.runtime as runtime
from core.config import CANDLE_TIMEFRAME, PAIRS, STOP_PERCENTILES, TRADING_PARAMS
from core.config import VOLATILITY_LEVELS as LEVELS
from trading.market_analyzer import analyze_structural_noise

# Lookback windows (days) tried in ascending order during the stability sweep.
# The shortest window with the best K_STOP coverage wins.
_LOOKBACK_WINDOWS_DAYS = (30, 45, 60, 90, 120, 180, 240, 365)


def calculate_k_stops(pair: str, events: list[dict[str, Any]]) -> dict[str, float | None]:
    if not events:
        return {lvl: None for lvl in LEVELS}

    def get_pct_k_value(level, pct):
        level_values = []
        for event in events:
            vol_data = event.get("volatility_levels", {}).get(level)
            if not vol_data:
                continue
            k_value = vol_data.get("k_value")
            if k_value is None:
                continue
            level_values.append(k_value)

        if not level_values:
            return None

        value = pd.Series(level_values).quantile(pct)
        return math.ceil(value * 10) / 10

    return {lvl: get_pct_k_value(lvl, STOP_PERCENTILES[pair][lvl]) for lvl in LEVELS}


def _count_non_none(sell_k: dict, buy_k: dict) -> int:
    """Count non-None K_STOP slots across both sides (max 10 = 5 levels x 2)."""
    return sum(1 for v in list(sell_k.values()) + list(buy_k.values()) if v is not None)


def _select_lookback_window(pair: str, df_all: pd.DataFrame) -> tuple[int, list[dict], list[dict]]:
    """
    Auto-select the shortest lookback window whose K_STOP coverage is maximal.

    Iterates over _LOOKBACK_WINDOWS_DAYS from shortest to longest.  For each
    window, slices df_all to the last N days and computes K_STOPs.  Returns
    as soon as full coverage (10/10 non-None slots) is achieved.  If no window
    achieves full coverage, returns the window with the highest coverage.
    Falls back to the full dataset when no window has at least 10 rows.
    """
    if df_all.empty:
        return 365, [], []

    max_ts = int(df_all["time"].max())
    best_days = None
    best_score = -1
    best_result: tuple[list, list] | None = None

    for days in _LOOKBACK_WINDOWS_DAYS:
        cutoff_ts = max_ts - days * 86400
        df_window = df_all[df_all["time"] >= cutoff_ts]

        if len(df_window) < 10:
            continue

        up_events, down_events = analyze_structural_noise(df_window)
        sell_k = calculate_k_stops(pair, up_events)
        buy_k = calculate_k_stops(pair, down_events)
        score = _count_non_none(sell_k, buy_k)

        if score > best_score:
            best_score = score
            best_days = days
            best_result = (up_events, down_events)

        if score == 10:
            break

    if best_result is None:
        up_events, down_events = analyze_structural_noise(df_all)
        return 365, up_events, down_events

    return best_days, best_result[0], best_result[1]


def calculate_trading_parameters(pair: str, infoLog: bool = True) -> None:
    if infoLog:
        logging.info(f"Calculating trading parameters for {pair}...")

    try:
        df = db.load_ohlc_data(pair, CANDLE_TIMEFRAME).dropna(subset=["atr"])
    except Exception as e:
        logging.error(f"Error loading data for {pair}: {e}")
        raise e

    PAIRS[pair]["atr_20pct"] = np.percentile(df["atr"], 20)
    PAIRS[pair]["atr_50pct"] = np.percentile(df["atr"], 50)
    PAIRS[pair]["atr_80pct"] = np.percentile(df["atr"], 80)
    PAIRS[pair]["atr_95pct"] = np.percentile(df["atr"], 95)

    if infoLog:
        logging.info(
            "ATR percentiles → P20:{:,.1f}€ | P50:{:,.1f}€ | P80:{:,.1f}€ | P95:{:,.1f}€".format(
                PAIRS[pair]["atr_20pct"], PAIRS[pair]["atr_50pct"], PAIRS[pair]["atr_80pct"], PAIRS[pair]["atr_95pct"]
            )
        )

    best_days, up_events, down_events = _select_lookback_window(pair, df)
    sell_k_stops = calculate_k_stops(pair, up_events)
    buy_k_stops = calculate_k_stops(pair, down_events)

    TRADING_PARAMS[pair]["sell"]["K_STOP"] = sell_k_stops
    TRADING_PARAMS[pair]["buy"]["K_STOP"] = buy_k_stops

    runtime.update_calibration_cache(pair, best_days, up_events, down_events)

    if infoLog:
        logging.info(f"Lookback window: {best_days}d")

        def fmt(k):
            return f"{k:.2f}" if k is not None else "N/A"

        sell_msg = " | ".join(f"{lvl}:{fmt(sell_k_stops[lvl])}" for lvl in LEVELS)
        logging.info(f"K_STOP_SELL → {sell_msg}")
        buy_msg = " | ".join(f"{lvl}:{fmt(buy_k_stops[lvl])}" for lvl in LEVELS)
        logging.info(f"K_STOP_BUY  → {buy_msg}")


def get_volatility_level(pair: str, atr_val: float) -> str:
    if atr_val < PAIRS[pair]["atr_20pct"]:
        return "LL"
    elif atr_val < PAIRS[pair]["atr_50pct"]:
        return "LV"
    elif atr_val < PAIRS[pair]["atr_80pct"]:
        return "MV"
    elif atr_val < PAIRS[pair]["atr_95pct"]:
        return "HV"

    return "HH"


def get_k_stop(pair: str, side: str, atr_val: float) -> float | None:
    vol = get_volatility_level(pair, atr_val)

    k_stop = TRADING_PARAMS[pair][side]["K_STOP"].get(vol)
    if k_stop is not None:
        return k_stop

    op_side = "buy" if side == "sell" else "sell"
    k_stop = TRADING_PARAMS[pair][op_side]["K_STOP"].get(vol)
    if k_stop is not None:
        return k_stop

    idx = LEVELS.index(vol)
    for offset in range(1, len(LEVELS)):
        for neighbor in (idx - offset, idx + offset):
            if 0 <= neighbor < len(LEVELS):
                k_stop = TRADING_PARAMS[pair][side]["K_STOP"].get(LEVELS[neighbor])
                if k_stop is not None:
                    return k_stop

    return None
