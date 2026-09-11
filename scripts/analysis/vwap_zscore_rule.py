"""La regla de umbral sobre la VWAP, puntuada en dinero: ¿paga la reversion que el screen ve?

Read-only y temporal. Lee los CSV OHLCVT de Kraken directamente, sin base de datos y sin motor.

De donde viene. `signal_screen.py` encontro que la distancia a la VWAP sale de la banda nula
contra la etiqueta honesta (AUC 0.436 a 12 h, p=0.00, en la direccion que revierte) -- pero
tambien salen `ma_dist_1w`, `ret_3d` y `rsi_1d`, y la distancia SIN ponderar por volumen puntua
igual o mejor. Es decir: hay reversion debil, y no es del VWAP. Falta la unica medida que
decide: ¿paga como REGLA que el bot pueda ejecutar?

Por que no basta con el AUC. La leccion cara del estudio es que una esperanza FIRMADA no es
alcanzable por un bot largo-o-plano: `E[-signo(r_t) * r_{t+1}]` cobra igual por acertar una
subida que por acertar una bajada, y la mitad "acertar la bajada" se convierte en estar en
euros, que en activo base es la forma dominante de perder. El contrario ingenuo llego a
+0.742 % por operacion y, convertido a largo-o-plano, pierde 54-69 % del activo base en su peor
ano CON LA COMISION A CERO. Por eso aqui se convierte primero y se compara con la comision
despues.

La regla, fijada antes de correr nada:

  * ANCLA      VWAP rodante de `n` velas, ponderada por volumen sobre el CIERRE (no el precio
               tipico). Se usa el cierre a proposito: el camino barajado del control no tiene
               maximo ni minimo propios, y el control tiene que estar controlado -- misma
               construccion en los dos brazos o la comparacion no vale.
  * Z          (cierre - VWAP) / desviacion ponderada por volumen alrededor de esa misma VWAP.
               Todo con datos hasta t inclusive.
  * ESTADO     largo-o-plano, y por defecto DENTRO del activo: mantener es 0 % por construccion
               y es la vara. Vende (a euros) cuando z >= +k. Vuelve a comprar cuando z <= -k
               (reentrada "banda") o cuando z <= 0 (reentrada "ancla", que recupera la posicion
               antes y pasa menos tiempo en caja).
  * PUERTA     con la puerta cerrada se MANTIENE el activo, que es el encuadre del estudio:
               mantener por defecto y operar solo en lo admitido. Se corre con puerta
               (`impulso m=0.07 k=7`) y sin ella, para separar el mercado de la puerta.

El control. Los mismos retornos barajados, con el volumen de cada vela viajando con SU retorno
(la misma permutacion), asi que se conserva la relacion contemporanea precio-volumen y se
destruye toda dependencia serial. Si la regla gana lo mismo sobre un camino sin memoria, lo que
gana no es reversion del mercado. Es la fila que decide.

La rejilla es de 24 brazos (3 ventanas x 4 umbrales x 2 reentradas), fijada arriba y corrida
entera: se informa la DISTRIBUCION y la tasa base del control, nunca la cabeza de la tabla.

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):
  PYTHONPATH=. python scripts/analysis/vwap_zscore_rule.py "C:/Dev/Kraken OHLCVT"
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lateral_horizon as lh

BAR = 15
# Rejilla fijada antes de correr. 96 velas = 1 dia, la ventana que puntuo en el screen.
WINDOWS = (16, 96, 672)
KS = (1.0, 1.5, 2.0, 2.5)
REENTRIES = ("banda", "ancla")
WIN_LABEL = {16: "4h", 96: "1d", 672: "1sem"}


def vwap_z(close: np.ndarray, volume: np.ndarray, n: int) -> np.ndarray:
    """z = (cierre - VWAP_n) / desviacion ponderada por volumen, con datos hasta t inclusive."""
    c = pd.Series(close)
    w = pd.Series(volume).clip(lower=0.0)
    wsum = w.rolling(n).sum()
    vwap = (c * w).rolling(n).sum() / wsum
    var = (c.pow(2) * w).rolling(n).sum() / wsum - vwap.pow(2)
    sigma = np.sqrt(var.clip(lower=0.0))
    return ((c - vwap) / sigma.replace(0.0, np.nan)).to_numpy(dtype=float)


def state_path(z: np.ndarray, is_open: np.ndarray, k: float, reentry: str) -> np.ndarray:
    """Camino dentro(+1)/fuera(-1) de la regla de histeresis, vectorizado.

    La histeresis se reduce a un relleno hacia adelante porque las senales son idempotentes:
    vender estando ya fuera no hace nada, y comprar estando ya dentro tampoco. Asi que el estado
    en t es la ULTIMA directiva no nula hasta t, con la precedencia puerta > venta > compra.
    """
    floor = -k if reentry == "banda" else 0.0
    directive = np.where(
        ~is_open | np.isnan(z),
        1,  # puerta cerrada, o sin historia todavia: mantener el activo
        np.where(z >= k, -1, np.where(z <= floor, 1, 0)),
    ).astype(np.int8)
    directive[0] = 1  # se empieza dentro del activo
    pos = np.where(directive != 0, np.arange(len(directive)), 0)
    return directive[np.maximum.accumulate(pos)]


def base_asset(price: np.ndarray, state: np.ndarray, fee: float) -> tuple[float, int]:
    """Activo base acumulado en %, y numero de operaciones. Mantener es 0 % por construccion.

    El camino de estado no depende de la comision, asi que se calcula una vez y se cobra
    despues: por eso esta funcion recibe `state` en vez de recalcular la regla por cada tarifa.
    """
    flips = np.flatnonzero(np.diff(state)) + 1
    coins, cash, inside = 1.0, 0.0, True
    for i in flips:
        if state[i] > 0:
            coins, cash, inside = cash / price[i] * (1.0 - fee), 0.0, True
        else:
            cash, coins, inside = coins * price[i] * (1.0 - fee), 0.0, False
    final = coins if inside else cash / price[-1]
    return (final - 1.0) * 100.0, len(flips)


def shuffle_path(logp: np.ndarray, volume: np.ndarray, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Retornos barajados con SU volumen viajando en la misma permutacion."""
    rng = np.random.default_rng(seed)
    r = np.diff(logp)
    perm = rng.permutation(len(r))
    fake = np.concatenate(([logp[0]], logp[0] + np.cumsum(r[perm])))
    fake_vol = np.concatenate(([volume[0]], volume[1:][perm]))
    return fake, fake_vol


