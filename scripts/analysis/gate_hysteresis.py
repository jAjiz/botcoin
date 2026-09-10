"""El parpadeo de la puerta: ¿arranca operaciones que acaban perdiendo, y lo arregla un retraso?

Read-only y temporal. Lee los CSV OHLCVT de Kraken directamente, sin base de datos.

`lateral_market_structure.py` conto la fragmentacion y la descarto por su peso en el tiempo:
452-902 tramos al ano, de los que 375-745 duran menos de una hora, pero esos cortos suman solo
el 0.8-2.2 % del tiempo abierto. Ese argumento es correcto para el TIEMPO y no dice nada sobre
las OPERACIONES, que es lo que cuesta dinero. Un tramo de veinte minutos no da para un ciclo
completo, pero si da de sobra para que salte el trailing-stop; y en cuanto la mascara vuelve a
caer, el bot esta en euros y se le fuerza la recompra en la vela siguiente. Es decir: el
parpadeo no gasta tiempo, gasta COMISIONES y recompras forzadas, que es justo el grupo de
ciclos que compone negativo.

Dos medidas:

  REPARTO     Cada ciclo se etiqueta por la DURACION del tramo abierto en el que cayo su venta,
              y se compone cada grupo en activo base. Si los tramos cortos concentran ciclos y
              esos ciclos pierden, el parpadeo tiene un coste propio y separable.

  HISTERESIS  Un retraso de confirmacion antes de ABRIR, en velas de 1 min: la puerta cierra en
              cuanto la condicion falla (nunca se retrasa la proteccion) y solo abre tras `d`
              minutos seguidos cumpliendose. Se barre `d` y se mira que le pasa a las cuatro
              cosas a la vez: cobertura, DERIVA ADMITIDA (el objetivo propio de la puerta, que
              no se puede estropear para arreglar el parpadeo), numero de recompras forzadas y
              resultado en activo base. Un retraso que limpia el parpadeo pero mete tendencia
              dentro no es una mejora, es cambiar de puerta.

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):
  PYTHONPATH=. python scripts/analysis/gate_hysteresis.py "C:/Dev/Kraken OHLCVT"
"""

import argparse
import dataclasses
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import execution_fidelity as ef
import gate_config_sweep as gcs
import lateral_market_structure as lms

from core.config import ATR_DESV_LIMIT, RECALIBRATION_BARS
from trading.engine import EngineConfig, mark_to_market, simulate_operations

BARS_PER_YEAR = 365 * 1440

# Retrasos de confirmacion, en minutos. 0 es la puerta actual, sin histeresis.
DELAYS = (0, 60, 240, 720, 1440, 2880, 10080)

# Cuatro configuraciones del barrido de 105, de la mas rapida a la mas lenta, para ver si el
# efecto del retraso depende de cuanto opere el bot.
CONFIGS = ((0.0, 0.5), (0.0, 0.9), (0.01, 0.9), (0.02, 0.9))


def confirm(raw: np.ndarray, need: int) -> np.ndarray:
    """Abre solo tras `need` velas seguidas cumpliendose; cierra en cuanto falla.

    Asimetrico a proposito. Retrasar el CIERRE dejaria al bot operando dentro de un impulso ya
    detectado, que es exactamente lo que la puerta existe para evitar.
    """
    if need <= 0:
        return raw
    run = np.zeros(len(raw), dtype=int)
    n = 0
    for i, ok in enumerate(raw):
        n = n + 1 if ok else 0
        run[i] = n
    return raw & (run > need)


def drift(step: np.ndarray, is_open: np.ndarray) -> float:
    """Deriva anualizada del mercado que la puerta admite, la misma que `gate_filter_quality`."""
    inside = step[is_open]
    if len(inside) < 1000:
        return 0.0
    return 100.0 * (np.exp(inside.sum() * BARS_PER_YEAR / len(inside)) - 1.0)


def base_score(ops: list, final_price: float, hold: float) -> float:
    """Activo base acumulado frente a mantener, que es 0 % por construccion."""
    eur = mark_to_market(ops, final_price) if ops else 0.0
    return ((1.0 + eur / 100.0) / (1.0 + hold / 100.0) - 1.0) * 100.0


def run(fine, scheduled, mask, mm, stop, fee, **switches) -> list:
    cfg = EngineConfig(
        pair="XBTEUR",
        calibration=ef._calibration(scheduled[0][1], stop),
        k_act=None,
        min_margin=mm,
        atr_desv_limit=ATR_DESV_LIMIT,
        calibration_schedule=tuple((at, ef._calibration(p, stop)) for at, p in scheduled),
    )
    cfg = dataclasses.replace(cfg, force_hold_bars=mask, reset_on_unmask=True, **switches)
    return simulate_operations(fine, cfg, fee_rate=fee)


