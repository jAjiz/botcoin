import copy

import numpy as np
import pandas as pd

import core.config as config
import trading.optimizer.search as optimizer
from trading.optimizer.search import OptimizerRequest, OptimizerResult, run_auto_optimize, run_optimize

_PAIR = "XBTEUR"
_LEVELS = ("LL", "LV", "MV", "HV", "HH")


def _make_df(n: int = 200) -> pd.DataFrame:
    i = np.arange(n)
    price = 100.0 + 25.0 * np.sin(i / 8.0)
    atr = 2.0 + 1.0 * np.abs(np.sin(i / 11.0))
    dtime = pd.date_range("2026-01-01", periods=n, freq="15min").strftime("%Y-%m-%d %H:%M").tolist()
    return pd.DataFrame(
        {
            "time": (np.arange(n) * 900 + 1_767_225_600).tolist(),  # run_optimize sorts by "time"
            "dtime": dtime,
            "high": price + 2.0,
            "low": price - 2.0,
            "close": price,
            "open": price,
            "atr": atr,
        }
    )


def _calibration() -> dict:
    return {
        "up_events": [{"volatility_levels": {"LV": {"k_value": 1.5}, "MV": {"k_value": 2.0}}}],
        "down_events": [{"volatility_levels": {"LV": {"k_value": 1.2}, "MV": {"k_value": 1.8}}}],
        "atr_p20": 1.0,
        "atr_p50": 2.0,
        "atr_p80": 3.0,
        "atr_p95": 4.0,
    }


def _result(robust: float, *, mode: str = "OPTIMIZE") -> OptimizerResult:
    """Minimal OptimizerResult carrying a single candidate with a given robust_pnl."""
    return OptimizerResult(
        pair=_PAIR,
        mode=mode,
        top_candidates=[{"k_act": 0.0, "min_margin": None, "stop_pcts": {}, "robust_pnl_pct": robust}],
        suggested_env_lines=[f"{_PAIR}_K_ACT=0.0"],
        n_trials_run=10,
        n_trials_pruned=0,
    )


# --- run_optimize (OPTIMIZE / CURRENT) -------------------------------------


def test_run_optimize_smoke(monkeypatch) -> None:
    monkeypatch.setattr(optimizer.db, "load_ohlc_data", lambda _p, _tf: _make_df())

    result = run_optimize(OptimizerRequest(pair=_PAIR, mode="OPTIMIZE", n_trials=10), calibration=None)

    assert result.pair == _PAIR
    assert result.mode == "OPTIMIZE"
    assert result.n_trials_run == 10
    assert isinstance(result.suggested_env_lines, list) and result.suggested_env_lines
    assert len(result.top_candidates) >= 1
    best = result.top_candidates[0]
    assert set(_LEVELS) == set(best["stop_pcts"])


def test_run_optimize_no_global_mutation(monkeypatch) -> None:
    monkeypatch.setattr(optimizer.db, "load_ohlc_data", lambda _p, _tf: _make_df())
    monkeypatch.setitem(
        config.TRADING_PARAMS,
        _PAIR,
        {"buy": {"K_ACT": "1.0", "MIN_MARGIN": "0.005"}, "sell": {"K_ACT": "1.0", "MIN_MARGIN": "0.005"}},
    )
    monkeypatch.setitem(config.PAIRS, _PAIR, {"atr_20pct": 1.0, "atr_50pct": 2.0})

    before_tp = copy.deepcopy(config.TRADING_PARAMS[_PAIR])
    before_pairs = copy.deepcopy(config.PAIRS[_PAIR])

    run_optimize(OptimizerRequest(pair=_PAIR, mode="OPTIMIZE", n_trials=5), calibration=None)

    assert config.TRADING_PARAMS[_PAIR] == before_tp
    assert config.PAIRS[_PAIR] == before_pairs


def test_run_optimize_current_mode(monkeypatch) -> None:
    monkeypatch.setattr(optimizer.db, "load_ohlc_data", lambda _p, _tf: _make_df())
    monkeypatch.setattr(
        optimizer,
        "TRADING_PARAMS",
        {_PAIR: {"buy": {"K_ACT": None, "MIN_MARGIN": 0.005}, "sell": {"K_ACT": None, "MIN_MARGIN": 0.005}}},
    )
    monkeypatch.setattr(optimizer, "STOP_PERCENTILES", {_PAIR: dict.fromkeys(_LEVELS, 0.9)})

    result = run_optimize(OptimizerRequest(pair=_PAIR, mode="CURRENT"), calibration=None)

    assert result.n_trials_run == 1
    assert len(result.top_candidates) == 1
    assert result.top_candidates[0]["min_margin"] == 0.005


