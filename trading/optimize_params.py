import sys
import math
import itertools
import numpy as np
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Ensure sibling packages are importable when running as a script.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import ATR_DESV_LIMIT, PAIRS, TRADING_PARAMS, VOLATILITY_LEVELS as LEVELS, STOP_PERCENTILES
from trading.backtest import simulate_operations
from trading.market_analyzer import analyze_structural_noise, load_data

MODES = ("CONSERVATIVE", "AGGRESSIVE", "CURRENT")
STOP_PCT_CHOICES = (0.20, 0.35, 0.50, 0.65, 0.75, 0.80, 0.90, 0.95)
K_ACT_CHOICES = (0.0, 1.0, 2.0, 3.0)
MIN_MARGIN_CHOICES = (0.000, 0.003, 0.006, 0.009)

SPLIT_METHODS = ("RESET", "CONTINUE", "BOTH")
RANK_MODES = ("ROBUST", "MEAN")

def _parse_args() -> dict:
    args = {
        "pair": None,
        "mode": None,
        "fee_pct": 0.0,
        "start": None,
        "end": None,
        "train_split": 1.0,
        "split_method": "RESET",
        "rank": "ROBUST",
        "min_ops": 0,
        "min_test_ops": 0,
    }

    for arg in sys.argv[1:]:
        if arg.startswith("PAIR="):
            args["pair"] = arg.split("=", 1)[1].upper()
        elif arg.startswith("MODE="):
            args["mode"] = arg.split("=", 1)[1].strip().upper()
        elif arg.startswith("FEE_PCT="):
            args["fee_pct"] = float(arg.split("=", 1)[1])
        elif arg.startswith("START="):
            args["start"] = arg.split("=", 1)[1]
        elif arg.startswith("END="):
            args["end"] = arg.split("=", 1)[1]
        elif arg.startswith("TRAIN_SPLIT="):
            args["train_split"] = float(arg.split("=", 1)[1])
        elif arg.startswith("SPLIT_METHOD="):
            args["split_method"] = arg.split("=", 1)[1].strip().upper()
        elif arg.startswith("RANK="):
            args["rank"] = arg.split("=", 1)[1].strip().upper()
        elif arg.startswith("MIN_OPS="):
            args["min_ops"] = int(arg.split("=", 1)[1])
        elif arg.startswith("MIN_TEST_OPS="):
            args["min_test_ops"] = int(arg.split("=", 1)[1])

    if not args["pair"] or not args["mode"]:
        print(
            "Usage: python .\\trading\\optimize_params.py PAIR=XBTEUR MODE=CONSERVATIVE|AGGRESSIVE|CURRENT "
            "[FEE_PCT=0.00] [START=YYYY-MM-DD] [END=YYYY-MM-DD] "
            "[TRAIN_SPLIT=1.00] [SPLIT_METHOD=RESET|CONTINUE|BOTH] [RANK=ROBUST|MEAN] "
            "[MIN_OPS=0] [MIN_TEST_OPS=0]"
        )
        sys.exit(1)

    if args["mode"] not in MODES:
        raise ValueError(f"MODE must be one of {MODES}, got: {args['mode']}")

    if not (0.5 <= args["train_split"] <= 1.0):
        raise ValueError("TRAIN_SPLIT must be in [0.5, 1.0]")

    if args["split_method"] not in SPLIT_METHODS:
        raise ValueError(f"SPLIT_METHOD must be one of {SPLIT_METHODS}, got: {args['split_method']}")

    if args["rank"] not in RANK_MODES:
        raise ValueError(f"RANK must be one of {RANK_MODES}, got: {args['rank']}")

    if args["rank"] == "MEAN" and float(args["train_split"]) >= 1.0:
        raise ValueError("RANK=MEAN requires TRAIN_SPLIT < 1.0 (e.g. 0.5)")

    return args


def _set_pair_atr_thresholds(pair: str, df) -> None:
    atr = df["atr"].to_numpy(dtype=float)
    PAIRS[pair]["atr_20pct"] = float(np.percentile(atr, 20))
    PAIRS[pair]["atr_50pct"] = float(np.percentile(atr, 50))
    PAIRS[pair]["atr_80pct"] = float(np.percentile(atr, 80))
    PAIRS[pair]["atr_95pct"] = float(np.percentile(atr, 95))


