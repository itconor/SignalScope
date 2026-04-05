"""dab.py — DAB Scanner plugin for SignalScope
Drop this file alongside signalscope.py on BOTH the hub machine and the
client machine that has the RTL-SDR dongle attached.

Requirements (client machine):
    welle-cli  — must be in PATH  (https://github.com/AlbrechtL/welle.io)
    ffmpeg     — must be in PATH

Architecture:
    Client discovers services by scanning Band III DAB channels with welle-cli.
    Client tunes a service by running welle-cli in HTTP server mode (-w PORT),
    then ffmpeg reads from http://localhost:PORT/mp3/{SID} → MP3 chunks → hub relay.
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
    "version":  "1.0.34",
}

import hashlib
import hmac as _hmac
import json as _json
import os
import pathlib
import queue
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
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

# ── Scan region hierarchy ──────────────────────────────────────────────────────
# Each node: {id, label, icon, channels: [ch, ...], children: [...]}
# The browser renders a collapsible tree; any node can trigger a scan of its channels.
_SCAN_REGIONS = {
    "id": "europe", "label": "All Europe — Band III", "icon": "🌍",
    "channels": list(_DAB_CHANNELS.keys()),
    "children": [
        {
            "id": "uk", "label": "United Kingdom", "icon": "🇬🇧",
            "channels": ["10B","10C","11A","11B","11C","11D","12A","12B","12C","12D"],
            "children": [
                {"id":"uk_ni",        "label":"Northern Ireland",    "icon":"", "channels":["11D","11A","12B","12D","9A","9C"],  "children":[]},
                {"id":"uk_scotland",  "label":"Scotland",            "icon":"", "channels":["11D","11B","11C","12B","12C"],      "children":[]},
                {"id":"uk_wales",     "label":"Wales",               "icon":"", "channels":["11D","11A","12B","12C"],            "children":[]},
                {"id":"uk_national",  "label":"England — National",  "icon":"", "channels":["11D","12B","10B"],                  "children":[]},
                {"id":"uk_london",    "label":"London",              "icon":"", "channels":["11D","12B","10B","10C","11C","12D"],"children":[]},
                {"id":"uk_northwest", "label":"North West England",  "icon":"", "channels":["11D","12B","10B","11A","11C"],      "children":[]},
                {"id":"uk_northeast", "label":"North East England",  "icon":"", "channels":["11D","12B","10B","11B"],            "children":[]},
                {"id":"uk_yorkshire", "label":"Yorkshire",           "icon":"", "channels":["11D","12B","10B","11B","12A"],      "children":[]},
                {"id":"uk_midlands",  "label":"Midlands",            "icon":"", "channels":["11D","12B","10B","11A"],            "children":[]},
                {"id":"uk_south",     "label":"South England",       "icon":"", "channels":["11D","12B","10B","11C","12C"],      "children":[]},
            ],
        },
        {
            "id": "ireland", "label": "Republic of Ireland", "icon": "🇮🇪",
            "channels": ["9D","11B","11D"],
            "children": [],
        },
        {
            "id": "germany", "label": "Germany", "icon": "🇩🇪",
            "channels": ["5A","5C","7A","7B","7C","7D","8A","8D","9A","9D","10D"],
            "children": [],
        },
        {
            "id": "netherlands", "label": "Netherlands", "icon": "🇳🇱",
            "channels": ["7C","8A","8B","9B","10A","11A"],
            "children": [],
        },
        {
            "id": "france", "label": "France", "icon": "🇫🇷",
            "channels": ["10A","10B","10C","10D","11A","11B"],
            "children": [],
        },
        {
            "id": "norway", "label": "Norway", "icon": "🇳🇴",
            "channels": ["7B","9D","10A","10B","11D","12A"],
            "children": [],
        },
        {
            "id": "denmark", "label": "Denmark", "icon": "🇩🇰",
            "channels": ["10B","10C","10D","11A","11B","11C"],
            "children": [],
        },
        {
            "id": "belgium", "label": "Belgium", "icon": "🇧🇪",
            "channels": ["7B","8A","10B","11D"],
            "children": [],
        },
        {
            "id": "switzerland", "label": "Switzerland", "icon": "🇨🇭",
            "channels": ["7A","7B","7C","8A","12D"],
            "children": [],
        },
    ],
}

# ── Module-level state ──────────────────────────────────────────────────────────
_hub_sessions    = {}   # site → {slot_id, channel, service, bitrate, sdr_serial, ts}
_hub_pending     = {}   # site → command dict for client poller
_hub_dls         = {}   # site → {text, ts}
_hub_scan        = {}   # site → {status, channel, progress, total, found, muxes, ts}
_hub_scan_stop   = set()  # sites that have requested scan abort
_hub_sess_poll_ts = {}  # site → timestamp of last /api/hub/dab/status poll
_state_lock      = threading.Lock()
_client_sess     = {}   # {stop, thread, proc_welle, proc_ffmpeg, slot_id}
_scan_proc       = None  # current welle-cli scan subprocess (client side)
_services_file   = None  # pathlib.Path to dab_services.json, set in register()


# ── HMAC helpers ───────────────────────────────────────────────────────────────

def _sign_chunk(secret: str, data: bytes, ts: float) -> str:
    """HMAC-SHA256 matching hub_sign_payload() in signalscope.py."""
    key = hashlib.sha256(f"{secret}:signing".encode()).digest()
    msg = f"{int(ts)}:".encode() + data
    return _hmac.new(key, msg, hashlib.sha256).hexdigest()


def _safe_name(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in "-_ ").strip() or "stream"


# ── Services persistence ────────────────────────────────────────────────────────

def _load_services() -> dict:
    if not _services_file or not _services_file.exists():
        return {}
    try:
        return _json.loads(_services_file.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_services(data: dict) -> None:
    if _services_file:
        try:
            _services_file.write_text(_json.dumps(data, indent=2, ensure_ascii=False),
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
.dls-rt-static{font-size:13px;color:var(--lcd);font-family:'Courier New',monospace}
.dls-rt-scroll{display:inline-flex;white-space:nowrap;animation:dls-marquee 18s linear infinite;font-size:13px;color:var(--lcd);font-family:'Courier New',monospace}
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
.region-tree{display:flex;flex-direction:column;gap:1px;margin-bottom:4px}
.region-children{margin-left:14px;border-left:1px solid #17345f;padding-left:6px;display:flex;flex-direction:column;gap:1px}
.region-row{display:flex;align-items:center;gap:6px;padding:4px 6px;border-radius:6px;transition:background .1s}
.region-row:hover{background:rgba(23,168,255,.05)}
.region-toggle{width:18px;height:18px;background:none;border:none;color:var(--mu);cursor:pointer;font-size:9px;padding:0;flex-shrink:0;text-align:center;font-family:inherit;transition:color .1s;line-height:18px}
.region-toggle:hover{color:var(--acc)}
.region-toggle.leaf{visibility:hidden}
.region-label{font-size:13px;color:var(--tx);flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.region-ch{font-size:11px;color:var(--mu);font-family:'Courier New',monospace;white-space:nowrap;margin-right:2px}
.region-scan{padding:3px 10px;font-size:11px;background:#0a1e3a;border:1px solid rgba(23,168,255,.35);color:var(--acc);border-radius:4px;cursor:pointer;font-family:inherit;white-space:nowrap;flex-shrink:0;transition:background .1s}
.region-scan:hover{background:#112a50}
.region-scan:disabled{opacity:.4;cursor:not-allowed}
.btn-scan-stop{padding:4px 12px;font-size:11px;background:#1a0808;border:1px solid rgba(239,68,68,.4);color:var(--al);border-radius:4px;cursor:pointer;transition:background .12s;font-family:inherit}
.btn-scan-stop:hover{background:#280a0a}
.scan-prog{margin-top:10px;padding:10px 12px;background:rgba(0,0,0,.25);border:1px solid var(--bor);border-radius:8px}
.scan-prog-hdr{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.scan-prog-title{font-size:12px;font-weight:600;color:var(--mu);min-width:130px}
.scan-prog-track{flex:1;height:4px;background:#0a1828;border-radius:2px;overflow:hidden;border:1px solid var(--bor)}
.scan-prog-fill{height:100%;background:var(--acc);border-radius:2px;transition:width .4s}
.scan-prog-pct{font-size:11px;color:var(--mu);white-space:nowrap;min-width:42px;text-align:right}
.scan-found{font-size:12px;margin-bottom:6px}
.mux-chips{display:flex;flex-wrap:wrap;gap:6px}
.mux-chip{font-size:11px;padding:3px 10px;border-radius:12px;background:#0d2040;border:1px solid var(--bor);color:var(--tx);white-space:nowrap}
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
    <div class="panel-hdr">
      <span>Band Scan</span>
      <button class="btn-scan-stop" id="scan-stop-btn" style="display:none">&#9632; Stop</button>
    </div>
    <div class="region-tree" id="region-tree"></div>
    <!-- Progress panel (shown during and after scan) -->
    <div class="scan-prog" id="scan-prog" style="display:none">
      <div class="scan-prog-hdr">
        <span class="scan-prog-title" id="scan-prog-title">Scanning&hellip;</span>
        <div class="scan-prog-track"><div class="scan-prog-fill" id="scan-prog-fill" style="width:0%"></div></div>
        <span class="scan-prog-pct" id="scan-prog-pct">0 / ?</span>
      </div>
      <div class="scan-found" id="scan-found" style="color:var(--mu)">No muxes found yet&hellip;</div>
      <div class="mux-chips" id="mux-chips"></div>
    </div>
  </div>

</div>
</main>

<!-- Hidden audio element for MP3 streaming -->
<audio id="dab-audio" preload="none" type="audio/mpeg"></audio>

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
  _f('/api/hub/dab/devices/' + encodeURIComponent(site))
    .then(function(r){ return r.json(); })
    .then(function(d){
      var serials = d.serials || [];
      sdrSel.innerHTML = '<option value="">Auto</option>';
      serials.forEach(function(s){
        var o = document.createElement('option');
        o.value = s; o.textContent = s;
        sdrSel.appendChild(o);
      });
      // Auto-select the only dongle — no reason to stay on Auto
      if(serials.length === 1){ sdrSel.value = serials[0]; }
    }).catch(function(){});
  loadServices(site);
}
siteSel.addEventListener('change', loadDevices);
loadDevices();

// ── MSE audio streaming (works in Chrome + Safari) ────────────
// Safari's <audio src="..."> doesn't handle infinite chunked streams reliably.
// MSE (MediaSource Extensions) feeds chunks directly — supported in all
// modern browsers including Safari 13+.
var _mse = null, _sb = null, _mseQ = [], _mseReader = null;

function _mseAppend(){
  if(!_sb || _sb.updating || !_mseQ.length) return;
  try{ _sb.appendBuffer(_mseQ.shift()); } catch(e){}
}

function _teardownAudio(){
  if(_mseReader){ try{_mseReader.cancel();}catch(e){} _mseReader=null; }
  audioElem.pause();
  if(_mse){
    var oldSrc = audioElem.src;
    audioElem.removeAttribute('src');
    audioElem.load();
    try{ URL.revokeObjectURL(oldSrc); }catch(e){}
    _mse = null; _sb = null;
  } else {
    audioElem.src = '';
  }
  _mseQ = [];
}

function connectAudioStream(url){
  _teardownAudio();
  if(window.MediaSource && MediaSource.isTypeSupported('audio/mpeg')){
    _mse = new MediaSource();
    audioElem.src = URL.createObjectURL(_mse);
    _mse.addEventListener('sourceopen', function(){
      try{
        _sb = _mse.addSourceBuffer('audio/mpeg');
        _sb.mode = 'sequence';
        _sb.addEventListener('updateend', function(){
          // Trim old buffered data to avoid unbounded memory growth
          if(_sb && !_sb.updating && _sb.buffered.length){
            var end = _sb.buffered.end(_sb.buffered.length-1);
            var start = _sb.buffered.start(0);
            if(end - start > 30){
              try{ _sb.remove(start, end - 20); }catch(e){}
              return;
            }
          }
          _mseAppend();
        });
      } catch(e){
        // MSE setup failed — fall back to plain src
        _mse = null; _sb = null;
        audioElem.src = url; audioElem.load();
        audioElem.play().catch(function(){});
        return;
      }
      fetch(url, {credentials:'same-origin'}).then(function(r){
        _mseReader = r.body.getReader();
        (function pump(){
          _mseReader.read().then(function(d){
            if(d.done || !_mseReader) return;
            _mseQ.push(d.value);
            _mseAppend();
            pump();
          }).catch(function(){});
        })();
      }).catch(function(){});
    });
    audioElem.play().catch(function(){});
  } else {
    // Fallback for browsers without MSE
    audioElem.src = url; audioElem.load();
    audioElem.play().catch(function(){});
  }
}

// ── Connect / Disconnect ──────────────────────────────────────
connBtn.addEventListener('click', function(){
  if(_state === 'streaming' || _state === 'connecting'){
    doStop();
  } else {
    if(!_service || !_channel){
      _ssToast('Select a service from the list first.','warn');
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
      connectAudioStream(d.stream_url);
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
  _teardownAudio();
  _updateDls('');
  _slotId = '';
  setStatus('idle', 'Idle \u2014 select a service');
  _markActiveService('', '');
}

// Stop stream when page is closed/navigated away — belt-and-suspenders
// alongside the server-side session watchdog (30 s idle timeout).
window.addEventListener('beforeunload', function(){
  var site = siteSel.value;
  if(site && (_state === 'streaming' || _state === 'connecting')){
    // sendBeacon is fire-and-forget and survives page unload; fetch does not.
    // CSRF is not required for stop because it carries no side effects beyond
    // stopping a stream the user is closing anyway, and the slot_id in the
    // session is the authority (hub validates site ownership via _hub_sessions).
    var data = JSON.stringify({site: site, _beacon: true});
    navigator.sendBeacon('/api/hub/dab/stop_beacon', data);
  }
});

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
        connectAudioStream(d.stream_url);
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

// ── Band scan — region tree ────────────────────────────────────
var scanStopBtn  = document.getElementById('scan-stop-btn');
var scanProg     = document.getElementById('scan-prog');
var scanProgFill = document.getElementById('scan-prog-fill');
var scanProgPct  = document.getElementById('scan-prog-pct');
var scanProgTitle= document.getElementById('scan-prog-title');
var scanFound    = document.getElementById('scan-found');
var muxChips     = document.getElementById('mux-chips');
var regionTreeEl = document.getElementById('region-tree');

// Region data injected from server
var _REGIONS = {{regions | tojson}};

// Render the region hierarchy as a collapsible tree
function _buildRegionTree(node, container, depth, autoExpand){
  var hasChildren = node.children && node.children.length > 0;

  var row = document.createElement('div');
  row.className = 'region-row';

  // Toggle button
  var tog = document.createElement('button');
  tog.className = 'region-toggle' + (hasChildren ? '' : ' leaf');
  tog.textContent = autoExpand ? '\u25bc' : (hasChildren ? '\u25b6' : '');

  // Label
  var lbl = document.createElement('span');
  lbl.className = 'region-label';
  lbl.textContent = (node.icon ? node.icon + '\u00a0' : '') + node.label;

  // Channel count
  var cnt = document.createElement('span');
  cnt.className = 'region-ch';
  cnt.textContent = node.channels.length + ' ch';

  // Scan button
  var sb = document.createElement('button');
  sb.className = 'region-scan';
  sb.textContent = 'Scan';
  sb.addEventListener('click', function(e){
    e.stopPropagation();
    if(_scanPending) return;
    var site = siteSel.value; if(!site) return;
    _startScan(site, node.channels, (node.icon ? node.icon+'\u00a0' : '') + node.label);
  });

  row.appendChild(tog);
  row.appendChild(lbl);
  row.appendChild(cnt);
  row.appendChild(sb);
  container.appendChild(row);

  if(hasChildren){
    var childDiv = document.createElement('div');
    childDiv.className = 'region-children';
    childDiv.style.display = autoExpand ? '' : 'none';
    container.appendChild(childDiv);

    // Toggle expand/collapse
    tog.addEventListener('click', function(e){
      e.stopPropagation();
      var open = childDiv.style.display !== 'none';
      childDiv.style.display = open ? 'none' : '';
      tog.textContent = open ? '\u25b6' : '\u25bc';
    });

    node.children.forEach(function(child){
      // Auto-expand Europe root and UK by default
      var expand = (child.id === 'uk');
      _buildRegionTree(child, childDiv, depth + 1, expand);
    });
  }
}

_buildRegionTree(_REGIONS, regionTreeEl, 0, true);

// Disable/enable all scan buttons during scanning
function _setScanBtns(disabled){
  regionTreeEl.querySelectorAll('.region-scan').forEach(function(b){ b.disabled = disabled; });
}

function stopScanPoll(){
  if(_scanPoll){ clearInterval(_scanPoll); _scanPoll = null; }
  _scanPending = false;
}

function _startScan(site, channels, regionLabel){
  if(_state === 'streaming' || _state === 'connecting') doStop();
  _scanPending = true;
  _setScanBtns(true);
  scanStopBtn.style.display = '';
  // Reset + show progress panel
  scanProg.style.display   = '';
  scanProgFill.style.width = '0%';
  scanProgPct.textContent  = '0 / ' + channels.length;
  scanProgTitle.textContent = regionLabel + '\u2026';
  scanFound.textContent    = 'No muxes found yet\u2026';
  scanFound.style.color    = 'var(--mu)';
  muxChips.innerHTML       = '';

  var scanBody = {site: site, sdr_serial: sdrSel.value, channels: channels};
  console.log('[DAB] Starting scan', scanBody);
  _f('/api/hub/dab/scan', {
    method: 'POST',
    body: JSON.stringify(scanBody)
  })
  .then(function(r){
    if(!r.ok){ console.error('[DAB] /api/hub/dab/scan HTTP error', r.status); }
    return r.json();
  })
  .then(function(d){
    if(!d.ok){
      stopScanPoll();
      _setScanBtns(false);
      scanStopBtn.style.display = 'none';
      scanProg.style.display    = 'none';
      console.error('[DAB] Scan rejected by hub:', d.error || d);
      _ssToast('Scan failed: ' + (d.error || '?'),'err');
      return;
    }
    console.log('[DAB] Scan accepted by hub, polling for progress');
    _scanPoll = setInterval(function(){ _pollScan(site); }, 3000);
    setTimeout(function(){ _pollScan(site); }, 1500);
  })
  .catch(function(e){
    stopScanPoll();
    _setScanBtns(false);
    scanStopBtn.style.display = 'none';
    scanProg.style.display    = 'none';
    console.error('[DAB] Scan POST failed:', e);
  });
}

scanStopBtn.addEventListener('click', function(){
  var site = siteSel.value; if(!site) return;
  _f('/api/hub/dab/scan_stop', {
    method: 'POST',
    body: JSON.stringify({site: site})
  }).catch(function(){});
  stopScanPoll();
  _setScanBtns(false);
  scanStopBtn.style.display = 'none';
  scanProgTitle.textContent = 'Stopping\u2026';
});

function _pollScan(site){
  _f('/api/hub/dab/scan_status/' + encodeURIComponent(site))
    .then(function(r){ return r.json(); })
    .then(function(d){
      var total    = d.total    || 1;
      var progress = d.progress || 0;
      var muxes    = d.muxes    || [];

      scanProgFill.style.width = Math.round(progress / total * 100) + '%';
      scanProgPct.textContent  = progress + ' / ' + total;

      if(d.status === 'scanning' && d.channel)
        scanProgTitle.textContent = 'Scanning ' + d.channel + '\u2026';

      if(muxes.length){
        scanFound.textContent = muxes.length + ' mux' + (muxes.length !== 1 ? 'es' : '') + ' found:';
        scanFound.style.color = 'var(--ok)';
        muxChips.innerHTML = muxes.map(function(m){
          return '<span class="mux-chip">'
            + '<b>'+_esc(m.channel)+'</b>'
            + (m.ensemble && m.ensemble !== m.channel ? ' \u00b7 '+_esc(m.ensemble) : '')
            + (m.services ? ' <span style="color:var(--mu)">'+m.services+'</span>' : '')
            + '</span>';
        }).join('');
      }

      if(d.status === 'done' || d.status === 'idle'){
        stopScanPoll();
        _setScanBtns(false);
        scanStopBtn.style.display = 'none';
        scanProgFill.style.width  = '100%';
        scanProgPct.textContent   = total + ' / ' + total;
        if(d.status === 'done'){
          scanProgTitle.textContent = muxes.length
            ? 'Scan complete \u2014 ' + muxes.length + ' mux' + (muxes.length !== 1 ? 'es' : '') + ' found'
            : 'Scan complete \u2014 no muxes found';
          if(!muxes.length){
            scanFound.textContent = 'No DAB muxes found on the scanned channels.';
            scanFound.style.color = 'var(--wn)';
          }
        }
        loadServices(site);
      }
    }).catch(function(){});
}

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

    # ── Hub: session watchdog ──────────────────────────────────────────────────
    # If the browser closes/navigates away without hitting the Stop button,
    # the status poll stops but the client keeps welle-cli running forever.
    # The watchdog checks every 15 s and expires any session whose browser
    # has not polled /api/hub/dab/status in the last 30 s.
    _SESS_IDLE_TIMEOUT = 30  # seconds without a status poll → expire session

    def _session_watchdog():
        while True:
            time.sleep(15)
            if not hub_server:
                continue
            now = time.time()
            expired = []
            with _state_lock:
                for site, sess in list(_hub_sessions.items()):
                    last_poll = _hub_sess_poll_ts.get(site, sess.get("ts", now))
                    if now - last_poll > _SESS_IDLE_TIMEOUT:
                        expired.append((site, sess.get("slot_id", "")))
                        _hub_sessions.pop(site, None)
                        _hub_pending[site] = {"action": "stop"}
                        _hub_sess_poll_ts.pop(site, None)
            for site, slot_id in expired:
                slot = listen_registry.get(slot_id)
                if slot:
                    slot.closed = True
                monitor.log(f"[DAB] Session expired (browser inactive 30 s): site='{site}'")

    if hub_server:
        threading.Thread(target=_session_watchdog, daemon=True,
                         name="DABSessionWatchdog").start()

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
        return render_template_string(DAB_TPL, sites=sites, build=BUILD,
                                      regions=_SCAN_REGIONS)

    # ── Hub: DAB dongle list for a site ───────────────────────────────────────
    # Returns serials of scanner-role dongles only.  DAB-role dongles are used
    # for fixed background decoding and should not be grabbed by the DAB Scanner.

    @app.get("/api/hub/dab/devices/<path:site_name>")
    @login_required
    def dab_devices(site_name):
        serials = []
        if hub_server:
            sdata = hub_server._sites.get(site_name, {})
            seen  = set()
            for s in sdata.get("scanner_serials", []):
                if s not in seen:
                    seen.add(s)
                    serials.append(s)
        return jsonify({"serials": serials})

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

        # Use kind="dab" — NOT "scanner". The main relay handler
        # (_push_audio_request in signalscope.py) intercepts kind="scanner"
        # and launches an FM stream via rtl_fm, stealing the RTL-SDR dongle.
        # kind="dab" is plugin-managed: the DAB client's _stream_worker posts
        # audio chunks directly to /api/v1/audio_chunk/<slot_id>.
        slot = listen_registry.create(
            site, 0, kind="dab", mimetype="audio/mpeg",
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

    def _do_stop(site):
        """Shared stop logic — called by both the CSRF-protected stop and the beacon."""
        with _state_lock:
            sess = _hub_sessions.pop(site, None)
            _hub_pending[site] = {"action": "stop"}
        if sess:
            slot = listen_registry.get(sess.get("slot_id", ""))
            if slot:
                slot.closed = True
        _hub_sess_poll_ts.pop(site, None)
        monitor.log(f"[DAB] Session stopped: site='{site}'")

    @app.post("/api/hub/dab/stop")
    @login_required
    @csrf_protect
    def dab_stop():
        if not hub_server:
            return jsonify({"ok": False, "error": "no hub"}), 400
        data = request.get_json(silent=True) or {}
        site = str(data.get("site", "")).strip()
        _do_stop(site)
        return jsonify({"ok": True})

    @app.post("/api/hub/dab/stop_beacon")
    @login_required
    def dab_stop_beacon():
        """Called by navigator.sendBeacon on page unload — no CSRF token available."""
        if not hub_server:
            return ("", 204)
        try:
            raw = request.get_data(as_text=True) or "{}"
            data = _json.loads(raw)
        except Exception:
            data = {}
        site = str(data.get("site", "")).strip()
        if site:
            _do_stop(site)
        return ("", 204)

    # ── Hub: session status (browser polls every 2.5 s) ────────────────────────

    @app.get("/api/hub/dab/status/<path:site_name>")
    @login_required
    def dab_status(site_name):
        _hub_sess_poll_ts[site_name] = time.time()   # browser is still alive
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
        # Optional channel list from region selector; fall back to all channels
        req_channels = data.get("channels")
        if isinstance(req_channels, list):
            valid = set(_DAB_CHANNELS.keys())
            channels = [str(c).strip().upper() for c in req_channels
                        if str(c).strip().upper() in valid]
        else:
            channels = list(_DAB_CHANNELS.keys())
        if not channels:
            channels = list(_DAB_CHANNELS.keys())
        if not site:
            return jsonify({"ok": False, "error": "site required"}), 400
        with _state_lock:
            _hub_scan[site] = {
                "status":   "scanning",
                "channel":  "",
                "progress": 0,
                "total":    len(channels),
                "found":    0,
                "muxes":    [],
                "ts":       time.time(),
            }
            _hub_scan_stop.discard(site)
            _hub_pending[site] = {
                "action":     "scan",
                "sdr_serial": sdr_serial,
                "channels":   channels,
            }
        monitor.log(f"[DAB] Band scan triggered for site '{site}' ({len(channels)} channels)")
        return jsonify({"ok": True})

    # ── Hub: region presets (used by browser and mobile clients) ─────────────────

    @app.get("/api/hub/dab/regions")
    @login_required
    def dab_regions():
        """Return the scan region hierarchy (id, label, icon, channels, children)."""
        return jsonify({"ok": True, "regions": _SCAN_REGIONS})

    # ── Hub: scan status (browser polls during scan) ───────────────────────────

    @app.get("/api/hub/dab/scan_status/<path:site_name>")
    @login_required
    def dab_scan_status(site_name):
        entry = _hub_scan.get(site_name, {"status": "idle"})
        return jsonify({"ok": True, **entry})

    # ── Hub: stop an in-progress band scan ────────────────────────────────────

    @app.post("/api/hub/dab/scan_stop")
    @login_required
    @csrf_protect
    def dab_scan_stop():
        data = request.get_json(silent=True) or {}
        site = str(data.get("site", "")).strip()
        if site:
            with _state_lock:
                _hub_scan_stop.add(site)
                entry = _hub_scan.get(site)
                if entry:
                    entry["status"] = "idle"
                # Also queue an immediate scan_stop command so the client
                # SIGKILL's the current welle-cli probe without waiting for
                # the next channel's progress push (up to 12 s delay).
                _hub_pending[site] = {"action": "scan_stop"}
        return jsonify({"ok": True})

    # ── Hub: client polls for commands ─────────────────────────────────────────
    # POST (not GET) — consistent with every other client→hub call (heartbeat,
    # audio chunks, DLS push).  GET with custom headers can be silently blocked
    # by reverse-proxies and some middleware.  Site name comes from the JSON body
    # so it is not subject to header-stripping.  Also keep the old GET route for
    # backwards compat with older dab.py clients.

    _cmd_poll_seen = set()   # sites we've already logged a checkin for

    def _dab_cmd_poll_handler():
        if not hub_server:
            return jsonify({}), 200
        d    = request.get_json(silent=True) or {}
        site = str(d.get("site", "")
                   or request.headers.get("X-Dab-Site", "")).strip()
        if not site:
            return jsonify({}), 400
        sdata       = hub_server._sites.get(site, {})
        is_approved = sdata.get("_approved") or sdata.get("approved", True)
        if not is_approved or sdata.get("blocked"):
            monitor.log(f"[DAB] Cmd poll rejected for '{site}' "
                        f"(_approved={sdata.get('_approved')} "
                        f"approved={sdata.get('approved')} "
                        f"blocked={sdata.get('blocked')})")
            return jsonify({}), 403
        # Log first contact from each site so we know the client poller is live
        if site not in _cmd_poll_seen:
            _cmd_poll_seen.add(site)
            monitor.log(f"[DAB] Client '{site}' poller connected")
        with _state_lock:
            cmd = _hub_pending.pop(site, None)
        if cmd:
            monitor.log(f"[DAB] Cmd '{cmd.get('action')}' dispatched to '{site}'")
        return jsonify({"cmd": cmd} if cmd else {})

    @app.post("/api/hub/dab/cmd")
    def dab_cmd_poll_post():
        return _dab_cmd_poll_handler()

    @app.get("/api/hub/dab/cmd")
    def dab_cmd_poll_get():
        return _dab_cmd_poll_handler()

    # ── Client → Hub: push DLS update ─────────────────────────────────────────

    @app.post("/api/hub/dab/dls/<path:site_name>")
    def dab_dls_push(site_name):
        if not hub_server:
            return "", 204
        sdata = hub_server._sites.get(site_name, {})
        if not (sdata.get("_approved") or sdata.get("approved", True)) or sdata.get("blocked"):
            return "", 403
        d = request.get_json(silent=True) or {}
        text = str(d.get("text", "")).strip()
        _hub_dls[site_name] = {"text": text, "ts": time.time()}
        return "", 204

    # ── Client → Hub: push scan progress ──────────────────────────────────────

    @app.post("/api/hub/dab/scan_progress/<path:site_name>")
    def dab_scan_progress_push(site_name):
        if not hub_server:
            return jsonify({"ok": True, "stop": False})
        sdata = hub_server._sites.get(site_name, {})
        if not (sdata.get("_approved") or sdata.get("approved", True)) or sdata.get("blocked"):
            return jsonify({"ok": False}), 403
        d = request.get_json(silent=True) or {}
        should_stop = False
        with _state_lock:
            should_stop = site_name in _hub_scan_stop
            if should_stop:
                _hub_scan_stop.discard(site_name)
            entry = _hub_scan.setdefault(site_name, {
                "status": "scanning", "channel": "", "progress": 0,
                "total": len(_DAB_CHANNELS), "found": 0, "muxes": [], "ts": time.time(),
            })
            entry["status"]   = "scanning"
            entry["channel"]  = str(d.get("channel", ""))
            entry["progress"] = int(d.get("progress", entry.get("progress", 0)))
            entry["total"]    = int(d.get("total",    entry.get("total",    len(_DAB_CHANNELS))))
            entry["found"]    = int(d.get("found",    entry.get("found",    0)))
            # Append new mux if the client found one on this channel
            mux = d.get("mux")
            if mux and isinstance(mux, dict):
                existing = [m["channel"] for m in entry.get("muxes", [])]
                if mux.get("channel") not in existing:
                    entry.setdefault("muxes", []).append(mux)
        return jsonify({"ok": True, "stop": should_stop})

    # ── Client → Hub: push final scan results ─────────────────────────────────

    @app.post("/api/hub/dab/scan_result/<path:site_name>")
    def dab_scan_result_push(site_name):
        if not hub_server:
            return "", 204
        sdata = hub_server._sites.get(site_name, {})
        if not (sdata.get("_approved") or sdata.get("approved", True)) or sdata.get("blocked"):
            return "", 403
        d = request.get_json(silent=True) or {}
        services   = d.get("services", [])
        scanned_at = d.get("scanned_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
        db = _load_services()
        db[site_name] = {"services": services, "scanned_at": scanned_at}
        _save_services(db)
        with _state_lock:
            existing = _hub_scan.get(site_name, {})
            _hub_scan[site_name] = {
                "status":   "done",
                "found":    len(services),
                "progress": existing.get("total", len(_DAB_CHANNELS)),
                "total":    existing.get("total", len(_DAB_CHANNELS)),
                "muxes":    existing.get("muxes", []),
                "channel":  "",
                "ts":       time.time(),
            }
        monitor.log(f"[DAB] Scan complete for site '{site_name}': {len(services)} services")
        return "", 204

    # ── Relay: MP3 stream to browser ───────────────────────────────────────────
    # Custom relay without PCM silence injection (MP3 framing is not PCM)

    @app.get("/hub/dab/stream/<slot_id>")
    @login_required
    def dab_stream(slot_id):
        slot = listen_registry.get(slot_id)
        if not slot or slot.kind != "dab":
            return "Stream not found or expired", 404

        def generate():
            # 35s — welle-cli can take 15s+ to acquire DAB sync on marginal
            # signals before outputting any PCM for ffmpeg to encode.
            deadline = time.time() + 35.0
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

    # ── Start client command poller ────────────────────────────────────────────
    # Start unconditionally — the poller checks mode/hub_url on every iteration
    # so a misconfigured startup state can't permanently prevent it from running.
    # hub_only machines (pure hubs with no RTL-SDR) have hub_server set and
    # hub_url empty, so the poller will simply idle without doing anything.
    t = threading.Thread(target=_client_poller, args=(monitor,),
                          daemon=True, name="DABCmdPoller")
    t.start()
    monitor.log("[DAB] Client command poller started")


# ──────────────────────────────────────────────────────────────────────────────
# Client-side: command polling + dispatch
# ──────────────────────────────────────────────────────────────────────────────

def _client_poller(monitor):
    """Poll hub every 3 s for pending DAB commands."""
    import json as _json
    _consecutive_errors = 0
    _idles = 0
    while True:
        try:
            cfg     = monitor.app_cfg
            mode    = cfg.hub.mode or ""
            hub_url = (cfg.hub.hub_url or "").rstrip("/")
            site    = (cfg.hub.site_name or "").strip()
            if mode not in ("client", "both"):
                _idles += 1
                if _idles == 1:
                    monitor.log(f"[DAB] Poller idle: mode='{mode}' — hub-only machine, no scan")
                time.sleep(3)
                continue
            if not hub_url or not site:
                _idles += 1
                if _idles % 20 == 0:
                    monitor.log(f"[DAB] Poller waiting for config: hub_url={hub_url!r} site={site!r}")
                time.sleep(3)
                continue
            _idles = 0
            payload = _json.dumps({"site": site}).encode()
            req = urllib.request.Request(
                f"{hub_url}/api/hub/dab/cmd",
                data=payload,
                method="POST",
                headers={"Content-Type": "application/json",
                         "X-Dab-Site": site},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
                body   = resp.read()
            if status == 403:
                if _consecutive_errors % 20 == 0:
                    monitor.log(f"[DAB] Command poll 403: site '{site}' not approved on hub")
                _consecutive_errors += 1
            else:
                _consecutive_errors = 0
                d   = _json.loads(body)
                cmd = d.get("cmd")
                if cmd:
                    monitor.log(f"[DAB] Received command: {cmd.get('action')}")
                    _dispatch_client_cmd(cmd, hub_url, site, cfg, monitor)
        except urllib.error.HTTPError as e:
            if _consecutive_errors % 20 == 0:
                monitor.log(f"[DAB] Command poll HTTP error: {e.code} {e.reason}")
            _consecutive_errors += 1
        except Exception as e:
            if _consecutive_errors % 20 == 0:
                monitor.log(f"[DAB] Command poll error: {e}")
            _consecutive_errors += 1
        time.sleep(3)


def _lookup_device(cfg, sdr_serial):
    """Return (gain, ppm) for the given serial from cfg.sdr_devices."""
    for d in (cfg.sdr_devices or []):
        if d.serial == sdr_serial:
            return d.gain, d.ppm
    # Fallback: first device in the list
    if cfg.sdr_devices:
        d = cfg.sdr_devices[0]
        return d.gain, d.ppm
    return -1, 0


def _dispatch_client_cmd(cmd, hub_url, site, cfg, monitor):
    action     = cmd.get("action", "")
    sdr_serial = cmd.get("sdr_serial", "")
    gain, ppm  = _lookup_device(cfg, sdr_serial)

    if action == "start":
        _stop_stream()
        _start_stream(
            slot_id    = cmd["slot_id"],
            channel    = cmd.get("channel", ""),
            service    = cmd.get("service", ""),
            bitrate    = int(cmd.get("bitrate", 128) or 128),
            sdr_serial = sdr_serial,
            gain       = gain,
            ppm        = ppm,
            hub_url    = hub_url,
            site       = site,
            secret     = cfg.hub.secret_key or "",
            monitor    = monitor,
        )
    elif action == "stop":
        _stop_stream()
    elif action == "scan_stop":
        global _scan_proc
        p = _scan_proc
        if p:
            try:
                p.kill()
            except Exception:
                pass
        monitor.log("[DAB] Scan stopped by hub command")
    elif action == "scan":
        # Stop any running stream first — welle-cli must release the dongle
        # before the scan can open it.  Without this, the scan gets 0 services
        # because the device is still held by the stream worker.
        _stop_stream()
        channels = cmd.get("channels") or list(_DAB_CHANNELS.keys())
        t = threading.Thread(
            target=_do_scan,
            args=(site, sdr_serial, hub_url, channels, monitor),
            kwargs={"gain": gain, "ppm": ppm},
            daemon=True,
            name="DABScanner",
        )
        t.start()


def _start_stream(slot_id, channel, service, bitrate, sdr_serial, gain, ppm,
                  hub_url, site, secret, monitor=None):
    _stop_stream()
    stop = threading.Event()
    t = threading.Thread(
        target=_stream_worker,
        args=(slot_id, channel, service, bitrate, sdr_serial, gain, ppm,
              hub_url, site, secret, stop, monitor),
        daemon=True,
        name="DABWorker",
    )
    t.start()
    with _state_lock:
        _client_sess.clear()
        _client_sess.update({"stop": stop, "thread": t, "slot_id": slot_id,
                             "sdr_serial": sdr_serial})


def _stop_stream():
    with _state_lock:
        old = dict(_client_sess)
        _client_sess.clear()
    if old.get("stop"):
        old["stop"].set()
    killed_rtlsdr = False
    for key in ("proc_welle",):
        proc = old.get(key)
        if proc:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except Exception:
                    pass
                # welle-cli hard-killed — RTL2832 firmware may be stuck
                if key == "proc_welle":
                    killed_rtlsdr = True
            except Exception:
                pass
    if killed_rtlsdr:
        _usb_reset_rtlsdr(old.get("sdr_serial") or None)
    if old.get("thread"):
        old["thread"].join(timeout=3.0)


# ──────────────────────────────────────────────────────────────────────────────
# Hardware detection helpers
# ──────────────────────────────────────────────────────────────────────────────

def _is_raspberry_pi() -> bool:
    """Return True if this machine is a Raspberry Pi."""
    for path in ("/proc/device-tree/model", "/sys/firmware/devicetree/base/model"):
        try:
            with open(path, "rb") as fh:
                return b"raspberry pi" in fh.read().lower()
        except Exception:
            pass
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Client-side: DAB audio streaming worker
# ──────────────────────────────────────────────────────────────────────────────

def _stream_worker(slot_id, channel, service, bitrate, sdr_serial, gain, ppm,
                   hub_url, site, secret, stop, monitor=None):
    """
    Run welle-cli in HTTP server mode (-w PORT), poll mux.json to find the
    service SID, then stream /mp3/{SID} directly to the hub relay.

    welle-cli already serves MP3 from /mp3/{SID} — no ffmpeg transcoding needed.
    """
    def _log(msg):
        if monitor:
            monitor.log(msg)
        print(msg, flush=True)

    welle_bin = shutil.which("welle-cli")
    if not welle_bin:
        _log("[DAB] ERROR: welle-cli not found in PATH")
        return

    # Dedicated port for stream worker (separate from scan's 7979)
    _WELLE_STREAM_PORT = 7980
    _CHUNK = 8192   # bytes per read/POST (~0.5 s at 128 kbps)

    chunk_url = f"{hub_url}/api/v1/audio_chunk/{slot_id}"

    # welle-cli HTTP server mode.
    # -T  disables TII (Transmitter Identification Information) decoding —
    #     saves meaningful CPU on Raspberry Pi, not needed for audio playback.
    #     On a Pi decoding all services on a large mux (e.g. BBC National, 12+
    #     services) this is the single biggest CPU reduction available.
    # -C 1 (Pi only, no serial) limits simultaneous service decoding to 1 at a
    #     time. welle-cli stays locked on whichever service has an active HTTP
    #     consumer, so this does not interrupt playback.
    #     IMPORTANT: welle-cli rejects -C and -D together ("Cannot select both
    #     -C and -D"). -D is required when an SDR serial is specified, so -C 1
    #     is only added when no serial is set (single-dongle Pi setups).
    _on_pi = _is_raspberry_pi()
    welle_cmd = [welle_bin, "-w", str(_WELLE_STREAM_PORT), "-c", channel,
                 "-g", str(gain if gain is not None else -1)]
    if ppm:
        welle_cmd += ["-p", str(ppm)]
    if sdr_serial:
        welle_cmd += ["-D", f"driver=rtlsdr,serial={sdr_serial}"]
    if _on_pi:
        welle_cmd += ["-T"]
        if not sdr_serial:
            welle_cmd += ["-C", "1"]
            _log("[DAB] Raspberry Pi detected — using -T -C 1 to limit CPU")
        else:
            _log("[DAB] Raspberry Pi detected — using -T to limit CPU (-C skipped, incompatible with -D)")

    _log(f"[DAB] Starting stream: ch={channel} service='{service}' "
         f"serial={sdr_serial or 'auto'} gain={gain} ppm={ppm}")
    _log(f"[DAB] welle-cli cmd: {' '.join(welle_cmd)}")

    proc_welle = None

    try:
        proc_welle = subprocess.Popen(
            welle_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )

        # Register welle process so _stop_stream() can terminate it
        with _state_lock:
            if _client_sess.get("slot_id") == slot_id:
                _client_sess["proc_welle"] = proc_welle

        # Drain welle stdout — HTTP server status output, not needed
        def _drain_stdout():
            try:
                for _ in proc_welle.stdout:
                    pass
            except Exception:
                pass
        threading.Thread(target=_drain_stdout, daemon=True, name="DABWelleStdout").start()

        # Stderr log reader — drains welle-cli stderr for log visibility
        threading.Thread(
            target=_dls_reader,
            args=(proc_welle.stderr, stop, monitor),
            daemon=True, name="DABWelleLog",
        ).start()

        # ── Phase 1: poll mux.json to find the SID for the requested service ──
        sid      = None
        deadline = time.time() + 30.0
        _log(f"[DAB] Waiting for SID (service='{service}')...")

        while not stop.is_set() and time.time() < deadline:
            if proc_welle.poll() is not None:
                _log(f"[DAB] welle-cli exited early (rc={proc_welle.returncode})")
                return
            try:
                with urllib.request.urlopen(
                    f"http://localhost:{_WELLE_STREAM_PORT}/mux.json", timeout=2
                ) as r:
                    mux_data = _json.loads(r.read())
                for svc in mux_data.get("services", []):
                    lbl  = svc.get("label", {})
                    name = (lbl.get("label", "") or lbl.get("shortlabel", "")).strip()
                    if name.lower() == service.lower() or service.lower() in name.lower():
                        sid = str(svc.get("sid", ""))
                        break
                if sid:
                    _log(f"[DAB] Found SID={sid} for '{service}'")
                    # Start DLS poller now that SID is known
                    threading.Thread(
                        target=_dls_poller,
                        args=(_WELLE_STREAM_PORT, sid, service, site,
                              hub_url, stop, monitor),
                        daemon=True, name="DABDlsPoller",
                    ).start()
                    break
                else:
                    # mux.json replied but service not listed yet — log once
                    svcs = [((s.get("label",{}).get("label","") or s.get("label",{}).get("shortlabel","")).strip())
                            for s in mux_data.get("services", [])]
                    _log(f"[DAB] mux.json has {len(svcs)} services: {svcs[:8]}")
            except Exception as e:
                _log(f"[DAB] mux.json poll: {e}")
            time.sleep(1.0)

        if stop.is_set():
            return
        if not sid:
            _log(f"[DAB] Service '{service}' not found in mux after 30 s")
            return

        # ── Phase 2 + 3: open /mp3/{SID} and stream directly to hub ───────────
        # welle-cli already serves MP3 — no ffmpeg transcoding needed.
        # We open the stream, wait up to 35s for the first bytes, then relay.
        audio_url = f"http://localhost:{_WELLE_STREAM_PORT}/mp3/{sid}"
        _log(f"[DAB] Opening audio stream: {audio_url}")

        chunks_sent    = 0
        post_err_count = 0
        stream_opened  = False
        open_deadline  = time.time() + 35.0
        # Chunks received before the stream is considered "stable".
        # During welle-cli startup it decodes all services simultaneously,
        # which can cause brief OFDM resync pauses that stall the HTTP read
        # (seen as "timed out" after only a few chunks).  We allow reconnects
        # until this threshold is passed to survive those startup stalls.
        _STABLE_CHUNKS = 20

        while not stop.is_set():
            if proc_welle.poll() is not None:
                _log(f"[DAB] welle-cli exited (rc={proc_welle.returncode}) after {chunks_sent} chunks")
                return
            try:
                # 45 s read timeout — covers welle-cli OFDM resync stalls
                # (~6–10 s) that occur when all services start simultaneously.
                with urllib.request.urlopen(audio_url, timeout=45) as audio_r:
                    _log(f"[DAB] Audio stream connected — relaying to hub")
                    stream_opened = True
                    while not stop.is_set():
                        if proc_welle.poll() is not None:
                            _log(f"[DAB] welle-cli exited during stream after {chunks_sent} chunks")
                            return
                        chunk = audio_r.read(_CHUNK)
                        if not chunk:
                            _log(f"[DAB] Audio stream ended after {chunks_sent} chunks")
                            return
                        ts  = time.time()
                        sig = _sign_chunk(secret, chunk, ts) if secret else ""
                        req = urllib.request.Request(
                            chunk_url, data=chunk, method="POST",
                            headers={
                                "Content-Type": "application/octet-stream",
                                "X-Hub-Sig":    sig,
                                "X-Hub-Ts":     str(int(ts)),
                                "X-Hub-Nonce":  hashlib.md5(os.urandom(8)).hexdigest()[:16],
                            },
                        )
                        try:
                            urllib.request.urlopen(req, timeout=5).close()
                            if chunks_sent == 0:
                                _log(f"[DAB] First MP3 chunk sent to hub")
                            chunks_sent   += 1
                            post_err_count = 0
                        except Exception as e:
                            post_err_count += 1
                            if post_err_count == 1 or post_err_count % 20 == 0:
                                _log(f"[DAB] Chunk POST failed ({post_err_count}x): {e}")
            except Exception as e:
                if stream_opened and chunks_sent >= _STABLE_CHUNKS:
                    # Stream was running stably — treat error as fatal
                    _log(f"[DAB] Audio stream error after {chunks_sent} chunks: {e}")
                    return
                if stream_opened:
                    # Stalled during startup turbulence — reconnect
                    _log(f"[DAB] Audio stream stall during startup ({chunks_sent} chunks): {e} — reconnecting")
                    stream_opened = False
                    time.sleep(1.0)
                    continue
                if time.time() > open_deadline:
                    _log(f"[DAB] Audio endpoint not ready after 35 s: {e}")
                    return
                _log(f"[DAB] Waiting for audio stream: {e}")
                time.sleep(0.5)

    except Exception as e:
        _log(f"[DAB] Stream worker error: {e}")
    finally:
        if proc_welle:
            try:
                proc_welle.terminate()
            except Exception:
                pass
        _log(f"[DAB] Stream worker exited: ch={channel} service='{service}'")


def _dls_reader(stderr_stream, stop, monitor=None):
    """
    Drain welle-cli stderr and log every line.
    DLS is extracted from mux.json by _dls_poller, not from stderr.
    """
    _SUPPRESS = (
        "note:",
        "trying to resync",
        "hit end of (available)",
        "skipped ",
        "synconphase failed",
        "synconendnull failed",
        "failed to send audio for",
        "removing mp3 sender",
        "layer1.c",
        "illegal bit allocation",
        "aborting layer i decoding",
        "int123_do_layer1",
        "frankenstein stream",
        "big change from first",
    )
    try:
        for raw_line in stderr_stream:
            if stop.is_set():
                break
            try:
                line = raw_line.decode("utf-8", errors="replace").strip()
            except Exception:
                continue
            if not line:
                continue
            lower = line.lower()
            if any(s in lower for s in _SUPPRESS):
                continue
            if monitor:
                monitor.log(f"[DAB welle] {line}")
    except Exception:
        pass


def _dls_poller(welle_port, sid, service, site, hub_url, stop, monitor=None):
    """
    Poll welle-cli mux.json every 5 s and push dynamicLabel to hub.
    In HTTP server mode, DLS is in mux.json svc['dynamicLabel'], not stderr.
    Mirrors signalscope.py line 5393: svc.get('dynamicLabel') or svc.get('dls').
    """
    dls_url  = f"{hub_url}/api/hub/dab/dls/{urllib.parse.quote(site, safe='')}"
    mux_url  = f"http://localhost:{welle_port}/mux.json"
    last_dls = ""
    service_l = service.strip().lower()

    def _log(msg):
        if monitor:
            monitor.log(msg)

    while not stop.is_set():
        try:
            with urllib.request.urlopen(mux_url, timeout=3) as r:
                mux_data = _json.loads(r.read())
            for svc in mux_data.get("services", []):
                svc_sid = str(svc.get("sid", ""))
                lbl     = svc.get("label", {})
                name    = (lbl.get("label", "") or lbl.get("shortlabel", "")).strip()
                if svc_sid != sid and name.lower() != service_l:
                    continue
                # DLS field — may be a plain string or a dict with 'label'/'text' key
                raw_dls = svc.get("dynamicLabel", "") or svc.get("dls", "")
                if isinstance(raw_dls, dict):
                    raw_dls = raw_dls.get("label", "") or raw_dls.get("text", "") or ""
                dls = str(raw_dls).strip()
                if dls and dls != last_dls:
                    last_dls = dls
                    _log(f"[DAB] DLS: {dls}")
                    try:
                        req = urllib.request.Request(
                            dls_url,
                            data=_json.dumps({"text": dls}).encode(),
                            method="POST",
                            headers={"Content-Type": "application/json",
                                     "X-Dab-Site": site},
                        )
                        urllib.request.urlopen(req, timeout=3).close()
                    except Exception as e:
                        _log(f"[DAB] DLS push failed: {e}")
                break
        except Exception:
            pass
        stop.wait(5.0)


# ──────────────────────────────────────────────────────────────────────────────
# USB reset helper (Linux only)
# ──────────────────────────────────────────────────────────────────────────────

def _usb_reset_rtlsdr(serial=None):
    """
    Issue USBDEVFS_RESET ioctl to the RTL-SDR dongle — equivalent to an
    unplug/replug in software.  Recovers the RTL2832 firmware from the stuck
    state that welle-cli leaves it in when hard-killed.

    Linux-only (needs /sys/bus/usb and /dev/bus/usb).  Silent no-op on
    macOS/Windows or if the device can't be found.

    If *serial* is provided only the matching dongle is reset; otherwise the
    first RTL2832U device found is reset.
    """
    try:
        import fcntl as _fcntl
    except ImportError:
        return  # not Linux

    import os as _os

    # RTL2832U uses Realtek vendor 0x0bda with various product IDs
    _RTL_VID  = "0bda"
    _RTL_PIDS = {"2832", "2838", "2831", "2837"}

    SYS_USB = "/sys/bus/usb/devices"
    if not _os.path.isdir(SYS_USB):
        return

    target = None
    for dev_name in sorted(_os.listdir(SYS_USB)):
        dp = _os.path.join(SYS_USB, dev_name)
        try:
            vid = open(_os.path.join(dp, "idVendor")).read().strip()
            pid = open(_os.path.join(dp, "idProduct")).read().strip()
        except Exception:
            continue
        if vid != _RTL_VID or pid not in _RTL_PIDS:
            continue
        # Serial check (optional)
        if serial:
            try:
                dev_serial = open(_os.path.join(dp, "serial")).read().strip()
                if dev_serial != serial:
                    continue
            except Exception:
                pass  # no serial attr — still try
        try:
            bus    = int(open(_os.path.join(dp, "busnum")).read().strip())
            devnum = int(open(_os.path.join(dp, "devnum")).read().strip())
            target = f"/dev/bus/usb/{bus:03d}/{devnum:03d}"
            break
        except Exception:
            continue

    if not target:
        print("[DAB] USB reset: RTL-SDR device not found in sysfs")
        return

    # USBDEVFS_RESET = _IO('U', 20) = 0x5514
    USBDEVFS_RESET = 0x5514
    try:
        with open(target, "wb") as fh:
            _fcntl.ioctl(fh, USBDEVFS_RESET, 0)
        print(f"[DAB] USB reset issued to {target} — waiting for dongle to reinitialise")
        time.sleep(1.5)
    except PermissionError:
        print(f"[DAB] USB reset: permission denied for {target} "
              "(run as root or add udev rule: SUBSYSTEM==\"usb\", "
              "ATTR{{idVendor}}==\"0bda\", MODE=\"0666\")")
    except Exception as e:
        print(f"[DAB] USB reset failed ({target}): {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Client-side: DAB band scan
# ──────────────────────────────────────────────────────────────────────────────

def _do_scan(site, sdr_serial, hub_url, channels_to_scan=None, monitor=None, gain=-1, ppm=0):
    """
    Scan all Band III DAB channels with welle-cli (HTTP API mode).

    For each channel:
      - Launches welle-cli -w PORT -c CH (web server mode)
      - Polls http://localhost:PORT/mux.json for clean JSON service data
        (same approach as signalscope.py _dab_scan_mux — no text parsing)
      - Reports progress to hub in real time
      - Checks hub response for a stop signal (user clicked Stop)

    Progress is visible in real time in the browser scan panel.
    Final service list is pushed to hub.
    """
    def _log(msg):
        if monitor:
            monitor.log(msg)
        print(msg, flush=True)

    welle_bin = shutil.which("welle-cli")
    if not welle_bin:
        _log("[DAB] Scan aborted: welle-cli not found in PATH")
        return

    _site_enc    = urllib.parse.quote(site, safe="")
    progress_url = f"{hub_url}/api/hub/dab/scan_progress/{_site_enc}"
    result_url   = f"{hub_url}/api/hub/dab/scan_result/{_site_enc}"

    all_services = []
    channels     = channels_to_scan if channels_to_scan else list(_DAB_CHANNELS.keys())
    total        = len(channels)

    _log(f"[DAB] Band scan started: site='{site}' channels={total} welle={welle_bin}")

    # Use welle-cli's built-in HTTP server — avoids all text-parsing fragility.
    # Timing mirrors signalscope.py _dab_quick_probe:
    #   - poll from second 1 (no fixed startup delay)
    #   - accept on first 2 consecutive identical service lists (no min wait)
    #   - 18s timeout — marginal signals take 12-15s to acquire DAB sync
    _WELLE_PORT  = 7979
    _MAX_WAIT    = 18.0  # max seconds per channel (marginal signals need ~15s)
    _STABLE_NEED = 2     # consecutive identical service counts before accepting

    def _drain(pipe):
        try:
            for _ in pipe:
                pass
        except Exception:
            pass

    for idx, ch in enumerate(channels):
        # ── Push progress (before scanning this channel) ──────────────────
        should_stop = False
        try:
            payload = _json.dumps({
                "channel":  ch,
                "progress": idx,
                "total":    total,
                "found":    len(all_services),
            }).encode()
            req = urllib.request.Request(
                progress_url, data=payload, method="POST",
                headers={"Content-Type": "application/json", "X-Dab-Site": site},
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                resp_data = _json.loads(resp.read())
            if resp_data.get("stop"):
                should_stop = True
        except Exception:
            pass

        if should_stop:
            _log(f"[DAB] Scan stopped by user at channel {ch} ({idx}/{total})")
            break

        # ── Build welle-cli web-server command ────────────────────────────
        cmd = [welle_bin, "-w", str(_WELLE_PORT), "-c", ch,
               "-g", str(gain if gain is not None else -1)]
        if ppm:
            cmd += ["-p", str(ppm)]
        if sdr_serial:
            cmd += ["-D", f"driver=rtlsdr,serial={sdr_serial}"]

        # ── Launch and poll HTTP API for service list ─────────────────────
        proc = None
        found_services = []
        ensemble_name  = ch

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            global _scan_proc
            _scan_proc = proc

            # Drain stdout/stderr so pipes never fill and block welle-cli
            threading.Thread(target=_drain, args=(proc.stdout,), daemon=True).start()
            threading.Thread(target=_drain, args=(proc.stderr,), daemon=True).start()

            if proc.poll() is not None:
                _log(f"[DAB] {ch}: welle-cli exited early (device busy or no signal)")
            else:
                stable_count = 0
                deadline     = time.time() + _MAX_WAIT

                while time.time() < deadline:
                    time.sleep(1.0)
                    if proc.poll() is not None:
                        break

                    mux_data = None
                    for url in [
                        f"http://localhost:{_WELLE_PORT}/mux.json",
                        f"http://localhost:{_WELLE_PORT}/api/mux.json",
                    ]:
                        try:
                            with urllib.request.urlopen(url, timeout=1) as r:
                                mux_data = _json.loads(r.read())
                                break
                        except Exception:
                            break

                    if not mux_data:
                        stable_count = 0
                        continue

                    # Extract ensemble label
                    ens_obj = mux_data.get("ensemble", {})
                    ens_lbl = ens_obj.get("label", {})
                    _ens    = (ens_lbl.get("label", "") or ens_lbl.get("shortlabel", "")).strip()
                    if _ens:
                        ensemble_name = _ens

                    # Extract audio service names from the structured JSON —
                    # no text parsing, no regex, no device-init noise to strip
                    candidate = []
                    for svc in mux_data.get("services", []):
                        lbl  = svc.get("label", {})
                        name = (lbl.get("label", "") or lbl.get("shortlabel", "")).strip()
                        if not name:
                            continue
                        # Skip data-only services
                        is_audio = False
                        for c in svc.get("components", []):
                            if c.get("transportmode") == "audio":
                                is_audio = True
                                break
                        if not is_audio and svc.get("components"):
                            continue
                        candidate.append({
                            "name":     name,
                            "channel":  ch,
                            "ensemble": _ens or ch,
                            "sid":      svc.get("sid", ""),
                        })

                    if len(candidate) == len(found_services) and candidate:
                        stable_count += 1
                    else:
                        stable_count   = 0
                        found_services = candidate

                    if candidate and stable_count >= _STABLE_NEED:
                        found_services = candidate
                        break

        except Exception as e:
            _log(f"[DAB] Scan error on {ch}: {e}")
        finally:
            if proc is not None:
                proc.terminate()
                try:
                    # 4s grace — gives welle-cli time to call rtlsdr_close()
                    # so the kernel releases the USB device cleanly for the
                    # next channel (same as signalscope.py _dab_quick_probe).
                    proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    _log(f"[DAB] welle-cli hung on {ch}, sending SIGKILL")
                    proc.kill()
                    try:
                        proc.wait(timeout=3)
                    except Exception:
                        pass
                    _usb_reset_rtlsdr(sdr_serial or None)
                except Exception:
                    pass
            _scan_proc = None

        # 0.8s USB settle — libusb holds a kernel reference until the fd is
        # closed, which happens asynchronously after process exit.
        time.sleep(0.8)

        if found_services:
            _log(f"[DAB] {ch}: {len(found_services)} services ({ensemble_name})")
            all_services.extend(found_services)
            try:
                mux_payload = _json.dumps({
                    "channel":  ch,
                    "progress": idx + 1,
                    "total":    total,
                    "found":    len(all_services),
                    "mux": {
                        "channel":  ch,
                        "ensemble": ensemble_name,
                        "services": len(found_services),
                    },
                }).encode()
                req = urllib.request.Request(
                    progress_url, data=mux_payload, method="POST",
                    headers={"Content-Type": "application/json", "X-Dab-Site": site},
                )
                with urllib.request.urlopen(req, timeout=3) as resp:
                    resp_data = _json.loads(resp.read())
                if resp_data.get("stop"):
                    _log(f"[DAB] Scan stopped by user after finding mux on {ch}")
                    break
            except Exception:
                pass

    # ── Push final results ─────────────────────────────────────────────────
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
        _log(f"[DAB] Scan complete: {len(all_services)} services across {total} channels")
    except Exception as e:
        _log(f"[DAB] Failed to push scan results: {e}")
