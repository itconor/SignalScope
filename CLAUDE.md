# SignalScope — Project Memory

## Project Overview
SignalScope is a broadcast signal intelligence platform. Single Python file (`signalscope.py`) — Flask web app, client/hub architecture, RTL-SDR integration for FM/DAB monitoring.

- **Repo**: https://github.com/itconor/SignalScope
- **Current build string**: `BUILD = "SignalScope-3.5.154"` (increment on every release)
- **Update this file** at the end of any session where bugs are fixed, architecture is discovered, or features are added.
- **`gh` CLI path**: `/opt/homebrew/bin/gh` — always use this full path, it is installed and working

### MANDATORY release checklist — two separate flows depending on what changed

#### Plugin-only change (only files inside `plugins/` were modified)
1. **Plugin `SIGNALSCOPE_PLUGIN["version"]`** inside the `.py` file itself — increment (e.g. `"2.1.14"` → `"2.1.15"`). **Both the file AND plugins.json must match or the installed plugin loops offering the same update forever.**
2. **`plugins.json`** — update the matching entry's `"version"` to match step 1
3. **`CHANGELOG.md`** — add an entry at the top
4. `git add` changed files, `git commit`, `git push`
5. **NO `BUILD` bump. NO `gh release create`.** Plugin updates do NOT touch `signalscope.py` and do NOT get a SignalScope release.

#### Main app change (`signalscope.py` was modified)
1. **`BUILD`** in `signalscope.py` — increment (e.g. `3.5.154` → `3.5.155`)
2. **`CHANGELOG.md`** — add an entry at the top
3. `git add` changed files, `git commit`, `git push`
4. `/opt/homebrew/bin/gh release create v{BUILD_VERSION} --title "SignalScope-X.Y.Z" --notes "..."`

**Rule**: NEVER bump `BUILD` or create a `gh release` for a plugin-only change. NEVER create a release that says "SignalScope-X.Y.Z" when only a plugin file changed. The two flows are completely separate.

---

## Plugin System

### Directory layout
```
SignalScope/
├── signalscope.py          ← main app (never moved)
├── plugins/                ← ALL plugin files live here
│   ├── sdr.py
│   ├── logger.py
│   ├── icecast.py
│   └── ...
├── plugins.json            ← public registry (GitHub)
└── ...
```

### How it works
On startup, `_load_plugins()` scans the `plugins/` subdirectory for any `*.py` file that contains `SIGNALSCOPE_PLUGIN`. Matching files are imported and their `register(app, ctx)` function is called. A nav bar item is injected automatically between hub links and Settings.

**Migration**: On first run after upgrade from an older SignalScope that stored plugins alongside `signalscope.py`, any plugin `.py` files found in the root are automatically moved to `plugins/`. Associated config files (e.g. `icecast_config.json`) are moved at the same time. This is silent and logged.

Key code locations in `signalscope.py`:
- `_plugins: list[dict]` — module-level registry
- `_PLUGINS_SUBDIR = "plugins"` — subdirectory name constant
- `_load_plugins()` — migration + discovery + import
- `_scan_installed_plugins()` — settings page helper (scans `plugins/`)
- `_PLUGIN_REGISTRY_URL` — GitHub `plugins.json` URL
- Plugin API routes: `/api/plugins`, `/api/plugins/available`, `/api/plugins/install`, `/api/plugins/remove`
- Settings nav button: `b-plugins` / panel: `p-plugins` — outside the `<form>` tag

### Plugin registry
`plugins.json` at repo root lists installable plugins. Users can browse and install via **Settings → Plugins**.

---

## Writing a Plugin

### Plugin theming — MUST follow

Every plugin page MUST use the app's CSS variables and class names. Never invent bespoke colours or button classes.

**`:root` block** (copy verbatim into every `<style>` tag):
```css
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
```

**`body`**: `background:radial-gradient(circle at top,#12376f 0%,var(--bg) 38%,#05101f 100%);color:var(--tx);font-family:system-ui,sans-serif;font-size:13px`

**`<header>`**: `background:linear-gradient(180deg,rgba(10,31,65,.96),rgba(9,24,48,.96));border-bottom:1px solid var(--bor);padding:12px 20px;display:flex;align-items:center;gap:12px`

**Buttons** — use these exact class combinations, never custom classes:
| Class | Result |
|-------|--------|
| `btn bp` | Primary (blue) |
| `btn bd` | Danger (red) |
| `btn bg` | Ghost (dark) |
| `btn bp bs` | Primary small |
| `btn bd bs` | Danger small |
| `btn bg bs` | Ghost small |

Base `.btn`: `border:none;border-radius:8px;padding:5px 12px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit` — `.btn:hover{filter:brightness(1.15)}`

**Cards**:
- `.card` — `background:var(--sur);border:1px solid var(--bor);border-radius:12px;overflow:hidden;margin-bottom:16px`
- `.ch` (card header) — `padding:9px 14px;display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--bor);background:linear-gradient(180deg,#143766,#102b54);font-size:12px;font-weight:700;color:var(--acc);text-transform:uppercase;letter-spacing:.06em`
- `.cb` (card body) — `padding:14px`

**Badges**: `.badge` base + `.b-ok` (green) / `.b-al` (red) / `.b-mu` (grey)

**Tables**: `th` uses `color:var(--mu);font-size:11px;text-transform:uppercase;border-bottom:1px solid var(--bor)` — `tr:hover td{background:rgba(23,52,95,.35)}`

**Inputs/selects**: `background:#0d1e40;border:1px solid var(--bor);border-radius:6px;color:var(--tx);padding:6px 9px` — `:focus{border-color:var(--acc)}`

**Fields**: `.field{display:flex;flex-direction:column;gap:4px}` — label: `font-size:11px;color:var(--mu);font-weight:600;text-transform:uppercase;letter-spacing:.05em`

**Messages**: `#msg` + `.msg-ok{background:#0f2318;color:var(--ok);border:1px solid #166534}` / `.msg-err{background:#2a0a0a;color:var(--al);border:1px solid #991b1b}`

