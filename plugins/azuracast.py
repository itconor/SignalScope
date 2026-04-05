# azuracast.py — AzuraCast integration plugin for SignalScope
# Drop into the plugins/ subdirectory.

SIGNALSCOPE_PLUGIN = {
    "id":      "azuracast",
    "label":   "AzuraCast",
    "url":     "/azuracast",
    "icon":    "🎙",
    "version": "1.0.0",
}

import hashlib
import hmac as _hmac_mod
import json
import os
import sys
import threading
import time
import uuid
import urllib.request
import urllib.error
import urllib.parse

from flask import abort, jsonify, redirect, render_template_string, request

# ─── Constants ────────────────────────────────────────────────────────────────
_CONFIG_FILE     = "azuracast_config.json"
_POLL_INTERVAL   = 30   # seconds between AzuraCast API polls
_REPORT_INTERVAL = 30   # seconds between hub status POSTs
_DEFAULT_CFG = {
    "servers":       [],
    "poll_interval": 30,
}

# ─── Module-level state ───────────────────────────────────────────────────────
_station_status: dict  = {}   # "{server_id}::{station_id}" → status dict
_client_statuses: dict = {}   # hub side: site → payload
_silence_alerted: set  = set()
_lock = threading.Lock()

_cfg: dict = {}
_cfg_lock   = threading.Lock()

_monitor   = None
_app_dir   = ""

_poller_threads: dict = {}   # server_id → (thread, stop_event)
_poller_lock = threading.Lock()

_alert_fn = None


# ─── Logging ─────────────────────────────────────────────────────────────────

def _log(msg: str) -> None:
    if _monitor:
        _monitor.log(msg)


# ─── Config persistence ───────────────────────────────────────────────────────

def _cfg_path() -> str:
    return os.path.join(_app_dir, _CONFIG_FILE)


def _load_cfg() -> dict:
    path = _cfg_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            merged = dict(_DEFAULT_CFG)
            merged.update({k: v for k, v in data.items() if k in _DEFAULT_CFG})
            merged["servers"] = data.get("servers", [])
            return merged
        except Exception as exc:
            _log(f"[AzuraCast] Config load error: {exc}")
    return dict(_DEFAULT_CFG)


def _save_cfg(cfg: dict) -> None:
    path = _cfg_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as exc:
        _log(f"[AzuraCast] Config save error: {exc}")


def _get_cfg() -> dict:
    with _cfg_lock:
        return dict(_cfg)


def _set_cfg(new_cfg: dict) -> None:
    with _cfg_lock:
        _cfg.clear()
        _cfg.update(new_cfg)
    _save_cfg(new_cfg)


# ─── HMAC helpers ─────────────────────────────────────────────────────────────

def _make_sig(secret: str, data: bytes, ts: float) -> str:
    key = hashlib.sha256(f"{secret}:signing".encode()).digest()
    msg = f"{ts:.0f}:".encode() + data
    return _hmac_mod.new(key, msg, hashlib.sha256).hexdigest()


def _check_sig(secret: str, data: bytes) -> bool:
    if not secret:
        return True
    sig  = request.headers.get("X-Hub-Sig", "")
    ts_h = request.headers.get("X-Hub-Ts", "0")
    if not sig:
        return False
    try:
        ts = float(ts_h)
    except ValueError:
        return False
    if abs(time.time() - ts) > 120:
        return False
    expected = _make_sig(secret, data, ts)
    return _hmac_mod.compare_digest(sig, expected)


# ─── Alert log integration ────────────────────────────────────────────────────

def _get_alert_fn():
    global _alert_fn
    if _alert_fn:
        return _alert_fn
    for mod in sys.modules.values():
        if hasattr(mod, "_alert_log_append") and hasattr(mod, "hub_reports"):
            _alert_fn = mod._alert_log_append
            return _alert_fn
    return None


def _append_alert(stream: str, atype: str, message: str) -> None:
    fn = _get_alert_fn()
    if fn:
        try:
            fn({
                "id":             str(uuid.uuid4()),
                "ts":             time.strftime("%Y-%m-%d %H:%M:%S"),
                "stream":         stream,
                "type":           atype,
                "message":        message,
                "level_dbfs":     None,
                "rtp_loss_pct":   None,
                "rtp_jitter_ms":  None,
                "clip":           "",
                "ptp_state":      "", "ptp_offset_us": 0,
                "ptp_drift_us":   0, "ptp_jitter_us": 0, "ptp_gm": "",
            })
        except Exception as e:
            _log(f"[AzuraCast] Alert error: {e}")


# ─── AzuraCast API helpers ────────────────────────────────────────────────────

def _az_request(server_url: str, api_key: str, path: str, timeout: int = 10) -> dict:
    """Make a GET request to the AzuraCast API. Returns parsed JSON dict or raises."""
    url = server_url.rstrip("/") + "/api" + path
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _fmt_seconds(s: "int | None") -> str:
    if s is None or s < 0:
        return "0:00"
    m, sec = divmod(int(s), 60)
    return f"{m}:{sec:02d}"


# ─── Poller: one thread per server ────────────────────────────────────────────

