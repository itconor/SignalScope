# codec.py — Tieline / Comrex / Prodys Quantum ST / APT WorldCast codec monitor
# Drop alongside signalscope.py.  Hub-only plugin.

SIGNALSCOPE_PLUGIN = {
    "id":      "codec",
    "label":   "Codecs",
    "url":     "/hub/codecs",
    "icon":    "📡",
    "version": "1.0.5",
    # No hub_only — runs on both hub and client nodes.
    # Client: polls local codecs, pushes status to hub.
    # Hub: aggregates status from all connected sites.
}

import base64
import hashlib
import hmac as _hmac_mod
import http.cookiejar
import json
import os
import re
import socket
import sys
import threading
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# ── Module-level state ──────────────────────────────────────────────────────
_log         = None   # callable — set in register()
_cfg_file    = None   # Path to codec_devices.json
_lock        = threading.Lock()
_devices     = []     # list[dict] — loaded from _cfg_file
_status      = {}     # device_id → status dict
_alert_fn    = None   # _alert_log_append from signalscope module
_cookie_jars     = {}   # device_id → http.cookiejar.CookieJar (captured login sessions)

# Hub-side cache: receives codec status pushed by client nodes.
# Keyed by site_name → {"devices": [...], "status": {...}, "ts": float}
_hub_codec_cache = {}
_hub_codec_lock  = threading.Lock()

# Set in register() — used by the push thread on client nodes
_monitor = None

def _get_jar(did: str) -> http.cookiejar.CookieJar:
    """Return (creating if needed) the per-device cookie jar."""
    if did not in _cookie_jars:
        _cookie_jars[did] = http.cookiejar.CookieJar()
    return _cookie_jars[did]

def _has_session(did: str) -> bool:
    """True if the device jar has at least one cookie (i.e. user has logged in)."""
    jar = _cookie_jars.get(did)
    return jar is not None and any(True for _ in jar)

def _clear_session(did: str):
    """Discard the cookie jar for a device, forcing re-login."""
    _cookie_jars.pop(did, None)

# ── HMAC signing (matches signalscope hub_verify_signature) ────────────────
def _sign(secret: str, data: bytes, ts: float) -> str:
    key = hashlib.sha256(f"{secret}:signing".encode()).digest()
    msg = f"{int(ts)}:".encode() + data
    return _hmac_mod.new(key, msg, hashlib.sha256).hexdigest()

# ── Client → hub push thread ────────────────────────────────────────────────
def _push_thread():
    """
    Runs on client nodes.  Pushes local codec status to the hub every 15 s
    (or immediately after a state change via the poller).  Uses the same
    HMAC signing scheme as audio chunk uploads so the hub can verify origin.
    """
    while True:
        try:
            cfg     = _monitor.app_cfg
            hub_url = cfg.hub.hub_url.rstrip("/") if cfg.hub.hub_url else ""
            if not hub_url:
                time.sleep(30)
                continue
            site   = cfg.hub.site_name or "unknown"
            secret = cfg.hub.secret_key or ""

            with _lock:
                devs = list(_devices)
                stat = dict(_status)

            # Strip passwords before sending
            safe_devs = [{k: v for k, v in d.items() if k != "password"}
                         for d in devs]

            payload = json.dumps({
                "site":    site,
                "devices": safe_devs,
                "status":  stat,
                "ts":      time.time(),
            }, default=str).encode()

            ts  = time.time()
            sig = _sign(secret, payload, ts) if secret else ""
            req = urllib.request.Request(
                f"{hub_url}/api/codecs/client_status",
                data    = payload,
                method  = "POST",
                headers = {
                    "Content-Type": "application/json",
                    "X-Hub-Sig":    sig,
                    "X-Hub-Ts":     str(int(ts)),
                    "X-Site":       site,
                },
            )
            urllib.request.urlopen(req, timeout=10).close()
        except Exception as e:
            _log(f"[Codec] Push to hub failed: {e}")
        time.sleep(15)


# ── Device type registry ────────────────────────────────────────────────────
_DTYPES = {
    "comrex":     {"label": "Comrex",                  "port": 80,  "method": "http", "dual": False},
    "tieline":    {"label": "Tieline",                 "port": 80,  "method": "http", "dual": False},
    "quantum_st": {"label": "Prodys Quantum ST",       "port": 80,  "method": "http", "dual": True},
    "apt":        {"label": "APT / WorldCast Quantum", "port": 80,  "method": "http", "dual": True},
    "custom":     {"label": "Custom",                  "port": 80,  "method": "http", "dual": False},
    "tcp":        {"label": "TCP Ping Only",           "port": 80,  "method": "tcp",  "dual": False},
}
# Devices whose physical unit contains two independent codecs (A + B).
# _check_device returns {"a": status, "b": status} for these.
_DUAL_CODEC_TYPES = {"quantum_st", "apt"}

_STATE_COLOUR = {
    "connected":    "#22c55e",
    "idle":         "#17a8ff",
    "disconnected": "#f59e0b",
    "offline":      "#ef4444",
    "error":        "#ef4444",
    "unknown":      "#6b7280",
}
_STATE_LABEL = {
    "connected":    "Connected",
    "idle":         "Idle / Ready",
    "disconnected": "Disconnected",
    "offline":      "Offline",
    "error":        "Error",
    "unknown":      "Unknown",
}

# ── Config persistence ──────────────────────────────────────────────────────
def _load():
    global _devices
    try:
        _devices = json.loads(_cfg_file.read_text()) if _cfg_file and _cfg_file.exists() else []
    except Exception:
        _devices = []

def _save():
    try:
        _cfg_file.write_text(json.dumps(_devices, indent=2))
    except Exception as e:
        _log(f"[Codec] Save error: {e}")

# ── Connectivity checks ─────────────────────────────────────────────────────
def _tcp_ok(host, port, timeout=5):
    try:
        with socket.create_connection((host, int(port)), timeout=float(timeout)):
            return True
    except Exception:
        return False

def _snmp_get(host, community, oid, port=161, timeout=5):
    """
    Minimal SNMP GET (v2c) using pysnmp if available.
    Returns (value_str, error_str).
    """
    try:
        from pysnmp.hlapi import (
            getCmd, SnmpEngine, CommunityData, UdpTransportTarget,
            ContextData, ObjectType, ObjectIdentity,
        )
        ei, es, _, vbs = next(getCmd(
            SnmpEngine(),
            CommunityData(community, mpModel=1),
            UdpTransportTarget((host, int(port)), timeout=timeout, retries=1),
            ContextData(),
            ObjectType(ObjectIdentity(oid)),
        ))
        if ei or es:
            return None, str(ei or es)
        for vb in vbs:
            return str(vb[1]), None
        return None, "no response"
    except ImportError:
        return None, "pysnmp_not_installed"
    except Exception as e:
        return None, str(e)[:120]

def _http_check(dev, timeout=8):
    """HTTP GET → (state, detail, remote_name)."""
    state, detail, remote, _ = _http_check_with_body(dev, timeout)
    return state, detail, remote


def _http_check_with_body(dev, timeout=8):
    """HTTP GET → (state, detail, remote_name, body_str|None).

    Returns the raw response body as the 4th element so dual-codec scrapers
    can split it into A and B without a second network round-trip.
    Supports no auth, HTTP Basic, and HTTP Digest auth.
    """
    host     = dev.get("host", "").strip()
    port     = int(dev.get("port", 80))
    use_ssl  = dev.get("ssl", False)
    endpoint = dev.get("endpoint", "").strip() or _default_ep(dev.get("type", "custom"))
    user     = dev.get("username", "").strip()
    pwd      = dev.get("password", "").strip()
    dtype    = dev.get("type", "custom")

    scheme = "https" if use_ssl else "http"
    url    = f"{scheme}://{host}:{port}{endpoint}"

    did = dev.get("id", "")
    jar = _get_jar(did) if did else http.cookiejar.CookieJar()

    # Always attach the cookie jar — if the user has logged in via the proxy
    # it will contain a valid session and no auth headers are needed.
    # If credentials are also configured, attach Basic/Digest handlers too as
    # a fallback for devices that use standard HTTP auth.
    handlers: list = [urllib.request.HTTPCookieProcessor(jar)]
    if user:
        mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        mgr.add_password(None, url, user, pwd)
        handlers += [
            urllib.request.HTTPBasicAuthHandler(mgr),
            urllib.request.HTTPDigestAuthHandler(mgr),
        ]
    opener = urllib.request.build_opener(*handlers)
    opener.addheaders = [("User-Agent", "SignalScope-Codec/1.0")]

    try:
        with opener.open(url, timeout=timeout) as r:
            body = r.read(131072).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            hint = "Log in via the device page (🌐 button)" if not user else \
                   "Auth failed — check username/password or use the device page to log in"
            return "error", f"Auth required — {hint}", "", None
        return "offline", f"HTTP {e.code}", "", None
    except Exception as e:
        return "offline", str(e)[:100], "", None

    state, detail, remote = _parse_body(dtype, body)
    return state, detail, remote, body

def _default_ep(dtype):
    return {"tieline": "/api/v1/status", "comrex": "/", "quantum_st": "/", "apt": "/"}.get(dtype, "/")

