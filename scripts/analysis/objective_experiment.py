"""Walk-forward experiment for the optimizer method review (points 1 and 2).

Read-only. Nothing in ``trading/optimizer/`` is modified: the two variants under
test are installed as monkeypatches for the duration of a run, so the production
search code stays exactly as it is on disk.

Two factors, crossed (2x2):

  objective   ``split``  the current inner 67/33 cut, objective min(train, test)
              ``window`` no inner cut, objective the fit window's own PnL
  stops       ``5free``  one stop_pct per volatility level (today's SearchSpace)
              ``1shared`` a single stop_pct shared by all five levels

Every arm is judged the same way, and only that way: fit on window Wi, then score
the winning candidate on the disjoint window Wi+1. The in-sample number is printed
for contrast but never decides anything.

Usage (PYTHONPATH=. and DB env vars required):
  PYTHONPATH=. python scripts/analysis/objective_experiment.py XBTEUR ETHEUR --windows 3
"""

import argparse
import statistics
import time
from dataclasses import dataclass

import core.database as db
import trading.optimizer.search as optimizer
from core.config import CANDLE_TIMEFRAME, PAIRS, PARAM_SESSIONS, SLEEPING_INTERVAL
from core.config import VOLATILITY_LEVELS as LEVELS
from trading.market_analyzer import analyze_structural_noise, atr_ratio_percentiles
from trading.optimizer.search import (
    Candidate,
    GridSpec,
    OptimizerRequest,
    SearchSpace,
    _build_eval_context,
    _evaluate,
    run_optimize,
)