def _poll_server(server_cfg: dict) -> None:
    srv_id  = server_cfg.get("id", "")
    srv_url = server_cfg.get("url", "").rstrip("/")
    api_key = server_cfg.get("api_key", "")

    for station in server_cfg.get("stations", []):
        station_id   = str(station.get("station_id", "")).strip()
        display_name = station.get("display_name", "").strip()
        input_name   = station.get("input_name", "").strip()
        silence_alert = bool(station.get("silence_alert", False))
        key = f"{srv_id}::{station_id}"

        if not station_id:
            continue

        with _lock:
            prev = dict(_station_status.get(key, {}))

        try:
            data = _az_request(srv_url, api_key, f"/nowplaying/{station_id}")
        except Exception as exc:
            with _lock:
                existing = dict(_station_status.get(key, {}))
                existing.update({
                    "server_id":   srv_id,
                    "server_url":  srv_url,
                    "station_id":  station_id,
                    "station_name": display_name or station_id,
                    "input_name":  input_name,
                    "silence_alert": silence_alert,
                    "error":       str(exc),
                    "last_updated": time.time(),
                })
                _station_status[key] = existing
            _log(f"[AzuraCast] Poll error {key}: {exc}")
            continue

        station_info = data.get("station", {})
        station_name = display_name or station_info.get("name", station_id)
        now_pl       = data.get("now_playing", {}) or {}
        song         = now_pl.get("song", {}) or {}
        playing_next = data.get("playing_next", {}) or {}
        next_song    = playing_next.get("song", {}) or {}
        live_info    = data.get("live", {}) or {}
        listeners    = data.get("listeners", {}) or {}
        is_online    = bool(data.get("is_online", False))

        status = {
            "server_id":       srv_id,
            "server_url":      srv_url,
            "station_id":      station_id,
            "station_name":    station_name,
            "input_name":      input_name,
            "silence_alert":   silence_alert,
            "is_online":       is_online,
            "is_live":         bool(live_info.get("is_live", False)),
            "streamer_name":   live_info.get("streamer_name", ""),
            "listeners":       int(listeners.get("current", 0)),
            "listeners_unique": int(listeners.get("unique", 0)),
            "now_playing": {
                "title":    song.get("title", ""),
                "artist":   song.get("artist", ""),
                "album":    song.get("album", ""),
                "text":     song.get("text", ""),
                "art_url":  song.get("art", ""),
                "playlist": now_pl.get("playlist", ""),
                "elapsed":  int(now_pl.get("elapsed", 0)),
                "duration": int(now_pl.get("duration", 0)),
                "is_request": bool(now_pl.get("is_request", False)),
            },
            "playing_next": {
                "title":  next_song.get("title", ""),
                "artist": next_song.get("artist", ""),
                "text":   next_song.get("text", "") or playing_next.get("song", {}).get("text", ""),
            },
            "last_updated": time.time(),
            "error": "",
        }

        with _lock:
            _station_status[key] = status

        # ── Fault / recovery alerts on online-state transitions ──────────────
        prev_online = prev.get("is_online")
        if prev_online is not None:
            if prev_online and not is_online:
                _append_alert(station_name, "AZURACAST_FAULT",
                              f"[AzuraCast] {station_name} went offline")
                _log(f"[AzuraCast] FAULT — {station_name} offline")
            elif not prev_online and is_online:
                _append_alert(station_name, "AZURACAST_RECOVERY",
                              f"[AzuraCast] {station_name} is back online")
                _log(f"[AzuraCast] RECOVERY — {station_name} online")

        # ── Silence cross-reference ──────────────────────────────────────────
        if is_online and silence_alert and input_name and _monitor:
            try:
                cfg_ss = _monitor.app_cfg
                inp = next(
                    (i for i in (cfg_ss.inputs or [])
                     if getattr(i, "name", None) == input_name),
                    None
                )
                silence_active = bool(getattr(inp, "_silence_active", False)) if inp else False
                currently_alerted = key in _silence_alerted

                if silence_active and not currently_alerted:
                    _silence_alerted.add(key)
                    _append_alert(station_name, "AZURACAST_SILENCE",
                                  f"[AzuraCast] {station_name} is online but input '{input_name}' is silent")
                    _log(f"[AzuraCast] SILENCE mismatch — {station_name} / {input_name}")
                elif not silence_active and currently_alerted:
                    _silence_alerted.discard(key)
            except Exception as exc:
                _log(f"[AzuraCast] Silence check error: {exc}")


def _server_poller_thread(server_cfg: dict, stop_evt: threading.Event) -> None:
    poll_interval = _get_cfg().get("poll_interval", _POLL_INTERVAL)
    while not stop_evt.is_set():
        try:
            _poll_server(server_cfg)
        except Exception as exc:
            _log(f"[AzuraCast] Poller error: {exc}")
        stop_evt.wait(poll_interval)


def _start_pollers() -> None:
    global _poller_threads
    cfg = _get_cfg()
    new_ids = {s.get("id", "") for s in cfg.get("servers", [])}

    with _poller_lock:
        # Stop removed servers
        for sid in list(_poller_threads.keys()):
            if sid not in new_ids:
                _poller_threads[sid][1].set()
                _poller_threads.pop(sid, None)

        # Start new servers
        for srv in cfg.get("servers", []):
            sid = srv.get("id", "")
            if not sid or sid in _poller_threads:
                continue
            ev = threading.Event()
            t  = threading.Thread(
                target=_server_poller_thread,
                args=(srv, ev),
                daemon=True,
                name=f"AzuraCast-{sid}",
            )
            _poller_threads[sid] = (t, ev)
            t.start()


# ─── Hub reporter thread ──────────────────────────────────────────────────────

