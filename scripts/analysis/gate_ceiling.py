"""El techo de CUALQUIER puerta: elegirla dia a dia sabiendo el futuro, y ver si eso basta.

Read-only y temporal. Lee los CSV OHLCVT de Kraken directamente, sin base de datos.

Cinco puertas elegidas de cinco maneras dieron 0/105, y el cruce por celda sobre 154 variantes
x 8 anos x 4 configuraciones dio 0/452 con un mejor peor-ano de -8.5 %. Todo eso son puertas
CAUSALES de un catalogo concreto, asi que siempre queda la duda de si el catalogo se queda
corto. Este script la quita por arriba: en vez de buscar una puerta mejor, construye la MEJOR
POSIBLE con conocimiento perfecto del futuro y mira si siquiera esa cruza a mantener.

  ORACULO     La puerta se representa como un booleano por dia -- abierta o cerrada -- y se
              ajusta por ascenso de colina directamente contra el resultado del bot: primero por
              bloques semanales, luego afinando dia a dia. No pretende ser realizable: son 366
              parametros libres ajustados a un solo ano. Es una COTA. Si el bot no cruza a
              mantener ni con la puerta perfecta, no hay puerta que lo haga, y el problema no
              esta en la deteccion sino en el mecanismo.

  CONTROL     Obligatorio, y por la razon que este documento ya ha aprendido dos veces: ajustar
              366 parametros libres a una sola serie da positivo sobre CUALQUIER cosa, ruido
              incluido. Se repite el mismo ascenso de colina sobre el mismo camino con los
              retornos barajados. Lo unico interpretable es cuanto SUPERA el brazo real al
              control; el nivel absoluto no dice nada.

Se corre a 15 min por coste. Lo que salga positivo se verifica a 1 min antes de creerselo.

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):
  PYTHONPATH=. python scripts/analysis/gate_ceiling.py "C:/Dev/Kraken OHLCVT" --years 2024 2025
"""

import argparse
import dataclasses
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import execution_fidelity as ef
import gate_families_live as gfl
import lateral_horizon as lh

from core.config import ATR_DESV_LIMIT, RECALIBRATION_BARS
from trading.engine import EngineConfig, mark_to_market, simulate_operations

CONFIGS = ((0.0, 0.9), (0.02, 0.9))


class Scorer:
    """Evalua una puerta (un booleano por dia) devolviendo el activo base contra mantener."""

    def __init__(self, df: pd.DataFrame, scheduled: list, mm: float, stop: float, fee: float):
        self.df = df
        self.fee = fee
        close = df["close"].to_numpy(dtype=float)
        self.final = float(close[-1])
        self.hold = (self.final / float(close[0]) - 1.0) * 100.0
        day = df["dtime"].dt.floor("D").to_numpy()
        self.day_of = np.unique(day, return_inverse=True)[1]
        self.n_days = int(self.day_of.max()) + 1
        self.cfg = EngineConfig(
            pair="XBTEUR",
            calibration=ef._calibration(scheduled[0][1], stop),
            k_act=None,
            min_margin=mm,
            atr_desv_limit=ATR_DESV_LIMIT,
            calibration_schedule=tuple((at, ef._calibration(p, stop)) for at, p in scheduled),
        )
        self.calls = 0

    def __call__(self, open_days: np.ndarray) -> float:
        self.calls += 1
        mask = frozenset(np.flatnonzero(~open_days[self.day_of]).tolist())
        cfg = dataclasses.replace(self.cfg, force_hold_bars=mask, reset_on_unmask=True)
        ops = simulate_operations(self.df, cfg, fee_rate=self.fee)
        eur = mark_to_market(ops, self.final) if ops else 0.0
        return ((1.0 + eur / 100.0) / (1.0 + self.hold / 100.0) - 1.0) * 100.0


def climb(score: Scorer, start: np.ndarray, blocks: list[int], passes: int) -> tuple[np.ndarray, float]:
    """Ascenso de colina: en cada pasada recorre los bloques y acepta el volteo que mejora.

    Bloques grandes primero (semanas) y luego dias: converge en muchas menos evaluaciones que
    ir dia a dia desde el principio, y el motor cuesta lo mismo evalue lo que evalue.
    """
    cur = start.copy()
    best = score(cur)
    for size in blocks:
        for _ in range(passes):
            improved = False
            for a in range(0, score.n_days, size):
                trial = cur.copy()
                trial[a : a + size] = ~trial[a : a + size]
                val = score(trial)
                if val > best + 1e-9:
                    cur, best, improved = trial, val, True
            if not improved:
                break
    return cur, best


def build(data_dir: str, pair: str, year: int, recalib: int):
    t0 = int(pd.Timestamp(f"{year}-01-01").timestamp())
    lead = t0 - 86_400 * 200
    end = int(pd.Timestamp(f"{year}-12-31").timestamp()) + 86_399
    coarse = ef.coarse_frame(os.path.join(data_dir, f"{pair}_{lh.BAR}.csv"), lead, end, lh.BAR)
    pts = ef.build_schedule(coarse, recalib)
    keep = coarse["time"].to_numpy() >= t0
    df = coarse[keep].reset_index(drop=True)
    prior = [p for p in pts if p["time"] <= t0]
    pts = ([prior[-1]] if prior else []) + [p for p in pts if p["time"] > t0]
    return coarse, df, ef.remap(pts, df["time"].to_numpy()), keep


