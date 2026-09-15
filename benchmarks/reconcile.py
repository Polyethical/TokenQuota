"""Accuracy check: compare tokens tokenquota recorded with your provider's usage export.

1. Log committed usage with the on_event hook, e.g.

       import csv, datetime
       writer = csv.writer(open("ledger.csv", "a", newline=""))
       def log_event(e):
           if e["event"] == "committed":
               day = datetime.datetime.utcfromtimestamp(e["ts"]).date().isoformat()
               writer.writerow([day, e["model"], e["input_tokens"] or 0, e["output_tokens"] or 0])
       Quota(..., on_event=log_event)

2. Download a usage CSV from your provider's console for the same days.

3. Run:
       python reconcile.py ledger.csv provider.csv \\
           --date-col date --model-col model --input-col input_tokens --output-col output_tokens

The ledger file has no header (day, model, input, output). Column names for
the provider file differ by provider, so pass them as flags.
Exit code is 1 if any day/model differs by more than --tolerance (default 2%).
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict


def load_ledger(path):
    totals = defaultdict(int)
    with open(path, newline="") as f:
        for day, model, inp, out in csv.reader(f):
            totals[(day, model)] += int(inp) + int(out)
    return totals


def load_provider(path, a):
    totals = defaultdict(int)
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            day = row[a.date_col][:10]
            totals[(day, row[a.model_col])] += int(float(row[a.input_col] or 0)) + int(float(row[a.output_col] or 0))
    return totals


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("ledger")
    p.add_argument("provider")
    p.add_argument("--date-col", default="date")
    p.add_argument("--model-col", default="model")
    p.add_argument("--input-col", default="input_tokens")
    p.add_argument("--output-col", default="output_tokens")
    p.add_argument("--tolerance", type=float, default=0.02)
    a = p.parse_args(argv)
    ours, theirs = load_ledger(a.ledger), load_provider(a.provider, a)
    bad = 0
    print("| day | model | tokenquota | provider | diff |\n|---|---|---|---|---|")
    for key in sorted(set(ours) | set(theirs)):
        o, t = ours.get(key, 0), theirs.get(key, 0)
        diff = (o - t) / t if t else (0.0 if o == 0 else 1.0)
        flag = "" if abs(diff) <= a.tolerance else " **"
        bad += bool(flag)
        print(f"| {key[0]} | {key[1]} | {o:,} | {t:,} | {diff:+.2%}{flag} |")
    print(f"\n{'OK' if not bad else f'{bad} rows outside ±{a.tolerance:.0%}'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
