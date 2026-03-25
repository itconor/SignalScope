"""dab.py — DAB Scanner plugin for SignalScope
Drop this file alongside signalscope.py on BOTH the hub machine and the
client machine that has the RTL-SDR dongle attached.

Requirements (client machine):
    welle-cli  — must be in PATH  (https://github.com/AlbrechtL/welle.io)
    ffmpeg     — must be in PATH

Architecture:
    Client discovers services by scanning Band III DAB channels with welle-cli.
    Client tunes a service by piping welle-cli PCM → ffmpeg → MP3 chunks → hub relay.
    Hub streams MP3 to browser <audio> element.
    DLS (Dynamic Label Segment) text is extracted from welle-cli stderr and
    pushed to the hub, then polled by the browser for display.

Notes:
    welle-cli command format tested with welle.io v2.x.
    RTL-SDR device selection uses SoapySDR args: driver=rtlsdr,serial=XXXXX.
    Adjust _DAB_CHANNELS to match your region (default: European Band III).
"""

SIGNALSCOPE_PLUGIN = {
    "id":       "dab",
    "label":    "DAB Scanner",
    "url":      "/hub/dab",
    "icon":     "📻",
    "hub_only": True,
    "version":  "1.0.0",
}

import hashlib
import hmac as _hmac
import json
import os
import pathlib
import queue
import re
import shutil
import subprocess
import threading
import time
import urllib.request

# ── DAB Band III channels (European standard, kHz centre frequencies) ──────────
_DAB_CHANNELS = {
    "5A": 174928, "5B": 176640, "5C": 178352, "5D": 180064,
    "6A": 181936, "6B": 183648, "6C": 185360, "6D": 187072,
    "7A": 188928, "7B": 190640, "7C": 192352, "7D": 194064,
    "8A": 195936, "8B": 197648, "8C": 199360, "8D": 201072,
    "9A": 202928, "9B": 204640, "9C": 206352, "9D": 208064,
    "10A": 209936, "10B": 211648, "10C": 213360, "10D": 215072,
    "11A": 216928, "11B": 218640, "11C": 220352, "11D": 222064,
    "12A": 223936, "12B": 225648, "12C": 227360, "12D": 229072,
    "13A": 230784, "13B": 232496, "13C": 234208, "13D": 235776,
    "13E": 237488, "13F": 239200,
}

# ── Module-level state ──────────────────────────────────────────────────────────
_hub_sessions   = {}   # site → {slot_id, channel, service, bitrate, sdr_serial, ts}
_hub_pending    = {}   # site → command dict for client poller
_hub_dls        = {}   # site → {text, ts}
_hub_scan       = {}   # site → {status, channel, services, ts}
_state_lock     = threading.Lock()
_client_sess    = {}   # {stop, thread, proc_welle, proc_ffmpeg, slot_id}
_services_file  = None  # pathlib.Path to dab_services.json, set in register()


# ── HMAC helpers ───────────────────────────────────────────────────────────────

def _sign_chunk(secret: str, data: bytes, ts: float) -> str:
    """HMAC-SHA256 matching hub_sign_payload() in signalscope.py."""
    key = hashlib.sha256(f"{secret}:signing".encode()).digest()
    msg = f"{ts:.0f}:".encode() + data
    return _hmac.new(key, msg, hashlib.sha256).hexdigest()


def _safe_name(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in "-_ ").strip() or "stream"


# ── Services persistence ────────────────────────────────────────────────────────

def _load_services() -> dict:
    if not _services_file or not _services_file.exists():
        return {}
    try:
        return json.loads(_services_file.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_services(data: dict) -> None:
    if _services_file:
        try:
            _services_file.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                                      encoding="utf-8")
        except Exception as e:
            print(f"[DAB] Failed to save services: {e}")


# ── Browser template ────────────────────────────────────────────────────────────

