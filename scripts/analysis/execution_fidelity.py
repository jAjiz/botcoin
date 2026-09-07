"""¿Cuánto de lo medido en este estudio es un artefacto de simular con velas de 15 min?

Read-only y temporal. Lee los CSV OHLCVT de Kraken directamente, sin base de datos.

Todo el estudio de validación se ha corrido sobre velas de 15 minutos, y el motor resuelve
la lógica de trailing una vez por vela. Dentro de una misma vela, sobre una pierna `sell`,
``simulate_operations`` hace esto:

    extreme = high            # sube trailing_price al MAXIMO de la vela
    stop_px = stop_price(...) # y recalcula el stop desde ese maximo
    stop_hit = low <= stop_px # y lo compara contra el MINIMO de esa misma vela

En una vela que primero baja y luego sube, el bot real todavía tenía el stop viejo (más
bajo) cuando ocurrió el mínimo: no habría salido. El motor sí sale, y además contabiliza la
salida a ``stop_px``, un precio que ya no estaba disponible. Es look-ahead intravela, no
deslizamiento, y su tamaño escala con el rango de la vela — luego encoge al bajar de
resolución. Con 15 min de vela y el bot real consultando precio cada ``SLEEPING_INTERVAL``
(60 s), el artefacto puede ser grande.

Método. Se compara el MISMO config a tres resoluciones (15 / 5 / 1 min) manteniendo
constante todo lo que no es la resolución del camino de precios:

  * el ATR se calcula SIEMPRE sobre las velas de 15 min (la vista de volatilidad de
    producción) y se proyecta hacia adelante sobre la rejilla fina: cada vela fina toma el
    ATR de la vela de 15 min que la contiene. Así los niveles de volatilidad, los K_STOP y
    las distancias de stop son idénticos en los tres brazos;
  * el calendario de recalibración se construye una vez sobre las velas de 15 min y se
    remapea por marca de tiempo a la rejilla fina;
  * la ventana, las comisiones y los configs son los mismos.

Lo único que cambia entre brazos es cada cuánto se evalúa la lógica. Esa es la variable
aislada, y por eso este experimento NO es el barrido de timeframes confundido que sería
recalcular el ATR en cada resolución.

Tres puntos (15/5/1) y no dos, a propósito: si 15→5 mueve mucho y 5→1 casi nada, el efecto
converge y el número de 1 min es creíble. Si sigue creciendo, no hay convergencia y el
marco entero queda en cuestión.

Dos diferencias residuales que NO se corrigen, porque son parte de lo que la resolución
arregla y no confusiones a eliminar:

  * el precio de referencia de una vela es su cierre, que en 15 min mira hasta 15 minutos
    hacia adelante y en 1 min sólo uno;
  * por lo mismo, la compra inicial de cada brazo ocurre a un precio ligeramente distinto.

Lo que decide si el estudio sobrevive no es el nivel sino el ORDEN: si el ranking de configs
a 15 min y a 1 min coincide, las conclusiones comparativas se sostienen aunque los niveles
se muevan. Si no coincide, no se sostiene ninguna.

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):
  PYTHONPATH=. python scripts/analysis/execution_fidelity.py "C:/Dev/Kraken OHLCVT"
"""

import argparse
import itertools
import os
import statistics
import time

import numpy as np
import pandas as pd

from core.config import ATR_DESV_LIMIT, ATR_PERIOD, RECALIBRATION_BARS
from core.config import VOLATILITY_LEVELS as LEVELS
from trading.engine import EngineConfig, PairCalibration, mark_to_market, simulate_operations
from trading.market_analyzer import (
    _wilder_atr_from_scratch,
    analyze_structural_noise,
    atr_ratio_percentiles,
    k_values_by_level,
)
from trading.optimizer.search import _quantile_ceiled

CSV_COLUMNS = ["time", "open", "high", "low", "close", "volume", "count"]
FEE = 0.4

# La región que el estudio identificó como utilizable (min_margin 0.040-0.070), más los dos
# extremos para que el barrido cubra tanto la familia que opera poco como la que opera mucho.
MMS = (0.00, 0.02, 0.04, 0.05, 0.06, 0.07, 0.10)
STOPS = (0.5, 0.7, 0.9)

# La rejilla completa del estudio (105 configs), para comprobar si su único resultado
# positivo — que la región min_margin 0.040-0.070 es identificable — sobrevive a 1 min.
FULL_MMS = tuple(round(i * 0.01, 3) for i in range(21))
FULL_STOPS = (0.5, 0.6, 0.7, 0.8, 0.9)