**Hub-mode routing rule**: If a plugin has a hub overview page at `/hub/myplugin` AND a client page at `/myplugin`, the client route MUST redirect to the hub page when `mode == "hub"`:
```python
cfg_ss = monitor.app_cfg
mode   = getattr(getattr(cfg_ss, "hub", None), "mode", "standalone") or "standalone"

@app.get("/myplugin")
@login_required
def myplugin_page():
    if hub_server is not None and mode == "hub":
        from flask import redirect
        return redirect("/hub/myplugin")
    return render_template_string(_CLIENT_TPL)
```

**CRITICAL — `hub_server` is NEVER None**: `hub_server` in the plugin ctx is always a `HubServer()` instance (created unconditionally at module level in signalscope.py). The check `if hub_server is None:` will ALWAYS be False. To detect whether a plugin is running on a client node vs a hub node, use the `mode` string:
```python
cfg_ss  = monitor.app_cfg
mode    = getattr(getattr(cfg_ss, "hub", None), "mode", "standalone") or "standalone"
hub_url = (getattr(getattr(cfg_ss, "hub", None), "hub_url", "") or "").rstrip("/")

is_hub    = mode in ("hub", "both")
is_client = mode == "client" and bool(hub_url)
```
**Rule**: Never use `hub_server is None` to detect client nodes. Always use `mode == "client"` (and check `hub_url` is set for the HubClient to be active).

