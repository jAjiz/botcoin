"""Behavioral tests for the pure engine, one behavior per test."""

import dataclasses

import pandas as pd
import pytest

import trading.engine as engine

_LEVELS = ("LL", "LV", "MV", "HV", "HH")

# No-fee round trip: buy 100 -> sell 108 -> buy 92 -> sell 118. ATR=2.0, K_STOP=1.0; stop sits 2.0 from trailed extreme.
_ROUND_TRIP = [
    (100.0, 100.0, 100.0),  # k_act=0 activates at once; trailing 100, stop 98
    (110.0, 105.0, 108.0),  # trailing 110, stop 108; low 105 <= 108 -> sell @108
    (109.0, 90.0, 95.0),  # short from 108; trailing 90, stop 92; high 109 >= 92 -> buy @92
    (120.0, 118.0, 119.0),  # trailing 120, stop 118; low 118 <= 118 -> sell @118
]


def _df(
    rows: list[tuple[float, float, float]],
    atr: float | list[float] = 2.0,
    price_column: str | None = "close",
) -> pd.DataFrame:
    """Build an OHLC frame from (high, low, price) rows; ``price_column`` names the price column.

    Prices sit near 100, so the default ATR of 2.0 reads as a 0.02 ratio: level LV under ``_cfg``.
    """
    data: dict[str, list] = {
        "dtime": [f"t{i}" for i in range(len(rows))],
        "high": [r[0] for r in rows],
        "low": [r[1] for r in rows],
        "atr": list(atr) if isinstance(atr, list) else [atr] * len(rows),
    }
    if price_column is not None:
        data[price_column] = [r[2] for r in rows]
    return pd.DataFrame(data)


def _cfg(
    percentiles: tuple[float, float, float, float] = (0.01, 0.03, 0.05, 0.07),
    k_buy: dict[str, float | None] | None = None,
    k_sell: dict[str, float | None] | None = None,
    k_act: float | None = 0.0,
    min_margin: float = 0.0,
    atr_desv_limit: float = 0.2,
) -> engine.EngineConfig:
    return engine.EngineConfig(
        pair="T",
        calibration=engine.PairCalibration(
            atr_ratio_p20=percentiles[0],
            atr_ratio_p50=percentiles[1],
            atr_ratio_p80=percentiles[2],
            atr_ratio_p95=percentiles[3],
            k_stop_buy=k_buy if k_buy is not None else dict.fromkeys(_LEVELS, 1.0),
            k_stop_sell=k_sell if k_sell is not None else dict.fromkeys(_LEVELS, 1.0),
        ),
        k_act=k_act,
        min_margin=min_margin,
        atr_desv_limit=atr_desv_limit,
    )


# --- lookup_k_stop ---------------------------------------------------------


@pytest.mark.parametrize(
    ("atr", "expected"),
    [
        pytest.param(0.5, 1.0, id="LL"),
        pytest.param(2.0, 2.0, id="LV"),
        pytest.param(4.0, 3.0, id="MV"),
        pytest.param(6.0, 4.0, id="HV"),
        pytest.param(8.0, 5.0, id="HH"),
    ],
)
def test_lookup_k_stop_returns_the_k_of_the_classified_level(atr: float, expected: float) -> None:
    # ATRs 0.005/0.02/0.04/0.06/0.08 (close=100) vs thresholds (.01,.03,.05,.07): each lands on a different level.
    cfg = _cfg(k_sell=dict(zip(_LEVELS, (1.0, 2.0, 3.0, 4.0, 5.0), strict=True)))
    assert engine.lookup_k_stop(cfg, "sell", atr, 100.0) == expected


@pytest.mark.parametrize(
    ("k_sell", "k_buy", "expected"),
    [
        pytest.param({**dict.fromkeys(_LEVELS, None), "MV": 1.7}, None, 1.7, id="same-side"),
        pytest.param(None, {**dict.fromkeys(_LEVELS, None), "MV": 3.3}, 3.3, id="opposite-side"),
        pytest.param({**dict.fromkeys(_LEVELS, None), "HV": 2.5}, None, 2.5, id="neighbor-level"),
        pytest.param(None, None, None, id="all-missing"),
    ],
)
def test_lookup_k_stop_falls_back_same_side_then_opposite_then_neighbor(
    k_sell: dict[str, float | None] | None,
    k_buy: dict[str, float | None] | None,
    expected: float | None,
) -> None:
    # ATR=2.5 at close=100 => ratio 0.025; thresholds (.01,.02,.03,.04) => level MV.
    # An unset side means "no K anywhere on that side".
    cfg = _cfg(
        percentiles=(0.01, 0.02, 0.03, 0.04),
        k_sell=k_sell if k_sell is not None else dict.fromkeys(_LEVELS, None),
        k_buy=k_buy if k_buy is not None else dict.fromkeys(_LEVELS, None),
    )
    assert engine.lookup_k_stop(cfg, "sell", 2.5, 100.0) == expected


