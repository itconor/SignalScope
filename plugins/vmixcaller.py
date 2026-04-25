#!/usr/bin/env python3
"""
vMix Caller Manager — SignalScope plugin v1.3.0

Two audiences:
  • Operator (hub page /hub/vmixcaller)
      Selects the site running vMix, sets the vMix IP/port (pushed to the
      client node automatically on save), manages saved meetings, sees the
      participants list, queues commands.  Back-of-house.

  • Presenter (/hub/vmixcaller/presenter)
      Clean bookmark-friendly page on the "email machine".  Shows the
      live Zoom video feed, a one-click list of saved meetings, and basic
      in-call controls (mute, camera, leave).  No technical config needed.

Hub / client architecture:
  Commands are queued on the hub; the site node alongside vMix polls for
  them and executes against the local vMix API, then reports participants
  and connection status back.  Works through NAT — no direct hub→site
  connectivity required.

Video security:
  The SRT→HLS bridge (SRS Docker) binds port 8080 to localhost only.
  The plugin proxies HLS through /hub/vmixcaller/video/<path> which
  requires a valid SignalScope session — the video is never publicly
  accessible.  The SRT input port (UDP 10080) remains open so vMix can
  push from the remote site.
"""

SIGNALSCOPE_PLUGIN = {
    "id":      "vmixcaller",
    "label":   "vMix Caller",
    "url":     "/hub/vmixcaller",
    "icon":    "📹",
    "version": "1.5.0",
}

import os
import json
import time
import threading
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

from flask import request, jsonify, render_template_string, Response, abort

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_CFG_PATH = os.path.join(_BASE_DIR, "vmixcaller_config.json")
_cfg_lock = threading.Lock()

_DEFAULTS: dict = {
    "target_site":    "",
    "bridge_url":     "",
    "vmix_input":     1,
    "vmix_ip":        "127.0.0.1",
    "vmix_port":      8088,
    # saved_meetings is a list — handled separately below
}

# ── Hub-side runtime state ─────────────────────────────────────────────────────
_pending_cmd: dict = {}   # site_name → {fn, value, input, seq}
_site_status: dict = {}   # site_name → {ok, version, participants, error, ts}
_state_lock          = threading.Lock()

# ── Hub-side HLS relay buffer ──────────────────────────────────────────────────
# Client nodes fetch TS segments from the local LAN bridge and push them here.
# Hub serves a synthetic HLS manifest pointing to buffered segments so remote
# browsers (HTTPS hub) can play the video without reaching the LAN bridge.
_video_segments: dict = {}   # seq_num (int) → {"data": bytes, "duration": float}
_video_seq             = [0] # next seq — list so nested functions can mutate
_video_lock            = threading.Lock()
_VIDEO_MAX_SEGS        = 6   # ~12–18 s buffer at 2–3 s/segment


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
    # saved_meetings is preserved as-is (list of dicts)
    out["saved_meetings"] = saved.get("saved_meetings") or []
    return out


def _save_cfg(data: dict) -> dict:
    # Load full current config so we never lose keys we're not updating
    current = _load_cfg()
    for k in _DEFAULTS:
        if k in data:
            v = data[k]
            if k in ("vmix_port", "vmix_input"):
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    v = _DEFAULTS[k]
            current[k] = v
    if "saved_meetings" in data:
        current["saved_meetings"] = data["saved_meetings"]
    with _cfg_lock:
        with open(_CFG_PATH, "w") as fh:
            json.dump(current, fh, indent=2)
    return current


def _proxy_video_url(bridge_url: str) -> str:
    """Convert an internal bridge URL to the authenticated SignalScope proxy path.

    e.g. "http://127.0.0.1:8080/live/caller.m3u8"
       → "/hub/vmixcaller/video/live/caller.m3u8"
    """
    if not bridge_url:
        return ""
    try:
        path = urlparse(bridge_url.strip()).path
        if not path.startswith("/"):
            path = "/" + path
        return "/hub/vmixcaller/video" + path
    except Exception:
        return ""


# ── vMix API helpers (executed on the CLIENT side) ────────────────────────────

def _vmix_base(cfg: dict) -> str:
    ip   = (cfg.get("vmix_ip") or "127.0.0.1").strip().rstrip("/")
    port = int(cfg.get("vmix_port") or 8088)
    return f"http://{ip}:{port}/API"


def _vmix_fn(cfg: dict, fn: str, value: str = None, inp: int = None):
    """Call a vMix API function. Returns (ok: bool, text: str)."""
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
    """Fetch full vMix XML status. Returns (ok: bool, text: str)."""
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
    import hashlib, hmac as _hmac

    def _sign(secret, data_bytes, ts):
        key = hashlib.sha256(f"{secret}:signing".encode()).digest()
        msg = f"{ts:.0f}:".encode() + data_bytes
        return _hmac.new(key, msg, hashlib.sha256).hexdigest()

    def _hub_get(url, site, secret):
        ts   = time.time()
        hdrs = {"X-Site": site}
        if secret:
            sig = _sign(secret, b"", ts)
            hdrs.update({"X-Hub-Sig": sig, "X-Hub-Ts": f"{ts:.0f}",
                         "X-Hub-Nonce": hashlib.md5(os.urandom(8)).hexdigest()[:16]})
        req  = urllib.request.Request(url, headers=hdrs, method="GET")
        resp = urllib.request.urlopen(req, timeout=8)
        return json.loads(resp.read())

    def _hub_post(url, site, secret, payload_dict):
        payload = json.dumps(payload_dict, separators=(",", ":")).encode()
        ts   = time.time()
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
            cfg     = _load_cfg()
            app_cfg = monitor.app_cfg
            site    = (getattr(getattr(app_cfg, "hub", None), "site_name", "") or "").strip()
            secret  = (getattr(getattr(app_cfg, "hub", None), "secret_key",  "") or "").strip()
            if not site:
                time.sleep(5)
                continue

            d   = _hub_get(f"{hub_url}/api/vmixcaller/cmd", site, secret)
            cmd = d.get("cmd")
            if cmd and cmd.get("seq") != last_cmd_seq:
                last_cmd_seq = cmd["seq"]
                fn = cmd.get("fn", "")

                if fn == "__set_config__":
                    # Hub is pushing vMix connection config — save locally
                    updates = {}
                    if "vmix_ip"   in cmd: updates["vmix_ip"]   = cmd["vmix_ip"]
                    if "vmix_port" in cmd: updates["vmix_port"]  = cmd["vmix_port"]
                    if updates:
                        _save_cfg(updates)
                        cfg = _load_cfg()   # reload so next iteration uses new values
                else:
                    ok, resp = _vmix_fn(cfg, fn,
                                        value=cmd.get("value"), inp=cmd.get("input"))
                    try:
                        _hub_post(f"{hub_url}/api/vmixcaller/report", site, secret, {
                            "cmd_result": {"seq": cmd["seq"], "ok": ok, "resp": resp[:120]},
                        })
                    except Exception:
                        pass

            now = time.time()
            if now - last_full_report >= 12.0:
                last_full_report = now
                ok_xml, xml_text = _vmix_xml(cfg)
                report: dict = {
                    "ts":        now,
                    "ok":        ok_xml,
                    "vmix_ip":   cfg.get("vmix_ip",  "127.0.0.1"),
                    "vmix_port": cfg.get("vmix_port", 8088),
                }
                if ok_xml:
                    report["version"]      = _vmix_version(xml_text)
                    report["participants"] = _parse_participants(
                        xml_text, cfg.get("vmix_input", 1))
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


