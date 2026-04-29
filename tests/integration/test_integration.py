import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import core.database as db
import core.runtime as runtime
from api.routes import control, market, status
from exchange import kraken

# ============================================================================
# Kraken API Integration Tests
# ============================================================================


def _kraken_integration_enabled() -> bool:
    return os.getenv("RUN_KRAKEN_INTEGRATION", "false").lower() == "true"


@pytest.fixture(scope="session")
def kraken_enabled() -> bool:
    has_credentials = bool(os.getenv("KRAKEN_API_KEY")) and bool(os.getenv("KRAKEN_API_SECRET"))
    return _kraken_integration_enabled() and has_credentials


@pytest.mark.integration
def test_get_balance(kraken_enabled: bool) -> None:
    if not kraken_enabled:
        pytest.skip("Kraken integration disabled. Set RUN_KRAKEN_INTEGRATION=true with Kraken credentials.")

    balance = kraken.get_balance()

    assert balance is not None
    assert isinstance(balance, dict)


# ============================================================================
# Database Integration Tests
# ============================================================================


def _db_integration_enabled() -> bool:
    return os.getenv("RUN_DB_INTEGRATION", "false").lower() == "true"


@pytest.mark.integration
def test_get_bot_paused() -> None:
    if not _db_integration_enabled():
        pytest.skip("PostgreSQL DAL integration disabled. Set RUN_DB_INTEGRATION=true to run this test.")

    assert isinstance(db.get_bot_paused(), bool)


# ============================================================================
# API Route Integration Tests
# ============================================================================


@pytest.fixture
def control_client(monkeypatch):
    state = {"paused": False}
    monkeypatch.setattr(db, "get_bot_paused", lambda: state["paused"])
    monkeypatch.setattr(db, "set_bot_paused", lambda val, updated_by=None: state.update(paused=val))
    monkeypatch.setattr(runtime, "get_last_run_at", lambda: None)
    app = FastAPI()
    app.include_router(control.router)
    app.include_router(status.router)
    return TestClient(app)


@pytest.fixture
def market_client(monkeypatch):
    monkeypatch.setattr(market, "PAIRS", {"XBTEUR": {}})
    app = FastAPI()
    app.include_router(market.router)
    return TestClient(app)


@pytest.mark.integration
def test_pause_resume_cycle(control_client) -> None:
    """Control and status routes share state correctly across a pause/resume round-trip."""
    control_client.post("/control/pause", json={"updated_by": "test"})
    assert control_client.get("/status").json()["paused"] is True
    control_client.post("/control/resume")
    assert control_client.get("/status").json()["paused"] is False


@pytest.mark.integration
def test_market_reflects_runtime_update(market_client) -> None:
    """Runtime data written by the scheduler is immediately visible via the market route."""
    runtime.update_pair_data("XBTEUR", price=85000.0, atr=600.0, volatility_level="HH")
    resp = market_client.get("/market/XBTEUR")
    assert resp.status_code == 200
    assert resp.json()["last_price"] == 85000.0
    assert resp.json()["volatility_level"] == "HH"
