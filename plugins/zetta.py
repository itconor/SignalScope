# zetta.py — RCS Zetta SOAP sequencer monitor plugin for SignalScope
# Supports multiple independent Zetta instances, each with customisable
# colour schemes and a linked SignalScope broadcast chain.
#
# Requirements: none beyond the standard library (no zeep needed).
#
# Features
# --------
#  · Polls GetStationFull on Zetta StatusFeed for each configured station
#  · Full sequencer display: mode, status, GAP, ETM, now-playing, queue, progress
#  · Sequencer computer name (from SOAP data) displayed above GAP | ETM
#  · Multiple independent Zetta server instances
#  · Per-instance colour schemes (all state colours customisable)
#  · Per-instance broadcast chain association with live status badge
#  · Spot / ad-break detection by category name
#  · Debug SOAP explorer and station discovery

SIGNALSCOPE_PLUGIN = {
    "id":       "zetta",
    "label":    "Zetta",
    "url":      "/hub/zetta",
    "icon":     "📻",
    "hub_only": True,
    "version":  "2.1.1",
}

import json
import logging
import os
import threading
import time
import xml.etree.ElementTree as ET
import urllib.request
import urllib.error
from typing import Dict, List, Optional

_log = logging.getLogger("zetta_plugin")

# ── zeep (preferred SOAP client) ───────────────────────────────────────────────
_zeep = None

def _make_zeep_client(wsdl_url: str, base_url: str):
    """Create a zeep Client that rewrites any WSDL-internal hostname/port to the
    user-supplied base_url. This handles Zetta servers whose WSDL advertises an
    internal Windows hostname (e.g. rcs-bel-svr02.radioplayout.net) that is not
    resolvable from the SignalScope server."""
    from urllib.parse import urlparse, urlunparse
    user = urlparse(base_url)

    class _RewriteSession(_zeep.transports.Transport):
        def _rewrite(self, url):
            try:
                p = urlparse(url)
                if p.netloc and p.netloc != user.netloc:
                    url = urlunparse((
                        user.scheme or p.scheme,
                        user.netloc,
                        p.path, p.params, p.query, p.fragment
                    ))
            except Exception:
                pass
            return url

        def load(self, url):
            return super().load(self._rewrite(url))

        def post(self, address, message, headers):
            return super().post(self._rewrite(address), message, headers)

        def post_xml(self, address, envelope, headers):
            return super().post_xml(self._rewrite(address), envelope, headers)

    return _zeep.Client(wsdl=wsdl_url, transport=_RewriteSession())


def _ensure_zeep(log_fn=None) -> bool:
    """Import zeep, installing it via pip if missing. Returns True if available."""
    global _zeep
    if _zeep is not None:
        return True
    try:
        import zeep as _z
        _zeep = _z
        return True
    except ImportError:
        pass
    try:
        import subprocess, sys
        _msg = "[Zetta] zeep not installed — running pip install zeep..."
        (_log.info if log_fn is None else log_fn)(_msg)
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "zeep"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        import zeep as _z
        _zeep = _z
        _msg2 = "[Zetta] zeep installed successfully"
        (_log.info if log_fn is None else log_fn)(_msg2)
        return True
    except Exception as e:
        _msg3 = f"[Zetta] zeep install failed ({e}) — falling back to raw SOAP"
        (_log.warning if log_fn is None else log_fn)(_msg3)
        return False

# ── Constants ──────────────────────────────────────────────────────────────────
MODE_UNKNOWN     = 0
MODE_AUTOMATION  = 1
MODE_MANUAL      = 2
MODE_LIVE_ASSIST = 3
MODE_OFF_AIR     = 4

MODE_NAMES = {
    MODE_UNKNOWN:    "Unknown",
    MODE_AUTOMATION: "Automation",
    MODE_MANUAL:     "Manual",
    MODE_LIVE_ASSIST:"Live Assist",
    MODE_OFF_AIR:    "Off Air",
}

ST_QUEUED  = 1
ST_PLAYING = 2
STATION_STATE_NAMES = {ST_QUEUED: "QUEUED", ST_PLAYING: "PLAYING"}

ASSET_SONG = 1; ASSET_SPOT = 2; ASSET_LINK = 3
DISPLAYABLE_ASSET_TYPES = {ASSET_SONG, ASSET_SPOT, ASSET_LINK}
FILTERED_EVENT_TYPES    = {21, 23}

DEFAULT_SPOT_CATS = ["SPOT", "SPOTS", "COMMERCIAL", "COMMS", "PROMO", "PROMOS"]

DEFAULT_COLORS = {
    "box_bg":            "#111111",
    "box_border":        "#333333",
    "header_bg":         "#1a1a1a",
    "header_text":       "#ffffff",
    "meta_text":         "#aaaaaa",
    "seq_text":          "#17a8ff",
    "playing_live_bg":   "#000000",
    "playing_live_text": "#00cc00",
    "playing_auto_bg":   "#4a0080",
    "playing_auto_text": "#00dd00",
    "queued_bg":         "#1a1200",
    "queued_text":       "#c8a000",
    "off_air_bg":        "#aa0000",
    "off_air_text":      "#ff6666",
    "unknown_bg":        "#333333",
    "unknown_text":      "#888888",
    "progress_bar":      "#28a745",
    "progress_end":      "#dc3545",
}

# Color picker label / key pairs for the settings UI (grouped)
_COLOR_FIELDS = [
    ("Box background",          "box_bg"),
    ("Box border",              "box_border"),
    ("Header background",       "header_bg"),
    ("Header text",             "header_text"),
    ("Secondary text",          "meta_text"),
    ("Sequencer name text",     "seq_text"),
    ("Playing (Auto) — bg",     "playing_auto_bg"),
    ("Playing (Auto) — text",   "playing_auto_text"),
    ("Playing (Live) — bg",     "playing_live_bg"),
    ("Playing (Live) — text",   "playing_live_text"),
    ("Queued — bg",             "queued_bg"),
    ("Queued — text",           "queued_text"),
    ("Off Air — bg",            "off_air_bg"),
    ("Off Air — text",          "off_air_text"),
    ("Unknown — bg",            "unknown_bg"),
    ("Unknown — text",          "unknown_text"),
    ("Progress bar",            "progress_bar"),
    ("Progress bar (ending)",   "progress_end"),
]

# ── Module-level state ─────────────────────────────────────────────────────────
_cfg_lock         = threading.Lock()
_pollers:         Dict[str, "_InstancePoller"] = {}
_cfg_path         = ""
_wsdl_cache:      dict = {}
# Remote state: data pushed from client sites (keyed instance_id → station_id → data)
_remote_state:    Dict[str, Dict[str, dict]] = {}
_remote_state_lock = threading.Lock()
# Pending discovery requests: site_name → {url, evt, result}
_discover_pending: Dict[str, dict] = {}
_discover_lock    = threading.Lock()

# ── Utility formatters ─────────────────────────────────────────────────────────
def _fmt_dur(sec) -> str:
    s = int(sec or 0)
    if s <= 0: return "00:00"
    return f"{s//60:02d}:{s%60:02d}"

def _fmt_gap(sec) -> str:
    if sec is None: return "+00:00"
    s = int(abs(sec))
    return f"{'+'if sec>=0 else '-'}{s//60:02d}:{s%60:02d}"

def _get_css_state(mode: int, status: int) -> str:
    if mode == MODE_OFF_AIR:              return "z-off-air"
    if status == ST_PLAYING:
        if mode == MODE_LIVE_ASSIST:      return "z-live"
        return                                   "z-auto"
    return                                       "z-queued"

def _merge_colors(user: dict) -> dict:
    c = dict(DEFAULT_COLORS)
    if user:
        c.update({k: v for k, v in user.items() if k in DEFAULT_COLORS})
    return c

# ── Minimal SOAP client (no external deps) ────────────────────────────────────
_SOAP_ENV_11 = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"'
    ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
    ' xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
    "<soap:Body>"
    '<{method} xmlns="{ns}">{body}</{method}>'
    "</soap:Body></soap:Envelope>"
)

_SOAP_ENV_12 = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope"'
    ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
    ' xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
    "<soap12:Body>"
    '<{method} xmlns="{ns}">{body}</{method}>'
    "</soap12:Body></soap12:Envelope>"
)