Spec = tuple[float, float]


def candidates(full: bool = False) -> list[Spec]:
    mms, stops = (FULL_MMS, FULL_STOPS) if full else (MMS, STOPS)
    return [(mm, s) for mm in mms for s in stops]


def _signature(spec: Spec) -> str:
    return f"mm={spec[0]:.3f} s={spec[1]:.1f}"


# --- carga ------------------------------------------------------------------


def load_csv(path: str, t0: int, t1: int) -> pd.DataFrame:
    """Lee un CSV grande por trozos y se queda sólo con [t0, t1]; los ficheros vienen ordenados."""
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


def report_gaps(df: pd.DataFrame, minutes: int, label: str) -> None:
    step = minutes * 60
    deltas = df["time"].diff().dropna()
    holes = deltas[deltas != step]
    if len(holes):
        print(f"    AVISO {label}: {len(holes)} saltos (mayor: {int(holes.max()) // step} velas)")


def coarse_frame(path: str, t0: int, t1: int, minutes: int) -> pd.DataFrame:
    """Marco de 15 min con su ATR de Wilder — la vista de volatilidad que usa producción."""
    df = load_csv(path, t0, t1)
    report_gaps(df, minutes, f"{minutes}m")
    df["atr"] = _wilder_atr_from_scratch(df, ATR_PERIOD)
    df["dtime"] = pd.to_datetime(df["time"], unit="s")
    return df.dropna(subset=["atr"]).sort_values("time").reset_index(drop=True)


def fine_frame(path: str, coarse: pd.DataFrame, t0: int, t1: int, minutes: int) -> pd.DataFrame:
    """Marco fino con el ATR de 15 min proyectado hacia adelante.

    ``merge_asof`` hacia atrás asigna a cada vela fina el ATR de la vela de 15 min que la
    contiene — la misma que el brazo grueso está usando en ese mismo instante. Así ambos
    brazos comparten exactamente la misma vista de volatilidad (incluido su propio
    look-ahead de hasta 15 min), y la única diferencia que queda es la resolución del camino.
    """
    df = load_csv(path, t0, t1)
    report_gaps(df, minutes, f"{minutes}m")
    df = pd.merge_asof(
        df, coarse[["time", "atr"]].sort_values("time"), on="time", direction="backward", allow_exact_matches=True
    )
    df["dtime"] = pd.to_datetime(df["time"], unit="s")
    return df.dropna(subset=["atr"]).sort_values("time").reset_index(drop=True)


# --- calibración ------------------------------------------------------------


def build_schedule(coarse: pd.DataFrame, recalib_bars: int) -> list:
    """Puntos de calibración cada ``recalib_bars`` velas de 15 min, cada uno con su historia previa."""
    t0 = time.perf_counter()
    points = []
    for idx in range(0, len(coarse), recalib_bars):
        cal_df = coarse.iloc[: idx + 1]
        up_events, down_events = analyze_structural_noise(cal_df)
        points.append(
            {
                "time": int(coarse.iloc[idx]["time"]),
                "thresholds": atr_ratio_percentiles(cal_df),
                "up_k": k_values_by_level(up_events),
                "down_k": k_values_by_level(down_events),
            }
        )
        if len(points) % 100 == 0:
            print(f"    ... {len(points)} puntos ({time.perf_counter() - t0:.0f}s)", flush=True)
    print(f"  {len(points)} puntos cada {recalib_bars} velas de 15m ({time.perf_counter() - t0:.0f}s)")
    return points


def _calibration(point: dict, stop_pct: float) -> PairCalibration:
    th = point["thresholds"]
    return PairCalibration(
        atr_ratio_p20=th[0],
        atr_ratio_p50=th[1],
        atr_ratio_p80=th[2],
        atr_ratio_p95=th[3],
        k_stop_buy={lvl: _quantile_ceiled(point["down_k"][lvl], stop_pct) for lvl in LEVELS},
        k_stop_sell={lvl: _quantile_ceiled(point["up_k"][lvl], stop_pct) for lvl in LEVELS},
    )


