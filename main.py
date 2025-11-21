import time
from config import logging
from kraken_client import get_balance, get_closed_orders, get_current_price, place_limit_order, get_current_atr
from trailing_controller import load_trailing_state, save_trailing_state, is_processed, save_closed_order

# Bot configuration
SLEEPING_INTERVAL = 60

# Trading variables
K_ACT = 5.5
K_STOP = 2.5
MIN_MARGIN_PCT = 0.01 
ATR_PCT_MIN = MIN_MARGIN_PCT / (K_ACT - K_STOP)
MIN_BTC_ALLOCATION_PCT = 0.60

def main():
    try:
        while True:
            logging.info("======== STARTING SESSION ========")

            current_price = get_current_price("XXBTZEUR")
            current_atr = get_current_atr()
            current_balance = get_balance("XXBTZEUR")

            logging.info(f"Market: {current_price:,.1f}€ | ATR: {current_atr:,.1f}€")

            trailing_state = load_trailing_state()   
            
            one_session_ago = int(time.time()) - SLEEPING_INTERVAL
            one_week_ago = int(time.time()) - (60 * 60 * 24 * 7)

            closed_orders = get_closed_orders(one_week_ago, one_session_ago)
            if closed_orders:
                for order_id, order in closed_orders.items():
                    if is_processed(order_id, trailing_state):
                        continue
                    process_closed_order(order_id, order, trailing_state, current_atr)                
            else:
                logging.info("No closed orders returned.")

            update_activation_prices(trailing_state, current_atr)
            update_stop_prices(trailing_state, current_atr)
            update_trailing_state(trailing_state, current_price, current_atr, current_balance)

            logging.info(f"Session complete. Sleeping for {SLEEPING_INTERVAL}s.\n")
            time.sleep(SLEEPING_INTERVAL)   
    except KeyboardInterrupt:
        logging.info("Bot stopped manually by user.")

def now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

def calculate_atr_value(price, current_atr):
    if current_atr is None:
        atr_value = price * ATR_PCT_MIN # ATR data unavailable, use minimum threshold
    else:
        atr_pct = current_atr / price
        if atr_pct < ATR_PCT_MIN: 
            atr_value = price * ATR_PCT_MIN # ATR below minimum threshold, use minimum threshold
        else:
            atr_value = current_atr
    return round(atr_value, 1)

def process_closed_order(order_id, order, trailing_state, current_atr):
    logging.info(f"Processing order {order_id}...")
    price = float(order["price"])
    volume = float(order["vol_exec"])
    cost = float(order["cost"])
    side = order["descr"]["type"]
    pair = order["descr"]["pair"]

    if pair != "XBTEUR" or side not in ["buy", "sell"]:
        return

    atr_value = calculate_atr_value(price, current_atr)
    activation_distance = K_ACT * atr_value

    if side == "buy":
        new_side = "sell"
        activation_price = round(price + activation_distance, 1)
    elif side == "sell":
        new_side = "buy"
        activation_price = round(price - activation_distance, 1)

    trailing_state[order_id] = {
        "created_time": now_str(),
        "side": new_side,
        "entry_price": price,
        "volume": volume,
        "cost": cost,
        "activation_atr": atr_value,
        "activation_price": activation_price,
        "activation_time": None,
        "trailing_price": None,
        "stop_price": None,
        "stop_atr": None
    }

    logging.info(f"🆕[CREATE] New trailing position {order_id} for {new_side.upper()} order: activation at {activation_price:,}€")
    save_trailing_state(trailing_state)

def update_activation_prices(trailing_state, current_atr):
    logging.info("Checking activation prices...")

    for order_id, pos in trailing_state.items():
        if pos["trailing_price"]:
            continue  # Skip active trailing positions

        side = pos["side"]
        entry_price = pos["entry_price"]

        atr_value = calculate_atr_value(entry_price, current_atr)
        if pos["activation_atr"] * 0.8 < atr_value < pos["activation_atr"] * 1.2:
            continue  # ATR change within 20%, no update needed

        activation_distance = K_ACT * atr_value
        activation_price = round(entry_price + activation_distance if side == "sell" else entry_price - activation_distance, 1)

        pos["activation_atr"] = atr_value
        pos["activation_price"] = activation_price
        logging.info(f"♻️[ATR] Position {order_id}: updated activation price to {activation_price:,}€ due to ATR change.")

    save_trailing_state(trailing_state)

