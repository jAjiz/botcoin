"""Lanza el optimizador real contra los CSV de Kraken, sin base de datos ni stack levantado.

Read-only y temporal. Monta el mismo `OptimizerRequest` que aceptaria `POST /optimizer/jobs`
y llama a `run_optimize` / `run_auto_optimize` en proceso, con el cargador de OHLC y el
calendario de calibracion parcheados igual que el resto de arneses del estudio.

Existe para poder correr una busqueda con los stops SIN unificar (cinco valores libres, que
es lo que despliega el `SearchSpace` real) sobre el motor ARREGLADO: pata de caja correcta,
`mark_to_market`, y calendario de recalibracion. Las corridas anteriores a esta rama se
puntuaron sin esos tres, y el fallo de la pata de caja inflaba precisamente a las configs de
muchas operaciones, que son las que pasan mas tiempo en euros.

El marco se carga con calentamiento por delante de `--start`: los puntos del calendario se
anclan al marco, asi que sin calentamiento las primeras recalibraciones de la ventana verian
casi nada de historia y su K_STOP no significaria nada.

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):

    PYTHONPATH=. python scripts/analysis/run_optimizer_csv.py "C:/Dev/Kraken OHLCVT/XBTEUR_15.csv" \
        --mode AUTO --fee 0.04 --train-split 0.67 --stops 0.0 1.0 0.1 --min-margin 0.004 0.004 1
"""

import argparse
import dataclasses
import json
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import grid_sweep_holdout as gsh

import trading.optimizer.search as optimizer
from api.schemas import AutoSettings, GridSpec, OptimizerRequest, SearchSpace
from core.config import CANDLE_TIMEFRAME, RECALIBRATION_BARS
from trading.market_analyzer import _wilder_atr_from_scratch


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


def main() -> int:
    ap = argparse.ArgumentParser(description="El optimizador real, contra CSV")
    ap.add_argument("csv")
    ap.add_argument("--pair", default="XBTEUR")
    ap.add_argument("--mode", default="AUTO", choices=["OPTIMIZE", "AUTO"])
    ap.add_argument("--warmup-start", default="2024-10-01", help="Historia previa para el calendario.")
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--fee", type=float, default=0.04)
    ap.add_argument("--train-split", type=float, default=0.67)
    ap.add_argument("--stops", type=float, nargs=3, default=[0.0, 1.0, 0.1], metavar=("START", "END", "STEP"))
    ap.add_argument("--min-margin", type=float, nargs=3, default=[0.004, 0.004, 1.0], metavar=("START", "END", "STEP"))
    ap.add_argument("--k-act", type=float, nargs=3, default=None, metavar=("START", "END", "STEP"))
    ap.add_argument("--n-trials", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--recalib-bars", type=int, default=RECALIBRATION_BARS)
    ap.add_argument("--out", default="optimizer_result.json", help="Donde se vuelca el resultado.")
    args = ap.parse_args()

    t0 = int(pd.Timestamp(args.warmup_start).timestamp())
    t1 = int(pd.Timestamp(args.end).timestamp()) + 86_399

    print(f"[datos] {args.csv}")
    frame = build_frame(args.csv, t0, t1)
    gsh._install_ohlc(frame)

    print(f"\n[calibracion] calendario cada {args.recalib_bars} velas (tarda minutos)", flush=True)
    gsh._install_calibration_cache(frame, args.recalib_bars)

    space = SearchSpace(
        stop_pcts=GridSpec(start=args.stops[0], end=args.stops[1], step=args.stops[2]),
        k_act=GridSpec(start=args.k_act[0], end=args.k_act[1], step=args.k_act[2]) if args.k_act else None,
        min_margin=GridSpec(start=args.min_margin[0], end=args.min_margin[1], step=args.min_margin[2]),
    )
    req = OptimizerRequest(
        pair=args.pair,
        mode=args.mode,
        fee_pct=args.fee,
        start=args.start,
        end=args.end,
        train_split=args.train_split,
        n_trials=args.n_trials,
        seed=args.seed,
        recalibration_bars=args.recalib_bars,
        auto_settings=AutoSettings() if args.mode == "AUTO" else None,
        search_space=space,
    )
    print(f"\n[peticion]\n{json.dumps(req.model_dump(exclude_none=True), indent=2, default=str)}", flush=True)

    base = gsh._calibration_at(frame, gsh._dtime(frame, len(frame) - 1))
    runner = optimizer.run_auto_optimize if args.mode == "AUTO" else optimizer.run_optimize

    print(f"\n[busqueda] {args.mode}, esto tarda", flush=True)
    t = time.perf_counter()
    result = runner(req, base)
    print(f"  {time.perf_counter() - t:.0f}s\n")

    # OptimizerResult es un dataclass, no un modelo pydantic. Se vuelca a fichero ANTES de
    # imprimir: una busqueda AUTO cuesta mas de una hora y no puede perderse por el formateo.
    payload = dataclasses.asdict(result)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"request": req.model_dump(exclude_none=True), "result": payload}, fh, indent=2, default=str)
    print(f"[guardado] {args.out}", flush=True)
    print(flush=True)
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
