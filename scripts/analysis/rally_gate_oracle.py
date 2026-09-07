"""Cota superior HONESTA de una puerta de rally: ¿cuanto vale evitar las subidas?

Read-only y temporal. Lee un CSV OHLCVT de Kraken, sin base de datos.

Por que existe. Ya habia un oraculo de esto y dio +0.6 puntos, pero medía una
intervencion mas debil de lo que su nombre sugeria: suprimia SOLO las ventas. La perdida de
un rally tiene dos mitades y esa maquina tapaba una:

  1. vender durante la subida            -> la mascara de ventas lo tapa
  2. estar en euros mientras sube y       -> NO lo tapa, y no podia: la recompra estaba
     tener que recomprar mas arriba          excluida a proposito del gate

Si el bot ya estaba en euros cuando abre el rally, aquella mascara era INERTE: no habia
ninguna venta que suprimir, el bot se comia la subida desde fuera y recompraba arriba
exactamente igual que sin oraculo. Su propio docstring lo decia ("a bot sitting in cash when
a rally opens would not track hold by declining to sell") y se leyo como nota al pie.

Asi que +0.6 acota "diferir ventas durante un rally". No acota "evitar el rally".

Que mide este. La intervencion fuerte, resimulada de verdad: durante los periodos que SI
subieron, el bot debe estar INTEGRAMENTE en el activo. `EngineConfig.force_hold_bars`
suprime la venta (el stop sigue trepando, asi que la salida se difiere, no se cancela) y
ademas FUERZA LA ENTRADA al precio de la vela si el bot estaba en euros, pagando su
comision. Eso es lo maximo que este bot puede hacer con un rally detectado: dentro de su
espacio de acciones (aguantar o darse la vuelta) no hay respuesta mejor que "no vender y,
si estoy fuera, entrar".

Nada de overlays. Un overlay sustituye el resultado de un periodo alcista por el de hold
(0 % en base) en vez de resimular, y eso NO es la misma cantidad: la intervencion cambia lo
que el bot lleva encima DESPUES del tramo, y todo lo de despues depende de eso. Esa
confusion ya costo un +21.9 que resulto ser +0.6.

Como leerlo. El oraculo elige los periodos alcistas con conocimiento perfecto del futuro,
que ningun detector va a tener. Es una COTA: si ni sabiendolo todo gana, no hay premio que
perseguir y no merece la pena construir clasificador. Si gana mucho, entonces la pregunta
pasa a ser si existe una senal, y solo entonces vale la pena buscarla.

Puntuacion en ACTIVO BASE, que es el objetivo: (1 + r_bot) / (1 + r_hold) - 1. Mantener es
0 % por construccion, en cualquier regimen.

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):

    PYTHONPATH=. python scripts/analysis/rally_gate_oracle.py "C:/Dev/Kraken OHLCVT/XBTEUR_15.csv"
"""

import argparse
import dataclasses
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import grid_sweep_holdout as gsh

import trading.optimizer.search as optimizer
from core.config import ATR_DESV_LIMIT, CANDLE_TIMEFRAME, RECALIBRATION_BARS
from trading.engine import mark_to_market, simulate_operations
from trading.market_analyzer import _wilder_atr_from_scratch

BARS_PER_DAY = (24 * 60) // CANDLE_TIMEFRAME


def build_frame(path: str, t0: int, t1: int) -> pd.DataFrame:
    """El marco de 15 min recortado a [t0, t1] con su ATR de Wilder."""
    df = pd.read_csv(path, header=None, names=gsh.CSV_COLUMNS)
    df = df[(df["time"] >= t0) & (df["time"] <= t1)]
    df = df.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
    step = CANDLE_TIMEFRAME * 60
    holes = df["time"].diff().dropna()
    holes = holes[holes != step]
    if len(holes):
        print(f"  AVISO: {len(holes)} saltos (mayor: {int(holes.max()) // step} velas)")
    df["atr"] = _wilder_atr_from_scratch(df, gsh.ATR_PERIOD)
    df["dtime"] = pd.to_datetime(df["time"], unit="s")
    df = df.dropna(subset=["atr"]).reset_index(drop=True)
    print(f"  marco: {len(df)} velas con ATR  {gsh._dtime(df, 0)[:16]}..{gsh._dtime(df, len(df) - 1)[:16]}")
    return df


