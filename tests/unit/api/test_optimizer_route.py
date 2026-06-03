"""Unit tests for /optimizer/jobs routes."""

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import core.database as db
from api.routes import optimizer as optimizer_route
from trading.optimizer.jobs import OptimizerBusyError

_PAIR = "XBTEUR"
_PAIRS = {_PAIR: {}}
_JOB_ID = "11111111-1111-1111-1111-111111111111"
_TS = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

_JOB_ROW = {
    "id": _JOB_ID,
    "pair": _PAIR,
    "mode": "AGGRESSIVE",
    "split_method": "RESET",
    "status": "completed",
    "request": {"pair": _PAIR, "mode": "AGGRESSIVE"},
    "result": {"scores": {"robust_pnl_pct": 1.5}},
    "error": None,
    "created_at": _TS,
    "started_at": _TS,
    "finished_at": _TS,
}


def _make_client(monkeypatch) -> TestClient:
    monkeypatch.setattr(optimizer_route, "PAIRS", _PAIRS)
    app = FastAPI()
    app.include_router(optimizer_route.router)
    return TestClient(app)


def test_submit_unknown_pair_returns_400(monkeypatch) -> None:
    client = _make_client(monkeypatch)
    resp = client.post("/optimizer/jobs", json={"pair": "UNKNOWN", "mode": "AGGRESSIVE"})
    assert resp.status_code == 400
    assert "Unknown pair" in resp.json()["detail"]


def test_submit_returns_202_with_job_id(monkeypatch) -> None:
    monkeypatch.setattr(optimizer_route.JOB_STORE, "try_start", lambda req: _JOB_ID)
    client = _make_client(monkeypatch)
    resp = client.post("/optimizer/jobs", json={"pair": _PAIR, "mode": "AGGRESSIVE"})
    assert resp.status_code == 202
    body = resp.json()
    assert body["job_id"] == _JOB_ID
    assert body["status"] == "running"


def test_submit_busy_returns_409(monkeypatch) -> None:
    def _busy(req):
        raise OptimizerBusyError("already running")

    monkeypatch.setattr(optimizer_route.JOB_STORE, "try_start", _busy)
    client = _make_client(monkeypatch)
    resp = client.post("/optimizer/jobs", json={"pair": _PAIR, "mode": "AGGRESSIVE"})
    assert resp.status_code == 409


def test_get_job_404_when_unknown(monkeypatch) -> None:
    monkeypatch.setattr(db, "get_optimizer_job", lambda jid: None)
    client = _make_client(monkeypatch)
    resp = client.get("/optimizer/jobs/nonexistent-id")
    assert resp.status_code == 404


@pytest.mark.parametrize("status", ["running", "completed", "failed"])
def test_get_job_returns_status(monkeypatch, status: str) -> None:
    row = dict(
        _JOB_ROW,
        status=status,
        result=_JOB_ROW["result"] if status == "completed" else None,
        error="boom" if status == "failed" else None,
    )
    monkeypatch.setattr(db, "get_optimizer_job", lambda jid: row)
    client = _make_client(monkeypatch)
    resp = client.get(f"/optimizer/jobs/{_JOB_ID}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == status
    assert body["job_id"] == _JOB_ID
