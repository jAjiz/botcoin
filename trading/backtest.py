"""Backtest library entry point.

Pure ``run_backtest(req) -> BacktestResult``: no CLI, no prints, no global
mutation. Configuration for the simulation is built into an ``EngineConfig`` and
handed to ``trading.engine.simulate_operations``.
"""

from dataclasses import dataclass

import numpy as np

import core.database as db
import core.runtime as runtime
from core.config import ATR_DESV_LIMIT, CANDLE_TIMEFRAME, TRADING_PARAMS
from trading.engine import EngineConfig, Operation, PairCalibration, SidePolicy, simulate_operations
from trading.market_analyzer import analyze_structural_noise
from trading.parameters_manager import calculate_k_stops


@dataclass(frozen=True)
class BacktestRequest:
    pair: str
    fee_pct: float = 0.0
    start: str | None = None
    end: str | None = None
    max_ops: int | None = None
    use_live_config: bool = False  # if True, read events + ATR percentiles from the calibration cache; skip recompute


@dataclass(frozen=True)
class BacktestResult:
    pair: str
    fee_pct: float
    summary: dict  # {ops_count, pnl_samples, win_rate_pct, total_pnl_eur, total_fees_eur,
    #  best_op_pnl_eur, worst_op_pnl_eur, avg_op_pnl_eur, median_op_pnl_eur,
    #  row_count, source: "cache" | "recompute" | "slice"}
    operations: list[Operation]


def _coerce_float(v) -> float | None:
    try:
        return float(v) if v is not None and str(v).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _atr_percentiles(frame) -> tuple[float, float, float, float]:
    atr = frame["atr"].to_numpy(dtype=float)
    return tuple(float(np.percentile(atr, p)) for p in (20, 50, 80, 95))


def _build_summary(ops: list[Operation], row_count: int, source: str) -> dict:
    # All pnl_abs values (including the initial entry) for the correct net total.
    all_pnl = [op.pnl_abs for op in ops if op.pnl_abs is not None]
    # Round-trip trades only (skip idx=1, the initial market entry) for per-trade stats.
    trade_pnl = [op.pnl_abs for op in ops if op.pnl_abs is not None and op.idx != 1]
    total_fees = float(sum(op.fee_abs for op in ops if op.fee_abs is not None))
    total_pnl = float(sum(all_pnl)) if all_pnl else 0.0
    total_pnl_pct = float(ops[-1].cum_pnl) if ops and ops[-1].cum_pnl is not None else 0.0

    if trade_pnl:
        pnl = np.array(trade_pnl, dtype=float)
        win_rate = float(np.mean(pnl > 0) * 100.0)
        best = float(pnl.max())
        worst = float(pnl.min())
        avg = float(pnl.mean())
        median = float(np.median(pnl))
    else:
        win_rate = best = worst = avg = median = 0.0

    return {
        "ops_count": len(ops),
        "pnl_samples": len(trade_pnl),
        "win_rate_pct": win_rate,
        "total_pnl_eur": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "total_fees_eur": total_fees,
        "best_op_pnl_eur": best,
        "worst_op_pnl_eur": worst,
        "avg_op_pnl_eur": avg,
        "median_op_pnl_eur": median,
        "row_count": row_count,
        "source": source,
    }


def run_backtest(req: BacktestRequest) -> BacktestResult:
    df_full = (
        db.load_ohlc_data(req.pair, CANDLE_TIMEFRAME).dropna(subset=["atr"]).sort_values("time").reset_index(drop=True)
    )

    if req.start or req.end:
        # Date-sliced request: recompute events + ATR percentiles from the slice.
        source = "slice"
        df = df_full
        if req.start:
            df = df[df["dtime"] >= req.start]
        if req.end:
            df = df[df["dtime"] <= req.end]
        df = df.reset_index(drop=True)
        up_events, down_events = analyze_structural_noise(df)
        atr_p20, atr_p50, atr_p80, atr_p95 = _atr_percentiles(df)
    else:
        cached = runtime.get_pair_calibration(req.pair) if req.use_live_config else None
        if cached is not None:
            # Reuse the live bot's calibration (full history) — no recompute.
            source = "cache"
            df = df_full
            up_events = cached["up_events"]
            down_events = cached["down_events"]
            atr_p20 = cached["atr_p20"]
            atr_p50 = cached["atr_p50"]
            atr_p80 = cached["atr_p80"]
            atr_p95 = cached["atr_p95"]
        else:
            # Recompute from full history (cold cache or use_live_config=False).
            source = "recompute"
            df = df_full
            up_events, down_events = analyze_structural_noise(df_full)
            atr_p20, atr_p50, atr_p80, atr_p95 = _atr_percentiles(df_full)

    calibration = PairCalibration(
        atr_p20=atr_p20,
        atr_p50=atr_p50,
        atr_p80=atr_p80,
        atr_p95=atr_p95,
        k_stop_buy=calculate_k_stops(req.pair, down_events),
        k_stop_sell=calculate_k_stops(req.pair, up_events),
    )

    side_buy = SidePolicy(
        k_act=_coerce_float(TRADING_PARAMS[req.pair]["buy"].get("K_ACT")),
        min_margin=float(TRADING_PARAMS[req.pair]["buy"].get("MIN_MARGIN") or 0.0),
    )
    side_sell = SidePolicy(
        k_act=_coerce_float(TRADING_PARAMS[req.pair]["sell"].get("K_ACT")),
        min_margin=float(TRADING_PARAMS[req.pair]["sell"].get("MIN_MARGIN") or 0.0),
    )
    cfg = EngineConfig(req.pair, calibration, buy=side_buy, sell=side_sell, atr_desv_limit=ATR_DESV_LIMIT)

    operations = simulate_operations(df, cfg, fee_rate=req.fee_pct / 100.0, max_ops=req.max_ops)
    summary = _build_summary(operations, row_count=len(df), source=source)

    return BacktestResult(pair=req.pair, fee_pct=req.fee_pct, summary=summary, operations=operations)
