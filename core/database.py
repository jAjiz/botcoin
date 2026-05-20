import logging as stdlib_logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pandas as pd
from sqlalchemy import (
    URL,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    and_,
    create_engine,
    desc,
    func,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, declarative_base, sessionmaker
from sqlalchemy.pool import QueuePool

from core.config import (
    POSTGRES_DB,
    POSTGRES_HOST,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
    POSTGRES_USER,
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

# Create engine with connection pooling
engine = create_engine(
    DATABASE_URL,
    poolclass=QueuePool,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,  # Verify connections before using
    pool_recycle=3600,  # Recycle connections after 1 hour
    echo=False,  # Set to True for SQL debugging
)

# Session factory
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

# ORM Base
Base = declarative_base()


# TODO: split this module into core/db/models.py, core/db/ohlc.py,
#       core/db/positions.py, and core/db/control.py as the schema grows.

# ============================================================================
# ORM Models
# ============================================================================


class OHLCData(Base):
    """OHLC market data for trading pairs."""

    __tablename__ = "ohlc_data"

    pair = Column(Text, primary_key=True, nullable=False)
    timeframe_minutes = Column(Integer, primary_key=True, nullable=False)
    time = Column(BigInteger, primary_key=True, nullable=False)
    source_exchange = Column(Text, nullable=False, default="kraken")
    open = Column(Numeric(20, 10), nullable=False)
    high = Column(Numeric(20, 10), nullable=False)
    low = Column(Numeric(20, 10), nullable=False)
    close = Column(Numeric(20, 10), nullable=False)
    vwap = Column(Numeric(20, 10), nullable=True)
    volume = Column(Numeric(28, 10), nullable=True)
    count = Column(Integer, nullable=True)
    atr = Column(Numeric(20, 10), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        CheckConstraint("timeframe_minutes > 0", name="ck_ohlc_data_timeframe_positive"),
        CheckConstraint("count IS NULL OR count >= 0", name="ck_ohlc_data_count_nonnegative"),
        CheckConstraint("high >= low", name="ck_ohlc_data_price_range_valid"),
        CheckConstraint("open >= low AND open <= high", name="ck_ohlc_data_open_in_range"),
        CheckConstraint("close >= low AND close <= high", name="ck_ohlc_data_close_in_range"),
        Index("ix_ohlc_data_pair_timeframe_time_desc", pair, timeframe_minutes, desc(time)),
    )

    def to_dict(self) -> dict[str, Any]:
        # Only return useful fields for DataFrame construction
        return {
            "time": self.time,
            "open": float(self.open),
            "high": float(self.high),
            "low": float(self.low),
            "close": float(self.close),
            "vwap": float(self.vwap) if self.vwap is not None else None,
            "volume": float(self.volume) if self.volume is not None else None,
            "count": self.count,
            "atr": float(self.atr) if self.atr is not None else None,
        }


class ClosedPosition(Base):
    """Closed trading positions."""

    __tablename__ = "closed_positions"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    pair = Column(Text, nullable=False)
    side = Column(Text, nullable=False)
    volume = Column(Numeric(28, 10), nullable=False)
    entry_price = Column(Numeric(20, 10), nullable=False)
    activation_atr = Column(Numeric(20, 10), nullable=True)
    activation_price = Column(Numeric(20, 10), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    trailing_price = Column(Numeric(20, 10), nullable=True)
    stop_price = Column(Numeric(20, 10), nullable=True)
    stop_atr = Column(Numeric(20, 10), nullable=True)
    closing_price = Column(Numeric(20, 10), nullable=False)
    closing_order_id = Column(Text, nullable=False, unique=True)
    closed_at = Column(DateTime(timezone=True), nullable=False)
    pnl_percent = Column(Numeric(10, 4), nullable=False)
    inserted_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))

    __table_args__ = (
        CheckConstraint("side IN ('buy', 'sell')", name="ck_closed_positions_side_valid"),
        CheckConstraint("volume > 0", name="ck_closed_positions_volume_positive"),
        CheckConstraint("entry_price > 0", name="ck_closed_positions_entry_price_positive"),
        CheckConstraint("closing_price > 0", name="ck_closed_positions_closing_price_positive"),
        Index("ix_closed_positions_pair_closed_at_desc", pair, desc(closed_at)),
        Index("ix_closed_positions_closed_at_desc", desc(closed_at)),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "pair": self.pair,
            "side": self.side,
            "volume": float(self.volume),
            "entry_price": float(self.entry_price),
            "activation_atr": float(self.activation_atr) if self.activation_atr is not None else None,
            "activation_price": float(self.activation_price) if self.activation_price is not None else None,
            "created_at": self.created_at,
            "activated_at": self.activated_at,
            "trailing_price": float(self.trailing_price) if self.trailing_price is not None else None,
            "stop_price": float(self.stop_price) if self.stop_price is not None else None,
            "stop_atr": float(self.stop_atr) if self.stop_atr is not None else None,
            "closing_price": float(self.closing_price),
            "closing_order_id": self.closing_order_id,
            "closed_at": self.closed_at,
            "pnl_percent": float(self.pnl_percent),
        }


class TrailingState(Base):
    """Active trailing positions state."""

    __tablename__ = "trailing_state"

    pair = Column(Text, primary_key=True, nullable=False)
    side = Column(Text, nullable=False)
    volume = Column(Numeric(28, 10), nullable=False)
    entry_price = Column(Numeric(20, 10), nullable=False)
    activation_atr = Column(Numeric(20, 10), nullable=False)
    activation_price = Column(Numeric(20, 10), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    trailing_price = Column(Numeric(20, 10), nullable=True)
    stop_price = Column(Numeric(20, 10), nullable=True)
    stop_atr = Column(Numeric(20, 10), nullable=True)
    closing_order_id = Column(Text, nullable=True)
    closing_price = Column(Numeric(20, 10), nullable=True)
    closing_requested_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        CheckConstraint("side IN ('buy', 'sell')", name="ck_trailing_state_side_valid"),
        CheckConstraint("volume > 0", name="ck_trailing_state_volume_positive"),
        CheckConstraint("entry_price > 0", name="ck_trailing_state_entry_price_positive"),
        CheckConstraint(
            "(trailing_price IS NULL AND stop_price IS NULL AND stop_atr IS NULL) OR "
            "(trailing_price IS NOT NULL AND stop_price IS NOT NULL AND stop_atr IS NOT NULL)",
            name="ck_trailing_state_stop_fields_consistent",
        ),
        Index("ix_trailing_state_closing_order_id", closing_order_id),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair": self.pair,
            "side": self.side,
            "volume": float(self.volume),
            "entry_price": float(self.entry_price),
            "activation_atr": float(self.activation_atr),
            "activation_price": float(self.activation_price),
            "created_at": self.created_at,
            "activated_at": self.activated_at,
            "trailing_price": float(self.trailing_price) if self.trailing_price is not None else None,
            "stop_price": float(self.stop_price) if self.stop_price is not None else None,
            "stop_atr": float(self.stop_atr) if self.stop_atr is not None else None,
            "closing_order_id": self.closing_order_id,
            "closing_price": float(self.closing_price) if self.closing_price is not None else None,
            "closing_requested_at": self.closing_requested_at,
            "updated_at": self.updated_at,
        }


class BotControl(Base):
    """Bot control flags and settings."""

    __tablename__ = "bot_control"

    control_key = Column(Text, primary_key=True, nullable=False)
    control_value = Column(Text, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    updated_by = Column(Text, nullable=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "control_key": self.control_key,
            "control_value": self.control_value,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


class SessionRecord(Base):
    """Per-scheduler-tick session telemetry."""

    __tablename__ = "sessions"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    started_at = Column(DateTime(timezone=True), nullable=False, index=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(16), nullable=False)
    balance = Column(JSONB, nullable=True)
    pair_data = Column(JSONB, nullable=True)
    log_messages = Column(Text, nullable=True)


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
    except Exception as e:
        session.rollback()
        logger.error(f"Database session error: {e}")
        raise
    finally:
        session.close()


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _to_decimal_required(value: Any) -> Decimal:
    return Decimal(str(value))


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


# ============================================================================
# OHLC Data Operations
# ============================================================================


def load_ohlc_data(
    pair: str,
    timeframe: int,
    since_time: int | None = None,
    before_time: int | None = None,
    limit: int | None = None,
) -> pd.DataFrame:
    """Load OHLC data from the database.

    Args:
        pair: Trading pair.
        timeframe: Candle timeframe in minutes.
        since_time: Optional inclusive lower bound on `time` (Unix timestamp).
        before_time: Optional exclusive upper bound on `time` (Unix timestamp).
        limit: Optional maximum number of rows to return.

    Returns:
        A DataFrame with OHLC data and a datetime column, ordered newest first.
    """
    try:
        with get_session() as session:
            query = session.query(OHLCData).filter(and_(OHLCData.pair == pair, OHLCData.timeframe_minutes == timeframe))
            if since_time is not None:
                query = query.filter(OHLCData.time >= since_time)
            if before_time is not None:
                query = query.filter(OHLCData.time < before_time)
            query = query.order_by(desc(OHLCData.time))
            if limit is not None:
                query = query.limit(limit)
            records = query.all()
            if not records:
                return pd.DataFrame()
            df = pd.DataFrame([r.to_dict() for r in records])
            df["dtime"] = pd.to_datetime(pd.to_numeric(df["time"]), unit="s")
            logger.debug(f"Fetched {len(df)} OHLC records for {pair}")
            return df
    except Exception as e:
        logger.error(f"Error fetching OHLC data for {pair}: {e}")
        return pd.DataFrame()


def save_ohlc_data(pair: str, timeframe: int, df: pd.DataFrame) -> None:
    """Save OHLC data to the database.

    Args:
        pair: Trading pair.
        timeframe: Candle timeframe in minutes.
        df: DataFrame containing OHLC columns.
    """
    try:
        if df.empty:
            logger.warning(f"Empty DataFrame provided for {pair}")
            return
        records = df.to_dict("records")
        rows = [
            {
                "pair": pair,
                "timeframe_minutes": timeframe,
                "time": int(r["time"]),
                "open": _to_decimal_required(r["open"]),
                "high": _to_decimal_required(r["high"]),
                "low": _to_decimal_required(r["low"]),
                "close": _to_decimal_required(r["close"]),
                "vwap": Decimal(str(r["vwap"])) if "vwap" in r and pd.notna(r["vwap"]) else None,
                "volume": Decimal(str(r["volume"])) if "volume" in r and pd.notna(r["volume"]) else None,
                "count": int(r["count"]) if "count" in r and pd.notna(r["count"]) else None,
                "atr": Decimal(str(r["atr"])) if "atr" in r and pd.notna(r["atr"]) else None,
            }
            for r in records
        ]
        with get_session() as session:
            stmt = (
                pg_insert(OHLCData)
                .values(rows)
                .on_conflict_do_update(
                    index_elements=["pair", "timeframe_minutes", "time"],
                    set_={
                        "open": pg_insert(OHLCData).excluded.open,
                        "high": pg_insert(OHLCData).excluded.high,
                        "low": pg_insert(OHLCData).excluded.low,
                        "close": pg_insert(OHLCData).excluded.close,
                        "vwap": pg_insert(OHLCData).excluded.vwap,
                        "volume": pg_insert(OHLCData).excluded.volume,
                        "count": pg_insert(OHLCData).excluded.count,
                        "atr": pg_insert(OHLCData).excluded.atr,
                        "updated_at": func.now(),
                    },
                )
            )
            session.execute(stmt)
            logger.debug(f"Saved {len(rows)} OHLC records for {pair}")
    except Exception as e:
        logger.error(f"Error saving OHLC data for {pair}: {e}")
        raise


# ============================================================================
# Closed Position Operations
# ============================================================================


def save_closed_position(pair: str, position_data: dict[str, Any]) -> None:
    """Persist a closed position to the database.

    Args:
        pair: Trading pair.
        position_data: Dictionary containing closed position details.
    """
    try:
        record = ClosedPosition(
            pair=pair,
            side=position_data["side"],
            volume=_to_decimal_required(position_data["volume"]),
            entry_price=_to_decimal_required(position_data["entry_price"]),
            activation_atr=_to_decimal(position_data.get("activation_atr")),
            activation_price=_to_decimal(position_data.get("activation_price")),
            created_at=position_data["created_at"],
            activated_at=position_data.get("activated_at"),
            trailing_price=_to_decimal(position_data.get("trailing_price")),
            stop_price=_to_decimal(position_data.get("stop_price")),
            stop_atr=_to_decimal(position_data.get("stop_atr")),
            closing_price=_to_decimal_required(position_data["closing_price"]),
            closing_order_id=position_data["closing_order_id"],
            closed_at=datetime.now(UTC),
            pnl_percent=_to_decimal_required(position_data["pnl_percent"]),
        )
        with get_session() as session:
            session.add(record)
        logger.debug(f"Saved closed position for {pair} order {position_data['closing_order_id']}")
    except Exception as e:
        logger.error(f"Error saving closed position: {e}")
        raise


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
        with get_session() as session:
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
        with get_session() as session:
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
        Dictionary containing trailing state details, or None if not found.
    """
    try:
        with get_session() as session:
            record = session.query(TrailingState).filter(TrailingState.pair == pair).one_or_none()
            if record is None:
                return None
            state_entry = _trailing_record_to_state_entry(record)
            logger.debug(f"Fetched trailing state for {pair}")
            return state_entry
    except Exception as e:
        logger.error(f"Error loading trailing state for {pair}: {e}")
        return None


def delete_trailing_state(pair: str) -> bool:
    """Delete active trailing state for a trading pair.

    Args:
        pair: Trading pair.

    Returns:
        True if the trailing state was deleted, False otherwise.
    """
    try:
        with get_session() as session:
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


# ============================================================================
# Bot Control Operations
# ============================================================================


def get_control_value(control_key: str) -> str | None:
    """Get a bot control value by key."""
    try:
        with get_session() as session:
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
        with get_session() as session:
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
# Session Telemetry Operations
# ============================================================================


def create_session(started_at: datetime) -> int:
    with get_session() as session:
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
    with get_session() as session:
        session.execute(
            update(SessionRecord)
            .where(SessionRecord.id == session_id)
            .values(
                ended_at=ended_at,
                status=status,
                balance=balance,
                pair_data=pair_data,
                log_messages=log_messages,
            )
        )
