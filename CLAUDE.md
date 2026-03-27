# SignalScope — Project Memory

## Project Overview
SignalScope is a broadcast signal intelligence platform. Single Python file (`signalscope.py`) — Flask web app, client/hub architecture, RTL-SDR integration for FM/DAB monitoring.

- **Repo**: https://github.com/itconor/SignalScope
- **Current build string**: `BUILD = "SignalScope-3.4.31"` (increment on every release)
- **Update this file** at the end of any session where bugs are fixed, architecture is discovered, or features are added.
- **Release flow**: bump `BUILD`, update `CHANGELOG.md`, `git commit`, `git push`, `gh release create v{version}`

---

## Plugin System

### How it works
On startup, `_load_plugins()` scans the app directory for any `*.py` file (except `signalscope.py` itself) that contains the string `SIGNALSCOPE_PLUGIN`. Matching files are imported and their `register(app, ctx)` function is called. A nav bar item is injected automatically between hub links and Settings.

Key code locations in `signalscope.py`:
- `_plugins: list[dict]` — module-level registry (line ~11340)
- `_load_plugins()` — discovery and import (line ~11350)
- `_scan_installed_plugins()` — settings page helper (line ~11416)
- `_PLUGIN_REGISTRY_URL` — GitHub `plugins.json` URL (line ~11450)
- Plugin API routes: `/api/plugins`, `/api/plugins/available`, `/api/plugins/install`, `/api/plugins/remove`
- Settings nav button: `b-plugins` / panel: `p-plugins` — outside the `<form>` tag

### Plugin registry
`plugins.json` at repo root lists installable plugins. Users can browse and install via **Settings → Plugins**.

---

## Writing a Plugin

### Minimal skeleton
```python
# myplugin.py — drop alongside signalscope.py

SIGNALSCOPE_PLUGIN = {
    "id":    "myplugin",       # unique slug, matches filename stem
    "label": "My Plugin",      # nav bar label
    "url":   "/hub/myplugin",  # nav bar href
    "icon":  "🔧",             # optional emoji
}

def register(app, ctx):
    """Called once at startup. Register Flask routes here."""
    login_required  = ctx["login_required"]
    csrf_protect    = ctx["csrf_protect"]
    monitor         = ctx["monitor"]         # AppMonitor — access cfg, log, etc.
    hub_server      = ctx["hub_server"]      # HubServer or None
    listen_registry = ctx["listen_registry"] # ListenSlotRegistry
    BUILD           = ctx["BUILD"]           # version string

    @app.get("/hub/myplugin")
    @login_required
    def myplugin_page():
        return "<h1>My Plugin</h1>"
```

### `ctx` keys
| Key | Type | Description |
|-----|------|-------------|
| `app` | Flask | The Flask application |
| `monitor` | AppMonitor | Access `monitor.app_cfg` (config), `monitor.log()`, etc. |
| `hub_server` | HubServer \| None | Hub state, `_sites`, `_scanner_sessions`, etc. |
| `listen_registry` | ListenSlotRegistry | Create/get relay slots |
| `login_required` | decorator | Require authenticated session |
| `csrf_protect` | decorator | Validate CSRF token on POST routes |
| `BUILD` | str | e.g. `"SignalScope-3.3.79"` |

### Accessing config
```python
cfg      = monitor.app_cfg          # AppConfig dataclass
hub_url  = cfg.hub.hub_url          # remote hub URL (client side)
site     = cfg.hub.site_name        # this machine's site name
secret   = cfg.hub.secret_key       # HMAC secret
dongles  = cfg.sdr_devices          # list[SdrDevice]
scanner_dongles = [d for d in cfg.sdr_devices if d.role == "scanner"]
```

### Using the audio relay infrastructure

Plugins can push PCM audio to the browser using the **existing** relay pipeline — no new streaming code needed.

