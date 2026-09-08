"""Una sola configuracion contra mantener, año a año: mm = 0 con el reanclaje topado en la venta.

Read-only y temporal. Lee el CSV OHLCVT de 15 min de Kraken, sin base de datos.

El barrido de comisiones que dio el umbral de rotacion (~0.16-0.26 % por operacion) se midio
sobre UNA ventana, 2025-04..2025-12, elegida porque el precio volvio. Esa es la ventana mas
favorable de todos los datos, asi que cualquier lectura sacada de ella describe el mejor caso.
Aqui se corre la misma configuracion sobre intervalos que no se eligieron por su resultado --
los años naturales -- y se compara UNICAMENTE contra mantener, que es la unica referencia que
importa. No hay rejilla, no hay mediana, no hay otras configs: una config, muchos intervalos.

La configuracion es la de la tabla de comisiones: `min_margin = 0`, `stop_pct = 0.9` en los
cinco niveles, rama min_margin (`k_act = None`), y `reanchor_cap_at_entry = True`, que es el
tope que impide que la activacion de recompra cruce el precio de la venta. Es la que mas
opera de las que no pierden por ciclo.

Cada intervalo se calibra con seis meses de historia previa, que es la convencion de estos
arneses (`--cal-start` en reanchor_ablation), no la historia completa desde T0. El calendario
de recalibracion es el de produccion.

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):

    PYTHONPATH=. python scripts/analysis/capped_mm0_across_years.py "C:/Dev/Kraken OHLCVT/XBTEUR_15.csv"
    PYTHONPATH=. python scripts/analysis/capped_mm0_across_years.py CSV --years 2021 2022 2023
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
from core.config import VOLATILITY_LEVELS as LEVELS
from trading.engine import mark_to_market, simulate_operations
from trading.optimizer.search import Candidate

CONFIG = Candidate(k_act=None, min_margin=0.0, stop_pcts=dict.fromkeys(LEVELS, 0.9))
OVERRIDES = {"reanchor_cap_at_entry": True}
FEES = (0.40, 0.16)
LEAD_MONTHS = 6


def evaluate(ctx, fee_pct: float, hold: float) -> dict:
    cfg = optimizer._build_engine_config(
        gsh.PAIR, CONFIG, ctx.atr_ratio_thresholds, ctx.up_k, ctx.down_k, ATR_DESV_LIMIT, ctx.calibration_points
    )
    cfg = dataclasses.replace(cfg, **OVERRIDES)
    ops = simulate_operations(ctx.df, cfg, fee_rate=fee_pct / 100.0)
    eur = mark_to_market(ops, float(ctx.df.iloc[-1]["close"])) if ops else 0.0
    return {
        "eur": eur,
        "base": gsh._btc(eur, hold),
        "ops": sum(1 for op in ops if op.idx != 1),
        "cash": cd.time_in_cash(ops, ctx.df) if ops else 1.0,
        "ends_in_cash": bool(ops) and ops[-1].side == "sell",
    }


def window(csv: str, start: str, end: str, recalib_bars: int) -> tuple:
    lead = (pd.Timestamp(start) - pd.DateOffset(months=LEAD_MONTHS)).strftime("%Y-%m-%d")
    t0 = int(pd.Timestamp(lead).timestamp())
    t1 = int(pd.Timestamp(end).timestamp()) + 86_399
    frame = rgo.build_frame(csv, t0, t1)
    gsh._install_ohlc(frame)
    gsh._install_calibration_cache(frame, recalib_bars)
    first_bar = int((frame["dtime"] >= pd.Timestamp(start)).idxmax())
    last_bar = len(frame) - 1
    ctx = gsh._context(frame, first_bar, last_bar, gsh._dtime(frame, last_bar))
    hold = (float(ctx.df.iloc[-1]["close"]) / float(ctx.df.iloc[0]["close"]) - 1.0) * 100.0
    return ctx, hold


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--years", nargs="*", type=int, default=[2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025])
    ap.add_argument("--recalib-bars", type=int, default=RECALIBRATION_BARS)
    args = ap.parse_args()

    print("[config] mm=0.000 stop=0.9 en los cinco niveles, reanclaje topado en la venta")
    print(f"[datos]  {args.csv}  calibracion con {LEAD_MONTHS} meses previos")
    rows = []
    for year in args.years:
        print("")
        print(f"[{year}] construyendo marco y calendario...", flush=True)
        t0 = time.perf_counter()
        ctx, hold = window(args.csv, f"{year}-01-01", f"{year}-12-31", args.recalib_bars)
        row = {"year": year, "hold": hold}
        for fee in FEES:
            row[fee] = evaluate(ctx, fee, hold)
        rows.append(row)
        print(f"  hold {hold:+.1f} %  ({time.perf_counter() - t0:.0f}s)", flush=True)

    print("")
    print("[resultado] activo base frente a mantener; mantener = 0 % por construccion")
    head = f"  {'año':>6} {'hold EUR':>10}"
    for fee in FEES:
        head += f" | {f'{fee:.2f} %: base':>15} {'EUR':>8} {'ops':>5} {'caja':>6}"
    print(head)
    for r in rows:
        line = f"  {r['year']:>6} {r['hold']:>+9.1f} %"
        for fee in FEES:
            e = r[fee]
            line += f" | {e['base']:>+14.1f}% {e['eur']:>+7.1f}% {e['ops']:>5} {100 * e['cash']:>5.0f}%"
        print(line)

    for fee in FEES:
        wins = sum(1 for r in rows if r[fee]["base"] > 0)
        vals = sorted(r[fee]["base"] for r in rows)
        print("")
        print(
            f"  a {fee:.2f} %: bate a mantener {wins}/{len(rows)} años, "
            f"mediana {vals[len(vals) // 2]:+.1f} %, mejor {vals[-1]:+.1f} %, peor {vals[0]:+.1f} %"
        )

    print("")
    print("[lectura] Un año positivo en activo base es un año en que el bot acabo con mas monedas que")
    print("          quien no hizo nada. Compara la columna 'hold EUR' para ver en que regimen ocurre.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
