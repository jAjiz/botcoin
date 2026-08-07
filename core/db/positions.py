"""Position access: closed positions (``closed_positions``) and the active
trailing state (``trailing_state``)."""

import logging as stdlib_logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.db.models import ClosedPosition, TrailingState, _to_decimal, _to_decimal_required
from core.db.session import open_session

logger = stdlib_logging.getLogger(__name__)


def _state_entry_to_trailing_record(pair: str, position_data: dict[str, Any]) -> TrailingState:
    return TrailingState(
        pair=pair,
        side=position_data["side"],
        volume=_to_decimal_required(position_data["volume"]),
        entry_price=_to_decimal_required(position_data["entry_price"]),
        activation_atr=_to_decimal_required(position_data["activation_atr"]),
        activation_price=_to_decimal_required(position_data["activation_price"]),
        created_at=position_data["created_at"],
        activated_at=position_data.get("activated_at"),
        trailing_price=_to_decimal(position_data.get("trailing_price")),
        stop_price=_to_decimal(position_data.get("stop_price")),
        stop_atr=_to_decimal(position_data.get("stop_atr")),
        closing_order_id=position_data.get("closing_order_id"),
        closing_price=_to_decimal(position_data.get("closing_price")),
        closing_requested_at=position_data.get("closing_requested_at"),
    )


def _trailing_record_to_state_entry(record: TrailingState) -> dict[str, Any]:
    state_entry: dict[str, Any] = {
        "side": record.side,
        "volume": float(record.volume),
        "entry_price": float(record.entry_price),
        "activation_atr": float(record.activation_atr),
        "activation_price": float(record.activation_price),
        "created_at": record.created_at,
    }
    if record.activated_at is not None:
        state_entry["activated_at"] = record.activated_at
    if record.trailing_price is not None:
        state_entry["trailing_price"] = float(record.trailing_price)
    if record.stop_price is not None:
        state_entry["stop_price"] = float(record.stop_price)
    if record.stop_atr is not None:
        state_entry["stop_atr"] = float(record.stop_atr)
    if record.closing_order_id is not None:
        state_entry["closing_order_id"] = record.closing_order_id
    if record.closing_price is not None:
        state_entry["closing_price"] = float(record.closing_price)
    if record.closing_requested_at is not None:
        state_entry["closing_requested_at"] = record.closing_requested_at
    return state_entry


def record_position_closed(pair: str, position_data: dict[str, Any]) -> None:
    """Persist a completed close atomically: insert into closed_positions and
    delete the pair's trailing_state in ONE transaction. The insert is idempotent
    on closing_order_id so a crash-retry converges instead of violating the
    unique constraint (which previously wedged the session loop)."""
    values = {
        "pair": pair,
        "side": position_data["side"],
        "volume": _to_decimal_required(position_data["volume"]),
        "entry_price": _to_decimal_required(position_data["entry_price"]),
        "activation_atr": _to_decimal(position_data.get("activation_atr")),
        "activation_price": _to_decimal(position_data.get("activation_price")),
        "created_at": position_data["created_at"],
        "activated_at": position_data.get("activated_at"),
        "trailing_price": _to_decimal(position_data.get("trailing_price")),
        "stop_price": _to_decimal(position_data.get("stop_price")),
        "stop_atr": _to_decimal(position_data.get("stop_atr")),
        "closing_price": _to_decimal_required(position_data["closing_price"]),
        "closing_order_id": position_data["closing_order_id"],
        "closed_at": datetime.now(UTC),
        "pnl_percent": _to_decimal_required(position_data["pnl_percent"]),
    }
    with open_session() as session:
        result = session.execute(
            pg_insert(ClosedPosition).values(values).on_conflict_do_nothing(index_elements=["closing_order_id"])
        )
        if result.rowcount == 0:
            logger.warning(
                f"Closed position insert for {pair} was a no-op (closing_order_id "
                f"{position_data['closing_order_id']} already exists). Expected on a "
                "crash-retry; investigate if this is unexpected."
            )
        session.query(TrailingState).filter(TrailingState.pair == pair).delete()
    logger.debug(f"Recorded closed position for {pair} order {position_data['closing_order_id']}")


def load_closed_positions(pair: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    """Load closed positions ordered by closed_at descending.

    Args:
        pair: Optional trading pair filter. If None, loads all positions.
        limit: Optional maximum number of records to return.

    Returns:
        List of closed position dictionaries, newest first.
        Returns an empty list on error.
    """
    try:
        with open_session() as session:
            query = session.query(ClosedPosition)
            if pair is not None:
                query = query.filter(ClosedPosition.pair == pair)
            query = query.order_by(desc(ClosedPosition.closed_at))
            if limit is not None:
                query = query.limit(limit)
            records = query.all()
            result = [r.to_dict() for r in records]
            logger.debug(f"Fetched {len(result)} closed positions" + (f" for {pair}" if pair else ""))
            return result
    except Exception as e:
        error_msg = "Error loading closed positions" + (f" for {pair}" if pair else "")
        logger.error(f"{error_msg}: {e}")
        return []


# ============================================================================
# Trailing State Operations
# ============================================================================


def save_trailing_state(pair: str, position_data: dict[str, Any]) -> None:
    """Persist active trailing state for a trading pair.

    Args:
        pair: Trading pair.
        position_data: Dictionary containing trailing state details.
    """
    try:
        with open_session() as session:
            session.merge(_state_entry_to_trailing_record(pair, position_data))
        logger.debug(f"Saved trailing state for {pair}")
    except Exception as e:
        logger.error(f"Error saving trailing state for {pair}: {e}")
        raise


def load_trailing_state(pair: str) -> dict[str, Any] | None:
    """Load active trailing state for a trading pair.

    Args:
        pair: Trading pair.

    Returns:
        Dictionary containing trailing state details, or None only if no row
        exists. DB errors are logged and re-raised, not returned as None.
    """
    try:
        with open_session() as session:
            record = session.query(TrailingState).filter(TrailingState.pair == pair).one_or_none()
            if record is None:
                return None
            state_entry = _trailing_record_to_state_entry(record)
            logger.debug(f"Fetched trailing state for {pair}")
            return state_entry
    except Exception as e:
        logger.error(f"Error loading trailing state for {pair}: {e}")
        raise


def delete_trailing_state(pair: str) -> bool:
    """Delete active trailing state for a trading pair.

    Args:
        pair: Trading pair.

    Returns:
        True if the trailing state was deleted, False otherwise.
    """
    try:
        with open_session() as session:
            record = session.query(TrailingState).filter(TrailingState.pair == pair).one_or_none()
            if record is None:
                logger.debug(f"No trailing state found for {pair}")
                return False
            session.delete(record)
        logger.debug(f"Deleted trailing state for {pair}")
        return True
    except Exception as e:
        logger.error(f"Error deleting trailing state for {pair}: {e}")
        return False
