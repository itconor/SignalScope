#!/usr/bin/env python3
"""
vMix Caller Manager — SignalScope plugin v1.5.2

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
    "version": "1.5.23",
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
_pending_cmd: dict  = {}   # site_name → {fn, value, input, seq}
_site_status: dict  = {}   # site_name → {ok, version, participants, error, ts}
_relay_wanted: dict = {}   # site_name → bool (hub wants this site's video relay)
_state_lock          = threading.Lock()

# ── Client-side relay gate ─────────────────────────────────────────────────────
# Set by the client thread when hub sends relay:"start"; cleared on "stop".
# _video_relay_loop blocks on this so it only runs on demand.
_relay_event = threading.Event()

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


def _compute_video_url(cfg: dict, is_hub_node: bool) -> str:
    """Return the ready-to-use video URL for the browser to request.

    Two preview modes are detected automatically from the URL format:

    HLS mode  (.m3u8 URL):
      Hub with localhost bridge → proxied via /hub/vmixcaller/video/<path>
      Hub with LAN bridge or no bridge → relay buffer (relay.m3u8)
      Client node → proxied via /hub/vmixcaller/video/<path>

    WebRTC / iframe mode  (any http(s):// URL that isn't .m3u8 — e.g. the
      SRS player page at /players/rtc_player.html?...):
      Client node → direct URL (SRS is on the same LAN, browser can reach it)
      Hub node with localhost bridge → direct URL
      Hub node with LAN bridge → "" (browser on remote hub can't reach LAN SRS)
    """
    bridge = (cfg.get("bridge_url") or "").strip()

    low = bridge.lower()
    # Detect mode
    is_hls     = low.split("?")[0].endswith(".m3u8")
    is_webrtc  = low.startswith("webrtc://")

    def _is_local(url: str) -> bool:
        try:
            h = (urlparse(url).hostname or "").lower()
            return h in ("127.0.0.1", "localhost", "::1")
        except Exception:
            return False

    def _is_local_webrtc(url: str) -> bool:
        try:
            return url.split("//")[1].split("/")[0].lower() in (
                "127.0.0.1", "localhost", "::1")
        except Exception:
            return False

    if is_hls:
        if is_hub_node:
            if bridge and _is_local(bridge):
                path = urlparse(bridge).path or "/live/caller.m3u8"
                if not path.startswith("/"):
                    path = "/" + path
                return "/hub/vmixcaller/video" + path
            # LAN bridge or no bridge — serve from relay buffer
            return "/hub/vmixcaller/video/relay.m3u8"
        else:
            try:
                path = urlparse(bridge).path or "/live/caller.m3u8"
                if not path.startswith("/"):
                    path = "/" + path
                return "/hub/vmixcaller/video" + path
            except Exception:
                return ""

    elif is_webrtc:
        # webrtc://host/app/stream  — passed to JS which builds the WHEP URL.
        # Hub nodes can't reach a LAN SRS; only return for localhost or client.
        if is_hub_node and not _is_local_webrtc(bridge):
            return ""
        return bridge

    else:
        # http(s):// player page → iframe in browser.
        # Hub nodes can't reach a LAN SRS; only return for localhost or client.
        if is_hub_node and not _is_local(bridge):
            return ""
        return bridge


# ── vMix API helpers (executed on the CLIENT side) ────────────────────────────

def _vmix_base(cfg: dict) -> str:
    ip   = (cfg.get("vmix_ip") or "127.0.0.1").strip().rstrip("/")
    port = int(cfg.get("vmix_port") or 8088)
    return f"http://{ip}:{port}/API"


def _vmix_fn(cfg: dict, fn: str, value: str = None, inp: int = None,
             value2: str = None, value3: str = None):
    """Call a vMix API function. Returns (ok: bool, text: str)."""
    params: dict = {"Function": fn}
    input_num = inp or cfg.get("vmix_input")
    if input_num:
        params["Input"] = str(input_num)
    if value  is not None: params["Value"]  = str(value)
    if value2 is not None: params["Value2"] = str(value2)
    if value3 is not None: params["Value3"] = str(value3)
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

            # ── Relay gate: hub tells us whether to run the video relay ──────
            relay = d.get("relay")
            if relay == "start":
                _relay_event.set()
            elif relay == "stop":
                _relay_event.clear()

            cmd = d.get("cmd")
            if cmd and cmd.get("seq") != last_cmd_seq:
                last_cmd_seq = cmd["seq"]
                fn = cmd.get("fn", "")

                if fn == "__set_config__":
                    # Hub is pushing full plugin config — save locally.
                    # Includes bridge_url so the relay thread and presenter page
                    # both have the correct value without manual client entry.
                    updates = {}
                    for _k in ("vmix_ip", "vmix_port", "vmix_input", "bridge_url"):
                        if _k in cmd:
                            updates[_k] = cmd[_k]
                    if updates:
                        _save_cfg(updates)
                        cfg = _load_cfg()   # reload so next iteration uses new values
                else:
                    ok, resp = _vmix_fn(cfg, fn,
                                        value=cmd.get("value"), inp=cmd.get("input"),
                                        value2=cmd.get("value2"), value3=cmd.get("value3"))
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
                    "ts":          now,
                    "ok":          ok_xml,
                    "vmix_ip":     cfg.get("vmix_ip",  "127.0.0.1"),
                    "vmix_port":   cfg.get("vmix_port", 8088),
                    "relay_active": _relay_event.is_set(),
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
        if not _relay_event.is_set():
            time.sleep(2)
            continue
        try:
            cfg        = _load_cfg()
            bridge_url = (cfg.get("bridge_url") or "").strip()
            app_cfg    = monitor.app_cfg
            site       = (getattr(getattr(app_cfg, "hub", None), "site_name", "") or "").strip()
            secret     = (getattr(getattr(app_cfg, "hub", None), "secret_key",  "") or "").strip()

            if not bridge_url or not site:
                time.sleep(5)
                continue

            # Derive the HLS manifest URL from bridge_url.
            # webrtc://HOST/APP/STREAM  →  SRS HLS: http://HOST:8080/APP/STREAM.m3u8
            # http(s)://HOST/path.m3u8  →  use as-is (existing HLS bridge)
            low_bridge = bridge_url.lower()
            if low_bridge.startswith("webrtc://"):
                try:
                    rest          = bridge_url.split("//", 1)[1]
                    whost, wpath  = rest.split("/", 1)
                    base          = f"http://{whost}:8080"
                    manifest_url  = f"{base}/{wpath}.m3u8"
                    parsed        = urlparse(manifest_url)   # needed for manifest_dir below
                except Exception:
                    time.sleep(5)
                    continue
            elif low_bridge.startswith("http"):
                parsed       = urlparse(bridge_url)
                base         = f"{parsed.scheme}://{parsed.netloc}"
                manifest_url = base + parsed.path
            else:
                time.sleep(5)
                continue

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
#pvframe{position:absolute;inset:0;width:100%;height:100%;border:0;display:none;background:#000}
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
// Three modes detected from purl:
//   webrtc://host/app/stream  → native WHEP (RTCPeerConnection into <video>)
//   http(s)://...             → iframe (SRS player page or other web player)
//   /hub/... path             → HLS proxied via SignalScope (hls.js / native)
// Separate <audio> element used by toggleAudio() to play caller audio independently
// of the <video> element's mute state — avoids autoplay-policy issues with vid.muted.
var _hearAudio=null;
function _teardownPreview(vid){
  if(!vid)return;
  if(vid._pc){try{vid._pc.close();}catch(e){}vid._pc=null;}
  if(vid._hls){try{vid._hls.destroy();}catch(e){}vid._hls=null;}
  vid.srcObject=null;vid.src='';
  // Also stop any active audio output when the stream tears down.
  if(_hearAudio){try{_hearAudio.pause();_hearAudio.srcObject=null;}catch(e){}_hearAudio=null;}
}
// _syncAudioBtn / toggleAudio are shared across all three pages.
// Presenter page redefines _syncAudioBtn below to use its .call-btn.muted styling.
function _syncAudioBtn(){
  var btn=document.getElementById('audio-btn');
  if(!btn)return;
  if(_hearAudio){
    btn.textContent='\uD83D\uDD0A Hearing Caller';
    btn.classList.remove('muted');btn.classList.remove('bg');btn.classList.add('bp');
    btn.title='Click to mute caller audio';
  } else {
    btn.textContent='\uD83D\uDD07 Hear Caller';
    btn.classList.remove('bp');btn.classList.add('bg');btn.classList.add('muted');
    btn.title='Click to hear caller audio through this page';
  }
}
function toggleAudio(){
  var vid=document.getElementById('pvid');
  if(!vid)return;
  if(_hearAudio){
    // Stop audio
    try{_hearAudio.pause();_hearAudio.srcObject=null;}catch(e){}
    _hearAudio=null;
  } else {
    // Start audio — called inside a user-gesture click handler so play() is allowed.
    // Use a dedicated <audio> element so we're completely independent of vid.muted state.
    var ms=vid.srcObject;
    var tracks=ms?ms.getAudioTracks():[];
    if(tracks.length>0){
      var ams=new MediaStream(tracks);
      _hearAudio=new Audio();
      _hearAudio.srcObject=ams;
      _hearAudio.play().catch(function(e){console.warn('hear caller audio failed:',e);_hearAudio=null;});
    } else {
      console.warn('toggleAudio: no audio tracks in stream yet');
    }
  }
  _syncAudioBtn();
}
function _startWhep(whepUrl,vid,ov,msg){
  ov.classList.remove('hidden');if(msg)msg.textContent='Connecting (WebRTC)\u2026';
  vid.style.display='block';
  var pc=new RTCPeerConnection({iceServers:[]});
  vid._pc=pc;
  pc.addTransceiver('audio',{direction:'recvonly'});
  pc.addTransceiver('video',{direction:'recvonly'});
  // Use a single owned MediaStream and addTrack() each arriving track.
  // Relying on e.streams[0] fails when the server sends no MSID in the SDP answer
  // (common with SRS/vMix) — in that case e.streams is empty for the audio track
  // and vid.srcObject ends up pointing to a video-only stream with no audio attached.
  var _ms=new MediaStream();
  vid.srcObject=_ms;
  // Set muted via JS (not the HTML attribute) so defaultMuted stays false.
  // The HTML 'muted' attribute makes defaultMuted=true; any subsequent play()
  // call resets vid.muted back to true, permanently blocking audio unmuting.
  vid.muted=true;
  pc.ontrack=function(e){
    _ms.addTrack(e.track);
    vid.play().catch(function(){});
    if(e.track.kind==='video')ov.classList.add('hidden');
  };
  pc.oniceconnectionstatechange=function(){
    if(pc.iceConnectionState==='failed'||pc.iceConnectionState==='disconnected'){
      ov.classList.remove('hidden');if(msg)msg.textContent='WebRTC connection lost \u2014 is SRS running?';
    }
  };
  pc.createOffer()
    .then(function(o){return pc.setLocalDescription(o);})
    .then(function(){
      return fetch('/api/vmixcaller/whep_proxy?url='+encodeURIComponent(whepUrl),{method:'POST',headers:{'Content-Type':'application/sdp'},body:pc.localDescription.sdp});
    })
    .then(function(r){if(!r.ok)throw new Error('WHEP '+r.status);return r.text();})
    .then(function(sdp){return pc.setRemoteDescription({type:'answer',sdp:sdp});})
    .catch(function(e){ov.classList.remove('hidden');if(msg)msg.textContent='WebRTC error: '+e.message;});
}
function initPreview(purl){
  var vid=document.getElementById('pvid');
  var frm=document.getElementById('pvframe');
  var ov=document.getElementById('pvw-ov');
  var msg=document.getElementById('pvmsg');
  if(!ov)return;
  _teardownPreview(vid);
  if(frm){frm.src='';frm.style.display='none';}
  if(!purl){
    if(vid){vid.style.display='none';}
    ov.classList.remove('hidden');if(msg)msg.textContent='No preview stream configured';return;
  }
  // webrtc://host/app/stream → native WHEP
  var wm=purl.match(/^webrtc:\/\/([^\/]+)\/([^\/]+)\/(.+)$/i);
  if(wm){
    var whepUrl='http://'+wm[1]+':1985/rtc/v1/whep/?app='+encodeURIComponent(wm[2])+'&stream='+encodeURIComponent(wm[3]);
    if(frm){frm.src='';frm.style.display='none';}
    _startWhep(whepUrl,vid,ov,msg);return;
  }
  // http(s):// → iframe (SRS player page or other embedded player)
  if(/^https?:\/\//i.test(purl)){
    if(vid)vid.style.display='none';
    if(frm){frm.src=purl;frm.style.display='block';}
    ov.classList.add('hidden');return;
  }
  // /hub/... → HLS
  if(vid)vid.style.display='block';
  ov.classList.remove('hidden');if(msg)msg.textContent='Connecting to stream\u2026';
  if(typeof Hls!=='undefined'&&Hls.isSupported()){
    var hls=new Hls({lowLatencyMode:true,liveSyncDurationCount:2,maxBufferLength:10,xhrSetup:function(xhr){xhr.withCredentials=true;}});
    vid._hls=hls;
    hls.loadSource(purl);hls.attachMedia(vid);
    hls.on(Hls.Events.MANIFEST_PARSED,function(){ov.classList.add('hidden');vid.play().catch(function(){});});
    hls.on(Hls.Events.ERROR,function(ev,data){
      if(data.fatal){ov.classList.remove('hidden');if(msg)msg.textContent='Stream unavailable \u2014 is the bridge running?';}
    });
  } else if(vid&&vid.canPlayType('application/vnd.apple.mpegurl')){
    vid.src=purl;vid.load();
    vid.oncanplay=function(){ov.classList.add('hidden');};
    vid.onerror=function(){ov.classList.remove('hidden');if(msg)msg.textContent='Stream unavailable \u2014 is the bridge running?';};
    vid.play().catch(function(){});
  } else {
    ov.classList.remove('hidden');if(msg)msg.textContent='HLS not supported in this browser';
  }
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

var _lastJoin={mid:'',pass:'',name:''};
function joinWith(mid,pass,name){
  if(!mid){showMsg('Enter a Meeting ID',false);return;}
  _lastJoin={mid:mid,pass:pass||'',name:name||'Guest Producer'};
  if(typeof _updateReconnectBtn==='function')_updateReconnectBtn();
  sendCmd('ZoomJoinMeeting',mid+'|'+(pass||'')+'|'+(name||'Guest Producer'))
    .then(function(d){if(d.ok){showMsg('Joining\u2026',true);setMeetingState(true);}});
}
function leaveMeeting(){
  sendCmd('ZoomLeaveMeeting').then(function(d){if(d.ok){showMsg('Left meeting',true);setMeetingState(false);}});
}
function reconnect(){
  if(!_lastJoin.mid){showMsg('No previous meeting to reconnect to',false);return;}
  showMsg('Reconnecting\u2026',true);
  sendCmd('ZoomJoinMeeting',_lastJoin.mid+'|'+_lastJoin.pass+'|'+_lastJoin.name)
    .then(function(d){if(d.ok)setMeetingState(true);});
}
function muteSelf(){
  sendCmd('ZoomMuteSelf').then(function(d){
    if(d.ok){_selfMuted=!_selfMuted;var b=document.getElementById('mute-btn');if(b)b.textContent=_selfMuted?'\uD83D\uDD0A Unmute Self':'\uD83D\uDD07 Mute Self';showMsg(_selfMuted?'Muted':'Unmuted',true);}
  });
}
function stopCamera(){
  // Correct vMix API names: ZoomStopVideo / ZoomStartVideo
  var fn=_camOff?'ZoomStartVideo':'ZoomStopVideo';
  sendCmd(fn).then(function(d){
    if(d.ok){_camOff=!_camOff;var b=document.getElementById('cam-btn');if(b)b.textContent=_camOff?'\uD83D\uDCF7 Start Camera':'\uD83D\uDCF7 Stop Camera';showMsg(_camOff?'Camera off':'Camera on',true);}
  });
}
function muteAll(){sendCmd('ZoomMuteAllParticipants').then(function(d){if(d.ok)showMsg('All guests muted',true);});}

// ── Keyboard shortcuts ────────────────────────────────────────────────────────
document.addEventListener('keydown',function(e){
  var t=(document.activeElement||{}).tagName||'';
  if(t==='INPUT'||t==='TEXTAREA'||t==='SELECT')return;
  if(e.key==='m'||e.key==='M'){e.preventDefault();muteSelf();}
  if(e.key==='c'||e.key==='C'){e.preventDefault();stopCamera();}
});

// ── Delegated click handler (CSP-safe — replaces all onclick= attributes) ─────
// All buttons use data-action= instead of onclick= so no CSP hash is required.
// Functions guarded with typeof so hub-only / presenter-only actions don't error.
document.addEventListener('click',function(e){
  var btn=e.target.closest('[data-action]');
  if(!btn)return;
  var a=btn.dataset.action;
  if(a==='muteSelf')                                          muteSelf();
  else if(a==='stopCamera')                                   stopCamera();
  else if(a==='leaveMeeting')                                 leaveMeeting();
  else if(a==='muteAll')                                      muteAll();
  else if(a==='hangUp'      &&typeof hangUp==='function')     hangUp();
  else if(a==='toggleAudio' &&typeof toggleAudio==='function')toggleAudio();
  else if(a==='joinSaved'   &&typeof joinSaved==='function')  joinSaved(btn);
  else if(a==='toggleManual'&&typeof toggleManual==='function')toggleManual();
  else if(a==='joinManual'  &&typeof joinManual==='function') joinManual();
  else if(a==='saveConfig'  &&typeof saveConfig==='function') saveConfig();
  else if(a==='loadState'   &&typeof loadState==='function')  loadState();
  else if(a==='addManual'   &&typeof addManual==='function')  addManual();
  else if(a==='addMeeting'  &&typeof addMeeting==='function') addMeeting();
  else if(a==='putOnAir'    &&typeof putOnAir==='function')   putOnAir(btn);
  else if(a==='joinSavedAdmin'&&typeof joinSavedAdmin==='function')joinSavedAdmin(btn);
  else if(a==='deleteMeeting'&&typeof deleteMeeting==='function')deleteMeeting(btn);
  else if(a==='toggleRelay'  &&typeof toggleRelay==='function')  toggleRelay();
  else if(a==='testVmix'    &&typeof testVmix==='function')     testVmix();
  else if(a==='reconnect'   &&typeof reconnect==='function')    reconnect();
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
<link rel="icon" type="image/x-icon" href="/static/signalscope_icon.png">
<script nonce="{{csp_nonce()}}" src="https://cdn.jsdelivr.net/npm/hls.js@1/dist/hls.min.js" defer></script>
<style nonce="{{csp_nonce()}}">
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(circle at top,#12376f 0%,var(--bg) 38%,#05101f 100%);color:var(--tx);font-family:system-ui,sans-serif;font-size:14px;min-height:100vh}

/* ── Header — no nav links, presenter-only ──────────────────────────── */
.hdr{background:linear-gradient(180deg,rgba(10,31,65,.97),rgba(9,24,48,.97));border-bottom:1px solid var(--bor);padding:14px 24px;display:flex;align-items:center;gap:14px;position:sticky;top:0;z-index:50;backdrop-filter:blur(8px)}
.hdr-logo{font-size:22px;flex-shrink:0}
.hdr-title{font-size:17px;font-weight:700;letter-spacing:-.02em}
.hdr-sub{font-size:11px;color:var(--mu);margin-top:1px;transition:color .3s,font-weight .3s}
.hdr-sub.on-call{color:var(--ok);font-weight:600}
.hdr-powered{font-size:11px;color:var(--mu);opacity:.45;text-decoration:none;letter-spacing:.04em;transition:opacity .2s;white-space:nowrap;margin-left:auto}
.hdr-powered:hover{opacity:.8}
@media(max-width:500px){.hdr-powered{display:none}}

/* ── In-call bar ─────────────────────────────────────────────────────── */
#call-bar{display:none;margin:16px 24px 0;max-width:1060px;margin-left:auto;margin-right:auto;background:linear-gradient(135deg,rgba(34,197,94,.11),rgba(34,197,94,.06));border:1.5px solid rgba(34,197,94,.35);border-radius:16px;padding:14px 20px;align-items:center;gap:12px;flex-wrap:wrap}
#call-bar.visible{display:flex}
.call-badge{font-size:12px;font-weight:700;padding:4px 14px;border-radius:20px;background:rgba(34,197,94,.16);color:var(--ok);border:1px solid rgba(34,197,94,.3)}
.call-spacer{flex:1}
.call-btn{border:none;border-radius:10px;padding:8px 18px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;background:#0d2346;color:var(--tx);border:1px solid var(--bor);transition:filter .15s,background .2s,color .2s}
.call-btn:hover{filter:brightness(1.2)}
.call-btn.muted{background:rgba(245,158,11,.12);color:var(--wn);border-color:rgba(245,158,11,.35)}
.call-btn.leave{background:rgba(239,68,68,.12);color:var(--al);border-color:rgba(239,68,68,.3)}
.call-btn.leave:hover{background:rgba(239,68,68,.22);filter:none}
.onair-badge{font-size:12px;font-weight:700;padding:4px 14px;border-radius:20px;background:rgba(239,68,68,.18);color:var(--al);border:1px solid rgba(239,68,68,.4);animation:onair-pulse 1.4s ease-in-out infinite}
@keyframes onair-pulse{0%,100%{opacity:1}50%{opacity:.55}}
.caller-waiting{color:var(--ok)!important;font-weight:600!important}

/* ── Main ────────────────────────────────────────────────────────────── */
main{max-width:1060px;margin:0 auto;padding:0 24px 48px}

/* ── Video hero ──────────────────────────────────────────────────────── */
.video-hero{margin-top:20px;border-radius:20px;overflow:hidden;border:1.5px solid var(--bor);background:#000;position:relative;aspect-ratio:16/9;box-shadow:0 8px 32px rgba(0,0,0,.5)}
.video-hero video,.video-hero iframe{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;border:0;display:block}
.pvw-ov{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;color:var(--mu);font-size:13px;text-align:center;padding:30px;background:rgba(0,0,0,.84)}
.pvw-ov.hidden{display:none}
.pvw-icon{font-size:56px;line-height:1}
#pvmsg{font-size:14px;color:var(--mu);line-height:1.5}
.pvw-hint{font-size:12px;color:rgba(138,164,200,.5);margin-top:4px}

/* ── Section heading ─────────────────────────────────────────────────── */
.section{padding:28px 0 0}
.section-title{font-size:12px;font-weight:700;color:var(--mu);text-transform:uppercase;letter-spacing:.1em;margin-bottom:16px;display:flex;align-items:center;gap:8px}
.section-title span{font-weight:400;text-transform:none;letter-spacing:0;color:#4a6080;font-size:11px}

/* ── Meeting cards ───────────────────────────────────────────────────── */
.mtg-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:14px}
.mtg-card{background:var(--sur);border:1.5px solid var(--bor);border-radius:18px;padding:20px;display:flex;flex-direction:column;gap:14px;transition:border-color .2s,box-shadow .2s,transform .15s}
.mtg-card:hover{border-color:rgba(23,168,255,.35);transform:translateY(-2px);box-shadow:0 10px 28px rgba(0,0,0,.4)}
.mtg-top{display:flex;align-items:flex-start;gap:13px}
.mtg-av{width:48px;height:48px;border-radius:13px;display:flex;align-items:center;justify-content:center;font-size:19px;font-weight:800;color:#fff;flex-shrink:0;box-shadow:0 4px 12px rgba(0,0,0,.3);letter-spacing:-.01em}
.mtg-meta{flex:1;min-width:0}
.mtg-name{font-size:15px;font-weight:700;line-height:1.3;margin-bottom:3px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.mtg-id-text{font-size:11px;color:var(--mu)}
.join-big{background:linear-gradient(135deg,#1a7fe8,#17a8ff);border:none;border-radius:12px;color:#fff;font-size:14px;font-weight:700;padding:12px;width:100%;cursor:pointer;font-family:inherit;box-shadow:0 2px 12px rgba(23,168,255,.3);transition:filter .2s,box-shadow .2s;display:flex;align-items:center;justify-content:center;gap:8px}
.join-big:hover{filter:brightness(1.1);box-shadow:0 4px 18px rgba(23,168,255,.5)}
.join-big:disabled{opacity:.4;cursor:not-allowed;filter:none;box-shadow:none}
.no-meetings{background:rgba(23,52,95,.25);border:1px dashed var(--bor);border-radius:16px;padding:36px 28px;text-align:center;color:var(--mu);font-size:13px;line-height:1.7}

/* ── Manual join ─────────────────────────────────────────────────────── */
#manual-section{padding:22px 0 0}
#manual-toggle{background:none;border:none;color:var(--mu);font-size:12px;cursor:pointer;padding:0;font-family:inherit;text-decoration:underline;text-underline-offset:2px;transition:color .2s}
#manual-toggle:hover{color:var(--tx)}
#manual-form{display:none;margin-top:14px}
#manual-form.open{display:block}
.manual-card{background:var(--sur);border:1.5px solid var(--bor);border-radius:18px;padding:22px}
.field{display:flex;flex-direction:column;gap:5px;margin-bottom:14px}
.field:last-child{margin-bottom:0}
.fl{font-size:11px;color:var(--mu);font-weight:600;text-transform:uppercase;letter-spacing:.05em}
input[type=text],input[type=password]{background:#0d1e40;border:1px solid var(--bor);border-radius:9px;color:var(--tx);padding:10px 13px;font-size:14px;font-family:inherit;width:100%;transition:border-color .2s}
input:focus{outline:none;border-color:var(--acc)}
.r2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.join-manual{background:linear-gradient(135deg,#1a7fe8,#17a8ff);border:none;border-radius:12px;color:#fff;font-size:14px;font-weight:700;padding:12px;width:100%;margin-top:16px;cursor:pointer;font-family:inherit;box-shadow:0 2px 12px rgba(23,168,255,.3);transition:filter .2s;display:flex;align-items:center;justify-content:center;gap:8px}
.join-manual:hover{filter:brightness(1.1)}

/* ── Msg ─────────────────────────────────────────────────────────────── */
#msg{display:none;padding:10px 16px;border-radius:10px;margin-top:16px;font-size:13px}
.mok{background:#0f2318;color:var(--ok);border:1px solid #166534}
.mer{background:#2a0a0a;color:var(--al);border:1px solid #991b1b}

/* ── Keyboard hints ──────────────────────────────────────────────────── */
.kbd-hints{padding:18px 0 0;text-align:center;font-size:11px;color:rgba(138,164,200,.4)}
kbd{background:rgba(13,30,64,.8);border:1px solid rgba(23,52,95,.8);border-radius:3px;padding:0 5px;font-size:10px;font-family:monospace}

/* ── Responsive ──────────────────────────────────────────────────────── */
@media(max-width:700px){
  main{padding:0 16px 36px}
  #call-bar{margin:12px 0 0;padding:12px 16px}
  .video-hero{margin-top:14px;border-radius:14px}
  .section{padding:22px 0 0}
  .mtg-grid{grid-template-columns:1fr 1fr}
  .hdr{padding:12px 16px}
}
@media(max-width:440px){.mtg-grid{grid-template-columns:1fr}}
</style>
</head>
<body>

<!-- ── Header — presenter-only, no SignalScope navigation ───────────── -->
<header class="hdr">
  <span class="hdr-logo">📹</span>
  <div>
    <div class="hdr-title">Caller</div>
    <div class="hdr-sub" id="hdr-sub">Ready to join</div>
  </div>
  <a href="/" class="hdr-powered">SignalScope</a>
</header>

<main>
<div id="msg"></div>

<!-- ── In-call bar (hidden until in meeting) ─────────────────────────── -->
<div id="call-bar">
  <span class="call-badge">● ON CALL</span>
  <span id="onair-badge" class="onair-badge" style="display:none">📡 ON AIR</span>
  <button class="call-btn" id="mute-btn"  data-action="muteSelf">🔇 Mute Self</button>
  <button class="call-btn" id="cam-btn"   data-action="stopCamera">📷 Stop Camera</button>
  <div class="call-spacer"></div>
  <button class="call-btn leave" data-action="hangUp">📴 Leave</button>
</div>

<!-- ── Video hero ─────────────────────────────────────────────────────── -->
<div class="video-hero">
  <video id="pvid" autoplay playsinline></video>
  <iframe id="pvframe" src="" allow="autoplay" title="Caller preview" style="display:none"></iframe>
  <div id="pvw-ov" class="pvw-ov">
    <div class="pvw-icon">📷</div>
    <div id="pvmsg">{% if bridge_url %}Waiting for caller…{% else %}No preview stream configured{% endif %}</div>
    {% if not bridge_url %}
    <div class="pvw-hint">Ask your engineer to configure a Preview URL</div>
    {% endif %}
  </div>
</div>
<div style="text-align:center;margin-top:10px;display:flex;justify-content:center;gap:10px;flex-wrap:wrap">
  <button class="call-btn muted" id="audio-btn" data-action="toggleAudio" title="Click to hear caller audio through this page">🔇 Hear Caller</button>
  <button class="call-btn" id="reconnect-btn" data-action="reconnect" style="display:none" title="Rejoin the last meeting">🔄 Reconnect</button>
</div>

<!-- ── Saved meetings ─────────────────────────────────────────────────── -->
{% if meetings %}
<div class="section">
  <div class="section-title">Saved Meetings <span>— one click to join</span></div>
</div>
<div class="mtg-grid">
  {% set av_colors=[['#1a7fe8','#17a8ff'],['#16a047','#22c55e'],['#9333e8','#a855f7'],['#c87f0a','#f59e0b'],['#d91a6e','#ec4899'],['#0d9488','#14b8a6'],['#c2440f','#f97316']] %}
  {% for m in meetings %}
  {% set c = av_colors[loop.index0 % av_colors|length] %}
  <div class="mtg-card">
    <div class="mtg-top">
      <div class="mtg-av" style="background:linear-gradient(135deg,{{c[0]}},{{c[1]}})">{{(m.name[:1])|upper|e}}</div>
      <div class="mtg-meta">
        <div class="mtg-name">{{m.name|e}}</div>
        <div class="mtg-id-text">{{m.id|e}}{% if m.pass %} &nbsp;·&nbsp; Passcode set{% endif %}</div>
      </div>
    </div>
    <button class="join-big join-btn"
            data-mid="{{m.id|e}}"
            data-pass="{{m.pass|e}}"
            data-dname="{{m.display_name|e}}"
            data-action="joinSaved">📞 Join</button>
  </div>
  {% endfor %}
</div>
{% else %}
<div class="section">
  <div class="section-title">Saved Meetings</div>
</div>
<div class="no-meetings">
  No saved meetings yet.<br>
  Your engineer can add them from the hub or client controls page.
</div>
{% endif %}

<!-- ── Manual join ────────────────────────────────────────────────────── -->
<div id="manual-section">
  <button id="manual-toggle" data-action="toggleManual">＋ Join a different meeting…</button>
  <div id="manual-form">
    <div class="manual-card">
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
          <label class="fl">Your Name</label>
          <input type="text" id="mtg-name" placeholder="Guest Producer">
        </div>
      </div>
      <button class="join-manual join-btn" data-action="joinManual">📞 Join Meeting</button>
    </div>
  </div>
</div>

<div class="kbd-hints"><kbd>M</kbd> mute &nbsp;·&nbsp; <kbd>C</kbd> camera</div>

</main>
<script nonce="{{csp_nonce()}}">
""" + _JS_HELPERS + r"""

// ── Presenter-specific ────────────────────────────────────────────────────────
var _videoUrl = {{video_url_json|safe}};

function joinSaved(btn){
  joinWith(btn.dataset.mid, btn.dataset.pass, btn.dataset.dname||'Guest Producer');
}
function joinManual(){
  var mid =(document.getElementById('mtg-id').value||'').trim();
  var pass=(document.getElementById('mtg-pass').value||'').trim();
  var name=(document.getElementById('mtg-name').value||'Guest Producer').trim();
  joinWith(mid, pass, name);
}

// ── Audio toggle — presenter override ────────────────────────────────────────
// toggleAudio() is defined in _JS_HELPERS (shared). Override only _syncAudioBtn
// here so the presenter's .call-btn.muted styling is used for this page's button.
function _syncAudioBtn(){
  var btn=document.getElementById('audio-btn');
  if(!btn)return;
  if(_hearAudio){
    btn.textContent='\uD83D\uDD0A Hearing Caller';
    btn.classList.remove('muted');
    btn.title='Click to mute caller audio preview';
  } else {
    btn.textContent='\uD83D\uDD07 Hear Caller';
    btn.classList.add('muted');
    btn.title='Click to hear caller audio through this page';
  }
}

// ── Reconnect button visibility ───────────────────────────────────────────────
function _updateReconnectBtn(){
  var btn=document.getElementById('reconnect-btn');
  if(btn)btn.style.display=_lastJoin.mid?'':'none';
}

// ── Override setMeetingState to drive presenter-specific UI ───────────────────
var _baseSMS = setMeetingState;
setMeetingState = function(v){
  _baseSMS(v);
  // In-call action bar
  var bar=document.getElementById('call-bar');
  if(bar){if(v)bar.classList.add('visible');else bar.classList.remove('visible');}
  // Header status line — polling will override when caller is waiting
  var sub=document.getElementById('hdr-sub');
  if(sub&&v){sub.className='hdr-sub on-call';sub.textContent='● On call';}
  // Disable all join buttons while in a meeting
  document.querySelectorAll('.join-btn').forEach(function(b){b.disabled=v;});
  // Manual join section — collapse when call starts
  if(v){
    var f=document.getElementById('manual-form');
    var t=document.getElementById('manual-toggle');
    if(f){f.classList.remove('open');}
    if(t){t.textContent='＋ Join a different meeting…';}
  }
  // Sync audio button text whenever call state changes
  _syncAudioBtn();
};

function hangUp(){
  leaveMeeting();
  if(_videoUrl) setTimeout(function(){initPreview(_videoUrl);},1500);
}

function toggleManual(){
  var f=document.getElementById('manual-form');
  var t=document.getElementById('manual-toggle');
  var open=f.classList.toggle('open');
  t.textContent=open?'✕ Cancel':'＋ Join a different meeting…';
}

// ── Presenter state polling ───────────────────────────────────────────────────
// Polls /api/vmixcaller/state every 4 s so the presenter page reacts to:
//   • A new caller joining (chime + flash + header update)
//   • A caller going ON AIR in vMix (pulsing red ON AIR badge)
//   • All callers leaving (header resets to Ready to join)
// The presenter never polls when there's no data (no site configured on hub).

var _presParts=[], _presPollT=null;

function _presChime(){
  try{
    var ac=new(window.AudioContext||window.webkitAudioContext)();
    var osc=ac.createOscillator(), gain=ac.createGain();
    osc.connect(gain); gain.connect(ac.destination);
    osc.frequency.value=880; gain.gain.value=0;
    gain.gain.setValueAtTime(0,ac.currentTime);
    gain.gain.linearRampToValueAtTime(0.25,ac.currentTime+0.04);
    gain.gain.exponentialRampToValueAtTime(0.001,ac.currentTime+0.5);
    osc.start(ac.currentTime); osc.stop(ac.currentTime+0.55);
    // Second tone after brief gap
    var osc2=ac.createOscillator(), g2=ac.createGain();
    osc2.connect(g2); g2.connect(ac.destination);
    osc2.frequency.value=1100; g2.gain.value=0;
    g2.gain.setValueAtTime(0,ac.currentTime+0.18);
    g2.gain.linearRampToValueAtTime(0.2,ac.currentTime+0.22);
    g2.gain.exponentialRampToValueAtTime(0.001,ac.currentTime+0.65);
    osc2.start(ac.currentTime+0.18); osc2.stop(ac.currentTime+0.7);
  }catch(e){}
}

function _presFlash(){
  var h=document.querySelector('.video-hero');
  if(!h)return;
  var orig=h.style.boxShadow;
  h.style.transition='box-shadow .25s';
  h.style.boxShadow='0 0 0 3px rgba(34,197,94,.7)';
  setTimeout(function(){h.style.boxShadow='0 0 32px rgba(34,197,94,.45)';},280);
  setTimeout(function(){h.style.boxShadow=orig;},1200);
}

function _pollPresenterState(){
  clearTimeout(_presPollT);
  fetch('/api/vmixcaller/state',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      if(!d.has_data){_presPollT=setTimeout(_pollPresenterState,4000);return;}
      var parts=d.participants||[];

      // Caller-joined detection: previous list was non-empty comparison base,
      // skip the very first poll (no previous baseline yet → _presParts is [])
      // Only fire if we already had at least 0 known entries (not first load).
      if(_presParts!==null && parts.length>_presParts.length){
        _presChime();
        _presFlash();
        showMsg('\uD83D\uDCDE Caller joined: '+(parts[parts.length-1].name||'Guest'),true);
      }
      _presParts=parts;

      // ON AIR tally — any participant with active:true means they're on air in vMix
      var onAir=parts.some(function(p){return p.active;});
      var badge=document.getElementById('onair-badge');
      if(badge) badge.style.display=onAir?'inline-flex':'none';

      // Header status when not in a meeting
      if(!_inMeeting){
        var sub=document.getElementById('hdr-sub');
        if(sub){
          if(parts.length>0){
            sub.className='hdr-sub on-call';
            sub.textContent='\u25CF Caller waiting (\u2026join to connect)';
          } else {
            sub.className='hdr-sub';
            sub.textContent='Ready to join';
          }
        }
      }
    })
    .catch(function(){});
  _presPollT=setTimeout(_pollPresenterState,4000);
}

document.addEventListener('DOMContentLoaded',function(){
  try{if(_videoUrl) initPreview(_videoUrl);}catch(e){}
  _syncAudioBtn();
  _pollPresenterState();
  var mi=document.getElementById('mtg-id');
  if(mi) mi.addEventListener('keydown',function(e){if(e.key==='Enter') joinManual();});
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
<script nonce="{{csp_nonce()}}" src="https://cdn.jsdelivr.net/npm/hls.js@1/dist/hls.min.js" defer></script>
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
      <div class="ch">📹 Caller Preview
        <div class="ch-r" id="relay-ctrl" style="display:none">
          <span id="relay-ind" style="font-size:11px;color:var(--ok)"></span>
          <button id="relay-btn" class="btn bg bs" data-action="toggleRelay">📡 Stream to hub</button>
        </div>
      </div>
      <div class="cb" style="padding:10px">
        <div class="pvw-wrap">
          <video id="pvid" autoplay playsinline></video>
          <iframe id="pvframe" src="" allow="autoplay" title="Caller preview"></iframe>
          <div id="pvw-ov" class="pvw-ov">
            <div class="pvw-icon">📷</div>
            <div id="pvmsg">Configure a Preview URL to enable preview</div>
          </div>
        </div>
        <div style="margin-top:8px;text-align:center">
          <button class="btn bg bs muted" id="audio-btn" data-action="toggleAudio" title="Click to hear caller audio through this page">🔇 Hear Caller</button>
        </div>
      </div>
    </div>

    <!-- Bridge setup guide -->
    <div class="card">
      <div class="ch">⚙ Bridge Setup</div>
      <div class="cb" style="font-size:12px;color:var(--mu);line-height:1.65">

        <p style="font-weight:600;color:var(--tx);margin-bottom:6px">Option A — WebRTC player page <span style="font-weight:400;color:var(--ok)">(recommended)</span></p>
        <p style="margin-bottom:6px">Run SRS on the same LAN as vMix. Publish from vMix via SRT or RTMP; SRS exposes a built-in WebRTC player page for the browser:</p>
        <pre style="background:#0d1e40;border:1px solid var(--bor);border-radius:6px;padding:8px 10px;font-size:11px;color:#7dd3fc;white-space:pre-wrap;margin:6px 0">docker run -d --name srs --restart unless-stopped \
  -p 10080:10080/udp -p 8080:8080 -p 1935:1935 \
  -p 8000:8000/udp \
  ossrs/srs:5 ./objs/srs -c conf/rtc.conf</pre>
        <p style="margin-bottom:4px">In vMix → Zoom input → <strong>Output</strong> → enable SRT (<strong>Type: Caller</strong>):</p>
        <ul style="padding-left:16px;margin-bottom:6px">
          <li><strong style="color:var(--tx)">Hostname</strong> — LAN IP of the SRS machine (e.g. <code style="background:#0d1e40;padding:1px 5px;border-radius:3px">192.168.13.2</code>)</li>
          <li><strong style="color:var(--tx)">Port</strong> — <code style="background:#0d1e40;padding:1px 5px;border-radius:3px">10080</code></li>
          <li><strong style="color:var(--tx)">Stream ID</strong> — <code style="background:#0d1e40;padding:1px 5px;border-radius:3px">#!::h=live/caller,m=publish</code></li>
        </ul>
        <p>Set <strong style="color:var(--tx)">Preview URL</strong> below to the SRS WebRTC player page:</p>
        <pre style="background:#0d1e40;border:1px solid var(--bor);border-radius:6px;padding:6px 10px;font-size:11px;color:#7dd3fc;margin:6px 0">http://192.168.13.2:8080/players/rtc_player.html?autostart=true&amp;stream=webrtc://192.168.13.2/live/caller</pre>
        <p style="font-size:11px;color:var(--wn);margin-top:4px">⚠ WebRTC preview is served directly from the SRS machine. The client node and presenter page must be on the <strong>same LAN</strong> as SRS. The hub operator page will not show the preview if the hub is on a remote server.</p>

        <hr style="border:none;border-top:1px solid var(--bor);margin:12px 0">

        <p style="font-weight:600;color:var(--tx);margin-bottom:6px">Option B — HLS stream (legacy / hub-server bridge)</p>
        <p style="margin-bottom:6px">Run SRS with SRT input on the hub server. Bind port 8080 to localhost — SignalScope proxies the HLS stream securely. Set Preview URL to:</p>
        <pre style="background:#0d1e40;border:1px solid var(--bor);border-radius:6px;padding:6px 10px;font-size:11px;color:#7dd3fc;margin:6px 0">http://127.0.0.1:8080/live/caller.m3u8</pre>
        <p style="font-size:11px">SignalScope detects the <code>.m3u8</code> extension and proxies the stream — hub works from any browser.</p>

        <hr style="border:none;border-top:1px solid var(--bor);margin:12px 0">

        <p style="font-weight:600;color:var(--tx);margin-bottom:6px">📌 Presenter bookmark</p>
        <p style="margin-bottom:4px">For WebRTC, open the presenter page from the <strong style="color:var(--tx)">client node</strong> URL so the browser is on the same LAN as SRS:</p>
        <pre style="background:#0d1e40;border:1px solid var(--bor);border-radius:6px;padding:6px 10px;font-size:11px;color:#7dd3fc;margin:6px 0">http://&lt;client-node-ip&gt;:&lt;port&gt;/hub/vmixcaller/presenter</pre>

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
            <label class="fl">vMix Zoom Input</label>
            <input type="text" id="vmix-input" value="{{cfg.vmix_input}}" placeholder="1">
            <div class="hint">Number or name of the Zoom input in vMix. Names work if you've renamed the input (e.g. "Zoom Call 1").</div>
          </div>
          <div class="field" style="justify-content:flex-end">
            <label class="fl">&nbsp;</label>
            <button class="btn bp" data-action="saveConfig">💾 Save &amp; Push to Site</button>
          </div>
        </div>
        <div class="field" style="margin-top:4px">
          <label class="fl">Preview URL</label>
          <input type="text" id="bridge-url" placeholder="http://192.168.x.x:8080/players/rtc_player.html?autostart=true&stream=webrtc://192.168.x.x/live/caller" value="{{cfg.bridge_url|e}}">
          <div class="hint">WebRTC player page (SRS <code>/players/rtc_player.html?...</code>) or HLS <code>.m3u8</code> URL. See setup guide on the left. Client node also uses this to drive its own preview.</div>
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
          <button class="btn bp join-btn" style="flex:1;justify-content:center" data-action="joinManual">📞 Join Meeting</button>
        </div>
        <div class="brow call-btns" style="flex-wrap:wrap">
          <button class="btn bg" id="mute-btn" data-action="muteSelf">🔇 Mute Self</button>
          <button class="btn bg" id="cam-btn"  data-action="stopCamera">📷 Stop Camera</button>
          <button class="btn bg"               data-action="muteAll">🔇 Mute All</button>
          <button class="btn bd"               data-action="leaveMeeting">📴 Leave</button>
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
      <button class="btn bg bs" data-action="loadState">↻ Refresh</button>
    </div>
  </div>
  <div class="cb">
    <div id="plist" class="plist"><div class="empty-note">Select a site and wait for the client to report participants</div></div>
    <div class="padd">
      <input type="text" id="padd-in" placeholder="Manually add caller name…">
      <button class="btn bg bs" data-action="addManual">+ Add</button>
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
      <button class="btn bp" style="align-self:flex-end" data-action="addMeeting">+ Add</button>
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

var _videoUrl    = {{video_url_json|safe}};
var _relayOn     = false;
var _relaySite   = '';
var _relayLive   = false;   // true once client confirms relay_active

function _needsRelay(){
  // Hub needs the relay when it can't access the bridge directly:
  //   • _videoUrl is empty  → LAN WebRTC or no bridge configured
  //   • _videoUrl points at relay.m3u8  → LAN HLS (compute_video_url already
  //     resolved it to relay buffer because bridge host isn't localhost)
  return !_videoUrl || _videoUrl.indexOf('relay.m3u8') >= 0;
}

function updateRelayCtrl(){
  var ctrl=document.getElementById('relay-ctrl');
  var btn =document.getElementById('relay-btn');
  var ind =document.getElementById('relay-ind');
  var site=(document.getElementById('target-site').value||'').trim();
  if(!ctrl)return;
  if(!site||!_needsRelay()){ctrl.style.display='none';return;}
  ctrl.style.display='';
  if(_relayOn){
    btn.textContent='\u23F9 Stop stream';btn.className='btn bd bs';
    ind.textContent=_relayLive?'\u25CF Live':'Connecting\u2026';
    ind.style.color=_relayLive?'var(--ok)':'var(--wn)';
  }else{
    btn.textContent='\uD83D\uDCE1 Stream to hub';btn.className='btn bg bs';
    ind.textContent='';
  }
}

function toggleRelay(){
  var site=(document.getElementById('target-site').value||'').trim();
  if(!site){showMsg('Select a site first',false);return;}
  var want=!_relayOn;
  // Stop a relay on a different site if one is running
  if(_relaySite&&_relaySite!==site&&_relayOn)
    _post('/api/vmixcaller/relay',{site:_relaySite,active:false});
  _post('/api/vmixcaller/relay',{site:site,active:want})
    .then(function(d){
      if(!d.ok){showMsg(d.error||'Failed',false);return;}
      _relayOn=want;_relaySite=want?site:'';_relayLive=false;
      updateRelayCtrl();
      if(!want){
        initPreview(_videoUrl);
        showMsg('Relay stopped',true);
      }else{
        // Start loading relay.m3u8 immediately — hls.js will retry until
        // segments arrive (typically 3–6 s after the client starts relaying)
        initPreview('/hub/vmixcaller/video/relay.m3u8');
      }
    }).catch(function(e){showMsg('Error: '+e,false);});
}

function saveConfig(){
  var site  = document.getElementById('target-site').value;
  var ip    = document.getElementById('vmix-ip').value.trim()||'127.0.0.1';
  var port  = parseInt(document.getElementById('vmix-port').value)||8088;
  var inpRaw=(document.getElementById('vmix-input').value||'').trim();
  var inp=isNaN(inpRaw)||inpRaw===''?inpRaw:(parseInt(inpRaw)||1); // keep string names; coerce plain numbers
  var url   = document.getElementById('bridge-url').value.trim();
  _post('/api/vmixcaller/config',{target_site:site,vmix_ip:ip,vmix_port:port,vmix_input:inp,bridge_url:url})
    .then(function(d){
      if(d.ok){
        var msg=site?'Settings saved \u2014 vMix config sent to '+site:'Settings saved';
        showMsg(msg,true);
        if(site)loadState();
        // Fetch the computed proxy URL from server so initPreview gets the
        // right path (relay manifest for LAN bridges, direct path for localhost)
        fetch('/api/vmixcaller/video_url',{credentials:'same-origin'})
          .then(function(r){return r.json();})
          .then(function(v){_videoUrl=v.url||'';initPreview(_videoUrl);updateRelayCtrl();})
          .catch(function(){});
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
      // Update "● Live" badge once client confirms relay is running
      if(_relayOn&&d.relay_active&&!_relayLive){
        _relayLive=true;
        updateRelayCtrl();
      }
    }).catch(function(){setStatus('off','State poll failed',0);});
  _statePollT=setTimeout(loadState,4000);
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
    return '<div class="pi"><span class="pn">'+safe+'</span>'+b+'<button class="btn bw bs" data-name="'+safe+'" data-action="putOnAir">\uD83D\uDCFA Put On Air</button></div>';
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
      +'<button class="btn bp bs" data-idx="'+i+'" data-action="joinSavedAdmin">&#128222; Join</button>'
      +'<button class="btn bd bs" data-idx="'+i+'" data-action="deleteMeeting">&#x2715;</button>'
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
  try{if(_videoUrl) initPreview(_videoUrl);}catch(e){}
  var sel=document.getElementById('target-site');
  if(sel){
    if(sel.value) loadState();
    sel.addEventListener('change',function(){
      // Stop relay on old site when operator switches site
      if(_relayOn&&_relaySite){
        _post('/api/vmixcaller/relay',{site:_relaySite,active:false});
        _relayOn=false;_relaySite='';_relayLive=false;
        initPreview(_videoUrl);
      }
      updateRelayCtrl();
      if(this.value) loadState();
    });
  }
  updateRelayCtrl();
  loadMeetings();
  var pi=document.getElementById('padd-in');
  if(pi) pi.addEventListener('keydown',function(e){if(e.key==='Enter') addManual();});
  // Stop relay cleanly when operator closes/refreshes the tab
  window.addEventListener('beforeunload',function(){
    if(_relayOn&&_relaySite) _post('/api/vmixcaller/relay',{site:_relaySite,active:false});
  });
});
</script>
</body>
</html>"""


# ── Client page (full-featured — matches hub capabilities) ────────────────────
_CLIENT_TPL = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<title>vMix Caller — SignalScope</title>
<script nonce="{{csp_nonce()}}" src="https://cdn.jsdelivr.net/npm/hls.js@1/dist/hls.min.js" defer></script>
<style nonce="{{csp_nonce()}}">
""" + _CSS + r"""
/* client-specific */
#sbar{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--mu);margin-bottom:14px;padding:8px 12px;background:var(--sur);border:1px solid var(--bor);border-radius:8px}
.sep{border:none;border-top:1px solid var(--bor);margin:12px 0}
.mtg-admin-row{display:flex;align-items:center;gap:8px;padding:7px 10px;background:#091e42;border:1px solid var(--bor);border-radius:8px;margin-bottom:6px}
.mtg-admin-row .mtg-name{flex:1;font-weight:600}
.mtg-admin-row .mtg-id{font-size:11px;color:var(--mu)}
.add-mtg{display:grid;grid-template-columns:1fr 1fr 1fr auto;gap:8px;margin-top:10px;align-items:end}
@media(max-width:600px){.add-mtg{grid-template-columns:1fr 1fr}}
.hint{font-size:11px;color:var(--mu);margin-top:3px;line-height:1.4}
</style>
</head>
<body>
{{topnav("vmixcaller")|safe}}
<main>
<div id="msg"></div>

<!-- ── Status bar ─────────────────────────────────────────────────────────── -->
<div id="sbar">
  <span class="dot" id="vdot"></span>
  <span id="vstatus">Checking vMix…</span>
  <span id="vago" class="ago" style="margin-left:6px"></span>
  <span style="margin-left:auto;display:flex;gap:10px;align-items:center">
    <a href="/hub/vmixcaller/presenter" class="btn bp bs" title="Presenter bookmark page">🖥 Presenter View</a>
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
          <video id="pvid" autoplay playsinline></video>
          <iframe id="pvframe" src="" allow="autoplay" title="Caller preview"></iframe>
          <div id="pvw-ov" class="pvw-ov">
            <div class="pvw-icon">📷</div>
            <div id="pvmsg">{% if cfg.bridge_url %}Waiting for caller…{% else %}Configure a Preview URL in settings{% endif %}</div>
          </div>
        </div>
        <div style="margin-top:8px;text-align:center">
          <button class="btn bg bs muted" id="audio-btn" data-action="toggleAudio" title="Click to hear caller audio through this page">🔇 Hear Caller</button>
        </div>
      </div>
    </div>

    {% if hub_url %}
    <!-- Hub connection info -->
    <div class="card">
      <div class="ch">📡 Hub Connection</div>
      <div class="cb" style="font-size:12px;color:var(--mu)">
        <p>Hub: <strong style="color:var(--tx)">{{hub_url|e}}</strong></p>
        <p style="margin-top:6px">Commands polled every 3 s. Participants reported every ~12 s.</p>
        <p style="margin-top:6px">Hub operator can push vMix config from their settings page.</p>
      </div>
    </div>
    {% endif %}
  </div>

  <!-- ── RIGHT: controls ────────────────────────────────────────────────────── -->
  <div>

    <!-- vMix Settings -->
    <div class="card">
      <div class="ch">⚙ vMix Settings</div>
      <div class="cb">
        <div class="r2">
          <div class="field">
            <label class="fl">vMix IP</label>
            <input type="text" id="vmix-ip" placeholder="127.0.0.1" value="{{cfg.vmix_ip|e}}">
          </div>
          <div class="field">
            <label class="fl">Port</label>
            <input type="number" id="vmix-port" value="{{cfg.vmix_port}}" min="1" max="65535">
          </div>
        </div>
        <div class="r2">
          <div class="field">
            <label class="fl">Zoom Input</label>
            <input type="text" id="vmix-input" value="{{cfg.vmix_input}}" placeholder="1">
            <div class="hint">Number or name of the Zoom input in vMix.</div>
          </div>
          <div class="field" style="justify-content:flex-end">
            <label class="fl">&nbsp;</label>
            <button class="btn bg bs" data-action="testVmix">🔌 Test vMix</button>
          </div>
        </div>
        <div class="field">
          <label class="fl">Preview URL</label>
          <input type="text" id="bridge-url" placeholder="webrtc://192.168.x.x/live/caller" value="{{cfg.bridge_url|e}}">
          <div class="hint">WebRTC: <code>webrtc://host/app/stream</code> · HLS: <code>.m3u8</code> URL. Usually pushed from the hub Save &amp; Push button.</div>
        </div>
        <div class="brow">
          <button class="btn bp" data-action="saveConfig">💾 Save</button>
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
          <button class="btn bp join-btn" style="flex:1;justify-content:center" data-action="joinManual">📞 Join Meeting</button>
        </div>
        <div class="brow call-btns" style="flex-wrap:wrap">
          <button class="btn bg" id="mute-btn" data-action="muteSelf">🔇 Mute Self</button>
          <button class="btn bg" id="cam-btn"  data-action="stopCamera">📷 Stop Camera</button>
          <button class="btn bg"               data-action="muteAll">🔇 Mute All</button>
          <button class="btn bd"               data-action="leaveMeeting">📴 Leave</button>
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
      <button class="btn bg bs" data-action="loadState">↻ Refresh</button>
    </div>
  </div>
  <div class="cb">
    <div id="plist" class="plist"><div class="empty-note">No participants yet — join a meeting to see callers</div></div>
    <div class="hint" style="margin-top:6px">After joining, Zoom may take 15–30 s to fully connect and populate callers in vMix. If the list is empty, wait 30 s then refresh.</div>
    <div class="padd">
      <input type="text" id="padd-in" placeholder="Manually add caller name…">
      <button class="btn bg bs" data-action="addManual">+ Add</button>
    </div>
  </div>
</div>

<!-- ── Saved Meetings ─────────────────────────────────────────────────────── -->
<div class="card">
  <div class="ch">📋 Saved Meetings
    <div class="ch-r">
      <a href="/hub/vmixcaller/presenter" class="btn bw bs" target="_blank">🖥 Open Presenter View</a>
    </div>
  </div>
  <div class="cb">
    <p style="font-size:12px;color:var(--mu);margin-bottom:10px">
      Saved meetings appear on the Presenter View for one-click joining.
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
      <button class="btn bp" style="align-self:flex-end" data-action="addMeeting">+ Add</button>
    </div>
  </div>
</div>

</main>
<script nonce="{{csp_nonce()}}">
""" + _JS_HELPERS + r"""

// ── Client-specific additions ─────────────────────────────────────────────────
var _videoUrl = {{video_url_json|safe}};

function setStatus(state,text,ts){
  document.getElementById('vdot').className='dot '+(state==='ok'?'dok':state==='warn'?'dwn':'dal');
  document.getElementById('vstatus').textContent=text;
  var ago=document.getElementById('vago');
  if(ago)ago.textContent=ts?'('+_ago(ts)+')':'';
}

function saveConfig(){
  var _inpRaw=(document.getElementById('vmix-input').value||'').trim();
  var d={
    vmix_ip:   (document.getElementById('vmix-ip').value||'').trim()||'127.0.0.1',
    vmix_port: parseInt(document.getElementById('vmix-port').value)||8088,
    vmix_input:isNaN(_inpRaw)||_inpRaw===''?_inpRaw:(parseInt(_inpRaw)||1),
    bridge_url:(document.getElementById('bridge-url').value||'').trim()
  };
  _post('/api/vmixcaller/config',d).then(function(r){
    if(r.ok){
      showMsg('Saved',true);
      fetch('/api/vmixcaller/video_url',{credentials:'same-origin'})
        .then(function(r){return r.json();})
        .then(function(v){_videoUrl=v.url||'';initPreview(_videoUrl);})
        .catch(function(){});
    } else showMsg(r.error||'Save failed',false);
  }).catch(function(e){showMsg('Error: '+e,false);});
}

function testVmix(){
  fetch('/api/vmixcaller/test_local',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      if(d.ok)showMsg('\u2713 vMix reachable \u2014 version '+d.version,true);
      else showMsg('\u2717 Cannot reach vMix: '+(d.error||'unknown'),false);
    }).catch(function(e){showMsg('Error: '+e,false);});
}

function joinManual(){
  var mid=(document.getElementById('mtg-id').value||'').trim();
  var pass=(document.getElementById('mtg-pass').value||'').trim();
  var name=(document.getElementById('mtg-name').value||'Guest Producer').trim();
  joinWith(mid,pass,name);
}

// ── State polling — direct vMix query (no hub cycle delay) ───────────────────
var _statePollT=null;
function loadState(){
  clearTimeout(_statePollT);
  fetch('/api/vmixcaller/local_state',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      if(d.ok){
        setStatus('ok','vMix connected'+(d.version?' \u2014 v'+d.version:''),d.ts);
      } else {
        setStatus('al','vMix unreachable: '+(d.error||''),d.ts);
      }
      renderParticipants(d.participants||[]);
      var pago=document.getElementById('parts-ago');
      if(pago&&d.ts)pago.textContent='updated '+_ago(d.ts);
    }).catch(function(){setStatus('off','Poll failed',0);});
  _statePollT=setTimeout(loadState,4000);
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
    return '<div class="pi"><span class="pn">'+safe+'</span>'+b+'<button class="btn bw bs" data-name="'+safe+'" data-action="putOnAir">\uD83D\uDCFA Put On Air</button></div>';
  }).join('');
}
function addManual(){var inp=document.getElementById('padd-in');var n=inp.value.trim();if(!n)return;if(!_parts.some(function(p){return p.name===n;})){_parts.push({name:n,muted:false,active:false});renderParticipants(_parts);}inp.value='';}
function putOnAir(btn){
  sendCmd('ZoomSelectParticipantByName',btn.dataset.name)
    .then(function(d){if(d.ok)showMsg('"'+btn.dataset.name+'" queued for air',true);});
}

