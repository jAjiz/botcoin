from core.config import TRADING_PARAMS as PARAMS

def process_order(side, entry_price, atr_val, pair):
    if side == "buy":
        new_side = "sell"
        sign = 1
    else:
        new_side = "buy"
        sign = -1

    activation_distance = calculate_activation_dist(new_side, atr_val, entry_price, pair)
    activation_price = entry_price + sign * activation_distance
    return new_side, activation_price

def calculate_activation_dist(side, atr_val, entry_price, pair):
    k_stop = PARAMS[pair][side]["K_STOP"]
    min_margin = PARAMS[pair][side]["MIN_MARGIN"]
    activation_distance = k_stop * atr_val + min_margin * entry_price
    return activation_distance

def calculate_stop_price(side, trailing_ref_price, atr_val, pair):
    stop_distance = PARAMS[pair][side]["K_STOP"] * atr_val

    if side == "sell":
        stop_price = trailing_ref_price - stop_distance
    else:
        stop_price = trailing_ref_price + stop_distance

    return stop_price