def _parse_body(dtype, body):
    """Return (state, detail, remote_name) from HTTP response body."""
    bl = body.lower()

    # ── Try JSON first ──
    try:
        d = json.loads(body)
        if isinstance(d, dict):
            st  = str(d.get("status", d.get("connection_status",
                       d.get("state", d.get("channelState", ""))))) .lower()
            rem = str(d.get("remote", d.get("remote_name",
                       d.get("caller", d.get("peer", "")))))
            if "connect" in st and "disconnect" not in st:
                return "connected", f"Remote: {rem}" if rem else "Connected", rem
            if any(x in st for x in ("idle", "ready", "listen", "available", "standby")):
                return "idle", "Ready — no active connection", ""
            if "disconnect" in st:
                return "disconnected", "Remote disconnected", ""
    except (ValueError, KeyError):
        pass

    # ── XML quick scan (Tieline uses XML in some models) ──
    if "<" in body:
        for tag in ("state", "status", "channelstatus", "linkstate", "connectionstate"):
            m = re.search(rf"<{tag}[^>]*>([^<]+)</{tag}>", body, re.I)
            if m:
                val = m.group(1).strip().lower()
                if "connect" in val and "disconnect" not in val:
                    return "connected", f"XML: {m.group(1).strip()}", ""
                if "disconnect" in val:
                    return "disconnected", f"XML: {m.group(1).strip()}", ""
                if any(x in val for x in ("idle", "ready")):
                    return "idle", f"XML: {m.group(1).strip()}", ""

    # ── HTML keyword scan (Comrex / Tieline web pages) ──
    # Check "not connected" BEFORE "connected" to avoid false positives
    if any(x in bl for x in ("not connected", "disconnected", "no connection",
                              "link down", "no carrier", "no remote", "line idle")):
        return "disconnected", "Not connected", ""

    if "connected" in bl:
        # Try to extract remote name from common HTML patterns
        for pat in (r'remote["\s:>]+([A-Za-z0-9][\w\.\-]{1,40})',
                    r'peer["\s:>]+([A-Za-z0-9][\w\.\-]{1,40})',
                    r'caller["\s:>]+([A-Za-z0-9][\w\.\-]{1,40})'):
            m = re.search(pat, body, re.I)
            if m:
                rem = m.group(1)
                return "connected", f"Remote: {rem}", rem
        return "connected", "Connected", ""

    if any(x in bl for x in ("idle", "ready", "waiting", "listening",
                              "standby", "available", "no connection")):
        return "idle", "Ready — no active connection", ""

    # Device responded but status unclear — it's at least online
    return "idle", "Online (status unclear)", ""

def _snmp_check(dev):
    """SNMP-based check for Prodys Quantum ST / APT WorldCast. Returns (state, detail, remote)."""
    host      = dev.get("host", "").strip()
    port      = int(dev.get("snmp_port", 161))
    community = dev.get("snmp_community", "public").strip() or "public"
    oid       = dev.get("snmp_oid", "").strip()

    # ── Prodys Quantum ST connection-state OID ──────────────────────────────
    # Prodys enterprise MIB: 1.3.6.1.4.1.32775 (Prodys)
    # Common path: quantumEncoderStatus or similar.
    # Without exact MIB we default to sysDescr as alive-check and let user
    # configure a specific OID via settings.
    if not oid:
        oid = "1.3.6.1.2.1.1.1.0"  # sysDescr — confirms device is alive

    val, err = _snmp_get(host, community, oid, port=port)

    if err == "pysnmp_not_installed":
        # pysnmp not available — fall back to TCP + HTTP
        _log("[Codec] pysnmp not installed; falling back to HTTP for SNMP devices")
        return _http_check(dev)

    if err:
        # SNMP failed — try HTTP fallback before declaring offline
        alive = _tcp_ok(host, 80)
        if alive:
            state, detail, rem = _http_check(dev)
            return state, f"(SNMP fail) {detail}", rem
        return "offline", f"SNMP: {err}", ""

    # Interpret the value
    # If user configured a specific connection-state OID the value is typically:
    #   Prodys: 1=connected, 0=idle  (INTEGER)
    #   APT:    varies by MIB version
    # For sysDescr we just confirm alive
    if val is not None:
        vl = val.lower()
        if val == "1" or "connect" in vl:
            return "connected", f"SNMP: {val}", ""
        if val == "0" or any(x in vl for x in ("idle", "ready", "standby")):
            return "idle", f"SNMP: {val}", ""
        # sysDescr or unknown OID — device is reachable
        return "idle", f"Online (SNMP ok: {val[:40]})", ""

    return "unknown", "No SNMP value returned", ""

# ── Device poll ─────────────────────────────────────────────────────────────
def _check_device(dev):
    """
    Poll one device.
    Single-codec devices return {state, detail, remote}.
    Dual-codec devices (quantum_st, apt) return {"a": {...}, "b": {...}}.
    """
    host   = dev.get("host", "").strip()
    port   = int(dev.get("port", 80))
    dtype  = dev.get("type", "custom")
    method = dev.get("method", _DTYPES.get(dtype, {}).get("method", "http"))
    dual   = dtype in _DUAL_CODEC_TYPES

    if not host:
        empty = {"state": "unknown", "detail": "No host configured", "remote": ""}
        return {"a": empty, "b": empty} if dual else empty

    if method == "tcp":
        ok    = _tcp_ok(host, port)
        single = {"state": "idle" if ok else "offline",
                  "detail": "TCP reachable" if ok else "TCP unreachable",
                  "remote": ""}
        return {"a": single, "b": dict(single)} if dual else single

    if method == "snmp":
        state, detail, remote = _snmp_check(dev)
        single = {"state": state, "detail": detail, "remote": remote}
        return {"a": single, "b": dict(single)} if dual else single

    # HTTP — fetch the page once and parse
    state, detail, remote, body = _http_check_with_body(dev)
    if not dual:
        return {"state": state, "detail": detail, "remote": remote}

    # Dual-codec: try to split the page into A and B
    if body and state != "offline":
        sa, da, ra, sb, db, rb = _scrape_dual(body)
        return {
            "a": {"state": sa, "detail": da, "remote": ra},
            "b": {"state": sb, "detail": db, "remote": rb},
        }
    # Device offline or no body — report same state for both
    empty = {"state": state, "detail": detail, "remote": remote}
    return {"a": empty, "b": dict(empty)}

# ── Background poller ───────────────────────────────────────────────────────
def _poller():
    _log("[Codec] Poller started")
    _next: dict = {}
    while True:
        now = time.time()
        with _lock:
            devs = list(_devices)
        for dev in devs:
            did      = dev.get("id", "")
            interval = max(10, int(dev.get("poll_interval", 30)))
            if not did or now < _next.get(did, 0):
                continue
            _next[did] = now + interval
            dtype = dev.get("type", "custom")
            dual  = dtype in _DUAL_CODEC_TYPES
            try:
                new = _check_device(dev)
            except Exception as e:
                err = {"state": "error", "detail": str(e)[:100], "remote": ""}
                new = {"a": err, "b": dict(err)} if dual else err

            now2 = time.time()
            with _lock:
                old = _status.get(did, {})
                if dual:
                    # Merge A and B independently, preserving last_change
                    updated = {}
                    for ch in ("a", "b"):
                        n_ch      = new.get(ch, {})
                        o_ch      = old.get(ch, {})
                        n_state   = n_ch.get("state", "unknown")
                        o_state   = o_ch.get("state", "unknown")
                        n_ch["last_checked"] = now2
                        n_ch["last_change"]  = (o_ch.get("last_change", now2)
                                                if n_state == o_state else now2)
                        updated[ch] = n_ch
                    updated["last_checked"] = now2
                    _status[did] = updated
                else:
                    old_state = old.get("state", "unknown")
                    new_state = new.get("state", "unknown")
                    new["last_checked"] = now2
                    new["last_change"]  = (old.get("last_change", now2)
                                           if old_state == new_state else now2)
                    _status[did] = new

            # Per-channel state-change alerts
            if dual:
                for ch in ("a", "b"):
                    o_state = old.get(ch, {}).get("state", "unknown")
                    n_state = new.get(ch, {}).get("state", "unknown")
                    ch_lbl  = f" Codec {'A' if ch == 'a' else 'B'}"
                    if o_state not in ("offline", "disconnected", "unknown", "error") \
                            and n_state in ("offline", "disconnected", "error"):
                        _do_alert(dev, n_state, new[ch].get("detail", ""), ch_lbl)
                    elif o_state in ("offline", "disconnected", "error") \
                            and n_state in ("connected", "idle"):
                        _do_recovery(dev, n_state, ch_lbl)
            else:
                o_state = old.get("state", "unknown")
                n_state = new.get("state", "unknown")
                if o_state not in ("offline", "disconnected", "unknown", "error") \
                        and n_state in ("offline", "disconnected", "error"):
                    _do_alert(dev, n_state, new.get("detail", ""))
                elif o_state in ("offline", "disconnected", "error") \
                        and n_state in ("connected", "idle"):
                    _do_recovery(dev, n_state)
        time.sleep(2)

# ── Alert log integration ───────────────────────────────────────────────────
def _get_alert_fn():
    global _alert_fn
    if _alert_fn:
        return _alert_fn
    for mod in sys.modules.values():
        if hasattr(mod, "_alert_log_append") and hasattr(mod, "hub_reports"):
            _alert_fn = mod._alert_log_append
            return _alert_fn
    return None

