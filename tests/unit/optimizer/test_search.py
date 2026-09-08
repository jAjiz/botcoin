import copy
import types

import numpy as np
import pandas as pd
import pytest

import core.config as config
import trading.market_analyzer as market_analyzer
import trading.optimizer.search as optimizer
from trading.optimizer.search import (
    GridSpec,
    OptimizerRequest,
    OptimizerResult,
    SearchSpace,
    enumerate_candidates,
    run_optimize,
)

_PAIR = "XBTEUR"
_LEVELS = ("LL", "LV", "MV", "HV", "HH")


def _space(*, k_act: bool = True, min_margin: bool = True) -> SearchSpace:
    """A small coarse search space. Toggle a branch off by passing False."""
    return SearchSpace(
        stop_pcts=GridSpec(0.15, 0.90, 0.25),  # {0.15, 0.40, 0.65, 0.90}
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
        "atr_ratio_p20": 0.01,
        "atr_ratio_p50": 0.02,
        "atr_ratio_p80": 0.03,
        "atr_ratio_p95": 0.04,
    }


def _result(robust: float, *, mode: str = "OPTIMIZE", k_act: float = 0.0) -> OptimizerResult:
    """Minimal OptimizerResult with one candidate."""
    return OptimizerResult(
        pair=_PAIR,
        mode=mode,
        top_candidates=[{"k_act": k_act, "min_margin": None, "stop_pcts": {}, "robust_pnl_pct": robust}],
        suggested_env_lines=[f"{_PAIR}_K_ACT={k_act}"],
        n_candidates=10,
    )


# --- run_optimize (OPTIMIZE / CURRENT) -------------------------------------


def test_run_optimize_smoke(monkeypatch) -> None:
    monkeypatch.setattr(optimizer.db, "load_ohlc_data", lambda _p, _tf: _make_df())

    result = run_optimize(OptimizerRequest(pair=_PAIR, mode="OPTIMIZE", search_space=_space()), calibration=None)

    assert result.pair == _PAIR
    assert result.mode == "OPTIMIZE"
    assert result.n_candidates == 44
    assert isinstance(result.suggested_env_lines, list) and result.suggested_env_lines
    assert len(result.top_candidates) >= 1
    best = result.top_candidates[0]
    assert set(_LEVELS) == set(best["stop_pcts"])


def test_run_optimize_grid_honored(monkeypatch) -> None:
    """Stop percentiles come only from the configured grid."""
    monkeypatch.setattr(optimizer.db, "load_ohlc_data", lambda _p, _tf: _make_df())

    result = run_optimize(OptimizerRequest(pair=_PAIR, mode="OPTIMIZE", search_space=_space()), calibration=None)

    allowed = {0.15, 0.40, 0.65, 0.90}
    for cand in result.top_candidates:
        for v in cand["stop_pcts"].values():
            assert round(v, 2) in allowed


def test_run_optimize_branch_off_kact(monkeypatch) -> None:
    """k_act grid = None → only the min_margin branch runs; full budget to it."""
    monkeypatch.setattr(optimizer.db, "load_ohlc_data", lambda _p, _tf: _make_df())

    result = run_optimize(
        OptimizerRequest(pair=_PAIR, mode="OPTIMIZE", search_space=_space(k_act=False)),
        calibration=None,
    )

    assert result.n_candidates == 24  # 6 min_margin values x 4 stops
    assert all(c["k_act"] is None for c in result.top_candidates)
    assert all(c["min_margin"] is not None for c in result.top_candidates)


def test_run_optimize_branch_off_minmargin(monkeypatch) -> None:
    """min_margin grid = None → only the k_act branch runs."""
    monkeypatch.setattr(optimizer.db, "load_ohlc_data", lambda _p, _tf: _make_df())

    result = run_optimize(
        OptimizerRequest(pair=_PAIR, mode="OPTIMIZE", search_space=_space(min_margin=False)),
        calibration=None,
    )

    assert result.n_candidates == 20  # 5 k_act values x 4 stops
    assert all(c["min_margin"] is None for c in result.top_candidates)
    assert all(c["k_act"] is not None for c in result.top_candidates)


def test_run_optimize_requires_search_space() -> None:
    with pytest.raises(ValueError, match="search_space is required"):
        run_optimize(OptimizerRequest(pair=_PAIR, mode="OPTIMIZE"), calibration=None)


def test_run_optimize_no_global_mutation(monkeypatch) -> None:
    monkeypatch.setattr(optimizer.db, "load_ohlc_data", lambda _p, _tf: _make_df())
    monkeypatch.setitem(
        config.TRADING_PARAMS,
        _PAIR,
        {"K_ACT": "1.0", "MIN_MARGIN": "0.005"},
    )
    monkeypatch.setitem(config.PAIRS, _PAIR, {"atr_ratio_p20": 0.01, "atr_ratio_p50": 0.02})

    before_tp = copy.deepcopy(config.TRADING_PARAMS[_PAIR])
    before_pairs = copy.deepcopy(config.PAIRS[_PAIR])

    run_optimize(OptimizerRequest(pair=_PAIR, mode="OPTIMIZE", search_space=_space()), calibration=None)

    assert config.TRADING_PARAMS[_PAIR] == before_tp
    assert config.PAIRS[_PAIR] == before_pairs


