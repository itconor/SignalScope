# logger.py — SignalScope compliance logger plugin
# Drop alongside signalscope.py

SIGNALSCOPE_PLUGIN = {
    "id":      "logger",
    "label":   "Logger",
    "url":     "/hub/logger",
    "icon":    "🎙",
    "version": "1.5.4",
}

import datetime
import fcntl
import hashlib as _hashlib
import hmac as _hmac_mod
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import urllib.parse as _urllib_parse
import urllib.request as _urllib_req
from pathlib import Path

from flask import abort, jsonify, render_template_string, request, Response, send_file, session

# ─── Constants ────────────────────────────────────────────────────────────────
_SEG_SECS      = 300        # 5-minute segments
_SILENCE_DB    = -55.0      # dBFS threshold
_SILENCE_DUR   = 1.0        # min silence duration (s)
_DEFAULT_HQ    = "128k"
_DEFAULT_LQ    = "48k"
_DEFAULT_LQ_AFTER = 30      # days before quality downgrade
_DEFAULT_RETAIN   = 90      # days before deletion
_CONFIG_FILE   = "logger_config.json"
_REC_DIR       = "logger_recordings"
_DB_FILE       = "logger_index.db"

# Recording format definitions: fmt → (codec_args, ffmpeg_container, file_ext)
_REC_FORMATS = {
    # fmt: (codec_args, ffmpeg_container, file_ext, output_sample_rate)
    # libopus only accepts 8000/12000/16000/24000/48000 Hz — must use 48000, not 44100
    "mp3":  ([],                   "mp3",  "mp3",  "44100"),
    "aac":  (["-c:a", "aac"],      "adts", "aac",  "44100"),
    "opus": (["-c:a", "libopus"],  "ogg",  "opus", "48000"),
}
_AUDIO_GLOBS = ("*.mp3", "*.aac", "*.opus")

# ─── Module state ─────────────────────────────────────────────────────────────
_monitor        = None
_app_dir        = None
_listen_registry = None   # set in register() on hub nodes
_meta_pollers: dict = {}   # slug → _MetaPoller
_meta_lock      = threading.Lock()

# Background move status (shared between move thread and status endpoint)
_move_status      = {"active": False, "current": "", "done": 0, "total": 0, "error": ""}
_move_status_lock = threading.Lock()


def _rec_root() -> Path:
    """Return the absolute recordings root directory.

    Reads ``rec_dir`` from config.  If it is an absolute path it is used as-is;
    if relative it is resolved relative to the plugin directory; if blank the
    default ``logger_recordings`` subdirectory is used.
    """
    with _cfg_lock:
        raw = _cfg.get("rec_dir", "").strip()
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else (_app_dir / p).resolve()
    return (_app_dir / _REC_DIR).resolve()
_cfg       = {}
_cfg_lock  = threading.Lock()
_recorders = {}   # slug → _RecorderThread
_rec_lock  = threading.Lock()

# ─── Hub aggregation state (hub-only) ────────────────────────────────────────
_hub_logger_streams  = {}   # site → [{"slug":..., "name":...}]
_hub_logger_catalog  = {}   # site → {slug: {name, owner, rec_format, updated}}
_hub_logger_pending  = {}   # site → pending cmd dict or None
_hub_logger_days     = {}   # "{site}:{slug}" → [date_str, ...]
_hub_logger_segs     = {}   # "{site}:{slug}:{date}" → [seg_dict, ...]
_hub_logger_active   = {}   # site → current play slot_id
_hub_logger_meta     = {}   # "{site}:{slug}:{date}" → [event_dict, ...]
_hub_logger_lock     = threading.Lock()
_hub_logger_events   = {}   # site → threading.Event for long-poll wakeup

def _hub_set_pending(site: str, cmd: dict):
    """Store a command for a site and wake any waiting long-poll connection."""
    with _hub_logger_lock:
        _hub_logger_pending[site] = cmd
        evt = _hub_logger_events.get(site)
        if evt:
            evt.set()


# ─── HMAC signing helpers ─────────────────────────────────────────────────────

def _make_sig(secret: str, data: bytes, ts: float) -> str:
    key = _hashlib.sha256(f"{secret}:signing".encode()).digest()
    msg = f"{ts:.0f}:".encode() + data
    return _hmac_mod.new(key, msg, _hashlib.sha256).hexdigest()


def _check_sig(secret: str, data: bytes) -> bool:
    """Verify X-Hub-Sig header on incoming client request. Returns True if no secret configured."""
    if not secret:
        return True
    sig   = request.headers.get("X-Hub-Sig", "")
    ts_h  = request.headers.get("X-Hub-Ts", "0")
    if not sig:
        return False
    try:
        ts = float(ts_h)
    except ValueError:
        return False
    if abs(time.time() - ts) > 120:
        return False
    expected = _make_sig(secret, data, ts)
    return _hmac_mod.compare_digest(sig, expected)


# ─────────────────────────────────────────────────────────────────────────────
#  CLIENT → HUB: AUDIO PUSH & POLLER
# ─────────────────────────────────────────────────────────────────────────────

_CHUNK_BYTES  = 9600   # 0.1 s of 48 kHz mono 16-bit PCM
_CHUNK_DUR    = 0.1    # seconds per single chunk
_BATCH_CHUNKS = 16     # chunks concatenated into one HTTP POST (= 1.6 s per POST)
_BATCH_BYTES  = _CHUNK_BYTES * _BATCH_CHUNKS
_BATCH_DUR    = _CHUNK_DUR   * _BATCH_CHUNKS   # 1.6 s
_PUSH_RATE    = 3.0    # push at 3× real-time; large pre-buffer absorbs jitter


def _push_audio_to_relay(hub_url, slot_id, slug, date, filename, seek_s, cfg):
    """Push PCM audio to a hub relay slot by transcoding a recorded segment with ffmpeg.

    Batches 16 raw PCM blocks (1.6 s of audio) per HTTP POST to amortise
    WAN round-trip cost.  Pushes at 3× real-time so the relay buffer stays
    4–8 s ahead of playback; the browser's 5 s pre-buffer absorbs jitter.
    """
    path = _stream_rec_root_by_slug(_safe(slug)) / _safe(slug) / date / filename
    if not path.exists():
        _log(f"[Logger] AudioPush: file not found: {path}")
        return

    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error",
           "-ss", str(seek_s), "-i", str(path),
           "-ac", "1", "-ar", "48000", "-f", "s16le", "pipe:1"]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except Exception as e:
        _log(f"[Logger] AudioPush: ffmpeg launch failed: {e}")
        return

    secret    = (getattr(cfg.hub, "secret_key", None) or "").strip()
    chunk_url = f"{hub_url}/api/v1/audio_chunk/{slot_id}"
    _log(f"[Logger] AudioPush: starting {slug}/{date}/{filename} seek={seek_s:.1f}s → slot {slot_id}")

    push_start = time.monotonic()
    batch_n    = 0
    total_chunks = 0

    try:
        buf = b""
        while True:
            piece = proc.stdout.read(_CHUNK_BYTES)
            if not piece:
                # Flush any remaining partial batch
                if buf:
                    _audio_post(chunk_url, buf, secret)
                    total_chunks += len(buf) // _CHUNK_BYTES
                break

            buf += piece
            if len(buf) < _BATCH_BYTES:
                continue   # keep accumulating

            batch_n += 1
            total_chunks += _BATCH_CHUNKS

            # Rate-limit: pace to 3× real-time so we stay ahead without
            # flooding the relay queue on a fast LAN.
            target_t = push_start + batch_n * _BATCH_DUR / _PUSH_RATE
            wait     = target_t - time.monotonic()
            if wait > 0:
                time.sleep(wait)

            if not _audio_post(chunk_url, buf, secret):
                break   # slot closed or unrecoverable error
            buf = b""

    finally:
        _log(f"[Logger] AudioPush: finished {total_chunks} chunks ({total_chunks*_CHUNK_DUR:.1f}s) for {slug}/{date}/{filename}")
        try:
            proc.kill()
        except Exception:
            pass


def _audio_post(chunk_url: str, data: bytes, secret: str) -> bool:
    """POST a PCM batch to the relay endpoint.  Returns False if the slot is gone."""
    ts      = time.time()
    headers = {"Content-Type": "application/octet-stream"}
    if secret:
        sig   = _make_sig(secret, data, ts)
        nonce = _hashlib.md5(os.urandom(8)).hexdigest()[:16]
        headers["X-Hub-Sig"]   = sig
        headers["X-Hub-Ts"]    = f"{ts:.0f}"
        headers["X-Hub-Nonce"] = nonce
    req = _urllib_req.Request(chunk_url, data=data, method="POST", headers=headers)
    try:
        _urllib_req.urlopen(req, timeout=10).close()
        return True
    except Exception as e:
        err = str(e)
        if "404" in err or "HTTP Error 404" in err:
            _log(f"[Logger] AudioPush: slot closed")
        else:
            _log(f"[Logger] AudioPush: POST failed: {e}")
        return False


