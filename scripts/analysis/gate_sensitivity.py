"""Mapa de sensibilidad de la puerta `alcista`: meseta o pico.

Read-only y temporal. Lee el CSV OHLCVT de 15 min de Kraken, sin base de datos.

`lateral_detector.py` encontro que `alcista m=0.10 k=10 d=0` -- cerrar la puerta mientras el
cierre de hoy este un `m` por encima de ALGUN cierre de los ultimos `k` dias -- bate a mantener
en dos ventanas continuas. Pero esos dos numeros salieron de una rejilla de tres valores por eje
(`m` en 0.07/0.10/0.15, `k` en 5/7/10) y el ganador esta en el BORDE de ambos: `k=10` es el
maximo probado y `m=0.10` el central. Un optimo en el borde no se ha explorado, se ha topado.

La pregunta que responde este barrido es la unica que separa un mecanismo de un parametro
ajustado: si el vecindario de (0.10, 10) es una MESETA -- todos los vecinos ganan, la superficie
cambia despacio -- la regla captura algo estructural y los dos numeros son intercambiables
dentro de un rango. Si es un PICO -- los vecinos caen o cambian de signo -- lo que se encontro
es el mejor punto de 18 pruebas sobre unos datos concretos, y no hay razon para esperar que
sobreviva a datos nuevos.

Se puntua como una corrida continua por ventana, nunca por años encadenados (ver el defecto de
puntuacion por segmentos: encadenar factores anuales inflo un resultado seis veces).

SEGUNDO BRAZO, y no es opcional. `lateral_detector.py` calcula la bandera del dia i con el
CIERRE del dia i y la aplica a las velas de ese mismo dia desde las 00:00 (`daily_closes` mapea
cada dia a `first_bar..last_bar`). Eso son hasta 24 horas de look-ahead, sistematicas y siempre
a favor: la puerta se cierra la mañana del dia en que el precio hace el movimiento que la
cierra. `--lags` reevalua toda la rejilla con la bandera desplazada `lag` dias, que es lo que un
motor puede aplicar de verdad -- el estado de hoy se decide con el cierre de ayer. La diferencia
entre `lag=0` y `lag=1` ES el sesgo, y hasta medirla ninguna cifra de la puerta esta limpia.

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):

    PYTHONPATH=. python scripts/analysis/gate_sensitivity.py "C:/Dev/Kraken OHLCVT/XBTEUR_15.csv"
"""

import argparse
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import grid_sweep_holdout as gsh
import lateral_detector as ld

from core.config import RECALIBRATION_BARS

# Rejilla fina alrededor de (0.10, 10). Los ejes se extienden HACIA AFUERA del ganador en ambas
# direcciones, que es lo que faltaba: `k` llegaba hasta 10 y ahi se quedo el optimo.
MOVES = (0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.12, 0.15, 0.20, 0.25)
LOOKS = (3, 5, 7, 10, 14, 20, 30)

WINDOWS = (("2019-01-01", "2022-12-31"), ("2023-01-01", "2025-12-31"))


def lagged(flags: list[bool], lag: int) -> list[bool]:
    """La bandera del dia i pasa a decidirse con el cierre del dia i-lag.

    El relleno inicial es `False` (puerta cerrada = mantener el activo), que es la unica opcion
    conservadora: sin historia suficiente no se opera.
    """
    return flags if lag <= 0 else [False] * lag + flags[:-lag]


def sweep(args, span: tuple[str, str], lags: list[int]) -> dict[int, dict[tuple[float, int], dict]]:
    """Todas las combinaciones de la rejilla sobre una sola corrida continua, por cada lag."""
    label = f"{span[0][:4]}-{span[1][:4]}"
    print("")
    print(f"================ {label} continuo ================", flush=True)
    w = ld.build(args, label, span=span)
    n_bars = len(w["ctx"].df)
    print(f"  hold {w['hold']:+.2f} %  |  {len(w['closes'])} dias  |  {n_bars} velas", flush=True)

    out: dict[int, dict[tuple[float, int], dict]] = {lag: {} for lag in lags}
    t0 = time.perf_counter()
    for move in MOVES:
        for lag in lags:
            for look in LOOKS:
                flags = lagged(ld.det_up_impulse(w["closes"], move, look, 0), lag)
                segs = ld.segments_from(w["days"], flags, w["ctx"].df)
                if not segs:
                    out[lag][(move, look)] = {"base": 0.0, "ops": 0, "open": 0.0}
                    continue
                mask = ld.mask_of(segs, n_bars)
                res = ld.run_config(w["ctx"], w["ctx"].calibration_points, mask, w["hold"], w["final"])
                out[lag][(move, look)] = {
                    "base": res["base"],
                    "ops": res["ops"],
                    "open": 100.0 * (n_bars - len(mask)) / n_bars,
                }
        done = sum(len(v) for v in out.values())
        print(
            f"    m={move:.2f} listo ({done}/{len(MOVES) * len(LOOKS) * len(lags)}, {time.perf_counter() - t0:.0f}s)",
            flush=True,
        )
    return out


