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
    "id":    "websdr",
    "label": "Web SDR",
    "url":   "/hub/sdr",
    "icon":  "📡",
}

import hashlib
import hmac as _hmac
import os
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
_hub_sessions  = {}   # site_name → {slot_id, freq_mhz, mode, sdr_serial}
_hub_pending   = {}   # site_name → pending command dict for client poller
_sdr_spectrum  = {}   # slot_id   → {fft, cf, bw, n, ts}
_client_sess   = {}   # {stop: Event, thread: Thread, slot_id: str}
_state_lock    = threading.Lock()


# ──────────────────────────────────────────────────────────────────────────────
# Plugin entry point
# ──────────────────────────────────────────────────────────────────────────────

def register(app, ctx):
    from flask import request, jsonify, render_template_string

    monitor         = ctx["monitor"]
    hub_server      = ctx.get("hub_server")
    listen_registry = ctx["listen_registry"]
    login_required  = ctx["login_required"]
    csrf_protect    = ctx["csrf_protect"]

    # ── Hub: browser page ────────────────────────────────────────────────────
    @app.get("/hub/sdr")
    @login_required
    def websdr_page():
        sites = []
        if hub_server:
            now = time.time()
            with hub_server._lock:
                for sname, sdata in sorted(hub_server._sites.items()):
                    if sdata.get("_approved") and not sdata.get("blocked"):
                        serials = sdata.get("scanner_serials", [])
                        if serials:
                            online = (now - sdata.get("_received", 0)) < 120
                            sites.append({"name": sname, "online": online,
                                          "serials": serials})
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
    # Authenticated by slot_id (treated as a bearer token, set by hub at start)
    @app.post("/api/hub/sdr/spectrum/<slot_id>")
    def websdr_spectrum_push(slot_id):
        d = request.get_json(silent=True) or {}
        if d:
            d["ts"] = time.time()
            _sdr_spectrum[slot_id] = d
            # Prune stale entries (> 300 slots is excessive)
            if len(_sdr_spectrum) > 300:
                oldest = sorted(_sdr_spectrum, key=lambda k: _sdr_spectrum[k].get("ts",0))
                for k in oldest[:100]:
                    _sdr_spectrum.pop(k, None)
        return "", 204

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


