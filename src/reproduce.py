#!/usr/bin/env python3
"""Recompute every number in the study from the raw traces and verify them.

    python3 src/reproduce.py

Reads the CSVs in data/, recomputes the statistics quoted in docs/STUDY.md and
README.md, and checks them against the recorded values in src/expected.json.
Exits non-zero if any figure has drifted.

This is the point of publishing the raw traces: the claims are checkable in one
command rather than taken on trust. It runs on every push (see
.github/workflows/verify.yml), so a change that breaks a published number fails
visibly instead of quietly.

Standard library only - no dependencies.
"""
import csv
import json
import os
import statistics
import sys
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
EXPECTED = os.path.join(HERE, "expected.json")

WIN = 30           # rolling window, samples (~3.4 s at 8.9 Hz)
THRESHOLD = 1.4    # detection threshold, dBm

TOL_MEAN = 0.05    # dBm
TOL_SIGMA = 0.02   # dBm


def load(path):
    with open(path) as fh:
        return [(float(r[0]), int(r[1])) for r in list(csv.reader(fh))[1:]]


def rolling(values, w=WIN):
    buf, out = collections.deque(maxlen=w), []
    for x in values:
        buf.append(x)
        if len(buf) == w:
            out.append(statistics.pstdev(buf))
    return out


def stats(path):
    rows = load(path)
    vals = [v for _, v in rows]
    r = sorted(rolling(vals))
    pct = lambda q: r[int(len(r) * q)]
    duration = rows[-1][0] - rows[0][0] if len(rows) > 1 else 0.0
    return {
        "n": len(vals),
        "rate_hz": round(len(vals) / duration, 2) if duration else 0.0,
        "mean": round(statistics.mean(vals), 2),
        "p10": round(pct(0.10), 2),
        "median": round(pct(0.50), 2),
        "p90": round(pct(0.90), 2),
        "max": round(r[-1], 2),
    }


def main():
    if not os.path.isdir(DATA):
        sys.exit("data/ not found - run from the repository root")

    with open(EXPECTED) as fh:
        expected = json.load(fh)

    print("Recomputing from raw traces in data/\n")
    header = ("%-28s %6s %7s %9s %6s %7s %6s %6s"
              % ("trace", "n", "Hz", "mean", "p10", "median", "p90", "max"))
    print(header)
    print("-" * len(header))

    results, failures = {}, []

    for name in sorted(expected["traces"]):
        path = os.path.join(DATA, name)
        if not os.path.exists(path):
            failures.append("%s is missing from data/" % name)
            continue
        s = stats(path)
        results[name] = s
        print("%-28s %6d %7.2f %9.2f %6.2f %7.2f %6.2f %6.2f"
              % (name, s["n"], s["rate_hz"], s["mean"],
                 s["p10"], s["median"], s["p90"], s["max"]))

        exp = expected["traces"][name]
        if exp.get("mean") is not None and abs(s["mean"] - exp["mean"]) > TOL_MEAN:
            failures.append("%s mean %.2f, expected %.2f"
                            % (name, s["mean"], exp["mean"]))
        if (exp.get("median") is not None
                and abs(s["median"] - exp["median"]) > TOL_SIGMA):
            failures.append("%s median sigma %.2f, expected %.2f"
                            % (name, s["median"], exp["median"]))

    if not results:
        sys.exit("\nno traces found in data/")

    print("\nHeadline comparison (matched means, door held open):\n")
    base = results.get("rssi_door_open_empty.csv")
    if base:
        for mv in ("rssi_m3.csv", "rssi_m4.csv"):
            if mv not in results:
                continue
            m = results[mv]
            ratio = m["median"] / base["median"]
            gap = abs(m["mean"] - base["mean"])
            overlap = "yes" if base["max"] >= m["p10"] else "no"
            print("  %-15s median %.2f vs %.2f  ->  %.1fx   "
                  "mean gap %.2f dB   distributions overlap: %s"
                  % (mv.replace("rssi_", "").replace(".csv", ""),
                     m["median"], base["median"], ratio, gap, overlap))
            if ratio < expected["min_ratio"]:
                failures.append("%s ratio %.2f below pre-registered %.1f"
                                % (mv, ratio, expected["min_ratio"]))
            if base["max"] >= m["p10"]:
                failures.append("%s overlaps the empty-room distribution" % mv)

        mid = (base["max"] + min(results[m]["p10"]
                                 for m in ("rssi_m3.csv", "rssi_m4.csv")
                                 if m in results)) / 2
        print("\n  Empty-room max %.2f < moving p10 %.2f, so a single threshold "
              "separates every window." % (base["max"], mid * 2 - base["max"]))
        print("  Threshold used in the dashboards: %.1f dBm" % THRESHOLD)

    print()
    if failures:
        print("FAILED - %d discrepancy(ies):" % len(failures))
        for f in failures:
            print("  - %s" % f)
        sys.exit(1)

    print("OK - every published figure reproduced from the raw data.")


if __name__ == "__main__":
    main()
