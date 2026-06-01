"""Equivalence guard for the Step 1 engine refactor.

Runs the new pure ``engine.simulate_operations`` and the legacy
``backtest.simulate_operations`` (still present until Step 4) against the same
OHLC frame and an EngineConfig built to match the live globals, and asserts the
two produce identical operations. Once Step 4 deletes the legacy function, this
should be replaced with a frozen golden-file comparison.
"""

from dataclasses import astuple

import numpy as np
import pandas as pd

import core.config as config
import trading.backtest as backtest
import trading.engine as engine

_PAIR = "EQVTEST"

# ATR percentile thresholds spread across the fixture's ATR range so several
# volatility levels are exercised.
_ATR = {"atr_20pct": 3.3, "atr_50pct": 3.7, "atr_80pct": 4.1, "atr_95pct": 4.4}
_K_STOP = {"LL": 1.0, "LV": 1.2, "MV": 1.5, "HV": 2.0, "HH": 2.5}
_MIN_MARGIN = 0.001


def _make_df() -> pd.DataFrame:
    n = 50
    i = np.arange(n)
    # Large-amplitude zigzag so activations and stops fire repeatedly.
    price = 100.0 + 25.0 * np.sin(i / 4.0)
    high = price + 3.0
    low = price - 3.0
    atr = 3.0 + 1.5 * np.abs(np.sin(i / 5.0))
    dtime = pd.date_range("2026-01-01", periods=n, freq="15min").strftime("%Y-%m-%d %H:%M").tolist()
    return pd.DataFrame({"dtime": dtime, "high": high, "low": low, "close": price, "atr": atr})


def _ops_as_tuples(ops) -> list[tuple]:
    return [astuple(op) for op in ops]


def test_engine_matches_legacy_backtest(monkeypatch) -> None:
    # PAIRS and TRADING_PARAMS are the same dict objects imported by both
    # backtest and parameters_manager, so setitem here is visible to the legacy
    # path's get_k_stop and _activation_price.
    monkeypatch.setitem(config.PAIRS, _PAIR, dict(_ATR))
    monkeypatch.setitem(
        config.TRADING_PARAMS,
        _PAIR,
        {
            "sell": {"K_STOP": dict(_K_STOP), "K_ACT": None, "MIN_MARGIN": _MIN_MARGIN},
            "buy": {"K_STOP": dict(_K_STOP), "K_ACT": None, "MIN_MARGIN": _MIN_MARGIN},
        },
    )

    df = _make_df()
    fee_rate = 0.001

    cfg = engine.EngineConfig(
        pair=_PAIR,
        calibration=engine.PairCalibration(
            atr_p20=_ATR["atr_20pct"],
            atr_p50=_ATR["atr_50pct"],
            atr_p80=_ATR["atr_80pct"],
            atr_p95=_ATR["atr_95pct"],
            k_stop_buy=dict(_K_STOP),
            k_stop_sell=dict(_K_STOP),
        ),
        buy=engine.SidePolicy(k_act=None, min_margin=_MIN_MARGIN),
        sell=engine.SidePolicy(k_act=None, min_margin=_MIN_MARGIN),
        atr_desv_limit=backtest.ATR_DESV_LIMIT,
    )

    legacy_ops = backtest.simulate_operations(df, _PAIR, fee_rate=fee_rate)
    new_ops = engine.simulate_operations(df, cfg, fee_rate=fee_rate)

    # The fixture must actually produce trades, otherwise the guard is vacuous.
    assert len(new_ops) > 1
    assert _ops_as_tuples(new_ops) == _ops_as_tuples(legacy_ops)
