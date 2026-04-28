from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class MarketItem(BaseModel):
    pair: str
    base_asset: Optional[str] = None
    last_price: Optional[float] = None
    atr: Optional[float] = None
    volatility_level: Optional[str] = None


class PositionDetail(BaseModel):
    side: str
    volume: float
    entry_price: float
    activation_atr: float
    activation_price: float
    created_at: datetime
    activated_at: Optional[datetime] = None
    trailing_price: Optional[float] = None
    stop_price: Optional[float] = None
    stop_atr: Optional[float] = None
    closing_order_id: Optional[str] = None
    closing_price: Optional[float] = None
    closing_requested_at: Optional[datetime] = None


class PositionResponse(BaseModel):
    pair: str
    position: Optional[PositionDetail] = None


class BalanceResponse(BaseModel):
    balance: dict[str, float]


class StatusResponse(BaseModel):
    paused: bool
    last_run_at: Optional[datetime] = None


class ControlRequest(BaseModel):
    updated_by: Optional[str] = None


class ControlResponse(BaseModel):
    paused: bool
    updated_by: Optional[str] = None