```python
# 1. Create a slot (hub side — usually triggered by a browser POST)
slot = listen_registry.create(
    site_name, 0,
    kind="scanner",                        # reuses scanner relay
    mimetype="application/octet-stream",
    freq_mhz=96.5,                         # metadata only
)
slot_id    = slot.slot_id
stream_url = f"/hub/scanner/stream/{slot_id}"   # give this to the browser

# 2. Push PCM chunks (client side — in a background thread)
#    Format: 16-bit signed LE, mono, 48 kHz, 9600 bytes per block (0.1 s)
import urllib.request, hashlib, hmac as _hmac, time, os

def _sign(secret, data, ts):
    key = hashlib.sha256(f"{secret}:signing".encode()).digest()
    msg = f"{ts:.0f}:".encode() + data
    return _hmac.new(key, msg, hashlib.sha256).hexdigest()

chunk_url = f"{hub_url}/api/v1/audio_chunk/{slot_id}"
ts  = time.time()
sig = _sign(secret, pcm_bytes, ts) if secret else ""
req = urllib.request.Request(chunk_url, data=pcm_bytes, method="POST",
      headers={"Content-Type": "application/octet-stream",
               "X-Hub-Sig": sig, "X-Hub-Ts": f"{ts:.0f}",
               "X-Hub-Nonce": hashlib.md5(os.urandom(8)).hexdigest()[:16]})
urllib.request.urlopen(req, timeout=5).close()

# 3. Browser connects to stream_url — reuse scanner JS audio pump:
#    connectAudio(stream_url)  — 48 kHz, 16-bit PCM, _PRE=1.0 s pre-buffer
```

### Hub ↔ Client communication

The hub cannot directly call the client (NAT). Use the **client-polls-hub** pattern:

```python
# Hub side: queue a command for the client
_pending = {}   # site → cmd dict (module-level)

@app.get("/api/myplugin/cmd")
def myplugin_cmd_poll():
    site = request.headers.get("X-Site", "").strip()
    sdata = hub_server._sites.get(site, {})
    if not sdata.get("_approved"): return jsonify({}), 403
    cmd = _pending.pop(site, None)
    return jsonify({"cmd": cmd} if cmd else {})

# Client side: polling thread (started in register())
def _poller(monitor):
    import urllib.request, json
    while True:
        cfg = monitor.app_cfg
        r = urllib.request.urlopen(
            urllib.request.Request(f"{cfg.hub.hub_url}/api/myplugin/cmd",
                headers={"X-Site": cfg.hub.site_name}), timeout=5)
        d = json.loads(r.read())
        if d.get("cmd"): _handle(d["cmd"])
        time.sleep(3)
```

### SDR dongle access

```python
import shutil, subprocess

rtl_sdr = shutil.which("rtl_sdr")   # or "rtl_fm", "welle-cli", etc.
cmd = [rtl_sdr, "-f", "96500000", "-s", "1024000", "-g", "0", "-n", "0", "-"]
if sdr_serial:
    cmd[1:1] = ["-d", sdr_serial]
proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
# proc.stdout → raw uint8 IQ pairs (I then Q, 127.5-centred)
```

IQ → complex float → demodulate pattern:
```python
import numpy as np
raw = np.frombuffer(proc.stdout.read(204800), dtype=np.uint8)
iq  = (raw[0::2].astype(np.float32) - 127.5 \
     + 1j*(raw[1::2].astype(np.float32) - 127.5)) / 127.5
# WFM demodulate:
demod = np.angle(iq[1:] * np.conj(iq[:-1])) / np.pi
# Resample 1024 kHz → 48 kHz (scipy):
from scipy import signal as sp
audio = sp.resample_poly(demod, 3, 64)   # UP=3, DN=64
pcm   = np.clip(audio * 32767 * 2.5, -32768, 32767).astype(np.int16)
```

### Scanner dongle role

Dongles marked `role = "scanner"` in Settings → SDR Devices are:
- Reported to hub in every heartbeat: `payload["scanner_serials"]`
- Stored per-site in `hub_server._sites[site]["scanner_serials"]`
- Returned by `GET /api/hub/scanner/devices/<site>`
- Used to filter the FM Scanner and Web SDR site selectors

