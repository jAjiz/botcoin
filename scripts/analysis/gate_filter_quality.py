"""Selecciona la puerta por su PROPIO objetivo: filtrar tendencia, no ganar dinero.

Read-only y temporal. Lee los CSV OHLCVT de Kraken directamente, sin base de datos.

Elegir una puerta por rentabilidad mezcla dos responsabilidades y elige entre 154 candidatas
con una metrica ruidosa -- que es exactamente como este estudio se equivoco tres veces. Cada
pieza de la estrategia tiene un objetivo propio y hay que clasificarla por el suyo. El de la
puerta es UNO: dejar fuera las subidas y dejar dentro el mayor tiempo posible de mercado sin
deriva, para que el trailing-stop trabaje donde puede. Cuanto rinda eso despues es
responsabilidad de `min_margin` y los stops, y se ataca por separado.

Ese cambio de criterio tiene tres consecuencias practicas:

  * **No hace falta el motor.** Solo precio y banderas: ni ATR, ni calendario de calibracion,
    ni comisiones, ni configs. Lo caro desaparece.
  * **Por lo mismo, se puede exigir consistencia sobre MUCHOS anos**, no sobre los tres que la
    simulacion permitia. Una puerta que filtra bien 2018-2025 es otra cosa que una que filtra
    bien 2024.
  * **La metrica no puede ser un escalar inventado, ni un conteo de frontera.** Hay un
    compromiso real: una puerta siempre cerrada deja fuera el 100 % de la subida y no deja
    nada que operar, y una siempre abierta admite el mercado entero. Contar anos en la
    frontera de Pareto no vale -- la puerta abierta al 100 % JAMAS esta dominada, porque nada
    tiene mas cobertura que ella, asi que el conteo premia justo a las que no filtran. Lo que
    se hace es fijar el compromiso: se agrupan las variantes por TRAMO DE COBERTURA y dentro
    de cada tramo se ordenan por la peor deriva admitida. Asi se lee "a la cobertura que
    quieras, esta es la que mejor filtra", sin pesos y sin grados de libertad.

Metricas por variante y ano:

  abierta     % de velas de 1 min con la puerta abierta. Es la materia prima del bot.
  dentro      rendimiento acumulado del precio mientras la puerta esta ABIERTA. El objetivo
              es ~0: ni subida que perjudique al trailing ni bajada regalada.
  fuera       rendimiento acumulado mientras esta CERRADA. Aqui es donde deben caer las
              tendencias.
  deriva      `dentro` anualizado por cobertura, para poder comparar puertas que abren
              tiempos muy distintos. Es la columna que se minimiza.
  ER dentro   mediana del ratio de eficiencia sobre ventanas de 7 dias de retornos horarios
              admitidos: 0 es un paseo sin direccion, 1 es una recta. Sobre el ano ENTERO esta
              medida no discrimina (sale ~0.01 para todo, porque el camino acumulado es
              enorme), asi que se mide por ventana y se toma la mediana.

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):
  PYTHONPATH=. python scripts/analysis/gate_filter_quality.py "C:/Dev/Kraken OHLCVT"
"""

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import execution_fidelity as ef
import gate_families_live as gfl

BARS_PER_YEAR = 365 * 1440


