"""Exploratory measurement that informed the optimizer search grids.

Read-only. Measures, per pair, the structural distributions behind each grid
(see docs/specs/optimizer-validation-design.md). It only *reports* distributions —
the grids are fixed in the spec, not produced here:

- stop_pcts : per side (sell/up, buy/down) and volatility level, the K-value
              sample count and percentiles (the per-level K distribution).
- k_act     : the favorable-leg / ATR distribution (pivot-to-pivot moves in ATR
              units) — anchors the k_act upper bound.
- min_margin: the ATR/price ratio distribution — anchors the min_margin band.

Reported on the full window and the train/test split to eyeball distribution
stability. Nothing is written.

The `--sweep` mode varies MINIMUM_CHANGE_PCT (the pivot noise filter) and reports
how per-level sample counts and median K shift — the input to that decision.

Usage (PYTHONPATH=. and DB env vars required):

    PYTHONPATH=. python scripts/analysis/grid_derivation_explore.py XBTEUR ETHEUR
    PYTHONPATH=. python scripts/analysis/grid_derivation_explore.py XBTEUR --sweep
"""

import argparse
import math
from itertools import pairwise

import numpy as np
import pandas as pd

import core.database as db
import trading.market_analyzer as ma
from core.config import CANDLE_TIMEFRAME, PAIRS
from core.config import VOLATILITY_LEVELS as LEVELS
from trading.market_analyzer import analyze_structural_noise, detect_pivots

K_QUANT = 0.1  # K_STOP is ceil-quantized to this resolution (ceil(q*10)/10)


# --- distribution helpers --------------------------------------------------


def _pctls(arr: np.ndarray) -> dict[str, float]:
    if arr.size == 0:
        return {}
    qs = (10, 25, 50, 75, 90, 95, 99)
    out = {f"p{q}": float(np.percentile(arr, q)) for q in qs}
    out["min"] = float(arr.min())
    out["max"] = float(arr.max())
    return out


def _k_values_by_level(events: list[dict]) -> dict[str, np.ndarray]:
    out: dict[str, list[float]] = {lvl: [] for lvl in LEVELS}
    for e in events:
        for lvl in LEVELS:
            d = (e.get("volatility_levels") or {}).get(lvl)
            if d and d.get("k_value") is not None:
                out[lvl].append(float(d["k_value"]))
    return {lvl: np.array(v, dtype=float) for lvl, v in out.items()}


def _leg_atr_distribution(df: pd.DataFrame, order: int) -> np.ndarray:
    """Favorable-leg magnitude in ATR units across consecutive pivots: the move
    available from entry (a pivot) to the next pivot, divided by the segment's
    mean ATR. Anchors how far k_act can be set and still activate on a typical leg."""
    pivots = detect_pivots(df, order)
    legs: list[float] = []
    for (i0, _, p0, _), (i1, _, p1, _) in pairwise(pivots):
        seg_atr = df["atr"].iloc[i0 : i1 + 1]
        mean_atr = float(seg_atr.mean()) if len(seg_atr) else 0.0
        if mean_atr > 0:
            legs.append(abs(float(p1) - float(p0)) / mean_atr)
    return np.array(legs, dtype=float)


def _ceil_k(q: float) -> float:
    return math.ceil(q * (1.0 / K_QUANT)) / (1.0 / K_QUANT)


# --- reporting -------------------------------------------------------------


def _fmt_pctls(label: str, d: dict[str, float]) -> str:
    if not d:
        return f"    {label:<10} (no samples)"
    keys = ("p10", "p25", "p50", "p75", "p90", "p95", "p99", "max")
    body = "  ".join(f"{k}={d[k]:.3f}" for k in keys if k in d)
    return f"    {label:<10} {body}"


def _report_stops(side: str, events: list[dict]) -> None:
    print(f"\n  [stop_pcts] side={side}  (K-value = adverse excursion / ATR, per level)")
    by_level = _k_values_by_level(events)
    for lvl in LEVELS:
        kv = by_level[lvl]
        print(f"    {lvl}: n={kv.size}")
        if kv.size:
            print(_fmt_pctls("dist", _pctls(kv)))


def _report_kact(df: pd.DataFrame, order: int) -> None:
    legs = _leg_atr_distribution(df, order)
    print(f"\n  [k_act] favorable-leg / ATR  (n={legs.size})  — anchors the k_act upper bound")
    print(_fmt_pctls("leg/ATR", _pctls(legs)))


def _report_minmargin(df: pd.DataFrame, sell_events: list[dict], buy_events: list[dict]) -> None:
    ratio = (df["atr"] / df["close"].replace(0, np.nan)).dropna().to_numpy()
    print(f"\n  [min_margin] ATR/price ratio  (n={ratio.size})  — anchors the min_margin band")
    print(_fmt_pctls("ATR/price", _pctls(ratio)))
    if ratio.size == 0:
        return
    med_ratio = float(np.median(ratio))
    # Representative structural K_STOP: median ceil-quantized K across all level samples.
    # The additive min_margin term matches the structural K_STOP*ATR term at ~ k_ref*ratio.
    all_k = np.concatenate(
        [v for ev in (sell_events, buy_events) for v in _k_values_by_level(ev).values() if v.size]
        or [np.array([], dtype=float)]
    )
    k_ref = _ceil_k(float(np.median(all_k))) if all_k.size else 1.0
    print(
        f"      structural ref K_STOP~{k_ref:.1f}, median ATR/price={med_ratio:.5f} → additive==structural at ~{k_ref * med_ratio:.5f}"
    )