### Browser audio pump (JS — copy this into plugin templates)
```javascript
var _audioCtx=null,_gainNode=null,_reader=null,_nextTime=0;
var _pcmBuf=new Uint8Array(0),_sched=0;
var _SR=48000,_BLK_S=4800,_BLK_B=9600,_PRE=1.0;

function _initAudio(){
  if(_audioCtx)return;
  _audioCtx=new(window.AudioContext||window.webkitAudioContext)({sampleRate:_SR});
  _gainNode=_audioCtx.createGain(); _gainNode.connect(_audioCtx.destination);
}
function connectAudio(url){
  if(_reader){try{_reader.cancel();}catch(e){}_reader=null;}
  _pcmBuf=new Uint8Array(0);_sched=0;
  _initAudio(); if(_audioCtx.state==='suspended')_audioCtx.resume();
  _nextTime=_audioCtx.currentTime+_PRE;
  fetch(url,{credentials:'same-origin'}).then(function(r){
    _reader=r.body.getReader();
    (function pump(){_reader.read().then(function(d){
      if(d.done||!_reader)return;
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
        src.start(t);_nextTime=t+buf.duration;_sched++;
      }
      pump();
    });})();
  });
}
```

### Adding a plugin to the public registry

Add an entry to `plugins.json` at the repo root:
```json
{
  "id":           "myplugin",
  "name":         "My Plugin",
  "file":         "myplugin.py",
  "icon":         "🔧",
  "description":  "What it does.",
  "version":      "1.0.0",
  "requirements": "numpy scipy",
  "url":          "https://raw.githubusercontent.com/itconor/SignalScope/main/myplugin.py"
}
```

Users will see it in **Settings → Plugins → Check GitHub for plugins** and can install with one click.

---

## Known Bugs Fixed (don't reintroduce)

### Plugin Install/Remove buttons blocked by CSP (fixed 3.3.80)
`_compute_csp_hashes()` pre-hashes every `onclick=` value found in `*_TPL` strings at startup.
Two patterns silently blocked by CSP `script-src-attr 'unsafe-hashes'`:
1. Jinja2-rendered onclick values — hash is computed for the literal `{{p.file}}` source text, not the rendered value (e.g. `pluginRemove('sdr.py')`).
2. onclick strings built dynamically in JS at runtime — values depend on the GitHub API response and can never be known at startup.

Fix: Replace all dynamic `onclick=` in the plugins panel with `data-*` attributes and a single delegated listener inside the `<script nonce="{{csp_nonce()}}">` block (covered by the CSP nonce, not subject to hash requirement):
```html
<!-- Remove button — was onclick="pluginRemove('{{p.file}}')" -->
<button class="btn bw bs plugin-rm-btn" data-file="{{p.file|e}}">Remove</button>

<!-- Install button (built in JS) — was onclick="pluginInstall(...)" -->
'<button class="btn bp bs plugin-install-btn" '
+ 'data-id='+JSON.stringify(p.id)+' data-url='+JSON.stringify(p.url)+' data-file='+JSON.stringify(p.file)+'>'
+ '⬇ Install</button>'
```
```javascript
// Inside <script nonce="{{csp_nonce()}}">:
document.getElementById('p-plugins').addEventListener('click', function(e){
  var rm = e.target.closest('.plugin-rm-btn');
  if(rm){ pluginRemove(rm.dataset.file); return; }
  var inst = e.target.closest('.plugin-install-btn');
  if(inst){ pluginInstall(inst.dataset.id, inst.dataset.url, inst.dataset.file); }
});
```
**Rule**: Never add `onclick=` to elements whose handler strings contain runtime-variable content or Jinja2 expressions. Use `data-*` + event delegation instead.
**Rule**: Every plugin template rendered with `render_template_string` MUST include `nonce="{{csp_nonce()}}"` on ALL `<script>` and `<style>` tags, and `<meta name="csrf-token" content="{{csrf_token()}}">` in `<head>`. Without the nonce the browser's CSP silently blocks the entire script/style block. Without the meta tag, CSRF posts will fail on HTTPS hubs (see 3.3.81).

### Plugin active detection mismatch (fixed 3.3.82)
`_scan_installed_plugins()` checked `py.stem in active_ids` where `active_ids` came from `p.get("id")` on loaded plugins. If a plugin's declared `id` differs from its filename stem (e.g. `sdr.py` with `id="websdr"`), the check always returned False — showing "Restart needed" even when the plugin was running.

Fix: `_load_plugins()` now stores `info["_src"] = py.name` on every entry in `_plugins`. `_scan_installed_plugins()` matches by `p.get("_src") == py.name` and sets `active = matched_entry is not None`.

