"""El mercado que la puerta admite: ¿tiene estructura explotable por ALGUN mecanismo?

Read-only y temporal. Lee los CSV OHLCVT de Kraken directamente, sin base de datos.

Con la puerta `impulso m=0.07 k=7` fijada por calidad de filtro, el barrido de las 105
configuraciones de trailing-stop dio 0/105 y una superficie monotona cuyo optimo es no operar.
Eso dice que ESE mecanismo pierde, no que el mercado admitido no tenga nada. Este script separa
las dos cosas, y lo hace con un limite superior en vez de con otra busqueda.

Tres medidas, de la mas concluyente a la mas descriptiva:

  TECHO      Lo maximo que se puede acumular en activo base operando las velas admitidas CON
             PREVISION PERFECTA, pagando la comision real y CON UN TOPE DE OPERACIONES. Sin el
             tope la cifra no acota nada: a comision cero salen 165 000 operaciones y un numero
             astronomico, que es cierto y no informa. Con el tope responde la pregunta util --
             "un mecanismo que opere N veces al ano, en el mejor de los casos, ¿cuanto puede
             sacar de estas velas?" -- y se compara contra lo que el bot saca de verdad. Se
             resuelve exacto con programacion dinamica de dos estados (dentro / en euros) y una
             dimension de operaciones gastadas, en O(n*N).

             Ojo con lo que NO demuestra: un techo alto no dice que exista un mecanismo causal
             que lo alcance. El cribado de senales ya midio que la etiqueta perfecta de pivotes
             vale +406 % en 2025 y que ninguna caracteristica causal la predice. El techo solo
             descarta la explicacion "aqui no hay nada que capturar".

  VARIANZA   Cociente de varianzas de Lo-MacKinlay a varios horizontes, calculado SOLO dentro
             de tramos contiguos abiertos (el salto de precio a traves de una puerta cerrada no
             es un retorno que nadie pudiera capturar). VR < 1 es reversion a la media -- hay
             oscilacion que cobrar --, VR = 1 es paseo aleatorio, VR > 1 es tendencia. El
             estadistico z va con la correccion robusta a heterocedasticidad, que es obligatoria
             en precios de cripto.

  AMPLITUD   Distribucion del tamano de los tramos admitidos y de sus oscilaciones internas,
             contra el coste de ida y vuelta (2 x comision). Un mercado que revierte con
             amplitud por debajo del coste es inexplotable aunque el VR lo delate.

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):
  PYTHONPATH=. python scripts/analysis/lateral_market_structure.py "C:/Dev/Kraken OHLCVT"
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import execution_fidelity as ef
import gate_families_live as gfl
import gate_live_fidelity as glf

HORIZONS = (5, 15, 60, 240, 1440)


def stretches(is_open: np.ndarray) -> list[tuple[int, int]]:
    """Tramos contiguos con la puerta abierta, como pares [inicio, fin)."""
    edges = np.diff(np.concatenate(([0], is_open.view(np.int8), [0])))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1), strict=True))


def ceiling(price: np.ndarray, spans: list[tuple[int, int]], fee: float, caps: tuple[int, ...]) -> dict[int, float]:
    """Maximo activo base con prevision perfecta y como mucho `cap` operaciones, por cada cap.

    Dos estados (dentro del activo / en euros) por numero de operaciones ya gastadas. Fuera de
    los tramos abiertos hay que estar DENTRO (es lo que hace la puerta), asi que cada tramo
    empieza y acaba en ese estado y solo se optimiza lo de en medio. El presupuesto de
    operaciones es GLOBAL del ano, no por tramo, asi que se arrastra de un tramo al siguiente.
    """
    keep = 1.0 - fee
    top = max(caps)
    # long[j] = unidades de activo con j operaciones gastadas; flat[j] = euros, idem.
    long_v = np.full(top + 1, -np.inf)
    flat_v = np.full(top + 1, -np.inf)
    long_v[0] = 1.0
    for a, b in spans:
        p = price[a:b]
        if len(p) < 2:
            continue
        for px in p:
            # Vender: pasa de long[j] a flat[j+1]. Comprar: de flat[j] a long[j+1].
            sell = np.concatenate(([-np.inf], long_v[:-1] * px * keep))
            buy = np.concatenate(([-np.inf], flat_v[:-1] / px * keep))
            flat_v = np.maximum(flat_v, sell)
            long_v = np.maximum(long_v, buy)
        # El tramo debe cerrar dentro del activo.
        back = np.concatenate(([-np.inf], flat_v[:-1] / p[-1] * keep))
        long_v = np.maximum(long_v, back)
        flat_v = np.full(top + 1, -np.inf)
    best = np.maximum.accumulate(np.where(np.isfinite(long_v), long_v, -np.inf))
    return {c: float(best[c]) for c in caps}


def variance_ratio(steps: np.ndarray, q: int) -> tuple[float, float]:
    """VR(q) de Lo-MacKinlay con z robusto a heterocedasticidad."""
    n = len(steps)
    if n < 10 * q:
        return float("nan"), float("nan")
    mu = steps.mean()
    var1 = ((steps - mu) ** 2).sum() / (n - 1)
    agg = np.convolve(steps, np.ones(q), mode="valid")
    varq = ((agg - q * mu) ** 2).sum() / ((n - q + 1) * q)
    vr = varq / var1 if var1 > 0 else float("nan")
    # Correccion robusta: suma ponderada de las autocovarianzas de los cuadrados.
    d = (steps - mu) ** 2
    denom = d.sum() ** 2
    theta = 0.0
    for j in range(1, q):
        # delta_j = sum (r_t-mu)^2 (r_{t-j}-mu)^2 / (sum (r_t-mu)^2)^2. Sin factor n: llevarlo
        # hacia dentro inflaba theta por n y aplastaba todos los z a 0.0.
        theta += (2.0 * (q - j) / q) ** 2 * ((d[j:] * d[:-j]).sum() / denom)
    z = (vr - 1.0) / np.sqrt(theta) if theta > 0 else float("nan")
    return vr, z


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir")
    ap.add_argument("--pair", default="XBTEUR")
    ap.add_argument("--years", nargs="*", type=int, default=[2025])
    ap.add_argument("--fee", type=float, default=0.4, help="Comision por pierna, en porcentaje.")
    ap.add_argument("--move", type=float, default=0.07)
    ap.add_argument("--look", type=int, default=7)
    args = ap.parse_args()

    fee = args.fee / 100.0
    print(f"[puerta] impulso m={args.move:.2f} k={args.look}, FIJA (elegida por calidad de filtro)")
    print(f"  comision {args.fee} %/pierna -> ida y vuelta {200 * fee:.2f} %")

    for year in args.years:
        cal_t0 = int(pd.Timestamp(f"{year - 1}-07-01").timestamp())
        t0 = int(pd.Timestamp(f"{year}-01-01").timestamp())
        t1 = int(pd.Timestamp(f"{year}-12-31").timestamp()) + 86_399
        coarse = ef.coarse_frame(os.path.join(args.data_dir, f"{args.pair}_15.csv"), cal_t0, t1, 15)
        days, dclose = glf.daily_closes_from(coarse)
        day = coarse["dtime"].dt.floor("D")
        fine = ef.fine_frame(os.path.join(args.data_dir, f"{args.pair}_1.csv"), coarse, t0, t1, 1)
        s = gfl.Series(
            fine,
            days,
            dclose,
            coarse.groupby(day)["high"].max().to_numpy(float),
            coarse.groupby(day)["low"].min().to_numpy(float),
        )
        is_open = gfl.det_impulse(s, args.move, args.look)
        price = fine["close"].to_numpy(dtype=float)
        spans = stretches(is_open)
        hold = price[-1] / price[0] - 1.0

        print(f"\n================ {year} ================")
        print(f"  mantener {100 * hold:+.1f} % EUR   puerta abierta {100 * is_open.mean():.1f} %   {len(spans)} tramos")

        lens = np.array([b - a for a, b in spans], dtype=float)
        rets = np.array([price[b - 1] / price[a] - 1.0 for a, b in spans])
        rng = np.array([(price[a:b].max() / price[a:b].min() - 1.0) for a, b in spans if b > a])
        print(
            f"  duracion: mediana {np.median(lens) / 60:.1f} h, p90 {np.percentile(lens, 90) / 60:.1f} h, "
            f"maxima {lens.max() / 60:.1f} h"
        )
        print(
            f"  |retorno| del tramo: mediana {100 * np.median(np.abs(rets)):.2f} %   "
            f"recorrido max-min: mediana {100 * np.median(rng):.2f} %"
        )

        # La puerta no tiene histeresis al cerrar, asi que puede parpadear alrededor del umbral y
        # fabricar tramos de minutos en los que no cabe ninguna operacion. Esto lo cuantifica.
        print(f"\n  [FRAGMENTACION] un tramo corto no da tiempo a nada; el coste de ida y vuelta es {200 * fee:.2f} %")
        print(f"    {'duracion':>12}{'tramos':>9}{'% del tiempo abierto':>23}{'recorrido mediano':>20}")
        edges = [(0, 60), (60, 240), (240, 1440), (1440, 10080), (10080, 10**9)]
        etiquetas = ["< 1 h", "1-4 h", "4-24 h", "1-7 d", "> 7 d"]
        total_open = lens.sum()
        for (lo, hi), etiqueta in zip(edges, etiquetas, strict=True):
            sel = [i for i, x in enumerate(lens) if lo <= x < hi]
            if not sel:
                print(f"    {etiqueta:>12}{0:>9}{0.0:>22.1f}%{'-':>20}")
                continue
            share = 100 * lens[sel].sum() / total_open
            print(f"    {etiqueta:>12}{len(sel):>9}{share:>22.1f}%{100 * np.median(rng[sel]):>19.2f}%")

        print("\n  [TECHO] prevision perfecta sobre las velas admitidas, con tope de operaciones")
        caps = (10, 25, 50, 100, 250, 500)
        best = ceiling(price, spans, fee, caps)
        print(f"    {'tope ops':>10}{'activo base':>16}")
        for c in caps:
            print(f"    {c:>10}{100 * (best[c] - 1):>15.1f}%")

        print("\n  [VARIANZA] dentro de tramos contiguos; VR<1 revierte, VR=1 paseo aleatorio")
        inner = np.concatenate([np.diff(np.log(price[a:b])) for a, b in spans if b - a > 2])
        print(f"    {len(inner)} retornos de 1 min admitidos")
        print(f"    {'horizonte':>10}{'VR':>9}{'z':>9}")
        for q in HORIZONS:
            vr, z = variance_ratio(inner, q)
            print(f"    {q:>8} m{vr:>9.3f}{z:>9.1f}")

        del fine, s, coarse

    print("\n[lectura] el TECHO acota lo disponible, no lo alcanzable: dice cuanto habria si se")
    print("          acertara siempre, con N operaciones y pagando comisiones. Compararlo con lo que el")
    print("          bot saca mide cuanto se deja, pero un techo alto NO implica que exista una regla")
    print("          causal que lo toque -- eso ya se midio aparte y salio que no.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
