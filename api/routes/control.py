from fastapi import APIRouter, Body

import core.database as db
from api.schemas import ControlRequest, ControlResponse

router = APIRouter(tags=["control"])


@router.post("/control/pause", response_model=ControlResponse)
def pause(req: ControlRequest | None = Body(default=None)) -> ControlResponse:
    updated_by = req.updated_by if req else None
    if not db.get_bot_paused():
        db.set_bot_paused(True, updated_by=updated_by)
    return ControlResponse(paused=True, updated_by=updated_by)


@router.post("/control/resume", response_model=ControlResponse)
def resume(req: ControlRequest | None = Body(default=None)) -> ControlResponse:
    updated_by = req.updated_by if req else None
    if db.get_bot_paused():
        db.set_bot_paused(False, updated_by=updated_by)
    return ControlResponse(paused=False, updated_by=updated_by)
