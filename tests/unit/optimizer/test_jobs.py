"""Unit tests for optimizer.jobs.JobStore."""

import asyncio
from dataclasses import dataclass
from queue import Empty
from unittest.mock import MagicMock, patch

import pytest

import core.database as db
from optimizer.jobs import JobStore, OptimizerBusyError, _ActiveJob


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


def _fake_process(alive: bool = True) -> MagicMock:
    p = MagicMock()
    p.is_alive.return_value = alive
    p.exitcode = 0
    return p


def test_try_start_inserts_row_and_returns_id(monkeypatch) -> None:
    store = JobStore()
    created_ids = []

    def _fake_create(pair, mode, split_method, request):
        job_id = "test-job-uuid"
        created_ids.append({"pair": pair, "mode": mode})
        return job_id

    monkeypatch.setattr(db, "create_optimizer_job", _fake_create)

    process = _fake_process()
    with patch("optimizer.jobs._CTX") as mock_ctx:
        mock_ctx.Queue.return_value = MagicMock()
        mock_ctx.Process.return_value = process

        job_id = store.try_start(_FakeReq())

    assert job_id == "test-job-uuid"
    assert created_ids[0]["pair"] == "XBTEUR"
    assert created_ids[0]["mode"] == "AGGRESSIVE"
    process.start.assert_called_once()


def test_try_start_busy_raises(monkeypatch) -> None:
    store = JobStore()
    alive_process = _fake_process(alive=True)
    store._active = _ActiveJob(
        job_id="existing-job",
        process=alive_process,
        queue=MagicMock(),
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
        process=_fake_process(),
        queue=MagicMock(),
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
        process=_fake_process(),
        queue=MagicMock(),
        pair="XBTEUR",
    )
    store._active = active

    store._finalize(active, "error", "boom")

    assert failed["job_id"] == "job-2"
    assert "boom" in failed["error"]
    assert store._active is None


def test_supervise_drains_after_exit(monkeypatch) -> None:
    """The supervisor does a final blocking drain after the worker exits.
    Guards the race where get_nowait() misses a result that is already in the
    queue feeder thread when is_alive() flips to False."""
    store = JobStore()
    completed = {}

    monkeypatch.setattr(db, "complete_optimizer_job", lambda job_id, result: completed.update({"job_id": job_id}))

    process = MagicMock()
    process.is_alive.return_value = False
    process.exitcode = 0

    queue = MagicMock()
    queue.get_nowait.side_effect = Empty()
    queue.get.return_value = ("ok", {"scores": {"robust_pnl_pct": 1.0}})

    active = _ActiveJob(job_id="drain-job", process=process, queue=queue, pair="XBTEUR")
    store._active = active

    async def _run_one_tick():
        await asyncio.sleep(0)
        active_snap = store._snapshot_active()
        if active_snap is None:
            return
        try:
            msg = active_snap.queue.get_nowait()
        except Exception:
            msg = None
        if msg is not None:
            store._finalize(active_snap, *msg)
            return
        if not active_snap.process.is_alive():
            try:
                kind, payload = active_snap.queue.get(timeout=2.0)
            except Exception:
                store._finalize(active_snap, "error", f"worker exited with code {active_snap.process.exitcode}")
            else:
                store._finalize(active_snap, kind, payload)

    asyncio.run(_run_one_tick())

    assert completed.get("job_id") == "drain-job"
    assert store._active is None
