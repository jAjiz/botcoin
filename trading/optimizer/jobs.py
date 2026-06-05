import asyncio
import threading
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import dataclass
from multiprocessing import get_context

import core.database as db
import core.logging as logging
import core.runtime as runtime
from trading.optimizer.worker import _worker_func

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
                split_method="CONTINUE",
                request=req.__dict__,
            )
            logging.info(
                f"🔧 [Optimizer] Started for {req.pair} (mode={req.mode}, job={job_id})",
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
                if payload.get("mode") == "AUTO":
                    self._notify_auto(active, payload)
                else:
                    best = (payload.get("top_candidates") or [{}])[0]
                    robust = best.get("robust_pnl_pct")
                    pnl_str = f"{robust:.2f}%" if robust is not None else "n/a"
                    logging.info(
                        f"✅ [Optimizer] Completed for {active.pair} (job={active.job_id}). Best: pnl={pnl_str}",
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

    def _notify_auto(self, active: _ActiveJob, payload: dict) -> None:
        best = (payload.get("top_candidates") or [{}])[0]
        robust = best.get("robust_pnl_pct")
        robust_str = f"{robust:.2f}%" if robust is not None else "n/a"
        n_conv = payload.get("n_trials_at_convergence")
        n_agreed = payload.get("n_seeds_agreed", 0)
        n_seeds = len(payload.get("seeds_used") or [])
        env_lines = "\n".join(payload.get("suggested_env_lines") or [])

        if payload.get("converged"):
            current_robust = payload.get("current_robust_pnl")
            current_str = f"{current_robust:.2f}%" if current_robust is not None else "n/a"
            if payload.get("is_improvement"):
                msg = (
                    f"🚀 [AutoOptimize] {active.pair} converged "
                    f"({n_agreed}/{n_seeds} seeds, {n_conv} trials) — improvement found\n"
                    f"Current robust: {current_str} → New: {robust_str}\n"
                    f"{env_lines}"
                )
            else:
                msg = (
                    f"ℹ️ [AutoOptimize] {active.pair} converged "  # noqa: RUF001 (intentional info emoji)
                    f"({n_agreed}/{n_seeds} seeds, {n_conv} trials) — current is better\n"
                    f"{current_str} (current) vs {robust_str} (found) — no change needed"
                )
        else:
            msg = f"⚠️ [AutoOptimize] {active.pair} — no convergence reached\nBest found: {robust_str}"
        logging.info(msg, to_telegram=True)

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
