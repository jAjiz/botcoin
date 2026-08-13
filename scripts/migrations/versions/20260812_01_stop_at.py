"""Rename trailing_state.closing_requested_at to stop_at.

The field now latches the stop breach (written before the placement attempt),
not the successful close request. Values carry over: the old timestamp is the
same event for every row written before this change.

Revision ID: 20260812_01
Revises: 20260616_01
Create Date: 2026-08-12 00:00:00
"""

from __future__ import annotations

from alembic import op

revision = "20260812_01"
down_revision = "20260616_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("trailing_state", "closing_requested_at", new_column_name="stop_at")


def downgrade() -> None:
    op.alter_column("trailing_state", "stop_at", new_column_name="closing_requested_at")
