"""Con la puerta ya fijada, barrer `min_margin` y `stop_pct` sobre las velas que admite.

Read-only y temporal. Lee los CSV OHLCVT de Kraken directamente, sin base de datos.

Division de responsabilidades, que es lo que hace utilizable este barrido. La puerta se eligio
por su PROPIO objetivo -- dejar la tendencia fuera y admitir el
maximo de mercado sin deriva, medido sobre ocho anos y sin tocar el motor -- y no se vuelve a
tocar aqui. Lo unico que se elige en este script es la configuracion del trailing-stop, sobre
las velas que la puerta ya decidio. Elegir las dos cosas a la vez con la misma metrica es como
este estudio se equivoco tres veces.

Dos puertas, y no son variantes de lo mismo: filtran mercados de signo opuesto, asi que su
configuracion optima no tiene por que parecerse.

  simetrica   `impulso m=0.07 k=7`. Cierra en impulsos de CUALQUIER signo. Admite el mercado
              mas plano que se ha medido: la deriva anualizada que deja entrar cabe en
              [-35 %, +35 %] los ocho anos, contra un mercado que va de -62 % a +274 %.
  alcista     cierra solo en impulsos ALCISTAS, asi que lo que queda dentro es un mercado que
              solo cae (deriva admitida de -70 % a -100 % anualizada, todos los anos). No es
              un detector de laterales sino de bajadas, y la pregunta abierta es si eso se
              cosecha: con `mm=0.020` medido, no; con una configuracion rapida el resultado de
              los ocho anos decia que si. Ese desacuerdo es lo que resuelve este barrido.

Se puntua en activo base contra mantener (0 % por construccion) y se ordena por el PEOR ano,
nunca por la mediana ni por el mejor: produccion corre una configuracion y no elige el ano.

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):
  PYTHONPATH=. python scripts/analysis/gate_config_sweep.py "C:/Dev/Kraken OHLCVT" --gate simetrica
"""

import argparse
import dataclasses
import json
import os
import statistics
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import execution_fidelity as ef
import gate_families_live as gfl

from core.config import ATR_DESV_LIMIT, RECALIBRATION_BARS
from trading.engine import EngineConfig, mark_to_market, simulate_operations

# La rejilla del estudio: 21 x 5 = 105 configuraciones.
MIN_MARGINS = tuple(round(0.01 * i, 3) for i in range(0, 21))
STOPS = (0.5, 0.6, 0.7, 0.8, 0.9)

GATES = {
    # Elegidas sobre 2018-2025 por calidad de filtro (deriva admitida contra cobertura) y nada mas.
    "simetrica": lambda s: gfl.det_impulse(s, 0.07, 7),
    # Deriva admitida <= -96 % los ocho anos: el mercado mas puramente bajista que se filtra,
    # a costa de abrir solo el 29 % del tiempo.
    "alcista_estricta": lambda s: gfl.det_off_high(s, 20, 0.10, 0),
    # Casi la misma deriva (-83 % en su peor ano) con el triple de cobertura (85 %). La pareja
    # con la anterior aisla si lo que hace falta es mercado puro o materia prima.
    "alcista_amplia": lambda s: gfl.det_no_new_high(s, 10, 0),
    # Elegidas en `gate_structure_screen.py` por lo contrario que las anteriores: por el EXCESO
    # de reversion sobre un control barajado, es decir por dejar pasar estructura en vez de
    # dejar pasar mercado plano. Pagan ese exceso admitiendo mucha mas deriva.
    "estructura": lambda s: gfl.det_er(s, 20, 0.3, 0),
    "estructura_max": lambda s: gfl.det_er(s, 10, 0.2, 0),
    "ninguna": lambda s: np.ones(len(s.price), dtype=bool),
}


def build(data_dir: str, pair: str, year: int, lead: int, recalib: int):
    cal_start = (pd.Timestamp(f"{year}-01-01") - pd.DateOffset(months=lead)).strftime("%Y-%m-%d")
    cal_t0 = int(pd.Timestamp(cal_start).timestamp())
    sim_t0 = int(pd.Timestamp(f"{year}-01-01").timestamp())
    t1 = int(pd.Timestamp(f"{year}-12-31").timestamp()) + 86_399

    coarse = ef.coarse_frame(os.path.join(data_dir, f"{pair}_15.csv"), cal_t0, t1, 15)
    points = ef.build_schedule(coarse, recalib)
    prior = [p for p in points if p["time"] <= sim_t0]
    points = ([prior[-1]] if prior else []) + [p for p in points if p["time"] > sim_t0]

    days, dclose = ef.daily_closes_from(coarse)
    day = coarse["dtime"].dt.floor("D")
    dhigh = coarse.groupby(day)["high"].max().to_numpy(dtype=float)
    dlow = coarse.groupby(day)["low"].min().to_numpy(dtype=float)
    fine = ef.fine_frame(os.path.join(data_dir, f"{pair}_1.csv"), coarse, sim_t0, t1, 1)
    return fine, points, gfl.Series(fine, days, dclose, dhigh, dlow)


