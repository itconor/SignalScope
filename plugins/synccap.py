"""
synccap.py  —  Multi-site synchronized audio capture

Hub page lets you pick any number of inputs across any connected sites,
set a capture duration (5–300 s), and press Capture.  The hub broadcasts a
"capture at T+5 s" command to each client via a lightweight poll; each client
grabs the last N seconds from its _stream_buffer and uploads the audio back to
the hub.  Hub-local inputs are captured directly.  All clips for a session are
presented together with inline audio players for side-by-side comparison.

Hub-only plugin — nav item hidden on client/standalone nodes.
"""

SIGNALSCOPE_PLUGIN = {
    "id":       "synccap",
    "label":    "Sync Capture",
    "url":      "/hub/synccap",
    "icon":     "🎙",
    "hub_only": True,
    "version":  "1.0.5",
}

import os
import json
import uuid
import time
import wave
import io
import threading
import shutil
import subprocess
import re
import urllib.request
import urllib.error

try:
    import numpy as np
    _HAS_NP = True
except ImportError:
    _HAS_NP = False

# ── module-level state (set in register) ─────────────────────────────────────

_CLIP_DIR      = None   # plugins/synccap_clips/
_DB_PATH       = None   # plugins/synccap_db.json
_db_lock       = threading.Lock()

_SAMPLE_RATE   = 48000
_MIN_DUR       = 5
_MAX_DUR       = 300
_CLIENT_POLL_S = 3      # client polls /api/synccap/cmd every 3 s (fallback for old cores)
_EXPIRE_S      = 180    # capture expires after 3 min if not complete

# hub-side: pending commands for old-core clients that poll /api/synccap/cmd
# dict of site → (cmd_dict, expiry_ts)
_pending_cmds  = {}
_pending_lock  = threading.Lock()

# client-side: set of capture_ids already processed — prevents double-capture
# if both heartbeat and poll deliver the same command
_processed_captures: set = set()


# ── DB helpers ────────────────────────────────────────────────────────────────

