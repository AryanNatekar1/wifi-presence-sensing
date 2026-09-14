# Raw traces

CSV files with two columns: `t` (seconds from trial start) and `dbm` (RSSI).

Copy them off the Pi with:

    scp pi@<pi-host>:~/rssi_*.csv data/

Reproduce every figure in the study with:

    python3 ../src/analyse.py rssi_*.csv

## Trials in this set

| File | Condition | Notes |
|---|---|---|
| `rssi_empty.csv` | empty, geometry A | **void** — devices moved between runs |
| `rssi_moving.csv` | moving, geometry A | **void** — 9.75 dB mean gap |
| `rssi_e2.csv` | empty, tethered | valid geometry, but Pi ~1 m from AP |
| `rssi_m2.csv` | moving, tethered | 1.12× — no separation |
| `rssi_e3.csv` | empty, door closed | door confound, 15.34 dB gap |
| `rssi_m3.csv` | moving, door open | 2.17× — not trustworthy |
| `rssi_e4.csv` | empty, door closed | door confound, 7.37 dB gap |
| `rssi_m4.csv` | moving, door open | 1.56× — not trustworthy |
| `rssi_e5.csv` | empty, 180 s | unexplained: matched mean, high variance |
| `rssi_door_open_empty.csv` | empty, door open | **the clean baseline** — median σ 0.51 |

The void and confounded trials are kept on purpose. They are the evidence for
the methodological argument in the study, not clutter.