def _append_alert(stream, atype, message):
    fn = _get_alert_fn()
    if fn:
        try:
            fn({
                "id":              str(uuid.uuid4()),
                "ts":              time.strftime("%Y-%m-%d %H:%M:%S"),
                "stream":          stream,
                "type":            atype,
                "message":         message,
                "level_dbfs":      None,
                "rtp_loss_pct":    None,
                "rtp_jitter_ms":   None,
                "clip":            "",
                "ptp_state":       "",
                "ptp_offset_us":   0,
                "ptp_drift_us":    0,
                "ptp_jitter_us":   0,
                "ptp_gm":          "",
            })
        except Exception as e:
            _log(f"[Codec] Alert log error: {e}")

def _do_alert(dev, state, detail, channel_label: str = ""):
    name   = dev.get("name", dev.get("host", "?"))
    dtype  = _DTYPES.get(dev.get("type", ""), {}).get("label", dev.get("type", "Codec"))
    stream = dev.get("stream", name)
    label  = f"{name}{channel_label}"
    _append_alert(stream, "CODEC_FAULT",
                  f"[{dtype}] {label} — {_STATE_LABEL.get(state, state)}: {detail}")
    _log(f"[Codec] FAULT — {label} → {state}: {detail}")

def _do_recovery(dev, state, channel_label: str = ""):
    name   = dev.get("name", dev.get("host", "?"))
    dtype  = _DTYPES.get(dev.get("type", ""), {}).get("label", dev.get("type", "Codec"))
    stream = dev.get("stream", name)
    label  = f"{name}{channel_label}"
    _append_alert(stream, "CODEC_RECOVERY",
                  f"[{dtype}] {label} — recovered ({_STATE_LABEL.get(state, state)})")
    _log(f"[Codec] RECOVERED — {label} → {state}")

# ── Helpers ─────────────────────────────────────────────────────────────────
def _dev_by_id(did):
    with _lock:
        for d in _devices:
            if d.get("id") == did:
                return d
    return None

def _age(ts):
    if not ts:
        return "never"
    s = int(time.time() - ts)
    if s < 60:    return f"{s}s ago"
    if s < 3600:  return f"{s//60}m ago"
    if s < 86400: return f"{s//3600}h ago"
    return f"{s//86400}d ago"

def _duration(ts):
    if not ts:
        return ""
    s = int(time.time() - ts)
    if s < 60:    return f"{s}s"
    if s < 3600:  return f"{s//60}m {s%60}s"
    if s < 86400: return f"{s//3600}h {(s%3600)//60}m"
    return f"{s//86400}d {(s%86400)//3600}h"

