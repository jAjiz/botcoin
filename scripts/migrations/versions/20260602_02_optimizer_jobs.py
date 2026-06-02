"""Phase 10: add optimizer_jobs table for async optimizer job tracking.

Revision ID: 20260602_02
Revises: 20260524_01
Create Date: 2026-06-02 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260602_02"
down_revision = "20260524_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.create_table(
        "optimizer_jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("pair", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("split_method", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("request", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('running','completed','failed')", name="ck_opt_jobs_status_valid"
        ),
        sa.CheckConstraint(
            "mode IN ('CONSERVATIVE','AGGRESSIVE','CURRENT')", name="ck_opt_jobs_mode_valid"
        ),
    )
    op.create_index(
        "ix_opt_jobs_created_at_desc",
        "optimizer_jobs",
        [sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_opt_jobs_status_running",
        "optimizer_jobs",
        ["status"],
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index("ix_opt_jobs_status_running", table_name="optimizer_jobs")
    op.drop_index("ix_opt_jobs_created_at_desc", table_name="optimizer_jobs")
    op.drop_table("optimizer_jobs")
