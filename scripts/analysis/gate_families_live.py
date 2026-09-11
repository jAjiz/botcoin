"""Todas las familias de detector, en su version CONTINUA, a fidelidad de produccion.

Read-only y temporal. Lee los CSV OHLCVT de Kraken directamente, sin base de datos.

`gate_live_fidelity.py` establecio dos cosas sobre 2024. Que la puerta evaluada en cada vela
de 1 min contra los cierres diarios ya completados (+3.4 % de activo base) no es la misma cosa
que la puerta diaria con un dia de retraso (-19.4 %): lo que mataba a la regla no era la
causalidad sino el RETRASO, y la granularidad diaria era una herencia del arnes, no parte de la
hipotesis. Y que bajar de 15 min a 1 min vale 19 puntos al bot incluso sin puerta, porque el
motor a 15 min sube el trailing al maximo de la vela y lo compara contra el minimo de esa misma
vela. Aqui se aplica esa leccion a TODAS las familias que en algun momento salieron bien.

Solo se simulan brazos que un bot puede ejecutar: camino de 1 min, ATR de 15 min proyectado,
calendario de calibracion de 15 min remapeado, y detectores que en cada vela miran unicamente
dias YA COMPLETADOS mas el precio de ese instante. No hay brazo con look-ahead ni brazo diario:
los dos ya estan medidos y los dos estan descartados.

La conversion de cada familia a continua es siempre la misma idea -- donde la version diaria
usaba "el cierre de hoy", la continua usa "el precio de este minuto", y la ventana de
referencia son los `n` dias cerrados anteriores:

  alcista   cerrada si el precio esta `move` por encima de algun cierre de los `look` dias
            previos. Es la familia del +3.4 %; aqui se le mapea la superficie entera.
  impulso   igual pero simetrica: tambien cierra si el precio esta `move` por DEBAJO. Es la
            referencia obligatoria, porque la diferencia contra `alcista` mide lo que aporta
            operar las caidas.
  bajo max  abierta solo si el precio esta al menos `pct` por debajo del maximo de `n` dias.
  sin max   abierta mientras el precio no supere el maximo de los `n` dias previos.
  bajo ema  abierta solo por debajo de la media exponencial de `n` dias.
  caja      abierta si los `n` dias previos y el precio actual caben en una caja de `span`.
  er        ratio de eficiencia de Kaufman sobre `n` dias cerrados mas el precio actual.
  rotura    con estado: al lateralizar fija el techo del rango y cierra en el INSTANTE en que
            el precio lo cruza. A 1 min esta familia es la que mas gana con la resolucion,
            porque su premisa era precisamente no esperar a que un movimiento se complete.

El `delay` de confirmacion se conserva y se generaliza: la puerta cierra en cuanto la condicion
falla, y abre solo tras `delay` dias DE RELOJ con la condicion cumpliendose sin interrupcion
(`delay * 1440` velas). La asimetria es deliberada -- perder un rango cuesta cero porque fuera
de la puerta se mantiene, y mantener es 0 % en activo base; abrir dentro de una tendencia si
cuesta.

El liston es 0.0 % en activo base, que es mantener. El brazo sin puerta se imprime como
referencia de cuanto dano hay que reparar, no como listón.

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):
  PYTHONPATH=. python scripts/analysis/gate_families_live.py "C:/Dev/Kraken OHLCVT"
"""

import argparse
import dataclasses
import json
import os
import time

import execution_fidelity as ef
import numpy as np
import pandas as pd

from core.config import ATR_DESV_LIMIT, RECALIBRATION_BARS
from trading.engine import EngineConfig, mark_to_market, simulate_operations

# --- rejillas, fijadas antes de correr --------------------------------------

MOVES = (0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.12, 0.15, 0.20, 0.25)
LOOKS = (3, 5, 7, 10, 14, 20, 30)

IMPULSE = [(m, k) for m in (0.07, 0.10, 0.15) for k in (5, 7, 10, 14)]
OFFHIGH = [(n, p, d) for n in (20, 30, 60) for p in (0.03, 0.05, 0.10) for d in (0, 3)]
NOHIGH = [(n, d) for n in (10, 20, 30) for d in (0, 3)]
EMA = [(n, d) for n in (20, 50, 100) for d in (0, 3)]
BOX = [(n, s, d) for n in (10, 20, 30) for s in (0.06, 0.10, 0.15) for d in (0, 3)]
ER = [(n, t, d) for n in (10, 20, 30) for t in (0.2, 0.3, 0.4) for d in (0, 3)]
BREAK = [
    (entry, n, sp, mg)
    for entry, n, sp in (("caja", 20, 0.10), ("caja", 30, 0.15), ("sinmax", 20, 0.0))
    for mg in (0.0, 0.02)
]