# --- activation price ------------------------------------------------------


@pytest.mark.parametrize(
    ("k_act", "side", "expected"),
    [
        # k_act set: distance = 2.0 * ATR = 4.0, and min_margin is ignored.
        (2.0, "sell", 104.0),
        (2.0, "buy", 96.0),
        # k_act unset: distance = K_STOP(1.0) * ATR(2.0) + min_margin(0.01) * 100 = 3.0.
        (None, "sell", 103.0),
        (None, "buy", 97.0),
    ],
)
def test_activation_price_uses_k_act_when_set_else_k_stop_plus_min_margin(
    k_act: float | None, side: str, expected: float
) -> None:
    cfg = _cfg(k_act=k_act, min_margin=0.01)
    assert engine.activation_price(cfg, side, 100.0, 2.0, 100.0) == pytest.approx(expected)


def test_stop_price_anchors_on_the_trailing_price_but_classifies_with_close() -> None:
    # With ATR 2.0 the trailing price reads LV (2/100) while the close reads MV (2/60).
    cfg = _cfg(
        percentiles=(0.01, 0.03, 0.05, 0.07),
        k_sell={"LL": 1.0, "LV": 2.0, "MV": 5.0, "HV": 4.0, "HH": 3.0},
    )

    assert engine.stop_price(cfg, "sell", 100.0, 2.0, 60.0) == pytest.approx(90.0)


# --- simulate_operations ---------------------------------------------------


def test_round_trip_alternates_sides_and_compounds_cum_pnl() -> None:
    ops = engine.simulate_operations(_df(_ROUND_TRIP), _cfg())

    assert [(op.idx, op.side, op.price) for op in ops] == [
        (1, "buy", 100.0),
        (2, "sell", 108.0),
        (3, "buy", 92.0),
        (4, "sell", 118.0),
    ]
    # A buy leg closes a cash leg: euros held do not move, so it books nothing.
    assert [op.pnl_abs for op in ops] == pytest.approx([0.0, 8.0, 0.0, 26.0])
    assert [op.pnl_pct for op in ops] == pytest.approx([0.0, 8.0, 0.0, 26 / 92 * 100])
    # cum_pnl compounds the long legs only: 1.08 * (118/92) - 1.
    assert [op.cum_pnl for op in ops] == pytest.approx([0.0, 8.0, 8.0, 38.5217391])


def test_an_exit_row_reports_the_k_stop_of_its_own_level() -> None:
    # The stop fires 10 below the bar price, and the p50 boundary sits between the two
    # ratios: the bar reads LV (2/100) while the execution price reads MV (2/90).
    cfg = _cfg(
        percentiles=(0.01, 0.021, 0.05, 0.07),
        k_sell={"LL": 1.0, "LV": 5.0, "MV": 3.0, "HV": 4.0, "HH": 2.0},
    )
    rows = [(100.0, 100.0, 100.0), (100.0, 80.0, 100.0)]

    exit_op = engine.simulate_operations(_df(rows), cfg)[1]

    assert exit_op.price == 90.0
    assert exit_op.vol == "LV"
    assert exit_op.k_stop == 5.0


def test_entry_and_exit_legs_record_their_own_fee() -> None:
    ops = engine.simulate_operations(_df(_ROUND_TRIP[:2]), _cfg(), fee_rate=0.01)

    entry, exit_ = ops
    # The entry leg has no counterpart yet, so it books its fee as a negative PnL.
    assert (entry.fee_abs, entry.pnl_abs, entry.pnl_pct) == pytest.approx((1.0, -1.0, -1.0))
    assert exit_.fee_abs == pytest.approx(1.08)
    assert exit_.pnl_abs == pytest.approx(8.0 - 1.08)  # gross 8 minus the exit fee
    assert exit_.vol == "LV"  # the exit leg is classified at its own bar: 2.0 / 108
    assert exit_.k_stop == 1.0


def test_max_ops_caps_operation_count() -> None:
    assert len(engine.simulate_operations(_df(_ROUND_TRIP), _cfg(), max_ops=2)) == 2


