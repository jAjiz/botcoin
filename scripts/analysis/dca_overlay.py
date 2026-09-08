"""Sobre una DCA mensual, ¿que aporta automatizar la ENTRADA? Monedas por euro aportado.

Read-only y temporal. Lee el CSV OHLCVT de 15 min de Kraken, sin base de datos y sin motor.

El estudio cerro el bot como generador de rentabilidad frente a mantener. Queda una pregunta
distinta, y con otra referencia: el usuario aporta una cantidad fija cada mes, asi que el
patron a batir ya NO es comprar y mantener desde t0 sino la propia DCA ingenua. Y la DCA
ingenua no es un patron trivial: comprar un importe fijo compra mas monedas cuando el precio
esta bajo, que es el MISMO mecanismo de convexidad que el reequilibrio -- ya lo lleva puesto.

Se comparan variantes que no predicen nada, solo colocan la compra:

  dia 1         el importe entero en la primera vela del mes (la referencia)
  semanal       el importe partido en cuatro compras, una por semana
  diaria        el importe partido entre todos los dias del mes
  limite -X%    orden limitada a X % por debajo de la apertura del mes; si el minimo del mes
                no la toca, se compra a mercado en el cierre del mes (el coste de esperar)
  caida         aporta doble si el precio esta >20 % por debajo de su maximo de 90 dias, y
                la mitad si no; el sobrante se guarda en euros y se valora al final

Todas aportan EXACTAMENTE el mismo total en euros, y se puntuan por valor final (monedas al
precio final mas euros sin gastar) dividido por lo aportado. Comparar cualquier otra cosa
seria comparar tamaños distintos de inversion.

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):

    PYTHONPATH=. python scripts/analysis/dca_overlay.py "C:/Dev/Kraken OHLCVT/XBTEUR_15.csv"
"""

import argparse

import pandas as pd

CSV_COLUMNS = ["time", "open", "high", "low", "close", "volume", "count"]
FEE = 0.4
CONTRIB = 100.0
WINDOWS = (("2018-2025", "2018-01-01"), ("2021-2025", "2021-01-01"), ("2023-2025", "2023-01-01"))


def build_daily(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, header=None, names=CSV_COLUMNS)
    df["dtime"] = pd.to_datetime(df["time"], unit="s")
    df = df.drop_duplicates(subset=["time"]).sort_values("time").set_index("dtime")
    daily = df.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    return daily.reset_index()


def _months(daily: pd.DataFrame) -> list[pd.DataFrame]:
    return [g.reset_index(drop=True) for _, g in daily.groupby(daily["dtime"].dt.to_period("M"))]


def _buy(eur: float, price: float, fee: float) -> float:
    return (eur * (1.0 - fee)) / price


def variant(months: list[pd.DataFrame], name: str, fee: float, peak: dict) -> dict:
    coins, cash, buys = 0.0, 0.0, 0
    for i, m in enumerate(months):
        cash += CONTRIB
        if name == "dia 1":
            spend = [(cash, float(m.iloc[0]["open"]))]
        elif name == "semanal":
            rows = [m.iloc[j] for j in range(0, len(m), 7)]
            spend = [(cash / len(rows), float(r["open"])) for r in rows]
        elif name == "diaria":
            spend = [(cash / len(m), float(r["open"])) for _, r in m.iterrows()]
        elif name.startswith("limite"):
            pct = float(name.split()[-1].rstrip("%")) / 100.0
            limit = float(m.iloc[0]["open"]) * (1.0 + pct)
            hit = float(m["low"].min()) <= limit
            spend = [(cash, limit if hit else float(m.iloc[-1]["close"]))]
        elif name == "caida":
            px = float(m.iloc[0]["open"])
            want = CONTRIB * 2.0 if px < peak[i] * 0.8 else CONTRIB * 0.5
            spend = [(min(want, cash), px)]
        for eur, price in spend:
            if eur <= 0:
                continue
            coins += _buy(eur, price, fee)
            cash -= eur
            buys += 1
    final = float(months[-1].iloc[-1]["close"])
    contributed = CONTRIB * len(months)
    return {"value": coins * final + cash, "contributed": contributed, "buys": buys}


def report(daily: pd.DataFrame, fee: float) -> None:
    names = ["dia 1", "semanal", "diaria", "limite -5%", "limite -10%", "limite -20%", "caida"]
    for label, start in WINDOWS:
        part = daily[daily["dtime"] >= pd.Timestamp(start)].reset_index(drop=True)
        months = _months(part)
        peak = {}
        for i, m in enumerate(months):
            hist = part[part["dtime"] <= m.iloc[0]["dtime"]].tail(90)
            peak[i] = float(hist["high"].max())
        base = variant(months, "dia 1", fee, peak)
        hold = float(part.iloc[-1]["close"]) / float(part.iloc[0]["open"]) - 1.0
        print(f"\n[{label}]  {len(months)} meses, {base['contributed']:.0f} EUR aportados, precio {hold:+.0%}")
        print(f"  {'variante':<14} {'valor final':>12} {'x aportado':>11} {'vs dia 1':>10} {'compras':>8}")
        for name in names:
            r = variant(months, name, fee, peak)
            print(
                f"  {name:<14} {r['value']:>11,.0f}E {r['value'] / r['contributed']:>10.2f}x "
                f"{(r['value'] / base['value'] - 1.0) * 100.0:>+9.2f}% {r['buys']:>8}"
            )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--fee", type=float, default=FEE, help="Comision por compra, en %%.")
    args = ap.parse_args()
    print(f"[datos] {args.csv}  comision {args.fee} %  aportacion {CONTRIB:.0f} EUR/mes")
    daily = build_daily(args.csv)
    print(f"  {len(daily)} dias  {str(daily.iloc[0]['dtime'])[:10]}..{str(daily.iloc[-1]['dtime'])[:10]}")
    report(daily, args.fee / 100.0)
    print("\n[lectura] Todas aportan el mismo total. 'vs dia 1' es lo unico que mide colocar la compra.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
