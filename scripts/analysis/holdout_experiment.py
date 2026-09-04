"""Task 1 of the validation study: does a config fitted on the first N days beat
buy-and-hold over everything that comes after, measured in euros?

Read-only and temporary, like everything else in this directory. It reads Kraken's
OHLCVT CSVs directly and monkeypatches ``db.load_ohlc_data`` with the frame it
builds, so no database is needed.

What separates this harness from the others here is the scoring shape. The winning
config is scored on ONE continuous ``simulate_operations`` call over the whole
remainder. No segment restart: a restart charges an entry fee the running bot never
pays and throws the open position away at every boundary, which destroys the very
mechanism a trailing stop that rides a move for weeks depends on. See "Harness
defects" in docs/specs/optimizer-validation-design.md.

What is frozen over the hold-out span, and what is not:

  frozen   the candidate's stop_pct percentiles, and its k_act / min_margin
  moving   K_STOP itself and the volatility thresholds, recalibrated every
           --recalib-bars bars from data up to that bar only, exactly as the live
           bot does

So "fit once and never touch it" freezes the *shape* of the config, not the
distances it produces: a stop_pct of 0.9 keeps meaning "the 90th percentile of the
retracements observed so far", and what that percentile is worth in euros is
recomputed as the run advances.

Usage (PYTHONPATH=. required; no DB env vars needed):
  PYTHONPATH=. python scripts/analysis/holdout_experiment.py path/to/XBTEUR_15_*.csv
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
    run_optimize,
)

CSV_COLUMNS = ["time", "open", "high", "low", "close", "volume", "count"]
PAIR = "XBTEUR"
FEE = 0.4
BARS_PER_DAY = (24 * 60) // CANDLE_TIMEFRAME

# min_margin reaches 0.10: it is the only ATR-independent activation floor, and the
# old 0.010 ceiling could not express quiescence, so buy-and-hold was outside the
# space. stop_pcts stops at 0.9: at 1.0 the stop is set by a single observation.
SPACE = SearchSpace(
    stop_pcts=GridSpec(0.5, 0.9, 0.1), k_act=GridSpec(0.0, 6.0, 0.5), min_margin=GridSpec(0.0, 0.10, 0.005)
)


# --- data -------------------------------------------------------------------


def build_frame(paths: list[str]) -> pd.DataFrame:
    """One continuous frame from the CSVs, with the bot's own Wilder ATR."""
    frames = []
    for path in paths:
        part = pd.read_csv(path, header=None, names=CSV_COLUMNS)
        print(f"  {path}: {len(part)} velas")
        frames.append(part)
    df = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["time"]).sort_values("time")
    df = df.reset_index(drop=True)

    # Bars are addressed by position here, so a hole would silently shorten every
    # window that spans it.
    step = CANDLE_TIMEFRAME * 60
    deltas = df["time"].diff().dropna()
    holes = deltas[deltas != step]
    if len(holes):
        print(f"\n  AVISO: {len(holes)} saltos en la serie (mayor: {int(holes.max()) // step} velas)")

    df["atr"] = _wilder_atr_from_scratch(df, ATR_PERIOD)
    df["dtime"] = pd.to_datetime(df["time"], unit="s")
    # Mirror what _build_eval_context does to df_full, so bar positions agree.
    df = df.dropna(subset=["atr"]).sort_values("time").reset_index(drop=True)
    print(f"\n  marco: {len(df)} velas con ATR  {_stamp(df, 0)}..{_stamp(df, len(df) - 1)}")
    return df


def _stamp(frame: pd.DataFrame, bar: int) -> str:
    return str(frame.iloc[bar]["dtime"])[:16]


def _dtime(frame: pd.DataFrame, bar: int) -> str:
    return str(frame.iloc[bar]["dtime"])


# --- patches installed for the whole run ------------------------------------


def _install_ohlc(frame: pd.DataFrame) -> None:
    """Serve the CSV frame wherever the optimizer would hit the database."""

    def loader(pair, timeframe, *a, **kw):
        return frame.copy()

    db.load_ohlc_data = loader


