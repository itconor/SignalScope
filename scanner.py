"""scanner.py — FM Scanner plugin for SignalScope
Drop this file alongside signalscope.py on the hub machine.

Architecture:
    Hub routes manage scanner sessions per remote site.
    Client machines with role="scanner" dongles are listed in the site selector.
    The hub creates a relay slot; the client AppMonitor streams PCM to it.
    Browser connects to the relay slot stream for live FM audio + RDS.
"""

SIGNALSCOPE_PLUGIN = {
    "id":       "scanner",
    "label":    "FM Scanner",
    "url":      "/hub/scanner",
    "icon":     "📻",
    "hub_only": True,
    "version":  "1.0.0",
}

import hashlib
import hmac as _hmac
import json
import queue
import struct
import time

# ── Inline signature verification (mirrors signalscope.py hub_verify_signature) ─
def _derive_key(secret: str, purpose: str) -> bytes:
    return hashlib.sha256(f"{secret}:{purpose}".encode()).digest()

def _sign_payload(secret: str, payload_bytes: bytes, ts: float) -> str:
    key = _derive_key(secret, "signing")
    msg = f"{ts:.0f}:".encode() + payload_bytes
    return _hmac.new(key, msg, hashlib.sha256).hexdigest()

def _verify_sig(secret: str, payload_bytes: bytes, sig: str, ts: float):
    """Returns (ok, reason). Tolerance 30 s."""
    if abs(time.time() - ts) > 30:
        return False, "timestamp out of window"
    expected = _sign_payload(secret, payload_bytes, ts)
    if not _hmac.compare_digest(expected, sig):
        return False, "invalid signature"
    return True, ""

def _safe_name(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in "-_").strip() or "stream"

_HUB_SITE_TIMEOUT = 30.0   # seconds before a site is marked offline


