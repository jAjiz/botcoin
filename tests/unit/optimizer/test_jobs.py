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
    mode: str = "OPTIMIZE"
    start: str | None = None
    end: str | None = None

    @property
    def __dict__(self):
        return {
            "pair": self.pair,
            "mode": self.mode,
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
    store = JobStore(max_concurrent=1)
    created_ids = []
    _next_id = 1

    def _fake_create(pair, mode, split_method, request):
        created_ids.append({"pair": pair, "mode": mode})
        return _next_id

    monkeypatch.setattr(db, "create_optimizer_job", _fake_create)

    mock_future = MagicMock()
    mock_future.done.return_value = False

    with patch("trading.optimizer.jobs._EXECUTOR") as mock_executor:
        mock_executor.submit.return_value = mock_future
        job_id = store.try_start(_FakeReq())

    assert job_id == _next_id
    assert created_ids[0]["pair"] == "XBTEUR"
    assert created_ids[0]["mode"] == "OPTIMIZE"
    mock_executor.submit.assert_called_once()


def test_try_start_busy_raises(monkeypatch) -> None:
    store = JobStore(max_concurrent=1)
    running_future = MagicMock()
    running_future.done.return_value = False
    store._active[1] = _ActiveJob(job_id=1, future=running_future, pair="XBTEUR")

    with pytest.raises(OptimizerBusyError):
        store.try_start(_FakeReq())


def test_try_start_concurrency_allows_n_slots(monkeypatch) -> None:
    """With _max=2, two jobs can start; a third raises OptimizerBusyError."""
    store = JobStore(max_concurrent=2)
    _id_counter = iter([1, 2])

    def _fake_create(pair, mode, split_method, request):
        return next(_id_counter)

    monkeypatch.setattr(db, "create_optimizer_job", _fake_create)

    mock_future = MagicMock()
    mock_future.done.return_value = False

    with patch("trading.optimizer.jobs._EXECUTOR") as mock_executor:
        mock_executor.submit.return_value = mock_future
        id1 = store.try_start(_FakeReq(pair="XBTEUR"))
        id2 = store.try_start(_FakeReq(pair="ETHEUR"))

    assert id1 == 1
    assert id2 == 2
    assert len(store._active) == 2

    with pytest.raises(OptimizerBusyError):
        store.try_start(_FakeReq())


def test_finalize_completes_job(monkeypatch) -> None:
    store = JobStore(max_concurrent=1)
    completed = {}

    monkeypatch.setattr(
        db, "complete_optimizer_job", lambda job_id, result: completed.update({"job_id": job_id, "result": result})
    )

    active = _ActiveJob(job_id=1, future=concurrent.futures.Future(), pair="XBTEUR")
    store._active[1] = active

    store._finalize(active, "ok", {"scores": {"robust_pnl_pct": 3.5}})

    assert completed["job_id"] == 1
    assert completed["result"]["scores"]["robust_pnl_pct"] == 3.5
    assert 1 not in store._active


def test_finalize_failed_job(monkeypatch) -> None:
    store = JobStore(max_concurrent=1)
    failed = {}

    monkeypatch.setattr(
        db, "fail_optimizer_job", lambda job_id, error: failed.update({"job_id": job_id, "error": error})
    )

    active = _ActiveJob(job_id=2, future=concurrent.futures.Future(), pair="XBTEUR")
    store._active[2] = active

    store._finalize(active, "error", "boom")

    assert failed["job_id"] == 2
    assert "boom" in failed["error"]
    assert 2 not in store._active


def test_supervise_ok(monkeypatch) -> None:
    """supervise() calls _finalize with the result when the worker succeeds."""
    store = JobStore(max_concurrent=1)
    completed = {}

    monkeypatch.setattr(db, "complete_optimizer_job", lambda job_id, result: completed.update({"job_id": job_id}))

    store._active[10] = _ActiveJob(
        job_id=10,
        future=_resolved_future({"scores": {"robust_pnl_pct": 2.0}}),
        pair="XBTEUR",
    )

    asyncio.run(store.supervise(10))

    assert completed["job_id"] == 10
    assert 10 not in store._active


def test_supervise_error(monkeypatch) -> None:
    """supervise() calls _finalize with error when the worker fails."""
    store = JobStore(max_concurrent=1)
    failed = {}

    monkeypatch.setattr(
        db, "fail_optimizer_job", lambda job_id, error: failed.update({"job_id": job_id, "error": error})
    )

    store._active[11] = _ActiveJob(
        job_id=11,
        future=_failed_future(RuntimeError("boom")),
        pair="XBTEUR",
    )

    asyncio.run(store.supervise(11))

    assert failed["job_id"] == 11
    assert "boom" in failed["error"]
    assert 11 not in store._active
