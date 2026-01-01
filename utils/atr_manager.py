import numpy as np
import logging
from utils.market_noise_analyzer import load_data, detect_pivots, calculate_noise_between_pivots, DEFAULT_ORDER
from core.config import ATR_MIN_PERCENTILE

# ATR minimum coefficient range
ATR_MIN_COEFF_MIN = 0.7
ATR_MIN_COEFF_MAX = 0.9

def calculate_atr_min(pair):
    try:
        df = load_data(pair)
    except Exception as e:
        logging.error(f"Error loading data for {pair}: {e}")
        return 0

    atr_median = df['atr'].median()
    if atr_median == 0 or np.isnan(atr_median):
        logging.error(f"Invalid ATR median for {pair}: {atr_median}")
        return 0

    pivots = detect_pivots(df, DEFAULT_ORDER)
    
    ratios = []
    for i in range(1, len(pivots)):
        event = calculate_noise_between_pivots(df, (pivots[i-1], pivots[i]))
        if event and event.get('atr_at_max'):
            ratio = event['atr_at_max'] / atr_median
            ratios.append(ratio)
            
    if not ratios:
        return ATR_MIN_COEFF_MIN * atr_median # Default to lower bound if no events found

    # Calculate percentile
    floor_coeff = np.percentile(ratios, ATR_MIN_PERCENTILE * 100)
    
    # Clamp to configured range
    floor_coeff = np.clip(floor_coeff, ATR_MIN_COEFF_MIN, ATR_MIN_COEFF_MAX)

    atr_min = floor_coeff * atr_median
    logging.info(f"[{pair}] ATR Min Calculation: Median ATR={atr_median:.4f}, Coeff={floor_coeff:.4f}, ATR Min={atr_min:.4f}")
    
    return atr_min
