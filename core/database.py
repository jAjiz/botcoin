"""Database facade: connection/session ownership plus the full DAL surface.

The queries themselves live in ``core/db/`` split by domain (models, ohlc,
positions, control, jobs) and are re-exported here, so every call site keeps
using ``db.<function>`` and ``core.database`` stays the one module to patch.
This module owns the engine, the sessionmaker and ``get_session``; the domain
modules reach back for it at call time through ``core.db.session.open_session``.
"""

import logging as stdlib_logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import URL, create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

from core.config import (
    POSTGRES_DB,
    POSTGRES_HOST,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
    POSTGRES_USER,
)
from core.db.control import (
    cleanup_orphaned_sessions,
    create_session,
    finalize_session,
    get_bot_paused,
    get_control_value,
    load_all_pair_config,
    set_bot_paused,
    set_control_value,
    upsert_pair_config,
)
from core.db.jobs import (
    cleanup_orphaned_optimizer_jobs,
    complete_optimizer_job,
    create_optimizer_job,
    fail_optimizer_job,
    get_optimizer_job,
    list_optimizer_jobs,
)
from core.db.models import (
    Base,
    BotControl,
    ClosedPosition,
    OHLCData,
    OptimizerJob,
    PairConfig,
    SessionRecord,
    TrailingState,
    _to_decimal,
    _to_decimal_required,
)
from core.db.ohlc import load_ohlc_data, save_ohlc_data
from core.db.positions import (
    _state_entry_to_trailing_record,
    _trailing_record_to_state_entry,
    delete_trailing_state,
    load_closed_positions,
    load_trailing_state,
    record_position_closed,
    save_trailing_state,
)

logger = stdlib_logging.getLogger(__name__)

# ============================================================================
# Database Setup
# ============================================================================

DATABASE_URL = URL.create(
    drivername="postgresql+psycopg",
    username=POSTGRES_USER,
    password=POSTGRES_PASSWORD,
    host=POSTGRES_HOST,
    port=int(POSTGRES_PORT),
    database=POSTGRES_DB,
)

DB_CONNECT_TIMEOUT_SECONDS = 10
DB_STATEMENT_TIMEOUT_MS = 30_000


def _build_connect_args() -> dict[str, Any]:
    """libpq params so a DB stall can't hang the single scheduler thread:
    connect_timeout caps connecting, TCP keepalives catch a peer that dies
    mid-query, statement_timeout caps a slow/locked query. Each raises instead
    of blocking, so the tick fails and the next one runs."""
    return {
        "connect_timeout": DB_CONNECT_TIMEOUT_SECONDS,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 3,
        "options": f"-c statement_timeout={DB_STATEMENT_TIMEOUT_MS}",
    }


engine = create_engine(
    DATABASE_URL,
    poolclass=QueuePool,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=False,
    connect_args=_build_connect_args(),
)

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


# ============================================================================
# Session Management
# ============================================================================


@contextmanager
def get_session() -> Iterator[Session]:
    """Context manager for database sessions."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        # Rollback and re-raise without logging: every DAL function already logs
        # the failure with its own context, and the API handler logs what is left.
        # Logging here only produced a duplicate line with less information.
        session.rollback()
        raise
    finally:
        session.close()


# ============================================================================
# Health Check
# ============================================================================


def check_database_connection() -> bool:
    """Verify database connection is working."""
    try:
        with get_session() as session:
            session.execute(text("SELECT 1"))
        logger.debug("Database connection successful")
        return True
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        return False


# The re-exported DAL surface. Grouped by domain in the imports above; sorted
# here because that is what RUF022 enforces.
__all__ = [
    "DATABASE_URL",
    "DB_CONNECT_TIMEOUT_SECONDS",
    "DB_STATEMENT_TIMEOUT_MS",
    "Base",
    "BotControl",
    "ClosedPosition",
    "OHLCData",
    "OptimizerJob",
    "PairConfig",
    "SessionLocal",
    "SessionRecord",
    "TrailingState",
    "_state_entry_to_trailing_record",
    "_to_decimal",
    "_to_decimal_required",
    "_trailing_record_to_state_entry",
    "check_database_connection",
    "cleanup_orphaned_optimizer_jobs",
    "cleanup_orphaned_sessions",
    "complete_optimizer_job",
    "create_optimizer_job",
    "create_session",
    "delete_trailing_state",
    "engine",
    "fail_optimizer_job",
    "finalize_session",
    "get_bot_paused",
    "get_control_value",
    "get_optimizer_job",
    "get_session",
    "list_optimizer_jobs",
    "load_all_pair_config",
    "load_closed_positions",
    "load_ohlc_data",
    "load_trailing_state",
    "record_position_closed",
    "save_ohlc_data",
    "save_trailing_state",
    "set_bot_paused",
    "set_control_value",
    "upsert_pair_config",
]