def arms() -> list[tuple[int, float, str]]:
    return [(n, k, re) for n in WINDOWS for k in KS for re in REENTRIES]


def run_year(data_dir: str, pair: str, year: int, args) -> dict:
    """Todo lo que un ano aporta: precios, volumen, puerta y los caminos barajados."""
    coarse, logp, is_open, inside = lh.build(data_dir, pair, year, args.move, args.look, args.delay)
    volume = coarse["volume"].to_numpy(dtype=float)
    price = np.exp(logp)
    out = {
        "price": price,
        "volume": volume,
        "gated": is_open,
        "ungated": inside,
        "cover": float(is_open.mean()),
        "hold_eur": (price[inside][-1] / price[inside][0] - 1.0) * 100.0,
        "controls": [],
    }
    for s in range(args.shuffles):
        fake_logp, fake_vol = shuffle_path(logp, volume, 1000 * year + s)
        fake_price = np.exp(fake_logp)
        fake_gate = lh.gate_on(coarse, fake_price, args.move, args.look, args.delay) & inside
        out["controls"].append((fake_price, fake_vol, fake_gate, inside))
    del coarse
    return out


def score(price, volume, gate, n, k, reentry, fee) -> tuple[float, int]:
    return base_asset(price, state_path(vwap_z(price, volume, n), gate, k, reentry), fee)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir")
    ap.add_argument("--pair", default="XBTEUR")
    ap.add_argument("--years", nargs="*", type=int, default=[2024, 2025])
    ap.add_argument("--fee", type=float, default=0.40, help="Comision por pierna, en porcentaje.")
    ap.add_argument("--move", type=float, default=0.07)
    ap.add_argument("--look", type=int, default=7)
    ap.add_argument("--delay", type=int, default=0)
    ap.add_argument("--shuffles", type=int, default=5)
    args = ap.parse_args()

    fee = args.fee / 100.0
    print("[regla] VWAP z: vende en z>=+k, recompra en z<=-k (banda) o z<=0 (ancla)")
    print("        largo-o-plano, por defecto DENTRO; mantener = 0 % de activo base")
    print(f"        {len(arms())} brazos  comision {args.fee:.2f} %/pierna  {args.shuffles} barajados/ano")
    print(f"[puerta] impulso m={args.move:.2f} k={args.look} delay={args.delay} d, velas de {BAR} min")

    data = {}
    for year in args.years:
        data[year] = run_year(args.data_dir, args.pair, year, args)
        d = data[year]
        print(f"  {year}: puerta abierta {100 * d['cover']:.0f} %   mantener {d['hold_eur']:+.1f} % en euros")

    for mode in ("gated", "ungated"):
        title = "CON la puerta lateral" if mode == "gated" else "SIN puerta (el ano entero)"
        print("")
        print(f"[{title}]  activo base contra mantener; f0 = comision cero")
        head = f"  {'ventana':>8}{'k':>5}{'reentrada':>11}"
        for year in args.years:
            head += f"{str(year) + ' f0':>11}{str(year) + ' fee':>11}{'ops':>7}"
        head += f"{'ctrl f0 mediana':>17}"
        print(head)
        rows = []
        for n, k, reentry in arms():
            row = f"  {WIN_LABEL[n]:>8}{k:>5.1f}{reentry:>11}"
            free_by_year, paid_by_year, ctrl_free = [], [], []
            for year in args.years:
                d = data[year]
                gate = d[mode]
                f0, ops = score(d["price"], d["volume"], gate, n, k, reentry, 0.0)
                fp, _ = score(d["price"], d["volume"], gate, n, k, reentry, fee)
                free_by_year.append(f0)
                paid_by_year.append(fp)
                cs = [
                    score(p, v, (g if mode == "gated" else ins), n, k, reentry, 0.0)[0]
                    for p, v, g, ins in d["controls"]
                ]
                ctrl_free.append(float(np.median(cs)))
                row += f"{f0:>+10.1f}%{fp:>+10.1f}%{ops:>7}"
            row += f"{float(np.median(ctrl_free)):>+16.1f}%"
            print(row)
            rows.append((min(free_by_year), min(paid_by_year), min(ctrl_free)))

        n_arms = len(rows)
        pos_free = sum(1 for f, _, _ in rows if f > 0)
        pos_paid = sum(1 for _, p, _ in rows if p > 0)
        pos_ctrl = sum(1 for _, _, c in rows if c > 0)
        print(
            f"  positivos en TODOS los anos: comision cero {pos_free}/{n_arms}, "
            f"comision real {pos_paid}/{n_arms}, CONTROL barajado {pos_ctrl}/{n_arms}"
        )
        print(
            f"  mejor peor-ano: comision cero {max(f for f, _, _ in rows):+.1f} %, "
            f"comision real {max(p for _, p, _ in rows):+.1f} %, "
            f"control {max(c for _, _, c in rows):+.1f} %"
        )

    print("")
    print("[lectura] la columna que decide es la del CONTROL. La regla solo es reversion del")
    print("          mercado en la parte que SUPERA al camino barajado; lo que iguala al control")
    print("          lo fabrica la propia regla al condicionar. Y la comparacion con la comision")
    print("          solo vale ya convertida a largo-o-plano, que es lo que hay en estas columnas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