def _hub_logger_poller():
    """Background thread: registers local streams with the hub and handles playback commands."""
    _registered_ts = 0.0
    _last_hub_url  = None

    while True:
        try:
            cfg = _monitor.app_cfg if _monitor else None
            if cfg is None:
                time.sleep(5)
                continue

            hub_url = (getattr(cfg.hub, "hub_url", None) or "").strip().rstrip("/")
            site    = (getattr(cfg.hub, "site_name", None) or "").strip()
            secret  = (getattr(cfg.hub, "secret_key", None) or "").strip()

            if not hub_url or not site:
                time.sleep(10)
                continue

            # Log once when hub_url is first detected
            if hub_url != _last_hub_url:
                _log(f"[Logger] HubPoller: connecting to hub {hub_url} as site '{site}'")
                _last_hub_url = hub_url

            now = time.time()

            # ── Registration (every 60 s) ───────────────────────────────────
            if now - _registered_ts >= 60:
                try:
                    streams = [{"slug": s["slug"], "name": s["name"]}
                               for s in _available_streams()]
                    # Include catalog entries from all recording roots
                    catalog = {}
                    for _root in _all_rec_roots():
                        catalog.update(_catalog_read(_root))
                    payload = json.dumps({"site": site, "streams": streams, "catalog": catalog}).encode()
                    ts      = time.time()
                    headers = {"Content-Type": "application/json"}
                    if secret:
                        headers["X-Hub-Sig"]   = _make_sig(secret, payload, ts)
                        headers["X-Hub-Ts"]    = f"{ts:.0f}"
                        headers["X-Hub-Nonce"] = _hashlib.md5(os.urandom(8)).hexdigest()[:16]
                    req = _urllib_req.Request(
                        f"{hub_url}/api/logger/hub/register",
                        data=payload, method="POST", headers=headers)
                    _urllib_req.urlopen(req, timeout=10).close()
                    _registered_ts = time.time()
                    _log(f"[Logger] HubPoller: registered {len(streams)} stream(s) with hub")
                except Exception as e:
                    _log(f"[Logger] HubPoller: registration failed: {e}")

            # ── Command poll (long-poll: blocks up to 25 s on the hub) ────────
            # No sleep between polls — the hub holds the connection open until a
            # command is available or 25 s elapse, so we reconnect immediately.
            try:
                ts      = time.time()
                headers = {}
                if secret:
                    headers["X-Hub-Sig"]   = _make_sig(secret, b"", ts)
                    headers["X-Hub-Ts"]    = f"{ts:.0f}"
                    headers["X-Hub-Nonce"] = _hashlib.md5(os.urandom(8)).hexdigest()[:16]
                site_enc = _urllib_parse.quote(site, safe="")
                req = _urllib_req.Request(
                    f"{hub_url}/api/logger/hub/poll/{site_enc}",
                    method="GET", headers=headers)
                resp = _urllib_req.urlopen(req, timeout=30)  # 30 s > 25 s hub hold
                data = json.loads(resp.read())
                resp.close()

                cmd = data.get("cmd")
                if cmd:
                    ctype = cmd.get("type", "")
                    _log(f"[Logger] HubPoller: got command type='{ctype}' slug='{cmd.get('slug','')}' date='{cmd.get('date','')}'")

                    if ctype == "days":
                        slug  = cmd.get("slug", "")
                        sroot = _stream_rec_root_by_slug(_safe(slug))
                        sdir  = sroot / _safe(slug)
                        if sdir.exists():
                            days = sorted(
                                [d.name for d in sdir.iterdir()
                                 if d.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", d.name)],
                                reverse=True)
                        else:
                            days = []
                        _log(f"[Logger] HubPoller: days for slug='{slug}' root={sroot}: {days}")
                        _hub_result_post(hub_url, secret, site,
                                         {"site": site, "type": "days", "slug": slug, "days": days})

                    elif ctype == "segments":
                        slug  = cmd.get("slug", "")
                        date  = cmd.get("date", "")
                        sroot = _stream_rec_root_by_slug(_safe(slug))
                        segs  = _get_segments(_safe(slug), date, base_root=sroot) if slug and date else []
                        _log(f"[Logger] HubPoller: segments for slug='{slug}' date='{date}': {len(segs)} segs")
                        _hub_result_post(hub_url, secret, site,
                                         {"site": site, "type": "segments",
                                          "slug": slug, "date": date, "segments": segs})

                    elif ctype == "play":
                        slot_id  = cmd.get("slot_id", "")
                        slug     = cmd.get("slug", "")
                        date     = cmd.get("date", "")
                        filename = cmd.get("filename", "")
                        seek_s   = float(cmd.get("seek_s", 0))
                        _log(f"[Logger] HubPoller: play {slug}/{date}/{filename} seek={seek_s:.1f}s slot={slot_id}")
                        threading.Thread(
                            target=_push_audio_to_relay,
                            args=(hub_url, slot_id, slug, date, filename, seek_s, cfg),
                            daemon=True, name="LoggerAudioPush").start()

                    elif ctype == "metadata":
                        slug  = cmd.get("slug", "")
                        date  = cmd.get("date", "")
                        try:
                            midnight = datetime.datetime.strptime(date, "%Y-%m-%d").replace(
                                tzinfo=datetime.timezone.utc).timestamp()
                        except Exception:
                            midnight = 0.0
                        events = _meta_query(slug, midnight)
                        _hub_result_post(hub_url, secret, site,
                                         {"site": site, "type": "metadata",
                                          "slug": slug, "date": date, "events": events})

                    else:
                        _log(f"[Logger] HubPoller: unknown command type '{ctype}'")

            except Exception as e:
                _log(f"[Logger] HubPoller: poll failed: {e}")
                time.sleep(2)  # back-off only on error to avoid hammering on network failure

        except Exception as e:
            _log(f"[Logger] HubPoller: outer error: {e}")
            time.sleep(2)


def _hub_result_post(hub_url, secret, site, payload_dict):
    """POST a result back to the hub."""
    payload = json.dumps(payload_dict).encode()
    ts      = time.time()
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-Hub-Sig"]   = _make_sig(secret, payload, ts)
        headers["X-Hub-Ts"]    = f"{ts:.0f}"
        headers["X-Hub-Nonce"] = _hashlib.md5(os.urandom(8)).hexdigest()[:16]
    req = _urllib_req.Request(
        f"{hub_url}/api/logger/hub/result",
        data=payload, method="POST", headers=headers)
    try:
        resp = _urllib_req.urlopen(req, timeout=10)
        resp.close()
        _log(f"[Logger] HubPoller: result POST ok type='{payload_dict.get('type')}'")
    except Exception as e:
        _log(f"[Logger] HubPoller: result POST failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  REGISTRATION
# ─────────────────────────────────────────────────────────────────────────────

def register(app, ctx):
    global _monitor, _app_dir, _listen_registry
    _monitor       = ctx["monitor"]
    login_req      = ctx["login_required"]
    csrf_dec       = ctx["csrf_protect"]
    mobile_api_req = ctx.get("mobile_api_required", login_req)  # Bearer-token auth for /api/mobile/*
    _app_dir       = Path(__file__).parent
    hub_server     = ctx.get("hub_server")

    _load_config()
    _init_db()
    _rec_root().mkdir(parents=True, exist_ok=True)

    threading.Thread(target=_delayed_start, daemon=True, name="LoggerInit").start()
    threading.Thread(target=_maintenance_loop, daemon=True, name="LoggerMaint").start()

    # Start hub poller on client nodes (also started unconditionally — checks hub_url each loop)
    threading.Thread(target=_hub_logger_poller, daemon=True, name="LoggerHubPoller").start()

    if hub_server is not None:
        _listen_registry = ctx["listen_registry"]

    @app.route("/hub/logger")
    @login_req
    def logger_page():
        return render_template_string(_TPL)

    @app.get("/api/logger/streams")
    @login_req
    def api_logger_streams():
        streams = _available_streams()
        # Merge streams from shared catalog files (other instances)
        local_slugs = {s["slug"] for s in streams}
        for root in _all_rec_roots():
            for slug, info in _catalog_read(root).items():
                if slug not in local_slugs:
                    streams.append({"name": info["name"], "slug": slug,
                                    "url": "", "catalog": True,
                                    "owner": info.get("owner", "")})
                    local_slugs.add(slug)
        return jsonify(streams)

    @app.get("/api/logger/config")
    @login_req
    def api_logger_get_config():
        with _cfg_lock:
            return jsonify(dict(_cfg))

    @app.post("/api/logger/config")
    @login_req
    @csrf_dec
    def api_logger_save_config():
        data = request.get_json(force=True) or {}

        # ── Validate base_dirs ───────────────────────────────────────────
        new_base_dirs = data.get("base_dirs", None)
        if new_base_dirs is not None:
            if not isinstance(new_base_dirs, list):
                return jsonify({"ok": False, "error": "base_dirs must be a list"}), 400
            for bd in new_base_dirs:
                name = str(bd.get("name", "")).strip()
                path_str = str(bd.get("path", "")).strip()
                if not name:
                    return jsonify({"ok": False, "error": "Each base directory must have a name"}), 400
                if not path_str:
                    return jsonify({"ok": False, "error": f"Base directory '{name}' has no path"}), 400
                if ".." in Path(path_str).parts:
                    return jsonify({"ok": False, "error": f"Path for '{name}' must not contain .."}), 400

        # ── Validate global rec_dir ──────────────────────────────────────
        new_rec_dir = str(data.get("rec_dir", "")).strip()
        if new_rec_dir and ".." in Path(new_rec_dir).parts:
            return jsonify({"ok": False, "error": "rec_dir must not contain .."}), 400

        # ── Detect per-stream base_dir changes (before we update _cfg) ───
        # Compute the default root BEFORE acquiring _cfg_lock — _rec_root() also
        # acquires _cfg_lock, and threading.Lock is not reentrant; calling it
        # inside the block below would deadlock the request thread forever.
        default_old_root = _rec_root()
        new_streams = data.get("streams", {})
        moves_needed = []   # list of (stream_name, old_root, new_root)
        with _cfg_lock:
            old_base_dirs = {bd["name"]: bd["path"]
                             for bd in _cfg.get("base_dirs", [])
                             if bd.get("name") and bd.get("path")}
            for stream_name, new_scfg in new_streams.items():
                old_scfg    = _cfg.get("streams", {}).get(stream_name, {})
                old_bd_name = old_scfg.get("base_dir", "").strip()
                new_bd_name = str(new_scfg.get("base_dir", "")).strip()
                if old_bd_name == new_bd_name:
                    continue
                # Resolve old root (use pre-computed default — no lock re-entry)
                if old_bd_name and old_bd_name in old_base_dirs:
                    old_root = _resolve_path(old_base_dirs[old_bd_name])
                else:
                    old_root = default_old_root
                # Resolve new root (using incoming base_dirs list)
                new_bd_map = {bd["name"]: bd["path"]
                              for bd in (new_base_dirs or _cfg.get("base_dirs", []))
                              if bd.get("name") and bd.get("path")}
                if new_bd_name and new_bd_name in new_bd_map:
                    new_root = _resolve_path(new_bd_map[new_bd_name])
                else:
                    new_root = (_resolve_path(new_rec_dir) if new_rec_dir
                                else (_app_dir / _REC_DIR).resolve())
                _log(f"[Logger] Move queued: '{stream_name}' {old_root} → {new_root}")
                moves_needed.append((stream_name, old_root, new_root))

        # ── Save config FIRST so new recordings land in the right place ─
        # Moves are backgrounded — do not block the HTTP request.
        with _cfg_lock:
            _cfg["streams"] = new_streams
            _cfg["rec_dir"] = new_rec_dir
            if new_base_dirs is not None:
                _cfg["base_dirs"] = new_base_dirs
            new_mic_key = str(data.get("mic_api_key", "")).strip()
            _cfg["mic_api_key"] = new_mic_key
            _save_config()

        # Ensure all configured roots exist
        try:
            _rec_root().mkdir(parents=True, exist_ok=True)
            for root in _all_rec_roots():
                root.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return jsonify({"ok": False, "error": f"Cannot create recordings directory: {e}"}), 400

        _reconcile_recorders()

        # ── Kick off background moves (non-blocking) ─────────────────────
        if moves_needed:
            def _bg_moves(moves):
                with _move_status_lock:
                    _move_status["active"]  = True
                    _move_status["done"]    = 0
                    _move_status["total"]   = len(moves)
                    _move_status["current"] = ""
                    _move_status["error"]   = ""
                for stream_name, old_root, new_root in moves:
                    with _move_status_lock:
                        _move_status["current"] = stream_name
                    try:
                        if old_root != new_root:
                            new_root.mkdir(parents=True, exist_ok=True)
                            _move_stream_recordings(stream_name, old_root, new_root)
                    except Exception as e:
                        _log(f"[Logger] Background move failed for '{stream_name}': {e}")
                        with _move_status_lock:
                            _move_status["error"] = f"{stream_name}: {e}"
                    with _move_status_lock:
                        _move_status["done"] += 1
                with _move_status_lock:
                    _move_status["active"]  = False
                    _move_status["current"] = ""
                _log(f"[Logger] Background move complete: {len(moves)} stream(s)")

            threading.Thread(target=_bg_moves, args=(moves_needed,),
                             daemon=True, name="LoggerMoveRecordings").start()
            return jsonify({"ok": True, "moves": len(moves_needed), "moving": True})

        return jsonify({"ok": True, "moves": 0})

    @app.get("/api/logger/move_status")
    @login_req
    def api_logger_move_status():
        with _move_status_lock:
            return jsonify(dict(_move_status))

    @app.get("/api/logger/status")
    @login_req
    def api_logger_status():
        with _rec_lock:
            active = {sl: {
                "stream":     t.stream_name,
                "running":    t.is_alive(),
                "last_error": t.last_error,
                "last_ok_ts": t.last_ok_ts,
                "seg_count":  t.seg_count,
            } for sl, t in _recorders.items()}
        # Sum disk usage across all configured root directories
        seen_files = set()
        total = 0
        try:
            for root in _all_rec_roots():
                try:
                    if not root.exists():
                        continue
                    for pat in _AUDIO_GLOBS:
                      for f in root.rglob(pat):
                        try:
                            if f.is_file():
                                key = str(f.resolve())
                                if key not in seen_files:
                                    seen_files.add(key)
                                    total += f.stat().st_size
                        except OSError:
                            pass
                except OSError as e:
                    _log(f"[Logger] Disk scan error for {root}: {e}")
        except Exception as e:
            _log(f"[Logger] Status disk scan failed: {e}")
        rec_root = _rec_root()
        return jsonify({"recorders": active, "disk_bytes": total,
                        "rec_root": str(rec_root)})

    @app.get("/api/logger/days/<stream_slug>")
    @login_req
    def api_logger_days(stream_slug):
        sdir = _stream_rec_root_by_slug(_safe(stream_slug)) / _safe(stream_slug)
        if not sdir.exists():
            return jsonify([])
        days = sorted(
            [d.name for d in sdir.iterdir()
             if d.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", d.name)],
            reverse=True)
        return jsonify(days)

    @app.get("/api/logger/segments/<stream_slug>/<date>")
    @login_req
    def api_logger_segments(stream_slug, date):
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            return jsonify([]), 400
        return jsonify(_get_segments(_safe(stream_slug), date))

    @app.get("/api/logger/audio/<stream_slug>/<date>/<filename>")
    @login_req
    def api_logger_audio(stream_slug, date, filename):
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            abort(400)
        if not re.match(r"^[\w\-]+\.(mp3|aac|opus)$", filename):
            abort(400)
        path = _stream_rec_root_by_slug(_safe(stream_slug)) / _safe(stream_slug) / date / filename
        # Confirm resolved path stays within the recordings root
        try:
            _assert_within_rec_root(path)
        except ValueError:
            abort(400)
        if not path.exists():
            abort(404)
        return _serve_ranged(path, "audio/mpeg")

    # ── Hub aggregation routes (registered on hub nodes only) ─────────────────
    if hub_server is not None:

        @app.post("/api/logger/hub/register")
        def api_logger_hub_register():
            data  = request.get_data()
            secret = (_monitor.app_cfg.hub.secret_key or "").strip() if _monitor else ""
            if not _check_sig(secret, data):
                _log("[Logger] Hub register: HMAC check failed (forbidden)")
                return jsonify({"error": "forbidden"}), 403
            body  = json.loads(data) if data else {}
            site  = str(body.get("site", "")).strip()
            streams = body.get("streams", [])
            catalog = body.get("catalog", {})
            if not site:
                return jsonify({"error": "no site"}), 400
            with _hub_logger_lock:
                _hub_logger_streams[site] = streams
                if isinstance(catalog, dict) and catalog:
                    _hub_logger_catalog[site] = catalog
            _log(f"[Logger] Hub: site '{site}' registered {len(streams)} stream(s), {len(catalog)} catalog entries")
            return jsonify({"ok": True})

        @app.get("/api/logger/hub/poll/<path:site>")
        def api_logger_hub_poll(site):
            secret = (_monitor.app_cfg.hub.secret_key or "").strip() if _monitor else ""
            if not _check_sig(secret, b""):
                _log(f"[Logger] Hub poll: HMAC check failed for site '{site}'")
                return jsonify({"error": "forbidden"}), 403
            # ── Long-poll: wait up to 25 s for a command ──────────────────────
            # Register an Event for this site so _hub_set_pending() can wake us
            # immediately instead of making the client wait for the next 3-second
            # poll cycle.  Multiple simultaneous connections from the same site
            # are unlikely but safe — they share the event and both return the
            # same (now-empty) pending slot.
            evt = _hub_logger_events.setdefault(site, threading.Event())
            evt.clear()
            # Check for an already-queued command before blocking
            with _hub_logger_lock:
                cmd = _hub_logger_pending.pop(site, None)
            if cmd:
                _log(f"[Logger] Hub poll: dispatching cmd type='{cmd.get('type')}' to site '{site}'")
                return jsonify({"cmd": cmd})
            # Block until woken by _hub_set_pending or 25 s timeout
            evt.wait(timeout=25)
            with _hub_logger_lock:
                cmd = _hub_logger_pending.pop(site, None)
            if cmd:
                _log(f"[Logger] Hub poll: dispatching cmd type='{cmd.get('type')}' to site '{site}'")
                return jsonify({"cmd": cmd})
            return jsonify({})

        @app.post("/api/logger/hub/result")
        def api_logger_hub_result():
            data   = request.get_data()
            secret = (_monitor.app_cfg.hub.secret_key or "").strip() if _monitor else ""
            if not _check_sig(secret, data):
                _log("[Logger] Hub result: HMAC check failed (forbidden)")
                return jsonify({"error": "forbidden"}), 403
            body   = json.loads(data) if data else {}
            site   = str(body.get("site", "")).strip()
            rtype  = str(body.get("type", "")).strip()
            slug   = str(body.get("slug", "")).strip()
            date   = str(body.get("date", "")).strip()
            with _hub_logger_lock:
                if rtype == "days":
                    days = body.get("days", [])
                    _hub_logger_days[f"{site}:{slug}"] = days
                    _log(f"[Logger] Hub result: stored {len(days)} day(s) for {site}:{slug}")
                elif rtype == "segments":
                    segs = body.get("segments", [])
                    _hub_logger_segs[f"{site}:{slug}:{date}"] = segs
                    _log(f"[Logger] Hub result: stored {len(segs)} segment(s) for {site}:{slug}:{date}")
                elif rtype == "metadata":
                    events = body.get("events", [])
                    _hub_logger_meta[f"{site}:{slug}:{date}"] = events
                    _log(f"[Logger] Hub result: stored {len(events)} meta event(s) for {site}:{slug}:{date}")
            return jsonify({"ok": True})

        @app.get("/api/logger/hub/sites")
        @login_req
        def api_logger_hub_sites():
            with _hub_logger_lock:
                sites = list(_hub_logger_streams.keys())
            return jsonify(sites)

        @app.get("/api/logger/hub/catalog")
        @login_req
        def api_logger_hub_catalog():
            """Merged catalog from all registered sites — streams with recordings."""
            result = []
            seen = set()
            with _hub_logger_lock:
                for site, cat in _hub_logger_catalog.items():
                    for slug, info in cat.items():
                        if slug not in seen:
                            result.append({
                                "slug": slug,
                                "name": info.get("name", slug),
                                "site": site,
                                "owner": info.get("owner", site),
                                "rec_format": info.get("rec_format", "mp3"),
                            })
                            seen.add(slug)
            result.sort(key=lambda x: x["name"].lower())
            return jsonify(result)

        @app.get("/api/logger/hub/streams/<path:site>")
        @login_req
        def api_logger_hub_streams(site):
            with _hub_logger_lock:
                streams = _hub_logger_streams.get(site, [])
            return jsonify(streams)

        @app.get("/api/logger/hub/days/<path:site_slug>")
        @login_req
        def api_logger_hub_days(site_slug):
            # site_slug is "site/slug" — split on first "/"
            parts = site_slug.split("/", 1)
            if len(parts) != 2:
                return jsonify({"error": "bad path"}), 400
            site, slug = parts
            key = f"{site}:{slug}"
            with _hub_logger_lock:
                if key in _hub_logger_days:
                    days = _hub_logger_days[key]
                    _log(f"[Logger] Hub days cache hit: {site}:{slug} → {len(days)} day(s)")
                    return jsonify(days)
            _log(f"[Logger] Hub days: queuing cmd for site='{site}' slug='{slug}'")
            _hub_set_pending(site, {"type": "days", "slug": slug})
            return jsonify({"pending": True})

        @app.get("/api/logger/hub/segments/<path:site_slug_date>")
        @login_req
        def api_logger_hub_segments(site_slug_date):
            # path is "site/slug/date" — rsplit to get date, then site/slug
            parts = site_slug_date.rsplit("/", 2)
            if len(parts) != 3:
                return jsonify({"error": "bad path"}), 400
            site, slug, date = parts
            key = f"{site}:{slug}:{date}"
            with _hub_logger_lock:
                if key in _hub_logger_segs:
                    segs = _hub_logger_segs[key]
                    _log(f"[Logger] Hub segs cache hit: {key} → {len(segs)} seg(s)")
                    return jsonify(segs)
            _log(f"[Logger] Hub segs: queuing cmd for site='{site}' slug='{slug}' date='{date}'")
            _hub_set_pending(site, {"type": "segments", "slug": slug, "date": date})
            return jsonify({"pending": True})

        @app.post("/api/logger/hub/play")
        @login_req
        @csrf_dec
        def api_logger_hub_play():
            body     = request.get_json(force=True) or {}
            site     = str(body.get("site", "")).strip()
            slug     = str(body.get("slug", "")).strip()
            date     = str(body.get("date", "")).strip()
            filename = str(body.get("filename", "")).strip()
            seek_s   = float(body.get("seek_s", 0))
            if not all([site, slug, date, filename]):
                return jsonify({"error": "missing fields"}), 400
            # Cancel existing slot for this site if any
            with _hub_logger_lock:
                old_id = _hub_logger_active.get(site)
            if old_id and _listen_registry:
                try:
                    old_slot = _listen_registry.get(old_id)
                    if old_slot:
                        old_slot.closed = True
                except Exception:
                    pass
            # Create new slot
            if not _listen_registry:
                return jsonify({"error": "no listen_registry"}), 500
            slot = _listen_registry.create(
                site, 0, kind="scanner", mimetype="application/octet-stream")
            with _hub_logger_lock:
                _hub_logger_active[site] = slot.slot_id
            _hub_set_pending(site, {
                "type": "play",
                "slot_id": slot.slot_id,
                "slug": slug,
                "date": date,
                "filename": filename,
                "seek_s": seek_s,
            })
            return jsonify({
                "ok": True,
                "slot_id": slot.slot_id,
                "stream_url": f"/hub/scanner/stream/{slot.slot_id}",
            })

        @app.get("/api/logger/hub/metadata/<path:site_slug_date>")
        @login_req
        def api_logger_hub_metadata(site_slug_date):
            parts = site_slug_date.rsplit("/", 2)
            if len(parts) != 3:
                return jsonify({"error": "bad path"}), 400
            site, slug, date = parts
            key = f"{site}:{slug}:{date}"
            with _hub_logger_lock:
                if key in _hub_logger_meta:
                    events = _hub_logger_meta[key]
                    _log(f"[Logger] Hub meta cache hit: {key} → {len(events)} event(s)")
                    return jsonify(events)
            _log(f"[Logger] Hub meta: queuing cmd for site='{site}' slug='{slug}' date='{date}'")
            _hub_set_pending(site, {"type": "metadata", "slug": slug, "date": date})
            return jsonify({"pending": True})

    # ── Regular logger routes ─────────────────────────────────────────────────

    @app.get("/api/logger/metadata/<stream_slug>/<date>")
    @login_req
    def api_logger_metadata(stream_slug, date):
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            return jsonify([]), 400
        slug = _safe(stream_slug)
        try:
            midnight = datetime.datetime.strptime(date, "%Y-%m-%d").replace(
                tzinfo=datetime.timezone.utc).timestamp()
        except ValueError:
            return jsonify([]), 400
        return jsonify(_meta_query(slug, midnight))

    @app.post("/api/logger/mic")
    def api_logger_mic():
        """REST endpoint — record a mic-on / mic-off event on the timeline.

        Auth: logged-in browser session OR ``Authorization: Bearer <mic_api_key>``.

        JSON body::

            {
              "stream": "bbc_radio_1",   // stream slug (required)
              "state":  "on",            // "on" or "off" (required)
              "label":  "Studio A",      // mic name shown in tooltip (optional)
              "site":   "London",        // hub-mode site name (optional)
              "ts":     1234567890.0     // Unix timestamp; omit to use server time
            }
        """
        # ── Auth ──────────────────────────────────────────────────────────
        authenticated = bool(session.get("user"))
        if not authenticated:
            api_key = _cfg.get("mic_api_key", "").strip()
            if api_key:
                auth_hdr = request.headers.get("Authorization", "")
                if auth_hdr.startswith("Bearer ") and auth_hdr[7:] == api_key:
                    authenticated = True
        if not authenticated:
            return jsonify({"ok": False, "error": "Unauthorized — set a Mic API Key in Logger Settings or log in"}), 401

        data = request.get_json(force=True) or {}

        # ── Validate ──────────────────────────────────────────────────────
        state = str(data.get("state", "")).lower().strip()
        if state not in ("on", "off"):
            return jsonify({"ok": False, "error": "state must be 'on' or 'off'"}), 400
        stream = str(data.get("stream", "")).strip()
        if not stream:
            return jsonify({"ok": False, "error": "stream is required"}), 400
        slug  = _safe(stream)
        label = str(data.get("label", "")).strip()
        ts    = float(data.get("ts", 0) or 0) or time.time()

        # ── Write to local metadata_log ───────────────────────────────────
        entry_type = f"mic_{state}"
        _meta_write(slug, ts, entry_type, {"label": label, "title": label, "state": state})

        # ── Inject into hub meta cache (so the hub UI updates immediately) ─
        try:
            dt = datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
            y, mo, d = int(dt[:4]), int(dt[5:7]), int(dt[8:10])
            midnight = datetime.datetime(y, mo, d, tzinfo=datetime.timezone.utc).timestamp()
            event_entry = {
                "ts_s":      round(ts - midnight, 3),
                "type":      entry_type,
                "title":     label,
                "artist":    "",
                "show_name": "",
                "presenter": "",
            }
            with _hub_logger_lock:
                for key, evts in _hub_logger_meta.items():
                    _, k_slug, k_date = key.split(":", 2)
                    if k_slug == slug and k_date == dt:
                        evts.append(event_entry)
                        evts.sort(key=lambda e: e["ts_s"])
        except Exception:
            pass  # hub cache injection is best-effort

        _log(f"[Logger] Mic {state}: stream={slug} label={label!r} ts={ts:.1f}")
        return jsonify({"ok": True, "ts": ts, "stream": slug, "state": state})

    # ── Mobile API (iOS app) ─────────────────────────────────────────────────

    @app.get("/api/mobile/logger/status")
    @mobile_api_req
    def api_mobile_logger_status():
        """Presence check — 200 if logger plugin is installed, 404 otherwise."""
        with _rec_lock:
            active = {sl: {"stream": t.stream_name, "running": t.is_alive()}
                      for sl, t in _recorders.items()}
        return jsonify({"installed": True, "recorders": active})

    @app.get("/api/mobile/logger/sites")
    @mobile_api_req
    def api_mobile_logger_sites():
        if hub_server is None:
            return jsonify({"sites": []})
        with _hub_logger_lock:
            sites = list(_hub_logger_streams.keys())
        return jsonify({"sites": sites})

    @app.get("/api/mobile/logger/streams")
    @mobile_api_req
    def api_mobile_logger_streams():
        site = request.args.get("site", "").strip()
        if hub_server is not None and site:
            with _hub_logger_lock:
                streams = list(_hub_logger_streams.get(site, []))
            return jsonify({"streams": streams})
        streams = _available_streams()
        return jsonify({"streams": streams})

    @app.get("/api/mobile/logger/catalog")
    @mobile_api_req
    def api_mobile_logger_catalog():
        """Merged catalog of all streams with recordings across all sites."""
        if hub_server is None:
            # Local mode: return catalog from local recording roots
            catalog = {}
            for root in _all_rec_roots():
                catalog.update(_catalog_read(root))
            result = [{"slug": slug, "name": info.get("name", slug),
                       "site": _my_owner(), "owner": info.get("owner", ""),
                       "rec_format": info.get("rec_format", "mp3")}
                      for slug, info in catalog.items()]
            result.sort(key=lambda x: x["name"].lower())
            return jsonify({"catalog": result})
        # Hub mode: merge catalog from all registered sites
        result = []
        seen = set()
        with _hub_logger_lock:
            for site, cat in _hub_logger_catalog.items():
                for slug, info in cat.items():
                    if slug not in seen:
                        result.append({"slug": slug, "name": info.get("name", slug),
                                       "site": site, "owner": info.get("owner", site),
                                       "rec_format": info.get("rec_format", "mp3")})
                        seen.add(slug)
        result.sort(key=lambda x: x["name"].lower())
        return jsonify({"catalog": result})

    @app.get("/api/mobile/logger/days")
    @mobile_api_req
    def api_mobile_logger_days():
        site = request.args.get("site", "").strip()
        slug = request.args.get("slug", "").strip()
        if not slug:
            return jsonify({"error": "slug required"}), 400
        if hub_server is not None and site:
            key = f"{site}:{_safe(slug)}"
            with _hub_logger_lock:
                if key in _hub_logger_days:
                    return jsonify({"days": _hub_logger_days[key], "pending": False})
            _hub_set_pending(site, {"type": "days", "slug": _safe(slug)})
            return jsonify({"days": [], "pending": True})
        # Local mode
        sdir = _stream_rec_root_by_slug(_safe(slug)) / _safe(slug)
        if not sdir.exists():
            return jsonify({"days": [], "pending": False})
        days = sorted(
            [d.name for d in sdir.iterdir()
             if d.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", d.name)],
            reverse=True)
        return jsonify({"days": days, "pending": False})

    @app.get("/api/mobile/logger/segments")
    @mobile_api_req
    def api_mobile_logger_segments():
        site = request.args.get("site", "").strip()
        slug = request.args.get("slug", "").strip()
        date = request.args.get("date", "").strip()
        if not (slug and date):
            return jsonify({"error": "slug and date required"}), 400
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            return jsonify({"error": "invalid date"}), 400
        if hub_server is not None and site:
            key = f"{site}:{_safe(slug)}:{date}"
            with _hub_logger_lock:
                if key in _hub_logger_segs:
                    segs = sorted(_hub_logger_segs[key], key=lambda s: s.get("start_s", 0))
                    return jsonify({"segments": segs, "pending": False})
            _hub_set_pending(site, {"type": "segments", "slug": _safe(slug), "date": date})
            return jsonify({"segments": [], "pending": True})
        # Local mode
        segs = _get_segments(_safe(slug), date)
        return jsonify({"segments": segs, "pending": False})

    @app.get("/api/mobile/logger/metadata")
    @mobile_api_req
    def api_mobile_logger_metadata():
        site = request.args.get("site", "").strip()
        slug = request.args.get("slug", "").strip()
        date = request.args.get("date", "").strip()
        if not (slug and date):
            return jsonify({"error": "slug and date required"}), 400
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            return jsonify({"error": "invalid date"}), 400
        if hub_server is not None and site:
            key = f"{site}:{_safe(slug)}:{date}"
            with _hub_logger_lock:
                if key in _hub_logger_meta:
                    return jsonify({"events": _hub_logger_meta[key], "pending": False})
            _hub_set_pending(site, {"type": "metadata", "slug": _safe(slug), "date": date})
            return jsonify({"events": [], "pending": True})
        # Local mode — read from shared DB (covers catalog/foreign streams too)
        try:
            midnight = datetime.datetime.strptime(date, "%Y-%m-%d").replace(
                tzinfo=datetime.timezone.utc).timestamp()
        except ValueError:
            return jsonify({"events": [], "pending": False})
        events = _meta_query(_safe(slug), midnight)
        return jsonify({"events": events, "pending": False})

    @app.post("/api/mobile/logger/play")
    @mobile_api_req
    def api_mobile_logger_play():
        body     = request.get_json(force=True) or {}
        site     = str(body.get("site", "")).strip()
        slug     = str(body.get("slug", "")).strip()
        date     = str(body.get("date", "")).strip()
        filename = str(body.get("filename", "")).strip()
        seek_s   = float(body.get("seek_s", 0))
        if not (slug and date and filename):
            return jsonify({"error": "missing fields"}), 400
        # Local / direct-connect mode: return a direct PCM stream URL
        if hub_server is None or not site:
            qs = _urllib_parse.urlencode({
                "slug": slug, "date": date, "filename": filename, "seek_s": seek_s
            })
            return jsonify({
                "ok": True,
                "slot_id": None,
                "stream_url": f"/api/mobile/logger/stream_pcm?{qs}",
            })
        if _listen_registry is None:
            return jsonify({"error": "relay not available"}), 503
        # Cancel existing slot for this site
        with _hub_logger_lock:
            old_id = _hub_logger_active.get(site)
        if old_id:
            try:
                old_slot = _listen_registry.get(old_id)
                if old_slot:
                    old_slot.closed = True
            except Exception:
                pass
        slot = _listen_registry.create(site, 0, kind="scanner",
                                       mimetype="application/octet-stream")
        with _hub_logger_lock:
            _hub_logger_active[site] = slot.slot_id
        _hub_set_pending(site, {
            "type": "play",
            "slot_id": slot.slot_id,
            "slug": _safe(slug),
            "date": date,
            "filename": filename,
            "seek_s": seek_s,
        })
        return jsonify({
            "ok": True,
            "slot_id": slot.slot_id,
            "stream_url": f"/api/mobile/logger/relay_stream/{slot.slot_id}",
        })

    @app.get("/api/mobile/logger/relay_stream/<slot_id>")
    @mobile_api_req
    def api_mobile_logger_relay_stream(slot_id):
        """Mobile-auth wrapper that streams PCM from a relay slot.
        Uses Bearer/token auth instead of the session-only /hub/scanner/stream/ route."""
        if not _listen_registry:
            return ("", 503)
        slot = _listen_registry.get(slot_id)
        if not slot:
            return ("", 404)

        _SILENCE = b"\x00" * 9600   # 0.1 s of silence (16-bit mono 48 kHz)
        _THRESHOLD = 1.0             # inject silence after 1 s gap

        def _gen():
            last_data = time.monotonic()
            while not getattr(slot, "closed", False):
                try:
                    chunk = slot.get(timeout=0.30)
                    if chunk is not None:
                        last_data = time.monotonic()
                        yield chunk
                    else:
                        if time.monotonic() - last_data > _THRESHOLD:
                            yield _SILENCE
                            last_data = time.monotonic()
                except Exception:
                    if time.monotonic() - last_data > _THRESHOLD:
                        yield _SILENCE
                        last_data = time.monotonic()

        return Response(_gen(), mimetype="application/octet-stream",
                        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})

    @app.get("/api/mobile/logger/stream_pcm")
    @mobile_api_req
    def api_mobile_logger_stream_pcm():
        """Direct-mode: transcode a recorded segment to raw PCM and stream it.
        Used when the iOS app is connected directly to this node (not via a hub relay).
        Format: 16-bit signed LE, mono, 48 kHz — same as the hub relay stream."""
        slug     = request.args.get("slug", "").strip()
        date     = request.args.get("date", "").strip()
        filename = request.args.get("filename", "").strip()
        seek_s   = float(request.args.get("seek_s", 0) or 0)
        if not (slug and date and filename):
            return jsonify({"error": "missing params"}), 400
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            return jsonify({"error": "invalid date"}), 400
        # _safe() replaces dots with underscores — do NOT apply to filenames
        if not re.match(r"^[\w\-]+\.(mp3|aac|opus|wav)$", filename):
            return jsonify({"error": "invalid filename"}), 400
        path = _stream_rec_root_by_slug(_safe(slug)) / _safe(slug) / date / filename
        if not path.exists():
            return ("", 404)
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            return jsonify({"error": "ffmpeg not installed"}), 500
        cmd = [ffmpeg_bin, "-hide_banner", "-loglevel", "error",
               "-ss", str(max(0.0, seek_s)), "-i", str(path),
               "-ac", "1", "-ar", "48000", "-f", "s16le", "pipe:1"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        def _gen():
            _CHUNK = 9600  # 0.1 s of 16-bit mono 48 kHz PCM
            try:
                while True:
                    chunk = proc.stdout.read(_CHUNK)
                    if not chunk:
                        break
                    yield chunk
            finally:
                try:
                    proc.kill()
                except Exception:
                    pass

        return Response(_gen(), mimetype="application/octet-stream",
                        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})

    @app.post("/api/mobile/logger/stop")
    @mobile_api_req
    def api_mobile_logger_stop():
        body = request.get_json(force=True) or {}
        site = str(body.get("site", "")).strip()
        with _hub_logger_lock:
            old_id = _hub_logger_active.pop(site, None)
        if old_id and _listen_registry:
            try:
                old_slot = _listen_registry.get(old_id)
                if old_slot:
                    old_slot.closed = True
            except Exception:
                pass
        return jsonify({"ok": True})

    @app.post("/api/logger/export")
    @login_req
    @csrf_dec
    def api_logger_export():
        data = request.get_json(force=True) or {}
        stream_slug = data.get("stream", "")
        date        = data.get("date", "")
        start_s     = float(data.get("start_s", 0))
        end_s       = float(data.get("end_s", 60))
        fmt         = data.get("fmt", "mp3")
        if fmt not in ("mp3", "aac", "opus"):
            fmt = "mp3"
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            return jsonify({"error": "bad date"}), 400
        if end_s <= start_s or end_s - start_s > 7200:
            return jsonify({"error": "invalid range (max 2h)"}), 400
        return _export_clip(_safe(stream_slug), date, start_s, end_s, fmt=fmt)


# ─────────────────────────────────────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────────────────────────────────────

def _load_config():
    global _cfg
    p = _app_dir / _CONFIG_FILE
    try:
        if p.exists():
            with open(p) as f:
                _cfg = json.load(f)
    except Exception:
        _cfg = {}
    _cfg.setdefault("streams", {})
    _cfg.setdefault("rec_dir", "")
    _cfg.setdefault("base_dirs", [])   # list of {"name": str, "path": str}
    _cfg.setdefault("mic_api_key", "")  # Bearer token for /api/logger/mic REST API

def _save_config():
    try:
        with open(_app_dir / _CONFIG_FILE, "w") as f:
            json.dump(_cfg, f, indent=2)
    except Exception as e:
        _log(f"[Logger] Config save failed: {e}")

_ALLOWED_BITRATES = {"32k", "48k", "64k", "96k", "128k", "192k", "256k", "320k"}

def _resolve_path(path_str: str) -> Path:
    """Resolve an absolute or app-relative path string to a Path."""
    p = Path(path_str.strip())
    return p if p.is_absolute() else (_app_dir / p).resolve()


def _stream_rec_root(stream_name: str) -> Path:
    """Return the recordings root for a specific stream.

    Looks up the stream's ``base_dir`` name in the ``base_dirs`` list.
    Falls back to the global ``_rec_root()`` if unset or not found.
    """
    with _cfg_lock:
        scfg   = _cfg.get("streams", {}).get(stream_name, {})
        bd_name = scfg.get("base_dir", "").strip()
        if bd_name:
            for bd in _cfg.get("base_dirs", []):
                if bd.get("name") == bd_name:
                    path_str = bd.get("path", "").strip()
                    if path_str:
                        return _resolve_path(path_str)
    return _rec_root()


def _stream_rec_root_by_slug(slug: str) -> Path:
    """Return the recordings root for a stream identified by its slug."""
    name = _slug_to_name(slug)
    if name:
        return _stream_rec_root(name)
    # Foreign stream (catalog) — scan all roots for matching slug directory
    for root in _all_rec_roots():
        if (root / slug).is_dir():
            return root
    return _rec_root()


def _all_rec_roots() -> set:
    """Return the set of all unique recording root paths currently configured."""
    roots = {_rec_root()}
    with _cfg_lock:
        for bd in _cfg.get("base_dirs", []):
            path_str = bd.get("path", "").strip()
            if path_str:
                try:
                    roots.add(_resolve_path(path_str))
                except Exception:
                    pass
    return roots


# ─── Shared catalog ──────────────────────────────────────────────────────────
_CATALOG_FILE    = "catalog.json"
_CATALOG_STALE_S = 7200   # 2 hours — entries older than this are stale


def _my_owner() -> str:
    """Return a unique owner identifier for this SignalScope instance."""
    try:
        return (_monitor.app_cfg.hub.site_name or "").strip() or "local"
    except Exception:
        return "local"


def _catalog_path(root: Path) -> Path:
    return root / _CATALOG_FILE


def _catalog_read(root: Path) -> dict:
    """Read catalog.json from *root*, dropping stale entries (>2 h old)."""
    p = _catalog_path(root)
    if not p.exists():
        return {}
    try:
        with open(p, "r") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_SH)
            except OSError:
                pass  # SMB may not support flock — proceed without lock
            data = json.load(f)
    except Exception:
        return {}
    now = time.time()
    return {slug: info for slug, info in data.items()
            if now - info.get("updated", 0) < _CATALOG_STALE_S}


def _catalog_write(root: Path, slug: str, name: str, rec_format: str):
    """Add or update our entry in the shared catalog (atomic with flock)."""
    p = _catalog_path(root)
    try:
        root.mkdir(parents=True, exist_ok=True)
        lock_path = root / ".catalog.lock"
        with open(lock_path, "w") as lf:
            try:
                fcntl.flock(lf, fcntl.LOCK_EX)
            except OSError:
                pass
            # Read existing
            data = {}
            if p.exists():
                try:
                    with open(p, "r") as f:
                        data = json.load(f)
                except Exception:
                    data = {}
            # Merge our entry
            data[slug] = {
                "name":       name,
                "owner":      _my_owner(),
                "rec_format": rec_format,
                "updated":    time.time(),
            }
            # Write atomically via tmp + rename
            tmp = p.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(str(tmp), str(p))
    except Exception as e:
        _log(f"[Logger] Catalog write failed: {e}")


def _catalog_remove(root: Path, slug: str):
    """Remove our entry from the catalog (only if we own it)."""
    p = _catalog_path(root)
    if not p.exists():
        return
    try:
        lock_path = root / ".catalog.lock"
        with open(lock_path, "w") as lf:
            try:
                fcntl.flock(lf, fcntl.LOCK_EX)
            except OSError:
                pass
            with open(p, "r") as f:
                data = json.load(f)
            owner = _my_owner()
            if slug in data and data[slug].get("owner") == owner:
                del data[slug]
                tmp = p.with_suffix(".tmp")
                with open(tmp, "w") as f:
                    json.dump(data, f, indent=2)
                os.replace(str(tmp), str(p))
    except Exception as e:
        _log(f"[Logger] Catalog remove failed: {e}")


def _move_stream_recordings(stream_name: str, old_root: Path, new_root: Path):
    """Move a stream's slug directory from old_root to new_root."""
    slug    = _slug(stream_name)
    old_dir = old_root / _safe(slug)
    new_dir = new_root / _safe(slug)
    if not old_dir.exists() or old_dir == new_dir:
        return
    try:
        new_dir.parent.mkdir(parents=True, exist_ok=True)
        if new_dir.exists():
            # Merge: move individual date sub-dirs so we don't clobber existing data
            for date_dir in old_dir.iterdir():
                dst = new_dir / date_dir.name
                if dst.exists():
                    _log(f"[Logger] Move: skipping {date_dir.name} for '{stream_name}' — already exists at destination")
                else:
                    shutil.move(str(date_dir), str(dst))
            old_dir.rmdir()          # remove now-empty source dir
        else:
            shutil.move(str(old_dir), str(new_dir))
        _log(f"[Logger] Moved recordings for '{stream_name}': {old_dir} → {new_dir}")
    except Exception as e:
        _log(f"[Logger] Failed to move recordings for '{stream_name}': {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  METADATA POLLER
# ─────────────────────────────────────────────────────────────────────────────

def _parse_nowplaying(raw: dict):
    """Parse a now-playing JSON response into a normalised dict.

    Supports Planet Radio/Bauer, Triton Digital, and generic flat JSON.
    Returns ``{"title","artist","show_name","presenter"}`` or None.
    """
    if not isinstance(raw, dict):
        return None
    title = artist = show_name = presenter = ""

    # ── Planet Radio / Bauer ──────────────────────────────────────────────
    # {"data": {"now": {"title":…,"artist":…}, "schedule": {"current": {"title":…}}}}
    data = raw.get("data") or raw.get("result") or raw
    if isinstance(data, dict):
        now = data.get("now") or data.get("current_song") or {}
        if isinstance(now, dict):
            title  = str(now.get("title", "") or now.get("song", "") or "").strip()
            artist = str(now.get("artist", "") or now.get("artist_name", "") or "").strip()
        sched = data.get("schedule") or data.get("on_air") or {}
        if isinstance(sched, dict):
            cur = sched.get("current") or sched.get("programme") or sched
            if isinstance(cur, dict):
                show_name = str(cur.get("title", "") or cur.get("name", "") or cur.get("show", "") or "").strip()
                presenter = str(cur.get("presenter", "") or cur.get("host", "") or "").strip()

    # ── Triton Digital ────────────────────────────────────────────────────
    # {"now_playing": {"song": {"title":…,"artist":…}}}
    if not title and isinstance(raw.get("now_playing"), dict):
        song = raw["now_playing"].get("song") or raw["now_playing"]
        if isinstance(song, dict):
            title  = str(song.get("title", "") or "").strip()
            artist = str(song.get("artist", "") or "").strip()

    # ── Generic flat ──────────────────────────────────────────────────────
    if not title:
        title  = str(raw.get("title", "") or raw.get("track", "") or raw.get("song", "") or "").strip()
        artist = str(raw.get("artist", "") or raw.get("artist_name", "") or "").strip()
    if not show_name:
        show_name = str(raw.get("show", "") or raw.get("show_name", "") or raw.get("programme", "") or "").strip()
        presenter = presenter or str(raw.get("presenter", "") or raw.get("host", "") or "").strip()

    if not title and not show_name:
        return None
    return {"title": title, "artist": artist, "show_name": show_name, "presenter": presenter}


def _get_live_dls_rds(stream_name: str):
    """Read live DLS/RDS text from SignalScope's monitor for a stream.

    Returns ``{"title","artist","show_name","presenter"}`` or None.
    """
    if not _monitor:
        return None
    try:
        for inp in _monitor.app_cfg.inputs:
            if inp.name != stream_name:
                continue
            # DAB DLS text
            dls = getattr(inp, "_dls_text", None) or getattr(inp, "dls_text", None)
            if dls and str(dls).strip():
                return {"title": str(dls).strip(), "artist": "", "show_name": "", "presenter": ""}
            # FM RDS radiotext / programme service name
            rt = getattr(inp, "_rds_rt", None) or getattr(inp, "rds_rt", None)
            ps = getattr(inp, "_rds_ps", None) or getattr(inp, "rds_ps", None)
            if rt or ps:
                text = str(rt or ps or "").strip()
                return {"title": text, "artist": "", "show_name": "", "presenter": ""}
    except Exception:
        pass
    return None


def _meta_write(slug: str, ts: float, entry_type: str, info: dict):
    """Write a metadata event to both the local and shared metadata databases."""
    row = (slug, ts, entry_type,
           info.get("title", ""), info.get("artist", ""),
           info.get("show_name", ""), info.get("presenter", ""),
           json.dumps(info))
    _SQL = """INSERT OR REPLACE INTO metadata_log
              (stream, ts, type, title, artist, show_name, presenter, raw)
              VALUES (?,?,?,?,?,?,?,?)"""
    # Local SQLite (existing behaviour)
    try:
        db = _get_db()
        db.execute(_SQL, row)
        db.commit()
        db.close()
    except Exception as e:
        _log(f"[Logger] Meta DB write error: {e}")
    # Shared DB alongside recordings (visible to other logger instances on same filesystem)
    try:
        sroot = _stream_rec_root_by_slug(slug)
        with _smd_lock(sroot):
            sdb = _open_shared_meta_db(sroot)
            sdb.execute(_SQL, row)
            sdb.commit()
            sdb.close()
    except Exception as e:
        _log(f"[Logger] Meta shared DB write error: {e}")


class _MetaPoller(threading.Thread):
    """Polls a now-playing URL (or DLS/RDS) every 30 s and logs changes."""

    _POLL_INTERVAL = 30

    def __init__(self, stream_name: str, slug: str):
        super().__init__(daemon=True, name=f"MetaPoll-{slug}")
        self.stream_name = stream_name
        self.slug        = slug
        self.stop_evt    = threading.Event()
        self._last_track: str = ""   # "title||artist" dedup key
        self._last_show:  str = ""   # "show_name||presenter" dedup key

    def run(self):
        while not self.stop_evt.is_set():
            try:
                self._poll_once()
            except Exception as e:
                _log(f"[Logger] MetaPoller ({self.slug}): {e}")
            self.stop_evt.wait(self._POLL_INTERVAL)

    def _poll_once(self):
        with _cfg_lock:
            scfg = _cfg.get("streams", {}).get(self.stream_name, {})
        url = str(scfg.get("nowplaying_url", "") or "").strip()

        info = None
        if url:
            try:
                req  = _urllib_req.Request(url, headers={"User-Agent": "SignalScope-Logger/1.4"})
                resp = _urllib_req.urlopen(req, timeout=10)
                raw  = json.loads(resp.read().decode("utf-8", errors="replace"))
                resp.close()
                info = _parse_nowplaying(raw)
            except Exception as e:
                _log(f"[Logger] MetaPoller ({self.slug}): API error: {e}")

        # DLS/RDS fallback when no URL configured or API failed
        if not info:
            info = _get_live_dls_rds(self.stream_name)

        if not info:
            return

        now = time.time()

        # Write track event if title/artist changed
        track_key = f"{info.get('title', '')}||{info.get('artist', '')}"
        if info.get("title") and track_key != self._last_track:
            self._last_track = track_key
            _meta_write(self.slug, now, "track", info)

        # Write show event if show name changed
        show_key = f"{info.get('show_name', '')}||{info.get('presenter', '')}"
        if info.get("show_name") and show_key != self._last_show:
            self._last_show = show_key
            _meta_write(self.slug, now, "show", info)

    def stop(self):
        self.stop_evt.set()


def _reconcile_meta_pollers():
    """Start/stop MetaPoller threads to match current stream config."""
    streams = _available_streams()
    with _meta_lock:
        active_slugs = {s["slug"] for s in streams}
        for s in streams:
            slug = s["slug"]
            scfg = _stream_cfg(s["name"])
            should_poll = scfg.get("enabled") or bool(scfg.get("nowplaying_url", "").strip())
            if should_poll:
                if slug not in _meta_pollers or not _meta_pollers[slug].is_alive():
                    t = _MetaPoller(s["name"], slug)
                    _meta_pollers[slug] = t
                    t.start()
            elif slug in _meta_pollers:
                _meta_pollers[slug].stop()
                del _meta_pollers[slug]
        # Stop pollers for streams no longer present
        for slug in list(_meta_pollers.keys()):
            if slug not in active_slugs:
                _meta_pollers[slug].stop()
                del _meta_pollers[slug]


def _stream_cfg(stream_name):
    with _cfg_lock:
        s = _cfg.get("streams", {}).get(stream_name, {})
    hq  = s.get("hq_bitrate", _DEFAULT_HQ)
    lq  = s.get("lq_bitrate", _DEFAULT_LQ)
    fmt = s.get("rec_format", "mp3")
    if fmt not in _REC_FORMATS:
        fmt = "mp3"
    return {
        "enabled":        bool(s.get("enabled", False)),
        "hq_bitrate":     hq if hq in _ALLOWED_BITRATES else _DEFAULT_HQ,
        "lq_bitrate":     lq if lq in _ALLOWED_BITRATES else _DEFAULT_LQ,
        "lq_after_days":  int(s.get("lq_after_days", _DEFAULT_LQ_AFTER)),
        "retain_days":    int(s.get("retain_days", _DEFAULT_RETAIN)),
        "base_dir":       s.get("base_dir", ""),
        "nowplaying_url": str(s.get("nowplaying_url", "") or "").strip(),
        "rec_format":     fmt,
    }

def _available_streams():
    """Return all enabled SignalScope inputs.
    Recording taps SignalScope's internal _stream_buffer, so every input
    type (FM, DAB, HTTP, AoIP, sound device…) is capturable."""
    cfg = _monitor.app_cfg
    return [{"name": inp.name, "slug": _slug(inp.name), "url": inp.device_index}
            for inp in cfg.inputs if inp.enabled]

def _slug_to_name(slug):
    for s in _available_streams():
        if s["slug"] == slug:
            return s["name"]
    return None

def _log(msg):
    if _monitor:
        _monitor.log(msg)
    else:
        print(msg, flush=True)


# ─────────────────────────────────────────────────────────────────────────────
#  DATABASE
# ─────────────────────────────────────────────────────────────────────────────

def _init_db():
    db = _get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS segments (
            stream        TEXT NOT NULL,
            date          TEXT NOT NULL,
            filename      TEXT NOT NULL,
            start_s       REAL NOT NULL,
            has_silence   INTEGER DEFAULT 0,
            silence_pct   REAL    DEFAULT 0.0,
            silence_ranges TEXT   DEFAULT '[]',
            quality       TEXT    DEFAULT 'high',
            PRIMARY KEY (stream, date, filename)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS metadata_log (
            stream    TEXT NOT NULL,
            ts        REAL NOT NULL,
            type      TEXT NOT NULL,
            title     TEXT DEFAULT '',
            artist    TEXT DEFAULT '',
            show_name TEXT DEFAULT '',
            presenter TEXT DEFAULT '',
            raw       TEXT DEFAULT '',
            PRIMARY KEY (stream, ts, type)
        )
    """)
    db.commit()
    db.close()

def _get_db():
    conn = sqlite3.connect(str(_app_dir / _DB_FILE), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


# Per-root locks — serialise all same-process threads that touch the same
# metadata.db so SQLite never sees two concurrent writers from one process.
_smd_locks: dict = {}
_smd_locks_guard = threading.Lock()

def _smd_lock(root: Path) -> threading.Lock:
    key = str(root)
    with _smd_locks_guard:
        if key not in _smd_locks:
            _smd_locks[key] = threading.Lock()
        return _smd_locks[key]


def _open_shared_meta_db(root: Path):
    """Open (creating if needed) the shared metadata DB at {root}/metadata.db.

    Caller MUST hold _smd_lock(root) before calling this.
    WAL mode is set only on first creation to avoid the brief exclusive lock
    that PRAGMA journal_mode=WAL requires on every subsequent open.
    """
    path    = root / "metadata.db"
    is_new  = not path.exists()
    conn    = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    if is_new:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS metadata_log (
            stream    TEXT NOT NULL,
            ts        REAL NOT NULL,
            type      TEXT NOT NULL,
            title     TEXT DEFAULT '',
            artist    TEXT DEFAULT '',
            show_name TEXT DEFAULT '',
            presenter TEXT DEFAULT '',
            raw       TEXT DEFAULT '',
            PRIMARY KEY (stream, ts, type)
        )
    """)
    conn.commit()
    return conn


def _meta_query(slug: str, midnight: float) -> list:
    """Return metadata events for slug on the day starting at midnight (UTC Unix ts).

    Checks the shared metadata.db in the recording root first so that
    catalog/foreign streams (written by another logger instance to the same
    filesystem) are visible.  Falls back to the local SQLite DB.
    """
    end = midnight + 86400
    _SQL = ("SELECT ts, type, title, artist, show_name, presenter FROM metadata_log "
            "WHERE stream=? AND ts>=? AND ts<? ORDER BY ts")

    def _rows_to_events(rows):
        return [{"ts_s": r["ts"] - midnight, "type": r["type"],
                 "title": r["title"] or "", "artist": r["artist"] or "",
                 "show_name": r["show_name"] or "", "presenter": r["presenter"] or ""}
                for r in rows]

    # Shared DB (covers both local and foreign streams on a shared filesystem)
    try:
        sroot = _stream_rec_root_by_slug(slug)
        shared_path = sroot / "metadata.db"
        if shared_path.exists():
            with _smd_lock(sroot):
                sdb  = _open_shared_meta_db(sroot)
                rows = sdb.execute(_SQL, (slug, midnight, end)).fetchall()
                sdb.close()
            if rows:
                return _rows_to_events(rows)
    except Exception as e:
        _log(f"[Logger] Shared meta DB read error for '{slug}': {e}")

    # Fall back to local SQLite (pre-1.5.2 recordings not yet seeded)
    try:
        db   = _get_db()
        rows = db.execute(_SQL, (slug, midnight, end)).fetchall()
        db.close()
        return _rows_to_events(rows)
    except Exception:
        return []

def _upsert_segment(slug, date, filename, start_s, silence_ranges, quality="high"):
    total_sil = sum(e - s for s, e in silence_ranges)
    has_sil   = 1 if total_sil > 0.5 else 0
    sil_pct   = min(100.0, total_sil / _SEG_SECS * 100)
    try:
        db = _get_db()
        db.execute("""
            INSERT OR REPLACE INTO segments
            (stream, date, filename, start_s, has_silence, silence_pct, silence_ranges, quality)
            VALUES (?,?,?,?,?,?,?,?)
        """, (slug, date, filename, start_s, has_sil, sil_pct,
              json.dumps(silence_ranges), quality))
        db.commit()
        db.close()
    except Exception as e:
        _log(f"[Logger] DB write error: {e}")

def _get_segments(slug, date, base_root=None):
    root    = base_root if base_root is not None else _stream_rec_root_by_slug(slug)
    day_dir = root / slug / date
    result  = {}
    try:
        db   = _get_db()
        rows = db.execute(
            "SELECT * FROM segments WHERE stream=? AND date=? ORDER BY start_s",
            (slug, date)).fetchall()
        db.close()
        for r in rows:
            d = dict(r)
            try:
                d["silence_ranges"] = json.loads(d.get("silence_ranges") or "[]")
            except Exception:
                d["silence_ranges"] = []
            result[d["filename"]] = d
    except Exception:
        pass
    # Supplement with filesystem scan (all supported audio formats)
    if day_dir.exists():
        found = []
        for pat in _AUDIO_GLOBS:
            found.extend(day_dir.glob(pat))
        for f in sorted(found, key=lambda x: x.name):
            if f.name not in result:
                ss = float(_fname_to_secs(f.name) or 0)
                result[f.name] = {
                    "stream": slug, "date": date, "filename": f.name,
                    "start_s": ss, "has_silence": 0, "silence_pct": 0.0,
                    "silence_ranges": [], "quality": "high"
                }
    return sorted(result.values(), key=lambda x: x["start_s"])


# ─────────────────────────────────────────────────────────────────────────────
#  RECORDER THREAD
# ─────────────────────────────────────────────────────────────────────────────

class _RecorderThread(threading.Thread):
    """Records one SignalScope input by tapping its internal _stream_buffer.

    SignalScope stores decoded PCM for every input as np.float32 chunks
    (48 kHz, mono, 24000 samples = 0.5 s each) in a rolling deque.  We
    drain that deque into an ffmpeg process via stdin so that FM, DAB,
    HTTP, AoIP, sound-device — anything SignalScope monitors — can be
    logged without re-opening the source.
    """

    # SignalScope constants (must match signalscope.py)
    _SR         = 48000
    _CHUNK_SIZE = 24000   # samples per chunk (0.5 s)

    def __init__(self, stream_name, slug):
        super().__init__(daemon=True, name=f"Logger-{slug}")
        self.stream_name = stream_name
        self.slug        = slug
        self.stop_evt    = threading.Event()
        self.last_error  = None   # last ffmpeg error string, or None if OK
        self.last_ok_ts  = None   # time.time() of last successful segment
        self.seg_count   = 0      # total segments successfully written

    def _get_buf(self):
        """Return the live _stream_buffer deque for this input, or None."""
        try:
            for inp in _monitor.app_cfg.inputs:
                if inp.name == self.stream_name:
                    return getattr(inp, "_stream_buffer", None)
        except Exception:
            pass
        return None

    def run(self):
        _log(f"[Logger] Started recording: {self.stream_name}")
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        if not ffmpeg:
            self.last_error = "ffmpeg not found"
            _log(f"[Logger] ffmpeg not found — cannot record {self.stream_name}")
            return
        while not self.stop_evt.is_set():
            try:
                self._record_segment(ffmpeg)
            except Exception as e:
                self.last_error = str(e)
                _log(f"[Logger] Recorder error ({self.stream_name}): {e}")
                self.stop_evt.wait(5)
        _log(f"[Logger] Stopped recording: {self.stream_name}")
        try:
            _catalog_remove(_stream_rec_root(self.stream_name), self.slug)
        except Exception:
            pass

    def _record_segment(self, ffmpeg):
        scfg = _stream_cfg(self.stream_name)
        if not scfg["enabled"]:
            self.stop_evt.set()
            return

        # ── Catalog ownership check ─────────────────────────────────────
        rec_root = _stream_rec_root(self.stream_name)
        try:
            cat = _catalog_read(rec_root)
            entry = cat.get(self.slug)
            if entry and entry.get("owner") != _my_owner():
                self.last_error = f"Stream owned by '{entry['owner']}'"
                _log(f"[Logger] Skipping {self.stream_name} — already recorded by '{entry['owner']}'")
                self.stop_evt.wait(30)
                return
        except Exception:
            pass

        now       = datetime.datetime.utcnow()
        seg_start = _seg_start(now)
        seg_end   = seg_start + datetime.timedelta(seconds=_SEG_SECS)
        date_str  = seg_start.strftime("%Y-%m-%d")
        time_str  = seg_start.strftime("%H-%M")
        rec_fmt   = scfg["rec_format"]
        _codec_args, _container, _ext, _ar = _REC_FORMATS[rec_fmt]
        filename  = f"{time_str}.{_ext}"
        start_s   = seg_start.hour * 3600 + seg_start.minute * 60

        out_dir  = rec_root / self.slug / date_str
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / filename

        # Skip if this segment already exists and looks complete
        if out_path.exists() and out_path.stat().st_size > 5000:
            wait = (seg_end - datetime.datetime.utcnow()).total_seconds()
            if wait > 0:
                self.stop_evt.wait(wait)
            return

        # Wait up to 10 s for SignalScope to populate the buffer
        waited = 0
        while self._get_buf() is None and waited < 10 and not self.stop_evt.is_set():
            self.stop_evt.wait(1)
            waited += 1
        if self.stop_evt.is_set():
            return

        # Launch ffmpeg reading raw float32 PCM from stdin.
        # silencedetect runs as an audio filter so we keep that metadata.
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "warning",
               "-f", "f32le", "-ar", str(self._SR), "-ac", "1", "-i", "pipe:0",
               "-af", f"silencedetect=n={_SILENCE_DB:.1f}dB:d={_SILENCE_DUR}",
               "-vn", "-ac", "1", "-ar", _ar,
               *_codec_args, "-b:a", scfg["hq_bitrate"],
               "-f", _container, str(out_path)]

        silence_ranges = []
        sil_start      = None
        stderr_lines   = []

        try:
            proc = subprocess.Popen(cmd,
                                    stdin=subprocess.PIPE,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE,
                                    bufsize=0)
        except Exception as e:
            self.last_error = str(e)
            _log(f"[Logger] Could not launch ffmpeg for {self.stream_name}: {e}")
            return

        # Read stderr on a background thread so it never blocks the write loop.
        # stdin must stay binary (float32 PCM) so we can't use text=True on the
        # whole Popen — decode stderr lines explicitly instead.
        def _read_stderr():
            for raw in proc.stderr:
                stderr_lines.append(raw.rstrip().decode("utf-8", errors="replace"))
        stderr_thr = threading.Thread(target=_read_stderr, daemon=True)
        stderr_thr.start()

        # Drain _stream_buffer → ffmpeg stdin until segment end.
        # We track the last chunk object we've written; if it disappears from
        # the deque (buffer wrapped) we log a warning and catch up.
        last_ref = None

        try:
            while not self.stop_evt.is_set():
                if datetime.datetime.utcnow() >= seg_end:
                    break

                buf = self._get_buf()
                if not buf:
                    self.stop_evt.wait(0.3)
                    continue

                chunks = list(buf)   # thread-safe snapshot
                if not chunks:
                    self.stop_evt.wait(0.3)
                    continue

                if last_ref is None:
                    # First iteration — anchor to latest chunk, don't replay history
                    last_ref = chunks[-1]
                    new_chunks = []
                else:
                    # Find our anchor in the current snapshot
                    idx = next((i for i, c in enumerate(chunks) if c is last_ref), None)
                    if idx is None:
                        # Anchor evicted — buffer lapped us (shouldn't happen with 0.2 s poll)
                        _log(f"[Logger] Buffer overrun on '{self.stream_name}', small gap possible")
                        new_chunks = chunks
                    else:
                        new_chunks = chunks[idx + 1:]
                    if new_chunks:
                        last_ref = new_chunks[-1]

                if new_chunks:
                    import numpy as np
                    raw = b"".join(c.astype(np.float32).tobytes() for c in new_chunks)
                    try:
                        proc.stdin.write(raw)
                    except (BrokenPipeError, OSError):
                        break

                self.stop_evt.wait(0.2)

        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass
            proc.wait()
            stderr_thr.join(timeout=2)

        # Parse silence events from accumulated stderr
        for line in stderr_lines:
            m_s = re.search(r"silence_start:\s*([\d.]+)", line)
            m_e = re.search(r"silence_end:\s*([\d.]+)", line)
            if m_s:
                sil_start = float(m_s.group(1))
            if m_e and sil_start is not None:
                silence_ranges.append([sil_start, float(m_e.group(1))])
                sil_start = None
        if sil_start is not None:
            silence_ranges.append([sil_start, _SEG_SECS])

        if proc.returncode not in (0, None) and not self.stop_evt.is_set():
            err = next((l for l in reversed(stderr_lines)
                        if l.strip() and not l.startswith("  ")), "unknown ffmpeg error")
            self.last_error = f"rc={proc.returncode}: {err}"[:250]
            _log(f"[Logger] ffmpeg failed for '{self.stream_name}': {self.last_error}")
            self.stop_evt.wait(10)
            return

        if out_path.exists() and out_path.stat().st_size > 1000:
            _upsert_segment(self.slug, date_str, filename, start_s, silence_ranges)
            self.last_error = None
            self.last_ok_ts = time.time()
            self.seg_count += 1
            _catalog_write(rec_root, self.slug, self.stream_name, scfg["rec_format"])
            _log(f"[Logger] Segment saved: {self.stream_name}/{date_str}/{filename}")

        # Wait for next 5-minute boundary
        if not self.stop_evt.is_set():
            wait = (seg_end - datetime.datetime.utcnow()).total_seconds()
            if wait > 0:
                self.stop_evt.wait(wait)

    def stop(self):
        self.stop_evt.set()


def _seg_start(now_utc):
    total = now_utc.hour * 3600 + now_utc.minute * 60 + now_utc.second
    base  = (total // _SEG_SECS) * _SEG_SECS
    return now_utc.replace(hour=base // 3600, minute=(base % 3600) // 60,
                           second=0, microsecond=0)

def _seed_shared_meta_dbs():
    """Merge this instance's local SQLite metadata into the shared metadata.db.

    Runs on every startup.  Uses INSERT OR IGNORE so multiple logger instances
    sharing the same recording root each contribute their own events without
    overwriting each other — idempotent regardless of upgrade order.
    """
    try:
        db    = _get_db()
        slugs = [r[0] for r in db.execute("SELECT DISTINCT stream FROM metadata_log").fetchall()]
        db.close()
    except Exception:
        return
    for slug in slugs:
        try:
            sroot = _stream_rec_root_by_slug(slug)
            db    = _get_db()
            rows  = db.execute(
                "SELECT stream, ts, type, title, artist, show_name, presenter, raw "
                "FROM metadata_log WHERE stream=?", (slug,)).fetchall()
            db.close()
            if not rows:
                continue
            with _smd_lock(sroot):
                sdb   = _open_shared_meta_db(sroot)
                n_new = 0
                for r in rows:
                    cur = sdb.execute(
                        "INSERT OR IGNORE INTO metadata_log "
                        "(stream, ts, type, title, artist, show_name, presenter, raw) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (r["stream"], r["ts"], r["type"], r["title"], r["artist"],
                         r["show_name"], r["presenter"], r["raw"]))
                    n_new += cur.rowcount
                sdb.commit()
                sdb.close()
            if n_new:
                _log(f"[Logger] Merged {n_new} metadata event(s) into shared DB for '{slug}'")
        except Exception as e:
            _log(f"[Logger] Metadata seed error for '{slug}': {e}")


def _delayed_start():
    time.sleep(3)
    _reconcile_recorders()
    _seed_shared_meta_dbs()

def _reconcile_recorders():
    streams = _available_streams()
    with _rec_lock:
        for s in streams:
            slug = s["slug"]
            scfg = _stream_cfg(s["name"])
            if scfg["enabled"] and (slug not in _recorders or not _recorders[slug].is_alive()):
                t = _RecorderThread(s["name"], slug)
                _recorders[slug] = t
                t.start()
            elif not scfg["enabled"] and slug in _recorders:
                _recorders[slug].stop()
                del _recorders[slug]
    # Seed catalog for all enabled streams so catalog.json exists immediately
    # after startup (or upgrade) without waiting for the first segment write.
    for s in streams:
        scfg = _stream_cfg(s["name"])
        if scfg["enabled"]:
            try:
                root = _stream_rec_root(s["name"])
                slug_dir = root / _slug(s["name"])
                if slug_dir.exists() and any(slug_dir.iterdir()):
                    _catalog_write(root, _slug(s["name"]), s["name"], scfg["rec_format"])
            except Exception:
                pass
    _reconcile_meta_pollers()


# ─────────────────────────────────────────────────────────────────────────────
#  MAINTENANCE
# ─────────────────────────────────────────────────────────────────────────────

def _maintenance_loop():
    time.sleep(120)
    while True:
        try:
            _run_maintenance()
        except Exception as e:
            _log(f"[Logger] Maintenance error: {e}")
        time.sleep(3600)

def _run_maintenance():
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    today  = datetime.date.today()
    seen_stream_dirs: set = set()

    for rec_root in _all_rec_roots():
        if not rec_root.exists():
            continue
        for stream_dir in rec_root.iterdir():
            if not stream_dir.is_dir():
                continue
            key = str(stream_dir.resolve())
            if key in seen_stream_dirs:
                continue
            seen_stream_dirs.add(key)
            name = _slug_to_name(stream_dir.name)
            if name is None:
                continue
            scfg = _stream_cfg(name)
            for day_dir in stream_dir.iterdir():
                if not day_dir.is_dir():
                    continue
                try:
                    day_date = datetime.date.fromisoformat(day_dir.name)
                except ValueError:
                    continue
                age      = (today - day_date).days
                retain   = scfg["retain_days"]
                lq_after = scfg["lq_after_days"]
                if retain > 0 and age >= retain:
                    try:
                        _assert_within_rec_root(day_dir)
                    except ValueError:
                        _log(f"[Logger] Refusing to prune path outside any rec root: {day_dir}")
                        continue
                    _log(f"[Logger] Pruning {day_dir.name} ({stream_dir.name})")
                    shutil.rmtree(day_dir, ignore_errors=True)
                elif lq_after > 0 and age >= lq_after:
                    for pat in _AUDIO_GLOBS:
                        for af in day_dir.glob(pat):
                            _maybe_downgrade(af, stream_dir.name, day_dir.name,
                                             scfg["lq_bitrate"], ffmpeg)

def _maybe_downgrade(path, slug, date, lq_br, ffmpeg):
    try:
        db  = _get_db()
        row = db.execute("SELECT quality FROM segments WHERE stream=? AND date=? AND filename=?",
                         (slug, date, path.name)).fetchone()
        db.close()
        if row and row["quality"] == "low":
            return
    except Exception:
        pass
    # Determine re-encode args from the file's own extension
    _ext_to_fmt = {".mp3": ("mp3", [],                  "44100"),
                   ".aac": ("adts", ["-c:a", "aac"],    "44100"),
                   ".opus": ("ogg", ["-c:a", "libopus"],"48000")}
    _lq_container, _lq_codec, _lq_ar = _ext_to_fmt.get(path.suffix, ("mp3", [], "44100"))
    tmp = path.with_name(path.stem + ".lq" + path.suffix)
    try:
        r = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error",
             "-i", str(path), *_lq_codec, "-b:a", lq_br, "-ac", "1",
             "-ar", _lq_ar, "-f", _lq_container, str(tmp)],
            timeout=120, capture_output=True)
        if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 1000:
            tmp.replace(path)
            db = _get_db()
            db.execute("UPDATE segments SET quality='low' WHERE stream=? AND date=? AND filename=?",
                       (slug, date, path.name))
            db.commit()
            db.close()
            _log(f"[Logger] Downgraded {slug}/{date}/{path.name}")
        else:
            tmp.unlink(missing_ok=True)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        _log(f"[Logger] Downgrade failed {path.name}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  CLIP EXPORT
# ─────────────────────────────────────────────────────────────────────────────

def _assert_within_rec_root(path: Path):
    """Raise ValueError if path escapes ALL known recordings roots."""
    resolved = path.resolve()
    for root in _all_rec_roots():
        try:
            resolved.relative_to(root.resolve())
            return   # within at least one known root — OK
        except ValueError:
            pass
    raise ValueError(f"Path escapes all recordings roots: {path}")

def _ffmpeg_concat_escape(p: Path) -> str:
    """Escape a path for use in an ffmpeg concat list file."""
    # ffmpeg concat format: single-quote the path, escape internal single quotes
    return str(p).replace("\\", "\\\\").replace("'", "\\'")

_EXPORT_FMTS = {
    # fmt: (ffmpeg_audio_args, container_flag, mime, ext)
    # MP3  — stream-copy existing segments (no re-encode, instant)
    "mp3":  (["-c", "copy"],                                   "mp3",  "audio/mpeg",         ".mp3"),
    # AAC  — ~half the size of MP3 at equal perceived quality; fragmented MP4 for pipe
    "aac":  (["-c:a", "aac", "-b:a", "128k",
              "-movflags", "frag_keyframe+empty_moov"],        "mp4",  "audio/mp4",           ".m4a"),
    # Opus — most efficient; WebM container plays in all modern browsers
    "opus": (["-c:a", "libopus", "-b:a", "96k"],               "webm", "audio/webm",          ".webm"),
}

def _export_clip(slug, date, start_s, end_s, fmt="mp3"):
    root    = _stream_rec_root_by_slug(slug)
    day_dir = root / slug / date
    if not day_dir.exists():
        return jsonify({"error": "no recordings"}), 404
    ffmpeg  = shutil.which("ffmpeg") or "ffmpeg"
    segs    = _get_segments(slug, date, base_root=root)

    audio_args, container, mime, ext = _EXPORT_FMTS.get(fmt, _EXPORT_FMTS["mp3"])
    # Stream-copy optimisation only works when source and target are both MP3;
    # for any other combination always re-encode via the codec args.
    _src_ext = None  # determined after relevant list is built

    # Build list of relevant segments, validating each path stays within rec root
    relevant = []
    for seg in segs:
        if not (seg["start_s"] + _SEG_SECS > start_s and seg["start_s"] < end_s):
            continue
        seg_path = day_dir / seg["filename"]
        if not seg_path.exists():
            continue
        try:
            _assert_within_rec_root(seg_path)
        except ValueError:
            continue
        relevant.append((seg["start_s"], seg_path))

    if not relevant:
        return jsonify({"error": "no segments in range"}), 404

    # Use stream-copy only when source segments are MP3 and target format is MP3
    _src_ext = relevant[0][1].suffix.lstrip(".")
    if fmt == "mp3" and _src_ext == "mp3":
        audio_args = ["-c", "copy"]

    tf_name = None
    try:
        if len(relevant) == 1:
            ss0, seg_path = relevant[0]
            ss = max(0.0, start_s - ss0)
            cmd = [ffmpeg, "-hide_banner", "-loglevel", "error",
                   "-ss", f"{ss:.3f}", "-i", str(seg_path),
                   "-t", f"{end_s - start_s:.3f}",
                   *audio_args, "-f", container, "pipe:1"]
        else:
            # Write concat list; use -safe 1 (relative paths not required when
            # paths are escaped correctly and don't use protocol prefixes).
            # We keep -safe 0 only because paths are absolute, but we validate
            # every path is within the recordings root above.
            tf = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
            tf_name = tf.name          # capture before close so finally can clean up
            try:
                for _, p in relevant:
                    tf.write(f"file '{_ffmpeg_concat_escape(p)}'\n")
            finally:
                tf.close()
            first_ss = relevant[0][0]
            ss       = max(0.0, start_s - first_ss)
            cmd = [ffmpeg, "-hide_banner", "-loglevel", "error",
                   "-f", "concat", "-safe", "0", "-i", tf_name,
                   "-ss", f"{ss:.3f}", "-t", f"{end_s - start_s:.3f}",
                   *audio_args, "-f", container, "pipe:1"]

        proc = subprocess.run(cmd, capture_output=True, timeout=120)
        if proc.returncode != 0:
            return jsonify({"error": "ffmpeg failed"}), 500
        h   = int(start_s) // 3600
        m   = (int(start_s) % 3600) // 60
        dur = int(end_s - start_s)
        fname = f"{slug}_{date}_{h:02d}h{m:02d}m_{dur}s{ext}"
        return Response(proc.stdout, mimetype=mime,
            headers={"Content-Disposition": f'attachment; filename="{fname}"'})
    except subprocess.TimeoutExpired:
        return jsonify({"error": "export timeout"}), 500
    finally:
        if tf_name:
            try:
                os.unlink(tf_name)
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _slug(name):
    return re.sub(r"[^\w\-]", "_", name).strip("_").lower() or "stream"

def _safe(s):
    """Sanitise a URL path component."""
    return re.sub(r"[^\w\-]", "_", s).lower()

def _fname_to_secs(filename):
    m = re.match(r"^(\d{2})-(\d{2})\.(mp3|aac|opus)$", filename)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 if m else None

def _serve_ranged(path: Path, mime: str):
    size    = path.stat().st_size
    rng_hdr = request.headers.get("Range")
    if rng_hdr:
        m = re.match(r"bytes=(\d*)-(\d*)", rng_hdr)
        if m:
            start  = int(m.group(1)) if m.group(1) else 0
            end    = int(m.group(2)) if m.group(2) else size - 1
            end    = min(end, size - 1)
            if start > end:
                return Response(status=416,
                                headers={"Content-Range": f"bytes */{size}"})
            length = end - start + 1
            with open(path, "rb") as f:
                f.seek(start)
                data = f.read(length)
            return Response(data, 206, mimetype=mime, headers={
                "Content-Range":  f"bytes {start}-{end}/{size}",
                "Accept-Ranges":  "bytes",
                "Content-Length": str(length),
                "Cache-Control":  "public, max-age=3600",
            })
    return send_file(path, mimetype=mime, conditional=True)


# ─────────────────────────────────────────────────────────────────────────────
#  TEMPLATE
# ─────────────────────────────────────────────────────────────────────────────

_TPL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{ csrf_token() }}">
<title>Logger — SignalScope</title>
<style nonce="{{ csp_nonce() }}">
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(circle at top,#12376f 0%,var(--bg) 38%,#05101f 100%);color:var(--tx);font-family:system-ui,sans-serif;font-size:14px;min-height:100vh}
a{color:var(--acc);text-decoration:none}
/* Header */
header{background:linear-gradient(180deg,rgba(10,31,65,.96),rgba(9,24,48,.96));border-bottom:1px solid var(--bor);padding:10px 18px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;box-shadow:0 10px 24px rgba(0,0,0,.18)}
header h1{font-size:16px;font-weight:700;color:var(--tx)}
header .sub{font-size:12px;color:var(--mu)}
.hdr-right{margin-left:auto;display:flex;gap:8px;align-items:center}
/* Sidebar + layout */
.app-wrap{display:flex;height:calc(100vh - 46px)}
.sb{width:200px;flex-shrink:0;background:var(--sur);border-right:1px solid var(--bor);padding:10px 0;overflow-y:auto}
.tb{display:flex;align-items:center;gap:8px;width:100%;padding:9px 15px;background:none;border:none;border-left:3px solid transparent;color:var(--mu);font-size:13px;cursor:pointer;text-align:left;transition:background .12s,color .12s}
.tb:hover{background:rgba(255,255,255,.04);color:var(--tx)}
.tb.on{background:rgba(255,255,255,.05);color:var(--tx);border-left-color:var(--acc);font-weight:600}
.tb-sep{height:1px;background:var(--bor);margin:8px 12px}
/* Content panels */
.content{flex:1;overflow:hidden;display:flex;flex-direction:column}
/* Sidebar stream/date controls */
.ctrl-panel{padding:12px 14px;border-bottom:1px solid var(--bor);display:flex;flex-direction:column;gap:12px}
.slbl{font-size:10px;font-weight:700;color:var(--mu);letter-spacing:.7px;text-transform:uppercase;margin-bottom:4px}
select,input[type=number]{width:100%;background:#173a69;border:1px solid var(--bor);color:var(--tx);padding:7px 9px;border-radius:6px;font-size:13px;outline:none;appearance:auto}
select:focus,input:focus{border-color:var(--acc)}
/* Date nav */
.date-nav{display:flex;align-items:center;gap:6px}
.date-nav .date-lbl{flex:1;text-align:center;font-weight:600;font-size:13px;color:var(--tx)}
.date-nav button{background:var(--sur);border:1px solid var(--bor);color:var(--mu);width:28px;height:28px;border-radius:5px;cursor:pointer;font-size:15px;flex-shrink:0}
.date-nav button:hover{color:var(--tx);border-color:var(--acc)}
/* Day list */
.day-list{padding:8px 10px;display:flex;flex-direction:column;gap:3px;overflow-y:auto;max-height:180px}
.day-btn{background:none;border:none;color:var(--mu);font-size:12px;text-align:left;padding:4px 6px;border-radius:4px;cursor:pointer;width:100%}
.day-btn:hover{background:rgba(23,168,255,.1);color:var(--acc)}
.day-btn.active{color:var(--acc);font-weight:600}
/* Seg info */
.seg-info{margin:8px 12px;padding:10px 12px;background:var(--bg);border:1px solid var(--bor);border-radius:8px;font-size:12px;color:var(--mu);display:none}
/* Buttons */
.btn{display:inline-flex;align-items:center;gap:5px;padding:5px 12px;border-radius:8px;font-size:13px;cursor:pointer;border:none;font-weight:600;transition:filter .14s}
.btn:hover{filter:brightness(1.08)}
.btn:disabled{opacity:.4;cursor:not-allowed;filter:none}
.bp{background:var(--acc);color:#fff}
.bg{background:var(--bor);color:var(--tx)}
.bs{padding:3px 9px;font-size:12px}
/* Badge */
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700}
.b-rec{background:rgba(239,68,68,.2);color:#f87171}
.b-idle{background:#1e3a5f;color:var(--mu)}
.b-err{background:rgba(245,158,11,.2);color:#fbbf24}
.err-msg{margin-top:6px;padding:8px 10px;background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.25);border-radius:6px;font-size:12px;color:#f87171;word-break:break-word}
/* Timeline */
.tl-wrap{flex:1;overflow-y:auto;padding:14px 18px}
.tl-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px}
.tl-title{font-size:15px;font-weight:700;color:var(--tx)}
.tl-legend{display:flex;gap:10px;font-size:11px;color:var(--mu)}
.tl-legend span{display:flex;align-items:center;gap:4px}
.tl-legend i{width:11px;height:11px;border-radius:2px;display:inline-block}
.tl-grid{display:grid;grid-template-columns:38px 1fr;gap:3px;align-items:center}
.tl-hour-lbl{text-align:right;font-size:10px;color:var(--mu);padding-right:7px;line-height:1}
.tl-row{display:grid;grid-template-columns:repeat(12,1fr);gap:2px}
.tl-block{height:22px;border-radius:3px;cursor:pointer;transition:opacity .1s,outline .1s;position:relative}
.tl-block:hover{opacity:.75;outline:2px solid var(--acc)}
.tl-block.selected{outline:2px solid #fff}
.tl-block.playing{outline:2px solid var(--ok)}
.tl-block[data-status="none"]{background:#0e2040}
.tl-block[data-status="ok"]{background:#166534}
.tl-block[data-status="warn"]{background:#78350f}
.tl-block[data-status="silent"]{background:#7f1d1d}
.tl-block[data-status="future"]{background:#0a1828}
.tl-block::after{content:attr(data-tip);position:absolute;bottom:calc(100% + 5px);left:50%;transform:translateX(-50%);background:#0d2346;border:1px solid var(--bor);padding:4px 8px;border-radius:4px;font-size:11px;white-space:nowrap;color:var(--tx);pointer-events:none;opacity:0;transition:opacity .12s;z-index:10}
.tl-block:hover::after{opacity:1}
.tl-block.in-range::before{content:'';position:absolute;inset:0;background:rgba(23,168,255,.30);border-radius:3px;pointer-events:none}
/* Zoom / expand controls */
.zoom-grp{display:flex;gap:1px;background:var(--bor);border-radius:5px;overflow:hidden;flex-shrink:0}
.zoom-btn{padding:3px 9px;font-size:11px;font-weight:700;border:none;cursor:pointer;background:var(--sur);color:var(--mu);transition:background .12s,color .12s;white-space:nowrap;line-height:1.4}
.zoom-btn:hover{background:rgba(255,255,255,.07);color:var(--tx)}
.zoom-btn.zact{background:var(--acc);color:#fff}
/* Horizontal scroll wrapper for zoomed overview */
.tl-scroll-wrap{overflow-x:auto;overflow-y:visible;margin:0 -1px;padding:0 1px;cursor:grab}
.tl-scroll-wrap.tl-panning{cursor:grabbing;user-select:none}
.tl-scroll-wrap::-webkit-scrollbar{height:4px}
.tl-scroll-wrap::-webkit-scrollbar-thumb{background:var(--bor);border-radius:2px}
.tl-scroll-wrap::-webkit-scrollbar-thumb:hover{background:var(--mu)}
#tl-zoom-content{min-width:100%;width:100%;transition:width .22s}
/* Expanded heights — day-bar grows, bands grow, hour grid stays the same */
.tl-wrap.tl-exp .day-bar{height:80px}
.tl-wrap.tl-exp #show-band{height:30px}
.tl-wrap.tl-exp .show-span{height:28px;font-size:11px}
.tl-wrap.tl-exp #track-band{height:30px}
.tl-wrap.tl-exp .track-span{height:28px;font-size:11px}
.tl-wrap.tl-exp #mic-band{height:22px}
.tl-wrap.tl-exp .mic-span{height:20px}
/* Separator between zoomed overview and the hour grid */
#tl-grid{margin-top:8px;padding-top:6px;border-top:1px solid var(--bor)}
/* Day bar */
.day-bar{width:100%;height:30px;position:relative;background:#0a1828;border-radius:6px;overflow:hidden;cursor:pointer;margin-bottom:12px;flex-shrink:0;border:1px solid var(--bor);user-select:none}
.day-bar-bg{display:flex;height:100%;width:100%}
.day-bar-blk{height:100%;flex:1}
.day-bar-head{position:absolute;top:0;bottom:0;width:2px;background:var(--ok);pointer-events:none;z-index:4;transform:translateX(-50%)}
.day-bar-in{position:absolute;top:0;bottom:0;width:2px;background:var(--acc);pointer-events:none;z-index:3;transform:translateX(-50%)}
.day-bar-out{position:absolute;top:0;bottom:0;width:2px;background:#f59e0b;pointer-events:none;z-index:3;transform:translateX(-50%)}
.day-bar-range{position:absolute;top:0;bottom:0;background:rgba(23,168,255,.18);pointer-events:none;z-index:2}
.day-bar-hover{position:absolute;top:0;bottom:0;width:1px;background:rgba(255,255,255,.3);pointer-events:none;z-index:5;display:none}
.day-bar-ticks{position:absolute;inset:0;pointer-events:none;z-index:1}
/* Notice */
.notice{background:rgba(23,168,255,.08);border:1px solid rgba(23,168,255,.25);border-radius:8px;padding:11px 14px;font-size:13px;color:var(--mu);margin-bottom:12px}
.notice b{color:var(--acc)}
.notice-link{color:var(--acc);cursor:pointer;text-decoration:underline}
/* Player */
.player{background:linear-gradient(180deg,#143766,#102b54);border-top:1px solid var(--bor);padding:11px 18px;display:flex;flex-direction:column;gap:8px}
.player-top{display:flex;align-items:center;gap:10px}
.player-info{flex:1;min-width:0}
.p-title{font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--tx)}
.p-sub{font-size:11px;color:var(--mu)}
.play-btn{width:34px;height:34px;border-radius:50%;background:var(--ok);border:none;color:#fff;cursor:pointer;font-size:15px;display:flex;align-items:center;justify-content:center;flex-shrink:0}
.play-btn:hover{filter:brightness(1.1)}
.time-lbl{font-size:12px;color:var(--mu);font-variant-numeric:tabular-nums;min-width:60px;text-align:center}
/* Scrub */
.scrub-wrap{position:relative;height:22px;display:flex;align-items:center;cursor:pointer}
.scrub-track{width:100%;height:4px;background:#0e2040;border-radius:2px;position:relative;overflow:visible;border:1px solid var(--bor)}
.scrub-fill{height:4px;background:var(--ok);border-radius:2px;pointer-events:none}
.scrub-thumb{position:absolute;top:50%;transform:translate(-50%,-50%);width:12px;height:12px;border-radius:50%;background:var(--ok);pointer-events:none;box-shadow:0 0 4px rgba(34,197,94,.5)}
.scrub-in,.scrub-out{position:absolute;top:-4px;bottom:-4px;width:2px;background:var(--acc);pointer-events:none;z-index:2}
/* Export bar */
.export-bar{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--mu)}
.inout-lbl{color:var(--acc);font-weight:600;font-size:12px}
/* Settings */
.settings-content{flex:1;overflow-y:auto;padding:18px 22px;max-width:780px}
.settings-content h2{font-size:16px;font-weight:700;color:var(--tx);margin-bottom:4px}
.settings-content .sub{color:var(--mu);font-size:13px;margin-bottom:18px}
/* Stream card in settings */
.scard{background:var(--sur);border:1px solid var(--bor);border-radius:10px;overflow:hidden;margin-bottom:10px}
.scard-hdr{background:linear-gradient(180deg,#143766,#102b54);padding:10px 14px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--bor)}
.scard-hdr .name{font-weight:700;font-size:14px;color:var(--tx)}
.scard-hdr .url{font-size:11px;color:var(--mu);margin-top:2px;word-break:break-all}
.scard-body{padding:14px}
.scard-fields{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.scard-fields label{font-size:11px;color:var(--mu);display:block;margin-bottom:5px;font-weight:600;letter-spacing:.03em;text-transform:uppercase}
/* Toggle switch */
.tog-wrap{display:flex;align-items:center;gap:8px}
.tog{position:relative;width:38px;height:20px;flex-shrink:0}
.tog input{opacity:0;width:0;height:0;position:absolute}
.tog-sl{position:absolute;inset:0;background:var(--bor);border-radius:20px;cursor:pointer;transition:.2s}
.tog-sl:before{content:'';position:absolute;height:14px;width:14px;left:3px;bottom:3px;background:var(--mu);border-radius:50%;transition:.2s}
.tog input:checked + .tog-sl{background:var(--ok)}
.tog input:checked + .tog-sl:before{transform:translateX(18px);background:#fff}
.tog-lbl{font-size:12px;color:var(--mu)}
.tog-lbl.on{color:var(--ok);font-weight:600}
/* Disk info */
.disk-card{margin-top:14px;padding:11px 14px;background:var(--sur);border:1px solid var(--bor);border-radius:8px;font-size:13px;color:var(--mu)}
.hidden{display:none}
/* Show band */
#show-band{width:100%;height:20px;position:relative;background:transparent;margin-bottom:4px;flex-shrink:0}
.show-span{position:absolute;top:1px;height:18px;background:rgba(109,40,217,.15);border-left:2px solid rgba(167,139,250,.7);border-radius:0 2px 2px 0;overflow:hidden;white-space:nowrap;font-size:10px;color:#c4b5fd;padding:0 5px;display:flex;align-items:center;box-sizing:border-box}
/* Track blocks — amber tint over existing status colour */
.tl-block.has-track[data-status="ok"]{background:#92400e}
.tl-block.has-track[data-status="warn"]{background:#7c3a0a}
/* Track band — precise per-song spans below the hour grid */
#track-band{width:100%;height:20px;position:relative;background:transparent;margin-top:3px;flex-shrink:0}
.track-span{position:absolute;top:1px;height:18px;background:rgba(180,83,9,.2);border-left:2px solid rgba(251,191,36,.8);border-radius:0 2px 2px 0;overflow:hidden;white-space:nowrap;font-size:10px;color:#fcd34d;padding:0 5px;display:flex;align-items:center;box-sizing:border-box;cursor:default}
.track-span:hover{background:rgba(180,83,9,.45);z-index:5}
/* Mic band — on-air periods (green) */
#mic-band{width:100%;height:14px;position:relative;background:transparent;margin-top:3px;flex-shrink:0}
.mic-span{position:absolute;top:1px;height:12px;background:rgba(16,185,129,.25);border-left:2px solid rgba(52,211,153,.9);border-radius:0 2px 2px 0;box-sizing:border-box;cursor:default}
.mic-span:hover{background:rgba(16,185,129,.45);z-index:5}
.mic-span.mic-live{background:rgba(16,185,129,.4);border-left-color:#6ee7b7}
.tl-block.has-track[data-status="none"]{background:#3d2000}
/* Now playing URL input in settings */
.np-url-row{padding-top:10px;border-top:1px solid var(--bor);margin-top:10px}
</style>
</head>
<body>
<header>
  <span style="font-size:20px">🎙</span>
  <div>
    <h1>Logger</h1>
    <div class="sub">Compliance recording &amp; playback</div>
  </div>
  <div class="hdr-right">
    <span id="rec-status" class="badge b-idle">Idle</span>
    <a href="/" class="btn bg bs">← SignalScope</a>
  </div>
</header>
<div id="hdr-errors" style="padding:0 18px"></div>

<div class="app-wrap">
  <!-- Left sidebar nav -->
  <nav class="sb" id="sidebar">
    <button class="tb on" id="tab-timeline" data-tab="timeline">📅 Timeline</button>
    <button class="tb" id="tab-settings" data-tab="settings">⚙ Settings</button>
    <div class="tb-sep"></div>

    <!-- Stream + date controls (shown in timeline mode) -->
    <div class="ctrl-panel" id="ctrl-panel">
      <div id="hub-site-row" style="display:none">
        <div class="slbl">Site</div>
        <select id="site-sel"></select>
      </div>
      <div id="hub-status" style="font-size:11px;color:var(--wn);padding:2px 0 0 2px;min-height:14px"></div>
      <div>
        <div class="slbl">Stream</div>
        <select id="stream-sel">
          <option value="">— select —</option>
        </select>
      </div>
      <div>
        <div class="slbl">Date</div>
        <div class="date-nav">
          <button id="btn-prev-day">‹</button>
          <div class="date-lbl" id="date-lbl">—</div>
          <button id="btn-next-day">›</button>
        </div>
      </div>
    </div>

    <div class="day-list" id="day-list"></div>
    <div class="seg-info" id="seg-info"><div id="seg-info-text"></div></div>
  </nav>

  <!-- Main content area -->
  <div class="content">

    <!-- TIMELINE VIEW -->
    <div id="view-timeline" style="display:flex;flex-direction:column;flex:1;overflow:hidden">
      <div class="tl-wrap" id="tl-wrap">
        <div class="tl-head">
          <div class="tl-title" id="tl-title">Select a stream</div>
          <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-left:auto">
            <div class="zoom-grp">
              <button class="zoom-btn zact" data-z="1">1×</button>
              <button class="zoom-btn" data-z="2">2×</button>
              <button class="zoom-btn" data-z="4">4×</button>
              <button class="zoom-btn" data-z="8">8×</button>
            </div>
            <button class="zoom-btn" id="exp-btn" title="Expand rows" style="border-radius:5px">↕ Expand</button>
            <div class="tl-legend">
              <span><i style="background:#166534"></i> OK</span>
              <span><i style="background:#78350f"></i> Some silence</span>
              <span><i style="background:#7f1d1d"></i> Silence</span>
              <span><i style="background:#0e2040"></i> No recording</span>
              <span><i style="background:#92400e"></i> Track</span>
            </div>
          </div>
        </div>
        <div id="tl-notice" class="notice hidden">
          <b>No streams enabled for recording.</b> Open <span class="notice-link" id="notice-settings-link">Settings</span> to choose which streams to log.
        </div>
        <div class="tl-scroll-wrap">
          <div id="tl-zoom-content">
            <div class="day-bar" id="day-bar">
              <div class="day-bar-bg" id="day-bar-bg"></div>
              <div class="day-bar-range hidden" id="day-bar-range"></div>
              <div class="day-bar-head hidden" id="day-bar-head"></div>
              <div class="day-bar-in hidden" id="day-bar-in"></div>
              <div class="day-bar-out hidden" id="day-bar-out"></div>
              <div class="day-bar-hover" id="day-bar-hover"></div>
            </div>
            <div id="tl-time-axis" style="display:flex;justify-content:space-between;font-size:10px;color:var(--mu);margin-bottom:4px;padding:0 1px">
              <span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>23:55</span>
            </div>
            <div id="show-band"></div>
            <div id="mic-band"></div>
            <div id="track-band"></div>
          </div>
        </div>
        <div id="tl-grid" class="tl-grid"></div>
      </div>

      <div class="player">
        <audio id="audio-el" preload="none"></audio>
        <div class="player-top">
          <button class="play-btn" id="play-btn">▶</button>
          <div class="player-info">
            <div class="p-title" id="p-title">No segment selected</div>
            <div class="p-sub" id="p-sub">Click a block on the timeline to play</div>
          </div>
          <span class="time-lbl" id="time-lbl">--:--:--</span>
        </div>
        <div class="scrub-wrap" id="scrub">
          <div class="scrub-track">
            <div class="scrub-fill" id="scrub-fill" style="width:0%"></div>
            <div class="scrub-thumb" id="scrub-thumb" style="left:0%"></div>
            <div class="scrub-in hidden" id="scrub-in"></div>
            <div class="scrub-out hidden" id="scrub-out"></div>
          </div>
        </div>
        <div class="export-bar">
          <button class="btn bg bs" id="mark-in-btn">⬥ Mark In</button>
          <button class="btn bg bs" id="mark-out-btn">⬥ Mark Out</button>
          <span class="inout-lbl" id="inout-lbl"></span>
          <div style="flex:1"></div>
          <select id="export-fmt" title="Export format" style="background:#173a69;border:1px solid var(--bor);color:var(--tx);padding:4px 7px;border-radius:6px;font-size:12px;outline:none;cursor:pointer">
            <option value="mp3">MP3</option>
            <option value="aac">AAC</option>
            <option value="opus">Opus</option>
          </select>
          <button class="btn bp bs" id="export-btn" disabled>⬇ Export Clip</button>
        </div>
      </div>
    </div>

    <!-- SETTINGS VIEW -->
    <div id="view-settings" class="settings-content hidden">
      <h2>Recording Settings</h2>
      <p class="sub">Enable recording per stream. Changes take effect immediately after saving.</p>

      <!-- Base directories -->
      <div style="margin-bottom:18px;padding:14px 16px;background:var(--sur);border:1px solid var(--bor);border-radius:10px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
          <label style="font-size:12px;font-weight:700;color:var(--mu);letter-spacing:.6px;text-transform:uppercase">Base Directories</label>
          <button class="btn bp bs" id="add-basedir-btn" style="padding:4px 10px;font-size:12px">+ Add Directory</button>
        </div>
        <div style="font-size:12px;color:var(--mu);margin-bottom:8px">
          Named locations where recordings are stored. Assign each stream to a directory below.
          The <em>Default</em> path is used when no directory is assigned.
        </div>
        <div id="basedir-rows"></div>
        <!-- Default path (legacy / fallback) -->
        <div style="margin-top:10px;padding-top:10px;border-top:1px solid var(--bor)">
          <label style="font-size:11px;font-weight:700;color:var(--mu);letter-spacing:.5px;text-transform:uppercase;display:block;margin-bottom:4px">Default Path</label>
          <input type="text" id="rec-dir-input" placeholder="Default: logger_recordings (next to signalscope.py)"
                 style="width:100%;background:#173a69;border:1px solid var(--bor);color:var(--tx);padding:7px 10px;border-radius:6px;font-size:12px;font-family:monospace;outline:none">
          <div id="rec-dir-resolved" style="margin-top:4px;font-size:11px;color:var(--mu);font-family:monospace"></div>
        </div>
      </div>

      <!-- Mic API Key -->
      <div style="margin-bottom:18px;padding:14px 16px;background:var(--sur);border:1px solid var(--bor);border-radius:10px">
        <label style="font-size:12px;font-weight:700;color:var(--mu);letter-spacing:.6px;text-transform:uppercase;display:block;margin-bottom:6px">Mic API Key</label>
        <input type="text" id="mic-api-key-input" placeholder="Leave blank to require a logged-in session"
               style="width:100%;background:#173a69;border:1px solid var(--bor);color:var(--tx);padding:7px 10px;border-radius:6px;font-size:12px;font-family:monospace;outline:none">
        <div style="margin-top:5px;font-size:11px;color:var(--mu)">
          REST API: <code style="background:rgba(255,255,255,.06);padding:1px 5px;border-radius:3px">POST /api/logger/mic</code>
          with <code style="background:rgba(255,255,255,.06);padding:1px 5px;border-radius:3px">Authorization: Bearer &lt;key&gt;</code>
          — body: <code style="background:rgba(255,255,255,.06);padding:1px 5px;border-radius:3px">{"stream":"slug","state":"on","label":"Studio A"}</code>
        </div>
      </div>

      <div id="settings-rows"></div>
      <div style="margin-top:14px;display:flex;gap:10px;align-items:center">
        <button class="btn bp" id="save-settings-btn">💾 Save Settings</button>
        <span id="save-msg" style="color:var(--ok);font-size:13px;display:none">✓ Saved</span>
        <span id="save-err" style="color:var(--al);font-size:13px;display:none"></span>
      </div>
      <div class="disk-card" id="disk-info">Calculating disk usage…</div>
    </div>

  </div><!-- /content -->
</div><!-- /app-wrap -->

<script nonce="{{ csp_nonce() }}">
(function(){
// ── Helpers ───────────────────────────────────────────────────────────────
function _csrf(){ return (document.querySelector('meta[name="csrf-token"]')||{}).content||''; }
function _get(u){ return fetch(u,{credentials:'same-origin'}).then(function(r){ return r.json(); }); }
function _post(u,b){
  return fetch(u,{method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRFToken':_csrf()},
    body:JSON.stringify(b)}).then(function(r){ return r.json(); });
}
function _esc(s){ var d=document.createElement('div'); d.textContent=s; return d.innerHTML; }
function _fmt(s){ s=isNaN(s)?0:s; var m=Math.floor(s/60),ss=Math.floor(s%60); return m+':'+(ss<10?'0':'')+ss; }
function _fmtAbs(s){ var h=Math.floor(s/3600),m=Math.floor((s%3600)/60),sc=Math.floor(s%60); return (h>0?h+':':'')+(m<10&&h>0?'0':'')+m+':'+(sc<10?'0':'')+sc; }
function _fmtWall(s){ s=s||0; var h=Math.floor(s/3600)%24,m=Math.floor((s%3600)/60),sc=Math.floor(s%60); return String(h).padStart(2,'0')+':'+String(m).padStart(2,'0')+':'+String(sc).padStart(2,'0'); }
function _fmtBytes(b){ if(b<1048576) return (b/1024).toFixed(1)+'KB'; if(b<1073741824) return (b/1048576).toFixed(1)+'MB'; return (b/1073741824).toFixed(2)+'GB'; }

// ── State ─────────────────────────────────────────────────────────────────
var _SEG_SECS    = 300;
var _streams     = [];
var _currentSlug = '';
var _currentDate = '';
var _selSeg      = null;
var _segsAll     = [];
var _markIn      = null;
var _markOut     = null;
var _scrubDrag   = false;
var _cfg         = {streams:{}};
var _planetStations = [];  // [{rpuid, name}] from /api/nowplaying_stations
var _tlZoom = 1;           // current horizontal zoom level (1/2/4/8)
var _tlExp  = false;       // expanded row height mode
var _metaEvents  = [];   // metadata events for the current day

// ── Hub mode state ─────────────────────────────────────────────────────────
var _hubSite        = '';    // empty = local mode, non-empty = hub mode
var _hubPlayStart   = null;
var _hubPlayOffset  = 0;
var _hubIsPlaying   = false; // true while PCM stream is active
var _hubPlayPending = false; // true while startHubPlay POST is in-flight (prevents double-start)

// ── PCM audio pump (for hub mode playback) ─────────────────────────────────
var _audioCtx=null,_gainNode=null,_pcmReader=null,_nextTime=0;
var _pcmBuf=new Uint8Array(0);
var _activeSrcs=[];  // all live AudioBufferSource nodes — stopped on stream switch
var _SR=48000,_BLK_S=4800,_BLK_B=9600,_PRE=0.6;
function _initAudio(){
  if(_audioCtx)return;
  _audioCtx=new(window.AudioContext||window.webkitAudioContext)({sampleRate:_SR});
  _gainNode=_audioCtx.createGain(); _gainNode.connect(_audioCtx.destination);
}
function _killActiveSrcs(){
  // Stop every scheduled AudioBufferSource so old audio cannot bleed into the
  // next stream. .stop() is safe to call even on nodes that haven't started yet.
  var srcs = _activeSrcs; _activeSrcs = [];
  srcs.forEach(function(s){ try{ s.stop(); }catch(e){} });
}
function _stopHubAudio(){
  // Cancel incoming stream, kill scheduled buffers, and mute.
  // Increment _playGen so any in-flight POST response is treated as stale.
  ++_playGen;
  if(_pcmReader){try{_pcmReader.cancel();}catch(e){}_pcmReader=null;}
  _pcmBuf=new Uint8Array(0);
  _killActiveSrcs();
  if(_gainNode && _audioCtx){
    _gainNode.gain.cancelScheduledValues(0);
    _gainNode.gain.value = 0;
  }
  _hubIsPlaying   = false;
  _hubPlayPending = false;
  _hubPlayStart   = null;
  var pb=document.getElementById('play-btn');
  pb.textContent='▶'; pb.disabled=false;
}
function connectAudio(url){
  if(_pcmReader){try{_pcmReader.cancel();}catch(e){}_pcmReader=null;}
  _pcmBuf=new Uint8Array(0);
  _initAudio(); if(_audioCtx.state==='suspended')_audioCtx.resume();
  // Clear any pending automation and unmute — use direct .value assignment
  // rather than setValueAtTime so there is no risk of conflicts with prior
  // scheduled events leaving the gain stuck at 0.
  _gainNode.gain.cancelScheduledValues(0);
  _gainNode.gain.value = 1;
  _nextTime=_audioCtx.currentTime+_PRE;
  fetch(url,{credentials:'same-origin'}).then(function(r){
    _pcmReader=r.body.getReader();
    (function pump(){_pcmReader.read().then(function(d){
      if(d.done||!_pcmReader)return;
      var tmp=new Uint8Array(_pcmBuf.length+d.value.length);
      tmp.set(_pcmBuf);tmp.set(d.value,_pcmBuf.length);_pcmBuf=tmp;
      while(_pcmBuf.length>=_BLK_B){
        var blk=_pcmBuf.slice(0,_BLK_B);_pcmBuf=_pcmBuf.slice(_BLK_B);
        var buf=_audioCtx.createBuffer(1,_BLK_S,_SR);
        var ch=buf.getChannelData(0);
        var dv=new DataView(blk.buffer,blk.byteOffset,blk.byteLength);
        for(var i=0;i<_BLK_S;i++)ch[i]=dv.getInt16(i*2,true)/32768.0;
        var src=_audioCtx.createBufferSource();
        src.buffer=buf;src.connect(_gainNode);
        var t=Math.max(_nextTime,_audioCtx.currentTime+0.05);
        src.start(t);_nextTime=t+buf.duration;
        _activeSrcs.push(src);
        src.onended=function(){ var i=_activeSrcs.indexOf(src); if(i>=0)_activeSrcs.splice(i,1); };
      }
      pump();
    });})();
  });
}

// ── Tab switching ─────────────────────────────────────────────────────────
function showTab(tab){
  var isTimeline = tab==='timeline';
  document.getElementById('view-timeline').style.display = isTimeline ? 'flex' : 'none';
  document.getElementById('view-settings').classList.toggle('hidden', isTimeline);
  document.getElementById('ctrl-panel').style.display = isTimeline ? '' : 'none';
  document.getElementById('day-list').style.display   = isTimeline ? '' : 'none';
  document.getElementById('seg-info').style.display   = (_selSeg && isTimeline) ? 'block' : 'none';
  document.getElementById('tab-timeline').classList.toggle('on', isTimeline);
  document.getElementById('tab-settings').classList.toggle('on', !isTimeline);
  if(!isTimeline) loadSettingsPanel();
}

document.getElementById('sidebar').addEventListener('click', function(e){
  var t = e.target.closest('[data-tab]');
  if(t) showTab(t.dataset.tab);
});

document.getElementById('notice-settings-link').addEventListener('click', function(){ showTab('settings'); });

// ── Init ──────────────────────────────────────────────────────────────────
var today = new Date();
_currentDate = today.toISOString().slice(0,10);
document.getElementById('date-lbl').textContent = _currentDate;

loadStreams();
loadStatus();
setInterval(loadStatus, 10000);
setupAudio();
checkHubMode();
// Hub playback time tracking interval
setInterval(function(){
  if(!_hubSite || !_hubPlayStart || !_selSeg) return;
  var wallPos = _hubPlayOffset + (Date.now() - _hubPlayStart) / 1000;
  document.getElementById('time-lbl').textContent = _fmtWall(wallPos);
  var dbh = document.getElementById('day-bar-head');
  dbh.style.left = (wallPos / 86400 * 100) + '%';
  dbh.classList.remove('hidden');
  // Update scrub fill using segment duration
  var segDur = _SEG_SECS;
  var elapsed = wallPos - _selSeg.start_s;
  var pct = Math.max(0, Math.min(100, elapsed / segDur * 100));
  document.getElementById('scrub-fill').style.width = pct + '%';
  document.getElementById('scrub-thumb').style.left  = pct + '%';
  updateScrubMarkers();
}, 200);

// ── Hub mode ───────────────────────────────────────────────────────────────
var _catalogStreams = [];  // [{slug, name, site, owner, rec_format}]

function checkHubMode(){
  _get('/api/logger/hub/catalog').then(function(entries){
    if(!Array.isArray(entries) || !entries.length) return;
    _catalogStreams = entries;
    // Populate stream selector directly from catalog — no site selection needed
    var sel = document.getElementById('stream-sel');
    sel.innerHTML = '<option value="">— select stream —</option>';
    entries.forEach(function(s){
      var o = document.createElement('option');
      o.value = s.slug;
      o.dataset.site = s.site;
      o.textContent = s.name + ' (' + s.site + ')';
      sel.appendChild(o);
    });
  }).catch(function(){});
}

document.getElementById('site-sel').addEventListener('change', function(){
  _hubSite = this.value;
  _stopHubAudio();
  _currentSlug = ''; _selSeg = null; _segsAll = []; _markIn = _markOut = null;
  _hubStatusMsg('');
  updateInOutLabel();
  document.getElementById('export-btn').disabled = true;
  document.getElementById('day-bar-head').classList.add('hidden');
  buildDayBar([]);
  buildTimeline([]);
  document.getElementById('day-list').innerHTML = '';
  if(_hubSite){
    // Load streams for selected site (legacy path — kept for backward compat)
    _get('/api/logger/hub/streams/' + encodeURIComponent(_hubSite)).then(function(streams){
      var sel = document.getElementById('stream-sel');
      sel.innerHTML = '<option value="">— select stream —</option>';
      streams.forEach(function(s){
        var o = document.createElement('option');
        o.value = s.slug;
        o.dataset.site = _hubSite;
        o.textContent = '[' + _hubSite + '] ' + s.name;
        sel.appendChild(o);
      });
    });
  } else {
    loadStreams();
  }
});

// ── API wrappers (local vs hub mode) ──────────────────────────────────────
function apiDays(slug, cb){
  if(!_hubSite){ _get('/api/logger/days/'+slug).then(cb); return; }
  var attempts = 0;
  var MAX_ATTEMPTS = 20;  // ~60s total before giving up
  function tryFetch(){
    attempts++;
    _get('/api/logger/hub/days/'+encodeURIComponent(_hubSite)+'/'+encodeURIComponent(slug)).then(function(d){
      if(d && d.pending){
        if(attempts >= MAX_ATTEMPTS){
          _hubStatusMsg('Remote site not responding — check SignalScope logs on client node');
          return;
        }
        _hubStatusMsg('Fetching dates from remote site\u2026 ('+attempts+')');
        setTimeout(tryFetch, 3000);
      } else {
        _hubStatusMsg('');
        cb(d);
      }
    }).catch(function(e){ setTimeout(tryFetch, 4000); });
  }
  tryFetch();
}

function apiSegments(slug, date, cb){
  if(!_hubSite){ _get('/api/logger/segments/'+slug+'/'+date).then(cb); return; }
  var attempts = 0;
  var MAX_ATTEMPTS = 20;
  function tryFetch(){
    attempts++;
    _get('/api/logger/hub/segments/'+encodeURIComponent(_hubSite)+'/'+encodeURIComponent(slug)+'/'+date).then(function(d){
      if(d && d.pending){
        if(attempts >= MAX_ATTEMPTS){
          _hubStatusMsg('Remote site not responding — check SignalScope logs on client node');
          return;
        }
        _hubStatusMsg('Fetching segments from remote site\u2026 ('+attempts+')');
        setTimeout(tryFetch, 3000);
      } else {
        _hubStatusMsg('');
        cb(d);
      }
    }).catch(function(e){ setTimeout(tryFetch, 4000); });
  }
  tryFetch();
}

function _hubStatusMsg(msg){
  var el = document.getElementById('hub-status');
  if(el) el.textContent = msg;
}

// ── Stream selector ───────────────────────────────────────────────────────
document.getElementById('stream-sel').addEventListener('change', function(){
  _currentSlug = this.value;
  // Auto-set _hubSite from catalog entry (or clear for local streams)
  var opt = this.options[this.selectedIndex];
  _hubSite = (opt && opt.dataset.site) ? opt.dataset.site : '';
  _selSeg = null; _segsAll = []; _markIn = _markOut = null;
  _stopHubAudio();
  _hubStatusMsg('');
  updateInOutLabel();
  document.getElementById('export-btn').disabled = true;
  document.getElementById('day-bar-head').classList.add('hidden');
  buildDayBar([]);
  if(_currentSlug) loadDays();
  else buildTimeline([]);
});

function loadStreams(){
  _get('/api/logger/streams').then(function(data){
    _streams = data;
    var sel = document.getElementById('stream-sel');
    sel.innerHTML = '<option value="">— select stream —</option>';
    data.forEach(function(s){
      var o = document.createElement('option');
      o.value = s.slug;
      o.textContent = s.name + (s.catalog ? ' (' + (s.owner||'remote') + ')' : '');
      sel.appendChild(o);
    });
    _get('/api/logger/config').then(function(cfg){
      _cfg = cfg;
      // Auto-select first enabled stream, or first stream
      var enabled = data.filter(function(s){ return (cfg.streams||{})[s.name] && cfg.streams[s.name].enabled; });
      var pick = enabled.length ? enabled[0] : (data.length ? data[0] : null);
      if(pick){
        sel.value = pick.slug;
        _currentSlug = pick.slug;
        loadDays();
      }
      // Show notice if nothing enabled
      var anyEnabled = Object.values(cfg.streams||{}).some(function(s){ return s.enabled; });
      document.getElementById('tl-notice').classList.toggle('hidden', anyEnabled || data.length===0);
    });
  });
}

// ── Date nav ──────────────────────────────────────────────────────────────
document.getElementById('btn-prev-day').addEventListener('click', function(){ shiftDay(-1); });
document.getElementById('btn-next-day').addEventListener('click', function(){ shiftDay(1); });

function shiftDay(delta){
  if(!_currentDate) return;
  var d = new Date(_currentDate+'T00:00:00Z');
  d.setUTCDate(d.getUTCDate()+delta);
  _currentDate = d.toISOString().slice(0,10);
  document.getElementById('date-lbl').textContent = _currentDate;
  loadSegments();
}

function loadDays(){
  apiDays(_currentSlug, function(days){
    var el = document.getElementById('day-list');
    el.innerHTML = '';
    if(!days.length){ el.innerHTML='<span style="color:var(--mu);font-size:12px;padding:4px 6px">No recordings yet</span>'; }
    days.slice(0,20).forEach(function(d){
      var btn = document.createElement('button');
      btn.className = 'day-btn' + (d===_currentDate?' active':'');
      btn.textContent = d;
      btn.addEventListener('click', function(){
        _currentDate=d;
        document.getElementById('date-lbl').textContent=d;
        el.querySelectorAll('.day-btn').forEach(function(b){ b.classList.remove('active'); });
        btn.classList.add('active');
        loadSegments();
      });
      el.appendChild(btn);
    });
    loadSegments();
  });
}

// ── Timeline ──────────────────────────────────────────────────────────────
function loadSegments(){
  if(!_currentSlug||!_currentDate){ buildTimeline([]); _applyMeta([]); return; }
  document.getElementById('tl-title').textContent = _currentDate;
  _segsAll = []; _markIn = _markOut = null; _metaEvents = [];
  updateInOutLabel();
  document.getElementById('export-btn').disabled = true;
  document.getElementById('day-bar-head').classList.add('hidden');
  buildDayBar([]);
  document.getElementById('show-band').innerHTML = '';
  document.getElementById('mic-band').innerHTML = '';
  document.getElementById('track-band').innerHTML = '';
  apiSegments(_currentSlug, _currentDate, function(segs){
    buildTimeline(segs || []);
    loadMeta(_currentSlug, _currentDate);
  });
}

function loadMeta(slug, date){
  if(_hubSite){
    var attempts = 0;
    function tryMetaFetch(){
      attempts++;
      _get('/api/logger/hub/metadata/'+encodeURIComponent(_hubSite)+'/'+encodeURIComponent(slug)+'/'+date).then(function(d){
        if(d && d.pending){
          if(attempts >= 10){ _applyMeta([]); return; }
          setTimeout(tryMetaFetch, 3000);
        } else {
          _applyMeta(Array.isArray(d) ? d : []);
        }
      }).catch(function(){ _applyMeta([]); });
    }
    tryMetaFetch();
    return;
  }
  _get('/api/logger/metadata/'+encodeURIComponent(slug)+'/'+date)
    .then(function(events){ _applyMeta(Array.isArray(events) ? events : []); })
    .catch(function(){ _applyMeta([]); });
}

function _applyMeta(events){
  _metaEvents = events;
  // Apply track colours and timestamped tooltips to timeline blocks
  document.querySelectorAll('.tl-block').forEach(function(blk){
    var ss = parseInt(blk.dataset.startS || -1, 10);
    if(isNaN(ss) || ss < 0) return;
    var end = ss + _SEG_SECS;
    var tracks = events.filter(function(e){
      return e.type === 'track' && e.ts_s >= ss && e.ts_s < end;
    });
    if(tracks.length){
      blk.classList.add('has-track');
      var trackLines = tracks.map(function(t){
        var th = Math.floor(t.ts_s / 3600);
        var tm = Math.floor((t.ts_s % 3600) / 60);
        var ts = Math.floor(t.ts_s % 60);
        var tstr = ('0'+th).slice(-2)+':'+('0'+tm).slice(-2)+':'+('0'+ts).slice(-2);
        return '\U0001F3B5 '+tstr+' '+(t.artist ? t.artist+' \u2014 ' : '')+t.title;
      }).join(' | ');
      blk.dataset.tip = (blk.dataset.tip || '') + ' \u00b7 ' + trackLines;
    } else {
      blk.classList.remove('has-track');
    }
  });
  _renderShowBand(events);
  _renderMicBand(events);
  _renderTrackBand(events);
}

function _renderShowBand(events){
  var band = document.getElementById('show-band');
  if(!band) return;
  band.innerHTML = '';
  var shows = events.filter(function(e){ return e.type === 'show' && e.show_name; });
  if(!shows.length) return;
  // Merge consecutive entries with the same show_name + presenter into one block
  // (the metadata API may re-emit the same show name on every poll cycle)
  var merged = [];
  for(var i = 0; i < shows.length; i++){
    var key = shows[i].show_name + '\x00' + (shows[i].presenter || '');
    if(merged.length && merged[merged.length - 1]._key === key) continue;
    merged.push({ts_s: shows[i].ts_s, show_name: shows[i].show_name,
                 presenter: shows[i].presenter || '', _key: key});
  }
  for(var i = 0; i < merged.length; i++){
    var start = merged[i].ts_s;
    var end   = (i + 1 < merged.length) ? merged[i + 1].ts_s : 86400;
    var w = (end - start) / 86400 * 100;
    var l = start / 86400 * 100;
    if(w < 0.05) continue;
    var sp = document.createElement('div');
    sp.className = 'show-span';
    sp.style.left  = l + '%';
    sp.style.width = w + '%';
    var label = merged[i].show_name;
    if(merged[i].presenter) label += ' \u00b7 ' + merged[i].presenter;
    sp.textContent = label;
    sp.title = label;
    band.appendChild(sp);
  }
}

function _renderMicBand(events){
  var band = document.getElementById('mic-band');
  if(!band) return;
  band.innerHTML = '';
  var mics = events.filter(function(e){ return e.type==='mic_on' || e.type==='mic_off'; });
  if(!mics.length) return;
  var onStart = null, onLabel = '';
  function _addSpan(start, end, label, live){
    var w = (end - start) / 86400 * 100;
    var l = start / 86400 * 100;
    if(w < 0.01) return;
    var th = Math.floor(start / 3600);
    var tm = Math.floor((start % 3600) / 60);
    var ts = Math.floor(start % 60);
    var tstr = ('0'+th).slice(-2)+':'+('0'+tm).slice(-2)+':'+('0'+ts).slice(-2);
    var sp = document.createElement('div');
    sp.className = 'mic-span'+(live?' mic-live':'');
    sp.style.left  = l + '%';
    sp.style.width = w + '%';
    sp.title = tstr + ' \u2014 ' + (label ? label + ' on air' : 'Mic on air');
    band.appendChild(sp);
  }
  for(var i = 0; i < mics.length; i++){
    if(mics[i].type === 'mic_on'){
      if(onStart === null){ onStart = mics[i].ts_s; onLabel = mics[i].title || ''; }
    } else {
      if(onStart !== null){ _addSpan(onStart, mics[i].ts_s, onLabel, false); onStart = null; onLabel = ''; }
    }
  }
  if(onStart !== null) _addSpan(onStart, 86400, onLabel, true);  // still live
}

function _renderTrackBand(events){
  var band = document.getElementById('track-band');
  if(!band) return;
  band.innerHTML = '';
  var tracks = events.filter(function(e){ return e.type === 'track' && e.title; });
  if(!tracks.length) return;
  for(var i = 0; i < tracks.length; i++){
    var start = tracks[i].ts_s;
    var end   = (i + 1 < tracks.length) ? tracks[i + 1].ts_s : 86400;
    var w = (end - start) / 86400 * 100;
    var l = start / 86400 * 100;
    if(w < 0.02) continue;
    var th = Math.floor(start / 3600);
    var tm = Math.floor((start % 3600) / 60);
    var ts = Math.floor(start % 60);
    var tstr = ('0'+th).slice(-2)+':'+('0'+tm).slice(-2)+':'+('0'+ts).slice(-2);
    var sp = document.createElement('div');
    sp.className = 'track-span';
    sp.style.left  = l + '%';
    sp.style.width = w + '%';
    var label = (tracks[i].artist ? tracks[i].artist + ' \u2014 ' : '') + tracks[i].title;
    sp.textContent = label;
    sp.title = tstr + ' \u2014 ' + label;
    band.appendChild(sp);
  }
}

function buildTimeline(segs){
  _segsAll = segs;
  var grid = document.getElementById('tl-grid');
  grid.innerHTML = '';
  var lookup = {};
  segs.forEach(function(s){ lookup[s.start_s] = s; });
  var now = new Date();
  var todayStr = now.toISOString().slice(0,10);
  var nowSecs  = now.getUTCHours()*3600 + now.getUTCMinutes()*60;

  for(var h=0; h<24; h++){
    var lbl = document.createElement('div');
    lbl.className = 'tl-hour-lbl';
    lbl.textContent = String(h).padStart(2,'0')+':00';
    grid.appendChild(lbl);
    var row = document.createElement('div');
    row.className = 'tl-row';
    for(var m=0; m<60; m+=5){
      var ss = h*3600 + m*60;
      var blk = document.createElement('div');
      blk.className = 'tl-block';
      blk.dataset.startS = String(ss);
      var seg = lookup[ss];
      var future = (_currentDate===todayStr && ss>nowSecs);
      if(future){
        blk.dataset.status='future';
        blk.dataset.tip=String(h).padStart(2,'0')+':'+String(m).padStart(2,'0')+' — future';
      } else if(seg){
        var sp = seg.silence_pct||0;
        blk.dataset.status = sp>80?'silent':sp>10?'warn':'ok';
        blk.dataset.tip = String(h).padStart(2,'0')+':'+String(m).padStart(2,'0')
          +' · '+seg.quality+' · '+sp.toFixed(0)+'% silence';
        (function(s,b){ b.addEventListener('click', function(){ playSeg(s,b); }); })(seg,blk);
      } else {
        blk.dataset.status='none';
        blk.dataset.tip=String(h).padStart(2,'0')+':'+String(m).padStart(2,'0')+' — no recording';
      }
      row.appendChild(blk);
    }
    grid.appendChild(row);
  }
  buildDayBar(segs);
}

function playSeg(seg, blkEl){
  _loadSegAndSeek(seg, 0);
  updateDayBarMarkers();
}

// ── Hub mode playback ─────────────────────────────────────────────────────
// _playGen increments on every startHubPlay call. The POST .then() checks
// that the generation still matches before acting — stale responses from
// superseded clicks are silently discarded, preventing duplicate streams.
var _playGen = 0;

function startHubPlay(seg, offset){
  // Cancel the current reader and stop all scheduled buffers immediately so
  // the old stream's pre-buffered audio does not overlap with the new stream.
  if(_pcmReader){try{_pcmReader.cancel();}catch(e){}_pcmReader=null;}
  _pcmBuf=new Uint8Array(0);
  _killActiveSrcs();
  _hubIsPlaying   = false;
  _hubPlayPending = true;
  // Immediate visual feedback — show loading state before the POST even returns
  document.getElementById('play-btn').textContent='⏳';
  document.getElementById('play-btn').disabled=true;
  var gen = ++_playGen;   // capture generation for this click
  var seekSecs = offset;
  _post('/api/logger/hub/play', {
    site: _hubSite, slug: _currentSlug, date: _currentDate,
    filename: seg.filename, seek_s: seekSecs
  }).then(function(r){
    if(gen !== _playGen){
      // Superseded by a later click — discard but re-enable button if nothing else pending
      if(!_hubIsPlaying && !_hubPlayPending) { document.getElementById('play-btn').disabled=false; }
      return;
    }
    _hubPlayPending = false;
    document.getElementById('play-btn').disabled=false;
    if(!r || !r.ok){ document.getElementById('play-btn').textContent='▶'; return; }
    connectAudio(r.stream_url);
    _selSeg = seg;
    _hubIsPlaying  = true;
    _hubPlayStart  = Date.now();
    _hubPlayOffset = seg.start_s + seekSecs;
    var h=Math.floor(seg.start_s/3600),m=Math.floor((seg.start_s%3600)/60);
    document.getElementById('p-title').textContent=String(h).padStart(2,'0')+':'+String(m).padStart(2,'0');
    document.getElementById('p-sub').textContent=_hubSite+' · '+_currentSlug;
    _updateGridPlaying(seg.start_s);
    document.getElementById('play-btn').textContent='⏸';
  }).catch(function(){
    if(gen===_playGen){ _hubPlayPending=false; document.getElementById('play-btn').disabled=false; document.getElementById('play-btn').textContent='▶'; }
  });
}

// ── Segment loading & navigation ──────────────────────────────────────────
function _loadSegAndSeek(seg, offset){
  _selSeg=seg;
  var h=Math.floor(seg.start_s/3600),m=Math.floor((seg.start_s%3600)/60);
  var spct=(seg.silence_pct||0).toFixed(1);
  document.getElementById('seg-info-text').innerHTML='<b>'+_esc(seg.filename)+'</b><br>Silence: '+spct+'% · '+(seg.quality||'?')+' quality';
  document.getElementById('seg-info').style.display='block';

  if(_hubSite){
    startHubPlay(seg, offset);
    return;
  }

  // Local mode
  var a=document.getElementById('audio-el');
  var url='/api/logger/audio/'+_currentSlug+'/'+_currentDate+'/'+seg.filename;
  document.getElementById('p-title').textContent=String(h).padStart(2,'0')+':'+String(m).padStart(2,'0');
  document.getElementById('p-sub').textContent=_currentDate+' · '+_currentSlug;
  _updateGridPlaying(seg.start_s);
  if(a.getAttribute('data-src')!==url){
    a.setAttribute('data-src',url);
    a.src=url; a.load();
    var _oncp=function(){ a.currentTime=Math.max(0,offset); a.play().catch(function(){}); a.removeEventListener('canplay',_oncp); };
    a.addEventListener('canplay',_oncp);
  } else {
    a.currentTime=Math.max(0,offset);
    if(a.paused) a.play().catch(function(){});
  }
}

function _updateGridPlaying(startS){
  document.querySelectorAll('.tl-block').forEach(function(b){ b.classList.remove('selected','playing'); });
  var blk=document.querySelector('.tl-block[data-start-s="'+startS+'"]');
  if(blk) blk.classList.add('selected','playing');
}

function _playNext(){
  if(!_selSeg||!_segsAll.length) return;
  var next=null, ns=_selSeg.start_s+_SEG_SECS;
  for(var i=0;i<_segsAll.length;i++){ if(_segsAll[i].start_s===ns){ next=_segsAll[i]; break; } }
  if(next){ _loadSegAndSeek(next,0); } else { _hubIsPlaying=false; document.getElementById('play-btn').textContent='▶'; _updateGridPlaying(-1); }
}

function _seekToSecs(secs){
  if(!_segsAll.length) return;
  secs=Math.max(0,Math.min(86399,secs));
  var seg=null;
  for(var i=0;i<_segsAll.length;i++){
    if(_segsAll[i].start_s<=secs && secs<_segsAll[i].start_s+_SEG_SECS){ seg=_segsAll[i]; break; }
  }
  if(!seg) return;
  var offset=secs-seg.start_s;
  _loadSegAndSeek(seg, offset);
}

function buildDayBar(segs){
  var bg=document.getElementById('day-bar-bg');
  bg.innerHTML='';
  var lookup={};
  segs.forEach(function(s){ lookup[s.start_s]=s; });
  for(var i=0;i<288;i++){
    var ss=i*300;
    var d=document.createElement('div');
    d.className='day-bar-blk';
    var seg=lookup[ss];
    if(seg){
      var sp=seg.silence_pct||0;
      d.style.background=sp>80?'#7f1d1d':sp>10?'#78350f':'#166534';
    } else {
      d.style.background='#0e2040';
    }
    bg.appendChild(d);
  }
  updateDayBarMarkers();
}

function updateDayBarMarkers(){
  var inE=document.getElementById('day-bar-in'),outE=document.getElementById('day-bar-out'),rngE=document.getElementById('day-bar-range');
  if(_markIn!==null){ inE.style.left=(_markIn/86400*100)+'%'; inE.classList.remove('hidden'); } else { inE.classList.add('hidden'); }
  if(_markOut!==null){ outE.style.left=(_markOut/86400*100)+'%'; outE.classList.remove('hidden'); } else { outE.classList.add('hidden'); }
  if(_markIn!==null&&_markOut!==null){ rngE.style.left=(_markIn/86400*100)+'%'; rngE.style.width=((_markOut-_markIn)/86400*100)+'%'; rngE.classList.remove('hidden'); } else { rngE.classList.add('hidden'); }
  document.querySelectorAll('.tl-block[data-start-s]').forEach(function(b){
    var ss=parseInt(b.dataset.startS||-1);
    b.classList.toggle('in-range', _markIn!==null&&_markOut!==null&&ss+_SEG_SECS>_markIn&&ss<_markOut);
  });
}

// ── Player ────────────────────────────────────────────────────────────────
function setupAudio(){
  var a = document.getElementById('audio-el');
  a.addEventListener('timeupdate', function(){
    if(_hubSite) return;   // hub mode uses setInterval instead
    var cur=a.currentTime, dur=a.duration||0;
    if(_selSeg){
      var wallPos=_selSeg.start_s+(a.currentTime||0);
      document.getElementById('time-lbl').textContent=_fmtWall(wallPos);
      var dbh=document.getElementById('day-bar-head');
      dbh.style.left=(wallPos/86400*100)+'%'; dbh.classList.remove('hidden');
    }
    var pct = dur>0 ? cur/dur*100 : 0;
    document.getElementById('scrub-fill').style.width = pct+'%';
    document.getElementById('scrub-thumb').style.left  = pct+'%';
    updateScrubMarkers();
  });
  a.addEventListener('ended', function(){
    if(_hubSite) return;
    _playNext();
  });
  a.addEventListener('pause', function(){ if(!_hubSite) document.getElementById('play-btn').textContent='▶'; });
  a.addEventListener('play',  function(){ if(!_hubSite) document.getElementById('play-btn').textContent='⏸'; });
}

document.getElementById('play-btn').addEventListener('click', function(){
  if(_hubSite){
    if(_hubIsPlaying || _hubPlayPending){
      // Pause: mute immediately and cancel the stream
      _stopHubAudio();
    } else if(_selSeg){
      // Resume: restart from the position we stopped at
      var resumeOffset = Math.max(0, _hubPlayOffset - _selSeg.start_s);
      startHubPlay(_selSeg, resumeOffset);
    }
    return;
  }
  var a=document.getElementById('audio-el');
  if(!a.src||a.src===location.href) return;
  if(a.paused) a.play(); else a.pause();
});

// ── Scrub ─────────────────────────────────────────────────────────────────
var scrubEl = document.getElementById('scrub');
scrubEl.addEventListener('click', function(e){ if(!_scrubDrag) seekTo(e); });
scrubEl.addEventListener('mousedown', function(e){
  if(e.button!==0) return;
  _scrubDrag=true; seekTo(e);
  function mv(ev){ if(_scrubDrag) seekTo(ev); }
  function up(){ _scrubDrag=false; document.removeEventListener('mousemove',mv); document.removeEventListener('mouseup',up); }
  document.addEventListener('mousemove',mv); document.addEventListener('mouseup',up);
  e.preventDefault();
});

// ── Spacebar play/pause (prevent page scroll) ─────────────────────────────
document.addEventListener('keydown', function(e){
  if(e.code !== 'Space') return;
  var tag = (document.activeElement||{}).tagName||'';
  if(tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
  e.preventDefault();
  document.getElementById('play-btn').click();
});

function seekTo(e){
  var rect = document.querySelector('.scrub-track').getBoundingClientRect();
  var pct  = Math.max(0,Math.min(1,(e.clientX-rect.left)/rect.width));
  var a    = document.getElementById('audio-el');
  if(a.duration) a.currentTime = pct*a.duration;
}

// ── Day bar seek ──────────────────────────────────────────────────────────
function _dayBarSeek(e){
  var rect=document.getElementById('day-bar').getBoundingClientRect();
  var pct=Math.max(0,Math.min(1,(e.clientX-rect.left)/rect.width));
  var secs=Math.floor(pct*86400);
  _seekToSecs(secs);
}
document.getElementById('day-bar').addEventListener('click',function(e){ _dayBarSeek(e); });
document.getElementById('day-bar').addEventListener('mousemove',function(e){
  var rect=this.getBoundingClientRect();
  var pct=Math.max(0,Math.min(1,(e.clientX-rect.left)/rect.width));
  var hv=document.getElementById('day-bar-hover');
  hv.style.left=(pct*100)+'%'; hv.style.display='block';
});
document.getElementById('day-bar').addEventListener('mouseleave',function(){
  document.getElementById('day-bar-hover').style.display='none';
});

// ── Right-click on timeline overview: set mark-in / mark-out ─────────────
// Attached to document so Safari honours preventDefault reliably
document.addEventListener('contextmenu', function(e){
  var wrap = document.querySelector('.tl-scroll-wrap');
  if(!wrap) return;
  // Must be inside the scroll wrap (day-bar included)
  if(!e.target.closest('.tl-scroll-wrap')) return;
  e.preventDefault();
  var content = document.getElementById('tl-zoom-content');
  if(!content) return;
  var rect = content.getBoundingClientRect();
  var pct  = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
  var secs = pct * 86400;
  // Cycle: no-in → set-in | in-only → set-out | both set → new-in
  if(_markIn === null || _markOut !== null){
    _markIn  = secs;
    _markOut = null;
  } else {
    // mark-in is set, mark-out is not — place out (or move in if clicked before it)
    if(secs <= _markIn){ _markIn = secs; }
    else { _markOut = secs; }
  }
  updateInOutLabel(); updateScrubMarkers(); updateDayBarMarkers();
  document.getElementById('export-btn').disabled =
    (_markIn === null || _markOut === null || !!_hubSite);
});

// ── Mark in/out ───────────────────────────────────────────────────────────
function _currentPlayPos(){
  if(_hubSite && _hubPlayStart && _selSeg){
    return _hubPlayOffset + (Date.now() - _hubPlayStart) / 1000;
  }
  var a=document.getElementById('audio-el');
  return _selSeg ? _selSeg.start_s + (a.currentTime||0) : 0;
}

document.getElementById('mark-in-btn').addEventListener('click', function(){
  if(!_selSeg) return;
  _markIn = _currentPlayPos();
  if(_markOut!==null && _markOut<=_markIn) _markOut=null;
  updateInOutLabel(); updateScrubMarkers(); updateDayBarMarkers();
  document.getElementById('export-btn').disabled = (_markIn===null||_markOut===null||!!_hubSite);
});

document.getElementById('mark-out-btn').addEventListener('click', function(){
  if(!_selSeg) return;
  _markOut = _currentPlayPos();
  if(_markIn!==null && _markOut<=_markIn) _markIn=null;
  updateInOutLabel(); updateScrubMarkers(); updateDayBarMarkers();
  document.getElementById('export-btn').disabled = (_markIn===null||_markOut===null||!!_hubSite);
});

function updateInOutLabel(){
  var parts=[];
  if(_markIn!==null)  parts.push('In: '+_fmtWall(_markIn));
  if(_markOut!==null) parts.push('Out: '+_fmtWall(_markOut));
  if(_markIn!==null&&_markOut!==null) parts.push('Dur: '+_fmt(_markOut-_markIn));
  document.getElementById('inout-lbl').textContent = parts.join('  ·  ');
}

function updateScrubMarkers(){
  if(!_selSeg) return;
  var dur=_hubSite ? _SEG_SECS : (document.getElementById('audio-el').duration||_SEG_SECS);
  var ss=_selSeg.start_s;
  var iE=document.getElementById('scrub-in'), oE=document.getElementById('scrub-out');
  if(_markIn!==null&&_markIn>=ss&&_markIn<ss+dur){ iE.style.left=((_markIn-ss)/dur*100)+'%'; iE.classList.remove('hidden'); } else { iE.classList.add('hidden'); }
  if(_markOut!==null&&_markOut>=ss&&_markOut<=ss+dur){ oE.style.left=((_markOut-ss)/dur*100)+'%'; oE.classList.remove('hidden'); } else { oE.classList.add('hidden'); }
}

// ── Export ────────────────────────────────────────────────────────────────
document.getElementById('export-btn').addEventListener('click', function(){
  if(_markIn===null||_markOut===null||!_currentSlug||!_currentDate) return;
  var btn=this; btn.disabled=true; btn.textContent='⏳ Exporting…';
  var fmt=(document.getElementById('export-fmt')||{}).value||'mp3';
  var extMap={mp3:'.mp3',aac:'.m4a',opus:'.webm'};
  var ext=extMap[fmt]||'.mp3';
  fetch('/api/logger/export',{method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRFToken':_csrf()},
    body:JSON.stringify({stream:_currentSlug,date:_currentDate,start_s:_markIn,end_s:_markOut,fmt:fmt})
  }).then(function(r){
    if(!r.ok){ return r.json().then(function(d){ alert('Export failed: '+(d.error||r.status)); }); }
    return r.blob().then(function(blob){
      var a=document.createElement('a');
      a.href=URL.createObjectURL(blob);
      a.download=_currentSlug+'_'+_currentDate+'_clip'+ext;
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
    });
  }).finally(function(){ btn.disabled=false; btn.textContent='⬇ Export Clip'; });
});

// ── Status ────────────────────────────────────────────────────────────────
function loadStatus(){
  _get('/api/logger/status').then(function(data){
    var recs      = data.recorders||{};
    var recList   = Object.values(recs);
    var running   = recList.filter(function(r){ return r.running; });
    var errored   = running.filter(function(r){ return r.last_error; });
    var healthy   = running.filter(function(r){ return !r.last_error && r.seg_count > 0; });
    var badge     = document.getElementById('rec-status');
    if(errored.length && !healthy.length){
      badge.className   = 'badge b-err';
      badge.textContent = '⚠ Error ('+errored.length+')';
    } else if(running.length){
      badge.className   = 'badge b-rec';
      badge.textContent = '● REC ('+running.length+')';
    } else {
      badge.className   = 'badge b-idle';
      badge.textContent = 'Idle';
    }
    // Show per-recorder error messages under the header
    var errBox = document.getElementById('hdr-errors');
    if(errBox){
      errBox.innerHTML = '';
      errored.forEach(function(r){
        var d=document.createElement('div'); d.className='err-msg';
        d.textContent = r.stream+': '+r.last_error;
        errBox.appendChild(d);
      });
    }
    var di = document.getElementById('disk-info');
    if(di) di.textContent = 'Disk: '+_fmtBytes(data.disk_bytes||0)
      +(running.length ? ' · '+running.length+' recording' : ' · idle')
      +(errored.length ? ' · '+errored.length+' error(s)' : '');
  });
}

// ── Settings ──────────────────────────────────────────────────────────────
function loadSettingsPanel(){
  Promise.all([_get('/api/logger/streams'), _get('/api/logger/config'), _get('/api/logger/status')])
  .then(function(res){
    _streams=res[0]; _cfg=res[1];
    if(!_cfg.base_dirs) _cfg.base_dirs = [];
    renderBaseDirs();
    renderSettingsRows();
    var st = res[2];
    document.getElementById('disk-info').textContent =
      'Disk usage: '+_fmtBytes(st.disk_bytes||0);
    var rdInp = document.getElementById('rec-dir-input');
    rdInp.value = _cfg.rec_dir || '';
    var rdRes = document.getElementById('rec-dir-resolved');
    rdRes.textContent = st.rec_root ? '→ ' + st.rec_root : '';
    var mkInput = document.getElementById('mic-api-key-input');
    if(mkInput) mkInput.value = _cfg.mic_api_key || '';
    // Fetch Planet Radio stations — non-critical, re-render rows with dropdown if available
    _get('/api/nowplaying_stations').then(function(stations){
      if(Array.isArray(stations) && stations.length){
        _planetStations = stations;
        renderSettingsRows();
      }
    }).catch(function(){});
  }).catch(function(err){
    document.getElementById('disk-info').textContent = '⚠ Settings failed to load — check server logs';
    console.error('loadSettingsPanel failed:', err);
  });
}

function renderBaseDirs(){
  var el = document.getElementById('basedir-rows');
  el.innerHTML = '';
  var dirs = _cfg.base_dirs || [];
  if(!dirs.length){
    el.innerHTML = '<div style="color:var(--mu);font-size:12px;padding:4px 0">No named directories yet. Click "+ Add Directory" to create one.</div>';
    return;
  }
  dirs.forEach(function(bd, idx){
    var row = document.createElement('div');
    row.style.cssText = 'display:flex;gap:8px;align-items:center;margin-bottom:6px';
    row.innerHTML =
      '<input type="text" data-bd-idx="'+idx+'" data-bd-key="name" value="'+_esc(bd.name||'')+'" placeholder="Name (e.g. NAS)"'
      +' style="flex:0 0 140px;background:#173a69;border:1px solid var(--bor);color:var(--tx);padding:6px 8px;border-radius:6px;font-size:12px;outline:none">'
      +'<input type="text" data-bd-idx="'+idx+'" data-bd-key="path" value="'+_esc(bd.path||'')+'" placeholder="/mnt/recordings"'
      +' style="flex:1;background:#173a69;border:1px solid var(--bor);color:var(--tx);padding:6px 8px;border-radius:6px;font-size:12px;font-family:monospace;outline:none">'
      +'<button class="btn bw bs bd-rm-btn" data-bd-idx="'+idx+'" style="padding:5px 10px;font-size:12px;flex-shrink:0">✕</button>';
    el.appendChild(row);
  });
}

function _getBaseDirsFromForm(){
  var dirs = [];
  document.querySelectorAll('[data-bd-idx]').forEach(function(inp){
    var idx = parseInt(inp.dataset.bdIdx, 10);
    if(!dirs[idx]) dirs[idx] = {};
    dirs[idx][inp.dataset.bdKey] = inp.value.trim();
  });
  return dirs.filter(function(d){ return d && d.name && d.path; });
}

document.getElementById('add-basedir-btn').addEventListener('click', function(){
  if(!_cfg.base_dirs) _cfg.base_dirs = [];
  _cfg.base_dirs.push({name:'', path:''});
  renderBaseDirs();
});

document.getElementById('basedir-rows').addEventListener('click', function(e){
  var btn = e.target.closest('.bd-rm-btn');
  if(!btn) return;
  var idx = parseInt(btn.dataset.bdIdx, 10);
  var dirs = _getBaseDirsFromForm();
  // also include rows being edited that may have empty name/path
  document.querySelectorAll('[data-bd-idx]').forEach(function(inp){
    var i = parseInt(inp.dataset.bdIdx, 10);
    if(!dirs[i]) dirs[i] = {};
    dirs[i][inp.dataset.bdKey] = inp.value.trim();
  });
  // Filter out the removed index
  var all = [];
  document.querySelectorAll('[data-bd-key="name"]').forEach(function(inp,i){
    if(i !== idx) all.push({name: inp.value.trim(), path: document.querySelectorAll('[data-bd-key="path"]')[i].value.trim()});
  });
  _cfg.base_dirs = all;
  renderBaseDirs();
  renderSettingsRows();
});

function renderSettingsRows(){
  var el=document.getElementById('settings-rows');
  el.innerHTML='';
  if(!_streams.length){
    el.innerHTML='<p style="color:var(--mu)">No input streams configured in SignalScope.</p>';
    return;
  }
  var dirs = _getBaseDirsFromForm();
  _streams.filter(function(s){ return !s.catalog; }).forEach(function(s){
    var sc=(_cfg.streams||{})[s.name]||{};
    var card=document.createElement('div'); card.className='scard';
    var chkd = sc.enabled ? ' checked' : '';
    var eid  = 'lbl-en-'+s.name.replace(/[^a-z0-9]/gi,'_');
    // Base directory dropdown options
    var bdOpts = '<option value=""'+((!sc.base_dir||sc.base_dir==='')?' selected':'')+'>Default</option>';
    dirs.forEach(function(bd){
      if(bd.name) bdOpts += '<option value="'+_esc(bd.name)+'"'+(sc.base_dir===bd.name?' selected':'')+'>'+_esc(bd.name)+'</option>';
    });
    // Planet Radio station dropdown
    var curUrl = sc.nowplaying_url || '';
    var planetMatch = curUrl.match(/\/api\/nowplaying\/([A-Za-z0-9_]+)$/);
    var selectedRpuid = planetMatch ? planetMatch[1] : '';
    var planetSelHtml = '';
    if(_planetStations.length){
      var opts = '<option value="">— Custom URL —</option>';
      _planetStations.forEach(function(ps){
        opts += '<option value="'+_esc(ps.rpuid)+'"'+(selectedRpuid===ps.rpuid?' selected':'')+'>'+_esc(ps.name)+'</option>';
      });
      planetSelHtml = '<div style="margin-bottom:8px">'
        +'<label style="font-size:11px;color:var(--mu);font-weight:600;letter-spacing:.03em;text-transform:uppercase;display:block;margin-bottom:5px">Planet Radio Station</label>'
        +'<select data-stream="'+_esc(s.name)+'" data-planet-sel'
        +' style="width:100%;background:#173a69;border:1px solid var(--bor);color:var(--tx);padding:7px 9px;border-radius:6px;font-size:12px;outline:none">'
        +opts+'</select>'
        +'</div>';
    }
    card.innerHTML =
      '<div class="scard-hdr">'
      +'<div><div class="name">'+_esc(s.name)+'</div><div class="url">'+_esc(s.url||'')+'</div></div>'
      +'<div class="tog-wrap">'
      +'<label class="tog"><input type="checkbox" data-stream="'+_esc(s.name)+'" data-key="enabled"'+chkd+'>'
      +'<span class="tog-sl"></span></label>'
      +'<span class="tog-lbl'+(sc.enabled?' on':'')+'" id="'+eid+'">'+(sc.enabled?'Recording':'Off')+'</span>'
      +'</div></div>'
      +'<div class="scard-body"><div class="scard-fields">'
      +'<div><label>Format</label><select data-stream="'+_esc(s.name)+'" data-key="rec_format">'
      +[['mp3','MP3'],['aac','AAC'],['opus','Opus']].map(function(f){ return '<option value="'+f[0]+'"'+((sc.rec_format||'mp3')===f[0]?' selected':'')+'>'+f[1]+'</option>'; }).join('')
      +'</select></div>'
      +'<div><label>HQ Bitrate</label><select data-stream="'+_esc(s.name)+'" data-key="hq_bitrate">'
      +['64k','96k','128k','192k','256k','320k'].map(function(b){ return '<option'+(( sc.hq_bitrate||'128k')===b?' selected':'')+'>'+b+'</option>'; }).join('')
      +'</select></div>'
      +'<div><label>LQ Bitrate</label><select data-stream="'+_esc(s.name)+'" data-key="lq_bitrate">'
      +['32k','48k','64k','96k'].map(function(b){ return '<option'+((sc.lq_bitrate||'48k')===b?' selected':'')+'>'+b+'</option>'; }).join('')
      +'</select></div>'
      +'<div><label>LQ after (days)</label><input type="number" min="1" max="3650" data-stream="'+_esc(s.name)+'" data-key="lq_after_days" value="'+(sc.lq_after_days||30)+'"></div>'
      +'<div><label>Delete after (days)</label><input type="number" min="1" max="3650" data-stream="'+_esc(s.name)+'" data-key="retain_days" value="'+(sc.retain_days||90)+'"></div>'
      +'<div><label>Base Directory</label><select data-stream="'+_esc(s.name)+'" data-key="base_dir">'+bdOpts+'</select></div>'
      +'</div>'   // close scard-fields
      +'<div class="np-url-row">'
      +planetSelHtml
      +'<label style="font-size:11px;color:var(--mu);font-weight:600;letter-spacing:.03em;text-transform:uppercase;display:block;margin-bottom:5px">Now Playing URL</label>'
      +'<input type="text" data-stream="'+_esc(s.name)+'" data-key="nowplaying_url" value="'+_esc(sc.nowplaying_url||'')+'"'
      +' placeholder="https://api.example.com/now-playing?station=… (leave blank to use DLS/RDS)"'
      +' style="width:100%;background:#173a69;border:1px solid var(--bor);color:var(--tx);padding:7px 9px;border-radius:6px;font-size:12px;font-family:monospace;outline:none">'
      +'<div style="font-size:11px;color:var(--mu);margin-top:4px">Planet Radio, Triton, or any JSON endpoint returning artist/title/show. Polled every 30 s. Leave blank to fall back to DLS/RDS.</div>'
      +'</div>'
      +'</div>';
    var chk = card.querySelector('input[type=checkbox]');
    chk.addEventListener('change', function(){
      var lbl=document.getElementById(eid);
      lbl.textContent = this.checked ? 'Recording' : 'Off';
      lbl.className   = 'tog-lbl'+(this.checked?' on':'');
    });
    var planetSel = card.querySelector('[data-planet-sel]');
    if(planetSel){
      planetSel.addEventListener('change', function(){
        var rpuid = this.value;
        var urlInp = card.querySelector('[data-key="nowplaying_url"]');
        if(urlInp){
          urlInp.value = rpuid ? (window.location.origin + '/api/nowplaying/' + rpuid) : '';
        }
      });
    }
    el.appendChild(card);
  });
}

document.getElementById('save-settings-btn').addEventListener('click', function(){
  var streams={};
  document.querySelectorAll('[data-stream]').forEach(function(el){
    var name=el.dataset.stream, key=el.dataset.key;
    if(!streams[name]) streams[name]={};
    if(el.type==='checkbox') streams[name][key]=el.checked;
    else if(el.type==='number') streams[name][key]=parseInt(el.value,10)||0;
    else streams[name][key]=el.value;
  });
  var recDir    = document.getElementById('rec-dir-input').value.trim();
  var baseDirs  = _getBaseDirsFromForm();
  var micKey    = (document.getElementById('mic-api-key-input')||{}).value||'';
  var saveMsg   = document.getElementById('save-msg');
  var saveErr   = document.getElementById('save-err');
  saveMsg.style.display='none'; saveErr.style.display='none';
  _post('/api/logger/config',{streams:streams, rec_dir:recDir, base_dirs:baseDirs, mic_api_key:micKey}).then(function(r){
    if(r.ok){
      _cfg.streams=streams; _cfg.rec_dir=recDir; _cfg.base_dirs=baseDirs; _cfg.mic_api_key=micKey;
      fetch('/api/logger/status').then(function(res){ return res.json(); }).then(function(st){
        var rdRes=document.getElementById('rec-dir-resolved');
        rdRes.textContent = st.rec_root ? '→ '+st.rec_root : '';
        document.getElementById('disk-info').textContent='Disk usage: '+_fmtBytes(st.disk_bytes||0);
      });
      var anyEnabled=Object.values(streams).some(function(s){ return s.enabled; });
      document.getElementById('tl-notice').classList.toggle('hidden', anyEnabled);
      if(r.moving){
        // Moves running in the background — poll until done
        saveMsg.textContent='Moving recordings in background…'; saveMsg.style.display='inline';
        (function pollMove(){
          _get('/api/logger/move_status').then(function(ms){
            if(ms.active){
              var pct = ms.total>0 ? Math.round(ms.done/ms.total*100) : 0;
              var cur = ms.current ? ' · '+ms.current : '';
              saveMsg.textContent='Moving recordings… '+ms.done+'/'+ms.total+cur+' ('+pct+'%)';
              setTimeout(pollMove, 1500);
            } else {
              var errTxt = ms.error ? ' (warning: '+ms.error+')' : '';
              saveMsg.textContent='✓ Saved — '+r.moves+' stream'+(r.moves!==1?'s':'')+' moved'+errTxt;
              setTimeout(function(){ saveMsg.style.display='none'; }, 6000);
            }
          }).catch(function(){ setTimeout(pollMove, 3000); });
        })();
      } else {
        saveMsg.textContent='✓ Saved';
        saveMsg.style.display='inline';
        setTimeout(function(){ saveMsg.style.display='none'; },4000);
      }
    } else {
      saveMsg.style.display='none';
      var errTxt = (r && r.error) ? r.error : 'Save failed';
      saveErr.textContent = '✗ '+errTxt; saveErr.style.display='inline';
    }
  });
});

// ── Timeline zoom & expand ─────────────────────────────────────────────────
function _setTlZoom(z){
  _tlZoom = z;
  var content = document.getElementById('tl-zoom-content');
  if(content) content.style.width = (z === 1 ? '100%' : (z * 100) + '%');
  document.querySelectorAll('.zoom-btn[data-z]').forEach(function(b){
    b.classList.toggle('zact', parseInt(b.dataset.z, 10) === z);
  });
  // After zoom, scroll to keep the current playhead or selection roughly centred
  var wrap = document.querySelector('.tl-scroll-wrap');
  if(wrap && z > 1){
    // Scroll to keep the current view position proportional
    var ratio = wrap.scrollLeft / (wrap.scrollWidth || 1);
    wrap.scrollLeft = ratio * wrap.scrollWidth;
  }
}

function _setTlExp(v){
  _tlExp = v;
  var tw = document.getElementById('tl-wrap');
  if(tw) tw.classList.toggle('tl-exp', v);
  var btn = document.getElementById('exp-btn');
  if(btn) btn.classList.toggle('zact', v);
}

document.querySelector('.zoom-grp').addEventListener('click', function(e){
  var btn = e.target.closest('.zoom-btn[data-z]');
  if(!btn) return;
  _setTlZoom(parseInt(btn.dataset.z, 10));
});

document.getElementById('exp-btn').addEventListener('click', function(){
  _setTlExp(!_tlExp);
});

// ── Drag-to-pan on the zoomed overview ────────────────────────────────────
(function(){
  var wrap = document.querySelector('.tl-scroll-wrap');
  if(!wrap) return;
  var _dn = false, _startX = 0, _startScroll = 0;

  wrap.addEventListener('mousedown', function(e){
    if(e.button !== 0) return;
    // Let the day-bar keep its own scrub interaction
    if(e.target.closest('#day-bar')) return;
    _dn = true;
    _startX      = e.clientX;
    _startScroll = wrap.scrollLeft;
    wrap.classList.add('tl-panning');
    e.preventDefault();
  });

  document.addEventListener('mousemove', function(e){
    if(!_dn) return;
    wrap.scrollLeft = _startScroll - (e.clientX - _startX);
  });

  document.addEventListener('mouseup', function(){
    if(!_dn) return;
    _dn = false;
    wrap.classList.remove('tl-panning');
  });

  // Touch support
  wrap.addEventListener('touchstart', function(e){
    if(e.target.closest('#day-bar')) return;
    _dn = true;
    _startX      = e.touches[0].clientX;
    _startScroll = wrap.scrollLeft;
  }, {passive: true});

  wrap.addEventListener('touchmove', function(e){
    if(!_dn) return;
    wrap.scrollLeft = _startScroll - (e.touches[0].clientX - _startX);
  }, {passive: true});

  wrap.addEventListener('touchend', function(){ _dn = false; });
})();

})();
</script>
</body>
</html>"""
