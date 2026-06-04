"""Parameter optimizer.

Pure ``run_optimize(req, calibration) -> OptimizerResult``: an Optuna TPE search
over per-level stop percentiles plus (per mode) an activation multiplier or a
minimum margin. No CLI, no prints, and — critically — no mutation of the global
trading config: every candidate is evaluated by building an ``EngineConfig`` and
running ``trading.engine.simulate_operations``.

The calibration (structural events + ATR percentiles) is passed in explicitly,
not read from ``core.runtime`` — the worker runs in a spawned child process whose
runtime cache is empty (see the Phase 10 plan §5.0). ``None`` means "recompute
from the working dataframe" (sliced requests or a cold parent cache).
"""

import math
from dataclasses import dataclass

import numpy as np
import optuna
from optuna.samplers import TPESampler

import core.database as db
from core.config import ATR_DESV_LIMIT, CANDLE_TIMEFRAME, STOP_PERCENTILES, TRADING_PARAMS
from core.config import VOLATILITY_LEVELS as LEVELS
from trading.engine import EngineConfig, PairCalibration, SidePolicy, simulate_operations
from trading.market_analyzer import analyze_structural_noise

optuna.logging.set_verbosity(optuna.logging.WARNING)

MODES = ("OPTIMIZE", "CURRENT")
SPLIT_METHODS = ("RESET", "CONTINUE", "BOTH")


# --- pure helpers ----------------------------------------------------------


def _compute_atr_thresholds(df) -> tuple[float, float, float, float]:
    atr = df["atr"].to_numpy(dtype=float)
    p20, p50, p80, p95 = (float(np.percentile(atr, p)) for p in (20, 50, 80, 95))
    return p20, p50, p80, p95


def _quantile_ceiled(values: np.ndarray, pct: float) -> float | None:
    if values.size == 0:
        return None
    q = float(np.quantile(values, pct))
    return math.ceil(q * 10.0) / 10.0


def _k_values_by_level(events: list[dict]) -> dict[str, np.ndarray]:
    out: dict[str, list[float]] = {lvl: [] for lvl in LEVELS}
    for e in events:
        vols = e.get("volatility_levels") or {}
        for lvl in LEVELS:
            d = vols.get(lvl)
            if not d:
                continue
            k = d.get("k_value")
            if k is None:
                continue
            out[lvl].append(float(k))
    return {lvl: np.array(vals, dtype=float) for lvl, vals in out.items()}


@dataclass(frozen=True)
class Candidate:
    k_act: float | None
    min_margin: float | None
    stop_pcts: dict[str, float]


@dataclass(frozen=True)
class Score:
    total_pnl: float
    ops: int
    pnl_samples: int


def _robust_key(train: Score, test: Score) -> tuple[float, int]:
    # Robust rank: worst-case P&L across train/test.
    return (float(min(train.total_pnl, test.total_pnl)), int(min(train.pnl_samples, test.pnl_samples)))


def _overall_robust_key(
    reset_train: Score,
    reset_test: Score,
    cont_train: Score,
    cont_test: Score,
) -> tuple[float, int]:
    rr_pnl, rr_n = _robust_key(reset_train, reset_test)
    cr_pnl, cr_n = _robust_key(cont_train, cont_test)
    return (float(min(rr_pnl, cr_pnl)), int(min(rr_n, cr_n)))


def _score_run(ops) -> Score:
    if not ops:
        return Score(total_pnl=-1e18, ops=0, pnl_samples=0)

    total = float(ops[-1].cum_pnl or 0.0)
    pnl_samples = sum(1 for op in ops if op.pnl_abs is not None)
    return Score(total_pnl=total, ops=len(ops), pnl_samples=pnl_samples)