**Rule**: When storing identity for loaded plugins always include `_src = py.name` so that plugins whose `id` doesn't match their filename can be correctly identified as active.

### Hub-only nav items (added 3.3.82)
Plugins that only make sense in hub/both mode can set `"hub_only": True` in their `SIGNALSCOPE_PLUGIN` dict. `topnav()` filters these out when the node is in client-only mode.

```python
SIGNALSCOPE_PLUGIN = {
    "id":       "myplugin",
    "hub_only": True,   # suppress nav item in client-only mode
    ...
}
```

### Plugin Install CSRF fails on HTTPS hub (fixed 3.3.81)
The plugins panel IIFE captured the CSRF token once at page-load time from the cookie:
```javascript
var _csrf = (document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/)||[])[1]||'';
```
On hubs behind an SSL proxy the cookie has `Secure=True; SameSite=Strict` and the captured value could be stale or empty, causing every `_csrfPost()` call to send an empty `X-CSRFToken` header.

Fix: replace with `_getCsrf()` called fresh on every POST — reads the `<meta name="csrf-token">` tag first (server-rendered, always correct), falls back to the cookie:
```javascript
function _getCsrf(){
  return (document.querySelector('meta[name="csrf-token"]')||{}).content
      || (document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/)||[])[1]
      || '';
}
```
**Rule**: Never capture the CSRF token in a variable at IIFE time. Always call `_getCsrf()` (or equivalent) at the point of use so the value is always fresh and the meta tag fallback is available.

### Clip inline player errors — persistent (fixed 3.3.136)
Despite `preload="none"` (3.3.132) and `send_file(conditional=True)` (3.3.133), `<audio>` elements still errored. Root cause: Werkzeug's `make_conditional` Range handling is unreliable with certain browser/proxy combinations — Chrome/Safari issue a `Range: bytes=0-` probe before playback and error if they receive anything other than 206.

Fix: replaced `send_file` entirely with an explicit Range handler in `_serve_clip_wav()`. Reads the file into memory (`open(path,"rb").read()`), parses the `Range` header manually, returns 206 with correct `Content-Range` / `Accept-Ranges` headers for Range requests, or 200 with `Accept-Ranges: bytes` for full-file requests.

**Rule**: For audio/video file endpoints served to browser `<audio>`/`<video>` elements, do NOT use `send_file(conditional=True)` — handle Range requests explicitly. Clip files are small enough (≤500 KB) to read into memory without streaming.

### Clip sync — re-upload clips missing from hub (added 3.3.136)
After a successful upload `_upload_clip_inner` writes a zero-byte `.hub` marker (e.g. `clip.hub`) alongside the WAV. The new `_sync_pending_clips()` method on `HubClient` scans `alert_snippets/` for WAV files without a `.hub` marker and uploads them using `_upload_clip_inner` with empty chain_name/chain_id (clips appear on Reports but not in fault-replay panels). Called from the heartbeat thread every 10 cycles (~100 s) via `threading.Thread(target=self._sync_pending_clips, daemon=True, name="ClipSync").start()`.

**Rule**: Always write a `.hub` marker after confirmed upload. Never delete `.hub` markers — they prevent re-uploads on sync.

### FM Scanner stereo always MONO (fixed 3.3.77)
At heartbeat ingestion (`/api/v1/heartbeat`), `sess["rds"]` was only updated when `ps` or `rt` was present. Fields like `stereo`, `tp`, `ta`, `pi` arrive from redsea *before* PS/RT and were silently dropped.
```python
# WRONG — was:
if _sess and (_scanner_rds.get("ps") or _scanner_rds.get("rt")):
# CORRECT — is:
if _sess and any(k for k in _scanner_rds if not k.startswith("_")):
```
Internal fields (e.g. `_level`) start with `_` and don't trigger the update. All real RDS fields do.

### FM Scanner doTuneOrStart — clicking history/presets/scan results while idle did nothing (fixed 3.3.74)
All three click handlers (`.hist-item`, `.preset-item`, `.peak-btn`) were gated on `_state === 'streaming'`. Added `doTuneOrStart(freq)`: calls `doTune()` if streaming, `doStart()` if idle with a site selected.

