"""Screen the Trend/Chop regime filter before building it into the strategy.

Read-only, and it changes no strategy code: the filter is applied as an overlay on
whole windows, not inside the engine. That is deliberate. The question here is
whether the signal exists at all, and a coarse overlay answers it for a fraction of
the cost of putting a classifier into ``trading/engine.py``. If the signal is not
visible at this granularity, a finer implementation will not rescue it.

The classifier is the Choppiness Index the backlog card names:

    CI = 100 * log10(sum(TR, n) / (max(high, n) - min(low, n))) / log10(n)

High CI means the bar range was spent going nowhere (chop); low CI means the market
covered ground (trend). Every window is classified from the ``--ci-bars`` bars that
end *before* it opens, so no decision sees its own outcome.

One config is fitted once on the first ``--fit-days`` days and then held fixed, so
the only thing that varies across windows is the market. Windows slide by one day,
which makes them overlap: good for measuring the CI/return relationship, wrong for
compounding, so the chained figures use disjoint windows only.

Usage (PYTHONPATH=. and DB env vars required):
  PYTHONPATH=. python scripts/analysis/regime_filter_screen.py XBTEUR
"""

import argparse
import statistics
import time
from dataclasses import dataclass

import numpy as np

import scripts.analysis.refit_frequency_experiment as harness
import trading.optimizer.search as optimizer
from core.config import PAIRS

BARS_PER_DAY = harness.BARS_PER_DAY


@dataclass
class Window:
    first_bar: int
    last_bar: int
    ci: float
    bot: float
    hold: float

    @property
    def edge(self) -> float:
        """What the bot added over holding the same window."""
        return self.bot - self.hold


def choppiness_index(df, n: int) -> np.ndarray:
    """Choppiness Index per bar, using the ``n`` bars that end at that bar.

    Returns NaN until ``n`` bars are available. 100 means every bar of range was
    retraced; 0 means the range was covered in one direction.
    """
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)

    prev_close = np.concatenate(([close[0]], close[:-1]))
    true_range = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))

    out = np.full(len(df), np.nan)
    tr_sum = np.cumsum(true_range)
    for i in range(n - 1, len(df)):
        window_tr = tr_sum[i] - (tr_sum[i - n] if i >= n else 0.0)
        span = high[i - n + 1 : i + 1].max() - low[i - n + 1 : i + 1].min()
        if span > 0 and window_tr > 0:
            out[i] = 100.0 * np.log10(window_tr / span) / np.log10(n)
    return out


def fit_config(pair: str, fit_bars: int, seed: int, n_trials: int) -> dict:
    """Fit one config on the opening window, calibrated with the past only."""
    return harness._fit(pair, 0, fit_bars - 1, seed, n_trials)


def score_window(pair: str, cand: dict, first_bar: int, last_bar: int, decided_at: str) -> float | None:
    """Run the fixed config over one window it never saw, valuing the position left open."""
    pnl, _ops = harness._score(pair, cand, first_bar, last_bar, decided_at)
    return pnl


def collect(pair: str, cand: dict, fit_bars: int, hold_bars: int, ci: np.ndarray, slide: int) -> list[Window]:
    """Score the fixed config on every window that starts after the fit window."""
    n = len(harness._full_frame(pair))
    windows: list[Window] = []
    first = fit_bars
    while first + hold_bars <= n:
        last = first + hold_bars - 1
        value = ci[first - 1]  # the classifier only ever sees bars before the window
        bot = score_window(pair, cand, first, last, harness._dtime(pair, first))
        if bot is not None and not np.isnan(value):
            windows.append(Window(first, last, float(value), bot, harness._hold_return(pair, first, last)))
        first += slide
    return windows


def _spearman(xs: list[float], ys: list[float]) -> float:
    """Rank correlation, so a monotone but curved relationship still shows."""

    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for pos, i in enumerate(order):
            r[i] = float(pos)
        return r

    rx, ry = ranks(xs), ranks(ys)
    return statistics.correlation(rx, ry) if len(xs) > 2 else float("nan")