# ── Video relay — client pushes HLS segments to hub ───────────────────────────

def _video_relay_loop(monitor, hub_url: str):
    """Runs on client nodes only.  Fetches TS segments from the local LAN
    bridge and POSTs them to the hub so remote browsers can play the stream
    via the hub's synthetic HLS manifest — no direct LAN access needed.

    Poll interval: 1.5 s (segments are typically 2–3 s long, so we get each
    one once while staying well inside the segment duration).
    """
    import collections, hashlib, hmac as _hmac

    hub_url = hub_url.rstrip("/")

    # Track which segment filenames we have already pushed (bounded)
    _sent_list: list = []
    _sent_set:  set  = set()

    def _track(name: str):
        if name in _sent_set:
            return
        _sent_set.add(name)
        _sent_list.append(name)
        if len(_sent_list) > 40:
            old = _sent_list.pop(0)
            _sent_set.discard(old)

    def _sign(secret: str, data_bytes: bytes, ts: float) -> str:
        key = hashlib.sha256(f"{secret}:signing".encode()).digest()
        msg = f"{ts:.0f}:".encode() + data_bytes
        return _hmac.new(key, msg, hashlib.sha256).hexdigest()

    def _push(site: str, secret: str, seg_data: bytes, duration: float):
        ts   = time.time()
        hdrs = {
            "Content-Type":   "application/octet-stream",
            "X-Site":         site,
            "X-Seg-Duration": f"{duration:.3f}",
        }
        if secret:
            sig = _sign(secret, seg_data, ts)
            hdrs.update({
                "X-Hub-Sig":   sig,
                "X-Hub-Ts":    f"{ts:.0f}",
                "X-Hub-Nonce": hashlib.md5(os.urandom(8)).hexdigest()[:16],
            })
        req = urllib.request.Request(
            f"{hub_url}/api/vmixcaller/video_push",
            data=seg_data, headers=hdrs, method="POST",
        )
        urllib.request.urlopen(req, timeout=12).close()

    while True:
        try:
            cfg        = _load_cfg()
            bridge_url = (cfg.get("bridge_url") or "").strip()
            app_cfg    = monitor.app_cfg
            site       = (getattr(getattr(app_cfg, "hub", None), "site_name", "") or "").strip()
            secret     = (getattr(getattr(app_cfg, "hub", None), "secret_key",  "") or "").strip()

            if not bridge_url or not site:
                time.sleep(5)
                continue

            parsed       = urlparse(bridge_url)
            base         = f"{parsed.scheme}://{parsed.netloc}"
            manifest_url = base + parsed.path

            # ── Fetch manifest ────────────────────────────────────────────────
            resp     = urllib.request.urlopen(
                urllib.request.Request(manifest_url, method="GET"), timeout=5)
            manifest = resp.read().decode("utf-8", errors="replace")

            # ── Parse EXTINF + segment lines ──────────────────────────────────
            to_fetch: list = []
            duration = 2.0
            for line in manifest.splitlines():
                line = line.strip()
                if line.startswith("#EXTINF:"):
                    try:
                        duration = float(line[8:].split(",")[0])
                    except Exception:
                        pass
                elif line and not line.startswith("#"):
                    to_fetch.append((line, duration))

            # ── Fetch & push only new segments ────────────────────────────────
            manifest_dir = parsed.path.rsplit("/", 1)[0]
            for seg_name, seg_dur in to_fetch:
                if seg_name in _sent_set:
                    continue
                if seg_name.startswith("http"):
                    seg_url = seg_name
                elif seg_name.startswith("/"):
                    seg_url = base + seg_name
                else:
                    seg_url = base + manifest_dir + "/" + seg_name
                try:
                    seg_data = urllib.request.urlopen(
                        urllib.request.Request(seg_url, method="GET"), timeout=10
                    ).read()
                except Exception:
                    continue
                try:
                    _push(site, secret, seg_data, seg_dur)
                    _track(seg_name)
                except Exception:
                    pass   # hub unreachable — will retry next cycle

        except Exception:
            pass   # bridge not running, not configured, etc. — keep looping

        time.sleep(1.5)


# ══════════════════════════════════════════════════════════════════════════════
# Shared CSS
# ══════════════════════════════════════════════════════════════════════════════

_CSS = """:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(circle at top,#12376f 0%,var(--bg) 38%,#05101f 100%);color:var(--tx);font-family:system-ui,sans-serif;font-size:13px;min-height:100vh}
.btn{border:none;border-radius:8px;padding:5px 12px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;display:inline-block;text-decoration:none}.btn:hover{filter:brightness(1.15)}.btn:disabled{opacity:.45;cursor:not-allowed;filter:none}
.bp{background:var(--acc);color:#fff}.bd{background:var(--al);color:#fff}.bg{background:#0d2346;color:var(--tx);border:1px solid var(--bor)}.bw{background:#1a3a1a;color:var(--ok);border:1px solid #166534}.bs{padding:3px 9px;font-size:11px}
.nav-active{background:var(--acc)!important;color:#fff!important}
main{max-width:1100px;margin:0 auto;padding:18px 16px}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media(max-width:720px){.g2{grid-template-columns:1fr}}
.card{background:var(--sur);border:1px solid var(--bor);border-radius:12px;overflow:hidden;margin-bottom:14px}
.ch{padding:9px 14px;display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--bor);background:linear-gradient(180deg,#143766,#102b54);font-size:12px;font-weight:700;color:var(--acc);text-transform:uppercase;letter-spacing:.06em}
.ch-r{margin-left:auto;display:flex;gap:6px;align-items:center}
.cb{padding:14px}
.field{display:flex;flex-direction:column;gap:4px;margin-bottom:10px}.field:last-child{margin-bottom:0}
.fl{font-size:11px;color:var(--mu);font-weight:600;text-transform:uppercase;letter-spacing:.05em}
input[type=text],input[type=number],input[type=password],select{background:#0d1e40;border:1px solid var(--bor);border-radius:6px;color:var(--tx);padding:6px 9px;font-size:13px;font-family:inherit;width:100%}
input:focus,select:focus{outline:none;border-color:var(--acc)}
.r2{display:grid;grid-template-columns:1fr 1fr;gap:8px}.r3{display:grid;grid-template-columns:2fr 1fr 1fr;gap:8px}
.brow{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--mu);display:inline-block;flex-shrink:0;transition:background .3s}
.dok{background:var(--ok)}.dal{background:var(--al)}.dwn{background:var(--wn)}
kbd{background:#0d1e40;border:1px solid var(--bor);border-radius:3px;padding:0 5px;font-size:11px;font-family:monospace}
.plist{display:flex;flex-direction:column;gap:6px}
.pi{display:flex;align-items:center;gap:8px;padding:7px 10px;background:#091e42;border:1px solid var(--bor);border-radius:8px}
.pn{flex:1;font-weight:600;font-size:13px}
.pbadge{font-size:10px;color:var(--mu);background:#0d2346;border:1px solid var(--bor);border-radius:4px;padding:1px 5px}
.pbadge.muted{color:var(--wn);border-color:var(--wn)}.pbadge.air{color:var(--ok);border-color:var(--ok)}
.padd{display:flex;gap:6px;margin-top:10px}.padd input{flex:1}
.ago{font-size:11px;color:var(--mu)}
#msg{padding:9px 13px;border-radius:8px;margin-bottom:12px;display:none;font-size:12px}
.mok{background:#0f2318;color:var(--ok);border:1px solid #166534}.mer{background:#2a0a0a;color:var(--al);border:1px solid #991b1b}
.empty-note{color:var(--mu);font-size:12px;padding:4px 0}
/* video */
.pvw-wrap{position:relative;width:100%;aspect-ratio:16/9;background:#000;border-radius:10px;overflow:hidden}
.pvw-wrap video{width:100%;height:100%;object-fit:cover;display:block}
.pvw-ov{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;color:var(--mu);font-size:12px;text-align:center;padding:20px;background:rgba(0,0,0,.82)}
.pvw-ov.hidden{display:none}
.pvw-icon{font-size:40px;line-height:1}
/* call state */
.call-btns{display:none}
.in-meeting .join-btn{display:none}.in-meeting .call-btns{display:flex}
/* saved meetings */
.mtg-row{display:flex;align-items:center;gap:8px;padding:8px 10px;background:#091e42;border:1px solid var(--bor);border-radius:8px;margin-bottom:6px}
.mtg-name{flex:1;font-weight:600}
.mtg-id{font-size:11px;color:var(--mu)}"""

