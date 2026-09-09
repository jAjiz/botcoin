"""Detectores CAUSALES de lateralidad: cuanto del techo oracular sobrevive sin mirar al futuro.

Read-only y temporal. Lee el CSV OHLCVT de 15 min de Kraken, sin base de datos.

`lateral_gate_oracle.py` establecio el techo: con etiquetas perfectas, mantener por defecto y
operar solo en los rangos lleva al bot de 0/105 batiendo a mantener a 54/105 (2023) y 38/105
(2024), y una region de la rejilla (`mm` 0.02-0.03 con stop ancho) transfiere a un año
retenido. Todo eso usa etiquetas que miran siete dias hacia adelante. Aqui se sustituye el
oraculo por reglas que solo ven el pasado, con la configuracion YA FIJADA fuera de este
experimento (`mm=0.020 stop=0.9`), asi que lo unico que se esta eligiendo es el detector.

Tres familias, todas con el mismo post-filtro de confirmacion: la puerta CIERRA en cuanto la
condicion falla y ABRE solo tras `delay` dias consecutivos cumpliendola. Esa asimetria es
deliberada -- perder un tramo lateral cuesta cero, porque fuera de la puerta se mantiene y
mantener es 0 % en activo base, mientras que abrir en una tendencia si cuesta.

  impulso   el espejo causal del oraculo: cerrado si |cierre_t / cierre_{t-k} - 1| >= move
            para algun k <= look. Es la referencia obligatoria, porque la distancia contra el
            oraculo mide exactamente lo que valia mirar hacia adelante. Su defecto conocido es
            que llega tarde: reconoce el impulso cuando el movimiento ya ocurrio.
  caja      el rango de los ultimos `n` dias cabe en una caja de `span`. Como regla de
            ETIQUETADO esto ya fallo una vez (absorbio los impulsos, 97 % lateral), asi que su
            reparto de dias se imprime antes que ningun resultado.
  er        ratio de eficiencia de Kaufman sobre `n` dias por debajo de un umbral. Disenado
            para esta tarea exacta. Nota: el cribado de señales encontro nulo el ER *para
            predecir* el retorno futuro; clasificar el regimen presente es otra tarea.

Filtrar por volatilidad NO esta aqui a proposito: ya se midio que los tramos de ATR alto no
son mas direccionales que los de ATR bajo.

PROTOCOLO, fijado antes de correr: la rejilla de abajo es la que hay, el detector se elige por
2024+2025 y 2023 no participa en la eleccion. Se reporta ademas la aportacion por clase de
tramo del oraculo, que responde a si la configuracion tambien funciona en bajadas.

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):

    PYTHONPATH=. python scripts/analysis/lateral_detector.py "C:/Dev/Kraken OHLCVT/XBTEUR_15.csv"
"""

import argparse
import dataclasses
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import grid_sweep_holdout as gsh
import lateral_gate_oracle as lgo
import rally_gate_oracle as rgo
import regime_switch_oracle as rso

import trading.optimizer.search as optimizer
from core.config import ATR_DESV_LIMIT, RECALIBRATION_BARS
from core.config import VOLATILITY_LEVELS as LEVELS
from trading.engine import mark_to_market, simulate_operations
from trading.optimizer.search import Candidate

CONFIG = Candidate(k_act=None, min_margin=0.020, stop_pcts=dict.fromkeys(LEVELS, 0.9))

IMPULSE = [(m, k, d) for m in (0.07, 0.10, 0.15) for k in (5, 7, 10) for d in (0, 3, 7)]
BOX = [(n, s, d) for n in (10, 20, 30) for s in (0.06, 0.10, 0.15) for d in (0, 3)]
ER = [(n, t, d) for n in (10, 20, 30) for t in (0.2, 0.3, 0.4) for d in (0, 3)]


def _confirm(raw: list[bool], delay: int) -> list[bool]:
    """Cierra en cuanto ``raw`` falla; abre solo tras ``delay`` dias consecutivos cumpliendo."""
    out, run = [], 0
    for ok in raw:
        run = run + 1 if ok else 0
        out.append(ok and run > delay)
    return out


def det_impulse(closes: list[float], move: float, look: int, delay: int) -> list[bool]:
    raw = []
    for i in range(len(closes)):
        back = closes[max(0, i - look) : i]
        raw.append(not any(abs(closes[i] / c - 1.0) >= move for c in back))
    return _confirm(raw, delay)


