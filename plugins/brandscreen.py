# brandscreen.py — SignalScope Brand Screen plugin
# Animated full-screen studio branding display with studios, stations,
# live now-playing, orbit rings, SSE-driven instant assignment changes,
# brand-hued backgrounds, and audio-level reactive animations.
# Drop into the plugins/ subdirectory.

import os, json, uuid, threading, mimetypes, functools, colorsys, time as _time, hashlib, datetime as _dt
import queue as _queue
from flask import (request, jsonify, render_template_string,
                   send_file, session, Response, stream_with_context, g, make_response)

SIGNALSCOPE_PLUGIN = {
    "id":       "brandscreen",
    "label":    "Brand Screen",
    "url":      "/hub/brandscreen",
    "icon":     "📺",
    "hub_only": True,
    "version":  "1.3.24",
}

_BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
_CFG_PATH    = os.path.join(_BASE_DIR, "brandscreen_cfg.json")
_LOGO_DIR    = os.path.join(_BASE_DIR, "brandscreen_logos")
_SB_CFG_PATH = os.path.join(_BASE_DIR, "studioboard_cfg.json")   # for mic-live polling
_LOCK        = threading.Lock()
_NOTIFY      = {}              # studio_id → list[queue.Queue]
_NLOCK       = threading.Lock()
_takeovers         = {}    # studio_id  → {"title":str,"text":str,"ts":float}
_station_takeovers = {}    # station_id → {"title":str,"text":str,"ts":float}
_TLOCK             = threading.Lock()
_STLOCK            = threading.Lock()
_mic_live    = {}              # bs_studio_id → bool  (last known mic state)
_MLOCK       = threading.Lock()

# ─────────────────────────────────────── schedule state ──────────────────────
# Runtime state tracking active schedule takeovers — not persisted.
# Per studio: pre_station_id = what to revert to; rest_override = True when a
# REST call came in during the scheduled window (suppresses the end-of-window
# revert so the operator assignment sticks).
_schedule_state: dict = {}   # studio_id → {pre_station_id, active_schedule_id, rest_override}
_SCHED_LOCK = threading.Lock()

# ─────────────────────────────────── CueServer pending commands ───────────────
# site_name → {"host": str, "cmd": str}  — latest brand-change command per site
_cs_pending: dict = {}
_cs_lock    = threading.Lock()
# Admin-initiated command queue: site → list[{action, host, ...}]
# Popped from the front by the client poller; responses stored in _cs_result.
_cs_admin_queue: dict = {}
_cs_admin_lock  = threading.Lock()
# Latest CueServer result per site, posted back by the client.
# site → {ts, action, data, error}
_cs_result: dict = {}
_cs_result_lock = threading.Lock()

os.makedirs(_LOGO_DIR, exist_ok=True)

# ─────────────────────────────────────────────── config helpers ───────────────

def _cfg_load():
    try:
        with open(_CFG_PATH) as f:
            d = json.load(f)
            if "schedules" not in d:
                d["schedules"] = []
            return d
    except Exception:
        return {"stations": [], "studios": [], "api_key": "", "schedules": []}

def _cfg_save(cfg):
    with _LOCK:
        with open(_CFG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)

def _get_station(cfg, sid):
    for s in cfg.get("stations", []):
        if s.get("id") == sid:
            return s
    return None

def _get_studio(cfg, sid):
    for s in cfg.get("studios", []):
        if s.get("id") == sid:
            return s
    return None

def _new_station():
    return {
        "id":                 str(uuid.uuid4())[:8],
        "name":               "My Station",
        "enabled":            True,
        "brand_colour":       "#17a8ff",
        "accent_colour":      "#ffffff",
        "bg_style":           "particles",
        "logo_anim":          "orbit",
        "show_clock":         True,
        "show_on_air":        True,
        "show_now_playing":   True,
        "np_source":          "none",
        "np_zetta_key":       "",
        "np_api_url":         "",
        "np_api_title_path":  "now_playing.song.title",
        "np_api_artist_path": "now_playing.song.artist",
        "np_manual":          "",
        "message":            "",
        "level_key":          "",   # "site|stream" for audio reactivity
        "full_screen_logo":   False, # display logo only — no backgrounds, animations, clock, NP
        "cueserver_cmd":      "",    # CueServer CGI command string (e.g. "Cue 5 Go")
        "cs_brightness":      100,   # LED brightness for this brand (0–100, default 100)
    }

def _new_studio():
    return {
        "id":           str(uuid.uuid4())[:8],
        "name":         "Studio",
        "station_id":   "",
        "token":        str(uuid.uuid4()).replace("-", ""),
        "sb_studio_id":    "",   # linked studioboard studio for mic-live detection
        "cueserver_site": "",    # hub site name where CueServer lives
        "cueserver_host": "",    # CueServer hostname / IP on that site's LAN
        "cs_zone_name":   "",    # CueServer zone name (e.g. "Studio1LED") — if set,
                                 # commands use "ZONE "…" Preset N ON" format instead of "Cue N Go"
        # List of DMX strips: [{name, ch_r, ch_g, ch_b, ch_w}]
        # ch_w = 0 means no white channel (RGB only).
        # ch_w > 0 enables RGBW — white component extracted from the brand colour.
        # Brightness is per-brand (cs_brightness on the station), NOT per strip.
        "cs_strips": [],
    }

def _ensure_api_key(cfg):
    if not cfg.get("api_key"):
        cfg["api_key"] = str(uuid.uuid4()).replace("-", "")
        _cfg_save(cfg)
    return cfg["api_key"]

# ─────────────────────────────────── mic-live monitor ─────────────────────────

def _sb_cfg_load():
    """Load studioboard config without raising. Returns {} on any error."""
    try:
        with open(_SB_CFG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}

def _mic_monitor_loop():
    """Background thread: poll studioboard_cfg.json every 2 s and push
    mic_live / mic_down SSE events to any brand-screen studios that have a
    linked studioboard studio set."""
    while True:
        try:
            _time.sleep(2)
            bs_cfg = _cfg_load()
            sb_cfg = _sb_cfg_load()
            sb_studios = {s["id"]: s for s in sb_cfg.get("studios", [])}
            for bs_studio in bs_cfg.get("studios", []):
                bs_id = bs_studio.get("id", "")
                sb_id = (bs_studio.get("sb_studio_id") or "").strip()
                if not bs_id or not sb_id:
                    continue
                sb_s     = sb_studios.get(sb_id)
                new_live = bool(sb_s.get("mic_live", False)) if sb_s else False
                with _MLOCK:
                    old_live = _mic_live.get(bs_id, False)
                    changed  = (new_live != old_live)
                    if changed:
                        _mic_live[bs_id] = new_live
                if changed:
                    _notify_studio_msg(bs_id, "mic_live" if new_live else "mic_down")
        except Exception:
            pass

def _check_schedules():
    """Check all schedules and activate/deactivate as needed. Called every 30 s."""
    cfg = _cfg_load()
    schedules = cfg.get("schedules", [])
    studios   = {s["id"]: s for s in cfg.get("studios", [])}

    now        = _dt.datetime.now()
    day_short  = now.strftime("%a").lower()      # "mon", "tue", …
    current_hm = now.strftime("%H:%M")           # "14:35"

    today_date = now.strftime("%Y-%m-%d")         # "2026-04-30"

    # Build map of which schedule (if any) should be active per studio right now
    active_by_studio = {}
    for sched in schedules:
        if not sched.get("enabled", True):
            continue
        studio_id = sched.get("studio_id", "")
        if not studio_id:
            continue
        sched_date = (sched.get("date") or "").strip()
        if sched_date:
            # One-off: only active on the specified date
            if sched_date != today_date:
                continue
        else:
            # Recurring: check day of week
            days = sched.get("days") or []       # [] = every day
            if days and day_short not in days:
                continue
        start = sched.get("start_time", "00:00")
        end   = sched.get("end_time",   "23:59")
        if start <= current_hm < end:
            if studio_id not in active_by_studio:
                active_by_studio[studio_id] = sched  # first matching wins

    # ── Activate schedules that just became current ────────────────────────────
    for studio_id, sched in active_by_studio.items():
        with _SCHED_LOCK:
            state = _schedule_state.get(studio_id, {})
            if state.get("active_schedule_id"):
                continue               # already under a schedule
            studio = studios.get(studio_id)
            if not studio:
                continue
            pre_station_id = studio.get("station_id", "")
            _schedule_state[studio_id] = {
                "pre_station_id":    pre_station_id,
                "active_schedule_id": sched["id"],
                "rest_override":     False,
            }
        # Apply the scheduled station and push SSE update
        cfg2 = _cfg_load()
        for st in cfg2.get("studios", []):
            if st["id"] == studio_id:
                st["station_id"] = sched["station_id"]
                break
        _cfg_save(cfg2)
        _notify_studio(studio_id)
        # CueServer — trigger LED scene for the newly scheduled station
        _cueserver_trigger(studios.get(studio_id), _get_station(cfg2, sched["station_id"]))

    # ── Deactivate schedules whose window has passed ───────────────────────────
    with _SCHED_LOCK:
        to_check = list(_schedule_state.items())

    for studio_id, state in to_check:
        if studio_id in active_by_studio:
            continue                                  # still inside the window
        if not state.get("active_schedule_id"):
            continue

        rest_override  = state.get("rest_override", False)
        pre_station_id = state.get("pre_station_id", "")

        with _SCHED_LOCK:
            _schedule_state.pop(studio_id, None)

        if not rest_override:
            # Revert studio to the brand it had before the schedule activated
            cfg2 = _cfg_load()
            for st in cfg2.get("studios", []):
                if st["id"] == studio_id:
                    st["station_id"] = pre_station_id
                    break
            _cfg_save(cfg2)
            _notify_studio(studio_id)
            # CueServer — trigger LED scene for the reverted station
            _cueserver_trigger(_get_studio(cfg2, studio_id),
                               _get_station(cfg2, pre_station_id) if pre_station_id else None)


def _schedule_loop():
    """Background thread: run schedule checks every 30 s."""
    while True:
        try:
            _time.sleep(30)
            _check_schedules()
        except Exception:
            pass

def _cueserver_trigger(studio, station):
    """Queue a CueServer LED command for the client that hosts this studio.

    Called whenever the active brand changes for a studio (manual assignment,
    schedule activate, or schedule deactivate/revert).  The command is stored
    in _cs_pending keyed by site name.  The client poller thread (started in
    register() on client nodes) picks it up within ~5 s and fires the CGI call.

    If station.cueserver_cmd is set it is fired as-is (supports both
    "Cue N Go" and "ZONE "Name" Preset N ON" formats).  If no command is set
    but the studio has cs_strips configured, a live Channel-At command is
    generated from the brand colour using station.cs_brightness (0–100).
    """
    if not studio or not station:
        return
    cs_site = (studio.get("cueserver_site") or "").strip()
    cs_host = (studio.get("cueserver_host") or "").strip()
    if not cs_site or not cs_host:
        return
    cs_cmd = (station.get("cueserver_cmd") or "").strip()
    if not cs_cmd:
        # Fall back to live DMX colour command from brand colour + brightness
        strips = studio.get("cs_strips") or []
        if strips and station.get("brand_colour"):
            bri    = int(station.get("cs_brightness") or 100)
            cs_cmd = _cs_colour_cmd(station["brand_colour"], strips, bri)
    if not cs_cmd:
        return
    with _cs_lock:
        _cs_pending[cs_site] = {"host": cs_host, "cmd": cs_cmd}

def _cs_colour_cmd(hex_colour, strips, brightness=100):
    """Build a CueScript command that sets all configured DMX strips from a hex colour.

    Each strip is a dict {name, ch_r, ch_g, ch_b, ch_w} where:
    - ch_r/g/b/w = DMX channel numbers (1–512); 0 = skip that channel
    - ch_w       = White channel; 0 = not fitted (RGB mode).
                   When ch_w > 0, RGBW decomposition is applied:
                   W = min(R,G,B); RGB reduced accordingly.

    `brightness` (0–100, default 100) is the per-brand/station brightness and
    applies uniformly to all strips.  It is stored on the station, not the strip.

    CueScript uses percentage values (0–100), so 255 → 100, 128 → 50, etc.
    """
    r, g, b = _hex_rgb(hex_colour)
    scale   = max(0.0, min(100.0, float(brightness))) / 100.0
    parts   = []
    for strip in strips:
        ch_r  = int(strip.get("ch_r") or 0)
        ch_g  = int(strip.get("ch_g") or 0)
        ch_b  = int(strip.get("ch_b") or 0)
        ch_w  = int(strip.get("ch_w") or 0)

        if ch_w:
            # RGBW: extract white component
            w_raw = min(r, g, b)
            r_c, g_c, b_c = r - w_raw, g - w_raw, b - w_raw
        else:
            r_c, g_c, b_c, w_raw = r, g, b, 0

        def _pct(v):
            return round(v / 255 * 100 * scale)

        if ch_r: parts.append(f"Channel {ch_r} At {_pct(r_c)}")
        if ch_g: parts.append(f"Channel {ch_g} At {_pct(g_c)}")
        if ch_b: parts.append(f"Channel {ch_b} At {_pct(b_c)}")
        if ch_w: parts.append(f"Channel {ch_w} At {_pct(w_raw)}")

    return " ".join(parts)

def _parse_cue_list_xml(raw):
    """Best-effort parser for CueServer's /get.cgi?req=csi XML response.

    CueServer's schema is not fully documented, so we try every common variant:
    - <cue num="N" label="…" />
    - <cue id="N" name="…" />
    - <cue number="N" title="…" />
    Returns a sorted list of {"num": str, "label": str} dicts.
    """
    cues = []
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(raw)
        for elem in root.iter():
            num = next(
                (elem.get(k, "").strip() for k in ("num", "id", "number", "cue")
                 if elem.get(k, "").strip()), None
            )
            if not num:
                continue
            try:
                float(num)         # ensure it's actually numeric
            except ValueError:
                continue
            label = next(
                (elem.get(k, "").strip() for k in ("label", "name", "title", "text")
                 if elem.get(k, "").strip()), ""
            )
            cues.append({"num": num, "label": label or f"Cue {num}"})
    except Exception:
        pass
    # Deduplicate and sort numerically
    seen: set = set()
    deduped = [c for c in cues if not (c["num"] in seen or seen.add(c["num"]))]
    return sorted(deduped, key=lambda c: float(c["num"]))

# Start mic monitor thread once at plugin load
threading.Thread(target=_mic_monitor_loop, daemon=True, name="bs-mic-monitor").start()
threading.Thread(target=_schedule_loop, daemon=True, name="bs-scheduler").start()

# ─────────────────────────────── brand palette derivation ────────────────────

def _brand_palette(hex_colour):
    """
    Derive background colours from the brand hue so the whole screen
    feels 'in' the brand colour, not just dark with coloured accents.
    Returns dict with bg_deep, bg_dark, bg_mid as hex strings and
    bg_deep_rgb as comma-separated r,g,b.
    """
    r, g, b = _hex_rgb(hex_colour)
    # Achromatic (white/grey/black) — fall back to neutral dark navy
    if max(r, g, b) - min(r, g, b) < 12:
        return {
            "bg_deep": "#070f24", "bg_dark": "#0d1f3e",
            "bg_mid":  "#122d5a", "bg_deep_rgb": "7,15,36",
        }
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    sat = min(max(s, 0.55), 0.95)

    def _hsv(val):
        rr, gg, bb = colorsys.hsv_to_rgb(h, sat, val)
        return (int(rr * 255), int(gg * 255), int(bb * 255))

    # ── Brand-colour V calculation ────────────────────────────────────────────
    # Goal: backgrounds that are CLEARLY the brand colour on a TV screen at
    # studio viewing distance, not near-black with a faint hue tint.
    #
    # Strategy: use fixed V base values (0.28 / 0.42 / 0.58) that are high
    # enough to register on any display.  Then cap V downward only for hues
    # that are naturally very bright (yellow, lime) so they don't blow out.
    # Dark hues (blue, red, purple) get the full base — they need high V to
    # produce a visible colour.
    _rh, _gh, _bh = colorsys.hsv_to_rgb(h, 1.0, 1.0)
    _hl = 0.299 * _rh + 0.587 * _gh + 0.114 * _bh  # luma weight of this hue

    # _cap: headroom for bright hues.  Yellow ≈ 0.22, green ≈ 0.30,
    # red ≈ 0.64, blue ≈ 0.86.  Dark hues are unconstrained (cap > base).
    _cap = max(0.22, 1.0 - _hl * 1.2)

    def _pv(base, ca, cb):
        """Clamp base V for bright hues; dark hues use base unchanged."""
        return min(base, _cap * ca + cb)

    dp = _hsv(_pv(0.28, 0.50, 0.10))  # deep  — clearly hue-tinted, not black
    dk = _hsv(_pv(0.42, 0.70, 0.16))  # dark  — solidly the brand colour
    md = _hsv(_pv(0.58, 1.00, 0.22))  # mid   — bold brand colour, fills the room

    def _hex3(t): return f"#{t[0]:02x}{t[1]:02x}{t[2]:02x}"
    return {
        "bg_deep":     _hex3(dp),
        "bg_dark":     _hex3(dk),
        "bg_mid":      _hex3(md),
        "bg_deep_rgb": f"{dp[0]},{dp[1]},{dp[2]}",
    }

# ─────────────────────────────────────── SSE notification ────────────────────

def _notify_studio_msg(studio_id, msg):
    """Push an arbitrary SSE data string to every listener on studio_id."""
    with _NLOCK:
        qs = list(_NOTIFY.get(studio_id, []))
    for q in qs:
        try:
            q.put_nowait(msg)
        except _queue.Full:
            pass

def _notify_studio(studio_id):
    _notify_studio_msg(studio_id, "assignment_changed")

def _notify_station_studios(cfg, station_id, msg="settings_changed"):
    """Push msg to every studio SSE stream that is currently showing station_id."""
    for studio in cfg.get("studios", []):
        if studio.get("station_id") == station_id:
            _notify_studio_msg(studio.get("id", ""), msg)

def _sse_stream(studio_id):
    q = _queue.Queue(maxsize=8)
    with _NLOCK:
        _NOTIFY.setdefault(studio_id, []).append(q)
    try:
        yield "data: connected\n\n"
        while True:
            try:
                msg = q.get(timeout=25)
                yield f"data: {msg}\n\n"
            except _queue.Empty:
                yield ": keepalive\n\n"
    finally:
        with _NLOCK:
            try:
                _NOTIFY[studio_id].remove(q)
            except (ValueError, KeyError):
                pass

# ─────────────────────────────────────── API key auth ────────────────────────

