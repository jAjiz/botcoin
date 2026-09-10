"""La reversion del mercado admitido, a horizontes LARGOS: ¿hay dinero en ella?

Read-only y temporal. Lee los CSV OHLCVT de Kraken directamente, sin base de datos.

`lateral_market_structure.py` midio el cociente de varianzas hasta 1 dia y encontro lo unico
que apunta a algo en todo el estudio: VR(1 dia) = 0.853 / 0.753 / 0.846 en 2023-2025, es decir
reversion a la media DEBIL pero con el mismo signo en tres ventanas independientes. Aquel
script se paraba ahi por dos razones malas: el horizonte maximo era 1 dia, y el VR se calculaba
sobre la CONCATENACION de los tramos abiertos, asi que una ventana de agregacion de un dia
cruzaba fronteras de tramo casi siempre. Empalmar tramos independientes destruye la
autocorrelacion real y empuja el VR HACIA 1, de modo que aquella cifra, si acaso, se queda
corta -- pero es sucia y no sirve para extender el horizonte.

Aqui se arregla lo uno y lo otro:

  VR          Lo-MacKinlay con z robusto a heterocedasticidad, pero contando SOLO las ventanas
              de agregacion que caben ENTERAS dentro de un mismo tramo abierto. Horizontes de
              15 min a 30 dias. Se imprime cuantas ventanas no solapadas equivalen, porque a 30
              dias el estadistico se queda sin datos mucho antes que el codigo.

  CONTRARIO   La medida que decide, y que el VR no da: la esperanza BRUTA de la regla contraria
              ingenua a horizonte h -- apostar contra el ultimo movimiento de h -- sobre
              retornos NO SOLAPADOS dentro de tramos. E[-sign(r_t) * r_{t+1}], con su error
              tipico. Un VR de 0.75 dice que hay oscilacion; esto dice cuanta vale, en tanto por
              ciento y por operacion, y se compara contra la ida y vuelta real (2 x comision).
              Un mercado que revierte por debajo del coste es inexplotable aunque el VR lo
              delate.

  POR ANO     El mismo numero ano a ano, para no repetir el error de creer una cifra agregada
              que en realidad fabrica un solo ano. La regla del estudio: consistencia entre
              datos, no la mediana ni el total.

La puerta se evalua sobre la rejilla de 15 min, no la de 1 min: a horizontes de dias la
resolucion del camino es irrelevante y asi caben ocho anos sin pelear con la memoria.

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):
  PYTHONPATH=. python scripts/analysis/lateral_horizon.py "C:/Dev/Kraken OHLCVT"
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import execution_fidelity as ef
import gate_families_live as gfl
import lateral_market_structure as lms

BAR = 15  # minutos por vela en la rejilla de trabajo
DAY = 1440 // BAR

# Horizontes en velas de 15 min, de 15 min a 30 dias.
HORIZONS = (1, 4, 16, 48, DAY, 2 * DAY, 3 * DAY, 5 * DAY, 7 * DAY, 14 * DAY, 30 * DAY)


def label(q: int) -> str:
    if q < 4:
        return f"{q * BAR} m"
    if q < DAY:
        return f"{q * BAR // 60} h"
    return f"{q / DAY:.0f} d"


def gate_on(coarse: pd.DataFrame, close: np.ndarray, move: float, look: int, delay: int) -> np.ndarray:
    """La puerta `impulso` evaluada continuamente sobre un camino de precios cualquiera.

    Se separa del cargador a proposito: el control sintetico necesita aplicar EXACTAMENTE la
    misma puerta a un camino barajado, y cualquier diferencia entre las dos rutas invalidaria
    la comparacion.
    """
    day = coarse["dtime"].dt.floor("D")
    frame = coarse.assign(close=close)
    days = day.drop_duplicates().to_numpy()
    dclose = frame.groupby(day)["close"].last().to_numpy(dtype=float)
    s = gfl.Series(frame, days, dclose, dclose, dclose)
    return gfl.det_impulse(s, move, look, delay)


def build(data_dir: str, pair: str, year: int, move: float, look: int, delay: int):
    """Marco de 15 min del ano, la puerta sobre esa misma rejilla y su cobertura."""
    t0 = int(pd.Timestamp(f"{year}-01-01").timestamp())
    lead = t0 - 86_400 * (look + 10)
    end = int(pd.Timestamp(f"{year}-12-31").timestamp()) + 86_399
    coarse = ef.coarse_frame(os.path.join(data_dir, f"{pair}_{BAR}.csv"), lead, end, BAR)
    close = coarse["close"].to_numpy(dtype=float)
    # El calentamiento se recorta aqui: sin historia suficiente la puerta no decide nada.
    inside = coarse["time"].to_numpy() >= t0
    return coarse, np.log(close), gate_on(coarse, close, move, look, delay) & inside, inside


def shuffled(coarse: pd.DataFrame, logp: np.ndarray, inside: np.ndarray, move: float, look: int, delay: int, seed: int):
    """Control: el MISMO camino con sus retornos barajados, y la misma puerta encima.

    Barajar conserva la distribucion marginal de los retornos y su volatilidad agregada, y
    destruye toda dependencia serial por construccion. Si la puerta produce el mismo VR<1 sobre
    un camino sin memoria, el VR no mide el mercado: mide la puerta. `impulso` admite el precio
    solo mientras se mantiene dentro de una banda alrededor de los cierres recientes, y
    condicionar a permanecer en una banda TRUNCA los recorridos largos, que es exactamente lo
    que un VR por debajo de 1 detecta.
    """
    rng = np.random.default_rng(seed)
    r = np.diff(logp)
    fake = np.concatenate(([logp[0]], logp[0] + np.cumsum(rng.permutation(r))))
    return fake, gate_on(coarse, np.exp(fake), move, look, delay) & inside


def stretch_ids(is_open: np.ndarray) -> np.ndarray:
    """Identificador de tramo por vela; -1 donde la puerta esta cerrada."""
    out = np.full(len(is_open), -1, dtype=int)
    for k, (a, b) in enumerate(lms.stretches(is_open)):
        out[a:b] = k
    return out


def vr_within(logp: np.ndarray, sid: np.ndarray, q: int) -> tuple[float, float, int]:
    """VR(q) de Lo-MacKinlay contando solo ventanas contenidas ENTERAS en un tramo.

    Devuelve (VR, z robusto, ventanas no solapadas equivalentes).
    """
    r = np.diff(logp)
    ok1 = (sid[1:] >= 0) & (sid[:-1] == sid[1:])  # el retorno de una vela no cruza frontera
    steps = r[ok1]
    if len(steps) < 10 * q:
        return float("nan"), float("nan"), 0
    mu = steps.mean()
    var1 = ((steps - mu) ** 2).sum() / (len(steps) - 1)
    if var1 <= 0:
        return float("nan"), float("nan"), 0
    if q == 1:
        return 1.0, 0.0, len(steps)
    valid = np.convolve(ok1.astype(int), np.ones(q, dtype=int), mode="valid") == q
    agg = np.convolve(r, np.ones(q), mode="valid")[valid]
    if len(agg) < 10 * q:
        return float("nan"), float("nan"), len(agg) // q
    varq = ((agg - q * mu) ** 2).sum() / (len(agg) * q)
    vr = varq / var1
    d = (steps - mu) ** 2
    denom = d.sum() ** 2
    theta = 0.0
    for j in range(1, q):
        theta += (2.0 * (q - j) / q) ** 2 * ((d[j:] * d[:-j]).sum() / denom)
    z = (vr - 1.0) / np.sqrt(theta) if theta > 0 else float("nan")
    return vr, z, len(agg) // q


def contrarian_pairs(logp: np.ndarray, sid: np.ndarray, q: int) -> list[float]:
    """Pagos brutos de apostar contra el ultimo movimiento de q velas, en log.

    Retornos de q velas CONSECUTIVOS y NO SOLAPADOS dentro de un mismo tramo abierto, para que
    ningun par cruce una puerta cerrada ni comparta velas con el siguiente.
    """
    pay: list[float] = []
    for a, b in lms.stretches(sid >= 0):
        seg = logp[a:b]
        k = (len(seg) - 1) // q
        if k < 2:
            continue
        blocks = np.diff(seg[: k * q + 1 : q])
        pay.extend((-np.sign(blocks[:-1]) * blocks[1:]).tolist())
    return pay


def summarize(pay: list[float]) -> tuple[float, float, int]:
    """(media en %, error tipico en %, numero de pares)."""
    if len(pay) < 5:
        return float("nan"), float("nan"), len(pay)
    v = 100.0 * (np.exp(np.asarray(pay)) - 1.0)
    return float(v.mean()), float(v.std(ddof=1) / np.sqrt(len(v))), len(v)


def arm_table(title: str, data: dict, trip: float) -> None:
    """VR y contrario de un brazo, con las mismas columnas para poder compararlos de un vistazo."""
    print(f"\n[{title}]")
    print(f"  {'horizonte':>10}{'VR mediano':>13}{'series VR<1':>13}{'pares':>8}{'bruto':>11}{'t':>7}{'neto':>11}")
    for q in HORIZONS:
        vrs = [v for v, _, _ in (vr_within(lp, sid, q) for lp, sid in data.values()) if not np.isnan(v)]
        allpay: list[float] = []
        for lp, sid in data.values():
            allpay.extend(contrarian_pairs(lp, sid, q))
        m, se, n = summarize(allpay)
        vr_txt = "-" if not vrs else f"{np.median(vrs):.3f}"
        cnt = "-" if not vrs else f"{sum(x < 1 for x in vrs)}/{len(vrs)}"
        if np.isnan(m):
            print(f"  {label(q):>10}{vr_txt:>13}{cnt:>13}{n:>8}{'-':>11}{'-':>7}{'-':>11}")
            continue
        t = m / se if se > 0 else float("nan")
        print(f"  {label(q):>10}{vr_txt:>13}{cnt:>13}{n:>8}{m:>10.3f}%{t:>7.1f}{m - trip:>10.3f}%")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir")
    ap.add_argument("--pair", default="XBTEUR")
    ap.add_argument("--years", nargs="*", type=int, default=list(range(2018, 2026)))
    ap.add_argument("--fee", type=float, default=0.4, help="Comision por pierna, en porcentaje.")
    ap.add_argument("--move", type=float, default=0.07)
    ap.add_argument("--look", type=int, default=7)
    ap.add_argument("--delay", type=int, default=0, help="Dias de confirmacion antes de ABRIR.")
    ap.add_argument("--shuffles", type=int, default=5, help="Caminos barajados por ano para el control.")
    args = ap.parse_args()

    trip = 2 * args.fee
    print(f"[puerta] impulso m={args.move:.2f} k={args.look} delay={args.delay} d, sobre velas de {BAR} min")
    print(f"  ida y vuelta {trip:.2f} %   anos {args.years[0]}-{args.years[-1]}   {args.shuffles} barajados/ano")

    gated, ungated, control, cover = {}, {}, {}, {}
    for year in args.years:
        coarse, logp, is_open, inside = build(args.data_dir, args.pair, year, args.move, args.look, args.delay)
        gated[year] = (logp, stretch_ids(is_open))
        # Sin puerta: el ano entero es un solo tramo, para separar lo que es del mercado de lo
        # que le anade condicionar a permanecer dentro de una banda.
        ungated[year] = (logp, stretch_ids(inside))
        cover[year] = float(is_open.mean())
        for k in range(args.shuffles):
            fake, fake_open = shuffled(coarse, logp, inside, args.move, args.look, args.delay, 1000 * year + k)
            control[(year, k)] = (fake, stretch_ids(fake_open))
        del coarse

    print("\n[cobertura] " + "  ".join(f"{y}:{100 * cover[y]:.0f}%" for y in args.years))
    arm_table("REAL, con la puerta", gated, trip)
    arm_table("REAL, sin puerta (el ano entero)", ungated, trip)
    arm_table("CONTROL: retornos barajados, misma puerta", control, trip)

    print("\n[POR ANO] el contrario a horizontes largos con la puerta, ano a ano")
    longs = tuple(q for q in HORIZONS if q >= DAY)
    print(f"  {'ano':>6}{'abierta':>9}" + "".join(f"{label(q):>14}" for q in longs))
    for year in args.years:
        cells = []
        for q in longs:
            m, _, n = summarize(contrarian_pairs(*gated[year], q))
            cells.append("-" if np.isnan(m) else f"{m:+.2f}% ({n})")
        print(f"  {year:>6}{100 * cover[year]:>8.1f}%" + "".join(f"{c:>14}" for c in cells))

    print("\n[lectura] la fila que decide es la del CONTROL. Barajar destruye toda memoria del")
    print("          mercado, asi que cualquier VR<1 que sobreviva ahi lo fabrica la puerta al")
    print("          condicionar a permanecer dentro de una banda, no el mercado. Solo la parte")
    print("          en que el brazo real SUPERA al control es estructura, y aun asi solo hay")
    print(f"          negocio si el 'neto' -- ya descontados los {trip:.2f} % de ida y vuelta -- es positivo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
