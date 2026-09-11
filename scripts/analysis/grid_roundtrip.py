"""El grid: ¿hay oscilacion que cosechar por encima de la comision, y en que par?

Read-only y temporal. Lee los CSV OHLCVT de Kraken directamente, sin base de datos y sin motor.

La pregunta, y por que es distinta de las veintitres anteriores. Un grid NO predice nada. No
necesita que ninguna caracteristica causal anticipe el retorno futuro, que es lo que el estudio
lleva veintitres avenidas descartando. Solo necesita que el precio recorra ida y vuelta una
amplitud mayor que el peaje. Es la primera hipotesis del estudio que no depende de predecir.

La aritmetica minima, antes de medir nada: un viaje completo paga DOS comisiones. A 0.40 % por
pierna el espaciado tiene que superar 0.8 % solo para empatar; a 0.25 % (maker), 0.5 %. Y no hay
escapatoria por ninguno de los dos lados -- apretar el grid da mas viajes y cada uno muere en
comisiones, abrirlo exige excursiones grandes, que es cuando el rango se rompe.

El resultado que hace falta recordar para leer esto. Sobre un camino SIN MEMORIA y SIN DERIVA el
resultado total de un grid a comision cero es exactamente CERO: es una martingala. Lo realizado
(los viajes cerrados, siempre positivos, la tasa de acierto que se ensena) y el inventario no
realizado (negativo cuando el precio ha bajado a traves del grid) se cancelan termino a termino.
Un grid no extrae valor por el hecho de oscilar: lo extrae solo si la oscilacion es REVERSION de
verdad a la escala de su espaciado.

Cuidado con leer la columna del control como si fuera ese cero. Barajar CONSERVA la deriva --
la permutacion no cambia la suma de los retornos, asi que el camino falso termina en el mismo
precio que el real -- y con deriva un grid tiene un lastre estructural de caja que no tiene nada
que ver con la oscilacion: `centrado` se planta en 0.5 cuando el precio esta en su ancla, de
modo que en un ano que multiplica por 2.3 se deja la mitad de la subida y marca -29 % sin haber
hecho nada mal. Ese mismo lastre esta en las dos columnas. Lo unico que el barajado destruye es
la DEPENDENCIA SERIAL, asi que lo que mide la prueba no es el nivel de ninguna de las dos
columnas: es la DISTANCIA entre ellas.

La regla, fijada antes de correr nada:

  * ANCLA      mediana rodante del log-precio sobre 672 velas (1 semana), con datos hasta t
               inclusive. Un ancla que se adapta es lo que hace un grid real con recentrado
               automatico, y es lo que convierte la prueba en "oscilacion contra tendencia" en
               vez de "donde tuvimos la suerte de anclar".
  * FORMA      fraccion objetivo de activo base como funcion de la distancia al ancla:
                 centrado  f = recorte(0.5 - (x-A)/(2W))   mitad y mitad en el ancla
                 techo     f = recorte(1.0 - (x-A)/W)      lleno en el ancla y por debajo, que
                                                           es la version nativa de acumular
                                                           activo base: vende repuntes y
                                                           recompra, sin soltar el nucleo
  * ESPACIADO  se opera cuando el nivel del grid se ha movido un escalon entero, es decir
               cuando la fraccion objetivo se separa `s x pendiente` de la que habia en la
               ultima operacion. Eso es exactamente una escalera de espaciado `s` en log-precio,
               y trae la histeresis de serie (sin ella el precio vibra sobre un nivel y factura
               comisiones sin moverse).
  * PUERTA     con la puerta cerrada NO SE OPERA: la posicion se congela donde estaba y el grid
               se reanuda al reabrir. Es la unica semantica defendible aqui, y se midio antes de
               elegirla: `impulso` conmuta 567 veces en 2024, asi que obligar a volver a activo
               entero en cada cierre fabricaba 287 unidades de rotacion contra 24, con
               operaciones que movian el 44 % de la cartera cuando el escalon del grid es el
               8 %. Eso no mide un grid, mide la puerta. Se corre con puerta y sin ella.

Puntuacion. Activo base acumulado contra mantener, CON EL INVENTARIO VALORADO -- que es todo el
asunto: puntuar solo lo realizado es precisamente el espejismo. Se imprime ademas el resultado
en EUROS, que es lo que ensena el panel de un grid comercial, para que la distancia entre las
dos columnas se vea en vez de contarse.

Las horas de operacion se cobran al CIERRE de la vela en la que el nivel cambia, no al precio
del nivel: un grid con ordenes limitadas llenaria en el nivel y pagaria maker. Esa diferencia no
se estima -- se contesta con la comision de equilibrio, que es la cifra que decide y que el
script calcula por biseccion.

El control. Los mismos retornos barajados (misma distribucion marginal, misma volatilidad
agregada, cero dependencia serial) con la misma puerta encima. Si el grid gana lo mismo sobre un
camino sin memoria, lo que gana no es reversion.

La rejilla es de 24 brazos (3 anchuras x 4 espaciados x 2 formas), fijada arriba y corrida
entera: se informa la DISTRIBUCION y la tasa base del control, nunca la cabeza de la tabla.

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):
  PYTHONPATH=. python scripts/analysis/grid_roundtrip.py "C:/Dev/Kraken OHLCVT"
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lateral_horizon as lh

BAR = 15
ANCHOR = 672  # velas de 15 min = 1 semana
# Rejilla fijada antes de correr. Los espaciados abrazan el umbral de 0.8 % (ida y vuelta a
# comision taker): dos por debajo, dos por encima.
WIDTHS = (0.05, 0.10, 0.20)
SPACINGS = (0.004, 0.008, 0.016, 0.032)
SHAPES = ("centrado", "techo")


def anchor_log(logp: np.ndarray) -> np.ndarray:
    """Mediana rodante del log-precio, con datos hasta t inclusive."""
    return pd.Series(logp).rolling(ANCHOR).median().to_numpy(dtype=float)


def target_fraction(logp: np.ndarray, anchor: np.ndarray, w: float, shape: str) -> np.ndarray:
    """Fraccion de activo base que la forma pide en cada vela. Sin puerta: la puerta no entra aqui."""
    d = logp - anchor
    f = 0.5 - d / (2.0 * w) if shape == "centrado" else 1.0 - d / w
    return np.clip(np.nan_to_num(f, nan=1.0), 0.0, 1.0)


def tradable(anchor: np.ndarray, is_open: np.ndarray) -> np.ndarray:
    """Velas en las que el grid puede operar: puerta abierta y ancla ya formada."""
    return is_open & ~np.isnan(anchor)


def slope(w: float, shape: str) -> float:
    """Cuanta fraccion mueve un escalon de log-precio: la pendiente de la forma."""
    return 1.0 / (2.0 * w) if shape == "centrado" else 1.0 / w


def trade_indices(f_target: np.ndarray, step: float, allowed: np.ndarray) -> np.ndarray:
    """Velas en las que el nivel del grid se ha movido un escalon entero desde la ultima operacion.

    Es un barrido secuencial y no hay primitiva de numpy que lo haga: el disparo depende de
    donde quedo la operacion anterior, que es justamente la histeresis. Se recorre sobre listas
    de Python a proposito, que indexar el array escalar a escalar cuesta un orden mas.

    Con la puerta cerrada no se opera y la referencia NO se actualiza: al reabrir, el grid
    compara contra el nivel de su ultima operacion real, que es lo que hace un bot pausado.
    """
    ft = f_target.tolist()
    ok = allowed.tolist()
    last = ft[0]
    idx = []
    for i in range(1, len(ft)):
        if not ok[i]:
            continue
        v = ft[i]
        if v - last >= step or last - v >= step:
            idx.append(i)
            last = v
    return np.asarray(idx, dtype=np.int64)


def replay(price: np.ndarray, f_target: np.ndarray, idx: np.ndarray, fee: float, p0: float) -> tuple[float, float, float]:
    """(activo base en % contra mantener, euros en %, rotacion) de ejecutar esas operaciones.

    Se empieza con 1 moneda y 0 euros, asi que mantener es 0 % por construccion. El calendario de
    operaciones NO depende de la comision -- tras rebalancear la fraccion real vale exactamente
    la objetivo, y entre operaciones deriva igual se haya pagado lo que se haya pagado -- de modo
    que el barrido caro se hace una vez y aqui solo se cobra.
    """
    coins, cash = 1.0, 0.0
    turnover = 0.0
    for i in idx:
        p = price[i]
        value = coins * p + cash
        if value <= 0.0:
            break
        g = f_target[i]
        gap = abs(g - coins * p / value)
        turnover += gap
        value -= gap * value * fee
        coins = g * value / p
        cash = value - coins * p
    base = coins + cash / price[-1]
    # `p0` es el precio de la PRIMERA vela del ano, no la del calentamiento: el activo base se
    # mide contra mantener y no le afecta el arranque, pero el retorno en euros si, y `mantener`
    # se informa sobre el ano. Dividir por price[0] mezclaba dos ventanas distintas.
    eur = (coins * price[-1] + cash) / p0
    return (base - 1.0) * 100.0, (eur - 1.0) * 100.0, turnover


def breakeven_fee(price: np.ndarray, f_target: np.ndarray, idx: np.ndarray, p0: float) -> float:
    """Comision por pierna que lleva el activo base a cero, en puntos basicos. NaN si ya es negativo."""
    if replay(price, f_target, idx, 0.0, p0)[0] <= 0.0:
        return float("nan")
    lo, hi = 0.0, 0.05
    if replay(price, f_target, idx, hi, p0)[0] > 0.0:
        return float("inf")
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        if replay(price, f_target, idx, mid, p0)[0] > 0.0:
            lo = mid
        else:
            hi = mid
    return 10_000.0 * 0.5 * (lo + hi)


def efficiency(logp: np.ndarray, n: int) -> np.ndarray:
    """Ratio de Kaufman: desplazamiento neto sobre camino recorrido, en ventanas de `n` velas.

    Es la medida que decide si mas volatilidad sirve de algo: un grid no quiere movimiento, quiere
    movimiento que NO vaya a ninguna parte. Cerca de 1 el precio va en linea recta (tendencia,
    veneno para un grid); cerca de 0 se pasea sin desplazarse (oscilacion, que es lo cosechable).
    """
    x = pd.Series(logp)
    net = (x - x.shift(n)).abs()
    path = x.diff().abs().rolling(n).sum()
    return (net / path.replace(0.0, np.nan)).to_numpy(dtype=float)


def arms() -> list[tuple[float, float, str]]:
    return [(w, s, sh) for w in WIDTHS for s in SPACINGS for sh in SHAPES]


def run_year(data_dir: str, pair: str, year: int, args) -> dict:
    coarse, logp, is_open, inside = lh.build(data_dir, pair, year, args.move, args.look, args.delay)
    price = np.exp(logp)
    first = int(np.argmax(inside))  # primera vela del ano; lo anterior es calentamiento
    er = efficiency(logp, 96)
    out = {
        "logp": logp,
        "price": price,
        "anchor": anchor_log(logp),
        "gated": is_open,
        "ungated": inside,
        "p0": float(price[first]),
        "cover": float(is_open.mean()),
        "hold_eur": (price[inside][-1] / price[inside][0] - 1.0) * 100.0,
        "er_open": float(np.nanmedian(er[is_open])),
        "er_shut": float(np.nanmedian(er[inside & ~is_open])),
        "amp_open": float(np.nanmedian(np.abs(np.diff(logp, prepend=logp[0]))[is_open]) * 100.0),
        "amp_shut": float(np.nanmedian(np.abs(np.diff(logp, prepend=logp[0]))[inside & ~is_open]) * 100.0),
        "controls": [],
    }
    for s in range(args.shuffles):
        fake_logp, fake_gate = lh.shuffled(coarse, logp, inside, args.move, args.look, args.delay, 1000 * year + s)
        fake_price = np.exp(fake_logp)
        out["controls"].append(
            {
                "logp": fake_logp,
                "price": fake_price,
                "anchor": anchor_log(fake_logp),
                "gated": fake_gate,
                "ungated": inside,
                "p0": float(fake_price[first]),
            }
        )
    del coarse
    return out


def evaluate(path: dict, mode: str, w: float, sp: float, shape: str, fee: float) -> dict:
    f_target = target_fraction(path["logp"], path["anchor"], w, shape)
    idx = trade_indices(f_target, sp * slope(w, shape), tradable(path["anchor"], path[mode]))
    free, eur_free, turn = replay(path["price"], f_target, idx, 0.0, path["p0"])
    paid, eur_paid, _ = replay(path["price"], f_target, idx, fee, path["p0"])
    return {
        "free": free,
        "paid": paid,
        "eur_paid": eur_paid,
        "eur_free": eur_free,
        "ops": len(idx),
        "turn": turn,
        "be": breakeven_fee(path["price"], f_target, idx, path["p0"]),
    }


def table(pair: str, mode: str, data: dict, years: list[int], fee: float) -> None:
    title = "CON la puerta lateral" if mode == "gated" else "SIN puerta (el ano entero)"
    print("")
    print(f"[{pair} -- {title}]  activo base contra mantener; f0 = comision cero")
    head = f"  {'W':>5}{'s':>7}{'forma':>10}"
    for year in years:
        head += f"{str(year) + ' f0':>11}{str(year) + ' fee':>11}{'ops':>7}"
    head += f"{'ctrl f0':>10}{'equilibrio':>12}"
    print(head)

    rows = []
    for w, sp, shape in arms():
        row = f"  {100 * w:>4.0f}%{100 * sp:>6.1f}%{shape:>10}"
        free_y, paid_y, ctrl_y, be_y = [], [], [], []
        for year in years:
            d = data[year]
            r = evaluate(d, mode, w, sp, shape, fee)
            free_y.append(r["free"])
            paid_y.append(r["paid"])
            be_y.append(r["be"])
            ctrl_y.append(
                float(np.median([evaluate(c, mode, w, sp, shape, fee)["free"] for c in d["controls"]]))
            )
            row += f"{r['free']:>+10.1f}%{r['paid']:>+10.1f}%{r['ops']:>7}"
        worst_free, worst_paid, worst_ctrl = min(free_y), min(paid_y), min(ctrl_y)
        be = min(be_y) if worst_free > 0 and not any(np.isnan(b) for b in be_y) else float("nan")
        be_txt = "-" if np.isnan(be) else (">500 pb" if np.isinf(be) else f"{be:.1f} pb")
        print(row + f"{worst_ctrl:>+9.1f}%{be_txt:>12}")
        rows.append((worst_free, worst_paid, worst_ctrl))

    n = len(rows)
    print(
        f"  positivos en TODOS los anos: comision cero {sum(1 for f, _, _ in rows if f > 0)}/{n}, "
        f"comision real {sum(1 for _, p, _ in rows if p > 0)}/{n}, "
        f"CONTROL barajado {sum(1 for _, _, c in rows if c > 0)}/{n}"
    )
    print(
        f"  mejor peor-ano: comision cero {max(f for f, _, _ in rows):+.1f} %, "
        f"comision real {max(p for _, p, _ in rows):+.1f} %, "
        f"control {max(c for _, _, c in rows):+.1f} %   |   "
        f"mediana del control {np.median([c for _, _, c in rows]):+.1f} %"
    )


def mirage(pair: str, data: dict, years: list[int], fee: float) -> None:
    """El brazo que mejor queda EN EUROS, y lo que ese mismo brazo hizo en activo base."""
    best, best_eur = None, -1e18
    for w, sp, shape in arms():
        rs = [evaluate(data[y], "gated", w, sp, shape, fee) for y in years]
        e = min(r["eur_paid"] for r in rs)
        if e > best_eur:
            best, best_eur = (w, sp, shape, rs), e
    w, sp, shape, rs = best
    print("")
    print(f"[{pair} -- el espejismo]  el brazo con puerta que mejor queda en euros: W={100 * w:.0f}% s={100 * sp:.1f}% {shape}")
    for year, r in zip(years, rs):
        print(
            f"  {year}: en EUROS {r['eur_paid']:+.1f} %   mantener {data[year]['hold_eur']:+.1f} %   "
            f"en ACTIVO BASE contra mantener {r['paid']:+.1f} %"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir")
    ap.add_argument("--pairs", nargs="*", default=["XBTEUR", "ETHEUR", "SOLEUR"])
    ap.add_argument("--years", nargs="*", type=int, default=[2024, 2025])
    ap.add_argument("--fee", type=float, default=0.40, help="Comision por pierna, en porcentaje.")
    ap.add_argument("--move", type=float, default=0.07)
    ap.add_argument("--look", type=int, default=7)
    ap.add_argument("--delay", type=int, default=0)
    ap.add_argument("--shuffles", type=int, default=5)
    args = ap.parse_args()

    fee = args.fee / 100.0
    print("[regla] grid de rebalanceo hacia la mediana semanal, largo-o-plano, sin apalancar")
    print("        activo base contra mantener CON EL INVENTARIO VALORADO; mantener = 0 %")
    print(f"        {len(arms())} brazos  comision {args.fee:.2f} %/pierna  {args.shuffles} barajados/ano")
    print(f"        ida y vuelta = {2 * args.fee:.2f} %: por debajo de ese espaciado no hay nada que cosechar")
    print(f"[puerta] impulso m={args.move:.2f} k={args.look} delay={args.delay} d, velas de {BAR} min")

    for pair in args.pairs:
        print("")
        print(f"=== {pair} ===")
        data = {}
        for year in args.years:
            data[year] = run_year(args.data_dir, pair, year, args)
            d = data[year]
            print(f"  {year}: puerta abierta {100 * d['cover']:.0f} %   mantener {d['hold_eur']:+.1f} % en euros")
            print(
                f"        amplitud mediana por vela: dentro {d['amp_open']:.3f} % / fuera {d['amp_shut']:.3f} %"
                f"   |   eficiencia a 1 dia: dentro {d['er_open']:.3f} / fuera {d['er_shut']:.3f}"
            )
        for mode in ("gated", "ungated"):
            table(pair, mode, data, args.years, fee)
        mirage(pair, data, args.years, fee)
        del data

    print("")
    print("[lectura] el NIVEL de las columnas no dice nada: barajar conserva la deriva, y con deriva")
    print("          un grid arrastra un lastre de caja estructural que sale igual en el mercado real")
    print("          y en el control. Lo que mide la prueba es la DISTANCIA entre las dos, que es lo")
    print("          unico que el barajado destruye: la dependencia serial. Y esa distancia, dividida")
    print("          por la rotacion, es la comision de equilibrio -- la unica cifra que decide si un")
    print("          grid es viable en Kraken o solo para quien cobra por poner la orden.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