# The live bot recalibrates every PARAM_SESSIONS ticks; in bars of the working
# frame that is how often the simulation must move its calibration to match.
RECALIB_BARS = max(1, (PARAM_SESSIONS * SLEEPING_INTERVAL) // (CANDLE_TIMEFRAME * 60))

FEE = 0.4
SPLIT = 0.67
KACT = GridSpec(0.0, 6.0, 0.5)
MM = GridSpec(0.0, 0.010, 0.002)
STOPS = GridSpec(0.5, 0.9, 0.1)

SPACE = SearchSpace(stop_pcts=STOPS, k_act=KACT, min_margin=MM)


# --- patches installed for the whole run -----------------------------------


def _install_shared_ohlc_cache() -> None:
    """Memoize load_ohlc_data by (pair, timeframe).

    _build_eval_context reloads the full history on every fit, and one load costs
    ~10 s against the restored dump — far more than the search itself.
    """
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
    """Keep both optimizer branches in-process.

    The branch pool spawns workers that re-import the module, so a monkeypatched
    _suggest_stops would silently not apply inside them.
    """
    optimizer._PARALLEL_MIN_TRIALS = 10**9


def _install_calibration_cache() -> None:
    """Memoize the calibration schedule per window.

    Every arm and seed of a transition re-fits the same window, and the in-tree
    builder recomputes the points on each call — one rebuild costs minutes over a
    long window, so the fit window alone would be rebuilt once per arm per seed.
    """
    real = optimizer.build_calibration_inputs
    cache: dict[tuple, tuple] = {}

    def cached(df_full, df, recalib_bars):
        if df.empty:
            return real(df_full, df, recalib_bars)
        key = (str(df.iloc[0]["dtime"]), str(df.iloc[-1]["dtime"]), len(df), recalib_bars)
        if key not in cache:
            cache[key] = real(df_full, df, recalib_bars)
        return cache[key]

    optimizer.build_calibration_inputs = cached


_REAL_SUGGEST_STOPS = optimizer._suggest_stops
_REAL_CANDIDATE_FROM_PARAMS = optimizer._candidate_from_params
_ANCHOR = LEVELS[0]


def _shared_suggest_stops(trial, grid: GridSpec) -> dict[str, float]:
    """Suggest one stop_pct and apply it to every level (the '1shared' variant)."""
    value = trial.suggest_float(f"stop_pct_{_ANCHOR}", grid.start, grid.end, step=grid.step)
    return dict.fromkeys(LEVELS, value)


def _shared_candidate_from_params(params: dict) -> Candidate:
    """Rebuild a candidate when only the anchor level was registered as a param."""
    anchor = params[f"stop_pct_{_ANCHOR}"]
    stop_pcts = {lvl: params.get(f"stop_pct_{lvl}", anchor) for lvl in LEVELS}
    if "k_act" in params:
        return Candidate(k_act=params["k_act"], min_margin=None, stop_pcts=stop_pcts)
    return Candidate(k_act=None, min_margin=params.get("min_margin", 0.0), stop_pcts=stop_pcts)


def _set_stops_mode(shared: bool) -> None:
    optimizer._suggest_stops = _shared_suggest_stops if shared else _REAL_SUGGEST_STOPS
    optimizer._candidate_from_params = _shared_candidate_from_params if shared else _REAL_CANDIDATE_FROM_PARAMS


# --- arms -------------------------------------------------------------------


@dataclass(frozen=True)
class Arm:
    name: str
    train_split: float  # 0.67 keeps the inner cut; 1.0 removes it
    shared: bool


ARMS = (
    Arm("split  / 5free  (hoy)", SPLIT, False),
    Arm("split  / 1shared", SPLIT, True),
    Arm("window / 5free", 1.0, False),
    Arm("window / 1shared", 1.0, True),
)


@dataclass
class Row:
    pair: str
    transition: str
    arm: str
    seed: int
    in_sample: float | None
    oos: float | None
    fit_ops: int | None
    oos_ops: int


# --- experiment -------------------------------------------------------------


def _window_bounds(pair: str, timeframe: int, n_windows: int) -> list[str]:
    df = db.load_ohlc_data(pair, timeframe).dropna(subset=["atr"]).sort_values("time").reset_index(drop=True)
    n = len(df)
    if n == 0:
        return []
    return [str(df.iloc[min(int(n * i / n_windows), n - 1)]["dtime"]) for i in range(n_windows + 1)]


def _cand_from_dict(cand: dict) -> Candidate:
    return Candidate(k_act=cand.get("k_act"), min_margin=cand.get("min_margin"), stop_pcts=cand.get("stop_pcts"))


def _frozen_calibration(pair: str, boundary: str) -> dict:
    """Calibration from the start of history up to ``boundary``, and no further.

    Passed explicitly to both contexts of a transition so the test window is never
    calibrated with its own data. Left to _build_eval_context (calibration=None),
    the cut would be the *request's* end, which for the test window includes it —
    a look-ahead, since production at any instant inside that window only knows
    the past. The boundary is also the fit window's end, so freezing it here is
    exactly "calibrate once, at the moment the config is chosen".
    """
    df = db.load_ohlc_data(pair, CANDLE_TIMEFRAME).dropna(subset=["atr"]).sort_values("time").reset_index(drop=True)
    cal_df = df[df["dtime"] <= boundary].reset_index(drop=True)
    up_events, down_events = analyze_structural_noise(cal_df)
    p20, p50, p80, p95 = atr_ratio_percentiles(cal_df)
    return {
        "up_events": up_events,
        "down_events": down_events,
        "atr_ratio_p20": p20,
        "atr_ratio_p50": p50,
        "atr_ratio_p80": p80,
        "atr_ratio_p95": p95,
    }


def _oos_context(pair: str, start: str, end: str, calibration: dict | None, recalib_bars: int):
    """Evaluation context for a window: no inner cut, so the score is its own PnL."""
    req = OptimizerRequest(
        pair=pair,
        mode="OPTIMIZE",
        fee_pct=FEE,
        start=start,
        end=end,
        train_split=1.0,
        n_trials=1,
        seed=0,
        search_space=SPACE,
        recalibration_bars=recalib_bars,
    )
    return _build_eval_context(req, calibration)


def _fit(
    pair: str,
    start: str,
    end: str,
    arm: Arm,
    seed: int,
    n_trials: int,
    calibration: dict | None,
    recalib_bars: int,
) -> dict | None:
    req = OptimizerRequest(
        pair=pair,
        mode="OPTIMIZE",
        fee_pct=FEE,
        start=start,
        end=end,
        train_split=arm.train_split,
        n_trials=n_trials,
        seed=seed,
        search_space=SPACE,
        recalibration_bars=recalib_bars,
    )
    try:
        return run_optimize(req, calibration).top_candidates[0]
    except ValueError:
        return None


def run(pairs, n_windows: int, seeds, n_trials: int, calib_mode: str, recalib_bars: int) -> list[Row]:
    rows: list[Row] = []
    progressive = calib_mode == "progressive"
    for pair in pairs:
        bounds = _window_bounds(pair, CANDLE_TIMEFRAME, n_windows)
        if not bounds:
            print(f"\n=== {pair}: sin datos ===")
            continue
        print(f"\n=== {pair}  ventanas={n_windows}  limites={[b[:10] for b in bounds]} ===")

        for i in range(n_windows - 1):
            w_fit = (bounds[i], bounds[i + 1])
            w_oos = (bounds[i + 1], bounds[i + 2])
            transition = f"W{i}->W{i + 1}"
            calib = _frozen_calibration(pair, w_fit[1]) if calib_mode == "frozen" else None
            print(
                f"\n  {transition}   ajuste [{w_fit[0][:10]}..{w_fit[1][:10]}]  prueba [{w_oos[0][:10]}..{w_oos[1][:10]}]"
            )

            # The optimizer builds its own schedule now; 0 asks it for a single calibration.
            bars = recalib_bars if progressive else 0
            if progressive:
                print(f"    recalibracion cada {recalib_bars} velas, construida por el optimizador")
            elif calib is not None:
                print(f"    calibracion congelada en {w_fit[1][:10]} ({len(calib['up_events'])} eventos)")

            oos_ctx = _oos_context(pair, w_oos[0], w_oos[1], calib, bars)

            for arm in ARMS:
                _set_stops_mode(arm.shared)
                for seed in seeds:
                    t0 = time.perf_counter()
                    best = _fit(pair, w_fit[0], w_fit[1], arm, seed, n_trials, calib, bars)
                    if best is None:
                        rows.append(Row(pair, transition, arm.name, seed, None, None, None, 0))
                        print(f"    {arm.name:<22} seed={seed:<5} sin candidato valido")
                        continue
                    ev = _evaluate(_cand_from_dict(best), oos_ctx)
                    oos = None if ev.in_sample.total_pnl <= -1e17 else round(ev.in_sample.total_pnl, 2)
                    rows.append(
                        Row(
                            pair,
                            transition,
                            arm.name,
                            seed,
                            best.get("in_sample_pnl_pct"),
                            oos,
                            best.get("train_ops"),
                            ev.in_sample.pnl_samples,
                        )
                    )
                    branch = "k_act" if best.get("k_act") is not None else "min_margin"
                    print(
                        f"    {arm.name:<22} seed={seed:<5} dentro={best.get('in_sample_pnl_pct')!s:>8}"
                        f"  FUERA={oos!s:>8}  ops={best.get('train_ops')}/{ev.in_sample.pnl_samples}"
                        f"  {branch:<10} stops={best.get('stop_pcts')}  ({time.perf_counter() - t0:.0f}s)"
                    )
    _set_stops_mode(False)
    return rows


# --- reporting --------------------------------------------------------------


def _agg(values: list[float]) -> str:
    if not values:
        return "     -"
    return f"{statistics.median(values):>6.2f}"


def summarize(rows: list[Row], pairs) -> None:
    print("\n\n" + "=" * 78)
    print("RESUMEN — PnL fuera de muestra, mediana entre semillas")
    print("=" * 78)

    header = f"{'brazo':<22}" + "".join(f"{p:>12}" for p in pairs) + f"{'TODOS':>12}"
    print("\n" + header)
    print("-" * len(header))
    for arm in ARMS:
        line = f"{arm.name:<22}"
        every: list[float] = []
        for pair in pairs:
            vals = [r.oos for r in rows if r.arm == arm.name and r.pair == pair and r.oos is not None]
            every.extend(vals)
            line += f"{_agg(vals):>12}"
        line += f"{_agg(every):>12}"
        print(line)

    print("\n\nMismo corte, por transicion (mediana entre semillas):")
    transitions = sorted({r.transition for r in rows})
    header = f"{'brazo':<22}" + "".join(f"{p + ' ' + t:>18}" for p in pairs for t in transitions)
    print("\n" + header)
    print("-" * len(header))
    for arm in ARMS:
        line = f"{arm.name:<22}"
        for pair in pairs:
            for t in transitions:
                vals = [
                    r.oos
                    for r in rows
                    if r.arm == arm.name and r.pair == pair and r.transition == t and r.oos is not None
                ]
                line += f"{_agg(vals):>18}"
        print(line)

    print("\n\nContraste dentro / fuera de muestra (mediana global):")
    print(f"\n{'brazo':<22}{'DENTRO':>10}{'FUERA':>10}{'caida':>10}")
    print("-" * 52)
    for arm in ARMS:
        ins = [r.in_sample for r in rows if r.arm == arm.name and r.in_sample is not None]
        out = [r.oos for r in rows if r.arm == arm.name and r.oos is not None]
        if not ins or not out:
            continue
        mi, mo = statistics.median(ins), statistics.median(out)
        print(f"{arm.name:<22}{mi:>10.2f}{mo:>10.2f}{mi - mo:>10.2f}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Walk-forward experiment: inner objective x stop_pct dimensionality.")
    ap.add_argument("pairs", nargs="*", help="Pares (por defecto: PAIRS de config).")
    ap.add_argument("--windows", type=int, default=3)
    ap.add_argument("--seeds", type=str, default="42,7,99")
    ap.add_argument("--n-trials", type=int, default=1200)
    ap.add_argument(
        "--calib",
        choices=["progressive", "frozen", "end"],
        default="progressive",
        help="progressive: recalibrar cada --recalib-bars velas con solo el pasado, como el bot. "
        "frozen: una calibracion fija en el limite entre ajuste y prueba. "
        "end: comportamiento por defecto del optimizador (la prueba se calibra con sus propios datos).",
    )
    ap.add_argument("--recalib-bars", type=int, default=RECALIB_BARS)
    args = ap.parse_args()

    pairs = args.pairs or [p for p in PAIRS if p]
    seeds = [int(s) for s in args.seeds.split(",")]

    _install_shared_ohlc_cache()
    _force_sequential_branches()
    _install_calibration_cache()

    print(
        f"[experimento] pares={pairs} ventanas={args.windows} semillas={seeds} "
        f"n_trials={args.n_trials} fee={FEE} calib={args.calib} recalib_bars={args.recalib_bars}"
    )
    t0 = time.perf_counter()
    rows = run(pairs, args.windows, seeds, args.n_trials, args.calib, args.recalib_bars)
    summarize(rows, pairs)
    print(f"\ntotal {time.perf_counter() - t0:.0f}s")


if __name__ == "__main__":
    main()
