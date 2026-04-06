"""
pluginmgr.py  —  Remote Plugin Manager

Hub-side page showing every connected site's installed plugins in a single
matrix.  Lets you install, update, and remove plugins on the hub and on any
approved client site from one screen.

How it works
────────────
• Hub: serves the page and all API endpoints.  Hub plugins are managed
  directly (file I/O).
• Clients: a background thread reports installed plugins to the hub every
  60 s via POST /api/pluginmgr/report and receives any pending command in
  the response.  Commands are also delivered via the heartbeat ACK system
  (cores 3.5.84+) so they arrive within one heartbeat cycle even if the
  client hasn't polled yet.
• Bootstrap: the plugin must be installed on each client before that client
  can report its plugin list.  Use the existing Settings → Plugins page on
  the client's own web UI to bootstrap, then manage remotely from here.
"""

SIGNALSCOPE_PLUGIN = {
    "id":       "pluginmgr",
    "label":    "Plugin Manager",
    "url":      "/hub/pluginmgr",
    "icon":     "🧩",
    "hub_only": True,
    "version":  "1.0.0",
}

import os
import json
import re
import time
import threading
import urllib.request
import urllib.error

_REGISTRY_URL  = "https://raw.githubusercontent.com/itconor/SignalScope/main/plugins.json"
_POLL_INTERVAL = 60   # seconds between client plugin-list reports
_STALE_SECS    = 150  # client considered offline after this many seconds

# module state (set in register)
_PLUGIN_DIR  = None
_state_lock  = threading.Lock()
_client_data = {}   # site → {plugins, last_seen, cmd_result}
_pending_cmds = {}  # site → cmd dict (action, file, url)


# ── plugin scanner ─────────────────────────────────────────────────────────────

def _scan_plugins(plugin_dir):
    """Return list of dicts {file, id, label, version, icon} for every
    SIGNALSCOPE_PLUGIN file in plugin_dir.  Reads only the first 8 KB of each
    file — never imports — so safe for files that may fail to import."""
    result = []
    try:
        for fn in sorted(os.listdir(plugin_dir)):
            if not fn.endswith(".py"):
                continue
            path = os.path.join(plugin_dir, fn)
            info = {"file": fn, "id": None, "label": None, "version": None, "icon": "🔌"}
            try:
                with open(path, "r", errors="replace") as fh:
                    head = fh.read(8192)
                m = re.search(r'SIGNALSCOPE_PLUGIN\s*=\s*\{([^}]+)\}', head, re.DOTALL)
                if not m:
                    continue
                block = m.group(1)
                for key in ("id", "label", "version", "icon"):
                    km = re.search(r'["\']' + key + r'["\']\s*:\s*["\']([^"\']+)["\']', block)
                    if km:
                        info[key] = km.group(1)
            except Exception:
                continue
            if info.get("id"):
                result.append(info)
    except Exception:
        pass
    return result


# ── registry fetch ─────────────────────────────────────────────────────────────

def _fetch_registry():
    """Fetch plugins.json from GitHub.  Returns list or None on failure."""
    try:
        req  = urllib.request.Request(
            _REGISTRY_URL,
            headers={"User-Agent": "SignalScope-PluginMgr/1.0"},
        )
        resp = urllib.request.urlopen(req, timeout=12)
        return json.loads(resp.read())
    except Exception:
        return None


# ── command execution (client side) ───────────────────────────────────────────

