"""Phase 1 (V3): add pair_config table for dynamic per-pair configuration.

Revision ID: 20260616_01
Revises: 20260608_01
Create Date: 2026-06-16 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260616_01"
down_revision = "20260608_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pair_config",
        sa.Column("pair", sa.Text(), primary_key=True, nullable=False),
        sa.Column("target_pct", sa.Numeric(6, 3), nullable=False, server_default="0"),
        sa.Column("hodl_pct", sa.Numeric(6, 3), nullable=False, server_default="0"),
        sa.Column("k_act", sa.Numeric(10, 4), nullable=True),
        sa.Column("min_margin", sa.Numeric(12, 8), nullable=False, server_default="0"),
        sa.Column("stop_pct_ll", sa.Numeric(4, 3), nullable=False, server_default="0.90"),
        sa.Column("stop_pct_lv", sa.Numeric(4, 3), nullable=False, server_default="0.90"),
        sa.Column("stop_pct_mv", sa.Numeric(4, 3), nullable=False, server_default="0.90"),
        sa.Column("stop_pct_hv", sa.Numeric(4, 3), nullable=False, server_default="0.90"),
        sa.Column("stop_pct_hh", sa.Numeric(4, 3), nullable=False, server_default="0.90"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_by", sa.Text(), nullable=True),
        sa.CheckConstraint("target_pct >= 0 AND target_pct <= 100", name="ck_pair_config_target_pct_range"),
        sa.CheckConstraint("hodl_pct >= 0 AND hodl_pct <= 100", name="ck_pair_config_hodl_pct_range"),
        sa.CheckConstraint("k_act IS NULL OR k_act >= 0", name="ck_pair_config_k_act_nonneg"),
        sa.CheckConstraint("min_margin >= 0", name="ck_pair_config_min_margin_nonneg"),
        sa.CheckConstraint("stop_pct_ll >= 0 AND stop_pct_ll <= 1", name="ck_pair_config_stop_ll_range"),
        sa.CheckConstraint("stop_pct_lv >= 0 AND stop_pct_lv <= 1", name="ck_pair_config_stop_lv_range"),
        sa.CheckConstraint("stop_pct_mv >= 0 AND stop_pct_mv <= 1", name="ck_pair_config_stop_mv_range"),
        sa.CheckConstraint("stop_pct_hv >= 0 AND stop_pct_hv <= 1", name="ck_pair_config_stop_hv_range"),
        sa.CheckConstraint("stop_pct_hh >= 0 AND stop_pct_hh <= 1", name="ck_pair_config_stop_hh_range"),
    )


def downgrade() -> None:
    op.drop_table("pair_config")
