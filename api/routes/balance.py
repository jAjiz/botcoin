from fastapi import APIRouter

import core.runtime as runtime
from api.schemas import BalanceResponse

router = APIRouter(tags=["balance"])


@router.get("/balance", response_model=BalanceResponse)
def get_balance() -> BalanceResponse:
    return BalanceResponse(balance=runtime.get_last_balance())
