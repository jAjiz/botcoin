import math
import pandas as pd
import logging
from core.config import PAIRS, TRADING_PARAMS
from trading.market_analyzer import load_data, analyze_structural_noise

def calculate_k_stops(events_data, atr_median):
    if not events_data:
        return {"LV": None, "MV": None, "HV": None, "EV": None}
    
    lv = [e for e in events_data if e['atr_at_max'] < atr_median]
    mv = [e for e in events_data if atr_median <= e['atr_at_max'] < atr_median * 1.5]
    hv = [e for e in events_data if atr_median * 1.5 <= e['atr_at_max'] < atr_median * 3]
    ev = [e for e in events_data if e['atr_at_max'] >= atr_median * 3]
    
    def get_p75_k_value(events):
        if not events:
            return None
        value = pd.Series([e['k_value'] for e in events]).quantile(0.75)
        precision = 1  # One decimal place
        factor = 10 ** precision
        return math.ceil(value * factor) / factor
    
    return {
        "LV": get_p75_k_value(lv),
        "MV": get_p75_k_value(mv),
        "HV": get_p75_k_value(hv),
        "EV": get_p75_k_value(ev)
    }

def calculate_trading_parameters(pair):
    try:
        df = load_data(pair)
    except Exception as e:
        logging.error(f"Error loading data for {pair}: {e}")
        raise e
    
    PAIRS[pair]['atr_median'] = df['atr'].median()
    logging.info(f"ATR_MEDIAN → {PAIRS[pair]['atr_median']:,.1f}€")
    
    uptrend_data, downtrend_data = analyze_structural_noise(df)
    sell_k_stops = calculate_k_stops(uptrend_data, PAIRS[pair]['atr_median'])
    buy_k_stops = calculate_k_stops(downtrend_data, PAIRS[pair]['atr_median'])
    
    TRADING_PARAMS[pair]["sell"]["K_STOP"] = sell_k_stops
    TRADING_PARAMS[pair]["buy"]["K_STOP"] = buy_k_stops
    
    fmt = lambda k: f"{k:.2f}" if k is not None else "N/A"
    logging.info(f"K_STOP_SELL → LV:{fmt(sell_k_stops['LV'])} | MV:{fmt(sell_k_stops['MV'])} | HV:{fmt(sell_k_stops['HV'])} | EV:{fmt(sell_k_stops['EV'])}")
    logging.info(f"K_STOP_BUY  → LV:{fmt(buy_k_stops['LV'])} | MV:{fmt(buy_k_stops['MV'])} | HV:{fmt(buy_k_stops['HV'])} | EV:{fmt(buy_k_stops['EV'])}")

def get_volatility_level(pair, atr_val):
    atr_median = PAIRS[pair].get('atr_median')
    if atr_median is None:
        raise ValueError(f"ATR_MEDIAN not calculated for {pair}")
    
    if atr_val < atr_median:
        return "LV"
    elif atr_val < atr_median * 1.5:
        return "MV"
    elif atr_val < atr_median * 3:
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