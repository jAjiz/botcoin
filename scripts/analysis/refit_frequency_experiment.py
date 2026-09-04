"""Walk-forward experiment: how often should the bot's config be re-fitted?

Read-only and temporary. Nothing in ``trading/optimizer/`` is modified: the variants
under test are installed as monkeypatches for the duration of a run.

Every arm is scored on ONE continuous run over the same forward span, never restarted.
A reconfiguration enters through the engine's two schedules — the new ``stop_pct`` rides
the calibration schedule, the new ``k_act``/``min_margin`` ride the activation one — so
the config changes from that bar on without liquidating the position the bot happens to
hold, which is what ``PATCH /config/{pair}`` does to the live bot. Scoring in restarted
segments instead would charge an entry fee per boundary and throw the open position
away, and the number of boundaries is the very variable under test: it would penalise
exactly the arm that reconfigures most.

The search is fixed to one stop_pct shared by all five levels and no inner train/test
cut, so the only factor is *how often* the config is re-fitted and *on which data*:

  fijo        fitted once before the span opens, then never touched (the control: its
              number must come out identical for every cadence)
  reajuste    re-fitted at every boundary on the trailing fit window (adapts)
  expansivo   re-fitted at every boundary on all history so far (stable)
  mantener    buy at the start of the span, hold to the end

All cadences share one forward span, so their results — and buy-and-hold — are directly
comparable rather than being chained over different segmentations.

Calibration is progressive everywhere: every ``--recalib-bars`` bars the simulation
adopts the calibration computed from all history up to that bar and no further, which is
what the live bot holds at that instant.

Usage (PYTHONPATH=. required; DB env vars only when --csv is omitted):
  PYTHONPATH=. python scripts/analysis/refit_frequency_experiment.py XBTEUR \
      --csv path/to/XBTEUR_15_*.csv --fit-days 90 --step-days 60 120 180
"""

import argparse
import dataclasses
import itertools
import statistics
import time
from dataclasses import dataclass

import pandas as pd

import core.database as db
import trading.optimizer.search as optimizer
from core.config import ATR_DESV_LIMIT, ATR_PERIOD, CANDLE_TIMEFRAME, PAIRS, PARAM_SESSIONS, SLEEPING_INTERVAL
from core.config import VOLATILITY_LEVELS as LEVELS
from trading.engine import ActivationParams, EngineConfig, mark_to_market, simulate_operations
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
    run_optimize,
)

