# pdm.py — 25/7 Programme Delay Manager plugin for SignalScope
#
# Client node: connects to the PDM unit via TCP, polls delay depth at 1 Hz,
#              pushes data to the hub every 2 s.
# Hub node:    receives depth reports, displays live pills per reporting site.
#
# Routes (client / standalone):
#   /pdm                — config page (set PDM IP/port) + live local depth
#   /api/pdm/status     — JSON: {depth, connected, host, port, error}
#   /api/pdm/config     — POST: save IP/port, trigger reconnect
#
# Routes (hub):
#   /pdm                — redirects to /hub/pdm
#   /hub/pdm            — overview pills, one per reporting client site
#   /api/pdm/report     — POST from client nodes (HMAC-signed)
#   /api/pdm/hub_status — JSON: all site snapshots

SIGNALSCOPE_PLUGIN = {
    "id":      "pdm",
    "label":   "PDM",
    "url":     "/pdm",
    "icon":    "⏱",
    "version": "1.0.0",
}

import hashlib, hmac as _hmac, json, os, re, socket, threading, time, urllib.request

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_CFG_PATH = os.path.join(_BASE_DIR, "pdm_cfg.json")
_DEFAULT_CFG = {"host": "", "port": 5443}

# ── Config ────────────────────────────────────────────────────────────────────

def _load_cfg():
    try:
        with open(_CFG_PATH) as f:
            return {**_DEFAULT_CFG, **json.load(f)}
    except Exception:
        return dict(_DEFAULT_CFG)

def _save_cfg(cfg):
    with open(_CFG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

# ── Client-side live state ────────────────────────────────────────────────────

_state = {
    "depth":     None,   # float ms or None
    "connected": False,
    "host":      "",
    "port":      5443,
    "error":     "",
}
_state_lock = threading.Lock()
_stop_evt   = threading.Event()
_reload_evt = threading.Event()   # set to force immediate reconnect

_DEPTH_RE = re.compile(r'(?<=!Depth=)[0-9.]+')

# ── Hub-side received state ───────────────────────────────────────────────────

_hub_sites = {}   # site_name → {depth, connected, host, port, error, ts}
_hub_lock  = threading.Lock()
_STALE_SECS = 15

# ── HMAC helpers ──────────────────────────────────────────────────────────────

def _sign_body(secret, body_bytes, ts):
    if not secret:
        return ""
    key = hashlib.sha256(f"{secret}:signing".encode()).digest()
    msg = f"{ts:.0f}:".encode() + body_bytes
    return _hmac.new(key, msg, hashlib.sha256).hexdigest()

def _verify_sig(secret, body_bytes, ts_str, sig):
    if not secret:
        return True   # no secret → open (mirrors main app behaviour)
    try:
        ts = float(ts_str)
    except (TypeError, ValueError):
        return False
    if abs(time.time() - ts) > 60:
        return False
    return _hmac.compare_digest(_sign_body(secret, body_bytes, ts), sig or "")

# ── Client TCP poll thread ────────────────────────────────────────────────────

def _poll_loop():
    while not _stop_evt.is_set():
        cfg  = _load_cfg()
        host = cfg.get("host", "").strip()
        port = int(cfg.get("port", 5443))
        _reload_evt.clear()

        if not host:
            with _state_lock:
                _state.update(connected=False, depth=None,
                              error="No IP address configured", host="", port=port)
            _stop_evt.wait(3)
            continue

        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect((host, port))
            with _state_lock:
                _state.update(connected=True, error="", host=host, port=port)

            sock.sendall(b"enable All\r\n")
            time.sleep(0.3)

            buf = ""
            while not _stop_evt.is_set() and not _reload_evt.is_set():
                sock.sendall(b"get Depth\r\n")
                sock.settimeout(2)
                try:
                    chunk = sock.recv(4096).decode("ascii", errors="ignore")
                    if not chunk:
                        raise ConnectionResetError("connection closed by remote")
                    buf += chunk
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        m = _DEPTH_RE.search(line)
                        if m:
                            with _state_lock:
                                _state["depth"] = float(m.group())
                except socket.timeout:
                    pass

                _stop_evt.wait(1)

        except Exception as e:
            with _state_lock:
                _state.update(connected=False, depth=None, error=str(e))
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
            with _state_lock:
                _state["connected"] = False

        _stop_evt.wait(5)

# ── Client push thread (sends snapshot to hub every 2 s) ─────────────────────

def _push_loop(monitor):
    while not _stop_evt.is_set():
        try:
            cfg_ss  = monitor.app_cfg
            hub_url = (getattr(getattr(cfg_ss, "hub", None), "hub_url", "") or "").rstrip("/")
            secret  = getattr(getattr(cfg_ss, "hub", None), "secret_key", "") or ""
            site    = getattr(getattr(cfg_ss, "hub", None), "site_name", "") or ""

            if hub_url and site:
                with _state_lock:
                    snap = dict(_state)

                body = json.dumps({
                    "depth":     snap["depth"],
                    "connected": snap["connected"],
                    "host":      snap["host"],
                    "port":      snap["port"],
                    "error":     snap["error"],
                }).encode()

                ts  = time.time()
                sig = _sign_body(secret, body, ts)

                req = urllib.request.Request(
                    f"{hub_url}/api/pdm/report",
                    data=body,
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "X-Site":       site,
                        "X-Hub-Ts":     f"{ts:.0f}",
                        "X-Hub-Sig":    sig,
                    },
                )
                urllib.request.urlopen(req, timeout=5).close()

        except Exception:
            pass

        _stop_evt.wait(2)