### Scanner `out_deadline` burst causing ~1.5 s silence (fixed 3.3.69)
`out_deadline` was initialised to `time.monotonic()` at loop start, *before* the `_SKIP=15` warm-up silence blocks were flushed. Those 15 × 0.1 s blocks pushed the deadline 1.5 s into the past, causing 15 blocks to be sent instantly on connect. Fix: defer `out_deadline = None`; set it to `time.monotonic()` only when the **first real block** is dequeued.

### Clip audio not playing on hub Reports page (fixed 3.3.140)
Root cause: nginx's default `client_max_body_size` is 1 MB. A 30-second WAV clip at 48 kHz mono-16-bit is ~2.75 MB. The upload failed with HTTP 413; the clip never landed on the hub disk; the audio player returned a 404.

Two compounding bugs:
1. `_sync_pending_clips` logged "Clip sync uploaded" even when `_upload_clip_inner` returned early due to a 4xx error (including 413) — making failed uploads look successful.
2. `hub_proxy_alert_clip` hardcoded MIME and filename from the request; didn't check alternative extensions in the local cache.

Fixes:
- `_upload_clip_inner` now auto-compresses WAV→MP3 before upload when file > 200 KB. 30 s WAV → ~350 KB MP3. Uses `_try_encode_mp3` (lameenc or ffmpeg). If no encoder available, WAV is still sent and a log message explains the nginx directive needed.
- `_upload_clip_inner` now returns `bool` (True = success, False = failure); 413 path returns False.
- `_sync_pending_clips` only increments count and logs "uploaded" on True.
- `hub_proxy_alert_clip` now tries both `.wav` and `.mp3` extensions in local cache lookup. Serves with correct MIME type based on actual file found.

**Rule**: Never hardcode `mimetype="audio/wav"` in `hub_proxy_alert_clip` — clips may be stored as `.mp3` if the WAV was above the compression threshold. Use `_clip_mime` (set during local-cache lookup) for both 206 and 200 responses.
**Rule**: `_upload_clip_inner` must return a boolean. `_sync_pending_clips` must check it before logging success.

### Hub Reports clip player 404 for streams with "/" in name (fixed 3.3.165)
The clip URL was built in the Jinja2 template using `e.stream|urlencode`. Jinja2's `urlencode` filter uses `urllib.parse.quote` with `safe='/'`, so a literal `/` in a stream name (e.g. `Northern Ireland DAB / Absolute 90s`) was left unencoded in the URL path. The Flask route used `<stream_name>` (no `path:` converter), so the router split at the slash and returned a 404 before any audio loaded.

Fix: route changed from `<stream_name>/<filename>` to `<path:stream_filename>`; function body does `stream_name, filename = stream_filename.rsplit('/', 1)`. The direct `__wrapped__` call from the mobile API combines them as `f"{stream_name}/{filename}"`.

**Rule**: Any Flask route that captures a segment that may contain `/` (stream names, chain names, site names) MUST use `<path:param>`. When combining multiple such segments into one `<path:>` converter, split on the last `/` (`.rsplit('/', 1)`) to separate the final filename from the preceding path.

### Clip Format setting removed (3.3.141)
The `clip_format` AppConfig field ("wav"/"mp3") and its Settings UI selector have been removed. Local clips are always saved as WAV. Upload compression (WAV→MP3) is applied automatically by `_upload_clip_inner` when the file exceeds ~200 KB, regardless of any setting.

**Rule**: Do not re-add a `clip_format` setting. Upload compression is unconditional and automatic; local storage is always WAV.

### Silence clips ending before the silence ends (fixed 3.3.145)
`_save_alert_wav` was called immediately when silence was *detected* (after `silence_min_duration` seconds). The clip captured audio leading up to the silence onset but the silence was still active when the clip ended.

Fix: `InputConfig` gains two new runtime fields — `_silence_active: bool` and `_silence_alert_key: str`. When silence is first detected these are set. The existing start clip is still saved. An `elif` branch catches the transition back to audio (`_silence_active and lev > threshold`) and saves a second clip labelled `silence_end` plus a "Audio restored" history entry. The recovery clip ends AFTER the silence, so it shows the tail of the outage and the moment audio comes back.

