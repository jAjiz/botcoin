"""Phase 4 initial PostgreSQL schema.

Revision ID: 20260414_01
Revises:
Create Date: 2026-04-14 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260414_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ohlc_data",
        sa.Column("pair", sa.Text(), nullable=False),
        sa.Column("timeframe_minutes", sa.Integer(), nullable=False),
        sa.Column("time", sa.BigInteger(), nullable=False),
        sa.Column("source_exchange", sa.Text(), nullable=False, server_default="kraken"),
        sa.Column("open", sa.Numeric(20, 10), nullable=False),
        sa.Column("high", sa.Numeric(20, 10), nullable=False),
        sa.Column("low", sa.Numeric(20, 10), nullable=False),
        sa.Column("close", sa.Numeric(20, 10), nullable=False),
        sa.Column("vwap", sa.Numeric(20, 10), nullable=True),
        sa.Column("volume", sa.Numeric(28, 10), nullable=True),
        sa.Column("count", sa.Integer(), nullable=True),
        sa.Column("atr", sa.Numeric(20, 10), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("timeframe_minutes > 0", name="ck_ohlc_data_timeframe_positive"),
        sa.CheckConstraint("count IS NULL OR count >= 0", name="ck_ohlc_data_count_nonnegative"),
        sa.CheckConstraint("high >= low", name="ck_ohlc_data_price_range_valid"),
        sa.CheckConstraint("open >= low AND open <= high", name="ck_ohlc_data_open_in_range"),
        sa.CheckConstraint("close >= low AND close <= high", name="ck_ohlc_data_close_in_range"),
        sa.PrimaryKeyConstraint("pair", "timeframe_minutes", "time", name="pk_ohlc_data"),
    )
    op.create_index(
        "ix_ohlc_data_pair_timeframe_time_desc",
        "ohlc_data",
        ["pair", "timeframe_minutes", sa.text('"time" DESC')],
        unique=False,
    )

    op.create_table(
        "closed_positions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("pair", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("volume", sa.Numeric(28, 10), nullable=False),
        sa.Column("entry_price", sa.Numeric(20, 10), nullable=False),
        sa.Column("activation_atr", sa.Numeric(20, 10), nullable=True),
        sa.Column("activation_price", sa.Numeric(20, 10), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trailing_price", sa.Numeric(20, 10), nullable=True),
        sa.Column("stop_price", sa.Numeric(20, 10), nullable=True),
        sa.Column("stop_atr", sa.Numeric(20, 10), nullable=True),
        sa.Column("closing_price", sa.Numeric(20, 10), nullable=False),
        sa.Column("closing_order_id", sa.Text(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pnl_percent", sa.Numeric(10, 4), nullable=False),
        sa.Column("inserted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("side IN ('buy', 'sell')", name="ck_closed_positions_side_valid"),
        sa.CheckConstraint("volume > 0", name="ck_closed_positions_volume_positive"),
        sa.CheckConstraint("entry_price > 0", name="ck_closed_positions_entry_price_positive"),
        sa.CheckConstraint("closing_price > 0", name="ck_closed_positions_closing_price_positive"),
        sa.PrimaryKeyConstraint("id", name="pk_closed_positions"),
        sa.UniqueConstraint("closing_order_id", name="uq_closed_positions_closing_order_id"),
    )
    op.create_index(
        "ix_closed_positions_pair_closed_at_desc",
        "closed_positions",
        ["pair", sa.text("closed_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_closed_positions_closed_at_desc",
        "closed_positions",
        [sa.text("closed_at DESC")],
        unique=False,
    )

    op.create_table(
        "trailing_state",
        sa.Column("pair", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("volume", sa.Numeric(28, 10), nullable=False),
        sa.Column("entry_price", sa.Numeric(20, 10), nullable=False),
        sa.Column("activation_atr", sa.Numeric(20, 10), nullable=False),
        sa.Column("activation_price", sa.Numeric(20, 10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trailing_price", sa.Numeric(20, 10), nullable=True),
        sa.Column("stop_price", sa.Numeric(20, 10), nullable=True),
        sa.Column("stop_atr", sa.Numeric(20, 10), nullable=True),
        sa.Column("closing_order_id", sa.Text(), nullable=True),
        sa.Column("closing_price", sa.Numeric(20, 10), nullable=True),
        sa.Column("closing_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("side IN ('buy', 'sell')", name="ck_trailing_state_side_valid"),
        sa.CheckConstraint("volume > 0", name="ck_trailing_state_volume_positive"),
        sa.CheckConstraint("entry_price > 0", name="ck_trailing_state_entry_price_positive"),
        sa.CheckConstraint(
            "(trailing_price IS NULL AND stop_price IS NULL AND stop_atr IS NULL) OR "
            "(trailing_price IS NOT NULL AND stop_price IS NOT NULL AND stop_atr IS NOT NULL)",
            name="ck_trailing_state_stop_fields_consistent",
        ),
        sa.PrimaryKeyConstraint("pair", name="pk_trailing_state"),
    )
    op.create_index(
        "ix_trailing_state_closing_order_id",
        "trailing_state",
        ["closing_order_id"],
        unique=False,
        postgresql_where=sa.text("closing_order_id IS NOT NULL"),
    )

    op.create_table(
        "bot_control",
        sa.Column("control_key", sa.Text(), nullable=False),
        sa.Column("control_value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_by", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("control_key", name="pk_bot_control"),
    )

    op.bulk_insert(
        sa.table(
            "bot_control",
            sa.column("control_key", sa.Text()),
            sa.column("control_value", sa.Text()),
            sa.column("updated_by", sa.Text()),
        ),
        [{"control_key": "bot_paused", "control_value": "false", "updated_by": "migration"}],
    )


def downgrade() -> None:
    op.drop_table("bot_control")
    op.drop_index("ix_trailing_state_closing_order_id", table_name="trailing_state")
    op.drop_table("trailing_state")
    op.drop_index("ix_closed_positions_closed_at_desc", table_name="closed_positions")
    op.drop_index("ix_closed_positions_pair_closed_at_desc", table_name="closed_positions")
    op.drop_table("closed_positions")
    op.drop_index("ix_ohlc_data_pair_timeframe_time_desc", table_name="ohlc_data")
    op.drop_table("ohlc_data")