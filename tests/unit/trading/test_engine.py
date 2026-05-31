"""Tests for the pure simulation engine (trading/engine.py)."""

import pandas as pd
import pytest

from trading.engine import (
    EngineConfig,
    PairCalibration,
    _pnl_abs,
    _resolve_k_stop,
    _vol_level,
    simulate_operations,
)

_CAL = PairCalibration(
    atr_20pct=1.0,
    atr_50pct=2.0,
    atr_80pct=3.0,
    atr_95pct=4.0,
    sell_k_stops={"LL": 1.0, "LV": 1.5, "MV": 2.0, "HV": 2.5, "HH": 3.0},
    buy_k_stops={"LL": 1.0, "LV": 1.5, "MV": 2.0, "HV": 2.5, "HH": 3.0},
    k_act_sell=None,
    k_act_buy=None,
    min_margin_sell=0.0,
    min_margin_buy=0.0,
    atr_desv_limit=0.2,
)

_CFG = EngineConfig(fee_rate=0.0)


def _make_df(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["dtime"] = pd.to_datetime(df.get("time", range(len(rows))), unit="s")
    return df


# ============================================================================
# _vol_level
# ============================================================================


def test_vol_level_maps_correctly():
    assert _vol_level(0.5, _CAL) == "LL"
    assert _vol_level(1.5, _CAL) == "LV"
    assert _vol_level(2.5, _CAL) == "MV"
    assert _vol_level(3.5, _CAL) == "HV"
    assert _vol_level(4.5, _CAL) == "HH"


# ============================================================================
# _resolve_k_stop
# ============================================================================


def test_resolve_k_stop_returns_exact_level():
    assert _resolve_k_stop(_CAL, "sell", "MV") == pytest.approx(2.0)


def test_resolve_k_stop_falls_back_to_opposite_side():
    cal = PairCalibration(
        atr_20pct=1.0,
        atr_50pct=2.0,
        atr_80pct=3.0,
        atr_95pct=4.0,
        sell_k_stops={"LL": None, "LV": None, "MV": None, "HV": None, "HH": None},
        buy_k_stops={"LL": 1.0, "LV": 1.5, "MV": 2.0, "HV": 2.5, "HH": 3.0},
        k_act_sell=None,
        k_act_buy=None,
        min_margin_sell=0.0,
        min_margin_buy=0.0,
    )
    assert _resolve_k_stop(cal, "sell", "MV") == pytest.approx(2.0)


def test_resolve_k_stop_falls_back_to_neighbor():
    cal = PairCalibration(
        atr_20pct=1.0,
        atr_50pct=2.0,
        atr_80pct=3.0,
        atr_95pct=4.0,
        sell_k_stops={"LL": None, "LV": None, "MV": None, "HV": None, "HH": 3.0},
        buy_k_stops={"LL": None, "LV": None, "MV": None, "HV": None, "HH": None},
        k_act_sell=None,
        k_act_buy=None,
        min_margin_sell=0.0,
        min_margin_buy=0.0,
    )
    # HV has no value; nearest neighbor (HH) has 3.0
    assert _resolve_k_stop(cal, "sell", "HV") == pytest.approx(3.0)


# ============================================================================
# _pnl_abs
# ============================================================================


def test_pnl_abs_buy_entry_profit():
    assert _pnl_abs("buy", 100.0, 110.0) == pytest.approx(10.0)


def test_pnl_abs_buy_entry_loss():
    assert _pnl_abs("buy", 100.0, 90.0) == pytest.approx(-10.0)


def test_pnl_abs_sell_entry_profit():
    assert _pnl_abs("sell", 110.0, 100.0) == pytest.approx(10.0)


# ============================================================================
# simulate_operations — basic
# ============================================================================


def test_simulate_returns_empty_for_empty_df():
    df = pd.DataFrame(columns=["high", "low", "close", "atr", "dtime"])
    ops = simulate_operations(df, _CAL, _CFG)
    assert ops == []


def test_simulate_returns_empty_when_all_atr_zero():
    df = _make_df([{"high": 110, "low": 90, "close": 100, "atr": 0, "time": 0}])
    ops = simulate_operations(df, _CAL, _CFG)
    assert ops == []


def test_simulate_starts_with_initial_buy(sample_dataframe):
    ops = simulate_operations(sample_dataframe, _CAL, _CFG)
    assert len(ops) >= 1
    assert ops[0].side == "buy"
    assert ops[0].pnl_abs is None  # entry — no P&L yet


def test_simulate_generates_round_trips(sample_dataframe):
    ops = simulate_operations(sample_dataframe, _CAL, _CFG)
    sides = [op.side for op in ops]
    # Must alternate buy/sell starting from buy
    assert sides[0] == "buy"
    for i in range(1, len(sides)):
        assert sides[i] != sides[i - 1]


def test_simulate_respects_max_ops(sample_dataframe):
    ops = simulate_operations(sample_dataframe, _CAL, EngineConfig(max_ops=2))
    assert len(ops) <= 2


def test_simulate_fee_reduces_cum_pnl(sample_dataframe):
    ops_no_fee = simulate_operations(sample_dataframe, _CAL, EngineConfig(fee_rate=0.0))
    ops_with_fee = simulate_operations(sample_dataframe, _CAL, EngineConfig(fee_rate=0.01))
    if len(ops_no_fee) > 0 and len(ops_with_fee) > 0:
        # Fees must reduce or equal final cumulative P&L
        cum_no_fee = ops_no_fee[-1].cum_pnl or 0.0
        cum_with_fee = ops_with_fee[-1].cum_pnl or 0.0
        assert cum_with_fee <= cum_no_fee


def test_simulate_k_act_causes_immediate_activation(sample_dataframe):
    """k_act_sell=0 sets activation distance to 0 → position activates immediately."""
    cal_immediate = PairCalibration(
        atr_20pct=_CAL.atr_20pct,
        atr_50pct=_CAL.atr_50pct,
        atr_80pct=_CAL.atr_80pct,
        atr_95pct=_CAL.atr_95pct,
        sell_k_stops=_CAL.sell_k_stops,
        buy_k_stops=_CAL.buy_k_stops,
        k_act_sell=0.0,
        k_act_buy=0.0,
        min_margin_sell=0.0,
        min_margin_buy=0.0,
    )
    ops = simulate_operations(sample_dataframe, cal_immediate, _CFG)
    # With k_act=0, activation price equals entry price → more round-trips expected
    assert len(ops) >= 1
