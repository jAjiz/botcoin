
from fastapi import APIRouter, HTTPException

import core.database as db
from api.schemas import PositionDetail, PositionResponse
from core.config import PAIRS

router = APIRouter(tags=["positions"])


def _build_position_detail(pair: str) -> PositionDetail | None:
    pos = db.load_trailing_state(pair)
    if pos is None:
        return None
    return PositionDetail(**pos)


@router.get("/positions", response_model=dict[str, PositionDetail | None])
def get_positions() -> dict[str, PositionDetail | None]:
    return {pair: _build_position_detail(pair) for pair in PAIRS}


@router.get("/positions/{pair}", response_model=PositionResponse)
def get_position_pair(pair: str) -> PositionResponse:
    if pair not in PAIRS:
        raise HTTPException(status_code=404, detail=f"Unknown pair: {pair}")
    return PositionResponse(pair=pair, position=_build_position_detail(pair))
