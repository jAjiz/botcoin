from core.config import K_ACT, K_STOP, ATR_MIN_PCT, MIN_MARGIN_PCT

def process_order(side, entry_price, current_atr):
    new_side = "buy" if side == "sell" else "sell"
    atr_value = calculate_atr_value(entry_price, current_atr)
    activation_distance = calculate_activation_dist(atr_value)
    activation_price = entry_price + activation_distance if new_side == "sell" else entry_price - activation_distance
    return new_side, atr_value, activation_price

def calculate_atr_value(price, current_atr):
    if current_atr is None:
        atr_value = price * ATR_MIN_PCT # ATR data unavailable, use minimum threshold
    else:
        atr_pct = current_atr / price
        if atr_pct < ATR_MIN_PCT: 
            atr_value = price * ATR_MIN_PCT # ATR below minimum threshold, use minimum threshold
        else:
            atr_value = current_atr

    return atr_value

def calculate_activation_dist(atr_value):
    activation_distance = K_ACT * atr_value
    return activation_distance

def calculate_stop_price(side, entry_price, trailing_ref_price, atr_val):
    raw_stop = K_STOP * atr_val
    min_margin_eur = entry_price * MIN_MARGIN_PCT
    
    if side == "sell":
        max_space = (trailing_ref_price - entry_price) - min_margin_eur
    else:
        max_space = (entry_price - trailing_ref_price) - min_margin_eur

    stop_distance = min(raw_stop, max(0.0, max_space))
    stop_price = trailing_ref_price - stop_distance if side == "sell" else trailing_ref_price + stop_distance

    return stop_price