def _split_scores_from_single_run(ops, boundary_time: str) -> tuple[Score, Score]:
    if not ops:
        empty = Score(total_pnl=-1e18, ops=0, pnl_samples=0)
        return empty, empty

    total_net = float(ops[-1].cum_pnl or 0.0)

    before = [op for op in ops if str(op.time) < str(boundary_time)]
    after = [op for op in ops if str(op.time) >= str(boundary_time)]

    first_net = float(before[-1].cum_pnl or 0.0) if before else 0.0

    first_samples = sum(1 for op in before if op.pnl_abs is not None)
    second_samples = sum(1 for op in after if op.pnl_abs is not None)

    first_score = Score(total_pnl=first_net, ops=len(before), pnl_samples=first_samples)
    second_score = Score(total_pnl=(total_net - first_net), ops=len(after), pnl_samples=second_samples)
    return first_score, second_score


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


def _candidate_from_env(pair: str) -> Candidate:
    raw_k_act = TRADING_PARAMS[pair]["buy"].get("K_ACT")
    k_act: float | None
    try:
        k_act = float(raw_k_act) if raw_k_act is not None and str(raw_k_act).strip() != "" else None
    except (TypeError, ValueError):
        k_act = None

    raw_mm = TRADING_PARAMS[pair]["buy"].get("MIN_MARGIN", 0) or 0
    try:
        min_margin = float(raw_mm)
    except (TypeError, ValueError):
        min_margin = 0.0

    stop_pcts = {lvl: float(STOP_PERCENTILES[pair][lvl]) for lvl in LEVELS}
    return Candidate(k_act=k_act, min_margin=min_margin, stop_pcts=stop_pcts)


def _candidate_to_dict(cand: Candidate) -> dict:
    return {"k_act": cand.k_act, "min_margin": cand.min_margin, "stop_pcts": dict(cand.stop_pcts)}


def _build_engine_config(
    pair: str,
    cand: Candidate,
    atr_thresholds: tuple[float, float, float, float],
    up_k: dict[str, np.ndarray],
    down_k: dict[str, np.ndarray],
    atr_desv_limit: float,
) -> EngineConfig:
    # Stops: sell uses uptrend events; buy uses downtrend events.
    sell_k_stop = {lvl: _quantile_ceiled(up_k[lvl], cand.stop_pcts[lvl]) for lvl in LEVELS}
    buy_k_stop = {lvl: _quantile_ceiled(down_k[lvl], cand.stop_pcts[lvl]) for lvl in LEVELS}
    calibration = PairCalibration(
        atr_p20=atr_thresholds[0],
        atr_p50=atr_thresholds[1],
        atr_p80=atr_thresholds[2],
        atr_p95=atr_thresholds[3],
        k_stop_buy=buy_k_stop,
        k_stop_sell=sell_k_stop,
    )
    side = SidePolicy(k_act=cand.k_act, min_margin=cand.min_margin or 0.0)
    return EngineConfig(pair=pair, calibration=calibration, buy=side, sell=side, atr_desv_limit=atr_desv_limit)


# --- Optuna search ---------------------------------------------------------


def _build_study(seed: int) -> optuna.Study:
    return optuna.create_study(direction="maximize", sampler=TPESampler(seed=seed))


def _suggest_kact(trial: optuna.Trial) -> Candidate:
    stop_pcts = {lvl: trial.suggest_float(f"stop_pct_{lvl}", 0.20, 0.95, step=0.05) for lvl in LEVELS}
    return Candidate(
        k_act=trial.suggest_float("k_act", 0.0, 4.0, step=0.5),
        min_margin=None,
        stop_pcts=stop_pcts,
    )


def _suggest_minmargin(trial: optuna.Trial) -> Candidate:
    stop_pcts = {lvl: trial.suggest_float(f"stop_pct_{lvl}", 0.20, 0.95, step=0.05) for lvl in LEVELS}
    return Candidate(
        k_act=None,
        min_margin=trial.suggest_float("min_margin", 0.0, 0.01, step=0.001),
        stop_pcts=stop_pcts,
    )


def _candidate_from_params(params: dict) -> Candidate:
    stop_pcts = {lvl: params[f"stop_pct_{lvl}"] for lvl in LEVELS}
    if "k_act" in params:
        return Candidate(k_act=params["k_act"], min_margin=None, stop_pcts=stop_pcts)
    return Candidate(k_act=None, min_margin=params.get("min_margin", 0.0), stop_pcts=stop_pcts)


# --- request / result ------------------------------------------------------