def _hub_reporter_thread(monitor) -> None:
    while True:
        time.sleep(_REPORT_INTERVAL)
        try:
            cfg_ss  = monitor.app_cfg
            hub_url = getattr(getattr(cfg_ss, "hub", None), "hub_url", "").rstrip("/")
            if not hub_url:
                continue
            site   = getattr(getattr(cfg_ss, "hub", None), "site_name", "") or ""
            secret = getattr(getattr(cfg_ss, "hub", None), "secret_key", "") or ""

            with _lock:
                stations_out = list(_station_status.values())

            payload = {
                "site":     site,
                "stations": stations_out,
                "ts":       time.time(),
            }
            body = json.dumps(payload, default=str).encode("utf-8")
            ts   = time.time()
            sig  = _make_sig(secret, body, ts) if secret else ""

            req = urllib.request.Request(
                f"{hub_url}/api/azuracast/client_status",
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "X-Site":       site,
                    "X-Hub-Ts":     f"{ts:.0f}",
                    "X-Hub-Sig":    sig,
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
        except Exception as exc:
            _log(f"[AzuraCast] Hub reporter error: {exc}")


# ─── Inputs helper ────────────────────────────────────────────────────────────

def _get_inputs_list(monitor) -> list:
    try:
        cfg_ss = monitor.app_cfg
        result = []
        for inp in (cfg_ss.inputs or []):
            result.append({
                "name":       getattr(inp, "name", ""),
                "device":     getattr(inp, "device", ""),
                "has_buffer": bool(getattr(inp, "_silence_active", None) is not None),
            })
        return result
    except Exception:
        return []


# ─── HTML Templates ───────────────────────────────────────────────────────────

_CLIENT_TPL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<title>AzuraCast — SignalScope</title>
<style nonce="{{csp_nonce()}}">
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(circle at top,#12376f 0%,var(--bg) 38%,#05101f 100%);color:var(--tx);font-family:system-ui,sans-serif;font-size:13px;min-height:100vh}
header{background:linear-gradient(180deg,rgba(10,31,65,.96),rgba(9,24,48,.96));border-bottom:1px solid var(--bor);padding:12px 20px;display:flex;align-items:center;gap:12px}
header h1{font-size:16px;font-weight:700;color:var(--acc);flex:1}
.main{padding:20px 24px;max-width:1200px;margin:0 auto}
.btn{border:none;border-radius:8px;padding:5px 12px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit}
.btn:hover{filter:brightness(1.15)}
.bp{background:var(--acc);color:#07142b}
.bd{background:var(--al);color:#fff}
.bg{background:#17345f;color:var(--tx)}
.bs{padding:3px 9px;font-size:12px}
.card{background:var(--sur);border:1px solid var(--bor);border-radius:12px;overflow:hidden;margin-bottom:16px}
.ch{padding:9px 14px;display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--bor);background:linear-gradient(180deg,#143766,#102b54);font-size:12px;font-weight:700;color:var(--acc);text-transform:uppercase;letter-spacing:.06em}
.cb{padding:14px}
.badge{display:inline-block;border-radius:5px;padding:2px 7px;font-size:11px;font-weight:700}
.b-ok{background:#0f2318;color:var(--ok);border:1px solid var(--ok)}
.b-al{background:#2a0a0a;color:var(--al);border:1px solid var(--al)}
.b-mu{background:#0d2346;color:var(--mu);border:1px solid var(--bor)}
.b-wn{background:#2a1a00;color:var(--wn);border:1px solid var(--wn)}
.field{display:flex;flex-direction:column;gap:4px;margin-bottom:10px}
.field label{font-size:11px;color:var(--mu);font-weight:600;text-transform:uppercase;letter-spacing:.05em}
.field input,.field select{background:#0d1e40;border:1px solid var(--bor);border-radius:6px;color:var(--tx);padding:6px 9px;font-size:13px;font-family:inherit}
.field input:focus,.field select:focus{outline:none;border-color:var(--acc)}
#msg{display:none;border-radius:8px;padding:8px 12px;margin-bottom:14px;font-size:13px}
.msg-ok{display:block!important;background:#0f2318;color:var(--ok);border:1px solid #166534}
.msg-err{display:block!important;background:#2a0a0a;color:var(--al);border:1px solid #991b1b}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px;margin-bottom:20px}
.station-card{background:var(--sur);border:1px solid var(--bor);border-radius:12px;overflow:hidden}
details summary{cursor:pointer;list-style:none}
details summary::-webkit-details-marker{display:none}
.disc-row td{padding:5px 8px;border-bottom:1px solid var(--bor)}
.disc-row:hover td{background:rgba(23,52,95,.35)}
table{border-collapse:collapse;width:100%}
th{color:var(--mu);font-size:11px;text-transform:uppercase;border-bottom:1px solid var(--bor);padding:6px 8px;text-align:left}
td{padding:6px 8px}
.srv-section{margin-bottom:12px;padding:10px;background:#0a1a36;border-radius:8px;border:1px solid var(--bor)}
.srv-section h4{font-size:12px;color:var(--acc);margin-bottom:8px;font-weight:700}
.empty-state{text-align:center;padding:36px 20px;color:var(--mu)}
.empty-state .es-icon{font-size:28px;margin-bottom:8px;display:block;opacity:.5}
.empty-state .es-title{font-size:13px;font-weight:600;color:var(--tx);margin-bottom:4px}
.empty-state .es-sub{font-size:12px;line-height:1.5}
</style>
</head>
<body>
<header>
  <h1>🎙 AzuraCast</h1>
  <a href="/" class="btn bg bs">← Back</a>
</header>
<div class="main">
  <div id="msg"></div>
  <div id="cards" class="grid"></div>

  <!-- Config card -->
  <div class="card">
    <details id="cfg-details">
      <summary class="ch" style="justify-content:space-between;cursor:pointer">
        <span>⚙ Configure Servers</span>
        <span style="font-size:11px;color:var(--mu);font-weight:400">click to expand</span>
      </summary>
      <div class="cb">
        <!-- Existing servers -->
        <div id="srv-list"></div>

        <!-- Add server form -->
        <div class="card" style="margin-top:12px">
          <div class="ch">Add / Discover Server</div>
          <div class="cb">
            <div class="field"><label>AzuraCast URL</label>
              <input id="az-url" type="url" placeholder="https://radio.example.com">
            </div>
            <div class="field"><label>API Key (optional)</label>
              <input id="az-key" type="text" placeholder="Leave blank for public API">
            </div>
            <button class="btn bp" onclick="doDiscover(this)">🔍 Discover Stations</button>
            <div id="disc-result" style="margin-top:12px"></div>
          </div>
        </div>
      </div>
    </details>
  </div>
</div>

<script nonce="{{csp_nonce()}}">
(function(){
'use strict';

var _progData = {};   // key → {elapsed, duration, fetchedAt}
var _progTimer = null;
var _refreshTimer = null;
var _inputs = [];

function _getCsrf(){
  return (document.querySelector('meta[name="csrf-token"]')||{}).content
      || (document.cookie.match(/(?:^|;[ \t]*)csrf_token=([^;]+)/)||[])[1]
      || '';
}

function _post(url, data){
  return fetch(url, {
    method: 'POST',
    headers: {'Content-Type':'application/json','X-CSRFToken': _getCsrf()},
    credentials: 'same-origin',
    body: JSON.stringify(data)
  }).then(function(r){ return r.json(); });
}

function _showMsg(txt, ok){
  var el = document.getElementById('msg');
  el.textContent = txt;
  el.className = ok ? 'msg-ok' : 'msg-err';
}

function _fmtSecs(s){
  if(!s || s < 0) return '0:00';
  var m = Math.floor(s/60), sec = s%60;
  return m + ':' + (sec < 10 ? '0' : '') + sec;
}

function _safeKey(k){ return k.replace(/[^a-zA-Z0-9]/g,'_'); }

function _renderCard(s){
  var key = _safeKey(s.server_id + '_' + s.station_id);
  var onlineBadge = s.is_online
    ? '<span class="badge b-ok">Online</span>'
    : '<span class="badge b-al">Offline</span>';
  var liveBadge = s.is_live
    ? '<span class="badge b-wn" style="margin-right:4px">🎤 Live</span>'
    : '';
  var listeners = '<span class="badge b-mu" style="margin-right:4px">'
    + (s.listeners||0) + ' 👥</span>';

  var np = s.now_playing || {};
  var title = np.title || (s.is_online ? '—' : '');
  var artist = np.artist || '';
  var album = np.album || '';
  var artistLine = [artist, album].filter(Boolean).join(' · ') || '&nbsp;';

  var elapsed = np.elapsed || 0;
  var duration = np.duration || 0;
  var pct = duration > 0 ? Math.min(100, Math.round(elapsed/duration*100)) : 0;

  _progData[key] = { elapsed: elapsed, duration: duration, fetchedAt: Date.now()/1000 };

  var playlistLine = '';
  if(s.is_live && s.streamer_name){
    playlistLine = '🎤 Live: ' + _esc(s.streamer_name);
  } else if(np.playlist){
    playlistLine = '📋 ' + _esc(np.playlist) + ' · AutoDJ';
  }

  var nextInfo = '';
  var nxt = s.playing_next || {};
  var nxtText = nxt.text || ([nxt.title, nxt.artist].filter(Boolean).join(' — '));
  if(nxtText) nextInfo = 'Next: ' + _esc(nxtText);

  var inputLine = s.input_name
    ? 'Input: <span style="color:var(--acc)">' + _esc(s.input_name) + '</span>'
    : '<em>not mapped</em>';

  var errorLine = s.error
    ? '<div style="color:var(--al);font-size:11px;margin-top:6px">⚠ ' + _esc(s.error) + '</div>'
    : '';

  return '<div class="station-card">'
    + '<div class="ch" style="justify-content:space-between">'
    + '<span>🎙 ' + _esc(s.station_name) + '</span>'
    + '<span>' + liveBadge + listeners + onlineBadge + '</span>'
    + '</div>'
    + '<div class="cb">'
    + '<div style="font-size:14px;font-weight:600;color:var(--tx);margin-bottom:2px">' + _esc(title) + '</div>'
    + '<div style="font-size:12px;color:var(--mu);margin-bottom:8px">' + artistLine + '</div>'
    + '<div style="background:var(--bor);border-radius:3px;height:4px;margin-bottom:4px">'
    + '<div id="prog_' + key + '" style="background:var(--acc);border-radius:3px;height:4px;width:' + pct + '%"></div>'
    + '</div>'
    + '<div style="display:flex;justify-content:space-between;font-size:11px;color:var(--mu);margin-bottom:8px">'
    + '<span id="elapsed_' + key + '">' + _fmtSecs(elapsed) + '</span>'
    + '<span>' + _fmtSecs(duration) + '</span>'
    + '</div>'
    + (playlistLine ? '<div style="font-size:11px;color:var(--mu);margin-bottom:6px">' + playlistLine + '</div>' : '')
    + (nextInfo ? '<div style="font-size:11px;color:var(--mu);margin-bottom:6px">' + nextInfo + '</div>' : '')
    + '<div style="margin-top:8px;font-size:11px;color:var(--mu)">' + inputLine + '</div>'
    + errorLine
    + '</div>'
    + '</div>';
}

function _esc(s){
  return String(s||'')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function _renderCards(stations){
  var el = document.getElementById('cards');
  if(!stations || !stations.length){
    el.innerHTML = '<div class="empty-state"><span class="es-icon">📻</span>'
      +'<div class="es-title">No stations configured yet</div>'
      +'<div class="es-sub">Expand the <strong>Servers &amp; Configuration</strong> panel below to discover and add AzuraCast stations.</div></div>';
    return;
  }
  el.innerHTML = stations.map(_renderCard).join('');
}

function _renderSrvList(servers, stations){
  var el = document.getElementById('srv-list');
  if(!servers || !servers.length){
    el.innerHTML = '<div class="empty-state" style="padding:24px 20px"><span class="es-icon">🔌</span>'
      +'<div class="es-title">No AzuraCast servers added yet</div>'
      +'<div class="es-sub">Enter your server URL and API key in the form below and click <strong>🔍 Discover Stations</strong>.</div></div>';
    return;
  }
  var html = '';
  servers.forEach(function(srv){
    var srvStations = stations.filter(function(s){ return s.server_id === srv.id; });
    html += '<div class="srv-section">';
    html += '<div style="display:flex;justify-content:space-between;align-items:center">'
      + '<h4>' + _esc(srv.url) + '</h4>'
      + '<button class="btn bd bs srv-rm-btn" data-srv-id="' + _esc(srv.id) + '">Remove Server</button>'
      + '</div>';
    if(srvStations.length){
      html += '<table style="margin-top:6px"><thead><tr>'
        + '<th>Station</th><th>Display Name</th><th>Input</th><th>Silence Alert</th><th></th>'
        + '</tr></thead><tbody>';
      srvStations.forEach(function(s){
        html += '<tr>'
          + '<td>' + _esc(s.station_id) + '</td>'
          + '<td>' + _esc(s.station_name) + '</td>'
          + '<td>' + (s.input_name ? _esc(s.input_name) : '<em>none</em>') + '</td>'
          + '<td>' + (s.silence_alert ? '✓' : '—') + '</td>'
          + '<td><button class="btn bd bs sta-rm-btn"'
          + ' data-srv-id="' + _esc(srv.id) + '"'
          + ' data-sta-id="' + _esc(s.station_id) + '">Remove</button></td>'
          + '</tr>';
      });
      html += '</tbody></table>';
    } else {
      html += '<div style="color:var(--mu);font-size:12px;margin-top:4px">No stations.</div>';
    }
    html += '</div>';
  });
  el.innerHTML = html;

  el.addEventListener('click', function(e){
    var rmSrv = e.target.closest('.srv-rm-btn');
    if(rmSrv){ doRemoveServer(rmSrv.dataset.srvId); return; }
    var rmSta = e.target.closest('.sta-rm-btn');
    if(rmSta){ doRemoveStation(rmSta.dataset.srvId, rmSta.dataset.staId); }
  });
}

function loadStatus(){
  fetch('/api/azuracast/status', {credentials:'same-origin'})
    .then(function(r){ return r.json(); })
    .then(function(d){
      _renderCards(d.stations || []);
      _renderSrvList(d.servers || [], d.stations || []);
    })
    .catch(function(e){ _showMsg('Load error: '+e, false); });
}

function _tickProgress(){
  var now = Date.now()/1000;
  Object.keys(_progData).forEach(function(key){
    var p = _progData[key];
    if(!p.duration) return;
    var elapsed = p.elapsed + (now - p.fetchedAt);
    elapsed = Math.min(elapsed, p.duration);
    var pct = Math.round(elapsed / p.duration * 100);
    var progEl = document.getElementById('prog_' + key);
    var elEl = document.getElementById('elapsed_' + key);
    if(progEl) progEl.style.width = pct + '%';
    if(elEl) elEl.textContent = _fmtSecs(Math.floor(elapsed));
  });
}

function _startTimers(){
  if(_progTimer) clearInterval(_progTimer);
  if(_refreshTimer) clearInterval(_refreshTimer);
  _progTimer = setInterval(_tickProgress, 1000);
  _refreshTimer = setInterval(loadStatus, 15000);
}

// ── Discovery ────────────────────────────────────────────────────────────────

function _loadInputs(cb){
  fetch('/api/azuracast/inputs', {credentials:'same-origin'})
    .then(function(r){ return r.json(); })
    .then(function(d){ _inputs = d.inputs || []; if(cb) cb(); })
    .catch(function(){ _inputs = []; if(cb) cb(); });
}

function _inputSelect(id){
  var opts = '<option value="">— none —</option>';
  _inputs.forEach(function(i){
    opts += '<option value="' + _esc(i.name) + '">' + _esc(i.name) + '</option>';
  });
  return '<select id="' + id + '">' + opts + '</select>';
}

function doDiscover(btn){
  var url = document.getElementById('az-url').value.trim();
  var key = document.getElementById('az-key').value.trim();
  if(!url){ _showMsg('Enter a server URL first.', false); return; }

  document.getElementById('disc-result').innerHTML
    = '<div style="color:var(--mu)">Discovering…</div>';
  _btnLoad(btn);

  _loadInputs(function(){
    _post('/api/azuracast/discover', {url:url, api_key:key})
      .then(function(d){
        _btnReset(btn);
        if(!d.ok){ _showMsg(d.error||'Discovery failed', false);
          document.getElementById('disc-result').innerHTML=''; return; }
        var stations = d.stations||[];
        if(!stations.length){
          document.getElementById('disc-result').innerHTML
            = '<div style="color:var(--mu)">No stations found.</div>';
          return;
        }
        var html = '<table><thead><tr>'
          + '<th>ID</th><th>Name</th><th>Shortcode</th><th>Listeners</th><th>Status</th><th></th>'
          + '</tr></thead><tbody>';
        stations.forEach(function(s, i){
          html += '<tr class="disc-row">'
            + '<td>' + _esc(s.id) + '</td>'
            + '<td>' + _esc(s.name) + '</td>'
            + '<td>' + _esc(s.shortcode) + '</td>'
            + '<td>' + (s.listeners||0) + '</td>'
            + '<td>' + (s.is_online ? '<span class="badge b-ok">Online</span>' : '<span class="badge b-al">Offline</span>') + '</td>'
            + '<td><button class="btn bp bs disc-add-btn"'
            + ' data-idx="' + i + '"'
            + ' data-id="' + _esc(String(s.id)) + '"'
            + ' data-name="' + _esc(s.name) + '"'
            + '>Add</button></td>'
            + '</tr>'
            + '<tr id="add-form-' + i + '" style="display:none"><td colspan="6">'
            + '<div style="padding:8px;background:#0a1a36;border-radius:8px">'
            + '<div class="field"><label>Display Name</label>'
            + '<input id="dn-' + i + '" type="text" value="' + _esc(s.name) + '"></div>'
            + '<div class="field"><label>Link to Input</label>'
            + _inputSelect('inp-' + i)
            + '</div>'
            + '<div class="field" style="flex-direction:row;align-items:center;gap:8px">'
            + '<input type="checkbox" id="sa-' + i + '" checked>'
            + '<label for="sa-' + i + '" style="text-transform:none;letter-spacing:0;color:var(--tx)">Alert when AzuraCast is online but input is silent</label>'
            + '</div>'
            + '<button class="btn bp bs" onclick="doAddStation('
            + i + ',\'' + _esc(String(s.id)) + '\')">Confirm Add</button>'
            + ' <button class="btn bg bs" onclick="document.getElementById(\'add-form-' + i + '\').style.display=\'none\'">Cancel</button>'
            + '</div>'
            + '</td></tr>';
        });
        html += '</tbody></table>';
        var el = document.getElementById('disc-result');
        el.innerHTML = html;
        el.addEventListener('click', function(ev){
          var btn = ev.target.closest('.disc-add-btn');
          if(btn){
            var idx = btn.dataset.idx;
            var row = document.getElementById('add-form-' + idx);
            if(row) row.style.display = (row.style.display === 'none' ? '' : 'none');
          }
        });
      })
      .catch(function(e){ _btnReset(btn); _showMsg('Discover error: '+e, false); });
  });
}

function doAddStation(idx, stationId){
  var url    = document.getElementById('az-url').value.trim();
  var apiKey = document.getElementById('az-key').value.trim();
  var dn     = (document.getElementById('dn-'  + idx)||{}).value || '';
  var inp    = (document.getElementById('inp-' + idx)||{}).value || '';
  var sa     = !!(document.getElementById('sa-'  + idx)||{}).checked;

  _post('/api/azuracast/station/add', {
    url: url, api_key: apiKey,
    station_id: stationId,
    display_name: dn,
    input_name: inp,
    silence_alert: sa
  }).then(function(d){
    if(d.ok){ _showMsg('Station added.', true); loadStatus();
      document.getElementById('disc-result').innerHTML='';
      document.getElementById('az-url').value='';
      document.getElementById('az-key').value='';
    } else { _showMsg(d.error||'Add failed', false); }
  }).catch(function(e){ _showMsg('Error: '+e, false); });
}

function doRemoveStation(srvId, staId){
  _ssConfirm('Remove station ' + staId + '?',function(){
  _post('/api/azuracast/station/remove', {server_id: srvId, station_id: staId})
    .then(function(d){
      if(d.ok){ _showMsg('Station removed.', true); loadStatus(); }
      else { _showMsg(d.error||'Remove failed', false); }
    }).catch(function(e){ _showMsg('Error: '+e, false); });
  });
}

function doRemoveServer(srvId){
  _ssConfirm('Remove this server and all its stations?',function(){
  _post('/api/azuracast/server/remove', {server_id: srvId})
    .then(function(d){
      if(d.ok){ _showMsg('Server removed.', true); loadStatus(); }
      else { _showMsg(d.error||'Remove failed', false); }
    }).catch(function(e){ _showMsg('Error: '+e, false); });
  });
}

// ── Init ─────────────────────────────────────────────────────────────────────
loadStatus();
_startTimers();

})();
</script>
</body>
</html>
"""

_HUB_TPL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<title>AzuraCast Overview — SignalScope</title>
<style nonce="{{csp_nonce()}}">
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(circle at top,#12376f 0%,var(--bg) 38%,#05101f 100%);color:var(--tx);font-family:system-ui,sans-serif;font-size:13px;min-height:100vh}
header{background:linear-gradient(180deg,rgba(10,31,65,.96),rgba(9,24,48,.96));border-bottom:1px solid var(--bor);padding:12px 20px;display:flex;align-items:center;gap:12px}
header h1{font-size:16px;font-weight:700;color:var(--acc);flex:1}
.main{padding:20px 24px;max-width:1400px;margin:0 auto}
.btn{border:none;border-radius:8px;padding:5px 12px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit}
.btn:hover{filter:brightness(1.15)}
.bg{background:#17345f;color:var(--tx)}
.bs{padding:3px 9px;font-size:12px}
.card{background:var(--sur);border:1px solid var(--bor);border-radius:12px;overflow:hidden;margin-bottom:16px}
.ch{padding:9px 14px;display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--bor);background:linear-gradient(180deg,#143766,#102b54);font-size:12px;font-weight:700;color:var(--acc);text-transform:uppercase;letter-spacing:.06em}
.cb{padding:0}
.badge{display:inline-block;border-radius:5px;padding:2px 7px;font-size:11px;font-weight:700}
.b-ok{background:#0f2318;color:var(--ok);border:1px solid var(--ok)}
.b-al{background:#2a0a0a;color:var(--al);border:1px solid var(--al)}
.b-mu{background:#0d2346;color:var(--mu);border:1px solid var(--bor)}
.b-wn{background:#2a1a00;color:var(--wn);border:1px solid var(--wn)}
table{border-collapse:collapse;width:100%}
th{color:var(--mu);font-size:11px;text-transform:uppercase;border-bottom:1px solid var(--bor);padding:8px 12px;text-align:left;white-space:nowrap}
td{padding:7px 12px;border-bottom:1px solid rgba(23,52,95,.4);vertical-align:middle}
tr:hover td{background:rgba(23,52,95,.35)}
.site-sep td{background:rgba(10,25,55,.7);color:var(--acc);font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;padding:5px 12px}
#msg{display:none;border-radius:8px;padding:8px 12px;margin-bottom:14px;font-size:13px}
.msg-err{display:block!important;background:#2a0a0a;color:var(--al);border:1px solid #991b1b}
.empty-state{text-align:center;padding:48px 20px;color:var(--mu)}
.empty-state .es-icon{font-size:32px;margin-bottom:10px;display:block;opacity:.5}
.empty-state .es-title{font-size:14px;font-weight:600;color:var(--tx);margin-bottom:6px}
.empty-state .es-sub{font-size:12px;line-height:1.55}
</style>
</head>
<body>
<header>
  <h1>🎙 AzuraCast Overview</h1>
  <a href="/" class="btn bg bs">← Back</a>
</header>
<div class="main">
  <div id="msg"></div>
  <div class="card">
    <div class="ch">All Sites</div>
    <div class="cb">
      <table>
        <thead>
          <tr>
            <th>Site</th>
            <th>Station</th>
            <th>Online</th>
            <th>Mode</th>
            <th>Now Playing</th>
            <th>Listeners</th>
            <th>Last Updated</th>
          </tr>
        </thead>
        <tbody id="hub-tbody">
          <tr><td colspan="7" style="color:var(--mu);padding:20px;text-align:center">Loading…</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>
<script nonce="{{csp_nonce()}}">
(function(){
'use strict';

function _esc(s){
  return String(s||'')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function _age(ts){
  if(!ts) return '—';
  var s = Math.floor(Date.now()/1000 - ts);
  if(s < 60)   return s + 's ago';
  if(s < 3600) return Math.floor(s/60) + 'm ago';
  return Math.floor(s/3600) + 'h ago';
}

function loadOverview(){
  fetch('/api/hub/azuracast/overview', {credentials:'same-origin'})
    .then(function(r){ return r.json(); })
    .then(function(d){
      var sites = d.sites || {};
      var rows = '';
      var siteNames = Object.keys(sites).sort();
      if(!siteNames.length){
        rows = '<tr><td colspan="7" style="color:var(--mu);padding:20px;text-align:center">No client data yet.</td></tr>';
      } else {
        siteNames.forEach(function(site){
          var payload = sites[site] || {};
          var stations = payload.stations || [];
          rows += '<tr class="site-sep"><td colspan="7">📡 ' + _esc(site) + '</td></tr>';
          if(!stations.length){
            rows += '<tr><td colspan="7" style="color:var(--mu)">No stations reported.</td></tr>';
            return;
          }
          stations.forEach(function(s){
            var np = s.now_playing || {};
            var nowPlayingText = [np.title, np.artist].filter(Boolean).join(' — ') || '—';
            var modeLabel = s.is_live
              ? '<span class="badge b-wn">🎤 ' + _esc(s.streamer_name||'Live') + '</span>'
              : '<span style="color:var(--mu)">AutoDJ</span>';
            var onlineBadge = s.is_online
              ? '<span class="badge b-ok">Online</span>'
              : '<span class="badge b-al">Offline</span>';
            rows += '<tr>'
              + '<td style="color:var(--mu)">' + _esc(site) + '</td>'
              + '<td><strong>' + _esc(s.station_name) + '</strong></td>'
              + '<td>' + onlineBadge + '</td>'
              + '<td>' + modeLabel + '</td>'
              + '<td style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'
              + _esc(nowPlayingText) + '</td>'
              + '<td>' + (s.listeners||0) + '</td>'
              + '<td style="color:var(--mu)">' + _age(s.last_updated) + '</td>'
              + '</tr>';
          });
        });
      }
      document.getElementById('hub-tbody').innerHTML = rows;
    })
    .catch(function(e){
      var el = document.getElementById('msg');
      el.textContent = 'Load error: ' + e;
      el.className = 'msg-err';
    });
}

loadOverview();
setInterval(loadOverview, 15000);
})();
</script>
</body>
</html>
"""


# ─── register() ──────────────────────────────────────────────────────────────

def register(app, ctx):
    global _monitor, _app_dir

    login_required = ctx["login_required"]
    csrf_protect   = ctx["csrf_protect"]
    monitor        = ctx["monitor"]
    hub_server     = ctx["hub_server"]
    mobile_api_req = ctx.get("mobile_api_required", ctx["login_required"])

    _monitor = monitor
    _app_dir = os.path.dirname(os.path.abspath(__file__))

    # Load config from disk
    with _cfg_lock:
        loaded = _load_cfg()
        _cfg.update(loaded)

    _log("[AzuraCast] Plugin loaded")

    cfg_ss = monitor.app_cfg
    mode   = getattr(getattr(cfg_ss, "hub", None), "mode", "standalone") or "standalone"

    # Start pollers
    _start_pollers()

    # ── Client-side routes ────────────────────────────────────────────────────

    @app.get("/azuracast")
    @login_required
    def azuracast_page():
        if hub_server is not None and mode == "hub":
            return redirect("/hub/azuracast")
        return render_template_string(_CLIENT_TPL)

    @app.get("/api/azuracast/status")
    @login_required
    def azuracast_api_status():
        cfg_now = _get_cfg()
        servers_out = []
        for srv in cfg_now.get("servers", []):
            servers_out.append({
                "id":      srv.get("id", ""),
                "url":     srv.get("url", ""),
                "has_key": bool(srv.get("api_key", "")),
            })
        with _lock:
            stations_out = list(_station_status.values())
        return jsonify({"servers": servers_out, "stations": stations_out})

    @app.get("/api/azuracast/inputs")
    @login_required
    def azuracast_api_inputs():
        return jsonify({"inputs": _get_inputs_list(monitor)})

    @app.post("/api/azuracast/discover")
    @login_required
    @csrf_protect
    def azuracast_discover():
        body    = request.get_json(silent=True) or {}
        url     = str(body.get("url", "")).strip().rstrip("/")
        api_key = str(body.get("api_key", "")).strip()

        if not url.startswith(("http://", "https://")):
            return jsonify({"ok": False, "error": "URL must start with http:// or https://"}), 400

        try:
            data = _az_request(url, api_key, "/stations", timeout=10)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 200

        stations_out = []
        for s in (data if isinstance(data, list) else []):
            stations_out.append({
                "id":        str(s.get("id", "")),
                "name":      s.get("name", ""),
                "shortcode": s.get("shortcode", ""),
                "listen_url": s.get("listen_url", ""),
                "listeners": int((s.get("listeners") or {}).get("current", 0)),
                "is_online": bool(s.get("is_online", True)),
            })
        return jsonify({"ok": True, "stations": stations_out})

    @app.post("/api/azuracast/station/add")
    @login_required
    @csrf_protect
    def azuracast_station_add():
        body        = request.get_json(silent=True) or {}
        url         = str(body.get("url", "")).strip().rstrip("/")
        api_key     = str(body.get("api_key", "")).strip()
        station_id  = str(body.get("station_id", "")).strip()
        display_name = str(body.get("display_name", "")).strip()
        input_name  = str(body.get("input_name", "")).strip()
        silence_alert = bool(body.get("silence_alert", False))

        if not url.startswith(("http://", "https://")):
            return jsonify({"ok": False, "error": "URL must start with http:// or https://"}), 400
        if not station_id:
            return jsonify({"ok": False, "error": "station_id is required"}), 400

        cfg_now = _get_cfg()
        servers = cfg_now.get("servers", [])

        # Find or create server entry
        srv = next((s for s in servers if s.get("url", "").rstrip("/") == url), None)
        if srv is None:
            srv = {
                "id":       uuid.uuid4().hex[:8],
                "url":      url,
                "api_key":  api_key,
                "stations": [],
            }
            servers.append(srv)
        else:
            # Update API key if provided
            if api_key:
                srv["api_key"] = api_key

        # Check not already added
        existing = next((s for s in srv.get("stations", [])
                         if str(s.get("station_id", "")) == station_id), None)
        if existing:
            return jsonify({"ok": False, "error": "Station already configured"}), 400

        srv.setdefault("stations", []).append({
            "station_id":   station_id,
            "display_name": display_name,
            "input_name":   input_name,
            "silence_alert": silence_alert,
        })

        cfg_now["servers"] = servers
        _set_cfg(cfg_now)
        _start_pollers()
        return jsonify({"ok": True})

    @app.post("/api/azuracast/station/remove")
    @login_required
    @csrf_protect
    def azuracast_station_remove():
        body       = request.get_json(silent=True) or {}
        server_id  = str(body.get("server_id", "")).strip()
        station_id = str(body.get("station_id", "")).strip()

        if not server_id or not station_id:
            return jsonify({"ok": False, "error": "server_id and station_id required"}), 400

        cfg_now = _get_cfg()
        changed = False
        for srv in cfg_now.get("servers", []):
            if srv.get("id") == server_id:
                before = len(srv.get("stations", []))
                srv["stations"] = [
                    s for s in srv.get("stations", [])
                    if str(s.get("station_id", "")) != station_id
                ]
                changed = len(srv["stations"]) < before
                break

        if not changed:
            return jsonify({"ok": False, "error": "Station not found"}), 404

        # Remove cached status
        key = f"{server_id}::{station_id}"
        with _lock:
            _station_status.pop(key, None)
        _silence_alerted.discard(key)

        _set_cfg(cfg_now)
        _start_pollers()
        return jsonify({"ok": True})

    @app.post("/api/azuracast/server/remove")
    @login_required
    @csrf_protect
    def azuracast_server_remove():
        body      = request.get_json(silent=True) or {}
        server_id = str(body.get("server_id", "")).strip()

        if not server_id:
            return jsonify({"ok": False, "error": "server_id required"}), 400

        cfg_now = _get_cfg()
        before  = len(cfg_now.get("servers", []))
        cfg_now["servers"] = [
            s for s in cfg_now.get("servers", [])
            if s.get("id") != server_id
        ]
        if len(cfg_now["servers"]) == before:
            return jsonify({"ok": False, "error": "Server not found"}), 404

        # Remove cached statuses for this server
        with _lock:
            for key in [k for k in _station_status if k.startswith(f"{server_id}::")]:
                _station_status.pop(key, None)
                _silence_alerted.discard(key)

        # Stop the poller for this server
        with _poller_lock:
            if server_id in _poller_threads:
                _poller_threads[server_id][1].set()
                _poller_threads.pop(server_id, None)

        _set_cfg(cfg_now)
        return jsonify({"ok": True})

    @app.post("/api/azuracast/config/save")
    @login_required
    @csrf_protect
    def azuracast_config_save():
        body = request.get_json(silent=True) or {}
        cfg_now = _get_cfg()

        if "poll_interval" in body:
            try:
                pi = int(body["poll_interval"])
                if 5 <= pi <= 300:
                    cfg_now["poll_interval"] = pi
            except (TypeError, ValueError):
                pass

        _set_cfg(cfg_now)
        _start_pollers()
        return jsonify({"ok": True})

    # ── Mobile route ─────────────────────────────────────────────────────────

    @app.get("/api/mobile/azuracast/stations")
    @mobile_api_req
    def azuracast_mobile_stations():
        if hub_server is not None:
            # Hub: aggregate all sites
            with _lock:
                all_statuses = []
                for site, payload in _client_statuses.items():
                    for st in (payload.get("stations") or []):
                        entry = dict(st)
                        entry["_site"] = site
                        all_statuses.append(entry)
            return jsonify({"stations": all_statuses})
        else:
            with _lock:
                return jsonify({"stations": list(_station_status.values())})

    # ── Hub-only routes ───────────────────────────────────────────────────────

    if hub_server is not None:

        @app.get("/hub/azuracast")
        @login_required
        def hub_azuracast_page():
            return render_template_string(_HUB_TPL)

        @app.get("/api/hub/azuracast/overview")
        @login_required
        def hub_azuracast_overview():
            with _lock:
                sites_copy = {k: dict(v) for k, v in _client_statuses.items()}
            return jsonify({"sites": sites_copy})

        @app.post("/api/azuracast/client_status")
        def azuracast_client_status():
            raw    = request.get_data()
            secret = (getattr(getattr(monitor.app_cfg, "hub", None),
                              "secret_key", None) or "").strip()

            if secret:
                if not _check_sig(secret, raw):
                    abort(403)
            else:
                site_h = request.headers.get("X-Site", "").strip()
                sdata  = hub_server._sites.get(site_h, {})
                if not sdata.get("_approved"):
                    abort(403)

            try:
                payload = json.loads(raw.decode("utf-8", errors="replace"))
            except Exception:
                abort(400)

            site = str(payload.get("site", "")).strip()
            if not site:
                site = request.headers.get("X-Site", "unknown").strip()

            with _lock:
                _client_statuses[site] = payload

            return jsonify({"ok": True})

    # ── Hub reporter: runs when we have a hub to report to ───────────────────

    hub_url = getattr(getattr(cfg_ss, "hub", None), "hub_url", "").strip()
    if hub_url and mode in ("client", "both", "standalone"):
        t_reporter = threading.Thread(
            target=_hub_reporter_thread,
            args=(monitor,),
            daemon=True,
            name="AzuraCastHubReporter",
        )
        t_reporter.start()

    _log("[AzuraCast] Plugin registered successfully")
