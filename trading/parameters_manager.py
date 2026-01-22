import math
import logging
import numpy as np
import pandas as pd
from core.config import PAIRS, TRADING_PARAMS, STOP_PERCENTILES, VOLATILITY_LEVELS as LEVELS
from trading.market_analyzer import load_data, analyze_structural_noise

def calculate_k_stops(pair, events):
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

def calculate_trading_parameters(pair):
    logging.info(f"Calculating trading parameters...")
    try:
        df = load_data(pair)
    except Exception as e:
        logging.error(f"Error loading data for {pair}: {e}")
        raise e
    
    PAIRS[pair]["atr_20pct"] = np.percentile(df["atr"], 20)
    PAIRS[pair]["atr_50pct"] = np.percentile(df["atr"], 50)
    PAIRS[pair]["atr_80pct"] = np.percentile(df["atr"], 80)
    PAIRS[pair]["atr_95pct"] = np.percentile(df["atr"], 95)
    logging.info(
        "ATR percentiles → P20:{:,.1f}€ | P50:{:,.1f}€ | P80:{:,.1f}€ | P95:{:,.1f}€".format(
            PAIRS[pair]["atr_20pct"], PAIRS[pair]["atr_50pct"], PAIRS[pair]["atr_80pct"], PAIRS[pair]["atr_95pct"])
    )
    
    uptrend_events, downtrend_events = analyze_structural_noise(df)
    sell_k_stops = calculate_k_stops(pair, uptrend_events)
    buy_k_stops = calculate_k_stops(pair, downtrend_events)
    
    TRADING_PARAMS[pair]["sell"]["K_STOP"] = sell_k_stops
    TRADING_PARAMS[pair]["buy"]["K_STOP"] = buy_k_stops
    
    fmt = lambda k: f"{k:.2f}" if k is not None else "N/A"
    sell_msg = " | ".join(f"{lvl}:{fmt(sell_k_stops[lvl])}" for lvl in LEVELS)
    buy_msg = " | ".join(f"{lvl}:{fmt(buy_k_stops[lvl])}" for lvl in LEVELS)
    logging.info(f"K_STOP_SELL → {sell_msg}")
    logging.info(f"K_STOP_BUY  → {buy_msg}")

def get_volatility_level(pair, atr_val):
    if atr_val < PAIRS[pair]["atr_20pct"]:
        return "LL"
    elif atr_val < PAIRS[pair]["atr_50pct"]:
        return "LV"
    elif atr_val < PAIRS[pair]["atr_80pct"]:
        return "MV"
    elif atr_val < PAIRS[pair]["atr_95pct"]:
        return "HV"
    
    return "HH"

def get_k_stop(pair, side, atr_val):
    vol = get_volatility_level(pair, atr_val)
    
    k_stop = TRADING_PARAMS[pair][side]["K_STOP"].get(vol)
    if k_stop is not None:
        return k_stop
    
    # Try opposite side K_STOP as fallback
    op_side = "buy" if side == "sell" else "sell"
    k_stop = TRADING_PARAMS[pair][op_side]["K_STOP"].get(vol)
    if k_stop is not None:
        return k_stop
    
    # Search neighboring levels
    idx = LEVELS.index(vol)
    for offset in range(1, len(LEVELS)):
        for neighbor in (idx - offset, idx + offset):
            if 0 <= neighbor < len(LEVELS):
                k_stop = TRADING_PARAMS[pair][side]["K_STOP"].get(LEVELS[neighbor])
                if k_stop is not None:
                    return k_stop
    
    return None