# ── CSS shared between both templates ─────────────────────────────────────────

_CSS = """
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(circle at top,#12376f 0%,var(--bg) 38%,#05101f 100%);color:var(--tx);font-family:system-ui,sans-serif;font-size:13px;min-height:100vh}
a.btn{display:inline-block;text-decoration:none}
.btn{border:none;border-radius:8px;padding:5px 12px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit}
.btn:hover{filter:brightness(1.15)}
.bp{background:var(--acc);color:#fff}.bd{background:var(--al);color:#fff}.bg{background:#0d2346;color:var(--tx);border:1px solid var(--bor)}
.bs{padding:3px 9px;font-size:12px}.nav-active{background:var(--acc)!important;color:#fff!important}
main{max-width:820px;margin:24px auto;padding:0 16px}
.card{background:var(--sur);border:1px solid var(--bor);border-radius:12px;overflow:hidden;margin-bottom:16px}
.ch{padding:9px 14px;display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--bor);background:linear-gradient(180deg,#143766,#102b54);font-size:12px;font-weight:700;color:var(--acc);text-transform:uppercase;letter-spacing:.06em}
.cb{padding:16px}
.field{display:flex;flex-direction:column;gap:4px}
.field label{font-size:11px;color:var(--mu);font-weight:600;text-transform:uppercase;letter-spacing:.05em}
input{background:#0d1e40;border:1px solid var(--bor);border-radius:6px;color:var(--tx);padding:6px 9px;font-size:13px;font-family:inherit;width:100%}
input:focus{outline:none;border-color:var(--acc)}
.row{display:flex;gap:12px}.row .field{flex:1}
.badge{display:inline-block;border-radius:4px;padding:2px 8px;font-size:11px;font-weight:700}
.b-ok{background:#0f2318;color:var(--ok);border:1px solid #166534}
.b-al{background:#2a0a0a;color:var(--al);border:1px solid #991b1b}
.b-wn{background:#2a1800;color:var(--wn);border:1px solid #92400e}
.b-mu{background:#0d1e40;color:var(--mu);border:1px solid var(--bor)}
.msg-ok{background:#0f2318;color:var(--ok);border:1px solid #166534;padding:8px 12px;border-radius:6px;font-size:12px;margin-bottom:12px}
.msg-err{background:#2a0a0a;color:var(--al);border:1px solid #991b1b;padding:8px 12px;border-radius:6px;font-size:12px;margin-bottom:12px}
"""