def _soap_call(url: str, method: str, body_xml: str, ns: str, timeout: int = 6):
    # Try SOAP 1.2 first (application/soap+xml), fall back to SOAP 1.1 (text/xml)
    for content_type, envelope, action_header in [
        ("application/soap+xml; charset=utf-8",
         _SOAP_ENV_12, {"Content-Type": "application/soap+xml; charset=utf-8; action=\"{ns}{method}\""}),
        ("text/xml; charset=utf-8",
         _SOAP_ENV_11, {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '"{ns}{method}"'}),
    ]:
        env = envelope.format(method=method, ns=ns, body=body_xml)
        headers = {k: v.format(ns=ns, method=method) for k, v in action_header.items()}
        req = urllib.request.Request(url, data=env.encode("utf-8"),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
            return ET.fromstring(raw), raw.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 415:
                continue   # wrong SOAP version — try the other one
            raise
    raise RuntimeError("Both SOAP 1.1 and 1.2 failed for this endpoint")

def _wsdl_info(url: str, timeout: int = 6) -> dict:
    if url in _wsdl_cache:
        return _wsdl_cache[url]
    sep = "&" if "?" in url else "?"
    info = {"ns": "", "methods": [], "endpoint": url}
    try:
        with urllib.request.urlopen(url + sep + "wsdl", timeout=timeout) as r:
            root = ET.fromstring(r.read())
        info["ns"] = root.get("targetNamespace", "")
        for el in root.iter():
            tag = el.tag.split("}")[-1]
            if tag == "operation":
                nm = el.get("name", "")
                if nm and nm not in info["methods"]:
                    info["methods"].append(nm)
            # Extract the actual SOAP POST endpoint from soap:address location
            if tag == "address":
                loc = el.get("location", "")
                if loc and loc.startswith("http"):
                    info["endpoint"] = loc
    except Exception as e:
        _log.debug("[Zetta] WSDL fetch failed: %s", e)
    _wsdl_cache[url] = info
    return info

def _soap_endpoint(url: str, timeout: int = 6) -> str:
    """Return the actual SOAP POST endpoint.
    Takes the path from the WSDL soap:address but keeps the host/port from the
    user-supplied URL — prevents DNS failures when the WSDL contains a Windows
    hostname that the SignalScope server cannot resolve."""
    from urllib.parse import urlparse, urlunparse
    wsdl_ep = _wsdl_info(url, timeout).get("endpoint") or url
    try:
        user  = urlparse(url)
        wsdl  = urlparse(wsdl_ep)
        # Replace scheme/host/port with what the user entered; keep WSDL path
        fixed = urlunparse((
            user.scheme or wsdl.scheme,
            user.netloc,          # user's IP:port
            wsdl.path or user.path,
            "", "", ""
        ))
        return fixed
    except Exception:
        return url

def _ns(url: str, timeout: int = 6) -> str:
    ns = _wsdl_info(url, timeout).get("ns", "") or "http://www.rcsworks.com/"
    return ns if ns.endswith("/") else ns + "/"

def _find(elem: ET.Element, *local_names: str) -> str:
    """Recursive first-match by local tag name. Returns text or ''."""
    for name in local_names:
        for child in elem.iter():
            if child.tag.split("}")[-1] == name and child.text:
                return child.text.strip()
    return ""

def _find_in(elem: ET.Element, container_tag: str, *field_names: str) -> str:
    """Find field_names inside the first child matching container_tag."""
    for child in elem.iter():
        if child.tag.split("}")[-1] == container_tag:
            return _find(child, *field_names)
    return ""

def _derive_sf_url(main_url: str) -> str:
    try:
        from urllib.parse import urlparse
        h = urlparse(main_url).hostname or ""
        return f"http://{h}:3132/StatusFeed" if h else ""
    except Exception:
        return ""

def _parse_zetta_ts(s: str) -> str:
    if not s or "T" not in s: return s
    if s.endswith("Z"): s = s[:-1]
    if len(s) > 6 and s[-3] == ":" and s[-6] in ("+", "-"): s = s[:-6]
    dot = s.rfind(".")
    if dot != -1: s = s[:dot + 7]
    return s

# ── GetStationFull rich parser ─────────────────────────────────────────────────
def _parse_station_full(root: ET.Element, station_id: str, spot_cats: list) -> dict:
    """Parse a full GetStationFull XML response into a display dict."""
    # Station name and computer name at top level
    station_name  = _find(root, "Name", "StationName", "DisplayName")
    computer_name = (_find(root, "ComputerName", "MachineName", "SequencerName") or None)

    # Mode / Status / Gap / ETM — prefer values nested inside Metadata
    mode_raw   = _find_in(root, "Metadata", "Mode")   or _find(root, "Mode")   or "0"
    status_raw = _find_in(root, "Metadata", "Status") or _find(root, "Status") or "1"
    gap_raw    = (_find_in(root, "Metadata", "GapTimeInSeconds", "GapTime")
                  or _find(root, "GapTimeInSeconds", "GapTime") or "0")
    etm_raw    = (_find_in(root, "Metadata", "TargetGapTimeUtc", "ETM", "ExpectedTimeOfMediaEnd")
                  or _find(root, "TargetGapTimeUtc", "ETM", "ExpectedTimeOfMediaEnd") or None)

    try:   mode   = int(mode_raw)
    except: mode  = MODE_UNKNOWN
    try:   status = int(status_raw)
    except: status = ST_QUEUED
    try:   gap_s  = float(gap_raw)
    except: gap_s = 0.0

    # Format ETM
    etm_str = "--:--:--"
    if etm_raw:
        try:
            from datetime import datetime
            cleaned = _parse_zetta_ts(str(etm_raw))
            for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%H:%M:%S"):
                try:
                    etm_str = datetime.strptime(cleaned, fmt).strftime("%H:%M:%S"); break
                except ValueError:
                    pass
        except Exception:
            etm_str = str(etm_raw)[:8]

    # Parse queue events
    now_playing = None
    queue_items = []
    sc_upper = [c.upper() for c in spot_cats] if spot_cats else []

    # Find the Queue container first, then iterate Events within it
    queue_root = root
    for el in root.iter():
        if el.tag.split("}")[-1] == "Queue":
            queue_root = el; break

    for el in queue_root.iter():
        if el.tag.split("}")[-1] != "Event":
            continue
        try:
            title     = _find(el, "Title") or ""
            artist    = _find(el, "Artist") or ""
            dur_s     = float(_find(el, "DurationInSeconds", "Duration") or 0)
            ev_status = int(_find(el, "Status") or 1)
            asset_t   = int(_find(el, "AssetType") or 0)
            ev_type   = int(_find(el, "Type") or 0)
            ev_guid   = _find(el, "EventGUID", "GUID") or ""
            raw_cat   = (_find(el, "CategoryName", "Category") or "").upper()
        except Exception:
            continue

        if asset_t not in DISPLAYABLE_ASSET_TYPES: continue
        if ev_type in FILTERED_EVENT_TYPES:         continue
        if dur_s <= 0:                              continue
        if not title.strip():                       continue

        if artist and artist.strip() and artist not in ("Spot Block", "Execute Command", "Macro"):
            display = f"{artist} - {title}".upper()
        else:
            display = title.upper()
        if len(display) > 45:
            display = display[:42] + "…"

        is_spot = any(sc in raw_cat or raw_cat in sc for sc in sc_upper) if sc_upper else False

        parsed = {
            "title":            display,
            "raw_title":        title,
            "raw_artist":       artist,
            "duration":         _fmt_dur(dur_s),
            "duration_seconds": dur_s,
            "status":           ev_status,
            "asset_type":       asset_t,
            "is_playing":       ev_status == ST_PLAYING,
            "is_spot":          is_spot,
            "event_guid":       ev_guid,
            "raw_category":     raw_cat,
        }

        if ev_status == ST_PLAYING and now_playing is None:
            now_playing = parsed
        elif ev_status != ST_PLAYING and len(queue_items) < 3:
            queue_items.append(parsed)

    # Deduplicate: if the playing track reappears in the queue (same GUID), drop it
    if now_playing and now_playing.get("event_guid"):
        np_guid = now_playing["event_guid"]
        queue_items = [q for q in queue_items if q.get("event_guid") != np_guid]

    is_spot_now = now_playing.get("is_spot", False) if now_playing else False

    return {
        "station_id":       station_id,
        "station_name":     station_name,
        "computer_name":    computer_name,
        "mode":             mode,
        "mode_name":        MODE_NAMES.get(mode, "Unknown"),
        "status":           status,
        "status_name":      STATION_STATE_NAMES.get(status, "QUEUED"),
        "css_state":        _get_css_state(mode, status),
        "gap":              _fmt_gap(gap_s),
        "etm":              etm_str,
        "now_playing":      now_playing,
        "queue":            queue_items,
        "is_spot":          is_spot_now,
        "error":            None,
        "ts":               time.time(),
    }

def _parse_station_full_zeep(result, station_id: str, friendly_name: str, spot_cats: list) -> dict:
    """Parse a zeep GetStationFull result object into a display dict."""
    if result is None:
        raise ValueError("GetStationFull returned None")

    station_name = getattr(result, "Name", None) or friendly_name

    metadata = getattr(result, "Metadata", None)
    if metadata is not None:
        mode  = int(getattr(metadata, "Mode", MODE_UNKNOWN) or MODE_UNKNOWN)
        status = int(getattr(metadata, "Status", ST_QUEUED) or ST_QUEUED)
        gap_s  = float(getattr(metadata, "GapTimeInSeconds", 0) or 0)
        etm_utc = getattr(metadata, "TargetGapTimeUtc", None)
        # ProcessingComputerName is inside Metadata
        computer_name = (getattr(metadata, "ProcessingComputerName", None)
                         or getattr(metadata, "ComputerName", None)
                         or getattr(result, "ComputerName", None)
                         or getattr(result, "MachineName", None))
    else:
        mode = MODE_UNKNOWN; status = ST_QUEUED; gap_s = 0.0; etm_utc = None
        computer_name = None

    # Format ETM
    etm_str = "--:--:--"
    if etm_utc is not None:
        try:
            from datetime import datetime
            if isinstance(etm_utc, datetime):
                etm_str = etm_utc.strftime("%H:%M:%S")
            else:
                etm_str = str(etm_utc)[:8]
        except Exception:
            pass

    # Parse queue events
    now_playing = None
    queue_items = []
    sc_upper = [c.upper() for c in spot_cats] if spot_cats else []

    queue = getattr(result, "Queue", None)
    events = list(getattr(queue, "Event", []) or []) if queue is not None else []

    for event in events:
        title   = getattr(event, "Title",            "") or ""
        artist  = getattr(event, "Artist",           "") or ""
        dur_s   = float(getattr(event, "DurationInSeconds", 0) or 0)
        ev_status = int(getattr(event, "Status",     ST_QUEUED) or ST_QUEUED)
        asset_t = int(getattr(event, "AssetType",    0) or 0)
        ev_type = int(getattr(event, "Type",         0) or 0)
        ev_guid = getattr(event, "EventGUID",        "") or ""
        raw_cat = (getattr(event, "CategoryName", None)
                   or getattr(event, "Category", "") or "").upper()

        if asset_t not in DISPLAYABLE_ASSET_TYPES: continue
        if ev_type in FILTERED_EVENT_TYPES:         continue
        if dur_s <= 0:                              continue
        if not title.strip():                       continue

        if artist and artist.strip() and artist not in ("Spot Block", "Execute Command", "Macro"):
            display = f"{artist} - {title}".upper()
        else:
            display = title.upper()
        if len(display) > 45:
            display = display[:42] + "…"

        is_spot = any(sc in raw_cat or raw_cat in sc for sc in sc_upper) if sc_upper else False

        parsed = {
            "title": display, "raw_title": title, "raw_artist": artist,
            "duration": _fmt_dur(dur_s), "duration_seconds": dur_s,
            "status": ev_status, "asset_type": asset_t,
            "is_playing": ev_status == ST_PLAYING,
            "is_spot": is_spot, "event_guid": ev_guid, "raw_category": raw_cat,
        }

        if ev_status == ST_PLAYING and now_playing is None:
            now_playing = parsed
        elif ev_status != ST_PLAYING and len(queue_items) < 3:
            queue_items.append(parsed)

    # Deduplicate: if the playing track reappears in the queue (same GUID), drop it
    if now_playing and now_playing.get("event_guid"):
        np_guid = now_playing["event_guid"]
        queue_items = [q for q in queue_items if q.get("event_guid") != np_guid]

    return {
        "station_id":    station_id,
        "station_name":  station_name,
        "computer_name": computer_name,
        "mode":          mode,
        "mode_name":     MODE_NAMES.get(mode, "Unknown"),
        "status":        status,
        "status_name":   STATION_STATE_NAMES.get(status, "QUEUED"),
        "css_state":     _get_css_state(mode, status),
        "gap":           _fmt_gap(gap_s),
        "etm":           etm_str,
        "now_playing":   now_playing,
        "queue":         queue_items,
        "is_spot":       now_playing.get("is_spot", False) if now_playing else False,
        "error":         None,
        "ts":            time.time(),
    }

def _sf_get_stations(sf_url: str, timeout: int) -> list:
    ns = _ns(sf_url, timeout)
    root, _ = _soap_call(sf_url, "GetStations", "", ns, timeout)
    results = []
    for el in root.iter():
        if el.tag.split("}")[-1] in ("Station", "StationInfo", "StationDetails"):
            sid  = _find(el, "ID", "Id", "StationID", "StationId")
            name = _find(el, "Name", "StationName", "CallLetters", "DisplayName")
            if sid:
                results.append({"id": sid, "name": name or sid})
    return results


# ── Per-instance poller ────────────────────────────────────────────────────────
class _InstancePoller:
    def __init__(self, iid: str, inst_cfg: dict, log_fn):
        self.iid         = iid
        self.cfg         = inst_cfg          # reference — read under _cfg_lock
        self.log         = log_fn
        self.lock        = threading.Lock()
        self.running     = False
        self.thread      = None
        self.sf_health   = {"ok": None, "error": "", "ts": 0.0}
        self._state:     Dict[str, dict] = {}
        self._last_guid: Dict[str, str]  = {}
        self._play_start: Dict[str, float] = {}
        self._client     = None   # zeep.Client — created in _connect()

    def start(self):
        if self.running: return
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True,
                                       name=f"Zetta-{self.iid}")
        self.thread.start()
        self.log(f"[Zetta] Instance '{self.iid}' poller started")

    def stop(self):
        self.running = False
        self._client = None
        if self.thread:
            self.thread.join(timeout=4)

    def _sf_url(self) -> str:
        with _cfg_lock:
            return self.cfg.get("status_feed_url", "").strip()

    def _connect(self) -> bool:
        """Create a zeep client for this instance. Returns True on success."""
        sf_url = self._sf_url()
        if not sf_url:
            self.sf_health = {"ok": False, "error": "No Status Feed URL configured", "ts": time.time()}
            return False
        try:
            sep  = "&" if "?" in sf_url else "?"
            wsdl = sf_url + sep + "wsdl"
            self.log(f"[Zetta] '{self.iid}' connecting to {wsdl}")
            self._client = _make_zeep_client(wsdl, sf_url)
            self.sf_health = {"ok": True, "error": "", "ts": time.time()}
            self.log(f"[Zetta] '{self.iid}' connected")
            return True
        except Exception as e:
            self._client = None
            self.sf_health = {"ok": False, "error": str(e), "ts": time.time()}
            self.log(f"[Zetta] '{self.iid}' connection failed: {e}")
            return False

    def _poll_once(self):
        # If polling is delegated to a remote client site, skip direct polling —
        # data arrives via POST /api/zetta/site_data from the client.
        with _cfg_lock:
            if self.cfg.get("poll_site", ""):
                return

        if self._client is None:
            if not _zeep or not self._connect():
                return

        with _cfg_lock:
            stations  = list(self.cfg.get("stations", []))
            spot_cats = list(self.cfg.get("spot_categories", DEFAULT_SPOT_CATS))

        for st in stations:
            sid      = str(st.get("id", "")).strip()
            if not sid: continue
            friendly = st.get("name", sid)
            try:
                result = self._client.service.GetStationFull(stationID=int(sid))
                data   = _parse_station_full_zeep(result, sid, friendly, spot_cats)
                self.sf_health = {"ok": True, "error": "", "ts": time.time()}

                # Track play start for progress/countdown
                np = data.get("now_playing")
                if np:
                    guid = np.get("event_guid", "")
                    if guid != self._last_guid.get(sid):
                        self._last_guid[sid]  = guid
                        self._play_start[sid] = time.time()

                dur     = np["duration_seconds"] if np else 0
                elapsed = time.time() - self._play_start.get(sid, time.time())
                data["duration_seconds"]  = dur
                data["remaining_seconds"] = max(0.0, dur - elapsed)
                data["play_start_time"]   = self._play_start.get(sid, 0.0)

                with self.lock:
                    self._state[sid] = data

            except Exception as e:
                self.log(f"[Zetta] '{self.iid}' poll error station {sid}: {e}")
                self.sf_health = {"ok": False, "error": str(e), "ts": time.time()}
                self._client = None   # force reconnect on next cycle
                with self.lock:
                    self._state.setdefault(sid, {})["error"] = str(e)
                    self._state[sid]["station_id"] = sid

    def _loop(self):
        while self.running:
            try:
                self._poll_once()
            except Exception as e:
                self.log(f"[Zetta] '{self.iid}' loop error: {e}")
                self._client = None
            with _cfg_lock:
                interval = int(self.cfg.get("poll_interval", 10) or 10)
            for _ in range(max(1, interval)):
                if not self.running: return
                time.sleep(1)

    def get_state(self) -> dict:
        with self.lock:
            return {k: dict(v) for k, v in self._state.items()}

