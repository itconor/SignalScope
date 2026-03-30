# icecast.py — SignalScope Icecast2 streaming manager plugin
# Drop alongside signalscope.py

SIGNALSCOPE_PLUGIN = {
    "id":       "icecast",
    "label":    "Icecast",
    "url":      "/icecast",
    "icon":     "📡",
    "version":  "1.1.0",
    "hub_only": False,
}

import hashlib as _hashlib
import hmac as _hmac_mod
import json
import numpy as _np
import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
import urllib.request as _urllib_req
import uuid

from flask import abort, jsonify, redirect, render_template_string, request

# ─── Constants ────────────────────────────────────────────────────────────────
_CONFIG_FILE  = "icecast_config.json"
_ICECAST_XML  = "/tmp/signalscope_icecast.xml"
_STATUS_INTERVAL = 15     # seconds between Icecast status polls
_REPORT_INTERVAL = 30     # seconds between hub status POSTs
_CMD_INTERVAL    = 10     # seconds between hub cmd polls
_MOUNT_RE        = re.compile(r'^/[a-zA-Z0-9/_\-]{1,63}$')
_DEFAULT_CFG = {
    "port": 8000,
    "source_password": "signalscope",
    "admin_password": "signalscope_admin",
    "hostname": "localhost",
    "streams": [],
}

# ─── Module-level state ───────────────────────────────────────────────────────
_icecast_proc: "subprocess.Popen | None" = None
_stream_threads: dict = {}  # stream_id → IcecastStreamThread
_stream_status: dict = {}   # stream_id → {listeners, connected, start_time, error}
_client_statuses: dict = {} # hub side: site → status dict
_pending_cmds: dict  = {}   # hub side: site → list[dict]
_lock = threading.Lock()

_cfg: dict = {}
_cfg_lock = threading.Lock()

_monitor    = None
_app_dir: str = ""


# ─── Helpers: logging ─────────────────────────────────────────────────────────

def _log(msg: str) -> None:
    if _monitor:
        _monitor.log(msg)


# ─── Config persistence ───────────────────────────────────────────────────────

def _cfg_path() -> str:
    return os.path.join(_app_dir, _CONFIG_FILE)


def _load_cfg() -> dict:
    path = _cfg_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # merge defaults for any missing top-level keys
            merged = dict(_DEFAULT_CFG)
            merged.update({k: v for k, v in data.items() if k in _DEFAULT_CFG})
            merged["streams"] = data.get("streams", [])
            return merged
        except Exception as exc:
            _log(f"[Icecast] Config load error: {exc}")
    return dict(_DEFAULT_CFG)


def _save_cfg(cfg: dict) -> None:
    path = _cfg_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as exc:
        _log(f"[Icecast] Config save error: {exc}")


def _get_cfg() -> dict:
    with _cfg_lock:
        return dict(_cfg)


def _set_cfg(new_cfg: dict) -> None:
    with _cfg_lock:
        _cfg.clear()
        _cfg.update(new_cfg)
    _save_cfg(new_cfg)


# ─── HMAC helpers (same pattern as logger.py) ─────────────────────────────────

def _make_sig(secret: str, data: bytes, ts: float) -> str:
    key = _hashlib.sha256(f"{secret}:signing".encode()).digest()
    msg = f"{ts:.0f}:".encode() + data
    return _hmac_mod.new(key, msg, _hashlib.sha256).hexdigest()


def _check_sig(secret: str, data: bytes) -> bool:
    """Verify X-Hub-Sig on an incoming client request. Returns True if no secret."""
    if not secret:
        return True
    sig  = request.headers.get("X-Hub-Sig", "")
    ts_h = request.headers.get("X-Hub-Ts", "0")
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


# ─── Mount point sanitiser ────────────────────────────────────────────────────

def _sanitise_mount(raw: str) -> "str | None":
    s = raw.strip()
    if not s.startswith("/"):
        s = "/" + s
    if not _MOUNT_RE.match(s):
        return None
    return s[:64]


def _sanitise_port(raw) -> "int | None":
    try:
        p = int(raw)
    except (TypeError, ValueError):
        return None
    return p if 1024 <= p <= 65535 else None


# ─── Icecast2 XML config generation ──────────────────────────────────────────

def _write_icecast_xml(cfg: dict) -> str:
    port     = int(cfg.get("port", 8000))
    src_pw   = cfg.get("source_password", "signalscope")
    adm_pw   = cfg.get("admin_password", "signalscope_admin")
    hostname = cfg.get("hostname", "localhost")

    mount_blocks = ""
    for s in cfg.get("streams", []):
        if not s.get("enabled", True):
            continue
        mount   = s.get("mount", "/stream")
        name    = s.get("name", "Stream")
        desc    = s.get("description", "")
        genre   = s.get("genre", "Radio")
        public  = "1" if s.get("public", False) else "0"
        mount_blocks += f"""
    <mount type="normal">
        <mount-name>{mount}</mount-name>
        <stream-name>{name}</stream-name>
        <stream-description>{desc}</stream-description>
        <genre>{genre}</genre>
        <public>{public}</public>
    </mount>
"""

    xml = f"""<icecast>
    <location>Earth</location>
    <admin>icemaster@localhost</admin>
    <limits>
        <clients>100</clients>
        <sources>10</sources>
        <queue-size>524288</queue-size>
        <client-timeout>30</client-timeout>
        <header-timeout>15</header-timeout>
        <source-timeout>10</source-timeout>
        <burst-on-connect>1</burst-on-connect>
        <burst-size>65536</burst-size>
    </limits>
    <authentication>
        <source-password>{src_pw}</source-password>
        <relay-password>{src_pw}</relay-password>
        <admin-user>admin</admin-user>
        <admin-password>{adm_pw}</admin-password>
    </authentication>
    <hostname>{hostname}</hostname>
    <listen-socket>
        <port>{port}</port>
    </listen-socket>
    <http-headers>
        <header name="Access-Control-Allow-Origin" value="*"/>
    </http-headers>
{mount_blocks}
    <fileserve>1</fileserve>
    <paths>
        <basedir>/usr/share/icecast2</basedir>
        <logdir>/var/log/icecast2</logdir>
        <webroot>/usr/share/icecast2/web</webroot>
        <adminroot>/usr/share/icecast2/admin</adminroot>
        <pidfile>/tmp/signalscope_icecast.pid</pidfile>
    </paths>
    <logging>
        <accesslog>-</accesslog>
        <errorlog>-</errorlog>
        <loglevel>2</loglevel>
        <logsize>10000</logsize>
    </logging>
    <security>
        <chroot>0</chroot>
    </security>
</icecast>
"""
    with open(_ICECAST_XML, "w", encoding="utf-8") as f:
        f.write(xml)
    return _ICECAST_XML


# ─── Icecast2 process management ─────────────────────────────────────────────

def _icecast_running() -> bool:
    global _icecast_proc
    with _lock:
        proc = _icecast_proc
    if proc is None:
        return False
    if proc.poll() is not None:
        return False
    # Also try connecting to the port
    cfg = _get_cfg()
    port = int(cfg.get("port", 8000))
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=1)
        s.close()
        return True
    except OSError:
        return False


