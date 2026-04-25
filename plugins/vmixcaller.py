#!/usr/bin/env python3
"""
vMix Caller Manager — SignalScope plugin v1.0.0

Control Zoom/Teams callers in vMix directly from the hub dashboard.

Features:
  • Join / leave Zoom meetings via vMix API
  • Mute self, stop camera, mute all guests
  • Participants list pulled from vMix XML feed (server-side proxy — no CORS)
  • One-click "Put On Air" (ZoomSelectParticipantByName)
  • Optional caller video preview via SRT→HLS bridge on Ubuntu server
  • Keyboard shortcuts: M = mute self, C = toggle camera
"""

SIGNALSCOPE_PLUGIN = {
    "id":      "vmixcaller",
    "label":   "vMix Caller",
    "url":     "/hub/vmixcaller",
    "icon":    "📹",
    "version": "1.0.0",
}

import os
import json
import threading
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET

from flask import request, jsonify, render_template_string

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_CFG_PATH = os.path.join(_BASE_DIR, "vmixcaller_config.json")
_cfg_lock = threading.Lock()

_DEFAULTS: dict = {
    "vmix_ip":    "",
    "vmix_port":  8088,
    "vmix_input": 1,
    "bridge_url": "",
}


# ── Config helpers ─────────────────────────────────────────────────────────────

def _load_cfg() -> dict:
    with _cfg_lock:
        try:
            with open(_CFG_PATH) as fh:
                saved = json.load(fh)
        except Exception:
            saved = {}
    out = dict(_DEFAULTS)
    for k in _DEFAULTS:
        if k in saved:
            out[k] = saved[k]
    return out


def _save_cfg(data: dict) -> dict:
    out = dict(_DEFAULTS)
    for k in _DEFAULTS:
        if k not in data:
            continue
        v = data[k]
        if k == "vmix_port":
            try:
                v = int(v)
            except (TypeError, ValueError):
                v = 8088
        elif k == "vmix_input":
            try:
                v = int(v)
            except (TypeError, ValueError):
                v = 1
        out[k] = v
    with _cfg_lock:
        with open(_CFG_PATH, "w") as fh:
            json.dump(out, fh, indent=2)
    return out


# ── vMix API helpers ───────────────────────────────────────────────────────────

def _vmix_base(cfg: dict) -> str:
    ip   = (cfg.get("vmix_ip") or "").strip().rstrip("/")
    port = int(cfg.get("vmix_port") or 8088)
    return f"http://{ip}:{port}/API"


def _vmix_fn(cfg: dict, fn: str, value: str = None, extra: dict = None):
    """Call a vMix API function. Returns (ok, response_text)."""
    params: dict = {"Function": fn}
    inp = cfg.get("vmix_input")
    if inp:
        params["Input"] = str(inp)
    if value is not None:
        params["Value"] = str(value)
    if extra:
        params.update(extra)
    url = _vmix_base(cfg) + "/?" + urllib.parse.urlencode(params)
    try:
        resp = urllib.request.urlopen(
            urllib.request.Request(url, method="GET"), timeout=6
        )
        return True, resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        return False, str(e.reason)
    except Exception as e:
        return False, str(e)


def _vmix_xml(cfg: dict):
    """Fetch vMix XML status. Returns (ok, xml_text)."""
    url = _vmix_base(cfg) + "/"
    try:
        resp = urllib.request.urlopen(
            urllib.request.Request(url, method="GET"), timeout=6
        )
        return True, resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        return False, str(e.reason)
    except Exception as e:
        return False, str(e)


