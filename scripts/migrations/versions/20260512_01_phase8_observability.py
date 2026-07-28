"""Phase 8: sessions table + grafana_reader role.

Revision ID: 20260512_01
Revises: 20260414_01
Create Date: 2026-05-12 00:00:00
"""

from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260512_01"
down_revision = "20260414_01"
branch_labels = None
depends_on = None

GRAFANA_TABLES = ("ohlc_data", "closed_positions", "trailing_state", "bot_control", "sessions")


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
        sa.Column("log_messages", sa.Text, nullable=True),
    )
    op.create_index("ix_sessions_started_at", "sessions", ["started_at"], unique=False)

    # 2. grafana_reader role — read-only login used by the Grafana datasource.
    password = os.environ.get("GRAFANA_DB_PASSWORD")
    if not password:
        raise RuntimeError(
            "GRAFANA_DB_PASSWORD must be set in the environment for migration 20260512_01. "
            "Set it in .env (it is also consumed by the grafana service)."
        )
    conn = op.get_bind()
    database = conn.engine.url.database

    # The verb (CREATE/ALTER ROLE ... PASSWORD %L) is a bare identifier chosen
    # client-side, not user input; the password itself is quoted server-side by
    # Postgres' own format(%L, ...) via a bound parameter, so a literal `'` or
    # `$$` in it cannot break out of the statement.
    role_exists = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = 'grafana_reader'")).scalar() is not None
    verb = (
        "ALTER ROLE grafana_reader WITH LOGIN PASSWORD %L"
        if role_exists
        else "CREATE ROLE grafana_reader LOGIN PASSWORD %L"
    )
    role_stmt = conn.execute(text("SELECT format(:verb, :pw)").bindparams(verb=verb, pw=password)).scalar()
    # Sent through the raw DBAPI cursor, deliberately: the finished statement
    # carries the password as literal text, and both layers above the driver
    # would corrupt a `%` in it. SQLAlchemy's compiler doubles `%` to `%%` for
    # psycopg's pyformat paramstyle, and psycopg re-scans the text for
    # placeholders whenever it is handed a parameter container (SQLAlchemy
    # passes an empty but non-None one). The cursor takes the statement verbatim.
    cur = conn.connection.cursor()
    try:
        cur.execute(role_stmt)
    finally:
        cur.close()

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