@pytest.mark.parametrize(
    ("atr", "expected_ops"),
    [
        pytest.param([0.0, 0.0], 0, id="no-valid-atr-at-all"),
        # The bar that would have triggered the exit has no ATR, so it is skipped.
        pytest.param([2.0, 0.0], 1, id="invalid-bar-skipped-mid-stream"),
    ],
)
def test_bars_without_a_valid_atr_are_skipped(atr: list[float], expected_ops: int) -> None:
    assert len(engine.simulate_operations(_df(_ROUND_TRIP[:2], atr=atr), _cfg())) == expected_ops


@pytest.mark.parametrize(
    ("rows", "expected_exit"),
    [
        pytest.param(
            [
                (100.0, 100.0, 100.0),  # buy at 100; sell activation target = 102
                (97.0, 97.0, 97.0),  # gap 102 - 97 = 5 > 2 -> re-anchor activation to 99
                (100.0, 99.5, 99.8),  # high 100 >= 99 -> activates; stop = 100 - 2 = 98
                (101.0, 95.0, 98.0),  # trailing 101, stop 99; low 95 -> sell at 99
            ],
            99.0,
            id="drifted-away",
        ),
        pytest.param(
            [
                (100.0, 100.0, 100.0),  # buy at 100; sell activation target = 102
                (101.0, 100.5, 100.8),  # gap 102 - 100.8 = 1.2 < 2 -> no re-anchor
                (103.0, 101.5, 102.5),  # high 103 >= 102 -> activates; stop = 103 - 2 = 101
                (104.0, 99.0, 101.5),  # trailing 104, stop 102; low 99 -> sell at 102
            ],
            102.0,
            id="still-within-one-distance",
        ),
    ],
)
def test_activation_reanchors_only_once_price_drifts_a_full_distance_away(
    rows: list[tuple[float, float, float]], expected_exit: float
) -> None:
    # k_act=1.0, ATR=2.0: both thresholds are 2.0; without re-anchoring the first frame never activates.
    ops = engine.simulate_operations(_df(rows), _cfg(k_act=1.0))

    assert len(ops) == 2
    assert ops[1].price == pytest.approx(expected_exit)


@pytest.mark.parametrize(
    ("k_act", "rows", "expected_exit"),
    [
        pytest.param(
            1.0,
            [
                (100.0, 100.0, 100.0),  # buy at 100; activation = 100 + 1.0*2.0 = 102
                (101.0, 100.0, 100.0),  # ATR halves -> activation re-derived at 101; high 101 activates
            ],
            100.0,  # trailing 101, stop = 101 - 1.0*1.0 = 100; low 100 -> sell at 100
            id="activation-recalculated-before-activating",
        ),
        pytest.param(
            0.0,
            [
                (100.0, 100.0, 100.0),  # buy at 100, activates at once; stop = 100 - 2.0 = 98
                (100.0, 99.0, 100.0),  # ATR halves -> stop tightens to 99; low 99 -> sell at 99
            ],
            99.0,
            id="stop-recalculated-after-activating",
        ),
    ],
)
def test_atr_drift_beyond_the_limit_recalculates_activation_and_stop(
    k_act: float, rows: list[tuple[float, float, float]], expected_exit: float
) -> None:
    # ATR (2.0) sits outside the +/-20% band around 1.0, so both prices re-derive; held flat, neither bar exits.
    ops = engine.simulate_operations(_df(rows, atr=[2.0, 1.0]), _cfg(k_act=k_act))

    assert len(ops) == 2
    assert ops[1].price == pytest.approx(expected_exit)


@pytest.mark.parametrize("price_column", ["open", None])
def test_bar_price_falls_back_to_open_then_to_the_high_low_midpoint(price_column: str | None) -> None:
    # With no close column the reference price is the open, then the high/low midpoint.
    ops = engine.simulate_operations(_df(_ROUND_TRIP[:2], price_column=price_column), _cfg())

    assert ops[0].price == 100.0
    assert ops[1].price == 108.0


# --- calibration schedule --------------------------------------------------


def _cal(k: float, percentiles: tuple[float, float, float, float] = (0.01, 0.03, 0.05, 0.07)):
    """A calibration whose every level, both sides, carries the same K_STOP."""
    return engine.PairCalibration(
        atr_ratio_p20=percentiles[0],
        atr_ratio_p50=percentiles[1],
        atr_ratio_p80=percentiles[2],
        atr_ratio_p95=percentiles[3],
        k_stop_buy=dict.fromkeys(_LEVELS, k),
        k_stop_sell=dict.fromkeys(_LEVELS, k),
    )


def _with_schedule(cfg: engine.EngineConfig, schedule) -> engine.EngineConfig:
    return dataclasses.replace(cfg, calibration_schedule=tuple(schedule))


