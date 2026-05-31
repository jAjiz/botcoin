"""Optimizer job endpoints.

POST /optimizer/jobs  — submit a job (202) or reject if busy (409)
GET  /optimizer/jobs  — list all jobs
GET  /optimizer/jobs/{id} — fetch a single job
"""

import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

import core.database as db
from optimizer.job_store import is_busy, try_start_job

router = APIRouter(prefix="/optimizer/jobs", tags=["optimizer"])


class OptimizerJobBody(BaseModel):
    pair: str
    mode: str
    fee_pct: float = 0.0
    start: str | None = None
    end: str | None = None
    train_split: float = 1.0
    split_method: str = "RESET"
    min_ops: int = 0
    min_test_ops: int = 0
    n_trials: int = 200

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, v: str) -> str:
        if v not in ("CONSERVATIVE", "AGGRESSIVE"):
            raise ValueError("mode must be CONSERVATIVE or AGGRESSIVE")
        return v

    @field_validator("split_method")
    @classmethod
    def _validate_split_method(cls, v: str) -> str:
        if v not in ("RESET", "CONTINUE", "BOTH"):
            raise ValueError("split_method must be RESET, CONTINUE, or BOTH")
        return v


@router.post("", status_code=202)
def submit_optimizer_job(body: OptimizerJobBody):
    if is_busy():
        raise HTTPException(status_code=409, detail="An optimizer job is already running")

    job_id = str(uuid.uuid4())
    request_dict = body.model_dump()

    db.create_optimizer_job(
        job_id=job_id,
        pair=body.pair,
        mode=body.mode,
        request_json=json.dumps(request_dict),
    )

    started = try_start_job(job_id, request_dict)
    if not started:
        # Lost a race with a concurrent request
        db.update_optimizer_job(
            job_id,
            status="failed",
            ended_at=datetime.now(UTC),
            error="Slot acquired by concurrent request",
        )
        raise HTTPException(status_code=409, detail="An optimizer job is already running")

    return {"job_id": job_id}


@router.get("")
def list_optimizer_jobs():
    jobs = db.list_optimizer_jobs()
    return [_serialize(j) for j in jobs]


@router.get("/{job_id}")
def get_optimizer_job(job_id: str):
    job = db.get_optimizer_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _serialize(job)


def _serialize(job: dict) -> dict:
    out = dict(job)
    result_json = out.pop("result_json", None)
    if result_json:
        try:
            out["result"] = json.loads(result_json)
        except Exception:
            out["result"] = None
    else:
        out["result"] = None
    return out
