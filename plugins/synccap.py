"""
synccap.py  —  Multi-site synchronized audio capture

Hub page lets you pick any number of inputs across any connected sites,
set a capture duration (5–300 s), and press Capture.  The hub broadcasts a
"capture at T+15 s" command to each client via heartbeat ACK; each client
grabs the last N seconds from its _stream_buffer and uploads the audio back
to the hub.  Hub-local inputs are captured directly.  All clips for a session
are presented together with inline audio players for side-by-side comparison.

Hub-only plugin — nav item hidden on client/standalone nodes.

v1.0.13: atomic DB writes, upload size limit, streaming clip serve, bounded
processed-capture set, pending-cmd queue per site, upload validation, EBU R128
LUFS/true-peak, level-diff dB, sub-sample lag interpolation, octave-band
spectrum, stereo L/R analysis, RDS/DLS metadata snapshot, BWF export, async
alignment with job polling, single heapq scheduler, JS Map cap cache,
ResizeObserver waveforms, reference clip selector, pagination + search,
timestamp label pre-fill, per-clip download buttons, configurable storage path.
"""

SIGNALSCOPE_PLUGIN = {
    "id":       "synccap",
    "label":    "Sync Capture",
    "url":      "/hub/synccap",
    "icon":     "🎙",
    "hub_only": True,
    "version":  "1.0.13",
}

import os
import json
import uuid
import time
import wave
import io
import threading
import shutil
import struct
import heapq
import re
import urllib.request
import urllib.error
from collections import deque

try:
    import numpy as np
    _HAS_NP = True
except ImportError:
    _HAS_NP = False

# ── constants ─────────────────────────────────────────────────────────────────

_SAMPLE_RATE       = 48000
_MIN_DUR           = 5
_MAX_DUR           = 300
_CLIENT_POLL_S     = 3
_EXPIRE_S          = 180
_MAX_UPLOAD_BYTES  = _MAX_DUR * _SAMPLE_RATE * 2 * 2 + 65536  # 300 s stereo WAV + header
_MAX_PROC_CAPTURES = 500      # bounded dedup set (Bug 4)
_MAX_ALIGN_JOBS    = 100      # max async alignment job entries

# ── module-level state (set in register) ──────────────────────────────────────

_CLIP_DIR   = None   # plugins/synccap_clips/
_DB_PATH    = None   # plugins/synccap_db.json
_CFG_PATH   = None   # plugins/synccap_cfg.json
_db_lock    = threading.Lock()

# Bug 5: pending cmds as queue per site
_pending_cmds  = {}   # site → [(cmd_dict, expiry_ts), …]
_pending_lock  = threading.Lock()

# Bug 4: bounded processed-captures set
_proc_set  = set()
_proc_deq  = deque()
_proc_lock = threading.Lock()

# Item 24: single heapq scheduler
_sched_heap  = []
_sched_hlock = threading.Lock()
_sched_wake  = threading.Event()
_sched_seq   = 0

# Item 23: async alignment jobs
_align_jobs     = {}   # job_id → {"status": …, "result": …, "ts": …}
_align_seq_list = []   # FIFO for eviction
_align_lock     = threading.Lock()

# Globals set during register() for use by scheduled event handlers
_cfg_ss      = None
_hub_srv     = None
_monitor_ref = None


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _load_db():
    if not _DB_PATH or not os.path.exists(_DB_PATH):
        return {}
    try:
        with open(_DB_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_db(data):
    """Bug 1: atomic write via temp file + os.replace()."""
    tmp = _DB_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, _DB_PATH)


# ── Plugin config helpers (Item 27) ───────────────────────────────────────────

def _load_plugin_cfg():
    if not _CFG_PATH or not os.path.exists(_CFG_PATH):
        return {}
    try:
        with open(_CFG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_plugin_cfg(data):
    tmp = _CFG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, _CFG_PATH)


# ── Bounded processed-captures (Bug 4) ────────────────────────────────────────

def _add_processed(cid):
    """Mark capture_id as processed. Returns False if already seen."""
    with _proc_lock:
        if cid in _proc_set:
            return False
        _proc_set.add(cid)
        _proc_deq.append(cid)
        while len(_proc_deq) > _MAX_PROC_CAPTURES:
            _proc_set.discard(_proc_deq.popleft())
        return True


# ── EBU R128 / loudness helpers (Item 7) ──────────────────────────────────────

def _k_weight(x):
    """Apply ITU-R BS.1770 K-weighting filter chain (48 kHz). Uses scipy if available."""
    try:
        from scipy import signal as _sp
        b1 = [1.53512485958697, -2.69169618940638, 1.19839281085285]
        a1 = [1.0, -1.69065929318241, 0.73248077421585]
        b2 = [1.0, -2.0, 1.0]
        a2 = [1.0, -1.99004745483398, 0.99007225036603]
        y = _sp.lfilter(b1, a1, x.astype(np.float64))
        return _sp.lfilter(b2, a2, y).astype(np.float32)
    except ImportError:
        return x   # no scipy: return unweighted (LUFS ≈ RMS-based)


def _analyse_lufs(audio_mono):
    """Return (lufs_i, tp_dbfs) for a mono float32 array. Both may be None."""
    if not _HAS_NP or len(audio_mono) == 0:
        return None, None
    weighted = _k_weight(audio_mono)
    ms = float(np.mean(weighted ** 2))
    lufs = round(-0.691 + 10.0 * np.log10(ms + 1e-9), 1)
    tp   = round(20.0 * np.log10(float(np.max(np.abs(audio_mono))) + 1e-9), 1)
    return lufs, tp


# ── Octave-band spectrum (Item 10) ────────────────────────────────────────────

_OCTAVE_CENTERS = [63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]