# ── HTML template ───────────────────────────────────────────────────────────
_PAGE_TPL = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<title>Codecs — SignalScope</title>
<style nonce="{{csp_nonce()}}">
:root{--bg:#07142b;--bg2:#0d2040;--bg3:#112855;--ac:#17a8ff;--ac2:#0d8fd9;
--tx:#e0f0ff;--tx2:#7ab8d8;--br:#1a3a6a;--rd:#ef4444;--gn:#22c55e;--yw:#f59e0b;}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);font:14px/1.5 system-ui,sans-serif;min-height:100vh}
a{color:var(--ac);text-decoration:none}
.nav{background:var(--bg2);border-bottom:1px solid var(--br);padding:0 18px;
  display:flex;align-items:center;gap:8px;height:46px;flex-wrap:wrap}
.nav .logo{font-weight:700;color:var(--ac);font-size:1.05em;margin-right:8px}
.nav a{color:var(--tx2);font-size:13px;padding:4px 8px;border-radius:4px}
.nav a:hover,.nav a.cur{background:var(--bg3);color:var(--tx)}
.wrap{max-width:1100px;margin:0 auto;padding:24px 16px}
h1{font-size:1.3em;font-weight:600;color:var(--tx);margin-bottom:20px}
.toolbar{display:flex;align-items:center;gap:10px;margin-bottom:20px}
.btn{padding:6px 14px;border-radius:5px;border:none;cursor:pointer;font-size:13px;
  font-weight:500;transition:opacity .15s}
.btn:hover{opacity:.85}.btn:disabled{opacity:.4;cursor:default}
.btn.bp{background:var(--ac);color:#07142b}
.btn.bd{background:var(--rd);color:#fff}
.btn.bw{background:#1a3a6a;color:var(--tx)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.card{background:var(--bg2);border:1px solid var(--br);border-radius:8px;
  padding:16px;position:relative}
.card-head{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:10px}
.card-name{font-weight:600;font-size:1em;color:var(--tx)}
.card-type{font-size:11px;color:var(--tx2);background:var(--bg3);padding:2px 7px;
  border-radius:10px;margin-top:2px;display:inline-block}
.card-badge{display:flex;align-items:center;gap:6px;font-size:13px;font-weight:600}
.dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.card-detail{font-size:12px;color:var(--tx2);margin-top:6px;min-height:16px}
.card-remote{font-size:12px;color:var(--gn);margin-top:3px;min-height:14px}
.card-meta{font-size:11px;color:#4a7899;margin-top:8px;display:flex;
  justify-content:space-between}
/* Dual-codec A/B split */
.dual-row{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px}
.ch-block{background:var(--bg3);border-radius:5px;padding:8px 10px}
.ch-label{font-size:10px;font-weight:700;color:var(--tx2);letter-spacing:.05em;
  margin-bottom:4px}
.ch-badge{display:flex;align-items:center;gap:5px;font-size:12px;font-weight:600}
.ch-detail{font-size:11px;color:var(--tx2);margin-top:3px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.ch-remote{font-size:11px;color:#22c55e;margin-top:2px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.card-actions{display:flex;gap:6px;margin-top:12px}
.card-actions .btn{padding:4px 10px;font-size:12px}
.empty{text-align:center;color:var(--tx2);padding:60px 20px;font-size:15px}
.site-header{grid-column:1/-1;font-size:13px;font-weight:700;color:var(--tx2);
  text-transform:uppercase;letter-spacing:.06em;padding:6px 0 8px;
  border-bottom:1px solid var(--br);margin-top:12px}
.site-header:first-child{margin-top:0}
.site-grid{grid-column:1/-1;display:grid;
  grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.stale-badge{font-size:10px;background:#7c3a00;color:#ffd;border-radius:3px;
  padding:1px 5px;margin-left:4px;vertical-align:middle}
/* Modal */
.overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);
  z-index:100;align-items:center;justify-content:center}
.overlay.open{display:flex}
.modal{background:var(--bg2);border:1px solid var(--br);border-radius:10px;
  padding:24px;width:100%;max-width:480px;max-height:90vh;overflow-y:auto}
.modal h2{font-size:1.1em;margin-bottom:18px;color:var(--tx)}
.field{margin-bottom:14px}
.field label{display:block;font-size:12px;color:var(--tx2);margin-bottom:4px}
.field input,.field select{width:100%;background:var(--bg3);border:1px solid var(--br);
  border-radius:5px;color:var(--tx);padding:7px 10px;font-size:13px}
.field input:focus,.field select:focus{outline:none;border-color:var(--ac)}
.field .hint{font-size:11px;color:#4a7899;margin-top:3px}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.modal-btns{display:flex;justify-content:flex-end;gap:8px;margin-top:20px}
.snmp-fields{display:none}
/* Device page iframe modal */
.page-modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);
  z-index:200;flex-direction:column;align-items:center;justify-content:center}
.page-modal.open{display:flex}
.page-wrap{background:var(--bg2);border:1px solid var(--br);border-radius:8px;
  width:95vw;max-width:1000px;height:88vh;display:flex;flex-direction:column;overflow:hidden}
.page-bar{background:var(--bg3);padding:8px 14px;display:flex;align-items:center;
  gap:10px;border-bottom:1px solid var(--br);flex-shrink:0}
.page-bar .page-title{font-weight:600;flex:1;font-size:13px;color:var(--tx)}
.page-bar .session-badge{font-size:11px;padding:2px 8px;border-radius:10px;
  background:#22c55e22;color:#22c55e;border:1px solid #22c55e44}
.page-bar .session-badge.none{background:#ef444422;color:#ef4444;border-color:#ef444444}
.page-frame{flex:1;border:none;width:100%;background:#fff}
</style></head>
<body>
<nav class="nav">
  <span class="logo">📡 Codecs</span>
  <a href="{{overview_url}}">Overview</a>
  <a href="{{reports_url}}">Reports</a>
  <a href="/hub/codecs" class="cur">Codecs</a>
  <a href="{{settings_url}}">Settings</a>
</nav>
<div class="wrap">
  <div class="toolbar">
    <h1>Codec Monitor</h1>
    <button class="btn bp" id="add-btn">+ Add Device</button>
    <button class="btn bw" id="refresh-btn" style="margin-left:auto">↻ Refresh</button>
  </div>
  <div class="grid" id="grid">{{cards_html}}</div>
</div>

<!-- Device page proxy modal -->
<div class="page-modal" id="page-modal">
  <div class="page-wrap">
    <div class="page-bar">
      <span class="page-title" id="page-title">Device Page</span>
      <span class="session-badge none" id="session-badge">No session</span>
      <button class="btn bw" id="page-reload-btn" style="padding:4px 10px;font-size:12px">↻ Reload</button>
      <button class="btn bw" id="clear-session-btn" style="padding:4px 10px;font-size:12px">✕ Clear session</button>
      <button class="btn bw" id="page-close-btn" style="padding:4px 10px;font-size:12px">Close</button>
    </div>
    <iframe class="page-frame" id="page-frame" src="about:blank"></iframe>
  </div>
</div>

<!-- Add / Edit modal -->
<div class="overlay" id="overlay">
  <div class="modal">
    <h2 id="modal-title">Add Codec Device</h2>
    <div class="field">
      <label>Name</label>
      <input id="f-name" placeholder="e.g. Studio 1 Link">
    </div>
    <div class="row2">
      <div class="field">
        <label>Type</label>
        <select id="f-type">
          <option value="comrex">Comrex</option>
          <option value="tieline">Tieline</option>
          <option value="quantum_st">Prodys Quantum ST</option>
          <option value="apt">APT / WorldCast Quantum</option>
          <option value="custom">Custom (HTTP)</option>
          <option value="tcp">TCP Ping Only</option>
        </select>
      </div>
      <div class="field">
        <label>Host / IP</label>
        <input id="f-host" placeholder="192.168.1.10">
      </div>
    </div>
    <div class="row2">
      <div class="field">
        <label>Port</label>
        <input id="f-port" type="number" value="80" min="1" max="65535">
      </div>
      <div class="field">
        <label>Poll interval (s)</label>
        <input id="f-interval" type="number" value="30" min="10" max="300">
      </div>
    </div>
    <div class="field">
      <label>Associated stream (for alerts)</label>
      <input id="f-stream" placeholder="e.g. Studio 1 — leave blank to use device name">
    </div>
    <!-- HTTP fields -->
    <div id="http-fields">
      <div class="field">
        <label>HTTP endpoint path</label>
        <input id="f-endpoint" placeholder="Leave blank for device-type default">
        <div class="hint">Comrex: / &nbsp;|&nbsp; Tieline: /api/v1/status</div>
      </div>
      <div class="row2">
        <div class="field">
          <label>Username (optional)</label>
          <input id="f-user" autocomplete="off">
        </div>
        <div class="field">
          <label>Password (optional)</label>
          <input id="f-pwd" type="password" autocomplete="off">
        </div>
      </div>
      <div class="field">
        <label><input id="f-ssl" type="checkbox"> Use HTTPS</label>
      </div>
    </div>
    <!-- SNMP fields -->
    <div class="snmp-fields" id="snmp-fields">
      <div class="row2">
        <div class="field">
          <label>SNMP community</label>
          <input id="f-community" value="public">
        </div>
        <div class="field">
          <label>SNMP port</label>
          <input id="f-snmp-port" type="number" value="161">
        </div>
      </div>
      <div class="field">
        <label>Connection status OID (optional)</label>
        <input id="f-oid" placeholder="Leave blank for sysDescr alive-check">
        <div class="hint">Prodys: consult your device MIB. Blank = TCP/HTTP fallback.</div>
      </div>
    </div>
    <div class="modal-btns">
      <button class="btn bw" id="cancel-btn">Cancel</button>
      <button class="btn bp" id="save-btn">Save</button>
    </div>
  </div>
</div>

<script nonce="{{csp_nonce()}}">
var _editId = null;
var _autoRefresh = null;

function _csrf(){
  return (document.querySelector('meta[name="csrf-token"]')||{}).content||'';
}
function _post(url,body){
  return fetch(url,{method:'POST',headers:{'Content-Type':'application/json',
    'X-CSRFToken':_csrf()},body:JSON.stringify(body)}).then(r=>r.json());
}

// ── Port / field defaults per type ──
var _TYPE_DEFS = {
  comrex:     {port:80,  method:'http'},
  tieline:    {port:80,  method:'http'},
  quantum_st: {port:161, method:'snmp'},
  apt:        {port:161, method:'snmp'},
  custom:     {port:80,  method:'http'},
  tcp:        {port:80,  method:'tcp'},
};
document.getElementById('f-type').addEventListener('change', function(){
  var t = this.value;
  var def = _TYPE_DEFS[t]||{port:80,method:'http'};
  document.getElementById('f-port').value = def.port;
  var isSnmp = def.method === 'snmp';
  document.getElementById('http-fields').style.display = isSnmp?'none':'';
  document.getElementById('snmp-fields').style.display = isSnmp?'':'none';
});

function openAdd(){
  _editId = null;
  document.getElementById('modal-title').textContent = 'Add Codec Device';
  document.getElementById('f-name').value = '';
  document.getElementById('f-type').value = 'comrex';
  document.getElementById('f-type').dispatchEvent(new Event('change'));
  document.getElementById('f-host').value = '';
  document.getElementById('f-port').value = '80';
  document.getElementById('f-interval').value = '30';
  document.getElementById('f-stream').value = '';
  document.getElementById('f-endpoint').value = '';
  document.getElementById('f-user').value = '';
  document.getElementById('f-pwd').value = '';
  document.getElementById('f-ssl').checked = false;
  document.getElementById('f-community').value = 'public';
  document.getElementById('f-snmp-port').value = '161';
  document.getElementById('f-oid').value = '';
  document.getElementById('overlay').classList.add('open');
  document.getElementById('f-name').focus();
}

function openEdit(id, name, type, host, port, interval, stream, endpoint,
                  user, ssl, community, snmpPort, oid){
  _editId = id;
  document.getElementById('modal-title').textContent = 'Edit ' + name;
  document.getElementById('f-name').value = name;
  document.getElementById('f-type').value = type;
  document.getElementById('f-type').dispatchEvent(new Event('change'));
  document.getElementById('f-host').value = host;
  document.getElementById('f-port').value = port;
  document.getElementById('f-interval').value = interval;
  document.getElementById('f-stream').value = stream;
  document.getElementById('f-endpoint').value = endpoint;
  document.getElementById('f-user').value = user;
  document.getElementById('f-pwd').value = '';
  document.getElementById('f-ssl').checked = ssl;
  document.getElementById('f-community').value = community||'public';
  document.getElementById('f-snmp-port').value = snmpPort||161;
  document.getElementById('f-oid').value = oid||'';
  document.getElementById('overlay').classList.add('open');
}

function closeModal(){ document.getElementById('overlay').classList.remove('open'); }

document.getElementById('add-btn').addEventListener('click', openAdd);
document.getElementById('cancel-btn').addEventListener('click', closeModal);
document.getElementById('overlay').addEventListener('click', function(e){
  if(e.target===this) closeModal();
});

document.getElementById('save-btn').addEventListener('click', function(){
  var _sb = this;
  var body = {
    name:          document.getElementById('f-name').value.trim(),
    type:          document.getElementById('f-type').value,
    host:          document.getElementById('f-host').value.trim(),
    port:          parseInt(document.getElementById('f-port').value)||80,
    poll_interval: parseInt(document.getElementById('f-interval').value)||30,
    stream:        document.getElementById('f-stream').value.trim(),
    endpoint:      document.getElementById('f-endpoint').value.trim(),
    username:      document.getElementById('f-user').value.trim(),
    password:      document.getElementById('f-pwd').value,
    ssl:           document.getElementById('f-ssl').checked,
    snmp_community:document.getElementById('f-community').value.trim()||'public',
    snmp_port:     parseInt(document.getElementById('f-snmp-port').value)||161,
    snmp_oid:      document.getElementById('f-oid').value.trim(),
  };
  if(!body.name||!body.host){ _ssToast('Name and host are required.','warn'); return; }
  var url = _editId ? '/api/codecs/devices/'+_editId : '/api/codecs/devices';
  _btnLoad(_sb);
  _post(url, body).then(function(r){
    _btnReset(_sb);
    if(r.ok){ closeModal(); loadStatus(); }
    else _ssToast('Save failed: '+(r.error||'unknown'),'err');
  }).catch(function(e){ _btnReset(_sb); _ssToast('Save failed: '+e,'err'); });
});

// ── Card actions via event delegation ──
document.getElementById('grid').addEventListener('click', function(e){
  var rm   = e.target.closest('.rm-btn');
  var edit = e.target.closest('.edit-btn');
  var chk  = e.target.closest('.check-btn');
  var pg   = e.target.closest('.page-btn');
  if(rm){
    _ssConfirm('Remove device "'+rm.dataset.name+'"?',function(){
      _post('/api/codecs/devices/'+rm.dataset.id+'/remove',{}).then(function(r){
        if(r.ok) loadStatus(); else _ssToast('Remove failed','err');
      });
    },{danger:true,yesLabel:'Remove',title:'Remove Device'});
    return;
  }
  if(edit){
    var d = edit.dataset;
    openEdit(d.id, d.name, d.type, d.host, d.port, d.interval, d.stream||'',
             d.endpoint||'', d.user||'', d.ssl==='true',
             d.community||'public', d.snmpport||161, d.oid||'');
  }
  if(chk){
    chk.textContent='…'; chk.disabled=true;
    _post('/api/codecs/devices/'+chk.dataset.id+'/check',{}).then(function(){
      loadStatus();
    });
  }
  if(pg){ openDevicePage(pg.dataset.id, pg.dataset.name, pg.dataset.session==='true'); }
});

// ── Device page proxy modal ──
var _pageDevId = null;
function openDevicePage(did, name, hasSession){
  _pageDevId = did;
  document.getElementById('page-title').textContent = name + ' — Device Page';
  _updateSessionBadge(hasSession);
  document.getElementById('page-frame').src = '/hub/codecs/proxy/'+did+'/';
  document.getElementById('page-modal').classList.add('open');
}
function _updateSessionBadge(hasSession){
  var b = document.getElementById('session-badge');
  if(hasSession){
    b.textContent = '● Session active'; b.className = 'session-badge';
  } else {
    b.textContent = '○ No session — log in below'; b.className = 'session-badge none';
  }
}
document.getElementById('page-close-btn').addEventListener('click', function(){
  document.getElementById('page-modal').classList.remove('open');
  document.getElementById('page-frame').src = 'about:blank';
  loadStatus(); // refresh cards to show updated session state
});
document.getElementById('page-reload-btn').addEventListener('click', function(){
  var f = document.getElementById('page-frame');
  f.src = f.src; // force reload
});
document.getElementById('clear-session-btn').addEventListener('click', function(){
  if(!_pageDevId) return;
  _ssConfirm('Clear saved session? You will need to log in again.',function(){
  _post('/api/codecs/devices/'+_pageDevId+'/clear_session',{}).then(function(){
    _updateSessionBadge(false);
    var f = document.getElementById('page-frame');
    f.src = f.src;
  });
  });
});

// ── Status refresh ──
function _stateColour(s){
  return {connected:'#22c55e',idle:'#17a8ff',disconnected:'#f59e0b',
          offline:'#ef4444',error:'#ef4444',unknown:'#6b7280'}[s]||'#6b7280';
}
function _stateLabel(s){
  return {connected:'Connected',idle:'Idle / Ready',disconnected:'Disconnected',
          offline:'Offline',error:'Error',unknown:'Unknown'}[s]||s;
}
function _age(ts){
  if(!ts) return 'never';
  var s=Math.floor(Date.now()/1000-ts);
  if(s<60) return s+'s ago';
  if(s<3600) return Math.floor(s/60)+'m ago';
  return Math.floor(s/3600)+'h ago';
}
function _dur(ts){
  if(!ts) return '';
  var s=Math.floor(Date.now()/1000-ts);
  if(s<60) return s+'s';
  if(s<3600) return Math.floor(s/60)+'m '+s%60+'s';
  if(s<86400) return Math.floor(s/3600)+'h '+Math.floor((s%3600)/60)+'m';
  return Math.floor(s/86400)+'d '+Math.floor((s%86400)/3600)+'h';
}

function _chBlock(ch, st){
  if(!st) return '';
  var col = _stateColour(st.state||'unknown');
  var lbl = _stateLabel(st.state||'unknown');
  return '<div class="ch-block">'+
    '<div class="ch-label">CODEC '+ch.toUpperCase()+'</div>'+
    '<div class="ch-badge"><div class="dot" style="background:'+col+'"></div>'+
      '<span style="color:'+col+'">'+lbl+'</span></div>'+
    (st.remote?'<div class="ch-remote">⇄ '+_esc(st.remote)+'</div>':'')+
    '<div class="ch-detail">'+_esc(st.detail||'')+'</div>'+
  '</div>';
}

function loadStatus(){
  fetch('/api/codecs/status').then(r=>r.json()).then(function(data){
    var grid = document.getElementById('grid');
    if(!data.devices||!data.devices.length){
      grid.innerHTML='<div class="empty">No codec devices configured.<br>Click <b>+ Add Device</b> to get started.</div>';
      return;
    }

    // Group by site when multiple sites present
    var sites = {};
    data.devices.forEach(function(d){
      var s = d._site||'';
      if(!sites[s]) sites[s]=[];
      sites[s].push(d);
    });
    var siteNames = Object.keys(sites).sort();
    var multiSite = siteNames.length > 1;

    var html = '';
    siteNames.forEach(function(siteName){
      if(multiSite){
        html += '<div class="site-header">'+_esc(siteName)+'</div>';
        html += '<div class="site-grid">';
      }
      html += sites[siteName].map(function(d){
      var st    = d.status||{};
      var dual  = d.dual_codec;
      var dtype = d.type_label||d.type;
      var checked = _age(st.last_checked);

      // For card header badge: single = st directly, dual = worst of A/B
      var headState, headCol, headLbl;
      if(dual){
        var sa = (st.a||{}).state||'unknown', sb = (st.b||{}).state||'unknown';
        var rank = {connected:0,idle:1,unknown:2,disconnected:3,error:4,offline:4};
        headState = (rank[sa]||0) >= (rank[sb]||0) ? sa : sb;
        headCol = _stateColour(headState); headLbl = _stateLabel(headState);
      } else {
        headState = st.state||'unknown';
        headCol = _stateColour(headState); headLbl = _stateLabel(headState);
      }

      var bodyHtml = dual
        ? '<div class="dual-row">'+_chBlock('a',st.a)+_chBlock('b',st.b)+'</div>'
        : (st.remote?'<div class="card-remote">⇄ '+_esc(st.remote)+'</div>':'')+
          '<div class="card-detail">'+_esc(st.detail||'')+'</div>';

      var dur = st.last_change ? _dur(st.last_change) : '';

      return '<div class="card">'+
        '<div class="card-head">'+
          '<div><div class="card-name">'+_esc(d.name)+(d._stale?' <span class="stale-badge" title="No update from this site for >90s">stale</span>':'')+'</div>'+
          '<span class="card-type">'+_esc(dtype)+'</span>'+
          (d.has_session?' <span style="font-size:10px;color:#22c55e" title="Session active">● session</span>':'')+
          '</div>'+
          '<div class="card-badge">'+
            '<div class="dot" style="background:'+headCol+'"></div>'+
            '<span style="color:'+headCol+'">'+headLbl+'</span>'+
          '</div>'+
        '</div>'+
        bodyHtml+
        '<div class="card-meta">'+
          '<span>Checked '+checked+'</span>'+
          (!dual&&dur?'<span style="color:'+headCol+'">'+headLbl+' '+dur+'</span>':'')+
        '</div>'+
        '<div class="card-actions">'+
          '<button class="btn bw check-btn" data-id="'+d.id+'">↻ Check</button>'+
          '<button class="btn bw page-btn" data-id="'+d.id+'"'+
            ' data-name="'+_esc(d.name)+'"'+
            ' data-session="'+(d.has_session?'true':'false')+'"'+
            ' title="Open device web page (log in to enable status monitoring)"'+
          '>🌐</button>'+
          '<button class="btn bw edit-btn"'+
            ' data-id="'+d.id+'"'+
            ' data-name="'+_esc(d.name)+'"'+
            ' data-type="'+d.type+'"'+
            ' data-host="'+_esc(d.host)+'"'+
            ' data-port="'+d.port+'"'+
            ' data-interval="'+d.poll_interval+'"'+
            ' data-stream="'+_esc(d.stream||'')+'"'+
            ' data-endpoint="'+_esc(d.endpoint||'')+'"'+
            ' data-user="'+_esc(d.username||'')+'"'+
            ' data-ssl="'+!!d.ssl+'"'+
            ' data-community="'+_esc(d.snmp_community||'public')+'"'+
            ' data-snmpport="'+(d.snmp_port||161)+'"'+
            ' data-oid="'+_esc(d.snmp_oid||'')+'"'+
          '>✎ Edit</button>'+
          '<button class="btn bd rm-btn" data-id="'+d.id+'" data-name="'+_esc(d.name)+'">✕</button>'+
        '</div>'+
      '</div>';
    }).join('');
      if(multiSite) html += '</div>'; // close site-grid
    });
    grid.innerHTML = html;
  }).catch(function(e){ console.error('Status fetch failed',e); });
}

function _esc(s){ return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;')
  .replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

document.getElementById('refresh-btn').addEventListener('click', loadStatus);

// Auto-refresh every 30s
_autoRefresh = setInterval(loadStatus, 30000);
loadStatus();
</script>
</body></html>"""

# ── Dual-codec HTML scraper ────────────────────────────────────────────────
def _scrape_dual(body: str):
    """
    Split a two-column device page (Prodys Quantum ST, APT WorldCast) into
    left (Codec A) and right (Codec B) and return (state_a, detail_a, remote_a,
    state_b, detail_b, remote_b).

    Strategy:
    1. Look for explicit A/B or 1/2 column markers in the HTML (class/id names).
    2. Fall back to splitting the body at its midpoint and parsing each half.
    """

    def _extract(fragment: str, label: str):
        """Parse one column fragment and return (state, detail, remote)."""
        # Delegate to the existing keyword parser, tagged with a dummy dtype
        state, detail, remote = _parse_body("custom", fragment)
        return state, detail, remote

    # ── Strategy 1: explicit A/B markers in class or id ─────────────────────
    # Look for containers like: id/class containing "codec-a", "codec_a",
    # "channel-a", "encoder-a", "enc1", "codec1", "left", "portA" etc.
    _A_PATS = re.compile(
        r'(?:id|class)\s*=\s*["\'][^"\']*(?:codec[-_]?a|channel[-_]?a|enc(?:oder)?[-_]?a|'
        r'enc(?:oder)?[-_]?1|codec[-_]?1|channel[-_]?1|left[-_]?codec|portA|port[-_]?a)[^"\']*["\']',
        re.I,
    )
    _B_PATS = re.compile(
        r'(?:id|class)\s*=\s*["\'][^"\']*(?:codec[-_]?b|channel[-_]?b|enc(?:oder)?[-_]?b|'
        r'enc(?:oder)?[-_]?2|codec[-_]?2|channel[-_]?2|right[-_]?codec|portB|port[-_]?b)[^"\']*["\']',
        re.I,
    )
    m_a = _A_PATS.search(body)
    m_b = _B_PATS.search(body)

    if m_a and m_b:
        # Find each container block (next closing tag sequence after the match)
        def _block(start_pos: int) -> str:
            """Grab ~8 kB of HTML starting from start_pos."""
            return body[start_pos: start_pos + 8192]

        frag_a = _block(m_a.start())
        frag_b = _block(m_b.start())
        sa, da, ra = _extract(frag_a, "A")
        sb, db, rb = _extract(frag_b, "B")
        return sa, da, ra, sb, db, rb

    # ── Strategy 2: split at midpoint ────────────────────────────────────────
    mid = len(body) // 2
    frag_a = body[:mid]
    frag_b = body[mid:]
    sa, da, ra = _extract(frag_a, "A")
    sb, db, rb = _extract(frag_b, "B")
    return sa, da, ra, sb, db, rb


# ── SNMP trap receiver ──────────────────────────────────────────────────────
# Listens on UDP (default port 10162 — no root required).
# Configure your Quantum ST / APT device to send traps to the hub IP on this port.
# Traps update _status immediately without waiting for the next poll cycle.
#
# Trap → device mapping is by source IP.  If a trap arrives from an IP that
# matches a configured device's host, the device's status is updated instantly.
# For dual-codec devices the codec channel (A or B) is inferred from the
# varbind OID last-component (even index → A, odd → B, or explicit 1/2 suffix).

_TRAP_PORT = 10162   # overridden by global config (see register())

def _start_trap_receiver(port: int):
    import socket as _sock
    _log(f"[Codec] SNMP trap receiver starting on UDP {port}")
    try:
        s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
        s.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", port))
        s.settimeout(1.0)
    except Exception as e:
        _log(f"[Codec] Trap receiver failed to bind UDP {port}: {e}")
        return

    _log(f"[Codec] SNMP trap receiver listening on UDP {port}")
    while True:
        try:
            data, addr = s.recvfrom(65535)
            src_ip = addr[0]
            threading.Thread(target=_handle_trap, args=(data, src_ip),
                             daemon=True).start()
        except _sock.timeout:
            continue
        except Exception as e:
            _log(f"[Codec] Trap recv error: {e}")


def _handle_trap(data: bytes, src_ip: str):
    """Parse an incoming SNMP trap and update the matching device's status."""
    # Find device by host IP
    with _lock:
        matching = [d for d in _devices if d.get("host", "").strip() == src_ip]
    if not matching:
        return   # no configured device for this IP

    # Try pysnmp parsing first; fall back to raw heuristic
    trap_info = _parse_trap_pysnmp(data) or _parse_trap_raw(data)
    if not trap_info:
        return

    event_type = trap_info.get("event", "")   # "connected"|"disconnected"|"idle"|""
    channel    = trap_info.get("channel", "")  # "a"|"b"|""
    detail     = trap_info.get("detail", "SNMP trap")

    if not event_type:
        return   # unrecognised trap, ignore

    state = {
        "connected":    "connected",
        "disconnected": "disconnected",
        "idle":         "idle",
        "offline":      "offline",
        "alarm":        "offline",
    }.get(event_type, "unknown")

    now = time.time()
    for dev in matching:
        did   = dev.get("id", "")
        dtype = dev.get("type", "")
        dual  = dtype in _DUAL_CODEC_TYPES

        with _lock:
            old = _status.get(did, {})
            if dual:
                ch_key = channel if channel in ("a", "b") else "a"
                old_state = old.get(ch_key, {}).get("state", "unknown")
                new_sub   = {"state": state, "detail": f"[trap] {detail}",
                             "remote": trap_info.get("remote", ""),
                             "last_checked": now,
                             "last_change":  now if old_state != state
                                             else old.get(ch_key, {}).get("last_change", now)}
                updated = dict(old)
                updated[ch_key] = new_sub
                updated["last_checked"] = now
                _status[did] = updated
            else:
                old_state = old.get("state", "unknown")
                _status[did] = {
                    "state":        state,
                    "detail":       f"[trap] {detail}",
                    "remote":       trap_info.get("remote", ""),
                    "last_checked": now,
                    "last_change":  now if old_state != state else old.get("last_change", now),
                }

        # Fire alerts on state change
        if old_state not in ("offline", "disconnected", "unknown") and state in ("offline", "disconnected"):
            _do_alert(dev, state, detail,
                      channel_label=f" Codec {'A' if channel == 'a' else 'B'}" if dual and channel else "")
        elif old_state in ("offline", "disconnected") and state in ("connected", "idle"):
            _do_recovery(dev, state,
                         channel_label=f" Codec {'A' if channel == 'a' else 'B'}" if dual and channel else "")

        _log(f"[Codec] Trap from {src_ip}: {dev['name']}"
             + (f" [{channel.upper()}]" if dual and channel else "")
             + f" → {state}: {detail}")


def _parse_trap_pysnmp(data: bytes) -> dict | None:
    """Parse SNMP trap using pysnmp. Returns dict or None."""
    try:
        from pysnmp.proto import api as papi
        msg_ver = int(papi.decodeMessageVersion(data))
        p_mod   = papi.PROTOCOL_MODULES[msg_ver]
        msg, _  = p_mod.Message(), None
        from pyasn1.codec.ber import decoder as _ber
        msg, _  = _ber.decode(data, asn1Spec=p_mod.Message())
        pdu     = p_mod.apiMessage.getPDU(msg)

        if msg_ver == papi.protoVersion1:
            pdu_type = pdu.__class__.__name__
            enterprise = str(p_mod.apiTrapPDU.getEnterprise(pdu))
            vbs        = p_mod.apiTrapPDU.getVarBinds(pdu)
        else:
            pdu_type = pdu.__class__.__name__
            vbs      = p_mod.apiPDU.getVarBinds(pdu)
            enterprise = ""

        return _interpret_varbinds(vbs, enterprise)
    except Exception:
        return None


def _parse_trap_raw(data: bytes) -> dict | None:
    """
    Very simple raw SNMP heuristic — look for connection-state keywords in the
    printable portion of the trap PDU bytes. Used when pysnmp is unavailable.
    """
    try:
        printable = data.decode("latin-1", errors="replace").lower()
        if any(x in printable for x in ("connect", "link up", "established")):
            return {"event": "connected", "detail": "trap (raw)", "channel": ""}
        if any(x in printable for x in ("disconnect", "link down", "terminated", "lost")):
            return {"event": "disconnected", "detail": "trap (raw)", "channel": ""}
        if any(x in printable for x in ("alarm", "fault", "fail")):
            return {"event": "offline", "detail": "trap (raw)", "channel": ""}
    except Exception:
        pass
    return None


def _interpret_varbinds(vbs, enterprise: str = "") -> dict:
    """
    Convert pysnmp varbinds to a dict with event/channel/detail/remote.

    Prodys enterprise OID: 1.3.6.1.4.1.32775
    APT/WorldCast OID:     1.3.6.1.4.1.7034
    Channel inference:
    - OID ends in .1.x or contains "EncoderA" / "ChannelA" → channel "a"
    - OID ends in .2.x or contains "EncoderB" / "ChannelB" → channel "b"
    """
    channel = ""
    detail  = ""
    event   = ""
    remote  = ""

    for oid, val in vbs:
        oid_str = str(oid)
        val_str = str(val).strip()
        val_low = val_str.lower()

        # ── Channel detection ────────────────────────────────────────────────
        if not channel:
            if re.search(r'\b(encoder|channel|codec)[-_]?a\b', oid_str, re.I) or \
               oid_str.endswith(".1.0") or re.search(r'\.1\.\d+$', oid_str):
                channel = "a"
            elif re.search(r'\b(encoder|channel|codec)[-_]?b\b', oid_str, re.I) or \
                 oid_str.endswith(".2.0") or re.search(r'\.2\.\d+$', oid_str):
                channel = "b"

        # ── Event detection ──────────────────────────────────────────────────
        if not event:
            if any(x in val_low for x in ("connect", "established", "up", "linked")):
                event = "connected"
            elif any(x in val_low for x in ("disconnect", "down", "lost", "terminat")):
                event = "disconnected"
            elif any(x in val_low for x in ("alarm", "fault", "fail", "error")):
                event = "offline"
            elif any(x in val_low for x in ("idle", "ready", "standby", "waiting")):
                event = "idle"

        # ── Remote name ──────────────────────────────────────────────────────
        if not remote and re.search(r'(remote|peer|caller|address)', oid_str, re.I):
            if val_str and val_str not in ("0", ""):
                remote = val_str[:60]

        detail = detail or val_str[:80]

    return {"event": event, "channel": channel, "detail": detail, "remote": remote}


# ── Proxy helpers ──────────────────────────────────────────────────────────
def _rewrite_html(html: str, did: str, current_path: str) -> str:
    """
    Rewrite href/src/action URLs in HTML so all navigation stays inside the
    SignalScope proxy.  Absolute paths on the device become proxy paths;
    relative paths are resolved against the current proxy path.
    External URLs (different host) are left untouched.
    A <base target="_self"> is injected so anchor clicks stay in the iframe.
    """
    proxy_base = f"/hub/codecs/proxy/{did}"

    def rw(url: str) -> str:
        url = url.strip()
        if not url:
            return url
        # Leave these alone
        if url.startswith(("#", "data:", "javascript:", "mailto:", "//")):
            return url
        # Absolute URL pointing to a different host — leave alone
        if url.startswith(("http://", "https://")):
            return url
        # Absolute path on the device (e.g. /api/status)
        if url.startswith("/"):
            return f"{proxy_base}{url}"
        # Relative path — resolve against current directory
        parent = current_path.rsplit("/", 1)[0] if "/" in current_path else ""
        base   = f"{proxy_base}/{parent}/" if parent else f"{proxy_base}/"
        return base + url

    def replace_attr(m):
        attr, quote, url = m.group(1), m.group(2), m.group(3)
        return f'{attr}={quote}{rw(url)}{quote}'

    html = re.sub(r'(href|src|action)=(["\'])([^"\']*)\2', replace_attr, html, flags=re.I)

    # Inject <base target="_self"> so links stay inside the iframe, not open in parent
    if "</head>" in html.lower():
        html = re.sub(r'</head>', '<base target="_self"></head>', html, count=1, flags=re.I)

    return html


def _proxy_fetch(dev, url_path: str, method: str = "GET",
                 post_data: bytes = b"", content_type: str = "",
                 extra_headers: dict = None):
    """
    Fetch a URL through the device proxy, using the device's cookie jar.
    Returns (body_bytes, content_type_str, status_code).
    Raises urllib.error.HTTPError / Exception on failure.
    """
    host    = dev.get("host", "").strip()
    port    = int(dev.get("port", 80))
    use_ssl = dev.get("ssl", False)
    scheme  = "https" if use_ssl else "http"
    did     = dev.get("id", "")

    target = f"{scheme}://{host}:{port}/{url_path.lstrip('/')}"

    jar     = _get_jar(did)
    opener  = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    hdrs    = [("User-Agent", "SignalScope-Codec/1.0")]
    if extra_headers:
        hdrs += list(extra_headers.items())
    opener.addheaders = hdrs

    if method == "POST":
        req = urllib.request.Request(target, data=post_data, method="POST")
        if content_type:
            req.add_header("Content-Type", content_type)
    else:
        req = urllib.request.Request(target)

    with opener.open(req, timeout=15) as r:
        body = r.read(4 * 1024 * 1024)   # 4 MB cap
        ct   = r.headers.get("Content-Type", "application/octet-stream")
        return body, ct, r.status


# ── register() ─────────────────────────────────────────────────────────────
def register(app, ctx):
    global _log, _cfg_file, _monitor

    _monitor    = ctx["monitor"]
    _log        = _monitor.log
    _cfg_file   = Path(ctx["monitor"].app_cfg.__class__.__module__
                       .replace(".", "/")).parent / "codec_devices.json"

    # Resolve cfg file relative to signalscope.py location via __file__ fallback
    for mod in sys.modules.values():
        if hasattr(mod, "_alert_log_append") and hasattr(mod, "hub_reports"):
            try:
                import inspect
                src = inspect.getfile(mod)
                _cfg_file = Path(src).parent / "codec_devices.json"
            except Exception:
                pass
            break

    _load()
    login_req  = ctx["login_required"]
    csrf_dec   = ctx["csrf_protect"]
    mobile_req = ctx.get("mobile_api_required", ctx["login_required"])
    hub_server = ctx.get("hub_server")
    is_hub     = hub_server is not None   # True on hub or both-mode nodes

    # Always start the local poller (polls devices configured on this node)
    t = threading.Thread(target=_poller, daemon=True, name="CodecPoller")
    t.start()

    # On client/both nodes: push status to hub + run SNMP trap receiver
    # (Traps are sent from codecs on the LAN the client is on)
    trap_port = 10162
    try:
        meta_file = _cfg_file.parent / "codec_trap_port"
        if meta_file.exists():
            trap_port = int(meta_file.read_text().strip())
    except Exception:
        pass
    tr = threading.Thread(target=_start_trap_receiver, args=(trap_port,),
                          daemon=True, name="CodecTrapRecv")
    tr.start()

    cfg = _monitor.app_cfg
    if cfg.hub.hub_url:
        # This node has a hub to push to (client or both-mode)
        pt = threading.Thread(target=_push_thread, daemon=True, name="CodecPush")
        pt.start()
        _log("[Codec] Client push thread started")

    # ── Web routes ────────────────────────────────────────────────────────
    @app.get("/hub/codecs")
    @login_req
    def codec_page():
        from flask import render_template_string
        # Render initial cards server-side; JS will keep them updated
        cards = _render_cards()
        return render_template_string(
            _PAGE_TPL,
            cards_html=cards,
            overview_url="/hub/status" if is_hub else "/",
            reports_url="/hub/reports" if is_hub else "/reports",
            settings_url="/hub/settings" if is_hub else "/settings",
        )

    @app.get("/api/codecs/status")
    @login_req
    def codec_status():
        from flask import jsonify

        # ── Local devices (configured on this node) ──────────────────────
        with _lock:
            devs = list(_devices)
            stat = dict(_status)

        cfg_local = _monitor.app_cfg
        local_site = cfg_local.hub.site_name or "(local)"

        out = []
        for d in devs:
            did = d.get("id", "")
            row = dict(d)
            row.pop("password", None)
            row["type_label"]  = _DTYPES.get(d.get("type",""), {}).get("label", d.get("type",""))
            row["has_session"] = _has_session(did)
            row["dual_codec"]  = d.get("type","") in _DUAL_CODEC_TYPES
            row["_site"]       = local_site
            row["_local"]      = True
            row["status"]      = stat.get(did, {"state": "unknown", "detail": "Not yet checked"})
            out.append(row)

        # ── Remote sites (hub only) ───────────────────────────────────────
        if is_hub:
            with _hub_codec_lock:
                remote_cache = dict(_hub_codec_cache)
            stale_thresh = time.time() - 90   # >90 s without update = stale
            for site_name, entry in sorted(remote_cache.items()):
                stale = entry.get("seen", 0) < stale_thresh
                for d in entry.get("devices", []):
                    did  = d.get("id", "")
                    row  = dict(d)
                    row.pop("password", None)
                    row["type_label"] = _DTYPES.get(d.get("type",""), {}).get("label", d.get("type",""))
                    row["has_session"] = False   # session lives on the remote client
                    row["dual_codec"]  = d.get("type","") in _DUAL_CODEC_TYPES
                    row["_site"]       = site_name
                    row["_local"]      = False
                    row["_stale"]      = stale
                    row["status"]      = entry.get("status", {}).get(did,
                                         {"state": "unknown", "detail": "No data yet"})
                    out.append(row)

        return jsonify({"devices": out, "is_hub": is_hub})

    @app.post("/api/codecs/devices")
    @login_req
    @csrf_dec
    def codec_add():
        from flask import request, jsonify
        body = request.get_json(force=True) or {}
        dev  = _build_dev(body)
        with _lock:
            _devices.append(dev)
            _save()
        _log(f"[Codec] Added device: {dev['name']} ({dev['host']})")
        return jsonify({"ok": True, "id": dev["id"]})

    @app.post("/api/codecs/devices/<did>")
    @login_req
    @csrf_dec
    def codec_update(did):
        from flask import request, jsonify
        body = request.get_json(force=True) or {}
        with _lock:
            for i, d in enumerate(_devices):
                if d.get("id") == did:
                    updated = _build_dev(body, existing=d)
                    _devices[i] = updated
                    _save()
                    _log(f"[Codec] Updated device: {updated['name']}")
                    return jsonify({"ok": True})
        return jsonify({"error": "not found"}), 404

    @app.post("/api/codecs/devices/<did>/remove")
    @login_req
    @csrf_dec
    def codec_remove(did):
        from flask import jsonify
        with _lock:
            before = len(_devices)
            _devices[:] = [d for d in _devices if d.get("id") != did]
            _status.pop(did, None)
            if len(_devices) < before:
                _save()
                _log(f"[Codec] Removed device {did}")
                return jsonify({"ok": True})
        return jsonify({"error": "not found"}), 404

    @app.post("/api/codecs/devices/<did>/check")
    @login_req
    @csrf_dec
    def codec_force_check(did):
        from flask import jsonify
        dev = _dev_by_id(did)
        if not dev:
            return jsonify({"error": "not found"}), 404
        try:
            new = _check_device(dev)
        except Exception as e:
            new = {"state": "error", "detail": str(e)[:100], "remote": ""}
        new["last_checked"] = time.time()
        with _lock:
            old       = _status.get(did, {})
            old_state = old.get("state", "unknown")
            new_state = new["state"]
            new["last_change"] = old.get("last_change", new["last_checked"]) \
                if old_state == new_state else new["last_checked"]
            _status[did] = new
        if old_state not in ("offline", "disconnected", "unknown", "error") \
                and new_state in ("offline", "disconnected", "error"):
            _do_alert(dev, new_state, new.get("detail", ""))
        elif old_state in ("offline", "disconnected", "error") \
                and new_state in ("connected", "idle"):
            _do_recovery(dev, new_state)
        return jsonify({"ok": True, "status": new})

    # ── Device page proxy ─────────────────────────────────────────────────
    # Proxies the device's own web interface through SignalScope so the user
    # can log in via the device's native UI.  The server-side cookie jar
    # captures the session, which is then reused for status polling.

    @app.route("/hub/codecs/proxy/<did>", methods=["GET", "POST"],
               strict_slashes=False)
    @app.route("/hub/codecs/proxy/<did>/<path:url_path>", methods=["GET", "POST"])
    @login_req
    def codec_proxy(did, url_path=""):
        from flask import request, Response
        dev = _dev_by_id(did)
        if not dev:
            return Response("Device not found", status=404, content_type="text/plain")

        qs = request.query_string.decode("utf-8", errors="replace")
        full_path = (url_path + ("?" + qs if qs else ""))

        try:
            body, ct, status = _proxy_fetch(
                dev, full_path,
                method       = request.method,
                post_data    = request.get_data() if request.method == "POST" else b"",
                content_type = request.headers.get("Content-Type", ""),
            )
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read()
                err_ct   = e.headers.get("Content-Type", "text/html")
                if "text/html" in err_ct:
                    html = _rewrite_html(err_body.decode("utf-8", errors="replace"), did, url_path)
                    return Response(html.encode("utf-8"), status=e.code,
                                    content_type="text/html; charset=utf-8")
            except Exception:
                pass
            return Response(f"HTTP {e.code}: {e.reason}", status=e.code,
                            content_type="text/plain")
        except Exception as e:
            return Response(f"Proxy error: {e}", status=502, content_type="text/plain")

        # Rewrite HTML so all links stay in the proxy
        if "text/html" in ct:
            html = _rewrite_html(body.decode("utf-8", errors="replace"), did, url_path)
            return Response(html.encode("utf-8"), status=status,
                            content_type="text/html; charset=utf-8")

        return Response(body, status=status, content_type=ct)

    @app.post("/api/codecs/devices/<did>/clear_session")
    @login_req
    @csrf_dec
    def codec_clear_session(did):
        from flask import jsonify
        _clear_session(did)
        _log(f"[Codec] Session cleared for device {did}")
        return jsonify({"ok": True})

    # ── Hub: receive codec status pushed by client nodes ─────────────────
    @app.post("/api/codecs/client_status")
    def codec_client_status():
        """
        Receives codec status pushed by client nodes every 15 s.
        Verifies the HMAC signature using the hub's shared secret.
        No login_req — uses hub signature auth (same as heartbeat).
        """
        from flask import request, jsonify
        cfg_hub = _monitor.app_cfg
        secret  = cfg_hub.hub.secret_key or ""

        raw = request.get_data()
        if secret:
            sig  = request.headers.get("X-Hub-Sig", "")
            ts_h = request.headers.get("X-Hub-Ts",  "0")
            try:
                ts = float(ts_h)
            except ValueError:
                return jsonify({"error": "bad ts"}), 400
            # Verify using same scheme as _sign()
            key      = hashlib.sha256(f"{secret}:signing".encode()).digest()
            msg      = f"{int(ts)}:".encode() + raw
            expected = _hmac_mod.new(key, msg, hashlib.sha256).hexdigest()
            if not _hmac_mod.compare_digest(expected, sig):
                return jsonify({"error": "forbidden"}), 403

        try:
            data = json.loads(raw)
        except Exception:
            return jsonify({"error": "bad json"}), 400

        site    = str(data.get("site", "")).strip()
        devices = data.get("devices", [])
        status  = data.get("status", {})
        ts_recv = data.get("ts", time.time())

        if not site:
            return jsonify({"error": "missing site"}), 400

        with _hub_codec_lock:
            _hub_codec_cache[site] = {
                "devices": devices,
                "status":  status,
                "ts":      ts_recv,
                "seen":    time.time(),
            }

        return jsonify({"ok": True})

    # ── Mobile API ────────────────────────────────────────────────────────
    @app.get("/api/mobile/codecs/status")
    @mobile_req
    def api_mobile_codecs_status():
        from flask import jsonify
        with _lock:
            devs = list(_devices)
            stat = dict(_status)
        out = []
        now = time.time()
        for d in devs:
            did  = d.get("id", "")
            st   = stat.get(did, {})
            dual = d.get("type", "") in _DUAL_CODEC_TYPES

            def _ch_obj(sub):
                return {
                    "state":       sub.get("state", "unknown"),
                    "state_label": _STATE_LABEL.get(sub.get("state", "unknown"), "Unknown"),
                    "detail":      sub.get("detail", ""),
                    "remote":      sub.get("remote", ""),
                    "last_change": sub.get("last_change"),
                    "duration_s":  int(now - sub["last_change"]) if sub.get("last_change") else None,
                }

            row = {
                "id":           did,
                "name":         d.get("name", ""),
                "type":         d.get("type", ""),
                "type_label":   _DTYPES.get(d.get("type",""), {}).get("label", d.get("type","")),
                "dual_codec":   dual,
                "stream":       d.get("stream", ""),
                "host":         d.get("host", ""),
                "last_checked": st.get("last_checked"),
            }
            if dual:
                row["codec_a"] = _ch_obj(st.get("a", {}))
                row["codec_b"] = _ch_obj(st.get("b", {}))
                # Top-level state = worst of A/B for simple checks
                rank = {"connected": 0, "idle": 1, "unknown": 2,
                        "disconnected": 3, "error": 4, "offline": 4}
                sa = st.get("a", {}).get("state", "unknown")
                sb = st.get("b", {}).get("state", "unknown")
                row["state"] = sa if rank.get(sa, 0) >= rank.get(sb, 0) else sb
            else:
                row["state"]       = st.get("state", "unknown")
                row["state_label"] = _STATE_LABEL.get(st.get("state", "unknown"), "Unknown")
                row["detail"]      = st.get("detail", "")
                row["remote"]      = st.get("remote", "")
                row["last_change"] = st.get("last_change")
                row["duration_s"]  = int(now - st["last_change"]) if st.get("last_change") else None
            out.append(row)
        return jsonify({"codecs": out, "ts": time.time()})

    _log("[Codec] Plugin registered — polling started")

# ── Helpers ─────────────────────────────────────────────────────────────────
def _build_dev(body, existing=None):
    """Build a device dict from POST body, preserving id and password if not changed."""
    dev = dict(existing) if existing else {"id": str(uuid.uuid4())}
    dev["name"]          = str(body.get("name", "")).strip()[:80]
    dev["type"]          = str(body.get("type", "custom")).strip()
    dev["host"]          = str(body.get("host", "")).strip()
    dev["port"]          = max(1, min(65535, int(body.get("port") or
                               _DTYPES.get(dev["type"], {}).get("port", 80))))
    dev["poll_interval"] = max(10, min(300, int(body.get("poll_interval") or 30)))
    dev["stream"]        = str(body.get("stream", "")).strip()[:100]
    dev["endpoint"]      = str(body.get("endpoint", "")).strip()
    dev["username"]      = str(body.get("username", "")).strip()
    dev["ssl"]           = bool(body.get("ssl", False))
    dev["snmp_community"]= str(body.get("snmp_community", "public")).strip() or "public"
    dev["snmp_port"]     = max(1, min(65535, int(body.get("snmp_port") or 161)))
    dev["snmp_oid"]      = str(body.get("snmp_oid", "")).strip()
    dev["method"]        = _DTYPES.get(dev["type"], {}).get("method", "http")
    # Only overwrite password if a new one was supplied
    new_pwd = str(body.get("password", ""))
    if new_pwd:
        dev["password"] = new_pwd
    elif "password" not in dev:
        dev["password"] = ""
    return dev

def _render_cards():
    """Server-side render of cards for initial page load."""
    with _lock:
        devs = list(_devices)
        stat = dict(_status)
    if not devs:
        return '<div class="empty">No codec devices configured.<br>' \
               'Click <b>+ Add Device</b> to get started.</div>'
    html = []
    for d in devs:
        did    = d.get("id", "")
        st     = stat.get(did, {"state": "unknown", "detail": "Not yet checked"})
        state  = st.get("state", "unknown")
        col    = _STATE_COLOUR.get(state, "#6b7280")
        lbl    = _STATE_LABEL.get(state, state)
        dtype  = _DTYPES.get(d.get("type",""), {}).get("label", d.get("type",""))
        detail = st.get("detail", "")
        remote = st.get("remote", "")
        html.append(
            f'<div class="card">'
            f'<div class="card-head">'
            f'<div><div class="card-name">{_he(d["name"])}</div>'
            f'<span class="card-type">{_he(dtype)}</span></div>'
            f'<div class="card-badge">'
            f'<div class="dot" style="background:{col}"></div>'
            f'<span style="color:{col}">{lbl}</span></div></div>'
            + (f'<div class="card-remote">⇄ {_he(remote)}</div>' if remote else '')
            + f'<div class="card-detail">{_he(detail)}</div>'
            f'<div class="card-meta"><span>Not yet checked</span></div>'
            f'<div class="card-actions">'
            f'<button class="btn bw check-btn" data-id="{did}">↻ Check</button>'
            f'<button class="btn bw edit-btn"'
            f' data-id="{did}" data-name="{_he(d["name"])}" data-type="{d.get("type","")}"'
            f' data-host="{_he(d.get("host",""))}" data-port="{d.get("port",80)}"'
            f' data-interval="{d.get("poll_interval",30)}"'
            f' data-stream="{_he(d.get("stream",""))}"'
            f' data-endpoint="{_he(d.get("endpoint",""))}"'
            f' data-user="{_he(d.get("username",""))}"'
            f' data-ssl="{str(bool(d.get("ssl",False))).lower()}"'
            f' data-community="{_he(d.get("snmp_community","public"))}"'
            f' data-snmpport="{d.get("snmp_port",161)}"'
            f' data-oid="{_he(d.get("snmp_oid",""))}"'
            f'>✎ Edit</button>'
            f'<button class="btn bd rm-btn" data-id="{did}"'
            f' data-name="{_he(d["name"])}">✕</button>'
            f'</div></div>'
        )
    return "\n".join(html)

def _he(s):
    """HTML-escape a string."""
    return str(s).replace("&","&amp;").replace('"','&quot;').replace("<","&lt;").replace(">","&gt;")
