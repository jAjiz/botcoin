"""Cota superior de una config POR REGIMEN: ¿cuanto vale conmutar la config con etiquetas perfectas?

Read-only y temporal. Lee el CSV OHLCVT de 15 min de Kraken, sin base de datos.

La pregunta. Si un operador (o un oraculo) supiera en que regimen esta el mercado — lateral,
bajista, alcista — y cambiara la config del par en cada frontera via `PATCH /config`, ¿cuanto
mas activo base acumularia que con UNA config fija? Es la misma cota que acoto la puerta del
rally: si ni con etiquetas perfectas gana, no hay clasificador ni humano que lo recupere.

Etiquetado por FORMA, no por tiempo. Las subidas y bajadas de BTC ocurren en dias y entre
ellas hay semanas de lateral; una ventana fija de N semanas mezcla las dos cosas. Sobre los
cierres diarios:

  1. IMPULSO  = todo dia que forme parte de un movimiento de al menos M % completado en K
               dias o menos (cualquier par de cierres a <= K dias con |neto| >= M marca los
               dias entre ambos). Los dias marcados contiguos forman un tramo, con direccion
               por el signo de su neto: ALCISTA o BAJISTA; si el neto no llega a M/2 (subio y
               volvio) es un PICO.
  2. LATERAL  = un hueco entre impulsos de al menos D dias. Un hueco mas corto es CORTO: una
               pausa entre dos impulsos, no un regimen; se reporta, no se excluye.

M, K y D se fijan ANTES de ver que config gana en cada clase: M ~ 2x la distancia de
activacion de la config recomendada, K una semana, D la cadencia de operacion buscada.
Se probo antes una contencion en caja (el tramo mas largo que cabe en un W %): absorbio los
impulsos, porque en 2025 fueron saltos de uno o dos dias entre cajas contiguas, y el 97 % de
los dias salieron laterales. Etiquetar el movimiento primero es lo que respeta la forma.

Corridas continuas, nunca suma de tramos. Cada config se simula UNA vez sobre toda la
ventana y se parte en tramos marcando a mercado en cada frontera (cociente de factores);
la conmutada es UNA corrida en la que k_act/min_margin y el stop_pct cambian en las
fronteras, con la posicion abierta arrastrandose a traves de ellas. Sumar corridas
independientes por tramo reinicia el bot en cada frontera y ya costo un +21.9 falso.

El cambio de min_margin en mitad de una corrida necesita el `activation_schedule` que la rama
retiro del motor por no tener llamador en produccion. Aqui se reproduce SIN tocar el motor:
las entradas del calendario de calibracion de la conmutada llevan su min_margin, y
`activation_distance` se parchea en el modulo para leerlo. Solo la rama min_margin (k_act
None), que es la rejilla del estudio.

Puntuacion en ACTIVO BASE: (1 + r_bot) / (1 + r_hold) - 1. Mantener es 0 % por construccion.

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):

    PYTHONPATH=. python scripts/analysis/regime_switch_oracle.py "C:/Dev/Kraken OHLCVT/XBTEUR_15.csv"
    PYTHONPATH=. python scripts/analysis/regime_switch_oracle.py CSV --move-pct 7 --max-days 5
"""

import argparse
import dataclasses
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import grid_sweep_holdout as gsh
import rally_gate_oracle as rgo

import trading.engine as engine
import trading.optimizer.search as optimizer
from core.config import ATR_DESV_LIMIT, RECALIBRATION_BARS
from trading.engine import PairCalibration, mark_to_market, simulate_operations

CLASSES = ("lateral", "bajista", "alcista", "pico", "corto")


# --- etiquetado -------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Segment:
    label: str
    first_bar: int  # primera vela de 15 min del tramo (inclusive)
    last_bar: int  # ultima vela (inclusive)
    hold_pct: float  # retorno de mantener DENTRO del tramo, desde el cierre anterior a su primera vela
    days: int


