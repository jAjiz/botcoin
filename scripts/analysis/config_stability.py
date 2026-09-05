"""Is any config reliably good, or is config quality not persistent at all?

Read-only and temporary. Reads Kraken's OHLCVT CSVs directly, so no database is needed.

The predictiveness sweep showed that picking the config with the best in-sample PnL lands
at median percentile 50 of the forward distribution across nine decision dates — no better
than drawing one at random. That kills selection-by-in-sample-PnL, but it leaves a
different question open, and it is the one that decides whether any config is deployable:

  does a config that did well in one window tend to do well in the next?

If yes, the search should select for cross-window stability instead of for in-sample PnL,
and a robust config exists to be found. If no — if a config's rank in one window says
nothing about its rank in the next — then no objective repairs this, and the problem is the
strategy or its parameterisation rather than the optimizer.

Method. Every config runs **once, continuously**, over the whole span after a warmup, and
that single run is then split into N consecutive periods by the ratio of its compounded
growth factors at each boundary. Nothing is ever restarted: the position a config holds at
a boundary carries into the next period, exactly as it does for the live bot.

Only **ranks within a period** are compared, never levels across periods, which removes the
market's regime from the comparison — every config in a period faces the same market.

An earlier version of this harness swept N *disjoint restarted* windows instead, and that
was wrong in a way worth recording. A 60-day window never lets a config whose activation
barrier takes 30-90 days to cross activate at all, so it pins the low-frequency family this
study cares about to the zero-operation value. That distorts the ranking and not merely the
levels, since it hits some configs and not others. The tell was two measurements of the
same data disagreeing: +22.3 % median base-asset gain over 364 continuous days against
about -0.4 % in every restarted 60-day window.

The headline statistic is the Spearman rank correlation between consecutive windows. Near
0 means config quality does not persist and there is nothing to select. Near 1 means it
does.

Usage (PYTHONPATH=. required; no DB env vars needed):
  PYTHONPATH=. python scripts/analysis/config_stability.py path/to/XBTEUR_15_*.csv
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
SPACE = SearchSpace(stop_pcts=STOP_GRID, k_act=None, min_margin=MM_GRID)


def _grid(g: GridSpec) -> list[float]:
    n = round((g.end - g.start) / g.step)
    return [round(g.start + i * g.step, 5) for i in range(n + 1)]


def candidates() -> list[Candidate]:
    return [
        Candidate(k_act=None, min_margin=mm, stop_pcts=dict.fromkeys(LEVELS, stop))
        for mm in _grid(MM_GRID)
        for stop in _grid(STOP_GRID)
    ]


def _signature(cand: Candidate) -> str:
    return f"mm={cand.min_margin:.3f} s={next(iter(cand.stop_pcts.values())):.1f}"


# --- data and calibration (same pattern as the other harnesses) --------------


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


_POINTS: list[CalibrationInputs] = []
_POINT_TIMES: list[str] = []


def _install_calibration_cache(frame: pd.DataFrame, recalib_bars: int) -> None:
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


def _period_returns(ops, bounds: list[tuple[str, float]], final_price: float) -> list[float]:
    """Split ONE continuous run into per-period returns, without ever restarting it.

    ``bounds`` carries (time, price) at the *start* of periods 2..N; the run's end closes
    the last period. Each period's return is the ratio of compounded growth factors, the
    n-way form of ``optimizer._second_half_net`` — a period is what the portfolio did while
    it ran, and the position it holds at a boundary simply carries across.
    """
    cums = [mark_to_market([op for op in ops if str(op.time) < t], price) for t, price in bounds]
    cums.append(mark_to_market(ops, final_price))

    out, prev = [], 1.0
    for cum in cums:
        factor = 1.0 + cum / 100.0
        out.append(-100.0 if prev <= 0.0 else ((factor / prev) - 1.0) * 100.0)
        prev = factor
    return out


def sweep_continuous(
    frame: pd.DataFrame, first_bar: int, last_bar: int, starts: list[int], cands: list[Candidate]
) -> list[list[float]]:
    """Every config on ONE run over [first_bar, last_bar], scored per period. [period][config].

    The run is never restarted, so no config is truncated at a boundary and none pays an
    entry fee it would not have paid. That was the flaw in the first version of this
    harness: 60-day disjoint windows never let a config whose activation barrier takes
    30-90 days to cross activate at all, which pushed exactly the low-frequency family this
    study cares about onto the zero-operation value and distorted the ranking, not just the
    levels. The tell was that the same data gave a +22.3 % median over 364 continuous days
    and about -0.4 % in every 60-day restarted window.
    """
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
    # Calibration frozen at the run's open; the schedule carries the rest, past-only.
    ctx = _build_eval_context(req, _calibration_at(frame, _dtime(frame, first_bar)))
    final_price = float(ctx.df.iloc[-1]["close"])
    bounds = [(_dtime(frame, b), float(frame.iloc[b]["close"])) for b in starts]

    per_config = []
    for cand in cands:
        cfg = optimizer._build_engine_config(
            PAIR, cand, ctx.atr_ratio_thresholds, ctx.up_k, ctx.down_k, ATR_DESV_LIMIT, ctx.calibration_points
        )
        ops = simulate_operations(ctx.df, cfg, fee_rate=FEE / 100.0)
        per_config.append([0.0] * (len(bounds) + 1) if not ops else _period_returns(ops, bounds, final_price))
    # [config][period] -> [period][config]
    return [list(col) for col in zip(*per_config, strict=True)]


def _ranks(values: list[float]) -> list[float]:
    """Average ranks, 1 = worst. Ties share their mean rank so the correlation stays valid."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def _percentiles(values: list[float]) -> list[float]:
    n = len(values)
    return [100.0 * (r - 1) / (n - 1) for r in _ranks(values)]


