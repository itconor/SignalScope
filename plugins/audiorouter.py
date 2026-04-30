# audiorouter.py — Broadcast Audio Router plugin for SignalScope
# Drop into plugins/. Requires ffmpeg on client nodes.
#
# Routes audio from any monitored input on any connected SignalScope site to a
# Livewire multicast output on any connected site.
#
# Architecture:
#   Hub stores route config and manages relay slots for cross-site routes.
#   Clients poll /api/audiorouter/poll every 8 s, start/stop ffmpeg processes.
#   Same-site: client runs ffmpeg locally (input → Livewire multicast).
#   Cross-site: source client POSTs PCM chunks to hub relay slot;
#               dest client fetches from hub relay, pipes to ffmpeg → Livewire.

SIGNALSCOPE_PLUGIN = {
    "id":       "audiorouter",
    "label":    "Audio Router",
    "url":      "/hub/audiorouter",
    "icon":     "🔀",
    "hub_only": True,
    "version":  "1.2.6",
}

import hashlib
import hmac as _hmac
import json
import os
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from flask import jsonify, render_template_string, request

try:
    import numpy as _np
    _HAVE_NP = True
except ImportError:
    _HAVE_NP = False

# ── Paths ──────────────────────────────────────────────────────────────────────
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_CFG_PATH = os.path.join(_BASE_DIR, "audiorouter_cfg.json")

# ── Module-level state ─────────────────────────────────────────────────────────
_cfg_lock   = threading.Lock()
_status_lock = threading.Lock()

# Per-route runtime status reported by clients: route_id → {site_name → {status, error, direct_url, ts}}
_route_status: dict = {}

# Per-route relay slot IDs managed by hub: route_id → slot_id
# NOTE: slots are kept for backward-compat only; the actual audio relay now
# flows through _hub_broadcasters (hub-side) rather than the scanner relay.
_route_slots: dict = {}
_slots_lock = threading.Lock()

# Hub-side broadcasters for relay audio: route_id → _StreamBroadcaster
# Created lazily when the first push arrives or the first consumer connects.
# No scanner-slot inactivity-timeout issues — lives as long as the hub process.
_hub_broadcasters: dict = {}
_hbcast_lock = threading.Lock()

# Active ffmpeg processes on client: route_id → subprocess.Popen
_active_procs: dict = {}
_procs_lock = threading.Lock()

# Active source PCM reader threads: route_id → threading.Thread
_active_src_threads: dict = {}
_src_threads_lock = threading.Lock()

# Active stream broadcasters on source clients: route_id → _StreamBroadcaster
_active_broadcasters: dict = {}
_bcast_lock = threading.Lock()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _lw_multicast(stream_id: int) -> str:
    """Livewire multicast address formula."""
    return f"239.192.{(stream_id >> 8) & 0xFF}.{stream_id & 0xFF}"


def _sign_chunk(secret: str, data: bytes, ts: float) -> str:
    key = hashlib.sha256(f"{secret}:signing".encode()).digest()
    msg = f"{ts:.0f}:".encode() + data
    return _hmac.new(key, msg, hashlib.sha256).hexdigest()


def _load_cfg() -> dict:
    try:
        with open(_CFG_PATH) as f:
            return json.load(f)
    except Exception:
        return {"routes": []}


def _save_cfg(data: dict):
    tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(_CFG_PATH), suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, _CFG_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _get_route(rid: str) -> dict | None:
    with _cfg_lock:
        cfg = _load_cfg()
    for r in cfg.get("routes", []):
        if r.get("id") == rid:
            return r
    return None


def _ffmpeg_rtp_url(stream_id: int) -> str:
    mc = _lw_multicast(stream_id)
    return f"rtp://{mc}:5004?ttl=15&buffer_size=65536"


def _input_type(device_index: str) -> str:
    """Classify a device_index string."""
    di = (device_index or "").lower().strip()
    if di.startswith(("http://", "https://", "srt://", "rtsp://")):
        return "network"
    if di.startswith("fm://"):
        return "fm"
    if di.startswith("dab://"):
        return "dab"
    return "alsa"


def _find_input(source_stream: str, monitor):
    """Return the InputConfig whose name matches source_stream, or None."""
    try:
        for inp in (getattr(monitor.app_cfg, "inputs", None) or []):
            if getattr(inp, "name", "") == source_stream:
                return inp
    except Exception:
        pass
    return None


def _stream_buf_chunks(inp, stop: threading.Event):
    """
    Generator that yields int16 LE STEREO PCM bytes from SignalScope's
    monitoring buffers.  Always outputs 2-channel interleaved s16le:

      • Stereo source  → reads _audio_buffer (L/R interleaved float32),
                         passes through as true stereo.
      • Mono source    → reads _stream_buffer (mono float32), duplicates
                         the single channel to both L and R.

    Using always-stereo output means the dest ffmpeg command can
    unconditionally use -ac 2, regardless of source type.

    Previous design read _stream_buffer for all inputs and applied a
    stereo-deinterleave to mono data, halving the sample count and
    producing double-speed "chipmunk" audio.  Fixed here.

    IMPORTANT: Both _stream_buffer and _audio_buffer default to None
    until the monitoring loop starts.  We wait (without crashing) until
    the buffer is initialised — a None here was the root cause of the
    periodic broadcaster-close / 8-second restart cycle that exhausted
    Waitress threads on the hub.
    """
    if not _HAVE_NP:
        return
    seen_seq = getattr(inp, "_live_chunk_seq", 0)
    while not stop.is_set():
        n_ch = int(getattr(inp, "_audio_channels", 1) or 1)
        # Stereo sources: _audio_buffer holds L/R interleaved float32 chunks.
        # Mono sources: _stream_buffer holds mono float32 chunks.
        buf = getattr(inp, "_audio_buffer" if n_ch == 2 else "_stream_buffer", None)
        if buf is None:
            # Monitoring loop hasn't started yet — wait and retry.
            stop.wait(0.1)
            continue
        cur_seq = getattr(inp, "_live_chunk_seq", seen_seq)
        if cur_seq <= seen_seq:
            stop.wait(0.05)
            continue
        n_new = min(cur_seq - seen_seq, len(buf))
        if n_new > 0:
            for c in list(buf)[-n_new:]:
                try:
                    arr = _np.asarray(c, dtype=_np.float32).ravel()
                    if n_ch != 2:
                        # Mono → duplicate to stereo (L=R)
                        arr = _np.column_stack([arr, arr]).ravel()
                    # arr is now always stereo-interleaved float32
                    yield (_np.clip(arr * 32767, -32768, 32767)
                           .astype(_np.int16).tobytes())
                except Exception:
                    pass
        seen_seq = cur_seq
        stop.wait(0.05)



# ── P2P stream broadcaster ─────────────────────────────────────────────────────

import collections as _collections


class _StreamBroadcaster:
    """Fan-out raw PCM chunks from one ffmpeg process to multiple consumers.

    Maintains a short ring buffer so a late-joining consumer (direct HTTP
    client) can start a few chunks in rather than stalling.
    """

    def __init__(self, maxchunks: int = 200):  # ~20 s at 9600 B/chunk
        self._lock  = threading.Lock()
        self._buf   = _collections.deque(maxlen=maxchunks)
        self._seq   = 0
        self._cond  = threading.Condition(self._lock)
        self.closed = False

    def push(self, chunk: bytes):
        with self._cond:
            self._buf.append((self._seq, chunk))
            self._seq += 1
            self._cond.notify_all()

    def close(self):
        with self._cond:
            self.closed = True
            self._cond.notify_all()

    def consumer(self, catchup: int = 5):
        """Generator that yields PCM chunks. Blocks waiting for new data."""
        with self._lock:
            # Start a few chunks back so the dest has a small pre-buffer
            start_seq = max(0, self._seq - catchup)
        seq = start_seq
        while True:
            with self._cond:
                if self.closed:
                    return
                available = [(s, c) for s, c in self._buf if s >= seq]
                if not available:
                    self._cond.wait(timeout=2.0)
                    if self.closed:
                        return
                    continue
            for s, chunk in sorted(available, key=lambda x: x[0]):
                seq = s + 1
                yield chunk

    def consumer_with_keepalive(self, catchup: int = 0, interval: float = 0.1):
        """Like consumer() but yields silence every `interval` s when no audio flows.

        This prevents Waitress from holding a worker thread indefinitely while
        waiting for the first real chunk.  Silence keeps the HTTP connection
        alive and ensures GeneratorExit is delivered promptly when the dest
        disconnects.  0.1 s silence = 9600 bytes at 48 kHz / 16-bit mono.
        """
        _SILENCE = bytes(9600)
        with self._lock:
            seq = max(0, self._seq - catchup)
        while True:
            to_yield = []
            with self._cond:
                if self.closed:
                    return
                available = [(s, c) for s, c in self._buf if s >= seq]
                if not available:
                    self._cond.wait(timeout=interval)
                    if self.closed:
                        return
                    available = [(s, c) for s, c in self._buf if s >= seq]
            if available:
                for s, chunk in sorted(available, key=lambda x: x[0]):
                    seq = s + 1
                    to_yield.append(chunk)
            else:
                to_yield.append(_SILENCE)
            for chunk in to_yield:
                yield chunk


