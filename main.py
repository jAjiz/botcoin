import time
from kraken_client import get_balance, get_closed_orders, get_current_price, place_limit_order, get_current_atr
from trailing_controller import load_trailing_state, save_trailing_state, is_processed, save_closed_order
from logger import log_info, log_warning, log_error

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
        import telegram_interface
        telegram_interface.start_telegram_thread()
        log_info("🚀 Bot Iniciado y listo.", to_telegram=True)

        while True:
            if telegram_interface.BOT_PAUSED:
                log_info("Bot is paused. Sleeping...")
                time.sleep(SLEEPING_INTERVAL)
                continue

            log_info("======== STARTING SESSION ========")

            current_price = get_current_price("XXBTZEUR")
            current_atr = get_current_atr()
            current_balance = get_balance()

            log_info(f"Market: {current_price:,.1f}€ | ATR: {current_atr:,.1f}€")

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
                log_info("No closed orders returned.")

            update_trailing_state(trailing_state, current_price, current_atr, current_balance)

            log_info(f"Session complete. Sleeping for {SLEEPING_INTERVAL}s.\n")
            time.sleep(SLEEPING_INTERVAL)   
    except KeyboardInterrupt:
        log_info("Bot stopped manually by user.", to_telegram=True)

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
    return atr_value

def process_closed_order(order_id, order, trailing_state, current_atr):
    log_info(f"Processing order {order_id}...")
    entry_price = float(order["price"])
    volume = float(order["vol_exec"])
    cost = float(order["cost"])
    side = order["descr"]["type"]
    pair = order["descr"]["pair"]

    if pair != "XBTEUR" or side not in ["buy", "sell"]:
        return

    new_side = "buy" if side == "sell" else "sell"
    atr_value = calculate_atr_value(entry_price, current_atr)
    activation_distance = K_ACT * atr_value
    activation_price = entry_price + activation_distance if new_side == "sell" else entry_price - activation_distance

    trailing_state[order_id] = {
        "created_time": now_str(),
        "side": new_side,
        "entry_price": entry_price,
        "volume": volume,
        "cost": cost,
        "activation_atr": round(atr_value, 1),
        "activation_price": round(activation_price, 1),
        "activation_time": None,
        "trailing_price": None,
        "stop_price": None,
        "stop_atr": None
    }

    log_info(f"🆕[CREATE] New trailing position {order_id} for {new_side.upper()} order: activation at {trailing_state[order_id]['activation_price']:,}€", to_telegram=True)
    save_trailing_state(trailing_state)

def update_trailing_state(trailing_state, current_price, current_atr, current_balance):
    log_info(f"Checking trailing positions...")

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
    
    def recalibrate_activation(order_id, pos, atr_val):
        side = pos["side"]
        entry_price = pos["entry_price"]

        activation_distance = K_ACT * atr_val
        activation_price = entry_price + activation_distance if side == "sell" else entry_price - activation_distance

        pos.update({
            "activation_price": round(activation_price, 1),
            "activation_atr": round(atr_val, 1)
        })
        log_info(f"♻️[ATR] Position {order_id}: recalibrate activation price to {pos['activation_price']:,}€.")

    def recalibrate_stop(order_id, pos, atr_val):
        side = pos["side"]
        entry_price = pos["entry_price"]
        trailing_price = pos["trailing_price"]

        stop_price = calculate_stop_price(side, entry_price, trailing_price, atr_val)
        pos.update({
            "stop_price": round(stop_price, 1),
            "stop_atr": round(atr_val, 1)
        })
        log_info(f"♻️[ATR] Position {order_id}: recalibrate stop price to {pos['stop_price']:,}€.")

    def close_position(order_id, pos):
        try:
            side = pos["side"]
            stop_price = pos["stop_price"]
            volume = pos["volume"]
            cost = pos["cost"]
            log_warning(f"⛔[CLOSE] Stop price {stop_price:,}€ hit for position {order_id}: placing LIMIT {side.upper()} order", to_telegram=True)

            if side == "sell":
                cost = volume * stop_price
                pnl = (stop_price - pos["entry_price"]) / pos["entry_price"] * 100
            else:
                volume = cost / stop_price
                pnl = (pos["entry_price"] - stop_price) / pos["entry_price"] * 100

            closing_order = place_limit_order("XXBTZEUR", side, stop_price, volume)
            log_info(f"[PnL] Closed position: {pnl:+.2f}% gain before fees", to_telegram=True)

            pos.update({
                "cost": cost,
                "volume": volume,
                "closing_price": stop_price,
                "closing_time": now_str(),
                "closing_order": closing_order,
                "pnl": round(pnl, 2)
            })
            save_closed_order(trailing_state[order_id], order_id)
            del trailing_state[order_id]
            log_info(f"Trailing position {order_id} closed and removed.")
        except Exception as e:
            log_error(f"Failed to close trailing position {order_id}: {e}")
        
    def can_execute_sell(vol_to_sell):
        btc_after_sell = float(current_balance.get("XXBT")) - vol_to_sell
        eur_after_sell = float(current_balance.get("ZEUR")) + (vol_to_sell * current_price)

        total_value_after = (btc_after_sell * current_price) + eur_after_sell
        if total_value_after == 0: return True

        btc_allocation_after = (btc_after_sell * current_price) / total_value_after
        
        if btc_allocation_after < MIN_BTC_ALLOCATION_PCT:
            log_warning(f"🛡️[BLOCKED] Sell {order_id} by inventory ratio: {btc_allocation_after:.2%} < min: {MIN_BTC_ALLOCATION_PCT:.0%}.", to_telegram=True)
            return False
            
        return True

    for order_id, pos in list(trailing_state.items()):
        side = pos["side"]
        entry_price = pos["entry_price"]
        trailing_active = pos["trailing_price"] is not None
        atr_val = calculate_atr_value(entry_price, current_atr)

        if not trailing_active:
            if pos["activation_atr"] * 0.8 > atr_val or atr_val > pos["activation_atr"] * 1.2:
                recalibrate_activation(order_id, pos, atr_val)

            if (side == "sell" and current_price >= pos["activation_price"]) or \
               (side == "buy" and current_price <= pos["activation_price"]):
                
                stop_price = calculate_stop_price(side, entry_price, current_price, atr_val)
                pos.update({
                    "trailing_price": current_price,
                    "stop_price": round(stop_price, 1),
                    "stop_atr": round(atr_val, 1),
                    "activation_time": now_str()
                })
                log_info(f"⚡[ACTIVE] Trailing activated for position {order_id}: New price {pos['trailing_price']:,}€ | Stop {pos['stop_price']:,}€", to_telegram=True)
        else:
            if pos["stop_atr"] * 0.8 > atr_val or atr_val > pos["stop_atr"] * 1.2:
                recalibrate_stop(order_id, pos, atr_val)

            if (side == "sell" and current_price <= pos["stop_price"] and can_execute_sell(pos["volume"])) or \
               (side == "buy" and current_price >= pos["stop_price"]):
                
                close_position(order_id, pos)
                continue 

            if (side == "sell" and current_price > pos["trailing_price"]) or \
               (side == "buy" and current_price < pos["trailing_price"]):
                
                stop_price = calculate_stop_price(side, entry_price, current_price, atr_val)
                pos.update({
                    "trailing_price": current_price,
                    "stop_price": round(stop_price, 1),
                    "stop_atr": round(atr_val, 1)
                })
                log_info(f"📈[TRAIL] Position {order_id}: New price {pos['trailing_price']:,}€ | Stop {pos['stop_price']:,}€", to_telegram=True)
    
    save_trailing_state(trailing_state)

if __name__ == "__main__":
    main()
