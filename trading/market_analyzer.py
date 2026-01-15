import pandas as pd
import numpy as np
import sys
import os
from pathlib import Path
from scipy.signal import argrelextrema

# Ensure sibling packages are importable when running as a script.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import MARKET_ANALYZER, CANDLE_TIMEFRAME

DEFAULT_ORDER = MARKET_ANALYZER["DEFAULT_ORDER"]
MINIMUM_CHANGE_PCT = MARKET_ANALYZER["MINIMUM_CHANGE_PCT"]

def get_args():
    args = {'pair': None, 'show_events': False, 'order': DEFAULT_ORDER, 'volatility_level': None}

    for arg in sys.argv[1:]:
        if arg.startswith('PAIR='):
            args['pair'] = arg.split('=')[1].upper()
        elif arg.startswith('ORDER='):
            args['order'] = int(arg.split('=')[1])
        elif arg == 'SHOW_EVENTS':
            args['show_events'] = True
        elif arg.startswith('Volatility='):
            args['volatility_level'] = arg.split('=')[1].upper()
    
    if not args['pair']:
        print("Error: PAIR parameter is required.")
        print("Usage: python market_noise_analyzer.py " \
            "PAIR=ETHEUR [ORDER=20] [SHOW_EVENTS] [Volatility=LV|MV|HV|EV]")
        sys.exit(1)
    
    return args

