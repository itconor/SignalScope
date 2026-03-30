"""sdr.py — WebSDR plugin for SignalScope
Drop this file alongside signalscope.py on BOTH the hub machine and the
client machine that has the RTL-SDR dongle attached.

Requirements (client machine):
    pip install numpy scipy          # scipy optional but recommended
    rtl_sdr must be in PATH          # part of rtl-sdr package

Architecture (Option A — server-side demodulation):
    Client  → rtl_sdr IQ → demodulate (numpy/scipy) → PCM chunks → hub relay
    Client  → FFT of IQ  → spectrum frames → hub spectrum buffer
    Hub     → streams PCM to browser (existing audio relay infrastructure)
    Hub     → serves latest FFT frame to browser waterfall poller
    Browser → waterfall canvas + audio player + tune/mode commands
"""

SIGNALSCOPE_PLUGIN = {
    "id":       "websdr",
    "label":    "Web SDR",
    "url":      "/hub/sdr",
    "icon":     "📡",
    "hub_only": True,   # only inject nav item in hub / both mode
    "version":  "1.2.0",
}

import hashlib
import hmac as _hmac
import os
import queue
import struct
import threading
import time
import urllib.request

import numpy as np

# ── Optional scipy (better resampling + filtering) ─────────────────────────────
try:
    from scipy import signal as _sp
    _HAS_SCIPY = True
except ImportError:
    _sp = None
    _HAS_SCIPY = False

# ── SDR parameters ─────────────────────────────────────────────────────────────
_SAMPLE_RATE  = 1_024_000   # RTL-SDR IQ sample rate
_OUT_RATE     = 48_000      # PCM output rate to browser
_UP, _DN      = 3, 64       # resample ratio: 48000/1024000 reduced → 3/64
_BLK_SAMPS    = 102_400     # 0.1 s of IQ at 1.024 MSPS
_BLK_BYTES    = _BLK_SAMPS * 2   # 2 bytes per IQ sample (uint8 I + uint8 Q)
_BLK_DUR      = _BLK_SAMPS / _SAMPLE_RATE   # 0.1 s
_PCM_BLKSIZE  = int(_OUT_RATE * _BLK_DUR) * 2  # 9600 bytes — matches scanner

# ── Module-level state ─────────────────────────────────────────────────────────
_hub_sessions    = {}   # site_name → {slot_id, freq_mhz, mode, sdr_serial}
_hub_pending     = {}   # site_name → pending command dict for client poller
_sdr_spectrum    = {}   # slot_id   → {fft, cf, bw, n, level, ts}
_sdr_scan_results= {}   # site_name → {peaks, start_mhz, end_mhz, step_khz, ts}
_client_sess     = {}   # {stop: Event, thread: Thread, slot_id: str}
_state_lock      = threading.Lock()
_monitor         = None  # set in register(); used by module-level worker for logging


# ──────────────────────────────────────────────────────────────────────────────
# Plugin entry point
# ──────────────────────────────────────────────────────────────────────────────

