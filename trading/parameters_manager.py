import math
import pandas as pd
import numpy as np
import logging
from core.config import PAIRS, TRADING_PARAMS
from trading.market_analyzer import load_data, analyze_structural_noise

def calculate_k_stops(pair, events_data):
    if not events_data:
        return {"LV": None, "MV": None, "HV": None, "EV": None}
    
    atr_50pct = PAIRS[pair]['atr_50pct']
    atr_80pct = PAIRS[pair]['atr_80pct']
    atr_95pct = PAIRS[pair]['atr_95pct']

    lv = [e for e in events_data if e['atr_at_max'] < atr_50pct]
    mv = [e for e in events_data if atr_50pct <= e['atr_at_max'] < atr_80pct]
    hv = [e for e in events_data if atr_80pct <= e['atr_at_max'] < atr_95pct]
    ev = [e for e in events_data if e['atr_at_max'] >= atr_95pct]
    
    def get_pct_k_value(events, pct):
        if not events:
            return None
        value = pd.Series([e['k_value'] for e in events]).quantile(pct)
        precision = 1  # One decimal place
        factor = 10 ** precision
        return math.ceil(value * factor) / factor
    
    return {
        "LV": get_pct_k_value(lv, 0.90), # Percentile 90 for LV
        "MV": get_pct_k_value(mv, 0.90), # Percentile 90 for MV
        "HV": get_pct_k_value(hv, 0.75), # Percentile 75 for HV
        "EV": get_pct_k_value(ev, 0.75) # Percentile 75 for EV
    }

def calculate_trading_parameters(pair):
    logging.info(f"Calculating trading parameters...")
    try:
        df = load_data(pair)
    except Exception as e:
        logging.error(f"Error loading data for {pair}: {e}")
        raise e
    
    PAIRS[pair]['atr_50pct'] = np.percentile(df['atr'], 50)
    PAIRS[pair]['atr_80pct'] = np.percentile(df['atr'], 80)
    PAIRS[pair]['atr_95pct'] = np.percentile(df['atr'], 95)
    logging.info(f"ATR percentiles → 50:{PAIRS[pair]['atr_50pct']:,.1f}€ | 80:{PAIRS[pair]['atr_80pct']:,.1f}€ | 95:{PAIRS[pair]['atr_95pct']:,.1f}€")
    
    uptrend_data, downtrend_data = analyze_structural_noise(df)
    sell_k_stops = calculate_k_stops(pair, uptrend_data)
    buy_k_stops = calculate_k_stops(pair, downtrend_data)
    
    TRADING_PARAMS[pair]["sell"]["K_STOP"] = sell_k_stops
    TRADING_PARAMS[pair]["buy"]["K_STOP"] = buy_k_stops
    
    fmt = lambda k: f"{k:.2f}" if k is not None else "N/A"
    logging.info(f"K_STOP_SELL → LV:{fmt(sell_k_stops['LV'])} | MV:{fmt(sell_k_stops['MV'])} | HV:{fmt(sell_k_stops['HV'])} | EV:{fmt(sell_k_stops['EV'])}")
    logging.info(f"K_STOP_BUY  → LV:{fmt(buy_k_stops['LV'])} | MV:{fmt(buy_k_stops['MV'])} | HV:{fmt(buy_k_stops['HV'])} | EV:{fmt(buy_k_stops['EV'])}")

def get_volatility_level(pair, atr_val):
    atr_50pct = PAIRS[pair]['atr_50pct']
    atr_80pct = PAIRS[pair]['atr_80pct']
    atr_95pct = PAIRS[pair]['atr_95pct']
    
    if atr_val < atr_50pct:
        return "LV"
    elif atr_val < atr_80pct:
        return "MV"
    elif atr_val < atr_95pct:
        return "HV"
    else:
        return "EV"

def get_k_stop(pair, side, atr_val):
    vol = get_volatility_level(pair, atr_val)
    
    k_stop = TRADING_PARAMS[pair][side]["K_STOP"].get(vol)
    if k_stop is not None:
        return k_stop
    
    op_side = "buy" if side == "sell" else "sell"
    k_stop = TRADING_PARAMS[pair][op_side]["K_STOP"].get(vol)
    if k_stop is not None:
        return k_stop
    
    vols = ["LV", "MV", "HV", "EV"]
    idx = vols.index(vol)
    max_offset = len(vols) - 1
    for offset in range(1, max_offset + 1):
        for neighbor in (idx - offset, idx + offset):
            if 0 <= neighbor < len(vols):
                k_stop = TRADING_PARAMS[pair][side]["K_STOP"].get(vols[neighbor])
                if k_stop is not None:
                    return k_stop
    
    return None