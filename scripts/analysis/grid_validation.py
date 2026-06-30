"""Validation harness for the fixed optimizer search grids.

Runs the optimizer over the spec's fixed grids and reports diagnostics. Read-only;
nothing is written. See docs/specs/optimizer-grid-derivation-design.md.

Modes:
  opt          edge-pinning + coarseness sensitivity (spec tests 1, 4): OPTIMIZE per
               pair/grid/seed; reports the winner, edge flags, robust/train/test/ops.
  coverage     candidate coverage (test 2): fraction of random grid candidates that
               produce >=1 train and >=1 test op (band-scaling sanity).
  auto         AUTO convergence (test 3) under the current exact-match criterion.
  walkforward  out-of-sample (the decisive test): split each pair's history into N
               equal time windows; optimize on window i and evaluate the winner on the
               disjoint window i+1. Window bounds are computed from the data, so this
               works unchanged as more history accumulates (use --windows 3+ then).

Usage (PYTHONPATH=. and DB env vars required):
  PYTHONPATH=. python scripts/analysis/grid_validation.py walkforward XBTEUR ETHEUR --windows 3
  PYTHONPATH=. python scripts/analysis/grid_validation.py opt XBTEUR
  PYTHONPATH=. python scripts/analysis/grid_validation.py coverage
  PYTHONPATH=. python scripts/analysis/grid_validation.py auto XBTEUR
"""

import argparse
import random

import core.database as db
from core.config import CANDLE_TIMEFRAME, PAIRS
from core.config import VOLATILITY_LEVELS as LEVELS
from trading.optimizer.search import (
    AutoSettings,
    Candidate,
    GridSpec,
    OptimizerRequest,
    SearchSpace,
    _build_eval_context,
    _evaluate,
    run_auto_optimize,
    run_optimize,
)

FEE = 0.4
SPLIT = 0.67
KACT = GridSpec(0.0, 6.0, 0.5)
MM = GridSpec(0.0, 0.010, 0.002)
STOP_HI = 0.9
STOP_LO = 0.5  # the spec floor; the band is [STOP_LO, STOP_HI]


def space(stop_lo: float = STOP_LO, stop_step: float = 0.1) -> SearchSpace:
    return SearchSpace(stop_pcts=GridSpec(stop_lo, STOP_HI, stop_step), k_act=KACT, min_margin=MM)


def grid_vals(g: GridSpec) -> list[float]:
    n = round((g.end - g.start) / g.step)
    return [round(g.start + i * g.step, 5) for i in range(n + 1)]


def _at(v, *edges) -> bool:
    return v is not None and any(abs(v - e) < 1e-9 for e in edges)


def edge_flags(cand: dict, stop_lo: float) -> list[str]:
    flags = []
    for lvl, v in (cand.get("stop_pcts") or {}).items():
        if _at(v, stop_lo, STOP_HI):
            flags.append(f"stop_{lvl}={v}")
    if _at(cand.get("k_act"), KACT.start, KACT.end):
        flags.append(f"k_act={cand['k_act']}")
    if _at(cand.get("min_margin"), MM.start, MM.end):
        flags.append(f"min_margin={cand['min_margin']}")
    return flags


def _branch(cand: dict) -> str:
    return "k_act" if cand.get("k_act") is not None else "min_margin"


def _cand_from_dict(cand: dict) -> Candidate:
    return Candidate(k_act=cand.get("k_act"), min_margin=cand.get("min_margin"), stop_pcts=cand.get("stop_pcts"))


# --- tests 1 + 4: OPTIMIZE per pair/grid/seed ------------------------------


def run_opt(pairs, steps, seeds, n_trials):
    print(f"[opt] n_trials={n_trials} fee={FEE} split={SPLIT} seeds={seeds}")
    for pair in pairs:
        for step in steps:
            n_vals = len(grid_vals(GridSpec(STOP_LO, STOP_HI, step)))
            print(f"\n=== {pair}  stop_step={step}  ({n_vals}^5 stop combos) ===")
            for seed in seeds:
                req = OptimizerRequest(
                    pair=pair,
                    mode="OPTIMIZE",
                    fee_pct=FEE,
                    train_split=SPLIT,
                    n_trials=n_trials,
                    seed=seed,
                    search_space=space(stop_step=step),
                )
                best = run_optimize(req, None).top_candidates[0]
                print(
                    f"  seed={seed:>4} branch={_branch(best):<10} robust={best.get('robust_pnl_pct')} "
                    f"train={best.get('train_pnl_pct')} test={best.get('test_pnl_pct')} "
                    f"ops={best.get('train_ops')}/{best.get('test_ops')}"
                )
                print(f"            edge-pinned: {edge_flags(best, STOP_LO) or 'none'}")
                print(
                    f"            stop_pcts={best.get('stop_pcts')} k_act={best.get('k_act')} min_margin={best.get('min_margin')}"
                )


# --- test 2: candidate coverage --------------------------------------------


