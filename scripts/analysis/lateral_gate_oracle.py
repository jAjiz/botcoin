"""Mantener por defecto y operar SOLO en los tramos laterales: cual es el techo con etiquetas perfectas.

Read-only y temporal. Lee el CSV OHLCVT de 15 min de Kraken, sin base de datos.

Las tres puertas ya medidas tenian por defecto OPERAR: el oraculo de regimenes cambiaba de
config pero operaba siempre, y la puerta de rallies forzaba asignacion solo en las subidas.
Esta invierte el defecto: fuera de los tramos laterales el bot mantiene el activo, que en
activo base vale exactamente 0 % por construccion, y solo opera dentro de ellos. Eso no se ha
corrido nunca, y ademas hace que el bot entre en cada lateral SOSTENIENDO el activo, que es el
lado correcto para cosechar un rango (el oraculo de regimenes fracaso justamente porque el
lado que se sostiene al empezar un tramo lo decide).

La puerta se implementa con `force_hold_bars` sobre todas las velas NO laterales. Se miden
cuatro brazos, y los dos ultimos atacan cosas distintas:

  sin puerta        el bot opera todo el año (la referencia de 105 configs)
  ancla viva        la puerta de siempre: al levantarse, el stop sigue anclado a maximos
                    trailados DENTRO de la mascara. Es lo que costo -21.4 en la puerta de
                    rallies ("congelada cuatro meses, fuera de posicion").
  reinicio          `reset_on_unmask`: al levantar la mascara la pata se reabre en esa vela,
                    asi que ningun ancla precede a la puerta.
  calib. local      ademas, la calibracion dentro de un lateral ve SOLO la historia desde el
                    inicio de ese lateral, no todo el historico. La hipotesis del dueño: los
                    datos del impulso previo pueden no aportar nada, o dañar, a la calibracion
                    de un rango. Un nivel sin eventos suficientes cae al punto global.

Etiquetado por impulsos de `regime_switch_oracle` (M=10 %, K=7 d, D=7 d), fijado antes de
rankear nada. Puntuacion en ACTIVO BASE sobre la ventana completa, mantener = 0 %.

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):

    PYTHONPATH=. python scripts/analysis/lateral_gate_oracle.py "C:/Dev/Kraken OHLCVT/XBTEUR_15.csv"
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
import regime_switch_oracle as rso

import trading.optimizer.search as optimizer
from core.config import ATR_DESV_LIMIT, RECALIBRATION_BARS
from core.config import VOLATILITY_LEVELS as LEVELS
from trading.engine import mark_to_market, simulate_operations
from trading.market_analyzer import (
    CalibrationInputs,
    analyze_structural_noise,
    atr_ratio_percentiles,
    k_values_by_level,
)

MIN_LOCAL_BARS = 192  # dos dias de velas de 15 min antes de fiarse de una calibracion local


def _in_force(points: tuple[CalibrationInputs, ...], at: int) -> CalibrationInputs:
    """El punto global vigente en la vela ``at``."""
    prior = [p for p in points if p.at <= at]
    return prior[-1] if prior else points[0]


def _filled(local: dict, base: dict) -> tuple[dict, int]:
    """Rellena con el punto global los niveles sin eventos suficientes en la ventana local."""
    out, missing = {}, 0
    for lvl in LEVELS:
        k = local.get(lvl)
        if k is None or len(k) == 0:
            out[lvl] = base[lvl]
            missing += 1
        else:
            out[lvl] = k
    return out, missing


def local_points(df, segs, recalib_bars: int, global_pts: tuple) -> tuple[tuple, int, int]:
    """Calendario cuyos puntos dentro de un lateral ven solo la historia desde su inicio."""
    out = list(global_pts)
    built = fallbacks = 0
    for seg in segs:
        if seg.label != "lateral":
            continue
        for at in range(seg.first_bar, seg.last_bar + 1, recalib_bars):
            if at - seg.first_bar < MIN_LOCAL_BARS:
                continue
            window = df.iloc[seg.first_bar : at + 1]
            up, down = analyze_structural_noise(window)
            base = _in_force(global_pts, at)
            up_k, miss_up = _filled(k_values_by_level(up), base.up_k)
            down_k, miss_down = _filled(k_values_by_level(down), base.down_k)
            out = [p for p in out if p.at != at]
            out.append(CalibrationInputs(at, atr_ratio_percentiles(window), up_k, down_k))
            built += 1
            fallbacks += miss_up + miss_down
    return tuple(sorted(out, key=lambda p: p.at)), built, fallbacks


def evaluate(ctx, cand, points: tuple, overrides: dict, hold: float, final: float, segs, lat: list[int]) -> dict:
    cfg = optimizer._build_engine_config(
        gsh.PAIR, cand, ctx.atr_ratio_thresholds, ctx.up_k, ctx.down_k, ATR_DESV_LIMIT, points
    )
    cfg = dataclasses.replace(cfg, **overrides)
    ops = simulate_operations(ctx.df, cfg, fee_rate=gsh.FEE / 100.0)
    eur = mark_to_market(ops, final) if ops else 0.0
    per_eur = rgo._period_returns(ops, rso._bounds(ctx.df, segs)) if ops else [0.0] * len(segs)
    per_seg = [gsh._btc(per_eur[i], segs[i].hold_pct) for i in lat]
    return {
        "cand": cand,
        "base": gsh._btc(eur, hold),
        "ops": sum(1 for op in ops if op.idx != 1),
        "cash": cd.time_in_cash(ops, ctx.df) if ops else 0.0,
        "per_seg": per_seg,
    }


def selection_test(results: dict[str, list[dict]], n_lat: int, track: list[str] = ()) -> None:
    """¿Sirve de algo ELEGIR la mejor config, o la eleccion no sobrevive al siguiente rango?

    La mediana solo es el estimador honesto si elegir no lleva informacion. Esto lo mide en vez
    de asumirlo: primero cuantos de los tramos laterales gana la config mas consistente, y
    despues el test que decide -- se elige la mejor sobre la primera mitad de los rangos y se
    mira en que percentil de las 105 cae sobre la segunda. Percentil 50 es azar.
    """
    cut = n_lat // 2
    print("")
    print(f"[eleccion] {n_lat} tramos laterales, ajuste en los {cut} primeros y prueba en los {n_lat - cut} ultimos")
    print(
        f"  {'brazo':<32} {'mejor consistencia':>19} {'elegida: ajuste':>16} {'prueba':>9} "
        f"{'percentil':>10} {'mejor posible':>14}"
    )
    named = []
    for arm, rows in results.items():
        wins = [sum(1 for v in r["per_seg"] if v > 0) for r in rows]
        fit = [rso._compound(r["per_seg"][:cut]) for r in rows]
        test = [rso._compound(r["per_seg"][cut:]) for r in rows]
        pick = max(range(len(rows)), key=lambda i: fit[i])
        steady = max(range(len(rows)), key=lambda i: (wins[i], test[i]))
        ranked = sorted(test)
        pct = 100.0 * sum(1 for v in ranked if v < test[pick]) / len(ranked)
        print(
            f"  {arm:<32} {max(wins):>13}/{n_lat:<5} {fit[pick]:>+15.1f}% {test[pick]:>+8.1f}% "
            f"{pct:>9.0f}% {max(test):>+13.1f}%"
        )
        named.append((arm, rows[pick], wins[pick], rows[steady], wins[steady]))

    print("")
    print("[tasa base] cuantos tramos gana una config CUALQUIERA: sin esto, un 9/9 no se puede interpretar")
    print(f"  {'brazo':<32} {'mediana':>9} {'p90':>7} {'maximo':>8} {'configs 9/9':>13}")
    for arm, rows in results.items():
        wins = sorted(sum(1 for v in r["per_seg"] if v > 0) for r in rows)
        n = len(wins)
        print(
            f"  {arm:<32} {wins[n // 2]:>6}/{n_lat:<2} {wins[int(0.9 * n)]:>4}/{n_lat:<2} "
            f"{wins[-1]:>5}/{n_lat:<2} {sum(1 for w in wins if w == n_lat):>9}/{n}"
        )

    if track:
        print("")
        print("[seguimiento] configs fijadas fuera de esta ventana y evaluadas aqui SIN volver a elegir")
        for arm, rows in results.items():
            print(f"  {arm}")
            print(f"    {'config':<20} {'gana':>7} {'compuesto':>11} {'mediana/tramo':>15} {'ops':>6}")
            for sig in track:
                hit = [r for r in rows if gsh._signature(r["cand"]) == sig]
                if not hit:
                    continue
                r = hit[0]
                per = sorted(r["per_seg"])
                print(
                    f"    {sig:<20} {sum(1 for v in per if v > 0):>4}/{n_lat:<2} "
                    f"{rso._compound(r['per_seg']):>+10.1f}% {per[len(per) // 2]:>+14.2f}% {r['ops']:>6}"
                )

    print("")
    print("[quien es]  la elegida por el ajuste y la mas consistente NO tienen por que coincidir")
    print(f"  {'brazo':<32} {'elegida por el ajuste':<28} {'gana':>6}   {'la mas consistente':<22} {'gana':>6}")
    for arm, picked, pw, steady, sw in named:
        print(
            f"  {arm:<32} {gsh._signature(picked['cand']):<22} {picked['ops']:>3} ops {pw:>4}/{n_lat:<2} "
            f"  {gsh._signature(steady['cand']):<16} {steady['ops']:>3} ops {sw:>4}/{n_lat}"
        )


def report(results: dict[str, list[dict]]) -> None:
    print("")
    print("[resultado] activo base sobre el año, mantener = 0 %")
    print(
        f"  {'brazo':<32} {'mediana':>8} {'p25':>7} {'p75':>7} {'mejor':>7} {'peor':>8} "
        f"{'bate':>9} {'ops':>6} {'caja':>6}"
    )
    for arm, rows in results.items():
        vals = sorted(r["base"] for r in rows)
        n = len(vals)
        print(
            f"  {arm:<32} {vals[n // 2]:>+7.1f}% {vals[n // 4]:>+6.1f}% {vals[3 * n // 4]:>+6.1f}% "
            f"{vals[-1]:>+6.1f}% {vals[0]:>+7.1f}% {sum(1 for v in vals if v > 0):>4}/{n:<4} "
            f"{sum(r['ops'] for r in rows) / n:>6.1f} {100 * sum(r['cash'] for r in rows) / n:>5.0f}%"
        )
    print("")
    print("[mejor config de cada brazo]  responde tambien a la pregunta sobre los stops")
    for arm, rows in results.items():
        b = max(rows, key=lambda r: r["base"])
        print(f"  {arm:<32} {b['base']:>+7.1f}%  {gsh._signature(b['cand']):<20} {b['ops']:>4} ops")


def decompose(ctx, arms: dict, results: dict, segs, lifts: set[int]) -> None:
    """De donde sale el dinero de la mejor config de cada brazo, y cuanto de el nace en el borde.

    Un brazo con puerta solo puede ganar dentro de los laterales. Si su acumulacion aparece en
    los tramos que la puerta tenia CERRADA, o sus ventas se agolpan en las primeras velas tras
    abrirla, lo que se esta midiendo no es la cosecha del rango: es la etiqueta filtrandose --
    el stop siguio trailando bajo la mascara y sale vendiendo en el techo de un impulso que el
    oraculo acaba de declarar terminado.
    """
    bar_of = {str(t): i for i, t in enumerate(ctx.df["dtime"].tolist())}
    ordered = sorted(lifts)
    print("")
    print("[descomposicion] acumulacion de base por clase de tramo, en la mejor config de cada brazo")
    print(f"  {'brazo':<32} {'lateral':>9} {'alcista':>9} {'bajista':>9} {'ventas <1d tras abrir':>23}")
    for arm, (overrides, points) in arms.items():
        best = max(results[arm], key=lambda r: r["base"])
        cfg = optimizer._build_engine_config(
            gsh.PAIR, best["cand"], ctx.atr_ratio_thresholds, ctx.up_k, ctx.down_k, ATR_DESV_LIMIT, points
        )
        cfg = dataclasses.replace(cfg, **overrides)
        ops = simulate_operations(ctx.df, cfg, fee_rate=gsh.FEE / 100.0)
        per_eur = rgo._period_returns(ops, rso._bounds(ctx.df, segs))
        by_class: dict[str, list[float]] = {}
        for seg, eur in zip(segs, per_eur, strict=True):
            by_class.setdefault(seg.label, []).append(gsh._btc(eur, seg.hold_pct))
        sells = [op for op in ops if op.side == "sell" and op.idx != 1]
        near = 0
        for op in sells:
            bar = bar_of.get(str(op.time))
            if bar is None:
                continue
            prior = [i for i in ordered if i <= bar]
            if prior and bar - prior[-1] <= 96:  # 96 velas de 15 min = un dia
                near += 1
        share = f"{near}/{len(sells)}" if sells else "-"
        print(
            f"  {arm:<32} {rso._compound(by_class.get('lateral', [])):>+8.1f}% "
            f"{rso._compound(by_class.get('alcista', [])):>+8.1f}% "
            f"{rso._compound(by_class.get('bajista', [])):>+8.1f}% {share:>23}"
        )


def cross_window(per_window: list[tuple[str, dict[str, list[dict]], int]], top: int) -> None:
    """Que configuraciones ganan tramos laterales de forma consistente en TODAS las ventanas.

    El criterio es tramos ganados, no compuesto: el compuesto es una suma que domina el tramo
    mas grande, y en 2024 eso eligio una config de 4/9 habiendo una de 7/9 en la misma rejilla.
    Se ordena por tramos ganados sobre el total de ventanas y se desempata por la mediana de la
    acumulacion por tramo, que tambien es una tasa y no una suma.
    """
    arms = per_window[0][1].keys()
    total_segs = sum(n for _, _, n in per_window)
    for arm in arms:
        rows = []
        for i, cand in enumerate(gsh.candidates()):
            per = [w[arm][i]["per_seg"] for _, w, _ in per_window]
            flat = [v for chunk in per for v in chunk]
            wins = [sum(1 for v in chunk if v > 0) for chunk in per]
            ordered = sorted(flat)
            rows.append(
                {
                    "sig": gsh._signature(cand),
                    "wins": sum(wins),
                    "per_win": wins,
                    "median": ordered[len(ordered) // 2] if ordered else 0.0,
                    "per_comp": [rso._compound(chunk) for chunk in per],
                    "ops": [w[arm][i]["ops"] for _, w, _ in per_window],
                }
            )
        rows.sort(key=lambda r: (r["wins"], r["median"]), reverse=True)
        print("")
        print(f"[consistencia entre ventanas]  {arm}")
        head = f"  {'config':<20} {'gana':>7}"
        for label, _, n in per_window:
            head += f" {label + ' (/' + str(n) + ')':>12}"
        head += f" {'mediana/tramo':>14}"
        for label, _, _ in per_window:
            head += f" {'comp ' + label:>12}"
        head += f" {'ops':>10}"
        print(head)
        for r in rows[:top]:
            line = f"  {r['sig']:<20} {r['wins']:>4}/{total_segs:<2}"
            for w in r["per_win"]:
                line += f" {w:>12}"
            line += f" {r['median']:>+13.2f}%"
            for c in r["per_comp"]:
                line += f" {c:>+11.1f}%"
            line += f" {'/'.join(str(o) for o in r['ops']):>10}"
            print(line)


def run_window(args, start: str, end: str) -> tuple[dict[str, list[dict]], int]:
    """Un año completo: marco, calibracion, tramos, y las 105 configs en los cuatro brazos."""
    cal_start = (pd.Timestamp(start) - pd.DateOffset(months=6)).strftime("%Y-%m-%d")
    cal_t0 = int(pd.Timestamp(cal_start).timestamp())
    t1 = int(pd.Timestamp(end).timestamp()) + 86_399
    frame = rgo.build_frame(args.csv, cal_t0, t1)
    gsh._install_ohlc(frame)
    print("")
    print(f"[calibracion] calendario global cada {args.recalib_bars} velas (tarda minutos)", flush=True)
    gsh._install_calibration_cache(frame, args.recalib_bars)

    first_bar = int((frame["dtime"] >= pd.Timestamp(start)).idxmax())
    ctx = gsh._context(frame, first_bar, len(frame) - 1, gsh._dtime(frame, len(frame) - 1))
    hold = (float(ctx.df.iloc[-1]["close"]) / float(ctx.df.iloc[0]["close"]) - 1.0) * 100.0
    final = float(ctx.df.iloc[-1]["close"])
    print("")
    print(f"[ventana] {gsh._dtime(ctx.df, 0)[:10]}..{gsh._dtime(ctx.df, len(ctx.df) - 1)[:10]}  hold {hold:+.2f} %")

    segs = rso.segments(ctx.df, args.move, args.max_days, args.min_days)
    rso.print_segments(ctx.df, segs)
    lateral = set()
    for seg in segs:
        if seg.label == "lateral":
            lateral.update(range(seg.first_bar, seg.last_bar + 1))
    mask = frozenset(i for i in range(len(ctx.df)) if i not in lateral)
    print("")
    print(f"  puerta abierta en {len(lateral)} de {len(ctx.df)} velas ({100 * len(lateral) / len(ctx.df):.0f} %)")

    print("")
    print("[calibracion local] puntos que solo ven su propio lateral", flush=True)
    t0 = time.perf_counter()
    loc, built, fallbacks = local_points(ctx.df, segs, args.recalib_bars, ctx.calibration_points)
    print(f"  {built} puntos locales, {fallbacks} niveles caidos al global ({time.perf_counter() - t0:.0f}s)")

    arms = {
        "sin puerta": ({}, ctx.calibration_points),
        "puerta, ancla viva": ({"force_hold_bars": mask}, ctx.calibration_points),
        "puerta, reinicio": ({"force_hold_bars": mask, "reset_on_unmask": True}, ctx.calibration_points),
        "puerta, reinicio + calib. local": ({"force_hold_bars": mask, "reset_on_unmask": True}, loc),
    }
    lat_idx = [i for i, seg in enumerate(segs) if seg.label == "lateral"]
    cands = gsh.candidates()
    print("")
    print(f"[barrido] {len(cands)} configs x {len(arms)} brazos", flush=True)
    results = {}
    for arm, (overrides, points) in arms.items():
        t0 = time.perf_counter()
        results[arm] = [evaluate(ctx, c, points, overrides, hold, final, segs, lat_idx) for c in cands]
        print(f"  {arm:<32} ({time.perf_counter() - t0:.0f}s)", flush=True)
    report(results)
    decompose(ctx, arms, results, segs, {s.first_bar for s in segs if s.label == "lateral"})
    selection_test(results, len(lat_idx), args.track)
    return results, len(lat_idx)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--years", nargs="*", type=int, default=[2024, 2025])
    ap.add_argument("--move", type=float, default=0.10)
    ap.add_argument("--max-days", type=int, default=7)
    ap.add_argument("--min-days", type=int, default=7)
    ap.add_argument("--recalib-bars", type=int, default=RECALIBRATION_BARS)
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument(
        "--track", nargs="*", default=[], help="Firmas a seguir en cada ventana, p. ej. 'mm=0.020 stop=0.9'."
    )
    args = ap.parse_args()

    print(f"[datos] {args.csv}")
    per_window = []
    for year in args.years:
        print("")
        print(f"================ {year} ================", flush=True)
        results, n_lat = run_window(args, f"{year}-01-01", f"{year}-12-31")
        per_window.append((str(year), results, n_lat))

    if len(per_window) > 1:
        cross_window(per_window, args.top)

    print("")
    print("[lectura] La puerta solo puede ganar si el bot rentabiliza los laterales: fuera de ellos")
    print("          mantiene, y mantener es 0 % por construccion. En la tabla entre ventanas, una")
    print("          config buena de verdad gana tramos en LAS DOS y no solo en la que mas aporta.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