def periods(df: pd.DataFrame, period_days: int) -> list[tuple[int, int, float]]:
    """(primera vela, ultima vela, retorno de mantener en %) por periodo consecutivo."""
    span = period_days * BARS_PER_DAY
    out = []
    for start in range(0, len(df), span):
        end = min(start + span, len(df)) - 1
        if end - start < span // 2:  # una cola corta se pega al periodo anterior
            if out:
                prev = out[-1]
                end_px = float(df.iloc[end]["close"])
                out[-1] = (prev[0], end, (end_px / float(df.iloc[prev[0]]["close"]) - 1.0) * 100.0)
            break
        first_px, last_px = float(df.iloc[start]["close"]), float(df.iloc[end]["close"])
        out.append((start, end, (last_px / first_px - 1.0) * 100.0))
    return out


def _period_returns(ops, bounds: list[tuple[str, float]]) -> list[float]:
    """Retorno en euros DENTRO de cada periodo, por el cociente de factores de crecimiento.

    ``bounds`` son (instante de cierre del periodo, precio de cierre), en orden. Marcar a
    mercado en cada frontera y dividir factores es la unica forma honesta de partir una
    corrida continua: no reinicia nada, no liquida nada, y la posicion sigue viva a traves de
    la frontera exactamente igual que en la corrida entera.
    """
    out, prev = [], 1.0
    for t, px in bounds:
        upto = [op for op in ops if str(op.time) <= t]
        cum = mark_to_market(upto, px) if upto else 0.0
        factor = 1.0 + cum / 100.0
        out.append(-100.0 if prev <= 0.0 else ((factor / prev) - 1.0) * 100.0)
        prev = factor
    return out


def sweep(ctx, cands, mask: frozenset, bounds=None) -> list:
    """Retorno marcado en euros, operaciones cerradas y (si se piden) retornos por periodo."""
    out = []
    final_price = float(ctx.df.iloc[-1]["close"])
    for cand in cands:
        cfg = optimizer._build_engine_config(
            gsh.PAIR, cand, ctx.atr_ratio_thresholds, ctx.up_k, ctx.down_k, ATR_DESV_LIMIT, ctx.calibration_points
        )
        cfg = dataclasses.replace(cfg, force_hold_bars=mask)
        ops = simulate_operations(ctx.df, cfg, fee_rate=gsh.FEE / 100.0)
        if not ops:
            out.append((cand, 0.0, 0, [0.0] * len(bounds or [])))
            continue
        closed = sum(1 for op in ops if op.pnl_abs is not None and op.idx != 1)
        per = _period_returns(ops, bounds) if bounds else []
        out.append((cand, round(mark_to_market(ops, final_price), 2), closed, per))
    return out


