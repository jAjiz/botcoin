"""Walk-forward experiment: does re-fitting the config often beat fitting it once?

Read-only. Nothing in ``trading/optimizer/`` is modified: the variants under test
are installed as monkeypatches for the duration of a run.

The search is fixed to the proposed setup — one stop_pct shared by all five levels,
no inner train/test cut — so the only factor is *how often* the config is re-fitted
and *on which data*:

  fijo        fitted once on the first fit window, then applied to every segment
  reajuste    re-fitted at every step on the trailing fit window (adapts, few ops)
  expansivo   re-fitted at every step on all history so far (stable, barely moves)
  hold        buy at the segment start, sell at its end

Each arm is judged only on disjoint forward segments it never saw. Segment returns
are also chained, since that is the number an account would actually have seen.

Calibration is progressive everywhere: every ``--recalib-bars`` bars the simulation
adopts the calibration computed from all history up to that bar and no further,
which is what the live bot holds at that instant.

Usage (PYTHONPATH=. and DB env vars required):
  PYTHONPATH=. python scripts/analysis/refit_frequency_experiment.py XBTEUR
"""

import argparse
import dataclasses
import statistics
import time
from dataclasses import dataclass, field

import core.database as db
import trading.optimizer.search as optimizer
from core.config import ATR_DESV_LIMIT, CANDLE_TIMEFRAME, PAIRS, PARAM_SESSIONS, SLEEPING_INTERVAL
from core.config import VOLATILITY_LEVELS as LEVELS
from trading.engine import mark_to_market, simulate_operations
from trading.market_analyzer import (
    CalibrationInputs,
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
    run_optimize,
)