def _load_db():
    if not _DB_PATH or not os.path.exists(_DB_PATH):
        return {}
    try:
        with open(_DB_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_db(data):
    with open(_DB_PATH, "w") as f:
        json.dump(data, f, indent=2)


# ── audio helpers ─────────────────────────────────────────────────────────────

def _chunks_to_pcm(chunks, duration_s, n_ch):
    """Return last duration_s*SR*n_ch int16 samples as bytes.

    _stream_buffer holds float32 in [-1.0, 1.0].  Scale by 32767 before
    converting to int16 — without this the int16 values are 0/±1 (silence).
    """
    if not _HAS_NP or not chunks:
        return b""
    need = int(duration_s * _SAMPLE_RATE) * n_ch
    try:
        arr = np.concatenate(list(chunks))
        if arr.size > need:
            arr = arr[-need:]
        # Scale float32 [-1, 1] → int16 [-32767, 32767]
        arr = np.clip(arr, -1.0, 1.0)
        return (arr * 32767).astype(np.int16).tobytes()
    except Exception:
        return b""


def _pcm_to_wav(pcm_bytes, n_ch):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(n_ch)
        wf.setsampwidth(2)
        wf.setframerate(_SAMPLE_RATE)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def _wav_to_mp3(wav_bytes):
    """Compress WAV → MP3 via ffmpeg.  Returns MP3 bytes or None."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    try:
        result = subprocess.run(
            [ffmpeg, "-y", "-f", "wav", "-i", "pipe:0",
             "-codec:a", "libmp3lame", "-q:a", "4",
             "-f", "mp3", "pipe:1"],
            input=wav_bytes, capture_output=True, timeout=60,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout
    except Exception:
        pass
    return None


def _capture_input(cfg, duration_s):
    """
    Grab duration_s of audio from cfg._stream_buffer.
    Returns (audio_bytes, ext, n_ch) or (None, None, None).
    """
    if not _HAS_NP:
        return None, None, None
    try:
        buf  = getattr(cfg, "_stream_buffer", None)
        if buf is None:
            return None, None, None
        n_ch = getattr(cfg, "_audio_channels", 1) or 1
        pcm  = _chunks_to_pcm(buf, duration_s, n_ch)
        if not pcm:
            return None, None, None
        wav = _pcm_to_wav(pcm, n_ch)
        mp3 = _wav_to_mp3(wav) if len(wav) > 200_000 else None
        if mp3:
            return mp3, "mp3", n_ch
        return wav, "wav", n_ch
    except Exception:
        return None, None, None


# ── misc helpers ──────────────────────────────────────────────────────────────

def _safe(s):
    return re.sub(r"[^\w\-]", "_", str(s))[:40]


def _update_status(cap):
    n_sel   = len(cap["selections"])
    n_clips = len(cap["clips"])
    if n_clips >= n_sel:
        cap["status"] = "complete"
    elif n_clips > 0:
        cap["status"] = "partial"


# ── client-side command handler (non-hub nodes) ───────────────────────────────

def _handle_capture_cmd(cmd, monitor, hub_url, site):
    capture_id = cmd.get("capture_id")
    capture_at = cmd.get("capture_at", time.time())
    duration_s = cmd.get("duration_s", 30)
    streams    = cmd.get("streams", [])

    # Deduplicate: both heartbeat and poll may deliver the same capture
    if capture_id in _processed_captures:
        monitor.log(f"[SyncCap] Client: duplicate capture {capture_id} — ignored")
        return
    _processed_captures.add(capture_id)

    monitor.log(
        f"[SyncCap] Client received capture {capture_id}: "
        f"{len(streams)} stream(s), {duration_s} s, "
        f"capture_at T+{capture_at - time.time():.1f} s"
    )

    wait = capture_at - time.time()
    if 0 < wait <= 30:
        time.sleep(wait)

    for stream_name in streams:
        inp = next(
            (i for i in monitor.app_cfg.inputs if i.name == stream_name),
            None,
        )
        if not inp:
            monitor.log(
                f"[SyncCap] Client: stream '{stream_name}' not found in inputs "
                f"(capture {capture_id})"
            )
            continue
        audio, ext, n_ch = _capture_input(inp, duration_s)
        if not audio:
            monitor.log(
                f"[SyncCap] Client: no audio captured for '{stream_name}' "
                f"(capture {capture_id}) — buffer empty or numpy unavailable"
            )
            continue
        monitor.log(
            f"[SyncCap] Client: captured {len(audio)} bytes ({ext}) "
            f"for '{stream_name}', uploading to hub…"
        )
        try:
            url = f"{hub_url}/api/synccap/clip/{capture_id}"
            req = urllib.request.Request(
                url, data=audio, method="POST",
                headers={
                    "Content-Type": f"audio/{ext}",
                    "X-Site":       site,
                    "X-Stream":     stream_name,
                    "X-Channels":   str(n_ch or 1),
                    "X-Ext":        ext,
                },
            )
            urllib.request.urlopen(req, timeout=30).close()
            monitor.log(
                f"[SyncCap] Client: uploaded '{stream_name}' to hub OK "
                f"(capture {capture_id})"
            )
        except Exception as exc:
            monitor.log(
                f"[SyncCap] Client: upload failed for '{stream_name}' "
                f"(capture {capture_id}): {exc}"
            )


# ── register ──────────────────────────────────────────────────────────────────

def register(app, ctx):
    global _CLIP_DIR, _DB_PATH

    login_required = ctx["login_required"]
    csrf_protect   = ctx["csrf_protect"]
    monitor        = ctx["monitor"]
    hub_server     = ctx["hub_server"]
    BUILD          = ctx["BUILD"]

    _plugin_dir = os.path.dirname(os.path.abspath(__file__))
    _CLIP_DIR   = os.path.join(_plugin_dir, "synccap_clips")
    _DB_PATH    = os.path.join(_plugin_dir, "synccap_db.json")
    os.makedirs(_CLIP_DIR, exist_ok=True)

    cfg_ss  = monitor.app_cfg
    mode    = getattr(getattr(cfg_ss, "hub", None), "mode", "standalone") or "standalone"
    hub_url = (getattr(getattr(cfg_ss, "hub", None), "hub_url", "") or "").rstrip("/")

    # hub_server is always a HubServer() instance (never None in signalscope.py).
    # Use mode to distinguish hub from client nodes.
    is_hub    = mode in ("hub", "both")
    is_client = mode == "client" and bool(hub_url)

    # ── Client node: register heartbeat command handler, no routes ───────────
    if is_client:
        register_cmd_handler = ctx.get("register_cmd_handler")
        if register_cmd_handler:
            def _on_synccap_capture(payload):
                cfg2    = monitor.app_cfg
                h_url   = (getattr(getattr(cfg2, "hub", None), "hub_url", "") or "").rstrip("/")
                site2   = getattr(getattr(cfg2, "hub", None), "site_name", "") or ""
                threading.Thread(
                    target=_handle_capture_cmd,
                    args=(payload, monitor, h_url, site2),
                    daemon=True,
                    name="SyncCapCapture",
                ).start()
            register_cmd_handler("synccap_capture", _on_synccap_capture)
            monitor.log("[SyncCap] Client handler registered (heartbeat delivery)")
        else:
            # Fallback for cores older than 3.5.84: poll the hub poll endpoint
            monitor.log("[SyncCap] Core pre-3.5.84 — using poll fallback for capture delivery")
            def _poll_for_captures():
                last_err_log = 0.0
                while True:
                    try:
                        cfg2  = monitor.app_cfg
                        h_url = (getattr(getattr(cfg2, "hub", None), "hub_url", "") or "").rstrip("/")
                        site2 = getattr(getattr(cfg2, "hub", None), "site_name", "") or ""
                        if not h_url or not site2:
                            time.sleep(_CLIENT_POLL_S)
                            continue
                        req  = urllib.request.Request(
                            f"{h_url}/api/synccap/cmd",
                            headers={"X-Site": site2},
                        )
                        resp = urllib.request.urlopen(req, timeout=5)
                        data = json.loads(resp.read())
                        cmd  = data.get("cmd")
                        if cmd:
                            threading.Thread(
                                target=_handle_capture_cmd,
                                args=(cmd, monitor, h_url, site2),
                                daemon=True,
                                name="SyncCapCapture",
                            ).start()
                    except Exception as exc:
                        now = time.time()
                        if now - last_err_log > 30:
                            monitor.log(f"[SyncCap] Poll error: {exc}")
                            last_err_log = now
                    time.sleep(_CLIENT_POLL_S)

            threading.Thread(
                target=_poll_for_captures, daemon=True, name="SyncCapPoll"
            ).start()

    if not is_hub:
        return

    # ── Hub node: register all routes ─────────────────────────────────────────

    # ── Page ──────────────────────────────────────────────────────────────────
    @app.get("/hub/synccap")
    @login_required
    def synccap_page():
        return _render_page(BUILD)

    # ── Input list ────────────────────────────────────────────────────────────
    @app.get("/api/synccap/inputs")
    @login_required
    def synccap_inputs():
        result = []
        for inp in cfg_ss.inputs:
            result.append({
                "site":   "(hub)",
                "stream": inp.name,
                "type":   getattr(inp, "device_index", "") or "",
            })
        for site, sdata in hub_server._sites.items():
            if not sdata.get("_approved"):
                continue
            for s in sdata.get("streams", []):
                result.append({
                    "site":   site,
                    "stream": s.get("name", ""),
                    "type":   s.get("device_index", "") or "",
                })
        from flask import jsonify
        return jsonify(result)

    # ── Trigger ───────────────────────────────────────────────────────────────
    @app.post("/api/synccap/trigger")
    @login_required
    @csrf_protect
    def synccap_trigger():
        from flask import request, jsonify
        body       = request.get_json(silent=True) or {}
        label      = str(body.get("label", "")).strip()[:80] or "Capture"
        duration   = max(_MIN_DUR, min(_MAX_DUR, int(body.get("duration_s", 30))))
        selections = body.get("selections", [])
        if not selections:
            return jsonify({"error": "No inputs selected"}), 400

        capture_id   = uuid.uuid4().hex[:12]
        capture_at   = time.time() + 15  # 15 s so heartbeat (≤10 s cycle) has time to deliver
        triggered_at = time.time()

        cap = {
            "capture_id":   capture_id,
            "label":        label,
            "duration_s":   duration,
            "triggered_at": triggered_at,
            "capture_at":   capture_at,
            "selections":   selections,
            "clips":        [],
            "status":       "waiting",
        }

        with _db_lock:
            db = _load_db()
            db[capture_id] = cap
            _save_db(db)

        # Queue commands for remote client sites
        sites_needed = {}
        hub_streams  = []
        for sel in selections:
            if sel["site"] == "(hub)":
                hub_streams.append(sel["stream"])
            else:
                sites_needed.setdefault(sel["site"], []).append(sel["stream"])

        for site, streams in sites_needed.items():
            cmd_payload = {
                "capture_id": capture_id,
                "capture_at": capture_at,
                "duration_s": duration,
                "streams":    streams,
            }
            # Primary: deliver via heartbeat ACK (cores 3.5.84+)
            hub_server.push_pending_command(site, {
                "type":    "synccap_capture",
                "payload": cmd_payload,
            })
            # Fallback: also serve via /api/synccap/cmd poll (older cores)
            with _pending_lock:
                _pending_cmds[site] = (cmd_payload, time.time() + _EXPIRE_S)

        # Hub-local capture — wait until capture_at then grab stream buffers
        if hub_streams:
            def _do_hub_capture():
                wait = capture_at - time.time()
                if wait > 0:
                    time.sleep(wait)
                for stream_name in hub_streams:
                    inp = next(
                        (i for i in cfg_ss.inputs if i.name == stream_name), None
                    )
                    if not inp:
                        continue
                    audio, ext, n_ch = _capture_input(inp, duration)
                    if not audio:
                        monitor.log(
                            f"[SyncCap] Hub: no audio for '{stream_name}'"
                        )
                        continue
                    fname = (
                        f"{capture_id}_{_safe('hub')}_{_safe(stream_name)}.{ext}"
                    )
                    path = os.path.join(_CLIP_DIR, fname)
                    with open(path, "wb") as f:
                        f.write(audio)
                    with _db_lock:
                        db2 = _load_db()
                        if capture_id in db2:
                            db2[capture_id]["clips"].append({
                                "site":        "(hub)",
                                "stream":      stream_name,
                                "filename":    fname,
                                "n_ch":        n_ch or 1,
                                "received_at": time.time(),
                            })
                            _update_status(db2[capture_id])
                            _save_db(db2)
                    monitor.log(
                        f"[SyncCap] Hub captured '{stream_name}' → {fname}"
                    )

            threading.Thread(
                target=_do_hub_capture, daemon=True, name="SyncCapHub"
            ).start()

        # Expiry watchdog
        def _expire():
            time.sleep(_EXPIRE_S)
            with _db_lock:
                db2 = _load_db()
                if capture_id in db2 and db2[capture_id]["status"] in (
                    "waiting", "partial"
                ):
                    db2[capture_id]["status"] = "expired"
                    _save_db(db2)

        threading.Thread(
            target=_expire, daemon=True, name="SyncCapExpire"
        ).start()

        return jsonify({"capture_id": capture_id, "capture_at": capture_at})

    # ── Poll endpoint (fallback for old-core clients) ─────────────────────────
    # New cores receive commands via heartbeat ACK. Old cores (pre-3.5.84)
    # poll this endpoint.  Command is removed on first successful serve.
    @app.get("/api/synccap/cmd")
    def synccap_cmd_poll():
        from flask import request, jsonify
        site = request.headers.get("X-Site", "").strip()
        if not site:
            return jsonify({}), 400
        sdata = hub_server._sites.get(site, {})
        if not sdata.get("_approved"):
            return jsonify({}), 403
        with _pending_lock:
            entry = _pending_cmds.get(site)
            if entry:
                cmd, expiry = entry
                if time.time() > expiry:
                    del _pending_cmds[site]
                    return jsonify({})
                # Pop on first serve so we don't re-deliver to the same client
                del _pending_cmds[site]
                return jsonify({"cmd": cmd})
        return jsonify({})

    # ── Clip upload (clients POST here) ───────────────────────────────────────
    @app.post("/api/synccap/clip/<capture_id>")
    def synccap_upload_clip(capture_id):
        from flask import request, jsonify
        site        = request.headers.get("X-Site", "").strip()
        stream_name = request.headers.get("X-Stream", "").strip()
        n_ch        = int(request.headers.get("X-Channels", "1") or "1")
        ext         = request.headers.get("X-Ext", "wav")
        if not site or not stream_name:
            return jsonify({"error": "Missing headers"}), 400
        sdata = hub_server._sites.get(site, {})
        if not sdata.get("_approved"):
            return jsonify({"error": "Not approved"}), 403
        data = request.get_data()
        if not data:
            return jsonify({"error": "No audio data"}), 400
        with _db_lock:
            db = _load_db()
            if capture_id not in db:
                return jsonify({"error": "Unknown capture"}), 404
            fname = (
                f"{capture_id}_{_safe(site)}_{_safe(stream_name)}.{ext}"
            )
            path = os.path.join(_CLIP_DIR, fname)
            with open(path, "wb") as f:
                f.write(data)
            db[capture_id]["clips"].append({
                "site":        site,
                "stream":      stream_name,
                "filename":    fname,
                "n_ch":        n_ch,
                "received_at": time.time(),
            })
            _update_status(db[capture_id])
            _save_db(db)
        monitor.log(
            f"[SyncCap] Received clip from {site}/{stream_name} → {fname}"
        )
        return jsonify({"ok": True})

    # ── Status poll ───────────────────────────────────────────────────────────
    @app.get("/api/synccap/status/<capture_id>")
    @login_required
    def synccap_status(capture_id):
        from flask import jsonify
        with _db_lock:
            db  = _load_db()
            cap = db.get(capture_id)
        if not cap:
            return jsonify({"error": "Not found"}), 404
        return jsonify(cap)

    # ── Capture list ──────────────────────────────────────────────────────────
    @app.get("/api/synccap/captures")
    @login_required
    def synccap_list():
        from flask import jsonify
        with _db_lock:
            db = _load_db()
        caps = sorted(db.values(), key=lambda c: c["triggered_at"], reverse=True)
        return jsonify(caps[:60])

    # ── Serve clip (with Range support for browser <audio>) ───────────────────
    @app.get("/api/synccap/clip/<capture_id>/<path:filename>")
    @login_required
    def synccap_serve_clip(capture_id, filename):
        from flask import request
        filename = os.path.basename(filename)
        if not filename.startswith(capture_id):
            return "Forbidden", 403
        path = os.path.join(_CLIP_DIR, filename)
        if not os.path.exists(path):
            return "Not found", 404
        ext  = filename.rsplit(".", 1)[-1].lower()
        mime = "audio/mpeg" if ext == "mp3" else "audio/wav"
        data      = open(path, "rb").read()
        file_size = len(data)
        range_hdr = request.headers.get("Range")
        if range_hdr:
            try:
                parts = range_hdr.replace("bytes=", "").split("-")
                sta   = int(parts[0]) if parts[0] else 0
                end   = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1
                end   = min(end, file_size - 1)
                body  = data[sta:end + 1]
                resp  = app.response_class(body, status=206, mimetype=mime)
                resp.headers["Content-Range"]  = f"bytes {sta}-{end}/{file_size}"
                resp.headers["Accept-Ranges"]  = "bytes"
                resp.headers["Content-Length"] = str(len(body))
                return resp
            except Exception:
                pass
        resp = app.response_class(data, status=200, mimetype=mime)
        resp.headers["Accept-Ranges"]  = "bytes"
        resp.headers["Content-Length"] = str(file_size)
        return resp

    # ── Delete capture ────────────────────────────────────────────────────────
    @app.delete("/api/synccap/capture/<capture_id>")
    @login_required
    @csrf_protect
    def synccap_delete(capture_id):
        from flask import jsonify
        with _db_lock:
            db  = _load_db()
            cap = db.pop(capture_id, None)
            if cap:
                _save_db(db)
        if cap:
            for clip in cap.get("clips", []):
                p = os.path.join(_CLIP_DIR, clip["filename"])
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass
        return jsonify({"ok": bool(cap)})


# ── page template ─────────────────────────────────────────────────────────────

def _render_page(BUILD):
    from flask import render_template_string
    return render_template_string(_PAGE_TPL, BUILD=BUILD)


_PAGE_TPL = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<title>Sync Capture — {{BUILD}}</title>
<style nonce="{{csp_nonce()}}">
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(circle at top,#12376f 0%,var(--bg) 38%,#05101f 100%);color:var(--tx);font-family:system-ui,sans-serif;font-size:13px;min-height:100vh}
header{background:linear-gradient(180deg,rgba(10,31,65,.96),rgba(9,24,48,.96));border-bottom:1px solid var(--bor);padding:12px 20px;display:flex;align-items:center;gap:12px}
header a{color:var(--acc);text-decoration:none;font-size:12px}
h1{font-size:15px;font-weight:700}
.wrap{max-width:1240px;margin:0 auto;padding:20px;display:grid;grid-template-columns:320px 1fr;gap:20px}
@media(max-width:780px){.wrap{grid-template-columns:1fr}}
.card{background:var(--sur);border:1px solid var(--bor);border-radius:12px;overflow:hidden;margin-bottom:16px}
.ch{padding:9px 14px;display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--bor);background:linear-gradient(180deg,#143766,#102b54);font-size:12px;font-weight:700;color:var(--acc);text-transform:uppercase;letter-spacing:.06em}
.cb{padding:14px}
.btn{border:none;border-radius:8px;padding:5px 12px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit}
.btn:hover{filter:brightness(1.15)}
.btn:disabled{opacity:.4;cursor:not-allowed}
.bp{background:var(--acc);color:#07142b}
.bd{background:var(--al);color:#fff}
.bg{background:#142242;color:var(--tx)}
.bs{padding:3px 9px;font-size:11px}
.field{display:flex;flex-direction:column;gap:4px;margin-bottom:12px}
.field label{font-size:11px;color:var(--mu);font-weight:600;text-transform:uppercase;letter-spacing:.05em}
input[type=text],input[type=number]{background:#0d1e40;border:1px solid var(--bor);border-radius:6px;color:var(--tx);padding:6px 9px;font-family:inherit;font-size:13px;width:100%}
input:focus{border-color:var(--acc);outline:none}
/* stream selector */
.stream-list{max-height:360px;overflow-y:auto;display:flex;flex-direction:column;gap:3px}
.site-group{margin-bottom:4px}
.site-hdr{font-size:10px;font-weight:700;color:var(--mu);text-transform:uppercase;letter-spacing:.07em;padding:4px 4px 2px;border-bottom:1px solid rgba(23,52,95,.5);margin-bottom:2px}
.stream-item{display:flex;align-items:center;gap:7px;padding:5px 7px;border-radius:6px;cursor:pointer;border:1px solid transparent;transition:background .12s}
.stream-item:hover{background:rgba(23,52,95,.4)}
.stream-item.sel{background:rgba(23,168,255,.12);border-color:rgba(23,168,255,.3)}
.stream-item input[type=checkbox]{accent-color:var(--acc);width:13px;height:13px;flex-shrink:0;cursor:pointer}
.stream-name{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:12px}
.type-badge{font-size:9px;background:#0d1e40;border:1px solid var(--bor);border-radius:3px;padding:0 4px;color:var(--mu);flex-shrink:0}
#sel-count{font-size:11px;color:var(--mu)}
/* duration */
.dur-wrap{display:flex;align-items:center;gap:8px}
.dur-wrap input[type=range]{flex:1;accent-color:var(--acc)}
.dur-val{font-size:14px;color:var(--acc);font-weight:700;width:48px;text-align:right;flex-shrink:0}
/* progress */
.prog-row{display:flex;align-items:center;gap:10px;padding:7px 0;border-bottom:1px solid rgba(23,52,95,.4)}
.prog-row:last-child{border:none}
.prog-st{width:70px;flex-shrink:0;font-size:11px;font-weight:600}
.st-wait{color:var(--mu)}
.st-ok{color:var(--ok)}
.st-exp{color:var(--wn)}
.prog-site{font-size:11px;color:var(--mu);width:90px;flex-shrink:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.prog-stream{flex:1;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
/* history table */
table{width:100%;border-collapse:collapse}
th{color:var(--mu);font-size:11px;text-transform:uppercase;letter-spacing:.04em;padding:6px 10px;text-align:left;border-bottom:1px solid var(--bor)}
td{padding:7px 10px;border-bottom:1px solid rgba(23,52,95,.4);font-size:12px}
.cap-row{cursor:pointer;transition:background .1s}
.cap-row:hover td{background:rgba(23,52,95,.3)}
.badge{font-size:10px;border-radius:4px;padding:1px 6px;font-weight:600}
.b-ok{background:rgba(34,197,94,.15);color:var(--ok)}
.b-wn{background:rgba(245,158,11,.15);color:var(--wn)}
.b-mu{background:rgba(138,164,200,.12);color:var(--mu)}
.b-al{background:rgba(239,68,68,.15);color:var(--al)}
/* clips panel */
.clips-panel{background:#050d1e;border-top:1px solid var(--bor)}
.clips-grid{padding:14px;display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}
.clip-card{background:var(--sur);border:1px solid var(--bor);border-radius:8px;padding:12px}
.clip-site{font-size:10px;color:var(--mu);margin-bottom:2px}
.clip-stream{font-size:13px;font-weight:600;margin-bottom:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
audio{width:100%;height:30px}
.no-clips{color:var(--mu);font-size:12px;padding:10px}
/* message */
#msg{display:none;padding:8px 12px;border-radius:6px;margin-bottom:12px;font-size:12px}
.msg-ok{background:#0f2318;color:var(--ok);border:1px solid #166534}
.msg-err{background:#2a0a0a;color:var(--al);border:1px solid #991b1b}
/* shimmer */
.shimmer{position:relative;overflow:hidden}
.shimmer::after{content:'';position:absolute;inset:0;background:linear-gradient(90deg,transparent 0%,rgba(255,255,255,.08) 50%,transparent 100%);background-size:200% 100%;animation:shim 1.2s infinite linear}
@keyframes shim{0%{background-position:200% 0}100%{background-position:-200% 0}}
</style>
</head>
<body>
<header>
  <a href="/hub">← Hub</a>
  <h1>🎙 Sync Capture</h1>
  <span style="margin-left:auto;font-size:11px;color:var(--mu)">{{BUILD}}</span>
</header>

<div class="wrap">

  <!-- ── Left column: selector + settings ── -->
  <div>
    <div class="card">
      <div class="ch">
        Select Inputs
        <span id="sel-count" style="margin-left:auto;font-weight:400">0 selected</span>
      </div>
      <div class="cb">
        <div id="msg"></div>
        <div style="display:flex;gap:6px;margin-bottom:8px">
          <button class="btn bg bs" id="btn-all">All</button>
          <button class="btn bg bs" id="btn-none">Clear</button>
          <button class="btn bg bs" id="btn-reload" style="margin-left:auto">↻</button>
        </div>
        <div class="stream-list" id="stream-list">
          <div style="color:var(--mu);padding:8px 0;font-size:12px">Loading…</div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="ch">Capture Settings</div>
      <div class="cb">
        <div class="field">
          <label>Label</label>
          <input type="text" id="cap-label" placeholder="e.g. 09:00 Morning check" maxlength="80" spellcheck="false" autocomplete="off">
        </div>
        <div class="field">
          <label>Duration — <span id="dur-display">30 s</span></label>
          <div class="dur-wrap">
            <input type="range" id="cap-dur" min="5" max="300" value="30">
            <span class="dur-val" id="dur-val">30s</span>
          </div>
        </div>
        <button class="btn bp" id="cap-btn" style="width:100%;padding:8px">⏺ Capture</button>
      </div>
    </div>
  </div>

  <!-- ── Right column: progress + history ── -->
  <div>
    <div class="card" id="prog-card" style="display:none">
      <div class="ch" id="prog-title">Collecting clips…</div>
      <div class="cb" id="prog-body"></div>
    </div>

    <div class="card">
      <div class="ch">
        Capture History
        <button class="btn bg bs" id="btn-refresh" style="margin-left:auto">↻ Refresh</button>
      </div>
      <div style="overflow-x:auto">
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Label</th>
              <th style="white-space:nowrap">Duration</th>
              <th>Clips</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody id="cap-tbody">
            <tr><td colspan="6" style="color:var(--mu);text-align:center;padding:20px">No captures yet</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

</div><!-- /wrap -->

<script nonce="{{csp_nonce()}}">
(function(){
'use strict';

var _inputs   = [];
var _selected = new Set();
var _pollTimer = null;
var _activeId  = null;

// ── csrf / msg ─────────────────────────────────────────────────────────────
function _csrf(){
  return (document.querySelector('meta[name="csrf-token"]')||{}).content
      || (document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/)||[])[1] || '';
}
function showMsg(txt, ok){
  var el=document.getElementById('msg');
  el.textContent=txt; el.style.display='block';
  el.className=ok?'msg-ok':'msg-err';
  if(ok) setTimeout(function(){el.style.display='none'},4000);
}
function esc(s){
  return String(s||'')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── duration slider ────────────────────────────────────────────────────────
document.getElementById('cap-dur').addEventListener('input', function(){
  var v=this.value;
  document.getElementById('dur-val').textContent=v+'s';
  document.getElementById('dur-display').textContent=v+' s';
});

// ── input loading ──────────────────────────────────────────────────────────
function loadInputs(){
  document.getElementById('stream-list').innerHTML=
    '<div style="color:var(--mu);padding:8px 0;font-size:12px">Loading…</div>';
  fetch('/api/synccap/inputs',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(data){_inputs=data; renderInputs();})
    .catch(function(){
      document.getElementById('stream-list').innerHTML=
        '<div style="color:var(--al);padding:8px 0;font-size:12px">Failed to load inputs</div>';
    });
}

function renderInputs(){
  var list = document.getElementById('stream-list');
  if(!_inputs.length){
    list.innerHTML='<div style="color:var(--mu);padding:8px 0;font-size:12px">No inputs found</div>';
    updateCount(); return;
  }
  // Group by site
  var bySite={};
  _inputs.forEach(function(inp){
    (bySite[inp.site] = bySite[inp.site]||[]).push(inp);
  });
  var html='';
  Object.keys(bySite).sort(function(a,b){
    if(a==='(hub)') return -1; if(b==='(hub)') return 1;
    return a.localeCompare(b);
  }).forEach(function(site){
    html+='<div class="site-group"><div class="site-hdr">'+esc(site)+'</div>';
    bySite[site].forEach(function(inp,i){
      var key=inp.site+'|||'+inp.stream;
      var chk=_selected.has(key);
      var tl=_typeLabel(inp.type);
      html+='<div class="stream-item'+(chk?' sel':'')+'" data-key="'+esc(key)+'">'
        +'<input type="checkbox"'+(chk?' checked':'')+' aria-label="'+esc(inp.stream)+'">'
        +'<span class="stream-name">'+esc(inp.stream)+'</span>'
        +(tl?'<span class="type-badge">'+esc(tl)+'</span>':'')
        +'</div>';
    });
    html+='</div>';
  });
  list.innerHTML=html;
  // Delegate clicks
  list.querySelectorAll('.stream-item').forEach(function(div){
    div.addEventListener('click', function(e){
      var key=div.dataset.key;
      if(e.target.tagName==='INPUT'){
        // checkbox changed
        if(e.target.checked) _selected.add(key); else _selected.delete(key);
        div.classList.toggle('sel', _selected.has(key));
      } else {
        var cb=div.querySelector('input[type=checkbox]');
        if(_selected.has(key)){_selected.delete(key); cb.checked=false;}
        else{_selected.add(key); cb.checked=true;}
        div.classList.toggle('sel', _selected.has(key));
      }
      updateCount();
    });
  });
  updateCount();
}

function _typeLabel(t){
  if(!t) return '';
  var tl=(t||'').toLowerCase();
  if(tl.startsWith('fm://'))   return 'FM';
  if(tl.startsWith('dab://'))  return 'DAB';
  if(tl.startsWith('http'))    return 'HTTP';
  if(tl.startsWith('rtp://'))  return 'RTP';
  if(tl.startsWith('alsa://')) return 'ALSA';
  return '';
}
function updateCount(){
  document.getElementById('sel-count').textContent=_selected.size+' selected';
}

document.getElementById('btn-all').addEventListener('click', function(){
  _inputs.forEach(function(inp){_selected.add(inp.site+'|||'+inp.stream);});
  renderInputs();
});
document.getElementById('btn-none').addEventListener('click', function(){
  _selected.clear(); renderInputs();
});
document.getElementById('btn-reload').addEventListener('click', loadInputs);

// ── trigger capture ────────────────────────────────────────────────────────
document.getElementById('cap-btn').addEventListener('click', triggerCapture);

function triggerCapture(){
  if(!_selected.size){ showMsg('Select at least one input first', false); return; }
  var selections=[];
  _selected.forEach(function(key){
    var p=key.split('|||'); selections.push({site:p[0], stream:p[1]});
  });
  var label = document.getElementById('cap-label').value.trim()||'Capture';
  var dur   = parseInt(document.getElementById('cap-dur').value, 10);
  var btn   = document.getElementById('cap-btn');
  btn.disabled=true; btn.classList.add('shimmer'); btn.textContent='Triggering…';

  fetch('/api/synccap/trigger',{
    method:'POST', credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRFToken':_csrf()},
    body: JSON.stringify({label:label, duration_s:dur, selections:selections})
  })
  .then(function(r){return r.json();})
  .then(function(d){
    btn.disabled=false; btn.classList.remove('shimmer'); btn.textContent='⏺ Capture';
    if(d.error){ showMsg(d.error, false); return; }
    showMsg('Capture triggered — waiting for clips…', true);
    _activeId = d.capture_id;
    startProgress(d.capture_id, selections);
  })
  .catch(function(){
    btn.disabled=false; btn.classList.remove('shimmer'); btn.textContent='⏺ Capture';
    showMsg('Request failed — check connection', false);
  });
}

// ── progress panel ─────────────────────────────────────────────────────────
function startProgress(capId, selections){
  var card  = document.getElementById('prog-card');
  var title = document.getElementById('prog-title');
  var body  = document.getElementById('prog-body');
  card.style.display='';
  title.textContent='Collecting clips…';
  body.innerHTML = selections.map(function(s){
    return '<div class="prog-row" id="pr_'+_rowKey(s.site,s.stream)+'">'
      +'<span class="prog-st st-wait">Waiting</span>'
      +'<span class="prog-site">'+esc(s.site)+'</span>'
      +'<span class="prog-stream">'+esc(s.stream)+'</span>'
      +'</div>';
  }).join('');
  if(_pollTimer) clearInterval(_pollTimer);
  _pollTimer = setInterval(function(){ pollProgress(capId, selections); }, 2000);
}

function _rowKey(site, stream){
  return (site+'__'+stream).replace(/[^a-zA-Z0-9]/g,'_');
}

function pollProgress(capId, selections){
  fetch('/api/synccap/status/'+capId, {credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(cap){
      var got={};
      (cap.clips||[]).forEach(function(cl){ got[cl.site+'|||'+cl.stream]=true; });
      selections.forEach(function(s){
        var key=s.site+'|||'+s.stream;
        var row=document.getElementById('pr_'+_rowKey(s.site,s.stream));
        if(!row) return;
        var st=row.querySelector('.prog-st');
        if(got[key]){
          st.textContent='✓ Ready'; st.className='prog-st st-ok';
        } else if(cap.status==='expired'){
          st.textContent='Expired'; st.className='prog-st st-exp';
        }
      });
      if(cap.status==='complete'||cap.status==='expired'){
        clearInterval(_pollTimer); _pollTimer=null;
        document.getElementById('prog-title').textContent =
          cap.status==='complete'?'Capture complete ✓':'Capture expired — partial results';
        loadCaptures(capId);  // pass capId to auto-expand
      }
    })
    .catch(function(){});
}

// ── history table ──────────────────────────────────────────────────────────
document.getElementById('btn-refresh').addEventListener('click', function(){ loadCaptures(); });

function loadCaptures(autoExpandId){
  fetch('/api/synccap/captures',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(caps){
      renderHistory(caps);
      // Auto-expand the latest capture row when triggered by completion
      if(autoExpandId){
        var panel = document.getElementById('clips_'+autoExpandId);
        if(panel) panel.style.display='';
      }
    })
    .catch(function(){});
}

function renderHistory(caps){
  var tbody = document.getElementById('cap-tbody');
  if(!caps.length){
    tbody.innerHTML='<tr><td colspan="6" style="color:var(--mu);text-align:center;padding:20px">No captures yet</td></tr>';
    return;
  }
  var BADGE={
    complete:'<span class="badge b-ok">Complete</span>',
    partial: '<span class="badge b-wn">Partial</span>',
    waiting: '<span class="badge b-mu">Waiting</span>',
    expired: '<span class="badge b-al">Expired</span>',
  };
  tbody.innerHTML = caps.map(function(cap){
    var d   = new Date(cap.triggered_at*1000);
    var ds  = d.toLocaleDateString([],{month:'short',day:'numeric'});
    var ts  = d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',second:'2-digit'});
    var nSel = cap.selections.length;
    var nCl  = cap.clips.length;
    var badge= BADGE[cap.status]||cap.status;
    var capJson = esc(JSON.stringify(cap));
    return '<tr class="cap-row" data-capid="'+cap.capture_id+'" data-cap="'+capJson+'">'
      +'<td style="white-space:nowrap"><span style="color:var(--mu);font-size:11px">'+ds+'</span><br>'+ts+'</td>'
      +'<td>'+esc(cap.label)+'</td>'
      +'<td style="white-space:nowrap">'+cap.duration_s+' s</td>'
      +'<td style="white-space:nowrap">'+nCl+' / '+nSel+'</td>'
      +'<td>'+badge+'</td>'
      +'<td><button class="btn bd bs" data-del="'+cap.capture_id+'">✕</button></td>'
      +'</tr>'
      +'<tr id="clips_'+cap.capture_id+'" style="display:none">'
      +'<td colspan="6" class="clips-panel">'+(
        cap.clips.length
          ? '<div class="clips-grid">'+cap.clips.map(function(cl){
              var src='/api/synccap/clip/'+cap.capture_id+'/'+encodeURIComponent(cl.filename);
              var ch=cl.n_ch===2?'<span class="badge b-mu" style="font-size:9px;margin-left:4px">STEREO</span>':'';
              return '<div class="clip-card">'
                +'<div class="clip-site">'+esc(cl.site)+'</div>'
                +'<div class="clip-stream">'+esc(cl.stream)+ch+'</div>'
                +'<audio controls preload="none" src="'+src+'"></audio>'
                +'</div>';
            }).join('')+'</div>'
          : '<div class="no-clips">No clips received for this capture.</div>'
      )+'</td></tr>';
  }).join('');
}

// Delegate table clicks
document.getElementById('cap-tbody').addEventListener('click', function(e){
  var delBtn = e.target.closest('[data-del]');
  if(delBtn){
    e.stopPropagation();
    if(!window.confirm('Delete this capture and all its clips?')) return;
    deleteCapture(delBtn.dataset.del);
    return;
  }
  var row = e.target.closest('.cap-row');
  if(!row) return;
  var capId   = row.dataset.capid;
  var panel   = document.getElementById('clips_'+capId);
  if(!panel) return;
  panel.style.display = panel.style.display==='none'?'':'none';
});

function deleteCapture(capId){
  fetch('/api/synccap/capture/'+capId,{
    method:'DELETE', credentials:'same-origin',
    headers:{'X-CSRFToken':_csrf()}
  })
  .then(function(r){return r.json();})
  .then(function(d){ if(d.ok) loadCaptures(); })
  .catch(function(){});
}

// ── init ───────────────────────────────────────────────────────────────────
loadInputs();
loadCaptures();

})();
</script>
</body>
</html>
"""
