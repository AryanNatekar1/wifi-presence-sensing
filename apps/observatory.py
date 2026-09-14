#!/usr/bin/env python3
"""Pi Sensing Observatory - live RSSI presence detection dashboard."""
import subprocess, time, re, statistics, collections, threading, json
from flask import Flask

IFACE, HZ, WIN, THRESH = "wlan0", 10, 30, 1.4

app = Flask(__name__)
LOCK = threading.Lock()
T0 = time.time()
S = {
    "rssi": None, "sigma": 0.0, "motion": 0.0, "present": False,
    "hist": [], "n": 0, "up": 0, "events": 0, "peak": 0.0,
    "present_n": 0, "pct": 0.0, "log": [], "rssi_hist": [],
}


def gateway():
    out = subprocess.run(["ip", "route"], capture_output=True, text=True).stdout
    m = re.search(r"default via (\S+)", out)
    return m.group(1) if m else None


def read_rssi():
    out = subprocess.run(["iw", "dev", IFACE, "link"],
                         capture_output=True, text=True).stdout
    m = re.search(r"signal:\s*(-?\d+)", out)
    return int(m.group(1)) if m else None


def sampler():
    gw = gateway()
    if gw:
        subprocess.Popen(["ping", "-i", "0.05", "-q", gw],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    buf = collections.deque(maxlen=WIN)
    was = False
    while True:
        v = read_rssi()
        if v is not None:
            buf.append(v)
            if len(buf) >= 10:
                sd = statistics.pstdev(buf)
                now = sd > THRESH
                with LOCK:
                    S["rssi"] = v
                    S["sigma"] = round(sd, 2)
                    S["motion"] = round(min(sd / 6.0, 1.0), 3)
                    S["present"] = now
                    S["n"] += 1
                    S["up"] = int(time.time() - T0)
                    S["peak"] = round(max(S["peak"], sd), 2)
                    if now:
                        S["present_n"] += 1
                    S["pct"] = round(100.0 * S["present_n"] / S["n"], 1)
                    if now and not was:
                        S["events"] += 1
                        S["log"].insert(0, {
                            "t": time.strftime("%H:%M:%S"),
                            "s": round(sd, 2),
                        })
                        S["log"] = S["log"][:6]
                    S["hist"].append(round(sd, 2))
                    S["hist"] = S["hist"][-160:]
                    S["rssi_hist"].append(v)
                    S["rssi_hist"] = S["rssi_hist"][-160:]
                was = now
        time.sleep(1.0 / HZ)


@app.route("/data")
def data():
    with LOCK:
        return json.dumps(S)


@app.route("/")
def home():
    return PAGE


PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pi Sensing Observatory</title><style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
background:#04060b;color:#d8e3ef;overflow:hidden;height:100vh}
#field{position:fixed;inset:0;width:100%;height:100%}
.ui{position:fixed;z-index:5}
#hdr{top:20px;left:24px}
#hdr h1{font-size:1.35rem;font-weight:600;letter-spacing:-.01em}
#hdr h1 b{color:#3b82f6;font-weight:600}
#hdr p{font-size:.58rem;color:#4e5f73;letter-spacing:.24em;margin-top:5px}
#live{top:22px;right:24px;display:flex;align-items:center;gap:8px;
background:rgba(10,26,16,.8);border:1px solid #15803d;border-radius:20px;
padding:6px 14px;font-size:.62rem;letter-spacing:.16em;color:#4ade80}
#dot{width:7px;height:7px;border-radius:50%;background:#4ade80;
animation:bl 1.6s infinite}
@keyframes bl{0%,100%{opacity:1}50%{opacity:.25}}
.card{background:rgba(8,12,20,.78);border:1px solid #161f2c;border-radius:11px;
padding:15px 16px;backdrop-filter:blur(10px)}
.t{font-size:.56rem;color:#4e5f73;letter-spacing:.22em;margin-bottom:11px}
.kv{display:flex;justify-content:space-between;align-items:baseline;padding:6px 0}
.kv+.kv{border-top:1px solid #101822}
.k{font-size:.67rem;color:#6f8095}
.v{font-size:.95rem;color:#60a5fa;font-variant-numeric:tabular-nums}
.v.g{color:#4ade80}
.na{font-size:.68rem;color:#333f4d}
#left{top:88px;left:24px;width:228px}
#right{top:88px;right:24px;width:238px}
#stat{margin-top:13px;padding:16px 12px;border-radius:9px;text-align:center;
border:1px solid #222d3b;transition:background .4s,border-color .4s}
#stat.on{background:rgba(21,128,61,.15);border-color:#16a34a}
#word{font-size:1.4rem;font-weight:700;letter-spacing:.09em;color:#46525f;
transition:color .3s}
#stat.on #word{color:#4ade80}
#sub{font-size:.6rem;color:#4e5f73;margin-top:6px}
#logc{margin-top:13px}
.le{display:flex;justify-content:space-between;font-size:.63rem;
color:#5f7185;padding:3px 0}
.le b{color:#4ade80;font-weight:500}
#bench{bottom:132px;left:24px;width:228px}
.bar{margin:7px 0}
.bl{display:flex;justify-content:space-between;font-size:.6rem;
color:#6f8095;margin-bottom:3px}
.bt{height:5px;background:#101822;border-radius:3px;overflow:hidden}
.bf{height:100%;border-radius:3px}
#strip{bottom:22px;left:24px;right:24px;height:92px}
#bars{width:100%;height:66px;display:block}
#cap{font-size:.58rem;color:#3f4c5b;margin-top:7px;line-height:1.6}
</style></head><body>
<canvas id="field"></canvas>

<div class="ui" id="hdr"><h1><b>&pi;</b> Pi Observatory</h1>
<p>WI-FI RSSI PRESENCE SENSING</p></div>
<div class="ui" id="live"><span id="dot"></span>LIVE &middot; REAL DATA</div>

<div class="ui card" id="left">
<div class="t">MEASURED &mdash; LIVE</div>
<div class="kv"><span class="k">RSSI</span><span class="v" id="rs">&mdash;</span></div>
<div class="kv"><span class="k">Rolling &sigma;</span><span class="v" id="sg">&mdash;</span></div>
<div class="kv"><span class="k">Motion index</span><span class="v" id="mo">&mdash;</span></div>
<div class="kv"><span class="k">Peak &sigma;</span><span class="v" id="pk">&mdash;</span></div>
<div class="kv"><span class="k">Samples</span><span class="v" id="nn">&mdash;</span></div>
<div class="kv"><span class="k">Uptime</span><span class="v" id="up">&mdash;</span></div>
</div>

<div class="ui card" id="right">
<div class="t">NOT MEASURED &mdash; REQUIRES CSI</div>
<div class="kv"><span class="k">Heart rate</span><span class="na">n/a</span></div>
<div class="kv"><span class="k">Respiration</span><span class="na">n/a</span></div>
<div class="kv"><span class="k">Pose keypoints</span><span class="na">n/a</span></div>
<div class="kv"><span class="k">Person count</span><span class="na">n/a</span></div>
<div id="stat"><div id="word">STANDBY</div><div id="sub">initialising</div></div>
<div id="logc"><div class="t" style="margin:13px 0 7px">DETECTION EVENTS
<span style="float:right;color:#60a5fa" id="ev">0</span></div>
<div id="log"></div></div>
</div>

<div class="ui card" id="bench">
<div class="t">MEASURED BENCHMARK</div>
<div class="bar"><div class="bl"><span>Empty room</span><span>0.51</span></div>
<div class="bt"><div class="bf" style="width:16%;background:#2b3746"></div></div></div>
<div class="bar"><div class="bl"><span>Walking &mdash; trial 4</span><span>2.91</span></div>
<div class="bt"><div class="bf" style="width:91%;background:#3b82f6"></div></div></div>
<div class="bar"><div class="bl"><span>Walking &mdash; trial 3</span><span>3.10</span></div>
<div class="bt"><div class="bf" style="width:97%;background:#4ade80"></div></div></div>
<div style="font-size:.58rem;color:#46525f;margin-top:9px;line-height:1.6">
Median rolling &sigma; (dBm). Separation 5.7&ndash;6.1&times;,
non-overlapping. Means matched within 1.2 dB.</div>
</div>

<div class="ui" id="strip"><canvas id="bars"></canvas>
<div id="cap">Threshold &sigma; &gt; 1.4 dBm &middot; 3.4 s window &middot; 8.9 Hz
&middot; BCM43430 single antenna &middot; no camera, no microphone<br>
Detects movement through the Pi&ndash;AP path. Stationary occupancy, vital signs
and pose are not obtainable from RSSI.</div></div>

<script>
var fc=document.getElementById('field'),f=fc.getContext('2d');
var bc=document.getElementById('bars'),b=bc.getContext('2d');
var sig=0,disp=0,pres=false,hist=[],sweep=0,tick=0;
var P=[];for(var i=0;i<70;i++)P.push({a:Math.random()*6.283,
r:.25+Math.random()*.75,s:.0008+Math.random()*.0022,z:Math.random()});

function field(){
 var W=fc.width=fc.clientWidth*2,H=fc.height=fc.clientHeight*2;
 f.clearRect(0,0,W,H);
 var cx=W/2,cy=H/2+H*.03,R=Math.min(W,H)*.30;
 disp+=(sig-disp)*.12;
 var e=Math.min(disp/4,1.3);
 var col=pres?[74,222,128]:[59,130,246];
 var rgb=col[0]+','+col[1]+','+col[2];

 // ground mesh
 var rows=13,cols=26;
 for(var r=1;r<=rows;r++){
  f.beginPath();
  for(var c=0;c<=cols;c++){
   var t=c/cols*Math.PI*2;
   var rad=R*(r/rows)*1.55;
   var w=Math.sin(t*4+tick*.05+r*.5)*e*R*.055;
   var x=cx+Math.cos(t)*(rad+w);
   var y=cy+Math.sin(t)*(rad+w)*.30+Math.sin(r*.7+tick*.04)*e*R*.02;
   c?f.lineTo(x,y):f.moveTo(x,y);
  }
  f.closePath();
  f.strokeStyle='rgba('+rgb+','+(.05+ (1-r/rows)*.13)+')';
  f.lineWidth=1.4;f.stroke();
 }
 for(var s=0;s<24;s++){
  var t2=s/24*Math.PI*2;
  f.beginPath();
  for(var r2=1;r2<=rows;r2++){
   var rad2=R*(r2/rows)*1.55;
   var w2=Math.sin(t2*4+tick*.05+r2*.5)*e*R*.055;
   var x2=cx+Math.cos(t2)*(rad2+w2);
   var y2=cy+Math.sin(t2)*(rad2+w2)*.30+Math.sin(r2*.7+tick*.04)*e*R*.02;
   r2>1?f.lineTo(x2,y2):f.moveTo(x2,y2);
  }
  f.strokeStyle='rgba('+rgb+',.055)';f.lineWidth=1.2;f.stroke();
 }

 // radar sweep
 sweep+=pres?.028:.012;
 var sx=cx+Math.cos(sweep)*R*1.55,sy=cy+Math.sin(sweep)*R*1.55*.30;
 var grd=f.createLinearGradient(cx,cy,sx,sy);
 grd.addColorStop(0,'rgba('+rgb+',.32)');
 grd.addColorStop(1,'rgba('+rgb+',0)');
 f.beginPath();f.moveTo(cx,cy);f.lineTo(sx,sy);
 f.strokeStyle=grd;f.lineWidth=3;f.stroke();

 // particles
 for(var p=0;p<P.length;p++){
  var q=P[p];q.a+=q.s*(1+e*2.5);
  var pr=R*1.55*q.r;
  var px=cx+Math.cos(q.a)*pr, py=cy+Math.sin(q.a)*pr*.30-q.z*R*.5*e;
  var sz=1.6+q.z*2.4;
  f.beginPath();f.arc(px,py,sz,0,6.283);
  f.fillStyle='rgba('+rgb+','+(.12+e*.3)*q.z+')';f.fill();
 }

 // core
 var cr=R*.19*(1+e*.16);
 var cg=f.createRadialGradient(cx,cy,0,cx,cy,cr*2.4);
 cg.addColorStop(0,'rgba('+rgb+',.5)');
 cg.addColorStop(.4,'rgba('+rgb+',.12)');
 cg.addColorStop(1,'rgba('+rgb+',0)');
 f.beginPath();f.arc(cx,cy,cr*2.4,0,6.283);f.fillStyle=cg;f.fill();
 f.beginPath();f.arc(cx,cy,cr,0,6.283);
 f.strokeStyle='rgba('+rgb+',.65)';f.lineWidth=2.2;f.stroke();

 // pulse rings on detection
 if(pres){
  var ph=(tick*.008)%1;
  for(var k=0;k<2;k++){
   var pp=(ph+k*.5)%1;
   f.beginPath();
   f.ellipse(cx,cy,R*1.55*pp,R*1.55*.30*pp,0,0,6.283);
   f.strokeStyle='rgba(74,222,128,'+(1-pp)*.45+')';
   f.lineWidth=2.6;f.stroke();
  }
 }
 tick++;
}

function bars(){
 var W=bc.width=bc.clientWidth*2,H=bc.height=bc.clientHeight*2;
 b.clearRect(0,0,W,H);
 var mx=4;for(var i=0;i<hist.length;i++)if(hist[i]>mx)mx=hist[i];
 var w=W/160, ty=H-(1.4/mx)*H;
 b.setLineDash([9,9]);b.beginPath();b.moveTo(0,ty);b.lineTo(W,ty);
 b.strokeStyle='#26303d';b.lineWidth=2;b.stroke();b.setLineDash([]);
 for(var j=0;j<hist.length;j++){
  var v=hist[j],h=(v/mx)*H;
  b.fillStyle=v>1.4?'#4ade80':'#1b2431';
  b.fillRect(j*w,H-h,w-2,h);
 }
}

function esc(s){return String(s).replace(/[<>&]/g,'')}

async function poll(){
 try{
  var d=await(await fetch('/data')).json();
  sig=d.sigma;pres=d.present;hist=d.hist;
  document.getElementById('rs').textContent=(d.rssi===null?'—':d.rssi+' dBm');
  var sgEl=document.getElementById('sg');
  sgEl.textContent=d.sigma.toFixed(2);
  sgEl.className='v'+(d.present?' g':'');
  document.getElementById('mo').textContent=d.motion.toFixed(3);
  document.getElementById('pk').textContent=d.peak.toFixed(2);
  document.getElementById('nn').textContent=d.n.toLocaleString();
  document.getElementById('up').textContent=
    Math.floor(d.up/60)+'m '+(d.up%60)+'s';
  document.getElementById('ev').textContent=d.events;
  var st=document.getElementById('stat');
  st.className=d.present?'on':'';
  document.getElementById('word').textContent=d.present?'PRESENT':'ABSENT';
  document.getElementById('sub').textContent=
    d.present?'movement in monitored path':'no movement detected';
  var h='';
  for(var i=0;i<d.log.length;i++)
   h+='<div class="le"><span>'+esc(d.log[i].t)+
      '</span><b>&sigma; '+esc(d.log[i].s)+'</b></div>';
  document.getElementById('log').innerHTML=h||
   '<div class="le" style="color:#333f4d">no events yet</div>';
 }catch(e){}
}
function loop(){field();bars();requestAnimationFrame(loop)}
setInterval(poll,300);poll();loop();
</script></body></html>"""

if __name__ == "__main__":
    threading.Thread(target=sampler, daemon=True).start()
    app.run(host="0.0.0.0", port=5003)