def test_calibration_at_returns_the_last_entry_at_or_before_the_bar() -> None:
    # The schedule is a step function: an entry holds until the next one starts.
    second, third = _cal(2.0), _cal(3.0)
    cfg = _with_schedule(_cfg(), [(2, second), (5, third)])
    base = cfg.calibration

    assert engine._calibration_at(cfg, 0) is base
    assert engine._calibration_at(cfg, 1) is base
    assert engine._calibration_at(cfg, 2) is second
    assert engine._calibration_at(cfg, 4) is second
    assert engine._calibration_at(cfg, 5) is third
    assert engine._calibration_at(cfg, 99) is third


def test_a_scheduled_recalibration_widens_the_stop_from_its_bar_on() -> None:
    # K_STOP 1.0 -> 2.0 at bar 2: the short exits at 94, and the wider stop survives bar 3.
    df = _df(_ROUND_TRIP)
    cfg = _cfg()

    plain = engine.simulate_operations(df, cfg)
    scheduled = engine.simulate_operations(df, _with_schedule(cfg, [(2, _cal(2.0))]))

    assert [op.price for op in plain] == [100.0, 108.0, 92.0, 118.0]
    assert [op.price for op in scheduled] == [100.0, 108.0, 94.0]
    assert [op.k_stop for op in scheduled] == [1.0, 1.0, 2.0]


def test_a_recalibration_does_not_reprice_a_stop_already_resting() -> None:
    # Mirrors tick_position: only ATR drift or a new extreme re-prices a stop, never a recalibration.
    rows = [(100.0, 100.0, 100.0), (100.0, 99.0, 100.0), (100.0, 97.0, 99.0)]
    cfg = _cfg()

    ops = engine.simulate_operations(_df(rows), _with_schedule(cfg, [(1, _cal(3.0))]))

    # Bar 0 priced the stop at 98.0 with K=1.0; K=3.0 would have put it at 94.0.
    assert ops[1].price == 98.0
    # k_stop names the level of the exit bar, not the K that priced the stop.
    assert ops[1].k_stop == 3.0


def test_a_schedule_entry_due_on_a_skipped_bar_applies_at_the_next_usable_one() -> None:
    # Bar 2 has no usable ATR, yet bar 3 already buys on the K_STOP scheduled for bar 2.
    rows = [(100.0, 100.0, 100.0), (110.0, 105.0, 108.0), (109.0, 90.0, 95.0), (120.0, 100.0, 105.0)]
    df = _df(rows, atr=[2.0, 2.0, 0.0, 2.0])
    cfg = _cfg()

    plain = engine.simulate_operations(df, cfg)
    scheduled = engine.simulate_operations(df, _with_schedule(cfg, [(2, _cal(2.0))]))

    assert [op.price for op in plain] == [100.0, 108.0, 102.0]
    assert [op.price for op in scheduled] == [100.0, 108.0, 104.0]


def test_the_first_operation_uses_the_calibration_of_its_own_bar() -> None:
    # The opening BUY lands on bar 1, so a recalibration scheduled at bar 1 applies to it.
    df = _df(_ROUND_TRIP, atr=[0.0, 2.0, 2.0, 2.0])
    cfg = _cfg()

    ops = engine.simulate_operations(df, _with_schedule(cfg, [(1, _cal(4.0))]))

    assert ops[0].time == "t1"
    assert ops[0].k_stop == 4.0


# --- mark_to_market --------------------------------------------------------


@pytest.mark.parametrize(
    ("rows", "final", "expected"),
    [
        # Ends long from 100 (only the opening buy): +20% unrealized.
        pytest.param([(100.0, 100.0, 100.0)], 120.0, 20.0, id="open-long-gains"),
        pytest.param([(100.0, 100.0, 100.0)], 80.0, -20.0, id="open-long-loses"),
        # Ends holding euros after the round trip's first exit: nothing left to value.
        pytest.param(_ROUND_TRIP[:2], 120.0, 8.0, id="open-cash-adds-nothing"),
    ],
)
def test_mark_to_market_values_the_position_the_run_ended_on(
    rows: list[tuple[float, float, float]], final: float, expected: float
) -> None:
    ops = engine.simulate_operations(_df(rows), _cfg())

    assert engine.mark_to_market(ops, final) == pytest.approx(expected)


def test_mark_to_market_of_a_run_with_no_operations_is_zero() -> None:
    assert engine.mark_to_market([], 120.0) == 0.0


def test_mark_to_market_of_a_priceless_operation_keeps_the_realized_total() -> None:
    # A zero price has no base to compute a return against, so report only what was booked.
    priceless = engine.Operation(1, "t0", "buy", 0.0, "LV", 1.0, 0.0, None, None, 7.5)

    assert engine.mark_to_market([priceless], 120.0) == 7.5
