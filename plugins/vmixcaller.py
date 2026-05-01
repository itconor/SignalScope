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
    "version": "1.8.2",
}

import os
import json
import time
import uuid as _uuid_mod
import hashlib
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

# ── Hub-side runtime state ─────────────────────────────────────────────────────
_pending_cmd: dict  = {}   # site_name → {fn, value, input, seq}
_site_status: dict  = {}   # site_name → {ok, version, participants, error, ts}
_relay_wanted: dict = {}   # site_name → bool (hub wants this site's video relay)
_state_lock          = threading.Lock()

# ── Client-side relay gate ─────────────────────────────────────────────────────
# Set by the client thread when hub sends relay:"start"; cleared on "stop".
# _video_relay_loop blocks on this so it only runs on demand.
_relay_event = threading.Event()

# ── Zoom API hub-side state ──────────────────────────────────────────────
_zoom_token_lock      = threading.Lock()
_zoom_token_cache     = {"token": "", "expires": 0.0}
_zoom_meetings_lock   = threading.Lock()
_zoom_meetings_cache  = {"ts": 0.0, "data": []}
_zoom_participants_cache = {"ts": 0.0, "meeting_id": "", "data": {"participants": [], "waiting": []}}
_zoom_participants_lock  = threading.Lock()
_zoom_webhook_counter    = [0]   # incremented on each inbound webhook event


# ── Hub-side HLS relay buffer ──────────────────────────────────────────────────
# Client nodes fetch TS segments from the local LAN bridge and push them here.
# Hub serves a synthetic HLS manifest pointing to buffered segments so remote
# browsers (HTTPS hub) can play the video without reaching the LAN bridge.
_video_segments: dict = {}   # seq_num (int) → {"data": bytes, "duration": float}
_video_seq             = [0] # next seq — list so nested functions can mutate
_video_lock            = threading.Lock()
_VIDEO_MAX_SEGS        = 6   # ~12–18 s buffer at 2–3 s/segment


# ── Config helpers ─────────────────────────────────────────────────────────────
# Config schema (stored in vmixcaller_config.json):
#   target_site:         str   — which SignalScope site runs vMix
#   active_instance_id:  str   — ID of the currently selected vMix instance
#   instances:           list  — saved vMix instances (see _make_instance below)
#   saved_meetings:      list  — saved Zoom meeting presets
#
# Each instance dict:
#   id, name, vmix_ip, vmix_port, vmix_input, bridge_url
#
# Backwards compat: if no "instances" key, legacy flat vmix_ip/bridge_url
# fields are migrated to a single "Default" instance transparently.

def _make_instance(name="New Instance", vmix_ip="127.0.0.1", vmix_port=8088,
                   vmix_input=1, bridge_url="", inst_id=None,
                   preview_mode="srt", ndi_source="") -> dict:
    return {
        "id":           inst_id or str(_uuid_mod.uuid4())[:8],
        "name":         name,
        "vmix_ip":      vmix_ip,
        "vmix_port":    int(vmix_port or 8088),
        "vmix_input":   vmix_input,
        "bridge_url":   bridge_url,
        "preview_mode": preview_mode if preview_mode in ("srt", "ndi") else "srt",
        "ndi_source":   ndi_source,
    }


def _load_raw() -> dict:
    """Return raw stored JSON dict without any processing."""
    with _cfg_lock:
        try:
            with open(_CFG_PATH) as fh:
                return json.load(fh)
        except Exception:
            return {}


def _load_cfg() -> dict:
    """Return processed config.

    Active instance fields (vmix_ip, vmix_port, vmix_input, bridge_url) are
    injected at the top level so all existing code keeps working unchanged.
    """
    raw = _load_raw()

    # ── Migration: legacy flat fields → single "Default" instance ─────────
    instances = raw.get("instances")
    if instances is None:
        leg_ip    = raw.get("vmix_ip",    "127.0.0.1")
        leg_port  = raw.get("vmix_port",   8088)
        leg_input = raw.get("vmix_input",  1)
        leg_burl  = raw.get("bridge_url",  "")
        if any([leg_ip != "127.0.0.1", leg_port != 8088, leg_input != 1, leg_burl]):
            instances = [_make_instance("Default", leg_ip, leg_port,
                                        leg_input, leg_burl, "default")]
        else:
            instances = []

    active_id = raw.get("active_instance_id", "")
    active: dict = {}
    for inst in instances:
        if inst.get("id") == active_id:
            active = inst
            break
    if not active and instances:
        active = instances[0]
        active_id = active.get("id", "")

    return {
        "target_site":         raw.get("target_site", ""),
        "active_instance_id":  active_id,
        "instances":           instances,
        "saved_meetings":      raw.get("saved_meetings") or [],
        # Flat fields from active instance — keeps all downstream code unchanged
        "vmix_ip":    active.get("vmix_ip",    "127.0.0.1"),
        "vmix_port":  active.get("vmix_port",   8088),
        "vmix_input": active.get("vmix_input",  1),
        "bridge_url": active.get("bridge_url",  ""),
    }


def _save_cfg(data: dict) -> dict:
    """Persist a partial update and return the new processed config.

    Handles two call styles:
    • Instance-aware: pass ``instances``, ``active_instance_id``, ``target_site``
    • Legacy flat: pass ``vmix_ip``/``vmix_port``/``vmix_input``/``bridge_url`` —
      these are written into the current active instance so the client node's
      local ``saveConfig`` and the ``__set_config__`` push both keep working.
    """
    with _cfg_lock:
        try:
            with open(_CFG_PATH) as fh:
                current = json.load(fh)
        except Exception:
            current = {}

        for k in ("target_site", "active_instance_id"):
            if k in data:
                current[k] = data[k]
        if "instances" in data:
            current["instances"] = data["instances"]
        if "saved_meetings" in data:
            current["saved_meetings"] = data["saved_meetings"]

        # Legacy flat fields → update the active instance in-place
        _flat = {k: data[k] for k in ("vmix_ip", "vmix_port", "vmix_input", "bridge_url")
                 if k in data}
        if _flat:
            instances = current.get("instances")
            if instances is None:
                # First use without instances: create "Default" from merged flat fields
                defaults = {"vmix_ip": "127.0.0.1", "vmix_port": 8088,
                            "vmix_input": 1, "bridge_url": ""}
                defaults.update(_flat)
                instances = [_make_instance(
                    "Default", defaults["vmix_ip"], defaults["vmix_port"],
                    defaults["vmix_input"], defaults["bridge_url"], "default")]
                current["instances"] = instances
                if not current.get("active_instance_id"):
                    current["active_instance_id"] = "default"
            else:
                aid = current.get("active_instance_id", "")
                idx = next((i for i, x in enumerate(instances) if x.get("id") == aid), None)
                if idx is None and instances:
                    idx = 0
                if idx is not None:
                    for k, v in _flat.items():
                        if k == "vmix_port":
                            try:
                                v = int(v)
                            except (TypeError, ValueError):
                                v = 8088
                        instances[idx][k] = v

        with open(_CFG_PATH, "w") as fh:
            json.dump(current, fh, indent=2)
    return _load_cfg()


def _get_active_instance(cfg: dict) -> dict:
    instances = cfg.get("instances") or []
    aid = cfg.get("active_instance_id", "")
    for inst in instances:
        if inst.get("id") == aid:
            return inst
    return instances[0] if instances else {}



# ══════════════════════════════════════════════════════════════════════════════
# Zoom Server-to-Server OAuth helpers (hub-side only)
# ══════════════════════════════════════════════════════════════════════════════

def _zoom_has_creds(raw: dict) -> bool:
    return bool(raw.get("zoom_account_id") and
                raw.get("zoom_client_id") and
                raw.get("zoom_client_secret"))


