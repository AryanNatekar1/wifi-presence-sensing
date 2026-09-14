# Detecting Human Presence with Wi-Fi Signal Strength on a Raspberry Pi 3B

### Or: how three experimental confounds nearly hid a real effect

**Aryan Natekar** · September 2026

---

## Abstract

Wi-Fi sensing infers human presence from perturbation of radio signals. Most
published work uses Channel State Information (CSI), which requires specialised
hardware or patched firmware. This study asks whether the far more accessible
Received Signal Strength Indicator (RSSI) — available on any Linux Wi-Fi
interface — is sufficient to detect a person walking between two commodity
devices.

Across ten trials (~5,460 samples) on a Raspberry Pi 3B, the initial answer
appeared to be no. Three separate experimental artifacts — USB undervoltage,
insufficient device separation, and inconsistent door state — each independently
suppressed or imitated the effect. After all three were eliminated, short-window
RSSI variance separated cleanly: **median rolling σ of 0.51 dBm for an empty
room versus 2.91 and 3.10 for continuous human movement, a ratio of 5.7–6.1×
against a pre-registered threshold of 2.0×, with fully non-overlapping
distributions.**

The methodological finding is the more useful one: at three separate points, a
confound produced numbers that could have been reported as either a positive or
a negative result. Matching the mean received power between conditions was the
diagnostic that exposed each one.

---

## 1. Motivation

Camera-based monitoring is effective but invasive. For the case that motivated
this work — remote monitoring of elderly people living alone, a common situation
in regions with high youth outmigration — invasiveness is often disqualifying. A
sensor that detects presence while recording no image and no audio is a
categorically different proposition for the person being monitored.

The question here is narrow and practical: does it work on hardware a student
already owns, with no firmware modification?

---

## 2. Method

### 2.1 Apparatus

| Component | Specification |
|---|---|
| Sensing device | Raspberry Pi 3B, BCM43430 Wi-Fi (single chip antenna) |
| OS | Debian 13 (trixie), aarch64, kernel 6.18.34-rpt-rpi-v8 |
| Python | 3.13.5 |
| Access point | Laptop-hosted hotspot, 2412 MHz (channel 1) |
| Power | Independent 5 V supply (see §3.1) |

### 2.2 Sampling

RSSI was obtained by polling `iw dev wlan0 link` and parsing the `signal:`
field.

A critical detail: the driver refreshes RSSI only on frame reception. On an idle
link the same stale value is returned for seconds, producing artificially low
variance. A background `ping` at 20 Hz against the gateway ran for the duration
of every trial to guarantee fresh samples. This is why the logger requires root.

Target rate 10 Hz; achieved **8.85–8.92 Hz** across all trials, the shortfall
due to subprocess spawn overhead. Human gait is a sub-5 Hz phenomenon, so this
remains above Nyquist for the target signal. The achieved rate is reported here
rather than the nominal one.

### 2.3 Metric

Global standard deviation across a whole trial is not a valid detection metric —
it is dominated by slow drift in the mean over tens of seconds. A practical
detector compares recent seconds against a quiet baseline.

The metric is therefore **rolling standard deviation over a 30-sample (~3.4 s)
sliding window**, summarised by its distribution (p10 / median / p90 / max).

### 2.4 Pre-registered decision rule

Fixed before the controlled trials and **not modified afterwards**:

> Moving-condition median rolling σ ≥ **2.0×** empty-condition median → viable.
> < **1.5×** → not viable.

---

## 3. The three confounds

This section is the substance of the study. Each confound was detected by the
same diagnostic: **comparing mean received power between conditions.** A human
body crossing a path changes the mean by a small amount. Differences of 7–15 dB
indicate that something structural changed, and that the comparison is invalid.

### 3.1 Confound 1 — USB undervoltage and device proximity

The Pi was initially powered from the laptop's USB port. Two consequences:

1. A Pi 3B draws up to 2.5 A at 5 V. A USB-A port supplies 0.5 A (0.9 A on
   USB 3.0). The radio is the first subsystem to suffer under voltage sag.
2. USB power tethers the Pi to within a cable length of the laptop — which was
   *also* the access point. Transmitter and receiver sat roughly one metre
   apart, with no meaningful path for a body to interrupt.

Under these conditions (mean −44.96 vs −44.65 dBm, geometry verified stable):

| Condition | n | Mean (dBm) | Rolling σ p10 / med / p90 / max |
|---|---|---|---|
| Empty | 400 | −44.96 | 0.99 / **1.94** / 4.16 / 5.96 |
| Moving | 399 | −44.65 | 0.82 / **2.17** / 4.97 / 6.00 |

Ratio **1.12×** — a clear failure by the pre-registered rule.

This result was internally consistent and looked like a genuine hardware
limitation. It was not. The geometry made detection impossible regardless of
hardware capability. **A well-controlled experiment measuring the wrong thing
still produces clean, wrong numbers.**

