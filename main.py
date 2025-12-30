import time
import sys
import core.logging as logging
import services.telegram as telegram
import strategies.dualk as dualk_mode
import strategies.onek as onek_mode
from exchange.kraken import get_balance, get_last_price, get_current_atr, get_closed_orders, place_limit_order
from core.state import load_trailing_state, save_trailing_state, is_processed, save_closed_position
from core.config import PAIRS, SLEEPING_INTERVAL, MODE, ASSET_MIN_ALLOCATION, RECENTER_PARAMS
from core.validation import validate_config

def main():
    # Validate configuration before starting
    if not validate_config():
        sys.exit(1)
    
    try:
        telegram.initialize_telegram()

        while True:
            if telegram.BOT_PAUSED:
                logging.info("Bot is paused. Sleeping...\n")
                time.sleep(SLEEPING_INTERVAL)
                continue

            logging.info("======== STARTING SESSION ========")
            trailing_state = load_trailing_state()
            current_balance = get_balance()
            
            if not current_balance:
                logging.error(f"Could not fetch balance. Skipping session and retrying in {SLEEPING_INTERVAL}s.\n")
                time.sleep(SLEEPING_INTERVAL)
                continue
            
            two_session_ago = int(time.time()) - SLEEPING_INTERVAL * 2
            one_week_ago = int(time.time()) - (60 * 60 * 24 * 7)
            
            for pair in PAIRS.keys():
                current_price = get_last_price(PAIRS[pair]["primary"])
                current_atr = get_current_atr(pair)

                if current_price is None or current_atr is None:
                    logging.error(f"Could not fetch price or ATR for {pair}. Skipping this pair.\n")
                    continue

                logging.info(f"[{pair}] Market: {current_price:,.1f}€ | ATR: {current_atr:,.1f}€")
                
                if pair not in trailing_state:
                    trailing_state[pair] = {}
                pair_state = trailing_state[pair]
                
                closed_orders = get_closed_orders(one_week_ago, two_session_ago)
                if closed_orders:
                    for order_id, order in closed_orders.items():
                        order_pair = order.get("descr", {}).get("pair", "")
                        if order_pair != pair:
                            continue
                        if is_processed(order_id, pair_state):
                            continue
                        process_closed_order(order_id, order, pair_state, current_atr, pair)
                
                update_trailing_state(pair_state, pair, current_price, current_atr, current_balance)
                time.sleep(1)  # To avoid hitting rate limits
            
            save_trailing_state(trailing_state)

            logging.info(f"Session complete. Sleeping for {SLEEPING_INTERVAL}s.\n")
            time.sleep(SLEEPING_INTERVAL)

    except KeyboardInterrupt:
        logging.info("BoTC stopped manually by user.\n", to_telegram=True)
    finally:
        telegram.stop_telegram_thread()

def now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

def get_reference_price(pos):
    return pos.get("reference_price", pos["entry_price"])

def process_closed_order(order_id, order, pair_state, current_atr, pair):
    logging.info(f"Processing order {order_id}...")
    entry_price = float(order["price"])
    volume = float(order["vol_exec"])
    cost = float(order["cost"])
    side = order["descr"]["type"]

    if side not in ["buy", "sell"]:
        return

    if MODE == "onek":
        new_side, atr_value, activation_price = onek_mode.process_order(side, entry_price, current_atr, pair)
    elif MODE == "dualk":
        new_side, atr_value, activation_price = dualk_mode.process_order(side, entry_price, current_atr, pair)

    existing_position = None
    for existing_id, pos in list(pair_state.items()):
        if pos["mode"] != MODE or pos["side"] != new_side or pos.get("trailing_price") is not None:
            continue
        
        reference_price = pos.get("reference_price", pos["entry_price"])
        price_diff_pct = abs(reference_price - entry_price) / reference_price * 100        
        if price_diff_pct < 0.5:  # 0.5% threshold to consider merging
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
            f"🔀[MERGE] Unified order {order_id} into existing position {existing_id}: "
            f"activation at {pair_state[existing_id]['activation_price']:,}€",
            to_telegram=True
        )
    else:
        pair_state[order_id] = {
            "side": new_side,
            "mode": MODE,
            "created_time": now_str(),
            "opening_order": [order_id],
            "entry_price": entry_price,
            "volume": volume,
            "cost": round(cost, 2),
            "activation_atr": round(atr_value, 1),
            "activation_price": round(activation_price, 1)
        }
        
        logging.info(
            f"🆕[CREATE] New trailing position {order_id} for {new_side.upper()} order: "
            f"activation at {pair_state[order_id]['activation_price']:,}€",
            to_telegram=True
        )    

