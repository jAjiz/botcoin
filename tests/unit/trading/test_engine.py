"""Behavioral tests for the pure engine.

Each test pins one hand-reasoned behavior on the smallest fixture that can show it:
the pure helpers are called directly, and only the loop behaviors that need a
sequence of bars build a frame. A failure names the behavior that changed.
"""

import pandas as pd
import pytest

import trading.engine as engine

_LEVELS = ("LL", "LV", "MV", "HV", "HH")

# One full round trip with no fee: buy 100 -> sell 108 -> buy 92 -> sell 118.
# ATR is 2.0 and every K_STOP is 1.0, so each stop sits 2.0 from the trailed extreme.
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
    """Build an OHLC frame from (high, low, price) rows.

    Prices sit around 100, so an ATR of 2.0 reads as a 0.02 ATR/close ratio against
    the ratio percentiles ``_cfg`` supplies. ``price_column`` names the column the
    engine reads the bar's reference price from (``None`` omits it)."""
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
    # At close=100 these ATRs read as ratios 0.005/0.02/0.04/0.06/0.08 against the
    # thresholds (.01,.03,.05,.07), so each one lands on a different level.
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
    assert engine.activation_price(cfg, side, 100.0, 2.0) == pytest.approx(expected)


# --- simulate_operations ---------------------------------------------------


def test_round_trip_alternates_sides_and_compounds_cum_pnl() -> None:
    ops = engine.simulate_operations(_df(_ROUND_TRIP), _cfg())

    assert [(op.idx, op.side, op.price) for op in ops] == [
        (1, "buy", 100.0),
        (2, "sell", 108.0),
        (3, "buy", 92.0),
        (4, "sell", 118.0),
    ]
    # A buy leg closes a short: PnL is entry - exit, the mirror of the sell leg.
    assert [op.pnl_abs for op in ops] == pytest.approx([0.0, 8.0, 16.0, 26.0])
    assert [op.pnl_pct for op in ops] == pytest.approx([0.0, 8.0, 16 / 108 * 100, 26 / 92 * 100])
    # cum_pnl compounds the per-leg returns: 1.08 * (124/108) * (118/92) - 1.
    assert [op.cum_pnl for op in ops] == pytest.approx([0.0, 8.0, 24.0, 59.0434783])


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
    # k_act=1.0 and ATR=2.0, so both the activation distance and the re-anchor
    # threshold are 2.0. Without re-anchoring the first frame never activates.
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
    # The stored ATR (2.0) sits outside the second bar's +/-20% band around 1.0, so
    # both prices are re-derived. Held at 2.0 neither frame produces an exit.
    ops = engine.simulate_operations(_df(rows, atr=[2.0, 1.0]), _cfg(k_act=k_act))

    assert len(ops) == 2
    assert ops[1].price == pytest.approx(expected_exit)


@pytest.mark.parametrize("price_column", ["open", None])
def test_bar_price_falls_back_to_open_then_to_the_high_low_midpoint(price_column: str | None) -> None:
    # With no close column the reference price is the open, then the high/low midpoint.
    ops = engine.simulate_operations(_df(_ROUND_TRIP[:2], price_column=price_column), _cfg())

    assert ops[0].price == 100.0
    assert ops[1].price == 108.0
