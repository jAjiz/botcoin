"""¿Es viable el bot SIN reanclaje de la activacion? Ablacion sobre las 105 configs, en activo base.

Read-only y temporal. Lee el CSV OHLCVT de 15 min de Kraken, sin base de datos.

La descomposicion por ciclos (cycle_decomposition.py) situo toda la perdida de operar en un
mecanismo: el reanclaje de la activacion de compra, que sigue al precio cuando se aleja y
hace recomprar mas caro tras un rally. Sin el, un ciclo venta->recompra no puede perder
(recompra como mucho a P_venta * (1 - mm)). La contrapartida es que un bot en caja cuyo
precio no vuelve NO RECOMPRA NUNCA, y en activo base eso es una perdida sin limite que no
aparece como ciclo. Es decir, quitar el reanclaje es apostar a que el precio vuelve.

Por eso se mide en DOS ventanas elegidas antes de correr:

  * la del estudio, 2025-04-01 .. 2025-12-31: el precio SI volvio (cayo en noviembre
    por debajo de las ventas de primavera). Es la ventana mas favorable a la apuesta.
  * 2024-10-01 .. 2025-03-31: subio de 60k a 100k y no volvio a 60k. Es la desfavorable.

Un resultado que solo gane en la primera es la apuesta direccional, medida. Tres brazos:

  base            el motor de produccion (ambos lados reanclan)
  sin compra      `reanchor_buy=False`: la recompra espera a que el precio vuelva
  sin ninguno     `reanchor_sell=False, reanchor_buy=False`
  hasta la venta  `reanchor_cap_at_entry=True`: la activacion sigue al precio pero nunca cruza el
                  precio de la venta, asi que la recompra llega cuando el precio VUELVE al nivel de
                  la venta (K*ATR por encima, y no mm + K*ATR por debajo como sin reanclaje)

Puntuacion en ACTIVO BASE: (1 + r_bot) / (1 + r_hold) - 1, mantener = 0 %. Se reporta
la distribucion de las 105 configs por brazo, la mediana por min_margin, y el tiempo en caja.

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):

    PYTHONPATH=. python scripts/analysis/reanchor_ablation.py "C:/Dev/Kraken OHLCVT/XBTEUR_15.csv"
    PYTHONPATH=. python scripts/analysis/reanchor_ablation.py CSV --cal-start 2024-07-01 --start 2024-10-01 --end 2025-03-31
"""

import argparse
import dataclasses
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cycle_decomposition as cd
import grid_sweep_holdout as gsh
import rally_gate_oracle as rgo

import trading.optimizer.search as optimizer
from core.config import ATR_DESV_LIMIT, RECALIBRATION_BARS
from trading.engine import mark_to_market, simulate_operations

ARMS = {
    "base": {},
    "sin reanclaje de compra": {"reanchor_buy": False},
    "sin reanclaje (ambos)": {"reanchor_sell": False, "reanchor_buy": False},
    "reanclaje hasta la venta": {"reanchor_cap_at_entry": True},
}


def sweep(ctx, cands, overrides: dict, hold: float) -> list[dict]:
    final_price = float(ctx.df.iloc[-1]["close"])
    out = []
    for cand in cands:
        cfg = optimizer._build_engine_config(
            gsh.PAIR, cand, ctx.atr_ratio_thresholds, ctx.up_k, ctx.down_k, ATR_DESV_LIMIT, ctx.calibration_points
        )
        cfg = dataclasses.replace(cfg, **overrides)
        ops = simulate_operations(ctx.df, cfg, fee_rate=gsh.FEE / 100.0)
        eur = mark_to_market(ops, final_price) if ops else 0.0
        out.append(
            {
                "cand": cand,
                "base": gsh._btc(eur, hold),
                "ops": sum(1 for op in ops if op.idx != 1),
                "cash": cd.time_in_cash(ops, ctx.df),
                "ends_in_cash": bool(ops) and ops[-1].side == "sell",
            }
        )
    return out


def _median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else float("nan")


def report(results: dict[str, list[dict]], hold: float) -> None:
    print(f"\n[resultado] activo base sobre la ventana, mantener = 0 % (hold {hold:+.2f} % en euros)")
    print(
        f"  {'brazo':<26} {'mediana':>8} {'p25':>7} {'p75':>7} {'mejor':>7} {'peor':>7} {'bate':>8} {'ops':>5} {'en caja':>8} {'acaba en caja':>14}"
    )
    for arm, rows in results.items():
        vals = sorted(r["base"] for r in rows)
        n = len(vals)
        print(
            f"  {arm:<26} {vals[n // 2]:>+7.1f}% {vals[n // 4]:>+6.1f}% {vals[3 * n // 4]:>+6.1f}% {vals[-1]:>+6.1f}% {vals[0]:>+6.1f}% "
            f"{sum(1 for v in vals if v > 0):>4}/{n:<3} {sum(r['ops'] for r in rows) / n:>5.1f} "
            f"{100.0 * sum(r['cash'] for r in rows) / n:>7.0f}% {sum(1 for r in rows if r['ends_in_cash']):>9}/{n}"
        )

    print("\n[por min_margin] mediana de activo base de las 5 configs de cada mm, por brazo")
    arms = list(results)
    print(f"  {'mm':>5}" + "".join(f"{a[:22]:>24}" for a in arms))
    mms = sorted({r["cand"].min_margin for r in results[arms[0]]})
    for mm in mms:
        line = f"  {mm:>5.2f}"
        for arm in arms:
            rows = [r for r in results[arm] if r["cand"].min_margin == mm]
            line += f"{_median([r['base'] for r in rows]):>+17.1f}% {sum(r['ops'] for r in rows) / len(rows):>5.1f}"
        print(line)
    print("  (activo base %, operaciones medias)")

    base = results[arms[0]]
    for arm in arms[1:]:
        deltas = sorted(b["base"] - a["base"] for a, b in zip(base, results[arm], strict=True))
        n = len(deltas)
        print(
            f"\n  {arm} vs base, config a config: mediana {deltas[n // 2]:+.1f}  p25 {deltas[n // 4]:+.1f}  p75 {deltas[3 * n // 4]:+.1f}"
            f"  mejora en {sum(1 for d in deltas if d > 0)}/{n}"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="Ablacion del reanclaje de la activacion, en activo base")
    ap.add_argument("csv", help="Ruta al <PAIR>_15.csv de Kraken")
    ap.add_argument("--cal-start", default="2025-01-01")
    ap.add_argument("--start", default="2025-04-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--recalib-bars", type=int, default=RECALIBRATION_BARS)
    ap.add_argument("--fee", type=float, default=gsh.FEE, help="Comision por operacion, en %.")
    args = ap.parse_args()
    gsh.FEE = args.fee
    print(f"[comision] {gsh.FEE:.2f} % por operacion")

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
    print(f"\n[ventana] {gsh._dtime(ctx.df, 0)[:10]}..{gsh._dtime(ctx.df, len(ctx.df) - 1)[:10]}  hold {hold:+.2f} %")

    cands = gsh.candidates()
    print(f"\n[barrido] {len(cands)} configs x {len(ARMS)} brazos", flush=True)
    t0 = time.perf_counter()
    results = {arm: sweep(ctx, cands, overrides, hold) for arm, overrides in ARMS.items()}
    print(f"  {time.perf_counter() - t0:.0f}s")
    report(results, hold)
    print(
        "\n[lectura] Sin reanclaje, un bot en caja cuyo precio no vuelve no recompra nunca: 'acaba en caja'\n"
        "          dice cuantas configs terminan la ventana fuera del activo. Comparar las dos ventanas."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
