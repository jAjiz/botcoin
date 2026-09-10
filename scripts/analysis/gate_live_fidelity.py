"""La puerta, simulada como el bot funciona de verdad: sesiones de 1 min, ATR de 15 min.

Read-only y temporal. Lee los CSV OHLCVT de Kraken directamente, sin base de datos.

Todo lo medido sobre la puerta hasta ahora corre sobre velas de 15 min y una bandera diaria,
y las dos cosas resultaron ser problemas. `execution_fidelity.py` ya habia establecido que el
motor tiene look-ahead INTRAVELA -- sube el trailing al maximo de la vela y lo compara contra
el minimo de esa misma vela, cosa que un bot que consulta precio cada 60 s no puede hacer --
y `gate_sensitivity.py` encontro que la bandera diaria se calculaba con el cierre del dia y se
aplicaba a ese mismo dia desde las 00:00, que son otras 24 h de look-ahead. Aqui se quitan las
dos a la vez.

Fidelidad, punto por punto:

  * el camino de precios es de 1 min, que es el `SLEEPING_INTERVAL` de produccion (60 s);
  * el ATR se calcula SIEMPRE sobre las velas de 15 min -- la vista de volatilidad que
    produccion tiene en `ohlc_data` -- y se proyecta hacia adelante: cada vela de 1 min toma
    el ATR de la vela de 15 min que la contiene;
  * el calendario de recalibracion se construye una vez sobre las velas de 15 min y se
    remapea por marca de tiempo, igual que hace `execution_fidelity.py`;
  * los cierres diarios que alimentan la puerta salen del marco de 15 min, no del de 1 min,
    porque es la serie que el bot tiene almacenada.

BRAZOS, fijados antes de correr, y los cuatro se reportan salga lo que salga:

  A  sin puerta      el bot tal cual, a 1 min. La referencia contra la que se mide la puerta.
  B  puerta L0       la bandera del dia i sale del cierre del dia i y se aplica a los minutos
                     de ese dia. Es lo que midio el estudio. Mira 24 h al futuro y esta aqui
                     SOLO para medir cuanto vale ese sesgo a esta fidelidad.
  C  puerta L1       la bandera del dia i sale del cierre del dia i-1. Causal, pero arrastra
                     hasta 24 h de retraso: el precio puede haber subido un 30 % desde ese
                     cierre y la puerta sigue abierta.
  D  puerta continua en cada minuto, cerrada si el PRECIO ACTUAL esta `move` por encima de
                     alguno de los `look` cierres diarios YA COMPLETADOS. Misma formula que
                     la regla original con el precio de este minuto en lugar del cierre del
                     dia: causal y sin retraso. Es la unica de las tres que podria desplegarse.

Se corre tambien a 15 min para separar dos cosas que se han estado midiendo juntas: cuanto
cambia por la resolucion del camino y cuanto por la frescura de la bandera.

El liston es mantener, que en activo base es 0 % por construccion. La referencia NO es el
brazo sin puerta: un brazo que pierde menos que otro brazo que pierde sigue perdiendo.

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):
  PYTHONPATH=. python scripts/analysis/gate_live_fidelity.py "C:/Dev/Kraken OHLCVT"
"""

import argparse
import dataclasses
import os
import time

import execution_fidelity as ef
import numpy as np
import pandas as pd

from core.config import ATR_DESV_LIMIT, RECALIBRATION_BARS
from trading.engine import EngineConfig, mark_to_market, simulate_operations

MOVE = 0.10
LOOK = 10
MIN_MARGIN = 0.020
STOP_PCT = 0.9