def _compute_stream_token(secret: str, route_id: str) -> str:
    """Stable 32-char token for direct PCM stream auth (secret + route_id)."""
    key = (secret or "signalscope").encode()
    return _hmac.new(key, f"audiorouter:{route_id}".encode(), hashlib.sha256).hexdigest()[:32]


def _my_direct_url(cfg_ss, route_id: str, token: str) -> str:
    """Best-effort URL that peer nodes can use to pull PCM from this node directly."""
    import socket
    port = _SS_PORT
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        ip = "127.0.0.1"
    return f"http://{ip}:{port}/api/audiorouter/stream/{route_id}?token={token}"


def _probe_direct_url(url: str, timeout: float = 2.5) -> bool:
    """Return True if the peer's direct stream URL responds within timeout."""
    try:
        # Issue a HEAD-like GET that we immediately close — just verify reachability
        req = urllib.request.Request(url, headers={"X-Probe": "1"})
        resp = urllib.request.urlopen(req, timeout=timeout)
        # Any non-error response (200, 206, even 206 partial) means reachable
        resp.close()
        return True
    except Exception:
        return False


def _drain_stderr(proc, label: str, monitor):
    """Read ffmpeg stderr in a background thread and log on non-zero exit."""
    lines = []
    try:
        for raw in proc.stderr:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                lines.append(line)
                if len(lines) > 20:
                    lines.pop(0)
    except Exception:
        pass
    rc = proc.wait()
    if rc not in (0, -9, -15) and lines:
        monitor.log(f"[AudioRouter] {label} ffmpeg exit {rc}: {lines[-1]}")
    elif rc not in (0, -9, -15):
        monitor.log(f"[AudioRouter] {label} ffmpeg exit {rc} (no stderr)")


def _stop_route_proc(route_id: str):
    """Kill any running ffmpeg for this route_id (source or dest)."""
    with _procs_lock:
        proc = _active_procs.pop(route_id, None)
    if proc and proc.poll() is None:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass

    with _src_threads_lock:
        _active_src_threads.pop(route_id, None)


def _report_status(hub_url: str, site_name: str, route_id: str, status: str,
                   error: str = "", direct_url: str = ""):
    """POST status update to hub."""
    try:
        payload = json.dumps({
            "route_id":   route_id,
            "site":       site_name,
            "status":     status,
            "error":      error,
            "direct_url": direct_url,
        }).encode()
        req = urllib.request.Request(
            f"{hub_url}/api/audiorouter/client_status",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json", "X-Site": site_name},
        )
        urllib.request.urlopen(req, timeout=8).close()
    except Exception:
        pass


# ── Hub page template ──────────────────────────────────────────────────────────

