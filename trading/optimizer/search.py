"""Parameter optimizer — ``run_optimize`` over an enumerated search space (OPTIMIZE/CURRENT).

See CLAUDE.md's "Trading tools" section and Design choices for the design and semantics.
"""

import math
from dataclasses import dataclass
from itertools import product

import numpy as np
import pandas as pd

import core.database as db
from core.config import ATR_DESV_LIMIT, CANDLE_TIMEFRAME, RECALIBRATION_BARS, STOP_PERCENTILES, TRADING_PARAMS
from core.config import VOLATILITY_LEVELS as LEVELS
from trading.engine import EngineConfig, PairCalibration, mark_to_market, simulate_operations
from trading.market_analyzer import (
    CalibrationInputs,
    analyze_structural_noise,
    atr_ratio_percentiles,
    build_calibration_inputs,
    k_values_by_level,
)

# AUTO is gone from the search: with one shared stop_pct the space is enumerable, so there is
# no sampler for seeds to reach consensus about. It stays in HISTORICAL_MODES because stored
# jobs carry it and must still read back.
MODES = ("OPTIMIZE", "CURRENT")
HISTORICAL_MODES = ("OPTIMIZE", "CURRENT", "AUTO")


# --- search space ----------------------------------------------------------


@dataclass(frozen=True)
class GridSpec:
    """A uniform numeric grid (start, end, step); mirrors the Pydantic GridSpec in api.schemas."""

    start: float
    end: float
    step: float


@dataclass(frozen=True)
class SearchSpace:
    """Per-request search grids; a None branch grid disables that branch (at least one must be set)."""

    stop_pcts: GridSpec
    k_act: GridSpec | None
    min_margin: GridSpec | None


@dataclass(frozen=True)
class CurrentParams:
    """CURRENT-mode evaluation knobs; a set field overrides the live .env value, all None evaluates it as-is."""

    stop_pcts: dict[str, float] | None = None
    k_act: float | None = None
    min_margin: float | None = None


def _current_params_from_dict(d: dict) -> CurrentParams:
    return CurrentParams(
        stop_pcts=d.get("stop_pcts"),
        k_act=d.get("k_act"),
        min_margin=d.get("min_margin"),
    )


def _grid_from_dict(d: dict | None) -> GridSpec | None:
    return None if d is None else GridSpec(**d)


def _search_space_from_dict(d: dict) -> SearchSpace:
    """Coerce a plain dict (from ``model_dump``/``asdict`` round-trips) into a SearchSpace."""
    return SearchSpace(
        stop_pcts=GridSpec(**d["stop_pcts"]),
        k_act=_grid_from_dict(d.get("k_act")),
        min_margin=_grid_from_dict(d.get("min_margin")),
    )


# --- pure helpers ----------------------------------------------------------


def _quantile_ceiled(values: np.ndarray, pct: float) -> float | None:
    if values.size == 0:
        return None
    q = float(np.quantile(values, pct))
    return math.ceil(q * 10.0) / 10.0


_k_values_by_level = k_values_by_level


def _grid_values(g: GridSpec) -> list[float]:
    """Every point on the grid, both ends inclusive. GridSpec validation guarantees
    (end - start) is an integer multiple of step, so the count is exact."""
    n = round((g.end - g.start) / g.step)
    return [round(g.start + i * g.step, 10) for i in range(n + 1)]


def enumerate_candidates(space: SearchSpace) -> list["Candidate"]:
    """Every config in the space, both branches, in a deterministic order.

    One shared ``stop_pct`` across the five levels rather than five searched independently:
    at the operation counts these configs produce, a run exercises two or three levels, so
    the rest are unidentified and a search fills them with noise. Freeing them was measured
    and the deployed search does not converge (0/4 seeds after 12 000 trials). Shared, the
    space is a small product that can simply be enumerated -- no sampler, no seed, no
    convergence question, and an identical request always returns an identical ranking.
    """
    stops = _grid_values(space.stop_pcts)
    out: list[Candidate] = []
    if space.k_act is not None:
        out += [
            Candidate(k_act=k, min_margin=None, stop_pcts=dict.fromkeys(LEVELS, s))
            for k, s in product(_grid_values(space.k_act), stops)
        ]
    if space.min_margin is not None:
        out += [
            Candidate(k_act=None, min_margin=mm, stop_pcts=dict.fromkeys(LEVELS, s))
            for mm, s in product(_grid_values(space.min_margin), stops)
        ]
    return out


