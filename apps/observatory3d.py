#!/usr/bin/env python3
"""Pi Sensing Observatory - final build.

Combines:
  * movement detection from short-window RSSI variance
  * multi-AP fingerprinting with kNN zone classification
  * classical MDS projection of RF fingerprint space into 3D
  * orbitable live 3D view, exportable datasets

Honest scope: the 3D view is a map of RF SIMILARITY SPACE, not a surveyed
floorplan. Zones that look close on screen have similar radio signatures.
That is a real and useful embedding, but it is not room geometry, and it is
not LiDAR.
"""
import subprocess, re, time, json, threading, math, statistics, collections
from flask import Flask, request

try:
    import numpy as np
except Exception:
    np = None

IFACE = "wlan0"
ABSENT = -100.0
K = 3
HZ = 10
WIN = 30
THRESH = 1.4

app = Flask(__name__)
LOCK = threading.Lock()
T0 = time.time()
SCANNING = threading.Event()

D = {
    "rssi": None, "sigma": 0.0, "motion": 0.0, "present": False,
    "hist": [], "n": 0, "events": 0, "peak": 0.0, "up": 0,
    "zones": {}, "coords": {}, "live_xyz": None,
    "guess": None, "conf": 0.0, "aps": [], "scans": 0,
    "log": [], "busy": False, "scanning": False,
    "started": time.strftime("%Y-%m-%d %H:%M:%S"),
}


# ---------------------------------------------------------------- radio io

def gateway():
    out = subprocess.run(["ip", "route"], capture_output=True, text=True).stdout
    m = re.search(r"default via (\S+)", out)
    return m.group(1) if m else None


def read_rssi():
    out = subprocess.run(["iw", "dev", IFACE, "link"],
                         capture_output=True, text=True).stdout
    m = re.search(r"signal:\s*(-?\d+)", out)
    return int(m.group(1)) if m else None


def scan():
    try:
        out = subprocess.run(["iw", "dev", IFACE, "scan"],
                             capture_output=True, text=True, timeout=25).stdout
    except Exception:
        return {}
    seen, bss, ssid = {}, None, ""
    for line in out.splitlines():
        s = line.strip()
        m = re.match(r"BSS ([0-9a-f:]{17})", s)
        if m:
            bss, ssid = m.group(1), ""
            continue
        m = re.match(r"SSID:\s*(.*)", s)
        if m and bss:
            ssid = m.group(1)[:18]
        m = re.match(r"signal:\s*(-?[\d.]+)", s)
        if m and bss:
            seen[bss] = (float(m.group(1)), ssid)
    return seen


# ------------------------------------------------------------- fingerprints

def vec(sample, keys):
    return [sample.get(k, ABSENT) for k in keys]


def dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def all_keys(zones, live=None):
    ks = set(live or {})
    for smp in zones.values():
        for s in smp:
            ks |= set(s)
    return sorted(ks)


def centroids(zones, keys):
    out = {}
    for name, smp in zones.items():
        if not smp:
            continue
        cols = list(zip(*[vec(s, keys) for s in smp]))
        out[name] = [sum(c) / len(c) for c in cols]
    return out


def mds(cents):
    """Classical MDS of zone centroids into 3D, normalised to [-1, 1]."""
    names = sorted(cents)
    n = len(names)
    if n == 0:
        return {}
    if n == 1:
        return {names[0]: [0.0, 0.0, 0.0]}
    if np is None or n == 2:
        return {nm: [(-1.0 + 2.0 * i / max(1, n - 1)), 0.0, 0.0]
                for i, nm in enumerate(names)}
    M = np.array([cents[nm] for nm in names], dtype=float)
    sq = ((M[:, None, :] - M[None, :, :]) ** 2).sum(-1)
    J = np.eye(n) - np.ones((n, n)) / n
    B = -0.5 * J @ sq @ J
    w, v = np.linalg.eigh(B)
    idx = np.argsort(w)[::-1][:3]
    w = np.clip(w[idx], 0, None)
    C = v[:, idx] * np.sqrt(w)
    if C.shape[1] < 3:
        C = np.pad(C, ((0, 0), (0, 3 - C.shape[1])))
    mx = np.abs(C).max() or 1.0
    C = C / mx
    return {nm: [round(float(x), 4) for x in C[i]] for i, nm in enumerate(names)}


