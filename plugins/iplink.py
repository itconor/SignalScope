# iplink.py — WebRTC IP Link (ipDTL-style browser contribution codec)
# Drop into the plugins/ subdirectory.
#
# The hub creates named "rooms" with a shareable talent URL.
# Any browser (phone, laptop, tablet) opens the link — no download required.
# Low-latency Opus audio, bidirectional IFB/talkback, live level meters, RTT stats.
# Signalling is pure HTTP polling — no WebSocket dependency.

SIGNALSCOPE_PLUGIN = {
    "id":      "iplink",
    "label":   "IP Link",
    "url":     "/hub/iplink",
    "icon":    "🎙",
    "version": "1.4.2",
}

import asyncio as _asyncio
import fractions as _fractions
import json
import os
import queue as _queue
import random
import secrets
import socket
import struct
import threading
import time
import uuid

# Optional: aiortc for server-side WebRTC (talent stays connected without browser)
try:
    from aiortc import (RTCPeerConnection as _RTCPC,
                        RTCSessionDescription as _RTCSDesc,
                        RTCIceCandidate as _RTCICand,
                        RTCConfiguration as _RTCCfg,
                        RTCIceServer as _RTCISrv,
                        MediaStreamTrack as _MSTBase)
    import av as _av
    _AIORTC = True
except ImportError:
    _AIORTC = False

try:
    import numpy as _np
    _HAS_NP = True
except ImportError:
    _HAS_NP = False

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_lock   = threading.Lock()
_rooms  = {}      # room_id -> room dict  (see _new_room())
_log    = None    # set in register()

_ROOM_EXPIRE_S    = 7200   # expire idle rooms after 2 h (permanent rooms never expire)
_STUN_SERVERS     = ["stun:stun.l.google.com:19302", "stun:stun1.l.google.com:19302"]
_STUN             = _STUN_SERVERS   # alias used in route templates
_ROOMS_PATH       = os.path.join(_BASE_DIR, "iplink_rooms.json")

# ── Livewire / AES67 RTP multicast output ─────────────────────────────────────
_lw_senders      = {}   # room_id → _LivewireSender (hub-local sender)
_lw_relay_slots  = {}   # slot_id → queue.Queue  (hub→client PCM relay)
_lw_relay_closed = set()  # slot_ids that are closed/done

# ── Server-side routing (permanent rooms) ─────────────────────────────────────
_INTERNAL_TOKEN = secrets.token_hex(32)  # used for internal server-to-server calls
_aio_loop       = None   # asyncio event loop (background thread) for aiortc
_server_pcs     = {}     # room_id → RTCPeerConnection (server-side WebRTC)
_server_tracks  = {}     # room_id → _ServerAudioTrack
_src_threads    = {}     # room_id → (Thread, Event)
_monitor_ref    = None   # set in register()
_listen_reg_ref = None   # set in register()


class _LivewireSender:
    """Send AES67-compatible RTP multicast (L24/48kHz) from S16LE PCM input.
    Compatible with Axia Livewire standard channels and generic AES67 receivers."""

    PT         = 96      # dynamic payload type for L24/48000/2
    RATE       = 48000
    SAMPLES_PP = 48      # 1 ms per packet — Livewire standard

    def __init__(self, address: str, port: int = 5004, n_ch: int = 2, ttl: int = 32):
        self.address = address
        self.port    = port
        self.n_ch    = n_ch
        self._seq    = random.randint(0, 0xFFFF)
        self._ts     = random.randint(0, 0xFFFFFFFF)
        self._ssrc   = random.randint(0, 0xFFFFFFFF)
        self._buf    = b""
        self.closed  = False
        self._sock   = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)

    @staticmethod
    def livewire_address(channel: int) -> str:
        """Convert Livewire channel number (1-32767) to multicast address."""
        return f"239.192.{(channel >> 8) & 0xFF}.{channel & 0xFF}"

    def _rtp_header(self) -> bytes:
        return struct.pack('!BBHII',
            0x80,
            self.PT,
            self._seq  & 0xFFFF,
            self._ts   & 0xFFFFFFFF,
            self._ssrc,
        )

    @staticmethod
    def _s16le_to_l24(data: bytes) -> bytes:
        """Convert 16-bit signed LE PCM to 24-bit signed big-endian (L24)."""
        out = bytearray()
        for i in range(0, len(data) - 1, 2):
            s = int.from_bytes(data[i:i+2], 'little', signed=True)
            # Scale S16 → S24 (shift left 8)
            out += (s << 8).to_bytes(3, 'big', signed=True)
        return bytes(out)

    def feed(self, pcm_s16le: bytes):
        """Buffer S16LE PCM and emit RTP packets as data accumulates."""
        if self.closed:
            return
        self._buf += pcm_s16le
        bytes_pp = self.SAMPLES_PP * self.n_ch * 2   # bytes per packet (S16LE)
        while len(self._buf) >= bytes_pp:
            chunk    = self._buf[:bytes_pp]
            self._buf = self._buf[bytes_pp:]
            payload  = self._s16le_to_l24(chunk)
            pkt      = self._rtp_header() + payload
            try:
                self._sock.sendto(pkt, (self.address, self.port))
            except Exception:
                pass
            self._seq = (self._seq + 1) & 0xFFFF
            self._ts  = (self._ts + self.SAMPLES_PP) & 0xFFFFFFFF

    def stop(self):
        self.closed = True
        try:
            self._sock.close()
        except Exception:
            pass


# ── Server-side audio track (aiortc) ──────────────────────────────────────────
# Only defined when aiortc + numpy are available; otherwise _ServerAudioTrack = None.
if _AIORTC and _HAS_NP:
    class _ServerAudioTrack(_MSTBase):
        """Audio track whose PCM is fed by the server-side source thread.
        Delivers 20 ms Opus frames (960 samples at 48 kHz) to the talent browser."""
        kind    = "audio"
        SAMPLES = 960           # 20 ms at 48 kHz — Opus preferred frame size
        RATE    = 48000
        FRAME_B = SAMPLES * 2   # bytes per frame: S16LE mono

        def __init__(self):
            super().__init__()
            self._q   = _asyncio.Queue(maxsize=200)
            self._buf = b""
            self._pts = 0
            self._tb  = _fractions.Fraction(1, self.RATE)

        async def _push(self, pcm_s16le: bytes):
            """Called from source thread (via run_coroutine_threadsafe)."""
            try:
                self._q.put_nowait(pcm_s16le)
            except _asyncio.QueueFull:
                pass  # drop on overflow

        async def recv(self):
            while len(self._buf) < self.FRAME_B:
                try:
                    chunk = await _asyncio.wait_for(self._q.get(), timeout=2.0)
                    self._buf += chunk
                except _asyncio.TimeoutError:
                    self._buf += b'\x00' * self.FRAME_B  # silence pad on stall
            frame_bytes = self._buf[:self.FRAME_B]
            self._buf   = self._buf[self.FRAME_B:]
            samples = _np.frombuffer(frame_bytes, dtype=_np.int16).astype(_np.float32) / 32768.0
            frame   = _av.AudioFrame.from_ndarray(samples.reshape(1, -1),
                                                   format='fltp', layout='mono')
            frame.sample_rate = self.RATE
            frame.pts         = self._pts
            frame.time_base   = self._tb
            self._pts        += self.SAMPLES
            return frame
else:
    _ServerAudioTrack = None


# ── asyncio event loop for server-side WebRTC ─────────────────────────────────
def _ensure_aio_loop():
    """Start the asyncio event loop in a background thread (idempotent)."""
    global _aio_loop
    if _aio_loop and _aio_loop.is_running():
        return _aio_loop
    _aio_loop = _asyncio.new_event_loop()
    threading.Thread(target=_aio_loop.run_forever,
                     daemon=True, name="IPLink-AIO").start()
    return _aio_loop


# ── Server-side source routing ─────────────────────────────────────────────────

def _route_pcm_chunk(room_id: str, pcm: bytes):
    """Deliver one PCM chunk to:  (a) server-side WebRTC track → talent,
    (b) hub-local Livewire sender → multicast.
    Called from the source thread — must be thread-safe."""
    # (a) aiortc audio track
    if _AIORTC and _aio_loop:
        trk = _server_tracks.get(room_id)
        if trk:
            _asyncio.run_coroutine_threadsafe(trk._push(pcm), _aio_loop)
    # (b) hub-local Livewire multicast
    room = _rooms.get(room_id)
    if not room:
        return
    out = room.get("output", {})
    if out.get("type") in ("livewire", "multicast") and out.get("site", "hub") == "hub":
        addr = out.get("address", "")
        if not addr:
            return
        sender = _lw_senders.get(room_id)
        if not sender:
            sender = _LivewireSender(addr, out.get("port", 5004), n_ch=1)
            _lw_senders[room_id] = sender
        sender.feed(pcm)


def _local_source_loop(room_id: str, idx: int, stop_evt: threading.Event):
    """Poll a local monitor input's _stream_buffer for new PCM and route it.

    Uses object-identity tracking on deque items — handles ring-buffer wraparound.
    Polls every 50 ms, which is fine for 0.1 s chunks produced by the monitor loop.
    """
    last_key = None
    while not stop_evt.is_set():
        if not _monitor_ref:
            time.sleep(0.5)
            continue
        inputs = getattr(_monitor_ref.app_cfg, 'inputs', []) or []
        if idx >= len(inputs):
            time.sleep(1.0)
            continue
        inp  = inputs[idx]
        sbuf = list(getattr(inp, '_stream_buffer', []))
        if not sbuf:
            time.sleep(0.1)
            continue
        if last_key is None:
            # First run: start from current tail; don't replay history
            last_key = id(sbuf[-1])
            time.sleep(0.05)
            continue
        # Find our last-seen chunk by object identity
        pos = None
        for i, c in enumerate(sbuf):
            if id(c) == last_key:
                pos = i
                break
        if pos is None:
            # Chunk fell off the rolling deque — skip to current end
            last_key = id(sbuf[-1])
            time.sleep(0.05)
            continue
        for c in sbuf[pos + 1:]:
            _route_pcm_chunk(room_id, c)
            last_key = id(c)
        time.sleep(0.05)


def _remote_source_loop(room_id: str, site: str, idx: int, stop_evt: threading.Event):
    """Stream PCM from a remote client site via the hub listen_registry relay."""
    if not _listen_reg_ref:
        if _log:
            _log(f"[IPLink] No listen_registry available for remote source (room {room_id[:8]})")
        return
    slot = None
    try:
        slot = _listen_reg_ref.create(
            site, idx, kind="scanner", mimetype="application/octet-stream"
        )
    except Exception as exc:
        if _log:
            _log(f"[IPLink] Remote slot create failed: {exc}")
        return
    while not stop_evt.is_set():
        try:
            chunk = slot.get(timeout=1.0)
            if chunk is None:
                break
            _route_pcm_chunk(room_id, chunk)
        except Exception:
            if not stop_evt.is_set():
                time.sleep(0.5)
    try:
        slot.closed = True
    except Exception:
        pass


def _src_thread_fn(room_id: str, source: dict, stop_evt: threading.Event):
    site = source.get("site", "local")
    idx  = int(source.get("idx", 0))
    if _log:
        _log(f"[IPLink] Source thread started: room {room_id[:8]} ← {site}[{idx}]")
    if site == "local":
        _local_source_loop(room_id, idx, stop_evt)
    else:
        _remote_source_loop(room_id, site, idx, stop_evt)
    if _log:
        _log(f"[IPLink] Source thread stopped: room {room_id[:8]}")


def _start_source_routing(room_id: str):
    """Start (or restart) the server-side audio pipeline for a permanent room."""
    _stop_source_routing(room_id)
    room = _rooms.get(room_id)
    if not room or not room.get("permanent") or not room.get("server_managed"):
        return
    source = room.get("source")
    if not source:
        return
    stop_evt = threading.Event()
    t = threading.Thread(
        target=_src_thread_fn,
        args=(room_id, source, stop_evt),
        daemon=True,
        name=f"IPLink-Src-{room_id[:8]}",
    )
    _src_threads[room_id] = (t, stop_evt)
    t.start()


def _stop_source_routing(room_id: str):
    """Stop the server-side audio pipeline for a room (safe to call if not running)."""
    entry = _src_threads.pop(room_id, None)
    if entry:
        entry[1].set()  # signal stop
    if _AIORTC and _aio_loop:
        pc  = _server_pcs.pop(room_id, None)
        trk = _server_tracks.pop(room_id, None)
        if pc:
            _asyncio.run_coroutine_threadsafe(pc.close(), _aio_loop)
        if trk:
            try:
                trk.stop()
            except Exception:
                pass


# ── Server-side WebRTC coroutines (run on _aio_loop) ──────────────────────────

async def _server_accept_offer(room_id: str):
    """Create a server-side RTCPeerConnection and answer the talent's SDP offer."""
    if not _AIORTC or not _ServerAudioTrack:
        return
    with _lock:
        room = _rooms.get(room_id)
        if not room or not room.get("offer"):
            return
        offer_sdp    = room["offer"]
        room["hub_ice"] = []   # clear stale ICE from any prior session
    # Tear down any previous server PC for this room
    old_pc  = _server_pcs.pop(room_id, None)
    old_trk = _server_tracks.pop(room_id, None)
    if old_pc:
        try:
            await old_pc.close()
        except Exception:
            pass
    if old_trk:
        try:
            old_trk.stop()
        except Exception:
            pass
    # Create a fresh audio track and peer connection
    track = _ServerAudioTrack()
    _server_tracks[room_id] = track
    pc = _RTCPC(configuration=_RTCCfg(
        iceServers=[_RTCISrv(urls=u) for u in _STUN_SERVERS]
    ))
    _server_pcs[room_id] = pc

    @pc.on("connectionstatechange")
    async def _on_state():
        state = pc.connectionState
        with _lock:
            r = _rooms.get(room_id)
            if not r:
                return
            if state == "connected":
                r["status"]       = "connected"
                r["connected_at"] = time.time()
                if _log:
                    _log(f"[IPLink] Server PC connected: room '{r['name']}'")
            elif state in ("failed", "disconnected", "closed"):
                r["status"]           = "disconnected"
                r["disconnected_at"]  = time.time()
                if _log:
                    _log(f"[IPLink] Server PC {state}: room '{r['name']}'")

    pc.addTrack(track)
    try:
        await pc.setRemoteDescription(_RTCSDesc(sdp=offer_sdp, type="offer"))

        # Add any talent ICE candidates that arrived before setRemoteDescription
        # (fast clients can POST candidates almost immediately after the offer)
        with _lock:
            r = _rooms.get(room_id)
            early_ice = list(r["talent_ice"]) if r else []
        for cand_data in early_ice:
            await _server_add_ice(room_id, cand_data)

        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        # ── Non-trickle ICE: wait for gathering to finish ────────────────────
        # aiortc starts ICE gathering when setLocalDescription is called.
        # The answer SDP has no candidates until gathering completes.
        # We MUST wait here — otherwise the talent receives an answer with no
        # server candidates and the connection can never be established.
        for _ in range(300):   # up to 30 s (100 ms steps)
            if pc.iceGatheringState == "complete":
                break
            await _asyncio.sleep(0.1)

        final_sdp = pc.localDescription.sdp   # now contains all ICE candidates
        with _lock:
            r = _rooms.get(room_id)
            if r:
                r["answer"] = final_sdp
                r["hub_ice"] = []   # not used in non-trickle mode — candidates are in SDP
                r["status"]  = "connecting"
        if _log:
            r = _rooms.get(room_id)
            _log(f"[IPLink] Server answer ready for room '{r['name'] if r else room_id[:8]}' "
                 f"(ICE state: {pc.iceGatheringState})")
        _start_source_routing(room_id)
    except Exception as exc:
        if _log:
            _log(f"[IPLink] Server offer accept failed for {room_id[:8]}: {exc}")


async def _server_add_ice(room_id: str, cand_data: dict):
    """Add a talent ICE candidate to the server-side peer connection.
    Silently no-ops if the PC doesn't exist or remote description isn't set yet."""
    pc = _server_pcs.get(room_id)
    if not pc or pc.remoteDescription is None:
        return
    cand_str = str(cand_data.get("candidate", ""))
    if not cand_str:
        return
    if cand_str.startswith("candidate:"):
        cand_str = cand_str[10:]
    try:
        # aioice.Candidate.from_sdp parses the SDP-format candidate line
        try:
            from aioice.candidate import Candidate as _AioiceCand
        except ImportError:
            from aioice import Candidate as _AioiceCand
        parsed = _AioiceCand.from_sdp(cand_str)
        rtc_c  = _RTCICand(
            component     = parsed.component,
            foundation    = parsed.foundation,
            ip            = parsed.host,
            port          = parsed.port,
            priority      = parsed.priority,
            protocol      = parsed.transport.lower(),
            type          = parsed.type,
            sdpMid        = cand_data.get("sdpMid", "0"),
            sdpMLineIndex = int(cand_data.get("sdpMLineIndex", 0)),
        )
        await pc.addIceCandidate(rtc_c)
    except Exception:
        pass   # candidate parse failures are non-fatal


# ── Persistent room save / load ────────────────────────────────────────────────

def _save_rooms():
    """Persist permanent rooms to disk."""
    with _lock:
        permanent = {rid: r for rid, r in _rooms.items() if r.get("permanent")}
    # Serialise only the config fields — not transient signalling state
    out = {}
    for rid, r in permanent.items():
        # Strip transient keys (prefixed with _) from output before persisting
        saved_output = {k: v for k, v in r.get("output", {}).items()
                        if not k.startswith("_")}
        out[rid] = {
            "id":            r["id"],
            "name":          r["name"],
            "quality":       r["quality"],
            "permanent":     True,
            "output":        saved_output,
            "created":       r["created"],
            "server_managed": r.get("server_managed", False),
            "source":        r.get("source"),          # {site, idx, name} or None
        }
    try:
        with open(_ROOMS_PATH, "w") as fh:
            json.dump(out, fh, indent=2)
    except Exception as e:
        if _log:
            _log(f"[IPLink] Failed to save rooms: {e}")


def _load_rooms():
    """Load permanent rooms from disk and add to _rooms with reset signalling state."""
    try:
        with open(_ROOMS_PATH) as fh:
            saved = json.load(fh)
    except FileNotFoundError:
        return
    except Exception as e:
        if _log:
            _log(f"[IPLink] Failed to load rooms: {e}")
        return
    loaded = 0
    with _lock:
        for rid, r in saved.items():
            if rid not in _rooms:
                room = _new_room(r["name"], r.get("quality", "broadcast"))
                room["id"]             = rid
                room["permanent"]      = True
                room["output"]         = r.get("output", {})
                room["created"]        = r.get("created", time.time())
                room["server_managed"] = r.get("server_managed", False)
                room["source"]         = r.get("source")
                _rooms[rid]            = room
                loaded += 1
    if _log and loaded:
        _log(f"[IPLink] Loaded {loaded} permanent room(s) from disk")

_SIP_CFG_PATH = os.path.join(_BASE_DIR, "iplink_sip_cfg.json")
_SIP_CFG_DEFAULT = {
    "enabled":      False,
    "server":       "",     # wss://pbx.example.com:8089/ws
    "username":     "",
    "password":     "",
    "domain":       "",     # SIP domain / realm (often same as PBX hostname)
    "display_name": "Studio",
}


def _load_sip_cfg() -> dict:
    try:
        with open(_SIP_CFG_PATH) as fh:
            d = json.load(fh)
        cfg = dict(_SIP_CFG_DEFAULT)
        cfg.update({k: v for k, v in d.items() if k in _SIP_CFG_DEFAULT})
        return cfg
    except Exception:
        return dict(_SIP_CFG_DEFAULT)


def _save_sip_cfg(cfg: dict):
    with open(_SIP_CFG_PATH, "w") as fh:
        json.dump(cfg, fh, indent=2)

# Quality presets (communicated to hub JS to set Opus parameters)
_QUALITY = {
    "voice":     {"maxBitrate": 64000,  "stereo": False, "label": "Voice (64 kbps mono)"},
    "broadcast": {"maxBitrate": 128000, "stereo": True,  "label": "Broadcast (128 kbps stereo)"},
    "hifi":      {"maxBitrate": 256000, "stereo": True,  "label": "Hi-Fi (256 kbps stereo)"},
}


# ─── Room helpers ─────────────────────────────────────────────────────────────

def _new_room(name: str, quality: str = "broadcast") -> dict:
    return {
        "id":            str(uuid.uuid4()),
        "name":          name,
        "quality":       quality if quality in _QUALITY else "broadcast",
        "permanent":     False,
        "output":        {"type": "speaker"},   # speaker | livewire | multicast
        "server_managed": False,                 # permanent rooms only: server handles routing
        "source":        None,                   # {site, idx, name} — server-side audio source
        "created":       time.time(),
        "last_active":  time.time(),
        "status":       "waiting",        # waiting | offer_received | connected | disconnected
        "offer":        None,             # SDP string from talent
        "answer":       None,             # SDP string from hub
        "talent_ice":   [],               # list of ICE candidate JSON strings (talent→hub)
        "hub_ice":      [],               # list of ICE candidate JSON strings (hub→talent)
        "talent_level": 0.0,
        "hub_level":    0.0,
        "hub_muted":    False,
        "talent_ip":    "",
        "connected_at": None,
        "disconnected_at": None,
        "stats":        {},               # RTT, bitrate, etc. from talent
    }


def _room_age_s(room: dict) -> float:
    return round(time.time() - room["created"])


def _touch(room: dict):
    room["last_active"] = time.time()


def _room_public(room: dict) -> dict:
    """Subset of room dict safe to return to API callers."""
    q = _QUALITY.get(room["quality"], _QUALITY["broadcast"])
    r = dict(room)
    r.pop("offer", None)   # never send SDP to listing endpoints
    r.pop("answer", None)
    r["quality_label"]    = q["label"]
    r["talent_ice_count"] = len(room["talent_ice"])
    r["hub_ice_count"]    = len(room["hub_ice"])
    r["age_s"]            = _room_age_s(room)
    r["permanent"]        = bool(room.get("permanent", False))
    r["output"]           = room.get("output", {"type": "speaker"})
    r["server_managed"]        = bool(room.get("server_managed", False))
    r["source"]                = room.get("source")
    r["server_routing_active"] = room.get("id", "") in _src_threads
    if room["connected_at"]:
        r["duration_s"] = round(time.time() - room["connected_at"])
    return r


def _cleanup_thread():
    while True:
        time.sleep(60)
        cutoff = time.time() - _ROOM_EXPIRE_S
        with _lock:
            # Permanent rooms never expire
            expired = [k for k, v in _rooms.items()
                       if v["last_active"] < cutoff and not v.get("permanent")]
            for k in expired:
                del _rooms[k]
            if expired and _log:
                _log(f"[IPLink] Expired {len(expired)} idle room(s)")


# ─── Templates ────────────────────────────────────────────────────────────────

