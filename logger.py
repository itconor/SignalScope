# logger.py — SignalScope compliance logger plugin
# Drop alongside signalscope.py

SIGNALSCOPE_PLUGIN = {
    "id":      "logger",
    "label":   "Logger",
    "url":     "/hub/logger",
    "icon":    "🎙",
    "version": "1.1.0",
}

import datetime
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from flask import abort, jsonify, render_template_string, request, Response, send_file

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

# ─── Module state ─────────────────────────────────────────────────────────────
_monitor   = None
_app_dir   = None


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


# ─────────────────────────────────────────────────────────────────────────────
#  REGISTRATION
# ─────────────────────────────────────────────────────────────────────────────

def register(app, ctx):
    global _monitor, _app_dir
    _monitor  = ctx["monitor"]
    login_req = ctx["login_required"]
    csrf_dec  = ctx["csrf_protect"]
    _app_dir  = Path(__file__).parent

    _load_config()
    _init_db()
    _rec_root().mkdir(parents=True, exist_ok=True)

    threading.Thread(target=_delayed_start, daemon=True, name="LoggerInit").start()
    threading.Thread(target=_maintenance_loop, daemon=True, name="LoggerMaint").start()

    @app.route("/hub/logger")
    @login_req
    def logger_page():
        return render_template_string(_TPL)

    @app.get("/api/logger/streams")
    @login_req
    def api_logger_streams():
        return jsonify(_available_streams())

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
        with _cfg_lock:
            _cfg["streams"] = data.get("streams", _cfg.get("streams", {}))
            # Validate and store the recordings path
            new_rec_dir = str(data.get("rec_dir", "")).strip()
            if new_rec_dir:
                # Basic safety: reject obviously dangerous values
                p = Path(new_rec_dir)
                if ".." in p.parts:
                    return jsonify({"ok": False, "error": "Path must not contain .."}), 400
            _cfg["rec_dir"] = new_rec_dir
            _save_config()
        # Ensure new directory exists
        try:
            _rec_root().mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return jsonify({"ok": False, "error": f"Cannot create recordings directory: {e}"}), 400
        _reconcile_recorders()
        return jsonify({"ok": True})

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
        rec_root = _rec_root()
        total = sum(f.stat().st_size for f in rec_root.rglob("*.mp3") if f.is_file()) \
                if rec_root.exists() else 0
        return jsonify({"recorders": active, "disk_bytes": total,
                        "rec_root": str(rec_root)})

    @app.get("/api/logger/days/<stream_slug>")
    @login_req
    def api_logger_days(stream_slug):
        sdir = _rec_root() / _safe(stream_slug)
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
        if not re.match(r"^[\w\-]+\.mp3$", filename):
            abort(400)
        path = _rec_root() / _safe(stream_slug) / date / filename
        # Confirm resolved path stays within the recordings root
        try:
            _assert_within_rec_root(path)
        except ValueError:
            abort(400)
        if not path.exists():
            abort(404)
        return _serve_ranged(path, "audio/mpeg")

    @app.post("/api/logger/export")
    @login_req
    @csrf_dec
    def api_logger_export():
        data = request.get_json(force=True) or {}
        stream_slug = data.get("stream", "")
        date        = data.get("date", "")
        start_s     = float(data.get("start_s", 0))
        end_s       = float(data.get("end_s", 60))
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            return jsonify({"error": "bad date"}), 400
        if end_s <= start_s or end_s - start_s > 7200:
            return jsonify({"error": "invalid range (max 2h)"}), 400
        return _export_clip(_safe(stream_slug), date, start_s, end_s)


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

def _save_config():
    try:
        with open(_app_dir / _CONFIG_FILE, "w") as f:
            json.dump(_cfg, f, indent=2)
    except Exception as e:
        _log(f"[Logger] Config save failed: {e}")

_ALLOWED_BITRATES = {"32k", "48k", "64k", "96k", "128k", "192k", "256k", "320k"}

