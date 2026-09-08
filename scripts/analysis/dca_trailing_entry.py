"""¿Y si la aportacion mensual entra con el trailing del bot: acompañar la caida y comprar en el rebote?

Read-only y temporal. Lee el CSV OHLCVT de 15 min de Kraken, sin base de datos.

`dca_overlay.py` midio limites FIJOS (-5 %, -10 %) y salieron negativos, pero eso NO cierra
esta pregunta: un limite fijo no entra nunca en un mes que solo sube, mientras que un
trailing entra siempre, porque cualquier rebote lo dispara. Es otro mecanismo, con otra
compensacion, y merece su propia medicion.

La regla, que es la activacion de compra del bot aplicada solo a la entrada:

  1. la aportacion llega en la primera vela del mes
  2. (opcional) esperar a que el precio caiga `caida` x ATR desde la apertura del mes
  3. seguir el minimo corriente hacia abajo
  4. comprar cuando el precio suba `rebote` x ATR desde ese minimo
  5. si el mes acaba sin disparar, comprar a mercado en el cierre (hay que desplegar el
     mismo dinero que las demas ramas, o no serian comparables)

La compensacion, en una frase: pagas `rebote` x ATR SIEMPRE, y a cambio te llevas la caida
intramensual CUANDO la hay. En un mes alcista el rebote salta enseguida y el coste es
pequeño; en uno bajista compras cerca del minimo. Lo que puede estropearlo es la patologia
conocida del bot en el otro lado: en una caida sostenida el primer rebote no es el suelo.

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):

    PYTHONPATH=. python scripts/analysis/dca_trailing_entry.py "C:/Dev/Kraken OHLCVT/XBTEUR_15.csv"
"""

import argparse

import pandas as pd

from core.config import ATR_PERIOD
from trading.market_analyzer import _wilder_atr_from_scratch

CSV_COLUMNS = ["time", "open", "high", "low", "close", "volume", "count"]
FEE = 0.4
CONTRIB = 100.0
WINDOWS = (("2018-2025", "2018-01-01"), ("2021-2025", "2021-01-01"), ("2023-2025", "2023-01-01"))
ARMS = (
    ("rebote", 0.0, 0.5),
    ("rebote", 0.0, 1.0),
    ("rebote", 0.0, 2.0),
    ("rebote", 0.0, 3.0),
    ("caida", 1.0, 1.0),
    ("caida", 2.0, 1.0),
    ("caida", 3.0, 1.0),
    ("caida", 2.0, 2.0),
)


def build_frame(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, header=None, names=CSV_COLUMNS)
    df = df.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
    df["atr"] = _wilder_atr_from_scratch(df, ATR_PERIOD)
    df["dtime"] = pd.to_datetime(df["time"], unit="s")
    return df.dropna(subset=["atr"]).reset_index(drop=True)


def entry_price(bars, fall_k: float, bounce_k: float) -> tuple[float, int]:
    """Precio y vela de entrada de la aportacion del mes; cierre del mes si nunca dispara."""
    ref = bars[0][0]
    activated = fall_k <= 0.0
    low = ref
    for i, (price, atr) in enumerate(bars):
        if not activated:
            if price <= ref - fall_k * atr:
                activated, low = True, price
            continue
        low = min(low, price)
        if price >= low + bounce_k * atr:
            return price, i
    return bars[-1][0], len(bars) - 1


def run(months: list[list[tuple[float, float]]], fall_k: float, bounce_k: float, fee: float, final: float) -> dict:
    coins, late, waits, better = 0.0, 0, [], 0
    for bars in months:
        price, idx = entry_price(bars, fall_k, bounce_k)
        coins += (CONTRIB * (1.0 - fee)) / price
        late += idx == len(bars) - 1
        waits.append(idx / 4.0)  # velas de 15 min -> horas
        better += price < bars[0][0]
    waits.sort()
    return {
        "value": coins * final,
        "contributed": CONTRIB * len(months),
        "late": late,
        "wait": waits[len(waits) // 2],
        "better": better,
    }


def report(frame: pd.DataFrame, fee: float) -> None:
    for label, start in WINDOWS:
        part = frame[frame["dtime"] >= pd.Timestamp(start)]
        months = [
            list(zip(g["close"].tolist(), g["atr"].tolist(), strict=True))
            for _, g in part.groupby(part["dtime"].dt.to_period("M"))
        ]
        final = float(part.iloc[-1]["close"])
        base = run(months, 0.0, 0.0, fee, final)  # rebote 0 => compra en la primera vela
        print(f"\n[{label}]  {len(months)} meses, {base['contributed']:.0f} EUR aportados")
        print(
            f"  {'variante':<22} {'valor final':>12} {'x aportado':>11} {'vs dia 1':>10} "
            f"{'espera':>9} {'mejor':>8} {'al cierre':>10}"
        )
        print(
            f"  {'dia 1':<22} {base['value']:>11,.0f}E {base['value'] / base['contributed']:>10.2f}x {0.0:>+9.2f}% {'-':>10}"
        )
        for kind, fall_k, bounce_k in ARMS:
            r = run(months, fall_k, bounce_k, fee, final)
            name = f"rebote {bounce_k:g}xATR" if kind == "rebote" else f"caida {fall_k:g} + rebote {bounce_k:g}"
            print(
                f"  {name:<22} {r['value']:>11,.0f}E {r['value'] / r['contributed']:>10.2f}x "
                f"{(r['value'] / base['value'] - 1.0) * 100.0:>+9.2f}% {r['wait']:>7.1f} h "
                f"{r['better']:>5}/{len(months)} {r['late']:>7}/{len(months)}"
            )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--fee", type=float, default=FEE, help="Comision por compra, en %%.")
    args = ap.parse_args()
    print(f"[datos] {args.csv}  comision {args.fee} %  aportacion {CONTRIB:.0f} EUR/mes")
    frame = build_frame(args.csv)
    print(f"  {len(frame)} velas con ATR  {str(frame.iloc[0]['dtime'])[:10]}..{str(frame.iloc[-1]['dtime'])[:10]}")
    report(frame, args.fee / 100.0)
    print("")
    print("[lectura] 'espera' es la mediana de horas hasta entrar, 'mejor' los meses que entraron por debajo")
    print("          del precio del dia 1, y 'al cierre' los que acabaron sin disparar. Una espera de horas")
    print("          significa que la regla degenera en comprar el dia 1; una mayoria de meses 'mejor' con el")
    print("          saldo en contra es una tasa de acierto alta con esperanza negativa.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
