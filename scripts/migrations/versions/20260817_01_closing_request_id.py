"""Persist the closing request id on trailing state.

Set before every placement attempt, so a lost AddOrder response can be resolved by client id.

Revision ID: 20260817_01
Revises: 20260812_01
Create Date: 2026-08-17 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260817_01"
down_revision = "20260812_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trailing_state", sa.Column("closing_request_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("trailing_state", "closing_request_id")