def _quantile_ceiled(values: np.ndarray, pct: float) -> Optional[float]:
    if values.size == 0:
        return None
    q = float(np.quantile(values, pct))
    return math.ceil(q * 10.0) / 10.0


def _k_values_by_level(events: List[dict]) -> Dict[str, np.ndarray]:
    out: Dict[str, List[float]] = {lvl: [] for lvl in LEVELS}
    for e in events:
        vols = e.get("volatility_levels") or {}
        for lvl in LEVELS:
            d = vols.get(lvl)
            if not d:
                continue
            k = d.get("k_value")
            if k is None:
                continue
            out[lvl].append(float(k))
    return {lvl: np.array(vals, dtype=float) for lvl, vals in out.items()}


def _apply_candidate_mode(
    pair: str,
    mode: str,
    k_act: Optional[float],
    min_margin: Optional[float],
    stop_pcts: Dict[str, float],
    up_k: Dict[str, np.ndarray],
    down_k: Dict[str, np.ndarray],
) -> None:
    if mode == "CURRENT":
        raise ValueError("Use _apply_current_config() for MODE=CURRENT")
    if mode == "AGGRESSIVE":
        if k_act is None:
            raise ValueError("AGGRESSIVE mode requires k_act")
        TRADING_PARAMS[pair]["sell"]["K_ACT"] = float(k_act)
        TRADING_PARAMS[pair]["buy"]["K_ACT"] = float(k_act)
        # MIN_MARGIN kept (ignored when K_ACT is set)
    else:
        if min_margin is None:
            raise ValueError("CONSERVATIVE mode requires min_margin")
        # Force K_ACT path off
        TRADING_PARAMS[pair]["sell"]["K_ACT"] = None
        TRADING_PARAMS[pair]["buy"]["K_ACT"] = None
        TRADING_PARAMS[pair]["sell"]["MIN_MARGIN"] = float(min_margin)
        TRADING_PARAMS[pair]["buy"]["MIN_MARGIN"] = float(min_margin)

    # Stops: sell uses uptrend events; buy uses downtrend events
    sell_k_stop = {lvl: _quantile_ceiled(up_k[lvl], stop_pcts[lvl]) for lvl in LEVELS}
    buy_k_stop = {lvl: _quantile_ceiled(down_k[lvl], stop_pcts[lvl]) for lvl in LEVELS}
    TRADING_PARAMS[pair]["sell"]["K_STOP"] = sell_k_stop
    TRADING_PARAMS[pair]["buy"]["K_STOP"] = buy_k_stop


@dataclass(frozen=True)
class Candidate:
    k_act: Optional[float]
    min_margin: Optional[float]
    stop_pcts: Dict[str, float]


def _count_combinations(mode: str) -> int:
    stop_combinations = len(STOP_PCT_CHOICES) ** len(LEVELS)
    if mode == "AGGRESSIVE":
        return stop_combinations * len(K_ACT_CHOICES)
    else:
        return stop_combinations * len(MIN_MARGIN_CHOICES)


def _iter_exhaustive_candidates(mode: str) -> List[Candidate]:
    # Exhaustively enumerates the full discrete grid. This can be large.
    candidates: List[Candidate] = []

    for ll, lv, mv, hv, hh in itertools.product(STOP_PCT_CHOICES, repeat=len(LEVELS)):
        stop_pcts = {"LL": ll, "LV": lv, "MV": mv, "HV": hv, "HH": hh}

        if mode == "AGGRESSIVE":
            for k_act in K_ACT_CHOICES:
                candidates.append(Candidate(k_act=float(k_act), min_margin=None, stop_pcts=stop_pcts))
        else:
            for min_margin in MIN_MARGIN_CHOICES:
                candidates.append(Candidate(k_act=None, min_margin=float(min_margin), stop_pcts=stop_pcts))

    return candidates


