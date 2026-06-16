import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pandas as pd
import pytest

import core.database as database
from core.database import (
    BotControl,
    ClosedPosition,
    OHLCData,
    TrailingState,
    check_database_connection,
    delete_trailing_state,
    finalize_session,
    get_bot_paused,
    get_control_value,
    get_session,
    load_closed_positions,
    load_ohlc_data,
    load_trailing_state,
    save_closed_position,
    save_ohlc_data,
    save_trailing_state,
    set_bot_paused,
    set_control_value,
)


class FakeQuery:
    def __init__(self, records: list[Any] | None = None) -> None:
        self.records = records or []
        self.filter_calls = 0
        self.order_by_calls = 0
        self.limit_value: int | None = None

    def filter(self, *args: Any, **_kwargs: Any) -> "FakeQuery":
        self.filter_calls += 1
        for expr in args:
            try:
                attr = expr.left.key
                value = expr.right.value
                self.records = [r for r in self.records if getattr(r, attr, None) == value]
            except AttributeError:
                pass
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

    def one_or_none(self) -> Any | None:
        return self.records[0] if self.records else None


class FakeSession:
    def __init__(self, records: list[Any] | None = None) -> None:
        self.query_obj = FakeQuery(records)
        self.added_records: list[Any] = []
        self.merged_records: list[Any] = []
        self.deleted_records: list[Any] = []
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0
        self.executed_sql: list[str] = []
        self.commit_error: Exception | None = None

    def query(self, _model: Any) -> FakeQuery:
        return self.query_obj

    def add(self, record: Any) -> None:
        self.added_records.append(record)

    def merge(self, record: Any) -> Any:
        self.merged_records.append(record)
        return record

    def delete(self, record: Any) -> None:
        self.deleted_records.append(record)

    def commit(self) -> None:
        self.commit_calls += 1
        if self.commit_error:
            raise self.commit_error

    def rollback(self) -> None:
        self.rollback_calls += 1

    def close(self) -> None:
        self.close_calls += 1

    def execute(self, sql) -> None:
        self.executed_sql.append(str(sql))


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


def patch_get_session_error(monkeypatch: pytest.MonkeyPatch, message: str = "DB error") -> None:
    def _failing_get_session() -> FakeSessionContextManager:
        return FakeSessionContextManager(FakeSession(), enter_error=Exception(message))

    monkeypatch.setattr(database, "get_session", _failing_get_session)


