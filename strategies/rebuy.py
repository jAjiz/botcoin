from core.config import REBUY_K_STOP as K_STOP

def process_order(side, entry_price, current_atr):
    activation_distance = calculate_activation_dist(new_side, current_atr, entry_price)

    if side == "buy":
        new_side = "sell"
        activation_price = entry_price + activation_distance 
    else:
        new_side = "buy"
        activation_price = entry_price - activation_distance

    return new_side, current_atr, activation_price

def calculate_activation_dist(side, atr_value, entry_price):
    if side == "sell":
        # activation_distance = stop_distance + 0.25% entry price + 0.4% sell price + 0.4% rebuy price
        activation_distance = (0.992 * K_STOP * atr_value + 0.0105 * entry_price) / 0.992
    else:
        activation_distance = K_STOP * atr_value
    return activation_distance

def calculate_stop_price(side, trailing_ref_price, atr_val):
    stop_distance = K_STOP * atr_val
    stop_price = trailing_ref_price - stop_distance if side == "sell" else trailing_ref_price + stop_distance
    return stop_price
    