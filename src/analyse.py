#!/usr/bin/env python3
import csv, statistics, sys, collections

def load(f):
    with open(f) as fh: return [(float(r[0]),int(r[1])) for r in list(csv.reader(fh))[1:]]

def roll(v, w=30):
    b, out = collections.deque(maxlen=w), []
    for x in v:
        b.append(x)
        if len(b) == w: out.append(statistics.pstdev(b))
    return out

for f in sys.argv[1:]:
    d = load(f); v = [x[1] for x in d]
    r = sorted(roll(v))
    p = lambda q: r[int(len(r)*q)]
    print(f"{f:20} mean={statistics.mean(v):7.2f}  "
          f"rolling sd: p10={p(.10):.2f} median={p(.50):.2f} p90={p(.90):.2f} max={r[-1]:.2f}")