DAB_TPL = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DAB Scanner — SignalScope</title>
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
.setup-bar select{padding:6px 10px;font-size:13px;background:#0d1e40;border:1px solid var(--bor);border-radius:6px;color:var(--tx);outline:none}
.setup-bar select:focus{border-color:var(--acc)}
.btn-connect{padding:7px 20px;border-radius:6px;cursor:pointer;font-size:13px;font-weight:600;border:1px solid var(--acc);background:#0e2a5a;color:#bfdbfe;transition:background .15s;font-family:inherit}
.btn-connect:hover{background:#143a80}
.btn-connect.active{background:#1a4028;border-color:var(--ok);color:#86efac}
main{flex:1;display:flex;align-items:flex-start;justify-content:center;padding:24px 16px}
.page-col{display:flex;flex-direction:column;gap:12px;width:100%;max-width:780px}
.tuner{background:var(--sur);border:1px solid var(--bor);border-radius:14px;padding:22px 24px;box-shadow:0 6px 24px rgba(0,0,0,.4)}
.status-row{display:flex;align-items:center;justify-content:center;gap:8px;margin-bottom:16px;font-size:13px;color:var(--mu)}
.sdot{width:9px;height:9px;border-radius:50%;flex-shrink:0;transition:background .3s,box-shadow .3s}
.sdot.idle{background:#2a3a4a}
.sdot.connecting{background:var(--wn);box-shadow:0 0 8px rgba(245,158,11,.7)}
.sdot.streaming{background:var(--ok);box-shadow:0 0 8px rgba(34,197,94,.6)}
.sdot.error{background:var(--al);box-shadow:0 0 8px rgba(239,68,68,.6)}
.service-disp-wrap{text-align:center;margin-bottom:14px}
.service-disp{display:inline-block;font-family:'Courier New',monospace;font-size:32px;font-weight:700;letter-spacing:1px;color:var(--lcd);text-shadow:0 0 14px rgba(0,229,255,.5);background:#030a12;border:2px solid #0c2440;border-radius:10px;padding:10px 22px;min-width:300px;max-width:100%;line-height:1.2;text-align:center;word-break:break-word}
.service-sub{font-size:12px;color:var(--mu);margin-top:5px;min-height:16px;text-align:center}
.dls-wrap{background:#040c1a;border:1px solid #0f2248;border-radius:8px;padding:10px 14px;margin-bottom:14px;min-height:46px}
.dls-label{font-size:10px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;color:var(--mu);margin-bottom:5px}
.dls-rt-outer{overflow:hidden;white-space:nowrap;text-align:center}
.dls-rt-static{font-size:13px;color:var(--lcd-dim);font-family:'Courier New',monospace}
.dls-rt-scroll{display:inline-flex;white-space:nowrap;animation:dls-marquee 18s linear infinite;font-size:13px;color:var(--lcd-dim);font-family:'Courier New',monospace}
.dls-rt-scroll span{display:inline-block;padding-right:3rem}
@keyframes dls-marquee{0%{transform:translateX(0)}100%{transform:translateX(-50%)}}
.vol-row{display:flex;align-items:center;justify-content:center;gap:10px;padding-top:14px;border-top:1px solid var(--bor)}
.vol-row label{font-size:12px;color:var(--mu)}
.vol-row input[type=range]{width:110px;accent-color:var(--acc)}
.panels-row{display:flex;gap:10px;flex-wrap:wrap}
.side-panel{background:var(--sur);border:1px solid var(--bor);border-radius:12px;padding:14px;flex:1;min-width:200px;box-shadow:0 2px 10px rgba(0,0,0,.3)}
.panel-hdr{font-size:10px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;color:var(--mu);margin-bottom:10px;display:flex;align-items:center;justify-content:space-between}
.hist-item,.preset-item{display:flex;align-items:center;gap:8px;padding:5px 8px;border-radius:6px;cursor:pointer;transition:background .12s;border:1px solid transparent}
.hist-item:hover,.preset-item:hover{background:rgba(23,168,255,.08);border-color:rgba(23,168,255,.2)}
.hist-name,.preset-svc{font-size:12px;font-weight:600;color:var(--tx);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
.hist-ch,.preset-ch{font-size:10px;color:var(--mu);font-family:'Courier New',monospace;flex-shrink:0;white-space:nowrap}
.preset-del{font-size:11px;padding:1px 5px;border-radius:3px;background:none;border:none;color:#3a5270;cursor:pointer;font-family:inherit;transition:color .12s;flex-shrink:0}
.preset-del:hover{color:var(--al)}
.preset-save-row{display:flex;gap:6px;margin-top:10px;padding-top:10px;border-top:1px solid var(--bor)}
.preset-save-row input{flex:1;min-width:0;padding:5px 8px;font-size:12px;background:#0d1e40;border:1px solid var(--bor);border-radius:5px;color:var(--tx);outline:none}
.preset-save-row input:focus{border-color:var(--acc)}
.btn-sm{padding:5px 12px;font-size:12px;background:#0a1a3a;border:1px solid var(--bor);color:var(--acc);border-radius:6px;cursor:pointer;transition:background .1s;font-family:inherit;white-space:nowrap}
.btn-sm:hover{background:#142040}
.btn-sm:disabled{opacity:.3;cursor:not-allowed}
.empty-note{font-size:11px;color:#3a5270;text-align:center;padding:8px 0}
.services-panel{background:var(--sur);border:1px solid var(--bor);border-radius:12px;padding:14px;box-shadow:0 2px 10px rgba(0,0,0,.3)}
.ensemble-group{margin-bottom:10px}
.ensemble-hdr{font-size:11px;font-weight:700;letter-spacing:.8px;text-transform:uppercase;color:var(--acc);padding:4px 6px;border-bottom:1px solid var(--bor);margin-bottom:4px;display:flex;align-items:center;justify-content:space-between}
.ensemble-ch{font-size:10px;color:var(--mu);font-family:'Courier New',monospace;font-weight:400}
.svc-item{display:flex;align-items:center;gap:8px;padding:5px 8px;border-radius:6px;cursor:pointer;transition:background .12s;border:1px solid transparent}
.svc-item:hover{background:rgba(23,168,255,.08);border-color:rgba(23,168,255,.2)}
.svc-item.active-svc{background:rgba(34,197,94,.1);border-color:rgba(34,197,94,.3)}
.svc-name{font-size:13px;font-weight:500;color:var(--tx);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.svc-badge{font-size:10px;padding:1px 5px;border-radius:3px;background:#0a1628;border:1px solid var(--bor);color:var(--mu)}
.scan-panel{background:var(--sur);border:1px solid var(--bor);border-radius:12px;padding:14px;box-shadow:0 2px 10px rgba(0,0,0,.3)}
.scan-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.btn-scan{padding:5px 14px;font-size:12px;background:#0a1e3a;border:1px solid rgba(23,168,255,.4);color:var(--acc);border-radius:6px;cursor:pointer;transition:background .12s;font-family:inherit}
.btn-scan:hover{background:#112a50}
.btn-scan:disabled{opacity:.4;cursor:not-allowed}
.scan-status{font-size:12px;color:var(--wn)}
footer{padding:14px 20px;text-align:center;font-size:11px;color:var(--mu);border-top:1px solid var(--bor);background:rgba(6,18,34,.86)}
</style>
</head>
<body>
<header>
  <img src="/static/signalscope_icon.png" style="width:28px;height:28px;opacity:.85;flex-shrink:0">
  <h1>SignalScope</h1>
  <span class="badge">DAB Scanner</span>
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
  <label>Quality</label>
  <select id="qual-sel">
    <option value="64">64 kbps</option>
    <option value="96">96 kbps</option>
    <option value="128" selected>128 kbps</option>
    <option value="192">192 kbps</option>
    <option value="256">256 kbps</option>
  </select>
  <button class="btn-connect" id="connect-btn" disabled>Connect</button>
</div>

<main>
<div class="page-col">

  <!-- Tuner card -->
  <div class="tuner">
    <div class="status-row">
      <span class="sdot idle" id="status-dot"></span>
      <span id="status-txt">Select a service from the list below</span>
    </div>
    <div class="service-disp-wrap">
      <div class="service-disp" id="svc-disp">— No service —</div>
      <div class="service-sub" id="svc-sub">&nbsp;</div>
    </div>
    <div class="dls-wrap" id="dls-wrap">
      <div class="dls-label">DLS</div>
      <div class="dls-rt-outer" id="dls-rt-outer">
        <span class="dls-rt-static" id="dls-rt-static" style="color:#2a4060">Waiting for DLS…</span>
      </div>
    </div>
    <div class="vol-row">
      <label>&#128266;</label>
      <input type="range" id="vol" min="0" max="1" step="0.05" value="0.85">
      <span id="vol-val" style="font-size:12px;color:var(--mu);width:32px">85%</span>
    </div>
  </div>

  <!-- Services browser -->
  <div class="services-panel">
    <div class="panel-hdr">
      <span>Services</span>
      <span id="svc-count" style="font-size:11px;color:var(--mu)"></span>
    </div>
    <div id="svc-list"><p class="empty-note">Scan for DAB services using the Scan panel below</p></div>
  </div>

  <!-- History + Presets -->
  <div class="panels-row">
    <div class="side-panel">
      <div class="panel-hdr">Recently Played</div>
      <div id="hist-list"><p class="empty-note">Tune to build history</p></div>
    </div>
    <div class="side-panel">
      <div class="panel-hdr">Presets</div>
      <div id="preset-list"><p class="empty-note">No presets saved</p></div>
      <div class="preset-save-row">
        <input id="preset-name-inp" placeholder="Label this service…" maxlength="32">
        <button class="btn-sm" id="preset-save-btn" disabled>Save</button>
      </div>
    </div>
  </div>

  <!-- Scan panel -->
  <div class="scan-panel">
    <div class="panel-hdr">Band Scan</div>
    <div class="scan-row">
      <button class="btn-scan" id="scan-btn" disabled>&#128268; Scan All Channels</button>
      <span class="scan-status" id="scan-status" style="display:none"></span>
    </div>
    <p style="font-size:11px;color:var(--mu);margin-top:8px">
      Scans all Band III DAB channels and builds the service list.
      Active stream will be stopped. Takes ~5–10 minutes.
    </p>
  </div>

</div>
</main>

<!-- Hidden audio element for MP3 streaming -->
<audio id="dab-audio" preload="none"></audio>

<footer>{{build}} &nbsp;&middot;&nbsp; DAB Scanner Plugin</footer>

<script nonce="{{csp_nonce()}}">
(function(){
'use strict';

var siteSel   = document.getElementById('site-sel');
var sdrSel    = document.getElementById('sdr-sel');
var qualSel   = document.getElementById('qual-sel');
var connBtn   = document.getElementById('connect-btn');
var svcDisp   = document.getElementById('svc-disp');
var svcSub    = document.getElementById('svc-sub');
var dlsOuter  = document.getElementById('dls-rt-outer');
var dlsStatic = document.getElementById('dls-rt-static');
var vol       = document.getElementById('vol');
var volVal    = document.getElementById('vol-val');
var svcList   = document.getElementById('svc-list');
var svcCount  = document.getElementById('svc-count');
var histList  = document.getElementById('hist-list');
var presetList    = document.getElementById('preset-list');
var presetNameInp = document.getElementById('preset-name-inp');
var presetSaveBtn = document.getElementById('preset-save-btn');
var scanBtn       = document.getElementById('scan-btn');
var scanStatus    = document.getElementById('scan-status');
var audioElem     = document.getElementById('dab-audio');

var _state   = 'idle';   // idle | connecting | streaming
var _service = '';
var _channel = '';
var _ensemble= '';
var _slotId  = '';
var _poll    = null;
var _scanPoll = null;
var _scanPending = false;

function _getCsrf(){
  return (document.querySelector('meta[name="csrf-token"]')||{}).content
      || (document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/)||[])[1]
      || '';
}

function _f(url, opts){
  opts = opts || {};
  opts.credentials = 'same-origin';
  if(!opts.headers) opts.headers = {};
  opts.headers['X-CSRFToken'] = _getCsrf();
  if(!opts.headers['Content-Type'] && opts.body)
    opts.headers['Content-Type'] = 'application/json';
  return fetch(url, opts);
}

function _esc(s){ var d=document.createElement('div'); d.textContent=s; return d.innerHTML; }

// ── Status display ────────────────────────────────────────────
function setStatus(cls, txt){
  _state = cls;
  var dot = document.getElementById('status-dot');
  var tx  = document.getElementById('status-txt');
  dot.className = 'sdot ' + cls;
  tx.textContent = txt;
  connBtn.textContent = (cls === 'streaming' || cls === 'connecting') ? 'Disconnect' : 'Connect';
  connBtn.className   = 'btn-connect' + (cls === 'streaming' ? ' active' : '');
  presetSaveBtn.disabled = (cls !== 'streaming');
}

// ── DLS display ───────────────────────────────────────────────
var _lastDls = '';
function _updateDls(text){
  if(text === _lastDls) return;
  _lastDls = text || '';
  if(!_lastDls){
    dlsOuter.innerHTML = '<span class="dls-rt-static" style="color:#2a4060">No DLS</span>';
    return;
  }
  if(_lastDls.length <= 50){
    dlsOuter.innerHTML = '<span class="dls-rt-static">'+_esc(_lastDls)+'</span>';
  } else {
    var s = _esc(_lastDls);
    dlsOuter.innerHTML = '<span class="dls-rt-scroll"><span>'+s+'&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</span><span>'+s+'&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</span></span>';
  }
}

// ── Volume ────────────────────────────────────────────────────
function _applyVol(v){
  audioElem.volume = parseFloat(v);
  volVal.textContent = Math.round(v * 100) + '%';
}
vol.addEventListener('input', function(){ _applyVol(this.value); });
_applyVol(vol.value);

// ── SDR device list ───────────────────────────────────────────
function loadDevices(){
  var site = siteSel.value; if(!site){ return; }
  _f('/api/hub/scanner/devices/' + encodeURIComponent(site))
    .then(function(r){ return r.json(); })
    .then(function(d){
      sdrSel.innerHTML = '<option value="">Auto</option>';
      (d.serials || []).forEach(function(s){
        var o = document.createElement('option');
        o.value = s; o.textContent = s;
        sdrSel.appendChild(o);
      });
    }).catch(function(){});
  loadServices(site);
  scanBtn.disabled = false;
}
siteSel.addEventListener('change', loadDevices);
loadDevices();

// ── Connect / Disconnect ──────────────────────────────────────
connBtn.addEventListener('click', function(){
  if(_state === 'streaming' || _state === 'connecting'){
    doStop();
  } else {
    if(!_service || !_channel){
      alert('Select a service from the list first.');
      return;
    }
    doConnect(_service, _channel, _ensemble);
  }
});

function doConnect(service, channel, ensemble){
  _service = service; _channel = channel; _ensemble = ensemble || '';
  svcDisp.textContent = service;
  svcSub.textContent  = ensemble ? (channel + ' · ' + ensemble) : channel;
  setStatus('connecting', 'Connecting to ' + service + '\u2026');
  _updateDls('');
  _markActiveService(service, channel);

  _f('/api/hub/dab/start', {
    method: 'POST',
    body: JSON.stringify({
      site:       siteSel.value,
      sdr_serial: sdrSel.value,
      service:    service,
      channel:    channel,
      bitrate:    parseInt(qualSel.value, 10) || 128,
    })
  })
  .then(function(r){ return r.json(); })
  .then(function(d){
    if(d.ok){
      _slotId = d.slot_id;
      // Set audio src and attempt play (user gesture context should still be active)
      audioElem.src = d.stream_url;
      audioElem.load();
      var prom = audioElem.play();
      if(prom !== undefined){ prom.catch(function(){}); }
      startStatusPoll();
      _saveHistory(service, channel, ensemble);
    } else {
      setStatus('error', 'Error: ' + (d.error || '?'));
    }
  })
  .catch(function(){ setStatus('error', 'Network error \u2014 try again'); });
}

function doStop(){
  stopStatusPoll();
  stopScanPoll();
  _f('/api/hub/dab/stop', {
    method: 'POST',
    body: JSON.stringify({site: siteSel.value})
  }).catch(function(){});
  audioElem.pause();
  audioElem.src = '';
  _updateDls('');
  _slotId = '';
  setStatus('idle', 'Idle \u2014 select a service');
  _markActiveService('', '');
}

// ── Status poll ───────────────────────────────────────────────
function startStatusPoll(){ stopStatusPoll(); _poll = setInterval(_checkStatus, 2500); }
function stopStatusPoll(){ if(_poll){ clearInterval(_poll); _poll = null; } }

function _checkStatus(){
  var site = siteSel.value; if(!site) return;
  _f('/api/hub/dab/status/' + encodeURIComponent(site))
    .then(function(r){ return r.json(); })
    .then(function(d){
      if(!d.active){ stopStatusPoll(); setStatus('idle', 'Stream ended'); return; }
      if(d.slot_id && d.slot_id !== _slotId){
        _slotId = d.slot_id;
        audioElem.src = d.stream_url;
        audioElem.load();
        var p = audioElem.play(); if(p) p.catch(function(){});
      }
      if(d.dls !== undefined) _updateDls(d.dls);
      if(_state === 'connecting' && d.streaming){
        setStatus('streaming', '\u25cf Live \u2014 ' + _service);
      }
    }).catch(function(){});
}

// ── Services browser ──────────────────────────────────────────
var _allServices = [];

function loadServices(site){
  _f('/api/hub/dab/services/' + encodeURIComponent(site))
    .then(function(r){ return r.json(); })
    .then(function(d){
      _allServices = d.services || [];
      _renderServices();
    }).catch(function(){});
}

function _renderServices(){
  var svcs = _allServices;
  if(!svcs.length){
    svcList.innerHTML = '<p class="empty-note">No services found — run a scan to discover DAB services</p>';
    svcCount.textContent = '';
    return;
  }
  svcCount.textContent = svcs.length + ' service' + (svcs.length !== 1 ? 's' : '');
  // Group by ensemble
  var groups = {};
  var order  = [];
  svcs.forEach(function(s){
    var key = (s.ensemble || s.channel || 'Unknown');
    if(!groups[key]){ groups[key] = {ensemble: key, channel: s.channel, svcs: []}; order.push(key); }
    groups[key].svcs.push(s);
  });
  var html = '';
  order.forEach(function(key){
    var g = groups[key];
    html += '<div class="ensemble-group">'
      + '<div class="ensemble-hdr"><span>'+_esc(g.ensemble)+'</span>'
      + '<span class="ensemble-ch">'+_esc(g.channel || '')+'</span></div>';
    g.svcs.forEach(function(s){
      var active = (s.name === _service && s.channel === _channel);
      html += '<div class="svc-item'+(active?' active-svc':'')+'" data-svc="'+_esc(s.name)+'" data-ch="'+_esc(s.channel||'')+'" data-ens="'+_esc(s.ensemble||'')+'">'
        + '<span class="svc-name">'+_esc(s.name)+'</span>'
        + '</div>';
    });
    html += '</div>';
  });
  svcList.innerHTML = html;
}

function _markActiveService(service, channel){
  svcList.querySelectorAll('.svc-item').forEach(function(el){
    var a = (el.dataset.svc === service && el.dataset.ch === channel);
    el.classList.toggle('active-svc', a);
  });
  connBtn.disabled = (!siteSel.value);
}

document.addEventListener('click', function(e){
  var si = e.target.closest('.svc-item');
  if(si){
    var svc = si.dataset.svc, ch = si.dataset.ch, ens = si.dataset.ens;
    if(_state === 'streaming' || _state === 'connecting') doStop();
    setTimeout(function(){ doConnect(svc, ch, ens); }, 100);
  }
});

// ── History (localStorage) ────────────────────────────────────
var _HIST_KEY = 'ss_dab_hist';
var _HIST_MAX = 10;
function _loadHistory(){ try{ return JSON.parse(localStorage.getItem(_HIST_KEY)||'[]'); }catch(e){ return []; } }
function _saveHistory(svc, ch, ens){
  var list = _loadHistory().filter(function(h){ return !(h.s === svc && h.c === ch); });
  list.unshift({s: svc, c: ch, e: ens || ''});
  if(list.length > _HIST_MAX) list.length = _HIST_MAX;
  try{ localStorage.setItem(_HIST_KEY, JSON.stringify(list)); }catch(ex){}
  _renderHistory(list);
}
function _renderHistory(list){
  if(!list) list = _loadHistory();
  if(!list.length){ histList.innerHTML = '<p class="empty-note">Tune to build history</p>'; return; }
  histList.innerHTML = list.map(function(h){
    return '<div class="hist-item" data-svc="'+_esc(h.s)+'" data-ch="'+_esc(h.c)+'" data-ens="'+_esc(h.e||'')+'">'
      +'<span class="hist-name">'+_esc(h.s)+'</span>'
      +'<span class="hist-ch">'+_esc(h.c)+'</span></div>';
  }).join('');
}
document.addEventListener('click', function(e){
  var hi = e.target.closest('.hist-item');
  if(hi){
    if(_state === 'streaming' || _state === 'connecting') doStop();
    var svc = hi.dataset.svc, ch = hi.dataset.ch, ens = hi.dataset.ens;
    setTimeout(function(){ doConnect(svc, ch, ens); }, 100);
  }
});
_renderHistory();

// ── Presets (localStorage) ────────────────────────────────────
var _PSET_KEY = 'ss_dab_presets';
function _loadPresets(){ try{ return JSON.parse(localStorage.getItem(_PSET_KEY)||'[]'); }catch(e){ return []; } }
function _savePreset(label, svc, ch, ens){
  var list = _loadPresets().filter(function(p){ return p.label !== label; });
  list.unshift({label: label, s: svc, c: ch, e: ens || ''});
  try{ localStorage.setItem(_PSET_KEY, JSON.stringify(list)); }catch(ex){}
  _renderPresets();
}
function _deletePreset(label){
  var list = _loadPresets().filter(function(p){ return p.label !== label; });
  try{ localStorage.setItem(_PSET_KEY, JSON.stringify(list)); }catch(ex){}
  _renderPresets();
}
function _renderPresets(){
  var list = _loadPresets();
  if(!list.length){ presetList.innerHTML = '<p class="empty-note">No presets saved</p>'; return; }
  presetList.innerHTML = list.map(function(p){
    return '<div class="preset-item" data-svc="'+_esc(p.s)+'" data-ch="'+_esc(p.c)+'" data-ens="'+_esc(p.e||'')+'">'
      +'<span class="preset-svc">'+_esc(p.label)+'</span>'
      +'<span class="preset-ch">'+_esc(p.c)+'</span>'
      +'<button class="preset-del" data-preset-label="'+_esc(p.label)+'" title="Delete">\u2715</button>'
      +'</div>';
  }).join('');
}
document.addEventListener('click', function(e){
  var del = e.target.closest('.preset-del');
  if(del){ e.stopPropagation(); _deletePreset(del.dataset.presetLabel); return; }
  var pi = e.target.closest('.preset-item');
  if(pi){
    if(_state === 'streaming' || _state === 'connecting') doStop();
    var svc = pi.dataset.svc, ch = pi.dataset.ch, ens = pi.dataset.ens;
    setTimeout(function(){ doConnect(svc, ch, ens); }, 100);
  }
});
presetSaveBtn.addEventListener('click', function(){
  var label = presetNameInp.value.trim();
  if(!label || !_service) return;
  _savePreset(label, _service, _channel, _ensemble);
  presetNameInp.value = '';
});
presetNameInp.addEventListener('keydown', function(e){
  if(e.key === 'Enter'){
    var label = this.value.trim();
    if(label && _service){ _savePreset(label, _service, _channel, _ensemble); this.value = ''; }
  }
});
_renderPresets();

// ── Band scan ──────────────────────────────────────────────────
function stopScanPoll(){ if(_scanPoll){ clearInterval(_scanPoll); _scanPoll = null; } _scanPending = false; }

scanBtn.addEventListener('click', function(){
  if(_scanPending) return;
  var site = siteSel.value; if(!site) return;
  if(_state === 'streaming' || _state === 'connecting') doStop();
  _scanPending = true;
  scanBtn.disabled = true;
  scanStatus.style.display = '';
  scanStatus.textContent = 'Starting scan\u2026';

  _f('/api/hub/dab/scan', {
    method: 'POST',
    body: JSON.stringify({site: site, sdr_serial: sdrSel.value})
  })
  .then(function(r){ return r.json(); })
  .then(function(d){
    if(!d.ok){
      stopScanPoll();
      scanBtn.disabled = false;
      scanStatus.style.display = 'none';
      alert('Scan failed: ' + (d.error || '?'));
      return;
    }
    _scanPoll = setInterval(function(){ _pollScan(site); }, 4000);
    setTimeout(function(){ _pollScan(site); }, 2000);
  })
  .catch(function(){
    stopScanPoll();
    scanBtn.disabled = false;
    scanStatus.style.display = 'none';
  });
});

function _pollScan(site){
  _f('/api/hub/dab/scan_status/' + encodeURIComponent(site))
    .then(function(r){ return r.json(); })
    .then(function(d){
      if(d.status === 'scanning'){
        scanStatus.textContent = 'Scanning ' + (d.channel || '\u2026') + ' (' + (d.found || 0) + ' found so far)';
      } else if(d.status === 'done' || d.status === 'idle'){
        stopScanPoll();
        scanBtn.disabled = false;
        scanStatus.style.display = 'none';
        loadServices(site);
      }
    }).catch(function(){});
}

// Initial enable state
connBtn.disabled = !siteSel.value;

})();
</script>
</body></html>"""


# ── Plugin entry point ──────────────────────────────────────────────────────────

def register(app, ctx):
    from flask import request, jsonify, render_template_string, Response, redirect

    global _services_file

    monitor         = ctx["monitor"]
    hub_server      = ctx.get("hub_server")
    listen_registry = ctx["listen_registry"]
    login_required  = ctx["login_required"]
    csrf_protect    = ctx["csrf_protect"]
    BUILD           = ctx["BUILD"]

    # Locate the services JSON file alongside this plugin
    _services_file = pathlib.Path(__file__).parent / "dab_services.json"

    # ── Hub: browser page ──────────────────────────────────────────────────────

    @app.get("/hub/dab")
    @login_required
    def dab_page():
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
                        online = (now - sdata.get("_received", 0)) < 30.0
                        sites.append({"name": sname, "online": online,
                                      "scanner_serials": scanner_serials})
        return render_template_string(DAB_TPL, sites=sites, build=BUILD)

    # ── Hub: start DAB stream ──────────────────────────────────────────────────

    @app.post("/api/hub/dab/start")
    @login_required
    @csrf_protect
    def dab_start():
        if not hub_server:
            return jsonify({"ok": False, "error": "no hub"}), 400
        data       = request.get_json(silent=True) or {}
        site       = str(data.get("site", "")).strip()
        sdr_serial = str(data.get("sdr_serial", "") or "").strip()
        service    = str(data.get("service", "")).strip()
        channel    = str(data.get("channel", "")).strip().upper()
        bitrate    = int(data.get("bitrate", 128) or 128)
        if bitrate not in (64, 96, 128, 192, 256):
            bitrate = 128
        if not site or not service or not channel:
            return jsonify({"ok": False, "error": "site, service, channel required"}), 400

        # Expire old session
        old = _hub_sessions.pop(site, None)
        if old:
            old_slot = listen_registry.get(old.get("slot_id", ""))
            if old_slot:
                old_slot.closed = True

        slot = listen_registry.create(
            site, 0, kind="scanner", mimetype="audio/mpeg",
            freq_mhz=0,
        )
        with _state_lock:
            _hub_sessions[site] = {
                "slot_id":    slot.slot_id,
                "service":    service,
                "channel":    channel,
                "bitrate":    bitrate,
                "sdr_serial": sdr_serial,
                "ts":         time.time(),
            }
            _hub_pending[site] = {
                "action":     "start",
                "slot_id":    slot.slot_id,
                "service":    service,
                "channel":    channel,
                "bitrate":    bitrate,
                "sdr_serial": sdr_serial,
            }
        monitor.log(f"[DAB] Session started: site='{site}' service='{service}' ch={channel}")
        return jsonify({"ok": True, "slot_id": slot.slot_id,
                        "stream_url": f"/hub/dab/stream/{slot.slot_id}"})

    # ── Hub: stop DAB stream ───────────────────────────────────────────────────

    @app.post("/api/hub/dab/stop")
    @login_required
    @csrf_protect
    def dab_stop():
        if not hub_server:
            return jsonify({"ok": False, "error": "no hub"}), 400
        data = request.get_json(silent=True) or {}
        site = str(data.get("site", "")).strip()
        with _state_lock:
            sess = _hub_sessions.pop(site, None)
            _hub_pending[site] = {"action": "stop"}
        if sess:
            slot = listen_registry.get(sess.get("slot_id", ""))
            if slot:
                slot.closed = True
        monitor.log(f"[DAB] Session stopped: site='{site}'")
        return jsonify({"ok": True})

    # ── Hub: session status (browser polls every 2.5 s) ────────────────────────

    @app.get("/api/hub/dab/status/<path:site_name>")
    @login_required
    def dab_status(site_name):
        sess = _hub_sessions.get(site_name)
        if not sess:
            return jsonify({"ok": True, "active": False})
        slot = listen_registry.get(sess.get("slot_id", ""))
        if not slot or slot.closed or slot.stale:
            _hub_sessions.pop(site_name, None)
            return jsonify({"ok": True, "active": False})
        dls_entry = _hub_dls.get(site_name, {})
        streaming = slot.last_chunk > slot.created
        return jsonify({
            "ok":        True,
            "active":    True,
            "slot_id":   sess["slot_id"],
            "service":   sess.get("service", ""),
            "channel":   sess.get("channel", ""),
            "stream_url": f"/hub/dab/stream/{sess['slot_id']}",
            "streaming": streaming,
            "dls":       dls_entry.get("text", ""),
        })

    # ── Hub: services list for a site ─────────────────────────────────────────

    @app.get("/api/hub/dab/services/<path:site_name>")
    @login_required
    def dab_services(site_name):
        db = _load_services()
        entry = db.get(site_name, {})
        return jsonify({
            "ok":         True,
            "services":   entry.get("services", []),
            "scanned_at": entry.get("scanned_at", ""),
        })

    # ── Hub: trigger band scan ─────────────────────────────────────────────────

    @app.post("/api/hub/dab/scan")
    @login_required
    @csrf_protect
    def dab_scan():
        if not hub_server:
            return jsonify({"ok": False, "error": "no hub"}), 400
        data       = request.get_json(silent=True) or {}
        site       = str(data.get("site", "")).strip()
        sdr_serial = str(data.get("sdr_serial", "") or "").strip()
        if not site:
            return jsonify({"ok": False, "error": "site required"}), 400
        with _state_lock:
            _hub_scan[site] = {"status": "scanning", "channel": "", "found": 0, "ts": time.time()}
            _hub_pending[site] = {
                "action":     "scan",
                "sdr_serial": sdr_serial,
            }
        monitor.log(f"[DAB] Band scan triggered for site '{site}'")
        return jsonify({"ok": True})

    # ── Hub: scan status (browser polls during scan) ───────────────────────────

    @app.get("/api/hub/dab/scan_status/<path:site_name>")
    @login_required
    def dab_scan_status(site_name):
        entry = _hub_scan.get(site_name, {"status": "idle"})
        return jsonify({"ok": True, **entry})

    # ── Hub: client polls for commands ─────────────────────────────────────────
    # No login_required — authenticated by site being approved in hub_server._sites

    @app.get("/api/hub/dab/cmd")
    def dab_cmd_poll():
        if not hub_server:
            return jsonify({}), 200
        site = request.headers.get("X-Dab-Site", "").strip()
        if not site:
            return jsonify({}), 400
        sdata = hub_server._sites.get(site, {})
        if not sdata.get("_approved"):
            return jsonify({}), 403
        with _state_lock:
            cmd = _hub_pending.pop(site, None)
        return jsonify({"cmd": cmd} if cmd else {})

    # ── Client → Hub: push DLS update ─────────────────────────────────────────

    @app.post("/api/hub/dab/dls/<path:site_name>")
    def dab_dls_push(site_name):
        if not hub_server:
            return "", 204
        sdata = hub_server._sites.get(site_name, {})
        if not sdata.get("_approved"):
            return "", 403
        d = request.get_json(silent=True) or {}
        text = str(d.get("text", "")).strip()
        _hub_dls[site_name] = {"text": text, "ts": time.time()}
        return "", 204

    # ── Client → Hub: push scan progress ──────────────────────────────────────

    @app.post("/api/hub/dab/scan_progress/<path:site_name>")
    def dab_scan_progress_push(site_name):
        if not hub_server:
            return "", 204
        sdata = hub_server._sites.get(site_name, {})
        if not sdata.get("_approved"):
            return "", 403
        d = request.get_json(silent=True) or {}
        with _state_lock:
            entry = _hub_scan.setdefault(site_name, {"status": "scanning", "found": 0, "ts": time.time()})
            entry["status"]  = "scanning"
            entry["channel"] = str(d.get("channel", ""))
            entry["found"]   = int(d.get("found", entry.get("found", 0)))
        return "", 204

    # ── Client → Hub: push final scan results ─────────────────────────────────

    @app.post("/api/hub/dab/scan_result/<path:site_name>")
    def dab_scan_result_push(site_name):
        if not hub_server:
            return "", 204
        sdata = hub_server._sites.get(site_name, {})
        if not sdata.get("_approved"):
            return "", 403
        d = request.get_json(silent=True) or {}
        services   = d.get("services", [])
        scanned_at = d.get("scanned_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
        db = _load_services()
        db[site_name] = {"services": services, "scanned_at": scanned_at}
        _save_services(db)
        with _state_lock:
            _hub_scan[site_name] = {"status": "done", "found": len(services), "ts": time.time()}
        monitor.log(f"[DAB] Scan complete for site '{site_name}': {len(services)} services")
        return "", 204

    # ── Relay: MP3 stream to browser ───────────────────────────────────────────
    # Custom relay without PCM silence injection (MP3 framing is not PCM)

    @app.get("/hub/dab/stream/<slot_id>")
    @login_required
    def dab_stream(slot_id):
        slot = listen_registry.get(slot_id)
        if not slot or slot.kind != "scanner":
            return "Stream not found or expired", 404

        def generate():
            deadline = time.time() + 20.0
            try:
                while True:
                    try:
                        chunk = slot.get(timeout=0.5)
                        yield chunk
                        break
                    except queue.Empty:
                        if slot.closed:
                            return
                        if time.time() > deadline:
                            return
                while True:
                    try:
                        chunk = slot.get(timeout=1.0)
                        yield chunk
                    except queue.Empty:
                        if slot.closed:
                            break
                        if slot.stale:
                            break
            finally:
                slot.closed = True
                listen_registry.remove(slot.slot_id)

        return Response(
            generate(),
            mimetype="audio/mpeg",
            headers={
                "Cache-Control":    "no-cache, no-store",
                "X-Accel-Buffering": "no",
                "Accept-Ranges":    "none",
            },
            direct_passthrough=True,
        )

    # ── Start client command poller (on client machines) ──────────────────────

    cfg = monitor.app_cfg
    if cfg.hub.mode in ("client", "both") and cfg.hub.hub_url:
        t = threading.Thread(target=_client_poller, args=(monitor,),
                              daemon=True, name="DABCmdPoller")
        t.start()
        print("[DAB] Client command poller started")


# ──────────────────────────────────────────────────────────────────────────────
# Client-side: command polling + dispatch
# ──────────────────────────────────────────────────────────────────────────────

def _client_poller(monitor):
    """Poll hub every 3 s for pending DAB commands."""
    import json as _json
    while True:
        try:
            cfg     = monitor.app_cfg
            hub_url = (cfg.hub.hub_url or "").rstrip("/")
            site    = (cfg.hub.site_name or "").strip()
            if hub_url and site:
                req = urllib.request.Request(
                    f"{hub_url}/api/hub/dab/cmd",
                    headers={"X-Dab-Site": site},
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    d = _json.loads(resp.read())
                cmd = d.get("cmd")
                if cmd:
                    _dispatch_client_cmd(cmd, hub_url, site, cfg)
        except Exception:
            pass
        time.sleep(3)


def _dispatch_client_cmd(cmd, hub_url, site, cfg):
    action = cmd.get("action", "")
    if action == "start":
        _stop_stream()
        _start_stream(
            slot_id    = cmd["slot_id"],
            channel    = cmd.get("channel", ""),
            service    = cmd.get("service", ""),
            bitrate    = int(cmd.get("bitrate", 128) or 128),
            sdr_serial = cmd.get("sdr_serial", ""),
            hub_url    = hub_url,
            site       = site,
            secret     = cfg.hub.secret_key or "",
        )
    elif action == "stop":
        _stop_stream()
    elif action == "scan":
        t = threading.Thread(
            target=_do_scan,
            args=(site, cmd.get("sdr_serial", ""), hub_url),
            daemon=True,
            name="DABScanner",
        )
        t.start()


def _start_stream(slot_id, channel, service, bitrate, sdr_serial, hub_url, site, secret):
    _stop_stream()
    stop = threading.Event()
    t = threading.Thread(
        target=_stream_worker,
        args=(slot_id, channel, service, bitrate, sdr_serial, hub_url, site, secret, stop),
        daemon=True,
        name="DABWorker",
    )
    t.start()
    with _state_lock:
        _client_sess.clear()
        _client_sess.update({"stop": stop, "thread": t, "slot_id": slot_id})


def _stop_stream():
    with _state_lock:
        old = dict(_client_sess)
        _client_sess.clear()
    if old.get("stop"):
        old["stop"].set()
    for key in ("proc_welle", "proc_ffmpeg"):
        proc = old.get(key)
        if proc:
            try:
                proc.terminate()
            except Exception:
                pass
    if old.get("thread"):
        old["thread"].join(timeout=3.0)


# ──────────────────────────────────────────────────────────────────────────────
# Client-side: DAB audio streaming worker
# ──────────────────────────────────────────────────────────────────────────────

def _stream_worker(slot_id, channel, service, bitrate, sdr_serial, hub_url, site, secret, stop):
    """
    Launch welle-cli | ffmpeg pipeline and POST MP3 chunks to hub relay.

    welle-cli outputs:
      stdout → raw PCM  S16LE stereo 48000 Hz
      stderr → status lines including DLS text

    ffmpeg transcodes PCM → MP3 at the requested bitrate.
    """
    welle_bin  = shutil.which("welle-cli")
    ffmpeg_bin = shutil.which("ffmpeg")

    if not welle_bin:
        print("[DAB] ERROR: welle-cli not found in PATH")
        return
    if not ffmpeg_bin:
        print("[DAB] ERROR: ffmpeg not found in PATH")
        return

    chunk_url = f"{hub_url}/api/v1/audio_chunk/{slot_id}"

    # welle-cli command
    welle_cmd = [welle_bin, "-c", channel, "-s", service, "-A", "stdout"]
    if sdr_serial:
        welle_cmd[1:1] = ["-D", f"driver=rtlsdr,serial={sdr_serial}"]

    # ffmpeg: PCM S16LE stereo 48kHz → MP3
    ffmpeg_cmd = [
        ffmpeg_bin,
        "-loglevel", "error",
        "-f", "s16le", "-ar", "48000", "-ac", "2",
        "-i", "pipe:0",
        "-c:a", "libmp3lame",
        "-b:a", f"{bitrate}k",
        "-f", "mp3", "pipe:1",
    ]

    print(f"[DAB] Starting stream: ch={channel} service='{service}' "
          f"bitrate={bitrate}k serial={sdr_serial or 'auto'}")

    proc_welle  = None
    proc_ffmpeg = None

    try:
        proc_welle = subprocess.Popen(
            welle_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        proc_ffmpeg = subprocess.Popen(
            ffmpeg_cmd,
            stdin=proc_welle.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        # Allow proc_welle to receive SIGPIPE when ffmpeg exits
        proc_welle.stdout.close()

        # Register processes so _stop_stream() can terminate them
        with _state_lock:
            if _client_sess.get("slot_id") == slot_id:
                _client_sess["proc_welle"]  = proc_welle
                _client_sess["proc_ffmpeg"] = proc_ffmpeg

        # DLS reader thread (reads welle-cli stderr)
        dls_thread = threading.Thread(
            target=_dls_reader,
            args=(proc_welle.stderr, site, hub_url, stop),
            daemon=True,
            name="DABDlsReader",
        )
        dls_thread.start()

        # Read MP3 from ffmpeg and POST chunks to hub
        _CHUNK = 8192  # ~0.5 s at 128 kbps
        out_deadline = None

        while not stop.is_set():
            chunk = proc_ffmpeg.stdout.read(_CHUNK)
            if not chunk:
                break

            # Pace output: don't send faster than real-time
            if out_deadline is None:
                out_deadline = time.monotonic()
            wait = out_deadline - time.monotonic()
            if wait > 0:
                time.sleep(min(wait, 0.2))

            dur = len(chunk) * 8 / (bitrate * 1000)
            out_deadline += dur

            ts  = time.time()
            sig = _sign_chunk(secret, chunk, ts) if secret else ""
            req = urllib.request.Request(
                chunk_url,
                data=chunk,
                method="POST",
                headers={
                    "Content-Type":  "application/octet-stream",
                    "X-Hub-Sig":     sig,
                    "X-Hub-Ts":      str(int(ts)),
                    "X-Hub-Nonce":   hashlib.md5(os.urandom(8)).hexdigest()[:16],
                },
            )
            try:
                urllib.request.urlopen(req, timeout=5).close()
            except Exception:
                pass

    except Exception as e:
        print(f"[DAB] Stream worker error: {e}")
    finally:
        for p in (proc_ffmpeg, proc_welle):
            if p:
                try:
                    p.terminate()
                except Exception:
                    pass
        print(f"[DAB] Stream worker exited: ch={channel} service='{service}'")


def _dls_reader(stderr_stream, site, hub_url, stop):
    """
    Read DLS text from welle-cli stderr and push updates to hub.

    welle-cli typically emits:
        DLS: <text>
    or variations like:  DLS update: <text>
    """
    dls_url  = f"{hub_url}/api/hub/dab/dls/{site}"
    last_dls = ""

    try:
        for raw_line in stderr_stream:
            if stop.is_set():
                break
            try:
                line = raw_line.decode("utf-8", errors="replace").strip()
            except Exception:
                continue

            # Match common welle-cli DLS output patterns
            m = re.search(r"DLS[:\s]+(.+)", line, re.IGNORECASE)
            if not m:
                m = re.search(r"label[:\s]+(.+)", line, re.IGNORECASE)
            if not m:
                continue

            dls = m.group(1).strip().strip('"\'')
            if dls == last_dls or not dls:
                continue
            last_dls = dls

            try:
                data = json.dumps({"text": dls}).encode()
                req  = urllib.request.Request(
                    dls_url,
                    data=data,
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "X-Dab-Site":   site,
                    },
                )
                urllib.request.urlopen(req, timeout=3).close()
            except Exception:
                pass
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Client-side: DAB band scan
# ──────────────────────────────────────────────────────────────────────────────

def _do_scan(site, sdr_serial, hub_url):
    """
    Scan all Band III DAB channels with welle-cli, collect service names,
    and push the complete service list to the hub.

    welle-cli is run for each channel for up to 12 seconds.
    Services are identified by parsing the combined stdout/stderr output.
    """
    import json as _json

    welle_bin = shutil.which("welle-cli")
    if not welle_bin:
        print("[DAB] Scan aborted: welle-cli not found in PATH")
        return

    progress_url = f"{hub_url}/api/hub/dab/scan_progress/{site}"
    result_url   = f"{hub_url}/api/hub/dab/scan_result/{site}"

    all_services = []
    channels = list(_DAB_CHANNELS.keys())

    print(f"[DAB] Band scan started for site '{site}': {len(channels)} channels")

    for ch in channels:
        # Build scan command (no -s = no service, no -A = no audio)
        cmd = [welle_bin, "-c", ch]
        if sdr_serial:
            cmd[1:1] = ["-D", f"driver=rtlsdr,serial={sdr_serial}"]

        # Report progress
        try:
            d = _json.dumps({"channel": ch, "found": len(all_services)}).encode()
            req = urllib.request.Request(
                progress_url, data=d, method="POST",
                headers={"Content-Type": "application/json", "X-Dab-Site": site},
            )
            urllib.request.urlopen(req, timeout=3).close()
        except Exception:
            pass

        # Run welle-cli, collect output with a timeout
        output_lines = []
        done_evt = threading.Event()

        def _reader(proc):
            try:
                for line in proc.stdout:
                    output_lines.append(line)
            except Exception:
                pass
            done_evt.set()

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            )
            t = threading.Thread(target=_reader, args=(proc,), daemon=True)
            t.start()
            done_evt.wait(timeout=12)
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                pass
        except Exception as e:
            print(f"[DAB] Scan error on {ch}: {e}")
            continue

        if not output_lines:
            continue

        text = b"".join(output_lines).decode("utf-8", errors="replace")

        # Try to extract ensemble name
        ensemble_name = ch  # default to channel name
        for pat in [
            r'ensemble[:\s"]+([^\n"]+)',
            r'label[:\s"]+([^\n"]+)',
            r'mux[:\s"]+([^\n"]+)',
        ]:
            em = re.search(pat, text, re.IGNORECASE)
            if em:
                name = em.group(1).strip().strip('"\'').strip()
                if name and len(name) > 1:
                    ensemble_name = name
                    break

        # Extract service names using multiple patterns
        found_in_channel = set()
        patterns = [
            # "Service: NAME" or "Service NAME"
            r"[Ss]ervice[:\s]+['\"]?([^'\"\n\r\t,]+)['\"]?",
            # NAME (SID: 0xXXXX)
            r"([A-Za-z][A-Za-z0-9 &+\-\.]{2,40})\s+\(SID",
            # Quoted service names
            r"'([A-Za-z][A-Za-z0-9 &+\-\.]{2,40})'",
        ]
        for pat in patterns:
            for m in re.finditer(pat, text):
                name = m.group(1).strip()
                if (name and len(name) >= 2 and len(name) <= 64
                        and name not in found_in_channel
                        and not name.lower().startswith("using")
                        and not name.lower().startswith("found")
                        and not name.lower().startswith("error")
                        and not name.lower().startswith("trying")):
                    found_in_channel.add(name)

        for svc_name in sorted(found_in_channel):
            all_services.append({
                "name":     svc_name,
                "channel":  ch,
                "ensemble": ensemble_name,
            })

        if found_in_channel:
            print(f"[DAB] {ch}: {len(found_in_channel)} services ({ensemble_name})")

    # Push final results to hub
    try:
        d = _json.dumps({
            "services":   all_services,
            "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }).encode()
        req = urllib.request.Request(
            result_url, data=d, method="POST",
            headers={"Content-Type": "application/json", "X-Dab-Site": site},
        )
        urllib.request.urlopen(req, timeout=5).close()
        print(f"[DAB] Scan complete: {len(all_services)} services found across {len(channels)} channels")
    except Exception as e:
        print(f"[DAB] Failed to push scan results: {e}")