def shuffled_frame(coarse: pd.DataFrame, keep: np.ndarray, seed: int) -> tuple[pd.DataFrame, np.ndarray]:
    """El mismo marco con los retornos del ANO barajados, y su OHLC y ATR rehechos.

    Dos cosas que la primera version hizo mal y que invalidaban el control:

      - Barajaba sobre el marco entero, calentamiento incluido, asi que el retorno del ANO no se
        conservaba: un camino sintetico que cayera un 99.9 % dentro del ano hace que cualquier
        cosa parezca astronomicamente mejor que mantener. Ahora la permutacion se aplica solo a
        los retornos de dentro del ano, de modo que el sintetico empieza y acaba donde el real.
      - Dejaba el ATR del camino real pegado al camino sintetico, asi que el bot media su
        distancia de stop con la volatilidad de otra serie. Ahora el rango de cada vela se
        traslada en proporcion al cierre nuevo y el ATR de Wilder se recalcula encima.
    """
    rng = np.random.default_rng(seed)
    out = coarse.copy()
    close = coarse["close"].to_numpy(dtype=float)
    idx = np.flatnonzero(keep)
    a, b = int(idx[0]), int(idx[-1])
    inner = np.diff(np.log(close[a : b + 1]))
    new = close.copy()
    new[a : b + 1] = np.exp(np.concatenate(([np.log(close[a])], np.log(close[a]) + np.cumsum(rng.permutation(inner)))))
    scale = new / close
    for col in ("open", "high", "low", "close"):
        if col in out.columns:
            out[col] = out[col].to_numpy(dtype=float) * scale
    out["atr"] = ef._wilder_atr_from_scratch(out, ef.ATR_PERIOD)
    return out.dropna(subset=["atr"]).reset_index(drop=True), new


def causal_gate(coarse: pd.DataFrame, keep: np.ndarray, close: np.ndarray) -> np.ndarray:
    """La puerta simetrica actual sobre un camino cualquiera, como punto de partida."""
    frame = coarse.assign(close=close)
    days, dclose = ef.daily_closes_from(frame)
    s = gfl.Series(frame, days, dclose, dclose, dclose)
    return gfl.det_impulse(s, 0.07, 7)[keep]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir")
    ap.add_argument("--pair", default="XBTEUR")
    ap.add_argument("--years", nargs="*", type=int, default=[2024, 2025])
    ap.add_argument("--fee", type=float, default=0.4)
    ap.add_argument("--recalib-bars", type=int, default=RECALIBRATION_BARS)
    ap.add_argument("--passes", type=int, default=3)
    ap.add_argument("--shuffles", type=int, default=2)
    args = ap.parse_args()

    ef.FEE = args.fee
    fee = args.fee / 100.0
    print("[techo] la puerta se elige dia a dia CON el futuro delante. Es una cota, no una estrategia.")
    print(f"  velas de {lh.BAR} min, comision {args.fee} %/pierna, {args.shuffles} caminos barajados de control")

    for year in args.years:
        coarse, df, scheduled, keep = build(args.data_dir, args.pair, year, args.recalib_bars)
        print(f"\n================ {year} ================", flush=True)

        # Brazos: el real y sus barajados, cada uno con su marco entero coherente.
        arms = [("real", coarse, df, keep)]
        for k in range(args.shuffles):
            fake_coarse, _ = shuffled_frame(coarse, keep, 1000 * year + k)
            fk = fake_coarse["time"].to_numpy() >= int(pd.Timestamp(f"{year}-01-01").timestamp())
            arms.append((f"barajado {k + 1}", fake_coarse, fake_coarse[fk].reset_index(drop=True), fk))

        for mm, stop in CONFIGS:
            print(f"\n  --- config mm={mm:.2f} stop={stop} ---")
            print(
                f"    {'camino':>14}{'mantener':>11}{'causal':>10}{'oraculo':>10}{'abierta':>10}{'evals':>8}{'seg':>7}"
            )
            for tag, arm_coarse, pdf, arm_keep in arms:
                score = Scorer(pdf, scheduled, mm, stop, fee)
                seed = causal_gate(arm_coarse, arm_keep, arm_coarse["close"].to_numpy(dtype=float))
                start = np.zeros(score.n_days, dtype=bool)
                for d in range(score.n_days):
                    start[d] = bool(seed[score.day_of == d].mean() > 0.5)
                base = score(start)
                t0 = time.perf_counter()
                best_days, best = climb(score, start, [7, 1], args.passes)
                print(
                    f"    {tag:>14}{score.hold:>10.1f}%{base:>9.1f}%{best:>9.1f}%"
                    f"{100 * best_days.mean():>9.1f}%{score.calls:>8}{time.perf_counter() - t0:>7.0f}",
                    flush=True,
                )
        del coarse, df

    print("\n[lectura] la cifra que importa es cuanto supera el brazo REAL a los barajados. Ajustar")
    print("          366 parametros libres a una serie da positivo sobre ruido puro, asi que el nivel")
    print("          absoluto del oraculo no dice nada por si solo. Y si ni el oraculo real cruza a")
    print("          mantener, ninguna puerta lo hara y el problema no esta en la deteccion.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
