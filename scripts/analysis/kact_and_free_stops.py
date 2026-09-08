"""Los dos huecos del registro: activacion inmediata (k_act = 0) y los cinco stops libres.

Read-only y temporal. Lee el CSV OHLCVT de 15 min de Kraken, sin base de datos.

Ninguno de los dos esta cerrado por medicion directa sobre la ventana larga:

  * la rama k_act esta DESACTIVADA en todos los barridos del estudio (las 105 configs usan
    `k_act=None`, es decir la rama min_margin). Lo que hay contra ella es estructural --
    no tiene suelo independiente del ATR, asi que su barrera se hunde cuando el mercado se
    calma -- y un recuento viejo de 12 ajustes hold-out que no gano ninguno. `k_act = 0`,
    la activacion inmediata, no se ha barrido nunca.
  * los cinco stops libres se midieron UNA vez, con el buscador Optuna sobre 2025 y
    min_margin fijo en 0.004, y el resultado fue `converged: False`: 0 de 4 semillas
    coincidieron tras 12 000 evaluaciones. Eso dice que la BUSQUEDA no identifica un
    optimo, no que la region no contenga uno. Con enumeracion imposible (21^5), aqui se
    sondea por muestreo aleatorio, que responde una pregunta mas modesta y suficiente:
    ¿hay algo ahi dentro que bata a la mejor config de stop compartido, o a mantener?

Las tres ramas comparten calendario de calibracion y ventana, asi que son comparables entre
si y con los resultados anteriores. Puntuacion en ACTIVO BASE, mantener = 0 %.

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):

    PYTHONPATH=. python scripts/analysis/kact_and_free_stops.py CSV --cal-start 2022-07-01 \
        --start 2023-01-01 --end 2025-12-31 --samples 600
"""

import argparse
import os
import random
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cycle_decomposition as cd
import grid_sweep_holdout as gsh
import rally_gate_oracle as rgo

import trading.optimizer.search as optimizer
from core.config import ATR_DESV_LIMIT, RECALIBRATION_BARS
from core.config import VOLATILITY_LEVELS as LEVELS
from trading.engine import mark_to_market, simulate_operations
from trading.optimizer.search import Candidate

K_ACTS = (0.0, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0)
STOPS = (0.5, 0.6, 0.7, 0.8, 0.9)


def evaluate(ctx, cand: Candidate, hold: float, final_price: float) -> dict:
    cfg = optimizer._build_engine_config(
        gsh.PAIR, cand, ctx.atr_ratio_thresholds, ctx.up_k, ctx.down_k, ATR_DESV_LIMIT, ctx.calibration_points
    )
    ops = simulate_operations(ctx.df, cfg, fee_rate=gsh.FEE / 100.0)
    eur = mark_to_market(ops, final_price) if ops else 0.0
    return {
        "cand": cand,
        "base": gsh._btc(eur, hold),
        "ops": sum(1 for op in ops if op.idx != 1),
        "cash": cd.time_in_cash(ops, ctx.df) if ops else 1.0,
    }


def kact_candidates() -> list[Candidate]:
    """La rama k_act, incluida la activacion inmediata. min_margin se ignora en esta rama."""
    return [Candidate(k_act=k, min_margin=0.0, stop_pcts=dict.fromkeys(LEVELS, s)) for k in K_ACTS for s in STOPS]


def free_candidates(n: int, rng: random.Random) -> list[Candidate]:
    """Muestreo aleatorio de los cinco stops libres, con min_margin de la misma rejilla que las 105."""
    mms = gsh._grid(gsh.MM_GRID)
    return [
        Candidate(
            k_act=None,
            min_margin=rng.choice(mms),
            stop_pcts={lvl: rng.choice(STOPS) for lvl in LEVELS},
        )
        for _ in range(n)
    ]


