"""Behavioral tests for the pure engine.

Each test pins a concrete, hand-reasoned behavior on a small fixture (op count,
side, execution price, PnL, fee, K_STOP fallback) rather than a frozen blob, so a
failure says which behavior changed.
"""

import pandas as pd
import pytest

import trading.engine as engine

_LEVELS = ("LL", "LV", "MV", "HV", "HH")


def _df(rows: list[tuple[float, float, float]], atr: float = 2.0) -> pd.DataFrame:
    """Build an OHLC frame from (high, low, close) rows with a constant ATR."""
    return pd.DataFrame(
        {
            "dtime": [f"t{i}" for i in range(len(rows))],
            "high": [r[0] for r in rows],
            "low": [r[1] for r in rows],
            "close": [r[2] for r in rows],
            "atr": [atr] * len(rows),
        }
    )


def _cfg(
    percentiles: tuple[float, float, float, float] = (1.0, 3.0, 5.0, 7.0),
    k_buy: dict[str, float | None] | None = None,
    k_sell: dict[str, float | None] | None = None,
    k_act: float | None = 0.0,
    min_margin: float = 0.0,
    atr_desv_limit: float = 0.2,
) -> engine.EngineConfig:
    kb = k_buy if k_buy is not None else dict.fromkeys(_LEVELS, 1.0)
    ks = k_sell if k_sell is not None else dict.fromkeys(_LEVELS, 1.0)
    return engine.EngineConfig(
        pair="T",
        calibration=engine.PairCalibration(
            atr_p20=percentiles[0],
            atr_p50=percentiles[1],
            atr_p80=percentiles[2],
            atr_p95=percentiles[3],
            k_stop_buy=kb,
            k_stop_sell=ks,
        ),
        buy=engine.SidePolicy(k_act=k_act, min_margin=min_margin),
        sell=engine.SidePolicy(k_act=k_act, min_margin=min_margin),
        atr_desv_limit=atr_desv_limit,
    )


# --- simulate_operations ---------------------------------------------------


def test_first_operation_is_buy_at_first_valid_row() -> None:
    # ATR=2.0 with thresholds (1,3,5,7) => level LV, K_STOP 1.0.
    df = _df([(100.0, 100.0, 100.0), (110.0, 105.0, 108.0)])
    ops = engine.simulate_operations(df, _cfg())

    assert ops[0].side == "buy"
    assert ops[0].price == 100.0
    assert ops[0].vol == "LV"
    assert ops[0].k_stop == 1.0
    assert ops[0].pnl_abs is None
    assert ops[0].cum_pnl == 0.0  # no fee


def test_sell_exit_price_and_pnl_no_fee() -> None:
    # Row1 lifts trailing to high=110, stop = 110 - 1.0*2.0 = 108; low=105 <= 108 -> sell @108.
    df = _df([(100.0, 100.0, 100.0), (110.0, 105.0, 108.0), (112.0, 109.0, 110.0)])
    ops = engine.simulate_operations(df, _cfg(), fee_rate=0.0)

    assert len(ops) == 2
    sell = ops[1]
    assert sell.side == "sell"
    assert sell.price == 108.0
    assert sell.fee_abs == 0.0
    assert sell.pnl_abs == pytest.approx(8.0)  # 108 - 100
    assert sell.pnl_pct == pytest.approx(8.0)  # 8 / 100 * 100


def test_fee_reduces_pnl_and_is_recorded() -> None:
    df = _df([(100.0, 100.0, 100.0), (110.0, 105.0, 108.0), (112.0, 109.0, 110.0)])
    ops = engine.simulate_operations(df, _cfg(), fee_rate=0.01)

    assert ops[0].fee_abs == pytest.approx(1.0)  # 100 * 0.01
    sell = ops[1]
    assert sell.fee_abs == pytest.approx(1.08)  # 108 * 0.01
    assert sell.pnl_abs == pytest.approx(8.0 - 1.08)  # gross 8 minus fee


def test_max_ops_caps_operation_count() -> None:
    # Frame that produces 4 operations uncapped.
    rows = [
        (100.0, 100.0, 100.0),
        (110.0, 105.0, 108.0),  # sell @108
        (109.0, 90.0, 95.0),  # buy  @92
        (120.0, 118.0, 119.0),  # sell @118
    ]
    df = _df(rows)

    assert len(engine.simulate_operations(df, _cfg())) == 4
    assert len(engine.simulate_operations(df, _cfg(), max_ops=2)) == 2


def test_returns_empty_when_no_valid_atr() -> None:
    df = _df([(100.0, 99.0, 100.0), (101.0, 100.0, 100.5)], atr=0.0)
    assert engine.simulate_operations(df, _cfg()) == []


# --- lookup_k_stop ---------------------------------------------------------


def test_lookup_k_stop_direct_hit() -> None:
    # ATR=2.5 with thresholds (1,2,3,4) => level MV.
    cfg = _cfg(percentiles=(1.0, 2.0, 3.0, 4.0), k_sell={**dict.fromkeys(_LEVELS, None), "MV": 1.7})
    assert engine.lookup_k_stop(cfg, "sell", 2.5) == 1.7


def test_lookup_k_stop_falls_back_to_opposite_side() -> None:
    cfg = _cfg(
        percentiles=(1.0, 2.0, 3.0, 4.0),
        k_sell=dict.fromkeys(_LEVELS, None),
        k_buy={**dict.fromkeys(_LEVELS, None), "MV": 3.3},
    )
    assert engine.lookup_k_stop(cfg, "sell", 2.5) == 3.3


def test_lookup_k_stop_falls_back_to_neighbor_level() -> None:
    # MV missing on both sides; nearest same-side neighbor present is HV.
    cfg = _cfg(
        percentiles=(1.0, 2.0, 3.0, 4.0),
        k_sell={**dict.fromkeys(_LEVELS, None), "HV": 2.5},
        k_buy=dict.fromkeys(_LEVELS, None),
    )
    assert engine.lookup_k_stop(cfg, "sell", 2.5) == 2.5


def test_lookup_k_stop_returns_none_when_all_missing() -> None:
    cfg = _cfg(
        percentiles=(1.0, 2.0, 3.0, 4.0), k_sell=dict.fromkeys(_LEVELS, None), k_buy=dict.fromkeys(_LEVELS, None)
    )
    assert engine.lookup_k_stop(cfg, "sell", 2.5) is None
