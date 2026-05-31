"""POST /backtest — synchronous backtest endpoint."""

import dataclasses

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from trading.backtest import BacktestRequest, run_backtest

router = APIRouter(prefix="/backtest", tags=["backtest"])


class BacktestBody(BaseModel):
    pair: str
    fee_pct: float = 0.0
    start: str | None = None
    end: str | None = None
    max_ops: int | None = None


@router.post("")
def post_backtest(body: BacktestBody):
    try:
        result = run_backtest(
            BacktestRequest(
                pair=body.pair,
                fee_pct=body.fee_pct,
                start=body.start,
                end=body.end,
                max_ops=body.max_ops,
            )
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=f"Unknown pair: {e}") from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return dataclasses.asdict(result)
