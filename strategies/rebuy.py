from core.config import REBUY_K_STOP as K_STOP
from core.state import load_closed_positions

def process_order(order_id, side, entry_price, current_atr):
    if side == "buy":
        new_side = "sell"
        atr_value = current_atr        
    else:
        new_side = "buy"
        closed_positions = load_closed_positions()
        for closed_id, pos in list(closed_positions.items()):
            if closed_id == order_id:
                atr_value = pos["stop_atr"]
                break

    activation_distance = calculate_activation_dist(new_side, atr_value, entry_price)
    activation_price = entry_price + activation_distance 

    return new_side, atr_value, activation_price

def calculate_activation_dist(side, atr_value, entry_price):
    activation_distance = 0
    if side == "sell":
        # activation_distance = stop_distance + 0.25% entry price + 0.4% sell price + 0.4% rebuy price
        activation_distance = (0.996 * K_STOP * atr_value + 0.0105 * entry_price) / 0.992
    return activation_distance

def calculate_stop_price(side, trailing_ref_price, atr_val):
    stop_distance = K_STOP * atr_val
    stop_price = trailing_ref_price - stop_distance if side == "sell" else trailing_ref_price + stop_distance
    return stop_price
    