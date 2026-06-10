import copy
import types

import numpy as np
import pandas as pd
import pytest

import core.config as config
import trading.optimizer.search as optimizer
from trading.optimizer.search import (
    AutoSettings,
    GridSpec,
    OptimizerRequest,
    OptimizerResult,
    SearchSpace,
    run_auto_optimize,
    run_optimize,
)

_PAIR = "XBTEUR"
_LEVELS = ("LL", "LV", "MV", "HV", "HH")


def _space(*, k_act: bool = True, min_margin: bool = True) -> SearchSpace:
    """A small coarse search space. Toggle a branch off by passing False."""
    return SearchSpace(
        stop_pcts=GridSpec(0.20, 0.95, 0.25),  # {0.20, 0.45, 0.70, 0.95}
        k_act=GridSpec(0.0, 4.0, 1.0) if k_act else None,
        min_margin=GridSpec(0.0, 0.01, 0.002) if min_margin else None,
    )


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


def _result(robust: float, *, mode: str = "OPTIMIZE", k_act: float = 0.0) -> OptimizerResult:
    """Minimal OptimizerResult carrying a single candidate. ``k_act`` distinguishes
    the config signature so AUTO convergence (which groups by params) can be steered."""
    return OptimizerResult(
        pair=_PAIR,
        mode=mode,
        top_candidates=[{"k_act": k_act, "min_margin": None, "stop_pcts": {}, "robust_pnl_pct": robust}],
        suggested_env_lines=[f"{_PAIR}_K_ACT={k_act}"],
        n_trials_run=10,
    )


# --- run_optimize (OPTIMIZE / CURRENT) -------------------------------------


def test_run_optimize_smoke(monkeypatch) -> None:
    monkeypatch.setattr(optimizer.db, "load_ohlc_data", lambda _p, _tf: _make_df())

    result = run_optimize(
        OptimizerRequest(pair=_PAIR, mode="OPTIMIZE", n_trials=10, search_space=_space()), calibration=None
    )

    assert result.pair == _PAIR
    assert result.mode == "OPTIMIZE"
    assert result.n_trials_run == 10
    assert isinstance(result.suggested_env_lines, list) and result.suggested_env_lines
    assert len(result.top_candidates) >= 1
    best = result.top_candidates[0]
    assert set(_LEVELS) == set(best["stop_pcts"])


def test_run_optimize_grid_honored(monkeypatch) -> None:
    """Searched stop percentiles come only from the configured coarse grid."""
    monkeypatch.setattr(optimizer.db, "load_ohlc_data", lambda _p, _tf: _make_df())

    result = run_optimize(
        OptimizerRequest(pair=_PAIR, mode="OPTIMIZE", n_trials=12, search_space=_space()), calibration=None
    )

    allowed = {0.20, 0.45, 0.70, 0.95}
    for cand in result.top_candidates:
        for v in cand["stop_pcts"].values():
            assert round(v, 2) in allowed


def test_run_optimize_branch_off_kact(monkeypatch) -> None:
    """k_act grid = None → only the min_margin branch runs; full budget to it."""
    monkeypatch.setattr(optimizer.db, "load_ohlc_data", lambda _p, _tf: _make_df())

    result = run_optimize(
        OptimizerRequest(pair=_PAIR, mode="OPTIMIZE", n_trials=8, search_space=_space(k_act=False)),
        calibration=None,
    )

    assert result.n_trials_run == 8  # single branch gets the whole budget
    assert all(c["k_act"] is None for c in result.top_candidates)
    assert all(c["min_margin"] is not None for c in result.top_candidates)


def test_run_optimize_branch_off_minmargin(monkeypatch) -> None:
    """min_margin grid = None → only the k_act branch runs."""
    monkeypatch.setattr(optimizer.db, "load_ohlc_data", lambda _p, _tf: _make_df())

    result = run_optimize(
        OptimizerRequest(pair=_PAIR, mode="OPTIMIZE", n_trials=8, search_space=_space(min_margin=False)),
        calibration=None,
    )

    assert result.n_trials_run == 8
    assert all(c["min_margin"] is None for c in result.top_candidates)
    assert all(c["k_act"] is not None for c in result.top_candidates)


