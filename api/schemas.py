from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class MarketItem(BaseModel):
    pair: str
    last_price: Optional[float] = None
    atr: Optional[float] = None
    volatility_level: Optional[str] = None


class PositionDetail(BaseModel):
    side: str
    volume: float
    entry_price: float
    activation_price: float
    trailing_price: Optional[float] = None
    stop_price: Optional[float] = None
    estimated_pnl_percent: Optional[float] = None


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