def _force_sequential_branches() -> None:
    """Spawned branch workers re-import the module, so a monkeypatch would not apply inside them."""
    optimizer._PARALLEL_MIN_TRIALS = 10**9


_POINTS: list[CalibrationInputs] = []
_POINT_TIMES: list[str] = []


def _install_calibration_cache(frame: pd.DataFrame, recalib_bars: int) -> None:
    """Compute the calibration points once for the whole frame and slice them per window.

    The in-tree builder recomputes on every call and one pass over a 15-month window
    costs minutes, while this harness asks for the same points once per fit and once
    per score. Anchoring the grid to the frame rather than to each window's first bar
    also matches the live bot, which recalibrates on its own clock regardless of where
    an analysis window starts.
    """
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


_CAL_CACHE: dict = {}


def _calibration_at(frame: pd.DataFrame, cutoff: str) -> dict:
    """Structural events and ATR percentiles from the start of history up to ``cutoff``."""
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


# --- fit and score ----------------------------------------------------------


def fit(frame: pd.DataFrame, last_bar: int, seed: int, n_trials: int, train_split: float) -> dict | None:
    """Search the best config on [0, last_bar], calibrated with that window's past only."""
    end = _dtime(frame, last_bar)
    req = OptimizerRequest(
        pair=PAIR,
        mode="OPTIMIZE",
        fee_pct=FEE,
        start=_dtime(frame, 0),
        end=end,
        train_split=train_split,
        n_trials=n_trials,
        seed=seed,
        search_space=SPACE,
    )
    try:
        return run_optimize(req, _calibration_at(frame, end)).top_candidates[0]
    except ValueError:
        return None