@dataclass(frozen=True)
class Candidate:
    k_act: float | None
    min_margin: float | None
    stop_pcts: dict[str, float]


@dataclass(frozen=True)
class Score:
    total_pnl: float
    pnl_samples: int


def _score_run(ops, final_price: float) -> Score:
    """Score a run, valuing the position it ends on: a run never stops flat."""
    if not ops:
        return Score(total_pnl=-1e18, pnl_samples=0)
    pnl_samples = sum(1 for op in ops if op.pnl_abs is not None)
    return Score(total_pnl=mark_to_market(ops, final_price), pnl_samples=pnl_samples)


def _second_half_net(total_net: float, first_net: float) -> float:
    """Return of the second half alone, in percent, as a ratio of growth factors (cum_pnl compounds)."""
    first_factor = 1.0 + (first_net / 100.0)
    if first_factor <= 0.0:
        return -100.0
    return (((1.0 + (total_net / 100.0)) / first_factor) - 1.0) * 100.0


def _split_scores_from_single_run(
    ops, boundary_time: str, boundary_price: float, final_price: float
) -> tuple[Score, Score]:
    """Split one continuous run in two, valuing the position open at each half's end."""
    if not ops:
        empty = Score(total_pnl=-1e18, pnl_samples=0)
        return empty, empty

    total_net = mark_to_market(ops, final_price)
    before = [op for op in ops if str(op.time) < str(boundary_time)]
    after = [op for op in ops if str(op.time) >= str(boundary_time)]
    first_net = mark_to_market(before, boundary_price)
    first_samples = sum(1 for op in before if op.pnl_abs is not None)
    second_samples = sum(1 for op in after if op.pnl_abs is not None)
    return (
        Score(total_pnl=first_net, pnl_samples=first_samples),
        Score(total_pnl=_second_half_net(total_net, first_net), pnl_samples=second_samples),
    )


def _format_env_lines(pair: str, cand: Candidate) -> list[str]:
    lines = []
    if cand.k_act is not None:
        lines.append(f"{pair}_K_ACT={cand.k_act:.1f}")
    if cand.min_margin is not None:
        lines.append(f"{pair}_MIN_MARGIN={cand.min_margin:.3f}")
    lines.append(f"{pair}_STOP_PCT_LL={cand.stop_pcts['LL']:.2f}")
    lines.append(f"{pair}_STOP_PCT_LV={cand.stop_pcts['LV']:.2f}")
    lines.append(f"{pair}_STOP_PCT_MV={cand.stop_pcts['MV']:.2f}")
    lines.append(f"{pair}_STOP_PCT_HV={cand.stop_pcts['HV']:.2f}")
    lines.append(f"{pair}_STOP_PCT_HH={cand.stop_pcts['HH']:.2f}")
    return lines


def _candidate_from_env(req: "OptimizerRequest") -> Candidate:
    pair = req.pair
    cur = req.current_params or CurrentParams()
    if cur.k_act is not None:
        k_act = cur.k_act
    else:
        raw_k_act = TRADING_PARAMS[pair].get("K_ACT")
        try:
            k_act = float(raw_k_act) if raw_k_act is not None and str(raw_k_act).strip() != "" else None
        except (TypeError, ValueError):
            k_act = None
    if cur.min_margin is not None:
        min_margin = cur.min_margin
    else:
        raw_mm = TRADING_PARAMS[pair].get("MIN_MARGIN", 0) or 0
        try:
            min_margin = float(raw_mm)
        except (TypeError, ValueError):
            min_margin = 0.0
    if cur.stop_pcts is not None:
        stop_pcts = {lvl: float(cur.stop_pcts[lvl]) for lvl in LEVELS}
    else:
        stop_pcts = {lvl: float(STOP_PERCENTILES[pair][lvl]) for lvl in LEVELS}
    return Candidate(k_act=k_act, min_margin=min_margin, stop_pcts=stop_pcts)


def _round2(v: float | None) -> float | None:
    """Round an output value to 2 decimals; pass None through unchanged."""
    return None if v is None else round(float(v), 2)


def _candidate_to_dict(cand: Candidate) -> dict:
    return {
        "k_act": cand.k_act,
        "min_margin": cand.min_margin,
        "stop_pcts": {lvl: _round2(p) for lvl, p in cand.stop_pcts.items()},
    }