def _stream_cfg(stream_name):
    with _cfg_lock:
        s = _cfg.get("streams", {}).get(stream_name, {})
    hq = s.get("hq_bitrate", _DEFAULT_HQ)
    lq = s.get("lq_bitrate", _DEFAULT_LQ)
    return {
        "enabled":       bool(s.get("enabled", False)),
        "hq_bitrate":    hq if hq in _ALLOWED_BITRATES else _DEFAULT_HQ,
        "lq_bitrate":    lq if lq in _ALLOWED_BITRATES else _DEFAULT_LQ,
        "lq_after_days": int(s.get("lq_after_days", _DEFAULT_LQ_AFTER)),
        "retain_days":   int(s.get("retain_days", _DEFAULT_RETAIN)),
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
    db.commit()
    db.close()

def _get_db():
    conn = sqlite3.connect(str(_app_dir / _DB_FILE), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

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

def _get_segments(slug, date):
    day_dir = _rec_root() / slug / date
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
    # Supplement with filesystem scan
    if day_dir.exists():
        for f in sorted(day_dir.glob("*.mp3")):
            if f.name not in result:
                ss = _fname_to_secs(f.name) or 0
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

    def _record_segment(self, ffmpeg):
        scfg = _stream_cfg(self.stream_name)
        if not scfg["enabled"]:
            self.stop_evt.set()
            return

        now       = datetime.datetime.utcnow()
        seg_start = _seg_start(now)
        seg_end   = seg_start + datetime.timedelta(seconds=_SEG_SECS)
        date_str  = seg_start.strftime("%Y-%m-%d")
        time_str  = seg_start.strftime("%H-%M")
        filename  = f"{time_str}.mp3"
        start_s   = seg_start.hour * 3600 + seg_start.minute * 60

        out_dir  = _rec_root() / self.slug / date_str
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
               "-vn", "-ac", "1", "-ar", "44100",
               "-b:a", scfg["hq_bitrate"],
               "-f", "mp3", str(out_path)]

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

def _delayed_start():
    time.sleep(3)
    _reconcile_recorders()

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
    ffmpeg   = shutil.which("ffmpeg") or "ffmpeg"
    rec_root = _rec_root()
    if not rec_root.exists():
        return
    today = datetime.date.today()
    for stream_dir in rec_root.iterdir():
        if not stream_dir.is_dir():
            continue
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
            age = (today - day_date).days
            retain = scfg["retain_days"]
            lq_after = scfg["lq_after_days"]
            if retain > 0 and age >= retain:
                try:
                    _assert_within_rec_root(day_dir)
                except ValueError:
                    _log(f"[Logger] Refusing to prune path outside rec root: {day_dir}")
                    continue
                _log(f"[Logger] Pruning {day_dir.name} ({stream_dir.name})")
                shutil.rmtree(day_dir, ignore_errors=True)
            elif lq_after > 0 and age >= lq_after:
                for mp3 in day_dir.glob("*.mp3"):
                    _maybe_downgrade(mp3, stream_dir.name, day_dir.name,
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
    tmp = path.with_suffix(".lq.mp3")
    try:
        r = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error",
             "-i", str(path), "-b:a", lq_br, "-ac", "1", "-f", "mp3", str(tmp)],
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
    """Raise ValueError if path escapes the recordings root directory."""
    rec_root = (_rec_root()).resolve()
    try:
        path.resolve().relative_to(rec_root)
    except ValueError:
        raise ValueError(f"Path escapes recordings root: {path}")

def _ffmpeg_concat_escape(p: Path) -> str:
    """Escape a path for use in an ffmpeg concat list file."""
    # ffmpeg concat format: single-quote the path, escape internal single quotes
    return str(p).replace("\\", "\\\\").replace("'", "\\'")

def _export_clip(slug, date, start_s, end_s):
    day_dir = _rec_root() / slug / date
    if not day_dir.exists():
        return jsonify({"error": "no recordings"}), 404
    ffmpeg  = shutil.which("ffmpeg") or "ffmpeg"
    segs    = _get_segments(slug, date)

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

    tf_name = None
    try:
        if len(relevant) == 1:
            ss0, seg_path = relevant[0]
            ss = max(0.0, start_s - ss0)
            cmd = [ffmpeg, "-hide_banner", "-loglevel", "error",
                   "-ss", f"{ss:.3f}", "-i", str(seg_path),
                   "-t", f"{end_s - start_s:.3f}",
                   "-c", "copy", "-f", "mp3", "pipe:1"]
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
                   "-c", "copy", "-f", "mp3", "pipe:1"]

        proc = subprocess.run(cmd, capture_output=True, timeout=120)
        if proc.returncode != 0:
            return jsonify({"error": "ffmpeg failed"}), 500
        h   = int(start_s) // 3600
        m   = (int(start_s) % 3600) // 60
        dur = int(end_s - start_s)
        fname = f"{slug}_{date}_{h:02d}h{m:02d}m_{dur}s.mp3"
        return Response(proc.stdout, mimetype="audio/mpeg",
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
    m = re.match(r"^(\d{2})-(\d{2})\.mp3$", filename)
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
          <div class="tl-legend">
            <span><i style="background:#166534"></i> OK</span>
            <span><i style="background:#78350f"></i> Some silence</span>
            <span><i style="background:#7f1d1d"></i> Silence</span>
            <span><i style="background:#0e2040"></i> No recording</span>
          </div>
        </div>
        <div id="tl-notice" class="notice hidden">
          <b>No streams enabled for recording.</b> Open <span class="notice-link" id="notice-settings-link">Settings</span> to choose which streams to log.
        </div>
        <div class="day-bar" id="day-bar">
          <div class="day-bar-bg" id="day-bar-bg"></div>
          <div class="day-bar-range hidden" id="day-bar-range"></div>
          <div class="day-bar-head hidden" id="day-bar-head"></div>
          <div class="day-bar-in hidden" id="day-bar-in"></div>
          <div class="day-bar-out hidden" id="day-bar-out"></div>
          <div class="day-bar-hover" id="day-bar-hover"></div>
        </div>
        <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--mu);margin-bottom:8px;padding:0 1px">
          <span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>23:55</span>
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
          <button class="btn bp bs" id="export-btn" disabled>⬇ Export Clip</button>
        </div>
      </div>
    </div>

    <!-- SETTINGS VIEW -->
    <div id="view-settings" class="settings-content hidden">
      <h2>Recording Settings</h2>
      <p class="sub">Enable recording per stream. Changes take effect immediately after saving.</p>

      <!-- Recordings path -->
      <div style="margin-bottom:18px;padding:14px 16px;background:var(--sur);border:1px solid var(--bor);border-radius:10px">
        <label style="font-size:12px;font-weight:700;color:var(--mu);letter-spacing:.6px;text-transform:uppercase;display:block;margin-bottom:6px">Recordings Path</label>
        <input type="text" id="rec-dir-input" placeholder="Default: logger_recordings (next to signalscope.py)"
               style="width:100%;background:#173a69;border:1px solid var(--bor);color:var(--tx);padding:8px 10px;border-radius:6px;font-size:13px;font-family:monospace;outline:none">
        <div style="margin-top:6px;font-size:12px;color:var(--mu)">
          Absolute path (e.g. <code>/mnt/recordings</code>) or relative to the SignalScope directory.
          Leave blank to use the default. The directory is created automatically if it does not exist.
        </div>
        <div id="rec-dir-resolved" style="margin-top:5px;font-size:11px;color:var(--mu);font-family:monospace"></div>
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