_HUB_TPL = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Audio Router — SignalScope</title>
<link rel="icon" type="image/x-icon" href="/static/signalscope_icon.png">
<meta name="csrf-token" content="{{csrf_token()}}">
<style nonce="{{csp_nonce()}}">
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;font-size:13px;background:radial-gradient(circle at top,#12376f 0%,var(--bg) 38%,#05101f 100%);color:var(--tx);min-height:100vh}
header{background:linear-gradient(180deg,rgba(10,31,65,.96),rgba(9,24,48,.96));border-bottom:1px solid var(--bor);padding:12px 20px;display:flex;align-items:center;gap:12px}
a{color:var(--acc);text-decoration:none}
.btn{display:inline-flex;align-items:center;border:none;border-radius:8px;padding:5px 12px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;text-decoration:none;white-space:nowrap}
.btn:hover{filter:brightness(1.15)}
.bg{background:var(--bor);color:var(--tx)}.bp{background:var(--acc);color:#fff}.bd{background:var(--al);color:#fff}
.bs{padding:3px 9px;font-size:12px}
.nav-active{background:var(--acc)!important;color:#fff!important}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:600}
.b-ok{background:#0f2318;color:var(--ok);border:1px solid #166534}
.b-al{background:#2a0a0a;color:var(--al);border:1px solid #991b1b}
.b-mu{background:#0d1e40;color:var(--mu);border:1px solid var(--bor)}
.b-wn{background:#1f1200;color:var(--wn);border:1px solid #92400e}
main{padding:24px 20px 48px;max-width:1100px;margin:0 auto}
.ph{margin-bottom:20px}
.ph h1{font-size:22px;font-weight:800;letter-spacing:-.02em}
.ph p{color:var(--mu);margin-top:4px;font-size:12px}
.card{background:var(--sur);border:1px solid var(--bor);border-radius:12px;overflow:hidden;margin-bottom:16px}
.ch{padding:9px 14px;display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--bor);background:linear-gradient(180deg,#143766,#102b54);font-size:12px;font-weight:700;color:var(--acc);text-transform:uppercase;letter-spacing:.06em}
.ch-right{margin-left:auto}
.cb{padding:14px}
table{width:100%;border-collapse:collapse}
th{color:var(--mu);font-size:11px;text-transform:uppercase;letter-spacing:.05em;padding:6px 10px;border-bottom:1px solid var(--bor);text-align:left;font-weight:600}
td{padding:9px 10px;border-bottom:1px solid rgba(23,52,95,.35);font-size:13px;vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:rgba(23,52,95,.35)}
.mu{color:var(--mu)} .ts{font-size:11px;color:var(--mu)}
.field{display:flex;flex-direction:column;gap:4px;margin-bottom:12px}
.field label{font-size:11px;color:var(--mu);font-weight:600;text-transform:uppercase;letter-spacing:.05em}
input[type=text],input[type=number],select{background:#0d1e40;border:1px solid var(--bor);border-radius:6px;color:var(--tx);padding:6px 9px;font-size:13px;font-family:inherit}
input[type=text]:focus,input[type=number]:focus,select:focus{outline:none;border-color:var(--acc)}
.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
#msg{padding:10px 14px;border-radius:8px;margin-bottom:14px;display:none;font-weight:600;font-size:13px}
.msg-ok{background:#0f2318;color:var(--ok);border:1px solid #166534;display:block!important}
.msg-err{background:#2a0a0a;color:var(--al);border:1px solid #991b1b;display:block!important}
.empty{padding:32px;text-align:center;color:var(--mu);font-size:13px}
code{font-family:monospace;font-size:12px;color:var(--mu)}
.form-collapse{display:none}
.form-collapse.open{display:block}
.act-btns{display:flex;gap:6px}
.rcard{background:var(--sur);border:1px solid var(--bor);border-radius:12px;overflow:hidden;margin-bottom:12px}
.rcard-head{padding:10px 14px;display:flex;align-items:center;gap:10px;background:linear-gradient(180deg,#143766,#102b54);border-bottom:1px solid var(--bor)}
.rcard-name{font-size:14px;font-weight:700;flex:1}
.rcard-acts{display:flex;gap:6px;margin-left:auto}
.rcard-body{display:flex;align-items:flex-start;gap:0;padding:0}
.rcard-side{flex:1;padding:14px 16px}
.rcard-arrow{display:flex;align-items:center;justify-content:center;padding:14px 0;font-size:20px;color:var(--acc);flex-shrink:0;width:40px;border-left:1px solid rgba(23,52,95,.4);border-right:1px solid rgba(23,52,95,.4);background:rgba(23,52,95,.2);align-self:stretch}
.rcard-role{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--mu);margin-bottom:8px}
.rcard-site,.rcard-stream{font-size:12px;margin-bottom:4px;color:var(--mu)}
.rcard-site strong,.rcard-stream strong{color:var(--tx)}
.rcard-mc{margin-top:2px;margin-bottom:6px}
.rcard-st{margin-top:8px;display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.rcard-err{margin-top:6px;font-size:11px;color:var(--al);background:#2a0a0a;border:1px solid #991b1b;border-radius:4px;padding:3px 7px}
.via-tag{font-size:10px;background:#0d1e40;color:var(--mu);border:1px solid var(--bor);border-radius:999px;padding:1px 7px}
</style>
</head><body>
{{topnav("audiorouter")|safe}}
<main>
  <div class="ph">
    <h1>🔀 Audio Router</h1>
    <p>Route monitored inputs to Livewire multicast outputs across connected sites.</p>
  </div>
  <div id="msg"></div>

  <!-- Add Route card -->
  <div class="card">
    <div class="ch">
      Add Route
      <span class="ch-right">
        <button class="btn bg bs" id="toggle-form-btn">+ New Route</button>
      </span>
    </div>
    <div class="cb form-collapse" id="add-form">
      <div class="field">
        <label>Route Name</label>
        <input type="text" id="f-name" placeholder="e.g. Cool FM → Studio 1" style="max-width:400px">
      </div>
      <div class="grid2" style="margin-bottom:12px">
        <div class="field" style="margin-bottom:0">
          <label>Source Site</label>
          <select id="f-src-site">
            <option value="">— select —</option>
            {% for s in sites %}<option value="{{s|e}}">{{s|e}}</option>{% endfor %}
          </select>
        </div>
        <div class="field" style="margin-bottom:0">
          <label>Source Stream</label>
          <select id="f-src-stream">
            <option value="">— select source site first —</option>
          </select>
        </div>
      </div>
      <div class="grid2" style="margin-bottom:12px">
        <div class="field" style="margin-bottom:0">
          <label>Dest Site</label>
          <select id="f-dst-site">
            <option value="">— select —</option>
            {% for s in sites %}<option value="{{s|e}}">{{s|e}}</option>{% endfor %}
          </select>
        </div>
        <div class="field" style="margin-bottom:0">
          <label>Livewire Stream ID (1–65535)</label>
          <input type="number" id="f-lw-id" min="1" max="65535" placeholder="1001" style="max-width:200px">
        </div>
      </div>
      <div id="lw-addr-preview" class="ts" style="margin-bottom:14px"></div>
      <button class="btn bp" id="add-route-btn">Add Route</button>
    </div>
  </div>

  <!-- Routes table card -->
  <div class="card">
    <div class="ch">Routes</div>
    <div id="routes-wrap">
      <div class="empty">Loading&hellip;</div>
    </div>
  </div>
</main>
<script nonce="{{csp_nonce()}}">
(function(){
'use strict';
function _getCsrf(){
  return (document.querySelector('meta[name="csrf-token"]')||{}).content
      || (document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/)||[])[1]||'';
}
function _post(url,body){
  return fetch(url,{method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRFToken':_getCsrf()},
    body:JSON.stringify(body)});
}
function _del(url){
  return fetch(url,{method:'DELETE',credentials:'same-origin',
    headers:{'X-CSRFToken':_getCsrf()}});
}
function _patch(url,body){
  return fetch(url,{method:'PATCH',credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRFToken':_getCsrf()},
    body:JSON.stringify(body)});
}
function _esc(s){var d=document.createElement('div');d.textContent=String(s||'');return d.innerHTML;}
function _showMsg(txt,err){
  var el=document.getElementById('msg');
  el.textContent=txt;el.className=err?'msg-err':'msg-ok';
  setTimeout(function(){el.className='';el.textContent='';},5000);
}
function _lwMc(id){
  id=parseInt(id,10);
  if(isNaN(id)||id<1||id>65535) return '';
  return '239.192.'+((id>>8)&0xFF)+'.'+(id&0xFF)+':5004';
}

// Stream list cache: site → [stream names]
var _streamCache = {};

function _loadStreams(site,selEl,defVal){
  if(!site){selEl.innerHTML='<option value="">— select source site first —</option>';return;}
  if(_streamCache[site]){_populateStreams(selEl,_streamCache[site],defVal);return;}
  fetch('/api/audiorouter/site_streams?site='+encodeURIComponent(site),{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      _streamCache[site]=d.streams||[];
      _populateStreams(selEl,_streamCache[site],defVal);
    }).catch(function(){
      selEl.innerHTML='<option value="">Error loading streams</option>';
    });
}

function _populateStreams(selEl,streams,defVal){
  if(!streams.length){
    selEl.innerHTML='<option value="">No streams on this site</option>';
    return;
  }
  selEl.innerHTML=streams.map(function(s){
    var sel=(s===defVal)?' selected':'';
    return '<option value="'+_esc(s)+'"'+sel+'>'+_esc(s)+'</option>';
  }).join('');
}

// ── Add form ──────────────────────────────────────────────────────────────────
var toggleBtn=document.getElementById('toggle-form-btn');
var addForm=document.getElementById('add-form');
toggleBtn.addEventListener('click',function(){
  addForm.classList.toggle('open');
  toggleBtn.textContent=addForm.classList.contains('open')?'✕ Cancel':'+ New Route';
});

document.getElementById('f-src-site').addEventListener('change',function(){
  _loadStreams(this.value,document.getElementById('f-src-stream'),'');
});

document.getElementById('f-lw-id').addEventListener('input',function(){
  var mc=_lwMc(this.value);
  document.getElementById('lw-addr-preview').textContent=mc?'Multicast: '+mc:'';
});

document.getElementById('add-route-btn').addEventListener('click',function(){
  var name=document.getElementById('f-name').value.trim();
  var srcSite=document.getElementById('f-src-site').value;
  var srcStream=document.getElementById('f-src-stream').value;
  var dstSite=document.getElementById('f-dst-site').value;
  var lwId=parseInt(document.getElementById('f-lw-id').value||'0',10);
  if(!name){_showMsg('Route name is required.',true);return;}
  if(!srcSite){_showMsg('Select a source site.',true);return;}
  if(!srcStream){_showMsg('Select a source stream.',true);return;}
  if(!dstSite){_showMsg('Select a destination site.',true);return;}
  if(!lwId||lwId<1||lwId>65535){_showMsg('Livewire Stream ID must be 1–65535.',true);return;}
  _post('/api/audiorouter/routes',{
    name:name,source_site:srcSite,source_stream:srcStream,
    dest_site:dstSite,dest_lw_stream_id:lwId,enabled:true
  }).then(function(r){return r.json();})
  .then(function(d){
    if(d.ok){
      _showMsg('Route added.',false);
      addForm.classList.remove('open');
      toggleBtn.textContent='+ New Route';
      document.getElementById('f-name').value='';
      document.getElementById('f-src-site').value='';
      document.getElementById('f-src-stream').innerHTML='<option value="">— select source site first —</option>';
      document.getElementById('f-dst-site').value='';
      document.getElementById('f-lw-id').value='';
      document.getElementById('lw-addr-preview').textContent='';
      _refresh();
    } else {
      _showMsg('Error: '+(d.error||'unknown'),true);
    }
  }).catch(function(e){_showMsg('Request failed: '+e,true);});
});

// ── Routes cards ──────────────────────────────────────────────────────────────
function _overallBadge(r){
  if(!r.enabled) return '<span class="badge b-mu">Disabled</span>';
  var st=r.status||'';
  if(st==='active')     return '<span class="badge b-ok">Active</span>';
  if(st==='error')      return '<span class="badge b-al">Error</span>';
  if(st==='connecting') return '<span class="badge b-wn">Connecting</span>';
  return '<span class="badge b-mu">Idle</span>';
}

function _sideBadge(st){
  var s=st&&st.status||'';
  if(s==='active')     return '<span class="badge b-ok">Active</span>';
  if(s==='error')      return '<span class="badge b-al">Error</span>';
  if(s==='connecting') return '<span class="badge b-wn">Connecting</span>';
  if(s==='idle')       return '<span class="badge b-mu">Idle</span>';
  return '<span class="badge b-mu">—</span>';
}

function _timeAgo(ts){
  if(!ts) return '';
  var secs=Math.round((Date.now()/1000)-ts);
  if(secs<0) secs=0;
  return '<span class="ts">('+secs+'s ago)</span>';
}

function _renderRoutes(routes){
  var wrap=document.getElementById('routes-wrap');
  if(!routes.length){
    wrap.innerHTML='<div class="empty">No routes configured — click "+ New Route" to add one.</div>';
    return;
  }
  var html='';
  for(var i=0;i<routes.length;i++){
    var r=routes[i];
    var mc=_lwMc(r.dest_lw_stream_id);
    var srcSt=r.source_status||{};
    var dstSt=r.dest_status||{};
    var srcErr=(srcSt.error||'').replace(/^via (direct|hub)$/i,'').trim();
    var dstVia='';var dstErr='';
    var dstErrRaw=dstSt.error||'';
    var viaMatch=dstErrRaw.match(/^via (direct|hub)$/i);
    if(viaMatch){dstVia=viaMatch[1];}else{dstErr=dstErrRaw;}

    html+='<div class="rcard" data-id="'+_esc(r.id)+'">'
      +'<div class="rcard-head">'
        +'<strong class="rcard-name">'+_esc(r.name)+'</strong>'
        +_overallBadge(r)
        +'<span class="rcard-acts">'
          +'<button class="btn bg bs route-toggle-btn" data-id="'+_esc(r.id)+'" data-enabled="'+(r.enabled?'1':'0')+'">'+(r.enabled?'Disable':'Enable')+'</button>'
          +'<button class="btn bd bs route-del-btn" data-id="'+_esc(r.id)+'">Delete</button>'
        +'</span>'
      +'</div>'
      +'<div class="rcard-body">'
        +'<div class="rcard-side">'
          +'<div class="rcard-role">SOURCE</div>'
          +'<div class="rcard-site">Site: <strong>'+_esc(r.source_site)+'</strong></div>'
          +'<div class="rcard-stream">Stream: <strong>'+_esc(r.source_stream)+'</strong></div>'
          +'<div class="rcard-st">'+_sideBadge(srcSt)+_timeAgo(srcSt.ts)+'</div>'
          +(srcErr?'<div class="rcard-err">⚠ '+_esc(srcErr)+'</div>':'')
        +'</div>'
        +'<div class="rcard-arrow">→</div>'
        +'<div class="rcard-side">'
          +'<div class="rcard-role">DESTINATION</div>'
          +'<div class="rcard-site">Site: <strong>'+_esc(r.dest_site)+'</strong></div>'
          +'<div class="rcard-stream">LW Channel: <strong>'+_esc(r.dest_lw_stream_id)+'</strong></div>'
          +'<div class="rcard-mc ts">Multicast: <code>'+_esc(mc)+'</code></div>'
          +'<div class="rcard-st">'+_sideBadge(dstSt)
            +(dstVia?'<span class="via-tag">via '+_esc(dstVia)+'</span>':'')
            +_timeAgo(dstSt.ts)+'</div>'
          +(dstErr?'<div class="rcard-err">⚠ '+_esc(dstErr)+'</div>':'')
        +'</div>'
      +'</div>'
    +'</div>';
  }
  wrap.innerHTML=html;
}

// Event delegation for table actions
document.getElementById('routes-wrap').addEventListener('click',function(e){
  var tb=e.target.closest('.route-toggle-btn');
  if(tb){
    var rid=tb.dataset.id;
    var enabled=(tb.dataset.enabled==='0');
    _patch('/api/audiorouter/routes/'+encodeURIComponent(rid),{enabled:enabled})
      .then(function(r){return r.json();})
      .then(function(d){if(d.ok)_refresh();else _showMsg('Error: '+(d.error||'?'),true);});
    return;
  }
  var db=e.target.closest('.route-del-btn');
  if(db){
    var rid2=db.dataset.id;
    if(!confirm('Delete this route?')) return;
    _del('/api/audiorouter/routes/'+encodeURIComponent(rid2))
      .then(function(r){return r.json();})
      .then(function(d){if(d.ok)_refresh();else _showMsg('Error: '+(d.error||'?'),true);});
  }
});

function _refresh(){
  fetch('/api/audiorouter/routes',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){_renderRoutes(d.routes||[]);})
    .catch(function(){});
}

_refresh();
setInterval(_refresh,5000);
})();
</script></body></html>"""


# ── Client/standalone page (redirects to hub) ──────────────────────────────────

_CLIENT_TPL = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<title>Audio Router — SignalScope</title>
<style nonce="{{csp_nonce()}}">
body{font-family:system-ui,sans-serif;font-size:13px;background:#07142b;color:#eef5ff;
     display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
.box{background:#0d2346;border:1px solid #17345f;border-radius:12px;padding:32px 40px;max-width:400px;text-align:center}
h2{margin-bottom:12px;font-size:18px}
p{color:#8aa4c8;font-size:13px;margin-bottom:20px}
a{color:#17a8ff;text-decoration:none;font-weight:600}
</style>
</head><body>
<div class="box">
  <h2>🔀 Audio Router</h2>
  <p>Audio Router is a hub-only feature.<br>Manage routes from the hub dashboard.</p>
  <a href="/">← Dashboard</a>
</div>
</body></html>"""


# ── register ───────────────────────────────────────────────────────────────────

def register(app, ctx):
    global _route_status, _route_slots

    login_required   = ctx["login_required"]
    csrf_protect     = ctx["csrf_protect"]
    monitor          = ctx["monitor"]
    hub_server       = ctx["hub_server"]
    listen_registry  = ctx["listen_registry"]
    BUILD            = ctx["BUILD"]

    cfg_ss    = monitor.app_cfg
    mode      = getattr(getattr(cfg_ss, "hub", None), "mode",      "standalone") or "standalone"
    hub_url   = (getattr(getattr(cfg_ss, "hub", None), "hub_url",  "") or "").rstrip("/")
    site_name = (getattr(getattr(cfg_ss, "hub", None), "site_name","") or "")
    secret    = (getattr(getattr(cfg_ss, "hub", None), "secret_key","") or "")

    is_hub    = mode in ("hub", "both")
    is_client = mode in ("client", "both") and bool(hub_url)

    # ── Hub page ───────────────────────────────────────────────────────────────

    @app.get("/hub/audiorouter")
    @login_required
    def audiorouter_hub_page():
        if not is_hub:
            return render_template_string(_CLIENT_TPL)
        sites = _get_known_sites(hub_server, cfg_ss, site_name)
        return render_template_string(_HUB_TPL, sites=sites)

    @app.get("/audiorouter")
    @login_required
    def audiorouter_client_page():
        if is_hub:
            from flask import redirect
            return redirect("/hub/audiorouter")
        return render_template_string(_CLIENT_TPL)

    # ── Hub API: list streams for a site (populates Add Route dropdown) ────────

    @app.get("/api/audiorouter/site_streams")
    @login_required
    def audiorouter_site_streams():
        site = request.args.get("site", "").strip()
        streams = _streams_for_site(site, hub_server, cfg_ss, site_name)
        return jsonify({"streams": streams})

    # ── Hub API: list all routes (with status merged) ──────────────────────────

    @app.get("/api/audiorouter/routes")
    @login_required
    def audiorouter_routes_list():
        with _cfg_lock:
            cfg = _load_cfg()
        routes = cfg.get("routes", [])
        merged = []
        for r in routes:
            entry = dict(r)
            with _status_lock:
                site_map = dict(_route_status.get(r["id"], {}))
            src_st = site_map.get(r.get("source_site", ""), {})
            dst_st = site_map.get(r.get("dest_site",   ""), {})
            # Overall status: active > connecting > error > idle
            overall = "idle"
            for _s in [src_st, dst_st]:
                if _s.get("status") == "active":
                    overall = "active"
                    break
                if _s.get("status") == "connecting":
                    overall = "connecting"
            if overall not in ("active", "connecting"):
                for _s in [src_st, dst_st]:
                    if _s.get("status") == "error":
                        overall = "error"
                        break
            entry["status"]        = overall
            entry["error"]         = src_st.get("error", "") or dst_st.get("error", "")
            entry["source_status"] = src_st
            entry["dest_status"]   = dst_st
            merged.append(entry)
        return jsonify({"routes": merged})

    # ── Hub API: create route ─────────────────────────────────────────────────

    @app.post("/api/audiorouter/routes")
    @login_required
    @csrf_protect
    def audiorouter_routes_create():
        data = request.get_json(silent=True) or {}
        name        = str(data.get("name", "")).strip()
        source_site = str(data.get("source_site", "")).strip()
        source_stream = str(data.get("source_stream", "")).strip()
        dest_site   = str(data.get("dest_site", "")).strip()
        lw_id       = data.get("dest_lw_stream_id")
        enabled     = bool(data.get("enabled", True))

        if not name:
            return jsonify({"ok": False, "error": "name required"}), 400
        if not source_site or not source_stream:
            return jsonify({"ok": False, "error": "source_site and source_stream required"}), 400
        if not dest_site:
            return jsonify({"ok": False, "error": "dest_site required"}), 400
        try:
            lw_id = int(lw_id)
            assert 1 <= lw_id <= 65535
        except Exception:
            return jsonify({"ok": False, "error": "dest_lw_stream_id must be 1–65535"}), 400

        route = {
            "id":               str(uuid.uuid4()),
            "name":             name,
            "source_site":      source_site,
            "source_stream":    source_stream,
            "dest_site":        dest_site,
            "dest_lw_stream_id": lw_id,
            "enabled":          enabled,
        }

        # Create relay slot for cross-site routes
        if source_site != dest_site and enabled:
            _ensure_relay_slot(route, listen_registry, monitor)

        with _cfg_lock:
            cfg = _load_cfg()
            cfg.setdefault("routes", []).append(route)
            _save_cfg(cfg)

        return jsonify({"ok": True, "id": route["id"]})

    # ── Hub API: delete route ─────────────────────────────────────────────────

    @app.delete("/api/audiorouter/routes/<rid>")
    @login_required
    @csrf_protect
    def audiorouter_routes_delete(rid):
        with _cfg_lock:
            cfg = _load_cfg()
        routes = cfg.get("routes", [])
        new_routes = [r for r in routes if r.get("id") != rid]
        if len(new_routes) == len(routes):
            return jsonify({"ok": False, "error": "not found"}), 404

        # Close relay slot and hub broadcaster if they exist
        with _slots_lock:
            slot_id = _route_slots.pop(rid, None)
        if slot_id:
            slot = listen_registry.get(slot_id)
            if slot:
                slot.closed = True
        with _hbcast_lock:
            bc = _hub_broadcasters.pop(rid, None)
        if bc:
            bc.close()

        with _status_lock:
            _route_status.pop(rid, None)

        with _cfg_lock:
            cfg["routes"] = new_routes
            _save_cfg(cfg)

        return jsonify({"ok": True})

    # ── Hub API: update route (enabled/name) ──────────────────────────────────

    @app.patch("/api/audiorouter/routes/<rid>")
    @login_required
    @csrf_protect
    def audiorouter_routes_update(rid):
        data = request.get_json(silent=True) or {}

        with _cfg_lock:
            cfg = _load_cfg()
        routes = cfg.get("routes", [])
        target = None
        for r in routes:
            if r.get("id") == rid:
                target = r
                break
        if target is None:
            return jsonify({"ok": False, "error": "not found"}), 404

        if "enabled" in data:
            target["enabled"] = bool(data["enabled"])
        if "name" in data:
            target["name"] = str(data["name"]).strip()

        # When enabling a cross-site route, ensure relay slot exists
        if target.get("enabled") and target.get("source_site") != target.get("dest_site"):
            with _slots_lock:
                has_slot = rid in _route_slots
            if not has_slot:
                _ensure_relay_slot(target, listen_registry, monitor)

        # When disabling, close relay slot and hub broadcaster
        if not target.get("enabled"):
            with _slots_lock:
                slot_id = _route_slots.pop(rid, None)
            if slot_id:
                slot = listen_registry.get(slot_id)
                if slot:
                    slot.closed = True
            with _hbcast_lock:
                bc = _hub_broadcasters.pop(rid, None)
            if bc:
                bc.close()
            with _status_lock:
                _route_status.pop(rid, None)

        with _cfg_lock:
            _save_cfg(cfg)

        return jsonify({"ok": True})

    # ── Hub API: client poll ───────────────────────────────────────────────────

    @app.get("/api/audiorouter/poll")
    def audiorouter_poll():
        """
        Client polls this endpoint. Returns routes where this site is source or dest,
        with role ('local'/'source'/'dest'), relay slot info for cross-site, and
        hub credentials for HMAC signing.
        """
        polling_site = request.headers.get("X-Site", "").strip()
        if not polling_site:
            return jsonify({"error": "missing X-Site header"}), 400

        # Validate site is approved (skip check for standalone/same-machine mode)
        if is_hub and hub_server:
            sdata = hub_server._sites.get(polling_site, {})
            if not sdata.get("_approved") and polling_site != site_name:
                return jsonify({"error": "site not approved"}), 403

        with _cfg_lock:
            cfg = _load_cfg()
        routes = cfg.get("routes", [])

        out = []
        for r in routes:
            if not r.get("enabled"):
                continue
            src = r.get("source_site", "")
            dst = r.get("dest_site",   "")
            if polling_site not in (src, dst):
                continue

            entry = dict(r)

            if src == dst and src == polling_site:
                # Same-site: this client handles everything locally
                entry["role"] = "local"
                entry["hub_url"]    = ""
                entry["hub_secret"] = ""
            elif polling_site == src:
                # Cross-site: we are the source — post PCM chunks to hub
                entry["role"] = "source"
                entry["hub_url"]    = _self_url(cfg_ss)
                entry["hub_secret"] = secret
                with _slots_lock:
                    entry["slot_id"] = _route_slots.get(r["id"], "")
            else:
                # Cross-site: we are the dest — try direct first, hub relay fallback
                entry["role"] = "dest"
                entry["hub_url"]    = _self_url(cfg_ss)
                entry["hub_secret"] = secret
                with _slots_lock:
                    entry["slot_id"] = _route_slots.get(r["id"], "")
                # Pass the source's self-reported direct stream URL (if available)
                with _status_lock:
                    entry["direct_url"] = (_route_status.get(r["id"], {})
                                           .get(r.get("source_site", ""), {})
                                           .get("direct_url", ""))

            # Attach device_index for the source stream so clients can build ffmpeg cmd
            if entry["role"] in ("local", "source"):
                di = _get_device_index(r["source_site"], r["source_stream"],
                                       hub_server, cfg_ss, site_name, polling_site)
                entry["device_index"] = di or ""

            out.append(entry)

        return jsonify({"routes": out})

    # ── Hub API: client status report ─────────────────────────────────────────

    @app.post("/api/audiorouter/client_status")
    def audiorouter_client_status():
        data = request.get_json(silent=True) or {}
        rid        = str(data.get("route_id", "")).strip()
        rsite      = str(data.get("site", "")).strip()
        status     = str(data.get("status", "")).strip()
        error      = str(data.get("error", "")).strip()
        direct_url = str(data.get("direct_url", "")).strip()
        if not rid:
            return jsonify({"ok": False, "error": "route_id required"}), 400
        with _status_lock:
            if rid not in _route_status:
                _route_status[rid] = {}
            _route_status[rid][rsite] = {
                "status":     status,
                "error":      error,
                "direct_url": direct_url,
                "ts":         time.time(),
            }
        return jsonify({"ok": True})

    # ── Direct PCM stream endpoint (P2P — no hub relay) ───────────────────────
    # Not login_required: authenticated via per-route HMAC token instead.
    # NOTE: each active direct route holds one Waitress worker thread. With
    # typical route counts (< 10) this is well within the threads=64 budget.

    @app.get("/api/audiorouter/stream/<route_id>")
    def audiorouter_direct_stream(route_id):
        from flask import Response
        token    = (request.args.get("token") or
                    request.headers.get("X-Audio-Token") or "")
        expected = _compute_stream_token(secret, route_id)
        if not token or token != expected:
            return jsonify({"error": "invalid token"}), 401

        # If this is a probe request, just confirm we're alive
        if request.headers.get("X-Probe"):
            return jsonify({"ok": True})

        with _bcast_lock:
            bc = _active_broadcasters.get(route_id)
        if bc is None:
            return jsonify({"error": "route not active on this node"}), 404

        def _gen():
            try:
                for chunk in bc.consumer_with_keepalive():
                    yield chunk
            except GeneratorExit:
                pass

        return Response(
            _gen(),
            mimetype="application/octet-stream",
            headers={
                "X-Accel-Buffering": "no",
                "X-Audio-Format":    "s16le/48000/2",
                "Cache-Control":     "no-cache",
            },
        )

    # ── Hub relay: source pushes PCM, dest reads it ───────────────────────────────
    # Both endpoints use the per-route HMAC token — no browser session needed.
    # The hub holds a _StreamBroadcaster per route; source writes, dest(s) read.
    # This replaces the scanner-relay-slot path which expired during DAB startup.

    @app.post("/api/audiorouter/push_chunk/<rid>")
    def audiorouter_push_chunk(rid):
        """Source POSTs raw PCM chunks here instead of /api/v1/audio_chunk."""
        token    = (request.args.get("token") or
                    request.headers.get("X-Audio-Token") or "")
        expected = _compute_stream_token(secret, rid)
        if not token or token != expected:
            return jsonify({"error": "invalid token"}), 401
        data = request.get_data()
        if data:
            with _hbcast_lock:
                bc = _hub_broadcasters.get(rid)
                if bc is None or bc.closed:
                    bc = _StreamBroadcaster()
                    _hub_broadcasters[rid] = bc
            bc.push(data)
        return "", 204

    @app.get("/api/audiorouter/hub_stream/<rid>")
    def audiorouter_hub_stream(rid):
        """Dest GETs a continuous PCM stream from the hub broadcaster."""
        from flask import Response
        token    = (request.args.get("token") or
                    request.headers.get("X-Audio-Token") or "")
        expected = _compute_stream_token(secret, rid)
        if not token or token != expected:
            return jsonify({"error": "invalid token"}), 401

        # Ensure a broadcaster exists — create one now if the source hasn't
        # pushed yet; it will block in consumer() until data arrives.
        with _hbcast_lock:
            bc = _hub_broadcasters.get(rid)
            if bc is None or bc.closed:
                bc = _StreamBroadcaster()
                _hub_broadcasters[rid] = bc

        def _gen():
            # consumer_with_keepalive() yields silence every 0.1 s when the
            # source hasn't started yet — this commits HTTP headers immediately
            # (preventing nginx proxy_read_timeout) AND ensures the Waitress
            # thread returns to a yield point regularly so client disconnects
            # are detected promptly.
            try:
                for chunk in bc.consumer_with_keepalive(catchup=0):
                    yield chunk
            except GeneratorExit:
                pass

        return Response(
            _gen(),
            mimetype="application/octet-stream",
            headers={
                "X-Accel-Buffering": "no",
                "X-Audio-Format":    "s16le/48000/2",
                "Cache-Control":     "no-cache",
            },
        )

    # ── Hub startup: re-create relay slots for existing enabled cross-site routes ─
    # _route_slots is in-memory only — it's empty on every server restart.
    # Without this, existing cross-site routes would report
    # "No relay slot assigned yet" until the user toggles them.

    if is_hub:
        def _restore_slots():
            try:
                with _cfg_lock:
                    saved = _load_cfg()
                for r in saved.get("routes", []):
                    if (r.get("enabled")
                            and r.get("source_site") != r.get("dest_site")):
                        _ensure_relay_slot(r, listen_registry, monitor)
            except Exception as e:
                monitor.log(f"[AudioRouter] Slot restore error: {e}")
        threading.Thread(target=_restore_slots, daemon=True,
                         name="ARSlotRestore").start()

    # ── Client: start routing daemon thread ───────────────────────────────────

    if is_client or mode in ("hub", "both", "standalone"):
        _effective_hub_url = hub_url if is_client else _self_url(cfg_ss)

        def _client_router_thread():
            # Give the Flask app a moment to fully start before polling
            time.sleep(5)
            monitor.log("[AudioRouter] Client router thread started.")
            while True:
                try:
                    _poll_and_execute(
                        _effective_hub_url,
                        site_name,
                        monitor,
                        listen_registry,
                    )
                except Exception as e:
                    monitor.log(f"[AudioRouter] Poll cycle error: {e}")
                time.sleep(8)

        threading.Thread(
            target=_client_router_thread,
            daemon=True,
            name="AudioRouterClient",
        ).start()


# ── Hub helper: ensure a relay slot exists for a cross-site route ──────────────

def _ensure_relay_slot(route: dict, listen_registry, monitor):
    rid = route["id"]
    with _slots_lock:
        if rid in _route_slots:
            return _route_slots[rid]

    try:
        slot = listen_registry.create(
            route["dest_site"],
            0,
            kind="scanner",
            mimetype="application/octet-stream",
        )
        with _slots_lock:
            _route_slots[rid] = slot.slot_id
        monitor.log(f"[AudioRouter] Relay slot {slot.slot_id} created for route {route['name']!r}")
        return slot.slot_id
    except Exception as e:
        monitor.log(f"[AudioRouter] Failed to create relay slot for {route['name']!r}: {e}")
        return None


# ── Site/stream helpers ────────────────────────────────────────────────────────

def _get_known_sites(hub_server, cfg_ss, local_site_name: str) -> list[str]:
    """Return list of known site names (hub _sites + local site)."""
    sites = set()
    if hub_server:
        try:
            with hub_server._lock:
                for sname in hub_server._sites:
                    sites.add(sname)
        except Exception:
            pass
    if local_site_name:
        sites.add(local_site_name)
    return sorted(sites)


def _streams_for_site(site: str, hub_server, cfg_ss, local_site_name: str) -> list[str]:
    """Return list of monitored stream names for a given site."""
    # For the local site, read directly from config
    if site == local_site_name:
        try:
            inputs = getattr(cfg_ss, "inputs", []) or []
            return [getattr(inp, "name", "") for inp in inputs if getattr(inp, "name", "")]
        except Exception:
            return []

    # For remote sites, read from hub heartbeat data
    if hub_server:
        try:
            sdata = hub_server._sites.get(site, {})
            streams = sdata.get("streams", [])
            if isinstance(streams, list):
                return [s.get("name", "") for s in streams if s.get("name")]
        except Exception:
            pass
    return []


def _get_device_index(source_site: str, source_stream: str,
                      hub_server, cfg_ss, local_site_name: str,
                      polling_site: str) -> str:
    """Return device_index for source_stream on source_site."""
    if source_site == local_site_name and source_site == polling_site:
        try:
            inputs = getattr(cfg_ss, "inputs", []) or []
            for inp in inputs:
                if getattr(inp, "name", "") == source_stream:
                    return getattr(inp, "device_index", "") or ""
        except Exception:
            pass
        return ""

    # For remote sites the hub doesn't store device_index in heartbeat —
    # the client running on source_site will look it up from its own cfg.
    # Return empty; the client ignores this field for its own streams.
    return ""


_SS_PORT = 5000  # SignalScope always binds on port 5000 (see signalscope.py)


def _self_url(cfg_ss) -> str:
    """Best-effort URL for reaching this node (hub_url or localhost fallback)."""
    hub_url = (getattr(getattr(cfg_ss, "hub", None), "hub_url", "") or "").rstrip("/")
    if hub_url:
        return hub_url
    return f"http://127.0.0.1:{_SS_PORT}"


# ── Client routing logic ───────────────────────────────────────────────────────

def _poll_and_execute(hub_url: str, site_name: str, monitor, listen_registry):
    """
    Poll hub for routes assigned to this site.
    Start/stop ffmpeg processes as needed.
    """
    try:
        req = urllib.request.Request(
            f"{hub_url}/api/audiorouter/poll",
            headers={"X-Site": site_name},
        )
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return  # Not approved yet — silent
        raise
    except Exception as e:
        monitor.log(f"[AudioRouter] Poll failed: {e}")
        return

    routes = data.get("routes", [])
    active_ids = {r["id"] for r in routes}

    # Stop processes for routes no longer in our list
    with _procs_lock:
        stale = [rid for rid in list(_active_procs.keys()) if rid not in active_ids]
    for rid in stale:
        monitor.log(f"[AudioRouter] Stopping removed/disabled route {rid}")
        _stop_route_proc(rid)
        _report_status(hub_url, site_name, rid, "idle")

    # Start / maintain routes
    for route in routes:
        rid  = route["id"]
        role = route.get("role", "")

        # For local/source roles, fill in device_index from local config if absent
        if role in ("local", "source") and not route.get("device_index"):
            try:
                inputs = getattr(monitor.app_cfg, "inputs", []) or []
                for inp in inputs:
                    if getattr(inp, "name", "") == route.get("source_stream", ""):
                        route = dict(route)
                        route["device_index"] = getattr(inp, "device_index", "") or ""
                        break
            except Exception:
                pass

        # Check if already running and healthy
        with _procs_lock:
            existing = _active_procs.get(rid)
        if existing is not None and existing.poll() is None:
            continue  # Already running

        if existing is not None:
            # Process died unexpectedly
            monitor.log(f"[AudioRouter] Route {route['name']!r} process exited — restarting")
            _stop_route_proc(rid)

        _start_route(route, hub_url, site_name, monitor, listen_registry)


def _start_route(route: dict, hub_url: str, site_name: str, monitor, listen_registry):
    """Start the appropriate ffmpeg process(es) for a route on this client."""
    rid         = route["id"]
    role        = route.get("role", "")
    lw_id       = int(route.get("dest_lw_stream_id", 0))
    rtp_url     = _ffmpeg_rtp_url(lw_id)
    source_site = route.get("source_site", "")
    source_stream = route.get("source_stream", "")
    device_index = route.get("device_index", "")

    # For the source role, device_index should have been populated by _poll_and_execute.
    # If still missing, report an error — we cannot start without knowing the input source.
    if role in ("local", "source") and not device_index:
        monitor.log(
            f"[AudioRouter] Route {route['name']!r}: "
            f"no device_index for {source_stream!r} on {source_site!r}"
        )
        _report_status(hub_url, site_name, rid, "error",
                       f"device_index not found for {source_stream!r}")
        return

    itype = _input_type(device_index) if role in ("local", "source") else "relay"

    # For source/local roles: always prefer reading from SignalScope's already-decoded
    # PCM buffer (_stream_buffer) over spawning a new process on the raw device.
    #
    # This is the correct approach for ALL input types:
    #   • DAB / FM      — buffer-only (raw device URIs can't be opened by ffmpeg)
    #   • Livewire      — device_index is a numeric stream ID (e.g. "7503"), not a
    #                     usable ffmpeg input; SignalScope already decodes the RTP
    #   • ALSA          — monitoring loop holds the device open; a second open fails
    #   • HTTP / RTP    — same stream, no need to reconnect independently
    #
    # Direct ffmpeg fallback only runs when the input isn't actively monitored
    # locally (inp not found) or numpy is unavailable.
    if role in ("local", "source") and _HAVE_NP:
        inp = _find_input(source_stream, monitor)
        if inp is not None:
            monitor.log(
                f"[AudioRouter] Route {route['name']!r}: "
                f"using PCM buffer (itype={itype})"
            )
            if role == "local":
                _start_local_route_buffered(route, inp, rtp_url, hub_url, site_name, monitor)
            else:
                _start_source_route_buffered(route, inp, hub_url, site_name, monitor,
                                             cfg_ss=monitor.app_cfg)
            return
        # Input not found locally — fall through to direct ffmpeg below

    if role == "local":
        _start_local_route(route, device_index, itype, rtp_url, hub_url, site_name, monitor)
    elif role == "source":
        _start_source_route(route, device_index, itype, hub_url, site_name, monitor,
                            cfg_ss=monitor.app_cfg)
    elif role == "dest":
        _start_dest_route(route, rtp_url, hub_url, site_name, monitor)


def _build_ffmpeg_input_args(device_index: str, itype: str) -> list[str]:
    """Build ffmpeg input arguments for a given device_index and input type."""
    if itype == "network":
        return [
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
            "-i", device_index,
        ]
    elif itype == "alsa":
        return ["-f", "alsa", "-i", device_index]
    else:
        return ["-i", device_index]


def _ffmpeg_lw_output_args(rtp_url: str) -> list[str]:
    """ffmpeg output arguments for Livewire L24 stereo 48kHz RTP."""
    return [
        "-ac", "2",
        "-ar", "48000",
        "-acodec", "pcm_s24be",
        "-f", "rtp",
        "-payload_type", "97",
        rtp_url,
    ]


def _start_local_route_buffered(route: dict, inp, rtp_url: str,
                                hub_url: str, site_name: str, monitor):
    """Same-site DAB/FM route: SignalScope PCM buffer → ffmpeg stdin → Livewire RTP."""
    import shutil
    rid    = route["id"]
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        _report_status(hub_url, site_name, rid, "error", "ffmpeg not found")
        return

    # _stream_buf_chunks always outputs stereo-interleaved s16le/48000/2
    cmd = ([ffmpeg, "-y",
            "-f", "s16le", "-ar", "48000", "-ac", "2",
            "-i", "pipe:0"]
           + _ffmpeg_lw_output_args(rtp_url))
    monitor.log(f"[AudioRouter] Local/buffered route {route['name']!r}: {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        with _procs_lock:
            _active_procs[rid] = proc
    except Exception as e:
        monitor.log(f"[AudioRouter] Local/buffered route {route['name']!r} ffmpeg failed: {e}")
        _report_status(hub_url, site_name, rid, "error", str(e))
        return

    threading.Thread(target=_drain_stderr, args=(proc, route["name"], monitor),
                     daemon=True, name=f"ARStderr-{rid[:8]}").start()
    _report_status(hub_url, site_name, rid, "active")

    stop = threading.Event()

    def _writer():
        try:
            for pcm_bytes in _stream_buf_chunks(inp, stop):
                if proc.poll() is not None:
                    break
                try:
                    proc.stdin.write(pcm_bytes)
                except BrokenPipeError:
                    break
        except Exception as e:
            monitor.log(f"[AudioRouter] Buffer writer {route['name']!r} error: {e}")
        finally:
            stop.set()
            try:
                proc.stdin.close()
            except Exception:
                pass
            monitor.log(f"[AudioRouter] Buffer writer {route['name']!r} stopped.")

    threading.Thread(target=_writer, daemon=True, name=f"ARBuf-{rid[:8]}").start()

    with _src_threads_lock:
        _active_src_threads[rid] = threading.current_thread()


def _start_source_route_buffered(route: dict, inp, hub_url: str, site_name: str,
                                  monitor, cfg_ss=None):
    """Cross-site DAB/FM SOURCE role: SignalScope PCM buffer → broadcaster → hub relay + direct HTTP."""
    rid    = route["id"]
    secret = route.get("hub_secret", "")

    bc = _StreamBroadcaster()
    with _bcast_lock:
        _active_broadcasters[rid] = bc

    token      = _compute_stream_token(secret, rid)
    direct_url = _my_direct_url(cfg_ss, rid, token) if cfg_ss else ""
    push_url   = f"{hub_url}/api/audiorouter/push_chunk/{rid}?token={token}"

    # Sentinel proc so _stop_route_proc / already-running check works
    stop = threading.Event()

    class _SentinelProc:
        def poll(self): return None if not stop.is_set() else 0
        def kill(self): stop.set()
        def wait(self, timeout=None): stop.wait(timeout)

    with _procs_lock:
        _active_procs[rid] = _SentinelProc()

    _report_status(hub_url, site_name, rid, "connecting", direct_url=direct_url)

    def _reader():
        first = True
        try:
            for pcm_bytes in _stream_buf_chunks(inp, stop):
                bc.push(pcm_bytes)
                if first:
                    _report_status(hub_url, site_name, rid, "active", direct_url=direct_url)
                    first = False
        except Exception as e:
            monitor.log(f"[AudioRouter] Buffered source reader {route['name']!r} error: {e}")
        finally:
            bc.close()
            with _bcast_lock:
                _active_broadcasters.pop(rid, None)
            stop.set()
            monitor.log(f"[AudioRouter] Buffered source reader {route['name']!r} stopped.")

    threading.Thread(target=_reader, daemon=True, name=f"ARBufR-{rid[:8]}").start()

    def _hub_sender():
        try:
            for chunk in bc.consumer():
                req = urllib.request.Request(
                    push_url, data=chunk, method="POST",
                    headers={"Content-Type": "application/octet-stream"},
                )
                try:
                    urllib.request.urlopen(req, timeout=5).close()
                except Exception:
                    pass
        except Exception as e:
            monitor.log(f"[AudioRouter] Buffered hub sender {route['name']!r} error: {e}")
        finally:
            _report_status(hub_url, site_name, rid, "idle")

    t = threading.Thread(target=_hub_sender, daemon=True, name=f"ARBufS-{rid[:8]}")
    t.start()
    with _src_threads_lock:
        _active_src_threads[rid] = t


def _start_local_route(route: dict, device_index: str, itype: str, rtp_url: str,
                       hub_url: str, site_name: str, monitor):
    """Same-site route: input → Livewire multicast via ffmpeg."""
    rid = route["id"]
    import shutil
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        _report_status(hub_url, site_name, rid, "error", "ffmpeg not found")
        return

    cmd = [ffmpeg, "-y"] + _build_ffmpeg_input_args(device_index, itype) + \
          _ffmpeg_lw_output_args(rtp_url)

    monitor.log(f"[AudioRouter] Local route {route['name']!r}: {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        with _procs_lock:
            _active_procs[rid] = proc
        threading.Thread(target=_drain_stderr, args=(proc, route["name"], monitor),
                         daemon=True, name=f"ARStderr-{rid[:8]}").start()
        _report_status(hub_url, site_name, rid, "active")
    except Exception as e:
        monitor.log(f"[AudioRouter] Local route {route['name']!r} failed to start: {e}")
        _report_status(hub_url, site_name, rid, "error", str(e))


def _start_source_route(route: dict, device_index: str, itype: str,
                        hub_url: str, site_name: str, monitor, cfg_ss=None):
    """
    Cross-site SOURCE role.
    Routing priority (best latency first):
      1. Direct P2P  — dest pulls PCM from this node's /api/audiorouter/stream/<rid>
      2. Hub relay   — fallback when dest can't reach us directly

    A single ffmpeg process feeds a _StreamBroadcaster. Two consumers run
    concurrently:
      • Hub relay sender   — always runs (posts 9600-byte chunks to hub slot)
      • Direct HTTP stream — served via Flask /api/audiorouter/stream/<rid>
        (dest connects directly when it can, no hub audio involved)
    """
    rid    = route["id"]
    secret = route.get("hub_secret", "")

    import shutil
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        _report_status(hub_url, site_name, rid, "error", "ffmpeg not found")
        return

    # ── Start ffmpeg → stdout (raw 16-bit LE mono 48kHz PCM) ──────────────────
    cmd = [ffmpeg, "-y"] + _build_ffmpeg_input_args(device_index, itype) + [
        "-ac", "1", "-ar", "48000", "-f", "s16le", "pipe:1",
    ]
    monitor.log(f"[AudioRouter] Source route {route['name']!r}: {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with _procs_lock:
            _active_procs[rid] = proc
    except Exception as e:
        monitor.log(f"[AudioRouter] Source route {route['name']!r} ffmpeg failed: {e}")
        _report_status(hub_url, site_name, rid, "error", str(e))
        return

    threading.Thread(target=_drain_stderr, args=(proc, route["name"], monitor),
                     daemon=True, name=f"ARStderr-{rid[:8]}").start()

    # ── Create broadcaster for fan-out to hub relay + direct HTTP clients ──────
    bc = _StreamBroadcaster()
    with _bcast_lock:
        _active_broadcasters[rid] = bc

    # ── Compute direct URL + hub push URL ─────────────────────────────────────
    token      = _compute_stream_token(secret, rid)
    direct_url = _my_direct_url(cfg_ss, rid, token) if cfg_ss else ""
    push_url   = f"{hub_url}/api/audiorouter/push_chunk/{rid}?token={token}"
    _report_status(hub_url, site_name, rid, "connecting", direct_url=direct_url)

    # ── Reader thread: ffmpeg stdout → broadcaster ─────────────────────────────
    def _reader():
        try:
            while proc.poll() is None:
                chunk = proc.stdout.read(9600)
                if not chunk:
                    break
                bc.push(chunk)
        except Exception as e:
            monitor.log(f"[AudioRouter] Source reader {route['name']!r} error: {e}")
        finally:
            bc.close()
            with _bcast_lock:
                _active_broadcasters.pop(rid, None)
            monitor.log(f"[AudioRouter] Source reader {route['name']!r} stopped.")

    threading.Thread(target=_reader, daemon=True, name=f"ARRdr-{rid[:8]}").start()

    # ── Hub relay sender: POSTs chunks to hub push_chunk endpoint ──────────────
    def _hub_sender():
        first = True
        try:
            for chunk in bc.consumer():
                if first:
                    _report_status(hub_url, site_name, rid, "active",
                                   direct_url=direct_url)
                    first = False
                req = urllib.request.Request(
                    push_url, data=chunk, method="POST",
                    headers={"Content-Type": "application/octet-stream"},
                )
                try:
                    urllib.request.urlopen(req, timeout=5).close()
                except Exception:
                    pass  # Hub temporarily unreachable — direct path still works
        except Exception as e:
            monitor.log(f"[AudioRouter] Hub relay sender {route['name']!r} error: {e}")
        finally:
            _report_status(hub_url, site_name, rid, "idle")
            monitor.log(f"[AudioRouter] Hub relay sender {route['name']!r} stopped.")

    t = threading.Thread(target=_hub_sender, daemon=True, name=f"ARSrc-{rid[:8]}")
    t.start()
    with _src_threads_lock:
        _active_src_threads[rid] = t


def _start_dest_route(route: dict, rtp_url: str,
                      hub_url: str, site_name: str, monitor):
    """
    Cross-site DEST role.
    Routing priority (best latency first):
      1. Direct P2P  — ffmpeg pulls PCM directly from source node via
                       /api/audiorouter/stream/<rid>?token=HMAC (no hub audio)
      2. Hub relay   — /api/audiorouter/hub_stream/<rid>?token=HMAC
                       Token-authenticated hub endpoint (NOT /hub/scanner/stream/
                       which requires browser session cookie).

    The source node reports its direct stream URL via the hub status API.
    The hub includes it in the poll response as 'direct_url'. We probe it
    before falling back to the hub relay URL.
    """
    rid        = route["id"]
    direct_url = (route.get("direct_url") or "").strip()
    secret     = (route.get("hub_secret") or "").strip()

    # Hub relay URL — HMAC-authenticated, no scanner slot or session needed.
    # The hub creates a broadcaster lazily when the source first pushes data;
    # the dest just needs hub_url, rid, and the HMAC token.
    token          = _compute_stream_token(secret, rid)
    hub_stream_url = f"{hub_url}/api/audiorouter/hub_stream/{rid}?token={token}"

    if not hub_url:
        _report_status(hub_url, site_name, rid, "error", "No hub URL configured")
        return

    import shutil
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        _report_status(hub_url, site_name, rid, "error", "ffmpeg not found")
        return

    # ── Choose stream URL: prefer direct P2P, fall back to hub relay ──────────
    if direct_url:
        monitor.log(f"[AudioRouter] Dest route {route['name']!r}: probing direct URL…")
        if _probe_direct_url(direct_url, timeout=2.5):
            stream_url = direct_url
            via = "direct"
            monitor.log(f"[AudioRouter] Dest route {route['name']!r}: direct path OK → {direct_url}")
        else:
            monitor.log(
                f"[AudioRouter] Dest route {route['name']!r}: direct probe failed, "
                f"falling back to hub relay"
            )
            stream_url = hub_stream_url
            via = "hub"
    else:
        stream_url = hub_stream_url
        via = "hub"

    if not stream_url:
        _report_status(hub_url, site_name, rid, "error", "No usable stream URL")
        return

    # ── Build ffmpeg command: raw PCM → stereo L24 → Livewire RTP ─────────────
    # Both hub_stream and direct_stream serve s16le/48000/2 (always stereo):
    # stereo sources pass through L/R; mono sources are duplicated to L=R.
    cmd = [
        ffmpeg, "-y",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-f", "s16le",
        "-ar", "48000",
        "-ac", "2",
        "-i", stream_url,
    ] + _ffmpeg_lw_output_args(rtp_url)

    monitor.log(
        f"[AudioRouter] Dest route {route['name']!r} via={via}: {' '.join(cmd)}"
    )
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        with _procs_lock:
            _active_procs[rid] = proc
        threading.Thread(target=_drain_stderr, args=(proc, route["name"], monitor),
                         daemon=True, name=f"ARStderr-{rid[:8]}").start()
        _report_status(hub_url, site_name, rid, "active", f"via {via}")
    except Exception as e:
        monitor.log(f"[AudioRouter] Dest route {route['name']!r} ffmpeg failed: {e}")
        _report_status(hub_url, site_name, rid, "error", str(e))

