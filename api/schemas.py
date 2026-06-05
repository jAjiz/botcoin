from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


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


class BacktestRequest(BaseModel):
    pair: str
    fee_pct: float = 0.0
    start: str | None = None
    end: str | None = None
    max_ops: int | None = None
    use_live_config: bool = False


class OperationDTO(BaseModel):
    idx: int
    time: str
    side: str
    price: float
    vol: str
    k_stop: float
    fee_abs: float
    pnl_abs: float | None
    pnl_pct: float | None
    cum_pnl: float | None


class BacktestResponse(BaseModel):
    pair: str
    fee_pct: float
    summary: dict
    operations: list[OperationDTO]


class OptimizerRequest(BaseModel):
    pair: str
    mode: Literal["OPTIMIZE", "CURRENT", "AUTO"]
    fee_pct: float = 0.0
    start: str | None = None
    end: str | None = None
    train_split: float = Field(default=0.8, ge=0.5, le=1.0)
    min_ops: int = 0
    min_test_ops: int = 0
    n_trials: int = Field(default=1_000, ge=1, le=10_000)
    seed: int = 42
    # AUTO mode params (ignored for OPTIMIZE/CURRENT)
    n_seeds: int = Field(default=4, ge=2, le=8)
    min_agree: int = Field(default=3, ge=2, le=8)
    trial_step: int = Field(default=500, ge=100, le=2_000)
    max_trials: int = Field(default=9_000, ge=500, le=20_000)


class OptimizerJobAcceptedResponse(BaseModel):
    job_id: str
    status: Literal["running"] = "running"


class OptimizerJobStatusResponse(BaseModel):
    job_id: str
    pair: str
    mode: str
    split_method: str
    status: Literal["running", "completed", "failed"]
    request: dict
    result: dict | None = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
