import copy

import numpy as np
import pandas as pd

import core.config as config
import trading.optimizer.search as optimizer
from trading.optimizer.search import OptimizerRequest, run_optimize

_PAIR = "XBTEUR"
_LEVELS = ("LL", "LV", "MV", "HV", "HH")


def _make_df(n: int = 200) -> pd.DataFrame:
    i = np.arange(n)
    price = 100.0 + 25.0 * np.sin(i / 8.0)
    atr = 2.0 + 1.0 * np.abs(np.sin(i / 11.0))
    dtime = pd.date_range("2026-01-01", periods=n, freq="15min").strftime("%Y-%m-%d %H:%M").tolist()
    return pd.DataFrame(
        {"dtime": dtime, "high": price + 2.0, "low": price - 2.0, "close": price, "open": price, "atr": atr}
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


def test_run_optimize_smoke_aggressive(monkeypatch) -> None:
    monkeypatch.setattr(optimizer.db, "load_ohlc_data", lambda _p, _tf: _make_df())

    result = run_optimize(OptimizerRequest(pair=_PAIR, mode="AGGRESSIVE", n_trials=10), calibration=None)

    assert result.pair == _PAIR
    assert result.mode == "AGGRESSIVE"
    assert result.n_trials_run == 10
    assert set(result.best_candidate["stop_pcts"]) == set(_LEVELS)
    assert isinstance(result.suggested_env_lines, list) and result.suggested_env_lines
    assert set(result.scores) == {"in_sample_pnl_pct", "train_pnl_pct", "test_pnl_pct", "robust_pnl_pct"}
    assert len(result.top_candidates) >= 1


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

    run_optimize(OptimizerRequest(pair=_PAIR, mode="AGGRESSIVE", n_trials=5), calibration=None)

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
    assert result.best_candidate["min_margin"] == 0.005


def test_run_optimize_uses_passed_calibration(monkeypatch) -> None:
    monkeypatch.setattr(optimizer.db, "load_ohlc_data", lambda _p, _tf: _make_df(n=80))

    def _boom(_df):
        raise AssertionError("analyze_structural_noise must not be called when calibration is passed")

    monkeypatch.setattr(optimizer, "analyze_structural_noise", _boom)

    result = run_optimize(
        OptimizerRequest(pair=_PAIR, mode="AGGRESSIVE", n_trials=5),
        calibration=_calibration(),
    )

    assert result.n_trials_run == 5