def _start_icecast() -> "tuple[bool, str]":
    global _icecast_proc
    if _icecast_running():
        return True, "Already running"

    icecast_bin = shutil.which("icecast2") or shutil.which("icecast")
    if not icecast_bin:
        return False, "icecast2 not found in PATH"

    cfg = _get_cfg()
    try:
        xml_path = _write_icecast_xml(cfg)
    except Exception as exc:
        return False, f"Config write error: {exc}"

    try:
        proc = subprocess.Popen(
            [icecast_bin, "-c", xml_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        return False, f"Failed to start icecast2: {exc}"

    with _lock:
        _icecast_proc = proc

    # wait up to 3 s for port to open
    for _ in range(15):
        time.sleep(0.2)
        if _icecast_running():
            _log("[Icecast] icecast2 started")
            return True, "Started"

    return False, "icecast2 started but port did not open in time"


def _stop_icecast() -> "tuple[bool, str]":
    global _icecast_proc
    with _lock:
        proc = _icecast_proc
        _icecast_proc = None

    if proc is None or proc.poll() is not None:
        return True, "Not running"

    try:
        proc.send_signal(signal.SIGTERM)
    except OSError:
        pass

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            _log("[Icecast] icecast2 stopped")
            return True, "Stopped"
        time.sleep(0.1)

    try:
        proc.kill()
    except OSError:
        pass
    _log("[Icecast] icecast2 killed")
    return True, "Killed"


# ─── ffmpeg stream thread management ─────────────────────────────────────────

def urllib_quote(s: str) -> str:
    import urllib.parse
    return urllib.parse.quote(str(s), safe="")


class IcecastStreamThread(threading.Thread):
    """Worker thread: taps inp._stream_buffer → ffmpeg stdin → Icecast2.

    Input routing:
    - HTTP/HTTPS inputs: URL passed directly to ffmpeg (-re -i <url>).
      Native codec is preserved which can be stereo.
    - All other inputs (FM, DAB, ALSA, RTP, …): tap inp._stream_buffer,
      a rolling deque of mono float32 chunks at 48 kHz (same approach as
      the Logger plugin).  Selecting stereo=True still works here by
      letting ffmpeg upmix the mono channel to dual-mono L=R.

    The thread self-restarts after failure with a 5-second delay, until
    stop() is called.
    """

    def __init__(self, stream_id: str, stream_cfg: dict, ice_cfg: dict,
                 monitor_ref) -> None:
        super().__init__(daemon=True, name=f"IcecastStream-{stream_id}")
        self.stream_id   = stream_id
        self.stream_cfg  = dict(stream_cfg)
        self.ice_cfg     = dict(ice_cfg)
        self.monitor_ref = monitor_ref
        self._stop_evt   = threading.Event()

    def stop(self) -> None:
        self._stop_evt.set()

    def _find_buffer(self, input_name: str):
        """Return the _stream_buffer deque for the named input, or None."""
        try:
            inputs = getattr(self.monitor_ref.app_cfg, "inputs", []) or []
            for inp in inputs:
                if getattr(inp, "name", "") == input_name:
                    return getattr(inp, "_stream_buffer", None)
        except Exception:
            pass
        return None

    def run(self) -> None:  # noqa: C901
        sid          = self.stream_id
        input_name   = self.stream_cfg.get("input_name", "")
        input_device = self.stream_cfg.get("input_device", "")
        bitrate      = int(self.stream_cfg.get("bitrate", 128))
        fmt          = self.stream_cfg.get("format", "mp3")
        mount        = self.stream_cfg.get("mount", "/stream")
        stereo       = bool(self.stream_cfg.get("stereo", False))
        name         = self.stream_cfg.get("name", "Stream")
        genre        = self.stream_cfg.get("genre", "Radio")

        src_pw = self.ice_cfg.get("source_password", "signalscope")
        port   = int(self.ice_cfg.get("port", 8000))

        # Always connect to localhost — the source password is embedded in the URL
        dest = (f"icecast://source:{src_pw}@127.0.0.1:{port}{mount}"
                f"?name={urllib_quote(name)}&ice-genre={urllib_quote(genre)}")

        channels = 2 if stereo else 1

        if fmt == "ogg":
            codec_args = ["-vn", "-c:a", "libopus",    "-b:a", f"{bitrate}k",
                          "-ac", str(channels), "-f", "ogg"]
        else:
            codec_args = ["-vn", "-c:a", "libmp3lame", "-b:a", f"{bitrate}k",
                          "-ac", str(channels), "-f", "mp3"]

        # Route: direct URL (HTTP inputs) or PCM pipe (everything else)
        dev = input_device.strip()
        use_url = dev.startswith("http://") or dev.startswith("https://")

        if use_url:
            # Feed stream URL directly to ffmpeg — native audio codec preserved,
            # which may be stereo.  -re throttles to real-time playback speed.
            input_args = ["-re", "-i", dev]
        else:
            # Mono float32 48 kHz PCM via stdin — works for FM, DAB, ALSA, RTP, …
            input_args = ["-f", "f32le", "-ar", "48000", "-ac", "1", "-i", "pipe:0"]

        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            _log(f"[Icecast] Stream {sid}: ffmpeg not found — aborting thread")
            return

        cmd = ([ffmpeg_bin, "-hide_banner", "-loglevel", "error"]
               + input_args + codec_args + [dest])

        while not self._stop_evt.is_set():
            proc  = None
            stdin = None
            try:
                if use_url:
                    proc = subprocess.Popen(cmd,
                                            stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL)
                else:
                    proc = subprocess.Popen(cmd,
                                            stdin=subprocess.PIPE,
                                            stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL)
                    stdin = proc.stdin

                with _lock:
                    _stream_status.setdefault(sid, {}).update(
                        {"start_time": time.time(), "error": ""})

                if use_url:
                    while not self._stop_evt.is_set():
                        if proc.poll() is not None:
                            break
                        self._stop_evt.wait(1.0)
                else:
                    # Snapshot+anchor drain loop — identical to logger.py pattern.
                    # Never calls popleft() so the buffer can be shared with the
                    # Logger plugin or any other consumer simultaneously.
                    last_ref = None
                    while not self._stop_evt.is_set():
                        buf = self._find_buffer(input_name)
                        if buf is None:
                            self._stop_evt.wait(1.0)
                            continue
                        chunks = list(buf)          # thread-safe snapshot
                        if not chunks:
                            self._stop_evt.wait(0.2)
                            continue
                        if last_ref is None:
                            last_ref = chunks[-1]   # anchor at current tail
                            new_chunks: list = []
                        else:
                            idx = next(
                                (i for i, c in enumerate(chunks) if c is last_ref),
                                None,
                            )
                            if idx is None:
                                new_chunks = chunks  # buffer lapped
                            else:
                                new_chunks = chunks[idx + 1:]
                            if new_chunks:
                                last_ref = new_chunks[-1]
                        if new_chunks:
                            raw = b"".join(c.astype(_np.float32).tobytes()
                                           for c in new_chunks)
                            try:
                                stdin.write(raw)
                                stdin.flush()
                            except (BrokenPipeError, OSError):
                                break
                        if proc.poll() is not None:
                            break
                        self._stop_evt.wait(0.1)

            except Exception as exc:
                _log(f"[Icecast] Stream {sid} error: {exc}")
                with _lock:
                    _stream_status.setdefault(sid, {})["error"] = str(exc)
            finally:
                if stdin:
                    try:
                        stdin.close()
                    except OSError:
                        pass
                if proc:
                    try:
                        proc.send_signal(signal.SIGTERM)
                    except OSError:
                        pass
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        try:
                            proc.kill()
                        except OSError:
                            pass

            if not self._stop_evt.is_set():
                _log(f"[Icecast] Stream {sid} exited; restarting in 5 s")
                self._stop_evt.wait(5.0)

        _log(f"[Icecast] Stream {sid} thread stopped")


def _start_stream(stream_id: str) -> "tuple[bool, str]":
    cfg     = _get_cfg()
    streams = cfg.get("streams", [])
    stream  = next((s for s in streams if s.get("id") == stream_id), None)
    if stream is None:
        return False, "Stream not found"
    if not stream.get("enabled", True):
        return False, "Stream is disabled"

    with _lock:
        existing = _stream_threads.get(stream_id)
    if existing and existing.is_alive():
        return True, "Already running"

    if not shutil.which("ffmpeg"):
        return False, "ffmpeg not found in PATH"

    with _lock:
        _stream_status[stream_id] = {
            "listeners": 0, "connected": False,
            "start_time": time.time(), "error": "",
        }

    t = IcecastStreamThread(stream_id, stream, cfg, _monitor)
    with _lock:
        _stream_threads[stream_id] = t
    t.start()
    _log(f"[Icecast] Stream {stream_id} ({stream.get('name')}) thread started")
    return True, "Started"


def _stop_stream(stream_id: str) -> "tuple[bool, str]":
    with _lock:
        t = _stream_threads.pop(stream_id, None)

    if t is None or not t.is_alive():
        return True, "Not running"

    t.stop()
    t.join(timeout=5.0)
    if t.is_alive():
        _log(f"[Icecast] Stream {stream_id} thread did not exit in 5 s")
    _log(f"[Icecast] Stream {stream_id} stopped")
    return True, "Stopped"


# ─── Icecast2 HTTP status polling ─────────────────────────────────────────────

def _poll_icecast_status(port: int) -> dict:
    """Query Icecast2 /status-json.xsl and return mount → {listeners, connected}."""
    url = f"http://127.0.0.1:{port}/status-json.xsl"
    try:
        req = _urllib_req.Request(url, headers={"User-Agent": "SignalScope"})
        with _urllib_req.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return {}

    result = {}
    icestats = data.get("icestats", {})
    sources = icestats.get("source", [])
    if isinstance(sources, dict):
        sources = [sources]
    for src in sources:
        mount = src.get("listenurl", "")
        # listenurl is e.g. http://localhost:8000/coolfm — extract path
        try:
            from urllib.parse import urlparse
            mount = urlparse(mount).path
        except Exception:
            pass
        result[mount] = {
            "listeners": src.get("listeners", 0),
            "connected": True,
        }
    return result


# ─── Background thread: status collector ──────────────────────────────────────

def _status_collector_thread() -> None:
    """Polls Icecast HTTP status every _STATUS_INTERVAL seconds.
    Stream threads self-restart on failure; this thread only updates
    listener counts and connected state from the Icecast HTTP API."""
    while True:
        try:
            cfg  = _get_cfg()
            port = int(cfg.get("port", 8000))

            if _icecast_running():
                mount_status = _poll_icecast_status(port)
            else:
                mount_status = {}

            with _lock:
                for s in cfg.get("streams", []):
                    sid      = s.get("id", "")
                    ms       = mount_status.get(s.get("mount", ""), {})
                    existing = _stream_status.get(sid, {})
                    _stream_status[sid] = {
                        "listeners":  ms.get("listeners", 0),
                        "connected":  ms.get("connected", False),
                        "start_time": existing.get("start_time"),
                        "error":      existing.get("error", ""),
                    }
        except Exception as exc:
            _log(f"[Icecast] Status collector error: {exc}")

        time.sleep(_STATUS_INTERVAL)


# ─── Background thread: hub reporter ──────────────────────────────────────────

def _hub_reporter_thread(monitor) -> None:
    """Posts client status to hub every _REPORT_INTERVAL seconds."""
    while True:
        time.sleep(_REPORT_INTERVAL)
        try:
            cfg_ss = monitor.app_cfg
            hub_url = getattr(getattr(cfg_ss, "hub", None), "hub_url", "").rstrip("/")
            if not hub_url:
                continue
            site   = getattr(getattr(cfg_ss, "hub", None), "site_name", "") or ""
            secret = getattr(getattr(cfg_ss, "hub", None), "secret_key", "") or ""

            ice_cfg = _get_cfg()
            with _lock:
                st_copy = dict(_stream_status)
                sp_copy = {k: v.is_alive() for k, v in _stream_threads.items()}

            streams_out = []
            for s in ice_cfg.get("streams", []):
                sid = s.get("id", "")
                st  = st_copy.get(sid, {})
                streams_out.append({
                    "id":        sid,
                    "name":      s.get("name", ""),
                    "mount":     s.get("mount", ""),
                    "format":    s.get("format", "mp3"),
                    "bitrate":   s.get("bitrate", 128),
                    "enabled":   s.get("enabled", True),
                    "running":   sp_copy.get(sid, False),
                    "listeners": st.get("listeners", 0),
                    "connected": st.get("connected", False),
                    "start_time": st.get("start_time"),
                })

            payload = {
                "site":    site,
                "running": _icecast_running(),
                "port":    int(ice_cfg.get("port", 8000)),
                "streams": streams_out,
                "ts":      time.time(),
                "inputs":  _get_inputs_list(monitor),
            }
            body = json.dumps(payload).encode("utf-8")
            ts   = time.time()
            sig  = _make_sig(secret, body, ts) if secret else ""

            req = _urllib_req.Request(
                f"{hub_url}/api/icecast/client_status",
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "X-Site":       site,
                    "X-Hub-Ts":     f"{ts:.0f}",
                    "X-Hub-Sig":    sig,
                },
            )
            with _urllib_req.urlopen(req, timeout=10) as resp:
                resp.read()
        except Exception as exc:
            _log(f"[Icecast] Hub reporter error: {exc}")


# ─── Background thread: hub cmd poller ────────────────────────────────────────

def _cmd_poller_thread(monitor) -> None:
    """Polls hub for pending commands every _CMD_INTERVAL seconds."""
    while True:
        time.sleep(_CMD_INTERVAL)
        try:
            cfg_ss  = monitor.app_cfg
            hub_url = getattr(getattr(cfg_ss, "hub", None), "hub_url", "").rstrip("/")
            if not hub_url:
                continue
            site   = getattr(getattr(cfg_ss, "hub", None), "site_name", "") or ""
            secret = getattr(getattr(cfg_ss, "hub", None), "secret_key", "") or ""

            ts  = time.time()
            sig = _make_sig(secret, b"", ts) if secret else ""
            req = _urllib_req.Request(
                f"{hub_url}/api/icecast/cmd",
                headers={
                    "X-Site":    site,
                    "X-Hub-Ts":  f"{ts:.0f}",
                    "X-Hub-Sig": sig,
                },
            )
            with _urllib_req.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))

            cmds = data.get("cmds", [])
            for cmd in cmds:
                _handle_cmd(cmd, monitor)
        except Exception as exc:
            _log(f"[Icecast] Cmd poller error: {exc}")


