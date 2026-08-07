"""Unit tests for /optimizer/jobs routes."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import core.database as db
from api.routes import optimizer as optimizer_route
from api.schemas import OptimizerRequest as OptimizerRequestSchema
from trading.optimizer.jobs import OptimizerBusyError

_PAIR = "XBTEUR"
_PAIRS = {_PAIR: {}}
_JOB_ID = 1
_TS = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

# Minimal valid search space; OPTIMIZE/AUTO require one at the schema level.
_SPACE = {
    "stop_pcts": {"start": 0.20, "end": 0.95, "step": 0.25},
    "k_act": {"start": 0.0, "end": 4.0, "step": 1.0},
    "min_margin": None,
}

_JOB_ROW = {
    "id": _JOB_ID,
    "pair": _PAIR,
    "mode": "OPTIMIZE",
    "split_method": "CONTINUE",
    "status": "completed",
    "request": {"pair": _PAIR, "mode": "OPTIMIZE", "fee_pct": 0.4, "n_trials": 1000},
    "result": {
        "pair": _PAIR,
        "mode": "OPTIMIZE",
        "top_candidates": [
            {
                "k_act": 0.0,
                "min_margin": None,
                "stop_pcts": {},
                "in_sample_pnl_pct": 2.0,
                "train_pnl_pct": 1.8,
                "test_pnl_pct": 1.5,
                "robust_pnl_pct": 1.5,
            },
        ],
        "suggested_env_lines": ["XBTEUR_K_ACT=0.0"],
        "n_trials_run": 10,
        "n_trials_pruned": 0,
    },
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
    resp = client.post("/optimizer/jobs", json={"pair": "UNKNOWN", "mode": "OPTIMIZE", "search_space": _SPACE})
    assert resp.status_code == 400
    assert "Unknown pair" in resp.json()["detail"]


def test_submit_invalid_mode_returns_422(monkeypatch) -> None:
    client = _make_client(monkeypatch)
    resp = client.post("/optimizer/jobs", json={"pair": _PAIR, "mode": "AGGRESSIVE"})
    assert resp.status_code == 422


@pytest.mark.parametrize("mode", ["OPTIMIZE", "AUTO"])
def test_submit_without_search_space_returns_422(monkeypatch, mode: str) -> None:
    """search_space is required for the search modes — enforced at the route."""
    client = _make_client(monkeypatch)
    resp = client.post("/optimizer/jobs", json={"pair": _PAIR, "mode": mode})
    assert resp.status_code == 422
    assert "search_space is required" in resp.json()["detail"]


def test_submit_returns_202_with_job_id(monkeypatch) -> None:
    monkeypatch.setattr(optimizer_route.JOB_STORE, "try_start", lambda req: _JOB_ID)
    client = _make_client(monkeypatch)
    resp = client.post("/optimizer/jobs", json={"pair": _PAIR, "mode": "OPTIMIZE", "search_space": _SPACE})
    assert resp.status_code == 202
    body = resp.json()
    assert body["job_id"] == _JOB_ID
    assert body["status"] == "running"


def test_submit_calls_try_start_via_to_thread(monkeypatch) -> None:
    """try_start does a DB insert + a synchronous Telegram HTTP call; it must run
    off the event loop via asyncio.to_thread, not be awaited/called directly."""
    calls = []

    async def _fake_to_thread(func, *args):
        calls.append(func)
        return func(*args)

    monkeypatch.setattr(optimizer_route.asyncio, "to_thread", _fake_to_thread)
    monkeypatch.setattr(optimizer_route.JOB_STORE, "try_start", lambda req: _JOB_ID)
    client = _make_client(monkeypatch)
    resp = client.post("/optimizer/jobs", json={"pair": _PAIR, "mode": "OPTIMIZE", "search_space": _SPACE})
    assert resp.status_code == 202
    assert calls == [optimizer_route.JOB_STORE.try_start]


def test_submit_retains_supervise_task_and_discards_on_completion(monkeypatch) -> None:
    """The supervise() task must be kept in a module-level set (not fire-and-forget
    via a bare create_task) so it cannot be garbage-collected mid-flight, and must
    be discarded once it completes."""
    monkeypatch.setattr(optimizer_route, "PAIRS", _PAIRS)
    monkeypatch.setattr(optimizer_route.JOB_STORE, "try_start", lambda req: _JOB_ID)
    monkeypatch.setattr(optimizer_route.JOB_STORE, "supervise", AsyncMock(return_value=None))

    async def _run():
        req = OptimizerRequestSchema(pair=_PAIR, mode="OPTIMIZE", search_space=_SPACE)
        await optimizer_route.submit(req)
        assert len(optimizer_route._supervise_tasks) == 1
        # yield control so the task runs to completion and its done-callback fires
        for _ in range(5):
            await asyncio.sleep(0)
        assert len(optimizer_route._supervise_tasks) == 0

    asyncio.run(_run())


def test_submit_disabled_returns_503(monkeypatch) -> None:
    monkeypatch.setattr(optimizer_route, "MAX_CONCURRENT_JOBS", 0)
    client = _make_client(monkeypatch)
    resp = client.post("/optimizer/jobs", json={"pair": _PAIR, "mode": "OPTIMIZE", "search_space": _SPACE})
    assert resp.status_code == 503
    assert "disabled" in resp.json()["detail"].lower()


def test_submit_busy_returns_409(monkeypatch) -> None:
    def _busy(req):
        raise OptimizerBusyError("all slots busy")

    monkeypatch.setattr(optimizer_route.JOB_STORE, "try_start", _busy)
    client = _make_client(monkeypatch)
    resp = client.post("/optimizer/jobs", json={"pair": _PAIR, "mode": "OPTIMIZE", "search_space": _SPACE})
    assert resp.status_code == 409


def test_get_job_404_when_unknown(monkeypatch) -> None:
    monkeypatch.setattr(db, "get_optimizer_job", lambda jid: None)
    client = _make_client(monkeypatch)
    resp = client.get("/optimizer/jobs/999")
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


def test_get_job_output_is_deduped_and_pruned(monkeypatch) -> None:
    """pair/mode appear once — inside the echoed request, not at the top level nor
    duplicated in the result; split_method is gone."""
    monkeypatch.setattr(db, "get_optimizer_job", lambda jid: dict(_JOB_ROW))
    client = _make_client(monkeypatch)
    body = client.get(f"/optimizer/jobs/{_JOB_ID}").json()

    assert "pair" not in body and "mode" not in body and "split_method" not in body
    assert body["request"]["pair"] == _PAIR and body["request"]["mode"] == "OPTIMIZE"
    assert "pair" not in body["result"] and "mode" not in body["result"]
    # scores survive the typed candidate model
    assert body["result"]["top_candidates"][0]["robust_pnl_pct"] == 1.5
    # OPTIMIZE result has no AUTO block
    assert body["result"]["auto"] is None


def test_get_auto_job_nests_auto_fields(monkeypatch) -> None:
    """AUTO result: the consensus fields are grouped under a nested `auto` object,
    not repeated at the top level of the result."""
    row = dict(
        _JOB_ROW,
        mode="AUTO",
        request={"pair": _PAIR, "mode": "AUTO", "auto_settings": {"n_seeds": 4, "min_agree": 3}},
        result={
            "mode": "AUTO",
            "top_candidates": [{"k_act": 0.0, "stop_pcts": {}, "robust_pnl_pct": 5.0}],
            "suggested_env_lines": [],
            "n_trials_run": 2000,
            "converged": True,
            "n_seeds_agreed": 3,
            "seeds_used": [1, 2, 3, 4],
        },
    )
    monkeypatch.setattr(db, "get_optimizer_job", lambda jid: row)
    client = _make_client(monkeypatch)
    body = client.get(f"/optimizer/jobs/{_JOB_ID}").json()

    auto = body["result"]["auto"]
    assert auto["converged"] is True and auto["n_seeds_agreed"] == 3
    assert auto["seeds_used"] == [1, 2, 3, 4]
    # AUTO reports only the search outcome — no comparison against current
    assert "is_improvement" not in auto and "current_robust_pnl" not in auto
    # the AUTO fields are not duplicated at the result top level
    assert "converged" not in body["result"] and "seeds_used" not in body["result"]
    # the request echo groups the AUTO knobs
    assert body["request"]["auto_settings"]["n_seeds"] == 4


def test_submit_rejects_non_iso_start(monkeypatch) -> None:
    """An unparseable window used to be accepted and only fail inside the spawned
    worker, after a job row had already been created."""
    client = _make_client(monkeypatch)
    resp = client.post(
        "/optimizer/jobs",
        json={"pair": _PAIR, "mode": "OPTIMIZE", "search_space": _SPACE, "start": "last week"},
    )
    assert resp.status_code == 422
    assert "ISO" in str(resp.json()["detail"])


def test_submit_rejects_non_iso_end(monkeypatch) -> None:
    client = _make_client(monkeypatch)
    resp = client.post(
        "/optimizer/jobs",
        json={"pair": _PAIR, "mode": "OPTIMIZE", "search_space": _SPACE, "end": "31/01/2026"},
    )
    assert resp.status_code == 422


def test_submit_accepts_naive_iso_window(monkeypatch) -> None:
    monkeypatch.setattr(optimizer_route.JOB_STORE, "try_start", lambda req: _JOB_ID)
    client = _make_client(monkeypatch)
    resp = client.post(
        "/optimizer/jobs",
        json={
            "pair": _PAIR,
            "mode": "OPTIMIZE",
            "search_space": _SPACE,
            "start": "2026-01-01",
            "end": "2026-02-01T00:00:00",
        },
    )
    assert resp.status_code == 202


def test_optimizer_request_echoes_a_non_iso_window() -> None:
    """The model must stay lenient: it doubles as the response model for stored
    jobs, and `try_start` inserts the row *before* the run, so a job whose window
    never parsed still has a row to render."""
    req = OptimizerRequestSchema(pair=_PAIR, mode="OPTIMIZE", start="last week")
    assert req.start == "last week"


def test_get_job_renders_a_stored_non_iso_window(monkeypatch) -> None:
    row = dict(_JOB_ROW, request={**_JOB_ROW["request"], "start": "last week"})
    monkeypatch.setattr(db, "get_optimizer_job", lambda jid: row)
    client = _make_client(monkeypatch)
    resp = client.get(f"/optimizer/jobs/{_JOB_ID}")
    assert resp.status_code == 200
    assert resp.json()["request"]["start"] == "last week"


def test_list_jobs_is_not_broken_by_one_stored_non_iso_window(monkeypatch) -> None:
    """The whole list is built in one comprehension: a single unrenderable row
    would hide every other job."""
    bad = dict(_JOB_ROW, request={**_JOB_ROW["request"], "start": "last week"})
    monkeypatch.setattr(db, "list_optimizer_jobs", lambda limit: [bad, dict(_JOB_ROW)])
    client = _make_client(monkeypatch)
    resp = client.get("/optimizer/jobs")
    assert resp.status_code == 200
    assert len(resp.json()) == 2