@dataclass(frozen=True)
class OptimizerRequest:
    pair: str
    mode: str = "OPTIMIZE"  # "OPTIMIZE" | "CURRENT"
    fee_pct: float = 0.0
    start: str | None = None
    end: str | None = None
    train_split: float = 1.0
    split_method: str = "BOTH"  # "RESET" | "CONTINUE" | "BOTH"
    min_ops: int = 0
    min_test_ops: int = 0
    n_trials: int = 300
    seed: int = 42


@dataclass(frozen=True)
class OptimizerResult:
    pair: str
    mode: str
    split_method: str
    top_candidates: list[dict]  # top 5 unique; each has candidate params + scores
    suggested_env_lines: list[str]  # formatted .env lines for top_candidates[0]
    n_trials_run: int
    n_trials_pruned: int


@dataclass(frozen=True)
class _Eval:
    in_sample: Score
    train: Score
    test: Score
    robust_pnl: float
    train_samples: int
    test_samples: int


def _evaluate(
    cand: Candidate,
    *,
    pair: str,
    df,
    train_df,
    test_df,
    split_boundary_time: str | None,
    split_method: str,
    fee_rate: float,
    atr_thresholds: tuple[float, float, float, float],
    up_k: dict[str, np.ndarray],
    down_k: dict[str, np.ndarray],
) -> _Eval:
    cfg = _build_engine_config(pair, cand, atr_thresholds, up_k, down_k, ATR_DESV_LIMIT)
    ops_all = simulate_operations(df, cfg, fee_rate=fee_rate)
    in_sample = _score_run(ops_all)

    if test_df.empty:
        return _Eval(in_sample, in_sample, Score(-1e18, 0, 0), in_sample.total_pnl, in_sample.pnl_samples, 0)

    if split_method == "RESET":
        train = _score_run(simulate_operations(train_df, cfg, fee_rate=fee_rate))
        test = _score_run(simulate_operations(test_df, cfg, fee_rate=fee_rate))
        robust_pnl = _robust_key(train, test)[0]
        return _Eval(in_sample, train, test, robust_pnl, train.pnl_samples, test.pnl_samples)

    if split_method == "CONTINUE":
        train, test = _split_scores_from_single_run(ops_all, split_boundary_time)
        robust_pnl = _robust_key(train, test)[0]
        return _Eval(in_sample, train, test, robust_pnl, train.pnl_samples, test.pnl_samples)

    # BOTH: worst-case across RESET and CONTINUE methods.
    reset_train = _score_run(simulate_operations(train_df, cfg, fee_rate=fee_rate))
    reset_test = _score_run(simulate_operations(test_df, cfg, fee_rate=fee_rate))
    cont_train, cont_test = _split_scores_from_single_run(ops_all, split_boundary_time)
    robust_pnl = _overall_robust_key(reset_train, reset_test, cont_train, cont_test)[0]
    train_samples = min(reset_train.pnl_samples, cont_train.pnl_samples)
    test_samples = min(reset_test.pnl_samples, cont_test.pnl_samples)
    return _Eval(in_sample, reset_train, reset_test, robust_pnl, train_samples, test_samples)


def _scores_dict(ev: _Eval) -> dict:
    def _clean(v: float) -> float | None:
        return None if v <= -1e17 else v

    return {
        "in_sample_pnl_pct": _clean(ev.in_sample.total_pnl),
        "train_pnl_pct": _clean(ev.train.total_pnl),
        "test_pnl_pct": _clean(ev.test.total_pnl),
        "robust_pnl_pct": _clean(ev.robust_pnl),
    }