@pytest.fixture
def ohlc_record():
    """Create a sample OHLCData ORM object."""
    return OHLCData(
        pair="XBTEUR",
        timeframe_minutes=15,
        time=1743508800,
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
        created_at=datetime(2026, 4, 1, 10, 0, 0, tzinfo=UTC),
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
    assert data["time"] == 1743508800
    assert data["open"] == 100.0
    assert data["close"] == 101.0
    assert data["count"] == 123
    assert isinstance(data["atr"], float)


def test_ohlc_decimal_precision():
    """Test that Decimal precision is maintained."""
    record = OHLCData(
        pair="XBTEUR",
        timeframe_minutes=15,
        time=int(datetime.now().timestamp()),
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


def test_load_ohlc_data_with_records(monkeypatch, ohlc_record):
    """Test loading OHLC data with existing records.

    Uses mocked session to simulate database records.
    """
    session = FakeSession(records=[ohlc_record])
    patch_get_session(monkeypatch, session)

    df = load_ohlc_data("XBTEUR", 15)

    assert not df.empty
    assert len(df) == 1
    assert "time" in df.columns
    assert "dtime" in df.columns
    assert "open" in df.columns
    assert "close" in df.columns
    assert "vwap" in df.columns
    assert "count" in df.columns


def test_load_ohlc_data_with_since_timestamp(monkeypatch):
    """Test loading OHLC data with since_time filter."""
    since_ts = 1717200000  # Some Unix timestamp

    session = FakeSession(records=[])
    patch_get_session(monkeypatch, session)

    df = load_ohlc_data("XBTEUR", 15, since_time=since_ts)

    assert session.query_obj.filter_calls >= 2
    assert df.empty


def test_save_ohlc_data(monkeypatch, sample_dataframe):
    """Test saving OHLC data to database."""
    session = FakeSession()
    patch_get_session(monkeypatch, session)

    save_ohlc_data("XBTEUR", 15, sample_dataframe)

    assert len(session.added_records) == 0
    assert len(session.executed_sql) == 1
    assert "ohlc_data" in session.executed_sql[0].lower()


def test_save_ohlc_data_empty_dataframe(caplog):
    """Test saving empty DataFrame (should log warning and return)."""
    save_ohlc_data("XBTEUR", 15, pd.DataFrame())

    assert "Empty DataFrame provided for XBTEUR" in caplog.text


def test_load_ohlc_data_with_limit(monkeypatch):
    """Test load_ohlc_data with limit preserves the requested row count."""
    records = [
        OHLCData(
            pair="XBTEUR",
            timeframe_minutes=15,
            time=1735690500,
            open=Decimal("101.0"),
            high=Decimal("102.0"),
            low=Decimal("100.0"),
            close=Decimal("101.5"),
            atr=Decimal("1.1"),
        ),
        OHLCData(
            pair="XBTEUR",
            timeframe_minutes=15,
            time=1735689600,
            open=Decimal("100.0"),
            high=Decimal("101.0"),
            low=Decimal("99.0"),
            close=Decimal("100.5"),
            atr=Decimal("1.0"),
        ),
    ]
    session = FakeSession(records=records)
    patch_get_session(monkeypatch, session)

    df = load_ohlc_data("XBTEUR", 15, limit=1)

    assert len(df) == 1
    assert "time" in df.columns
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
        "side": "buy",
        "volume": Decimal("0.5"),
        "entry_price": Decimal("50000"),
        "activation_atr": None,
        "activation_price": Decimal("50100"),
        "created_at": datetime(2026, 4, 1, 10, 0, 0, tzinfo=UTC),
        "activated_at": None,
        "trailing_price": None,
        "stop_price": None,
        "stop_atr": None,
        "closing_price": Decimal("50500"),
        "closing_order_id": "order_12345",
        "pnl_percent": Decimal("1.0"),
    }
    data.update(overrides)
    return data


def _make_trailing_state_entry(**overrides) -> dict:
    """Return a minimal valid trailing state entry using the app-facing shape."""
    data = {
        "side": "buy",
        "volume": 0.5,
        "entry_price": 50000.0,
        "activation_atr": 200.0,
        "activation_price": 50100.0,
        "created_at": datetime(2026, 4, 1, 10, 0, 0, tzinfo=UTC),
    }
    data.update(overrides)
    return data


def test_save_closed_position(monkeypatch):
    """Test saving a closed position to the database."""
    session = FakeSession()
    patch_get_session(monkeypatch, session)

    save_closed_position("XBTEUR", _make_closed_position_data())

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

    save_closed_position("XBTEUR", _make_closed_position_data())

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
        side="sell",
        activation_atr=Decimal("50"),
        activated_at=datetime(2026, 4, 2, 10, 0, 0, tzinfo=UTC),
        trailing_price=Decimal("2980"),
        stop_price=Decimal("3020"),
        stop_atr=Decimal("40"),
    )
    save_closed_position("ETHEUR", data)

    saved = session.added_records[0]
    assert saved.pair == "ETHEUR"
    assert saved.activated_at == datetime(2026, 4, 2, 10, 0, 0, tzinfo=UTC)
    assert float(saved.trailing_price) == 2980.0
    assert float(saved.stop_atr) == 40.0


def test_save_closed_position_raises_on_db_error(monkeypatch):
    """Test that save_closed_position re-raises on database error."""
    patch_get_session_error(monkeypatch)

    with pytest.raises(Exception, match="DB error"):
        save_closed_position("XBTEUR", _make_closed_position_data())


def test_load_closed_positions_with_records(monkeypatch, closed_position_record):
    """Test loading all closed positions returns correct dicts."""
    session = FakeSession(records=[closed_position_record])
    patch_get_session(monkeypatch, session)

    result = load_closed_positions()

    assert len(result) == 1
    assert result[0]["pair"] == "XBTEUR"
    assert result[0]["side"] == "buy"
    assert result[0]["closing_order_id"] == "order_12345"


def test_load_closed_positions_with_pair_and_limit(monkeypatch, closed_position_record):
    """Test load_closed_positions respects pair filter and limit parameters."""
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

    result = load_closed_positions(pair="XBTEUR", limit=1)

    assert len(result) == 1
    assert result[0]["pair"] == "XBTEUR"
    assert session.query_obj.limit_value == 1
    assert session.query_obj.filter_calls == 1