def remap(points: list, times: np.ndarray) -> list[tuple[int, dict]]:
    """Reindexa los puntos de calibración a la rejilla de ``times`` por marca de tiempo.

    Un punto entra en vigor en la primera vela cuyo instante es >= el suyo, que es lo mismo
    que hace el brazo grueso. El punto en vigor al abrir la ventana se ancla en la barra 0,
    así que la entrada 0 siempre existe y ningún brazo corre sin calibración.
    """
    out: list[tuple[int, dict]] = []
    for point in points:
        at = int(np.searchsorted(times, point["time"], side="left"))
        if at >= len(times):
            continue
        out.append((0 if not out else at, point))
    return out


# --- barrido ----------------------------------------------------------------


def run_arm(df: pd.DataFrame, points: list, cands: list[Spec], label: str) -> list[dict]:
    """Corre todos los configs sobre un brazo. Una corrida continua por config, sin reinicios."""
    times = df["time"].to_numpy()
    scheduled = remap(points, times)
    base = scheduled[0][1]
    first_price = float(df.iloc[0]["close"])
    final_price = float(df.iloc[-1]["close"])
    hold = (final_price / first_price - 1.0) * 100.0

    rows = []
    t0 = time.perf_counter()
    for spec in cands:
        cfg = EngineConfig(
            pair="XBTEUR",
            calibration=_calibration(base, spec[1]),
            k_act=None,
            min_margin=spec[0],
            atr_desv_limit=ATR_DESV_LIMIT,
            calibration_schedule=tuple((at, _calibration(p, spec[1])) for at, p in scheduled),
        )
        ops = simulate_operations(df, cfg, fee_rate=FEE / 100.0)
        eur = mark_to_market(ops, final_price) if ops else 0.0
        rows.append(
            {
                "spec": spec,
                "ops": len(ops),
                "eur": eur,
                "btc": ((1.0 + eur / 100.0) / (1.0 + hold / 100.0) - 1.0) * 100.0,
            }
        )
    print(f"  {label}: {len(df)} velas, {len(cands)} configs en {time.perf_counter() - t0:.0f}s", flush=True)
    return rows


