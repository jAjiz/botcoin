"""¿El problema son las comisiones? La regla lenta contraria, puntuada como un bot de verdad.

Read-only y temporal. Lee los CSV OHLCVT de Kraken directamente, sin base de datos.

La criba de horizontes dejo una observacion con forma de pista: la esperanza BRUTA de apostar
contra el ultimo movimiento crece con el horizonte aproximadamente como la raiz del tiempo
(+0.011 % a 1 h, +0.183 % a 1 d, +0.742 % a 5 d), mientras la comision es FIJA por operacion.
Es decir, el cociente mejora solo con esperar mas. A 5 dias el bruto ya roza los 0.80 % de ida y
vuelta. Eso plantea una pregunta limpia: ¿es la comision lo unico que separa a esa regla de
ganar dinero?

Aqui se responde sin proxies. Tres diferencias con la criba, y las tres importan:

  LARGO/EFECTIVO  La criba media E[-signo(r_t) * r_{t+1}], que es una apuesta con signo: se
                  gana igual acertando una subida que una bajada. El bot NO puede ponerse
                  corto. Aqui la regla es la que el bot podria ejecutar: tras un bloque que
                  SUBE se pasa a efectivo, tras un bloque que BAJA se compra. Nada mas.

  ACTIVO BASE     Se puntua contra mantener, que es 0 % por construccion, no en euros. Estar en
                  efectivo mientras el precio sube es la forma principal de perder activo base,
                  y una esperanza bruta positiva por operacion puede convivir con eso.

  FASE            Los bloques de q velas empiezan en un punto arbitrario. Un solo desfase es un
                  estadistico de orden disfrazado, asi que se promedian LAS q fases posibles
                  (submuestreadas) y se reporta la mediana y el peor.

Se corre a varios niveles de comision -- 0.40 % (taker de Kraken, lo que este estudio ha
supuesto siempre), 0.25 % (maker) y 0 % -- porque esa comparacion es literalmente la pregunta:
si a 0 % es positiva y a 0.40 % no, el problema son las comisiones y hay algo que optimizar; si
a 0 % tampoco lo es, no lo son.

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):
  PYTHONPATH=. python scripts/analysis/slow_contrarian.py "C:/Dev/Kraken OHLCVT"
"""

import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lateral_horizon as lh

FEES = (0.40, 0.25, 0.0)
HORIZON_DAYS = (2, 3, 5, 7, 10, 14, 21, 30)
PHASES = 8  # desfases de arranque muestreados por horizonte


def run_rule(logp: np.ndarray, q: int, phase: int, fee: float) -> float:
    """Activo base acumulado frente a mantener, en porcentaje.

    Se arranca DENTRO del activo (mantener es la referencia) y en cada frontera de bloque se
    decide con el retorno del bloque que acaba de cerrarse: si subio, a efectivo; si bajo,
    dentro. Causal por construccion -- la decision del bloque i solo mira hasta su frontera.
    """
    edges = list(range(phase, len(logp), q))
    if len(edges) < 3:
        return float("nan")
    units = 1.0  # unidades de activo base
    cash = 0.0  # euros
    holding = True
    for i in range(1, len(edges) - 1):
        prev = logp[edges[i]] - logp[edges[i - 1]]
        want = prev < 0.0  # tras una bajada, dentro; tras una subida, fuera
        if want == holding:
            continue
        px = float(np.exp(logp[edges[i]]))
        if holding:
            cash, units, holding = units * px * (1.0 - fee), 0.0, False
        else:
            units, cash, holding = cash / px * (1.0 - fee), 0.0, True
    if not holding:
        units = cash / float(np.exp(logp[edges[-1]]))
    return 100.0 * (units - 1.0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir")
    ap.add_argument("--pair", default="XBTEUR")
    ap.add_argument("--years", nargs="*", type=int, default=list(range(2018, 2026)))
    ap.add_argument("--move", type=float, default=0.07)
    ap.add_argument("--look", type=int, default=7)
    args = ap.parse_args()

    print("[regla] tras un bloque que sube -> efectivo; tras uno que baja -> dentro. Solo largo/efectivo.")
    print(f"  activo base contra mantener (0 % por construccion)   {PHASES} desfases por horizonte")

    data = {}
    for year in args.years:
        _, logp, _, _ = lh.build(args.data_dir, args.pair, year, args.move, args.look, 0)
        data[year] = logp

    for fee_pct in FEES:
        fee = fee_pct / 100.0
        print(f"\n================ comision {fee_pct:.2f} %/pierna (ida y vuelta {2 * fee_pct:.2f} %) ================")
        print(f"  {'horizonte':>10}" + "".join(f"{y:>9}" for y in args.years) + f"{'peor':>9}{'anos>0':>9}")
        for days in HORIZON_DAYS:
            q = days * lh.DAY
            cells, per_year = [], []
            for year in args.years:
                logp = data[year]
                vals = [run_rule(logp, q, p, fee) for p in np.linspace(0, q - 1, PHASES, dtype=int)]
                vals = [v for v in vals if not np.isnan(v)]
                m = float(np.median(vals)) if vals else float("nan")
                per_year.append(m)
                cells.append("-" if np.isnan(m) else f"{m:+.1f}%")
            good = [v for v in per_year if not np.isnan(v)]
            worst = f"{min(good):+.1f}%" if good else "-"
            wins = f"{sum(v > 0 for v in good)}/{len(good)}"
            print(f"  {days:>8} d" + "".join(f"{c:>9}" for c in cells) + f"{worst:>9}{wins:>9}")

    print("\n[lectura] la comparacion entre los tres bloques ES la pregunta. Si la regla solo es")
    print("          positiva a comision 0, el problema son las comisiones y hay margen que buscar")
    print("          (maker en vez de taker, tramo de volumen). Si tampoco lo es a 0, no lo son.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
