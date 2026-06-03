import asyncio
import threading
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import dataclass
from multiprocessing import get_context

import core.database as db
import core.logging as logging
import core.runtime as runtime
from optimizer.worker import _worker_func

_EXECUTOR = ProcessPoolExecutor(max_workers=1, mp_context=get_context("spawn"))


@dataclass
class _ActiveJob:
    job_id: str
    future: Future
    pair: str


class OptimizerBusyError(Exception):
    """Raised when a new submission arrives while an optimization is already running."""


class JobStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: _ActiveJob | None = None

    def try_start(self, req) -> str:
        """Atomically: confirm slot is free, INSERT optimizer_jobs row, submit worker.
        Returns job_id. Raises OptimizerBusyError if another job is running.

        Snapshots the live calibration here (in the parent) and passes it to the
        worker, because the spawned child starts with an empty core.runtime and
        cannot read the cache itself. Sliced requests get None and the worker
        recomputes from the slice."""
        with self._lock:
            if self._active is not None and not self._active.future.done():
                raise OptimizerBusyError(f"Optimizer job {self._active.job_id} is already running")
            calibration = None
            if not req.start and not req.end:
                calibration = runtime.get_pair_calibration(req.pair)
            job_id = db.create_optimizer_job(
                pair=req.pair,
                mode=req.mode,
                split_method=req.split_method,
                request=req.__dict__,
            )
            logging.info(
                f"🔧 [Optimizer] Started for {req.pair} (mode={req.mode}, split={req.split_method}, job={job_id})",
                to_telegram=True,
            )
            future = _EXECUTOR.submit(_worker_func, req.__dict__, calibration)
            self._active = _ActiveJob(job_id=job_id, future=future, pair=req.pair)
            return job_id

    async def supervise(self) -> None:
        """Awaits the active job's future and persists the result. Called once
        per job via asyncio.create_task() immediately after try_start()."""
        active = self._snapshot_active()
        if active is None:
            return
        try:
            result = await asyncio.wrap_future(active.future)
            self._finalize(active, "ok", result)
        except Exception as exc:
            self._finalize(active, "error", str(exc))

    def _snapshot_active(self) -> _ActiveJob | None:
        with self._lock:
            return self._active

    def _finalize(self, active: _ActiveJob, kind: str, payload) -> None:
        try:
            if kind == "ok":
                db.complete_optimizer_job(active.job_id, payload)
                logging.info(
                    f"✅ [Optimizer] Completed for {active.pair} (job={active.job_id}). "
                    f"Best: pnl={payload['scores'].get('robust_pnl_pct', 0):.2f}%",
                    to_telegram=True,
                )
            else:
                db.fail_optimizer_job(active.job_id, str(payload))
                logging.error(
                    f"❌ [Optimizer] Failed for {active.pair} (job={active.job_id})",
                    to_telegram=True,
                )
        finally:
            with self._lock:
                self._active = None

    def shutdown(self) -> None:
        """Called from FastAPI lifespan finally block. Cancel pending work and
        mark any running job as failed in the DB; process cleanup is left to Docker."""
        active = self._snapshot_active()
        if active is None:
            return
        _EXECUTOR.shutdown(wait=False, cancel_futures=True)
        db.fail_optimizer_job(active.job_id, "interrupted by shutdown")
        with self._lock:
            self._active = None


JOB_STORE = JobStore()