def _parse_participants(xml_text: str, input_num) -> list:
    """
    Parse vMix XML and extract Zoom/Teams participants from the specified
    input number. vMix nests participant elements inside <input> blocks;
    different vMix versions use slightly different attribute names.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    target = str(input_num)
    seen: set = set()
    participants: list = []

    for inp_el in root.iter("input"):
        if str(inp_el.get("number", "")) != target:
            continue
        for p in inp_el.iter("participant"):
            name = (
                p.get("displayName")
                or p.get("name")
                or p.get("Name")
                or ""
            ).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            participants.append({
                "name":   name,
                "muted":  p.get("muted",  "false").lower() == "true",
                "active": p.get("active", "false").lower() == "true",
            })

    return participants


def _vmix_version(xml_text: str) -> str:
    """Extract vMix version from XML, e.g. '26.0.0.56'."""
    try:
        root = ET.fromstring(xml_text)
        return root.findtext("version") or root.get("version") or ""
    except Exception:
        return ""


# ── HTML Template ──────────────────────────────────────────────────────────────

_TPL = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<title>vMix Caller — SignalScope</title>
<style nonce="{{csp_nonce()}}">
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(circle at top,#12376f 0%,var(--bg) 38%,#05101f 100%);color:var(--tx);font-family:system-ui,sans-serif;font-size:13px;min-height:100vh}
header{background:linear-gradient(180deg,rgba(10,31,65,.96),rgba(9,24,48,.96));border-bottom:1px solid var(--bor);padding:12px 20px;display:flex;align-items:center;gap:12px}
header h1{font-size:15px;font-weight:700;flex:1}
.btn{border:none;border-radius:8px;padding:5px 12px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;display:inline-block;text-decoration:none}
.btn:hover{filter:brightness(1.15)}
.btn:disabled{opacity:.45;cursor:not-allowed;filter:none}
.bp{background:var(--acc);color:#fff}
.bd{background:var(--al);color:#fff}
.bg{background:#0d2346;color:var(--tx);border:1px solid var(--bor)}
.bw{background:#1a3a1a;color:var(--ok);border:1px solid #166534}
.bs{padding:3px 9px;font-size:11px}
.nav-active{background:var(--acc)!important;color:#fff!important}
main{max-width:1100px;margin:0 auto;padding:18px 16px}
/* grid */
.g2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:720px){.g2{grid-template-columns:1fr}}
/* cards */
.card{background:var(--sur);border:1px solid var(--bor);border-radius:12px;overflow:hidden;margin-bottom:14px}
.ch{padding:9px 14px;display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--bor);background:linear-gradient(180deg,#143766,#102b54);font-size:12px;font-weight:700;color:var(--acc);text-transform:uppercase;letter-spacing:.06em}
.ch-r{margin-left:auto;display:flex;gap:6px;align-items:center}
.cb{padding:14px}
/* form */
.field{display:flex;flex-direction:column;gap:4px;margin-bottom:10px}
.field:last-child{margin-bottom:0}
.fl{font-size:11px;color:var(--mu);font-weight:600;text-transform:uppercase;letter-spacing:.05em}
input[type=text],input[type=number],input[type=password]{background:#0d1e40;border:1px solid var(--bor);border-radius:6px;color:var(--tx);padding:6px 9px;font-size:13px;font-family:inherit;width:100%}
input:focus{outline:none;border-color:var(--acc)}
.r2{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.r3{display:grid;grid-template-columns:2fr 1fr 1fr;gap:8px}
.brow{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.full{width:100%;justify-content:center}
/* video */
#pvw{position:relative;width:100%;aspect-ratio:16/9;background:#000;border-radius:8px;overflow:hidden;display:flex;align-items:center;justify-content:center}
#pvw video{width:100%;height:100%;object-fit:cover;display:block}
#pvw-ov{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;color:var(--mu);font-size:12px;text-align:center;padding:20px;background:#000}
#pvw-ov.hidden{display:none}
.pvw-icon{font-size:36px;line-height:1}
/* status */
#sbar{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--mu);margin-bottom:14px;padding:8px 12px;background:var(--sur);border:1px solid var(--bor);border-radius:8px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--mu);display:inline-block;flex-shrink:0;transition:background .3s}
.dok{background:var(--ok)}.dal{background:var(--al)}.dwn{background:var(--wn)}
kbd{background:#0d1e40;border:1px solid var(--bor);border-radius:3px;padding:0 5px;font-size:11px;font-family:monospace}
/* participants */
.plist{display:flex;flex-direction:column;gap:6px}
.pi{display:flex;align-items:center;gap:8px;padding:7px 10px;background:#091e42;border:1px solid var(--bor);border-radius:8px}
.pn{flex:1;font-weight:600;font-size:13px}
.pbadge{font-size:10px;color:var(--mu);background:#0d2346;border:1px solid var(--bor);border-radius:4px;padding:1px 5px}
.pbadge.muted{color:var(--wn);border-color:var(--wn)}
.pbadge.air{color:var(--ok);border-color:var(--ok)}
.padd{display:flex;gap:6px;margin-top:10px}
.padd input{flex:1}
/* guide */
.guide{font-size:12px;color:var(--mu);line-height:1.65}
.guide code{background:#0d1e40;border:1px solid var(--bor);border-radius:4px;padding:2px 6px;font-size:11px;color:#7dd3fc;word-break:break-all;display:inline}
.guide .blk{display:block;margin:5px 0;padding:7px 10px}
.guide ol{padding-left:18px;margin:8px 0}
.guide li{margin-bottom:8px}
/* msg */
#msg{padding:9px 13px;border-radius:8px;margin-bottom:12px;display:none;font-size:12px}
.mok{background:#0f2318;color:var(--ok);border:1px solid #166534}
.mer{background:#2a0a0a;color:var(--al);border:1px solid #991b1b}
/* call state — hide join when in-meeting, show call controls */
.call-btns{display:none}
.in-meeting .join-btn{display:none}
.in-meeting .call-btns{display:flex}
.empty-note{color:var(--mu);font-size:12px;padding:4px 0}
</style>
</head>
<body>
{{topnav("vmixcaller")|safe}}
<main>

<div id="msg"></div>

<!-- Status bar -->
<div id="sbar">
  <span class="dot" id="vdot"></span>
  <span id="vstatus">vMix: enter IP and save to connect</span>
  <span style="margin-left:auto;display:flex;gap:10px">
    <span><kbd>M</kbd> mute self</span>
    <span><kbd>C</kbd> toggle camera</span>
  </span>
</div>

<div class="g2">

  <!-- ── LEFT: caller preview ──────────────────────────────────────────────── -->
  <div>
    <div class="card">
      <div class="ch">📹 Caller Preview</div>
      <div class="cb">
        <div id="pvw">
          <video id="pvid" autoplay muted playsinline></video>
          <div id="pvw-ov">
            <div class="pvw-icon">📷</div>
            <div id="pvmsg">Configure a bridge URL to enable preview</div>
          </div>
        </div>
      </div>
    </div>

    <!-- SRT bridge setup guide — hidden when bridge URL is configured -->
    <div class="card" id="guide-card">
      <div class="ch">⚙ SRT Bridge Setup (Ubuntu)</div>
      <div class="cb guide">
        <p>To show the caller here, run this once on your Ubuntu server:</p>
        <code class="blk">docker run -d --name srs-srt --restart unless-stopped \
  -p 10080:10080/udp -p 8080:8080 \
  ossrs/srs:5 ./objs/srs -c conf/srt.conf</code>
        <ol>
          <li>In vMix, open your Zoom input → <strong>Output</strong> → enable <strong>SRT Output</strong>.<br>
            Set the destination to:<br>
            <code>srt://&lt;ubuntu-ip&gt;:10080?streamid=#!::h=live/caller,m=publish</code>
          </li>
          <li>In the <strong>vMix Connection</strong> panel below, set<br>
            <strong>Preview URL</strong> to <code>http://&lt;ubuntu-ip&gt;:8080/live/caller.m3u8</code><br>
            and click Save.
          </li>
          <li>The preview will appear once a call is active and the SRT container is running.
            Works natively in Safari; Chrome/Firefox need the URL to be an HTTP stream
            (MJPEG, WebRTC, or plain video file).
          </li>
        </ol>
        <p style="margin-top:8px;color:#4b6080">The SRS container uses no CPU when idle and auto-restarts on reboot.</p>
      </div>
    </div>
  </div>

  <!-- ── RIGHT: controls ───────────────────────────────────────────────────── -->
  <div>

    <!-- vMix connection -->
    <div class="card">
      <div class="ch">🔌 vMix Connection</div>
      <div class="cb">
        <div class="r3">
          <div class="field">
            <label class="fl">vMix IP Address</label>
            <input type="text" id="vmix-ip" placeholder="192.168.1.100"
                   value="{{cfg.vmix_ip|e}}">
          </div>
          <div class="field">
            <label class="fl">Port</label>
            <input type="number" id="vmix-port" value="{{cfg.vmix_port}}"
                   min="1" max="65535">
          </div>
          <div class="field">
            <label class="fl">Input #</label>
            <input type="number" id="vmix-input" value="{{cfg.vmix_input}}"
                   min="1" max="200">
          </div>
        </div>
        <div class="field">
          <label class="fl">Preview URL (optional — HLS/MJPEG/WebRTC stream from bridge)</label>
          <input type="text" id="bridge-url"
                 placeholder="http://ubuntu-server:8080/live/caller.m3u8"
                 value="{{cfg.bridge_url|e}}">
        </div>
        <div class="brow">
          <button class="btn bp" onclick="saveConfig()">💾 Save</button>
          <button class="btn bg" onclick="testConn()">🔌 Test Connection</button>
        </div>
      </div>
    </div>

    <!-- Meeting controls -->
    <div class="card" id="meeting-card">
      <div class="ch">📞 Meeting Controls</div>
      <div class="cb">
        <div class="field">
          <label class="fl">Meeting ID</label>
          <input type="text" id="mtg-id" placeholder="123 456 7890">
        </div>
        <div class="r2">
          <div class="field">
            <label class="fl">Passcode</label>
            <input type="password" id="mtg-pass" placeholder="••••••">
          </div>
          <div class="field">
            <label class="fl">Display Name</label>
            <input type="text" id="mtg-name" placeholder="Guest Producer">
          </div>
        </div>

        <!-- Join button (visible when not in meeting) -->
        <div class="brow">
          <button class="btn bp full join-btn" onclick="joinMeeting()">📞 Join Meeting</button>
        </div>

        <!-- In-call controls (visible when in meeting) -->
        <div class="brow call-btns" style="flex-wrap:wrap">
          <button class="btn bg" id="mute-btn"  onclick="muteSelf()">🔇 Mute Self</button>
          <button class="btn bg" id="cam-btn"   onclick="stopCamera()">📷 Stop Camera</button>
          <button class="btn bg"                onclick="muteAll()">🔇 Mute All Guests</button>
          <button class="btn bd"                onclick="leaveMeeting()">📴 Leave</button>
        </div>
      </div>
    </div>

  </div><!-- /right col -->
</div><!-- /g2 -->

<!-- ── Participants ─────────────────────────────────────────────────────────── -->
<div class="card">
  <div class="ch">
    👥 Participants
    <div class="ch-r">
      <button class="btn bg bs" onclick="loadParticipants()">↻ Refresh</button>
    </div>
  </div>
  <div class="cb">
    <div id="plist" class="plist">
      <div class="empty-note">No participants — join a meeting or click Refresh</div>
    </div>
    <div class="padd">
      <input type="text" id="padd-in"
             placeholder="Manually add caller name…"
             onkeydown="if(event.key==='Enter')addManual()">
      <button class="btn bg bs" onclick="addManual()">+ Add</button>
    </div>
  </div>
</div>

</main>

<script nonce="{{csp_nonce()}}">
// ── CSRF ─────────────────────────────────────────────────────────────────────
function _csrf(){
  return (document.querySelector('meta[name="csrf-token"]')||{}).content
    || (document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/)||[])[1] || '';
}

// ── Helpers ──────────────────────────────────────────────────────────────────
var _msgT = null;
function showMsg(txt, ok){
  var el=document.getElementById('msg');
  el.textContent=txt;
  el.className=ok?'mok':'mer';
  el.style.display='block';
  clearTimeout(_msgT);
  _msgT=setTimeout(function(){el.style.display='none';},5000);
}

function _post(url, data){
  return fetch(url,{
    method:'POST',
    headers:{'Content-Type':'application/json','X-CSRFToken':_csrf()},
    credentials:'same-origin',
    body:JSON.stringify(data)
  }).then(function(r){return r.json();});
}

function _esc(s){
  return String(s).replace(/[&<>"']/g,function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
  });
}

// ── vMix status dot ──────────────────────────────────────────────────────────
function setVmixStatus(state, text){
  var d=document.getElementById('vdot');
  var s=document.getElementById('vstatus');
  d.className='dot '+(state==='ok'?'dok':state==='warn'?'dwn':'dal');
  s.textContent='vMix: '+text;
}

// ── Config ───────────────────────────────────────────────────────────────────
function saveConfig(){
  var cfg={
    vmix_ip:    document.getElementById('vmix-ip').value.trim(),
    vmix_port:  parseInt(document.getElementById('vmix-port').value)||8088,
    vmix_input: parseInt(document.getElementById('vmix-input').value)||1,
    bridge_url: document.getElementById('bridge-url').value.trim(),
  };
  _post('/api/vmixcaller/config', cfg).then(function(d){
    if(d.ok){
      showMsg('Settings saved', true);
      initPreview(cfg.bridge_url);
      if(cfg.vmix_ip) testConn();
    } else {
      showMsg(d.error||'Save failed', false);
    }
  }).catch(function(e){showMsg('Save error: '+e,false);});
}

// ── Connection test ──────────────────────────────────────────────────────────
function testConn(){
  setVmixStatus('warn','connecting…');
  fetch('/api/vmixcaller/status',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      if(d.ok){
        var ver=d.version ? ' v'+d.version : '';
        setVmixStatus('ok','connected'+ver);
        if(d.participants && d.participants.length>0){
          renderParticipants(d.participants);
        }
      } else {
        setVmixStatus('al','not reachable — '+(d.error||'check IP / port'));
      }
    })
    .catch(function(){setVmixStatus('al','request failed');});
}

// ── Meeting controls ─────────────────────────────────────────────────────────
var _inMeeting=false;
var _selfMuted=false;
var _camOff=false;

function setMeetingState(active){
  _inMeeting=active;
  document.getElementById('meeting-card').className=active?'card in-meeting':'card';
  if(!active){ _selfMuted=false; _camOff=false; }
}

function joinMeeting(){
  var mid =(document.getElementById('mtg-id').value||'').trim();
  var pass=(document.getElementById('mtg-pass').value||'').trim();
  var name=(document.getElementById('mtg-name').value||'Guest Producer').trim();
  if(!mid){showMsg('Enter a Meeting ID',false);return;}
  // vMix ZoomJoinMeeting: Value = MeetingID|Passcode|DisplayName
  _post('/api/vmixcaller/function',{fn:'ZoomJoinMeeting',value:mid+'|'+pass+'|'+name})
    .then(function(d){
      if(d.ok){showMsg('Join command sent to vMix',true);setMeetingState(true);}
      else showMsg(d.error||'vMix returned an error',false);
    }).catch(function(e){showMsg('Error: '+e,false);});
}

function leaveMeeting(){
  _post('/api/vmixcaller/function',{fn:'ZoomLeaveMeeting'})
    .then(function(d){
      if(d.ok){showMsg('Left meeting',true);setMeetingState(false);clearParticipants();}
      else showMsg(d.error||'vMix error',false);
    }).catch(function(e){showMsg('Error: '+e,false);});
}

function muteSelf(){
  _post('/api/vmixcaller/function',{fn:'ZoomMuteSelf'})
    .then(function(d){
      if(d.ok){
        _selfMuted=!_selfMuted;
        document.getElementById('mute-btn').textContent=_selfMuted?'🔊 Unmute Self':'🔇 Mute Self';
        showMsg(_selfMuted?'Microphone muted':'Microphone unmuted',true);
      } else showMsg(d.error||'vMix error',false);
    }).catch(function(e){showMsg('Error: '+e,false);});
}

function stopCamera(){
  _post('/api/vmixcaller/function',{fn:'ZoomStopCamera'})
    .then(function(d){
      if(d.ok){
        _camOff=!_camOff;
        document.getElementById('cam-btn').textContent=_camOff?'📷 Start Camera':'📷 Stop Camera';
        showMsg(_camOff?'Camera stopped':'Camera started',true);
      } else showMsg(d.error||'vMix error',false);
    }).catch(function(e){showMsg('Error: '+e,false);});
}

function muteAll(){
  _post('/api/vmixcaller/function',{fn:'ZoomMuteAll'})
    .then(function(d){
      if(d.ok) showMsg('All guests muted',true);
      else showMsg(d.error||'vMix error',false);
    }).catch(function(e){showMsg('Error: '+e,false);});
}

// ── Participants ──────────────────────────────────────────────────────────────
var _parts=[];

function loadParticipants(){
  fetch('/api/vmixcaller/status',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      if(d.ok){
        renderParticipants(d.participants||[]);
        if(!d.participants||!d.participants.length)
          showMsg('No participants found in vMix input',false);
      } else {
        showMsg(d.error||'Could not reach vMix',false);
      }
    }).catch(function(e){showMsg('Error: '+e,false);});
}

function clearParticipants(){
  _parts=[];
  renderParticipants([]);
}

function renderParticipants(list){
  _parts=list;
  var el=document.getElementById('plist');
  if(!list||!list.length){
    el.innerHTML='<div class="empty-note">No participants — join a meeting or click Refresh</div>';
    return;
  }
  el.innerHTML=list.map(function(p){
    var safe=_esc(p.name);
    var badges='';
    if(p.muted)  badges+='<span class="pbadge muted">MUTED</span>';
    if(p.active) badges+='<span class="pbadge air">ON AIR</span>';
    return '<div class="pi">'
      +'<span class="pn">'+safe+'</span>'
      +badges
      +'<button class="btn bw bs" data-name="'+safe+'" onclick="putOnAir(this)">📺 Put On Air</button>'
      +'</div>';
  }).join('');
}

function addManual(){
  var inp=document.getElementById('padd-in');
  var name=inp.value.trim();
  if(!name) return;
  if(_parts.some(function(p){return p.name===name;})) return;
  _parts.push({name:name,muted:false,active:false});
  renderParticipants(_parts);
  inp.value='';
}

function putOnAir(btn){
  var name=btn.dataset.name;
  _post('/api/vmixcaller/function',{fn:'ZoomSelectParticipantByName',value:name})
    .then(function(d){
      if(d.ok) showMsg('"'+name+'" sent to vMix output',true);
      else showMsg(d.error||'vMix error',false);
    }).catch(function(e){showMsg('Error: '+e,false);});
}

// ── Video preview ─────────────────────────────────────────────────────────────
function initPreview(url){
  var vid=document.getElementById('pvid');
  var ov=document.getElementById('pvw-ov');
  var msg=document.getElementById('pvmsg');
  var guide=document.getElementById('guide-card');

  if(!url){
    ov.classList.remove('hidden');
    msg.textContent='Configure a bridge URL in settings to enable preview';
    if(guide) guide.style.display='';
    return;
  }

  if(guide) guide.style.display='none';
  ov.classList.remove('hidden');
  msg.textContent='Connecting to stream…';

  vid.src=url;
  vid.load();
  vid.play().then(function(){
    ov.classList.add('hidden');
  }).catch(function(){
    // autoplay may fail silently — wait for canplay
  });

  vid.oncanplay=function(){ov.classList.add('hidden');};
  vid.onerror=function(){
    ov.classList.remove('hidden');
    msg.textContent='Stream unavailable — is the SRT bridge running?';
  };
}

// ── Keyboard shortcuts ────────────────────────────────────────────────────────
document.addEventListener('keydown',function(e){
  var tag=(document.activeElement||{}).tagName||'';
  if(tag==='INPUT'||tag==='TEXTAREA'||tag==='SELECT') return;
  if(e.key==='m'||e.key==='M'){e.preventDefault();muteSelf();}
  if(e.key==='c'||e.key==='C'){e.preventDefault();stopCamera();}
});

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded',function(){
  var bridgeUrl=document.getElementById('bridge-url').value.trim();
  initPreview(bridgeUrl);
  var vmixIp=document.getElementById('vmix-ip').value.trim();
  if(vmixIp) testConn();
});
</script>
</body>
</html>"""