# ── Shared JS helpers (inlined into both pages) ────────────────────────────────
_JS_HELPERS = r"""
function _csrf(){return(document.querySelector('meta[name="csrf-token"]')||{}).content||(document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/)||[])[1]||'';}
var _msgT=null;
function showMsg(t,ok){var e=document.getElementById('msg');if(!e)return;e.textContent=t;e.className=ok?'mok':'mer';e.style.display='block';clearTimeout(_msgT);_msgT=setTimeout(function(){e.style.display='none';},5500);}
function _post(url,data){return fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':_csrf()},credentials:'same-origin',body:JSON.stringify(data)}).then(function(r){return r.json();});}
function _del(url){return fetch(url,{method:'DELETE',headers:{'X-CSRFToken':_csrf()},credentials:'same-origin'}).then(function(r){return r.json();});}
function _esc(s){return String(s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function _ago(ts){if(!ts)return'';var s=Math.round((Date.now()/1000)-ts);if(s<5)return'just now';if(s<60)return s+'s ago';return Math.round(s/60)+'m ago';}

// ── Proxy URL helper ──────────────────────────────────────────────────────────
// Always routes video through the local SignalScope proxy
// (/hub/vmixcaller/video/<path>), regardless of whether the bridge URL is
// localhost or a LAN IP.
//
// Why this works for both setups:
//  • Bridge on hub server (localhost): hub proxy fetches from 127.0.0.1 — works.
//  • Bridge on client LAN: hub proxy can't reach the LAN IP, BUT the same
//    proxy route is also registered on the client node. If the presenter opens
//    the presenter page from the client node URL (http://client-ip:port/...)
//    the client's proxy fetches from the LAN bridge — works, no HTTPS/mixed
//    content issues, no cert needed.
function _proxyUrl(u){
  if(!u)return'';
  try{var p=new URL(u);return'/hub/vmixcaller/video'+p.pathname;}
  catch(e){return'';}
}

// ── Video preview ─────────────────────────────────────────────────────────────
// Always converts to proxy URL before setting video src.
function initPreview(url){
  var vid=document.getElementById('pvid');
  var ov=document.getElementById('pvw-ov');
  var msg=document.getElementById('pvmsg');
  if(!vid||!ov)return;
  var purl=_proxyUrl(url);
  if(!purl){ov.classList.remove('hidden');if(msg)msg.textContent='No preview stream configured';return;}
  ov.classList.remove('hidden');if(msg)msg.textContent='Connecting to stream\u2026';
  vid.src=purl;vid.load();
  vid.oncanplay=function(){ov.classList.add('hidden');};
  vid.onerror=function(){ov.classList.remove('hidden');if(msg)msg.textContent='Stream unavailable \u2014 is the bridge running?';};
  vid.play().catch(function(){});
}

// ── Send command (queued via hub) ─────────────────────────────────────────────
function sendCmd(fn, value){
  return _post('/api/vmixcaller/function',{fn:fn,value:value}).then(function(d){
    if(!d.ok)showMsg(d.error||'Error queuing command',false);
    return d;
  }).catch(function(e){showMsg('Error: '+e,false);return{ok:false};});
}

// ── Call state ────────────────────────────────────────────────────────────────
var _inMeeting=false,_selfMuted=false,_camOff=false;
function setMeetingState(v){
  _inMeeting=v;
  document.querySelectorAll('.meeting-card').forEach(function(c){c.className=v?'card meeting-card in-meeting':'card meeting-card';});
  if(!v){_selfMuted=false;_camOff=false;resetCallBtns();}
}
function resetCallBtns(){
  var mb=document.getElementById('mute-btn');var cb=document.getElementById('cam-btn');
  if(mb)mb.textContent='\uD83D\uDD07 Mute Self';if(cb)cb.textContent='\uD83D\uDCF7 Stop Camera';
}

function joinWith(mid,pass,name){
  if(!mid){showMsg('Enter a Meeting ID',false);return;}
  sendCmd('ZoomJoinMeeting',mid+'|'+(pass||'')+'|'+(name||'Guest Producer'))
    .then(function(d){if(d.ok){showMsg('Joining\u2026',true);setMeetingState(true);}});
}
function leaveMeeting(){
  sendCmd('ZoomLeaveMeeting').then(function(d){if(d.ok){showMsg('Left meeting',true);setMeetingState(false);}});
}
function muteSelf(){
  sendCmd('ZoomMuteSelf').then(function(d){
    if(d.ok){_selfMuted=!_selfMuted;var b=document.getElementById('mute-btn');if(b)b.textContent=_selfMuted?'\uD83D\uDD0A Unmute Self':'\uD83D\uDD07 Mute Self';showMsg(_selfMuted?'Muted':'Unmuted',true);}
  });
}
function stopCamera(){
  sendCmd('ZoomStopCamera').then(function(d){
    if(d.ok){_camOff=!_camOff;var b=document.getElementById('cam-btn');if(b)b.textContent=_camOff?'\uD83D\uDCF7 Start Camera':'\uD83D\uDCF7 Stop Camera';showMsg(_camOff?'Camera off':'Camera on',true);}
  });
}
function muteAll(){sendCmd('ZoomMuteAll').then(function(d){if(d.ok)showMsg('All guests muted',true);});}

// ── Keyboard shortcuts ────────────────────────────────────────────────────────
document.addEventListener('keydown',function(e){
  var t=(document.activeElement||{}).tagName||'';
  if(t==='INPUT'||t==='TEXTAREA'||t==='SELECT')return;
  if(e.key==='m'||e.key==='M'){e.preventDefault();muteSelf();}
  if(e.key==='c'||e.key==='C'){e.preventDefault();stopCamera();}
});
"""

# ══════════════════════════════════════════════════════════════════════════════
# Presenter page  (/hub/vmixcaller/presenter)
# ══════════════════════════════════════════════════════════════════════════════

