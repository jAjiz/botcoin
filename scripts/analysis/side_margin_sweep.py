"""¿Acumula mas activo base vender con reticencia y recomprar con prisa?

Read-only y temporal. Lee un CSV OHLCVT de Kraken, sin base de datos.

La ultima palanca estructural sin medir sobre el motor arreglado: `min_margin` por lado.
Los `k_act`/`min_margin` por lado se quitaron de produccion por "sin beneficio observable",
pero eso se midio con la pata de caja pagada como corto y sin recalibracion. Aqui se vuelve
a medir con los tres arreglos.

El mecanismo no necesita adivinar el regimen; sale del objetivo en activo base:

  * estar FUERA durante una subida pierde bitcoins para siempre (recompras mas arriba);
  * estar DENTRO durante una bajada no cuesta ni un satoshi.

El riesgo real vive en el lado de venta; el de compra solo tiene coste de oportunidad. Eso
justifica, de forma estatica, una barrera de activacion ANCHA para vender y ESTRECHA para
recomprar. La direccion contraria se mide tambien: si las dos dan lo mismo, la asimetria no
es un mecanismo sino ruido.

Metodo: barrido PAREADO, no optimizador. Para cada config simetrica de la rejilla del
estudio (21 min_margin x 5 stop_pct compartido = 105) se simulan sus vecinas asimetricas
a distancia delta en cada direccion, en la misma corrida continua, y se reporta la
DISTRIBUCION del delta en activo base frente a la simetrica. Es el metodo que cerro los
stops por lado (+-0.4 puntos, signo contrario al mecanismo). Si el delta se reparte
alrededor de cero, se cierra aqui sin gastar mas.

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):

    PYTHONPATH=. python scripts/analysis/side_margin_sweep.py "C:/Dev/Kraken OHLCVT/XBTEUR_15.csv"
"""

import argparse
import dataclasses
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import grid_sweep_holdout as gsh

import trading.optimizer.search as optimizer
from core.config import ATR_DESV_LIMIT, CANDLE_TIMEFRAME, RECALIBRATION_BARS
from trading.engine import mark_to_market, simulate_operations
from trading.market_analyzer import _wilder_atr_from_scratch

DELTAS = (0.01, 0.02, 0.04)
# (etiqueta, signo sobre el lado sell): +1 ensancha la venta y estrecha la compra.
DIRECTIONS = (("vende reticente / compra con prisa", +1), ("vende con prisa / compra reticente", -1))


def build_frame(path: str, t0: int, t1: int) -> pd.DataFrame:
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


def score(ctx, cand, mm_sell: float, mm_buy: float, hold: float) -> tuple[float, int]:
    """Acumulacion de activo base y operaciones cerradas de una config con margenes por lado."""
    cfg = optimizer._build_engine_config(
        gsh.PAIR, cand, ctx.atr_ratio_thresholds, ctx.up_k, ctx.down_k, ATR_DESV_LIMIT, ctx.calibration_points
    )
    cfg = dataclasses.replace(cfg, min_margin_sell=mm_sell, min_margin_buy=mm_buy)
    ops = simulate_operations(ctx.df, cfg, fee_rate=gsh.FEE / 100.0)
    if not ops:
        return gsh._btc(0.0, hold), 0
    eur = mark_to_market(ops, float(ctx.df.iloc[-1]["close"]))
    closed = sum(1 for op in ops if op.pnl_abs is not None and op.idx != 1)
    return gsh._btc(eur, hold), closed


