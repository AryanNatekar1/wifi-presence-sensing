#!/usr/bin/env python3
"""Pi RF Mapper - multi-AP RSSI fingerprinting for coarse zone localisation.

Uses every visible access point as an illuminator. Each zone is described by
a vector of signal strengths across all observed BSSIDs; live position is
classified by k-nearest-neighbour against captured fingerprints.

Honest scope: this localises to a trained zone. It does not produce a
floorplan, pose, or vital signs.
"""
import subprocess, re, time, json, threading, math, statistics, collections
from flask import Flask, request

IFACE = "wlan0"
ABSENT = -100          # assumed dBm for an AP not seen in a scan
K = 3                  # kNN neighbours

app = Flask(__name__)
LOCK = threading.Lock()

DB = {
    "zones": {},       # name -> list of {bssid: dbm} samples
    "live": {},        # latest scan
    "guess": None,
    "conf": 0.0,
    "aps": 0,
    "scans": 0,
    "log": [],
    "busy": False,
    "started": time.strftime("%Y-%m-%d %H:%M:%S"),
}


def scan():
    """Return {bssid: dbm} for every AP currently visible."""
    try:
        out = subprocess.run(
            ["iw", "dev", IFACE, "scan"],
            capture_output=True, text=True, timeout=25).stdout
    except Exception:
        return {}
    seen, bssid = {}, None
    for line in out.splitlines():
        line = line.strip()
        m = re.match(r"BSS ([0-9a-f:]{17})", line)
        if m:
            bssid = m.group(1)
            continue
        m = re.match(r"signal:\s*(-?[\d.]+)", line)
        if m and bssid:
            seen[bssid] = float(m.group(1))
    return seen


def vector(sample, keys):
    return [sample.get(k, ABSENT) for k in keys]