def update_stop_prices(trailing_state, current_atr):
    logging.info("Checking stop prices...")

    for order_id, pos in trailing_state.items():
        if not pos.get("trailing_price"):
            continue  # Skip inactive trailing positions

        side = pos["side"]
        entry_price = pos["entry_price"]
        trailing_price = pos["trailing_price"]

        atr_value = calculate_atr_value(entry_price, current_atr)
        if pos["stop_atr"] * 0.8 < atr_value < pos["stop_atr"] * 1.2:
            continue  # ATR change within 20%, no update needed
        
        stop_distance = K_STOP * atr_value
        candidate_stop = round(trailing_price - stop_distance if side == "sell" else trailing_price + stop_distance, 1)

        favorable = (side == "sell" and candidate_stop > pos["stop_price"]) or (side == "buy" and candidate_stop < pos["stop_price"])
        if favorable:
            logging.info(f"♻️[ATR] Position {order_id}: updated stop price to {candidate_stop:,}€ due to ATR change.")
            pos["stop_price"] = candidate_stop
            pos["stop_atr"] = atr_value

    save_trailing_state(trailing_state)

def update_trailing_state(trailing_state, current_price, current_atr, current_balance):
    logging.info(f"Checking trailing positions...")
    
    def update_prices():
        trailing_price = current_price
        atr_for_stop = calculate_atr_value(entry_price, current_atr)
        stop_distance = K_STOP * atr_for_stop

        if side == "sell" :
            if trailing_price - stop_distance < entry_price * (1 + MIN_MARGIN_PCT):
                stop_distance = trailing_price - entry_price * (1 + MIN_MARGIN_PCT)
        elif side == "buy":
            if trailing_price + stop_distance > entry_price * (1 - MIN_MARGIN_PCT):
                stop_distance = entry_price * (1 - MIN_MARGIN_PCT) - trailing_price

        stop_price = round(trailing_price - stop_distance if side == "sell" else trailing_price + stop_distance, 1)
        pos["stop_atr"] = atr_for_stop
        return trailing_price, stop_price

    def activate_trailing():
        trailing_price, stop_price = update_prices()
        pos["activation_time"] = now_str()
        logging.info(f"⚡[ACTIVE] Trailing activated for position {order_id}: new price at {trailing_price:,}€ | stop at {stop_price:,}€")
        return trailing_price, stop_price
    
    def update_trailing():
        trailing_price, stop_price = update_prices()
        logging.info(f"📈[UPDATE] Position {order_id}: updated trailing price to {trailing_price:,}€ | new stop at {stop_price:,}€")
        return trailing_price, stop_price

    def close_trailing(side):
        try:
            logging.info(f"⛔[CLOSE] Stop price {stop_price:,}€ hit for position {order_id}: placing LIMIT {side.upper()} order")
            closing_order = place_limit_order("XXBTZEUR", side, current_price, volume)

            if side == "sell":
                pnl = (current_price - entry_price) / entry_price * 100
            else:
                pnl = (entry_price - current_price) / entry_price * 100
            logging.info(f"[PnL] Closed position at {current_price:,}€: {pnl:+.2f}% gain before fees")

            pos["cost"] = cost
            pos["volume"] = volume
            pos["closing_price"] = current_price
            pos["closing_time"] = now_str()
            pos["closing_order"] = closing_order
            pos["pnl"] = round(pnl, 2)
            save_closed_order(trailing_state[order_id], order_id)
            del trailing_state[order_id]
            logging.info(f"Trailing position {order_id} closed and removed.")
        except Exception as e:
            logging.error(f"Failed to close trailing position {order_id}: {e}")
        
    def can_execute_sell():
        btc_after_sell = current_balance.get("XXBT") - volume
        eur_after_sell = current_balance.get("ZEUR") + (volume * current_price)

        total_value_after = (btc_after_sell * current_price) + eur_after_sell
        if total_value_after == 0: return True

        btc_allocation_after = (btc_after_sell * current_price) / total_value_after
        
        if btc_allocation_after < MIN_BTC_ALLOCATION_PCT:
            logging.warning(f"🛡️[BLOCKED] Sell {order_id} by inventory ratio: {btc_allocation_after:.2%} < min: {MIN_BTC_ALLOCATION_PCT:.0%}.")
            return False
            
        return True

    for order_id, pos in list(trailing_state.items()):
        side = pos["side"]
        entry_price = pos["entry_price"]
        volume = pos["volume"]
        cost = pos["cost"]
        activation_price = pos["activation_price"]
        trailing_price = pos["trailing_price"]
        stop_price = pos["stop_price"]
        
        if side == "sell" :
            if not trailing_price and current_price >= activation_price:
                pos["trailing_price"], pos["stop_price"] = activate_trailing()
            elif trailing_price:
                if current_price > trailing_price:
                    pos["trailing_price"], pos["stop_price"] = update_trailing()
                if current_price <= stop_price and can_execute_sell():
                    cost = volume * current_price
                    close_trailing(side)

        elif side == "buy":
            if not trailing_price and current_price <= activation_price:
                pos["trailing_price"], pos["stop_price"] = activate_trailing()
            elif trailing_price:
                if current_price < trailing_price:
                    pos["trailing_price"], pos["stop_price"] = update_trailing()
                if current_price >= stop_price:
                    volume = cost / current_price
                    close_trailing(side)
    
    save_trailing_state(trailing_state)

if __name__ == "__main__":
    main()
