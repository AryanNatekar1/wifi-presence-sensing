#!/usr/bin/env python3
"""Generate the study figures from the raw traces.

    pip install matplotlib
    python3 src/figures.py

Writes into figures/:
    fig1_distributions.png   rolling-sigma distributions, empty vs moving
    fig2_timeseries.png      the clean baseline and one moving run, over time
    fig3_confounds.png       every trial pair, ratio against mean gap

Figure 3 is the one worth looking at. It plots each trial's separation ratio
against the mean-power gap between its two conditions, which makes the point
the study is really about: the trials with large mean gaps are the ones that
produced misleading ratios.
"""
import csv
import collections
import os
import statistics
import sys

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    sys.exit("matplotlib required:  pip install matplotlib")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "figures")

WIN = 30
THRESHOLD = 1.4

INK = "#1b2431"
EMPTY_C = "#64748b"
MOVE_C = "#16a34a"
BAD_C = "#dc2626"


def load(name):
    with open(os.path.join(DATA, name)) as fh:
        return [(float(r[0]), int(r[1])) for r in list(csv.reader(fh))[1:]]


def rolling(values, w=WIN):
    buf, out = collections.deque(maxlen=w), []
    for x in values:
        buf.append(x)
        if len(buf) == w:
            out.append(statistics.pstdev(buf))
    return out


def sigmas(name):
    return rolling([v for _, v in load(name)])


def mean_dbm(name):
    return statistics.mean([v for _, v in load(name)])


def fig1():
    fig, ax = plt.subplots(figsize=(8, 4.2))
    e = sigmas("rssi_door_open_empty.csv")
    m3 = sigmas("rssi_m3.csv")
    m4 = sigmas("rssi_m4.csv")

    bins = [i * 0.25 for i in range(0, 40)]
    ax.hist(e, bins=bins, color=EMPTY_C, alpha=0.75,
            label="Empty room (n=%d)" % len(e))
    ax.hist(m3 + m4, bins=bins, color=MOVE_C, alpha=0.65,
            label="Walking (n=%d)" % (len(m3) + len(m4)))
    ax.axvline(THRESHOLD, color=INK, ls="--", lw=1.4)
    ax.text(THRESHOLD + 0.1, ax.get_ylim()[1] * 0.92,
            "threshold %.1f dBm" % THRESHOLD, fontsize=9, color=INK)

    ax.set_xlabel("Rolling standard deviation over 3.4 s (dBm)")
    ax.set_ylabel("Windows")
    ax.set_title("Distributions do not overlap: empty max %.2f < walking p10 %.2f"
                 % (max(e), sorted(m3 + m4)[int(len(m3 + m4) * 0.1)]),
                 fontsize=10)
    ax.legend(frameon=False, fontsize=9)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "fig1_distributions.png"), dpi=150)
    plt.close(fig)


def fig2():
    fig, axes = plt.subplots(2, 1, figsize=(8, 5), sharex=True)
    for ax, name, colour, label in (
            (axes[0], "rssi_door_open_empty.csv", EMPTY_C, "Empty room"),
            (axes[1], "rssi_m4.csv", MOVE_C, "Walking the path")):
        rows = load(name)
        s = rolling([v for _, v in rows])
        t = [rows[i + WIN - 1][0] for i in range(len(s))]
        ax.plot(t, s, color=colour, lw=1.2)
        ax.axhline(THRESHOLD, color=INK, ls="--", lw=1.1)
        ax.set_ylim(0, 9)
        ax.set_ylabel("rolling sigma")
        ax.set_title("%s  -  mean %.2f dBm" % (label, mean_dbm(name)),
                     fontsize=10, loc="left")
        ax.spines[["top", "right"]].set_visible(False)
    axes[1].set_xlabel("Time (s)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "fig2_timeseries.png"), dpi=150)
    plt.close(fig)


def fig3():
    pairs = [
        ("geometry A (void)", "rssi_empty.csv", "rssi_moving.csv"),
        ("tethered", "rssi_e2.csv", "rssi_m2.csv"),
        ("door confound 1", "rssi_e3.csv", "rssi_m3.csv"),
        ("door confound 2", "rssi_e4.csv", "rssi_m4.csv"),
        ("controlled (m3)", "rssi_door_open_empty.csv", "rssi_m3.csv"),
        ("controlled (m4)", "rssi_door_open_empty.csv", "rssi_m4.csv"),
    ]
    fig, ax = plt.subplots(figsize=(8, 4.6))
    for label, en, mn in pairs:
        es, ms = sigmas(en), sigmas(mn)
        emed = sorted(es)[len(es) // 2]
        mmed = sorted(ms)[len(ms) // 2]
        ratio = mmed / emed
        gap = abs(mean_dbm(mn) - mean_dbm(en))
        valid = gap < 2.0
        ax.scatter(gap, ratio, s=90, zorder=3,
                   color=MOVE_C if valid else BAD_C,
                   edgecolor="white", linewidth=1.2)
        ax.annotate("%s\n%.2fx" % (label, ratio), (gap, ratio),
                    textcoords="offset points", xytext=(9, -4), fontsize=8,
                    color=INK)

    ax.axhline(2.0, color=INK, ls="--", lw=1.2)
    ax.text(0.2, 2.08, "pre-registered threshold 2.0x", fontsize=8.5, color=INK)
    ax.axvspan(2.0, 17, color=BAD_C, alpha=0.06)
    ax.text(8.5, 0.4, "mean gap > 2 dB: geometry or environment changed,\n"
                      "so the comparison is not valid",
            fontsize=8.5, color=BAD_C, ha="center")

    ax.set_xlabel("Mean received power gap between conditions (dB)")
    ax.set_ylabel("Separation ratio (moving median / empty median)")
    ax.set_title("Every misleading result sits in the shaded region", fontsize=10)
    ax.set_xlim(-0.6, 17)
    ax.set_ylim(0, 7)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "fig3_confounds.png"), dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    fig1()
    fig2()
    fig3()
    print("wrote figures to %s" % OUT)
