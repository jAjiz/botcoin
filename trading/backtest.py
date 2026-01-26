import sys
import numpy as np
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

# Ensure sibling packages are importable when running as a script.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import ATR_DESV_LIMIT, PAIRS, TRADING_PARAMS
from trading.market_analyzer import load_data
from trading.parameters_manager import calculate_trading_parameters, get_k_stop

def _parse_args() -> dict:
    args = {
        "pair": None,
        "fee_pct": 0.0,  # percentage (e.g., 0.26 == 0.26%)
        "start": None,
        "end": None,
        "max_ops": None,
    }

    for arg in sys.argv[1:]:
        if arg.startswith("PAIR="):
            args["pair"] = arg.split("=", 1)[1].upper()
        elif arg.startswith("FEE_PCT="):
            args["fee_pct"] = float(arg.split("=", 1)[1])
        elif arg.startswith("START="):
            args["start"] = arg.split("=", 1)[1]
        elif arg.startswith("END="):
            args["end"] = arg.split("=", 1)[1]
        elif arg.startswith("MAX_OPS="):
            args["max_ops"] = int(arg.split("=", 1)[1])

    if not args["pair"]:
        print("Error: PAIR parameter is required.")
        print(
            "Usage: python .\\trading\\backtest.py PAIR=XBTEUR "
            "[FEE_PCT=0.00] [START=YYYY-MM-DD] [END=YYYY-MM-DD] "
            "[MAX_OPS=50]"
        )
        sys.exit(1)

    return args


def _atr_thresholds(pair: str) -> Tuple[float, float, float, float]:
    return (
        float(PAIRS[pair]["atr_20pct"]),
        float(PAIRS[pair]["atr_50pct"]),
        float(PAIRS[pair]["atr_80pct"]),
        float(PAIRS[pair]["atr_95pct"]),
    )


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


def _activation_price(pair: str, side: str, entry_price: float, atr_val: float) -> float:
    k_act = TRADING_PARAMS[pair][side].get("K_ACT")
    if k_act is not None:
        activation_distance = float(k_act) * atr_val
    else:
        k_stop = get_k_stop(pair, side, atr_val) or 0.0
        min_margin = float(TRADING_PARAMS[pair][side].get("MIN_MARGIN", 0) or 0)
        activation_distance = float(k_stop) * atr_val + (min_margin * entry_price)

    if side == "sell":
        return entry_price + activation_distance
    return entry_price - activation_distance


def _stop_price(pair: str, side: str, trailing_price: float, atr_val: float) -> float:
    k_stop = get_k_stop(pair, side, atr_val) or 0.0
    stop_distance = float(k_stop) * atr_val
    if side == "sell":
        return trailing_price - stop_distance
    return trailing_price + stop_distance


def _pnl_abs(prev_side: str, prev_price: float, curr_price: float) -> float:
    # P&L is computed vs previous executed operation price
    if prev_side == "buy":
        return curr_price - prev_price
    return prev_price - curr_price


@dataclass(frozen=True)
class Operation:
    idx: int
    time: str
    side: str  # "buy" | "sell"
    price: float
    vol: str
    k_stop: float
    fee_abs: float
    pnl_abs: Optional[float]
    pnl_pct: Optional[float]
    cum_pnl: Optional[float]


def simulate_operations(df, pair: str, fee_rate: float = 0.0, max_ops: Optional[int] = None) -> List[Operation]:
    atr_20, atr_50, atr_80, atr_95 = _atr_thresholds(pair)

    ops: List[Operation] = []
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
    first_k = get_k_stop(pair, "buy", first_atr) or 0.0
    first_fee = float(first_price) * float(fee_rate)
    cum_pnl -= first_fee
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
    activation_price = None
    activation_atr = None
    trailing_price = None
    stop_price = None
    stop_atr = None

    for _, row in df.iterrows():
        atr = float(row["atr"])
        if atr <= 0 or np.isnan(atr):
            continue

        high = float(row["high"])
        low = float(row["low"])
        dtime = str(row["dtime"])
        vol = _vol_level_from_atr(atr, atr_20, atr_50, atr_80, atr_95)

        atr_limit_max = atr * (1 + ATR_DESV_LIMIT)
        atr_limit_min = atr * (1 - ATR_DESV_LIMIT)

        if activation_price is None:
            activation_price = _activation_price(pair, side, entry_price, atr)
            activation_atr = atr

        if not active:
            # Recalibrate activation 
            if activation_atr is not None and (activation_atr < atr_limit_min or activation_atr > atr_limit_max):
                activation_price = _activation_price(pair, side, entry_price, atr)
                activation_atr = atr

            # Activation check
            if side == "sell" and high >= activation_price:
                active = True
                trailing_price = high
                stop_price = _stop_price(pair, side, trailing_price, atr)
                stop_atr = atr
            elif side == "buy" and low <= activation_price:
                active = True
                trailing_price = low
                stop_price = _stop_price(pair, side, trailing_price, atr)
                stop_atr = atr
            else:
                continue

        # Recalibrate stop 
        if stop_price is not None and trailing_price is not None and stop_atr is not None:
            if stop_atr < atr_limit_min or stop_atr > atr_limit_max:
                stop_price = _stop_price(pair, side, trailing_price, atr)
                stop_atr = atr

        # Stop hit check & trailing update
        if side == "sell":
            if high > trailing_price:
                trailing_price = high
                stop_price = _stop_price(pair, side, trailing_price, atr)
                stop_atr = atr
            if low <= stop_price:
                exec_price = stop_price
                prev = ops[-1]
                fee = float(exec_price) * float(fee_rate)
                pnl = _pnl_abs(prev.side, prev.price, exec_price) - fee
                pnl_pct = (pnl / prev.price) * 100 if prev.price else None
                cum_pnl += pnl
                k_used = get_k_stop(pair, "sell", atr) or 0.0
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
                activation_price = None
                activation_atr = None
                trailing_price = None
                stop_price = None
                stop_atr = None
        else:
            if low < trailing_price:
                trailing_price = low
                stop_price = _stop_price(pair, side, trailing_price, atr)
                stop_atr = atr
            if high >= stop_price:
                exec_price = stop_price
                prev = ops[-1]
                fee = float(exec_price) * float(fee_rate)
                pnl = _pnl_abs(prev.side, prev.price, exec_price) - fee
                pnl_pct = (pnl / prev.price) * 100 if prev.price else None
                cum_pnl += pnl
                k_used = get_k_stop(pair, "buy", atr) or 0.0
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
                activation_price = None
                activation_atr = None
                trailing_price = None
                stop_price = None
                stop_atr = None

    return ops


