"""Does fitting a config predict anything, or is a good hold-out result a lucky draw?

Read-only and temporary. Reads Kraken's OHLCVT CSVs directly, so no database is needed.

The refit-cadence experiment found that the arm which fits *least* wins, monotonically.
That is the signature of an in-sample optimum with no predictive value: if fitting adds
nothing, the arm that commits to it least simply draws a config at random and can get
lucky. This settles it by enumeration rather than by argument.

With one shared stop_pct and no k_act branch the space is 21 x 5 = 105 configs, small
enough to sweep whole. So there is no sampler and no seed here: every config is evaluated
in-sample over the fit window and again on one continuous run over the forward span, and
the question becomes a rank. If the in-sample winner lands near the median of the forward
distribution, the fit is worth nothing and the good result was luck. If it lands in the
top decile, the fit predicts and the problem is re-fitting, not fitting.

Both denominations are reported, because they answer different questions:

  EUR   the portfolio's euro return, marked to market at the final price
  BTC   how much more of the base asset the run ended holding, which is
        (1 + r_bot) / (1 + r_hold) - 1

Within one span the two rank configs identically — the final price is a constant, so the
second is a monotone transform of the first. What changes is the benchmark: holding is
-23.7% in euros but exactly 0% in BTC, in every regime, so a config that never trades
stops looking like skill.

Usage (PYTHONPATH=. required; no DB env vars needed):
  PYTHONPATH=. python scripts/analysis/grid_sweep_holdout.py path/to/XBTEUR_15_*.csv
"""

import argparse
import dataclasses
import statistics
import time

import pandas as pd

import core.database as db
import trading.optimizer.search as optimizer
from core.config import ATR_DESV_LIMIT, ATR_PERIOD, CANDLE_TIMEFRAME, RECALIBRATION_BARS
from core.config import VOLATILITY_LEVELS as LEVELS
from trading.engine import mark_to_market, simulate_operations
from trading.market_analyzer import (
    CalibrationInputs,
    _wilder_atr_from_scratch,
    analyze_structural_noise,
    atr_ratio_percentiles,
    k_values_by_level,
)
from trading.optimizer.search import (
    Candidate,
    GridSpec,
    OptimizerRequest,
    SearchSpace,
    _build_eval_context,
)

CSV_COLUMNS = ["time", "open", "high", "low", "close", "volume", "count"]
PAIR = "XBTEUR"
FEE = 0.4
BARS_PER_DAY = (24 * 60) // CANDLE_TIMEFRAME

MM_GRID = GridSpec(0.0, 0.20, 0.01)
STOP_GRID = GridSpec(0.5, 0.9, 0.1)
# Only needed to satisfy OptimizerRequest; nothing here samples from it.
SPACE = SearchSpace(stop_pcts=STOP_GRID, k_act=None, min_margin=MM_GRID)


def _grid(g: GridSpec) -> list[float]:
    n = round((g.end - g.start) / g.step)
    return [round(g.start + i * g.step, 5) for i in range(n + 1)]


def candidates() -> list[Candidate]:
    """Every config in the space, one shared stop_pct across the five levels."""
    return [
        Candidate(k_act=None, min_margin=mm, stop_pcts=dict.fromkeys(LEVELS, stop))
        for mm in _grid(MM_GRID)
        for stop in _grid(STOP_GRID)
    ]


def _signature(cand: Candidate) -> str:
    return f"mm={cand.min_margin:.3f} stop={next(iter(cand.stop_pcts.values())):.1f}"


# --- data -------------------------------------------------------------------


def build_frame(paths: list[str]) -> pd.DataFrame:
    frames = []
    for path in paths:
        part = pd.read_csv(path, header=None, names=CSV_COLUMNS)
        print(f"  {path}: {len(part)} velas")
        frames.append(part)
    df = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["time"]).sort_values("time")
    df = df.reset_index(drop=True)

    step = CANDLE_TIMEFRAME * 60
    deltas = df["time"].diff().dropna()
    holes = deltas[deltas != step]
    if len(holes):
        print(f"\n  AVISO: {len(holes)} saltos en la serie (mayor: {int(holes.max()) // step} velas)")

    df["atr"] = _wilder_atr_from_scratch(df, ATR_PERIOD)
    df["dtime"] = pd.to_datetime(df["time"], unit="s")
    df = df.dropna(subset=["atr"]).sort_values("time").reset_index(drop=True)
    print(f"\n  marco: {len(df)} velas con ATR  {_dtime(df, 0)[:16]}..{_dtime(df, len(df) - 1)[:16]}")
    return df


