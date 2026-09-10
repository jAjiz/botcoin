"""Al reves: ¿QUE tiene que dejar entrar la puerta para que el bot gane, y existe eso?

Read-only y temporal. Lee los CSV OHLCVT de Kraken directamente, sin base de datos.

Hasta ahora se elegia una puerta y se buscaba una configuracion que la rentabilizara, y salio
0/105 cinco veces seguidas. Este script invierte el planteamiento, que es la pregunta del
propietario: en vez de elegir la puerta primero, se mide QUE PROPIEDADES tiene que tener el
mercado admitido para que el bot gane, y despues se comprueba si alguna puerta las entrega de
forma causal y repetida.

Dos pasos, y el segundo es el que decide:

  MAPA        Se corre el bot sobre lo que admiten muchas puertas distintas, elegidas para
              cubrir el rango entero de deriva y volatilidad admitidas -- no las buenas, TODAS,
              incluidas las que dejan pasar tendencia a mansalva. Cada celda (puerta, ano) da un
              punto: propiedades del mercado admitido -> resultado del bot en activo base. Con
              suficientes puntos, la region rentable, si existe, se ve.

  ENTREGA     Si el mapa dice "el bot gana cuando entra mercado con la propiedad X", queda la
              pregunta que de verdad importa: ¿hay alguna puerta que entregue X de forma
              CONSISTENTE ano tras ano? Entregarlo un ano es suerte; entregarlo ocho es un
              filtro. Y si la propiedad X resulta ser la deriva futura, no hay puerta que valga:
              eso es predecir, y el cribado de senales ya midio que no se puede.

Se corre a 15 min, no a 1 min. Esta medido en este documento que la mediana del delta entre
ambas resoluciones es +0.00 % sobre las 105 configuraciones y que la correlacion de rangos es
0.80-0.94, asi que a 15 min la CRIBA es fiel aunque una celda suelta se mueva; lo que salga se
verifica a 1 min antes de creerselo.

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):
  PYTHONPATH=. python scripts/analysis/gate_requirement.py "C:/Dev/Kraken OHLCVT"
"""

import argparse
import dataclasses
import json
import os
import statistics
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import execution_fidelity as ef
import gate_families_live as gfl
import gate_live_fidelity as glf
import lateral_horizon as lh
import lateral_market_structure as lms

from core.config import ATR_DESV_LIMIT, RECALIBRATION_BARS
from trading.engine import EngineConfig, mark_to_market, simulate_operations