def sweep(fine: pd.DataFrame, points: list, mask: frozenset[int], fee: float) -> list[dict]:
    """Las 105 configuraciones sobre una corrida continua, con la puerta ya aplicada."""
    times = fine["time"].to_numpy()
    scheduled = ef.remap(points, times)
    final_price = float(fine.iloc[-1]["close"])
    hold = (final_price / float(fine.iloc[0]["close"]) - 1.0) * 100.0

    out = []
    t0 = time.perf_counter()
    for stop in STOPS:
        cal = ef._calibration(scheduled[0][1], stop)
        schedule = tuple((at, ef._calibration(p, stop)) for at, p in scheduled)
        for mm in MIN_MARGINS:
            cfg = EngineConfig(
                pair="XBTEUR",
                calibration=cal,
                k_act=None,
                min_margin=mm,
                atr_desv_limit=ATR_DESV_LIMIT,
                calibration_schedule=schedule,
            )
            cfg = dataclasses.replace(cfg, force_hold_bars=mask, reset_on_unmask=True)
            ops = simulate_operations(fine, cfg, fee_rate=fee / 100.0)
            eur = mark_to_market(ops, final_price) if ops else 0.0
            out.append(
                {
                    "mm": mm,
                    "stop": stop,
                    "base": ((1.0 + eur / 100.0) / (1.0 + hold / 100.0) - 1.0) * 100.0,
                    "ops": len(ops),
                }
            )
        print(
            f"    stop={stop} listo ({len(out)}/{len(STOPS) * len(MIN_MARGINS)}, {time.perf_counter() - t0:.0f}s)",
            flush=True,
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir")
    ap.add_argument("--pair", default="XBTEUR")
    ap.add_argument("--gate", default="simetrica", help=f"Una de: {', '.join(GATES)}")
    ap.add_argument("--years", nargs="*", type=int, default=[2023, 2024, 2025])
    ap.add_argument("--lead-months", type=int, default=6)
    ap.add_argument("--recalib-bars", type=int, default=RECALIBRATION_BARS)
    ap.add_argument("--fee", type=float, default=ef.FEE)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if args.gate not in GATES:
        raise SystemExit(f"puerta desconocida: {args.gate}. Opciones: {', '.join(GATES)}")
    ef.FEE = args.fee

    print(f"[datos] {args.pair}   puerta '{args.gate}' FIJA (elegida por calidad de filtro, no por retorno)")
    print(f"  camino de 1 min, ATR de 15 min, comision {args.fee} %/pierna, {len(MIN_MARGINS)}x{len(STOPS)} configs")

    per_year: dict[int, dict[tuple[float, float], dict]] = {}
    for year in args.years:
        print(f"\n================ {year} ================", flush=True)
        fine, points, s = build(args.data_dir, args.pair, year, args.lead_months, args.recalib_bars)
        is_open = GATES[args.gate](s)
        mask = frozenset(np.flatnonzero(~is_open).tolist())
        hold = (float(fine.iloc[-1]["close"]) / float(fine.iloc[0]["close"]) - 1.0) * 100.0
        print(
            f"  {len(fine)} velas   mantener {hold:+.1f} % EUR   puerta abierta {100 * is_open.mean():.1f} %",
            flush=True,
        )
        per_year[year] = {(r["mm"], r["stop"]): r for r in sweep(fine, points, mask, args.fee)}
        del fine, s

    years = sorted(per_year)
    keys = list(per_year[years[0]])
    worst = {k: min(per_year[y][k]["base"] for y in years) for k in keys}
    med = {k: statistics.median([per_year[y][k]["base"] for y in years]) for k in keys}

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "gate": args.gate,
                    "years": years,
                    "rows": {f"{k[0]}|{k[1]}": {str(y): per_year[y][k] for y in years} for k in keys},
                },
                fh,
            )
        print("")
        print(f"[json] {args.out}")

    print(f"\n[ranking] puerta '{args.gate}', ordenado por PEOR ano (produccion no elige el ano)")
    head = f"  {'config':<20}" + "".join(f"{y:>10}" for y in years) + f"{'peor':>9}{'mediana':>10}{'ops med':>9}"
    print(head)
    for k in sorted(keys, key=lambda k: worst[k], reverse=True)[: args.top]:
        ops = statistics.median([per_year[y][k]["ops"] for y in years])
        line = f"  mm={k[0]:.3f} s={k[1]:.1f}      " + "".join(f"{per_year[y][k]['base']:>+9.1f}%" for y in years)
        print(line + f"{worst[k]:>+8.1f}%{med[k]:>+9.1f}%{ops:>9.0f}")

    pos = [k for k in keys if worst[k] > 0.0]
    print(f"\n  configuraciones positivas en TODOS los anos: {len(pos)}/{len(keys)}")
    for y in years:
        vals = [per_year[y][k]["base"] for k in keys]
        n_pos = sum(1 for v in vals if v > 0)
        print(
            f"    {y}: mediana {statistics.median(vals):+6.1f} %   positivas {n_pos:>3}/{len(keys)}   mejor {max(vals):+6.1f} %"
        )

    print("\n[superficie del peor ano] filas mm, columnas stop")
    print("    mm     " + "".join(f"{s:>9}" for s in STOPS))
    for mm in MIN_MARGINS:
        print(f"    {mm:.3f}  " + "".join(f"{worst[(mm, s)]:>9.1f}" for s in STOPS))

    print("\n[lectura] el liston es 0.0 % (mantener). Una region ancha de la superficie por encima de")
    print("          cero vale mas que un maximo aislado: lo segundo ya se ha demostrado tres veces que")
    print("          es un estadistico de orden. La puerta NO se re-elige a la vista de esto.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
