"""Single-slot in-memory lock for the optimizer worker.

Only one optimizer job may run at a time.  The slot is represented by
``_current_job_id``.  A monitor thread clears it automatically when the
worker subprocess finishes.
"""

import multiprocessing
import threading

_lock = threading.Lock()
_current_job_id: str | None = None


def is_busy() -> bool:
    """Return True if an optimizer job is currently running."""
    with _lock:
        return _current_job_id is not None


def get_current_job_id() -> str | None:
    """Return the job id of the currently running optimizer job, or None."""
    with _lock:
        return _current_job_id


def try_start_job(job_id: str, request_dict: dict) -> bool:
    """Try to acquire the slot and start the optimizer worker subprocess.

    Returns True if the worker was started, False if the slot is already busy.
    The slot is released automatically when the worker process finishes.
    """
    global _current_job_id

    with _lock:
        if _current_job_id is not None:
            return False
        _current_job_id = job_id

    from optimizer.worker import worker_entry

    ctx = multiprocessing.get_context("spawn")
    proc = ctx.Process(target=worker_entry, args=(job_id, request_dict), daemon=True)
    proc.start()

    def _monitor() -> None:
        global _current_job_id
        proc.join()
        with _lock:
            _current_job_id = None

    t = threading.Thread(target=_monitor, daemon=True)
    t.start()
    return True