def _execute_cmd(cmd, plugin_dir, hub_url, site, monitor):
    action    = cmd.get("action", "")
    file_name = cmd.get("file", "")
    url       = cmd.get("url", "")
    result    = {"action": action, "file": file_name, "ok": False, "msg": ""}

    if action in ("install", "update") and file_name and url:
        try:
            resp = urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "SignalScope-PluginMgr/1.0"}),
                timeout=30,
            )
            data = resp.read()
            path = os.path.join(plugin_dir, file_name)
            tmp  = path + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
            result["ok"]  = True
            result["msg"] = f"Installed {file_name} — restart SignalScope to activate"
            monitor.log(f"[PluginMgr] {site}: installed {file_name}")
        except Exception as exc:
            result["msg"] = str(exc)
            monitor.log(f"[PluginMgr] {site}: install {file_name} failed — {exc}")

    elif action == "remove" and file_name:
        path = os.path.join(plugin_dir, file_name)
        try:
            if os.path.exists(path):
                os.remove(path)
                result["ok"]  = True
                result["msg"] = f"Removed {file_name} — restart SignalScope to complete"
                monitor.log(f"[PluginMgr] {site}: removed {file_name}")
            else:
                result["msg"] = "File not found"
        except Exception as exc:
            result["msg"] = str(exc)
            monitor.log(f"[PluginMgr] {site}: remove {file_name} failed — {exc}")

    else:
        result["msg"] = f"Unknown action: {action}"

    # Report result back to hub
    try:
        req = urllib.request.Request(
            f"{hub_url}/api/pluginmgr/result",
            data=json.dumps({"result": result}).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "X-Site": site},
        )
        urllib.request.urlopen(req, timeout=10).close()
    except Exception:
        pass


# ── register ───────────────────────────────────────────────────────────────────

