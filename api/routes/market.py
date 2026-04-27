from fastapi import APIRouter, HTTPException

import core.runtime as runtime
from api.schemas import MarketItem
from core.config import PAIRS

router = APIRouter(tags=["market"])


def _build_market_item(pair: str) -> MarketItem:
    data = runtime.get_pair_data(pair)
    return MarketItem(
        pair=pair,
        last_price=data.get("last_price"),
        atr=data.get("atr"),
        volatility_level=data.get("volatility_level"),
    )


@router.get("/market", response_model=list[MarketItem])
def get_market() -> list[MarketItem]:
    return [_build_market_item(pair) for pair in PAIRS]


@router.get("/market/{pair}", response_model=MarketItem)
def get_market_pair(pair: str) -> MarketItem:
    if pair not in PAIRS:
        raise HTTPException(status_code=404, detail=f"Unknown pair: {pair}")
    return _build_market_item(pair)
