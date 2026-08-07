"""SQLAlchemy models and the Decimal converters that feed them.

Leaf module: it must not import ``core.database`` (which imports every module in
this package), so it holds no engine, no session and no query.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    desc,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base

Base = declarative_base()


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
        """Only the fields needed to build the OHLC DataFrame."""
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


class PairConfig(Base):
    """Per-pair dynamic trading configuration (DB-authoritative, seeded from env)."""

    __tablename__ = "pair_config"

    pair = Column(Text, primary_key=True, nullable=False)
    target_pct = Column(Numeric(6, 3), nullable=False, default=0)
    hodl_pct = Column(Numeric(6, 3), nullable=False, default=0)
    k_act = Column(Numeric(10, 4), nullable=True)
    min_margin = Column(Numeric(12, 8), nullable=False, default=0)
    stop_pct_ll = Column(Numeric(4, 3), nullable=False, default=0.90)
    stop_pct_lv = Column(Numeric(4, 3), nullable=False, default=0.90)
    stop_pct_mv = Column(Numeric(4, 3), nullable=False, default=0.90)
    stop_pct_hv = Column(Numeric(4, 3), nullable=False, default=0.90)
    stop_pct_hh = Column(Numeric(4, 3), nullable=False, default=0.90)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    updated_by = Column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("target_pct >= 0 AND target_pct <= 100", name="ck_pair_config_target_pct_range"),
        CheckConstraint("hodl_pct >= 0 AND hodl_pct <= 100", name="ck_pair_config_hodl_pct_range"),
        CheckConstraint("k_act IS NULL OR k_act >= 0", name="ck_pair_config_k_act_nonneg"),
        CheckConstraint("min_margin >= 0", name="ck_pair_config_min_margin_nonneg"),
        CheckConstraint("stop_pct_ll >= 0 AND stop_pct_ll <= 1", name="ck_pair_config_stop_ll_range"),
        CheckConstraint("stop_pct_lv >= 0 AND stop_pct_lv <= 1", name="ck_pair_config_stop_lv_range"),
        CheckConstraint("stop_pct_mv >= 0 AND stop_pct_mv <= 1", name="ck_pair_config_stop_mv_range"),
        CheckConstraint("stop_pct_hv >= 0 AND stop_pct_hv <= 1", name="ck_pair_config_stop_hv_range"),
        CheckConstraint("stop_pct_hh >= 0 AND stop_pct_hh <= 1", name="ck_pair_config_stop_hh_range"),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair": self.pair,
            "target_pct": float(self.target_pct),
            "hodl_pct": float(self.hodl_pct),
            "k_act": float(self.k_act) if self.k_act is not None else None,
            "min_margin": float(self.min_margin),
            "stop_pct_ll": float(self.stop_pct_ll),
            "stop_pct_lv": float(self.stop_pct_lv),
            "stop_pct_mv": float(self.stop_pct_mv),
            "stop_pct_hv": float(self.stop_pct_hv),
            "stop_pct_hh": float(self.stop_pct_hh),
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


class OptimizerJob(Base):
    """Optimizer job state and results."""

    __tablename__ = "optimizer_jobs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    pair = Column(Text, nullable=False)
    mode = Column(Text, nullable=False)
    split_method = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    request = Column(JSONB, nullable=False)
    result = Column(JSONB, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('running','completed','failed')", name="ck_opt_jobs_status_valid"),
        CheckConstraint("mode IN ('OPTIMIZE','CURRENT','AUTO')", name="ck_opt_jobs_mode_valid"),
        Index("ix_opt_jobs_created_at_desc", desc(created_at)),
        Index("ix_opt_jobs_status_running", status, postgresql_where=text("status = 'running'")),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "pair": self.pair,
            "mode": self.mode,
            "split_method": self.split_method,
            "status": self.status,
            "request": self.request,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class SessionRecord(Base):
    """Per-scheduler-tick session telemetry."""

    __tablename__ = "sessions"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    started_at = Column(DateTime(timezone=True), nullable=False, index=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(16), nullable=False)
    log_messages = Column(Text, nullable=True)


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _to_decimal_required(value: Any) -> Decimal:
    return Decimal(str(value))
