"""On-startup orphan cleanup for the optimizer_jobs table."""

import core.database as db
import core.logging as logging


def cleanup_orphaned_jobs() -> None:
    """Mark any jobs stuck in running/queued state as failed.

    A job can be stuck if the process was killed mid-run (e.g. container
    restart).  Called during FastAPI lifespan startup before the scheduler
    begins.
    """
    count = db.mark_orphaned_optimizer_jobs_failed()
    if count > 0:
        logging.warning(f"Marked {count} orphaned optimizer job(s) as failed on startup")
