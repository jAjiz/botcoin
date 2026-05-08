import asyncio
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import api.app as api_app
import core.database as db
import core.runtime as runtime
from api.app import health, lifespan, verify_token
from api.routes import balance, control, market, positions, status

_PAIRS = {"XBTEUR": {}, "ETHEUR": {}}
_TS = datetime(2026, 4, 27, 10, 0, 0, tzinfo=UTC)
_POSITION = {
    "side": "buy",
    "volume": 0.01,
    "entry_price": 80000.0,
    "activation_atr": 500.0,
    "activation_price": 81000.0,
    "created_at": datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC),
    "activated_at": None,
    "trailing_price": 82000.0,
    "stop_price": 78000.0,
    "stop_atr": 490.0,
    "closing_order_id": None,
    "closing_price": None,
    "closing_requested_at": None,
}


def _control_state(monkeypatch, initial_paused=False):
    state = {"paused": initial_paused, "set_calls": 0}
    monkeypatch.setattr(db, "get_bot_paused", lambda: state["paused"])

    def _set(val, updated_by=None):
        state["paused"] = val
        state["set_calls"] += 1

    monkeypatch.setattr(db, "set_bot_paused", _set)
    app = FastAPI()
    app.include_router(control.router)
    return TestClient(app), state


# ============================================================================
# Health
# ============================================================================


def test_health_returns_ok():
    assert health() == {"ok": True}


# ============================================================================
# Token verification
# ============================================================================


def test_verify_token_no_secret_configured(monkeypatch):
    monkeypatch.setattr(api_app, "API_SECRET_TOKEN", None)
    verify_token(None)  # must not raise


@pytest.mark.parametrize("token", [None, "wrong-token"])
def test_verify_token_invalid_raises_401(monkeypatch, token):
    monkeypatch.setattr(api_app, "API_SECRET_TOKEN", "correct-secret")
    with pytest.raises(HTTPException) as exc_info:
        verify_token(token)
    assert exc_info.value.status_code == 401


def test_verify_token_correct_passes(monkeypatch):
    monkeypatch.setattr(api_app, "API_SECRET_TOKEN", "correct-secret")
    verify_token("correct-secret")  # must not raise


# ============================================================================
# Lifespan
# ============================================================================


def test_lifespan_validate_config_fails(monkeypatch):
    monkeypatch.setattr(api_app, "validate_config", lambda: False)

    async def _run():
        async with lifespan(MagicMock()):
            pass

    with pytest.raises(RuntimeError, match="Configuration validation failed"):
        asyncio.run(_run())


def test_lifespan_db_connection_fails(monkeypatch):
    monkeypatch.setattr(api_app, "validate_config", lambda: True)
    monkeypatch.setattr(api_app.db, "check_database_connection", lambda: False)

    async def _run():
        async with lifespan(MagicMock()):
            pass

    with pytest.raises(RuntimeError, match="Cannot connect to PostgreSQL"):
        asyncio.run(_run())


def test_lifespan_success_starts_and_stops_scheduler(monkeypatch):
    monkeypatch.setattr(api_app, "validate_config", lambda: True)
    monkeypatch.setattr(api_app.db, "check_database_connection", lambda: True)
    monkeypatch.setattr(api_app, "scheduler", None)

    mock_scheduler = MagicMock()
    monkeypatch.setattr(api_app, "AsyncIOScheduler", lambda **kwargs: mock_scheduler)

    async def _run():
        async with lifespan(MagicMock()):
            pass

    asyncio.run(_run())
    mock_scheduler.start.assert_called_once()
    mock_scheduler.shutdown.assert_called_once_with(wait=True)


# ============================================================================
# Market
# ============================================================================


def test_get_market_returns_all_pairs(monkeypatch):
    monkeypatch.setattr(market, "PAIRS", _PAIRS)
    data = {
        "XBTEUR": {"last_price": 80000.0, "atr": 500.0, "volatility_level": "MV"},
        "ETHEUR": {"last_price": 2000.0, "atr": 100.0, "volatility_level": "LV"},
    }
    monkeypatch.setattr(runtime, "get_pair_data", lambda pair: data.get(pair, {}))
    app = FastAPI()
    app.include_router(market.router)
    pairs = {item["pair"]: item for item in TestClient(app).get("/market").json()}
    assert pairs["XBTEUR"]["last_price"] == 80000.0
    assert pairs["ETHEUR"]["volatility_level"] == "LV"


def test_get_market_unknown_pair_returns_404(monkeypatch):
    monkeypatch.setattr(market, "PAIRS", _PAIRS)
    monkeypatch.setattr(runtime, "get_pair_data", lambda pair: {})
    app = FastAPI()
    app.include_router(market.router)
    assert TestClient(app).get("/market/UNKNOWN").status_code == 404


# ============================================================================
# Balance
# ============================================================================


def test_get_balance(monkeypatch):
    monkeypatch.setattr(runtime, "get_last_balance", lambda: {"ZEUR": 1500.0, "XXBT": 0.25})
    app = FastAPI()
    app.include_router(balance.router)
    assert TestClient(app).get("/balance").json() == {"balance": {"ZEUR": 1500.0, "XXBT": 0.25}}


# ============================================================================
# Status
# ============================================================================


def test_get_status(monkeypatch):
    monkeypatch.setattr(db, "get_bot_paused", lambda: True)
    monkeypatch.setattr(runtime, "get_last_run_at", lambda: _TS)
    app = FastAPI()
    app.include_router(status.router)
    body = TestClient(app).get("/status").json()
    assert body["paused"] is True
    assert body["last_run_at"] is not None


# ============================================================================
# Control
# ============================================================================


def test_pause(monkeypatch):
    client, state = _control_state(monkeypatch, initial_paused=False)
    resp = client.post("/control/pause", json={"updated_by": "telegram"})
    assert resp.json() == {"paused": True, "updated_by": "telegram"}
    assert state["paused"] is True


def test_resume_accepts_no_body(monkeypatch):
    client, state = _control_state(monkeypatch, initial_paused=True)
    assert client.post("/control/resume").json()["paused"] is False
    assert state["paused"] is False


def test_pause_is_idempotent(monkeypatch):
    client, state = _control_state(monkeypatch, initial_paused=True)
    client.post("/control/pause")
    assert state["set_calls"] == 0


# ============================================================================
# Positions
# ============================================================================


def test_get_positions(monkeypatch):
    monkeypatch.setattr(positions, "PAIRS", _PAIRS)
    monkeypatch.setattr(db, "load_trailing_state", lambda pair: _POSITION if pair == "XBTEUR" else None)
    app = FastAPI()
    app.include_router(positions.router)
    body = TestClient(app).get("/positions").json()
    assert body["XBTEUR"]["entry_price"] == 80000.0
    assert body["ETHEUR"] is None


def test_get_position_unknown_pair_returns_404(monkeypatch):
    monkeypatch.setattr(positions, "PAIRS", _PAIRS)
    monkeypatch.setattr(db, "load_trailing_state", lambda pair: None)
    app = FastAPI()
    app.include_router(positions.router)
    assert TestClient(app).get("/positions/UNKNOWN").status_code == 404


# ============================================================================
# Exception handler
# ============================================================================


def test_unhandled_exception_returns_500():
    app = FastAPI()
    app.add_exception_handler(Exception, api_app._unhandled)

    @app.get("/boom")
    def boom():
        raise ValueError("unexpected")

    resp = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert resp.status_code == 500
    assert resp.json() == {"detail": "internal error"}
