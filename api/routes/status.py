from fastapi import APIRouter

import core.database as db
import core.runtime as runtime
from api.schemas import StatusResponse

router = APIRouter(tags=["status"])


@router.get("/status", response_model=StatusResponse)
def get_status() -> StatusResponse:
    return StatusResponse(
        paused=db.get_bot_paused(),
        last_run_at=runtime.get_last_run_at(),
    )