def score(frame: pd.DataFrame, cand: dict, first_bar: int, last_bar: int, decided_at: str) -> tuple[float | None, int]:
    """Score the config on ONE continuous run over [first_bar, last_bar].

    The run ends mid-position by construction, so it is marked to market at the final
    price: a leg is booked only when it closes, and dropping the open one would take
    the whole move since the last operation off the bot's side of the comparison.
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
    # The base calibration is frozen at the decision instant; the schedule carries the
    # recalibrations that happen after it, each computed from its own past only.
    ctx = _build_eval_context(req, _calibration_at(frame, decided_at))
    cfg = optimizer._build_engine_config(
        PAIR,
        Candidate(k_act=cand.get("k_act"), min_margin=cand.get("min_margin"), stop_pcts=cand.get("stop_pcts")),
        ctx.atr_ratio_thresholds,
        ctx.up_k,
        ctx.down_k,
        ATR_DESV_LIMIT,
        ctx.calibration_points,
    )
    ops = simulate_operations(ctx.df, cfg, fee_rate=FEE / 100.0)
    if not ops:
        return None, 0
    marked = mark_to_market(ops, float(ctx.df.iloc[-1]["close"]))
    closed = sum(1 for op in ops if op.pnl_abs is not None and op.idx != 1)
    return round(marked, 2), closed


def hold_return(frame: pd.DataFrame, first_bar: int, last_bar: int) -> float:
    opened = float(frame.iloc[first_bar]["close"])
    closed = float(frame.iloc[last_bar]["close"])
    return round((closed / opened - 1.0) * 100.0, 2)


def _signature(cand: dict) -> str:
    branch = f"k_act={cand['k_act']:.1f}" if cand.get("k_act") is not None else f"mm={cand.get('min_margin'):.3f}"
    stops = "/".join(f"{cand['stop_pcts'][lvl]:.1f}" for lvl in LEVELS)
    return f"{branch} s={stops}"


# --- experiment -------------------------------------------------------------


def run(frame: pd.DataFrame, fit_days: list[int], seeds: list[int], n_trials: int, train_split: float) -> dict:
    results: dict = {}
    last_bar = len(frame) - 1

    for n_days in fit_days:
        fit_bars = n_days * BARS_PER_DAY
        if fit_bars >= len(frame):
            print(f"\n[{n_days}d] no cabe en el marco, se omite")
            continue

        decided_at = _dtime(frame, fit_bars - 1)
        hold = hold_return(frame, fit_bars, last_bar)
        forward_days = (last_bar - fit_bars) / BARS_PER_DAY
        print(
            f"\n[ajuste {n_days}d] decide en {decided_at[:10]}  "
            f"evalua {_stamp(frame, fit_bars)[:10]}..{_stamp(frame, last_bar)[:10]} "
            f"({forward_days:.0f}d)  mantener={hold:+.2f}%"
        )

        for seed in seeds:
            t0 = time.perf_counter()
            cand = fit(frame, fit_bars - 1, seed, n_trials, train_split)
            if cand is None:
                results[(n_days, seed)] = (None, 0, "-", hold)
                print(f"  seed={seed:<4} sin candidato valido")
                continue
            pnl, ops = score(frame, cand, fit_bars, last_bar, decided_at)
            results[(n_days, seed)] = (pnl, ops, _signature(cand), hold)
            delta = "-" if pnl is None else f"{pnl - hold:+.1f}"
            print(
                f"  seed={seed:<4} FUERA={pnl!s:>8}%  vs hold={delta:>7}  ops={ops:<4} "
                f"{_signature(cand):<34} ({time.perf_counter() - t0:.0f}s)",
                flush=True,
            )

    return results


def summarize(results: dict, fit_days: list[int], seeds: list[int]) -> None:
    print("\n\n" + "=" * 92)
    print("RESUMEN — ajuste en los primeros N dias, puntuado en UNA corrida continua sobre el resto")
    print("=" * 92)

    print(f"\n{'ajuste':<9}{'mediana':>10}{'peor':>9}{'mejor':>9}{'mantener':>11}{'vs hold':>10}{'gana':>7}  configs")
    print("-" * 100)
    for n_days in fit_days:
        rows = [results.get((n_days, s)) for s in seeds]
        rows = [r for r in rows if r is not None and r[0] is not None]
        if not rows:
            print(f"{f'{n_days}d':<9}{'sin resultados':>10}")
            continue
        pnls = [r[0] for r in rows]
        hold = rows[0][3]
        median = statistics.median(pnls)
        wins = sum(1 for p in pnls if p > hold)
        uniq = sorted({r[2] for r in rows})
        print(
            f"{f'{n_days}d':<9}{median:>9.2f}%{min(pnls):>8.2f}%{max(pnls):>8.2f}%"
            f"{hold:>10.2f}%{median - hold:>+9.1f}{f'{wins}/{len(rows)}':>7}  {uniq[0]}"
        )
        for extra in uniq[1:]:
            print(f"{'':<55}{extra}")

    print(
        "\nLa unica cifra que decide es 'vs hold': el valor en euros de la cartera contra "
        "comprar y mantener\nsobre el mismo tramo, fuera de muestra. 'gana' cuenta cuantas "
        "semillas lo superan."
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Tarea 1: ajuste en N dias, evaluacion continua sobre el resto.")
    ap.add_argument("files", nargs="+", help="ficheros OHLCVT de Kraken (sin cabecera)")
    ap.add_argument("--fit-days", type=int, nargs="+", default=[60, 120, 180, 240])
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    ap.add_argument("--trials", type=int, default=120, help="trials por rama (k_act y min_margin)")
    ap.add_argument("--train-split", type=float, default=0.67, help="corte interno del optimizador (1.0 lo desactiva)")
    ap.add_argument("--recalib-bars", type=int, default=RECALIBRATION_BARS)
    args = ap.parse_args()

    print(f"[datos] {len(args.files)} ficheros")
    frame = build_frame(args.files)

    print(f"\n[calibracion] construyendo el calendario cada {args.recalib_bars} velas (esto tarda minutos)")
    _install_ohlc(frame)
    _force_sequential_branches()
    _install_calibration_cache(frame, args.recalib_bars)

    print(
        f"\n[busqueda] {args.trials} trials por rama, semillas {args.seeds}, "
        f"corte interno train_split={args.train_split}, comision {FEE}% por pata"
    )
    results = run(frame, args.fit_days, args.seeds, args.trials, args.train_split)
    summarize(results, args.fit_days, args.seeds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