# ── Client page ───────────────────────────────────────────────────────────────

_CLIENT_TPL = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<title>25/7 PDM — SignalScope</title>
<style nonce="{{csp_nonce()}}">""" + _CSS + r"""
.depth-wrap{text-align:center;padding:28px 0 14px}
.depth-num{font-size:64px;font-weight:700;letter-spacing:-2px;line-height:1;font-variant-numeric:tabular-nums}
.depth-unit{font-size:14px;color:var(--mu);margin-top:6px;text-transform:uppercase;letter-spacing:.05em}
.depth-live{color:var(--acc)}.depth-na{color:var(--mu)}
.frames-row{text-align:center;padding-bottom:14px;font-size:12px;color:var(--mu)}
.frames-row span{color:var(--tx);font-weight:600}
.status-row{display:flex;align-items:center;gap:8px;margin-bottom:4px}
</style></head>
<body>
{{topnav("PDM")|safe}}
<main>
  <div class="card">
    <div class="ch">⏱ Live Delay Depth</div>
    <div class="cb">
      <div class="status-row">
        <span id="conn-badge" class="badge b-mu">—</span>
        <span id="conn-host" style="color:var(--mu);font-size:12px"></span>
      </div>
      <div class="depth-wrap">
        <div id="depth-num" class="depth-num depth-na">—</div>
        <div class="depth-unit">milliseconds</div>
      </div>
      <div id="frames-row" class="frames-row" style="display:none">
        ≈ <span id="f25">—</span> frames @ 25 fps &nbsp;/&nbsp; <span id="f30">—</span> frames @ 30 fps
      </div>
    </div>
  </div>

  <div class="card">
    <div class="ch">⚙ PDM Connection</div>
    <div class="cb">
      <div id="cfg-msg" style="display:none"></div>
      <div class="row" style="margin-bottom:14px">
        <div class="field">
          <label>IP Address</label>
          <input id="cfg-host" type="text" placeholder="10.0.25.44" value="{{host|e}}">
        </div>
        <div class="field" style="max-width:110px">
          <label>Port</label>
          <input id="cfg-port" type="number" min="1" max="65535" value="{{port}}">
        </div>
      </div>
      <button class="btn bp" id="save-btn">Save &amp; Reconnect</button>
    </div>
  </div>
</main>
<script nonce="{{csp_nonce()}}">
function _getCsrf(){
  return (document.querySelector('meta[name="csrf-token"]')||{}).content
      ||(document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/)||[])[1]||'';
}
function showMsg(el,txt,ok){
  el.textContent=txt;el.className=ok?'msg-ok':'msg-err';el.style.display='';
  setTimeout(function(){el.style.display='none';},4000);
}
function pollStatus(){
  fetch('/api/pdm/status',{credentials:'same-origin'})
  .then(function(r){return r.json();})
  .then(function(d){
    var badge=document.getElementById('conn-badge');
    var hostEl=document.getElementById('conn-host');
    var numEl=document.getElementById('depth-num');
    var frRow=document.getElementById('frames-row');
    if(d.connected){
      badge.textContent='Connected';badge.className='badge b-ok';
      hostEl.textContent=d.host+':'+d.port;
    }else{
      badge.textContent='Disconnected';badge.className='badge b-al';
      hostEl.textContent=d.error||'';
    }
    if(d.depth!==null&&d.depth!==undefined){
      numEl.textContent=d.depth.toFixed(1);numEl.className='depth-num depth-live';
      frRow.style.display='';
      document.getElementById('f25').textContent=(d.depth/40).toFixed(2);
      document.getElementById('f30').textContent=(d.depth/33.333).toFixed(2);
    }else{
      numEl.textContent='—';numEl.className='depth-num depth-na';
      frRow.style.display='none';
    }
  }).catch(function(){});
}
setInterval(pollStatus,1000);pollStatus();