def label(d: int) -> str:
    if d == 0:
        return "sin retraso"
    if d < 1440:
        return f"{d // 60} h"
    return f"{d // 1440} d"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir")
    ap.add_argument("--pair", default="XBTEUR")
    ap.add_argument("--gate", default="simetrica", help=f"Una de: {', '.join(gcs.GATES)}")
    ap.add_argument("--years", nargs="*", type=int, default=[2023, 2024, 2025])
    ap.add_argument("--lead-months", type=int, default=6)
    ap.add_argument("--recalib-bars", type=int, default=RECALIBRATION_BARS)
    ap.add_argument("--fee", type=float, default=ef.FEE)
    args = ap.parse_args()

    ef.FEE = args.fee
    fee = args.fee / 100.0
    print(f"[puerta] '{args.gate}'   camino de 1 min, ATR de 15 min, comision {args.fee} %/pierna")
    print("  el retraso solo afecta a la APERTURA; el cierre nunca se retrasa")

    for year in args.years:
        fine, points, s = gcs.build(args.data_dir, args.pair, year, args.lead_months, args.recalib_bars)
        price = fine["close"].to_numpy(dtype=float)
        times = fine["time"].to_numpy()
        scheduled = ef.remap(points, times)
        final_price = float(price[-1])
        hold = (final_price / float(price[0]) - 1.0) * 100.0
        step = np.concatenate(([0.0], np.diff(np.log(price))))
        bar_of = {str(t): i for i, t in enumerate(fine["dtime"].tolist())}
        raw = gcs.GATES[args.gate](s)

        print(f"\n================ {year} ================", flush=True)
        print(f"  mantener {hold:+.1f} % EUR   puerta cruda abierta {100 * raw.mean():.1f} %")

        # --- reparto de ciclos por la duracion del tramo en que cae la venta ------------
        is_open = raw
        spans = lms.stretches(is_open)
        span_of = np.full(len(price), -1, dtype=int)
        for a, b in spans:
            span_of[a:b] = b - a
        mask = frozenset(np.flatnonzero(~is_open).tolist())
        print("\n  [REPARTO] ciclos por duracion del tramo abierto en que se vendio")
        print(f"    {'config':<16}{'tramo <1h':>22}{'1h-1d':>22}{'>1d':>22}")
        for mm, stop in CONFIGS:
            ops = run(fine, scheduled, mask, mm, stop, fee)
            buckets = {"corto": [1.0, 0], "medio": [1.0, 0], "largo": [1.0, 0]}
            for i in range(len(ops) - 1):
                if ops[i].side != "sell" or ops[i + 1].side != "buy":
                    continue
                sell, buy = ops[i], ops[i + 1]
                dur = span_of[bar_of[sell.time]]
                key = "corto" if 0 <= dur < 60 else ("medio" if dur < 1440 else "largo")
                buckets[key][0] *= (sell.price / buy.price) * (1.0 - fee) ** 2
                buckets[key][1] += 1
            cells = "".join(f"{100 * (v - 1):>13.1f}% ({n:>3}){'':>2}" for v, n in buckets.values())
            print(f"    mm={mm:.3f} s={stop}{cells}")

        # --- barrido del retraso de confirmacion ---------------------------------------
        print("\n  [HISTERESIS] que le hace el retraso a la puerta y al bot")
        print(
            f"    {'retraso':>12}{'abierta':>9}{'tramos':>8}{'% t. cortos':>13}{'deriva adm.':>13}"
            + "".join(f"{f'mm={mm:.2f}/{s}':>13}" for mm, s in CONFIGS)
        )
        for d in DELAYS:
            gated = confirm(raw, d)
            if not gated.any():
                print(f"    {label(d):>12}{'0.0':>8}%{'-':>8}{'-':>13}{'-':>13}")
                continue
            sp = lms.stretches(gated)
            lens = np.array([b - a for a, b in sp], dtype=float)
            short = 100.0 * lens[lens < 60].sum() / lens.sum() if lens.sum() else 0.0
            m = frozenset(np.flatnonzero(~gated).tolist())
            cells = ""
            for mm, stop in CONFIGS:
                ops = run(fine, scheduled, m, mm, stop, fee)
                cells += f"{base_score(ops, final_price, hold):>12.1f}%"
            print(
                f"    {label(d):>12}{100 * gated.mean():>8.1f}%{len(sp):>8}{short:>12.1f}%"
                f"{drift(step, gated):>12.0f}%{cells}",
                flush=True,
            )
        del fine, s

    print("\n[lectura] el retraso solo es una mejora si sube el resultado SIN estropear la deriva")
    print("          admitida: esa es la razon de ser de la puerta y no se negocia por comodidad.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
