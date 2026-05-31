"""
Backtest runner — exposes run_backtest() for use by the POST /backtest endpoint.

The script no longer has a CLI entry point. The simulation engine has been
extracted to trading/engine.py so the optimizer can run candidate configs
without touching global state.
"""

from dataclasses import dataclass

import numpy as np

import core.database as db
from core.config import ATR_DESV_LIMIT, CANDLE_TIMEFRAME, PAIRS, TRADING_PARAMS
from trading.engine import EngineConfig, PairCalibration
from trading.engine import simulate_operations as _simulate
from trading.parameters_manager import calculate_trading_parameters


@dataclass
class BacktestRequest:
    pair: str
    fee_pct: float = 0.0  # e.g. 0.26 == 0.26 %
    start: str | None = None  # "YYYY-MM-DD" inclusive lower bound
    end: str | None = None  # "YYYY-MM-DD" inclusive upper bound
    max_ops: int | None = None


@dataclass
class BacktestResult:
    pair: str
    fee_pct: float
    total_ops: int
    pnl_samples: int
    win_rate_pct: float | None
    total_pnl_abs: float | None
    avg_pnl_abs: float | None
    median_pnl_abs: float | None
    best_pnl_abs: float | None
    worst_pnl_abs: float | None
    total_fees_abs: float
    cum_pnl_pct: float | None
    operations: list


def _build_calibration(pair: str) -> PairCalibration:
    p = PAIRS[pair]
    tp = TRADING_PARAMS[pair]
    k_act_sell = tp["sell"].get("K_ACT")
    k_act_buy = tp["buy"].get("K_ACT")
    return PairCalibration(
        atr_20pct=float(p.get("atr_20pct") or 0),
        atr_50pct=float(p.get("atr_50pct") or 0),
        atr_80pct=float(p.get("atr_80pct") or 0),
        atr_95pct=float(p.get("atr_95pct") or 0),
        sell_k_stops=tp["sell"].get("K_STOP") or {},
        buy_k_stops=tp["buy"].get("K_STOP") or {},
        k_act_sell=float(k_act_sell) if k_act_sell is not None else None,
        k_act_buy=float(k_act_buy) if k_act_buy is not None else None,
        min_margin_sell=float(tp["sell"].get("MIN_MARGIN") or 0),
        min_margin_buy=float(tp["buy"].get("MIN_MARGIN") or 0),
        atr_desv_limit=ATR_DESV_LIMIT,
    )


def run_backtest(req: BacktestRequest) -> BacktestResult:
    """Run a backtest simulation against stored OHLC data.

    Reads K_STOP / K_ACT parameters from in-memory TRADING_PARAMS (set by the
    scheduler).  If they are missing, calculates them fresh from the DB first.
    """
    pair = req.pair

    if not PAIRS.get(pair, {}).get("atr_20pct"):
        calculate_trading_parameters(pair, infoLog=False)

    cal = _build_calibration(pair)
    cfg = EngineConfig(fee_rate=req.fee_pct / 100.0, max_ops=req.max_ops)

    df = db.load_ohlc_data(pair, CANDLE_TIMEFRAME).dropna(subset=["atr"])
    if req.start:
        df = df[df["dtime"] >= req.start]
    if req.end:
        df = df[df["dtime"] <= req.end]
    df = df.reset_index(drop=True)

    ops = _simulate(df, cal, cfg)

    pnl_values = [op.pnl_abs for op in ops if op.pnl_abs is not None]
    total_fees = sum(op.fee_abs for op in ops if op.fee_abs is not None)

    if pnl_values:
        arr = np.array(pnl_values, dtype=float)
        win_rate: float | None = float(np.mean(arr > 0) * 100.0)
        total_pnl: float | None = float(arr.sum())
        avg_pnl: float | None = float(arr.mean())
        median_pnl: float | None = float(np.median(arr))
        best_pnl: float | None = float(arr.max())
        worst_pnl: float | None = float(arr.min())
    else:
        win_rate = total_pnl = avg_pnl = median_pnl = best_pnl = worst_pnl = None

    cum_pnl = float(ops[-1].cum_pnl) if ops and ops[-1].cum_pnl is not None else None

    operations = [
        {
            "idx": op.idx,
            "time": op.time,
            "side": op.side,
            "price": op.price,
            "vol": op.vol,
            "k_stop": op.k_stop,
            "fee_abs": op.fee_abs,
            "pnl_abs": op.pnl_abs,
            "pnl_pct": op.pnl_pct,
            "cum_pnl": op.cum_pnl,
        }
        for op in ops
    ]

    return BacktestResult(
        pair=pair,
        fee_pct=req.fee_pct,
        total_ops=len(ops),
        pnl_samples=len(pnl_values),
        win_rate_pct=win_rate,
        total_pnl_abs=total_pnl,
        avg_pnl_abs=avg_pnl,
        median_pnl_abs=median_pnl,
        best_pnl_abs=best_pnl,
        worst_pnl_abs=worst_pnl,
        total_fees_abs=float(total_fees),
        cum_pnl_pct=cum_pnl,
        operations=operations,
    )
