#!/usr/bin/env python3
"""
vMix Caller Manager — SignalScope plugin v1.1.0

Hub / client architecture:
  • Hub     — control surface. Operator selects which site has vMix, queues
              commands, and sees participants reported back from that site.
  • Client  — the site node running alongside vMix. A background thread polls
              the hub for queued commands, executes them against the local vMix
              API, and periodically reports participants + connection status
              back to the hub so the operator can see them.
  • Standalone — direct connection (legacy / single-machine setups).

Because the hub cannot call into a client behind NAT, all communication uses
the standard SignalScope client-polls-hub pattern from hub ↔ client infra.
"""

SIGNALSCOPE_PLUGIN = {
    "id":      "vmixcaller",
    "label":   "vMix Caller",
    "url":     "/hub/vmixcaller",
    "icon":    "📹",
    "version": "1.1.0",
}

import os
import json
import time
import threading
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET

from flask import request, jsonify, render_template_string

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_CFG_PATH = os.path.join(_BASE_DIR, "vmixcaller_config.json")
_cfg_lock = threading.Lock()

# Defaults for every key used by either hub or client.
_DEFAULTS: dict = {
    # ── Hub fields ────────────────────────────────────────────────────────────
    "target_site": "",       # which site node runs vMix
    "bridge_url":  "",       # optional HLS/MJPEG preview URL for the hub page
    # ── Shared field ─────────────────────────────────────────────────────────
    "vmix_input":  1,        # which vMix input is the Zoom/Teams input
    # ── Client fields (set on the site node that has vMix) ───────────────────
    "vmix_ip":     "127.0.0.1",
    "vmix_port":   8088,
}

# ── Hub-side runtime state ─────────────────────────────────────────────────────
# These dicts live only on hub/both nodes.  On pure clients they're inert.
_pending_cmd: dict = {}   # site_name → {fn, value, input, seq}
_site_status: dict = {}   # site_name → {ok, version, participants, error, ts}
_state_lock          = threading.Lock()


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
        if k in ("vmix_port", "vmix_input"):
            try:
                v = int(v)
            except (TypeError, ValueError):
                v = _DEFAULTS[k]
        out[k] = v
    with _cfg_lock:
        with open(_CFG_PATH, "w") as fh:
            json.dump(out, fh, indent=2)
    return out


# ── vMix API helpers (run on the CLIENT side) ──────────────────────────────────

def _vmix_base(cfg: dict) -> str:
    ip   = (cfg.get("vmix_ip") or "127.0.0.1").strip().rstrip("/")
    port = int(cfg.get("vmix_port") or 8088)
    return f"http://{ip}:{port}/API"