@pytest.mark.parametrize("pair", [None, "XBTEUR"])
def test_load_closed_positions_empty_or_error(monkeypatch, pair):
    """Test load_closed_positions returns empty list for empty query or db error."""
    session = FakeSession(records=[])
    patch_get_session(monkeypatch, session)

    result = load_closed_positions(pair=pair)

    assert result == []
    if pair is not None:
        assert session.query_obj.filter_calls == 1

    patch_get_session_error(monkeypatch)
    result_on_error = load_closed_positions(pair=pair)
    assert result_on_error == []


# ============================================================================
# Trailing State Operations Tests (Mocked)
# ============================================================================


def test_save_trailing_state(monkeypatch):
    """Test saving active trailing state to the database."""
    session = FakeSession()
    patch_get_session(monkeypatch, session)

    save_trailing_state("XBTEUR", _make_trailing_state_entry())

    assert len(session.merged_records) == 1
    saved = session.merged_records[0]
    assert isinstance(saved, TrailingState)
    assert saved.pair == "XBTEUR"
    assert saved.side == "buy"
    assert float(saved.activation_price) == 50100.0


def test_save_trailing_state_only_updates_target_pair(monkeypatch, trailing_state_record):
    """Test saving trailing state does not delete or mutate other pairs implicitly."""
    session = FakeSession(records=[trailing_state_record])
    patch_get_session(monkeypatch, session)

    save_trailing_state("ETHEUR", _make_trailing_state_entry(side="sell"))

    assert len(session.merged_records) == 1
    assert session.merged_records[0].pair == "ETHEUR"
    assert session.deleted_records == []


def test_save_trailing_state_with_optional_fields(monkeypatch):
    """Test saving trailing state with active trailing and closing fields populated."""
    session = FakeSession()
    patch_get_session(monkeypatch, session)

    save_trailing_state(
        "XBTEUR",
        _make_trailing_state_entry(
            trailing_price=50400.0,
            stop_price=50200.0,
            stop_atr=150.0,
            closing_order_id="close_123",
            closing_price=50150.0,
            closing_requested_at=datetime(2026, 4, 1, 11, 15, 0, tzinfo=UTC),
            activated_at=datetime(2026, 4, 1, 10, 30, 0, tzinfo=UTC),
        ),
    )

    saved = session.merged_records[0]
    assert float(saved.trailing_price) == 50400.0
    assert float(saved.stop_price) == 50200.0
    assert saved.closing_order_id == "close_123"


def test_save_trailing_state_raises_on_db_error(monkeypatch):
    """Test that save_trailing_state re-raises on database error."""
    patch_get_session_error(monkeypatch)

    with pytest.raises(Exception, match="DB error"):
        save_trailing_state("XBTEUR", _make_trailing_state_entry())


@pytest.mark.parametrize(
    "pair,records,expect_found",
    [
        ("XBTEUR", [], False),
        (
            "XBTEUR",
            [
                TrailingState(
                    pair="XBTEUR",
                    side="buy",
                    volume=Decimal("0.5"),
                    entry_price=Decimal("50000"),
                    activation_atr=Decimal("200"),
                    activation_price=Decimal("50100"),
                    created_at=datetime(2026, 4, 1, 10, 0, 0, tzinfo=UTC),
                    trailing_price=Decimal("50400"),
                    stop_price=Decimal("50200"),
                    stop_atr=Decimal("150"),
                )
            ],
            True,
        ),
        (
            "ETHEUR",
            [
                TrailingState(
                    pair="XBTEUR",
                    side="buy",
                    volume=Decimal("0.5"),
                    entry_price=Decimal("50000"),
                    activation_atr=Decimal("200"),
                    activation_price=Decimal("50100"),
                    created_at=datetime(2026, 4, 1, 10, 0, 0, tzinfo=UTC),
                    trailing_price=Decimal("50400"),
                    stop_price=Decimal("50200"),
                    stop_atr=Decimal("150"),
                )
            ],
            False,
        ),
    ],
)
def test_load_trailing_state(monkeypatch, pair, records, expect_found):
    """Test loading trailing state for found/missing pair scenarios."""
    session = FakeSession(records=records)
    patch_get_session(monkeypatch, session)

    result = load_trailing_state(pair)

    if expect_found:
        assert result is not None
        assert result["side"] == "buy"
        assert result["activation_price"] == 50100.0
        assert result["created_at"] == datetime(2026, 4, 1, 10, 0, 0, tzinfo=UTC)
    else:
        assert result is None
    assert session.query_obj.filter_calls == 1