def daily_closes_from(coarse: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Cierre de cada dia natural UTC del marco de 15 min, y el dia al que pertenece cada vela."""
    day = coarse["dtime"].dt.floor("D")
    closes = coarse.groupby(day)["close"].last()
    return closes.index.to_numpy(), closes.to_numpy(dtype=float)


def open_days(closes: np.ndarray, move: float, look: int, lag: int) -> np.ndarray:
    """Puerta abierta el dia i, decidida con el cierre del dia i-lag.

    Abierta equivale a "el cierre de referencia esta por debajo de (1+move) x el MINIMO de los
    `look` cierres previos": es la misma condicion que `any(c_i/c_j - 1 >= move)` negada, solo
    que en forma cerrada. Los primeros `look` dias no tienen historia suficiente y quedan
    CERRADOS, que es la opcion conservadora -- sin datos no se opera.
    """
    n = len(closes)
    out = np.zeros(n, dtype=bool)
    for i in range(look + lag, n):
        ref = closes[i - lag]
        window = closes[i - lag - look : i - lag]
        out[i] = ref < (1.0 + move) * window.min()
    return out


def mask_from_days(fine: pd.DataFrame, days: np.ndarray, flags: np.ndarray) -> frozenset[int]:
    """Velas de 1 min que caen en un dia con la puerta CERRADA (el motor las fuerza a mantener)."""
    fine_day = fine["dtime"].dt.floor("D").to_numpy()
    lookup = dict(zip(days, flags, strict=True))
    is_open = np.array([lookup.get(d, False) for d in fine_day], dtype=bool)
    return frozenset(np.flatnonzero(~is_open).tolist())


def mask_continuous(fine: pd.DataFrame, days: np.ndarray, closes: np.ndarray, move: float, look: int) -> frozenset[int]:
    """Puerta evaluada en cada vela: precio actual contra los `look` cierres diarios completados.

    El minimo movil se calcula sobre dias YA CERRADOS -- para una vela del dia d entran d-1
    hasta d-look -- asi que en ningun instante entra informacion que ese minuto no tuviera.
    """
    floor_by_day = np.full(len(days), np.nan)
    for i in range(look, len(days)):
        floor_by_day[i] = closes[i - look : i].min()
    lookup = dict(zip(days, floor_by_day, strict=True))
    fine_day = fine["dtime"].dt.floor("D").to_numpy()
    floor = np.array([lookup.get(d, np.nan) for d in fine_day], dtype=float)
    price = fine["close"].to_numpy(dtype=float)
    # NaN (sin historia suficiente) -> cerrada, igual que en la version diaria.
    is_open = np.where(np.isnan(floor), False, price < (1.0 + move) * floor)
    return frozenset(np.flatnonzero(~is_open).tolist())


def run(df: pd.DataFrame, points: list, mask: frozenset[int], label: str, fee: float) -> dict:
    """Una corrida continua del config fijo sobre `df`, con la puerta dada."""
    times = df["time"].to_numpy()
    scheduled = ef.remap(points, times)
    final_price = float(df.iloc[-1]["close"])
    hold = (final_price / float(df.iloc[0]["close"]) - 1.0) * 100.0

    cfg = EngineConfig(
        pair="XBTEUR",
        calibration=ef._calibration(scheduled[0][1], STOP_PCT),
        k_act=None,
        min_margin=MIN_MARGIN,
        atr_desv_limit=ATR_DESV_LIMIT,
        calibration_schedule=tuple((at, ef._calibration(p, STOP_PCT)) for at, p in scheduled),
    )
    # `reset_on_unmask` es obligatorio en cualquier experimento con puerta: sin el, el trailing
    # sigue corriendo bajo la mascara y la salida se ancla a un maximo que la puerta ya tapo.
    cfg = dataclasses.replace(cfg, force_hold_bars=mask, reset_on_unmask=True)

    t0 = time.perf_counter()
    ops = simulate_operations(df, cfg, fee_rate=fee / 100.0)
    eur = mark_to_market(ops, final_price) if ops else 0.0
    base = ((1.0 + eur / 100.0) / (1.0 + hold / 100.0) - 1.0) * 100.0
    open_pct = 100.0 * (len(df) - len(mask)) / len(df)
    print(
        f"  {label:<22} activo base {base:>+8.1f} %   EUR {eur:>+8.1f} %   "
        f"{len(ops):>4} ops   puerta abierta {open_pct:>5.1f} %   ({time.perf_counter() - t0:.0f}s)",
        flush=True,
    )
    return {"label": label, "base": base, "eur": eur, "ops": len(ops), "open": open_pct}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir", help="Carpeta con XBTEUR_1.csv y XBTEUR_15.csv")
    ap.add_argument("--pair", default="XBTEUR")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2024-12-31")
    ap.add_argument("--lead-months", type=int, default=6, help="Historia previa para calibrar.")
    ap.add_argument("--recalib-bars", type=int, default=RECALIBRATION_BARS)
    ap.add_argument("--fee", type=float, default=ef.FEE, help="Comision por pierna, en porcentaje.")
    ap.add_argument("--move", type=float, default=MOVE)
    ap.add_argument("--look", type=int, default=LOOK)
    ap.add_argument("--resolutions", default="1,15", help="Resoluciones del camino de precios, en minutos.")
    args = ap.parse_args()

    ef.FEE = args.fee
    cal_start = (pd.Timestamp(args.start) - pd.DateOffset(months=args.lead_months)).strftime("%Y-%m-%d")
    cal_t0 = int(pd.Timestamp(cal_start).timestamp())
    sim_t0 = int(pd.Timestamp(args.start).timestamp())
    t1 = int(pd.Timestamp(args.end).timestamp()) + 86_399

    print(f"[datos] {args.pair} en {args.data_dir}   comision {args.fee} %/pierna")
    print(f"  config fija mm={MIN_MARGIN:.3f} stop={STOP_PCT}   regla m={args.move:.2f} k={args.look}")
    print(f"  calibracion desde {cal_start}, simulacion {args.start}..{args.end}")

    coarse = ef.coarse_frame(os.path.join(args.data_dir, f"{args.pair}_15.csv"), cal_t0, t1, 15)
    print(f"  15m: {len(coarse)} velas con ATR  {coarse.iloc[0]['dtime']}..{coarse.iloc[-1]['dtime']}")

    print(f"\n[calibracion] calendario cada {args.recalib_bars} velas de 15m (tarda minutos)")
    points = ef.build_schedule(coarse, args.recalib_bars)
    prior = [p for p in points if p["time"] <= sim_t0]
    points = ([prior[-1]] if prior else []) + [p for p in points if p["time"] > sim_t0]
    print(f"  {len(points)} puntos aplican a la ventana de simulacion")

    days, dcloses = daily_closes_from(coarse)
    flags_l0 = open_days(dcloses, args.move, args.look, 0)
    flags_l1 = open_days(dcloses, args.move, args.look, 1)
    print(
        f"\n[bandera diaria] {len(days)} dias   abierta L0 {flags_l0.mean() * 100:.0f} %   L1 {flags_l1.mean() * 100:.0f} %"
    )

    rows = {}
    for res in [int(x) for x in args.resolutions.split(",")]:
        path = os.path.join(args.data_dir, f"{args.pair}_{res}.csv")
        df = (
            coarse[coarse["time"] >= sim_t0].reset_index(drop=True)
            if res == 15
            else ef.fine_frame(path, coarse, sim_t0, t1, res)
        )
        hold = (float(df.iloc[-1]["close"]) / float(df.iloc[0]["close"]) - 1.0) * 100.0
        print(f"\n================ camino de {res} min ================")
        print(f"  {len(df)} velas   mantener {hold:+.2f} % EUR (0.0 % en activo base, por construccion)")
        arms = [
            ("A sin puerta", frozenset()),
            ("B puerta L0", mask_from_days(df, days, flags_l0)),
            ("C puerta L1", mask_from_days(df, days, flags_l1)),
            ("D puerta continua", mask_continuous(df, days, dcloses, args.move, args.look)),
        ]
        rows[res] = [run(df, points, mask, label, args.fee) for label, mask in arms]
        del df

    print("\n[resumen] activo base (%), por brazo y resolucion")
    labels = [r["label"] for r in next(iter(rows.values()))]
    header = f"  {'brazo':<22}" + "".join(f"{f'{r} min':>12}" for r in rows)
    print(header)
    for i, label in enumerate(labels):
        print(f"  {label:<22}" + "".join(f"{rows[r][i]['base']:>+11.1f}%" for r in rows))

    if 1 in rows and 15 in rows:
        print("\n  efecto de la resolucion (1 min - 15 min), en puntos:")
        for i, label in enumerate(labels):
            print(f"    {label:<22}{rows[1][i]['base'] - rows[15][i]['base']:>+8.1f}")
    if 1 in rows:
        b, c = rows[1][1]["base"], rows[1][2]["base"]
        print(f"\n  sesgo de look-ahead de la bandera a 1 min (B - C): {b - c:+.1f} puntos")

    print("\n[lectura] el liston es 0.0 % (mantener). Que un brazo con puerta supere al brazo A no lo")
    print("          hace viable: A pierde, y perder menos sigue siendo perder. Solo cuenta el signo")
    print("          frente a cero, y solo en los brazos causales C y D.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
