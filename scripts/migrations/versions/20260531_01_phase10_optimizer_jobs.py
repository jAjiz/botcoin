"""Phase 10: optimizer_jobs table.

Revision ID: 20260531_01
Revises: 20260524_01
Create Date: 2026-05-31 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260531_01"
down_revision = "20260524_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "optimizer_jobs",
        sa.Column("id", sa.Text, primary_key=True, nullable=False),
        sa.Column("pair", sa.Text, nullable=False),
        sa.Column("mode", sa.Text, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("request_json", sa.Text, nullable=False),
        sa.Column("result_json", sa.Text, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="ck_optimizer_jobs_status_valid",
        ),
    )
    op.create_index(
        "ix_optimizer_jobs_created_at_desc",
        "optimizer_jobs",
        [sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_optimizer_jobs_created_at_desc", table_name="optimizer_jobs")
    op.drop_table("optimizer_jobs")
