#!/usr/bin/env python3
"""Pi RF Shadow Imager - coarse radio tomographic imaging from multi-AP RSSI.

Principle
---------
Each visible access point forms an independent radio link into the Pi. When a
body blocks a link, that link attenuates relative to its empty-room baseline.
Links arrive from different bearings, so the intersection of several shadowed
links localises the blocker.

This is radio tomographic imaging (RTI), simplified for a single receiver.

Honest scope
------------
This is NOT LiDAR and NOT a floorplan. There is no time-of-flight, no phase and
one antenna, so nothing here measures distance. The output is an occupancy
LIKELIHOOD field over assumed link bearings. Bearings are unknown by default
and are set by you on the dial; accuracy depends entirely on how well they
match the real AP directions. Resolution is coarse - expect a blob, not a wall.
"""
import subprocess, re, time, json, threading, math, statistics, collections
from flask import Flask, request

try:
    import numpy as np
except Exception:
    np = None

IFACE = "wlan0"
GRID = 48           # heatmap resolution per side
SIGMA = 0.16        # link corridor half-width, in normalised grid units
MIN_EV = 1.2        # z-score below which a link counts as unshadowed

app = Flask(__name__)
LOCK = threading.Lock()
T0 = time.time()

D = {
    "base": {},          # bssid -> {"mu":float,"sd":float,"ssid":str,"n":int}
    "bearings": {},      # bssid -> radians, user adjustable
    "live": {},          # bssid -> dbm
    "ev": {},            # bssid -> z-score evidence
    "grid": [],
    "bearing": 0.0,
    "rose": [],
    "conf": 0.0,
    "scans": 0,
    "calibrating": False,
    "cal_n": 0,
    "started": time.strftime("%Y-%m-%d %H:%M:%S"),
    "history": [],
}


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
            ssid = m.group(1)[:16]
        m = re.match(r"signal:\s*(-?[\d.]+)", s)
        if m and bss:
            seen[bss] = (float(m.group(1)), ssid)
    return seen


def evidence(live):
    """Per-link shadow evidence: how far below empty-room baseline, in sigmas."""
    ev = {}
    with LOCK:
        base = {k: dict(v) for k, v in D["base"].items()}
    for b, mu_sd in base.items():
        if b not in live:
            # link vanished entirely - strong but unreliable evidence
            ev[b] = 2.5
            continue
        drop = mu_sd["mu"] - live[b]
        z = drop / max(mu_sd["sd"], 0.8)
        ev[b] = round(max(0.0, z), 2)
    return ev


def image(ev, bearings):
    """Angular blockage likelihood.

    With a single receiver every link converges on the Pi, so the links cannot
    triangulate a position - they only carry BEARING information. This returns
    an angular likelihood over 360 bins plus a swept field for display.
    Range is not recoverable and is not claimed.
    """
    active = [(b, e) for b, e in ev.items() if e >= MIN_EV and b in bearings]
    if not active or np is None:
        return [], 0.0, 0.0, []

    BINS = 180
    th = np.linspace(-np.pi, np.pi, BINS, endpoint=False)
    ang = np.zeros(BINS)
    spread = 0.42  # angular uncertainty per link, radians

    for b, e in active:
        d = np.angle(np.exp(1j * (th - bearings[b])))
        ang += min(e, 6.0) * np.exp(-(d ** 2) / (2 * spread ** 2))

    if ang.max() <= 0:
        return [], 0.0, 0.0, []
    ang = ang / ang.max()

    # circular concentration as confidence, tempered by link count
    w = ang / ang.sum()
    R = float(abs((w * np.exp(1j * th)).sum()))
    conf = round(min(1.0, R * min(1.0, len(active) / 3.0)), 3)
    peak_th = round(float(th[int(np.argmax(ang))]), 4)

    # swept 2D field for display: bearing likelihood, radially tapered.
    # the taper encodes that a blocker can be anywhere along the path, with
    # least certainty right at the receiver.
    ax = np.linspace(-1.0, 1.0, GRID)
    X, Y = np.meshgrid(ax, ax)
    r = np.sqrt(X ** 2 + Y ** 2)
    a = np.arctan2(Y, X)
    idx = ((a + np.pi) / (2 * np.pi) * BINS).astype(int) % BINS
    field = ang[idx]
    taper = np.exp(-((r - 0.55) ** 2) / (2 * 0.30 ** 2)) * (r < 1.02)
    field = field * taper
    if field.max() > 0:
        field = field / field.max()

    flat = (field * 255).astype("uint8").flatten().tolist()
    return flat, peak_th, conf, [round(float(v), 3) for v in ang]