def daily_closes(df: pd.DataFrame) -> pd.DataFrame:
    """Un cierre por dia natural (UTC) y la primera/ultima vela de 15 min que lo compone."""
    day = df["dtime"].dt.floor("D")
    grouped = df.groupby(day)
    out = pd.DataFrame(
        {
            "close": grouped["close"].last(),
            "first_bar": grouped.apply(lambda g: int(g.index.min())),
            "last_bar": grouped.apply(lambda g: int(g.index.max())),
        }
    )
    return out.reset_index(drop=True)


def impulse_days(closes: list[float], move: float, max_days: int) -> list[bool]:
    """Dias que forman parte de un movimiento de al menos ``move`` completado en ``max_days`` o menos."""
    n = len(closes)
    mark = [False] * n
    for i in range(n):
        for j in range(i + 1, min(n, i + max_days + 1)):
            if abs(closes[j] / closes[i] - 1.0) >= move:
                for k in range(i + 1, j + 1):
                    mark[k] = True
    return mark


def label_days(closes: list[float], move: float, max_days: int, min_days: int) -> list[tuple[int, int, str]]:
    """Tramos (primer dia, ultimo dia, etiqueta) que cubren todos los dias, en orden."""
    mark = impulse_days(closes, move, max_days)
    out = []
    i = 0
    while i < len(closes):
        j = i
        while j + 1 < len(closes) and mark[j + 1] == mark[i]:
            j += 1
        if mark[i]:
            label = _impulse_label(closes, i, j, move)
        elif j - i + 1 >= min_days:
            label = "lateral"
        else:
            label = "corto"
        out.append((i, j, label))
        i = j + 1
    return out


def _impulse_label(closes: list[float], first: int, last: int, move: float) -> str:
    ref = closes[first - 1] if first > 0 else closes[first]
    net = closes[last] / ref - 1.0
    if abs(net) < move / 2.0:
        return "pico"
    return "alcista" if net > 0 else "bajista"


def segments(df: pd.DataFrame, move: float, max_days: int, min_days: int) -> list[Segment]:
    days = daily_closes(df)
    closes = days["close"].astype(float).tolist()
    out = []
    for first, last, label in label_days(closes, move, max_days, min_days):
        first_bar = int(days.iloc[first]["first_bar"])
        last_bar = int(days.iloc[last]["last_bar"])
        ref = float(df.iloc[first_bar - 1]["close"]) if first_bar > 0 else float(df.iloc[first_bar]["close"])
        hold = (float(df.iloc[last_bar]["close"]) / ref - 1.0) * 100.0
        out.append(Segment(label, first_bar, last_bar, hold, last - first + 1))
    return out


def print_segments(df: pd.DataFrame, segs: list[Segment]) -> None:
    print(f"\n  {'tramo':<26} {'dias':>5} {'hold':>8}  etiqueta")
    for s in segs:
        print(
            f"  {gsh._dtime(df, s.first_bar)[:10]}..{gsh._dtime(df, s.last_bar)[5:10]} {s.days:>5} {s.hold_pct:>+7.1f}%  {s.label}"
        )
    total = sum(s.days for s in segs)
    print("\n  [premisa] reparto de dias y duracion/magnitud mediana por clase")
    for label in CLASSES:
        group = [s for s in segs if s.label == label]
        if not group:
            print(f"    {label:<8} 0 tramos")
            continue
        days = sorted(s.days for s in group)
        mags = sorted(abs(s.hold_pct) for s in group)
        print(
            f"    {label:<8} {len(group):>2} tramos  {sum(days):>3} dias ({100.0 * sum(days) / total:>4.0f} %)"
            f"  duracion mediana {days[len(days) // 2]:>3} d  |hold| mediana {mags[len(mags) // 2]:>5.1f} %"
        )


# --- conmutacion de config sin tocar el motor -------------------------------


@dataclasses.dataclass(frozen=True)
class RegimeCalibration(PairCalibration):
    """Una calibracion que ademas lleva el min_margin en vigor: el activation_schedule, plegado."""

    min_margin: float = 0.0


_ORIGINAL_ACTIVATION_DISTANCE = engine.activation_distance


def _activation_distance_with_regime(cfg, side, reference_price, atr_val, close, cal=None):
    if isinstance(cal, RegimeCalibration):
        cfg = dataclasses.replace(cfg, min_margin=cal.min_margin)
    return _ORIGINAL_ACTIVATION_DISTANCE(cfg, side, reference_price, atr_val, close, cal)