def _candidate_from_env(pair: str) -> Candidate:
    raw_k_act = TRADING_PARAMS[pair]["buy"].get("K_ACT")
    k_act: Optional[float]
    try:
        k_act = float(raw_k_act) if raw_k_act is not None and str(raw_k_act).strip() != "" else None
    except Exception:
        k_act = None

    raw_mm = TRADING_PARAMS[pair]["buy"].get("MIN_MARGIN", 0) or 0
    try:
        min_margin = float(raw_mm)
    except Exception:
        min_margin = 0.0

    stop_pcts = {lvl: float(STOP_PERCENTILES[pair][lvl]) for lvl in LEVELS}
    return Candidate(k_act=k_act, min_margin=min_margin, stop_pcts=stop_pcts)


def _apply_current_config(
    pair: str,
    stop_pcts: Dict[str, float],
    up_k: Dict[str, np.ndarray],
    down_k: Dict[str, np.ndarray],
) -> None:
    sell_k_stop = {lvl: _quantile_ceiled(up_k[lvl], stop_pcts[lvl]) for lvl in LEVELS}
    buy_k_stop = {lvl: _quantile_ceiled(down_k[lvl], stop_pcts[lvl]) for lvl in LEVELS}
    TRADING_PARAMS[pair]["sell"]["K_STOP"] = sell_k_stop
    TRADING_PARAMS[pair]["buy"]["K_STOP"] = buy_k_stop


@dataclass(frozen=True)
class Score:
    total_pnl: float
    ops: int
    pnl_samples: int


def _score_key(score: Score) -> Tuple[float, int]:
    # Rank: net P&L, then number of P&L samples.
    return (float(score.total_pnl), int(score.pnl_samples))


def _robust_key(train: Score, test: Score) -> Tuple[float, int]:
    # Robust rank: worst-case P&L across train/test.
    return (float(min(train.total_pnl, test.total_pnl)), int(min(train.pnl_samples, test.pnl_samples)))


def _overall_robust_key(
    reset_train: Score,
    reset_test: Score,
    cont_train: Score,
    cont_test: Score,
) -> Tuple[float, int]:
    rr_pnl, rr_n = _robust_key(reset_train, reset_test)
    cr_pnl, cr_n = _robust_key(cont_train, cont_test)
    return (float(min(rr_pnl, cr_pnl)), int(min(rr_n, cr_n)))


def _score_run(ops) -> Score:
    if not ops:
        return Score(total_pnl=-1e18, ops=0, pnl_samples=0)

    total = float(ops[-1].cum_pnl or 0.0)
    pnl_samples = sum(1 for op in ops if op.pnl_abs is not None)
    return Score(total_pnl=total, ops=len(ops), pnl_samples=pnl_samples)


def _split_scores_from_single_run(ops, boundary_time: str) -> Tuple[Score, Score]:
    if not ops:
        empty = Score(total_pnl=-1e18, ops=0, pnl_samples=0)
        return empty, empty

    total_net = float(ops[-1].cum_pnl or 0.0)

    before = [op for op in ops if str(op.time) < str(boundary_time)]
    after = [op for op in ops if str(op.time) >= str(boundary_time)]

    if before:
        first_net = float(before[-1].cum_pnl or 0.0)
    else:
        first_net = 0.0

    first_samples = sum(1 for op in before if op.pnl_abs is not None)
    second_samples = sum(1 for op in after if op.pnl_abs is not None)

    first_score = Score(total_pnl=first_net, ops=len(before), pnl_samples=first_samples)
    second_score = Score(total_pnl=(total_net - first_net), ops=len(after), pnl_samples=second_samples)
    return first_score, second_score


def _combined_key(in_sample: Score, train: Score, test: Score) -> Tuple[float, int]:
    robust_pnl, robust_n = _robust_key(train, test)
    mean = (float(in_sample.total_pnl) + float(robust_pnl)) / 2.0
    return (mean, int(min(in_sample.pnl_samples, robust_n)))


def _combined_key_from_robust(in_sample: Score, robust: Tuple[float, int]) -> Tuple[float, int]:
    robust_pnl, robust_n = robust
    mean = (float(in_sample.total_pnl) + float(robust_pnl)) / 2.0
    return (mean, int(min(in_sample.pnl_samples, int(robust_n))))


