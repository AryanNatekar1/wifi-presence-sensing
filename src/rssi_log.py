#!/usr/bin/env python3
import subprocess, time, re, csv, sys, statistics, collections

IFACE, HZ, WIN = "wlan0", 10, 30

def gateway():
    out = subprocess.run(["ip","route"], capture_output=True, text=True).stdout
    m = re.search(r"default via (\S+)", out)
    return m.group(1) if m else None

def rssi():
    out = subprocess.run(["iw","dev",IFACE,"link"], capture_output=True, text=True).stdout
    m = re.search(r"signal:\s*(-?\d+)", out)
    return int(m.group(1)) if m else None

label = sys.argv[1] if len(sys.argv) > 1 else "test"
dur   = float(sys.argv[2]) if len(sys.argv) > 2 else 60

gw = gateway()
if not gw: sys.exit("no default gateway — is wlan0 connected?")
ping = subprocess.Popen(["ping","-i","0.05","-q",gw],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)

buf, rows, t0 = collections.deque(maxlen=WIN), [], time.time()
try:
    while time.time() - t0 < dur:
        v = rssi()
        if v is not None:
            t = time.time() - t0
            buf.append(v); rows.append((round(t,3), v))
            if len(buf) >= 5:
                sd = statistics.pstdev(buf)
                print(f"\r{t:6.1f}s  {v:4d} dBm  sd={sd:5.2f} "
                      f"{'#'*min(int(sd*10),50):<50}", end="")
        time.sleep(1/HZ)
finally:
    ping.terminate()
    fn = f"rssi_{label}.csv"
    with open(fn,"w",newline="") as f:
        w = csv.writer(f); w.writerow(["t","dbm"]); w.writerows(rows)
    v = [r[1] for r in rows]
    print(f"\n\n{label}: n={len(v)} mean={statistics.mean(v):.2f} "
          f"sd={statistics.pstdev(v):.3f} range={min(v)}..{max(v)} -> {fn}")
