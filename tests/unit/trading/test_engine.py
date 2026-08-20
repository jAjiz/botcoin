"""Behavioral tests for the pure engine.

Each test pins a concrete, hand-reasoned behavior on a small fixture (op count,
side, execution price, PnL, fee, K_STOP fallback) rather than a frozen blob, so a
failure says which behavior changed. The single exception is the golden-file
regression at the bottom, which exists precisely to prove that a refactor of the
simulation loop produced a byte-identical operation list.
"""

import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd
import pytest

import trading.engine as engine

_LEVELS = ("LL", "LV", "MV", "HV", "HH")
_FIXTURES_DIR = Path(__file__).parents[1] / "fixtures"


def _df(rows: list[tuple[float, float, float]], atr: float = 2.0) -> pd.DataFrame:
    """Build an OHLC frame from (high, low, close) rows with a constant ATR.

    Closes sit around 100, so an ATR of 2.0 reads as a 0.02 ATR/close ratio against
    the ratio percentiles ``_cfg`` supplies."""
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
    percentiles: tuple[float, float, float, float] = (0.01, 0.03, 0.05, 0.07),
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
            atr_ratio_p20=percentiles[0],
            atr_ratio_p50=percentiles[1],
            atr_ratio_p80=percentiles[2],
            atr_ratio_p95=percentiles[3],
            k_stop_buy=kb,
            k_stop_sell=ks,
        ),
        k_act=k_act,
        min_margin=min_margin,
        atr_desv_limit=atr_desv_limit,
    )


# --- simulate_operations ---------------------------------------------------


def test_first_operation_is_buy_at_first_valid_row() -> None:
    # ATR=2.0 at close=100 => ratio 0.02; thresholds (.01,.03,.05,.07) => level LV, K_STOP 1.0.
    df = _df([(100.0, 100.0, 100.0), (110.0, 105.0, 108.0)])
    ops = engine.simulate_operations(df, _cfg())

    assert ops[0].side == "buy"
    assert ops[0].price == 100.0
    assert ops[0].vol == "LV"
    assert ops[0].k_stop == 1.0
    # The entry leg records its own fee as a negative PnL; with no fee it is zero.
    assert ops[0].pnl_abs == pytest.approx(0.0)
    assert ops[0].pnl_pct == pytest.approx(0.0)
    assert ops[0].cum_pnl == 0.0  # no fee


def test_sell_exit_price_and_pnl_no_fee() -> None:
    # Row1 lifts trailing to high=110, stop = 110 - 1.0*2.0 = 108; low=105 <= 108 -> sell @108.
    df = _df([(100.0, 100.0, 100.0), (110.0, 105.0, 108.0)])
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
    assert ops[0].pnl_abs == pytest.approx(-1.0)  # entry leg records its fee as negative PnL
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
    # ATR=2.5 at close=100 => ratio 0.025; thresholds (.01,.02,.03,.04) => level MV.
    cfg = _cfg(percentiles=(0.01, 0.02, 0.03, 0.04), k_sell={**dict.fromkeys(_LEVELS, None), "MV": 1.7})
    assert engine.lookup_k_stop(cfg, "sell", 2.5, 100.0) == 1.7


def test_lookup_k_stop_falls_back_to_opposite_side() -> None:
    cfg = _cfg(
        percentiles=(0.01, 0.02, 0.03, 0.04),
        k_sell=dict.fromkeys(_LEVELS, None),
        k_buy={**dict.fromkeys(_LEVELS, None), "MV": 3.3},
    )
    assert engine.lookup_k_stop(cfg, "sell", 2.5, 100.0) == 3.3


def test_lookup_k_stop_falls_back_to_neighbor_level() -> None:
    # MV missing on both sides; nearest same-side neighbor present is HV.
    cfg = _cfg(
        percentiles=(0.01, 0.02, 0.03, 0.04),
        k_sell={**dict.fromkeys(_LEVELS, None), "HV": 2.5},
        k_buy=dict.fromkeys(_LEVELS, None),
    )
    assert engine.lookup_k_stop(cfg, "sell", 2.5, 100.0) == 2.5