// ── Saved meetings ────────────────────────────────────────────────────────────
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
      +'<button class="btn bp bs" data-idx="'+i+'" data-action="joinSavedAdmin">&#128222; Join</button>'
      +'<button class="btn bd bs" data-idx="'+i+'" data-action="deleteMeeting">&#x2715;</button>'
      +'</div>';
  }).join('');
}
function joinSavedAdmin(btn){
  var m=_meetings[parseInt(btn.dataset.idx)];if(!m)return;
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
  try{if(_videoUrl) initPreview(_videoUrl);}catch(e){}
  loadState();
  loadMeetings();
  var pi=document.getElementById('padd-in');
  if(pi) pi.addEventListener('keydown',function(e){if(e.key==='Enter') addManual();});
  var mi=document.getElementById('mtg-id');
  if(mi) mi.addEventListener('keydown',function(e){if(e.key==='Enter') joinManual();});
});
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

    # ── Video relay thread (client and both-mode nodes) ───────────────────────
    # Event-driven — only pulls/pushes when hub requests relay via cmd response.
    if mode in ("client", "both") and hub_url:
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
            video_url = _compute_video_url(cfg, is_hub)
            return render_template_string(_HUB_TPL, cfg=cfg, sites=sites,
                                          video_url_json=json.dumps(video_url))
        if is_client:
            video_url = _compute_video_url(cfg, False)
            return render_template_string(_CLIENT_TPL, cfg=cfg, hub_url=hub_url,
                                          video_url_json=json.dumps(video_url))
        # Standalone
        video_url = _compute_video_url(cfg, True)
        return render_template_string(_HUB_TPL, cfg=cfg, sites=[],
                                      video_url_json=json.dumps(video_url))

    # ── Presenter page ────────────────────────────────────────────────────────
    @app.get("/hub/vmixcaller/presenter")
    @login_required
    def vmixcaller_presenter():
        cfg       = _load_cfg()
        meetings  = cfg.get("saved_meetings") or []
        bridge    = (cfg.get("bridge_url") or "").strip()
        video_url = _compute_video_url(cfg, is_hub)
        return render_template_string(
            _PRESENTER_TPL,
            cfg=cfg,
            meetings=meetings,
            bridge_url=bridge,
            video_url_json=json.dumps(video_url),
        )

    # ── WHEP proxy — avoids browser CORS/mixed-content on direct SRS fetch ──────
    # Browser POSTs SDP offer here; we forward to SRS and return the SDP answer.
    # Works on hub, client, and standalone nodes.
    @app.post("/api/vmixcaller/whep_proxy")
    @login_required
    def vmixcaller_whep_proxy():
        import urllib.request as _ur
        import urllib.error as _ue
        url = request.args.get("url", "").strip()
        if not url.startswith(("http://", "https://")):
            return Response("Bad URL", status=400, mimetype="text/plain")
        sdp_offer = request.get_data(as_text=True)
        try:
            req = _ur.Request(
                url,
                data=sdp_offer.encode(),
                method="POST",
                headers={"Content-Type": "application/sdp"},
            )
            with _ur.urlopen(req, timeout=10) as resp:
                sdp_answer = resp.read().decode()
            return Response(sdp_answer, status=200, mimetype="application/sdp")
        except _ue.HTTPError as exc:
            return Response(f"WHEP {exc.code}", status=exc.code, mimetype="text/plain")
        except Exception as exc:
            return Response(str(exc), status=502, mimetype="text/plain")

    # ── Authenticated HLS proxy / relay server (/hub/vmixcaller/video/<path>) ──
    # Registered on ALL nodes (hub and client).
    #
    # Hub with localhost bridge:
    #   • Direct proxy to local SRS (except "relay.m3u8" which always uses buffer)
    #
    # Hub with LAN bridge or no bridge configured:
    #   • seg/N.ts  → served from in-memory relay buffer (client pushed via video_push)
    #   • *.m3u8    → synthetic manifest from relay buffer
    #   • relay.m3u8 → always synthetic manifest even for localhost bridges
    #
    # Client node:
    #   • All requests proxied directly to LAN bridge (same network).
    @app.get("/hub/vmixcaller/video/<path:seg>")
    @login_required
    def vmixcaller_video_proxy(seg):
        cfg      = _load_cfg()
        base_url = (cfg.get("bridge_url") or "").strip()

        if is_hub:
            # Determine if we have a localhost bridge (direct proxy available)
            localhost_bridge = False
            parsed_lb = None
            if base_url:
                try:
                    parsed_lb = urlparse(base_url)
                    host = (parsed_lb.hostname or "").lower()
                    localhost_bridge = host in ("127.0.0.1", "localhost", "::1")
                except Exception:
                    pass

            # ── Buffered TS segments (relay buffer) ───────────────────────────
            # These are always from the relay buffer regardless of bridge type.
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

            # ── Manifest requests ─────────────────────────────────────────────
            if seg.endswith(".m3u8"):
                # "relay.m3u8" always generates synthetic manifest from buffer.
                # Other .m3u8 paths on localhost bridge → proxy to SRS directly.
                if localhost_bridge and seg != "relay.m3u8":
                    try:
                        proxy_url = f"{parsed_lb.scheme}://{parsed_lb.netloc}/{seg}"
                        resp = urllib.request.urlopen(
                            urllib.request.Request(proxy_url, method="GET"), timeout=5)
                        data = resp.read()
                        return Response(data,
                                        content_type="application/vnd.apple.mpegurl",
                                        headers={"Cache-Control": "no-cache"})
                    except urllib.error.HTTPError as e:
                        return Response("", status=e.code)
                    except Exception:
                        return Response("", status=502)

                # Relay buffer manifest (LAN bridge, no bridge, or relay.m3u8)
                with _video_lock:
                    if not _video_segments:
                        # Return a valid open-ended live manifest with no segments.
                        # hls.js treats this as "stream not yet available" and retries
                        # at the target duration interval (~3 s) rather than erroring.
                        return Response(
                            "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:3\n#EXT-X-MEDIA-SEQUENCE:0\n",
                            status=200,
                            content_type="application/vnd.apple.mpegurl",
                            headers={"Cache-Control": "no-cache"},
                        )
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

            # ── Other paths on localhost bridge (e.g. raw .ts from SRS) ──────
            if localhost_bridge:
                try:
                    proxy_url = f"{parsed_lb.scheme}://{parsed_lb.netloc}/{seg}"
                    resp = urllib.request.urlopen(
                        urllib.request.Request(proxy_url, method="GET"), timeout=5)
                    data = resp.read()
                    ct   = resp.headers.get("Content-Type", "video/mp2t")
                    return Response(data, content_type=ct,
                                    headers={"Cache-Control": "no-cache"})
                except urllib.error.HTTPError as e:
                    return Response("", status=e.code)
                except Exception:
                    return Response("", status=502)

            return Response("not found", status=404, content_type="text/plain")

        # ── Client node: direct proxy to LAN bridge ───────────────────────────
        if not base_url:
            return Response("no bridge configured", status=404,
                            content_type="text/plain")
        try:
            parsed = urlparse(base_url)
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

    @app.get("/api/vmixcaller/video_url")
    @login_required
    def vmixcaller_video_url():
        """Return the browser-ready proxy URL for the video feed.
        Called by the hub page after saving config so the JS can re-init
        the video without a page reload.
        """
        cfg = _load_cfg()
        return jsonify({"url": _compute_video_url(cfg, is_hub)})

    @app.post("/api/vmixcaller/config")
    @login_required
    @csrf_protect
    def vmixcaller_save_config():
        data = request.get_json(silent=True) or {}
        try:
            cfg = _save_cfg(data)
            # Whenever the hub saves config with a target site selected,
            # push ALL relevant fields to the client node so it has the
            # bridge URL (for video relay + presenter page), vMix IP/port,
            # and input number — no manual entry needed on the client.
            target = (cfg.get("target_site") or "").strip()
            if is_hub and target:
                with _state_lock:
                    _pending_cmd[target] = {
                        "fn":         "__set_config__",
                        "vmix_ip":    cfg.get("vmix_ip",    "127.0.0.1"),
                        "vmix_port":  cfg.get("vmix_port",   8088),
                        "vmix_input": cfg.get("vmix_input",  1),
                        "bridge_url": cfg.get("bridge_url",  ""),
                        "seq":        int(time.time() * 1000),
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
        # ZoomJoinMeeting requires Value (meeting ID), Value2 (password), Value3 (display name).
        # JS sends these pipe-delimited in value for backwards compat; unpack here.
        raw_value = data.get("value")
        value2    = data.get("value2")
        value3    = data.get("value3")
        if fn == "ZoomJoinMeeting" and raw_value and "|" in str(raw_value) and value2 is None:
            parts     = str(raw_value).split("|", 2)
            raw_value = parts[0]                                         # meeting ID
            value2    = parts[1] if len(parts) > 1 else ""               # password
            value3    = parts[2] if len(parts) > 2 else "Guest Producer" # display name
        if not is_hub:
            # Standalone: execute directly
            if not cfg.get("vmix_ip"):
                return jsonify({"ok": False, "error": "No vMix IP configured"})
            ok, resp = _vmix_fn(cfg, fn, value=raw_value, inp=data.get("input"),
                                value2=value2, value3=value3)
            return jsonify({"ok": ok,
                            "response": resp[:200] if ok else None,
                            "error":    resp if not ok else None})
        if not target_site:
            return jsonify({"ok": False, "error": "No target site selected — save settings first"}), 400
        with _state_lock:
            _pending_cmd[target_site] = {
                "fn":     fn,
                "value":  raw_value,
                "value2": value2,
                "value3": value3,
                "input":  data.get("input") or cfg.get("vmix_input", 1),
                "seq":    int(time.time() * 1000),
            }
        return jsonify({"ok": True, "queued_for": target_site})

    # ── Client polls for pending command ──────────────────────────────────────
    # NOT decorated with @login_required — client polls from a background thread
    # using HMAC headers (no session cookie).  Auth is via site approval + optional
    # HMAC signature, matching vmixcaller_report / vmixcaller_video_push.
    @app.get("/api/vmixcaller/cmd")
    def vmixcaller_cmd_poll():
        import hashlib as _hs, hmac as _hm
        site = request.headers.get("X-Site", "").strip()
        if not site:
            return jsonify({"error": "missing X-Site"}), 400
        with hub_server._lock:
            approved = hub_server._sites.get(site, {}).get("_approved")
        if not approved:
            return jsonify({"error": "not approved"}), 403
        # Optional HMAC verification
        secret = getattr(getattr(monitor.app_cfg, "hub", None), "secret_key", "") or ""
        if secret:
            sig  = request.headers.get("X-Hub-Sig", "")
            ts_s = request.headers.get("X-Hub-Ts", "0")
            try:
                ts = float(ts_s)
                if abs(time.time() - ts) > 120:
                    return jsonify({"error": "timestamp expired"}), 403
                key      = _hs.sha256(f"{secret}:signing".encode()).digest()
                expected = _hm.new(key, f"{ts:.0f}:".encode() + b"", _hs.sha256).hexdigest()
                if not _hm.compare_digest(sig, expected):
                    return jsonify({"error": "bad signature"}), 403
            except Exception:
                return jsonify({"error": "auth error"}), 403
        with _state_lock:
            cmd          = _pending_cmd.pop(site, None)
            relay_wanted = _relay_wanted.get(site, False)
        result = {"relay": "start" if relay_wanted else "stop"}
        if cmd:
            result["cmd"] = cmd
        return jsonify(result)

    # ── Hub: start / stop video relay for a site ─────────────────────────────
    @app.post("/api/vmixcaller/relay")
    @login_required
    @csrf_protect
    def vmixcaller_relay_control():
        data   = request.get_json(silent=True) or {}
        site   = str(data.get("site", "") or "").strip()
        active = bool(data.get("active", False))
        if not site:
            return jsonify({"ok": False, "error": "no site"}), 400
        with _state_lock:
            _relay_wanted[site] = active
        if not active:
            # Flush stale segments so relay.m3u8 doesn't serve old video
            with _video_lock:
                _video_segments.clear()
        return jsonify({"ok": True})

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

    # ── Client: live vMix state (participants + version, direct query) ────────
    # Used by the client page's loadState() poll so participants update without
    # waiting for the hub 12 s reporting cycle.  Works on any node mode.
    @app.get("/api/vmixcaller/local_state")
    @login_required
    def vmixcaller_local_state():
        cfg = _load_cfg()
        now = time.time()
        ok, xml_text = _vmix_xml(cfg)
        if ok:
            return jsonify({
                "ok":           True,
                "ts":           now,
                "version":      _vmix_version(xml_text),
                "participants": _parse_participants(xml_text, cfg.get("vmix_input", 1)),
            })
        return jsonify({
            "ok":           False,
            "ts":           now,
            "error":        xml_text[:120],
            "participants": [],
        })
