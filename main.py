import time
import sys
import core.logging as logging
import core.runtime as runtime
import services.telegram as telegram
from trading.parameters_manager import calculate_trading_parameters, get_volatility_level
from trading.positions_manager import create_position, update_activation_price, update_stop_price, close_position
from exchange.kraken import get_balance, get_last_prices, get_current_atr, get_order_status
from core.state import load_trailing_state, save_trailing_state, save_closed_position
from core.config import SLEEPING_INTERVAL, PAIRS, PARAM_SESSIONS, ATR_DESV_LIMIT
from core.validation import validate_config
from core.utils import now_str

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

            last_prices = get_last_prices(PAIRS)
            if not last_prices:
                logging.error(f"Could not fetch prices. Skipping session and retrying in {SLEEPING_INTERVAL}s.\n")
                time.sleep(SLEEPING_INTERVAL)
                continue
            
            for pair in PAIRS.keys():
                logging.info(f"--- Processing pair: [{pair}] ---")
                if session_count % PARAM_SESSIONS == 0:
                    calculate_trading_parameters(pair)

                current_price = last_prices.get(pair, None)
                current_atr = get_current_atr(pair)

                if current_price is None or current_atr is None:
                    logging.error(f"Could not fetch price or ATR. Skipping this pair.")
                    continue
                else:
                    vol_level = get_volatility_level(pair, current_atr)
                    logging.info(f"Market: {current_price:,.1f}€ | ATR: {current_atr:,.1f}€ ({vol_level})")
                    runtime.update_pair_data(pair, price=current_price, atr=current_atr, volatility_level=vol_level)

                    if vol_level == "LV":
                        current_atr = PAIRS[pair]['atr_50pct']
                        logging.info(f"Low volatility level. Using ATR floor (median): {current_atr:,.1f}€")

                if check_closed_positions(pair, trailing_state):
                    create_position(pair, current_balance, last_prices, current_atr, trailing_state)
                
                if pair in trailing_state and trailing_state[pair]:
                    update_trailing_state(pair, current_balance, last_prices, current_atr, trailing_state)
                    
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

def check_closed_positions(pair, trailing_state):
    if pair not in trailing_state or not trailing_state[pair]:
        return True
    
    closing_order = trailing_state[pair].get("closing_order")
    if closing_order:
        status = get_order_status(closing_order)
        if status and status not in ["pending", "open"]:
            save_closed_position(pair, trailing_state[pair])
            del trailing_state[pair]
            logging.info(f"Trailing position removed for {pair}.")
            return True
        
    return False

def update_trailing_state(pair, current_balance, last_prices, current_atr, trailing_state):
    current_price = last_prices[pair]
    pos = trailing_state[pair]
    side = pos["side"]
    trailing_active = pos.get("trailing_price") is not None
    atr_limit_max = current_atr * (1 + ATR_DESV_LIMIT)
    atr_limit_min = current_atr * (1 - ATR_DESV_LIMIT)

    if not trailing_active:
        # Recalibrate activation
        if pos["activation_atr"] < atr_limit_min or pos["activation_atr"] > atr_limit_max:
            update_activation_price(pair, pos, current_atr)
            logging.info(f"♻️ Recalibrate {side.upper()} position: activation price to {pos['activation_price']:,}€.")

        # Activation check
        if (side == "sell" and current_price >= pos["activation_price"]) or \
            (side == "buy" and current_price <= pos["activation_price"]):
            pos["activation_time"] = now_str()
            logging.info(f"[{pair}] ⚡ Activation price {pos['activation_price']:,}€ reached for {side.upper()} position.",
                            to_telegram=True)
            update_stop_price(pair, pos, current_price, current_atr)
            logging.info(f"📈 Update {side.upper()} position: new trailing price {pos['trailing_price']:,}€ | stop {pos['stop_price']:,}€")

    else:
        # Recalibrate stop
        if pos["stop_atr"] < atr_limit_min or pos["stop_atr"] > atr_limit_max:
            update_stop_price(pair, pos, pos["trailing_price"], current_atr)
            logging.info(f"♻️ Recalibrate {side.upper()} position: stop price to {pos['stop_price']:,}€.")

        # Stop hit check
        if (side == "sell" and current_price <= pos["stop_price"]) or \
            (side == "buy" and current_price >= pos["stop_price"]):
            close_position(pair, pos, current_balance, last_prices, trailing_state)
            return

        # Update trailing
        if (side == "sell" and current_price > pos["trailing_price"]) or \
            (side == "buy" and current_price < pos["trailing_price"]):
            update_stop_price(pair, pos, current_price, current_atr)
            logging.info(f"📈 Update {side.upper()} position: new trailing price {pos['trailing_price']:,}€ | stop {pos['stop_price']:,}€")


if __name__ == "__main__":
    main()
