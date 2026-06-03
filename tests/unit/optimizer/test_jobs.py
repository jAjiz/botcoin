"""Unit tests for trading.optimizer.jobs.JobStore."""

import asyncio
import concurrent.futures
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

import core.database as db
from trading.optimizer.jobs import JobStore, OptimizerBusyError, _ActiveJob


@dataclass
class _FakeReq:
    pair: str = "XBTEUR"
    mode: str = "AGGRESSIVE"
    split_method: str = "RESET"
    start: str | None = None
    end: str | None = None

    @property
    def __dict__(self):
        return {
            "pair": self.pair,
            "mode": self.mode,
            "split_method": self.split_method,
            "start": self.start,
            "end": self.end,
        }


def _resolved_future(result) -> concurrent.futures.Future:
    f = concurrent.futures.Future()
    f.set_result(result)
    return f


def _failed_future(exc: Exception) -> concurrent.futures.Future:
    f = concurrent.futures.Future()
    f.set_exception(exc)
    return f


def test_try_start_inserts_row_and_returns_id(monkeypatch) -> None:
    store = JobStore()
    created_ids = []

    def _fake_create(pair, mode, split_method, request):
        job_id = "test-job-uuid"
        created_ids.append({"pair": pair, "mode": mode})
        return job_id

    monkeypatch.setattr(db, "create_optimizer_job", _fake_create)

    mock_future = MagicMock()
    mock_future.done.return_value = False

    with patch("trading.optimizer.jobs._EXECUTOR") as mock_executor:
        mock_executor.submit.return_value = mock_future
        job_id = store.try_start(_FakeReq())

    assert job_id == "test-job-uuid"
    assert created_ids[0]["pair"] == "XBTEUR"
    assert created_ids[0]["mode"] == "AGGRESSIVE"
    mock_executor.submit.assert_called_once()


def test_try_start_busy_raises(monkeypatch) -> None:
    store = JobStore()
    running_future = MagicMock()
    running_future.done.return_value = False
    store._active = _ActiveJob(
        job_id="existing-job",
        future=running_future,
        pair="XBTEUR",
    )

    with pytest.raises(OptimizerBusyError, match="existing-job"):
        store.try_start(_FakeReq())


def test_finalize_completes_job(monkeypatch) -> None:
    store = JobStore()
    completed = {}

    monkeypatch.setattr(
        db, "complete_optimizer_job", lambda job_id, result: completed.update({"job_id": job_id, "result": result})
    )

    active = _ActiveJob(
        job_id="job-1",
        future=concurrent.futures.Future(),
        pair="XBTEUR",
    )
    store._active = active

    store._finalize(active, "ok", {"scores": {"robust_pnl_pct": 3.5}})

    assert completed["job_id"] == "job-1"
    assert completed["result"]["scores"]["robust_pnl_pct"] == 3.5
    assert store._active is None


def test_finalize_failed_job(monkeypatch) -> None:
    store = JobStore()
    failed = {}

    monkeypatch.setattr(
        db, "fail_optimizer_job", lambda job_id, error: failed.update({"job_id": job_id, "error": error})
    )

    active = _ActiveJob(
        job_id="job-2",
        future=concurrent.futures.Future(),
        pair="XBTEUR",
    )
    store._active = active

    store._finalize(active, "error", "boom")

    assert failed["job_id"] == "job-2"
    assert "boom" in failed["error"]
    assert store._active is None


def test_supervise_ok(monkeypatch) -> None:
    """supervise() calls _finalize with the result when the worker succeeds."""
    store = JobStore()
    completed = {}

    monkeypatch.setattr(db, "complete_optimizer_job", lambda job_id, result: completed.update({"job_id": job_id}))

    store._active = _ActiveJob(
        job_id="job-ok",
        future=_resolved_future({"scores": {"robust_pnl_pct": 2.0}}),
        pair="XBTEUR",
    )

    asyncio.run(store.supervise())

    assert completed["job_id"] == "job-ok"
    assert store._active is None


def test_supervise_error(monkeypatch) -> None:
    """supervise() calls _finalize with error when the worker fails."""
    store = JobStore()
    failed = {}

    monkeypatch.setattr(
        db, "fail_optimizer_job", lambda job_id, error: failed.update({"job_id": job_id, "error": error})
    )

    store._active = _ActiveJob(
        job_id="job-err",
        future=_failed_future(RuntimeError("boom")),
        pair="XBTEUR",
    )

    asyncio.run(store.supervise())

    assert failed["job_id"] == "job-err"
    assert "boom" in failed["error"]
    assert store._active is None
