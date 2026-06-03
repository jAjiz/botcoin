import asyncio
import contextlib
import multiprocessing as mp
import threading
from dataclasses import dataclass

import core.database as db
import core.logging as logging
import core.runtime as runtime
from optimizer.worker import _entrypoint

_CTX = mp.get_context("spawn")


@dataclass
class _ActiveJob:
    job_id: str
    process: mp.Process
    queue: mp.Queue
    pair: str


class OptimizerBusyError(Exception):
    """Raised when a new submission arrives while an optimization is already running."""


class JobStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: _ActiveJob | None = None
        self._supervisor_task: asyncio.Task | None = None

    def try_start(self, req) -> str:
        """Atomically: confirm slot is free, INSERT optimizer_jobs row, spawn worker.
        Returns job_id. Raises OptimizerBusyError if another job is running.

        Snapshots the live calibration here (in the parent) and passes it to the
        worker, because the spawned child starts with an empty core.runtime and
        cannot read the cache itself. Sliced requests get None and the worker
        recomputes from the slice."""
        with self._lock:
            if self._active is not None and self._active.process.is_alive():
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
            queue = _CTX.Queue()
            process = _CTX.Process(target=_entrypoint, args=(req.__dict__, calibration, queue), daemon=True)
            process.start()
            self._active = _ActiveJob(job_id=job_id, process=process, queue=queue, pair=req.pair)
            return job_id

    async def supervise(self) -> None:
        """Single long-lived asyncio task. Blocks in a thread pool on the active
        job's result queue. On completion, persists result/error and clears the slot."""
        while True:
            active = self._snapshot_active()
            if active is None:
                await asyncio.sleep(1.0)
                continue
            try:
                kind, payload = await asyncio.to_thread(active.queue.get)
            except Exception as exc:
                self._finalize(active, "error", f"queue read failed: {exc}")
                continue
            self._finalize(active, kind, payload)

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
            with contextlib.suppress(Exception):
                active.process.join(timeout=5)
            with self._lock:
                self._active = None

    def shutdown(self) -> None:
        """Called from FastAPI lifespan finally block. Terminate any active child."""
        active = self._snapshot_active()
        if active is None:
            return
        try:
            active.process.terminate()
            active.process.join(timeout=5)
        except Exception:
            pass
        db.fail_optimizer_job(active.job_id, "interrupted by shutdown")
        with self._lock:
            self._active = None


JOB_STORE = JobStore()
