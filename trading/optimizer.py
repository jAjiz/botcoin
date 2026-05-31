"""
Parameter optimizer using Optuna TPE search.

Replaces the exhaustive grid search from the old optimize_params.py with a
Bayesian TPE sampler. All global-state mutations are eliminated — each trial
builds a PairCalibration object directly from candidate parameters and passes
it to the pure engine, leaving TRADING_PARAMS untouched.
"""

import json
import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import optuna

import core.database as db
from core.config import ATR_DESV_LIMIT, CANDLE_TIMEFRAME
from core.config import VOLATILITY_LEVELS as LEVELS
from trading.engine import EngineConfig, PairCalibration, simulate_operations
from trading.market_analyzer import analyze_structural_noise

optuna.logging.set_verbosity(optuna.logging.WARNING)

STOP_PCT_CHOICES = (0.20, 0.35, 0.50, 0.65, 0.75, 0.80, 0.90, 0.95)
K_ACT_CHOICES = (0.0, 1.0, 2.0, 3.0)
MIN_MARGIN_CHOICES = (0.000, 0.003, 0.006, 0.009)


@dataclass
class OptimizerRequest:
    pair: str
    mode: str  # CONSERVATIVE | AGGRESSIVE
    fee_pct: float = 0.0
    start: str | None = None
    end: str | None = None
    train_split: float = 1.0
    split_method: str = "RESET"  # RESET | CONTINUE | BOTH
    min_ops: int = 0
    min_test_ops: int = 0
    n_trials: int = 200

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OptimizerResult:
    pair: str
    mode: str
    n_trials_run: int
    best_robust_pnl: float
    best_in_sample_pnl: float
    best_train_pnl: float
    best_test_pnl: float
    best_candidate: dict
    suggested_env_lines: list

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


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
            if k is not None:
                out[lvl].append(float(k))
    return {lvl: np.array(vals, dtype=float) for lvl, vals in out.items()}


def _build_calibration(
    atr_20: float,
    atr_50: float,
    atr_80: float,
    atr_95: float,
    up_k: dict[str, np.ndarray],
    down_k: dict[str, np.ndarray],
    stop_pcts: dict[str, float],
    k_act: float | None,
    min_margin: float | None,
) -> PairCalibration:
    sell_k_stops = {lvl: _quantile_ceiled(up_k[lvl], stop_pcts[lvl]) for lvl in LEVELS}
    buy_k_stops = {lvl: _quantile_ceiled(down_k[lvl], stop_pcts[lvl]) for lvl in LEVELS}
    return PairCalibration(
        atr_20pct=atr_20,
        atr_50pct=atr_50,
        atr_80pct=atr_80,
        atr_95pct=atr_95,
        sell_k_stops=sell_k_stops,
        buy_k_stops=buy_k_stops,
        k_act_sell=k_act,
        k_act_buy=k_act,
        min_margin_sell=float(min_margin or 0),
        min_margin_buy=float(min_margin or 0),
        atr_desv_limit=ATR_DESV_LIMIT,
    )


@dataclass(frozen=True)
class _Score:
    total_pnl: float
    pnl_samples: int


def _score_ops(ops) -> _Score:
    if not ops:
        return _Score(total_pnl=-1e18, pnl_samples=0)
    total = float(ops[-1].cum_pnl or 0.0)
    samples = sum(1 for op in ops if op.pnl_abs is not None)
    return _Score(total_pnl=total, pnl_samples=samples)


def _split_scores(ops, boundary_time: str) -> tuple[_Score, _Score]:
    if not ops:
        empty = _Score(total_pnl=-1e18, pnl_samples=0)
        return empty, empty
    total_net = float(ops[-1].cum_pnl or 0.0)
    before = [op for op in ops if str(op.time) < str(boundary_time)]
    after = [op for op in ops if str(op.time) >= str(boundary_time)]
    first_net = float(before[-1].cum_pnl or 0.0) if before else 0.0
    return (
        _Score(
            total_pnl=first_net,
            pnl_samples=sum(1 for op in before if op.pnl_abs is not None),
        ),
        _Score(
            total_pnl=total_net - first_net,
            pnl_samples=sum(1 for op in after if op.pnl_abs is not None),
        ),
    )


def _format_env_lines(pair: str, candidate: dict) -> list[str]:
    lines = []
    k_act = candidate.get("k_act")
    min_margin = candidate.get("min_margin")
    stop_pcts = candidate.get("stop_pcts", {})
    if k_act is not None:
        lines.append(f"{pair}_K_ACT={k_act:.1f}")
    if min_margin is not None:
        lines.append(f"{pair}_MIN_MARGIN={min_margin:.3f}")
    for lvl in LEVELS:
        if lvl in stop_pcts:
            lines.append(f"{pair}_STOP_PCT_{lvl}={stop_pcts[lvl]:.2f}")
    return lines