Both fields are reset in: cascade-suppress early return, stream reconnect (`cfg._silence_secs=0.0` block), and monitor-loop disconnect reset.

**Rule**: Do not remove `_silence_active` / `_silence_alert_key` resets from the cascade-suppress, stream-reconnect, and monitor disconnect paths — stale `True` values would cause spurious recovery clips after unrelated stream restarts.

### Hub Reports — Chain Faults always "0" (fixed 3.3.159)
`hub_reports()` only collected events from `s.get("recent_alerts", [])` for each site (client heartbeat data). `CHAIN_FAULT` events are generated on the hub by `_fire_chain_fault()` → `_alert_log_append()` and written to the hub's own `alert_log.json`. In pure hub-mode they are never in any site's `recent_alerts`, so `counts.get('CHAIN_FAULT', 0)` was always 0.

Fix: after collecting site events, also call `_alert_log_load(2000)` and merge hub events tagged `_site="(hub)"`. A `seen_ids` set prevents duplication when the hub is also a client (both-mode). Chain/stream lookup works identically for hub events.

**Rule**: `hub_reports()` must always merge the hub's own alert log in addition to site heartbeat events. Hub-generated events (CHAIN_FAULT and others) only exist in `alert_log.json`, not in site payloads.

### Hub Reports — Silence type missing from type filter (fixed 3.3.159)
`type_names` was built purely from event types present in `all_events`. On busy systems, silence events could be displaced from the 50-event heartbeat window and the silence-family types (SILENCE, STUDIO_FAULT, STL_FAULT, TX_DOWN, DAB_AUDIO_FAULT, RTP_FAULT) would disappear from the Type dropdown.

Fix: `type_names` is now built as the union of dynamic types found in `all_events` plus the constant set `_SILENCE_TYPES`, so silence-related options are always present in the filter regardless of the current event window.

**Rule**: Never build `type_names` in `hub_reports()` from events alone — always union with `_SILENCE_TYPES` to keep the filter stable.

### Hub Reports — "Clips only" off screen (fixed 3.3.161)
The "Clips only" checkbox was a flex item inside `.filters` alongside the Site/Stream/Type/Chain selects and two `datetime-local` inputs. On a 1280 px display the combined minimum content width (~1280 px) matched the viewport, pushing the checkbox off the right edge on any screen narrower than that.

Fix: "Clips only" checkbox was moved out of `.filters` into a new `.filter-row-count` flex row directly below the filter bar. The row is `justify-content:space-between` with the checkbox on the left and the "N events shown" count on the right. The filter bar itself now only contains select/datetime controls.

**Rule**: Never put the "Clips only" checkbox back inside the `.filters` flex container — it must remain in the `.filter-row-count` row below the filter bar.

### Broadcast Chains — fault log refresh destroys replay panel and jumps scroll (fixed 3.3.161)
`loadFaultLog` used `[20000,40000,70000].forEach(...)` to schedule staggered refreshes. Because all three timeouts were scheduled on every call (including auto-refresh calls), the pattern caused exponential growth: within 2 minutes the function was being called every 20–40 seconds indefinitely. Each call did `body.innerHTML=html` which: (1) destroyed any open replay panel (`<tr id="rrow_...">`) — the user's "dropdown closes", and (2) caused some browsers to reset scroll position to the top.

Fix applied to `loadFaultLog`:
- Added `_nRefresh` parameter (default 0); each call schedules **one** next refresh at `_delays[_nRefresh]` and increments the counter. Cap is `_delays.length` (3), so refreshes happen at approximately +20 s, +60 s, +170 s from initial open — then stop.
- `window.scrollY` is saved before and restored after `body.innerHTML=html`.
- If `body.querySelector('[id^="rrow_"]')` is truthy (replay panel is open), `body.innerHTML` replacement is skipped for that cycle; the next refresh is still scheduled.

**Rule**: Never schedule multiple timeouts inside `loadFaultLog` per call — use the `_nRefresh` counter pattern to ensure exactly one next timeout, capped at three total.
**Rule**: Always save/restore `window.scrollY` around `body.innerHTML = html` in fault log refreshes.