def _dtime(frame: pd.DataFrame, bar: int) -> str:
    return str(frame.iloc[bar]["dtime"])


def _install_ohlc(frame: pd.DataFrame) -> None:
    def loader(pair, timeframe, *a, **kw):
        return frame.copy()

    db.load_ohlc_data = loader


# --- progressive calibration ------------------------------------------------

_POINTS: list[CalibrationInputs] = []
_POINT_TIMES: list[str] = []


def _install_calibration_cache(frame: pd.DataFrame, recalib_bars: int) -> None:
    """Frame-anchored points computed once and sliced per window; see the other harnesses."""
    global _POINTS, _POINT_TIMES
    t0 = time.perf_counter()
    points = []
    for idx in range(0, len(frame), recalib_bars):
        cal_df = frame.iloc[: idx + 1]
        up_events, down_events = analyze_structural_noise(cal_df)
        points.append(
            CalibrationInputs(
                idx, atr_ratio_percentiles(cal_df), k_values_by_level(up_events), k_values_by_level(down_events)
            )
        )
        if len(points) % 100 == 0:
            print(f"    ... {len(points)} puntos ({time.perf_counter() - t0:.0f}s)", flush=True)
    _POINTS = points
    _POINT_TIMES = [str(frame.iloc[p.at]["dtime"]) for p in points]
    print(f"  {len(points)} puntos cada {recalib_bars} velas ({time.perf_counter() - t0:.0f}s)")
    optimizer.build_calibration_inputs = _cached_calibration_inputs


def _cached_calibration_inputs(_df_full, df, recalib_bars: int) -> tuple:
    if not _POINTS or recalib_bars <= 0 or df.empty:
        return ()
    times = [str(t) for t in df["dtime"].tolist()]
    index_of = {t: i for i, t in enumerate(times)}
    first, last = times[0], times[-1]
    in_force = [p for p, t in zip(_POINTS, _POINT_TIMES, strict=True) if t <= first][-1]
    out = [dataclasses.replace(in_force, at=0)]
    for point, t in zip(_POINTS, _POINT_TIMES, strict=True):
        if first < t <= last and t in index_of:
            out.append(dataclasses.replace(point, at=index_of[t]))
    return tuple(out)


_CAL_CACHE: dict = {}


def _calibration_at(frame: pd.DataFrame, cutoff: str) -> dict:
    if cutoff not in _CAL_CACHE:
        cal_df = frame[frame["dtime"] <= cutoff].reset_index(drop=True)
        up_events, down_events = analyze_structural_noise(cal_df)
        p20, p50, p80, p95 = atr_ratio_percentiles(cal_df)
        _CAL_CACHE[cutoff] = {
            "up_events": up_events,
            "down_events": down_events,
            "atr_ratio_p20": p20,
            "atr_ratio_p50": p50,
            "atr_ratio_p80": p80,
            "atr_ratio_p95": p95,
        }
    return _CAL_CACHE[cutoff]


# --- sweep ------------------------------------------------------------------


def _context(frame: pd.DataFrame, first_bar: int, last_bar: int, decided_at: str):
    req = OptimizerRequest(
        pair=PAIR,
        mode="OPTIMIZE",
        fee_pct=FEE,
        start=_dtime(frame, first_bar),
        end=_dtime(frame, last_bar),
        train_split=1.0,
        n_trials=1,
        seed=0,
        search_space=SPACE,
    )
    return _build_eval_context(req, _calibration_at(frame, decided_at))


def sweep(ctx, cands: list[Candidate]) -> list[tuple[Candidate, float, int]]:
    """Marked euro return and closed-op count of every config on one continuous run."""
    out = []
    final_price = float(ctx.df.iloc[-1]["close"])
    for cand in cands:
        cfg = optimizer._build_engine_config(
            PAIR, cand, ctx.atr_ratio_thresholds, ctx.up_k, ctx.down_k, ATR_DESV_LIMIT, ctx.calibration_points
        )
        ops = simulate_operations(ctx.df, cfg, fee_rate=FEE / 100.0)
        if not ops:
            out.append((cand, 0.0, 0))
            continue
        marked = mark_to_market(ops, final_price)
        closed = sum(1 for op in ops if op.pnl_abs is not None and op.idx != 1)
        out.append((cand, round(marked, 2), closed))
    return out


