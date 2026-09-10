"""¿Cuanto cuesta la recompra forzada al cerrarse la puerta?

Read-only y temporal. Lee los CSV OHLCVT de Kraken directamente, sin base de datos.

La puerta estricta solo alcista admite un mercado que cae al -96 % anualizado. Con deriva tan
negativa, vender y recomprar deberia tener esperanza BRUTA positiva -- se recompra mas barato --
y sin embargo el barrido dio residuos de -10 a -14 puntos POR DEBAJO de la factura de
comisiones. La hipotesis es la recompra forzada.

El mecanismo, en `trading/engine.py`: mientras la puerta esta cerrada el bot debe estar dentro
del activo. Si esta en euros cuando la vela queda enmascarada, `if forced and side == "buy"`
compra a mercado en esa misma vela. Y una puerta solo alcista cierra exactamente cuando el
precio SE RECUPERA (deja de estar un `pct` por debajo de su maximo de `n` dias), asi que la
recompra forzada cae sistematicamente en el rebote: justo el peor momento.

La venta no tiene este problema -- `if forced: continue` en la linea 421 la difiere -- asi que
la UNICA operacion que puede caer en una vela enmascarada es la recompra forzada, y eso la hace
identificable sin tocar el motor: `op.idx in mask and op.side == "buy"`.

Dos medidas, y la segunda es la que responde:

  REPARTO       Cada ciclo (venta -> recompra) vale `(S/B) * (1-f)^2` en activo base. Se
                separan los ciclos que cierran con recompra FORZADA de los que cierran con una
                voluntaria y se compone cada grupo. Si el grupo forzado concentra la perdida,
                la hipotesis vive.
  ALTERNATIVA   Para cada recompra forzada, a que precio se habria recomprado esperando a que
                la puerta REABRIERA. El cociente `B_alt / B_forzada` mayor que 1 significa que
                forzar salio bien; menor que 1, que costo dinero. Es una descomposicion de
                primer orden, NO un contrafactual completo: cambiar una recompra desplaza todas
                las piernas siguientes. Si el efecto sale grande, hace falta un interruptor en
                el motor y una comparacion controlada, como con `reset_on_unmask`.

Uso (PYTHONPATH=. obligatorio; sin variables de entorno de BD):
  PYTHONPATH=. python scripts/analysis/forced_rebuy_cost.py "C:/Dev/Kraken OHLCVT"
"""

import argparse
import dataclasses
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import execution_fidelity as ef
import gate_config_sweep as gcs

from core.config import ATR_DESV_LIMIT, RECALIBRATION_BARS
from trading.engine import EngineConfig, simulate_operations

CONFIGS = ((0.0, 0.5), (0.0, 0.9), (0.01, 0.9), (0.02, 0.9))


def cycles(ops: list, mask: frozenset[int], fee: float) -> tuple[list, list]:
    """Pares (venta, recompra) separados por si la recompra fue forzada por la mascara."""
    forced, free = [], []
    for i in range(len(ops) - 1):
        if ops[i].side != "sell" or ops[i + 1].side != "buy":
            continue
        sell, buy = ops[i], ops[i + 1]
        factor = (sell.price / buy.price) * (1.0 - fee) ** 2
        (forced if buy.idx in mask else free).append((sell, buy, factor))
    return forced, free


def compound(rows: list) -> float:
    out = 1.0
    for _, _, f in rows:
        out *= f
    return 100.0 * (out - 1.0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir")
    ap.add_argument("--pair", default="XBTEUR")
    ap.add_argument("--gates", nargs="*", default=["alcista_estricta", "alcista_amplia", "simetrica"])
    ap.add_argument("--years", nargs="*", type=int, default=[2023, 2024, 2025])
    ap.add_argument("--lead-months", type=int, default=6)
    ap.add_argument("--recalib-bars", type=int, default=RECALIBRATION_BARS)
    ap.add_argument("--fee", type=float, default=ef.FEE)
    args = ap.parse_args()

    ef.FEE = args.fee
    fee = args.fee / 100.0
    print("[hipotesis] la recompra forzada al cerrarse la puerta cae en el rebote y se come la deriva")
    print(f"  comision {args.fee} %/pierna   configs {CONFIGS}")

    for year in args.years:
        fine, points, s = gcs.build(args.data_dir, args.pair, year, args.lead_months, args.recalib_bars)
        price = fine["close"].to_numpy(dtype=float)
        times = fine["time"].to_numpy()
        scheduled = ef.remap(points, times)

        for gate in args.gates:
            is_open = gcs.GATES[gate](s)
            mask = frozenset(np.flatnonzero(~is_open).tolist())
            # Para cada vela, el indice de la siguiente vela ABIERTA (donde se recompraria si no
            # se forzase). -1 si la puerta ya no vuelve a abrir en el ano.
            nxt = np.full(len(price), -1)
            following = -1
            for i in range(len(price) - 1, -1, -1):
                nxt[i] = following
                if is_open[i]:
                    following = i

            print(f"\n================ {year}  puerta '{gate}' ================")
            print(f"  puerta abierta {100 * is_open.mean():.1f} %")
            print(
                f"  {'config':<16}{'ciclos':>8}{'forzados':>10}{'comp. forzados':>17}"
                f"{'comp. libres':>15}{'B_alt/B_forz':>15}"
            )
            for mm, stop in CONFIGS:
                cfg = EngineConfig(
                    pair="XBTEUR",
                    calibration=ef._calibration(scheduled[0][1], stop),
                    k_act=None,
                    min_margin=mm,
                    atr_desv_limit=ATR_DESV_LIMIT,
                    calibration_schedule=tuple((at, ef._calibration(p, stop)) for at, p in scheduled),
                )
                cfg = dataclasses.replace(cfg, force_hold_bars=mask, reset_on_unmask=True)
                ops = simulate_operations(fine, cfg, fee_rate=fee)
                forced, free = cycles(ops, mask, fee)
                if not forced and not free:
                    print(f"  mm={mm:.3f} s={stop}      {0:>8}{0:>10}{'-':>17}{'-':>15}{'-':>15}")
                    continue
                ratio = 1.0
                for _, buy, _ in forced:
                    j = nxt[buy.idx]
                    if j >= 0:
                        ratio *= price[j] / buy.price
                print(
                    f"  mm={mm:.3f} s={stop}      {len(forced) + len(free):>8}{len(forced):>10}"
                    f"{compound(forced):>16.1f}%{compound(free):>14.1f}%{100 * (ratio - 1):>14.1f}%"
                )
        del fine, s

    print("\n[lectura] 'comp. forzados' y 'comp. libres' reparten el resultado del bot entre los ciclos")
    print("          que cerro la mascara y los que cerro el propio trailing. 'B_alt/B_forz' por encima")
    print("          de 0 % significa que esperar a la reapertura habria comprado MAS CARO, es decir que")
    print("          forzar salio bien; por debajo, que la recompra forzada costo dinero.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