def _make_api_auth():
    def _api_auth(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            if session.get("logged_in"):
                return f(*args, **kwargs)
            auth  = request.headers.get("Authorization", "")
            token = auth.removeprefix("Bearer ").strip() if auth else ""
            cfg   = _cfg_load()
            key   = cfg.get("api_key", "")
            if key and token == key:
                return f(*args, **kwargs)
            return jsonify({"error": "Unauthorized"}), 401
        return wrapper
    return _api_auth

# ─────────────────────────────────────── now-playing resolver ─────────────────

def _resolve_np(station, monitor):
    src = station.get("np_source", "none")
    try:
        if src == "zetta":
            key  = station.get("np_zetta_key", "")
            data = getattr(monitor, "_zetta_live_station_data", lambda: {})()
            np   = (data.get(key) or {}).get("now_playing") or {}
            if np:
                return {
                    "title":   (np.get("raw_title") or np.get("title") or "").strip(),
                    "artist":  (np.get("raw_artist") or "").strip(),
                    "is_spot": int(np.get("asset_type") or 0) == 2,
                }
            return {"title": "", "artist": "", "is_spot": False}
        elif src == "json_api":
            import urllib.request
            url = station.get("np_api_url", "")
            if url:
                r = urllib.request.urlopen(url, timeout=5)
                d = json.loads(r.read())
                def _dig(obj, path):
                    for k in (path or "").split("."):
                        obj = (obj or {}).get(k, "") if isinstance(obj, dict) else ""
                    return str(obj or "")
                return {
                    "title":   _dig(d, station.get("np_api_title_path", "")),
                    "artist":  _dig(d, station.get("np_api_artist_path", "")),
                    "is_spot": False,
                }
        elif src == "manual":
            return {"title": station.get("np_manual", ""), "artist": "", "is_spot": False}
    except Exception:
        pass
    return None

# ─────────────────────────────────────────────── logo helpers ─────────────────

def _logo_file(sid):
    for ext in ("png", "svg", "jpg", "jpeg", "webp", "gif"):
        p = os.path.join(_LOGO_DIR, f"{sid}.{ext}")
        if os.path.exists(p):
            return p, ext
    return None, None

def _hex_rgb(h):
    h = (h or "#000000").lstrip("#")
    return int(h[:2], 16), int(h[2:4], 16), int(h[4:], 16)

# ──────────────────────────────────────────── producer template ───────────────
# Shown to any non-admin (plugin-role) user. Stripped-down: no brand config,
# no API tab, no studio creation. Just "what's on each screen" + switch + schedule.

_PRODUCER_BS_TPL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<title>Brand Screen Control</title>
<style nonce="{{csp_nonce()}}">
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(circle at top,#12376f 0%,var(--bg) 38%,#05101f 100%);color:var(--tx);font-family:system-ui,sans-serif;font-size:14px;min-height:100vh}
.hdr{background:linear-gradient(180deg,rgba(10,31,65,.97),rgba(9,24,48,.97));border-bottom:1px solid var(--bor);padding:14px 24px;display:flex;align-items:center;gap:14px;position:sticky;top:0;z-index:50;backdrop-filter:blur(8px)}
.hdr-logo{font-size:22px}.hdr-title{font-size:17px;font-weight:700;letter-spacing:-.02em}.hdr-sub{font-size:11px;color:var(--mu);margin-top:1px}
.hdr-right{margin-left:auto;display:flex;align-items:center;gap:10px}
.hdr-link{font-size:12px;color:var(--mu);background:rgba(23,52,95,.4);padding:5px 12px;border-radius:20px;border:1px solid var(--bor);text-decoration:none;transition:color .2s}.hdr-link:hover{color:var(--tx)}
.btn{border:none;border-radius:8px;padding:8px 16px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;display:inline-block;text-decoration:none}.btn:hover{filter:brightness(1.15)}
.bp{background:var(--acc);color:#fff}.bd{background:var(--al);color:#fff}.bg{background:#132040;color:var(--tx)}.bs{font-size:11px;padding:3px 9px}
main{max-width:1100px;margin:0 auto;padding:24px 20px}
.sec-title{font-size:11px;font-weight:700;color:var(--mu);text-transform:uppercase;letter-spacing:.1em;margin-bottom:14px;display:flex;align-items:center;gap:8px}
.sec-title::after{content:'';flex:1;height:1px;background:var(--bor)}
/* ── Studio cards ── */
.studio-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px;margin-bottom:32px}
.studio-card{background:var(--sur);border:1px solid var(--bor);border-radius:14px;overflow:hidden;transition:border-color .2s}
.studio-card.has-sched{border-color:rgba(23,168,255,.45)}
.sc-top{padding:16px 18px 14px;display:flex;align-items:center;gap:14px}
.sc-logo-box{width:72px;height:44px;border-radius:8px;background:#071428;border:1px solid var(--bor);display:flex;align-items:center;justify-content:center;overflow:hidden;flex-shrink:0}
.sc-logo-box img{max-width:100%;max-height:100%;object-fit:contain}
.sc-logo-placeholder{font-size:24px;opacity:.3}
.sc-info{flex:1;min-width:0}
.sc-studio-name{font-size:11px;font-weight:600;color:var(--mu);text-transform:uppercase;letter-spacing:.07em;margin-bottom:3px}
.sc-brand-name{font-size:16px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sc-unassigned{font-size:14px;color:var(--mu);font-style:italic}
.sc-sched-badge{display:inline-flex;align-items:center;gap:5px;font-size:10px;font-weight:700;background:rgba(23,168,255,.12);color:var(--acc);border:1px solid rgba(23,168,255,.3);border-radius:999px;padding:2px 8px;margin-top:4px}
.sc-switch{padding:12px 18px 16px;border-top:1px solid var(--bor);background:rgba(0,0,0,.12);display:flex;gap:8px;align-items:center}
.sc-switch select{flex:1;background:#0d1e40;border:1px solid var(--bor);border-radius:8px;color:var(--tx);padding:7px 10px;font-size:13px;font-family:inherit}.sc-switch select:focus{border-color:var(--acc);outline:none}
.btn-switch{background:var(--acc);color:#fff;border:none;border-radius:8px;padding:7px 18px;font-size:13px;font-weight:700;cursor:pointer;white-space:nowrap;font-family:inherit}.btn-switch:hover{filter:brightness(1.1)}.btn-switch:disabled{opacity:.45;cursor:default;filter:none}
/* ── Brands panel ── */
.brand-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px;margin-bottom:12px}
.brand-card{background:var(--sur);border:1px solid var(--bor);border-radius:10px;padding:10px;display:flex;flex-direction:column;align-items:center;gap:6px}
.brand-logo-box{width:80px;height:48px;border-radius:6px;background:#071428;border:1px solid var(--bor);display:flex;align-items:center;justify-content:center;overflow:hidden}
.brand-logo-box img{max-width:100%;max-height:100%;object-fit:contain}
.brand-name{font-size:12px;font-weight:700;text-align:center;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
/* ── Upload area ── */
.upload-area{border:2px dashed var(--bor);border-radius:10px;padding:20px;text-align:center;cursor:pointer;transition:border-color .2s,background .2s;color:var(--mu)}
.upload-area:hover,.upload-area.drag{border-color:var(--acc);background:rgba(23,168,255,.05);color:var(--tx)}
.upload-area.has-file{border-color:var(--ok);background:rgba(34,197,94,.05)}
.upload-icon{font-size:28px;display:block;margin-bottom:6px}
.upload-label{font-size:12px;font-weight:600}
.upload-hint{font-size:11px;color:var(--mu);margin-top:3px}
/* ── Collapsible cards ── */
.coll-card{background:var(--sur);border:1px dashed var(--bor);border-radius:12px;overflow:hidden;margin-bottom:10px}
.coll-toggle{padding:12px 16px;display:flex;align-items:center;gap:10px;cursor:pointer;font-size:13px;font-weight:600;color:var(--acc);user-select:none}
.coll-toggle:hover{background:rgba(23,168,255,.04)}
.coll-body{padding:16px;border-top:1px solid var(--bor);display:none}
/* ── Form bits ── */
.field{display:flex;flex-direction:column;gap:5px;margin-bottom:14px}
.field label{font-size:11px;color:var(--mu);font-weight:600;text-transform:uppercase;letter-spacing:.05em}
.field input[type=text],.field input[type=url],.field select{background:#0d1e40;border:1px solid var(--bor);border-radius:8px;color:var(--tx);padding:8px 11px;font-size:13px;font-family:inherit}
.field input:focus,.field select:focus{border-color:var(--acc);outline:none}
input[type=time],input[type=date]{background:#0d1e40;border:1px solid var(--bor);border-radius:8px;color:var(--tx);padding:8px 11px;font-size:13px;font-family:inherit}
input[type=time]:focus,input[type=date]:focus{border-color:var(--acc);outline:none}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
/* ── Schedule list ── */
.sched-row{background:var(--sur);border:1px solid var(--bor);border-radius:10px;padding:12px 16px;display:flex;align-items:center;gap:12px;margin-bottom:8px}
.sched-row.active-sched{border-color:var(--acc)}
.sched-info{flex:1;min-width:0}
.sched-name{font-weight:700;font-size:13px;margin-bottom:3px}
.sched-detail{font-size:12px;color:var(--mu)}
.sched-badge{font-size:10px;font-weight:700;background:rgba(23,168,255,.18);color:var(--acc);border:1px solid rgba(23,168,255,.35);border-radius:999px;padding:2px 8px;margin-left:6px}
.sched-badge.oneoff{background:rgba(245,158,11,.15);color:var(--wn);border-color:rgba(245,158,11,.35)}
.sched-badge.past{background:rgba(239,68,68,.12);color:var(--al);border-color:rgba(239,68,68,.3)}
.tog{position:relative;display:inline-block;width:36px;height:20px;flex-shrink:0}
.tog input{opacity:0;width:0;height:0}
.tog-sl{position:absolute;cursor:pointer;inset:0;background:#1a3060;border-radius:20px;transition:.2s}
.tog-sl:before{content:"";position:absolute;height:14px;width:14px;left:3px;bottom:3px;background:#8aa4c8;border-radius:50%;transition:.2s}
.tog input:checked+.tog-sl{background:var(--acc)}
.tog input:checked+.tog-sl:before{transform:translateX(16px);background:#fff}
/* ── Day / type buttons ── */
.day-btns{display:flex;gap:4px;flex-wrap:wrap;margin-top:6px}
.day-btn{padding:4px 10px;border:1px solid var(--bor);border-radius:6px;background:#0d1e40;color:var(--mu);font-size:12px;font-weight:600;cursor:pointer}
.day-btn.sel{background:rgba(23,168,255,.2);color:var(--acc);border-color:var(--acc)}
.stype-btns{display:flex;gap:0;margin-top:6px}
.stype-btn{padding:5px 14px;border:1px solid var(--bor);background:#0d1e40;color:var(--mu);font-size:12px;font-weight:600;cursor:pointer}
.stype-btn:first-child{border-radius:6px 0 0 6px}.stype-btn:last-child{border-radius:0 6px 6px 0;border-left:none}
.stype-btn.sel{background:rgba(23,168,255,.2);color:var(--acc);border-color:var(--acc)}
/* ── One-off brand source toggle ── */
.bsrc-btns{display:flex;gap:0;margin-bottom:12px}
.bsrc-btn{padding:5px 14px;border:1px solid var(--bor);background:#0d1e40;color:var(--mu);font-size:12px;font-weight:600;cursor:pointer}
.bsrc-btn:first-child{border-radius:6px 0 0 6px}.bsrc-btn:last-child{border-radius:0 6px 6px 0;border-left:none}
.bsrc-btn.sel{background:rgba(23,168,255,.2);color:var(--acc);border-color:var(--acc)}
/* ── Misc ── */
.msg-box{border-radius:8px;padding:10px 14px;margin-bottom:18px;font-size:12px;display:none}
.msg-ok{background:#0f2318;color:var(--ok);border:1px solid #166534}
.msg-err{background:#2a0a0a;color:var(--al);border:1px solid #991b1b}
.empty-state{text-align:center;padding:32px 24px;color:var(--mu);font-size:13px}
@media(max-width:600px){main{padding:16px 12px}.sc-switch{flex-wrap:wrap}.grid2{grid-template-columns:1fr}}
</style>
</head>
<body>
<header class="hdr">
  <span class="hdr-logo">📺</span>
  <div>
    <div class="hdr-title">Brand Screen Control</div>
    <div class="hdr-sub">Switch brands &amp; manage schedules</div>
  </div>
  <div class="hdr-right">
    {% if has_presenter %}<a href="/producer" class="hdr-link">🎙 Producer View</a>{% endif %}
    {% if has_listener %}<a href="/listener" class="hdr-link">🎧 Listen</a>{% endif %}
  </div>
</header>
<main>
  <div id="msg" class="msg-box"></div>

  <!-- ── Studios: what's on now ─────────────────────────────────────────── -->
  <div class="sec-title">📺 What's on each studio right now</div>
  <div class="studio-grid" id="studio-grid"></div>

  <!-- ── Brands ─────────────────────────────────────────────────────────── -->
  <div class="sec-title">🎨 Brands</div>
  <div class="brand-grid" id="brand-grid"></div>
  <div class="coll-card" id="add-brand-wrap" style="margin-bottom:32px">
    <div class="coll-toggle" id="add-brand-toggle">
      <span>＋ Upload a new brand</span>
      <span style="margin-left:auto;font-size:11px;color:var(--mu)" id="add-brand-arrow">▼</span>
    </div>
    <div class="coll-body" id="add-brand-body">
      <div class="field">
        <label>Brand name</label>
        <input type="text" id="ab-name" placeholder="e.g. Heart Extra, Christmas Special…">
      </div>
      <div class="field" style="margin-bottom:16px">
        <label>Logo (PNG or SVG recommended)</label>
        <div class="upload-area" id="ab-drop">
          <span class="upload-icon">🖼</span>
          <span class="upload-label" id="ab-drop-label">Click or drag a logo here</span>
          <span class="upload-hint">PNG, SVG, JPG or WebP — transparent background works best</span>
        </div>
      </div>
      <button class="btn bp" id="ab-save-btn" style="width:100%;padding:10px">Upload brand</button>
      <input type="file" id="ab-file-input" accept="image/png,image/svg+xml,image/jpeg,image/webp,image/gif" style="display:none">
    </div>
  </div>

  <!-- ── Scheduled changes ──────────────────────────────────────────────── -->
  <div class="sec-title">⏰ Scheduled changes</div>
  <div class="coll-card" id="add-sched-wrap">
    <div class="coll-toggle" id="add-sched-toggle">
      <span>＋ Add a scheduled brand change</span>
      <span style="margin-left:auto;font-size:11px;color:var(--mu)" id="add-sched-arrow">▼</span>
    </div>
    <div class="coll-body" id="add-sched-body">
      <div class="field">
        <label>Label (so you remember what this is for)</label>
        <input type="text" id="sf-name" placeholder="e.g. Friday Night Show, Christmas Branding…">
      </div>
      <div class="field" style="margin-bottom:10px">
        <label>Studio</label>
        <select id="sf-studio"></select>
      </div>
      <div class="field" style="margin-bottom:10px">
        <label>When should this run?</label>
        <div class="stype-btns" id="sf-type-btns">
          <button type="button" class="stype-btn sel" data-type="recurring">🔁 Regular (weekly)</button>
          <button type="button" class="stype-btn" data-type="oneoff">📅 One-off date</button>
        </div>
      </div>
      <!-- Recurring: day picker -->
      <div id="sf-recurring-section">
        <div class="field">
          <label>Days (leave none selected = every day)</label>
          <div class="day-btns" id="sf-days">
            <button type="button" class="day-btn" data-day="mon">Mon</button>
            <button type="button" class="day-btn" data-day="tue">Tue</button>
            <button type="button" class="day-btn" data-day="wed">Wed</button>
            <button type="button" class="day-btn" data-day="thu">Thu</button>
            <button type="button" class="day-btn" data-day="fri">Fri</button>
            <button type="button" class="day-btn" data-day="sat">Sat</button>
            <button type="button" class="day-btn" data-day="sun">Sun</button>
          </div>
        </div>
        <div class="field">
          <label>Switch to brand</label>
          <select id="sf-station-rec"></select>
        </div>
      </div>
      <!-- One-off: date + brand source choice -->
      <div id="sf-oneoff-section" style="display:none">
        <div class="field">
          <label>Date</label>
          <input type="date" id="sf-date" style="max-width:220px">
        </div>
        <div class="field" style="margin-bottom:6px">
          <label>Brand to show</label>
          <div class="bsrc-btns" id="sf-bsrc-btns">
            <button type="button" class="bsrc-btn sel" data-src="existing">Use an existing brand</button>
            <button type="button" class="bsrc-btn" data-src="upload">Upload a logo for this event</button>
          </div>
        </div>
        <div id="sf-brand-existing">
          <div class="field">
            <select id="sf-station-oof"></select>
          </div>
        </div>
        <div id="sf-brand-upload" style="display:none">
          <div class="field">
            <label>Brand / event name</label>
            <input type="text" id="sf-oof-name" placeholder="e.g. Christmas Special 2026">
          </div>
          <div class="field" style="margin-bottom:16px">
            <label>Logo file</label>
            <div class="upload-area" id="sf-oof-drop">
              <span class="upload-icon">🖼</span>
              <span class="upload-label" id="sf-oof-drop-label">Click or drag a logo here</span>
              <span class="upload-hint">PNG, SVG, JPG or WebP</span>
            </div>
          </div>
          <input type="file" id="sf-oof-file" accept="image/png,image/svg+xml,image/jpeg,image/webp,image/gif" style="display:none">
        </div>
      </div>
      <div class="grid2" style="margin-bottom:16px">
        <div class="field" style="margin-bottom:0">
          <label>Starts at</label>
          <input type="time" id="sf-start" value="09:00">
        </div>
        <div class="field" style="margin-bottom:0">
          <label>Ends at</label>
          <input type="time" id="sf-end" value="17:00">
        </div>
      </div>
      <button class="btn bp" id="sf-save-btn" style="width:100%;padding:10px">Save scheduled change</button>
    </div>
  </div>
  <div id="sched-list" style="margin-bottom:32px"></div>
</main>

<script nonce="{{csp_nonce()}}">
var _studios   = {{studios_json|safe}};
var _stations  = {{stations_json|safe}};
var _schedules = {{schedules_json|safe}};
var _schedActive = {};

function _csrf(){return(document.querySelector('meta[name="csrf-token"]')||{}).content||'';}
function _post(u,d){return fetch(u,{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':_csrf()},credentials:'same-origin',body:JSON.stringify(d)});}
function _patch(u,d){return fetch(u,{method:'PATCH',headers:{'Content-Type':'application/json','X-CSRFToken':_csrf()},credentials:'same-origin',body:JSON.stringify(d)});}
function _put(u,d){return fetch(u,{method:'PUT',headers:{'Content-Type':'application/json','X-CSRFToken':_csrf()},credentials:'same-origin',body:JSON.stringify(d)});}
function _del(u){return fetch(u,{method:'DELETE',headers:{'X-CSRFToken':_csrf()},credentials:'same-origin'});}
function _esc(s){var d=document.createElement('div');d.appendChild(document.createTextNode(s||''));return d.innerHTML;}
function _msg(t,ok){
  var el=document.getElementById('msg');
  el.className='msg-box '+(ok?'msg-ok':'msg-err');
  el.textContent=t;el.style.display='block';
  clearTimeout(el._t);el._t=setTimeout(function(){el.style.display='none';},5000);
}
function _uploadFile(url,file){
  var fd=new FormData();fd.append('logo',file);
  return fetch(url,{method:'POST',headers:{'X-CSRFToken':_csrf()},credentials:'same-origin',body:fd});
}

// ── Studio grid ──────────────────────────────────────────────────────────────
function renderStudios(){
  var el=document.getElementById('studio-grid');
  if(!_studios.length){
    el.innerHTML='<div class="empty-state" style="grid-column:1/-1">No studios configured yet. Ask your administrator to set them up.</div>';
    return;
  }
  el.innerHTML=_studios.map(function(sd){
    var st=_stations.find(function(s){return s.id===sd.station_id;})||null;
    var sa=_schedActive[sd.id];
    var hasSched=!!(sa&&sa.active_schedule_id);
    var schedBrand=null;
    if(hasSched){var sc=_schedules.find(function(s){return s.id===sa.active_schedule_id;});schedBrand=sc?_stations.find(function(s){return s.id===sc.station_id;}):null;}
    var logoHtml=(st&&st._has_logo)?'<img src="/api/brandscreen/logo/'+st.id+'?t='+Date.now()+'" alt="">':'';
    var logoBox='<div class="sc-logo-box">'+logoHtml+'<span class="sc-logo-placeholder" style="display:'+((st&&st._has_logo)?'none':'flex')+'">📺</span></div>';
    var brandCol=st?(st.brand_colour||'#17a8ff'):'#17a8ff';
    var brandName=st?('<span style="color:'+_esc(brandCol)+'">'+_esc(st.name)+'</span>'):'<span class="sc-unassigned">Nothing assigned</span>';
    var schedBadge=hasSched?'<div class="sc-sched-badge">📅 Scheduled'+(schedBrand?' — '+_esc(schedBrand.name):'')+'</div>':'';
    var opts=_stations.map(function(s){return '<option value="'+s.id+'"'+(sd.station_id===s.id?' selected':'')+'>'+_esc(s.name)+'</option>';}).join('');
    return '<div class="studio-card'+(hasSched?' has-sched':'')+'" id="scard-'+sd.id+'">'
      +'<div class="sc-top">'+logoBox+'<div class="sc-info"><div class="sc-studio-name">'+_esc(sd.name)+'</div><div class="sc-brand-name">'+brandName+'</div>'+schedBadge+'</div></div>'
      +'<div class="sc-switch"><select id="sw-'+sd.id+'"><option value="">— choose a brand —</option>'+opts+'</select>'
      +'<button class="btn-switch" data-sdid="'+sd.id+'">Switch now</button></div></div>';
  }).join('');
  el.querySelectorAll('.btn-switch').forEach(function(btn){
    btn.addEventListener('click',function(){
      var sdid=this.dataset.sdid,sel=document.getElementById('sw-'+sdid),sid=sel?sel.value:'';
      if(!sid){_msg('Choose a brand first.',false);return;}
      this.disabled=true;var me=this;
      _put('/api/brandscreen/studio/'+sdid+'/station',{station_id:sid})
        .then(function(r){return r.json();}).then(function(d){
          me.disabled=false;
          if(!d.ok){_msg(d.error||'Failed to switch.',false);return;}
          var sd=_studios.find(function(s){return s.id===sdid;});if(sd) sd.station_id=sid;
          renderStudios();_msg('Switched ✓',true);
        }).catch(function(){me.disabled=false;_msg('Connection error.',false);});
    });
  });
}

// ── Brand grid ───────────────────────────────────────────────────────────────
function renderBrands(){
  var el=document.getElementById('brand-grid');
  if(!_stations.length){el.innerHTML='<div style="color:var(--mu);font-size:13px;margin-bottom:8px">No brands yet — upload one below.</div>';return;}
  el.innerHTML=_stations.map(function(s){
    var logo=(s._has_logo)?'<img src="/api/brandscreen/logo/'+s.id+'?t='+Date.now()+'" alt="">':'<span style="font-size:22px;opacity:.25">📺</span>';
    return '<div class="brand-card"><div class="brand-logo-box">'+logo+'</div>'
      +'<div class="brand-name" style="color:'+_esc(s.brand_colour||'#17a8ff')+'">'+_esc(s.name)+'</div></div>';
  }).join('');
}

// ── Schedule list ────────────────────────────────────────────────────────────
function _schedDayLabel(sc){
  if(sc.date){
    var d=new Date(sc.date+'T00:00:00');
    var dnames=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
    var mnames=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    return dnames[d.getDay()]+' '+d.getDate()+' '+mnames[d.getMonth()]+' '+d.getFullYear();
  }
  var ds=sc.days||[];if(!ds.length) return 'Every day';
  var n={mon:'Mon',tue:'Tue',wed:'Wed',thu:'Thu',fri:'Fri',sat:'Sat',sun:'Sun'};
  return ds.map(function(d){return n[d]||d;}).join(', ');
}
function _schedIsPast(sc){
  if(!sc.date) return false;
  var t=new Date();t.setHours(0,0,0,0);var d=new Date(sc.date+'T00:00:00');d.setHours(0,0,0,0);return d<t;
}
function renderSchedules(){
  var el=document.getElementById('sched-list');
  if(!_schedules.length){el.innerHTML='<div style="color:var(--mu);font-size:13px;padding:8px 0">No scheduled changes yet.</div>';return;}
  var sdMap={};_studios.forEach(function(s){sdMap[s.id]=s.name||s.id;});
  var stMap={};_stations.forEach(function(s){stMap[s.id]=s.name||s.id;});
  var html='';
  _schedules.forEach(function(sc){
    var isActive=!!_schedActive[sc.studio_id]&&_schedActive[sc.studio_id].active_schedule_id===sc.id;
    var isOneOff=!!(sc.date),isPast=_schedIsPast(sc);
    var badges='';
    if(isActive) badges+='<span class="sched-badge">ACTIVE NOW</span>';
    if(isOneOff&&isPast) badges+='<span class="sched-badge past">PAST</span>';
    else if(isOneOff) badges+='<span class="sched-badge oneoff">ONE-OFF</span>';
    html+='<div class="sched-row'+(isActive?' active-sched':'')+'">'
      +'<div class="sched-info"><div class="sched-name">'+_esc(sc.name||'Unnamed')+badges+'</div>'
      +'<div class="sched-detail">Studio: <strong>'+_esc(sdMap[sc.studio_id]||sc.studio_id)+'</strong> → Brand: <strong>'+_esc(stMap[sc.station_id]||sc.station_id)+'</strong></div>'
      +'<div class="sched-detail">'+_schedDayLabel(sc)+' &nbsp;·&nbsp; '+_esc(sc.start_time||'?')+' – '+_esc(sc.end_time||'?')+'</div></div>'
      +'<label class="tog"><input type="checkbox" class="sched-tog"'+(sc.enabled?' checked':'')+' data-sid="'+sc.id+'"><span class="tog-sl"></span></label>'
      +'<button class="btn bd bs sched-del-btn" data-sid="'+sc.id+'">Delete</button></div>';
  });
  el.innerHTML=html;
  el.querySelectorAll('.sched-tog').forEach(function(cb){
    cb.addEventListener('change',function(){
      var sid=this.dataset.sid;
      _patch('/api/brandscreen/schedule/'+sid,{enabled:this.checked}).then(function(r){return r.json();}).then(function(d){
        if(!d.ok){_msg('Could not update.',false);return;}
        var sc=_schedules.find(function(s){return s.id===sid;});if(sc) sc.enabled=cb.checked;
      });
    });
  });
  el.querySelectorAll('.sched-del-btn').forEach(function(btn){
    btn.addEventListener('click',function(){
      var sid=this.dataset.sid;
      if(!confirm('Delete this scheduled change?')) return;
      _del('/api/brandscreen/schedule/'+sid).then(function(r){return r.json();}).then(function(d){
        if(!d.ok){_msg('Delete failed.',false);return;}
        _schedules=_schedules.filter(function(s){return s.id!==sid;});renderSchedules();
      });
    });
  });
}

// ── Refresh active state ──────────────────────────────────────────────────────
function _loadActive(){
  fetch('/api/brandscreen/schedules',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){_schedActive=d.active||{};renderStudios();renderSchedules();})
    .catch(function(){});
}
function _populateSchedSelects(){
  ['sf-station-rec','sf-station-oof'].forEach(function(id){
    var el=document.getElementById(id);if(!el) return;
    el.innerHTML='<option value="">— choose a brand —</option>';
    _stations.forEach(function(s){el.innerHTML+='<option value="'+s.id+'">'+_esc(s.name||s.id)+'</option>';});
  });
  var ss=document.getElementById('sf-studio');if(!ss) return;
  ss.innerHTML='<option value="">— choose a studio —</option>';
  _studios.forEach(function(s){ss.innerHTML+='<option value="'+s.id+'">'+_esc(s.name||s.id)+'</option>';});
}

renderStudios();renderBrands();renderSchedules();
_loadActive();setInterval(_loadActive,30000);

// ── Add brand form ────────────────────────────────────────────────────────────
(function(){
  var tog=document.getElementById('add-brand-toggle');
  var body=document.getElementById('add-brand-body');
  var arrow=document.getElementById('add-brand-arrow');
  tog.addEventListener('click',function(){var o=body.style.display!=='none';body.style.display=o?'none':'block';arrow.textContent=o?'▼':'▲';});

  var fileInp=document.getElementById('ab-file-input');
  var drop=document.getElementById('ab-drop');
  var dropLabel=document.getElementById('ab-drop-label');
  var abFile=null;
  function _setAbFile(f){
    abFile=f;
    if(f){drop.classList.add('has-file');dropLabel.textContent='✓ '+f.name;}
    else{drop.classList.remove('has-file');dropLabel.textContent='Click or drag a logo here';}
  }
  drop.addEventListener('click',function(){fileInp.click();});
  fileInp.addEventListener('change',function(){_setAbFile(this.files[0]||null);});
  drop.addEventListener('dragover',function(e){e.preventDefault();drop.classList.add('drag');});
  drop.addEventListener('dragleave',function(){drop.classList.remove('drag');});
  drop.addEventListener('drop',function(e){e.preventDefault();drop.classList.remove('drag');_setAbFile((e.dataTransfer.files||[])[0]||null);});

  document.getElementById('ab-save-btn').addEventListener('click',function(){
    var name=document.getElementById('ab-name').value.trim();
    if(!name){_msg('Enter a brand name.',false);return;}
    if(!abFile){_msg('Choose a logo file.',false);return;}
    var me=this;me.disabled=true;me.textContent='Uploading…';
    _post('/api/brandscreen/station',{}).then(function(r){return r.json();}).then(function(d){
      if(!d.ok){me.disabled=false;me.textContent='Upload brand';_msg(d.error||'Could not create brand.',false);return;}
      var sid=d.station.id;
      return _post('/api/brandscreen/station/'+sid,{name:name,full_screen_logo:true,logo_anim:'float',bg_style:'particles',show_now_playing:false})
        .then(function(){return _uploadFile('/api/brandscreen/logo/'+sid,abFile);})
        .then(function(){
          d.station.name=name;d.station._has_logo=true;d.station.full_screen_logo=true;
          _stations.push(d.station);
          renderBrands();_populateSchedSelects();
          document.getElementById('ab-name').value='';_setAbFile(null);
          body.style.display='none';arrow.textContent='▼';
          me.disabled=false;me.textContent='Upload brand';
          _msg('Brand "'+name+'" uploaded ✓',true);
        });
    }).catch(function(){me.disabled=false;me.textContent='Upload brand';_msg('Upload failed — check your connection.',false);});
  });
})();

// ── Add schedule form ─────────────────────────────────────────────────────────
(function(){
  var tog=document.getElementById('add-sched-toggle');
  var body=document.getElementById('add-sched-body');
  var arrow=document.getElementById('add-sched-arrow');
  tog.addEventListener('click',function(){var o=body.style.display!=='none';body.style.display=o?'none':'block';arrow.textContent=o?'▼':'▲';});

  _populateSchedSelects();

  var _t=new Date();
  document.getElementById('sf-date').value=_t.getFullYear()+'-'+String(_t.getMonth()+1).padStart(2,'0')+'-'+String(_t.getDate()).padStart(2,'0');

  // Recurring / one-off toggle
  var typeBtns=document.getElementById('sf-type-btns');
  var recSec=document.getElementById('sf-recurring-section');
  var oofSec=document.getElementById('sf-oneoff-section');
  typeBtns.addEventListener('click',function(e){
    var btn=e.target.closest('.stype-btn');if(!btn) return;
    typeBtns.querySelectorAll('.stype-btn').forEach(function(b){b.classList.remove('sel');});btn.classList.add('sel');
    var oof=btn.dataset.type==='oneoff';recSec.style.display=oof?'none':'block';oofSec.style.display=oof?'block':'none';
  });

  // Day buttons
  document.getElementById('sf-days').addEventListener('click',function(e){var b=e.target.closest('.day-btn');if(b) b.classList.toggle('sel');});

  // One-off: existing brand vs upload toggle
  var bsrcBtns=document.getElementById('sf-bsrc-btns');
  var brandExisting=document.getElementById('sf-brand-existing');
  var brandUpload=document.getElementById('sf-brand-upload');
  bsrcBtns.addEventListener('click',function(e){
    var btn=e.target.closest('.bsrc-btn');if(!btn) return;
    bsrcBtns.querySelectorAll('.bsrc-btn').forEach(function(b){b.classList.remove('sel');});btn.classList.add('sel');
    var isUp=btn.dataset.src==='upload';brandExisting.style.display=isUp?'none':'block';brandUpload.style.display=isUp?'block':'none';
  });

  // One-off upload area
  var oofFileInp=document.getElementById('sf-oof-file');
  var oofDrop=document.getElementById('sf-oof-drop');
  var oofDropLabel=document.getElementById('sf-oof-drop-label');
  var oofFile=null;
  function _setOofFile(f){oofFile=f;if(f){oofDrop.classList.add('has-file');oofDropLabel.textContent='✓ '+f.name;}else{oofDrop.classList.remove('has-file');oofDropLabel.textContent='Click or drag a logo here';}}
  oofDrop.addEventListener('click',function(){oofFileInp.click();});
  oofFileInp.addEventListener('change',function(){_setOofFile(this.files[0]||null);});
  oofDrop.addEventListener('dragover',function(e){e.preventDefault();oofDrop.classList.add('drag');});
  oofDrop.addEventListener('dragleave',function(){oofDrop.classList.remove('drag');});
  oofDrop.addEventListener('drop',function(e){e.preventDefault();oofDrop.classList.remove('drag');_setOofFile((e.dataTransfer.files||[])[0]||null);});

  function _resetForm(){
    document.getElementById('sf-name').value='';
    document.getElementById('sf-days').querySelectorAll('.day-btn.sel').forEach(function(b){b.classList.remove('sel');});
    document.getElementById('sf-start').value='09:00';
    document.getElementById('sf-end').value='17:00';
    typeBtns.querySelectorAll('.stype-btn').forEach(function(b){b.classList.remove('sel');});
    typeBtns.querySelector('.stype-btn').classList.add('sel');
    recSec.style.display='block';oofSec.style.display='none';
    bsrcBtns.querySelectorAll('.bsrc-btn').forEach(function(b){b.classList.remove('sel');});
    bsrcBtns.querySelector('.bsrc-btn').classList.add('sel');
    brandExisting.style.display='block';brandUpload.style.display='none';
    document.getElementById('sf-oof-name').value='';_setOofFile(null);
    body.style.display='none';arrow.textContent='▼';
  }

  document.getElementById('sf-save-btn').addEventListener('click',function(){
    var name=document.getElementById('sf-name').value.trim();
    var studio=document.getElementById('sf-studio').value;
    var start=document.getElementById('sf-start').value;
    var end=document.getElementById('sf-end').value;
    var selType=document.querySelector('#sf-type-btns .stype-btn.sel');
    var isOneOff=selType&&selType.dataset.type==='oneoff';
    var days=isOneOff?[]:[].slice.call(document.querySelectorAll('#sf-days .day-btn.sel')).map(function(b){return b.dataset.day;});
    var date=isOneOff?(document.getElementById('sf-date').value||''):'';
    var selBsrc=document.querySelector('#sf-bsrc-btns .bsrc-btn.sel');
    var isUpload=isOneOff&&selBsrc&&selBsrc.dataset.src==='upload';
    if(!name){_msg('Give this scheduled change a label.',false);return;}
    if(!studio){_msg('Choose a studio.',false);return;}
    if(isOneOff&&!date){_msg('Pick a date.',false);return;}
    if(!start||!end){_msg('Set start and end times.',false);return;}
    if(start>=end){_msg('End time must be after start time.',false);return;}
    var me=this;me.disabled=true;me.textContent='Saving…';

    function _doSave(stationId){
      _post('/api/brandscreen/schedule',{name:name,studio_id:studio,station_id:stationId,days:days,date:date,start_time:start,end_time:end})
        .then(function(r){return r.json();}).then(function(d){
          me.disabled=false;me.textContent='Save scheduled change';
          if(!d.ok){_msg(d.error||'Could not save.',false);return;}
          _schedules.push(d.schedule);renderSchedules();_resetForm();
          _msg('Scheduled change saved ✓',true);
        }).catch(function(){me.disabled=false;me.textContent='Save scheduled change';_msg('Connection error.',false);});
    }

    if(isUpload){
      var oofName=document.getElementById('sf-oof-name').value.trim()||name;
      if(!oofFile){_msg('Choose a logo file for this event.',false);me.disabled=false;me.textContent='Save scheduled change';return;}
      me.textContent='Uploading logo…';
      _post('/api/brandscreen/station',{}).then(function(r){return r.json();}).then(function(d){
        if(!d.ok){me.disabled=false;me.textContent='Save scheduled change';_msg('Could not create brand.',false);return;}
        var sid=d.station.id;
        return _post('/api/brandscreen/station/'+sid,{name:oofName,full_screen_logo:true,logo_anim:'float',bg_style:'particles',show_now_playing:false})
          .then(function(){return _uploadFile('/api/brandscreen/logo/'+sid,oofFile);})
          .then(function(){
            d.station.name=oofName;d.station._has_logo=true;d.station.full_screen_logo=true;
            _stations.push(d.station);renderBrands();_populateSchedSelects();
            me.textContent='Saving…';_doSave(sid);
          });
      }).catch(function(){me.disabled=false;me.textContent='Save scheduled change';_msg('Upload failed.',false);});
    } else {
      var stid=isOneOff?document.getElementById('sf-station-oof').value:document.getElementById('sf-station-rec').value;
      if(!stid){_msg('Choose a brand to switch to.',false);me.disabled=false;me.textContent='Save scheduled change';return;}
      _doSave(stid);
    }
  });
})();
</script>
</body>
</html>"""

# ──────────────────────────────────────────────── admin template ──────────────

_ADMIN_TPL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<title>Brand Screen</title>
<style nonce="{{csp_nonce()}}">
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(circle at top,#12376f 0%,var(--bg) 38%,#05101f 100%);color:var(--tx);font-family:system-ui,sans-serif;font-size:14px;min-height:100vh}
/* ── Header — matches Producer View / Listener exactly ── */
.hdr{background:linear-gradient(180deg,rgba(10,31,65,.97),rgba(9,24,48,.97));border-bottom:1px solid var(--bor);padding:14px 24px;display:flex;align-items:center;gap:14px;position:sticky;top:0;z-index:50;backdrop-filter:blur(8px)}
.hdr-logo{font-size:22px}
.hdr-title{font-size:17px;font-weight:700;letter-spacing:-.02em}
.hdr-sub{font-size:11px;color:var(--mu);margin-top:1px}
.hdr-right{margin-left:auto;display:flex;align-items:center;gap:10px}
.hdr-nav{font-size:13px;font-weight:700;color:#fff;background:linear-gradient(135deg,#1a7fe8,#17a8ff);padding:8px 18px;border-radius:20px;text-decoration:none;display:flex;align-items:center;gap:7px;box-shadow:0 2px 12px rgba(23,168,255,.35);transition:filter .2s,box-shadow .2s}
.hdr-nav:hover{filter:brightness(1.1);box-shadow:0 4px 18px rgba(23,168,255,.5)}
.hdr-back{font-size:12px;color:var(--mu);background:rgba(23,52,95,.4);padding:5px 12px;border-radius:20px;border:1px solid var(--bor);text-decoration:none;transition:color .2s}
.hdr-back:hover{color:var(--tx)}
.hdr-powered{font-size:11px;color:var(--mu);opacity:.55;text-decoration:none;letter-spacing:.03em;white-space:nowrap;transition:opacity .2s}
.hdr-powered:hover{opacity:.9}
@media(max-width:700px){.hdr-powered{display:none}.hdr{padding:12px 16px}}
.btn{border:none;border-radius:8px;padding:5px 12px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;text-decoration:none;display:inline-block}
.btn:hover{filter:brightness(1.15)}.bp{background:var(--acc);color:#fff}.bd{background:var(--al);color:#fff}.bg{background:#132040;color:var(--tx)}.bs{font-size:11px;padding:3px 9px}
main{max-width:960px;margin:0 auto;padding:20px 16px}
/* ── Onboarding — shown on first visit when no stations/studios exist ── */
.onboard{background:linear-gradient(135deg,rgba(23,168,255,.09),rgba(23,168,255,.04));border:1px solid rgba(23,168,255,.22);border-radius:18px;padding:28px 32px;margin-bottom:24px}
.onboard-title{font-size:20px;font-weight:800;letter-spacing:-.02em;color:var(--tx);margin-bottom:6px}
.onboard-sub{font-size:13px;color:var(--mu);margin-bottom:22px;line-height:1.5}
.onboard-steps{display:flex;flex-direction:column;gap:12px}
.onboard-step{display:flex;align-items:flex-start;gap:16px;padding:14px 18px;background:rgba(0,0,0,.18);border-radius:12px;border:1px solid rgba(23,52,95,.55)}
.onboard-num{width:30px;height:30px;border-radius:50%;background:var(--acc);color:#fff;font-size:13px;font-weight:800;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:1px}
.onboard-step-title{font-size:13px;font-weight:700;color:var(--tx);margin-bottom:4px}
.onboard-step-desc{font-size:12px;color:var(--mu);line-height:1.55}
/* ── Tab count chips ── */
.tab-count{font-size:10px;font-weight:600;color:var(--mu);background:rgba(23,52,95,.7);padding:1px 6px;border-radius:10px;margin-left:5px;vertical-align:middle}
.tab-btn.active .tab-count{color:rgba(23,168,255,.75);background:rgba(23,168,255,.12)}
@media(max-width:640px){main{padding:16px 12px}.onboard{padding:18px 16px}.onboard-step{gap:12px}}
.tab-nav{display:flex;gap:4px;margin-bottom:20px;border-bottom:1px solid var(--bor)}
.tab-btn{background:none;border:none;color:var(--mu);font-size:13px;font-weight:600;padding:8px 18px;cursor:pointer;border-radius:8px 8px 0 0;border-bottom:2px solid transparent;margin-bottom:-1px;font-family:inherit}
.tab-btn:hover{color:var(--tx)}.tab-btn.active{color:var(--acc);border-bottom-color:var(--acc);background:rgba(23,168,255,.06)}
.tab-panel{display:none}.tab-panel.active{display:block}
.sc{background:var(--sur);border:1px solid var(--bor);border-radius:10px;margin-bottom:10px;overflow:hidden}
.sc-head{display:flex;align-items:center;gap:12px;padding:11px 14px}
.sc-logo{width:80px;height:44px;border-radius:6px;background:#0a1a3a;border:1px solid var(--bor);display:flex;align-items:center;justify-content:center;overflow:hidden;flex-shrink:0}
.sc-logo img{max-width:100%;max-height:100%;object-fit:contain}
.sc-meta{flex:1;min-width:0}.sc-name{font-weight:700;font-size:14px;margin-bottom:2px}.sc-sub{font-size:11px;color:var(--mu)}
.sc-actions{display:flex;gap:6px;align-items:center;flex-shrink:0}
.sc-body{padding:14px;border-top:1px solid var(--bor);display:none}.sc-body.open{display:block}
.field{display:flex;flex-direction:column;gap:4px;margin-bottom:12px}
.field label{font-size:11px;color:var(--mu);font-weight:600;text-transform:uppercase;letter-spacing:.05em}
input[type=text],input[type=url],input[type=number],select{background:#0d1e40;border:1px solid var(--bor);border-radius:6px;color:var(--tx);padding:6px 9px;font-size:13px;font-family:inherit;width:100%}
input[type=text]:focus,input[type=url]:focus,input[type=number]:focus,select:focus{border-color:var(--acc);outline:none}
.cs-scene-list{width:100%;border-collapse:collapse;font-size:11px}
.cs-scene-list th{color:var(--mu);font-size:10px;text-transform:uppercase;letter-spacing:.04em;padding:4px 6px;border-bottom:1px solid var(--bor);text-align:left}
.cs-scene-list td{padding:3px 6px;border-bottom:1px solid rgba(23,52,95,.3)}
.cs-scene-list tr:hover td{background:rgba(23,52,95,.35);cursor:pointer}
.cs-scene-list .cue-num{font-family:monospace;color:var(--acc);min-width:40px}
.cs-scene-list .cue-use{font-size:10px;color:var(--acc);cursor:pointer;padding:1px 6px;border:1px solid var(--acc);border-radius:4px;background:transparent;font-family:inherit}
.cs-strip-row input[type=number]{width:auto}  /* override global width:100% for inline strip inputs */
input[type=color]{background:#0d1e40;border:1px solid var(--bor);border-radius:6px;padding:2px 4px;height:32px;cursor:pointer;width:100%}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}
.sep{border:none;border-top:1px solid var(--bor);margin:12px 0}
.slabel{font-size:11px;font-weight:700;color:var(--mu);text-transform:uppercase;letter-spacing:.08em;margin-bottom:10px}
.logo-prev{width:120px;height:60px;border-radius:6px;background:#0a1a3a;border:1px solid var(--bor);display:flex;align-items:center;justify-content:center;overflow:hidden;margin-bottom:8px}
.logo-prev img{max-width:100%;max-height:100%;object-fit:contain}
.row-url{font-family:monospace;font-size:11px;color:var(--acc);word-break:break-all;background:#071428;border:1px solid var(--bor);border-radius:6px;padding:7px 10px;cursor:pointer;margin-top:5px}
.tok-row{display:flex;gap:6px;align-items:center}
.tok-row input{font-family:monospace;font-size:11px;color:var(--mu)}
.msg-box{border-radius:8px;padding:10px 14px;margin-bottom:14px;font-size:12px;display:none}
.msg-ok{background:#0f2318;color:var(--ok);border:1px solid #166534}
.msg-err{background:#2a0a0a;color:var(--al);border:1px solid #991b1b}
.badge{display:inline-flex;align-items:center;padding:2px 8px;border-radius:20px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.06em}
.b-ok{background:rgba(34,197,94,.12);color:var(--ok);border:1px solid rgba(34,197,94,.25)}
.b-mu{background:rgba(138,164,200,.1);color:var(--mu);border:1px solid rgba(138,164,200,.2)}
.cb-row{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.cb-row input[type=checkbox]{width:14px;height:14px;accent-color:var(--acc);flex-shrink:0}
.np-fields{display:none}.np-fields.open{display:block}
.hint{font-size:11px;color:var(--mu);margin-top:4px}
.swatch{width:14px;height:14px;border-radius:3px;flex-shrink:0;border:1px solid rgba(255,255,255,.15)}
.studio-assign{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--mu)}
.empty-state{text-align:center;padding:48px 24px;color:var(--mu)}
pre.api-eg{background:#050e20;border:1px solid var(--bor);border-radius:8px;padding:14px;font-size:12px;color:#8ad;overflow-x:auto;white-space:pre-wrap;line-height:1.6;margin-top:8px}
.api-key-val{font-family:monospace;font-size:12px;background:#050e20;border:1px solid var(--bor);border-radius:6px;padding:8px 12px;color:var(--acc);word-break:break-all}
table{width:100%;border-collapse:collapse}
th{color:var(--mu);font-size:11px;text-transform:uppercase;letter-spacing:.05em;text-align:left;padding:6px 8px;border-bottom:1px solid var(--bor)}
td{padding:6px 8px;font-size:12px;border-bottom:1px solid rgba(23,52,95,.4)}
td code{font-size:11px;background:#050e20;padding:2px 6px;border-radius:4px;color:var(--acc)}
.lev-badge{display:inline-flex;align-items:center;gap:5px;font-size:10px;color:var(--ok);background:rgba(34,197,94,.1);border:1px solid rgba(34,197,94,.2);border-radius:20px;padding:1px 8px;margin-left:6px}
.sched-row{background:var(--sur);border:1px solid var(--bor);border-radius:8px;margin-bottom:8px;padding:11px 14px;display:flex;align-items:center;gap:12px}
.sched-row.active-sched{border-color:var(--acc)}
.sched-info{flex:1;min-width:0}
.sched-name{font-weight:700;font-size:13px;margin-bottom:3px}
.sched-detail{font-size:11px;color:var(--mu)}
.sched-badge{font-size:10px;font-weight:700;background:rgba(23,168,255,.18);color:var(--acc);border:1px solid rgba(23,168,255,.35);border-radius:999px;padding:2px 8px;margin-left:6px}
.sched-badge.oneoff{background:rgba(245,158,11,.15);color:var(--wn);border-color:rgba(245,158,11,.35)}
.sched-badge.past{background:rgba(239,68,68,.12);color:var(--al);border-color:rgba(239,68,68,.3)}
.day-btns{display:flex;gap:4px;flex-wrap:wrap;margin-top:6px}
.day-btn{padding:3px 8px;border:1px solid var(--bor);border-radius:6px;background:#0d1e40;color:var(--mu);font-size:11px;font-weight:600;cursor:pointer}
.day-btn.sel{background:rgba(23,168,255,.2);color:var(--acc);border-color:var(--acc)}
.stype-btns{display:flex;gap:0;margin-top:6px}
.stype-btn{padding:4px 12px;border:1px solid var(--bor);background:#0d1e40;color:var(--mu);font-size:11px;font-weight:600;cursor:pointer}
.stype-btn:first-child{border-radius:6px 0 0 6px}
.stype-btn:last-child{border-radius:0 6px 6px 0;border-left:none}
.stype-btn.sel{background:rgba(23,168,255,.2);color:var(--acc);border-color:var(--acc)}
input[type=time]{background:#0d1e40;border:1px solid var(--bor);border-radius:6px;color:var(--tx);padding:6px 9px;font-size:13px;font-family:inherit}
input[type=time]:focus{border-color:var(--acc);outline:none}
.tog{position:relative;display:inline-block;width:36px;height:20px;flex-shrink:0}
.tog input{opacity:0;width:0;height:0}
.tog-sl{position:absolute;cursor:pointer;inset:0;background:#1a3060;border-radius:20px;transition:.2s}
.tog-sl:before{content:"";position:absolute;height:14px;width:14px;left:3px;bottom:3px;background:#8aa4c8;border-radius:50%;transition:.2s}
.tog input:checked+.tog-sl{background:var(--acc)}
.tog input:checked+.tog-sl:before{transform:translateX(16px);background:#fff}
</style>
</head>
<body>
<header class="hdr">
  <span class="hdr-logo">📺</span>
  <div>
    <div class="hdr-title">Brand Screen</div>
    <div class="hdr-sub">Studios &amp; stations</div>
  </div>
  <div style="flex:1"></div>
  <a href="/" class="hdr-powered">Powered by SignalScope</a>
  <div class="hdr-right">
    {% if has_presenter %}<a href="/producer" class="hdr-nav">🎙 Producer</a>{% endif %}
    {% if has_listener %}<a href="/listener" class="hdr-nav">🎧 Listen</a>{% endif %}
    <a href="/" class="hdr-back">← Dashboard</a>
  </div>
</header>
<main>
  <div id="msg" class="msg-box"></div>

  <!-- ── Getting started — shown only when nothing is configured yet ── -->
  <div class="onboard" id="onboard-panel" style="display:none">
    <div class="onboard-title">👋 Getting started with Brand Screen</div>
    <div class="onboard-sub">Brand Screen shows animated full-screen branding on your studio displays and updates them in real time. Follow these four steps to get set up.</div>
    <div class="onboard-steps">
      <div class="onboard-step">
        <div class="onboard-num">1</div>
        <div style="flex:1">
          <div class="onboard-step-title">Create a Station (brand configuration)</div>
          <div class="onboard-step-desc" style="margin-bottom:10px">A <strong style="color:var(--tx)">Station</strong> holds your brand — logo, colours, background style, and logo animation. Create one for each radio brand or channel you want to display.</div>
          <button class="btn bp bs" id="onboard-add-station">＋ Create First Station</button>
        </div>
      </div>
      <div class="onboard-step">
        <div class="onboard-num">2</div>
        <div style="flex:1">
          <div class="onboard-step-title">Upload a logo &amp; set your brand colour</div>
          <div class="onboard-step-desc">Open the station you just created and upload a PNG logo with a transparent background. Set your brand colour — the entire background theme (gradients, particles, glow) is derived from it automatically. Your logo fills most of the screen.</div>
        </div>
      </div>
      <div class="onboard-step">
        <div class="onboard-num">3</div>
        <div style="flex:1">
          <div class="onboard-step-title">Create a Studio (a physical display screen)</div>
          <div class="onboard-step-desc" style="margin-bottom:10px">A <strong style="color:var(--tx)">Studio</strong> represents a screen in your building — "Studio 1 Screen", "Reception Display", etc. Each studio shows one station at a time and can be switched instantly without touching the display.</div>
          <button class="btn bg bs" id="onboard-add-studio">＋ Create First Studio</button>
        </div>
      </div>
      <div class="onboard-step">
        <div class="onboard-num">4</div>
        <div style="flex:1">
          <div class="onboard-step-title">Assign a station and open the screen URL</div>
          <div class="onboard-step-desc">In the Studios tab, open a studio, assign your station to it, and copy the <strong style="color:var(--tx)">Screen URL</strong>. Open that URL full-screen in a browser on your studio display — it authenticates automatically. The display updates in real time when you change the assignment here.</div>
        </div>
      </div>
    </div>
  </div>

  <nav class="tab-nav" id="tab-nav">
    <button class="tab-btn active" data-tab="studios">🖥 Studios<span class="tab-count" id="tc-studios"></span></button>
    <button class="tab-btn" data-tab="stations">📡 Stations<span class="tab-count" id="tc-stations"></span></button>
    <button class="tab-btn" data-tab="schedules">⏰ Schedules<span class="tab-count" id="tc-schedules"></span></button>
    <button class="tab-btn" data-tab="api">🔗 REST API</button>
  </nav>

  <div class="tab-panel active" id="tp-studios">
    <div style="margin-bottom:14px"><button class="btn bp" id="add-studio-btn">＋ Add Studio</button></div>
    <div id="studio-list"></div>
  </div>

  <div class="tab-panel" id="tp-stations">
    <div style="margin-bottom:14px"><button class="btn bp" id="add-station-btn">＋ Add Station</button></div>
    <div id="station-list"></div>
  </div>

  <div class="tab-panel" id="tp-schedules">
    <!-- Add Schedule form -->
    <div class="sc" id="add-sched-wrap" style="margin-bottom:14px">
      <div class="sc-head" style="cursor:pointer" id="add-sched-toggle">
        <span style="font-weight:700;font-size:13px">＋ Add Schedule</span>
        <span style="margin-left:auto;font-size:11px;color:var(--mu)" id="add-sched-arrow">▼</span>
      </div>
      <div class="sc-body" id="add-sched-body" style="display:none">
        <div class="field">
          <label>Schedule Name</label>
          <input type="text" id="sf-name" placeholder="e.g. Afternoon Show" style="max-width:340px">
        </div>
        <div class="grid2" style="margin-bottom:12px">
          <div class="field" style="margin-bottom:0">
            <label>Studio</label>
            <select id="sf-studio"></select>
          </div>
          <div class="field" style="margin-bottom:0">
            <label>Brand (Station)</label>
            <select id="sf-station"></select>
          </div>
        </div>
        <div class="field" style="margin-bottom:10px">
          <label>Schedule Type</label>
          <div class="stype-btns" id="sf-type-btns">
            <button type="button" class="stype-btn sel" data-type="recurring">🔁 Recurring (weekly)</button>
            <button type="button" class="stype-btn" data-type="oneoff">📅 One-off (specific date)</button>
          </div>
        </div>
        <div id="sf-recurring-section">
          <div class="field">
            <label>Days (leave all unselected = every day)</label>
            <div class="day-btns" id="sf-days">
              <button type="button" class="day-btn" data-day="mon">Mon</button>
              <button type="button" class="day-btn" data-day="tue">Tue</button>
              <button type="button" class="day-btn" data-day="wed">Wed</button>
              <button type="button" class="day-btn" data-day="thu">Thu</button>
              <button type="button" class="day-btn" data-day="fri">Fri</button>
              <button type="button" class="day-btn" data-day="sat">Sat</button>
              <button type="button" class="day-btn" data-day="sun">Sun</button>
            </div>
          </div>
        </div>
        <div id="sf-oneoff-section" style="display:none">
          <div class="field">
            <label>Date</label>
            <input type="date" id="sf-date" style="max-width:200px">
          </div>
        </div>
        <div class="grid2" style="margin-bottom:14px">
          <div class="field" style="margin-bottom:0">
            <label>Start Time</label>
            <input type="time" id="sf-start" value="09:00">
          </div>
          <div class="field" style="margin-bottom:0">
            <label>End Time</label>
            <input type="time" id="sf-end" value="17:00">
          </div>
        </div>
        <button class="btn bp" id="sf-save-btn">Save Schedule</button>
      </div>
    </div>
    <!-- Schedule list -->
    <div id="sched-list"><div style="color:var(--mu);font-size:13px">No schedules yet.</div></div>
  </div>

  <div class="tab-panel" id="tp-api">
    <div class="sc"><div class="sc-body open">
      <div class="slabel">API Key</div>
      <div class="api-key-val" id="api-key-display">{{api_key|e}}</div>
      <div style="display:flex;gap:8px;margin-top:10px">
        <button class="btn bg bs" data-action="copy-api-key">Copy</button>
        <button class="btn bd bs" data-action="regen-api-key">Regenerate</button>
      </div>
    </div></div>
    <div class="sc"><div class="sc-body open">
      <div class="slabel">Change assignment</div>
      <pre class="api-eg" id="eg-assign">PUT {{origin}}/api/brandscreen/studio/{studio_id}/station
Authorization: Bearer {{api_key|e}}
Content-Type: application/json

{"station_id": "{station_id}"}
</pre>
      <p class="hint" style="margin-top:6px">The browser display updates instantly via SSE — no reload needed. Send <code>{"station_id":""}</code> to unassign.</p>
      <hr class="sep">
      <div class="slabel" style="margin-bottom:8px">Full-screen takeover — by Studio</div>
      <pre class="api-eg" id="eg-takeover">POST {{origin}}/api/brandscreen/studio/{studio_id}/takeover
Authorization: Bearer {{api_key|e}}
Content-Type: application/json

{"title": "BREAKING", "text": "Your message here"}</pre>
      <p class="hint" style="margin-top:6px">Instantly overlays one studio display. Background colours follow the station brand palette. Display updates in real time — no reload needed.</p>
      <pre class="api-eg" style="margin-top:8px">DELETE {{origin}}/api/brandscreen/studio/{studio_id}/takeover
Authorization: Bearer {{api_key|e}}</pre>
      <p class="hint" style="margin-top:6px">Clears the takeover on that studio and returns to normal branding.</p>
      <hr class="sep">
      <div class="slabel" style="margin-bottom:8px">Full-screen takeover — by Station (all screens)</div>
      <pre class="api-eg" id="eg-station-takeover">POST {{origin}}/api/brandscreen/station/{station_id}/takeover
Authorization: Bearer {{api_key|e}}
Content-Type: application/json

{"title": "BREAKING", "text": "Your message here"}</pre>
      <p class="hint" style="margin-top:6px">Fires on <strong>every studio currently displaying this station</strong>. Useful for sending a message to all screens showing a brand at once. A studio-level takeover takes priority over a station-level one on that screen.</p>
      <pre class="api-eg" style="margin-top:8px">DELETE {{origin}}/api/brandscreen/station/{station_id}/takeover
Authorization: Bearer {{api_key|e}}</pre>
      <p class="hint" style="margin-top:6px">Clears the station-level takeover from all screens showing that station (studios with a studio-level override are not affected).</p>
      <hr class="sep">
      <div class="slabel" style="margin-bottom:8px">List studios &amp; stations</div>
      <pre class="api-eg">GET {{origin}}/api/brandscreen/studios
Authorization: Bearer {{api_key|e}}</pre>
      <hr class="sep">
      <div class="slabel" style="margin-bottom:8px">Reference</div>
      <table><thead><tr><th>Name</th><th>Type</th><th>ID</th></tr></thead>
      <tbody id="api-ref-body"></tbody></table>
    </div></div>
  </div>
</main>

<input type="file" id="logo-input" accept="image/png,image/svg+xml,image/jpeg,image/webp,image/gif" style="display:none">

<script nonce="{{csp_nonce()}}">
var _studios     = {{studios_json|safe}};
var _stations    = {{stations_json|safe}};
var _zetStations = [];
var _streams     = {{streams_json|safe}};
var _sbStudios   = [];   // studioboard studios for mic-live linking
var _currentLogoSid = null;

function _csrf(){ return (document.querySelector('meta[name="csrf-token"]')||{}).content||''; }
function _post(url,data){ return fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':_csrf()},body:JSON.stringify(data)}); }
function _patch(url,data){ return fetch(url,{method:'PATCH',headers:{'Content-Type':'application/json','X-CSRFToken':_csrf()},body:JSON.stringify(data)}); }
function _del(url){ return fetch(url,{method:'DELETE',headers:{'X-CSRFToken':_csrf()}}); }
function _msg(txt,ok){
  var el=document.getElementById('msg');
  el.className='msg-box '+(ok?'msg-ok':'msg-err');
  el.textContent=txt; el.style.display='block';
  clearTimeout(el._t); el._t=setTimeout(function(){el.style.display='none';},4500);
}
function _esc(s){var d=document.createElement('div');d.appendChild(document.createTextNode(s||''));return d.innerHTML;}
function _v(id){var el=document.getElementById(id);return el?(el.type==='checkbox'?el.checked:el.value):null;}
function _stById(id){return _stations.find(function(s){return s.id===id;})||null;}
function _sdById(id){return _studios.find(function(s){return s.id===id;})||null;}
var _BG_L={particles:'Particles',aurora:'Aurora',waves:'Waves',minimal:'Minimal',beams:'Beams',grid:'Grid',burst:'Burst',haze:'Haze'};
var _AN_L={orbit:'Orbit rings',pulse:'Pulse',glow:'Glow',float:'Float',spin:'Spin',glitch:'Glitch',bounce:'Bounce',none:'Static'};
var _NP_L={zetta:'Zetta',json_api:'JSON API',manual:'Manual',none:'None'};

document.getElementById('tab-nav').addEventListener('click',function(e){
  var btn=e.target.closest('.tab-btn'); if(!btn) return;
  document.querySelectorAll('.tab-btn').forEach(function(b){b.classList.remove('active');});
  document.querySelectorAll('.tab-panel').forEach(function(p){p.classList.remove('active');});
  if(btn.dataset.tab==='schedules') _populateSchedSelects();
  btn.classList.add('active');
  document.getElementById('tp-'+btn.dataset.tab).classList.add('active');
});

// ── Studios ───────────────────────────────────────────────────────────────
function renderStudios(){
  if(typeof _checkOnboard==='function') _checkOnboard();
  var el=document.getElementById('studio-list');
  if(!_studios.length){
    el.innerHTML='<div class="empty-state"><div style="font-size:36px;margin-bottom:10px">🖥️</div>No studios yet.<br><span style="font-size:12px;margin-top:6px;display:block">A studio is a physical display screen. Add one and assign it a station.</span></div>';
    return;
  }
  el.innerHTML=_studios.map(function(sd){
    var st=_stById(sd.station_id||'');
    var assignHtml=st
      ?'<span class="swatch" style="background:'+_esc(st.brand_colour)+'"></span>'+_esc(st.name)
      :'<span style="opacity:.5">— unassigned —</span>';
    return '<div class="sc" id="sd-'+sd.id+'">'
      +'<div class="sc-head">'
      +'<div style="flex:1;min-width:0">'
      +'<div class="sc-name">'+_esc(sd.name)+'</div>'
      +'<div class="studio-assign" style="margin-top:3px">'+assignHtml+'</div>'
      +'</div>'
      +'<div class="sc-actions">'
      +'<button class="btn bg bs" data-action="toggle-sd" data-sid="'+sd.id+'">Edit</button>'
      +'<a class="btn bg bs" href="/brandscreen/studio/'+sd.id+'?token='+sd.token+'" target="_blank">Preview ↗</a>'
      +'<button class="btn bd bs" data-action="del-sd" data-sid="'+sd.id+'">Delete</button>'
      +'</div></div>'
      +'<div class="sc-body" id="sdb-'+sd.id+'">'+_studioForm(sd)+'</div>'
      +'</div>';
  }).join('');
}
function _studioForm(sd){
  var stOpts=_stations.map(function(st){
    return '<option value="'+st.id+'"'+(sd.station_id===st.id?' selected':'')+'>'+_esc(st.name)+'</option>';
  }).join('');
  var sbOpts=_sbStudios.map(function(s){
    return '<option value="'+_esc(s.id)+'"'+((sd.sb_studio_id||'')=== s.id?' selected':'')+'>'+_esc(s.name)+'</option>';
  }).join('');
  var csHubSiteOpts=_hubSites.map(function(sn){
    return '<option value="'+_esc(sn)+'"'+((sd.cueserver_site||'')===sn?' selected':'')+'>'+_esc(sn)+'</option>';
  }).join('');
  var screenUrl=location.origin+'/brandscreen/studio/'+sd.id+'?token='+sd.token;
  return '<div class="grid2" style="margin-bottom:12px">'
    +'<div class="field"><label>Studio Name</label><input type="text" id="sd-name-'+sd.id+'" value="'+_esc(sd.name)+'"></div>'
    +'<div class="field"><label>Assigned Station</label><select id="sd-st-'+sd.id+'"><option value="">— none —</option>'+stOpts+'</select></div>'
    +'</div>'
    +'<div class="field" style="margin-bottom:12px">'
    +'<label>Mic Live — Link to Studio Board Studio</label>'
    +'<select id="sd-sbst-'+sd.id+'"><option value="">— none (no mic suppression) —</option>'+sbOpts+'</select>'
    +'<p class="hint" style="margin-top:4px">When the selected Studio Board studio has a mic live, this Brand Screen will show a full-screen suppression overlay.</p>'
    +'</div>'
    +'<hr class="sep">'
    +'<div class="slabel">CueServer LED Integration</div>'
    +'<div class="grid2" style="margin-bottom:8px">'
    +'<div class="field"><label>Client Site</label>'
    +'<select id="sd-cs-site-'+sd.id+'"><option value="">— none —</option>'+csHubSiteOpts+'</select>'
    +'<p class="hint" style="margin-top:4px">The SignalScope client node that is on the same LAN as the CueServer appliance for this studio.</p>'
    +'</div>'
    +'<div class="field"><label>CueServer Host</label>'
    +'<input type="text" id="sd-cs-host-'+sd.id+'" value="'+_esc(sd.cueserver_host||'')+'" placeholder="192.168.1.50">'
    +'<p class="hint" style="margin-top:4px">IP address or hostname of the CueServer appliance on the studio LAN.</p>'
    +'</div>'
    +'</div>'
    +'<div class="slabel" style="margin-bottom:6px">DMX Strips <span style="font-weight:400;font-size:10px;text-transform:none;letter-spacing:0;color:var(--mu)">— add one row per LED strip; brightness is set per brand</span></div>'
    +'<div id="sd-cs-strips-'+sd.id+'">'
    +(function(){
      var strips=sd.cs_strips&&sd.cs_strips.length?sd.cs_strips:[];
      return strips.map(function(strip,i){return _csStripRowHtml(sd.id,strip,i);}).join('');
    })()
    +'</div>'
    +'<button class="btn bg bs" data-action="cs-add-strip" data-sid="'+sd.id+'" style="margin-bottom:12px">+ Add Strip</button>'
    +'<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">'
    +'<button class="btn bg bs" data-action="cs-fetch-scenes" data-sid="'+sd.id+'">🔍 Fetch Scenes</button>'
    +'<span id="sd-cs-status-'+sd.id+'" style="font-size:11px;color:var(--mu)"></span>'
    +'</div>'
    +'<div id="sd-cs-scenes-'+sd.id+'" style="display:none;margin-bottom:12px">'
    +'</div>'
    +'<div class="slabel">Screen URL</div>'
    +'<div class="tok-row" style="margin-bottom:6px"><input type="text" value="'+_esc(sd.token)+'" readonly>'
    +'<button class="btn bg bs" data-action="regen-sd-tok" data-sid="'+sd.id+'">Regenerate</button></div>'
    +'<div class="row-url" id="sd-url-'+sd.id+'" title="Click to copy">'+_esc(screenUrl)+'</div>'
    +'<p class="hint" style="margin-top:5px">Open full-screen on your studio display. Token authenticates automatically.</p>'
    +'<div style="display:flex;gap:8px;margin-top:14px">'
    +'<button class="btn bp" data-action="save-sd" data-sid="'+sd.id+'">Save</button>'
    +'<button class="btn bg" data-action="toggle-sd" data-sid="'+sd.id+'">Cancel</button>'
    +'</div>'
    +'<hr class="sep">'
    +'<div class="slabel">Full-Screen Takeover</div>'
    +'<div class="grid2" style="margin-bottom:8px">'
    +'<div class="field"><label>Title (large, brand colour)</label>'
    +'<input type="text" id="sd-to-title-'+sd.id+'" placeholder="BREAKING NEWS" maxlength="80"></div>'
    +'<div class="field"><label>Body text</label>'
    +'<input type="text" id="sd-to-text-'+sd.id+'" placeholder="Your message here…" maxlength="200"></div>'
    +'</div>'
    +'<div style="display:flex;gap:8px">'
    +'<button class="btn bp bs" data-action="takeover-send" data-sid="'+sd.id+'">▶ Send Takeover</button>'
    +'<button class="btn bd bs" data-action="takeover-clear" data-sid="'+sd.id+'">✕ Clear</button>'
    +'</div>'
    +'<p class="hint" style="margin-top:5px">Instantly overlays this studio&#39;s screen with large title and text in the station brand colours. Background follows the assigned station palette.</p>';
}

// ── Stations ──────────────────────────────────────────────────────────────
function renderStations(){
  if(typeof _checkOnboard==='function') _checkOnboard();
  var el=document.getElementById('station-list');
  if(!_stations.length){
    el.innerHTML='<div class="empty-state"><div style="font-size:36px;margin-bottom:10px">📡</div>No stations yet.<br><span style="font-size:12px;margin-top:6px;display:block">A station is a brand config (logo, colours, animations). Assign it to one or more studios.</span></div>';
    return;
  }
  el.innerHTML=_stations.map(function(s){
    var lImg=s._has_logo?'<img src="/api/brandscreen/logo/'+s.id+'?t='+Date.now()+'" alt="">':'<span style="font-size:20px;opacity:.25">📺</span>';
    var levBadge=s.level_key?'<span class="lev-badge">⚡ Audio reactive</span>':'';
    return '<div class="sc" id="st-'+s.id+'">'
      +'<div class="sc-head">'
      +'<div class="sc-logo">'+lImg+'</div>'
      +'<div class="sc-meta">'
      +'<div class="sc-name" style="display:flex;align-items:center;gap:8px">'
      +'<span class="swatch" style="background:'+_esc(s.brand_colour)+'"></span>'+_esc(s.name)+levBadge+'</div>'
      +'<div class="sc-sub">'+(_BG_L[s.bg_style]||s.bg_style)+' · '+(_AN_L[s.logo_anim]||s.logo_anim)+' · NP: '+(_NP_L[s.np_source]||s.np_source)+'</div>'
      +'</div>'
      +'<div class="sc-actions">'
      +'<span class="badge '+(s.enabled?'b-ok':'b-mu')+'">'+(s.enabled?'On':'Off')+'</span>'
      +'<button class="btn bg bs" data-action="toggle-st" data-sid="'+s.id+'">Edit</button>'
      +'<button class="btn bd bs" data-action="del-st" data-sid="'+s.id+'">Delete</button>'
      +'</div></div>'
      +'<div class="sc-body" id="stb-'+s.id+'">'+_stationForm(s)+'</div>'
      +'</div>';
  }).join('');
}

function _stationForm(s){
  var zetOpts=_zetStations.map(function(z){
    return '<option value="'+_esc(z.key)+'"'+(s.np_zetta_key===z.key?' selected':'')+'>'+_esc(z.name)+'</option>';
  }).join('');
  var streamOpts=_streams.map(function(st){
    return '<option value="'+_esc(st.key)+'"'+(s.level_key===st.key?' selected':'')+'>'+_esc(st.label)+'</option>';
  }).join('');
  return '<div class="grid2">'
    +'<div class="field"><label>Station Name</label><input type="text" id="f-name-'+s.id+'" value="'+_esc(s.name)+'"></div>'
    +'<div class="field" style="justify-content:flex-end"><label style="margin-bottom:6px">Status</label>'
    +'<label style="display:flex;align-items:center;gap:8px;cursor:pointer">'
    +'<input type="checkbox" id="f-en-'+s.id+'"'+(s.enabled?' checked':'')+' style="accent-color:var(--acc);width:16px;height:16px">'
    +'<span>Enabled</span></label></div></div>'
    +'<div class="grid2">'
    +'<div class="field"><label>Brand Colour</label><input type="color" id="f-brand-'+s.id+'" value="'+_esc(s.brand_colour)+'"></div>'
    +'<div class="field"><label>Accent Colour</label><input type="color" id="f-accent-'+s.id+'" value="'+_esc(s.accent_colour)+'"></div>'
    +'</div>'
    +'<hr class="sep">'
    +'<div class="slabel">Animation</div>'
    +'<div class="grid2">'
    +'<div class="field"><label>Background</label><select id="f-bg-'+s.id+'">'
    +'<option value="particles"'+(s.bg_style==='particles'?' selected':'')+'>✦ Particles</option>'
    +'<option value="aurora"'+(s.bg_style==='aurora'?' selected':'')+'>◎ Aurora</option>'
    +'<option value="waves"'+(s.bg_style==='waves'?' selected':'')+'>⌇ Waves</option>'
    +'<option value="beams"'+(s.bg_style==='beams'?' selected':'')+'>🔦 Beams</option>'
    +'<option value="grid"'+(s.bg_style==='grid'?' selected':'')+'>⊞ Grid</option>'
    +'<option value="burst"'+(s.bg_style==='burst'?' selected':'')+'>✸ Burst</option>'
    +'<option value="haze"'+(s.bg_style==='haze'?' selected':'')+'>🌫 Haze</option>'
    +'<option value="minimal"'+(s.bg_style==='minimal'?' selected':'')+'>▪ Minimal</option>'
    +'</select></div>'
    +'<div class="field"><label>Logo Animation</label><select id="f-anim-'+s.id+'">'
    +'<option value="orbit"'+(s.logo_anim==='orbit'?' selected':'')+'>⊙ Orbit rings</option>'
    +'<option value="pulse"'+(s.logo_anim==='pulse'?' selected':'')+'>◉ Pulse</option>'
    +'<option value="glow"'+(s.logo_anim==='glow'?' selected':'')+'>✦ Glow</option>'
    +'<option value="float"'+(s.logo_anim==='float'?' selected':'')+'>↕ Float</option>'
    +'<option value="spin"'+(s.logo_anim==='spin'?' selected':'')+'>↻ Spin</option>'
    +'<option value="glitch"'+(s.logo_anim==='glitch'?' selected':'')+'>⚡ Glitch</option>'
    +'<option value="bounce"'+(s.logo_anim==='bounce'?' selected':'')+'>↑ Bounce</option>'
    +'<option value="none"'+(s.logo_anim==='none'?' selected':'')+'>— Static</option>'
    +'</select></div></div>'
    +'<div class="grid3" style="margin-bottom:8px">'
    +'<div class="cb-row"><input type="checkbox" id="f-clk-'+s.id+'"'+(s.show_clock?' checked':'')+'><label for="f-clk-'+s.id+'">Clock</label></div>'
    +'<div class="cb-row"><input type="checkbox" id="f-oair-'+s.id+'"'+(s.show_on_air?' checked':'')+'><label for="f-oair-'+s.id+'">On Air Badge</label></div>'
    +'<div class="cb-row"><input type="checkbox" id="f-np-'+s.id+'"'+(s.show_now_playing?' checked':'')+'><label for="f-np-'+s.id+'">Now Playing</label></div>'
    +'</div>'
    +'<div class="cb-row" style="margin-bottom:4px">'
    +'<input type="checkbox" id="f-fsl-'+s.id+'"'+(s.full_screen_logo?' checked':'')+'>'
    +'<label for="f-fsl-'+s.id+'"><strong>Full-screen logo mode</strong> — show logo only, no background effects, animations, clock, or now-playing</label>'
    +'</div>'
    +'<p class="hint" style="margin-bottom:4px">Upload a logo and tick this for a clean static brand image. Takeovers still work on top.</p>'
    +'<hr class="sep">'
    +'<div class="slabel">Audio Level <span style="font-weight:400;font-size:10px;text-transform:none;letter-spacing:0">— drives orbit opacity, spin speed, bounce, pulse rate, glow, beams, burst, grid &amp; particles</span></div>'
    +'<div class="field"><label>Input Stream</label>'
    +'<select id="f-lvkey-'+s.id+'">'
    +'<option value="">— none (static animations) —</option>'
    +streamOpts
    +'</select>'
    +(streamOpts?'':'<p class="hint">No streams detected from hub — check that sites are connected and sending heartbeats.</p>')
    +'</div>'
    +'<hr class="sep">'
    +'<div class="slabel">Logo</div>'
    +'<div class="logo-prev" id="lp-'+s.id+'">'+(s._has_logo?'<img src="/api/brandscreen/logo/'+s.id+'?t='+Date.now()+'" alt="">':'<span style="font-size:28px;opacity:.2">📺</span>')+'</div>'
    +'<div style="display:flex;gap:8px;align-items:center">'
    +'<button class="btn bg bs" data-action="upload-logo" data-sid="'+s.id+'">Upload Logo</button>'
    +(s._has_logo?'<button class="btn bd bs" data-action="del-logo" data-sid="'+s.id+'">Remove</button>':'')
    +'</div>'
    +'<p class="hint" style="margin-top:5px">PNG with transparent background recommended. SVG, JPG, WebP also supported.</p>'
    +(function(){
      // Only render the CueServer LED section if at least one studio has CueServer configured.
      var csStudios=_studios.filter(function(sd){return sd.cueserver_site&&sd.cueserver_host;});
      if(!csStudios.length) return '';
      var bri=s.cs_brightness!==undefined?s.cs_brightness:100;
      var stOpts='<option value="">— choose studio —</option>'
        +csStudios.map(function(sd){return '<option value="'+_esc(sd.id)+'">'+_esc(sd.name)+'</option>';}).join('');
      return '<hr class="sep">'
        +'<div class="slabel">CueServer LED</div>'
        +'<div class="field" style="margin-bottom:12px">'
        +'<label>Brightness <span id="f-csBri-lbl-'+s.id+'" style="font-weight:400;color:var(--tx)">'+bri+'%</span></label>'
        +'<input type="range" id="f-csBri-'+s.id+'" min="0" max="100" value="'+bri+'" style="width:100%;max-width:320px;accent-color:var(--acc);cursor:pointer">'
        +'<p class="hint" style="margin-top:4px">0 = off · 100 = full. The brand colour is set from the colour picker above — brightness controls how intense the LEDs are for this brand.</p>'
        +'</div>'
        +'<div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;margin-bottom:4px">'
        +'<div class="field" style="flex:1;min-width:160px"><label>Preview on studio</label>'
        +'<select id="f-cs-studio-'+s.id+'">'+stOpts+'</select></div>'
        +'<div style="display:flex;align-items:center;gap:8px;padding-bottom:1px">'
        +'<span id="f-cs-swatch-'+s.id+'" style="width:22px;height:22px;border-radius:4px;border:1px solid var(--bor);background:'+_esc(s.brand_colour||'#17a8ff')+';flex-shrink:0"></span>'
        +'<button class="btn bg bs" data-action="cs-preview" data-sid="'+s.id+'">💡 Preview</button>'
        +'</div>'
        +'</div>'
        +'<span id="f-cs-status-'+s.id+'" style="font-size:11px;color:var(--mu)"></span>';
    })()
    +'<hr class="sep">'
    +'<div class="slabel">Now Playing Source</div>'
    +'<div class="field"><label>Source</label>'
    +'<select id="f-npsrc-'+s.id+'" data-np-sel="'+s.id+'">'
    +'<option value="none"'+(s.np_source==='none'?' selected':'')+'>None</option>'
    +'<option value="zetta"'+(s.np_source==='zetta'?' selected':'')+'>Zetta</option>'
    +'<option value="json_api"'+(s.np_source==='json_api'?' selected':'')+'>JSON API</option>'
    +'<option value="manual"'+(s.np_source==='manual'?' selected':'')+'>Manual text</option>'
    +'</select></div>'
    +'<div class="np-fields'+(s.np_source==='zetta'?' open':'')+'" id="npf-zetta-'+s.id+'">'
    +'<div class="field"><label>Zetta Station</label><select id="f-zpkey-'+s.id+'"><option value="">— choose —</option>'+zetOpts+'</select>'
    +(zetOpts?'':'<p class="hint">No Zetta stations found.</p>')+'</div></div>'
    +'<div class="np-fields'+(s.np_source==='json_api'?' open':'')+'" id="npf-json-'+s.id+'">'
    +'<div class="field"><label>API URL</label><input type="url" id="f-npurl-'+s.id+'" value="'+_esc(s.np_api_url)+'"></div>'
    +'<div class="grid2"><div class="field"><label>Title path</label><input type="text" id="f-nptpath-'+s.id+'" value="'+_esc(s.np_api_title_path)+'"></div>'
    +'<div class="field"><label>Artist path</label><input type="text" id="f-npapath-'+s.id+'" value="'+_esc(s.np_api_artist_path)+'"></div></div>'
    +'<p class="hint">Dot notation, e.g. <code>now_playing.song.title</code></p></div>'
    +'<div class="np-fields'+(s.np_source==='manual'?' open':'')+'" id="npf-manual-'+s.id+'">'
    +'<div class="field"><label>Display text</label><input type="text" id="f-npman-'+s.id+'" value="'+_esc(s.np_manual)+'"></div></div>'
    +'<hr class="sep">'
    +'<div class="slabel">Live Message</div>'
    +'<div class="field"><label>Message (amber banner — blank to clear)</label>'
    +'<input type="text" id="f-msg-'+s.id+'" value="'+_esc(s.message)+'" maxlength="200"></div>'
    +'<div style="display:flex;gap:8px;margin-top:14px">'
    +'<button class="btn bp" data-action="save-st" data-sid="'+s.id+'">Save</button>'
    +'<button class="btn bg" data-action="toggle-st" data-sid="'+s.id+'">Cancel</button>'
    +'</div>';
}

function renderApiRef(){
  var rows='';
  _studios.forEach(function(sd){rows+='<tr><td>'+_esc(sd.name)+'</td><td style="color:var(--wn)">Studio</td><td><code>'+_esc(sd.id)+'</code></td></tr>';});
  _stations.forEach(function(st){rows+='<tr><td>'+_esc(st.name)+'</td><td style="color:var(--acc)">Station</td><td><code>'+_esc(st.id)+'</code></td></tr>';});
  document.getElementById('api-ref-body').innerHTML=rows;
}

// ── Actions ───────────────────────────────────────────────────────────────
function _addStudio(){
  _post('/api/brandscreen/studio',{}).then(function(r){return r.json();}).then(function(d){
    if(d.error){_msg(d.error,false);return;}
    _studios.push(d.studio); renderStudios(); renderApiRef();
    document.getElementById('sdb-'+d.studio.id).classList.add('open');
    _msg('Studio created.',true);
  });
}
function _saveSd(sid){
  var sd=_sdById(sid); if(!sd) return;
  _post('/api/brandscreen/studio/'+sid,{
    name:_v('sd-name-'+sid)||sd.name,
    station_id:_v('sd-st-'+sid)||'',
    sb_studio_id:_v('sd-sbst-'+sid)||'',
    cueserver_site:_v('sd-cs-site-'+sid)||'',
    cueserver_host:_v('sd-cs-host-'+sid)||'',
    cs_strips:_csGetStrips(sid),
  }).then(function(r){return r.json();}).then(function(d){
    if(d.error){_msg(d.error,false);return;}
    Object.assign(sd,d.studio); renderStudios(); renderApiRef(); _msg('Saved.',true);
  });
}
function _delSd(sid){
  if(!confirm('Delete studio?')) return;
  _del('/api/brandscreen/studio/'+sid).then(function(r){return r.json();}).then(function(d){
    if(d.error){_msg(d.error,false);return;}
    _studios=_studios.filter(function(s){return s.id!==sid;}); renderStudios(); renderApiRef(); _msg('Deleted.',true);
  });
}
function _regenSdTok(sid){
  if(!confirm('Regenerate token? The current URL will stop working.')) return;
  _post('/api/brandscreen/studio/'+sid+'/regen_token',{}).then(function(r){return r.json();}).then(function(d){
    if(d.error){_msg(d.error,false);return;}
    var sd=_sdById(sid); if(sd) sd.token=d.token;
    renderStudios(); document.getElementById('sdb-'+sid).classList.add('open'); _msg('Token regenerated.',true);
  });
}
function _addStation(){
  _post('/api/brandscreen/station',{}).then(function(r){return r.json();}).then(function(d){
    if(d.error){_msg(d.error,false);return;}
    _stations.push(d.station); renderStations(); renderApiRef();
    document.getElementById('stb-'+d.station.id).classList.add('open'); _msg('Station created.',true);
  });
}
function _saveSt(sid, _onDone){
  var s=_stById(sid); if(!s) return;
  var data={
    name:_v('f-name-'+sid)||s.name, enabled:!!_v('f-en-'+sid),
    brand_colour:_v('f-brand-'+sid)||s.brand_colour, accent_colour:_v('f-accent-'+sid)||s.accent_colour,
    bg_style:_v('f-bg-'+sid)||s.bg_style, logo_anim:_v('f-anim-'+sid)||s.logo_anim,
    show_clock:!!_v('f-clk-'+sid), show_on_air:!!_v('f-oair-'+sid), show_now_playing:!!_v('f-np-'+sid),
    full_screen_logo:!!_v('f-fsl-'+sid),
    level_key:_v('f-lvkey-'+sid)||'',
    np_source:_v('f-npsrc-'+sid)||'none', np_zetta_key:_v('f-zpkey-'+sid)||'',
    np_api_url:_v('f-npurl-'+sid)||'', np_api_title_path:_v('f-nptpath-'+sid)||'',
    np_api_artist_path:_v('f-npapath-'+sid)||'', np_manual:_v('f-npman-'+sid)||'',
    message:_v('f-msg-'+sid)||'',
    cs_brightness:parseInt(_v('f-csBri-'+sid)||'100',10),
  };
  _post('/api/brandscreen/station/'+sid, data).then(function(r){return r.json();}).then(function(d){
    if(d.error){_msg(d.error,false);return;}
    Object.assign(s,d.station);
    // Update in-memory brightness so live swatch stays correct
    renderStations(); renderStudios();
    if(_onDone) _onDone(); else _msg('Saved.',true);
  });
}
function _delSt(sid){
  if(!confirm('Delete this station? Studios assigned to it will become unassigned.')) return;
  _del('/api/brandscreen/station/'+sid).then(function(r){return r.json();}).then(function(d){
    if(d.error){_msg(d.error,false);return;}
    _stations=_stations.filter(function(s){return s.id!==sid;});
    _studios.forEach(function(sd){if(sd.station_id===sid)sd.station_id='';});
    renderStations(); renderStudios(); renderApiRef(); _msg('Deleted.',true);
  });
}
function _uploadLogo(sid){ _currentLogoSid=sid; document.getElementById('logo-input').value=''; document.getElementById('logo-input').click(); }
function _doUpload(file){
  if(!file||!_currentLogoSid) return;
  var sid=_currentLogoSid;
  var fd=new FormData(); fd.append('logo',file);
  fetch('/api/brandscreen/logo/'+sid,{method:'POST',headers:{'X-CSRFToken':_csrf()},body:fd})
    .then(function(r){return r.json();}).then(function(d){
      if(d.error){_msg(d.error,false);return;}
      var s=_stById(sid); if(s) s._has_logo=true; renderStations(); _msg('Logo uploaded.',true);
    });
}
function _delLogo(sid){
  if(!confirm('Remove logo?')) return;
  _del('/api/brandscreen/logo/'+sid).then(function(r){return r.json();}).then(function(d){
    if(d.error){_msg(d.error,false);return;}
    var s=_stById(sid); if(s) s._has_logo=false; renderStations(); _msg('Logo removed.',true);
  });
}
function _npSrcChanged(sid){
  var src=_v('f-npsrc-'+sid);
  ['zetta','json_api','manual'].forEach(function(k){
    var el=document.getElementById('npf-'+k+'-'+sid);
    if(el) el.className='np-fields'+(src===k?' open':'');
  });
}
function _copyApiKey(){
  var val=document.getElementById('api-key-display').textContent;
  navigator.clipboard&&navigator.clipboard.writeText(val.trim()); _msg('Copied.',true);
}
function _regenApiKey(){
  if(!confirm('Regenerate? All integrations using the current key will break.')) return;
  _post('/api/brandscreen/regen_api_key',{}).then(function(r){return r.json();}).then(function(d){
    if(d.error){_msg(d.error,false);return;}
    document.getElementById('api-key-display').textContent=d.api_key;
    var eg=document.getElementById('eg-assign');
    eg.textContent=eg.textContent.replace(/Bearer \S+/,'Bearer '+d.api_key); _msg('Regenerated.',true);
  });
}

// ── CueServer strip management ────────────────────────────────────────────
var _csSceneCache={};   // studio_id → [{num,label}]

function _csStripRowHtml(studio_id, strip, idx){
  strip=strip||{};
  return '<div class="cs-strip-row" style="display:flex;align-items:center;gap:6px;margin-bottom:6px;flex-wrap:wrap">'
    +'<input class="cs-sr-name" type="text" value="'+_esc(strip.name||'Strip '+(idx+1))+'" placeholder="Strip name" style="width:90px;font-size:12px">'
    +'<span style="font-size:10px;color:var(--mu)">R</span>'
    +'<input class="cs-sr-r" type="number" min="0" max="512" value="'+(strip.ch_r||0)+'" style="width:54px;font-size:12px" title="Red DMX channel (0=off)">'
    +'<span style="font-size:10px;color:var(--mu)">G</span>'
    +'<input class="cs-sr-g" type="number" min="0" max="512" value="'+(strip.ch_g||0)+'" style="width:54px;font-size:12px" title="Green DMX channel (0=off)">'
    +'<span style="font-size:10px;color:var(--mu)">B</span>'
    +'<input class="cs-sr-b" type="number" min="0" max="512" value="'+(strip.ch_b||0)+'" style="width:54px;font-size:12px" title="Blue DMX channel (0=off)">'
    +'<span style="font-size:10px;color:var(--mu)">W</span>'
    +'<input class="cs-sr-w" type="number" min="0" max="512" value="'+(strip.ch_w||0)+'" style="width:54px;font-size:12px" title="White DMX channel (0=not fitted, RGBW when set)">'
    +'<button class="btn bd bs" data-action="cs-del-strip" data-sid="'+_esc(studio_id)+'">×</button>'
    +'</div>';
}

function _csGetStrips(studio_id){
  var container=document.getElementById('sd-cs-strips-'+studio_id);
  if(!container) return [];
  var strips=[];
  container.querySelectorAll('.cs-strip-row').forEach(function(row){
    strips.push({
      name: (row.querySelector('.cs-sr-name').value||'').trim()||'Strip',
      ch_r: parseInt(row.querySelector('.cs-sr-r').value,10)||0,
      ch_g: parseInt(row.querySelector('.cs-sr-g').value,10)||0,
      ch_b: parseInt(row.querySelector('.cs-sr-b').value,10)||0,
      ch_w: parseInt(row.querySelector('.cs-sr-w').value,10)||0,
    });
  });
  return strips;
}

function _csAddStrip(studio_id){
  var container=document.getElementById('sd-cs-strips-'+studio_id);
  if(!container) return;
  var idx=container.querySelectorAll('.cs-strip-row').length;
  // Auto-increment: first free channel block of 4
  var lastW=0;
  container.querySelectorAll('.cs-strip-row').forEach(function(row){
    var w=parseInt(row.querySelector('.cs-sr-w').value,10)||0;
    var b=parseInt(row.querySelector('.cs-sr-b').value,10)||0;
    lastW=Math.max(lastW,w,b);
  });
  var start=lastW?lastW+1:1;
  var newStrip={name:'Strip '+(idx+1),ch_r:start,ch_g:start+1,ch_b:start+2,ch_w:start+3};
  var div=document.createElement('div');
  div.innerHTML=_csStripRowHtml(studio_id,newStrip,idx);
  container.appendChild(div.firstChild);
}

function _csDelStrip(btn){
  var row=btn.closest('.cs-strip-row');
  if(row) row.remove();
}

// ── CueServer admin helpers ────────────────────────────────────────────────

function _csRenderScenes(scenes, containerId, onClickCue){
  var el=document.getElementById(containerId); if(!el) return;
  if(!scenes||!scenes.length){
    el.innerHTML='<p style="font-size:11px;color:var(--mu);margin:0">No cues found — the cue stack may be empty.</p>';
    el.style.display='block'; return;
  }
  var rows=scenes.map(function(c){
    return '<tr>'
      +'<td class="cue-num">'+_esc(c.num)+'</td>'
      +'<td>'+_esc(c.label)+'</td>'
      +(onClickCue?'<td><button class="cue-use" data-cue-num="'+_esc(c.num)+'">Use</button></td>':'')
      +'</tr>';
  }).join('');
  el.innerHTML='<table class="cs-scene-list">'
    +'<tr><th>Cue</th><th>Name</th>'+(onClickCue?'<th></th>':'')+'</tr>'
    +rows+'</table>';
  if(onClickCue){
    el.querySelectorAll('.cue-use').forEach(function(btn){
      btn.addEventListener('click',function(){onClickCue(btn.dataset.cueNum);});
    });
  }
  el.style.display='block';
}

function _csPollResult(studio_id, action, onSuccess, onError, _attempts){
  _attempts=_attempts||0;
  if(_attempts>15){if(onError) onError('Timeout — client did not respond in time'); return;}
  setTimeout(function(){
    fetch('/api/brandscreen/cueserver_result/'+studio_id,{credentials:'same-origin'})
      .then(function(r){return r.json();}).catch(function(){return{pending:true};})
      .then(function(d){
        if(d.pending){_csPollResult(studio_id,action,onSuccess,onError,_attempts+1); return;}
        if(d.error){if(onError) onError(d.error); return;}
        if(d.action!==action){_csPollResult(studio_id,action,onSuccess,onError,_attempts+1); return;}
        if(onSuccess) onSuccess(d.data);
      });
  },2000);
}

function _csFetchScenes(studio_id){
  var statusEl=document.getElementById('sd-cs-status-'+studio_id);
  var scenesEl=document.getElementById('sd-cs-scenes-'+studio_id);
  if(statusEl) statusEl.textContent='Fetching…';
  _post('/api/brandscreen/cueserver_action/'+studio_id,{action:'poll_scenes'})
    .then(function(r){return r.json();}).then(function(d){
      if(d.error){if(statusEl)statusEl.textContent='Error: '+d.error; return;}
      _csPollResult(studio_id,'poll_scenes',
        function(data){
          var cues=(data&&data.cues)||[];
          _csSceneCache[studio_id]=cues;
          _csRenderScenes(cues,'sd-cs-scenes-'+studio_id,null);
          if(statusEl)statusEl.textContent=cues.length+' cue'+(cues.length===1?'':'s')+' found';
        },
        function(err){if(statusEl)statusEl.textContent='Error: '+err;}
      );
    }).catch(function(){if(statusEl)statusEl.textContent='Request failed';});
}

function _csBri(station_id){
  var el=document.getElementById('f-csBri-'+station_id);
  return el?parseInt(el.value,10):100;
}

function _csPreview(station_id){
  var studio_id=_v('f-cs-studio-'+station_id)||'';
  if(!studio_id){_msg('Choose a studio to preview on',false); return;}
  var colour=_v('f-brand-'+station_id)||((_stById(station_id)||{}).brand_colour||'#17a8ff');
  var bri=_csBri(station_id);
  var statusEl=document.getElementById('f-cs-status-'+station_id);
  if(statusEl)statusEl.textContent='Sending…';
  _post('/api/brandscreen/cueserver_action/'+studio_id,{action:'preview_colour',colour:colour,brightness:bri})
    .then(function(r){return r.json();}).then(function(d){
      if(d.error){if(statusEl)statusEl.textContent='Error: '+d.error; return;}
      if(statusEl)statusEl.textContent='Done — LEDs updating';
      setTimeout(function(){if(statusEl)statusEl.textContent='';},4000);
    }).catch(function(){if(statusEl)statusEl.textContent='Request failed';});
}

// (No data-cs-st-sel handler needed — brand form studio dropdown is a plain select now)

document.addEventListener('click',function(e){
  var btn=e.target.closest('[data-action]'); if(!btn) return;
  var a=btn.dataset.action, sid=btn.dataset.sid;
  if(a==='toggle-sd'){ var el=document.getElementById('sdb-'+sid); if(el)el.classList.toggle('open'); }
  else if(a==='save-sd') _saveSd(sid);
  else if(a==='del-sd')  _delSd(sid);
  else if(a==='regen-sd-tok') _regenSdTok(sid);
  else if(a==='toggle-st'){ var el2=document.getElementById('stb-'+sid); if(el2)el2.classList.toggle('open'); }
  else if(a==='save-st') _saveSt(sid);
  else if(a==='del-st')  _delSt(sid);
  else if(a==='upload-logo') _uploadLogo(sid);
  else if(a==='del-logo')    _delLogo(sid);
  else if(a==='copy-api-key')  _copyApiKey();
  else if(a==='regen-api-key') _regenApiKey();
  else if(a==='cs-add-strip')    _csAddStrip(sid);
  else if(a==='cs-del-strip')    _csDelStrip(btn);
  else if(a==='cs-fetch-scenes') _csFetchScenes(sid);
  else if(a==='cs-preview')      _csPreview(sid);
  else if(a==='takeover-send'){
    var _toTitle=(_v('sd-to-title-'+sid)||'').trim();
    var _toText=(_v('sd-to-text-'+sid)||'').trim();
    _post('/api/brandscreen/studio/'+sid+'/takeover',{title:_toTitle,text:_toText})
      .then(function(r){return r.json();}).then(function(d){
        if(d.error){_msg(d.error,false);return;}
        _msg('Takeover sent.',true);
      });
  }
  else if(a==='takeover-clear'){
    _del('/api/brandscreen/studio/'+sid+'/takeover')
      .then(function(r){return r.json();}).then(function(d){
        if(d.error){_msg(d.error,false);return;}
        _msg('Takeover cleared.',true);
      });
  }
  var url=e.target.closest('.row-url');
  if(url){ navigator.clipboard&&navigator.clipboard.writeText(url.textContent.trim()); _msg('URL copied.',true); }
});
document.getElementById('add-studio-btn').addEventListener('click',_addStudio);
document.getElementById('add-station-btn').addEventListener('click',_addStation);
document.getElementById('logo-input').addEventListener('change',function(){ _doUpload(this.files[0]); });

// ── Onboarding panel ──────────────────────────────────────────────────────
// Shown when there are no stations AND no studios. Buttons wire to the same
// actions as the tab Add buttons, then switch to the relevant tab.
function _checkOnboard(){
  var panel=document.getElementById('onboard-panel');
  if(panel) panel.style.display=(_stations.length===0&&_studios.length===0)?'block':'none';
  // Update tab count chips
  var tcs=document.getElementById('tc-studios');
  var tct=document.getElementById('tc-stations');
  var tcsched=document.getElementById('tc-schedules');
  if(tcs) tcs.textContent=_studios.length?'  '+_studios.length:'';
  if(tct) tct.textContent=_stations.length?'  '+_stations.length:'';
  if(tcsched) tcsched.textContent=_schedules.length?'  '+_schedules.length:'';
}
(function(){
  var obs=document.getElementById('onboard-add-station');
  if(obs) obs.addEventListener('click',function(){
    _addStation();
    // switch to stations tab so the new form is visible
    var stBtn=document.querySelector('[data-tab="stations"]');
    if(stBtn) stBtn.click();
  });
  var osd=document.getElementById('onboard-add-studio');
  if(osd) osd.addEventListener('click',function(){
    _addStudio();
    var sdBtn=document.querySelector('[data-tab="studios"]');
    if(sdBtn) sdBtn.click();
  });
})();
document.addEventListener('change',function(e){
  if(e.target.dataset.npSel) _npSrcChanged(e.target.dataset.npSel);
  // Keep CueServer colour swatch in sync with brand colour picker
  if(e.target.id&&e.target.id.startsWith('f-brand-')){
    var _stId=e.target.id.replace('f-brand-','');
    var sw=document.getElementById('f-cs-swatch-'+_stId);
    if(sw) sw.style.background=e.target.value;
  }
});
// Live-update brightness % labels as range sliders move
document.addEventListener('input',function(e){
  // Per-brand brightness slider (on brand/station form)
  if(e.target.id&&e.target.id.startsWith('f-csBri-')){
    var _stId=e.target.id.replace('f-csBri-','');
    var lbl=document.getElementById('f-csBri-lbl-'+_stId);
    if(lbl) lbl.textContent=e.target.value+'%';
    // Also update the CueServer colour swatch preview opacity to hint at brightness
  }
});

// Fetch Zetta stations from status_full (same source as studioboard)
fetch('/api/zetta/status_full',{credentials:'same-origin'})
  .then(function(r){return r.ok?r.json():{};}).catch(function(){return{};})
  .then(function(d){
    _zetStations=[];
    ((d.instances)||[]).forEach(function(inst){
      Object.keys(inst.stations||{}).forEach(function(sid){
        var stn=inst.stations[sid];
        _zetStations.push({key:inst.id+':'+sid,
          name:(inst.name||inst.id)+' / '+(stn.station_name||stn.name||sid)});
      });
    });
    _zetStations.sort(function(a,b){return a.name<b.name?-1:1;});
    renderStudios(); renderStations(); renderApiRef();
  });

// ── Schedules ────────────────────────────────────────────────────────────────
var _schedules = {{schedules_json|safe}};
var _schedActive = {};

function _schedDayLabel(sc){
  if(sc.date){
    var d=new Date(sc.date+'T00:00:00');
    var days=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
    var months=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    return days[d.getDay()]+' '+d.getDate()+' '+months[d.getMonth()]+' '+d.getFullYear();
  }
  var days=sc.days||[];
  if(!days.length) return 'Every day';
  var names={mon:'Mon',tue:'Tue',wed:'Wed',thu:'Thu',fri:'Fri',sat:'Sat',sun:'Sun'};
  return days.map(function(d){return names[d]||d;}).join(', ');
}
function _schedIsPast(sc){
  if(!sc.date) return false;
  var today=new Date(); today.setHours(0,0,0,0);
  var d=new Date(sc.date+'T00:00:00'); d.setHours(0,0,0,0);
  return d<today;
}

function renderSchedules(){
  var el=document.getElementById('sched-list');
  if(!el) return;
  if(!_schedules.length){el.innerHTML='<div style="color:var(--mu);font-size:13px;padding:8px 0">No schedules yet.</div>';return;}
  var studioMap={};_studios.forEach(function(s){studioMap[s.id]=s.name||s.id;});
  var stationMap={};_stations.forEach(function(s){stationMap[s.id]=s.name||s.id;});
  var html='';
  _schedules.forEach(function(sc){
    var isActive=!!_schedActive[sc.studio_id]&&_schedActive[sc.studio_id].active_schedule_id===sc.id;
    var restOvr=isActive&&_schedActive[sc.studio_id].rest_override;
    var isOneOff=!!(sc.date);
    var isPast=_schedIsPast(sc);
    var badges='';
    if(isActive) badges+='<span class="sched-badge">'+(restOvr?'REST OVERRIDE':'ACTIVE')+'</span>';
    if(isOneOff&&isPast) badges+='<span class="sched-badge past">PAST</span>';
    else if(isOneOff) badges+='<span class="sched-badge oneoff">ONE-OFF</span>';
    html+='<div class="sched-row'+(isActive?' active-sched':'')+'" data-sched-id="'+sc.id+'">'
      +'<div class="sched-info">'
      +'<div class="sched-name">'+_esc(sc.name||'Unnamed')+badges+'</div>'
      +'<div class="sched-detail">Studio: <strong>'+_esc(studioMap[sc.studio_id]||sc.studio_id)+'</strong>'
      +' → Brand: <strong>'+_esc(stationMap[sc.station_id]||sc.station_id)+'</strong></div>'
      +'<div class="sched-detail">'+_schedDayLabel(sc)+' &nbsp;·&nbsp; '
      +_esc(sc.start_time||'?')+' – '+_esc(sc.end_time||'?')+'</div>'
      +'</div>'
      +'<label class="tog" title="Enable/disable"><input type="checkbox" class="sched-tog"'+(sc.enabled?' checked':'')+' data-sid="'+sc.id+'"><span class="tog-sl"></span></label>'
      +'<button class="btn bd bs sched-del-btn" data-sid="'+sc.id+'">Delete</button>'
      +'</div>';
  });
  el.innerHTML=html;
  // Toggle enable
  el.querySelectorAll('.sched-tog').forEach(function(cb){
    cb.addEventListener('change',function(){
      var sid=this.dataset.sid;
      _patch('/api/brandscreen/schedule/'+sid,{enabled:this.checked})
        .then(function(r){return r.json();}).then(function(d){
          if(!d.ok){_msg('Save failed',false);return;}
          var sc=_schedules.find(function(s){return s.id===sid;});
          if(sc) sc.enabled=cb.checked;
        });
    });
  });
  // Delete
  el.querySelectorAll('.sched-del-btn').forEach(function(btn){
    btn.addEventListener('click',function(){
      var sid=this.dataset.sid;
      if(!confirm('Delete this schedule?')) return;
      _del('/api/brandscreen/schedule/'+sid)
        .then(function(r){return r.json();}).then(function(d){
          if(!d.ok){_msg('Delete failed',false);return;}
          _schedules=_schedules.filter(function(s){return s.id!==sid;});
          renderSchedules();
          _updateTabCounts();
        });
    });
  });
}

// Add-schedule form toggle
(function(){
  var tog=document.getElementById('add-sched-toggle');
  var body=document.getElementById('add-sched-body');
  var arrow=document.getElementById('add-sched-arrow');
  if(tog&&body) tog.addEventListener('click',function(){
    var open=body.style.display!=='none';
    body.style.display=open?'none':'block';
    if(arrow) arrow.textContent=open?'▼':'▲';
  });
})();

// Populate studio/station dropdowns when tab opens
function _populateSchedSelects(){
  var ss=document.getElementById('sf-studio');
  var st=document.getElementById('sf-station');
  if(!ss||!st) return;
  ss.innerHTML='<option value="">— select studio —</option>';
  _studios.forEach(function(s){ss.innerHTML+='<option value="'+s.id+'">'+_esc(s.name||s.id)+'</option>';});
  st.innerHTML='<option value="">— select brand —</option>';
  _stations.forEach(function(s){st.innerHTML+='<option value="'+s.id+'">'+_esc(s.name||s.id)+'</option>';});
}

// Schedule type toggle (Recurring / One-off)
(function(){
  var typeBtns=document.getElementById('sf-type-btns');
  var recSec=document.getElementById('sf-recurring-section');
  var oofSec=document.getElementById('sf-oneoff-section');
  var dateInp=document.getElementById('sf-date');
  // Default date to today
  var _today=new Date();
  dateInp.value=_today.getFullYear()+'-'+String(_today.getMonth()+1).padStart(2,'0')+'-'+String(_today.getDate()).padStart(2,'0');
  typeBtns.addEventListener('click',function(e){
    var btn=e.target.closest('.stype-btn');
    if(!btn) return;
    typeBtns.querySelectorAll('.stype-btn').forEach(function(b){b.classList.remove('sel');});
    btn.classList.add('sel');
    var isOneOff=btn.dataset.type==='oneoff';
    recSec.style.display=isOneOff?'none':'block';
    oofSec.style.display=isOneOff?'block':'none';
  });
})();

// Day button toggle
document.getElementById('sf-days').addEventListener('click',function(e){
  var btn=e.target.closest('.day-btn');
  if(btn) btn.classList.toggle('sel');
});

// Load active schedule state
function _loadSchedActive(){
  fetch('/api/brandscreen/schedules',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      _schedActive=d.active||{};
      renderSchedules();
    }).catch(function(){});
}
_loadSchedActive();
// Refresh active state every 60 s
setInterval(_loadSchedActive, 60000);

// Save new schedule
document.getElementById('sf-save-btn').addEventListener('click',function(){
  var name=document.getElementById('sf-name').value.trim();
  var studio=document.getElementById('sf-studio').value;
  var station=document.getElementById('sf-station').value;
  var start=document.getElementById('sf-start').value;
  var end=document.getElementById('sf-end').value;
  var selType=document.querySelector('#sf-type-btns .stype-btn.sel');
  var isOneOff=selType&&selType.dataset.type==='oneoff';
  var days=isOneOff?[]:
    [].slice.call(document.querySelectorAll('#sf-days .day-btn.sel')).map(function(b){return b.dataset.day;});
  var date=isOneOff?(document.getElementById('sf-date').value||''):'';
  if(!name){_msg('Enter a schedule name.',false);return;}
  if(!studio){_msg('Select a studio.',false);return;}
  if(!station){_msg('Select a brand.',false);return;}
  if(isOneOff&&!date){_msg('Select a date.',false);return;}
  if(!start||!end){_msg('Set start and end times.',false);return;}
  if(start>=end){_msg('End time must be after start time.',false);return;}
  _post('/api/brandscreen/schedule',{name:name,studio_id:studio,station_id:station,days:days,date:date,start_time:start,end_time:end})
    .then(function(r){return r.json();}).then(function(d){
      if(!d.ok){_msg(d.error||'Save failed',false);return;}
      _schedules.push(d.schedule);
      renderSchedules();
      _updateTabCounts();
      // Reset form
      document.getElementById('sf-name').value='';
      document.getElementById('sf-days').querySelectorAll('.day-btn.sel').forEach(function(b){b.classList.remove('sel');});
      document.getElementById('sf-start').value='09:00';
      document.getElementById('sf-end').value='17:00';
      // Reset type to Recurring
      document.querySelectorAll('#sf-type-btns .stype-btn').forEach(function(b){b.classList.remove('sel');});
      var firstType=document.querySelector('#sf-type-btns .stype-btn');
      if(firstType) firstType.classList.add('sel');
      document.getElementById('sf-recurring-section').style.display='block';
      document.getElementById('sf-oneoff-section').style.display='none';
      document.getElementById('add-sched-body').style.display='none';
      var arrow=document.getElementById('add-sched-arrow');
      if(arrow) arrow.textContent='▼';
      _msg('Schedule saved.',true);
    });
});

// Fetch Studio Board studios for mic-live linking
fetch('/api/studioboard/data',{credentials:'same-origin'})
  .then(function(r){return r.ok?r.json():{};}).catch(function(){return{};})
  .then(function(d){
    _sbStudios=(d.studios||[]).map(function(s){return{id:s.id,name:s.name||s.id};});
    _sbStudios.sort(function(a,b){return a.name<b.name?-1:1;});
    renderStudios();
  });

// Fetch connected hub sites for CueServer client-site dropdown
var _hubSites=[];
fetch('/api/brandscreen/hub_sites',{credentials:'same-origin'})
  .then(function(r){return r.ok?r.json():{};}).catch(function(){return{};})
  .then(function(d){
    _hubSites=(d.sites||[]);
    renderStudios();
  });
</script>
</body>
</html>"""

# ──────────────────────────────────────────────── screen template ─────────────

_SCREEN_TPL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{sname|e}}</title>
<style nonce="{{csp_nonce()}}">
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:100%;height:100%;overflow:hidden;font-family:system-ui,sans-serif;color:#fff}

/* ── Brand-derived base colour ──────────────────────────────────────────── */
/* bg_dark is the body fill — corners of every preset show brand colour,
   not black.  bg_mid is the brighter centre; bg_deep is used only for the
   very inner shadow layer of the vignette. */
body{background:{{bg_dark}}}
:root{
  --brand:{{brand|e}};--brand-rgb:{{brand_rgb|e}};--accent:{{accent|e}};
  --bg-deep:{{bg_deep|e}};--bg-dark:{{bg_dark|e}};--bg-mid:{{bg_mid|e}};
}

/* ── Backgrounds ─────────────────────────────────────────────────────────── */
canvas#cv{position:fixed;inset:0;width:100%;height:100%;z-index:0;display:none}

/* Particles: large ellipse fills the full viewport — whole screen is the brand
   colour.  Center glows with bg_mid; edges settle on bg_dark (= body). */
.bg-particles-base{position:fixed;inset:0;z-index:0;
  background:radial-gradient(ellipse 110% 105% at 50% 42%,var(--bg-mid) 0%,var(--bg-dark) 62%)}

/* Aurora: full-hue base + vivid radial blooms covering entire screen */
/* filter:hue-rotate()+brightness() animation removed — animating filter on a
   full-screen fixed element forces a per-frame GPU filter pass on Raspberry
   Pi / Yodeck, causing the screen to flash. Replaced with an opacity
   oscillation on ::before (compositor-only, zero repaint cost). */
.bg-aurora{position:fixed;inset:0;z-index:0;background:var(--bg-dark)}
.bg-aurora::before{content:'';position:fixed;inset:0;z-index:0;
  background:
    radial-gradient(ellipse 85% 75% at 18% 22%,rgba(var(--brand-rgb),.70) 0%,transparent 62%),
    radial-gradient(ellipse 75% 68% at 82% 78%,rgba(var(--brand-rgb),.60) 0%,transparent 62%),
    radial-gradient(ellipse 100% 85% at 50% 50%,rgba(var(--brand-rgb),.35) 0%,transparent 72%);
  animation:aurora-breathe 16s ease-in-out infinite alternate}
@keyframes aurora-breathe{0%{opacity:.82}100%{opacity:1}}

/* Waves: top-to-bottom brand gradient — the whole screen is the brand colour */
.bg-waves{position:fixed;inset:0;z-index:0;
  background:linear-gradient(180deg,var(--bg-mid) 0%,var(--bg-dark) 100%)}
.wave-wrap{position:fixed;bottom:0;left:0;width:100%;overflow:hidden;z-index:1;pointer-events:none}
.wave-wrap svg{display:block;width:200%}
.wave-wrap svg.w1{animation:wave-slide 9s  linear infinite}
.wave-wrap svg.w2{animation:wave-slide 13s linear infinite reverse;opacity:.65}
@keyframes wave-slide{0%{transform:translateX(0)}100%{transform:translateX(-50%)}}

/* Minimal: oversized ellipse so bg_mid fills the screen; bg_dark at edges */
.bg-minimal{position:fixed;inset:0;z-index:0;
  background:radial-gradient(ellipse 110% 105% at 50% 40%,var(--bg-mid) 0%,var(--bg-dark) 62%)}

/* Beams: sweeping concert spotlight columns rising from the floor */
/* overflow:hidden removed — on Raspberry Pi / Yodeck, overflow:hidden on a
   parent of will-change:transform children creates a clipping stacking context
   that Pi's compositor can fail to render, producing a black rectangle for one
   frame. The viewport already clips beams that extend outside it. */
.bg-beams{position:fixed;inset:0;z-index:0;background:var(--bg-dark)}
.bg-beams::after{content:'';position:absolute;bottom:0;left:0;right:0;height:44%;
  background:radial-gradient(ellipse 70% 100% at 50% 100%,rgba(var(--brand-rgb),.32) 0%,transparent 68%);pointer-events:none}
/* filter:blur() removed from beams — each blurred beam is promoted to its own
   GPU compositor texture (blur kernel adds ~130px padding on all sides). Four
   beams × ~1.4 MB each = ~5.6 MB of GPU memory, causing texture eviction and
   the black-flash on Pi. Beam is made 9vw wide (was 6vw) and gradient softened
   to visually approximate the blurred look without the filter. */
.beam{position:absolute;bottom:-5%;width:9vw;height:115vh;transform-origin:bottom center;
  border-radius:50% 50% 0 0 / 3% 3% 0 0;
  background:linear-gradient(to top,rgba(var(--brand-rgb),.38) 0%,rgba(var(--brand-rgb),.09) 55%,transparent 84%);
  will-change:opacity,transform}
.b1{left:13%;animation:bsw1 22s ease-in-out infinite}
.b2{left:34%;animation:bsw2 28s ease-in-out infinite}
.b3{left:58%;animation:bsw3 24s ease-in-out infinite}
.b4{left:80%;animation:bsw4 32s ease-in-out infinite}
@keyframes bsw1{0%,100%{opacity:.07;transform:rotate(-34deg)}50%{opacity:.28;transform:rotate(-17deg)}}
@keyframes bsw2{0%,100%{opacity:.20;transform:rotate(-8deg)}50%{opacity:.40;transform:rotate(10deg)}}
@keyframes bsw3{0%,100%{opacity:.16;transform:rotate(15deg)}50%{opacity:.09;transform:rotate(-5deg)}}
@keyframes bsw4{0%,100%{opacity:.07;transform:rotate(36deg)}50%{opacity:.24;transform:rotate(21deg)}}

/* Grid: synthwave perspective scrolling broadcast grid */
/* overflow:hidden removed for the same Pi compositor reason as bg-beams/burst —
   clipping a will-change:transform child creates a stacking context Pi can fail to render. */
.bg-grid{position:fixed;inset:0;z-index:0;background:var(--bg-dark)}
.bg-grid::before{content:'';position:absolute;left:50%;top:35%;transform:translate(-50%,-50%);
  width:52vw;height:12vw;border-radius:50%;
  background:radial-gradient(circle,rgba(var(--brand-rgb),.42) 0%,transparent 68%);
  filter:blur(30px);pointer-events:none}
/* Grid scroll: background-position animation replaced with transform:translateY on a
   ::before pseudo-element — translateY is GPU-composited (no per-frame repaint);
   background-position was not compositable and caused a full repaint every frame on Pi/Yodeck. */
.bg-grid-plane{position:absolute;width:220%;left:-60%;top:32%;bottom:0;
  transform-origin:50% 100%;transform:perspective(580px) rotateX(64deg)}
.bg-grid-plane::before{content:'';position:absolute;top:-80px;left:0;right:0;bottom:0;
  background-image:linear-gradient(rgba(var(--brand-rgb),.32) 1px,transparent 1px),
    linear-gradient(90deg,rgba(var(--brand-rgb),.32) 1px,transparent 1px);
  background-size:80px 80px;animation:grid-scroll 3.0s linear infinite;will-change:transform}
.bg-grid-fade{position:absolute;inset:0;pointer-events:none;
  background:linear-gradient(to bottom,var(--bg-dark) 0%,transparent 26%,transparent 50%,var(--bg-dark) 100%)}
@keyframes grid-scroll{0%{transform:translateY(0)}100%{transform:translateY(80px)}}

/* Burst: slowly rotating sunray starburst — bold, energetic */
/* overflow:hidden removed for the same Pi compositor reason as bg-beams */
.bg-burst{position:fixed;inset:0;z-index:0;background:var(--bg-dark);
  display:flex;align-items:center;justify-content:center}
.burst-rays{position:absolute;width:210vmax;height:210vmax;
  background:repeating-conic-gradient(rgba(var(--brand-rgb),.11) 0deg 8deg,transparent 8deg 30deg);
  animation:burst-spin 70s linear infinite;will-change:transform}
.burst-core{position:absolute;width:58vmin;height:58vmin;border-radius:50%;
  background:radial-gradient(circle,rgba(var(--brand-rgb),.44) 0%,rgba(var(--brand-rgb),.08) 52%,transparent 70%);
  filter:blur(24px)}
@keyframes burst-spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}

/* Haze: drifting atmospheric blobs — broadcast studio mood lighting */
.bg-haze{position:fixed;inset:0;z-index:0;background:var(--bg-dark)}
.haze-blob{position:absolute;border-radius:50%;filter:blur(88px)}
.hz1{width:80vw;height:80vw;top:-18%;left:-18%;
  background:rgba(var(--brand-rgb),.30);animation:hz1 22s ease-in-out infinite alternate}
.hz2{width:65vw;height:65vw;bottom:-14%;right:-12%;
  background:rgba(var(--brand-rgb),.24);animation:hz2 30s ease-in-out infinite alternate;animation-delay:-10s}
.hz3{width:52vw;height:52vw;top:22%;left:24%;
  background:rgba(var(--brand-rgb),.17);animation:hz3 38s ease-in-out infinite alternate;animation-delay:-18s}
@keyframes hz1{0%{transform:translate(0,0) scale(1)}100%{transform:translate(14vw,9vh) scale(1.22)}}
@keyframes hz2{0%{transform:translate(0,0) scale(1)}100%{transform:translate(-11vw,-8vh) scale(1.16)}}
@keyframes hz3{0%{transform:translate(0,0) scale(1)}50%{transform:translate(9vw,-12vh) scale(0.88)}100%{transform:translate(-7vw,7vh) scale(1.12)}}

/* Glitch: digital broadcast interference — CSS transform-only (no filter, so JS glow still applies) */
.la-glitch #logo-img{animation:lo-glitch 6s steps(1) infinite}
@keyframes lo-glitch{
  0%,86%,100%{transform:translate(0,0) skewX(0deg)}
  87%{transform:translate(-6px,0) skewX(-5deg)}
  88%{transform:translate(5px,-2px) skewX(3deg)}
  89%{transform:translate(-3px,2px)}
  90%,92%{transform:translate(0,0)}
  91%,93%{transform:translate(-4px,0) skewX(-2deg)}
  94%{transform:translate(4px,0) skewX(2deg)}
  95%{transform:translate(0,-3px)}
  96%{transform:translate(-2px,0)}
  97%{transform:translate(3px,2px) skewX(1deg)}
  98%{transform:translate(0,0) skewX(-0.5deg)}}

/* ── Screen layout ───────────────────────────────────────────────────────── */
#screen{position:relative;z-index:2;width:100%;height:100vh;display:flex;flex-direction:column;
  animation:px-drift 90s step-end infinite;transition:opacity .55s ease}
@keyframes px-drift{0%,100%{transform:translate(0,0)}25%{transform:translate(1px,0)}50%{transform:translate(1px,1px)}75%{transform:translate(0,1px)}}
#screen.fade-out{opacity:0}

/* ── Top bar ─────────────────────────────────────────────────────────────── */
#top-bar{display:flex;align-items:flex-start;justify-content:space-between;padding:22px 32px 0;flex-shrink:0}
#on-air{display:none;align-items:center;gap:8px;
  background:rgba(239,68,68,.14);border:1.5px solid rgba(239,68,68,.5);border-radius:8px;
  padding:6px 18px;font-size:12px;font-weight:800;letter-spacing:.18em;text-transform:uppercase;color:#ef4444}
#on-air.vis{display:flex}
.oa-dot{width:8px;height:8px;border-radius:50%;background:#ef4444;animation:oab 1.2s ease-in-out infinite}
@keyframes oab{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.22;transform:scale(.65)}}
#clock-wrap{text-align:right;display:none}
#clock-wrap.vis{display:block}
#clock-time{font-size:30px;font-weight:200;letter-spacing:.08em;font-variant-numeric:tabular-nums;color:rgba(255,255,255,.92)}
#clock-date{font-size:11px;color:rgba(255,255,255,.35);letter-spacing:.07em;text-transform:uppercase;margin-top:2px}

/* ── Centre / logo zone ──────────────────────────────────────────────────── */
#centre{flex:1;display:flex;align-items:center;justify-content:center;position:relative;overflow:visible}

/* Audio-reactive centre bloom — always present, driven by JS */
/* filter:blur(32px) removed — at 80vw width (1536px on 1920p) the GPU texture
   needed to render this element with blur is (1536+192)²≈11.9 MB. When the Pi
   runs out of GPU memory, this layer is evicted and renders as a black circle.
   Gradient extended to 78% transparent (was 68%) to keep a soft fade without
   the blur. The JS RAF loop drives opacity/scale so the glow still pulses. */
#lev-bloom{position:absolute;width:80vw;height:80vw;border-radius:50%;pointer-events:none;z-index:3;
  background:radial-gradient(circle,rgba(var(--brand-rgb),.82) 0%,rgba(var(--brand-rgb),.50) 22%,rgba(var(--brand-rgb),.14) 55%,transparent 78%);
  transform:scale(0.05);opacity:0.3;will-change:transform,opacity}
/* Full-screen level-reactive background brightening */
#bg-pulse{position:fixed;inset:0;z-index:1;pointer-events:none;
  background:radial-gradient(ellipse 90% 80% at 50% 48%,rgba(var(--brand-rgb),.18) 0%,transparent 72%);
  opacity:0;will-change:opacity}

.logo-zone{position:relative;display:flex;align-items:center;justify-content:center;z-index:10}
/* Logo fills the stage — large is the point */
#logo-img{
  width:68vw;max-width:1100px;
  max-height:52vh;
  object-fit:contain;
  display:block;
  filter:drop-shadow(0 0 40px rgba(var(--brand-rgb),.25));
  will-change:transform,filter}
#logo-ph{font-size:clamp(60px,12vw,160px);opacity:.1;z-index:10}

/* ── Orbit rings — scaled in vw so they wrap the large logo ─────────────── */
.orbit-wrap{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;pointer-events:none;z-index:6;overflow:visible}
.orb{position:absolute;border-radius:50%;border:1.5px solid var(--brand)}
/* Ring 1: outer — solid, clockwise */
.orb1{width:84vw;height:34vw;opacity:.52;animation:orb-s1 10s linear infinite}
/* box-shadow removed from orbit dots — on Raspberry Pi / Yodeck, box-shadow on a
   ::before pseudo-element inside a rotating will-change:transform layer cannot be
   compositor-only: the layer must be re-rasterised every frame to paint the shadow,
   causing a visible screen flash. Replaced with a radial-gradient dot that is baked
   into the rasterised texture — zero per-frame cost. Dot is wider to compensate. */
.orb1::before{content:'';position:absolute;width:clamp(14px,2vw,28px);height:clamp(14px,2vw,28px);
  border-radius:50%;
  background:radial-gradient(circle,rgba(255,255,255,.96) 0%,var(--brand) 38%,transparent 70%);
  top:calc(-1 * clamp(7px,1vw,14px));left:calc(50% - clamp(7px,1vw,14px))}
/* Ring 2: inner — dashed, counter-clockwise */
.orb2{width:63vw;height:26vw;opacity:.3;border-style:dashed;animation:orb-s2 16s linear infinite reverse}
.orb2::before{content:'';position:absolute;width:clamp(10px,1.5vw,22px);height:clamp(10px,1.5vw,22px);
  border-radius:50%;
  background:radial-gradient(circle,rgba(255,255,255,.96) 0%,var(--brand) 38%,transparent 70%);
  bottom:calc(-1 * clamp(5px,.75vw,11px));left:calc(50% - clamp(5px,.75vw,11px))}
@keyframes orb-s1{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}
@keyframes orb-s2{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}

/* ── Pulse rings ─────────────────────────────────────────────────────────── */
.pulse-wrap{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;pointer-events:none;z-index:6;overflow:visible}
.prng{position:absolute;width:48vw;height:48vw;border-radius:50%;
  border:2px solid var(--brand);opacity:0;animation:pulse-out 3.2s ease-out infinite}
.prng:nth-child(2){animation-delay:1.07s}.prng:nth-child(3){animation-delay:2.13s}
@keyframes pulse-out{0%{transform:scale(.45);opacity:.8}100%{transform:scale(1.8);opacity:0}}

/* ── Logo animations ─────────────────────────────────────────────────────── */
.la-float .logo-zone{animation:lo-float 4s ease-in-out infinite}
@keyframes lo-float{0%,100%{transform:translateY(0)}50%{transform:translateY(-18px)}}
/* Glow: base drop-shadow always present via logo-img filter; JS intensifies it via --glow-size */
/* No transition on logo-img — RAF loop at 60fps provides smoothing natively */

/* ── Audio-reactive overlays (all presets) ───────────────────────────────── */
/* Vignette: very subtle dark edge that lifts on beats. Base opacity is low
   so it adds depth without crushing the brand colour at the screen edges. */
#vignette{position:fixed;inset:0;z-index:4;pointer-events:none;
  background:radial-gradient(ellipse 72% 66% at 50% 46%,transparent 25%,rgba(0,0,0,.65) 100%);
  opacity:.18;will-change:opacity}
/* Beat flash: brand-colour radial wash that fires above 0.45 threshold */
/* No CSS transition — RAF loop at 60fps provides smooth interpolation natively */
#beat-flash{position:fixed;inset:0;z-index:5;pointer-events:none;
  background:radial-gradient(ellipse 90% 80% at 50% 47%,rgba(var(--brand-rgb),.42) 0%,transparent 68%);
  opacity:0;will-change:opacity}
/* Inner bloom core: sharp punchy "lamp" at the logo centre — sits above the
   large soft bloom and gives a distinct bright source that pulses with beats */
/* filter:blur(4px) removed — even a small blur forces a larger GPU texture
   allocation; at 16vw the saving is modest but every MB counts on Pi. */
#lev-bloom-core{position:absolute;width:16vw;height:16vw;border-radius:50%;pointer-events:none;z-index:4;
  background:radial-gradient(circle,rgba(var(--brand-rgb),1) 0%,rgba(var(--brand-rgb),.42) 40%,transparent 72%);
  transform:scale(0.06);opacity:0;will-change:transform,opacity}

/* ── Now-playing lower third ─────────────────────────────────────────────── */
#lower{flex-shrink:0;padding:0 48px 30px;display:none;flex-direction:column;align-items:center;text-align:center}
#lower.vis{display:flex}
#np-line{width:56px;height:1.5px;background:linear-gradient(90deg,transparent,var(--brand),transparent);margin-bottom:10px}
#np-label{font-size:9px;font-weight:700;letter-spacing:.22em;color:var(--brand);text-transform:uppercase;margin-bottom:10px}
#np-title{font-size:22px;font-weight:300;letter-spacing:.02em;color:rgba(255,255,255,.95);margin-bottom:5px;max-width:82vw;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#np-artist{font-size:14px;color:rgba(255,255,255,.42);max-width:60vw;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#np-spot{display:none;margin-top:10px;padding:3px 16px;border-radius:20px;background:rgba(245,158,11,.13);color:#f59e0b;border:1px solid rgba(245,158,11,.35);font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase}
#np-spot.vis{display:inline-block}

/* ── Message banner ──────────────────────────────────────────────────────── */
#msg-bar{flex-shrink:0;display:none;align-items:center;justify-content:center;gap:10px;background:rgba(245,158,11,.92);padding:11px 28px;font-size:15px;font-weight:600;color:#1a0900;animation:msg-flash 2.5s ease-in-out infinite}
#msg-bar.vis{display:flex}
@keyframes msg-flash{0%,100%{opacity:1}50%{opacity:.8}}

/* ── Waiting (no station) ────────────────────────────────────────────────── */
#waiting{position:fixed;inset:0;display:none;align-items:center;justify-content:center;flex-direction:column;gap:16px;color:rgba(255,255,255,.3);z-index:20}
#waiting.vis{display:flex}

/* ── Full-screen logo mode ───────────────────────────────────────────────── */
/* Covers all backgrounds and reactive overlays (z-0 to z-10). Sits below the
   takeover (z-50) so breakings/messages still appear on top.
   Anti-burn-in drift is applied via the same px-drift keyframe as #screen. */
#fslogo-wrap{position:fixed;inset:0;z-index:25;display:flex;align-items:center;justify-content:center;
  background:var(--bg-dark);animation:px-drift 90s step-end infinite}
#fslogo-img{width:100vw;height:100vh;object-fit:contain;display:block}

/* ── Full-screen takeover overlay ────────────────────────────────────────── */
/* Sits above everything (z-index:50). Background uses brand-derived deep/dark
   colours so the palette is preserved. Title is brand-coloured at TV-legible
   size; body text is bright white. No filter:blur on any element — Pi safe. */
#takeover{position:fixed;inset:0;z-index:50;display:flex;flex-direction:column;
  align-items:center;justify-content:center;gap:clamp(16px,3.5vh,52px);
  background:linear-gradient(160deg,var(--bg-deep) 0%,var(--bg-dark) 100%);
  padding:6vw;text-align:center;
  opacity:0;pointer-events:none;transition:opacity .6s ease}
#takeover.vis{opacity:1;pointer-events:auto}
#takeover-title{font-size:clamp(48px,9vw,160px);font-weight:700;
  color:var(--brand);letter-spacing:-.025em;line-height:1.05;
  text-shadow:0 0 80px rgba(var(--brand-rgb),.55),0 0 180px rgba(var(--brand-rgb),.22)}
#takeover-text{font-size:clamp(22px,4vw,72px);font-weight:300;
  color:rgba(255,255,255,.92);letter-spacing:.01em;line-height:1.3;
  max-width:82vw}

</style>
</head>

<body class="la-{{logo_anim|e}}">
{% if bg_style == 'particles' %}
<div class="bg-particles-base"></div>
<canvas id="cv"></canvas>
{% elif bg_style == 'aurora' %}
<div class="bg-aurora"></div>
{% elif bg_style == 'waves' %}
<div class="bg-waves"></div>
<div class="wave-wrap">
  <svg class="w1" viewBox="0 0 1440 110" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="none">
    <path d="M0,55 C240,95 480,15 720,55 C960,95 1200,15 1440,55 L1440,110 L0,110Z" fill="rgba({{brand_rgb}},0.38)"/>
    <path d="M0,70 C360,30 720,90 1080,55 C1260,38 1380,65 1440,60 L1440,110 L0,110Z" fill="rgba({{brand_rgb}},0.22)"/>
  </svg>
  <svg class="w2" viewBox="0 0 1440 80" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="none">
    <path d="M0,40 C180,72 540,10 720,40 C900,70 1260,10 1440,40 L1440,80 L0,80Z" fill="rgba({{brand_rgb}},0.28)"/>
  </svg>
</div>
{% elif bg_style == 'beams' %}
<div class="bg-beams">
  <div class="beam b1"></div><div class="beam b2"></div>
  <div class="beam b3"></div><div class="beam b4"></div>
</div>
{% elif bg_style == 'grid' %}
<div class="bg-grid">
  <div class="bg-grid-plane"></div>
  <div class="bg-grid-fade"></div>
</div>
{% elif bg_style == 'burst' %}
<div class="bg-burst">
  <div class="burst-rays"></div>
  <div class="burst-core"></div>
</div>
{% elif bg_style == 'haze' %}
<div class="bg-haze">
  <div class="haze-blob hz1"></div>
  <div class="haze-blob hz2"></div>
  <div class="haze-blob hz3"></div>
</div>
{% else %}
<div class="bg-minimal"></div>
{% endif %}

{% if full_screen_logo and has_logo and station_id %}
<div id="fslogo-wrap">
  <img id="fslogo-img" src="/api/brandscreen/logo/{{station_id|e}}" alt="{{sname|e}}">
</div>
{% endif %}

<div id="screen">
  <div id="top-bar">
    <div id="on-air"><div class="oa-dot"></div>ON AIR</div>
    <div id="clock-wrap"><div id="clock-time">--:--:--</div><div id="clock-date"></div></div>
  </div>

  <div id="bg-pulse"></div>
  <div id="vignette"></div>
  <div id="beat-flash"></div>
  <div id="centre">
    <div id="lev-bloom"></div>
    <div id="lev-bloom-core"></div>
    {% if logo_anim == 'orbit' %}
    <div class="orbit-wrap"><div class="orb orb1"></div><div class="orb orb2"></div></div>
    {% elif logo_anim == 'pulse' %}
    <div class="pulse-wrap"><div class="prng"></div><div class="prng"></div><div class="prng"></div></div>
    {% endif %}
    <div class="logo-zone">
      {% if has_logo %}<img id="logo-img" src="/api/brandscreen/logo/{{station_id|e}}" alt="{{sname|e}}">
      {% elif station_id %}<div id="logo-ph">📺</div>
      {% endif %}
    </div>
  </div>

  <div id="lower">
    <div id="np-line"></div>
    <div id="np-label">Now Playing</div>
    <div id="np-title">—</div>
    <div id="np-artist"></div>
    <div id="np-spot">Ad Break</div>
  </div>
  <div id="msg-bar"><span>📢</span><span id="msg-txt"></span></div>
</div>
<div id="waiting">
  <div style="font-size:40px">🖥️</div>
  <div style="font-size:16px">{{studio_name|e}}</div>
  <div style="font-size:13px">Waiting for station assignment…</div>
</div>
<div id="takeover">
  <div id="takeover-title"></div>
  <div id="takeover-text"></div>
</div>

<script nonce="{{csp_nonce()}}">
var _studioId   = '{{studio_id|e}}';
var _stationId  = '{{station_id|e}}';
var _bgStyle    = '{{bg_style|e}}';
var _brandRgb   = '{{brand_rgb|e}}';
var _logoAnim   = '{{logo_anim|e}}';
var _showClock  = {{show_clock|lower}};
var _showOair   = {{show_on_air|lower}};
var _showNP     = {{show_now_playing|lower}};
var _levelKey   = '{{level_key|e}}';   // "site|stream" or ""
// Full-screen logo mode — when true, all backgrounds/animations/NP are suppressed.
// Takeovers still work.  SSE reload still works.
var _fsLogo     = {{full_screen_logo|lower}};
// Kiosk token: embedded server-side so JS sub-requests can pass it as
// a query param (?token=...) without relying on session cookies.
// Mirrors the wb_token pattern used by wallboard.py for Yodeck compat.
var _kioskToken = '{{kiosk_token|e}}';
var _hasStation = !!_stationId;
// Helper — append ?token= to a URL when running in kiosk mode
function _tk(url){ return _kioskToken ? (url + (url.indexOf('?')>=0?'&':'?') + 'token=' + encodeURIComponent(_kioskToken)) : url; }

if(!_hasStation){ document.getElementById('waiting').classList.add('vis'); }

// ── Clock ───────────────────────────────────────────────────────────────────
if(_showClock && _hasStation && !_fsLogo){
  document.getElementById('clock-wrap').classList.add('vis');
  var _D=['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
  var _M=['January','February','March','April','May','June','July','August','September','October','November','December'];
  function _tick(){
    var n=new Date();
    document.getElementById('clock-time').textContent=
      String(n.getHours()).padStart(2,'0')+':'+String(n.getMinutes()).padStart(2,'0')+':'+String(n.getSeconds()).padStart(2,'0');
    document.getElementById('clock-date').textContent=_D[n.getDay()]+' '+n.getDate()+' '+_M[n.getMonth()]+' '+n.getFullYear();
  }
  _tick(); setInterval(_tick, 1000);
}

// ── Particle canvas ─────────────────────────────────────────────────────────
if(_bgStyle==='particles' && _hasStation && !_fsLogo){
  var cv=document.getElementById('cv');
  var ctx=cv.getContext('2d');
  var _rgb=_brandRgb.split(',').map(Number);
  function _resize(){cv.width=window.innerWidth;cv.height=window.innerHeight;}
  _resize(); window.addEventListener('resize',_resize);
  cv.style.display='block';
  var pts=[];
  function _pt(){
    return{x:Math.random()*cv.width,y:cv.height+Math.random()*140,
      r:1.2+Math.random()*3.2,sp:0.3+Math.random()*0.7,
      dr:(Math.random()-.5)*0.3,al:0.3+Math.random()*0.55};
  }
  for(var i=0;i<100;i++){var p=_pt();if(i<55)p.y=Math.random()*cv.height;pts.push(p);}
  var _speedMult=1;
  function _drawPts(){
    ctx.clearRect(0,0,cv.width,cv.height);
    pts.forEach(function(p){
      p.y-=p.sp*_speedMult; p.x+=p.dr;
      if(p.y<-10||p.x<-10||p.x>cv.width+10) Object.assign(p,_pt());
      ctx.beginPath(); ctx.arc(p.x,p.y,p.r,0,Math.PI*2);
      ctx.fillStyle='rgba('+_rgb[0]+','+_rgb[1]+','+_rgb[2]+','+p.al+')'; ctx.fill();
    });
    requestAnimationFrame(_drawPts);
  }
  _drawPts();
}

// ── Audio-level reactive animations ─────────────────────────────────────────
// _rawLev  — latest poll target (updated every 150 ms by _pollLevel)
// _lev     — slow ambient EMA (driven by RAF loop at 60 fps toward _rawLev)
// _levSnap — fast beat EMA    (driven by RAF loop at 60 fps toward _rawLev)
// Decoupling poll from render prevents the 150 ms jump-to-value jitter.
var _rawLev   = 0;
var _lev      = 0;
var _levSnap  = 0;
var _bloom    = document.getElementById('lev-bloom');
var _bloomCore= document.getElementById('lev-bloom-core');
var _bgPulse  = document.getElementById('bg-pulse');
var _vignette = document.getElementById('vignette');
var _beatFlash= document.getElementById('beat-flash');
var _orbWrap  = document.querySelector('.orbit-wrap');
var _orb1     = document.querySelector('.orb1');
var _orb2     = document.querySelector('.orb2');
var _prngs    = document.querySelectorAll('.prng');
var _pulseWrap= document.querySelector('.pulse-wrap');
var _logoImg  = document.getElementById('logo-img');
var _npTitle  = document.getElementById('np-title');
var _waveWrap = document.querySelector('.wave-wrap');
// New background element refs (null when bg is a different preset — safe to guard)
var _beams    = document.querySelectorAll('.beam');
var _hBlobs   = document.querySelectorAll('.haze-blob');
var _burstRays= document.querySelector('.burst-rays');
var _gridPlane= document.querySelector('.bg-grid-plane');
// Background element for hue-shift (skip aurora — it has its own CSS animation)
var _bgEl     = _bgStyle!=='aurora' ? (
  document.querySelector('.bg-particles-base') ||
  document.querySelector('.bg-waves') ||
  document.querySelector('.bg-minimal') ||
  document.querySelector('.bg-beams') ||
  document.querySelector('.bg-grid') ||
  document.querySelector('.bg-burst') ||
  document.querySelector('.bg-haze') ) : null;

// _applyEffects: called at 60 fps by the RAF loop; reads _lev / _levSnap
// which are already smoothed — no EMA computation here.
function _applyEffects(){

  // ── Outer bloom: large soft halo — grows from tiny to screen-filling ───────
  var bScale = 0.12 + _levSnap * 3.1;
  var bOp    = Math.min(0.92, 0.25 + _levSnap * 0.70);
  _bloom.style.transform = 'scale(' + bScale.toFixed(3) + ')';
  _bloom.style.opacity   = bOp.toFixed(3);

  // ── Inner bloom core: sharp "lamp" at logo centre — the punchy heartbeat ──
  if(_bloomCore){
    _bloomCore.style.transform = 'scale(' + (0.06 + _levSnap * 2.4).toFixed(3) + ')';
    _bloomCore.style.opacity   = Math.min(0.92, _levSnap * 0.98).toFixed(3);
  }

  // ── Background brightening wash ────────────────────────────────────────────
  if(_bgPulse) _bgPulse.style.opacity = (_levSnap * 0.92).toFixed(3);

  // ── Vignette breath — subtle edge darkening that lifts on loud audio ────────
  if(_vignette) _vignette.style.opacity = Math.max(0.01, 0.18 - _levSnap * 0.17).toFixed(3);

  // ── Beat flash: brand-colour radial wash, fires above 0.45 threshold ───────
  if(_beatFlash) _beatFlash.style.opacity = Math.max(0, (_levSnap - 0.45) * 0.40).toFixed(3);

  // ── Logo: bigger scale + brightness/saturation pump ────────────────────────
  // drop-shadow() removed from the JS filter — a dynamically-sized drop-shadow
  // (up to 575 px radius) requires the GPU to re-blur the full element alpha
  // channel every frame, which causes visible glitching on Raspberry Pi /
  // Yodeck players. The reactive glow effect is provided by #lev-bloom and
  // #lev-bloom-core (transform+opacity — GPU-composited, free). brightness()
  // and saturate() are cheap single-pass GPU filters and stay.
  if(_logoImg){
    var logoScale = 1.0 + _levSnap * 0.22;               // 1.0→1.22
    var bright    = (1.0 + _levSnap * 0.60).toFixed(2);  // 1.0→1.60 brightness
    var sat       = (1.0 + _levSnap * 1.10).toFixed(2);  // 1.0→2.10 saturation
    // spin/bounce RAF loop owns transform; glitch CSS keyframes own transform —
    // don't overwrite with a plain scale() or they'll fight each other
    if(_logoAnim !== 'spin' && _logoAnim !== 'bounce' && _logoAnim !== 'glitch'){
      _logoImg.style.transform = 'scale(' + logoScale.toFixed(4) + ')';
    }
    _logoImg.style.filter = 'brightness(' + bright + ') saturate(' + sat + ')';
  }

  // ── Orbit rings: opacity + gentle container scale ─────────────────────────
  // Do NOT change animationDuration — mid-flight duration changes cause the
  // browser to restart the animation, producing visible glitching every 150 ms.
  // Instead: fade rings in/out and gently breathe the container size.
  if(_orb1) _orb1.style.opacity = (0.18 + _levSnap * 0.60).toFixed(2);  // 0.18→0.78
  if(_orb2) _orb2.style.opacity = (0.06 + _levSnap * 0.38).toFixed(2);  // 0.06→0.44
  if(_orbWrap) _orbWrap.style.transform = 'scale(' + (0.94 + _levSnap * 0.12).toFixed(3) + ')';

  // ── Pulse rings: visibility — never change animationDuration (causes restart jitter)
  // The CSS animation cycles at its fixed 3.2 s rate; JS scales the pulse-wrap
  // opacity so rings brighten on beats and fade at silence.
  if(_pulseWrap) _pulseWrap.style.opacity = Math.min(1, _levSnap * 2.2).toFixed(3);

  // Background hue-shift intentionally omitted — applying hue-rotate() to a
  // full-screen element every frame is expensive on Raspberry Pi / Yodeck.

  // ── Waves preset: pump wave height with level (bottom-anchored scaleY) ─────
  if(_waveWrap){
    _waveWrap.style.transformOrigin = 'bottom center';
    _waveWrap.style.transform = 'scaleY(' + (1 + _levSnap * 0.6).toFixed(3) + ')';
  }

  // ── Particle speed ─────────────────────────────────────────────────────────
  if(_bgStyle==='particles') _speedMult = 1 + _levSnap * 12;

  // ── Beams: raise opacity with level — blur is fixed in CSS, not re-applied ──
  // Mutating filter:blur() from JS forces a full repaint of each beam element
  // every frame; swapping to opacity means the GPU reuses the cached blurred
  // texture and only adjusts the alpha — compositor-only, no repaint.
  if(_beams.length){
    var _bOp = (0.5 + _levSnap * 0.5).toFixed(2);
    _beams.forEach(function(b){ b.style.opacity = _bOp; });
  }

  // ── Burst: pulse ray opacity with level ────────────────────────────────────
  if(_burstRays) _burstRays.style.opacity = (0.65 + _levSnap * 0.35).toFixed(2);

  // ── Grid: raise opacity with level (no per-frame filter mutation) ──────────
  if(_gridPlane) _gridPlane.style.opacity = (0.6 + _levSnap * 0.4).toFixed(2);

  // ── Haze: breathe blob opacity with level — blur is fixed in CSS ───────────
  // blur(88px) is baked into CSS and the GPU caches it; only opacity changes
  // here so no per-frame repaint is triggered.
  if(_hBlobs.length){
    var _hOp = (0.5 + _levSnap * 0.5).toFixed(2);
    _hBlobs.forEach(function(b){ b.style.opacity = _hOp; });
  }

  // ── Now-playing title glow: text halos with the brand colour on beats ───────
  if(_npTitle) _npTitle.style.textShadow =
    '0 0 ' + Math.round(_levSnap * 50) + 'px rgba(' + _brandRgb + ',' +
    Math.min(0.9, _levSnap * 0.9).toFixed(2) + ')';
}

// ── Single merged 60 fps RAF loop ──────────────────────────────────────────
// Consolidating EMA smoothing, spin, bounce, and _applyEffects into ONE
// requestAnimationFrame registration reduces per-frame scheduling overhead —
// important on Raspberry Pi / Yodeck where three independent rAF callbacks
// add measurable CPU cost even when two of them early-exit immediately.
//
// EMA alpha values (per-frame equivalents of per-150ms targets):
//   per-frame = 1 - (1 - alpha_150ms)^(1/9)   [9 frames ≈ 150 ms at 60 fps]
//   _lev      attack 0.45 → 0.066   decay 0.18 → 0.022
//   _levSnap  attack 0.65 → 0.12    decay 0.38 → 0.055
var _spinAngle = 0;
var _bounceY = 0, _bounceVy = 0, _prevSnapLev = 0;
(function _raf(){
  // EMA smoothing toward _rawLev (updated every 150 ms by level poll)
  _lev     = _lev     + (_rawLev - _lev)     * (_rawLev > _lev     ? 0.066 : 0.022);
  _levSnap = _levSnap + (_rawLev - _levSnap) * (_rawLev > _levSnap ? 0.12  : 0.055);

  // Spin logo — owns _logoImg.style.transform in spin mode (_applyEffects skips it)
  if(_logoAnim === 'spin'){
    _spinAngle = (_spinAngle + 0.25 + _levSnap * 3.25) % 360;
    if(_logoImg)
      _logoImg.style.transform = 'rotate(' + _spinAngle.toFixed(1) + 'deg) scale(' + (1.0 + _levSnap * 0.14).toFixed(3) + ')';
  }

  // Bounce logo — elastic physics, owns transform in bounce mode
  if(_logoAnim === 'bounce'){
    var _dSnap = _levSnap - _prevSnapLev;
    if(_dSnap > 0.10 && _levSnap > 0.28){ _bounceVy = -(10 + _levSnap * 20); }
    _prevSnapLev = _levSnap;
    _bounceVy += 3.8;
    _bounceY   = Math.min(_bounceY + _bounceVy, 0);
    if(_bounceY >= 0){ _bounceVy *= -0.38; }
    if(Math.abs(_bounceVy) < 0.2 && _bounceY > -0.5){ _bounceY = 0; _bounceVy = 0; }
    if(_logoImg)
      _logoImg.style.transform = 'translateY(' + _bounceY.toFixed(1) + 'px) scale(' + (1.0 + _levSnap * 0.10).toFixed(3) + ')';
  }

  _applyEffects();
  requestAnimationFrame(_raf);
})();

// ── Live level poll ──────────────────────────────────────────────────────────
// Poll sets _rawLev every 150 ms; the RAF loop above smoothly interpolates
// toward it and applies all effects — no visual jump on each poll tick.

// /api/hub/live_levels returns a NESTED structure:
//   { "site_name": [ {name, level_dbfs, ...}, ... ], ... }
// _levelKey is "site|stream" — must split to look up d[site][stream].
if(_levelKey && _hasStation && !_fsLogo){
  var _lkSep  = _levelKey.indexOf('|');
  var _lkSite = _lkSep >= 0 ? _levelKey.slice(0, _lkSep) : _levelKey;
  var _lkName = _lkSep >= 0 ? _levelKey.slice(_lkSep + 1) : '';
  var _levErr = 0;
  function _pollLevel(){
    fetch(_tk('/api/hub/live_levels'),{credentials:'same-origin'})
      .then(function(r){ if(!r.ok) throw new Error(r.status); return r.json(); })
      .then(function(d){
        _levErr = 0;
        var e = null;
        var siteArr = d[_lkSite];
        if(Array.isArray(siteArr)){
          for(var i=0;i<siteArr.length;i++){
            if(siteArr[i].name === _lkName){ e = siteArr[i]; break; }
          }
        }
        if(e && e.level_dbfs != null){
          // Map −60 dBFS → 0.0,  0 dBFS → 1.0 — just set target; RAF loop smooths
          _rawLev = Math.max(0, Math.min(1, (e.level_dbfs + 60) / 60));
        } else {
          _rawLev = 0;
        }
      })
      .catch(function(){ _levErr++; if(_levErr > 10) _rawLev = 0; });
  }
  _pollLevel(); setInterval(_pollLevel, 150);
}

// ── Now-playing poll ────────────────────────────────────────────────────────
function _applyNP(d){
  if(_showNP && _hasStation){
    document.getElementById('lower').classList.add('vis');
    var np=d.np||{};
    var title=np.title||'', artist=np.artist||'', isSpot=np.is_spot||false;
    document.getElementById('np-title').textContent = title||'—';
    var aEl=document.getElementById('np-artist');
    aEl.textContent=artist; aEl.style.display=artist?'':'none';
    isSpot ? document.getElementById('np-spot').classList.add('vis') : document.getElementById('np-spot').classList.remove('vis');
    if(_showOair){
      (title&&!isSpot) ? document.getElementById('on-air').classList.add('vis') : document.getElementById('on-air').classList.remove('vis');
    }
  }
  var msg=(d.message||'').trim();
  var mb=document.getElementById('msg-bar');
  if(msg){document.getElementById('msg-txt').textContent=msg; mb.classList.add('vis');}
  else{mb.classList.remove('vis');}
}
function _pollNP(){
  if(!_stationId) return;
  fetch(_tk('/api/brandscreen/data/'+_stationId),{credentials:'same-origin'})
    .then(function(r){return r.json();}).then(_applyNP).catch(function(){});
}
if(_hasStation && !_fsLogo){ _pollNP(); setInterval(_pollNP, 10000); }

// ── Full-screen takeover (with mic-live suppression) ────────────────────────
// When a mic is live in the linked Studio Board studio, takeovers are held
// back silently.  The moment the mic goes down, any pending takeover shows.
var _micIsLive       = false;
var _pendingTakeover = null;   // {title, text} stored while mic is live

function _showTakeover(title, text){
  if(_micIsLive){
    // Mic live — store but do not show yet
    _pendingTakeover = {title: title || '', text: text || ''};
    return;
  }
  _pendingTakeover = null;
  document.getElementById('takeover-title').textContent = title || '';
  document.getElementById('takeover-text').textContent  = text  || '';
  document.getElementById('takeover').classList.add('vis');
}
function _clearTakeover(){
  _pendingTakeover = null;
  document.getElementById('takeover').classList.remove('vis');
}

// Called when mic state changes. On mic-up: hide any visible takeover and
// park it as pending.  On mic-down: show any parked takeover.
function _setMicLive(live){
  _micIsLive = live;
  if(live){
    var to=document.getElementById('takeover');
    if(to.classList.contains('vis')){
      // Takeover was showing — park it and hide
      _pendingTakeover = {
        title: document.getElementById('takeover-title').textContent,
        text:  document.getElementById('takeover-text').textContent,
      };
      to.classList.remove('vis');
    }
  } else {
    // Mic down — release any parked takeover
    if(_pendingTakeover){
      var pt=_pendingTakeover; _pendingTakeover=null;
      _showTakeover(pt.title, pt.text);
    }
  }
}

// ── SSE — instant studio assignment / settings / takeover / mic-live updates ─
if(_studioId){
  var _es = new EventSource(_tk('/api/brandscreen/events/studio/'+_studioId),{withCredentials:true});
  _es.onmessage = function(e){
    if(e.data==='assignment_changed' || e.data==='settings_changed'){
      // Reload to pick up new brand settings, bg_style, logo_anim, toggles, etc.
      // Fade-out first so there is no hard-cut flash during the page reload.
      document.getElementById('screen').classList.add('fade-out');
      setTimeout(function(){ location.replace(location.href); }, 580);
    } else if(e.data.indexOf('takeover:') === 0){
      try{ var _td=JSON.parse(e.data.slice(9)); _showTakeover(_td.title,_td.text); }catch(ex){}
    } else if(e.data==='takeover_clear'){
      _clearTakeover();
    } else if(e.data==='mic_live'){
      _setMicLive(true);
    } else if(e.data==='mic_down'){
      _setMicLive(false);
    }
  };
  // On page load: fetch mic state first, then takeover state, so that
  // _showTakeover already knows whether the mic is live and can park it.
  fetch(_tk('/api/brandscreen/studio/'+_studioId+'/mic_state'),{credentials:'same-origin'})
    .then(function(r){ return r.ok?r.json():{}; })
    .then(function(d){
      _micIsLive = !!(d.mic_live);
      // Now restore any active takeover — respects _micIsLive via _showTakeover
      return fetch(_tk('/api/brandscreen/studio/'+_studioId+'/takeover'),{credentials:'same-origin'});
    })
    .then(function(r){ return r.ok?r.json():{}; })
    .then(function(d){ if(d.active) _showTakeover(d.title,d.text); })
    .catch(function(){});
}
</script>
</body>
</html>"""

# ────────────────────────────────────────────────── register ──────────────────

def register(app, ctx):
    login_required = ctx["login_required"]
    csrf_protect   = ctx["csrf_protect"]
    monitor        = ctx["monitor"]
    hub_server     = ctx["hub_server"]

    # Allow producer/plugin-role users to write to brandscreen API routes so
    # they can change studio assignments, schedules, etc. without needing admin.
    ctx.get("register_plugin_write_prefix", lambda _: None)("/api/brandscreen/")

    api_auth = _make_api_auth()

    # ── Kiosk: strip security headers so Yodeck can load the page ────────────
    _KIOSK_PREFIXES = ("/brandscreen/", "/api/brandscreen/")

    def _bs_kiosk_headers(response):
        is_kiosk = getattr(g, "_bs_kiosk", False)
        if not is_kiosk:
            for pfx in _KIOSK_PREFIXES:
                if request.path.startswith(pfx):
                    is_kiosk = True
                    break
        if is_kiosk:
            for h in ("X-Frame-Options", "Content-Security-Policy",
                      "X-Content-Type-Options", "Referrer-Policy",
                      "Strict-Transport-Security"):
                response.headers.pop(h, None)
            response.headers["Access-Control-Allow-Origin"] = "*"
        return response

    try:
        app.after_request_funcs.setdefault(None, []).insert(0, _bs_kiosk_headers)
    except Exception:
        app.after_request(_bs_kiosk_headers)

    # ── Token-based kiosk auth ────────────────────────────────────────────────
    def _validate_bs_token(token):
        """Return True if token matches any studio or station token in config."""
        if not token:
            return False
        cfg = _cfg_load()
        for s in list(cfg.get("stations", [])) + list(cfg.get("studios", [])):
            if s.get("token") == token:
                return True
        return False

    @app.before_request
    def _bs_token_before():
        # Fire for any path that carries a token — kiosk pages + any API
        # calls the JS makes that include the token as a query param
        token = (request.args.get("token") or "").strip()
        if not token:
            return
        g._bs_kiosk = True
        if session.get("logged_in"):
            return
        if _validate_bs_token(token):
            session["logged_in"] = True
            session["login_ts"]  = _time.time()
            session["username"]  = "brandscreen"
            session["role"]      = "viewer"
            if not session.get("_csrf"):
                session["_csrf"] = hashlib.sha256(os.urandom(32)).hexdigest()

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _screen_params(st, studio_id="", studio_name="", kiosk_token=""):
        """Build render_template_string kwargs for the screen template."""
        brand  = (st or {}).get("brand_colour", "#17a8ff")
        accent = (st or {}).get("accent_colour", "#ffffff")
        r, g, b = _hex_rgb(brand)
        pal    = _brand_palette(brand)
        sid    = (st or {}).get("id", "") if st else ""
        p, _   = _logo_file(sid) if sid else (None, None)
        lk     = (st or {}).get("level_key", "")
        return dict(
            studio_id=studio_id, studio_name=studio_name,
            station_id=sid,
            sname=(st or {}).get("name", studio_name),
            brand=brand, accent=accent,
            brand_rgb=f"{r},{g},{b}",
            bg_deep=pal["bg_deep"], bg_dark=pal["bg_dark"], bg_mid=pal["bg_mid"],
            bg_style=(st or {}).get("bg_style", "minimal"),
            logo_anim=(st or {}).get("logo_anim", "none"),
            show_clock=(st or {}).get("show_clock", True),
            show_on_air=(st or {}).get("show_on_air", True),
            show_now_playing=(st or {}).get("show_now_playing", True),
            has_logo=p is not None,
            level_key=lk,
            full_screen_logo=bool((st or {}).get("full_screen_logo", False)),
            kiosk_token=kiosk_token,
        )

    def _get_streams():
        streams = []
        try:
            for sd in (hub_server.get_sites() or []):
                site = sd.get("name") or sd.get("site", "")
                for s in (sd.get("streams") or []):
                    name = (s.get("name") or "").strip()
                    if name:
                        key = f"{site}|{name}"
                        streams.append({"key": key, "site": site,
                                        "stream": name,
                                        "label": f"{site} / {name}"})
        except Exception:
            pass
        return sorted(streams, key=lambda x: x["label"])

    # ── Admin / Producer page ─────────────────────────────────────────────────
    _STANDARD_ROLES = {"admin", "operator", "viewer"}

    @app.get("/hub/brandscreen")
    @login_required
    def bs_admin():
        cfg      = _cfg_load()
        stations = [dict(s) for s in cfg.get("stations", [])]
        for s in stations:
            p, _ = _logo_file(s["id"])
            s["_has_logo"] = p is not None
        has_presenter = any(str(r) == "/producer" for r in app.url_map.iter_rules())
        has_listener  = any(str(r) == "/listener"  for r in app.url_map.iter_rules())
        # Non-admin (plugin-role) users get the simplified producer view
        role = session.get("role", "admin")
        if role not in _STANDARD_ROLES:
            return render_template_string(
                _PRODUCER_BS_TPL,
                stations_json=json.dumps(stations),
                studios_json=json.dumps(cfg.get("studios", [])),
                schedules_json=json.dumps(cfg.get("schedules", [])),
                has_presenter=has_presenter,
                has_listener=has_listener,
            )
        api_key = _ensure_api_key(cfg)
        return render_template_string(
            _ADMIN_TPL,
            stations_json=json.dumps(stations),
            studios_json=json.dumps(cfg.get("studios", [])),
            schedules_json=json.dumps(cfg.get("schedules", [])),
            streams_json=json.dumps(_get_streams()),
            api_key=api_key,
            origin=request.host_url.rstrip("/"),
            has_presenter=has_presenter,
            has_listener=has_listener,
        )

    def _kiosk_response(html):
        """Wrap an HTML string in a kiosk-safe Response: strip all security
        headers, set CORS + Cache-Control, and suppress session-cookie writing.
        Mirrors the approach used by studioboard_tv()."""
        resp = make_response(html)
        for h in ("X-Frame-Options", "Content-Security-Policy",
                  "X-Content-Type-Options", "Referrer-Policy",
                  "Strict-Transport-Security"):
            resp.headers.pop(h, None)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        # Do NOT persist the session cookie — the token in the URL is the
        # sole auth mechanism for kiosk/Yodeck mode.
        session.modified = False
        return resp

    # ── Studio screen ─────────────────────────────────────────────────────────
    @app.get("/brandscreen/studio/<studio_id>")
    def bs_studio_screen(studio_id):
        g._bs_kiosk = True
        app_cfg = monitor.app_cfg
        token   = (request.args.get("token") or "").strip()
        if getattr(getattr(app_cfg, "auth", None), "enabled", False):
            if not session.get("logged_in") and not _validate_bs_token(token):
                return ("<h2>Token required</h2>"
                        "<p>Open this URL with <code>?token=YOUR_TOKEN</code></p>"), 403
        cfg    = _cfg_load()
        studio = _get_studio(cfg, studio_id)
        if not studio:
            return "Studio not found", 404
        sid = studio.get("station_id", "")
        st  = _get_station(cfg, sid) if sid else None
        if st and not st.get("enabled", True):
            st = None
        return _kiosk_response(render_template_string(
            _SCREEN_TPL,
            **_screen_params(st, studio_id=studio_id, studio_name=studio.get("name", ""),
                             kiosk_token=token),
        ))

    # ── Direct station screen (backward compat) ───────────────────────────────
    @app.get("/brandscreen/<station_id>")
    def bs_station_screen(station_id):
        if station_id == "studio":
            return "Not found", 404
        g._bs_kiosk = True
        app_cfg = monitor.app_cfg
        token   = (request.args.get("token") or "").strip()
        if getattr(getattr(app_cfg, "auth", None), "enabled", False):
            if not session.get("logged_in") and not _validate_bs_token(token):
                return ("<h2>Token required</h2>"
                        "<p>Open this URL with <code>?token=YOUR_TOKEN</code></p>"), 403
        cfg = _cfg_load()
        s   = _get_station(cfg, station_id)
        if not s:
            return "Station not found", 404
        if not s.get("enabled", True):
            return "Screen disabled", 403
        return _kiosk_response(render_template_string(_SCREEN_TPL,
                                                       **_screen_params(s, kiosk_token=token)))

    # ── SSE ───────────────────────────────────────────────────────────────────
    @app.get("/api/brandscreen/events/studio/<studio_id>")
    def bs_events(studio_id):
        return Response(
            stream_with_context(_sse_stream(studio_id)),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Now-playing data (public) ─────────────────────────────────────────────
    @app.get("/api/brandscreen/data/<station_id>")
    def bs_data(station_id):
        cfg = _cfg_load()
        s   = _get_station(cfg, station_id)
        if not s:
            return jsonify({"error": "not found"}), 404
        np_data = _resolve_np(s, monitor) if s.get("show_now_playing") else None
        return jsonify({"enabled": s.get("enabled", True), "np": np_data, "message": s.get("message", "")})

    # ── REST API: list ────────────────────────────────────────────────────────
    @app.get("/api/brandscreen/studios")
    @api_auth
    def bs_list():
        cfg = _cfg_load()
        return jsonify({
            "studios":  cfg.get("studios", []),
            "stations": [{k: v for k, v in s.items() if not k.startswith("_")}
                         for s in cfg.get("stations", [])],
        })

    # ── REST API: assign station ──────────────────────────────────────────────
    @app.route("/api/brandscreen/studio/<studio_id>/station", methods=["PUT", "POST"])
    @api_auth
    def bs_assign(studio_id):
        cfg    = _cfg_load()
        studio = _get_studio(cfg, studio_id)
        if not studio:
            return jsonify({"error": "Studio not found"}), 404
        data       = request.get_json(force=True) or {}
        station_id = (data.get("station_id") or "").strip()
        if station_id and not _get_station(cfg, station_id):
            return jsonify({"error": "Station not found"}), 404
        # If a scheduled takeover is currently active for this studio, mark it
        # as REST-overridden so the schedule end does NOT revert the assignment.
        with _SCHED_LOCK:
            if studio_id in _schedule_state:
                _schedule_state[studio_id]["rest_override"] = True
        studio["station_id"] = station_id
        _cfg_save(cfg)
        _notify_studio(studio_id)
        # CueServer — trigger LED scene for the newly assigned station
        _cueserver_trigger(studio, _get_station(cfg, station_id) if station_id else None)
        return jsonify({"ok": True, "studio_id": studio_id, "station_id": station_id})

    # ── Schedule CRUD ─────────────────────────────────────────────────────────

    @app.get("/api/brandscreen/schedules")
    @login_required
    def bs_schedules_list():
        cfg = _cfg_load()
        with _SCHED_LOCK:
            active = {sid: {"pre_station_id": s.get("pre_station_id", ""),
                            "active_schedule_id": s.get("active_schedule_id", ""),
                            "rest_override": s.get("rest_override", False)}
                      for sid, s in _schedule_state.items()}
        return jsonify({"schedules": cfg.get("schedules", []), "active": active})

    @app.post("/api/brandscreen/schedule")
    @login_required
    @csrf_protect
    def bs_schedule_create():
        cfg  = _cfg_load()
        data = request.get_json(force=True) or {}
        studio_id  = (data.get("studio_id")  or "").strip()
        station_id = (data.get("station_id") or "").strip()
        if not studio_id or not _get_studio(cfg, studio_id):
            return jsonify({"error": "Studio not found"}), 400
        if not station_id or not _get_station(cfg, station_id):
            return jsonify({"error": "Station not found"}), 400
        sched_date = (data.get("date") or "").strip()
        if sched_date:
            # Validate YYYY-MM-DD format
            try:
                _dt.datetime.strptime(sched_date, "%Y-%m-%d")
            except ValueError:
                return jsonify({"error": "Invalid date format, expected YYYY-MM-DD"}), 400
            days = []   # days ignored for one-off schedules
        else:
            valid_days = {"mon","tue","wed","thu","fri","sat","sun"}
            days = [d for d in (data.get("days") or []) if d in valid_days]
        start = (data.get("start_time") or "00:00").strip()
        end   = (data.get("end_time")   or "23:59").strip()
        sched = {
            "id":         str(uuid.uuid4())[:8],
            "name":       (data.get("name") or "Schedule").strip()[:80],
            "studio_id":  studio_id,
            "station_id": station_id,
            "date":       sched_date,
            "days":       days,
            "start_time": start,
            "end_time":   end,
            "enabled":    True,
        }
        cfg.setdefault("schedules", []).append(sched)
        _cfg_save(cfg)
        return jsonify({"ok": True, "schedule": sched})

    @app.patch("/api/brandscreen/schedule/<sched_id>")
    @login_required
    @csrf_protect
    def bs_schedule_update(sched_id):
        cfg   = _cfg_load()
        sched = next((s for s in cfg.get("schedules", []) if s["id"] == sched_id), None)
        if not sched:
            return jsonify({"error": "Schedule not found"}), 404
        data  = request.get_json(force=True) or {}
        for k in ("name", "days", "date", "start_time", "end_time", "enabled",
                  "studio_id", "station_id"):
            if k in data:
                sched[k] = data[k]
        _cfg_save(cfg)
        return jsonify({"ok": True, "schedule": sched})

    @app.delete("/api/brandscreen/schedule/<sched_id>")
    @login_required
    @csrf_protect
    def bs_schedule_delete(sched_id):
        cfg = _cfg_load()
        before = len(cfg.get("schedules", []))
        cfg["schedules"] = [s for s in cfg.get("schedules", []) if s["id"] != sched_id]
        if len(cfg["schedules"]) == before:
            return jsonify({"error": "Not found"}), 404
        _cfg_save(cfg)
        return jsonify({"ok": True})

    # ── Logo ──────────────────────────────────────────────────────────────────
    @app.get("/api/brandscreen/logo/<station_id>")
    def bs_logo_get(station_id):
        p, ext = _logo_file(station_id)
        if not p:
            return "", 404
        mt, _ = mimetypes.guess_type(f"x.{ext}")
        return send_file(p, mimetype=mt or "image/png")

    @app.post("/api/brandscreen/logo/<station_id>")
    @login_required
    @csrf_protect
    def bs_logo_upload(station_id):
        cfg = _cfg_load()
        if not _get_station(cfg, station_id):
            return jsonify({"error": "Station not found"}), 404
        f = request.files.get("logo")
        if not f:
            return jsonify({"error": "No file"}), 400
        fn  = (f.filename or "").lower()
        ext = fn.rsplit(".", 1)[-1] if "." in fn else ""
        if ext not in ("png", "svg", "jpg", "jpeg", "webp", "gif"):
            return jsonify({"error": "Use PNG, SVG, JPG or WebP"}), 400
        for e in ("png", "svg", "jpg", "jpeg", "webp", "gif"):
            old = os.path.join(_LOGO_DIR, f"{station_id}.{e}")
            if os.path.exists(old):
                os.remove(old)
        f.save(os.path.join(_LOGO_DIR, f"{station_id}.{ext}"))
        _notify_station_studios(_cfg_load(), station_id)
        return jsonify({"ok": True})

    @app.delete("/api/brandscreen/logo/<station_id>")
    @login_required
    @csrf_protect
    def bs_logo_delete(station_id):
        for e in ("png", "svg", "jpg", "jpeg", "webp", "gif"):
            p = os.path.join(_LOGO_DIR, f"{station_id}.{e}")
            if os.path.exists(p):
                os.remove(p)
        _notify_station_studios(_cfg_load(), station_id)
        return jsonify({"ok": True})

    # ── Studio CRUD ───────────────────────────────────────────────────────────
    @app.post("/api/brandscreen/studio")
    @login_required
    @csrf_protect
    def bs_studio_create():
        cfg = _cfg_load()
        s   = _new_studio()
        cfg.setdefault("studios", []).append(s)
        _cfg_save(cfg)
        return jsonify({"ok": True, "studio": s})

    @app.post("/api/brandscreen/studio/<studio_id>")
    @login_required
    @csrf_protect
    def bs_studio_save(studio_id):
        cfg = _cfg_load()
        s   = _get_studio(cfg, studio_id)
        if not s:
            return jsonify({"error": "Studio not found"}), 404
        data = request.get_json(force=True) or {}
        for k in ("name", "station_id", "sb_studio_id",
                  "cueserver_site", "cueserver_host", "cs_zone_name"):
            if k in data:
                s[k] = str(data[k])[:200] if data[k] is not None else ""
        # cs_strips: validate each strip's channel numbers
        # Brightness is per-brand/station (cs_brightness), NOT per strip —
        # strip dicts contain channel assignments only.
        if "cs_strips" in data:
            raw_strips = data["cs_strips"] if isinstance(data["cs_strips"], list) else []
            clean = []
            for strip in raw_strips:
                if not isinstance(strip, dict):
                    continue
                def _ch(v):
                    try: return max(0, min(512, int(v)))
                    except (TypeError, ValueError): return 0
                clean.append({
                    "name": str(strip.get("name") or "Strip")[:40],
                    "ch_r": _ch(strip.get("ch_r")),
                    "ch_g": _ch(strip.get("ch_g")),
                    "ch_b": _ch(strip.get("ch_b")),
                    "ch_w": _ch(strip.get("ch_w")),
                })
            s["cs_strips"] = clean
        _cfg_save(cfg)
        _notify_studio(studio_id)
        return jsonify({"ok": True, "studio": s})

    @app.delete("/api/brandscreen/studio/<studio_id>")
    @login_required
    @csrf_protect
    def bs_studio_delete(studio_id):
        cfg = _cfg_load()
        cfg["studios"] = [s for s in cfg.get("studios", []) if s.get("id") != studio_id]
        _cfg_save(cfg)
        return jsonify({"ok": True})

    @app.post("/api/brandscreen/studio/<studio_id>/regen_token")
    @login_required
    @csrf_protect
    def bs_studio_regen(studio_id):
        cfg = _cfg_load()
        s   = _get_studio(cfg, studio_id)
        if not s:
            return jsonify({"error": "Studio not found"}), 404
        s["token"] = str(uuid.uuid4()).replace("-", "")
        _cfg_save(cfg)
        return jsonify({"ok": True, "token": s["token"]})

    # ── Station CRUD ──────────────────────────────────────────────────────────
    @app.post("/api/brandscreen/station")
    @login_required
    @csrf_protect
    def bs_station_create():
        cfg = _cfg_load()
        s   = _new_station()
        cfg.setdefault("stations", []).append(s)
        _cfg_save(cfg)
        s["_has_logo"] = False
        return jsonify({"ok": True, "station": s})

    @app.post("/api/brandscreen/station/<station_id>")
    @login_required
    @csrf_protect
    def bs_station_save(station_id):
        cfg = _cfg_load()
        s   = _get_station(cfg, station_id)
        if not s:
            return jsonify({"error": "Station not found"}), 404
        data    = request.get_json(force=True) or {}
        allowed = [
            "name", "enabled", "brand_colour", "accent_colour",
            "bg_style", "logo_anim", "show_clock", "show_on_air", "show_now_playing",
            "level_key", "np_source", "np_zetta_key", "np_api_url",
            "np_api_title_path", "np_api_artist_path", "np_manual", "message",
            "full_screen_logo", "cueserver_cmd", "cs_brightness",
        ]
        for k in allowed:
            if k in data:
                s[k] = data[k]
        # Clamp cs_brightness to 0–100
        if "cs_brightness" in data:
            try:
                s["cs_brightness"] = max(0, min(100, int(round(float(data["cs_brightness"])))))
            except (TypeError, ValueError):
                s["cs_brightness"] = 100
        _cfg_save(cfg)
        # Notify any studio screen currently showing this station
        _notify_station_studios(cfg, station_id)
        p, _ = _logo_file(station_id)
        s["_has_logo"] = p is not None
        return jsonify({"ok": True, "station": s})

    @app.delete("/api/brandscreen/station/<station_id>")
    @login_required
    @csrf_protect
    def bs_station_delete(station_id):
        cfg = _cfg_load()
        cfg["stations"] = [s for s in cfg.get("stations", []) if s.get("id") != station_id]
        for sd in cfg.get("studios", []):
            if sd.get("station_id") == station_id:
                sd["station_id"] = ""
                _notify_studio(sd["id"])
        _cfg_save(cfg)
        for e in ("png", "svg", "jpg", "jpeg", "webp", "gif"):
            p = os.path.join(_LOGO_DIR, f"{station_id}.{e}")
            if os.path.exists(p):
                os.remove(p)
        return jsonify({"ok": True})

    # ── Takeover API ──────────────────────────────────────────────────────────

    @app.route("/api/brandscreen/studio/<studio_id>/takeover", methods=["POST", "PUT"])
    @api_auth
    def bs_takeover_set(studio_id):
        cfg = _cfg_load()
        if not _get_studio(cfg, studio_id):
            return jsonify({"error": "Studio not found"}), 404
        data  = request.get_json(force=True) or {}
        title = (data.get("title") or "").strip()
        text  = (data.get("text")  or "").strip()
        with _TLOCK:
            _takeovers[studio_id] = {"title": title, "text": text, "ts": _time.time()}
        _notify_studio_msg(studio_id, "takeover:" + json.dumps({"title": title, "text": text}))
        return jsonify({"ok": True})

    @app.delete("/api/brandscreen/studio/<studio_id>/takeover")
    @api_auth
    def bs_takeover_clear(studio_id):
        with _TLOCK:
            _takeovers.pop(studio_id, None)
        _notify_studio_msg(studio_id, "takeover_clear")
        return jsonify({"ok": True})

    @app.get("/api/brandscreen/studio/<studio_id>/takeover")
    @api_auth
    def bs_takeover_get(studio_id):
        # Studio-level takeover takes priority
        with _TLOCK:
            t = dict(_takeovers.get(studio_id) or {})
        if t:
            return jsonify({"active": True, "title": t.get("title", ""),
                            "text": t.get("text", ""), "ts": t.get("ts", 0)})
        # Fall back to station-level takeover for whichever station this studio shows
        cfg    = _cfg_load()
        studio = _get_studio(cfg, studio_id)
        if studio:
            sid = studio.get("station_id", "")
            if sid:
                with _STLOCK:
                    st = dict(_station_takeovers.get(sid) or {})
                if st:
                    return jsonify({"active": True, "title": st.get("title", ""),
                                    "text": st.get("text", ""), "ts": st.get("ts", 0)})
        return jsonify({"active": False})

    # ── Station-level takeover (fires on every studio showing that station) ───

    @app.route("/api/brandscreen/station/<station_id>/takeover", methods=["POST", "PUT"])
    @api_auth
    def bs_station_takeover_set(station_id):
        cfg = _cfg_load()
        if not _get_station(cfg, station_id):
            return jsonify({"error": "Station not found"}), 404
        data  = request.get_json(force=True) or {}
        title = (data.get("title") or "").strip()
        text  = (data.get("text")  or "").strip()
        with _STLOCK:
            _station_takeovers[station_id] = {"title": title, "text": text, "ts": _time.time()}
        msg = "takeover:" + json.dumps({"title": title, "text": text})
        notified = 0
        for studio in cfg.get("studios", []):
            if studio.get("station_id") == station_id:
                _notify_studio_msg(studio["id"], msg)
                notified += 1
        return jsonify({"ok": True, "studios_notified": notified})

    @app.delete("/api/brandscreen/station/<station_id>/takeover")
    @api_auth
    def bs_station_takeover_clear(station_id):
        with _STLOCK:
            _station_takeovers.pop(station_id, None)
        cfg = _cfg_load()
        for studio in cfg.get("studios", []):
            if studio.get("station_id") == station_id:
                sid = studio["id"]
                # Only clear screens that have no studio-level override
                with _TLOCK:
                    has_studio_override = sid in _takeovers
                if not has_studio_override:
                    _notify_studio_msg(sid, "takeover_clear")
        return jsonify({"ok": True})

    @app.get("/api/brandscreen/station/<station_id>/takeover")
    @api_auth
    def bs_station_takeover_get(station_id):
        with _STLOCK:
            t = dict(_station_takeovers.get(station_id) or {})
        if t:
            return jsonify({"active": True, "title": t.get("title", ""),
                            "text": t.get("text", ""), "ts": t.get("ts", 0)})
        return jsonify({"active": False})

    # ── Mic-live state query (used on page load to restore state) ─────────────
    @app.get("/api/brandscreen/studio/<studio_id>/mic_state")
    @api_auth
    def bs_mic_state(studio_id):
        """Return the current mic-live state for a brand-screen studio.
        Reads from _mic_live (updated by the background monitor thread) or, if
        not yet initialised for this studio, queries studioboard_cfg.json directly
        so the very first page load always shows the correct state."""
        with _MLOCK:
            if studio_id in _mic_live:
                return jsonify({"mic_live": _mic_live[studio_id]})
        # Not yet populated by the background thread — read directly
        bs_cfg = _cfg_load()
        bs_sd  = _get_studio(bs_cfg, studio_id)
        if not bs_sd:
            return jsonify({"mic_live": False})
        sb_id  = (bs_sd.get("sb_studio_id") or "").strip()
        if not sb_id:
            return jsonify({"mic_live": False})
        sb_cfg = _sb_cfg_load()
        for s in sb_cfg.get("studios", []):
            if s.get("id") == sb_id:
                return jsonify({"mic_live": bool(s.get("mic_live", False))})
        return jsonify({"mic_live": False})

    # ── API key ───────────────────────────────────────────────────────────────
    @app.post("/api/brandscreen/regen_api_key")
    @login_required
    @csrf_protect
    def bs_regen_api_key():
        cfg = _cfg_load()
        cfg["api_key"] = str(uuid.uuid4()).replace("-", "")
        _cfg_save(cfg)
        return jsonify({"ok": True, "api_key": cfg["api_key"]})

    # ── CueServer: list hub sites (for studio config dropdown) ────────────────
    @app.get("/api/brandscreen/hub_sites")
    @login_required
    def bs_hub_sites():
        """Return the names of all approved client sites connected to this hub."""
        try:
            sites = sorted(
                name for name, data in (hub_server._sites or {}).items()
                if data.get("_approved")
            )
        except Exception:
            sites = []
        return jsonify({"sites": sites})

    # ── CueServer: hub poll endpoint (called by client poller thread) ─────────
    @app.get("/api/brandscreen/cueserver_cmd")
    def bs_cueserver_cmd_poll():
        """Return and consume the next CueServer action for the requesting site.

        Admin-initiated actions (poll_scenes, preview_colour, record_cue) take
        priority over brand-change fire commands.  Client authenticates via
        X-Site header; hub checks _approved before returning.
        """
        site = (request.headers.get("X-Site") or "").strip()
        if not site:
            return jsonify({}), 400
        sdata = (hub_server._sites or {}).get(site, {})
        if not sdata.get("_approved"):
            return jsonify({}), 403

        # Admin commands first (FIFO queue)
        with _cs_admin_lock:
            queue = _cs_admin_queue.get(site, [])
            admin_cmd = queue.pop(0) if queue else None

        if admin_cmd:
            return jsonify(admin_cmd)

        # Brand-change fire command (latest wins)
        with _cs_lock:
            cmd = _cs_pending.pop(site, None)
        if not cmd:
            return jsonify({})
        return jsonify({"action": "fire", "host": cmd["host"], "cmd": cmd["cmd"]})

    # ── CueServer: admin action dispatcher ───────────────────────────────────
    @app.post("/api/brandscreen/cueserver_action/<studio_id>")
    @login_required
    @csrf_protect
    def bs_cueserver_action(studio_id):
        """Queue an admin-initiated CueServer action for a studio's client site.

        Actions:
          poll_scenes   — fetch cue list from CueServer (/get.cgi?req=csi)
          preview_colour — set DMX channels to the brand colour (no record)
          record_cue    — set DMX + Record Cue N, returns cue number on success
        """
        cfg    = _cfg_load()
        studio = _get_studio(cfg, studio_id)
        if not studio:
            return jsonify({"error": "Studio not found"}), 404
        cs_site = (studio.get("cueserver_site") or "").strip()
        cs_host = (studio.get("cueserver_host") or "").strip()
        if not cs_site or not cs_host:
            return jsonify({"error": "Studio has no CueServer site or host configured"}), 400

        data   = request.get_json(force=True) or {}
        action = (data.get("action") or "").strip()

        # Resolve strips — migrate old single-channel config transparently
        strips = studio.get("cs_strips") or []
        if not strips:
            # Legacy single-strip migration (cs_dmx_r/g/b from v1.3.19)
            r_ch = int(studio.get("cs_dmx_r") or 1)
            g_ch = int(studio.get("cs_dmx_g") or 2)
            b_ch = int(studio.get("cs_dmx_b") or 3)
            strips = [{"name": "Strip 1", "ch_r": r_ch, "ch_g": g_ch, "ch_b": b_ch, "ch_w": 0}]

        # brightness: sent from browser, clamped 0–100
        try:
            bri = max(0, min(100, int(round(float(data.get("brightness") or 100)))))
        except (TypeError, ValueError):
            bri = 100

        if action == "poll_scenes":
            cmd = {"action": "poll_scenes", "host": cs_host}

        elif action == "preview_colour":
            colour  = (data.get("colour") or "#ffffff").strip()
            cs_cmd  = _cs_colour_cmd(colour, strips, bri)
            if not cs_cmd:
                return jsonify({"error": "No DMX strips configured for this studio"}), 400
            cmd = {"action": "preview_colour", "host": cs_host, "cs_cmd": cs_cmd}

        elif action == "record_cue":
            colour  = (data.get("colour") or "#ffffff").strip()
            cue_num = int(data.get("cue_num") or 0)
            if cue_num <= 0:
                return jsonify({"error": "Invalid cue number"}), 400
            cs_cmd = _cs_colour_cmd(colour, strips, bri)
            if not cs_cmd:
                return jsonify({"error": "No DMX strips configured for this studio"}), 400
            cmd = {
                "action":    "record_cue",
                "host":      cs_host,
                "cs_cmd":    cs_cmd,
                "cue_num":   cue_num,
                "studio_id": studio_id,
            }

        else:
            return jsonify({"error": f"Unknown action: {action}"}), 400

        with _cs_admin_lock:
            if cs_site not in _cs_admin_queue:
                _cs_admin_queue[cs_site] = []
            _cs_admin_queue[cs_site].append(cmd)

        # Clear any stale result for this site so the poller can detect a fresh one
        with _cs_result_lock:
            _cs_result.pop(cs_site, None)

        return jsonify({"ok": True, "site": cs_site, "action": action})

    # ── CueServer: client posts action result back to hub ─────────────────────
    @app.post("/api/brandscreen/cueserver_result_post")
    def bs_cueserver_result_post():
        """Called by the client poller after executing an admin action.

        Stores the result so the admin browser can retrieve it via the poll endpoint.
        Authentication: X-Site header + _approved check.
        """
        site = (request.headers.get("X-Site") or "").strip()
        if not site:
            return jsonify({}), 400
        sdata = (hub_server._sites or {}).get(site, {})
        if not sdata.get("_approved"):
            return jsonify({}), 403
        body = request.get_json(force=True) or {}
        with _cs_result_lock:
            _cs_result[site] = {
                "ts":     _time.time(),
                "action": body.get("action", ""),
                "data":   body.get("data"),
                "error":  body.get("error", ""),
            }
        return jsonify({"ok": True})

    # ── CueServer: admin polls for action result ──────────────────────────────
    @app.get("/api/brandscreen/cueserver_result/<studio_id>")
    @login_required
    def bs_cueserver_result(studio_id):
        """Return the latest CueServer result for a studio's client site."""
        cfg    = _cfg_load()
        studio = _get_studio(cfg, studio_id)
        if not studio:
            return jsonify({"error": "Studio not found"}), 404
        cs_site = (studio.get("cueserver_site") or "").strip()
        if not cs_site:
            return jsonify({"error": "No CueServer site configured"}), 400
        with _cs_result_lock:
            result = _cs_result.get(cs_site)
        if not result:
            return jsonify({"pending": True})
        return jsonify({"pending": False, **result})

    # ── CueServer: client poller thread ──────────────────────────────────────
    # Only starts on client nodes.  Polls the hub every 5 s, and when a command
    # is available executes it against the local CueServer appliance.
    import urllib.parse as _urlparse_cs
    _cs_cfg    = monitor.app_cfg
    _cs_mode   = getattr(getattr(_cs_cfg, "hub", None), "mode", "standalone") or "standalone"
    _cs_hub    = (getattr(getattr(_cs_cfg, "hub", None), "hub_url",   "") or "").rstrip("/")
    _cs_site   = (getattr(getattr(_cs_cfg, "hub", None), "site_name", "") or "").strip()

    if _cs_mode == "client" and _cs_hub and _cs_site:
        def _cueserver_client_poller():
            import urllib.request as _ureq
            import xml.etree.ElementTree as _ET
            _poll_url   = f"{_cs_hub}/api/brandscreen/cueserver_cmd"
            _result_url = f"{_cs_hub}/api/brandscreen/cueserver_result_post"

            def _cs_get(host, path):
                """GET from CueServer; returns (text, None) or (None, error)."""
                try:
                    with _ureq.urlopen(f"http://{host}{path}", timeout=8) as r:
                        return r.read().decode("utf-8", errors="replace"), None
                except Exception as e:
                    return None, str(e)

            def _cs_exe(host, cmd_str):
                cs_url = f"http://{host}/exe.cgi?{_urlparse_cs.urlencode({'cmd': cmd_str})}"
                try:
                    _ureq.urlopen(cs_url, timeout=5).close()
                    return None
                except Exception as e:
                    return str(e)

            def _post_result(action, data=None, error=""):
                try:
                    body = json.dumps({"action": action, "data": data, "error": error}).encode()
                    req  = _ureq.Request(_result_url, data=body, method="POST",
                                         headers={"Content-Type": "application/json",
                                                  "X-Site": _cs_site})
                    _ureq.urlopen(req, timeout=5).close()
                except Exception:
                    pass

            while True:
                try:
                    _time.sleep(5)
                    req = _ureq.Request(_poll_url, headers={"X-Site": _cs_site})
                    with _ureq.urlopen(req, timeout=8) as resp:
                        d = json.loads(resp.read())

                    if not d:
                        continue

                    host   = d.get("host", "")
                    action = d.get("action", "fire")

                    # ── Brand-change fire command ─────────────────────────────
                    if action == "fire":
                        cmd = d.get("cmd", "")
                        if host and cmd:
                            _cs_exe(host, cmd)
                        # no response needed

                    # ── Admin: poll CueServer for cue list ────────────────────
                    elif action == "poll_scenes":
                        if not host:
                            continue
                        raw, err = _cs_get(host, "/get.cgi?req=csi")
                        if err:
                            _post_result("poll_scenes", error=err)
                            continue
                        cues = _parse_cue_list_xml(raw)
                        _post_result("poll_scenes", data={"cues": cues, "raw": raw[:2000]})

                    # ── Admin: preview colour on DMX (no record) ──────────────
                    elif action == "preview_colour":
                        if not host:
                            continue
                        err = _cs_exe(host, d.get("cs_cmd", ""))
                        _post_result("preview_colour",
                                     data={"sent": d.get("cs_cmd", "")},
                                     error=err or "")

                    # ── Admin: set colour + Record Cue N ──────────────────────
                    elif action == "record_cue":
                        if not host:
                            continue
                        cue_num = d.get("cue_num", 0)
                        # Step 1: set DMX channels to the colour
                        err = _cs_exe(host, d.get("cs_cmd", ""))
                        if err:
                            _post_result("record_cue", error=f"Set colour failed: {err}")
                            continue
                        # Step 2: record the cue (CueServer must be sequential)
                        err = _cs_exe(host, f"Record Cue {cue_num}")
                        if err:
                            _post_result("record_cue", error=f"Record failed: {err}")
                            continue
                        _post_result("record_cue", data={
                            "cue_num":   cue_num,
                            "colour_cmd": d.get("cs_cmd", ""),
                            "studio_id": d.get("studio_id", ""),
                        })

                except Exception:
                    pass

        threading.Thread(target=_cueserver_client_poller, daemon=True,
                         name="bs-cueserver-poll").start()
