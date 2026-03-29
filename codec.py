# codec.py — Tieline / Comrex / Prodys Quantum ST / APT WorldCast codec monitor
# Drop alongside signalscope.py.  Hub-only plugin.

SIGNALSCOPE_PLUGIN = {
    "id":       "codec",
    "label":    "Codecs",
    "url":      "/hub/codecs",
    "icon":     "📡",
    "hub_only": True,
    "version":  "1.0.1",
}

import base64
import json
import os
import re
import socket
import sys
import threading
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path

# ── Module-level state ──────────────────────────────────────────────────────
_log      = None   # callable — set in register()
_cfg_file = None   # Path to codec_devices.json
_lock     = threading.Lock()
_devices  = []     # list[dict] — loaded from _cfg_file
_status   = {}     # device_id → status dict
_alert_fn = None   # _alert_log_append from signalscope module

# ── Device type registry ────────────────────────────────────────────────────
_DTYPES = {
    "comrex":     {"label": "Comrex",                  "port": 80,  "method": "http"},
    "tieline":    {"label": "Tieline",                 "port": 80,  "method": "http"},
    "quantum_st": {"label": "Prodys Quantum ST",       "port": 161, "method": "snmp"},
    "apt":        {"label": "APT / WorldCast Quantum", "port": 161, "method": "snmp"},
    "custom":     {"label": "Custom",                  "port": 80,  "method": "http"},
    "tcp":        {"label": "TCP Ping Only",           "port": 80,  "method": "tcp"},
}

_STATE_COLOUR = {
    "connected":    "#22c55e",
    "idle":         "#17a8ff",
    "disconnected": "#f59e0b",
    "offline":      "#ef4444",
    "error":        "#ef4444",
    "unknown":      "#6b7280",
}
_STATE_LABEL = {
    "connected":    "Connected",
    "idle":         "Idle / Ready",
    "disconnected": "Disconnected",
    "offline":      "Offline",
    "error":        "Error",
    "unknown":      "Unknown",
}

# ── Config persistence ──────────────────────────────────────────────────────
def _load():
    global _devices
    try:
        _devices = json.loads(_cfg_file.read_text()) if _cfg_file and _cfg_file.exists() else []
    except Exception:
        _devices = []

def _save():
    try:
        _cfg_file.write_text(json.dumps(_devices, indent=2))
    except Exception as e:
        _log(f"[Codec] Save error: {e}")

# ── Connectivity checks ─────────────────────────────────────────────────────
def _tcp_ok(host, port, timeout=5):
    try:
        with socket.create_connection((host, int(port)), timeout=float(timeout)):
            return True
    except Exception:
        return False

def _snmp_get(host, community, oid, port=161, timeout=5):
    """
    Minimal SNMP GET (v2c) using pysnmp if available.
    Returns (value_str, error_str).
    """
    try:
        from pysnmp.hlapi import (
            getCmd, SnmpEngine, CommunityData, UdpTransportTarget,
            ContextData, ObjectType, ObjectIdentity,
        )
        ei, es, _, vbs = next(getCmd(
            SnmpEngine(),
            CommunityData(community, mpModel=1),
            UdpTransportTarget((host, int(port)), timeout=timeout, retries=1),
            ContextData(),
            ObjectType(ObjectIdentity(oid)),
        ))
        if ei or es:
            return None, str(ei or es)
        for vb in vbs:
            return str(vb[1]), None
        return None, "no response"
    except ImportError:
        return None, "pysnmp_not_installed"
    except Exception as e:
        return None, str(e)[:120]

