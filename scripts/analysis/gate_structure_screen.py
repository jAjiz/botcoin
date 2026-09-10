"""¿Hay ALGUNA puerta que admita un mercado con estructura de verdad, no solo plano?

Read-only y temporal. Lee los CSV OHLCVT de Kraken directamente, sin base de datos.

La puerta se eligio por un objetivo -- dejar la tendencia fuera, admitir el maximo de mercado
sin deriva -- y con ella el mercado admitido salio paseo aleatorio. La pregunta razonable que
eso deja abierta es si el objetivo estaba mal: quiza exista otra puerta que deje pasar MAS
estructura, aunque admita mas deriva, y sobre esa si haya algo que cosechar.

Este script contesta esa pregunta y no otra, sobre las 154 variantes causales del catalogo, y
la contesta con un control, porque sin control la medida no significa nada:

  EXCESO      VR de Lo-MacKinlay dentro de los tramos que la variante admite, MENOS el VR de la
              misma variante aplicada al mismo camino con los retornos BARAJADOS. El termino de
              control no es opcional: una puerta admite el precio mientras cumple una condicion
              escrita sobre el precio, y condicionar asi trunca los recorridos largos y produce
              VR < 1 sobre material sin ninguna memoria. Lo unico que puede ser estructura del
              mercado es lo que sobra por encima del control.

  NEGOCIO     La esperanza BRUTA por operacion de la mejor de las dos apuestas ingenuas a
              horizonte h -- contraria o de continuacion, la que salga positiva -- sobre
              retornos no solapados dentro de tramos, contra la ida y vuelta real. Es la version
              en dinero del exceso: una variante puede tener estructura medible y no llegar a
              pagar las comisiones, y entonces no sirve.

Se ordena por el PEOR ano, nunca por la mediana ni por el mejor: produccion corre una variante
y no elige el ano. Y se imprime al lado la deriva admitida, para que se vea el precio del
cambio de objetivo: una puerta que deja pasar estructura dejando pasar tambien la tendencia no
es un filtro de laterales, es otra cosa.

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):
  PYTHONPATH=. python scripts/analysis/gate_structure_screen.py "C:/Dev/Kraken OHLCVT"
"""

import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gate_families_live as gfl
import lateral_horizon as lh