def run_optimize(req: OptimizerRequest) -> OptimizerResult:
    """Run Optuna TPE optimization over the stop / activation parameter space.

    Reads OHLC data from the database, derives structural-noise K events, then
    runs ``req.n_trials`` Optuna trials where each trial evaluates one candidate
    config via the pure simulation engine (no globals mutated).
    """
    if req.mode not in ("CONSERVATIVE", "AGGRESSIVE"):
        raise ValueError(f"mode must be CONSERVATIVE or AGGRESSIVE, got {req.mode!r}")
    if not (0.5 <= req.train_split <= 1.0):
        raise ValueError("train_split must be in [0.5, 1.0]")
    if req.split_method not in ("RESET", "CONTINUE", "BOTH"):
        raise ValueError(f"split_method must be RESET, CONTINUE, or BOTH, got {req.split_method!r}")

    df = db.load_ohlc_data(req.pair, CANDLE_TIMEFRAME).dropna(subset=["atr"])
    if req.start:
        df = df[df["dtime"] >= req.start]
    if req.end:
        df = df[df["dtime"] <= req.end]
    df = df.reset_index(drop=True)

    if df.empty:
        raise ValueError("No OHLC rows after date slicing")

    atr = df["atr"].to_numpy(dtype=float)
    atr_20 = float(np.percentile(atr, 20))
    atr_50 = float(np.percentile(atr, 50))
    atr_80 = float(np.percentile(atr, 80))
    atr_95 = float(np.percentile(atr, 95))

    up_events, down_events = analyze_structural_noise(df)
    up_k = _k_values_by_level(up_events)
    down_k = _k_values_by_level(down_events)

    split_idx = int(len(df) * req.train_split)
    train_df = df.iloc[:split_idx].reset_index(drop=True)
    test_df = df.iloc[split_idx:].reset_index(drop=True)
    boundary_time: str | None = None
    if not test_df.empty:
        boundary_time = str(df.iloc[split_idx]["dtime"])

    cfg = EngineConfig(fee_rate=req.fee_pct / 100.0)

    def objective(trial: optuna.Trial) -> float:
        stop_pcts = {
            "LL": trial.suggest_categorical("stop_pct_ll", STOP_PCT_CHOICES),
            "LV": trial.suggest_categorical("stop_pct_lv", STOP_PCT_CHOICES),
            "MV": trial.suggest_categorical("stop_pct_mv", STOP_PCT_CHOICES),
            "HV": trial.suggest_categorical("stop_pct_hv", STOP_PCT_CHOICES),
            "HH": trial.suggest_categorical("stop_pct_hh", STOP_PCT_CHOICES),
        }
        k_act: float | None
        min_margin: float | None
        if req.mode == "AGGRESSIVE":
            k_act = trial.suggest_categorical("k_act", K_ACT_CHOICES)
            min_margin = None
        else:
            k_act = None
            min_margin = trial.suggest_categorical("min_margin", MIN_MARGIN_CHOICES)

        cal = _build_calibration(atr_20, atr_50, atr_80, atr_95, up_k, down_k, stop_pcts, k_act, min_margin)

        ops_all = simulate_operations(df, cal, cfg)
        in_sample = _score_ops(ops_all)

        if test_df.empty:
            train_score = in_sample
            test_score = _Score(total_pnl=0.0, pnl_samples=0)
            robust = in_sample.total_pnl
        elif req.split_method == "RESET":
            ops_train = simulate_operations(train_df, cal, cfg)
            ops_test = simulate_operations(test_df, cal, cfg)
            train_score = _score_ops(ops_train)
            test_score = _score_ops(ops_test)
            if train_score.pnl_samples < req.min_ops or test_score.pnl_samples < req.min_test_ops:
                return -1e18
            robust = min(train_score.total_pnl, test_score.total_pnl)
        else:
            # CONTINUE (and BOTH — use CONTINUE for the TPE objective)
            assert boundary_time is not None
            train_score, test_score = _split_scores(ops_all, boundary_time)
            if train_score.pnl_samples < req.min_ops or test_score.pnl_samples < req.min_test_ops:
                return -1e18
            robust = min(train_score.total_pnl, test_score.total_pnl)

        trial.set_user_attr("in_sample_pnl", in_sample.total_pnl)
        trial.set_user_attr("train_pnl", train_score.total_pnl)
        trial.set_user_attr("test_pnl", test_score.total_pnl)
        trial.set_user_attr("robust_pnl", robust)
        trial.set_user_attr("k_act", k_act)
        trial.set_user_attr("min_margin", min_margin)
        trial.set_user_attr("stop_pcts", stop_pcts)
        return robust

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=42),
    )
    study.optimize(objective, n_trials=req.n_trials, show_progress_bar=False)

    best_trial = study.best_trial
    best_candidate: dict[str, Any] = {
        "k_act": best_trial.user_attrs.get("k_act"),
        "min_margin": best_trial.user_attrs.get("min_margin"),
        "stop_pcts": best_trial.user_attrs.get("stop_pcts", {}),
    }

    return OptimizerResult(
        pair=req.pair,
        mode=req.mode,
        n_trials_run=req.n_trials,
        best_robust_pnl=float(best_trial.user_attrs.get("robust_pnl", study.best_value)),
        best_in_sample_pnl=float(best_trial.user_attrs.get("in_sample_pnl", 0.0)),
        best_train_pnl=float(best_trial.user_attrs.get("train_pnl", 0.0)),
        best_test_pnl=float(best_trial.user_attrs.get("test_pnl", 0.0)),
        best_candidate=best_candidate,
        suggested_env_lines=_format_env_lines(req.pair, best_candidate),
    )
