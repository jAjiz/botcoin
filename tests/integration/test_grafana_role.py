import os

import pytest
from sqlalchemy import URL, create_engine, text

pytestmark = pytest.mark.integration

if os.environ.get("RUN_DB_INTEGRATION") != "true":
    pytest.skip("RUN_DB_INTEGRATION not set", allow_module_level=True)


def _reader_engine():
    password = os.environ["GRAFANA_DB_PASSWORD"]
    url = URL.create(
        drivername="postgresql+psycopg",
        username="grafana_reader",
        password=password,
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        database=os.environ.get("POSTGRES_DB", "DBbotc"),
    )
    return create_engine(url)


def test_grafana_reader_can_select_each_table():
    engine = _reader_engine()
    with engine.connect() as conn:
        for table in ("ohlc_data", "closed_positions", "trailing_state", "bot_control", "sessions"):
            conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))


def test_grafana_reader_cannot_insert():
    engine = _reader_engine()
    with engine.connect() as conn, pytest.raises(Exception):
        conn.execute(text("INSERT INTO bot_control (control_key, control_value) VALUES ('test_insert', 'x')"))
        conn.commit()
