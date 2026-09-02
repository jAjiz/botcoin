"""Pure trading simulation engine.

Leaf module: no import from ``core.config`` or ``trading.parameters_manager``.
"""

from dataclasses import dataclass

import numpy as np

LEVELS = ("LL", "LV", "MV", "HV", "HH")


@dataclass(frozen=True)
class PairCalibration:
    # Percentiles of ATR/close, not of ATR: a level means the same thing at any price.
    atr_ratio_p20: float
    atr_ratio_p50: float
    atr_ratio_p80: float
    atr_ratio_p95: float
    k_stop_buy: dict[str, float | None]  # {level: k}
    k_stop_sell: dict[str, float | None]


@dataclass(frozen=True)
class EngineConfig:
    """Everything a simulation needs, with no module-level globals.

    ``calibration_schedule`` mirrors the live recalibration every ``PARAM_SESSIONS``
    ticks: ``(bar index, calibration in force from that bar on)``, ascending. An
    empty schedule keeps ``calibration`` for the whole run.
    """

    pair: str
    calibration: PairCalibration
    k_act: float | None
    min_margin: float
    atr_desv_limit: float
    calibration_schedule: tuple[tuple[int, PairCalibration], ...] = ()


@dataclass(frozen=True)
class Operation:
    idx: int
    time: str
    side: str  # "buy" | "sell"
    price: float
    vol: str
    k_stop: float
    fee_abs: float
    pnl_abs: float | None
    pnl_pct: float | None
    cum_pnl: float | None


def _vol_level_from_atr(atr_val: float, close: float, p20: float, p50: float, p80: float, p95: float) -> str:
    """Classify ATR relative to price. Mirrors parameters_manager.get_volatility_level."""
    ratio = atr_val / close if close else 0.0
    if ratio < p20:
        return "LL"
    if ratio < p50:
        return "LV"
    if ratio < p80:
        return "MV"
    if ratio < p95:
        return "HV"
    return "HH"


def _pnl_abs(prev_side: str, prev_price: float, curr_price: float) -> float:
    """EUR move of the leg opened by ``prev_side``; a cash leg holds euros, so its balance does not move."""
    return curr_price - prev_price if prev_side == "buy" else 0.0


def _calibration_at(cfg: EngineConfig, idx: int) -> PairCalibration:
    """The calibration in force at bar ``idx``: the last scheduled entry at or before it."""
    cal = cfg.calibration
    for at, scheduled in cfg.calibration_schedule:
        if at > idx:
            break
        cal = scheduled
    return cal


def _k_for_level(cal: PairCalibration, side: str, vol: str) -> float | None:
    """Resolve K_STOP for an already-classified level: same side, then opposite, then nearest neighbours."""
    same = cal.k_stop_sell if side == "sell" else cal.k_stop_buy
    opp = cal.k_stop_buy if side == "sell" else cal.k_stop_sell

    k_stop = same.get(vol)
    if k_stop is not None:
        return k_stop

    k_stop = opp.get(vol)
    if k_stop is not None:
        return k_stop

    idx = LEVELS.index(vol)
    for offset in range(1, len(LEVELS)):
        for neighbor in (idx - offset, idx + offset):
            if 0 <= neighbor < len(LEVELS):
                k_stop = same.get(LEVELS[neighbor])
                if k_stop is not None:
                    return k_stop

    return None


def lookup_k_stop(
    cfg: EngineConfig, side: str, atr_val: float, close: float, cal: PairCalibration | None = None
) -> float | None:
    """Resolve K_STOP for a side/ATR, classifying ``close`` into a level first.

    ``cal`` overrides ``cfg.calibration`` for callers that walk a schedule.
    """
    if cal is None:
        cal = cfg.calibration
    vol = _vol_level_from_atr(
        atr_val, close, cal.atr_ratio_p20, cal.atr_ratio_p50, cal.atr_ratio_p80, cal.atr_ratio_p95
    )
    return _k_for_level(cal, side, vol)


def activation_distance(
    cfg: EngineConfig,
    side: str,
    reference_price: float,
    atr_val: float,
    close: float,
    cal: PairCalibration | None = None,
) -> float:
    """``reference_price`` anchors the distance; ``close`` classifies the level."""
    k_act = cfg.k_act
    if k_act is not None:
        return float(k_act) * atr_val
    k_stop = lookup_k_stop(cfg, side, atr_val, close, cal) or 0.0
    return float(k_stop) * atr_val + (cfg.min_margin * reference_price)


