import time
import sys
import core.logging as logging
import core.runtime as runtime
import services.telegram as telegram
from trading.parameters_manager import calculate_trading_parameters, get_volatility_level
from trading.positions_manager import calculate_activation_price, calculate_stop_price
from exchange.kraken import get_balance, get_last_price, get_current_atr, get_closed_orders, place_limit_order
from core.state import load_trailing_state, save_trailing_state, is_processed, save_closed_position
from core.config import SLEEPING_INTERVAL, PAIRS, PARAM_SESSIONS, ATR_DESV_LIMIT
from core.validation import validate_config

def main():
    # Validate configuration before starting
    if not validate_config():
        sys.exit(1)
    
    try:
        telegram.initialize_telegram()
        session_count = 0

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
            else:
                runtime.update_balance(current_balance)
            
            two_session_ago = int(time.time()) - SLEEPING_INTERVAL * 2
            one_week_ago = int(time.time()) - (60 * 60 * 24 * 7)
            closed_orders = get_closed_orders(one_week_ago, two_session_ago)
            
            for pair in PAIRS.keys():
                if session_count % PARAM_SESSIONS == 0:
                    logging.info(f"Calculating trading parameters for {pair}...")
                    calculate_trading_parameters(pair)

                current_price = get_last_price(PAIRS[pair]["primary"])
                current_atr = get_current_atr(pair)

                if current_price is None or current_atr is None:
                    logging.error(f"Could not fetch price or ATR for {pair}. Skipping this pair.")
                    continue
                else:
                    vol_level = get_volatility_level(pair, current_atr)
                    logging.info(f"[{pair}] Market: {current_price:,.1f}€ | ATR: {current_atr:,.1f}€ ({vol_level})")
                    runtime.update_pair_data(pair, price=current_price, atr=current_atr, volatility_level=vol_level)

                    if vol_level == "LV":
                        current_atr = PAIRS[pair]['atr_50pct']
                        logging.info(f"[{pair}] Low volatility level. Using ATR floor (median): {current_atr:,.1f}€")

                if pair not in trailing_state:
                    trailing_state[pair] = {}
                pair_state = trailing_state[pair]
                
                if closed_orders:
                    for order_id, order in closed_orders.items():
                        if order["descr"]["pair"] != pair:
                            continue
                        if is_processed(order_id, pair_state):
                            continue
                        process_closed_order(pair, pair_state, order_id, order, current_atr)
                
                update_trailing_state(pair, pair_state, current_price, current_atr)
                time.sleep(1)  # To avoid hitting rate limits
            
            save_trailing_state(trailing_state)
            runtime.update_trailing_state(trailing_state)

            session_count += 1
            logging.info(f"Session complete. Sleeping for {SLEEPING_INTERVAL}s.\n")
            time.sleep(SLEEPING_INTERVAL)

    except Exception as e:
        logging.error(f"BoTC encountered an error: {e}\n", to_telegram=True)
    except KeyboardInterrupt:
        logging.info("BoTC stopped manually by user.\n", to_telegram=True)
    finally:
        telegram.stop_telegram_thread()

def now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

def process_closed_order(pair, pair_state, order_id, order, atr_val):
    logging.info(f"Processing order {order_id}...")
    entry_price = float(order["price"])
    volume = float(order["vol_exec"])
    cost = float(order["cost"])
    side = order["descr"]["type"]

    if side not in ["buy", "sell"]:
        return
    
    if side == "buy":
        new_side = "sell"
    else:
        new_side = "buy"

    activation_price = calculate_activation_price(pair, new_side, entry_price, atr_val)

    pair_state[order_id] = {
        "side": new_side,
        "created_time": now_str(),
        "opening_order": [order_id],
        "entry_price": entry_price,
        "volume": volume,
        "cost": round(cost, 2),
        "activation_atr": round(atr_val, 1),
        "activation_price": round(activation_price, 1)
    }
    
    logging.info(
        f"🆕[CREATE] New trailing position {order_id} for {new_side.upper()} order: "
        f"activation at {pair_state[order_id]['activation_price']:,}€",
        to_telegram=True
    )    