// ── Stream selector ───────────────────────────────────────────────────────
document.getElementById('stream-sel').addEventListener('change', function(){
  _currentSlug = this.value;
  _selSeg = null; _segsAll = []; _markIn = _markOut = null;
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
      o.value = s.slug; o.textContent = s.name; sel.appendChild(o);
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
  _get('/api/logger/days/'+_currentSlug).then(function(days){
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
  if(!_currentSlug||!_currentDate){ buildTimeline([]); return; }
  document.getElementById('tl-title').textContent = _currentDate;
  _segsAll = []; _markIn = _markOut = null;
  updateInOutLabel();
  document.getElementById('export-btn').disabled = true;
  document.getElementById('day-bar-head').classList.add('hidden');
  buildDayBar([]);
  _get('/api/logger/segments/'+_currentSlug+'/'+_currentDate).then(function(segs){
    buildTimeline(segs);
  });
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

// ── Segment loading & navigation ──────────────────────────────────────────
function _loadSegAndSeek(seg, offset){
  var a=document.getElementById('audio-el');
  var url='/api/logger/audio/'+_currentSlug+'/'+_currentDate+'/'+seg.filename;
  _selSeg=seg;
  var h=Math.floor(seg.start_s/3600),m=Math.floor((seg.start_s%3600)/60);
  document.getElementById('p-title').textContent=String(h).padStart(2,'0')+':'+String(m).padStart(2,'0');
  document.getElementById('p-sub').textContent=_currentDate+' · '+_currentSlug;
  _updateGridPlaying(seg.start_s);
  var spct=(seg.silence_pct||0).toFixed(1);
  document.getElementById('seg-info-text').innerHTML='<b>'+_esc(seg.filename)+'</b><br>Silence: '+spct+'% · '+seg.quality+' quality';
  document.getElementById('seg-info').style.display='block';
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
  if(next){ _loadSegAndSeek(next,0); } else { document.getElementById('play-btn').textContent='▶'; _updateGridPlaying(-1); }
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
    _playNext();
  });
  a.addEventListener('pause', function(){ document.getElementById('play-btn').textContent='▶'; });
  a.addEventListener('play',  function(){ document.getElementById('play-btn').textContent='⏸'; });
}

document.getElementById('play-btn').addEventListener('click', function(){
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

// ── Mark in/out ───────────────────────────────────────────────────────────
document.getElementById('mark-in-btn').addEventListener('click', function(){
  if(!_selSeg) return;
  var a=document.getElementById('audio-el');
  _markIn = _selSeg.start_s + a.currentTime;
  if(_markOut!==null && _markOut<=_markIn) _markOut=null;
  updateInOutLabel(); updateScrubMarkers(); updateDayBarMarkers();
  document.getElementById('export-btn').disabled = (_markIn===null||_markOut===null);
});

document.getElementById('mark-out-btn').addEventListener('click', function(){
  if(!_selSeg) return;
  var a=document.getElementById('audio-el');
  _markOut = _selSeg.start_s + a.currentTime;
  if(_markIn!==null && _markOut<=_markIn) _markIn=null;
  updateInOutLabel(); updateScrubMarkers(); updateDayBarMarkers();
  document.getElementById('export-btn').disabled = (_markIn===null||_markOut===null);
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
  var dur=document.getElementById('audio-el').duration||_SEG_SECS;
  var ss=_selSeg.start_s;
  var iE=document.getElementById('scrub-in'), oE=document.getElementById('scrub-out');
  if(_markIn!==null&&_markIn>=ss&&_markIn<ss+dur){ iE.style.left=((_markIn-ss)/dur*100)+'%'; iE.classList.remove('hidden'); } else { iE.classList.add('hidden'); }
  if(_markOut!==null&&_markOut>=ss&&_markOut<=ss+dur){ oE.style.left=((_markOut-ss)/dur*100)+'%'; oE.classList.remove('hidden'); } else { oE.classList.add('hidden'); }
}

// ── Export ────────────────────────────────────────────────────────────────
document.getElementById('export-btn').addEventListener('click', function(){
  if(_markIn===null||_markOut===null||!_currentSlug||!_currentDate) return;
  var btn=this; btn.disabled=true; btn.textContent='⏳ Exporting…';
  fetch('/api/logger/export',{method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRFToken':_csrf()},
    body:JSON.stringify({stream:_currentSlug,date:_currentDate,start_s:_markIn,end_s:_markOut})
  }).then(function(r){
    if(!r.ok){ return r.json().then(function(d){ alert('Export failed: '+(d.error||r.status)); }); }
    return r.blob().then(function(blob){
      var a=document.createElement('a');
      a.href=URL.createObjectURL(blob);
      a.download=_currentSlug+'_'+_currentDate+'_clip.mp3';
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
    renderSettingsRows();
    var st = res[2];
    document.getElementById('disk-info').textContent =
      'Disk usage: '+_fmtBytes(st.disk_bytes||0);
    // Populate recordings path input
    var rdInp = document.getElementById('rec-dir-input');
    rdInp.value = _cfg.rec_dir || '';
    var rdRes = document.getElementById('rec-dir-resolved');
    rdRes.textContent = st.rec_root ? '→ ' + st.rec_root : '';
  });
}

function renderSettingsRows(){
  var el=document.getElementById('settings-rows');
  el.innerHTML='';
  if(!_streams.length){
    el.innerHTML='<p style="color:var(--mu)">No input streams configured in SignalScope.</p>';
    return;
  }
  _streams.forEach(function(s){
    var sc=(_cfg.streams||{})[s.name]||{};
    var card=document.createElement('div'); card.className='scard';
    var chkd = sc.enabled ? ' checked' : '';
    var eid  = 'lbl-en-'+s.name.replace(/[^a-z0-9]/gi,'_');
    card.innerHTML =
      '<div class="scard-hdr">'
      +'<div><div class="name">'+_esc(s.name)+'</div><div class="url">'+_esc(s.url||'')+'</div></div>'
      +'<div class="tog-wrap">'
      +'<label class="tog"><input type="checkbox" data-stream="'+_esc(s.name)+'" data-key="enabled"'+chkd+'>'
      +'<span class="tog-sl"></span></label>'
      +'<span class="tog-lbl'+(sc.enabled?' on':'')+'" id="'+eid+'">'+(sc.enabled?'Recording':'Off')+'</span>'
      +'</div></div>'
      +'<div class="scard-body"><div class="scard-fields">'
      +'<div><label>HQ Bitrate</label><select data-stream="'+_esc(s.name)+'" data-key="hq_bitrate">'
      +['64k','96k','128k','192k','256k','320k'].map(function(b){ return '<option'+(( sc.hq_bitrate||'128k')===b?' selected':'')+'>'+b+'</option>'; }).join('')
      +'</select></div>'
      +'<div><label>LQ Bitrate</label><select data-stream="'+_esc(s.name)+'" data-key="lq_bitrate">'
      +['32k','48k','64k','96k'].map(function(b){ return '<option'+((sc.lq_bitrate||'48k')===b?' selected':'')+'>'+b+'</option>'; }).join('')
      +'</select></div>'
      +'<div><label>LQ after (days)</label><input type="number" min="1" max="3650" data-stream="'+_esc(s.name)+'" data-key="lq_after_days" value="'+(sc.lq_after_days||30)+'"></div>'
      +'<div><label>Delete after (days)</label><input type="number" min="1" max="3650" data-stream="'+_esc(s.name)+'" data-key="retain_days" value="'+(sc.retain_days||90)+'"></div>'
      +'</div></div>';
    var chk = card.querySelector('input[type=checkbox]');
    chk.addEventListener('change', function(){
      var lbl=document.getElementById(eid);
      lbl.textContent = this.checked ? 'Recording' : 'Off';
      lbl.className   = 'tog-lbl'+(this.checked?' on':'');
    });
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
  var recDir = document.getElementById('rec-dir-input').value.trim();
  var saveMsg = document.getElementById('save-msg');
  var saveErr = document.getElementById('save-err');
  saveMsg.style.display='none'; saveErr.style.display='none';
  _post('/api/logger/config',{streams:streams, rec_dir:recDir}).then(function(r){
    if(r.ok){
      _cfg.streams=streams; _cfg.rec_dir=recDir;
      saveMsg.style.display='inline';
      setTimeout(function(){ saveMsg.style.display='none'; },3000);
      // Refresh resolved path display from status
      fetch('/api/logger/status').then(function(res){ return res.json(); }).then(function(st){
        var rdRes=document.getElementById('rec-dir-resolved');
        rdRes.textContent = st.rec_root ? '→ '+st.rec_root : '';
        document.getElementById('disk-info').textContent='Disk usage: '+_fmtBytes(st.disk_bytes||0);
      });
      // Refresh notice on timeline
      var anyEnabled=Object.values(streams).some(function(s){ return s.enabled; });
      document.getElementById('tl-notice').classList.toggle('hidden', anyEnabled);
    } else {
      var errTxt = (r && r.error) ? r.error : 'Save failed';
      saveErr.textContent = '✗ '+errTxt; saveErr.style.display='inline';
    }
  });
});

})();
</script>
</body>
</html>"""