def _btc(eur_pct: float, hold_pct: float) -> float:
    """Base-asset accumulation: (1 + r_bot) / (1 + r_hold) - 1, in percent."""
    return ((1.0 + eur_pct / 100.0) / (1.0 + hold_pct / 100.0) - 1.0) * 100.0


def _percentile_of(value: float, values: list[float]) -> float:
    """Share of the population this value is at least as good as, in percent."""
    return 100.0 * sum(1 for v in values if value >= v) / len(values)


# --- reporting --------------------------------------------------------------


def report(in_sample, forward, hold: float, top: int) -> dict:
    """Print one decision date's detail and return the row the final summary needs."""
    fwd_by_sig = {_signature(c): (pnl, ops) for c, pnl, ops in forward}
    fwd_pnls = [pnl for _, pnl, _ in forward]

    print("\n\n" + "=" * 96)
    print("DISTRIBUCION DEL TRAMO FUERA DE MUESTRA — los 105 configs, corrida continua")
    print("=" * 96)
    ranked = sorted(forward, key=lambda r: r[1], reverse=True)
    quart = statistics.quantiles(fwd_pnls, n=4)
    print(f"\n  mantener            {hold:>8.2f}% EUR   {0.0:>8.2f}% BTC")
    print(
        f"  mejor de los 105    {ranked[0][1]:>8.2f}% EUR   {_btc(ranked[0][1], hold):>8.2f}% BTC   {_signature(ranked[0][0])}"
    )
    print(f"  cuartil alto        {quart[2]:>8.2f}% EUR   {_btc(quart[2], hold):>8.2f}% BTC")
    print(f"  mediana             {quart[1]:>8.2f}% EUR   {_btc(quart[1], hold):>8.2f}% BTC")
    print(f"  cuartil bajo        {quart[0]:>8.2f}% EUR   {_btc(quart[0], hold):>8.2f}% BTC")
    print(
        f"  peor de los 105     {ranked[-1][1]:>8.2f}% EUR   {_btc(ranked[-1][1], hold):>8.2f}% BTC   {_signature(ranked[-1][0])}"
    )
    beats = sum(1 for p in fwd_pnls if p > hold)
    print(f"\n  configs que baten a mantener: {beats}/{len(fwd_pnls)}")

    print("\n\n" + "=" * 96)
    print("¿PREDICE EL AJUSTE? — donde cae en el tramo cada config elegido en la ventana de ajuste")
    print("=" * 96)
    best_in = sorted(in_sample, key=lambda r: r[1], reverse=True)
    print(
        f"\n  {'#':<4}{'config elegido':<24}{'ajuste':>10}{'FUERA €':>11}{'FUERA BTC':>12}{'percentil':>11}{'ops':>6}"
    )
    print("  " + "-" * 78)
    for rank, (cand, in_pnl, _) in enumerate(best_in[:top], start=1):
        fwd_pnl, fwd_ops = fwd_by_sig[_signature(cand)]
        print(
            f"  {rank:<4}{_signature(cand):<24}{in_pnl:>9.2f}%{fwd_pnl:>10.2f}%{_btc(fwd_pnl, hold):>11.2f}%"
            f"{_percentile_of(fwd_pnl, fwd_pnls):>10.0f}%{fwd_ops:>6}"
        )

    chosen = best_in[0][0]
    pct = _percentile_of(fwd_by_sig[_signature(chosen)][0], fwd_pnls)
    print(
        f"\n  El ganador in-sample cae en el percentil {pct:.0f} del tramo. Cerca de 50 significa que el ajuste"
        "\n  no aporta informacion y un buen resultado es azar; cerca de 90 significa que si predice."
    )

    best_fwd = max(fwd_pnls)
    return {
        "sig": _signature(chosen),
        "in_pnl": best_in[0][1],
        "fwd": fwd_by_sig[_signature(chosen)][0],
        "ops": fwd_by_sig[_signature(chosen)][1],
        "pct": pct,
        "top_pcts": [_percentile_of(fwd_by_sig[_signature(c)][0], fwd_pnls) for c, _, _ in best_in[:top]],
        "median_fwd": statistics.median(fwd_pnls),
        "best_fwd": best_fwd,
        "beats_hold": sum(1 for p in fwd_pnls if p > hold),
        "n": len(fwd_pnls),
    }