def update_trailing_state(pair, pair_state, current_price, current_atr):
    logging.info(f"Checking trailing positions...")

    def update_activation_price(pos, atr_val):
        activation_price = calculate_activation_price(pair, pos["side"], pos["entry_price"], atr_val)

        pos.update({
            "activation_price": round(activation_price, 1),
            "activation_atr": round(atr_val, 1)
        })

    def update_stop_price(pos, trailing_price, atr_val):
        stop_price = calculate_stop_price(pair, pos["side"], trailing_price, atr_val)
    
        pos.update({
            "trailing_price": trailing_price,
            "stop_price": round(stop_price, 1),
            "stop_atr": round(atr_val, 1)
        })
    
    def close_position(order_id, pos):
        try:
            side = pos["side"]
            entry_price = pos["entry_price"]
            stop_price = pos["stop_price"]
            volume = pos["volume"]
            cost = pos["cost"]
            logging.info(f"⛔|CLOSE| Stop price {stop_price:,}€ hit for position {order_id}: placing LIMIT {side.upper()} order",
                          to_telegram=True)

            if side == "sell":
                cost = volume * current_price
                pnl = (current_price - entry_price) / entry_price * 100
            else:
                volume = cost / current_price
                pnl = (entry_price - current_price) / entry_price * 100

            closing_order = place_limit_order(pair, side, current_price, volume)
            if not closing_order:
                logging.error(f"Failed to place closing order for position {order_id}. Aborting close.", to_telegram=True)
                return
            logging.info(f"💸[PnL] Closed position: {pnl:+.2f}% result", to_telegram=True)

            pos.update({
                "cost": round(cost, 2),
                "volume": round(volume, 8),
                "closing_price": current_price,
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
        atr_limit_max = current_atr * (1 + ATR_DESV_LIMIT)
        atr_limit_min = current_atr * (1 - ATR_DESV_LIMIT)

        if not trailing_active:
            # Recalibrate activation
            if pos["activation_atr"] < atr_limit_min or pos["activation_atr"] > atr_limit_max:
                update_activation_price(pos, current_atr)
                logging.info(f"♻️[ATR] Position {order_id}: recalibrate activation price to {pos['activation_price']:,}€.")

            # Activation check
            if (side == "sell" and current_price >= pos["activation_price"]) or \
               (side == "buy" and current_price <= pos["activation_price"]):
                pos["activation_time"] = now_str()
                logging.info(f"⚡[ACTIVE] Activation price {pos['activation_price']:,}€ reached for position {order_id}",
                              to_telegram=True)
                update_stop_price(pos, current_price, current_atr)
                logging.info(f"📈[TRAIL] Position {order_id}: New price {pos['trailing_price']:,}€ | Stop {pos['stop_price']:,}€")

        else:
            # Recalibrate stop
            if pos["stop_atr"] < atr_limit_min or pos["stop_atr"] > atr_limit_max:
                update_stop_price(pos, pos["trailing_price"], current_atr)
                logging.info(f"♻️[ATR] Position {order_id}: recalibrate stop price to {pos['stop_price']:,}€.")

            # Stop hit check
            if (side == "sell" and current_price <= pos["stop_price"]) or \
               (side == "buy" and current_price >= pos["stop_price"]):
                close_position(order_id, pos)
                continue 

            # Update trailing
            if (side == "sell" and current_price > pos["trailing_price"]) or \
               (side == "buy" and current_price < pos["trailing_price"]):
                update_stop_price(pos, current_price, current_atr)
                logging.info(f"📈[TRAIL] Position {order_id}: New price {pos['trailing_price']:,}€ | Stop {pos['stop_price']:,}€")
                
    
if __name__ == "__main__":
    main()
