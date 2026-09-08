"""¿Cuanto vale de verdad reequilibrar una asignacion fija, frente al bot y frente a mantener?

Read-only y temporal. Lee el CSV OHLCVT de 15 min de Kraken, sin base de datos y sin motor:
la regla es una funcion del cierre y nada mas, asi que no hay configs, ni calibracion, ni ATR.

El estudio cerro con que el bot no bate a mantener en ninguna ventana alcista y que su
perdida por operacion es estructural. Queda una pregunta separada: de lo que el bot hace
-- pasar tiempo fuera del activo y volver a entrar mas abajo -- ¿que parte es capturable
por una regla que no predice nada? Esa es la prima de reequilibrio, y tiene forma cerrada
aproximada: una cartera de peso fijo `w` reequilibrada cobra del orden de
`0.5 * w * (1 - w) * sigma^2` al año sobre la misma mezcla NO reequilibrada. Con w = 0.5 y
sigma = 40 % eso es ~2 % anual. Aqui se mide en vez de asumirse.

La regla, deliberadamente la mas simple que existe:

    manten una fraccion `w` del valor de la cartera en el activo y el resto en euros;
    cuando el peso real se sale de [w - banda, w + banda], vuelve a `w` pagando comision
    sobre el importe movido. Se comprueba en cada vela de 15 min (lo mas favorable posible).

Se reportan DOS referencias, porque miden cosas distintas y confundirlas es el error:

  vs mantener (100 % activo)   la referencia del estudio. Una cartera al 50 % tiene la mitad
                               de exposicion, asi que en un tramo alcista PIERDE contra
                               mantener por construccion. No es un fallo de la regla.
  vs la MISMA mezcla estatica  la prima de reequilibrio propiamente dicha: misma exposicion
                               inicial, misma direccionalidad, unica diferencia el reequilibrio.
                               Es la unica cifra que aisla lo que la regla aporta, y es
                               identica en euros y en activo base (ambas dividen por el
                               mismo precio final).

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):

    PYTHONPATH=. python scripts/analysis/constant_mix_rebalance.py "C:/Dev/Kraken OHLCVT/XBTEUR_15.csv"
    PYTHONPATH=. python scripts/analysis/constant_mix_rebalance.py CSV --start 2025-04-01 --end 2025-12-31
"""

import argparse

import pandas as pd

CSV_COLUMNS = ["time", "open", "high", "low", "close", "volume", "count"]
FEE = 0.4

WEIGHTS = (0.25, 0.5, 0.75)
BANDS = (0.02, 0.05, 0.10, 0.20)
WINDOWS = (
    ("2023", "2023-01-01", "2023-12-31"),
    ("2024", "2024-01-01", "2024-12-31"),
    ("2025", "2025-01-01", "2025-12-31"),
    ("2023-2025", "2023-01-01", "2025-12-31"),
)


def build_frame(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, header=None, names=CSV_COLUMNS)
    df["dtime"] = pd.to_datetime(df["time"], unit="s")
    return df.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)


def run(closes: list[float], w: float, band: float, fee_rate: float) -> dict:
    """Cartera de peso fijo `w` reequilibrada por banda, revisada en cada vela."""
    p0 = closes[0]
    base, cash = w / p0, 1.0 - w  # valor inicial 1 EUR
    trades = 0
    traded = 0.0
    for price in closes:
        value = base * price + cash
        actual = base * price / value
        if abs(actual - w) <= band:
            continue
        target_base_eur = w * value
        delta = target_base_eur - base * price  # >0 compra, <0 venta
        # La comision sale del efectivo, asi que el importe movido se ajusta a lo que hay.
        cost = abs(delta) * fee_rate
        base += delta / price
        cash -= delta + cost
        trades += 1
        traded += abs(delta)
    final = base * closes[-1] + cash
    static = w * (closes[-1] / p0) + (1.0 - w)  # la misma mezcla, nunca tocada
    return {
        "eur": (final - 1.0) * 100.0,
        "vs_static": (final / static - 1.0) * 100.0,
        "trades": trades,
        "turnover": traded * 100.0,
    }


def report(df: pd.DataFrame, fee_rate: float) -> None:
    for name, start, end in WINDOWS:
        part = df[(df["dtime"] >= pd.Timestamp(start)) & (df["dtime"] <= pd.Timestamp(end) + pd.Timedelta(days=1))]
        if part.empty:
            continue
        closes = part["close"].tolist()
        hold = (closes[-1] / closes[0] - 1.0) * 100.0
        print(f"\n[{name}]  {str(part.iloc[0]['dtime'])[:10]}..{str(part.iloc[-1]['dtime'])[:10]}  hold {hold:+.1f} %")
        print(f"  {'w':>5} {'banda':>7} {'EUR':>9} {'vs mantener':>13} {'vs mezcla estatica':>20} {'reeq.':>7}")
        for w in WEIGHTS:
            for band in BANDS:
                r = run(closes, w, band, fee_rate)
                vs_hold = ((1.0 + r["eur"] / 100.0) / (1.0 + hold / 100.0) - 1.0) * 100.0
                print(
                    f"  {w:>5.2f} {band:>7.0%} {r['eur']:>+8.1f}% {vs_hold:>+12.1f}% "
                    f"{r['vs_static']:>+19.2f}% {r['trades']:>7}"
                )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--fee", type=float, default=FEE, help="Comision por operacion, en %%.")
    args = ap.parse_args()

    print(f"[datos] {args.csv}  comision {args.fee} %")
    frame = build_frame(args.csv)
    print(f"  {len(frame)} velas  {str(frame.iloc[0]['dtime'])[:10]}..{str(frame.iloc[-1]['dtime'])[:10]}")
    report(frame, args.fee / 100.0)
    print(
        "\n[lectura] 'vs mantener' compara media exposicion contra exposicion completa: en un tramo\n"
        "          alcista es negativo por construccion y no dice nada de la regla. 'vs mezcla\n"
        "          estatica' es lo unico que aisla lo que aporta reequilibrar."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