BARS_PER_DAY = 1440


# La configuracion del trailing-stop se mantiene FIJA en todo el banco: lo unico que varia
# entre variantes es la puerta, para que la comparacion tenga un solo grado de libertad.
MIN_MARGIN = 0.020
STOP_PCT = 0.9


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


def _roll(values: np.ndarray, n: int, fn) -> np.ndarray:
    """`fn` sobre los `n` dias CERRADOS anteriores a cada dia. NaN mientras no haya historia."""
    out = np.full(len(values), np.nan)
    for i in range(n, len(values)):
        out[i] = fn(values[i - n : i])
    return out


def _confirm(raw: np.ndarray, delay_days: int) -> np.ndarray:
    """Cierra en cuanto `raw` falla; abre solo tras `delay_days` de reloj cumpliendose."""
    if delay_days <= 0:
        return raw
    need = delay_days * BARS_PER_DAY
    out = np.zeros(len(raw), dtype=bool)
    run = 0
    for i, ok in enumerate(raw):
        run = run + 1 if ok else 0
        out[i] = bool(ok) and run > need
    return out


class Series:
    """Todo lo que un detector continuo necesita, ya alineado a la rejilla fina."""

    def __init__(self, fine: pd.DataFrame, days: np.ndarray, dclose: np.ndarray, dhigh: np.ndarray, dlow: np.ndarray):
        self.price = fine["close"].to_numpy(dtype=float)
        pos = {d: i for i, d in enumerate(days)}
        self.day_idx = np.array([pos[d] for d in fine["dtime"].dt.floor("D").to_numpy()], dtype=int)
        self.dclose, self.dhigh, self.dlow = dclose, dhigh, dlow
        self.n_days = len(days)
        # Primera y ultima vela fina de cada dia, para la familia con estado.
        self.day_start = np.searchsorted(self.day_idx, np.arange(self.n_days), side="left")
        self.day_end = np.searchsorted(self.day_idx, np.arange(self.n_days), side="right")

    def per_day(self, values: np.ndarray) -> np.ndarray:
        """Difunde un escalar por dia a cada vela de ese dia."""
        return values[self.day_idx]


def det_up(s: Series, move: float, look: int, delay: int = 0) -> np.ndarray:
    floor = s.per_day(_roll(s.dclose, look, np.min))
    return _confirm(np.where(np.isnan(floor), False, s.price < (1.0 + move) * floor), delay)


def det_impulse(s: Series, move: float, look: int, delay: int = 0) -> np.ndarray:
    lo = s.per_day(_roll(s.dclose, look, np.min))
    hi = s.per_day(_roll(s.dclose, look, np.max))
    ok = (s.price < (1.0 + move) * lo) & (s.price > (1.0 - move) * hi)
    return _confirm(np.where(np.isnan(lo), False, ok), delay)


def det_off_high(s: Series, n: int, pct: float, delay: int) -> np.ndarray:
    hi = s.per_day(_roll(s.dclose, n, np.max))
    return _confirm(np.where(np.isnan(hi), False, s.price <= (1.0 - pct) * hi), delay)


def det_no_new_high(s: Series, n: int, delay: int) -> np.ndarray:
    hi = s.per_day(_roll(s.dclose, n, np.max))
    return _confirm(np.where(np.isnan(hi), False, s.price < hi), delay)


def det_below_ema(s: Series, n: int, delay: int) -> np.ndarray:
    k = 2.0 / (n + 1.0)
    ema = np.empty(s.n_days)
    acc = s.dclose[0]
    for i, c in enumerate(s.dclose):
        acc = c * k + acc * (1 - k)
        ema[i] = acc
    # El valor de AYER: el de hoy incorporaria el cierre de hoy, que aun no existe.
    prev = np.concatenate(([np.nan], ema[:-1]))
    ref = s.per_day(prev)
    return _confirm(np.where(np.isnan(ref), False, s.price < ref), delay)


def det_box(s: Series, n: int, span: float, delay: int) -> np.ndarray:
    lo = np.minimum(s.per_day(_roll(s.dclose, n, np.min)), s.price)
    hi = np.maximum(s.per_day(_roll(s.dclose, n, np.max)), s.price)
    mid = (hi + lo) / 2.0
    ok = np.where(mid > 0, (hi - lo) / np.where(mid > 0, mid, 1.0) <= span, False)
    return _confirm(np.where(np.isnan(lo) | np.isnan(hi), False, ok), delay)


