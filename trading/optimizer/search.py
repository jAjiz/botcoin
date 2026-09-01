"""Parameter optimizer — run_optimize (OPTIMIZE/CURRENT) and run_auto_optimize (AUTO); see CLAUDE.md's "Trading tools" section and Design choices for the design and semantics."""

import contextlib
import math
import random
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from multiprocessing import get_context

import numpy as np
import optuna
import pandas as pd
from optuna.samplers import TPESampler

import core.database as db
from core.config import ATR_DESV_LIMIT, CANDLE_TIMEFRAME, STOP_PERCENTILES, TRADING_PARAMS
from core.config import VOLATILITY_LEVELS as LEVELS
from trading.engine import EngineConfig, PairCalibration, simulate_operations
from trading.market_analyzer import analyze_structural_noise, atr_ratio_percentiles

optuna.logging.set_verbosity(optuna.logging.WARNING)

MODES = ("OPTIMIZE", "CURRENT", "AUTO")


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
class AutoSettings:
    """AUTO-mode convergence knobs (mirrors the Pydantic AutoSettings)."""

    n_seeds: int = 4
    min_agree: int = 3
    trial_step: int = 500
    max_trials: int = 9_000


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
    pnl_samples: int


def _score_run(ops) -> Score:
    if not ops:
        return Score(total_pnl=-1e18, pnl_samples=0)
    total = float(ops[-1].cum_pnl or 0.0)
    pnl_samples = sum(1 for op in ops if op.pnl_abs is not None)
    return Score(total_pnl=total, pnl_samples=pnl_samples)


def _second_half_net(total_net: float, first_net: float) -> float:
    """Return of the second half alone, in percent, as a ratio of growth factors (cum_pnl compounds)."""
    first_factor = 1.0 + (first_net / 100.0)
    if first_factor <= 0.0:
        return -100.0
    return (((1.0 + (total_net / 100.0)) / first_factor) - 1.0) * 100.0


def _split_scores_from_single_run(ops, boundary_time: str) -> tuple[Score, Score]:
    if not ops:
        empty = Score(total_pnl=-1e18, pnl_samples=0)
        return empty, empty

    total_net = float(ops[-1].cum_pnl or 0.0)
    before = [op for op in ops if str(op.time) < str(boundary_time)]
    after = [op for op in ops if str(op.time) >= str(boundary_time)]
    first_net = float(before[-1].cum_pnl or 0.0) if before else 0.0
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


def _build_engine_config(
    pair: str,
    cand: Candidate,
    atr_ratio_thresholds: tuple[float, float, float, float],
    up_k: dict[str, np.ndarray],
    down_k: dict[str, np.ndarray],
    atr_desv_limit: float,
) -> EngineConfig:
    sell_k_stop = {lvl: _quantile_ceiled(up_k[lvl], cand.stop_pcts[lvl]) for lvl in LEVELS}
    buy_k_stop = {lvl: _quantile_ceiled(down_k[lvl], cand.stop_pcts[lvl]) for lvl in LEVELS}
    calibration = PairCalibration(
        atr_ratio_p20=atr_ratio_thresholds[0],
        atr_ratio_p50=atr_ratio_thresholds[1],
        atr_ratio_p80=atr_ratio_thresholds[2],
        atr_ratio_p95=atr_ratio_thresholds[3],
        k_stop_buy=buy_k_stop,
        k_stop_sell=sell_k_stop,
    )
    return EngineConfig(
        pair=pair,
        calibration=calibration,
        k_act=cand.k_act,
        min_margin=cand.min_margin or 0.0,
        atr_desv_limit=atr_desv_limit,
    )


# --- Optuna search ---------------------------------------------------------


def _build_study(seed: int) -> optuna.Study:
    return optuna.create_study(direction="maximize", sampler=TPESampler(seed=seed))


def _suggest_stops(trial: optuna.Trial, grid: GridSpec) -> dict[str, float]:
    return {lvl: trial.suggest_float(f"stop_pct_{lvl}", grid.start, grid.end, step=grid.step) for lvl in LEVELS}