def _pair_calibration(
    cand: Candidate,
    atr_ratio_thresholds: tuple[float, float, float, float],
    up_k: dict[str, np.ndarray],
    down_k: dict[str, np.ndarray],
) -> PairCalibration:
    return PairCalibration(
        atr_ratio_p20=atr_ratio_thresholds[0],
        atr_ratio_p50=atr_ratio_thresholds[1],
        atr_ratio_p80=atr_ratio_thresholds[2],
        atr_ratio_p95=atr_ratio_thresholds[3],
        k_stop_buy={lvl: _quantile_ceiled(down_k[lvl], cand.stop_pcts[lvl]) for lvl in LEVELS},
        k_stop_sell={lvl: _quantile_ceiled(up_k[lvl], cand.stop_pcts[lvl]) for lvl in LEVELS},
    )


def _build_engine_config(
    pair: str,
    cand: Candidate,
    atr_ratio_thresholds: tuple[float, float, float, float],
    up_k: dict[str, np.ndarray],
    down_k: dict[str, np.ndarray],
    atr_desv_limit: float,
    calibration_points: tuple[CalibrationInputs, ...] = (),
) -> EngineConfig:
    return EngineConfig(
        pair=pair,
        calibration=_pair_calibration(cand, atr_ratio_thresholds, up_k, down_k),
        k_act=cand.k_act,
        min_margin=cand.min_margin or 0.0,
        atr_desv_limit=atr_desv_limit,
        # The candidate's percentiles are what turn each point into the calibration in force there.
        calibration_schedule=tuple(
            (p.at, _pair_calibration(cand, p.atr_ratio_thresholds, p.up_k, p.down_k)) for p in calibration_points
        ),
    )


# --- Optuna search ---------------------------------------------------------


# --- request / result ------------------------------------------------------


@dataclass(frozen=True)
class OptimizerRequest:
    pair: str
    mode: str  # "OPTIMIZE" | "CURRENT"
    fee_pct: float = 0.0
    start: str | None = None
    end: str | None = None
    train_split: float = 1.0
    min_ops: int = 0
    min_test_ops: int = 0
    # Candles between simulated recalibrations; None follows the live cadence, 0 calibrates once.
    recalibration_bars: int | None = None
    # Search grids: required for OPTIMIZE, ignored by CURRENT.
    search_space: SearchSpace | None = None
    # CURRENT-mode .env overrides; ignored by OPTIMIZE.
    current_params: CurrentParams | None = None

    def __post_init__(self) -> None:
        """Coerce the plain dicts produced by model_dump()/asdict round-trips."""
        if isinstance(self.search_space, dict):
            object.__setattr__(self, "search_space", _search_space_from_dict(self.search_space))
        if isinstance(self.current_params, dict):
            object.__setattr__(self, "current_params", _current_params_from_dict(self.current_params))


@dataclass(frozen=True)
class OptimizerResult:
    pair: str
    mode: str
    top_candidates: list[dict]  # top 5 unique; each has candidate params + scores
    suggested_env_lines: list[str]  # formatted .env lines for top_candidates[0]
    n_candidates: int  # size of the enumerated space, after min_ops filtering


@dataclass(frozen=True)
class _Eval:
    in_sample: Score
    train: Score
    test: Score
    robust_pnl: float
    train_samples: int
    test_samples: int


@dataclass(frozen=True)
class EvalContext:
    """Everything a candidate needs to be scored; built once per run and shared by every candidate."""

    pair: str
    df: pd.DataFrame
    train_df: pd.DataFrame
    test_df: pd.DataFrame
    split_boundary_time: str | None
    # A half is valued at the price where it ends, since a run never stops flat.
    boundary_price: float
    final_price: float
    fee_rate: float
    atr_ratio_thresholds: tuple[float, float, float, float]
    up_k: dict[str, np.ndarray]
    down_k: dict[str, np.ndarray]
    min_ops: int
    min_test_ops: int
    # Buy-and-hold over the window and over each half, in percent. Reported beside every
    # euro figure and never ranked on: the objective is base-asset accumulation, and the
    # two disagree in sign whenever a half falls (a -5.19 % euro result over 2025 is
    # +14.79 % of base asset accumulated). Ranking on it would be worse, not better --
    # `min(train, test)` collapses onto one half once the halves sit in opposite regimes.
    hold_pct: float = 0.0
    train_hold_pct: float = 0.0
    test_hold_pct: float = 0.0
    calibration_points: tuple[CalibrationInputs, ...] = ()
    search_space: SearchSpace | None = None