BARS_PER_YEAR = 365 * (1440 // lh.BAR)

# Configuraciones que operan de verdad. Las de `min_margin` alto no operan y su resultado es
# -0.4 % en cualquier mercado, asi que no informan sobre que necesita el bot.
CONFIGS = ((0.0, 0.9), (0.01, 0.9), (0.02, 0.9), (0.03, 0.9))


def admitted(logp: np.ndarray, is_open: np.ndarray) -> dict:
    """Propiedades del mercado que una puerta deja entrar, todas anualizadas por cobertura."""
    step = np.concatenate(([0.0], np.diff(logp)))
    inside = step[is_open]
    n = len(inside)
    if n < 2000:
        return {}
    spans = [b - a for a, b in lms.stretches(is_open)]
    return {
        "open": 100.0 * float(is_open.mean()),
        "drift": 100.0 * (np.exp(inside.sum() * BARS_PER_YEAR / n) - 1.0),
        "vol": 100.0 * float(inside.std()) * np.sqrt(BARS_PER_YEAR),
        "span": float(np.median(spans)) * lh.BAR / 1440.0 if spans else 0.0,
    }


def bot_on(coarse: pd.DataFrame, scheduled: list, is_open: np.ndarray, fee: float) -> dict:
    """El bot sobre las velas admitidas, en activo base contra mantener, por configuracion."""
    mask = frozenset(np.flatnonzero(~is_open).tolist())
    close = coarse["close"].to_numpy(dtype=float)
    final_price = float(close[-1])
    hold = (final_price / float(close[0]) - 1.0) * 100.0
    out = {}
    for mm, stop in CONFIGS:
        cfg = EngineConfig(
            pair="XBTEUR",
            calibration=ef._calibration(scheduled[0][1], stop),
            k_act=None,
            min_margin=mm,
            atr_desv_limit=ATR_DESV_LIMIT,
            calibration_schedule=tuple((at, ef._calibration(p, stop)) for at, p in scheduled),
        )
        cfg = dataclasses.replace(cfg, force_hold_bars=mask, reset_on_unmask=True)
        ops = simulate_operations(coarse, cfg, fee_rate=fee)
        eur = mark_to_market(ops, final_price) if ops else 0.0
        out[(mm, stop)] = ((1.0 + eur / 100.0) / (1.0 + hold / 100.0) - 1.0) * 100.0
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir")
    ap.add_argument("--pair", default="XBTEUR")
    ap.add_argument("--years", nargs="*", type=int, default=list(range(2018, 2026)))
    ap.add_argument("--fee", type=float, default=0.4)
    ap.add_argument("--recalib-bars", type=int, default=RECALIBRATION_BARS)
    ap.add_argument("--min-open", type=float, default=10.0)
    ap.add_argument("--out", default=None, help="Vuelca los puntos por celda a JSON.")
    args = ap.parse_args()

    ef.FEE = args.fee
    fee = args.fee / 100.0
    print("[inversion] que necesita el bot que entre, medido sobre todas las puertas del catalogo")
    print(f"  velas de {lh.BAR} min, comision {args.fee} %/pierna, {len(CONFIGS)} configuraciones")

    points: list[dict] = []  # un punto por (puerta, ano, config)
    per_gate: dict[str, list[tuple[int, dict, dict]]] = {}
    for year in args.years:
        t0 = int(pd.Timestamp(f"{year}-01-01").timestamp())
        lead = t0 - 86_400 * 200
        end = int(pd.Timestamp(f"{year}-12-31").timestamp()) + 86_399
        coarse = ef.coarse_frame(os.path.join(args.data_dir, f"{args.pair}_{lh.BAR}.csv"), lead, end, lh.BAR)
        sched_pts = ef.build_schedule(coarse, args.recalib_bars)
        keep = coarse["time"].to_numpy() >= t0
        year_df = coarse[keep].reset_index(drop=True)
        prior = [p for p in sched_pts if p["time"] <= t0]
        sched_pts = ([prior[-1]] if prior else []) + [p for p in sched_pts if p["time"] > t0]
        scheduled = ef.remap(sched_pts, year_df["time"].to_numpy())

        days, dclose = glf.daily_closes_from(coarse)
        day = coarse["dtime"].dt.floor("D")
        s_full = gfl.Series(
            coarse,
            days,
            dclose,
            coarse.groupby(day)["high"].max().to_numpy(float),
            coarse.groupby(day)["low"].min().to_numpy(float),
        )
        logp = np.log(year_df["close"].to_numpy(dtype=float))
        for name, flags in gfl.families(s_full).items():
            is_open = flags[keep]
            props = admitted(logp, is_open)
            if not props or props["open"] < args.min_open:
                continue
            res = bot_on(year_df, scheduled, is_open, fee)
            per_gate.setdefault(name, []).append((year, props, res))
            for spec, val in res.items():
                points.append({"gate": name, "year": year, "spec": spec, **props, "bot": val})
        print(f"  {year} listo ({sum(1 for p in points if p['year'] == year)} puntos)", flush=True)
        del coarse, year_df, s_full

    print(f"\n  {len(points)} puntos (puerta x ano x config)")

    # --- PASO 1: donde gana el bot -----------------------------------------------------
    print("\n[MAPA] resultado del bot por deriva admitida del mercado que entra")
    edges = [(-(10**9), -60), (-60, -30), (-30, -10), (-10, 10), (10, 30), (30, 60), (60, 150), (150, 10**9)]
    print(f"  {'deriva admitida':>18}{'puntos':>8}{'mediana':>10}{'p90':>9}{'% > 0':>8}{'mejor':>9}")
    for lo, hi in edges:
        sel = [p["bot"] for p in points if lo <= p["drift"] < hi]
        if not sel:
            continue
        tag = f"{lo:+.0f}% a {hi:+.0f}%" if abs(lo) < 10**8 else f"< {hi:+.0f}%"
        if hi > 10**8:
            tag = f"> {lo:+.0f}%"
        share = 100.0 * sum(v > 0 for v in sel) / len(sel)
        print(
            f"  {tag:>18}{len(sel):>8}{statistics.median(sel):>9.1f}%"
            f"{np.percentile(sel, 90):>8.1f}%{share:>7.0f}%{max(sel):>8.1f}%"
        )

    print("\n[MAPA] lo mismo por volatilidad admitida, dentro de la banda de deriva plana (-30 a +30 %)")
    flat = [p for p in points if -30 <= p["drift"] < 30]
    vedges = [(0, 30), (30, 45), (45, 60), (60, 80), (80, 10**9)]
    print(f"  {'volatilidad':>18}{'puntos':>8}{'mediana':>10}{'p90':>9}{'% > 0':>8}{'mejor':>9}")
    for lo, hi in vedges:
        sel = [p["bot"] for p in flat if lo <= p["vol"] < hi]
        if not sel:
            continue
        tag = f"{lo:.0f}-{hi:.0f} %" if hi < 10**8 else f"> {lo:.0f} %"
        share = 100.0 * sum(v > 0 for v in sel) / len(sel)
        print(
            f"  {tag:>18}{len(sel):>8}{statistics.median(sel):>9.1f}%"
            f"{np.percentile(sel, 90):>8.1f}%{share:>7.0f}%{max(sel):>8.1f}%"
        )

    # --- PASO 2: se puede entregar de forma consistente ---------------------------------
    print("\n[ENTREGA] puertas por anos en que su mercado admitido cae en la region ganadora")
    win = [p for p in points if p["bot"] > 0]
    if win:
        lo_d, hi_d = np.percentile([p["drift"] for p in win], [10, 90])
        print(f"  region ganadora (10-90 % de los puntos positivos): deriva admitida {lo_d:+.0f} % a {hi_d:+.0f} %")
        print(f"  {'puerta':<26}{'anos en region':>16}{'deriva por ano':>40}")
        rows = []
        for name, cells in per_gate.items():
            if len(cells) < len(args.years):
                continue
            inside = sum(lo_d <= c[1]["drift"] <= hi_d for c in cells)
            rows.append((inside, name, [c[1]["drift"] for c in cells]))
        rows.sort(key=lambda t: -t[0])
        for inside, name, drifts in rows[:15]:
            per = " ".join(f"{d:+.0f}" for d in drifts)
            print(f"  {name:<26}{inside:>10}/{len(drifts):<5}{per:>40}")

    # --- PASO 3: la prueba estandar del estudio, por celda y no por agregado -----------
    print("\n[CRUCE] pares (puerta, config) positivos en TODOS los anos, no en el 29 % de los puntos")
    cells: dict[tuple, dict[int, float]] = {}
    for p in points:
        cells.setdefault((p["gate"], p["spec"]), {})[p["year"]] = p["bot"]
    full = {k: v for k, v in cells.items() if len(v) == len(args.years)}
    # Tasa base honesta: el producto de las tasas de acierto de cada ano, no una tasa global
    # elevada a ocho. El mapa ya muestra que el ano manda sobre la variante.
    rate = 1.0
    per_year_rate = []
    for y in args.years:
        vals = [p["bot"] for p in points if p["year"] == y]
        r = sum(v > 0 for v in vals) / len(vals) if vals else 0.0
        per_year_rate.append(r)
        rate *= r
    print(f"  {len(full)} pares con los {len(args.years)} anos completos")
    print(
        "  positivas por ano: "
        + "  ".join(f"{y}:{100 * r:.0f}%" for y, r in zip(args.years, per_year_rate, strict=True))
    )
    print(f"  esperado por azar si los anos fueran independientes: {rate * len(full):.2f} pares")
    ranked = sorted(full.items(), key=lambda kv: -min(kv[1].values()))
    best = [(k, v) for k, v in ranked if all(x > 0 for x in v.values())]
    print(f"  POSITIVOS EN LOS {len(args.years)}: {len(best)}")
    print("\n  los 12 mejores por PEOR ano:")
    print(f"  {'puerta':<26}{'config':>14}{'peor ano':>10}{'anos>0':>8}   por ano")
    for (gate, spec), v in ranked[:12]:
        per = " ".join(f"{v[y]:+.0f}" for y in args.years)
        wins = sum(x > 0 for x in v.values())
        print(f"  {gate:<26}{f'mm={spec[0]:.2f}/{spec[1]}':>14}{min(v.values()):>9.1f}%{wins:>5}/{len(v)}   {per}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump([{**p, "spec": list(p["spec"])} for p in points], fh)
        print(f"\n  {len(points)} puntos volcados en {args.out}")

    print("\n[lectura] el paso 1 dice que necesita el bot; el paso 2 dice si alguna puerta lo entrega")
    print("          los ocho anos. Entregarlo dos o tres es el mismo instrumento condicional de")
    print("          siempre: acierta cuando el mercado cae, y eso no lo elige la puerta.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
