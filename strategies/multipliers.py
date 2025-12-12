from core.config import TRADING_PARAMS as PARAMS

def process_order(side, entry_price, current_atr, pair):
    atr_value = calculate_atr_value(entry_price, current_atr, pair)
    activation_distance = calculate_activation_dist(atr_value, pair)
    if side == "buy":
        new_side = "sell"
        activation_price = entry_price + activation_distance
    else:
        new_side = "buy"
        activation_price = entry_price - activation_distance
    return new_side, atr_value, activation_price

def calculate_atr_value(price, current_atr, pair):
    if current_atr is None:
        # ATR data unavailable, use minimum threshold
        atr_value = price * PARAMS[pair]["ATR_MIN_PCT"] 
    else:
        atr_pct = current_atr / price
        if atr_pct < PARAMS[pair]["ATR_MIN_PCT"]:
            # ATR below minimum threshold, use minimum threshold
            atr_value = price * PARAMS[pair]["ATR_MIN_PCT"]
        else:
            atr_value = current_atr

    return atr_value

def calculate_activation_dist(atr_value, pair):
    activation_distance = PARAMS[pair]["K_ACT"] * atr_value
    return activation_distance

def calculate_stop_price(side, entry_price, trailing_ref_price, atr_val, pair):
    raw_stop = PARAMS[pair]["K_STOP"] * atr_val
    min_margin_eur = entry_price * PARAMS[pair]["MIN_MARGIN_PCT"]
    
    if side == "sell":
        max_space = (trailing_ref_price - entry_price) - min_margin_eur
    else:
        max_space = (entry_price - trailing_ref_price) - min_margin_eur

    stop_distance = min(raw_stop, max(0.0, max_space))
    stop_price = trailing_ref_price - stop_distance if side == "sell" else trailing_ref_price + stop_distance

    return stop_price