def _evaluate(cand: Candidate, ctx: EvalContext) -> _Eval:
    cfg = _build_engine_config(
        ctx.pair, cand, ctx.atr_ratio_thresholds, ctx.up_k, ctx.down_k, ATR_DESV_LIMIT, ctx.calibration_points
    )
    ops_all = simulate_operations(ctx.df, cfg, fee_rate=ctx.fee_rate)
    in_sample = _score_run(ops_all, ctx.final_price)

    if ctx.test_df.empty:
        return _Eval(in_sample, in_sample, Score(-1e18, 0), in_sample.total_pnl, in_sample.pnl_samples, 0)

    train, test = _split_scores_from_single_run(ops_all, ctx.split_boundary_time, ctx.boundary_price, ctx.final_price)
    robust_pnl = min(train.total_pnl, test.total_pnl)
    return _Eval(in_sample, train, test, robust_pnl, train.pnl_samples, test.pnl_samples)


def _base_asset_pct(eur_pct: float | None, hold_pct: float) -> float | None:
    """Base asset accumulated: (1 + r_bot) / (1 + r_hold) - 1, in percent. Holding is 0 % by
    construction, in any regime, which is what makes it the comparable figure."""
    if eur_pct is None:
        return None
    divisor = 1.0 + hold_pct / 100.0
    if divisor <= 0.0:  # the asset went to zero; accumulation is undefined, not infinite
        return None
    return _round2(((1.0 + eur_pct / 100.0) / divisor - 1.0) * 100.0)


def _hold_dict(in_sample: float | None, train: float | None, test: float | None, ctx: "EvalContext") -> dict:
    """Buy-and-hold over the window and each half, and the same results in base asset."""
    return {
        "hold_pct": _round2(ctx.hold_pct),
        "train_hold_pct": _round2(ctx.train_hold_pct),
        "test_hold_pct": _round2(ctx.test_hold_pct),
        "in_sample_base_pct": _base_asset_pct(_round2(in_sample), ctx.hold_pct),
        "train_base_pct": _base_asset_pct(_round2(train), ctx.train_hold_pct),
        "test_base_pct": _base_asset_pct(_round2(test), ctx.test_hold_pct),
    }


def _scores_dict(ev: _Eval, ctx: "EvalContext") -> dict:
    def _clean(v: float) -> float | None:
        return None if v <= -1e17 else _round2(v)

    in_sample, train, test = _clean(ev.in_sample.total_pnl), _clean(ev.train.total_pnl), _clean(ev.test.total_pnl)
    return {
        "in_sample_pnl_pct": in_sample,
        "train_pnl_pct": train,
        "test_pnl_pct": test,
        "robust_pnl_pct": _clean(ev.robust_pnl),
        "train_ops": ev.train_samples,
        "test_ops": ev.test_samples,
        **_hold_dict(in_sample, train, test, ctx),
    }


# --- study execution -------------------------------------------------------


# Below this trial count, branch parallelism isn't worth the process-spawn overhead.
_PARALLEL_MIN_TRIALS = 200


def _result_from_evaluated(
    req: OptimizerRequest, scored: list[tuple[Candidate, _Eval]], ctx: EvalContext
) -> OptimizerResult:
    """Rank the evaluated candidates and format the top five."""
    # By robust_pnl, ties broken by in-sample, then test, then train PnL. Enumeration is
    # already deterministic, so an identical request returns an identical ranking.
    ranked = sorted(
        scored,
        key=lambda ce: (ce[1].robust_pnl, ce[1].in_sample.total_pnl, ce[1].test.total_pnl, ce[1].train.total_pnl),
        reverse=True,
    )
    top = ranked[:5]
    return OptimizerResult(
        pair=req.pair,
        mode=req.mode,
        top_candidates=[{**_candidate_to_dict(cand), **_scores_dict(ev, ctx)} for cand, ev in top],
        suggested_env_lines=_format_env_lines(req.pair, top[0][0]),
        n_candidates=len(scored),
    )