def _print_summary(ops: List[Operation]) -> None:
    if not ops:
        print("No operations found.")
        return

    pnl_values = [op.pnl_abs for op in ops if op.pnl_abs is not None]
    if not pnl_values:
        print("Only the initial operation was created (no exits/re-entries).")
        return

    total_fees = float(sum(op.fee_abs for op in ops if op.fee_abs is not None))

    pnl = np.array(pnl_values, dtype=float)
    win_rate = float(np.mean(pnl > 0) * 100.0)

    print("\n=== BACKTEST SUMMARY (PER OPERATION) ===")
    print(f"Operations: {len(ops)} | P&L samples: {len(pnl)}")
    print(f"Win rate: {win_rate:.1f}% | Total P&L (net): {pnl.sum():.2f}€ | Avg: {pnl.mean():.2f}€ | Median: {np.median(pnl):.2f}€")
    print(f"Best op P&L: {pnl.max():.2f}€ | Worst op P&L: {pnl.min():.2f}€")
    print(f"Total fees: {total_fees:.2f}€")


def _print_operations(ops: List[Operation], limit: Optional[int] = 100) -> None:
    if not ops:
        return

    show_limit = limit
    if show_limit is not None and show_limit > 0:
        title = f"\n=== OPERATIONS (first {min(show_limit, len(ops))}) ==="
    else:
        title = "\n=== OPERATIONS (all) ==="
    print(title)
    header = (f"{'#':>3} | {'Time':<20} | {'Side':>4} | {'Price':>10} | {'Vol':>3} | {'K_STOP':>6} | {'Fee€':>9} | {'P&L€':>10} | {'P&L%':>8} | {'Cum€':>10}")
    print(header)
    print("-" * len(header))

    for op in ops[:show_limit]:
        fee_abs = "" if op.fee_abs is None else f"{op.fee_abs:>9.2f}"
        pnl_abs = "" if op.pnl_abs is None else f"{op.pnl_abs:>10.2f}"
        pnl_pct = "" if op.pnl_pct is None else f"{op.pnl_pct:>7.2f}%"
        cum = "" if op.cum_pnl is None else f"{op.cum_pnl:>10.2f}"
        print(f"{op.idx:>3} | {op.time:<20} | {op.side:>4} | {op.price:>10.1f} | {op.vol:>3} | {op.k_stop:>6.2f} | {fee_abs:>9} | {pnl_abs:>10} | {pnl_pct:>8} | {cum:>10}")


def main() -> None:
    args = _parse_args()
    pair = args["pair"]
    fee_rate = float(args.get("fee_pct") or 0.0) / 100.0

    # Ensure we have thresholds + K_STOP in memory
    calculate_trading_parameters(pair)

    df = load_data(pair)

    # Optional date slicing (expects dtime comparable as string YYYY-MM-DD...)
    if args["start"]:
        df = df[df["dtime"] >= args["start"]]
    if args["end"]:
        df = df[df["dtime"] <= args["end"]]
    df = df.reset_index(drop=True)

    ops = simulate_operations(df, pair, fee_rate=fee_rate, max_ops=args["max_ops"])

    print(f"\nPAIR={pair}")
    print(f"Fee per op: {fee_rate * 100.0:.4f}% ({fee_rate:.6f} fraction)")
    print(f"K_STOP_SELL: {TRADING_PARAMS[pair]['sell']['K_STOP']}")
    print(f"K_STOP_BUY : {TRADING_PARAMS[pair]['buy']['K_STOP']}")

    _print_summary(ops)
    _print_operations(ops, limit=args["max_ops"])


if __name__ == "__main__":
    main()