def _stats(rows, hold: float) -> dict:
    btc = sorted(gsh._btc(eur, hold) for _, eur, _, _ in rows)
    n = len(btc)
    return {
        "median": btc[n // 2],
        "best": btc[-1],
        "worst": btc[0],
        "beat": sum(1 for v in btc if v > 0.0),
        "n": n,
        "ops": sum(c for _, _, c, _ in rows) / n,
    }


def report(arms: dict[str, dict], hold: float) -> None:
    print(f"\n[resultado] acumulacion de activo base, mantener = 0 % (hold en euros {hold:+.2f} %)")
    print(f"  {'brazo':<28} {'mediana':>9} {'mejor':>9} {'peor':>9} {'bate hold':>11} {'ops medias':>11}")
    base = None
    for label, st in arms.items():
        print(
            f"  {label:<28} {st['median']:>8.1f}% {st['best']:>8.1f}% {st['worst']:>8.1f}% "
            f"{st['beat']:>6}/{st['n']:<4} {st['ops']:>11.1f}"
        )
        if base is None:
            base = st
    for label, st in list(arms.items())[1:]:
        print(f"\n  {label} vs sin puerta: mediana {st['median'] - base['median']:+.1f} puntos")


def main() -> int:
    ap = argparse.ArgumentParser(description="Cota honesta de una puerta de rally (asignacion forzada)")
    ap.add_argument("csv", help="Ruta al <PAIR>_15.csv de Kraken")
    ap.add_argument("--cal-start", default="2025-01-01", help="Inicio de la historia de calibracion.")
    ap.add_argument("--start", default="2025-04-01", help="Inicio de la simulacion (tras el calentamiento).")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--period-days", type=int, default=60)
    ap.add_argument("--rally-pct", type=float, default=5.0, help="Hold por encima de esto marca periodo alcista.")
    ap.add_argument("--recalib-bars", type=int, default=RECALIBRATION_BARS)
    args = ap.parse_args()

    cal_t0 = int(pd.Timestamp(args.cal_start).timestamp())
    t1 = int(pd.Timestamp(args.end).timestamp()) + 86_399

    print(f"[datos] {args.csv}")
    frame = build_frame(args.csv, cal_t0, t1)
    gsh._install_ohlc(frame)

    print(f"\n[calibracion] calendario cada {args.recalib_bars} velas (tarda minutos)", flush=True)
    gsh._install_calibration_cache(frame, args.recalib_bars)

    sim_t0 = pd.Timestamp(args.start)
    first_bar = int((frame["dtime"] >= sim_t0).idxmax())
    last_bar = len(frame) - 1
    decided_at = gsh._dtime(frame, last_bar)
    ctx = gsh._context(frame, first_bar, last_bar, decided_at)

    hold = (float(ctx.df.iloc[-1]["close"]) / float(ctx.df.iloc[0]["close"]) - 1.0) * 100.0
    print(f"\n[ventana] {gsh._dtime(ctx.df, 0)[:10]}..{gsh._dtime(ctx.df, len(ctx.df) - 1)[:10]}  {len(ctx.df)} velas")

    print(f"\n[periodos] de {args.period_days} dias; alcista = hold > {args.rally_pct:+.1f} %")
    mask: set[int] = set()
    pers = periods(ctx.df, args.period_days)
    for start, end, ret in pers:
        rally = ret > args.rally_pct
        if rally:
            mask.update(range(start, end + 1))
        flag = "  <-- ALCISTA, se bloquea" if rally else ""
        print(f"  {gsh._dtime(ctx.df, start)[:10]}..{gsh._dtime(ctx.df, end)[:10]}  hold {ret:+7.2f} %{flag}")
    bounds = [(gsh._dtime(ctx.df, en), float(ctx.df.iloc[en]["close"])) for _, en, _ in pers]
    print(f"  {len(mask)} de {len(ctx.df)} velas bloqueadas ({100.0 * len(mask) / len(ctx.df):.0f} %)")
    if not mask:
        print("  ningun periodo alcista: nada que acotar")
        return 0

    cands = gsh.candidates()
    print(f"\n[barrido] {len(cands)} configs x 2 brazos", flush=True)
    t0 = time.perf_counter()
    ungated = sweep(ctx, cands, frozenset(), bounds)
    gated = sweep(ctx, cands, frozenset(mask), bounds)
    print(f"  {time.perf_counter() - t0:.0f}s")

    report({"sin puerta": _stats(ungated, hold), "oraculo (asignacion forzada)": _stats(gated, hold)}, hold)

    print("\n[por periodo] mediana de acumulacion de activo base DENTRO de cada tramo")
    print(f"  {'periodo':<26} {'hold':>8} {'sin puerta':>12} {'oraculo':>10} {'delta':>8}")
    for i, (st, en, hret) in enumerate(pers):
        med_u = sorted(gsh._btc(r[3][i], hret) for r in ungated)[len(ungated) // 2]
        med_g = sorted(gsh._btc(r[3][i], hret) for r in gated)[len(gated) // 2]
        label = f"{gsh._dtime(ctx.df, st)[:10]}..{gsh._dtime(ctx.df, en)[5:10]}"
        label += " *" if hret > args.rally_pct else ""
        print(f"  {label:<26} {hret:>7.1f}% {med_u:>11.1f}% {med_g:>9.1f}% {med_g - med_u:>+7.1f}")
    print("  (* tramo bloqueado por el oraculo)")

    print(
        "\n[lectura] El oraculo elige los periodos alcistas SABIENDO el futuro. Es un techo:\n"
        "          si la mejora es menor que los +-9 puntos que aporta el propio reloj del\n"
        "          simulador, no hay premio y ningun detector merece construirse."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
