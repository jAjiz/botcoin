"""¿Predice ALGO observable en t lo que el bot deberia haber hecho? Y si lo predice, ¿vale dinero?

Read-only y temporal. Lee el CSV OHLCVT de 15 min de Kraken, sin base de datos. No usa el
motor: no hay configs del bot ni trailing stop. Es una medida del mercado, no del bot.

La pregunta. La ingenieria inversa ("etiquetar cuando el bot deberia haber comprado/vendido y
disenar la operativa que lo replique") se descompone en dos pasos muy asimetricos. Etiquetar
es GRATIS: con el futuro delante la etiqueta perfecta siempre existe. Replicar no es un
problema de diseno sino de PREDICCION: hace falta una funcion de lo visible en t que prediga
la etiqueta. Este script mide ese segundo paso, sin construir ninguna estrategia.

La leccion del script, y el motivo de que tenga tres partes en vez de una: **un AUC alto
contra la etiqueta de pivote no significa nada**. La primera version media solo eso y daba
0.72-0.76 fuera de muestra, que habria pasado por un hallazgo enorme. Es una TAUTOLOGIA: los
pivotes se alternan minimo/maximo, asi que una vela en mitad de un tramo alcista tiene
retorno reciente positivo Y etiqueta +1 por la misma razon — el tramo ya ha empezado. El
clasificador no predice el futuro, lee el presente. Por eso hay tres medidas y las tres
tienen que pasar:

  1. ETIQUETA DE PIVOTE      +1 si el siguiente pivote esta por encima del cierre de t (habia
                             que estar DENTRO), -1 si por debajo (en CAJA). Se reporta su
                             valor en dinero: es enorme, y por eso el AUC contra ella enganya.
  2. ETIQUETA HONESTA        signo del retorno futuro a horizonte FIJO (12h/1d/3d). No hay
                             tautologia posible: es exactamente lo que hay que adivinar para
                             ganar dinero manteniendo una posicion en el tiempo.
  3. PRUEBA DE DINERO        la prediccion del modelo COMO ASIGNACION, revisada en cada
                             muestra, puntuada en activo base contra mantener. Es la unica
                             cifra que no se puede inflar con una etiqueta mal elegida.

Caracteristicas estrictamente causales (datos hasta t inclusive), en dos familias:

  * PRECIO/VOLATILIDAD: retornos a varios rezagos, ATR/close, ER de Kaufman con signo,
    distancia a medias moviles, RSI. El screen de regimenes ya la midio nula, asi que entra
    como CONTROL.
  * FLUJO: z-score del volumen y del numero de operaciones, tamano medio de operacion y
    desequilibrio de volumen con signo. Son las dos columnas del CSV que este estudio nunca
    habia usado, y lo unico que el resultado del ER no cubre.

El nulo es una PERMUTACION CIRCULAR de las etiquetas, no una barajada: estan muy
autocorrelacionadas (rachas dentro de una tendencia) y barajarlas daria una banda nula
demasiado estrecha, que es como se lee ruido por senal.

Muestreo NO SOLAPADO: el paso es la mediana de velas hasta el siguiente pivote, asi que dos
muestras consecutivas no comparten episodio. La n impresa es la n real. Particion temporal
fijada de antemano: se ajusta en [--start, --split) y se mide en [--split, --end].

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):

    PYTHONPATH=. python scripts/analysis/signal_screen.py "C:/Dev/Kraken OHLCVT/XBTEUR_15.csv"
"""

import argparse

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from core.config import ATR_PERIOD
from trading.market_analyzer import _wilder_atr_from_scratch, detect_pivots

CSV_COLUMNS = ["time", "open", "high", "low", "close", "volume", "count"]
BARS_PER_DAY = 96
# Comision por lado: la tarifa maker de Kraken, la mas favorable a la hipotesis.
FEE = 0.0016


def build_frame(path: str, t0: int, t1: int) -> pd.DataFrame:
    df = pd.read_csv(path, header=None, names=CSV_COLUMNS)
    df = df[(df["time"] >= t0) & (df["time"] <= t1)]
    df = df.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
    df["atr"] = _wilder_atr_from_scratch(df, ATR_PERIOD)
    df["dtime"] = pd.to_datetime(df["time"], unit="s")
    return df.dropna(subset=["atr"]).reset_index(drop=True)