def _octave_bands(audio, sr=_SAMPLE_RATE):
    """Return {str(fc): dBFS} for each 1-octave band. Uses middle 10 s."""
    if not _HAS_NP or len(audio) < sr:
        return None
    n   = len(audio)
    win = min(n, int(10 * sr))
    seg = audio[(n - win) // 2 : (n - win) // 2 + win]
    nfft = max(512, 1 << int(np.log2(len(seg))))
    freq = np.fft.rfftfreq(nfft, 1.0 / sr)
    mag2 = np.abs(np.fft.rfft(seg, n=nfft)) ** 2
    result = {}
    for fc in _OCTAVE_CENTERS:
        fl, fh = fc / 2 ** 0.5, fc * 2 ** 0.5
        mask = (freq >= fl) & (freq < fh)
        e = float(np.sum(mag2[mask])) if mask.any() else 0.0
        result[str(fc)] = round(10.0 * np.log10(e / nfft + 1e-9), 1)
    return result


# ── Stereo L/R analysis (Item 11) ─────────────────────────────────────────────

def _stereo_analysis(wav_path):
    """Return {l_rms_dbfs, r_rms_dbfs, balance_db, lr_corr} or None for mono."""
    if not _HAS_NP:
        return None
    try:
        with wave.open(wav_path, "rb") as wf:
            if wf.getnchannels() != 2:
                return None
            raw = wf.readframes(wf.getnframes())
        pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0
        st  = pcm.reshape(-1, 2)
        L, R = st[:, 0], st[:, 1]
        l_rms = float(np.sqrt(np.mean(L ** 2)))
        r_rms = float(np.sqrt(np.mean(R ** 2)))
        bal   = round(20.0 * np.log10((r_rms + 1e-9) / (l_rms + 1e-9)), 1)
        lc, rc = L - L.mean(), R - R.mean()
        lr_corr = round(float(np.dot(lc, rc) / (len(L) * (np.std(lc) * np.std(rc) + 1e-9))), 3)
        return {
            "l_rms_dbfs": round(20.0 * np.log10(l_rms + 1e-9), 1),
            "r_rms_dbfs": round(20.0 * np.log10(r_rms + 1e-9), 1),
            "balance_db": bal,
            "lr_corr":    round(min(1.0, max(-1.0, lr_corr)), 3),
        }
    except Exception:
        return None


# ── BWF bext-chunk builder (Item 17) ──────────────────────────────────────────

def _build_bwf(wav_bytes, stream_name, site, captured_at_ts, lufs=None):
    """Insert a BWF bext chunk into an existing WAV file. Returns new bytes."""
    if len(wav_bytes) < 44:
        return wav_bytes
    orig_date = time.strftime("%Y-%m-%d", time.gmtime(captured_at_ts))
    orig_time = time.strftime("%H:%M:%S", time.gmtime(captured_at_ts))
    try:
        midnight  = time.mktime(time.strptime(orig_date, "%Y-%m-%d"))
        time_ref  = int((captured_at_ts - midnight) * _SAMPLE_RATE)
    except Exception:
        time_ref  = 0
    time_ref_lo = time_ref & 0xFFFFFFFF
    time_ref_hi = (time_ref >> 32) & 0xFFFFFFFF
    lufs_int = int(round((lufs or -99.0) * 100))

    desc    = (f"SyncCap: {stream_name} @ {site}").encode("ascii", "replace")[:256].ljust(256, b"\x00")
    orig    = b"SignalScope SyncCap\x00".ljust(32, b"\x00")[:32]
    origref = b"synccap\x00".ljust(32, b"\x00")[:32]
    dstr    = orig_date.encode("ascii").ljust(10, b"\x00")[:10]
    tstr    = orig_time.encode("ascii").ljust(8, b"\x00")[:8]

    bext_fixed = (
        desc + orig + origref + dstr + tstr
        + struct.pack("<IIH", time_ref_lo, time_ref_hi, 2)
        + b"\x00" * 64
        + struct.pack("<hhhhh", lufs_int, 0, 0, lufs_int, lufs_int)
        + b"\x00" * 180
    )
    coding = b"A=PCM,F=48000,W=16,M=stereo,T=SignalScope SyncCap\r\n\x00"
    if len(coding) % 2:
        coding += b"\x00"
    bext_data  = bext_fixed + coding
    bext_chunk = b"bext" + struct.pack("<I", len(bext_data)) + bext_data

    new_riff_size = len(wav_bytes) - 8 + len(bext_chunk)
    return b"RIFF" + struct.pack("<I", new_riff_size) + b"WAVE" + bext_chunk + wav_bytes[12:]


# ── audio helpers ─────────────────────────────────────────────────────────────

def _chunks_to_pcm(chunks, duration_s, n_ch):
    if not _HAS_NP or not chunks:
        return b""
    need = int(duration_s * _SAMPLE_RATE) * n_ch
    try:
        arr = np.concatenate(list(chunks))
        if arr.size > need:
            arr = arr[-need:]
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


def _capture_input(cfg, duration_s):
    """Grab duration_s of audio. Returns (wav_bytes, 'wav', n_ch) or (None,None,None)."""
    if not _HAS_NP:
        return None, None, None
    try:
        n_ch = getattr(cfg, "_audio_channels", 1) or 1
        if n_ch == 2:
            abuf = getattr(cfg, "_audio_buffer", None)
            sbuf = getattr(cfg, "_stream_buffer", None)
            if abuf and sbuf:
                s_chunks = list(sbuf)
                a_chunks = list(abuf)
                if s_chunks and a_chunks:
                    stereo_size = s_chunks[-1].size * 2
                    safe = []
                    for c in reversed(a_chunks):
                        if c.size == stereo_size:
                            safe.append(c)
                        else:
                            break
                    safe.reverse()
                    need_frames = int(duration_s * _SAMPLE_RATE)
                    have_frames = sum(c.size for c in safe) // 2
                    if safe and have_frames >= need_frames * 0.9:
                        audio  = np.concatenate(safe)
                        frames = audio.reshape(-1, 2)[-need_frames:]
                        audio  = np.clip(frames.flatten(), -1.0, 1.0)
                        pcm    = (audio * 32767).astype(np.int16).tobytes()
                        if pcm:
                            return _pcm_to_wav(pcm, 2), "wav", 2
        buf = getattr(cfg, "_stream_buffer", None)
        if buf is None:
            return None, None, None
        pcm = _chunks_to_pcm(buf, duration_s, 1)
        if not pcm:
            return None, None, None
        return _pcm_to_wav(pcm, 1), "wav", 1
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


# ── RDS/DLS metadata snapshot (Item 16) ───────────────────────────────────────

def _snap_metadata(site, stream_name):
    """Snapshot RDS/DLS/NowPlaying metadata for a stream right now."""
    result = {}
    try:
        if site == "(hub)" and _cfg_ss is not None:
            inp = next((i for i in _cfg_ss.inputs if i.name == stream_name), None)
            if inp:
                rds = getattr(inp, "_scanner_rds", None)
                if rds:
                    result["rds"] = {k: v for k, v in rds.items() if not k.startswith("_")}
                dls = getattr(inp, "_dls_text", None) or getattr(inp, "_rds_text", None)
                if dls:
                    result["dls"] = str(dls)
        elif _hub_srv is not None:
            sdata = _hub_srv._sites.get(site, {})
            for s in sdata.get("streams", []):
                if s.get("name") == stream_name:
                    np_d = s.get("nowplaying") or s.get("rds")
                    if np_d:
                        result["nowplaying"] = np_d
                    break
    except Exception:
        pass
    return result or None


# ── hub-capture + expiry event handlers (used by scheduler) ──────────────────

def _do_hub_capture(capture_id, hub_streams, duration):
    for stream_name in hub_streams:
        inp = next((i for i in _cfg_ss.inputs if i.name == stream_name), None)
        if not inp:
            continue
        audio, ext, n_ch = _capture_input(inp, duration)
        if not audio:
            if _monitor_ref:
                _monitor_ref.log(f"[SyncCap] Hub: no audio for '{stream_name}'")
            continue
        fname = f"{capture_id}_{_safe('hub')}_{_safe(stream_name)}.{ext}"
        path  = os.path.join(_CLIP_DIR, fname)
        with open(path, "wb") as f:
            f.write(audio)
        meta = _snap_metadata("(hub)", stream_name)
        # per-clip LUFS
        try:
            with wave.open(path, "rb") as wf:
                raw = wf.readframes(wf.getnframes())
            pcm_a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0
            if wf.getnchannels() == 2:
                pcm_a = pcm_a.reshape(-1, 2).mean(axis=1)
            lufs, tp = _analyse_lufs(pcm_a)
        except Exception:
            lufs, tp = None, None
        with _db_lock:
            db2 = _load_db()
            if capture_id in db2:
                clip_rec = {
                    "site": "(hub)", "stream": stream_name,
                    "filename": fname, "n_ch": n_ch or 1,
                    "received_at": time.time(),
                }
                if meta:
                    clip_rec["metadata"] = meta
                if lufs is not None:
                    clip_rec["lufs"] = lufs
                if tp is not None:
                    clip_rec["tp_dbfs"] = tp
                db2[capture_id]["clips"].append(clip_rec)
                _update_status(db2[capture_id])
                _save_db(db2)
        if _monitor_ref:
            _monitor_ref.log(f"[SyncCap] Hub captured '{stream_name}' → {fname}")


def _do_expire(capture_id):
    with _db_lock:
        db2 = _load_db()
        if capture_id in db2 and db2[capture_id]["status"] in ("waiting", "partial"):
            db2[capture_id]["status"] = "expired"
            _save_db(db2)


# ── single heapq scheduler (Item 24) ─────────────────────────────────────────

def _schedule(at, etype, *args):
    global _sched_seq
    with _sched_hlock:
        heapq.heappush(_sched_heap, (at, _sched_seq, etype, args))
        _sched_seq += 1
    _sched_wake.set()


def _sched_loop():
    """Single daemon thread replacing per-capture SyncCapHub + SyncCapExpire threads."""
    while True:
        with _sched_hlock:
            next_at = _sched_heap[0][0] if _sched_heap else None
        if next_at is None:
            _sched_wake.wait(timeout=60)
            _sched_wake.clear()
            continue
        wait = next_at - time.time()
        if wait > 0:
            _sched_wake.wait(timeout=min(wait, 60))
            _sched_wake.clear()
            continue
        # Pop and dispatch
        with _sched_hlock:
            if not _sched_heap or _sched_heap[0][0] > time.time():
                continue
            _, _, etype, args = heapq.heappop(_sched_heap)
        if etype == "hub_cap":
            cap_id, hub_streams, duration = args
            threading.Thread(
                target=_do_hub_capture,
                args=(cap_id, hub_streams, duration),
                daemon=True,
                name=f"SyncCapHub-{cap_id[:6]}",
            ).start()
        elif etype == "expire":
            cap_id, = args
            _do_expire(cap_id)


# ── alignment core (Item 9 sub-sample, shared by sync + async) ───────────────

def _compute_alignment(cap, ref_fn=None):
    """Compute alignment data for a capture dict. Returns result dict or raises."""
    clips = cap.get("clips", [])
    if len(clips) < 2:
        raise ValueError("Need at least 2 clips to align")

    sr = _SAMPLE_RATE

    # Load each clip as mono float32; keep stereo for per-clip analysis
    loaded = []
    for cl in clips:
        path = os.path.join(_CLIP_DIR, cl["filename"])
        if not os.path.exists(path):
            continue
        try:
            with wave.open(path, "rb") as wf:
                n_ch = wf.getnchannels()
                raw  = wf.readframes(wf.getnframes())
            pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0
            mono = pcm.reshape(-1, 2).mean(axis=1) if n_ch == 2 else pcm
            loaded.append({
                "filename": cl["filename"],
                "site":     cl["site"],
                "stream":   cl["stream"],
                "audio":    mono,
                "n_ch":     n_ch,
            })
        except Exception:
            continue

    if len(loaded) < 2:
        raise ValueError("Could not load enough clips for alignment")

    # If caller specified a reference, put it first
    if ref_fn:
        idx = next((i for i, x in enumerate(loaded) if x["filename"] == ref_fn), None)
        if idx is not None and idx != 0:
            loaded.insert(0, loaded.pop(idx))

    # Step 1: FFT cross-correlation with sub-sample parabolic interpolation (Item 9)
    min_dur  = min(len(c["audio"]) for c in loaded) / sr
    window_s = min(10.0, min_dur / 2.0)
    window_n = max(1, int(window_s * sr))

    def _norm(a):
        s = np.std(a)
        return a / (s + 1e-9)

    ref     = loaded[0]
    ref_win = _norm(ref["audio"][:window_n])
    lags    = {ref["filename"]: 0.0}

    for item in loaded[1:]:
        tgt_win = _norm(item["audio"][:window_n])
        n       = len(ref_win) + len(tgt_win) - 1
        corr    = np.fft.irfft(
            np.fft.rfft(ref_win, n) * np.conj(np.fft.rfft(tgt_win, n)), n
        )
        k_star = int(np.argmax(corr))
        # Sub-sample parabolic interpolation
        if 0 < k_star < len(corr) - 1:
            y0, y1, y2 = corr[k_star - 1], corr[k_star], corr[k_star + 1]
            delta   = 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2 + 1e-12)
            k_interp = k_star + delta
        else:
            k_interp = float(k_star)
        if k_interp > n // 2:
            k_interp -= n
        lags[item["filename"]] = k_interp / sr

    max_lag  = max(lags.values())
    offsets  = {fn: round(max_lag - lag, 3) for fn, lag in lags.items()}
    durations = {c["filename"]: round(len(c["audio"]) / sr, 3) for c in loaded}

    min_remaining = min(durations[fn] - offsets.get(fn, 0.0) for fn in durations)
    overlap_s = round(max(0.0, min_remaining), 3)

    # Step 2: align audio arrays by offset
    aligned_audio = {}
    for item in loaded:
        fn   = item["filename"]
        skip = int(offsets[fn] * sr)
        aligned_audio[fn] = item["audio"][skip:]

    overlap_n = int(overlap_s * sr)

    # Step 3: RMS envelope correlation scores
    _ENV_BLOCK = int(0.050 * sr)
    score_s    = min(15.0, overlap_s * 0.6)
    score_n    = max(_ENV_BLOCK * 2, int(score_s * sr))
    mid_start  = max(0, (overlap_n - score_n) // 2)

    def _rms_env_seg(audio, start, n, block):
        seg = audio[start: start + n]
        if len(seg) < block:
            return np.array([float(np.sqrt(np.mean(seg ** 2)))])
        nb  = len(seg) // block
        blk = seg[: nb * block].reshape(nb, block)
        return np.sqrt(np.mean(blk ** 2, axis=1))

    ref_fn  = ref["filename"]
    ref_env = _rms_env_seg(aligned_audio[ref_fn], mid_start, score_n, _ENV_BLOCK)
    ref_env = ref_env - ref_env.mean()
    ref_std = float(np.std(ref_env)) + 1e-9

    scores = {ref_fn: 1.0}
    for item in loaded[1:]:
        fn      = item["filename"]
        tgt_env = _rms_env_seg(aligned_audio[fn], mid_start, score_n, _ENV_BLOCK)
        if len(tgt_env) < 2:
            scores[fn] = None
            continue
        n_use = min(len(ref_env), len(tgt_env))
        r_env = ref_env[:n_use]
        t_env = tgt_env[:n_use] - tgt_env[:n_use].mean()
        tgt_std = float(np.std(t_env)) + 1e-9
        r = float(np.dot(r_env / ref_std, t_env / tgt_std)) / n_use
        scores[fn] = round(max(0.0, min(1.0, r)), 4)

    # Step 4: level differences dB vs reference (Item 8)
    ref_rms_val = float(np.sqrt(np.mean(aligned_audio[ref_fn][:overlap_n] ** 2))) if overlap_n > 0 else 1e-9
    level_diffs = {ref_fn: 0.0}
    for item in loaded[1:]:
        fn = item["filename"]
        seg = aligned_audio[fn][:overlap_n]
        if overlap_n > 0 and len(seg) > 0:
            tgt_rms = float(np.sqrt(np.mean(seg ** 2)))
            ld = round(20.0 * np.log10((tgt_rms + 1e-9) / (ref_rms_val + 1e-9)), 1)
        else:
            ld = None
        level_diffs[fn] = ld

    # Step 5: per-clip LUFS + true peak (Item 7)
    lufs_data = {}
    tp_data   = {}
    for item in loaded:
        fn   = item["filename"]
        lufs, tp = _analyse_lufs(item["audio"])
        lufs_data[fn] = lufs
        tp_data[fn]   = tp

    # Step 6: octave bands (Item 10)
    bands_data = {}
    for item in loaded:
        fn = item["filename"]
        bands_data[fn] = _octave_bands(item["audio"])

    # Step 7: stereo L/R analysis (Item 11)
    stereo_data = {}
    for item in loaded:
        fn = item["filename"]
        if item["n_ch"] == 2:
            path = os.path.join(_CLIP_DIR, fn)
            stereo_data[fn] = _stereo_analysis(path)

    # Step 8: waveform thumbnails
    _WF_POINTS = 600

    def _rms_envelope(audio, n_points):
        if len(audio) == 0:
            return [0.0] * n_points
        block = max(1, len(audio) // n_points)
        out = []
        for i in range(n_points):
            sl = audio[i * block: (i + 1) * block]
            out.append(float(np.sqrt(np.mean(sl ** 2))) if len(sl) else 0.0)
        return out

    waveforms         = {}
    compare_waveforms = {}
    for item in loaded:
        fn  = item["filename"]
        seg = aligned_audio[fn]
        waveforms[fn]         = _rms_envelope(seg, _WF_POINTS)
        ov = seg[:overlap_n] if overlap_n > 0 else seg
        compare_waveforms[fn] = _rms_envelope(ov, _WF_POINTS)

    return {
        "offsets":           offsets,
        "durations":         durations,
        "overlap_s":         overlap_s,
        "scores":            scores,
        "level_diffs":       level_diffs,
        "lufs":              lufs_data,
        "tp_dbfs":           tp_data,
        "bands":             bands_data,
        "stereo":            stereo_data,
        "waveforms":         waveforms,
        "compare_waveforms": compare_waveforms,
        "ref_filename":      ref_fn,
    }


# ── client-side command handler ───────────────────────────────────────────────

def _handle_capture_cmd(cmd, monitor, hub_url, site):
    capture_id = cmd.get("capture_id")
    capture_at = cmd.get("capture_at", time.time())
    duration_s = cmd.get("duration_s", 30)
    streams    = cmd.get("streams", [])

    if not _add_processed(capture_id):
        monitor.log(f"[SyncCap] Client: duplicate capture {capture_id} — ignored")
        return

    monitor.log(
        f"[SyncCap] Client received capture {capture_id}: "
        f"{len(streams)} stream(s), {duration_s} s, "
        f"T+{capture_at - time.time():.1f} s"
    )

    wait = capture_at - time.time()
    if 0 < wait <= 30:
        time.sleep(wait)

    for stream_name in streams:
        inp = next((i for i in monitor.app_cfg.inputs if i.name == stream_name), None)
        if not inp:
            monitor.log(f"[SyncCap] Client: stream '{stream_name}' not found (capture {capture_id})")
            continue
        audio, ext, n_ch = _capture_input(inp, duration_s)
        if not audio:
            monitor.log(f"[SyncCap] Client: no audio for '{stream_name}' (capture {capture_id})")
            continue
        monitor.log(f"[SyncCap] Client: captured {len(audio)} bytes for '{stream_name}', uploading…")
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
            monitor.log(f"[SyncCap] Client: uploaded '{stream_name}' OK (capture {capture_id})")
        except Exception as exc:
            monitor.log(f"[SyncCap] Client: upload failed for '{stream_name}' (capture {capture_id}): {exc}")


# ── register ──────────────────────────────────────────────────────────────────

def register(app, ctx):
    global _CLIP_DIR, _DB_PATH, _CFG_PATH, _cfg_ss, _hub_srv, _monitor_ref

    login_required = ctx["login_required"]
    csrf_protect   = ctx["csrf_protect"]
    monitor        = ctx["monitor"]
    hub_server     = ctx["hub_server"]
    BUILD          = ctx["BUILD"]

    _monitor_ref = monitor
    _hub_srv     = hub_server

    _plugin_dir = os.path.dirname(os.path.abspath(__file__))
    _DB_PATH    = os.path.join(_plugin_dir, "synccap_db.json")
    _CFG_PATH   = os.path.join(_plugin_dir, "synccap_cfg.json")

    # Item 27: configurable clip directory
    pcfg     = _load_plugin_cfg()
    clip_dir = pcfg.get("clip_dir", "").strip()
    _CLIP_DIR = clip_dir if clip_dir else os.path.join(_plugin_dir, "synccap_clips")
    os.makedirs(_CLIP_DIR, exist_ok=True)

    cfg_ss  = monitor.app_cfg
    _cfg_ss = cfg_ss
    mode    = getattr(getattr(cfg_ss, "hub", None), "mode", "standalone") or "standalone"
    hub_url = (getattr(getattr(cfg_ss, "hub", None), "hub_url", "") or "").rstrip("/")

    is_hub    = mode in ("hub", "both")
    is_client = mode == "client" and bool(hub_url)

    # ── Client node ───────────────────────────────────────────────────────────
    if is_client:
        register_cmd_handler = ctx.get("register_cmd_handler")
        if register_cmd_handler:
            def _on_synccap_capture(payload):
                cfg2  = monitor.app_cfg
                h_url = (getattr(getattr(cfg2, "hub", None), "hub_url", "") or "").rstrip("/")
                site2 = getattr(getattr(cfg2, "hub", None), "site_name", "") or ""
                threading.Thread(
                    target=_handle_capture_cmd,
                    args=(payload, monitor, h_url, site2),
                    daemon=True, name="SyncCapCapture",
                ).start()
            register_cmd_handler("synccap_capture", _on_synccap_capture)
            monitor.log("[SyncCap] Client handler registered (heartbeat delivery)")
        else:
            monitor.log("[SyncCap] Core pre-3.5.84 — using poll fallback")
            def _poll_for_captures():
                last_err = 0.0
                while True:
                    try:
                        cfg2  = monitor.app_cfg
                        h_url = (getattr(getattr(cfg2, "hub", None), "hub_url", "") or "").rstrip("/")
                        site2 = getattr(getattr(cfg2, "hub", None), "site_name", "") or ""
                        if not h_url or not site2:
                            time.sleep(_CLIENT_POLL_S); continue
                        req  = urllib.request.Request(f"{h_url}/api/synccap/cmd", headers={"X-Site": site2})
                        data = json.loads(urllib.request.urlopen(req, timeout=5).read())
                        cmd  = data.get("cmd")
                        if cmd:
                            threading.Thread(
                                target=_handle_capture_cmd,
                                args=(cmd, monitor, h_url, site2),
                                daemon=True, name="SyncCapCapture",
                            ).start()
                    except Exception as exc:
                        now = time.time()
                        if now - last_err > 30:
                            monitor.log(f"[SyncCap] Poll error: {exc}"); last_err = now
                    time.sleep(_CLIENT_POLL_S)
            threading.Thread(target=_poll_for_captures, daemon=True, name="SyncCapPoll").start()

    if not is_hub:
        return

    # Start single scheduler thread (Item 24)
    threading.Thread(target=_sched_loop, daemon=True, name="SyncCapSched").start()

    # ── Hub routes ────────────────────────────────────────────────────────────

    @app.get("/hub/synccap")
    @login_required
    def synccap_page():
        return _render_page(BUILD)

    @app.get("/api/synccap/inputs")
    @login_required
    def synccap_inputs():
        from flask import jsonify
        result = []
        for inp in cfg_ss.inputs:
            result.append({"site": "(hub)", "stream": inp.name,
                           "type": getattr(inp, "device_index", "") or ""})
        for site, sdata in hub_server._sites.items():
            if not sdata.get("_approved"):
                continue
            for s in sdata.get("streams", []):
                result.append({"site": site, "stream": s.get("name", ""),
                               "type": s.get("device_index", "") or ""})
        return jsonify(result)

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
        capture_at   = time.time() + 15
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

        # Dispatch per site
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
            hub_server.push_pending_command(site, {"type": "synccap_capture", "payload": cmd_payload})
            # Bug 5: append to queue rather than overwrite
            with _pending_lock:
                if site not in _pending_cmds:
                    _pending_cmds[site] = []
                _pending_cmds[site].append((cmd_payload, time.time() + _EXPIRE_S))

        # Item 24: schedule hub capture + expiry via single scheduler
        if hub_streams:
            _schedule(capture_at, "hub_cap", capture_id, tuple(hub_streams), duration)
        _schedule(capture_at + _EXPIRE_S, "expire", capture_id)

        return jsonify({"capture_id": capture_id, "capture_at": capture_at})

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
            queue = _pending_cmds.get(site) or []
            now   = time.time()
            queue = [(c, e) for c, e in queue if e > now]
            if queue:
                cmd, _ = queue.pop(0)
                _pending_cmds[site] = queue
                return jsonify({"cmd": cmd})
            _pending_cmds[site] = []
        return jsonify({})

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

        # Bug 2: reject oversized uploads before reading body
        cl_hdr = request.content_length
        if cl_hdr and cl_hdr > _MAX_UPLOAD_BYTES:
            return jsonify({"error": "Clip too large"}), 413

        data = request.get_data()
        if len(data) > _MAX_UPLOAD_BYTES:
            return jsonify({"error": "Clip too large"}), 413
        if not data:
            return jsonify({"error": "No audio data"}), 400

        with _db_lock:
            db = _load_db()
            if capture_id not in db:
                return jsonify({"error": "Unknown capture"}), 404
            cap = db[capture_id]

            # Bug 6: validate site+stream is in this capture's selections
            sel_pairs = {(s["site"], s["stream"]) for s in cap.get("selections", [])}
            if (site, stream_name) not in sel_pairs:
                return jsonify({"error": "Stream not in selections for this capture"}), 400

            fname = f"{capture_id}_{_safe(site)}_{_safe(stream_name)}.{ext}"
            path  = os.path.join(_CLIP_DIR, fname)
            with open(path, "wb") as f:
                f.write(data)

            # Per-clip analysis
            lufs, tp = None, None
            if _HAS_NP and ext == "wav":
                try:
                    with wave.open(path, "rb") as wf:
                        raw_a = wf.readframes(wf.getnframes())
                        wfnch = wf.getnchannels()
                    pcm_a = np.frombuffer(raw_a, dtype=np.int16).astype(np.float32) / 32767.0
                    if wfnch == 2:
                        pcm_a = pcm_a.reshape(-1, 2).mean(axis=1)
                    lufs, tp = _analyse_lufs(pcm_a)
                except Exception:
                    pass

            meta = _snap_metadata(site, stream_name)
            clip_rec = {
                "site": site, "stream": stream_name,
                "filename": fname, "n_ch": n_ch,
                "received_at": time.time(),
            }
            if meta:
                clip_rec["metadata"] = meta
            if lufs is not None:
                clip_rec["lufs"] = lufs
            if tp is not None:
                clip_rec["tp_dbfs"] = tp

            cap["clips"].append(clip_rec)
            _update_status(cap)
            _save_db(db)

        monitor.log(f"[SyncCap] Received clip from {site}/{stream_name} → {fname}")
        return jsonify({"ok": True})

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

    @app.get("/api/synccap/captures")
    @login_required
    def synccap_list():
        """Item 21: paginated + searchable capture list."""
        from flask import request, jsonify
        q      = request.args.get("q", "").strip().lower()
        offset = max(0, int(request.args.get("offset", 0)))
        limit  = min(60, max(1, int(request.args.get("limit", 20))))
        with _db_lock:
            db = _load_db()
        caps = sorted(db.values(), key=lambda c: c["triggered_at"], reverse=True)
        if q:
            caps = [c for c in caps if q in c.get("label", "").lower()
                    or q in c.get("capture_id", "").lower()]
        total = len(caps)
        page  = caps[offset: offset + limit]
        return jsonify({"total": total, "offset": offset, "limit": limit, "captures": page})

    @app.get("/api/synccap/clip/<capture_id>/<path:filename>")
    @login_required
    def synccap_serve_clip(capture_id, filename):
        """Bug 3: streaming Range-aware serve (no full read into memory)."""
        from flask import request, Response
        filename = os.path.basename(filename)
        if not filename.startswith(capture_id):
            return "Forbidden", 403
        path = os.path.join(_CLIP_DIR, filename)
        if not os.path.exists(path):
            return "Not found", 404
        ext       = filename.rsplit(".", 1)[-1].lower()
        mime      = "audio/mpeg" if ext == "mp3" else "audio/wav"
        file_size = os.path.getsize(path)
        range_hdr = request.headers.get("Range", "")
        if range_hdr:
            try:
                parts = range_hdr.replace("bytes=", "").split("-")
                sta   = int(parts[0]) if parts[0] else 0
                end   = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1
                end   = min(end, file_size - 1)
                length = end - sta + 1

                def _gen_range():
                    with open(path, "rb") as fh:
                        fh.seek(sta)
                        rem = length
                        while rem > 0:
                            chunk = fh.read(min(65536, rem))
                            if not chunk:
                                break
                            rem -= len(chunk)
                            yield chunk

                resp = Response(_gen_range(), status=206, mimetype=mime)
                resp.headers["Content-Range"]  = f"bytes {sta}-{end}/{file_size}"
                resp.headers["Accept-Ranges"]  = "bytes"
                resp.headers["Content-Length"] = str(length)
                return resp
            except Exception:
                pass

        def _gen_full():
            with open(path, "rb") as fh:
                while True:
                    chunk = fh.read(65536)
                    if not chunk:
                        break
                    yield chunk

        resp = Response(_gen_full(), status=200, mimetype=mime)
        resp.headers["Accept-Ranges"]  = "bytes"
        resp.headers["Content-Length"] = str(file_size)
        return resp

    @app.get("/api/synccap/clip/<capture_id>/<path:filename>/bwf")
    @login_required
    def synccap_serve_bwf(capture_id, filename):
        """Item 17: serve clip as BWF WAV with bext timecode chunk."""
        from flask import jsonify
        filename = os.path.basename(filename)
        if not filename.startswith(capture_id):
            return "Forbidden", 403
        path = os.path.join(_CLIP_DIR, filename)
        if not os.path.exists(path):
            return "Not found", 404
        if not filename.endswith(".wav"):
            return "BWF only supported for WAV clips", 400
        with open(path, "rb") as fh:
            wav_bytes = fh.read()
        # Find clip metadata for stream/site/timestamp
        with _db_lock:
            db  = _load_db()
            cap = db.get(capture_id, {})
        cl = next((c for c in cap.get("clips", []) if c["filename"] == filename), {})
        stream_name = cl.get("stream", "")
        site        = cl.get("site", "")
        captured_at = cap.get("capture_at", time.time())
        lufs        = cl.get("lufs")
        bwf = _build_bwf(wav_bytes, stream_name, site, captured_at, lufs)
        bwf_name = filename.replace(".wav", ".bwf.wav")
        from flask import Response
        resp = Response(bwf, status=200, mimetype="audio/wav")
        resp.headers["Content-Disposition"] = f'attachment; filename="{bwf_name}"'
        resp.headers["Content-Length"]      = str(len(bwf))
        return resp

    @app.post("/api/synccap/align_async/<capture_id>")
    @login_required
    @csrf_protect
    def synccap_align_async(capture_id):
        """Item 23: start async alignment job. Returns {job_id}."""
        from flask import request, jsonify
        if not _HAS_NP:
            return jsonify({"error": "numpy unavailable"}), 503
        body   = request.get_json(silent=True) or {}
        ref_fn = body.get("ref", "").strip() or None

        with _db_lock:
            db  = _load_db()
            cap = db.get(capture_id)
        if not cap:
            return jsonify({"error": "Not found"}), 404

        job_id = uuid.uuid4().hex[:16]

        def _run_job():
            try:
                result = _compute_alignment(cap, ref_fn)
                with _align_lock:
                    _align_jobs[job_id] = {"status": "done", "result": result, "ts": time.time()}
                    _align_seq_list.append(job_id)
                    while len(_align_seq_list) > _MAX_ALIGN_JOBS:
                        old = _align_seq_list.pop(0)
                        _align_jobs.pop(old, None)
            except Exception as exc:
                with _align_lock:
                    _align_jobs[job_id] = {"status": "error", "error": str(exc), "ts": time.time()}
                    _align_seq_list.append(job_id)

        with _align_lock:
            _align_jobs[job_id] = {"status": "pending", "ts": time.time()}
        threading.Thread(target=_run_job, daemon=True, name=f"SyncCapAlign-{job_id[:6]}").start()
        return jsonify({"job_id": job_id}), 202

    @app.get("/api/synccap/align_result/<job_id>")
    @login_required
    def synccap_align_result(job_id):
        """Item 23: poll async alignment result."""
        from flask import jsonify
        with _align_lock:
            job = _align_jobs.get(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        return jsonify(job)

    @app.get("/api/synccap/align/<capture_id>")
    @login_required
    def synccap_align(capture_id):
        """Synchronous alignment — kept for backward-compatibility."""
        from flask import request, jsonify
        if not _HAS_NP:
            return jsonify({"error": "numpy unavailable"}), 503
        with _db_lock:
            db  = _load_db()
            cap = db.get(capture_id)
        if not cap:
            return jsonify({"error": "Not found"}), 404
        ref_fn = request.args.get("ref", "").strip() or None
        try:
            return jsonify(_compute_alignment(cap, ref_fn))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

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

    @app.get("/api/synccap/disk")
    @login_required
    def synccap_disk():
        """Item 27: disk usage for clip directory."""
        from flask import jsonify
        try:
            total_b, _, free_b = shutil.disk_usage(_CLIP_DIR)
            clip_b = sum(
                os.path.getsize(os.path.join(_CLIP_DIR, f))
                for f in os.listdir(_CLIP_DIR)
                if os.path.isfile(os.path.join(_CLIP_DIR, f))
            )
            return jsonify({
                "clip_dir":    _CLIP_DIR,
                "clip_bytes":  clip_b,
                "free_bytes":  free_b,
                "total_bytes": total_b,
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.get("/api/synccap/config")
    @login_required
    def synccap_get_config():
        from flask import jsonify
        pcfg = _load_plugin_cfg()
        return jsonify({"clip_dir": pcfg.get("clip_dir", ""), "effective_dir": _CLIP_DIR})

    @app.post("/api/synccap/config")
    @login_required
    @csrf_protect
    def synccap_save_config():
        from flask import request, jsonify
        global _CLIP_DIR
        body     = request.get_json(silent=True) or {}
        clip_dir = str(body.get("clip_dir", "")).strip()
        pcfg     = _load_plugin_cfg()
        if clip_dir:
            os.makedirs(clip_dir, exist_ok=True)
            pcfg["clip_dir"] = clip_dir
        else:
            pcfg.pop("clip_dir", None)
        _save_plugin_cfg(pcfg)
        _CLIP_DIR = clip_dir if clip_dir else os.path.join(
            os.path.dirname(_CFG_PATH), "synccap_clips"
        )
        os.makedirs(_CLIP_DIR, exist_ok=True)
        return jsonify({"ok": True, "clip_dir": _CLIP_DIR})


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
.dur-wrap{display:flex;align-items:center;gap:8px}
.dur-wrap input[type=range]{flex:1;accent-color:var(--acc)}
.dur-val{font-size:14px;color:var(--acc);font-weight:700;width:48px;text-align:right;flex-shrink:0}
.prog-row{display:flex;align-items:center;gap:10px;padding:7px 0;border-bottom:1px solid rgba(23,52,95,.4)}
.prog-row:last-child{border:none}
.prog-st{width:70px;flex-shrink:0;font-size:11px;font-weight:600}
.st-wait{color:var(--mu)}.st-ok{color:var(--ok)}.st-exp{color:var(--wn)}
.prog-site{font-size:11px;color:var(--mu);width:90px;flex-shrink:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.prog-stream{flex:1;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.countdown{font-size:11px;color:var(--acc);font-weight:600;margin-bottom:8px}
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
.clips-panel{background:#050d1e;border-top:1px solid var(--bor)}
.clips-grid{padding:14px;display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
.clip-card{background:var(--sur);border:1px solid var(--bor);border-radius:8px;padding:12px}
.clip-site{font-size:10px;color:var(--mu);margin-bottom:2px}
.clip-stream{font-size:13px;font-weight:600;margin-bottom:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.clip-meta{font-size:10px;color:var(--mu);margin-bottom:6px;display:flex;flex-wrap:wrap;gap:5px}
.clip-meta span{background:#0d1e40;border:1px solid var(--bor);border-radius:3px;padding:0 4px}
.clip-audio-row{display:flex;align-items:center;gap:5px}
audio{flex:1;height:30px}
.no-clips{color:var(--mu);font-size:12px;padding:10px}
#msg{display:none;padding:8px 12px;border-radius:6px;margin-bottom:12px;font-size:12px}
.msg-ok{background:#0f2318;color:var(--ok);border:1px solid #166534}
.msg-err{background:#2a0a0a;color:var(--al);border:1px solid #991b1b}
.shimmer{position:relative;overflow:hidden}
.shimmer::after{content:'';position:absolute;inset:0;background:linear-gradient(90deg,transparent 0%,rgba(255,255,255,.08) 50%,transparent 100%);background-size:200% 100%;animation:shim 1.2s infinite linear}
@keyframes shim{0%{background-position:200% 0}100%{background-position:-200% 0}}
.clips-panel-bar{display:flex;align-items:center;gap:8px;padding:8px 14px;border-bottom:1px solid var(--bor);flex-wrap:wrap}
.clips-panel-bar-lbl{font-size:11px;color:var(--mu);font-weight:700;text-transform:uppercase;letter-spacing:.04em}
.align-panel{padding:12px 14px;border-top:1px solid var(--bor);background:#030b18}
.align-hdr{display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap}
.align-track{display:grid;grid-template-columns:160px auto auto auto 1fr auto;align-items:start;gap:6px;padding:8px 0;border-bottom:1px solid rgba(23,52,95,.35)}
.align-track:last-child{border:none}
.align-track-info{min-width:0}
.align-track audio{height:28px;width:100%}
.offset-pill{background:rgba(23,168,255,.15);color:var(--acc);border:1px solid rgba(23,168,255,.3);border-radius:4px;padding:1px 7px;font-size:11px;font-weight:600;white-space:nowrap;flex-shrink:0}
.offset-pill.zero{background:rgba(34,197,94,.12);color:var(--ok);border-color:rgba(34,197,94,.3)}
.overlap-badge{background:rgba(245,158,11,.12);color:var(--wn);border:1px solid rgba(245,158,11,.3);border-radius:4px;padding:1px 7px;font-size:11px;font-weight:600}
.score-pill{border-radius:4px;padding:1px 7px;font-size:11px;font-weight:700;white-space:nowrap;flex-shrink:0}
.score-ex{background:rgba(34,197,94,.15);color:#4ade80;border:1px solid rgba(34,197,94,.3)}
.score-gd{background:rgba(23,168,255,.12);color:var(--acc);border:1px solid rgba(23,168,255,.25)}
.score-fr{background:rgba(245,158,11,.12);color:var(--wn);border:1px solid rgba(245,158,11,.3)}
.score-pr{background:rgba(239,68,68,.12);color:var(--al);border:1px solid rgba(239,68,68,.3)}
.score-ref{background:rgba(138,164,200,.1);color:var(--mu);border:1px solid rgba(138,164,200,.2)}
.lvl-pill{background:rgba(138,164,200,.1);color:var(--mu);border:1px solid rgba(138,164,200,.2);border-radius:4px;padding:1px 6px;font-size:10px;font-weight:600;white-space:nowrap;flex-shrink:0}
.wf-canvas{width:100%;height:52px;display:block;border-radius:4px;cursor:crosshair}
.compare-panel{margin-top:12px;padding-top:12px;border-top:1px solid rgba(23,52,95,.5)}
.compare-panel label{font-size:10px;color:var(--mu);font-weight:700;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;display:block}
.compare-canvas{width:100%;height:80px;display:block;border-radius:4px;background:#020810}
.compare-legend{display:flex;flex-wrap:wrap;gap:10px;margin-top:5px}
.compare-legend-item{display:flex;align-items:center;gap:5px;font-size:10px;color:var(--mu)}
.compare-legend-swatch{width:12px;height:3px;border-radius:2px;flex-shrink:0}
.spectrum-panel{margin-top:10px;padding-top:10px;border-top:1px solid rgba(23,52,95,.5)}
.spec-canvas{width:100%;height:70px;display:block;border-radius:4px;background:#020810}
.ref-sel-row{display:flex;align-items:center;gap:6px;margin-bottom:10px;flex-wrap:wrap}
.ref-sel-row select{background:#0d1e40;border:1px solid var(--bor);border-radius:6px;color:var(--tx);padding:4px 8px;font-size:12px;font-family:inherit}
.stereo-info{font-size:10px;color:var(--mu);background:#0a1930;border:1px solid var(--bor);border-radius:4px;padding:3px 7px;margin-top:4px}
.pager{display:flex;align-items:center;gap:8px;margin-top:8px}
.search-row{display:flex;gap:6px;margin-bottom:10px}
.search-row input{flex:1;padding:5px 9px}
</style>
</head>
<body>
<header>
  <a href="/hub">← Hub</a>
  <h1>🎙 Sync Capture</h1>
  <span id="disk-badge" style="font-size:11px;color:var(--mu);margin-left:auto"></span>
  <span style="font-size:11px;color:var(--mu)">{{BUILD}}</span>
</header>

<div class="wrap">

  <!-- ── Left column ── -->
  <div>
    <div class="card">
      <div class="ch">Select Inputs <span id="sel-count" style="margin-left:auto;font-weight:400">0 selected</span></div>
      <div class="cb">
        <div id="msg"></div>
        <div style="display:flex;gap:6px;margin-bottom:8px">
          <button class="btn bg bs" id="btn-all">All</button>
          <button class="btn bg bs" id="btn-none">Clear</button>
          <button class="btn bg bs" id="btn-reload" style="margin-left:auto">↻</button>
        </div>
        <div class="stream-list" id="stream-list"><div style="color:var(--mu);padding:8px 0;font-size:12px">Loading…</div></div>
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

    <div class="card">
      <div class="ch">Storage</div>
      <div class="cb">
        <div class="field">
          <label>Clip Directory</label>
          <input type="text" id="clip-dir-input" spellcheck="false" autocomplete="off" placeholder="Default: plugins/synccap_clips">
        </div>
        <div id="disk-info" style="font-size:11px;color:var(--mu);margin-bottom:10px"></div>
        <button class="btn bg bs" id="save-storage-btn">Save Path</button>
      </div>
    </div>
  </div>

  <!-- ── Right column ── -->
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
      <div class="cb" style="padding-bottom:8px">
        <div class="search-row">
          <input type="text" id="search-input" placeholder="Search label…" spellcheck="false" autocomplete="off">
          <button class="btn bg bs" id="search-btn">Search</button>
          <button class="btn bg bs" id="search-clear">×</button>
        </div>
      </div>
      <div style="overflow-x:auto">
        <table>
          <thead><tr>
            <th>Time</th><th>Label</th><th style="white-space:nowrap">Dur</th>
            <th>Clips</th><th>Status</th><th></th>
          </tr></thead>
          <tbody id="cap-tbody"><tr><td colspan="6" style="color:var(--mu);text-align:center;padding:20px">No captures yet</td></tr></tbody>
        </table>
      </div>
      <div class="cb" style="padding-top:8px">
        <div class="pager" id="pager" style="display:none">
          <button class="btn bg bs" id="pg-prev">‹ Prev</button>
          <span id="pg-info" style="font-size:11px;color:var(--mu)"></span>
          <button class="btn bg bs" id="pg-next">Next ›</button>
        </div>
      </div>
    </div>
  </div>

</div>

<script nonce="{{csp_nonce()}}">
(function(){
'use strict';

var _inputs   = [];
var _selected = new Set();
var _pollTimer = null;
var _activeId  = null;
var _capCache  = new Map();   // Item 25: capture_id → cap object
var _pgOffset  = 0;
var _pgLimit   = 20;
var _pgTotal   = 0;
var _pgQuery   = '';

// ── csrf / helpers ────────────────────────────────────────────────────────────
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
function esc(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function fmtBytes(b){
  if(b<1024) return b+'B';
  if(b<1048576) return (b/1024).toFixed(1)+'KB';
  if(b<1073741824) return (b/1048576).toFixed(1)+'MB';
  return (b/1073741824).toFixed(2)+'GB';
}

// ── duration slider ───────────────────────────────────────────────────────────
document.getElementById('cap-dur').addEventListener('input', function(){
  var v=this.value;
  document.getElementById('dur-val').textContent=v+'s';
  document.getElementById('dur-display').textContent=v+' s';
});

// ── label pre-fill (Item 22) ──────────────────────────────────────────────────
(function(){
  var now=new Date();
  var h=now.getHours().toString().padStart(2,'0');
  var m=now.getMinutes().toString().padStart(2,'0');
  document.getElementById('cap-label').value=h+':'+m+' Capture';
})();

// ── inputs ────────────────────────────────────────────────────────────────────
function loadInputs(){
  document.getElementById('stream-list').innerHTML='<div style="color:var(--mu);padding:8px 0;font-size:12px">Loading…</div>';
  fetch('/api/synccap/inputs',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){_inputs=d; renderInputs();})
    .catch(function(){
      document.getElementById('stream-list').innerHTML='<div style="color:var(--al);padding:8px 0;font-size:12px">Failed to load inputs</div>';
    });
}
function renderInputs(){
  var list=document.getElementById('stream-list');
  if(!_inputs.length){list.innerHTML='<div style="color:var(--mu);padding:8px 0;font-size:12px">No inputs found</div>';updateCount();return;}
  var bySite={};
  _inputs.forEach(function(inp){(bySite[inp.site]=bySite[inp.site]||[]).push(inp);});
  var html='';
  Object.keys(bySite).sort(function(a,b){if(a==='(hub)')return -1;if(b==='(hub)')return 1;return a.localeCompare(b);})
  .forEach(function(site){
    html+='<div class="site-group"><div class="site-hdr">'+esc(site)+'</div>';
    bySite[site].forEach(function(inp){
      var key=inp.site+'|||'+inp.stream;
      var chk=_selected.has(key);
      var tl=_typeLabel(inp.type);
      html+='<div class="stream-item'+(chk?' sel':'')+'" data-key="'+esc(key)+'">'
        +'<input type="checkbox"'+(chk?' checked':'')+' aria-label="'+esc(inp.stream)+'">'
        +'<span class="stream-name">'+esc(inp.stream)+'</span>'
        +(tl?'<span class="type-badge">'+esc(tl)+'</span>':'')+'</div>';
    });
    html+='</div>';
  });
  list.innerHTML=html;
  list.querySelectorAll('.stream-item').forEach(function(div){
    div.addEventListener('click',function(e){
      var key=div.dataset.key;
      if(e.target.tagName==='INPUT'){
        if(e.target.checked)_selected.add(key); else _selected.delete(key);
        div.classList.toggle('sel',_selected.has(key));
      } else {
        var cb=div.querySelector('input[type=checkbox]');
        if(_selected.has(key)){_selected.delete(key);cb.checked=false;}
        else{_selected.add(key);cb.checked=true;}
        div.classList.toggle('sel',_selected.has(key));
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
function updateCount(){ document.getElementById('sel-count').textContent=_selected.size+' selected'; }
document.getElementById('btn-all').addEventListener('click',function(){_inputs.forEach(function(i){_selected.add(i.site+'|||'+i.stream);});renderInputs();});
document.getElementById('btn-none').addEventListener('click',function(){_selected.clear();renderInputs();});
document.getElementById('btn-reload').addEventListener('click',loadInputs);

// ── trigger ───────────────────────────────────────────────────────────────────
document.getElementById('cap-btn').addEventListener('click',triggerCapture);
function triggerCapture(){
  if(!_selected.size){showMsg('Select at least one input first',false);return;}
  var selections=[];
  _selected.forEach(function(key){var p=key.split('|||');selections.push({site:p[0],stream:p[1]});});
  var label=document.getElementById('cap-label').value.trim()||'Capture';
  var dur=parseInt(document.getElementById('cap-dur').value,10);
  var btn=document.getElementById('cap-btn');
  btn.disabled=true; btn.classList.add('shimmer'); btn.textContent='Triggering…';
  fetch('/api/synccap/trigger',{
    method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRFToken':_csrf()},
    body:JSON.stringify({label:label,duration_s:dur,selections:selections})
  })
  .then(function(r){return r.json();})
  .then(function(d){
    btn.disabled=false; btn.classList.remove('shimmer'); btn.textContent='⏺ Capture';
    if(d.error){showMsg(d.error,false);return;}
    showMsg('Capture triggered — waiting for clips…',true);
    _activeId=d.capture_id;
    startProgress(d.capture_id, selections, d.capture_at);
  })
  .catch(function(){
    btn.disabled=false; btn.classList.remove('shimmer'); btn.textContent='⏺ Capture';
    showMsg('Request failed — check connection',false);
  });
}

// ── progress panel (Item 18: countdown) ──────────────────────────────────────
function startProgress(capId, selections, captureAt){
  var card=document.getElementById('prog-card');
  var title=document.getElementById('prog-title');
  var body=document.getElementById('prog-body');
  card.style.display='';
  title.textContent='Collecting clips…';
  var cdEl='<div class="countdown" id="countdown_'+capId+'">Preparing…</div>';
  body.innerHTML=cdEl+selections.map(function(s){
    return '<div class="prog-row" id="pr_'+_rowKey(s.site,s.stream)+'">'
      +'<span class="prog-st st-wait">Waiting</span>'
      +'<span class="prog-site">'+esc(s.site)+'</span>'
      +'<span class="prog-stream">'+esc(s.stream)+'</span>'
      +'</div>';
  }).join('');
  // Countdown ticker
  var cdTick=setInterval(function(){
    var el=document.getElementById('countdown_'+capId);
    if(!el){clearInterval(cdTick);return;}
    var rem=(captureAt*1000)-Date.now();
    if(rem>0) el.textContent='Capturing in '+(rem/1000).toFixed(1)+' s…';
    else{el.textContent='Capturing…';clearInterval(cdTick);}
  },100);
  if(_pollTimer) clearInterval(_pollTimer);
  _pollTimer=setInterval(function(){pollProgress(capId,selections);},2000);
}
function _rowKey(site,stream){ return (site+'__'+stream).replace(/[^a-zA-Z0-9]/g,'_'); }
function pollProgress(capId,selections){
  fetch('/api/synccap/status/'+capId,{credentials:'same-origin'})
  .then(function(r){return r.json();})
  .then(function(cap){
    var got={};
    (cap.clips||[]).forEach(function(cl){got[cl.site+'|||'+cl.stream]=true;});
    selections.forEach(function(s){
      var key=s.site+'|||'+s.stream;
      var row=document.getElementById('pr_'+_rowKey(s.site,s.stream));
      if(!row) return;
      var st=row.querySelector('.prog-st');
      if(got[key]){st.textContent='✓ Ready';st.className='prog-st st-ok';}
      else if(cap.status==='expired'){st.textContent='Expired';st.className='prog-st st-exp';}
    });
    if(cap.status==='complete'||cap.status==='expired'){
      clearInterval(_pollTimer);_pollTimer=null;
      document.getElementById('prog-title').textContent=
        cap.status==='complete'?'Capture complete ✓':'Capture expired — partial results';
      loadCaptures(capId);
    }
  }).catch(function(){});
}

// ── history / pagination (Item 21) ───────────────────────────────────────────
document.getElementById('btn-refresh').addEventListener('click',function(){loadCaptures();});
document.getElementById('search-btn').addEventListener('click',function(){
  _pgQuery=document.getElementById('search-input').value.trim();
  _pgOffset=0; loadCaptures();
});
document.getElementById('search-clear').addEventListener('click',function(){
  _pgQuery=''; document.getElementById('search-input').value=''; _pgOffset=0; loadCaptures();
});
document.getElementById('search-input').addEventListener('keydown',function(e){
  if(e.key==='Enter'){_pgQuery=this.value.trim();_pgOffset=0;loadCaptures();}
});
document.getElementById('pg-prev').addEventListener('click',function(){
  if(_pgOffset>0){_pgOffset=Math.max(0,_pgOffset-_pgLimit);loadCaptures();}
});
document.getElementById('pg-next').addEventListener('click',function(){
  if(_pgOffset+_pgLimit<_pgTotal){_pgOffset+=_pgLimit;loadCaptures();}
});

function loadCaptures(autoExpandId){
  var url='/api/synccap/captures?offset='+_pgOffset+'&limit='+_pgLimit+((_pgQuery)?'&q='+encodeURIComponent(_pgQuery):'');
  fetch(url,{credentials:'same-origin'})
  .then(function(r){return r.json();})
  .then(function(d){
    _pgTotal=d.total||0;
    var caps=d.captures||[];
    caps.forEach(function(c){_capCache.set(c.capture_id,c);});
    renderHistory(caps);
    var pager=document.getElementById('pager');
    var pgInfo=document.getElementById('pg-info');
    if(_pgTotal>_pgLimit){
      pager.style.display='';
      var from=_pgOffset+1, to=Math.min(_pgOffset+_pgLimit,_pgTotal);
      pgInfo.textContent=from+'–'+to+' of '+_pgTotal;
      document.getElementById('pg-prev').disabled=_pgOffset===0;
      document.getElementById('pg-next').disabled=_pgOffset+_pgLimit>=_pgTotal;
    } else {
      pager.style.display='none';
    }
    if(autoExpandId){
      var panel=document.getElementById('clips_'+autoExpandId);
      if(panel) panel.style.display='';
    }
  }).catch(function(){});
}

// ── history render ─────────────────────────────────────────────────────────────
function renderHistory(caps){
  var tbody=document.getElementById('cap-tbody');
  if(!caps.length){
    tbody.innerHTML='<tr><td colspan="6" style="color:var(--mu);text-align:center;padding:20px">'
      +(_pgQuery?'No captures match "'+esc(_pgQuery)+'"':'No captures yet')+'</td></tr>';
    return;
  }
  var BADGE={complete:'<span class="badge b-ok">Complete</span>',partial:'<span class="badge b-wn">Partial</span>',waiting:'<span class="badge b-mu">Waiting</span>',expired:'<span class="badge b-al">Expired</span>'};
  tbody.innerHTML=caps.map(function(cap){
    var d=new Date(cap.triggered_at*1000);
    var ds=d.toLocaleDateString([],{month:'short',day:'numeric'});
    var ts=d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',second:'2-digit'});
    var nSel=cap.selections.length, nCl=cap.clips.length;
    var badge=BADGE[cap.status]||cap.status;
    // Item 25: no data-cap — use _capCache
    return '<tr class="cap-row" data-capid="'+cap.capture_id+'">'
      +'<td style="white-space:nowrap"><span style="color:var(--mu);font-size:11px">'+ds+'</span><br>'+ts+'</td>'
      +'<td>'+esc(cap.label)+'</td>'
      +'<td style="white-space:nowrap">'+cap.duration_s+' s</td>'
      +'<td>'+nCl+' / '+nSel+'</td>'
      +'<td>'+badge+'</td>'
      +'<td><button class="btn bd bs" data-del="'+cap.capture_id+'">✕</button></td>'
      +'</tr>'
      +'<tr id="clips_'+cap.capture_id+'" style="display:none">'
      +'<td colspan="6" class="clips-panel">'
      +'<div class="clips-panel-bar">'
      +'<span class="clips-panel-bar-lbl">Clips</span>'
      +(cap.clips.length>=2?'<button class="btn bg bs align-btn" data-capid="'+cap.capture_id+'" style="margin-left:auto">⇌ Align</button>':'')
      +'</div>'
      +(cap.clips.length
        ?'<div class="clips-grid" id="cg_'+cap.capture_id+'">'+cap.clips.map(function(cl){
            var src='/api/synccap/clip/'+cap.capture_id+'/'+encodeURIComponent(cl.filename);
            var bwfSrc=src+'/bwf';
            var ch=cl.n_ch===2?'<span class="badge b-mu" style="font-size:9px;margin-left:4px">STEREO</span>':'';
            var metas='';
            if(cl.lufs!=null) metas+='<span>LUFS '+cl.lufs+'</span>';
            if(cl.tp_dbfs!=null) metas+='<span>TP '+cl.tp_dbfs+' dBTP</span>';
            if(cl.metadata&&cl.metadata.rds&&cl.metadata.rds.ps) metas+='<span>📻 '+esc(cl.metadata.rds.ps)+'</span>';
            if(cl.metadata&&cl.metadata.dls) metas+='<span>'+esc(cl.metadata.dls.substring(0,30))+'</span>';
            return '<div class="clip-card">'
              +'<div class="clip-site">'+esc(cl.site)+'</div>'
              +'<div class="clip-stream">'+esc(cl.stream)+ch+'</div>'
              +(metas?'<div class="clip-meta">'+metas+'</div>':'')
              +'<div class="clip-audio-row">'
              +'<audio controls preload="none" src="'+src+'"></audio>'
              +'<a class="btn bg bs" href="'+src+'" download="'+esc(cl.filename)+'" title="Download WAV">⬇</a>'
              +(cl.filename.endsWith('.wav')?'<a class="btn bg bs" href="'+bwfSrc+'" download="'+esc(cl.filename.replace(/\.wav$/,'.bwf.wav'))+'" title="Download BWF WAV">BWF</a>':'')
              +'</div>'
              +'</div>';
          }).join('')+'</div>'
        :'<div class="no-clips">No clips received for this capture.</div>'
      )
      +'<div id="ap_'+cap.capture_id+'"></div>'
      +'</td></tr>';
  }).join('');
}

// ── table delegation ──────────────────────────────────────────────────────────
document.getElementById('cap-tbody').addEventListener('click',function(e){
  var delBtn=e.target.closest('[data-del]');
  if(delBtn){e.stopPropagation();if(!window.confirm('Delete this capture and all its clips?'))return;deleteCapture(delBtn.dataset.del);return;}
  var alignBtn=e.target.closest('.align-btn');
  if(alignBtn){e.stopPropagation();alignCapture(alignBtn.dataset.capid,alignBtn,null);return;}
  var row=e.target.closest('.cap-row');
  if(!row)return;
  var capId=row.dataset.capid;
  var panel=document.getElementById('clips_'+capId);
  if(!panel)return;
  panel.style.display=panel.style.display==='none'?'':'none';
});
function deleteCapture(capId){
  fetch('/api/synccap/capture/'+capId,{method:'DELETE',credentials:'same-origin',headers:{'X-CSRFToken':_csrf()}})
  .then(function(r){return r.json();})
  .then(function(d){if(d.ok){_capCache.delete(capId);loadCaptures();}})
  .catch(function(){});
}

// ── alignment (async — Item 23) ───────────────────────────────────────────────
var _WF_COLORS=['#17a8ff','#22c55e','#f59e0b','#ef4444','#a78bfa','#fb923c','#34d399','#f472b6'];

function alignCapture(capId, btn, refFn){
  var ap=document.getElementById('ap_'+capId);
  // Toggle off if already shown and no new ref
  if(ap&&ap.dataset.alignDone==='1'&&!refFn){
    ap.innerHTML=''; ap.dataset.alignDone='';
    btn.textContent='⇌ Align'; return;
  }
  btn.disabled=true; btn.classList.add('shimmer'); btn.textContent='Analysing…';
  var body=refFn?JSON.stringify({ref:refFn}):'{}';
  fetch('/api/synccap/align_async/'+capId,{
    method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRFToken':_csrf()},
    body:body
  })
  .then(function(r){return r.json();})
  .then(function(d){
    if(d.error) throw new Error(d.error);
    _pollAlign(capId,d.job_id,btn);
  })
  .catch(function(err){
    btn.disabled=false; btn.classList.remove('shimmer'); btn.textContent='⇌ Align';
    showMsg('Alignment failed: '+err.message,false);
  });
}

function _pollAlign(capId, jobId, btn){
  fetch('/api/synccap/align_result/'+jobId,{credentials:'same-origin'})
  .then(function(r){return r.json();})
  .then(function(d){
    if(d.status==='pending'){setTimeout(function(){_pollAlign(capId,jobId,btn);},500);return;}
    if(d.status==='error') throw new Error(d.error||'Alignment failed');
    btn.disabled=false; btn.classList.remove('shimmer'); btn.textContent='⇌ Hide Alignment';
    var cap=_capCache.get(capId)||{};
    var ap=document.getElementById('ap_'+capId);
    if(ap) ap.dataset.alignDone='1';
    applyAlignment(capId,d.result,cap.clips||[]);
  })
  .catch(function(err){
    btn.disabled=false; btn.classList.remove('shimmer'); btn.textContent='⇌ Align';
    showMsg('Alignment failed: '+err.message,false);
  });
}

// ── score helpers ─────────────────────────────────────────────────────────────
function _scoreCls(s,isRef){
  if(isRef) return 'score-pill score-ref';
  if(s===null||s===undefined) return 'score-pill score-pr';
  if(s>=0.80) return 'score-pill score-ex';
  if(s>=0.60) return 'score-pill score-gd';
  if(s>=0.40) return 'score-pill score-fr';
  return 'score-pill score-pr';
}
function _scoreLbl(s,isRef){
  if(isRef) return 'Reference';
  if(s===null||s===undefined) return 'N/A';
  var p=Math.round(s*100);
  if(s>=0.80) return p+'% ✓';
  if(s>=0.60) return p+'%';
  if(s>=0.40) return p+'% ⚠';
  return p+'% ✗';
}
function _scoreTitle(s,isRef){
  if(isRef) return 'Reference clip. Scores based on RMS loudness envelope — FM/DAB/unprocessed compare fairly.';
  if(s===null||s===undefined) return 'Insufficient overlap to score';
  var p=Math.round(s*100);
  if(s>=0.80) return p+'% — same content confirmed';
  if(s>=0.60) return p+'% — likely same content';
  if(s>=0.40) return p+'% — uncertain (heavy processing difference?)';
  return p+'% — content mismatch or one stream silent';
}

// ── waveform drawing ──────────────────────────────────────────────────────────
function _drawWF(canvas, pts, color, bg){
  var W=canvas.offsetWidth||600, H=canvas.height;
  canvas.width=W;
  var ctx=canvas.getContext('2d');
  ctx.clearRect(0,0,W,H);
  if(bg){ctx.fillStyle=bg;ctx.fillRect(0,0,W,H);}
  if(!pts||!pts.length) return;
  var max=Math.max.apply(null,pts)||1, cx=W/pts.length;
  ctx.fillStyle=color;
  for(var i=0;i<pts.length;i++){
    var h=(pts[i]/max)*(H/2-1), x=Math.round(i*cx), w=Math.max(1,Math.round(cx));
    ctx.fillRect(x,H/2-h,w,h*2);
  }
  ctx.fillStyle='rgba(255,255,255,0.07)'; ctx.fillRect(0,H/2-1,W,1);
}
function _drawCompare(canvas, allPts, colors){
  var W=canvas.offsetWidth||600, H=canvas.height;
  canvas.width=W;
  var ctx=canvas.getContext('2d');
  ctx.fillStyle='#020810'; ctx.fillRect(0,0,W,H);
  var gmax=0;
  allPts.forEach(function(pts){if(pts){var m=Math.max.apply(null,pts);if(m>gmax)gmax=m;}});
  if(!gmax) gmax=1;
  allPts.forEach(function(pts,idx){
    if(!pts) return;
    var color=colors[idx%colors.length], cx=W/pts.length;
    ctx.strokeStyle=color; ctx.lineWidth=1.5; ctx.globalAlpha=0.85;
    ctx.beginPath();
    for(var i=0;i<pts.length;i++){
      var x=i*cx+cx/2, amp=(pts[i]/gmax)*(H/2-2);
      if(i===0) ctx.moveTo(x,H/2-amp); else ctx.lineTo(x,H/2-amp);
    }
    for(var i=pts.length-1;i>=0;i--){
      var x=i*cx+cx/2, amp=(pts[i]/gmax)*(H/2-2);
      ctx.lineTo(x,H/2+amp);
    }
    ctx.closePath(); ctx.globalAlpha=0.25; ctx.fillStyle=color; ctx.fill();
    ctx.globalAlpha=0.85; ctx.stroke();
  });
  ctx.globalAlpha=1; ctx.fillStyle='rgba(255,255,255,0.06)'; ctx.fillRect(0,H/2-1,W,1);
}
function _drawSpectrum(canvas, allBands, colors){
  var W=canvas.offsetWidth||600, H=canvas.height;
  canvas.width=W;
  var ctx=canvas.getContext('2d');
  ctx.fillStyle='#020810'; ctx.fillRect(0,0,W,H);
  var freqs=['63','125','250','500','1000','2000','4000','8000','16000'];
  var n=allBands.length, nF=freqs.length;
  var minDb=Infinity, maxDb=-Infinity;
  allBands.forEach(function(b){if(!b)return;freqs.forEach(function(f){var v=b[f];if(v!=null){if(v<minDb)minDb=v;if(v>maxDb)maxDb=v;}});});
  if(!isFinite(minDb)){ctx.fillStyle='#8aa4c8';ctx.font='10px system-ui';ctx.textAlign='center';ctx.fillText('No spectrum data',W/2,H/2);return;}
  var dbRange=maxDb-minDb+1, gW=W/nF;
  var barW=Math.max(2,(gW-4)/Math.max(1,n));
  allBands.forEach(function(b,ci){
    if(!b)return;
    freqs.forEach(function(f,fi){
      var v=b[f]; if(v==null)return;
      var barH=Math.max(1,((v-minDb)/dbRange)*(H-16));
      var x=fi*gW+ci*barW+2, y=H-barH-14;
      ctx.fillStyle=colors[ci%colors.length]; ctx.globalAlpha=0.8;
      ctx.fillRect(x,y,barW-1,barH);
    });
  });
  ctx.globalAlpha=1;
  ctx.fillStyle='#8aa4c8'; ctx.font='9px system-ui'; ctx.textAlign='center';
  freqs.forEach(function(f,fi){
    var lbl=parseInt(f)>=1000?(parseInt(f)/1000)+'k':f;
    ctx.fillText(lbl,fi*gW+gW/2,H-2);
  });
}

// ── applyAlignment ────────────────────────────────────────────────────────────
function applyAlignment(capId, data, clips){
  var ap=document.getElementById('ap_'+capId);
  if(!ap) return;
  var offsets=data.offsets||{}, durations=data.durations||{};
  var scores=data.scores||{}, levDiffs=data.level_diffs||{};
  var lufsD=data.lufs||{}, tpD=data.tp_dbfs||{};
  var bands=data.bands||{}, stereoD=data.stereo||{};
  var waveforms=data.waveforms||{}, cmpWaves=data.compare_waveforms||{};
  var overlapS=data.overlap_s||0, refFn=data.ref_filename||'';

  var tracks=clips.filter(function(cl){return offsets[cl.filename]!==undefined;});
  if(!tracks.length){ap.innerHTML='<div class="no-clips" style="margin:10px">Could not correlate clips.</div>';return;}

  // Item 20: Reference selector
  var refSelHtml='<div class="ref-sel-row"><label style="font-size:11px;color:var(--mu);font-weight:600;text-transform:uppercase;letter-spacing:.05em">Reference:</label>'
    +'<select id="ref-sel-'+capId+'">'
    +tracks.map(function(cl,i){return '<option value="'+esc(cl.filename)+'"'+(cl.filename===refFn?' selected':'')+'>'+esc(cl.stream)+'</option>';}).join('')
    +'</select>'
    +'<button class="btn bg bs" id="realign-btn-'+capId+'">↻ Re-align</button>'
    +'</div>';

  var scoreNote='<span style="font-size:10px;color:var(--mu)" title="Score is RMS loudness envelope — FM/DAB/unprocessed compare fairly">Scores: loudness envelope match</span>';

  var html='<div class="align-panel">';
  html+=refSelHtml;
  html+='<div class="align-hdr">';
  html+='<button class="btn bp bs" id="play-all-'+capId+'">▶ Play All Aligned</button>';
  html+='<button class="btn bg bs" id="stop-all-'+capId+'">■ Stop</button>';
  html+='<button class="btn bg bs" id="cmp-btn-'+capId+'">📊 Compare</button>';
  html+='<button class="btn bg bs" id="spec-btn-'+capId+'">📈 Spectrum</button>';
  html+='<span class="overlap-badge" title="Aligned overlap duration">⧖ '+overlapS.toFixed(1)+' s</span>';
  html+=scoreNote;
  html+='</div>';

  tracks.forEach(function(cl,idx){
    var fn=cl.filename, off=offsets[fn]||0, dur=durations[fn]||0;
    var sc=scores[fn], isRef=(fn===refFn||idx===0);
    var ld=levDiffs[fn], lufs=lufsD[fn], tp=tpD[fn];
    var src='/api/synccap/clip/'+capId+'/'+encodeURIComponent(fn);
    var offLabel=off===0?'+0.0 s':(off>0?'+':'')+off.toFixed(3)+' s';
    var pillCls=off===0?'offset-pill zero':'offset-pill';
    var ch=cl.n_ch===2?'<span class="badge b-mu" style="font-size:9px;margin-left:4px">STEREO</span>':'';
    var color=_WF_COLORS[idx%_WF_COLORS.length];
    var ldStr=ld===null||ld===undefined?'':(ld>=0?'+':'')+ld+' dB';
    var lufsStr=lufs!=null?'LUFS '+lufs:'';
    var tpStr=tp!=null?'TP '+tp:'';
    var metaBadges=[ldStr,lufsStr,tpStr].filter(Boolean).map(function(s){return '<span class="lvl-pill">'+esc(s)+'</span>';}).join('');

    html+='<div class="align-track" data-fn="'+esc(fn)+'">';
    html+='<div class="align-track-info"><div class="clip-site" style="font-size:10px;color:var(--mu)">'+esc(cl.site)+'</div><div class="clip-stream" style="font-size:12px;font-weight:600">'+esc(cl.stream)+ch+'</div></div>';
    html+='<span class="'+pillCls+'" title="Skip offset to reach common programme point">'+offLabel+'</span>';
    html+='<span class="'+_scoreCls(sc,isRef)+'" title="'+_scoreTitle(sc,isRef)+'">'+_scoreLbl(sc,isRef)+'</span>';
    html+='<div style="display:flex;gap:4px;flex-wrap:wrap;align-items:center">'+metaBadges+'</div>';
    html+='<div style="display:flex;flex-direction:column;gap:4px;min-width:0">';
    html+='<audio class="align-audio" controls preload="metadata" src="'+src+'" data-offset="'+off+'" style="width:100%;height:28px"></audio>';
    html+='<canvas class="wf-canvas" data-fn="'+esc(fn)+'" height="40" style="background:#020810;"></canvas>';
    // Stereo info
    var si=stereoD[fn];
    if(si) html+='<div class="stereo-info">L: '+si.l_rms_dbfs+' dBFS  R: '+si.r_rms_dbfs+' dBFS  Bal: '+(si.balance_db>=0?'+':'')+si.balance_db+' dB  L/R corr: '+si.lr_corr+'</div>';
    html+='</div>';
    html+='<span style="color:var(--mu);font-size:11px;flex-shrink:0;white-space:nowrap">'+dur.toFixed(1)+' s</span>';
    html+='</div>';
  });

  // Compare + Spectrum panels (initially hidden)
  html+='<div id="cmp-panel-'+capId+'" class="compare-panel" style="display:none"><label>Aligned Waveform Comparison — overlap region</label><canvas id="cmp-canvas-'+capId+'" class="compare-canvas" height="80"></canvas><div class="compare-legend" id="cmp-legend-'+capId+'"></div></div>';
  html+='<div id="spec-panel-'+capId+'" class="spectrum-panel" style="display:none"><label>Octave Band Spectrum (middle 10 s)</label><canvas id="spec-canvas-'+capId+'" class="spec-canvas" height="70"></canvas><div class="compare-legend" id="spec-legend-'+capId+'"></div></div>';
  html+='</div>';
  ap.innerHTML=html;

  // Draw individual waveforms with Item 26: ResizeObserver
  var _roInstances=[];
  tracks.forEach(function(cl,idx){
    var fn=cl.filename, color=_WF_COLORS[idx%_WF_COLORS.length];
    var canvas=ap.querySelector('.wf-canvas[data-fn="'+CSS.escape(fn)+'"]');
    if(!canvas)return;
    requestAnimationFrame(function(){_drawWF(canvas,waveforms[fn],color,'#020810');});
    if(typeof ResizeObserver!=='undefined'){
      var ro=new ResizeObserver(function(){
        requestAnimationFrame(function(){_drawWF(canvas,waveforms[fn],color,'#020810');});
      });
      ro.observe(canvas); _roInstances.push(ro);
    }
  });

  // Legend
  var legendEl=document.getElementById('cmp-legend-'+capId);
  if(legendEl){
    legendEl.innerHTML=tracks.map(function(cl,idx){
      return '<div class="compare-legend-item"><div class="compare-legend-swatch" style="background:'+_WF_COLORS[idx%_WF_COLORS.length]+'"></div>'+esc(cl.stream)+' <span style="color:#445566">('+esc(cl.site)+')</span></div>';
    }).join('');
  }
  var specLegEl=document.getElementById('spec-legend-'+capId);
  if(specLegEl) specLegEl.innerHTML=document.getElementById('cmp-legend-'+capId).innerHTML;

  // Compare button
  var cmpBtn=document.getElementById('cmp-btn-'+capId);
  var cmpPanel=document.getElementById('cmp-panel-'+capId);
  var cmpCanvas=document.getElementById('cmp-canvas-'+capId);
  var _cmpDrawn=false;
  if(cmpBtn&&cmpPanel&&cmpCanvas){
    cmpBtn.addEventListener('click',function(){
      var vis=cmpPanel.style.display!=='none';
      cmpPanel.style.display=vis?'none':'';
      cmpBtn.textContent=vis?'📊 Compare':'📊 Hide';
      if(!vis&&!_cmpDrawn){
        _cmpDrawn=true;
        requestAnimationFrame(function(){
          var allPts=tracks.map(function(cl){return cmpWaves[cl.filename]||null;});
          _drawCompare(cmpCanvas,allPts,tracks.map(function(_,i){return _WF_COLORS[i%_WF_COLORS.length];}));
        });
        if(typeof ResizeObserver!=='undefined'){
          var ro=new ResizeObserver(function(){
            if(cmpPanel.style.display!=='none') requestAnimationFrame(function(){
              _drawCompare(cmpCanvas,tracks.map(function(cl){return cmpWaves[cl.filename]||null;}),tracks.map(function(_,i){return _WF_COLORS[i%_WF_COLORS.length];}));
            });
          }); ro.observe(cmpCanvas); _roInstances.push(ro);
        }
      }
    });
  }

  // Spectrum button
  var specBtn=document.getElementById('spec-btn-'+capId);
  var specPanel=document.getElementById('spec-panel-'+capId);
  var specCanvas=document.getElementById('spec-canvas-'+capId);
  var _specDrawn=false;
  if(specBtn&&specPanel&&specCanvas){
    specBtn.addEventListener('click',function(){
      var vis=specPanel.style.display!=='none';
      specPanel.style.display=vis?'none':'';
      specBtn.textContent=vis?'📈 Spectrum':'📈 Hide';
      if(!vis&&!_specDrawn){
        _specDrawn=true;
        requestAnimationFrame(function(){
          _drawSpectrum(specCanvas,tracks.map(function(cl){return bands[cl.filename]||null;}),tracks.map(function(_,i){return _WF_COLORS[i%_WF_COLORS.length];}));
        });
      }
    });
  }

  // Play all
  document.getElementById('play-all-'+capId).addEventListener('click',function(){
    ap.querySelectorAll('.align-audio').forEach(function(a){a.currentTime=parseFloat(a.dataset.offset)||0;a.play().catch(function(){});});
  });
  document.getElementById('stop-all-'+capId).addEventListener('click',function(){
    ap.querySelectorAll('.align-audio').forEach(function(a){a.pause();});
  });

  // Re-align with chosen reference (Item 20)
  var realignBtn=document.getElementById('realign-btn-'+capId);
  if(realignBtn){
    realignBtn.addEventListener('click',function(){
      var sel=document.getElementById('ref-sel-'+capId);
      var refFn2=sel?sel.value:'';
      var alignBtn=document.querySelector('.align-btn[data-capid="'+capId+'"]');
      ap.innerHTML=''; ap.dataset.alignDone='';
      if(alignBtn) alignCapture(capId,alignBtn,refFn2||null);
    });
  }
}

// ── storage settings (Item 27) ────────────────────────────────────────────────
function loadDisk(){
  fetch('/api/synccap/disk',{credentials:'same-origin'})
  .then(function(r){return r.json();})
  .then(function(d){
    if(d.error)return;
    document.getElementById('disk-badge').textContent='Clips: '+fmtBytes(d.clip_bytes)+'  Free: '+fmtBytes(d.free_bytes);
    document.getElementById('disk-info').textContent='Dir: '+d.clip_dir+'  •  '+fmtBytes(d.clip_bytes)+' used  •  '+fmtBytes(d.free_bytes)+' free';
  }).catch(function(){});
  fetch('/api/synccap/config',{credentials:'same-origin'})
  .then(function(r){return r.json();})
  .then(function(d){
    var inp=document.getElementById('clip-dir-input');
    inp.value=d.clip_dir||'';
    inp.placeholder=d.effective_dir||'plugins/synccap_clips';
  }).catch(function(){});
}
document.getElementById('save-storage-btn').addEventListener('click',function(){
  var dir=document.getElementById('clip-dir-input').value.trim();
  fetch('/api/synccap/config',{
    method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRFToken':_csrf()},
    body:JSON.stringify({clip_dir:dir})
  })
  .then(function(r){return r.json();})
  .then(function(d){if(d.ok){showMsg('Storage path saved — new clips will use: '+d.clip_dir,true);loadDisk();}})
  .catch(function(){showMsg('Failed to save storage path',false);});
});

// ── init ──────────────────────────────────────────────────────────────────────
loadInputs();
loadCaptures();
loadDisk();

})();
</script>
</body>
</html>
"""
