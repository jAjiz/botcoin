import time
import core.logging as logging
import services.telegram as telegram
import strategies.multipliers as multipliers_mode
import strategies.rebuy as rebuy_mode
from exchange.kraken import get_balance, get_closed_orders, get_current_price, place_limit_order, get_current_atr
from core.state import load_trailing_state, save_trailing_state, is_processed, save_closed_position
from core.config import MODE, SLEEPING_INTERVAL

def main():
    try:
        telegram.start_telegram_thread()

        while True:
            if telegram.BOT_PAUSED:
                logging.info("Bot is paused. Sleeping...")
                time.sleep(SLEEPING_INTERVAL)
                continue

            logging.info("======== STARTING SESSION ========")
            current_price = get_current_price("XXBTZEUR")
            current_atr = get_current_atr()
            current_balance = get_balance()

            if current_price is None or current_atr is None or not current_balance:
                logging.error(f"Could not fetch price, ATR, or balance. Skipping session and retrying in {SLEEPING_INTERVAL}s...")
                time.sleep(SLEEPING_INTERVAL)
                continue

            logging.info(f"Market: {current_price:,.1f}€ | ATR: {current_atr:,.1f}€")

            trailing_state = load_trailing_state()   
            
            one_session_ago = int(time.time()) - SLEEPING_INTERVAL * 2
            one_week_ago = int(time.time()) - (60 * 60 * 24 * 7)

            closed_orders = get_closed_orders(one_week_ago, one_session_ago)
            if closed_orders:
                for order_id, order in closed_orders.items():
                    if is_processed(order_id, trailing_state):
                        continue
                    process_closed_order(order_id, order, trailing_state, current_atr)                
            else:
                logging.info("No closed orders returned.")

            update_trailing_state(trailing_state, current_price, current_atr, current_balance)

            logging.info(f"Session complete. Sleeping for {SLEEPING_INTERVAL}s.\n")
            time.sleep(SLEEPING_INTERVAL)   
    except KeyboardInterrupt:
        logging.info("BoTC stopped manually by user.", to_telegram=True)
    finally:
        telegram.stop_telegram_thread()

def now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

def process_closed_order(order_id, order, trailing_state, current_atr):
    logging.info(f"Processing order [{order_id}]...")
    entry_price = float(order["price"])
    volume = float(order["vol_exec"])
    cost = float(order["cost"])
    side = order["descr"]["type"]
    pair = order["descr"]["pair"]

    if pair != "XBTEUR" or side not in ["buy", "sell"]:
        return

    if MODE == "multipliers":
        new_side, atr_value, activation_price = multipliers_mode.process_order(side, entry_price, current_atr)
    elif MODE == "rebuy":
        new_side, atr_value, activation_price = rebuy_mode.process_order(side, entry_price, current_atr)

    existing_position = None
    for existing_id, pos in list(trailing_state.items()):
        if pos["mode"] != MODE or pos["side"] != new_side or pos.get("trailing_price") is not None:
            continue
        
        price_diff_pct = abs(pos["entry_price"] - entry_price) / pos["entry_price"] * 100        
        if price_diff_pct <= 1.0:
            existing_position = (existing_id, pos)
            break
    
    if existing_position:
        existing_id, existing_pos = existing_position

        if new_side == "sell":
            new_volume = existing_pos["volume"] + volume
            new_cost = new_volume * existing_pos["entry_price"]
        else:
            new_cost = existing_pos["cost"] + cost
            new_volume = new_cost / existing_pos["entry_price"]
        
        existing_pos["volume"] = round(new_volume, 8)
        existing_pos["cost"] = round(new_cost, 2)
        existing_pos["opening_order"].append(order_id)
        
        logging.info(
            f"🔀|MERGE| Unified order [{order_id}] into existing position [{existing_id}]: "
            f"activation at {trailing_state[existing_id]['activation_price']:,}€",
            to_telegram=True
        )
    else:
        trailing_state[order_id] = {
            "mode": MODE,
            "created_time": now_str(),
            "opening_order": [order_id],
            "side": new_side,
            "entry_price": entry_price,
            "volume": volume,
            "cost": round(cost, 2),
            "activation_atr": round(atr_value, 1),
            "activation_price": round(activation_price, 1)
        }
        
        logging.info(
            f"🆕|CREATE| New trailing position [{order_id}] for {new_side.upper()} order: "
            f"activation at {trailing_state[order_id]['activation_price']:,}€",
            to_telegram=True
        )
    
    save_trailing_state(trailing_state)