def _vmix_fn(cfg: dict, fn: str, value: str = None, inp: int = None):
    """Call a vMix API function.  Returns (ok: bool, text: str)."""
    params: dict = {"Function": fn}
    input_num = inp or cfg.get("vmix_input")
    if input_num:
        params["Input"] = str(input_num)
    if value is not None:
        params["Value"] = str(value)
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
    """Fetch full vMix XML status.  Returns (ok: bool, text: str)."""
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
    """Extract Zoom/Teams participants from vMix XML for the given input."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    target = str(input_num)
    seen: set = set()
    out: list = []

    for inp_el in root.iter("input"):
        if str(inp_el.get("number", "")) != target:
            continue
        for p in inp_el.iter("participant"):
            name = (
                p.get("displayName") or p.get("name") or p.get("Name") or ""
            ).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            out.append({
                "name":   name,
                "muted":  p.get("muted",  "false").lower() == "true",
                "active": p.get("active", "false").lower() == "true",
            })
    return out


def _vmix_version(xml_text: str) -> str:
    try:
        return ET.fromstring(xml_text).findtext("version") or ""
    except Exception:
        return ""


# ── Client background thread ───────────────────────────────────────────────────

def _start_client_thread(monitor, hub_url: str):
    """
    Started on any client/both node that has a hub_url configured.
    Polls the hub every 3 s for pending vMix commands, executes them
    locally, then reports participants + connection status back.
    """
    import hashlib, hmac as _hmac

    def _sign(secret, data_bytes, ts):
        key = hashlib.sha256(f"{secret}:signing".encode()).digest()
        msg = f"{ts:.0f}:".encode() + data_bytes
        return _hmac.new(key, msg, hashlib.sha256).hexdigest()

    def _hub_get(url, site, secret):
        ts  = time.time()
        hdrs = {"X-Site": site}
        if secret:
            sig = _sign(secret, b"", ts)
            hdrs.update({"X-Hub-Sig": sig, "X-Hub-Ts": f"{ts:.0f}",
                          "X-Hub-Nonce": hashlib.md5(os.urandom(8)).hexdigest()[:16]})
        req = urllib.request.Request(url, headers=hdrs, method="GET")
        resp = urllib.request.urlopen(req, timeout=8)
        return json.loads(resp.read())

    def _hub_post(url, site, secret, payload_dict):
        payload = json.dumps(payload_dict, separators=(",", ":")).encode()
        ts  = time.time()
        hdrs = {"Content-Type": "application/json", "X-Site": site}
        if secret:
            sig = _sign(secret, payload, ts)
            hdrs.update({"X-Hub-Sig": sig, "X-Hub-Ts": f"{ts:.0f}",
                          "X-Hub-Nonce": hashlib.md5(os.urandom(8)).hexdigest()[:16]})
        req = urllib.request.Request(url, data=payload, headers=hdrs, method="POST")
        urllib.request.urlopen(req, timeout=8).close()

    last_full_report = 0.0
    last_cmd_seq     = None
    hub_url          = hub_url.rstrip("/")

    while True:
        try:
            cfg    = _load_cfg()
            app_cfg = monitor.app_cfg
            site   = (getattr(getattr(app_cfg, "hub", None), "site_name", "") or "").strip()
            secret = (getattr(getattr(app_cfg, "hub", None), "secret_key",  "") or "").strip()

            if not site:
                time.sleep(5)
                continue

            # ── Poll hub for a pending command ────────────────────────────────
            try:
                d = _hub_get(f"{hub_url}/api/vmixcaller/cmd", site, secret)
            except Exception:
                time.sleep(5)
                continue

            cmd = d.get("cmd")
            if cmd and cmd.get("seq") != last_cmd_seq:
                last_cmd_seq = cmd["seq"]
                ok, resp = _vmix_fn(cfg, cmd["fn"],
                                    value=cmd.get("value"),
                                    inp=cmd.get("input"))
                # Send result immediately after command
                try:
                    _hub_post(f"{hub_url}/api/vmixcaller/report", site, secret, {
                        "cmd_result": {"seq": cmd["seq"], "ok": ok, "resp": resp[:120]},
                    })
                except Exception:
                    pass

            # ── Periodic full status report (participants + connection) ────────
            now = time.time()
            if now - last_full_report >= 12.0:
                last_full_report = now
                ok_xml, xml_text = _vmix_xml(cfg)
                report: dict = {"ts": now, "ok": ok_xml}
                if ok_xml:
                    report["version"]      = _vmix_version(xml_text)
                    report["participants"] = _parse_participants(
                        xml_text, cfg.get("vmix_input", 1)
                    )
                else:
                    report["error"]        = xml_text[:120]
                    report["participants"] = []
                try:
                    _hub_post(f"{hub_url}/api/vmixcaller/report", site, secret, report)
                except Exception:
                    pass

        except Exception:
            pass

        time.sleep(3)


# ══════════════════════════════════════════════════════════════════════════════
# Templates
# ══════════════════════════════════════════════════════════════════════════════

_CSS = """
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(circle at top,#12376f 0%,var(--bg) 38%,#05101f 100%);color:var(--tx);font-family:system-ui,sans-serif;font-size:13px;min-height:100vh}
.btn{border:none;border-radius:8px;padding:5px 12px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;display:inline-block;text-decoration:none}.btn:hover{filter:brightness(1.15)}.btn:disabled{opacity:.45;cursor:not-allowed;filter:none}
.bp{background:var(--acc);color:#fff}.bd{background:var(--al);color:#fff}.bg{background:#0d2346;color:var(--tx);border:1px solid var(--bor)}.bw{background:#1a3a1a;color:var(--ok);border:1px solid #166534}.bs{padding:3px 9px;font-size:11px}
.nav-active{background:var(--acc)!important;color:#fff!important}
main{max-width:1100px;margin:0 auto;padding:18px 16px}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:720px){.g2{grid-template-columns:1fr}}
.card{background:var(--sur);border:1px solid var(--bor);border-radius:12px;overflow:hidden;margin-bottom:14px}
.ch{padding:9px 14px;display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--bor);background:linear-gradient(180deg,#143766,#102b54);font-size:12px;font-weight:700;color:var(--acc);text-transform:uppercase;letter-spacing:.06em}
.ch-r{margin-left:auto;display:flex;gap:6px;align-items:center}
.cb{padding:14px}
.field{display:flex;flex-direction:column;gap:4px;margin-bottom:10px}.field:last-child{margin-bottom:0}
.fl{font-size:11px;color:var(--mu);font-weight:600;text-transform:uppercase;letter-spacing:.05em}
input[type=text],input[type=number],input[type=password],select{background:#0d1e40;border:1px solid var(--bor);border-radius:6px;color:var(--tx);padding:6px 9px;font-size:13px;font-family:inherit;width:100%}
input:focus,select:focus{outline:none;border-color:var(--acc)}
.r2{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.r3{display:grid;grid-template-columns:2fr 1fr 1fr;gap:8px}
.brow{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.full{width:100%;justify-content:center}
#pvw{position:relative;width:100%;aspect-ratio:16/9;background:#000;border-radius:8px;overflow:hidden;display:flex;align-items:center;justify-content:center}
#pvw video{width:100%;height:100%;object-fit:cover;display:block}
#pvw-ov{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;color:var(--mu);font-size:12px;text-align:center;padding:20px;background:#000}
#pvw-ov.hidden{display:none}
#sbar{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--mu);margin-bottom:14px;padding:8px 12px;background:var(--sur);border:1px solid var(--bor);border-radius:8px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--mu);display:inline-block;flex-shrink:0;transition:background .3s}
.dok{background:var(--ok)}.dal{background:var(--al)}.dwn{background:var(--wn)}
kbd{background:#0d1e40;border:1px solid var(--bor);border-radius:3px;padding:0 5px;font-size:11px;font-family:monospace}
.plist{display:flex;flex-direction:column;gap:6px}
.pi{display:flex;align-items:center;gap:8px;padding:7px 10px;background:#091e42;border:1px solid var(--bor);border-radius:8px}
.pn{flex:1;font-weight:600;font-size:13px}
.pbadge{font-size:10px;color:var(--mu);background:#0d2346;border:1px solid var(--bor);border-radius:4px;padding:1px 5px}
.pbadge.muted{color:var(--wn);border-color:var(--wn)}.pbadge.air{color:var(--ok);border-color:var(--ok)}
.padd{display:flex;gap:6px;margin-top:10px}.padd input{flex:1}
.stale{color:var(--wn);font-size:11px}
.guide{font-size:12px;color:var(--mu);line-height:1.65}
.guide code{background:#0d1e40;border:1px solid var(--bor);border-radius:4px;padding:2px 6px;font-size:11px;color:#7dd3fc;word-break:break-all}
.guide code.blk{display:block;margin:5px 0;padding:8px 10px}
.guide ol{padding-left:18px;margin:8px 0}.guide li{margin-bottom:8px}
#msg{padding:9px 13px;border-radius:8px;margin-bottom:12px;display:none;font-size:12px}
.mok{background:#0f2318;color:var(--ok);border:1px solid #166534}.mer{background:#2a0a0a;color:var(--al);border:1px solid #991b1b}
.call-btns{display:none}
.in-meeting .join-btn{display:none}.in-meeting .call-btns{display:flex}
.empty-note{color:var(--mu);font-size:12px;padding:4px 0}
.ago{font-size:11px;color:var(--mu)}
"""

# ── Hub template ───────────────────────────────────────────────────────────────
_HUB_TPL = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<title>vMix Caller — SignalScope</title>
<style nonce="{{csp_nonce()}}">""" + _CSS + r"""</style>
</head>
<body>
{{topnav("vmixcaller")|safe}}
<main>
<div id="msg"></div>

<!-- Status bar -->
<div id="sbar">
  <span class="dot" id="vdot"></span>
  <span id="vstatus">No site selected</span>
  <span id="vago" class="ago" style="margin-left:6px"></span>
  <span style="margin-left:auto;display:flex;gap:10px">
    <span><kbd>M</kbd> mute self</span><span><kbd>C</kbd> toggle camera</span>
  </span>
</div>

<div class="g2">

  <!-- ── LEFT: caller preview ─────────────────────────────────────────────── -->
  <div>
    <div class="card">
      <div class="ch">📹 Caller Preview</div>
      <div class="cb">
        <div id="pvw">
          <video id="pvid" autoplay muted playsinline></video>
          <div id="pvw-ov">
            <div style="font-size:36px">📷</div>
            <div id="pvmsg">Configure a bridge URL to enable preview</div>
          </div>
        </div>
      </div>
    </div>

    <!-- SRT bridge setup guide — hidden when bridge_url is saved -->
    <div class="card" id="guide-card">
      <div class="ch">⚙ SRT Bridge Setup (Ubuntu server)</div>
      <div class="cb guide">
        <p>To preview the caller here, run this once on your Ubuntu server:</p>
        <code class="blk">docker run -d --name srs-srt --restart unless-stopped \<br>
  -p 10080:10080/udp -p 8080:8080 \<br>
  ossrs/srs:5 ./objs/srs -c conf/srt.conf</code>
        <ol>
          <li>In vMix, open the Zoom input → <strong>Output</strong> → enable <strong>SRT Output</strong> and set destination:<br>
            <code>srt://&lt;ubuntu-ip&gt;:10080?streamid=#!::h=live/caller,m=publish</code></li>
          <li>In <strong>Settings</strong> below, set <strong>Preview URL</strong> to<br>
            <code>http://&lt;ubuntu-ip&gt;:8080/live/caller.m3u8</code></li>
          <li>Preview appears here once a call is active. Works natively in Safari; for Chrome configure a WebRTC or MJPEG output instead.</li>
        </ol>
      </div>
    </div>
  </div>

  <!-- ── RIGHT: controls ───────────────────────────────────────────────────── -->
  <div>

    <!-- Site + settings -->
    <div class="card">
      <div class="ch">🔌 Site &amp; Settings</div>
      <div class="cb">
        <div class="field">
          <label class="fl">vMix Site</label>
          <select id="target-site">
            <option value="">— select site —</option>
            {% for s in sites %}
            <option value="{{s|e}}"{% if s==cfg.target_site %} selected{% endif %}>{{s|e}}</option>
            {% endfor %}
          </select>
          <span style="font-size:11px;color:var(--mu);margin-top:2px">
            The site node running alongside vMix. vMix IP/port is configured on that node.
          </span>
        </div>
        <div class="r2">
          <div class="field">
            <label class="fl">vMix Input #</label>
            <input type="number" id="vmix-input" value="{{cfg.vmix_input}}" min="1" max="200">
          </div>
          <div class="field" style="justify-content:flex-end">
            <label class="fl">&nbsp;</label>
            <button class="btn bp" onclick="saveConfig()">💾 Save</button>
          </div>
        </div>
        <div class="field" style="margin-top:4px">
          <label class="fl">Preview URL (optional — HLS/MJPEG from bridge on hub network)</label>
          <input type="text" id="bridge-url"
                 placeholder="http://ubuntu-server:8080/live/caller.m3u8"
                 value="{{cfg.bridge_url|e}}">
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
        <div class="brow">
          <button class="btn bp full join-btn" onclick="joinMeeting()">📞 Join Meeting</button>
        </div>
        <div class="brow call-btns" style="flex-wrap:wrap">
          <button class="btn bg" id="mute-btn"  onclick="muteSelf()">🔇 Mute Self</button>
          <button class="btn bg" id="cam-btn"   onclick="stopCamera()">📷 Stop Camera</button>
          <button class="btn bg"                onclick="muteAll()">🔇 Mute All Guests</button>
          <button class="btn bd"                onclick="leaveMeeting()">📴 Leave</button>
        </div>
      </div>
    </div>

  </div>
</div>

<!-- Participants -->
<div class="card">
  <div class="ch">
    👥 Participants
    <div class="ch-r">
      <span id="parts-ago" class="ago"></span>
      <button class="btn bg bs" onclick="loadState()">↻ Refresh</button>
    </div>
  </div>
  <div class="cb">
    <div id="plist" class="plist">
      <div class="empty-note">Select a site and wait for the client to report participants</div>
    </div>
    <div class="padd">
      <input type="text" id="padd-in" placeholder="Manually add caller name…"
             onkeydown="if(event.key==='Enter')addManual()">
      <button class="btn bg bs" onclick="addManual()">+ Add</button>
    </div>
  </div>
</div>

</main>
<script nonce="{{csp_nonce()}}">
function _csrf(){return(document.querySelector('meta[name="csrf-token"]')||{}).content||(document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/)||[])[1]||'';}
var _msgT=null;
function showMsg(t,ok){var e=document.getElementById('msg');e.textContent=t;e.className=ok?'mok':'mer';e.style.display='block';clearTimeout(_msgT);_msgT=setTimeout(function(){e.style.display='none';},5000);}
function _post(url,data){return fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':_csrf()},credentials:'same-origin',body:JSON.stringify(data)}).then(function(r){return r.json();});}
function _esc(s){return String(s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function _ago(ts){if(!ts)return'';var s=Math.round((Date.now()/1000)-ts);if(s<5)return'just now';if(s<60)return s+'s ago';return Math.round(s/60)+'m ago';}

// ── vMix status dot ──────────────────────────────────────────────────────────
function setStatus(state,text,ts){
  document.getElementById('vdot').className='dot '+(state==='ok'?'dok':state==='warn'?'dwn':'dal');
  document.getElementById('vstatus').textContent=text;
  var ago=document.getElementById('vago');
  ago.textContent=ts?'('+_ago(ts)+')':'';
}

// ── Config ────────────────────────────────────────────────────────────────────
function saveConfig(){
  var site=document.getElementById('target-site').value;
  var inp=parseInt(document.getElementById('vmix-input').value)||1;
  var url=document.getElementById('bridge-url').value.trim();
  _post('/api/vmixcaller/config',{target_site:site,vmix_input:inp,bridge_url:url})
    .then(function(d){
      if(d.ok){showMsg('Settings saved',true);initPreview(url);if(site)loadState();}
      else showMsg(d.error||'Save failed',false);
    }).catch(function(e){showMsg('Save error: '+e,false);});
}

// ── Queue a command to vMix via the site client ──────────────────────────────
function sendCmd(fn,value){
  var site=document.getElementById('target-site').value;
  if(!site){showMsg('Select a site first',false);return Promise.reject('no site');}
  return _post('/api/vmixcaller/function',{fn:fn,value:value,
      input:parseInt(document.getElementById('vmix-input').value)||1})
    .then(function(d){
      if(!d.ok)showMsg(d.error||'Error queuing command',false);
      return d;
    }).catch(function(e){showMsg('Error: '+e,false);});
}

// ── Meeting controls ──────────────────────────────────────────────────────────
var _inMeeting=false,_selfMuted=false,_camOff=false;
function setMeetingState(v){_inMeeting=v;document.getElementById('meeting-card').className=v?'card in-meeting':'card';if(!v){_selfMuted=false;_camOff=false;}}
function joinMeeting(){
  var mid=(document.getElementById('mtg-id').value||'').trim();
  var pass=(document.getElementById('mtg-pass').value||'').trim();
  var name=(document.getElementById('mtg-name').value||'Guest Producer').trim();
  if(!mid){showMsg('Enter a Meeting ID',false);return;}
  sendCmd('ZoomJoinMeeting',mid+'|'+pass+'|'+name).then(function(d){if(d&&d.ok){showMsg('Join command queued for site',true);setMeetingState(true);}});
}
function leaveMeeting(){sendCmd('ZoomLeaveMeeting').then(function(d){if(d&&d.ok){showMsg('Leave command queued',true);setMeetingState(false);clearParticipants();}});}
function muteSelf(){sendCmd('ZoomMuteSelf').then(function(d){if(d&&d.ok){_selfMuted=!_selfMuted;document.getElementById('mute-btn').textContent=_selfMuted?'🔊 Unmute Self':'🔇 Mute Self';showMsg(_selfMuted?'Mute queued':'Unmute queued',true);}});}
function stopCamera(){sendCmd('ZoomStopCamera').then(function(d){if(d&&d.ok){_camOff=!_camOff;document.getElementById('cam-btn').textContent=_camOff?'📷 Start Camera':'📷 Stop Camera';showMsg(_camOff?'Camera stop queued':'Camera start queued',true);}});}
function muteAll(){sendCmd('ZoomMuteAll').then(function(d){if(d&&d.ok)showMsg('Mute all queued',true);});}

// ── Participants ──────────────────────────────────────────────────────────────
var _parts=[];
function clearParticipants(){_parts=[];renderParticipants([]);}
function renderParticipants(list){
  _parts=list;
  var el=document.getElementById('plist');
  if(!list||!list.length){el.innerHTML='<div class="empty-note">No participants — waiting for site client to report from vMix</div>';return;}
  el.innerHTML=list.map(function(p){
    var safe=_esc(p.name);
    var badges=(p.muted?'<span class="pbadge muted">MUTED</span>':'')+(p.active?'<span class="pbadge air">ON AIR</span>':'');
    return '<div class="pi"><span class="pn">'+safe+'</span>'+badges+'<button class="btn bw bs" data-name="'+safe+'" onclick="putOnAir(this)">📺 Put On Air</button></div>';
  }).join('');
}
function addManual(){var inp=document.getElementById('padd-in');var n=inp.value.trim();if(!n)return;if(!_parts.some(function(p){return p.name===n;})){_parts.push({name:n,muted:false,active:false});renderParticipants(_parts);}inp.value='';}
function putOnAir(btn){
  sendCmd('ZoomSelectParticipantByName',btn.dataset.name)
    .then(function(d){if(d&&d.ok)showMsg('"'+btn.dataset.name+'" queued for air',true);});
}

// ── Poll hub state (participants + connection status from client) ─────────────
var _statePollT=null;
function loadState(){
  fetch('/api/vmixcaller/state',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      var site=document.getElementById('target-site').value;
      if(!site){setStatus('off','No site selected',0);return;}
      if(!d.has_data){
        setStatus('warn','Waiting for '+site+' to report…',0);
        return;
      }
      if(d.ok){
        var ver=d.version?' v'+d.version:'';
        setStatus('ok',site+' — vMix connected'+ver,d.ts);
      } else {
        setStatus('al',site+' — vMix unreachable: '+(d.error||'unknown'),d.ts);
      }
      renderParticipants(d.participants||[]);
      var ago=document.getElementById('parts-ago');
      if(ago) ago.textContent=d.ts?'updated '+_ago(d.ts):'';
    })
    .catch(function(){setStatus('off','State poll failed',0);});
  _statePollT=setTimeout(loadState,8000);
}

// ── Video preview ─────────────────────────────────────────────────────────────
function initPreview(url){
  var vid=document.getElementById('pvid'),ov=document.getElementById('pvw-ov'),msg=document.getElementById('pvmsg'),guide=document.getElementById('guide-card');
  if(!url){ov.classList.remove('hidden');msg.textContent='Configure a bridge URL in settings';if(guide)guide.style.display='';return;}
  if(guide)guide.style.display='none';
  ov.classList.remove('hidden');msg.textContent='Connecting…';
  vid.src=url;vid.load();
  vid.oncanplay=function(){ov.classList.add('hidden');};
  vid.onerror=function(){ov.classList.remove('hidden');msg.textContent='Stream unavailable — is the SRT bridge running?';};
  vid.play().catch(function(){});
}

// ── Keyboard shortcuts ────────────────────────────────────────────────────────
document.addEventListener('keydown',function(e){
  var t=(document.activeElement||{}).tagName||'';
  if(t==='INPUT'||t==='TEXTAREA'||t==='SELECT')return;
  if(e.key==='m'||e.key==='M'){e.preventDefault();muteSelf();}
  if(e.key==='c'||e.key==='C'){e.preventDefault();stopCamera();}
});

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded',function(){
  initPreview(document.getElementById('bridge-url').value.trim());
  if(document.getElementById('target-site').value) loadState();
});
</script>
</body>
</html>"""


# ── Client status page (shown when accessing plugin page on a client node) ────
_CLIENT_TPL = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<title>vMix Caller Client — SignalScope</title>
<style nonce="{{csp_nonce()}}">""" + _CSS + r"""</style>
</head>
<body>
{{topnav("vmixcaller")|safe}}
<main>
<div id="msg"></div>
<div class="card">
  <div class="ch">📹 vMix Caller — Client Node</div>
  <div class="cb">
    <p style="margin-bottom:12px;color:var(--mu);font-size:12px">
      This is a client node. The control surface is on the hub. Configure the local
      vMix address below so this node can execute commands from the hub operator.
    </p>
    <div class="r2">
      <div class="field">
        <label class="fl">Local vMix IP</label>
        <input type="text" id="vmix-ip" placeholder="127.0.0.1" value="{{cfg.vmix_ip|e}}">
      </div>
      <div class="field">
        <label class="fl">Port</label>
        <input type="number" id="vmix-port" value="{{cfg.vmix_port}}" min="1" max="65535">
      </div>
    </div>
    <div class="field">
      <label class="fl">vMix Input # (Zoom/Teams input)</label>
      <input type="number" id="vmix-input" value="{{cfg.vmix_input}}" min="1" max="200">
    </div>
    <div class="brow">
      <button class="btn bp" onclick="saveClient()">💾 Save</button>
      <button class="btn bg" onclick="testLocal()">🔌 Test Local vMix</button>
    </div>
    <div id="test-result" style="margin-top:10px;font-size:12px;color:var(--mu)"></div>
  </div>
</div>

<div class="card">
  <div class="ch">📡 Hub Connection Status</div>
  <div class="cb" style="font-size:12px;color:var(--mu)">
    <p>Hub: <strong style="color:var(--tx)">{{hub_url|e}}</strong></p>
    <p style="margin-top:6px">This node polls the hub every 3 s for commands and reports
    participants every ~12 s. If the hub operator can see participants, this connection is working.</p>
  </div>
</div>
</main>
<script nonce="{{csp_nonce()}}">
function _csrf(){return(document.querySelector('meta[name="csrf-token"]')||{}).content||(document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/)||[])[1]||'';}
var _msgT=null;
function showMsg(t,ok){var e=document.getElementById('msg');e.textContent=t;e.className=ok?'mok':'mer';e.style.display='block';clearTimeout(_msgT);_msgT=setTimeout(function(){e.style.display='none';},5000);}
function saveClient(){
  var d={vmix_ip:document.getElementById('vmix-ip').value.trim()||'127.0.0.1',vmix_port:parseInt(document.getElementById('vmix-port').value)||8088,vmix_input:parseInt(document.getElementById('vmix-input').value)||1};
  fetch('/api/vmixcaller/config',{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':_csrf()},credentials:'same-origin',body:JSON.stringify(d)}).then(function(r){return r.json();}).then(function(r){showMsg(r.ok?'Saved':'Error: '+(r.error||'failed'),r.ok);}).catch(function(e){showMsg('Error: '+e,false);});
}
function testLocal(){
  fetch('/api/vmixcaller/test_local',{credentials:'same-origin'}).then(function(r){return r.json();}).then(function(d){
    var el=document.getElementById('test-result');
    el.style.color=d.ok?'var(--ok)':'var(--al)';
    el.textContent=d.ok?'✓ vMix reachable — version '+d.version:'✗ Cannot reach vMix: '+(d.error||'unknown');
  }).catch(function(e){document.getElementById('test-result').textContent='Error: '+e;});
}
</script>
</body>
</html>"""


# ── Plugin registration ────────────────────────────────────────────────────────

def register(app, ctx):
    login_required  = ctx["login_required"]
    csrf_protect    = ctx["csrf_protect"]
    monitor         = ctx["monitor"]
    hub_server      = ctx["hub_server"]

    cfg_ss   = monitor.app_cfg
    mode     = getattr(getattr(cfg_ss, "hub", None), "mode", "standalone") or "standalone"
    hub_url  = (getattr(getattr(cfg_ss, "hub", None), "hub_url", "") or "").rstrip("/")
    is_hub   = mode in ("hub", "both")
    is_client = mode == "client" and bool(hub_url)

    # ── Start client polling thread on client / both nodes ────────────────────
    if mode in ("client", "both") and hub_url:
        t = threading.Thread(
            target=_start_client_thread,
            args=(monitor, hub_url),
            daemon=True,
            name="VmixCallerClient",
        )
        t.start()

    # ── Main page ─────────────────────────────────────────────────────────────
    @app.get("/hub/vmixcaller")
    @login_required
    def vmixcaller_page():
        cfg = _load_cfg()

        if is_hub:
            # Build site list from hub_server._sites
            with hub_server._lock:
                sites = sorted(
                    n for n, d in hub_server._sites.items()
                    if d.get("_approved")
                )
            return render_template_string(_HUB_TPL, cfg=cfg, sites=sites)

        if is_client:
            return render_template_string(
                _CLIENT_TPL, cfg=cfg, hub_url=hub_url
            )

        # Standalone — fall through to hub template with empty sites list
        # (direct-connection path kept from v1.0.0 for single-machine setups)
        return render_template_string(_HUB_TPL, cfg=cfg, sites=[])

    # ── Config ────────────────────────────────────────────────────────────────
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
            return jsonify({"ok": True, "config": _save_cfg(data)})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    # ── Hub: queue a command for a site ───────────────────────────────────────
    @app.post("/api/vmixcaller/function")
    @login_required
    @csrf_protect
    def vmixcaller_function():
        data = request.get_json(silent=True) or {}
        fn   = str(data.get("fn", "") or "").strip()
        if not fn:
            return jsonify({"ok": False, "error": "Missing function name"}), 400

        cfg         = _load_cfg()
        target_site = cfg.get("target_site", "").strip()

        # In standalone mode, execute directly on the hub machine
        if not is_hub:
            if not cfg.get("vmix_ip"):
                return jsonify({"ok": False, "error": "No vMix IP configured"})
            ok, resp = _vmix_fn(cfg, fn, value=data.get("value"),
                                inp=data.get("input"))
            return jsonify({"ok": ok, "response": resp[:200] if ok else None,
                            "error": resp if not ok else None})

        if not target_site:
            return jsonify({"ok": False, "error": "No target site selected"}), 400

        with _state_lock:
            _pending_cmd[target_site] = {
                "fn":    fn,
                "value": data.get("value"),
                "input": data.get("input") or cfg.get("vmix_input", 1),
                "seq":   int(time.time() * 1000),
            }
        return jsonify({"ok": True, "queued_for": target_site})

    # ── Hub: client polls for its pending command ─────────────────────────────
    @app.get("/api/vmixcaller/cmd")
    @login_required
    def vmixcaller_cmd_poll():
        site = request.headers.get("X-Site", "").strip()
        if not site:
            return jsonify({"error": "missing X-Site"}), 400
        with hub_server._lock:
            sdata = hub_server._sites.get(site, {})
        if not sdata.get("_approved"):
            return jsonify({"error": "not approved"}), 403
        with _state_lock:
            cmd = _pending_cmd.pop(site, None)
        return jsonify({"cmd": cmd} if cmd else {})

    # ── Hub: client posts status / participants ────────────────────────────────
    @app.post("/api/vmixcaller/report")
    def vmixcaller_report():
        site = request.headers.get("X-Site", "").strip()
        if not site:
            return jsonify({"error": "missing X-Site"}), 400
        with hub_server._lock:
            sdata = hub_server._sites.get(site, {})
        if not sdata.get("_approved"):
            return jsonify({"error": "not approved"}), 403
        data = request.get_json(silent=True) or {}
        data["ts"] = data.get("ts") or time.time()
        data["has_data"] = True
        with _state_lock:
            _site_status[site] = data
        return jsonify({"ok": True})

    # ── Hub: browser polls for latest client status ───────────────────────────
    @app.get("/api/vmixcaller/state")
    @login_required
    def vmixcaller_state():
        cfg         = _load_cfg()
        target_site = cfg.get("target_site", "").strip()
        if not target_site:
            return jsonify({"has_data": False})
        with _state_lock:
            status = dict(_site_status.get(target_site, {}))
        status.setdefault("has_data", bool(status))
        return jsonify(status)

    # ── Client: local vMix connection test (shown on client config page) ──────
    @app.get("/api/vmixcaller/test_local")
    @login_required
    def vmixcaller_test_local():
        cfg = _load_cfg()
        ok, xml_text = _vmix_xml(cfg)
        if ok:
            return jsonify({"ok": True, "version": _vmix_version(xml_text)})
        return jsonify({"ok": False, "error": xml_text[:120]})