def grid(title: str, res: dict[tuple[float, int], dict], key: str, fmt: str) -> None:
    print("")
    print(f"  {title}")
    print("    m / k " + "".join(f"{k:>9}" for k in LOOKS))
    for move in MOVES:
        line = f"    {move:.2f} "
        for look in LOOKS:
            line += f"{res[(move, look)][key]:>{fmt}}"
        print(line)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--recalib-bars", type=int, default=RECALIBRATION_BARS)
    ap.add_argument(
        "--window",
        nargs=2,
        action="append",
        default=None,
        help="START END; repetible. Por defecto las dos ventanas del estudio. Una por proceso "
        "paraleliza el barrido, porque el coste manda en la calibracion y es cuadratico.",
    )
    ap.add_argument(
        "--lags",
        nargs="*",
        type=int,
        default=[0, 1],
        help="Dias de desplazamiento de la bandera. 0 es lo que midio el estudio y mira 24 h al "
        "futuro; 1 es lo que un motor puede aplicar. La diferencia es el sesgo.",
    )
    ap.add_argument("--out", default=None, help="Vuelca los resultados a JSON para fusionar varias corridas.")
    ap.add_argument("--merge", nargs="*", default=None, help="Solo fusiona estos JSON y imprime el resumen.")
    args = ap.parse_args()

    if args.merge:
        per_win = {}
        for path in args.merge:
            with open(path, encoding="utf-8") as fh:
                for label, res in json.load(fh).items():
                    per_win[label] = {(float(k.split("|")[0]), int(k.split("|")[1])): v for k, v in res.items()}
        summary(per_win)
        return 0

    print(f"[datos] {args.csv}   config fija {gsh._signature(ld.CONFIG)}   fee {gsh.FEE} % por pata")
    print(f"[rejilla] {len(MOVES)} x {len(LOOKS)} = {len(MOVES) * len(LOOKS)} puntos por ventana, d=0")

    per_win = {}
    for span in [tuple(x) for x in args.window] if args.window else WINDOWS:
        base_label = f"{span[0][:4]}-{span[1][:4]}"
        res = sweep(args, span, args.lags)
        for lag in args.lags:
            label = f"{base_label} L{lag}"
            per_win[label] = res[lag]
            grid(f"{label}: activo base (%)", res[lag], "base", "9.1f")
            grid(f"{label}: velas con la puerta abierta (%)", res[lag], "open", "9.0f")
            grid(f"{label}: operaciones", res[lag], "ops", "9d")
        if 0 in args.lags and 1 in args.lags:
            # El sesgo de look-ahead, punto por punto. Si es pequeño y sin signo, el resultado
            # sobrevive; si es grande y siempre positivo, toda la linea estaba mirando al futuro.
            bias = {k: {"d": res[0][k]["base"] - res[1][k]["base"]} for k in res[0]}
            grid(f"{base_label}: sesgo de look-ahead, L0 - L1 (puntos)", bias, "d", "9.1f")
            vals = [v["d"] for v in bias.values()]
            print(
                f"    mediana {statistics.median(vals):+.1f} pts, "
                f"positivos {sum(1 for v in vals if v > 0)}/{len(vals)}, "
                f"maximo {max(vals):+.1f}, minimo {min(vals):+.1f}"
            )

    if args.out:
        dumped = {lb: {f"{m}|{k}": v for (m, k), v in res.items()} for lb, res in per_win.items()}
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(dumped, fh)
        print("")
        print(f"[json] {args.out}")

    summary(per_win)
    return 0


def summary(per_win: dict) -> None:

    # Lo unico que decide meseta contra pico: cuantos vecinos siguen ganando, y en las DOS
    # ventanas a la vez. Un punto que gana en una y pierde en la otra no es mecanismo.
    labels = list(per_win)
    print("")
    print("[resumen] cada punto en cada ventana y lag; 'min' es el peor de todos")
    print(f"  {'m':>5} {'k':>4} " + "".join(f"{lb:>14}" for lb in labels) + f"{'min':>10}")
    rows = []
    for move in MOVES:
        for look in LOOKS:
            vals = [per_win[lb][(move, look)]["base"] for lb in labels]
            rows.append((move, look, vals, min(vals)))
    for move, look, vals, worst in sorted(rows, key=lambda r: r[3], reverse=True):
        print(f"  {move:>5.2f} {look:>4} " + "".join(f"{v:>+13.1f}%" for v in vals) + f"{worst:>+9.1f}%")

    both = [r for r in rows if r[3] > 0.0]
    print("")
    print(f"  positivos en TODAS las columnas: {len(both)} de {len(rows)}")
    for lb in labels:
        vals = [per_win[lb][(m, k)]["base"] for m, k in [(r[0], r[1]) for r in rows]]
        pos = sum(1 for v in vals if v > 0.0)
        print(f"    {lb}: mediana {statistics.median(vals):+.1f} %, positivos {pos}/{len(vals)}")

    ref = (0.10, 10)
    print("")
    print("  el ganador previo m=0.10 k=10: " + ", ".join(f"{lb} {per_win[lb][ref]['base']:+.1f} %" for lb in labels))
    print("")
    print("[lectura] meseta = los vecinos del ganador tienen su mismo signo y un valor parecido, en todas")
    print("          las columnas. Pico = el ganador destaca sobre vecinos que pierden. Una fila o columna")
    print("          que cambia de signo de golpe marca donde deja de funcionar el mecanismo, no ruido.")
    print("          L0 mira 24 h al futuro y L1 no: comparar SIEMPRE contra L1.")


if __name__ == "__main__":
    raise SystemExit(main())
