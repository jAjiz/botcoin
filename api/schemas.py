from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class MarketItem(BaseModel):
    pair: str
    base_asset: str | None = None
    last_price: float | None = None
    atr: float | None = None
    volatility_level: str | None = None


class PositionDetail(BaseModel):
    side: str
    volume: float
    entry_price: float
    activation_atr: float
    activation_price: float
    created_at: datetime
    activated_at: datetime | None = None
    trailing_price: float | None = None
    stop_price: float | None = None
    stop_atr: float | None = None
    closing_order_id: str | None = None
    closing_price: float | None = None
    closing_requested_at: datetime | None = None


class PositionResponse(BaseModel):
    pair: str
    position: PositionDetail | None = None


class BalanceResponse(BaseModel):
    balance: dict[str, float]


class StatusResponse(BaseModel):
    paused: bool
    last_run_at: datetime | None = None


class ControlRequest(BaseModel):
    updated_by: str | None = None


class ControlResponse(BaseModel):
    paused: bool
    updated_by: str | None = None
