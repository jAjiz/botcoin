import pandas as pd
import numpy as np
import sys
import os
from datetime import datetime, timedelta
from scipy.signal import argrelextrema

import core.logging as logging
from core.config import MARKET_ANALYZER, CANDLE_TIMEFRAME, MARKET_DATA_DAYS, ATR_PERIOD
from core.utils import print_pair_argument_error, print_structural_noise_results
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

        if len(df) < 2:
            return None

        current_atr = df["ATR"].iloc[-2]
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
        print_pair_argument_error()
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
    

def calculate_noise_between_pivots(df, pivot_pair, atr_percentiles):
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
        'LL': (0, atr_percentiles['p20']),
        'LV': (atr_percentiles['p20'], atr_percentiles['p50']),
        'MV': (atr_percentiles['p50'], atr_percentiles['p80']),
        'HV': (atr_percentiles['p80'], atr_percentiles['p95']),
        'HH': (atr_percentiles['p95'], float('inf'))
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
        'volatility_levels': volatility_levels
    }
    
    return event


def analyze_structural_noise(df, order=DEFAULT_ORDER, print_results=False, show_events=False, volatility_level=None):
    pivots = detect_pivots(df, order)
    
    # Calculate ATR percentiles
    atr_percentiles = {
        'p20': np.percentile(df['atr'], 20),
        'p50': np.percentile(df['atr'], 50),
        'p80': np.percentile(df['atr'], 80),
        'p95': np.percentile(df['atr'], 95),
    }
    
    # Calculate noise (events) for each pivot pair
    uptrend_events = []
    downtrend_events = []
    for i in range(1, len(pivots)):
        event = calculate_noise_between_pivots(df, (pivots[i-1], pivots[i]), atr_percentiles)
        if event and event['volatility_levels']:
            if event['type'] == 'uptrend':
                uptrend_events.append(event)
            else:
                downtrend_events.append(event)
    
    if print_results:
        print_structural_noise_results(
            uptrend_events,
            downtrend_events,
            MINIMUM_CHANGE_PCT,
            atr_percentiles,
            show_events,
            volatility_level,
        )

    return uptrend_events, downtrend_events


if __name__ == "__main__":
    args = get_args()
    analyze_structural_noise(
        load_data(args['pair']), 
        args['order'], 
        True, 
        args['show_events'], 
        args['volatility_level']
    )