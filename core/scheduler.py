import logging as std_logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import core.database as db
import core.logging as logging
import core.runtime as runtime
from core.config import PAIRS, PARAM_SESSIONS, SESSION_FAILURE_ALERT_THRESHOLD, SLEEPING_INTERVAL, TRADING_ENABLED
from core.utils import now_utc, round_price
from exchange.kraken import get_balance, get_last_prices
from trading.market_analyzer import get_current_atr
from trading.parameters_manager import calculate_trading_parameters, get_volatility_level
from trading.positions_manager import (
    ClosingState,
    create_position,
    is_closing,
    is_open,
    manage_close_position,
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


def _notify_session_outcome(status: str, reason: str | None, elapsed_seconds: float) -> None:
    """Edge-triggered Telegram alerting: one message when the failure streak hits
    the threshold, one on recovery, ``paused`` neutral. Touches only runtime and
    the DB-independent Telegram logger, so it never masks the session's exception.

    On a completed session it also tracks an independent *overrun* streak: a session
    whose wall-clock ``elapsed_seconds`` reached ``SLEEPING_INTERVAL`` ran long enough
    to skip the next tick, so ≥ threshold in a row warns that the host is likely
    resource-starved. The overrun-recovery message is suppressed when a failure
    recovery fires the same tick (the latter already implies normal operation)."""
    if status == "completed":
        failure_recovered = runtime.register_session_success()
        if failure_recovered:
            logging.info("✅ Trading sessions recovered; data is updating again.", to_telegram=True)
        if elapsed_seconds >= SLEEPING_INTERVAL:
            count = runtime.register_session_overrun(SESSION_FAILURE_ALERT_THRESHOLD)
            if count is not None:
                logging.error(
                    f"⚠️ {count} trading sessions in a row overran the {SLEEPING_INTERVAL}s interval "
                    f"(last took {elapsed_seconds:.0f}s); ticks are being skipped and prices/positions "
                    "may lag. The host is likely resource-starved.",
                    to_telegram=True,
                )
        elif runtime.register_session_ontime() and not failure_recovered:
            logging.info(
                f"✅ Trading session timing back to normal (last took {elapsed_seconds:.0f}s).",
                to_telegram=True,
            )
    elif status == "failed":
        count = runtime.register_session_failure(SESSION_FAILURE_ALERT_THRESHOLD)
        if count is not None:
            detail = f" Last error: {reason}." if reason else ""
            logging.error(
                f"⚠️ {count} trading sessions have failed in a row.{detail} Prices and positions are not being updated.",
                to_telegram=True,
            )


def _persist_pair_state(pair: str, current: dict | None, previous: dict | None) -> bool:
    """Write a pair's state back if the session changed it. Returns False when the
    write failed, so the caller can mark the pair failed instead of letting the
    exception escape the loop."""
    if current == previous:
        return True
    try:
        if current is None:
            # Position was dropped in-memory (e.g. _drop_position); remove the DB row.
            db.delete_trailing_state(pair)
        else:
            db.save_trailing_state(pair, current)
    except Exception:
        logging.exception(f"Failed to persist state for {pair}.")
        return False
    return True


def trading_session() -> None:
    global _session_count

    collector = _SessionLogCollector()
    app_logger = std_logging.getLogger("botc")
    app_logger.addHandler(collector)

    session_id: int | None = None
    status = "failed"  # overwritten on success / paused
    failure_reason: str | None = None
    current_balance: dict | None = None
    pair_data: dict[str, dict] = {}
    started_at = now_utc()  # session start; reused for the DB row and the elapsed measure

    try:
        session_id = db.create_session(started_at)

        if db.get_bot_paused():
            logging.info("Bot is paused. Skipping session.\n")
            status = "paused"
            return

        logging.info("======== STARTING SESSION ========")
        trailing_state = {}

        current_balance = call_with_retry(get_balance)
        if current_balance is None:
            failure_reason = "could not fetch balance"
            logging.error("Could not fetch balance. Skipping session.\n")
            return
        runtime.update_balance(current_balance)

        last_prices = call_with_retry(get_last_prices, PAIRS)
        if last_prices is None:
            failure_reason = "could not fetch prices"
            logging.error("Could not fetch prices. Skipping session.\n")
            return

        failed_pairs: list[str] = []
        for pair in PAIRS:
            previous_state: dict | None = None
            try:
                logging.info(f"--- Processing pair: [{pair}] ---")
                trailing_state[pair] = db.load_trailing_state(pair)
                previous_state = dict(trailing_state[pair]) if trailing_state.get(pair) else None
                current_price = last_prices.get(pair, None)
                current_atr = call_with_retry(get_current_atr, pair)

                if current_price is None or current_atr is None:
                    # Counted as failed: an unpriced pair is an unmanaged pair.
                    logging.error("Could not fetch price or ATR. Skipping this pair.")
                    failed_pairs.append(pair)
                    continue

                if _session_count % PARAM_SESSIONS == 0 or runtime.pop_config_dirty(pair):
                    calculate_trading_parameters(pair)

                vol_level = get_volatility_level(pair, current_atr)
                logging.info(
                    f"Market: {round_price(pair, current_price):,}€ | "
                    f"ATR: {round_price(pair, current_atr):,}€ ({vol_level})"
                )
                runtime.update_pair_data(pair, price=current_price, atr=current_atr, volatility_level=vol_level)
                pair_data[pair] = {
                    "price": current_price,
                    "atr": current_atr,
                    "volatility_level": vol_level,
                }

                if not TRADING_ENABLED:
                    # Non-trading replica: record market data, never touch positions.
                    if trailing_state.get(pair):
                        logging.warning(
                            f"TRADING_ENABLED is false but {pair} has a stored position; "
                            "it is NOT being managed (trailing stop frozen). If the position "
                            "is latched (stop_at set), the owed exit is never placed either — "
                            "a breached stop stays unfilled until trading is re-enabled."
                        )
                    continue

                pos = trailing_state.get(pair)
                if is_closing(pos):
                    match manage_close_position(pair, pos, current_balance, last_prices, trailing_state):
                        case ClosingState.FILLED:
                            db.record_position_closed(pair, pos)
                            del trailing_state[pair]
                            logging.info(f"Trailing position removed for {pair}.")
                        case ClosingState.UNMANAGED:
                            # Routed into the consecutive-failure alert rather than a
                            # per-tick Telegram: an unmanaged pair is a failed pair.
                            logging.error(f"[{pair}] Could not place the owed exit order; marking the pair failed.")
                            failed_pairs.append(pair)
                        case ClosingState.PENDING:
                            pass

                if not trailing_state.get(pair):
                    create_position(pair, current_balance, last_prices, current_atr, trailing_state)

                if is_open(trailing_state.get(pair)):
                    tick_position(pair, trailing_state[pair], current_balance, last_prices, current_atr, trailing_state)
            except Exception:
                logging.exception(f"Error processing {pair}; skipping this pair for the rest of the session.")
                failed_pairs.append(pair)
            finally:
                # In `finally` so a closing order placed just before a failure still
                # reaches the DB; otherwise it lives on at Kraken with its id lost.
                persisted = _persist_pair_state(pair, trailing_state.get(pair), previous_state)
                if not persisted and pair not in failed_pairs:
                    failed_pairs.append(pair)

        _session_count += 1
        runtime.update_last_run_at(now_utc())
        if failed_pairs:
            status = "failed"
            failure_reason = f"pair errors: {', '.join(failed_pairs)}"
            logging.error(f"======== SESSION COMPLETE WITH ERRORS ({failure_reason}) ========")
        else:
            status = "completed"
            logging.info("======== SESSION COMPLETE ========")
    except Exception as exc:
        logging.exception("Unhandled exception in trading_session")
        status = "failed"
        failure_reason = failure_reason or f"unhandled exception: {exc}"
        raise
    finally:
        app_logger.removeHandler(collector)
        ended_at = now_utc()
        elapsed_seconds = (ended_at - started_at).total_seconds()
        _notify_session_outcome(status, failure_reason, elapsed_seconds)
        if session_id is not None:
            db.finalize_session(
                session_id=session_id,
                ended_at=ended_at,
                status=status,
                balance=current_balance,
                pair_data=pair_data,
                log_messages="\n".join(collector.lines) or None,
            )
