import logging as std_logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import core.database as db
import core.logging as logging
import core.runtime as runtime
from core.config import PAIRS, PARAM_SESSIONS
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


class _SessionLogCollector(std_logging.Handler):
    """Captures application logger records as plain text lines for session persistence."""

    def __init__(self) -> None:
        super().__init__(level=std_logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: std_logging.LogRecord) -> None:
        ts = datetime.fromtimestamp(record.created, tz=UTC).isoformat()
        self.lines.append(f"{ts} {record.levelname} {record.getMessage()}")


def call_with_retry[T](func: Callable[..., T], *args: Any) -> T | None:
    for attempt in range(READ_ONLY_RETRY_ATTEMPTS):
        result = func(*args)
        if result is not None:
            return result
        if attempt < READ_ONLY_RETRY_ATTEMPTS - 1:
            time.sleep(1)
    return None


def trading_session() -> None:
    global _session_count

    collector = _SessionLogCollector()
    app_logger = std_logging.getLogger("botc")
    app_logger.addHandler(collector)

    session_id: int | None = None
    status = "failed"  # overwritten on success / paused
    current_balance: dict | None = None
    pair_data: dict[str, dict] = {}

    try:
        session_id = db.create_session(now_utc())

        if db.get_bot_paused():
            logging.info("Bot is paused. Skipping session.\n")
            status = "paused"
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
            previous_state = dict(trailing_state[pair]) if trailing_state.get(pair) else None
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
            pair_data[pair] = {
                "price": current_price,
                "atr": current_atr,
                "volatility_level": vol_level,
            }

            if is_closing_complete(trailing_state.get(pair)):
                # TODO: save_closed_position and delete_trailing_state are separate
                # transactions; a crash between them re-detects the close next session
                # and double-records it. Wrap both in one transaction for idempotency.
                db.save_closed_position(pair, trailing_state[pair])
                db.delete_trailing_state(pair)
                del trailing_state[pair]
                logging.info(f"Trailing position removed for {pair}.")

            if not trailing_state.get(pair):
                create_position(pair, current_balance, last_prices, current_atr, trailing_state)

            if is_open(trailing_state.get(pair)):
                tick_position(pair, trailing_state[pair], current_balance, last_prices, current_atr, trailing_state)

            current_state = trailing_state.get(pair)
            if current_state != previous_state:
                if current_state is None:
                    # Position was dropped in-memory (e.g. _drop_position); remove the DB row.
                    db.delete_trailing_state(pair)
                else:
                    db.save_trailing_state(pair, current_state)

        _session_count += 1
        runtime.update_last_run_at(now_utc())
        logging.info("======== SESSION COMPLETE ========")
        status = "completed"
    except Exception:
        logging.exception("Unhandled exception in trading_session")
        status = "failed"
        raise
    finally:
        app_logger.removeHandler(collector)
        if session_id is not None:
            db.finalize_session(
                session_id=session_id,
                ended_at=now_utc(),
                status=status,
                balance=current_balance,
                pair_data=pair_data,
                log_messages="\n".join(collector.lines) or None,
            )