def _http_check(dev, timeout=8):
    """HTTP GET → (state, detail, remote_name).

    Supports no auth, HTTP Basic, and HTTP Digest auth.  When credentials are
    configured we build an opener with both Basic and Digest handlers so the
    correct scheme is selected automatically after the server's 401 challenge.
    For devices with no auth we still pre-send a Basic header to avoid a
    wasted round-trip.
    """
    host     = dev.get("host", "").strip()
    port     = int(dev.get("port", 80))
    use_ssl  = dev.get("ssl", False)
    endpoint = dev.get("endpoint", "").strip() or _default_ep(dev.get("type", "custom"))
    user     = dev.get("username", "").strip()
    pwd      = dev.get("password", "").strip()
    dtype    = dev.get("type", "custom")

    scheme = "https" if use_ssl else "http"
    url    = f"{scheme}://{host}:{port}{endpoint}"

    try:
        if user:
            # Build an opener that handles both Basic and Digest challenges.
            # urllib will re-send with the correct scheme after the 401.
            mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
            mgr.add_password(None, url, user, pwd)
            opener = urllib.request.build_opener(
                urllib.request.HTTPBasicAuthHandler(mgr),
                urllib.request.HTTPDigestAuthHandler(mgr),
            )
            opener.addheaders = [("User-Agent", "SignalScope-Codec/1.0")]
            with opener.open(url, timeout=timeout) as r:
                body = r.read(131072).decode("utf-8", errors="replace")
        else:
            req = urllib.request.Request(url, headers={"User-Agent": "SignalScope-Codec/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read(131072).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return "error", "Auth failed — check username/password (HTTP 401)", ""
        return "offline", f"HTTP {e.code}", ""
    except Exception as e:
        return "offline", str(e)[:100], ""

    return _parse_body(dtype, body)

def _default_ep(dtype):
    return {"tieline": "/api/v1/status", "comrex": "/", "quantum_st": "/", "apt": "/"}.get(dtype, "/")

def _parse_body(dtype, body):
    """Return (state, detail, remote_name) from HTTP response body."""
    bl = body.lower()

    # ── Try JSON first ──
    try:
        d = json.loads(body)
        if isinstance(d, dict):
            st  = str(d.get("status", d.get("connection_status",
                       d.get("state", d.get("channelState", ""))))) .lower()
            rem = str(d.get("remote", d.get("remote_name",
                       d.get("caller", d.get("peer", "")))))
            if "connect" in st and "disconnect" not in st:
                return "connected", f"Remote: {rem}" if rem else "Connected", rem
            if any(x in st for x in ("idle", "ready", "listen", "available", "standby")):
                return "idle", "Ready — no active connection", ""
            if "disconnect" in st:
                return "disconnected", "Remote disconnected", ""
    except (ValueError, KeyError):
        pass

    # ── XML quick scan (Tieline uses XML in some models) ──
    if "<" in body:
        for tag in ("state", "status", "channelstatus", "linkstate", "connectionstate"):
            m = re.search(rf"<{tag}[^>]*>([^<]+)</{tag}>", body, re.I)
            if m:
                val = m.group(1).strip().lower()
                if "connect" in val and "disconnect" not in val:
                    return "connected", f"XML: {m.group(1).strip()}", ""
                if "disconnect" in val:
                    return "disconnected", f"XML: {m.group(1).strip()}", ""
                if any(x in val for x in ("idle", "ready")):
                    return "idle", f"XML: {m.group(1).strip()}", ""

    # ── HTML keyword scan (Comrex / Tieline web pages) ──
    # Check "not connected" BEFORE "connected" to avoid false positives
    if any(x in bl for x in ("not connected", "disconnected", "no connection",
                              "link down", "no carrier", "no remote", "line idle")):
        return "disconnected", "Not connected", ""

    if "connected" in bl:
        # Try to extract remote name from common HTML patterns
        for pat in (r'remote["\s:>]+([A-Za-z0-9][\w\.\-]{1,40})',
                    r'peer["\s:>]+([A-Za-z0-9][\w\.\-]{1,40})',
                    r'caller["\s:>]+([A-Za-z0-9][\w\.\-]{1,40})'):
            m = re.search(pat, body, re.I)
            if m:
                rem = m.group(1)
                return "connected", f"Remote: {rem}", rem
        return "connected", "Connected", ""

    if any(x in bl for x in ("idle", "ready", "waiting", "listening",
                              "standby", "available", "no connection")):
        return "idle", "Ready — no active connection", ""

    # Device responded but status unclear — it's at least online
    return "idle", "Online (status unclear)", ""

def _snmp_check(dev):
    """SNMP-based check for Prodys Quantum ST / APT WorldCast. Returns (state, detail, remote)."""
    host      = dev.get("host", "").strip()
    port      = int(dev.get("snmp_port", 161))
    community = dev.get("snmp_community", "public").strip() or "public"
    oid       = dev.get("snmp_oid", "").strip()

    # ── Prodys Quantum ST connection-state OID ──────────────────────────────
    # Prodys enterprise MIB: 1.3.6.1.4.1.32775 (Prodys)
    # Common path: quantumEncoderStatus or similar.
    # Without exact MIB we default to sysDescr as alive-check and let user
    # configure a specific OID via settings.
    if not oid:
        oid = "1.3.6.1.2.1.1.1.0"  # sysDescr — confirms device is alive

    val, err = _snmp_get(host, community, oid, port=port)

    if err == "pysnmp_not_installed":
        # pysnmp not available — fall back to TCP + HTTP
        _log("[Codec] pysnmp not installed; falling back to HTTP for SNMP devices")
        return _http_check(dev)

    if err:
        # SNMP failed — try HTTP fallback before declaring offline
        alive = _tcp_ok(host, 80)
        if alive:
            state, detail, rem = _http_check(dev)
            return state, f"(SNMP fail) {detail}", rem
        return "offline", f"SNMP: {err}", ""

    # Interpret the value
    # If user configured a specific connection-state OID the value is typically:
    #   Prodys: 1=connected, 0=idle  (INTEGER)
    #   APT:    varies by MIB version
    # For sysDescr we just confirm alive
    if val is not None:
        vl = val.lower()
        if val == "1" or "connect" in vl:
            return "connected", f"SNMP: {val}", ""
        if val == "0" or any(x in vl for x in ("idle", "ready", "standby")):
            return "idle", f"SNMP: {val}", ""
        # sysDescr or unknown OID — device is reachable
        return "idle", f"Online (SNMP ok: {val[:40]})", ""

    return "unknown", "No SNMP value returned", ""

# ── Device poll ─────────────────────────────────────────────────────────────
def _check_device(dev):
    """Poll one device. Returns {state, detail, remote}."""
    host   = dev.get("host", "").strip()
    port   = int(dev.get("port", 80))
    dtype  = dev.get("type", "custom")
    method = dev.get("method", _DTYPES.get(dtype, {}).get("method", "http"))

    if not host:
        return {"state": "unknown", "detail": "No host configured", "remote": ""}

    if method == "tcp":
        ok = _tcp_ok(host, port)
        return {"state": "idle" if ok else "offline",
                "detail": "TCP reachable" if ok else "TCP unreachable",
                "remote": ""}

    if method == "snmp":
        state, detail, remote = _snmp_check(dev)
    else:
        state, detail, remote = _http_check(dev)

    return {"state": state, "detail": detail, "remote": remote}

# ── Background poller ───────────────────────────────────────────────────────
def _poller():
    _log("[Codec] Poller started")
    _next: dict = {}
    while True:
        now = time.time()
        with _lock:
            devs = list(_devices)
        for dev in devs:
            did      = dev.get("id", "")
            interval = max(10, int(dev.get("poll_interval", 30)))
            if not did or now < _next.get(did, 0):
                continue
            _next[did] = now + interval
            try:
                new = _check_device(dev)
            except Exception as e:
                new = {"state": "error", "detail": str(e)[:100], "remote": ""}
            new["last_checked"] = now
            with _lock:
                old       = _status.get(did, {})
                old_state = old.get("state", "unknown")
                new_state = new["state"]
                new["last_change"] = (
                    old.get("last_change", now)
                    if old_state == new_state else now
                )
                _status[did] = new
            # State-change alerts
            if old_state not in ("offline", "disconnected", "unknown", "error") \
                    and new_state in ("offline", "disconnected", "error"):
                _do_alert(dev, new_state, new.get("detail", ""))
            elif old_state in ("offline", "disconnected", "error") \
                    and new_state in ("connected", "idle"):
                _do_recovery(dev, new_state)
        time.sleep(2)

# ── Alert log integration ───────────────────────────────────────────────────
def _get_alert_fn():
    global _alert_fn
    if _alert_fn:
        return _alert_fn
    for mod in sys.modules.values():
        if hasattr(mod, "_alert_log_append") and hasattr(mod, "hub_reports"):
            _alert_fn = mod._alert_log_append
            return _alert_fn
    return None

def _append_alert(stream, atype, message):
    fn = _get_alert_fn()
    if fn:
        try:
            fn({
                "id":              str(uuid.uuid4()),
                "ts":              time.strftime("%Y-%m-%d %H:%M:%S"),
                "stream":          stream,
                "type":            atype,
                "message":         message,
                "level_dbfs":      None,
                "rtp_loss_pct":    None,
                "rtp_jitter_ms":   None,
                "clip":            "",
                "ptp_state":       "",
                "ptp_offset_us":   0,
                "ptp_drift_us":    0,
                "ptp_jitter_us":   0,
                "ptp_gm":          "",
            })
        except Exception as e:
            _log(f"[Codec] Alert log error: {e}")

def _do_alert(dev, state, detail):
    name   = dev.get("name", dev.get("host", "?"))
    dtype  = _DTYPES.get(dev.get("type", ""), {}).get("label", dev.get("type", "Codec"))
    stream = dev.get("stream", name)
    _append_alert(stream, "CODEC_FAULT",
                  f"[{dtype}] {name} — {_STATE_LABEL.get(state, state)}: {detail}")
    _log(f"[Codec] FAULT — {name} → {state}: {detail}")

def _do_recovery(dev, state):
    name   = dev.get("name", dev.get("host", "?"))
    dtype  = _DTYPES.get(dev.get("type", ""), {}).get("label", dev.get("type", "Codec"))
    stream = dev.get("stream", name)
    _append_alert(stream, "CODEC_RECOVERY",
                  f"[{dtype}] {name} — recovered ({_STATE_LABEL.get(state, state)})")
    _log(f"[Codec] RECOVERED — {name} → {state}")

# ── Helpers ─────────────────────────────────────────────────────────────────
def _dev_by_id(did):
    with _lock:
        for d in _devices:
            if d.get("id") == did:
                return d
    return None

def _age(ts):
    if not ts:
        return "never"
    s = int(time.time() - ts)
    if s < 60:    return f"{s}s ago"
    if s < 3600:  return f"{s//60}m ago"
    if s < 86400: return f"{s//3600}h ago"
    return f"{s//86400}d ago"

def _duration(ts):
    if not ts:
        return ""
    s = int(time.time() - ts)
    if s < 60:    return f"{s}s"
    if s < 3600:  return f"{s//60}m {s%60}s"
    if s < 86400: return f"{s//3600}h {(s%3600)//60}m"
    return f"{s//86400}d {(s%86400)//3600}h"

# ── HTML template ───────────────────────────────────────────────────────────
_PAGE_TPL = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<title>Codecs — SignalScope</title>
<style nonce="{{csp_nonce()}}">
:root{--bg:#07142b;--bg2:#0d2040;--bg3:#112855;--ac:#17a8ff;--ac2:#0d8fd9;
--tx:#e0f0ff;--tx2:#7ab8d8;--br:#1a3a6a;--rd:#ef4444;--gn:#22c55e;--yw:#f59e0b;}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);font:14px/1.5 system-ui,sans-serif;min-height:100vh}
a{color:var(--ac);text-decoration:none}
.nav{background:var(--bg2);border-bottom:1px solid var(--br);padding:0 18px;
  display:flex;align-items:center;gap:8px;height:46px;flex-wrap:wrap}
.nav .logo{font-weight:700;color:var(--ac);font-size:1.05em;margin-right:8px}
.nav a{color:var(--tx2);font-size:13px;padding:4px 8px;border-radius:4px}
.nav a:hover,.nav a.cur{background:var(--bg3);color:var(--tx)}
.wrap{max-width:1100px;margin:0 auto;padding:24px 16px}
h1{font-size:1.3em;font-weight:600;color:var(--tx);margin-bottom:20px}
.toolbar{display:flex;align-items:center;gap:10px;margin-bottom:20px}
.btn{padding:6px 14px;border-radius:5px;border:none;cursor:pointer;font-size:13px;
  font-weight:500;transition:opacity .15s}
.btn:hover{opacity:.85}.btn:disabled{opacity:.4;cursor:default}
.btn.bp{background:var(--ac);color:#07142b}
.btn.bd{background:var(--rd);color:#fff}
.btn.bw{background:#1a3a6a;color:var(--tx)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.card{background:var(--bg2);border:1px solid var(--br);border-radius:8px;
  padding:16px;position:relative}
.card-head{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:10px}
.card-name{font-weight:600;font-size:1em;color:var(--tx)}
.card-type{font-size:11px;color:var(--tx2);background:var(--bg3);padding:2px 7px;
  border-radius:10px;margin-top:2px;display:inline-block}
.card-badge{display:flex;align-items:center;gap:6px;font-size:13px;font-weight:600}
.dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.card-detail{font-size:12px;color:var(--tx2);margin-top:6px;min-height:16px}
.card-remote{font-size:12px;color:var(--gn);margin-top:3px;min-height:14px}
.card-meta{font-size:11px;color:#4a7899;margin-top:8px;display:flex;
  justify-content:space-between}
.card-actions{display:flex;gap:6px;margin-top:12px}
.card-actions .btn{padding:4px 10px;font-size:12px}
.empty{text-align:center;color:var(--tx2);padding:60px 20px;font-size:15px}
/* Modal */
.overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);
  z-index:100;align-items:center;justify-content:center}
.overlay.open{display:flex}
.modal{background:var(--bg2);border:1px solid var(--br);border-radius:10px;
  padding:24px;width:100%;max-width:480px;max-height:90vh;overflow-y:auto}
.modal h2{font-size:1.1em;margin-bottom:18px;color:var(--tx)}
.field{margin-bottom:14px}
.field label{display:block;font-size:12px;color:var(--tx2);margin-bottom:4px}
.field input,.field select{width:100%;background:var(--bg3);border:1px solid var(--br);
  border-radius:5px;color:var(--tx);padding:7px 10px;font-size:13px}
.field input:focus,.field select:focus{outline:none;border-color:var(--ac)}
.field .hint{font-size:11px;color:#4a7899;margin-top:3px}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.modal-btns{display:flex;justify-content:flex-end;gap:8px;margin-top:20px}
.snmp-fields{display:none}
</style></head>
<body>
<nav class="nav">
  <span class="logo">📡 Codecs</span>
  <a href="/hub/status">Overview</a>
  <a href="/hub/reports">Reports</a>
  <a href="/hub/codecs" class="cur">Codecs</a>
  <a href="/hub/settings">Settings</a>
</nav>
<div class="wrap">
  <div class="toolbar">
    <h1>Codec Monitor</h1>
    <button class="btn bp" id="add-btn">+ Add Device</button>
    <button class="btn bw" id="refresh-btn" style="margin-left:auto">↻ Refresh</button>
  </div>
  <div class="grid" id="grid">{{cards_html}}</div>
</div>

<!-- Add / Edit modal -->
<div class="overlay" id="overlay">
  <div class="modal">
    <h2 id="modal-title">Add Codec Device</h2>
    <div class="field">
      <label>Name</label>
      <input id="f-name" placeholder="e.g. Studio 1 Link">
    </div>
    <div class="row2">
      <div class="field">
        <label>Type</label>
        <select id="f-type">
          <option value="comrex">Comrex</option>
          <option value="tieline">Tieline</option>
          <option value="quantum_st">Prodys Quantum ST</option>
          <option value="apt">APT / WorldCast Quantum</option>
          <option value="custom">Custom (HTTP)</option>
          <option value="tcp">TCP Ping Only</option>
        </select>
      </div>
      <div class="field">
        <label>Host / IP</label>
        <input id="f-host" placeholder="192.168.1.10">
      </div>
    </div>
    <div class="row2">
      <div class="field">
        <label>Port</label>
        <input id="f-port" type="number" value="80" min="1" max="65535">
      </div>
      <div class="field">
        <label>Poll interval (s)</label>
        <input id="f-interval" type="number" value="30" min="10" max="300">
      </div>
    </div>
    <div class="field">
      <label>Associated stream (for alerts)</label>
      <input id="f-stream" placeholder="e.g. Studio 1 — leave blank to use device name">
    </div>
    <!-- HTTP fields -->
    <div id="http-fields">
      <div class="field">
        <label>HTTP endpoint path</label>
        <input id="f-endpoint" placeholder="Leave blank for device-type default">
        <div class="hint">Comrex: / &nbsp;|&nbsp; Tieline: /api/v1/status</div>
      </div>
      <div class="row2">
        <div class="field">
          <label>Username (optional)</label>
          <input id="f-user" autocomplete="off">
        </div>
        <div class="field">
          <label>Password (optional)</label>
          <input id="f-pwd" type="password" autocomplete="off">
        </div>
      </div>
      <div class="field">
        <label><input id="f-ssl" type="checkbox"> Use HTTPS</label>
      </div>
    </div>
    <!-- SNMP fields -->
    <div class="snmp-fields" id="snmp-fields">
      <div class="row2">
        <div class="field">
          <label>SNMP community</label>
          <input id="f-community" value="public">
        </div>
        <div class="field">
          <label>SNMP port</label>
          <input id="f-snmp-port" type="number" value="161">
        </div>
      </div>
      <div class="field">
        <label>Connection status OID (optional)</label>
        <input id="f-oid" placeholder="Leave blank for sysDescr alive-check">
        <div class="hint">Prodys: consult your device MIB. Blank = TCP/HTTP fallback.</div>
      </div>
    </div>
    <div class="modal-btns">
      <button class="btn bw" id="cancel-btn">Cancel</button>
      <button class="btn bp" id="save-btn">Save</button>
    </div>
  </div>
</div>

<script nonce="{{csp_nonce()}}">
var _editId = null;
var _autoRefresh = null;

function _csrf(){
  return (document.querySelector('meta[name="csrf-token"]')||{}).content||'';
}
function _post(url,body){
  return fetch(url,{method:'POST',headers:{'Content-Type':'application/json',
    'X-CSRFToken':_csrf()},body:JSON.stringify(body)}).then(r=>r.json());
}

// ── Port / field defaults per type ──
var _TYPE_DEFS = {
  comrex:     {port:80,  method:'http'},
  tieline:    {port:80,  method:'http'},
  quantum_st: {port:161, method:'snmp'},
  apt:        {port:161, method:'snmp'},
  custom:     {port:80,  method:'http'},
  tcp:        {port:80,  method:'tcp'},
};
document.getElementById('f-type').addEventListener('change', function(){
  var t = this.value;
  var def = _TYPE_DEFS[t]||{port:80,method:'http'};
  document.getElementById('f-port').value = def.port;
  var isSnmp = def.method === 'snmp';
  document.getElementById('http-fields').style.display = isSnmp?'none':'';
  document.getElementById('snmp-fields').style.display = isSnmp?'':'none';
});

function openAdd(){
  _editId = null;
  document.getElementById('modal-title').textContent = 'Add Codec Device';
  document.getElementById('f-name').value = '';
  document.getElementById('f-type').value = 'comrex';
  document.getElementById('f-type').dispatchEvent(new Event('change'));
  document.getElementById('f-host').value = '';
  document.getElementById('f-port').value = '80';
  document.getElementById('f-interval').value = '30';
  document.getElementById('f-stream').value = '';
  document.getElementById('f-endpoint').value = '';
  document.getElementById('f-user').value = '';
  document.getElementById('f-pwd').value = '';
  document.getElementById('f-ssl').checked = false;
  document.getElementById('f-community').value = 'public';
  document.getElementById('f-snmp-port').value = '161';
  document.getElementById('f-oid').value = '';
  document.getElementById('overlay').classList.add('open');
  document.getElementById('f-name').focus();
}

function openEdit(id, name, type, host, port, interval, stream, endpoint,
                  user, ssl, community, snmpPort, oid){
  _editId = id;
  document.getElementById('modal-title').textContent = 'Edit ' + name;
  document.getElementById('f-name').value = name;
  document.getElementById('f-type').value = type;
  document.getElementById('f-type').dispatchEvent(new Event('change'));
  document.getElementById('f-host').value = host;
  document.getElementById('f-port').value = port;
  document.getElementById('f-interval').value = interval;
  document.getElementById('f-stream').value = stream;
  document.getElementById('f-endpoint').value = endpoint;
  document.getElementById('f-user').value = user;
  document.getElementById('f-pwd').value = '';
  document.getElementById('f-ssl').checked = ssl;
  document.getElementById('f-community').value = community||'public';
  document.getElementById('f-snmp-port').value = snmpPort||161;
  document.getElementById('f-oid').value = oid||'';
  document.getElementById('overlay').classList.add('open');
}

function closeModal(){ document.getElementById('overlay').classList.remove('open'); }

document.getElementById('add-btn').addEventListener('click', openAdd);
document.getElementById('cancel-btn').addEventListener('click', closeModal);
document.getElementById('overlay').addEventListener('click', function(e){
  if(e.target===this) closeModal();
});

document.getElementById('save-btn').addEventListener('click', function(){
  var body = {
    name:          document.getElementById('f-name').value.trim(),
    type:          document.getElementById('f-type').value,
    host:          document.getElementById('f-host').value.trim(),
    port:          parseInt(document.getElementById('f-port').value)||80,
    poll_interval: parseInt(document.getElementById('f-interval').value)||30,
    stream:        document.getElementById('f-stream').value.trim(),
    endpoint:      document.getElementById('f-endpoint').value.trim(),
    username:      document.getElementById('f-user').value.trim(),
    password:      document.getElementById('f-pwd').value,
    ssl:           document.getElementById('f-ssl').checked,
    snmp_community:document.getElementById('f-community').value.trim()||'public',
    snmp_port:     parseInt(document.getElementById('f-snmp-port').value)||161,
    snmp_oid:      document.getElementById('f-oid').value.trim(),
  };
  if(!body.name||!body.host){ alert('Name and host are required.'); return; }
  var url = _editId ? '/api/codecs/devices/'+_editId : '/api/codecs/devices';
  _post(url, body).then(function(r){
    if(r.ok){ closeModal(); loadStatus(); }
    else alert('Save failed: '+(r.error||'unknown'));
  }).catch(function(e){ alert('Save failed: '+e); });
});

// ── Card actions via event delegation ──
document.getElementById('grid').addEventListener('click', function(e){
  var rm   = e.target.closest('.rm-btn');
  var edit = e.target.closest('.edit-btn');
  var chk  = e.target.closest('.check-btn');
  if(rm){
    if(!confirm('Remove "'+rm.dataset.name+'"?')) return;
    _post('/api/codecs/devices/'+rm.dataset.id+'/remove',{}).then(function(r){
      if(r.ok) loadStatus(); else alert('Remove failed');
    });
  }
  if(edit){
    var d = edit.dataset;
    openEdit(d.id, d.name, d.type, d.host, d.port, d.interval, d.stream||'',
             d.endpoint||'', d.user||'', d.ssl==='true',
             d.community||'public', d.snmpport||161, d.oid||'');
  }
  if(chk){
    chk.textContent='…'; chk.disabled=true;
    _post('/api/codecs/devices/'+chk.dataset.id+'/check',{}).then(function(){
      loadStatus();
    });
  }
});

// ── Status refresh ──
function _stateColour(s){
  return {connected:'#22c55e',idle:'#17a8ff',disconnected:'#f59e0b',
          offline:'#ef4444',error:'#ef4444',unknown:'#6b7280'}[s]||'#6b7280';
}
function _stateLabel(s){
  return {connected:'Connected',idle:'Idle / Ready',disconnected:'Disconnected',
          offline:'Offline',error:'Error',unknown:'Unknown'}[s]||s;
}
function _age(ts){
  if(!ts) return 'never';
  var s=Math.floor(Date.now()/1000-ts);
  if(s<60) return s+'s ago';
  if(s<3600) return Math.floor(s/60)+'m ago';
  return Math.floor(s/3600)+'h ago';
}
function _dur(ts){
  if(!ts) return '';
  var s=Math.floor(Date.now()/1000-ts);
  if(s<60) return s+'s';
  if(s<3600) return Math.floor(s/60)+'m '+s%60+'s';
  if(s<86400) return Math.floor(s/3600)+'h '+Math.floor((s%3600)/60)+'m';
  return Math.floor(s/86400)+'d '+Math.floor((s%86400)/3600)+'h';
}

function loadStatus(){
  fetch('/api/codecs/status').then(r=>r.json()).then(function(data){
    var grid = document.getElementById('grid');
    if(!data.devices||!data.devices.length){
      grid.innerHTML='<div class="empty">No codec devices configured.<br>Click <b>+ Add Device</b> to get started.</div>';
      return;
    }
    grid.innerHTML = data.devices.map(function(d){
      var st = d.status||{};
      var state = st.state||'unknown';
      var col   = _stateColour(state);
      var lbl   = _stateLabel(state);
      var dtype = d.type_label||d.type;
      var checked = _age(st.last_checked);
      var dur   = st.last_change ? _dur(st.last_change) : '';
      var detail = st.detail||'';
      var remote = st.remote ? '⇄ '+st.remote : '';
      return '<div class="card">'+
        '<div class="card-head">'+
          '<div><div class="card-name">'+_esc(d.name)+'</div>'+
          '<span class="card-type">'+_esc(dtype)+'</span></div>'+
          '<div class="card-badge">'+
            '<div class="dot" style="background:'+col+'"></div>'+
            '<span style="color:'+col+'">'+lbl+'</span>'+
          '</div>'+
        '</div>'+
        (remote?'<div class="card-remote">'+_esc(remote)+'</div>':'')+
        '<div class="card-detail">'+_esc(detail)+'</div>'+
        '<div class="card-meta">'+
          '<span>Checked '+checked+'</span>'+
          (dur?'<span style="color:'+col+'">'+lbl+' '+dur+'</span>':'')+
        '</div>'+
        '<div class="card-actions">'+
          '<button class="btn bw check-btn" data-id="'+d.id+'">↻ Check</button>'+
          '<button class="btn bw edit-btn"'+
            ' data-id="'+d.id+'"'+
            ' data-name="'+_esc(d.name)+'"'+
            ' data-type="'+d.type+'"'+
            ' data-host="'+_esc(d.host)+'"'+
            ' data-port="'+d.port+'"'+
            ' data-interval="'+d.poll_interval+'"'+
            ' data-stream="'+_esc(d.stream||'')+'"'+
            ' data-endpoint="'+_esc(d.endpoint||'')+'"'+
            ' data-user="'+_esc(d.username||'')+'"'+
            ' data-ssl="'+!!d.ssl+'"'+
            ' data-community="'+_esc(d.snmp_community||'public')+'"'+
            ' data-snmpport="'+(d.snmp_port||161)+'"'+
            ' data-oid="'+_esc(d.snmp_oid||'')+'"'+
          '>✎ Edit</button>'+
          '<button class="btn bd rm-btn" data-id="'+d.id+'" data-name="'+_esc(d.name)+'">✕</button>'+
        '</div>'+
      '</div>';
    }).join('');
  }).catch(function(e){ console.error('Status fetch failed',e); });
}

function _esc(s){ return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;')
  .replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

document.getElementById('refresh-btn').addEventListener('click', loadStatus);

// Auto-refresh every 30s
_autoRefresh = setInterval(loadStatus, 30000);
loadStatus();
</script>
</body></html>"""

# ── register() ─────────────────────────────────────────────────────────────
def register(app, ctx):
    global _log, _cfg_file

    _log        = ctx["monitor"].log
    _cfg_file   = Path(ctx["monitor"].app_cfg.__class__.__module__
                       .replace(".", "/")).parent / "codec_devices.json"

    # Resolve cfg file relative to signalscope.py location via __file__ fallback
    for mod in sys.modules.values():
        if hasattr(mod, "_alert_log_append") and hasattr(mod, "hub_reports"):
            try:
                import inspect
                src = inspect.getfile(mod)
                _cfg_file = Path(src).parent / "codec_devices.json"
            except Exception:
                pass
            break

    _load()
    login_req  = ctx["login_required"]
    csrf_dec   = ctx["csrf_protect"]
    mobile_req = ctx.get("mobile_api_required", ctx["login_required"])

    # Start poller
    t = threading.Thread(target=_poller, daemon=True, name="CodecPoller")
    t.start()

    # ── Web routes ────────────────────────────────────────────────────────
    @app.get("/hub/codecs")
    @login_req
    def codec_page():
        from flask import render_template_string
        # Render initial cards server-side; JS will keep them updated
        cards = _render_cards()
        return render_template_string(_PAGE_TPL, cards_html=cards)

    @app.get("/api/codecs/status")
    @login_req
    def codec_status():
        from flask import jsonify
        with _lock:
            devs = list(_devices)
            stat = dict(_status)
        out = []
        for d in devs:
            did = d.get("id", "")
            row = dict(d)
            row.pop("password", None)  # never send password to browser
            row["type_label"] = _DTYPES.get(d.get("type", ""), {}).get("label", d.get("type", ""))
            row["status"] = stat.get(did, {"state": "unknown", "detail": "Not yet checked"})
            out.append(row)
        return jsonify({"devices": out})

    @app.post("/api/codecs/devices")
    @login_req
    @csrf_dec
    def codec_add():
        from flask import request, jsonify
        body = request.get_json(force=True) or {}
        dev  = _build_dev(body)
        with _lock:
            _devices.append(dev)
            _save()
        _log(f"[Codec] Added device: {dev['name']} ({dev['host']})")
        return jsonify({"ok": True, "id": dev["id"]})

    @app.post("/api/codecs/devices/<did>")
    @login_req
    @csrf_dec
    def codec_update(did):
        from flask import request, jsonify
        body = request.get_json(force=True) or {}
        with _lock:
            for i, d in enumerate(_devices):
                if d.get("id") == did:
                    updated = _build_dev(body, existing=d)
                    _devices[i] = updated
                    _save()
                    _log(f"[Codec] Updated device: {updated['name']}")
                    return jsonify({"ok": True})
        return jsonify({"error": "not found"}), 404

    @app.post("/api/codecs/devices/<did>/remove")
    @login_req
    @csrf_dec
    def codec_remove(did):
        from flask import jsonify
        with _lock:
            before = len(_devices)
            _devices[:] = [d for d in _devices if d.get("id") != did]
            _status.pop(did, None)
            if len(_devices) < before:
                _save()
                _log(f"[Codec] Removed device {did}")
                return jsonify({"ok": True})
        return jsonify({"error": "not found"}), 404

    @app.post("/api/codecs/devices/<did>/check")
    @login_req
    @csrf_dec
    def codec_force_check(did):
        from flask import jsonify
        dev = _dev_by_id(did)
        if not dev:
            return jsonify({"error": "not found"}), 404
        try:
            new = _check_device(dev)
        except Exception as e:
            new = {"state": "error", "detail": str(e)[:100], "remote": ""}
        new["last_checked"] = time.time()
        with _lock:
            old       = _status.get(did, {})
            old_state = old.get("state", "unknown")
            new_state = new["state"]
            new["last_change"] = old.get("last_change", new["last_checked"]) \
                if old_state == new_state else new["last_checked"]
            _status[did] = new
        if old_state not in ("offline", "disconnected", "unknown", "error") \
                and new_state in ("offline", "disconnected", "error"):
            _do_alert(dev, new_state, new.get("detail", ""))
        elif old_state in ("offline", "disconnected", "error") \
                and new_state in ("connected", "idle"):
            _do_recovery(dev, new_state)
        return jsonify({"ok": True, "status": new})

    # ── Mobile API ────────────────────────────────────────────────────────
    @app.get("/api/mobile/codecs/status")
    @mobile_req
    def api_mobile_codecs_status():
        from flask import jsonify
        with _lock:
            devs = list(_devices)
            stat = dict(_status)
        out = []
        for d in devs:
            did = d.get("id", "")
            st  = stat.get(did, {"state": "unknown"})
            out.append({
                "id":           did,
                "name":         d.get("name", ""),
                "type":         d.get("type", ""),
                "type_label":   _DTYPES.get(d.get("type",""), {}).get("label", d.get("type","")),
                "stream":       d.get("stream", ""),
                "host":         d.get("host", ""),
                "state":        st.get("state", "unknown"),
                "state_label":  _STATE_LABEL.get(st.get("state","unknown"), "Unknown"),
                "detail":       st.get("detail", ""),
                "remote":       st.get("remote", ""),
                "last_checked": st.get("last_checked"),
                "last_change":  st.get("last_change"),
                "duration_s":   int(time.time() - st["last_change"])
                                if st.get("last_change") else None,
            })
        return jsonify({"codecs": out, "ts": time.time()})

    _log("[Codec] Plugin registered — polling started")

# ── Helpers ─────────────────────────────────────────────────────────────────
def _build_dev(body, existing=None):
    """Build a device dict from POST body, preserving id and password if not changed."""
    dev = dict(existing) if existing else {"id": str(uuid.uuid4())}
    dev["name"]          = str(body.get("name", "")).strip()[:80]
    dev["type"]          = str(body.get("type", "custom")).strip()
    dev["host"]          = str(body.get("host", "")).strip()
    dev["port"]          = max(1, min(65535, int(body.get("port") or
                               _DTYPES.get(dev["type"], {}).get("port", 80))))
    dev["poll_interval"] = max(10, min(300, int(body.get("poll_interval") or 30)))
    dev["stream"]        = str(body.get("stream", "")).strip()[:100]
    dev["endpoint"]      = str(body.get("endpoint", "")).strip()
    dev["username"]      = str(body.get("username", "")).strip()
    dev["ssl"]           = bool(body.get("ssl", False))
    dev["snmp_community"]= str(body.get("snmp_community", "public")).strip() or "public"
    dev["snmp_port"]     = max(1, min(65535, int(body.get("snmp_port") or 161)))
    dev["snmp_oid"]      = str(body.get("snmp_oid", "")).strip()
    dev["method"]        = _DTYPES.get(dev["type"], {}).get("method", "http")
    # Only overwrite password if a new one was supplied
    new_pwd = str(body.get("password", ""))
    if new_pwd:
        dev["password"] = new_pwd
    elif "password" not in dev:
        dev["password"] = ""
    return dev

def _render_cards():
    """Server-side render of cards for initial page load."""
    with _lock:
        devs = list(_devices)
        stat = dict(_status)
    if not devs:
        return '<div class="empty">No codec devices configured.<br>' \
               'Click <b>+ Add Device</b> to get started.</div>'
    html = []
    for d in devs:
        did    = d.get("id", "")
        st     = stat.get(did, {"state": "unknown", "detail": "Not yet checked"})
        state  = st.get("state", "unknown")
        col    = _STATE_COLOUR.get(state, "#6b7280")
        lbl    = _STATE_LABEL.get(state, state)
        dtype  = _DTYPES.get(d.get("type",""), {}).get("label", d.get("type",""))
        detail = st.get("detail", "")
        remote = st.get("remote", "")
        html.append(
            f'<div class="card">'
            f'<div class="card-head">'
            f'<div><div class="card-name">{_he(d["name"])}</div>'
            f'<span class="card-type">{_he(dtype)}</span></div>'
            f'<div class="card-badge">'
            f'<div class="dot" style="background:{col}"></div>'
            f'<span style="color:{col}">{lbl}</span></div></div>'
            + (f'<div class="card-remote">⇄ {_he(remote)}</div>' if remote else '')
            + f'<div class="card-detail">{_he(detail)}</div>'
            f'<div class="card-meta"><span>Not yet checked</span></div>'
            f'<div class="card-actions">'
            f'<button class="btn bw check-btn" data-id="{did}">↻ Check</button>'
            f'<button class="btn bw edit-btn"'
            f' data-id="{did}" data-name="{_he(d["name"])}" data-type="{d.get("type","")}"'
            f' data-host="{_he(d.get("host",""))}" data-port="{d.get("port",80)}"'
            f' data-interval="{d.get("poll_interval",30)}"'
            f' data-stream="{_he(d.get("stream",""))}"'
            f' data-endpoint="{_he(d.get("endpoint",""))}"'
            f' data-user="{_he(d.get("username",""))}"'
            f' data-ssl="{str(bool(d.get("ssl",False))).lower()}"'
            f' data-community="{_he(d.get("snmp_community","public"))}"'
            f' data-snmpport="{d.get("snmp_port",161)}"'
            f' data-oid="{_he(d.get("snmp_oid",""))}"'
            f'>✎ Edit</button>'
            f'<button class="btn bd rm-btn" data-id="{did}"'
            f' data-name="{_he(d["name"])}">✕</button>'
            f'</div></div>'
        )
    return "\n".join(html)

def _he(s):
    """HTML-escape a string."""
    return str(s).replace("&","&amp;").replace('"','&quot;').replace("<","&lt;").replace(">","&gt;")