# ── Template ───────────────────────────────────────────────────────────────────
SCANNER_TPL = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FM Scanner — SignalScope</title>
<link rel="icon" type="image/x-icon" href="/static/signalscope_icon.png">
<meta name="csrf-token" content="{{csrf_token()}}">
<style nonce="{{csp_nonce()}}">
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8;--lcd:#00e5ff;--lcd-dim:#004a60}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:radial-gradient(circle at top,#12376f 0%,var(--bg) 38%,#05101f 100%);color:var(--tx);min-height:100vh;display:flex;flex-direction:column}
a{color:var(--acc);text-decoration:none}
header{background:linear-gradient(180deg,rgba(10,31,65,.96),rgba(9,24,48,.96));border-bottom:1px solid var(--bor);padding:12px 20px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;box-shadow:0 6px 18px rgba(0,0,0,.18)}
header h1{font-size:17px;font-weight:700}
.badge{font-size:11px;padding:2px 8px;border-radius:999px;background:#1e3a5f;color:var(--acc)}
nav a{color:var(--tx);font-size:13px;padding:5px 10px;border-radius:6px;background:var(--bor);text-decoration:none;transition:background .15s}
nav a:hover{background:#254880;color:#fff}
.setup-bar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:10px 20px;background:rgba(10,20,48,.7);border-bottom:1px solid var(--bor)}
.setup-bar label{font-size:12px;color:var(--mu);white-space:nowrap}
.setup-bar select,.setup-bar input[type=number]{padding:6px 10px;font-size:13px;background:#0d1e40;border:1px solid var(--bor);border-radius:6px;color:var(--tx);outline:none}
.setup-bar select:focus,.setup-bar input[type=number]:focus{border-color:var(--acc)}
.btn-connect{padding:7px 20px;border-radius:6px;cursor:pointer;font-size:13px;font-weight:600;border:1px solid var(--acc);background:#0e2a5a;color:#bfdbfe;transition:background .15s;font-family:inherit}
.btn-connect:hover{background:#143a80}
.btn-connect.active{background:#1a4028;border-color:var(--ok);color:#86efac}
main{flex:1;display:flex;align-items:flex-start;justify-content:center;padding:24px 16px}
.page-col{display:flex;flex-direction:column;gap:12px;width:100%;max-width:700px}
.tuner{background:var(--sur);border:1px solid var(--bor);border-radius:14px;padding:22px 24px;box-shadow:0 6px 24px rgba(0,0,0,.4)}
.status-row{display:flex;align-items:center;justify-content:center;gap:8px;margin-bottom:16px;font-size:13px;color:var(--mu)}
.sdot{width:9px;height:9px;border-radius:50%;flex-shrink:0;transition:background .3s,box-shadow .3s}
.delay-badge{margin-left:auto;font-size:.7rem;font-family:monospace;color:var(--dim);background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);border-radius:4px;padding:1px 5px;white-space:nowrap}
.sdot.idle{background:#2a3a4a}
.sdot.connecting{background:var(--wn);box-shadow:0 0 8px rgba(245,158,11,.7)}
.sdot.streaming{background:var(--ok);box-shadow:0 0 8px rgba(34,197,94,.6)}
.sdot.error{background:var(--al);box-shadow:0 0 8px rgba(239,68,68,.6)}
.freq-wrap{text-align:center;margin-bottom:14px}
.freq-disp{display:inline-block;font-family:'Courier New',monospace;font-size:58px;font-weight:700;letter-spacing:3px;color:var(--lcd);text-shadow:0 0 14px rgba(0,229,255,.6),0 0 28px rgba(0,229,255,.18);background:#030a12;border:2px solid #0c2440;border-radius:10px;padding:10px 22px;min-width:278px;line-height:1.1;transition:color .2s}
.freq-unit{font-size:18px;color:var(--lcd-dim);letter-spacing:2px;margin-top:4px;font-family:'Courier New',monospace}
.freq-sub{font-size:12px;color:var(--mu);margin-top:5px;min-height:16px}
.level-wrap{display:flex;align-items:center;justify-content:center;gap:8px;margin-top:8px}
.level-label{font-size:10px;color:var(--mu);width:60px;text-align:right;font-family:'Courier New',monospace;flex-shrink:0}
.level-track{flex:1;max-width:240px;height:6px;background:#0a1628;border-radius:3px;overflow:hidden;border:1px solid var(--bor)}
.level-fill{height:100%;border-radius:3px;transition:width .5s,background .4s;background:var(--al);width:0%}
.rds-wrap{background:#040c1a;border:1px solid #0f2248;border-radius:8px;padding:10px 14px;margin-bottom:14px}
.rds-ps{display:block;font-family:'Courier New',monospace;font-size:22px;font-weight:700;letter-spacing:4px;color:var(--lcd);text-align:center;text-shadow:0 0 10px rgba(0,229,255,.4);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-height:28px}
.rds-rt-outer{overflow:hidden;white-space:nowrap;margin-top:5px;text-align:center}
.rds-rt-static{font-size:12px;color:var(--mu)}
.rds-rt-scroll{display:inline-flex;white-space:nowrap;animation:rds-marquee 16s linear infinite;font-size:12px;color:var(--mu)}
.rds-rt-scroll span{display:inline-block;padding-right:3rem}
@keyframes rds-marquee{0%{transform:translateX(0)}100%{transform:translateX(-50%)}}
.rds-meta{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px;justify-content:center;align-items:center}
.rds-badge{font-size:10px;padding:2px 7px;border-radius:4px;font-weight:700;letter-spacing:.3px;background:#0a1628;border:1px solid var(--bor);color:var(--mu)}
.rds-badge.on{background:rgba(23,168,255,.12);border-color:rgba(23,168,255,.4);color:var(--acc)}
.rds-badge.stereo-on{background:rgba(34,197,94,.12);border-color:rgba(34,197,94,.4);color:var(--ok)}
.rds-badge.pty-on{background:rgba(168,85,247,.15);border-color:rgba(168,85,247,.4);color:#c084fc}
.rds-pi{font-size:9px;color:#4a6a8a;font-family:'Courier New',monospace;padding:2px 4px}
.tune-row{display:flex;align-items:center;justify-content:center;gap:6px;margin-bottom:10px}
.tune-btn{background:#0a1a3a;border:1px solid var(--bor);color:var(--acc);font-size:13px;font-weight:600;padding:9px 14px;border-radius:8px;cursor:pointer;min-width:58px;text-align:center;transition:background .1s;user-select:none;font-family:inherit}
.tune-btn:hover{background:#142040;border-color:#2a5a90;color:#80c4ff}
.tune-btn:active{background:#1a2a50}
.tune-btn:disabled{opacity:.3;cursor:not-allowed}
.step-row{display:flex;align-items:center;justify-content:center;gap:6px;margin-bottom:14px;flex-wrap:wrap}
.step-lbl{font-size:11px;color:var(--mu)}
.step-btn{font-size:11px;padding:4px 11px;border-radius:20px;background:#08122a;border:1px solid var(--bor);color:var(--mu);cursor:pointer;transition:all .15s;font-family:inherit}
.step-btn.sel{background:#0e2a5a;border-color:var(--acc);color:var(--acc)}
.step-btn:hover:not(.sel){border-color:#2a4060;color:var(--tx)}
.manual-row{display:flex;align-items:center;justify-content:center;gap:8px;flex-wrap:wrap}
.manual-row input[type=number]{width:100px;padding:6px 10px;font-size:14px;text-align:center;background:#0d1e40;border:1px solid var(--bor);border-radius:6px;color:var(--tx);outline:none}
.manual-row input[type=number]:focus{border-color:var(--acc)}
.btn-sm{padding:6px 14px;font-size:13px;background:#0a1a3a;border:1px solid var(--bor);color:var(--acc);border-radius:6px;cursor:pointer;transition:background .1s;font-family:inherit}
.btn-sm:hover{background:#142040}
.btn-sm:disabled{opacity:.3;cursor:not-allowed}
.btn-rec{padding:6px 13px;font-size:12px;background:#160a28;border:1px solid rgba(168,85,247,.4);color:#c084fc;border-radius:6px;cursor:pointer;transition:all .15s;font-family:inherit}
.btn-rec:hover{background:#220a38;border-color:#c084fc}
.btn-rec:disabled{opacity:.3;cursor:not-allowed}
.kb-row{text-align:center;margin-top:12px;font-size:11px;color:#3a5270}
.vol-row{display:flex;align-items:center;justify-content:center;gap:10px;margin-top:14px;padding-top:14px;border-top:1px solid var(--bor)}
.vol-row label{font-size:12px;color:var(--mu)}
.vol-row input[type=range]{width:110px;accent-color:var(--acc)}
.panels-row{display:flex;gap:10px;flex-wrap:wrap}
.side-panel{background:var(--sur);border:1px solid var(--bor);border-radius:12px;padding:14px;flex:1;min-width:200px;box-shadow:0 2px 10px rgba(0,0,0,.3)}
.panel-hdr{font-size:10px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;color:var(--mu);margin-bottom:10px;display:flex;align-items:center;justify-content:space-between}
.hist-item,.preset-item{display:flex;align-items:center;gap:8px;padding:5px 8px;border-radius:6px;cursor:pointer;transition:background .12s;border:1px solid transparent}
.hist-item:hover,.preset-item:hover{background:rgba(23,168,255,.08);border-color:rgba(23,168,255,.2)}
.hist-freq,.preset-freq{font-family:'Courier New',monospace;font-size:13px;font-weight:700;color:var(--lcd);min-width:54px;flex-shrink:0}
.hist-ps,.preset-lbl{font-size:11px;color:var(--mu);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
.preset-del{font-size:11px;padding:1px 5px;border-radius:3px;background:none;border:none;color:#3a5270;cursor:pointer;font-family:inherit;transition:color .12s;flex-shrink:0}
.preset-del:hover{color:var(--al)}
.preset-save-row{display:flex;gap:6px;margin-top:10px;padding-top:10px;border-top:1px solid var(--bor)}
.preset-save-row input{flex:1;min-width:0;padding:5px 8px;font-size:12px;background:#0d1e40;border:1px solid var(--bor);border-radius:5px;color:var(--tx);outline:none}
.preset-save-row input:focus{border-color:var(--acc)}
.preset-save-row .btn-sm{padding:5px 12px;font-size:12px;flex-shrink:0}
.empty-note{font-size:11px;color:#3a5270;text-align:center;padding:8px 0}
.scan-panel{background:var(--sur);border:1px solid var(--bor);border-radius:12px;padding:14px;box-shadow:0 2px 10px rgba(0,0,0,.3)}
.btn-scan{padding:4px 14px;font-size:12px;background:#0a1e3a;border:1px solid rgba(23,168,255,.4);color:var(--acc);border-radius:6px;cursor:pointer;transition:background .12s;font-family:inherit}
.btn-scan:hover{background:#112a50}
.btn-scan:disabled{opacity:.4;cursor:not-allowed}
.scan-status{font-size:12px;color:var(--wn);margin:8px 0 2px}
.scan-peaks{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.peak-btn{font-family:'Courier New',monospace;font-size:12px;padding:5px 10px;border-radius:6px;background:#0a1628;border:1px solid var(--bor);color:var(--acc);cursor:pointer;transition:all .12px;text-align:center;font-family:inherit}
.peak-btn:hover{background:#112040;border-color:var(--acc)}
.peak-db{font-size:9px;color:var(--mu);display:block;margin-top:1px}
footer{padding:14px 20px;text-align:center;font-size:11px;color:var(--mu);border-top:1px solid var(--bor);background:rgba(6,18,34,.86)}
</style>
</head>
<body>
<header>
  <img src="/static/signalscope_icon.png" style="width:28px;height:28px;opacity:.85;flex-shrink:0">
  <h1>SignalScope</h1>
  <span class="badge">FM Scanner</span>
  <nav style="margin-left:auto;display:flex;gap:8px">
    <a href="/hub">← Hub Dashboard</a>
  </nav>
</header>

<div class="setup-bar">
  <label>Site</label>
  <select id="site-sel">
    {% for s in sites %}
    <option value="{{s.name|e}}"{% if not s.online %} disabled{% endif %}>{{s.name|e}}{% if not s.online %} (offline){% endif %}</option>
    {% endfor %}
    {% if not sites %}<option disabled>No sites with Scanner dongle</option>{% endif %}
  </select>
  <label>SDR</label>
  <select id="sdr-sel"><option value="">Auto</option></select>
  <label>Start</label>
  <input type="number" id="start-freq" value="96.5" step="0.1" min="76" max="108" style="width:82px">
  <span style="font-size:12px;color:var(--mu)">MHz</span>
  <label>Gain</label>
  <input type="number" id="gain-inp" value="40" min="0" max="50" step="1" style="width:60px" title="SDR gain (dB)">
  <label>PPM</label>
  <input type="number" id="ppm-inp" value="0" min="-100" max="100" step="1" style="width:60px" title="PPM frequency correction">
  <button class="btn-connect" id="connect-btn">Connect</button>
</div>

<main>
  <div class="page-col">

    <!-- Tuner card -->
    <div class="tuner">
      <div class="status-row">
        <span class="sdot idle" id="status-dot"></span>
        <span id="status-txt">Idle — pick a site and connect</span>
        <span class="delay-badge" id="delay-badge" style="display:none" title="Buffer ahead / RTT"></span>
      </div>
      <div class="freq-wrap">
        <div class="freq-disp" id="freq-display">---.--</div>
        <div class="freq-unit">MHz FM</div>
        <div class="freq-sub" id="freq-sub">&nbsp;</div>
        <div class="level-wrap" id="level-wrap" style="display:none">
          <span class="level-label" id="level-val">-- dBFS</span>
          <div class="level-track"><div class="level-fill" id="level-fill"></div></div>
        </div>
      </div>
      <div class="rds-wrap" id="rds-wrap" style="display:none">
        <span class="rds-ps" id="rds-ps">&nbsp;</span>
        <div class="rds-rt-outer" id="rds-rt-outer"></div>
        <div class="rds-meta" id="rds-meta"></div>
      </div>
      <div class="tune-row">
        <button class="tune-btn" data-step="-1.0" disabled>&minus;1.0</button>
        <button class="tune-btn" id="btn-dn" data-step="-0.1" disabled>&minus;0.1</button>
        <button class="tune-btn" id="btn-up" data-step="+0.1" disabled>+0.1</button>
        <button class="tune-btn" data-step="+1.0" disabled>+1.0</button>
      </div>
      <div class="step-row">
        <span class="step-lbl">Step:</span>
        <button class="step-btn" data-step="0.05">0.05</button>
        <button class="step-btn" data-step="0.1">0.1</button>
        <button class="step-btn sel" data-step="0.2">0.2</button>
        <button class="step-btn" data-step="0.5">0.5</button>
        <button class="step-btn" data-step="1.0">1.0</button>
        <span class="step-lbl">MHz</span>
      </div>
      <div class="manual-row">
        <input type="number" id="manual-freq" placeholder="97.3" min="76" max="108" step="0.1">
        <span style="font-size:12px;color:var(--mu)">MHz</span>
        <button class="btn-sm" id="manual-btn" disabled>Tune</button>
        <button class="btn-rec" id="rec30-btn" disabled title="Record 30 s WAV">&#9679; 30s</button>
        <button class="btn-rec" id="rec60-btn" disabled title="Record 60 s WAV">&#9679; 60s</button>
      </div>
      <div class="kb-row">&#8592;&#8594; tune &nbsp;&middot;&nbsp; Shift+&#8592;&#8594; &times;5 &nbsp;&middot;&nbsp; &#8593;&#8595; &plusmn;1 MHz &nbsp;&middot;&nbsp; Enter: manual</div>
      <div class="vol-row">
        <label>&#128266;</label>
        <input type="range" id="vol" min="0" max="1" step="0.05" value="0.85">
      </div>
    </div>

    <!-- History + Presets -->
    <div class="panels-row">
      <div class="side-panel">
        <div class="panel-hdr">Recent Stations</div>
        <div id="hist-list"><p class="empty-note">Tune to build history</p></div>
      </div>
      <div class="side-panel">
        <div class="panel-hdr">Presets</div>
        <div id="preset-list"><p class="empty-note">No presets saved</p></div>
        <div class="preset-save-row">
          <input id="preset-name-inp" placeholder="Station name&hellip;" maxlength="32">
          <button class="btn-sm" id="preset-save-btn" disabled>Save</button>
        </div>
      </div>
    </div>

    <!-- Band Scan -->
    <div class="scan-panel">
      <div class="panel-hdr">
        Band Scan &mdash; FM 76&ndash;108 MHz
        <button class="btn-scan" id="scan-btn" disabled>&#128269; Scan</button>
      </div>
      <div class="scan-status" id="scan-status" style="display:none">&#9203; Scanning&hellip; takes 30&ndash;60 s</div>
      <div class="scan-peaks" id="scan-peaks"></div>
    </div>

  </div>
</main>

<footer>SignalScope {{build}} &nbsp;&middot;&nbsp; Broadcast Signal Intelligence</footer>

<script nonce="{{csp_nonce()}}">
(function(){
'use strict';
function _getCsrf(){
  return (document.querySelector('meta[name="csrf-token"]')||{}).content
      || (document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/)||[])[1]
      || '';
}
function _f(url,o){
  o=o||{};
  o.headers=Object.assign({'X-CSRFToken':_getCsrf(),'Content-Type':'application/json'},o.headers||{});
  return fetch(url,o);
}
function _esc(s){return String(s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
var siteSel       = document.getElementById('site-sel');
var sdrSel        = document.getElementById('sdr-sel');
var startFreq     = document.getElementById('start-freq');
var gainInp       = document.getElementById('gain-inp');
var ppmInp        = document.getElementById('ppm-inp');
var connBtn       = document.getElementById('connect-btn');
var freqDisp      = document.getElementById('freq-display');
var freqSub       = document.getElementById('freq-sub');
var statusDot     = document.getElementById('status-dot');
var statusTxt     = document.getElementById('status-txt');
var manFreq       = document.getElementById('manual-freq');
var manBtn        = document.getElementById('manual-btn');
var volSlider     = document.getElementById('vol');
var rec30Btn      = document.getElementById('rec30-btn');
var rec60Btn      = document.getElementById('rec60-btn');
var scanBtn       = document.getElementById('scan-btn');
var presetSaveBtn = document.getElementById('preset-save-btn');
var presetNameInp = document.getElementById('preset-name-inp');
var _freq = 96.5, _step = 0.2, _state = 'idle', _slotId = '', _poll = null;
var _scanPoll = null, _scanPending = false;

// ── Web Audio API state ───────────────────────────────────────
var _audioCtx = null;
var _gainNode = null;
var _reader   = null;
var _nextTime = 0;
var _pcmBuf   = new Uint8Array(0);
var _SR       = 48000;
var _BLK_S    = 4800;
var _BLK_B    = _BLK_S * 2;
var _PRE      = 1.0;
var _sched    = 0;

function fmt(f){ return f.toFixed(2); }
function clamp(f){ return Math.max(76, Math.min(108, parseFloat(f.toFixed(2)))); }

function setStatus(state, msg){
  _state = state;
  statusDot.className = 'sdot ' + state;
  statusTxt.textContent = msg;
  var on = (state === 'streaming' || state === 'connecting');
  document.querySelectorAll('.tune-btn').forEach(function(b){ b.disabled = !on; });
  manBtn.disabled       = !on;
  rec30Btn.disabled     = !on;
  rec60Btn.disabled     = !on;
  scanBtn.disabled      = _scanPending || !siteSel.value;
  presetSaveBtn.disabled = !on;
  siteSel.disabled = on;
  sdrSel.disabled  = on;
  gainInp.disabled = on;
  ppmInp.disabled  = on;
  connBtn.textContent = on ? 'Disconnect' : 'Connect';
  connBtn.classList.toggle('active', on);
  if(!on){ document.getElementById('level-wrap').style.display = 'none'; }
}

function updateFreq(f){ _freq = f; freqDisp.textContent = fmt(f); }

// ── Signal level bar ──────────────────────────────────────────
function _updateLevel(lvl){
  var wrap = document.getElementById('level-wrap');
  var fill = document.getElementById('level-fill');
  var val  = document.getElementById('level-val');
  if(lvl === undefined || lvl === null){ wrap.style.display = 'none'; return; }
  wrap.style.display = 'flex';
  val.textContent = lvl + ' dBFS';
  var pct = Math.max(0, Math.min(100, (lvl + 96) / 96 * 100));
  fill.style.width = pct + '%';
  fill.style.background = lvl > -20 ? 'var(--ok)' : lvl > -40 ? 'var(--wn)' : 'var(--al)';
}

// ── Extended RDS display ──────────────────────────────────────
function _updateRDS(rds){
  var wrap   = document.getElementById('rds-wrap');
  var psEl   = document.getElementById('rds-ps');
  var rtOut  = document.getElementById('rds-rt-outer');
  var metaEl = document.getElementById('rds-meta');
  var ps     = ((rds && rds.ps) || '').trim();
  var rt     = ((rds && rds.rt) || '').trim();
  var pty    = (rds && rds.pty)    || '';
  var pi     = (rds && rds.pi)     || '';
  var ct     = (rds && rds.ct)     || '';
  var stereo = !!(rds && rds.stereo);
  var tp     = !!(rds && rds.tp);
  var ta     = !!(rds && rds.ta);
  var af     = (rds && Array.isArray(rds.af)) ? rds.af : [];
  if(!ps && !rt && !pty){ wrap.style.display = 'none'; return; }
  wrap.style.display = 'block';
  psEl.textContent = ps || '\u00a0';
  if(rt.length > 28){
    rtOut.innerHTML = '<span class="rds-rt-scroll"><span>'+_esc(rt)+'&nbsp;&nbsp;&nbsp;&nbsp;</span><span>'+_esc(rt)+'&nbsp;&nbsp;&nbsp;&nbsp;</span></span>';
  } else {
    rtOut.innerHTML = rt ? '<span class="rds-rt-static">'+_esc(rt)+'</span>' : '';
  }
  var b = [];
  b.push(stereo ? '<span class="rds-badge stereo-on">STEREO</span>' : '<span class="rds-badge">MONO</span>');
  if(pty) b.push('<span class="rds-badge pty-on">'+_esc(pty)+'</span>');
  if(tp)  b.push('<span class="rds-badge on">TP</span>');
  if(ta)  b.push('<span class="rds-badge on">TA</span>');
  if(pi)  b.push('<span class="rds-pi">PI:'+_esc(pi)+'</span>');
  if(ct)  b.push('<span class="rds-badge">'+_esc(ct.slice(0,16))+'</span>');
  if(af.length) b.push('<span class="rds-badge" title="'+af.map(function(f){return f+' MHz';}).join(', ')+'">AF:'+af.length+'</span>');
  metaEl.innerHTML = b.join('');
}

// ── Load SDR devices ──────────────────────────────────────────
function loadDevices(){
  var site = siteSel.value; if(!site) return;
  _f('/api/hub/scanner/devices/' + encodeURIComponent(site))
    .then(function(r){ return r.json(); })
    .then(function(d){
      sdrSel.innerHTML = '<option value="">Auto</option>';
      (d.serials || []).forEach(function(s){
        var o = document.createElement('option');
        o.value = s; o.textContent = s; sdrSel.appendChild(o);
      });
    }).catch(function(){});
}
siteSel.addEventListener('change', loadDevices);
loadDevices();

// ── Volume ────────────────────────────────────────────────────
volSlider.addEventListener('input', function(){
  if(_gainNode) _gainNode.gain.value = parseFloat(this.value);
});

// ── Connect / Disconnect ──────────────────────────────────────
connBtn.addEventListener('click', function(){
  // Prime AudioContext in the user-gesture handler (Chrome autoplay policy
  // requires resume() to be called synchronously from a user gesture; if it
  // is only called inside a .then() callback the activation may have expired).
  _initAudio();
  if(_audioCtx.state === 'suspended') _audioCtx.resume();
  if(_state === 'streaming' || _state === 'connecting'){ doStop(); }
  else { _freq = parseFloat(startFreq.value) || 96.5; doStart(_freq); }
});

function doStart(freq){
  freq = clamp(freq);
  setStatus('connecting', 'Connecting\u2026');
  updateFreq(freq);
  _f('/api/hub/scanner/start', {
    method: 'POST',
    body: JSON.stringify({
      site:       siteSel.value,
      sdr_serial: sdrSel.value,
      freq_mhz:   freq,
      gain:       parseFloat(gainInp.value) || 40,
      ppm:        parseInt(ppmInp.value)    || 0
    })
  }).then(function(r){ return r.json(); }).then(function(d){
    if(d.ok){
      _slotId = d.slot_id;
      connectAudio(d.stream_url);
      setStatus('connecting', 'Waiting for stream at ' + fmt(freq) + ' MHz\u2026');
      startPoll();
    } else { setStatus('error', 'Error: ' + (d.error || '?')); }
  }).catch(function(){ setStatus('error', 'Network error'); });
}

function doTuneOrStart(freq){
  if(_state === 'streaming' || _state === 'connecting') doTune(freq);
  else if(siteSel.value) doStart(freq);
}

function doTune(freq){
  if(_state !== 'streaming' && _state !== 'connecting') return;
  freq = clamp(freq);
  updateFreq(freq);
  freqSub.textContent = 'Retuning\u2026';
  freqDisp.style.color = 'var(--lcd-dim)';
  _updateRDS({});
  _f('/api/hub/scanner/tune', {
    method: 'POST',
    body: JSON.stringify({site: siteSel.value, freq_mhz: freq})
  }).then(function(r){ return r.json(); }).then(function(d){
    if(d.ok){
      _slotId = d.slot_id;
      connectAudio(d.stream_url);
      freqSub.textContent = '\u00a0';
      setStatus('connecting', 'Waiting for ' + fmt(freq) + ' MHz\u2026');
      _lastHistPs = ''; _lastHistFreq = freq;
      _saveHistory(freq, '');
    } else { freqSub.textContent = 'Tune failed'; }
  }).catch(function(){ freqSub.textContent = 'Network error'; });
}

function doStop(){
  stopPoll();
  stopScanPoll();
  _f('/api/hub/scanner/stop', {
    method: 'POST',
    body: JSON.stringify({site: siteSel.value})
  }).catch(function(){});
  disconnectAudio();
  _updateRDS({});
  _updateLevel(null);
  _slotId = '';
  freqDisp.textContent = '---.--';
  freqDisp.style.color = '';
  freqSub.textContent = '\u00a0';
  setStatus('idle', 'Idle \u2014 pick a site and connect');
}

// ── Web Audio API helpers ─────────────────────────────────────
function _initAudio(){
  if(_audioCtx) return;
  _audioCtx = new (window.AudioContext || window.webkitAudioContext)({sampleRate: _SR});
  _gainNode = _audioCtx.createGain();
  _gainNode.gain.value = parseFloat(volSlider.value);
  _gainNode.connect(_audioCtx.destination);
}

function disconnectAudio(){
  if(_reader){ try{ _reader.cancel(); }catch(e){} _reader = null; }
  _pcmBuf = new Uint8Array(0);
  _sched  = 0;
  _rttMs  = null;
  _delayBadge.style.display = 'none';
}

function connectAudio(url){
  disconnectAudio();
  _initAudio();
  if(_audioCtx.state === 'suspended') _audioCtx.resume();
  _nextTime = _audioCtx.currentTime + _PRE;
  _pcmBuf   = new Uint8Array(0);
  _sched    = 0;
  fetch(url, {credentials: 'same-origin'})
    .then(function(resp){
      if(!resp.ok || !resp.body){
        if(_state !== 'idle') setStatus('error', 'Stream error \u2014 try reconnecting');
        return;
      }
      _reader = resp.body.getReader();
      (function pump(){
        _reader.read().then(function(r){
          if(r.done || !_reader) return;
          _feedPCM(r.value);
          pump();
        }).catch(function(){
          if(_state !== 'idle'){ setStatus('error', 'Stream error \u2014 try reconnecting'); stopPoll(); }
        });
      })();
    })
    .catch(function(){
      if(_state !== 'idle'){ setStatus('error', 'Network error \u2014 try reconnecting'); stopPoll(); }
    });
}

function _feedPCM(chunk){
  var tmp = new Uint8Array(_pcmBuf.length + chunk.length);
  tmp.set(_pcmBuf); tmp.set(chunk, _pcmBuf.length);
  _pcmBuf = tmp;
  while(_pcmBuf.length >= _BLK_B){
    _scheduleBlock(_pcmBuf.slice(0, _BLK_B));
    _pcmBuf = _pcmBuf.slice(_BLK_B);
  }
}

function _scheduleBlock(bytes){
  // Safeguard: resume context if it ended up suspended after the click handler ran
  if(_audioCtx.state === 'suspended') _audioCtx.resume();
  var buf = _audioCtx.createBuffer(1, _BLK_S, _SR);
  var ch  = buf.getChannelData(0);
  var dv  = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  for(var i = 0; i < _BLK_S; i++) ch[i] = dv.getInt16(i * 2, true) / 32768.0;
  var src = _audioCtx.createBufferSource();
  src.buffer = buf;
  src.connect(_gainNode);
  var t = Math.max(_nextTime, _audioCtx.currentTime + 0.05);
  src.start(t);
  _nextTime = t + buf.duration;
  _sched++;
  _updateDelayBadge();
  if(_state === 'connecting' && _sched > Math.ceil(_PRE / 0.1) + 1){
    freqDisp.style.color = '';
    freqSub.textContent  = '\u00a0';
    setStatus('streaming', '\u25cf Live \u2014 ' + fmt(_freq) + ' MHz');
  }
}

// ── Status poll (2 s) ─────────────────────────────────────────
function startPoll(){ stopPoll(); _poll = setInterval(checkStatus, 2000); }
function stopPoll(){ clearInterval(_poll); _poll = null; }

var _lastHistPs = '';
var _lastHistFreq = 0;
var _rttMs = null;
var _delayBadge = document.getElementById('delay-badge');

function _updateDelayBadge(){
  if(!_audioCtx || _state === 'idle'){ _delayBadge.style.display = 'none'; return; }
  var bufMs = Math.round(Math.max(0, _nextTime - _audioCtx.currentTime) * 1000);
  var txt = 'buf\u00a0' + bufMs + '\u202fms';
  if(_rttMs !== null) txt += '\u2002rtt\u00a0' + _rttMs + '\u202fms';
  _delayBadge.textContent = txt;
  _delayBadge.style.display = '';
}

function checkStatus(){
  var site = siteSel.value; if(!site) return;
  _f('/api/hub/scanner/status/' + encodeURIComponent(site))
    .then(function(r){ return r.json(); })
    .then(function(d){
      if(!d.active){ stopPoll(); return; }
      if(d.slot_id && d.slot_id !== _slotId){ _slotId = d.slot_id; connectAudio(d.stream_url); }
      var rds = d.rds || {};
      _updateRDS(rds);
      _updateLevel(rds._level !== undefined ? rds._level : null);
      if(d.rtt_ms !== undefined && d.rtt_ms !== null) _rttMs = d.rtt_ms;
      _updateDelayBadge();
      var ps = (rds.ps || '').trim();
      if(ps && (ps !== _lastHistPs || Math.abs(_freq - _lastHistFreq) > 0.04)){
        _lastHistPs = ps;
        _lastHistFreq = _freq;
        _saveHistory(_freq, ps);
      }
    }).catch(function(){});
}

// ── Tune buttons + step ───────────────────────────────────────
document.addEventListener('click', function(e){
  var tb = e.target.closest('.tune-btn');
  if(tb && !tb.disabled){ doTune(_freq + parseFloat(tb.dataset.step || 0)); return; }
  var sb = e.target.closest('.step-btn');
  if(sb){
    _step = parseFloat(sb.dataset.step);
    document.querySelectorAll('.step-btn').forEach(function(b){ b.classList.remove('sel'); });
    sb.classList.add('sel');
    document.getElementById('btn-dn').textContent = '\u2212' + _step.toFixed(2);
    document.getElementById('btn-dn').dataset.step = (-_step).toString();
    document.getElementById('btn-up').textContent = '+' + _step.toFixed(2);
    document.getElementById('btn-up').dataset.step = _step.toString();
  }
});
manBtn.addEventListener('click', function(){
  var f = parseFloat(manFreq.value); if(!isNaN(f)) doTune(f);
});
manFreq.addEventListener('keydown', function(e){
  if(e.key === 'Enter'){ var f = parseFloat(manFreq.value); if(!isNaN(f)) doTune(f); }
});

// ── Keyboard shortcuts ────────────────────────────────────────
document.addEventListener('keydown', function(e){
  if(e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
  if(_state !== 'streaming' && _state !== 'connecting') return;
  var m = e.shiftKey ? 5 : 1;
  if(e.key === 'ArrowRight'){ e.preventDefault(); doTune(_freq + _step * m); }
  else if(e.key === 'ArrowLeft'){ e.preventDefault(); doTune(_freq - _step * m); }
  else if(e.key === 'ArrowUp'){ e.preventDefault(); doTune(_freq + 1.0 * m); }
  else if(e.key === 'ArrowDown'){ e.preventDefault(); doTune(_freq - 1.0 * m); }
});

// ── Record ────────────────────────────────────────────────────
function doRecord(secs){
  var site = siteSel.value; if(!site) return;
  var a = document.createElement('a');
  a.href = '/api/hub/scanner/record/' + encodeURIComponent(site) + '?secs=' + secs;
  a.download = '';
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
}
rec30Btn.addEventListener('click', function(){ doRecord(30); });
rec60Btn.addEventListener('click', function(){ doRecord(60); });

// ── Frequency history (localStorage) ─────────────────────────
var _HIST_KEY = 'ss_hist';
var _HIST_MAX = 10;
function _loadHistory(){ try{ return JSON.parse(localStorage.getItem(_HIST_KEY)||'[]'); }catch(e){ return []; } }
function _saveHistory(freq, ps){
  var list = _loadHistory().filter(function(h){ return Math.abs(h.f - freq) > 0.04; });
  list.unshift({f: parseFloat(freq.toFixed(2)), ps: ps || ''});
  if(list.length > _HIST_MAX) list.length = _HIST_MAX;
  try{ localStorage.setItem(_HIST_KEY, JSON.stringify(list)); }catch(e){}
  _renderHistory(list);
}
function _renderHistory(list){
  if(!list) list = _loadHistory();
  var el = document.getElementById('hist-list');
  if(!list.length){ el.innerHTML = '<p class="empty-note">Tune to build history</p>'; return; }
  el.innerHTML = list.map(function(h){
    return '<div class="hist-item" data-freq="'+h.f+'">'
      +'<span class="hist-freq">'+h.f.toFixed(2)+'</span>'
      +'<span class="hist-ps">'+_esc(h.ps || 'FM')+'</span>'
      +'</div>';
  }).join('');
}
document.addEventListener('click', function(e){
  var hi = e.target.closest('.hist-item');
  if(hi){ doTuneOrStart(parseFloat(hi.dataset.freq)); }
});
_renderHistory();

// ── Presets (localStorage) ────────────────────────────────────
var _PSET_KEY = 'ss_presets';
function _loadPresets(){ try{ return JSON.parse(localStorage.getItem(_PSET_KEY)||'[]'); }catch(e){ return []; } }
function _savePreset(name, freq){
  var list = _loadPresets().filter(function(p){ return p.name !== name; });
  list.unshift({name: name, f: parseFloat(freq.toFixed(2))});
  try{ localStorage.setItem(_PSET_KEY, JSON.stringify(list)); }catch(e){}
  _renderPresets();
}
function _deletePreset(name){
  var list = _loadPresets().filter(function(p){ return p.name !== name; });
  try{ localStorage.setItem(_PSET_KEY, JSON.stringify(list)); }catch(e){}
  _renderPresets();
}
function _renderPresets(){
  var el = document.getElementById('preset-list');
  var list = _loadPresets();
  if(!list.length){ el.innerHTML = '<p class="empty-note">No presets saved</p>'; return; }
  el.innerHTML = list.map(function(p){
    return '<div class="preset-item" data-freq="'+p.f+'">'
      +'<span class="preset-freq">'+p.f.toFixed(2)+'</span>'
      +'<span class="preset-lbl">'+_esc(p.name)+'</span>'
      +'<button class="preset-del" data-preset-name="'+_esc(p.name)+'" title="Delete">\u2715</button>'
      +'</div>';
  }).join('');
}
document.addEventListener('click', function(e){
  var del = e.target.closest('.preset-del');
  if(del){ e.stopPropagation(); _deletePreset(del.dataset.presetName); return; }
  var pi = e.target.closest('.preset-item');
  if(pi){ doTuneOrStart(parseFloat(pi.dataset.freq)); }
});
presetSaveBtn.addEventListener('click', function(){
  var name = presetNameInp.value.trim(); if(!name) return;
  _savePreset(name, _freq); presetNameInp.value = '';
});
presetNameInp.addEventListener('keydown', function(e){
  if(e.key === 'Enter'){ var n = this.value.trim(); if(n){ _savePreset(n, _freq); this.value = ''; } }
});
_renderPresets();

// ── Band scan ─────────────────────────────────────────────────
function stopScanPoll(){ clearInterval(_scanPoll); _scanPoll = null; _scanPending = false; }
scanBtn.addEventListener('click', function(){
  if(_scanPending) return;
  var site = siteSel.value; if(!site) return;
  if(_state === 'streaming' || _state === 'connecting') doStop();
  _scanPending = true;
  scanBtn.disabled = true;
  document.getElementById('scan-status').style.display = 'block';
  document.getElementById('scan-status').textContent = 'Scanning\u2026';
  document.getElementById('scan-peaks').innerHTML = '';
  var body = {
    site: site,
    sdr_serial: sdrSel ? sdrSel.value : '',
    ppm:  ppmInp  ? parseInt(ppmInp.value  || '0', 10) : 0,
    gain: gainInp ? parseFloat(gainInp.value || '38')  : 38.0,
  };
  _f('/api/hub/scanner/band_scan', {
    method: 'POST',
    body: JSON.stringify(body)
  }).then(function(r){ return r.json(); }).then(function(d){
    if(!d.ok){
      stopScanPoll();
      scanBtn.disabled = _scanPending || !siteSel.value;
      document.getElementById('scan-status').style.display = 'none';
      alert('Band scan failed: ' + (d.error || '?'));
      return;
    }
    _scanPoll = setInterval(function(){ _pollScan(site); }, 3000);
    setTimeout(function(){ _pollScan(site); }, 1000);
  }).catch(function(){
    stopScanPoll();
    scanBtn.disabled = _scanPending || !siteSel.value;
    document.getElementById('scan-status').style.display = 'none';
  });
});
function _pollScan(site){
  _f('/api/hub/scanner/scan_result/' + encodeURIComponent(site))
    .then(function(r){ return r.json(); })
    .then(function(d){
      if(d.ready){
        stopScanPoll();
        scanBtn.disabled = false;
        document.getElementById('scan-status').style.display = 'none';
        _renderScanResults(d);
      }
    }).catch(function(){});
}
function _renderScanResults(d){
  var peaks = d.peaks || [];
  if(!peaks.length){
    document.getElementById('scan-peaks').innerHTML = '<span style="font-size:12px;color:var(--mu)">No strong stations found</span>';
    return;
  }
  var sorted = peaks.slice().sort(function(a,b){ return (b.power_db||0)-(a.power_db||0); }).slice(0,24);
  document.getElementById('scan-peaks').innerHTML = sorted.map(function(p){
    var f  = parseFloat(p.freq_mhz||0).toFixed(2);
    var db = (p.power_db||0).toFixed(1);
    return '<button class="peak-btn" data-freq="'+f+'">'+f+' MHz<span class="peak-db">'+db+' dB</span></button>';
  }).join('');
}
document.addEventListener('click', function(e){
  var pb = e.target.closest('.peak-btn');
  if(pb){ doTuneOrStart(parseFloat(pb.dataset.freq)); }
});
})();
</script>
</body></html>"""


# ── Plugin entry point ─────────────────────────────────────────────────────────

def register(app, ctx):
    from flask import request, jsonify, render_template_string, Response, redirect

    monitor         = ctx["monitor"]
    hub_server      = ctx.get("hub_server")
    listen_registry = ctx["listen_registry"]
    login_required  = ctx["login_required"]
    csrf_protect    = ctx["csrf_protect"]
    BUILD           = ctx["BUILD"]

    # ── Hub: browser page ─────────────────────────────────────────────────────

    @app.get("/hub/scanner")
    @login_required
    def hub_scanner_page():
        """FM Scanner page — tune a remote site's SDR dongle in real time."""
        cfg = monitor.app_cfg
        if cfg.hub.mode not in ("hub", "both"):
            return redirect("/")
        sites = []
        if hub_server:
            now = time.time()
            with hub_server._lock:
                for sname, sdata in sorted(hub_server._sites.items()):
                    if sdata.get("approved", True) and not sdata.get("blocked"):
                        scanner_serials = sdata.get("scanner_serials", [])
                        if not scanner_serials:
                            continue
                        online = (now - sdata.get("_received", 0)) < _HUB_SITE_TIMEOUT
                        sites.append({"name": sname, "online": online,
                                      "scanner_serials": scanner_serials})
        return render_template_string(SCANNER_TPL, sites=sites, build=BUILD)

    # ── API: list scanner-role dongles for a site ─────────────────────────────

    @app.get("/api/hub/scanner/devices/<path:site_name>")
    @login_required
    def hub_scanner_devices(site_name):
        if not hub_server:
            return jsonify({"ok": False, "error": "no hub"}), 400
        site_data = hub_server._sites.get(site_name, {})
        serials   = site_data.get("scanner_serials", [])
        return jsonify({"ok": True, "serials": serials})

    # ── API: start scanner session ────────────────────────────────────────────

    @app.post("/api/hub/scanner/start")
    @login_required
    @csrf_protect
    def hub_scanner_start():
        if not hub_server:
            return jsonify({"ok": False, "error": "no hub"}), 400
        _ALLOWED_BITRATES = {"48k", "64k", "96k", "128k", "192k", "256k"}
        data       = request.get_json(silent=True) or {}
        site       = str(data.get("site", "")).strip()
        sdr_serial = str(data.get("sdr_serial", "") or "").strip()
        freq_mhz   = float(data.get("freq_mhz", 96.5) or 96.5)
        ppm        = int(data.get("ppm", 0) or 0)
        gain       = data.get("gain")
        bitrate    = str(data.get("bitrate", "128k") or "128k").strip().lower()
        if bitrate not in _ALLOWED_BITRATES:
            bitrate = "128k"
        if not site:
            return jsonify({"ok": False, "error": "site required"}), 400

        old = hub_server._scanner_sessions.pop(site, None)
        if old:
            old_slot = listen_registry.get(old["slot_id"])
            if old_slot:
                old_slot.closed = True

        slot = listen_registry.create(
            site, 0, kind="scanner", mimetype="application/octet-stream",
            freq_mhz=freq_mhz, sdr_serial=sdr_serial, ppm=ppm, gain=gain,
        )
        hub_server._scanner_sessions[site] = {
            "slot_id":    slot.slot_id,
            "freq_mhz":   freq_mhz,
            "sdr_serial": sdr_serial,
            "bitrate":    bitrate,
            "started":    time.time(),
            "rds":        {},
        }
        monitor.log(f"[Scanner] Session started for site '{site}' at {freq_mhz:.2f} MHz "
                    f"(slot {slot.slot_id[:6]})")
        stream_url = f"/hub/scanner/stream/{slot.slot_id}"
        return jsonify({"ok": True, "slot_id": slot.slot_id, "stream_url": stream_url})

    # ── API: retune ───────────────────────────────────────────────────────────

    @app.post("/api/hub/scanner/tune")
    @login_required
    @csrf_protect
    def hub_scanner_tune():
        if not hub_server:
            return jsonify({"ok": False, "error": "no hub"}), 400
        data     = request.get_json(silent=True) or {}
        site     = str(data.get("site", "")).strip()
        freq_mhz = float(data.get("freq_mhz", 96.5) or 96.5)
        if not site:
            return jsonify({"ok": False, "error": "site required"}), 400

        sess = hub_server._scanner_sessions.get(site)
        if not sess:
            return jsonify({"ok": False, "error": "no active scanner session for this site"}), 404

        old_slot = listen_registry.get(sess["slot_id"])
        if old_slot:
            old_slot.closed = True

        slot = listen_registry.create(
            site, 0, kind="scanner", mimetype="application/octet-stream",
            freq_mhz=freq_mhz,
            sdr_serial=sess.get("sdr_serial", ""),
            ppm=int(sess.get("ppm", 0)),
            gain=sess.get("gain"),
        )
        sess["slot_id"]  = slot.slot_id
        sess["freq_mhz"] = freq_mhz
        sess["rds"] = {}
        monitor.log(f"[Scanner] Retuned site '{site}' → {freq_mhz:.2f} MHz (slot {slot.slot_id[:6]})")
        stream_url = f"/hub/scanner/stream/{slot.slot_id}"
        return jsonify({"ok": True, "slot_id": slot.slot_id, "stream_url": stream_url,
                        "freq_mhz": freq_mhz})

    # ── API: stop ─────────────────────────────────────────────────────────────

    @app.post("/api/hub/scanner/stop")
    @login_required
    @csrf_protect
    def hub_scanner_stop():
        if not hub_server:
            return jsonify({"ok": False, "error": "no hub"}), 400
        data = request.get_json(silent=True) or {}
        site = str(data.get("site", "")).strip()
        sess = hub_server._scanner_sessions.pop(site, None)
        if sess:
            slot = listen_registry.get(sess["slot_id"])
            if slot:
                slot.closed = True
            monitor.log(f"[Scanner] Session stopped for site '{site}'")
        return jsonify({"ok": True})

    # ── API: session status ───────────────────────────────────────────────────

    @app.get("/api/hub/scanner/status/<path:site_name>")
    @login_required
    def hub_scanner_status(site_name):
        if not hub_server:
            return jsonify({"ok": True, "active": False})
        sess = hub_server._scanner_sessions.get(site_name)
        if not sess:
            return jsonify({"ok": True, "active": False})
        slot = listen_registry.get(sess["slot_id"])
        streaming = bool(slot and not slot.closed and not slot.stale)
        if not streaming:
            hub_server._scanner_sessions.pop(site_name, None)
            return jsonify({"ok": True, "active": False})
        stream_url = f"/hub/scanner/stream/{sess['slot_id']}"
        rtt_ms = round(slot.rtt_ema * 1000) if slot.rtt_ema else None
        return jsonify({
            "ok":        True,
            "active":    True,
            "slot_id":   sess["slot_id"],
            "freq_mhz":  sess["freq_mhz"],
            "stream_url": stream_url,
            "streaming": slot.last_chunk > slot.created,
            "rds":       sess.get("rds", {}),
            "rtt_ms":    rtt_ms,
        })

    # ── API: trigger band scan ────────────────────────────────────────────────

    @app.post("/api/hub/scanner/band_scan")
    @login_required
    @csrf_protect
    def hub_scanner_band_scan():
        if not hub_server:
            return jsonify({"ok": False, "error": "no hub"}), 400
        data      = request.get_json(silent=True) or {}
        site      = str(data.get("site", "")).strip()
        if not site:
            return jsonify({"ok": False, "error": "site required"}), 400
        sess = hub_server._scanner_sessions.get(site, {})
        hub_server._scanner_scan_results.pop(site, None)
        sdr_serial = str(data.get("sdr_serial") or sess.get("sdr_serial") or "").strip()
        ppm        = int(data.get("ppm")  if data.get("ppm")  is not None else sess.get("ppm",  0))
        gain       = float(data.get("gain") if data.get("gain") is not None else sess.get("gain", 38.0))
        hub_server.push_pending_command(site, {
            "type": "scanner_band_scan",
            "payload": {
                "sdr_serial": sdr_serial,
                "ppm":        ppm,
                "gain":       gain,
                "start_mhz":  float(data.get("start_mhz", 76.0)),
                "end_mhz":    float(data.get("end_mhz",  108.0)),
                "step_khz":   int(data.get("step_khz", 100)),
            },
        })
        monitor.log(f"[Scanner] Band scan command pushed to site '{site}'")
        return jsonify({"ok": True})

    # ── Client → Hub: receive band scan result ────────────────────────────────

    @app.post("/hub/scanner_scan_result")
    def hub_scanner_scan_result():
        """Receive band scan results from a client site (no login, signature-based auth)."""
        cfg    = monitor.app_cfg
        secret = cfg.hub.secret_key
        if cfg.hub.mode not in ("hub", "both"):
            return jsonify({"error": "not a hub"}), 404
        raw_body  = request.get_data()
        site_name = request.headers.get("X-Hub-Site", "").strip()
        if secret:
            sig  = request.headers.get("X-Hub-Sig", "")
            ts_h = request.headers.get("X-Hub-Ts", "0")
            try:
                ts = float(ts_h)
            except ValueError:
                return jsonify({"error": "invalid timestamp"}), 403
            ok, reason = _verify_sig(secret, raw_body, sig, ts)
            if not ok:
                return jsonify({"error": "forbidden", "reason": reason}), 403
        try:
            data = json.loads(raw_body)
        except Exception:
            return jsonify({"error": "bad json"}), 400
        site_name = site_name or str(data.get("site", "")).strip()
        if not site_name:
            return jsonify({"error": "missing site"}), 400
        if hub_server:
            hub_server._scanner_scan_results[site_name] = {
                "peaks":     data.get("peaks", []),
                "ts":        data.get("ts", time.time()),
                "start_mhz": data.get("start_mhz", 76.0),
                "end_mhz":   data.get("end_mhz", 108.0),
                "step_khz":  data.get("step_khz", 100),
            }
            monitor.log(f"[Scanner] Band scan result received from '{site_name}': "
                        f"{len(data.get('peaks', []))} points")
        return jsonify({"ok": True})

    # ── API: get band scan result ─────────────────────────────────────────────

    @app.get("/api/hub/scanner/scan_result/<path:site_name>")
    @login_required
    def hub_scanner_scan_result_get(site_name):
        if not hub_server:
            return jsonify({"ok": True, "ready": False})
        result = hub_server._scanner_scan_results.get(site_name)
        if not result:
            return jsonify({"ok": True, "ready": False})
        return jsonify({"ok": True, "ready": True, **result})

    # ── API: record WAV from PCM buffer ──────────────────────────────────────

    @app.get("/api/hub/scanner/record/<path:site_name>")
    @login_required
    def hub_scanner_record(site_name):
        import io as _io
        secs = min(int(request.args.get("secs", 30)), 60)
        if not hub_server:
            return jsonify({"error": "no hub"}), 400
        buf = hub_server._scanner_pcm.get(site_name)
        if not buf:
            return jsonify({"error": "no buffered audio — stream must be active"}), 404
        blocks_needed = secs * 10
        pcm_bytes = b"".join(list(buf)[-blocks_needed:])
        if not pcm_bytes:
            return jsonify({"error": "buffer empty"}), 404
        SR, CH, BPS = 48000, 1, 16
        wav_io = _io.BytesIO()
        data_size  = len(pcm_bytes)
        chunk_size = 36 + data_size
        wav_io.write(b"RIFF")
        wav_io.write(struct.pack("<I", chunk_size))
        wav_io.write(b"WAVE")
        wav_io.write(b"fmt ")
        wav_io.write(struct.pack("<IHHIIHH", 16, 1, CH, SR,
                                 SR * CH * BPS // 8, CH * BPS // 8, BPS))
        wav_io.write(b"data")
        wav_io.write(struct.pack("<I", data_size))
        wav_io.write(pcm_bytes)
        wav_io.seek(0)
        ts_str = time.strftime("%Y%m%d_%H%M%S")
        fname  = f"scanner_{_safe_name(site_name)}_{ts_str}.wav"
        from flask import send_file as _sf
        return _sf(wav_io, mimetype="audio/wav", as_attachment=True, download_name=fname)

    # ── Relay: stream PCM to browser ──────────────────────────────────────────

    def _scanner_relay_response(slot, startup_timeout=20.0):
        _BLOCK    = 48000 * 2 // 10   # 9600 bytes = 0.1 s at 48 kHz mono S16LE
        _BLK_DUR  = 0.10
        _SILENCE  = b"\x00" * _BLOCK

        def generate():
            deadline   = time.time() + startup_timeout
            started    = False
            next_sil_t = time.monotonic() + _BLK_DUR
            try:
                yield _SILENCE
                while True:
                    try:
                        chunk = slot.get(timeout=0.05)
                        started = True
                        yield chunk
                        break
                    except queue.Empty:
                        if slot.closed:
                            return
                        if time.time() > deadline:
                            return
                        now = time.monotonic()
                        if now >= next_sil_t:
                            yield _SILENCE
                            next_sil_t += _BLK_DUR

                _kp_threshold = 1.0
                _get_to       = 0.30
                next_kp_t = time.monotonic() + _kp_threshold
                _pcm_site = None
                if hub_server:
                    from collections import deque as _deque
                    for _sn, _sess in hub_server._scanner_sessions.items():
                        if _sess.get("slot_id") == slot.slot_id:
                            _pcm_site = _sn
                            if _pcm_site not in hub_server._scanner_pcm:
                                hub_server._scanner_pcm[_pcm_site] = _deque(maxlen=600)
                            break
                while True:
                    try:
                        chunk = slot.get(timeout=_get_to)
                        if slot.rtt_ema:
                            _kp_threshold = max(0.3, min(0.8, 0.1 + slot.rtt_ema * 3))
                            _get_to       = max(0.15, min(0.5, slot.rtt_ema * 2))
                        next_kp_t = time.monotonic() + _kp_threshold
                        if _pcm_site and hub_server and len(chunk) > 0:
                            hub_server._scanner_pcm[_pcm_site].append(chunk)
                        yield chunk
                    except queue.Empty:
                        if slot.closed:
                            break
                        if started and slot.stale:
                            break
                        now = time.monotonic()
                        if now >= next_kp_t:
                            yield _SILENCE
                            next_kp_t = now + _kp_threshold
            finally:
                slot.closed = True
                listen_registry.remove(slot.slot_id)

        return Response(
            generate(),
            mimetype=slot.mimetype,
            headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"},
            direct_passthrough=True,
        )

    @app.get("/hub/scanner/stream/<slot_id>")
    @login_required
    def hub_scanner_stream(slot_id):
        slot = listen_registry.get(slot_id)
        if not slot or slot.kind != "scanner":
            return "Stream not found or expired", 404
        return _scanner_relay_response(slot, startup_timeout=20.0)