### Minimal skeleton
```python
# myplugin.py — drop into the plugins/ subdirectory

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
| `login_required` | decorator | Require authenticated **web session** — use for browser-facing routes |
| `csrf_protect` | decorator | Validate CSRF token on POST routes |
| `mobile_api_required` | decorator | Require valid mobile API Bearer token — use for ALL `/api/mobile/...` routes |
| `BUILD` | str | e.g. `"SignalScope-3.4.58"` |

**Rule**: Any route under `/api/mobile/...` added by a plugin MUST use `ctx["mobile_api_required"]` (not `login_required`). Using `login_required` on a mobile route means: (a) if auth is disabled → no auth at all; (b) if auth is enabled → Bearer tokens are silently ignored and every call gets a login redirect. Always fall back safely: `mobile_api_req = ctx.get("mobile_api_required", ctx["login_required"])`.

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

Plugin source files live in `plugins/` in the repo. Add an entry to `plugins.json` at the repo root (the `url` field points to the raw GitHub URL under `plugins/`):
```json
{
  "id":           "myplugin",
  "name":         "My Plugin",
  "file":         "myplugin.py",
  "icon":         "🔧",
  "description":  "What it does.",
  "version":      "1.0.0",
  "requirements": "numpy scipy",
  "url":          "https://raw.githubusercontent.com/itconor/SignalScope/main/plugins/myplugin.py"
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

**Rule**: Any standalone hub page that calls `{{topnav(...)|safe}}` MUST define these CSS classes or the navbar will be broken: `display:inline-block` and `text-decoration:none` on `.btn` (topnav renders nav links as `<a class="btn ...">` — without `display:inline-block` they collapse; without `text-decoration:none` they show underlines), and `.nav-active{background:var(--acc)!important;color:#fff!important}` (without this the active dropdown item has no highlight). Use the Hub Reports template `.btn` definition as the reference.

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

### Silence clips starting mid-silence (fixed 3.4.73)
At the moment silence is confirmed (after `silence_min_duration` seconds), `_audio_buffer` contains mostly silence because it fills continuously — the pre-fault audio has already scrolled past. Result: the onset clip starts in the middle of silence and the fault point is inaudible.

Fix: `_save_alert_wav` gains an optional `_chunks` parameter. When provided it is used instead of `cfg._audio_buffer` (skipping the `_ensure_alert_buffer_capacity` resize). At the silence onset trigger, a snapshot of `cfg._stream_buffer` is passed as `_chunks`. `_stream_buffer` is a 20 s rolling deque that at detection time contains ~17 s of pre-fault audio + the silence onset — so the clip starts with normal programme audio and you can hear the exact moment signal was lost.

**Rule**: Never call `_save_alert_wav` for silence onset without passing `_chunks=list(cfg._stream_buffer)`. Using `_audio_buffer` at that point gives a clip that is mostly silence.

### Chain clips inconsistent length / hardcoded 10 s (fixed 3.4.73)
All onset clips in `_fire_chain_fault()` used the hard-coded `CHAIN_CLIP_MIN_SECS = 10.0` regardless of chain config or fault duration. Recovery clips used `CHAIN_CLIP_MIN_SECS` as the floor.

Fix: Added `clip_seconds` field to chain config dict. `api_chains_save()` reads, validates (10–300 s, 0 = use system default), and persists it. `_fire_chain_fault()` computes `_onset_dur = float(chain.get("clip_seconds") or CHAIN_CLIP_MIN_SECS)` and passes it to `_save_alert_wav`. Remote `save_clip` commands use the same value. `_schedule_chain_recovery_clips()` uses `chain.get("clip_seconds", CHAIN_CLIP_MIN_SECS)` as the floor for recovery clip duration. Chain builder UI: new **Clip duration (s)** field in the Timing & Behaviour grid; auto-expands if non-zero.

**Rule**: All chain clip call sites (`_fire_chain_fault`, `_schedule_chain_recovery_clips`, remote `save_clip` payload) must read `chain.get("clip_seconds") or CHAIN_CLIP_MIN_SECS` — never hard-code `CHAIN_CLIP_MIN_SECS` as the requested duration.

### Chain clips inconsistent length — recovery/last_good short despite full buffer (fixed 3.4.122)
`_schedule_chain_recovery_clips` called `_save_alert_wav` without `_chunks`, falling back to `_audio_buffer`. `_audio_buffer` defaults to `ALERT_BUFFER_SECONDS` (10 s). `_ensure_alert_buffer_capacity` only changes the `maxlen` of the deque — it cannot back-fill audio not previously recorded. So recovery clips contained only whatever had accumulated in the 10 s ring at save time, giving 5 s, 14 s or 30 s at random.

The same bug existed in `_cmd_save_clip` for remote nodes: `_fault_chunks` was `None` for `status != "fault"` (i.e. `last_good` and `recovery`), causing those clip types to also fall back to `_audio_buffer`.

Fix: both paths now snapshot `_stream_buffer` (60 s rolling, always full after the first minute) for ALL clip types:
- `_schedule_chain_recovery_clips`: `_rc_chunks = list(_lc._stream_buffer) if ... is not None else None` passed as `_chunks`; `_ensure_alert_buffer_capacity` calls removed (no longer needed)
- `_cmd_save_clip`: `_fault_chunks = list(_sb) if (_sb is not None) else None` unconditionally (was gated on `status == "fault"`)

**Rule**: Every call to `_save_alert_wav` for chain clips (fault, last_good, recovery, any position) MUST pass `_chunks=list(cfg._stream_buffer)`. Never omit `_chunks` and let the function fall back to `_audio_buffer` — `_audio_buffer` is sized for silence-alert clips (short, ~5–10 s) not chain fault clips (up to 300 s).

### Hub connection broken / `/status.json` 500 for non-FM inputs (fixed 3.5.81)
`_fm_stereo_blend` was added to the heartbeat payload and `inp_dict` in 3.5.76 without being declared as a default field in `InputConfig`. For FM inputs the FM monitoring loop initialises it via `getattr(cfg, "_fm_stereo_blend", 0.0)` at startup. For every other input type (DAB, ALSA, HTTP, RTP) the attribute is never created. Accessing it directly (`round(inp._fm_stereo_blend, 3)`) raised `AttributeError` in both the heartbeat builder and `status_json`, causing: (1) every heartbeat POST to fail → hub connection broken; (2) `/status.json` → HTTP 500.

Fix: `_fm_stereo_blend: float = field(default=0.0, init=False, repr=False)` added to `InputConfig`.

**Rule**: Every runtime attribute accessed without `getattr()` in the heartbeat payload or in `inp_dict` / `status_json` MUST have a default declared in `InputConfig` via `field(default=..., init=False, repr=False)`. Never rely on a monitoring-loop assignment to initialise an attribute that is read unconditionally — non-matching input types will never run that loop branch.

---

### Hub live meters zero for ~60 s after restart (fixed 3.4.105)
`InputConfig._last_level_dbfs` defaults to `-120.0`. On restart, `_live_loop` immediately sent that default to the hub. The live-push merger stored `-120.0` (not `None`, so the null-guard didn't help), overwriting the valid levels that `_load_state()` had just restored from `hub_state.json`. All bars dropped to 0 % and stayed there until the monitoring loop connected to audio (~60 s later).

Fix: added `_has_real_level: bool` field (default `False`) to `InputConfig`. `analyse_chunk` sets it `True` on the first real measurement. Monitor stop resets it to `False` alongside `_last_level_dbfs`. `_live_loop` now sends `null` for `level_dbfs`/`peak_dbfs` when `_has_real_level` is False — the hub merger already skips `null`, so restored state is preserved until real audio data flows (typically within 1–2 s of stream connect).

**Rule**: Never send `_last_level_dbfs` in a live push frame unless `_has_real_level` is `True`. The value `-120.0` is the uninitialised default, not a real measurement.

---

### Meter Wall bars sluggish — updated once per second from heartbeat data (fixed 3.4.108)
`POLL_MS = 1000` drove the entire UI including bar heights from `/api/meterwall/data`, which reads `hub_server.get_sites()` (heartbeat data, updated every ~10 s). Bars therefore moved at most once per second with levels that could be up to 10 s stale.

Fix (plugin v1.1.0): added a secondary `livePoll()` function that fetches `/api/hub/live_levels` at 150 ms (`LIVE_MS = 150`). On first success it sets `_liveActive = true`. `updateCard()` is guarded — when `_liveActive` is true it skips bar/peak/level writes so the 1 s metadata poll does not flicker stale heartbeat values over the live data. The card key scheme (`site|stream`) is identical in both endpoints so no mapping is needed.

**Rule**: The `updateCard()` bar/peak/level block must remain gated on `!_liveActive`. Removing the gate would cause the 1 s metadata poll to briefly overwrite live values with up-to-10 s-stale heartbeat data every second.

---

### Hub "Play" produces no audio for stereo streams (fixed 3.4.128)
Two code paths both sent the wrong channel count to ffmpeg, producing a corrupt MP3 stream that browsers refused to decode silently.

1. **`stream_live` `_live_buf()` fallback** — when `_live_n_ch == 2` (stereo) but `_audio_buffer` was temporarily empty, the function fell back to `_stream_buffer` (mono, 24 000 samples/chunk). ffmpeg was already started with `-ac 2`, so it received half the expected bytes per 0.5 s and encoded a half-speed or corrupt MP3 stream.

   Fix: `_live_buf()` now always returns `inp._audio_buffer` when `_live_n_ch == 2`, even if the deque is empty. The writer loop waits for the first stereo chunk (arrives within one CHUNK_DURATION, 0.5 s).

2. **Hub relay writer (`kind="live"` in `_push_audio_request`)** — always read from `_stream_buffer` (mono) and ran ffmpeg with `-ac 1`, regardless of whether the stream was stereo. Hub-relayed stereo streams produced mono MP3; in some browser/proxy combinations the player appeared but produced no audio.

   Fix: relay checks `inp.stereo and inp._audio_channels == 2`. If true, reads from `_audio_buffer` with `ffmpeg -ac 2 -b:a 256k` (mirrors `stream_live`). Added `_relay_live_buf()` helper — like the fixed `_live_buf()` it never mixes mono/stereo.

**Rule**: Never fall back from `_audio_buffer` to `_stream_buffer` (or vice-versa) inside a path where ffmpeg has already been started with a fixed `-ac N`. The channel count must stay consistent for the lifetime of the ffmpeg process. Both `_live_buf()` and `_relay_live_buf()` enforce this.
**Rule**: `_relay_n_ch` is captured once when the relay thread starts (from `inp._audio_channels`). If the stream transitions from mono to stereo while the relay is running, the relay stays mono for that connection — the user must re-press Play to get a new stereo relay.

---

### Hub L/R stereo bars don't live-update (fixed 3.4.129)
`level_dbfs_l` and `level_dbfs_r` were missing from two places:
1. The `_live_loop` slim frame (POSTed at 5 Hz to `/api/v1/live_push`) — only `level_dbfs`, `peak_dbfs`, `silence_active`, `ai_status`, `lufs_m`, `lufs_s` were included.
2. `_LIVE_STREAM_FIELDS` in `hub_live_push` — the merger that copies slim-frame values into `hub_server._sites` skipped the L/R fields.

Result: L/R values in `_sites` were only refreshed by the 10-second heartbeat. The browser's 150 ms `/api/hub/live_levels` poll returned stale L/R data, so the L/R bars appeared frozen.

Fix: both fields added to the `_live_loop` frame (guarded by `_has_real_level and _audio_channels == 2`) and to `_LIVE_STREAM_FIELDS`. L/R bars now update at the same 150 ms cadence as the main RMS bar.

**Rule**: Any per-stream metric that should update at sub-second cadence MUST appear in BOTH the `_live_loop` `streams_snap` dict AND in `_LIVE_STREAM_FIELDS`. Adding to only one of the two is silent — the bar just won't update.

---

### HTTP stream inputs freeze permanently on stream loss (fixed 3.4.130)
When an HTTP/HTTPS input stream dropped, ffmpeg entered its internal retry loop (`-reconnect`, `-reconnect_streamed`) and held the stdout pipe open indefinitely without producing audio. The monitoring loop's `select([proc.stdout], [], [], 1.0)` returned `readable=[]` each second, but because ffmpeg never exited there was no EOF to break the inner loop. The existing `_HTTP_STALL_SECS = 10.0` detection set `cfg._has_real_level = False` but then just issued `continue` — waiting forever for either new data or ffmpeg to exit, neither of which came. Levels were permanently frozen at the last real measurement until the monitor was manually restarted.

Fix: when `_HTTP_STALL_SECS` (now 8 s) expires with no data, the stuck ffmpeg process is killed (`proc.kill()`) and the inner loop is broken. The outer reconnect loop then waits 5 s and starts a fresh ffmpeg process. Total worst-case recovery: ~13 s. `-reconnect_delay_max` also reduced from 10 → 5 since application-level restart now takes over.

**Rule**: Never rely on ffmpeg's own reconnect loop as the sole recovery mechanism for HTTP inputs. After `_HTTP_STALL_SECS` of no audio data, kill the process and restart — ffmpeg can remain alive indefinitely with the pipe open and no output.

---

### FM stereo L/R bars missing from hub dashboard, Watch view, client status page (fixed 3.4.136)
All three dashboard templates gated the L/R bar DOM elements on `{%- if inp.stereo %}` / `{% if st.get('stereo') %}` / `{% if s.get('stereo') %}`. FM RTL-SDR inputs never have this config flag set — `inp.stereo` is a user checkbox only used for ALSA/DAB/HTTP/RTP inputs. So even with the stereo decoder running and `_level_dbfs_l`/`_level_dbfs_r` populated, the L/R bar elements were never rendered and the live-update JS had nothing to target.

Fix: each of the three template conditions now also fires for FM inputs:
- Client status page (`signalscope.py` ~line 15847): `{%- if inp.stereo or inp.device_index.lower().startswith('fm://') %}`
- Watch view (~line 30500): `{% if st.get('stereo') or dtype == 'fm' %}`
- Hub overview (~line 31795): `{% if s.get('stereo') or s.get('device_index','').lower().startswith('fm://') %}`

The L/R wrapper div starts `display:none` (and carries `data-fm-lr="1"`) for FM inputs that don't yet have stereo data. Each JS update block (L/R stereo bars section) now checks `lrWrap.dataset.fmLr === '1'` and sets `lrWrap.style.display = 'flex'` on first real L/R measurement. Result: bars appear automatically once the pilot tone is locked, and don't clutter the card for mono FM signals.

**Rule**: Never gate L/R bar DOM elements on `inp.stereo` (or `s.get('stereo')`) alone. FM inputs detect stereo at runtime via `_fm_stereo` / `_audio_channels == 2`, not via the `stereo` config flag. Always also check the device_index prefix (`fm://`) so the DOM elements exist to receive live updates.

---

### Meter Wall stereo L/R bars (added meterwall v1.1.2)
Meter Wall cards for stereo streams show two narrow side-by-side bars (L and R) instead of the single mono bar. The card always contains both `.mtr-mono` and `.mtr-stereo` sections; CSS rules toggle which is visible based on `card.dataset.stereo = "1"`. The `livePoll()` function reads `level_dbfs_l` and `level_dbfs_r` from `/api/hub/live_levels` and stores them in `_targetLev[key+'|L']` / `_targetLev[key+'|R']`; it also sets `card.dataset.stereo = '1'`. The `_meterRaf()` loop detects keys ending with `|L` / `|R`, strips the suffix to find the base card key, then queries `.mtr-lr[data-ch="L/R"]` within that card. The main mono bar uses `.mtr-mono .mtr-fill` / `.mtr-mono .mtr-peak` selectors to avoid touching the hidden L/R bar elements.

**Rule**: In `_meterRaf()`, always check for `|L`/`|R` suffix before the normal card lookup. Never use `card.querySelector('.mtr-fill')` without a `.mtr-mono` or `.mtr-lr[data-ch=...]` scope — it will match the first `.mtr-fill` in the DOM which may be the wrong bar once stereo elements are present.
**Rule**: Never put `data-stereo="1"` on cards from the 1 s metadata poll alone — rely on `livePoll()` to set it from live L/R data, so the badge only appears on genuinely stereo streams with real L/R measurements.

---

### FM stereo stale SOS filter state → R-channel distortion on blend re-entry (fixed 3.4.146)

The 3.4.144 blend fix gated `_mpx_to_stereo` on `_stereo_blend > 0.0`. When blend was 0.0 (poor pilot), the function was skipped entirely — leaving `zi_lpr`, `zi_pilot`, `zi_lmr` stale. On first call after blend rose above 0, the stale `zi_pilot` state caused a transient inflated `pilot_peak`. Because `pilot_n = pilot / (pilot_peak + 1e-9)`, a too-large `pilot_peak` makes `pilot_n` have amplitude << 1, adding a DC offset to `sub38 = 2·pilot_n²−1`. That DC term multiplies through `lmr_raw = samp * sub38`, contaminating `lmr` with L+R content after the LPF. R channel = `lpr − lmr×2` then distorts. Explained the "worked for a bit, then went bad again" behaviour.

Fix: `_mpx_to_stereo` no longer takes a `blend` parameter. It is called on **every** block whenever `cfg._fm_stereo` is True — keeping all filter states continuously updated. The function always computes full `lmr * 2.0`. Blending is applied externally:
```python
_b = 0.0 if _force_mono else _stereo_blend
if _b <= 0.0:
    # mono: don't populate L/R buffers
elif _b < 1.0:
    L_out = (_b * L_48 + (1.0 - _b) * mono_48).astype(np.float32)
    R_out = (_b * R_48 + (1.0 - _b) * mono_48).astype(np.float32)
else:
    L_out = L_48; R_out = R_48
```
`mono_48 = (L_48 + R_48) / 2` is the noise-cancelling blend reference (L-R terms cancel in the sum, so `mono_48 = lpr` exactly).

**Rule**: Never gate `_mpx_to_stereo` on blend value or force-mono flag — the function must be called on every block to keep SOS filter states fresh. Apply blend/force-mono AFTER the call using `mono_48` as the reference.
**Rule**: Never pass a `blend` parameter into `_mpx_to_stereo`. The blend parameter was removed in 3.4.146 precisely because internal blending caused state staleness. Blend externally only.

### Force Mono for FM inputs (added 3.4.146, persisted in 3.4.147)

Persisted config field: `InputConfig.fm_force_mono` (bool, default `False`). When True, `_mpx_to_stereo` is still called on every block (to keep SOS filter states warm) but `_b = 0.0` forces the mono path — both output channels receive `mono_48`. Survives monitor restart.

- Config field: `fm_force_mono: bool = False` in `InputConfig` (serialised to `config.json`)
- UI: checkbox "Force mono output" in the FM section of the input edit form (Settings → Inputs → Edit)
- Status page: when active, shows "🔇 Force Mono active — change in input config" notice with a link
- Poll response includes `fm_force_mono` field for status page display

**Rule**: `fm_force_mono` is a persisted `InputConfig` field. Do not re-add a runtime-only toggle API for it. The checkbox on the input edit form is the correct UI.

---

### Backup button fails / DB corruption on server migration (fixed 3.4.143)

**Backup button OOM**: `settings_backup()` wrote the ZIP into a `BytesIO` buffer in RAM. A large `metrics_history.db` (500 MB+) exhausted available memory and the download failed silently.

Fix: ZIP is written to a named temp file on disk (`tempfile.NamedTemporaryFile(suffix=".zip", dir=BASE_DIR)`), then streamed to the browser via `send_file` with an `after_this_request` cleanup. No practical size limit.

**logger_index.db missing from backup**: The Logger plugin's database (`plugins/logger_index.db`) was never included in the ZIP. It is now backed up and restored alongside `metrics_history.db`, using the same `sqlite3.backup()` hot-copy.

**Restore RAM exhaustion**: `settings_restore()` called `f.read(512*1024*1024)` before validating the ZIP — the whole upload went into RAM. Fix: `f.save(tmp_path)` writes to disk first; `zf.extract()` is used instead of `zf.read()` for DB entries.

**Server migration / live DB corruption**: Copying a live SignalScope directory with `cp` or a file manager captures the `.db` file and its `-wal`/`-shm` journal at different moments → inconsistent snapshot. Safe options:
1. Use the fixed backup button (produces a WAL-safe SQLite snapshot inside the ZIP)
2. CLI: `sqlite3 metrics_history.db ".backup /tmp/metrics_safe.db"` — safe even while running
3. Use `rsync --checksum` for the recordings directory (resumable, large-file safe)
4. Full procedure: hot-backup DBs → rsync recordings → copy JSONs → stop old server → final rsync pass → start new server

**Rule**: Never include `logger_index.db` in a path-relative arc name inside the ZIP without placing it under `plugins/` — the restore route uses `_PLUGINS_SUBDIR` to know where to put it back.
**Rule**: Any `send_file()` of a temp file MUST be paired with an `@after_this_request` cleanup hook to delete the temp file once Flask finishes streaming.

---

### Morning Report always showing "All Clear" — wrong data file paths (fixed 3.5.107, morning_report v1.2.2)
`_BASE_DIR = os.path.dirname(os.path.abspath(__file__))` resolves to the `plugins/` subdirectory. `_METRICS_DB`, `_ALERT_LOG`, and `_SLA_PATH` were all joined to `_BASE_DIR`, so they pointed to `plugins/metrics_history.db`, `plugins/alert_log.json`, `plugins/sla_data.json` — none of which exist (the real files are in the parent app directory). All three loader functions call `os.path.exists()` first and silently return `[]`/`{}` on failure, so every report contained zero faults and always showed "All Clear".

Fix: added `_APP_DIR = os.path.dirname(_BASE_DIR)` and changed the three shared-data paths to use `_APP_DIR`. Plugin-private files (`morning_report_cfg.json`, `morning_report_cache.json`) remain under `_BASE_DIR` in `plugins/`.

**Rule**: Any plugin that reads shared app data files (`metrics_history.db`, `alert_log.json`, `sla_data.json`, etc.) MUST resolve them from `os.path.dirname(_BASE_DIR)` (the parent app directory), NOT from `_BASE_DIR` (the `plugins/` subdirectory). Only plugin-specific config and cache files should live under `_BASE_DIR`.

---

### Morning Report chains missing for clean chains (fixed 3.4.141)
`all_chain_names` in `_generate_report()` was built solely from `chain_fault_log` (SQLite) and `alert_log` events. A chain with no historical faults had zero entries in either source and never appeared in any section of the report — including the chain health table.

Fix: `_generate_report()` now also reads `monitor.app_cfg.signal_chains` (the live Broadcast Chains configuration) and adds every configured chain name to `all_chain_names`. Chains with no fault history appear with "✓ None — 100% on-air" status as expected.

**Rule**: Always include `live_chain_names` (from `monitor.app_cfg.signal_chains`) in the `all_chain_names` union inside `_generate_report()`. Never build the chain list from fault history alone — clean chains would be invisible.

Also added in 3.4.141 (Morning Report plugin v1.1.0): plain-English headline banner, traffic-light trend pills, uptime %, human-readable outage durations, plain-language column headers and section titles, translated stream quality labels, emoji pattern indicators.

---

### DAB startup sequential 52 s per service — welle-cli `-C` carousel mode (fixed 3.5.6, root cause fixed 3.5.87)
`-C N` is a documented welle-cli flag meaning "decode N programmes at a time in a carousel". With `-C 1`, welle-cli activates exactly one service encoder, holds it for ~52 s, then deactivates it and starts the next. With 9 services = ~8 min total (confirmed empirically: services appeared at 52 s, 104 s, 156 s; services 4+ missed the 120 s probe deadline). **Critically confirmed in 3.5.86: `-C 20` on a 12-service mux ALSO produces 52 s / 104 s / 156 s / 207 s sequential activation** — `-C N` for any N is carousel mode regardless of N vs service count.

Fix (3.5.86, supersedes 3.5.16): **omit `-C` entirely on non-Pi**. Without `-C`, welle-cli decodes the full ensemble simultaneously — all services ready in ~10–15 s. The DabPrewarm then opens persistent connections to all `/mp3/<sid>` endpoints in parallel so consumer ffmpeg processes see immediate data.

**Root cause confirmed (3.5.87)**: The `-C N` flag and its presence/absence do NOT affect the 52-s per-service startup. The real cause was two bugs in the consumer-side probe:
1. **Prewarm `timeout=30 s` < 52 s service startup** → all prewarm connections timed out and disconnected before any encoder was ready, making the prewarm completely ineffective.
2. **Probe cycling every ~5.5 s** (5 s socket timeout + 0.5 s sleep) → each disconnect re-queued the service at the back of welle-cli's internal encoder queue. With 9 services all constantly disconnecting and reconnecting, welle-cli processed them in a round-robin rather than sequentially, producing the same ~52 s intervals regardless of -C value.

**Actual fix (3.5.87)**:
- Prewarm `timeout=70 s` (> 52 s service startup) and `end_ts=600 s` → prewarm connections stay alive until data arrives, keeping every service's position in the welle-cli queue
- Probe uses ONE persistent connection per attempt with `timeout=70 s` per read → no constant reconnect churn. Genuine errors (connection refused) still retry with 3 s gap (not 0.5 s). Timeout exceptions are NOT logged as errors since they are expected while waiting in queue.
- `ready_deadline=660 s` (11 min, covers 12 services × 52 s = 624 s worst case)

**Rule**: **Never add `-C` (any value) to the non-Pi `_start_dab_session` command.** It doesn't help and the probe/prewarm fix is the real solution.
**Rule**: Pi MUST use `-T -C N` where N = `len(session.consumers)` (CPU limiting). The 0.5 s sleep before the command is built ensures `len(session.consumers)` reflects all consumers, not just the first.
**Rule**: Prewarm `_warm_one` `timeout` MUST be ≥70 s (> one 52-s service startup). Previous value of 30 s caused all connections to close before any encoder was ready.
**Rule**: The probe loop must NOT use a short per-attempt timeout (≤52 s) that causes disconnect/reconnect cycling. Use `timeout=70` per read with a persistent connection. Only retry on genuine errors (not socket.timeout).
**Rule**: `-C 1` IS correct (and required) in `_stream_worker` in dab.py on Raspberry Pi for audio playback. Do not confuse the monitoring session (`_start_dab_session`) with the playback session (`_stream_worker`). Monitoring: **no `-C`** on non-Pi, `-T -C N` on Pi. Playback: `-T -C 1` on Pi (no serial), `-T` only on Pi (with serial).

### Pi multi-dongle DAB — rtl_tcp proxy (added 3.5.95)

**Root cause confirmed**: The apt-installed `welle-cli` on Raspberry Pi OS **always opens USB device 0** regardless of any device-selection arguments (`-F rtl_sdr,N`, `-D driver=rtlsdr,serial=X` — all silently ignored). When FM monitoring holds device 0, welle-cli for a second DAB dongle (device 1) still tries to open device 0 and gets `usb_claim_interface error -6`.

**Fix**: For `device_idx > 0` on Pi, `_start_dab_session` launches `rtl_tcp -d DEVICE_IDX` first. `rtl_tcp` correctly opens the requested device by index (same mechanism as `rtl_fm -d 0`). `welle-cli` connects via `-F rtl_tcp,127.0.0.1:PORT` and never touches USB directly — its broken device-selection is bypassed entirely.

- `DabSharedSession` has two new fields: `rtl_tcp_proc` (subprocess) and `rtl_tcp_port` (int).
- `_stop_dab_session` kills `rtl_tcp_proc` AFTER `welle-cli` exits — killing rtl_tcp first would cause welle-cli to error during shutdown.
- If `rtl_tcp` binary is not found or fails to start, falls back to direct `-F rtl_sdr,N` with a log warning.

**Rule**: Never use `-F rtl_sdr,N` or `-D driver=rtlsdr,serial=X` as the sole device-selection mechanism on Raspberry Pi when `device_idx > 0`. These arguments are ignored by the Pi apt welle-cli build. Always use the `rtl_tcp` proxy path.
**Rule**: In `_stop_dab_session`, always kill `rtl_tcp_proc` AFTER `welle-cli` (`session.proc`) has fully exited. Never reverse this order.

---

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

### Hub live relay read chunk size (fixed 3.5.24)
`_push_audio_request` relay reader used `proc.stdout.read(4096)` — each read was followed by a blocking WAN POST. At 256 kbps stereo (DAB) that's 4 round trips per 0.5 s chunk. With WAN RTT > 125 ms the relay fell behind real-time, producing "starts and stops" stutter. Fix: `proc.stdout.read(16384)` — one WAN POST per 0.5 s chunk at 256 kbps. Pre-seed raised from 3 s to 5 s for additional headroom on high-latency links.

**Rule**: The relay reader `proc.stdout.read(N)` size must be at least `bitrate_bytes_per_sec × CHUNK_DURATION` (≈ 16 000 for 256 kbps stereo). Never use 4096 here — it multiplies WAN round trips by 4× for stereo streams.

---

### Stereo setting change requires monitor restart (fixed 3.5.27)
`input_edit` (POST) updates fields in-place on the existing `InputConfig` object to preserve runtime state. For most fields (silence thresholds, alert flags, etc.) this is fine — the monitor loop reads `cfg.field` dynamically. But `stereo` is baked into local variables at loop startup:
- DAB: `_dab_n_ch = 2 if cfg.stereo else 1` (line ~8251); ffmpeg launched with `-ac _dab_n_ch`; `CHUNK_BYTES` computed from `_dab_n_ch`
- HTTP/RTP: `_http_n_ch = 2 if cfg.stereo else 1` (line ~9515); same pattern

If `cfg.stereo` changes mid-run (e.g. user unticks stereo and saves), the ffmpeg process is still outputting interleaved stereo PCM. The loop falls through to the `else` branch (mono) but `samp.size` is double the expected mono size — the data is treated as a double-length mono signal, producing distorted (interleaved L/R) audio. The UI stereo indicator eventually clears, but audio is corrupt.

Fix: `input_edit` captures `_old_stereo` and `_old_device` before the in-place update. After save, if either changed, `monitor.stop_monitoring(); monitor.start_monitoring()` is called. Same restart behaviour as the remote `set_input_field` command.

**Rule**: Any `InputConfig` field that is read once at monitor-loop startup into a local variable — `_dab_n_ch`, `_http_n_ch`, ffmpeg `-ac`, `CHUNK_BYTES` — requires a monitor restart when changed. `input_edit` already handles `stereo` and `device_index`; do not add new "baked-in" fields without also adding them to the restart-trigger check.

---

### FM stereo L/R imbalance — pilot amplitude EMA normalisation (fixed 3.5.79)
The real cause of systematic L/R imbalance was `pilot_peak = np.max(np.abs(pilot))` using an instantaneous per-block peak. Under noise, `np.max` is elevated above the true pilot amplitude, making `pilot_n = pilot / pilot_peak` have amplitude < 1. Then `sub38 = 2·pilot_n² − 1 = (A²−1) + A²·cos(2θ)` has a DC offset of `(A²−1)` that leaks L+R content into `lmr` asymmetrically → L/R level difference that varies with signal content, present on every station.

Fix: replaced `np.max` with a slow EMA of `√2 × RMS(pilot)` (α = 0.05). For a pure sine, `√2 × RMS = peak`, so the normalisation target is the same but is stable against noise spikes. State stored in `_stereo_zi["pilot_amp_ema"]` (initialised to 0.0; seeds from first block).

**Rule**: Never use `np.max(np.abs(pilot))` as the pilot amplitude estimate. Use the EMA of `√2 × RMS(pilot)` — resistant to noise spikes that would undersize `pilot_n` and add a DC offset to `sub38`.

rtl_fm command:
- **MUST use `-M fm`** (NOT `-M wbfm`). `-M wbfm` hardcodes a 32 kHz output rate regardless of `-s`, breaking all downstream resampling. `-M fm -s 171000` outputs the FM discriminator at 171 kHz — the pilot is detectable at 19 kHz in the FFT, proving the full MPX composite is present.
- **MUST NOT include `-A`** (de-emphasis). At 19 kHz, 50 µs de-emphasis attenuates the pilot by ~16 dB, corrupting the subcarrier reconstruction. De-emphasis is applied in Python by `_apply_deemph` at 48 kHz after stereo decode.

**Rule**: Never change `-M fm` to `-M wbfm` in the FM monitoring command. wbfm forces 32 kHz output.
**Rule**: Never add `-A` to the FM monitoring rtl_fm command. Python de-emphasis handles this correctly after decode.

---

### FM de-emphasis (added 3.5.27)
First-order IIR de-emphasis applied at 48 kHz after FM demodulation and resample. Configured per-input via `InputConfig.fm_deemphasis` (`"50us"` | `"75us"` | `"off"`, default `"50us"`). Filter: `b=[1−α]`, `a=[1, −α]`, α = exp(−1/(τ·48000)). Stateful zi tracking across chunks for three channels (mono/L/R) via `_deemph_zi_m`, `_deemph_zi_l`, `_deemph_zi_r`.

---

### AI Monitor stereo support (added 3.5.30)
`_ai_loop` now detects stereo streams via `cfg._audio_channels == 2`. When stereo, `_audio_buffer` chunks are deinterleaved (`L = raw[0::2]`, `R = raw[1::2]`), and `mid = (L+R)/2` is used for the 14 standard features. Two additional features are appended:
- **[14] L–R correlation** normalised to [0,1]: normal programme ~0.65–0.95; one dead channel ~0.5; total phase reversal → near 0 after norm.
- **[15] L/R RMS imbalance**: `|dBFS_L − dBFS_R| / 20`, clipped to [0,1]. Severe one-channel drop: 1.0; normal: near 0.

Constants: `AI_FEATURE_DIM = 14` (mono), `AI_FEATURE_DIM_STEREO = 16` (stereo). `feat_dim` is derived from `samples.shape[1]` throughout `_train_autoencoder` and `_build_onnx` — never hard-coded.

`feat_dim` is stored in `<stream>_stats.json` (alongside `mean`/`std`/`n`). On `_load()`, the ONNX model's input shape is checked against the stored `feat_dim`; any mismatch triggers a learning phase reset. `InputConfig._ai_feat_dim` holds the current model's feature count; `StreamAI.feed()` compares this against the stream's current stereo mode and resets to learning if they diverge (e.g. after a stereo→mono config change).

**Rule**: Never pass `L=None, R=None` to `feed()` when `cfg._audio_channels == 2` — doing so trains/infers on 14 features while the stream is stereo, defeating channel-fault detection.
**Rule**: Never hard-code `AI_FEATURE_DIM` in `_train_autoencoder` or `_build_onnx`. Always derive `feat_dim` from `samples.shape[1]` (training) or `W1.shape[0]` (ONNX build).
**Rule**: `_retrain_model` must check that the initial corpus has the same `feat_dim` as `clean_samples` before vstack. If mismatched, skip the initial corpus and retrain on `clean_samples` only (logged as a warning).

Applied in:
- Stereo path: after DC removal/clip on L_48, R_48, mono_48 — before blend
- Mono path: after `_mpx_to_audio()` return

Settings UI: `<select name="fm_deemphasis">` dropdown in the FM section of the input edit form (after Force mono checkbox). Persisted in `config.json`. Hub API patch field added to `_allowed` and `_REMOTE_BOOL_FIELDS` is NOT used (string field, not bool — handled only by direct form save).

**Rule**: `_apply_deemph` is gated on `_have_scipy_resampler` — if scipy is unavailable (e.g. bare Python), it returns the input unchanged. Do not raise an error; de-emphasis silently degrades to off.
**Rule**: De-emphasis must be applied BEFORE the stereo blend step so that both L_48 and R_48 and the mono_48 blend reference all share the same filter characteristic. Applying only to L/R (not mono_48) would cause the blended output to have inconsistent frequency response during partial stereo.

### Settings page structure
- Template: `SETTINGS_TPL` (starts ~line 14, inside `"""`)
- Tab nav: `<nav class="sb">` with `<button class="tb" id="b-{id}" onclick="st('{id}')">`
- Panels: `<div class="pn" id="p-{id}">` — inside `<form>` for settings that save, OUTSIDE for JS-only panels (like Plugins)
- `st(id)` JS function shows/hides panels and updates active button

### SDR device roles
`SdrDevice.role` options: `"none"` | `"dab"` | `"fm"` | `"scanner"`
- `"scanner"` — designated FM Scanner / WebSDR dongle
- Reported to hub in heartbeat as `scanner_serials`

---

## DAB Plugin Notes

### welle-cli command rules
- **`-C` MUST NOT be used in `_start_dab_session` on non-Pi hardware.** `-C N` for any value of N activates encoders in a carousel — one at a time, ~52 s each — regardless of N vs service count. Confirmed empirically: `-C 20` on a 12-service mux still gives 52 s / 104 s / 156 s / 207 s sequential activation (3.5.86). Without `-C`, welle-cli decodes the full ensemble simultaneously and all services are ready in ~10–15 s. On Pi, use `-T -C N` (where N = `len(session.consumers)`) to limit CPU load.
- **`-C 1` IS correct for `_stream_worker` on Raspberry Pi, but only when no serial is set** — welle-cli rejects `-C` and `-D` together (`Cannot select both -C and -D`). `-D driver=rtlsdr,serial=XXXXX` is required when a serial is configured. Therefore: on Pi with no serial, add `-T -C 1`; on Pi with a serial, add `-T` only. `-T` alone still saves significant CPU by disabling TII decoding. Users with a single DAB dongle can clear the serial in Settings to also benefit from `-C 1`.
- **`-T` flag** disables TII (Transmitter Identification Information) decoding. Not needed for audio playback; saves significant CPU on Pi. Always present in the `_stream_worker` welle-cli command.

### `_is_raspberry_pi()` helper (dab.py)
Reads `/proc/device-tree/model` (or `/sys/firmware/devicetree/base/model` as fallback). Returns `True` if file contains `"raspberry pi"` (case-insensitive). Returns `False` on any exception or on non-Pi hardware.

### Plugin install / `_make_isolated_app` rule
`_wrap_view` in `_make_isolated_app` must cache wrappers by `id(view_fn)` in a closure-level `_wrap_cache` dict. Never create a new closure per call — stacked route decorators on the same function (e.g. `@app.get("/x") @app.get("/x/<id>")`) produce two calls to `_wrap_view` for the same function. Without caching, two different wrapper objects are produced → Flask `AssertionError: View function mapping is overwriting an existing endpoint function` → the `except` in `_load_plugins` catches it → `_plugins.append(info)` never runs → plugin shows "Restart needed" forever.

---

### Hub overview live bars broken for stream names with `+`, `&`, or other special chars (fixed 3.5.101)
`HUB_TPL` and `HUB_WALL_TPL` built element IDs using a Jinja2 `|replace(...)` chain that only handled ` `, `/`, `.`, `-`, `(`, `)`. The JavaScript `_liveKey()` / `_wLiveKey()` functions used `replace(/[^a-zA-Z0-9|]/g, '_')` — a comprehensive regex that replaces ANY other character. For stream names like "BBC Radio 4+", the Jinja2 ID contained `+` but the JS key had `_` instead, causing `document.getElementById()` to silently return `null`. Live level fills, peak hold, L/R bars, and silence state never updated for those streams.

Fix: registered `_jinja_safe_lkey` as the `safe_lkey` Jinja2 filter (`re.sub(r'[^a-zA-Z0-9|]', '_', s)`). Both templates now use `|safe_lkey` instead of the limited replace chain.

**Rule**: Any Jinja2 template that generates an element `id` from a site-name + stream-name combination MUST use `|safe_lkey` (not `|replace(...)` chains). The filter is registered at app startup and mirrors the JS `_liveKey` / `_wLiveKey` functions exactly. Never add new replace chains for this purpose — they will miss characters.

**Rule**: The JS `_liveKey(site, stream)` and `_wLiveKey(site, stream)` functions in `HUB_TPL` and `HUB_WALL_TPL` use `replace(/[^a-zA-Z0-9|]/g, '_')`. The `safe_lkey` Python filter uses `re.sub(r'[^a-zA-Z0-9|]', '_', s)`. These are equivalent. Never change either without updating the other.