def register(app, ctx):
    global _PLUGIN_DIR

    login_required = ctx["login_required"]
    csrf_protect   = ctx["csrf_protect"]
    monitor        = ctx["monitor"]
    hub_server     = ctx["hub_server"]
    BUILD          = ctx["BUILD"]

    _PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))

    cfg_ss  = monitor.app_cfg
    mode    = getattr(getattr(cfg_ss, "hub", None), "mode", "standalone") or "standalone"
    hub_url = (getattr(getattr(cfg_ss, "hub", None), "hub_url", "") or "").rstrip("/")

    is_hub    = mode in ("hub", "both", "standalone")
    is_client = mode == "client" and bool(hub_url)

    # ── Client node: report plugins + poll commands ───────────────────────────
    if is_client:
        register_cmd_handler = ctx.get("register_cmd_handler")
        if register_cmd_handler:
            def _on_pluginmgr_cmd(payload):
                cfg2   = monitor.app_cfg
                h_url  = (getattr(getattr(cfg2, "hub", None), "hub_url", "") or "").rstrip("/")
                site2  = getattr(getattr(cfg2, "hub", None), "site_name", "") or ""
                threading.Thread(
                    target=_execute_cmd,
                    args=(payload, _PLUGIN_DIR, h_url, site2, monitor),
                    daemon=True, name="PluginMgrCmd",
                ).start()
            register_cmd_handler("pluginmgr_cmd", _on_pluginmgr_cmd)

        def _client_loop():
            last_err = 0.0
            while True:
                try:
                    cfg2   = monitor.app_cfg
                    h_url  = (getattr(getattr(cfg2, "hub", None), "hub_url", "") or "").rstrip("/")
                    site2  = getattr(getattr(cfg2, "hub", None), "site_name", "") or ""
                    if not h_url or not site2:
                        time.sleep(_POLL_INTERVAL)
                        continue
                    plugins = _scan_plugins(_PLUGIN_DIR)
                    req = urllib.request.Request(
                        f"{h_url}/api/pluginmgr/report",
                        data=json.dumps({"plugins": plugins}).encode(),
                        method="POST",
                        headers={"Content-Type": "application/json", "X-Site": site2},
                    )
                    resp = urllib.request.urlopen(req, timeout=10)
                    data = json.loads(resp.read())
                    cmd  = data.get("cmd")
                    if cmd:
                        threading.Thread(
                            target=_execute_cmd,
                            args=(cmd, _PLUGIN_DIR, h_url, site2, monitor),
                            daemon=True, name="PluginMgrCmd",
                        ).start()
                except Exception as exc:
                    now = time.time()
                    if now - last_err > 120:
                        monitor.log(f"[PluginMgr] Client report error: {exc}")
                        last_err = now
                time.sleep(_POLL_INTERVAL)

        threading.Thread(target=_client_loop, daemon=True, name="PluginMgrClient").start()
        monitor.log("[PluginMgr] Client reporter started")

    if not is_hub:
        return

    # ── Hub routes ─────────────────────────────────────────────────────────────

    @app.get("/hub/pluginmgr")
    @login_required
    def pluginmgr_page():
        return _render_page(BUILD)

    @app.get("/api/pluginmgr/data")
    @login_required
    def pluginmgr_data():
        from flask import jsonify
        hub_plugins = _scan_plugins(_PLUGIN_DIR)
        with _state_lock:
            clients  = {k: dict(v) for k, v in _client_data.items()}
            pending  = dict(_pending_cmds)
        return jsonify({
            "hub":     {"plugins": hub_plugins, "last_seen": time.time()},
            "clients": clients,
            "pending": pending,
            "mode":    mode,
        })

    @app.get("/api/pluginmgr/registry")
    @login_required
    def pluginmgr_registry():
        from flask import jsonify
        reg = _fetch_registry()
        if reg is None:
            return jsonify({"error": "Could not fetch registry from GitHub"}), 502
        return jsonify(reg)

    @app.post("/api/pluginmgr/report")
    def pluginmgr_report():
        """Clients POST their plugin list here and receive any pending cmd."""
        from flask import request, jsonify
        site_hdr = request.headers.get("X-Site", "").strip()
        if not site_hdr:
            return jsonify({"error": "Missing X-Site"}), 400
        sdata = hub_server._sites.get(site_hdr, {})
        if not sdata.get("_approved"):
            return jsonify({"error": "Not approved"}), 403
        body    = request.get_json(silent=True) or {}
        plugins = body.get("plugins", [])
        with _state_lock:
            prev = _client_data.get(site_hdr, {})
            _client_data[site_hdr] = {
                "plugins":    plugins,
                "last_seen":  time.time(),
                "cmd_result": prev.get("cmd_result"),
            }
            cmd = _pending_cmds.pop(site_hdr, None)
        return jsonify({"cmd": cmd} if cmd else {})

    @app.post("/api/pluginmgr/result")
    def pluginmgr_result():
        """Clients POST command results here."""
        from flask import request, jsonify
        site_hdr = request.headers.get("X-Site", "").strip()
        if not site_hdr:
            return jsonify({"error": "Missing X-Site"}), 400
        body   = request.get_json(silent=True) or {}
        result = body.get("result", {})
        with _state_lock:
            if site_hdr in _client_data:
                _client_data[site_hdr]["cmd_result"] = result
        status = "OK" if result.get("ok") else result.get("msg", "error")
        monitor.log(f"[PluginMgr] {site_hdr}: {result.get('action')} {result.get('file')} → {status}")
        return jsonify({"ok": True})

    @app.post("/api/pluginmgr/cmd/<site>")
    @login_required
    @csrf_protect
    def pluginmgr_queue_cmd(site):
        """Queue an install/update/remove command for a client site."""
        from flask import request, jsonify
        body      = request.get_json(silent=True) or {}
        action    = body.get("action", "").strip()
        file_name = body.get("file", "").strip()
        url       = body.get("url", "").strip()
        if action not in ("install", "update", "remove"):
            return jsonify({"error": "Invalid action"}), 400
        if not file_name:
            return jsonify({"error": "Missing file"}), 400
        if action in ("install", "update") and not url:
            return jsonify({"error": "Missing url"}), 400
        cmd = {"action": action, "file": file_name, "url": url}
        # Deliver via heartbeat ACK (fast path, cores ≥ 3.5.84)
        hub_server.push_pending_command(site, {"type": "pluginmgr_cmd", "payload": cmd})
        # Also store for report-poll fallback
        with _state_lock:
            _pending_cmds[site] = cmd
            if site in _client_data:
                _client_data[site]["cmd_result"] = None
        return jsonify({"ok": True, "msg": f"Command queued for {site}"})

    @app.post("/api/pluginmgr/hub_action")
    @login_required
    @csrf_protect
    def pluginmgr_hub_action():
        """Install or remove a plugin on the hub itself (direct file I/O)."""
        from flask import request, jsonify
        body      = request.get_json(silent=True) or {}
        action    = body.get("action", "").strip()
        file_name = body.get("file", "").strip()
        url       = body.get("url", "").strip()

        if not file_name or not re.match(r'^[\w\-]+\.py$', file_name):
            return jsonify({"error": "Invalid file name"}), 400

        if action in ("install", "update"):
            if not url:
                return jsonify({"error": "Missing url"}), 400
            try:
                resp = urllib.request.urlopen(
                    urllib.request.Request(url, headers={"User-Agent": "SignalScope-PluginMgr/1.0"}),
                    timeout=30,
                )
                data = resp.read()
                path = os.path.join(_PLUGIN_DIR, file_name)
                tmp  = path + ".tmp"
                with open(tmp, "wb") as fh:
                    fh.write(data)
                os.replace(tmp, path)
                monitor.log(f"[PluginMgr] Hub: installed {file_name}")
                return jsonify({"ok": True, "msg": f"{file_name} installed — restart SignalScope to activate"})
            except Exception as exc:
                return jsonify({"error": str(exc)}), 500

        elif action == "remove":
            path = os.path.join(_PLUGIN_DIR, file_name)
            if not os.path.exists(path):
                return jsonify({"error": "File not found"}), 404
            try:
                os.remove(path)
                monitor.log(f"[PluginMgr] Hub: removed {file_name}")
                return jsonify({"ok": True, "msg": f"{file_name} removed — restart SignalScope to complete"})
            except Exception as exc:
                return jsonify({"error": str(exc)}), 500

        return jsonify({"error": "Invalid action"}), 400