_PRESENTER_TPL = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<title>Caller — vMix</title>
<style nonce="{{csp_nonce()}}">
""" + _CSS + r"""
/* ── Presenter-specific overrides ─────────────────────────────────────── */
main{max-width:860px}
.hero-video{margin-bottom:18px}
.pvw-wrap{border-radius:12px;border:2px solid var(--bor)}
.mtg-row{padding:11px 14px;cursor:default}
.mtg-name{font-size:14px}
.join-big{padding:7px 18px;font-size:13px}
/* In-call toolbar */
#call-bar{display:none;background:var(--sur);border:1px solid var(--bor);border-radius:12px;padding:12px 14px;margin-bottom:14px;align-items:center;gap:10px;flex-wrap:wrap}
#call-bar.visible{display:flex}
#call-bar .badge{font-size:11px;padding:2px 8px;border-radius:20px;background:#0f2318;color:var(--ok);border:1px solid #166534;font-weight:600}
#call-bar .spacer{flex:1}
/* Manual join section */
#manual-section{margin-bottom:14px}
#manual-toggle{background:none;border:none;color:var(--mu);font-size:12px;cursor:pointer;padding:0;font-family:inherit;text-decoration:underline;text-underline-offset:2px}
#manual-toggle:hover{color:var(--tx)}
#manual-form{display:none;margin-top:10px}
#manual-form.open{display:block}
.no-meetings{color:var(--mu);font-size:13px;padding:12px;text-align:center;border:1px dashed var(--bor);border-radius:8px}
</style>
</head>
<body>
{{topnav("vmixcaller")|safe}}
<main>
<div id="msg"></div>

<!-- ── In-call toolbar ────────────────────────────────────────────────────── -->
<div id="call-bar">
  <span class="badge">● ON CALL</span>
  <button class="btn bg" id="mute-btn" onclick="muteSelf()">🔇 Mute Self</button>
  <button class="btn bg" id="cam-btn"  onclick="stopCamera()">📷 Stop Camera</button>
  <div class="spacer"></div>
  <button class="btn bd" onclick="hangUp()">📴 Leave</button>
</div>

<!-- ── Video feed ─────────────────────────────────────────────────────────── -->
<div class="card hero-video">
  <div class="ch">
    📹 Caller
    {% if not bridge_url %}
    <span style="margin-left:auto;font-size:11px;color:var(--wn);font-weight:400;text-transform:none;letter-spacing:0">
      ⚠ No preview stream — ask your engineer to configure a Bridge URL
    </span>
    {% endif %}
  </div>
  <div class="cb" style="padding:10px">
    <div class="pvw-wrap">
      <video id="pvid" autoplay muted playsinline></video>
      <div id="pvw-ov">
        <div class="pvw-icon">📷</div>
        <div id="pvmsg">{% if bridge_url %}Waiting for caller…{% else %}No preview stream configured{% endif %}</div>
      </div>
    </div>
  </div>
</div>

<!-- ── Saved meetings ──────────────────────────────────────────────────────── -->
<div class="card">
  <div class="ch">
    📋 Meetings
    <div class="ch-r">
      <a href="/hub/vmixcaller" class="btn bg bs">⚙ Hub Controls</a>
    </div>
  </div>
  <div class="cb">
    {% if meetings %}
      {% for m in meetings %}
      <div class="mtg-row">
        <div>
          <div class="mtg-name">{{m.name|e}}</div>
          <div class="mtg-id">ID: {{m.id|e}}{% if m.pass %} &nbsp;·&nbsp; Passcode set{% endif %}</div>
        </div>
        <button class="btn bp join-big join-btn"
                data-mid="{{m.id|e}}"
                data-pass="{{m.pass|e}}"
                data-dname="{{m.display_name|e}}"
                onclick="joinSaved(this)">📞 Join</button>
      </div>
      {% endfor %}
    {% else %}
      <div class="no-meetings">No saved meetings yet — add them in <a href="/hub/vmixcaller" style="color:var(--acc)">Hub Controls</a></div>
    {% endif %}
  </div>
</div>

<!-- ── Manual join ─────────────────────────────────────────────────────────── -->
<div id="manual-section">
  <button id="manual-toggle" onclick="toggleManual()">＋ Join a different meeting…</button>
  <div id="manual-form">
    <div class="card" style="margin-top:8px;margin-bottom:0">
      <div class="ch">✏ Join Manually</div>
      <div class="cb">
        <div class="field">
          <label class="fl">Meeting ID</label>
          <input type="text" id="mtg-id" placeholder="123 456 7890"
                 onkeydown="if(event.key==='Enter')joinManual()">
        </div>
        <div class="r2">
          <div class="field">
            <label class="fl">Passcode</label>
            <input type="password" id="mtg-pass" placeholder="••••••">
          </div>
          <div class="field">
            <label class="fl">Your Display Name</label>
            <input type="text" id="mtg-name" placeholder="Guest Producer">
          </div>
        </div>
        <div class="brow">
          <button class="btn bp join-btn" onclick="joinManual()">📞 Join Meeting</button>
        </div>
      </div>
    </div>
  </div>
</div>

</main>
<script nonce="{{csp_nonce()}}">
""" + _JS_HELPERS + r"""

// ── Presenter-specific ────────────────────────────────────────────────────────
var _bridgeUrl = {{bridge_url_json}};

function joinSaved(btn){
  joinWith(btn.dataset.mid, btn.dataset.pass, btn.dataset.dname||'Guest Producer');
}
function joinManual(){
  var mid =(document.getElementById('mtg-id').value||'').trim();
  var pass=(document.getElementById('mtg-pass').value||'').trim();
  var name=(document.getElementById('mtg-name').value||'Guest Producer').trim();
  joinWith(mid, pass, name);
}

// Override setMeetingState to also show/hide call bar and disable join buttons
var _baseSMS = setMeetingState;
setMeetingState = function(v){
  _baseSMS(v);
  var bar=document.getElementById('call-bar');
  if(bar){if(v)bar.classList.add('visible');else bar.classList.remove('visible');}
  document.querySelectorAll('.join-btn').forEach(function(b){b.disabled=v;});
};

function hangUp(){
  leaveMeeting();
  if(_bridgeUrl) setTimeout(function(){initPreview(_bridgeUrl);},1500);
}

function toggleManual(){
  var f=document.getElementById('manual-form');
  var t=document.getElementById('manual-toggle');
  var open=f.classList.toggle('open');
  t.textContent=open?'✕ Cancel':'＋ Join a different meeting…';
}

document.addEventListener('DOMContentLoaded',function(){
  initPreview(_bridgeUrl);
});
</script>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════════════════════
# Hub / operator page  (/hub/vmixcaller)
# ══════════════════════════════════════════════════════════════════════════════

