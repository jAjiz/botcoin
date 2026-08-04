import asyncio

from fastapi import APIRouter, HTTPException, Query

import core.database as db
from api.schemas import (
    OptimizerJobAcceptedResponse,
    OptimizerJobStatusResponse,
    OptimizerRequest,
)
from core.config import MAX_CONCURRENT_JOBS, PAIRS
from trading.optimizer.jobs import JOB_STORE, OptimizerBusyError
from trading.optimizer.search import OptimizerRequest as DTORequest

router = APIRouter(prefix="/optimizer", tags=["optimizer"])


_HIDDEN_ROW_KEYS = ("id", "pair", "mode", "split_method")

# Retains supervise() tasks so they can't be garbage-collected mid-flight:
# asyncio only holds a weak reference to a task created via create_task.
_supervise_tasks: set[asyncio.Task] = set()


def _row_to_response(row: dict) -> OptimizerJobStatusResponse:
    # pair/mode are echoed inside `request`; split_method is CONTINUE-only.
    return OptimizerJobStatusResponse(job_id=row["id"], **{k: v for k, v in row.items() if k not in _HIDDEN_ROW_KEYS})


@router.post("/jobs", response_model=OptimizerJobAcceptedResponse, status_code=202)
async def submit(req: OptimizerRequest) -> OptimizerJobAcceptedResponse:
    if MAX_CONCURRENT_JOBS <= 0:
        raise HTTPException(status_code=503, detail="Optimizer is disabled on this host (MAX_CONCURRENT_JOBS=0)")
    if req.pair not in PAIRS:
        raise HTTPException(status_code=400, detail=f"Unknown pair: {req.pair}")
    # Enforced here, not on the model, so the model can still echo historical requests
    # back without re-failing.
    if req.mode in ("OPTIMIZE", "AUTO") and req.search_space is None:
        raise HTTPException(status_code=422, detail="search_space is required for OPTIMIZE and AUTO modes")
    try:
        # try_start does a synchronous DB insert + a synchronous Telegram HTTP call
        # (2s timeout) + a process-pool submit — keep all of that off the event loop.
        job_id = await asyncio.to_thread(JOB_STORE.try_start, DTORequest(**req.model_dump()))
    except OptimizerBusyError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    task = asyncio.create_task(JOB_STORE.supervise(job_id))
    _supervise_tasks.add(task)
    task.add_done_callback(_supervise_tasks.discard)
    return OptimizerJobAcceptedResponse(job_id=job_id)


@router.get("/jobs/{job_id}", response_model=OptimizerJobStatusResponse)
def get_job(job_id: int) -> OptimizerJobStatusResponse:
    row = db.get_optimizer_job(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    return _row_to_response(row)


@router.get("/jobs", response_model=list[OptimizerJobStatusResponse])
def list_jobs(limit: int = Query(default=20, ge=1, le=100)) -> list[OptimizerJobStatusResponse]:
    return [_row_to_response(row) for row in db.list_optimizer_jobs(limit=limit)]