def _format_env_lines(pair: str, cand: Candidate) -> List[str]:
    lines = []
    if cand.k_act is not None:
        lines.append(f"{pair}_K_ACT={cand.k_act:.1f}")
    if cand.min_margin is not None:
        lines.append(f"{pair}_MIN_MARGIN={cand.min_margin:.3f}")
        lines.append(f"# To disable {pair}_K_ACT without deleting it, set: {pair}_K_ACT=")
        lines.append(f"# (empty string) or: {pair}_K_ACT=none")
    lines.append(f"{pair}_STOP_PCT_LL={cand.stop_pcts['LL']:.2f}")
    lines.append(f"{pair}_STOP_PCT_LV={cand.stop_pcts['LV']:.2f}")
    lines.append(f"{pair}_STOP_PCT_MV={cand.stop_pcts['MV']:.2f}")
    lines.append(f"{pair}_STOP_PCT_HV={cand.stop_pcts['HV']:.2f}")
    lines.append(f"{pair}_STOP_PCT_HH={cand.stop_pcts['HH']:.2f}")
    return lines


def main() -> None:
    args = _parse_args()
    pair = args["pair"]
    mode = args["mode"]
    fee_rate = float(args["fee_pct"]) / 100.0
    split_method = args["split_method"]
    rank_mode = args["rank"]

    df = load_data(pair)
    if args["start"]:
        df = df[df["dtime"] >= args["start"]]
    if args["end"]:
        df = df[df["dtime"] <= args["end"]]
    df = df.reset_index(drop=True)

    if df.empty:
        raise ValueError("No rows after START/END slicing")

    split_idx = int(len(df) * float(args["train_split"]))
    train_df = df.iloc[:split_idx].reset_index(drop=True)
    test_df = df.iloc[split_idx:].reset_index(drop=True)
    split_boundary_time = None
    if not test_df.empty:
        split_boundary_time = str(df.iloc[split_idx]["dtime"])

    # Calibrate thresholds on full dataset (mimics real bot behavior where K_STOP is set once on all available data)
    _set_pair_atr_thresholds(pair, df)
    up_events_full, down_events_full = analyze_structural_noise(df)
    up_k = _k_values_by_level(up_events_full)
    down_k = _k_values_by_level(down_events_full)

    # MODE=CURRENT: evaluate current env config, ignoring min_ops thresholds
    if mode == "CURRENT":
        cand = _candidate_from_env(pair)
        _apply_current_config(pair, cand.stop_pcts, up_k, down_k)

        print(f"PAIR={pair} | MODE={mode} | FEE_PCT={args['fee_pct']:.3f} | ATR_DESV_LIMIT={ATR_DESV_LIMIT}")
        print(f"Rows: {len(df)} | Train rows: {len(train_df)} | Test rows: {len(test_df)}")
        print("\n=== CURRENT CONFIG (from .env) ===")
        for line in _format_env_lines(pair, cand):
            print(line)

        # Precompute full run for CONTINUE splits
        ops_all = simulate_operations(df, pair, fee_rate=fee_rate, max_ops=None)
        in_sample_score = _score_run(ops_all)

        if test_df.empty:
            print("\n=== SCORE (in-sample) ===")
            print(
                f"Train: pnl={in_sample_score.total_pnl:.2f}€ | ops={in_sample_score.ops} | pnl_samples={in_sample_score.pnl_samples}"
            )
            return

        # RESET split scores
        ops_train_reset = simulate_operations(train_df, pair, fee_rate=fee_rate, max_ops=None)
        train_reset = _score_run(ops_train_reset)
        ops_test_reset = simulate_operations(test_df, pair, fee_rate=fee_rate, max_ops=None)
        test_reset = _score_run(ops_test_reset)

        # CONTINUE split scores
        train_cont, test_cont = _split_scores_from_single_run(ops_all, split_boundary_time)

        print("\n=== SCORE (walk-forward) ===")
        print(f"Split method: {split_method}")

        if split_method == "RESET":
            robust = _robust_key(train_reset, test_reset)
            print("Robust rank key (min train/test): " f"pnl={robust[0]:.2f}€ | pnl_samples={robust[1]}")
            print(
                f"Train: pnl={train_reset.total_pnl:.2f}€ | ops={train_reset.ops} | pnl_samples={train_reset.pnl_samples}"
            )
            print(
                f"Test : pnl={test_reset.total_pnl:.2f}€ | ops={test_reset.ops} | pnl_samples={test_reset.pnl_samples}"
            )
        elif split_method == "CONTINUE":
            robust = _robust_key(train_cont, test_cont)
            print("Robust rank key (min train/test): " f"pnl={robust[0]:.2f}€ | pnl_samples={robust[1]}")
            print(
                f"Train: pnl={train_cont.total_pnl:.2f}€ | ops={train_cont.ops} | pnl_samples={train_cont.pnl_samples}"
            )
            print(
                f"Test : pnl={test_cont.total_pnl:.2f}€ | ops={test_cont.ops} | pnl_samples={test_cont.pnl_samples}"
            )
        else:
            robust_reset = _robust_key(train_reset, test_reset)
            robust_cont = _robust_key(train_cont, test_cont)
            overall = _overall_robust_key(train_reset, test_reset, train_cont, test_cont)
            print(
                "Robust RESET (min train/test): "
                f"pnl={robust_reset[0]:.2f}€ | pnl_samples={robust_reset[1]}"
            )
            print(
                "Robust CONTINUE (min train/test): "
                f"pnl={robust_cont[0]:.2f}€ | pnl_samples={robust_cont[1]}"
            )
            print(
                "Overall worst-case (min of both methods): "
                f"pnl={overall[0]:.2f}€ | pnl_samples={overall[1]}"
            )
            print(
                f"RESET    Train: pnl={train_reset.total_pnl:.2f}€ | ops={train_reset.ops} | pnl_samples={train_reset.pnl_samples}"
            )
            print(
                f"RESET    Test : pnl={test_reset.total_pnl:.2f}€ | ops={test_reset.ops} | pnl_samples={test_reset.pnl_samples}"
            )
            print(
                f"CONTINUE Train: pnl={train_cont.total_pnl:.2f}€ | ops={train_cont.ops} | pnl_samples={train_cont.pnl_samples}"
            )
            print(
                f"CONTINUE Test : pnl={test_cont.total_pnl:.2f}€ | ops={test_cont.ops} | pnl_samples={test_cont.pnl_samples}"
            )
        return

    print(
        f"PAIR={pair} | MODE={mode} | FEE_PCT={args['fee_pct']:.3f} | "
        f"ATR_DESV_LIMIT={ATR_DESV_LIMIT} | SPLIT_METHOD={split_method} | RANK={rank_mode}"
    )
    print(f"Rows: {len(df)} | Train rows: {len(train_df)} | Test rows: {len(test_df)}")
    print(f"Testing {_count_combinations(mode)} combinations (exhaustive)...\n")

    # Generate exhaustive candidate grid for CONSERVATIVE/AGGRESSIVE
    candidates = _iter_exhaustive_candidates(mode)

    # Run exhaustive search
    best: List[Tuple[Score, Candidate]] = []
    min_ops_required = int(args["min_ops"])
    evaluated: List[Tuple[Tuple[float, int], Score, Score, Score, Candidate]] = []
    tested = 0
    passed_train = 0
    passed_test = 0

    for cand in candidates:
        tested += 1
        _apply_candidate_mode(pair, mode, cand.k_act, cand.min_margin, cand.stop_pcts, up_k, down_k)

        # In-sample score (full dataset)
        ops_all = simulate_operations(df, pair, fee_rate=fee_rate, max_ops=None)
        in_sample_score = _score_run(ops_all)

        # Split scores
        if test_df.empty:
            train_score = in_sample_score
            test_score = Score(total_pnl=-1e18, ops=0, pnl_samples=0)
        elif split_method == "RESET":
            ops_train = simulate_operations(train_df, pair, fee_rate=fee_rate, max_ops=None)
            train_score = _score_run(ops_train)
            ops_test = simulate_operations(test_df, pair, fee_rate=fee_rate, max_ops=None)
            test_score = _score_run(ops_test)
        elif split_method == "CONTINUE":
            train_score, test_score = _split_scores_from_single_run(ops_all, split_boundary_time)
        else:
            # BOTH: compute RESET and CONTINUE and rank by worst-case across the two methods
            ops_train = simulate_operations(train_df, pair, fee_rate=fee_rate, max_ops=None)
            reset_train = _score_run(ops_train)
            ops_test = simulate_operations(test_df, pair, fee_rate=fee_rate, max_ops=None)
            reset_test = _score_run(ops_test)
            cont_train, cont_test = _split_scores_from_single_run(ops_all, split_boundary_time)
            train_score = reset_train
            test_score = reset_test

        if split_method == "BOTH":
            if min(reset_train.pnl_samples, cont_train.pnl_samples) < min_ops_required:
                continue
        else:
            if train_score.pnl_samples < min_ops_required:
                continue
        passed_train += 1

        if test_df.empty:
            best.append((in_sample_score, cand))
            continue

        if split_method == "BOTH":
            if min(reset_test.pnl_samples, cont_test.pnl_samples) < int(args["min_test_ops"]):
                continue
        else:
            if test_score.pnl_samples < int(args["min_test_ops"]):
                continue
        passed_test += 1

        if split_method == "BOTH":
            overall_robust = _overall_robust_key(reset_train, reset_test, cont_train, cont_test)
            if rank_mode == "MEAN":
                key = _combined_key_from_robust(in_sample_score, overall_robust)
            else:
                key = overall_robust
            evaluated.append((key, in_sample_score, reset_train, reset_test, cand))
        else:
            if rank_mode == "MEAN":
                key = _combined_key(in_sample_score, train_score, test_score)
            else:
                key = _robust_key(train_score, test_score)
            evaluated.append((key, in_sample_score, train_score, test_score, cand))

    print(f"Exhaustive grid tested: {tested} | Passed train ops: {passed_train} | Passed test ops: {passed_test}")

    if test_df.empty:
        if not best:
            print("No candidates met MIN_OPS constraint.")
            sys.exit(2)
        best.sort(key=lambda t: _score_key(t[0]), reverse=True)
        train_score, best_cand = best[0]
        print("\n=== BEST (in-sample, exhaustive) ===")
        print(f"Train: pnl={train_score.total_pnl:.2f}€ | ops={train_score.ops} | pnl_samples={train_score.pnl_samples}")
        print("\nSuggested .env lines (use single-underscore names):")
        for line in _format_env_lines(pair, best_cand):
            print(line)
        return

    if not evaluated:
        print("No candidates met MIN_TEST_OPS constraint on the test split.")
        sys.exit(3)

    evaluated.sort(key=lambda t: t[0], reverse=True)
    key, in_sample_score, train_score, test_score, best_cand = evaluated[0]
    robust = _robust_key(train_score, test_score)
    combined = _combined_key(in_sample_score, train_score, test_score)

    print("\n=== BEST (walk-forward, exhaustive) ===")
    print(f"Split method: {split_method}")
    print(f"Rank mode: {rank_mode}")
    if split_method == "BOTH":
        # Note: train_score/test_score here correspond to RESET; key was computed from overall worst-case.
        ops_all_best = simulate_operations(df, pair, fee_rate=fee_rate, max_ops=None)
        cont_train_best, cont_test_best = _split_scores_from_single_run(ops_all_best, split_boundary_time)
        overall = _overall_robust_key(train_score, test_score, cont_train_best, cont_test_best)
        combined_overall = _combined_key_from_robust(in_sample_score, overall)
        robust_reset = _robust_key(train_score, test_score)
        robust_cont = _robust_key(cont_train_best, cont_test_best)
        print("Robust RESET (min train/test): " f"pnl={robust_reset[0]:.2f}€ | pnl_samples={robust_reset[1]}")
        print("Robust CONTINUE (min train/test): " f"pnl={robust_cont[0]:.2f}€ | pnl_samples={robust_cont[1]}")
        print("Overall worst-case (min of both methods): " f"pnl={overall[0]:.2f}€ | pnl_samples={overall[1]}")
        print("Combined mean (in-sample + overall)/2: " f"mean={combined_overall[0]:.2f}€ | n={combined_overall[1]}")
        print(f"In-sample: pnl={in_sample_score.total_pnl:.2f}€ | ops={in_sample_score.ops} | pnl_samples={in_sample_score.pnl_samples}")
        print(f"RESET Train: pnl={train_score.total_pnl:.2f}€ | ops={train_score.ops} | pnl_samples={train_score.pnl_samples}")
        print(f"RESET Test : pnl={test_score.total_pnl:.2f}€ | ops={test_score.ops} | pnl_samples={test_score.pnl_samples}")
        print(
            f"CONT Train: pnl={cont_train_best.total_pnl:.2f}€ | ops={cont_train_best.ops} | pnl_samples={cont_train_best.pnl_samples}"
        )
        print(
            f"CONT Test : pnl={cont_test_best.total_pnl:.2f}€ | ops={cont_test_best.ops} | pnl_samples={cont_test_best.pnl_samples}"
        )
    else:
        print("Robust key (min train/test): " f"pnl={robust[0]:.2f}€ | pnl_samples={robust[1]}")
        print("Combined mean (in-sample + robust)/2: " f"mean={combined[0]:.2f}€ | n={combined[1]}")
        print(f"In-sample: pnl={in_sample_score.total_pnl:.2f}€ | ops={in_sample_score.ops} | pnl_samples={in_sample_score.pnl_samples}")
        print(f"Train: pnl={train_score.total_pnl:.2f}€ | ops={train_score.ops} | pnl_samples={train_score.pnl_samples}")
        print(f"Test : pnl={test_score.total_pnl:.2f}€ | ops={test_score.ops} | pnl_samples={test_score.pnl_samples}")
    print("\n=== TOP CANDIDATES (robust rank) ===")
    for i, (k, ins, tr, te, c) in enumerate(evaluated[:5], start=1):
        act = f"K_ACT={c.k_act:.1f}" if c.k_act is not None else f"MIN_MARGIN={c.min_margin:.3f}"

        if split_method != "BOTH":
            r = _robust_key(tr, te)
            m = _combined_key(ins, tr, te)
            print(
                f"{i:>2}. key={k[0]:.1f}€ n={k[1]} | mean={m[0]:.1f}€ | robust={r[0]:.1f}€ (n={r[1]}) | "
                f"ins={ins.total_pnl:.1f}€ ({ins.pnl_samples}) | train={tr.total_pnl:.1f}€ ({tr.pnl_samples}) | "
                f"test={te.total_pnl:.1f}€ ({te.pnl_samples}) | {act}"
            )
            continue

        # BOTH: recompute RESET+CONTINUE metrics for accurate reporting (top 5 only)
        _apply_candidate_mode(pair, mode, c.k_act, c.min_margin, c.stop_pcts, up_k, down_k)
        ops_all_top = simulate_operations(df, pair, fee_rate=fee_rate, max_ops=None)
        ins_top = _score_run(ops_all_top)
        ops_train_reset_top = simulate_operations(train_df, pair, fee_rate=fee_rate, max_ops=None)
        train_reset_top = _score_run(ops_train_reset_top)
        ops_test_reset_top = simulate_operations(test_df, pair, fee_rate=fee_rate, max_ops=None)
        test_reset_top = _score_run(ops_test_reset_top)
        train_cont_top, test_cont_top = _split_scores_from_single_run(ops_all_top, split_boundary_time)

        robust_reset_top = _robust_key(train_reset_top, test_reset_top)
        robust_cont_top = _robust_key(train_cont_top, test_cont_top)
        overall_top = _overall_robust_key(train_reset_top, test_reset_top, train_cont_top, test_cont_top)
        mean_top = _combined_key_from_robust(ins_top, overall_top)

        print(
            f"{i:>2}. key={k[0]:.1f}€ n={k[1]} | mean={mean_top[0]:.1f}€ | overall={overall_top[0]:.1f}€ (n={overall_top[1]}) | "
            f"reset={robust_reset_top[0]:.1f}€ (n={robust_reset_top[1]}) | cont={robust_cont_top[0]:.1f}€ (n={robust_cont_top[1]}) | "
            f"ins={ins_top.total_pnl:.1f}€ ({ins_top.pnl_samples}) | {act}"
        )
    print("\nSuggested .env lines (use single-underscore names):")
    for line in _format_env_lines(pair, best_cand):
        print(line)


if __name__ == "__main__":
    main()