def _handle_cmd(cmd: dict, monitor) -> None:
    action = cmd.get("action", "")
    sid    = cmd.get("stream_id", "")
    try:
        if action == "start_server":
            ok, msg = _start_icecast()
            _log(f"[Icecast] Hub cmd start_server: {msg}")
        elif action == "stop_server":
            ok, msg = _stop_icecast()
            _log(f"[Icecast] Hub cmd stop_server: {msg}")
        elif action == "start_stream":
            ok, msg = _start_stream(sid)
            _log(f"[Icecast] Hub cmd start_stream {sid}: {msg}")
        elif action == "stop_stream":
            ok, msg = _stop_stream(sid)
            _log(f"[Icecast] Hub cmd stop_stream {sid}: {msg}")
        elif action == "update_config":
            new_cfg = cmd.get("config", {})
            if new_cfg:
                _set_cfg(new_cfg)
                _log("[Icecast] Hub cmd update_config applied")
        elif action == "add_stream":
            stream_data = cmd.get("stream", {})
            if stream_data:
                _add_stream_to_cfg(stream_data)
                _log(f"[Icecast] Hub cmd add_stream: {stream_data.get('name')}")
        else:
            _log(f"[Icecast] Unknown hub cmd action: {action}")
    except Exception as exc:
        _log(f"[Icecast] Error handling cmd {action}: {exc}")


def _add_stream_to_cfg(stream_data: dict) -> None:
    with _cfg_lock:
        if not _cfg.get("streams"):
            _cfg["streams"] = []
        _cfg["streams"].append(stream_data)
        _save_cfg(dict(_cfg))


# ─── Inputs helper ────────────────────────────────────────────────────────────

def _get_inputs_list(monitor) -> list:
    """Return input list for the UI dropdown.
    'device' is the raw device_index / URL used to decide whether the
    IcecastStreamThread should use a direct URL or the _stream_buffer tap.
    """
    try:
        inputs = getattr(monitor.app_cfg, "inputs", []) or []
        result = []
        for inp in inputs:
            name   = getattr(inp, "name", "") or ""
            device = (getattr(inp, "device_index", "") or
                      getattr(inp, "url", "") or "")
            buf    = getattr(inp, "_stream_buffer", None)
            result.append({
                "name":       name,
                "device":     device,
                "has_buffer": buf is not None,
            })
        return result
    except Exception:
        return []


# ─── HTML templates ───────────────────────────────────────────────────────────