def summarize(rows: list[dict]) -> None:
    """The whole point of several decision dates: is percentile 93 a rule or one draw?"""
    print("\n\n" + "=" * 96)
    print("RESUMEN — el ganador in-sample, fecha a fecha, dentro de la distribucion de su tramo")
    print("=" * 96)
    print(
        f"\n  {'decide':<12}{'tramo':>8}{'mantener':>11}{'config elegido':>22}"
        f"{'FUERA BTC':>12}{'percentil':>11}{'bate hold':>11}"
    )
    print("  " + "-" * 87)
    for r in rows:
        print(
            f"  {r['date'][:10]:<12}{f'{r["fwd_days"]:.0f}d':>8}{r['hold']:>10.2f}%{r['sig']:>22}"
            f"{_btc(r['fwd'], r['hold']):>11.2f}%{r['pct']:>10.0f}%{f'{r["beats_hold"]}/{r["n"]}':>11}"
        )

    pcts = [r["pct"] for r in rows]
    all_top = [p for r in rows for p in r["top_pcts"]]
    print(
        f"\n  mediana del percentil del ganador: {statistics.median(pcts):.0f}"
        f"   (peor {min(pcts):.0f}, mejor {max(pcts):.0f}, n={len(pcts)})"
        f"\n  mediana del percentil de los mejores in-sample de cada fecha: {statistics.median(all_top):.0f}"
        f"   (n={len(all_top)})"
        "\n\n  Cerca de 50 significa que el ajuste no aporta informacion sobre el futuro y un buen"
        "\n  resultado suelto es azar. Cerca de 90 significa que la eleccion in-sample predice."
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Barrido exhaustivo: el ajuste, ¿predice o es azar?")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--fit-days", type=int, default=90, help="Longitud de la ventana de ajuste.")
    ap.add_argument(
        "--decide-days",
        type=int,
        nargs="+",
        default=[90, 120, 150, 180, 210, 240, 270, 300, 330],
        help="Dias desde el inicio del marco en los que se decide; cada uno ajusta con los --fit-days previos "
        "y se evalua sobre todo lo que queda.",
    )
    ap.add_argument("--top", type=int, default=10, help="Cuantos ganadores in-sample se detallan.")
    ap.add_argument("--recalib-bars", type=int, default=RECALIBRATION_BARS)
    args = ap.parse_args()

    print(f"[datos] {len(args.files)} ficheros")
    frame = build_frame(args.files)
    _install_ohlc(frame)

    print(f"\n[calibracion] calendario cada {args.recalib_bars} velas (tarda minutos)")
    _install_calibration_cache(frame, args.recalib_bars)

    cands = candidates()
    last_bar = len(frame) - 1
    rows = []

    for decide_days in args.decide_days:
        fit_bars = decide_days * BARS_PER_DAY
        first_fit = fit_bars - args.fit_days * BARS_PER_DAY
        if first_fit < 0 or fit_bars >= last_bar:
            print(f"\n[decide {decide_days}d] no cabe en el marco, se omite")
            continue

        decided_at = _dtime(frame, fit_bars - 1)
        hold = round((float(frame.iloc[last_bar]["close"]) / float(frame.iloc[fit_bars]["close"]) - 1.0) * 100.0, 2)
        fwd_days = (last_bar - fit_bars) / BARS_PER_DAY
        print("\n\n" + "#" * 96)
        print(
            f"[decide {decide_days}d] {len(cands)} configs, sin muestreador y sin semilla"
            f"\n  ajuste  {_dtime(frame, first_fit)[:10]}..{decided_at[:10]} ({args.fit_days}d)"
            f"\n  tramo   {_dtime(frame, fit_bars)[:10]}..{_dtime(frame, last_bar)[:10]} "
            f"({fwd_days:.0f}d)  mantener={hold:+.2f}%",
            flush=True,
        )

        t0 = time.perf_counter()
        in_sample = sweep(_context(frame, first_fit, fit_bars - 1, decided_at), cands)
        forward = sweep(_context(frame, fit_bars, last_bar, decided_at), cands)
        print(f"  barridos en {time.perf_counter() - t0:.0f}s", flush=True)

        row = report(in_sample, forward, hold, args.top)
        row.update({"date": decided_at, "hold": hold, "fwd_days": fwd_days, "decide_days": decide_days})
        rows.append(row)

    if rows:
        summarize(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