# ── Plugin registration ────────────────────────────────────────────────────────

def register(app, ctx):
    login_required = ctx["login_required"]
    csrf_protect   = ctx["csrf_protect"]

    # ── Page ──────────────────────────────────────────────────────────────────
    @app.get("/hub/vmixcaller")
    @login_required
    def vmixcaller_page():
        cfg = _load_cfg()
        return render_template_string(_TPL, cfg=cfg)

    # ── Config API ────────────────────────────────────────────────────────────
    @app.get("/api/vmixcaller/config")
    @login_required
    def vmixcaller_get_config():
        return jsonify(_load_cfg())

    @app.post("/api/vmixcaller/config")
    @login_required
    @csrf_protect
    def vmixcaller_save_config():
        data = request.get_json(silent=True) or {}
        try:
            saved = _save_cfg(data)
            return jsonify({"ok": True, "config": saved})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    # ── vMix status proxy ─────────────────────────────────────────────────────
    @app.get("/api/vmixcaller/status")
    @login_required
    def vmixcaller_status():
        cfg = _load_cfg()
        if not cfg.get("vmix_ip"):
            return jsonify({"ok": False, "error": "No vMix IP configured"})

        ok, xml_text = _vmix_xml(cfg)
        if not ok:
            return jsonify({"ok": False, "error": xml_text})

        version      = _vmix_version(xml_text)
        participants = _parse_participants(xml_text, cfg.get("vmix_input", 1))
        return jsonify({
            "ok":           True,
            "version":      version,
            "participants": participants,
        })

    # ── vMix function proxy ───────────────────────────────────────────────────
    @app.post("/api/vmixcaller/function")
    @login_required
    @csrf_protect
    def vmixcaller_function():
        data  = request.get_json(silent=True) or {}
        fn    = str(data.get("fn",    "") or "").strip()
        value = data.get("value")

        if not fn:
            return jsonify({"ok": False, "error": "Missing function name"}), 400

        cfg = _load_cfg()
        if not cfg.get("vmix_ip"):
            return jsonify({"ok": False, "error": "No vMix IP configured"})

        ok, resp = _vmix_fn(cfg, fn, value=value if value is not None else None)
        if ok:
            return jsonify({"ok": True,  "response": resp[:200]})
        else:
            return jsonify({"ok": False, "error": resp}), 502