_HUB_TPL = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<title>vMix Caller — SignalScope</title>
<style nonce="{{csp_nonce()}}">
""" + _CSS + r"""
/* hub-specific */
#sbar{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--mu);margin-bottom:14px;padding:8px 12px;background:var(--sur);border:1px solid var(--bor);border-radius:8px}
.sep{border:none;border-top:1px solid var(--bor);margin:12px 0}
.mtg-admin-row{display:flex;align-items:center;gap:8px;padding:7px 10px;background:#091e42;border:1px solid var(--bor);border-radius:8px;margin-bottom:6px}
.mtg-admin-row .mtg-name{flex:1;font-weight:600}
.mtg-admin-row .mtg-id{font-size:11px;color:var(--mu)}
.add-mtg{display:grid;grid-template-columns:1fr 1fr 1fr auto;gap:8px;margin-top:10px;align-items:end}
@media(max-width:600px){.add-mtg{grid-template-columns:1fr 1fr}}
.hint{font-size:11px;color:var(--mu);margin-top:3px;line-height:1.4}
.reported-addr{font-size:11px;color:var(--ok);margin-top:3px}
</style>
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
  <span style="margin-left:auto;display:flex;gap:10px;align-items:center">
    <a href="/hub/vmixcaller/presenter" class="btn bp bs" title="For HTTPS hubs with a LAN bridge, give the presenter the client node URL instead — see setup guide">🖥 Presenter View</a>
    <span><kbd>M</kbd> mute</span><span><kbd>C</kbd> camera</span>
  </span>
</div>

<div class="g2">

  <!-- ── LEFT: preview ──────────────────────────────────────────────────────── -->
  <div>
    <div class="card">
      <div class="ch">📹 Caller Preview</div>
      <div class="cb" style="padding:10px">
        <div class="pvw-wrap">
          <video id="pvid" autoplay muted playsinline></video>
          <div id="pvw-ov">
            <div class="pvw-icon">📷</div>
            <div id="pvmsg">Configure a Bridge URL to enable preview</div>
          </div>
        </div>
      </div>
    </div>

    <!-- SRT bridge setup guide -->
    <div class="card">
      <div class="ch">⚙ SRT Bridge Setup</div>
      <div class="cb" style="font-size:12px;color:var(--mu);line-height:1.65">

        <p style="font-weight:600;color:var(--tx);margin-bottom:6px">Option A — Bridge on the same LAN as vMix <span style="font-weight:400;color:var(--ok)">(recommended)</span></p>
        <p style="margin-bottom:6px">Run the bridge on any Ubuntu machine on the <strong style="color:var(--tx)">same LAN as vMix</strong> (can be the SignalScope client node):</p>
        <pre style="background:#0d1e40;border:1px solid var(--bor);border-radius:6px;padding:8px 10px;font-size:11px;color:#7dd3fc;white-space:pre-wrap;margin:6px 0">docker run -d --name srs-srt --restart unless-stopped \
  -p 10080:10080/udp -p 8080:8080 \
  ossrs/srs:5 ./objs/srs -c conf/srt.conf</pre>
        <p style="margin-bottom:4px">In vMix → Zoom input → <strong>Output</strong> → enable SRT, set <strong>Type: Caller</strong>:</p>
        <ul style="padding-left:16px;margin-bottom:6px">
          <li><strong style="color:var(--tx)">Hostname</strong> — LAN IP of the bridge machine (e.g. <code style="background:#0d1e40;padding:1px 5px;border-radius:3px">192.168.13.2</code>)</li>
          <li><strong style="color:var(--tx)">Port</strong> — <code style="background:#0d1e40;padding:1px 5px;border-radius:3px">10080</code></li>
          <li><strong style="color:var(--tx)">Stream ID</strong> — <code style="background:#0d1e40;padding:1px 5px;border-radius:3px">#!::h=live/caller,m=publish</code></li>
        </ul>
        <p>Set <strong style="color:var(--tx)">Bridge URL</strong> below to the bridge machine's LAN IP:</p>
        <pre style="background:#0d1e40;border:1px solid var(--bor);border-radius:6px;padding:6px 10px;font-size:11px;color:#7dd3fc;margin:6px 0">http://192.168.13.2:8080/live/caller.m3u8</pre>
        <p style="font-size:11px;color:var(--wn);margin-top:4px">⚠ <strong>Hub uses HTTPS?</strong> Don't use the hub URL for the presenter page — the browser will block HTTP video as mixed content. Instead, bookmark the presenter page from the <strong>client node</strong> (see note below).</p>

        <hr style="border:none;border-top:1px solid var(--bor);margin:12px 0">

        <p style="font-weight:600;color:var(--tx);margin-bottom:6px">Option B — Bridge on the hub server</p>
        <p style="margin-bottom:6px">Run SRS on the hub server. Bind port 8080 to localhost — SignalScope proxies it securely. vMix pushes SRT over the internet to the hub's public IP.</p>
        <pre style="background:#0d1e40;border:1px solid var(--bor);border-radius:6px;padding:8px 10px;font-size:11px;color:#7dd3fc;white-space:pre-wrap;margin:6px 0">docker run -d --name srs-srt --restart unless-stopped \
  -p 10080:10080/udp -p 127.0.0.1:8080:8080 \
  ossrs/srs:5 ./objs/srs -c conf/srt.conf</pre>
        <p>Set vMix SRT Hostname to the hub's public IP, then set Bridge URL to:</p>
        <pre style="background:#0d1e40;border:1px solid var(--bor);border-radius:6px;padding:6px 10px;font-size:11px;color:#7dd3fc;margin:6px 0">http://127.0.0.1:8080/live/caller.m3u8</pre>
        <p style="font-size:11px">SignalScope automatically detects the localhost URL and proxies the stream — no public port 8080 needed.</p>

        <hr style="border:none;border-top:1px solid var(--bor);margin:12px 0">

        <p style="font-weight:600;color:var(--tx);margin-bottom:6px">📌 Presenter bookmark — hub on HTTPS</p>
        <p style="margin-bottom:6px">If your hub uses HTTPS and the bridge is on a LAN machine, the presenter page must be opened from the <strong style="color:var(--tx)">client node</strong> URL (HTTP), not the hub URL. The video is proxied through SignalScope locally — no cert or port changes needed.</p>
        <p style="margin-bottom:4px">Give the presenter this bookmark:</p>
        <pre style="background:#0d1e40;border:1px solid var(--bor);border-radius:6px;padding:6px 10px;font-size:11px;color:#7dd3fc;margin:6px 0">http://&lt;client-node-ip&gt;:&lt;port&gt;/hub/vmixcaller/presenter</pre>
        <p style="font-size:11px">The hub presenter button still works for operators (hub can proxy a localhost bridge). For a LAN bridge, only the client node can reach it.</p>

      </div>
    </div>
  </div>

  <!-- ── RIGHT: controls ────────────────────────────────────────────────────── -->
  <div>
    <!-- Settings -->
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
        </div>
        <div class="r2">
          <div class="field">
            <label class="fl">vMix IP (on site node)</label>
            <input type="text" id="vmix-ip" placeholder="127.0.0.1" value="{{cfg.vmix_ip|e}}">
            <div class="hint" id="vmix-ip-reported"></div>
          </div>
          <div class="field">
            <label class="fl">vMix Port</label>
            <input type="number" id="vmix-port" value="{{cfg.vmix_port}}" min="1" max="65535">
          </div>
        </div>
        <div class="r2">
          <div class="field">
            <label class="fl">vMix Input #</label>
            <input type="number" id="vmix-input" value="{{cfg.vmix_input}}" min="1" max="200">
          </div>
          <div class="field" style="justify-content:flex-end">
            <label class="fl">&nbsp;</label>
            <button class="btn bp" onclick="saveConfig()">💾 Save &amp; Push to Site</button>
          </div>
        </div>
        <div class="field" style="margin-top:4px">
          <label class="fl">Bridge URL (internal HLS, proxied by SignalScope)</label>
          <input type="text" id="bridge-url" placeholder="http://127.0.0.1:8080/live/caller.m3u8" value="{{cfg.bridge_url|e}}">
          <div class="hint">Run the SRT bridge on this hub server — see setup guide on the left.</div>
        </div>
      </div>
    </div>

    <!-- Meeting controls -->
    <div class="card meeting-card" id="meeting-card">
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
          <button class="btn bp join-btn" style="flex:1;justify-content:center" onclick="joinManual()">📞 Join Meeting</button>
        </div>
        <div class="brow call-btns" style="flex-wrap:wrap">
          <button class="btn bg" id="mute-btn" onclick="muteSelf()">🔇 Mute Self</button>
          <button class="btn bg" id="cam-btn"  onclick="stopCamera()">📷 Stop Camera</button>
          <button class="btn bg"               onclick="muteAll()">🔇 Mute All</button>
          <button class="btn bd"               onclick="leaveMeeting()">📴 Leave</button>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- ── Participants ────────────────────────────────────────────────────────── -->
<div class="card">
  <div class="ch">👥 Participants
    <div class="ch-r">
      <span id="parts-ago" class="ago"></span>
      <button class="btn bg bs" onclick="loadState()">↻ Refresh</button>
    </div>
  </div>
  <div class="cb">
    <div id="plist" class="plist"><div class="empty-note">Select a site and wait for the client to report participants</div></div>
    <div class="padd">
      <input type="text" id="padd-in" placeholder="Manually add caller name…" onkeydown="if(event.key==='Enter')addManual()">
      <button class="btn bg bs" onclick="addManual()">+ Add</button>
    </div>
  </div>
</div>

<!-- ── Saved Meetings (admin) ─────────────────────────────────────────────── -->
<div class="card">
  <div class="ch">📋 Saved Meetings
    <div class="ch-r">
      <a href="/hub/vmixcaller/presenter" class="btn bw bs" target="_blank">🖥 Open Presenter View</a>
    </div>
  </div>
  <div class="cb">
    <p style="font-size:12px;color:var(--mu);margin-bottom:10px">
      These appear on the Presenter View so the presenter can join with one click.
    </p>
    <div id="mtg-admin-list"></div>
    <hr class="sep">
    <p class="fl" style="margin-bottom:8px">Add Meeting</p>
    <div class="add-mtg">
      <div class="field" style="margin-bottom:0">
        <label class="fl">Name</label>
        <input type="text" id="new-mtg-name" placeholder="Morning Standup">
      </div>
      <div class="field" style="margin-bottom:0">
        <label class="fl">Meeting ID</label>
        <input type="text" id="new-mtg-id" placeholder="123 456 7890">
      </div>
      <div class="field" style="margin-bottom:0">
        <label class="fl">Passcode (optional)</label>
        <input type="password" id="new-mtg-pass" placeholder="••••••">
      </div>
      <button class="btn bp" style="align-self:flex-end" onclick="addMeeting()">+ Add</button>
    </div>
  </div>
</div>

</main>
<script nonce="{{csp_nonce()}}">
""" + _JS_HELPERS + r"""

// ── Hub-specific additions ────────────────────────────────────────────────────

function setStatus(state,text,ts){
  document.getElementById('vdot').className='dot '+(state==='ok'?'dok':state==='warn'?'dwn':'dal');
  document.getElementById('vstatus').textContent=text;
  var ago=document.getElementById('vago');
  if(ago)ago.textContent=ts?'('+_ago(ts)+')':'';
}

function saveConfig(){
  var site  = document.getElementById('target-site').value;
  var ip    = document.getElementById('vmix-ip').value.trim()||'127.0.0.1';
  var port  = parseInt(document.getElementById('vmix-port').value)||8088;
  var inp   = parseInt(document.getElementById('vmix-input').value)||1;
  var url   = document.getElementById('bridge-url').value.trim();
  _post('/api/vmixcaller/config',{target_site:site,vmix_ip:ip,vmix_port:port,vmix_input:inp,bridge_url:url})
    .then(function(d){
      if(d.ok){
        var msg=site?'Settings saved \u2014 vMix config sent to '+site:'Settings saved';
        showMsg(msg,true);
        initPreview(url);
        if(site)loadState();
      } else showMsg(d.error||'Save failed',false);
    }).catch(function(e){showMsg('Error: '+e,false);});
}

function joinManual(){
  var site=document.getElementById('target-site').value;
  if(!site){showMsg('Select a site first',false);return;}
  var mid =(document.getElementById('mtg-id').value||'').trim();
  var pass=(document.getElementById('mtg-pass').value||'').trim();
  var name=(document.getElementById('mtg-name').value||'Guest Producer').trim();
  joinWith(mid,pass,name);
}

// Override sendCmd to check site selection first
var _baseSendCmd=sendCmd;
sendCmd=function(fn,value){
  var site=document.getElementById('target-site');
  if(site&&!site.value){showMsg('Select a site first',false);return Promise.resolve({ok:false});}
  return _baseSendCmd(fn,value);
};

// ── State polling ─────────────────────────────────────────────────────────────
var _statePollT=null;
function loadState(){
  clearTimeout(_statePollT);
  fetch('/api/vmixcaller/state',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      var site=(document.getElementById('target-site')||{}).value||'';
      if(!site){setStatus('off','No site selected',0);return;}
      if(!d.has_data){setStatus('warn','Waiting for '+site+' to report\u2026',0);}
      else if(d.ok){
        var addr=d.vmix_ip?'  \u2014  vMix at '+d.vmix_ip+':'+d.vmix_port:'';
        setStatus('ok',site+(d.version?' \u2014 vMix v'+d.version:' \u2014 vMix connected')+addr,d.ts);
        // Update IP/port fields with what the client is actually using
        var ipEl=document.getElementById('vmix-ip');
        var ptEl=document.getElementById('vmix-port');
        var repEl=document.getElementById('vmix-ip-reported');
        if(d.vmix_ip&&ipEl&&ipEl.value!==d.vmix_ip){
          if(repEl)repEl.textContent='\u2714 Site reporting: '+d.vmix_ip+':'+d.vmix_port;
        } else if(repEl){repEl.textContent='';}
      } else {
        setStatus('al',site+' \u2014 vMix unreachable: '+(d.error||''),d.ts);
      }
      renderParticipants(d.participants||[]);
      var pago=document.getElementById('parts-ago');
      if(pago&&d.ts)pago.textContent='updated '+_ago(d.ts);
    }).catch(function(){setStatus('off','State poll failed',0);});
  _statePollT=setTimeout(loadState,8000);
}

