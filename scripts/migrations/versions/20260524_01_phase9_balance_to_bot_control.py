"""Phase 9-1: drop sessions.balance/pair_data; move snapshots to bot_control.

Revision ID: 20260524_01
Revises: 20260512_01
Create Date: 2026-05-24 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260524_01"
down_revision = "20260512_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("sessions", "balance")
    op.drop_column("sessions", "pair_data")


def downgrade() -> None:
    op.add_column("sessions", sa.Column("pair_data", JSONB, nullable=True))
    op.add_column("sessions", sa.Column("balance", JSONB, nullable=True))
