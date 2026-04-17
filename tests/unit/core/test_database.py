from datetime import datetime
from decimal import Decimal
from typing import Any

import pandas as pd
import pytest
import core.database as database

from core.database import (
    OHLCData,
    ClosedPosition,
    TrailingState,
    BotControl,
    load_ohlc_data,
    save_ohlc_data,
    save_closed_position,
    load_closed_positions,
    get_session,
    check_database_connection,
)


class FakeQuery:
    def __init__(self, records: list[Any] | None = None) -> None:
        self.records = records or []
        self.filter_calls = 0
        self.order_by_calls = 0
        self.limit_value: int | None = None

    def filter(self, *_args: Any, **_kwargs: Any) -> "FakeQuery":
        self.filter_calls += 1
        return self

    def order_by(self, *_args: Any, **_kwargs: Any) -> "FakeQuery":
        self.order_by_calls += 1
        return self

    def limit(self, value: int) -> "FakeQuery":
        self.limit_value = value
        return self

    def all(self) -> list[Any]:
        if self.limit_value is None:
            return self.records
        return self.records[: self.limit_value]


class FakeSession:
    def __init__(self, records: list[Any] | None = None) -> None:
        self.query_obj = FakeQuery(records)
        self.added_records: list[Any] = []
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0
        self.executed_sql: list[str] = []
        self.commit_error: Exception | None = None

    def query(self, _model: Any) -> FakeQuery:
        return self.query_obj

    def add(self, record: Any) -> None:
        self.added_records.append(record)

    def add_all(self, records: list[Any]) -> None:
        self.added_records.extend(records)

    def commit(self) -> None:
        self.commit_calls += 1
        if self.commit_error:
            raise self.commit_error

    def rollback(self) -> None:
        self.rollback_calls += 1

    def close(self) -> None:
        self.close_calls += 1

    def execute(self, sql: str) -> None:
        self.executed_sql.append(sql)


class FakeSessionContextManager:
    def __init__(self, session: FakeSession, enter_error: Exception | None = None) -> None:
        self._session = session
        self._enter_error = enter_error

    def __enter__(self) -> FakeSession:
        if self._enter_error:
            raise self._enter_error
        return self._session

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        _ = (exc_type, exc, tb)
        return False


def patch_get_session(monkeypatch: pytest.MonkeyPatch, session: FakeSession) -> None:
    def _get_session() -> FakeSessionContextManager:
        return FakeSessionContextManager(session)

    monkeypatch.setattr(database, "get_session", _get_session)


@pytest.fixture
def ohlc_record():
    """Create a sample OHLCData ORM object."""
    return OHLCData(
        pair="XBTEUR",
        timeframe_minutes=15,
        dtime=datetime(2026, 4, 1, 12, 0, 0),
        open=Decimal("100.00"),
        high=Decimal("101.50"),
        low=Decimal("99.50"),
        close=Decimal("101.00"),
        vwap=Decimal("100.75"),
        volume=Decimal("1000"),
        count=123,
        atr=Decimal("1.00"),
    )


@pytest.fixture
def closed_position_record():
    """Create a sample ClosedPosition ORM object."""
    return ClosedPosition(
        pair="XBTEUR",
        side="buy",
        volume=Decimal("0.5"),
        entry_price=Decimal("50000"),
        activation_price=Decimal("50100"),
        created_at=datetime(2026, 4, 1, 10, 0, 0),
        closing_price=Decimal("50500"),
        closing_order_id="order_12345",
        closed_at=datetime(2026, 4, 1, 14, 0, 0),
        pnl_percent=Decimal("1.0"),
    )


@pytest.fixture
def trailing_state_record():
    """Create a sample TrailingState ORM object."""
    return TrailingState(
        pair="XBTEUR",
        side="buy",
        volume=Decimal("0.5"),
        entry_price=Decimal("50000"),
        activation_atr=Decimal("200"),
        activation_price=Decimal("50100"),
        created_at=datetime(2026, 4, 1, 10, 0, 0),
        trailing_price=Decimal("50400"),
        stop_price=Decimal("50200"),
        stop_atr=Decimal("150"),
    )


@pytest.fixture
def bot_control_record():
    """Create a sample BotControl ORM object."""
    return BotControl(
        control_key="bot_paused",
        control_value="false",
        updated_by="test",
    )


# ============================================================================
# ORM Model Tests
# ============================================================================


def test_ohlc_creation(ohlc_record):
    """Test creating an OHLCData record."""
    assert ohlc_record.pair == "XBTEUR"
    assert ohlc_record.timeframe_minutes == 15
    assert float(ohlc_record.open) == 100.0
    assert float(ohlc_record.close) == 101.0