def det_er(s: Series, n: int, thresh: float, delay: int) -> np.ndarray:
    """Kaufman sobre los `n` dias cerrados, con el precio actual como punto final del camino."""
    path = _roll(s.dclose, n, lambda w: float(np.abs(np.diff(w)).sum()))
    first = _roll(s.dclose, n, lambda w: float(w[0]))
    last = _roll(s.dclose, n, lambda w: float(w[-1]))
    p, f, m = s.per_day(path), s.per_day(first), s.per_day(last)
    total = p + np.abs(s.price - m)
    er = np.where(total > 0, np.abs(s.price - f) / np.where(total > 0, total, 1.0), 1.0)
    return _confirm(np.where(np.isnan(p), False, er <= thresh), delay)


def det_breakout(s: Series, entry: str, n: int, span: float, margin: float, delay: int = 3) -> np.ndarray:
    """Al lateralizar fija el techo del rango; cierra en el INSTANTE en que el precio lo cruza.

    La entrada se evalua en la frontera del dia (con dias cerrados); la ruptura, en cada vela.
    Una vez rota, el resto del dia queda cerrado y la entrada se vuelve a evaluar al dia siguiente.
    """
    out = np.zeros(len(s.price), dtype=bool)
    lo_roll = _roll(s.dclose, n, np.min)
    hi_roll = _roll(s.dclose, n, np.max)
    hi_h = _roll(s.dhigh, n, np.max)
    open_, ceil, run = False, 0.0, 0
    for d in range(s.n_days):
        a, b = s.day_start[d], s.day_end[d]
        if a >= b:
            continue
        if open_:
            breach = np.flatnonzero(s.price[a:b] > ceil)
            if breach.size:
                out[a : a + breach[0]] = True
                open_, run = False, 0
                continue
            out[a:b] = True
            continue
        if np.isnan(lo_roll[d]) or np.isnan(hi_roll[d]):
            continue
        if entry == "caja":
            mid = (hi_roll[d] + lo_roll[d]) / 2.0
            ok = mid > 0 and (hi_roll[d] - lo_roll[d]) / mid <= span
        else:
            ok = s.price[a] < hi_roll[d]
        run = run + 1 if ok else 0
        if ok and run > delay:
            open_ = True
            ceil = hi_h[d] * (1.0 + margin)
    return out