# --- etiquetas --------------------------------------------------------------


def label_by_next_pivot(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """(+1 dentro / -1 en caja, velas hasta el pivote) por vela; NaN donde no hay pivote futuro."""
    pivots = detect_pivots(df)
    close = df["close"].to_numpy(dtype=float)
    label = np.full(len(df), np.nan)
    horizon = np.full(len(df), np.nan)
    cursor = 0
    for i in range(len(df)):
        while cursor < len(pivots) and pivots[cursor][0] <= i:
            cursor += 1
        if cursor >= len(pivots):
            break
        at, _, price, _ = pivots[cursor]
        label[i] = 1.0 if price > close[i] else -1.0
        horizon[i] = at - i
    return label, horizon


def label_forward(df: pd.DataFrame, bars: int) -> np.ndarray:
    """Signo del retorno de t a t+bars. La etiqueta honesta: sin tautologia posible."""
    close = df["close"].to_numpy(dtype=float)
    out = np.full(len(df), np.nan)
    out[:-bars] = np.sign(close[bars:] - close[:-bars])
    return out


# --- caracteristicas causales -----------------------------------------------


def _z(series: pd.Series, short: int, long: int) -> pd.Series:
    """z-score de la media corta contra la distribucion larga; todo con datos hasta t."""
    return (series.rolling(short).mean() - series.rolling(long).mean()) / series.rolling(long).std()


def _signed_er(close: pd.Series, n: int) -> pd.Series:
    """Ratio de eficiencia de Kaufman CON signo sobre las ultimas n velas."""
    return close.diff(n) / close.diff().abs().rolling(n).sum()


def _rsi(close: pd.Series, n: int) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100.0 - 100.0 / (1.0 + up / down)


def features(df: pd.DataFrame) -> pd.DataFrame:
    close = df["close"].astype(float)
    out = pd.DataFrame(index=df.index)

    for name, n in (("ret_1h", 4), ("ret_4h", 16), ("ret_1d", 96), ("ret_3d", 288), ("ret_2w", 1344)):
        out[name] = np.log(close / close.shift(n))
    out["atr_ratio"] = df["atr"].astype(float) / close
    out["er_1d"] = _signed_er(close, 96)
    out["er_3d"] = _signed_er(close, 288)
    out["ma_dist_1w"] = close / close.rolling(672).mean() - 1.0
    out["ma_dist_1m"] = close / close.rolling(2880).mean() - 1.0
    out["rsi_1d"] = _rsi(close, 96)

    volume = df["volume"].astype(float)
    count = df["count"].astype(float)
    out["vol_z"] = _z(np.log1p(volume), 96, 2880)
    out["cnt_z"] = _z(np.log1p(count), 96, 2880)
    out["trade_size_z"] = _z(np.log1p(volume / count.replace(0, np.nan)), 96, 2880)
    # Desequilibrio de flujo: volumen firmado por el signo de la vela, sobre el volumen total.
    signed = volume * np.sign(close.diff())
    out["flow_imb_1d"] = signed.rolling(96).sum() / volume.rolling(96).sum()
    out["flow_imb_3d"] = signed.rolling(288).sum() / volume.rolling(288).sum()
    return out


PRICE_FEATURES = (
    "ret_1h", "ret_4h", "ret_1d", "ret_3d", "ret_2w",
    "atr_ratio", "er_1d", "er_3d", "ma_dist_1w", "ma_dist_1m", "rsi_1d",
)  # fmt: skip
FLOW_FEATURES = ("vol_z", "cnt_z", "trade_size_z", "flow_imb_1d", "flow_imb_3d")


# --- metrica, nulo y ajuste -------------------------------------------------


def auc(score: np.ndarray, label: np.ndarray) -> float:
    """AUC de Mann-Whitney: P(score de un +1 > score de un -1), empates a 0.5."""
    pos, neg = score[label > 0], score[label < 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    ranks = pd.Series(score).rank().to_numpy()
    return (ranks[label > 0].sum() - len(pos) * (len(pos) + 1) / 2.0) / (len(pos) * len(neg))


def circular_null(score: np.ndarray, label: np.ndarray, n: int, rng) -> np.ndarray:
    """Banda nula de AUC desplazando la etiqueta en circulo: conserva su autocorrelacion."""
    margin = max(1, len(label) // 20)
    shifts = rng.integers(margin, len(label) - margin, size=n)
    return np.array([auc(score, np.roll(label, int(s))) for s in shifts])


def fit_logistic(x: np.ndarray, y: np.ndarray, l2: float = 1.0) -> np.ndarray:
    """Logistica con L2, por L-BFGS. y en {0,1}; x ya estandarizado y con columna de unos."""

    def loss(w):
        z = np.clip(x @ w, -30, 30)
        return (np.sum(np.log1p(np.exp(z)) - y * z) + l2 * np.sum(w[1:] ** 2) / 2.0) / len(y)

    return minimize(loss, np.zeros(x.shape[1]), method="L-BFGS-B").x


def base_asset_of(position: np.ndarray, price: np.ndarray, fee: float) -> tuple[float, int]:
    """Activo base acumulado, en %, de una asignacion binaria revisada muestra a muestra.

    Empieza dentro del activo con una moneda y termina valorada al ultimo precio, igual que
    `mark_to_market`. Mantener es 0 % por construccion.
    """
    coins, cash, inside, flips = 1.0, 0.0, True, 0
    for i in range(len(position) - 1):
        want = position[i] > 0
        if want == inside:
            continue
        flips += 1
        if inside:
            cash, coins, inside = coins * price[i] * (1.0 - fee), 0.0, False
        else:
            coins, cash, inside = cash / price[i] * (1.0 - fee), 0.0, True
    final_coins = coins if inside else cash / price[-1]
    return (final_coins - 1.0) * 100.0, flips


# --- informe ----------------------------------------------------------------


def _auc_table(title: str, names, feats: np.ndarray, label: np.ndarray, rng, n_null: int) -> None:
    print(f"\n  [{title}]")
    print(f"    {'caracteristica':<16} {'AUC':>7} {'nulo p5':>9} {'nulo p95':>9} {'p':>7}")
    for i, feat in enumerate(names):
        a = auc(feats[:, i], label)
        null = circular_null(feats[:, i], label, n_null, rng)
        p = float(np.mean(np.abs(null - 0.5) >= abs(a - 0.5)))
        flag = "  <-- fuera del nulo" if p < 0.05 else ""
        print(
            f"    {feat:<16} {a:>7.3f} {np.quantile(null, 0.05):>9.3f} {np.quantile(null, 0.95):>9.3f} {p:>7.3f}{flag}"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="¿Predice algo visible en t la accion ideal, y vale dinero?")
    ap.add_argument("csv", help="Ruta al <PAIR>_15.csv de Kraken")
    ap.add_argument("--start", default="2021-01-01", help="Inicio del ajuste (mas calentamiento por delante).")
    ap.add_argument("--split", default="2025-01-01", help="Frontera ajuste/prueba, fijada de antemano.")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--n-null", type=int, default=300)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    t0 = int((pd.Timestamp(args.start) - pd.Timedelta(days=60)).timestamp())
    t1 = int(pd.Timestamp(args.end).timestamp()) + 86_399
    print(f"[datos] {args.csv}")
    frame = build_frame(args.csv, t0, t1)
    print(f"  {len(frame)} velas  {str(frame['dtime'].iloc[0])[:16]}..{str(frame['dtime'].iloc[-1])[:16]}")

    label, horizon = label_by_next_pivot(frame)
    feats = features(frame)
    ok = (
        ~np.isnan(label)
        & feats.notna().all(axis=1).to_numpy()
        & (frame["dtime"] >= pd.Timestamp(args.start)).to_numpy()
    )
    stride = int(np.nanmedian(horizon[ok]))
    idx = np.where(ok)[0][::stride]

    times = frame["dtime"].to_numpy()[idx]
    price = frame["close"].to_numpy(dtype=float)[idx]
    split = np.datetime64(pd.Timestamp(args.split))
    train, test = times < split, times >= split
    y = label[idx]
    names = list(feats.columns)
    xv = feats.to_numpy(dtype=float)[idx]
    mu, sd = xv[train].mean(axis=0), xv[train].std(axis=0)
    xs = (xv - mu) / np.where(sd > 0, sd, 1.0)
    rng = np.random.default_rng(args.seed)

    print(f"\n[muestreo] paso {stride} velas ({stride / BARS_PER_DAY:.1f} d), la mediana hasta el pivote: sin solape")
    print(
        f"  {train.sum()} de ajuste ({str(times[train][0])[:10]}..{str(times[train][-1])[:10]}), "
        f"{test.sum()} de prueba ({str(times[test][0])[:10]}..{str(times[test][-1])[:10]})"
    )
    print(f"  tasa base (+1): ajuste {100 * (y[train] > 0).mean():.0f} %, prueba {100 * (y[test] > 0).mean():.0f} %")

    hold = (price[test][-1] / price[test][0] - 1.0) * 100.0
    oracle, oracle_flips = base_asset_of(y[test], price[test], FEE)
    print(f"\n[1. valor de la etiqueta] el pivote, operado con conocimiento perfecto y comision {100 * FEE:.2f} %")
    print(
        f"  activo base {oracle:+.1f} % en {oracle_flips} cambios de lado (mantener 0 %, hold {hold:+.1f} % en euros)"
    )
    print("  La etiqueta vale una fortuna. Por eso el AUC contra ella, abajo, no dice lo que parece.")

    print("\n[2. AUC contra la etiqueta de pivote] TAUTOLOGICO — mide si se sabe en que tramo se esta, no el futuro")
    for title, fam in (("precio/volatilidad — control", PRICE_FEATURES), ("flujo", FLOW_FEATURES)):
        sel = [names.index(n) for n in names if n in fam]
        _auc_table(title, [names[i] for i in sel], xs[test][:, sel], y[test], rng, args.n_null)

    print("\n[3. AUC contra la etiqueta honesta] signo del retorno futuro a horizonte fijo")
    print(f"    {'caracteristica':<16}" + "".join(f"{h:>10}" for h in ("12h", "1d", "3d")))
    forwards = {h: label_forward(frame, b)[idx] for h, b in (("12h", 48), ("1d", 96), ("3d", 288))}
    for i, feat in enumerate(names):
        row = f"    {feat:<16}"
        for h in ("12h", "1d", "3d"):
            f_lab = forwards[h]
            m = test & ~np.isnan(f_lab) & (f_lab != 0)
            row += f"{auc(xs[m][:, i], f_lab[m]):>10.3f}"
        print(row)

    print("\n[4. multivariante y prueba de dinero] logistica ajustada SOLO en el tramo de ajuste")
    print(
        f"    {'familia':<22} {'AUC aj.':>8} {'AUC pr.':>8} {'p':>6} {'base 0 %':>10} {'base maker':>11} {'cambios':>8}"
    )
    for fam_name, cols in (("precio/volatilidad", PRICE_FEATURES), ("flujo", FLOW_FEATURES), ("todas", tuple(names))):
        sel = [names.index(n) for n in names if n in cols]
        w = fit_logistic(np.c_[np.ones(train.sum()), xs[train][:, sel]], (y[train] > 0).astype(float))
        score_in = np.c_[np.ones(train.sum()), xs[train][:, sel]] @ w
        score = np.c_[np.ones(test.sum()), xs[test][:, sel]] @ w
        null = circular_null(score, y[test], args.n_null, rng)
        p = float(np.mean(np.abs(null - 0.5) >= abs(auc(score, y[test]) - 0.5)))
        free, flips = base_asset_of(score, price[test], 0.0)
        paid, _ = base_asset_of(score, price[test], FEE)
        print(
            f"    {fam_name:<22} {auc(score_in, y[train]):>8.3f} {auc(score, y[test]):>8.3f} {p:>6.3f} "
            f"{free:>+9.1f}% {paid:>+10.1f}% {flips:>8}"
        )

    print(
        "\n[lectura] Las tres medidas juntas. La etiqueta perfecta vale cientos de puntos; un clasificador\n"
        "          con AUC alto CONTRA ELLA captura una fraccion minima incluso a comision cero, y contra la\n"
        "          etiqueta honesta ninguna caracteristica sale de 0.5. El AUC alto es saber en que tramo se\n"
        "          esta — informacion del presente, que ya esta en el precio — y el dinero esta en cuando\n"
        "          termina el tramo, que es lo que no se predice."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