def test_ohlc_to_dict(ohlc_record):
    """Test converting OHLCData to dictionary."""
    data = ohlc_record.to_dict()
    assert data["pair"] == "XBTEUR"
    assert data["open"] == 100.0
    assert data["close"] == 101.0
    assert data["count"] == 123
    assert isinstance(data["atr"], float)


def test_ohlc_decimal_precision():
    """Test that Decimal precision is maintained."""
    record = OHLCData(
        pair="XBTEUR",
        timeframe_minutes=15,
        dtime=datetime.now(),
        open=Decimal("100.1234567890"),
        high=Decimal("101.9999999999"),
        low=Decimal("99.0000000001"),
        close=Decimal("100.5555555555"),
    )
    assert record.open == Decimal("100.1234567890")


def test_closed_position_creation(closed_position_record):
    """Test creating a ClosedPosition record."""
    assert closed_position_record.pair == "XBTEUR"
    assert closed_position_record.side == "buy"
    assert float(closed_position_record.volume) == 0.5


def test_closed_position_to_dict(closed_position_record):
    """Test converting ClosedPosition to dictionary."""
    data = closed_position_record.to_dict()
    assert data["pair"] == "XBTEUR"
    assert data["side"] == "buy"
    assert data["volume"] == 0.5
    assert "closing_order_id" in data


def test_trailing_state_creation(trailing_state_record):
    """Test creating a TrailingState record."""
    assert trailing_state_record.pair == "XBTEUR"
    assert trailing_state_record.side == "buy"
    assert float(trailing_state_record.volume) == 0.5


def test_trailing_state_to_dict(trailing_state_record):
    """Test converting TrailingState to dictionary."""
    data = trailing_state_record.to_dict()
    assert data["pair"] == "XBTEUR"
    assert data["volume"] == 0.5
    assert "trailing_price" in data


def test_bot_control_creation(bot_control_record):
    """Test creating a BotControl record."""
    assert bot_control_record.control_key == "bot_paused"
    assert bot_control_record.control_value == "false"


def test_bot_control_to_dict(bot_control_record):
    """Test converting BotControl to dictionary."""
    data = bot_control_record.to_dict()
    assert data["control_key"] == "bot_paused"
    assert data["control_value"] == "false"


# ============================================================================
# OHLC Operations Tests (Mocked)
# ============================================================================


def test_load_ohlc_data_empty(monkeypatch):
    """Test loading OHLC data when no records exist.

    Uses mocked session to simulate empty database.
    """
    session = FakeSession(records=[])
    patch_get_session(monkeypatch, session)

    df = load_ohlc_data("XBTEUR", 15)

    assert df.empty
    assert isinstance(df, pd.DataFrame)


def test_load_ohlc_data_with_records(monkeypatch, get_ohlc_record):
    """Test loading OHLC data with existing records.

    Uses mocked session to simulate database records.
    """
    session = FakeSession(records=[get_ohlc_record])
    patch_get_session(monkeypatch, session)

    df = load_ohlc_data("XBTEUR", 15)

    assert not df.empty
    assert len(df) == 1
    assert "dtime" in df.columns
    assert "open" in df.columns
    assert "close" in df.columns
    assert "vwap" in df.columns
    assert "count" in df.columns


def test_load_ohlc_data_with_since_timestamp(monkeypatch):
    """Test loading OHLC data with since_timestamp filter."""
    since_ts = 1717200000  # Some Unix timestamp

    session = FakeSession(records=[])
    patch_get_session(monkeypatch, session)

    df = load_ohlc_data("XBTEUR", 15, since_timestamp=since_ts)

    assert session.query_obj.filter_calls >= 2
    assert df.empty


def test_save_ohlc_data(monkeypatch, sample_dataframe):
    """Test saving OHLC data to database."""
    session = FakeSession()
    patch_get_session(monkeypatch, session)

    save_ohlc_data("XBTEUR", 15, sample_dataframe)

    assert len(session.added_records) == len(sample_dataframe)


def test_save_ohlc_data_empty_dataframe(caplog):
    """Test saving empty DataFrame (should log warning and return)."""
    save_ohlc_data("XBTEUR", 15, pd.DataFrame())

    assert "Empty DataFrame provided for XBTEUR" in caplog.text