_CLIENT_TPL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<title>Icecast Streaming — SignalScope</title>
<style nonce="{{csp_nonce()}}">
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(circle at top,#12376f 0%,var(--bg) 38%,#05101f 100%);color:var(--tx);font-family:system-ui,sans-serif;font-size:14px;min-height:100vh}
a{color:var(--acc);text-decoration:none}
header{background:linear-gradient(180deg,rgba(10,31,65,.96),rgba(9,24,48,.96));border-bottom:1px solid var(--bor);padding:12px 20px;display:flex;align-items:center;gap:12px}
header h1{font-size:16px;font-weight:600;color:var(--tx);flex:1}
.main{padding:20px 24px;max-width:1200px;margin:0 auto}
.card{background:var(--sur);border:1px solid var(--bor);border-radius:12px;overflow:hidden;margin-bottom:16px}
.ch{padding:9px 14px;display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--bor);background:linear-gradient(180deg,#143766,#102b54);font-size:12px;font-weight:700;color:var(--acc);text-transform:uppercase;letter-spacing:.06em}
.cb{padding:14px}
.status-bar{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.badge{font-size:11px;padding:2px 8px;border-radius:999px}
.b-ok{background:#0f2318;color:var(--ok);border:1px solid #166534}
.b-al{background:#2a0a0a;color:var(--al);border:1px solid #991b1b}
.b-mu{background:var(--bor);color:var(--mu)}
.btn{border:none;border-radius:8px;padding:5px 12px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit}
.btn:hover{filter:brightness(1.15)}
.bp{background:var(--acc);color:#fff}
.bd{background:var(--al);color:#fff}
.bg{background:var(--bor);color:var(--tx)}
.bs{padding:3px 9px;font-size:12px}
details>summary{list-style:none}
details>summary::-webkit-details-marker{display:none}
.field{display:flex;flex-direction:column;gap:4px}
.field label{font-size:11px;color:var(--mu);font-weight:600;text-transform:uppercase;letter-spacing:.05em}
input,select{background:#0d1e40;border:1px solid var(--bor);border-radius:6px;color:var(--tx);padding:6px 9px;font-size:13px;outline:none;font-family:inherit}
input:focus,select:focus{border-color:var(--acc)}
table{width:100%;border-collapse:collapse;font-size:13px}
th{padding:7px 10px;text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--mu);border-bottom:1px solid var(--bor)}
td{padding:8px 10px;border-bottom:1px solid var(--bor);vertical-align:middle}
tr:hover td{background:rgba(23,52,95,.35)}
.url-link{font-size:12px;color:var(--acc);font-family:monospace}
#msg{padding:8px 14px;border-radius:8px;margin-bottom:12px;display:none;font-size:13px}
.msg-ok{background:#0f2318;color:var(--ok);border:1px solid #166534}
.msg-err{background:#2a0a0a;color:var(--al);border:1px solid #991b1b}
</style>
</head>
<body>
<header>
  <h1>📡 Icecast Streaming</h1>
  <a href="/" class="btn bg bs">← Dashboard</a>
</header>
<div class="main">
  <div id="msg"></div>

  <!-- Server Status Card -->
  <div class="card">
    <div class="ch">Server</div>
    <div class="cb">
      <div class="status-bar">
        <span id="srv-badge" class="badge b-mu">Checking…</span>
        <span id="srv-port" style="font-size:12px;color:var(--mu)"></span>
        <button class="btn bp bs" data-action="server-start">Start</button>
        <button class="btn bd bs" data-action="server-stop">Stop</button>
      </div>
    </div>
  </div>

  <!-- Server Settings Card (collapsible) -->
  <div class="card">
    <details>
      <summary class="ch" style="cursor:pointer">Server Settings</summary>
      <div class="cb">
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
          <div class="field"><label>Port</label><input type="number" id="cfg-port" min="1024" max="65535"></div>
          <div class="field"><label>Hostname</label><input type="text" id="cfg-hostname"></div>
          <div class="field"><label>Source Password</label><input type="text" id="cfg-srcpw"></div>
          <div class="field"><label>Admin Password</label><input type="text" id="cfg-admpw"></div>
        </div>
        <div style="margin-top:10px">
          <button class="btn bp bs" data-action="save-config">Save Settings</button>
        </div>
      </div>
    </details>
  </div>

  <!-- Streams Table Card -->
  <div class="card">
    <div class="ch">Streams</div>
    <div class="cb" style="padding:0">
      <table>
        <thead>
          <tr>
            <th>Name</th><th>Input</th><th>Mount</th><th>Format</th>
            <th>Kbps</th><th>Ch</th><th>Status</th><th>Listeners</th><th>URL</th><th>Actions</th>
          </tr>
        </thead>
        <tbody id="streams-tbody">
          <tr><td colspan="10" style="color:var(--mu);text-align:center;padding:14px">Loading…</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- Add Stream Card -->
  <div class="card">
    <div class="ch">Add Stream</div>
    <div class="cb">
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px" id="add-form">
        <div class="field">
          <label>Name</label>
          <input type="text" id="add-name" placeholder="Cool FM">
        </div>
        <div class="field">
          <label>Input</label>
          <select id="add-input"></select>
        </div>
        <div class="field">
          <label>Mount Point</label>
          <input type="text" id="add-mount" placeholder="/coolfm">
        </div>
        <div class="field">
          <label>Format</label>
          <select id="add-format">
            <option value="mp3">MP3</option>
            <option value="ogg">OGG/Opus</option>
          </select>
        </div>
        <div class="field">
          <label>Bitrate (kbps)</label>
          <select id="add-bitrate">
            <option value="64">64</option>
            <option value="96">96</option>
            <option value="128" selected>128</option>
            <option value="192">192</option>
            <option value="320">320</option>
          </select>
        </div>
        <div class="field">
          <label>Genre</label>
          <input type="text" id="add-genre" placeholder="Radio" value="Radio">
        </div>
        <div class="field">
          <label>Description</label>
          <input type="text" id="add-desc" placeholder="">
        </div>
        <div class="field" style="justify-content:flex-end">
          <label>&nbsp;</label>
          <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap">
            <label style="font-size:12px;color:var(--mu);display:flex;gap:5px;align-items:center"
                   title="HTTP inputs: native stereo preserved. FM/DAB/ALSA/RTP: mono is upmixed to dual-mono L=R.">
              <input type="checkbox" id="add-stereo"> Stereo
            </label>
            <label style="font-size:12px;color:var(--mu);display:flex;gap:5px;align-items:center">
              <input type="checkbox" id="add-public"> Public
            </label>
            <button class="btn bp" data-action="add-stream">Add Stream</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>

<script nonce="{{csp_nonce()}}">
var _cfg={port:8000,hostname:'localhost'};
var _refreshTimer=null;

function _getCsrf(){
  return (document.querySelector('meta[name="csrf-token"]')||{}).content
      || (document.cookie.match(/(?:^|;\\s*)csrf_token=([^;]+)/)||[])[1]
      || '';
}

function _showMsg(txt,ok){
  var el=document.getElementById('msg');
  el.textContent=txt;
  el.className=ok?'msg-ok':'msg-err';
  el.style.display='block';
  clearTimeout(_showMsg._t);
  _showMsg._t=setTimeout(function(){el.style.display='none';},4000);
}

function _post(url,body,cb){
  fetch(url,{method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRFToken':_getCsrf()},
    body:JSON.stringify(body)})
  .then(function(r){return r.json();})
  .then(function(d){cb(null,d);})
  .catch(function(e){cb(e,null);});
}

function _refresh(){
  fetch('/api/icecast/status',{credentials:'same-origin'})
  .then(function(r){return r.json();})
  .then(function(d){_render(d);})
  .catch(function(){});
}

function _render(d){
  // server badge
  var badge=document.getElementById('srv-badge');
  var portEl=document.getElementById('srv-port');
  if(d.running){
    badge.className='badge b-ok'; badge.textContent='Running';
  } else {
    badge.className='badge b-al'; badge.textContent='Stopped';
  }
  portEl.textContent=d.running?'Port '+d.port:'Port '+d.port+' (not listening)';
  _cfg.port=d.port; _cfg.hostname=d.hostname||'localhost';

  // settings fields (only populate if empty to avoid disrupting editing)
  if(!document.activeElement||document.activeElement.tagName!=='INPUT'){
    document.getElementById('cfg-port').value=d.port||8000;
    document.getElementById('cfg-hostname').value=d.hostname||'localhost';
    document.getElementById('cfg-srcpw').value=d.source_password||'';
    document.getElementById('cfg-admpw').value=d.admin_password||'';
  }

  // streams table
  var tbody=document.getElementById('streams-tbody');
  if(!d.streams||d.streams.length===0){
    tbody.innerHTML='<tr><td colspan="9" style="color:var(--mu);text-align:center;padding:14px">No streams configured</td></tr>';
    return;
  }
  var html='';
  d.streams.forEach(function(s){
    var stBadge=s.running
      ? '<span class="badge b-ok">On Air</span>'
      : (s.enabled?'<span class="badge b-mu">Idle</span>':'<span class="badge b-al">Disabled</span>');
    var streamUrl='http://'+(_cfg.hostname||'localhost')+':'+_cfg.port+s.mount;
    var chLabel=s.stereo?'<span title="Stereo" style="color:var(--ok)">2</span>':'<span title="Mono" style="color:var(--mu)">1</span>';
    html+='<tr>'
      +'<td>'+_esc(s.name)+'</td>'
      +'<td style="font-size:12px;color:var(--mu)">'+_esc(s.input_name||'')+'</td>'
      +'<td style="font-family:monospace;font-size:12px">'+_esc(s.mount)+'</td>'
      +'<td>'+_esc(s.format.toUpperCase())+'</td>'
      +'<td>'+s.bitrate+'</td>'
      +'<td style="text-align:center">'+chLabel+'</td>'
      +'<td>'+stBadge+'</td>'
      +'<td style="text-align:center">'+(s.connected?s.listeners:'\u2013')+'</td>'
      +'<td><a class="url-link" href="'+streamUrl+'" target="_blank">'+_esc(streamUrl)+'</a></td>'
      +'<td style="white-space:nowrap">'
      +(s.running
        ? '<button class="btn bd bs stream-stop-btn" data-id="'+_esc(s.id)+'">Stop</button>'
        : '<button class="btn bp bs stream-start-btn" data-id="'+_esc(s.id)+'">Start</button>')
      +' <button class="btn bg bs stream-del-btn" data-id="'+_esc(s.id)+'" data-name="'+_esc(s.name)+'">Del</button>'
      +'</td>'
      +'</tr>';
  });
  tbody.innerHTML=html;
}

function _esc(s){
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// Load inputs for add-stream dropdown
function _loadInputs(){
  fetch('/api/icecast/inputs',{credentials:'same-origin'})
  .then(function(r){return r.json();})
  .then(function(d){
    var sel=document.getElementById('add-input');
    sel.innerHTML='<option value="">— select input —</option>';
    (d.inputs||[]).forEach(function(inp){
      var opt=document.createElement('option');
      opt.value=inp.device;
      var isUrl=inp.device&&(inp.device.startsWith('http://')||inp.device.startsWith('https://'));
      var hint=isUrl?' [URL \u2014 native stereo]':(inp.has_buffer?' [PCM tap]':' [no buffer]');
      opt.textContent=inp.name+hint;
      opt.dataset.name=inp.name;
      sel.appendChild(opt);
    });
  })
  .catch(function(){});
}

// Auto-suggest mount from name
document.getElementById('add-name').addEventListener('input',function(){
  var m=this.value.toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/(^-|-$)/g,'');
  if(m) document.getElementById('add-mount').value='/'+m;
});

// Event delegation
document.body.addEventListener('click',function(e){
  var el=e.target;

  if(el.dataset.action==='server-start'){
    _post('/api/icecast/server/start',{},function(err,d){
      if(err||!d.ok){_showMsg((d&&d.error)||'Error starting server',false);}
      else{_showMsg('Icecast2 started',true);_refresh();}
    });
    return;
  }
  if(el.dataset.action==='server-stop'){
    _post('/api/icecast/server/stop',{},function(err,d){
      if(err||!d.ok){_showMsg((d&&d.error)||'Error stopping server',false);}
      else{_showMsg('Icecast2 stopped',true);_refresh();}
    });
    return;
  }
  if(el.dataset.action==='save-config'){
    var port=parseInt(document.getElementById('cfg-port').value,10);
    var hostname=document.getElementById('cfg-hostname').value.trim();
    var srcpw=document.getElementById('cfg-srcpw').value;
    var admpw=document.getElementById('cfg-admpw').value;
    if(!port||port<1024||port>65535){_showMsg('Invalid port',false);return;}
    _post('/api/icecast/config/save',{port:port,hostname:hostname,
      source_password:srcpw,admin_password:admpw},function(err,d){
      if(err||!d.ok){_showMsg((d&&d.error)||'Save failed',false);}
      else{_showMsg('Settings saved',true);_refresh();}
    });
    return;
  }
  if(el.dataset.action==='add-stream'){
    var name=document.getElementById('add-name').value.trim();
    var selInp=document.getElementById('add-input');
    var device=selInp.value;
    var inputName=selInp.options[selInp.selectedIndex]?selInp.options[selInp.selectedIndex].dataset.name||device:'';
    var mount=document.getElementById('add-mount').value.trim();
    var fmt=document.getElementById('add-format').value;
    var br=parseInt(document.getElementById('add-bitrate').value,10);
    var genre=document.getElementById('add-genre').value.trim()||'Radio';
    var desc=document.getElementById('add-desc').value.trim();
    var pub=document.getElementById('add-public').checked;
    var stereo=document.getElementById('add-stereo').checked;
    if(!name){_showMsg('Name is required',false);return;}
    if(!mount){_showMsg('Mount point is required',false);return;}
    _post('/api/icecast/stream/add',{name:name,input_device:device,
      input_name:inputName,mount:mount,format:fmt,bitrate:br,
      stereo:stereo,genre:genre,description:desc,public:pub,enabled:true},function(err,d){
      if(err||!d.ok){_showMsg((d&&d.error)||'Add failed',false);}
      else{_showMsg('Stream added',true);_refresh();}
    });
    return;
  }

  var startBtn=el.closest('.stream-start-btn');
  if(startBtn){
    _post('/api/icecast/stream/start',{id:startBtn.dataset.id},function(err,d){
      if(err||!d.ok){_showMsg((d&&d.error)||'Start failed',false);}
      else{_showMsg('Stream started',true);_refresh();}
    });
    return;
  }
  var stopBtn=el.closest('.stream-stop-btn');
  if(stopBtn){
    _post('/api/icecast/stream/stop',{id:stopBtn.dataset.id},function(err,d){
      if(err||!d.ok){_showMsg((d&&d.error)||'Stop failed',false);}
      else{_showMsg('Stream stopped',true);_refresh();}
    });
    return;
  }
  var delBtn=el.closest('.stream-del-btn');
  if(delBtn){
    if(!confirm('Delete stream "'+delBtn.dataset.name+'"?')) return;
    _post('/api/icecast/stream/delete',{id:delBtn.dataset.id},function(err,d){
      if(err||!d.ok){_showMsg((d&&d.error)||'Delete failed',false);}
      else{_showMsg('Stream deleted',true);_refresh();}
    });
    return;
  }
});

_loadInputs();
_refresh();
_refreshTimer=setInterval(_refresh,10000);
</script>
</body>
</html>"""

_HUB_TPL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<title>Icecast Overview — SignalScope</title>
<style nonce="{{csp_nonce()}}">
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(circle at top,#12376f 0%,var(--bg) 38%,#05101f 100%);color:var(--tx);font-family:system-ui,sans-serif;font-size:14px;min-height:100vh}
a{color:var(--acc);text-decoration:none}
header{background:linear-gradient(180deg,rgba(10,31,65,.96),rgba(9,24,48,.96));border-bottom:1px solid var(--bor);padding:12px 20px;display:flex;align-items:center;gap:12px}
header h1{font-size:16px;font-weight:600;color:var(--tx);flex:1}
.main{padding:20px 24px;max-width:1400px;margin:0 auto}
.card{background:var(--sur);border:1px solid var(--bor);border-radius:12px;overflow:hidden;margin-bottom:16px}
.ch{padding:9px 14px;display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--bor);background:linear-gradient(180deg,#143766,#102b54);font-size:12px;font-weight:700;color:var(--acc);text-transform:uppercase;letter-spacing:.06em}
.cb{padding:14px}
.badge{font-size:11px;padding:2px 8px;border-radius:999px}
.b-ok{background:#0f2318;color:var(--ok);border:1px solid #166534}
.b-al{background:#2a0a0a;color:var(--al);border:1px solid #991b1b}
.b-mu{background:var(--bor);color:var(--mu)}
.btn{border:none;border-radius:8px;padding:5px 12px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit}
.btn:hover{filter:brightness(1.15)}
.bp{background:var(--acc);color:#fff}
.bd{background:var(--al);color:#fff}
.bg{background:var(--bor);color:var(--tx)}
.bs{padding:3px 9px;font-size:12px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{padding:7px 10px;text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--mu);border-bottom:1px solid var(--bor)}
td{padding:8px 10px;border-bottom:1px solid var(--bor);vertical-align:middle}
tr:hover td{background:rgba(23,52,95,.35)}
.detail-row{background:rgba(7,20,43,.6)!important;display:none}
.detail-row.open{display:table-row}
.sub-table{width:100%;border-collapse:collapse;font-size:12px;margin:4px 0}
.sub-table th{padding:5px 8px;color:var(--mu);font-size:11px;border-bottom:1px solid var(--bor)}
.sub-table td{padding:5px 8px;border-bottom:1px solid var(--bor)}
.modal-bg{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:100;align-items:center;justify-content:center}
.modal-bg.open{display:flex}
.modal{background:var(--sur);border:1px solid var(--bor);border-radius:12px;padding:20px;min-width:360px;max-width:500px;width:90%}
.modal h2{font-size:12px;color:var(--acc);margin-bottom:14px;text-transform:uppercase;letter-spacing:.06em;font-weight:700}
.field{display:flex;flex-direction:column;gap:4px;margin-bottom:10px}
.field label{font-size:11px;color:var(--mu);font-weight:600;text-transform:uppercase;letter-spacing:.05em}
input,select{background:#0d1e40;border:1px solid var(--bor);border-radius:6px;color:var(--tx);padding:6px 9px;font-size:13px;outline:none;font-family:inherit}
input:focus,select:focus{border-color:var(--acc)}
.modal-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:14px}
#msg{padding:8px 14px;border-radius:8px;margin-bottom:12px;display:none;font-size:13px}
.msg-ok{background:#0f2318;color:var(--ok);border:1px solid #166534}
.msg-err{background:#2a0a0a;color:var(--al);border:1px solid #991b1b}
</style>
</head>
<body>
<header>
  <h1>📡 Icecast Overview</h1>
  <a href="/" class="btn bg bs">← Dashboard</a>
</header>
<div class="main">
  <div id="msg"></div>
  <div class="card">
    <div class="ch">All Sites</div>
    <div class="cb" style="padding:0">
      <table>
        <thead>
          <tr>
            <th></th>
            <th>Site</th>
            <th>Server</th>
            <th>Streams</th>
            <th>Total Listeners</th>
            <th>Last Updated</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody id="sites-tbody">
          <tr><td colspan="7" style="color:var(--mu);text-align:center;padding:14px">Loading…</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>

<!-- Add stream modal -->
<div class="modal-bg" id="add-modal">
  <div class="modal">
    <h2>Add Stream</h2>
    <input type="hidden" id="modal-site">
    <div class="field"><label>Name</label><input type="text" id="modal-name" placeholder="Cool FM"></div>
    <div class="field"><label>Input</label><select id="modal-input"></select></div>
    <div class="field"><label>Mount Point</label><input type="text" id="modal-mount" placeholder="/coolfm"></div>
    <div class="field"><label>Format</label>
      <select id="modal-format"><option value="mp3">MP3</option><option value="ogg">OGG/Opus</option></select>
    </div>
    <div class="field"><label>Bitrate (kbps)</label>
      <select id="modal-bitrate">
        <option value="64">64</option><option value="96">96</option>
        <option value="128" selected>128</option><option value="192">192</option><option value="320">320</option>
      </select>
    </div>
    <div class="field"><label>Genre</label><input type="text" id="modal-genre" value="Radio"></div>
    <div class="field"><label>Description</label><input type="text" id="modal-desc"></div>
    <div class="field">
      <label style="display:flex;gap:6px;align-items:center;cursor:pointer;text-transform:none;font-size:12px"
             title="HTTP inputs: native stereo. FM/DAB/ALSA/RTP: mono upmixed to dual-mono.">
        <input type="checkbox" id="modal-stereo"> Stereo output
      </label>
    </div>
    <div class="modal-actions">
      <button class="btn bg bs" id="modal-cancel">Cancel</button>
      <button class="btn bp bs" id="modal-add">Add Stream</button>
    </div>
  </div>
</div>

<script nonce="{{csp_nonce()}}">
var _openSite=null;

function _getCsrf(){
  return (document.querySelector('meta[name="csrf-token"]')||{}).content
      || (document.cookie.match(/(?:^|;\\s*)csrf_token=([^;]+)/)||[])[1]
      || '';
}
function _showMsg(txt,ok){
  var el=document.getElementById('msg');
  el.textContent=txt; el.className=ok?'msg-ok':'msg-err'; el.style.display='block';
  clearTimeout(_showMsg._t);
  _showMsg._t=setTimeout(function(){el.style.display='none';},4000);
}
function _post(url,body,cb){
  fetch(url,{method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRFToken':_getCsrf()},
    body:JSON.stringify(body)})
  .then(function(r){return r.json();})
  .then(function(d){cb(null,d);})
  .catch(function(e){cb(e,null);});
}
function _esc(s){
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function _ago(ts){
  if(!ts) return '\u2014';
  var d=Math.round(Date.now()/1000-ts);
  if(d<5) return 'just now';
  if(d<60) return d+'s ago';
  if(d<3600) return Math.round(d/60)+'m ago';
  return Math.round(d/3600)+'h ago';
}

function _refresh(){
  fetch('/api/hub/icecast/overview',{credentials:'same-origin'})
  .then(function(r){return r.json();})
  .then(function(d){_render(d.sites||{});})
  .catch(function(){});
}

function _render(sites){
  var tbody=document.getElementById('sites-tbody');
  var keys=Object.keys(sites);
  if(!keys.length){
    tbody.innerHTML='<tr><td colspan="7" style="color:var(--mu);text-align:center;padding:14px">No client sites reporting Icecast status</td></tr>';
    return;
  }
  var html='';
  keys.forEach(function(site){
    var s=sites[site];
    var running=s.running;
    var streams=s.streams||[];
    var totalListeners=streams.reduce(function(a,st){return a+(st.listeners||0);},0);
    var srvBadge=running
      ?'<span class="badge b-ok">Running</span>'
      :'<span class="badge b-al">Stopped</span>';
    var rowId='dr-'+site.replace(/[^a-z0-9]/gi,'_');
    html+='<tr>'
      +'<td><button class="btn bg bs expand-btn" data-site="'+_esc(site)+'" data-row="'+rowId+'">\u25b6</button></td>'
      +'<td><strong>'+_esc(site)+'</strong></td>'
      +'<td>'+srvBadge+'</td>'
      +'<td>'+streams.length+'</td>'
      +'<td>'+totalListeners+'</td>'
      +'<td style="font-size:12px;color:var(--mu)">'+_ago(s.ts)+'</td>'
      +'<td style="white-space:nowrap">'
      +(running
        ?'<button class="btn bd bs hub-stop-srv" data-site="'+_esc(site)+'">Stop Server</button> '
        :'<button class="btn bp bs hub-start-srv" data-site="'+_esc(site)+'">Start Server</button> ')
      +'<button class="btn bp bs hub-add-stream" data-site="'+_esc(site)+'">+ Stream</button>'
      +'</td>'
      +'</tr>'
      +'<tr class="detail-row" id="'+rowId+'">'
      +'<td colspan="7" style="padding:0 10px 10px 32px">'
      +_renderStreams(site,streams,s.port||8000,s.hostname||site)
      +'</td></tr>';
  });
  tbody.innerHTML=html;
}

function _renderStreams(site,streams,port,hostname){
  if(!streams.length) return '<em style="color:var(--mu);font-size:12px">No streams</em>';
  var html='<table class="sub-table"><thead><tr>'
    +'<th>Name</th><th>Mount</th><th>Format</th><th>Kbps</th>'
    +'<th>Status</th><th>Listeners</th><th>URL</th><th>Actions</th>'
    +'</tr></thead><tbody>';
  streams.forEach(function(s){
    var stBadge=s.running
      ?'<span class="badge b-ok">On Air</span>'
      :(s.enabled?'<span class="badge b-mu">Idle</span>'
        :'<span class="badge b-al">Disabled</span>');
    var url='http://'+hostname+':'+port+s.mount;
    html+='<tr>'
      +'<td>'+_esc(s.name)+'</td>'
      +'<td style="font-family:monospace">'+_esc(s.mount)+'</td>'
      +'<td>'+_esc(s.format.toUpperCase())+'</td>'
      +'<td>'+s.bitrate+'</td>'
      +'<td>'+stBadge+'</td>'
      +'<td style="text-align:center">'+(s.connected?s.listeners:'\u2013')+'</td>'
      +'<td><a href="'+url+'" target="_blank" style="font-size:11px;font-family:monospace;color:var(--acc)">'+_esc(url)+'</a></td>'
      +'<td style="white-space:nowrap">'
      +(s.running
        ?'<button class="btn bd bs hub-stop-str" data-site="'+_esc(site)+'" data-id="'+_esc(s.id)+'">Stop</button>'
        :'<button class="btn bp bs hub-start-str" data-site="'+_esc(site)+'" data-id="'+_esc(s.id)+'">Start</button>')
      +'</td>'
      +'</tr>';
  });
  html+='</tbody></table>';
  return html;
}

// Event delegation
document.body.addEventListener('click',function(e){
  var el=e.target;

  var expBtn=el.closest('.expand-btn');
  if(expBtn){
    var rowId=expBtn.dataset.row;
    var row=document.getElementById(rowId);
    if(row){
      var isOpen=row.classList.contains('open');
      row.classList.toggle('open',!isOpen);
      expBtn.textContent=isOpen?'\u25b6':'\u25bc';
    }
    return;
  }

  var startSrv=el.closest('.hub-start-srv');
  if(startSrv){
    _post('/api/hub/icecast/cmd',{site:startSrv.dataset.site,action:'start_server'},function(err,d){
      if(err||!d.ok){_showMsg((d&&d.error)||'Error',false);}
      else{_showMsg('Command queued',true);}
    });
    return;
  }
  var stopSrv=el.closest('.hub-stop-srv');
  if(stopSrv){
    _post('/api/hub/icecast/cmd',{site:stopSrv.dataset.site,action:'stop_server'},function(err,d){
      if(err||!d.ok){_showMsg((d&&d.error)||'Error',false);}
      else{_showMsg('Command queued',true);}
    });
    return;
  }
  var startStr=el.closest('.hub-start-str');
  if(startStr){
    _post('/api/hub/icecast/cmd',{site:startStr.dataset.site,
      action:'start_stream',stream_id:startStr.dataset.id},function(err,d){
      if(err||!d.ok){_showMsg((d&&d.error)||'Error',false);}
      else{_showMsg('Command queued',true);}
    });
    return;
  }
  var stopStr=el.closest('.hub-stop-str');
  if(stopStr){
    _post('/api/hub/icecast/cmd',{site:stopStr.dataset.site,
      action:'stop_stream',stream_id:stopStr.dataset.id},function(err,d){
      if(err||!d.ok){_showMsg((d&&d.error)||'Error',false);}
      else{_showMsg('Command queued',true);}
    });
    return;
  }

  var addBtn=el.closest('.hub-add-stream');
  if(addBtn){
    var site=addBtn.dataset.site;
    document.getElementById('modal-site').value=site;
    document.getElementById('modal-name').value='';
    document.getElementById('modal-mount').value='';
    document.getElementById('modal-genre').value='Radio';
    document.getElementById('modal-desc').value='';
    document.getElementById('modal-stereo').checked=false;
    // load inputs for this site from stored status
    fetch('/api/hub/icecast/overview',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      var inputs=(d.sites[site]||{}).inputs||[];
      var sel=document.getElementById('modal-input');
      sel.innerHTML='<option value="">— select input —</option>';
      inputs.forEach(function(inp){
        var opt=document.createElement('option');
        opt.value=inp.device;
        var isUrl=inp.device&&(inp.device.startsWith('http://')||inp.device.startsWith('https://'));
        var hint=isUrl?' [URL \u2014 native stereo]':(inp.has_buffer?' [PCM tap]':' [no buffer]');
        opt.textContent=inp.name+hint;
        opt.dataset.name=inp.name; sel.appendChild(opt);
      });
    });
    document.getElementById('add-modal').classList.add('open');
    return;
  }
});

document.getElementById('modal-cancel').addEventListener('click',function(){
  document.getElementById('add-modal').classList.remove('open');
});
document.getElementById('add-modal').addEventListener('click',function(e){
  if(e.target===this) this.classList.remove('open');
});
document.getElementById('modal-name').addEventListener('input',function(){
  var m=this.value.toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/(^-|-$)/g,'');
  if(m) document.getElementById('modal-mount').value='/'+m;
});
document.getElementById('modal-add').addEventListener('click',function(){
  var site=document.getElementById('modal-site').value;
  var name=document.getElementById('modal-name').value.trim();
  var selInp=document.getElementById('modal-input');
  var device=selInp.value;
  var inputName=selInp.options[selInp.selectedIndex]?selInp.options[selInp.selectedIndex].dataset.name||device:'';
  var mount=document.getElementById('modal-mount').value.trim();
  var fmt=document.getElementById('modal-format').value;
  var br=parseInt(document.getElementById('modal-bitrate').value,10);
  var genre=document.getElementById('modal-genre').value.trim()||'Radio';
  var desc=document.getElementById('modal-desc').value.trim();
  var stereo=document.getElementById('modal-stereo').checked;
  if(!name||!site){_showMsg('Name and site are required',false);return;}
  if(!mount){_showMsg('Mount point is required',false);return;}
  _post('/api/hub/icecast/cmd',{
    site:site, action:'add_stream',
    stream:{
      id:Math.random().toString(36).slice(2,10),
      name:name, input_device:device, input_name:inputName,
      mount:mount, format:fmt, bitrate:br, stereo:stereo, genre:genre,
      description:desc, enabled:true, public:false
    }
  },function(err,d){
    if(err||!d.ok){_showMsg((d&&d.error)||'Add failed',false);}
    else{
      _showMsg('Stream add command queued',true);
      document.getElementById('add-modal').classList.remove('open');
    }
  });
});

_refresh();
setInterval(_refresh,15000);
</script>
</body>
</html>"""


# ─── register() ───────────────────────────────────────────────────────────────

def register(app, ctx):
    global _monitor, _app_dir

    login_required   = ctx["login_required"]
    csrf_protect     = ctx["csrf_protect"]
    monitor          = ctx["monitor"]
    hub_server       = ctx["hub_server"]
    mobile_api_req   = ctx.get("mobile_api_required", ctx["login_required"])

    _monitor  = monitor
    _app_dir  = os.path.dirname(os.path.abspath(__file__))

    # Load config from disk
    with _cfg_lock:
        loaded = _load_cfg()
        _cfg.update(loaded)

    _log("[Icecast] Plugin loaded")

    cfg_ss = monitor.app_cfg
    mode   = getattr(getattr(cfg_ss, "hub", None), "mode", "standalone") or "standalone"

    # ── Client-side routes ────────────────────────────────────────────────────

    @app.get("/icecast")
    @login_required
    def icecast_page():
        if hub_server is not None and mode == "hub":
            return redirect("/hub/icecast")
        return render_template_string(_CLIENT_TPL)

    @app.get("/api/icecast/status")
    @login_required
    def icecast_api_status():
        ice_cfg = _get_cfg()
        running = _icecast_running()
        with _lock:
            st_copy = dict(_stream_status)
            sp_copy = {k: v.is_alive() for k, v in _stream_threads.items()}

        streams_out = []
        for s in ice_cfg.get("streams", []):
            sid = s.get("id", "")
            st  = st_copy.get(sid, {})
            streams_out.append({
                "id":         sid,
                "name":       s.get("name", ""),
                "input_name": s.get("input_name", ""),
                "mount":      s.get("mount", ""),
                "format":     s.get("format", "mp3"),
                "bitrate":    s.get("bitrate", 128),
                "stereo":     s.get("stereo", False),
                "enabled":    s.get("enabled", True),
                "running":    sp_copy.get(sid, False),
                "listeners":  st.get("listeners", 0),
                "connected":  st.get("connected", False),
                "start_time": st.get("start_time"),
            })

        return jsonify({
            "running":          running,
            "port":             int(ice_cfg.get("port", 8000)),
            "hostname":         ice_cfg.get("hostname", "localhost"),
            "source_password":  "***" if ice_cfg.get("source_password") else "",
            "admin_password":   "***" if ice_cfg.get("admin_password") else "",
            "streams":          streams_out,
        })

    @app.post("/api/icecast/server/start")
    @login_required
    @csrf_protect
    def icecast_server_start():
        ok, msg = _start_icecast()
        if ok:
            return jsonify({"ok": True, "msg": msg})
        return jsonify({"ok": False, "error": msg}), 500

    @app.post("/api/icecast/server/stop")
    @login_required
    @csrf_protect
    def icecast_server_stop():
        ok, msg = _stop_icecast()
        return jsonify({"ok": ok, "msg": msg})

    @app.post("/api/icecast/stream/add")
    @login_required
    @csrf_protect
    def icecast_stream_add():
        body = request.get_json(silent=True) or {}
        mount = _sanitise_mount(body.get("mount", ""))
        if not mount:
            return jsonify({"ok": False, "error": "Invalid mount point"}), 400
        name = str(body.get("name", "")).strip()
        if not name:
            return jsonify({"ok": False, "error": "Name required"}), 400

        stream = {
            "id":           uuid.uuid4().hex[:8],
            "name":         name,
            "input_device": str(body.get("input_device", "")),
            "input_name":   str(body.get("input_name", "")),
            "mount":        mount,
            "bitrate":      int(body.get("bitrate", 128)),
            "format":       "ogg" if body.get("format") == "ogg" else "mp3",
            "stereo":       bool(body.get("stereo", False)),
            "enabled":      bool(body.get("enabled", True)),
            "description":  str(body.get("description", "")),
            "genre":        str(body.get("genre", "Radio")),
            "public":       bool(body.get("public", False)),
        }
        with _cfg_lock:
            _cfg.setdefault("streams", []).append(stream)
            _save_cfg(dict(_cfg))
        _log(f"[Icecast] Stream added: {name} → {mount}")
        return jsonify({"ok": True, "id": stream["id"]})

    @app.post("/api/icecast/stream/update")
    @login_required
    @csrf_protect
    def icecast_stream_update():
        body = request.get_json(silent=True) or {}
        sid  = str(body.get("id", "")).strip()
        if not sid:
            return jsonify({"ok": False, "error": "id required"}), 400
        with _cfg_lock:
            streams = _cfg.get("streams", [])
            for s in streams:
                if s.get("id") == sid:
                    if "name" in body:
                        s["name"] = str(body["name"]).strip()
                    if "mount" in body:
                        m = _sanitise_mount(body["mount"])
                        if not m:
                            return jsonify({"ok": False, "error": "Invalid mount"}), 400
                        s["mount"] = m
                    if "bitrate" in body:
                        s["bitrate"] = int(body["bitrate"])
                    if "format" in body:
                        s["format"] = "ogg" if body["format"] == "ogg" else "mp3"
                    if "enabled" in body:
                        s["enabled"] = bool(body["enabled"])
                    if "genre" in body:
                        s["genre"] = str(body["genre"])
                    if "description" in body:
                        s["description"] = str(body["description"])
                    if "public" in body:
                        s["public"] = bool(body["public"])
                    if "stereo" in body:
                        s["stereo"] = bool(body["stereo"])
                    if "input_device" in body:
                        s["input_device"] = str(body["input_device"])
                    if "input_name" in body:
                        s["input_name"] = str(body["input_name"])
                    _save_cfg(dict(_cfg))
                    return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "Stream not found"}), 404

    @app.post("/api/icecast/stream/delete")
    @login_required
    @csrf_protect
    def icecast_stream_delete():
        body = request.get_json(silent=True) or {}
        sid  = str(body.get("id", "")).strip()
        if not sid:
            return jsonify({"ok": False, "error": "id required"}), 400
        # Stop it first if running
        _stop_stream(sid)
        with _cfg_lock:
            before = len(_cfg.get("streams", []))
            _cfg["streams"] = [s for s in _cfg.get("streams", []) if s.get("id") != sid]
            if len(_cfg.get("streams", [])) == before:
                return jsonify({"ok": False, "error": "Stream not found"}), 404
            _save_cfg(dict(_cfg))
        with _lock:
            _stream_status.pop(sid, None)
        _log(f"[Icecast] Stream deleted: {sid}")
        return jsonify({"ok": True})

    @app.post("/api/icecast/stream/start")
    @login_required
    @csrf_protect
    def icecast_stream_start():
        body = request.get_json(silent=True) or {}
        sid  = str(body.get("id", "")).strip()
        ok, msg = _start_stream(sid)
        if ok:
            return jsonify({"ok": True, "msg": msg})
        return jsonify({"ok": False, "error": msg}), 400

    @app.post("/api/icecast/stream/stop")
    @login_required
    @csrf_protect
    def icecast_stream_stop():
        body = request.get_json(silent=True) or {}
        sid  = str(body.get("id", "")).strip()
        ok, msg = _stop_stream(sid)
        return jsonify({"ok": ok, "msg": msg})

    @app.post("/api/icecast/config/save")
    @login_required
    @csrf_protect
    def icecast_config_save():
        body = request.get_json(silent=True) or {}
        port = _sanitise_port(body.get("port"))
        if port is None:
            return jsonify({"ok": False, "error": "Invalid port (1024–65535)"}), 400
        hostname = str(body.get("hostname", "localhost")).strip() or "localhost"
        src_pw   = str(body.get("source_password", "")).strip()
        adm_pw   = str(body.get("admin_password", "")).strip()
        with _cfg_lock:
            _cfg["port"]             = port
            _cfg["hostname"]         = hostname
            if src_pw and src_pw != "***":
                _cfg["source_password"] = src_pw
            if adm_pw and adm_pw != "***":
                _cfg["admin_password"]  = adm_pw
            _save_cfg(dict(_cfg))
        _log(f"[Icecast] Config saved (port={port}, hostname={hostname})")
        return jsonify({"ok": True})

    @app.get("/api/icecast/inputs")
    @login_required
    def icecast_inputs():
        inputs = _get_inputs_list(monitor)
        return jsonify({"inputs": inputs})

    # ── Hub-side routes ───────────────────────────────────────────────────────

    if hub_server is not None:

        @app.get("/hub/icecast")
        @login_required
        def hub_icecast_page():
            return render_template_string(_HUB_TPL)

        @app.get("/api/hub/icecast/overview")
        @login_required
        def hub_icecast_overview():
            with _lock:
                sites_copy = {k: dict(v) for k, v in _client_statuses.items()}
            return jsonify({"sites": sites_copy})

        @app.post("/api/icecast/client_status")
        def icecast_client_status():
            """Called by clients to POST their current Icecast status."""
            raw    = request.get_data()
            secret = (getattr(getattr(monitor.app_cfg, "hub", None),
                              "secret_key", None) or "").strip()

            if secret:
                if not _check_sig(secret, raw):
                    abort(403)
            else:
                # Without a secret: require the site to be known and approved
                site_h = request.headers.get("X-Site", "").strip()
                sdata  = hub_server._sites.get(site_h, {})
                if not sdata.get("_approved"):
                    abort(403)

            try:
                payload = json.loads(raw.decode("utf-8", errors="replace"))
            except Exception:
                abort(400)

            site = str(payload.get("site", "")).strip()
            if not site:
                site = request.headers.get("X-Site", "unknown").strip()

            with _lock:
                _client_statuses[site] = payload

            return jsonify({"ok": True})

        @app.get("/api/icecast/cmd")
        def icecast_cmd_poll():
            """Polled by clients to retrieve pending commands."""
            site   = request.headers.get("X-Site", "").strip()
            secret = (getattr(getattr(monitor.app_cfg, "hub", None),
                              "secret_key", None) or "").strip()

            if secret:
                if not _check_sig(secret, b""):
                    abort(403)
            else:
                sdata = hub_server._sites.get(site, {})
                if not sdata.get("_approved"):
                    abort(403)

            with _lock:
                cmds = list(_pending_cmds.pop(site, []))

            return jsonify({"cmds": cmds})

        @app.post("/api/hub/icecast/cmd")
        @login_required
        @csrf_protect
        def hub_icecast_cmd():
            """Hub operator queues a command for a client site."""
            body   = request.get_json(silent=True) or {}
            site   = str(body.get("site", "")).strip()
            action = str(body.get("action", "")).strip()
            if not site or not action:
                return jsonify({"ok": False, "error": "site and action required"}), 400

            cmd: dict = {"action": action}
            if "stream_id" in body:
                cmd["stream_id"] = str(body["stream_id"])
            if "config" in body:
                cmd["config"] = body["config"]
            if "stream" in body:
                cmd["stream"] = body["stream"]

            with _lock:
                _pending_cmds.setdefault(site, []).append(cmd)

            _log(f"[Icecast] Hub queued cmd '{action}' for site '{site}'")
            return jsonify({"ok": True})

    # ── Mobile route ──────────────────────────────────────────────────────────

    @app.get("/api/mobile/icecast/streams")
    @mobile_api_req
    def mobile_icecast_streams():
        """Return all streams across all sites (hub) or local streams (client)."""
        result = []
        if hub_server is not None:
            with _lock:
                for site, st in _client_statuses.items():
                    for s in st.get("streams", []):
                        hostname = st.get("hostname", site)
                        port     = st.get("port", 8000)
                        result.append({
                            "site":      site,
                            "id":        s.get("id"),
                            "name":      s.get("name"),
                            "mount":     s.get("mount"),
                            "format":    s.get("format"),
                            "bitrate":   s.get("bitrate"),
                            "running":   s.get("running", False),
                            "listeners": s.get("listeners", 0),
                            "url":       f"http://{hostname}:{port}{s.get('mount','')}",
                        })
        else:
            ice_cfg = _get_cfg()
            hostname = ice_cfg.get("hostname", "localhost")
            port     = ice_cfg.get("port", 8000)
            with _lock:
                st_copy = dict(_stream_status)
                sp_copy = {k: v.is_alive() for k, v in _stream_threads.items()}
            for s in ice_cfg.get("streams", []):
                sid = s.get("id", "")
                st  = st_copy.get(sid, {})
                result.append({
                    "site":      getattr(getattr(monitor.app_cfg, "hub", None), "site_name", ""),
                    "id":        sid,
                    "name":      s.get("name"),
                    "mount":     s.get("mount"),
                    "format":    s.get("format"),
                    "bitrate":   s.get("bitrate"),
                    "running":   sp_copy.get(sid, False),
                    "listeners": st.get("listeners", 0),
                    "url":       f"http://{hostname}:{port}{s.get('mount','')}",
                })
        return jsonify({"streams": result})

    # ── Background threads ────────────────────────────────────────────────────

    # Status collector always runs (client side, local polling)
    t_status = threading.Thread(
        target=_status_collector_thread,
        daemon=True,
        name="IcecastStatusCollector",
    )
    t_status.start()

    # Hub reporter and cmd poller only when we have a hub to talk to
    hub_url = getattr(getattr(cfg_ss, "hub", None), "hub_url", "").strip()
    if hub_url and mode in ("client", "both", "standalone"):
        t_reporter = threading.Thread(
            target=_hub_reporter_thread,
            args=(monitor,),
            daemon=True,
            name="IcecastHubReporter",
        )
        t_reporter.start()

        t_poller = threading.Thread(
            target=_cmd_poller_thread,
            args=(monitor,),
            daemon=True,
            name="IcecastCmdPoller",
        )
        t_poller.start()

    _log("[Icecast] Plugin registered successfully")