def test_run_optimize_requires_search_space() -> None:
    with pytest.raises(ValueError, match="search_space is required"):
        run_optimize(OptimizerRequest(pair=_PAIR, mode="OPTIMIZE", n_trials=4), calibration=None)


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

    run_optimize(OptimizerRequest(pair=_PAIR, mode="OPTIMIZE", n_trials=5, search_space=_space()), calibration=None)

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
        OptimizerRequest(pair=_PAIR, mode="OPTIMIZE", n_trials=5, search_space=_space()),
        calibration=_calibration(),
    )

    assert result.n_trials_run == 5


# --- run_auto_optimize -----------------------------------------------------


def _patch_auto(monkeypatch, *, seed_fn) -> None:
    """Mock the AUTO seams so convergence is steered deterministically without
    running Optuna. ``seed_fn(seed, n_trials)`` returns ``(k_act, robust)`` for
    that seed: convergence groups by config signature, so seeds sharing a
    ``k_act`` 'agree'. AUTO no longer compares against current, so there is no
    CURRENT seam to mock here."""
    monkeypatch.setattr(optimizer, "_build_eval_context", lambda _req, _cal: None)
    monkeypatch.setattr(optimizer, "_new_seed_studies", lambda seed, _space: types.SimpleNamespace(seed=seed))

    def _seed_result(state, n, _ctx, _req, _ex=None):
        k_act, robust = seed_fn(state.seed, n)
        return _result(robust, k_act=k_act)

    monkeypatch.setattr(optimizer, "_seed_result", _seed_result)


def test_auto_converges_first_batch(monkeypatch) -> None:
    monkeypatch.setattr(optimizer.random, "sample", lambda _pop, k: [11, 22, 33, 44][:k])
    # 3 of 4 seeds land on the same config (k_act=1.0) → convergence.
    _patch_auto(
        monkeypatch,
        seed_fn=lambda seed, _n: {11: (1.0, 6.84), 22: (1.0, 6.84), 33: (1.0, 6.84), 44: (9.0, -1.0)}[seed],
    )

    req = OptimizerRequest(pair=_PAIR, mode="AUTO", n_trials=1000, auto_settings=AutoSettings(), search_space=_space())
    out = run_auto_optimize(req, calibration=None)

    assert out.mode == "AUTO"
    assert out.converged is True
    assert out.n_seeds_agreed == 3
    assert out.n_trials_run == 1000
    assert out.seeds_used == [11, 22, 33, 44]
    # the winning config is the one the 3 agreeing seeds found
    assert out.top_candidates[0]["k_act"] == 1.0


def test_auto_escalates_until_convergence(monkeypatch) -> None:
    monkeypatch.setattr(optimizer.random, "sample", lambda _pop, k: [1, 2, 3, 4][:k])

    # First level (1000 trials): all-different configs → no convergence.
    # Second level (1500 trials): three seeds agree on the same config (k_act=7.0).
    def _seed_fn(seed: int, n: int) -> tuple[float, float]:
        if n == 1000:
            return ({1: 1.0, 2: 2.0, 3: 3.0, 4: 4.0}[seed], 1.0)
        return ({1: 7.0, 2: 7.0, 3: 7.0, 4: 9.0}[seed], 7.0)

    _patch_auto(monkeypatch, seed_fn=_seed_fn)

    req = OptimizerRequest(pair=_PAIR, mode="AUTO", n_trials=1000, auto_settings=AutoSettings(), search_space=_space())
    out = run_auto_optimize(req, calibration=None)

    assert out.converged is True
    assert out.n_trials_run == 1500


def test_auto_no_convergence_returns_best_fallback(monkeypatch) -> None:
    monkeypatch.setattr(optimizer.random, "sample", lambda _pop, k: [1, 2, 3, 4][:k])
    # Every level has all-different configs → never reaches min_agree within budget.
    _patch_auto(
        monkeypatch,
        seed_fn=lambda seed, _n: ({1: 1.0, 2: 2.0, 3: 3.0, 4: 4.0}[seed], {1: 1.0, 2: 2.0, 3: 3.0, 4: 8.5}[seed]),
    )

    req = OptimizerRequest(
        pair=_PAIR,
        mode="AUTO",
        n_trials=1000,
        auto_settings=AutoSettings(max_trials=2000),
        search_space=_space(),
    )
    out = run_auto_optimize(req, calibration=None)

    assert out.converged is False
    # Fallback returns the highest-robust candidate seen in the last batch.
    assert out.top_candidates[0]["robust_pnl_pct"] == 8.5