RECALIB_BARS = max(1, (PARAM_SESSIONS * SLEEPING_INTERVAL) // (CANDLE_TIMEFRAME * 60))
BARS_PER_DAY = (24 * 60) // CANDLE_TIMEFRAME

FEE = 0.4
# The k_act branch is disabled: it won none of the 12 hold-out fits, and defect 5 says why
# — it is the only branch with no ATR-independent floor, so its barrier collapses exactly
# when the market is calm. Dropping it hands the whole trial budget to min_margin.
# min_margin reaches 0.20 because 7 of those 12 winners pinned at 0.090, one step under the
# previous 0.10 ceiling, and a bound a winner touches is a bound to widen. The step is 0.01
# rather than 0.005: with one shared stop_pct the space is 21x5, small enough that seeds
# can agree on a config instead of on a score.
SPACE = SearchSpace(stop_pcts=GridSpec(0.5, 0.9, 0.1), k_act=None, min_margin=GridSpec(0.0, 0.20, 0.01))

ARMS = ("fijo", "reajuste", "expansivo")


# --- patches installed for the whole run -----------------------------------


CSV_COLUMNS = ["time", "open", "high", "low", "close", "volume", "count"]


def _install_csv_ohlc(paths: list[str]) -> None:
    """Serve Kraken's OHLCVT files wherever the optimizer would hit the database.

    Lets the experiment run with no database at all. ATR is computed with the bot's own
    Wilder implementation, exactly as scripts/import_kraken_ohlcvt.py would have stored it.
    """
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
        print(f"  AVISO: {len(holes)} saltos en la serie (mayor: {int(holes.max()) // step} velas)")

    df["atr"] = _wilder_atr_from_scratch(df, ATR_PERIOD)
    df["dtime"] = pd.to_datetime(df["time"], unit="s")

    def loader(pair, timeframe, *a, **kw):
        return df.copy()

    db.load_ohlc_data = loader


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
        if len(points) % 100 == 0:
            print(f"    ... {len(points)} puntos ({time.perf_counter() - t0:.0f}s)", flush=True)
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
class Decision:
    """A config and the frame bar from which it is in force."""

    at_bar: int
    cand: dict


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


def _decisions(
    pair: str, arm: str, fit_bars: int, first_bar: int, last_bar: int, step_bars: int, seed: int, n_trials: int
) -> list[Decision] | None:
    """The configs this arm would have had in force across [first_bar, last_bar].

    Every arm starts from the same config, fitted on the window that ends exactly where
    the forward span opens, so they differ only in whether and on what they re-fit later.
    """
    first = _fit(pair, first_bar - fit_bars, first_bar - 1, seed, n_trials)
    if first is None:
        return None
    out = [Decision(first_bar, first)]
    if arm == "fijo":
        return out

    boundary = first_bar + step_bars
    while boundary <= last_bar:
        if arm == "reajuste":
            cand = _fit(pair, boundary - fit_bars, boundary - 1, seed, n_trials)
        else:
            cand = _fit(pair, 0, boundary - 1, seed, n_trials)
        # A search that finds nothing leaves the previous config in force, which is also
        # what the operator would be left with.
        out.append(Decision(boundary, cand if cand is not None else out[-1].cand))
        boundary += step_bars
    return out


def _as_candidate(cand: dict) -> Candidate:
    return Candidate(k_act=cand.get("k_act"), min_margin=cand.get("min_margin"), stop_pcts=cand.get("stop_pcts"))


def _score_continuous(pair: str, decisions: list[Decision], first_bar: int, last_bar: int) -> tuple[float | None, int]:
    """Score a whole sequence of configs on ONE run that never restarts.

    The configs enter through the engine's two schedules, so a reconfiguration changes
    what the bot does from that bar on without liquidating whatever position it holds.
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
    ctx = _build_eval_context(req, _calibration_at(pair, _dtime(pair, first_bar)))

    # Bar indices relative to the window the engine walks.
    rel = [(d.at_bar - first_bar, _as_candidate(d.cand)) for d in decisions]

    def in_force(bar: int) -> Candidate:
        chosen = rel[0][1]
        for at, cand in rel:
            if at > bar:
                break
            chosen = cand
        return chosen

    # Each calibration point becomes K_STOP values under the percentiles of whichever config
    # was in force at that point: the stop_pct half of a reconfiguration rides the calibration
    # schedule, and only k_act/min_margin need the activation one.
    cal_schedule = tuple(
        (p.at, optimizer._pair_calibration(in_force(p.at), p.atr_ratio_thresholds, p.up_k, p.down_k))
        for p in ctx.calibration_points
    )
    act_schedule = tuple((at, ActivationParams(cand.k_act, cand.min_margin or 0.0)) for at, cand in rel)

    opening = rel[0][1]
    cfg = EngineConfig(
        pair=pair,
        calibration=optimizer._pair_calibration(opening, ctx.atr_ratio_thresholds, ctx.up_k, ctx.down_k),
        k_act=opening.k_act,
        min_margin=opening.min_margin or 0.0,
        atr_desv_limit=ATR_DESV_LIMIT,
        calibration_schedule=cal_schedule,
        activation_schedule=act_schedule,
    )
    ops = simulate_operations(ctx.df, cfg, fee_rate=FEE / 100.0)
    if not ops:
        return None, 0
    marked = mark_to_market(ops, float(ctx.df.iloc[-1]["close"]))
    closed = sum(1 for op in ops if op.pnl_abs is not None and op.idx != 1)
    return round(marked, 2), closed


def _signature(cand: dict) -> str:
    branch = f"k_act={cand['k_act']:.1f}" if cand.get("k_act") is not None else f"mm={cand.get('min_margin'):.3f}"
    return f"{branch} stop={next(iter(cand['stop_pcts'].values())):.1f}"


def run(pair: str, fit_bars: int, step_bars: int, seeds, n_trials: int, fixed: dict) -> dict:
    """One continuous forward run per (arm, seed). Values are (pnl, ops, changes, first signature)."""
    first_bar, last_bar = fit_bars, len(_full_frame(pair)) - 1
    results: dict = {}

    for seed in seeds:
        for arm in ARMS:
            t0 = time.perf_counter()
            # The fijo arm never re-fits, so its configs do not depend on the cadence: its
            # number must come out identical for every cadence, which is the control.
            if arm == "fijo" and seed in fixed:
                decisions = fixed[seed]
            else:
                decisions = _decisions(pair, arm, fit_bars, first_bar, last_bar, step_bars, seed, n_trials)
                if arm == "fijo":
                    fixed[seed] = decisions

            if decisions is None:
                results[(arm, seed)] = (None, 0, 0, "-")
                print(f"    {arm:<11} seed={seed:<5} sin candidato valido")
                continue

            pnl, ops = _score_continuous(pair, decisions, first_bar, last_bar)
            changes = sum(1 for a, b in itertools.pairwise(decisions) if _signature(a.cand) != _signature(b.cand))
            results[(arm, seed)] = (pnl, ops, changes, _signature(decisions[0].cand))
            print(
                f"    {arm:<11} seed={seed:<5} FUERA={pnl!s:>8}%  ops={ops:<4} "
                f"reconfigs={len(decisions) - 1:<3} cambios={changes:<3} "
                f"inicial={_signature(decisions[0].cand):<22} ({time.perf_counter() - t0:.0f}s)",
                flush=True,
            )

    return results


# --- reporting --------------------------------------------------------------


def summarize(results: dict, seeds, hold: float) -> None:
    print(f"\n  {'brazo':<12}{'mediana':>10}{'peor':>9}{'mejor':>9}{'vs hold':>10}{'gana':>7}{'ops':>7}{'cambios':>9}")
    print("  " + "-" * 73)
    for arm in ARMS:
        rows = [results.get((arm, s)) for s in seeds]
        rows = [r for r in rows if r is not None and r[0] is not None]
        if not rows:
            print(f"  {arm:<12}{'sin resultados':>10}")
            continue
        pnls = [r[0] for r in rows]
        median = statistics.median(pnls)
        wins = sum(1 for p in pnls if p > hold)
        print(
            f"  {arm:<12}{median:>9.2f}%{min(pnls):>8.2f}%{max(pnls):>8.2f}%{median - hold:>+9.1f}"
            f"{f'{wins}/{len(rows)}':>7}{round(statistics.median([r[1] for r in rows])):>7}"
            f"{round(statistics.median([r[2] for r in rows])):>9}"
        )
    print(f"  {'mantener':<12}{hold:>9.2f}%{hold:>8.2f}%{hold:>8.2f}%{0.0:>+9.1f}{'-':>7}{1:>7}{0:>9}")


def main() -> None:
    global FEE
    ap = argparse.ArgumentParser(description="Walk-forward: re-fit cadence against a single fit and buy-and-hold.")
    ap.add_argument("pairs", nargs="*", help="Pares (por defecto: PAIRS de config).")
    ap.add_argument("--fit-days", type=int, default=90, help="Dias de datos que ve cada ajuste.")
    ap.add_argument(
        "--step-days",
        type=int,
        nargs="+",
        default=[60, 120, 180],
        help="Cada cuantos dias se reconfigura. Varias cadencias en una sola corrida comparten el tramo y el "
        "calendario de calibracion, que asi se paga una vez.",
    )
    ap.add_argument("--csv", nargs="+", default=None, help="Ficheros OHLCVT de Kraken; sin esto se lee de la BD.")
    ap.add_argument("--seeds", type=str, default="42,7,99")
    ap.add_argument("--n-trials", type=int, default=100)
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

    if args.csv:
        _install_csv_ohlc(args.csv)
    _install_shared_ohlc_cache()
    _force_sequential_branches()
    optimizer.build_calibration_inputs = _cached_calibration_inputs
    optimizer._suggest_stops = _shared_suggest_stops
    optimizer._candidate_from_params = _shared_candidate_from_params

    print(
        f"[experimento] pares={pairs} ajuste={args.fit_days}d cadencias={args.step_days}d "
        f"semillas={seeds} n_trials={args.n_trials} fee={FEE} recalib_bars={args.recalib_bars}"
    )
    t0 = time.perf_counter()
    for pair in pairs:
        df = _full_frame(pair)
        print(f"\n=== {pair}  {len(df)} velas  {_dtime(pair, 0)[:10]}..{_dtime(pair, len(df) - 1)[:10]} ===")
        # The points depend only on the pair and the calibration cadence, not on the refit
        # cadence, so every --step-days value is served by the same set.
        _set_points(_global_cal_points(pair, args.recalib_bars), pair)

        first_bar, last_bar = fit_bars, len(df) - 1
        hold = round(_hold_return(pair, first_bar, last_bar), 2)
        print(
            f"\n[tramo] {_dtime(pair, first_bar)[:10]}..{_dtime(pair, last_bar)[:10]} "
            f"({(last_bar - first_bar) / BARS_PER_DAY:.0f}d)  mantener={hold:+.2f}%"
            "\n        identico para todas las cadencias, asi que sus resultados son directamente comparables"
        )

        fixed: dict = {}
        for step_days in args.step_days:
            n_dec = (last_bar - first_bar) // (step_days * BARS_PER_DAY) + 1
            print(f"\n\n### cadencia {step_days}d — {n_dec} configuraciones sobre el tramo ###")
            summarize(run(pair, fit_bars, step_days * BARS_PER_DAY, seeds, args.n_trials, fixed), seeds, hold)
    print(f"\ntotal {time.perf_counter() - t0:.0f}s")


if __name__ == "__main__":
    main()
