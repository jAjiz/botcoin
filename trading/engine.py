"""
Pure simulation engine for the trailing-stop strategy.

All parameters are passed explicitly via PairCalibration + EngineConfig — no
global state is read or mutated. This allows the backtest endpoint and the
optimizer to run candidate configs in isolation.
"""

from dataclasses import dataclass

import numpy as np

_LEVELS = ("LL", "LV", "MV", "HV", "HH")


@dataclass(frozen=True)
class PairCalibration:
    """All per-pair parameters needed by the simulator."""

    atr_20pct: float
    atr_50pct: float
    atr_80pct: float
    atr_95pct: float
    sell_k_stops: dict  # {level -> float | None}
    buy_k_stops: dict
    k_act_sell: float | None
    k_act_buy: float | None
    min_margin_sell: float
    min_margin_buy: float
    atr_desv_limit: float = 0.2


@dataclass(frozen=True)
class EngineConfig:
    """Execution parameters for a single simulation run."""

    fee_rate: float = 0.0
    max_ops: int | None = None


@dataclass(frozen=True)
class Operation:
    idx: int
    time: str
    side: str
    price: float
    vol: str
    k_stop: float
    fee_abs: float
    pnl_abs: float | None
    pnl_pct: float | None
    cum_pnl: float | None


def _vol_level(atr_val: float, cal: PairCalibration) -> str:
    if atr_val < cal.atr_20pct:
        return "LL"
    if atr_val < cal.atr_50pct:
        return "LV"
    if atr_val < cal.atr_80pct:
        return "MV"
    if atr_val < cal.atr_95pct:
        return "HV"
    return "HH"


def _resolve_k_stop(cal: PairCalibration, side: str, vol: str) -> float:
    k_stops = cal.sell_k_stops if side == "sell" else cal.buy_k_stops
    opp_k_stops = cal.buy_k_stops if side == "sell" else cal.sell_k_stops

    k = k_stops.get(vol)
    if k is not None:
        return float(k)
    k = opp_k_stops.get(vol)
    if k is not None:
        return float(k)
    idx = _LEVELS.index(vol)
    for offset in range(1, len(_LEVELS)):
        for n in (idx - offset, idx + offset):
            if 0 <= n < len(_LEVELS):
                k = k_stops.get(_LEVELS[n])
                if k is not None:
                    return float(k)
    return 0.0


def _activation_price(cal: PairCalibration, side: str, entry_price: float, atr_val: float) -> float:
    k_act = cal.k_act_sell if side == "sell" else cal.k_act_buy
    if k_act is not None:
        dist = float(k_act) * atr_val
    else:
        vol = _vol_level(atr_val, cal)
        k_stop = _resolve_k_stop(cal, side, vol)
        min_margin = cal.min_margin_sell if side == "sell" else cal.min_margin_buy
        dist = k_stop * atr_val + float(min_margin) * entry_price
    return (entry_price + dist) if side == "sell" else (entry_price - dist)


def _stop_price(cal: PairCalibration, side: str, trailing_price: float, atr_val: float) -> float:
    vol = _vol_level(atr_val, cal)
    k_stop = _resolve_k_stop(cal, side, vol)
    dist = k_stop * atr_val
    return (trailing_price - dist) if side == "sell" else (trailing_price + dist)


def _pnl_abs(prev_side: str, prev_price: float, curr_price: float) -> float:
    if prev_side == "buy":
        return curr_price - prev_price
    return prev_price - curr_price