document.getElementById('save-btn').addEventListener('click',function(){
  var msgEl=document.getElementById('cfg-msg');
  var host=document.getElementById('cfg-host').value.trim();
  var port=parseInt(document.getElementById('cfg-port').value)||5443;
  if(!host){showMsg(msgEl,'IP address is required',false);return;}
  fetch('/api/pdm/config',{
    method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRFToken':_getCsrf()},
    body:JSON.stringify({host:host,port:port})
  }).then(function(r){return r.json();})
  .then(function(d){showMsg(msgEl,d.ok?'Saved — reconnecting…':'Error: '+(d.error||'?'),!!d.ok);})
  .catch(function(e){showMsg(msgEl,'Error: '+e,false);});
});
</script>
</body></html>"""

# ── Hub overview page ─────────────────────────────────────────────────────────

_HUB_TPL = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<title>25/7 PDM — SignalScope</title>
<style nonce="{{csp_nonce()}}">""" + _CSS + r"""
.pills{display:flex;flex-wrap:wrap;gap:14px;padding:4px 0}
.pill{background:#071a3a;border:1px solid var(--bor);border-radius:12px;padding:16px 20px;min-width:210px;flex:1;max-width:320px}
.pill-site{font-size:11px;color:var(--mu);font-weight:600;text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px;display:flex;align-items:center;gap:6px}
.pill-depth{font-size:40px;font-weight:700;letter-spacing:-1px;color:var(--acc);font-variant-numeric:tabular-nums;line-height:1}
.pill-depth.na{color:var(--mu);font-size:28px}
.pill-unit{font-size:11px;color:var(--mu);margin-top:3px;text-transform:uppercase;letter-spacing:.05em}
.pill-frames{font-size:11px;color:var(--mu);margin-top:5px}
.pill-frames span{color:var(--tx);font-weight:600}
.pill-err{font-size:11px;color:var(--al);margin-top:4px}
.no-sites{color:var(--mu);padding:8px 0}
</style></head>
<body>
{{topnav("PDM")|safe}}
<main>
  <div class="card">
    <div class="ch">⏱ 25/7 PDM — Live Delay Depth</div>
    <div class="cb">
      <div class="pills" id="pills"></div>
    </div>
  </div>
</main>
<script nonce="{{csp_nonce()}}">
var _STALE=15;
function poll(){
  fetch('/api/pdm/hub_status',{credentials:'same-origin'})
  .then(function(r){return r.json();})
  .then(function(d){
    var wrap=document.getElementById('pills');
    var sites=Object.keys(d).sort();
    if(!sites.length){
      wrap.innerHTML='<div class="no-sites">No client sites reporting PDM data yet.</div>';
      return;
    }
    var now=Math.floor(Date.now()/1000);
    var html='';
    sites.forEach(function(site){
      var s=d[site];
      var stale=(now-s.ts)>_STALE;
      var bcls=s.connected&&!stale?'b-ok':stale?'b-wn':'b-al';
      var btxt=stale?'Stale':s.connected?'Connected':'Disconnected';
      var depH,frH='',erH='';
      if(s.depth!==null&&s.depth!==undefined&&!stale){
        depH='<div class="pill-depth">'+s.depth.toFixed(1)+'</div>';
        frH='<div class="pill-frames">≈ <span>'+(s.depth/40).toFixed(2)+'</span> f@25 &nbsp;/ '
           +'<span>'+(s.depth/33.333).toFixed(2)+'</span> f@30</div>';
      }else{
        depH='<div class="pill-depth na">—</div>';
      }
      if(s.error&&!s.connected){
        erH='<div class="pill-err">'+s.error.replace(/[<>&]/g,function(c){return{'<':'&lt;','>':'&gt;','&':'&amp;'}[c]||c;})+'</div>';
      }
      html+='<div class="pill">'
        +'<div class="pill-site"><span class="badge '+bcls+'">'+btxt+'</span>'
        +site.replace(/[<>&]/g,function(c){return{'<':'&lt;','>':'&gt;','&':'&amp;'}[c]||c;})+'</div>'
        +depH+'<div class="pill-unit">milliseconds</div>'+frH+erH+'</div>';
    });
    wrap.innerHTML=html;
  }).catch(function(){});
}
setInterval(poll,2000);poll();
</script>
</body></html>"""

