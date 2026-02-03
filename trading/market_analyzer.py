import pandas as pd
import numpy as np
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path
from scipy.signal import argrelextrema

# Ensure sibling packages are importable when running as a script.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import core.logging as logging
from core.config import MARKET_ANALYZER, CANDLE_TIMEFRAME, MARKET_DATA_DAYS, ATR_PERIOD
from exchange.kraken import fetch_ohlc_data

DEFAULT_ORDER = MARKET_ANALYZER["DEFAULT_ORDER"]
MINIMUM_CHANGE_PCT = MARKET_ANALYZER["MINIMUM_CHANGE_PCT"]


def get_current_atr(pair):
    try:
        atr_file = f"data/{pair}_ohlc_data_{CANDLE_TIMEFRAME}min.csv"
        since_param = None
        existing_df = None

        if os.path.exists(atr_file):
            try:
                existing_df = pd.read_csv(atr_file, index_col=0, parse_dates=True)
                if not existing_df.empty:
                    since_param = int(existing_df.index[-1].timestamp())
            except Exception:
                existing_df = None

        df = fetch_ohlc_data(pair, CANDLE_TIMEFRAME, since_param)
        
        if df is None or df.empty:
            return None

        if existing_df is not None and not existing_df.empty:
            df = pd.concat([existing_df, df])
            df = df[~df.index.duplicated(keep='last')]
            df = df.sort_index()

        cutoff_date = datetime.now() - timedelta(days=MARKET_DATA_DAYS)
        df = df[df.index >= cutoff_date]

        df["H-L"] = df["high"] - df["low"]
        df["H-PC"] = (df["high"] - df["close"].shift(1)).abs()
        df["L-PC"] = (df["low"] - df["close"].shift(1)).abs()
        df["TR"] = df[["H-L", "H-PC", "L-PC"]].max(axis=1)
        df["ATR"] = df["TR"].rolling(ATR_PERIOD).mean()
        df.to_csv(atr_file)

        current_atr = df["ATR"].iloc[-1]
        return current_atr
    except Exception as e:
        logging.error(f"Error getting ATR for {pair}: {e}")
        return None
    

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
            "PAIR=ETHEUR [ORDER=20] [SHOW_EVENTS] [Volatility=LL|LV|MV|HV|HH|ALL]")
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
    
def calculate_noise_between_pivots(df, pivot_pair, atr_20pct, atr_50pct, atr_80pct, atr_95pct):
    start_idx, start_type, start_price, start_dtime = pivot_pair[0]
    end_idx, end_type, end_price, end_dtime = pivot_pair[1]
    
    price_change_pct = abs((end_price - start_price) / start_price)
    segment = df.iloc[start_idx+1:end_idx]
    
    if len(segment) == 0:
        return {}
    
    if start_type == 'min' and end_type == 'max':
        # Uptrend: calculate drawdown and find maximum K (drawdown / ATR)
        rolling_max = segment['high'].expanding().max()
        drawdowns = rolling_max - segment['low']
        segment_copy = segment.copy()
        segment_copy['k_values'] = drawdowns / segment_copy['atr'].replace(0, np.nan)
    elif start_type == 'max' and end_type == 'min':
        # Downtrend: calculate bounce and find maximum K (bounce / ATR)
        rolling_min = segment['low'].expanding().min()
        bounces = segment['high'] - rolling_min
        segment_copy = segment.copy()
        segment_copy['k_values'] = bounces / segment_copy['atr'].replace(0, np.nan)
    else:
        return {}
    
    # Now find max K for each volatility level
    volatility_levels = {}
    vol_ranges = {
        'LL': (0, atr_20pct),
        'LV': (atr_20pct, atr_50pct),
        'MV': (atr_50pct, atr_80pct),
        'HV': (atr_80pct, atr_95pct),
        'HH': (atr_95pct, float('inf'))
    }
    
    for vol_level, (min_atr, max_atr) in vol_ranges.items():
        mask = (segment_copy['atr'] >= min_atr) & (segment_copy['atr'] < max_atr)
        if not mask.any():
            continue
        
        vol_segment = segment_copy[mask]
        idx_max = vol_segment['k_values'].idxmax()
        
        if start_type == 'min' and end_type == 'max':
            max_value = (rolling_max.loc[idx_max] - segment.loc[idx_max, 'low'])
        else:
            max_value = (segment.loc[idx_max, 'high'] - rolling_min.loc[idx_max])
        
        k_value = vol_segment['k_values'].loc[idx_max]
        atr_at_max = segment.loc[idx_max, 'atr']
        
        volatility_levels[vol_level] = {
            'max_value': max_value,
            'atr_at_max': atr_at_max,
            'k_value': k_value
        }
    
    event = {
        'type': 'uptrend' if start_type == 'min' else 'downtrend',
        'start_dtime': start_dtime,
        'end_dtime': end_dtime,
        'price_change_pct': price_change_pct,
        'price_change_k': (end_price - start_price) / segment['atr'].mean(),
        'volatility_levels': volatility_levels
    }
    
    return event