def test_run_optimize_current_mode(monkeypatch) -> None:
    monkeypatch.setattr(optimizer.db, "load_ohlc_data", lambda _p, _tf: _make_df())
    monkeypatch.setattr(
        optimizer,
        "TRADING_PARAMS",
        {_PAIR: {"K_ACT": None, "MIN_MARGIN": 0.005}},
    )
    monkeypatch.setattr(optimizer, "STOP_PERCENTILES", {_PAIR: dict.fromkeys(_LEVELS, 0.9)})

    result = run_optimize(OptimizerRequest(pair=_PAIR, mode="CURRENT"), calibration=None)

    assert result.n_candidates == 1
    assert len(result.top_candidates) == 1
    assert result.top_candidates[0]["min_margin"] == 0.005


def test_build_eval_context_calibrates_over_history_up_to_end_not_start(monkeypatch) -> None:
    """A sliced job calibrates over [T0, end] (full history up to the window end), independent of `start`."""
    df = _make_df(n=200)
    monkeypatch.setattr(optimizer.db, "load_ohlc_data", lambda _p, _tf: df.copy())
    monkeypatch.setattr(optimizer, "TRADING_PARAMS", {_PAIR: {"K_ACT": None, "MIN_MARGIN": 0.0}})
    monkeypatch.setattr(optimizer, "STOP_PERCENTILES", {_PAIR: dict.fromkeys(_LEVELS, 0.9)})
    seen: list[int] = []
    monkeypatch.setattr(optimizer, "analyze_structural_noise", lambda d: seen.append(len(d)) or ([], []))

    end = df["dtime"].iloc[120]
    run_optimize(OptimizerRequest(pair=_PAIR, mode="CURRENT", start=df["dtime"].iloc[0], end=end), calibration=None)
    run_optimize(OptimizerRequest(pair=_PAIR, mode="CURRENT", start=df["dtime"].iloc[80], end=end), calibration=None)

    assert seen[0] == seen[1]  # calibration window independent of `start`
    assert seen[0] == int((df["dtime"] <= end).sum())  # spans all history up to `end`


def test_run_optimize_uses_passed_calibration(monkeypatch) -> None:
    monkeypatch.setattr(optimizer.db, "load_ohlc_data", lambda _p, _tf: _make_df(n=80))

    def _boom(_df):
        raise AssertionError("analyze_structural_noise must not be called when calibration is passed")

    monkeypatch.setattr(optimizer, "analyze_structural_noise", _boom)

    result = run_optimize(
        OptimizerRequest(pair=_PAIR, mode="OPTIMIZE", search_space=_space()),
        calibration=_calibration(),
    )

    assert result.n_candidates == 44


_FLAT = 100.0  # every leg opens here, so marking at this price adds nothing


def _op(time: str, cum_pnl: float, price: float = _FLAT, side: str = "buy"):
    """Minimal stand-in for an engine Operation."""
    return types.SimpleNamespace(time=time, cum_pnl=cum_pnl, pnl_abs=1.0, price=price, side=side)


def _split(ops, boundary: str, boundary_price: float = _FLAT, final_price: float = _FLAT):
    """Split with both halves marked at the price their legs opened at: no open-leg effect."""
    return optimizer._split_scores_from_single_run(ops, boundary, boundary_price, final_price)


def test_split_second_half_compounds_instead_of_subtracting() -> None:
    """cum_pnl compounds, so the second half is a ratio of growth factors, not a subtraction."""
    ops = [_op("2026-01-01 00:00", 50.0), _op("2026-01-02 00:00", 100.0)]

    train, test = _split(ops, "2026-01-02 00:00")

    assert train.total_pnl == pytest.approx(50.0)
    assert test.total_pnl == pytest.approx(100.0 / 3.0)


def test_split_second_half_magnifies_a_loss_after_a_losing_train() -> None:
    """A losing train half shrinks the base, so the same drop is a larger percentage of what is left."""
    ops = [_op("2026-01-01 00:00", -20.0), _op("2026-01-02 00:00", -40.0)]

    _train, test = _split(ops, "2026-01-02 00:00")

    assert test.total_pnl == pytest.approx(-25.0)


def test_split_second_half_survives_a_wiped_out_train_half() -> None:
    """A train half at -100% leaves nothing to compound, so report a total loss, not a division by zero."""
    ops = [_op("2026-01-01 00:00", -100.0), _op("2026-01-02 00:00", -100.0)]

    _train, test = _split(ops, "2026-01-02 00:00")

    assert test.total_pnl == pytest.approx(-100.0)


def test_split_with_no_train_ops_reports_the_whole_run_as_the_second_half() -> None:
    """With no op before the boundary, first_net is 0, so the second half equals the full run."""
    ops = [_op("2026-02-01 00:00", 12.5)]

    train, test = _split(ops, "2026-01-01 00:00")

    assert train.total_pnl == pytest.approx(0.0)
    assert test.total_pnl == pytest.approx(12.5)


