"""Record the real Kraken fee of each closed position.

Nullable with no backfill: rows written before this migration are gross, rows after are net.

Revision ID: 20260820_01
Revises: 20260817_01
Create Date: 2026-08-20 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260820_01"
down_revision = "20260817_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("closed_positions", sa.Column("fee_eur", sa.Numeric(20, 10), nullable=True))


def downgrade() -> None:
    op.drop_column("closed_positions", "fee_eur")
