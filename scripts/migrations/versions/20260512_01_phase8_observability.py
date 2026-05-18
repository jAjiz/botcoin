"""Phase 8: sessions table + grafana_reader role.

Revision ID: 20260512_01
Revises: 20260414_01
Create Date: 2026-05-12 00:00:00
"""

from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260512_01"
down_revision = "20260414_01"
branch_labels = None
depends_on = None

GRAFANA_TABLES = ("ohlc_data", "closed_positions", "trailing_state", "bot_control", "sessions")


def _escape_literal(value: str) -> str:
    return value.replace("'", "''")


def upgrade() -> None:
    # 1. sessions table — written to by the scheduler each tick.
    op.create_table(
        "sessions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("balance", JSONB, nullable=True),
        sa.Column("pair_data", JSONB, nullable=True),
        sa.Column("log_messages", JSONB, nullable=True),
    )
    op.create_index("ix_sessions_started_at", "sessions", ["started_at"], unique=False)

    # 2. grafana_reader role — read-only login used by the Grafana datasource.
    password = os.environ.get("GRAFANA_DB_PASSWORD")
    if not password:
        raise RuntimeError(
            "GRAFANA_DB_PASSWORD must be set in the environment for migration 20260512_01. "
            "Set it in .env (it is also consumed by the grafana service)."
        )
    password_sql = _escape_literal(password)
    database = op.get_bind().engine.url.database

    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_reader') THEN
                CREATE ROLE grafana_reader LOGIN PASSWORD '{password_sql}';
            ELSE
                ALTER ROLE grafana_reader WITH LOGIN PASSWORD '{password_sql}';
            END IF;
        END
        $$;
        """
    )

    op.execute(f'GRANT CONNECT ON DATABASE "{database}" TO grafana_reader;')
    op.execute("GRANT USAGE ON SCHEMA public TO grafana_reader;")
    for table in GRAFANA_TABLES:
        op.execute(f"GRANT SELECT ON TABLE public.{table} TO grafana_reader;")

    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLES FROM grafana_reader;"
    )


def downgrade() -> None:
    database = op.get_bind().engine.url.database
    for table in GRAFANA_TABLES:
        op.execute(f"REVOKE SELECT ON TABLE public.{table} FROM grafana_reader;")
    op.execute("REVOKE USAGE ON SCHEMA public FROM grafana_reader;")
    op.execute(f'REVOKE CONNECT ON DATABASE "{database}" FROM grafana_reader;')
    op.execute("DROP ROLE IF EXISTS grafana_reader;")
    op.drop_index("ix_sessions_started_at", table_name="sessions")
    op.drop_table("sessions")
