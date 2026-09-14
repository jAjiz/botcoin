"""Cruza los detectores continuos entre ventanas: ¿las que ganan aquí ganan también allí?

Read-only y temporal. Solo lee los JSON que produce `gate_families_live.py --out`.

La mediana de una familia NO decide nada: producción ejecuta UNA variante, elegida a propósito,
y una familia con mediana -14 % puede contener la que funciona. Descartar por mediana esconde
exactamente lo que se está buscando. Lo que decide es la **consistencia entre datos**: tomar las
variantes concretas que baten a mantener en una ventana y comprobar si son las MISMAS que lo
hacen en las otras.

Y esa cuenta hay que leerla contra su tasa base. Si en cada ventana gana una fracción `p` de las
variantes y las ventanas fuesen tiradas independientes, `p^w` de ellas ganarían en las `w`
ventanas por puro azar. Ese numero se imprime al lado del observado: "positiva en 3 de 3" solo
significa algo si supera claramente lo que el azar produce con 154 candidatas.

Uso:
  PYTHONPATH=. python scripts/analysis/gate_families_cross.py a.json b.json c.json
"""

import argparse
import json
import statistics


def load(paths: list[str]) -> tuple[list[str], dict[str, dict[str, float]], dict[str, float], dict[str, float]]:
    per_window: dict[str, dict[str, float]] = {}
    ungated: dict[str, float] = {}
    order: list[str] = []
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            blob = json.load(fh)
        label = blob["window"][:4]
        order.append(label)
        ungated[label] = blob["ungated"]
        per_window[label] = {r["label"]: r["base"] for r in blob["rows"]}
    names = set(per_window[order[0]])
    for label in order[1:]:
        names &= set(per_window[label])
    scores = {n: {w: per_window[w][n] for w in order} for n in sorted(names)}
    return order, scores, ungated, {w: len(per_window[w]) for w in order}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json", nargs="+", help="Salidas de gate_families_live.py --out, una por ventana.")
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()

    windows, scores, ungated, _sizes = load(args.json)
    n = len(scores)
    print(f"[ventanas] {', '.join(windows)}   {n} variantes presentes en todas")
    print("  sin puerta: " + "   ".join(f"{w} {ungated[w]:+.1f} %" for w in windows))

    rate = {w: sum(1 for v in scores.values() if v[w] > 0.0) / n for w in windows}
    print("\n  ganan a mantener por ventana: " + "   ".join(f"{w} {rate[w] * 100:.0f} %" for w in windows))
    chance = 1.0
    for w in windows:
        chance *= rate[w]
    print(f"  tasa base si las ventanas fuesen independientes: {chance * 100:.1f} % -> {chance * n:.1f} de {n}")

    wins = {name: sum(1 for w in windows if v[w] > 0.0) for name, v in scores.items()}
    allwin = [name for name, k in wins.items() if k == len(windows)]
    print(f"  OBSERVADO positivas en {len(windows)}/{len(windows)}: {len(allwin)} de {n}")

    print("\n[ranking] por ventanas ganadas, desempate por el PEOR año (lo que se puede prometer)")
    header = f"  {'detector':<26}" + "".join(f"{w:>10}" for w in windows) + f"{'gana':>6}{'peor':>9}"
    print(header)
    ranked = sorted(scores.items(), key=lambda kv: (wins[kv[0]], min(kv[1].values())), reverse=True)
    for name, v in ranked[: args.top]:
        worst = min(v.values())
        print(
            f"  {name:<26}"
            + "".join(f"{v[w]:>+9.1f}%" for w in windows)
            + f"{wins[name]:>5}/{len(windows)}{worst:>+8.1f}%"
        )

    print("\n[por familia] cuantas de sus variantes ganan en TODAS las ventanas")
    fams = ("alcista", "impulso", "bajo max", "sin max", "bajo ema", "caja", "er", "rotura")
    print(f"  {'familia':<10}{'n':>4}{'en todas':>10}{'mediana del peor año':>24}")
    for fam in fams:
        group = [(name, v) for name, v in scores.items() if name.startswith(fam)]
        if not group:
            continue
        every = sum(1 for name, _ in group if wins[name] == len(windows))
        worst_med = statistics.median(min(v.values()) for _, v in group)
        print(f"  {fam:<10}{len(group):>4}{every:>10}{worst_med:>+23.1f}%")

    print("\n[lectura] la columna que importa es 'peor': es lo que una variante puede prometer sin")
    print("          elegir el año. Comparar SIEMPRE el observado en n/n contra la tasa base de arriba;")
    print("          si son parecidos, la coincidencia es azar y no hay nada que elegir.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
