"""¿De donde sale la perdida de operar? Descomposicion por ciclos venta->compra, en activo base.

Read-only y temporal. Lee el CSV OHLCVT de 15 min de Kraken, sin base de datos.

En activo base la pata dentro del activo vale 0 por construccion: solo la pata en caja
(vender y recomprar) cambia cuantas monedas hay. Un ciclo venta a P_s -> compra a P_b gana
P_s / P_b - 1 menos dos comisiones. Por diseño, la compra no puede activarse hasta que el
precio caiga K*ATR + mm*P_s por debajo de la venta, y ejecuta K*ATR por encima del minimo,
asi que un ciclo LIMPIO recompra como mucho a P_s * (1 - mm): gana al menos mm - comisiones.
No puede perder.

La unica forma de recomprar MAS CARO es el reanclaje de la activacion de compra (mirror de
`reanchor_activation_price`): en cuanto el precio supera P_s, la activacion sube con el, y
la recompra llega tras una caida de K*ATR + mm desde el nuevo maximo. Existe para que un bot
en caja vuelva a entrar tras un rally en vez de quedarse fuera para siempre; en euros no
cuesta nada (caja es caja), en base cuesta toda la excursion. Es decir: la pata en caja
tiene stop de BENEFICIO (el trailing de compra la cierra al primer rebote) y NO tiene stop de
PERDIDA. El bot se diseño en euros, donde la caja no tiene riesgo.

Este script no simula nada nuevo: pasa las 105 configs de la rejilla una vez cada una,
empareja cada venta con la compra siguiente y clasifica el ciclo por su signo en base.
Reporta, por min_margin: cuantos ciclos ganan y cuanto (contra el suelo mm - comisiones),
cuantos pierden y cuanto, y que parte del tiempo pasa el bot en caja.

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):

    PYTHONPATH=. python scripts/analysis/cycle_decomposition.py "C:/Dev/Kraken OHLCVT/XBTEUR_15.csv"
"""

import argparse
import os
import sys
import time
from itertools import pairwise

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import grid_sweep_holdout as gsh
import rally_gate_oracle as rgo

import trading.optimizer.search as optimizer
from core.config import ATR_DESV_LIMIT, RECALIBRATION_BARS
from trading.engine import simulate_operations


def cycles(ops, fee_rate: float) -> list[float]:
    """Ganancia en base, en %, de cada ciclo venta->compra cerrado, con sus dos comisiones."""
    out = []
    for prev, curr in pairwise(ops):
        if prev.side == "sell" and curr.side == "buy":
            out.append(((prev.price / curr.price) * (1.0 - fee_rate) ** 2 - 1.0) * 100.0)
    return out


def time_in_cash(ops, df: pd.DataFrame) -> float:
    """Fraccion de velas de la ventana en las que el bot estaba en euros."""
    times = [str(t) for t in df["dtime"].tolist()]
    index_of = {t: i for i, t in enumerate(times)}
    in_cash = 0
    for prev, curr in pairwise(ops):
        if prev.side == "sell":
            in_cash += index_of[str(curr.time)] - index_of[str(prev.time)]
    if ops and ops[-1].side == "sell":
        in_cash += len(times) - 1 - index_of[str(ops[-1].time)]
    return in_cash / max(len(times) - 1, 1)


def _median(xs: list[float]) -> float:
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser(description="Descomposicion por ciclos venta->compra, en activo base")
    ap.add_argument("csv", help="Ruta al <PAIR>_15.csv de Kraken")
    ap.add_argument("--cal-start", default="2025-01-01")
    ap.add_argument("--start", default="2025-04-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--recalib-bars", type=int, default=RECALIBRATION_BARS)
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
    fee_rate = gsh.FEE / 100.0
    floor = ((1.0 - fee_rate) ** 2 - 1.0) * 100.0  # lo que cuestan las dos comisiones de un ciclo
    print(
        f"\n[ventana] {gsh._dtime(ctx.df, 0)[:10]}..{gsh._dtime(ctx.df, len(ctx.df) - 1)[:10]}  comision por ciclo {floor:+.2f} %"
    )

    print(f"\n[barrido] {len(gsh.candidates())} configs", flush=True)
    t0 = time.perf_counter()
    by_mm: dict[float, dict] = {}
    all_gains, all_losses = [], []
    for cand in gsh.candidates():
        cfg = optimizer._build_engine_config(
            gsh.PAIR, cand, ctx.atr_ratio_thresholds, ctx.up_k, ctx.down_k, ATR_DESV_LIMIT, ctx.calibration_points
        )
        ops = simulate_operations(ctx.df, cfg, fee_rate=fee_rate)
        cyc = cycles(ops, fee_rate)
        row = by_mm.setdefault(cand.min_margin, {"gains": [], "losses": [], "cash": [], "n_cfg": 0})
        row["gains"] += [c for c in cyc if c > 0.0]
        row["losses"] += [c for c in cyc if c <= 0.0]
        row["cash"].append(time_in_cash(ops, ctx.df))
        row["n_cfg"] += 1
        all_gains += [c for c in cyc if c > 0.0]
        all_losses += [c for c in cyc if c <= 0.0]
    print(f"  {time.perf_counter() - t0:.0f}s")

    print("\n[por min_margin] ciclos venta->compra de las 5 configs (stop 0.5..0.9) de cada mm, en activo base")
    print(
        f"  {'mm':>5} {'suelo':>7} {'ciclos':>7} {'ganan':>6} {'med gana':>9} {'suma':>7} "
        f"{'pierden':>8} {'med pierde':>11} {'suma':>7} {'peor':>7} {'en caja':>8}"
    )
    for mm in sorted(by_mm):
        r = by_mm[mm]
        g, lo = r["gains"], r["losses"]
        print(
            f"  {mm:>5.2f} {mm * 100 + floor:>+6.1f}% {len(g) + len(lo):>7} {len(g):>6} {_median(g):>+8.1f}% {sum(g):>+6.0f}% "
            f"{len(lo):>8} {_median(lo):>+10.1f}% {sum(lo):>+6.0f}% {min(lo) if lo else 0.0:>+6.1f}% "
            f"{100.0 * sum(r['cash']) / r['n_cfg']:>7.0f}%"
        )
    print(
        "  suelo = mm - comisiones: lo minimo que gana un ciclo sin reanclaje. 'en caja' = fraccion del tiempo en euros,"
        " media de las 5 configs."
    )

    n = len(all_gains) + len(all_losses)
    print(f"\n[total] {n} ciclos en las 105 configs")
    print(
        f"  ganan   {len(all_gains):>5} ({100.0 * len(all_gains) / n:.0f} %)  mediana {_median(all_gains):+.1f} %  suma {sum(all_gains):+.0f} %"
    )
    print(
        f"  pierden {len(all_losses):>5} ({100.0 * len(all_losses) / n:.0f} %)  mediana {_median(all_losses):+.1f} %  suma {sum(all_losses):+.0f} %"
    )
    big = sorted(all_losses)[: max(1, len(all_losses) // 10)]
    print(
        f"  el decil peor de las perdidas: {len(big)} ciclos, suma {sum(big):+.0f} % — {100.0 * sum(big) / sum(all_losses):.0f} % de toda la perdida"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