def _btc(eur_pct: float, hold_pct: float) -> float:
    return ((1.0 + eur_pct / 100.0) / (1.0 + hold_pct / 100.0) - 1.0) * 100.0


# --- reporting --------------------------------------------------------------


def report(cands: list[Candidate], results: list[list[float]], labels: list[str], holds: list[float], top: int) -> None:
    pcts = [_percentiles(w) for w in results]
    n_win = len(results)

    print("\n\n" + "=" * 100)
    print("PERSISTENCIA — correlacion de rangos entre ventanas consecutivas")
    print("=" * 100)
    print("\n  Si la calidad de un config persiste, un config bien situado en una ventana lo esta")
    print("  tambien en la siguiente y la correlacion es alta. Cerca de 0 no hay nada que elegir.\n")
    consec = []
    for i in range(n_win - 1):
        rho = statistics.correlation(_ranks(results[i]), _ranks(results[i + 1]))
        consec.append(rho)
        print(f"  {labels[i]} -> {labels[i + 1]}   rho = {rho:+.2f}")
    print(f"\n  mediana consecutiva: {statistics.median(consec):+.2f}")

    pairs = [
        statistics.correlation(_ranks(results[i]), _ranks(results[j]))
        for i in range(n_win)
        for j in range(i + 1, n_win)
    ]
    print(f"  mediana de todos los pares ({len(pairs)}): {statistics.median(pairs):+.2f}")

    print("\n\n" + "=" * 100)
    print("CONFIGS MAS ESTABLES — ordenados por percentil mediano entre ventanas")
    print("=" * 100)
    rows = []
    for idx, cand in enumerate(cands):
        per = [pcts[w][idx] for w in range(n_win)]
        rows.append((cand, statistics.median(per), min(per), max(per), per))
    rows.sort(key=lambda r: r[1], reverse=True)

    header = f"\n  {'config':<18}{'mediana':>9}{'peor':>7}{'mejor':>7}   " + "".join(
        f"{lab[5:10]:>8}" for lab in labels
    )
    print(header)
    print("  " + "-" * (41 + 8 * n_win))
    for cand, med, lo, hi, per in rows[:top]:
        line = f"  {_signature(cand):<18}{med:>8.0f}%{lo:>6.0f}%{hi:>6.0f}%   " + "".join(f"{p:>8.0f}" for p in per)
        print(line)
    print("  ...")
    for cand, med, lo, hi, per in rows[-3:]:
        line = f"  {_signature(cand):<18}{med:>8.0f}%{lo:>6.0f}%{hi:>6.0f}%   " + "".join(f"{p:>8.0f}" for p in per)
        print(line)

    best = rows[0]
    print(
        f"\n  El mas estable ({_signature(best[0])}) tiene percentil mediano {best[1]:.0f} y su peor"
        f"\n  ventana es el percentil {best[2]:.0f}. Un percentil mediano alto con un peor caso muy bajo"
        "\n  significa que tampoco ese config es fiable, solo que tuvo mas ventanas buenas."
    )

    print("\n\n" + "=" * 100)
    print("POR VENTANA — que hace el espacio entero, en BTC acumulado")
    print("=" * 100)
    print(f"\n  {'ventana':<24}{'mantener':>10}{'mejor':>10}{'mediana':>10}{'peor':>10}{'bate hold':>12}")
    print("  " + "-" * 76)
    for w in range(n_win):
        vals = results[w]
        hold = holds[w]
        print(
            f"  {labels[w]:<24}{0.0:>9.1f}%{_btc(max(vals), hold):>9.1f}%"
            f"{_btc(statistics.median(vals), hold):>9.1f}%{_btc(min(vals), hold):>9.1f}%"
            f"{f'{sum(1 for v in vals if v > hold)}/{len(vals)}':>12}"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="¿Persiste la calidad de un config entre ventanas?")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--warmup-days", type=int, default=90, help="Dias iniciales que no se evaluan.")
    ap.add_argument("--windows", type=int, default=6, help="Ventanas disjuntas consecutivas.")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--recalib-bars", type=int, default=RECALIBRATION_BARS)
    args = ap.parse_args()

    print(f"[datos] {len(args.files)} ficheros")
    frame = build_frame(args.files)
    _install_ohlc(frame)

    print(f"\n[calibracion] calendario cada {args.recalib_bars} velas (tarda minutos)")
    _install_calibration_cache(frame, args.recalib_bars)

    warmup = args.warmup_days * BARS_PER_DAY
    last_bar = len(frame) - 1
    width = (last_bar - warmup) // args.windows
    starts = [warmup + w * width for w in range(args.windows)]
    cands = candidates()

    labels, holds = [], []
    for w in range(args.windows):
        first = starts[w]
        last = starts[w + 1] - 1 if w + 1 < args.windows else last_bar
        labels.append(f"{_dtime(frame, first)[:10]}..{_dtime(frame, last)[:10]}")
        holds.append(round((float(frame.iloc[last]["close"]) / float(frame.iloc[first]["close"]) - 1.0) * 100.0, 2))

    print(
        f"\n[barrido] {len(cands)} configs, UNA corrida continua de "
        f"{(last_bar - warmup) / BARS_PER_DAY:.0f}d partida en {args.windows} periodos de "
        f"{width / BARS_PER_DAY:.0f}d, sin muestreador y sin semilla"
    )
    for label, hold in zip(labels, holds, strict=True):
        print(f"  {label}  mantener={hold:+.2f}%")

    t0 = time.perf_counter()
    results = sweep_continuous(frame, warmup, last_bar, starts[1:], cands)
    print(f"  barrido en {time.perf_counter() - t0:.0f}s", flush=True)

    report(cands, results, labels, holds, args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