def test_load_trailing_state_returns_none_on_error(monkeypatch):
    """Test that load_trailing_state returns None on database error."""
    patch_get_session_error(monkeypatch)
    assert load_trailing_state("XBTEUR") is None


def test_delete_trailing_state_success(monkeypatch, trailing_state_record):
    """Test deleting trailing state for an existing pair."""
    session = FakeSession(records=[trailing_state_record])
    patch_get_session(monkeypatch, session)

    result = delete_trailing_state("XBTEUR")

    assert result is True
    assert session.deleted_records == [trailing_state_record]


def test_delete_trailing_state_missing(monkeypatch, trailing_state_record):
    """Test deleting trailing state returns False when pair is missing."""
    session = FakeSession(records=[trailing_state_record])
    patch_get_session(monkeypatch, session)

    result = delete_trailing_state("ETHEUR")

    assert result is False
    assert session.deleted_records == []


def test_delete_trailing_state_returns_false_on_error(monkeypatch):
    """Test that delete_trailing_state returns False on database error."""
    patch_get_session_error(monkeypatch)

    result = delete_trailing_state("XBTEUR")

    assert result is False


# ============================================================================
# Bot Control Operations Tests (Mocked)
# ============================================================================


def test_get_control_value_found(monkeypatch, bot_control_record):
    """Test getting control value when key exists."""
    session = FakeSession(records=[bot_control_record])
    patch_get_session(monkeypatch, session)

    value = get_control_value("bot_paused")

    assert value == "false"
    assert session.query_obj.filter_calls == 1


def test_get_control_value_missing(monkeypatch):
    """Test getting control value when key does not exist."""
    session = FakeSession(records=[])
    patch_get_session(monkeypatch, session)

    value = get_control_value("bot_paused")

    assert value is None


def test_get_control_value_returns_none_on_error(monkeypatch):
    """Test get_control_value returns None on database error."""
    patch_get_session_error(monkeypatch)

    value = get_control_value("bot_paused")

    assert value is None


def test_set_control_value(monkeypatch):
    """Test setting a control value stores BotControl record."""
    session = FakeSession()
    patch_get_session(monkeypatch, session)

    set_control_value("bot_paused", "true", updated_by="unit-test")

    assert len(session.merged_records) == 1
    saved = session.merged_records[0]
    assert isinstance(saved, BotControl)
    assert saved.control_key == "bot_paused"
    assert saved.control_value == "true"
    assert saved.updated_by == "unit-test"


def test_set_control_value_raises_on_error(monkeypatch):
    """Test set_control_value re-raises database errors."""
    patch_get_session_error(monkeypatch)

    with pytest.raises(Exception, match="DB error"):
        set_control_value("bot_paused", "true")


@pytest.mark.parametrize(
    "record,expected",
    [
        (BotControl(control_key="bot_paused", control_value="true"), True),
        (BotControl(control_key="bot_paused", control_value="false"), False),
        (None, True),  # Missing record defaults to True: fail-closed (paused)
    ],
)
def test_get_bot_paused(monkeypatch, record, expected):
    """Test get_bot_paused for true, false, and missing values."""
    session = FakeSession(records=[record] if record else [])
    patch_get_session(monkeypatch, session)
    assert get_bot_paused() is expected


def test_set_bot_paused(monkeypatch):
    """Test set_bot_paused stores normalized true/false values."""
    session = FakeSession()
    patch_get_session(monkeypatch, session)

    set_bot_paused(True, updated_by="telegram")
    set_bot_paused(False)

    assert len(session.merged_records) == 2
    assert session.merged_records[0].control_key == "bot_paused"
    assert session.merged_records[0].control_value == "true"
    assert session.merged_records[0].updated_by == "telegram"
    assert session.merged_records[1].control_value == "false"


def test_set_bot_paused_raises_on_error(monkeypatch):
    """Test set_bot_paused re-raises database errors."""
    patch_get_session_error(monkeypatch)

    with pytest.raises(Exception, match="DB error"):
        set_bot_paused(True)


# ============================================================================
# finalize_session Tests
# ============================================================================