def simulate_operations(df, cal: PairCalibration, cfg: EngineConfig) -> list[Operation]:
    """
    Simulate trailing-stop operations over OHLC data.

    Args:
        df: DataFrame with high, low, close (or open), atr, dtime columns.
        cal: Per-pair calibration (ATR thresholds, K_STOP, K_ACT, margins).
        cfg: Execution config (fee rate, max ops cap).

    Returns:
        List of Operations, starting with the initial buy.
    """
    ops: list[Operation] = []
    cum_pnl = 0.0

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
    first_vol = _vol_level(first_atr, cal)
    first_k = _resolve_k_stop(cal, "buy", first_vol)
    first_fee = first_price * cfg.fee_rate
    cum_pnl -= cfg.fee_rate * 100.0

    ops.append(
        Operation(
            idx=1,
            time=first_time,
            side="buy",
            price=first_price,
            vol=first_vol,
            k_stop=first_k,
            fee_abs=first_fee,
            pnl_abs=None,
            pnl_pct=None,
            cum_pnl=cum_pnl,
        )
    )

    side = "sell"
    entry_price = first_price
    active = False
    activation_price: float | None = None
    activation_atr: float | None = None
    trailing_price: float | None = None
    stop_price_val: float | None = None
    stop_atr: float | None = None

    for _, row in df.iterrows():
        atr = float(row["atr"])
        if atr <= 0 or np.isnan(atr):
            continue

        high = float(row["high"])
        low = float(row["low"])
        dtime = str(row["dtime"])
        vol = _vol_level(atr, cal)

        atr_limit_max = atr * (1 + cal.atr_desv_limit)
        atr_limit_min = atr * (1 - cal.atr_desv_limit)

        if activation_price is None:
            activation_price = _activation_price(cal, side, entry_price, atr)
            activation_atr = atr

        if not active:
            if activation_atr is not None and (activation_atr < atr_limit_min or activation_atr > atr_limit_max):
                activation_price = _activation_price(cal, side, entry_price, atr)
                activation_atr = atr

            if side == "sell" and high >= activation_price:
                active = True
                trailing_price = high
                stop_price_val = _stop_price(cal, side, trailing_price, atr)
                stop_atr = atr
            elif side == "buy" and low <= activation_price:
                active = True
                trailing_price = low
                stop_price_val = _stop_price(cal, side, trailing_price, atr)
                stop_atr = atr
            else:
                continue

        if (
            stop_price_val is not None
            and trailing_price is not None
            and stop_atr is not None
            and (stop_atr < atr_limit_min or stop_atr > atr_limit_max)
        ):
            stop_price_val = _stop_price(cal, side, trailing_price, atr)
            stop_atr = atr

        if side == "sell":
            if high > trailing_price:
                trailing_price = high
                stop_price_val = _stop_price(cal, side, trailing_price, atr)
                stop_atr = atr
            if low <= stop_price_val:
                exec_price = stop_price_val
                prev = ops[-1]
                fee = exec_price * cfg.fee_rate
                pnl = _pnl_abs(prev.side, prev.price, exec_price) - fee
                pnl_pct = (pnl / prev.price) * 100 if prev.price else None
                if pnl_pct is not None:
                    cum_factor = (1.0 + cum_pnl / 100.0) * (1.0 + pnl_pct / 100.0)
                    cum_pnl = (cum_factor - 1.0) * 100.0
                k_used = _resolve_k_stop(cal, "sell", vol)
                ops.append(
                    Operation(
                        idx=len(ops) + 1,
                        time=dtime,
                        side="sell",
                        price=float(exec_price),
                        vol=vol,
                        k_stop=k_used,
                        fee_abs=fee,
                        pnl_abs=pnl,
                        pnl_pct=pnl_pct,
                        cum_pnl=cum_pnl,
                    )
                )
                if cfg.max_ops is not None and len(ops) >= cfg.max_ops:
                    break
                side = "buy"
                entry_price = float(exec_price)
                active = False
                activation_price = None
                activation_atr = None
                trailing_price = None
                stop_price_val = None
                stop_atr = None
        else:
            if low < trailing_price:
                trailing_price = low
                stop_price_val = _stop_price(cal, side, trailing_price, atr)
                stop_atr = atr
            if high >= stop_price_val:
                exec_price = stop_price_val
                prev = ops[-1]
                fee = exec_price * cfg.fee_rate
                pnl = _pnl_abs(prev.side, prev.price, exec_price) - fee
                pnl_pct = (pnl / prev.price) * 100 if prev.price else None
                if pnl_pct is not None:
                    cum_factor = (1.0 + cum_pnl / 100.0) * (1.0 + pnl_pct / 100.0)
                    cum_pnl = (cum_factor - 1.0) * 100.0
                k_used = _resolve_k_stop(cal, "buy", vol)
                ops.append(
                    Operation(
                        idx=len(ops) + 1,
                        time=dtime,
                        side="buy",
                        price=float(exec_price),
                        vol=vol,
                        k_stop=k_used,
                        fee_abs=fee,
                        pnl_abs=pnl,
                        pnl_pct=pnl_pct,
                        cum_pnl=cum_pnl,
                    )
                )
                if cfg.max_ops is not None and len(ops) >= cfg.max_ops:
                    break
                side = "sell"
                entry_price = float(exec_price)
                active = False
                activation_price = None
                activation_atr = None
                trailing_price = None
                stop_price_val = None
                stop_atr = None

    return ops