def test_load_ohlc_data_with_limit(monkeypatch):
    """Test load_ohlc_data with limit preserves the requested row count."""
    records = [
        OHLCData(
            pair="XBTEUR", timeframe_minutes=15,
            dtime=datetime(2026, 1, 1, 0, 15, 0),
            open=Decimal("101.0"), high=Decimal("102.0"),
            low=Decimal("100.0"), close=Decimal("101.5"),
            atr=Decimal("1.1"),
        ),
        OHLCData(
            pair="XBTEUR", timeframe_minutes=15,
            dtime=datetime(2026, 1, 1, 0, 0, 0),
            open=Decimal("100.0"), high=Decimal("101.0"),
            low=Decimal("99.0"), close=Decimal("100.5"),
            atr=Decimal("1.0"),
        ),
    ]
    session = FakeSession(records=records)
    patch_get_session(monkeypatch, session)

    df = load_ohlc_data("XBTEUR", 15, limit=1)

    assert len(df) == 1
    assert "dtime" in df.columns
    assert "atr" in df.columns
    assert session.query_obj.limit_value == 1


def test_get_latest_ohlc_not_exists(monkeypatch):
    """Test fetching OHLC when no record exists."""
    session = FakeSession(records=[])
    patch_get_session(monkeypatch, session)

    df = load_ohlc_data("XBTEUR", 15)

    assert df.empty


# ============================================================================
# Session Management Tests
# ============================================================================


def test_get_session_context_manager(monkeypatch):
    """Test get_session context manager behavior.

    Verifies that session is properly created and closed.
    """
    session = FakeSession()
    monkeypatch.setattr(database, "SessionLocal", lambda: session)

    with get_session() as current_session:
        assert current_session == session

    assert session.commit_calls == 1
    assert session.close_calls == 1


def test_get_session_rollback_on_error(monkeypatch):
    """Test that session is rolled back on error."""
    session = FakeSession()
    session.commit_error = Exception("DB Error")
    monkeypatch.setattr(database, "SessionLocal", lambda: session)

    with pytest.raises(Exception):
        with get_session():
            pass

    assert session.rollback_calls == 1
    assert session.close_calls == 1


# ============================================================================
# Health Check Tests
# ============================================================================


def test_check_database_connection_success(monkeypatch):
    """Test successful database connection check."""
    session = FakeSession()
    patch_get_session(monkeypatch, session)

    result = check_database_connection()

    assert result is True
    assert session.executed_sql == ["SELECT 1"]


def test_check_database_connection_failure(monkeypatch):
    """Test database connection check when connection fails."""
    def _failing_get_session() -> FakeSessionContextManager:
        return FakeSessionContextManager(FakeSession(), enter_error=Exception("Connection failed"))

    monkeypatch.setattr(database, "get_session", _failing_get_session)

    result = check_database_connection()

    assert result is False


# ============================================================================
# Closed Position Operations Tests (Mocked)
# ============================================================================


def _make_closed_position_data(**overrides) -> dict:
    """Return a minimal valid closed position data dict."""
    data = {
        "pair": "XBTEUR",
        "side": "buy",
        "volume": Decimal("0.5"),
        "entry_price": Decimal("50000"),
        "activation_atr": None,
        "activation_price": Decimal("50100"),
        "created_at": datetime(2026, 4, 1, 10, 0, 0),
        "activated_at": None,
        "trailing_price": None,
        "stop_price": None,
        "stop_atr": None,
        "closing_price": Decimal("50500"),
        "closing_order_id": "order_12345",
        "closed_at": datetime(2026, 4, 1, 14, 0, 0),
        "pnl_percent": Decimal("1.0"),
    }
    data.update(overrides)
    return data


def test_save_closed_position(monkeypatch):
    """Test saving a closed position to the database."""
    session = FakeSession()
    patch_get_session(monkeypatch, session)

    save_closed_position(_make_closed_position_data())

    assert len(session.added_records) == 1
    saved = session.added_records[0]
    assert isinstance(saved, ClosedPosition)
    assert saved.pair == "XBTEUR"
    assert saved.side == "buy"
    assert saved.closing_order_id == "order_12345"


def test_save_closed_position_optional_fields_none(monkeypatch):
    """Test saving a closed position with all optional fields as None."""
    session = FakeSession()
    patch_get_session(monkeypatch, session)

    save_closed_position(_make_closed_position_data())

    saved = session.added_records[0]
    assert saved.activation_atr is None
    assert saved.activated_at is None
    assert saved.trailing_price is None
    assert saved.stop_price is None
    assert saved.stop_atr is None