def test_finalize_session_updates_sessions_row(monkeypatch):
    """finalize_session issues an UPDATE against the sessions table."""
    session = FakeSession()
    patch_get_session(monkeypatch, session)

    finalize_session(
        session_id=42,
        ended_at=datetime(2026, 5, 24, 10, 0, 0, tzinfo=UTC),
        status="completed",
        balance=None,
        pair_data=None,
        log_messages=None,
    )

    assert len(session.executed_sql) == 1
    assert "sessions" in session.executed_sql[0].lower()


def test_finalize_session_writes_snapshots_to_bot_control(monkeypatch):
    """When balance and pair_data are present, finalize_session writes both to
    bot_control via set_control_value."""
    session = FakeSession()
    patch_get_session(monkeypatch, session)

    balance = {"ZEUR": "1500", "XXBT": "0.1"}
    pair_data = {"XBTEUR": {"price": 90000.0, "atr": 500.0, "volatility_level": "HV"}}

    finalize_session(
        session_id=1,
        ended_at=datetime(2026, 5, 24, 10, 0, 0, tzinfo=UTC),
        status="completed",
        balance=balance,
        pair_data=pair_data,
        log_messages=None,
    )

    keys = [r.control_key for r in session.merged_records]
    assert "latest_balance" in keys
    assert "latest_pair_data" in keys

    bal_record = next(r for r in session.merged_records if r.control_key == "latest_balance")
    pd_record = next(r for r in session.merged_records if r.control_key == "latest_pair_data")
    assert json.loads(bal_record.control_value) == balance
    assert json.loads(pd_record.control_value) == pair_data
    assert bal_record.updated_by == "scheduler"
    assert pd_record.updated_by == "scheduler"


def test_finalize_session_skips_snapshots_when_none(monkeypatch):
    """When balance is None and pair_data is empty, no bot_control rows are written."""
    session = FakeSession()
    patch_get_session(monkeypatch, session)

    finalize_session(
        session_id=1,
        ended_at=datetime(2026, 5, 24, 10, 0, 0, tzinfo=UTC),
        status="paused",
        balance=None,
        pair_data={},
        log_messages=None,
    )

    assert session.merged_records == []


def test_finalize_session_swallows_snapshot_write_error(monkeypatch, caplog):
    """A bot_control write failure must not propagate out of finalize_session."""
    call_count = 0

    def _get_session_side_effects():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call: the sessions UPDATE — succeeds.
            return FakeSessionContextManager(FakeSession())
        # Subsequent calls (set_control_value): fail.
        return FakeSessionContextManager(FakeSession(), enter_error=Exception("DB error"))

    monkeypatch.setattr(database, "get_session", _get_session_side_effects)

    # Must not raise.
    finalize_session(
        session_id=1,
        ended_at=datetime(2026, 5, 24, 10, 0, 0, tzinfo=UTC),
        status="completed",
        balance={"ZEUR": "100"},
        pair_data={"XBTEUR": {"price": 1.0}},
        log_messages=None,
    )

    assert "Error saving latest_balance" in caplog.text


def test_pair_config_to_dict_round_trips_types():
    from core.database import PairConfig

    row = PairConfig(
        pair="XBTEUR",
        target_pct=Decimal("30.000"),
        hodl_pct=Decimal("10.000"),
        k_act=Decimal("2.0000"),
        min_margin=Decimal("0.00100000"),
        stop_pct_ll=Decimal("0.900"),
        stop_pct_lv=Decimal("0.900"),
        stop_pct_mv=Decimal("0.900"),
        stop_pct_hv=Decimal("0.900"),
        stop_pct_hh=Decimal("0.950"),
    )
    d = row.to_dict()
    assert d["pair"] == "XBTEUR"
    assert d["target_pct"] == 30.0
    assert d["k_act"] == 2.0
    assert d["stop_pct_hh"] == 0.95
    assert isinstance(d["min_margin"], float)


def test_pair_config_to_dict_handles_null_k_act():
    from core.database import PairConfig

    row = PairConfig(
        pair="ETHEUR",
        target_pct=Decimal("0"),
        hodl_pct=Decimal("0"),
        k_act=None,
        min_margin=Decimal("0.002"),
        stop_pct_ll=Decimal("0.9"),
        stop_pct_lv=Decimal("0.9"),
        stop_pct_mv=Decimal("0.9"),
        stop_pct_hv=Decimal("0.9"),
        stop_pct_hh=Decimal("0.9"),
    )
    assert row.to_dict()["k_act"] is None