def _start_capture(slot_id, freq_mhz, mode, gain, sdr_serial, hub_url, secret):
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
    """
    import shutil, subprocess, json as _json

    chunk_url    = f"{hub_url}/api/v1/audio_chunk/{slot_id}"
    spectrum_url = f"{hub_url}/api/hub/sdr/spectrum/{slot_id}"

    rtl_sdr_bin = shutil.which("rtl_sdr")
    if not rtl_sdr_bin:
        print("[WebSDR] rtl_sdr not found in PATH — install rtl-sdr package")
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

    print(f"[WebSDR] Starting capture: {freq_mhz:.3f} MHz  mode={mode}  "
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

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=_BLK_BYTES * 4,
        )

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
                # Pre-filter to ±12 kHz then FM discriminate
                if _HAS_SCIPY:
                    sos  = _sp.butter(5, 12_000 / (_SAMPLE_RATE / 2), output="sos")
                    iq_f = _sp.sosfilt(sos, iq)
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

            # ── Pace to real-time ─────────────────────────────────────────
            if out_deadline is None:
                out_deadline = time.monotonic()
            slack = out_deadline - time.monotonic()
            if slack > 0.001:
                time.sleep(slack)

            # ── Push PCM to hub audio relay ───────────────────────────────
            try:
                ts_c  = time.time()
                sig_c = _sign_chunk(secret, pcm_b, ts_c) if secret else ""
                hdrs  = {
                    "Content-Type": "application/octet-stream",
                    "X-Hub-Sig":    sig_c,
                    "X-Hub-Ts":     f"{ts_c:.0f}",
                    "X-Hub-Nonce":  hashlib.md5(os.urandom(8)).hexdigest()[:16],
                }
                req = urllib.request.Request(chunk_url, data=pcm_b,
                                             headers=hdrs, method="POST")
                urllib.request.urlopen(req, timeout=5).close()
            except Exception as e:
                print(f"[WebSDR] PCM push failed: {e}")

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
                             "bw": _SAMPLE_RATE / 1e6, "n": n}
                    data  = _json.dumps(frame).encode()
                    req   = urllib.request.Request(
                        spectrum_url, data=data,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    urllib.request.urlopen(req, timeout=2).close()
                except Exception:
                    pass

    except Exception as e:
        print(f"[WebSDR] Worker error: {e}")
    finally:
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                pass
        print(f"[WebSDR] Worker stopped for slot {slot_id[:6]}")


# ──────────────────────────────────────────────────────────────────────────────
# Browser template
# ──────────────────────────────────────────────────────────────────────────────

WEBSDR_TPL = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Web SDR — SignalScope</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" type="image/x-icon" href="/static/signalscope_icon.png">
<style>
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
    <option value="{{s.name|e}}"{% if not s.online %} disabled{% endif %}>
      {{s.name|e}}{% if not s.online %} (offline){% endif %}
    </option>
    {% endfor %}
    {% if not sites %}<option disabled>No sites with Scanner dongle</option>{% endif %}
  </select>

  <label>SDR</label>
  <select id="sdr-sel"><option value="">Auto</option></select>

  <label>Freq</label>
  <input type="number" id="freq-inp" value="96.5" step="0.1" min="50" max="2000"
         style="width:90px">
  <span style="font-size:12px;color:var(--mu)">MHz</span>

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
  <div class="tuner-btns">
    <button class="tune-btn" id="btn-dn" data-step="-0.1">−0.1</button>
    <button class="tune-btn" id="btn-up" data-step="0.1">+0.1</button>
  </div>
  <div class="vol-wrap">
    <label>Vol</label>
    <input type="range" id="vol-slider" min="0" max="2" step="0.05" value="1">
  </div>
</div>

<script>
(function(){
'use strict';

var _csrf = (document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/)||[])[1]||'';
function _f(url,o){o=o||{};o.credentials='same-origin';
  o.headers=Object.assign({'X-CSRFToken':_csrf,'Content-Type':'application/json'},o.headers||{});
  return fetch(url,o);}

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
var gainSel   = document.getElementById('gain-sel');
var connBtn   = document.getElementById('conn-btn');
var sDot      = document.getElementById('s-dot');
var sTxt      = document.getElementById('s-txt');
var freqDisp  = document.getElementById('freq-disp');
var volSlider = document.getElementById('vol-slider');
var noSig     = document.getElementById('no-sig');

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
    setState('streaming', '● Live — ' + _cf.toFixed(3) + ' MHz');
    noSig.style.display = 'none';
  }
}

// ── Status helpers ─────────────────────────────────────────────────────────
function setState(st, msg){
  _state = st;
  sDot.className = 'sdot ' + st;
  sTxt.textContent = msg;
  connBtn.textContent = (st==='streaming'||st==='connecting') ? 'Disconnect' : 'Connect';
  connBtn.className = 'btn-conn ' + ((st==='streaming'||st==='connecting') ? 'active' : 'idle');
}

// ── Mode buttons ───────────────────────────────────────────────────────────
document.getElementById('mode-sel').addEventListener('click', function(e){
  var mb = e.target.closest('.mode-btn');
  if(!mb) return;
  document.querySelectorAll('.mode-btn').forEach(function(b){b.classList.remove('sel');});
  mb.classList.add('sel');
  _mode = mb.dataset.mode;
  if(_state==='streaming'||_state==='connecting') doTune(_cf);
});

// ── Connect/Disconnect ─────────────────────────────────────────────────────
connBtn.addEventListener('click', function(){
  if(_state==='streaming'||_state==='connecting') doStop();
  else doStart(parseFloat(freqInp.value)||96.5);
});

function doStart(freq){
  if(!siteSel.value){ setState('error','Select a site'); return; }
  _cf = freq; freqDisp.textContent = freq.toFixed(3);
  setState('connecting','Connecting…');
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
  _cf = freq; freqDisp.textContent = freq.toFixed(3);
  setState('connecting','Retuning…');
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
  stopSpectrumPoll(); disconnectAudio();
  _f('/api/hub/sdr/stop',{method:'POST',body:JSON.stringify({site:siteSel.value})})
    .catch(function(){});
  setState('idle','Idle');
  noSig.style.display = '';
  _slotId = '';
}

// ── SDR selector ───────────────────────────────────────────────────────────
siteSel.addEventListener('change', function(){
  sdrSel.innerHTML = '<option value="">Auto</option>';
  var site = siteSel.value; if(!site) return;
  _f('/api/hub/scanner/devices/' + encodeURIComponent(site))
    .then(function(r){return r.json();})
    .then(function(d){
      (d.serials||[]).forEach(function(s){
        var o=document.createElement('option');
        o.value=s; o.textContent=s; sdrSel.appendChild(o);
      });
    }).catch(function(){});
});
if(siteSel.value) siteSel.dispatchEvent(new Event('change'));

// ── Tune step buttons ──────────────────────────────────────────────────────
document.querySelectorAll('.tune-btn').forEach(function(b){
  b.addEventListener('click', function(){
    doTune(Math.round((_cf + parseFloat(b.dataset.step)) * 1000) / 1000);
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
  _wfW = wrap.clientWidth;
  _wfH = wrap.clientHeight;
  _wfCanvas.width  = _wfW;
  _wfCanvas.height = _wfH;
  _ruCanvas.width  = _ruCanvas.parentElement.clientWidth;
  _ruCanvas.height = 22;
  _drawRuler();
}
window.addEventListener('resize', _resize);
_resize();

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
  freqInp.value = freq.toFixed(3);
  doTune(freq);
});
_wfCanvas.title = 'Click to tune';

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
      // Update BW if changed
      if(d.bw && Math.abs(d.bw - _bw) > 0.001){
        _bw = d.bw;
        _drawRuler();
      }
      _paintRow(d.fft);
    }).catch(function(){});
}

})();
</script>
</body>
</html>"""