def register(app, ctx):
    from flask import request, jsonify, render_template_string

    global _monitor
    monitor         = ctx["monitor"]
    _monitor        = monitor
    hub_server      = ctx.get("hub_server")
    listen_registry = ctx["listen_registry"]
    login_required  = ctx["login_required"]
    csrf_protect    = ctx["csrf_protect"]

    # ── Hub: browser page ────────────────────────────────────────────────────
    @app.get("/hub/sdr")
    @login_required
    def websdr_page():
        sites = []
        cfg = monitor.app_cfg
        # Include the hub machine itself when running in hub/both mode
        # (it won't appear in hub_server._sites which only contains remote clients)
        local_mode = cfg.hub.mode  # "client" | "hub" | "both"
        if local_mode in ("hub", "both"):
            local_serials = [d.serial for d in cfg.sdr_devices
                             if d.role == "scanner"]
            if local_serials:
                local_name = cfg.hub.site_name or "local"
                sites.append({"name": local_name, "online": True,
                               "serials": local_serials, "local": True})
        if hub_server:
            now = time.time()
            with hub_server._lock:
                for sname, sdata in sorted(hub_server._sites.items()):
                    if sdata.get("_approved") and not sdata.get("blocked"):
                        serials = sdata.get("scanner_serials", [])
                        online  = (now - sdata.get("_received", 0)) < 120
                        # Show all connected sites; sites without scanner dongles
                        # are shown disabled so the user knows they are seen but
                        # need a dongle configured with role=Scanner on the client.
                        sites.append({"name": sname, "online": online,
                                      "serials": serials, "local": False})
        return render_template_string(WEBSDR_TPL, sites=sites,
                                      build=ctx["BUILD"])

    # ── Hub: start session ───────────────────────────────────────────────────
    @app.post("/api/hub/sdr/start")
    @login_required
    @csrf_protect
    def websdr_start():
        data       = request.get_json(silent=True) or {}
        site       = str(data.get("site", "")).strip()
        freq_mhz   = float(data.get("freq_mhz", 96.5) or 96.5)
        mode       = str(data.get("mode", "wfm") or "wfm").lower()
        gain       = data.get("gain", "auto")
        sdr_serial = str(data.get("sdr_serial", "") or "")

        if not site or not hub_server:
            return jsonify({"ok": False, "error": "site required"}), 400

        # Expire old session
        old = _hub_sessions.pop(site, None)
        if old:
            old_slot = listen_registry.get(old["slot_id"])
            if old_slot:
                old_slot.closed = True

        slot = listen_registry.create(
            site, 0, kind="scanner", mimetype="application/octet-stream",
            freq_mhz=freq_mhz,
        )
        with _state_lock:
            _hub_sessions[site] = {"slot_id": slot.slot_id,
                                   "freq_mhz": freq_mhz, "mode": mode,
                                   "sdr_serial": sdr_serial}
            _hub_pending[site] = {"action": "start",
                                  "slot_id": slot.slot_id,
                                  "freq_mhz": freq_mhz,
                                  "mode": mode,
                                  "gain": gain,
                                  "sdr_serial": sdr_serial}
        # Register in _scanner_sessions so the relay generator buffers PCM for recording
        if hub_server and hasattr(hub_server, "_scanner_pcm"):
            from collections import deque as _deque
            hub_server._scanner_pcm.setdefault(site, _deque(maxlen=600))
            if hasattr(hub_server, "_scanner_sessions"):
                hub_server._scanner_sessions[site] = {"slot_id": slot.slot_id,
                                                       "sdr_serial": sdr_serial}
        return jsonify({"ok": True, "slot_id": slot.slot_id,
                        "stream_url": f"/hub/scanner/stream/{slot.slot_id}"})

    # ── Hub: retune ──────────────────────────────────────────────────────────
    @app.post("/api/hub/sdr/tune")
    @login_required
    @csrf_protect
    def websdr_tune():
        data     = request.get_json(silent=True) or {}
        site     = str(data.get("site", "")).strip()
        freq_mhz = float(data.get("freq_mhz", 96.5) or 96.5)
        mode     = str(data.get("mode", "wfm") or "wfm").lower()

        sess = _hub_sessions.get(site)
        if not sess:
            return jsonify({"ok": False, "error": "no active session"}), 404

        old_slot = listen_registry.get(sess["slot_id"])
        if old_slot:
            old_slot.closed = True

        slot = listen_registry.create(
            site, 0, kind="scanner", mimetype="application/octet-stream",
            freq_mhz=freq_mhz,
        )
        with _state_lock:
            sess["slot_id"]  = slot.slot_id
            sess["freq_mhz"] = freq_mhz
            sess["mode"]     = mode
            _hub_pending[site] = {"action": "tune",
                                  "slot_id": slot.slot_id,
                                  "freq_mhz": freq_mhz,
                                  "mode": mode,
                                  "gain": sess.get("gain", "auto"),
                                  "sdr_serial": sess.get("sdr_serial", "")}
        # Keep _scanner_sessions in sync so PCM buffer keeps being filled
        if hub_server and hasattr(hub_server, "_scanner_sessions"):
            hub_server._scanner_sessions[site] = {"slot_id": slot.slot_id,
                                                   "sdr_serial": sess.get("sdr_serial", "")}
        return jsonify({"ok": True, "slot_id": slot.slot_id,
                        "stream_url": f"/hub/scanner/stream/{slot.slot_id}"})

    # ── Hub: stop ────────────────────────────────────────────────────────────
    @app.post("/api/hub/sdr/stop")
    @login_required
    @csrf_protect
    def websdr_stop():
        data = request.get_json(silent=True) or {}
        site = str(data.get("site", "")).strip()
        with _state_lock:
            sess = _hub_sessions.pop(site, None)
            _hub_pending[site] = {"action": "stop"}
        if sess:
            old_slot = listen_registry.get(sess["slot_id"])
            if old_slot:
                old_slot.closed = True
        if hub_server and hasattr(hub_server, "_scanner_sessions"):
            hub_server._scanner_sessions.pop(site, None)
        return jsonify({"ok": True})

    # ── Hub: browser polls spectrum ──────────────────────────────────────────
    @app.get("/api/hub/sdr/spectrum/<slot_id>")
    @login_required
    def websdr_spectrum_get(slot_id):
        return jsonify(_sdr_spectrum.get(slot_id, {}))

    # ── Hub: client polls for commands ───────────────────────────────────────
    # No login_required — authenticated by X-Sdr-Site matching an approved site
    @app.get("/api/hub/sdr/cmd")
    def websdr_cmd_poll():
        if not hub_server:
            return jsonify({}), 200
        site = request.headers.get("X-Sdr-Site", "").strip()
        if not site:
            return jsonify({}), 400
        sdata = hub_server._sites.get(site, {})
        if not sdata.get("_approved"):
            return jsonify({}), 403
        with _state_lock:
            cmd = _hub_pending.pop(site, None)
        return jsonify({"cmd": cmd} if cmd else {})

    # ── Client: receives spectrum frames pushed from the SDR worker ──────────
    # Authenticated by HMAC-SHA256 (X-Hub-Sig / X-Hub-Ts) when a secret is set.
    @app.post("/api/hub/sdr/spectrum/<slot_id>")
    def websdr_spectrum_push(slot_id):
        # Validate that this is a known active slot before accepting data
        if slot_id not in _sdr_spectrum and slot_id not in {
            s.get("slot_id", "") for s in _hub_sessions.values()
        }:
            # Also allow pushes from the local _client_sess slot
            local_slot = _client_sess.get("slot_id", "")
            if slot_id != local_slot:
                return jsonify({"error": "unknown slot"}), 404

        # HMAC authentication — verify when a secret is configured AND the
        # caller included a signature header.  If sig is absent we allow the
        # request through (backward compat with pre-3.4.68 sdr.py clients
        # that don't yet send X-Hub-Sig for spectrum frames); if sig is
        # present but wrong we reject immediately.
        cfg    = monitor.app_cfg
        secret = cfg.hub.secret_key
        raw_body = request.get_data()
        sig  = request.headers.get("X-Hub-Sig", "")
        if secret and sig:
            ts_h = request.headers.get("X-Hub-Ts", "0")
            try:
                ts = float(ts_h)
            except ValueError:
                return jsonify({"error": "invalid timestamp"}), 403
            if abs(time.time() - ts) > 300:
                return jsonify({"error": "timestamp out of window"}), 403
            key = hashlib.sha256(f"{secret}:signing".encode()).digest()
            msg = f"{ts:.0f}:".encode() + raw_body
            expected = _hmac.new(key, msg, hashlib.sha256).hexdigest()
            if not _hmac.compare_digest(expected, sig):
                return jsonify({"error": "forbidden"}), 403
        import json as _json
        try:
            d = _json.loads(raw_body) if raw_body else {}
        except Exception:
            return jsonify({"error": "bad json"}), 400

        if d:
            d["ts"] = time.time()
            _sdr_spectrum[slot_id] = d
            # Prune stale entries (> 300 slots is excessive)
            if len(_sdr_spectrum) > 300:
                oldest = sorted(_sdr_spectrum, key=lambda k: _sdr_spectrum[k].get("ts",0))
                for k in oldest[:100]:
                    _sdr_spectrum.pop(k, None)
        return "", 204

    # ── Hub: browser requests band scan ─────────────────────────────────────
    @app.post("/api/hub/sdr/band_scan")
    @login_required
    @csrf_protect
    def websdr_band_scan():
        data       = request.get_json(silent=True) or {}
        site       = str(data.get("site", "")).strip()
        if not site:
            return jsonify({"ok": False, "error": "site required"}), 400
        sdr_serial = str(data.get("sdr_serial", "") or "")
        _sdr_scan_results.pop(site, None)   # clear stale result
        with _state_lock:
            _hub_pending[site] = {
                "action":     "band_scan",
                "start_mhz":  float(data.get("start_mhz",  76.0)),
                "end_mhz":    float(data.get("end_mhz",   108.0)),
                "step_khz":   int(data.get("step_khz",     100)),
                "gain":       data.get("gain", "auto"),
                "sdr_serial": sdr_serial,
            }
        monitor.log(f"[WebSDR] Band scan queued for site '{site}'")
        return jsonify({"ok": True})

    # ── Client → Hub: band scan result ───────────────────────────────────────
    # No login_required — authenticated by X-Sdr-Site + approved-site check
    @app.post("/api/hub/sdr/scan_result")
    def websdr_scan_result_push():
        site = request.headers.get("X-Sdr-Site", "").strip()
        if not site:
            return jsonify({"error": "missing site"}), 400
        if hub_server:
            sdata = hub_server._sites.get(site, {})
            if not sdata.get("_approved"):
                return jsonify({"error": "forbidden"}), 403
        d = request.get_json(silent=True) or {}
        if d:
            _sdr_scan_results[site] = {**d, "ts": time.time()}
            monitor.log(f"[WebSDR] Scan result from '{site}': "
                        f"{len(d.get('peaks', []))} readings")
        return "", 204

    # ── Hub: browser polls for scan result ───────────────────────────────────
    @app.get("/api/hub/sdr/scan_result/<path:site_name>")
    @login_required
    def websdr_scan_result_get(site_name):
        result = _sdr_scan_results.get(site_name)
        if not result:
            return jsonify({"ready": False})
        return jsonify({"ready": True, **result})

    # ── Hub: browser downloads a WAV recording ───────────────────────────────
    @app.get("/api/hub/sdr/record/<path:site_name>")
    @login_required
    def websdr_record(site_name):
        import io as _io
        from flask import send_file as _sf
        secs = min(int(request.args.get("secs", 30)), 60)
        if not hub_server:
            return jsonify({"error": "no hub"}), 400
        buf = getattr(hub_server, "_scanner_pcm", {}).get(site_name)
        if not buf:
            return jsonify({"error": "no buffered audio — stream must be active"}), 404
        pcm_bytes = b"".join(list(buf)[-(secs * 10):])
        if not pcm_bytes:
            return jsonify({"error": "buffer empty"}), 404
        SR, CH, BPS = 48000, 1, 16
        wav_io      = _io.BytesIO()
        data_size   = len(pcm_bytes)
        wav_io.write(b"RIFF")
        wav_io.write(struct.pack("<I", 36 + data_size))
        wav_io.write(b"WAVE")
        wav_io.write(b"fmt ")
        wav_io.write(struct.pack("<IHHIIHH", 16, 1, CH, SR,
                                 SR * CH * BPS // 8, CH * BPS // 8, BPS))
        wav_io.write(b"data")
        wav_io.write(struct.pack("<I", data_size))
        wav_io.write(pcm_bytes)
        wav_io.seek(0)
        sess    = _hub_sessions.get(site_name, {})
        freq_s  = f"_{sess['freq_mhz']:.3f}MHz" if sess.get("freq_mhz") else ""
        ts_str  = time.strftime("%Y%m%d_%H%M%S")
        fname   = f"websdr_{site_name.replace(' ','_')}{freq_s}_{ts_str}.wav"
        return _sf(wav_io, mimetype="audio/wav", as_attachment=True,
                   download_name=fname)

    # ── Client: start polling thread ─────────────────────────────────────────
    cfg = monitor.app_cfg
    if cfg.hub.mode in ("client", "both") and cfg.hub.hub_url:
        t = threading.Thread(target=_client_poller, args=(monitor,),
                              daemon=True, name="SDRCmdPoller")
        t.start()
        print("[WebSDR] Client command poller started")
    else:
        if not _HAS_SCIPY:
            print("[WebSDR] Warning: scipy not found — install with: pip install scipy")


# ──────────────────────────────────────────────────────────────────────────────
# Client-side: command polling + dispatch
# ──────────────────────────────────────────────────────────────────────────────

def _client_poller(monitor):
    """Poll hub every 3 s for pending SDR commands."""
    while True:
        try:
            cfg     = monitor.app_cfg
            hub_url = (cfg.hub.hub_url or "").rstrip("/")
            site    = (cfg.hub.site_name or "").strip()
            if hub_url and site:
                req = urllib.request.Request(
                    f"{hub_url}/api/hub/sdr/cmd",
                    headers={"X-Sdr-Site": site},
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    import json as _json
                    d = _json.loads(resp.read())
                cmd = d.get("cmd")
                if cmd:
                    _dispatch_client_cmd(cmd, hub_url, cfg)
        except Exception:
            pass
        time.sleep(3)


def _dispatch_client_cmd(cmd, hub_url, cfg):
    action = cmd.get("action", "")
    if action in ("start", "tune"):
        _stop_capture()
        _start_capture(
            slot_id    = cmd["slot_id"],
            freq_mhz   = float(cmd.get("freq_mhz", 96.5)),
            mode       = cmd.get("mode", "wfm"),
            gain       = cmd.get("gain", "auto"),
            sdr_serial = cmd.get("sdr_serial", ""),
            hub_url    = hub_url,
            secret     = cfg.hub.secret_key or "",
        )
    elif action == "stop":
        _stop_capture()
    elif action == "band_scan":
        # Stop any active stream so rtl_power can use the dongle
        _stop_capture()
        threading.Thread(target=_run_band_scan, args=(cmd, hub_url, cfg),
                         daemon=True, name="SDRBandScan").start()


def _run_band_scan(cmd, hub_url, cfg):
    """Run rtl_power band scan on the client and POST the result to the hub."""
    import shutil, subprocess, os, tempfile, json as _json

    def _log(msg):
        if _monitor: _monitor.log(msg)
        else: print(msg)

    start_mhz  = float(cmd.get("start_mhz",  76.0))
    end_mhz    = float(cmd.get("end_mhz",   108.0))
    step_khz   = int(cmd.get("step_khz",     100))
    gain       = cmd.get("gain", "auto")
    sdr_serial = str(cmd.get("sdr_serial", "") or "")
    site       = (cfg.hub.site_name or "").strip()

    # Validate serial against registered SDR devices before passing to subprocess
    if sdr_serial:
        known_serials = [d.serial for d in cfg.sdr_devices]
        if sdr_serial not in known_serials:
            _log(f"[WebSDR] Band scan rejected: serial {sdr_serial!r} not in registered devices")
            return

    rtl_power = shutil.which("rtl_power")
    if not rtl_power:
        _log("[WebSDR] rtl_power not found — band scan unavailable")
        return

    gain_arg = "0" if str(gain).lower() == "auto" else str(gain)

    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tf:
        tmpfile = tf.name

    try:
        scan_cmd = [rtl_power,
                    "-f", f"{start_mhz}M:{end_mhz}M:{step_khz}k",
                    "-g", gain_arg,
                    "-i", "1",   # 1-second integration per bin
                    "-1",        # single sweep then exit
                    tmpfile]
        if sdr_serial:
            scan_cmd[1:1] = ["-d", sdr_serial]
        _log(f"[WebSDR] Band scan {start_mhz:.1f}–{end_mhz:.1f} MHz "
             f"step {step_khz} kHz…")
        subprocess.run(scan_cmd, timeout=180,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Parse CSV: date, time, Hz_low, Hz_high, step_Hz, samples, v0, v1, …
        peaks = []
        with open(tmpfile, newline="") as f:
            for line in f:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 7:
                    continue
                try:
                    hz_low  = float(parts[2])
                    hz_step = float(parts[4])
                    vals    = [float(x) for x in parts[6:] if x]
                    for i, v in enumerate(vals):
                        peaks.append({
                            "freq_mhz": round((hz_low + hz_step * i) / 1e6, 4),
                            "power_db": round(v, 1),
                        })
                except (ValueError, IndexError):
                    continue

        # Keep readings within 25 dB of the strongest signal
        if peaks:
            p_max = max(p["power_db"] for p in peaks)
            peaks = [p for p in peaks if p["power_db"] >= p_max - 25]
            peaks.sort(key=lambda p: p["freq_mhz"])

        _log(f"[WebSDR] Band scan complete: {len(peaks)} readings above threshold")

        data = _json.dumps({
            "site":      site,
            "peaks":     peaks,
            "start_mhz": start_mhz,
            "end_mhz":   end_mhz,
            "step_khz":  step_khz,
        }).encode()
        req = urllib.request.Request(
            f"{hub_url}/api/hub/sdr/scan_result",
            data=data,
            headers={"Content-Type": "application/json", "X-Sdr-Site": site},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10).close()

    except subprocess.TimeoutExpired:
        _log("[WebSDR] Band scan timed out after 180 s")
    except Exception as e:
        _log(f"[WebSDR] Band scan error: {e}")
    finally:
        try: os.unlink(tmpfile)
        except: pass


def _start_capture(slot_id, freq_mhz, mode, gain, sdr_serial, hub_url, secret):
    # Stop and fully drain the old worker before opening the dongle again.
    # _stop_capture kills the subprocess so the join returns quickly.
    _stop_capture()
    stop = threading.Event()
    t = threading.Thread(
        target=_sdr_worker,
        args=(slot_id, freq_mhz, mode, gain, sdr_serial, hub_url, secret, stop),
        daemon=True, name="SDRWorker",
    )
    t.start()
    with _state_lock:
        _client_sess.clear()
        _client_sess.update({"stop": stop, "thread": t, "slot_id": slot_id})


def _stop_capture():
    with _state_lock:
        old = dict(_client_sess)
        _client_sess.clear()
    if old.get("stop"):
        old["stop"].set()
    # Kill the subprocess directly so proc.stdout.read() unblocks immediately
    # and the thread exits before we start a new worker (dongle conflict).
    proc = old.get("proc")
    if proc:
        try:
            proc.terminate()
        except Exception:
            pass
    if old.get("thread"):
        old["thread"].join(timeout=2.0)


# ──────────────────────────────────────────────────────────────────────────────
# Client-side: IQ capture, demodulate, push PCM + spectrum
# ──────────────────────────────────────────────────────────────────────────────

def _sign_chunk(secret: str, data: bytes, ts: float) -> str:
    """HMAC-SHA256 matching hub_sign_payload() in signalscope.py."""
    key = hashlib.sha256(f"{secret}:signing".encode()).digest()
    msg = f"{ts:.0f}:".encode() + data
    return _hmac.new(key, msg, hashlib.sha256).hexdigest()


def _sdr_worker(slot_id, freq_mhz, mode, gain, sdr_serial, hub_url, secret, stop_flag):
    """
    Capture raw IQ from RTL-SDR, demodulate, and push:
      • PCM audio  → hub audio relay  (existing /api/v1/audio_chunk/<slot_id>)
      • FFT frames → hub spectrum buf (new    /api/hub/sdr/spectrum/<slot_id>)

    Demodulation modes:
      wfm  — wideband FM (broadcast, 200 kHz), 75 μs de-emphasis
      nfm  — narrow FM (comms, ±5 kHz deviation)
      am   — amplitude modulation (envelope detector)

    Two-thread delivery: the pacing loop enqueues PCM blocks at real-time rate
    without blocking on POSTs.  A dedicated POST thread drains the queue and
    batches accumulated blocks when WAN RTT > _BLK_DUR (0.1 s), ensuring the
    hub always receives audio at real-time rate regardless of round-trip latency.
    """
    import shutil, subprocess, json as _json

    chunk_url    = f"{hub_url}/api/v1/audio_chunk/{slot_id}"
    spectrum_url = f"{hub_url}/api/hub/sdr/spectrum/{slot_id}"

    def _log(msg):
        if _monitor:
            _monitor.log(msg)
        else:
            print(msg)

    rtl_sdr_bin = shutil.which("rtl_sdr")
    if not rtl_sdr_bin:
        _log("[WebSDR] rtl_sdr not found in PATH — install rtl-sdr package")
        return

    # Validate serial against registered SDR devices before passing to subprocess
    if sdr_serial and _monitor:
        cfg_now = _monitor.app_cfg
        known_serials = [d.serial for d in cfg_now.sdr_devices]
        if sdr_serial not in known_serials:
            _log(f"[WebSDR] SDR capture rejected: serial {sdr_serial!r} not in registered devices")
            return

    gain_arg = "0" if str(gain).lower() == "auto" else str(int(float(gain) * 10))

    cmd = [rtl_sdr_bin,
           "-f", str(int(freq_mhz * 1e6)),
           "-s", str(_SAMPLE_RATE),
           "-g", gain_arg,
           "-n", "0",    # stream indefinitely
           "-"]
    if sdr_serial:
        cmd[1:1] = ["-d", sdr_serial]

    _log(f"[WebSDR] Starting capture: {freq_mhz:.3f} MHz  mode={mode}  "
         f"serial={sdr_serial or 'auto'}  gain={gain}")

    # Build resampler state (polyphase FIR coefficients)
    if _HAS_SCIPY:
        _resample = lambda x: _sp.resample_poly(x.real, _UP, _DN).astype(np.float32)
    else:
        # Linear interpolation fallback (adequate, some aliasing)
        _n_out = int(_BLK_SAMPS * _OUT_RATE / _SAMPLE_RATE)
        _t_in  = np.arange(_BLK_SAMPS, dtype=np.float32)
        _t_out = np.linspace(0, _BLK_SAMPS - 1, _n_out, dtype=np.float32)
        _resample = lambda x: np.interp(_t_out, _t_in, x.real).astype(np.float32)

    # De-emphasis IIR coefficients (75 μs broadcast, 50 μs for some regions)
    _TAU   = 75e-6
    _dt    = 1.0 / _OUT_RATE
    _alpha = _dt / (_TAU + _dt)       # ≈ 0.281 at 48 kHz
    _deemph_prev = np.float32(0.0)    # carries state between blocks

    out_deadline  = None
    fft_deadline  = time.monotonic()
    proc          = None
    _level_db     = None   # exponential moving average of RMS dBFS
    _nfm_sos      = None   # NFM pre-filter SOS coefficients (built once)
    _nfm_zi       = None   # NFM SOS filter state carried across blocks

    # ── POST worker: drains post_q, batching blocks when RTT > _BLK_DUR ─────
    # Decouples pacing (real-time) from network I/O (RTT-bound) so WAN latency
    # never stalls the demodulation/pacing loop.
    post_q:   queue.Queue = queue.Queue(maxsize=200)
    post_err: list        = [0]   # mutable error counter shared with POST thread

    def _post_worker():
        while not stop_flag.is_set():
            try:
                blk = post_q.get(timeout=1.0)
            except queue.Empty:
                continue
            # Drain any blocks that accumulated while the previous POST was
            # in flight — this is the batching that handles RTT > _BLK_DUR.
            batch = bytearray(blk)
            while True:
                try:
                    batch += post_q.get_nowait()
                except queue.Empty:
                    break
            try:
                body  = bytes(batch)
                ts_c  = time.time()
                sig_c = _sign_chunk(secret, body, ts_c) if secret else ""
                hdrs  = {
                    "Content-Type": "application/octet-stream",
                    "X-Hub-Sig":    sig_c,
                    "X-Hub-Ts":     f"{ts_c:.0f}",
                    "X-Hub-Nonce":  hashlib.md5(os.urandom(8)).hexdigest()[:16],
                }
                req = urllib.request.Request(chunk_url, data=body,
                                             headers=hdrs, method="POST")
                urllib.request.urlopen(req, timeout=5).close()
                post_err[0] = 0
            except Exception as e:
                post_err[0] += 1
                if post_err[0] >= 5:
                    _log(f"[WebSDR] PCM push failed: {e}")
                    stop_flag.set()
                    return

    threading.Thread(target=_post_worker, daemon=True,
                     name=f"SDRPost-{slot_id[:6]}").start()

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=_BLK_BYTES * 4,
        )
        # Register proc in session so _stop_capture() can kill it immediately
        with _state_lock:
            if _client_sess.get("slot_id") == slot_id:
                _client_sess["proc"] = proc

        raw_buf = b""

        while not stop_flag.is_set():
            # ── Read one block of raw IQ bytes ────────────────────────────
            needed  = _BLK_BYTES - len(raw_buf)
            chunk   = proc.stdout.read(needed)
            if not chunk:
                break
            raw_buf += chunk
            if len(raw_buf) < _BLK_BYTES:
                continue

            raw     = np.frombuffer(raw_buf[:_BLK_BYTES], dtype=np.uint8)
            raw_buf = raw_buf[_BLK_BYTES:]

            # ── Convert uint8 I/Q → complex float32, zero-centred ─────────
            iq = ((raw[0::2].astype(np.float32) - 127.5)
                  + 1j * (raw[1::2].astype(np.float32) - 127.5)) / 127.5

            # ── Demodulate ────────────────────────────────────────────────
            if mode == "am":
                demod = np.abs(iq)
                demod -= np.mean(demod)

            elif mode == "nfm":
                # Pre-filter to ±12 kHz then FM discriminate.
                # SOS filter is built once and state (zi) is carried across
                # blocks to avoid click/pop artefacts at block boundaries.
                if _HAS_SCIPY:
                    if _nfm_sos is None:
                        _nfm_sos = _sp.butter(
                            5, 12_000 / (_SAMPLE_RATE / 2), output="sos")
                        _nfm_zi = np.zeros(
                            (_nfm_sos.shape[0], 2), dtype=complex)
                    iq_f, _nfm_zi = _sp.sosfilt(_nfm_sos, iq, zi=_nfm_zi)
                else:
                    iq_f = iq   # skip pre-filter without scipy
                demod = np.angle(iq_f[1:] * np.conj(iq_f[:-1])) / np.pi
                # Pad to original length
                demod = np.concatenate(([demod[0]], demod))

            else:  # wfm (default)
                demod = np.angle(iq[1:] * np.conj(iq[:-1])) / np.pi
                demod = np.concatenate(([demod[0]], demod))

            # ── Resample IQ block rate → 48 kHz ──────────────────────────
            audio = _resample(demod.astype(np.float32))

            # ── 75 μs de-emphasis (WFM only) ─────────────────────────────
            nonlocal_state = [_deemph_prev]
            if mode == "wfm":
                if _HAS_SCIPY:
                    b_de = [_alpha]
                    a_de = [1.0, _alpha - 1.0]
                    audio, zf = _sp.lfilter(b_de, a_de, audio,
                                            zi=[nonlocal_state[0]])
                    nonlocal_state[0] = zf[0]
                else:
                    y = np.empty_like(audio)
                    prev = nonlocal_state[0]
                    for i in range(len(audio)):
                        prev = _alpha * audio[i] + (1.0 - _alpha) * prev
                        y[i] = prev
                    nonlocal_state[0] = prev
                    audio = y
                _deemph_prev = nonlocal_state[0]

            # ── Convert to 16-bit PCM ─────────────────────────────────────
            scale = 32767.0 * (2.5 if mode == "wfm" else 4.0)
            pcm   = np.clip(audio * scale, -32768, 32767).astype(np.int16)
            pcm_b = pcm.tobytes()

            # ── Track signal level (EMA of RMS dBFS) ─────────────────────
            _rms = float(np.sqrt(np.mean(audio ** 2)))
            _db  = 20.0 * np.log10(max(_rms, 1e-12))
            _level_db = _db if _level_db is None else 0.85 * _level_db + 0.15 * _db

            # ── Pace to real-time then enqueue for POST ───────────────────
            if out_deadline is None:
                out_deadline = time.monotonic()
            slack = out_deadline - time.monotonic()
            if slack > 0.001:
                time.sleep(slack)
            try:
                post_q.put_nowait(pcm_b)
            except queue.Full:
                pass   # hub unreachable; POST thread will detect and stop

            out_deadline += _BLK_DUR

            # ── Push FFT spectrum ~10×/s ──────────────────────────────────
            now = time.monotonic()
            if now >= fft_deadline:
                fft_deadline = now + 0.1
                try:
                    n   = 2048
                    win = np.blackman(n)
                    mag = np.abs(np.fft.fftshift(np.fft.fft(iq[:n] * win, n)))
                    db  = (20 * np.log10(mag / n + 1e-12)).round(1).tolist()
                    frame = {"fft": db, "cf": freq_mhz,
                             "bw": _SAMPLE_RATE / 1e6, "n": n,
                             "level": round(_level_db, 1) if _level_db is not None else None}
                    data  = _json.dumps(frame).encode()
                    _spec_hdrs = {"Content-Type": "application/json"}
                    if secret:
                        _ts  = time.time()
                        _key = hashlib.sha256(f"{secret}:signing".encode()).digest()
                        _msg = f"{_ts:.0f}:".encode() + data
                        _spec_hdrs["X-Hub-Sig"] = _hmac.new(_key, _msg, hashlib.sha256).hexdigest()
                        _spec_hdrs["X-Hub-Ts"]  = f"{_ts:.0f}"
                    req   = urllib.request.Request(
                        spectrum_url, data=data,
                        headers=_spec_hdrs,
                        method="POST",
                    )
                    urllib.request.urlopen(req, timeout=2).close()
                except Exception:
                    pass

    except Exception as e:
        _log(f"[WebSDR] Worker error: {e}")
    finally:
        stop_flag.set()   # ensure POST thread exits cleanly
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                pass
        _log(f"[WebSDR] Worker stopped for slot {slot_id[:6]}")


# ──────────────────────────────────────────────────────────────────────────────
# Browser template
# ──────────────────────────────────────────────────────────────────────────────

WEBSDR_TPL = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Web SDR — SignalScope</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<link rel="icon" type="image/x-icon" href="/static/signalscope_icon.png">
<style nonce="{{csp_nonce()}}">
:root{--bg:#060d1a;--bg2:#0d1629;--bg3:#111f35;--bor:#1e3048;--tx:#f0f4ff;
      --mu:#6b7280;--acc:#3b82f6;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);font-family:'Segoe UI',system-ui,sans-serif;
     min-height:100vh;display:flex;flex-direction:column}
header{background:linear-gradient(180deg,rgba(10,31,65,.96),rgba(9,24,48,.96));
       border-bottom:1px solid var(--bor);padding:8px 20px;display:flex;
       align-items:center;gap:10px;flex-shrink:0}
.logo{font-weight:800;font-size:20px;color:var(--tx);letter-spacing:-.02em}
.logo span{color:var(--acc)}
.back{margin-left:auto;color:var(--mu);font-size:13px;text-decoration:none;
      padding:4px 10px;border:1px solid var(--bor);border-radius:6px;
      background:var(--bg3)}
.back:hover{color:var(--tx)}
.build{font-size:11px;color:var(--mu);padding:3px 8px;border-radius:999px;
       background:#0f2e5e;border:1px solid var(--bor)}

.toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;
          padding:8px 12px;background:var(--bg2);border-bottom:1px solid var(--bor);
          flex-shrink:0}
.toolbar label{font-size:11px;color:var(--mu);font-weight:600;letter-spacing:.06em;
               text-transform:uppercase;white-space:nowrap}
.toolbar select,.toolbar input[type=number],.toolbar input[type=text]{
  padding:5px 8px;background:var(--bg3);border:1px solid var(--bor);
  border-radius:6px;color:var(--tx);font-size:13px}
.toolbar select{cursor:pointer}
.btn-conn{padding:6px 20px;border-radius:8px;font-size:13px;font-weight:700;
          border:none;cursor:pointer;transition:background .15s}
.btn-conn.idle{background:var(--acc);color:#fff}
.btn-conn.active{background:#7f1d1d;color:#fca5a5}
.mode-sel{display:flex;gap:4px}
.mode-btn{padding:4px 10px;border-radius:6px;font-size:12px;font-weight:600;
          border:1px solid var(--bor);background:var(--bg3);color:var(--mu);
          cursor:pointer;transition:all .15s}
.mode-btn.sel{background:rgba(59,130,246,.25);border-color:var(--acc);color:var(--tx)}

.sdr-main{flex:1;display:flex;flex-direction:column;overflow:hidden;position:relative}
.freq-ruler{height:22px;background:var(--bg2);border-bottom:1px solid var(--bor);
            flex-shrink:0;position:relative;overflow:hidden}
#ruler-canvas{display:block;width:100%;height:100%}
.waterfall-wrap{flex:1;position:relative;overflow:hidden;cursor:crosshair;min-height:200px}
#wf-canvas{display:block;width:100%;height:100%}
.wf-overlay{position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none}
.no-signal{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
           color:var(--mu);font-size:14px;text-align:center;pointer-events:none}

.info-bar{display:flex;align-items:center;gap:16px;padding:6px 14px;
           background:var(--bg2);border-top:1px solid var(--bor);flex-shrink:0;
           flex-wrap:wrap}
.freq-disp{font-size:28px;font-weight:800;font-variant-numeric:tabular-nums;
           letter-spacing:.04em;color:var(--ok);line-height:1}
.freq-mhz{font-size:13px;color:var(--mu);margin-top:2px}
.sdot{width:10px;height:10px;border-radius:50%;background:var(--mu);flex-shrink:0}
.sdot.streaming{background:var(--ok);box-shadow:0 0 6px var(--ok)}
.sdot.connecting{background:var(--wn);animation:blink 1s infinite}
.sdot.error{background:var(--al)}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}
.status-txt{font-size:13px;color:var(--mu)}
.vol-wrap{display:flex;align-items:center;gap:6px;margin-left:auto}
.vol-wrap label{font-size:11px;color:var(--mu)}
#vol-slider{width:80px;accent-color:var(--acc)}
.tuner-btns{display:flex;gap:4px}
.tune-btn{padding:4px 10px;border-radius:6px;font-size:13px;font-weight:700;
          border:1px solid var(--bor);background:var(--bg3);color:var(--tx);
          cursor:pointer}
.tune-btn:hover{background:rgba(59,130,246,.2)}

/* Record buttons */
.btn-rec{padding:4px 10px;border-radius:6px;font-size:12px;font-weight:700;
         border:1px solid var(--bor);background:var(--bg3);color:var(--mu);
         cursor:pointer;transition:all .15s}
.btn-rec:not(:disabled):hover{border-color:#ef4444;color:#ef4444}
.btn-rec:disabled{opacity:.4;cursor:default}

/* Band scan */
.scan-btn{padding:5px 12px;border-radius:6px;font-size:12px;font-weight:700;
          border:1px solid var(--bor);background:var(--bg3);color:var(--mu);
          cursor:pointer;transition:all .15s;white-space:nowrap}
.scan-btn:not(:disabled):hover{border-color:var(--acc);color:var(--tx)}
.scan-btn:disabled{opacity:.4;cursor:default}
.scan-col{flex:1.4}
.scan-peak{display:flex;align-items:center;gap:4px;padding:2px 3px;border-radius:4px;
           cursor:pointer;font-size:12px;color:var(--mu)}
.scan-peak:hover{background:rgba(59,130,246,.15);color:var(--tx)}
.scan-peak .sp-freq{font-weight:700;font-variant-numeric:tabular-nums;
                    color:var(--tx);min-width:52px}
.scan-peak .sp-bar-bg{flex:1;height:5px;background:var(--bg3);border-radius:3px;
                       overflow:hidden;min-width:20px}
.scan-peak .sp-bar{height:100%;border-radius:3px;background:var(--acc)}
.scan-peak .sp-db{font-size:10px;min-width:36px;text-align:right}

/* Level meter */
.level-wrap{display:flex;align-items:center;gap:6px}
.level-bar-bg{width:90px;height:7px;background:var(--bg3);border-radius:4px;
              border:1px solid var(--bor);overflow:hidden}
.level-bar{height:100%;width:0%;border-radius:4px;transition:width .12s}
.level-txt{font-size:11px;font-variant-numeric:tabular-nums;min-width:52px;color:var(--mu)}

/* History / Presets panel */
.hp-panel{display:flex;gap:0;border-top:1px solid var(--bor);background:var(--bg2);
          max-height:110px;overflow:hidden;flex-shrink:0}
.hp-col{flex:1;padding:5px 10px;overflow-y:auto;border-right:1px solid var(--bor);
        min-width:0}
.hp-col:last-child{border-right:none}
.hp-hdr{font-size:10px;font-weight:700;color:var(--mu);text-transform:uppercase;
        letter-spacing:.08em;margin-bottom:3px;display:flex;align-items:center;gap:6px;
        position:sticky;top:0;background:var(--bg2);padding-bottom:2px}
.hp-save-btn{margin-left:auto;padding:1px 7px;border-radius:4px;font-size:10px;
             border:1px solid var(--bor);background:var(--bg3);color:var(--mu);
             cursor:pointer;white-space:nowrap}
.hp-save-btn:hover{color:var(--tx)}
.hp-item{display:flex;align-items:center;gap:5px;padding:2px 3px;border-radius:4px;
         cursor:pointer;font-size:12px;white-space:nowrap;overflow:hidden;color:var(--mu)}
.hp-item:hover{background:rgba(59,130,246,.15);color:var(--tx)}
.hp-item .hp-freq{font-weight:700;font-variant-numeric:tabular-nums;color:var(--tx)}
.hp-item .hp-mode{font-size:10px;font-weight:600;background:var(--bg3);
                  border:1px solid var(--bor);border-radius:3px;padding:0 4px;
                  line-height:16px;flex-shrink:0}
.hp-item .hp-name{flex:1;overflow:hidden;text-overflow:ellipsis;font-size:11px}
.hp-del{flex-shrink:0;padding:0 4px;color:var(--mu);font-size:11px;line-height:1}
.hp-del:hover{color:var(--al)}
.hp-empty{font-size:11px;color:var(--mu);font-style:italic;padding:2px 4px}
.key-hint{font-size:10px;color:var(--mu);margin-left:auto;display:none}
@media(min-width:700px){.key-hint{display:block}}
</style>
</head>
<body>
<header>
  <span class="logo">Signal<span>Scope</span></span>
  <span style="font-size:12px;color:var(--mu)">Web SDR</span>
  <span class="build">{{build}}</span>
  <a href="/hub" class="back">← Hub</a>
</header>

<div class="toolbar">
  <label>Site</label>
  <select id="site-sel">
    {% for s in sites %}
    {% set no_dongle = not s.serials %}
    {% set unavail = not s.online or no_dongle %}
    <option value="{{s.name|e}}"
            data-serials="{{s.serials | join(',') | e}}"
            {% if unavail %} disabled{% endif %}>
      {{s.name|e}}{% if not s.online %} (offline){% elif no_dongle %} (no Scanner dongle){% endif %}
    </option>
    {% endfor %}
    {% if not sites %}<option disabled>No connected sites — check hub connections</option>{% endif %}
  </select>

  <label>SDR</label>
  <select id="sdr-sel"><option value="">Auto</option></select>

  <label>Freq</label>
  <input type="number" id="freq-inp" value="96.5" step="0.1" min="0.1" max="2000000"
         style="width:100px">
  <button id="unit-btn" style="padding:4px 8px;border-radius:6px;font-size:11px;
          font-weight:700;border:1px solid var(--bor);background:var(--bg3);
          color:var(--mu);cursor:pointer;letter-spacing:.05em">MHz</button>

  <label>Gain</label>
  <select id="gain-sel">
    <option value="auto">Auto</option>
    <option value="10">10 dB</option>
    <option value="20">20 dB</option>
    <option value="30">30 dB</option>
    <option value="40">40 dB</option>
    <option value="49.6">49.6 dB</option>
  </select>

  <label>Mode</label>
  <div class="mode-sel" id="mode-sel">
    <button class="mode-btn sel" data-mode="wfm">WFM</button>
    <button class="mode-btn" data-mode="nfm">NFM</button>
    <button class="mode-btn" data-mode="am">AM</button>
  </div>

  <button class="btn-conn idle" id="conn-btn">Connect</button>

  <div style="margin-left:auto;display:flex;align-items:center;gap:6px">
    <label>Scan</label>
    <input type="number" id="scan-start" value="76" step="1" min="0.1" max="2000"
           style="width:56px" title="Scan start MHz">
    <span style="font-size:11px;color:var(--mu)">–</span>
    <input type="number" id="scan-end" value="108" step="1" min="0.1" max="2000"
           style="width:56px" title="Scan end MHz">
    <span style="font-size:11px;color:var(--mu)">MHz</span>
    <button class="scan-btn" id="scan-btn" disabled>📡 Scan</button>
    <span id="scan-status" style="font-size:11px;color:var(--mu);display:none">Scanning…</span>
  </div>
</div>

<div class="sdr-main">
  <div class="freq-ruler"><canvas id="ruler-canvas"></canvas></div>
  <div class="waterfall-wrap" id="wf-wrap">
    <canvas id="wf-canvas"></canvas>
    <div class="no-signal" id="no-sig">Connect to see spectrum</div>
  </div>
</div>

<div class="info-bar">
  <div>
    <div class="freq-disp" id="freq-disp">---.---</div>
    <div class="freq-mhz">MHz</div>
  </div>
  <div style="display:flex;align-items:center;gap:6px">
    <div class="sdot" id="s-dot"></div>
    <span class="status-txt" id="s-txt">Idle</span>
  </div>
  <div class="level-wrap" id="level-wrap" style="display:none">
    <div class="level-bar-bg"><div class="level-bar" id="level-bar"></div></div>
    <span class="level-txt" id="level-txt">--- dBFS</span>
  </div>
  <div class="tuner-btns">
    <button class="tune-btn" id="btn-dn" data-step="-0.1">−0.1</button>
    <button class="tune-btn" id="btn-up" data-step="0.1">+0.1</button>
  </div>
  <div class="vol-wrap">
    <label>Vol</label>
    <input type="range" id="vol-slider" min="0" max="2" step="0.05" value="1">
  </div>
  <div style="display:flex;gap:4px;flex-shrink:0">
    <button class="btn-rec" id="rec30-btn" disabled title="Download last 30 s as WAV">⏺ 30s</button>
    <button class="btn-rec" id="rec60-btn" disabled title="Download last 60 s as WAV">⏺ 60s</button>
  </div>
  <span class="key-hint" title="Keyboard: ← → tune ±0.1 MHz · ↑ ↓ tune ±1 MHz · PgUp/Dn ±10 MHz · Shift ×5">
    ← → ±0.1&nbsp; ↑ ↓ ±1&nbsp; PgUp/Dn ±10 MHz
  </span>
</div>

<div class="hp-panel">
  <div class="hp-col">
    <div class="hp-hdr">📻 History</div>
    <div id="hist-list"></div>
  </div>
  <div class="hp-col">
    <div class="hp-hdr">
      ⭐ Presets
      <button class="hp-save-btn" id="save-preset-btn">+ Save current</button>
    </div>
    <div id="preset-list"></div>
  </div>
  <div class="hp-col scan-col" id="scan-col" style="display:none">
    <div class="hp-hdr">📡 Scan Results</div>
    <div id="scan-list"></div>
  </div>
</div>

<script nonce="{{csp_nonce()}}">
(function(){
'use strict';

function _getCsrf(){
  return (document.querySelector('meta[name="csrf-token"]')||{}).content
      || (document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/)||[])[1]
      || '';
}
function _f(url,o){o=o||{};o.credentials='same-origin';
  o.headers=Object.assign({'X-CSRFToken':_getCsrf(),'Content-Type':'application/json'},o.headers||{});
  return fetch(url,o);}

function _esc(s){var d=document.createElement('div');d.textContent=s||'';return d.innerHTML;}

// ── State ──────────────────────────────────────────────────────────────────
var _state    = 'idle';
var _slotId   = '';
var _cf       = 96.5;    // centre frequency MHz
var _bw       = 1.024;   // bandwidth MHz (matches 1.024 MSPS)
var _mode     = 'wfm';
var _poll     = null;

// ── DOM refs ───────────────────────────────────────────────────────────────
var siteSel   = document.getElementById('site-sel');
var sdrSel    = document.getElementById('sdr-sel');
var freqInp   = document.getElementById('freq-inp');
var unitBtn   = document.getElementById('unit-btn');
var gainSel   = document.getElementById('gain-sel');
var connBtn   = document.getElementById('conn-btn');
var sDot      = document.getElementById('s-dot');
var sTxt      = document.getElementById('s-txt');
var freqDisp  = document.getElementById('freq-disp');
var volSlider = document.getElementById('vol-slider');
var noSig     = document.getElementById('no-sig');

// ── Unit toggle (MHz / kHz) ────────────────────────────────────────────────
var _useKhz = false;
function _toMhz(val){ return _useKhz ? val / 1000 : val; }
function _fromMhz(mhz){ return _useKhz ? mhz * 1000 : mhz; }
function _freqStep(){ return _useKhz ? 100 : 0.1; }
unitBtn.addEventListener('click', function(){
  var curMhz = _toMhz(parseFloat(freqInp.value) || 96.5);
  _useKhz = !_useKhz;
  unitBtn.textContent = _useKhz ? 'kHz' : 'MHz';
  unitBtn.style.color = _useKhz ? 'var(--acc)' : 'var(--mu)';
  freqInp.step = _freqStep();
  freqInp.value = _useKhz ? (curMhz * 1000).toFixed(0) : curMhz.toFixed(3);
});

// ── Web Audio ──────────────────────────────────────────────────────────────
var _audioCtx = null, _gainNode = null, _reader = null;
var _nextTime = 0, _pcmBuf = new Uint8Array(0), _sched = 0;
var _SR = 48000, _BLK_S = 4800, _BLK_B = _BLK_S * 2, _PRE = 1.0;

function _initAudio(){
  if(_audioCtx) return;
  _audioCtx = new (window.AudioContext || window.webkitAudioContext)({sampleRate:_SR});
  _gainNode = _audioCtx.createGain();
  _gainNode.gain.value = parseFloat(volSlider.value);
  _gainNode.connect(_audioCtx.destination);
}
function disconnectAudio(){
  if(_reader){ try{_reader.cancel();}catch(e){} _reader=null; }
  _pcmBuf = new Uint8Array(0); _sched = 0;
}
function connectAudio(url){
  disconnectAudio(); _initAudio();
  if(_audioCtx.state === 'suspended') _audioCtx.resume();
  _nextTime = _audioCtx.currentTime + _PRE;
  _pcmBuf   = new Uint8Array(0); _sched = 0;
  fetch(url, {credentials:'same-origin'}).then(function(r){
    if(!r.ok || !r.body){ setState('error','Stream failed'); return; }
    _reader = r.body.getReader();
    (function pump(){
      _reader.read().then(function(d){
        if(d.done || !_reader) return;
        _feedPCM(d.value); pump();
      }).catch(function(){ if(_state!=='idle') setState('error','Stream lost'); });
    })();
  }).catch(function(){ setState('error','Network error'); });
}
function _feedPCM(chunk){
  var tmp = new Uint8Array(_pcmBuf.length + chunk.length);
  tmp.set(_pcmBuf); tmp.set(chunk, _pcmBuf.length); _pcmBuf = tmp;
  while(_pcmBuf.length >= _BLK_B){
    _scheduleBlock(_pcmBuf.slice(0,_BLK_B));
    _pcmBuf = _pcmBuf.slice(_BLK_B);
  }
}
function _scheduleBlock(bytes){
  var buf = _audioCtx.createBuffer(1, _BLK_S, _SR);
  var ch  = buf.getChannelData(0);
  var dv  = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  for(var i=0;i<_BLK_S;i++) ch[i] = dv.getInt16(i*2, true)/32768.0;
  var src = _audioCtx.createBufferSource();
  src.buffer = buf; src.connect(_gainNode);
  var t = Math.max(_nextTime, _audioCtx.currentTime + 0.05);
  src.start(t); _nextTime = t + buf.duration; _sched++;
  if(_state==='connecting' && _sched > Math.ceil(_PRE/0.1)+1){
    var dispFreq = _useKhz ? (_cf*1000).toFixed(0)+' kHz' : _cf.toFixed(3)+' MHz';
    setState('streaming', '● Live — ' + dispFreq);
    noSig.style.display = 'none';
  }
}

// ── Status helpers ─────────────────────────────────────────────────────────
function setState(st, msg){
  _state = st;
  sDot.className = 'sdot ' + st;
  sTxt.textContent = msg;
  var live = (st==='streaming'||st==='connecting');
  connBtn.textContent = live ? 'Disconnect' : 'Connect';
  connBtn.className   = 'btn-conn ' + (live ? 'active' : 'idle');
  // Scan enabled any time a site is selected and not already scanning
  scanBtn.disabled  = !siteSel.value || _scanning;
  // Record requires active stream (buffer must be filling)
  rec30Btn.disabled = !live;
  rec60Btn.disabled = !live;
}

// ── Mode helpers ────────────────────────────────────────────────────────────
function _setMode(mode){
  _mode = mode;
  document.querySelectorAll('.mode-btn').forEach(function(b){
    b.classList.toggle('sel', b.dataset.mode === mode);
  });
}

// ── Mode buttons ───────────────────────────────────────────────────────────
document.getElementById('mode-sel').addEventListener('click', function(e){
  var mb = e.target.closest('.mode-btn');
  if(!mb) return;
  _setMode(mb.dataset.mode);
  if(_state==='streaming'||_state==='connecting') doTune(_cf);
});

// ── Connect/Disconnect ─────────────────────────────────────────────────────
connBtn.addEventListener('click', function(){
  if(_state==='streaming'||_state==='connecting') doStop();
  else doStart(_toMhz(parseFloat(freqInp.value)||96.5));
});

function doStart(freq){
  if(!siteSel.value){ setState('error','Select a site'); return; }
  _cf = freq; freqDisp.textContent = freq.toFixed(3);
  freqInp.value = _fromMhz(freq).toFixed(_useKhz ? 0 : 3);
  noSig.textContent = 'Waiting for signal from client…';
  noSig.style.display = '';
  setState('connecting','Connecting…');
  _saveHistory(freq, _mode);
  _f('/api/hub/sdr/start', {method:'POST', body:JSON.stringify({
    site: siteSel.value,
    freq_mhz: freq,
    mode: _mode,
    gain: gainSel.value,
    sdr_serial: sdrSel.value,
  })}).then(function(r){return r.json();}).then(function(d){
    if(d.ok){
      _slotId = d.slot_id;
      connectAudio(d.stream_url);
      startSpectrumPoll();
    } else { setState('error', d.error||'Failed'); }
  }).catch(function(){ setState('error','Network error'); });
}

function doTune(freq){
  if(_state!=='streaming'&&_state!=='connecting') return;
  _cf = freq;
  freqDisp.textContent = freq.toFixed(3);
  freqInp.value = _fromMhz(freq).toFixed(_useKhz ? 0 : 3);
  setState('connecting','Retuning…');
  _saveHistory(freq, _mode);
  _f('/api/hub/sdr/tune', {method:'POST', body:JSON.stringify({
    site: siteSel.value, freq_mhz: freq, mode: _mode,
  })}).then(function(r){return r.json();}).then(function(d){
    if(d.ok){
      _slotId = d.slot_id;
      connectAudio(d.stream_url);
    }
  }).catch(function(){});
}

function doStop(){
  stopSpectrumPoll(); stopScanPoll(); disconnectAudio();
  _f('/api/hub/sdr/stop',{method:'POST',body:JSON.stringify({site:siteSel.value})})
    .catch(function(){});
  setState('idle','Idle');
  noSig.textContent = 'Connect to see spectrum';
  noSig.style.display = '';
  _slotId = '';
}

// ── SDR selector ───────────────────────────────────────────────────────────
siteSel.addEventListener('change', function(){
  sdrSel.innerHTML = '<option value="">Auto</option>';
  var opt = siteSel.options[siteSel.selectedIndex];
  if(!opt) return;
  var serials = (opt.dataset.serials || '').split(',').filter(Boolean);
  serials.forEach(function(s){
    var o=document.createElement('option');
    o.value=s; o.textContent=s; sdrSel.appendChild(o);
  });
  scanBtn.disabled = !siteSel.value || _scanning;
});
if(siteSel.value) siteSel.dispatchEvent(new Event('change'));

// ── Tune step buttons ──────────────────────────────────────────────────────
document.querySelectorAll('.tune-btn').forEach(function(b){
  b.addEventListener('click', function(){
    var step = _useKhz ? parseFloat(b.dataset.step) / 1000 : parseFloat(b.dataset.step);
    var freq = Math.round((_cf + step) * 1000) / 1000;
    freqInp.value = _fromMhz(freq).toFixed(_useKhz ? 0 : 3);
    doTune(freq);
  });
});

// ── Volume ─────────────────────────────────────────────────────────────────
volSlider.addEventListener('input', function(){
  if(_gainNode) _gainNode.gain.value = parseFloat(this.value);
});

// ── Waterfall ──────────────────────────────────────────────────────────────
var _wfCanvas  = document.getElementById('wf-canvas');
var _wfCtx     = _wfCanvas.getContext('2d', {willReadFrequently: true});
var _ruCanvas  = document.getElementById('ruler-canvas');
var _ruCtx     = _ruCanvas.getContext('2d');
var _wfW = 0, _wfH = 0;
var _specPoll  = null;
var _DB_MIN    = -110, _DB_MAX = -20;

function _resize(){
  var wrap = document.getElementById('wf-wrap');
  var dpr  = window.devicePixelRatio || 1;
  _wfW = Math.round(wrap.clientWidth  * dpr);
  _wfH = Math.round(wrap.clientHeight * dpr);
  _wfCanvas.width  = _wfW;
  _wfCanvas.height = _wfH;
  _wfCanvas.style.width  = wrap.clientWidth  + 'px';
  _wfCanvas.style.height = wrap.clientHeight + 'px';
  var ruW = _ruCanvas.parentElement.clientWidth;
  _ruCanvas.width  = Math.round(ruW * dpr);
  _ruCanvas.height = Math.round(22  * dpr);
  _ruCanvas.style.width  = ruW + 'px';
  _ruCanvas.style.height = '22px';
  _ruCtx.scale(dpr, dpr);
  _drawRuler();
}
window.addEventListener('resize', _resize);
// Defer first resize to ensure flex layout has settled
requestAnimationFrame(_resize);

// Precompute color LUT (256 entries)
var _colorLUT = (function(){
  var lut = new Uint8ClampedArray(256 * 3);
  for(var i=0;i<256;i++){
    var t = i/255;
    var r,g,b;
    if(t < 0.2){      r=0;   g=0;   b=Math.round(t/0.2*128); }
    else if(t < 0.4){ var s=(t-0.2)/0.2; r=0;   g=0;   b=Math.round(128+s*127); }
    else if(t < 0.6){ var s=(t-0.4)/0.2; r=0;   g=Math.round(s*255); b=255; }
    else if(t < 0.8){ var s=(t-0.6)/0.2; r=Math.round(s*255); g=255; b=Math.round((1-s)*255); }
    else {             var s=(t-0.8)/0.2; r=255; g=Math.round((1-s)*255); b=0; }
    lut[i*3]=r; lut[i*3+1]=g; lut[i*3+2]=b;
  }
  return lut;
})();

function _dbToIdx(db){
  return Math.round(Math.max(0,Math.min(255,(db-_DB_MIN)/(_DB_MAX-_DB_MIN)*255)));
}

function _drawRuler(){
  var ctx = _ruCtx;
  var w   = _ruCanvas.width;
  ctx.clearRect(0,0,w,22);
  ctx.fillStyle = '#0d1629';
  ctx.fillRect(0,0,w,22);
  ctx.fillStyle = '#6b7280';
  ctx.font = '10px monospace';
  ctx.textAlign = 'center';
  // Draw ticks every 0.1 MHz
  var freqMin = _cf - _bw/2;
  var freqMax = _cf + _bw/2;
  var step = 0.1;
  for(var f=Math.ceil(freqMin/step)*step; f<=freqMax+0.001; f+=step){
    f = Math.round(f*1000)/1000;
    var x = Math.round((f - freqMin) / _bw * w);
    var label = f.toFixed(1);
    ctx.fillStyle = (Math.abs(f - Math.round(f)) < 0.01) ? '#b7d8ff' : '#6b7280';
    ctx.fillRect(x, 0, 1, (Math.abs(f-Math.round(f))<0.01)?12:6);
    if(Math.abs(f - Math.round(f*2)/2) < 0.01){
      ctx.fillStyle = '#9ca3af';
      ctx.fillText(label, x, 21);
    }
  }
}

function _paintRow(fftDb){
  if(_wfW<=0||_wfH<=0) return;
  // Shift existing pixels down by 1 row
  var img = _wfCtx.getImageData(0,0,_wfW,_wfH);
  var d   = img.data;
  d.copyWithin(_wfW*4, 0, (_wfH-1)*_wfW*4);
  // Paint new row at top
  var nBins = fftDb.length;
  for(var x=0;x<_wfW;x++){
    var bin = Math.floor(x * nBins / _wfW);
    var idx = _dbToIdx(fftDb[bin]);
    var p   = x*4;
    d[p]   = _colorLUT[idx*3];
    d[p+1] = _colorLUT[idx*3+1];
    d[p+2] = _colorLUT[idx*3+2];
    d[p+3] = 255;
  }
  _wfCtx.putImageData(img,0,0);
}

// Draw crosshair on waterfall click → tune to that frequency
_wfCanvas.addEventListener('click', function(e){
  if(_state!=='streaming'&&_state!=='connecting') return;
  var rect = _wfCanvas.getBoundingClientRect();
  var x    = (e.clientX - rect.left) * (_wfCanvas.width / rect.width);
  var freq = (_cf - _bw/2) + (x / _wfW) * _bw;
  freq = Math.round(freq * 1000) / 1000;
  freqInp.value = _fromMhz(freq).toFixed(_useKhz ? 0 : 3);
  doTune(freq);
});
_wfCanvas.title = 'Click to tune';

// ── Record ─────────────────────────────────────────────────────────────────
var rec30Btn = document.getElementById('rec30-btn');
var rec60Btn = document.getElementById('rec60-btn');
function doRecord(secs){
  var site = siteSel.value; if(!site) return;
  var a = document.createElement('a');
  a.href = '/api/hub/sdr/record/' + encodeURIComponent(site) + '?secs=' + secs;
  a.download = '';
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
}
rec30Btn.addEventListener('click', function(){ doRecord(30); });
rec60Btn.addEventListener('click', function(){ doRecord(60); });

// ── Band scan ──────────────────────────────────────────────────────────────
var scanBtn    = document.getElementById('scan-btn');
var scanStatus = document.getElementById('scan-status');
var scanCol    = document.getElementById('scan-col');
var _scanPoll  = null;
var _scanning  = false;

function doScan(){
  var site = siteSel.value; if(!site) return;
  var startMhz = parseFloat(document.getElementById('scan-start').value) || 76;
  var endMhz   = parseFloat(document.getElementById('scan-end').value)   || 108;
  if(endMhz <= startMhz){ alert('End must be greater than start'); return; }
  _scanning = true;
  scanBtn.disabled = true;
  scanStatus.style.display = '';
  scanStatus.textContent = 'Scanning\u2026';
  document.getElementById('scan-list').innerHTML =
    '<div class="hp-empty">Waiting for results\u2026</div>';
  scanCol.style.display = '';
  _f('/api/hub/sdr/band_scan', {method:'POST', body: JSON.stringify({
    site: site,
    start_mhz: startMhz,
    end_mhz: endMhz,
    step_khz: 100,
    gain: gainSel.value,
    sdr_serial: sdrSel.value,
  })}).then(function(r){ return r.json(); }).then(function(d){
    if(!d.ok){ scanStatus.textContent = d.error||'Failed'; _scanning=false; return; }
    _scanPoll = setInterval(_pollScanResult, 3000);
    setTimeout(_pollScanResult, 2000);
  }).catch(function(){
    scanStatus.textContent = 'Request failed';
    _scanning = false;
    scanBtn.disabled = false;
  });
}
function stopScanPoll(){
  if(_scanPoll){ clearInterval(_scanPoll); _scanPoll=null; }
}
function _pollScanResult(){
  var site = siteSel.value; if(!site) return;
  fetch('/api/hub/sdr/scan_result/' + encodeURIComponent(site), {credentials:'same-origin'})
    .then(function(r){ return r.json(); })
    .then(function(d){
      if(!d.ready) return;
      stopScanPoll();
      _scanning = false;
      scanBtn.disabled = !siteSel.value;
      scanStatus.style.display = 'none';
      _renderScanPeaks(d.peaks || []);
    }).catch(function(){});
}
function _renderScanPeaks(peaks){
  var el = document.getElementById('scan-list');
  scanCol.style.display = '';
  if(!peaks.length){
    el.innerHTML = '<div class="hp-empty">No signals found</div>'; return;
  }
  var pMax = Math.max.apply(null, peaks.map(function(p){return p.power_db;}));
  var pMin = pMax - 25;
  el.innerHTML = peaks.map(function(p){
    var pct = Math.round(Math.max(0,Math.min(100,(p.power_db-pMin)/(pMax-pMin)*100)));
    return '<div class="scan-peak" data-f="'+p.freq_mhz+'">'
         + '<span class="sp-freq">'+p.freq_mhz.toFixed(3)+'</span>'
         + '<div class="sp-bar-bg"><div class="sp-bar" style="width:'+pct+'%"></div></div>'
         + '<span class="sp-db">'+p.power_db.toFixed(0)+' dB</span>'
         + '</div>';
  }).join('');
}
document.getElementById('scan-list').addEventListener('click', function(e){
  var pk = e.target.closest('.scan-peak');
  if(!pk) return;
  var freq = parseFloat(pk.dataset.f);
  _setMode('wfm');
  freqInp.value = _fromMhz(freq).toFixed(_useKhz ? 0 : 3);
  if(_state==='streaming'||_state==='connecting') doTune(freq);
  else if(siteSel.value) doStart(freq);
});
scanBtn.addEventListener('click', doScan);

// ── Level meter ────────────────────────────────────────────────────────────
var _levelBar  = document.getElementById('level-bar');
var _levelTxt  = document.getElementById('level-txt');
var _levelWrap = document.getElementById('level-wrap');
function _updateLevel(db){
  if(db === null || db === undefined){ _levelWrap.style.display='none'; return; }
  _levelWrap.style.display = '';
  var pct = Math.max(0, Math.min(100, (db + 80) / 80 * 100));
  _levelBar.style.width = pct + '%';
  var col = db > -20 ? 'var(--ok)' : db > -40 ? 'var(--wn)' : 'var(--al)';
  _levelBar.style.background = col;
  _levelTxt.style.color = col;
  _levelTxt.textContent = db.toFixed(1) + '\u202fdBFS';
}

// ── Spectrum polling ───────────────────────────────────────────────────────
function startSpectrumPoll(){
  stopSpectrumPoll();
  _specPoll = setInterval(_pollSpectrum, 100);
}
function stopSpectrumPoll(){
  if(_specPoll){ clearInterval(_specPoll); _specPoll=null; }
}
function _pollSpectrum(){
  if(!_slotId) return;
  fetch('/api/hub/sdr/spectrum/'+_slotId, {credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      if(!d.fft || !d.fft.length) return;
      // Hide the overlay on first frame — waterfall is now visible
      noSig.style.display = 'none';
      // Update BW if changed
      if(d.bw && Math.abs(d.bw - _bw) > 0.001){
        _bw = d.bw;
        _drawRuler();
      }
      _paintRow(d.fft);
      _updateLevel(d.level !== undefined ? d.level : null);
    }).catch(function(){});
}

// ── Keyboard shortcuts ─────────────────────────────────────────────────────
document.addEventListener('keydown', function(e){
  if(e.target.tagName==='INPUT'||e.target.tagName==='SELECT'||e.target.tagName==='TEXTAREA') return;
  if(_state!=='streaming'&&_state!=='connecting') return;
  var mult = e.shiftKey ? 5 : 1;
  var step = 0;
  if(e.key==='ArrowRight')    step =  0.1 * mult;
  else if(e.key==='ArrowLeft')  step = -0.1 * mult;
  else if(e.key==='ArrowUp')    step =  1.0 * mult;
  else if(e.key==='ArrowDown')  step = -1.0 * mult;
  else if(e.key==='PageUp')     step =  10.0 * mult;
  else if(e.key==='PageDown')   step = -10.0 * mult;
  else return;
  e.preventDefault();
  var freq = Math.round((_cf + step) * 1000) / 1000;
  freq = Math.max(0.1, Math.min(2000, freq));
  freqInp.value = _fromMhz(freq).toFixed(_useKhz ? 0 : 3);
  doTune(freq);
});

// ── History ────────────────────────────────────────────────────────────────
var _HIST_KEY = 'ss_sdr_hist';
var _HIST_MAX = 12;
function _loadHistory(){ try{return JSON.parse(localStorage.getItem(_HIST_KEY)||'[]');}catch(e){return[];} }
function _saveHistory(freq, mode){
  var list = _loadHistory().filter(function(h){
    return Math.abs(h.f - freq) > 0.05 || h.mode !== mode;
  });
  list.unshift({f: parseFloat(freq.toFixed(3)), mode: mode || 'wfm'});
  if(list.length > _HIST_MAX) list.length = _HIST_MAX;
  try{localStorage.setItem(_HIST_KEY, JSON.stringify(list));}catch(e){}
  _renderHistory(list);
}
function _renderHistory(list){
  if(!list) list = _loadHistory();
  var el = document.getElementById('hist-list');
  if(!el) return;
  if(!list.length){
    el.innerHTML = '<div class="hp-empty">Tune to build history</div>'; return;
  }
  el.innerHTML = list.map(function(h){
    return '<div class="hp-item" data-f="'+h.f+'" data-mode="'+(h.mode||'wfm')+'">'
         + '<span class="hp-freq">'+h.f.toFixed(3)+'</span>'
         + '<span class="hp-mode">'+(h.mode||'wfm').toUpperCase()+'</span>'
         + '</div>';
  }).join('');
}
document.getElementById('hist-list').addEventListener('click', function(e){
  var item = e.target.closest('.hp-item');
  if(!item) return;
  var freq = parseFloat(item.dataset.f);
  var mode = item.dataset.mode || 'wfm';
  _setMode(mode);
  if(_state==='streaming'||_state==='connecting') doTune(freq);
  else if(siteSel.value){ freqInp.value = _fromMhz(freq).toFixed(_useKhz?0:3); doStart(freq); }
});
_renderHistory();

// ── Presets ────────────────────────────────────────────────────────────────
var _PSET_KEY = 'ss_sdr_presets';
function _loadPresets(){ try{return JSON.parse(localStorage.getItem(_PSET_KEY)||'[]');}catch(e){return[];} }
function _savePreset(name, freq, mode){
  var list = _loadPresets().filter(function(p){return p.name!==name;});
  list.unshift({name:name, f:parseFloat(freq.toFixed(3)), mode:mode||'wfm'});
  try{localStorage.setItem(_PSET_KEY, JSON.stringify(list));}catch(e){}
  _renderPresets();
}
function _deletePreset(name){
  var list = _loadPresets().filter(function(p){return p.name!==name;});
  try{localStorage.setItem(_PSET_KEY, JSON.stringify(list));}catch(e){}
  _renderPresets();
}
function _renderPresets(){
  var el = document.getElementById('preset-list');
  if(!el) return;
  var list = _loadPresets();
  if(!list.length){
    el.innerHTML = '<div class="hp-empty">No presets — click + Save current while streaming</div>'; return;
  }
  el.innerHTML = list.map(function(p){
    return '<div class="hp-item" data-f="'+p.f+'" data-mode="'+(p.mode||'wfm')+'">'
         + '<span class="hp-freq">'+p.f.toFixed(3)+'</span>'
         + '<span class="hp-mode">'+(p.mode||'wfm').toUpperCase()+'</span>'
         + '<span class="hp-name">'+_esc(p.name)+'</span>'
         + '<span class="hp-del" data-pname="'+_esc(p.name)+'">✕</span>'
         + '</div>';
  }).join('');
}
document.getElementById('preset-list').addEventListener('click', function(e){
  var del = e.target.closest('.hp-del');
  if(del){ _deletePreset(del.dataset.pname); return; }
  var item = e.target.closest('.hp-item');
  if(!item) return;
  var freq = parseFloat(item.dataset.f);
  var mode = item.dataset.mode || 'wfm';
  _setMode(mode);
  if(_state==='streaming'||_state==='connecting') doTune(freq);
  else if(siteSel.value){ freqInp.value = _fromMhz(freq).toFixed(_useKhz?0:3); doStart(freq); }
});
document.getElementById('save-preset-btn').addEventListener('click', function(){
  if(_state!=='streaming'&&_state!=='connecting') return;
  var name = window.prompt('Preset name:', _cf.toFixed(3)+' MHz '+_mode.toUpperCase());
  if(!name||!name.trim()) return;
  _savePreset(name.trim(), _cf, _mode);
});
_renderPresets();

})();
</script>
</body>
</html>"""