def worker():
    while True:
        found = scan()
        if found:
            live = {b: v[0] for b, v in found.items()}
            with LOCK:
                cal = D["calibrating"]
            if cal:
                with LOCK:
                    for b, (dbm, ssid) in found.items():
                        e = D["base"].setdefault(
                            b, {"mu": dbm, "sd": 1.0, "ssid": ssid,
                                "n": 0, "_s": []})
                        e["_s"].append(dbm)
                        e["ssid"] = ssid or e["ssid"]
                        e["n"] = len(e["_s"])
                        e["mu"] = round(sum(e["_s"]) / len(e["_s"]), 2)
                        e["sd"] = round(
                            statistics.pstdev(e["_s"]) if len(e["_s"]) > 1
                            else 1.0, 2)
                    D["cal_n"] += 1
                    n = len(D["base"])
                    for i, b in enumerate(sorted(D["base"])):
                        D["bearings"].setdefault(b, 2 * math.pi * i / max(1, n))
            else:
                ev = evidence(live)
                with LOCK:
                    bearings = dict(D["bearings"])
                grid, peak_th, conf, rose = image(ev, bearings)
                with LOCK:
                    D["live"] = live
                    D["ev"] = ev
                    D["grid"] = grid
                    D["bearing"] = peak_th
                    D["rose"] = rose
                    D["conf"] = conf
                    D["scans"] += 1
                    if conf > 0:
                        D["history"].append(
                            {"t": round(time.time() - T0, 1),
                             "th": peak_th, "c": conf})
                        D["history"] = D["history"][-400:]
        time.sleep(1.0)


@app.route("/api/state")
def state():
    with LOCK:
        links = []
        for b in sorted(D["base"]):
            links.append([
                b, D["base"][b]["ssid"] or b[-8:],
                D["base"][b]["mu"], D["live"].get(b),
                D["ev"].get(b, 0.0),
                round(D["bearings"].get(b, 0.0), 4),
            ])
        return json.dumps({
            "grid": D["grid"], "n": GRID, "bearing": D["bearing"],
            "rose": D["rose"],
            "conf": D["conf"], "scans": D["scans"],
            "calibrating": D["calibrating"], "cal_n": D["cal_n"],
            "links": links, "trail": D["history"][-60:],
        })


@app.route("/api/calibrate", methods=["POST"])
def calibrate():
    on = bool((request.json or {}).get("on"))
    with LOCK:
        if on:
            D["base"] = {}
            D["bearings"] = {}
            D["cal_n"] = 0
        else:
            for e in D["base"].values():
                e.pop("_s", None)
        D["calibrating"] = on
    return json.dumps({"ok": True})


@app.route("/api/bearing", methods=["POST"])
def bearing():
    body = request.json or {}
    b, th = body.get("bssid"), body.get("theta")
    with LOCK:
        if b in D["bearings"]:
            D["bearings"][b] = float(th)
    return json.dumps({"ok": True})