_HUB_TPL = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>IP Link — SignalScope</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<style nonce="{{csp_nonce()}}">
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:radial-gradient(circle at top,#12376f 0%,var(--bg) 38%,#05101f 100%);color:var(--tx);font-size:13px;overflow-x:hidden}
header{background:linear-gradient(180deg,rgba(10,31,65,.96),rgba(9,24,48,.96));border-bottom:1px solid var(--bor);padding:12px 20px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.btn{display:inline-flex;align-items:center;border:none;border-radius:8px;padding:5px 12px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;text-decoration:none;white-space:nowrap}
.btn:hover{filter:brightness(1.15)}
.bp{background:var(--acc);color:#fff}.bd{background:var(--al);color:#fff}.bg{background:var(--bor);color:var(--tx)}
.bs{padding:3px 9px;font-size:12px}.nav-active{background:var(--acc)!important;color:#fff!important}
main{padding:20px;max-width:1100px;margin:0 auto}
.card{background:var(--sur);border:1px solid var(--bor);border-radius:12px;overflow:hidden;margin-bottom:16px}
.ch{padding:9px 14px;display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--bor);background:linear-gradient(180deg,#143766,#102b54);font-size:12px;font-weight:700;color:var(--acc);text-transform:uppercase;letter-spacing:.06em}
.cb{padding:14px}
.field{display:flex;flex-direction:column;gap:4px;margin-bottom:12px}
label{font-size:11px;color:var(--mu);font-weight:600;text-transform:uppercase;letter-spacing:.05em}
input,select{background:#0d1e40;border:1px solid var(--bor);border-radius:6px;color:var(--tx);padding:6px 9px;font-size:13px;font-family:inherit;width:100%}
input:focus,select:focus{border-color:var(--acc);outline:none}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;background:#1e3a5f;color:var(--acc)}
.b-ok{background:#0f2318;color:var(--ok);border:1px solid #166534}
.b-wn{background:#2a1e06;color:var(--wn);border:1px solid #92400e}
.b-al{background:#2a0a0a;color:var(--al);border:1px solid #991b1b}
.b-mu{background:#1a2040;color:var(--mu)}
/* Room cards */
.room-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px;margin-top:16px}
.room-card{background:var(--sur);border:1px solid var(--bor);border-radius:12px;overflow:hidden}
.room-card.rc-connected{border-color:var(--ok)}
.room-card.rc-offer{border-color:var(--wn);animation:pulse-border 1.5s infinite}
@keyframes pulse-border{0%,100%{border-color:var(--wn)}50%{border-color:#fbbf24}}
.rc-hdr{padding:10px 14px;background:linear-gradient(180deg,#143766,#102b54);display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--bor)}
.rc-name{font-weight:700;font-size:14px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rc-body{padding:12px 14px}
.rc-row{display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-bottom:1px solid rgba(23,52,95,.4);font-size:12px}
.rc-row:last-child{border-bottom:none}
.rc-lbl{color:var(--mu)}
/* Level bars */
.lvl-wrap{display:flex;align-items:center;gap:8px;margin:8px 0}
.lvl-label{font-size:11px;color:var(--mu);width:36px;text-align:right;flex-shrink:0}
.lvl-outer{flex:1;height:8px;background:#0a1628;border-radius:4px;overflow:hidden}
.lvl-fill{height:8px;border-radius:4px;transition:width .15s;background:var(--ok)}
.lvl-val{font-size:11px;width:36px;text-align:left;flex-shrink:0;font-variant-numeric:tabular-nums}
/* Accept call overlay */
.accept-banner{background:linear-gradient(135deg,#2a1e06,#3a2a06);border:1px solid var(--wn);border-radius:8px;padding:10px 14px;margin-bottom:12px;display:flex;align-items:center;gap:10px}
.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.copy-flash{color:var(--ok);font-size:11px;opacity:0;transition:opacity .3s}
.copy-flash.show{opacity:1}
.msg{padding:8px 12px;border-radius:6px;font-size:12px;margin-bottom:10px}
.msg-ok{background:#0f2318;color:var(--ok);border:1px solid #166534}
.msg-err{background:#2a0a0a;color:var(--al);border:1px solid #991b1b}
/* SIP */
.sip-pill{display:inline-flex;align-items:center;gap:5px;padding:3px 10px;border-radius:999px;font-size:11px;font-weight:600;transition:background .3s}
.sip-off{background:#1a2040;color:var(--mu)}
.sip-conn{background:#0f2318;color:var(--ok);border:1px solid #166534}
.sip-ring{background:#2a1e06;color:var(--wn);border:1px solid #92400e;animation:pulse-border 1s infinite}
.sip-incall{background:#0f2318;color:var(--ok);border:1px solid #166534}
.sip-err{background:#2a0a0a;color:var(--al);border:1px solid #991b1b}
.sip-sdot{width:7px;height:7px;border-radius:50%;flex-shrink:0;background:currentColor}
.sip-incoming-banner{background:linear-gradient(135deg,#2a1e06,#3a2a06);border:1px solid var(--wn);border-radius:8px;padding:12px 16px;margin-bottom:14px;display:none;flex-direction:row;align-items:center;gap:12px;animation:pulse-border 1.5s infinite}
.sip-call-card{background:var(--sur);border:2px solid var(--ok);border-radius:12px;overflow:hidden;margin-bottom:16px;display:none}
details>summary{cursor:pointer;font-size:12px;color:var(--mu);font-weight:600;text-transform:uppercase;letter-spacing:.05em;padding:4px 0;list-style:none;display:flex;align-items:center;gap:6px}
details>summary::before{content:'▶';font-size:10px;transition:transform .2s}
details[open]>summary::before{transform:rotate(90deg)}
/* Toggle switch (ipDTL-style settings) */
.tog-row{display:flex;justify-content:space-between;align-items:center;padding:9px 0;border-bottom:1px solid rgba(23,52,95,.4)}
.tog-row:last-child{border-bottom:none}
.tog-lbl{font-size:13px;color:var(--tx)}
.tog{position:relative;display:inline-block;width:42px;height:24px;flex-shrink:0}
.tog input{opacity:0;width:0;height:0}
.tog-sl{position:absolute;cursor:pointer;inset:0;background:#1a2a4a;border-radius:24px;transition:.25s}
.tog-sl:before{content:'';position:absolute;width:18px;height:18px;left:3px;bottom:3px;background:#8aa4c8;border-radius:50%;transition:.25s}
input:checked+.tog-sl{background:var(--ok)}
input:checked+.tog-sl:before{transform:translateX(18px);background:#fff}
/* Faders / sliders */
.fader-row{display:flex;align-items:center;gap:10px;padding:6px 0}
.fader-lbl{font-size:11px;color:var(--mu);width:36px;text-align:right;flex-shrink:0}
input[type=range].fader{flex:1;height:4px;accent-color:var(--acc);cursor:pointer}
.fader-val{font-size:11px;width:32px;text-align:left;font-variant-numeric:tabular-nums}
/* On-Air button */
.btn-onair{background:#1a3a1a;color:var(--ok);border:1px solid var(--ok)}
.btn-onair.active{background:var(--al);color:#fff;border-color:var(--al);animation:pulse-border 1.2s infinite}
/* Source selector */
select.src-sel{background:#0d1e40;border:1px solid var(--bor);border-radius:6px;color:var(--tx);padding:4px 8px;font-size:12px;font-family:inherit;width:100%;margin-top:4px}
/* IFB source status badge */
.rc-src-badge{font-size:10px;font-weight:600;border-radius:4px;padding:3px 6px;margin-top:4px;display:inline-block}
.rc-src-active{background:rgba(34,197,94,.15);color:var(--ok);border:1px solid rgba(34,197,94,.3)}
.rc-src-pending{background:rgba(138,164,200,.12);color:var(--mu);border:1px solid rgba(138,164,200,.2)}
/* Server-managed badge in room header */
.rc-sm-badge{font-size:10px;font-weight:700;border-radius:4px;padding:2px 7px;display:inline-flex;align-items:center;gap:3px}
.rc-sm-on{background:rgba(23,168,255,.18);color:var(--acc);border:1px solid rgba(23,168,255,.3)}
.rc-sm-idle{background:rgba(138,164,200,.12);color:var(--mu);border:1px solid rgba(138,164,200,.2)}
/* Output config panel */
.out-panel{background:#080f20;border:1px solid var(--bor);border-radius:8px;padding:10px 12px;margin-top:8px;font-size:12px}
.out-panel label{display:block;padding:3px 0;cursor:pointer;color:var(--tx);line-height:1.6}
.out-panel input[type=radio]{accent-color:var(--acc);vertical-align:middle;margin-right:5px}
.out-panel input[type=number],.out-panel input[type=text]{background:#0d1e40;border:1px solid var(--bor);border-radius:5px;color:var(--tx);padding:4px 8px;font-size:12px;font-family:inherit;width:100%;margin-top:4px}
.out-sub{padding:6px 0 2px 22px;display:none}
.out-sub.show{display:block}
.lw-addr{font-size:11px;color:var(--mu);margin-top:3px}
/* Permanent room indicator */
.rc-perm{font-size:13px;opacity:.7;flex-shrink:0}
/* Livewire active badge */
.lw-badge{background:#1a0f40;color:#a78bfa;border:1px solid #5b21b6;display:inline-flex;align-items:center;gap:5px;padding:2px 8px;border-radius:999px;font-size:10px;font-weight:700}
</style></head><body>
{{ topnav("iplink") | safe }}
<main>
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:10px">
    <div>
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
        <h1 style="font-size:20px;font-weight:700">🎙 IP Link</h1>
        <span class="sip-pill sip-off" id="sipStatusPill"><span class="sip-sdot"></span><span id="sipStatusTxt">SIP: Off</span></span>
      </div>
      <p style="font-size:12px;color:var(--mu);margin-top:4px">Browser-based contribution codec — share a link or take SIP calls from any device</p>
    </div>
    <button class="btn bp" id="createBtn">＋ New Room</button>
  </div>

  <!-- Create room panel -->
  <div id="createPanel" class="card" style="display:none;margin-bottom:16px">
    <div class="ch">📞 Create new room</div>
    <div class="cb">
      <div id="createMsg"></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
        <div class="field">
          <label>Room name</label>
          <input id="cName" placeholder="e.g. Studio A Guest Line" maxlength="50">
        </div>
        <div class="field">
          <label>Audio quality</label>
          <select id="cQuality">
            <option value="voice">Voice (64 kbps mono)</option>
            <option value="broadcast" selected>Broadcast (128 kbps stereo)</option>
            <option value="hifi">Hi-Fi (256 kbps stereo)</option>
          </select>
        </div>
      </div>
      <div style="margin-bottom:12px">
        <label style="font-size:12px;color:var(--tx);cursor:pointer;display:block;line-height:1.5">
          <input type="checkbox" id="cPermanent" style="accent-color:var(--acc);vertical-align:middle;margin-right:5px">📌 Permanent room (survives restarts, never expires)
        </label>
      </div>
      <div style="display:flex;gap:8px">
        <button class="btn bp" id="createSubmit">Create Room</button>
        <button class="btn bg" id="createCancel">Cancel</button>
      </div>
    </div>
  </div>

  <!-- SIP: incoming call banner -->
  <div class="sip-incoming-banner" id="sipIncomingBanner">
    <span style="font-size:22px">📞</span>
    <div style="flex:1">
      <div style="font-weight:700;font-size:14px">Incoming SIP Call</div>
      <div style="font-size:12px;color:var(--wn)" id="sipCallerName">Unknown caller</div>
    </div>
    <button class="btn bp bs" id="sipAnswerBtn">✅ Answer</button>
    <button class="btn bd bs" id="sipRejectBtn">✗ Decline</button>
  </div>

  <!-- SIP: active call card -->
  <div class="sip-call-card" id="sipCallCard">
    <div class="rc-hdr">
      <span style="font-size:18px">☎️</span>
      <span class="rc-name" id="sipCallRemote">SIP Call</span>
      <span class="badge b-ok">🔴 Live</span>
    </div>
    <div class="rc-body">
      <div class="lvl-wrap">
        <span class="lvl-label" style="font-size:10px">Remote</span>
        <div class="lvl-outer"><div class="lvl-fill" id="sipRemoteLvl" style="width:0%"></div></div>
        <span class="lvl-val" id="sipRemoteLvlVal">—</span>
      </div>
      <div class="lvl-wrap">
        <span class="lvl-label" style="font-size:10px">Mic</span>
        <div class="lvl-outer"><div class="lvl-fill" id="sipMicLvl" style="width:0%;background:var(--acc)"></div></div>
        <span class="lvl-val" id="sipMicLvlVal">—</span>
      </div>
      <div class="rc-row"><span class="rc-lbl">Duration</span><span id="sipCallDur">—</span></div>
      <div class="rc-row" id="sipRttRow" style="display:none"><span class="rc-lbl">RTT</span><span id="sipRttVal">—</span></div>
      <div class="actions">
        <button class="btn bg bs" id="sipMuteBtn">🎤 Mic ON</button>
        <button class="btn bd bs" id="sipHangupBtn">✗ Hang Up</button>
      </div>
    </div>
  </div>

  <!-- Settings panel -->
  <div class="card" style="margin-bottom:16px">
    <div class="ch">⚙ Settings</div>
    <div class="cb" style="display:grid;grid-template-columns:1fr 1fr;gap:0 32px">
      <div>
        <div class="tog-row">
          <span class="tog-lbl">Ringtone</span>
          <label class="tog"><input type="checkbox" id="settRingtone"><span class="tog-sl"></span></label>
        </div>
        <div class="tog-row">
          <span class="tog-lbl">Mute on Hangup</span>
          <label class="tog"><input type="checkbox" id="settMuteHangup"><span class="tog-sl"></span></label>
        </div>
        <div class="tog-row">
          <span class="tog-lbl">Auto-Accept</span>
          <label class="tog"><input type="checkbox" id="settAutoAccept"><span class="tog-sl"></span></label>
        </div>
      </div>
      <div>
        <div class="field" style="margin-bottom:0">
          <label>Default Audio Source</label>
          <select id="globalSrcSel" class="src-sel">
            <option value="mic">🎤 Hub Microphone</option>
          </select>
          <span style="font-size:11px;color:var(--mu);margin-top:4px">Default for rooms set to "↑ Default source"</span>
        </div>
      </div>
    </div>
  </div>

  <!-- Room grid (rendered by JS) -->
  <div id="roomGrid"></div>
  <p id="noRooms" style="color:var(--mu);font-size:13px;display:none">No rooms yet — create one to generate a shareable link for your contributor.</p>

  <!-- SIP section: dial + settings -->
  <div class="card" style="margin-top:16px">
    <div class="ch">☎ SIP Calls</div>
    <div class="cb">
      <div style="display:flex;gap:8px;margin-bottom:10px">
        <input id="sipDialInput" placeholder="Extension, number, or sip:user@domain" style="flex:1">
        <button class="btn bp" id="sipDialBtn">📞 Call</button>
      </div>
      <div id="sipCallErrMsg" style="display:none;color:var(--al);font-size:12px;margin-bottom:8px;padding:6px 10px;background:#2a0a0a;border-radius:6px;border:1px solid #991b1b"></div>
      <details id="sipSettingsDetails">
        <summary>SIP Account Settings</summary>
        <div style="margin-top:14px">
          <div id="sipSaveMsg" style="margin-bottom:10px"></div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
            <div class="field" style="grid-column:1/-1">
              <label>WebSocket Server URL</label>
              <input id="sipServer" placeholder="wss://pbx.example.com:8089/ws" autocomplete="off" spellcheck="false">
            </div>
            <div class="field">
              <label>SIP Username</label>
              <input id="sipUser" autocomplete="off" spellcheck="false">
            </div>
            <div class="field">
              <label>Password</label>
              <input id="sipPass" type="password" autocomplete="new-password">
            </div>
            <div class="field">
              <label>SIP Domain / Realm</label>
              <input id="sipDomain" placeholder="pbx.example.com" autocomplete="off" spellcheck="false">
            </div>
            <div class="field">
              <label>Display Name</label>
              <input id="sipDisplayName" value="Studio">
            </div>
          </div>
          <div style="margin-top:4px;margin-bottom:14px">
            <label style="font-size:12px;color:var(--tx);cursor:pointer;display:block;line-height:1.5">
              <input type="checkbox" id="sipEnabled" style="accent-color:var(--acc);vertical-align:middle;margin-right:5px">Auto-connect when page loads
            </label>
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <button class="btn bp" id="sipSaveBtn">💾 Save &amp; Connect</button>
            <button class="btn bg" id="sipDisconnectBtn">Disconnect</button>
          </div>
        </div>
      </details>
    </div>
  </div>

  <!-- Hidden audio elements -->
  <audio id="hubAudio" autoplay playsinline style="display:none"></audio>
  <audio id="sipAudio" autoplay playsinline style="display:none"></audio>
</main>

<script nonce="{{csp_nonce()}}">
var _csrf = (document.querySelector('meta[name="csrf-token"]')||{}).content || '';
function csrfHdr(){ return {'X-CSRFToken': (document.querySelector('meta[name="csrf-token"]')||{}).content||'','Content-Type':'application/json'}; }
function _csrfPost(url, body){ return fetch(url,{method:'POST',credentials:'same-origin',headers:csrfHdr(),body:JSON.stringify(body)}); }

// ─── Per-room WebRTC state ───────────────────────────────────────────────────
var _pcs = {};      // room_id → RTCPeerConnection (or true as placeholder)
var _streams = {};  // room_id → local MediaStream (mic)
var _iceIdx = {};   // room_id → last hub_ice index sent / talent_ice index consumed
var _levels = {};   // room_id → AudioContext analyser
var _connErrs = {}; // room_id → error string (shown in card, auto-cleared after 10 s)
var _micStream = null; // shared mic stream for hub side
var _sendGain = {};  // room_id → GainNode (send to talent)
var _recvGain = {};  // room_id → GainNode (received from talent)
var _recvCtx  = {};  // room_id → AudioContext for received audio
var _sendVol  = {};  // room_id → send volume (0-200, 100=unity)
var _recvVol  = {};  // room_id → recv volume (0-200, 100=unity)
var _onAir    = {};  // room_id → bool (on-air state)
var _feedReader = {}; // room_id → ReadableStreamReader (stream injection)
var _prevOfferRooms = {}; // room_id → bool (for ringtone trigger detection)
var _outPanelOpen   = {}; // room_id → bool
var _pendingOutType = {}; // room_id → output type selected but not yet saved (keeps radio stable across re-renders)
var _outputCapture  = {}; // room_id → {node, reader}
var _lwWorkletUrl   = null;
var _streamSources  = []; // cached list from /api/iplink/streams (never cleared on empty response)
var _roomSrc        = {}; // room_id → 'global' | 'mic' | 'stream:SITE:IDX'
var _srcSelOpen     = false; // true while a per-room source <select> is open — blocks _renderRooms innerHTML rebuild
var _feedAudio      = {}; // room_id → HTMLAudioElement (stream feed)
var _feedNodes      = {}; // room_id → {srcNode, gainNode, dest} — Web Audio nodes for active feed
// Single shared AudioContext for ALL stream feeds.
// Created/resumed on the first user gesture so it is always in "running" state
// by the time _injectStreamAudio is called (even from async callbacks).
// createMediaElementSource only redirects audio away from speakers when the
// context is running — if the context is suspended, Chrome lets the audio
// element play through speakers AND delivers silence to the Web Audio graph.
var _sharedAudioCtx = null;
function _ensureAudioCtx(){
  if(!_sharedAudioCtx||_sharedAudioCtx.state==='closed'){
    _sharedAudioCtx=new(window.AudioContext||window.webkitAudioContext)({sampleRate:48000});
  }
  if(_sharedAudioCtx.state==='suspended') _sharedAudioCtx.resume().catch(function(){});
  return _sharedAudioCtx;
}

var STUN = {{stun|tojson}};
var BASE = window.location.origin;

// ─── Settings (localStorage) ─────────────────────────────────────────────────
var _sett = {ringtone:false,muteHangup:false,autoAccept:false};
function _loadSett(){
  try{var s=JSON.parse(localStorage.getItem('iplink_sett')||'{}');Object.assign(_sett,s);}catch(e){}
  document.getElementById('settRingtone').checked = !!_sett.ringtone;
  document.getElementById('settMuteHangup').checked = !!_sett.muteHangup;
  document.getElementById('settAutoAccept').checked = !!_sett.autoAccept;
}
function _saveSett(){
  _sett.ringtone    = document.getElementById('settRingtone').checked;
  _sett.muteHangup  = document.getElementById('settMuteHangup').checked;
  _sett.autoAccept  = document.getElementById('settAutoAccept').checked;
  try{localStorage.setItem('iplink_sett',JSON.stringify(_sett));}catch(e){}
}
['settRingtone','settMuteHangup','settAutoAccept'].forEach(function(id){
  document.getElementById(id).addEventListener('change',_saveSett);
});

// When the global default source changes, re-apply to any connected room using 'global'
document.getElementById('globalSrcSel').addEventListener('change',function(){
  _ensureAudioCtx(); // pre-activate shared context while still in user-gesture
  Object.keys(_pcs).forEach(function(roomId){
    if(_pcs[roomId]&&_pcs[roomId]!==true&&(!_roomSrc[roomId]||_roomSrc[roomId]==='global')){
      _applySourceForRoom(roomId);
    }
  });
});

// ─── Ringtone (Web Audio API beep) ───────────────────────────────────────────
var _ringCtx=null, _ringIntervalId=null;
function _playRing(){
  if(!_sett.ringtone) return;
  if(!_ringCtx) try{_ringCtx=new(window.AudioContext||window.webkitAudioContext)();}catch(e){return;}
  var osc=_ringCtx.createOscillator(), g=_ringCtx.createGain();
  osc.frequency.value=1000; g.gain.value=0.3;
  osc.connect(g); g.connect(_ringCtx.destination);
  osc.start(); osc.stop(_ringCtx.currentTime+0.08);
}
function _startRingtone(){
  if(_ringIntervalId) return;
  _playRing();
  _ringIntervalId = setInterval(function(){ _playRing(); }, 2000);
}
function _stopRingtone(){
  if(_ringIntervalId){ clearInterval(_ringIntervalId); _ringIntervalId=null; }
}

// ─── On-Air toggle ────────────────────────────────────────────────────────────
function toggleOnAir(roomId){
  _onAir[roomId] = !_onAir[roomId];
  _refreshRooms();
}

// ─── Send/Recv volume faders ─────────────────────────────────────────────────
function setSendVol(roomId, val){
  _sendVol[roomId] = parseInt(val);
  var g=_sendGain[roomId]; if(g) g.gain.value=_sendVol[roomId]/100;
  var el=document.getElementById('rc_sendvolval_'+roomId); if(el) el.textContent=val+'%';
}
function setRecvVol(roomId, val){
  _recvVol[roomId] = parseInt(val);
  var g=_recvGain[roomId]; if(g) g.gain.value=_recvVol[roomId]/100;
  var el=document.getElementById('rc_recvvolval_'+roomId); if(el) el.textContent=val+'%';
}

// ─── Stream source cache & population ────────────────────────────────────────
// Never clear the cache on an empty response — a client reconnecting briefly
// can cause the server to return [] which would wipe the dropdown.

function _loadStreamSources(){
  fetch('/api/iplink/streams',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      var s=d.streams||[];
      if(s.length) _streamSources=s;   // only update cache if server returned something
      _populateSourceSelectors();
    }).catch(function(){});
}

// Fill a <select> with stream options, preserving fixedCount leading options.
// Global selector has 1 fixed option (mic); per-room selectors have 2 (default↑, mic).
function _fillSrcSel(sel, curVal, fixedCount){
  while(sel.options.length > fixedCount) sel.remove(fixedCount);
  _streamSources.forEach(function(s){
    var o=document.createElement('option');
    o.value='stream:'+(s.site||'local')+':'+s.idx;
    o.textContent=(s.active?'🟢':'⚪')+' '+s.name+(s.stereo?' · stereo':'');
    sel.appendChild(o);
  });
  for(var i=0;i<sel.options.length;i++){
    if(sel.options[i].value===curVal){sel.value=curVal;return;}
  }
  sel.value=sel.options[0]?sel.options[0].value:'mic';
}

function _populateSourceSelectors(){
  // Only updates the GLOBAL selector — per-room selectors are inlined by
  // _renderRooms with the correct selected= attribute, so they never need
  // a post-render population step.
  var gsel=document.getElementById('globalSrcSel');
  if(gsel) _fillSrcSel(gsel, gsel.value, 1);   // 1 fixed: mic
}

function _srcLabel(val){
  // Return a human-readable label for a source value (e.g. "stream:local:0")
  if(!val||val==='global') return null;
  if(val==='mic') return '🎤 Hub Microphone';
  if(val.indexOf('stream:')===0){
    var parts=val.split(':'), site=parts[1], idx=parseInt(parts[2]);
    for(var i=0;i<_streamSources.length;i++){
      var s=_streamSources[i];
      if((s.site||'local')===site && s.idx===idx) return '📡 '+s.name;
    }
    return '📡 Stream '+idx+(site==='local'?'':' @ '+site);
  }
  return val;
}

function _onRoomSrcChange(roomId, val){
  _ensureAudioCtx(); // pre-activate shared context while still in user-gesture
  _roomSrc[roomId]=val;
  // Update status badge immediately without waiting for the next _renderRooms cycle
  var badge=document.getElementById('rc_srcbadge_'+roomId);
  if(badge){
    var connected=!!(_pcs[roomId]&&_pcs[roomId]!==true);
    if(!val||val==='global'||val==='mic'){
      badge.style.display='none';
    } else {
      var lbl=_srcLabel(val)||val;
      badge.textContent=connected ? lbl+' — sending to talent' : lbl+' — ready, waiting for call';
      badge.className='rc-src-badge'+(connected?' rc-src-active':' rc-src-pending');
      badge.style.display='';
    }
  }
  // Always apply regardless of PC state.
  // If no active PC: _injectStreamAudio warms up the audio feed (starts the
  // stream, sets up the Web Audio graph) so it is ready the moment a call
  // connects.  replaceTrack inside _injectStreamAudio is guarded by the PC
  // check — it only fires when a real RTCPeerConnection exists.
  // When acceptCall() later establishes a PC, it calls _applySourceForRoom()
  // again which runs replaceTrack on the already-warmed feed.
  _applySourceForRoom(roomId);
  // For permanent server-managed rooms: also save source to server so it persists
  var _rd = _roomsData[roomId];
  if(_rd && _rd.permanent && _rd.server_managed && val && val !== 'global'){
    var _payload;
    if(val === 'mic'){
      _payload = {type:'mic'};
    } else {
      var _pts = val.split(':');  // 'stream:SITE:IDX'
      var _src = _streamSources.filter(function(s){
        return (s.site||'local')===_pts[1] && s.idx===parseInt(_pts[2]);
      })[0];
      _payload = {type:'stream', site:_pts[1], idx:parseInt(_pts[2]),
                  name: _src ? _src.name : ''};
    }
    _csrfPost('/api/iplink/room/'+roomId+'/source', _payload).catch(function(){});
  }
}

function toggleServerManaged(roomId){
  var rd = _roomsData[roomId];
  if(!rd || !rd.permanent) return;
  var now = !!rd.server_managed;
  _csrfPost('/api/iplink/room/'+roomId+'/server_managed', {enabled: !now})
    .then(function(d){ return d.json(); })
    .then(function(r){
      if(r.ok && !r.webrtc_available && !now){
        // aiortc not installed — still useful for Livewire-only routing
        console.info('[IPLink] Server routing enabled (Livewire mode — aiortc not available for WebRTC persistence)');
      }
    }).catch(function(){});
}

function saveServerSource(roomId){
  var sel = document.getElementById('smSrc_'+roomId);
  if(!sel) return;
  var val = sel.value;
  var payload;
  if(!val || val === 'none'){
    payload = {type:'none'};
  } else {
    var pts = val.split(':');  // 'stream:SITE:IDX'
    var src = _streamSources.filter(function(s){
      return (s.site||'local')===pts[1] && s.idx===parseInt(pts[2]);
    })[0];
    payload = {type:'stream', site:pts[1], idx:parseInt(pts[2]),
               name: src ? src.name : ''};
  }
  _csrfPost('/api/iplink/room/'+roomId+'/source', payload).catch(function(){});
}

_loadStreamSources();
setInterval(_loadStreamSources, 15000);

// ─── Output site list (for Livewire site selector) ───────────────────────────
var _lwSites = [{id:'hub',name:'Hub (local multicast)'}];
function _loadOutputSites(){
  fetch('/api/iplink/output_sites',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){ _lwSites = d.sites||[{id:'hub',name:'Hub (local multicast)'}]; })
    .catch(function(){});
}
_loadOutputSites();
setInterval(_loadOutputSites, 15000);

// ─── Stream audio injection ───────────────────────────────────────────────────
function _stopFeed(roomId){
  var a=_feedAudio[roomId];
  if(a){ try{a.pause();a.src='';}catch(e){} delete _feedAudio[roomId]; }
  // Disconnect Web Audio nodes but do NOT close _sharedAudioCtx — it is reused
  var nodes=_feedNodes[roomId];
  if(nodes){
    try{nodes.srcNode.disconnect();}catch(e){}
    try{nodes.gainNode.disconnect();}catch(e){}
    delete _feedNodes[roomId];
  }
  if(_feedReader[roomId]){ try{_feedReader[roomId].cancel();}catch(e){} delete _feedReader[roomId]; }
  delete _sendGain[roomId];
}

function _injectStreamAudio(roomId, streamUrl){
  // Architecture: a hidden <audio> element feeds the hub's live-relay MP3 into a
  // shared Web Audio graph (srcNode → gainNode → MediaStreamDestinationNode).
  // The destination stream track is given to WebRTC via replaceTrack so the
  // talent hears the injected stream instead of the hub microphone.
  //
  // CRITICAL: the AudioContext MUST be in "running" state when
  // createMediaElementSource is called.  If the context is suspended, Chrome
  // lets the audio element play through speakers AND sends silence to the Web
  // Audio graph (and hence the WebRTC track).  _ensureAudioCtx() is called
  // synchronously from every user-gesture handler so the context is always
  // running by the time we reach here (even via async callbacks).
  _stopFeed(roomId);

  var ctx=_ensureAudioCtx(); // shared running context — never suspended here

  var gainNode=ctx.createGain();
  gainNode.gain.value=(_sendVol[roomId]||100)/100;
  _sendGain[roomId]=gainNode;

  var dest=ctx.createMediaStreamDestination();
  gainNode.connect(dest);

  var audio=new Audio(streamUrl);
  audio.crossOrigin='use-credentials';
  var srcNode=ctx.createMediaElementSource(audio);
  srcNode.connect(gainNode);
  // srcNode → gainNode → dest (MediaStreamDestination).  Nothing connects to
  // ctx.destination, so no speaker output from this element.

  _feedNodes[roomId]={srcNode:srcNode,gainNode:gainNode,dest:dest};
  _feedAudio[roomId]=audio;

  audio.play().catch(function(e){
    console.warn('[IPLink] Audio element play failed:',e.message||e);
  });

  // Replace (or add) WebRTC audio track so the injected stream goes to the talent
  var pc=_pcs[roomId];
  if(pc&&pc!==true){
    var track=dest.stream.getAudioTracks()[0];
    if(track){
      var senders=pc.getSenders().filter(function(s){return s.track&&s.track.kind==='audio';});
      if(senders.length){
        senders.forEach(function(s){
          s.replaceTrack(track).catch(function(e){
            console.warn('[IPLink] replaceTrack failed:',e.message||e);
          });
        });
      } else {
        console.warn('[IPLink] No audio sender — adding track via addTrack');
        try{ pc.addTrack(track,dest.stream); }catch(e){ console.warn('[IPLink] addTrack failed:',e); }
      }
    }
  }
}

function _applySourceForRoom(roomId){
  // Per-room selection takes precedence; 'global' falls back to the default selector.
  // URL is built client-side (no server round-trip) so _injectStreamAudio is called
  // synchronously — keeping us inside the user-gesture context so the shared
  // AudioContext is still running when createMediaElementSource is invoked.
  var srcVal=_roomSrc[roomId]||'global';
  if(srcVal==='global'){
    var gsel=document.getElementById('globalSrcSel');
    srcVal=gsel?gsel.value:'mic';
  }
  if(srcVal==='mic'){
    _stopFeed(roomId);
    if(_micStream){
      var pc=_pcs[roomId];
      if(pc&&pc!==true){
        var t=_micStream.getAudioTracks()[0];
        pc.getSenders().forEach(function(s){if(s.track&&s.track.kind==='audio'){s.replaceTrack(t).catch(function(){});}});
      }
    }
  } else if(srcVal.indexOf('stream:')===0){
    // Build the live-relay URL directly — format: "stream:SITE:IDX"
    var parts=srcVal.split(':');
    var site=parts[1], idx=parseInt(parts[2]);
    var streamUrl=(site==='local')
      ? '/stream/'+idx+'/live'
      : '/hub/site/'+encodeURIComponent(site)+'/stream/'+idx+'/live';
    _injectStreamAudio(roomId, streamUrl);
  }
}

// ─── Livewire / output helpers ────────────────────────────────────────────────
function _lwAddr(ch){
  ch=parseInt(ch)||1;
  return '239.192.'+((ch>>8)&0xFF)+'.'+(ch&0xFF);
}
function updateLwAddr(roomId){
  var ch=document.getElementById('olw_'+roomId);
  var el=document.getElementById('olwaddr_'+roomId);
  var am=document.getElementById('omaddr_'+roomId);
  if(!ch||!el) return;
  var addr=_lwAddr(ch.value);
  el.textContent='Multicast: '+addr+' port 5004';
  if(am&&!am._customEdited) am.value=addr;
}
function setOutType(roomId, type){
  _pendingOutType[roomId]=type;   // remember selection so re-renders don't revert before Apply
  var sub=document.getElementById('outsub_'+roomId);
  if(sub) sub.classList.toggle('show', type==='livewire'||type==='multicast');
}
function toggleOutPanel(roomId){
  _outPanelOpen[roomId]=!_outPanelOpen[roomId];
  var el=document.getElementById('outp_'+roomId);
  if(el) el.style.display=_outPanelOpen[roomId]?'':'none';
}
function saveOutput(roomId){
  var typeEl=document.querySelector('input[name="ot_'+roomId+'"]:checked');
  var type=typeEl?typeEl.value:'speaker';
  var cfg={type:type};
  if(type==='livewire'||type==='multicast'){
    var ch=parseInt((document.getElementById('olw_'+roomId)||{}).value)||1;
    var addr=((document.getElementById('omaddr_'+roomId)||{}).value||_lwAddr(ch)).trim();
    var port=parseInt((document.getElementById('omport_'+roomId)||{}).value)||5004;
    var siteEl=document.getElementById('olwsite_'+roomId);
    var site=siteEl?siteEl.value:'hub';
    cfg.channel=ch; cfg.address=addr||_lwAddr(ch); cfg.port=port; cfg.site=site;
    cfg.type='livewire'; // normalise
  }
  fetch('/api/iplink/room/'+roomId+'/output',{
    method:'POST',credentials:'same-origin',headers:csrfHdr(),
    body:JSON.stringify(cfg)
  }).then(function(){
    _outPanelOpen[roomId]=false;
    delete _pendingOutType[roomId];   // saved — no longer need to override server state
    _refreshRooms();
    // If connected, start/stop capture accordingly
    if(_pcs[roomId]&&_pcs[roomId]!==true){
      if(cfg.type!=='speaker') _startOutputCapture(roomId);
      else _stopOutputCapture(roomId);
    }
  }).catch(function(){});
}

// ─── AudioWorklet PCM capture (received audio → server → Livewire multicast) ─
var _WORKLET_SRC=[
  'class IPLinkPcm extends AudioWorkletProcessor{',
  '  constructor(o){super(o);this._b=[];this._n=0;this._t=(o.processorOptions||{}).t||480;}',
  '  process(inp){var c=inp[0];if(!c||!c[0])return true;',
  '    this._b.push(c[0].slice());this._n+=c[0].length;',
  '    if(this._n>=this._t){',
  '      var o=new Float32Array(this._n),f=0;',
  '      this._b.forEach(function(b){o.set(b,f);f+=b.length;});',
  '      this.port.postMessage(o.buffer,[o.buffer]);',
  '      this._b=[];this._n=0;',
  '    }return true;}',
  '}registerProcessor("iplink-pcm",IPLinkPcm);',
].join('');

function _getWorkletUrl(){
  if(!_lwWorkletUrl)
    _lwWorkletUrl=URL.createObjectURL(new Blob([_WORKLET_SRC],{type:'application/javascript'}));
  return _lwWorkletUrl;
}

function _startOutputCapture(roomId){
  _stopOutputCapture(roomId);
  var rCtx=_recvCtx[roomId], rGain=_recvGain[roomId];
  if(!rCtx||!rGain) return;
  rCtx.audioWorklet.addModule(_getWorkletUrl()).then(function(){
    var node=new AudioWorkletNode(rCtx,'iplink-pcm',{processorOptions:{t:480}});
    rGain.connect(node);
    _outputCapture[roomId]={node:node};
    node.port.onmessage=function(e){
      var f32=new Float32Array(e.data);
      var s16=new Int16Array(f32.length);
      for(var i=0;i<f32.length;i++){var v=Math.max(-1,Math.min(1,f32[i]));s16[i]=v<0?v*32768:v*32767;}
      fetch('/api/iplink/room/'+roomId+'/output_chunk',{
        method:'POST',credentials:'same-origin',
        headers:{'Content-Type':'application/octet-stream'},
        body:new Uint8Array(s16.buffer),
      }).catch(function(){});
    };
  }).catch(function(e){console.warn('[IPLink] AudioWorklet failed:',e);});
}
function _stopOutputCapture(roomId){
  var c=_outputCapture[roomId];
  if(c){try{c.node.disconnect();}catch(e){}delete _outputCapture[roomId];}
}

// ─── Mic acquisition (shared across all rooms) ───────────────────────────────
function _getHubMic(cb){
  if(_micStream){ cb(null, _micStream); return; }
  navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true},video:false})
    .then(function(s){ _micStream=s; cb(null,s); })
    .catch(function(e){ cb(e,null); });
}

// ─── SDP munging ─────────────────────────────────────────────────────────────
// (No browser detection needed — SDP munge is applied unconditionally.)

// SDP compatibility shim — applied unconditionally to ALL incoming offers.
// Both Chrome M130+ and Safari reject certain lines in raw WebRTC offers:
//
//  - a=ssrc / a=ssrc-group       Chrome M130+ rejects the deprecated two-ID
//                                "msid:STREAM TRACK" format; Safari rejects all ssrc
//  - a=extmap-allow-mixed        Safari WebKit rejects and MISREPORTS as later lines
//  - a=extmap:N/direction        direction specifiers; Safari rejects
//  - a=rtcp-rsize                Safari rejects
//  - a=rtcp-fb: transport-cc     Safari rejects; optional — call works without it
//  - telephone-event / CN / RED  not needed for contribution
//  - PCMA / PCMU / static PTs    explicit rtpmap confuses parsers
//  - rtx / ulpfec / flexfec      not needed
// Orphan guard: fmtp without a matching rtpmap stripped (would cause parse error).
// ─── SDP normalisation ───────────────────────────────────────────────────────
// Room calls: both ends are WebRTC browsers — the raw offer is passed unchanged
//   to setRemoteDescription().  The server ensures the SDP ends with \r\n so
//   Chrome's line-oriented parser doesn't fail on an unterminated last line.
//   No JS manipulation; any rewriting risks making m= / a=fmtp PT references
//   inconsistent.
//
// SIP calls: the hub's own Chrome offer contains WebRTC-specific extension lines
//   that many SIP servers reject.  _sipMungeSdp strips only those attribute
//   lines — no m= rewriting, no codec removal.
function _sipMungeSdp(sdp){
  var DROP = [
    /^a=ssrc(-group)?:/,
    /^a=extmap-allow-mixed\s*$/,
    /^a=rtcp-rsize\s*$/,
    /^a=rtcp-fb:/,
  ];
  var out = sdp.split(/\r?\n/).filter(function(line){
    return !DROP.some(function(p){ return p.test(line); });
  }).map(function(line){
    // Normalise a=extmap:N/direction URI → a=extmap:N URI
    return line.replace(/^(a=extmap:\d+)\/(?:sendrecv|sendonly|recvonly|inactive)\b/, '$1');
  }).join('\r\n');
  // Ensure SDP ends with \r\n — required by RFC 4566; without it the SIP
  // server's Content-Length parsing may misread the body boundary.
  if(!out.endsWith('\r\n')) out += '\r\n';
  return out;
}

// ─── Hub WebRTC negotiation ──────────────────────────────────────────────────
function _showConnErr(roomId, msg){
  _connErrs[roomId] = msg;
  // Update the pre-rendered error div in-place — avoids a full re-render that
  // would rebuild the source selector and could lose the user's dropdown state.
  var el = document.getElementById('cerr_'+roomId);
  if(el){ el.textContent = '⚠ '+msg; el.style.display = ''; }
  else { _refreshRooms(); }   // fallback if card not yet rendered
  setTimeout(function(){
    delete _connErrs[roomId];
    var el2 = document.getElementById('cerr_'+roomId);
    if(el2) el2.style.display = 'none';
  }, 10000);
}

function acceptCall(roomId){
  if(_pcs[roomId]) return;   // already connecting, ignore double-click
  _ensureAudioCtx(); // pre-activate shared AudioContext while still in user-gesture
  // Mark as connecting immediately — _renderRooms checks this every 1.5 s
  // and will show "Connecting…" rather than re-rendering the Accept button.
  _pcs[roomId] = true;
  var btn = document.getElementById('accept_'+roomId);
  if(btn){ btn.disabled=true; btn.textContent='⏳ Connecting…'; }

  _getHubMic(function(err, micStream){
    if(err){
      alert('Microphone access denied: '+err.message+'\nYou can still receive audio but cannot send IFB/talkback.');
    }
    // Fetch the talent's offer
    fetch('/api/iplink/room/'+roomId+'/offer', {credentials:'same-origin'})
      .then(function(r){ return r.json(); })
      .then(function(d){
        if(!d.offer){ delete _pcs[roomId]; _showConnErr(roomId,'No offer found — talent may have disconnected. Reset the room and ask them to reconnect.'); return; }
        var pc = new RTCPeerConnection({iceServers: STUN.map(function(u){return{urls:u};})});
        _pcs[roomId] = pc;  // replace truthy placeholder with actual PC
        _iceIdx[roomId] = {sent:0, consumed:0};

        // Add mic track (IFB return to talent)
        if(micStream){
          micStream.getTracks().forEach(function(t){ pc.addTrack(t, micStream); });
        }

        // Receive talent audio — route through gain node for recv fader
        pc.ontrack = function(e){
          if(!e.streams[0]) return;
          var audio = document.getElementById('hubAudio');
          try{
            var rCtx=new(window.AudioContext||window.webkitAudioContext)();
            var rSrc=rCtx.createMediaStreamSource(e.streams[0]);
            var rGain=rCtx.createGain();
            rGain.gain.value=(_recvVol[roomId]||100)/100;
            _recvGain[roomId]=rGain; _recvCtx[roomId]=rCtx;
            var rDest=rCtx.createMediaStreamDestination();
            rSrc.connect(rGain); rGain.connect(rDest);
            if(audio) audio.srcObject=rDest.stream;
            _setupHubMeter(roomId, e.streams[0]);
            // Start Livewire capture if room is configured for multicast output
            fetch('/api/iplink/rooms',{credentials:'same-origin'})
              .then(function(r){return r.json();})
              .then(function(d){
                var rm=(d.rooms||[]).find(function(x){return x.id===roomId;});
                if(rm&&rm.output&&rm.output.type!=='speaker') _startOutputCapture(roomId);
              }).catch(function(){});
          }catch(ex){
            if(audio) audio.srcObject=e.streams[0];
            _setupHubMeter(roomId, e.streams[0]);
          }
        };

        // Collect ICE candidates
        pc.onicecandidate = function(e){
          if(!e.candidate) return;
          fetch('/api/iplink/room/'+roomId+'/ice', {
            method:'POST', credentials:'same-origin',
            headers:csrfHdr(),
            body:JSON.stringify({from:'hub', candidate:e.candidate})
          });
        };

        pc.onconnectionstatechange = function(){
          _refreshRooms();
          if(pc.connectionState === 'failed'){
            delete _pcs[roomId];
            fetch('/api/iplink/room/'+roomId+'/status',{method:'POST',credentials:'same-origin',headers:csrfHdr(),body:JSON.stringify({from:'hub',status:'disconnected'})});
          } else if(pc.connectionState === 'disconnected'){
            // Transient — don't tear down yet; ICE may recover
            fetch('/api/iplink/room/'+roomId+'/status',{method:'POST',credentials:'same-origin',headers:csrfHdr(),body:JSON.stringify({from:'hub',status:'disconnected'})});
          }
        };

        // Pass the talent's raw offer directly to setRemoteDescription.
        // Both ends are WebRTC browsers; they negotiate codecs natively.
        // The server guarantees the SDP ends with \r\n so Chrome's parser
        // doesn't fail on an unterminated last line.
        pc.setRemoteDescription({type:'offer', sdp:d.offer})
          .then(function(){ return pc.createAnswer(); })
          .then(function(ans){
            return pc.setLocalDescription(ans).then(function(){ return ans; });
          })
          .then(function(ans){
            return fetch('/api/iplink/room/'+roomId+'/answer',{
              method:'POST', credentials:'same-origin',
              headers:csrfHdr(),
              body:JSON.stringify({answer:ans.sdp})
            });
          })
          .then(function(){
            // Start polling for talent ICE candidates
            _pollTalentIce(roomId);
            // Apply quality settings
            _applyQuality(pc, roomId);
            // Apply selected audio source (mic or stream feed)
            _applySourceForRoom(roomId);
            // Wire up send gain node for mic path
            if(_micStream){
              try{
                var sCtx=new(window.AudioContext||window.webkitAudioContext)();
                var sSrc=sCtx.createMediaStreamSource(_micStream);
                var sGain=sCtx.createGain();
                sGain.gain.value=(_sendVol[roomId]||100)/100;
                _sendGain[roomId]=sGain;
                // Note: track already added; gain affects monitoring level display only
                // (track.enabled handles mute; replaceTrack handles source switching)
              }catch(ex){}
            }
          })
          .catch(function(e){
            console.error('[IPLink] setRemoteDescription error:', e.message);
            console.debug('[IPLink] raw offer SDP:\n', d.offer);
            delete _pcs[roomId];
            _showConnErr(roomId, 'WebRTC failed: '+(e.message||String(e)));
          });
      })
      .catch(function(e){
        // Outer catch: fetch/JSON errors on the offer request
        console.error('Hub offer fetch error:', e);
        delete _pcs[roomId];
        _showConnErr(roomId, 'Could not fetch offer: '+(e.message||String(e)));
      });
  });
}

function _applyQuality(pc, roomId){
  // Set Opus bitrate via RTCRtpSender.setParameters
  fetch('/api/iplink/rooms',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      var room = (d.rooms||[]).find(function(r){return r.id===roomId;});
      if(!room) return;
      var q = {voice:{maxBitrate:64000},broadcast:{maxBitrate:128000},hifi:{maxBitrate:256000}}[room.quality]||{maxBitrate:128000};
      pc.getSenders().forEach(function(s){
        if(s.track && s.track.kind==='audio'){
          var p = s.getParameters();
          if(!p.encodings) p.encodings=[{}];
          p.encodings[0].maxBitrate = q.maxBitrate;
          s.setParameters(p).catch(function(){});
        }
      });
    });
}

function _pollTalentIce(roomId){
  var idx = (_iceIdx[roomId]||{}).consumed || 0;
  fetch('/api/iplink/room/'+roomId+'/ice?from=talent&from_idx='+idx, {credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      var pc = _pcs[roomId];
      if(!pc || pc.connectionState==='closed') return;
      (d.candidates||[]).forEach(function(c){
        pc.addIceCandidate(c).catch(function(){});
      });
      if(_iceIdx[roomId]) _iceIdx[roomId].consumed = d.next_idx || idx;
      // Keep polling while connecting
      if(pc.connectionState !== 'connected' && pc.connectionState !== 'closed'){
        setTimeout(function(){ _pollTalentIce(roomId); }, 600);
      }
    })
    .catch(function(){ setTimeout(function(){ _pollTalentIce(roomId); }, 2000); });
}

// ─── Level meters ────────────────────────────────────────────────────────────
function _setupHubMeter(roomId, stream){
  try{
    var ctx = new(window.AudioContext||window.webkitAudioContext)();
    var src = ctx.createMediaStreamSource(stream);
    var an  = ctx.createAnalyser();
    an.fftSize = 512;
    src.connect(an);
    _levels[roomId] = an;
    var buf = new Uint8Array(an.frequencyBinCount);
    function tick(){
      an.getByteTimeDomainData(buf);
      var sq=0; for(var i=0;i<buf.length;i++){var s=(buf[i]-128)/128;sq+=s*s;}
      var rms = Math.sqrt(sq/buf.length);
      var el = document.getElementById('rc_tlvl_'+roomId);
      if(el){ el.style.width=(Math.min(rms*4,1)*100)+'%'; }
      requestAnimationFrame(tick);
    }
    tick();
  } catch(e){}
}

// ─── Mute toggle ─────────────────────────────────────────────────────────────
function toggleMute(roomId){
  fetch('/api/iplink/room/'+roomId+'/mute',{method:'POST',credentials:'same-origin',headers:csrfHdr()});
  if(_micStream){
    _micStream.getAudioTracks().forEach(function(t){t.enabled=!t.enabled;});
  }
}

// ─── Disconnect ──────────────────────────────────────────────────────────────
function disconnectRoom(roomId){
  var pc=_pcs[roomId];
  if(pc){ pc.close(); delete _pcs[roomId]; }
  _stopFeed(roomId);
  _stopOutputCapture(roomId);
  delete _sendGain[roomId]; delete _recvGain[roomId];
  try{ if(_recvCtx[roomId]) _recvCtx[roomId].close(); }catch(e){} delete _recvCtx[roomId];
  delete _onAir[roomId];
  if(_sett.muteHangup && _micStream){
    _micStream.getAudioTracks().forEach(function(t){t.enabled=false;});
  }
  fetch('/api/iplink/room/'+roomId+'/status',{method:'POST',credentials:'same-origin',headers:csrfHdr(),body:JSON.stringify({from:'hub',status:'disconnected'})});
}

function deleteRoom(roomId){
  if(!confirm('Delete this room and its shareable link?')) return;
  disconnectRoom(roomId);
  fetch('/api/iplink/room/'+roomId,{method:'DELETE',credentials:'same-origin',headers:csrfHdr()})
    .then(function(){ _refreshRooms(); });
}

function resetRoom(roomId){
  disconnectRoom(roomId);
  fetch('/api/iplink/room/'+roomId+'/reset',{method:'POST',credentials:'same-origin',headers:csrfHdr()})
    .then(function(){ _refreshRooms(); });
}

// ─── Copy link ───────────────────────────────────────────────────────────────
function copyLink(roomId, flashId){
  var url = BASE+'/iplink/join/'+roomId;
  navigator.clipboard.writeText(url).then(function(){
    var el=document.getElementById(flashId);
    if(el){el.classList.add('show');setTimeout(function(){el.classList.remove('show');},2000);}
  }).catch(function(){prompt('Copy this link:',url);});
}

// ─── Room list rendering ─────────────────────────────────────────────────────
function _statusBadge(status){
  var map={
    waiting:    '<span class="badge b-mu">⏳ Waiting for caller</span>',
    offer_received: '<span class="badge b-wn">📲 Incoming call…</span>',
    connected:  '<span class="badge b-ok">🔴 Live</span>',
    disconnected:'<span class="badge b-al">✗ Disconnected</span>',
  };
  return map[status]||('<span class="badge b-mu">'+status+'</span>');
}

function _fmt(s){
  if(!s) return '—';
  var m=Math.floor(s/60), sec=s%60;
  return (m>0?m+'m ':'')+sec+'s';
}

var _roomsData = {};  // room_id → room object from last poll (for server_managed checks)

function _renderRooms(rooms){
  var ng=document.getElementById('roomGrid');
  var np=document.getElementById('noRooms');
  if(!rooms.length){ ng.innerHTML=''; np.style.display=''; _roomsData={}; return; }
  np.style.display='none';
  // Update room data cache (used by _onRoomSrcChange to decide server-side save)
  _roomsData={};
  rooms.forEach(function(r){ _roomsData[r.id]=r; });
  var html='<div class="room-grid">';
  rooms.forEach(function(r){
    var cls='room-card';
    if(r.status==='connected') cls+=' rc-connected';
    else if(r.status==='offer_received') cls+=' rc-offer';
    var tlvlW = Math.round(r.talent_level*100);
    var hlvlW  = Math.round(r.hub_level*100);
    var talentLvlCol = r.talent_level>0.7?'var(--al)':r.talent_level>0.3?'var(--ok)':'var(--ok)';
    html+='<div class="'+cls+'" id="rc_'+r.id+'">';
    // Header
    html+='<div class="rc-hdr">';
    html+='<span style="font-size:18px">🎙</span>';
    html+='<span class="rc-name" title="'+r.name+'">'+_esc(r.name)+'</span>';
    if(r.permanent) html+='<span class="rc-perm" title="Permanent room">📌</span>';
    // Server-managed badge (permanent rooms only)
    if(r.permanent && r.server_managed){
      var _smBadge = r.server_routing_active
        ? '<span class="rc-sm-badge rc-sm-on" title="Server routing active">🖥 Server</span>'
        : '<span class="rc-sm-badge rc-sm-idle" title="Server routing enabled, waiting for source">🖥 Idle</span>';
      html += _smBadge;
    }
    var out=r.output||{};
    if(out.type&&out.type!=='speaker'){
      var _lbl=out.type==='livewire'?'LW ch '+out.channel:'AES67';
      if(out.site&&out.site!=='hub') _lbl+=' @ '+out.site;
      html+='<span class="lw-badge">📡 '+_esc(_lbl)+'</span>';
    }
    html+=_statusBadge(r.status);
    html+='</div>';
    // Body
    html+='<div class="rc-body">';
    // Accept banner — show Connecting… if a PeerConnection already exists for this room.
    // For permanent server-managed rooms: server auto-accepts; don't show the Accept button.
    if(r.status==='offer_received'){
      html+='<div class="accept-banner">📲 <span style="flex:1">Incoming call from contributor</span>';
      if(r.permanent && r.server_managed){
        html+='<button class="btn bp bs" disabled>🖥 Auto-accepting…</button>';
      } else if(_pcs[r.id]){
        html+='<button class="btn bp bs" disabled>⏳ Connecting…</button>';
      } else {
        html+='<button class="btn bp bs" id="accept_'+r.id+'" onclick="acceptCall(\''+r.id+'\')">✅ Accept</button>';
      }
      html+='</div>';
    }
    // Pre-rendered error div — updated in-place by _showConnErr to avoid re-render flickering
    var _cerr = _connErrs[r.id] || '';
    html+='<div id="cerr_'+r.id+'" class="msg msg-err" style="margin:8px 0 0'+(_cerr?'':';display:none')+'">'+(_cerr?'⚠ '+_esc(_cerr):'')+'</div>';
    // Level meters
    html+='<div class="lvl-wrap"><span class="lvl-label" style="font-size:10px">Talent</span>';
    html+='<div class="lvl-outer"><div class="lvl-fill" id="rc_tlvl_'+r.id+'" style="width:'+tlvlW+'%;background:'+talentLvlCol+'"></div></div>';
    html+='<span class="lvl-val" style="color:'+talentLvlCol+'">'+(r.talent_level>0?Math.round(r.talent_level*100)+'%':'—')+'</span></div>';
    html+='<div class="lvl-wrap"><span class="lvl-label" style="font-size:10px">Hub</span>';
    html+='<div class="lvl-outer"><div class="lvl-fill" id="rc_hlvl_'+r.id+'" style="width:'+hlvlW+'%;background:'+(r.hub_muted?'var(--mu)':'var(--acc)')+'"></div></div>';
    html+='<span class="lvl-val" style="color:var(--mu)">'+(r.hub_muted?'🔇':'')+(r.hub_level>0?Math.round(r.hub_level*100)+'%':'—')+'</span></div>';
    // Stats rows
    html+='<div class="rc-row"><span class="rc-lbl">Quality</span><span>'+_esc(r.quality_label)+'</span></div>';
    if(r.status==='connected' && r.duration_s!=null){
      html+='<div class="rc-row"><span class="rc-lbl">Duration</span><span>'+_fmt(r.duration_s)+'</span></div>';
    }
    if(r.talent_ip){ html+='<div class="rc-row"><span class="rc-lbl">Caller IP</span><span style="font-size:11px">'+_esc(r.talent_ip)+'</span></div>'; }
    if(r.stats && r.stats.rtt_ms!=null){
      html+='<div class="rc-row"><span class="rc-lbl">RTT</span><span style="color:'+(r.stats.rtt_ms>100?'var(--wn)':'var(--ok)')+'">'+r.stats.rtt_ms+' ms</span></div>';
    }
    if(r.stats && r.stats.loss_pct!=null){
      html+='<div class="rc-row"><span class="rc-lbl">Packet loss</span><span style="color:'+(r.stats.loss_pct>1?'var(--al)':r.stats.loss_pct>0?'var(--wn)':'var(--ok)')+'">'+r.stats.loss_pct+'%</span></div>';
    }
    // Faders (only when connected)
    if(r.status==='connected'){
      html+='<div class="fader-row">';
      html+='<span class="fader-lbl">Send</span>';
      html+='<input type="range" class="fader" id="rc_sendvol_'+r.id+'" min="0" max="200" value="'+((_sendVol[r.id]!=null)?_sendVol[r.id]:100)+'" oninput="setSendVol(\''+r.id+'\',this.value)">';
      html+='<span class="fader-val" id="rc_sendvolval_'+r.id+'">'+((_sendVol[r.id]!=null)?_sendVol[r.id]:100)+'%</span>';
      html+='</div>';
      html+='<div class="fader-row">';
      html+='<span class="fader-lbl">Recv</span>';
      html+='<input type="range" class="fader" id="rc_recvvol_'+r.id+'" min="0" max="200" value="'+((_recvVol[r.id]!=null)?_recvVol[r.id]:100)+'" oninput="setRecvVol(\''+r.id+'\',this.value)">';
      html+='<span class="fader-val" id="rc_recvvolval_'+r.id+'">'+((_recvVol[r.id]!=null)?_recvVol[r.id]:100)+'%</span>';
      html+='</div>';
    }
    // ── Per-room audio source selector ────────────────────────────────────────
    // Stream options are inlined at render time from _streamSources so the
    // correct selected= attribute is baked in. No post-render population step
    // needed; the dropdown never resets to "global" on the 1.5 s re-render.
    var _curSrc=_roomSrc[r.id]||'global';
    html+='<div style="margin:8px 0 4px"><label style="font-size:10px;color:var(--mu);font-weight:600;text-transform:uppercase;letter-spacing:.05em">Audio Source</label>';
    html+='<select id="rc_src_'+r.id+'" class="src-sel" onchange="_onRoomSrcChange(\''+r.id+'\',this.value)" style="margin-top:4px">';
    html+='<option value="global"'+(_curSrc==='global'?' selected':'')+'>↑ Default source</option>';
    html+='<option value="mic"'+(_curSrc==='mic'?' selected':'')+'>🎤 Hub Microphone</option>';
    _streamSources.forEach(function(s){
      var _sv='stream:'+(s.site||'local')+':'+s.idx;
      html+='<option value="'+_esc(_sv)+'"'+(_sv===_curSrc?' selected':'')+'>'+(_esc(s.active?'🟢 ':'⚪ '))+_esc(s.name)+(s.stereo?' · stereo':'')+'</option>';
    });
    html+='</select>';
    // IFB source status badge — shows current selection state below the dropdown
    var _rsv=_roomSrc[r.id]||'';
    var _rsLbl=_rsv&&_rsv!=='global'&&_rsv!=='mic'?_srcLabel(_rsv)||_rsv:'';
    var _connected=!!(_pcs[r.id]&&_pcs[r.id]!==true);
    var _feedActive=!!_feedAudio[r.id];
    if(_rsLbl){
      // Badge states:
      //   feed active + PC active  → green  "sending to talent"
      //   feed active + no PC      → muted  "ready, waiting for call"
      //   no feed yet              → muted  "loading…"
      var _badgeCls='rc-src-badge'+(_feedActive&&_connected?' rc-src-active':' rc-src-pending');
      var _badgeTxt=_feedActive
        ? (_connected ? _rsLbl+' — sending to talent' : _rsLbl+' — ready, waiting for call')
        : _rsLbl+' — loading\u2026';
      html+='<div id="rc_srcbadge_'+r.id+'" class="'+_badgeCls+'">'+_esc(_badgeTxt)+'</div>';
    } else {
      html+='<div id="rc_srcbadge_'+r.id+'" class="rc-src-badge" style="display:none"></div>';
    }
    html+='</div>';

    // ── Server-managed routing panel (permanent rooms only) ──────────────────
    if(r.permanent){
      var _smOn = !!r.server_managed;
      var _smSrc = r.source;
      var _smSrcLabel = _smSrc ? (_smSrc.name || (_smSrc.site==='local'?'Local':'Remote')+' ['+_smSrc.idx+']') : 'None';
      html+='<div style="margin:6px 0 2px;border:1px solid var(--bor);border-radius:8px;padding:10px;background:rgba(23,52,95,.18)">';
      html+='<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">';
      html+='<span style="font-size:11px;color:var(--mu);font-weight:700;text-transform:uppercase;letter-spacing:.05em;flex:1">🖥 Server Routing</span>';
      html+='<button class="btn bs '+(r.server_managed?'bp':'bg')+'" onclick="toggleServerManaged(\''+r.id+'\')">'+(r.server_managed?'On':'Off')+'</button>';
      html+='</div>';
      if(_smOn){
        html+='<div style="font-size:11px;color:var(--mu);margin-bottom:4px">Source: <span style="color:var(--tx)">'+_esc(_smSrcLabel)+'</span></div>';
        html+='<div style="font-size:10px;color:var(--mu);margin-bottom:6px">';
        if(r.server_routing_active){
          html+='<span style="color:var(--ok)">● Active</span> — audio routing server-side';
        } else if(_smSrc){
          html+='<span style="color:var(--wn)">⏳ Starting…</span>';
        } else {
          html+='<span style="color:var(--mu)">No source configured — select below and save</span>';
        }
        html+='</div>';
      }
      // Source selector for server-managed rooms: always show + a save button
      html+='<div style="display:flex;gap:6px;align-items:flex-end">';
      html+='<div style="flex:1"><label style="font-size:10px;color:var(--mu);font-weight:600;text-transform:uppercase;letter-spacing:.05em">Server Source</label>';
      html+='<select id="smSrc_'+r.id+'" class="src-sel" style="margin-top:3px">';
      html+='<option value="none">None</option>';
      _streamSources.forEach(function(s){
        var _sv='stream:'+(s.site||'local')+':'+s.idx;
        var _sel=_smSrc&&_smSrc.site===(s.site||'local')&&_smSrc.idx===s.idx?' selected':'';
        html+='<option value="'+_esc(_sv)+'"'+_sel+'>'+(s.active?'🟢 ':'⚪ ')+_esc(s.name)+'</option>';
      });
      html+='</select></div>';
      html+='<button class="btn bp bs" onclick="saveServerSource(\''+r.id+'\')" style="margin-bottom:1px">💾 Save</button>';
      html+='</div>';
      html+='</div>';
    }

    // Output config panel
    var outCfg=r.output||{};
    var outOpen=!!_outPanelOpen[r.id];
    html+='<div style="margin:6px 0 2px">';
    html+='<button class="btn bg bs" onclick="toggleOutPanel(\''+r.id+'\')" style="width:100%;justify-content:center">📡 Output: '+(outCfg.type==='livewire'?'Livewire ch '+outCfg.channel:outCfg.type==='multicast'?'AES67 '+_esc(outCfg.address||''):'Speaker')+'</button>';
    html+='<div id="outp_'+r.id+'" class="out-panel" style="'+(outOpen?'':'display:none')+'">';
    html+='<div style="font-size:11px;color:var(--mu);font-weight:600;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">Audio Output</div>';
    // Use _pendingOutType (user changed radio but hasn't clicked Apply yet) to keep
    // the selection stable across the 1.5 s _renderRooms re-render cycle.
    var _dispOut=_pendingOutType[r.id]||(outCfg.type||'speaker');
    html+='<label><input type="radio" name="ot_'+r.id+'" value="speaker"'+(_dispOut==='speaker'?' checked':'')+' onchange="setOutType(\''+r.id+'\',this.value)"> 🔊 Speaker (browser)</label>';
    html+='<label><input type="radio" name="ot_'+r.id+'" value="livewire"'+(_dispOut==='livewire'?' checked':'')+' onchange="setOutType(\''+r.id+'\',this.value)"> 📡 Livewire / AES67 Multicast</label>';
    // Livewire sub-fields
    html+='<div class="out-sub'+((_dispOut==='livewire'||_dispOut==='multicast')?' show':'')+'" id="outsub_'+r.id+'">';
    // Site selector — "Hub (local multicast)" or any connected client site
    html+='<div class="field" style="margin-bottom:6px"><label style="font-size:10px">Output Site</label>';
    html+='<select id="olwsite_'+r.id+'">';
    var _curSite=(outCfg.site||'hub');
    _lwSites.forEach(function(s){
      html+='<option value="'+_esc(s.id)+'"'+(_curSite===s.id?' selected':'')+'>'+_esc(s.name)+'</option>';
    });
    html+='</select></div>';
    html+='<div class="field" style="margin-bottom:6px"><label style="font-size:10px">Livewire Channel (1–32767)</label>';
    html+='<input type="number" id="olw_'+r.id+'" min="1" max="32767" value="'+(outCfg.channel||1)+'" oninput="updateLwAddr(\''+r.id+'\')"></div>';
    html+='<div class="lw-addr" id="olwaddr_'+r.id+'">Multicast: '+_lwAddr(outCfg.channel||1)+' port 5004</div>';
    html+='<details style="margin-top:8px"><summary style="font-size:11px;color:var(--mu);cursor:pointer">Custom address (AES67)</summary>';
    html+='<div class="field" style="margin-top:6px;margin-bottom:4px"><label style="font-size:10px">Multicast Address</label>';
    html+='<input type="text" id="omaddr_'+r.id+'" placeholder="239.192.x.x" value="'+(outCfg.address||_lwAddr(outCfg.channel||1))+'"></div>';
    html+='<div class="field" style="margin-bottom:4px"><label style="font-size:10px">Port</label>';
    html+='<input type="number" id="omport_'+r.id+'" min="1" max="65535" value="'+(outCfg.port||5004)+'"></div>';
    html+='</details>';
    html+='</div>';
    html+='<div style="margin-top:8px;display:flex;gap:6px">';
    html+='<button class="btn bp bs" onclick="saveOutput(\''+r.id+'\')">💾 Apply</button>';
    html+='<button class="btn bg bs" onclick="toggleOutPanel(\''+r.id+'\')">Close</button>';
    html+='</div></div></div>';
    // Actions
    html+='<div class="actions">';
    html+='<button class="btn bg bs" onclick="copyLink(\''+r.id+'\',\'flash_'+r.id+'\')">🔗 Copy Link</button>';
    html+='<span id="flash_'+r.id+'" class="copy-flash">Copied!</span>';
    if(r.status==='connected'){
      var onAirActive = !!_onAir[r.id];
      html+='<button class="btn btn-onair bs'+(onAirActive?' active':'')+'" onclick="toggleOnAir(\''+r.id+'\')">'+(onAirActive?'🔴 On Air':'⭕ On Air')+'</button>';
      html+='<button class="btn bg bs" onclick="toggleMute(\''+r.id+'\')">'+( r.hub_muted?'🔊 Unmute IFB':'🔇 Mute IFB')+'</button>';
      html+='<button class="btn bd bs" onclick="disconnectRoom(\''+r.id+'\')">Disconnect</button>';
    } else if(r.status==='disconnected'||r.status==='offer_received'){
      html+='<button class="btn bg bs" onclick="resetRoom(\''+r.id+'\')">↻ Reset</button>';
    }
    html+='<button class="btn bd bs" onclick="deleteRoom(\''+r.id+'\')" style="margin-left:auto">🗑 Delete</button>';
    html+='</div>';
    html+='</div></div>'; // rc-body, room-card
  });
  html+='</div>';
  // Don't rebuild the room grid while a per-room source <select> is open —
  // replacing innerHTML destroys the open dropdown and Chrome closes it immediately.
  if(_srcSelOpen){
    // The render was skipped, but _roomSrc[id] is already updated by _onRoomSrcChange.
    // Patch each visible select's value directly so the selection is visually correct
    // without a full rebuild.
    rooms.forEach(function(r){
      var sel=document.getElementById('rc_src_'+r.id);
      if(sel&&!sel.matches(':focus')){
        var want=_roomSrc[r.id]||'global';
        if(sel.value!==want) sel.value=want;
      }
    });
    return;
  }
  ng.innerHTML=html;
  // After injecting new DOM, wire up focus/blur on each per-room source <select>
  // so _srcSelOpen tracks whether ANY dropdown is currently open.
  // Wire _srcSelOpen guard on BOTH the per-room source select AND the server-source
  // select — any open dropdown must block the 1.5 s innerHTML rebuild that would close it.
  function _wireSel(sel){
    if(!sel) return;
    sel.addEventListener('mousedown',function(){ _srcSelOpen=true; });
    sel.addEventListener('focus',    function(){ _srcSelOpen=true; });
    sel.addEventListener('blur',     function(){ _srcSelOpen=false; });
    sel.addEventListener('change',   function(){ _srcSelOpen=false; });
    // Escape/Enter may not fire blur in all browsers:
    sel.addEventListener('keydown',  function(e){ if(e.key==='Escape'||e.key==='Enter') _srcSelOpen=false; });
  }
  rooms.forEach(function(r){
    _wireSel(document.getElementById('rc_src_'+r.id));
    _wireSel(document.getElementById('smSrc_'+r.id));
  });
  // Populate stream options on the newly-created global selector
  _populateSourceSelectors();
}

function _esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

// ─── Room list polling ────────────────────────────────────────────────────────
function _refreshRooms(){
  fetch('/api/iplink/rooms',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      var rooms=d.rooms||[];
      // Detect new offer_received rooms for ringtone + auto-accept
      var anyOffer=false;
      rooms.forEach(function(r){
        var wasOffer=_prevOfferRooms[r.id];
        var isOffer=r.status==='offer_received';
        if(isOffer){
          anyOffer=true;
          if(!wasOffer){
            // Newly arrived call
            if(_sett.autoAccept && !_pcs[r.id]) acceptCall(r.id);
          }
        }
        _prevOfferRooms[r.id]=isOffer;
      });
      if(anyOffer){ _startRingtone(); } else { _stopRingtone(); }
      _renderRooms(rooms);
    })
    .catch(function(){});
}
_loadSett();
_refreshRooms();
setInterval(_refreshRooms, 1500);

// ─── Hub-side level reporting ─────────────────────────────────────────────────
setInterval(function(){
  Object.keys(_pcs).forEach(function(roomId){
    var an=_levels[roomId];
    if(!an) return;
    var buf=new Uint8Array(an.frequencyBinCount);
    an.getByteTimeDomainData(buf);
    var sq=0; for(var i=0;i<buf.length;i++){var s=(buf[i]-128)/128;sq+=s*s;}
    var rms=Math.sqrt(sq/buf.length);
    fetch('/api/iplink/room/'+roomId+'/level',{method:'POST',credentials:'same-origin',headers:csrfHdr(),body:JSON.stringify({from:'hub',level:rms})});
  });
}, 300);

// ─── Create room form ─────────────────────────────────────────────────────────
document.getElementById('createBtn').addEventListener('click',function(){
  document.getElementById('createPanel').style.display='';
  document.getElementById('cName').focus();
});
document.getElementById('createCancel').addEventListener('click',function(){
  document.getElementById('createPanel').style.display='none';
  document.getElementById('createMsg').innerHTML='';
});
document.getElementById('createSubmit').addEventListener('click',function(){
  var name=(document.getElementById('cName').value||'').trim();
  var quality=document.getElementById('cQuality').value;
  if(!name){ document.getElementById('createMsg').innerHTML='<div class="msg msg-err">Enter a room name</div>'; return; }
  var permanent=document.getElementById('cPermanent').checked;
  fetch('/api/iplink/rooms',{method:'POST',credentials:'same-origin',
    headers:csrfHdr(), body:JSON.stringify({name:name,quality:quality,permanent:permanent})})
    .then(function(r){return r.json();})
    .then(function(d){
      if(d.error){document.getElementById('createMsg').innerHTML='<div class="msg msg-err">'+_esc(d.error)+'</div>'; return;}
      document.getElementById('createPanel').style.display='none';
      document.getElementById('cName').value='';
      document.getElementById('createMsg').innerHTML='';
      _refreshRooms();
      // Auto-copy link
      navigator.clipboard.writeText(BASE+'/iplink/join/'+d.room.id).catch(function(){});
    })
    .catch(function(e){ document.getElementById('createMsg').innerHTML='<div class="msg msg-err">'+e+'</div>'; });
});
document.getElementById('cName').addEventListener('keydown',function(e){if(e.key==='Enter')document.getElementById('createSubmit').click();});

// ════════════════════════════════════════════════════════════════════════════
// SIP CLIENT  (pure JS over WebSocket, no dependencies)
// ════════════════════════════════════════════════════════════════════════════

// ── Compact MD5 (RFC 1321) — required for SIP digest auth ──────────────────
function _md5(s){
  function ad(x,y){var l=(x&0xffff)+(y&0xffff);var m=(x>>16)+(y>>16)+(l>>16);return(m<<16)|(l&0xffff)}
  function rl(n,c){return(n<<c)|(n>>>(32-c))}
  function cm(q,a,b,x,s,t){return ad(rl(ad(ad(a,q),ad(x,t)),s),b)}
  function ff(a,b,c,d,x,s,t){return cm((b&c)|(~b&d),a,b,x,s,t)}
  function gg(a,b,c,d,x,s,t){return cm((b&d)|(c&~d),a,b,x,s,t)}
  function hh(a,b,c,d,x,s,t){return cm(b^c^d,a,b,x,s,t)}
  function ii(a,b,c,d,x,s,t){return cm(c^(b|~d),a,b,x,s,t)}
  function sb(x){var i,n=((x.length+8)>>6)+1,b=new Array(n*16).fill(0);for(i=0;i<x.length;i++)b[i>>2]|=x.charCodeAt(i)<<((i%4)*8);b[x.length>>2]|=0x80<<((x.length%4)*8);b[n*16-2]=x.length*8;return b}
  function rh(n){var hc='0123456789abcdef',j,s='';for(j=0;j<=3;j++)s+=hc[(n>>(j*8+4))&0xf]+hc[(n>>(j*8))&0xf];return s}
  var i,x=sb(s),a=1732584193,b=-271733879,c=-1732584194,d=271733878,oa,ob,oc,od;
  for(i=0;i<x.length;i+=16){oa=a;ob=b;oc=c;od=d;
    a=ff(a,b,c,d,x[i],7,-680876936);d=ff(d,a,b,c,x[i+1],12,-389564586);c=ff(c,d,a,b,x[i+2],17,606105819);b=ff(b,c,d,a,x[i+3],22,-1044525330);
    a=ff(a,b,c,d,x[i+4],7,-176418897);d=ff(d,a,b,c,x[i+5],12,1200080426);c=ff(c,d,a,b,x[i+6],17,-1473231341);b=ff(b,c,d,a,x[i+7],22,-45705983);
    a=ff(a,b,c,d,x[i+8],7,1770035416);d=ff(d,a,b,c,x[i+9],12,-1958414417);c=ff(c,d,a,b,x[i+10],17,-42063);b=ff(b,c,d,a,x[i+11],22,-1990404162);
    a=ff(a,b,c,d,x[i+12],7,1804603682);d=ff(d,a,b,c,x[i+13],12,-40341101);c=ff(c,d,a,b,x[i+14],17,-1502002290);b=ff(b,c,d,a,x[i+15],22,1236535329);
    a=gg(a,b,c,d,x[i+1],5,-165796510);d=gg(d,a,b,c,x[i+6],9,-1069501632);c=gg(c,d,a,b,x[i+11],14,643717713);b=gg(b,c,d,a,x[i],20,-373897302);
    a=gg(a,b,c,d,x[i+5],5,-701558691);d=gg(d,a,b,c,x[i+10],9,38016083);c=gg(c,d,a,b,x[i+15],14,-660478335);b=gg(b,c,d,a,x[i+4],20,-405537848);
    a=gg(a,b,c,d,x[i+9],5,568446438);d=gg(d,a,b,c,x[i+14],9,-1019803690);c=gg(c,d,a,b,x[i+3],14,-187363961);b=gg(b,c,d,a,x[i+8],20,1163531501);
    a=gg(a,b,c,d,x[i+13],5,-1444681467);d=gg(d,a,b,c,x[i+2],9,-51403784);c=gg(c,d,a,b,x[i+7],14,1735328473);b=gg(b,c,d,a,x[i+12],20,-1926607734);
    a=hh(a,b,c,d,x[i+5],4,-378558);d=hh(d,a,b,c,x[i+8],11,-2022574463);c=hh(c,d,a,b,x[i+11],16,1839030562);b=hh(b,c,d,a,x[i+14],23,-35309556);
    a=hh(a,b,c,d,x[i+1],4,-1530992060);d=hh(d,a,b,c,x[i+4],11,1272893353);c=hh(c,d,a,b,x[i+7],16,-155497632);b=hh(b,c,d,a,x[i+10],23,-1094730640);
    a=hh(a,b,c,d,x[i+13],4,681279174);d=hh(d,a,b,c,x[i],11,-358537222);c=hh(c,d,a,b,x[i+3],16,-722521979);b=hh(b,c,d,a,x[i+6],23,76029189);
    a=hh(a,b,c,d,x[i+9],4,-640364487);d=hh(d,a,b,c,x[i+12],11,-421815835);c=hh(c,d,a,b,x[i+15],16,530742520);b=hh(b,c,d,a,x[i+2],23,-995338651);
    a=ii(a,b,c,d,x[i],6,-198630844);d=ii(d,a,b,c,x[i+7],10,1126891415);c=ii(c,d,a,b,x[i+14],15,-1416354905);b=ii(b,c,d,a,x[i+5],21,-57434055);
    a=ii(a,b,c,d,x[i+12],6,1700485571);d=ii(d,a,b,c,x[i+3],10,-1894986606);c=ii(c,d,a,b,x[i+10],15,-1051523);b=ii(b,c,d,a,x[i+1],21,-2054922799);
    a=ii(a,b,c,d,x[i+8],6,1873313359);d=ii(d,a,b,c,x[i+15],10,-30611744);c=ii(c,d,a,b,x[i+6],15,-1560198380);b=ii(b,c,d,a,x[i+13],21,1309151649);
    a=ii(a,b,c,d,x[i+4],6,-145523070);d=ii(d,a,b,c,x[i+11],10,-1120210379);c=ii(c,d,a,b,x[i+2],15,718787259);b=ii(b,c,d,a,x[i+9],21,-343485551);
    a=ad(a,oa);b=ad(b,ob);c=ad(c,oc);d=ad(d,od);
  }
  return rh(a)+rh(b)+rh(c)+rh(d);
}

// ── SIP state ──────────────────────────────────────────────────────────────
var _sip = {
  ws:null, state:'idle', cfg:null,
  regCsq:0, callCsq:0,
  regCid:null, regFromTag:null,
  callCid:null, callFromTag:null, callToTag:null, callUri:null,
  myToTag:null,          // our tag in incoming call responses
  inInvite:null,         // pending incoming INVITE message
  pc:null,               // RTCPeerConnection for SIP call
  micStream:null, remoteAnalyser:null, micAnalyser:null,
  callStart:null, micMuted:false,
  regTimer:null, retryTimer:null, regTimeoutTimer:null, durTimer:null, regAuthAttempts:0,
  realm:null,    // learned from server's 401 WWW-Authenticate realm= during REGISTER
};

// ── Utilities ──────────────────────────────────────────────────────────────
function _sipRand(n){var c='abcdef0123456789',s='';for(var i=0;i<n;i++)s+=c[Math.floor(Math.random()*c.length)];return s;}
function _sipBranch(){return 'z9hG4bK'+_sipRand(12);}
function _sipTag(){return _sipRand(10);}
function _sipCid(host){return _sipRand(14)+'@'+(host||'ss');}

function _sipWsHostname(){try{return new URL(_sip.cfg.server).hostname;}catch(e){return 'ss';}}
function _sipWsHost(){try{var u=new URL(_sip.cfg.server);return u.hostname+(u.port?':'+u.port:'');}catch(e){return 'ss';}}
// Domain priority: explicit config → realm from server's 401 challenge → WS hostname.
// The realm is the correct SIP domain for all URIs (REGISTER, INVITE, From, To).
// Using the WS hostname alone causes 484 when the server's SIP realm differs.
function _sipDomain(){return (_sip.cfg.domain||'').trim()||_sip.realm||_sipWsHostname();}
function _sipSelfUri(){return 'sip:'+_sip.cfg.username+'@'+_sipDomain();}
function _sipContact(){return '<sip:'+_sip.cfg.username+'@'+_sipWsHostname()+';transport=ws>';}

function _sipExtractTag(h){var m=(h||'').match(/;tag=([^\s;,>]+)/);return m?m[1]:null;}
function _sipExtractURI(h){var m=(h||'').match(/<([^>]+)>/);if(m)return m[1];m=(h||'').match(/(sips?:[^\s;,>]+)/);return m?m[1]:(h||'').trim();}
function _sipExtractDisplay(h){var m=(h||'').match(/"([^"]+)"/);return m?m[1]:null;}

function _sipParseWWWAuth(h){
  var r={},re=/(\w+)=(?:"([^"]+)"|([^,\s]+))/g,m;
  while((m=re.exec(h))!==null)r[m[1]]=m[2]!==undefined?m[2]:m[3];
  return r;
}

function _sipDigest(method,uri,auth,user,pass){
  var realm=auth.realm||'',nonce=auth.nonce||'';
  var qop=auth.qop?(auth.qop.split(',')[0].trim()):null;
  var ha1=_md5(user+':'+realm+':'+pass);
  var ha2=_md5(method.toUpperCase()+':'+uri);
  var nc='00000001',cnonce=_sipRand(8),resp;
  if(qop==='auth'||qop==='auth-int'){
    resp=_md5(ha1+':'+nonce+':'+nc+':'+cnonce+':auth:'+ha2);
    return 'Digest username="'+user+'",realm="'+realm+'",nonce="'+nonce+'",uri="'+uri+'",nc='+nc+',cnonce="'+cnonce+'",qop=auth,response="'+resp+'",algorithm=MD5';
  }
  resp=_md5(ha1+':'+nonce+':'+ha2);
  return 'Digest username="'+user+'",realm="'+realm+'",nonce="'+nonce+'",uri="'+uri+'",response="'+resp+'",algorithm=MD5';
}

// ── SIP message builder ────────────────────────────────────────────────────
function _sipBuildReq(method,uri,hdrs,body){
  var lines=[method+' '+uri+' SIP/2.0'];
  Object.keys(hdrs).forEach(function(k){lines.push(k+': '+hdrs[k]);});
  lines.push('Content-Length: '+(body?body.length:0));
  lines.push('');
  if(body)lines.push(body);
  return lines.join('\r\n');
}

// ── SIP message parser ─────────────────────────────────────────────────────
function _sipParse(raw){
  var sep=raw.indexOf('\r\n\r\n'), sepLen=4;
  if(sep<0){sep=raw.indexOf('\n\n'); sepLen=2;}
  var hdrSec=sep>=0?raw.substring(0,sep):raw;
  var body=sep>=0?raw.substring(sep+sepLen):'';
  var lines=hdrSec.split(/\r?\n/);
  var fl=lines[0], hdrs={};
  var compact={v:'via',f:'from',t:'to',m:'contact',i:'call-id',l:'content-length',c:'content-type'};
  for(var i=1;i<lines.length;i++){
    var ln=lines[i]; if(!ln.trim())continue;
    var col=ln.indexOf(':'); if(col<0)continue;
    var k=ln.substring(0,col).trim().toLowerCase();
    var v=ln.substring(col+1).trim();
    k=compact[k]||k;
    hdrs[k]=hdrs[k]!==undefined?hdrs[k]+'\r\n'+v:v;
  }
  var rm=fl.match(/^SIP\/2\.0\s+(\d+)\s+(.*)/);
  return{isResponse:!!rm,status:rm?parseInt(rm[1]):null,reason:rm?rm[2]:null,
         method:!rm?fl.split(' ')[0]:null,uri:!rm?fl.split(' ')[1]:null,
         headers:hdrs,body:body};
}

// ── SIP response builder (echoes request Via/From/To/Call-ID/CSeq) ─────────
function _sipBuildResp(req,status,reason,extraHdrs,body){
  var lines=['SIP/2.0 '+status+' '+reason];
  // Echo Via (all lines)
  var via=req.headers['via']||'';
  via.split('\r\n').forEach(function(v){if(v.trim())lines.push('Via: '+v.trim());});
  // From: copy exactly
  if(req.headers['from'])lines.push('From: '+req.headers['from']);
  // To: add our tag for 2xx
  var toHdr=req.headers['to']||'';
  if(status>=200&&!toHdr.match(/;tag=/)){
    if(!_sip.myToTag)_sip.myToTag=_sipTag();
    toHdr+=';tag='+_sip.myToTag;
  }
  lines.push('To: '+toHdr);
  if(req.headers['call-id'])lines.push('Call-ID: '+req.headers['call-id']);
  if(req.headers['cseq'])   lines.push('CSeq: '+req.headers['cseq']);
  Object.keys(extraHdrs).forEach(function(k){lines.push(k+': '+extraHdrs[k]);});
  lines.push('Content-Length: '+(body?body.length:0));
  lines.push('');
  if(body)lines.push(body);
  return lines.join('\r\n');
}

// ── Low-level send ─────────────────────────────────────────────────────────
function _sipSend(msg){
  if(_sip.ws&&_sip.ws.readyState===1){
    console.debug('[IPLink SIP] >>>\n'+msg);
    _sip.ws.send(msg);
  }
}

// ── REGISTER ───────────────────────────────────────────────────────────────
function _sipRegister(authHdr){
  var cfg=_sip.cfg, dom=_sipDomain(), wshn=_sipWsHostname();
  _sip.regCsq++;
  var hdrs={
    'Via':         'SIP/2.0/WS '+wshn+';branch='+_sipBranch()+';rport',
    'Max-Forwards':'70',
    'From':        '"'+cfg.display_name+'" <'+_sipSelfUri()+'>;tag='+_sip.regFromTag,
    'To':          '<'+_sipSelfUri()+'>',
    'Call-ID':     _sip.regCid,
    'CSeq':        _sip.regCsq+' REGISTER',
    'Contact':     _sipContact()+';+sip.ice',
    'Expires':     '600',
    'Allow':       'INVITE,ACK,CANCEL,OPTIONS,BYE,INFO',
    'User-Agent':  'SignalScope-IPLink/1.1',
  };
  if(authHdr)hdrs['Authorization']=authHdr;
  _sipSend(_sipBuildReq('REGISTER','sip:'+dom,hdrs,''));
  _sipSetState('registering');
  // Timeout: if no response in 15 s, show an error rather than spinning forever
  clearTimeout(_sip.regTimeoutTimer);
  _sip.regTimeoutTimer=setTimeout(function(){
    if(_sip.state==='registering'){
      _sipSetState('error','Registration timed out — no response from server. Check the server URL and that it accepts SIP over WebSocket.');
    }
  },15000);
}

// ── Connect to SIP server ──────────────────────────────────────────────────
// Listen for CSP violations — if the browser blocks the SIP WebSocket due to
// Content-Security-Policy, a securitypolicyviolation event fires before onerror.
var _sipCspBlocked = false;
document.addEventListener('securitypolicyviolation', function(e){
  if(_sip.cfg && _sip.cfg.server && e.blockedURI &&
     _sip.cfg.server.indexOf(e.blockedURI.replace(/^wss?:/,'')) >= 0){
    _sipCspBlocked = true;
    _sipSetState('error','WebSocket blocked by browser security policy (CSP). Reload this page and try again — if it persists, contact your SignalScope administrator.');
  }
});

function _sipConnect(cfg){
  _sipStop();
  _sip.cfg=cfg;
  _sip.realm=null;  // reset; will be learned from server's 401 challenge
  _sip.regCid=_sipCid(_sipDomain());
  _sip.regFromTag=_sipTag();
  _sip.regCsq=0;
  _sipCspBlocked=false;
  _sipSetState('connecting');
  try{
    _sip.ws=new WebSocket(cfg.server,['sip']);
    _sip.ws.onopen=function(){_sipRegister();};
    _sip.ws.onmessage=function(e){_sipHandleMsg(e.data);};
    _sip.ws.onerror=function(ev){
      if(_sipCspBlocked) return; // already shown CSP error
      var hint='';
      if(location.protocol==='https:'&&cfg.server.indexOf('wss://')!==0){
        hint=' — hub is on HTTPS, server URL must use wss:// not ws://';
      } else {
        hint=' — possible causes: (1) cert not trusted — open '+cfg.server.replace('wss://','https://')+' in a new tab; (2) wrong port or server not running; (3) firewall blocking the port';
      }
      _sipSetState('error','WebSocket error'+hint);
    };
    _sip.ws.onclose=function(){
      if(_sip.state==='idle'||_sipCspBlocked)return;
      _sipSetState('error','Disconnected');
      _sip.retryTimer=setTimeout(function(){if(_sip.cfg)_sipConnect(_sip.cfg);},30000);
    };
  }catch(e){_sipSetState('error',e.message);}
}

function _sipStop(){
  clearTimeout(_sip.regTimer);clearTimeout(_sip.retryTimer);clearTimeout(_sip.regTimeoutTimer);clearInterval(_sip.durTimer);
  if(_sip.ws){try{_sip.ws.close();}catch(e){}_sip.ws=null;}
  _sipCleanupCall();
  _sip.state='idle'; _sipUpdateUI();
}

// ── Incoming INVITE ────────────────────────────────────────────────────────
function _sipHandleInvite(msg){
  _sipSend(_sipBuildResp(msg,100,'Trying',{},''));
  _sip.inInvite=msg;
  _sip.myToTag=null;   // will be created on first 2xx
  _sipSetState('incoming');
}

function sipAnswerCall(){
  if(!_sip.inInvite)return;
  var inv=_sip.inInvite;
  _sipSend(_sipBuildResp(inv,180,'Ringing',{'Contact':_sipContact()},''));
  // Hide the incoming banner immediately — use 'dialling' so call card renders
  // with the caller's name while we set up WebRTC (state→'incall' after ACK).
  _sipSetState('dialling');
  navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true},video:false})
    .then(function(stream){
      _sip.micStream=stream;
      _sip.pc=new RTCPeerConnection({iceServers:STUN.map(function(u){return{urls:u};})});
      stream.getTracks().forEach(function(t){_sip.pc.addTrack(t,stream);});
      _sip.pc.ontrack=function(e){
        var a=document.getElementById('sipAudio');
        if(a&&e.streams[0]){a.srcObject=e.streams[0];_sipSetupRemoteMeter(e.streams[0]);}
      };
      // Only tear down on 'failed' — 'disconnected' is temporary and ICE may recover.
      _sip.pc.onconnectionstatechange=function(){
        var cs=_sip.pc.connectionState;
        if(cs==='failed'){
          _sipCleanupCall();_sipSetState('registered');
          _sipShowCallErr('Call ended: connection failed');
        }
      };
      return _sip.pc.setRemoteDescription({type:'offer',sdp:inv.body});
    })
    .then(function(){return _sip.pc.createAnswer();})
    .then(function(ans){return _sip.pc.setLocalDescription(ans);})
    .then(function(){
      return new Promise(function(res){
        if(_sip.pc.iceGatheringState==='complete'){res();return;}
        _sip.pc.onicegatheringstatechange=function(){if(_sip.pc.iceGatheringState==='complete')res();};
        setTimeout(res,3000);
      });
    })
    .then(function(){
      var sdp=_sip.pc.localDescription.sdp;
      _sipSend(_sipBuildResp(inv,200,'OK',{'Contact':_sipContact(),'Content-Type':'application/sdp'},sdp));
      _sip.callCid=inv.headers['call-id']||'';
      _sipSetMicMeter(_sip.micStream);
      _sip.callStart=Date.now();
      _sipSetState('incall');
    })
    .catch(function(e){
      // Properly clean up so the incoming banner doesn't stick around.
      _sipSend(_sipBuildResp(inv,500,'Internal Error',{},''));
      console.error('SIP answer error:',e);
      _sipCleanupCall();
      _sipSetState('registered');
      _sipShowCallErr('Could not answer call: '+(e.message||e));
    });
}

function sipDeclineCall(){
  if(_sip.inInvite)_sipSend(_sipBuildResp(_sip.inInvite,603,'Decline',{},''));
  _sip.inInvite=null;
  _sipSetState('registered');
}

// ── Outgoing INVITE ────────────────────────────────────────────────────────
function sipDial(target){
  var t=(target||'').trim();
  if(!t){_sipShowCallErr('Enter an extension or SIP URI');return;}
  if(_sip.state!=='registered'){_sipShowCallErr('Not registered — check SIP settings');return;}
  var dom=_sipDomain();  // explicit config → learned realm → WS hostname
  // Build the call URI.  A SIP URI must have a host part (sip:user@host).
  // sip:ext with no @host is invalid — the server parses it as host=ext and
  // returns 484.  Always append the domain; _sipDomain() uses the realm
  // learned from the server's REGISTER challenge so it's always correct.
  _sip.callUri = t.match(/^sips?:/i) ? t
               : t.indexOf('@')>=0   ? 'sip:'+t
               : 'sip:'+t+'@'+dom;
  _sip.callCid=_sipCid(dom);
  _sip.callFromTag=_sipTag();
  _sip.callToTag=null;
  _sip.callCsq=0;
  _sip.myToTag=null;
  navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true},video:false})
    .then(function(stream){
      _sip.micStream=stream;
      _sip.pc=new RTCPeerConnection({iceServers:STUN.map(function(u){return{urls:u};})});
      stream.getTracks().forEach(function(t){_sip.pc.addTrack(t,stream);});
      _sip.pc.ontrack=function(e){
        var a=document.getElementById('sipAudio');
        if(a&&e.streams[0]){a.srcObject=e.streams[0];_sipSetupRemoteMeter(e.streams[0]);}
      };
      _sip.pc.onconnectionstatechange=function(){
        var cs=_sip.pc.connectionState;
        if(cs==='failed'){
          _sipCleanupCall();_sipSetState('registered');
          _sipShowCallErr('Call ended: connection failed');
        }
      };
      return _sip.pc.createOffer({offerToReceiveAudio:true});
    })
    .then(function(offer){return _sip.pc.setLocalDescription(offer);})
    .then(function(){
      return new Promise(function(res){
        if(_sip.pc.iceGatheringState==='complete'){res();return;}
        _sip.pc.onicegatheringstatechange=function(){if(_sip.pc.iceGatheringState==='complete')res();};
        setTimeout(res,3000);
      });
    })
    .then(function(){
      _sipSetMicMeter(_sip.micStream);
      _sipSendInvite(_sip.callUri,_sipMungeSdp(_sip.pc.localDescription.sdp));
      _sipSetState('dialling');
    })
    .catch(function(e){_sipShowCallErr('Microphone error: '+e.message);});
}

function _sipSendInvite(uri,sdp){
  var cfg=_sip.cfg, dom=_sipDomain();
  _sip.callCsq++;
  var hdrs={
    'Via':          'SIP/2.0/WS '+_sipWsHostname()+';branch='+_sipBranch()+';rport',
    'Max-Forwards': '70',
    'From':         '"'+cfg.display_name+'" <'+_sipSelfUri()+'>;tag='+_sip.callFromTag,
    'To':           '<'+uri+'>',
    'Call-ID':      _sip.callCid,
    'CSeq':         _sip.callCsq+' INVITE',
    'Contact':      _sipContact(),
    'Allow':        'INVITE,ACK,CANCEL,OPTIONS,BYE',
    'Content-Type': 'application/sdp',
  };
  _sipSend(_sipBuildReq('INVITE',uri,hdrs,sdp));
}

function _sipSendAck(okMsg,uri){
  var cfg=_sip.cfg;
  // RFC 3261 §17.1.1.3: ACK CSeq MUST equal the INVITE CSeq — do NOT increment.
  // The sequence number counter is only incremented for new requests (INVITE, BYE, etc).
  var cseqNum = (okMsg.headers['cseq']||'1 INVITE').match(/^(\d+)/);
  var cseq = (cseqNum ? cseqNum[1] : _sip.callCsq) + ' ACK';
  var toHdr=okMsg.headers['to']||('<'+uri+'>');
  var hdrs={
    'Via':          'SIP/2.0/WS '+_sipWsHostname()+';branch='+_sipBranch()+';rport',
    'Max-Forwards': '70',
    'From':         '"'+cfg.display_name+'" <'+_sipSelfUri()+'>;tag='+_sip.callFromTag,
    'To':           toHdr,
    'Call-ID':      _sip.callCid,
    'CSeq':         cseq,
    'Contact':      _sipContact(),
  };
  _sipSend(_sipBuildReq('ACK',uri,hdrs,''));
}

function _sipSendBye(){
  var cfg=_sip.cfg, dom=_sipDomain();
  var uri=_sip.callUri||_sipSelfUri();
  var toHdr='<'+uri+'>'+((_sip.callToTag)?';tag='+_sip.callToTag:'');
  _sip.callCsq++;
  var hdrs={
    'Via':          'SIP/2.0/WS '+_sipWsHostname()+';branch='+_sipBranch()+';rport',
    'Max-Forwards': '70',
    'From':         '"'+cfg.display_name+'" <'+_sipSelfUri()+'>;tag='+(_sip.callFromTag||_sipTag()),
    'To':           toHdr,
    'Call-ID':      _sip.callCid||_sipCid(dom),
    'CSeq':         _sip.callCsq+' BYE',
  };
  _sipSend(_sipBuildReq('BYE',uri,hdrs,''));
}

function sipHangup(){
  if(_sip.state==='incoming'){sipDeclineCall();return;}
  if(_sip.state==='incall'||_sip.state==='dialling')_sipSendBye();
  _sipCleanupCall();
  _sipSetState(_sip.state==='idle'?'idle':'registered');
}

// ── Main message handler ───────────────────────────────────────────────────
function _sipHandleMsg(raw){
  console.debug('[IPLink SIP] <<<\n'+raw);
  var msg=_sipParse(raw);
  if(msg.isResponse){
    var csqH=msg.headers['cseq']||'';
    var method=csqH.replace(/^\d+\s+/,'').toUpperCase().trim();
    var st=msg.status;
    if(method==='REGISTER'){
      clearTimeout(_sip.regTimeoutTimer);  // got a response — cancel the 15 s timeout
      if(st===200){
        _sip.regAuthAttempts=0;
        _sipSetState('registered');
        var exp=parseInt(((msg.headers['contact']||'').match(/expires=(\d+)/)||[])[1]||'600');
        clearTimeout(_sip.regTimer);
        _sip.regTimer=setTimeout(function(){if(_sip.state==='registered')_sipRegister();},exp*900);
      }else if(st===401||st===407){
        _sip.regAuthAttempts=(_sip.regAuthAttempts||0)+1;
        if(_sip.regAuthAttempts>2){
          _sipSetState('error','Authentication failed — check your SIP username and password.');
          return;
        }
        var wwwH=msg.headers['www-authenticate']||msg.headers['proxy-authenticate']||'';
        var auth=_sipParseWWWAuth(wwwH);
        // Learn the server's SIP realm from the challenge — this is the correct
        // domain for all SIP URIs (INVITE Request-URI, From, To).  Store it so
        // that bare extensions dial as sip:ext@realm rather than sip:ext (invalid)
        // or sip:ext@ws-hostname (404/484 if the hostname isn't the SIP realm).
        if(auth.realm && !_sip.realm) _sip.realm = auth.realm;
        var regUri='sip:'+_sipDomain();
        _sipRegister(_sipDigest('REGISTER',regUri,auth,_sip.cfg.username,_sip.cfg.password));
      }else if(st>=400){
        _sipSetState('error','Registration failed ('+st+')');
      }
    }else if(method==='INVITE'){
      if(st===180||st===183){
        _sipSetState('dialling');
        var pill=document.getElementById('sipStatusPill');
        if(pill)pill.querySelector('span:last-child').textContent='SIP: Ringing…';
      }else if(st===200){
        // Always ACK immediately — server retransmits 200 until it gets an ACK.
        _sipSendAck(msg, _sip.callUri);
        // Guard against duplicate 200 OK processing (server retransmits until ACK
        // is received; the PC is already in 'stable' after the first processing
        // and setRemoteDescription would fail with "Called in wrong state: stable").
        if(_sip.state==='incall'){ return; }
        _sip.callToTag=_sipExtractTag(msg.headers['to']||'');
        var sdp=msg.body;
        if(_sip.pc&&sdp){
          // Ensure SDP ends with \r\n
          var answerSdp = sdp.endsWith('\r\n') ? sdp : sdp + '\r\n';
          console.debug('[IPLink SIP] 200 OK answer SDP:\n', answerSdp);
          _sip.pc.setRemoteDescription({type:'answer', sdp:answerSdp})
            .then(function(){
              _sip.callStart=Date.now();
              _sipSetState('incall');
            }).catch(function(e){
              console.error('[IPLink SIP] setRemoteDescription on answer failed:', e.message);
              console.debug('[IPLink SIP] answer SDP that was rejected:\n', answerSdp);
              _sipCleanupCall();
              _sipSetState('registered');
              _sipShowCallErr('Call answered but media failed: '+e.message+' — check server logs for rtpengine errors');
            });
        } else if(_sip.pc && !sdp){
          _sip.callStart=Date.now();
          _sipSetState('incall');
        }
      }else if(st>=400){
        // RFC 3261 §17.1.1.3: non-2xx INVITE responses MUST be ACKed.
        // Build the ACK from the response headers directly — do NOT use
        // _sip.callCid / _sip.callFromTag because _sipCleanupCall() may have
        // already nulled them (server retransmits the 4xx until it gets an ACK).
        // The 4xx echoes Call-ID, From, CSeq from the original INVITE, so we
        // always have everything we need in msg.headers.
        // Use the To URI from the 4xx response as the ACK Request-URI.
        // _sip.callUri may already be null if _sipCleanupCall() ran on a
        // previous retransmission. The To header reliably contains the callee.
        var _ackUri = _sipExtractURI(msg.headers['to']) || _sip.callUri || _sipSelfUri();
        var _ackHdrs = {
          'Via':          'SIP/2.0/WS '+_sipWsHostname()+';branch='+_sipBranch()+';rport',
          'Max-Forwards': '70',
          'From':         msg.headers['from']  || '',
          'To':           msg.headers['to']    || '',
          'Call-ID':      msg.headers['call-id']|| '',
          'CSeq':         (msg.headers['cseq'] || '1 INVITE').replace(/INVITE$/i, 'ACK'),
          'Contact':      _sipContact(),
        };
        _sipSend(_sipBuildReq('ACK', _ackUri, _ackHdrs, ''));
        var reason=st+' '+(msg.reason||'');
        var hint='';
        if(st===404) hint=' — extension not found on server';
        else if(st===484) hint=' — check the dial string and SIP Domain/Realm setting';
        else if(st===486||st===600) hint=' — destination busy';
        else if(st===403) hint=' — forbidden (check dial permissions)';
        else if(st===488) hint=' — server rejected codec/SDP (check WebRTC support on server)';
        else if(st===500) hint=' — server internal error (check dial plan / extension exists)';
        console.warn('[IPLink SIP] INVITE failed '+reason+' — check browser DevTools console for full SIP traffic (filter: IPLink SIP)');
        _sipCleanupCall();
        _sipSetState('registered');
        _sipShowCallErr('Call failed: '+reason+hint);
      }
    }else if(method==='BYE'){
      if(st===200){_sipCleanupCall();_sipSetState('registered');}
    }
  }else{
    var m=msg.method;
    if(m==='INVITE'){
      if(_sip.state==='incall'||_sip.state==='incoming'||_sip.state==='dialling'){
        // Re-INVITE or second call while busy: decline
        _sipSend(_sipBuildResp(msg,486,'Busy Here',{},''));
      }else{
        _sipHandleInvite(msg);
      }
    }else if(m==='BYE'){
      _sipSend(_sipBuildResp(msg,200,'OK',{},''));
      _sipCleanupCall();_sipSetState('registered');
    }else if(m==='CANCEL'){
      if(_sip.inInvite&&(_sip.inInvite.headers['call-id']||'')===(msg.headers['call-id']||'')){
        _sipSend(_sipBuildResp(msg,200,'OK',{},''));
        _sipSend(_sipBuildResp(_sip.inInvite,487,'Request Terminated',{},''));
        _sip.inInvite=null;_sipSetState('registered');
      }
    }else if(m==='OPTIONS'){
      _sipSend(_sipBuildResp(msg,200,'OK',{'Allow':'INVITE,ACK,CANCEL,OPTIONS,BYE','Accept':'application/sdp'},''));
    }else if(m==='ACK'){
      // ACK to our 200 OK — call now fully established.
      // State is 'incall' if sipAnswerCall() finished before ACK arrived,
      // or 'dialling' if ACK arrived before the promise chain completed.
      if(_sip.state==='incoming'||_sip.state==='dialling'){
        if(!_sip.callStart)_sip.callStart=Date.now();
        _sipSetState('incall');
      }
    }
  }
}

// ── Level meters for SIP ───────────────────────────────────────────────────
function _sipSetMicMeter(stream){
  try{
    var ctx=new(window.AudioContext||window.webkitAudioContext)();
    var src=ctx.createMediaStreamSource(stream);
    var an=ctx.createAnalyser();an.fftSize=512;src.connect(an);
    _sip.micAnalyser=an;
  }catch(e){}
}
function _sipSetupRemoteMeter(stream){
  try{
    var ctx=new(window.AudioContext||window.webkitAudioContext)();
    var src=ctx.createMediaStreamSource(stream);
    var an=ctx.createAnalyser();an.fftSize=512;src.connect(an);
    _sip.remoteAnalyser=an;
  }catch(e){}
}
function _sipReadLevel(an){
  if(!an)return 0;
  var buf=new Uint8Array(an.frequencyBinCount);
  an.getByteTimeDomainData(buf);
  var sq=0;for(var i=0;i<buf.length;i++){var s=(buf[i]-128)/128;sq+=s*s;}
  return Math.sqrt(sq/buf.length);
}

// ── UI update ──────────────────────────────────────────────────────────────
function _sipSetState(state,errMsg){_sip.state=state;_sipUpdateUI(errMsg);}

function _sipUpdateUI(errMsg){
  var s=_sip.state;
  var pill=document.getElementById('sipStatusPill');
  var txt=document.getElementById('sipStatusTxt');
  var banner=document.getElementById('sipIncomingBanner');
  var callCard=document.getElementById('sipCallCard');
  var dialBtn=document.getElementById('sipDialBtn');
  var map={
    idle:      ['sip-off','SIP: Off'],
    connecting:['sip-off','SIP: Connecting…'],
    registering:['sip-off','SIP: Registering…'],
    registered:['sip-conn','SIP: Registered'],
    incoming:  ['sip-ring','SIP: Incoming call'],
    dialling:  ['sip-conn','SIP: Calling…'],
    incall:    ['sip-incall','SIP: On call'],
    error:     ['sip-err','SIP: '+(errMsg||'Error')],
  };
  var info=map[s]||['sip-off',s];
  if(pill){pill.className='sip-pill '+info[0];}
  if(txt){txt.textContent=info[1];}
  // Incoming banner
  if(banner){
    if(s==='incoming'&&_sip.inInvite){
      var fromH=_sip.inInvite.headers['from']||'';
      var disp=_sipExtractDisplay(fromH);
      var uri=_sipExtractURI(fromH).replace(/^sip:/i,'').split('@')[0];
      var name=disp?(disp+' ('+uri+')'):(uri||'Unknown');
      var cn=document.getElementById('sipCallerName');
      if(cn)cn.textContent=name;
      banner.style.display='flex';
    }else{
      banner.style.display='none';
    }
  }
  // Active call card
  if(callCard){
    if(s==='incall'||s==='dialling'){
      callCard.style.display='';
      var remEl=document.getElementById('sipCallRemote');
      if(remEl){
        var r=_sip.callUri||((_sip.inInvite)?_sipExtractURI(_sip.inInvite.headers['from']||''):'');
        remEl.textContent=r.replace(/^sip:/i,'');
      }
    }else{
      callCard.style.display='none';
    }
  }
  // Dial button enable/disable
  if(dialBtn)dialBtn.disabled=(s!=='registered');
  // Stop duration timer if not in call
  if(s!=='incall'&&s!=='dialling'){
    clearInterval(_sip.durTimer);_sip.durTimer=null;
    var dur=document.getElementById('sipCallDur');
    if(dur)dur.textContent='—';
  } else if(!_sip.durTimer){
    _sip.durTimer=setInterval(function(){
      var dur=document.getElementById('sipCallDur');
      if(dur&&_sip.callStart)dur.textContent=_fmt(Math.floor((Date.now()-_sip.callStart)/1000));
    },1000);
  }
}

function _sipShowCallErr(msg){
  var el=document.getElementById('sipCallErrMsg');
  if(!el)return;
  el.textContent=msg;el.style.display='';
  setTimeout(function(){el.style.display='none';},6000);
}

// ── Mic mute toggle ────────────────────────────────────────────────────────
function _sipToggleMute(){
  _sip.micMuted=!_sip.micMuted;
  if(_sip.micStream)_sip.micStream.getAudioTracks().forEach(function(t){t.enabled=!_sip.micMuted;});
  var btn=document.getElementById('sipMuteBtn');
  if(btn)btn.textContent=_sip.micMuted?'🔇 Mic MUTED':'🎤 Mic ON';
  if(_sip.micMuted){var el=document.getElementById('sipMicLvl');if(el)el.style.width='0%';}
}

// ── Cleanup ────────────────────────────────────────────────────────────────
function _sipCleanupCall(){
  clearInterval(_sip.durTimer);_sip.durTimer=null;
  if(_sip.pc){try{_sip.pc.close();}catch(e){}_sip.pc=null;}
  if(_sip.micStream){_sip.micStream.getTracks().forEach(function(t){t.stop();});_sip.micStream=null;}
  var a=document.getElementById('sipAudio');if(a)a.srcObject=null;
  _sip.remoteAnalyser=null;_sip.micAnalyser=null;
  _sip.inInvite=null;_sip.callCid=null;_sip.callFromTag=null;
  _sip.callToTag=null;_sip.callUri=null;_sip.callStart=null;_sip.micMuted=false;
  _sip.myToTag=null;
}

// ── Level meter ticker ─────────────────────────────────────────────────────
setInterval(function(){
  if(_sip.state!=='incall')return;
  var rl=_sipReadLevel(_sip.remoteAnalyser);
  var ml=_sipReadLevel(_sip.micAnalyser);
  var rf=document.getElementById('sipRemoteLvl'),rv=document.getElementById('sipRemoteLvlVal');
  var mf=document.getElementById('sipMicLvl'),mv=document.getElementById('sipMicLvlVal');
  if(rf)rf.style.width=(Math.min(rl*4,1)*100)+'%';
  if(rv)rv.textContent=rl>0?Math.round(rl*100)+'%':'—';
  if(!_sip.micMuted){if(mf)mf.style.width=(Math.min(ml*4,1)*100)+'%';if(mv)mv.textContent=ml>0?Math.round(ml*100)+'%':'—';}
  // RTT from WebRTC stats
  if(_sip.pc){
    _sip.pc.getStats().then(function(report){
      report.forEach(function(r){
        if(r.type==='candidate-pair'&&r.state==='succeeded'&&r.currentRoundTripTime!=null){
          var rtt=Math.round(r.currentRoundTripTime*1000);
          var row=document.getElementById('sipRttRow'),val=document.getElementById('sipRttVal');
          if(row)row.style.display='';
          if(val)val.textContent=rtt+' ms';
          val.style.color=rtt>100?'var(--wn)':'var(--ok)';
        }
      });
    }).catch(function(){});
  }
},200);

// ── SIP config save / load ─────────────────────────────────────────────────
function sipSaveCfg(){
  var cfg={
    enabled:  document.getElementById('sipEnabled').checked,
    server:   document.getElementById('sipServer').value.trim(),
    username: document.getElementById('sipUser').value.trim(),
    password: document.getElementById('sipPass').value,
    domain:   document.getElementById('sipDomain').value.trim(),
    display_name: document.getElementById('sipDisplayName').value.trim()||'Studio',
  };
  if(!cfg.server||!cfg.username){
    var msg=document.getElementById('sipSaveMsg');
    if(msg){msg.innerHTML='<div class="msg msg-err">Server URL and username are required</div>';return;}
  }
  fetch('/api/iplink/sip/config',{method:'POST',credentials:'same-origin',headers:csrfHdr(),body:JSON.stringify(cfg)})
    .then(function(r){return r.json();})
    .then(function(d){
      var msg=document.getElementById('sipSaveMsg');
      if(msg)msg.innerHTML='<div class="msg msg-ok">Saved. Connecting…</div>';
      setTimeout(function(){var m=document.getElementById('sipSaveMsg');if(m)m.innerHTML='';},3000);
      _sipConnect(cfg);
    })
    .catch(function(e){
      var msg=document.getElementById('sipSaveMsg');
      if(msg)msg.innerHTML='<div class="msg msg-err">'+_esc(''+e)+'</div>';
    });
}

function _sipLoadCfg(){
  fetch('/api/iplink/sip/config',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      if(d.server){
        document.getElementById('sipServer').value=d.server||'';
        document.getElementById('sipUser').value=d.username||'';
        document.getElementById('sipDomain').value=d.domain||'';
        document.getElementById('sipDisplayName').value=d.display_name||'Studio';
        document.getElementById('sipEnabled').checked=!!d.enabled;
        // Auto-connect if enabled (password returned from server for auto-connect only)
        if(d.enabled&&d.server&&d.username&&d._autopass){
          _sipConnect({server:d.server,username:d.username,password:d._autopass,
                       domain:d.domain,display_name:d.display_name||'Studio'});
        }
      }
    }).catch(function(){});
}

// ── Button wiring ──────────────────────────────────────────────────────────
document.getElementById('sipAnswerBtn').addEventListener('click',sipAnswerCall);
document.getElementById('sipRejectBtn').addEventListener('click',sipDeclineCall);
document.getElementById('sipHangupBtn').addEventListener('click',sipHangup);
document.getElementById('sipMuteBtn').addEventListener('click',_sipToggleMute);
document.getElementById('sipDialBtn').addEventListener('click',function(){
  sipDial(document.getElementById('sipDialInput').value);
});
document.getElementById('sipDialInput').addEventListener('keydown',function(e){
  if(e.key==='Enter')sipDial(this.value);
});
document.getElementById('sipSaveBtn').addEventListener('click',sipSaveCfg);
document.getElementById('sipDisconnectBtn').addEventListener('click',function(){
  _sipStop();
  var msg=document.getElementById('sipSaveMsg');
  if(msg){msg.innerHTML='<div class="msg msg-ok">Disconnected</div>';setTimeout(function(){msg.innerHTML='';},3000);}
});

// ── Init ──────────────────────────────────────────────────────────────────
_sipLoadCfg();
_sipUpdateUI();
</script>
</body></html>"""


# ─── Talent (contributor) page ────────────────────────────────────────────────

_TALENT_TPL = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{{room_name}} — IP Link</title>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<style nonce="{{csp_nonce()}}">
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
body{font-family:system-ui,sans-serif;background:radial-gradient(circle at top,#12376f 0%,var(--bg) 38%,#05101f 100%);color:var(--tx);min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:20px}
.card{background:var(--sur);border:1px solid var(--bor);border-radius:16px;padding:28px 24px;max-width:400px;width:100%;text-align:center;box-shadow:0 8px 32px rgba(0,0,0,.3)}
h1{font-size:22px;font-weight:800;margin-bottom:4px}
.sub{font-size:12px;color:var(--mu);margin-bottom:24px}
.status{font-size:14px;font-weight:700;padding:8px 0;margin-bottom:16px}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:middle}
.dot-wait{background:var(--mu)}
.dot-conn{background:var(--ok)}
.dot-err{background:var(--al)}
.dot-live{background:var(--ok);animation:pulse 1s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
/* Level meter */
.lvl-wrap{display:flex;align-items:center;gap:10px;margin:12px 0}
.lvl-label{font-size:12px;color:var(--mu);width:28px;text-align:right}
.lvl-outer{flex:1;height:12px;background:#0a1628;border-radius:6px;overflow:hidden}
.lvl-fill{height:12px;border-radius:6px;transition:width .1s;background:var(--ok)}
.lvl-val{font-size:12px;width:32px;text-align:left;font-variant-numeric:tabular-nums}
/* Mute button */
.mute-btn{width:100%;padding:14px;border:none;border-radius:10px;font-size:16px;font-weight:700;cursor:pointer;font-family:inherit;margin:14px 0;background:#1e3a5f;color:var(--tx);transition:background .2s}
.mute-btn.muted{background:#2a1010;color:var(--al)}
.mute-btn:active{filter:brightness(.85)}
/* Stats */
.stats{font-size:11px;color:var(--mu);margin-top:12px;line-height:1.8}
.err{color:var(--al);font-size:12px;margin-top:10px;padding:8px;background:#2a0a0a;border-radius:6px;border:1px solid #991b1b}
/* IFB */
.ifb-wrap{margin-top:14px;padding:10px 14px;background:#0a1628;border-radius:8px;font-size:12px;text-align:left}
.ifb-wrap label{color:var(--mu);display:block;margin-bottom:6px}
</style></head><body>
<div class="card" id="mainCard">
  <div style="font-size:36px;margin-bottom:12px">🎙</div>
  <h1 id="roomTitle">{{room_name}}</h1>
  <p class="sub">IP Link — Powered by SignalScope</p>

  <div id="statusLine" class="status">
    <span class="dot dot-wait"></span><span id="statusText">Initialising…</span>
  </div>

  <div id="lvlSection" style="display:none">
    <div class="lvl-wrap">
      <span class="lvl-label">You</span>
      <div class="lvl-outer"><div class="lvl-fill" id="tlvl" style="width:0%"></div></div>
      <span class="lvl-val" id="tlvlVal">—</span>
    </div>
    <div class="lvl-wrap">
      <span class="lvl-label">Hub</span>
      <div class="lvl-outer"><div class="lvl-fill" id="hlvl" style="width:0%;background:var(--acc)"></div></div>
      <span class="lvl-val" id="hlvlVal">—</span>
    </div>
  </div>

  <button class="mute-btn" id="muteBtn" style="display:none" onclick="toggleMute()">🎤 Mic ON</button>

  <div class="stats" id="statsDiv" style="display:none"></div>
  <div class="err" id="errDiv" style="display:none"></div>

  <!-- Hidden audio for IFB (hub talking back) -->
  <audio id="ifbAudio" autoplay playsinline style="display:none"></audio>
</div>

<script nonce="{{csp_nonce()}}">
var ROOM_ID = {{room_id|tojson}};
var STUN    = {{stun|tojson}};
var _pc, _mic, _muted=false, _pollTimer;
var _iceIdx = 0;   // hub ICE candidates consumed so far
var _statsTimer;

function _setStatus(dot, text){
  var sd = document.getElementById('statusLine');
  var dc = sd.querySelector('.dot');
  dc.className = 'dot '+dot;
  document.getElementById('statusText').textContent = text;
}
function _setErr(msg){
  var e=document.getElementById('errDiv');
  e.textContent=msg; e.style.display='';
}
function _apiPost(path, body){
  return fetch('/api/iplink'+path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
}
function _apiGet(path){
  return fetch('/api/iplink'+path);
}

// ─── Start connection ─────────────────────────────────────────────────────────
function _start(){
  _setStatus('dot-wait', 'Requesting microphone…');
  navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true},video:false})
    .then(function(stream){
      _mic = stream;
      _setupMicMeter(stream);
      document.getElementById('lvlSection').style.display='';
      document.getElementById('muteBtn').style.display='';
      _setStatus('dot-wait', 'Connecting…');
      _connect(stream);
    })
    .catch(function(e){
      _setStatus('dot-err', 'Microphone error');
      _setErr('Could not access microphone: '+e.message+'\nCheck your browser permissions.');
    });
}

function _connect(micStream){
  _pc = new RTCPeerConnection({iceServers: STUN.map(function(u){return{urls:u};})});

  // Add mic track
  micStream.getTracks().forEach(function(t){ _pc.addTrack(t, micStream); });

  // Receive IFB (hub talking back)
  _pc.ontrack = function(e){
    var audio = document.getElementById('ifbAudio');
    if(e.streams[0]){ audio.srcObject=e.streams[0]; _setupHubMeter(e.streams[0]); }
  };

  // Collect and send ICE
  _pc.onicecandidate = function(e){
    if(!e.candidate) return;
    _apiPost('/room/'+ROOM_ID+'/ice', {from:'talent', candidate:e.candidate});
  };

  _pc.onconnectionstatechange = function(){
    var s = _pc.connectionState;
    if(s==='connected'){
      _setStatus('dot-live','🔴 Live — connected to studio');
      _startStats();
    } else if(s==='disconnected'||s==='failed'){
      _setStatus('dot-err','Disconnected');
      _stopStats();
    } else if(s==='connecting'){
      _setStatus('dot-wait','Connecting…');
    }
    _apiPost('/room/'+ROOM_ID+'/status',{from:'talent',status:s});
  };

  // Create offer
  _pc.createOffer()
    .then(function(offer){ return _pc.setLocalDescription(offer).then(function(){ return offer; }); })
    .then(function(offer){
      return _apiPost('/room/'+ROOM_ID+'/offer', {offer: offer.sdp});
    })
    .then(function(){
      _setStatus('dot-wait','Waiting for studio to accept…');
      _pollAnswer();
    })
    .catch(function(e){ _setErr('WebRTC error: '+e.message); });
}

function _pollAnswer(){
  _apiGet('/room/'+ROOM_ID+'/answer')
    .then(function(r){ return r.json(); })
    .then(function(d){
      if(d.answer){
        _pc.setRemoteDescription({type:'answer', sdp:d.answer})
          .then(function(){ _pollHubIce(); })
          .catch(function(e){ _setErr('SDP error: '+e.message); });
      } else if(!d.room_exists){
        _setStatus('dot-err','Room not found');
        _setErr('This room has been deleted or expired.');
      } else {
        // Keep polling
        setTimeout(_pollAnswer, 600);
      }
    })
    .catch(function(){ setTimeout(_pollAnswer, 2000); });
}

function _pollHubIce(){
  _apiGet('/room/'+ROOM_ID+'/ice?from=hub&from_idx='+_iceIdx)
    .then(function(r){ return r.json(); })
    .then(function(d){
      (d.candidates||[]).forEach(function(c){
        _pc.addIceCandidate(c).catch(function(){});
      });
      _iceIdx = d.next_idx || _iceIdx;
      if(_pc.connectionState!=='connected'&&_pc.connectionState!=='closed'){
        setTimeout(_pollHubIce, 600);
      }
    })
    .catch(function(){ setTimeout(_pollHubIce, 2000); });
}

// ─── Level meters ─────────────────────────────────────────────────────────────
function _setupMicMeter(stream){
  try{
    var ctx = new(window.AudioContext||window.webkitAudioContext)();
    var src = ctx.createMediaStreamSource(stream);
    var an  = ctx.createAnalyser();
    an.fftSize=512; src.connect(an);
    var buf = new Uint8Array(an.frequencyBinCount);
    function tick(){
      an.getByteTimeDomainData(buf);
      var sq=0; for(var i=0;i<buf.length;i++){var s=(buf[i]-128)/128;sq+=s*s;}
      var rms=Math.sqrt(sq/buf.length);
      if(!_muted){
        var el=document.getElementById('tlvl');
        if(el) el.style.width=(Math.min(rms*4,1)*100)+'%';
        var vl=document.getElementById('tlvlVal');
        if(vl) vl.textContent=rms>0?Math.round(rms*100)+'%':'—';
        // Report level to hub
        _apiPost('/room/'+ROOM_ID+'/level',{from:'talent',level:rms});
      }
      requestAnimationFrame(tick);
    }
    tick();
  } catch(e){}
}

function _setupHubMeter(stream){
  try{
    var ctx = new(window.AudioContext||window.webkitAudioContext)();
    var src = ctx.createMediaStreamSource(stream);
    var an  = ctx.createAnalyser();
    an.fftSize=512; src.connect(an);
    var buf = new Uint8Array(an.frequencyBinCount);
    function tick(){
      an.getByteTimeDomainData(buf);
      var sq=0; for(var i=0;i<buf.length;i++){var s=(buf[i]-128)/128;sq+=s*s;}
      var rms=Math.sqrt(sq/buf.length);
      var el=document.getElementById('hlvl');
      if(el) el.style.width=(Math.min(rms*4,1)*100)+'%';
      var vl=document.getElementById('hlvlVal');
      if(vl) vl.textContent=rms>0?Math.round(rms*100)+'%':'—';
      requestAnimationFrame(tick);
    }
    tick();
  } catch(e){}
}

// ─── Mute ─────────────────────────────────────────────────────────────────────
function toggleMute(){
  _muted=!_muted;
  if(_mic){ _mic.getAudioTracks().forEach(function(t){t.enabled=!_muted;}); }
  var btn=document.getElementById('muteBtn');
  if(_muted){
    btn.textContent='🔇 Mic MUTED — tap to unmute';
    btn.classList.add('muted');
    document.getElementById('tlvl').style.width='0%';
    document.getElementById('tlvlVal').textContent='—';
  } else {
    btn.textContent='🎤 Mic ON';
    btn.classList.remove('muted');
  }
  _apiPost('/room/'+ROOM_ID+'/level',{from:'talent',level:0});
}

// ─── WebRTC stats ─────────────────────────────────────────────────────────────
function _startStats(){
  _statsTimer = setInterval(function(){
    if(!_pc) return;
    _pc.getStats().then(function(report){
      var rtt=null, loss=null, br=null;
      report.forEach(function(r){
        if(r.type==='candidate-pair'&&r.state==='succeeded'&&r.currentRoundTripTime!=null){
          rtt=Math.round(r.currentRoundTripTime*1000);
        }
        if(r.type==='inbound-rtp'&&r.mediaType==='audio'){
          if(r.packetsLost!=null&&r.packetsReceived!=null){
            var total=r.packetsLost+r.packetsReceived;
            loss=total>0?Math.round(r.packetsLost/total*1000)/10:0;
          }
        }
        if(r.type==='outbound-rtp'&&r.bytesSent!=null){br=r.bitrateMean||null;}
      });
      var sd=document.getElementById('statsDiv');
      if(sd){
        var parts=[];
        if(rtt!=null) parts.push('RTT: '+rtt+' ms');
        if(loss!=null) parts.push('Loss: '+loss+'%');
        sd.textContent=parts.join('  ·  ');
        sd.style.display=parts.length?'':'none';
        // POST stats to hub
        if(rtt!=null||loss!=null) _apiPost('/room/'+ROOM_ID+'/stats',{rtt_ms:rtt,loss_pct:loss});
      }
    }).catch(function(){});
  }, 3000);
}
function _stopStats(){ if(_statsTimer){clearInterval(_statsTimer);_statsTimer=null;} }

// ─── Kick off ─────────────────────────────────────────────────────────────────
window.addEventListener('load', function(){
  // Verify room still exists first
  _apiGet('/room/'+ROOM_ID+'/ping')
    .then(function(r){ return r.json(); })
    .then(function(d){
      if(!d.ok){ _setStatus('dot-err','Room not found'); _setErr('This link has expired or been deleted by the studio.'); return; }
      _start();
    })
    .catch(function(){ _setErr('Could not reach the SignalScope hub. Check your connection.'); });
});
</script>
</body></html>"""


# ─── Stream feed thread ──────────────────────────────────────────────────────

_feed_slots    = {}   # room_id → slot (active feed relay)
_feed_lock     = threading.Lock()
_pending_feeds = {}   # site_name → {slot_id, stream_idx} — hub queues, client consumes


def _feed_thread(inp, slot):
    """Push live audio from a local SignalScope input into a relay slot at real-time pace."""
    CHUNK_DUR = 0.095   # slightly under 0.1 s to avoid drifting behind
    prev_raw = None     # identity of last deque entry pushed (not a bytes copy)
    while not slot.closed and not slot.stale:
        t = time.monotonic()
        buf = getattr(inp, '_stream_buffer', None)
        if buf:
            try:
                raw = buf[-1]               # the actual deque entry object
                if raw is not prev_raw:     # new chunk arrived from monitor loop
                    slot.put(bytes(raw))
                    prev_raw = raw
            except (IndexError, Exception):
                pass
        elapsed = time.monotonic() - t
        time.sleep(max(0, CHUNK_DUR - elapsed))
    slot.closed = True   # ensure reaper picks it up


def _client_feed_thread(inp, hub_url, slot_id, secret):
    """Client-side: push live PCM from a local input to a hub relay slot via HTTP."""
    import urllib.request, hashlib, hmac as _hmac, os as _os
    CHUNK_DUR = 0.095
    chunk_url = f"{hub_url}/api/v1/audio_chunk/{slot_id}"
    prev_chunk_id = None

    def _sign(data):
        if not secret:
            return {}
        ts = time.time()
        key = hashlib.sha256(f"{secret}:signing".encode()).digest()
        msg = f"{ts:.0f}:".encode() + data
        sig = _hmac.new(key, msg, hashlib.sha256).hexdigest()
        nonce = hashlib.md5(_os.urandom(8)).hexdigest()[:16]
        return {"X-Hub-Sig": sig, "X-Hub-Ts": f"{ts:.0f}", "X-Hub-Nonce": nonce}

    prev_raw = None     # identity of last deque entry pushed
    while True:
        t = time.monotonic()
        buf = getattr(inp, '_stream_buffer', None)
        if not buf:
            time.sleep(0.1)
            continue
        try:
            raw = buf[-1]
            if raw is not prev_raw:
                chunk = bytes(raw)
                hdrs = {"Content-Type": "application/octet-stream"}
                hdrs.update(_sign(chunk))
                req = urllib.request.Request(chunk_url, data=chunk, method="POST", headers=hdrs)
                try:
                    resp = urllib.request.urlopen(req, timeout=5)
                    if resp.status == 404:
                        break   # slot gone — hub disconnected or room closed
                except Exception:
                    pass
                prev_raw = raw
        except (IndexError, Exception):
            pass
        elapsed = time.monotonic() - t
        time.sleep(max(0, CHUNK_DUR - elapsed))


def _client_lw_output_thread(hub_url, slot_id, address, port, n_ch):
    """Client-side: stream received audio from a hub relay slot and multicast it locally.

    The hub browser captures talent audio via AudioWorklet → POSTs S16LE chunks to
    /api/iplink/room/<id>/output_chunk → hub puts into the relay slot → this thread
    streams it from /api/iplink/output_stream/<slot_id> → feeds local _LivewireSender
    → UDP multicast on the client's LAN (where the Axia console lives).
    """
    import urllib.request
    sender = _LivewireSender(address, port, n_ch=n_ch)
    try:
        url = f"{hub_url}/api/iplink/output_stream/{slot_id}"
        req = urllib.request.urlopen(url, timeout=120)   # streaming; refreshed on stall
        READ_SZ = 9600   # ~0.1 s at 48 kHz S16LE stereo
        while True:
            chunk = req.read(READ_SZ)
            if not chunk:
                break
            sender.feed(chunk)
    except Exception as e:
        if _log:
            _log(f"[IPLink] Livewire output stream ended for {slot_id[:8]}: {e}")
    finally:
        sender.stop()


def _client_feed_poller(monitor_ref, hub_url, site_name, secret):
    """Client-side background thread: poll hub for feed commands and execute them."""
    import urllib.request, json as _json
    cmd_url = f"{hub_url}/api/iplink/feed_cmd"
    _active = {}   # slot_id → threading.Thread

    while True:
        try:
            req = urllib.request.Request(
                cmd_url, method="GET",
                headers={"X-Site": site_name},
            )
            resp = urllib.request.urlopen(req, timeout=5)
            d = _json.loads(resp.read())
            cmd = d.get("cmd")
            if cmd:
                slot_id    = cmd.get("slot_id")
                stop_id    = cmd.get("stop")
                cmd_type   = cmd.get("type", "feed")   # "feed" | "lw_output"

                if stop_id and stop_id in _active:
                    # Signal thread to stop by poisoning the slot (empty POST = EOF)
                    try:
                        urllib.request.urlopen(
                            urllib.request.Request(
                                f"{hub_url}/api/v1/audio_chunk/{stop_id}",
                                data=b"", method="POST",
                                headers={"Content-Type": "application/octet-stream"},
                            ), timeout=5)
                    except Exception:
                        pass
                    _active.pop(stop_id, None)

                elif cmd_type == "lw_output" and slot_id and slot_id not in _active:
                    # Hub wants us to stream from its relay slot and send LW multicast
                    address = str(cmd.get("address", ""))
                    port    = int(cmd.get("port", 5004))
                    n_ch    = int(cmd.get("n_ch", 2))
                    t = threading.Thread(
                        target=_client_lw_output_thread,
                        args=(hub_url, slot_id, address, port, n_ch),
                        daemon=True, name=f"IPLinkLWOut-{slot_id[:6]}",
                    )
                    _active[slot_id] = t
                    t.start()

                elif cmd_type == "feed" and slot_id and slot_id not in _active:
                    stream_idx = int(cmd.get("stream_idx", 0))
                    inputs = getattr(getattr(monitor_ref, 'app_cfg', None), 'inputs', []) or []
                    if 0 <= stream_idx < len(inputs):
                        inp = inputs[stream_idx]
                        t = threading.Thread(
                            target=_client_feed_thread,
                            args=(inp, hub_url, slot_id, secret),
                            daemon=True, name=f"IPLinkClientFeed-{slot_id[:6]}",
                        )
                        _active[slot_id] = t
                        t.start()
        except Exception:
            pass
        time.sleep(3)


# ─── Plugin registration ──────────────────────────────────────────────────────

def register(app, ctx):
    global _log, _monitor_ref, _listen_reg_ref
    login_required  = ctx["login_required"]
    csrf_protect    = ctx["csrf_protect"]
    monitor         = ctx["monitor"]
    listen_registry = ctx["listen_registry"]
    BUILD           = ctx["BUILD"]
    _log             = monitor.log
    _monitor_ref     = monitor
    _listen_reg_ref  = listen_registry

    try:
        from flask import request, jsonify, render_template_string as rts, make_response
    except ImportError:
        return

    # Mode detection
    _cfg_hub   = getattr(monitor.app_cfg, 'hub', None)
    _mode      = (getattr(_cfg_hub, 'mode', 'standalone') or 'standalone')
    _is_hub    = _mode in ('hub', 'both', 'standalone')
    _is_client = _mode == 'client'
    _hub_url   = (getattr(_cfg_hub, 'hub_url', '') or '').rstrip('/')
    _site_name = getattr(_cfg_hub, 'site_name', '') or ''
    _secret    = getattr(_cfg_hub, 'secret_key', '') or ''

    # Load permanent rooms from disk
    _load_rooms()

    # Start server-side routing for permanent rooms that have it configured
    if _is_hub:
        _has_sm = False
        for _rid, _room in list(_rooms.items()):
            if _room.get("permanent") and _room.get("server_managed") and _room.get("source"):
                _has_sm = True
                _start_source_routing(_rid)
        if _has_sm and _AIORTC:
            _ensure_aio_loop()

    # Start cleanup thread
    threading.Thread(target=_cleanup_thread, daemon=True, name="IPLinkCleanup").start()

    # Start client-side feed poller if we are a client node connected to a hub
    if _is_client and _hub_url and _site_name:
        threading.Thread(
            target=_client_feed_poller,
            args=(monitor, _hub_url, _site_name, _secret),
            daemon=True, name="IPLinkClientFeedPoller",
        ).start()
        _log(f"[IPLink] Client mode — feed poller started → {_hub_url}")

    # ── CSP patch — allow wss: on the hub IP Link page ─────────────────────────
    # SignalScope sets connect-src 'self' via an after_request handler. Flask
    # calls after_request handlers in REVERSE registration order, so inserting
    # at position 0 means our handler runs LAST — after SignalScope has already
    # set the CSP header — allowing us to reliably extend connect-src.
    from flask import request as _freq
    def _iplink_csp_patch(response):
        try:
            if _freq.path == "/hub/iplink":
                csp = response.headers.get("Content-Security-Policy", "")
                if csp and "connect-src" in csp and "wss:" not in csp:
                    response.headers["Content-Security-Policy"] = csp.replace(
                        "connect-src 'self'", "connect-src 'self' wss:")
        except Exception:
            pass
        return response
    app.after_request_funcs.setdefault(None, []).insert(0, _iplink_csp_patch)

    # ── Helper ─────────────────────────────────────────────────────────────────
    def _get_room(room_id):
        with _lock:
            return _rooms.get(room_id)

    # ── Hub management page ────────────────────────────────────────────────────
    @app.get("/hub/iplink")
    @login_required
    def iplink_hub():
        # csrf_token, csp_nonce, and topnav are Jinja2 context processors —
        # automatically available in render_template_string; do not pass explicitly.
        return rts(_HUB_TPL, stun=_STUN, build=BUILD)

    # ── Talent page (no login required) ───────────────────────────────────────
    @app.get("/iplink/join/<room_id>")
    def iplink_talent(room_id):
        try:
            from flask import render_template_string as rts
        except Exception:
            return "Error", 500
        room = _get_room(room_id)
        if not room:
            return "<h2 style='font-family:system-ui;padding:40px'>This IP Link room has expired or been deleted.</h2>", 404
        return rts(_TALENT_TPL, room_name=room["name"], room_id=room_id, stun=_STUN)

    # ── Room ping (talent checks room still alive) ────────────────────────────
    @app.get("/api/iplink/room/<room_id>/ping")
    def iplink_ping(room_id):
        room = _get_room(room_id)
        if not room:
            return jsonify({"ok": False}), 404
        _touch(room)
        return jsonify({"ok": True, "name": room["name"]})

    # ── Room CRUD ──────────────────────────────────────────────────────────────
    @app.get("/api/iplink/rooms")
    @login_required
    def iplink_list_rooms():
        with _lock:
            rooms = [_room_public(r) for r in sorted(_rooms.values(), key=lambda x: x["created"], reverse=True)]
        return jsonify({"rooms": rooms})

    @app.post("/api/iplink/rooms")
    @login_required
    @csrf_protect
    def iplink_create_room():
        data      = request.get_json(silent=True) or {}
        name      = str(data.get("name", "")).strip()[:50]
        quality   = str(data.get("quality", "broadcast"))
        permanent = bool(data.get("permanent", False))
        if not name:
            return jsonify({"error": "Room name required"}), 400
        room = _new_room(name, quality)
        room["permanent"] = permanent
        with _lock:
            _rooms[room["id"]] = room
        if permanent:
            _save_rooms()
        _log(f"[IPLink] Room created: '{name}' ({quality}) permanent={permanent} id={room['id'][:8]}")
        return jsonify({"room": _room_public(room)}), 201

    @app.delete("/api/iplink/room/<room_id>")
    @login_required
    @csrf_protect
    def iplink_delete_room(room_id):
        with _lock:
            room = _rooms.pop(room_id, None)
        # Stop any Livewire sender
        sender = _lw_senders.pop(room_id, None)
        if sender:
            sender.stop()
        if room:
            if room.get("permanent"):
                _save_rooms()
            _log(f"[IPLink] Room deleted: '{room['name']}'")
        return jsonify({"ok": bool(room)})

    @app.post("/api/iplink/room/<room_id>/reset")
    @login_required
    @csrf_protect
    def iplink_reset_room(room_id):
        with _lock:
            room = _rooms.get(room_id)
            if not room:
                return jsonify({"error": "Not found"}), 404
            room["status"]       = "waiting"
            room["offer"]        = None
            room["answer"]       = None
            room["talent_ice"]   = []
            room["hub_ice"]      = []
            room["talent_level"] = 0.0
            room["hub_level"]    = 0.0
            room["connected_at"] = None
            room["stats"]        = {}
            _touch(room)
        return jsonify({"ok": True})

    # ── Signalling ─────────────────────────────────────────────────────────────

    # Talent posts offer (no login — room_id is the token)
    @app.post("/api/iplink/room/<room_id>/offer")
    def iplink_post_offer(room_id):
        with _lock:
            room = _rooms.get(room_id)
            if not room:
                return jsonify({"error": "Room not found"}), 404
            data = request.get_json(silent=True) or {}
            sdp  = str(data.get("offer", "")).strip()
            if not sdp:
                return jsonify({"error": "No SDP"}), 400
            # Restore the CRLF that .strip() removed.  SDP is a line-oriented
            # format; RFC 4566 requires every line — including the last — to end
            # with CRLF.  Without it Chrome reports the last line as "Invalid SDP
            # line" because its parser can't find the line terminator.
            room["offer"]  = sdp + "\r\n"
            room["status"] = "offer_received"
            room["answer"] = None
            room["talent_ice"] = []
            room["hub_ice"]    = []
            room["talent_ip"]  = request.remote_addr or ""
            _touch(room)
        _log(f"[IPLink] Offer received for room '{room['name']}' from {room['talent_ip']}")
        # Permanent server-managed rooms: auto-accept offer with aiortc (no browser needed)
        if room.get("permanent") and room.get("server_managed") and _AIORTC and _ServerAudioTrack:
            loop = _ensure_aio_loop()
            _asyncio.run_coroutine_threadsafe(_server_accept_offer(room_id), loop)
        return jsonify({"ok": True})

    # Hub gets talent's offer
    @app.get("/api/iplink/room/<room_id>/offer")
    @login_required
    def iplink_get_offer(room_id):
        room = _get_room(room_id)
        if not room:
            return jsonify({"error": "Not found"}), 404
        return jsonify({"offer": room.get("offer")})

    # Hub posts answer
    @app.post("/api/iplink/room/<room_id>/answer")
    @login_required
    @csrf_protect
    def iplink_post_answer(room_id):
        with _lock:
            room = _rooms.get(room_id)
            if not room:
                return jsonify({"error": "Not found"}), 404
            data = request.get_json(silent=True) or {}
            sdp  = str(data.get("answer", "")).strip()
            if not sdp:
                return jsonify({"error": "No SDP"}), 400
            room["answer"] = sdp + "\r\n"
            room["status"] = "connecting"
            _touch(room)
        return jsonify({"ok": True})

    # Talent polls for hub's answer
    @app.get("/api/iplink/room/<room_id>/answer")
    def iplink_get_answer(room_id):
        room = _get_room(room_id)
        if not room:
            return jsonify({"room_exists": False, "answer": None})
        _touch(room)
        return jsonify({"room_exists": True, "answer": room.get("answer")})

    # Either side posts an ICE candidate
    @app.post("/api/iplink/room/<room_id>/ice")
    def iplink_post_ice(room_id):
        with _lock:
            room = _rooms.get(room_id)
            if not room:
                return jsonify({"error": "Not found"}), 404
            data = request.get_json(silent=True) or {}
            side = str(data.get("from", ""))
            cand = data.get("candidate")
            if not cand:
                return jsonify({"ok": True})   # null candidate = end of candidates
            if side == "talent":
                room["talent_ice"].append(cand)
                # Server-managed room: immediately forward candidate to aiortc PC
                if (room.get("permanent") and room.get("server_managed")
                        and _AIORTC and _aio_loop and room_id in _server_pcs):
                    _asyncio.run_coroutine_threadsafe(
                        _server_add_ice(room_id, cand), _aio_loop
                    )
            elif side == "hub":
                room["hub_ice"].append(cand)
            _touch(room)
        return jsonify({"ok": True})

    # Either side polls for the other's ICE candidates
    @app.get("/api/iplink/room/<room_id>/ice")
    def iplink_get_ice(room_id):
        room = _get_room(room_id)
        if not room:
            return jsonify({"candidates": [], "next_idx": 0})
        side     = request.args.get("from", "talent")    # "talent" or "hub"
        from_idx = int(request.args.get("from_idx", 0))
        with _lock:
            candidates = room["talent_ice"][from_idx:] if side == "talent" else room["hub_ice"][from_idx:]
            next_idx   = from_idx + len(candidates)
        _touch(room)
        return jsonify({"candidates": candidates, "next_idx": next_idx})

    # Level update (either side posts their outgoing level)
    @app.post("/api/iplink/room/<room_id>/level")
    def iplink_post_level(room_id):
        with _lock:
            room = _rooms.get(room_id)
            if not room:
                return jsonify({"error": "Not found"}), 404
            data  = request.get_json(silent=True) or {}
            side  = str(data.get("from", ""))
            level = float(data.get("level", 0.0))
            if side == "talent":
                room["talent_level"] = max(0.0, min(1.0, level))
            elif side == "hub":
                room["hub_level"] = max(0.0, min(1.0, level))
            _touch(room)
        return jsonify({"ok": True})

    # Connection status update
    @app.post("/api/iplink/room/<room_id>/status")
    def iplink_post_status(room_id):
        with _lock:
            room = _rooms.get(room_id)
            if not room:
                return jsonify({"error": "Not found"}), 404
            data   = request.get_json(silent=True) or {}
            side   = str(data.get("from", ""))
            status = str(data.get("status", ""))
            if status == "connected":
                room["status"] = "connected"
                if not room["connected_at"]:
                    room["connected_at"] = time.time()
                _log(f"[IPLink] Connection established: room '{room['name']}'")
            elif status in ("disconnected", "failed"):
                room["status"] = "disconnected"
                room["disconnected_at"] = time.time()
                room["talent_level"] = 0.0
                room["hub_level"]    = 0.0
                # Stop Livewire sender on disconnect
                sender = _lw_senders.pop(room_id, None)
                if sender:
                    sender.stop()
                if side == "hub":
                    _log(f"[IPLink] Room '{room['name']}' disconnected by hub")
            _touch(room)
        return jsonify({"ok": True})

    # WebRTC stats from talent
    @app.post("/api/iplink/room/<room_id>/stats")
    def iplink_post_stats(room_id):
        with _lock:
            room = _rooms.get(room_id)
            if not room:
                return jsonify({"error": "Not found"}), 404
            data = request.get_json(silent=True) or {}
            room["stats"] = {k: data[k] for k in ("rtt_ms", "loss_pct") if k in data}
            _touch(room)
        return jsonify({"ok": True})

    # Hub mute toggle
    @app.post("/api/iplink/room/<room_id>/mute")
    @login_required
    @csrf_protect
    def iplink_mute(room_id):
        with _lock:
            room = _rooms.get(room_id)
            if not room:
                return jsonify({"error": "Not found"}), 404
            room["hub_muted"] = not room["hub_muted"]
            muted = room["hub_muted"]
        _log(f"[IPLink] Hub IFB {'muted' if muted else 'unmuted'} on room '{room['name']}'")
        return jsonify({"ok": True, "muted": muted})

    # ── Live streams list ──────────────────────────────────────────────────────
    @app.get("/api/iplink/streams")
    @login_required
    def iplink_list_streams():
        """Return available streams for audio source injection.
        Hub/both/standalone: lists streams from connected client sites (hub_server._sites).
        Client/standalone with local inputs: also lists local inputs.
        """
        hub_server = ctx.get("hub_server")
        streams = []

        # Hub mode: gather streams from connected client sites
        if _is_hub and hub_server:
            try:
                sites = getattr(hub_server, '_sites', {})
                for site, sdata in sites.items():
                    if not sdata.get('_approved'):
                        continue
                    site_streams = sdata.get('streams', [])
                    for idx, s in enumerate(site_streams):
                        name = s.get('name') or s.get('stream_name') or f"Stream {idx}"
                        lvl  = s.get('level_dbfs')
                        active = lvl is not None and lvl > -90
                        # FM stereo: runtime-detected, never in heartbeat; but live-push sets level_dbfs_l
                        stereo = bool(s.get('stereo')) or s.get('level_dbfs_l') is not None
                        streams.append({
                            "site":   site,
                            "idx":    idx,
                            "name":   f"{site} — {name}",
                            "stereo": stereo,
                            "active": active,
                        })
            except Exception:
                pass

        # Always include local inputs (works on standalone / both mode)
        local_inputs = getattr(monitor.app_cfg, 'inputs', []) or []
        for idx, inp in enumerate(local_inputs):
            name = (getattr(inp, 'name', None) or
                    getattr(inp, 'stream_name', None) or
                    getattr(inp, 'device_index', None) or
                    f"Input {idx}")
            stereo = getattr(inp, '_audio_channels', 1) == 2
            active = getattr(inp, '_has_real_level', False)
            streams.append({
                "site":   "local",
                "idx":    idx,
                "name":   name,
                "stereo": stereo,
                "active": active,
            })

        return jsonify({"streams": streams})

    # ── Room audio feed (inject SignalScope stream into WebRTC) ────────────────
    # Architecture: the browser uses a hidden <audio> element pointed at the
    # hub's existing live-relay URL, captures it with createMediaElementSource,
    # routes through a GainNode → MediaStreamDestinationNode, then replaceTrack
    # on the WebRTC sender.  No separate PCM relay slot needed — this works for
    # both local hub inputs and remote client-site streams without requiring the
    # iplink plugin to be installed on client nodes.

    @app.post("/api/iplink/room/<room_id>/feed")
    @login_required
    @csrf_protect
    def iplink_set_feed(room_id):
        """Return the live-relay URL for a stream so the browser can inject it
        into a WebRTC room via createMediaElementSource.
        Local inputs: /stream/<idx>/live
        Remote site:  /hub/site/<site>/stream/<idx>/live
        """
        import urllib.parse as _urlparse
        room = _get_room(room_id)
        if not room:
            return jsonify({"error": "Room not found"}), 404
        data       = request.get_json(silent=True) or {}
        stream_idx = int(data.get("stream_idx", 0))
        site       = str(data.get("site", "local"))

        if site == "local":
            inputs = getattr(monitor.app_cfg, 'inputs', []) or []
            if stream_idx < 0 or stream_idx >= len(inputs):
                return jsonify({"error": "Invalid stream index"}), 400
            inp = inputs[stream_idx]
            name = (getattr(inp, 'name', None) or getattr(inp, 'stream_name', None)
                    or getattr(inp, 'device_index', None) or f"Input {stream_idx}")
            stream_url = f"/stream/{stream_idx}/live"
            _log(f"[IPLink] Feed: room '{room['name']}' ← local input [{stream_idx}] {name}")
        else:
            hub_server_ref = ctx.get("hub_server")
            if not hub_server_ref:
                return jsonify({"error": "hub_server not available"}), 500
            sites  = getattr(hub_server_ref, '_sites', {})
            sdata  = sites.get(site, {})
            if not sdata.get('_approved'):
                return jsonify({"error": f"Site '{site}' not connected"}), 400
            site_streams = sdata.get('streams', [])
            if stream_idx >= len(site_streams):
                return jsonify({"error": "Invalid stream index for this site"}), 400
            site_enc   = _urlparse.quote(site, safe='')
            stream_url = f"/hub/site/{site_enc}/stream/{stream_idx}/live"
            _log(f"[IPLink] Feed: room '{room['name']}' ← {site}[{stream_idx}]")

        return jsonify({"ok": True, "stream_url": stream_url})

    # ── Room output config ──────────────────────────────────────────────────────
    @app.post("/api/iplink/room/<room_id>/output")
    @login_required
    @csrf_protect
    def iplink_set_output(room_id):
        """Set the audio output routing for a room (speaker | livewire | multicast).
        For livewire/multicast, 'site' can be 'hub' (local UDP multicast) or a
        connected client site name (hub queues an lw_output command; client runs
        the _LivewireSender on its own network segment — e.g. the Axia LAN).
        """
        room = _get_room(room_id)
        if not room:
            return jsonify({"error": "Not found"}), 404
        data     = request.get_json(silent=True) or {}
        out_type = str(data.get("type", "speaker"))
        site_out = str(data.get("site", "hub")).strip() or "hub"
        output   = {"type": out_type, "site": site_out}

        if out_type in ("livewire", "multicast"):
            ch      = max(1, min(32767, int(data.get("channel", 1))))
            address = str(data.get("address", "") or _LivewireSender.livewire_address(ch)).strip()
            port    = max(1, min(65535, int(data.get("port", 5004))))
            n_ch    = 2   # Livewire standard channels are stereo
            output.update({"channel": ch, "address": address, "port": port, "n_ch": n_ch})

        # ── Tear down old output ─────────────────────────────────────────────
        old_sender = _lw_senders.pop(room_id, None)
        if old_sender:
            old_sender.stop()
        # Close any old relay slot
        old_out = room.get("output", {})
        old_slot_id = old_out.get("_slot_id")
        if old_slot_id:
            _lw_relay_closed.add(old_slot_id)
            old_q = _lw_relay_slots.pop(old_slot_id, None)
            if old_q:
                try: old_q.put_nowait(None)   # sentinel to unblock streaming reader
                except _queue.Full: pass

        # ── Start new output ─────────────────────────────────────────────────
        if out_type in ("livewire", "multicast"):
            if site_out == "hub":
                # Local UDP multicast from the hub process
                if room.get("status") == "connected":
                    sender = _LivewireSender(address, port, n_ch=n_ch)
                    _lw_senders[room_id] = sender
                    _log(f"[IPLink] Livewire sender started (hub-local): room '{room['name']}' → {address}:{port}")
            else:
                # Remote client site: create relay queue + queue command
                slot_id = str(uuid.uuid4())
                q = _queue.Queue(maxsize=300)   # ~30 s buffer at 10 Hz
                _lw_relay_slots[slot_id] = q
                _lw_relay_closed.discard(slot_id)
                output["_slot_id"] = slot_id   # transient — not saved to disk
                _pending_feeds[site_out] = {
                    "type":    "lw_output",
                    "slot_id": slot_id,
                    "address": address,
                    "port":    port,
                    "n_ch":    n_ch,
                }
                _log(f"[IPLink] Livewire relay queued for {site_out}: room '{room['name']}' → {address}:{port} slot={slot_id[:8]}")

        with _lock:
            room["output"] = output
        if room.get("permanent"):
            _save_rooms()
        _log(f"[IPLink] Room '{room['name']}' output set to {out_type} via {site_out}")
        return jsonify({"ok": True, "output": {k: v for k, v in output.items() if not k.startswith("_")}})

    # ── Server-managed routing (permanent rooms only) ──────────────────────────

    @app.post("/api/iplink/room/<room_id>/source")
    @login_required
    @csrf_protect
    def iplink_set_source(room_id):
        """Save the server-side audio source for a permanent room and restart
        the source pipeline.  Non-permanent rooms ignore this call."""
        room = _get_room(room_id)
        if not room:
            return jsonify({"error": "Not found"}), 404
        if not room.get("permanent"):
            return jsonify({"error": "Only permanent rooms support server-side source"}), 400

        data     = request.get_json(silent=True) or {}
        src_type = str(data.get("type", "none"))

        if src_type in ("none", ""):
            source = None
        elif src_type == "stream":
            site = str(data.get("site", "local")).strip() or "local"
            idx  = int(data.get("idx", 0))
            name = str(data.get("name", "")).strip()
            source = {"type": "stream", "site": site, "idx": idx, "name": name}
        else:
            return jsonify({"error": "Invalid source type"}), 400

        with _lock:
            room["source"] = source
        if room.get("permanent"):
            _save_rooms()

        # Restart the source pipeline if server_managed is on
        if room.get("server_managed"):
            _stop_source_routing(room_id)
            if source:
                _start_source_routing(room_id)

        _log(f"[IPLink] Room '{room['name']}' source → {source}")
        return jsonify({"ok": True, "source": source})

    @app.post("/api/iplink/room/<room_id>/server_managed")
    @login_required
    @csrf_protect
    def iplink_set_server_managed(room_id):
        """Enable or disable server-managed routing for a permanent room."""
        room = _get_room(room_id)
        if not room:
            return jsonify({"error": "Not found"}), 404
        if not room.get("permanent"):
            return jsonify({"error": "Only permanent rooms support server-managed mode"}), 400

        data    = request.get_json(silent=True) or {}
        enabled = bool(data.get("enabled", False))

        with _lock:
            room["server_managed"] = enabled

        if room.get("permanent"):
            _save_rooms()

        if enabled:
            if _AIORTC:
                _ensure_aio_loop()
            if room.get("source"):
                _start_source_routing(room_id)
        else:
            _stop_source_routing(room_id)

        _log(f"[IPLink] Room '{room['name']}' server_managed → {enabled}")
        return jsonify({"ok": True, "server_managed": enabled,
                        "webrtc_available": _AIORTC and _HAS_NP})

    @app.get("/api/iplink/capabilities")
    @login_required
    def iplink_capabilities():
        """Return server capabilities for the IP Link plugin."""
        return jsonify({
            "server_webrtc":    _AIORTC and bool(_ServerAudioTrack),
            "server_routing":   True,
            "aiortc_installed": _AIORTC,
            "numpy_installed":  _HAS_NP,
        })

    # ── Output PCM chunk (browser posts received audio for Livewire relay) ─────
    @app.post("/api/iplink/room/<room_id>/output_chunk")
    def iplink_output_chunk(room_id):
        """Receive S16LE PCM from the hub browser and forward to Livewire multicast.

        Routing:
          output.site == "hub"          → feed directly to local _LivewireSender (UDP multicast)
          output.site == "<client_name>" → put into relay queue; client streams it from
                                           /api/iplink/output_stream/<slot_id> and multicasts
                                           on its own network segment
        """
        room = _get_room(room_id)
        if not room:
            return jsonify({"error": "Not found"}), 404
        out      = room.get("output", {})
        out_type = out.get("type", "speaker")
        if out_type not in ("livewire", "multicast"):
            return jsonify({"ok": True, "dropped": True})

        pcm = request.get_data()
        if not pcm:
            return jsonify({"ok": True})

        site_out = out.get("site", "hub")
        if site_out == "hub":
            # Local multicast
            sender = _lw_senders.get(room_id)
            if not sender:
                # Lazy create (room may have been configured while not yet connected)
                sender = _LivewireSender(
                    out["address"], out.get("port", 5004), n_ch=out.get("n_ch", 2)
                )
                _lw_senders[room_id] = sender
            sender.feed(pcm)
        else:
            # Remote client site relay
            slot_id = out.get("_slot_id")
            if slot_id:
                q = _lw_relay_slots.get(slot_id)
                if q:
                    try:
                        q.put_nowait(pcm)
                    except _queue.Full:
                        pass   # drop chunk — client is too slow / disconnected

        return jsonify({"ok": True})

    # ── Client polls hub for pending feed commands ─────────────────────────────
    @app.get("/api/iplink/feed_cmd")
    def iplink_feed_cmd():
        """Client site polls this to receive pending feed commands from the hub."""
        site = request.headers.get("X-Site", "").strip()
        if not site:
            return jsonify({}), 400
        cmd = _pending_feeds.pop(site, None)
        return jsonify({"cmd": cmd} if cmd else {})

    # ── Output sites list ──────────────────────────────────────────────────────
    @app.get("/api/iplink/output_sites")
    @login_required
    def iplink_output_sites():
        """Return sites available for Livewire output routing.
        Hub (local multicast) is always first; approved connected client sites follow.
        """
        sites = [{"id": "hub", "name": "Hub (local multicast)"}]
        hub_server_ref = ctx.get("hub_server")
        if hub_server_ref:
            for site, sdata in getattr(hub_server_ref, '_sites', {}).items():
                if sdata.get('_approved'):
                    sites.append({"id": site, "name": site})
        return jsonify({"sites": sites})

    # ── Output PCM stream (hub→client Livewire relay) ──────────────────────────
    @app.get("/api/iplink/output_stream/<slot_id>")
    def iplink_output_stream(slot_id):
        """Streaming endpoint: client reads S16LE PCM and sends it as Livewire multicast.
        Auth: slot_id is a UUID pre-shared in the lw_output command — no session needed.
        """
        q = _lw_relay_slots.get(slot_id)
        if q is None:
            return jsonify({"error": "Not found"}), 404

        def _generate():
            try:
                while slot_id not in _lw_relay_closed:
                    try:
                        chunk = q.get(timeout=2.0)
                        if chunk is None:   # sentinel — stream closed
                            break
                        yield chunk
                    except _queue.Empty:
                        pass   # keep alive while slot stays open
            except GeneratorExit:
                pass

        resp = make_response(_generate())
        resp.headers['Content-Type'] = 'application/octet-stream'
        resp.headers['X-Accel-Buffering'] = 'no'
        resp.headers['Cache-Control'] = 'no-cache'
        return resp

    # ── SIP config API ─────────────────────────────────────────────────────────
    @app.get("/api/iplink/sip/config")
    @login_required
    def iplink_sip_get_config():
        cfg = _load_sip_cfg()
        out = {k: v for k, v in cfg.items() if k != "password"}
        out["_autopass"] = cfg.get("password", "") if cfg.get("enabled") else ""
        return jsonify(out)

    @app.post("/api/iplink/sip/config")
    @login_required
    @csrf_protect
    def iplink_sip_save_config():
        data = request.get_json(silent=True) or {}
        cfg = _load_sip_cfg()
        for k in ("enabled", "server", "username", "domain", "display_name"):
            if k in data:
                cfg[k] = data[k]
        # Only overwrite password if a non-empty value was sent
        if data.get("password"):
            cfg["password"] = data["password"]
        _save_sip_cfg(cfg)
        _log(f"[IPLink] SIP config saved — server: {cfg.get('server','')}, user: {cfg.get('username','')}")
        return jsonify({"ok": True})

    _log(f"[IPLink] Plugin registered — v1.3.5 — mode={_mode} — {len(_STUN_SERVERS)} STUN server(s)")