def distance(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def classify(live):
    """kNN against stored zone fingerprints. Returns (zone, confidence)."""
    with LOCK:
        zones = {z: list(v) for z, v in DB["zones"].items()}
    if not zones or not live:
        return None, 0.0

    keys = set(live)
    for samples in zones.values():
        for s in samples:
            keys |= set(s)
    keys = sorted(keys)

    lv = vector(live, keys)
    scored = []
    for name, samples in zones.items():
        for s in samples:
            scored.append((distance(lv, vector(s, keys)), name))
    if not scored:
        return None, 0.0
    scored.sort()
    top = scored[:K]

    votes = collections.Counter(n for _, n in top)
    best, count = votes.most_common(1)[0]

    # confidence: vote share, tempered by how close the best match actually is
    share = count / len(top)
    d_best = min(d for d, n in top if n == best)
    closeness = 1.0 / (1.0 + d_best / 20.0)
    return best, round(share * closeness, 3)


def worker():
    while True:
        with LOCK:
            if DB["busy"]:
                time.sleep(0.4)
                continue
        s = scan()
        if s:
            g, c = classify(s)
            with LOCK:
                DB["live"] = s
                DB["aps"] = len(s)
                DB["scans"] += 1
                prev = DB["guess"]
                DB["guess"], DB["conf"] = g, c
                if g and g != prev:
                    DB["log"].insert(0, {"t": time.strftime("%H:%M:%S"),
                                         "z": g, "c": c})
                    DB["log"] = DB["log"][:8]
        time.sleep(1.0)


@app.route("/api/state")
def state():
    with LOCK:
        return json.dumps({
            "guess": DB["guess"], "conf": DB["conf"], "aps": DB["aps"],
            "scans": DB["scans"], "log": DB["log"], "busy": DB["busy"],
            "zones": {z: len(v) for z, v in DB["zones"].items()},
            "top": sorted(DB["live"].items(), key=lambda kv: -kv[1])[:8],
        })


@app.route("/api/capture", methods=["POST"])
def capture():
    name = (request.json or {}).get("zone", "").strip()[:24]
    n = int((request.json or {}).get("n", 5))
    if not name:
        return json.dumps({"ok": False, "err": "zone name required"})
    with LOCK:
        DB["busy"] = True
    got = []
    for _ in range(max(1, min(n, 12))):
        s = scan()
        if s:
            got.append(s)
        time.sleep(0.3)
    with LOCK:
        DB["zones"].setdefault(name, []).extend(got)
        DB["busy"] = False
        total = len(DB["zones"][name])
    return json.dumps({"ok": True, "captured": len(got), "total": total})


@app.route("/api/clear", methods=["POST"])
def clear():
    name = (request.json or {}).get("zone", "")
    with LOCK:
        if name in DB["zones"]:
            del DB["zones"][name]
    return json.dumps({"ok": True})


@app.route("/api/export")
def export():
    with LOCK:
        payload = {
            "meta": {
                "device": "Raspberry Pi 3B / BCM43430",
                "interface": IFACE,
                "method": "multi-AP RSSI fingerprinting, kNN k=%d" % K,
                "absent_dbm": ABSENT,
                "session_started": DB["started"],
                "exported": time.strftime("%Y-%m-%d %H:%M:%S"),
                "total_scans": DB["scans"],
            },
            "zones": {z: v for z, v in DB["zones"].items()},
            "transitions": DB["log"],
        }
    return (json.dumps(payload, indent=2), 200, {
        "Content-Type": "application/json",
        "Content-Disposition": 'attachment; filename="rf_fingerprints.json"'})


@app.route("/api/export.csv")
def export_csv():
    with LOCK:
        zones = {z: list(v) for z, v in DB["zones"].items()}
    keys = sorted({b for smp in zones.values() for s in smp for b in s})
    rows = ["zone,sample," + ",".join(keys)]
    for z, samples in zones.items():
        for i, s in enumerate(samples):
            rows.append("%s,%d," % (z, i) +
                        ",".join(str(s.get(k, ABSENT)) for k in keys))
    return ("\n".join(rows), 200, {
        "Content-Type": "text/csv",
        "Content-Disposition": 'attachment; filename="rf_fingerprints.csv"'})


@app.route("/")
def home():
    return PAGE


PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pi RF Mapper</title><style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
background:#04060b;color:#d8e3ef;padding:22px;min-height:100vh}
h1{font-size:1.3rem;font-weight:600}h1 b{color:#3b82f6}
.sub{font-size:.58rem;color:#4e5f73;letter-spacing:.22em;margin:5px 0 18px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;max-width:1100px}
.card{background:rgba(8,12,20,.8);border:1px solid #161f2c;border-radius:11px;
padding:16px}
.t{font-size:.56rem;color:#4e5f73;letter-spacing:.22em;margin-bottom:12px}
.kv{display:flex;justify-content:space-between;padding:6px 0;font-size:.72rem}
.kv+.kv{border-top:1px solid #101822}
.k{color:#6f8095}.v{color:#60a5fa;font-variant-numeric:tabular-nums}
#zone{font-size:2rem;font-weight:700;color:#4ade80;text-align:center;
padding:14px 0}
#zone.none{color:#46525f;font-size:1.1rem}
.cb{height:6px;background:#101822;border-radius:3px;overflow:hidden;margin-top:8px}
.cf{height:100%;background:#4ade80;transition:width .4s}
input,button{font-family:inherit;font-size:.75rem;border-radius:7px;
border:1px solid #22303f;background:#0b1320;color:#d8e3ef;padding:9px 12px}
button{cursor:pointer;background:#132a44;border-color:#27405c}
button:hover{background:#1b3a5c}
button:disabled{opacity:.4;cursor:wait}
.row{display:flex;gap:8px;margin-bottom:10px}
.row input{flex:1}
.zl{display:flex;justify-content:space-between;align-items:center;
font-size:.72rem;padding:7px 0;border-top:1px solid #101822}
.zl b{color:#4ade80;font-weight:500}
.x{color:#7f1d1d;cursor:pointer;padding:0 6px}
.x:hover{color:#ef4444}
.ap{display:flex;justify-content:space-between;font-size:.64rem;
color:#6f8095;padding:3px 0}
.ap span:last-child{color:#60a5fa}
.le{display:flex;justify-content:space-between;font-size:.64rem;
color:#6f8095;padding:3px 0}
.note{font-size:.6rem;color:#46525f;line-height:1.7;margin-top:12px}
.warn{background:rgba(69,26,3,.5);border:1px solid #7c2d12;color:#fdba74;
font-size:.62rem;padding:10px 12px;border-radius:8px;line-height:1.6;
margin-top:12px}
</style></head><body>
<h1><b>&pi;</b> Pi RF Mapper</h1>
<div class="sub">MULTI-AP RSSI FINGERPRINTING &middot; kNN ZONE LOCALISATION</div>
<div class="grid">

<div class="card">
<div class="t">LIVE POSITION ESTIMATE</div>
<div id="zone" class="none">no zones trained</div>
<div class="cb"><div class="cf" id="cf" style="width:0"></div></div>
<div class="kv"><span class="k">Confidence</span><span class="v" id="cv">&mdash;</span></div>
<div class="kv"><span class="k">APs visible</span><span class="v" id="aps">&mdash;</span></div>
<div class="kv"><span class="k">Scans</span><span class="v" id="sc">&mdash;</span></div>
<div class="t" style="margin:16px 0 8px">ZONE TRANSITIONS</div>
<div id="log"></div>
</div>

<div class="card">
<div class="t">TRAIN A ZONE</div>
<div class="row">
<input id="zn" placeholder="e.g. desk, doorway, bed" maxlength="24">
<button id="cap" onclick="capture()">Capture</button>
</div>
<div class="note" style="margin:0 0 14px">Stand in the zone, then capture.
Five scans, roughly 15&ndash;20 s. Capture each zone two or three times from
slightly different spots for a stronger fingerprint.</div>
<div class="t">TRAINED ZONES</div>
<div id="zones"></div>
<div class="t" style="margin:16px 0 8px">EXPORT</div>
<div class="row">
<button onclick="location='/api/export'">JSON</button>
<button onclick="location='/api/export.csv'">CSV</button>
</div>
</div>

<div class="card">
<div class="t">STRONGEST ACCESS POINTS &mdash; LIVE</div>
<div id="ap"></div>
<div class="note">Every router in range acts as an illuminator. The fingerprint
is the vector of signal strengths across all of them &mdash; which is why more
visible APs gives better separation than a single link.</div>
</div>

<div class="card">
<div class="t">SCOPE</div>
<div class="kv"><span class="k">Zone localisation</span>
<span class="v" style="color:#4ade80">yes</span></div>
<div class="kv"><span class="k">Movement detection</span>
<span class="v" style="color:#4ade80">yes</span></div>
<div class="kv"><span class="k">Floorplan geometry</span>
<span class="v" style="color:#46525f">no</span></div>
<div class="kv"><span class="k">Pose keypoints</span>
<span class="v" style="color:#46525f">no</span></div>
<div class="kv"><span class="k">Vital signs</span>
<span class="v" style="color:#46525f">no</span></div>
<div class="note">This classifies which trained zone the device is in. It does
not measure distance, draw a floorplan, or infer body position &mdash; those
require CSI, multiple antennas, or known AP coordinates.</div>
<div class="warn">Scanning briefly retunes the radio. SSH and the dashboard may
stutter for a second during each scan. This is expected.</div>
</div>

</div>
<script>
async function poll(){
 try{
  var d=await(await fetch('/api/state')).json();
  var z=document.getElementById('zone');
  if(d.guess){z.textContent=d.guess;z.className='';}
  else{z.textContent=Object.keys(d.zones).length?'locating…':'no zones trained';
       z.className='none';}
  document.getElementById('cf').style.width=(d.conf*100)+'%';
  document.getElementById('cv').textContent=d.conf?d.conf.toFixed(3):'—';
  document.getElementById('aps').textContent=d.aps;
  document.getElementById('sc').textContent=d.scans;
  document.getElementById('cap').disabled=d.busy;
  var h='';
  for(var i=0;i<d.top.length;i++)
   h+='<div class="ap"><span>'+d.top[i][0]+'</span><span>'+
      d.top[i][1]+' dBm</span></div>';
  document.getElementById('ap').innerHTML=h||'<div class="ap">scanning…</div>';
  var zh='';
  for(var k in d.zones)
   zh+='<div class="zl"><span>'+k+' <b>'+d.zones[k]+
       ' samples</b></span><span class="x" onclick="del(\''+k+
       '\')">&times;</span></div>';
  document.getElementById('zones').innerHTML=zh||
   '<div class="note" style="margin:0">none yet</div>';
  var lh='';
  for(var j=0;j<d.log.length;j++)
   lh+='<div class="le"><span>'+d.log[j].t+'</span><span>'+
       d.log[j].z+' &middot; '+d.log[j].c+'</span></div>';
  document.getElementById('log').innerHTML=lh||
   '<div class="le">no transitions yet</div>';
 }catch(e){}
}
async function capture(){
 var n=document.getElementById('zn').value.trim();
 if(!n){alert('Name the zone first');return}
 document.getElementById('cap').disabled=true;
 await fetch('/api/capture',{method:'POST',
  headers:{'Content-Type':'application/json'},
  body:JSON.stringify({zone:n,n:5})});
 document.getElementById('zn').value='';
 poll();
}
async function del(z){
 await fetch('/api/clear',{method:'POST',
  headers:{'Content-Type':'application/json'},
  body:JSON.stringify({zone:z})});
 poll();
}
setInterval(poll,1200);poll();
</script></body></html>"""

if __name__ == "__main__":
    threading.Thread(target=worker, daemon=True).start()
    app.run(host="0.0.0.0", port=5004)