@app.route("/api/export")
def export():
    with LOCK:
        p = {
            "meta": {
                "device": "Raspberry Pi 3B / BCM43430 (single antenna)",
                "method": "radio tomographic imaging from multi-AP RSSI "
                          "attenuation; back-projection over user-set bearings",
                "grid": GRID, "corridor_sigma": SIGMA,
                "evidence_threshold_z": MIN_EV,
                "limits": "no time-of-flight, no phase, one antenna. "
                          "Output is an occupancy likelihood field, not "
                          "geometry. Bearings are user-supplied, not measured.",
                "session_started": D["started"],
                "exported": time.strftime("%Y-%m-%d %H:%M:%S"),
                "scans": D["scans"],
            },
            "baseline": {b: {k: v for k, v in e.items() if k != "_s"}
                         for b, e in D["base"].items()},
            "bearings": D["bearings"],
            "track": D["history"],
        }
    return (json.dumps(p, indent=2), 200,
            {"Content-Type": "application/json",
             "Content-Disposition": 'attachment; filename="rf_imaging.json"'})


@app.route("/")
def home():
    return PAGE


PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pi RF Shadow Imager</title><style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
background:#04060b;color:#d8e3ef;overflow:hidden;height:100vh}
#img{position:fixed;inset:0;width:100%;height:100%;cursor:crosshair}
.ui{position:fixed;z-index:5}
#hdr{top:18px;left:22px;pointer-events:none}
#hdr h1{font-size:1.28rem;font-weight:600}#hdr h1 b{color:#3b82f6}
#hdr p{font-size:.55rem;color:#4e5f73;letter-spacing:.22em;margin-top:4px}
#badge{top:20px;right:22px;display:flex;align-items:center;gap:8px;
background:rgba(10,26,16,.82);border:1px solid #15803d;border-radius:20px;
padding:6px 14px;font-size:.6rem;letter-spacing:.15em;color:#4ade80}
#badge.cal{background:rgba(41,28,5,.82);border-color:#a16207;color:#fbbf24}
#dot{width:7px;height:7px;border-radius:50%;background:currentColor;
animation:bl 1.6s infinite}@keyframes bl{0%,100%{opacity:1}50%{opacity:.25}}
.card{background:rgba(8,12,20,.85);border:1px solid #161f2c;border-radius:11px;
padding:14px 15px;backdrop-filter:blur(10px)}
.t{font-size:.54rem;color:#4e5f73;letter-spacing:.22em;margin-bottom:10px}
.kv{display:flex;justify-content:space-between;padding:5px 0;font-size:.7rem}
.kv+.kv{border-top:1px solid #101822}
.k{color:#6f8095}.v{color:#60a5fa;font-variant-numeric:tabular-nums}
#left{top:82px;left:22px;width:250px;max-height:64vh;overflow-y:auto}
#right{top:82px;right:22px;width:232px}
.lk{display:flex;justify-content:space-between;align-items:center;
font-size:.63rem;padding:5px 0;border-top:1px solid #101822;color:#7d8fa3}
.lk .e{font-variant-numeric:tabular-nums}
.lk.hot{color:#4ade80}.lk.hot .e{color:#4ade80;font-weight:600}
button{font-family:inherit;font-size:.7rem;border-radius:7px;cursor:pointer;
border:1px solid #27405c;background:#132a44;color:#d8e3ef;padding:9px 11px}
button:hover{background:#1b3a5c}
button.warn{background:#3a2408;border-color:#7c4a0a;color:#fbbf24}
.row{display:flex;gap:7px;margin-top:9px}.row button{flex:1}
.note{font-size:.58rem;color:#46525f;line-height:1.7;margin-top:11px}
.warnbox{background:rgba(69,26,3,.45);border:1px solid #7c2d12;color:#fdba74;
font-size:.6rem;padding:10px 11px;border-radius:8px;line-height:1.6;
margin-top:11px}
#hint{bottom:22px;left:22px;right:22px;font-size:.57rem;color:#3f4c5b;
line-height:1.7;pointer-events:none;text-align:center}
</style></head><body>
<canvas id="img"></canvas>

<div class="ui" id="hdr"><h1><b>&pi;</b> RF Shadow Imager</h1>
<p>MULTI-AP ATTENUATION TOMOGRAPHY</p></div>
<div class="ui" id="badge"><span id="dot"></span><span id="bt">LIVE</span></div>

<div class="ui card" id="left">
<div class="t">LINKS &mdash; SHADOW EVIDENCE (z)</div>
<div id="links"><div class="lk">calibrate first</div></div>
<div class="note">Drag any node on the dial to match the real direction of that
router. Bearings start evenly spaced and are guesses until you set them.</div>
</div>

<div class="ui card" id="right">
<div class="t">FIELD</div>
<div class="kv"><span class="k">Shadowed links</span><span class="v" id="nh">&mdash;</span></div>
<div class="kv"><span class="k">Blocked bearing</span><span class="v" id="px">&mdash;</span></div>
<div class="kv"><span class="k">Range</span><span class="v" style="color:#333f4d">not recoverable</span></div>
<div class="kv"><span class="k">Confidence</span><span class="v" id="cf">&mdash;</span></div>
<div class="kv"><span class="k">Scans</span><span class="v" id="sc">&mdash;</span></div>
<div class="kv"><span class="k">Baseline scans</span><span class="v" id="cn">&mdash;</span></div>
<div class="row"><button id="cal" class="warn" onclick="cal()">Calibrate</button></div>
<div class="row"><button onclick="location='/api/export'">Export JSON</button></div>
<div class="warnbox">Calibrate with the room EMPTY. Leave, then start it, wait
for 10+ baseline scans, stop it, and come back in. Everything is measured as
deviation from that baseline.</div>
<div class="note"><b style="color:#6f8095">Bearing only.</b> All links end at
the Pi, so they cannot triangulate a point - they give direction, not distance.
Range, wall positions, room shape, pose and vital signs are not recoverable
from RSSI with one antenna.</div>
</div>

<div class="ui" id="hint">Bright wedge = direction of the blocked links
&middot; bearing only, no range &middot; one receiver cannot triangulate, so
this is a direction estimate, not a floorplan</div>

<script>
var c=document.getElementById('img'),x=c.getContext('2d');
var S={grid:[],n:48,bearing:0,rose:[],conf:0,links:[],trail:[],calibrating:false};
var tick=0,drag=null,cxg=0,cyg=0,Rg=0;

function id(s){return document.getElementById(s)}

c.addEventListener('mousedown',function(e){
 var r=c.getBoundingClientRect();
 var mx=(e.clientX-r.left)*2, my=(e.clientY-r.top)*2;
 for(var i=0;i<S.links.length;i++){
  var th=S.links[i][5];
  var px=cxg+Math.cos(th)*Rg, py=cyg+Math.sin(th)*Rg;
  if(Math.hypot(mx-px,my-py)<26){drag=S.links[i][0];return}
 }
});
window.addEventListener('mouseup',function(){drag=null});
window.addEventListener('mousemove',function(e){
 if(!drag)return;
 var r=c.getBoundingClientRect();
 var mx=(e.clientX-r.left)*2, my=(e.clientY-r.top)*2;
 var th=Math.atan2(my-cyg,mx-cxg);
 for(var i=0;i<S.links.length;i++) if(S.links[i][0]===drag) S.links[i][5]=th;
 fetch('/api/bearing',{method:'POST',
  headers:{'Content-Type':'application/json'},
  body:JSON.stringify({bssid:drag,theta:th})});
});

function heat(v){
 // 0..1 -> deep blue -> cyan -> green -> yellow -> white
 var stops=[[4,8,20],[12,45,90],[20,120,150],[74,222,128],[240,230,140],[255,255,255]];
 var t=Math.max(0,Math.min(0.999,v))*(stops.length-1);
 var i=Math.floor(t),f=t-i;
 var a=stops[i],b=stops[i+1]||stops[i];
 return [a[0]+(b[0]-a[0])*f,a[1]+(b[1]-a[1])*f,a[2]+(b[2]-a[2])*f];
}

function draw(){
 var W=c.width=c.clientWidth*2,H=c.height=c.clientHeight*2;
 x.clearRect(0,0,W,H);
 var cx=W/2,cy=H/2,R=Math.min(W,H)*0.34;
 cxg=cx;cyg=cy;Rg=R;

 // heat field
 var N=S.n,gr=S.grid;
 if(gr&&gr.length===N*N){
  var img=x.createImageData(N,N);
  for(var i=0;i<N*N;i++){
   var v=gr[i]/255, col=heat(v);
   img.data[i*4]=col[0];img.data[i*4+1]=col[1];img.data[i*4+2]=col[2];
   img.data[i*4+3]=Math.min(255,40+v*215);
  }
  var off=document.createElement('canvas');off.width=N;off.height=N;
  off.getContext('2d').putImageData(img,0,0);
  x.save();x.imageSmoothingEnabled=true;
  x.globalCompositeOperation='lighter';
  x.drawImage(off,cx-R,cy-R,R*2,R*2);
  x.restore();
 }

 // range rings
 for(var k=1;k<=4;k++){
  x.beginPath();x.arc(cx,cy,R*k/4,0,6.283);
  x.strokeStyle='rgba(96,165,250,0.09)';x.lineWidth=1.3;x.stroke();
 }
 x.beginPath();x.moveTo(cx-R,cy);x.lineTo(cx+R,cy);
 x.moveTo(cx,cy-R);x.lineTo(cx,cy+R);
 x.strokeStyle='rgba(96,165,250,0.07)';x.lineWidth=1.2;x.stroke();

 // link corridors
 for(var i2=0;i2<S.links.length;i2++){
  var L=S.links[i2],th=L[5],ev=L[4]||0;
  var ax2=cx+Math.cos(th)*R, ay=cy+Math.sin(th)*R;
  var hot=ev>=1.2;
  var g2=x.createLinearGradient(ax2,ay,cx,cy);
  g2.addColorStop(0,hot?'rgba(74,222,128,0.45)':'rgba(96,165,250,0.12)');
  g2.addColorStop(1,'rgba(96,165,250,0.02)');
  x.beginPath();x.moveTo(ax2,ay);x.lineTo(cx,cy);
  x.strokeStyle=g2;x.lineWidth=hot?Math.min(9,3+ev*1.3):1.6;x.stroke();
  // node
  x.beginPath();x.arc(ax2,ay,hot?8:5.5,0,6.283);
  x.fillStyle=hot?'#4ade80':'#3b5b82';x.fill();
  x.font='16px ui-monospace,monospace';x.textAlign='center';
  x.fillStyle=hot?'#86efac':'#5a6f87';
  x.fillText(L[1],ax2+Math.cos(th)*34,ay+Math.sin(th)*34+5);
 }

 // bearing history, most recent brightest
 if(S.trail&&S.trail.length){
  for(var t2=0;t2<S.trail.length;t2++){
   var P=S.trail[t2],age=t2/S.trail.length;
   var qx=cx+Math.cos(P.th)*R*0.92, qy=cy+Math.sin(P.th)*R*0.92;
   x.beginPath();x.arc(qx,qy,2.2,0,6.283);
   x.fillStyle='rgba(255,255,255,'+(0.05+age*0.35)*P.c+')';x.fill();
  }
 }

 // angular likelihood rose
 if(S.rose&&S.rose.length){
  var B=S.rose.length;
  x.beginPath();
  for(var rr=0;rr<=B;rr++){
   var a3=-Math.PI+(rr%B)/B*2*Math.PI;
   var rad=R*(0.12+S.rose[rr%B]*0.30);
   var qx2=cx+Math.cos(a3)*rad, qy2=cy+Math.sin(a3)*rad;
   rr?x.lineTo(qx2,qy2):x.moveTo(qx2,qy2);
  }
  x.closePath();
  x.fillStyle='rgba(74,222,128,0.10)';x.fill();
  x.strokeStyle='rgba(74,222,128,0.45)';x.lineWidth=1.8;x.stroke();
 }

 // estimated bearing wedge - a direction, not a point
 if(S.conf>0){
  var bth=S.bearing, half=0.42*(1.25-S.conf*0.6);
  var wg=x.createRadialGradient(cx,cy,R*0.1,cx,cy,R);
  wg.addColorStop(0,'rgba(255,255,255,0.03)');
  wg.addColorStop(1,'rgba(255,255,255,'+(0.10+S.conf*0.18)+')');
  x.beginPath();x.moveTo(cx,cy);
  x.arc(cx,cy,R,bth-half,bth+half);x.closePath();
  x.fillStyle=wg;x.fill();
  x.beginPath();x.moveTo(cx,cy);
  x.lineTo(cx+Math.cos(bth)*R,cy+Math.sin(bth)*R);
  x.strokeStyle='rgba(255,255,255,'+(0.35+S.conf*0.45)+')';
  x.lineWidth=2.4;x.stroke();
  var ph=(tick*0.02)%1;
  var sr=R*(0.15+ph*0.8);
  x.beginPath();x.arc(cx,cy,sr,bth-half,bth+half);
  x.strokeStyle='rgba(255,255,255,'+(1-ph)*0.45+')';x.lineWidth=2.6;x.stroke();
 }

 // receiver at centre
 x.beginPath();x.arc(cx,cy,10,0,6.283);
 x.fillStyle='#0b1320';x.fill();
 x.strokeStyle='#60a5fa';x.lineWidth=2.4;x.stroke();
 x.font='15px ui-monospace,monospace';x.fillStyle='#4e5f73';
 x.textAlign='center';x.fillText('Pi',cx,cy+34);
 tick++;
}

async function poll(){
 try{
  var d=await(await fetch('/api/state')).json();
  var keep={};
  for(var i=0;i<S.links.length;i++)keep[S.links[i][0]]=S.links[i][5];
  S=d;
  if(drag) for(var j=0;j<S.links.length;j++)
   if(keep[S.links[j][0]]!==undefined) S.links[j][5]=keep[S.links[j][0]];
  var hot=0,h='';
  for(var k=0;k<d.links.length;k++){
   var L=d.links[k],isHot=L[4]>=1.2;if(isHot)hot++;
   h+='<div class="lk'+(isHot?' hot':'')+'"><span>'+L[1]+'</span>'+
      '<span class="e">'+(L[3]===null?'—':L[3])+' / '+L[2]+
      ' &middot; z '+L[4].toFixed(1)+'</span></div>';
  }
  id('links').innerHTML=h||'<div class="lk">calibrate first</div>';
  id('nh').textContent=hot+' / '+d.links.length;
  id('px').textContent=d.conf>0?
    (((d.bearing*180/Math.PI)+360)%360).toFixed(0)+'\u00b0':'—';
  id('cf').textContent=d.conf.toFixed(2);
  id('sc').textContent=d.scans;
  id('cn').textContent=d.cal_n;
  var bg=id('badge');
  if(d.calibrating){bg.className='cal';id('bt').textContent='CALIBRATING';
   id('cal').textContent='Stop calibration'}
  else{bg.className='';id('bt').textContent='LIVE · REAL DATA';
   id('cal').textContent='Calibrate'}
 }catch(e){}
}
async function cal(){
 await fetch('/api/calibrate',{method:'POST',
  headers:{'Content-Type':'application/json'},
  body:JSON.stringify({on:!S.calibrating})});
 poll();
}
function loop(){draw();requestAnimationFrame(loop)}
setInterval(poll,700);poll();loop();
</script></body></html>"""

if __name__ == "__main__":
    threading.Thread(target=worker, daemon=True).start()
    app.run(host="0.0.0.0", port=5006, threaded=True)