def report_relationship(windows: list[Window]) -> None:
    print("\n" + "=" * 78)
    print(f"RELACION entre el indice de chop y la ventaja del bot ({len(windows)} ventanas solapadas)")
    print("=" * 78)

    cis = [w.ci for w in windows]
    print(f"\nindice de chop: min={min(cis):.1f}  mediana={statistics.median(cis):.1f}  max={max(cis):.1f}")
    print(f"correlacion de rangos (chop vs bot-mantener): {_spearman(cis, [w.edge for w in windows]):+.3f}")

    order = sorted(windows, key=lambda w: w.ci)
    size = max(1, len(order) // 5)
    print(f"\n{'quintil de chop':<18}{'chop':>10}{'bot':>10}{'mantener':>11}{'bot-mant':>11}{'bot gana':>10}")
    print("-" * 70)
    for q in range(5):
        chunk = order[q * size : (q + 1) * size] if q < 4 else order[4 * size :]
        if not chunk:
            continue
        wins = sum(1 for w in chunk if w.edge > 0)
        print(
            f"{f'Q{q + 1} ' + ('mas tendencia' if q == 0 else 'mas chop' if q == 4 else ''):<18}"
            f"{statistics.mean(w.ci for w in chunk):>10.1f}"
            f"{statistics.mean(w.bot for w in chunk):>10.2f}"
            f"{statistics.mean(w.hold for w in chunk):>11.2f}"
            f"{statistics.mean(w.edge for w in chunk):>11.2f}"
            f"{f'{wins}/{len(chunk)}':>10}"
        )


def _chain(returns: list[float]) -> float:
    factor = 1.0
    for r in returns:
        factor *= 1.0 + (r / 100.0)
    return (factor - 1.0) * 100.0


def report_filter(windows: list[Window], hold_bars: int) -> None:
    """Chain disjoint windows under each filter rule, sweeping the threshold."""
    disjoint = []
    next_free = -1
    for w in sorted(windows, key=lambda w: w.first_bar):
        if w.first_bar > next_free:
            disjoint.append(w)
            next_free = w.last_bar
    if len(disjoint) < 3:
        print("\nDemasiadas pocas ventanas disjuntas para encadenar.")
        return

    always_bot = _chain([w.bot for w in disjoint])
    always_hold = _chain([w.hold for w in disjoint])

    print("\n\n" + "=" * 78)
    print(f"FILTRO aplicado a {len(disjoint)} ventanas disjuntas de {hold_bars // BARS_PER_DAY} dias")
    print("=" * 78)
    print(f"\nsiempre el bot   {always_bot:+8.2f}%")
    print(f"siempre mantener {always_hold:+8.2f}%")

    cis = sorted(w.ci for w in disjoint)
    thresholds = sorted({round(statistics.quantiles(cis, n=10)[i], 1) for i in range(9)})

    print(f"\n{'umbral':>8}{'opera si chop>umbral':>24}{'opera si chop<umbral':>24}{'ventanas operadas':>20}")
    print("-" * 76)
    for t in thresholds:
        chop_side = [w.bot if w.ci > t else w.hold for w in disjoint]
        trend_side = [w.bot if w.ci < t else w.hold for w in disjoint]
        traded = sum(1 for w in disjoint if w.ci > t)
        print(f"{t:>8.1f}{_chain(chop_side):>23.2f}%{_chain(trend_side):>23.2f}%{f'{traded}/{len(disjoint)}':>20}")
    print("\n'opera si chop>umbral' = el bot solo cuando el mercado esta lateral, mantener el resto.")
    print("'opera si chop<umbral' = lo contrario, que es lo que propone la ficha del backlog.")
    print("Barrer el umbral sobre estos mismos datos es dentro de muestra: lee la forma, no el maximo.")

    print(f"\n\nVentana a ventana:\n\n{'periodo':<24}{'chop':>8}{'bot':>10}{'mantener':>11}{'bot-mant':>11}")
    print("-" * 64)
    for w in disjoint:
        print(f"{_range(w):<24}{w.ci:>8.1f}{w.bot:>10.2f}{w.hold:>11.2f}{w.edge:>11.2f}")


_PAIR = ""


def _range(w: Window) -> str:
    return f"{harness._dtime(_PAIR, w.first_bar)[:10]}..{harness._dtime(_PAIR, w.last_bar)[:10]}"


def main() -> None:
    global _PAIR
    ap = argparse.ArgumentParser(description="Screen a Choppiness-Index regime filter against buy-and-hold.")
    ap.add_argument("pairs", nargs="*")
    ap.add_argument("--fit-days", type=int, default=30, help="Dias del ajuste unico que fija la configuracion.")
    ap.add_argument("--hold-days", type=int, default=7, help="Duracion de cada ventana evaluada.")
    ap.add_argument("--ci-days", type=int, default=7, help="Dias que mira el indice de chop, siempre pasados.")
    ap.add_argument("--slide-days", type=int, default=1, help="Cada cuantos dias empieza una ventana nueva.")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--n-trials", type=int, default=300)
    ap.add_argument("--recalib-bars", type=int, default=harness.RECALIB_BARS)
    ap.add_argument("--fee", type=float, default=harness.FEE, help="Comision por operacion, en porcentaje.")
    ap.add_argument("--from-date", type=str, default=None, help="Primera fecha de la serie continua a usar.")
    ap.add_argument("--to-date", type=str, default=None, help="Ultima fecha de la serie continua a usar.")
    args = ap.parse_args()

    harness.FEE = args.fee
    harness.set_bounds(args.from_date, args.to_date)

    harness._install_shared_ohlc_cache()
    harness._force_sequential_branches()
    optimizer.build_calibration_inputs = harness._cached_calibration_inputs
    optimizer._suggest_stops = harness._shared_suggest_stops
    optimizer._candidate_from_params = harness._shared_candidate_from_params

    fit_bars = args.fit_days * BARS_PER_DAY
    hold_bars = args.hold_days * BARS_PER_DAY
    ci_bars = args.ci_days * BARS_PER_DAY

    t0 = time.perf_counter()
    for pair in args.pairs or [p for p in PAIRS if p]:
        _PAIR = pair
        df = harness._full_frame(pair)
        print(
            f"\n=== {pair}  {len(df)} velas  {harness._dtime(pair, 0)[:10]}..{harness._dtime(pair, len(df) - 1)[:10]}"
        )
        harness._set_points(harness._global_cal_points(pair, args.recalib_bars), pair)

        cand = fit_config(pair, fit_bars, args.seed, args.n_trials)
        if cand is None:
            print("  sin candidato valido en la ventana de ajuste")
            continue
        print(f"[config fija] {harness._signature(cand)}  ajustada sobre los primeros {args.fit_days} dias")

        ci = choppiness_index(df, ci_bars)
        windows = collect(pair, cand, fit_bars, hold_bars, ci, args.slide_days * BARS_PER_DAY)
        if not windows:
            print("  sin ventanas evaluables")
            continue
        report_relationship(windows)
        report_filter(windows, hold_bars)

    print(f"\ntotal {time.perf_counter() - t0:.0f}s")


if __name__ == "__main__":
    main()
