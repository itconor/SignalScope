# zetta.py — RCS Zetta SOAP integration plugin for SignalScope
# Drop alongside signalscope.py. No extra pip packages required.
#
# Features
# --------
#  • Polls GetNowPlaying for each configured Zetta station
#  • Detects commercial / spot blocks by category name
#  • Dashboard page (/hub/zetta) with live now-playing cards
#  • GET /api/zetta/status  — JSON for all stations (is_spot, title, artist, …)
#  • POST /api/zetta/discover — fetch station list from Zetta via SOAP
#  • POST /api/zetta/test    — verify connectivity and show namespace
#  • POST /api/zetta/debug   — raw SOAP call for any method (troubleshooting)
#  • Public is_spot_block(station_id) for use by other plugins / chain logic

SIGNALSCOPE_PLUGIN = {
    "id":       "zetta",
    "label":    "Zetta",
    "url":      "/hub/zetta",
    "icon":     "📻",
    "hub_only": True,
    "version":  "1.0.4",
}

import json
import logging
import os
import threading
import time
import xml.etree.ElementTree as ET
import urllib.request
import urllib.error

_log = logging.getLogger("zetta_plugin")

# ── Module-level state ────────────────────────────────────────────────────────

_cfg_lock    = threading.Lock()
_state_lock  = threading.Lock()
_poller_stop = threading.Event()

_DEFAULT_CFG = {
    "url":              "",       # e.g. http://zetta-server/ZettaService/ZettaService.asmx
    "status_feed_url":  "",       # e.g. http://zetta-server:3132/StatusFeed (blank = auto-derive)
    "stations":         [],       # [{"id": "1", "name": "Cool FM"}, …]
    "poll_interval":    10,       # seconds between polls
    "timeout":          6,        # SOAP request timeout
    "spot_categories":  ["SPOT", "SPOTS", "COMMERCIAL", "COMMS", "PROMO", "PROMOS"],
}

_zetta_cfg    : dict = dict(_DEFAULT_CFG)
_zetta_state  : dict = {}   # station_id → state dict
_wsdl_cache   : dict = {}   # url → {"ns": str, "methods": [str]}
_cfg_path     : str  = ""
_sf_health    : dict = {"ok": None, "error": "", "ts": 0.0}  # Status Feed health

# ── Public API ────────────────────────────────────────────────────────────────

def is_spot_block(station_id: str) -> bool:
    """Return True if Zetta says station_id is currently in a commercial break."""
    with _state_lock:
        info = _zetta_state.get(str(station_id), {})
    return bool(info.get("is_spot", False))

def now_playing(station_id: str) -> dict:
    """Return the latest state dict for a station (empty dict if unknown)."""
    with _state_lock:
        return dict(_zetta_state.get(str(station_id), {}))

# ── Minimal SOAP client ───────────────────────────────────────────────────────

_SOAP_ENV = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    'xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
    "<soap:Body>"
    "<{method} xmlns=\"{ns}\">{body}</{method}>"
    "</soap:Body>"
    "</soap:Envelope>"
)

