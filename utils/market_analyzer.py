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

from core.config import MARKET_ANALYZER, ATR_INTERVAL

DEFAULT_ORDER = MARKET_ANALYZER["DEFAULT_ORDER"]

def get_args():
    args = {'pair': None, 'show_events': False, 'order': DEFAULT_ORDER}

    for arg in sys.argv[1:]:
        if arg.startswith('PAIR='):
            args['pair'] = arg.split('=')[1].upper()
        elif arg.startswith('ORDER='):
            args['order'] = int(arg.split('=')[1])
        elif arg == 'SHOW_EVENTS':
            args['show_events'] = True
    
    if not args['pair']:
        print("Error: PAIR parameter is required.")
        print("Usage: python market_noise_analyzer.py PAIR=ETHEUR [ORDER=20] [SHOW_EVENTS]")
        sys.exit(1)
    
    return args

def load_data(pair):
    atr_file = f"data/{pair}_atr_data_{ATR_INTERVAL}min.csv"
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
            if abs(curr_price - next_price) / curr_price < 0.015:  # 1.5% threshold
                del pivots[i + 1]
            else:
                i += 1
    return pivots
    
def calculate_noise_between_pivots(df, pivot_pair):
    start_idx, start_type, start_price, start_dtime = pivot_pair[0]
    end_idx, end_type, end_price, end_dtime = pivot_pair[1]
    
    price_change = abs((end_price - start_price) / start_price)
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
    atr_min = df['atr'].median()
    atr_at_max = max(atr_at_max, atr_min)
    k_value = max_value / atr_at_max if atr_at_max > 0 else 0
    
    return {
        'type': 'uptrend' if start_type == 'min' else 'downtrend',
        'start_dtime': start_dtime,
        'end_dtime': end_dtime,
        'price_change': price_change,
        'max_value': max_value,
        'atr_at_max': atr_at_max,
        'k_value': k_value
    }

def analyze_structural_noise(pair, order=DEFAULT_ORDER, show_events=False):
    df = load_data(pair)
    print(f"--- Analyzing {pair} Market Structure ({len(df)} candles) ---")
    
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
    
    # Print results
    print_statistics(uptrend_data, "UPTREND NOISE (Stop Loss configuration)")
    print_statistics(downtrend_data, "DOWNTREND NOISE (Reentry Stop configuration)")
    
    if show_events:
        print_events_detail(uptrend_data, "UPTREND EVENTS")
        print_events_detail(downtrend_data, "DOWNTREND EVENTS")

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
    print(f"{'From':<20} | {'To':<20} | {'Change %':>10} | {'Max Value':>11} | {'ATR':>9} | {'K Value':>10}")
    print("-" * 105)
    
    for event in events:
        change_pct = event['price_change'] * 100
        print(f"{str(event['start_dtime']):<20} | {str(event['end_dtime']):<20} "
              f"| {change_pct:>9.2f}% | {event['max_value']:>11.4f} | {event['atr_at_max']:>9.4f} | {event['k_value']:>9.2f}")

if __name__ == "__main__":
    args = get_args()
    analyze_structural_noise(args['pair'], args['order'], args['show_events'])