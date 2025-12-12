from core.config import TRADING_PARAMS as PARAMS

def process_order(side, entry_price, current_atr, pair):
    if side == "buy":
        new_side = "sell"
        activation_distance = calculate_activation_dist(new_side, current_atr, entry_price, pair)
        activation_price = entry_price + activation_distance
    else:
        new_side = "buy"
        activation_distance = calculate_activation_dist(new_side, current_atr, entry_price, pair)
        activation_price = entry_price - activation_distance

    return new_side, current_atr, activation_price


def calculate_activation_dist(side, atr_val, entry_price, pair):
    if side == "sell":
        activation_distance = PARAMS[pair]["K_STOP_SELL"] * atr_val + 0.0106 * entry_price
    else:
        activation_distance = PARAMS[pair]["K_STOP_BUY"] * atr_val + 0.001 * entry_price

    return activation_distance


def calculate_stop_price(side, trailing_ref_price, atr_val, pair):
    if side == "sell":
        stop_distance = PARAMS[pair]["K_STOP_SELL"] * atr_val
        stop_price = trailing_ref_price - stop_distance
    else:
        stop_distance = PARAMS[pair]["K_STOP_BUY"] * atr_val
        stop_price = trailing_ref_price + stop_distance

    return stop_price
    