engine.activation_distance = _activation_distance_with_regime


def switched_config(ctx, by_class: dict[str, optimizer.Candidate], segs: list[Segment]) -> engine.EngineConfig:
    """Una config cuyo calendario cambia de candidato en cada frontera de tramo.

    Cada entrada combina los ultimos inputs de calibracion en vigor (los mismos que usa la
    corrida fija) con el stop_pct y el min_margin del regimen que gobierna esa vela.
    """
    points = list(ctx.calibration_points)
    base_cand = by_class[segs[0].label]
    schedule = []
    seg_i = 0
    point_i = 0
    inputs = points[0]
    cand = base_cand
    bars = sorted({p.at for p in points} | {s.first_bar for s in segs})
    for at in bars:
        while point_i < len(points) and points[point_i].at <= at:
            inputs = points[point_i]
            point_i += 1
        while seg_i < len(segs) and segs[seg_i].first_bar <= at:
            cand = by_class[segs[seg_i].label]
            seg_i += 1
        cal = optimizer._pair_calibration(cand, inputs.atr_ratio_thresholds, inputs.up_k, inputs.down_k)
        schedule.append((at, RegimeCalibration(**dataclasses.asdict(cal), min_margin=cand.min_margin or 0.0)))
    fixed = optimizer._build_engine_config(
        gsh.PAIR, base_cand, ctx.atr_ratio_thresholds, ctx.up_k, ctx.down_k, ATR_DESV_LIMIT, ()
    )
    return dataclasses.replace(fixed, calibration_schedule=tuple(schedule))


# --- barrido y puntuacion ---------------------------------------------------


def _bounds(df: pd.DataFrame, segs: list[Segment]) -> list[tuple[str, float]]:
    return [(gsh._dtime(df, s.last_bar), float(df.iloc[s.last_bar]["close"])) for s in segs]


def _ops_in(ops, df: pd.DataFrame, seg: Segment) -> int:
    t0, t1 = gsh._dtime(df, seg.first_bar), gsh._dtime(df, seg.last_bar)
    return sum(1 for op in ops if op.pnl_abs is not None and op.idx != 1 and t0 <= str(op.time) <= t1)


def _run(ctx, cfg, segs: list[Segment]) -> dict:
    """Retorno marcado total y, por tramo, acumulacion de base y operaciones cerradas."""
    ops = simulate_operations(ctx.df, cfg, fee_rate=gsh.FEE / 100.0)
    final_price = float(ctx.df.iloc[-1]["close"])
    if not ops:
        return {"eur": 0.0, "per_base": [0.0] * len(segs), "per_ops": [0] * len(segs), "ops": 0}
    per_eur = rgo._period_returns(ops, _bounds(ctx.df, segs))
    return {
        "eur": mark_to_market(ops, final_price),
        "per_base": [gsh._btc(r, s.hold_pct) for r, s in zip(per_eur, segs, strict=True)],
        "per_ops": [_ops_in(ops, ctx.df, s) for s in segs],
        "ops": sum(1 for op in ops if op.pnl_abs is not None and op.idx != 1),
    }


def _compound(values: list[float]) -> float:
    factor = 1.0
    for v in values:
        factor *= 1.0 + v / 100.0
    return (factor - 1.0) * 100.0