def test_lookup_k_stop_returns_none_when_all_missing() -> None:
    cfg = _cfg(
        percentiles=(0.01, 0.02, 0.03, 0.04), k_sell=dict.fromkeys(_LEVELS, None), k_buy=dict.fromkeys(_LEVELS, None)
    )
    assert engine.lookup_k_stop(cfg, "sell", 2.5, 100.0) is None


# --- reanchor_activation_price -----------------------------------------------


def test_reanchor_pulls_activation_toward_price() -> None:
    # k_act=1.0, ATR=2.0 → activation distance = 2.0.
    # Buy at 100; sell-side activation target = 102. Price drifts down to 97;
    # gap = 102 - 97 = 5 > 2 → re-anchor to 99.
    # Row 2 (high=100) then crosses 99 and activates — it would NOT cross 102
    # without re-anchoring, so this frame has no second operation without the fix.
    rows = [
        (100.0, 100.0, 100.0),  # buy at 100
        (97.0, 97.0, 97.0),  # drift down; re-anchor activation to 99
        (100.0, 99.5, 99.8),  # high=100 >= 99 → activates; stop = 100 - 2 = 98; low 99.5 > 98
        (101.0, 95.0, 98.0),  # trailing=101, stop=99; low=95 → sell at 99
    ]
    cfg = _cfg(k_act=1.0, min_margin=0.0)
    ops = engine.simulate_operations(_df(rows), cfg)

    assert len(ops) == 2
    assert ops[1].side == "sell"
    assert ops[1].price == pytest.approx(99.0)


def test_reanchor_noop_when_within_distance() -> None:
    # k_act=1.0, ATR=2.0 → activation distance = 2.0.
    # Price stays within 2 of activation target throughout; re-anchor never fires.
    # Activation occurs at the original target (102).
    rows = [
        (100.0, 100.0, 100.0),  # buy at 100; activation target = 102
        (101.0, 100.5, 100.8),  # close=100.8; gap = 102 - 100.8 = 1.2 < 2 → no re-anchor; high=101 < 102
        (103.0, 101.5, 102.5),  # high=103 >= 102 → activates; stop = 103 - 2 = 101; low=101.5 > 101
        (104.0, 99.0, 101.5),  # trailing=104, stop=102; low=99 → sell at 102
    ]
    cfg = _cfg(k_act=1.0, min_margin=0.0)
    ops = engine.simulate_operations(_df(rows), cfg)

    assert len(ops) == 2
    assert ops[1].side == "sell"
    assert ops[1].price == pytest.approx(102.0)


# --- golden-file regression --------------------------------------------------


def golden_cfg() -> engine.EngineConfig:
    """Config recorded alongside the golden fixture. K values differ per level and
    per side (with gaps) so the snapshot exercises the lookup_k_stop fallbacks.

    The percentiles are ATR/close ratios; the golden frame prices around 100, so
    they are the pre-ratio thresholds (0.40/0.60/0.90/1.20) divided by that scale,
    and they still spread the frame's bars across all five levels."""
    return engine.EngineConfig(
        pair="GOLD",
        calibration=engine.PairCalibration(
            atr_ratio_p20=0.004,
            atr_ratio_p50=0.006,
            atr_ratio_p80=0.009,
            atr_ratio_p95=0.012,
            k_stop_buy={"LL": 1.1, "LV": 1.4, "MV": None, "HV": 2.2, "HH": 2.8},
            k_stop_sell={"LL": 1.0, "LV": None, "MV": 1.9, "HV": 2.1, "HH": None},
        ),
        k_act=None,
        min_margin=0.002,
        atr_desv_limit=0.15,
    )


GOLDEN_FEE_RATE = 0.0026


def test_simulate_operations_matches_golden_snapshot() -> None:
    """Byte-identical output on a recorded 800-bar frame.

    The behavioral tests above pin *why* each leg happens; this one pins the whole
    result so a mechanical rewrite of the loop (side-branch deduplication,
    iterrows -> itertuples) cannot change a single field without failing.
    """
    df = pd.read_csv(_FIXTURES_DIR / "engine_golden_ohlc.csv", parse_dates=["dtime"])
    expected = json.loads((_FIXTURES_DIR / "engine_golden_ops.json").read_text(encoding="utf-8"))

    ops = engine.simulate_operations(df, golden_cfg(), fee_rate=GOLDEN_FEE_RATE)

    assert [asdict(op) for op in ops] == expected
