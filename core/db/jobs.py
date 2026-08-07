"""Optimizer job access (``optimizer_jobs``)."""

import logging as stdlib_logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, update

from core.db.models import OptimizerJob
from core.db.session import open_session

logger = stdlib_logging.getLogger(__name__)


def create_optimizer_job(pair: str, mode: str, split_method: str, request: dict[str, Any]) -> int:
    """Insert a new job row with status='running' and started_at=now(). Returns job_id."""
    try:
        with open_session() as session:
            row = OptimizerJob(
                pair=pair,
                mode=mode,
                split_method=split_method,
                status="running",
                request=request,
                started_at=datetime.now(UTC),
            )
            session.add(row)
            session.flush()
            return row.id
    except Exception as e:
        logger.error(f"Error creating optimizer job for {pair}: {e}")
        raise


def complete_optimizer_job(job_id: int, result: dict[str, Any]) -> None:
    try:
        with open_session() as session:
            session.execute(
                update(OptimizerJob)
                .where(OptimizerJob.id == job_id)
                .values(status="completed", result=result, finished_at=datetime.now(UTC))
            )
    except Exception as e:
        logger.error(f"Error completing optimizer job {job_id}: {e}")
        raise


def fail_optimizer_job(job_id: int, error: str) -> None:
    try:
        with open_session() as session:
            session.execute(
                update(OptimizerJob)
                .where(OptimizerJob.id == job_id)
                .values(status="failed", error=error, finished_at=datetime.now(UTC))
            )
    except Exception as e:
        logger.error(f"Error failing optimizer job {job_id}: {e}")
        raise


def get_optimizer_job(job_id: int) -> dict[str, Any] | None:
    try:
        with open_session() as session:
            record = session.query(OptimizerJob).filter(OptimizerJob.id == job_id).one_or_none()
            if record is None:
                return None
            return record.to_dict()
    except Exception as e:
        logger.error(f"Error loading optimizer job {job_id}: {e}")
        return None


def list_optimizer_jobs(limit: int = 20) -> list[dict[str, Any]]:
    try:
        with open_session() as session:
            records = session.query(OptimizerJob).order_by(desc(OptimizerJob.created_at)).limit(limit).all()
            return [r.to_dict() for r in records]
    except Exception as e:
        logger.error(f"Error listing optimizer jobs: {e}")
        return []


def cleanup_orphaned_optimizer_jobs() -> int:
    """Mark every status='running' row as failed with error='interrupted by restart',
    finished_at=now(). Return the row count."""
    try:
        with open_session() as session:
            result = session.execute(
                update(OptimizerJob)
                .where(OptimizerJob.status == "running")
                .values(status="failed", error="interrupted by restart", finished_at=datetime.now(UTC))
            )
            return result.rowcount
    except Exception as e:
        logger.error(f"Error cleaning up orphaned optimizer jobs: {e}")
        return 0