def _stats(rows: list[dict], label: str) -> None:
    vals = sorted(r["base"] for r in rows)
    n = len(vals)
    best = max(rows, key=lambda r: r["base"])
    print(
        f"  {label:<28} {vals[n // 2]:>+7.1f}% {vals[n // 4]:>+6.1f}% {vals[3 * n // 4]:>+6.1f}% "
        f"{vals[-1]:>+6.1f}% {vals[0]:>+6.1f}% {sum(1 for v in vals if v > 0):>4}/{n:<4} "
        f"{sum(r['ops'] for r in rows) / n:>6.1f} {100.0 * sum(r['cash'] for r in rows) / n:>6.0f}%"
    )
    return best


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--cal-start", default="2022-07-01")
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--samples", type=int, default=600)
    ap.add_argument("--recalib-bars", type=int, default=RECALIBRATION_BARS)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    cal_t0 = int(pd.Timestamp(args.cal_start).timestamp())
    t1 = int(pd.Timestamp(args.end).timestamp()) + 86_399
    print(f"[datos] {args.csv}")
    frame = rgo.build_frame(args.csv, cal_t0, t1)
    gsh._install_ohlc(frame)
    print(f"\n[calibracion] calendario cada {args.recalib_bars} velas (tarda minutos)", flush=True)
    gsh._install_calibration_cache(frame, args.recalib_bars)

    first_bar = int((frame["dtime"] >= pd.Timestamp(args.start)).idxmax())
    last_bar = len(frame) - 1
    ctx = gsh._context(frame, first_bar, last_bar, gsh._dtime(frame, last_bar))
    hold = (float(ctx.df.iloc[-1]["close"]) / float(ctx.df.iloc[0]["close"]) - 1.0) * 100.0
    final_price = float(ctx.df.iloc[-1]["close"])
    print(f"\n[ventana] {gsh._dtime(ctx.df, 0)[:10]}..{gsh._dtime(ctx.df, len(ctx.df) - 1)[:10]}  hold {hold:+.2f} %")

    rng = random.Random(args.seed)
    groups = {
        "105 compartido (referencia)": gsh.candidates(),
        "rama k_act (con k_act=0)": kact_candidates(),
        f"{args.samples} stops libres": free_candidates(args.samples, rng),
    }
    print(f"\n[barrido] {sum(len(v) for v in groups.values())} simulaciones", flush=True)
    print(
        f"\n  {'grupo':<28} {'mediana':>8} {'p25':>7} {'p75':>7} {'mejor':>7} {'peor':>7} "
        f"{'bate':>9} {'ops':>6} {'en caja':>7}"
    )
    bests, kept = {}, {}
    for label, cands in groups.items():
        t0 = time.perf_counter()
        rows = [evaluate(ctx, c, hold, final_price) for c in cands]
        kept[label] = rows
        bests[label] = _stats(rows, label)
        print(f"  {'':<28} ({time.perf_counter() - t0:.0f}s)", flush=True)

    print("")
    print("[rama k_act, por multiplicador]  la activacion inmediata es k_act = 0")
    print(f"  {'k_act':>7} {'mediana':>9} {'mejor':>8} {'peor':>8} {'bate':>7} {'ops':>7} {'en caja':>8}")
    for k in K_ACTS:
        rows = [r for r in kept["rama k_act (con k_act=0)"] if r["cand"].k_act == k]
        vals = sorted(r["base"] for r in rows)
        print(
            f"  {k:>7g} {vals[len(vals) // 2]:>+8.1f}% {vals[-1]:>+7.1f}% {vals[0]:>+7.1f}% "
            f"{sum(1 for v in vals if v > 0):>3}/{len(vals):<3} {sum(r['ops'] for r in rows) / len(rows):>6.0f} "
            f"{100.0 * sum(r['cash'] for r in rows) / len(rows):>7.0f}%"
        )

    print("\n[mejor de cada grupo]")
    for label, b in bests.items():
        c = b["cand"]
        stops = ", ".join(f"{c.stop_pcts[lvl]:.1f}" for lvl in LEVELS)
        head = f"k_act={c.k_act:g}" if c.k_act is not None else f"mm={c.min_margin:.3f}"
        print(f"  {label:<28} {b['base']:>+7.1f}%  {head:<12} stops [{stops}]  {b['ops']:>4} ops")

    print("")
    print("[lectura] 'bate' cuenta configs por encima de mantener. Si la mejor de los stops libres no")
    print("          supera a la mejor de las 105, liberar los cinco niveles no aporta region nueva;")
    print("          si ninguna de las tres bate a mantener, la ventana esta cerrada para las tres.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
