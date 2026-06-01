"""Pure trading simulation engine.

Leaf module: it must not import from ``core.config`` or
``trading.parameters_manager``. All configuration is passed in via
``EngineConfig`` so the simulator can run against live state, a backtest
request, or an optimizer candidate without ever touching module-level globals.
"""

from dataclasses import dataclass

import numpy as np

# Fixed, ordered volatility levels. Kept local (not imported from core.config)
# so this module stays a leaf with no project dependencies.
LEVELS = ("LL", "LV", "MV", "HV", "HH")


@dataclass(frozen=True)
class PairCalibration:
    atr_p20: float
    atr_p50: float
    atr_p80: float
    atr_p95: float
    k_stop_buy: dict[str, float | None]  # {level: k}
    k_stop_sell: dict[str, float | None]


@dataclass(frozen=True)
class SidePolicy:
    k_act: float | None
    min_margin: float


@dataclass(frozen=True)
class EngineConfig:
    pair: str
    calibration: PairCalibration
    buy: SidePolicy
    sell: SidePolicy
    atr_desv_limit: float


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


def _vol_level_from_atr(atr_val: float, atr_20: float, atr_50: float, atr_80: float, atr_95: float) -> str:
    if atr_val < atr_20:
        return "LL"
    if atr_val < atr_50:
        return "LV"
    if atr_val < atr_80:
        return "MV"
    if atr_val < atr_95:
        return "HV"
    return "HH"


def _pnl_abs(prev_side: str, prev_price: float, curr_price: float) -> float:
    # P&L is computed vs previous executed operation price
    if prev_side == "buy":
        return curr_price - prev_price
    return prev_price - curr_price


def lookup_k_stop(cfg: EngineConfig, side: str, atr_val: float) -> float | None:
    """Resolve K_STOP for a side/ATR, reproducing parameters_manager.get_k_stop's
    fallback logic but reading from cfg.calibration instead of TRADING_PARAMS."""
    cal = cfg.calibration
    vol = _vol_level_from_atr(atr_val, cal.atr_p20, cal.atr_p50, cal.atr_p80, cal.atr_p95)

    same = cal.k_stop_sell if side == "sell" else cal.k_stop_buy
    opp = cal.k_stop_buy if side == "sell" else cal.k_stop_sell

    k_stop = same.get(vol)
    if k_stop is not None:
        return k_stop

    # Try opposite side K_STOP as fallback
    k_stop = opp.get(vol)
    if k_stop is not None:
        return k_stop

    # Search neighboring levels (same side only)
    idx = LEVELS.index(vol)
    for offset in range(1, len(LEVELS)):
        for neighbor in (idx - offset, idx + offset):
            if 0 <= neighbor < len(LEVELS):
                k_stop = same.get(LEVELS[neighbor])
                if k_stop is not None:
                    return k_stop

    return None


def activation_price(cfg: EngineConfig, side: str, entry_price: float, atr_val: float) -> float:
    policy = cfg.sell if side == "sell" else cfg.buy
    k_act = policy.k_act
    if k_act is not None:
        activation_distance = float(k_act) * atr_val
    else:
        k_stop = lookup_k_stop(cfg, side, atr_val) or 0.0
        min_margin = policy.min_margin
        activation_distance = float(k_stop) * atr_val + (min_margin * entry_price)

    if side == "sell":
        return entry_price + activation_distance
    return entry_price - activation_distance


def stop_price(cfg: EngineConfig, side: str, trailing_price: float, atr_val: float) -> float:
    k_stop = lookup_k_stop(cfg, side, atr_val) or 0.0
    stop_distance = float(k_stop) * atr_val
    if side == "sell":
        return trailing_price - stop_distance
    return trailing_price + stop_distance


