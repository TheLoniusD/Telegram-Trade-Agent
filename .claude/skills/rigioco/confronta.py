"""
Confronto segnale per segnale tra due strategie del rigioco.

    python3 .claude/skills/rigioco/confronta.py FILE_DETTAGLIO "attuale" "BE +1.5$" [--quanti 10]

FILE_DETTAGLIO è l'uscita di `prezzi.py rigioco ... --dettaglio` (sul branch server:
`git show origin/server:logs/rigioco_tick_dettaglio.txt > /tmp/det.txt`). Le due
strategie si indicano con la sigla della riga di intestazione del dettaglio
(es. "attuale", "4 ticket", "no prot", "BE +1.5$"). Stampa in quanti segnali la
seconda è meglio o peggio della prima, di quanto, i casi più grandi e i giorni.
"""
import argparse
import re
from collections import Counter, defaultdict

LABEL_START, CELL = 38, 9


def parse(path):
    labels, rows, day = None, [], None
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        m = re.match(r"── (\d{4}-\d\d-\d\d)", line)
        if m:
            day = m.group(1)
            continue
        m = re.match(r"   (\d\d:\d\d) (BUY |SELL) (.{24}) (.*)$", line)
        if m and day:
            cells = [None if c.strip() in ("—", "") else float(c) for c in m.group(4).split()]
            rows.append({"day": day, "time": m.group(1), "dir": m.group(2).strip(), "label": m.group(3).strip(), "cells": cells})
        elif line.startswith(" " * LABEL_START) and labels is None and line.strip():
            body = line[LABEL_START:]
            labels = [body[i:i + 8].strip() for i in range(0, len(body), CELL) if body[i:i + 8].strip()]
    if not labels:
        raise SystemExit("Riga delle sigle non trovata: serve l'uscita con --dettaglio")
    return labels, rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    parser.add_argument("base")
    parser.add_argument("variante")
    parser.add_argument("--quanti", type=int, default=10)
    args = parser.parse_args()
    labels, rows = parse(args.file)
    for name in (args.base, args.variante):
        if name not in labels:
            raise SystemExit(f"Sigla '{name}' non trovata. Sigle disponibili: {labels}")
    a, b = labels.index(args.base), labels.index(args.variante)
    diffs, by_day = [], defaultdict(float)
    for r in rows:
        if len(r["cells"]) != len(labels) or r["cells"][a] is None or r["cells"][b] is None:
            continue
        d = round(r["cells"][b] - r["cells"][a], 1)
        diffs.append((r, d))
        by_day[r["day"]] += d
    better = [x for x in diffs if x[1] > 0.05]
    worse = [x for x in diffs if x[1] < -0.05]
    total = sum(d for _, d in diffs)
    print(f"'{args.variante}' rispetto a '{args.base}' su {len(diffs)} segnali: {total:+.1f} $")
    print(f"  meglio in {len(better)} ({sum(d for _, d in better):+.1f} $), peggio in {len(worse)} "
          f"({sum(d for _, d in worse):+.1f} $), uguale in {len(diffs) - len(better) - len(worse)}")
    print("  differenze più frequenti:", Counter(d for _, d in diffs if abs(d) > 0.05).most_common(6))
    days_better = sum(1 for v in by_day.values() if v > 0.05)
    days_worse = sum(1 for v in by_day.values() if v < -0.05)
    print(f"  giorni: meglio in {days_better}, peggio in {days_worse} su {len(by_day)}")
    print("  per giorno:", "  ".join(f"{k[8:]}/{k[5:7]} {v:+.0f}" for k, v in sorted(by_day.items())))
    for title, items in (("peggioramenti", sorted(worse, key=lambda x: x[1])),
                         ("miglioramenti", sorted(better, key=lambda x: -x[1]))):
        print(f"\n{title} più grandi:")
        for r, d in items[:args.quanti]:
            print(f"  {r['day']} {r['time']} {r['dir']:4} {r['label'][:24]:24} {r['cells'][a]:8.1f} → {r['cells'][b]:8.1f}  ({d:+.1f})")


if __name__ == "__main__":
    main()