// ── Participants ──────────────────────────────────────────────────────────────
var _parts=[];
function renderParticipants(list){
  _parts=list;
  var el=document.getElementById('plist');
  if(!el)return;
  if(!list||!list.length){el.innerHTML='<div class="empty-note">No participants yet</div>';return;}
  el.innerHTML=list.map(function(p){
    var safe=_esc(p.name);
    var b=(p.muted?'<span class="pbadge muted">MUTED</span>':'')+(p.active?'<span class="pbadge air">ON AIR</span>':'');
    return '<div class="pi"><span class="pn">'+safe+'</span>'+b+'<button class="btn bw bs" data-name="'+safe+'" onclick="putOnAir(this)">\uD83D\uDCFA Put On Air</button></div>';
  }).join('');
}
function addManual(){var inp=document.getElementById('padd-in');var n=inp.value.trim();if(!n)return;if(!_parts.some(function(p){return p.name===n;})){_parts.push({name:n,muted:false,active:false});renderParticipants(_parts);}inp.value='';}
function putOnAir(btn){
  sendCmd('ZoomSelectParticipantByName',btn.dataset.name)
    .then(function(d){if(d.ok)showMsg('"'+btn.dataset.name+'" queued for air',true);});
}

// ── Saved meetings admin ──────────────────────────────────────────────────────
var _meetings=[];
function loadMeetings(){
  fetch('/api/vmixcaller/meetings',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){_meetings=d.meetings||[];renderMeetingsAdmin();})
    .catch(function(){});
}
function renderMeetingsAdmin(){
  var el=document.getElementById('mtg-admin-list');if(!el)return;
  if(!_meetings.length){el.innerHTML='<div class="empty-note" style="margin-bottom:0">No saved meetings yet</div>';return;}
  el.innerHTML=_meetings.map(function(m,i){
    return '<div class="mtg-admin-row">'
      +'<div><div class="mtg-name">'+_esc(m.name)+'</div>'
      +'<div class="mtg-id">ID: '+_esc(m.id)+(m.pass?' &nbsp;&middot;&nbsp; Passcode set':'')+'</div></div>'
      +'<button class="btn bp bs" data-idx="'+i+'" onclick="joinSavedAdmin(this)">&#128222; Join</button>'
      +'<button class="btn bd bs" data-idx="'+i+'" onclick="deleteMeeting(this)">&#x2715;</button>'
      +'</div>';
  }).join('');
}
function joinSavedAdmin(btn){
  var m=_meetings[parseInt(btn.dataset.idx)];if(!m)return;
  var site=document.getElementById('target-site');
  if(site&&!site.value){showMsg('Select a site first',false);return;}
  joinWith(m.id,m.pass,m.display_name||'Guest Producer');
}
function addMeeting(){
  var name=document.getElementById('new-mtg-name').value.trim();
  var id=document.getElementById('new-mtg-id').value.trim();
  var pass=document.getElementById('new-mtg-pass').value.trim();
  if(!name||!id){showMsg('Name and Meeting ID are required',false);return;}
  _post('/api/vmixcaller/meetings',{name:name,id:id,pass:pass,display_name:''})
    .then(function(d){
      if(d.ok){
        document.getElementById('new-mtg-name').value='';
        document.getElementById('new-mtg-id').value='';
        document.getElementById('new-mtg-pass').value='';
        loadMeetings();showMsg('Meeting saved',true);
      } else showMsg(d.error||'Save failed',false);
    }).catch(function(e){showMsg('Error: '+e,false);});
}
function deleteMeeting(btn){
  _del('/api/vmixcaller/meetings/'+btn.dataset.idx)
    .then(function(d){if(d.ok)loadMeetings();else showMsg(d.error||'Delete failed',false);})
    .catch(function(e){showMsg('Error: '+e,false);});
}

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded',function(){
  initPreview(document.getElementById('bridge-url').value.trim());
  if(document.getElementById('target-site').value) loadState();
  loadMeetings();
});
</script>
</body>
</html>"""


# ── Client config page ─────────────────────────────────────────────────────────
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
      This is a client node. The hub operator controls meetings from the hub;
      commands are executed here against local vMix. vMix IP and port can be
      set here or pushed remotely from the hub operator page.
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
      <button class="btn bg" onclick="testLocal()">🔌 Test vMix</button>
    </div>
    <div id="test-result" style="margin-top:10px;font-size:12px;color:var(--mu)"></div>
  </div>
</div>
<div class="card">
  <div class="ch">📡 Hub Connection</div>
  <div class="cb" style="font-size:12px;color:var(--mu)">
    <p>Hub: <strong style="color:var(--tx)">{{hub_url|e}}</strong></p>
    <p style="margin-top:6px">Polling every 3 s for commands. Participants reported every ~12 s.</p>
    <p style="margin-top:6px">The hub operator can push vMix IP/port updates from the hub settings page.</p>
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
    el.textContent=d.ok?'\u2713 vMix reachable \u2014 version '+d.version:'\u2717 Cannot reach vMix: '+(d.error||'unknown');
  }).catch(function(e){document.getElementById('test-result').textContent='Error: '+e;});
}
</script>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════════════════════
# Plugin registration
# ══════════════════════════════════════════════════════════════════════════════

def register(app, ctx):
    login_required  = ctx["login_required"]
    csrf_protect    = ctx["csrf_protect"]
    monitor         = ctx["monitor"]
    hub_server      = ctx["hub_server"]

    cfg_ss    = monitor.app_cfg
    mode      = getattr(getattr(cfg_ss, "hub", None), "mode", "standalone") or "standalone"
    hub_url   = (getattr(getattr(cfg_ss, "hub", None), "hub_url", "") or "").rstrip("/")
    is_hub    = mode in ("hub", "both")
    is_client = mode == "client" and bool(hub_url)

    # ── Client polling thread ─────────────────────────────────────────────────
    if mode in ("client", "both") and hub_url:
        threading.Thread(
            target=_start_client_thread, args=(monitor, hub_url),
            daemon=True, name="VmixCallerClient",
        ).start()

    # ── Video relay thread (client only — pushes HLS segments to hub) ─────────
    # Only start on pure client nodes.  Hub/both nodes are on the same machine
    # as (or same LAN as) the bridge, so the proxy route reaches it directly.
    if mode == "client" and hub_url:
        threading.Thread(
            target=_video_relay_loop, args=(monitor, hub_url),
            daemon=True, name="VmixCallerVideoRelay",
        ).start()

    # ── Operator / hub page ───────────────────────────────────────────────────
    @app.get("/hub/vmixcaller")
    @login_required
    def vmixcaller_page():
        cfg = _load_cfg()
        if is_hub:
            with hub_server._lock:
                sites = sorted(
                    n for n, d in hub_server._sites.items()
                    if d.get("_approved")
                )
            return render_template_string(_HUB_TPL, cfg=cfg, sites=sites)
        if is_client:
            return render_template_string(_CLIENT_TPL, cfg=cfg, hub_url=hub_url)
        # Standalone
        return render_template_string(_HUB_TPL, cfg=cfg, sites=[])

    # ── Presenter page ────────────────────────────────────────────────────────
    @app.get("/hub/vmixcaller/presenter")
    @login_required
    def vmixcaller_presenter():
        cfg      = _load_cfg()
        meetings = cfg.get("saved_meetings") or []
        bridge   = (cfg.get("bridge_url") or "").strip()
        return render_template_string(
            _PRESENTER_TPL,
            cfg=cfg,
            meetings=meetings,
            bridge_url=bridge,
            bridge_url_json=json.dumps(bridge),
        )

    # ── Authenticated HLS proxy / relay server (/hub/vmixcaller/video/<path>) ──
    # Registered on ALL nodes (hub and client).
    #
    # Hub with LAN bridge (internet hub, LAN bridge):
    #   • Requests for seg/N.ts      → served from the in-memory relay buffer
    #     (client pushed the segment via /api/vmixcaller/video_push)
    #   • Requests for *.m3u8        → synthetic manifest referencing buffered segs
    #   • Anything else              → 503 (not reachable)
    #
    # Hub with localhost bridge (bridge running on same machine as hub):
    #   • All requests proxied directly to the local SRS server.
    #
    # Client node (any bridge URL):
    #   • All requests proxied directly — client is on the same LAN as bridge.
    @app.get("/hub/vmixcaller/video/<path:seg>")
    @login_required
    def vmixcaller_video_proxy(seg):
        cfg      = _load_cfg()
        base_url = (cfg.get("bridge_url") or "").strip()
        if not base_url:
            return Response("no bridge configured", status=404,
                            content_type="text/plain")

        parsed = urlparse(base_url)
        host   = (parsed.hostname or "").lower()
        lan_bridge = is_hub and host not in ("127.0.0.1", "localhost", "::1")

        # ── Hub + LAN bridge: serve from relay buffer ─────────────────────────
        if lan_bridge:
            # Buffered TS segment: path is "seg/N.ts"
            if seg.startswith("seg/") and seg.endswith(".ts"):
                try:
                    seq = int(seg[4:-3])
                except ValueError:
                    return Response("", status=404)
                with _video_lock:
                    sd = _video_segments.get(seq)
                if sd:
                    return Response(sd["data"], content_type="video/mp2t",
                                    headers={"Cache-Control": "no-cache"})
                return Response("", status=404)

            # Manifest: generate synthetic HLS from buffer
            if seg.endswith(".m3u8"):
                with _video_lock:
                    if not _video_segments:
                        # No segments yet — relay hasn't started or bridge is off
                        return Response("", status=503, content_type="text/plain")
                    seqs  = sorted(_video_segments.keys())
                    lines = [
                        "#EXTM3U",
                        "#EXT-X-VERSION:3",
                        "#EXT-X-TARGETDURATION:3",
                        f"#EXT-X-MEDIA-SEQUENCE:{seqs[0]}",
                    ]
                    for s in seqs:
                        d = _video_segments[s]
                        lines.append(f"#EXTINF:{d['duration']:.3f},")
                        lines.append(f"/hub/vmixcaller/video/seg/{s}.ts")
                return Response(
                    "\n".join(lines) + "\n",
                    content_type="application/vnd.apple.mpegurl",
                    headers={"Cache-Control": "no-cache"},
                )

            # Unknown path for LAN bridge
            return Response("use seg/N.ts or caller.m3u8 path", status=404,
                            content_type="text/plain")

        # ── Direct proxy (localhost bridge or client node) ────────────────────
        try:
            proxy_url = f"{parsed.scheme}://{parsed.netloc}/{seg}"
            req  = urllib.request.Request(proxy_url, method="GET")
            resp = urllib.request.urlopen(req, timeout=5)
            data = resp.read()
            ct   = resp.headers.get("Content-Type", "application/octet-stream")
            if seg.endswith(".m3u8"):
                ct = "application/vnd.apple.mpegurl"
            return Response(data, content_type=ct,
                            headers={"Cache-Control": "no-cache"})
        except urllib.error.HTTPError as e:
            return Response("", status=e.code)
        except Exception:
            return Response("", status=502)

    # ── Client pushes HLS segment to hub relay buffer ─────────────────────────
    @app.post("/api/vmixcaller/video_push")
    def vmixcaller_video_push():
        site = request.headers.get("X-Site", "").strip()
        if not site:
            return jsonify({"error": "missing X-Site"}), 400
        with hub_server._lock:
            approved = hub_server._sites.get(site, {}).get("_approved")
        if not approved:
            return jsonify({"error": "not approved"}), 403
        data = request.get_data()
        if not data:
            return jsonify({"ok": True})
        try:
            duration = float(request.headers.get("X-Seg-Duration", "2.0"))
        except (ValueError, TypeError):
            duration = 2.0
        with _video_lock:
            seq = _video_seq[0]
            _video_seq[0] += 1
            _video_segments[seq] = {"data": data, "duration": duration}
            # Trim oldest segments
            while len(_video_segments) > _VIDEO_MAX_SEGS:
                del _video_segments[min(_video_segments.keys())]
        return jsonify({"ok": True})

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
            cfg = _save_cfg(data)
            # If on hub and a target site is selected, push vMix connection
            # settings to the client node via the command channel.
            target = (cfg.get("target_site") or "").strip()
            if is_hub and target and ("vmix_ip" in data or "vmix_port" in data):
                with _state_lock:
                    _pending_cmd[target] = {
                        "fn":        "__set_config__",
                        "vmix_ip":   cfg.get("vmix_ip",  "127.0.0.1"),
                        "vmix_port": cfg.get("vmix_port", 8088),
                        "seq":       int(time.time() * 1000),
                    }
            return jsonify({"ok": True, "config": cfg})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    # ── Saved meetings ────────────────────────────────────────────────────────
    @app.get("/api/vmixcaller/meetings")
    @login_required
    def vmixcaller_get_meetings():
        return jsonify({"meetings": _load_cfg().get("saved_meetings", [])})

    @app.post("/api/vmixcaller/meetings")
    @login_required
    @csrf_protect
    def vmixcaller_add_meeting():
        data = request.get_json(silent=True) or {}
        name = str(data.get("name", "") or "").strip()
        mid  = str(data.get("id",   "") or "").strip()
        if not name or not mid:
            return jsonify({"ok": False, "error": "Name and Meeting ID are required"}), 400
        entry = {
            "name":         name,
            "id":           mid,
            "pass":         str(data.get("pass",         "") or "").strip(),
            "display_name": str(data.get("display_name", "") or "").strip(),
        }
        cfg = _load_cfg()
        meetings = list(cfg.get("saved_meetings") or [])
        meetings.append(entry)
        _save_cfg({"saved_meetings": meetings})
        return jsonify({"ok": True})

    @app.route("/api/vmixcaller/meetings/<int:idx>", methods=["DELETE"])
    @login_required
    @csrf_protect
    def vmixcaller_delete_meeting(idx):
        cfg      = _load_cfg()
        meetings = list(cfg.get("saved_meetings") or [])
        if not 0 <= idx < len(meetings):
            return jsonify({"ok": False, "error": "Index out of range"}), 400
        meetings.pop(idx)
        _save_cfg({"saved_meetings": meetings})
        return jsonify({"ok": True})

    # ── Queue command for site ────────────────────────────────────────────────
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
        if not is_hub:
            # Standalone: execute directly
            if not cfg.get("vmix_ip"):
                return jsonify({"ok": False, "error": "No vMix IP configured"})
            ok, resp = _vmix_fn(cfg, fn, value=data.get("value"), inp=data.get("input"))
            return jsonify({"ok": ok,
                            "response": resp[:200] if ok else None,
                            "error":    resp if not ok else None})
        if not target_site:
            return jsonify({"ok": False, "error": "No target site selected — save settings first"}), 400
        with _state_lock:
            _pending_cmd[target_site] = {
                "fn":    fn,
                "value": data.get("value"),
                "input": data.get("input") or cfg.get("vmix_input", 1),
                "seq":   int(time.time() * 1000),
            }
        return jsonify({"ok": True, "queued_for": target_site})

    # ── Client polls for pending command ──────────────────────────────────────
    @app.get("/api/vmixcaller/cmd")
    @login_required
    def vmixcaller_cmd_poll():
        site = request.headers.get("X-Site", "").strip()
        if not site:
            return jsonify({"error": "missing X-Site"}), 400
        with hub_server._lock:
            approved = hub_server._sites.get(site, {}).get("_approved")
        if not approved:
            return jsonify({"error": "not approved"}), 403
        with _state_lock:
            cmd = _pending_cmd.pop(site, None)
        return jsonify({"cmd": cmd} if cmd else {})

    # ── Client reports status / participants ──────────────────────────────────
    @app.post("/api/vmixcaller/report")
    def vmixcaller_report():
        site = request.headers.get("X-Site", "").strip()
        if not site:
            return jsonify({"error": "missing X-Site"}), 400
        with hub_server._lock:
            approved = hub_server._sites.get(site, {}).get("_approved")
        if not approved:
            return jsonify({"error": "not approved"}), 403
        data = request.get_json(silent=True) or {}
        data["ts"]       = data.get("ts") or time.time()
        data["has_data"] = True
        with _state_lock:
            _site_status[site] = data
        return jsonify({"ok": True})

    # ── Hub browser polls latest client status ────────────────────────────────
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

    # ── Client: local vMix test ───────────────────────────────────────────────
    @app.get("/api/vmixcaller/test_local")
    @login_required
    def vmixcaller_test_local():
        cfg = _load_cfg()
        ok, xml_text = _vmix_xml(cfg)
        if ok:
            return jsonify({"ok": True, "version": _vmix_version(xml_text)})
        return jsonify({"ok": False, "error": xml_text[:120]})