def locate(live, cents, keys, coords):
    """kNN label plus an inverse-distance position in the MDS embedding."""
    if not cents or not live:
        return None, 0.0, None
    lv = vec(live, keys)
    ds = sorted((dist(lv, c), nm) for nm, c in cents.items())
    best_d, best = ds[0]
    if len(ds) > 1:
        margin = ds[1][0] / (best_d + 1e-6)
        conf = min(1.0, (margin - 1.0) * 1.6) * (1.0 / (1.0 + best_d / 25.0))
    else:
        conf = 1.0 / (1.0 + best_d / 25.0)
    tot = [0.0, 0.0, 0.0]
    wsum = 0.0
    for d, nm in ds[:K]:
        if nm not in coords:
            continue
        w = 1.0 / (d * d + 1e-3)
        for i in range(3):
            tot[i] += coords[nm][i] * w
        wsum += w
    xyz = [round(t / wsum, 4) for t in tot] if wsum else None
    return best, round(max(0.0, conf), 3), xyz


# ------------------------------------------------------------------ workers

def fast_sampler():
    gw = gateway()
    if gw:
        subprocess.Popen(["ping", "-i", "0.05", "-q", gw],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    buf = collections.deque(maxlen=WIN)
    was = False
    while True:
        if SCANNING.is_set():
            buf.clear()
            with LOCK:
                D["scanning"] = True
            time.sleep(0.3)
            continue
        with LOCK:
            D["scanning"] = False
        v = read_rssi()
        if v is not None:
            buf.append(v)
            if len(buf) >= 10:
                sd = statistics.pstdev(buf)
                now = sd > THRESH
                with LOCK:
                    D["rssi"] = v
                    D["sigma"] = round(sd, 2)
                    D["motion"] = round(min(sd / 6.0, 1.0), 3)
                    D["present"] = now
                    D["n"] += 1
                    D["peak"] = round(max(D["peak"], sd), 2)
                    D["up"] = int(time.time() - T0)
                    D["hist"].append(round(sd, 2))
                    D["hist"] = D["hist"][-160:]
                    if now and not was:
                        D["events"] += 1
                was = now
        time.sleep(1.0 / HZ)


def rebuild():
    """Recompute centroids and the MDS embedding. Call with LOCK held."""
    zones = {z: list(s) for z, s in D["zones"].items()}
    keys = all_keys(zones)
    cents = centroids(zones, keys)
    D["coords"] = mds(cents)
    return keys, cents


def scan_worker():
    while True:
        with LOCK:
            if D["busy"]:
                pass
        SCANNING.set()
        found = scan()
        SCANNING.clear()
        if found:
            live = {b: v[0] for b, v in found.items()}
            with LOCK:
                zones = {z: list(s) for z, s in D["zones"].items()}
                coords = dict(D["coords"])
            keys = all_keys(zones, live)
            cents = centroids(zones, keys)
            g, c, xyz = locate(live, cents, keys, coords)
            with LOCK:
                D["scans"] += 1
                D["aps"] = sorted(
                    ([b, found[b][1] or b[-8:], found[b][0]] for b in found),
                    key=lambda r: -r[2])[:10]
                prev = D["guess"]
                D["guess"], D["conf"], D["live_xyz"] = g, c, xyz
                if g and g != prev:
                    D["log"].insert(0, {"t": time.strftime("%H:%M:%S"),
                                        "z": g, "c": c})
                    D["log"] = D["log"][:8]
        time.sleep(1.2)


# --------------------------------------------------------------------- api

@app.route("/api/state")
def state():
    with LOCK:
        return json.dumps({
            "rssi": D["rssi"], "sigma": D["sigma"], "motion": D["motion"],
            "present": D["present"], "hist": D["hist"], "n": D["n"],
            "events": D["events"], "peak": D["peak"], "up": D["up"],
            "guess": D["guess"], "conf": D["conf"], "scans": D["scans"],
            "aps": D["aps"], "log": D["log"], "busy": D["busy"],
            "scanning": D["scanning"], "coords": D["coords"],
            "live_xyz": D["live_xyz"],
            "zones": {z: len(s) for z, s in D["zones"].items()},
        })


@app.route("/api/capture", methods=["POST"])
def capture():
    body = request.json or {}
    name = str(body.get("zone", "")).strip()[:24]
    if not name:
        return json.dumps({"ok": False})
    with LOCK:
        D["busy"] = True
    got = []
    for _ in range(5):
        SCANNING.set()
        s = scan()
        SCANNING.clear()
        if s:
            got.append({b: v[0] for b, v in s.items()})
        time.sleep(0.3)
    with LOCK:
        D["zones"].setdefault(name, []).extend(got)
        rebuild()
        D["busy"] = False
    return json.dumps({"ok": True, "captured": len(got)})


@app.route("/api/clear", methods=["POST"])
def clear():
    name = str((request.json or {}).get("zone", ""))
    with LOCK:
        D["zones"].pop(name, None)
        rebuild()
    return json.dumps({"ok": True})


@app.route("/api/export")
def export():
    with LOCK:
        p = {
            "meta": {
                "device": "Raspberry Pi 3B / BCM43430 (single antenna)",
                "method": "multi-AP RSSI fingerprinting; kNN k=%d; "
                          "classical MDS embedding" % K,
                "movement_metric": "rolling sigma, %d-sample window "
                                   "(~%.1f s) at ~%d Hz" % (WIN, WIN / 8.9, HZ),
                "threshold_dbm": THRESH,
                "absent_dbm": ABSENT,
                "scope": "movement detection and trained-zone localisation "
                         "only; no floorplan geometry, pose or vital signs",
                "session_started": D["started"],
                "exported": time.strftime("%Y-%m-%d %H:%M:%S"),
                "samples": D["n"], "scans": D["scans"],
                "detection_events": D["events"], "peak_sigma": D["peak"],
            },
            "zones": D["zones"],
            "embedding": D["coords"],
            "transitions": D["log"],
            "sigma_history": D["hist"],
        }
    return (json.dumps(p, indent=2), 200,
            {"Content-Type": "application/json",
             "Content-Disposition": 'attachment; filename="rf_session.json"'})


@app.route("/api/export.csv")
def export_csv():
    with LOCK:
        zones = {z: list(s) for z, s in D["zones"].items()}
    keys = all_keys(zones)
    rows = ["zone,sample," + ",".join(keys)]
    for z, smp in zones.items():
        for i, s in enumerate(smp):
            rows.append("%s,%d," % (z, i) +
                        ",".join(str(s.get(k, ABSENT)) for k in keys))
    return ("\n".join(rows), 200,
            {"Content-Type": "text/csv",
             "Content-Disposition": 'attachment; filename="fingerprints.csv"'})


@app.route("/")
def home():
    return PAGE


PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pi Sensing Observatory</title><style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
background:#04060b;color:#d8e3ef;overflow:hidden;height:100vh}
#view{position:fixed;inset:0;width:100%;height:100%;cursor:grab}
#view.drag{cursor:grabbing}
.ui{position:fixed;z-index:5}
#hdr{top:18px;left:22px;pointer-events:none}
#hdr h1{font-size:1.3rem;font-weight:600}#hdr h1 b{color:#3b82f6}
#hdr p{font-size:.55rem;color:#4e5f73;letter-spacing:.22em;margin-top:4px}
#live{top:20px;right:22px;display:flex;align-items:center;gap:8px;
background:rgba(10,26,16,.82);border:1px solid #15803d;border-radius:20px;
padding:6px 14px;font-size:.6rem;letter-spacing:.15em;color:#4ade80}
#live.sc{background:rgba(41,28,5,.82);border-color:#a16207;color:#fbbf24}
#dot{width:7px;height:7px;border-radius:50%;background:currentColor;
animation:bl 1.6s infinite}@keyframes bl{0%,100%{opacity:1}50%{opacity:.25}}
.card{background:rgba(8,12,20,.82);border:1px solid #161f2c;border-radius:11px;
padding:14px 15px;backdrop-filter:blur(10px)}
.t{font-size:.54rem;color:#4e5f73;letter-spacing:.22em;margin-bottom:10px}
.kv{display:flex;justify-content:space-between;padding:5px 0;font-size:.7rem}
.kv+.kv{border-top:1px solid #101822}
.k{color:#6f8095}.v{color:#60a5fa;font-variant-numeric:tabular-nums}
.v.g{color:#4ade80}.na{color:#333f4d;font-size:.67rem}
#left{top:82px;left:22px;width:216px}
#right{top:82px;right:22px;width:226px}
#zc{margin-top:11px;padding:13px 10px;border-radius:9px;text-align:center;
border:1px solid #222d3b;transition:.4s}
#zc.on{background:rgba(21,128,61,.15);border-color:#16a34a}
#word{font-size:1.25rem;font-weight:700;letter-spacing:.08em;color:#46525f}
#zc.on #word{color:#4ade80}
#sub{font-size:.58rem;color:#4e5f73;margin-top:5px}
#zone{font-size:1.5rem;font-weight:700;color:#60a5fa;text-align:center;
padding:8px 0}#zone.none{color:#46525f;font-size:.9rem}
.cb{height:5px;background:#101822;border-radius:3px;overflow:hidden}
.cf{height:100%;background:#60a5fa;transition:width .5s}
input,button{font-family:inherit;font-size:.7rem;border-radius:7px;
border:1px solid #27405c;background:#0b1320;color:#d8e3ef;padding:8px 10px}
button{cursor:pointer;background:#132a44}
button:hover{background:#1b3a5c}button:disabled{opacity:.4;cursor:wait}
.row{display:flex;gap:7px;margin-bottom:9px}.row input{flex:1;min-width:0}
.zl{display:flex;justify-content:space-between;font-size:.66rem;
padding:5px 0;border-top:1px solid #101822;color:#8fa2b8}
.zl b{color:#4ade80;font-weight:500}
.x{color:#7f1d1d;cursor:pointer;padding:0 5px}.x:hover{color:#ef4444}
.le,.ap{display:flex;justify-content:space-between;font-size:.62rem;
color:#6f8095;padding:2px 0}
.ap span:last-child{color:#60a5fa}
#bottom{bottom:18px;left:22px;right:22px;height:88px;pointer-events:none}
#bars{width:100%;height:58px;display:block}
#cap{font-size:.56rem;color:#3f4c5b;margin-top:6px;line-height:1.6}
#tools{bottom:120px;left:22px;width:216px}
#hint{bottom:120px;right:22px;font-size:.58rem;color:#3f4c5b;text-align:right;
line-height:1.7;pointer-events:none}
</style></head><body>
<canvas id="view"></canvas>

<div class="ui" id="hdr"><h1><b>&pi;</b> Pi Observatory</h1>
<p>RSSI SENSING &middot; RF-SPACE EMBEDDING</p></div>
<div class="ui" id="live"><span id="dot"></span><span id="lt">LIVE</span></div>

<div class="ui card" id="left">
<div class="t">MEASURED &mdash; LIVE</div>
<div class="kv"><span class="k">RSSI</span><span class="v" id="rs">&mdash;</span></div>
<div class="kv"><span class="k">Rolling &sigma;</span><span class="v" id="sg">&mdash;</span></div>
<div class="kv"><span class="k">Motion</span><span class="v" id="mo">&mdash;</span></div>
<div class="kv"><span class="k">Peak &sigma;</span><span class="v" id="pk">&mdash;</span></div>
<div class="kv"><span class="k">Events</span><span class="v" id="ev">&mdash;</span></div>
<div class="kv"><span class="k">Samples</span><span class="v" id="nn">&mdash;</span></div>
<div class="kv"><span class="k">Scans</span><span class="v" id="scn">&mdash;</span></div>
<div class="kv"><span class="k">Uptime</span><span class="v" id="up">&mdash;</span></div>
<div id="zc"><div id="word">STANDBY</div><div id="sub">initialising</div></div>
</div>

<div class="ui card" id="right">
<div class="t">ZONE ESTIMATE &mdash; kNN</div>
<div id="zone" class="none">no zones trained</div>
<div class="cb"><div class="cf" id="cf" style="width:0"></div></div>
<div class="kv" style="margin-top:6px"><span class="k">Confidence</span>
<span class="v" id="cv">&mdash;</span></div>
<div class="t" style="margin:14px 0 7px">NOT MEASURED &mdash; NEEDS CSI</div>
<div class="kv"><span class="k">Heart rate</span><span class="na">n/a</span></div>
<div class="kv"><span class="k">Respiration</span><span class="na">n/a</span></div>
<div class="kv"><span class="k">Pose keypoints</span><span class="na">n/a</span></div>
<div class="kv"><span class="k">Floorplan geometry</span><span class="na">n/a</span></div>
<div class="t" style="margin:14px 0 7px">ACCESS POINTS</div>
<div id="ap"></div>
</div>

<div class="ui card" id="tools">
<div class="t">TRAIN ZONE</div>
<div class="row"><input id="zn" placeholder="desk / door / bed" maxlength="24">
<button id="cb" onclick="cap()">Capture</button></div>
<div id="zones"></div>
<div class="row" style="margin-top:10px">
<button onclick="location='/api/export'">JSON</button>
<button onclick="location='/api/export.csv'">CSV</button>
<button onclick="reset()">Reset view</button></div>
</div>

<div class="ui" id="hint">drag to orbit &middot; scroll to zoom<br>
axes are RF-similarity space, not metres</div>

<div class="ui" id="bottom"><canvas id="bars"></canvas>
<div id="cap">&sigma; threshold 1.4 dBm &middot; 3.4 s window &middot; ~8.9 Hz
&middot; BCM43430 single antenna &middot; no camera, no microphone<br>
Movement detection and trained-zone localisation only. Node positions are a
classical-MDS embedding of fingerprint similarity &mdash; not surveyed room
geometry.</div></div>

<script>
var vc=document.getElementById('view'),g=vc.getContext('2d');
var bc=document.getElementById('bars'),b=bc.getContext('2d');
var S={sigma:0,present:false,hist:[],coords:{},live_xyz:null,aps:[],guess:null};
var disp=0,ry=0.6,rx=0.42,zoom=1,tick=0;
var drag=false,lx=0,ly=0;

vc.addEventListener('mousedown',function(e){drag=true;lx=e.clientX;ly=e.clientY;
 vc.classList.add('drag')});
window.addEventListener('mouseup',function(){drag=false;vc.classList.remove('drag')});
window.addEventListener('mousemove',function(e){if(!drag)return;
 ry+=(e.clientX-lx)*0.008;rx+=(e.clientY-ly)*0.006;
 rx=Math.max(-0.2,Math.min(1.35,rx));lx=e.clientX;ly=e.clientY});
vc.addEventListener('wheel',function(e){e.preventDefault();
 zoom*=e.deltaY>0?0.93:1.07;zoom=Math.max(0.45,Math.min(2.6,zoom))},
 {passive:false});
vc.addEventListener('touchstart',function(e){if(e.touches.length==1){drag=true;
 lx=e.touches[0].clientX;ly=e.touches[0].clientY}});
vc.addEventListener('touchmove',function(e){if(!drag||e.touches.length!=1)return;
 ry+=(e.touches[0].clientX-lx)*0.01;rx+=(e.touches[0].clientY-ly)*0.008;
 rx=Math.max(-0.2,Math.min(1.35,rx));lx=e.touches[0].clientX;
 ly=e.touches[0].clientY;e.preventDefault()},{passive:false});
vc.addEventListener('touchend',function(){drag=false});
function reset(){ry=0.6;rx=0.42;zoom=1}

function proj(p,cx,cy,sc){
 var x=p[0],y=p[1],z=p[2];
 var c=Math.cos(ry),s=Math.sin(ry);
 var x1=x*c-z*s, z1=x*s+z*c;
 var c2=Math.cos(rx),s2=Math.sin(rx);
 var y1=y*c2-z1*s2, z2=y*s2+z1*c2;
 var d=1/(1+z2*0.30);
 return [cx+x1*sc*d, cy+y1*sc*d, d, z2];
}

function draw(){
 var W=vc.width=vc.clientWidth*2,H=vc.height=vc.clientHeight*2;
 g.clearRect(0,0,W,H);
 var cx=W/2,cy=H/2+H*0.02,sc=Math.min(W,H)*0.30*zoom;
 disp+=(S.sigma-disp)*0.12;
 var e=Math.min(disp/4,1.3);
 var col=S.present?[74,222,128]:[59,130,246];
 var rgb=col[0]+','+col[1]+','+col[2];

 // floor grid in the embedding plane
 var N=12;
 for(var i=0;i<=N;i++){
  var u=-1+2*i/N;
  g.beginPath();
  for(var j=0;j<=N;j++){
   var v=-1+2*j/N;
   var w=Math.sin(u*3+v*3+tick*0.03)*e*0.05;
   var P=proj([u*1.25,w,v*1.25],cx,cy,sc);
   j?g.lineTo(P[0],P[1]):g.moveTo(P[0],P[1]);
  }
  g.strokeStyle='rgba('+rgb+',0.08)';g.lineWidth=1.2;g.stroke();
  g.beginPath();
  for(var j2=0;j2<=N;j2++){
   var v2=-1+2*j2/N;
   var w2=Math.sin(v2*3+u*3+tick*0.03)*e*0.05;
   var P2=proj([v2*1.25,w2,u*1.25],cx,cy,sc);
   j2?g.lineTo(P2[0],P2[1]):g.moveTo(P2[0],P2[1]);
  }
  g.strokeStyle='rgba('+rgb+',0.08)';g.stroke();
 }

 // access points on an outer ring, radius from signal strength
 var aps=S.aps||[];
 for(var a=0;a<aps.length;a++){
  var ang=a/Math.max(1,aps.length)*Math.PI*2;
  var str=Math.max(0,Math.min(1,(aps[a][2]+95)/55));
  var r=1.95-str*0.55;
  var P3=proj([Math.cos(ang)*r,-0.45-str*0.35,Math.sin(ang)*r],cx,cy,sc);
  g.beginPath();g.arc(P3[0],P3[1],3.0*P3[2],0,6.283);
  g.fillStyle='rgba(148,163,184,'+(0.25+str*0.5)+')';g.fill();
  var Pc=proj([0,0,0],cx,cy,sc);
  g.beginPath();g.moveTo(P3[0],P3[1]);g.lineTo(Pc[0],Pc[1]);
  g.strokeStyle='rgba(148,163,184,'+(0.03+str*0.07)+')';g.lineWidth=1;g.stroke();
  g.font=(9*P3[2]*2)+'px ui-monospace,monospace';
  g.fillStyle='rgba(148,163,184,0.5)';g.textAlign='center';
  g.fillText(aps[a][1],P3[0],P3[1]-9*P3[2]);
 }

 // trained zone nodes, sorted back-to-front
 var pts=[];
 for(var nm in S.coords){
  var P4=proj(S.coords[nm],cx,cy,sc);
  pts.push([P4[3],P4,nm]);
 }
 pts.sort(function(p,q){return q[0]-p[0]});
 for(var p=0;p<pts.length;p++){
  var P5=pts[p][1],nm2=pts[p][2],act=(nm2===S.guess);
  var rr=(act?11:7)*P5[2];
  var gr=g.createRadialGradient(P5[0],P5[1],0,P5[0],P5[1],rr*3);
  gr.addColorStop(0,act?'rgba(74,222,128,0.45)':'rgba(96,165,250,0.28)');
  gr.addColorStop(1,'rgba(0,0,0,0)');
  g.beginPath();g.arc(P5[0],P5[1],rr*3,0,6.283);g.fillStyle=gr;g.fill();
  g.beginPath();g.arc(P5[0],P5[1],rr,0,6.283);
  g.fillStyle=act?'#4ade80':'#3b82f6';g.fill();
  g.font=(11*P5[2]*2)+'px ui-monospace,monospace';
  g.fillStyle=act?'#86efac':'#94a3b8';g.textAlign='center';
  g.fillText(nm2,P5[0],P5[1]-rr-7*P5[2]);
 }

 // live estimated position
 if(S.live_xyz){
  var L=proj(S.live_xyz,cx,cy,sc);
  var ph=(tick*0.012)%1;
  for(var k2=0;k2<2;k2++){
   var pp=(ph+k2*0.5)%1;
   g.beginPath();g.arc(L[0],L[1],(8+pp*34)*L[2],0,6.283);
   g.strokeStyle='rgba('+rgb+','+(1-pp)*0.55+')';g.lineWidth=2.2;g.stroke();
  }
  g.beginPath();g.arc(L[0],L[1],6.5*L[2],0,6.283);
  g.fillStyle='#fff';g.fill();
  g.beginPath();g.arc(L[0],L[1],3*L[2],0,6.283);
  g.fillStyle='rgb('+rgb+')';g.fill();
 }
 tick++;
}

function bars(){
 var W=bc.width=bc.clientWidth*2,H=bc.height=bc.clientHeight*2;
 b.clearRect(0,0,W,H);
 var h=S.hist||[],mx=4;
 for(var i=0;i<h.length;i++)if(h[i]>mx)mx=h[i];
 var w=W/160,ty=H-(1.4/mx)*H;
 b.setLineDash([9,9]);b.beginPath();b.moveTo(0,ty);b.lineTo(W,ty);
 b.strokeStyle='#26303d';b.lineWidth=2;b.stroke();b.setLineDash([]);
 for(var j=0;j<h.length;j++){
  var bh=(h[j]/mx)*H;
  b.fillStyle=h[j]>1.4?'#4ade80':'#1b2431';
  b.fillRect(j*w,H-bh,w-2,bh);
 }
}

function id(s){return document.getElementById(s)}
async function poll(){
 try{
  var d=await(await fetch('/api/state')).json();
  S=d;
  id('rs').textContent=(d.rssi===null?'—':d.rssi+' dBm');
  var sg=id('sg');sg.textContent=d.sigma.toFixed(2);
  sg.className='v'+(d.present?' g':'');
  id('mo').textContent=d.motion.toFixed(3);
  id('pk').textContent=d.peak.toFixed(2);
  id('ev').textContent=d.events;
  id('nn').textContent=d.n.toLocaleString();
  id('scn').textContent=d.scans;
  id('up').textContent=Math.floor(d.up/60)+'m '+(d.up%60)+'s';
  var zc=id('zc');zc.className=d.present?'on':'';
  id('word').textContent=d.present?'PRESENT':'ABSENT';
  id('sub').textContent=d.present?'movement detected':'no movement';
  var lv=id('live');
  if(d.scanning){lv.className='sc';id('lt').textContent='SCANNING'}
  else{lv.className='';id('lt').textContent='LIVE · REAL DATA'}
  var z=id('zone');
  if(d.guess){z.textContent=d.guess;z.className=''}
  else{z.textContent=Object.keys(d.zones).length?'locating…':'no zones trained';
       z.className='none'}
  id('cf').style.width=(d.conf*100)+'%';
  id('cv').textContent=d.conf?d.conf.toFixed(3):'—';
  id('cb').disabled=d.busy;
  var ah='';
  for(var i=0;i<Math.min(5,d.aps.length);i++)
   ah+='<div class="ap"><span>'+d.aps[i][1]+'</span><span>'+
       d.aps[i][2]+'</span></div>';
  id('ap').innerHTML=ah||'<div class="ap">scanning…</div>';
  var zh='';
  for(var k in d.zones)
   zh+='<div class="zl"><span>'+k+' <b>'+d.zones[k]+
       '</b></span><span class="x" onclick="del(\''+k+'\')">&times;</span></div>';
  id('zones').innerHTML=zh||'<div class="zl">none yet</div>';
 }catch(e){}
}
async function cap(){
 var n=id('zn').value.trim();
 if(!n){alert('Name the zone first');return}
 id('cb').disabled=true;
 await fetch('/api/capture',{method:'POST',
  headers:{'Content-Type':'application/json'},
  body:JSON.stringify({zone:n})});
 id('zn').value='';poll();
}
async function del(z){
 await fetch('/api/clear',{method:'POST',
  headers:{'Content-Type':'application/json'},
  body:JSON.stringify({zone:z})});
 poll();
}
function loop(){draw();bars();requestAnimationFrame(loop)}
setInterval(poll,400);poll();loop();
</script></body></html>"""

if __name__ == "__main__":
    threading.Thread(target=fast_sampler, daemon=True).start()
    threading.Thread(target=scan_worker, daemon=True).start()
    app.run(host="0.0.0.0", port=5005, threaded=True)
