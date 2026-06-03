from fastapi import APIRouter, HTTPException

from api.schemas import BacktestRequest, BacktestResponse, OperationDTO
from core.config import PAIRS
from trading.backtest import BacktestRequest as DTORequest
from trading.backtest import run_backtest

router = APIRouter(tags=["backtest"])


@router.post("/backtest", response_model=BacktestResponse)
def post_backtest(req: BacktestRequest) -> BacktestResponse:
    if req.pair not in PAIRS:
        raise HTTPException(status_code=400, detail=f"Unknown pair: {req.pair}")
    result = run_backtest(DTORequest(**req.model_dump()))
    return BacktestResponse(
        pair=result.pair,
        fee_pct=result.fee_pct,
        summary=result.summary,
        operations=[OperationDTO(**op.__dict__) for op in result.operations],
    )