BARS_PER_YEAR = 365 * (1440 // lh.BAR)

# Horizontes donde el barrido de configuraciones deja al bot operar de verdad: de 4 h a 2 dias.
HORIZONS = (16, 48, lh.DAY, 2 * lh.DAY)


def series_for(coarse, close: np.ndarray) -> gfl.Series:
    """El objeto que los detectores consumen, construido sobre un camino cualquiera."""
    day = coarse["dtime"].dt.floor("D")
    frame = coarse.assign(close=close)
    days = day.drop_duplicates().to_numpy()
    dclose = frame.groupby(day)["close"].last().to_numpy(dtype=float)
    dhigh = frame.groupby(day)["close"].max().to_numpy(dtype=float)
    dlow = frame.groupby(day)["close"].min().to_numpy(dtype=float)
    return gfl.Series(frame, days, dclose, dhigh, dlow)


def profile(logp: np.ndarray, is_open: np.ndarray, trip: float) -> dict:
    """VR por horizonte, mejor apuesta ingenua y deriva admitida de una variante en un ano."""
    sid = lh.stretch_ids(is_open)
    step = np.concatenate(([0.0], np.diff(logp)))
    inside = step[is_open]
    out = {"open": 100.0 * float(is_open.mean()), "vr": {}, "net": {}}
    out["drift"] = 0.0 if len(inside) < 500 else 100.0 * (np.exp(inside.sum() * BARS_PER_YEAR / len(inside)) - 1.0)
    for q in HORIZONS:
        vr, _, _ = lh.vr_within(logp, sid, q)
        out["vr"][q] = vr
        m, _, n = lh.summarize(lh.contrarian_pairs(logp, sid, q))
        # La mejor de las dos direcciones: si el contrario pierde, el de continuacion gana.
        out["net"][q] = float("nan") if np.isnan(m) or n < 20 else abs(m) - trip
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir")
    ap.add_argument("--pair", default="XBTEUR")
    ap.add_argument("--years", nargs="*", type=int, default=list(range(2018, 2026)))
    ap.add_argument("--fee", type=float, default=0.4)
    ap.add_argument("--shuffles", type=int, default=3)
    ap.add_argument("--min-open", type=float, default=15.0, help="Cobertura minima para entrar en el ranking.")
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()

    trip = 2 * args.fee
    print(f"[criba] 154 variantes x {len(args.years)} anos, VR y apuesta ingenua contra un control barajado")
    print(f"  ida y vuelta {trip:.2f} %   velas de {lh.BAR} min   {args.shuffles} barajados/ano")

    real: dict[int, dict[str, dict]] = {}
    ctrl: dict[int, dict[str, list[dict]]] = {}
    for year in args.years:
        coarse, logp, _, inside = lh.build(args.data_dir, args.pair, year, 0.07, 7, 0)
        s = series_for(coarse, np.exp(logp))
        real[year] = {n: profile(logp, v & inside, trip) for n, v in gfl.families(s).items()}
        ctrl[year] = {n: [] for n in real[year]}
        rng = np.random.default_rng(year)
        r = np.diff(logp)
        for _ in range(args.shuffles):
            fake = np.concatenate(([logp[0]], logp[0] + np.cumsum(rng.permutation(r))))
            fs = series_for(coarse, np.exp(fake))
            for n, v in gfl.families(fs).items():
                ctrl[year][n].append(profile(fake, v & inside, trip))
        print(f"  {year} listo ({len(real[year])} variantes)", flush=True)
        del coarse, s

    names = [n for n in real[args.years[0]] if all(real[y][n]["open"] >= args.min_open for y in args.years)]
    print(f"\n  {len(names)} variantes con cobertura >= {args.min_open:.0f} % los {len(args.years)} anos")

    # Exceso de VR sobre el control, por variante y horizonte: solo esto puede ser estructura.
    print("\n[EXCESO DE VR] real menos barajado; negativo = revierte MAS que el control")
    print(f"  {'variante':<26}{'abierta':>9}{'peor deriva':>13}" + "".join(f"{lh.label(q):>10}" for q in HORIZONS))
    rows = []
    for n in names:
        exc = {}
        for q in HORIZONS:
            diffs = [
                real[y][n]["vr"][q] - float(np.mean([c["vr"][q] for c in ctrl[y][n] if not np.isnan(c["vr"][q])]))
                for y in args.years
                if not np.isnan(real[y][n]["vr"][q]) and any(not np.isnan(c["vr"][q]) for c in ctrl[y][n])
            ]
            exc[q] = float(np.median(diffs)) if diffs else float("nan")
        worst_drift = max(abs(real[y][n]["drift"]) for y in args.years)
        score = min((abs(v) for v in exc.values() if not np.isnan(v)), default=0.0)
        rows.append((score, n, exc, worst_drift))
    rows.sort(key=lambda t: -t[0])
    for score, n, exc, wd in rows[: args.top]:
        cov = np.mean([real[y][n]["open"] for y in args.years])
        cells = "".join(f"{'-' if np.isnan(exc[q]) else f'{exc[q]:+.3f}':>10}" for q in HORIZONS)
        print(f"  {n:<26}{cov:>8.1f}%{wd:>12.0f}%{cells}")
        del score

    # La version en dinero, que es la que decide.
    print(f"\n[NEGOCIO] mejor apuesta ingenua menos {trip:.2f} %, por su PEOR ano")
    print(f"  {'variante':<26}{'abierta':>9}{'peor deriva':>13}{'mejor h':>9}{'peor ano':>10}{'anos>0':>9}")
    money = []
    for n in names:
        best = None
        for q in HORIZONS:
            vals = [real[y][n]["net"][q] for y in args.years if not np.isnan(real[y][n]["net"][q])]
            if len(vals) < len(args.years) - 1:
                continue
            cand = (min(vals), q, sum(v > 0 for v in vals), len(vals))
            if best is None or cand[0] > best[0]:
                best = cand
        if best is None:
            continue
        money.append((best[0], n, best[1], best[2], best[3], max(abs(real[y][n]["drift"]) for y in args.years)))
    money.sort(key=lambda t: -t[0])
    for worst, n, q, wins, tot, wd in money[: args.top]:
        cov = np.mean([real[y][n]["open"] for y in args.years])
        print(f"  {n:<26}{cov:>8.1f}%{wd:>12.0f}%{lh.label(q):>9}{worst:>9.2f}%{wins:>6}/{tot}")

    print("\n[lectura] el exceso de VR es lo unico que el control no explica; el 'negocio' es ese exceso")
    print("          convertido en dinero. Una variante solo abre una via si su PEOR ano es positivo en")
    print("          la segunda tabla, y aun asi habria que mirar que deriva admite para pagarlo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