def simulate_operations(
    df,
    cfg: EngineConfig,
    fee_rate: float = 0.0,
    max_ops: int | None = None,
) -> list[Operation]:
    cal = cfg.calibration
    atr_20, atr_50, atr_80, atr_95 = cal.atr_p20, cal.atr_p50, cal.atr_p80, cal.atr_p95

    ops: list[Operation] = []
    # Track cumulative return in percent (compounded). Start at 0%.
    cum_pnl = 0.0

    # Start always with a BUY operation at first valid close
    first_row = None
    for _, row in df.iterrows():
        atr = float(row["atr"])
        if atr > 0 and not np.isnan(atr):
            first_row = row
            break
    if first_row is None:
        return ops

    first_atr = float(first_row["atr"])
    if "close" in first_row:
        first_price = float(first_row["close"])
    elif "open" in first_row:
        first_price = float(first_row["open"])
    else:
        first_price = (float(first_row["high"]) + float(first_row["low"])) / 2.0
    first_time = str(first_row["dtime"])
    first_vol = _vol_level_from_atr(first_atr, atr_20, atr_50, atr_80, atr_95)
    first_k = lookup_k_stop(cfg, "buy", first_atr) or 0.0
    first_fee = float(first_price) * float(fee_rate)
    # Convert the entry fee to percent of entry price and apply to cumulative %
    # Equivalent to an immediate negative return of fee_rate * 100.
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
            pnl_abs=None,
            pnl_pct=None,
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

    for _, row in df.iterrows():
        atr = float(row["atr"])
        if atr <= 0 or np.isnan(atr):
            continue

        high = float(row["high"])
        low = float(row["low"])
        dtime = str(row["dtime"])
        vol = _vol_level_from_atr(atr, atr_20, atr_50, atr_80, atr_95)

        atr_limit_max = atr * (1 + cfg.atr_desv_limit)
        atr_limit_min = atr * (1 - cfg.atr_desv_limit)

        if activation_px is None:
            activation_px = activation_price(cfg, side, entry_price, atr)
            activation_atr = atr

        if not active:
            # Recalibrate activation
            if activation_atr is not None and (activation_atr < atr_limit_min or activation_atr > atr_limit_max):
                activation_px = activation_price(cfg, side, entry_price, atr)
                activation_atr = atr

            # Activation check
            if side == "sell" and high >= activation_px:
                active = True
                trailing_price = high
                stop_px = stop_price(cfg, side, trailing_price, atr)
                stop_atr = atr
            elif side == "buy" and low <= activation_px:
                active = True
                trailing_price = low
                stop_px = stop_price(cfg, side, trailing_price, atr)
                stop_atr = atr
            else:
                continue

        # Recalibrate stop
        if (
            stop_px is not None
            and trailing_price is not None
            and stop_atr is not None
            and (stop_atr < atr_limit_min or stop_atr > atr_limit_max)
        ):
            stop_px = stop_price(cfg, side, trailing_price, atr)
            stop_atr = atr

        # Stop hit check & trailing update
        if side == "sell":
            if high > trailing_price:
                trailing_price = high
                stop_px = stop_price(cfg, side, trailing_price, atr)
                stop_atr = atr
            if low <= stop_px:
                exec_price = stop_px
                prev = ops[-1]
                fee = float(exec_price) * float(fee_rate)
                pnl = _pnl_abs(prev.side, prev.price, exec_price) - fee
                pnl_pct = (pnl / prev.price) * 100 if prev.price else None
                # Compound cumulative percent: (1+cum%)*(1+op%)-1
                if pnl_pct is not None:
                    cum_factor = (1.0 + (cum_pnl / 100.0)) * (1.0 + (float(pnl_pct) / 100.0))
                    cum_pnl = (cum_factor - 1.0) * 100.0
                k_used = lookup_k_stop(cfg, "sell", atr) or 0.0
                ops.append(
                    Operation(
                        idx=len(ops) + 1,
                        time=dtime,
                        side="sell",
                        price=float(exec_price),
                        vol=vol,
                        k_stop=float(k_used),
                        fee_abs=float(fee),
                        pnl_abs=float(pnl),
                        pnl_pct=float(pnl_pct) if pnl_pct is not None else None,
                        cum_pnl=float(cum_pnl),
                    )
                )

                if max_ops is not None and len(ops) >= max_ops:
                    break

                side = "buy"
                entry_price = float(exec_price)
                active = False
                activation_px = None
                activation_atr = None
                trailing_price = None
                stop_px = None
                stop_atr = None
        else:
            if low < trailing_price:
                trailing_price = low
                stop_px = stop_price(cfg, side, trailing_price, atr)
                stop_atr = atr
            if high >= stop_px:
                exec_price = stop_px
                prev = ops[-1]
                fee = float(exec_price) * float(fee_rate)
                pnl = _pnl_abs(prev.side, prev.price, exec_price) - fee
                pnl_pct = (pnl / prev.price) * 100 if prev.price else None
                # Compound cumulative percent: (1+cum%)*(1+op%)-1
                if pnl_pct is not None:
                    cum_factor = (1.0 + (cum_pnl / 100.0)) * (1.0 + (float(pnl_pct) / 100.0))
                    cum_pnl = (cum_factor - 1.0) * 100.0
                k_used = lookup_k_stop(cfg, "buy", atr) or 0.0
                ops.append(
                    Operation(
                        idx=len(ops) + 1,
                        time=dtime,
                        side="buy",
                        price=float(exec_price),
                        vol=vol,
                        k_stop=float(k_used),
                        fee_abs=float(fee),
                        pnl_abs=float(pnl),
                        pnl_pct=float(pnl_pct) if pnl_pct is not None else None,
                        cum_pnl=float(cum_pnl),
                    )
                )

                if max_ops is not None and len(ops) >= max_ops:
                    break

                side = "sell"
                entry_price = float(exec_price)
                active = False
                activation_px = None
                activation_atr = None
                trailing_price = None
                stop_px = None
                stop_atr = None

    return ops
