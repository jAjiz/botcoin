import pandas as pd
import numpy as np
import sys
import os
from scipy.signal import argrelextrema

DEFAULT_ORDER = 20
DATA_DIR = 'data'
DATA_EXTENSION = 'atr_data_15min.csv'

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
    file_path = os.path.join(DATA_DIR, f'{pair}_{DATA_EXTENSION}')
    
    if not os.path.exists(file_path):
        print(f"Error: File not found: {file_path}")
        sys.exit(1)
    
    try:
        df = pd.read_csv(file_path)
        df.columns = [c.strip().lower() for c in df.columns]
    except Exception as e:
        print(f"Error reading file: {e}")
        sys.exit(1)
    
    required_cols = {'low', 'high', 'dtime', 'atr'}
    if not required_cols.issubset(set(df.columns)):
        print(f"Error: Missing required columns. Need: {required_cols}")
        sys.exit(1)
    
    df.dropna(inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df

def detect_pivots(df, order):
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
    k_value = max_value / atr_at_max if atr_at_max > 0 else 0
    
    return {
        'type': 'uptrend' if start_type == 'min' else 'downtrend',
        'start_dtime': start_dtime,
        'end_dtime': end_dtime,
        'price_change': price_change,
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

def print_events_detail(events, title):
    if not events:
        return
    
    print(f"\n=== {title} ===")
    print(f"{'From':<20} | {'To':<20} | {'Change %':>10} | {'K Value':>10}")
    print("-" * 70)
    
    for event in events:
        change_pct = event['price_change'] * 100
        print(f"{str(event['start_dtime']):<20} | {str(event['end_dtime']):<20} "
              f"| {change_pct:>9.2f}% | {event['k_value']:>9.2f}")

if __name__ == "__main__":
    args = get_args()
    analyze_structural_noise(args['pair'], args['order'], args['show_events'])