def det_box(closes: list[float], n: int, span: float, delay: int) -> list[bool]:
    raw = []
    for i in range(len(closes)):
        win = closes[max(0, i - n + 1) : i + 1]
        lo, hi = min(win), max(win)
        raw.append(len(win) >= n and (hi - lo) / ((hi + lo) / 2.0) <= span)
    return _confirm(raw, delay)


def det_er(closes: list[float], n: int, thresh: float, delay: int) -> list[bool]:
    raw = []
    for i in range(len(closes)):
        win = closes[max(0, i - n) : i + 1]
        path = sum(abs(win[j] - win[j - 1]) for j in range(1, len(win)))
        er = abs(win[-1] - win[0]) / path if path > 0 else 1.0
        raw.append(len(win) > n and er <= thresh)
    return _confirm(raw, delay)


def segments_from(days: pd.DataFrame, open_flags: list[bool], df: pd.DataFrame) -> list[rso.Segment]:
    """Tramos abiertos por el detector, en el mismo tipo que usa la calibracion local."""
    out, i = [], 0
    while i < len(open_flags):
        if not open_flags[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(open_flags) and open_flags[j + 1]:
            j += 1
        first_bar = int(days.iloc[i]["first_bar"])
        last_bar = int(days.iloc[j]["last_bar"])
        ref = float(df.iloc[first_bar - 1]["close"]) if first_bar > 0 else float(df.iloc[first_bar]["close"])
        hold = (float(df.iloc[last_bar]["close"]) / ref - 1.0) * 100.0
        out.append(rso.Segment("lateral", first_bar, last_bar, hold, j - i + 1))
        i = j + 1
    return out


def mask_of(segs: list[rso.Segment], n_bars: int) -> frozenset[int]:
    openb = set()
    for s in segs:
        openb.update(range(s.first_bar, s.last_bar + 1))
    return frozenset(i for i in range(n_bars) if i not in openb)


def run_config(ctx, points, mask, hold: float, final: float) -> dict:
    cfg = optimizer._build_engine_config(
        gsh.PAIR, CONFIG, ctx.atr_ratio_thresholds, ctx.up_k, ctx.down_k, ATR_DESV_LIMIT, points
    )
    cfg = dataclasses.replace(cfg, force_hold_bars=mask, reset_on_unmask=True)
    ops = simulate_operations(ctx.df, cfg, fee_rate=gsh.FEE / 100.0)
    eur = mark_to_market(ops, final) if ops else 0.0
    return {"base": gsh._btc(eur, hold), "ops": sum(1 for op in ops if op.idx != 1), "ops_list": ops}


def agreement(det_open: set[int], oracle: list[rso.Segment], n_bars: int) -> tuple[float, float, float, float]:
    """Recall sobre laterales, precision, y como se reparten los falsos positivos por direccion."""
    lat = set()
    rise, fall = set(), set()
    for s in oracle:
        rng = range(s.first_bar, s.last_bar + 1)
        if s.label == "lateral":
            lat.update(rng)
        elif s.label == "alcista":
            rise.update(rng)
        elif s.label == "bajista":
            fall.update(rng)
    hit = len(det_open & lat)
    recall = 100.0 * hit / len(lat) if lat else 0.0
    prec = 100.0 * hit / len(det_open) if det_open else 0.0
    fp_rise = 100.0 * len(det_open & rise) / len(det_open) if det_open else 0.0
    fp_fall = 100.0 * len(det_open & fall) / len(det_open) if det_open else 0.0
    return recall, prec, fp_rise, fp_fall


def build(args, year: int) -> dict:
    start, end = f"{year}-01-01", f"{year}-12-31"
    cal_start = (pd.Timestamp(start) - pd.DateOffset(months=6)).strftime("%Y-%m-%d")
    t0 = int(pd.Timestamp(cal_start).timestamp())
    t1 = int(pd.Timestamp(end).timestamp()) + 86_399
    frame = rgo.build_frame(args.csv, t0, t1)
    gsh._install_ohlc(frame)
    print(f"[calibracion] {year}, calendario global cada {args.recalib_bars} velas", flush=True)
    gsh._install_calibration_cache(frame, args.recalib_bars)
    first_bar = int((frame["dtime"] >= pd.Timestamp(start)).idxmax())
    ctx = gsh._context(frame, first_bar, len(frame) - 1, gsh._dtime(frame, len(frame) - 1))
    hold = (float(ctx.df.iloc[-1]["close"]) / float(ctx.df.iloc[0]["close"]) - 1.0) * 100.0
    days = rso.daily_closes(ctx.df)
    oracle = rso.segments(ctx.df, 0.10, 7, 7)
    return {
        "year": year,
        "ctx": ctx,
        "hold": hold,
        "final": float(ctx.df.iloc[-1]["close"]),
        "days": days,
        "closes": days["close"].astype(float).tolist(),
        "oracle": oracle,
    }


def detectors(closes: list[float]) -> dict[str, list[bool]]:
    out = {"oraculo": []}
    for m, k, d in IMPULSE:
        out[f"impulso m={m:.2f} k={k} d={d}"] = det_impulse(closes, m, k, d)
    for n, sp, d in BOX:
        out[f"caja n={n} s={sp:.2f} d={d}"] = det_box(closes, n, sp, d)
    for n, t, d in ER:
        out[f"er n={n} t={t:.1f} d={d}"] = det_er(closes, n, t, d)
    del out["oraculo"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--years", nargs="*", type=int, default=[2024, 2025, 2023])
    ap.add_argument("--recalib-bars", type=int, default=RECALIBRATION_BARS)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--local-top", type=int, default=0, help="Reevalua los N mejores con calibracion local.")
    args = ap.parse_args()

    print(f"[datos] {args.csv}   config fija {gsh._signature(CONFIG)}")
    per_year = {}
    for year in args.years:
        print("")
        print(f"================ {year} ================", flush=True)
        w = build(args, year)
        print(f"  hold {w['hold']:+.2f} %  |  {len(w['closes'])} dias")

        # El techo: mascara oracular, con y sin calibracion local.
        olat = [s for s in w["oracle"] if s.label == "lateral"]
        omask = mask_of(olat, len(w["ctx"].df))
        loc, _, _ = lgo.local_points(w["ctx"].df, olat, args.recalib_bars, w["ctx"].calibration_points)
        ceiling = run_config(w["ctx"], loc, omask, w["hold"], w["final"])
        glob = run_config(w["ctx"], w["ctx"].calibration_points, omask, w["hold"], w["final"])
        print(f"  techo oracular: {ceiling['base']:+.1f} % (calib local)  {glob['base']:+.1f} % (calib global)")
        print("    los detectores usan calib GLOBAL, asi que su referencia es la segunda cifra")

        per_eur = rgo._period_returns(ceiling["ops_list"], rso._bounds(w["ctx"].df, w["oracle"]))
        by_class: dict[str, list[float]] = {}
        for seg, eur in zip(w["oracle"], per_eur, strict=True):
            by_class.setdefault(seg.label, []).append(gsh._btc(eur, seg.hold_pct))
        print(
            f"  por clase: lateral {rso._compound(by_class.get('lateral', [])):+.1f} %  "
            f"alcista {rso._compound(by_class.get('alcista', [])):+.1f} %  "
            f"bajista {rso._compound(by_class.get('bajista', [])):+.1f} %"
        )

        # Puerta abierta tambien en las bajadas: responde a si la MISMA config sirve cuando cae,
        # y cuantifica el fork de diseño (cerrar solo en subidas en vez de en toda tendencia).
        openfall = [s2 for s2 in w["oracle"] if s2.label in ("lateral", "bajista")]
        fmask = mask_of(openfall, len(w["ctx"].df))
        asym = run_config(w["ctx"], w["ctx"].calibration_points, fmask, w["hold"], w["final"])
        per_a = rgo._period_returns(asym["ops_list"], rso._bounds(w["ctx"].df, w["oracle"]))
        by_a: dict[str, list[float]] = {}
        for seg, eur in zip(w["oracle"], per_a, strict=True):
            by_a.setdefault(seg.label, []).append(gsh._btc(eur, seg.hold_pct))
        print(
            f"  abriendo tambien en bajistas: {asym['base']:+.1f} % ({asym['ops']} ops)  "
            f"de lo cual bajista {rso._compound(by_a.get('bajista', [])):+.1f} %"
        )

        rows = []
        t0 = time.perf_counter()
        for name, flags in detectors(w["closes"]).items():
            segs = segments_from(w["days"], flags, w["ctx"].df)
            if not segs:
                rows.append({"name": name, "base": 0.0, "ops": 0, "open": 0.0, "rec": 0.0, "pre": 0.0, "fpr": 0.0})
                continue
            mask = mask_of(segs, len(w["ctx"].df))
            res = run_config(w["ctx"], w["ctx"].calibration_points, mask, w["hold"], w["final"])
            openb = set(range(len(w["ctx"].df))) - set(mask)
            rec, pre, fpr, fpf = agreement(openb, w["oracle"], len(w["ctx"].df))
            rows.append(
                {
                    "name": name,
                    "base": res["base"],
                    "ops": res["ops"],
                    "open": 100.0 * len(openb) / len(w["ctx"].df),
                    "rec": rec,
                    "pre": pre,
                    "fpr": fpr,
                    "fpf": fpf,
                }
            )
        print(f"  {len(rows)} detectores ({time.perf_counter() - t0:.0f}s)", flush=True)
        per_year[year] = {
            "rows": {r["name"]: r for r in rows},
            "ceiling": ceiling["base"],
            "glob": glob["base"],
            "w": w,
        }

    fit = [y for y in args.years if y != 2023]
    names = list(per_year[args.years[0]]["rows"])
    ranked = sorted(names, key=lambda n: sum(per_year[y]["rows"][n]["base"] for y in fit), reverse=True)

    print("")
    print(f"[ranking] suma de activo base en {fit} (2023 NO participa)")
    head = f"  {'detector':<24}"
    for y in args.years:
        head += f" {y!s:>9}"
    head += f" {'abierta':>8} {'recall':>7} {'prec':>6} {'fp alc':>7} {'fp baj':>7} {'ops':>10}"
    print(head)
    for name in ranked[: args.top]:
        line = f"  {name:<24}"
        for y in args.years:
            line += f" {per_year[y]['rows'][name]['base']:>+8.1f}%"
        r = per_year[fit[0]]["rows"][name]
        line += (
            f" {r['open']:>7.0f}% {r['rec']:>6.0f}% {r['pre']:>5.0f}% {r['fpr']:>6.0f}% {r['fpf']:>6.0f}% "
            f"{'/'.join(str(per_year[y]['rows'][name]['ops']) for y in args.years):>10}"
        )
        print(line)

    print("")
    print("  " + "techo oracular:  " + "  ".join(f"{y}: {per_year[y]['ceiling']:+.1f}%" for y in args.years))

    if args.local_top:
        # El barrido corre con calibracion GLOBAL para ser tratable, pero el techo con calibracion
        # local es mucho mas alto en 2024 (+33.7 contra +9.7), asi que comparar detector-global
        # contra techo-local es injusto con el detector. Aqui se empareja: los mejores detectores
        # con la misma calibracion local que su techo.
        print("")
        print(f"[calibracion local] los {args.local_top} mejores detectores, emparejados con su techo", flush=True)
        head = f"  {'detector':<24}"
        for y in args.years:
            head += f" {y!s:>19}"
        print(head + "   (global -> local)")
        for name in ranked[: args.local_top]:
            line = f"  {name:<24}"
            for y in args.years:
                w = per_year[y]["w"]
                flags = detectors(w["closes"])[name]
                segs = segments_from(w["days"], flags, w["ctx"].df)
                if not segs:
                    line += f" {'-':>19}"
                    continue
                loc, _, _ = lgo.local_points(w["ctx"].df, segs, args.recalib_bars, w["ctx"].calibration_points)
                res = run_config(w["ctx"], loc, mask_of(segs, len(w["ctx"].df)), w["hold"], w["final"])
                line += f" {per_year[y]['rows'][name]['base']:>+8.1f}% ->{res['base']:>+7.1f}%"
            print(line, flush=True)
        print("")
        print("  " + "techo local:  " + "  ".join(f"{y}: {per_year[y]['ceiling']:+.1f}%" for y in args.years))
    print("")
    print("[lectura] 'abierta' es el % de velas con la puerta abierta y 'fp alc'/'fp baj' el reparto de los")
    print("          falsos positivos. Un falso positivo bajista NO es un error: sin puerta el bot gana en")
    print("          los tramos que caen. Comparar cada detector contra su techo oracular del mismo año.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
