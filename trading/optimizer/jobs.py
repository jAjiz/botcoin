import asyncio
import threading
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import asdict, dataclass
from multiprocessing import get_context

import core.database as db
import core.logging as logging
import core.runtime as runtime
from core.config import MAX_CONCURRENT_JOBS
from trading.optimizer.worker import _worker_func

_EXECUTOR = ProcessPoolExecutor(max_workers=max(MAX_CONCURRENT_JOBS, 1), mp_context=get_context("spawn"))


@dataclass
class _ActiveJob:
    job_id: int
    future: Future
    pair: str


class OptimizerBusyError(Exception):
    """Raised when a new submission arrives while all optimizer slots are busy."""


class JobStore:
    def __init__(self, max_concurrent: int = MAX_CONCURRENT_JOBS) -> None:
        self._lock = threading.Lock()
        self._active: dict[int, _ActiveJob] = {}
        self._max = max_concurrent

    def try_start(self, req) -> int:
        """Atomically: confirm a slot is free, INSERT optimizer_jobs row, submit worker.
        Returns job_id (int). Raises OptimizerBusyError if all slots are busy.

        Snapshots the live calibration here (in the parent) and passes it to the
        worker, because the spawned child starts with an empty core.runtime and
        cannot read the cache itself. Sliced requests get None and the worker
        recomputes from the slice."""
        with self._lock:
            if len(self._active) >= self._max:
                raise OptimizerBusyError(f"All {self._max} optimizer slot(s) are busy — try again later")
            calibration = None
            if not req.start and not req.end:
                calibration = runtime.get_pair_calibration(req.pair)
            # asdict (not __dict__) so a nested SearchSpace is fully dict-ified —
            # JSONB-serializable for the DB row and picklable for the worker.
            req_dict = asdict(req)
            job_id = db.create_optimizer_job(
                pair=req.pair,
                mode=req.mode,
                split_method="CONTINUE",
                request=req_dict,
            )
            logging.info(
                f"🔧 [Optimizer] Started for {req.pair} (job={job_id})\nMode: {req.mode}",
                to_telegram=True,
            )
            future = _EXECUTOR.submit(_worker_func, req_dict, calibration)
            self._active[job_id] = _ActiveJob(job_id=job_id, future=future, pair=req.pair)
            return job_id

    async def supervise(self, job_id: int) -> None:
        """Awaits a specific job's future and persists the result. Called once
        per job via asyncio.create_task() immediately after try_start()."""
        with self._lock:
            active = self._active.get(job_id)
        if active is None:
            return
        try:
            result = await asyncio.wrap_future(active.future)
            self._finalize(active, "ok", result)
        except Exception as exc:
            self._finalize(active, "error", str(exc))

    def _finalize(self, active: _ActiveJob, kind: str, payload) -> None:
        try:
            if kind == "ok":
                db.complete_optimizer_job(active.job_id, payload)
                if payload.get("mode") == "AUTO":
                    self._notify_auto(active, payload)
                else:
                    best = (payload.get("top_candidates") or [{}])[0]
                    robust = best.get("robust_pnl_pct")
                    pnl_str = f"{robust:.2f}%" if robust is not None else "n/a"
                    logging.info(
                        f"✅ [Optimizer] Completed for {active.pair} (job={active.job_id})\nBest pnl: {pnl_str}",
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
                self._active.pop(active.job_id, None)

    def _notify_auto(self, active: _ActiveJob, payload: dict) -> None:
        best = (payload.get("top_candidates") or [{}])[0]
        robust = best.get("robust_pnl_pct")
        robust_str = f"{robust:.2f}%" if robust is not None else "n/a"
        n_trials = payload.get("n_trials_run")
        n_agreed = payload.get("n_seeds_agreed", 0)
        n_seeds = len(payload.get("seeds_used") or [])
        env_lines = "\n".join(payload.get("suggested_env_lines") or [])

        if payload.get("converged"):
            msg = (
                f"✅ [AutoOptimize] {active.pair} (job={active.job_id}) — converged\n"
                f"{n_agreed}/{n_seeds} seeds, {n_trials} trials\n"
                f"Best robust: {robust_str}\n"
                f"{env_lines}"
            )
        else:
            msg = (
                f"⚠️ [AutoOptimize] {active.pair} (job={active.job_id}) — no convergence reached\n"
                f"Best found: {robust_str}\n"
                f"{env_lines}"
            )
        logging.info(msg, to_telegram=True)

    def shutdown(self) -> None:
        """Called from FastAPI lifespan finally block. Cancel pending work and
        mark any running jobs as failed in the DB; process cleanup is left to Docker."""
        with self._lock:
            active_jobs = list(self._active.values())
        if not active_jobs:
            return
        _EXECUTOR.shutdown(wait=False, cancel_futures=True)
        for active in active_jobs:
            db.fail_optimizer_job(active.job_id, "interrupted by shutdown")
        with self._lock:
            self._active.clear()


JOB_STORE = JobStore()