# ── Plugin registration ───────────────────────────────────────────────────────

def register(app, ctx):
    global _stop_evt

    login_required = ctx["login_required"]
    csrf_protect   = ctx["csrf_protect"]
    monitor        = ctx["monitor"]

    cfg_ss  = monitor.app_cfg
    mode    = getattr(getattr(cfg_ss, "hub", None), "mode", "standalone") or "standalone"
    hub_url = (getattr(getattr(cfg_ss, "hub", None), "hub_url", "") or "").rstrip("/")
    secret  = getattr(getattr(cfg_ss, "hub", None), "secret_key", "") or ""

    is_hub    = mode in ("hub", "both")
    is_client = mode == "client" and bool(hub_url)

    _stop_evt.clear()

    # Client: poll PDM device
    if not is_hub or mode == "both":
        t_poll = threading.Thread(target=_poll_loop, daemon=True, name="PDM-poll")
        t_poll.start()

    # Client: push to hub
    if is_client:
        t_push = threading.Thread(target=_push_loop, args=(monitor,), daemon=True, name="PDM-push")
        t_push.start()

    from flask import jsonify, redirect, render_template_string, request

    # /pdm — client config page, or redirect to hub overview
    @app.get("/pdm")
    @login_required
    def pdm_page():
        if is_hub:
            return redirect("/hub/pdm")
        c = _load_cfg()
        return render_template_string(_CLIENT_TPL, host=c.get("host",""), port=c.get("port",5443))

    # Client: live local status
    @app.get("/api/pdm/status")
    @login_required
    def pdm_status():
        with _state_lock:
            return jsonify(dict(_state))

    # Client: save IP/port config
    @app.post("/api/pdm/config")
    @login_required
    @csrf_protect
    def pdm_config_save():
        data = request.get_json(force=True) or {}
        host = str(data.get("host", "")).strip()
        port = int(data.get("port", 5443))
        if not (1 <= port <= 65535):
            return jsonify({"ok": False, "error": "invalid port"}), 400
        if not host:
            return jsonify({"ok": False, "error": "host required"}), 400
        _save_cfg({"host": host, "port": port})
        with _state_lock:
            _state["host"] = host
            _state["port"] = port
        _reload_evt.set()
        return jsonify({"ok": True})

    # Hub: overview page
    if is_hub:
        @app.get("/hub/pdm")
        @login_required
        def pdm_hub_page():
            return render_template_string(_HUB_TPL)

        # Hub: receive depth reports from client nodes (no @login_required — uses HMAC)
        @app.post("/api/pdm/report")
        def pdm_report():
            body   = request.get_data()
            ts_str = request.headers.get("X-Hub-Ts", "")
            sig    = request.headers.get("X-Hub-Sig", "")
            site   = request.headers.get("X-Site", "").strip()
            if not site:
                return jsonify({"ok": False, "error": "missing site"}), 400
            if not _verify_sig(secret, body, ts_str, sig):
                return jsonify({"ok": False, "error": "invalid signature"}), 403
            try:
                data = json.loads(body)
            except Exception:
                return jsonify({"ok": False, "error": "invalid JSON"}), 400
            with _hub_lock:
                _hub_sites[site] = {
                    "depth":     data.get("depth"),
                    "connected": bool(data.get("connected")),
                    "host":      str(data.get("host", "")),
                    "port":      int(data.get("port", 5443)),
                    "error":     str(data.get("error", "")),
                    "ts":        time.time(),
                }
            return jsonify({"ok": True})

        # Hub: all site snapshots for the hub page JS
        @app.get("/api/pdm/hub_status")
        @login_required
        def pdm_hub_status():
            with _hub_lock:
                return jsonify(dict(_hub_sites))
