from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import URL
from dotenv import load_dotenv

config = context.config

load_dotenv()

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url

    configured_url = config.get_main_option("sqlalchemy.url")
    if configured_url:
        return configured_url

    postgres_db = os.getenv("POSTGRES_DB", "botc")
    postgres_user = os.getenv("POSTGRES_USER", "botc")
    postgres_host = os.getenv("POSTGRES_HOST", "postgres")
    return str(
        URL.create(
            drivername="postgresql+psycopg",
            username=postgres_user,
            password=os.getenv("POSTGRES_PASSWORD", ""),
            host=postgres_host,
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            database=postgres_db,
        )
    )


def run_migrations_offline() -> None:
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_database_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()