After switching to an independent supply, `vcgencmd get_throttled` returned
`0x0`, confirming no undervoltage, and the Pi could be placed several metres
from the AP (link dropped to −58 dBm, roughly 20× less received power —
consistent with real room-scale separation).

### 3.2 Confound 2 — unmatched device geometry

An early trial pair produced means of −59.40 (empty) and −49.65 (moving): a
9.75 dB gap, roughly threefold in linear power. No human body produces that. The
devices had been repositioned between runs, confounding the condition variable
with a geometry variable. The pair was discarded.

### 3.3 Confound 3 — door state

Two trials after the power fix appeared promising:

| Trial | Empty mean | Moving mean | Empty med σ | Moving med σ | Ratio | Mean gap |
|---|---|---|---|---|---|---|
| 3 | −72.16 | −56.82 | 1.43 | 3.10 | 2.17× | **15.34 dB** |
| 4 | −63.68 | −56.31 | 1.86 | 2.91 | 1.56× | **7.37 dB** |

Trial 3 cleared the 2× threshold. It would have been tempting to report it.

But the mean gaps were far too large, and the cause was procedural: the
experimenter left the room and **closed the door** for empty runs, while moving
runs were necessarily conducted with the door open. Every comparison was
door-closed-empty against door-open-moving.

A direct test — empty room, door held **open** — gave a mean of −55.60 dBm,
within 1.2 dB of both moving runs. The door accounted for 8–16 dB.

**The door was a stronger signal than the human.**

---

## 4. Results after confound elimination

With power independent, devices separated, and door state held constant, all
three conditions sit at matched received power:

| Condition | n | Mean (dBm) | Rolling σ p10 / med / p90 / max |
|---|---|---|---|
| Empty (door open) | 400 | −55.60 | 0.47 / **0.51** / 0.76 / 1.02 |
| Moving (trial 3) | 399 | −56.82 | 1.99 / **3.10** / 5.42 / 8.10 |
| Moving (trial 4) | 401 | −56.31 | 1.79 / **2.91** / 4.93 / 7.87 |

Means matched within **1.2 dB**. Ratios: **6.1×** and **5.7×**.

The distributions do not overlap. The empty condition's **maximum** window
(1.02) falls below the moving conditions' **p10** (1.79 and 1.99). Every
3.4-second window in all three trials would be classified correctly by a single
fixed threshold at approximately 1.4 dBm.

This is the result. It passes the pre-registered rule by a factor of three.

---

## 5. Limitations

- **Replication outstanding.** The clean empty baseline is a single 45-second
  run. Two independent moving runs support it, but the empty condition requires
  repetition under identical conditions before the result is secure. *(Pending.)*
- One long trial looked anomalous and was not. A 180-second empty capture          returned mean −57.11 with global σ 3.463. Its rolling median is 0.85, close to   the clean baseline: the high global figure is slow drift across three minutes,   not short-window noise. This is precisely why the study uses a rolling window    rather than global σ.
- Single subject, single room, single AP, single channel.
- Continuous walking only. Detecting a stationary or seated person is a harder
  problem and was not attempted.
- No blinding; experimenter and subject were the same person.
- One feature (rolling σ) tested. Frequency-domain analysis in the gait band was
  not explored.

Scope of the claim: **rolling-variance analysis of RSSI on a BCM43430
distinguishes continuous human movement from an empty room under matched
conditions.** It is not a claim about through-wall sensing, stationary
occupancy, multi-person counting, or vital signs, none of which were tested.

---

## 6. Conclusion

Presence detection from RSSI alone is achievable on a Raspberry Pi 3B with no
additional sensors and no firmware modification. The effect is large — roughly
6× separation in short-window variance — and would support a simple threshold
classifier.

The harder lesson is methodological. This study produced, in sequence: a clean
negative (1.12×), an apparent pass (2.17×), an ambiguous middle (1.56×), and a
decisive pass (6.1×) — all measuring nominally the same thing. The differences
were power delivery, device separation, and a door. In each case the diagnostic
that exposed the artifact was checking whether mean received power matched
across conditions, a check that costs nothing and was the only thing standing
between a confound and a published claim.

**Further work.** RSSI collapses the channel to one scalar per sample. CSI
exposes per-subcarrier amplitude and phase, enabling stationary-occupancy
detection and respiration sensing. An ESP32-S3 or ESP32-C6 (~₹600–900) provides
CSI directly and is the natural next step. The MM-Fi dataset offers annotated
CSI for offline model development ahead of any hardware purchase.

---

## Appendix — reproducibility

Ten raw CSV traces (`t`, `dbm`), ~5,460 samples, including the void and
confounded trials, which are retained deliberately.

Collection script `src/rssi_log.py` and analysis script `src/analyse.py`. Both
depend only on the Python standard library and the `iw` utility.