def analyze_structural_noise(df, order=DEFAULT_ORDER, print_results=False, show_events=False,
                              volatility_level=None):
    # Detect and filter pivots
    pivots = detect_pivots(df, order)
    
    # Calculate ATR percentiles
    atr_20pct = np.percentile(df['atr'], 20)
    atr_50pct = np.percentile(df['atr'], 50)
    atr_80pct = np.percentile(df['atr'], 80)
    atr_95pct = np.percentile(df['atr'], 95)
    
    # Calculate noise for each pivot pair
    all_events = []
    uptrend_events = []
    downtrend_events = []
    
    for i in range(1, len(pivots)):
        event = calculate_noise_between_pivots(
            df, (pivots[i-1], pivots[i]), atr_20pct, atr_50pct, atr_80pct, atr_95pct
        )
        if event and event['volatility_levels']:
            all_events.append(event)
            if event['type'] == 'uptrend':
                uptrend_events.append(event)
            else:
                downtrend_events.append(event)
    
    if print_results:
        print(f"--- Analyzing Market Structure (minimum change {MINIMUM_CHANGE_PCT*100:.2f}%) ---")
        print(f"  P20: {atr_20pct:.1f} | P50: {atr_50pct:.1f} | P80: {atr_80pct:.1f} | P95: {atr_95pct:.1f}")
        
        if volatility_level is None:
            # Solo eventos sin desglose por volatilidad
            print_events_detail(uptrend_events, "UPTREND EVENTS")
            print_events_detail(downtrend_events, "DOWNTREND EVENTS")
        elif volatility_level == 'ALL':
            # Todos los niveles con desglose
            for vol_level in ['LL', 'LV', 'MV', 'HV', 'HH']:
                uptrend_vol = [e for e in uptrend_events if vol_level in e['volatility_levels']]
                downtrend_vol = [e for e in downtrend_events if vol_level in e['volatility_levels']]
                
                print(f"\n{'='*60}")
                print(f"VOLATILITY LEVEL: {vol_level}")
                print(f"{'='*60}")
                print_statistics(uptrend_vol, vol_level, "UPTREND NOISE (Stop Loss configuration)")
                print_statistics(downtrend_vol, vol_level, "DOWNTREND NOISE (Reentry Stop configuration)")
                
                if show_events:
                    print_events_detail(uptrend_vol, "UPTREND EVENTS", vol_level)
                    print_events_detail(downtrend_vol, "DOWNTREND EVENTS", vol_level)
        else:
            # Un nivel específico
            uptrend_vol = [e for e in uptrend_events if volatility_level in e['volatility_levels']]
            downtrend_vol = [e for e in downtrend_events if volatility_level in e['volatility_levels']]
            
            print_statistics(uptrend_vol, volatility_level, f"UPTREND NOISE - {volatility_level} (Stop Loss configuration)")
            print_statistics(downtrend_vol, volatility_level, f"DOWNTREND NOISE - {volatility_level} (Reentry Stop configuration)")
            
            if show_events:
                print_events_detail(uptrend_vol, f"UPTREND EVENTS - {volatility_level}", volatility_level)
                print_events_detail(downtrend_vol, f"DOWNTREND EVENTS - {volatility_level}", volatility_level)

    return uptrend_events, downtrend_events

def print_statistics(events, vol_level, title):
    if not events:
        print(f"\nNo events detected for {title}\n")
        return
    
    k_values = [e['volatility_levels'][vol_level]['k_value'] for e in events if vol_level in e['volatility_levels']]
    if not k_values:
        print(f"\nNo events detected for {title}\n")
        return
    
    s = pd.Series(k_values)
    
    print(f"\n=== {title} ===")
    print(f"Events: {len(s)} | Average: {s.mean():.2f} ATR")
    print(f"Percentile 50%: {s.quantile(0.50):.2f} ATR (Very Tight)")
    print(f"Percentile 75%: {s.quantile(0.75):.2f} ATR (Standard)")
    print(f"Percentile 90%: {s.quantile(0.90):.2f} ATR (Safe)")
    print(f"Percentile 95%: {s.quantile(0.95):.2f} ATR (Protected)")
    print(f"Percentile 100%: {s.quantile(1.00):.2f} ATR (Extreme)")

def print_events_detail(events, title, vol_level=None):
    if not events:
        return
    
    print(f"\n=== {title} ===")
    
    if vol_level is None:
        # Mostrar solo datos comunes
        print(f"{'From':<20} | {'To':<20} | {'Change %':>10} | {'Change K':>9}")
        print("-" * 70)
        for event in events:
            change_pct = event['price_change_pct'] * 100
            print(f"{str(event['start_dtime']):<20} | {str(event['end_dtime']):<20} "
                  f"| {change_pct:>9.2f}% | {event['price_change_k']:>9.2f}")
    else:
        # Mostrar datos comunes + específicos del nivel
        print(f"{'From':<20} | {'To':<20} | {'Change %':>10} | {'Change K':>9} | {'Max Value':>10} "
              f"| {'ATR at max':>10} | {'K Value':>8}")
        print("-" * 135)
        for event in events:
            if vol_level not in event['volatility_levels']:
                continue
            change_pct = event['price_change_pct'] * 100
            vol_data = event['volatility_levels'][vol_level]
            print(f"{str(event['start_dtime']):<20} | {str(event['end_dtime']):<20} "
                  f"| {change_pct:>9.2f}% | {event['price_change_k']:>9.2f} "
                  f"| {vol_data['max_value']:>10.1f} | {vol_data['atr_at_max']:>10.1f} | {vol_data['k_value']:>8.2f}")

if __name__ == "__main__":
    args = get_args()
    analyze_structural_noise(
        load_data(args['pair']), 
        args['order'], 
        True, 
        args['show_events'], 
        args['volatility_level']
    )