def class_table(results: list, segs: list[Segment], label: str, top: int, active: float) -> optimizer.Candidate | None:
    """Ranking de configs DENTRO de una clase: compuesto sobre sus tramos, y en cuantos bate a mantener."""
    idxs = [i for i, s in enumerate(segs) if s.label == label]
    if not idxs:
        return None
    days = sum(segs[i].days for i in idxs)
    rows = []
    for cand, res in results:
        base = [res["per_base"][i] for i in idxs]
        ops = sum(res["per_ops"][i] for i in idxs)
        rows.append((cand, _compound(base), sum(1 for b in base if b > 0.0), ops))
    rows.sort(key=lambda r: r[1], reverse=True)
    all_beat = sum(1 for _, _, beat, _ in rows if beat == len(idxs))
    print(f"\n  [{label}] {len(idxs)} tramos, {days} dias — {all_beat}/{len(rows)} configs baten a mantener en TODOS")
    print(f"    {'config':<22} {'compuesto':>10} {'bate':>7} {'ops':>5} {'ops/mes':>8}")
    for cand, comp, beat, ops in rows[:top]:
        print(
            f"    {gsh._signature(cand):<22} {comp:>+9.1f}% {beat:>3}/{len(idxs):<3} {ops:>5} {30.0 * ops / days:>8.1f}"
        )
    med = sorted(r[1] for r in rows)[len(rows) // 2]
    print(f"    mediana de los {len(rows)}: {med:+.1f} %")
    # The user's question: does anything that trades at least `active` times a month beat hold here?
    busy = [r for r in rows if 30.0 * r[3] / days >= active]
    if busy:
        print(f"    con >= {active:.0f} ops/mes: {len(busy)} configs, la mejor:")
        cand, comp, beat, ops = busy[0]
        print(
            f"    {gsh._signature(cand):<22} {comp:>+9.1f}% {beat:>3}/{len(idxs):<3} {ops:>5} {30.0 * ops / days:>8.1f}"
        )
    else:
        print(f"    con >= {active:.0f} ops/mes: ninguna config")
    return rows[0][0]


def main() -> int:
    ap = argparse.ArgumentParser(description="Cota de conmutar la config por regimen con etiquetas perfectas")
    ap.add_argument("csv", help="Ruta al <PAIR>_15.csv de Kraken")
    ap.add_argument("--cal-start", default="2025-01-01")
    ap.add_argument("--start", default="2025-04-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--move-pct", type=float, default=10.0, help="Movimiento minimo M de un impulso, en %.")
    ap.add_argument("--max-days", type=int, default=7, help="Dias K en los que debe completarse.")
    ap.add_argument("--min-days", type=int, default=7, help="Duracion minima D de un lateral.")
    ap.add_argument("--recalib-bars", type=int, default=RECALIBRATION_BARS)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--active", type=float, default=4.0, help="Ops/mes a partir de las cuales una config es 'activa'.")
    ap.add_argument("--labels-only", action="store_true", help="Solo etiquetar; no simular.")
    args = ap.parse_args()

    cal_t0 = int(pd.Timestamp(args.cal_start).timestamp())
    t1 = int(pd.Timestamp(args.end).timestamp()) + 86_399
    print(f"[datos] {args.csv}")
    frame = rgo.build_frame(args.csv, cal_t0, t1)
    gsh._install_ohlc(frame)

    sim_t0 = pd.Timestamp(args.start)
    first_bar = int((frame["dtime"] >= sim_t0).idxmax())
    last_bar = len(frame) - 1
    window = frame.iloc[first_bar : last_bar + 1].reset_index(drop=True)
    hold = (float(window.iloc[-1]["close"]) / float(window.iloc[0]["close"]) - 1.0) * 100.0

    print(
        f"\n[etiquetas] impulso = {args.move_pct:.0f} % en <= {args.max_days} d, lateral >= {args.min_days} d, "
        "sobre cierres diarios"
    )
    segs = segments(window, args.move_pct / 100.0, args.max_days, args.min_days)
    print_segments(window, segs)
    if args.labels_only:
        return 0

    print(f"\n[calibracion] calendario cada {args.recalib_bars} velas (tarda minutos)", flush=True)
    gsh._install_calibration_cache(frame, args.recalib_bars)
    ctx = gsh._context(frame, first_bar, last_bar, gsh._dtime(frame, last_bar))
    assert len(ctx.df) == len(window)
    print(f"\n[ventana] {gsh._dtime(window, 0)[:10]}..{gsh._dtime(window, len(window) - 1)[:10]}  hold {hold:+.2f} %")

    cands = gsh.candidates()
    print(f"\n[barrido] {len(cands)} configs fijas, corrida continua partida por tramo", flush=True)
    t0 = time.perf_counter()
    results = []
    for cand in cands:
        cfg = optimizer._build_engine_config(
            gsh.PAIR, cand, ctx.atr_ratio_thresholds, ctx.up_k, ctx.down_k, ATR_DESV_LIMIT, ctx.calibration_points
        )
        results.append((cand, _run(ctx, cfg, segs)))
    print(f"  {time.perf_counter() - t0:.0f}s")

    by_class = {}
    for label in CLASSES:
        best = class_table(results, segs, label, args.top, args.active)
        if best is not None:
            by_class[label] = best
    print("\n  [eleccion por clase]")
    for label, cand in by_class.items():
        print(f"    {label:<8} -> {gsh._signature(cand)}")
    same = len({gsh._signature(c) for c in by_class.values()}) == 1
    print(f"    {'la misma config en todas las clases' if same else 'configs distintas por clase'}")

    # Brazos: la mejor fija (elegida in-sample, como el oraculo), la recomendada, y la conmutada.
    by_sig = {gsh._signature(c): r for c, r in results}
    best_fixed = max(cands, key=lambda c: by_sig[gsh._signature(c)]["eur"])
    recommended = next(
        c for c in cands if abs(c.min_margin - 0.05) < 1e-9 and abs(next(iter(c.stop_pcts.values())) - 0.9) < 1e-9
    )
    arms = {
        "mejor fija": by_sig[gsh._signature(best_fixed)],
        "recomendada": by_sig[gsh._signature(recommended)],
        "conmutada": _run(ctx, switched_config(ctx, by_class, segs), segs),
    }
    bull = frozenset(b for s in segs if s.label == "alcista" for b in range(s.first_bar, s.last_bar + 1))
    if bull:
        cfg = dataclasses.replace(switched_config(ctx, by_class, segs), force_hold_bars=bull)
        arms["conmutada+hold alcista"] = _run(ctx, cfg, segs)
    medians = sorted(gsh._btc(r["eur"], hold) for _, r in results)
    months = sum(s.days for s in segs) / 30.0
    busy = sorted(
        ((gsh._btc(r["eur"], hold), r["ops"], c) for c, r in results if r["ops"] / months >= args.active), reverse=True
    )

    print(f"\n[resultado] acumulacion de activo base sobre la ventana, mantener = 0 % (hold {hold:+.2f} % en euros)")
    print(f"  mejor fija = {gsh._signature(best_fixed)}; recomendada = {gsh._signature(recommended)}")
    print(f"  {'brazo':<26} {'base':>8} {'ops':>5}")
    print(f"  {'mediana de las 105 fijas':<26} {medians[len(medians) // 2]:>+7.1f}%")
    for label, res in arms.items():
        print(f"  {label:<26} {gsh._btc(res['eur'], hold):>+7.1f}% {res['ops']:>5}")
    if busy:
        b, o, c = busy[0]
        print(
            f"  {len(busy)} configs con >= {args.active:.0f} ops/mes en toda la ventana; la mejor {gsh._signature(c)} {b:+.1f}% con {o} ops"
        )
    else:
        print(f"  ninguna config llega a {args.active:.0f} ops/mes en toda la ventana")

    print("\n[por tramo] acumulacion de base y operaciones cerradas dentro de cada tramo, brazo a brazo")
    names = list(arms)
    print(f"  {'tramo':<22} {'clase':<9}" + "".join(f"{n:>24}" for n in names))
    for i, s in enumerate(segs):
        row = f"  {gsh._dtime(window, s.first_bar)[:10]}..{gsh._dtime(window, s.last_bar)[5:10]} {s.label:<9}"
        row += "".join(f"{arms[n]['per_base'][i]:>+17.1f}% {arms[n]['per_ops'][i]:>4}" for n in names)
        print(row)

    ref = gsh._btc(arms[next(iter(arms))]["eur"], hold)
    print("\n[lectura] La conmutada cambia de config en cada frontera SABIENDO el regimen. Es un techo:")
    for label in list(arms)[2:]:
        print(f"          {label}: {gsh._btc(arms[label]['eur'], hold) - ref:+.1f} puntos sobre la mejor fija")
    print("          y el reloj de 15 min del simulador ya mueve un resultado +-9 puntos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
