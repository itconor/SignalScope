# ptpclock.py — PTP Wall Clock plugin for SignalScope
#
# Full-screen clock display synced to the server's GPS-disciplined time.
# Two modes: digital (default) and studio (analog broadcast clock).
# Branding via URL parameter: ?brand=Station+Name
#
# Usage:
#   /hub/ptpclock              — digital mode
#   /hub/ptpclock?mode=studio  — analog studio clock
#   /hub/ptpclock?brand=BBC+Radio+Ulster
#   /hub/ptpclock?mode=studio&brand=My+Station&tz=Europe/London

SIGNALSCOPE_PLUGIN = {
    "id":    "ptpclock",
    "label": "PTP Clock",
    "url":   "/hub/ptpclock",
    "icon":  "",
}

CLOCK_TPL = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{{brand or 'PTP Clock'}} — SignalScope</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<style nonce="{{csp_nonce()}}">
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%;overflow:hidden;background:#0a0e1a;color:#e0e6f0;font-family:'SF Mono','Consolas','Menlo',monospace}
body{display:flex;flex-direction:column;align-items:center;justify-content:center;user-select:none}

/* ── Digital Mode ──────────────────────────────────────────────── */
.digital{display:none;flex-direction:column;align-items:center;justify-content:center;width:100%;height:100%}
.d-brand{font-size:clamp(14px,2.5vw,28px);color:#64748b;letter-spacing:0.15em;text-transform:uppercase;margin-bottom:1vh}
.d-row{display:flex;gap:clamp(20px,6vw,80px);align-items:flex-start;justify-content:center}
.d-col{text-align:center}
.d-label{font-size:clamp(12px,1.8vw,22px);color:#64748b;letter-spacing:0.2em;text-transform:uppercase;margin-bottom:0.5vh}
.d-time{font-size:clamp(48px,12vw,160px);font-weight:200;letter-spacing:0.02em;line-height:1;color:#f1f5f9}
.d-tenths{font-size:clamp(24px,5vw,64px);font-weight:200;color:#475569;vertical-align:super;margin-left:2px}
.d-date{font-size:clamp(14px,2.2vw,26px);color:#64748b;margin-top:2vh;letter-spacing:0.1em}

/* ── Studio Mode ───────────────────────────────────────────────── */
.studio{display:none;flex-direction:column;align-items:center;justify-content:center;width:100%;height:100%}
.s-brand{font-size:clamp(14px,2.5vw,28px);color:#94a3b8;letter-spacing:0.15em;text-transform:uppercase;margin-bottom:1vh}
.s-canvas-wrap{position:relative;width:min(70vh,70vw);height:min(70vh,70vw)}
.s-canvas-wrap canvas{width:100%;height:100%}
.s-utc{font-size:clamp(18px,3vw,36px);color:#f1f5f9;margin-top:1.5vh;letter-spacing:0.05em}
.s-date{font-size:clamp(12px,1.8vw,20px);color:#64748b;margin-top:0.5vh;letter-spacing:0.1em}

/* ── PTP Status Bar ────────────────────────────────────────────── */
.ptp-bar{position:fixed;bottom:0;left:0;right:0;display:flex;align-items:center;justify-content:center;gap:clamp(10px,3vw,40px);padding:8px 16px;background:rgba(10,14,26,0.85);border-top:1px solid #1e293b;font-size:clamp(10px,1.4vw,14px);color:#64748b}
.ptp-dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:4px}
.ptp-dot.ok{background:#22c55e}.ptp-dot.warn{background:#f59e0b}.ptp-dot.alert,.ptp-dot.lost{background:#ef4444}.ptp-dot.idle{background:#475569}
.ptp-val{color:#94a3b8}

/* ── Mode switcher ─────────────────────────────────────────────── */
.mode-sw{position:fixed;top:12px;right:12px;display:flex;gap:6px;opacity:0.3;transition:opacity 0.3s}
.mode-sw:hover{opacity:1}
.mode-sw button{background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:4px;padding:4px 10px;font-size:11px;cursor:pointer}
.mode-sw button.active{background:#334155;color:#f1f5f9}
</style>
</head>
<body>

<!-- Digital Clock -->
<div class="digital" id="v-digital">
  <div class="d-brand" id="d-brand"></div>
  <div class="d-row">
    <div class="d-col">
      <div class="d-label">UTC</div>
      <div class="d-time" id="d-utc">--:--:--<span class="d-tenths">.--</span></div>
    </div>
    <div class="d-col">
      <div class="d-label" id="d-tz-label">LOCAL</div>
      <div class="d-time" id="d-local">--:--:--<span class="d-tenths">.--</span></div>
    </div>
  </div>
  <div class="d-date" id="d-date">---</div>
</div>

<!-- Studio Clock -->
<div class="studio" id="v-studio">
  <div class="s-brand" id="s-brand"></div>
  <div class="s-canvas-wrap">
    <canvas id="s-canvas" width="800" height="800"></canvas>
  </div>
  <div class="s-utc" id="s-utc">--:--:--</div>
  <div class="s-date" id="s-date">---</div>
</div>

<!-- PTP Status Bar -->
<div class="ptp-bar">
  <span><span class="ptp-dot idle" id="ptp-dot"></span> PTP <span class="ptp-val" id="ptp-state">---</span></span>
  <span>Offset <span class="ptp-val" id="ptp-off">---</span></span>
  <span>Jitter <span class="ptp-val" id="ptp-jit">---</span></span>
  <span>GM <span class="ptp-val" id="ptp-gm">---</span></span>
</div>

<!-- Mode Switcher -->
<div class="mode-sw">
  <button id="btn-digital" onclick="setMode('digital')">Digital</button>
  <button id="btn-studio" onclick="setMode('studio')">Studio</button>
</div>

<script nonce="{{csp_nonce()}}">
var _mode='digital',_brand='{{brand|e}}',_tz='{{tz|e}}';
var _utcH=0,_utcM=0,_utcS=0,_utcMs=0;
var _locH=0,_locM=0,_locS=0,_locMs=0;
var _dateStr='',_tzLabel='LOCAL';
var _ptpState='idle',_ptpOff=0,_ptpJit=0,_ptpGm='';
var _serverT=0,_clientT=0; // for interpolation between polls

function setMode(m){
  _mode=m;
  document.getElementById('v-digital').style.display=m==='digital'?'flex':'none';
  document.getElementById('v-studio').style.display=m==='studio'?'flex':'none';
  document.getElementById('btn-digital').className=m==='digital'?'active':'';
  document.getElementById('btn-studio').className=m==='studio'?'active':'';
  if(m==='studio')resizeCanvas();
  var u=new URL(location);u.searchParams.set('mode',m);history.replaceState(null,'',u);
}

// ── Branding ──────────────────────────────────────────────────────
if(_brand){
  document.getElementById('d-brand').textContent=_brand;
  document.getElementById('s-brand').textContent=_brand;
  document.title=_brand+' — PTP Clock';
}

// ── Timezone label ────────────────────────────────────────────────
if(_tz){
  try{
    var _test=new Date().toLocaleString('en-GB',{timeZone:_tz,timeZoneName:'short'});
    var _tzShort=_test.split(' ').pop();
    _tzLabel=_tzShort||_tz;
  }catch(e){_tzLabel=_tz;}
  document.getElementById('d-tz-label').textContent=_tzLabel;
}

// ── Poll server time ──────────────────────────────────────────────
function poll(){
  fetch('/api/hub/ptpclock/time',{credentials:'same-origin'}).then(function(r){return r.json()}).then(function(d){
    _serverT=d.unix;
    _clientT=performance.now()/1000;
    _dateStr=d.date;
    _tzLabel=d.tz_label||_tzLabel;
    document.getElementById('d-tz-label').textContent=_tzLabel;
    if(d.ptp){
      _ptpState=d.ptp.state||'idle';
      _ptpOff=d.ptp.offset_us||0;
      _ptpJit=d.ptp.jitter_us||0;
      _ptpGm=d.ptp.gm_id||'';
    }
    // Update PTP bar
    var dot=document.getElementById('ptp-dot');
    dot.className='ptp-dot '+_ptpState;
    document.getElementById('ptp-state').textContent=_ptpState.toUpperCase();
    document.getElementById('ptp-off').textContent=(_ptpOff/1000).toFixed(3)+' ms';
    document.getElementById('ptp-jit').textContent=(_ptpJit/1000).toFixed(3)+' ms';
    document.getElementById('ptp-gm').textContent=_ptpGm?_ptpGm.substring(0,16):'---';
  }).catch(function(){});
}
poll();
setInterval(poll,200);

// ── Render loop — interpolate between polls ───────────────────────
function render(){
  // Interpolate server time using local monotonic clock
  var elapsed=performance.now()/1000-_clientT;
  var now=_serverT+elapsed;
  if(_serverT===0){requestAnimationFrame(render);return;}

  // UTC breakdown
  var utcDate=new Date(now*1000);
  _utcH=utcDate.getUTCHours();_utcM=utcDate.getUTCMinutes();
  _utcS=utcDate.getUTCSeconds();_utcMs=utcDate.getUTCMilliseconds();

  // Local (in requested timezone or server default)
  var locStr;
  if(_tz){
    try{
      locStr=utcDate.toLocaleTimeString('en-GB',{timeZone:_tz,hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'});
    }catch(e){locStr=null;}
  }
  if(!locStr){
    // Use server-provided local offset (fallback)
    var locDate=utcDate; // browser local as fallback
    _locH=locDate.getHours();_locM=locDate.getMinutes();_locS=locDate.getSeconds();_locMs=locDate.getMilliseconds();
  } else {
    var pp=locStr.split(':');
    _locH=parseInt(pp[0]);_locM=parseInt(pp[1]);_locS=parseInt(pp[2]);_locMs=_utcMs;
  }

  if(_mode==='digital') renderDigital();
  else renderStudio(now);

  document.getElementById((_mode==='studio'?'s':'d')+'-date').textContent=_dateStr;

  requestAnimationFrame(render);
}

function pad2(n){return n<10?'0'+n:''+n}

function renderDigital(){
  var t1=pad2(_utcH)+':'+pad2(_utcM)+':'+pad2(_utcS);
  var ms1='.'+Math.floor(_utcMs/100);
  document.getElementById('d-utc').innerHTML=t1+'<span class="d-tenths">'+ms1+'</span>';
  var t2=pad2(_locH)+':'+pad2(_locM)+':'+pad2(_locS);
  var ms2='.'+Math.floor(_locMs/100);
  document.getElementById('d-local').innerHTML=t2+'<span class="d-tenths">'+ms2+'</span>';
}

// ── Studio analog clock ───────────────────────────────────────────
var _cvs,_ctx2d,_cw;
function resizeCanvas(){
  _cvs=document.getElementById('s-canvas');
  var wrap=_cvs.parentElement;
  var sz=Math.min(wrap.clientWidth,wrap.clientHeight);
  _cvs.width=sz*2;_cvs.height=sz*2;
  _cvs.style.width=sz+'px';_cvs.style.height=sz+'px';
  _ctx2d=_cvs.getContext('2d');
  _cw=sz*2;
}
window.addEventListener('resize',function(){if(_mode==='studio')resizeCanvas()});

function renderStudio(now){
  if(!_ctx2d)resizeCanvas();
  var c=_ctx2d,w=_cw,r=w/2;
  c.clearRect(0,0,w,w);
  c.save();
  c.translate(r,r);

  // Face
  c.beginPath();c.arc(0,0,r*0.95,0,Math.PI*2);
  c.fillStyle='#0f172a';c.fill();
  c.strokeStyle='#334155';c.lineWidth=r*0.01;c.stroke();

  // Hour markers
  for(var i=0;i<12;i++){
    var a=i*Math.PI/6-Math.PI/2;
    var isQuarter=i%3===0;
    var len=isQuarter?r*0.12:r*0.06;
    var thick=isQuarter?r*0.025:r*0.012;
    var x1=Math.cos(a)*(r*0.82);var y1=Math.sin(a)*(r*0.82);
    var x2=Math.cos(a)*(r*0.82+len);var y2=Math.sin(a)*(r*0.82+len);
    c.beginPath();c.moveTo(x1,y1);c.lineTo(x2,y2);
    c.strokeStyle=isQuarter?'#f1f5f9':'#94a3b8';c.lineWidth=thick;c.lineCap='round';c.stroke();
  }

  // Minute ticks
  for(var i=0;i<60;i++){
    if(i%5===0)continue;
    var a=i*Math.PI/30-Math.PI/2;
    var x1=Math.cos(a)*(r*0.88);var y1=Math.sin(a)*(r*0.88);
    var x2=Math.cos(a)*(r*0.91);var y2=Math.sin(a)*(r*0.91);
    c.beginPath();c.moveTo(x1,y1);c.lineTo(x2,y2);
    c.strokeStyle='#475569';c.lineWidth=r*0.005;c.stroke();
  }

  // Brand text on face
  if(_brand){
    c.font='600 '+Math.round(r*0.08)+'px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';
    c.fillStyle='#64748b';c.textAlign='center';c.textBaseline='middle';
    c.fillText(_brand,0,-r*0.3);
  }

  // "PTP" label
  c.font='600 '+Math.round(r*0.05)+'px monospace';
  c.fillStyle='#334155';c.textAlign='center';c.textBaseline='middle';
  c.fillText('PTP',0,r*0.35);

  // Hands
  var sec=_utcS+_utcMs/1000;
  var min=_utcM+sec/60;
  var hr=(_utcH%12)+min/60;

  // Hour hand
  var ha=hr*Math.PI/6-Math.PI/2;
  c.beginPath();c.moveTo(0,0);
  c.lineTo(Math.cos(ha)*r*0.5,Math.sin(ha)*r*0.5);
  c.strokeStyle='#f1f5f9';c.lineWidth=r*0.04;c.lineCap='round';c.stroke();

  // Minute hand
  var ma=min*Math.PI/30-Math.PI/2;
  c.beginPath();c.moveTo(0,0);
  c.lineTo(Math.cos(ma)*r*0.7,Math.sin(ma)*r*0.7);
  c.strokeStyle='#e2e8f0';c.lineWidth=r*0.025;c.lineCap='round';c.stroke();

  // Second hand (smooth sweep)
  var sa=sec*Math.PI/30-Math.PI/2;
  c.beginPath();
  c.moveTo(Math.cos(sa+Math.PI)*r*0.1,Math.sin(sa+Math.PI)*r*0.1);
  c.lineTo(Math.cos(sa)*r*0.82,Math.sin(sa)*r*0.82);
  c.strokeStyle='#ef4444';c.lineWidth=r*0.01;c.lineCap='round';c.stroke();

  // Center dot
  c.beginPath();c.arc(0,0,r*0.025,0,Math.PI*2);
  c.fillStyle='#ef4444';c.fill();

  c.restore();

  // UTC text below canvas
  document.getElementById('s-utc').textContent=pad2(_utcH)+':'+pad2(_utcM)+':'+pad2(_utcS)+'.'+Math.floor(_utcMs/100);
}

// ── Init ──────────────────────────────────────────────────────────
var urlMode=new URLSearchParams(location.search).get('mode')||'digital';
setMode(urlMode);
requestAnimationFrame(render);
</script>
</body>
</html>"""


def register(app, ctx):
    from flask import request, jsonify, render_template_string
    import time as _time

    monitor        = ctx["monitor"]
    login_required = ctx["login_required"]

    @app.get("/hub/ptpclock")
    @login_required
    def ptpclock_page():
        brand = request.args.get("brand", "")
        tz    = request.args.get("tz", "")
        return render_template_string(CLOCK_TPL, brand=brand, tz=tz)

    @app.get("/api/hub/ptpclock/time")
    @login_required
    def ptpclock_time():
        now = _time.time()
        utc = _time.gmtime(now)
        ms  = int((now % 1) * 1000)

        # Local time on server
        loc = _time.localtime(now)
        tz_name = _time.strftime("%Z", loc) or "LOCAL"

        # Date string
        date_str = _time.strftime("%A %d %B %Y", utc)

        # PTP data
        ptp_data = None
        ptp = getattr(monitor, "ptp", None)
        if ptp:
            last_sync_ago = now - ptp.last_sync if ptp.last_sync > 0 else -1
            ptp_data = {
                "state":         ptp.state,
                "offset_us":     round(ptp.offset_us, 1),
                "drift_us":      round(ptp.drift_us, 1),
                "jitter_us":     round(ptp.jitter_us, 1),
                "gm_id":         ptp.gm_id,
                "domain":        ptp.domain,
                "last_sync_ago": round(last_sync_ago, 1),
            }

        return jsonify({
            "utc":      _time.strftime("%H:%M:%S", utc) + f".{ms:03d}",
            "local":    _time.strftime("%H:%M:%S", loc) + f".{ms:03d}",
            "date":     date_str,
            "unix":     round(now, 3),
            "tz_label": tz_name,
            "ptp":      ptp_data,
        })
