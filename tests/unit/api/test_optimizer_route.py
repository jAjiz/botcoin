"""Tests for optimizer job endpoints."""

import json
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.routes.optimizer_jobs as route_mod
import core.database as db
from api.routes.optimizer_jobs import router

_FAKE_JOB = {
    "id": "abc-123",
    "pair": "XBTEUR",
    "mode": "CONSERVATIVE",
    "status": "completed",
    "created_at": datetime(2026, 5, 31, 0, 0, 0, tzinfo=UTC),
    "started_at": datetime(2026, 5, 31, 0, 0, 1, tzinfo=UTC),
    "ended_at": datetime(2026, 5, 31, 0, 5, 0, tzinfo=UTC),
    "request_json": json.dumps({"pair": "XBTEUR", "mode": "CONSERVATIVE"}),
    "result_json": json.dumps({"pair": "XBTEUR", "best_robust_pnl": 3.5}),
    "error": None,
}


def _make_app():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ============================================================================
# POST /optimizer/jobs
# ============================================================================


def test_submit_job_returns_202_with_job_id(monkeypatch):
    monkeypatch.setattr(route_mod, "is_busy", lambda: False)
    monkeypatch.setattr(db, "create_optimizer_job", lambda **_: None)
    monkeypatch.setattr(route_mod, "try_start_job", lambda job_id, req: True)

    client = _make_app()
    resp = client.post("/optimizer/jobs", json={"pair": "XBTEUR", "mode": "CONSERVATIVE"})

    assert resp.status_code == 202
    assert "job_id" in resp.json()


def test_submit_job_returns_409_when_busy(monkeypatch):
    monkeypatch.setattr(route_mod, "is_busy", lambda: True)

    client = _make_app()
    resp = client.post("/optimizer/jobs", json={"pair": "XBTEUR", "mode": "CONSERVATIVE"})

    assert resp.status_code == 409


def test_submit_job_returns_409_on_race_condition(monkeypatch):
    monkeypatch.setattr(route_mod, "is_busy", lambda: False)
    monkeypatch.setattr(db, "create_optimizer_job", lambda **_: None)
    monkeypatch.setattr(db, "update_optimizer_job", lambda *a, **kw: None)
    # try_start_job returns False (slot grabbed by concurrent request)
    monkeypatch.setattr(route_mod, "try_start_job", lambda job_id, req: False)

    client = _make_app()
    resp = client.post("/optimizer/jobs", json={"pair": "XBTEUR", "mode": "CONSERVATIVE"})

    assert resp.status_code == 409


def test_submit_job_rejects_invalid_mode(monkeypatch):
    client = _make_app()
    resp = client.post("/optimizer/jobs", json={"pair": "XBTEUR", "mode": "INVALID"})
    assert resp.status_code == 422


# ============================================================================
# GET /optimizer/jobs
# ============================================================================


def test_list_jobs_returns_serialized_jobs(monkeypatch):
    monkeypatch.setattr(db, "list_optimizer_jobs", lambda limit=50: [_FAKE_JOB.copy()])

    client = _make_app()
    resp = client.get("/optimizer/jobs")

    assert resp.status_code == 200
    jobs = resp.json()
    assert len(jobs) == 1
    assert jobs[0]["id"] == "abc-123"
    assert jobs[0]["result"] == {"pair": "XBTEUR", "best_robust_pnl": 3.5}
    assert "result_json" not in jobs[0]


# ============================================================================
# GET /optimizer/jobs/{id}
# ============================================================================


def test_get_job_returns_job(monkeypatch):
    monkeypatch.setattr(db, "get_optimizer_job", lambda job_id: _FAKE_JOB.copy())

    client = _make_app()
    resp = client.get("/optimizer/jobs/abc-123")

    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


def test_get_job_returns_404_when_not_found(monkeypatch):
    monkeypatch.setattr(db, "get_optimizer_job", lambda job_id: None)

    client = _make_app()
    resp = client.get("/optimizer/jobs/nonexistent")

    assert resp.status_code == 404