def _zoom_get_token(raw: dict) -> str:
    """Get or refresh Zoom S2S OAuth Bearer token. Thread-safe, cached 1 hr."""
    import base64
    import hmac as _hmac
    with _zoom_token_lock:
        if time.time() < _zoom_token_cache["expires"] - 30:
            return _zoom_token_cache["token"]
        aid = (raw.get("zoom_account_id")    or "").strip()
        cid = (raw.get("zoom_client_id")     or "").strip()
        cs  = (raw.get("zoom_client_secret") or "").strip()
        if not (aid and cid and cs):
            return ""
        creds = base64.b64encode(f"{cid}:{cs}".encode()).decode()
        url   = ("https://zoom.us/oauth/token"
                 "?grant_type=account_credentials"
                 f"&account_id={urllib.parse.quote(aid)}")
        req = urllib.request.Request(
            url, method="POST",
            headers={"Authorization": f"Basic {creds}",
                     "Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=12) as r:
                resp = json.loads(r.read())
            token = resp.get("access_token", "")
            exp   = int(resp.get("expires_in", 3600))
            _zoom_token_cache.update({"token": token, "expires": time.time() + exp})
            return token
        except Exception:
            _zoom_token_cache.update({"token": "", "expires": 0.0})
            return ""


def _zoom_api(raw: dict, method: str, path: str, body=None):
    """Call Zoom REST API v2. Returns (ok: bool, data: dict)."""
    import hmac as _hmac
    token = _zoom_get_token(raw)
    if not token:
        return False, {"message": "Zoom credentials not configured or auth failed"}
    url  = f"https://api.zoom.us/v2{path}"
    data = json.dumps(body, separators=(",", ":")).encode() if body else None
    req  = urllib.request.Request(
        url, data=data, method=method,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            raw_body = r.read()
            return True, json.loads(raw_body) if raw_body else {}
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read())
        except Exception:
            err = {"message": str(e)}
        if e.code == 401:
            with _zoom_token_lock:
                _zoom_token_cache.update({"token": "", "expires": 0.0})
        return False, err
    except Exception as e:
        return False, {"message": str(e)}


def _zoom_fetch_meetings(raw: dict, force: bool = False) -> list:
    """Return upcoming + running meetings from Zoom API, cached for 60 s."""
    with _zoom_meetings_lock:
        if not force and (time.time() - _zoom_meetings_cache["ts"]) < 60:
            return list(_zoom_meetings_cache["data"])
    out:  list = []
    seen: set  = set()

    def _add(m, status=None):
        mid = str(m.get("id", "")).strip()
        if not mid or mid in seen:
            return
        seen.add(mid)
        out.append({"id": mid,
                    "topic":      (m.get("topic") or "Untitled").strip(),
                    "status":     status or m.get("status", "waiting"),
                    "start_time": m.get("start_time", ""),
                    "duration":   int(m.get("duration") or 60),
                    "join_url":   m.get("join_url", ""),
                    "password":   (m.get("password") or "").strip()})

    ok, resp = _zoom_api(raw, "GET", "/users/me/meetings?type=upcoming&page_size=50")
    if ok:
        for m in resp.get("meetings", []):
            _add(m)
    ok2, resp2 = _zoom_api(raw, "GET", "/users/me/meetings?type=live&page_size=20")
    if ok2:
        for m in resp2.get("meetings", []):
            mid = str(m.get("id", ""))
            if mid in seen:
                for e in out:
                    if e["id"] == mid:
                        e["status"] = "started"; break
            else:
                _add(m, status="started")

    with _zoom_meetings_lock:
        _zoom_meetings_cache.update({"ts": time.time(), "data": out})
    return out



def _zoom_fetch_participants(raw: dict, meeting_id: str, force: bool = False) -> dict:
    """Fetch live + waiting-room participants for a running meeting (20 s cache)."""
    with _zoom_participants_lock:
        cached = _zoom_participants_cache
        if (not force
                and cached.get("meeting_id") == meeting_id
                and time.time() - cached.get("ts", 0) < 20):
            return dict(cached.get("data", {}))

    def _names(resp):
        out = []
        for p in resp.get("participants", []):
            pid  = (p.get("id") or "").strip()
            name = (p.get("user_name") or p.get("name") or "").strip()
            if not name:
                continue
            out.append({
                "id":      pid,
                "name":    name,
                "audio":   bool(p.get("audio")),   # audio field: "computer"/"Phone"/""
                "video":   str(p.get("video") or "").lower() == "started",
                "muted":   str(p.get("audio") or "") == "",   # empty = muted
                "is_host": bool(p.get("is_host")),
            })
        return out

    ok1, r1 = _zoom_api(raw, "GET",
        f"/meetings/{meeting_id}/participants?type=live&page_size=300")
    ok2, r2 = _zoom_api(raw, "GET",
        f"/meetings/{meeting_id}/participants?type=pending&page_size=300")

    data = {
        "participants": _names(r1) if ok1 else [],
        "waiting":      _names(r2) if ok2 else [],
        "error":        (r1 if not ok1 else {}).get("message", ""),
    }
    with _zoom_participants_lock:
        _zoom_participants_cache.update({
            "ts":         time.time(),
            "meeting_id": meeting_id,
            "data":       data,
        })
    return data


def _zoom_execute_action(raw: dict, action: str, data: dict) -> dict:
    """Execute a Zoom action. Returns a dict for jsonify()."""
    if not _zoom_has_creds(raw):
        return {"ok": False, "error": "Zoom API not configured on hub"}

    if action == "create":
        topic = (data.get("topic") or "").strip() or "Untitled Meeting"
        dur   = max(15, min(480, int(data.get("duration") or 60)))
        pw    = (data.get("passcode") or "").strip()
        wr    = bool(data.get("waiting_room", False))
        body  = {"topic": topic, "type": 1, "duration": dur,
                 "settings": {"waiting_room": wr, "join_before_host": not wr}}
        if pw:
            body["password"] = pw
        ok, resp = _zoom_api(raw, "POST", "/users/me/meetings", body)
        if ok:
            with _zoom_meetings_lock:
                _zoom_meetings_cache["ts"] = 0.0
            return {"ok": True,
                    "meeting_id": str(resp.get("id", "")),
                    "topic":      resp.get("topic", ""),
                    "password":   resp.get("password", ""),
                    "join_url":   resp.get("join_url", "")}
        return {"ok": False, "error": str(resp.get("message") or resp)[:120]}

    elif action == "end":
        mid = (data.get("meeting_id") or "").strip()
        if not mid:
            return {"ok": False, "error": "No meeting ID"}
        ok, resp = _zoom_api(raw, "PUT", f"/meetings/{mid}/status", {"action": "end"})
        if ok or (isinstance(resp, dict) and resp.get("code") in (3001, 3002)):
            with _zoom_meetings_lock:
                _zoom_meetings_cache["ts"] = 0.0
            return {"ok": True}
        return {"ok": False, "error": str(resp.get("message") or resp)[:120]}

    elif action == "delete":
        mid = (data.get("meeting_id") or "").strip()
        if not mid:
            return {"ok": False, "error": "No meeting ID"}
        ok, resp = _zoom_api(raw, "DELETE", f"/meetings/{mid}")
        if ok:
            with _zoom_meetings_lock:
                _zoom_meetings_cache["ts"] = 0.0
            return {"ok": True}
        return {"ok": False, "error": str(resp.get("message") or resp)[:120]}

    elif action == "refresh":
        try:
            return {"ok": True, "meetings": _zoom_fetch_meetings(raw, force=True)}
        except Exception as ex:
            return {"ok": False, "error": str(ex)[:120]}

    elif action == "mute_participant":
        mid = (data.get("meeting_id") or "").strip()
        pid = (data.get("participant_id") or "").strip()
        mute = bool(data.get("mute", True))
        if not mid or not pid:
            return {"ok": False, "error": "Missing meeting_id or participant_id"}
        ok, resp = _zoom_api(raw, "PUT", f"/meetings/{mid}/participants/{pid}",
                             {"mute": mute})
        if ok:
            with _zoom_participants_lock:
                if _zoom_participants_cache.get("meeting_id") == mid:
                    _zoom_participants_cache["ts"] = 0.0
            return {"ok": True}
        return {"ok": False, "error": str(resp.get("message") or resp)[:120]}

    elif action == "remove_participant":
        mid = (data.get("meeting_id") or "").strip()
        pid = (data.get("participant_id") or "").strip()
        if not mid or not pid:
            return {"ok": False, "error": "Missing meeting_id or participant_id"}
        ok, resp = _zoom_api(raw, "DELETE", f"/meetings/{mid}/participants/{pid}")
        if ok:
            with _zoom_participants_lock:
                if _zoom_participants_cache.get("meeting_id") == mid:
                    _zoom_participants_cache["ts"] = 0.0
            return {"ok": True}
        return {"ok": False, "error": str(resp.get("message") or resp)[:120]}

    elif action == "admit_participant":
        mid = (data.get("meeting_id") or "").strip()
        pid = (data.get("participant_id") or "").strip()
        if not mid or not pid:
            return {"ok": False, "error": "Missing meeting_id or participant_id"}
        ok, resp = _zoom_api(raw, "PUT", f"/meetings/{mid}/participants/{pid}",
                             {"hold": False})
        if ok:
            with _zoom_participants_lock:
                if _zoom_participants_cache.get("meeting_id") == mid:
                    _zoom_participants_cache["ts"] = 0.0
            return {"ok": True}
        return {"ok": False, "error": str(resp.get("message") or resp)[:120]}

    elif action == "admit_all":
        mid = (data.get("meeting_id") or "").strip()
        if not mid:
            return {"ok": False, "error": "Missing meeting_id"}
        ok, resp = _zoom_api(raw, "GET",
                             f"/meetings/{mid}/participants?type=pending&page_size=300")
        if not ok:
            return {"ok": False, "error": str(resp.get("message") or resp)[:120]}
        waiting = resp.get("participants", [])
        errors  = []
        for p in waiting:
            pid = (p.get("id") or "").strip()
            if not pid:
                continue
            ok2, r2 = _zoom_api(raw, "PUT", f"/meetings/{mid}/participants/{pid}",
                                {"hold": False})
            if not ok2:
                errors.append(str(r2.get("message") or r2)[:60])
        with _zoom_participants_lock:
            if _zoom_participants_cache.get("meeting_id") == mid:
                _zoom_participants_cache["ts"] = 0.0
        if errors:
            return {"ok": True, "admitted": len(waiting) - len(errors),
                    "warnings": errors}
        return {"ok": True, "admitted": len(waiting)}


    return {"ok": False, "error": f"Unknown action: {action!r}"}


def _zoom_validate_webhook(secret: str, ts_ms: str, body: bytes) -> bool:
    """Validate Zoom webhook HMAC-SHA256 signature.
    Zoom sends: x-zm-signature = 'v0=' + HMAC(secret, f'v0:{ts_ms}:{body_str}')
    """
    import hmac as _hm
    if not secret:
        return True   # no secret configured — accept all (dev/testing mode)
    try:
        msg      = f"v0:{ts_ms}:".encode() + body
        key      = secret.encode()
        expected = "v0=" + _hm.new(key, msg, __import__("hashlib").sha256).hexdigest()
        sig      = __import__("flask").request.headers.get("x-zm-signature", "")
        return _hm.compare_digest(sig, expected)
    except Exception:
        return False



def _zoom_hub_proxy_get(url: str, site: str, secret: str) -> dict:
    """HMAC-signed GET from client Flask to hub zoom_hub_data endpoint."""
    import hmac as _hmac
    ts   = time.time()
    hdrs = {"X-Site": site}
    if secret:
        key   = hashlib.sha256(f"{secret}:signing".encode()).digest()
        sig   = _hmac.new(key, f"{ts:.0f}:".encode() + b"", hashlib.sha256).hexdigest()
        nonce = hashlib.md5(os.urandom(8)).hexdigest()[:16]
        hdrs.update({"X-Hub-Sig": sig, "X-Hub-Ts": f"{ts:.0f}", "X-Hub-Nonce": nonce})
    req = urllib.request.Request(url, headers=hdrs, method="GET")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _zoom_hub_proxy_post(url: str, site: str, payload: dict) -> dict:
    """POST from client Flask to hub zoom_hub_action endpoint (approval-only auth)."""
    data = json.dumps(payload, separators=(",", ":")).encode()
    hdrs = {"Content-Type": "application/json", "X-Site": site}
    req  = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read())



# ══════════════════════════════════════════════════════════════════════════════
# NDI video preview helpers (client-side only)
# ══════════════════════════════════════════════════════════════════════════════

# Runtime NDI availability — checked once at import time so that the plugin
# loads cleanly even when ndi-python is not installed.
try:
    import NDIlib as _NDI
    _ndi_available: bool = True
except ImportError:
    _NDI              = None        # type: ignore
    _ndi_available    = False


def _ndi_sources(timeout_s: float = 4.0) -> list:
    """Return a list of NDI source name strings visible on the LAN."""
    if not _ndi_available:
        return []
    _NDI.initialize()
    find = _NDI.find_create_v2()
    try:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            sources = _NDI.find_get_current_sources(find) or []
            if sources:
                time.sleep(0.3)   # let the list stabilise
                sources = _NDI.find_get_current_sources(find) or []
                return [s.ndi_name for s in sources if s.ndi_name]
            time.sleep(0.25)
        return []
    finally:
        _NDI.find_destroy(find)


def _ndi_hls_relay(source_name: str, push_fn, stop_evt) -> None:
    """Receive NDI from *source_name*, encode to HLS via ffmpeg, call
    push_fn(data: bytes, duration: float) for each new TS segment.

    Runs synchronously — call from a daemon thread.
    Only works on POSIX systems (uses os.mkfifo for named pipes).
    """
    import tempfile, shutil, subprocess as _sp

    if not _ndi_available or not hasattr(os, "mkfifo"):
        return

    tmpdir = None
    recv   = None
    ff     = None
    vfh    = None
    afh    = None

    try:
        _NDI.initialize()
        tmpdir = tempfile.mkdtemp(prefix="ss_ndi_")

        # ── Discover source ──────────────────────────────────────────────────
        find     = _NDI.find_create_v2()
        deadline = time.time() + 10
        target   = None
        while time.time() < deadline and not stop_evt.is_set():
            for s in (_NDI.find_get_current_sources(find) or []):
                if s.ndi_name == source_name:
                    target = s
                    break
            if target:
                break
            time.sleep(0.4)
        _NDI.find_destroy(find)
        if target is None or stop_evt.is_set():
            return

        # ── Create receiver ──────────────────────────────────────────────────
        rd               = _NDI.RecvCreateV3()
        rd.color_format  = _NDI.RECV_COLOR_FORMAT_BGRX_BGRA
        recv             = _NDI.recv_create_v3(rd)
        _NDI.recv_connect(recv, target)

        # ── Probe: collect video + audio params from first frames ────────────
        w = h = fps_n = fps_d = ar = nch = None
        for _ in range(500):
            if stop_evt.is_set():
                return
            ft, vf, af, mf = _NDI.recv_capture_v2(recv, 50)
            if ft == _NDI.FRAME_TYPE_VIDEO and w is None:
                w, h  = vf.xres, vf.yres
                fps_n = vf.frame_rate_N
                fps_d = max(vf.frame_rate_D, 1)
                _NDI.recv_free_video_v2(recv, vf)
            elif ft == _NDI.FRAME_TYPE_AUDIO and ar is None:
                ar  = int(af.sample_rate or 48000)
                nch = max(int(af.no_channels or 2), 1)
                _NDI.recv_free_audio_v2(recv, af)
            if w and ar:
                break
        if not w or stop_evt.is_set():
            return

        # ── Named pipes + ffmpeg ─────────────────────────────────────────────
        vpipe  = os.path.join(tmpdir, "v")
        apipe  = os.path.join(tmpdir, "a")
        outdir = os.path.join(tmpdir, "hls")
        os.makedirs(outdir, exist_ok=True)
        os.mkfifo(vpipe)
        os.mkfifo(apipe)

        # Opener threads block until ffmpeg opens the reading side
        _v_ready = threading.Event()
        _a_ready = threading.Event()
        _vb, _ab = [None], [None]
        def _ov(): _vb[0] = open(vpipe, "wb"); _v_ready.set()
        def _oa(): _ab[0] = open(apipe, "wb"); _a_ready.set()
        threading.Thread(target=_ov, daemon=True).start()
        threading.Thread(target=_oa, daemon=True).start()

        ff_cmd = [
            "ffmpeg", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgra",
            "-s", f"{w}x{h}", "-r", f"{fps_n}/{fps_d}", "-i", vpipe,
            "-f", "s16le", "-ar", str(ar), "-ac", str(nch), "-i", apipe,
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
            "-b:v", "2000k", "-maxrate", "2500k", "-bufsize", "4000k",
            "-c:a", "aac", "-b:a", "128k",
            "-f", "hls", "-hls_time", "2", "-hls_list_size", "6",
            "-hls_flags", "delete_segments+independent_segments",
            "-hls_segment_filename", os.path.join(outdir, "seg%05d.ts"),
            os.path.join(outdir, "stream.m3u8"),
        ]
        ff = _sp.Popen(ff_cmd, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
        _v_ready.wait(timeout=10)
        _a_ready.wait(timeout=10)
        vfh = _vb[0]
        afh = _ab[0]
        if not vfh or not afh:
            return

        # ── Frame receive + encode loop ──────────────────────────────────────
        pushed      = set()
        push_clock  = time.time()

        def _push_new():
            for fn in sorted(os.listdir(outdir)):
                if not fn.endswith(".ts") or fn in pushed:
                    continue
                path = os.path.join(outdir, fn)
                try:
                    data = open(path, "rb").read()
                    if data:
                        push_fn(data, 2.0)
                        pushed.add(fn)
                except Exception:
                    pass

        while not stop_evt.is_set():
            ft, vf, af, mf = _NDI.recv_capture_v2(recv, 50)
            try:
                if ft == _NDI.FRAME_TYPE_VIDEO and vf.data is not None:
                    vfh.write(bytes(vf.data)); vfh.flush()
                    _NDI.recv_free_video_v2(recv, vf)
                elif ft == _NDI.FRAME_TYPE_AUDIO and af.data is not None:
                    # Float32 planar (channels × samples) → int16 interleaved
                    import numpy as _np
                    arr = _np.array(af.data, dtype=_np.float32)
                    pcm = (_np.clip(arr, -1.0, 1.0) * 32767.0).astype(_np.int16)
                    afh.write(pcm.T.flatten().tobytes()); afh.flush()
                    _NDI.recv_free_audio_v2(recv, af)
            except (BrokenPipeError, OSError):
                break
            if time.time() - push_clock > 0.5:
                _push_new()
                push_clock = time.time()

        _push_new()   # flush any final segments

    finally:
        try:
            if vfh: vfh.close()
        except Exception:
            pass
        try:
            if afh: afh.close()
        except Exception:
            pass
        if ff is not None:
            try:
                ff.wait(timeout=3)
            except Exception:
                pass
            if ff.poll() is None:
                ff.kill()
        if recv is not None:
            _NDI.recv_destroy(recv)
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)

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


# ── SRS Docker helpers ────────────────────────────────────────────────────────

_SRS_CONTAINER    = "srs"
_SRS_IMAGE        = "ossrs/srs:5"
# Persistent SRS config written to the host filesystem so it survives container
# recreations.  The Start SRS path writes this file before docker run, then
# bind-mounts it into the container at the path SRS reads on startup.
_SRS_HOST_CFG_DIR  = os.path.join(os.path.dirname(_BASE_DIR), "srs")
_SRS_HOST_CFG_PATH = os.path.join(_SRS_HOST_CFG_DIR, "rtc.conf")
_SRS_CONF_CONTENT  = """\
# SRS config — SRT + WebRTC (SRT -> RTMP -> RTC pipeline)
# Written by vMix Caller plugin on Start SRS. Edit here to customise.
listen              1935;
max_connections     1000;
daemon              off;
srs_log_tank        console;

http_server {
    enabled         on;
    listen          8080;
    dir             ./objs/nginx/html;
}

http_api {
    enabled         on;
    listen          1985;
}

stats {
    network         0;
}

srt_server {
    enabled         on;
    listen          10080;
    maxbw           1000000000;
    connect_timeout 4000;
    peerlatency     0;
    recvlatency     0;
    mix_correct     on;
}

rtc_server {
    enabled on;
    listen 8000;
    candidate $CANDIDATE;
}

vhost __defaultVhost__ {
    srt {
        enabled     on;
        srt_to_rtmp on;
    }
    rtc {
        enabled     on;
        rtmp_to_rtc on;
        rtc_to_rtmp off;
    }
    http_remux {
        enabled     on;
        mount       [vhost]/[app]/[stream].flv;
    }
}
"""

# Static docker flags — ports and config bind-mount.
# CANDIDATE env var is added dynamically in _srs_start().
_SRS_DOCKER_FLAGS = [
    "-d", "--name", _SRS_CONTAINER, "--restart", "unless-stopped",
    "-p", "10080:10080/udp",   # SRT input from vMix
    "-p", "8080:8080",          # HTTP server (SRS player page, HLS)
    "-p", "1935:1935",          # RTMP (internal pipeline)
    "-p", "1985:1985",          # HTTP API + WHEP endpoint
    "-p", "8000:8000/udp",     # WebRTC media (SRTP/ICE)
]
_SRS_IMAGE_CMD    = [_SRS_IMAGE, "./objs/srs", "-c", "conf/rtc.conf"]


def _srs_candidate_ip() -> str:
    """Return the primary LAN IPv4 address to pass as SRS CANDIDATE env var.

    Without CANDIDATE, SRS running in Docker bridge mode puts the container's
    internal IP (172.17.0.x) in WebRTC ICE candidates.  The browser on the LAN
    cannot reach that address, so video never connects even though WHEP
    negotiation succeeds.  Passing the real host LAN IP fixes this.
    """
    import socket as _sock
    try:
        s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _docker_cmd() -> list | None:
    """Return base docker command list.

    Tries direct invocation first (works if running as root or user is in
    the docker group).  Falls back to ['sudo', '-n', docker_path] which
    works after a passwordless sudoers entry has been written for docker.
    Returns None if docker is not installed or not accessible either way.
    """
    import shutil as _sh, subprocess as _sp
    d = _sh.which("docker")
    if not d:
        return None
    # Try direct access (root / docker group)
    try:
        if _sp.run([d, "version"], capture_output=True, timeout=4).returncode == 0:
            return [d]
    except Exception:
        pass
    # Try passwordless sudo (works after sudoers entry is written)
    sudo = _sh.which("sudo")
    if sudo:
        try:
            if _sp.run([sudo, "-n", d, "version"],
                       capture_output=True, timeout=4).returncode == 0:
                return [sudo, "-n", d]
        except Exception:
            pass
    return None


def _srs_status() -> dict:
    """Return dict: docker_ok, running, exists, status_text, logs."""
    docker = _docker_cmd()
    if not docker:
        return {"docker_ok": False, "running": False, "exists": False,
                "status_text": "docker not found or not accessible", "logs": ""}
    import subprocess as _sp
    try:
        r = _sp.run(
            docker + ["inspect", "--format", "{{.State.Status}}", _SRS_CONTAINER],
            capture_output=True, text=True, timeout=8,
        )
        if r.returncode != 0:
            return {"docker_ok": True, "running": False, "exists": False,
                    "status_text": "not created", "logs": ""}
        status = r.stdout.strip()
        running = status == "running"
        # Fetch last 30 log lines
        lr = _sp.run(
            docker + ["logs", "--tail", "30", _SRS_CONTAINER],
            capture_output=True, text=True, timeout=8,
        )
        logs = (lr.stdout + lr.stderr).strip()
        return {"docker_ok": True, "running": running, "exists": True,
                "status_text": status, "logs": logs}
    except Exception as e:
        return {"docker_ok": True, "running": False, "exists": False,
                "status_text": "error", "logs": str(e)}


def _srs_write_config() -> None:
    """Write the SRS config to the host filesystem so it can be bind-mounted."""
    import os as _os
    _os.makedirs(_SRS_HOST_CFG_DIR, exist_ok=True)
    with open(_SRS_HOST_CFG_PATH, "w") as _f:
        _f.write(_SRS_CONF_CONTENT)


def _srs_start() -> dict:
    """Start the SRS container. Pulls image on first run.

    Always removes any existing stopped container before recreating so that
    port changes and a fresh CANDIDATE IP take effect.  Writes the SRS config
    to the host first so SRT → RTMP → WebRTC bridging is always enabled.
    Running containers are left alone (use Stop then Start to apply config changes).
    """
    docker = _docker_cmd()
    if not docker:
        return {"ok": False, "msg": "docker not found or not accessible — install Docker first"}
    import subprocess as _sp
    try:
        # If already running, nothing to do
        st = _srs_status()
        if st.get("running"):
            return {"ok": True, "msg": "SRS already running"}

        # Remove any existing stopped container so new port/env/config flags take effect
        if st.get("exists"):
            _sp.run(docker + ["rm", "-f", _SRS_CONTAINER],
                    capture_output=True, timeout=15)

        # Write config with SRT + WebRTC pipeline to host, bind-mount into container
        try:
            _srs_write_config()
            vol_flag = ["-v", f"{_SRS_HOST_CFG_PATH}:/usr/local/srs/conf/rtc.conf:ro"]
        except Exception as _ce:
            vol_flag = []  # fall back to image default (no SRT) with a warning

        # Detect host LAN IP for WebRTC ICE CANDIDATE
        candidate = _srs_candidate_ip()
        run_args = _SRS_DOCKER_FLAGS + vol_flag + ["-e", f"CANDIDATE={candidate}"] + _SRS_IMAGE_CMD

        # Fresh run — may pull image on first use (can take ~60 s)
        r = _sp.run(docker + ["run"] + run_args,
                    capture_output=True, text=True, timeout=180)
        if r.returncode == 0:
            return {"ok": True, "msg": f"SRS container started (CANDIDATE={candidate})"}
        err = (r.stderr or r.stdout or "unknown error").strip()[:300]
        return {"ok": False, "msg": err}
    except Exception as e:
        return {"ok": False, "msg": str(e)[:200]}


def _srs_stop() -> dict:
    """Stop the SRS container (leaves it removable via docker start later)."""
    docker = _docker_cmd()
    if not docker:
        return {"ok": False, "msg": "docker not found"}
    import subprocess as _sp
    try:
        r = _sp.run(docker + ["stop", _SRS_CONTAINER],
                    capture_output=True, text=True, timeout=20)
        if r.returncode == 0:
            return {"ok": True, "msg": "SRS stopped"}
        err = (r.stderr or r.stdout or "").strip()[:200]
        return {"ok": False, "msg": err or "stop failed"}
    except Exception as e:
        return {"ok": False, "msg": str(e)[:200]}


def _docker_install(sudo_password: str = "") -> dict:
    """Install Docker and write a passwordless sudoers entry for docker commands.

    On Linux:
      1. Uses sudo -S to run apt-get install docker.io (or get.docker.com script)
      2. Writes /etc/sudoers.d/signalscope-docker so subsequent docker calls
         work via 'sudo -n docker ...' without any password
      3. Adds the current user to the docker group (takes effect after restart)

    sudo_password must be provided when not running as root.
    """
    import platform, subprocess as _sp, shutil as _sh, os as _os, getpass as _gp
    system = platform.system()
    if system != "Linux":
        return {
            "ok":  False,
            "msg": f"Auto-install only supported on Linux ({system} detected). "
                   "Download Docker Desktop from https://docs.docker.com/get-docker/",
        }

    pw = (sudo_password or "").encode()
    is_root = (_os.geteuid() == 0)

    if not is_root and not pw:
        return {"ok": False,
                "msg": "Sudo password required — enter it in the password field"}

    def _sudo_run(cmd, input_extra=b""):
        """Run a command with sudo -S feeding password, or directly if root."""
        if is_root:
            return _sp.run(cmd, capture_output=True, text=True, timeout=300)
        sudo = _sh.which("sudo")
        if not sudo:
            raise RuntimeError("sudo not found")
        return _sp.run(
            [sudo, "-S"] + cmd,
            input=(pw + b"\n" + input_extra).decode(errors="replace"),
            capture_output=True, text=True, timeout=300,
        )

    env = {**_os.environ, "DEBIAN_FRONTEND": "noninteractive"}

    # ── Step 1: install docker ────────────────────────────────────────────────
    apt = _sh.which("apt-get")
    if apt:
        try:
            _sudo_run([apt, "-y", "update"])
            r = _sudo_run([apt, "-y", "install", "docker.io"])
            if r.returncode != 0:
                out = ((r.stderr or "") + (r.stdout or "")).strip()[:400]
                return {"ok": False, "msg": out or "apt-get install docker.io failed — wrong sudo password?"}
        except Exception as e:
            return {"ok": False, "msg": str(e)[:200]}
    else:
        curl = _sh.which("curl")
        if not curl:
            return {"ok": False,
                    "msg": "apt-get not found and curl not available. Install Docker manually."}
        try:
            r = _sudo_run(["sh", "-c", "curl -fsSL https://get.docker.com | sh"])
            if r.returncode != 0:
                out = ((r.stderr or "") + (r.stdout or "")).strip()[:400]
                return {"ok": False, "msg": out or "get.docker.com install script failed"}
        except Exception as e:
            return {"ok": False, "msg": str(e)[:200]}

    # ── Step 2: write sudoers entry so docker works passwordlessly going forward ─
    if not is_root:
        try:
            _user = _gp.getuser()
        except Exception:
            _user = _os.environ.get("USER", "nobody")
        docker_path = _sh.which("docker") or "/usr/bin/docker"
        sudoers_line = f"{_user} ALL=(ALL) NOPASSWD: {docker_path}\n"
        sudoers_file = "/etc/sudoers.d/signalscope-docker"
        try:
            sudo = _sh.which("sudo")
            r = _sp.run(
                [sudo, "-S", "tee", sudoers_file],
                input=(pw.decode(errors="replace") + "\n" + sudoers_line),
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                _sp.run([sudo, "-S", "chmod", "0440", sudoers_file],
                        input=(pw.decode(errors="replace") + "\n"),
                        capture_output=True, timeout=5)
        except Exception:
            pass  # sudoers write failure is non-fatal — docker still installed

        # ── Step 3: add user to docker group (takes effect after restart) ──────
        try:
            _sudo_run(["usermod", "-aG", "docker", _user])
        except Exception:
            pass

    return {
        "ok":  True,
        "msg": "Docker installed successfully. "
               "SRS Start/Stop will now work via passwordless sudo. "
               "Restart SignalScope to also gain direct docker group access.",
    }


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
                    for _k in ("vmix_ip", "vmix_port", "vmix_input", "bridge_url",
                               "preview_mode", "ndi_source"):
                        if _k in cmd:
                            updates[_k] = cmd[_k]
                    if updates:
                        _save_cfg(updates)
                        cfg = _load_cfg()   # reload so next iteration uses new values
                elif fn in ("srs_start", "srs_stop"):
                    result = _srs_start() if fn == "srs_start" else _srs_stop()
                    try:
                        _hub_post(f"{hub_url}/api/vmixcaller/report", site, secret, {
                            "cmd_result": {
                                "seq":  cmd["seq"],
                                "ok":   result["ok"],
                                "resp": (result.get("msg") or "")[:120],
                            },
                        })
                    except Exception:
                        pass
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
                _srs_st = _srs_status()
                # Include instance list (id/name/bridge_url only — no vmix
                # credentials) so the hub can expose them to other plugins
                # (e.g. Brand Screen video source picker).
                _inst_summary = [
                    {"id": i.get("id", ""), "name": i.get("name", "Default"),
                     "bridge_url": i.get("bridge_url", "")}
                    for i in (cfg.get("instances") or [])
                ]
                report: dict = {
                    "ts":          now,
                    "ok":          ok_xml,
                    "vmix_ip":     cfg.get("vmix_ip",  "127.0.0.1"),
                    "vmix_port":   cfg.get("vmix_port", 8088),
                    "relay_active": _relay_event.is_set(),
                    "instances":   _inst_summary,
                    "srs": {
                        "docker_ok":   _srs_st["docker_ok"],
                        "running":     _srs_st["running"],
                        "exists":      _srs_st.get("exists", False),
                        "status_text": _srs_st.get("status_text", ""),
                    },
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
            cfg          = _load_cfg()
            bridge_url   = (cfg.get("bridge_url")   or "").strip()
            preview_mode = (cfg.get("preview_mode") or "srt").strip()
            ndi_source   = (cfg.get("ndi_source")   or "").strip()
            app_cfg      = monitor.app_cfg
            site         = (getattr(getattr(app_cfg, "hub", None), "site_name", "") or "").strip()
            secret       = (getattr(getattr(app_cfg, "hub", None), "secret_key",  "") or "").strip()

            # ── NDI mode: generate HLS segments from NDI source ──────────
            if preview_mode == "ndi":
                if not ndi_source or not site:
                    time.sleep(5)
                    continue
                if not _ndi_available:
                    time.sleep(30)
                    continue
                _ndi_kill = threading.Event()
                def _ndi_push(data, dur,
                              _site=site, _secret=secret):
                    try:
                        _push(_site, _secret, data, dur)
                    except Exception:
                        pass
                _ndi_t = threading.Thread(
                    target=_ndi_hls_relay,
                    args=(ndi_source, _ndi_push, _ndi_kill),
                    daemon=True,
                )
                _ndi_t.start()
                while _relay_event.is_set() and _ndi_t.is_alive():
                    time.sleep(1)
                _ndi_kill.set()
                _ndi_t.join(timeout=8)
                if _relay_event.is_set():
                    time.sleep(5)  # source lost; pause before retry
                continue

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
header{display:flex;align-items:center;gap:12px;padding:0}
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
.mtg-id{font-size:11px;color:var(--mu)}
/* vMix instance pills */
.inst-strip{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
.inst-pill{background:var(--sur);border:1.5px solid var(--bor);border-radius:20px;color:var(--mu);font-size:12px;font-weight:600;padding:5px 14px;cursor:pointer;font-family:inherit;transition:border-color .2s,color .2s,background .2s}
.inst-pill:hover{border-color:var(--acc);color:var(--tx)}
.inst-pill.active{background:rgba(23,168,255,.12);border-color:var(--acc);color:var(--acc)}
.inst-act-label{font-size:11px;color:var(--mu);margin-bottom:5px}"""

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
var _inMeeting=false,_selfMuted=false;
function setMeetingState(v){
  _inMeeting=v;
  document.querySelectorAll('.meeting-card').forEach(function(c){c.className=v?'card meeting-card in-meeting':'card meeting-card';});
  if(!v){_selfMuted=false;resetCallBtns();}
}
function resetCallBtns(){
  var mb=document.getElementById('mute-btn');
  if(mb)mb.textContent='\uD83D\uDD07 Mute Self';
}

// vMix API: ZoomJoinMeeting Value = "MeetingID,Password" (comma-separated).
// Display name is set by the Zoom account inside vMix, not via API.
var _lastJoin={mid:'',pass:''};
function joinWith(mid,pass){
  // Strip spaces — users often copy meeting IDs as "123 456 7890"
  mid=(mid||'').replace(/\s+/g,'');
  if(!mid){showMsg('Enter a Meeting ID',false);return;}
  _lastJoin={mid:mid,pass:pass||''};
  if(typeof _updateReconnectBtn==='function')_updateReconnectBtn();
  sendCmd('ZoomJoinMeeting',mid+','+(pass||''))
    .then(function(d){
      if(d.ok){
        // If vMix returned a non-empty body it may be a silent error — show it
        var vr=(d.response||'').trim();
        var isErr=vr&&(vr.toLowerCase().indexOf('error')>=0||vr.toLowerCase().indexOf('false')>=0);
        showMsg(vr||'Joining…',!isErr);
        setMeetingState(true);
      }
    });
}
function leaveMeeting(){
  sendCmd('ZoomLeaveMeeting').then(function(d){if(d.ok){
    showMsg('Left meeting',true);
    setMeetingState(false);
    if(typeof setActiveMeeting==='function')setActiveMeeting('','');
  }});
}
function reconnect(){
  if(!_lastJoin.mid){showMsg('No previous meeting to reconnect to',false);return;}
  showMsg('Reconnecting\u2026',true);
  sendCmd('ZoomJoinMeeting',_lastJoin.mid+','+_lastJoin.pass)
    .then(function(d){if(d.ok)setMeetingState(true);});
}
function muteSelf(){
  // ZoomMuteSelf mutes; ZoomUnMuteSelf unmutes (separate official API functions)
  var fn=_selfMuted?'ZoomUnMuteSelf':'ZoomMuteSelf';
  sendCmd(fn).then(function(d){
    if(d.ok){_selfMuted=!_selfMuted;var b=document.getElementById('mute-btn');if(b)b.textContent=_selfMuted?'\uD83D\uDD0A Unmute Self':'\uD83D\uDD07 Mute Self';showMsg(_selfMuted?'Muted':'Unmuted',true);}
  });
}


// ── Keyboard shortcuts ────────────────────────────────────────────────────────
document.addEventListener('keydown',function(e){
  var t=(document.activeElement||{}).tagName||'';
  if(t==='INPUT'||t==='TEXTAREA'||t==='SELECT')return;
  if(e.key==='m'||e.key==='M'){e.preventDefault();muteSelf();}
});

// ── Delegated click handler (CSP-safe — replaces all onclick= attributes) ─────
// All buttons use data-action= instead of onclick= so no CSP hash is required.
// Functions guarded with typeof so hub-only / presenter-only actions don't error.
document.addEventListener('click',function(e){
  var btn=e.target.closest('[data-action]');
  if(!btn)return;
  var a=btn.dataset.action;
  if(a==='muteSelf')                                          muteSelf();
  else if(a==='leaveMeeting')                                 leaveMeeting();
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
  else if(a==='switchInstance'&&typeof switchInstance==='function')switchInstance(btn);
  else if(a==='zoomJoin'      &&typeof zoomJoin==='function')       zoomJoin(btn);
  else if(a==='zoomEnd'       &&typeof zoomEnd==='function')        zoomEnd(btn);
  else if(a==='zoomAddSaved'  &&typeof zoomAddSaved==='function')   zoomAddSaved(btn);
  else if(a==='toggleZoomCreate'&&typeof toggleZoomCreate==='function')toggleZoomCreate();
  else if(a==='cancelZoomCreate'&&typeof cancelZoomCreate==='function')cancelZoomCreate();
  else if(a==='createZoomNow' &&typeof createZoomNow==='function')  createZoomNow();
  else if(a==='refreshZoom'   &&typeof loadZoomData==='function')   loadZoomData(true);
  else if(a==='saveZoomCreds' &&typeof saveZoomCreds==='function')  saveZoomCreds();
  else if(a==='discoverNdi'   &&typeof discoverNdi==='function')    discoverNdi();
  else if(a==='zoomMuteParticipant'   &&typeof zoomMuteParticipant==='function')   zoomMuteParticipant(btn);
  else if(a==='zoomRemoveParticipant' &&typeof zoomRemoveParticipant==='function') zoomRemoveParticipant(btn);
  else if(a==='zoomAdmitParticipant'  &&typeof zoomAdmitParticipant==='function')  zoomAdmitParticipant(btn);
  else if(a==='zoomAdmitAll'          &&typeof zoomAdmitAll==='function')          zoomAdmitAll();
  else if(a==='loadZoomParticipants'  &&typeof loadZoomParticipants==='function')  loadZoomParticipants(true);
  else if(a==='copyWebhookUrl'        &&typeof copyWebhookUrl==='function')        copyWebhookUrl();
  else if(a==='newInstance'  &&typeof newInstance==='function')   newInstance();
  else if(a==='saveInstance' &&typeof saveInstance==='function')  saveInstance();
  else if(a==='deleteInstance'&&typeof deleteInstance==='function')deleteInstance();
});

// ── Shared instance form helper (used by hub and client pages) ───────────────
function _populateInstForm(inst){
  if(!inst)return;
  var flds={
    'inst-name':  inst.name||'',
    'vmix-ip':    inst.vmix_ip||'127.0.0.1',
    'vmix-port':  inst.vmix_port||8088,
    'vmix-input': inst.vmix_input!=null?inst.vmix_input:'1',
    'bridge-url': inst.bridge_url||'',
    'ndi-source': inst.ndi_source||''
  };
  Object.keys(flds).forEach(function(id){
    var el=document.getElementById(id);if(el)el.value=flds[id];
  });
  var _pm=inst.preview_mode||'srt';
  var _pmr=document.querySelector('input[name="preview-mode"][value="'+_pm+'"]');
  if(_pmr)_pmr.checked=true;
  _syncPreviewMode(_pm);
}

// ── NDI preview mode helpers ──────────────────────────────────────────────────
function _syncPreviewMode(pm){
  var sf=document.getElementById('srt-fields');
  var nf=document.getElementById('ndi-fields');
  if(sf)sf.style.display=pm==='ndi'?'none':'';
  if(nf)nf.style.display=pm==='ndi'?'':'none';
  // Sync any radio not yet checked (called on page load via _populateInstForm)
  var r=document.querySelector('input[name="preview-mode"][value="'+pm+'"]');
  if(r&&!r.checked)r.checked=true;
}
document.addEventListener('change',function(e){
  if(e.target&&e.target.name==='preview-mode')_syncPreviewMode(e.target.value);
});
function discoverNdi(){
  var btn=document.querySelector('[data-action="discoverNdi"]');
  if(btn){btn.disabled=true;btn.textContent='Scanning…';}
  fetch('/api/vmixcaller/ndi_sources',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      if(btn){btn.disabled=false;btn.textContent='Discover';}
      var srcs=d.sources||[];
      if(!srcs.length){showMsg('No NDI sources found on LAN',false);return;}
      var el=document.getElementById('ndi-source');
      if(!el)return;
      // Build a temporary select that copies the chosen value into the text input
      var sel=document.createElement('select');
      sel.style.cssText='background:#0d1e40;border:1px solid var(--bor);border-radius:6px;color:var(--tx);padding:4px 6px;font-size:12px;margin-left:4px';
      sel.innerHTML='<option value="">— choose source —</option>'+srcs.map(function(s){
        return '<option value="'+_esc(s)+'">'+_esc(s)+'</option>';
      }).join('');
      sel.onchange=function(){
        if(sel.value){el.value=sel.value;}
        sel.parentNode.removeChild(sel);
      };
      el.parentNode.appendChild(sel);
      sel.focus();
    })
    .catch(function(){
      if(btn){btn.disabled=false;btn.textContent='Discover';}
      showMsg('NDI discovery request failed',false);
    });
}

// ── Zoom API meetings ─────────────────────────────────────────────────────────
var _zAutoT=null;
function loadZoomData(force){
  var url='/api/vmixcaller/zoom_data'+(force?'?refresh=1':'');
  fetch(url,{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      var conn=document.getElementById('zoom-connected');
      var ncfg=document.getElementById('zoom-not-cfg');
      if(conn)conn.style.display=d.configured?'':'none';
      if(ncfg)ncfg.style.display=d.configured?'none':'';
      _renderZoomMtgs(d.meetings||[],d.configured);
    }).catch(function(){});
  clearTimeout(_zAutoT);
  _zAutoT=setTimeout(function(){loadZoomData(false);},60000);
}
function _renderZoomMtgs(meetings,configured){
  var el=document.getElementById('zoom-meeting-list');
  if(!el)return;
  if(!configured){el.innerHTML='<div class="empty-note">Zoom API not configured on hub — add credentials in hub settings</div>';return;}
  if(!meetings.length){el.innerHTML='<div class="empty-note">No upcoming meetings found</div>';return;}
  // Auto-select if exactly one meeting is live and none manually chosen
  if(typeof setActiveMeeting==='function'&&!_activeMeetingId){
    var _livM=meetings.filter(function(m){return m.status==='started';});
    if(_livM.length===1)setActiveMeeting(_livM[0].id,_livM[0].topic||'');
  }
  el.innerHTML=meetings.map(function(m){
    var live=m.status==='started';
    var ts=m.start_time?_zFmt(m.start_time):'';
    return '<div class="mtg-row">'
      +'<span class="dot '+(live?'dok':'dwn')+'" style="flex-shrink:0" title="'+(live?'Live':'Scheduled')+'"></span>'
      +'<div style="flex:1;min-width:0;margin:0 6px">'
        +'<div class="mtg-name">'+_esc(m.topic)+'</div>'
        +'<div class="mtg-id">ID: '+_esc(m.id)+(ts?' · '+_esc(ts):'')+(live?' <b style="color:var(--ok)">LIVE</b>':'')+'</div>'
      +'</div>'
      +'<button class="btn bp bs" data-action="zoomJoin" data-mid="'+_esc(m.id)+'" data-pass="'+_esc(m.password||'')+'" data-topic="'+_esc(m.topic||'')+'">Join</button>'
      +(live?'<button class="btn bd bs" data-action="zoomEnd" data-mid="'+_esc(m.id)+'" title="End for all">End</button>':'')
      +'<button class="btn bg bs" data-action="zoomAddSaved" data-mid="'+_esc(m.id)+'" data-pass="'+_esc(m.password||'')+'" data-topic="'+_esc(m.topic)+'" title="Save to presets for Presenter View">+Save</button>'
      +'</div>';
  }).join('');
}
function _zFmt(iso){
  try{var d=new Date(iso);return d.toLocaleDateString([],{weekday:'short',month:'short',day:'numeric'})+' '+d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});}
  catch(e){return iso;}
}
function toggleZoomCreate(){
  var f=document.getElementById('zoom-create-form');
  if(f)f.style.display=f.style.display==='none'?'':'none';
}
function cancelZoomCreate(){var f=document.getElementById('zoom-create-form');if(f)f.style.display='none';}
function createZoomNow(){
  var topic=((document.getElementById('zm-topic')||{}).value||'').trim();
  var pass=((document.getElementById('zm-pass')||{}).value||'').trim();
  var dur=parseInt(((document.getElementById('zm-dur')||{}).value)||'60')||60;
  var wr=!!(document.getElementById('zm-waiting-room')||{}).checked;
  _post('/api/vmixcaller/zoom_action',{action:'create',topic:topic,passcode:pass,duration:dur,waiting_room:wr})
    .then(function(d){
      if(d.ok){
        showMsg('Meeting created — #'+d.meeting_id+(d.password?' (passcode: '+d.password+')':''),true);
        cancelZoomCreate();
        joinWith(d.meeting_id,d.password||'');
        if(typeof setActiveMeeting==='function')setActiveMeeting(d.meeting_id,topic||'New Meeting');
        setTimeout(function(){loadZoomData(true);},2000);
      } else showMsg(d.error||'Create failed',false);
    }).catch(function(e){showMsg('Error: '+e,false);});
}
function zoomJoin(btn){
  if(typeof setActiveMeeting==='function')setActiveMeeting(btn.dataset.mid||'',btn.dataset.topic||'');
  joinWith(btn.dataset.mid||'',btn.dataset.pass||'');
}
function zoomEnd(btn){
  if(!confirm('End this meeting for all participants?'))return;
  _post('/api/vmixcaller/zoom_action',{action:'end',meeting_id:btn.dataset.mid})
    .then(function(d){
      if(d.ok){showMsg('Meeting ended',true);setTimeout(function(){loadZoomData(true);},1500);}
      else showMsg(d.error||'Failed to end meeting',false);
    }).catch(function(e){showMsg('Error: '+e,false);});
}
function zoomAddSaved(btn){
  var name=(btn.dataset.topic||'Meeting').substring(0,40);
  var mid=btn.dataset.mid||'';var pass=btn.dataset.pass||'';
  if(!mid){showMsg('No meeting ID',false);return;}
  _post('/api/vmixcaller/meetings',{name:name,id:mid,pass:pass,display_name:''})
    .then(function(d){
      if(d.ok){showMsg('"'+_esc(name)+'" added to saved meetings',true);if(typeof loadMeetings==='function')loadMeetings();}
      else showMsg(d.error||'Save failed',false);
    }).catch(function(e){showMsg('Error: '+e,false);});
}
// Hub-only: save Zoom credentials and immediately test them
function saveZoomCreds(){
  var aid=((document.getElementById('zoom-account-id')||{}).value||'').trim();
  var cid=((document.getElementById('zoom-client-id')||{}).value||'').trim();
  var cs=((document.getElementById('zoom-client-secret')||{}).value||'').trim();
  var whs=((document.getElementById('zoom-webhook-secret')||{}).value||'').trim();
  _post('/api/vmixcaller/zoom_credentials',{zoom_account_id:aid,zoom_client_id:cid,zoom_client_secret:cs,zoom_webhook_secret:whs})
    .then(function(d){
      if(d.ok){
        showMsg('Credentials saved — testing…',true);
        fetch('/api/vmixcaller/zoom_status',{credentials:'same-origin'}).then(function(r){return r.json();}).then(function(s){
          var dot=document.getElementById('zoom-status-dot');
          var txt=document.getElementById('zoom-status-txt');
          if(dot)dot.className='dot '+(s.ok?'dok':'dal');
          if(txt)txt.textContent=s.ok?('✓ '+s.name+' ('+s.email+')'):('Auth failed: '+(s.error||''));
          if(s.ok){loadZoomData(true);}
        }).catch(function(){});
      } else showMsg(d.error||'Save failed',false);
    }).catch(function(e){showMsg('Error: '+e,false);});
}

// ── Phase 2: Zoom participant management ──────────────────────────────────────
var _activeMeetingId    = '';
var _activeMeetingTopic = '';
var _partsAutoT         = null;

function setActiveMeeting(mid, topic) {
  _activeMeetingId    = (mid || '').replace(/\s+/g, '');
  _activeMeetingTopic = topic || '';
  clearTimeout(_partsAutoT);
  _partsAutoT = null;
  var card = document.getElementById('zoom-parts-card');
  var lbl  = document.getElementById('zoom-parts-meeting-label');
  if (card) card.style.display = _activeMeetingId ? '' : 'none';
  if (lbl)  lbl.textContent    = _activeMeetingTopic
                                    ? _esc(_activeMeetingTopic)
                                    : (_activeMeetingId ? 'ID ' + _activeMeetingId : '');
  if (_activeMeetingId) {
    loadZoomParticipants(true);
  } else {
    var pl = document.getElementById('zoom-parts-list');
    var ws = document.getElementById('zoom-waiting-section');
    if (pl) pl.innerHTML = '<div class="empty-note">Join a meeting to see Zoom participants</div>';
    if (ws) ws.style.display = 'none';
  }
}

function loadZoomParticipants(force) {
  if (!_activeMeetingId) return;
  clearTimeout(_partsAutoT);
  _partsAutoT = null;
  fetch('/api/vmixcaller/zoom_participants?meeting_id=' + encodeURIComponent(_activeMeetingId)
        + (force ? '&refresh=1' : ''), {credentials: 'same-origin'})
    .then(function(r) { return r.json(); })
    .then(function(d) { renderZoomParticipants(d); })
    .catch(function() {})
    .then(function() {
      if (_activeMeetingId)
        _partsAutoT = setTimeout(function() { loadZoomParticipants(false); }, 15000);
    });
}

function renderZoomParticipants(data) {
  var pl  = document.getElementById('zoom-parts-list');
  var ws  = document.getElementById('zoom-waiting-section');
  var wl  = document.getElementById('zoom-waiting-list');
  var pc  = document.getElementById('zoom-parts-count');
  var wc  = document.getElementById('zoom-waiting-count');
  if (!pl) return;
  var parts   = data.participants || [];
  var waiting = data.waiting      || [];
  var mid     = _activeMeetingId;
  if (pc) pc.textContent = parts.length ? '(' + parts.length + ' in call)' : '';
  // Waiting room
  if (ws) ws.style.display = waiting.length ? '' : 'none';
  if (wc) wc.textContent = waiting.length ? '(' + waiting.length + ' waiting)' : '';
  if (wl) {
    wl.innerHTML = waiting.length ? waiting.map(function(p) {
      return '<div class="pi">'
        + '<span class="pn">' + _esc(p.name || 'Unknown') + '</span>'
        + '<button class="btn bw bs" data-action="zoomAdmitParticipant"'
        + ' data-pid="' + _esc(p.id) + '" data-mid="' + _esc(mid) + '">Admit</button>'
        + '</div>';
    }).join('') : '';
  }
  // Live participants
  if (!parts.length) {
    pl.innerHTML = data.error
      ? '<div class="empty-note" style="color:var(--al)">' + _esc(data.error) + '</div>'
      : '<div class="empty-note">No participants in meeting</div>';
    return;
  }
  pl.innerHTML = parts.map(function(p) {
    var safe      = _esc(p.name || 'Unknown');
    var audioIco  = p.muted ? '🔇' : '🔊';
    var videoIco  = p.video ? ' 📹' : '';
    var hostBadge = p.is_host ? '<span class="pbadge air">HOST</span>' : '';
    var muteLbl   = p.muted ? 'Req. Unmute' : 'Mute';
    return '<div class="pi">'
      + '<span style="font-size:13px;margin-right:4px;flex-shrink:0">' + audioIco + videoIco + '</span>'
      + '<span class="pn">' + safe + '</span>'
      + hostBadge
      + '<button class="btn bg bs" data-action="zoomMuteParticipant"'
      + ' data-pid="' + _esc(p.id) + '" data-mid="' + _esc(mid) + '"'
      + ' data-muted="' + (p.muted ? '1' : '0') + '">' + muteLbl + '</button>'
      + (p.is_host ? '' :
        '<button class="btn bd bs" data-action="zoomRemoveParticipant"'
        + ' data-pid="' + _esc(p.id) + '" data-mid="' + _esc(mid) + '"'
        + ' data-name="' + safe + '">Remove</button>')
      + '</div>';
  }).join('');
}

function zoomMuteParticipant(btn) {
  var mid    = btn.dataset.mid || _activeMeetingId;
  var pid    = btn.dataset.pid;
  var isMuted = btn.dataset.muted === '1';
  var wantMute = !isMuted;
  _post('/api/vmixcaller/zoom_action',
        {action: 'mute_participant', meeting_id: mid, participant_id: pid, mute: wantMute})
    .then(function(d) {
      if (d.ok) {
        showMsg(wantMute ? 'Muted' : 'Unmute request sent', true);
        btn.dataset.muted = wantMute ? '1' : '0';
        btn.textContent   = wantMute ? 'Req. Unmute' : 'Mute';
        setTimeout(function() { loadZoomParticipants(true); }, 1500);
      } else { showMsg(d.error || 'Failed', false); }
    }).catch(function(e) { showMsg('Error: ' + e, false); });
}

function zoomRemoveParticipant(btn) {
  var name = btn.dataset.name || 'this participant';
  if (!confirm('Remove ' + name + ' from the meeting?')) return;
  var mid = btn.dataset.mid || _activeMeetingId;
  var pid = btn.dataset.pid;
  _post('/api/vmixcaller/zoom_action',
        {action: 'remove_participant', meeting_id: mid, participant_id: pid})
    .then(function(d) {
      if (d.ok) { showMsg(name + ' removed', true); setTimeout(function() { loadZoomParticipants(true); }, 1500); }
      else       { showMsg(d.error || 'Failed to remove', false); }
    }).catch(function(e) { showMsg('Error: ' + e, false); });
}

function zoomAdmitParticipant(btn) {
  var mid  = btn.dataset.mid || _activeMeetingId;
  var pid  = btn.dataset.pid;
  var pRow = btn.closest('.pi');
  var name = pRow ? (pRow.querySelector('.pn') || {}).textContent || 'participant' : 'participant';
  _post('/api/vmixcaller/zoom_action',
        {action: 'admit_participant', meeting_id: mid, participant_id: pid})
    .then(function(d) {
      if (d.ok) { showMsg(name + ' admitted', true); setTimeout(function() { loadZoomParticipants(true); }, 1500); }
      else       { showMsg(d.error || 'Failed to admit', false); }
    }).catch(function(e) { showMsg('Error: ' + e, false); });
}

function zoomAdmitAll() {
  if (!_activeMeetingId) return;
  _post('/api/vmixcaller/zoom_action', {action: 'admit_all', meeting_id: _activeMeetingId})
    .then(function(d) {
      if (d.ok) {
        var n = d.admitted || 0;
        showMsg(n + ' participant' + (n === 1 ? '' : 's') + ' admitted', true);
        setTimeout(function() { loadZoomParticipants(true); }, 1500);
      } else { showMsg(d.error || 'Failed to admit all', false); }
    }).catch(function(e) { showMsg('Error: ' + e, false); });
}

// ── Phase 3: Webhook URL helper ───────────────────────────────────────────────
function copyWebhookUrl() {
  var el = document.getElementById('zoom-webhook-url-display');
  if (!el) return;
  var url = el.value;
  if (navigator.clipboard) {
    navigator.clipboard.writeText(url).then(function() { showMsg('Webhook URL copied', true); });
  } else {
    try { el.select(); document.execCommand('copy'); showMsg('Webhook URL copied', true); }
    catch(e) { showMsg('Copy failed — select URL manually', false); }
  }
}
// Populate webhook URL field with current origin once DOM is ready
document.addEventListener('DOMContentLoaded', function() {
  var el = document.getElementById('zoom-webhook-url-display');
  if (el) el.value = window.location.origin + '/api/vmixcaller/zoom_webhook';
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
    <div class="hdr-title">{% if selected_inst %}{{selected_inst.name|e}}{% else %}Caller{% endif %}</div>
    <div class="hdr-sub" id="hdr-sub">{% if selected_inst %}Ready to join{% else %}Select your studio{% endif %}</div>
  </div>
  {% if selected_inst and instances|length > 1 %}
  <a href="/hub/vmixcaller/presenter" class="hdr-powered" style="font-size:12px">&#9664; Studios</a>
  {% else %}
  <a href="/" class="hdr-powered">SignalScope</a>
  {% endif %}
</header>

<main>
<div id="msg"></div>

{% if selected_inst %}
<!-- ── In-call bar (hidden until in meeting) ─────────────────────────── -->
<div id="call-bar">
  <span class="call-badge">● ON CALL</span>
  <span id="onair-badge" class="onair-badge" style="display:none">📡 ON AIR</span>
  <button class="call-btn" id="mute-btn"  data-action="muteSelf">🔇 Mute Self</button>
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
      <div class="field">
        <label class="fl">Passcode</label>
        <input type="password" id="mtg-pass" placeholder="••••••">
      </div>
      <button class="join-manual join-btn" data-action="joinManual">📞 Join Meeting</button>
    </div>
  </div>
</div>

<div class="kbd-hints"><kbd>M</kbd> mute &nbsp;·&nbsp; <kbd>C</kbd> camera</div>

{% else %}
<!-- ── Studio picker ──────────────────────────────────────────────────── -->
{% if instances %}
<div class="section" style="padding-top:36px">
  <div class="section-title">Your Studios <span>&#8212; select to begin</span></div>
</div>
<div class="mtg-grid" style="margin-top:4px">
  {% set av_colors=[['#1a7fe8','#17a8ff'],['#16a047','#22c55e'],['#9333e8','#a855f7'],['#c87f0a','#f59e0b'],['#d91a6e','#ec4899'],['#0d9488','#14b8a6'],['#c2440f','#f97316']] %}
  {% for inst in instances %}
  {% set c = av_colors[loop.index0 % av_colors|length] %}
  <div class="mtg-card">
    <div class="mtg-top">
      <div class="mtg-av" style="background:linear-gradient(135deg,{{c[0]}},{{c[1]}})">{{(inst.name[:1])|upper|e}}</div>
      <div class="mtg-meta">
        <div class="mtg-name">{{inst.name|e}}</div>
        <div class="mtg-id-text">vMix &#64; {{inst.vmix_ip|e}}{% if inst.vmix_port and inst.vmix_port != 8088 %}:{{inst.vmix_port}}{% endif %}</div>
      </div>
    </div>
    <a href="/hub/vmixcaller/presenter?inst={{inst.id|e}}" class="join-big">&#127897; Enter Studio</a>
  </div>
  {% endfor %}
</div>
{% else %}
<div class="no-meetings" style="margin-top:36px">
  No studios configured yet.<br>
  Ask your engineer to add vMix instances in the hub settings.
</div>
{% endif %}
{% endif %}

</main>
<script nonce="{{csp_nonce()}}">
""" + _JS_HELPERS + r"""

// ── Presenter-specific ────────────────────────────────────────────────────────
var _videoUrl      = {{video_url_json|safe}};
var _presInstData  = {{selected_inst_json|safe}};
var _presInstId    = _presInstData.id    || '';
var _presInstInput = _presInstData.vmix_input || 1;

// Route all vMix commands to the selected instance's vmix_input
if (_presInstId) {
  sendCmd = function(fn, value) {
    return _post('/api/vmixcaller/function', {fn: fn, value: value, input: _presInstInput})
      .then(function(d) {
        if (!d.ok) showMsg(d.error || 'Error queuing command', false);
        return d;
      }).catch(function(e) { showMsg('Error: ' + e, false); return {ok: false}; });
  };
}

// Switch active vMix instance and reload video preview
function switchInstance(btn){
  var id=btn.dataset.id;
  document.querySelectorAll('.inst-pill').forEach(function(p){p.classList.toggle('active',p.dataset.id===id);});
  _post('/api/vmixcaller/instances/'+encodeURIComponent(id)+'/activate',{})
    .then(function(d){
      if(d.ok){
        fetch('/api/vmixcaller/video_url',{credentials:'same-origin'})
          .then(function(r){return r.json();})
          .then(function(v){_videoUrl=v.url||'';if(_videoUrl)initPreview(_videoUrl);})
          .catch(function(){});
      }
    }).catch(function(){});
}

function joinSaved(btn){
  joinWith(btn.dataset.mid, btn.dataset.pass);
}
function joinManual(){
  var mid =(document.getElementById('mtg-id').value||'').trim();
  var pass=(document.getElementById('mtg-pass').value||'').trim();
  joinWith(mid, pass);
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
    <span><kbd>M</kbd> mute</span>
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
        <p style="margin-bottom:6px">If Docker is installed on this machine, use the <strong style="color:var(--acc)">SRS Server card below</strong> — it starts the container with the correct config automatically (SRT + WebRTC pipeline). To run manually, first create the config file then mount it:</p>
        <pre style="background:#0d1e40;border:1px solid var(--bor);border-radius:6px;padding:8px 10px;font-size:11px;color:#7dd3fc;white-space:pre-wrap;margin:6px 0">docker run -d --name srs --restart unless-stopped \
  -p 10080:10080/udp -p 8080:8080 -p 1935:1935 \
  -p 1985:1985 -p 8000:8000/udp \
  -e CANDIDATE=&lt;LAN_IP_OF_THIS_MACHINE&gt; \
  -v /opt/signalscope/srs/rtc.conf:/usr/local/srs/conf/rtc.conf:ro \
  ossrs/srs:5 ./objs/srs -c conf/rtc.conf</pre>
        <p style="font-size:11px;color:var(--wn);margin-top:0;margin-bottom:6px">⚠ The default <code>rtc.conf</code> in the SRS image does <strong>not</strong> have SRT enabled — the bind-mount above supplies a corrected config. The <strong>Start SRS</strong> button writes this config automatically. Without it vMix packets arrive but SRS silently discards them.</p>
        <p style="margin-bottom:4px">In vMix → Zoom input → <strong>Output</strong> → enable SRT (<strong>Type: Caller</strong>):</p>
        <ul style="padding-left:16px;margin-bottom:6px">
          <li><strong style="color:var(--tx)">Hostname</strong> — LAN IP of the SRS machine</li>
          <li><strong style="color:var(--tx)">Port</strong> — <code style="background:#0d1e40;padding:1px 5px;border-radius:3px">10080</code></li>
          <li><strong style="color:var(--tx)">Stream ID</strong> — <code style="background:#0d1e40;padding:1px 5px;border-radius:3px">#!::r=live/caller,m=publish</code></li>
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

    <!-- SRS Server management -->
    <div class="card" id="srs-card">
      <div class="ch">🐳 SRS Bridge
        <span id="srs-badge" style="margin-left:8px;font-size:10px;padding:2px 8px;border-radius:10px;font-weight:700"></span>
      </div>
      <div class="cb">
        <div id="srs-no-site" style="font-size:12px;color:var(--mu)">Select a site above to manage its SRS bridge.</div>
        <div id="srs-site-panel" style="display:none">
          <div style="font-size:11px;color:var(--mu);margin-bottom:8px">Managing SRS on: <strong id="srs-site-label" style="color:var(--tx)"></strong></div>
          <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
            <div style="flex:1;min-width:160px">
              <div style="font-size:12px;color:var(--mu);margin-bottom:2px">Container status</div>
              <div id="srs-status-text" style="font-size:13px;color:var(--tx)">Waiting for report…</div>
            </div>
            <button class="btn bp bs" id="srs-start-btn" data-srs-action="start" style="display:none">▶ Start SRS</button>
            <button class="btn bd bs" id="srs-stop-btn" data-srs-action="stop" style="display:none">■ Stop SRS</button>
          </div>
          <div id="srs-install-docker-note" style="display:none;font-size:11px;color:var(--wn);margin-top:4px">Docker not found on this site — open the <strong>client page</strong> for this site to install it.</div>
          <div id="srs-msg" style="display:none;margin-top:8px;padding:6px 10px;border-radius:6px;font-size:12px"></div>
          <p style="font-size:11px;color:var(--mu);margin-top:10px;margin-bottom:0">
            Queues Start/Stop commands to the selected site. Status updates every ~12 s when the client reports back.
            Docker must be installed on the client machine. First start pulls the image (~1 min).
          </p>
        </div>
      </div>
    </div>
  </div>

  <!-- ── RIGHT: controls ────────────────────────────────────────────────────── -->
  <div>

    <!-- Site & Instance -->
    <div class="card">
      <div class="ch">🔌 Site &amp; Instance</div>
      <div class="cb">

        <!-- Site selector -->
        <div class="field">
          <label class="fl">vMix Site (SignalScope node)</label>
          <select id="target-site">
            <option value="">— select site —</option>
            {% for s in sites %}
            <option value="{{s|e}}"{% if s==cfg.target_site %} selected{% endif %}>{{s|e}}</option>
            {% endfor %}
          </select>
        </div>

        <hr style="border:none;border-top:1px solid var(--bor);margin:10px 0 12px">

        <!-- Instance selector row -->
        <div class="inst-act-label">vMix Instance</div>
        <div style="display:flex;gap:6px;margin-bottom:12px">
          <select id="inst-sel" style="flex:1">
            {% for inst in instances %}
            <option value="{{inst.id|e}}"{% if inst.id==active_instance_id %} selected{% endif %}>{{inst.name|e}}</option>
            {% endfor %}
          </select>
          <button class="btn bg bs" data-action="newInstance" title="Add new instance">+&nbsp;New</button>
          <button class="btn bd bs" id="del-inst-btn" data-action="deleteInstance" title="Delete this instance">🗑</button>
        </div>

        <!-- Instance edit fields -->
        <div class="field">
          <label class="fl">Instance Name</label>
          <input type="text" id="inst-name" placeholder="Studio A — vMix 1" value="{{active_inst.name|e}}">
        </div>
        <div class="r3">
          <div class="field">
            <label class="fl">vMix IP</label>
            <input type="text" id="vmix-ip" placeholder="127.0.0.1" value="{{active_inst.vmix_ip|e}}">
            <div class="hint" id="vmix-ip-reported"></div>
          </div>
          <div class="field">
            <label class="fl">Port</label>
            <input type="number" id="vmix-port" value="{{active_inst.vmix_port}}" min="1" max="65535">
          </div>
          <div class="field">
            <label class="fl">Zoom Input</label>
            <input type="text" id="vmix-input" value="{{active_inst.vmix_input}}" placeholder="1">
          </div>
        </div>
        <div class="field">
          <label class="fl">Video Preview Mode</label>
          <div style="display:flex;gap:14px;align-items:center">
            <label style="display:flex;align-items:center;gap:5px;font-size:12px;cursor:pointer"><input type="radio" name="preview-mode" value="srt" {% if active_inst.preview_mode != "ndi" %}checked{% endif %}> SRT Bridge</label>
            <label style="display:flex;align-items:center;gap:5px;font-size:12px;cursor:pointer"><input type="radio" name="preview-mode" value="ndi" {% if active_inst.preview_mode == "ndi" %}checked{% endif %}> NDI</label>
          </div>
        </div>
        <div id="srt-fields" {% if active_inst.preview_mode == "ndi" %}style="display:none"{% endif %}>
          <div class="field">
            <label class="fl">SRT Bridge URL</label>
            <input type="text" id="bridge-url" placeholder="webrtc://192.168.x.x/live/caller1" value="{{active_inst.bridge_url|e}}">
            <div class="hint">One SRS handles multiple instances via stream names — e.g. <code>webrtc://192.168.1.50/live/caller1</code>, <code>.../caller2</code>. See setup guide.</div>
          </div>
        </div>
        <div id="ndi-fields" {% if active_inst.preview_mode != "ndi" %}style="display:none"{% endif %}>
          <div class="field">
            <label class="fl">NDI Source Name</label>
            <div style="display:flex;gap:6px;align-items:center">
              <input type="text" id="ndi-source" placeholder="VMIX-PC (vMix - Input 1 - Zoom)" value="{{active_inst.ndi_source|e}}" style="flex:1">
              <button class="btn bg bs" data-action="discoverNdi" title="Scan LAN for NDI sources (run from client node)">Discover</button>
            </div>
            <div class="hint">vMix advertises all inputs as NDI — the source name is usually <code>MACHINE-NAME (vMix - Input N - Zoom)</code>. Use the Discover button on the <b>client node page</b> to find the exact name. No SRT bridge or Docker needed.</div>
          </div>
        </div>
        <div class="brow">
          <button class="btn bp" data-action="saveInstance">💾 Save Instance &amp; Push to Site</button>
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
        <div class="field">
          <label class="fl">Passcode</label>
          <input type="password" id="mtg-pass" placeholder="••••••">
        </div>
        <div class="brow">
          <button class="btn bp join-btn" style="flex:1;justify-content:center" data-action="joinManual">📞 Join Meeting</button>
        </div>
        <div class="brow call-btns" style="flex-wrap:wrap">
          <button class="btn bg" id="mute-btn" data-action="muteSelf">🔇 Mute Self</button>
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

<!-- ── Zoom API Configuration ─────────────────────────────────────────────── -->
<div class="card">
  <div class="ch">&#128247; Zoom API
    <div class="ch-r">
      <span id="zoom-status-dot" class="dot"></span>
      <span id="zoom-status-txt" class="ago" style="margin-left:4px">{% if zoom_configured %}Credentials saved &#8212; test below{% else %}Not configured{% endif %}</span>
    </div>
  </div>
  <div class="cb">
    <p class="hint" style="margin-bottom:10px">Server-to-Server OAuth &#8212; register a <em>Server-to-Server OAuth</em> app at <a href="https://marketplace.zoom.us" target="_blank" rel="noopener" style="color:var(--acc)">marketplace.zoom.us</a> and grant <code style="font-size:11px">meeting:write:admin</code> and <code style="font-size:11px">meeting:read:admin</code> scopes.</p>
    <div class="field"><label class="fl">Account ID</label><input type="text" id="zoom-account-id" placeholder="{% if zoom_configured %}Configured &#8212; re-enter to update{% else %}Your Account ID{% endif %}"></div>
    <div class="r2">
      <div class="field"><label class="fl">Client ID</label><input type="text" id="zoom-client-id" placeholder="Client ID"></div>
      <div class="field"><label class="fl">Client Secret</label><input type="password" id="zoom-client-secret" placeholder="Client Secret"></div>
    </div>
    <div class="brow"><button class="btn bp bs" data-action="saveZoomCreds">Save &amp; Test</button></div>
    <hr style="border-color:var(--bor);margin:14px 0">
    <p class="fl" style="margin-bottom:6px">Phase 3 &#8212; Webhooks (Optional)</p>
    <p class="hint" style="margin-bottom:10px">Real-time participant events. In your Zoom S2S OAuth app at <a href="https://marketplace.zoom.us" target="_blank" rel="noopener" style="color:var(--acc)">marketplace.zoom.us</a>, add an <em>Event Subscription</em> and paste this URL. Subscribe to: <em>Meeting &gt; Participant/Host joined, left, waiting room joined/left, meeting started/ended</em>.</p>
    <div class="field">
      <label class="fl">Webhook Endpoint URL &#8212; copy into Zoom Dashboard</label>
      <div style="display:flex;gap:6px">
        <input type="text" id="zoom-webhook-url-display" readonly style="font-family:monospace;font-size:11px;color:var(--mu)">
        <button class="btn bg bs" data-action="copyWebhookUrl" title="Copy to clipboard">Copy</button>
      </div>
    </div>
    <div class="field">
      <label class="fl">Webhook Secret Token</label>
      <input type="password" id="zoom-webhook-secret" placeholder="{% if zoom_webhook_configured %}Configured &#8212; re-enter to update{% else %}Secret Token from Zoom webhook config{% endif %}">
    </div>
  </div>
</div>

<!-- &#9135;&#9135; Zoom Meetings (hub view) &#9135;&#9135;&#9135;&#9135;&#9135;&#9135;&#9135;&#9135;&#9135;&#9135;&#9135;&#9135;&#9135;&#9135;&#9135;&#9135;&#9135;&#9135;&#9135;&#9135; -->
<div class="card" id="zoom-meetings-card">
  <div class="ch">&#128197; Zoom Meetings
    <div class="ch-r">
      <span id="zoom-connected" style="display:none;font-size:11px;color:var(--ok)">&#10003; Connected</span>
      <span id="zoom-not-cfg"   style="display:none;font-size:11px;color:var(--mu)">Not configured</span>
      <button class="btn bg bs" data-action="refreshZoom">&#8635; Refresh</button>
      <button class="btn bp bs" data-action="toggleZoomCreate">+ Create</button>
    </div>
  </div>
  <div class="cb">
    <div id="zoom-create-form" style="display:none;background:#091e42;border:1px solid var(--bor);border-radius:8px;padding:12px;margin-bottom:12px">
      <div class="r2" style="margin-bottom:8px">
        <div class="field" style="margin-bottom:0"><label class="fl">Topic</label><input type="text" id="zm-topic" placeholder="Meeting topic"></div>
        <div class="field" style="margin-bottom:0"><label class="fl">Passcode (optional)</label><input type="text" id="zm-pass" placeholder="Leave blank for none"></div>
      </div>
      <div class="r2" style="margin-bottom:8px">
        <div class="field" style="margin-bottom:0"><label class="fl">Duration (min)</label><input type="number" id="zm-dur" value="60" min="15" max="480"></div>
        <div class="field" style="margin-bottom:0;justify-content:flex-end"><label style="display:flex;align-items:center;gap:6px;font-size:12px;cursor:pointer;padding-bottom:2px"><input type="checkbox" id="zm-waiting-room"> Waiting room</label></div>
      </div>
      <div class="brow">
        <button class="btn bp bs" data-action="createZoomNow">&#9654; Start Now &amp; Join in vMix</button>
        <button class="btn bg bs" data-action="cancelZoomCreate">Cancel</button>
      </div>
    </div>
    <div id="zoom-meeting-list"><div class="empty-note">{% if zoom_configured %}Loading&#8230;{% else %}Save credentials above to load meetings{% endif %}</div></div>
  </div>
</div>

<!-- ── Zoom Participants & Waiting Room ──────────────────────────────────── -->
<div class="card" id="zoom-parts-card" style="display:none">
  <div class="ch">&#128101; Zoom Participants
    <div class="ch-r">
      <span id="zoom-parts-meeting-label" class="ago"></span>
      <button class="btn bg bs" data-action="loadZoomParticipants">&#8635; Refresh</button>
    </div>
  </div>
  <div class="cb">
    <div id="zoom-waiting-section" style="display:none;margin-bottom:14px;padding-bottom:14px;border-bottom:1px solid var(--bor)">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
        <span class="fl" style="color:var(--wn)">&#9201; Waiting Room</span>
        <span id="zoom-waiting-count" style="font-size:11px;color:var(--wn)"></span>
        <button class="btn bg bs" style="margin-left:auto" data-action="zoomAdmitAll">Admit All</button>
      </div>
      <div id="zoom-waiting-list" class="plist"></div>
    </div>
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
      <span class="fl">In Meeting</span>
      <span id="zoom-parts-count" class="ago"></span>
    </div>
    <div id="zoom-parts-list" class="plist">
      <div class="empty-note">Join a meeting to see Zoom participants</div>
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

// \u2500\u2500 Instance management \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
var _instances = {{instances_json|safe}};
var _activeInstId = {{active_instance_id_json|safe}};

function _getInstById(id){return _instances.find(function(i){return i.id===id;})||null;}

function _refreshVideoUrl(){
  fetch('/api/vmixcaller/video_url',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(v){_videoUrl=v.url||'';initPreview(_videoUrl);updateRelayCtrl();})
    .catch(function(){});
}

function newInstance(){
  var id='i'+Date.now().toString(36);
  var inst={id:id,name:'New Instance',vmix_ip:'127.0.0.1',vmix_port:8088,vmix_input:'1',bridge_url:''};
  _instances.push(inst);
  var sel=document.getElementById('inst-sel');
  if(sel){var opt=document.createElement('option');opt.value=id;opt.textContent='New Instance';opt.selected=true;sel.appendChild(opt);}
  _activeInstId=id;
  _populateInstForm(inst);
  var n=document.getElementById('inst-name');if(n){n.focus();n.select();}
}

function saveInstance(){
  var id=(document.getElementById('inst-sel')||{}).value||_activeInstId;
  var name=((document.getElementById('inst-name')||{}).value||'').trim()||'Instance';
  var ip=((document.getElementById('vmix-ip')||{}).value||'').trim()||'127.0.0.1';
  var port=parseInt((document.getElementById('vmix-port')||{}).value)||8088;
  var inpR=((document.getElementById('vmix-input')||{}).value||'').trim();
  var inp=inpR===''||isNaN(inpR)?inpR:(parseInt(inpR)||1);
  var burl=((document.getElementById('bridge-url')||{}).value||'').trim();
  var pm=(document.querySelector('input[name="preview-mode"]:checked')||{}).value||'srt';
  var ndis=((document.getElementById('ndi-source')||{}).value||'').trim();
  var site=((document.getElementById('target-site')||{}).value||'').trim();
  _post('/api/vmixcaller/instances/'+encodeURIComponent(id),{
    name:name,vmix_ip:ip,vmix_port:port,vmix_input:inp,bridge_url:burl,
    preview_mode:pm,ndi_source:ndis,activate:true,target_site:site||''
  }).then(function(d){
    if(d.ok){
      var i=_instances.findIndex(function(x){return x.id===id;});
      var upd={id:id,name:name,vmix_ip:ip,vmix_port:port,vmix_input:inp,bridge_url:burl,preview_mode:pm,ndi_source:ndis};
      if(i>=0)_instances[i]=upd;else _instances.push(upd);
      var sel=document.getElementById('inst-sel');
      if(sel){for(var j=0;j<sel.options.length;j++){if(sel.options[j].value===id){sel.options[j].textContent=name;break;}}}
      showMsg(site?'Saved \u2014 pushed to '+site:'Instance saved',true);
      _refreshVideoUrl();
      if(site)loadState();
    }else showMsg(d.error||'Save failed',false);
  }).catch(function(e){showMsg('Error: '+e,false);});
}

function deleteInstance(){
  if(_instances.length<=1){showMsg('Cannot delete the only instance',false);return;}
  var id=(document.getElementById('inst-sel')||{}).value||_activeInstId;
  if(!confirm('Delete this instance?'))return;
  fetch('/api/vmixcaller/instances/'+encodeURIComponent(id),{
    method:'DELETE',headers:{'X-CSRFToken':_csrf()},credentials:'same-origin'
  }).then(function(r){return r.json();}).then(function(d){
    if(d.ok){
      _instances=_instances.filter(function(i){return i.id!==id;});
      var sel=document.getElementById('inst-sel');
      if(sel){for(var j=sel.options.length-1;j>=0;j--){if(sel.options[j].value===id){sel.remove(j);break;}}}
      if(sel&&sel.value){_activeInstId=sel.value;_populateInstForm(_getInstById(_activeInstId));}
      showMsg('Instance deleted',true);
    }else showMsg(d.error||'Delete failed',false);
  }).catch(function(e){showMsg('Error: '+e,false);});
}

function joinManual(){
  var site=document.getElementById('target-site').value;
  if(!site){showMsg('Select a site first',false);return;}
  var mid =(document.getElementById('mtg-id').value||'').trim();
  var pass=(document.getElementById('mtg-pass').value||'').trim();
  if(mid&&typeof setActiveMeeting==='function')setActiveMeeting(mid,'');
  joinWith(mid,pass);
}

// Override sendCmd to include currently-selected site in every command POST.
// The base sendCmd does not know about the hub site selector — the backend reads
// target_site from saved config which may not match the selected site yet.
// By injecting site here, commands work immediately without a prior save.
var _baseSendCmd=sendCmd;
sendCmd=function(fn,value){
  var siteSel=document.getElementById('target-site');
  var siteVal=siteSel?siteSel.value:'';
  if(!siteVal){showMsg('Select a site first',false);return Promise.resolve({ok:false});}
  return _post('/api/vmixcaller/function',{fn:fn,value:value,site:siteVal})
    .then(function(d){if(!d.ok)showMsg(d.error||'Error queuing command',false);return d;})
    .catch(function(e){showMsg('Error: '+e,false);return{ok:false};});
};

// ── State polling ─────────────────────────────────────────────────────────────
var _statePollT=null;
function loadState(){
  clearTimeout(_statePollT);
  var site=(document.getElementById('target-site')||{}).value||'';
  fetch('/api/vmixcaller/state?site='+encodeURIComponent(site),{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      if(!site){setStatus('off','No site selected',0);updateSrsCard(null,'');return;}
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
      updateSrsCard(d.srs||null, site);
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
  if(typeof setActiveMeeting==='function')setActiveMeeting(m.id,m.name||m.id);
  joinWith(m.id,m.pass);
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
  // Populate instance form with active instance on load
  _populateInstForm(_getInstById(_activeInstId));
  // Instance selector change → populate form fields
  var isel=document.getElementById('inst-sel');
  if(isel){
    isel.addEventListener('change',function(){
      _activeInstId=this.value;
      _populateInstForm(_getInstById(_activeInstId));
    });
  }
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
      updateSrsCard(null, this.value);
      _srsMsg('',true);
      if(this.value) loadState();
    });
  }
  updateRelayCtrl();
  loadMeetings();
  loadZoomData({% if zoom_configured %}true{% else %}false{% endif %});
  var pi=document.getElementById('padd-in');
  if(pi) pi.addEventListener('keydown',function(e){if(e.key==='Enter') addManual();});
  // Stop relay cleanly when operator closes/refreshes the tab
  window.addEventListener('beforeunload',function(){
    if(_relayOn&&_relaySite) _post('/api/vmixcaller/relay',{site:_relaySite,active:false});
  });
  // ── SRS Server card (hub — reads site SRS from state poll) ──────────────────
  function _srsMsg(txt,ok){
    var el=document.getElementById('srs-msg');
    if(!el) return;
    el.textContent=txt;
    el.style.display=txt?'block':'none';
    el.style.background=ok?'#0f2318':'#2a0a0a';
    el.style.color=ok?'#22c55e':'#ef4444';
    el.style.border='1px solid '+(ok?'#166534':'#991b1b');
  }
  function updateSrsCard(srs, site){
    var noSite=document.getElementById('srs-no-site');
    var panel=document.getElementById('srs-site-panel');
    var lbl=document.getElementById('srs-site-label');
    var txt=document.getElementById('srs-status-text');
    var badge=document.getElementById('srs-badge');
    var startBtn=document.getElementById('srs-start-btn');
    var stopBtn=document.getElementById('srs-stop-btn');
    if(!site){
      if(noSite)noSite.style.display='';
      if(panel)panel.style.display='none';
      if(badge){badge.textContent='';badge.style.background='';}
      return;
    }
    if(noSite)noSite.style.display='none';
    if(panel)panel.style.display='';
    if(lbl)lbl.textContent=site;
    var installNote=document.getElementById('srs-install-docker-note');
    if(!srs){
      if(txt)txt.textContent='Waiting for first report…';
      if(badge){badge.textContent='';badge.style.background='';}
      if(installNote)installNote.style.display='none';
      if(startBtn)startBtn.style.display='none';
      if(stopBtn)stopBtn.style.display='none';
      return;
    }
    if(!srs.docker_ok){
      if(txt)txt.textContent='Docker not found on '+site;
      if(badge){badge.textContent='unavailable';badge.style.background='#374151';badge.style.color='#9ca3af';}
      if(installNote)installNote.style.display='block';
      if(startBtn)startBtn.style.display='none';
      if(stopBtn)stopBtn.style.display='none';
      return;
    }
    if(installNote)installNote.style.display='none';
    var st=srs.status_text||'unknown';
    if(txt)txt.textContent=srs.exists?(st.charAt(0).toUpperCase()+st.slice(1)):'Not created';
    if(badge){
      if(srs.running){badge.textContent='● running';badge.style.background='#052e16';badge.style.color='#22c55e';}
      else if(srs.exists){badge.textContent='● stopped';badge.style.background='#2a0a0a';badge.style.color='#ef4444';}
      else{badge.textContent='not created';badge.style.background='#374151';badge.style.color='#9ca3af';}
    }
    if(startBtn)startBtn.style.display=srs.running?'none':'inline-block';
    if(stopBtn)stopBtn.style.display=srs.running?'inline-block':'none';
  }
  function srsSendCmd(action){
    var site=(document.getElementById('target-site')||{}).value||'';
    if(!site){_srsMsg('Select a site first',false);return;}
    var _actionLabel = action==='start'?'Starting SRS':'Stopping SRS';
    _srsMsg(_actionLabel+' on '+site+' — command queued…',true);
    var startBtn=document.getElementById('srs-start-btn');
    var stopBtn=document.getElementById('srs-stop-btn');
    if(startBtn)startBtn.disabled=true;
    if(stopBtn)stopBtn.disabled=true;
    var csrf=(document.querySelector('meta[name="csrf-token"]')||{}).content
           ||(document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/)||[])[1]||'';
    fetch('/api/vmixcaller/srs_cmd',{method:'POST',credentials:'same-origin',
      headers:{'Content-Type':'application/json','X-CSRFToken':csrf},
      body:JSON.stringify({site:site,action:action})})
      .then(function(r){return r.json();})
      .then(function(d){
        if(d.ok){_srsMsg('Command sent to '+site+' — status updates in ~12 s',true);}
        else{_srsMsg(d.error||'Failed',false);}
        if(startBtn)startBtn.disabled=false;
        if(stopBtn)stopBtn.disabled=false;
      })
      .catch(function(e){_srsMsg('Error: '+e,false);if(startBtn)startBtn.disabled=false;if(stopBtn)stopBtn.disabled=false;});
  }
  document.getElementById('srs-card').addEventListener('click',function(e){
    var btn=e.target.closest('[data-srs-action]');
    if(!btn) return;
    var act=btn.dataset.srsAction;
    if(act==='start') srsSendCmd('start');
    else if(act==='stop') srsSendCmd('stop');
  });
  updateSrsCard(null, '');
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
    <span><kbd>M</kbd> mute</span>
  </span>
</div>

<div class="g2">

  <!-- ── LEFT: preview ──────────────────────────────────────────────────────── -->
  <div>
    <!-- Instance selector (only when >1 instance) -->
    {% if instances|length > 1 %}
    <div class="card">
      <div class="ch">📹 Active Instance</div>
      <div class="cb" style="padding:10px 14px">
        <div class="inst-strip" style="margin-bottom:0">
          {% for inst in instances %}
          <button class="inst-pill{% if inst.id==active_instance_id %} active{% endif %}"
                  data-id="{{inst.id|e}}"
                  data-action="switchInstance">{{inst.name|e}}</button>
          {% endfor %}
        </div>
      </div>
    </div>
    {% endif %}
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

    <!-- SRS Bridge management (local Docker) -->
    <div class="card" id="srs-card">
      <div class="ch">🐳 SRS Bridge
        <span id="srs-badge" style="margin-left:8px;font-size:10px;padding:2px 8px;border-radius:10px;font-weight:700"></span>
      </div>
      <div class="cb">
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
          <div style="flex:1;min-width:160px">
            <div style="font-size:12px;color:var(--mu);margin-bottom:2px">Container status</div>
            <div id="srs-status-text" style="font-size:13px;color:var(--tx)">Checking…</div>
          </div>
          <button class="btn bp bs" id="srs-start-btn" data-srs-action="start" style="display:none">▶ Start SRS</button>
          <button class="btn bd bs" id="srs-stop-btn" data-srs-action="stop" style="display:none">■ Stop SRS</button>
          <button class="btn bg bs" data-srs-action="refresh" title="Refresh status">↻</button>
        </div>
        <div id="srs-docker-install-row" style="display:none;margin-top:10px;padding:10px;background:#050e20;border:1px solid var(--bor);border-radius:8px">
          <div style="font-size:11px;color:var(--mu);margin-bottom:6px">Docker not found — enter your sudo password to install it:</div>
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <input type="password" id="srs-sudo-pw" placeholder="sudo password" autocomplete="current-password" style="background:#0d1e40;border:1px solid var(--bor);border-radius:6px;color:var(--tx);padding:6px 9px;font-size:12px;flex:1;min-width:140px">
            <button class="btn bp bs" id="srs-install-docker-btn" data-srs-action="install-docker">⬇ Install Docker</button>
          </div>
        </div>
        <div id="srs-msg" style="display:none;margin-top:8px;padding:6px 10px;border-radius:6px;font-size:12px"></div>
        <div id="srs-logs-wrap" style="margin-top:10px;display:none">
          <div style="font-size:11px;color:var(--mu);margin-bottom:4px;text-transform:uppercase;letter-spacing:.05em">Last 30 log lines</div>
          <pre id="srs-logs" style="background:#050e20;border:1px solid var(--bor);border-radius:6px;padding:8px 10px;font-size:10px;color:#7dd3fc;max-height:160px;overflow-y:auto;white-space:pre-wrap;margin:0"></pre>
        </div>
        <p style="font-size:11px;color:var(--mu);margin-top:10px;margin-bottom:0">
          Manages <code>ossrs/srs:5</code> on this machine. Docker must be installed.
          First start pulls the image — may take ~1 minute.
          Once started with <em>--restart unless-stopped</em> it survives reboots automatically.
        </p>
      </div>
    </div>
  </div>

  <!-- ── RIGHT: controls ────────────────────────────────────────────────────── -->
  <div>

    <!-- vMix Instance management -->
    <div class="card">
      <div class="ch">🔌 vMix Instances</div>
      <div class="cb">

        <!-- Instance selector row -->
        <div class="inst-act-label">Active Instance</div>
        <div style="display:flex;gap:6px;margin-bottom:12px">
          <select id="inst-sel" style="flex:1">
            {% for inst in instances %}
            <option value="{{inst.id|e}}"{% if inst.id==active_instance_id %} selected{% endif %}>{{inst.name|e}}</option>
            {% endfor %}
          </select>
          <button class="btn bg bs" data-action="newInstance" title="Add new instance">+&nbsp;New</button>
          <button class="btn bd bs" id="del-inst-btn" data-action="deleteInstance" title="Delete this instance">🗑</button>
        </div>

        <!-- Instance edit fields -->
        <div class="field">
          <label class="fl">Instance Name</label>
          <input type="text" id="inst-name" placeholder="Studio A — vMix 1" value="{{active_inst.name|e}}">
        </div>
        <div class="r3">
          <div class="field">
            <label class="fl">vMix IP</label>
            <input type="text" id="vmix-ip" placeholder="127.0.0.1" value="{{active_inst.vmix_ip|e}}">
          </div>
          <div class="field">
            <label class="fl">Port</label>
            <input type="number" id="vmix-port" value="{{active_inst.vmix_port}}" min="1" max="65535">
          </div>
          <div class="field">
            <label class="fl">Zoom Input</label>
            <input type="text" id="vmix-input" value="{{active_inst.vmix_input}}" placeholder="1">
          </div>
        </div>
        <div class="field">
          <label class="fl">Video Preview Mode</label>
          <div style="display:flex;gap:14px;align-items:center">
            <label style="display:flex;align-items:center;gap:5px;font-size:12px;cursor:pointer"><input type="radio" name="preview-mode" value="srt" {% if active_inst.preview_mode != "ndi" %}checked{% endif %}> SRT Bridge</label>
            <label style="display:flex;align-items:center;gap:5px;font-size:12px;cursor:pointer"><input type="radio" name="preview-mode" value="ndi" {% if active_inst.preview_mode == "ndi" %}checked{% endif %}> NDI</label>
          </div>
        </div>
        <div id="srt-fields" {% if active_inst.preview_mode == "ndi" %}style="display:none"{% endif %}>
          <div class="field">
            <label class="fl">SRT Bridge URL</label>
            <input type="text" id="bridge-url" placeholder="webrtc://192.168.x.x/live/caller1" value="{{active_inst.bridge_url|e}}">
            <div class="hint">Stream name differentiates instances on one SRS — e.g. <code>.../caller1</code>, <code>.../caller2</code>.</div>
          </div>
        </div>
        <div id="ndi-fields" {% if active_inst.preview_mode != "ndi" %}style="display:none"{% endif %}>
          <div class="field">
            <label class="fl">NDI Source Name</label>
            <div style="display:flex;gap:6px;align-items:center">
              <input type="text" id="ndi-source" placeholder="VMIX-PC (vMix - Input 1 - Zoom)" value="{{active_inst.ndi_source|e}}" style="flex:1">
              <button class="btn bg bs" data-action="discoverNdi" title="Scan LAN for NDI sources">Discover</button>
            </div>
            <div class="hint">Click <b>Discover</b> to scan the LAN and pick from a list, or type the source name directly. vMix sources are usually <code>MACHINE-NAME (vMix - Input N - Zoom)</code>.</div>
          </div>
        </div>
        <div class="brow">
          <button class="btn bp" data-action="saveInstance">💾 Save Instance</button>
          <button class="btn bg bs" data-action="testVmix">🔌 Test vMix</button>
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
        <div class="field">
          <label class="fl">Passcode</label>
          <input type="password" id="mtg-pass" placeholder="••••••">
        </div>
        <div class="brow">
          <button class="btn bp join-btn" style="flex:1;justify-content:center" data-action="joinManual">📞 Join Meeting</button>
        </div>
        <div class="brow call-btns" style="flex-wrap:wrap">
          <button class="btn bg" id="mute-btn" data-action="muteSelf">🔇 Mute Self</button>
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

<!-- ── Zoom API Meetings ───────────────────────────────────────────────────── -->
<div class="card" id="zoom-meetings-card">
  <div class="ch">&#128247; Zoom Meetings
    <div class="ch-r">
      <span id="zoom-connected" style="display:none;font-size:11px;color:var(--ok)">&#10003; Zoom API</span>
      <span id="zoom-not-cfg"   style="display:none;font-size:11px;color:var(--mu)">Not configured on hub</span>
      <button class="btn bg bs" data-action="refreshZoom" title="Refresh from Zoom API">&#8635;</button>
      <button class="btn bp bs" data-action="toggleZoomCreate">+ Create</button>
    </div>
  </div>
  <div class="cb">
    <div id="zoom-create-form" style="display:none;background:#091e42;border:1px solid var(--bor);border-radius:8px;padding:12px;margin-bottom:12px">
      <div class="r2" style="margin-bottom:8px">
        <div class="field" style="margin-bottom:0"><label class="fl">Topic</label><input type="text" id="zm-topic" placeholder="Meeting topic"></div>
        <div class="field" style="margin-bottom:0"><label class="fl">Passcode (optional)</label><input type="text" id="zm-pass" placeholder="Leave blank for none"></div>
      </div>
      <div class="r2" style="margin-bottom:8px">
        <div class="field" style="margin-bottom:0"><label class="fl">Duration (min)</label><input type="number" id="zm-dur" value="60" min="15" max="480"></div>
        <div class="field" style="margin-bottom:0;justify-content:flex-end"><label style="display:flex;align-items:center;gap:6px;font-size:12px;cursor:pointer;padding-bottom:2px"><input type="checkbox" id="zm-waiting-room"> Waiting room</label></div>
      </div>
      <div class="brow">
        <button class="btn bp bs" data-action="createZoomNow">&#9654; Start Now &amp; Join in vMix</button>
        <button class="btn bg bs" data-action="cancelZoomCreate">Cancel</button>
      </div>
    </div>
    <div id="zoom-meeting-list"><div class="empty-note">Loading&#8230;</div></div>
  </div>
</div>

<!-- ── Zoom Participants & Waiting Room ──────────────────────────────────── -->
<div class="card" id="zoom-parts-card" style="display:none">
  <div class="ch">&#128101; Zoom Participants
    <div class="ch-r">
      <span id="zoom-parts-meeting-label" class="ago"></span>
      <button class="btn bg bs" data-action="loadZoomParticipants">&#8635; Refresh</button>
    </div>
  </div>
  <div class="cb">
    <div id="zoom-waiting-section" style="display:none;margin-bottom:14px;padding-bottom:14px;border-bottom:1px solid var(--bor)">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
        <span class="fl" style="color:var(--wn)">&#9201; Waiting Room</span>
        <span id="zoom-waiting-count" style="font-size:11px;color:var(--wn)"></span>
        <button class="btn bg bs" style="margin-left:auto" data-action="zoomAdmitAll">Admit All</button>
      </div>
      <div id="zoom-waiting-list" class="plist"></div>
    </div>
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
      <span class="fl">In Meeting</span>
      <span id="zoom-parts-count" class="ago"></span>
    </div>
    <div id="zoom-parts-list" class="plist">
      <div class="empty-note">Join a meeting to see Zoom participants</div>
    </div>
  </div>
</div>

</main>
<script nonce="{{csp_nonce()}}">
""" + _JS_HELPERS + r"""

// ── Client-specific additions ─────────────────────────────────────────────────
var _videoUrl    = {{video_url_json|safe}};
var _instances   = {{instances_json|safe}};
var _activeInstId= {{active_instance_id_json|safe}};

function _getInstById(id){return _instances.find(function(i){return i.id===id;})||null;}

function _refreshVideoUrl(){
  fetch('/api/vmixcaller/video_url',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(v){_videoUrl=v.url||'';initPreview(_videoUrl);})
    .catch(function(){});
}

function newInstance(){
  var id='i'+Date.now().toString(36);
  var inst={id:id,name:'New Instance',vmix_ip:'127.0.0.1',vmix_port:8088,vmix_input:'1',bridge_url:''};
  _instances.push(inst);
  var sel=document.getElementById('inst-sel');
  if(sel){var opt=document.createElement('option');opt.value=id;opt.textContent='New Instance';opt.selected=true;sel.appendChild(opt);}
  _activeInstId=id;
  _populateInstForm(inst);
  var n=document.getElementById('inst-name');if(n){n.focus();n.select();}
}

function saveInstance(){
  var id=(document.getElementById('inst-sel')||{}).value||_activeInstId;
  var name=((document.getElementById('inst-name')||{}).value||'').trim()||'Instance';
  var ip=((document.getElementById('vmix-ip')||{}).value||'').trim()||'127.0.0.1';
  var port=parseInt((document.getElementById('vmix-port')||{}).value)||8088;
  var inpR=((document.getElementById('vmix-input')||{}).value||'').trim();
  var inp=inpR===''||isNaN(inpR)?inpR:(parseInt(inpR)||1);
  var burl=((document.getElementById('bridge-url')||{}).value||'').trim();
  var pm=(document.querySelector('input[name="preview-mode"]:checked')||{}).value||'srt';
  var ndis=((document.getElementById('ndi-source')||{}).value||'').trim();
  _post('/api/vmixcaller/instances/'+encodeURIComponent(id),{
    name:name,vmix_ip:ip,vmix_port:port,vmix_input:inp,bridge_url:burl,
    preview_mode:pm,ndi_source:ndis,activate:true
  }).then(function(d){
    if(d.ok){
      var i=_instances.findIndex(function(x){return x.id===id;});
      var upd={id:id,name:name,vmix_ip:ip,vmix_port:port,vmix_input:inp,bridge_url:burl,preview_mode:pm,ndi_source:ndis};
      if(i>=0)_instances[i]=upd;else _instances.push(upd);
      var sel=document.getElementById('inst-sel');
      if(sel){for(var j=0;j<sel.options.length;j++){if(sel.options[j].value===id){sel.options[j].textContent=name;break;}}}
      // Sync active pill labels
      document.querySelectorAll('.inst-pill').forEach(function(p){
        if(p.dataset.id===id)p.textContent=name;
      });
      showMsg('Instance saved',true);
      _refreshVideoUrl();
    }else showMsg(d.error||'Save failed',false);
  }).catch(function(e){showMsg('Error: '+e,false);});
}

function deleteInstance(){
  if(_instances.length<=1){showMsg('Cannot delete the only instance',false);return;}
  var id=(document.getElementById('inst-sel')||{}).value||_activeInstId;
  if(!confirm('Delete this instance?'))return;
  fetch('/api/vmixcaller/instances/'+encodeURIComponent(id),{
    method:'DELETE',headers:{'X-CSRFToken':_csrf()},credentials:'same-origin'
  }).then(function(r){return r.json();}).then(function(d){
    if(d.ok){
      _instances=_instances.filter(function(i){return i.id!==id;});
      var sel=document.getElementById('inst-sel');
      if(sel){for(var j=sel.options.length-1;j>=0;j--){if(sel.options[j].value===id){sel.remove(j);break;}}}
      if(sel&&sel.value){_activeInstId=sel.value;_populateInstForm(_getInstById(_activeInstId));}
      showMsg('Instance deleted',true);
    }else showMsg(d.error||'Delete failed',false);
  }).catch(function(e){showMsg('Error: '+e,false);});
}

// Switch active vMix instance (same logic as presenter page)
function switchInstance(btn){
  var id=btn.dataset.id;
  document.querySelectorAll('.inst-pill').forEach(function(p){p.classList.toggle('active',p.dataset.id===id);});
  _post('/api/vmixcaller/instances/'+encodeURIComponent(id)+'/activate',{})
    .then(function(d){
      if(d.ok){
        fetch('/api/vmixcaller/video_url',{credentials:'same-origin'})
          .then(function(r){return r.json();})
          .then(function(v){_videoUrl=v.url||'';if(_videoUrl)initPreview(_videoUrl);})
          .catch(function(){});
      }
    }).catch(function(){});
}

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
      var addr=d.vmix_addr?' ('+d.vmix_addr+')':'';
      if(d.ok)showMsg('\u2713 vMix reachable'+addr+' \u2014 v'+d.version,true);
      else showMsg('\u2717 Cannot reach vMix'+addr+': '+(d.error||'unknown error'),false);
    }).catch(function(e){showMsg('Error: '+e,false);});
}

function joinManual(){
  var mid=(document.getElementById('mtg-id').value||'').trim();
  var pass=(document.getElementById('mtg-pass').value||'').trim();
  if(mid&&typeof setActiveMeeting==='function')setActiveMeeting(mid,'');
  joinWith(mid,pass);
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
  if(typeof setActiveMeeting==='function')setActiveMeeting(m.id,m.name||m.id);
  joinWith(m.id,m.pass);
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

// ── SRS Bridge management (client — local Docker) ──────────────────────────────
function srsRefresh(){
  var txt=document.getElementById('srs-status-text');
  var badge=document.getElementById('srs-badge');
  var startBtn=document.getElementById('srs-start-btn');
  var stopBtn=document.getElementById('srs-stop-btn');
  var logsWrap=document.getElementById('srs-logs-wrap');
  var logsEl=document.getElementById('srs-logs');
  if(txt) txt.textContent='Checking…';
  fetch('/api/vmixcaller/srs_status',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      var installRow=document.getElementById('srs-docker-install-row');
      if(!d.docker_ok){
        if(txt) txt.textContent='Docker not found on this machine';
        if(badge){badge.textContent='unavailable';badge.style.background='#374151';badge.style.color='#9ca3af';}
        if(installRow) installRow.style.display='block';
        if(startBtn) startBtn.style.display='none';
        if(stopBtn)  stopBtn.style.display='none';
        if(logsWrap) logsWrap.style.display='none';
        return;
      }
      if(installRow) installRow.style.display='none';
      var st=d.status_text||'unknown';
      if(txt) txt.textContent=d.exists?(st.charAt(0).toUpperCase()+st.slice(1)):'Not created';
      if(badge){
        if(d.running){badge.textContent='● running';badge.style.background='#052e16';badge.style.color='#22c55e';}
        else if(d.exists){badge.textContent='● stopped';badge.style.background='#2a0a0a';badge.style.color='#ef4444';}
        else{badge.textContent='not created';badge.style.background='#374151';badge.style.color='#9ca3af';}
      }
      if(startBtn) startBtn.style.display=d.running?'none':'inline-block';
      if(stopBtn)  stopBtn.style.display=d.running?'inline-block':'none';
      if(d.logs&&d.logs.trim()){
        if(logsWrap) logsWrap.style.display='block';
        if(logsEl){logsEl.textContent=d.logs;logsEl.scrollTop=logsEl.scrollHeight;}
      } else {
        if(logsWrap) logsWrap.style.display='none';
      }
    })
    .catch(function(){if(txt) txt.textContent='Status unavailable';});
}
function _srsMsg(txt,ok){
  var el=document.getElementById('srs-msg');
  if(!el) return;
  el.textContent=txt;
  el.style.display=txt?'block':'none';
  el.style.background=ok?'#0f2318':'#2a0a0a';
  el.style.color=ok?'#22c55e':'#ef4444';
  el.style.border='1px solid '+(ok?'#166534':'#991b1b');
}
function srsStart(){
  _srsMsg('Starting SRS — please wait…',true);
  var startBtn=document.getElementById('srs-start-btn');
  if(startBtn) startBtn.disabled=true;
  var csrf=(document.querySelector('meta[name="csrf-token"]')||{}).content
         ||(document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/)||[])[1]||'';
  fetch('/api/vmixcaller/srs_start',{method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRFToken':csrf},body:'{}'})
    .then(function(r){return r.json();})
    .then(function(d){
      _srsMsg(d.msg||'',d.ok);
      if(startBtn) startBtn.disabled=false;
      setTimeout(srsRefresh,1500);
    })
    .catch(function(e){_srsMsg('Error: '+e,false);if(startBtn) startBtn.disabled=false;});
}
function srsStop(){
  _srsMsg('Stopping SRS…',true);
  var stopBtn=document.getElementById('srs-stop-btn');
  if(stopBtn) stopBtn.disabled=true;
  var csrf=(document.querySelector('meta[name="csrf-token"]')||{}).content
         ||(document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/)||[])[1]||'';
  fetch('/api/vmixcaller/srs_stop',{method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRFToken':csrf},body:'{}'})
    .then(function(r){return r.json();})
    .then(function(d){
      _srsMsg(d.msg||'',d.ok);
      if(stopBtn) stopBtn.disabled=false;
      setTimeout(srsRefresh,1500);
    })
    .catch(function(e){_srsMsg('Error: '+e,false);if(stopBtn) stopBtn.disabled=false;});
}
function srsInstallDocker(){
  var pwEl=document.getElementById('srs-sudo-pw');
  var pw=(pwEl&&pwEl.value)||'';
  if(!pw){_srsMsg('Enter your sudo password first',false);return;}
  _srsMsg('Installing Docker — this may take a few minutes…',true);
  var installBtn=document.getElementById('srs-install-docker-btn');
  if(installBtn) installBtn.disabled=true;
  var csrf=(document.querySelector('meta[name="csrf-token"]')||{}).content
         ||(document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/)||[])[1]||'';
  fetch('/api/vmixcaller/docker_install',{method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRFToken':csrf},
    body:JSON.stringify({password:pw})})
    .then(function(r){return r.json();})
    .then(function(d){
      _srsMsg((d.msg||''),d.ok);
      if(installBtn) installBtn.disabled=false;
      if(pwEl) pwEl.value='';
      if(d.ok) setTimeout(srsRefresh,2000);
    })
    .catch(function(e){_srsMsg('Error: '+e,false);if(installBtn) installBtn.disabled=false;});
}

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded',function(){
  try{if(_videoUrl) initPreview(_videoUrl);}catch(e){}
  // Populate instance form with active instance on load
  _populateInstForm(_getInstById(_activeInstId));
  // Instance selector change → populate form fields + update active id
  var isel=document.getElementById('inst-sel');
  if(isel){
    isel.addEventListener('change',function(){
      _activeInstId=this.value;
      _populateInstForm(_getInstById(_activeInstId));
    });
  }
  loadState();
  loadMeetings();
  loadZoomData(false);
  var pi=document.getElementById('padd-in');
  if(pi) pi.addEventListener('keydown',function(e){if(e.key==='Enter') addManual();});
  var mi=document.getElementById('mtg-id');
  if(mi) mi.addEventListener('keydown',function(e){if(e.key==='Enter') joinManual();});
  // SRS Bridge card
  var srsCard=document.getElementById('srs-card');
  if(srsCard){
    srsCard.addEventListener('click',function(e){
      var btn=e.target.closest('[data-srs-action]');
      if(!btn) return;
      var act=btn.dataset.srsAction;
      if(act==='start') srsStart();
      else if(act==='stop') srsStop();
      else if(act==='refresh') srsRefresh();
      else if(act==='install-docker') srsInstallDocker();
    });
    srsRefresh();
    setInterval(srsRefresh,15000);
  }
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

    # ── Brand Screen WHEP relay thread ────────────────────────────────────────
    # When a Brand Screen kiosk page (HTTPS hub) needs to establish a WebRTC
    # connection to the local SRS bridge (HTTP LAN), mixed-content policy blocks
    # the direct fetch.  The hub stores the SDP offer; this thread picks it up,
    # POSTs it to the local SRS bridge (HTTP → HTTP, no mixed-content), and
    # returns the SDP answer to the hub.  The browser then gets the answer and
    # WebRTC ICE connects directly browser ↔ SRS over UDP.
    # vmixcaller is already installed on every node that has an SRS bridge, so
    # this piggybacks on the existing vmixcaller client without needing a separate
    # brandscreen installation on the client.
    if mode in ("client", "both") and hub_url:
        def _bs_whep_relay_thread(hub_url=hub_url):
            import urllib.request as _ur
            import urllib.error   as _ue
            _cmd_url     = f"{hub_url}/api/brandscreen/whep_cmd"
            _fail_log    = False
            _started_log = False
            while True:
                # Read site_name dynamically — may be empty at plugin load time
                site = (getattr(getattr(monitor.app_cfg, "hub", None),
                                "site_name", "") or "").strip()
                if not site:
                    time.sleep(5)
                    continue
                if not _started_log:
                    monitor.log(f"[vmixcaller] BS-WHEP relay poller started "
                                f"(site={site}, hub={hub_url})")
                    _started_log = True
                try:
                    req = _ur.Request(_cmd_url, headers={"X-Site": site})
                    with _ur.urlopen(req, timeout=6) as r:
                        d = json.loads(r.read())
                    _fail_log = False
                    relay_id  = d.get("relay_id", "")
                    whep_url  = d.get("whep_url",  "")
                    sdp_offer = d.get("sdp",       "")
                    if relay_id and whep_url and sdp_offer:
                        monitor.log(f"[vmixcaller] BS-WHEP relay {relay_id[:8]}… "
                                    f"→ forwarding to {whep_url}")
                        try:
                            fwd = _ur.Request(
                                whep_url,
                                data=sdp_offer.encode(),
                                method="POST",
                                headers={"Content-Type": "application/sdp"},
                            )
                            with _ur.urlopen(fwd, timeout=10) as resp:
                                answer = resp.read().decode()
                            monitor.log(f"[vmixcaller] BS-WHEP SRS answered "
                                        f"({len(answer)} bytes)")
                        except (_ue.HTTPError, _ue.URLError, Exception) as exc:
                            monitor.log(f"[vmixcaller] BS-WHEP SRS POST failed: {exc}")
                            answer = ""
                        # Return answer (or empty string on failure) to hub
                        done_req = _ur.Request(
                            f"{hub_url}/api/brandscreen/whep_done/{relay_id}",
                            data=(answer or "").encode(),
                            method="POST",
                            headers={"Content-Type": "text/plain",
                                     "X-Site": site},
                        )
                        try:
                            _ur.urlopen(done_req, timeout=5).close()
                        except Exception as exc:
                            monitor.log(f"[vmixcaller] BS-WHEP done POST failed: {exc}")
                except Exception as exc:
                    if not _fail_log:
                        monitor.log(f"[vmixcaller] BS-WHEP relay poll failed "
                                    f"({_cmd_url}): {exc}")
                        _fail_log = True
                time.sleep(3)
        threading.Thread(target=_bs_whep_relay_thread, daemon=True,
                         name="VmixCallerBSWhepRelay").start()

    def _tpl_inst_vars(cfg: dict) -> dict:
        """Common template vars for instance selector rendering."""
        instances = cfg.get("instances") or []
        active_id = cfg.get("active_instance_id", "")
        active    = _get_active_instance(cfg)
        return {
            "instances":           instances,
            "active_instance_id":  active_id,
            "active_inst":         active,
            "instances_json":      json.dumps(instances),
            "active_instance_id_json": json.dumps(active_id),
        }

    # ── Operator / hub page ───────────────────────────────────────────────────
    @app.get("/hub/vmixcaller")
    @login_required
    def vmixcaller_page():
        cfg      = _load_cfg()
        raw      = _load_raw()
        iv       = _tpl_inst_vars(cfg)
        zoom_cfg = _zoom_has_creds(raw)
        if is_hub:
            with hub_server._lock:
                sites = sorted(
                    n for n, d in hub_server._sites.items()
                    if d.get("_approved")
                )
            video_url = _compute_video_url(cfg, is_hub)
            return render_template_string(_HUB_TPL, cfg=cfg, sites=sites,
                                          video_url_json=json.dumps(video_url),
                                          zoom_configured=zoom_cfg,
                                          zoom_webhook_configured=bool(raw.get("zoom_webhook_secret")),
                                          **iv)
        if is_client:
            video_url = _compute_video_url(cfg, False)
            return render_template_string(_CLIENT_TPL, cfg=cfg, hub_url=hub_url,
                                          video_url_json=json.dumps(video_url),
                                          zoom_configured=zoom_cfg, **iv)
        # Standalone
        video_url = _compute_video_url(cfg, True)
        return render_template_string(_HUB_TPL, cfg=cfg, sites=[],
                                      video_url_json=json.dumps(video_url),
                                      zoom_configured=zoom_cfg,
                                      zoom_webhook_configured=bool(raw.get("zoom_webhook_secret")),
                                      **iv)

    # ── Presenter page ────────────────────────────────────────────────────────
    @app.get("/hub/vmixcaller/presenter")
    @login_required
    def vmixcaller_presenter():
        cfg       = _load_cfg()
        instances = cfg.get("instances") or []
        meetings  = cfg.get("saved_meetings") or []

        # ?inst=<id> selects the studio. Auto-select when only one instance.
        inst_id       = request.args.get("inst", "").strip()
        selected_inst = next((i for i in instances if i.get("id") == inst_id), None)
        if selected_inst is None and len(instances) == 1:
            selected_inst = instances[0]

        # Compute video URL from the selected instance's own bridge_url.
        if selected_inst:
            inst_bridge = (selected_inst.get("bridge_url") or "").strip()
            inst_cfg    = dict(cfg, bridge_url=inst_bridge)
            video_url   = _compute_video_url(inst_cfg, is_hub)
        else:
            inst_bridge = ""
            video_url   = ""

        return render_template_string(
            _PRESENTER_TPL,
            cfg=cfg,
            meetings=meetings,
            instances=instances,
            bridge_url=inst_bridge,
            video_url_json=json.dumps(video_url),
            selected_inst=selected_inst,
            selected_inst_json=json.dumps(selected_inst or {}),
            active_instance_id=(selected_inst or {}).get("id", ""),
            active_instance_id_json=json.dumps((selected_inst or {}).get("id", "")),
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
        """Legacy config save endpoint — kept for backwards compat."""
        data = request.get_json(silent=True) or {}
        try:
            cfg    = _save_cfg(data)
            target = (cfg.get("target_site") or "").strip()
            _push_instance_to_site(_get_active_instance(cfg), target)
            return jsonify({"ok": True, "config": cfg})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    # ── Instance CRUD ─────────────────────────────────────────────────────────

    def _push_instance_to_site(inst: dict, target: str):
        """Queue a __set_config__ command so the client gets the instance fields."""
        if is_hub and target and inst:
            with _state_lock:
                _pending_cmd[target] = {
                    "fn":         "__set_config__",
                    "vmix_ip":    inst.get("vmix_ip",    "127.0.0.1"),
                    "vmix_port":  inst.get("vmix_port",   8088),
                    "vmix_input": inst.get("vmix_input",  1),
                    "bridge_url":   inst.get("bridge_url",   ""),
                    "preview_mode": inst.get("preview_mode", "srt"),
                    "ndi_source":   inst.get("ndi_source",   ""),
                    "seq":          int(time.time() * 1000),
                }

    @app.get("/api/vmixcaller/instances")
    @login_required
    def vmixcaller_list_instances():
        cfg = _load_cfg()
        return jsonify({
            "instances":          cfg.get("instances") or [],
            "active_instance_id": cfg.get("active_instance_id", ""),
        })

    @app.route("/api/vmixcaller/instances/<inst_id>", methods=["POST", "PUT"])
    @login_required
    @csrf_protect
    def vmixcaller_update_instance(inst_id):
        data      = request.get_json(silent=True) or {}
        raw       = _load_raw()
        instances = list(raw.get("instances") or [])
        # If this is a new client-side instance (not yet persisted), append it
        idx = next((i for i, x in enumerate(instances) if x.get("id") == inst_id), None)
        updated = _make_instance(
            name         = str(data.get("name",         "") or "").strip() or "Instance",
            vmix_ip      = str(data.get("vmix_ip",      "127.0.0.1") or "127.0.0.1").strip(),
            vmix_port    = int(data.get("vmix_port",    8088) or 8088),
            vmix_input   = data.get("vmix_input", 1),
            bridge_url   = str(data.get("bridge_url",   "") or "").strip(),
            preview_mode = str(data.get("preview_mode", "srt") or "srt").strip(),
            ndi_source   = str(data.get("ndi_source",   "") or "").strip(),
            inst_id      = inst_id,
        )
        if idx is not None:
            instances[idx] = updated
        else:
            instances.append(updated)
        save_data: dict = {"instances": instances}
        if data.get("activate"):
            save_data["active_instance_id"] = inst_id
        target = str(data.get("target_site") or raw.get("target_site", "") or "").strip()
        if target:
            save_data["target_site"] = target
        _save_cfg(save_data)
        if data.get("activate"):
            _push_instance_to_site(updated, target)
        return jsonify({"ok": True})

    @app.route("/api/vmixcaller/instances/<inst_id>", methods=["DELETE"])
    @login_required
    @csrf_protect
    def vmixcaller_delete_instance(inst_id):
        raw       = _load_raw()
        instances = [i for i in (raw.get("instances") or []) if i.get("id") != inst_id]
        update: dict = {"instances": instances}
        if raw.get("active_instance_id") == inst_id:
            update["active_instance_id"] = instances[0].get("id") if instances else ""
        _save_cfg(update)
        return jsonify({"ok": True})

    @app.post("/api/vmixcaller/instances/<inst_id>/activate")
    @login_required
    @csrf_protect
    def vmixcaller_activate_instance(inst_id):
        raw       = _load_raw()
        instances = raw.get("instances") or []
        inst      = next((i for i in instances if i.get("id") == inst_id), None)
        if not inst:
            return jsonify({"ok": False, "error": "Instance not found"}), 404
        _save_cfg({"active_instance_id": inst_id})
        return jsonify({"ok": True, "bridge_url": inst.get("bridge_url", "")})

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
        # Use site from request body first (hub page always sends it); fall back to saved config.
        target_site = (data.get("site") or cfg.get("target_site", "")).strip()
        # ZoomJoinMeeting Value = "MeetingID,Password" (comma-separated per official vMix API).
        # JS now sends it pre-formatted; no splitting needed here.
        raw_value = data.get("value")
        value2    = data.get("value2")
        value3    = data.get("value3")
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
        # site can be passed as a query param (hub page sends it on every poll).
        # Fall back to saved config for backwards compat.
        target_site = (request.args.get("site") or cfg.get("target_site", "")).strip()
        if not target_site:
            return jsonify({"has_data": False})
        with _state_lock:
            status = dict(_site_status.get(target_site, {}))
        status.setdefault("has_data", bool(status))
        return jsonify(status)

    # ── Hub: all instances reported by connected clients ─────────────────────
    # Returns {instances: [{site, id, name, bridge_url, whep_url}]} aggregated
    # from every approved site that has reported since startup.  Used by the
    # Brand Screen video source picker so it can see client-configured bridges.
    @app.get("/api/vmixcaller/hub_instances")
    @login_required
    def vmixcaller_hub_instances():
        from urllib.parse import urlparse
        def _to_whep(bridge_url):
            if not bridge_url:
                return ""
            if bridge_url.startswith("webrtc://"):
                rest  = bridge_url[len("webrtc://"):]
                parts = rest.split("/", 2)
                host   = parts[0] if parts else ""
                app_   = parts[1] if len(parts) > 1 else "live"
                stream = parts[2] if len(parts) > 2 else "caller"
                if host:
                    return f"http://{host}:1985/rtc/v1/whep/?app={app_}&stream={stream}"
            return ""

        out = []
        with _state_lock:
            snapshot = dict(_site_status)
        approved_sites = set()
        try:
            with hub_server._lock:
                approved_sites = {
                    s for s, d in (hub_server._sites or {}).items()
                    if d.get("_approved")
                }
        except Exception:
            pass
        for site, data in snapshot.items():
            if site not in approved_sites:
                continue
            for inst in (data.get("instances") or []):
                burl = (inst.get("bridge_url") or "").strip()
                out.append({
                    "site":       site,
                    "id":         inst.get("id", ""),
                    "name":       inst.get("name", "Default"),
                    "bridge_url": burl,
                    "whep_url":   _to_whep(burl),
                })
        return jsonify({"instances": out})

    # ── Client: local vMix test ───────────────────────────────────────────────
    @app.get("/api/vmixcaller/test_local")
    @login_required
    def vmixcaller_test_local():
        cfg = _load_cfg()
        ok, xml_text = _vmix_xml(cfg)
        vmix_addr = f"{cfg.get('vmix_ip','127.0.0.1')}:{cfg.get('vmix_port',8088)}"
        if ok:
            return jsonify({"ok": True, "version": _vmix_version(xml_text), "vmix_addr": vmix_addr})
        return jsonify({"ok": False, "error": xml_text[:120], "vmix_addr": vmix_addr})

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


    # ── NDI source discovery (client-node only) ──────────────────────────────
    @app.get("/api/vmixcaller/ndi_sources")
    @login_required
    def vmixcaller_ndi_sources():
        if is_hub and mode != "standalone":
            # Hub can't see LAN NDI — proxy to the configured client node
            app_cfg  = monitor.app_cfg
            raw      = _load_raw()
            site     = (raw.get("target_site") or "").strip()
            hub_cfg  = getattr(app_cfg, "hub", None)
            secret   = (getattr(hub_cfg, "secret_key", "") or "").strip()
            hub_base = (getattr(hub_cfg, "hub_url", "") or "").rstrip("/")
            if site and hub_base:
                try:
                    data = _zoom_hub_proxy_get(
                        f"{hub_base}/api/vmixcaller/ndi_sources_client", site, secret)
                    return jsonify(data)
                except Exception as e:
                    return jsonify({"sources": [], "error": str(e)[:80]})
            return jsonify({"sources": [], "note": "No client site configured"})
        # Running on client (or standalone) — scan locally
        if not _ndi_available:
            return jsonify({"sources": [],
                            "error": "ndi-python not installed — run: pip install ndi-python"})
        try:
            sources = _ndi_sources(timeout_s=4.0)
            return jsonify({"sources": sources})
        except Exception as e:
            return jsonify({"sources": [], "error": str(e)[:120]})

    # ── NDI sources — HMAC-auth endpoint for hub proxy ──────────────────────
    @app.get("/api/vmixcaller/ndi_sources_client")
    def vmixcaller_ndi_sources_client():
        import hmac as _hm
        site = request.headers.get("X-Site", "").strip()
        if not site:
            return jsonify({"error": "missing X-Site"}), 400
        with hub_server._lock:
            if not hub_server._sites.get(site, {}).get("_approved"):
                return jsonify({"error": "not approved"}), 403
        secret = getattr(getattr(monitor.app_cfg, "hub", None), "secret_key", "") or ""
        if secret:
            sig  = request.headers.get("X-Hub-Sig", "")
            ts_s = request.headers.get("X-Hub-Ts", "0")
            try:
                ts = float(ts_s)
                if abs(time.time() - ts) > 120:
                    return jsonify({"error": "timestamp expired"}), 403
                key      = hashlib.sha256(f"{secret}:signing".encode()).digest()
                expected = _hm.new(key, f"{ts:.0f}:".encode() + b"", hashlib.sha256).hexdigest()
                if not _hm.compare_digest(sig, expected):
                    return jsonify({"error": "bad signature"}), 403
            except Exception:
                return jsonify({"error": "auth error"}), 403
        if not _ndi_available:
            return jsonify({"sources": [],
                            "error": "ndi-python not installed on this client node"})
        try:
            return jsonify({"sources": _ndi_sources(timeout_s=4.0)})
        except Exception as e:
            return jsonify({"sources": [], "error": str(e)[:120]})

    # ══════════════════════════════════════════════════════════════════════════
    # Zoom API routes
    # ══════════════════════════════════════════════════════════════════════════

    # ── Save credentials (hub admin) ─────────────────────────────────────────
    @app.post("/api/vmixcaller/zoom_credentials")
    @login_required
    @csrf_protect
    def vmixcaller_zoom_credentials():
        if not is_hub and mode != "standalone":
            return jsonify({"ok": False, "error": "Hub only"}), 403
        data = request.get_json(silent=True) or {}
        with _cfg_lock:
            try:
                with open(_CFG_PATH) as fh:
                    current = json.load(fh)
            except Exception:
                current = {}
            for k in ("zoom_account_id", "zoom_client_id", "zoom_client_secret", "zoom_webhook_secret"):
                if k in data:
                    current[k] = str(data[k]).strip()
            with open(_CFG_PATH, "w") as fh:
                json.dump(current, fh, indent=2)
        with _zoom_token_lock:
            _zoom_token_cache.update({"token": "", "expires": 0.0})
        with _zoom_meetings_lock:
            _zoom_meetings_cache.update({"ts": 0.0, "data": []})
        return jsonify({"ok": True})

    # ── Test credentials (hub admin) ─────────────────────────────────────────
    @app.get("/api/vmixcaller/zoom_status")
    @login_required
    def vmixcaller_zoom_status():
        if not is_hub and mode != "standalone":
            return jsonify({"ok": False, "error": "Hub only"})
        raw = _load_raw()
        if not _zoom_has_creds(raw):
            return jsonify({"ok": False, "configured": False,
                            "error": "No credentials configured"})
        ok, data = _zoom_api(raw, "GET", "/users/me")
        if ok:
            return jsonify({"ok": True, "configured": True,
                            "name":  (data.get("display_name") or
                                      data.get("first_name", "")).strip(),
                            "email": data.get("email", "")})
        return jsonify({"ok": False, "configured": True,
                        "error": str(data.get("message") or data)[:120]})

    # ── Meetings data for client proxy (HMAC-authenticated) ──────────────────
    @app.get("/api/vmixcaller/zoom_hub_data")
    def vmixcaller_zoom_hub_data():
        import hmac as _hm
        site = request.headers.get("X-Site", "").strip()
        if not site:
            return jsonify({"error": "missing X-Site"}), 400
        with hub_server._lock:
            if not hub_server._sites.get(site, {}).get("_approved"):
                return jsonify({"error": "not approved"}), 403
        secret = getattr(getattr(monitor.app_cfg, "hub", None), "secret_key", "") or ""
        if secret:
            sig  = request.headers.get("X-Hub-Sig", "")
            ts_s = request.headers.get("X-Hub-Ts", "0")
            try:
                ts = float(ts_s)
                if abs(time.time() - ts) > 120:
                    return jsonify({"error": "timestamp expired"}), 403
                key      = hashlib.sha256(f"{secret}:signing".encode()).digest()
                expected = _hm.new(key, f"{ts:.0f}:".encode() + b"", hashlib.sha256).hexdigest()
                if not _hm.compare_digest(sig, expected):
                    return jsonify({"error": "bad signature"}), 403
            except Exception:
                return jsonify({"error": "auth error"}), 403
        raw = _load_raw()
        if not _zoom_has_creds(raw):
            return jsonify({"configured": False, "meetings": []})
        try:
            force    = request.args.get("refresh") == "1"
            meetings = _zoom_fetch_meetings(raw, force=force)
            return jsonify({"configured": True, "meetings": meetings})
        except Exception as e:
            return jsonify({"configured": True, "meetings": [],
                            "error": str(e)[:120]})

    # ── Meetings data — browser-facing (hub serves / client proxies) ──────────
    @app.get("/api/vmixcaller/zoom_data")
    @login_required
    def vmixcaller_zoom_data():
        if is_hub or mode == "standalone":
            raw = _load_raw()
            if not _zoom_has_creds(raw):
                return jsonify({"configured": False, "meetings": []})
            try:
                force    = request.args.get("refresh") == "1"
                meetings = _zoom_fetch_meetings(raw, force=force)
                return jsonify({"configured": True, "meetings": meetings})
            except Exception as e:
                return jsonify({"configured": True, "meetings": [],
                                "error": str(e)[:120]})
        if is_client:
            app_cfg = monitor.app_cfg
            site    = (getattr(getattr(app_cfg, "hub", None), "site_name", "") or "").strip()
            secret  = (getattr(getattr(app_cfg, "hub", None), "secret_key",  "") or "").strip()
            qstr    = "?refresh=1" if request.args.get("refresh") == "1" else ""
            try:
                data = _zoom_hub_proxy_get(
                    f"{hub_url}/api/vmixcaller/zoom_hub_data{qstr}", site, secret)
                return jsonify(data)
            except Exception as e:
                return jsonify({"configured": False, "meetings": [],
                                "error": str(e)[:120]})
        return jsonify({"configured": False, "meetings": []})

    # ── Meeting actions — hub-side for client proxy (approval-only auth) ──────
    @app.post("/api/vmixcaller/zoom_hub_action")
    def vmixcaller_zoom_hub_action():
        site = request.headers.get("X-Site", "").strip()
        if not site:
            return jsonify({"error": "missing X-Site"}), 400
        with hub_server._lock:
            if not hub_server._sites.get(site, {}).get("_approved"):
                return jsonify({"error": "not approved"}), 403
        raw    = _load_raw()
        data   = request.get_json(silent=True) or {}
        action = str(data.get("action", "") or "").strip()
        return jsonify(_zoom_execute_action(raw, action, data))


    # ── Participants — browser-facing (hub serves / client proxies) ───────────
    @app.get("/api/vmixcaller/zoom_participants")
    @login_required
    def vmixcaller_zoom_participants():
        meeting_id = request.args.get("meeting_id", "").strip()
        if not meeting_id:
            return jsonify({"participants": [], "waiting": [], "error": "no meeting_id"})
        force = request.args.get("refresh") == "1"
        if is_hub or mode == "standalone":
            raw = _load_raw()
            if not _zoom_has_creds(raw):
                return jsonify({"participants": [], "waiting": [],
                                "error": "Zoom API not configured on hub"})
            try:
                return jsonify(_zoom_fetch_participants(raw, meeting_id, force=force))
            except Exception as e:
                return jsonify({"participants": [], "waiting": [], "error": str(e)[:120]})
        if is_client:
            app_cfg = monitor.app_cfg
            site    = (getattr(getattr(app_cfg, "hub", None), "site_name", "") or "").strip()
            secret  = (getattr(getattr(app_cfg, "hub", None), "secret_key",  "") or "").strip()
            qstr    = f"?meeting_id={urllib.parse.quote(meeting_id)}"
            if force:
                qstr += "&refresh=1"
            try:
                data = _zoom_hub_proxy_get(
                    f"{hub_url}/api/vmixcaller/zoom_hub_participants{qstr}", site, secret)
                return jsonify(data)
            except Exception as e:
                return jsonify({"participants": [], "waiting": [], "error": str(e)[:120]})
        return jsonify({"participants": [], "waiting": []})

    # ── Participants — HMAC-auth hub endpoint (for client proxy) ─────────────
    @app.get("/api/vmixcaller/zoom_hub_participants")
    def vmixcaller_zoom_hub_participants():
        import hmac as _hm
        site = request.headers.get("X-Site", "").strip()
        if not site:
            return jsonify({"error": "missing X-Site"}), 400
        with hub_server._lock:
            if not hub_server._sites.get(site, {}).get("_approved"):
                return jsonify({"error": "not approved"}), 403
        secret = getattr(getattr(monitor.app_cfg, "hub", None), "secret_key", "") or ""
        if secret:
            sig  = request.headers.get("X-Hub-Sig", "")
            ts_s = request.headers.get("X-Hub-Ts", "0")
            try:
                ts = float(ts_s)
                if abs(time.time() - ts) > 120:
                    return jsonify({"error": "timestamp expired"}), 403
                key      = hashlib.sha256(f"{secret}:signing".encode()).digest()
                expected = _hm.new(key, f"{ts:.0f}:".encode() + b"", hashlib.sha256).hexdigest()
                if not _hm.compare_digest(sig, expected):
                    return jsonify({"error": "bad signature"}), 403
            except Exception:
                return jsonify({"error": "auth error"}), 403
        meeting_id = request.args.get("meeting_id", "").strip()
        if not meeting_id:
            return jsonify({"participants": [], "waiting": []})
        raw   = _load_raw()
        force = request.args.get("refresh") == "1"
        if not _zoom_has_creds(raw):
            return jsonify({"participants": [], "waiting": [],
                            "error": "Zoom API not configured"})
        try:
            return jsonify(_zoom_fetch_participants(raw, meeting_id, force=force))
        except Exception as e:
            return jsonify({"participants": [], "waiting": [], "error": str(e)[:120]})

    # ── Zoom Webhook receiver (Phase 3) ───────────────────────────────────────
    # NOT @login_required — Zoom POSTs from the internet.
    # Validated via HMAC-SHA256 if zoom_webhook_secret is configured.
    @app.post("/api/vmixcaller/zoom_webhook")
    def vmixcaller_zoom_webhook():
        import hmac as _hm
        body = request.get_data()
        try:
            data = json.loads(body) if body else {}
        except Exception:
            return jsonify({"error": "invalid JSON"}), 400

        event = data.get("event", "")

        # ── URL validation challenge (Zoom calls this to verify the endpoint) ─
        if event == "endpoint.url_validation":
            plain_token = (data.get("payload") or {}).get("plainToken", "")
            if not plain_token:
                return jsonify({"error": "no plainToken"}), 400
            raw    = _load_raw()
            secret = (raw.get("zoom_webhook_secret") or "").strip()
            if secret:
                import hmac as _hm2
                encrypted = _hm2.new(
                    secret.encode(), plain_token.encode(),
                    __import__("hashlib").sha256
                ).hexdigest()
            else:
                encrypted = ""
            return jsonify({"plainToken": plain_token, "encryptedToken": encrypted})

        # ── Validate signature (skip if no secret configured) ─────────────────
        raw    = _load_raw()
        secret = (raw.get("zoom_webhook_secret") or "").strip()
        ts_ms  = request.headers.get("x-zm-request-timestamp", "0")
        if not _zoom_validate_webhook(secret, ts_ms, body):
            return jsonify({"error": "invalid signature"}), 403
        # Check timestamp freshness (5 minutes)
        try:
            if abs(time.time() * 1000 - int(ts_ms)) > 300000:
                return jsonify({"error": "timestamp expired"}), 403
        except Exception:
            pass

        # ── Process events ────────────────────────────────────────────────────
        payload    = data.get("payload") or {}
        obj        = payload.get("object") or {}
        meeting_id = str(obj.get("id") or "").strip()

        if event in ("meeting.participant_joined", "meeting.participant_left",
                     "meeting.participant_waiting_room_joined",
                     "meeting.participant_waiting_room_left"):
            # Invalidate participant cache so next browser poll gets fresh data
            with _zoom_participants_lock:
                if _zoom_participants_cache.get("meeting_id") == meeting_id:
                    _zoom_participants_cache["ts"] = 0.0
            _zoom_webhook_counter[0] += 1

        elif event in ("meeting.started", "meeting.ended"):
            with _zoom_meetings_lock:
                _zoom_meetings_cache["ts"] = 0.0
            if event == "meeting.ended":
                with _zoom_participants_lock:
                    if _zoom_participants_cache.get("meeting_id") == meeting_id:
                        _zoom_participants_cache["ts"] = 0.0
            _zoom_webhook_counter[0] += 1

        elif event.startswith("recording."):
            _zoom_webhook_counter[0] += 1

        return jsonify({"ok": True})

    # ── Webhook event counter — browser polls this to detect new events ───────
    @app.get("/api/vmixcaller/zoom_webhook_counter")
    @login_required
    def vmixcaller_zoom_webhook_counter():
        return jsonify({"counter": _zoom_webhook_counter[0]})

    # ── SRS Docker management ─────────────────────────────────────────────────
    @app.get("/api/vmixcaller/srs_status")
    @login_required
    def vmixcaller_srs_status():
        return jsonify(_srs_status())

    @app.post("/api/vmixcaller/srs_start")
    @login_required
    @csrf_protect
    def vmixcaller_srs_start():
        return jsonify(_srs_start())

    @app.post("/api/vmixcaller/srs_stop")
    @login_required
    @csrf_protect
    def vmixcaller_srs_stop():
        return jsonify(_srs_stop())

    @app.post("/api/vmixcaller/srs_cmd")
    @login_required
    @csrf_protect
    def vmixcaller_srs_cmd():
        """Hub queues an srs_start or srs_stop command for a remote site."""
        data   = request.get_json(silent=True) or {}
        site   = str(data.get("site",   "") or "").strip()
        action = str(data.get("action", "") or "").strip()
        if not site or action not in ("start", "stop"):
            return jsonify({"ok": False,
                            "error": "site and action (start/stop) required"}), 400
        fn = f"srs_{action}"
        with _state_lock:
            _pending_cmd[site] = {
                "fn":  fn,
                "seq": int(time.time() * 1000),
            }
        return jsonify({"ok": True, "queued_for": site})

    @app.post("/api/vmixcaller/docker_install")
    @login_required
    @csrf_protect
    def vmixcaller_docker_install():
        """Install Docker on this (local) machine — used by client page."""
        data = request.get_json(silent=True) or {}
        pw   = str(data.get("password", "") or "")
        return jsonify(_docker_install(sudo_password=pw))

    # ── Meeting actions — browser-facing (hub executes / client proxies) ──────
    @app.post("/api/vmixcaller/zoom_action")
    @login_required
    @csrf_protect
    def vmixcaller_zoom_action():
        data   = request.get_json(silent=True) or {}
        action = str(data.get("action", "") or "").strip()
        if is_client and hub_url:
            app_cfg = monitor.app_cfg
            site    = (getattr(getattr(app_cfg, "hub", None), "site_name", "") or "").strip()
            try:
                result = _zoom_hub_proxy_post(
                    f"{hub_url}/api/vmixcaller/zoom_hub_action", site, data)
                return jsonify(result)
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)[:120]})
        raw = _load_raw()
        return jsonify(_zoom_execute_action(raw, action, data))

