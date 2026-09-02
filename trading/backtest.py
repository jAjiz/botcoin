"""Backtest library entry point.

Pure ``run_backtest(req) -> BacktestResult``: no CLI, no prints, no global
mutation. Configuration for the simulation is built into an ``EngineConfig`` and
handed to ``trading.engine.simulate_operations``.
"""

import math
from dataclasses import dataclass

import numpy as np

import core.database as db
import core.runtime as runtime
from core.config import ATR_DESV_LIMIT, CANDLE_TIMEFRAME, RECALIBRATION_BARS, STOP_PERCENTILES, TRADING_PARAMS
from trading.engine import EngineConfig, Operation, PairCalibration, mark_to_market, simulate_operations
from trading.market_analyzer import (
    analyze_structural_noise,
    atr_ratio_percentiles,
    build_calibration_inputs,
)
from trading.parameters_manager import calculate_k_stops


@dataclass(frozen=True)
class BacktestRequest:
    pair: str
    fee_pct: float = 0.0
    start: str | None = None
    end: str | None = None
    max_ops: int | None = None
    use_live_config: bool = False  # read the calibration cache instead of recomputing
    # Candles between simulated recalibrations; None follows the live cadence, 0 calibrates once.
    recalibration_bars: int | None = None


@dataclass(frozen=True)
class BacktestResult:
    pair: str
    fee_pct: float
    summary: dict  # see _build_summary
    operations: list[Operation]


def _coerce_float(v) -> float | None:
    try:
        return float(v) if v is not None and str(v).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _calibration_schedule(pair: str, df_full, df, recalib_bars: int) -> tuple:
    """Replay the live recalibration cadence, reading the pair's own stop percentiles at every point."""
    pcts = STOP_PERCENTILES[pair]
    schedule = []
    for point in build_calibration_inputs(df_full, df, recalib_bars):
        p20, p50, p80, p95 = point.atr_ratio_thresholds
        schedule.append(
            (
                point.at,
                PairCalibration(
                    atr_ratio_p20=p20,
                    atr_ratio_p50=p50,
                    atr_ratio_p80=p80,
                    atr_ratio_p95=p95,
                    k_stop_buy=_k_stops_from_values(point.down_k, pcts),
                    k_stop_sell=_k_stops_from_values(point.up_k, pcts),
                ),
            )
        )
    return tuple(schedule)


def _k_stops_from_values(k_by_level: dict, pcts: dict) -> dict[str, float | None]:
    """Same percentile-then-ceil rule as parameters_manager.calculate_k_stops, on ready-made arrays."""
    out = {}
    for lvl, values in k_by_level.items():
        out[lvl] = None if values.size == 0 else math.ceil(float(np.quantile(values, float(pcts[lvl]))) * 10.0) / 10.0
    return out


def _build_summary(ops: list[Operation], row_count: int, source: str, final_price: float) -> dict:
    # All pnl_abs values (including the initial entry) for the correct net total.
    all_pnl = [op.pnl_abs for op in ops if op.pnl_abs is not None]
    # Round-trip trades only (skip idx=1, the initial market entry) for per-trade stats.
    trade_pnl = [op.pnl_abs for op in ops if op.pnl_abs is not None and op.idx != 1]
    total_fees = float(sum(op.fee_abs for op in ops if op.fee_abs is not None))
    total_pnl = float(sum(all_pnl)) if all_pnl else 0.0
    total_pnl_pct = float(ops[-1].cum_pnl) if ops and ops[-1].cum_pnl is not None else 0.0
    # A run ends mid-position, so the realized total omits the open leg (see mark_to_market).
    marked_pnl_pct = mark_to_market(ops, final_price)

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
        "open_position_side": ops[-1].side if ops else None,
        "open_position_price": float(ops[-1].price) if ops else None,
        "marked_pnl_pct": marked_pnl_pct,
        "unrealized_pnl_pct": marked_pnl_pct - total_pnl_pct,
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
        # Simulate only [start, end], but calibrate over the full history up to `end`,
        # like the live bot does. Calibrating from the short slice made K_STOP/ATR
        # percentiles unstable (a one-day boundary shift could flip the result's sign).
        # Capping at `end` keeps the run from seeing data after its own window.
        source = "slice"
        df = df_full
        if req.start:
            df = df[df["dtime"] >= req.start]
        if req.end:
            df = df[df["dtime"] <= req.end]
        df = df.reset_index(drop=True)
        cal_df = df_full[df_full["dtime"] <= req.end].reset_index(drop=True) if req.end else df_full
        up_events, down_events = analyze_structural_noise(cal_df)
        atr_ratio_p20, atr_ratio_p50, atr_ratio_p80, atr_ratio_p95 = atr_ratio_percentiles(cal_df)
    else:
        cached = runtime.get_pair_calibration(req.pair) if req.use_live_config else None
        if cached is not None:
            source = "cache"
            df = df_full
            up_events = cached["up_events"]
            down_events = cached["down_events"]
            atr_ratio_p20 = cached["atr_ratio_p20"]
            atr_ratio_p50 = cached["atr_ratio_p50"]
            atr_ratio_p80 = cached["atr_ratio_p80"]
            atr_ratio_p95 = cached["atr_ratio_p95"]
        else:
            source = "recompute"
            df = df_full
            up_events, down_events = analyze_structural_noise(df_full)
            atr_ratio_p20, atr_ratio_p50, atr_ratio_p80, atr_ratio_p95 = atr_ratio_percentiles(df_full)

    calibration = PairCalibration(
        atr_ratio_p20=atr_ratio_p20,
        atr_ratio_p50=atr_ratio_p50,
        atr_ratio_p80=atr_ratio_p80,
        atr_ratio_p95=atr_ratio_p95,
        k_stop_buy=calculate_k_stops(req.pair, down_events),
        k_stop_sell=calculate_k_stops(req.pair, up_events),
    )

    recalib_bars = RECALIBRATION_BARS if req.recalibration_bars is None else int(req.recalibration_bars)
    cfg = EngineConfig(
        req.pair,
        calibration,
        k_act=_coerce_float(TRADING_PARAMS[req.pair].get("K_ACT")),
        min_margin=float(TRADING_PARAMS[req.pair].get("MIN_MARGIN") or 0.0),
        atr_desv_limit=ATR_DESV_LIMIT,
        calibration_schedule=_calibration_schedule(req.pair, df_full, df, recalib_bars),
    )

    fee_rate = req.fee_pct / 100.0
    operations = simulate_operations(df, cfg, fee_rate=fee_rate, max_ops=req.max_ops)
    summary = _build_summary(operations, row_count=len(df), source=source, final_price=float(df.iloc[-1]["close"]))

    return BacktestResult(pair=req.pair, fee_pct=req.fee_pct, summary=summary, operations=operations)
