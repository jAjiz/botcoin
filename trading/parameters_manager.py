import math
from typing import Any

import numpy as np
import pandas as pd

import core.database as db
import core.logging as logging
import core.runtime as runtime
from core.config import CANDLE_TIMEFRAME, PAIRS, STOP_PERCENTILES, TRADING_PARAMS
from core.config import VOLATILITY_LEVELS as LEVELS
from trading.engine import PairCalibration
from trading.market_analyzer import analyze_structural_noise


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


def calculate_trading_parameters(pair: str, infoLog: bool = True) -> None:
    if infoLog:
        logging.info(f"Calculating trading parameters for {pair}...")

    try:
        df = db.load_ohlc_data(pair, CANDLE_TIMEFRAME).dropna(subset=["atr"]).sort_values("time").reset_index(drop=True)
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

    uptrend_events, downtrend_events = analyze_structural_noise(df)
    sell_k_stops = calculate_k_stops(pair, uptrend_events)
    buy_k_stops = calculate_k_stops(pair, downtrend_events)

    TRADING_PARAMS[pair]["K_STOP"] = {"sell": sell_k_stops, "buy": buy_k_stops}

    if infoLog:
        pct_msg = " | ".join(f"{lvl}:{STOP_PERCENTILES[pair][lvl]}" for lvl in LEVELS)
        logging.info(f"Stop percentiles → {pct_msg}")

        def fmt(k):
            return f"{k:.2f}" if k is not None else "N/A"

        sell_msg = " | ".join(f"{lvl}:{fmt(sell_k_stops[lvl])}" for lvl in LEVELS)
        logging.info(f"K_STOP_SELL → {sell_msg}")
        buy_msg = " | ".join(f"{lvl}:{fmt(buy_k_stops[lvl])}" for lvl in LEVELS)
        logging.info(f"K_STOP_BUY  → {buy_msg}")

    # Dual-write: in addition to the globals above (the live-bot read path,
    # unchanged), publish the calibration to the in-process cache so backtest can
    # reuse the events + ATR percentiles without re-running analyze_structural_noise.
    runtime.update_pair_calibration(
        pair,
        up_events=uptrend_events,
        down_events=downtrend_events,
        atr_p20=float(PAIRS[pair]["atr_20pct"]),
        atr_p50=float(PAIRS[pair]["atr_50pct"]),
        atr_p80=float(PAIRS[pair]["atr_80pct"]),
        atr_p95=float(PAIRS[pair]["atr_95pct"]),
        row_count=len(df),
    )


def build_calibration(pair: str) -> PairCalibration:
    """Build a PairCalibration from current globals. Used by the API to seed
    EngineConfig from live state without re-running analyze_structural_noise."""
    return PairCalibration(
        atr_p20=float(PAIRS[pair]["atr_20pct"]),
        atr_p50=float(PAIRS[pair]["atr_50pct"]),
        atr_p80=float(PAIRS[pair]["atr_80pct"]),
        atr_p95=float(PAIRS[pair]["atr_95pct"]),
        k_stop_buy=dict(TRADING_PARAMS[pair]["K_STOP"].get("buy") or {}),
        k_stop_sell=dict(TRADING_PARAMS[pair]["K_STOP"].get("sell") or {}),
    )


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

    k_stop = TRADING_PARAMS[pair]["K_STOP"][side].get(vol)
    if k_stop is not None:
        return k_stop

    # Try opposite side K_STOP as fallback
    op_side = "buy" if side == "sell" else "sell"
    k_stop = TRADING_PARAMS[pair]["K_STOP"][op_side].get(vol)
    if k_stop is not None:
        return k_stop

    # Search neighboring levels
    idx = LEVELS.index(vol)
    for offset in range(1, len(LEVELS)):
        for neighbor in (idx - offset, idx + offset):
            if 0 <= neighbor < len(LEVELS):
                k_stop = TRADING_PARAMS[pair]["K_STOP"][side].get(LEVELS[neighbor])
                if k_stop is not None:
                    return k_stop

    return None