def activation_price(
    cfg: EngineConfig,
    side: str,
    entry_price: float,
    atr_val: float,
    close: float,
    cal: PairCalibration | None = None,
) -> float:
    distance = activation_distance(cfg, side, entry_price, atr_val, close, cal)
    if side == "sell":
        return entry_price + distance
    return entry_price - distance


def stop_price(
    cfg: EngineConfig,
    side: str,
    trailing_price: float,
    atr_val: float,
    close: float,
    cal: PairCalibration | None = None,
) -> float:
    """``trailing_price`` anchors the stop; ``close`` classifies the level."""
    k_stop = lookup_k_stop(cfg, side, atr_val, close, cal) or 0.0
    stop_distance = float(k_stop) * atr_val
    if side == "sell":
        return trailing_price - stop_distance
    return trailing_price + stop_distance


def _opposite(side: str) -> str:
    return "buy" if side == "sell" else "sell"


def _leg_pct(prev: Operation, pnl: float, fee_rate: float) -> float | None:
    """Return of the leg ending here; a cash leg only pays its fee, charged on the euros it spends."""
    if prev.side != "buy":
        return -float(fee_rate) * 100.0
    return (pnl / prev.price) * 100 if prev.price else None


def _record_stop_exit(
    ops: list[Operation],
    cal: PairCalibration,
    side: str,
    exec_price: float,
    dtime: str,
    vol: str,
    fee_rate: float,
    cum_pnl: float,
) -> float:
    """Append the exit leg for ``side`` and return the updated cumulative PnL; only a long leg books a price move."""
    prev = ops[-1]
    fee = float(exec_price) * float(fee_rate)
    pnl = _pnl_abs(prev.side, prev.price, exec_price) - fee
    pnl_pct = _leg_pct(prev, pnl, fee_rate)
    if pnl_pct is not None:
        cum_factor = (1.0 + (cum_pnl / 100.0)) * (1.0 + (float(pnl_pct) / 100.0))
        cum_pnl = (cum_factor - 1.0) * 100.0
    # The level of this row's own bar, so `vol` and `k_stop` agree; `exec_price` may predate both.
    k_used = _k_for_level(cal, side, vol) or 0.0
    ops.append(
        Operation(
            idx=len(ops) + 1,
            time=dtime,
            side=side,
            price=float(exec_price),
            vol=vol,
            k_stop=float(k_used),
            fee_abs=float(fee),
            pnl_abs=float(pnl),
            pnl_pct=float(pnl_pct) if pnl_pct is not None else None,
            cum_pnl=float(cum_pnl),
        )
    )
    return cum_pnl


def mark_to_market(ops: list[Operation], final_price: float) -> float:
    """Cumulative return with the still-open position valued at ``final_price``.

    A leg is booked only when it closes, so a run that ends mid-position reports
    only what it realized: the move since the last operation is missing. A run
    ending in euros has nothing to value, so only an open long leg adds anything.
    This is a valuation, not a liquidation — the position carries on past the end
    of the window, so it is charged no exit fee.
    """
    if not ops:
        return 0.0
    last = ops[-1]
    cum = float(last.cum_pnl) if last.cum_pnl is not None else 0.0
    if not last.price:
        return cum
    leg_pct = (_pnl_abs(last.side, last.price, float(final_price)) / last.price) * 100.0
    return ((1.0 + (cum / 100.0)) * (1.0 + (leg_pct / 100.0)) - 1.0) * 100.0


def _price_of(row, has_close: bool, has_open: bool) -> float:
    """Reference price of a bar: close, else open, else the high/low midpoint."""
    if has_close:
        return float(row.close)
    if has_open:
        return float(row.open)
    return (float(row.high) + float(row.low)) / 2.0