def _suggest_kact(trial: optuna.Trial, space: SearchSpace) -> Candidate:
    g = space.k_act
    return Candidate(
        k_act=trial.suggest_float("k_act", g.start, g.end, step=g.step),
        min_margin=None,
        stop_pcts=_suggest_stops(trial, space.stop_pcts),
    )


def _suggest_minmargin(trial: optuna.Trial, space: SearchSpace) -> Candidate:
    g = space.min_margin
    return Candidate(
        k_act=None,
        min_margin=trial.suggest_float("min_margin", g.start, g.end, step=g.step),
        stop_pcts=_suggest_stops(trial, space.stop_pcts),
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
    mode: str  # "OPTIMIZE" | "CURRENT" | "AUTO"
    fee_pct: float = 0.0
    start: str | None = None
    end: str | None = None
    train_split: float = 0.8
    min_ops: int = 0
    min_test_ops: int = 0
    n_trials: int = 1_000
    seed: int = 42
    # AUTO-mode knobs (None => defaults).
    auto_settings: AutoSettings | None = None
    # Search grids: required for OPTIMIZE/AUTO, ignored by CURRENT.
    search_space: SearchSpace | None = None
    # CURRENT-mode .env overrides; ignored by OPTIMIZE/AUTO.
    current_params: CurrentParams | None = None

    def __post_init__(self) -> None:
        """Coerce the plain dicts produced by model_dump()/asdict round-trips."""
        if isinstance(self.search_space, dict):
            object.__setattr__(self, "search_space", _search_space_from_dict(self.search_space))
        if isinstance(self.auto_settings, dict):
            object.__setattr__(self, "auto_settings", AutoSettings(**self.auto_settings))
        if isinstance(self.current_params, dict):
            object.__setattr__(self, "current_params", _current_params_from_dict(self.current_params))


@dataclass(frozen=True)
class OptimizerResult:
    pair: str
    mode: str
    top_candidates: list[dict]  # top 5 unique; each has candidate params + scores
    suggested_env_lines: list[str]  # formatted .env lines for top_candidates[0]
    n_trials_run: int
    # AUTO-only; left at their defaults by OPTIMIZE and CURRENT.
    converged: bool = False
    seeds_used: list = field(default_factory=list)
    n_seeds_agreed: int = 0


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
    """Everything a trial needs to score a candidate; built once per run and shared by every seed/level/trial."""

    pair: str
    df: pd.DataFrame
    train_df: pd.DataFrame
    test_df: pd.DataFrame
    split_boundary_time: str | None
    fee_rate: float
    atr_ratio_thresholds: tuple[float, float, float, float]
    up_k: dict[str, np.ndarray]
    down_k: dict[str, np.ndarray]
    min_ops: int
    min_test_ops: int
    search_space: SearchSpace | None = None


def _evaluate(cand: Candidate, ctx: EvalContext) -> _Eval:
    cfg = _build_engine_config(ctx.pair, cand, ctx.atr_ratio_thresholds, ctx.up_k, ctx.down_k, ATR_DESV_LIMIT)
    ops_all = simulate_operations(ctx.df, cfg, fee_rate=ctx.fee_rate)
    in_sample = _score_run(ops_all)

    if ctx.test_df.empty:
        return _Eval(in_sample, in_sample, Score(-1e18, 0), in_sample.total_pnl, in_sample.pnl_samples, 0)

    train, test = _split_scores_from_single_run(ops_all, ctx.split_boundary_time)
    robust_pnl = min(train.total_pnl, test.total_pnl)
    return _Eval(in_sample, train, test, robust_pnl, train.pnl_samples, test.pnl_samples)


def _scores_dict(ev: _Eval) -> dict:
    def _clean(v: float) -> float | None:
        return None if v <= -1e17 else _round2(v)

    return {
        "in_sample_pnl_pct": _clean(ev.in_sample.total_pnl),
        "train_pnl_pct": _clean(ev.train.total_pnl),
        "test_pnl_pct": _clean(ev.test.total_pnl),
        "robust_pnl_pct": _clean(ev.robust_pnl),
        "train_ops": ev.train_samples,
        "test_ops": ev.test_samples,
    }


# --- study execution -------------------------------------------------------


def _build_objective(study_type: str, ctx: EvalContext):
    """Build the Optuna objective for one branch (``kact`` or ``minmargin``)."""
    suggest_fn = _suggest_kact if study_type == "kact" else _suggest_minmargin

    def objective(trial: optuna.Trial) -> float:
        cand = suggest_fn(trial, ctx.search_space)
        ev = _evaluate(cand, ctx)
        if ctx.test_df.empty:
            if ev.train_samples < ctx.min_ops:
                raise optuna.TrialPruned()
        elif ev.train_samples < ctx.min_ops or ev.test_samples < ctx.min_test_ops:
            raise optuna.TrialPruned()
        trial.set_user_attr("in_sample_pnl", ev.in_sample.total_pnl)
        trial.set_user_attr("train_pnl", ev.train.total_pnl)
        trial.set_user_attr("test_pnl", ev.test.total_pnl)
        trial.set_user_attr("train_ops", ev.train_samples)
        trial.set_user_attr("test_ops", ev.test_samples)
        return ev.robust_pnl

    return objective


def _collect_completed(study: optuna.Study) -> list[tuple]:
    """Plain (params, value, user_attrs) tuples for the study's COMPLETE trials."""
    return [(t.params, t.value, t.user_attrs) for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]


@dataclass
class _SeedStudies:
    """A seed's warm-startable studies, one per active branch; ``done`` tracks the cumulative trial target per branch."""

    seed: int
    studies: dict[str, optuna.Study]
    done: dict[str, int]


def _new_seed_studies(seed: int, space: SearchSpace) -> _SeedStudies:
    # minmargin uses seed+1 so the two branches explore independently; a None grid omits that branch.
    studies: dict[str, optuna.Study] = {}
    if space.k_act is not None:
        studies["kact"] = _build_study(seed)
    if space.min_margin is not None:
        studies["minmargin"] = _build_study(seed + 1)
    return _SeedStudies(seed=seed, studies=studies, done=dict.fromkeys(studies, 0))


def _split_budget(target_n_trials: int, branches: list[str]) -> dict[str, int]:
    """Split the trial budget evenly across active branches; the remainder goes to the last one."""
    n = len(branches)
    base = target_n_trials // n
    out = dict.fromkeys(branches, base)
    out[branches[-1]] += target_n_trials - base * n
    return out


# Below this trial count, branch parallelism isn't worth the process-spawn overhead.
_PARALLEL_MIN_TRIALS = 200


def _branch_executor(target_trials: int):
    """Yields a 2-worker process pool for branch parallelism when the workload justifies it, else None."""
    if target_trials < _PARALLEL_MIN_TRIALS:
        return contextlib.nullcontext(None)
    return ProcessPoolExecutor(max_workers=2, mp_context=get_context("spawn"))


def _advance_branch(study: optuna.Study, study_type: str, n_trials: int, ctx: EvalContext) -> optuna.Study:
    """Run ``n_trials`` more on ``study`` and return it; module-level so it can run in a worker process."""
    study.optimize(_build_objective(study_type, ctx), n_trials=n_trials)
    return study


def _advance_seed_to(
    state: _SeedStudies,
    target_n_trials: int,
    ctx: EvalContext,
    executor: ProcessPoolExecutor | None = None,
) -> tuple[list[tuple], int]:
    """Warm-start each active branch to its share of ``target_n_trials``, running only the delta; returns (completed, n_total)."""
    branches = list(state.studies)
    targets = _split_budget(target_n_trials, branches)
    deltas = {b: targets[b] - state.done[b] for b in branches}
    work = [b for b in branches if deltas[b] > 0]

    if executor is not None and len(work) > 1:
        futures = {b: executor.submit(_advance_branch, state.studies[b], b, deltas[b], ctx) for b in work}
        for b, fut in futures.items():
            state.studies[b] = fut.result()
    else:
        for b in work:
            _advance_branch(state.studies[b], b, deltas[b], ctx)
    for b in branches:
        state.done[b] = targets[b]

    completed = [t for s in state.studies.values() for t in _collect_completed(s)]
    n_total = sum(len(s.trials) for s in state.studies.values())
    return completed, n_total


def _result_from_completed(req: OptimizerRequest, all_completed: list[tuple], n_total: int) -> OptimizerResult:
    """Rank, deduplicate and format the completed trials into an OptimizerResult."""
    # Rank by robust_pnl, breaking ties by in-sample, then test, then train PnL for determinism.
    ranked = sorted(
        all_completed,
        key=lambda t: (
            t[1],
            t[2].get("in_sample_pnl", -1e18),
            t[2].get("test_pnl", -1e18),
            t[2].get("train_pnl", -1e18),
        ),
        reverse=True,
    )

    # Deduplicate across both branches; disjoint keys mean same stop_pcts across branches won't collide.
    seen_params: set[tuple] = set()
    unique_completed = []
    for params, value, user_attrs in ranked:
        key = tuple(sorted(params.items()))
        if key not in seen_params:
            seen_params.add(key)
            unique_completed.append((params, value, user_attrs))
    top = unique_completed[:5]

    def _trial_dict(params: dict, value: float, user_attrs: dict) -> dict:
        cand = _candidate_from_params(params)
        return {
            **_candidate_to_dict(cand),
            "in_sample_pnl_pct": _round2(user_attrs.get("in_sample_pnl")),
            "train_pnl_pct": _round2(user_attrs.get("train_pnl")),
            "test_pnl_pct": _round2(user_attrs.get("test_pnl")),
            "robust_pnl_pct": _round2(value),
            "train_ops": user_attrs.get("train_ops"),
            "test_ops": user_attrs.get("test_ops"),
        }

    best_cand = _candidate_from_params(top[0][0])
    return OptimizerResult(
        pair=req.pair,
        mode=req.mode,
        top_candidates=[_trial_dict(p, v, ua) for p, v, ua in top],
        suggested_env_lines=_format_env_lines(req.pair, best_cand),
        n_trials_run=n_total,
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

    return EvalContext(
        pair=req.pair,
        df=df,
        train_df=train_df,
        test_df=test_df,
        split_boundary_time=split_boundary_time,
        fee_rate=fee_rate,
        atr_ratio_thresholds=atr_ratio_thresholds,
        up_k=up_k,
        down_k=down_k,
        min_ops=req.min_ops,
        min_test_ops=req.min_test_ops,
        search_space=req.search_space,
    )


def _current_result(req: OptimizerRequest, ctx: EvalContext) -> OptimizerResult:
    """Evaluate the live ``.env`` config (CURRENT mode)."""
    cand = _candidate_from_env(req)
    ev = _evaluate(cand, ctx)
    return OptimizerResult(
        pair=req.pair,
        mode=req.mode,
        top_candidates=[{**_candidate_to_dict(cand), **_scores_dict(ev)}],
        suggested_env_lines=_format_env_lines(req.pair, cand),
        n_trials_run=1,
    )


# --- main entry point ------------------------------------------------------


def run_optimize(req: OptimizerRequest, calibration: dict | None) -> OptimizerResult:
    if req.mode != "CURRENT" and req.search_space is None:
        raise ValueError("search_space is required for OPTIMIZE/AUTO")

    ctx = _build_eval_context(req, calibration)

    if req.mode == "CURRENT":
        return _current_result(req, ctx)

    state = _new_seed_studies(req.seed, req.search_space)
    with _branch_executor(req.n_trials) as executor:
        return _seed_result(state, req.n_trials, ctx, req, executor)


# --- AUTO mode convergence loop --------------------------------------------


def _candidate_signature(cand: dict) -> tuple:
    """Hashable signature of a candidate's config (not score); groups seeds that found the same solution."""
    return (
        cand.get("k_act"),
        cand.get("min_margin"),
        tuple(sorted((cand.get("stop_pcts") or {}).items())),
        cand.get("er_window"),
        cand.get("chop_enter_pct"),
    )


def _check_convergence(results: list[OptimizerResult], min_agree: int) -> tuple[OptimizerResult, int] | None:
    """Group results by config signature; return (best, n_agreed) for the highest-robust_pnl group reaching min_agree."""
    groups: dict[tuple, list[OptimizerResult]] = {}
    for r in results:
        if not r.top_candidates:
            continue
        groups.setdefault(_candidate_signature(r.top_candidates[0]), []).append(r)

    qualifying = [g for g in groups.values() if len(g) >= min_agree]
    if not qualifying:
        return None
    best_group = max(qualifying, key=lambda g: g[0].top_candidates[0].get("robust_pnl_pct") or -1e18)
    return best_group[0], len(best_group)


def _seed_result(
    state: _SeedStudies,
    target_n_trials: int,
    ctx: EvalContext,
    req: OptimizerRequest,
    executor: ProcessPoolExecutor | None = None,
) -> OptimizerResult:
    """Advance one seed's studies to ``target_n_trials`` (warm-start) and build its OptimizerResult."""
    completed, n_total = _advance_seed_to(state, target_n_trials, ctx, executor)
    if not completed:
        raise ValueError("No candidate met the min_ops / min_test_ops constraints")
    return _result_from_completed(req, completed, n_total)


def run_auto_optimize(req: OptimizerRequest, calibration: dict | None) -> OptimizerResult:
    if req.search_space is None:
        raise ValueError("search_space is required for OPTIMIZE/AUTO")
    auto = req.auto_settings or AutoSettings()
    # Seeded from req.seed (not the global RNG) so the stored request fully determines the run.
    seeds = random.Random(req.seed).sample(range(1, 9999), auto.n_seeds)
    # OHLC/calibration load once; each seed's studies stay alive across escalation levels (warm-start).
    ctx = _build_eval_context(req, calibration)
    states = {seed: _new_seed_studies(seed, req.search_space) for seed in seeds}

    n_trials = req.n_trials
    last_results: list[OptimizerResult] = []

    # One pool for the whole search, reused across seeds and escalation levels.
    with _branch_executor(auto.max_trials) as executor:
        while n_trials <= auto.max_trials:
            last_results = []
            for seed in seeds:
                # min_ops not met → this seed just doesn't converge this round; its studies persist for a higher budget.
                with contextlib.suppress(ValueError):
                    last_results.append(_seed_result(states[seed], n_trials, ctx, req, executor))

            converged = _check_convergence(last_results, auto.min_agree)
            if converged is not None:
                best, n_agreed = converged
                return OptimizerResult(
                    pair=req.pair,
                    mode="AUTO",
                    top_candidates=best.top_candidates,
                    suggested_env_lines=best.suggested_env_lines,
                    n_trials_run=n_trials,
                    converged=True,
                    seeds_used=seeds,
                    n_seeds_agreed=n_agreed,
                )

            n_trials += auto.trial_step

    valid = [r for r in last_results if r.top_candidates]
    if not valid:
        raise ValueError("AUTO mode: no valid candidates found within the trial budget")
    best_fallback = max(valid, key=lambda r: r.top_candidates[0].get("robust_pnl_pct") or -1e18)
    return OptimizerResult(
        pair=req.pair,
        mode="AUTO",
        top_candidates=best_fallback.top_candidates,
        suggested_env_lines=best_fallback.suggested_env_lines,
        n_trials_run=n_trials - auto.trial_step,
        converged=False,
        seeds_used=seeds,
    )
