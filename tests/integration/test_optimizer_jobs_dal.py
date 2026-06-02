import os

import pytest

import core.database as db


def _db_enabled() -> bool:
    return os.getenv("RUN_DB_INTEGRATION", "false").lower() == "true"


_SKIP = pytest.mark.skipif(not _db_enabled(), reason="Set RUN_DB_INTEGRATION=true to run")

_REQ = {"pair": "XBTEUR", "mode": "AGGRESSIVE", "n_trials": 10}


@pytest.mark.integration
@_SKIP
def test_create_then_complete() -> None:
    job_id = db.create_optimizer_job("XBTEUR", "AGGRESSIVE", "RESET", _REQ)
    assert job_id

    row = db.get_optimizer_job(job_id)
    assert row is not None
    assert row["status"] == "running"
    assert row["started_at"] is not None

    db.complete_optimizer_job(job_id, {"robust_pnl_pct": 5.0})

    row = db.get_optimizer_job(job_id)
    assert row["status"] == "completed"
    assert row["result"]["robust_pnl_pct"] == 5.0
    assert row["finished_at"] is not None


@pytest.mark.integration
@_SKIP
def test_create_then_fail() -> None:
    job_id = db.create_optimizer_job("XBTEUR", "CURRENT", "RESET", _REQ)

    db.fail_optimizer_job(job_id, "something went wrong")

    row = db.get_optimizer_job(job_id)
    assert row["status"] == "failed"
    assert "something went wrong" in row["error"]
    assert row["finished_at"] is not None


@pytest.mark.integration
@_SKIP
def test_cleanup_orphaned() -> None:
    id1 = db.create_optimizer_job("XBTEUR", "AGGRESSIVE", "RESET", _REQ)
    id2 = db.create_optimizer_job("XBTEUR", "CURRENT", "RESET", _REQ)

    cleaned = db.cleanup_orphaned_optimizer_jobs()
    assert cleaned >= 2

    for job_id in (id1, id2):
        row = db.get_optimizer_job(job_id)
        assert row["status"] == "failed"
        assert row["error"] == "interrupted by restart"
        assert row["finished_at"] is not None