def run_optimize(req: OptimizerRequest, calibration: dict | None) -> OptimizerResult:
    fee_rate = float(req.fee_pct) / 100.0

    df_full = (
        db.load_ohlc_data(req.pair, CANDLE_TIMEFRAME)
        .dropna(subset=["atr"])
        .sort_values("time")
        .reset_index(drop=True)
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

    # Resolve events + ATR percentiles: from the passed calibration when present,
    # else recompute from the working dataframe (sliced or cold-cache path).
    if calibration is not None:
        up_events = calibration["up_events"]
        down_events = calibration["down_events"]
        atr_thresholds = (
            calibration["atr_p20"],
            calibration["atr_p50"],
            calibration["atr_p80"],
            calibration["atr_p95"],
        )
    else:
        up_events, down_events = analyze_structural_noise(df)
        atr_thresholds = _compute_atr_thresholds(df)

    up_k = _k_values_by_level(up_events)
    down_k = _k_values_by_level(down_events)

    eval_kwargs = dict(
        pair=req.pair,
        df=df,
        train_df=train_df,
        test_df=test_df,
        split_boundary_time=split_boundary_time,
        split_method=req.split_method,
        fee_rate=fee_rate,
        atr_thresholds=atr_thresholds,
        up_k=up_k,
        down_k=down_k,
    )

    if req.mode == "CURRENT":
        cand = _candidate_from_env(req.pair)
        ev = _evaluate(cand, **eval_kwargs)
        return OptimizerResult(
            pair=req.pair,
            mode=req.mode,
            split_method=req.split_method,
            top_candidates=[{**_candidate_to_dict(cand), **_scores_dict(ev)}],
            suggested_env_lines=_format_env_lines(req.pair, cand),
            n_trials_run=1,
            n_trials_pruned=0,
        )

    def _make_objective(suggest_fn):
        def objective(trial: optuna.Trial) -> float:
            cand = suggest_fn(trial)
            ev = _evaluate(cand, **eval_kwargs)
            if test_df.empty:
                if ev.train_samples < req.min_ops:
                    raise optuna.TrialPruned()
            elif ev.train_samples < req.min_ops or ev.test_samples < req.min_test_ops:
                raise optuna.TrialPruned()
            trial.set_user_attr("in_sample_pnl", ev.in_sample.total_pnl)
            trial.set_user_attr("train_pnl", ev.train.total_pnl)
            trial.set_user_attr("test_pnl", ev.test.total_pnl)
            return ev.robust_pnl
        return objective

    n_kact = req.n_trials // 2
    n_minmargin = req.n_trials - n_kact

    study_kact = _build_study(req.seed)
    study_kact.optimize(_make_objective(_suggest_kact), n_trials=n_kact)

    study_minmargin = _build_study(req.seed + 1)
    study_minmargin.optimize(_make_objective(_suggest_minmargin), n_trials=n_minmargin)

    all_completed = [
        t
        for study in (study_kact, study_minmargin)
        for t in study.trials
        if t.state == optuna.trial.TrialState.COMPLETE
    ]
    n_pruned = sum(
        1
        for study in (study_kact, study_minmargin)
        for t in study.trials
        if t.state == optuna.trial.TrialState.PRUNED
    )
    if not all_completed:
        raise ValueError("No candidate met the min_ops / min_test_ops constraints")

    all_completed.sort(key=lambda t: t.value, reverse=True)

    # Deduplicate across both studies. Keys are disjoint (k_act vs min_margin
    # params) so same stop_pcts with different activation types won't collide.
    seen_params: set[tuple] = set()
    unique_completed = []
    for t in all_completed:
        key = tuple(sorted(t.params.items()))
        if key not in seen_params:
            seen_params.add(key)
            unique_completed.append(t)
    top = unique_completed[:5]

    def _trial_dict(t: optuna.trial.FrozenTrial) -> dict:
        cand = _candidate_from_params(t.params)
        return {
            **_candidate_to_dict(cand),
            "in_sample_pnl_pct": t.user_attrs.get("in_sample_pnl"),
            "train_pnl_pct": t.user_attrs.get("train_pnl"),
            "test_pnl_pct": t.user_attrs.get("test_pnl"),
            "robust_pnl_pct": t.value,
        }

    best_cand = _candidate_from_params(top[0].params)

    return OptimizerResult(
        pair=req.pair,
        mode=req.mode,
        split_method=req.split_method,
        top_candidates=[_trial_dict(t) for t in top],
        suggested_env_lines=_format_env_lines(req.pair, best_cand),
        n_trials_run=len(study_kact.trials) + len(study_minmargin.trials),
        n_trials_pruned=n_pruned,
    )