def update_trailing_state(trailing_state, current_price, current_atr, current_balance):
    logging.info(f"Checking trailing positions...")

    def calculate_stop_price(order_id, pos, entry_price, trailing_price):
        side = pos["side"]
        atr_val = pos["stop_atr"]

        if MODE == "multipliers":
            stop_price = multipliers_mode.calculate_stop_price(side, entry_price, trailing_price, atr_val)
        elif MODE == "rebuy":
            stop_price = rebuy_mode.calculate_stop_price(side, trailing_price, atr_val)
        
        pos.update({
            "trailing_price": current_price,
            "stop_price": round(stop_price, 1)
        })
        logging.info(f"📈|TRAIL| Position [{order_id}]: New price {pos['trailing_price']:,}€ - Stop {pos['stop_price']:,}€")

    
    def recalibrate_activation(order_id, pos, atr_val):
        side = pos["side"]
        entry_price = pos["entry_price"]

        if MODE == "multipliers":
            activation_distance = multipliers_mode.calculate_activation_dist(atr_val)
        elif MODE == "rebuy":
            activation_distance = rebuy_mode.calculate_activation_dist(side, atr_val, entry_price)

        activation_price = entry_price + activation_distance if side == "sell" else entry_price - activation_distance

        pos.update({
            "activation_price": round(activation_price, 1),
            "activation_atr": round(atr_val, 1)
        })
        logging.info(f"♻️|ATR| Position [{order_id}]: recalibrate activation price to {pos['activation_price']:,}€.")

    def recalibrate_stop(order_id, pos, atr_val):
        side = pos["side"]
        entry_price = pos["entry_price"]
        trailing_price = pos["trailing_price"]

        if MODE == "multipliers":
            stop_price = multipliers_mode.calculate_stop_price(side, entry_price, trailing_price, atr_val)
        elif MODE == "rebuy":
            stop_price = rebuy_mode.calculate_stop_price(side, trailing_price, atr_val)

        pos.update({
            "stop_price": round(stop_price, 1),
            "stop_atr": round(atr_val, 1)
        })
        logging.info(f"♻️|ATR| Position [{order_id}]: recalibrate stop price to {pos['stop_price']:,}€.")

    def close_position(order_id, pos):
        try:
            side = pos["side"]
            stop_price = pos["stop_price"]
            volume = pos["volume"]
            cost = pos["cost"]
            logging.info(f"⛔|CLOSE| Stop price {stop_price:,}€ hit for position [{order_id}]: placing LIMIT {side.upper()} order",
                          to_telegram=True)

            if side == "sell":
                cost = volume * stop_price
                pnl = (stop_price - pos["entry_price"]) / pos["entry_price"] * 100
            else:
                volume = cost / stop_price
                pnl = (pos["entry_price"] - stop_price) / pos["entry_price"] * 100

            closing_order = place_limit_order("XXBTZEUR", side, stop_price, volume)
            logging.info(f"|PnL| Closed position: {pnl:+.2f}% gain before fees", to_telegram=True)

            pos.update({
                "cost": round(cost, 2),
                "volume": round(volume, 8),
                "closing_time": now_str(),
                "pnl": round(pnl, 2)
            })
            save_closed_position(trailing_state[order_id], closing_order)
            del trailing_state[order_id]
            logging.info(f"Trailing position [{order_id}] closed and removed.")
        except Exception as e:
            logging.error(f"Failed to close trailing position [{order_id}]: {e}")

    for order_id, pos in list(trailing_state.items()):
        side = pos["side"]
        entry_price = pos["entry_price"]
        trailing_active = pos.get("trailing_price") is not None
        atr_val = multipliers_mode.calculate_atr_value(entry_price, current_atr) if MODE == "multipliers" else current_atr

        if not trailing_active:
            if pos["activation_atr"] * 0.8 > atr_val or atr_val > pos["activation_atr"] * 1.2:
                recalibrate_activation(order_id, pos, atr_val)

            if (side == "sell" and current_price >= pos["activation_price"]) or \
               (side == "buy" and current_price <= pos["activation_price"]):
                
                logging.info(f"⚡|ACTIVE| Trailing activated for position [{order_id}]", to_telegram=True)
                pos.update({
                    "stop_atr": pos["activation_atr"],
                    "activation_time": now_str()
                })

                calculate_stop_price(order_id, pos, entry_price, current_price)

        else:
            if (pos["stop_atr"] * 0.8 > atr_val or atr_val > pos["stop_atr"] * 1.2):
                recalibrate_stop(order_id, pos, atr_val)

            if (side == "sell" and current_price <= pos["stop_price"]) or \
               (side == "buy" and current_price >= pos["stop_price"]):
                
                if MODE == "multipliers" and side == "sell" \
                and not multipliers_mode.can_execute_sell(order_id, pos["volume"], current_balance, current_price):
                    continue

                close_position(order_id, pos)
                continue 

            if (side == "sell" and current_price > pos["trailing_price"]) or \
               (side == "buy" and current_price < pos["trailing_price"]):
                
                calculate_stop_price(order_id, pos, entry_price, current_price)
    
    save_trailing_state(trailing_state)

if __name__ == "__main__":
    main()