# ── page ───────────────────────────────────────────────────────────────────────

def _render_page(BUILD):
    from flask import render_template_string
    return render_template_string(_PAGE_TPL, BUILD=BUILD)


_PAGE_TPL = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<title>Plugin Manager — {{BUILD}}</title>
<style nonce="{{csp_nonce()}}">
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(circle at top,#12376f 0%,var(--bg) 38%,#05101f 100%);color:var(--tx);font-family:system-ui,sans-serif;font-size:13px;min-height:100vh}
header{background:linear-gradient(180deg,rgba(10,31,65,.96),rgba(9,24,48,.96));border-bottom:1px solid var(--bor);padding:12px 20px;display:flex;align-items:center;gap:12px}
header a{color:var(--acc);text-decoration:none;font-size:12px}
h1{font-size:15px;font-weight:700}
.wrap{max-width:1400px;margin:0 auto;padding:20px}
.card{background:var(--sur);border:1px solid var(--bor);border-radius:12px;overflow:hidden;margin-bottom:20px}
.ch{padding:9px 14px;display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--bor);background:linear-gradient(180deg,#143766,#102b54);font-size:12px;font-weight:700;color:var(--acc);text-transform:uppercase;letter-spacing:.06em;flex-wrap:wrap}
.cb{padding:16px}
.btn{border:none;border-radius:8px;padding:5px 12px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit}
.btn:hover{filter:brightness(1.15)}
.btn:disabled{opacity:.4;cursor:not-allowed}
.bp{background:var(--acc);color:#07142b}.bd{background:var(--al);color:#fff}
.bg{background:#142242;color:var(--tx)}.bw{background:#1a3a20;color:var(--ok)}
.bs{padding:3px 9px;font-size:11px}
.badge{font-size:10px;border-radius:4px;padding:2px 7px;font-weight:700}
.b-ok{background:rgba(34,197,94,.15);color:var(--ok)}
.b-wn{background:rgba(245,158,11,.15);color:var(--wn)}
.b-al{background:rgba(239,68,68,.15);color:var(--al)}
.b-mu{background:rgba(138,164,200,.12);color:var(--mu)}
.b-acc{background:rgba(23,168,255,.12);color:var(--acc)}
/* matrix table */
.matrix-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;min-width:600px}
th{color:var(--mu);font-size:11px;text-transform:uppercase;letter-spacing:.04em;padding:8px 12px;text-align:left;border-bottom:1px solid var(--bor);background:#08172e;white-space:nowrap}
th.site-col{text-align:center;min-width:160px}
td{padding:9px 12px;border-bottom:1px solid rgba(23,52,95,.35);vertical-align:top}
tr:last-child td{border:none}
tr:hover td{background:rgba(23,52,95,.2)}
.plugin-name{font-weight:600;font-size:13px}
.plugin-icon{margin-right:5px}
.plugin-id{font-size:10px;color:var(--mu);margin-top:1px}
.cell{display:flex;flex-direction:column;align-items:center;gap:5px}
.cell-ver{font-size:12px;font-weight:600;color:var(--tx)}
.cell-na{font-size:12px;color:rgba(138,164,200,.4)}
.cell-acts{display:flex;gap:4px;flex-wrap:wrap;justify-content:center}
.site-hdr{text-align:center}
.site-hdr-name{font-size:12px;font-weight:700;color:var(--tx)}
.site-hdr-status{font-size:10px;margin-top:2px}
/* registry card */
.reg-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px;padding:16px}
.reg-card{background:#08172e;border:1px solid var(--bor);border-radius:8px;padding:12px}
.reg-card-name{font-size:13px;font-weight:700;margin-bottom:3px}
.reg-card-desc{font-size:11px;color:var(--mu);margin-bottom:10px;line-height:1.4}
.reg-card-ver{font-size:10px;color:var(--mu);margin-bottom:8px}
.reg-card-acts{display:flex;flex-wrap:wrap;gap:5px}
/* notice banner */
.restart-banner{background:#1a1200;border:1px solid rgba(245,158,11,.4);border-radius:6px;color:var(--wn);font-size:12px;padding:8px 14px;margin-bottom:16px;display:none}
#msg{display:none;padding:8px 12px;border-radius:6px;margin-bottom:14px;font-size:12px}
.msg-ok{background:#0f2318;color:var(--ok);border:1px solid #166534}
.msg-err{background:#2a0a0a;color:var(--al);border:1px solid #991b1b}
.shimmer{position:relative;overflow:hidden}
.shimmer::after{content:'';position:absolute;inset:0;background:linear-gradient(90deg,transparent,rgba(255,255,255,.08),transparent);background-size:200% 100%;animation:shim 1.2s infinite linear}
@keyframes shim{0%{background-position:200% 0}100%{background-position:-200% 0}}
.stale{opacity:.5}
</style>
</head>
<body>
<header>
  <a href="/hub">← Hub</a>
  <h1>🧩 Plugin Manager</h1>
  <span style="margin-left:auto;font-size:11px;color:var(--mu)">{{BUILD}}</span>
</header>

<div class="wrap">
  <div id="msg"></div>
  <div class="restart-banner" id="restart-banner">⚠ Restart SignalScope on the affected node(s) to activate plugin changes.</div>

  <!-- Registry -->
  <div class="card">
    <div class="ch">
      Registry — Available Plugins
      <button class="btn bg bs" id="btn-registry" style="margin-left:auto">⬇ Check GitHub</button>
    </div>
    <div id="reg-body" style="color:var(--mu);font-size:12px;padding:16px">Click "Check GitHub" to load available plugins.</div>
  </div>

  <!-- Matrix -->
  <div class="card">
    <div class="ch">
      Installed Plugins — All Sites
      <button class="btn bg bs" id="btn-refresh" style="margin-left:auto">↻ Refresh</button>
      <span id="last-refresh" style="font-size:10px;color:var(--mu);font-weight:400"></span>
    </div>
    <div class="matrix-wrap">
      <div id="matrix-body" style="color:var(--mu);font-size:12px;padding:16px">Loading…</div>
    </div>
  </div>
</div>

<script nonce="{{csp_nonce()}}">
(function(){
'use strict';

var _data     = null;   // last /api/pluginmgr/data response
var _registry = null;   // last /api/pluginmgr/registry response
var _restartNeeded = false;

function _csrf(){
  return (document.querySelector('meta[name="csrf-token"]')||{}).content
      || (document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/)||[])[1] || '';
}
function showMsg(txt, ok){
  var el=document.getElementById('msg');
  el.textContent=txt; el.style.display='block';
  el.className=ok?'msg-ok':'msg-err';
  if(ok) setTimeout(function(){el.style.display='none'}, 5000);
}
function esc(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

function _versionGt(a, b){
  // returns true if a > b (semver-ish)
  if(!a||!b) return false;
  var ap=String(a).split('.').map(Number), bp=String(b).split('.').map(Number);
  for(var i=0;i<Math.max(ap.length,bp.length);i++){
    var d=(ap[i]||0)-(bp[i]||0);
    if(d>0) return true;
    if(d<0) return false;
  }
  return false;
}

// ── load data ─────────────────────────────────────────────────────────────────
function loadData(){
  fetch('/api/pluginmgr/data',{credentials:'same-origin'})
  .then(function(r){return r.json();})
  .then(function(d){
    _data=d;
    document.getElementById('last-refresh').textContent='Updated '+new Date().toLocaleTimeString();
    renderMatrix();
  }).catch(function(e){
    document.getElementById('matrix-body').innerHTML='<div style="color:var(--al);padding:8px">Failed to load: '+esc(String(e))+'</div>';
  });
}

function loadRegistry(){
  var btn=document.getElementById('btn-registry');
  btn.disabled=true; btn.classList.add('shimmer'); btn.textContent='Fetching…';
  fetch('/api/pluginmgr/registry',{credentials:'same-origin'})
  .then(function(r){return r.json();})
  .then(function(d){
    btn.disabled=false; btn.classList.remove('shimmer'); btn.textContent='⬇ Check GitHub';
    if(d.error){showMsg(d.error,false);return;}
    _registry=d;
    renderRegistry();
    renderMatrix();  // re-render matrix with update badges
  }).catch(function(e){
    btn.disabled=false; btn.classList.remove('shimmer'); btn.textContent='⬇ Check GitHub';
    showMsg('Failed to fetch registry: '+String(e),false);
  });
}

document.getElementById('btn-refresh').addEventListener('click', loadData);
document.getElementById('btn-registry').addEventListener('click', loadRegistry);

// ── registry panel ────────────────────────────────────────────────────────────
function renderRegistry(){
  if(!_registry){return;}
  var reg=document.getElementById('reg-body');
  if(!_registry.length){reg.innerHTML='<div style="padding:16px;color:var(--mu)">No plugins in registry.</div>';return;}
  var html='<div class="reg-grid">';
  _registry.forEach(function(p){
    var reqs=p.requirements?'<div style="font-size:10px;color:var(--mu);margin-bottom:6px">Requires: '+esc(p.requirements)+'</div>':'';
    html+='<div class="reg-card">'
      +'<div class="reg-card-name">'+(p.icon||'🔌')+' '+esc(p.name||p.id)+'</div>'
      +'<div class="reg-card-desc">'+esc((p.description||'').substring(0,120))+'</div>'
      +'<div class="reg-card-ver">v'+esc(p.version||'?')+'</div>'
      +reqs
      +'<div class="reg-card-acts" id="ra_'+esc(p.id)+'">'
      +_regCardButtons(p)
      +'</div>'
      +'</div>';
  });
  html+='</div>';
  reg.innerHTML=html;
}

function _regCardButtons(p){
  if(!_data) return '<span style="font-size:11px;color:var(--mu)">Load matrix first</span>';
  // Sites where this plugin is NOT installed
  var sites=_allSites();
  var btns='';
  sites.forEach(function(s){
    var isHub=s==='(hub)';
    var installed=_getInstalled(s,p.id);
    if(!installed){
      btns+='<button class="btn bg bs" onclick="doAction('+JSON.stringify(s)+','+JSON.stringify('install')+','+JSON.stringify(p.file||'')+','+JSON.stringify(p.url||'')+')">⬇ '+esc(s)+'</button>';
    } else if(_versionGt(p.version||'', installed.version||'')){
      btns+='<button class="btn bw bs" onclick="doAction('+JSON.stringify(s)+','+JSON.stringify('update')+','+JSON.stringify(p.file||'')+','+JSON.stringify(p.url||'')+')">↑ '+esc(s)+'</button>';
    } else {
      btns+='<span class="badge b-ok" style="font-size:10px">✓ '+esc(s)+'</span>';
    }
  });
  return btns||'<span style="font-size:11px;color:var(--mu)">No sites connected</span>';
}

function _allSites(){
  if(!_data) return [];
  var sites=['(hub)'];
  Object.keys(_data.clients||{}).sort().forEach(function(s){ sites.push(s); });
  return sites;
}

function _getInstalled(site, pluginId){
  if(!_data) return null;
  var plugins;
  if(site==='(hub)') plugins=(_data.hub||{}).plugins||[];
  else plugins=((_data.clients||{})[site]||{}).plugins||[];
  return plugins.find(function(p){return p.id===pluginId;})||null;
}

// ── matrix ────────────────────────────────────────────────────────────────────
function renderMatrix(){
  if(!_data){return;}
  var sites=_allSites();

  // Collect all known plugin ids across all sites
  var pluginMap={};  // id → {id, label, icon, file (registry), registry_version, registry_url}

  // From registry
  (_registry||[]).forEach(function(p){
    pluginMap[p.id]={id:p.id, label:p.name||p.id, icon:p.icon||'🔌', file:p.file||p.id+'.py',
                     registry_version:p.version||null, registry_url:p.url||null};
  });
  // From hub
  ((_data.hub||{}).plugins||[]).forEach(function(p){
    if(!p.id) return;
    if(!pluginMap[p.id]) pluginMap[p.id]={id:p.id, label:p.label||p.id, icon:p.icon||'🔌', file:p.file||p.id+'.py',
                                           registry_version:null, registry_url:null};
  });
  // From clients
  Object.values(_data.clients||{}).forEach(function(cd){
    (cd.plugins||[]).forEach(function(p){
      if(!p.id) return;
      if(!pluginMap[p.id]) pluginMap[p.id]={id:p.id, label:p.label||p.id, icon:p.icon||'🔌', file:p.file||p.id+'.py',
                                             registry_version:null, registry_url:null};
    });
  });

  var plugins=Object.values(pluginMap).sort(function(a,b){return a.label.localeCompare(b.label);});
  if(!plugins.length){
    document.getElementById('matrix-body').innerHTML='<div style="padding:16px;color:var(--mu)">No plugins found.</div>';
    return;
  }

  var html='<table><thead><tr><th>Plugin</th><th style="white-space:nowrap">Registry</th>';
  sites.forEach(function(s){
    var isHub=s==='(hub)';
    var cd=_data.clients[s]||{};
    var stale=!isHub&&(Date.now()/1000-cd.last_seen>150);
    var onlineHtml=isHub
      ?'<span class="badge b-ok">Hub</span>'
      :(stale?'<span class="badge b-wn">Stale</span>':'<span class="badge b-ok">Online</span>');
    var pend=(_data.pending||{})[s];
    var pendHtml=pend?'<span class="badge b-acc" style="margin-left:4px">⏳ '+esc(pend.action)+'</span>':'';
    var res=cd.cmd_result;
    var resHtml='';
    if(res){resHtml='<div style="font-size:10px;margin-top:3px;color:'+(res.ok?'var(--ok)':'var(--al)')+'">'+esc((res.ok?'✓ ':'✗ ')+(res.msg||''))+'</div>';}
    html+='<th class="site-col"><div class="site-hdr"><div class="site-hdr-name">'+esc(s)+'</div><div class="site-hdr-status">'+onlineHtml+pendHtml+resHtml+'</div></div></th>';
  });
  html+='</tr></thead><tbody>';

  plugins.forEach(function(plugin){
    var rv=plugin.registry_version;
    var rvHtml=rv?'<span class="badge b-mu">v'+esc(rv)+'</span>':'<span style="color:rgba(138,164,200,.4);font-size:11px">—</span>';
    html+='<tr>'
      +'<td><div class="plugin-name"><span class="plugin-icon">'+esc(plugin.icon||'🔌')+'</span>'+esc(plugin.label)+'</div>'
      +'<div class="plugin-id">'+esc(plugin.id)+'</div></td>'
      +'<td>'+rvHtml+'</td>';

    sites.forEach(function(s){
      var isHub=s==='(hub)';
      var installed=_getInstalled(s, plugin.id);
      var pend=(_data.pending||{})[s];
      var busy=pend&&pend.file===(plugin.file);
      var cell='<div class="cell'+(busy?' shimmer':')+'">';

      if(installed){
        var updateAvail=rv&&_versionGt(rv, installed.version||'');
        var vBadgeCls=updateAvail?'badge b-wn':'badge b-ok';
        cell+='<span class="'+vBadgeCls+'">v'+esc(installed.version||'?')+'</span>';
        if(updateAvail) cell+='<span style="font-size:10px;color:var(--wn)">v'+esc(rv)+' available</span>';
        cell+='<div class="cell-acts">';
        if(updateAvail&&plugin.registry_url){
          cell+=_actBtn(s, isHub, 'update', plugin.file, plugin.registry_url, '↑ Update', 'bw');
        }
        cell+=_actBtn(s, isHub, 'remove', plugin.file, '', '✕ Remove', 'bd');
        cell+='</div>';
      } else {
        cell+='<span class="cell-na">—</span>';
        if(plugin.registry_url){
          cell+='<div class="cell-acts">'+_actBtn(s, isHub, 'install', plugin.file, plugin.registry_url, '⬇ Install', 'bg')+'</div>';
        }
      }
      cell+='</div>';
      html+='<td>'+cell+'</td>';
    });
    html+='</tr>';
  });

  html+='</tbody></table>';
  document.getElementById('matrix-body').innerHTML=html;
}

function _actBtn(site, isHub, action, file, url, label, cls){
  var data=JSON.stringify({site:site,isHub:isHub,action:action,file:file,url:url});
  return '<button class="btn '+cls+' bs" data-action="'+esc(data)+'">'+esc(label)+'</button>';
}

// ── action delegation ─────────────────────────────────────────────────────────
document.addEventListener('click', function(e){
  var btn=e.target.closest('[data-action]');
  if(!btn) return;
  var d=JSON.parse(btn.dataset.action||'{}');
  if(!d.action) return;
  doAction(d.site, d.action, d.file, d.url);
});

function doAction(site, action, file, url){
  var label={'install':'Install','update':'Update','remove':'Remove'}[action]||action;
  if(action==='remove'&&!confirm('Remove '+file+' from '+site+'?')) return;
  var isHub=site==='(hub)';
  var endpoint=isHub?'/api/pluginmgr/hub_action':'/api/pluginmgr/cmd/'+encodeURIComponent(site);
  var body=isHub?{action:action,file:file,url:url}:{action:action,file:file,url:url};
  fetch(endpoint,{
    method:'POST', credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRFToken':_csrf()},
    body:JSON.stringify(body)
  })
  .then(function(r){return r.json();})
  .then(function(d){
    if(d.error){showMsg(d.error,false);return;}
    showMsg(d.msg||'Done',true);
    _restartNeeded=true;
    document.getElementById('restart-banner').style.display='';
    // Refresh after short delay so matrix updates
    setTimeout(loadData, isHub?500:2000);
  })
  .catch(function(e){showMsg('Request failed: '+e,false);});
}

// ── init ──────────────────────────────────────────────────────────────────────
loadData();
setInterval(loadData, 30000);

})();
</script>
</body>
</html>
"""
