from typing import Any

from fastapi import APIRouter, HTTPException

import core.database as db
from api.schemas import PositionDetail, PositionResponse
from core.config import PAIRS
from core.utils import round_price, round_volume

router = APIRouter(tags=["positions"])

# Price-scale fields rounded to the pair's precision for display. ATR fields
# (activation_atr, stop_atr) are deliberately excluded — they drive ATR-drift
# detection and must stay full precision.
_PRICE_FIELDS = ("entry_price", "activation_price", "stop_price", "trailing_price", "closing_price")


def _round_field(pair: str, key: str, value: Any) -> Any:
    if key in _PRICE_FIELDS:
        return round_price(pair, value)
    if key == "volume":
        return round_volume(pair, value)
    return value


def _build_position_detail(pair: str) -> PositionDetail | None:
    pos = db.load_trailing_state(pair)
    if pos is None:
        return None
    rounded = {k: _round_field(pair, k, v) for k, v in pos.items()}
    return PositionDetail(**rounded)


@router.get("/positions", response_model=dict[str, PositionDetail | None])
def get_positions() -> dict[str, PositionDetail | None]:
    return {pair: _build_position_detail(pair) for pair in PAIRS}


@router.get("/positions/{pair}", response_model=PositionResponse)
def get_position_pair(pair: str) -> PositionResponse:
    if pair not in PAIRS:
        raise HTTPException(status_code=404, detail=f"Unknown pair: {pair}")
    return PositionResponse(pair=pair, position=_build_position_detail(pair))