# ── Config management ──────────────────────────────────────────────────────────
def _load_cfg() -> dict:
    if not _cfg_path or not os.path.exists(_cfg_path):
        return {"instances": []}
    try:
        with open(_cfg_path) as f:
            raw = json.load(f)
        # Migrate old single-instance flat config (v1.x)
        if "instances" not in raw and ("url" in raw or "stations" in raw):
            raw = {"instances": [{
                "id": "default", "name": "Zetta",
                "url":             raw.get("url", ""),
                "status_feed_url": raw.get("status_feed_url", ""),
                "stations":        raw.get("stations", []),
                "poll_interval":   raw.get("poll_interval", 10),
                "timeout":         raw.get("timeout", 6),
                "spot_categories": raw.get("spot_categories", DEFAULT_SPOT_CATS),
                "chain_id":        "",
                "colors":          dict(DEFAULT_COLORS),
            }]}
        return raw
    except Exception as e:
        _log.warning("[Zetta] Config load error: %s", e)
        return {"instances": []}

def _save_cfg(cfg: dict):
    if not _cfg_path:
        return
    try:
        with open(_cfg_path, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        _log.warning("[Zetta] Config save error: %s", e)

def _restart_pollers(log_fn):
    """Stop old pollers and start fresh ones from current config."""
    global _pollers
    for p in list(_pollers.values()):
        try: p.stop()
        except Exception: pass
    _pollers.clear()
    cfg = _load_cfg()
    for inst in cfg.get("instances", []):
        iid = inst.get("id", "").strip()
        if not iid: continue
        p = _InstancePoller(iid, inst, log_fn)
        _pollers[iid] = p
        p.start()

# ── Chain status helper ────────────────────────────────────────────────────────
def _chain_status(hub_server, signal_chains, chain_id: str) -> Optional[dict]:
    if not chain_id: return None
    chain = next((c for c in (signal_chains or []) if c.get("id") == chain_id), None)
    if not chain: return None
    try:
        result = hub_server.eval_chain(chain)
        return {
            "name":   chain.get("name", chain_id),
            "status": result.get("display_status") or result.get("status", "unknown"),
        }
    except Exception:
        return {"name": chain.get("name", chain_id), "status": "unknown"}

# ── Templates ──────────────────────────────────────────────────────────────────
_PAGE_CSS = """
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;
     --wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(circle at top,#12376f 0%,var(--bg) 38%,#05101f 100%);
     color:var(--tx);font-family:system-ui,sans-serif;font-size:13px}
h1{font-size:16px;font-weight:700}
.page{padding:20px}
.card{background:var(--sur);border:1px solid var(--bor);border-radius:12px;
  overflow:hidden;margin-bottom:16px}
.ch{padding:9px 14px;display:flex;align-items:center;gap:8px;
  border-bottom:1px solid var(--bor);
  background:linear-gradient(180deg,#143766,#102b54);
  font-size:12px;font-weight:700;color:var(--acc);
  text-transform:uppercase;letter-spacing:.06em}
.cb{padding:14px}

/* Instance panels */
.inst-header{display:flex;align-items:center;gap:12px;margin-bottom:14px;flex-wrap:wrap}
.inst-title{font-size:15px;font-weight:700;flex:1}
.chain-badge{font-size:11px;font-weight:700;padding:3px 10px;border-radius:999px;
  white-space:nowrap}
.chain-ok{background:rgba(34,197,94,.15);color:var(--ok);border:1px solid rgba(34,197,94,.3)}
.chain-fault{background:rgba(239,68,68,.15);color:var(--al);border:1px solid rgba(239,68,68,.3)}
.chain-unknown{background:rgba(138,164,200,.08);color:var(--mu);border:1px solid var(--bor)}
select.chain-sel{background:#0d1e40;border:1px solid var(--bor);color:var(--tx);
  padding:4px 8px;border-radius:5px;font-size:12px;max-width:220px}
select.chain-sel:focus{border-color:var(--acc);outline:none}

/* Zetta boxes grid */
.z-grid{display:flex;flex-wrap:wrap;gap:12px}

/* The Zetta box itself — colours driven by CSS custom properties per-box */
.z-box{
  width:270px;flex-shrink:0;border-radius:8px;overflow:hidden;font-family:monospace;
  background:var(--zb,#111);border:2px solid var(--zbo,#333);
}
.z-head{background:var(--zh,#1a1a1a);padding:8px 12px;text-align:center;
  border-bottom:1px solid var(--zbo,#333)}
.z-sname{font-size:13px;font-weight:bold;color:var(--zht,#fff)}
.z-seq{font-size:10px;color:var(--zs,#17a8ff);margin:2px 0;letter-spacing:.04em}
.z-meta{font-size:11px;color:var(--zmt,#aaa);margin-top:1px}
.z-state{padding:10px 8px;text-align:center;font-size:18px;font-weight:700;
  letter-spacing:1px;background:var(--zstbg,#333);color:var(--zsttx,#888)}
.z-state .dur{font-size:11px;font-weight:400;margin-top:2px;opacity:.85}
.z-np{display:flex;justify-content:space-between;padding:5px 10px;
  font-size:11px;font-weight:700;border-bottom:1px solid rgba(255,255,255,.06)}
.z-np .zt{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-right:6px}
.z-np .zd{flex-shrink:0;color:rgba(255,255,255,.65)}
.z-countdown{margin-left:4px}
.z-countdown.ending{color:var(--al)}
.z-prog-track{height:3px;background:rgba(255,255,255,.1)}
.z-prog-bar{height:3px;background:var(--zpb,#28a745);transition:width .5s linear}
.z-queue-item{display:flex;justify-content:space-between;padding:3px 10px;
  font-size:11px;border-bottom:1px solid rgba(255,255,255,.04);
  color:var(--zqt,#ccc);background:var(--zqb,#0d0d1a)}
.z-queue-item .zt{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-right:6px}
.z-queue-item .zd{flex-shrink:0;opacity:.7}
.z-error{padding:8px 10px;text-align:center;color:var(--al);font-size:11px}
.z-disconnected{padding:14px;text-align:center;color:var(--al);font-size:12px;
  border:1px solid rgba(239,68,68,.2);border-radius:6px}
.z-disc-label{display:inline-block;font-size:9px;font-weight:700;padding:1px 5px;
  border-radius:999px;vertical-align:middle;margin-left:4px}

/* Settings form */
label{font-size:11px;color:var(--mu);font-weight:600;text-transform:uppercase;
  letter-spacing:.05em;display:block;margin-bottom:3px;margin-top:10px}
label:first-child{margin-top:0}
input[type=text],input[type=number],input[type=url],select,textarea{
  background:#0d1e40;border:1px solid var(--bor);color:var(--tx);
  padding:6px 9px;border-radius:6px;font-size:13px;width:100%}
input[type=text]:focus,input[type=number]:focus,input[type=url]:focus,
select:focus,textarea:focus{border-color:var(--acc);outline:none}
textarea{font-family:monospace;font-size:11px;resize:vertical}
input[type=color]{width:38px;height:28px;padding:2px;border-radius:4px;cursor:pointer}
.row{display:flex;gap:10px;flex-wrap:wrap}
.row .f{flex:1;min-width:140px}
.btn{display:inline-block;text-decoration:none;border:none;border-radius:8px;
  padding:5px 12px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit}
.btn:hover{filter:brightness(1.15)}
.nav-active{background:var(--acc)!important;color:#fff!important}
.btn.bp{background:var(--acc);color:#fff}
.btn.bd{background:var(--al);color:#fff}
.btn.bg{background:rgba(255,255,255,.06);color:var(--tx);border:1px solid var(--bor)}
.btn.bs{padding:3px 9px;font-size:12px}
.btn-row{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.st-row{display:flex;gap:6px;align-items:center;margin-bottom:5px;
  background:#071428;border:1px solid var(--bor);border-radius:5px;padding:6px 8px}
.st-row input{flex:1;min-width:0}
.hint{font-size:11px;color:var(--mu);margin-top:3px}
#msg{font-size:12px;margin-top:8px;min-height:14px}
.color-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:8px;margin-top:8px}
.color-row{display:flex;align-items:center;gap:7px;font-size:11px;color:var(--mu)}
.color-row label{margin:0;text-transform:none;letter-spacing:0;flex:1}
.inst-acc{border:1px solid var(--bor);border-radius:8px;margin-bottom:10px;overflow:hidden}
.inst-acc-hdr{padding:9px 14px;display:flex;align-items:center;gap:10px;cursor:pointer;
  background:rgba(255,255,255,.03)}
.inst-acc-hdr:hover{background:rgba(255,255,255,.06)}
.inst-acc-body{display:none;padding:14px;border-top:1px solid var(--bor)}
.inst-acc-body.open{display:block}
.tabs{display:flex;gap:2px;margin-bottom:14px}
.tab{padding:5px 14px;border-radius:6px 6px 0 0;border:1px solid var(--bor);
  border-bottom:none;cursor:pointer;font-size:12px;font-weight:600;color:var(--mu);background:var(--bg)}
.tab.act{background:var(--sur);color:var(--tx)}
.tb{display:none}.tb.act{display:block}
.new-inst-form{background:rgba(23,168,255,.04);border:1px solid rgba(23,168,255,.15);
  border-radius:8px;padding:14px;margin-bottom:14px;display:none}
"""

_PAGE_TPL = """<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<title>Zetta Sequencer — SignalScope</title>
<style nonce="{{csp_nonce()}}">""" + _PAGE_CSS + """</style>
</head><body>
{{topnav("zetta")|safe}}
<div class="page">
  <div class="tabs">
    <div class="tab act" data-tab="t-seq">&#127932; Sequencer</div>
    <div class="tab"     data-tab="t-cfg">&#x2699; Instances</div>
    <div class="tab"     data-tab="t-dbg">&#x1F50D; Debug</div>
  </div>

  <!-- ── Sequencer view ── -->
  <div class="tb act" id="t-seq">
    <div id="seq-root">
      <div style="color:var(--mu);padding:20px 0">Loading&#8230;</div>
    </div>
  </div>

  <!-- ── Instance config ── -->
  <div class="tb" id="t-cfg">
    <div class="card">
      <div class="ch">&#x2699; Zetta Instances</div>
      <div class="cb">
        <button class="btn bp bs" id="btn-new-inst">&#xFF0B; Add Instance</button>
        <div class="new-inst-form" id="new-inst-form">
          <div class="row" style="margin-bottom:8px">
            <div class="f"><label>Display Name</label>
              <input id="ni-name" placeholder="Studio A Zetta"></div>
            <div class="f"><label>Status Feed URL</label>
              <input id="ni-sfurl" placeholder="http://zetta-server:3132/StatusFeed" type="url"></div>
          </div>
          <div class="row" style="margin-bottom:8px">
            <div class="f" style="max-width:130px"><label>Poll interval (s)</label>
              <input id="ni-interval" type="number" value="10" min="5" max="120"></div>
            <div class="f" style="max-width:120px"><label>Timeout (s)</label>
              <input id="ni-timeout" type="number" value="6" min="2" max="30"></div>
            <div class="f"><label title="Which site makes SOAP calls to Zetta. Use when hub cannot reach Zetta directly.">Polling site</label>
              <select id="ni-pollsite"><option value="">(hub polls directly)</option></select>
              <div class="hint">Select a client site if the hub cannot reach Zetta.</div>
            </div>
          </div>
          <div><label>Spot categories (comma-separated)</label>
            <input id="ni-spots" value="SPOT,SPOTS,COMMERCIAL,COMMS,PROMO,PROMOS"></div>
          <div style="margin-top:10px"><label>Stations (one per row — ID and friendly name)</label>
            <div id="ni-stations"></div>
            <div style="display:flex;gap:6px;margin-top:6px">
              <button class="btn bg bs" id="btn-ni-add-stn">&#xFF0B; Add Station</button>
              <button class="btn bg bs btn-discover" data-target="ni-stations" data-url-src="ni-sfurl" data-msg="ni-msg">&#x1F50E; Discover Stations</button>
            </div>
          </div>
          <div class="btn-row">
            <button class="btn bp bs" id="btn-ni-save">&#x1F4BE; Create Instance</button>
            <button class="btn bg bs" id="btn-ni-cancel">Cancel</button>
          </div>
          <div id="ni-msg" style="font-size:12px;margin-top:6px;min-height:12px"></div>
        </div>
        <div id="instances-list" style="margin-top:14px">{{instances_html|safe}}</div>
      </div>
    </div>
  </div>

  <!-- ── Debug ── -->
  <div class="tb" id="t-dbg">
    <div class="card">
      <div class="ch">&#x1F50D; SOAP Debug Explorer</div>
      <div class="cb">
        <div class="row">
          <div class="f"><label>Service URL</label>
            <input id="dbg-url" placeholder="http://zetta-server/ZettaService/ZettaService.asmx"></div>
          <div class="f" style="max-width:220px"><label>SOAP Method</label>
            <input id="dbg-method" placeholder="GetStationFull"></div>
        </div>
        <div style="margin-top:8px"><label>Arguments (one per line: key:value)</label>
          <textarea id="dbg-body" rows="3" placeholder="stationId:1&#10;Leave blank for methods with no arguments (e.g. GetStations)"></textarea></div>
        <div class="btn-row">
          <button class="btn bg bs" id="btn-dbg">&#x25B6; Call</button>
          <button class="btn bg bs" id="btn-wsdl">&#x1F4C4; WSDL Methods</button>
        </div>
        <div id="dbg-msg" style="font-size:12px;margin-top:8px;min-height:12px"></div>
        <div id="dbg-methods" style="margin-top:8px;font-family:monospace;font-size:11px;
          color:var(--mu);column-count:3;gap:10px;line-height:1.8"></div>
        <label style="margin-top:12px">Raw response</label>
        <textarea id="dbg-resp" rows="14" readonly></textarea>
      </div>
    </div>
  </div>
</div>

<script nonce="{{csp_nonce()}}">
var _CHAINS = {{chains_json|safe}};
var _SITES  = {{sites_json|safe}};
// ── Helpers ───────────────────────────────────────────────────────────────────
function _csrf(){
  return (document.querySelector('meta[name="csrf-token"]')||{}).content
      || (document.cookie.match(/(?:^|;\\s*)csrf_token=([^;]+)/)||[])[1]||'';
}
function _f(url,opts){
  opts=Object.assign({credentials:'same-origin'},opts||{});
  opts.headers=Object.assign({'Content-Type':'application/json','X-CSRFToken':_csrf()},opts.headers||{});
  return fetch(url,opts);
}
function _esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function _msg(id,txt,col){var el=document.getElementById(id);if(!el)return;el.style.color=col||'var(--mu)';el.textContent=txt;}

// ── Tabs ──────────────────────────────────────────────────────────────────────
document.querySelectorAll('.tab').forEach(function(t){
  t.addEventListener('click',function(){
    document.querySelectorAll('.tab').forEach(function(x){x.classList.remove('act');});
    document.querySelectorAll('.tb').forEach(function(x){x.classList.remove('act');});
    t.classList.add('act');
    document.getElementById(t.dataset.tab).classList.add('act');
  });
});

// ── Station row helper ────────────────────────────────────────────────────────
function _stRow(container,id,name,chainId){
  var d=document.createElement('div');d.className='st-row';
  var chainSel='<select class="st-chain" style="flex:0 0 auto;max-width:170px;min-width:100px">'
    +'<option value="">(no chain)</option>'
    +(_CHAINS||[]).map(function(c){
      return '<option value="'+_esc(c.id||'')+'"'+(c.id===(chainId||'')?' selected':'')+'>'+_esc(c.name||c.id||'')+'</option>';
    }).join('')+'</select>';
  d.innerHTML='<input class="st-id"   placeholder="Station ID (number)" value="'+_esc(id||'')+'" style="max-width:100px">'
             +'<input class="st-name" placeholder="Friendly name"       value="'+_esc(name||'')+'">'
             +chainSel
             +'<button class="btn bd bs st-rm">&#x2715;</button>';
  container.appendChild(d);
  d.querySelector('.st-rm').addEventListener('click',function(){d.remove();});
}

// ── New instance form ────────────────────────────────────────────────────────
document.getElementById('btn-new-inst').addEventListener('click',function(){
  // Populate polling-site dropdown from connected sites
  var ps=document.getElementById('ni-pollsite');
  ps.innerHTML='<option value="">(hub polls directly)</option>';
  (_SITES||[]).forEach(function(s){
    var o=document.createElement('option');
    o.value=s;o.textContent=s;ps.appendChild(o);
  });
  document.getElementById('new-inst-form').style.display='block';
  this.style.display='none';
});
document.getElementById('btn-ni-cancel').addEventListener('click',function(){
  document.getElementById('new-inst-form').style.display='none';
  document.getElementById('btn-new-inst').style.display='';
});
document.getElementById('btn-ni-add-stn').addEventListener('click',function(){
  _stRow(document.getElementById('ni-stations'),'','');
});
document.getElementById('btn-ni-save').addEventListener('click',function(){
  var stns=[];
  document.querySelectorAll('#ni-stations .st-row').forEach(function(r){
    var id=r.querySelector('.st-id').value.trim();
    var nm=r.querySelector('.st-name').value.trim();
    var cid=(r.querySelector('.st-chain')||{}).value||'';
    if(id) stns.push({id:id,name:nm||id,chain_id:cid});
  });
  var p={
    name:      document.getElementById('ni-name').value.trim(),
    sf_url:    document.getElementById('ni-sfurl').value.trim(),
    interval:  parseInt(document.getElementById('ni-interval').value)||10,
    timeout:   parseInt(document.getElementById('ni-timeout').value)||6,
    spots:     document.getElementById('ni-spots').value,
    stations:  stns,
    poll_site: document.getElementById('ni-pollsite').value,
  };
  if(!p.name){_msg('ni-msg','Name is required.','var(--al)');return;}
  if(!p.sf_url){_msg('ni-msg','Status Feed URL is required.','var(--al)');return;}
  _f('/api/zetta/instance/add',{method:'POST',body:JSON.stringify(p)})
    .then(function(r){return r.json();})
    .then(function(d){
      if(d.ok){location.reload();}
      else _msg('ni-msg','Error: '+(d.error||'?'),'var(--al)');
    }).catch(function(e){_msg('ni-msg',''+e,'var(--al)');});
});

// ── Instance accordion ───────────────────────────────────────────────────────
document.querySelectorAll('.inst-acc-hdr').forEach(function(h){
  h.addEventListener('click',function(){
    var body=h.nextElementSibling;
    body.classList.toggle('open');
  });
});
// Save instance settings (delegated by instance-id)
document.getElementById('instances-list').addEventListener('click',function(e){
  // Save button
  var sv=e.target.closest('.btn-inst-save');
  if(sv){
    var iid=sv.dataset.iid;
    var form=document.getElementById('if-'+iid);
    var stns=[];
    form.querySelectorAll('.st-row').forEach(function(r){
      var id=r.querySelector('.st-id').value.trim();
      var nm=r.querySelector('.st-name').value.trim();
      var cid=(r.querySelector('.st-chain')||{}).value||'';
      if(id) stns.push({id:id,name:nm||id,chain_id:cid});
    });
    // Collect colors
    var colors={};
    form.querySelectorAll('.color-inp').forEach(function(c){colors[c.dataset.key]=c.value;});
    var p={
      name:      form.querySelector('.if-name').value.trim(),
      sf_url:    form.querySelector('.if-sfurl').value.trim(),
      interval:  parseInt(form.querySelector('.if-interval').value)||10,
      timeout:   parseInt(form.querySelector('.if-timeout').value)||6,
      spots:     form.querySelector('.if-spots').value,
      stations:  stns,
      colors:    colors,
      poll_site: (form.querySelector('.if-pollsite')||{}).value||'',
    };
    _f('/api/zetta/instance/'+encodeURIComponent(iid)+'/save',
       {method:'POST',body:JSON.stringify(p)})
      .then(function(r){return r.json();})
      .then(function(d){
        var msgEl=document.getElementById('imsg-'+iid);
        if(msgEl) msgEl.style.color=d.ok?'var(--ok)':'var(--al)';
        if(msgEl) msgEl.textContent=d.ok?'Saved.':'Error: '+(d.error||'?');
      });
    return;
  }
  // Delete button
  var dl=e.target.closest('.btn-inst-del');
  if(dl){
    if(!confirm('Delete this Zetta instance?')) return;
    var iid=dl.dataset.iid;
    _f('/api/zetta/instance/'+encodeURIComponent(iid)+'/delete',{method:'POST'})
      .then(function(r){return r.json();})
      .then(function(d){if(d.ok)location.reload();});
    return;
  }
  // Add station within instance
  var as=e.target.closest('.btn-inst-add-stn');
  if(as){
    var iid=as.dataset.iid;
    _stRow(document.getElementById('istns-'+iid),'','');
    return;
  }
  // Discover stations (delegated — handles both new-instance and existing)
  var dsc=e.target.closest('.btn-discover');
  if(dsc){ _discoverStations(dsc); return; }
});

// Discover button in new-instance form (outside instances-list)
document.getElementById('new-inst-form').addEventListener('click',function(e){
  var dsc=e.target.closest('.btn-discover');
  if(dsc){ _discoverStations(dsc); }
});

function _discoverStations(btn){
  var urlSrc  = btn.dataset.urlSrc;
  var target  = btn.dataset.target;
  var msgId   = btn.dataset.msg;
  var sfEl    = document.getElementById(urlSrc);
  var url     = (sfEl&&sfEl.value.trim())||'';
  if(!url){_msg(msgId,'Enter a Status Feed URL or SOAP URL first.','var(--al)');return;}
  // Find the poll_site select in the nearest form context
  var ctx=btn.closest('.inst-acc-body,.new-inst-form');
  var psEl=ctx?ctx.querySelector('.if-pollsite,#ni-pollsite'):null;
  var poll_site=(psEl&&psEl.value)||'';
  btn.disabled=true;
  _msg(msgId,poll_site?'Asking site \u201c'+poll_site+'\u201d to discover stations\u2026':'Discovering stations\u2026','var(--mu)');
  _f('/api/zetta/discover_stations',{method:'POST',body:JSON.stringify({url:url,poll_site:poll_site})})
    .then(function(r){return r.json();})
    .then(function(d){
      btn.disabled=false;
      if(!d.ok){_msg(msgId,'Error: '+(d.error||'?'),'var(--al)');return;}
      var cont=document.getElementById(target);
      if(!cont){_msg(msgId,'UI error: container not found','var(--al)');return;}
      // Clear existing rows, add discovered ones
      cont.querySelectorAll('.st-row').forEach(function(r){r.remove();});
      d.stations.forEach(function(s){_stRow(cont,s.id,s.name);});
      _msg(msgId,'Found '+d.stations.length+' station'+(d.stations.length===1?'':'s')+'.','var(--ok)');
    }).catch(function(e){btn.disabled=false;_msg(msgId,''+e,'var(--al)');});
}

// ── Sequencer live display ────────────────────────────────────────────────────
var _countdowns = {};

function _fmtTime(sec){
  sec=Math.max(0,Math.round(sec||0));
  var m=Math.floor(sec/60),s=sec%60;
  return m+':'+(s<10?'0':'')+s;
}

function _cssVars(c){
  return 'style="--zb:'+_esc(c.box_bg||'#111')+';--zbo:'+_esc(c.box_border||'#333')
        +';--zh:'+_esc(c.header_bg||'#1a1a1a')+';--zht:'+_esc(c.header_text||'#fff')
        +';--zmt:'+_esc(c.meta_text||'#aaa')+';--zs:'+_esc(c.seq_text||'#17a8ff')
        +'"';
}
function _stateVars(c,css_state){
  var bg,tx;
  if(css_state==='z-auto'){bg=c.playing_auto_bg||'#4a0080';tx=c.playing_auto_text||'#0d0';}
  else if(css_state==='z-live'){bg=c.playing_live_bg||'#000';tx=c.playing_live_text||'#0c0';}
  else if(css_state==='z-queued'){bg=c.queued_bg||'#1a1200';tx=c.queued_text||'#c8a000';}
  else if(css_state==='z-off-air'){bg=c.off_air_bg||'#a00';tx=c.off_air_text||'#f66';}
  else{bg=c.unknown_bg||'#333';tx=c.unknown_text||'#888';}
  return 'style="background:'+_esc(bg)+';color:'+_esc(tx)+'"';
}
function _qItemVars(c){
  return 'style="background:'+_esc(c.queue_item_bg||c.box_bg||'#111')
        +';color:'+_esc(c.queue_item_text||c.header_text||'#ccc')+'"';
}

function _renderBox(s,c,iid){
  var sid=s.station_id;
  var key=iid+'|'+sid;
  var css=s.css_state||'z-unknown';
  var np=s.now_playing;
  var seqLine=s.computer_name
    ?'<div class="z-seq">&#x1F5A5; '+_esc(s.computer_name)+'</div>':'';

  var stateHtml='<div class="z-state" id="zst-'+_esc(key)+'" '+_stateVars(c,css)+'>'
    +'<span id="zstlbl-'+_esc(key)+'">'+(s.status_name||'QUEUED')+'</span>'
    +'</div>';

  var npHtml='';
  if(np){
    npHtml='<div class="z-np">'
      +'<span class="zt" id="znp-'+_esc(key)+'">'+_esc(np.title||'')+'</span>'
      +'<span class="zd">'
      +'<span style="opacity:.6">'+_esc(np.duration||'--:--')+'</span>'
      +' <span class="z-countdown" id="zcd-'+_esc(key)+'">--:--</span></span>'
      +'</div>'
      +'<div class="z-prog-track"><div class="z-prog-bar" id="zpb-'+_esc(key)+'" style="width:0%;background:'+_esc(c.progress_bar||'#28a745')+'"></div></div>';
  }
  var qHtml=(s.queue||[]).map(function(q){
    return '<div class="z-queue-item" '+_qItemVars(c)+'>'
      +'<span class="zt">'+_esc(q.title||'')+'</span>'
      +'<span class="zd">'+_esc(q.duration||'')+'</span>'
      +'</div>';
  }).join('');
  if(s.error&&!np){qHtml='<div class="z-error">&#9888; '+_esc(s.error)+'</div>';}

  var chainBadgeHtml='';
  if(s.chain_status){
    var cst=s.chain_status.status||'unknown';
    var ccls=cst==='ok'?'chain-ok':cst.indexOf('fault')>=0||cst==='fault'?'chain-fault':'chain-unknown';
    var cicon=cst==='ok'?'&#x2714;':cst.indexOf('fault')>=0||cst==='fault'?'&#x26A0;':'&#x25CF;';
    chainBadgeHtml=' <span id="zcb-'+_esc(key)+'" class="chain-badge '+ccls
      +'" style="font-size:10px;vertical-align:middle">'
      +cicon+' '+_esc(s.chain_status.name||'Chain')+'</span>';
  }

  return '<div class="z-box" id="zbox-'+_esc(key)+'" '+_cssVars(c)+'>'
    +'<div class="z-head">'
    +'<div class="z-sname">'+_esc(s.station_name||('Station '+sid))
    +' <span style="font-size:10px;font-weight:400;color:var(--zmt)">['+_esc(s.mode_name||'?')+']</span>'
    +chainBadgeHtml+'</div>'
    +seqLine
    +'<div class="z-meta" id="zmeta-'+_esc(key)+'">'
    +'GAP: <span id="zgap-'+_esc(key)+'">'+_esc(s.gap||'+00:00')+'</span>'
    +' | ETM: <span id="zetm-'+_esc(key)+'">'+_esc(s.etm||'--:--:--')+'</span>'
    +'</div></div>'
    +stateHtml+npHtml+qHtml
    +'</div>';
}

function _renderInst(inst,chains){
  var c=inst.colors||{};
  var header='<div class="inst-header">'
    +'<span class="inst-title">'+_esc(inst.name||inst.id)+'</span>'
    +'</div>';

  var stns=Object.values(inst.stations||{});
  var boxes=stns.length
    ?('<div class="z-grid">'+stns.map(function(s){return _renderBox(s,c,inst.id);}).join('')+'</div>')
    :'<div style="color:var(--mu);font-size:12px">No stations configured for this instance.</div>';

  if(!inst.connected){
    boxes='<div class="z-disconnected">&#9888; Not connected to Zetta SOAP service'
          +(inst.last_error?' — '+_esc(inst.last_error):'')
          +'</div>'+boxes;
  }

  return '<div class="card" id="zinst-'+_esc(inst.id)+'">'
    +'<div class="ch">&#127932; '+_esc(inst.name||inst.id)+'</div>'
    +'<div class="cb">'+header+boxes+'</div>'
    +'</div>';
}

function _updateCountdowns(){
  var now=Date.now()/1000;
  Object.keys(_countdowns).forEach(function(key){
    var cd=_countdowns[key];
    if(!cd) return;
    var elapsed=now-cd.syncAt;
    var rem=Math.max(0,cd.remaining-elapsed);
    var dur=cd.duration;
    var cdEl=document.getElementById('zcd-'+key);
    var pbEl=document.getElementById('zpb-'+key);
    if(cdEl){
      cdEl.textContent='-'+_fmtTime(rem);
      if(rem<30&&rem>0) cdEl.classList.add('ending');
      else cdEl.classList.remove('ending');
    }
    if(pbEl&&dur>0){
      var pct=Math.min(100,((dur-rem)/dur)*100);
      pbEl.style.width=pct.toFixed(1)+'%';
      pbEl.style.background=rem<30?'var(--al)':'';
    }
  });
}

var _seqLoaded=false;
function _refreshSeq(){
  _f('/api/zetta/status_full').then(function(r){return r.json();})
    .then(function(d){
      var root=document.getElementById('seq-root');
      if(!d.instances||!d.instances.length){
        root.innerHTML='<div class="card"><div class="cb" style="color:var(--mu)">No Zetta instances configured. Go to the &#x2699; Instances tab to add one.</div></div>';
        return;
      }
      // Full re-render on first load or instance list change
      var newHtml=d.instances.map(function(inst){return _renderInst(inst,d.chains||{});}).join('');
      if(!_seqLoaded){
        root.innerHTML=newHtml;
        _seqLoaded=true;
      }
      // Incremental updates after first render
      d.instances.forEach(function(inst){
        var iid=inst.id;
        var c=inst.colors||{};
        var stns=Object.values(inst.stations||{});
        stns.forEach(function(s){
          var sid=s.station_id;
          var key=iid+'|'+sid;
          var css=s.css_state||'z-unknown';
          // Update gap/etm
          var gapEl=document.getElementById('zgap-'+key);
          var etmEl=document.getElementById('zetm-'+key);
          if(gapEl) gapEl.textContent=s.gap||'+00:00';
          if(etmEl) etmEl.textContent=s.etm||'--:--:--';
          // Update box CSS vars (color scheme may change)
          var boxEl=document.getElementById('zbox-'+key);
          if(boxEl){
            boxEl.style.cssText='--zb:'+c.box_bg+';--zbo:'+c.box_border
              +';--zh:'+c.header_bg+';--zht:'+c.header_text
              +';--zmt:'+c.meta_text+';--zs:'+c.seq_text;
          }
          // Update state area — only touch style and label, never innerHTML
          var stEl=document.getElementById('zst-'+key);
          if(stEl){
            stEl.setAttribute('style','background:'+_getCBg(c,css)+';color:'+_getCTx(c,css));
            var lblEl=document.getElementById('zstlbl-'+key);
            if(lblEl) lblEl.textContent=(s.status_name||'QUEUED');
          }
          // Update now-playing title
          var npEl=document.getElementById('znp-'+key);
          if(npEl&&s.now_playing) npEl.textContent=s.now_playing.title||'';
          // Update countdown sync
          if(s.now_playing){
            _countdowns[key]={
              remaining:s.remaining_seconds||0,
              duration:s.duration_seconds||0,
              syncAt:Date.now()/1000
            };
          } else {
            delete _countdowns[key];
            var cdEl=document.getElementById('zcd-'+key);
            if(cdEl) cdEl.textContent='--:--';
            var pbEl=document.getElementById('zpb-'+key);
            if(pbEl) pbEl.style.width='0%';
          }
          // Update per-station chain badge (inline span in station name row)
          var cbEl=document.getElementById('zcb-'+key);
          if(cbEl&&s.chain_status){
            var cst=s.chain_status.status||'unknown';
            var ccls=cst==='ok'?'chain-ok':cst.indexOf('fault')>=0||cst==='fault'?'chain-fault':'chain-unknown';
            var cicon=cst==='ok'?'&#x2714;':cst.indexOf('fault')>=0||cst==='fault'?'&#x26A0;':'&#x25CF;';
            cbEl.className='chain-badge '+ccls;
            cbEl.innerHTML=cicon+' '+_esc(s.chain_status.name||'Chain');
          }
        });
      });
    }).catch(function(){});
}

function _getCBg(c,css){
  if(css==='z-auto')  return c.playing_auto_bg||'#4a0080';
  if(css==='z-live')  return c.playing_live_bg||'#000';
  if(css==='z-queued')return c.queued_bg||'#1a1200';
  if(css==='z-off-air')return c.off_air_bg||'#a00';
  return c.unknown_bg||'#333';
}
function _getCTx(c,css){
  if(css==='z-auto')  return c.playing_auto_text||'#0d0';
  if(css==='z-live')  return c.playing_live_text||'#0c0';
  if(css==='z-queued')return c.queued_text||'#c8a000';
  if(css==='z-off-air')return c.off_air_text||'#f66';
  return c.unknown_text||'#888';
}

_refreshSeq();
setInterval(_refreshSeq, 5000);
setInterval(_updateCountdowns, 1000);

// ── Debug ─────────────────────────────────────────────────────────────────────
document.getElementById('btn-dbg').addEventListener('click',function(){
  var url=document.getElementById('dbg-url').value.trim();
  var method=document.getElementById('dbg-method').value.trim();
  var body=document.getElementById('dbg-body').value.trim();
  if(!url||!method){_msg('dbg-msg','URL and method required.','var(--al)');return;}
  document.getElementById('dbg-resp').value='';
  _msg('dbg-msg','Calling '+method+'…');
  _f('/api/zetta/debug',{method:'POST',body:JSON.stringify({url:url,method:method,body:body})})
    .then(function(r){return r.json();})
    .then(function(d){
      _msg('dbg-msg',d.ok?'✓ HTTP 200':'✗ '+d.error,d.ok?'var(--ok)':'var(--al)');
      document.getElementById('dbg-resp').value=d.raw||'';
    }).catch(function(e){_msg('dbg-msg',''+e,'var(--al)');});
});
document.getElementById('btn-wsdl').addEventListener('click',function(){
  var url=document.getElementById('dbg-url').value.trim();
  if(!url){_msg('dbg-msg','Enter a URL.','var(--al)');return;}
  _msg('dbg-msg','Fetching WSDL…');
  _f('/api/zetta/wsdl_methods',{method:'POST',body:JSON.stringify({url:url})})
    .then(function(r){return r.json();})
    .then(function(d){
      _msg('dbg-msg',d.methods&&d.methods.length?'Namespace: '+d.namespace:(d.error||'No methods found'),'var(--ok)');
      var el=document.getElementById('dbg-methods');
      el.innerHTML=(d.methods||[]).map(function(m){
        return '<span style="cursor:pointer;color:var(--acc)" class="wm" data-m="'+_esc(m)+'">'+_esc(m)+'</span>';
      }).join('  ');
    }).catch(function(e){_msg('dbg-msg',''+e,'var(--al)');});
});
document.getElementById('dbg-methods').addEventListener('click',function(e){
  var sp=e.target.closest('.wm');
  if(sp) document.getElementById('dbg-method').value=sp.dataset.m;
});
</script>
</body></html>
"""


# ── Instance accordion HTML builder (server-side) ─────────────────────────────
def _inst_accordion_html(instances: list, signal_chains: list, sites: list = None) -> str:
    """Render editable accordion panels for each configured instance."""
    import html as _h

    def _e(s): return _h.escape(str(s or ""))

    # Build polling-site options
    def _site_opts_for(current):
        opts = '<option value="">(hub polls directly)</option>'
        for s in (sites or []):
            sel = 'selected' if s == current else ''
            opts += f'<option value="{_e(s)}" {sel}>{_e(s)}</option>'
        return opts

    # Build chain options HTML (no selection) for per-station rows
    chain_opts_base = '<option value="">(no chain)</option>' + "".join(
        f'<option value="{_e(c.get("id",""))}">{_e(c.get("name",""))}</option>'
        for c in (signal_chains or [])
    )

    def _chain_opts_for(current_chain_id):
        return '<option value="">(no chain)</option>' + "".join(
            '<option value="{}" {}>{}</option>'.format(
                _e(c.get("id", "")),
                'selected' if c.get("id") == current_chain_id else "",
                _e(c.get("name", "")),
            )
            for c in (signal_chains or [])
        )

    parts = []
    for inst in instances:
        iid    = inst.get("id", "")
        colors = _merge_colors(inst.get("colors", {}))
        stns   = inst.get("stations", [])

        stn_rows = "".join(
            f'<div class="st-row">'
            f'<input class="st-id"   placeholder="Station ID"    value="{_e(s["id"])}"       style="max-width:100px">'
            f'<input class="st-name" placeholder="Friendly name" value="{_e(s.get("name",""))}">'
            f'<select class="st-chain" style="flex:0 0 auto;max-width:170px;min-width:100px">{_chain_opts_for(s.get("chain_id",""))}</select>'
            f'<button class="btn bd bs st-rm">&#x2715;</button>'
            f'</div>'
            for s in stns
        )
        stn_rows += (
            f'<div style="display:flex;gap:6px;margin-top:6px">'
            f'<button class="btn bg bs btn-inst-add-stn" data-iid="{_e(iid)}">&#xFF0B; Add Station</button>'
            f'<button class="btn bg bs btn-discover" data-target="istns-{_e(iid)}" data-url-src="if-sfurl-{_e(iid)}" data-msg="imsg-{_e(iid)}">&#x1F50E; Discover Stations</button>'
            f'</div>'
        )

        color_rows = "".join(
            f'<div class="color-row">'
            f'<input type="color" class="color-inp" data-key="{_e(key)}" value="{_e(colors.get(key, DEFAULT_COLORS.get(key, "#000")))}">'
            f'<label>{_e(label)}</label>'
            f'</div>'
            for label, key in _COLOR_FIELDS
        )

        parts.append(f"""
<div class="inst-acc">
  <div class="inst-acc-hdr">
    <span style="font-weight:700;flex:1">{_e(inst.get("name","Instance"))}</span>
    <span style="font-size:11px;color:var(--mu)">{_e(inst.get("status_feed_url",""))}</span>
    <button class="btn bd bs btn-inst-del" data-iid="{_e(iid)}" style="margin-left:12px">Delete</button>
  </div>
  <div class="inst-acc-body" id="if-{_e(iid)}">
    <div class="row">
      <div class="f"><label>Display Name</label><input class="if-name" value="{_e(inst.get("name",""))}"></div>
      <div class="f"><label>Status Feed URL</label><input class="if-sfurl" id="if-sfurl-{_e(iid)}" value="{_e(inst.get("status_feed_url",""))}"></div>
    </div>
    <div class="row" style="margin-top:8px">
      <div class="f" style="max-width:130px"><label>Poll interval (s)</label><input type="number" class="if-interval" value="{_e(inst.get("poll_interval",10))}"></div>
      <div class="f" style="max-width:120px"><label>Timeout (s)</label><input type="number" class="if-timeout" value="{_e(inst.get("timeout",6))}"></div>
      <div class="f"><label title="Which SignalScope site makes SOAP calls to Zetta. Use this when the hub cannot reach the Zetta server directly (e.g. hub is in a data centre, Zetta is on the broadcast LAN).">Polling site</label>
        <select class="if-pollsite">{_site_opts_for(inst.get("poll_site",""))}</select>
        <div class="hint">Hub polls directly when blank. Select a client site if the hub cannot reach Zetta.</div>
      </div>
    </div>
    <div style="margin-top:8px">
      <label>Spot categories</label>
      <input class="if-spots" value="{_e(", ".join(inst.get("spot_categories", DEFAULT_SPOT_CATS)))}">
    </div>
    <div style="margin-top:10px">
      <label>Stations</label>
      <div style="display:flex;gap:6px;font-size:10px;color:var(--mu);font-weight:600;text-transform:uppercase;padding:0 2px 3px;letter-spacing:.04em">
        <span style="flex:0 0 100px">Station ID</span>
        <span style="flex:1">Friendly name</span>
        <span style="flex:0 0 170px">Broadcast chain</span>
        <span style="width:55px"></span>
      </div>
      <div id="istns-{_e(iid)}">{stn_rows}</div>
    </div>
    <details style="margin-top:12px">
      <summary style="cursor:pointer;font-size:11px;color:var(--mu);font-weight:600;
        text-transform:uppercase;letter-spacing:.05em">Colour Scheme</summary>
      <div class="color-grid" style="margin-top:10px">{color_rows}</div>
    </details>
    <div class="btn-row">
      <button class="btn bp bs btn-inst-save" data-iid="{_e(iid)}">&#x1F4BE; Save</button>
    </div>
    <div id="imsg-{_e(iid)}" style="font-size:12px;margin-top:6px;min-height:12px"></div>
  </div>
</div>""")

    return "\n".join(parts) if parts else '<div style="color:var(--mu);font-size:12px">No instances yet.</div>'


# ── register ───────────────────────────────────────────────────────────────────
def register(app, ctx):
    global _cfg_path

    login_required = ctx["login_required"]
    csrf_protect   = ctx["csrf_protect"]
    monitor        = ctx["monitor"]
    hub_server     = ctx["hub_server"]

    _cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zetta_cfg.json")
    _ensure_zeep(monitor.log)
    _restart_pollers(monitor.log)

    # ── Main page ────────────────────────────────────────────────────────────
    @app.get("/hub/zetta")
    @login_required
    def zetta_page():
        from flask import render_template_string
        from markupsafe import Markup
        cfg = _load_cfg()
        insts = cfg.get("instances", [])
        chains = list(monitor.app_cfg.signal_chains or [])
        chains_json = json.dumps([
            {"id": c.get("id", ""), "name": c.get("name", "")}
            for c in chains
        ])
        # Connected + approved client sites for the polling-site dropdown
        sites = sorted(
            s for s, sd in (hub_server._sites or {}).items()
            if sd.get("_approved")
        )
        sites_json = json.dumps(sites)
        return render_template_string(
            _PAGE_TPL,
            instances_html=Markup(_inst_accordion_html(insts, chains, sites)),
            chains_json=chains_json,
            sites_json=sites_json,
        )

    # ── Full status for sequencer display ────────────────────────────────────
    @app.get("/api/zetta/status_full")
    @login_required
    def zetta_status_full():
        from flask import jsonify
        cfg    = _load_cfg()
        chains = list(monitor.app_cfg.signal_chains or [])

        out_insts = []
        for inst in cfg.get("instances", []):
            iid        = inst.get("id", "")
            poll_site  = inst.get("poll_site", "")
            p          = _pollers.get(iid)
            if poll_site:
                # Data comes from client push — use _remote_state
                with _remote_state_lock:
                    state = dict(_remote_state.get(iid, {}))
                last_push = max((v.get("ts", 0) for v in state.values()), default=0)
                age = time.time() - last_push if last_push else None
                sf_health = {
                    "ok": bool(state) and (age is not None and age < 60),
                    "error": "" if (state and age is not None and age < 60)
                             else (f"No data from site '{poll_site}'" if not state
                                   else f"Last update {int(age)}s ago"),
                    "ts": last_push,
                    "poll_site": poll_site,
                }
            else:
                state     = p.get_state() if p else {}
                sf_health = p.sf_health   if p else {"ok": None}
            colors   = _merge_colors(inst.get("colors", {}))

            # Enrich state with friendly name and per-station chain status
            stns_cfg = {str(s["id"]): s for s in inst.get("stations", [])}
            for sid, sdata in state.items():
                if sid in stns_cfg:
                    sdata.setdefault("station_name", stns_cfg[sid].get("name", sid))
                    cid = stns_cfg[sid].get("chain_id", "")
                    if cid:
                        cs = _chain_status(hub_server, chains, cid)
                        if cs:
                            sdata["chain_status"] = cs

            # Add placeholder entries for stations that haven't polled yet
            for sid, scfg in stns_cfg.items():
                if sid not in state:
                    placeholder = {
                        "station_id": sid, "station_name": scfg.get("name", sid),
                        "mode": MODE_UNKNOWN, "mode_name": "Unknown",
                        "status": ST_QUEUED, "status_name": "QUEUED",
                        "css_state": "z-unknown", "gap": "+00:00", "etm": "--:--:--",
                        "computer_name": None, "now_playing": None, "queue": [],
                        "is_spot": False, "error": "Waiting for first poll",
                        "remaining_seconds": 0, "duration_seconds": 0,
                    }
                    cid = scfg.get("chain_id", "")
                    if cid:
                        cs = _chain_status(hub_server, chains, cid)
                        if cs:
                            placeholder["chain_status"] = cs
                    state[sid] = placeholder

            out_insts.append({
                "id":         iid,
                "name":       inst.get("name", iid),
                "connected":  p.sf_health.get("ok") is not False if p else False,
                "last_error": p.sf_health.get("error") if p else None,
                "colors":     colors,
                "stations":   state,
            })

        return jsonify({"instances": out_insts, "chains": {}, "ts": time.time()})

    # ── Legacy /api/zetta/status (backward compat) ───────────────────────────
    @app.get("/api/zetta/status")
    @login_required
    def zetta_status():
        from flask import jsonify
        cfg = _load_cfg()
        merged = {}
        for inst in cfg.get("instances", []):
            iid = inst.get("id", "")
            p   = _pollers.get(iid)
            if not p: continue
            for sid, sdata in p.get_state().items():
                clean = {k: v for k, v in sdata.items()}
                merged[sid] = clean
        return jsonify({"stations": merged, "ts": time.time()})

    # ── Add instance ─────────────────────────────────────────────────────────
    @app.post("/api/zetta/instance/add")
    @login_required
    @csrf_protect
    def zetta_instance_add():
        from flask import request, jsonify
        import uuid
        data = request.get_json(silent=True) or {}
        name   = str(data.get("name",   "")).strip()
        sf_url = str(data.get("sf_url", "")).strip()
        if not name:
            return jsonify({"ok": False, "error": "name is required"})
        if not sf_url:
            return jsonify({"ok": False, "error": "Status Feed URL is required"})
        cfg   = _load_cfg()
        iid   = uuid.uuid4().hex[:10]
        raw_s = str(data.get("spots", "")).split(",")
        new_inst = {
            "id":               iid,
            "name":             name,
            "status_feed_url":  sf_url,
            "poll_interval":    max(5, min(120, int(data.get("interval", 10) or 10))),
            "timeout":          max(2, min(30,  int(data.get("timeout",  6)  or 6))),
            "spot_categories":  [c.strip().upper() for c in raw_s if c.strip()] or DEFAULT_SPOT_CATS,
            "poll_site":        str(data.get("poll_site", "")).strip(),
            "stations": [
                {
                    "id":       str(s.get("id","")).strip(),
                    "name":     str(s.get("name","")).strip(),
                    "chain_id": str(s.get("chain_id","")).strip(),
                }
                for s in data.get("stations", []) if str(s.get("id","")).strip()
            ],
            "colors": dict(DEFAULT_COLORS),
        }
        cfg.setdefault("instances", []).append(new_inst)
        _save_cfg(cfg)
        _restart_pollers(monitor.log)
        return jsonify({"ok": True, "id": iid})

    # ── Save instance ─────────────────────────────────────────────────────────
    @app.post("/api/zetta/instance/<iid>/save")
    @login_required
    @csrf_protect
    def zetta_instance_save(iid):
        from flask import request, jsonify
        data  = request.get_json(silent=True) or {}
        cfg   = _load_cfg()
        insts = cfg.get("instances", [])
        inst  = next((x for x in insts if x.get("id") == iid), None)
        if inst is None:
            return jsonify({"ok": False, "error": "instance not found"})
        raw_s = str(data.get("spots", "")).split(",")
        inst.update({
            "name":             str(data.get("name",    inst["name"])).strip(),
            "status_feed_url":  str(data.get("sf_url",  inst.get("status_feed_url",""))).strip(),
            "poll_interval":    max(5,  min(120, int(data.get("interval", inst.get("poll_interval",10)) or 10))),
            "timeout":          max(2,  min(30,  int(data.get("timeout",  inst.get("timeout", 6))    or 6))),
            "spot_categories":  [c.strip().upper() for c in raw_s if c.strip()] or DEFAULT_SPOT_CATS,
            "poll_site":        str(data.get("poll_site", inst.get("poll_site", ""))).strip(),
            "stations": [
                {
                    "id":       str(s.get("id","")).strip(),
                    "name":     str(s.get("name","")).strip(),
                    "chain_id": str(s.get("chain_id","")).strip(),
                }
                for s in data.get("stations", []) if str(s.get("id","")).strip()
            ],
        })
        user_colors = data.get("colors", {})
        inst["colors"] = _merge_colors(user_colors)
        _save_cfg(cfg)
        # Update live poller config reference
        p = _pollers.get(iid)
        if p:
            with _cfg_lock:
                p.cfg = inst
            # Restart this instance's poller
            p.stop()
            np = _InstancePoller(iid, inst, monitor.log)
            _pollers[iid] = np
            np.start()
        return jsonify({"ok": True})

    # ── Client site config endpoint ───────────────────────────────────────────
    # Client nodes call this to find out which Zetta instances they should poll.
    # Returns instance config filtered to instances assigned to the requesting site.
    @app.get("/api/zetta/site_config")
    def zetta_site_config():
        from flask import request, jsonify
        site = request.headers.get("X-Site", "").strip()
        if not site:
            return jsonify({"instances": []}), 400
        sdata = (hub_server._sites or {}).get(site, {})
        if not sdata.get("_approved"):
            return jsonify({"instances": []}), 403
        cfg = _load_cfg()
        matching = []
        for inst in cfg.get("instances", []):
            if inst.get("poll_site", "") == site:
                matching.append({
                    "id":              inst.get("id", ""),
                    "sf_url":          inst.get("status_feed_url", ""),
                    "stations":        inst.get("stations", []),
                    "spot_categories": inst.get("spot_categories", DEFAULT_SPOT_CATS),
                    "timeout":         inst.get("timeout", 6),
                    "poll_interval":   inst.get("poll_interval", 10),
                })
        # Include any pending discovery command for this site
        discover_cmd = None
        with _discover_lock:
            entry = _discover_pending.get(site)
            if entry and not entry.get("evt").is_set():
                discover_cmd = {"url": entry["url"]}
        return jsonify({"instances": matching, "discover_cmd": discover_cmd})

    # ── Client site data push endpoint ────────────────────────────────────────
    # Client nodes POST station data here after polling Zetta locally.
    @app.post("/api/zetta/site_data")
    def zetta_site_data():
        import hashlib as _hs, hmac as _hm
        from flask import request, jsonify
        site = request.headers.get("X-Site", "").strip()
        if not site:
            return jsonify({"ok": False}), 400
        sdata = (hub_server._sites or {}).get(site, {})
        if not sdata.get("_approved"):
            return jsonify({"ok": False}), 403
        # Optional HMAC verification
        secret = getattr(getattr(monitor.app_cfg, "hub", None), "secret_key", "") or ""
        if secret:
            sig  = request.headers.get("X-Hub-Sig", "")
            ts_s = request.headers.get("X-Hub-Ts",  "0")
            body = request.get_data()
            try:
                ts = float(ts_s)
                if abs(time.time() - ts) > 120:
                    return jsonify({"ok": False, "error": "timestamp expired"}), 403
                key      = _hs.sha256(f"{secret}:signing".encode()).digest()
                expected = _hm.new(key, f"{ts:.0f}:".encode() + body, _hs.sha256).hexdigest()
                if not _hm.compare_digest(sig, expected):
                    return jsonify({"ok": False, "error": "bad signature"}), 403
            except Exception:
                return jsonify({"ok": False, "error": "auth error"}), 403
        payload = request.get_json(silent=True) or {}
        iid     = str(payload.get("instance_id", "")).strip()
        sid     = str(payload.get("station_id",  "")).strip()
        stn_data = payload.get("data", {})
        if not iid or not sid or not isinstance(stn_data, dict):
            return jsonify({"ok": False, "error": "missing fields"}), 400
        with _remote_state_lock:
            if iid not in _remote_state:
                _remote_state[iid] = {}
            _remote_state[iid][sid] = stn_data
        return jsonify({"ok": True})

    # ── Client-side Zetta polling loop ────────────────────────────────────────
    # Runs only on client nodes: polls hub for Zetta config, calls SOAP locally
    # (client has LAN access to Zetta), pushes results back to hub.
    cfg_ss  = monitor.app_cfg
    mode    = getattr(getattr(cfg_ss, "hub", None), "mode", "standalone") or "standalone"
    hub_url = (getattr(getattr(cfg_ss, "hub", None), "hub_url", "") or "").rstrip("/")

    if mode == "client" and hub_url:
        import hashlib as _hs_c, hmac as _hm_c

        def _client_zetta_loop():
            while True:
                try:
                    _cfg_ss   = monitor.app_cfg
                    _hub_url  = (getattr(getattr(_cfg_ss, "hub", None), "hub_url", "") or "").rstrip("/")
                    _site     = getattr(getattr(_cfg_ss, "hub", None), "site_name", "") or ""
                    _secret   = getattr(getattr(_cfg_ss, "hub", None), "secret_key", "") or ""
                    _mode     = getattr(getattr(_cfg_ss, "hub", None), "mode", "") or ""
                    if _mode != "client" or not _hub_url or not _site:
                        time.sleep(15)
                        continue
                    # Fetch which instances this site should poll
                    try:
                        _req = urllib.request.Request(
                            f"{_hub_url}/api/zetta/site_config",
                            headers={"X-Site": _site},
                        )
                        with urllib.request.urlopen(_req, timeout=8) as _r:
                            _config = json.loads(_r.read())
                    except Exception as _e:
                        _log.debug("[Zetta client] config fetch: %s", _e)
                        time.sleep(15)
                        continue
                    _instances   = _config.get("instances", [])
                    _disc_cmd    = _config.get("discover_cmd")
                    # Handle any pending discover command from the hub
                    if _disc_cmd:
                        _disc_url = (_disc_cmd.get("url") or "").strip()
                        if _disc_url:
                            try:
                                _disc_stations = _sf_get_stations(_disc_url, timeout=10)
                                _disc_result   = {"ok": True, "stations": _disc_stations} \
                                                 if _disc_stations else \
                                                 {"ok": False, "error": "No stations returned", "stations": []}
                            except Exception as _de:
                                _disc_result = {"ok": False, "error": str(_de), "stations": []}
                            _disc_body = json.dumps({"result": _disc_result}).encode()
                            _ts_d = time.time()
                            if _secret:
                                _key_d  = _hs_c.sha256(f"{_secret}:signing".encode()).digest()
                                _sig_d  = _hm_c.new(_key_d, f"{_ts_d:.0f}:".encode() + _disc_body, _hs_c.sha256).hexdigest()
                            else:
                                _sig_d = ""
                            try:
                                _dr = urllib.request.Request(
                                    f"{_hub_url}/api/zetta/discover_result",
                                    data=_disc_body, method="POST",
                                    headers={
                                        "Content-Type": "application/json",
                                        "X-Site":    _site,
                                        "X-Hub-Sig": _sig_d,
                                        "X-Hub-Ts":  f"{_ts_d:.0f}",
                                    },
                                )
                                urllib.request.urlopen(_dr, timeout=10).close()
                            except Exception as _dre:
                                _log.debug("[Zetta client] discover_result push: %s", _dre)
                    if not _instances:
                        time.sleep(15)
                        continue
                    # Poll each instance's stations using raw SOAP (no zeep needed on client)
                    for _inst in _instances:
                        _iid      = _inst.get("id", "")
                        _sf_url   = (_inst.get("sf_url") or "").strip()
                        _stations = _inst.get("stations", [])
                        _s_cats   = _inst.get("spot_categories", DEFAULT_SPOT_CATS)
                        _timeout  = int(_inst.get("timeout", 6) or 6)
                        if not _sf_url or not _stations:
                            continue
                        try:
                            _ns_val = _ns(_sf_url, _timeout)
                            _ep     = _soap_endpoint(_sf_url, _timeout)
                        except Exception:
                            continue
                        for _st in _stations:
                            _sid = str(_st.get("id", "")).strip()
                            if not _sid:
                                continue
                            try:
                                _body = f"<stationID>{_sid}</stationID>"
                                _root, _ = _soap_call(_ep, "GetStationFull",
                                                      _body, _ns_val, _timeout)
                                _sdata = _parse_station_full(_root, _sid, _s_cats)
                            except Exception as _pe:
                                _log.debug("[Zetta client] station %s: %s", _sid, _pe)
                                _sdata = {"station_id": _sid,
                                          "error": str(_pe), "ts": time.time()}
                            # Push result to hub
                            _push_body = json.dumps({
                                "instance_id": _iid,
                                "station_id":  _sid,
                                "data":        _sdata,
                            }).encode()
                            _ts = time.time()
                            if _secret:
                                _key = _hs_c.sha256(f"{_secret}:signing".encode()).digest()
                                _sig = _hm_c.new(
                                    _key,
                                    f"{_ts:.0f}:".encode() + _push_body,
                                    _hs_c.sha256,
                                ).hexdigest()
                            else:
                                _sig = ""
                            try:
                                _push_req = urllib.request.Request(
                                    f"{_hub_url}/api/zetta/site_data",
                                    data=_push_body, method="POST",
                                    headers={
                                        "Content-Type": "application/json",
                                        "X-Site":       _site,
                                        "X-Hub-Sig":    _sig,
                                        "X-Hub-Ts":     f"{_ts:.0f}",
                                    },
                                )
                                urllib.request.urlopen(_push_req, timeout=8).close()
                            except Exception as _upe:
                                _log.debug("[Zetta client] push station %s: %s", _sid, _upe)
                    # Respect poll interval from first instance (all share one loop)
                    _sleep = int((_instances[0].get("poll_interval") or 10))
                    for _ in range(max(1, _sleep)):
                        time.sleep(1)
                except Exception as _le:
                    _log.debug("[Zetta client] loop error: %s", _le)
                    time.sleep(15)

        threading.Thread(target=_client_zetta_loop, daemon=True,
                         name="ZettaClientPoll").start()
        monitor.log("[Zetta] Client polling thread started")

    # ── Delete instance ───────────────────────────────────────────────────────
    @app.post("/api/zetta/instance/<iid>/delete")
    @login_required
    @csrf_protect
    def zetta_instance_delete(iid):
        from flask import jsonify
        cfg   = _load_cfg()
        insts = [x for x in cfg.get("instances", []) if x.get("id") != iid]
        cfg["instances"] = insts
        _save_cfg(cfg)
        p = _pollers.pop(iid, None)
        if p: p.stop()
        return jsonify({"ok": True})

    # ── Debug SOAP call (zeep) ────────────────────────────────────────────────
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
        if not _zeep:
            return jsonify({"ok": False,
                            "error": "zeep not available — restart SignalScope to retry install",
                            "raw": ""})
        try:
            sep    = "&" if "?" in url else "?"
            client = _make_zeep_client(url + sep + "wsdl", url)

            # Parse body as "key:value" lines (one per line).
            # e.g. "stationId:1" → {"stationId": 1}
            kwargs: dict = {}
            for raw_line in body.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" in line:
                    k, _, v = line.partition(":")
                else:
                    continue
                k = k.strip()
                v = v.strip()
                if not k:
                    continue
                if v.lower() == "true":
                    kwargs[k] = True
                elif v.lower() == "false":
                    kwargs[k] = False
                else:
                    try:    kwargs[k] = int(v)
                    except ValueError: kwargs[k] = v

            svc_method = getattr(client.service, method, None)
            if svc_method is None:
                # List available methods to help the user
                try:
                    available = sorted(str(op) for op in client.wsdl.services.values()
                                       for p in op.ports.values()
                                       for op2 in p.binding._operations.values()
                                       for op in [op2.name])
                except Exception:
                    available = []
                return jsonify({"ok": False,
                                "error": f"Method '{method}' not found in WSDL",
                                "raw": "Available: " + ", ".join(available) if available else ""})

            # Remap kwargs keys case-insensitively to match WSDL parameter names.
            # e.g. user types <StationId> but WSDL defines stationID → remap to stationID.
            # inspect.signature doesn't work on zeep proxies — read directly from WSDL.
            if kwargs:
                try:
                    sig_params: list = []
                    # Primary: navigate zeep WSDL internals (document/literal .asmx style)
                    try:
                        svc0  = list(client.wsdl.services.values())[0]
                        port0 = list(svc0.ports.values())[0]
                        op0   = port0.binding.get(method)
                        body0 = getattr(op0.input, "body", None)
                        if body0 is not None:
                            t = getattr(body0, "type", None)
                            if t is not None and hasattr(t, "elements"):
                                sig_params = [name for name, _ in t.elements]
                    except Exception:
                        pass
                    # Fallback: inspect.signature (works on some zeep versions)
                    if not sig_params:
                        import inspect as _inspect
                        raw = list(_inspect.signature(svc_method).parameters.keys())
                        if raw and raw not in (["args", "kwargs"], ["_args", "_kwargs"]):
                            sig_params = raw
                    if sig_params:
                        lower_map = {p.lower(): p for p in sig_params}
                        kwargs = {lower_map.get(k.lower(), k): v for k, v in kwargs.items()}
                except Exception:
                    pass  # fall through with original keys

            result = svc_method(**kwargs)

            # Serialize zeep object → readable string
            try:
                serialized = _zeep.helpers.serialize_object(result, target_cls=dict)
                import json as _json
                raw = _json.dumps(serialized, indent=2, default=str)
            except Exception:
                raw = str(result)

            return jsonify({"ok": True, "raw": raw})

        except Exception as e:
            return jsonify({"ok": False, "error": str(e), "raw": ""})

    # ── WSDL methods (zeep) ───────────────────────────────────────────────────
    @app.post("/api/zetta/wsdl_methods")
    @login_required
    @csrf_protect
    def zetta_wsdl_methods():
        from flask import request, jsonify
        data = request.get_json(silent=True) or {}
        url  = str(data.get("url", "")).strip()
        if not url:
            return jsonify({"error": "No URL", "methods": []})
        if not _zeep:
            return jsonify({"error": "zeep not available", "methods": []})
        try:
            sep    = "&" if "?" in url else "?"
            client = _make_zeep_client(url + sep + "wsdl", url)
            methods = []
            for svc in client.wsdl.services.values():
                for port in svc.ports.values():
                    for op_name in port.binding._operations:
                        if op_name not in methods:
                            methods.append(op_name)
            ns = client.wsdl.target_namespace or ""
            if methods:
                return jsonify({"namespace": ns, "methods": sorted(methods)})
            return jsonify({"error": "No operations found", "namespace": ns, "methods": []})
        except Exception as e:
            return jsonify({"error": str(e), "methods": []})

    # ── Discover stations ─────────────────────────────────────────────────────
    @app.post("/api/zetta/discover_stations")
    @login_required
    @csrf_protect
    def zetta_discover_stations():
        from flask import request, jsonify
        try:
            data      = request.get_json(silent=True) or {}
            url       = str(data.get("url",       "")).strip()
            poll_site = str(data.get("poll_site", "")).strip()
            if not url:
                return jsonify({"ok": False, "error": "No URL provided", "stations": []})

            if poll_site:
                # Hub can't reach Zetta directly — queue a discover command for the
                # client site and wait for it to push results back.
                evt = threading.Event()
                with _discover_lock:
                    _discover_pending[poll_site] = {
                        "url": url, "evt": evt, "result": None, "ts": time.time()
                    }
                got = evt.wait(timeout=25)
                with _discover_lock:
                    entry  = _discover_pending.pop(poll_site, {})
                    result = entry.get("result")
                if not got or result is None:
                    return jsonify({"ok": False,
                                    "error": f"Site '{poll_site}' did not respond in time — "
                                             "is it online and connected to the hub?",
                                    "stations": []})
                return jsonify(result)

            # Hub-direct: use raw SOAP (no zeep required)
            stations = _sf_get_stations(url, timeout=10)
            if not stations:
                return jsonify({"ok": False,
                                "error": "No stations returned by GetStations",
                                "stations": []})
            return jsonify({"ok": True, "stations": stations})
        except Exception as e:
            _log.exception("[Zetta] discover_stations error")
            return jsonify({"ok": False, "error": str(e), "stations": []})

    # ── Discover result (client pushes GetStations results back to hub) ────────
    @app.post("/api/zetta/discover_result")
    def zetta_discover_result():
        import hashlib as _hs, hmac as _hm
        from flask import request, jsonify
        site = request.headers.get("X-Site", "").strip()
        if not site:
            return jsonify({"ok": False}), 400
        sdata = (hub_server._sites or {}).get(site, {})
        if not sdata.get("_approved"):
            return jsonify({"ok": False}), 403
        # Optional HMAC verification
        secret = getattr(getattr(monitor.app_cfg, "hub", None), "secret_key", "") or ""
        if secret:
            sig  = request.headers.get("X-Hub-Sig", "")
            ts_s = request.headers.get("X-Hub-Ts",  "0")
            body = request.get_data()
            try:
                ts = float(ts_s)
                if abs(time.time() - ts) > 120:
                    return jsonify({"ok": False, "error": "timestamp expired"}), 403
                key      = _hs.sha256(f"{secret}:signing".encode()).digest()
                expected = _hm.new(key, f"{ts:.0f}:".encode() + body, _hs.sha256).hexdigest()
                if not _hm.compare_digest(sig, expected):
                    return jsonify({"ok": False, "error": "bad signature"}), 403
            except Exception:
                return jsonify({"ok": False, "error": "auth error"}), 403
        payload = request.get_json(silent=True) or {}
        result  = payload.get("result")
        with _discover_lock:
            entry = _discover_pending.get(site)
            if entry:
                entry["result"] = result
                entry["evt"].set()
        return jsonify({"ok": True})

    # ── Chain stations map (used by Broadcast Chains page) ───────────────────
    @app.get("/api/zetta/chain_stations")
    @login_required
    def zetta_chain_stations():
        from flask import jsonify
        cfg = _load_cfg()
        result: Dict[str, list] = {}
        for inst in cfg.get("instances", []):
            iid = inst.get("id", "")
            p   = _pollers.get(iid)
            state = p.get_state() if p else {}
            connected = p.sf_health.get("ok") is not False if p else False
            for stn in inst.get("stations", []):
                cid = str(stn.get("chain_id", "") or "").strip()
                if not cid:
                    continue
                sid   = str(stn.get("id", "")).strip()
                sdata = state.get(sid, {})
                result.setdefault(cid, []).append({
                    "station_id":   sid,
                    "station_name": stn.get("name") or sid,
                    "status_name":  sdata.get("status_name", "QUEUED"),
                    "css_state":    sdata.get("css_state",   "z-unknown"),
                    "now_playing":  sdata.get("now_playing"),
                    "remaining_seconds": sdata.get("remaining_seconds", 0),
                    "duration_seconds":  sdata.get("duration_seconds",  0),
                    "play_start_time":   sdata.get("play_start_time",   0),
                    "connected":    connected,
                })
        return jsonify(result)

    # ── Public is_spot_block ──────────────────────────────────────────────────
    def is_spot_block(station_id: str) -> bool:
        """Return True if any Zetta instance reports this station in a spot break."""
        sid = str(station_id)
        for p in _pollers.values():
            st = p.get_state().get(sid, {})
            if st.get("is_spot"):
                return True
        return False

    monitor.log("[Zetta] Plugin v2.1.1 registered — /hub/zetta")
