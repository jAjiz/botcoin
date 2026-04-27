import time
import core.logging as logging
import core.runtime as runtime
import core.database as db
from exchange.kraken import get_balance, get_last_prices, get_order_status
from core.config import SLEEPING_INTERVAL, PAIRS, PARAM_SESSIONS, ATR_DESV_LIMIT
from core.utils import now_utc
from trading.parameters_manager import calculate_trading_parameters, get_volatility_level
from trading.market_analyzer import get_current_atr
from trading.positions_manager import (
    close_position,
    create_position,
    refresh_position,
    update_activation_price,
    update_stop_price,
)

_session_count = 0
READ_ONLY_RETRY_ATTEMPTS = 3


def call_with_retry(func, *args):
    for attempt in range(READ_ONLY_RETRY_ATTEMPTS):
        try:
            result = func(*args)
            if result is not None:
                return result
        except Exception:
            pass
        if attempt < READ_ONLY_RETRY_ATTEMPTS - 1:
            time.sleep(1)
    return None


def trading_session():
    global _session_count

    if db.get_bot_paused():
        logging.info("Bot is paused. Skipping session.\n")
        return

    logging.info("======== STARTING SESSION ========")
    trailing_state = {}

    current_balance = call_with_retry(get_balance)
    if current_balance is None:
        logging.error("Could not fetch balance. Skipping session.\n")
        return
    runtime.update_balance(current_balance)

    last_prices = call_with_retry(get_last_prices, PAIRS)
    if last_prices is None:
        logging.error("Could not fetch prices. Skipping session.\n")
        return

    for pair in PAIRS.keys():
        logging.info(f"--- Processing pair: [{pair}] ---")
        trailing_state[pair] = db.load_trailing_state(pair)
        current_price = last_prices.get(pair, None)
        current_atr = call_with_retry(get_current_atr, pair)

        if current_price is None or current_atr is None:
            logging.error(f"Could not fetch price or ATR. Skipping this pair.")
            continue

        if _session_count % PARAM_SESSIONS == 0:
            calculate_trading_parameters(pair)

        vol_level = get_volatility_level(pair, current_atr)
        logging.info(f"Market: {current_price:,.1f}€ | ATR: {current_atr:,.1f}€ ({vol_level})")
        runtime.update_pair_data(pair, price=current_price, atr=current_atr, volatility_level=vol_level)

        if check_closed_position(pair, trailing_state):
            create_position(pair, current_balance, last_prices, current_atr, trailing_state)

        if check_open_position(pair, trailing_state):
            _update_trailing_state(pair, current_balance, last_prices, current_atr, trailing_state)

        if trailing_state.get(pair):
            db.save_trailing_state(pair, trailing_state[pair])

    _session_count += 1
    runtime.update_last_run_at(now_utc())
    logging.info(f"Session complete. Next run in {SLEEPING_INTERVAL}s.\n")


def check_closed_position(pair, trailing_state):
    if pair not in trailing_state or not trailing_state[pair]:
        return True

    closing_order = trailing_state[pair].get("closing_order_id")
    if closing_order:
        status = get_order_status(closing_order)
        if status and status not in ["pending", "open"]:
            db.save_closed_position(pair, trailing_state[pair])
            db.delete_trailing_state(pair)
            del trailing_state[pair]
            logging.info(f"Trailing position removed for {pair}.")
            return True

    return False


def check_open_position(pair, trailing_state):
    if pair not in trailing_state or not trailing_state[pair]:
        return False

    closing_order = trailing_state[pair].get("closing_order_id")
    if closing_order:
        return False

    return True


def _update_trailing_state(pair, current_balance, last_prices, current_atr, trailing_state):
    current_price = last_prices[pair]
    pos = trailing_state[pair]
    side = pos["side"]
    trailing_active = pos.get("trailing_price") is not None
    atr_limit_max = current_atr * (1 + ATR_DESV_LIMIT)
    atr_limit_min = current_atr * (1 - ATR_DESV_LIMIT)

    if not refresh_position(pair, pos, current_balance, last_prices, trailing_state):
        return

    if not trailing_active:
        if pos["activation_atr"] < atr_limit_min or pos["activation_atr"] > atr_limit_max:
            update_activation_price(pair, pos, current_atr)
            logging.info(f"♻️ Recalibrate {side.upper()} position: activation price to {pos['activation_price']:,}€.")

        if (side == "sell" and current_price >= pos["activation_price"]) or \
                (side == "buy" and current_price <= pos["activation_price"]):
            pos["activated_at"] = now_utc()
            logging.info(f"[{pair}] ⚡ Activation price {pos['activation_price']:,}€ reached for {side.upper()} position.",
                         to_telegram=True)
            update_stop_price(pair, pos, current_price, current_atr)
            logging.info(f"📈 Update {side.upper()} position: new trailing price {pos['trailing_price']:,}€ | stop {pos['stop_price']:,}€")

    else:
        if pos["stop_atr"] < atr_limit_min or pos["stop_atr"] > atr_limit_max:
            update_stop_price(pair, pos, pos["trailing_price"], current_atr)
            logging.info(f"♻️ Recalibrate {side.upper()} position: stop price to {pos['stop_price']:,}€.")

        if (side == "sell" and current_price <= pos["stop_price"]) or \
                (side == "buy" and current_price >= pos["stop_price"]):
            close_position(pair, pos, last_prices)
            return

        if (side == "sell" and current_price > pos["trailing_price"]) or \
                (side == "buy" and current_price < pos["trailing_price"]):
            update_stop_price(pair, pos, current_price, current_atr)
            logging.info(f"📈 Update {side.upper()} position: new trailing price {pos['trailing_price']:,}€ | stop {pos['stop_price']:,}€")
