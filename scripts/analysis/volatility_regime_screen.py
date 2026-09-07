"""¿Son los episodios de ATR alto mas DIRECCIONALES que los de ATR bajo?

Read-only y temporal. Lee los CSV OHLCVT de Kraken directamente, sin base de datos.
No simula nada: no hay motor, ni configs, ni comisiones. Es una medida descriptiva del
mercado, no del bot.

La pregunta. En terminos de acumulacion de activo el enemigo del bot NO es la volatilidad:
una oscilacion violenta que vuelve al mismo precio es el mejor escenario posible para un
trailing stop (mucho recorrido, mismo punto de partida). Lo que le hace perder base es
vender y que el precio no vuelva, es decir la direccion sostenida. Por eso "evitar la alta
volatilidad" solo tiene mecanismo si se cumple esto:

    los tramos de ATR alto son desproporcionadamente TENDENCIALES en vez de OSCILATORIOS

Si el ATR alto solo significa "los mismos vaivenes pero mas grandes", entonces evitarlo es
tirar justo el terreno donde el stop mas gana, y la idea se cae sin necesidad de simular.

El estadistico. Ratio de eficiencia (Kaufman) sobre los cierres de una ventana de N velas:

    ER = |close[i+N] - close[i]| / sum(|close[j+1] - close[j]|)

ER cerca de 1 es tendencia pura (todo el recorrido fue en la misma direccion); cerca de 0 es
oscilacion pura (mucho camino, ningun avance). Es adimensional: numerador y denominador
escalan igual con sigma, asi que se puede comparar entre niveles de volatilidad, que es
precisamente lo que aqui hace falta.

El nulo. Un paseo aleatorio sin deriva no da ER = 0, da ER ~ 1/sqrt(N):

    E|neto| = sigma*sqrt(N)*sqrt(2/pi)      E[sum|pasos|] = N*sigma*sqrt(2/pi)

Sin esa referencia un ER de 0.10 parece "plano" cuando a N=96 es exactamente lo que da el
azar. Se imprime en cada horizonte, y lo que importa es ER/nulo, no ER.

Ademas del ER se reporta el retorno NETO CON SIGNO por nivel, porque en base-asset los
episodios direccionales al alza y a la baja no son simetricos para el bot: vender y
recomprar mas arriba pierde base, vender y recomprar mas abajo la gana. Un ATR alto
direccional PERO sesgado al alza es el peor caso, y es el que justificaria la puerta.

Metodo y sus limites:

  * ATR de Wilder sobre las velas de 15 min, el mismo periodo que produccion (ATR_PERIOD),
    y niveles LL/LV/MV/HV/HH por los percentiles de ATR/close (atr_ratio_percentiles), que
    es la MISMA particion que usa get_volatility_level. Asi "HV" significa aqui lo que
    significa en el bot.
  * Ventanas NO SOLAPADAS (paso = N). Solapar multiplicaria la n aparente sin anadir
    informacion, y este estudio ya ha leido ruido como senal por ese camino. La n impresa
    es la n real.
  * Cada ventana se clasifica por el ATR de su PRIMERA vela, que es lo unico que un bot
    sabria en ese instante. La clasificacion no mira hacia adelante.
  * Lo que si mira hacia adelante son las FRONTERAS de nivel, calculadas sobre todo el
    fichero. Es aceptable para un descriptivo (no es una regla operable) pero significa que
    los numeros de aqui son un techo, no un backtest.

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):

    PYTHONPATH=. python scripts/analysis/volatility_regime_screen.py "C:/Dev/Kraken OHLCVT"
    PYTHONPATH=. python scripts/analysis/volatility_regime_screen.py DIR --pair USDCEUR
"""

import argparse
import math
import os

import numpy as np
import pandas as pd

from core.config import ATR_PERIOD
from trading.market_analyzer import _wilder_atr_from_scratch, atr_ratio_percentiles

CSV_COLUMNS = ["time", "open", "high", "low", "close", "volume", "trades"]
LEVELS = ("LL", "LV", "MV", "HV", "HH")

# Horizontes en velas de 15 min: 1h, 4h, 12h, 1d, 3d.
HORIZONS = (4, 16, 48, 96, 288)


def load_csv(path: str, t0: int, t1: int) -> pd.DataFrame:
    """Lee un CSV grande por trozos y se queda solo con [t0, t1]; los ficheros vienen ordenados."""
    parts = []
    for chunk in pd.read_csv(path, header=None, names=CSV_COLUMNS, chunksize=1_000_000):
        keep = chunk[(chunk["time"] >= t0) & (chunk["time"] <= t1)]
        if len(keep):
            parts.append(keep)
        if len(chunk) and int(chunk["time"].iloc[-1]) > t1:
            break
    if not parts:
        raise ValueError(f"{path}: sin velas en la ventana")
    df = pd.concat(parts, ignore_index=True).drop_duplicates(subset=["time"]).sort_values("time")
    return df.reset_index(drop=True)