def _build_eval_context(req: OptimizerRequest, calibration: dict | None) -> EvalContext:
    """Load OHLC once, slice by START/END, and assemble the EvalContext shared by every trial."""
    fee_rate = float(req.fee_pct) / 100.0

    df_full = (
        db.load_ohlc_data(req.pair, CANDLE_TIMEFRAME).dropna(subset=["atr"]).sort_values("time").reset_index(drop=True)
    )
    df = df_full
    if req.start:
        df = df[df["dtime"] >= req.start]
    if req.end:
        df = df[df["dtime"] <= req.end]
    df = df.reset_index(drop=True)
    if df.empty:
        raise ValueError("No rows after START/END slicing")

    split_idx = int(len(df) * float(req.train_split))
    train_df = df.iloc[:split_idx].reset_index(drop=True)
    test_df = df.iloc[split_idx:].reset_index(drop=True)
    split_boundary_time = None if test_df.empty else str(df.iloc[split_idx]["dtime"])
    final_price = float(df.iloc[-1]["close"])
    boundary_price = final_price if test_df.empty else float(test_df.iloc[0]["close"])
    first_price = float(df.iloc[0]["close"])
    hold_pct = (final_price / first_price - 1.0) * 100.0
    train_hold_pct = (boundary_price / first_price - 1.0) * 100.0
    test_hold_pct = 0.0 if test_df.empty else (final_price / boundary_price - 1.0) * 100.0

    if calibration is not None:
        up_events = calibration["up_events"]
        down_events = calibration["down_events"]
        atr_ratio_thresholds = (
            calibration["atr_ratio_p20"],
            calibration["atr_ratio_p50"],
            calibration["atr_ratio_p80"],
            calibration["atr_ratio_p95"],
        )
    else:
        # Calibrate over full history up to `end`, not the slice (see CLAUDE.md Design choices).
        cal_df = df_full[df_full["dtime"] <= req.end].reset_index(drop=True) if req.end else df_full
        up_events, down_events = analyze_structural_noise(cal_df)
        atr_ratio_thresholds = atr_ratio_percentiles(cal_df)

    up_k = _k_values_by_level(up_events)
    down_k = _k_values_by_level(down_events)
    # Built once and shared by every trial: the points do not depend on the candidate.
    recalib_bars = RECALIBRATION_BARS if req.recalibration_bars is None else int(req.recalibration_bars)
    calibration_points = build_calibration_inputs(df_full, df, recalib_bars)

    return EvalContext(
        pair=req.pair,
        df=df,
        train_df=train_df,
        test_df=test_df,
        split_boundary_time=split_boundary_time,
        boundary_price=boundary_price,
        final_price=final_price,
        fee_rate=fee_rate,
        atr_ratio_thresholds=atr_ratio_thresholds,
        up_k=up_k,
        down_k=down_k,
        min_ops=req.min_ops,
        min_test_ops=req.min_test_ops,
        hold_pct=hold_pct,
        train_hold_pct=train_hold_pct,
        test_hold_pct=test_hold_pct,
        calibration_points=calibration_points,
        search_space=req.search_space,
    )


def _current_result(req: OptimizerRequest, ctx: EvalContext) -> OptimizerResult:
    """Evaluate the live ``.env`` config (CURRENT mode)."""
    cand = _candidate_from_env(req)
    ev = _evaluate(cand, ctx)
    return OptimizerResult(
        pair=req.pair,
        mode=req.mode,
        top_candidates=[{**_candidate_to_dict(cand), **_scores_dict(ev, ctx)}],
        suggested_env_lines=_format_env_lines(req.pair, cand),
        n_candidates=1,
    )


# --- main entry point ------------------------------------------------------


def run_optimize(req: OptimizerRequest, calibration: dict | None) -> OptimizerResult:
    if req.mode != "CURRENT" and req.search_space is None:
        raise ValueError("search_space is required for OPTIMIZE")

    ctx = _build_eval_context(req, calibration)

    if req.mode == "CURRENT":
        return _current_result(req, ctx)

    candidates = enumerate_candidates(req.search_space)
    if not candidates:
        raise ValueError("search_space enumerates to no candidates")

    scored = []
    for cand in candidates:
        ev = _evaluate(cand, ctx)
        # The sampler used to prune these; enumeration filters them, with the same meaning.
        if ctx.test_df.empty:
            if ev.train_samples < ctx.min_ops:
                continue
        elif ev.train_samples < ctx.min_ops or ev.test_samples < ctx.min_test_ops:
            continue
        scored.append((cand, ev))

    if not scored:
        raise ValueError("every candidate was filtered out by min_ops / min_test_ops")
    return _result_from_evaluated(req, scored, ctx)
