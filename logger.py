# logger.py — SignalScope compliance logger plugin
# Drop alongside signalscope.py

SIGNALSCOPE_PLUGIN = {
    "id":    "logger",
    "label": "Logger",
    "url":   "/hub/logger",
    "icon":  "🎙",
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
    (_app_dir / _REC_DIR).mkdir(parents=True, exist_ok=True)

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
            _save_config()
        _reconcile_recorders()
        return jsonify({"ok": True})

    @app.get("/api/logger/status")
    @login_req
    def api_logger_status():
        with _rec_lock:
            active = {sl: {"stream": t.stream_name, "running": t.is_alive()}
                      for sl, t in _recorders.items()}
        rec_root = _app_dir / _REC_DIR
        total = sum(f.stat().st_size for f in rec_root.rglob("*.mp3") if f.is_file()) \
                if rec_root.exists() else 0
        return jsonify({"recorders": active, "disk_bytes": total})

    @app.get("/api/logger/days/<stream_slug>")
    @login_req
    def api_logger_days(stream_slug):
        sdir = _app_dir / _REC_DIR / _safe(stream_slug)
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
        path = _app_dir / _REC_DIR / _safe(stream_slug) / date / filename
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

def _save_config():
    try:
        with open(_app_dir / _CONFIG_FILE, "w") as f:
            json.dump(_cfg, f, indent=2)
    except Exception as e:
        _log(f"[Logger] Config save failed: {e}")

def _stream_cfg(stream_name):
    with _cfg_lock:
        s = _cfg.get("streams", {}).get(stream_name, {})
    return {
        "enabled":       bool(s.get("enabled", False)),
        "hq_bitrate":    s.get("hq_bitrate", _DEFAULT_HQ),
        "lq_bitrate":    s.get("lq_bitrate", _DEFAULT_LQ),
        "lq_after_days": int(s.get("lq_after_days", _DEFAULT_LQ_AFTER)),
        "retain_days":   int(s.get("retain_days", _DEFAULT_RETAIN)),
    }

def _available_streams():
    cfg = _monitor.app_cfg
    return [{"name": inp.name, "url": inp.device_index, "slug": _slug(inp.name)}
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
    day_dir = _app_dir / _REC_DIR / slug / date
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
    def __init__(self, stream_name, stream_url, slug):
        super().__init__(daemon=True, name=f"Logger-{slug}")
        self.stream_name = stream_name
        self.stream_url  = stream_url
        self.slug        = slug
        self.stop_evt    = threading.Event()

    def run(self):
        _log(f"[Logger] Started recording: {self.stream_name}")
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        while not self.stop_evt.is_set():
            try:
                self._record_segment(ffmpeg)
            except Exception as e:
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
        duration  = max(5, (seg_end - now).total_seconds())
        date_str  = seg_start.strftime("%Y-%m-%d")
        time_str  = seg_start.strftime("%H-%M")
        filename  = f"{time_str}.mp3"
        start_s   = seg_start.hour * 3600 + seg_start.minute * 60

        out_dir  = _app_dir / _REC_DIR / self.slug / date_str
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / filename

        # Skip if already recorded
        if out_path.exists() and out_path.stat().st_size > 5000:
            wait = (seg_end - datetime.datetime.utcnow()).total_seconds()
            if wait > 0:
                self.stop_evt.wait(wait)
            return

        # Build ffmpeg command
        http = self.stream_url.lower().startswith(("http://", "https://"))
        reconnect = ["-reconnect", "1", "-reconnect_streamed", "1",
                     "-reconnect_delay_max", "10"] if http else []
        cmd = ([ffmpeg, "-hide_banner", "-loglevel", "warning"]
               + reconnect
               + ["-i", self.stream_url,
                  "-af", f"silencedetect=n={_SILENCE_DB:.1f}dB:d={_SILENCE_DUR}",
                  "-t", str(int(duration)),
                  "-vn", "-ac", "1", "-ar", "44100",
                  "-b:a", scfg["hq_bitrate"],
                  "-f", "mp3", str(out_path)])

        silence_ranges = []
        sil_start      = None

        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, text=True, bufsize=1)
            for line in proc.stderr:
                m_s = re.search(r"silence_start:\s*([\d.]+)", line)
                m_e = re.search(r"silence_end:\s*([\d.]+)", line)
                if m_s:
                    sil_start = float(m_s.group(1))
                if m_e and sil_start is not None:
                    silence_ranges.append([sil_start, float(m_e.group(1))])
                    sil_start = None
                if self.stop_evt.is_set():
                    proc.terminate()
                    break
            proc.wait()
        except Exception as e:
            _log(f"[Logger] ffmpeg error ({self.stream_name}): {e}")
            return

        if sil_start is not None:
            silence_ranges.append([sil_start, duration])

        if out_path.exists() and out_path.stat().st_size > 1000:
            _upsert_segment(self.slug, date_str, filename, start_s, silence_ranges)

        # Wait for next boundary
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
                t = _RecorderThread(s["name"], s["url"], slug)
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
    rec_root = _app_dir / _REC_DIR
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
                _log(f"[Logger] Pruning {day_dir}")
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

def _export_clip(slug, date, start_s, end_s):
    day_dir = _app_dir / _REC_DIR / slug / date
    if not day_dir.exists():
        return jsonify({"error": "no recordings"}), 404
    ffmpeg  = shutil.which("ffmpeg") or "ffmpeg"
    segs    = _get_segments(slug, date)
    relevant = [(seg["start_s"], day_dir / seg["filename"])
                for seg in segs
                if seg["start_s"] + _SEG_SECS > start_s
                and seg["start_s"] < end_s
                and (day_dir / seg["filename"]).exists()]
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
            tf = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
            for _, p in relevant:
                tf.write(f"file '{p}'\n")
            tf.close()
            tf_name   = tf.name
            first_ss  = relevant[0][0]
            ss        = max(0.0, start_s - first_ss)
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
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#e6edf3;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;min-height:100vh}
a{color:#58a6ff;text-decoration:none}
/* Header */
.hdr{display:flex;align-items:center;gap:16px;padding:12px 20px;background:#161b22;border-bottom:1px solid #30363d}
.hdr-title{font-size:18px;font-weight:700;color:#e6edf3}
.hdr-sub{color:#8b949e;font-size:13px}
.hdr a{color:#8b949e;font-size:13px}
.hdr a:hover{color:#e6edf3}
/* Layout */
.layout{display:grid;grid-template-columns:260px 1fr;height:calc(100vh - 49px)}
.sidebar{background:#161b22;border-right:1px solid #30363d;padding:16px;overflow-y:auto;display:flex;flex-direction:column;gap:16px}
.main{display:flex;flex-direction:column;overflow:hidden}
/* Sidebar */
.sec-label{font-size:11px;font-weight:600;color:#8b949e;letter-spacing:.6px;text-transform:uppercase;margin-bottom:6px}
select,input[type=text],input[type=number]{width:100%;background:#0d1117;border:1px solid #30363d;color:#e6edf3;padding:6px 10px;border-radius:6px;font-size:13px;outline:none}
select:focus,input:focus{border-color:#58a6ff}
.btn{display:inline-flex;align-items:center;gap:6px;padding:7px 14px;border-radius:6px;border:none;cursor:pointer;font-size:13px;font-weight:500;transition:opacity .15s}
.btn:hover{opacity:.85}
.btn:disabled{opacity:.4;cursor:not-allowed}
.btn-blue{background:#1f6feb;color:#fff}
.btn-green{background:#238636;color:#fff}
.btn-red{background:#da3633;color:#fff}
.btn-ghost{background:transparent;border:1px solid #30363d;color:#8b949e}
.btn-ghost:hover{color:#e6edf3;border-color:#8b949e}
/* Date nav */
.date-nav{display:flex;align-items:center;gap:8px}
.date-nav .date-lbl{flex:1;text-align:center;font-weight:600;font-size:14px}
.date-nav button{background:#21262d;border:1px solid #30363d;color:#8b949e;width:30px;height:30px;border-radius:6px;cursor:pointer;font-size:16px}
.date-nav button:hover{color:#e6edf3}
/* Status badge */
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}
.badge-rec{background:rgba(218,54,51,.2);color:#f85149}
.badge-idle{background:rgba(139,148,158,.15);color:#8b949e}
.badge-lq{background:rgba(210,153,34,.2);color:#d2a213}
/* Timeline */
.tl-wrap{flex:1;overflow-y:auto;padding:16px 20px}
.tl-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
.tl-title{font-size:16px;font-weight:600}
.tl-legend{display:flex;gap:12px;font-size:11px;color:#8b949e}
.tl-legend span{display:flex;align-items:center;gap:4px}
.tl-legend i{width:12px;height:12px;border-radius:2px;display:inline-block}
.tl-grid{display:grid;grid-template-columns:40px 1fr;gap:4px;align-items:center}
.tl-hour-lbl{text-align:right;font-size:11px;color:#8b949e;padding-right:8px;line-height:1}
.tl-row{display:grid;grid-template-columns:repeat(12,1fr);gap:2px}
.tl-block{height:24px;border-radius:3px;cursor:pointer;transition:opacity .1s,outline .1s;position:relative}
.tl-block:hover{opacity:.8;outline:2px solid #58a6ff}
.tl-block.selected{outline:2px solid #fff}
.tl-block.playing{outline:2px solid #3fb950}
.tl-block[data-status="none"]{background:#21262d}
.tl-block[data-status="ok"]{background:#238636}
.tl-block[data-status="warn"]{background:#9e6a03}
.tl-block[data-status="silent"]{background:#b62324}
.tl-block[data-status="future"]{background:#161b22}
/* Tooltip */
.tl-block::after{content:attr(data-tip);position:absolute;bottom:calc(100% + 6px);left:50%;transform:translateX(-50%);background:#1c2128;border:1px solid #30363d;padding:4px 8px;border-radius:4px;font-size:11px;white-space:nowrap;color:#e6edf3;pointer-events:none;opacity:0;transition:opacity .15s;z-index:10}
.tl-block:hover::after{opacity:1}
/* Player bar */
.player{background:#161b22;border-top:1px solid #30363d;padding:12px 20px;display:flex;flex-direction:column;gap:8px}
.player-top{display:flex;align-items:center;gap:12px}
.player-info{flex:1;min-width:0}
.player-info .p-title{font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.player-info .p-sub{font-size:11px;color:#8b949e}
.player-controls{display:flex;align-items:center;gap:8px}
.play-btn{width:36px;height:36px;border-radius:50%;background:#238636;border:none;color:#fff;cursor:pointer;font-size:16px;display:flex;align-items:center;justify-content:center}
.play-btn:hover{background:#2ea043}
.time-lbl{font-size:12px;color:#8b949e;font-variant-numeric:tabular-nums;min-width:80px;text-align:center}
/* Scrub bar */
.scrub-wrap{position:relative;height:24px;display:flex;align-items:center;cursor:pointer}
.scrub-track{width:100%;height:4px;background:#21262d;border-radius:2px;position:relative;overflow:visible}
.scrub-fill{height:100%;background:#238636;border-radius:2px;pointer-events:none}
.scrub-thumb{position:absolute;top:50%;transform:translate(-50%,-50%);width:12px;height:12px;border-radius:50%;background:#3fb950;pointer-events:none}
.scrub-in,.scrub-out{position:absolute;top:0;bottom:0;width:2px;background:#58a6ff;pointer-events:none;z-index:2}
/* Export */
.export-bar{display:flex;align-items:center;gap:8px;font-size:12px;color:#8b949e}
.export-bar .in-out-lbl{color:#58a6ff;font-weight:600}
/* Settings panel */
.settings-wrap{padding:20px;max-width:700px;overflow-y:auto;height:100%}
.settings-wrap h2{font-size:16px;font-weight:600;margin-bottom:16px}
.stream-row{display:grid;grid-template-columns:1fr 90px 90px 90px 90px 50px;gap:8px;align-items:center;padding:10px 0;border-bottom:1px solid #21262d;font-size:13px}
.stream-row.hdr-row{font-size:11px;color:#8b949e;font-weight:600;padding-bottom:6px;border-bottom:2px solid #30363d}
.stream-row input[type=number]{width:100%}
input[type=checkbox]{width:18px;height:18px;accent-color:#1f6feb;cursor:pointer}
.disk-info{margin-top:16px;padding:12px;background:#161b22;border-radius:8px;font-size:13px;color:#8b949e}
.hidden{display:none}
.tab-bar{display:flex;gap:4px;padding:12px 20px 0;border-bottom:1px solid #30363d;background:#0d1117}
.tab{padding:8px 16px;border-radius:6px 6px 0 0;border:1px solid transparent;cursor:pointer;font-size:13px;color:#8b949e;background:transparent}
.tab.active{background:#161b22;border-color:#30363d #30363d #161b22;color:#e6edf3}
</style>
</head>
<body>
<div class="hdr">
  <div>
    <div class="hdr-title">🎙 Logger</div>
    <div class="hdr-sub">Compliance recording &amp; playback</div>
  </div>
  <div style="margin-left:auto;display:flex;gap:12px;align-items:center">
    <span id="rec-status" class="badge badge-idle">Idle</span>
    <a href="/">← SignalScope</a>
  </div>
</div>

<div class="tab-bar">
  <div class="tab active" onclick="showTab('timeline')" id="tab-timeline">Timeline</div>
  <div class="tab" onclick="showTab('settings')" id="tab-settings">Settings</div>
</div>

<!-- TIMELINE TAB -->
<div id="view-timeline" class="layout">
  <div class="sidebar">
    <div>
      <div class="sec-label">Stream</div>
      <select id="stream-sel" onchange="onStreamChange()">
        <option value="">— select stream —</option>
      </select>
    </div>
    <div>
      <div class="sec-label">Date</div>
      <div class="date-nav">
        <button onclick="shiftDay(-1)">‹</button>
        <div class="date-lbl" id="date-lbl">—</div>
        <button onclick="shiftDay(1)">›</button>
      </div>
    </div>
    <div id="day-list-wrap">
      <div class="sec-label">Days with recordings</div>
      <div id="day-list" style="display:flex;flex-direction:column;gap:4px;max-height:180px;overflow-y:auto"></div>
    </div>
    <div id="seg-info" style="background:#161b22;border-radius:8px;padding:12px;font-size:12px;color:#8b949e;display:none">
      <div id="seg-info-text"></div>
    </div>
  </div>

  <div class="main">
    <div class="tl-wrap" id="tl-wrap">
      <div class="tl-head">
        <div class="tl-title" id="tl-title">Select a stream</div>
        <div class="tl-legend">
          <span><i style="background:#238636"></i> OK</span>
          <span><i style="background:#9e6a03"></i> Partial silence</span>
          <span><i style="background:#b62324"></i> Silence</span>
          <span><i style="background:#21262d"></i> No recording</span>
        </div>
      </div>
      <div id="tl-grid" class="tl-grid"></div>
    </div>

    <div class="player" id="player">
      <audio id="audio-el" preload="none"></audio>
      <div class="player-top">
        <button class="play-btn" id="play-btn" onclick="togglePlay()">▶</button>
        <div class="player-info">
          <div class="p-title" id="p-title">No segment selected</div>
          <div class="p-sub" id="p-sub">Click a segment on the timeline to play</div>
        </div>
        <div class="player-controls">
          <span class="time-lbl" id="time-lbl">0:00 / 0:00</span>
        </div>
      </div>
      <div class="scrub-wrap" id="scrub" onclick="scrubClick(event)" onmousedown="scrubStart(event)">
        <div class="scrub-track">
          <div class="scrub-fill" id="scrub-fill" style="width:0%"></div>
          <div class="scrub-thumb" id="scrub-thumb" style="left:0%"></div>
          <div class="scrub-in hidden" id="scrub-in"></div>
          <div class="scrub-out hidden" id="scrub-out"></div>
        </div>
      </div>
      <div class="export-bar">
        <button class="btn btn-ghost" id="mark-in-btn" onclick="markIn()">⬥ Mark In</button>
        <button class="btn btn-ghost" id="mark-out-btn" onclick="markOut()">⬥ Mark Out</button>
        <span class="in-out-lbl" id="inout-lbl"></span>
        <div style="flex:1"></div>
        <button class="btn btn-blue" id="export-btn" onclick="exportClip()" disabled>⬇ Export Clip</button>
      </div>
    </div>
  </div>
</div>

<!-- SETTINGS TAB -->
<div id="view-settings" class="settings-wrap hidden">
  <h2>Recording Settings</h2>
  <div class="stream-row hdr-row">
    <div>Stream</div>
    <div>HQ bitrate</div>
    <div>LQ bitrate</div>
    <div>LQ after (days)</div>
    <div>Keep (days)</div>
    <div>Record</div>
  </div>
  <div id="settings-rows"></div>
  <div style="margin-top:16px;display:flex;gap:8px">
    <button class="btn btn-green" onclick="saveSettings()">💾 Save Settings</button>
    <span id="save-msg" style="color:#3fb950;font-size:13px;line-height:36px;display:none">Saved!</span>
  </div>
  <div class="disk-info" id="disk-info">Calculating disk usage…</div>
</div>

<script nonce="{{ csp_nonce() }}">
// ── State ─────────────────────────────────────────────────────────────────
var _streams      = [];
var _days         = [];
var _segments     = [];
var _currentSlug  = '';
var _currentDate  = '';
var _selSeg       = null;   // currently selected segment object
var _markIn       = null;   // absolute seconds since midnight
var _markOut      = null;
var _scrubDragging = false;
var _cfg          = {streams:{}};

function _csrf(){ return (document.querySelector('meta[name="csrf-token"]')||{}).content||''; }
function _get(url){ return fetch(url,{credentials:'same-origin'}).then(r=>r.json()); }
function _post(url,body){
  return fetch(url,{method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRFToken':_csrf()},
    body:JSON.stringify(body)}).then(r=>r.json());
}

// ── Init ──────────────────────────────────────────────────────────────────
window.addEventListener('load', function(){
  var today = new Date();
  _currentDate = today.toISOString().slice(0,10);
  updateDateLabel();
  loadStreams();
  loadStatus();
  setInterval(loadStatus, 10000);
  setupAudio();
});

function showTab(tab){
  document.getElementById('view-timeline').classList.toggle('hidden', tab!=='timeline');
  document.getElementById('view-settings').classList.toggle('hidden', tab!=='settings');
  document.getElementById('tab-timeline').classList.toggle('active', tab==='timeline');
  document.getElementById('tab-settings').classList.toggle('active', tab==='settings');
  if(tab==='settings') loadSettingsPanel();
}

// ── Streams ───────────────────────────────────────────────────────────────
function loadStreams(){
  _get('/api/logger/streams').then(function(data){
    _streams = data;
    var sel = document.getElementById('stream-sel');
    sel.innerHTML = '<option value="">— select stream —</option>';
    data.forEach(function(s){
      var o = document.createElement('option');
      o.value = s.slug; o.textContent = s.name;
      sel.appendChild(o);
    });
    if(_streams.length > 0){
      sel.value = _streams[0].slug;
      onStreamChange();
    }
  });
}

function onStreamChange(){
  _currentSlug = document.getElementById('stream-sel').value;
  _selSeg = null;
  _markIn = _markOut = null;
  updateInOutLabel();
  if(_currentSlug) loadDays();
}

// ── Days ──────────────────────────────────────────────────────────────────
function loadDays(){
  if(!_currentSlug) return;
  _get('/api/logger/days/'+_currentSlug).then(function(days){
    _days = days;
    var el = document.getElementById('day-list');
    el.innerHTML = '';
    days.slice(0,14).forEach(function(d){
      var btn = document.createElement('button');
      btn.className = 'btn btn-ghost';
      btn.style = 'width:100%;justify-content:flex-start;padding:5px 10px';
      btn.textContent = d;
      btn.onclick = function(){ _currentDate=d; updateDateLabel(); loadSegments(); };
      el.appendChild(btn);
    });
    loadSegments();
  });
}

function shiftDay(delta){
  if(!_currentDate) return;
  var d = new Date(_currentDate+'T00:00:00Z');
  d.setUTCDate(d.getUTCDate()+delta);
  _currentDate = d.toISOString().slice(0,10);
  updateDateLabel();
  loadSegments();
}

function updateDateLabel(){
  document.getElementById('date-lbl').textContent = _currentDate || '—';
}

// ── Segments & Timeline ───────────────────────────────────────────────────
function loadSegments(){
  if(!_currentSlug || !_currentDate){ buildTimeline([]); return; }
  document.getElementById('tl-title').textContent = _currentDate;
  _get('/api/logger/segments/'+_currentSlug+'/'+_currentDate).then(function(segs){
    _segments = segs;
    buildTimeline(segs);
  });
}

function buildTimeline(segs){
  var grid = document.getElementById('tl-grid');
  grid.innerHTML = '';
  // Build lookup: start_s → seg
  var lookup = {};
  segs.forEach(function(s){ lookup[s.start_s] = s; });

  var now = new Date();
  var todayStr = now.toISOString().slice(0,10);
  var nowSecs  = now.getUTCHours()*3600 + now.getUTCMinutes()*60;

  for(var h=0; h<24; h++){
    // Hour label
    var lbl = document.createElement('div');
    lbl.className = 'tl-hour-lbl';
    lbl.textContent = String(h).padStart(2,'0')+':00';
    grid.appendChild(lbl);

    // Row of 12 five-minute blocks
    var row = document.createElement('div');
    row.className = 'tl-row';
    for(var m=0; m<60; m+=5){
      var ss = h*3600 + m*60;
      var blk = document.createElement('div');
      blk.className = 'tl-block';
      var seg = lookup[ss];
      var isFuture = _currentDate === todayStr && ss > nowSecs;
      if(isFuture){
        blk.dataset.status = 'future';
        blk.dataset.tip = String(h).padStart(2,'0')+':'+String(m).padStart(2,'0')+' — future';
      } else if(seg){
        var spct = seg.silence_pct || 0;
        blk.dataset.status = spct > 80 ? 'silent' : spct > 10 ? 'warn' : 'ok';
        blk.dataset.tip = String(h).padStart(2,'0')+':'+String(m).padStart(2,'0')
          +' · '+seg.quality+' · silence '+spct.toFixed(0)+'%';
        blk.dataset.ss = ss;
        blk.dataset.fname = seg.filename;
        (function(s){ blk.onclick = function(){ playSeg(s, blk); }; })(seg);
      } else {
        blk.dataset.status = 'none';
        blk.dataset.tip = String(h).padStart(2,'0')+':'+String(m).padStart(2,'0')+' — no recording';
      }
      row.appendChild(blk);
    }
    grid.appendChild(row);
  }
}

function playSeg(seg, blkEl){
  // Deselect previous
  document.querySelectorAll('.tl-block.selected').forEach(function(b){ b.classList.remove('selected'); });
  if(blkEl) blkEl.classList.add('selected','playing');
  _selSeg = seg;

  // Show info
  var info = document.getElementById('seg-info');
  var spct = (seg.silence_pct||0).toFixed(1);
  document.getElementById('seg-info-text').innerHTML =
    '<b>'+seg.filename+'</b><br>Silence: '+spct+'% · '+seg.quality+' quality'
    +(seg.silence_ranges&&seg.silence_ranges.length?' · '+seg.silence_ranges.length+' gap(s)':'');
  info.style.display='block';

  // Load audio
  var url = '/api/logger/audio/'+_currentSlug+'/'+_currentDate+'/'+seg.filename;
  var audio = document.getElementById('audio-el');
  audio.src = url;
  audio.load();
  audio.play().catch(function(){});
  document.getElementById('p-title').textContent = seg.filename;
  document.getElementById('p-sub').textContent = _currentDate+' · '+_currentSlug;
  document.getElementById('play-btn').textContent = '⏸';

  // Reset marks relative to this segment for scrub display
  _markIn = _markOut = null;
  updateInOutLabel();
  resetScrubMarkers();
}

// ── Audio Player ──────────────────────────────────────────────────────────
function setupAudio(){
  var audio = document.getElementById('audio-el');
  audio.addEventListener('timeupdate', onTimeUpdate);
  audio.addEventListener('ended', onEnded);
  audio.addEventListener('pause', function(){ document.getElementById('play-btn').textContent='▶'; });
  audio.addEventListener('play',  function(){ document.getElementById('play-btn').textContent='⏸'; });
}

function togglePlay(){
  var audio = document.getElementById('audio-el');
  if(!audio.src || audio.src.endsWith('#')) return;
  if(audio.paused) audio.play(); else audio.pause();
}

function onTimeUpdate(){
  var audio = document.getElementById('audio-el');
  var cur = audio.currentTime, dur = audio.duration||0;
  document.getElementById('time-lbl').textContent = _fmt(cur)+' / '+_fmt(dur);
  var pct = dur>0 ? cur/dur*100 : 0;
  document.getElementById('scrub-fill').style.width = pct+'%';
  document.getElementById('scrub-thumb').style.left  = pct+'%';
  updateScrubMarkers();
}

function onEnded(){
  document.getElementById('play-btn').textContent = '▶';
  document.querySelectorAll('.tl-block.playing').forEach(function(b){ b.classList.remove('playing'); });
}

function _fmt(s){
  s = isNaN(s)?0:s;
  var m=Math.floor(s/60), ss=Math.floor(s%60);
  return m+':'+(ss<10?'0':'')+ss;
}

// ── Scrub ─────────────────────────────────────────────────────────────────
function scrubClick(e){
  if(_scrubDragging) return;
  seekTo(e);
}

function scrubStart(e){
  if(e.button!==0) return;
  _scrubDragging = true;
  seekTo(e);
  function onMove(ev){ if(_scrubDragging) seekTo(ev); }
  function onUp(){ _scrubDragging=false; document.removeEventListener('mousemove',onMove); document.removeEventListener('mouseup',onUp); }
  document.addEventListener('mousemove',onMove);
  document.addEventListener('mouseup',onUp);
  e.preventDefault();
}

function seekTo(e){
  var track = document.querySelector('.scrub-track');
  var rect  = track.getBoundingClientRect();
  var pct   = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
  var audio = document.getElementById('audio-el');
  if(audio.duration) audio.currentTime = pct * audio.duration;
}

// ── Mark In / Out ─────────────────────────────────────────────────────────
function markIn(){
  if(!_selSeg) return;
  var audio = document.getElementById('audio-el');
  _markIn = _selSeg.start_s + audio.currentTime;
  if(_markOut !== null && _markOut <= _markIn) _markOut = null;
  updateInOutLabel();
  updateScrubMarkers();
  document.getElementById('export-btn').disabled = (_markIn===null||_markOut===null);
}

function markOut(){
  if(!_selSeg) return;
  var audio = document.getElementById('audio-el');
  _markOut = _selSeg.start_s + audio.currentTime;
  if(_markIn !== null && _markOut <= _markIn) _markIn = null;
  updateInOutLabel();
  updateScrubMarkers();
  document.getElementById('export-btn').disabled = (_markIn===null||_markOut===null);
}

function updateInOutLabel(){
  var lbl = document.getElementById('inout-lbl');
  if(_markIn===null && _markOut===null){ lbl.textContent=''; return; }
  var parts = [];
  if(_markIn!==null)  parts.push('In: '+_fmtAbs(_markIn));
  if(_markOut!==null) parts.push('Out: '+_fmtAbs(_markOut));
  if(_markIn!==null && _markOut!==null) parts.push('Dur: '+_fmt(_markOut-_markIn));
  lbl.textContent = parts.join('  ·  ');
}

function _fmtAbs(secs){
  var h=Math.floor(secs/3600), m=Math.floor((secs%3600)/60), s=Math.floor(secs%60);
  return (h>0?h+':':'')+(m<10&&h>0?'0':'')+m+':'+(s<10?'0':'')+s;
}

function updateScrubMarkers(){
  if(!_selSeg) return;
  var audio = document.getElementById('audio-el');
  var dur   = audio.duration || _SEG_SECS;
  var segS  = _selSeg.start_s;
  var inEl  = document.getElementById('scrub-in');
  var outEl = document.getElementById('scrub-out');
  if(_markIn!==null && _markIn>=segS && _markIn<segS+dur){
    var pct = (_markIn-segS)/dur*100;
    inEl.style.left=pct+'%'; inEl.classList.remove('hidden');
  } else { inEl.classList.add('hidden'); }
  if(_markOut!==null && _markOut>=segS && _markOut<=segS+dur){
    var pct = (_markOut-segS)/dur*100;
    outEl.style.left=pct+'%'; outEl.classList.remove('hidden');
  } else { outEl.classList.add('hidden'); }
}

function resetScrubMarkers(){
  document.getElementById('scrub-in').classList.add('hidden');
  document.getElementById('scrub-out').classList.add('hidden');
}

// ── Export ────────────────────────────────────────────────────────────────
var _SEG_SECS = 300;

function exportClip(){
  if(_markIn===null||_markOut===null||!_currentSlug||!_currentDate) return;
  var btn = document.getElementById('export-btn');
  btn.disabled=true; btn.textContent='⏳ Exporting…';
  fetch('/api/logger/export',{
    method:'POST', credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRFToken':_csrf()},
    body: JSON.stringify({stream:_currentSlug,date:_currentDate,start_s:_markIn,end_s:_markOut})
  }).then(function(r){
    if(!r.ok){ r.json().then(function(d){ alert('Export failed: '+(d.error||r.status)); }); }
    else{
      return r.blob().then(function(blob){
        var a=document.createElement('a');
        a.href=URL.createObjectURL(blob);
        a.download=_currentSlug+'_'+_currentDate+'_clip.mp3';
        a.click();
      });
    }
  }).finally(function(){ btn.disabled=false; btn.textContent='⬇ Export Clip'; });
}

// ── Status ────────────────────────────────────────────────────────────────
function loadStatus(){
  _get('/api/logger/status').then(function(data){
    var recs = data.recorders||{};
    var active = Object.values(recs).filter(function(r){ return r.running; });
    var badge = document.getElementById('rec-status');
    if(active.length>0){
      badge.className='badge badge-rec';
      badge.textContent='● REC ('+active.length+')';
    } else {
      badge.className='badge badge-idle';
      badge.textContent='Idle';
    }
    var di = document.getElementById('disk-info');
    if(di) di.textContent='Disk usage: '+_fmtBytes(data.disk_bytes||0)
      +(active.length?' · '+active.length+' stream(s) recording':'');
  });
}

function _fmtBytes(b){
  if(b<1024) return b+'B';
  if(b<1048576) return (b/1024).toFixed(1)+'KB';
  if(b<1073741824) return (b/1048576).toFixed(1)+'MB';
  return (b/1073741824).toFixed(2)+'GB';
}

// ── Settings ──────────────────────────────────────────────────────────────
function loadSettingsPanel(){
  Promise.all([_get('/api/logger/streams'), _get('/api/logger/config'), _get('/api/logger/status')])
  .then(function(results){
    _streams = results[0];
    _cfg     = results[1];
    var status = results[2];
    renderSettingsRows();
    var di = document.getElementById('disk-info');
    di.textContent='Disk usage: '+_fmtBytes(status.disk_bytes||0);
  });
}

function renderSettingsRows(){
  var el = document.getElementById('settings-rows');
  el.innerHTML='';
  _streams.forEach(function(s){
    var sc = (_cfg.streams||{})[s.name]||{};
    var row=document.createElement('div'); row.className='stream-row';
    row.innerHTML=
      '<div style="font-weight:500">'+_esc(s.name)+'<br><span style="color:#8b949e;font-size:11px">'+_esc(s.url||'')+'</span></div>'
      +'<div><select data-stream="'+_esc(s.name)+'" data-key="hq_bitrate">'
      +['64k','96k','128k','192k','256k','320k'].map(function(b){
        return '<option'+(( sc.hq_bitrate||'128k')===b?' selected':'')+'>'+b+'</option>';}).join('')+'</select></div>'
      +'<div><select data-stream="'+_esc(s.name)+'" data-key="lq_bitrate">'
      +['32k','48k','64k','96k'].map(function(b){
        return '<option'+((sc.lq_bitrate||'48k')===b?' selected':'')+'>'+b+'</option>';}).join('')+'</select></div>'
      +'<div><input type="number" min="1" max="3650" data-stream="'+_esc(s.name)+'" data-key="lq_after_days" value="'+(sc.lq_after_days||30)+'" style="width:70px"></div>'
      +'<div><input type="number" min="1" max="3650" data-stream="'+_esc(s.name)+'" data-key="retain_days"   value="'+(sc.retain_days||90)+'"  style="width:70px"></div>'
      +'<div><input type="checkbox" data-stream="'+_esc(s.name)+'" data-key="enabled"'+(sc.enabled?' checked':'')+'></div>';
    el.appendChild(row);
  });
}

function _esc(s){ var d=document.createElement('div'); d.textContent=s; return d.innerHTML; }

function saveSettings(){
  var streams={};
  document.querySelectorAll('[data-stream]').forEach(function(el){
    var name=el.dataset.stream, key=el.dataset.key;
    if(!streams[name]) streams[name]={};
    if(el.type==='checkbox') streams[name][key]=el.checked;
    else if(el.type==='number') streams[name][key]=parseInt(el.value,10)||0;
    else streams[name][key]=el.value;
  });
  _post('/api/logger/config',{streams:streams}).then(function(r){
    if(r.ok){
      var msg=document.getElementById('save-msg');
      msg.style.display='inline'; setTimeout(function(){ msg.style.display='none'; },3000);
      _cfg.streams=streams;
    } else { alert('Save failed'); }
  });
}
</script>
</body>
</html>"""