def _q(values: list[float], p: float) -> float:
    return float(np.percentile(values, p)) if values else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser(description="min_margin por lado: barrido pareado")
    ap.add_argument("csv")
    ap.add_argument("--cal-start", default="2025-01-01")
    ap.add_argument("--start", default="2025-04-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--recalib-bars", type=int, default=RECALIBRATION_BARS)
    args = ap.parse_args()

    t0 = int(pd.Timestamp(args.cal_start).timestamp())
    t1 = int(pd.Timestamp(args.end).timestamp()) + 86_399

    print(f"[datos] {args.csv}")
    frame = build_frame(args.csv, t0, t1)
    gsh._install_ohlc(frame)
    print(f"\n[calibracion] calendario cada {args.recalib_bars} velas (tarda minutos)", flush=True)
    gsh._install_calibration_cache(frame, args.recalib_bars)

    first_bar = int((frame["dtime"] >= pd.Timestamp(args.start)).idxmax())
    last_bar = len(frame) - 1
    ctx = gsh._context(frame, first_bar, last_bar, gsh._dtime(frame, last_bar))
    hold = (float(ctx.df.iloc[-1]["close"]) / float(ctx.df.iloc[0]["close"]) - 1.0) * 100.0
    print(
        f"\n[ventana] {gsh._dtime(ctx.df, 0)[:10]}..{gsh._dtime(ctx.df, len(ctx.df) - 1)[:10]}  hold {hold:+.2f} % EUR"
    )

    cands = gsh.candidates()
    n_sims = len(cands) * (1 + len(DELTAS) * len(DIRECTIONS))
    print(
        f"\n[barrido] {len(cands)} simetricas x (1 + {len(DELTAS)} deltas x 2 direcciones) = {n_sims} sims", flush=True
    )
    t = time.perf_counter()

    sym = {}
    for cand in cands:
        sym[gsh._signature(cand)] = score(ctx, cand, cand.min_margin, cand.min_margin, hold)
    sym_vals = [v for v, _ in sym.values()]
    print(f"  simetricas: mediana {np.median(sym_vals):+.1f} %, ops medias {np.mean([o for _, o in sym.values()]):.1f}")

    results = {}  # (delta, label) -> list of (delta_base, asym_base, sym_base, ops)
    for delta in DELTAS:
        for label, sign in DIRECTIONS:
            rows = []
            for cand in cands:
                mm = cand.min_margin
                mm_sell = max(mm + sign * delta, 0.0)
                mm_buy = max(mm - sign * delta, 0.0)
                if mm_sell == mm_buy:  # ambos recortados a cero: no hay asimetria que medir
                    continue
                base, ops = score(ctx, cand, mm_sell, mm_buy, hold)
                sym_base = sym[gsh._signature(cand)][0]
                rows.append((base - sym_base, base, sym_base, ops))
            results[(delta, label)] = rows
    print(f"  {time.perf_counter() - t:.0f}s")

    print("\n[resultado] delta de acumulacion de activo base frente a la config simetrica, en puntos")
    print(
        f"  {'delta':>6}  {'direccion':<36} {'n':>4} {'mediana':>8} {'media':>7} {'p25':>7} {'p75':>7} {'>0':>6} {'ops':>5}"
    )
    for (delta, label), rows in results.items():
        d = [r[0] for r in rows]
        share = 100.0 * sum(1 for x in d if x > 0) / len(d)
        ops = np.mean([r[3] for r in rows])
        print(
            f"  {delta:>6.2f}  {label:<36} {len(d):>4} {np.median(d):>+8.2f} {np.mean(d):>+7.2f} "
            f"{_q(d, 25):>+7.2f} {_q(d, 75):>+7.2f} {share:>5.0f}% {ops:>5.1f}"
        )

    print("\n[mejor de cada brazo]  (maximo de ~105 tiradas: ilustrativo, no predictivo)")
    print(f"  {'simetrica':<44} {max(sym_vals):>+8.1f} %")
    for (delta, label), rows in results.items():
        print(f"  {f'{label} d={delta:.2f}':<44} {max(r[1] for r in rows):>+8.1f} %")

    print(
        "\n[lectura] Si el mecanismo existe, 'vende reticente' tiene mediana > 0 y crece con delta,\n"
        "          y la direccion contraria tiene mediana < 0. Si ambas se reparten alrededor de\n"
        "          cero, o se mueven juntas, la asimetria por lado no es una palanca."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