def run_coverage(pairs, n_samples):
    print(f"[coverage] n_samples={n_samples} per pair (valid = >=1 train op AND >=1 test op)")
    sv, kv, mv = grid_vals(GridSpec(STOP_LO, STOP_HI, 0.1)), grid_vals(KACT), grid_vals(MM)
    for pair in pairs:
        req = OptimizerRequest(
            pair=pair, mode="OPTIMIZE", fee_pct=FEE, train_split=SPLIT, n_trials=1, seed=0, search_space=space()
        )
        ctx = _build_eval_context(req, None)
        rng = random.Random(0)
        valid = both0 = 0
        for _ in range(n_samples):
            stops = {lvl: rng.choice(sv) for lvl in LEVELS}
            if rng.random() < 0.5:
                cand = Candidate(k_act=rng.choice(kv), min_margin=None, stop_pcts=stops)
            else:
                cand = Candidate(k_act=None, min_margin=rng.choice(mv), stop_pcts=stops)
            ev = _evaluate(cand, ctx)
            if ev.train_samples >= 1 and ev.test_samples >= 1:
                valid += 1
            if ev.train_samples == 0 and ev.test_samples == 0:
                both0 += 1
        print(f"  {pair}: valid={valid}/{n_samples} ({100 * valid / n_samples:.0f}%)  zero-op={both0}")


# --- test 3: AUTO convergence ----------------------------------------------


def run_auto(pairs, n_trials, trial_step, max_trials):
    auto = AutoSettings(n_seeds=4, min_agree=3, trial_step=trial_step, max_trials=max_trials)
    print(f"[auto] n_seeds=4 min_agree=3 start={n_trials} step={trial_step} max={max_trials} (exact-match signature)")
    for pair in pairs:
        req = OptimizerRequest(
            pair=pair,
            mode="AUTO",
            fee_pct=FEE,
            train_split=SPLIT,
            n_trials=n_trials,
            seed=42,
            search_space=space(),
            auto_settings=auto,
        )
        res = run_auto_optimize(req, None)
        best = res.top_candidates[0]
        print(
            f"  {pair}: converged={res.converged} agreed={res.n_seeds_agreed}/4 "
            f"trials_run={res.n_trials_run} best_robust={best.get('robust_pnl_pct')}"
        )


# --- walk-forward (out-of-sample) ------------------------------------------


def _window_bounds(pair, timeframe, n_windows):
    """N+1 dtime boundaries splitting the pair's history into N equal-row windows."""
    df = db.load_ohlc_data(pair, timeframe).dropna(subset=["atr"]).sort_values("time").reset_index(drop=True)
    n = len(df)
    if n == 0:
        return []
    return [str(df.iloc[min(int(n * i / n_windows), n - 1)]["dtime"]) for i in range(n_windows + 1)]


def run_walkforward(pairs, n_windows, seeds, n_trials):
    print(
        f"[walkforward] windows={n_windows} fee={FEE} seeds={seeds} n_trials={n_trials} (train_split=1.0; opt Wi, eval Wi+1)"
    )
    for pair in pairs:
        bounds = _window_bounds(pair, CANDLE_TIMEFRAME, n_windows)
        if not bounds:
            print(f"\n=== {pair}: no data ===")
            continue
        print(f"\n=== {pair}  bounds={bounds} ===")
        for i in range(n_windows - 1):
            w_opt, w_oos = (bounds[i], bounds[i + 1]), (bounds[i + 1], bounds[i + 2])
            oos_req = OptimizerRequest(
                pair=pair,
                mode="OPTIMIZE",
                fee_pct=FEE,
                start=w_oos[0],
                end=w_oos[1],
                train_split=1.0,
                n_trials=1,
                seed=0,
                search_space=space(),
            )
            oos_ctx = _build_eval_context(oos_req, None)
            print(f"  W{i} [{w_opt[0]}..{w_opt[1]}] -> W{i + 1} [{w_oos[0]}..{w_oos[1]}]")
            for seed in seeds:
                req = OptimizerRequest(
                    pair=pair,
                    mode="OPTIMIZE",
                    fee_pct=FEE,
                    start=w_opt[0],
                    end=w_opt[1],
                    train_split=1.0,
                    n_trials=n_trials,
                    seed=seed,
                    search_space=space(),
                )
                best = run_optimize(req, None).top_candidates[0]
                ev = _evaluate(_cand_from_dict(best), oos_ctx)
                print(
                    f"    seed={seed:>4} {_branch(best):<10} W{i}_in={best.get('robust_pnl_pct')} -> "
                    f"W{i + 1}_oos={round(ev.robust_pnl, 2)}  (ops {best.get('train_ops')} -> {ev.train_samples})"
                )


def main() -> None:
    ap = argparse.ArgumentParser(description="Validation harness for the fixed optimizer grids.")
    ap.add_argument("mode", choices=["opt", "coverage", "auto", "walkforward"])
    ap.add_argument("pairs", nargs="*", help="Pairs (default: PAIRS from config).")
    ap.add_argument("--seeds", type=str, default="42,7,99")
    ap.add_argument("--n-trials", type=int, default=300)
    ap.add_argument(
        "--windows", type=int, default=2, help="walkforward: number of disjoint windows (use 3+ with more history)."
    )
    args = ap.parse_args()

    pairs = args.pairs or [p for p in PAIRS if p]
    if not pairs:
        ap.error("no pairs given and PAIRS is empty in config")
    seeds = [int(s) for s in args.seeds.split(",")]

    if args.mode == "opt":
        run_opt(pairs, [0.1], seeds, args.n_trials)
    elif args.mode == "coverage":
        run_coverage(pairs, n_samples=1500)
    elif args.mode == "auto":
        run_auto(pairs, n_trials=400, trial_step=400, max_trials=1200)
    elif args.mode == "walkforward":
        run_walkforward(pairs, args.windows, seeds, args.n_trials)


if __name__ == "__main__":
    main()