def test_run_optimize_uses_passed_calibration(monkeypatch) -> None:
    monkeypatch.setattr(optimizer.db, "load_ohlc_data", lambda _p, _tf: _make_df(n=80))

    def _boom(_df):
        raise AssertionError("analyze_structural_noise must not be called when calibration is passed")

    monkeypatch.setattr(optimizer, "analyze_structural_noise", _boom)

    result = run_optimize(
        OptimizerRequest(pair=_PAIR, mode="OPTIMIZE", n_trials=5),
        calibration=_calibration(),
    )

    assert result.n_trials_run == 5


# --- run_auto_optimize -----------------------------------------------------


def _patch_optimize(monkeypatch, mapping: dict[str, float], current_robust: float) -> None:
    """Route run_optimize by mode: CURRENT returns a fixed robust; OPTIMIZE
    returns mapping[seed] so convergence can be steered deterministically."""

    def _fake(req: OptimizerRequest, _calibration) -> OptimizerResult:
        if req.mode == "CURRENT":
            return _result(current_robust, mode="CURRENT")
        return _result(mapping[req.seed])

    monkeypatch.setattr(optimizer, "run_optimize", _fake)


def test_auto_converges_first_batch_improvement(monkeypatch) -> None:
    monkeypatch.setattr(optimizer.random, "sample", lambda _pop, k: [11, 22, 33, 44][:k])
    # 3 of 4 seeds agree on 6.84 (rounds equal); current is worse → improvement.
    _patch_optimize(
        monkeypatch,
        {11: 6.84, 22: 6.841, 33: 6.838, 44: -1.0},
        current_robust=-3.7,
    )

    req = OptimizerRequest(pair=_PAIR, mode="AUTO", n_trials=1000, trial_step=500, max_trials=9000, min_agree=3)
    out = run_auto_optimize(req, calibration=None)

    assert out.mode == "AUTO"
    assert out.converged is True
    assert out.is_improvement is True
    assert out.n_seeds_agreed == 3
    assert out.n_trials_at_convergence == 1000
    assert out.seeds_used == [11, 22, 33, 44]
    assert out.current_robust_pnl == -3.7


def test_auto_converges_but_current_is_better(monkeypatch) -> None:
    monkeypatch.setattr(optimizer.random, "sample", lambda _pop, k: [1, 2, 3, 4][:k])
    _patch_optimize(monkeypatch, {1: 5.0, 2: 5.0, 3: 5.0, 4: 0.0}, current_robust=9.0)

    req = OptimizerRequest(pair=_PAIR, mode="AUTO", n_trials=1000, min_agree=3)
    out = run_auto_optimize(req, calibration=None)

    assert out.converged is True
    assert out.is_improvement is False
    assert out.current_robust_pnl == 9.0


def test_auto_escalates_until_convergence(monkeypatch) -> None:
    monkeypatch.setattr(optimizer.random, "sample", lambda _pop, k: [1, 2, 3, 4][:k])

    def _fake(req: OptimizerRequest, _calibration) -> OptimizerResult:
        if req.mode == "CURRENT":
            return _result(0.0, mode="CURRENT")
        # First batch (n_trials=1000): all-different → no convergence.
        # Second batch (n_trials=1500): three seeds agree on 7.0.
        if req.n_trials == 1000:
            return _result({1: 1.0, 2: 2.0, 3: 3.0, 4: 4.0}[req.seed])
        return _result({1: 7.0, 2: 7.0, 3: 7.0, 4: 0.0}[req.seed])

    monkeypatch.setattr(optimizer, "run_optimize", _fake)

    req = OptimizerRequest(pair=_PAIR, mode="AUTO", n_trials=1000, trial_step=500, max_trials=9000, min_agree=3)
    out = run_auto_optimize(req, calibration=None)

    assert out.converged is True
    assert out.n_trials_at_convergence == 1500


def test_auto_no_convergence_returns_best_fallback(monkeypatch) -> None:
    monkeypatch.setattr(optimizer.random, "sample", lambda _pop, k: [1, 2, 3, 4][:k])
    # Every batch is all-different → never reaches min_agree within the budget.
    monkeypatch.setattr(
        optimizer,
        "run_optimize",
        lambda req, _c: _result({1: 1.0, 2: 2.0, 3: 3.0, 4: 8.5}[req.seed]),
    )

    req = OptimizerRequest(pair=_PAIR, mode="AUTO", n_trials=1000, trial_step=500, max_trials=2000, min_agree=3)
    out = run_auto_optimize(req, calibration=None)

    assert out.converged is False
    assert out.is_improvement is None
    # Fallback returns the highest-robust candidate seen in the last batch.
    assert out.top_candidates[0]["robust_pnl_pct"] == 8.5
