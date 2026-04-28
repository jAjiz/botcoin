from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import core.database as db
import core.runtime as runtime
from api.routes import balance, control, market, positions, status

_PAIRS = {"XBTEUR": {}, "ETHEUR": {}}
_TS = datetime(2026, 4, 27, 10, 0, 0, tzinfo=timezone.utc)
_POSITION = {
    "side": "buy",
    "volume": 0.01,
    "entry_price": 80000.0,
    "activation_atr": 500.0,
    "activation_price": 81000.0,
    "created_at": datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc),
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

    @app.exception_handler(Exception)
    async def _unhandled(request, exc):
        return JSONResponse(status_code=500, content={"detail": "internal error"})

    @app.get("/boom")
    def boom():
        raise ValueError("unexpected")

    resp = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert resp.status_code == 500
    assert resp.json() == {"detail": "internal error"}