RECALIB_BARS = max(1, (PARAM_SESSIONS * SLEEPING_INTERVAL) // (CANDLE_TIMEFRAME * 60))
BARS_PER_DAY = (24 * 60) // CANDLE_TIMEFRAME

FEE = 0.4
SPACE = SearchSpace(
    stop_pcts=GridSpec(0.5, 0.9, 0.1), k_act=GridSpec(0.0, 6.0, 0.5), min_margin=GridSpec(0.0, 0.010, 0.002)
)

ARMS = ("fijo", "reajuste", "expansivo")


# --- patches installed for the whole run -----------------------------------


def _install_shared_ohlc_cache() -> None:
    """Memoize load_ohlc_data: one load costs ~10 s and every fit reloads it."""
    real = db.load_ohlc_data
    cache: dict[tuple, object] = {}

    def cached(pair, timeframe, *a, **kw):
        if a or kw:
            return real(pair, timeframe, *a, **kw)
        key = (pair, timeframe)
        if key not in cache:
            cache[key] = real(pair, timeframe)
        return cache[key].copy()

    db.load_ohlc_data = cached


def _force_sequential_branches() -> None:
    """Spawned branch workers re-import the module, so a monkeypatch would not apply inside them."""
    optimizer._PARALLEL_MIN_TRIALS = 10**9


_REAL_SUGGEST_STOPS = optimizer._suggest_stops
_REAL_CANDIDATE_FROM_PARAMS = optimizer._candidate_from_params
_ANCHOR = LEVELS[0]


def _shared_suggest_stops(trial, grid: GridSpec) -> dict[str, float]:
    """Suggest one stop_pct and apply it to every level."""
    value = trial.suggest_float(f"stop_pct_{_ANCHOR}", grid.start, grid.end, step=grid.step)
    return dict.fromkeys(LEVELS, value)


def _shared_candidate_from_params(params: dict) -> Candidate:
    """Rebuild a candidate when only the anchor level was registered as a param."""
    anchor = params[f"stop_pct_{_ANCHOR}"]
    stop_pcts = {lvl: params.get(f"stop_pct_{lvl}", anchor) for lvl in LEVELS}
    if "k_act" in params:
        return Candidate(k_act=params["k_act"], min_margin=None, stop_pcts=stop_pcts)
    return Candidate(k_act=None, min_margin=params.get("min_margin", 0.0), stop_pcts=stop_pcts)


# --- progressive calibration ------------------------------------------------

# The engine and the optimizer build a calibration schedule on their own, but the
# in-tree builder recomputes it per call, and a walk-forward asks for hundreds of
# overlapping windows over the same history. So the points are computed once for the
# whole frame and every window is served by slicing them. Anchoring the grid to the
# frame (not to each window's first bar) also matches the live bot, which
# recalibrates on its own clock regardless of where an analysis window happens to
# start.

_POINTS: list[CalibrationInputs] = []
_POINT_TIMES: list[str] = []


def _global_cal_points(pair: str, recalib_bars: int) -> list[CalibrationInputs]:
    """Calibration inputs every ``recalib_bars`` bars of the whole frame, past-only."""
    df_full = _full_frame(pair)
    points = []
    t0 = time.perf_counter()
    for idx in range(0, len(df_full), recalib_bars):
        cal_df = df_full.iloc[: idx + 1]
        up_events, down_events = analyze_structural_noise(cal_df)
        points.append(
            CalibrationInputs(
                idx, atr_ratio_percentiles(cal_df), k_values_by_level(up_events), k_values_by_level(down_events)
            )
        )
    print(f"[calibracion] {len(points)} puntos globales cada {recalib_bars} velas ({time.perf_counter() - t0:.0f}s)")
    return points


def _set_points(points: list[CalibrationInputs], pair: str) -> None:
    global _POINTS, _POINT_TIMES
    frame = _full_frame(pair)
    _POINTS = points
    _POINT_TIMES = [str(frame.iloc[p.at]["dtime"]) for p in points]


def _cached_calibration_inputs(_df_full, df, recalib_bars: int) -> tuple:
    """Stand in for market_analyzer.build_calibration_inputs, served from the cached points.

    Entry 0 always exists and carries the calibration already in force when the window
    opens, so the schedule governs the run from its very first bar.
    """
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


# --- data -------------------------------------------------------------------

_FRAMES: dict = {}
# Bars are addressed by position, so a hole in the series would silently shorten
# every window that spans it: bound the frame to one continuous stretch.
_BOUNDS: tuple[str | None, str | None] = (None, None)


def set_bounds(start: str | None, end: str | None) -> None:
    _FRAMES.clear()
    global _BOUNDS
    _BOUNDS = (start, end)


def _full_frame(pair: str):
    if pair not in _FRAMES:
        df = db.load_ohlc_data(pair, CANDLE_TIMEFRAME).dropna(subset=["atr"]).sort_values("time")
        start, end = _BOUNDS
        if start:
            df = df[df["dtime"] >= start]
        if end:
            df = df[df["dtime"] <= end]
        _FRAMES[pair] = df.reset_index(drop=True)
    return _FRAMES[pair]


_CAL_CACHE: dict = {}


def _calibration_at(pair: str, cutoff: str) -> dict:
    """Structural events and ATR percentiles from the start of history up to ``cutoff``."""
    key = (pair, cutoff)
    if key not in _CAL_CACHE:
        df = _full_frame(pair)
        cal_df = df[df["dtime"] <= cutoff].reset_index(drop=True)
        up_events, down_events = analyze_structural_noise(cal_df)
        p20, p50, p80, p95 = atr_ratio_percentiles(cal_df)
        _CAL_CACHE[key] = {
            "up_events": up_events,
            "down_events": down_events,
            "atr_ratio_p20": p20,
            "atr_ratio_p50": p50,
            "atr_ratio_p80": p80,
            "atr_ratio_p95": p95,
        }
    return _CAL_CACHE[key]


def _dtime(pair: str, bar: int) -> str:
    return str(_full_frame(pair).iloc[bar]["dtime"])


def _hold_return(pair: str, first_bar: int, last_bar: int) -> float:
    df = _full_frame(pair)
    opened = float(df.iloc[first_bar]["close"])
    closed = float(df.iloc[last_bar]["close"])
    return (closed / opened - 1.0) * 100.0


# --- experiment -------------------------------------------------------------


@dataclass
class Segment:
    pair: str
    step: int
    first_bar: int
    last_bar: int
    hold: float
    results: dict = field(default_factory=dict)  # (arm, seed) -> (pnl, ops, signature)


def _fit(pair: str, first_bar: int, last_bar: int, seed: int, n_trials: int) -> dict | None:
    """Search the best config on [first_bar, last_bar], calibrated with the past only."""
    start, end = _dtime(pair, first_bar), _dtime(pair, last_bar)
    req = OptimizerRequest(
        pair=pair,
        mode="OPTIMIZE",
        fee_pct=FEE,
        start=start,
        end=end,
        train_split=1.0,
        n_trials=n_trials,
        seed=seed,
        search_space=SPACE,
    )
    try:
        return run_optimize(req, _calibration_at(pair, end)).top_candidates[0]
    except ValueError:
        return None


def _score(pair: str, cand: dict, first_bar: int, last_bar: int, decided_at: str) -> tuple[float | None, int]:
    """Score a config on a forward segment it never saw, valuing the position left open.

    A window is a slice of a run that never stops, so it always ends mid-position.
    Chaining the realized totals would drop that leg from every segment.
    """
    req = OptimizerRequest(
        pair=pair,
        mode="OPTIMIZE",
        fee_pct=FEE,
        start=_dtime(pair, first_bar),
        end=_dtime(pair, last_bar),
        train_split=1.0,
        n_trials=1,
        seed=0,
        search_space=SPACE,
    )
    ctx = _build_eval_context(req, _calibration_at(pair, decided_at))
    cfg = optimizer._build_engine_config(
        pair,
        Candidate(k_act=cand.get("k_act"), min_margin=cand.get("min_margin"), stop_pcts=cand.get("stop_pcts")),
        ctx.atr_ratio_thresholds,
        ctx.up_k,
        ctx.down_k,
        ATR_DESV_LIMIT,
        ctx.calibration_points,
    )
    fee_rate = FEE / 100.0
    ops = simulate_operations(ctx.df, cfg, fee_rate=fee_rate)
    if not ops:
        return None, 0
    marked = mark_to_market(ops, float(ctx.df.iloc[-1]["close"]))
    closed = sum(1 for op in ops if op.pnl_abs is not None and op.idx != 1)
    return round(marked, 2), closed


def _signature(cand: dict) -> str:
    branch = f"k_act={cand['k_act']:.1f}" if cand.get("k_act") is not None else f"mm={cand.get('min_margin'):.3f}"
    return f"{branch} stop={next(iter(cand['stop_pcts'].values())):.1f}"


def run(pair: str, fit_bars: int, step_bars: int, seeds, n_trials: int) -> list[Segment]:
    n = len(_full_frame(pair))
    segments: list[Segment] = []
    fixed: dict[int, dict] = {}

    step = 0
    while fit_bars + (step + 1) * step_bars <= n:
        boundary = fit_bars + step * step_bars
        first, last = boundary, boundary + step_bars - 1
        seg = Segment(pair, step, first, last, round(_hold_return(pair, first, last), 2))
        decided_at = _dtime(pair, boundary)
        print(
            f"\n  paso {step:<3} decide en {decided_at[:10]}  "
            f"evalua [{_dtime(pair, first)[:10]}..{_dtime(pair, last)[:10]}]  mantener={seg.hold:+.2f}%"
        )

        for seed in seeds:
            for arm in ARMS:
                t0 = time.perf_counter()
                if arm == "fijo":
                    if seed not in fixed:
                        fixed[seed] = _fit(pair, 0, fit_bars - 1, seed, n_trials)
                    cand = fixed[seed]
                elif arm == "reajuste":
                    cand = _fit(pair, boundary - fit_bars, boundary - 1, seed, n_trials)
                else:
                    cand = _fit(pair, 0, boundary - 1, seed, n_trials)

                if cand is None:
                    seg.results[(arm, seed)] = (None, 0, "-")
                    print(f"    {arm:<11} seed={seed:<4} sin candidato valido")
                    continue

                pnl, ops = _score(pair, cand, first, last, decided_at)
                seg.results[(arm, seed)] = (pnl, ops, _signature(cand))
                print(
                    f"    {arm:<11} seed={seed:<4} FUERA={pnl!s:>8}  ops={ops:<3} "
                    f"{_signature(cand):<22} ({time.perf_counter() - t0:.0f}s)"
                )

        segments.append(seg)
        step += 1

    return segments


# --- reporting --------------------------------------------------------------


def _chain(returns: list[float]) -> float:
    """Compound a list of percentage returns into one."""
    factor = 1.0
    for r in returns:
        factor *= 1.0 + (r / 100.0)
    return (factor - 1.0) * 100.0


def summarize(segments: list[Segment], seeds) -> None:
    if not segments:
        print("\nSin segmentos: no hay datos suficientes para esa ventana y paso.")
        return

    holds = [s.hold for s in segments]
    print("\n\n" + "=" * 78)
    print(f"RESUMEN — {len(segments)} segmentos hacia delante, {len(seeds)} semillas")
    print("=" * 78)

    print(f"\n{'brazo':<14}{'encadenado':>12}{'mediana':>10}{'positivos':>11}{'gana a hold':>13}{'peor':>9}")
    print("-" * 69)
    for arm in ARMS:
        chained, medians, wins, positives, worst = [], [], [], [], []
        for seed in seeds:
            vals = [s.results.get((arm, seed), (None, 0, "-"))[0] for s in segments]
            vals = [v if v is not None else 0.0 for v in vals]
            chained.append(_chain(vals))
            medians.append(statistics.median(vals))
            positives.append(sum(1 for v in vals if v > 0))
            wins.append(sum(1 for v, h in zip(vals, holds, strict=True) if v > h))
            worst.append(min(vals))
        print(
            f"{arm:<14}{statistics.median(chained):>11.2f}%{statistics.median(medians):>10.2f}"
            f"{f'{round(statistics.median(positives))}/{len(segments)}':>11}"
            f"{f'{round(statistics.median(wins))}/{len(segments)}':>13}{min(worst):>9.2f}"
        )
    print(
        f"{'mantener':<14}{_chain(holds):>11.2f}%{statistics.median(holds):>10.2f}"
        f"{f'{sum(1 for h in holds if h > 0)}/{len(segments)}':>11}{'-':>13}{min(holds):>9.2f}"
    )

    print("\n\nPor segmento (mediana entre semillas):")
    header = f"{'paso':<6}{'periodo':<24}" + "".join(f"{a:>12}" for a in ARMS) + f"{'mantener':>12}"
    print("\n" + header)
    print("-" * len(header))
    for s in segments:
        line = f"{s.step:<6}{_dtime_range(s):<24}"
        for arm in ARMS:
            vals = [s.results.get((arm, seed), (None, 0, "-"))[0] for seed in seeds]
            vals = [v for v in vals if v is not None]
            line += f"{statistics.median(vals):>12.2f}" if vals else f"{'-':>12}"
        line += f"{s.hold:>12.2f}"
        print(line)

    print("\n\nEstabilidad de la configuracion elegida (semilla mas baja):")
    seed = seeds[0]
    print(f"\n{'paso':<6}" + "".join(f"{a:>26}" for a in ARMS))
    print("-" * (6 + 26 * len(ARMS)))
    changes = dict.fromkeys(ARMS, 0)
    previous = dict.fromkeys(ARMS, None)
    for s in segments:
        line = f"{s.step:<6}"
        for arm in ARMS:
            sig = s.results.get((arm, seed), (None, 0, "-"))[2]
            if previous[arm] is not None and sig != previous[arm]:
                changes[arm] += 1
            previous[arm] = sig
            line += f"{sig:>26}"
        print(line)
    print("\ncambios de configuracion: " + "  ".join(f"{a}={changes[a]}/{len(segments) - 1}" for a in ARMS))

    print("\n\nRuido entre semillas (rango del PnL fuera de muestra por segmento):")
    print(f"\n{'brazo':<14}{'rango medio':>14}{'configs unicas':>17}")
    print("-" * 45)
    for arm in ARMS:
        spans, uniq = [], []
        for s in segments:
            vals = [s.results.get((arm, sd), (None, 0, "-"))[0] for sd in seeds]
            vals = [v for v in vals if v is not None]
            sigs = {s.results.get((arm, sd), (None, 0, "-"))[2] for sd in seeds}
            if len(vals) > 1:
                spans.append(max(vals) - min(vals))
                uniq.append(len(sigs))
        if spans:
            print(f"{arm:<14}{statistics.mean(spans):>14.2f}{statistics.mean(uniq):>17.1f}")


def _dtime_range(s: Segment) -> str:
    return f"{_dtime(s.pair, s.first_bar)[:10]}..{_dtime(s.pair, s.last_bar)[:10]}"


def main() -> None:
    global FEE
    ap = argparse.ArgumentParser(description="Walk-forward: re-fit frequency against a single fit and buy-and-hold.")
    ap.add_argument("pairs", nargs="*", help="Pares (por defecto: PAIRS de config).")
    ap.add_argument("--fit-days", type=int, default=30, help="Dias de datos que ve cada ajuste (brazo reajuste).")
    ap.add_argument("--step-days", type=int, default=7, help="Cada cuantos dias se reajusta y se puntua.")
    ap.add_argument("--seeds", type=str, default="42,7,99")
    ap.add_argument("--n-trials", type=int, default=300)
    ap.add_argument("--recalib-bars", type=int, default=RECALIB_BARS)
    ap.add_argument("--from-date", type=str, default=None, help="Primera fecha de la serie continua a usar.")
    ap.add_argument("--to-date", type=str, default=None, help="Ultima fecha de la serie continua a usar.")
    ap.add_argument("--fee", type=float, default=FEE, help="Comision por operacion, en porcentaje.")
    args = ap.parse_args()

    FEE = args.fee
    set_bounds(args.from_date, args.to_date)

    pairs = args.pairs or [p for p in PAIRS if p]
    seeds = [int(s) for s in args.seeds.split(",")]
    fit_bars = args.fit_days * BARS_PER_DAY
    step_bars = args.step_days * BARS_PER_DAY

    _install_shared_ohlc_cache()
    _force_sequential_branches()
    optimizer.build_calibration_inputs = _cached_calibration_inputs
    optimizer._suggest_stops = _shared_suggest_stops
    optimizer._candidate_from_params = _shared_candidate_from_params

    print(
        f"[experimento] pares={pairs} ajuste={args.fit_days}d paso={args.step_days}d "
        f"semillas={seeds} n_trials={args.n_trials} fee={FEE} recalib_bars={args.recalib_bars}"
    )
    t0 = time.perf_counter()
    for pair in pairs:
        df = _full_frame(pair)
        print(f"\n=== {pair}  {len(df)} velas  {_dtime(pair, 0)[:10]}..{_dtime(pair, len(df) - 1)[:10]} ===")
        _set_points(_global_cal_points(pair, args.recalib_bars), pair)
        summarize(run(pair, fit_bars, step_bars, seeds, args.n_trials), seeds)
    print(f"\ntotal {time.perf_counter() - t0:.0f}s")


if __name__ == "__main__":
    main()