def load_data(pair):
    atr_file = f"data/{pair}_ohlc_data_{CANDLE_TIMEFRAME}min.csv"
    if not os.path.exists(atr_file):
        raise FileNotFoundError(f"File not found: {atr_file}")
    
    try:
        df = pd.read_csv(atr_file)
        df.columns = [c.strip().lower() for c in df.columns]
    except Exception as e:
        raise Exception(f"Error reading file: {e}")
    
    required_cols = {'low', 'high', 'dtime', 'atr'}
    if not required_cols.issubset(set(df.columns)):
        raise ValueError(f"Missing required columns. Need: {required_cols}")
    
    df.dropna(inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df

def detect_pivots(df, order=DEFAULT_ORDER):
    ilocs_min = argrelextrema(df['low'].values, np.less_equal, order=order)[0]
    ilocs_max = argrelextrema(df['high'].values, np.greater_equal, order=order)[0]
    
    pivots = []
    for i in ilocs_min:
        pivots.append((i, 'min', df['low'].iloc[i], df.iloc[i]['dtime']))
    for i in ilocs_max:
        pivots.append((i, 'max', df['high'].iloc[i], df.iloc[i]['dtime']))
    
    pivots.sort(key=lambda x: x[0])

    # Remove false pivots
    i = 0    
    while i < len(pivots) - 1:
        _, curr_type, curr_price, _ = pivots[i]
        _, next_type, next_price, _ = pivots[i + 1]
        
        if curr_type == next_type:
            if (curr_type == 'max' and curr_price >= next_price) or \
               (curr_type == 'min' and curr_price <= next_price):
                del pivots[i + 1]
            else:
                del pivots[i]
        elif curr_price != next_price:
            if abs(curr_price - next_price) / curr_price < MINIMUM_CHANGE_PCT:
                del pivots[i + 1]
            else:
                i += 1

    return pivots
    
def calculate_noise_between_pivots(df, pivot_pair):
    start_idx, start_type, start_price, start_dtime = pivot_pair[0]
    end_idx, end_type, end_price, end_dtime = pivot_pair[1]
    
    price_change_pct = abs((end_price - start_price) / start_price)
    segment = df.iloc[start_idx+1:end_idx]
    
    if len(segment) == 0:
        return None
    
    if start_type == 'min' and end_type == 'max':
        # Uptrend: calculate drawdown
        rolling_max = segment['high'].expanding().max()
        drawdowns = rolling_max - segment['low']
        idx_max = drawdowns.idxmax()
        max_value = drawdowns.loc[idx_max]
    elif start_type == 'max' and end_type == 'min':
        # Downtrend: calculate bounce
        rolling_min = segment['low'].expanding().min()
        bounces = segment['high'] - rolling_min
        idx_max = bounces.idxmax()
        max_value = bounces.loc[idx_max]
    else:
        return None
    
    atr_at_max = segment.loc[idx_max, 'atr']
    k_value = max_value / atr_at_max if atr_at_max > 0 else 0

    return {
        'type': 'uptrend' if start_type == 'min' else 'downtrend',
        'start_dtime': start_dtime,
        'end_dtime': end_dtime,
        'price_change_pct': price_change_pct,
        'price_change_k': (end_price - start_price) / atr_at_max,
        'max_value': max_value,
        'atr_at_max': atr_at_max,
        'k_value': k_value
    }

def filter_events_by_volatility(events, volatility_level, atr_50pct, atr_80pct, atr_95pct):
    """Filter events by volatility level using the same criteria as calculate_k_stops."""
    if not volatility_level:
        return events
    
    filtered = []
    for e in events:
        atr_at_max = e['atr_at_max']
        if volatility_level == 'LV' and atr_at_max < atr_50pct:
            filtered.append(e)
        elif volatility_level == 'MV' and atr_50pct <= atr_at_max < atr_80pct:
            filtered.append(e)
        elif volatility_level == 'HV' and atr_80pct <= atr_at_max < atr_95pct:
            filtered.append(e)
        elif volatility_level == 'EV' and atr_at_max >= atr_95pct:
            filtered.append(e)
    
    return filtered

def analyze_structural_noise(df, order=DEFAULT_ORDER, print_results=False, show_events=False,
                              volatility_level=None):
    # Detect and filter pivots
    pivots = detect_pivots(df, order)
    
    # Calculate noise for each pivot pair
    uptrend_data = []
    downtrend_data = []
    
    for i in range(1, len(pivots)):
        event = calculate_noise_between_pivots(df, (pivots[i-1], pivots[i]))
        if event:
            if event['type'] == 'uptrend':
                uptrend_data.append(event)
            else:
                downtrend_data.append(event)
    
    # Apply volatility filter if requested
    if volatility_level:
        atr_50pct = np.percentile(df['atr'], 50)
        atr_80pct = np.percentile(df['atr'], 80)
        atr_95pct = np.percentile(df['atr'], 95)
        
        uptrend_data = filter_events_by_volatility(uptrend_data, volatility_level, atr_50pct, atr_80pct, atr_95pct)
        downtrend_data = filter_events_by_volatility(downtrend_data, volatility_level, atr_50pct, atr_80pct, atr_95pct)
    
    if print_results:
        print(f"--- Analyzing Market Structure (minimum change {MINIMUM_CHANGE_PCT*100:.2f}%) ---")
        print_statistics(uptrend_data, "UPTREND NOISE (Stop Loss configuration)")
        print_statistics(downtrend_data, "DOWNTREND NOISE (Reentry Stop configuration)")
        
        if show_events:
            print_events_detail(uptrend_data, "UPTREND EVENTS")
            print_events_detail(downtrend_data, "DOWNTREND EVENTS")

    return uptrend_data, downtrend_data

def print_statistics(events, title):
    if not events:
        print(f"\n❌ No events detected for {title}\n")
        return
    
    k_values = [e['k_value'] for e in events]
    s = pd.Series(k_values)
    
    print(f"\n=== {title} ===")
    print(f"Events: {len(s)} | Average: {s.mean():.2f} ATR")
    print(f"Percentile 50%: {s.quantile(0.50):.2f} ATR (Very Tight)")
    print(f"Percentile 75%: {s.quantile(0.75):.2f} ATR (Standard)")
    print(f"Percentile 90%: {s.quantile(0.90):.2f} ATR (Safe)")
    print(f"Percentile 95%: {s.quantile(0.95):.2f} ATR (Protected)")
    print(f"Percentile 100%: {s.quantile(1.00):.2f} ATR (Extreme)")

def print_events_detail(events, title):
    if not events:
        return
    
    print(f"\n=== {title} ===")
    print(f"{'From':<20} | {'To':<20} | {'Change %':>10} | {'Change K':>9} | {'Max Value':>10} "
          f"| {'ATR at max':>10} | {'K Value':>8}")
    print("-" * 135)
    
    for event in events:
        change_pct = event['price_change_pct'] * 100
        print(f"{str(event['start_dtime']):<20} | {str(event['end_dtime']):<20} "
              f"| {change_pct:>9.2f}% | {event['price_change_k']:>9.2f} "
              f"| {event['max_value']:>10.1f} | {event['atr_at_max']:>10.1f} | {event['k_value']:>8.2f}")

if __name__ == "__main__":
    args = get_args()
    analyze_structural_noise(
        load_data(args['pair']), 
        args['order'], 
        True, 
        args['show_events'], 
        args['volatility_level']
    )