def _soap_call(url: str, method: str, body_xml: str, ns: str, timeout: int = 6) -> ET.Element:
    """POST a SOAP envelope; return parsed XML root. Raises on HTTP/network error."""
    envelope = _SOAP_ENV.format(method=method, ns=ns, body=body_xml)
    action   = f'"{ns}{method}"' if ns else f'"{method}"'
    req = urllib.request.Request(
        url,
        data=envelope.encode("utf-8"),
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction":   action,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return ET.fromstring(raw), raw.decode("utf-8", errors="replace")

def _wsdl_info(url: str, timeout: int = 6) -> dict:
    """Fetch WSDL and return {"ns": str, "methods": [str]}. Cached."""
    if url in _wsdl_cache:
        return _wsdl_cache[url]
    sep   = "&" if "?" in url else "?"
    wsdl_url = url + sep + "wsdl"
    info = {"ns": "", "methods": []}
    try:
        with urllib.request.urlopen(wsdl_url, timeout=timeout) as r:
            root = ET.fromstring(r.read())
        info["ns"] = root.get("targetNamespace", "")
        # Collect operation names from <wsdl:operation> elements
        for el in root.iter():
            tag = el.tag.split("}")[-1]
            if tag == "operation":
                name = el.get("name", "")
                if name and name not in info["methods"]:
                    info["methods"].append(name)
    except Exception as e:
        _log.warning(f"[Zetta] WSDL fetch failed: {e}")
    _wsdl_cache[url] = info
    return info

def _ns(url: str, timeout: int = 6) -> str:
    """Return the target namespace, with trailing slash, or a safe fallback."""
    ns = _wsdl_info(url, timeout).get("ns", "")
    if not ns:
        ns = "http://www.rcsworks.com/"
    if not ns.endswith("/"):
        ns += "/"
    return ns

def _find(elem: ET.Element, *local_names: str) -> str:
    """Find the first matching tag (ignoring XML namespace) anywhere in elem. Returns text or ''."""
    for name in local_names:
        for child in elem.iter():
            if child.tag.split("}")[-1] == name and child.text:
                return child.text.strip()
    return ""

# ── Status Feed helpers ───────────────────────────────────────────────────────

def _derive_status_feed_url(main_url: str) -> str:
    """Auto-derive the Zetta Status Feed URL from the main SOAP service URL.
    Strips path/port and rebuilds as http://HOST:3132/StatusFeed."""
    try:
        from urllib.parse import urlparse
        p = urlparse(main_url)
        host = p.hostname or ""
        if not host:
            return ""
        return f"http://{host}:3132/StatusFeed"
    except Exception:
        return ""

def _effective_sf_url() -> str:
    """Return the Status Feed URL to use (override or auto-derived)."""
    with _cfg_lock:
        override = _zetta_cfg.get("status_feed_url", "").strip()
        main_url = _zetta_cfg.get("url", "").strip()
    return override if override else _derive_status_feed_url(main_url)

def _parse_zetta_ts(s: str) -> str:
    """Strip Zetta timestamp quirks (trailing Z, UTC offset, microseconds) so
    strptime can handle them.  Returns cleaned string unchanged if not a ts."""
    if not s or "T" not in s:
        return s
    # Strip trailing Z
    if s.endswith("Z"):
        s = s[:-1]
    # Strip UTC offset ±HH:MM at end
    if len(s) > 6 and s[-3] == ":" and s[-6] in ("+", "-"):
        s = s[:-6]
    # Truncate microseconds to 6 digits for %f
    dot = s.rfind(".")
    if dot != -1:
        s = s[:dot + 7]   # keep at most 6 decimal places
    return s

def _sf_call_is_alive(sf_url: str, timeout: int) -> bool:
    """Call IsAlive on the Status Feed.  Returns True on any valid response."""
    try:
        ns = _ns(sf_url, timeout)
        _soap_call(sf_url, "IsAlive", "", ns, timeout)
        return True
    except Exception:
        return False

def _sf_call_get_stations(sf_url: str, timeout: int) -> list:
    """Call GetStations on the Status Feed; return list of {id, name}."""
    ns = _ns(sf_url, timeout)
    root, _ = _soap_call(sf_url, "GetStations", "", ns, timeout)
    results = []
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag in ("Station", "StationInfo", "StationDetails"):
            sid  = _find(el, "ID", "Id", "StationID", "StationId")
            name = _find(el, "Name", "StationName", "CallLetters",
                         "DisplayName", "Description")
            if sid:
                results.append({"id": sid, "name": name or sid})
    return results

def _sf_call_get_station_full(sf_url: str, station_id: str, timeout: int) -> dict:
    """Call GetStationFull on the Status Feed; return parsed state dict."""
    ns   = _ns(sf_url, timeout)
    body = f"<stationId>{station_id}</stationId>"
    root, raw = _soap_call(sf_url, "GetStationFull", body, ns, timeout)

    title       = _find(root, "Title", "MediaTitle", "CartTitle", "SongTitle", "Name")
    artist      = _find(root, "Artist", "MediaArtist", "ArtistName", "Performer")
    cart        = _find(root, "CartNumber", "Cart", "CartNum", "MediaID", "MediaId")
    category    = _find(root, "CategoryName", "Category", "MediaCategory",
                        "Type", "CartType", "CategoryType")
    duration_s  = _find(root, "Duration", "TotalDuration", "LengthSeconds",
                        "DurationSeconds", "TotalSeconds")
    time_left_s = _find(root, "TimeLeft", "RemainingSeconds", "TimeRemaining",
                        "SecondsRemaining", "TimeRemainingSeconds")

    try:    dur  = float(duration_s)  if duration_s  else 0.0
    except (ValueError, TypeError): dur  = 0.0
    try:    left = float(time_left_s) if time_left_s else 0.0
    except (ValueError, TypeError): left = 0.0

    raw_cat = category.upper()
    with _cfg_lock:
        spot_cats = [c.upper() for c in _zetta_cfg.get("spot_categories", [])]
    is_spot = any(sc in raw_cat or raw_cat in sc for sc in spot_cats) if spot_cats else False

    return {
        "station_id":   station_id,
        "title":        title,
        "artist":       artist,
        "cart":         cart,
        "category":     category,
        "raw_category": raw_cat,
        "duration":     dur,
        "time_left":    left,
        "is_spot":      is_spot,
        "ts":           time.time(),
        "error":        "",
        "_raw":         raw,
        "_source":      "status_feed",
    }

# ── Zetta method calls ────────────────────────────────────────────────────────

# Newer Zetta SOAP method names (tried in order; first success wins)
_NOW_PLAYING_METHODS = [
    "GetNowPlaying",
    "GetCurrentItem",
    "GetNowPlayingExtended",
    "GetCurrentlyPlaying",
    "GetCurrentAndNext",
]

_STATIONS_METHODS = [
    "GetStations",
    "GetStationList",
    "GetAllStations",
]

def _call_now_playing(url: str, ns: str, station_id: str, timeout: int) -> dict:
    """Try known GetNowPlaying variants; return parsed dict."""
    body = f"<StationID>{station_id}</StationID>"
    last_err = "No method succeeded"
    for method in _NOW_PLAYING_METHODS:
        try:
            root, raw = _soap_call(url, method, body, ns, timeout)
            return _parse_now_playing(root, station_id, raw)
        except Exception as e:
            last_err = f"{method}: {e}"
    return {"station_id": station_id, "error": last_err, "is_spot": False,
            "ts": time.time(), "_raw": ""}

def _call_get_stations(url: str, ns: str, timeout: int) -> list:
    """Try known GetStations variants; return list of {id, name}."""
    last_err = "No method succeeded"
    for method in _STATIONS_METHODS:
        try:
            root, _ = _soap_call(url, method, "", ns, timeout)
            results  = []
            # Look for Station / StationInfo elements containing an ID field
            for el in root.iter():
                tag = el.tag.split("}")[-1]
                if tag in ("Station", "StationInfo", "StationDetails"):
                    sid  = _find(el, "StationID", "Id", "ID", "StationId")
                    name = _find(el, "StationName", "Name", "CallLetters",
                                 "DisplayName", "Description")
                    if sid:
                        results.append({"id": sid, "name": name or sid})
            if results:
                return results
        except Exception as e:
            last_err = f"{method}: {e}"
    return [{"id": "error", "name": last_err}]

def _parse_now_playing(root: ET.Element, station_id: str, raw: str) -> dict:
    """Extract useful fields from a GetNowPlaying-style response."""
    title    = _find(root, "Title", "MediaTitle", "SongTitle", "Name", "CartTitle")
    artist   = _find(root, "Artist", "MediaArtist", "ArtistName", "Performer")
    cart     = _find(root, "CartNumber", "CartNum", "Cart", "MediaID", "MediaId")
    category = _find(root, "CategoryName", "Category", "MediaCategory",
                     "Type", "CartType", "CategoryType")
    duration_s = _find(root, "Duration", "TotalDuration", "LengthSeconds",
                       "DurationSeconds", "TotalSeconds")
    time_left_s = _find(root, "TimeLeft", "RemainingSeconds", "TimeRemaining",
                        "SecondsRemaining", "TimeRemainingSeconds")

    try:    dur  = float(duration_s)  if duration_s  else 0.0
    except (ValueError, TypeError): dur  = 0.0
    try:    left = float(time_left_s) if time_left_s else 0.0
    except (ValueError, TypeError): left = 0.0

    raw_cat = category.upper()
    with _cfg_lock:
        spot_cats = [c.upper() for c in _zetta_cfg.get("spot_categories", [])]
    is_spot = any(sc in raw_cat or raw_cat in sc for sc in spot_cats) if spot_cats else False

    return {
        "station_id":   station_id,
        "title":        title,
        "artist":       artist,
        "cart":         cart,
        "category":     category,
        "raw_category": raw_cat,
        "duration":     dur,
        "time_left":    left,
        "is_spot":      is_spot,
        "ts":           time.time(),
        "error":        "",
        "_raw":         raw,       # kept for debug page, stripped from /api/zetta/status
    }

# ── Poller ────────────────────────────────────────────────────────────────────

def _poll_loop(monitor):
    global _sf_health
    _log.info("[Zetta] Poller started")
    _sf_check_counter = 0  # check Status Feed health every N polls
    _SF_CHECK_EVERY   = 6  # re-probe IsAlive every 6 polls (~60 s at 10 s interval)

    while not _poller_stop.is_set():
        try:
            with _cfg_lock:
                url      = _zetta_cfg.get("url", "").strip()
                stations = list(_zetta_cfg.get("stations", []))
                interval = int(_zetta_cfg.get("poll_interval", 10))
                timeout  = int(_zetta_cfg.get("timeout", 6))

            sf_url = _effective_sf_url()

            # Probe Status Feed health periodically
            if sf_url:
                _sf_check_counter += 1
                if _sf_check_counter >= _SF_CHECK_EVERY or _sf_health["ok"] is None:
                    _sf_check_counter = 0
                    alive = _sf_call_is_alive(sf_url, timeout)
                    _sf_health = {"ok": alive, "error": "" if alive else "IsAlive returned False or unreachable", "ts": time.time()}
                    if alive:
                        _log.debug("[Zetta] Status Feed alive at %s", sf_url)
                    else:
                        _log.warning("[Zetta] Status Feed unavailable at %s — falling back to main service", sf_url)
            else:
                _sf_health = {"ok": False, "error": "No Status Feed URL configured or derivable", "ts": time.time()}

            use_sf = bool(sf_url) and _sf_health.get("ok")

            if url and stations:
                ns_main = _ns(url, timeout) if not use_sf else ""
                for st in stations:
                    sid = str(st.get("id", "")).strip()
                    if not sid:
                        continue
                    try:
                        if use_sf:
                            info = _sf_call_get_station_full(sf_url, sid, timeout)
                        else:
                            info = _call_now_playing(url, ns_main, sid, timeout)
                        with _state_lock:
                            _zetta_state[sid] = {**info, "name": st.get("name", sid)}
                    except Exception as e:
                        # If Status Feed call failed, retry once via main service
                        if use_sf:
                            try:
                                ns_main = ns_main or _ns(url, timeout)
                                info = _call_now_playing(url, ns_main, sid, timeout)
                                info["_source"] = "main_service_fallback"
                                with _state_lock:
                                    _zetta_state[sid] = {**info, "name": st.get("name", sid)}
                                continue
                            except Exception:
                                pass
                        with _state_lock:
                            _zetta_state[sid] = {
                                "station_id": sid, "name": st.get("name", sid),
                                "is_spot": False, "error": str(e),
                                "ts": time.time(), "_raw": "",
                            }
        except Exception as e:
            _log.error(f"[Zetta] Poller error: {e}")

        _poller_stop.wait(interval)
    _log.info("[Zetta] Poller stopped")

# ── Config helpers ────────────────────────────────────────────────────────────

def _load_cfg():
    global _zetta_cfg
    if _cfg_path and os.path.exists(_cfg_path):
        try:
            with open(_cfg_path) as f:
                loaded = json.load(f)
            with _cfg_lock:
                _zetta_cfg = {**_DEFAULT_CFG, **loaded}
            _log.info(f"[Zetta] Config loaded from {_cfg_path}")
        except Exception as e:
            _log.warning(f"[Zetta] Config load error: {e}")

def _save_cfg():
    if not _cfg_path:
        return
    with _cfg_lock:
        data = dict(_zetta_cfg)
    try:
        with open(_cfg_path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        _log.warning(f"[Zetta] Config save error: {e}")

# ── Page template ─────────────────────────────────────────────────────────────

_PAGE_TPL = """\
<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<title>Zetta — SignalScope</title>
<style nonce="{{csp_nonce()}}">
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);font-family:system-ui,sans-serif;font-size:14px}
.topbar{background:var(--sur);border-bottom:1px solid var(--bor);padding:10px 18px;
  display:flex;align-items:center;gap:12px}
.topbar a{color:var(--mu);text-decoration:none;font-size:13px}.topbar a:hover{color:var(--tx)}
h1{font-size:17px;font-weight:700}
.page{padding:20px;max-width:1100px;margin:0 auto}
.sec{background:var(--sur);border:1px solid var(--bor);border-radius:8px;padding:16px;margin-bottom:16px}
.sec-hdr{font-size:11px;font-weight:700;color:var(--mu);text-transform:uppercase;
  letter-spacing:.06em;margin-bottom:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:12px}
.card{background:rgba(255,255,255,.03);border:1px solid var(--bor);border-radius:7px;padding:14px;
  transition:border-color .3s}
.card.spot{border-color:var(--wn);background:rgba(245,158,11,.07)}
.card.music{border-color:var(--ok);background:rgba(34,197,94,.04)}
.card.err{border-color:var(--al);background:rgba(239,68,68,.05)}
.card-hdr{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.stn-name{font-weight:700;font-size:14px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.badge{font-size:10px;font-weight:700;padding:2px 7px;border-radius:999px;white-space:nowrap}
.badge.spot{background:rgba(245,158,11,.2);color:var(--wn);border:1px solid rgba(245,158,11,.3)}
.badge.music{background:rgba(34,197,94,.15);color:var(--ok);border:1px solid rgba(34,197,94,.25)}
.badge.other{background:rgba(138,164,200,.1);color:var(--mu);border:1px solid var(--bor)}
.badge.err{background:rgba(239,68,68,.15);color:var(--al);border:1px solid rgba(239,68,68,.25)}
.track-title{font-size:14px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.track-artist{font-size:12px;color:var(--mu);margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.meta-row{display:flex;gap:10px;margin-top:6px;font-size:11px;color:var(--mu);flex-wrap:wrap}
.prog-wrap{margin-top:8px;height:3px;background:var(--bor);border-radius:2px}
.prog-bar{height:3px;border-radius:2px;background:var(--acc)}
.prog-bar.spot{background:var(--wn)}
label{font-size:12px;color:var(--mu);display:block;margin-bottom:3px;margin-top:10px}
label:first-child{margin-top:0}
input,select,textarea{background:#173a69;border:1px solid var(--bor);color:var(--tx);
  padding:6px 10px;border-radius:5px;font-size:13px;width:100%}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--acc)}
textarea{font-family:monospace;font-size:11px;resize:vertical}
.row{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:4px}
.row .f{flex:1;min-width:140px}
.btn{padding:6px 14px;border:none;border-radius:5px;font-size:13px;font-weight:600;
  cursor:pointer;transition:.15s;white-space:nowrap}
.bp{background:var(--acc);color:#fff}.bp:hover{filter:brightness(1.1)}
.bg{background:rgba(255,255,255,.05);color:var(--tx);border:1px solid var(--bor)}.bg:hover{border-color:var(--mu)}
.br{background:rgba(239,68,68,.12);color:var(--al);border:1px solid rgba(239,68,68,.2)}
.btn-row{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.st-row{display:flex;gap:6px;align-items:center;margin-bottom:5px;
  background:var(--bg);border:1px solid var(--bor);border-radius:5px;padding:7px 9px}
.st-row input{flex:1}
.hint{font-size:11px;color:var(--mu);margin-top:3px}
#msg{font-size:12px;margin-top:8px;min-height:14px}
.tabs{display:flex;gap:2px;margin-bottom:12px}
.tab{padding:5px 14px;border-radius:5px 5px 0 0;border:1px solid var(--bor);
  border-bottom:none;cursor:pointer;font-size:12px;font-weight:600;color:var(--mu);background:var(--bg)}
.tab.active{background:var(--sur);color:var(--tx)}
.tab-body{display:none}.tab-body.active{display:block}
.methods-list{font-family:monospace;font-size:11px;color:var(--mu);
  column-count:3;column-gap:12px;line-height:1.7}
</style>
</head><body>
<div class="topbar">
  <a href="/hub">&#8592; Hub</a>
  <h1>&#128251; Zetta Integration</h1>
  <span id="poll-age" style="margin-left:auto;font-size:11px;color:var(--mu)"></span>
</div>
<div class="page">

  <!-- Now Playing -->
  <div class="sec">
    <div class="sec-hdr">Now Playing</div>
    <div class="grid" id="np-grid"><div style="color:var(--mu);font-size:13px">Loading&#8230;</div></div>
  </div>

  <!-- Settings + Discovery -->
  <div class="sec">
    <div class="tabs">
      <div class="tab active" data-tab="t-settings">&#x2699; Settings</div>
      <div class="tab" data-tab="t-debug">&#x1F50D; Debug / Discovery</div>
    </div>

    <!-- Settings tab -->
    <div class="tab-body active" id="t-settings">
      <div class="row">
        <div class="f">
          <label>Zetta SOAP Service URL</label>
          <input id="cfg-url" placeholder="http://zetta-server/ZettaService/ZettaService.asmx" value="{{url}}" required spellcheck="false" autocomplete="off">
          <div class="hint">Full URL to the .asmx endpoint (no ?wsdl)</div>
        </div>
      </div>
      <div class="row">
        <div class="f">
          <label>Status Feed URL <span style="color:var(--mu);font-weight:400">(optional — auto-derived if blank)</span></label>
          <input id="cfg-sf-url" placeholder="http://zetta-server:3132/StatusFeed" value="{{sf_url}}">
          <div class="hint">
            Port 3132 service used for <strong>GetStationFull</strong> — richer category data and more reliable spot detection.
            Leave blank to auto-derive from the SOAP host above.
            Status: <span id="sf-health-badge">{{sf_health_badge}}</span>
          </div>
        </div>
      </div>
      <div class="row">
        <div class="f" style="max-width:160px">
          <label>Poll interval (s)</label>
          <input type="number" id="cfg-interval" min="5" max="120" value="{{interval}}">
        </div>
        <div class="f" style="max-width:160px">
          <label>Request timeout (s)</label>
          <input type="number" id="cfg-timeout" min="2" max="30" value="{{timeout}}">
        </div>
      </div>
      <div>
        <label>Spot / commercial break categories (comma-separated, case-insensitive)</label>
        <input id="cfg-spot-cats" value="{{spot_cats}}"
               placeholder="SPOT, COMMERCIAL, PROMO">
        <div class="hint">
          Zetta category names that should trigger an AD BREAK flag.
          Check the Debug tab to see what your server returns for <em>CategoryName</em>.
        </div>
      </div>
      <div style="margin-top:14px">
        <label style="margin-bottom:6px">Stations</label>
        <div id="stations-list">{{stations_html}}</div>
        <button class="btn bg" id="btn-add-station" style="margin-top:7px">&#xFF0B; Add Station</button>
      </div>
      <div class="btn-row">
        <button class="btn bp"  id="btn-save">&#x1F4BE; Save</button>
        <button class="btn bg"  id="btn-discover">&#x1F50D; Discover Stations</button>
        <button class="btn bg"  id="btn-test">&#x26A1; Test Connection</button>
      </div>
      <div id="msg"></div>
    </div>

    <!-- Debug tab -->
    <div class="tab-body" id="t-debug">
      <div>
        <label>Service URL (overrides saved)</label>
        <input id="dbg-url" placeholder="http://zetta-server/ZettaService/ZettaService.asmx">
      </div>
      <div style="margin-top:10px">
        <label>SOAP Method</label>
        <input id="dbg-method" placeholder="GetNowPlaying">
      </div>
      <div style="margin-top:10px">
        <label>Body XML (inner content only, e.g. &lt;StationID&gt;1&lt;/StationID&gt;)</label>
        <textarea id="dbg-body" rows="3" placeholder="<StationID>1</StationID>"></textarea>
      </div>
      <div class="btn-row">
        <button class="btn bg" id="btn-dbg-call">&#x25B6; Call</button>
        <button class="btn bg" id="btn-dbg-wsdl">&#x1F4C4; Show WSDL Methods</button>
      </div>
      <div id="dbg-msg" style="font-size:12px;margin-top:8px;min-height:14px"></div>
      <div id="dbg-methods" style="margin-top:8px"></div>
      <label style="margin-top:12px">Raw response</label>
      <textarea id="dbg-response" rows="14" readonly></textarea>
    </div>
  </div>

  <!-- API reference -->
  <div class="sec">
    <div class="sec-hdr">API Reference</div>
    <div style="font-size:12px;color:var(--mu);line-height:1.8">
      <code style="color:var(--tx)">GET /api/zetta/status</code>
      &mdash; JSON: per-station playout state including <code>is_spot</code>, <code>title</code>, <code>artist</code>,
      <code>category</code>, <code>cart</code>, <code>duration</code>, <code>time_left</code><br>
      Can be queried by other plugins or external tools to gate on commercial breaks.
    </div>
  </div>

</div><!-- /page -->

<script nonce="{{csp_nonce()}}">
// ── CSRF / fetch helpers ──────────────────────────────────────────────────────
function _csrf(){
  return (document.querySelector('meta[name="csrf-token"]')||{}).content
      || (document.cookie.match(/(?:^|;\\s*)csrf_token=([^;]+)/)||[])[1]||'';
}
function _f(url,opts){
  opts=opts||{};
  opts.headers=Object.assign({'Content-Type':'application/json','X-CSRFToken':_csrf()},opts.headers||{});
  opts.credentials='same-origin';
  return fetch(url,opts);
}
function _esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function _msg(id,txt,col){var el=document.getElementById(id);el.style.color=col||'var(--mu)';el.textContent=txt;}

// ── Tabs ──────────────────────────────────────────────────────────────────────
document.querySelectorAll('.tab').forEach(function(t){
  t.addEventListener('click',function(){
    document.querySelectorAll('.tab').forEach(function(x){x.classList.remove('active');});
    document.querySelectorAll('.tab-body').forEach(function(x){x.classList.remove('active');});
    t.classList.add('active');
    document.getElementById(t.dataset.tab).classList.add('active');
  });
});

// ── Station rows ──────────────────────────────────────────────────────────────
function _stRow(id,name){
  var d=document.createElement('div');d.className='st-row';
  d.innerHTML='<input class="st-id"   placeholder="Station ID"   value="'+_esc(id||'')+'" style="max-width:110px">'
             +'<input class="st-name" placeholder="Friendly name" value="'+_esc(name||'')+'">'
             +'<button class="btn br st-rm-btn">&#x2715;</button>';
  return d;
}
document.getElementById('btn-add-station').addEventListener('click',function(){
  document.getElementById('stations-list').appendChild(_stRow('',''));
});
// Delegated listener for remove buttons (avoids CSP-blocked onclick attributes)
document.getElementById('stations-list').addEventListener('click',function(e){
  var btn=e.target.closest('.st-rm-btn');
  if(btn) btn.closest('.st-row').remove();
});

// ── Save settings ─────────────────────────────────────────────────────────────
document.getElementById('btn-save').addEventListener('click',function(){
  var stations=[];
  document.querySelectorAll('.st-row').forEach(function(r){
    var id=r.querySelector('.st-id').value.trim();
    var nm=r.querySelector('.st-name').value.trim();
    if(id) stations.push({id:id,name:nm||id});
  });
  var p={
    url:       document.getElementById('cfg-url').value.trim(),
    sf_url:    document.getElementById('cfg-sf-url').value.trim(),
    interval:  parseInt(document.getElementById('cfg-interval').value)||10,
    timeout:   parseInt(document.getElementById('cfg-timeout').value)||6,
    spot_cats: document.getElementById('cfg-spot-cats').value,
    stations:  stations,
  };
  _msg('msg','Saving…');
  var _sv=document.getElementById('btn-save');
  _btnLoad(_sv);
  _f('/api/zetta/settings',{method:'POST',body:JSON.stringify(p)})
    .then(function(r){return r.json();})
    .then(function(d){
      _btnReset(_sv);
      if(d.ok)_msg('msg','Saved.','var(--ok)');
      else _msg('msg','Error: '+(d.error||'?'),'var(--al)');
    }).catch(function(e){_btnReset(_sv);_msg('msg',''+e,'var(--al)');});
});

// ── Discover stations ─────────────────────────────────────────────────────────
document.getElementById('btn-discover').addEventListener('click',function(){
  var _dv=this;
  var url=document.getElementById('cfg-url').value.trim();
  var sfUrl=document.getElementById('cfg-sf-url').value.trim();
  if(!url){_msg('msg','Enter the Zetta SOAP URL first.','var(--wa)');return;}
  _msg('msg','Querying Zetta for station list…');
  _btnLoad(_dv);
  _f('/api/zetta/discover',{method:'POST',body:JSON.stringify({url:url,sf_url:sfUrl})})
    .then(function(r){return r.json();})
    .then(function(d){
      _btnReset(_dv);
      if(d.stations&&d.stations.length&&d.stations[0].id!=='error'){
        var src=d.source==='status_feed'?' (via Status Feed)':d.source==='main_service'?' (via main service)':'';
        var list=document.getElementById('stations-list');
        list.innerHTML='';
        d.stations.forEach(function(s){list.appendChild(_stRow(s.id,s.name));});
        _msg('msg','Found '+d.stations.length+' station(s)'+src+'. Review names then Save.','var(--ok)');
      } else {
        _msg('msg',(d.error||'No stations returned — enter them manually.'),'var(--wn)');
      }
    }).catch(function(e){_btnReset(_dv);_msg('msg',''+e,'var(--al)');});
});

// ── Test connection ───────────────────────────────────────────────────────────
document.getElementById('btn-test').addEventListener('click',function(){
  var _tv=this;
  var url=document.getElementById('cfg-url').value.trim();
  var sfUrl=document.getElementById('cfg-sf-url').value.trim();
  if(!url){_msg('msg','Enter the Zetta SOAP URL first.','var(--wa)');return;}
  _msg('msg','Testing…');
  _btnLoad(_tv);
  _f('/api/zetta/test',{method:'POST',body:JSON.stringify({url:url,sf_url:sfUrl})})
    .then(function(r){return r.json();})
    .then(function(d){
      _btnReset(_tv);
      if(d.ok){
        var sfPart=d.sf_ok===true?' · ✓ Status Feed alive ('+_esc(d.sf_url)+')'
                  :d.sf_ok===false?' · ⚠ Status Feed unreachable ('+_esc(d.sf_url)+')':'';
        _msg('msg','✓ Connected. Namespace: '+d.namespace+sfPart,'var(--ok)');
      } else {
        _msg('msg','✗ '+d.error,'var(--al)');
      }
    }).catch(function(e){_btnReset(_tv);_msg('msg',''+e,'var(--al)');});
});

// ── Debug — raw SOAP call ─────────────────────────────────────────────────────
document.getElementById('btn-dbg-call').addEventListener('click',function(){
  var _dc=this;
  var url=document.getElementById('dbg-url').value.trim()||document.getElementById('cfg-url').value.trim();
  var method=document.getElementById('dbg-method').value.trim();
  var body=document.getElementById('dbg-body').value.trim();
  if(!url){_msg('dbg-msg','Enter a Service URL (or fill in the Settings URL).','var(--wa)');return;}
  if(!method){_msg('dbg-msg','Enter a SOAP Method name.','var(--wa)');return;}
  _msg('dbg-msg','Calling '+method+'…');
  document.getElementById('dbg-response').value='';
  _btnLoad(_dc);
  _f('/api/zetta/debug',{method:'POST',body:JSON.stringify({url:url,method:method,body:body})})
    .then(function(r){return r.json();})
    .then(function(d){
      _btnReset(_dc);
      if(d.ok){
        _msg('dbg-msg','✓ Success (HTTP 200)','var(--ok)');
        document.getElementById('dbg-response').value=d.raw||'(empty response)';
      } else {
        _msg('dbg-msg','✗ '+d.error,'var(--al)');
        document.getElementById('dbg-response').value=d.raw||'';
      }
    }).catch(function(e){_btnReset(_dc);_msg('dbg-msg',''+e,'var(--al)');});
});

// ── Debug — list WSDL methods ─────────────────────────────────────────────────
document.getElementById('btn-dbg-wsdl').addEventListener('click',function(){
  var _dw=this;
  var url=document.getElementById('dbg-url').value.trim()||document.getElementById('cfg-url').value.trim();
  if(!url){_msg('dbg-msg','Enter a Service URL (or fill in the Settings URL).','var(--wa)');return;}
  _msg('dbg-msg','Fetching WSDL…');
  document.getElementById('dbg-methods').innerHTML='';
  _btnLoad(_dw);
  _f('/api/zetta/wsdl_methods',{method:'POST',body:JSON.stringify({url:url})})
    .then(function(r){return r.json();})
    .then(function(d){
      _btnReset(_dw);
      if(d.methods&&d.methods.length){
        _msg('dbg-msg','Namespace: '+d.namespace+'   ('+d.methods.length+' operations — click one to use it)','var(--ok)');
        document.getElementById('dbg-methods').innerHTML=
          '<div class="methods-list">'+d.methods.map(function(m){
            return '<span class="wsdl-method" data-method="'+_esc(m)+'" style="cursor:pointer;color:var(--ac)">'+_esc(m)+'</span>';
          }).join('<br>')+'</div>';
      } else {
        _msg('dbg-msg',d.error||'No operations found in WSDL.','var(--wa)');
      }
    }).catch(function(e){_btnReset(_dw);_msg('dbg-msg',''+e,'var(--al)');});
});
// Delegated click — select a WSDL method into the method input
document.getElementById('dbg-methods').addEventListener('click',function(e){
  var sp=e.target.closest('.wsdl-method');
  if(sp) document.getElementById('dbg-method').value=sp.dataset.method;
});

// ── Now playing cards ─────────────────────────────────────────────────────────
function _fmt(sec){
  sec=Math.max(0,Math.round(sec||0));
  var m=Math.floor(sec/60),s=sec%60;
  return m+':'+(s<10?'0':'')+s;
}
function _prog(dur,left,is_spot){
  if(!dur||dur<=0)return '';
  var pct=Math.max(0,Math.min(100,((dur-left)/dur)*100));
  return '<div class="prog-wrap"><div class="prog-bar'+(is_spot?' spot':'')+'" style="width:'+pct.toFixed(1)+'%"></div></div>';
}
function _badge(cat,is_spot,has_err){
  if(has_err)return '<span class="badge err">Error</span>';
  if(is_spot)return '<span class="badge spot">&#x26A1; Ad Break</span>';
  if(cat)    return '<span class="badge music">&#x266B; '+_esc(cat)+'</span>';
             return '<span class="badge other">Unknown</span>';
}

var _lastPoll=0;
function _refreshNowPlaying(){
  _f('/api/zetta/status').then(function(r){return r.json();})
    .then(function(d){
      _lastPoll=Date.now();
      // Update Status Feed health badge if present
      var sfBadge=document.getElementById('sf-health-badge');
      if(sfBadge&&d.sf_health!=null){
        if(d.sf_health.ok)sfBadge.innerHTML='<span style="color:var(--ok)">&#x2714; Status Feed connected</span>';
        else if(d.sf_health.ok===false)sfBadge.innerHTML='<span style="color:var(--wn)" title="'+_esc(d.sf_health.error||'')+'">&#x26A0; Unavailable — using main service</span>';
        else sfBadge.innerHTML='<span style="color:var(--mu)">Checking…</span>';
      }
      var grid=document.getElementById('np-grid');
      var stns=Object.values(d.stations||{});
      if(!stns.length){
        grid.innerHTML='<div style="color:var(--mu);font-size:13px">No stations configured — add them in Settings.</div>';
        return;
      }
      grid.innerHTML=stns.map(function(s){
        var err=!!(s.error);
        var cls=err?'err':s.is_spot?'spot':'music';
        var src=s._source==='status_feed'?'<span title="Data via Zetta Status Feed" style="font-size:10px;color:var(--mu)">&#x2022; SF</span>':
                s._source==='main_service_fallback'?'<span title="Status Feed failed — using main service" style="font-size:10px;color:var(--wn)">&#x26A0; fallback</span>':'';
        var body=err
          ? '<div style="color:var(--al);font-size:12px">'+_esc(s.error)+'</div>'
          : ('<div class="track-title">'+_esc(s.title||'—')+'</div>'
            +'<div class="track-artist">'+_esc(s.artist||'\u00a0')+'</div>'
            +'<div class="meta-row">'
            +(s.cart?'<span>Cart '+_esc(s.cart)+'</span>':'')
            +(s.duration?'<span>'+_fmt(s.time_left)+' / '+_fmt(s.duration)+'</span>':'')
            +(s.raw_category&&!s.is_spot?'<span>'+_esc(s.raw_category)+'</span>':'')
            +'</div>'
            +_prog(s.duration,s.time_left,s.is_spot));
        return '<div class="card '+cls+'">'
          +'<div class="card-hdr"><span class="stn-name">'+_esc(s.name||s.station_id)+'</span>'
          +_badge(s.raw_category,s.is_spot,err)+src+'</div>'
          +body+'</div>';
      }).join('');
    }).catch(function(){});
}
_refreshNowPlaying();
setInterval(_refreshNowPlaying, 5000);

// Poll-age counter
setInterval(function(){
  if(!_lastPoll)return;
  document.getElementById('poll-age').textContent='Updated '+Math.round((Date.now()-_lastPoll)/1000)+'s ago';
},1000);
</script>
</body></html>"""


# ── register ──────────────────────────────────────────────────────────────────

def register(app, ctx):
    global _cfg_path

    login_required = ctx["login_required"]
    csrf_protect   = ctx["csrf_protect"]
    monitor        = ctx["monitor"]

    _cfg_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "zetta_cfg.json"
    )
    _load_cfg()

    # Start background poller (daemon so it dies with the process)
    _poller_stop.clear()
    t = threading.Thread(target=_poll_loop, args=(monitor,),
                         daemon=True, name="ZettaPoller")
    t.start()

    # ── Page ──────────────────────────────────────────────────────────────────

    @app.get("/hub/zetta")
    @login_required
    def zetta_page():
        from flask import render_template_string
        from markupsafe import Markup
        with _cfg_lock:
            url       = _zetta_cfg.get("url", "")
            sf_url    = _zetta_cfg.get("status_feed_url", "")
            interval  = _zetta_cfg.get("poll_interval", 10)
            timeout   = _zetta_cfg.get("timeout", 6)
            spot_cats = ", ".join(_zetta_cfg.get("spot_categories", []))
            stations  = list(_zetta_cfg.get("stations", []))

        sf_ok = _sf_health.get("ok")
        if sf_ok is True:
            sf_badge = Markup('<span style="color:var(--ok)">&#x2714; Status Feed connected</span>')
        elif sf_ok is False:
            sf_badge = Markup('<span style="color:var(--wn)">&#x26A0; Unavailable — using main service</span>')
        else:
            sf_badge = Markup('<span style="color:var(--mu)">Not yet checked</span>')

        def _e(s):
            return (str(s)
                    .replace("&", "&amp;").replace("<", "&lt;")
                    .replace(">", "&gt;").replace('"', "&quot;"))

        rows = "".join(
            f'<div class="st-row">'
            f'<input class="st-id"   placeholder="Station ID"    value="{_e(s["id"])}"       style="max-width:110px">'
            f'<input class="st-name" placeholder="Friendly name" value="{_e(s.get("name",""))}">'
            f'<button class="btn br st-rm-btn">&#x2715;</button>'
            f'</div>'
            for s in stations
        )
        return render_template_string(
            _PAGE_TPL,
            url=url, sf_url=sf_url, interval=interval, timeout=timeout,
            spot_cats=spot_cats, stations_html=Markup(rows),
            sf_health_badge=sf_badge,
        )

    # ── Status API ────────────────────────────────────────────────────────────

    @app.get("/api/zetta/status")
    @login_required
    def zetta_status():
        from flask import jsonify
        with _cfg_lock:
            stns_cfg = {str(s["id"]): s for s in _zetta_cfg.get("stations", [])}
        with _state_lock:
            state = {k: dict(v) for k, v in _zetta_state.items()}

        merged = {}
        for sid, info in state.items():
            clean = {k: v for k, v in info.items() if k != "_raw"}
            clean["name"] = stns_cfg.get(sid, {}).get("name", sid)
            merged[sid] = clean
        for sid, scfg in stns_cfg.items():
            if sid not in merged:
                merged[sid] = {"station_id": sid, "name": scfg.get("name", sid),
                               "is_spot": False, "error": "No data yet"}
        return jsonify({"stations": merged, "ts": time.time(),
                        "sf_health": dict(_sf_health)})

    # ── Settings save ─────────────────────────────────────────────────────────

    @app.post("/api/zetta/settings")
    @login_required
    @csrf_protect
    def zetta_settings_save():
        from flask import request, jsonify
        data = request.get_json(silent=True) or {}
        with _cfg_lock:
            _zetta_cfg["url"]             = str(data.get("url",    "")).strip()
            _zetta_cfg["status_feed_url"] = str(data.get("sf_url", "")).strip()
            _zetta_cfg["poll_interval"]   = max(5, min(120, int(data.get("interval", 10) or 10)))
            _zetta_cfg["timeout"]         = max(2, min(30,  int(data.get("timeout",  6)  or 6)))
            raw_cats = str(data.get("spot_cats", "")).split(",")
            _zetta_cfg["spot_categories"] = [c.strip().upper() for c in raw_cats if c.strip()]
            _zetta_cfg["stations"] = [
                {"id":   str(s.get("id",   "")).strip(),
                 "name": str(s.get("name", "")).strip()}
                for s in data.get("stations", []) if str(s.get("id", "")).strip()
            ]
        _save_cfg()
        _wsdl_cache.clear()   # force namespace re-detection
        # Reset SF health so it re-probes on next poll cycle
        _sf_health.update({"ok": None, "error": "", "ts": 0.0})
        return jsonify({"ok": True})

    # ── Discover stations ─────────────────────────────────────────────────────

    @app.post("/api/zetta/discover")
    @login_required
    @csrf_protect
    def zetta_discover():
        from flask import request, jsonify
        data    = request.get_json(silent=True) or {}
        url     = str(data.get("url", "")).strip()
        sf_url  = str(data.get("sf_url", "")).strip() or _derive_status_feed_url(url)
        if not url:
            return jsonify({"error": "No URL provided", "stations": []})
        with _cfg_lock:
            timeout = _zetta_cfg.get("timeout", 6)

        # Try Status Feed GetStations first (richer/faster)
        if sf_url:
            try:
                stations = _sf_call_get_stations(sf_url, timeout)
                if stations:
                    return jsonify({"stations": stations, "namespace": _ns(sf_url, timeout),
                                    "source": "status_feed"})
            except Exception as sf_err:
                _log.debug("[Zetta] Status Feed discover failed (%s), trying main service", sf_err)

        # Fall back to main SOAP service
        try:
            ns       = _ns(url, timeout)
            stations = _call_get_stations(url, ns, timeout)
            return jsonify({"stations": stations, "namespace": ns, "source": "main_service"})
        except Exception as e:
            return jsonify({"error": str(e), "stations": []})

    # ── Test connection ───────────────────────────────────────────────────────

    @app.post("/api/zetta/test")
    @login_required
    @csrf_protect
    def zetta_test():
        from flask import request, jsonify
        data    = request.get_json(silent=True) or {}
        url     = str(data.get("url",    "")).strip()
        sf_url  = str(data.get("sf_url", "")).strip() or _derive_status_feed_url(url)
        if not url:
            return jsonify({"ok": False, "error": "No URL provided"})
        with _cfg_lock:
            timeout = _zetta_cfg.get("timeout", 6)

        result = {"ok": False, "namespace": "", "sf_ok": None, "sf_url": sf_url}
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout):
                pass
            result["namespace"] = _wsdl_info(url, timeout).get("ns") or "(could not parse WSDL)"
            result["ok"] = True
        except Exception as e:
            return jsonify({"ok": False, "error": str(e), "sf_ok": None, "sf_url": sf_url})

        # Also probe Status Feed if derivable
        if sf_url:
            try:
                result["sf_ok"] = _sf_call_is_alive(sf_url, timeout)
            except Exception:
                result["sf_ok"] = False

        return jsonify(result)

    # ── Raw debug call ────────────────────────────────────────────────────────

    @app.post("/api/zetta/debug")
    @login_required
    @csrf_protect
    def zetta_debug():
        from flask import request, jsonify
        data   = request.get_json(silent=True) or {}
        url    = str(data.get("url",    "")).strip()
        method = str(data.get("method", "")).strip()
        body   = str(data.get("body",   "")).strip()
        if not url or not method:
            return jsonify({"ok": False, "error": "url and method required", "raw": ""})
        with _cfg_lock:
            timeout = _zetta_cfg.get("timeout", 6)
        try:
            ns = _ns(url, timeout)
            _, raw = _soap_call(url, method, body, ns, timeout)
            # Pretty-print XML if possible
            try:
                import xml.dom.minidom
                pretty = xml.dom.minidom.parseString(raw.encode()).toprettyxml(indent="  ")
                # Strip the XML declaration line minidom adds
                raw = "\n".join(pretty.splitlines()[1:])
            except Exception:
                pass
            return jsonify({"ok": True, "raw": raw})
        except urllib.error.HTTPError as e:
            body_bytes = e.read()
            return jsonify({"ok": False, "error": f"HTTP {e.code}: {e.reason}",
                            "raw": body_bytes.decode("utf-8", errors="replace")})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e), "raw": ""})

    # ── WSDL methods list ─────────────────────────────────────────────────────

    @app.post("/api/zetta/wsdl_methods")
    @login_required
    @csrf_protect
    def zetta_wsdl_methods():
        from flask import request, jsonify
        data = request.get_json(silent=True) or {}
        url  = str(data.get("url", "")).strip()
        if not url:
            return jsonify({"error": "No URL", "methods": []})
        with _cfg_lock:
            timeout = _zetta_cfg.get("timeout", 6)
        # Clear cache so we always get a fresh WSDL
        _wsdl_cache.pop(url, None)
        info = _wsdl_info(url, timeout)
        if info["methods"]:
            return jsonify({"namespace": info["ns"], "methods": sorted(info["methods"])})
        return jsonify({"error": "No operations found in WSDL — check the URL",
                        "namespace": info["ns"], "methods": []})

    monitor.log("[Zetta] Plugin registered — dashboard at /hub/zetta")