def metrics(is_open: np.ndarray, step: np.ndarray) -> dict:
    """Deriva y forma del mercado dentro y fuera de la puerta."""
    inside, outside = step[is_open], step[~is_open]
    n_in = len(inside)
    if n_in < 1000:
        return {"open": 100.0 * n_in / len(step), "in": 0.0, "out": 0.0, "drift": 0.0, "er": 1.0}
    # Retornos horarios de las velas abiertas: a 1 min el ER lo domina el ruido de microestructura.
    hourly = np.add.reduceat(inside, np.arange(0, n_in, 60))
    # ER por ventanas de una semana (168 h) y mediana: sobre el ano entero el camino acumulado
    # es tan grande que el cociente sale ~0.01 para cualquier variante y no distingue nada.
    ers = []
    for i in range(0, len(hourly) - 168, 168):
        win = hourly[i : i + 168]
        path = np.abs(win).sum()
        if path > 0:
            ers.append(abs(win.sum()) / path)
    return {
        "open": 100.0 * n_in / len(step),
        "in": 100.0 * (np.exp(inside.sum()) - 1.0),
        "out": 100.0 * (np.exp(outside.sum()) - 1.0),
        "drift": 100.0 * (np.exp(inside.sum() * BARS_PER_YEAR / n_in) - 1.0),
        "er": float(np.median(ers)) if ers else 1.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir")
    ap.add_argument("--pair", default="XBTEUR")
    ap.add_argument("--years", nargs="*", type=int, default=[2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025])
    ap.add_argument("--min-open", type=float, default=25.0, help="Cobertura minima para entrar en el ranking.")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--out", default=None, help="Vuelca las metricas por variante y ano a JSON.")
    args = ap.parse_args()

    print(f"[datos] {args.pair}   anos {args.years}   sin motor: solo precio y banderas")
    print(f"  criterio: dejar fuera la tendencia y abrir el maximo de tiempo; cobertura minima {args.min_open:.0f} %")

    t0 = int(pd.Timestamp(f"{min(args.years) - 1}-07-01").timestamp())
    t1 = int(pd.Timestamp(f"{max(args.years)}-12-31").timestamp()) + 86_399
    coarse = ef.coarse_frame(os.path.join(args.data_dir, f"{args.pair}_15.csv"), t0, t1, 15)
    days, dclose = ef.daily_closes_from(coarse)
    day = coarse["dtime"].dt.floor("D")
    dhigh = coarse.groupby(day)["high"].max().to_numpy(dtype=float)
    dlow = coarse.groupby(day)["low"].min().to_numpy(dtype=float)
    fine_all = ef.fine_frame(os.path.join(args.data_dir, f"{args.pair}_1.csv"), coarse, t0, t1, 1)
    print(f"  {len(coarse)} velas de 15m, {len(fine_all)} de 1m")

    per_year: dict[int, dict[str, dict]] = {}
    for year in args.years:
        lo = pd.Timestamp(f"{year}-01-01")
        hi = pd.Timestamp(f"{year + 1}-01-01")
        fine = fine_all[(fine_all["dtime"] >= lo) & (fine_all["dtime"] < hi)].reset_index(drop=True)
        if len(fine) < 100_000:
            print(f"  {year}: solo {len(fine)} velas de 1m, se omite")
            continue
        s = gfl.Series(fine, days, dclose, dhigh, dlow)
        step = np.diff(np.log(fine["close"].to_numpy(dtype=float)))
        hold = 100.0 * (np.exp(step.sum()) - 1.0)
        rows = {name: metrics(flags[1:], step) for name, flags in gfl.families(s).items()}
        rows["__hold__"] = metrics(np.ones(len(step), dtype=bool), step)
        per_year[year] = rows
        print(f"  {year}: hold {hold:+8.1f} %   {len(rows) - 1} variantes", flush=True)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({str(y): rows for y, rows in per_year.items()}, fh)
        print("")
        print(f"[json] {args.out}")

    years = sorted(per_year)
    names = [
        n for n in per_year[years[0]] if n != "__hold__" and all(per_year[y][n]["open"] >= args.min_open for y in years)
    ]
    print(f"\n[cobertura] {len(names)} variantes abren >= {args.min_open:.0f} % en TODOS los anos")

    worst = {n: max(abs(per_year[y][n]["drift"]) for y in years) for n in names}
    cover = {n: float(np.median([per_year[y][n]["open"] for y in years])) for n in names}

    print("\n[por tramo de cobertura] dentro de cada tramo, las mejores por PEOR deriva admitida")
    print("  El compromiso se elige aqui: mas cobertura da mas materia prima al bot y admite mas deriva.")
    bands = [(25, 40), (40, 55), (55, 70), (70, 85), (85, 101)]
    for lo, hi in bands:
        group = sorted([n for n in names if lo <= cover[n] < hi], key=lambda n: worst[n])
        print(f"\n  cobertura {lo}-{hi if hi <= 100 else 100} %   ({len(group)} variantes)")
        if not group:
            print("    (ninguna)")
            continue
        print(f"    {'detector':<26}{'peor deriva':>13}{'abierta med':>13}{'ER dentro':>11}   por ano")
        for n in group[: args.top]:
            per = "  ".join(f"{per_year[y][n]['drift']:+.0f}%" for y in years)
            er = float(np.median([per_year[y][n]["er"] for y in years]))
            print(f"    {n:<26}{worst[n]:>+12.1f}%{cover[n]:>12.1f}%{er:>11.3f}   {per}")

    print("\n[referencia] deriva del mercado completo, por ano: lo que admitiria una puerta siempre abierta")
    for y in years:
        print(f"  {y}: {per_year[y]['__hold__']['drift']:+8.1f} %")

    print("\n[lectura] 'peor deriva' es lo que la puerta admite en su ano malo, anualizado por cobertura,")
    print("          y hay que leerla contra la referencia de abajo. Una puerta util la mantiene cerca de")
    print("          cero en TODOS los anos SIN colapsar la cobertura. La rentabilidad no entra aqui:")
    print("          es responsabilidad del trailing-stop y se ataca despues, sobre las velas admitidas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