def _analyze_frame(name: str, df: pd.DataFrame, order: int) -> None:
    if df.empty:
        print(f"\n  -- {name}: empty frame")
        return
    span = f"{df['dtime'].iloc[0]} -> {df['dtime'].iloc[-1]}"
    print(f"\n  == {name}  (rows={len(df)}, {span}) ==")
    sell_events, buy_events = analyze_structural_noise(df, order)
    _report_stops("sell", sell_events)
    _report_stops("buy", buy_events)
    _report_kact(df, order)
    _report_minmargin(df, sell_events, buy_events)


def explore_pair(pair: str, timeframe: int, train_split: float, order: int) -> None:
    df = db.load_ohlc_data(pair, timeframe).dropna(subset=["atr"]).sort_values("time").reset_index(drop=True)
    print("\n" + "=" * 78)
    print(f"PAIR {pair}  (timeframe={timeframe}m, total rows={len(df)})")
    print("=" * 78)
    if df.empty:
        print("  no OHLC data")
        return

    split_idx = int(len(df) * train_split)
    train_df = df.iloc[:split_idx].reset_index(drop=True)
    test_df = df.iloc[split_idx:].reset_index(drop=True)

    # FULL is the headline; TRAIN/TEST to eyeball distribution stability across the split.
    _analyze_frame("FULL", df, order)
    _analyze_frame(f"TRAIN ({train_split:.0%})", train_df, order)
    _analyze_frame(f"TEST ({1 - train_split:.0%})", test_df, order)


# --- MINIMUM_CHANGE_PCT sweep ----------------------------------------------


def _cell(arr: np.ndarray) -> str:
    """Compact 'n/median' cell for a K-value sample (or '-' when empty)."""
    return f"{arr.size}/{np.median(arr):.1f}" if arr.size else "-"


def _sweep_pair(pair: str, timeframe: int, mcp_values: list[float], order: int) -> None:
    """Sweep MINIMUM_CHANGE_PCT (the pivot noise filter) and report, per level and
    side, how the sample count and the median K shift. Lowering the threshold should
    add samples (helps the scarce tails) but, if it drops below the tradeable-swing
    floor, pull the median K down — calibrating stops on un-tradeable micro-noise.

    MINIMUM_CHANGE_PCT is a module global read inside detect_pivots, so it is patched
    on the imported module for each value and restored afterwards. Run on FULL history
    to maximize signal; this measures sensitivity, not a window choice."""
    df = db.load_ohlc_data(pair, timeframe).dropna(subset=["atr"]).sort_values("time").reset_index(drop=True)
    print("\n" + "=" * 90)
    print(f"SWEEP MINIMUM_CHANGE_PCT - {pair}  (FULL, rows={len(df)}, current default={ma.MINIMUM_CHANGE_PCT})")
    print("=" * 90)
    if df.empty:
        print("  no OHLC data")
        return

    original = ma.MINIMUM_CHANGE_PCT
    rows = []
    try:
        for mcp in mcp_values:
            ma.MINIMUM_CHANGE_PCT = mcp
            sell_events, buy_events = analyze_structural_noise(df, order)
            legs = _leg_atr_distribution(df, order)
            n_pivots = len(detect_pivots(df, order))
            rows.append((mcp, n_pivots, legs, _k_values_by_level(sell_events), _k_values_by_level(buy_events)))
    finally:
        ma.MINIMUM_CHANGE_PCT = original

    hdr = f"  {'mcp':>7} {'pivots':>7} {'legs n/med':>11}  " + "  ".join(f"{lvl:>9}" for lvl in LEVELS)
    for side in ("sell", "buy"):
        print(f"\n  side={side}   (cell = n / median K)")
        print(hdr)
        for mcp, n_pivots, legs, sell_k, buy_k in rows:
            by_level = sell_k if side == "sell" else buy_k
            legcell = f"{legs.size}/{np.median(legs):.1f}" if legs.size else "-"
            cells = "  ".join(f"{_cell(by_level[lvl]):>9}" for lvl in LEVELS)
            print(f"  {mcp:>7.4f} {n_pivots:>7} {legcell:>11}  {cells}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Exploratory measurement for optimizer grid derivation.")
    ap.add_argument("pairs", nargs="*", help="Pairs to analyze (default: PAIRS from config).")
    ap.add_argument("--timeframe", type=int, default=CANDLE_TIMEFRAME)
    ap.add_argument("--train-split", type=float, default=0.67)
    ap.add_argument("--order", type=int, default=None, help="Pivot detection order (default: market analyzer default).")
    ap.add_argument("--sweep", action="store_true", help="Sweep MINIMUM_CHANGE_PCT instead of the full analysis.")
    ap.add_argument(
        "--mcp-values",
        type=str,
        default="0.010,0.0125,0.015,0.020",
        help="Comma-separated MINIMUM_CHANGE_PCT values for --sweep.",
    )
    args = ap.parse_args()

    pairs = args.pairs or [p for p in PAIRS if p]
    if not pairs:
        ap.error("no pairs given and PAIRS is empty in config")

    from trading.market_analyzer import DEFAULT_ORDER

    order = args.order if args.order is not None else DEFAULT_ORDER

    if args.sweep:
        mcp_values = [float(v) for v in args.mcp_values.split(",")]
        for pair in pairs:
            _sweep_pair(pair, args.timeframe, mcp_values, order)
        return

    for pair in pairs:
        explore_pair(pair, args.timeframe, args.train_split, order)


if __name__ == "__main__":
    main()
