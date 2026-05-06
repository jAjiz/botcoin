import time
from collections.abc import Callable
from typing import Any

import core.database as db
import core.logging as logging
import core.runtime as runtime
from core.config import PAIRS, PARAM_SESSIONS, SLEEPING_INTERVAL
from core.utils import now_utc
from exchange.kraken import get_balance, get_last_prices
from trading.market_analyzer import get_current_atr
from trading.parameters_manager import calculate_trading_parameters, get_volatility_level
from trading.positions_manager import (
    create_position,
    is_closing_complete,
    is_open,
    tick_position,
)

_session_count: int = 0
READ_ONLY_RETRY_ATTEMPTS: int = 3

# TODO: add unit tests for trading_session.


def call_with_retry[T](func: Callable[..., T], *args: Any) -> T | None:
    for attempt in range(READ_ONLY_RETRY_ATTEMPTS):
        try:
            result = func(*args)
            if result is not None:
                return result
        except Exception as e:
            logging.warning(f"Attempt {attempt + 1}/{READ_ONLY_RETRY_ATTEMPTS} failed for {func.__name__}: {e}")
        if attempt < READ_ONLY_RETRY_ATTEMPTS - 1:
            time.sleep(1)
    return None


def trading_session() -> None:
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

    for pair in PAIRS:
        logging.info(f"--- Processing pair: [{pair}] ---")
        trailing_state[pair] = db.load_trailing_state(pair)
        current_price = last_prices.get(pair, None)
        current_atr = call_with_retry(get_current_atr, pair)

        if current_price is None or current_atr is None:
            logging.error("Could not fetch price or ATR. Skipping this pair.")
            continue

        if _session_count % PARAM_SESSIONS == 0:
            calculate_trading_parameters(pair)

        vol_level = get_volatility_level(pair, current_atr)
        logging.info(f"Market: {current_price:,.1f}€ | ATR: {current_atr:,.1f}€ ({vol_level})")
        runtime.update_pair_data(pair, price=current_price, atr=current_atr, volatility_level=vol_level)

        if is_closing_complete(trailing_state[pair]):
            db.save_closed_position(pair, trailing_state[pair])
            db.delete_trailing_state(pair)
            del trailing_state[pair]
            logging.info(f"Trailing position removed for {pair}.")

        if not trailing_state.get(pair):
            create_position(pair, current_balance, last_prices, current_atr, trailing_state)

        if is_open(trailing_state.get(pair)):
            tick_position(pair, trailing_state[pair], current_balance, last_prices, current_atr, trailing_state)

        if trailing_state.get(pair):
            db.save_trailing_state(pair, trailing_state[pair])

    _session_count += 1
    runtime.update_last_run_at(now_utc())
    logging.info(f"Session complete. Next run in {SLEEPING_INTERVAL}s.\n")