def test_save_closed_position_optional_fields_populated(monkeypatch):
    """Test saving a closed position with all optional fields populated."""
    session = FakeSession()
    patch_get_session(monkeypatch, session)

    data = _make_closed_position_data(
        pair="ETHEUR",
        side="sell",
        activation_atr=Decimal("50"),
        activated_at=datetime(2026, 4, 2, 10, 0, 0),
        trailing_price=Decimal("2980"),
        stop_price=Decimal("3020"),
        stop_atr=Decimal("40"),
    )
    save_closed_position(data)

    saved = session.added_records[0]
    assert saved.pair == "ETHEUR"
    assert saved.activated_at == datetime(2026, 4, 2, 10, 0, 0)
    assert float(saved.trailing_price) == 2980.0
    assert float(saved.stop_atr) == 40.0


def test_save_closed_position_raises_on_db_error(monkeypatch):
    """Test that save_closed_position re-raises on database error."""
    def _failing_get_session() -> FakeSessionContextManager:
        return FakeSessionContextManager(FakeSession(), enter_error=Exception("DB error"))

    monkeypatch.setattr(database, "get_session", _failing_get_session)

    with pytest.raises(Exception, match="DB error"):
        save_closed_position(_make_closed_position_data())


def test_load_closed_positions_empty(monkeypatch):
    """Test loading closed positions when none exist."""
    session = FakeSession(records=[])
    patch_get_session(monkeypatch, session)

    result = load_closed_positions()

    assert result == []
    assert isinstance(result, list)


def test_load_closed_positions_with_records(monkeypatch, closed_position_record):
    """Test loading all closed positions returns correct dicts."""
    session = FakeSession(records=[closed_position_record])
    patch_get_session(monkeypatch, session)

    result = load_closed_positions()

    assert len(result) == 1
    assert result[0]["pair"] == "XBTEUR"
    assert result[0]["side"] == "buy"
    assert result[0]["closing_order_id"] == "order_12345"


def test_load_closed_positions_with_limit(monkeypatch, closed_position_record):
    """Test load_closed_positions respects limit parameter."""
    record2 = ClosedPosition(
        pair="ETHEUR",
        side="sell",
        volume=Decimal("1.0"),
        entry_price=Decimal("3000"),
        closing_price=Decimal("3100"),
        closing_order_id="order_67890",
        closed_at=datetime(2026, 4, 2, 14, 0, 0),
        pnl_percent=Decimal("3.33"),
        created_at=datetime(2026, 4, 2, 10, 0, 0),
    )
    session = FakeSession(records=[closed_position_record, record2])
    patch_get_session(monkeypatch, session)

    result = load_closed_positions(limit=1)

    assert len(result) == 1
    assert session.query_obj.limit_value == 1


def test_load_closed_positions_returns_empty_on_error(monkeypatch):
    """Test that load_closed_positions returns empty list on database error."""
    def _failing_get_session() -> FakeSessionContextManager:
        return FakeSessionContextManager(FakeSession(), enter_error=Exception("DB error"))

    monkeypatch.setattr(database, "get_session", _failing_get_session)

    result = load_closed_positions()

    assert result == []


def test_load_closed_positions_with_pair_filter_empty(monkeypatch):
    """Test fetching closed positions for a pair with no records."""
    session = FakeSession(records=[])
    patch_get_session(monkeypatch, session)

    result = load_closed_positions(pair="XBTEUR")

    assert result == []
    assert session.query_obj.filter_calls == 1


def test_load_closed_positions_with_pair_filter_records(monkeypatch, closed_position_record):
    """Test fetching closed positions filtered by pair returns correct dicts."""
    session = FakeSession(records=[closed_position_record])
    patch_get_session(monkeypatch, session)

    result = load_closed_positions(pair="XBTEUR")

    assert len(result) == 1
    assert result[0]["pair"] == "XBTEUR"
    assert result[0]["closing_order_id"] == "order_12345"


def test_load_closed_positions_pair_and_limit(monkeypatch, closed_position_record):
    """Test load_closed_positions with both pair and limit parameters."""
    session = FakeSession(records=[closed_position_record])
    patch_get_session(monkeypatch, session)

    load_closed_positions(pair="XBTEUR", limit=5)

    assert session.query_obj.limit_value == 5
    assert session.query_obj.filter_calls == 1


def test_load_closed_positions_pair_filter_error(monkeypatch):
    """Test that load_closed_positions with pair filter returns empty list on error."""
    def _failing_get_session() -> FakeSessionContextManager:
        return FakeSessionContextManager(FakeSession(), enter_error=Exception("DB error"))

    monkeypatch.setattr(database, "get_session", _failing_get_session)

    result = load_closed_positions(pair="XBTEUR")

    assert result == []