def test_split_values_each_half_at_the_price_where_that_half_ends() -> None:
    """A run never stops flat, so each half books the leg still open when it ends."""
    ops = [_op("2026-01-01 00:00", 0.0), _op("2026-01-02 00:00", 0.0)]

    # Long from 100 at both ends: the train half is marked at 110, the whole run at 121.
    train, test = _split(ops, "2026-01-02 00:00", boundary_price=110.0, final_price=121.0)

    assert train.total_pnl == pytest.approx(10.0)
    assert test.total_pnl == pytest.approx(10.0)  # 1.21 / 1.10 - 1


def test_split_marks_nothing_onto_a_half_that_ends_in_cash() -> None:
    """A half ending on a sell holds euros, so the price it is marked at changes nothing."""
    ops = [_op("2026-01-01 00:00", 0.0, side="sell")]

    train, _test = _split(ops, "2026-01-02 00:00", boundary_price=110.0, final_price=110.0)

    assert train.total_pnl == pytest.approx(0.0)


# --- calibration schedule ---------------------------------------------------


_LEVELS = ("LL", "LV", "MV", "HV", "HH")


def _k_arrays(value: float) -> dict:
    return {lvl: np.array([value]) for lvl in _LEVELS}


def test_engine_config_carries_no_schedule_when_no_point_is_supplied() -> None:
    cand = optimizer.Candidate(k_act=1.0, min_margin=None, stop_pcts=dict.fromkeys(_LEVELS, 0.5))

    cfg = optimizer._build_engine_config("XBTEUR", cand, (0.1, 0.2, 0.3, 0.4), _k_arrays(3.0), _k_arrays(4.0), 0.2)

    assert cfg.calibration_schedule == ()


def test_engine_config_applies_the_candidate_percentiles_to_every_scheduled_point() -> None:
    """A point carries raw K values; the candidate's percentiles are what turn them into K_STOP."""
    cand = optimizer.Candidate(k_act=1.0, min_margin=None, stop_pcts=dict.fromkeys(_LEVELS, 0.5))
    points = (
        market_analyzer.CalibrationInputs(0, (0.1, 0.2, 0.3, 0.4), _k_arrays(3.0), _k_arrays(4.0)),
        market_analyzer.CalibrationInputs(7, (0.5, 0.6, 0.7, 0.8), _k_arrays(9.0), _k_arrays(9.0)),
    )

    cfg = optimizer._build_engine_config(
        "XBTEUR", cand, (0.1, 0.2, 0.3, 0.4), _k_arrays(3.0), _k_arrays(4.0), 0.2, points
    )

    assert [at for at, _ in cfg.calibration_schedule] == [0, 7]
    assert cfg.calibration_schedule[0][1].k_stop_sell["MV"] == 3.0
    assert cfg.calibration_schedule[1][1].k_stop_sell["MV"] == 9.0
    assert cfg.calibration_schedule[1][1].atr_ratio_p20 == 0.5


# --- enumeration ------------------------------------------------------------


def test_enumeration_covers_the_whole_product_of_both_branches() -> None:
    """No sampler, no seed: every point of every grid is evaluated, exactly once."""
    cands = enumerate_candidates(_space())

    assert len(cands) == 44  # 5 k_act x 4 stops, plus 6 min_margin x 4 stops
    assert len({(c.k_act, c.min_margin, tuple(sorted(c.stop_pcts.items()))) for c in cands}) == 44


def test_enumeration_shares_one_stop_pct_across_the_five_levels() -> None:
    """Five free values are unidentified at these operation counts; the search does not
    converge on them (0/4 seeds after 12 000 trials), so one shared value is what ships."""
    for cand in enumerate_candidates(_space()):
        assert len(set(cand.stop_pcts.values())) == 1
        assert set(cand.stop_pcts) == set(_LEVELS)


def test_enumeration_drops_a_branch_whose_grid_is_none() -> None:
    assert all(c.k_act is None for c in enumerate_candidates(_space(k_act=False)))
    assert all(c.min_margin is None for c in enumerate_candidates(_space(min_margin=False)))


def test_enumeration_is_deterministic() -> None:
    """The ranking is reproducible because the enumeration is, not because a seed was fixed."""
    first = enumerate_candidates(_space())
    second = enumerate_candidates(_space())

    assert [(c.k_act, c.min_margin, sorted(c.stop_pcts.items())) for c in first] == [
        (c.k_act, c.min_margin, sorted(c.stop_pcts.items())) for c in second
    ]


def test_a_fixed_dimension_enumerates_to_a_single_value() -> None:
    """start == end fixes a dimension rather than disabling it."""
    space = SearchSpace(stop_pcts=GridSpec(0.9, 0.9, 0.1), k_act=None, min_margin=GridSpec(0.05, 0.05, 1.0))
    cands = enumerate_candidates(space)

    assert len(cands) == 1
    assert cands[0].min_margin == 0.05
    assert set(cands[0].stop_pcts.values()) == {0.9}
