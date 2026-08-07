"""Control-plane access: the ``bot_control`` key/value store, per-pair config
(``pair_config``) and session telemetry (``sessions``).

Session telemetry lives here rather than in its own module because
``finalize_session`` writes the Grafana snapshots straight into ``bot_control``.
"""

import json
import logging as stdlib_logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update

from core.db.models import BotControl, PairConfig, SessionRecord
from core.db.session import open_session

logger = stdlib_logging.getLogger(__name__)


def get_control_value(control_key: str) -> str | None:
    """Get a bot control value by key."""
    try:
        with open_session() as session:
            record = session.query(BotControl).filter(BotControl.control_key == control_key).one_or_none()
            if record is None:
                return None
            return record.control_value
    except Exception as e:
        logger.error(f"Error loading control value for {control_key}: {e}")
        return None


def set_control_value(control_key: str, control_value: str, updated_by: str | None = None) -> None:
    """Set a bot control value by key."""
    try:
        with open_session() as session:
            session.merge(
                BotControl(
                    control_key=control_key,
                    control_value=control_value,
                    updated_by=updated_by,
                )
            )
        logger.debug(f"Saved control value for {control_key}")
    except Exception as e:
        logger.error(f"Error saving control value for {control_key}: {e}")
        raise


def get_bot_paused() -> bool:
    """Get bot paused state from bot_control table.

    Defaults to True (paused) when the row is missing or the value cannot be
    read."""
    value = get_control_value("bot_paused")
    if value is None:
        logger.warning("bot_paused record missing from bot_control table; defaulting to True (paused)")
        return True
    return str(value).strip().lower() == "true"


def set_bot_paused(paused: bool, updated_by: str | None = None) -> None:
    """Set bot paused state in bot_control table."""
    set_control_value("bot_paused", "true" if paused else "false", updated_by=updated_by)


# ============================================================================
# Pair Config Operations
# ============================================================================


def load_all_pair_config() -> dict[str, dict[str, Any]]:
    """Return {pair: pair_config_dict} for all stored pairs."""
    try:
        with open_session() as session:
            return {row.pair: row.to_dict() for row in session.query(PairConfig).all()}
    except Exception as e:
        logger.error(f"Error loading pair_config: {e}")
        return {}


def upsert_pair_config(pair: str, values: dict[str, Any], updated_by: str | None = None) -> None:
    """Insert or update one pair's config row. ``values`` is a flat typed dict
    with keys target_pct, hodl_pct, k_act, min_margin, stop_pct_ll..stop_pct_hh."""
    with open_session() as session:
        session.merge(PairConfig(pair=pair, updated_by=updated_by, **values))
    logger.debug(f"Saved pair_config for {pair}")


def create_session(started_at: datetime) -> int:
    with open_session() as session:
        row = SessionRecord(started_at=started_at, status="running")
        session.add(row)
        session.flush()
        return row.id


def finalize_session(
    session_id: int,
    ended_at: datetime,
    status: str,
    balance: dict | None,
    pair_data: dict | None,
    log_messages: str | None,
) -> None:
    with open_session() as session:
        session.execute(
            update(SessionRecord)
            .where(SessionRecord.id == session_id)
            .values(
                ended_at=ended_at,
                status=status,
                log_messages=log_messages,
            )
        )
    if balance is not None:
        try:
            set_control_value("latest_balance", json.dumps(balance), updated_by="scheduler")
        except Exception as e:
            logger.error(f"Error saving latest_balance to bot_control: {e}")
    if pair_data:
        try:
            set_control_value("latest_pair_data", json.dumps(pair_data), updated_by="scheduler")
        except Exception as e:
            logger.error(f"Error saving latest_pair_data to bot_control: {e}")


def cleanup_orphaned_sessions() -> int:
    """Mark every status='running' session 'failed' (ended_at=now()) at startup.

    Only finalize_session clears 'running', so a killed or hung process leaves
    the row stuck forever. Returns the affected row count."""
    try:
        with open_session() as session:
            result = session.execute(
                update(SessionRecord)
                .where(SessionRecord.status == "running")
                .values(status="failed", ended_at=datetime.now(UTC))
            )
            return result.rowcount
    except Exception as e:
        logger.error(f"Error cleaning up orphaned sessions: {e}")
        return 0