def update_trailing_state(pair_state, pair, current_price, current_atr, current_balance):
    logging.info(f"Checking trailing positions...")

    def calculate_activation_price(pos, atr_val):
        side = pos["side"]
        reference_price = get_reference_price(pos)

        if MODE == "onek":
            activation_distance = onek_mode.calculate_activation_dist(side, atr_val, reference_price, pair)
        elif MODE == "dualk":
            activation_distance = dualk_mode.calculate_activation_dist(side, atr_val, pair)

        activation_price = reference_price + activation_distance if side == "sell" else reference_price - activation_distance

        pos.update({
            "activation_price": round(activation_price, 1),
            "activation_atr": round(atr_val, 1)
        })

    def calculate_stop_price(pos, atr_val, trailing_price):
        side = pos["side"]
        reference_price = get_reference_price(pos)

        if MODE == "onek":
            stop_price = onek_mode.calculate_stop_price(side, trailing_price, atr_val, pair)
        elif MODE == "dualk":
            stop_price = dualk_mode.calculate_stop_price(side, reference_price, trailing_price, atr_val, pair)
        
        pos.update({
            "trailing_price": trailing_price,
            "stop_price": round(stop_price, 1),
            "stop_atr": round(atr_val, 1)
        })
        
    def check_recenter_activation(pos, atr_val, current_price):
        atr_threshold = float(RECENTER_PARAMS[pair]["ATR_MULT"]) * atr_val
        price_threshold = float(RECENTER_PARAMS[pair]["PRICE_PCT"]) * current_price
        max_threshold = max(atr_threshold, price_threshold)

        # If both RECENTER_PARAMS are set to 0, skip recentering
        if max_threshold > 0 and abs(pos["activation_price"] - current_price) > max_threshold:
            return True
        return False

    def can_execute_sell(order_id, vol_to_sell, current_balance, current_price):
        asset = PAIRS[pair]["base"]
        fiat = PAIRS[pair]["quote"]
        
        asset_after_sell = float(current_balance.get(asset, 0)) - vol_to_sell
        fiat_after_sell = float(current_balance.get(fiat, 0)) + (vol_to_sell * current_price)

        total_value_after = (asset_after_sell * current_price) + fiat_after_sell
        if total_value_after == 0: return True

        asset_allocation_after = (asset_after_sell * current_price) / total_value_after
        min_allocation = ASSET_MIN_ALLOCATION[pair]
        
        if asset_allocation_after < min_allocation:
            logging.warning(f"🛡️[BLOCKED] Sell {order_id} by inventory ratio: {asset_allocation_after:.2%} < min: {min_allocation:.0%}.",
                            to_telegram=True)
            return False
        
        return True

    def close_position(order_id, pos):
        try:
            side = pos["side"]
            entry_price = pos["entry_price"]
            stop_price = pos["stop_price"]
            volume = pos["volume"]
            cost = pos["cost"]
            logging.info(f"⛔[CLOSE] Stop price {stop_price:,}€ hit for position {order_id}: placing LIMIT {side.upper()} order",
                          to_telegram=True)

            if side == "sell":
                cost = volume * stop_price
                pnl = (stop_price - entry_price) / entry_price * 100
            else:
                volume = cost / stop_price
                pnl = (entry_price - stop_price) / entry_price * 100

            closing_order = place_limit_order(pair, side, stop_price, volume)
            if not closing_order:
                logging.error(f"Failed to place closing order for position {order_id}. Aborting close.", to_telegram=True)
                return
            
            logging.info(f"💸[PnL] Closed position: {pnl:+.2f}% result", to_telegram=True)
            pos.update({
                "cost": round(cost, 2),
                "volume": round(volume, 8),
                "closing_time": now_str(),
                "pnl": round(pnl, 2)
            })
            save_closed_position(pos, closing_order, pair)
            del pair_state[order_id]
            logging.info(f"Trailing position {order_id} closed and removed.")
        except Exception as e:
            logging.error(f"Failed to close trailing position {order_id}: {e}")

    for order_id, pos in list(pair_state.items()):
        side = pos["side"]
        trailing_active = pos.get("trailing_price") is not None
        atr_val = current_atr 
        if MODE == "dualk":
            atr_val = dualk_mode.calculate_atr_value(side, current_price, current_atr, pair)

        if not trailing_active:
            if check_recenter_activation(pos, atr_val, current_price):
                pos["reference_price"] = current_price
                calculate_activation_price(pos, atr_val)
                logging.info(f"🔄[RECENTER] Position {order_id}: recentered activation price to {pos['activation_price']:,}€.")

            if pos["activation_atr"] * 0.8 > atr_val or atr_val > pos["activation_atr"] * 1.2:
                calculate_activation_price(pos, atr_val)
                logging.info(f"♻️[ATR] Position {order_id}: recalibrate activation price to {pos['activation_price']:,}€.")

            if (side == "sell" and current_price >= pos["activation_price"]) or \
               (side == "buy" and current_price <= pos["activation_price"]):
                logging.info(f"⚡[ACTIVE] Activation price {pos['activation_price']:,}€ reached for position {order_id}", to_telegram=True)
                pos.update({
                    "stop_atr": pos["activation_atr"],
                    "activation_time": now_str()
                })
                calculate_stop_price(pos, atr_val, current_price)
                logging.info(f"📈[TRAIL] Position {order_id}: New price {pos['trailing_price']:,}€ | Stop {pos['stop_price']:,}€")

        else:
            if (pos["stop_atr"] * 0.8 > atr_val or atr_val > pos["stop_atr"] * 1.2):
                calculate_stop_price(pos, atr_val, pos["trailing_price"])
                logging.info(f"♻️[ATR] Position {order_id}: recalibrate stop price to {pos['stop_price']:,}€.")

            if (side == "sell" and current_price <= pos["stop_price"] and can_execute_sell(order_id, pos["volume"], current_balance, current_price)) or \
               (side == "buy" and current_price >= pos["stop_price"]):
                close_position(order_id, pos)
                continue 

            if (side == "sell" and current_price > pos["trailing_price"]) or \
               (side == "buy" and current_price < pos["trailing_price"]):
                calculate_stop_price(pos, atr_val, current_price)
                logging.info(f"📈[TRAIL] Position {order_id}: New price {pos['trailing_price']:,}€ | Stop {pos['stop_price']:,}€")
                
    
if __name__ == "__main__":
    main()