def families(s: Series) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    for m in MOVES:
        for k in LOOKS:
            out[f"alcista m={m:.2f} k={k}"] = det_up(s, m, k)
    for m, k in IMPULSE:
        out[f"impulso m={m:.2f} k={k}"] = det_impulse(s, m, k)
    for n, p, d in OFFHIGH:
        out[f"bajo max n={n} p={p:.2f} d={d}"] = det_off_high(s, n, p, d)
    for n, d in NOHIGH:
        out[f"sin max n={n} d={d}"] = det_no_new_high(s, n, d)
    for n, d in EMA:
        out[f"bajo ema n={n} d={d}"] = det_below_ema(s, n, d)
    for n, sp, d in BOX:
        out[f"caja n={n} s={sp:.2f} d={d}"] = det_box(s, n, sp, d)
    for n, t, d in ER:
        out[f"er n={n} t={t:.1f} d={d}"] = det_er(s, n, t, d)
    for entry, n, sp, mg in BREAK:
        tag = f"rotura {entry} n={n}" + (f" s={sp:.2f}" if entry == "caja" else "") + f" m={mg:.2f}"
        out[tag] = det_breakout(s, entry, n, sp, mg)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir")
    ap.add_argument("--pair", default="XBTEUR")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2024-12-31")
    ap.add_argument("--lead-months", type=int, default=6)
    ap.add_argument("--recalib-bars", type=int, default=RECALIBRATION_BARS)
    ap.add_argument("--fee", type=float, default=ef.FEE)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--out", default=None, help="Vuelca el resultado por variante a JSON, para cruzar ventanas.")
    args = ap.parse_args()

    ef.FEE = args.fee
    cal_start = (pd.Timestamp(args.start) - pd.DateOffset(months=args.lead_months)).strftime("%Y-%m-%d")
    cal_t0 = int(pd.Timestamp(cal_start).timestamp())
    sim_t0 = int(pd.Timestamp(args.start).timestamp())
    t1 = int(pd.Timestamp(args.end).timestamp()) + 86_399

    print(f"[datos] {args.pair}   comision {args.fee} %/pierna   config fija mm={MIN_MARGIN:.3f} stop={STOP_PCT}")
    print(f"  camino de 1 min, ATR de 15 min, {args.start}..{args.end} (calibra desde {cal_start})")

    coarse = ef.coarse_frame(os.path.join(args.data_dir, f"{args.pair}_15.csv"), cal_t0, t1, 15)
    print(f"\n[calibracion] cada {args.recalib_bars} velas de 15m")
    points = ef.build_schedule(coarse, args.recalib_bars)
    prior = [p for p in points if p["time"] <= sim_t0]
    points = ([prior[-1]] if prior else []) + [p for p in points if p["time"] > sim_t0]

    day_index, dclose = ef.daily_closes_from(coarse)
    day = coarse["dtime"].dt.floor("D")
    dhigh = coarse.groupby(day)["high"].max().to_numpy(dtype=float)
    dlow = coarse.groupby(day)["low"].min().to_numpy(dtype=float)

    fine = ef.fine_frame(os.path.join(args.data_dir, f"{args.pair}_1.csv"), coarse, sim_t0, t1, 1)
    hold = (float(fine.iloc[-1]["close"]) / float(fine.iloc[0]["close"]) - 1.0) * 100.0
    print(f"\n[ventana] {len(fine)} velas de 1 min   mantener {hold:+.2f} % EUR (0.0 % en activo base)")

    # Los arrays diarios van COMPLETOS, con el arranque de calibracion incluido: recortarlos a
    # la ventana dejaria sin historia a los primeros `n` dias y la puerta arrancaria cerrada.
    s = Series(fine, day_index, dclose, dhigh, dlow)

    print("")
    ref = run(fine, points, frozenset(), "sin puerta", args.fee)

    t0 = time.perf_counter()
    dets = families(s)
    print(f"\n[detectores] {len(dets)} variantes continuas construidas en {time.perf_counter() - t0:.0f}s", flush=True)

    rows = []
    t0 = time.perf_counter()
    for i, (name, is_open) in enumerate(dets.items(), 1):
        mask = frozenset(np.flatnonzero(~is_open).tolist())
        rows.append(run(fine, points, mask, name, args.fee))
        if i % 25 == 0:
            print(f"    ... {i}/{len(dets)} ({time.perf_counter() - t0:.0f}s)", flush=True)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"window": f"{args.start}..{args.end}", "ungated": ref["base"], "rows": rows}, fh)
        print("")
        print(f"[json] {args.out}")

    rows.sort(key=lambda r: r["base"], reverse=True)
    print(f"\n[ranking] {len(rows)} detectores continuos, activo base sobre {args.start[:4]}")
    print(f"  {'detector':<26} {'base':>9} {'EUR':>10} {'ops':>5} {'abierta':>9}")
    for r in rows[: args.top]:
        print(f"  {r['label']:<26} {r['base']:>+8.1f}% {r['eur']:>+9.1f}% {r['ops']:>5} {r['open']:>8.1f}%")
    print(f"  {'...':<26}")
    for r in rows[-3:]:
        print(f"  {r['label']:<26} {r['base']:>+8.1f}% {r['eur']:>+9.1f}% {r['ops']:>5} {r['open']:>8.1f}%")

    pos = [r for r in rows if r["base"] > 0.0]
    print(f"\n  por encima de mantener: {len(pos)}/{len(rows)}   sin puerta: {ref['base']:+.1f} %")
    print(
        "  Una sola ventana elige por RUIDO: esta cifra no descarta ni valida a nadie, y la"
        " mediana por familia menos aun, porque produccion ejecuta UNA variante."
    )
    print("  La prueba que decide es cruzar POR VARIANTE entre ventanas: --out y gate_families_cross.py.")

    print("\n[superficie de `alcista`] activo base (%), la familia del hallazgo previo")
    print("    m / k " + "".join(f"{k:>9}" for k in LOOKS))
    by_name = {r["label"]: r for r in rows}
    for m in MOVES:
        line = f"    {m:.2f} "
        for k in LOOKS:
            line += f"{by_name[f'alcista m={m:.2f} k={k}']['base']:>9.1f}"
        print(line)

    print("\n[lectura] el liston es 0.0 %. La superficie decide si `alcista` es un mecanismo (meseta:")
    print("          los vecinos comparten signo y magnitud) o un parametro ajustado (pico aislado).")
    print("          Un ranking sobre un solo año elige por ruido: leer la forma, no la cabeza.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