def simulate_operations(
    df,
    cfg: EngineConfig,
    fee_rate: float = 0.0,
    max_ops: int | None = None,
) -> list[Operation]:
    schedule = cfg.calibration_schedule

    ops: list[Operation] = []
    cum_pnl = 0.0  # cumulative return in percent, compounded

    # Resolved once: itertuples yields namedtuples, which have no membership test.
    has_close = "close" in df.columns
    has_open = "open" in df.columns

    # The simulation always opens with a BUY at the first bar with a valid ATR.
    first_idx, first_row = 0, None
    for idx, row in enumerate(df.itertuples(index=False)):
        atr = float(row.atr)
        if atr > 0 and not np.isnan(atr):
            first_idx, first_row = idx, row
            break
    if first_row is None:
        return ops

    cal = _calibration_at(cfg, first_idx)
    first_atr = float(first_row.atr)
    first_price = _price_of(first_row, has_close, has_open)
    first_time = str(first_row.dtime)
    first_vol = _vol_level_from_atr(
        first_atr, first_price, cal.atr_ratio_p20, cal.atr_ratio_p50, cal.atr_ratio_p80, cal.atr_ratio_p95
    )
    first_k = _k_for_level(cal, "buy", first_vol) or 0.0
    first_fee = float(first_price) * float(fee_rate)
    # The entry fee is an immediate negative return of fee_rate * 100 percent.
    cum_pnl -= float(fee_rate) * 100.0
    ops.append(
        Operation(
            idx=1,
            time=first_time,
            side="buy",
            price=first_price,
            vol=first_vol,
            k_stop=float(first_k),
            fee_abs=float(first_fee),
            pnl_abs=-float(first_fee),
            pnl_pct=-float(fee_rate) * 100.0,
            cum_pnl=float(cum_pnl),
        )
    )

    side = "sell"
    entry_price = first_price
    active = False
    activation_px = None
    activation_atr = None
    trailing_price = None
    stop_px = None
    stop_atr = None

    cal = cfg.calibration
    next_change = 0

    for idx, row in enumerate(df.itertuples(index=False)):
        # `<= idx` so an entry due on a bar the loop skips still applies at the next usable one.
        while next_change < len(schedule) and schedule[next_change][0] <= idx:
            cal = schedule[next_change][1]
            next_change += 1

        atr = float(row.atr)
        if atr <= 0 or np.isnan(atr):
            continue

        high = float(row.high)
        low = float(row.low)
        dtime = str(row.dtime)
        price = _price_of(row, has_close, has_open)
        vol = _vol_level_from_atr(
            atr, price, cal.atr_ratio_p20, cal.atr_ratio_p50, cal.atr_ratio_p80, cal.atr_ratio_p95
        )

        atr_limit_max = atr * (1 + cfg.atr_desv_limit)
        atr_limit_min = atr * (1 - cfg.atr_desv_limit)

        if activation_px is None:
            activation_px = activation_price(cfg, side, entry_price, atr, price, cal)
            activation_atr = atr

        if not active:
            if activation_atr is not None and (activation_atr < atr_limit_min or activation_atr > atr_limit_max):
                activation_px = activation_price(cfg, side, entry_price, atr, price, cal)
                activation_atr = atr

            # Mirrors positions_manager.reanchor_activation_price: stored ATR, not the bar ATR.
            exp_dist = activation_distance(cfg, side, price, activation_atr, price, cal)
            gap = (activation_px - price) if side == "sell" else (price - activation_px)
            if gap > exp_dist:
                activation_px = activation_price(cfg, side, price, activation_atr, price, cal)

            # A sell activates on the high crossing up, then trails the highs; a buy mirrors it.
            crossed = high >= activation_px if side == "sell" else low <= activation_px
            if not crossed:
                continue
            active = True
            trailing_price = high if side == "sell" else low
            stop_px = stop_price(cfg, side, trailing_price, atr, price, cal)
            stop_atr = atr

        if (
            stop_px is not None
            and trailing_price is not None
            and stop_atr is not None
            and (stop_atr < atr_limit_min or stop_atr > atr_limit_max)
        ):
            stop_px = stop_price(cfg, side, trailing_price, atr, price, cal)
            stop_atr = atr

        # Trail the favourable extreme first, then test the stop against the updated level.
        extreme = high if side == "sell" else low
        improved = extreme > trailing_price if side == "sell" else extreme < trailing_price
        if improved:
            trailing_price = extreme
            stop_px = stop_price(cfg, side, trailing_price, atr, price, cal)
            stop_atr = atr

        stop_hit = low <= stop_px if side == "sell" else high >= stop_px
        if not stop_hit:
            continue

        exec_price = stop_px
        cum_pnl = _record_stop_exit(ops, cal, side, exec_price, dtime, vol, fee_rate, cum_pnl)

        if max_ops is not None and len(ops) >= max_ops:
            break

        side = _opposite(side)
        entry_price = float(exec_price)
        active = False
        activation_px = None
        activation_atr = None
        trailing_price = None
        stop_px = None
        stop_atr = None

    return ops
