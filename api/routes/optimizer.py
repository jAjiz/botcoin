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


def _row_to_response(row: dict) -> OptimizerJobStatusResponse:
    # pair/mode now live inside the echoed `request`; split_method is CONTINUE-only
    # and no longer surfaced. The typed request/result models order + group the rest.
    return OptimizerJobStatusResponse(job_id=row["id"], **{k: v for k, v in row.items() if k not in _HIDDEN_ROW_KEYS})


@router.post("/jobs", response_model=OptimizerJobAcceptedResponse, status_code=202)
async def submit(req: OptimizerRequest) -> OptimizerJobAcceptedResponse:
    if MAX_CONCURRENT_JOBS <= 0:
        raise HTTPException(status_code=503, detail="Optimizer is disabled on this host (MAX_CONCURRENT_JOBS=0)")
    if req.pair not in PAIRS:
        raise HTTPException(status_code=400, detail=f"Unknown pair: {req.pair}")
    # search_space is required for the search modes (enforced here, not on the model,
    # so the same model can echo historical requests back without re-failing).
    if req.mode in ("OPTIMIZE", "AUTO") and req.search_space is None:
        raise HTTPException(status_code=422, detail="search_space is required for OPTIMIZE and AUTO modes")
    try:
        job_id = JOB_STORE.try_start(DTORequest(**req.model_dump()))
    except OptimizerBusyError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    asyncio.create_task(JOB_STORE.supervise(job_id))  # noqa: RUF006
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