### WAN audio choppy every ~0.5 s (fixed 3.3.70–3.3.73)
Root cause: hub hosted remotely from SDR client. WAN RTT >250 ms triggered `_KP_THRESHOLD = 0.25 s` silence injection in `generate_scanner()`. Also, when RTT > `_BLK_DUR` (0.1 s), sequential POSTs fell behind real-time.
Fixes applied:
- `_KP_THRESHOLD = 1.0` — silence only injected after 1 s gap
- `_PRE = 1.0` — browser pre-buffer raised to match
- `slot.get(timeout=0.30)` — relay generator polls less aggressively
- Adaptive chunk batching when RTT > `_BLK_DUR` — client batches multiple blocks per POST to maintain real-time throughput

---

## FM Scanner Details

### Band scan
- Endpoint: `POST /api/hub/scanner/band_scan` — requires `{site, start_mhz, end_mhz, step_khz}`
- Result poll: `GET /api/hub/scanner/scan_result/<site>` — returns `{ready, peaks: [{freq_mhz, power_db}]}`
- Runs `rtl_power` on the client; requires dongle to be free (conflicts with active stream)
- Scan button is enabled when a site with scanner dongles is selected, regardless of stream state

### `doTuneOrStart(freq)` pattern
```javascript
function doTuneOrStart(freq){
  if(_state === 'streaming' || _state === 'connecting') doTune(freq);
  else if(siteSel.value) doStart(freq);
}
```
Used by history items, preset items, and scan result peak buttons. Always use this instead of calling `doTune()` directly from click handlers.

### RDS data flow
```
redsea (client) → JSON line → _rds_reader thread → _scanner_rds dict
→ heartbeat payload["scanner_rds"] → hub /api/v1/heartbeat handler
→ sess["rds"] (only if any non-_ key present) → /api/hub/scanner/status/<site>
→ browser _updateRDS()
```
`_rds_reader` stabilises PS (needs 3 of last 12 matching, min 6 chars) and RT (2 of last 10 matching or ≥12 chars). Stereo/TP/TA/PI bypass stabilisation and are written directly.

### Site filtering
FM Scanner page only shows sites where `hub_server._sites[site]["scanner_serials"]` is non-empty. This list is populated from the client heartbeat (`scanner_serials = [d.serial for d in cfg.sdr_devices if d.role == "scanner"]`). After changing a dongle's role to `scanner`, wait one heartbeat cycle (~10 s) for the hub to update.

---

## Architecture Notes

### Hub ↔ Client heartbeat
- Client POSTs heartbeat every ~10 s to `{hub_url}/api/v1/heartbeat`
- Hub stores full payload in `hub_server._sites[site_name]`
- Hub returns ACK with `listen_requests`, `commands`, etc.
- `scanner_serials` (dongles with role=scanner) are included in every heartbeat payload

### Audio relay slots
- `listen_registry.create(site, idx, kind, ...)` → `ListenSlot`
- Client POSTs PCM to `/api/v1/audio_chunk/<slot_id>`
- Browser GETs `/hub/scanner/stream/<slot_id>` — existing relay generator
- Slot expires after inactivity; `slot.closed = True` to expire immediately

### WAN audio latency (solved in 3.3.70–3.3.73)
- `_KP_THRESHOLD = 1.0` in `generate_scanner()` relay — silence injection threshold
- Browser `_PRE = 1.0` — 1 s pre-buffer
- Client batches chunks when RTT > `_BLK_DUR` (0.1 s) to maintain real-time delivery
- RDS/stereo fields flow: client `_scanner_rds` dict → heartbeat payload → hub session `sess["rds"]` → `/api/hub/scanner/status/<site>` → browser

### Settings page structure
- Template: `SETTINGS_TPL` (starts ~line 14, inside `"""`)
- Tab nav: `<nav class="sb">` with `<button class="tb" id="b-{id}" onclick="st('{id}')">`
- Panels: `<div class="pn" id="p-{id}">` — inside `<form>` for settings that save, OUTSIDE for JS-only panels (like Plugins)
- `st(id)` JS function shows/hides panels and updates active button

### SDR device roles
`SdrDevice.role` options: `"none"` | `"dab"` | `"fm"` | `"scanner"`
- `"scanner"` — designated FM Scanner / WebSDR dongle
- Reported to hub in heartbeat as `scanner_serials`