def _ranks(values: list[float]) -> list[float]:
    """Rangos medios, 1 = peor. Los empates comparten su rango medio."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def spearman(a: list[float], b: list[float]) -> float:
    ra, rb = _ranks(a), _ranks(b)
    n = len(ra)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb, strict=True))
    va = sum((x - ma) ** 2 for x in ra) ** 0.5
    vb = sum((y - mb) ** 2 for y in rb) ** 0.5
    return 0.0 if va == 0 or vb == 0 else cov / (va * vb)


# --- informe ----------------------------------------------------------------


def report_selection(cands: list[Spec], index: dict, coarse: int, fine: int, top: int) -> None:
    """¿Habría elegido el estudio a 15 min lo que 1 min dice que es bueno?

    Es la pregunta con consecuencias: el ranking global puede correlacionar y aun así la
    cabeza — que es de donde sale un config a desplegar — no coincidir en nada.
    """
    by_coarse = sorted(cands, key=lambda s: index[coarse][s]["btc"], reverse=True)
    by_fine = sorted(cands, key=lambda s: index[fine][s]["btc"], reverse=True)
    overlap = len(set(by_coarse[:top]) & set(by_fine[:top]))

    print(f"\n\n  Cabeza del ranking: top {top} de {coarse}m frente al top {top} de {fine}m.")
    print(f"  Coinciden {overlap}/{top}. Un config elegido con {coarse}m se despliega esperando")
    print("  su fila de la izquierda y obtiene la de la derecha.\n")
    print(
        f"    {'#':>3}  {f'mejor segun {coarse}m':<20}{f'BTC {coarse}m':>10}{f'BTC {fine}m':>10}"
        f"   {f'mejor segun {fine}m':<20}"
    )
    print("    " + "-" * 78)
    for i in range(top):
        a, b = by_coarse[i], by_fine[i]
        print(
            f"    {i + 1:>3}  {_signature(a):<20}{index[coarse][a]['btc']:>9.2f}%{index[fine][a]['btc']:>9.2f}%"
            f"   {_signature(b):<20}"
        )

    # La única afirmación positiva que el estudio conserva: la región es identificable
    # aunque la elección dentro de ella no lo sea. Se comprueba en los dos brazos.
    region = [s for s in cands if 0.040 <= s[0] <= 0.070]
    other = [s for s in cands if s not in region]
    if region and other:
        print(f"\n\n  Region min_margin 0.040-0.070 ({len(region)} configs) frente al resto ({len(other)}):\n")
        print(f"    {'brazo':>7}{'region (med)':>15}{'resto (med)':>14}{'ventaja':>11}{'top 10 en region':>19}")
        print("    " + "-" * 66)
        for r in (coarse, fine):
            mr = statistics.median(index[r][s]["btc"] for s in region)
            mo = statistics.median(index[r][s]["btc"] for s in other)
            best = sorted(cands, key=lambda s: index[r][s]["btc"], reverse=True)[:10]
            print(
                f"    {f'{r}m':>7}{mr:>14.2f}%{mo:>13.2f}%{mr - mo:>+10.2f}%"
                f"{f'{sum(1 for s in best if s in region)}/10':>19}"
            )


def report(cands: list[Spec], arms: dict[int, list[dict]], holds: dict[int, float], top: int = 0) -> None:
    res = sorted(arms, reverse=True)  # 15, 5, 1
    coarse, fine = res[0], res[-1]

    print("\n\n" + "=" * 100)
    print("FIDELIDAD DE EJECUCION — el mismo config a tres resoluciones, misma vista de volatilidad")
    print("=" * 100)

    print(f"\n  {'resolucion':>11}{'mantener':>11}{'ops (med)':>12}{'EUR (med)':>12}{'vs hold BTC (med)':>20}")
    print("  " + "-" * 66)
    for r in res:
        rows = arms[r]
        print(
            f"  {f'{r}m':>11}{holds[r]:>+10.2f}%{statistics.median(x['ops'] for x in rows):>12.0f}"
            f"{statistics.median(x['eur'] for x in rows):>11.2f}%{statistics.median(x['btc'] for x in rows):>19.2f}%"
        )

    verbose = len(cands) <= 30
    if verbose:
        print(f"\n\n  Por config — 'vs hold' en BTC, que es el objetivo; delta contra el brazo de {coarse}m.\n")
    head = f"  {'config':>16}"
    for r in res:
        head += f"{f'ops {r}m':>9}"
    for r in res:
        head += f"{f'BTC {r}m':>11}"
    head += f"{f'd {fine}m-{coarse}m':>13}"
    if verbose:
        print(head)
        print("  " + "-" * (16 + 9 * len(res) + 11 * len(res) + 13))

    index = {r: {tuple(x["spec"]): x for x in arms[r]} for r in res}

    # Un config que no llega a activarse nunca es insensible a la resolución: su delta es cero
    # por construcción, y si son mayoría aplastan la mediana y empatan el ranking, que es lo
    # que infla la correlación. Se mide también sobre los que operan en TODOS los brazos, que
    # son los únicos donde la pregunta tiene contenido.
    active = [s for s in cands if all(index[r][s]["ops"] > 1 for r in res)]

    deltas, flips = [], 0
    for spec in cands:
        line = f"  {_signature(spec):>16}"
        for r in res:
            line += f"{index[r][spec]['ops']:>9}"
        for r in res:
            line += f"{index[r][spec]['btc']:>10.2f}%"
        delta = index[fine][spec]["btc"] - index[coarse][spec]["btc"]
        deltas.append(delta)
        if index[fine][spec]["btc"] * index[coarse][spec]["btc"] < 0:
            flips += 1
        line += f"{delta:>+12.2f}%"
        if verbose:
            print(line)

    act_deltas = [index[fine][s]["btc"] - index[coarse][s]["btc"] for s in active]
    act_flips = sum(1 for s in active if index[fine][s]["btc"] * index[coarse][s]["btc"] < 0)

    print("\n  " + "-" * 66)
    print(f"  {'':>34}{'todos':>12}{'solo activos':>15}")
    print(f"  {'configs':>34}{len(cands):>12}{len(active):>15}")
    print(
        f"  {f'delta mediana {fine}m-{coarse}m':>34}{statistics.median(deltas):>+11.2f}%"
        f"{(statistics.median(act_deltas) if act_deltas else 0.0):>+14.2f}%"
    )
    print(
        f"  {'delta maxima (valor absoluto)':>34}{max(deltas, key=abs):>+11.2f}%"
        f"{(max(act_deltas, key=abs) if act_deltas else 0.0):>+14.2f}%"
    )
    print(f"  {'cambian de signo':>34}{f'{flips}/{len(cands)}':>12}{f'{act_flips}/{len(active)}':>15}")

    print("\n\n  Correlacion de rangos entre resoluciones (sobre 'vs hold' en BTC):")
    print("  Esto es lo que decide si el estudio sobrevive. El nivel puede moverse; si el ORDEN")
    print("  aguanta, las conclusiones comparativas siguen en pie. La columna 'solo activos' es")
    print("  la que vale: los configs que no operan empatan y sesgan el rho hacia arriba.\n")
    print(f"    {'':>12}{'todos':>10}{'solo activos':>15}")
    for i, a in enumerate(res):
        for b in res[i + 1 :]:
            rho = spearman([index[a][s]["btc"] for s in cands], [index[b][s]["btc"] for s in cands])
            rho_a = (
                spearman([index[a][s]["btc"] for s in active], [index[b][s]["btc"] for s in active])
                if len(active) > 2
                else float("nan")
            )
            print(f"    {f'{a}m vs {b}m':>12}{rho:>+10.3f}{rho_a:>+15.3f}")

    if len(res) > 2:
        print("\n  Convergencia: si 15m->5m mueve mucho y 5m->1m casi nada, el efecto converge y el")
        print("  numero de 1m es creible. Si sigue creciendo, no hay convergencia.")
        pool = active or cands
        print(f"\n    {'paso':>12}{'delta med':>12}{'rho':>10}")
        for a, b in itertools.pairwise(res):
            step = statistics.median(index[b][s]["btc"] - index[a][s]["btc"] for s in pool)
            rho = spearman([index[a][s]["btc"] for s in pool], [index[b][s]["btc"] for s in pool])
            print(f"    {f'{a}m -> {b}m':>12}{step:>+11.2f}%{rho:>+10.3f}")
        print(f"\n    (mediana y rho sobre {len(pool)} configs activos)")

    if top:
        report_selection(cands, index, coarse, fine, min(top, len(cands)))


def main() -> int:
    ap = argparse.ArgumentParser(description="¿Es un artefacto de 15 min lo que mide el estudio?")
    ap.add_argument("data_dir", help="Carpeta con XBTEUR_1.csv, XBTEUR_5.csv, XBTEUR_15.csv")
    ap.add_argument("--pair", default="XBTEUR")
    ap.add_argument("--cal-start", default="2025-01-01", help="Inicio de la historia de calibracion.")
    ap.add_argument("--start", default="2025-04-01", help="Inicio de la simulacion (tras el calentamiento).")
    ap.add_argument("--end", default="2025-12-31", help="Fin de la ventana.")
    ap.add_argument("--recalib-bars", type=int, default=RECALIBRATION_BARS)
    ap.add_argument("--resolutions", default="15,5,1")
    ap.add_argument("--full", action="store_true", help="La rejilla completa del estudio (105 configs).")
    ap.add_argument("--top", type=int, default=10, help="Tamano de la cabeza del ranking que se compara.")
    args = ap.parse_args()

    cal_t0 = int(pd.Timestamp(args.cal_start).timestamp())
    sim_t0 = int(pd.Timestamp(args.start).timestamp())
    t1 = int(pd.Timestamp(args.end).timestamp()) + 86_399
    res = [int(x) for x in args.resolutions.split(",")]

    print(f"[datos] {args.pair} en {args.data_dir}")
    print(f"  calibracion desde {args.cal_start}, simulacion {args.start}..{args.end}")

    coarse = coarse_frame(os.path.join(args.data_dir, f"{args.pair}_15.csv"), cal_t0, t1, 15)
    print(f"  15m: {len(coarse)} velas con ATR  {coarse.iloc[0]['dtime']}..{coarse.iloc[-1]['dtime']}")

    print(f"\n[calibracion] calendario cada {args.recalib_bars} velas de 15m (tarda minutos)")
    points = build_schedule(coarse, args.recalib_bars)
    # Sólo importan los puntos vigentes en la ventana de simulación y el último previo a ella.
    prior = [p for p in points if p["time"] <= sim_t0]
    points = ([prior[-1]] if prior else []) + [p for p in points if p["time"] > sim_t0]
    print(f"  {len(points)} puntos aplican a la ventana de simulacion")

    cands = candidates(args.full)
    print(f"\n[barrido] {len(cands)} configs x {len(res)} resoluciones")

    arms, holds = {}, {}
    for r in res:
        path = os.path.join(args.data_dir, f"{args.pair}_{r}.csv")
        df = (
            coarse[coarse["time"] >= sim_t0].reset_index(drop=True)
            if r == 15
            else fine_frame(path, coarse, sim_t0, t1, r)
        )
        holds[r] = (float(df.iloc[-1]["close"]) / float(df.iloc[0]["close"]) - 1.0) * 100.0
        arms[r] = run_arm(df, points, cands, f"{r}m")
        del df

    report(cands, arms, holds, args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