def frame(path: str, t0: int, t1: int) -> pd.DataFrame:
    """Velas de 15 min con su ATR de Wilder: la vista de volatilidad de produccion."""
    df = load_csv(path, t0, t1)
    deltas = df["time"].diff().dropna()
    holes = deltas[deltas != 900]
    if len(holes):
        print(f"    AVISO: {len(holes)} saltos en la serie (mayor: {int(holes.max()) // 900} velas)")
    df["atr"] = _wilder_atr_from_scratch(df, ATR_PERIOD)
    df["dtime"] = pd.to_datetime(df["time"], unit="s")
    return df.dropna(subset=["atr"]).sort_values("time").reset_index(drop=True)


def classify(df: pd.DataFrame) -> np.ndarray:
    """Nivel de volatilidad por vela, con la misma particion que get_volatility_level."""
    p20, p50, p80, p95 = atr_ratio_percentiles(df)
    print(f"  fronteras ATR/close: p20={p20:.5f} p50={p50:.5f} p80={p80:.5f} p95={p95:.5f}")
    ratio = (df["atr"] / df["close"]).to_numpy(dtype=float)
    out = np.full(len(df), "HH", dtype=object)
    out[ratio < p95] = "HV"
    out[ratio < p80] = "MV"
    out[ratio < p50] = "LV"
    out[ratio < p20] = "LL"
    return out


def windows(closes: np.ndarray, levels: np.ndarray, n: int) -> list[tuple[str, float, float]]:
    """(nivel de la primera vela, ER, retorno neto %) por ventana NO solapada de n velas."""
    out = []
    for start in range(0, len(closes) - n, n):
        seg = closes[start : start + n + 1]
        path = float(np.abs(np.diff(seg)).sum())
        if path <= 0.0 or seg[0] <= 0.0:
            continue
        net = float(seg[-1] - seg[0])
        out.append((levels[start], abs(net) / path, 100.0 * net / seg[0]))
    return out


def report(closes: np.ndarray, levels: np.ndarray) -> None:
    for n in HORIZONS:
        rows = windows(closes, levels, n)
        if not rows:
            continue
        null = 1.0 / math.sqrt(n)
        hours = n * 15 / 60
        print(f"\n  Horizonte {n} velas ({hours:g} h) - nulo del paseo aleatorio ER = {null:.3f}")
        print(
            f"    {'nivel':<6} {'n':>5} {'ER medio':>9} {'ER/nulo':>8} {'ER med.':>8} {'ret medio %':>12} {'ret med. %':>11}"
        )
        for lvl in LEVELS:
            ers = [er for level, er, _ in rows if level == lvl]
            rets = [ret for level, _, ret in rows if level == lvl]
            if not ers:
                print(f"    {lvl:<6} {0:>5}          -        -        -            -           -")
                continue
            print(
                f"    {lvl:<6} {len(ers):>5} {np.mean(ers):>9.3f} {np.mean(ers) / null:>8.2f} "
                f"{np.median(ers):>8.3f} {np.mean(rets):>12.2f} {np.median(rets):>11.2f}"
            )
        allers = [er for _, er, _ in rows]
        print(
            f"    {'TODOS':<6} {len(allers):>5} {np.mean(allers):>9.3f} {np.mean(allers) / null:>8.2f} {np.median(allers):>8.3f}"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="¿Es el ATR alto mas tendencial que el ATR bajo?")
    ap.add_argument("data_dir", help="Carpeta con <PAIR>_15.csv")
    ap.add_argument("--pair", default="XBTEUR")
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--end", default="2026-03-31")
    args = ap.parse_args()

    t0 = int(pd.Timestamp(args.start).timestamp())
    t1 = int(pd.Timestamp(args.end).timestamp()) + 86_399

    print(f"[datos] {args.pair} 15m en {args.data_dir}, {args.start}..{args.end}, ATR_PERIOD={ATR_PERIOD}")
    df = frame(os.path.join(args.data_dir, f"{args.pair}_15.csv"), t0, t1)
    print(f"  {len(df)} velas con ATR  {df.iloc[0]['dtime']}..{df.iloc[-1]['dtime']}")

    levels = classify(df)
    closes = df["close"].to_numpy(dtype=float)

    print("\n[ER por nivel de volatilidad]  ER alto = tendencia, ER bajo = oscilacion")
    report(closes, levels)

    print(
        "\n[lectura] La hipotesis 'evitar la alta volatilidad' necesita que ER/nulo CREZCA de LL a HH.\n"
        "          Si es plano, el ATR alto son solo vaivenes mas grandes y el trailing stop los quiere.\n"
        "          El retorno medio con signo dice si ademas el tramo direccional se sesga al alza,\n"
        "          que es el caso que de verdad hace perder base al bot."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
