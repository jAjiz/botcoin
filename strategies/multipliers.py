import core.logging as logging
from core.config import MULT_K_ACT as K_ACT, MULT_K_STOP as K_STOP, ATR_PCT_MIN, MIN_MARGIN_PCT, MIN_BTC_ALLOCATION_PCT

def process_order(side, entry_price, current_atr):
    new_side = "buy" if side == "sell" else "sell"
    atr_value = calculate_atr_value(entry_price, current_atr)
    activation_distance = calculate_activation_dist(atr_value)
    activation_price = entry_price + activation_distance if new_side == "sell" else entry_price - activation_distance
    return new_side, atr_value, activation_price

def calculate_atr_value(price, current_atr):
    if current_atr is None:
        atr_value = price * ATR_PCT_MIN # ATR data unavailable, use minimum threshold
    else:
        atr_pct = current_atr / price
        if atr_pct < ATR_PCT_MIN: 
            atr_value = price * ATR_PCT_MIN # ATR below minimum threshold, use minimum threshold
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

def can_execute_sell(order_id, vol_to_sell, current_balance, current_price):
    btc_after_sell = float(current_balance.get("XXBT")) - vol_to_sell
    eur_after_sell = float(current_balance.get("ZEUR")) + (vol_to_sell * current_price)

    total_value_after = (btc_after_sell * current_price) + eur_after_sell
    if total_value_after == 0: return True

    btc_allocation_after = (btc_after_sell * current_price) / total_value_after
    
    if btc_allocation_after < MIN_BTC_ALLOCATION_PCT:
        logging.warning(f"🛡️|BLOCKED| Sell [{order_id}] by inventory ratio: {btc_allocation_after:.2%} < min: {MIN_BTC_ALLOCATION_PCT:.0%}.",
                         to_telegram=True)
        return False
        
    return True