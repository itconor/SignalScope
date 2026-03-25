# SignalScope Changelog

---

## [3.3.91] - 2026-03-25

### Fixed
- **FM Scanner no audio in Safari** (`scanner.py` v1.0.1) — `AudioContext.resume()` alone is not sufficient to activate the context in Safari; the browser requires actual audio output to be scheduled from within the user-gesture handler. Added `_unlockAudio()`: plays a 1-sample silent `AudioBuffer` synchronously in the Connect button's click handler, which forces Safari to fully activate the context. Also added a buffer-overflow guard in `_scheduleBlock`: if `_nextTime` is more than 3 s ahead of `currentTime` (i.e. blocks accumulated while the context was suspended), the schedule is reset to `currentTime + _PRE` and `_sched` is zeroed — preventing a burst of stale audio when the context finally activates.

## [3.3.90] - 2026-03-25

### Added
- **DAB Scanner plugin** (`dab.py`) — new plugin for DAB digital radio reception. Scans all European Band III channels with `welle-cli` to discover services and stores a per-site service database (`dab_services.json`). Services are grouped by ensemble in a browser panel; clicking a service tunes to it. Audio pipeline: `welle-cli | ffmpeg → MP3` at selectable bitrate (64/96/128/192/256 kbps), streamed to a browser `<audio>` element via the hub relay. DLS (Dynamic Label Segment) scrolling text is extracted from `welle-cli` stderr and polled by the browser (equivalent to FM RDS RadioText). Features: service browser, DLS display, history, presets, band scan with live progress. Hub-only plugin; client machines require `welle-cli` and `ffmpeg` in PATH. Added to `plugins.json` registry.

## [3.3.89] - 2026-03-25

### Added
- **Plugin update checking** — Settings → Plugins now automatically checks the GitHub registry for updates whenever the Plugins tab is opened (once per page load, no button click needed). Each plugin shows its installed vs latest version and one of three states: **✓ v1.0.0** (up to date), **⟳ Update to v1.0.1** (update available, orange button), or **⬇ Install** (not yet installed). A summary line ("2 updates available · 1 new") appears above the list. The manual **↻ Refresh** button forces a re-check. `_scan_installed_plugins()` now extracts the version from inactive plugin files via regex so version info is available even for plugins installed but not yet restarted. All plugins declare a `version` field in `SIGNALSCOPE_PLUGIN`.

## [3.3.88] - 2026-03-25

### Fixed
- **FM Scanner no audio after plugin move** — Chrome's autoplay policy requires `AudioContext.resume()` to be called synchronously inside a user-gesture handler. The Connect button's click handler now calls `_initAudio()` and `_audioCtx.resume()` immediately (before the async `fetch` to start the session), so the context is always in `running` state by the time PCM blocks arrive. A second safeguard `resume()` call is added at the top of `_scheduleBlock` to handle any edge cases where the context is suspended when data arrives.

## [3.3.87] - 2026-03-25

### Changed
- **FM Scanner extracted to plugin** — the FM Scanner is now a standalone `scanner.py` plugin (drop alongside `signalscope.py` on the hub machine). All routes (`/hub/scanner`, `/api/hub/scanner/*`, `/hub/scanner/stream/*`, `/hub/scanner_scan_result`) and the browser template are implemented in the plugin. The core client-side scanner logic (dongle management, audio streaming, RDS, band scan) remains in `signalscope.py` unchanged. The hardcoded **📻 FM Scanner** button in the hub dashboard is removed; the plugin nav item is injected automatically by the plugin loader.

## [3.3.86] - 2026-03-25

### Fixed
- **Web SDR silence after second frequency change** — race condition in `_stop_capture` / `_start_capture`: the stop event was set on the old worker but the function returned immediately without waiting for the thread to exit. The new worker then started a second `rtl_sdr` process while the old one still held the device, causing `librtlsdr` to return "device busy" and the new worker to exit silently. Fixed by: (1) storing the subprocess handle in `_client_sess` as soon as it is created; (2) terminating the process directly in `_stop_capture` so `proc.stdout.read()` unblocks immediately; (3) joining the old thread with a 2-second timeout before returning, so `_start_capture` never opens the dongle while the previous process is still running. `_start_capture` now explicitly calls `_stop_capture` first to ensure ordering.

### Added
- **Web SDR kHz/MHz unit toggle** — a unit button next to the frequency field switches between MHz and kHz entry. Clicking toggles the label (MHz → kHz, highlighted in blue) and converts the current value. All frequency entry paths — manual input, waterfall click-to-tune, and the ±0.1 step buttons — respect the active unit.

## [3.3.85] - 2026-03-25

### Fixed
- **Web SDR waterfall never appears** — the "Connect to see spectrum" overlay (`noSig`) was only hidden when enough PCM audio had been scheduled. If the client hadn't yet delivered audio (e.g. startup latency), the overlay permanently covered the canvas even while the waterfall was rendering correctly behind it. `noSig` is now hidden immediately on the first spectrum frame received from the server, decoupling waterfall visibility from audio state. On connect, the overlay message changes to "Waiting for signal from client…" so the user can see that a connection attempt is in progress.
- **Waterfall blurry on retina/HiDPI displays** — `_resize()` computed `devicePixelRatio` but never applied it. Canvas internal resolution is now set to `clientWidth × dpr` × `clientHeight × dpr` with matching CSS size overrides, giving sharp rendering on HiDPI screens. First resize deferred to `requestAnimationFrame` to ensure flex layout has settled before reading dimensions.

## [3.3.84] - 2026-03-25

### Fixed
- **Web SDR Connect button does nothing** — the WebSDR template's `<script>` and `<style>` blocks had no CSP `nonce` attribute, so the browser's Content Security Policy blocked the entire script. Added `nonce="{{csp_nonce()}}"` to both tags. Also added `<meta name="csrf-token">` to the page `<head>` and replaced the stale per-load `_csrf` cookie capture with a `_getCsrf()` function that reads the meta tag fresh on every request (same fix as 3.3.81 applied to the settings page).

## [3.3.83] - 2026-03-25

### Added
- **Plugin update from GitHub** — in **Settings → Plugins → Check GitHub for plugins**, installed plugins now show an **⟳ Update** button instead of a static "Installed" badge. Clicking it re-downloads the plugin file from the official repository and overwrites the local copy. A restart is required to apply the update. Uses the same validated `/api/plugins/install` endpoint as the initial install.

## [3.3.82] - 2026-03-25

### Fixed
- **Web SDR "No sites with Scanner dongle"** — the site selector was filtered to only show sites with `scanner_serials`, so any connected site whose dongle wasn't yet set to Scanner role was silently hidden. Now all connected, approved sites are shown; sites without a Scanner dongle are listed as disabled with a `(no Scanner dongle)` label so the user can see them and know what to configure. Also added support for the hub machine's own Scanner dongles in hub/both mode (the hub itself was invisible in its own site list).
- **Web SDR site selector serial lookup** — the SDR serial dropdown now reads serials from the `data-serials` attribute embedded in the site `<option>` at render time instead of making a separate `GET /api/hub/scanner/devices/<site>` API call.
- **Plugin shows "Restart needed" even after restart** — `_scan_installed_plugins()` matched plugins by `py.stem` (e.g. `"sdr"`) against `active_ids` built from the loaded plugin's `id` field (e.g. `"websdr"`). Since `sdr.py` declares `id = "websdr"` the match always failed. Fixed by storing `_src = py.name` on each entry in `_plugins` at load time and matching by source filename in `_scan_installed_plugins()`.
- **Web SDR nav item shown in client mode** — `topnav()` injected all loaded plugins unconditionally. Plugins can now declare `"hub_only": True` in their `SIGNALSCOPE_PLUGIN` manifest; these are suppressed from the nav when the node is in client-only mode. `sdr.py` sets this flag.

## [3.3.81] - 2026-03-25

### Fixed
- **Plugin Install/Remove CSRF fails on HTTPS hub** — the plugins panel captured the CSRF token once at IIFE execution time from the cookie (`var _csrf = document.cookie.match(...)`). On hubs behind an SSL proxy the `csrf_token` cookie is set with `Secure=True; SameSite=Strict`; under certain browser/proxy combinations the cookie read could return a stale or empty value. Replaced the captured variable with `_getCsrf()` — called fresh on every POST — which prefers the `<meta name="csrf-token">` tag (rendered server-side with the exact session token) and falls back to the cookie. This matches the pattern already used by `_csrfFetch` everywhere else in the settings page.

## [3.3.80] - 2026-03-25

### Fixed
- **Plugin Install/Remove buttons did nothing** — root cause was CSP `script-src-attr 'unsafe-hashes'`. The Remove button used a Jinja2-rendered `onclick="pluginRemove('{{p.file}}')"` whose hash was computed at startup against the literal template source, not the rendered value, causing a hash mismatch. The Install button was generated dynamically in JS at runtime and could never be pre-hashed. Fix: removed all per-element `onclick=` attributes from the plugins panel. Remove buttons now carry `class="plugin-rm-btn" data-file="..."` and Install buttons carry `class="plugin-install-btn" data-id="..." data-url="..." data-file="..."`. A single delegated `click` listener inside the nonce-covered `<script>` block routes clicks to `pluginRemove()` / `pluginInstall()` via `closest()`.

## [3.3.79] - 2026-03-25

### Added
- **Settings → Plugins panel** — new tab in Settings shows installed plugins (with active/restart-needed status and a Remove button) and lets you browse + install available plugins directly from the SignalScope GitHub repository.
- **Plugin registry (`plugins.json`)** — published at the repo root; lists available plugins with name, description, requirements, and download URL.
- **API endpoints**: `GET /api/plugins`, `GET /api/plugins/available`, `POST /api/plugins/install`, `POST /api/plugins/remove`. Install endpoint validates that the source URL is from the official repo and that the file contains `SIGNALSCOPE_PLUGIN` before writing to disk.

## [3.3.77] - 2026-03-25

### Fixed
- **FM Scanner stereo indicator always shows MONO** — the heartbeat RDS ingestion guard only updated `sess["rds"]` when `ps` or `rt` was present. Fields like `stereo`, `tp`, `ta`, and `pi` that arrive from redsea before any PS/RadioText was decoded were silently dropped. The guard now updates on any non-internal RDS field (any key not prefixed with `_`).

## [3.3.76] - 2026-03-25

### Added
- **Scanner dongle role** — SDR devices in Settings now have a `Scanner` role option alongside DAB/FM/None. Marking a dongle as `Scanner` designates it exclusively for the FM Scanner page.
- **FM Scanner site filtering** — the FM Scanner page now only shows sites that have at least one dongle configured with `role = Scanner`. Sites with no scanner dongle assigned are hidden entirely.
- **Scanner-only SDR dropdown** — `/api/hub/scanner/devices/<site>` now returns only `Scanner`-role serials (reported in each client heartbeat) instead of scraping all stream device URLs. The SDR selector on the scanner page therefore only shows the designated scanner dongle(s).

### How to migrate
1. Go to **Settings → SDR Devices** and change the role of your FM scanner dongle from `FM` or `None` to **Scanner**.
2. Save and allow one heartbeat cycle (~30 s) for the hub to pick up the new role.
3. The FM Scanner page will then show only sites with a designated scanner dongle.

## [3.3.75] - 2026-03-25

### Added
- **Plugin system** — drop any `*.py` file next to `signalscope.py` that exports `SIGNALSCOPE_PLUGIN` and it is auto-loaded at startup. Plugins register Flask routes via a `register(app, ctx)` call and get a nav bar item injected automatically between hub links and Settings. Zero footprint when no plugin files are present.

## [3.3.74] - 2026-03-24

### Fixed
- **FM Scanner — clicking history, presets, or scan results does nothing when idle**
  All three click handlers were gated behind `_state === 'streaming'`, so they silently did nothing when no stream was active. Added `doTuneOrStart(freq)`: if already streaming it calls `doTune` (fast retune); if idle with a site selected it calls `doStart` to connect at that frequency directly.

## [3.3.73] - 2026-03-24

### Fixed
- **FM Scanner — band scan unavailable when not streaming**
  The scan button was gated behind `!on` (only enabled during an active stream). Scan now works whenever a site is selected, regardless of streaming state.
- **FM Scanner — band scan returns no results when triggered while streaming**
  `rtl_power` and `rtl_fm` cannot share the same RTL-SDR dongle. Previously triggering a scan while connected always failed silently because the device was claimed. Now the UI automatically disconnects the active stream before pushing the scan command, freeing the dongle for `rtl_power`.
- **FM Scanner — offline scan used wrong SDR device**
  When scanning while disconnected, the band scan command fell back to default SDR params instead of the device/gain/PPM selected in the connect form. The UI now passes `sdr_serial`, `ppm`, and `gain` in the scan request body; the hub prefers those explicit values, falling back to the active session and then to defaults.

## [3.3.72] - 2026-03-24

### Fixed
- **FM Scanner — recurring underruns when RTT > 100 ms (root cause fix)**
  Previous releases raised the relay keepalive threshold but didn't fix the underlying bottleneck: the client POST loop was sequential — each iteration blocked for a full RTT before advancing the deadline clock only 100 ms. When RTT > 100 ms (typical WAN), blocks arrived at the hub at `0.1 s / RTT` of real-time rate, depleting the browser pre-buffer in `_PRE / (1 - 0.1/RTT)` seconds (≈ 2 s at 200 ms RTT).

  Fixed with a two-thread delivery model:
  - **Pacing loop** (existing thread): dequeues PCM blocks from the audio pipeline and enqueues them into a `post_q` at exact `_BLK_DUR` intervals. Never blocks on network I/O.
  - **POST worker thread** (new daemon `ScanPost-*`): drains `post_q`, batching any blocks that accumulated while the previous POST was in-flight into a single request. This means if RTT = 300 ms and 3 blocks queued, one POST sends 0.3 s of audio in 300 ms — exactly real-time throughput regardless of RTT.

### Added
- **Delay indicator in FM Scanner UI**: a small badge in the status bar shows live buffer depth (`buf NNN ms`) and the round-trip time to the SDR client (`rtt NNN ms`). Buffer depth is updated on every audio block (~10×/s); RTT is refreshed from the status poll every 2 s. Badge is hidden when not streaming.

## [3.3.71] - 2026-03-24

### Fixed
- **FM Scanner — adaptive relay keepalive to prevent recurring underruns on variable WAN links**
  The fixed 1.0 s keepalive threshold in 3.3.70 worked for typical WAN conditions but could still cause underruns on links with variable latency (cellular, congested ISP paths, etc.). The relay now auto-tunes its keepalive threshold and poll interval from the measured round-trip time of each chunk POST:
  - `_kp_threshold = max(0.3 s, min(0.8 s, 0.1 + rtt_ema × 3))` — silence is only injected when no chunk has arrived for long enough to be truly abnormal at the measured RTT, not just a momentary jitter spike.
  - `get_timeout = max(0.15 s, min(0.5 s, rtt_ema × 2))` — relay polls the slot queue at a rate matched to the connection speed rather than a fixed 300 ms.
  - The SDR client now measures the RTT of every chunk POST and includes it in the `X-Client-Rtt` header; the hub updates a per-slot EMA (α=0.2) on each received chunk so the relay always operates with a recent estimate.
  - Browser pre-buffer (`_PRE = 1.0 s`) remains ≥ the adaptive threshold ceiling (0.8 s), so silence injected at the relay never causes a browser underrun.

## [3.3.70] - 2026-03-24

### Fixed
- **FM Scanner — choppy audio when hub is hosted remotely from the SDR client (WAN)**
  The relay generator in `generate_scanner()` was injecting keepalive silence blocks whenever no PCM chunk arrived within 0.25 s (`_KP_THRESHOLD`). When the hub and the SDR client are separated by a WAN link, normal internet round-trip jitter easily exceeds 250 ms, causing silence to be injected on almost every block. The browser received a stream of real-audio / silence / real-audio / silence alternations, heard as rapid choppy dropouts even though no audio data was actually lost.
  Three values tuned together:
  1. **`_KP_THRESHOLD` 0.25 s → 1.0 s** — relay no longer injects silence unless a full second passes with no data, accommodating typical WAN latency without treating it as a gap.
  2. **`slot.get(timeout=)` 0.12 s → 0.30 s** — relay polling loop sleeps up to 300 ms per iteration before declaring a timeout, reducing the number of empty-queue wakeups when WAN blocks arrive in 150–250 ms.
  3. **`_PRE` (browser pre-buffer) 0.3 s → 1.0 s** — on connect the browser schedules audio 1.0 s ahead of `currentTime` instead of 0.3 s, giving the relay enough runway to absorb the raised keepalive threshold without the audio clock catching up to the buffer edge.

## [3.3.69] - 2026-03-24

### Fixed
- **FM Scanner — audio slow / glitchy every ~0.5 s** — three causes fixed:
  1. **`out_deadline` burst on startup** — the relay deadline clock was initialised to `time.monotonic()` before the loop started, but the pipeline discards the first 15 blocks (~1.5 s of silence) before putting real audio into the queue. When the first real block arrived, the deadline was already 1.5 s in the past, causing 15 blocks to be sent in a rapid burst. The browser then scheduled 1.5 s of audio in advance, creating persistently high latency. Fixed by deferring `out_deadline` initialisation to the moment the first block is actually dequeued.
  2. **Numpy RMS on the pipeline thread** — signal level was computed inline in `_pipeline` (numpy `frombuffer` + `mean` + `sqrt` + two `_scanner_rds_lock` acquisitions) every 10 blocks. This work on the hot audio path introduced intermittent stalls. Moved to a dedicated `_level_computer` daemon thread that drains a `level_q` (maxsize=1, drops if behind) so the pipeline thread is never blocked.
  3. **Triple lock acquisition in `_rds_reader`** — the RDS reader was calling `_get_scanner_rds()` (which acquires `_scanner_rds_lock`) separately for the PS guard, the RT guard, and the final update — three lock acquisitions per redsea JSON line when both PS and RT were present. Replaced with a single `_get_scanner_rds()` call at the start of each line's processing; the cached dict is reused for both guards and the final `_set_scanner_rds` call.

## [3.3.68] - 2026-03-24

### Fixed
- **FM Scanner — unstable RDS PS / RadioText names** — the scanner's `_rds_reader` was forwarding every raw PS string from redsea directly to the browser as soon as it arrived. redsea emits partial PS names as it assembles each 8-character block (one segment at a time), so the display was flickering between partial names, garbled fragments, and the correct name. Applied the same majority-vote stabilisation used by the client FM monitor: PS candidates are accumulated in a rolling 12-entry history and only promoted when the same candidate appears ≥ 3 times **and** is at least as long as the current confirmed name. A shorter candidate can never overwrite a confirmed longer name, preventing partials from clearing a locked name on retune. RadioText uses the same approach with a 10-entry history requiring ≥ 2 matches (or length ≥ 12 for long texts that may naturally vary slightly).

## [3.3.67] - 2026-03-24

### Fixed
- **FM Scanner — audio slow / dropping bits after 3.3.66** — `_saveHistory` was called on every 2-second status poll tick whenever a PS (station name) RDS field was present. Each call did a synchronous `localStorage.getItem` + `JSON.parse` + array operations + `localStorage.setItem` + a full `innerHTML` DOM rebuild. `localStorage` writes are synchronous and can stall the JS event loop for 10–50 ms, delaying the PCM fetch-pump callbacks long enough to cause audio scheduling drift and glitches. Fixed by: (1) caching the last-seen PS name and frequency — history is only written when they actually change (typically once per station tune, not every 2 s); (2) passing the freshly-built list directly to `_renderHistory` to avoid a redundant second `localStorage.getItem`; (3) removing the spurious `_saveHistory` call that was inside `_scheduleBlock` (the connection-to-streaming state transition), which ran on every audio block until streaming state was confirmed.

## [3.3.66] - 2026-03-24

### Added / Changed
- **FM Scanner — comprehensive UI and backend overhaul**
  - **Gain & PPM controls** added to the setup bar; both values are forwarded to `rtl_fm` on connect.
  - **Signal level bar** — shows live RMS dBFS (computed from the PCM stream every 10 blocks) with colour-coded fill: green (> −20 dBFS), amber, red (< −40 dBFS).
  - **Extended RDS panel** — now displays PTY genre badge, STEREO/MONO indicator, TP (Traffic Programme) and TA (Traffic Announcement) flags, PI code, clock-time (CT), and a hoverable AF (Alternative Frequency) count badge in addition to PS name and RadioText.
  - **Frequency history** — last 10 tuned frequencies are persisted to `localStorage` and shown as a clickable panel; updated automatically with the RDS PS name as soon as it arrives.
  - **Presets** — save named station bookmarks to `localStorage` with one click (or Enter); recall or delete from the panel; presets survive page reloads and browser restarts.
  - **Record button (30 s / 60 s)** — triggers a WAV download built from the hub's rolling 60-second PCM ring buffer (`GET /api/hub/scanner/record/<site>?secs=N`).
  - **Band scan panel** — "Scan" button triggers `rtl_power` 76–108 MHz sweep on the remote SDR; results poll automatically and appear as clickable frequency/dB cards that retune the scanner in one click.
  - Tuner card widened to 700 px max-width to accommodate the new panels.

## [3.3.65] - 2026-03-24

### Changed
- **Wall page — broadcast chains redesigned** — chains now render as a responsive card grid (auto-fill, min 360 px) instead of full-width horizontal rows. Each card has a coloured border and dot indicator that reflects chain status (green OK, red FAULT, amber AD BREAK, blue maintenance). Stack nodes (redundant pairs) are now rendered as a grouped bordered box showing each sub-node individually. All nodes show a live audio level bar and dB value. Fault-point nodes pulse with a red glow animation and display a FAULT tag. Downstream nodes from the fault point are visually dimmed. The badge shows fault duration (e.g. "FAULT 2m 15s") tracked client-side. AD BREAK / CHECKING states are reflected on the badge and card border.

## [3.3.64] - 2026-03-24

### Added
- **Signal chains — chain-level maintenance mode** — added a **🔧 Maint** button to the chain header that puts every node in the chain into maintenance at once. Uses the same duration popover as per-node maintenance (30 min / 1 h / 2 h / 4 h / ✕ Clear) with a note "Applies to all nodes in this chain". A **🔧 Maintenance** badge appears in the chain header whenever any node is in maintenance. New `POST /api/chains/<cid>/maintenance_all` endpoint handles the bulk set/clear.

## [3.3.63] - 2026-03-24

### Changed
- **Login page — security & UX improvements** — removed software version number from the login page (no need to advertise the build to unauthenticated visitors). Added a pulsing green "● System online — `<hostname>`" indicator so operators know which system they're signing into (uses configured `hub.site_name` if set, otherwise the machine hostname). Replaced the redundant footer text with a clean GitHub link. Version number is still shown inside the app after login.

## [3.3.62] - 2026-03-24

### Fixed / Added
- **Hub remote backup — always showed "Backup pending", never completed** — Three fixes: (1) `_daily_backup_loop` was sleeping 3600s *before* its first check, meaning new sites could wait up to 24h for their first auto-backup. The loop now runs immediately on startup, triggering backup for any online site with no backup or last backup ≥23h ago. (2) Added a **↻ Backup Now** / **📥 Backup Now** button to each site card on the hub dashboard, replacing the static "Backup pending" badge. Clicking sends a `backup` hub command via `/api/hub/site/<site>/backup`, then polls `/api/hub/site/<site>/backup_status` every 3 seconds for up to 30 seconds and reloads the page when the backup arrives. (3) Added `GET /api/hub/site/<site>/backup_status` API endpoint returning `{ts, size, age_s}` for JS polling.

## [3.3.61] - 2026-03-24

### Fixed / Added
- **Chain fault clips — nodes with recording disabled produced empty clips** — `_fire_chain_fault` was using each node's own `alert_wav_duration` (which can be 0 if "record on silence" is off) as the clip duration. With `alert_wav_duration=0` the audio buffer is only 2 chunks (42ms). Now uses `max(alert_wav_duration, CHAIN_CLIP_MIN_SECS=10s)` for all chain nodes, overriding the individual setting.
- **Chain fault clips — only 1-2 clips appearing in fault timeline** — remote `save_clip` commands now include a `duration` field; `_cmd_save_clip` on the client uses it instead of the local stream's own duration.
- **Chain fault timeline — late-arriving remote clips not shown** — fault log timeline now auto-refreshes once after 15 seconds so clips uploaded asynchronously from remote clients appear without a manual page reload.
- **Chain fault recording — full event capture (fault start → recovery + tail)** — added `_schedule_chain_recovery_clips`: at chain recovery, a daemon thread waits `fault_tail_secs` seconds then saves a clip from every chain node with duration `min(fault_duration + tail + 10s, 300s)`. This captures the entire event arc. Configurable via new chain settings `fault_tail_secs` (default 20s) and `record_all_nodes` (default true).

## [3.3.60] - 2026-03-24

### Added
- **Hub dashboard / replica page — on-demand remote log pull** — the 📋 Log button now shows the last 30 lines from the heartbeat cache instantly, with a **↻ Pull fresh log** button that sends a `push_log` hub command to the client. The client gathers its last 200 log lines and posts them back to the hub via `/hub/log_data`; the modal polls for the result (up to ~12 seconds, one heartbeat cycle). The dump is shown newest-first with a timestamp; the modal distinguishes between fresh pulled data and cached heartbeat data. No continuous data flow — only transmits when the button is clicked.

## [3.3.59] - 2026-03-24

### Fixed
- **Local audio input (sound://) — no audio in web player** — `_run_sound` fed PCM to `analyse_chunk` for monitoring but never wrote chunks into `cfg._stream_buffer` / `cfg._live_chunk_seq`. Every other input type does this after `analyse_chunk`; the sound device path was simply missing those two lines. Levels showed correctly but the live player had nothing to stream.

## [3.3.58] - 2026-03-24

### Fixed
- **Hub clip upload always fails with `FileNotFoundError: [Errno 2] No such file or directory: ''`** — In the heartbeat loop's auto-clip-upload drain, `_cpath` was passed as the 7th positional argument to `_upload_clip`, which maps to `chain_id`. The `clip_path` keyword argument therefore defaulted to `""`, causing `open("", "rb")` to fail on every clip. Fixed by passing `clip_path=_cpath` as a keyword argument.

## [3.3.57] - 2026-03-24

### Added
- **DAB channel scan — SNR badge on result cards** — each found mux now shows a colour-coded SNR badge alongside the channel/ensemble name: green (≥15 dB, good), amber (8–15 dB, marginal), red (<8 dB, weak). SNR was already measured during the probe but only visible on hover.

## [3.3.56] - 2026-03-24

### Fixed
- **DAB service audio endpoint probe deadline too short after channel scan** — After a channel scan ran through a monitored channel (e.g. 12D), the residual USB initialisation state caused the last service endpoint(s) to take longer than usual to start serving audio. The 15-second audio-endpoint probe deadline was insufficient; the service would be found in the mux listing but its `/mp3/` endpoint missed the window. Increased probe deadline from 15s→35s. Also increased the startup stale-welle-cli USB settle delay from 0.5s→1.2s to give the USB stack more time to fully release the interface before the monitoring welle-cli opens it.

## [3.3.55] - 2026-03-24

### Fixed
- **DAB shared mux session never becomes ready on marginal-signal sites** — The `_poll_mux` stability check required two consecutive service-count polls with a 5-second inter-poll sleep. On weak signals the mux can take 8–10s to first appear, so the minimum ready time was 8–10s + 5s + 1s = 14–16s — past the 12-second consumer wait cap. All streams then timed out, released the session, and retried until the 30-second startup window expired. Fix: raised consumer ready wait cap 12s→25s, reduced stability inter-poll sleep 5s→2s (minimum ready time now ~12–13s on weak signals), and raised startup deadline 30s→120s to allow genuine retries on poor sites without exhausting the window.
- **Restart via web UI orphans welle-cli processes** — `api_admin_restart` used `os.execv` to replace the process, leaving all child welle-cli instances running and holding the SDR dongle. The new process then couldn't open the device. Fix: kill all welle-cli processes (+ 0.8s USB settle) before exec'ing.

## [3.3.54] - 2026-03-24

### Added
- **Process control UI** — Settings → Maintenance now has a **Restart SignalScope** button (`/api/admin/restart`) and a **Kill orphan DAB processes** button (`/api/dab/kill_orphans`). Restart uses `os.execv` to re-exec the process in place; kill orphans sends SIGKILL to any running `welle-cli` processes and waits 1s for USB stack to settle. Both are accessible from any machine's settings page (hub or client) without shell access.
- **Kill orphan DAB processes** button also added inline in the DAB input configuration panel as **🔌 Free dongle** — available at the point of use when a channel/mux scan leaves the dongle claimed.

## [3.3.53] - 2026-03-24

### Fixed
- **DAB channel scan leaves dongle claimed after scan completes** — `_dab_quick_probe` called `proc.kill()` but never called `proc.wait()` afterward, leaving the USB device held by libusb. Any subsequent DAB session or scan would fail to open the dongle. Fix: wait up to 4s for clean SIGTERM exit, fall back to SIGKILL + `proc.wait()` to ensure the OS fully reaps the process, then add an 0.8s USB stack settle delay before returning. Whether the race triggered depended on USB controller speed, explaining why it affected some machines and not others.

## [3.3.52] - 2026-03-24

### Fixed
- **DAB channel scan — probe timeout too short for weak signals** — `_dab_quick_probe` used a 10-second per-channel timeout. DAB sync on a marginal signal can take 12–15 seconds, so weak-signal sites were timing out on every channel and returning no results even when a mux was present. Increased default probe timeout from 10s to 15s (full sweep ~8 min vs ~5 min previously).

## [3.3.51] - 2026-03-24

### Added
- **DAB — "Scan all channels" button in input configuration** — new 📡 *Scan all channels* button scans every Band III channel (5A–12D, 32 total) and shows a live list of receivable muxes. Each found mux appears as a clickable chip showing the channel, ensemble name, estimated SNR, and service count. Clicking a chip automatically sets the channel dropdown and triggers the existing service scan, so setup on a new site no longer requires guessing which channel to try. A progress bar and ⏹ Stop button are shown during the scan (typically 4–6 minutes for a full sweep; found muxes appear immediately as each channel returns a result). The scan uses the same dongle serial and gain/PPM settings as the mux scan.

## [3.3.50] - 2026-03-24

### Fixed / Added
- **DAB — configurable RTL-SDR gain for weak signal areas** — previously welle-cli was always launched with `-g -1` (hardware AGC) and there was no way to override it. Hardware AGC is fine for strong signals but can fail to decode weak muxes that a GUI tool like welle.io can pick up because it uses software gain control. Fix: added a **Gain** field to the SDR Devices table in Settings. Default is `-1` (hardware AGC, same as before). For weak-signal sites set it to `486` (48.6 dB) or `496` (49.6 dB max) to match the maximum manual gain. The gain is applied to: continuous DAB monitoring sessions, local mux scans, and remote hub-triggered scans.
- **DAB monitoring — PPM correction was silently ignored** — `_start_dab_session` set `session.ppm` from the dongle registry but then logged *"ignoring ppm"* and never passed `-p <ppm>` to welle-cli. Fixed: PPM is now correctly appended as `-p <ppm>` when non-zero.

## [3.3.49] - 2026-03-24

### Changed
- **Broadcast Chains — offline-site faults now say "node offline" not "signal lost"** — when a chain fault is caused by a remote site going offline (hub has not received a heartbeat from that site), the CHAIN_FAULT alert message now explicitly says the node is offline and includes how long ago it was last seen, rather than generically saying "signal lost". This makes it immediately clear whether the chain failure is a transmission/audio issue or a connectivity/monitoring issue.
  - Single-node faults: message changes from *"signal lost at 'TX1' (site: london, stream: …)"* to *"node offline: 'TX1' (site: london, stream: …) is not reporting, last seen 42s ago"*.
  - Stack-node faults: offline sub-nodes are now listed separately from silent sub-nodes (e.g. *"node_a offline; node_b silent"*) so the alert text reflects the true mix of failure modes.

## [3.3.48] - 2026-03-24

### Fixed
- **Hub client — stops retrying after hub downtime** — three issues combined to make clients appear to "give up" permanently when the hub went offline:
  1. **Silent retry loop**: `_prev_err` deduplication logged the `<urlopen error timed out>` message only once, then went completely silent even though retries were still happening. Operators had no way to confirm the client was still active. Fixed: periodic retry log now prints on the first failure and every 5th consecutive failure, showing the backoff level and next retry interval.
  2. **No reconnect confirmation**: when the hub came back online, reconnection happened silently. Fixed: a `[HubClient] Reconnected to hub after N failure(s)` message now prints when the first successful send follows a run of failures.
  3. **No crash recovery**: if any uncaught exception escaped the inner try/except blocks in `_loop` (e.g. from `_cfg_fn()`, `_normalise_url()`, or any command handler), the daemon thread would die silently and never restart. Fixed: top-level `try/except` wraps the entire while-loop body; any unexpected exception is logged with a full traceback and the loop retries after `BASE_WAIT`.
- **Hub client — slow failure detection**: `urlopen` timeout reduced from 10 s to 5 s. At maximum backoff (10 consecutive failures) the retry interval drops from 70 s to 65 s, and the ramp to max backoff is faster — reaching steady-state in roughly 50 s of hub downtime rather than 100 s.

## [3.3.42] - 2026-03-24

### Changed
- **FM Scanner — style updated to match hub dashboard** — replaced isolated CSS variables and custom header with the hub's colour palette (`--bg:#07142b`, `--sur:#0d2346`, `--acc:#17a8ff`, etc.), radial gradient body, gradient header, hub-style card (`.tuner`), matching nav back-link, and consistent footer. All button/input styles updated to match hub interactive elements.
- **FM Scanner — RDS display** — when `redsea` is installed, the scanner pipeline switches to 171 kHz output and pipes the FM-demodulated stream through `redsea -j`. PS name (station name) and RadioText are parsed from the JSON output, piggybacked on the heartbeat payload, stored in the hub scanner session, and returned by `/api/hub/scanner/status`. The scanner UI displays them in an LCD-style RDS panel below the frequency readout; RadioText scrolls as a marquee when longer than 28 characters. The panel is hidden when no RDS is available. If `redsea` is not installed, the scanner falls back to 48 kHz audio-only mode with no change in audio quality or latency.

## [3.3.41] - 2026-03-24

### Fixed
- **FM Scanner — no audio on Safari (streaming WAV rejected)** — Safari's AVFoundation immediately closes HTTP connections whose `Content-Type: audio/wav` response has no `Content-Length` and uses chunked transfer encoding. This caused `generate_relay()`'s `finally` block to fire within milliseconds, removing the slot and causing all subsequent client POSTs to return 404. Fix: completely replaced the `<audio>`-element + WAV-streaming approach with **Web Audio API + `fetch()` streaming** using raw S16LE PCM (`application/octet-stream`). The browser JS reads the fetch `ReadableStream`, decodes 4800-sample S16LE blocks, and schedules them via `AudioContext.createBufferSource()`. This works identically on Chrome, Firefox, and Safari.
- **FM Scanner — nginx proxy_read_timeout on hub → browser stream** — the hub's `generate_relay()` previously waited silently for the first client POST before yielding anything. If the client took longer than nginx's `proxy_read_timeout` the upstream connection was closed, removing the slot. Fix: new `_hub_scanner_relay_response()` immediately yields a silence block on connection, then continues sending paced silence at 0.1 s intervals until the first real PCM chunk arrives from the client. nginx never sees an idle upstream.
- **FM Scanner — WAV header removed from client pipeline** — the client (`_push_scanner_audio`) no longer constructs or sends a WAV header. The hub owns all stream framing; the client sends raw S16LE PCM only.

## [3.3.40] - 2026-03-24

### Fixed
- **FM Scanner — HTTP 404 on every audio_chunk POST (slot removed before first chunk)** — the WAV header used `RIFF` chunk size = 0 and `data` chunk size = 0. Browsers interpret `data` size = 0 as "zero bytes of audio data" and immediately close the HTTP connection with an error event. This caused `generate_relay()`'s `finally` block to fire and remove the slot from the registry within milliseconds of the first chunk being yielded — before the client's next POST could reach the hub. Fix: set both sizes to `0x7FFFFFFF`, the standard sentinel for "unknown/streaming length" used by streaming WAV servers (Icecast, rtl_fm_streamer, etc.). Browsers correctly treat this as a live stream and keep the connection open until it closes naturally.

## [3.3.39] - 2026-03-24

### Fixed
- **FM Scanner WAV stream error — silence during burst skip** — 3.3.38 sent the 44-byte WAV header as a separate first POST, then the stream went silent for 3 seconds while the USB startup burst was discarded. Browsers fire an error event on an audio stream that stalls immediately after the header. Fix: the pipeline now replaces burst blocks with silent PCM (`0x00` bytes) instead of discarding them, and prepends the WAV header to the very first silent block. The stream flows continuously from the first chunk; the browser hears ~1.5 s of silence then clean FM audio without ever seeing a gap.
- **FM Scanner — reduced burst skip from 30 to 15 blocks** — the measured USB burst is ~14 blocks; 15 gives a one-block margin while halving the initial silence from 3 s to 1.5 s.
- **FM Scanner UI — removed Quality/bitrate selector** — WAV streaming is uncompressed so the bitrate setting had no effect; removed the selector and all related JavaScript to avoid confusion.

## [3.3.38] - 2026-03-24

### Changed
- **FM Scanner — replaced MP3/ffmpeg pipeline with direct WAV streaming** — after multiple attempts to tame the codec buffer timing issues (3.3.25–3.3.37), the entire ffmpeg pipeline has been scrapped in favour of raw PCM streaming, matching the architecture of rtl_fm_streamer. The new path: `rtl_fm -r 48000` → pipeline thread reads 9600-byte blocks (0.1 s at 48 kHz), discards 30-block USB startup burst, enqueues raw S16LE PCM → main thread sends a 44-byte streaming WAV header as the first hub chunk, then dequeues and POSTs PCM blocks with an absolute-deadline clock that absorbs network RTT without risking bursts. Hub slot mimetype changed from `audio/mpeg` to `audio/wav`. No ffmpeg dependency, no codec buffer, no MP3 frame ordering issues. Audio timing is governed by the RTL-SDR hardware clock and the Web Audio API in the browser — the two layers that were always designed for this job.

## [3.3.37] - 2026-03-24

### Fixed
- **FM Scanner out-of-order audio — output clock deadline order** — 3.3.36's output clock slept *before* the POST, so each loop iteration consumed `sleep + POST_time` of wall time. In steady state (no burst) the POST network RTT meant the hub received audio fractionally slower than real-time, the browser's buffer ran dry, and the audio stuttered/skipped. Fix: switched to an absolute-deadline clock — sleep to the deadline, POST, *then* advance the deadline by `chunk_dur`. Both the POST time and the `read()` time are absorbed into the same `chunk_dur` budget, so the hub receives data at exactly the declared bitrate regardless of network round-trip time.

## [3.3.36] - 2026-03-24

### Fixed
- **FM Scanner fast audio — output-side bitrate clock** — the input clock correctly paced writes to ffmpeg stdin at 0.1 s/block, but ffmpeg has an internal codec buffer that can release a burst of MP3 frames to stdout at startup. Without any pacing on the read side, the main thread posted that burst to the hub in rapid succession; the hub queued all chunks immediately; the browser drained the queue faster than real-time and the audio played fast. Fix: a drift-free output clock computes each chunk's duration as `len(data)*8/bitrate_bps` and sleeps until the next target time before POSTing to the hub, ensuring the hub always receives audio at exactly the declared bitrate regardless of ffmpeg's internal buffering.

## [3.3.35] - 2026-03-24

### Fixed
- **FM Scanner — scanner never starts (regression introduced in 3.3.33/3.3.34)** — the duplicate-instance guard added `slot_id` to `_active_slots` and then checked it on entry to `_push_scanner_audio`. But the CALLER (`_push_audio_request` dispatch loop) **already** adds the slot to `_active_slots` before launching the thread — so the entry check inside `_push_scanner_audio` was always true, always returned immediately, and the scanner never ran. The caller's check at `if not slot_id or slot_id in active: continue` is the correct guard against duplicate threads; the redundant inner check has been removed entirely.

## [3.3.34] - 2026-03-24

### Fixed
- **FM Scanner — slot locked forever on any startup failure** — 3.3.33 added `_active_slots` duplicate prevention but several early-return paths (serial resolve error, no SDR devices found, rtl_fm Popen failed, ffmpeg Popen failed) returned without calling `_active_slots.discard(slot_id)`. This permanently locked the slot ID so every subsequent heartbeat delivery was rejected as "already active" and the scanner never actually started. Fix: introduced a `_discard()` helper and called it before every early return that follows the initial `_active_slots.add()`.

## [3.3.33] - 2026-03-24

### Fixed
- **FM Scanner fast audio — clock threshold 0.002 → 0.0005 s** — the drift-free clock in `_pipeline` used `if slack > 0.002: time.sleep(slack)` to guard against sleeping on tiny slacks. Because RTL-SDR hardware delivers blocks very slightly faster than nominal, the computed slack was routinely 1–2 ms — silently skipped every block. Over 100 blocks this accumulated to ~2% total drift, confirmed by rate-check diagnostics showing `avg interval 0.0980 s` vs target `0.1000 s`. Lowering the threshold to `0.0005 s` (0.5 ms) ensures these small slacks are no longer skipped and the clock tracks real-time correctly.
- **FM Scanner out-of-order audio — duplicate instance prevention** — the hub heartbeat may deliver the same scanner slot request more than once before the first `_push_scanner_audio` instance has started POSTing data. With no guard, two concurrent threads would write to the same hub slot producing interleaved, out-of-order MP3 chunks at the browser. Fix: `slot_id` is now added to `self._active_slots` at the very start of `_push_scanner_audio` (under `self._lock`) and checked on entry — duplicate invocations for the same slot return immediately.

## [3.3.28] - 2026-03-24

### Fixed
- **FM Scanner fast audio — mirrors the monitor's read/resample loop** — previous byte-drain approach (3.3.27) was worse because handing the raw pipe mid-stream to ffmpeg caused desync/garble. 3.3.28 uses a pipeline thread that is a direct copy of `_run_fm_rtlsdr`'s read loop: reads 0.1 s blocks of S16LE from rtl_fm, silently discards the first 25 blocks (2.5 s) to flush the librtlsdr USB async buffers (~480 KB ≈ 1.4 s), then resamples each block 171 kHz → 48 kHz with `resample_poly(x, 16, 57)` and writes 48 kHz PCM to ffmpeg stdin. ffmpeg encodes only — no libswresample resampling needed. Main thread does plain blocking `read(4096)` on ffmpeg stdout and POSTs with zero pacing code. After the burst blocks are discarded, `rtl_proc.stdout.read(_BLOCK)` in the pipeline thread naturally blocks at hardware rate, which paces ffmpeg stdin, which paces ffmpeg stdout, which paces the POST loop. Hardware IS the clock.

## [3.3.27] - 2026-03-24

### Fixed
- **FM Scanner fast audio — root cause finally identified and fixed by mirroring the live relay architecture exactly** — the working live relay (kind="live") feeds ffmpeg from `_stream_buffer` gated by `_live_chunk_seq`, so `proc.stdout.read(4096)` blocks naturally at hardware rate — **no pacing code of any kind**. The scanner couldn't do this because it has no `_stream_buffer`; it starts a fresh rtl_fm instead. The fundamental problem is the **USB startup burst**: librtlsdr pre-fills ~15 async buffers (~480 KB ≈ 1.4 s at 171 kHz) before the first real-time data arrives. All previous attempts (reader thread + output clock, throttle thread, direct pipe) failed because they received this burst and either delivered it too fast or deadlocked trying to slow it down. Fix: Python reads and discards 2 seconds of raw S16LE from rtl_fm stdout BEFORE handing the pipe file descriptor to ffmpeg. After the drain, rtl_fm's output is real-time hardware data only. The OS pipe then naturally rate-limits ffmpeg stdin to hardware rate, ffmpeg stdout produces at the same rate, and a plain `proc.stdout.read(4096)` loop (identical to the live relay) paces the POST to hub correctly. No reader thread, no drift-free clock, no queue, no `time.sleep()`.

## [3.3.26] - 2026-03-24

### Fixed
- **FM Scanner fast audio — reader thread + output-side pacing** — 3.3.25's throttle thread controlled the INPUT to ffmpeg but ffmpeg's internal codec buffers could still produce output in bursts, and reading ffmpeg stdout in the main thread (then sleeping before the next read) caused pipe backpressure that made ffmpeg's encode loop irregular. Root fix: split responsibilities cleanly — a **reader thread** drains ffmpeg stdout into an unbounded queue continuously (never sleeps, pipe is always clear), while the **main thread** dequeues chunks and POSTs to the hub paced by a drift-free clock (`next_post += len(data)*8/bitrate_bps`). Sleeping in the main thread is now safe because the reader thread decouples ffmpeg stdout from all main-thread sleeps. The hub therefore receives data at exactly real-time rate regardless of codec or USB startup burst behaviour. rtl_fm → ffmpeg path remains a direct OS pipe (no Python in the signal path). Sample rate stays at 171 000 Hz, identical to the FM monitor.

## [3.3.25] - 2026-03-24

### Fixed
- **FM Scanner fast audio — root cause identified: missing rate controller** — the fundamental difference between the FM monitor (works) and the scanner (fast) is that the monitor loop's blocking reads from rtl_fm ARE the rate controller: `_stream_buffer` only fills at real-time hardware rate, and `stream_live`'s writer reads from that already-paced buffer. The scanner had no equivalent — direct piping from rtl_fm to ffmpeg (3.3.23/3.3.24) meant the rtl_fm USB startup burst flooded ffmpeg faster than real-time, and Chrome sped up playback to prevent its buffer from growing indefinitely. Fix: re-introduce a **throttle thread** that reads raw 171 kHz S16LE blocks from rtl_fm and writes them to ffmpeg stdin paced by a drift-free clock (`next_write += 0.1 s`). Crucially, Python does **not** touch the audio content — no resampling, no float conversion — only the delivery rate is controlled. ffmpeg handles the 171 → 48 kHz resample via libswresample. The throttle thread is structurally identical to `stream_live`'s writer.

## [3.3.24] - 2026-03-24

### Fixed
- **FM Scanner still fast — wrong rtl_fm sample rate (240 kHz → 171 kHz)** — 3.3.23 used `-s 240000` hoping for a clean 5:1 integer ratio in ffmpeg. But RTL-SDR hardware's internal clock divider may not achieve exactly 240 000 Hz; if it snaps to the nearest achievable rate (e.g. 240 384 Hz) while ffmpeg is told "input is 240 000 Hz", every block plays at 240 384/240 000 = 1.0016 fast — perceptible as "slightly fast" audio. Fix: use **171 000 Hz** (the exact rate `_run_fm_rtlsdr` uses for the FM monitor, proven correct on this hardware). The rtl_fm command now mirrors `_run_fm_rtlsdr` flag-for-flag. ffmpeg's libswresample handles the 171 000 → 48 000 Hz (57:16) rational ratio with polyphase quality equivalent to scipy's `resample_poly`.

## [3.3.23] - 2026-03-24

### Changed
- **FM Scanner — remove Python from the audio path entirely** — all previous versions (3.3.15–3.3.22) put Python between rtl_fm and ffmpeg, either resampling PCM, maintaining a mux_buf, or running a drift-free clock. Every attempt introduced its own timing artefacts (running fast, stopping/starting, glitching) because Python's threading and `time.sleep()` cannot pace an audio pipe reliably. New approach:
  - `rtl_fm -s 240000` (FM discriminator, 240 kHz S16LE) pipes directly into `ffmpeg` via the OS pipe — `stdin=rtl_proc.stdout` with `rtl_proc.stdout.close()` on the Python side
  - **No Python in the signal path** — the OS pipe and ffmpeg's own real-time encoding loop handle timing, exactly as they do for all other rtl_fm uses in the codebase
  - ffmpeg resamples 240 kHz → 48 kHz with a clean **5:1 integer ratio** via libswresample (`-ar 240000` input, `-ar 48000` output) — no fractional ratio artefacts
  - 240 kHz is a native RTL-SDR hardware rate (≥ 225 kHz minimum), so no extra software decimation inside rtl_fm
  - Main thread reads 4096-byte chunks from ffmpeg stdout and POSTs to hub — `read(4096)` naturally blocks ~256 ms at 128 kbps, making ffmpeg's own encode pace the rate limiter
  - No pipeline thread, no drift-free clock, no scipy/resample_poly, no mux_buf

## [3.3.22] - 2026-03-24

### Fixed
- **FM Scanner "slightly fast" audio — pipeline clock initialised one block ahead** — 3.3.21's drift-free clock initialised `next_write = time.monotonic()` on the first block, meaning block 1 was always written to ffmpeg stdin with zero sleep. During the rtl_fm USB startup burst, this caused ffmpeg to receive audio slightly faster than real-time (by one block = 100ms) before the clock kicked in. Fix: initialise `next_write = time.monotonic() + _BLK_DUR` so every block including the first must wait its full 100ms, preventing the browser from ever receiving audio faster than real-time.

### Reverted
- **Output-side POST pacing removed** — an intermediate attempt (3.3.22 alpha) added a drift-free clock on the main thread's POST loop. This caused a pipe deadlock: sleeping before reading from ffmpeg stdout stalled the stdout pipe, which caused ffmpeg to stall encoding, which backed up into the pipeline thread's stdin writes. The result was audio that stopped and started in a loop. Rate control must live entirely on the pipeline thread (stdin side); the main thread must read from ffmpeg stdout as fast as data arrives.

## [3.3.21] - 2026-03-24

### Fixed
- **FM Scanner audio — single pipeline thread, drift-free clock** — previous 3.3.20 approach used a separate reader thread and writer thread sharing a deque. A silent bug killed the writer: if rtl_fm's USB startup burst filled the deque to ≥ 2 items before the writer's first prefill loop iteration ran, `now < None` (`TypeError`) was silently caught by `except Exception: pass`, leaving the writer dead and ffmpeg stdin orphaned. Replaced the entire reader/writer/deque architecture with a single `_pipeline` thread:
  - Reads raw 171 kHz S16LE from rtl_fm, assembles 100 ms blocks (34200 bytes), resamples with `resample_poly(x, 16, 57)` → 48 kHz float32 → S16LE
  - Paces writes to ffmpeg stdin with a **drift-free clock**: `next_write += 0.1` (100 ms per block). On startup burst, the accumulated USB buffer drains while the clock holds real-time; no reset on underflow
  - Main thread reads raw ffmpeg MP3 output in 4096-byte reads and POSTs directly to hub — exactly mirroring `_push_audio_request` kind="live", which is the proven-working live listen path
  - No deque, no prefill, no shared state between threads except the ffmpeg pipe and a `threading.Event` stop flag
- **Root cause of 3.3.20 failure** — the writer thread's `next_send = None` initialisation combined with a deque prefill guard (`if len(q) < _PREFILL`) caused a `TypeError` on first iteration during USB burst, silently killing the writer. The single-thread design eliminates all shared mutable state between reader and writer.

## [3.3.20] - 2026-03-24

### Fixed
- **FM Scanner rewrite — mirror working FM monitor pipeline exactly** — `_push_scanner_audio` now uses the identical audio path as `_run_fm_rtlsdr` + `stream_live`, which is the only FM audio path proven to work correctly:
  - `rtl_fm -s 171000` (no `-r` flag) — raw 171 kHz MPX output, same as the FM monitor
  - **Reader thread**: reads raw 171 kHz S16LE from rtl_fm, resamples with `scipy.signal.resample_poly(x, 16, 57)` (exact 171000→48000 polyphase filter, same call used by `_run_fm_rtlsdr`) → 48 kHz float32 chunks → deque
  - **Writer thread**: drains deque into ffmpeg stdin paced at `next_send += CHUNK_DURATION` (0.5 s per chunk) — identical to the `stream_live` writer that already works correctly for FM listen
  - **ffmpeg**: reads 48 kHz S16LE from the paced writer, encodes to MP3
  - **Main thread**: reads MP3 from ffmpeg, assembles 100 ms chunks, POSTs to hub relay slot
  - Hub relay remains simple pass-through (no hub-side pacing)
- **Root cause of "running fast"** — previous attempts used `rtl_fm -r 48000` (rtl_fm's own resampler, 57:16 non-integer ratio, imprecise) or `ffmpeg -ar 240000 → -ar 48000` (3.3.19). The FM monitor has always used scipy's polyphase resampler for this ratio. The scanner now does the same.

## [3.3.19] - 2026-03-24

### Fixed
- **FM Scanner audio running fast — root cause identified and fixed** — the real cause of the "slightly sped up" audio was not missing pacing but a sample-rate mismatch in `rtl_fm`. The previous command used `-s 171000 -r 48000` (ratio 57:16, a non-integer fraction). `rtl_fm`'s internal resampler is not precise for non-integer ratios and produced PCM at a slightly incorrect rate. Since the MP3 header claimed 48000 Hz, the browser played that slightly-off audio at 48000 Hz, making it sound fast regardless of any relay pacing applied. Fix: changed capture rate to `-s 240000` (exact integer multiple: 240000 = 5 × 48000) with no `-r` flag, and added `-ar 48000` to the ffmpeg command so libswresample (high-quality resampler) performs the 5:1 downsample. All hub-side and client-side pacing code removed.
- **FM Scanner glitching caused by relay pacing** — all `time.sleep()` pacing in `_hub_stream_relay_response` for scanner slots has been removed. The pacing attempts (3.3.15–3.3.18) were addressing a symptom rather than the root cause and introduced their own timing irregularities that caused audio glitches. The relay now passes chunks through as fast as they arrive.
- **Startup burst (USB buffer causing initial speed-up)** — `rtl_fm` accumulates a USB ring-buffer of audio before the first chunk reaches Python. Processing and posting this burst faster than real-time caused the browser to buffer ahead briefly. Fix: the client-side push loop now discards the first 2.5 seconds of ffmpeg output (by wall clock from when data first arrives) to flush the USB startup buffer. After the discard window the pipeline is in steady-state real-time mode. The `slot.get()` timeout on the relay side is increased to 1.0 s to give the client's 2.5 s discard period comfortable margin.

## [3.3.18] - 2026-03-23

### Fixed
- **FM Scanner pacing clock was never firing** — 3.3.17 initialised `_next = time.monotonic()` when the relay generator was created. The client (rtl_fm startup + ffmpeg init) typically takes 1-3 s to produce the first chunk. By then `_next` was 1-3 s in the past, `_slack` was permanently negative, and `time.sleep()` was never called — pacing was completely bypassed. Fix: lazy-init `_next` on the first chunk only (`_next = time.monotonic() + chunk_duration`), yield the first chunk immediately to minimise latency, then pace all subsequent chunks from that baseline.
- **FM Scanner audio dropout ("drops for a while then comes back")** — the relay generator called `slot.get(timeout=2.0)`, so any gap in client POSTs longer than 2 s caused a `queue.Empty` that the browser experienced as silence. 500 ms chunks posted every 500 ms left no tolerance for any POST latency spike. Fixed by reducing the client chunk size from 500 ms to 100 ms (`_bitrate_kbps * 1000 // 8 // 10`), giving 10 POSTs per second. The relay `slot.get()` timeout is reduced to 0.5 s to match.

> **Note**: The FM Scanner (`/hub/scanner`) is at an early stage of development as of this release. The real-time audio pipeline (`rtl_fm → ffmpeg → hub relay → browser`) has had several pacing and buffering fixes applied through 3.3.15–3.3.18. Further stability improvements may be needed. See README for known limitations.

## [3.3.17] - 2026-03-23

### Fixed
- **FM Scanner audio running super fast** — identified root cause as lazy-clock initialisation bug in hub relay pacing (corrected in 3.3.18). Client pipeline reverted to direct OS pipe (`rtl_fm → ffmpeg`) in this version; PCM piper thread from 3.3.16 removed as it worsened the problem. Hub relay pacing added here but initialised incorrectly.

## [3.3.16] - 2026-03-23

### Fixed
- **FM Scanner audio glitching** *(superseded by 3.3.17/3.3.18)* — attempted to pace audio at the PCM input side by interposing a Python thread between `rtl_fm` stdout and `ffmpeg` stdin. Caused bursty output and worsened glitching; approach abandoned.

## [3.3.15] - 2026-03-23

### Fixed
- **FM Scanner audio sped up and eventually dies** *(superseded by 3.3.17/3.3.18)* — attempted to add a `time.sleep()` between `ff_proc.stdout.read()` calls to pace output. This stalled ffmpeg's output pipe, which stalled ffmpeg reading from `rtl_fm`, which caused rtl_fm to drop USB samples. Root cause was on the hub relay side, not the push loop.

---

## [3.3.14] - 2026-03-23

### Changed
- **FM and DAB inputs now require an explicit dongle serial** — the "Any available" option has been removed from both the FM and DAB dongle dropdowns. A specific registered RTL-SDR dongle must be selected before the input can be saved. The form blocks submission with an inline error if no dongle is chosen. On the backend, `_run_fm` and `_run_dab` now hard-fail with a clear log message ("no dongle configured") rather than silently falling back to device index 0, which was the root cause of cross-stream dongle conflicts when multiple dongles are present. Existing inputs saved without a serial will show "FM (no dongle configured)" / "DAB (no dongle configured)" as their status until edited and a dongle is assigned.

---

## [3.3.13] - 2026-03-23

### Changed
- **Reverted 3.3.12 sysfs scan** — the Linux sysfs dongle enumeration and expanded stale-process kill (fuser + multi-tool search) introduced in 3.3.12 have been rolled back. The persistent `usb_claim_interface error -6` reports that motivated those changes were caused by a device serial-to-role misconfiguration in the user's setup, not a code bug. The 3.3.11 `rtl_test`-based `scan()` (with `_scan_lock`, double-check cache, `TimeoutExpired` partial-output handler, and 0.3 s settle) is restored. The stale welle-cli kill (welle-cli only, matching `rtl_sdr,N` driver tag) introduced in 3.3.11 is retained.

---

## [3.3.12] - 2026-03-23

### Fixed
- **SDR scan no longer opens any dongle** — replaced `rtl_test` subprocess enumeration with a direct Linux sysfs read (`/sys/bus/usb/devices/*/idVendor|idProduct|serial|manufacturer|product`). Sysfs only reads USB descriptors already cached by the kernel — no device is opened, no USB interface is claimed, and the result is instant. `rtl_test` is kept as a fallback for non-Linux systems. This eliminates every class of `usb_claim_interface error` that was caused by scan racing with welle-cli or rtl_fm at startup. The `_scan_lock` and lock-inside-lock double-check are retained for the fallback path.
- **Stale process kill now catches everything** — the kill code before each welle-cli launch previously only checked for stale `welle-cli` processes matching the driver tag. Expanded to also check `rtl_fm`, `rtl_test`, and `rtl_eeprom` by both `rtl_sdr,N` driver tag and `-d N` argument. Added `fuser /dev/bus/usb/XXX/YYY` (Linux) to catch any other process holding the raw USB device node regardless of tool name — covers system services, orphaned processes, etc. USB device path is resolved from sysfs busnum/devnum. Kill settle sleep increased to 1.0 s when any process was actually killed.

*Note: rolled back in 3.3.13 — root cause was device misconfiguration, not a code bug.*

---

## [3.3.11] - 2026-03-23

### Fixed
- **DAB still getting `usb_claim_interface error -6` with two dongles** — FM and DAB threads start simultaneously on monitor launch. Both called `scan()` at the same instant with a cold cache, spawning two parallel `rtl_test` processes. When the first process claimed device 0, some `rtl_test` builds fall back to the next available device (device 1) if device 0 is busy — briefly holding it. welle-cli trying to open device 1 at the exact same moment the 2-second timeout killed that second `rtl_test` hit the kernel's USB release window and got `LIBUSB_ERROR_BUSY`. Fix: added `_scan_lock` (a second `threading.Lock`) that serialises all `rtl_test` invocations — only one ever runs at a time. A thread that was waiting for the lock re-checks the cache on entry so it reuses the result rather than running a redundant scan. Added a 0.3 s settle after a timeout kill to ensure device 0 is fully released before any caller opens a dongle.

---

## [3.3.10] - 2026-03-23

### Fixed
- **DAB dongle getting `usb_claim_interface error -6` even on the correct device index** — `rtl_test -t` (the scan tool restored in 3.3.6) enables the Elonics E4000 tuner benchmark, which causes rtl_test to **open every connected RTL-SDR dongle in turn** (open device 0, close, open device 1, close, …) before settling on device 0 for the sample loop. On a 2-dongle setup this briefly claims device 1 (the DAB dongle). If welle-cli started during that window it got `LIBUSB_ERROR_BUSY`, triggering the USB backoff loop and never successfully attaching. The monitor appeared to correctly resolve the serial to device 1 (`-F rtl_sdr,1`), but the device was repeatedly busy. Fix: reverted to plain `rtl_test` (no `-t`). The "Found N device(s):" list is printed to stderr *before* any device is opened (it only enumerates USB descriptors), so the 8-second timeout + `TimeoutExpired` partial-output handler still returns the full device list while no dongle other than device 0 is ever opened.

---

## [3.3.9] - 2026-03-23

### Fixed
- **DAB and FM audio broken — `scan(force=True)` opening the device before welle-cli/rtl_fm could claim it** — `_run_dab` and `_run_fm` both called `sdr_manager.scan(force=True)` at startup to ensure a fresh device index. The fix for the scan bug (3.3.6) restored `rtl_test -t` as the scan tool, which actually *opens* every connected RTL-SDR device to enumerate it. At startup, the target dongle is not yet held by anything — so `rtl_test -t` successfully grabbed it, streamed samples for up to 8 seconds (until killed by the subprocess timeout), then released it. welle-cli/rtl_fm then tried to open the same device immediately after the SIGKILL. The kernel does not guarantee instant USB interface release after SIGKILL, so welle-cli got a device in a partially-released state, causing welle-cli to remove its MP3 sender within seconds of becoming ready. No audio ever reached the monitor. Fix: removed both `scan(force=True)` calls. `resolve_index()` already calls `scan()` naturally when the 10-second cache is empty (always the case on a fresh start), so device indices are still resolved correctly without pre-opening the hardware.

---

## [3.3.8] - 2026-03-23

### Fixed
- **Additional welle-cli log noise suppressed** — added `SyncOnEndNull failed` (OFDM null-symbol timing miss, same category as SyncOnPhase) and `Removing mp3 sender` (welle-cli housekeeping when a service's HTTP audio sender is torn down during signal dropout) to the noise-suppression list in the stderr reader.

---

## [3.3.7] - 2026-03-23

### Fixed
- **welle-cli log spam making DAB look broken** — the welle-cli stderr filter `"failed" in lower` was matching two high-frequency noise messages: `ofdm-processor: SyncOnPhase failed` (fires several times per second on a marginal signal — normal OFDM carrier-phase jitter, doesn't affect audio) and `Failed to send audio for <service_id>` (welle-cli's HTTP push firing when no active client is consuming that service URL). Both were flooding the log, creating the appearance of a broken stream when the mux and audio endpoint were actually healthy. Fix: suppress these two specific patterns before the general error/failed filter so they are silently discarded; all other welle-cli error lines continue to log normally.

---

## [3.3.6] - 2026-03-23

### Fixed
- **SDR scan returned no devices** — a previous fix intended to stop `rtl_test -t` from hanging when dongles were busy accidentally changed `[tool, "-t"]` to `[tool]` in both branches of a conditional, so `rtl_test` was always run with **no arguments**. Unlike `rtl_test -t` (which opens the dongle, quickly determines it is not an E4000, and exits), plain `rtl_test` loops reading samples indefinitely. It always hit the 8-second subprocess timeout, the `TimeoutExpired` exception was swallowed by the bare `except Exception`, and the scan returned an empty device list every time. Fix: restore `rtl_test -t` for the enumeration command. Also added explicit `TimeoutExpired` handling that recovers the partial stderr (the "Found N device(s):" list is always written before any open attempt) so the device list is returned even in the unlikely event of a genuine hang.

---

## [3.3.5] - 2026-03-23

### Fixed
- **FM Scanner audio sounded sped up** — the scanner used `rtl_fm -s 171000` piped into `ffmpeg -ar 171000 -af aresample=48000`. The mismatch between the 171 kHz capture rate and ffmpeg's internal MP3 frame timing caused the browser to receive audio that played back at the wrong speed. Fixed by adding `-r 48000` to the rtl_fm command so it performs its own internal resample and outputs S16LE at 48 kHz — the same rate the normal live stream pipeline uses. The ffmpeg command now matches `stream_live`: `-f s16le -ar 48000 -ac 1 -i pipe:0 -f mp3 -b:a <bitrate>`, with no resampling filter needed.

---

## [3.3.0] - 2026-03-23

### Added
- **RMS / Peak level toggle on hub and client stream cards** — click the "RMS" label on any stream's level bar to switch to instantaneous sample peak dBFS, click again to switch back. Preference is stored in `localStorage` (`ss_level_mode`) and applies to all cards simultaneously. Peak is computed every 0.5 s chunk alongside RMS and included in the AJAX payload (`peak_dbfs`). Label is accent-coloured to show it's interactive.

---

## [3.2.99] - 2026-03-23

### Fixed
- **DAB serial silently dropped on re-save when dongle role is wrong** — same bug as the FM serial fix in 3.2.97. The DAB "Dongle" `<select>` filters by role (`dab`/`none`). If the saved serial's role was set to `fm`, the restore code (`dab_serial.value = serial`) silently failed and the dropdown stayed on "Any available". Re-saving then stripped the serial from `device_index`, causing all DAB streams to default to device index 0 and conflict with the FM dongle. Fixed: same temporary `⚠ not in registry / wrong role` option technique applied to the DAB serial select.

---

## [3.2.98] - 2026-03-23

### Added
- **FM Scanner quality selector** — a Quality dropdown in the scanner setup bar lets you choose the MP3 bitrate before connecting: Low (48k), 64k, Medium (96k), High (128k, default), Very High (192k), Best (256k). Lower bitrates reduce relay bandwidth and typically eliminate glitching on slow or congested links at the cost of audio fidelity. The quality is locked while a session is active (along with the Site and SDR selectors) and takes effect on the next Connect. The selected bitrate is passed through the full relay chain — hub API → `ListenSlot` → client heartbeat ACK → `_push_scanner_audio` ffmpeg `-b:a` argument — so the choice is always honoured end-to-end.

### Improved
- **Scanner relay chunk size now adapts to bitrate** — the ffmpeg stdout read size is calculated as ½ second of audio at the chosen bitrate (e.g. 8000 bytes at 128k, previously hardcoded 4096). Larger, more consistent chunks reduce the number of HTTP round-trips in the relay loop and smooth out buffering gaps that caused the audio to glitch.

---

## [3.2.97] - 2026-03-23

### Fixed
- **FM + DAB dual-dongle conflict (part 2)** — even with 3.2.93 always passing the device index to welle-cli, the underlying race still existed when the two dongles resolved to the same device index (e.g. DAB configured with "Any available" defaulting to index 0, and the FM dongle also at index 0). Root cause: `SdrDeviceManager` had no cross-type visibility — DAB sessions registered nothing in `_owners`, so `claim()` couldn't detect them, and `_run_dab` had no visibility into active FM claims.

  Fix: added `_dab_owners: Dict[int, str]` to `SdrDeviceManager` (keyed by device index) alongside the existing serial-keyed `_owners` map.
  - `_start_dab_session` now calls `sdr_manager.claim_dab_device(idx, owner)` before launching welle-cli; raises `SdrBusyError` if an FM stream already holds that index.
  - `_stop_dab_session` calls `sdr_manager.release_dab_device(idx)` after stopping welle-cli.
  - `claim()` (used by FM/scanner) now checks `_dab_owners` before granting a lease; raises `SdrBusyError` with an actionable message if DAB already holds that device.
  - `_run_dab` performs an early FM-conflict check before the retry loop, setting `_livewire_mode = "DAB (device conflict — see logs)"` and logging which FM stream holds the conflicting device index. Also logs a warning when no serial is configured (defaulting to index 0) so multi-dongle users know to assign serials.
  - Added raw `device_index=` log line at startup of both `_run_fm` and `_run_dab` so the exact URL (including serial param) is always visible in logs for diagnostics.

- **FM stream serial silently dropped on re-save when dongle role is wrong** — the FM "Dongle" dropdown filters by role (`fm`/`none`). If a stream was saved with a serial whose dongle later had its role changed to `dab`, the restore code (`fm_serial.value = serial`) would silently fail (no matching `<option>`), leaving the dropdown on "Any available". Re-saving then stripped the serial from `device_index`, causing the next start to default to device 0. Fixed: if the restored serial isn't present in the dropdown, a temporary `⚠ not in registry / wrong role` option is inserted so the value is preserved. The user sees the warning label and can fix the role or pick the correct dongle.

---

## [3.2.95] - 2026-03-23

### Fixed
- **FM Scanner: CSRF validation failed on Connect/Tune/Stop** — scanner JS used raw `fetch()` without the `X-CSRFToken` header, and the page was missing the `<meta name="csrf-token">` tag. Fixed by adding the meta tag and a local `_f()` helper (identical to the one used by all other hub pages) that automatically injects `X-CSRFToken` into every request.
- **FM Scanner: all sites shown as offline** — the scanner route read `sdata.get("online")` directly from the raw `_sites` dict, but `online` is not stored there — it is computed dynamically from `_received` timestamp vs `HUB_SITE_TIMEOUT`. Fixed by computing `online = (now - sdata.get("_received", 0)) < HUB_SITE_TIMEOUT` in the route, matching the same logic used by the hub main page.

---

## [3.2.94] - 2026-03-23

### Fixed
- **FM Scanner page returns 500** — two bugs introduced with the scanner template: (1) the route passed `csp_nonce=g.csp_nonce` but `g` has no `csp_nonce` attribute — the nonce is stored at `g._csp_nonce` and exposed as a Jinja2 context-processor function `csp_nonce()`, so no manual passing is needed at all; (2) the template used `{{csp_nonce}}` (variable) instead of `{{csp_nonce()}}` (function call). Fixed by removing the explicit kwarg from `render_template_string` and correcting both `<style>` and `<script>` nonce attributes to use `{{csp_nonce()}}`.

---

## [3.2.93] - 2026-03-23

### Fixed
- **FM stream fails to start when used alongside a DAB stream on the same machine** — `_start_dab_session` built the welle-cli `-F` driver string with the condition `if session.device_idx and str(session.device_idx) != "0"`, which silently skipped the device index whenever the resolved index was `0`. This caused welle-cli to receive `-F rtl_sdr` (no index = "grab first available") instead of `-F rtl_sdr,0`. With two dongles plugged in, welle-cli would race `rtl_fm` for device 0 — whichever process started second would get `usb_claim_interface error` and fail. Fixed by always passing the explicit index: `-F rtl_sdr,{idx}`. With a single dongle, `rtl_sdr,0` behaves identically to `rtl_sdr`; with multiple dongles it pins welle-cli to the correct device and leaves the other index free for rtl_fm.

---

## [3.2.92] - 2026-03-23

### Added
- **Scanner Mode** — new `📻 FM Scanner` page accessible from the hub dashboard. Pick any connected site and its SDR dongle, enter a starting frequency, and click Connect. The hub creates a relay slot, the client starts `rtl_fm` at the requested frequency and pipes audio through `ffmpeg` (resampled to 48 kHz MP3 128 kbps), and the hub streams it live to the browser's audio player. Controls: **−1.0 / −step / +step / +1.0 MHz** buttons, configurable step size (0.05 / 0.1 / 0.2 / 0.5 / 1.0 MHz), direct frequency entry, and keyboard shortcuts (← → tune, ↑ ↓ ±1 MHz, Shift×5). Each tune creates a fresh relay slot so audio reconnects seamlessly. SDR serial numbers are auto-populated from the site's configured FM streams. Uses the existing signed `listen_registry` relay infrastructure — all encryption, signing and slot cleanup is inherited automatically.

---

## [3.2.91] - 2026-03-23

### Added
- **Auto-maintenance on hub-pushed update** — when the hub pushes a `self_update` command to a client site, every chain node belonging to that site is automatically placed into maintenance mode for up to 15 minutes. This suppresses false `CHAIN_FAULT` alerts during the update download, syntax validation, process restart, and audio-level settle time. The hub watches for the client's first heartbeat after the restart, immediately queues a `start` command so monitoring resumes without manual intervention, then starts a **60-second cooldown timer**. When the timer expires, maintenance mode is cleared on all of that site's chain nodes automatically. If the update fails (client never comes back), maintenance expires naturally after 15 minutes — no permanent suppression.

---

## [3.2.90] - 2026-03-23

### Fixed
- **Chain fault clips for local streams always 5 seconds regardless of configured clip length** — `_save_alert_wav` has a hardcoded default of `duration=5.0`. The local-stream chain clip save at fault time was called without a `duration` argument, so it always captured exactly 5 seconds no matter what "Alert clip length" was set to in the stream's settings. Remote node clips were already correct (`_cmd_save_clip` has always passed `inp.alert_wav_duration`). Fixed by passing `_lc.alert_wav_duration` to the local chain clip save, making both paths consistent.

---

## [3.2.89] - 2026-03-23

### Fixed
- **Maintenance popover never appeared when clicking 🔧 button** — `_openMaintPop()` was setting `style.display=''` to "show" the popover, which clears the inline style and falls back to the CSS rule `#maint-popover { display: none }`, keeping it permanently hidden. Changed to `style.display='block'` so the popover actually appears.

---

## [3.2.88] - 2026-03-23

### Added
- **Per-node silence threshold override in chain builder** — each stream row in the chain builder now has a "Silence dBFS override" numeric field. When set, this value replaces the stream's own configured silence threshold when evaluating that specific node within that chain. This allows the same physical stream (e.g. LONCTAXMQ05) to be treated as silent at −28 dBFS in a Downtown chain (where −28 dBFS is noise floor) while still being considered active at the same level in a separate Cool FM chain (where −28 dBFS represents real audio). The override is saved into the chain definition JSON and applied exclusively in `_eval_one_node`; the stream's original threshold is unchanged everywhere else. Works for both remote (hub) streams and local inputs.

---

## [3.2.87] - 2026-03-23

### Fixed
- **Reports page: remote clip audio players always show 404 / won't play** — The alert log stores remote clips with `stream = "site / stream"` (e.g., `"london / CoolFM - LONCTAXZC03"`). `clips_serve` applied `_safe_name()` to the full combined string, which strips spaces and `/` to produce `londonCoolFM-LONCTAXZC03`. But `hub_clip_upload` stored the file at `alert_snippets/{_safe_name(site)}_{_safe_name(stream)}/` = `london_CoolFM-LONCTAXZC03/` (note the underscore separator). These never matched → 404 → silent audio element failure. Fixed: `clips_serve` now splits on ` / ` before safe-naming, producing the same underscore-joined key. Also added `Accept-Ranges: bytes` and `Content-Length` headers so Chrome and Safari can play WAV files inline in `<audio>` elements.
- **Chain fault: only 1 clip captured instead of all chain nodes** — `pop_pending_command` delivered one `save_clip` command per 5-second heartbeat. For a 7-node chain this meant all clips arrived over 35 seconds. In practice users saw "No clips" or just 1 clip depending on when they looked. Root cause: the heartbeat ACK was designed for single commands. Fixed by replacing `pop_pending_command` with `pop_all_pending_commands` which atomically drains the entire command queue in one call. The ACK now sends `"commands": [...]` (full list) alongside the legacy `"command": <first>` for older client builds. The client dispatch loop now processes the full list in a single heartbeat cycle — all `save_clip` commands for a fault arrive and execute immediately on the next heartbeat.

---

## [3.2.86] - 2026-03-23

### Fixed
- **Chain node levels out of sync between stacks / second chain appearing stuck** — `eval_chain()` is called sequentially per chain in the `/api/chains/status` endpoint; each call takes its own `sites_snap` under the lock, but by the time the second chain evaluates, the first chain's SQLite SLA query has already consumed some milliseconds, meaning sub-nodes in the second chain's stacks could reflect slightly different points in time from those in the first chain. Over a 5-second poll interval this compounded into visible staleness.

### Added
- **Real-time chain node levels (2-second refresh)** — new lightweight `GET /api/chains/levels` endpoint takes a single atomic snapshot of `_sites` (one lock acquisition, no SQLite, no chain evaluation) and returns `{ site → { stream → { level, silence } } }` for every known stream including local inputs. The chains page now runs a separate 2-second `_refreshLevels()` poll that updates `.node-level` text on every chain-node using the `data-site` / `data-stream` attributes. Because all nodes read from the same snapshot, stacks and chains are always consistent with each other. The existing 5-second `/api/chains/status` poll continues to own border colours, fault detection, badge state, and trend arrows. Level refresh is automatically skipped during history time-travel mode.

---

## [3.2.85] - 2026-03-23

### Added
- **Maintenance mode UI for chain nodes** — each node in the live chain diagram now shows a 🔧 button (visible on hover; stays lit while in maintenance). Clicking it opens a popover with duration presets — **30 min / 1 h / 2 h / 4 h** — and a **✕ Clear** option. The selection POSTs to the existing `POST /api/chains/<cid>/maintenance` endpoint; the node turns blue immediately and shows a "🔧 Maint until HH:MM" badge, suppressing fault alerts for the chosen window. Works on both single-stream nodes and nodes inside stacks. Implemented using `data-chain-id` / `data-site` / `data-stream` attributes and a delegated `click` listener to comply with the CSP no-inline-handlers rule.

---

## [3.2.84] - 2026-03-23

### Fixed
- **Remote node audio clips lost across hub restarts** — `push_pending_command` stored `save_clip` commands (and all other hub→client commands) only in memory, inside `_sites[site]["_pending_commands"]`. A `hub_state.json` snapshot was only written when the *next client heartbeat arrived*. If the hub restarted between a chain fault firing and the next heartbeat (e.g. during a planned restart after a late-night incident), all queued `save_clip` commands were lost. Clients never received them, never captured audio, and the fault log entry permanently showed "No clips". Fixed: `push_pending_command` now takes a copy of `_sites` inside the lock and triggers an async `_save_snapshot` save immediately after appending each command — the same pattern used by `approve_site` and `ingest`. Clips that had already been uploaded before the restart were unaffected (WAV files and DB records both persist); only clips that were queued but not yet delivered were lost.

---

## [3.2.83] - 2026-03-23

### Added
- **Named stacks in Broadcast Chains builder** — each stack position in the chain builder now has an optional **Stack label** field (e.g. `Primary Sources`, `STL Feeds`, `TX Monitors`). The input appears automatically when a position has two or more streams. Labels are saved with the chain and used throughout the UI: fault messages, the live fault status line, the chain diagram, and the fault history log now all show the label instead of the generic `Stack → Stack` text. Restoring a saved chain restores the label into the builder.

---

## [3.2.82] - 2026-03-23

### Fixed
- **Broadcast Chains fault replay "Play All" showed "Done ✓" immediately** — clicking **▶ Play All** on the Replay timeline closed immediately without playing any audio. Root cause: audio clip data was stored in a `data-clips` HTML attribute as JSON via `_esc()`, which encodes `&`, `<`, and `>` but does NOT encode double-quotes (`"`). JSON strings containing `"` (all stream names, labels, etc.) broke the attribute value at the first quote, so `btn.dataset.clips` returned a truncated fragment, `JSON.parse` threw, the clips array fell back to `[]`, no `<audio>` elements were created, and `playNext()` immediately showed "Done ✓". Fixed by storing clip data in a JS-side map (`window._flogClipStore[fid]`) keyed by fault ID at render time; the Replay button carries only `data-fid` and the click handler reads from the map. The audio playback path in Hub Reports was unaffected.

---

## [3.2.81] - 2026-03-23

### Added
- **Mobile API: metric history endpoint** — new `GET /api/mobile/metrics/history` endpoint returns time-series data for any stream metric. Query parameters: `stream` (required), `metric` (default `level_dbfs`), `hours` (1/6/24, default 6), `site` (hub mode, optional). Returns `{ ok, stream, site, metric, hours, points: [{ts, value}, …] }`. Backed by `metrics_history.db`; the same 90-day retention as the web app signal history charts. Used by the iOS app signal history view.

### iOS App
- **Signal history charts** — tapping any stream row in the Hub Overview now navigates to a full-screen signal history view. Select a time range (1 h / 6 h / 24 h) and a metric (Level dBFS, LUFS Momentary, LUFS Integrated, RTP Loss %, RTP Jitter ms, FM Signal dBm, FM SNR dB, DAB SNR) from a picker. A Swift Charts line graph renders the selected metric with catmull-rom interpolation; axis labels match the selected range. Min / Avg / Max stats and point count shown below the chart.
- **RDS/DAB station name and now-playing in hub stream rows** — hub stream rows in the Sites list now show the RDS PS name or DAB service name in brand-blue below the stream name, and the now-playing / DLS text in muted grey when available. A chevron hint on the stream name row indicates the row is tappable.
- **Reports pagination (Load More)** — the Reports page now fetches up to 100 events per page. A **Load more events** button appears at the bottom of the unfiltered list when more events are available. Tapping uses cursor-based pagination (the timestamp of the last loaded event as the `before=` cursor) and appends new events without replacing the existing list.

---

## [3.2.80] - 2026-03-23

### Added
- **Chain health score: RTP packet loss component** — a fifth component is now included in the chain health score. The peak RTP packet loss across all RTP-capable nodes in the chain (sub-nodes inside stacks included) contributes a penalty of 0–10 pts: 0 pts at 0% loss, scaling linearly to −10 pts at ≥ 10% loss. FM, DAB, HTTP, and local sound device nodes report no RTP loss and are excluded. The health score tooltip now shows the RTP loss value when it is non-zero.

  Updated scoring summary:

  | Component | Range |
  |---|---|
  | 30-day SLA | 0–70 pts |
  | Fault frequency (7 d) | 0–20 pts |
  | Stability (flapping) | 0–10 pts |
  | Trending-down node penalty | −5 per node, max −15 |
  | **RTP packet loss penalty** | **0 to −10 pts** |

---

## [3.2.79] - 2026-03-23

### Fixed
- **Chain health score and SLA degrading due to long ad breaks** — when a "fault-if-ALL-silent" confirmation window timed out during a genuinely long ad break, `CHAIN_FAULT` fired correctly (audio was silent longer than the configured delay) but the event was counted against both the health score fault-frequency component (−4 pts per occurrence) and the SLA downtime counter. Faults that originate from an adbreak-candidate window timing out are now tagged as `adbreak_overshoot` and:
  - **Excluded from the 7-day fault-frequency count** — no −4 pt penalty per event; repeated long ad breaks no longer collapse the health score.
  - **Excluded from SLA downtime** — `chain_status = 1.0` (ok) is written to metric history while the chain is in this adbreak-confirmed state, so the 30-day SLA does not accumulate downtime from ad break periods.

  Genuine faults (real signal loss, post mix-in node failures, mix-in point itself going silent) are unaffected. The `CHAIN_FAULT` notification still fires so operators are aware of unusually long breaks.

---

## [3.2.78] - 2026-03-23

### Fixed
- **Mobile API settings Save button missing** — the APNs / Mobile API settings panel had no Save button, so changes to APNs Key ID, Team ID, Bundle ID, `.p8` private key, and the sandbox toggle were never persisted. Save button added to the panel footer, matching the style used by all other settings panels.

---

## [3.2.77] - 2026-03-23

### Added
- **Broadcast Chains click-to-listen mini-player** — clicking a live-enabled node on the Broadcast Chains page now opens a sticky mini-player bar fixed at the bottom of the viewport (stream name, site · chain name label, native audio controls, ⏹ Stop & close button). The active node displays a pulsing blue ring while audio is playing. Clicking the same node again or pressing Stop & close stops playback and hides the bar. Consistent with the mini-player introduced on the Hub and Hub Reports pages.

---

## [3.2.76] - 2026-03-23

### Fixed
- **Chain false faults and incorrect badge during ad breaks (no mixin node)** — two compounding bugs affected chains using a "fault if ALL silent" stack without a configured mix-in node:
  1. **Warmup backdating** — on service restart, the fault confirmation window was immediately backdated (`since = now − min_fault_secs`), so CHAIN_FAULT fired on the very first evaluation during an ongoing ad break. The "fault-if-ALL-silent" stack with healthy downstream nodes is now treated as `adbreak_candidate`, giving it a fresh window instead.
  2. **Badge showed "CHECKING…" instead of "AD BREAK"** — same root cause: `adbreak_candidate = False` meant the display status was `"pending"` not `"adbreak"`, so the informative countdown badge never appeared. Both paths (live eval and historical reconstruction) are fixed.

---

## [3.2.75] - 2026-03-23

### Added
- **Hub page Live button mini-player** — replaced the inline `<audio>` element that was appended next to each ▶ Live button (causing layout overflow) with the same sticky mini-player bar used on Hub Reports. Clicking ▶ Live opens a fixed bottom bar showing stream name, site, a pulsing 🔴 LIVE badge, native audio controls, and an ⏹ Stop & close button. Tapping the same Live button again also stops playback. Switching to a second stream stops the first automatically.

---

## [3.2.74] - 2026-03-23

### Fixed
- **AI-triggered clips saved at global duration (5 s) instead of per-input configured duration** — all six `analyse_chunk` call sites were passing `self.app_cfg.alert_wav_duration` (the global AppConfig default, 5.0 s) rather than `cfg.alert_wav_duration` (the per-input InputConfig value, user-configurable). Silence clips were unaffected as they used the per-input value directly. All call sites corrected; the local cached variable in the sound-device handler that also shadowed the per-input value has been removed.

---

## [3.2.73] - 2026-03-23

### Added
- **Hub Reports mini-player** — replaced the wide in-table audio player with a compact ▶ Play button. Clicking opens a sticky mini-player bar fixed at the bottom of the page (stream name, timestamp, native audio controls, ⬇ Download, ✕ close). The Clips table column shrinks from 220 px to 90 px, eliminating horizontal overflow on narrower screens.

---

## [3.2.72] - 2026-03-23

### Fixed
- **Remote node clips silently dropped on hub restart** — comprehensive logging added throughout the clip upload path (`push_pending_command`, `_cmd_save_clip`, `_upload_clip`, `hub_clip_upload`) so all failures are visible in the hub log. Root cause: a duplicate `_append_fault_log_entry` method (defined twice in the class) meant Python used only the last definition, which lacked the stack-aware label/site/stream logic from `_create_fault_log_entry`. The duplicate has been removed; the single remaining method delegates correctly. Also: improved fallback in `hub_clip_upload` when `flog` is empty after a hub restart.

---

## [3.2.71] - 2026-03-22

### Fixed
- **Chain fault history "No clips" for remote node clips** — three bugs combined to prevent audio clips uploaded by remote client sites from appearing in the chain fault history panel:
  1. `HubServer._load_fault_log_from_db()` is called from `__init__` before `monitor` (MonitorManager) is initialised, causing a silent `NameError` that left `_chain_fault_log` empty after every restart. A second deferred call is now made immediately after `monitor` is created so the in-memory fault log is always fully restored from SQLite.
  2. SQLite databases created before the `clips` column was added to `chain_fault_log` would silently fail all clip insert/update/read operations. `MetricsDB._init_db()` now runs an `ALTER TABLE … ADD COLUMN clips TEXT` migration on startup; the `OperationalError` raised when the column already exists is caught and ignored.
  3. If the hub restarted between a fault firing and a remote clip arriving, `_chain_fault_log` was empty even for the faulted chain, so the clip back-patch was silently skipped. The back-patch code now falls back to a direct DB read/update in this case so clips are always recorded regardless of in-memory state.
- **Clip back-patch fallback when entry_id not matched** — if an older client site sends a clip upload without a matching `entry_id` (e.g. the hub's ring-buffer evicted the entry), the code now logs a warning and falls back to the most recent fault log entry rather than silently dropping the clip.
- Added detailed server-log messages for all clip back-patch paths (success, fallback, skip) to aid future debugging.

---

## [3.2.67] - 2026-03-22

### Added
- **Chain Health Score** — every chain card now shows a live composite health score (0–100) alongside the SLA badge. The score combines four weighted components: 30-day SLA (0–70 pts, primary driver), fault frequency over the last 7 days (0–20 pts, −4 per fault), stability (0–10 pts, zeroed out while flapping), and a penalty for any chain nodes with a falling level trend (−5 per trending-down node, max −15). Colour-coded and labelled: **Healthy** (≥ 90, green) · **Watch** (75–89, amber) · **Degraded** (50–74, orange) · **Poor** (< 50, red). Hovering the badge shows a tooltip explaining each component. New chains with insufficient SLA data start around 65 and improve as history accumulates.
- **Fault Replay Timeline** — chain fault log entries that include audio clips now show a **🎬 Replay** button (replacing the old stacked chip list). Clicking expands an inline panel with:
  - A visual timeline of all captured node clips laid out left-to-right in signal-path order, coloured by status (fault = red, last good = green, others = muted), matching the chain diagram layout. Click any node to scroll to its audio player.
  - **▶ Play All** — plays all clips sequentially in chain order. The active node is highlighted in the timeline and the active player row is highlighted as playback moves through the chain.
  - Per-clip audio players with individual download (⬇) links. The panel can be collapsed by clicking the Replay button again.
- **Clip endpoint now serves inline by default** — `/api/chains/clip/<key>/<fname>` no longer sends `Content-Disposition: attachment`, allowing `<audio>` elements to stream directly in the browser. Append `?dl=1` to force a file download (the ⬇ links in the replay panel use this).

---

## [3.2.66] - 2026-03-22

### Fixed
- **Ad break countdown frozen until monitor loop tick** — when both stacked inputs went silent, the API detected the fault live via `eval_chain()` but `adbreak_remaining` was stuck at the full configured window (e.g. "90s") until the 10-second chain monitor loop ran and set the `"pending"` state. The UI displayed a frozen amber badge that appeared not to be counting down. Fixed by tracking a `_chain_api_pre_pending_since` timestamp on `HubServer` the moment the API first sees a fault; the countdown now starts immediately from that onset regardless of the monitor loop phase. The pre-pending timestamp is cleared automatically when the monitor loop takes over or the fault resolves.
- **Chain widget on hub overview page refreshing every 15 s** — the mini chain diagram on the hub overview page (`/hub`) was polling `/api/chains/status` every 15 seconds while everything else on the page refreshed every 5 seconds, causing the chain status boxes to appear noticeably stale. Reduced to 5 s to match the hub page refresh cadence.

---

## [3.2.65] - 2026-03-22

### Added
- **iOS Sites tab** — new **Sites** tab (tab 0) in the iOS app shows a live hub overview: a summary bar with counts for Online, Offline, Alerts, Warnings, and Streams; expandable site cards showing each site's status, last-seen time, and latency; per-site stream rows with level bar, format badge, SLA%, and AI status badge. Pull-to-refresh and loading/error/empty states. Backed by the new `/api/mobile/hub/overview` endpoint which aggregates hub `_sites` into a structured summary with per-site, per-stream data. Tab ordering updated to Sites → Faults → Chains → Reports → Settings.
- **Chain fault audio clips in iOS fault log** — the fault history panel on each chain's detail view now shows all audio clips captured at fault time. Each clip is listed with its node label, position status (fault/last good/ok), and an inline AVPlayer with play/pause controls. Token-authenticated playback via the new `/api/mobile/clip/<key>/<fname>` endpoint.

### Fixed
- **Web fault log Clips column always hidden** — the column header was gated on `hasClips`, a flag that was `false` for existing fault entries that pre-dated clip capture. The `hasClips` gate is removed; the column is always shown and individual rows display "No clips" when a fault entry has none.

---

## [3.2.62] - 2026-03-22

### Added
- **All-nodes audio clip capture on chain fault** — when a chain fault fires, SignalScope now saves audio clips from **every node** in the chain (not just the fault point and last-good node). Each clip is tagged with the node label, chain position index, and a status label (`fault`, `last_good`, or `posN`). Remote node clips are requested via the existing `save_clip` hub command and back-patched into the fault log entry by UUID once received — eliminating the previous race condition where clips were matched against `_flog[-1]` which could be stale if a second fault fired during upload.

### Fixed
- **Remote silence threshold hardcoded at −55 dBFS for chain evaluation** — `_eval_one_node()` used a hardcoded −55.0 dBFS silence floor when evaluating remote (hub client) streams, regardless of how the stream's silence threshold was configured on the client. The client's `silence_threshold_dbfs` is now sent in every heartbeat payload, and `_eval_one_node()` uses it with a −55.0 fallback for older clients that do not include it. Previously, streams with a silence threshold configured above or below −55 dBFS (e.g. −45 dBFS for a low-level feed) would be evaluated incorrectly by the chain engine.
- **`CHAIN_RECOVERED` alert fired with type `CHAIN_FAULT`** — the recovery notification was sending `alert_type="CHAIN_FAULT"` and writing `"type": "CHAIN_FAULT"` to the alert log, making recovery events indistinguishable from fault events in Reports and on the hub. Both the alert log entry and the `AlertSender.send()` call now correctly use `"CHAIN_RECOVERED"`. `CHAIN_RECOVERED` has also been added to `_ALL_ALERT_TYPES` and `_HUB_DEFAULT_FORWARD_TYPES` so it participates in hub forwarding and filtering.

---

## [3.2.61] - 2026-03-22

### Changed
- **AI retraining now genuinely builds on the original 24 h corpus** — previous behaviour was a full reset from random weights using only the recent rolling clean buffer (~8,000 windows), meaning feedback-triggered retrains discarded everything the model learned during the initial training phase. The 24 h training corpus is now saved to `ai_models/<stream>_initial.npy` once after the initial learning phase completes (written in a background thread, never overwritten). Every subsequent feedback-triggered retrain loads this file and combines it with the clean buffer (rolling live windows + labeled false-alarm clip features) before calling the Adam optimiser. Baseline reconstruction-error stats are also recomputed from the full combined dataset so z-score thresholds remain correctly calibrated. The threshold bias resets to 0 after retraining because the corrected knowledge is now baked into the model weights rather than applied as a post-hoc offset.

---

## [3.2.60] - 2026-03-22

### Added
- **AI feedback from Hub Reports** — the Hub Reports page now shows 👍 (false alarm) / 👎 (confirmed fault) buttons on every `AI_ALERT` and `AI_WARN` row, matching the buttons already present on the client's own reports page. Clicking a button from the hub:
  1. Stores the label immediately in the hub's `alert_feedback.json` so the button state persists on reload
  2. Queues an `ai_feedback` command for the relevant client — delivered on the next heartbeat (≈ 5 s)
  3. The client's `_cmd_ai_feedback` handler calls `_apply_feedback_label()`, which injects the clip features into the clean buffer and triggers a model retrain when the threshold is reached — identical to clicking the button locally
  - If the client is offline when the label is saved, the command remains queued in `hub_state.json` and is delivered on reconnect
  - The site badge briefly shows a ✓ tick to confirm the command was accepted
- **New hub endpoint** `POST /hub/api/alerts/<alert_id>/feedback` (login + CSRF required) — hub-side counterpart to the existing client `POST /api/alerts/<id>/feedback`
- **Hub feedback shown in reports** — `hub_reports()` now merges hub-stored feedback labels into events before rendering, so 👍/👎 states are reflected on page load

---

## [3.2.57] - 2026-03-22

### Fixed
- **Password change has no server-side confirmation check** — the Security settings form now validates `auth_password_confirm` server-side before hashing and storing. If the two fields do not match, a flash message is shown and the password is not updated. The existing client-side `chkPwMatch()` JS guard remains as the first line of defence; the server check is a belt-and-braces fallback.

---

## [3.2.56] - 2026-03-22

### Added
- **Hub auto-downloads alert clips from clients** — instead of the previous on-demand streaming proxy (which was unreliable when the client was under load or briefly offline), the hub now proactively downloads every alert clip as it is created on the client. A new `_hub_clip_queue` on `MonitorManager` is populated by `_save_alert_wav()` whenever a clip is saved and the node is running as a hub client. The queue is drained after each successful heartbeat, uploading clips to the hub via `POST /hub/clip_upload`. The hub stores clips in `alert_snippets/<site>_<stream>/` using a stable filename derived from the alert timestamp (idempotent — retries never overwrite). `hub_proxy_alert_clip()` checks local storage first and falls back to the live proxy only for older clips from before this release. Chain fault clips (saved via `_cmd_save_clip`) continue to upload directly and skip the queue to avoid double-upload.
- **Setup wizard improvements**:
  - Step 1 (Dependencies) now shows an info box for users who installed via the install script: *"Used the install script? All core dependencies were installed automatically — you can proceed straight to Next."*
  - The final wizard step button is now labelled **Set Password & Finish →** and redirects to `/settings#sec` (Security tab) so users land directly on the password field after completing the wizard
- **Confirm password field in Security settings** — the Security tab now shows a second *Confirm Password* input alongside the new-password field. A live `chkPwMatch()` function shows a green ✓ / red ✗ indicator as the user types, and the form's submit handler blocks submission if the fields do not match

---

## [3.2.55] - 2026-03-22

### Fixed
- **Stale audio levels persist on hub after client stops monitoring** — `stop_monitoring()` now resets `_last_level_dbfs = -120.0`, `_silence_secs = 0.0`, `_dab_ok = False`, and `_rtp_loss_pct = 0.0` for all inputs immediately after stopping the monitor loop. The hub heartbeat continues running independently of the monitor loop and was reporting stale healthy levels, preventing the hub from marking the streams as silent/down.

---

## [3.2.54] - 2026-03-22

### Fixed
- **Post-mixin node fault during ad break countdown did not alert immediately** — two bugs combined to suppress the bypass:
  1. `fault_idx` always points to the *first* faulting node. When a pre-mixin node is already silent (triggering the ad break countdown) and a post-mixin node then also faults, `fault_idx` still points to the pre-mixin node so `fault_is_post_mixin` was `False`. Fixed by scanning all nodes from `mixin_idx` onwards and setting a new `any_post_mixin_fault` flag when any node at or after the mix-in point is down.
  2. The `effective_post_mixin` calculation included `and not pending_adbreak`, which blocked the bypass in the very state where it is most needed. Fixed by removing the `not pending_adbreak` guard from both the `ok → alerted` and `pending → alerted` transition paths. Added a dedicated log reason: *"post mix-in node faulted during ad break window"*.

---

## [3.2.53] - 2026-03-22

### Fixed
- **APNs JWT cache not invalidated when credentials change in Settings** — `_get_apns_jwt()` was keyed only on token age (55-minute TTL). Saving new APNs credentials (key ID, team ID, or PEM) in Settings left the old JWT in the cache for up to 55 minutes, causing `InvalidProviderToken` rejections until the TTL naturally expired or the server was restarted. Fixed by adding a `cache_key = f"{key_id}:{team_id}"` comparison — any credential change forces immediate JWT regeneration. The settings save handler also explicitly clears the cache so the new credentials are used on the very next push attempt.

---

## [3.2.42] - 2026-03-22

### Fixed
- **Confirmation delay incorrectly applied to post-mixin faults**: `min_fault_seconds` is intended to absorb ad-break silence, which can only occur at nodes *before* the mix-in point (where ads are inserted). Faults at or after the mixin point (e.g. silence on the processed output or transmitter feed) are always real faults — ads cannot cause silence there. The confirmation window now bypasses immediately when `fault_index >= mixin_node_idx`, logging `"fault is post mix-in (node N), bypassing Xs confirmation window"`. This applies to both the initial `ok → pending` transition and to faults that shift position while already in the pending state. The warmup seeding path is also corrected so a post-mixin fault at startup is seeded as `alerted` rather than `pending`.

---

## [3.2.41] - 2026-03-22

### Added
- **APNs push notifications**: chain faults now trigger real Apple Push Notification Service pushes to all registered iPhone app instances — no app polling required. Notification taps deep-link directly into the faulted chain's detail view.
  - Server: new `MobileApiConfig` fields (`apns_key_id`, `apns_team_id`, `apns_bundle_id`, `apns_key_pem`, `apns_sandbox`) for APNs credentials. JWT is generated using ES256 with the `.p8` private key (using the `cryptography` library already in the stack), cached for 55 minutes, and pushed over HTTP/2 via `httpx[http2]`. Expired/unregistered tokens (410 response) are automatically removed. Push is sent on a background thread so it never blocks the alert state machine.
  - Server: new `POST /api/mobile/device_token` endpoint (mobile-token auth) — iOS app registers/unregisters its APNs hex token here. Tokens are persisted in `lwai_config.json`.
  - iOS: `AppDelegate` handles `didRegisterForRemoteNotificationsWithDeviceToken` and posts the hex token to `NotificationCenter`. `AppModel` uploads it to the server on first receive and whenever the server URL changes. Token is stored in `UserDefaults` to avoid redundant uploads.
  - iOS: `NotificationManager` now calls `UIApplication.shared.registerForRemoteNotifications()` after the user grants permission. `userNotificationCenter(_:didReceive:)` handles tap events — extracts `chain_id` from the notification payload and deep-links the app to that chain.
  - **Installer**: `httpx[http2]` added to `install_signalscope.sh`. Existing installs: `pip install 'httpx[http2]'` in the venv. Without it, APNs is a silent no-op with a log warning.
  - **Xcode setup required**: enable the **Push Notifications** capability in the target's Signing & Capabilities tab. Also add **Background Modes → Remote notifications** if you want delivery while the app is backgrounded.

- **Live fault view (iOS)**: `ChainDetailView` now auto-refreshes every **5 seconds** when the chain is in confirmed FAULT state. A red `LIVE` badge appears in the navigation bar. On recovery (or any non-fault status), the refresh rate drops back to 30 s to match the server evaluation cycle. Uses `task(id:)` so the loop restarts immediately when the interval changes — no polling drift.

- **Notification tap → deep link (iOS)**: tapping an APNs fault notification switches to the Faults tab and pushes `ChainDetailView` for the specific faulted chain. Uses `NavigationPath` in `FaultsView` for programmatic navigation.

### Fixed
- **Ad-break/pending chains showing as active faults (iOS)**: `AppModel.displayedFaults` was using the raw `activeFaults` list (from `/api/mobile/active_faults`) without filtering by `displayStatus`. Chains still in the confirmation window (`pending`/`adbreak`) were appearing in the Faults tab, triggering the fault banner, and incrementing the tab badge — even though they hadn't been confirmed as faults yet. Fixed by adding `.filter { $0.displayStatus == .fault }` to both code paths so only chains in confirmed FAULT state appear.

---

## [3.2.34] - 2026-03-22

### Added
- **Mobile API for iPhone app / widgets / Live Activity**:
  - `GET /api/mobile/chains` — token-protected snapshot of all broadcast chains using `results` as the list key
  - `GET /api/mobile/chains/<cid>` — token-protected single-chain detail using `chain` as the object key
  - `GET /api/mobile/active_faults` — token-protected active-fault view with the same chain summary shape used by the iPhone app fault list
  - Mobile chain payload now includes the UI-facing fields required by the app: `display_status`, `fault_reason`, `fault_at`, `pending`, `adbreak`, `adbreak_remaining`, `maintenance`, `maintenance_nodes`, `flapping`, `shared_fault_chains`, `sla_pct`, `updated_at`, `age_secs`, and nested `nodes`
  - Node payload supports both regular nodes and stack nodes with nested `nodes`, including `type`, `label`, `stream`, `site`, `status`, `reason`, `machine`, `live_url`, `level_dbfs`, `ts`, and stack `mode`

- **Mobile token management endpoints** (web-session protected, for provisioning the app):
  - `GET /api/mobile/token/status` — returns whether mobile access is enabled plus the full token and masked token
  - `POST /api/mobile/token/rotate` — rotates the mobile token
  - `POST /api/mobile/token/disable` — disables mobile token access

- **Mobile-token protected audio relay endpoints**:
  - `GET /api/mobile/stream/<idx>/live`
  - `GET /api/mobile/hub/site/<site>/stream/<sidx>/live`
  - These mirror the existing browser listen/relay endpoints but authenticate via the mobile token rather than `login_required`, allowing the iPhone app to monitor live audio directly from the hub relay path

- **Query-token support for mobile audio playback**:
  - Mobile token auth now also accepts `?token=...` in addition to `Authorization: Bearer ...` and `X-API-Key`
  - Added specifically so iPhone `AVPlayer` can play hub relay audio reliably on physical devices without depending on custom request headers

- **Mobile Reports API**:
  - `GET /api/mobile/reports/events` — token-protected reports/event feed suitable for recreating the hub Reports page in the iPhone app
  - `GET /api/mobile/reports/summary` — token-protected aggregate counts for top summary cards in the app
  - `GET /api/mobile/reports/clip/<clip_id>` — token-protected clip playback/download endpoint for report events with audio evidence
  - Reports events support mobile-friendly filtering via `site`, `stream`, `type`, `chain`, `before`, and `limit`

### Changed
- **Mobile `live_url` generation now returns mobile-safe relay URLs** — the mobile API no longer hands the app the browser/session-protected listen endpoints. `live_url` in mobile payloads now points at the mobile-token protected relay routes so the phone can fetch audio from one central hub origin.
- **Hub reports/clip access exposed cleanly to mobile clients** — the iPhone app can now mirror the existing reports workflow without weakening the existing logged-in web UI routes.

### Fixed
- **`level_dbfs` missing in mobile payloads** — some chain/node dictionaries were carrying live levels under `level` rather than `level_dbfs`, causing the iPhone app to receive `null` and show dead meters. Mobile serialization now uses `level_dbfs` when present and falls back to `level`, so real dBFS values propagate correctly to the app.
- **Metadata works but iPhone live audio fails** — root cause was an auth mismatch: the mobile JSON endpoints used mobile-token auth but the original listen routes were still web-session protected. Fixed by adding mobile-token protected relay endpoints and updating mobile `live_url` generation to use them.
- **Physical iPhone audio playback unreliable while Simulator worked** — supporting query-string tokens for mobile audio endpoints resolves the `AVPlayer` custom-header reliability issue seen on real devices.
- **Reports page not reproducible on mobile from the existing snapshot API alone** — added dedicated mobile reports endpoints plus clip access so the iPhone app can now mirror the hub reports experience instead of only approximating it from current chain state.

---
## [3.2.33] - 2026-03-21

### Changed
- **Chain monitor loop interval: 30 s → 10 s** — the alert state machine (fault detection, confirmation window, recovery) now runs every 10 seconds. The expensive trend computation remains gated at 30-second intervals, so hub CPU load is essentially unchanged. Benefits: confirmation timers are now accurate to ±10 s (was ±30 s), faults that start and clear within a 30-second window are no longer silently missed, and the persisted `chain_state` metric (see below) has 10 s granularity.

### Fixed
- **Chain history view ignores ad-break timer, always shows red** — the history/time-travel view on the Broadcast Chains page was trying to reconstruct whether a chain was in its confirmation window by walking backward through 1-minute `level_dbfs` metric snapshots (`_fault_duration_at`). This was off by up to 60 seconds — enough to make a chain appear as a confirmed fault (red) when it was actually still amber/pending. Fixed by writing the exact alert state machine state (`chain_state` metric: `1.0=ok`, `0.5=pending/adbreak`, `0.0=alerted`) to the metrics DB on every evaluation cycle (~10 s). The history endpoint now reads `chain_state` directly for an exact answer. The old level-based reconstruction is kept as a fallback for historical data written before this release.

---

## [3.2.32] - 2026-03-21

### Fixed
- **Editing an input makes the stream look dead until monitor restart**: `input_edit` was doing `inps[idx] = inp` — replacing the entire `InputConfig` object. Monitor threads capture a direct reference to the original object at `start_monitoring()` time and keep writing live data (`_last_level_dbfs`, `_audio_buffer`, `_stream_buffer`, `_ai_status`, RTP stats, DAB/FM state, SLA counters, etc.) to it. After the replacement, the dashboard and hub heartbeat read from the *new* object which has all runtime fields at their defaults (`_last_level_dbfs = -120.0`, `_audio_buffer = None`) — exactly what a dead/offline stream looks like. Fixed by updating config fields **in-place** on the existing object using `dataclasses.fields()` — only fields with `init=True` (the user-editable config) are overwritten; all `init=False` runtime state is preserved. The monitor threads never notice the change and continue operating without interruption. If the stream name changed, the `_stream_ais` lookup key is also updated atomically.

---

## [3.2.31] - 2026-03-21

### Changed
- **Hub client backups are now automatic and persistent**:
  - **Auto-daily backup**: `HubServer` runs a background thread that checks hourly and pushes a `backup` command to any online site whose last backup is more than 23 hours old. No manual intervention required — every site gets a fresh backup roughly once per day.
  - **Persistent disk storage**: backups are now written to `hub_backups/<site>/backup.zip` on the hub filesystem (plus a `backup_meta.json` sidecar). Previously they were held only in memory and lost on every hub restart. The backup index is reloaded from disk on startup so existing backups survive restarts.
  - **Backup button → direct download**: the "📥 Backup" trigger button on the hub site view has been replaced with a "⬇ Backup (Xh ago)" download link that immediately downloads whatever the hub already has on disk. If no backup exists yet (new site, first 24h), a "📥 Backup pending" badge is shown instead. No more waiting 60 s for a ZIP to upload.

---

## [3.2.30] - 2026-03-21

### Fixed
- **⬆ Update button on hub dashboard does nothing**: two bugs combined to make it silent. (1) `HUB_TPL` (the main hub dashboard) dynamically creates the update button via JS when a version mismatch is detected, but had **no click handler** for `.site-update-btn` — the button rendered fine but clicks were completely ignored. (2) The click handler in `HUB_SITE_TPL` (the per-site view) used `confirm()` for confirmation, which modern browsers **silently block on LAN HTTP origins** (same reason `removeSite` was already rewritten to use an inline bar). Both templates now use an inline amber confirmation bar (matching the existing site-removal pattern) that is fully CSP-compliant and works on HTTP.

---

## [3.2.29] - 2026-03-21

### Fixed
- **Comparators showing low confidence on pre/post processing pairs**: the previous algorithm used Pearson correlation on absolute `level_dbfs` values. A compressor or limiter deliberately flattens the dynamic range of the post-processing stream, causing near-zero variance and therefore low/erratic Pearson scores — even on a perfectly healthy chain. Replaced with a two-metric approach:
  - **Primary — silence/activity agreement** (processing-invariant): measures what fraction of 1-minute buckets both streams agree on silent vs active. Compressors and limiters cannot manufacture audio from silence, so this remains a reliable indicator regardless of how much processing sits between the two nodes. This is the base confidence score.
  - **Secondary — first-difference Pearson on active periods**: correlates level *changes* (not absolute levels) and only on time steps where both streams are carrying audio. This removes the DC-offset bias from limiters/AGC while still detecting dynamics divergence. Adds up to +20 pp to the base score but cannot lower it.
  - Hovering the comparator chip now shows a tooltip breakdown: overall confidence %, silence agreement %, dynamics r, and sample count.

---

## [3.2.28] - 2026-03-21

### Fixed
- **Chain history time-travel ignores ad-break / confirmation window**: clicking a fault log entry to view historical chain state was always showing the chain as full **FAULT** (red), even if the fault was still inside the `min_fault_seconds` confirmation window at that moment. The history endpoint (`/api/chains/history`) now reconstructs the pending/adbreak state from metric history: it queries the SQLite metric database backwards from the requested timestamp to find how long the fault node had been continuously below threshold at that point. If that duration was less than `min_fault_seconds`, the chain is shown as **AD BREAK** or **CHECKING…** (amber) with the correct remaining-seconds countdown, exactly as the live view would have shown it. The ad-break candidate check (fault before mixin node, mixin still up) is also applied in the historical path.

---

## [3.2.27] - 2026-03-21

### Added
- **Reports page — Alerts / Logs tabs**: the Alert Reports page now has two tabs above the filter bar. **Alerts** (default) hides informational `DAB_AVAILABLE` and `DAB_UNAVAILABLE` events that would otherwise spam the list. **Logs** shows only those informational events. Both tabs respect all existing stream/type/date/clip filters. Dynamically-loaded rows (via the 15 s refresh) are also categorised correctly in real time.

---

## [3.2.25] - 2026-03-21

### Fixed
- **False chain fault alert on ad-break recovery**: when a chain was in the confirmation window (pending/adbreak state) and the first node recovered (studio comes back after an ad), a brief heartbeat-reporting lag (~1 heartbeat cycle, ≤5 s) could leave the next downstream node still appearing silent for one monitor cycle. Because the elapsed time was already ≥ `min_fault_seconds` the system would fire an alert for that downstream node even though it was about to report as healthy. Fix: the confirmation window now tracks which chain position (`fault_index`) triggered the pending state. If the fault position **shifts** during the window, `since` is adjusted so that the new position has a short fixed grace window of **2 × heartbeat interval (10 s)** remaining before it can alert — rather than a full timer reset (which would have delayed a genuine fault). If the downstream node recovers within those 10 s (the normal lag scenario) no alert fires; if it stays down for 10 s it is treated as a real fault and alerts immediately. The log shows `"fault position shifted (pos N → M) — applying 10s grace window"` when triggered.

---

## [3.2.24] - 2026-03-21

### Changed
- **Shared-fault detection now relies exclusively on machine tags** — previously, when a chain fault fired, SignalScope would look for other chains that shared the same *site name* (or local stream) and append a "NOTE: other chains share site X" warning to the alert message. Site name is no longer used as a grouping key. Only nodes with an explicit **machine tag** set in the chain builder will participate in cross-chain shared-fault detection. Nodes without a machine tag are treated as independent regardless of which site they belong to. This affects both the alert message text and the "Also affecting: …" badge shown on the chain visual node card.

---

## [3.2.23] - 2026-03-21

### Added
- **Remote config backup**: hub dashboard gets a **"📥 Backup"** button per site — clicking it pushes a `backup` command to the client; the client generates a full backup ZIP (config + AI models + metrics DB + SLA/alert/hub state) and uploads it to the hub via `/hub/backup_upload` (same HMAC signing as clip uploads); the hub stores the latest backup per site and shows a timestamped **"⬇ Download Backup"** link; `GET /api/hub/site/<name>/backup` streams the ZIP, `POST` triggers a fresh backup
- **Network path test (ping)**: hub dashboard gets a **"🔍 Ping"** button per site — opens a modal to enter a target IP/hostname; hub pushes a `ping_test` command; client runs `ping -c 4` (Linux) or `ping -n 4` (Windows) and POSTs results back via `/hub/ping_result`; modal polls and displays full output with pass/fail indicator; `GET /api/hub/site/<name>/ping` returns latest result

---

## [3.2.22] - 2026-03-21

### Fixed
- **Chain fault amber countdown regression**: chains configured with a `min_fault_seconds` confirmation delay were briefly showing red (FAULT) at application startup before switching to amber (CHECKING…/AD BREAK). Root cause was the warmup iteration of the chains monitor loop seeding all pre-existing faults as `"alerted"` (confirmed) regardless of the confirmation delay setting. The fix: during the warmup pass, chains with `min_fault_seconds > 0` are now seeded as `"pending"` (amber) with the `since` timestamp backdated so the confirmation window has already elapsed — meaning the first real evaluation fires the alert if the fault is still ongoing, rather than creating an indefinitely-amber chain. Chains with `min_fault_seconds = 0` continue to be seeded as `"alerted"` to suppress duplicate alerts, as before. Ad-break candidates (fault before mixin point, mixin still up) receive a fresh confirmation window from `now` so a legitimate ad break that was in progress at restart time is handled correctly.

---

## [3.2.21] - 2026-03-21

### Added
- **System health in heartbeat payload**: client sites now report disk usage (total/used/free/%), process uptime, and (if psutil is available) CPU %, RAM %, and OS uptime in every heartbeat
- **App log in heartbeat payload**: last 30 lines of the application log are included in the heartbeat, each truncated to 200 characters
- **Hub: system health display**: summary bar on each site card now shows disk free (colour-coded green/amber/red), CPU %, RAM %, and process uptime
- **Hub: "📋 Log" button** per site — fetches the remote site's last 30 log lines and displays them newest-first in a modal overlay (dark background, monospace font)
- **Hub: "🔄 Restart" button** per online/running site — pushes a `restart` command; the client process restarts via `os.execv` after a 1-second delay
- **Hub: "🔄 Retrain AI" button** per stream in the Sources panel — pushes a `retrain_stream` command that calls `monitor.request_retrain(stream)` on the client
- **Hub: "🎚 Calibrate Silence" button** per stream in the Sources panel — prompts for headroom (default 6 dB), then sets the silence threshold to `current_level − headroom` on the client and saves the config
- New hub API endpoints: `GET /api/hub/site/<site>/log`, `POST /api/hub/site/<site>/restart`, `POST /api/hub/site/<site>/retrain`, `POST /api/hub/site/<site>/calibrate_silence`
- `_PROCESS_START` module-level constant (set at import time) for accurate process uptime reporting
- Optional `psutil` import at startup (try/except fallback to None) for CPU/RAM/OS uptime metrics

---

## [3.2.20] - 2026-03-21

### Fixed
- Hub client now starts immediately when hub settings are saved via the Settings page — previously it only connected when the monitor loop was started, meaning a freshly configured site showed no hub connection until monitoring was manually started
- `start_hub_client()` is now called after every Settings save; it is a no-op if already connected to the same URL, but if the hub URL or mode has changed it stops the old client and starts a new one with the updated configuration — no restart required

---

## [3.2.19] - 2026-03-21

### Added
- Hub dashboard now flags remote sites running outdated software — the build badge turns amber with a tooltip showing the hub's current version
- **"⬆ Update" button** appears on any online site running a different build; clicking it sends a confirmation prompt then pushes a `self_update` command via the hub heartbeat mechanism
- Hub serves its own `signalscope.py` at `/hub/update/download` for authenticated clients (HMAC-signed GET, same secret as heartbeats — unauthenticated requests are rejected)
- Client `_cmd_self_update` handler: downloads the new script, validates Python syntax with `py_compile`, atomically replaces the running file, then restarts via `os.execv` — if syntax validation fails the update is aborted and the original file is untouched
- New `/api/hub/site/<site>/update` POST endpoint (login + CSRF required) for the hub dashboard "Update" button

---

## [3.2.18] - 2026-03-21

### Fixed
- Hub Reports Chain column now correctly shows the chain name for streams that are part of a **stack node** — previously only top-level (non-stack) nodes were indexed in the stream→chain lookup, so any stream inside a stack showed "—" even though it belonged to a chain
- If a stream appears in multiple chains (e.g. as a redundancy node across several chains), the Chain column now shows all chain names comma-separated

---

## [3.2.17] - 2026-03-21

### Added
- Chain fault history is now persisted to `metrics_history.db` (new `chain_fault_log` table) — history survives restarts and is no longer lost on service restart
- Fault log loaded from DB on hub startup for all configured chains; in-memory ring buffer is seeded from DB rather than starting empty
- Fault log entries now carry a stable UUID `id` used as the DB primary key, enabling precise updates when recovery time or clip references are added later
- Clip references (local and remote) are written to the DB immediately when available; remote clips uploaded via `/hub/clip_upload` update the DB entry on arrival
- `/api/chains/<cid>/fault_log` now reads from DB (up to 100 entries) rather than the 25-entry in-memory ring buffer — full history visible in the UI
- DB pruning extended to also trim `chain_fault_log` entries older than the configured retention period (default 90 days)

---

## [3.2.16] - 2026-03-21

### Added
- Chain fault history now shows audio clip download buttons ("⬇ Fault" / "⬇ Last Good") inline in the Fault History table — Clips column only appears when at least one entry has clips; each button triggers a direct browser download of the WAV
- Local clips saved during chain fault detection are back-patched into the fault log entry immediately; remote clips uploaded from client sites are linked to their fault entry on arrival via `chain_id`
- New `/api/chains/clip/<key>/<fname>` endpoint serves WAV clips for download (login required, path traversal protected)

---

## [3.2.15] - 2026-03-21

### Added
- Remote clip capture for broadcast chain faults: when a chain fault fires on the hub, a `save_clip` command is pushed to each remote site that is at the fault position or last-good position — the client saves a WAV clip of the affected stream locally (visible in that site's own Reports) and asynchronously uploads it to the hub via the new `/hub/clip_upload` endpoint
- Hub `/hub/clip_upload` endpoint: receives base64-encoded WAV from clients, saves under `alert_snippets/<site>_<stream>/`, and writes an entry to the hub alert log — so the clip appears in the hub Reports page alongside the chain fault event
- HMAC/AES-256-GCM security on clip uploads — same signing and encryption as heartbeats; hub verifies signature and timestamp freshness before accepting

---

## [3.2.14] - 2026-03-21

### Fixed
- Chain fault and chain recovery events now always appear in the Reports page for hub-only chains — previously `_add_history` was only called for local nodes, so faults on all-remote hub chains were invisible in the alert log and Reports page despite sending notifications correctly

---

## [3.2.13] - 2026-03-21

### Added
- RTP packet loss now displayed on broadcast chain nodes for Livewire/AES67 streams only — shown as "RTP Loss: X.X%" below the level reading; colour-coded grey (0%), amber (>0.5%), red (>5%)
- RTP loss at time of fault is captured in the chain fault history log and shown as a dedicated column in the Fault History table (column only appears when at least one entry has RTP data)

---

## [3.2.12] - 2026-03-20

### Fixed
- Chain builder machine tag not persisting — `_clean_single_node()` in the `/api/chains` save handler was only keeping `site`, `stream` and `label`, silently discarding the `machine` field. Machine tag is now preserved through save/edit cycles.

---

## [3.2.11] - 2026-03-20

### Fixed
- Broadcast chain nodes with a confirmation window (ad break countdown or plain pending) no longer flash red before going amber. Previously the `/api/chains/status` endpoint was polled by the frontend every few seconds but the monitor loop that sets the `"pending"` state only runs every 30 seconds — during that gap `display_status` fell through to `"fault"` (red). The API now treats any chain where `internal_state` is `None` or `"ok"` but the live eval returns `"fault"` with `min_fault_seconds > 0` as immediately amber/pending, matching what the monitor loop would do on its next tick.

---

## [3.2.10] - 2026-03-20

### Changed
- Backup ZIP now includes all critical data files: `metrics_history.db` (signal history), `sla_data.json`, `alert_log.json`, and `hub_state.json` in addition to config and AI models
- Restore handler updated to restore metrics DB (closes shared connection first), SLA data, alert log and hub state from backup ZIP
- Restore upload cap raised from 64 MB to 512 MB to accommodate large metrics databases
- Settings page backup description and button label updated to reflect full backup scope

---

## [3.2.9] - 2026-03-20

### Added
- Extended SQLite metric history to capture all previously unused metrics:
  - `silence_flag` (1.0 = silent, 0.0 = audio present) for all stream types
  - `clip_count` (clipping events per snapshot) for all stream types
  - `fm_snr_db`, `fm_stereo` (0/1), `fm_rds_ok` (0/1) for FM streams
  - `dab_sig` (signal level dBm), `dab_bitrate` (kbps) for DAB streams
  - `rtp_loss_pct` now also included in the metric history selector UI
  - `ptp_offset_us`, `ptp_jitter_us`, `ptp_drift_us` written once per minute for local PTP monitor (keyed `ptp/local`) and per connected hub site (keyed `ptp/<site>`)
  - Hub site `health_pct` and `latency_ms` written to metric history once per heartbeat (keyed `site/<name>`)
- Both stream metric history chart selectors updated with all new metric options

---

## [3.2.6] — Broadcast Chain Stacking, Ad Break Intelligence & Click-to-Listen

### Broadcast Chain Node Stacking

Nodes at the same position in a chain can now be **stacked** — place multiple streams at a single point to model parallel monitoring (e.g. FM Rx and DAB Rx both hanging off the same transmitter output).

**How stacks work:**

- In the chain builder, each position can hold one or more streams. Click **+ Stack** within a position to add a second stream at the same point
- Each stack has a **fault mode**:
  - **ALL down = fault** — the position only faults when every stream in the stack is silent (ideal for redundant receivers; one surviving stream means the TX is still on air)
  - **ANY down = fault** — the position faults if any single stream goes silent (stricter monitoring)
- Stacks render as a vertical column of bubbles in the chain visual, each with its own live dBFS level and colour

**Stack fault alerts are descriptive.** Examples:

> `Chain fault in 'Absolute' — all 2 stream(s) at 'TX Output' are silent (DAB Rx, FM Rx). First failed position in the chain.`

> `Chain fault in 'Absolute' — 1 of 2 stream(s) at 'TX Output' is silent (DAB Rx silent; FM Rx OK) — stack mode is ANY, so this triggers a fault.`

- Audio clips are saved for **every** down local sub-node in a faulted stack
- Recovery logging descends into stacks so Hub Reports shows a complete per-stream fault timeline

### Ad Break Silence Handling

Chains where upstream nodes legitimately go silent during ad breaks (because ads inject from a separate feed) no longer generate false fault alerts.

#### Mix-in point

Mark one node in the chain as the **Ad mix-in point** — this is where ad audio enters the chain. If that node is still carrying audio, SignalScope knows ads are playing and holds the fault alert.

#### Fault confirmation delay

Set a **Fault confirmation delay** (seconds) in the chain builder. A fault only fires if the chain stays down for that entire window without the mix-in point recovering. Typical values: 90–180 s depending on ad break length.

#### Visual ad break state

During the confirmation window:

- Faulted upstream nodes turn **amber/yellow** (not red)
- Badge shows **AD BREAK — 87s** with a live countdown
- Fault label reads *"↳ Likely ad break — 87s remaining before fault alert"*
- The mix-in point node shows a **🔀 Mix-in — playing** marker
- All nodes downstream of the mix-in stay **green**

#### Instant bypass

If the mix-in point also goes silent mid-countdown, it cannot be an ad break — the confirmation timer is bypassed and the alert fires immediately, even if the delay hasn't elapsed.

#### Correct startup behaviour

On app start, pre-existing silence that looks like an ad break is shown as **amber** immediately — the chain never flashes red before settling into the countdown state.

### Click-to-Listen on Chain Nodes

Every node bubble on the Broadcast Chains page is now a **live audio monitor you can click**.

- **Click any node** → a pulsing blue ring appears and live audio streams from that point in the chain
- **Click again** → stops playback
- **Click a different node** → switches to that stream instantly
- No visible player controls — just the bubble and a small 🔊 icon
- Works for both local streams and remote hub sites (uses the same MP3 relay as the main dashboard)
- Stack sub-nodes each have their own live URL — click FM Rx or DAB Rx independently within a stack

### Signal Comparators on Chains

Add **correlation comparators** between any two positions in a chain to measure how well the signal tracks end-to-end or between specific points:

- Click **+ Add Comparator** in the chain builder and select any two positions
- **↔ Add End-to-End** adds a comparator from position 0 to the last position in one click
- Correlation is computed as Pearson r over the last 10 minutes of metric history (requires ≥ 5 minutes of shared data)
- Results shown as colour-coded chips below the chain visual:
  - 🟢 ≥ 80% — good correlation
  - 🟡 50–79% — moderate (check for processing delay or dropout)
  - 🔴 < 50% — poor correlation (potential fault or mismatch)
  - "No data yet" during the first few minutes after adding a comparator

---

## [3.2.0] — Wall Mode, RDS/DAB Name Alerting & Broadcast Chain Intelligence

### Hub Wall Mode — Complete Redesign

Wall mode (`/hub?wall=1`) is now a purpose-built wall board display rather than a CSS-enlarged version of the hub dashboard.

**What it shows:**

- **Header bar** — live clock (ticking every second), summary pills (⚠ Alerts / ⚡ Warnings / ✗ Sites Offline / ✓ All Systems OK), Exit Wall Mode link
- **Connected Sites strip** — one colour-coded pill per connected site: 🟢 green = OK, 🟡 amber = warnings, 🔴 red = alerts or offline, ⬜ grey = offline; alert/warn count shown on the pill
- **Broadcast Chains panel** — every configured chain shown as a horizontal row of colour-coded nodes with arrows; fault node marked **FAULT POINT**, downstream nodes greyed out; chain status badge (ALL OK / FAULT) at the right; updates every 15 seconds via AJAX without page reload
- **Stream Status grid** — every stream from every site in one unified grid, colour-coded border (green/amber/red/grey), level bar, device type badge (DAB/FM/LW), RDS PS or DAB service name, Signal Lost / Offline label when down; sorted alerts-first
- Page auto-refreshes every 60 seconds to pick up newly added streams or sites

### RDS Programme Service Name Alerting

Alert when the station name received on an FM stream does not match what is expected.

**Two modes:**
- **Expected name set** — fires `FM_RDS_MISMATCH` when the received RDS PS name differs from the configured expected name (e.g. wrong station on the feed)
- **No expected name** — fires `FM_RDS_MISMATCH` when the name changes from what was previously seen (unexpected format change or wrong feed)

**📌 Set button on hub** — next to the live RDS PS name on each FM stream card, a **📌 Set** button pins the current live name as the expected name without typing anything. A ✓ indicator replaces the button when the name matches; a ⚠ indicator with the expected name shows when there is a mismatch. An **📌 Update** button lets you re-pin to the new name in one click.

Alert type: `FM_RDS_MISMATCH` — included in all notification channels and hub forwarding rules.

### DAB Service Name Alerting

Same capability for DAB streams — alert when the service name received from the mux does not match expected.

**📌 Set button on hub** — same one-click pinning on each DAB stream card. Shows ✓ when matching, ⚠ when mismatched.

Alert type: `DAB_SERVICE_MISMATCH` — included in all notification channels and hub forwarding rules.

### Broadcast Chains — Fault Intelligence

#### Chain fault notification deduplication

When a stream is part of a broadcast chain, the **CHAIN_FAULT alert takes priority** over individual stream alerts:

- Individual stream alerts (SILENCE, STUDIO_FAULT, STL_FAULT, etc.) are still **logged to alert history** so they appear in Hub Reports
- The **push notification** (email, Teams, Pushover) is **suppressed** for the individual stream — only the CHAIN_FAULT fires
- The CHAIN_FAULT message provides richer context: the exact fault node, which site it is on, and how many downstream nodes are affected — rather than a generic silence notification with no location information

This prevents alert storms where a single fault in a chain would otherwise generate one CHAIN_FAULT plus one SILENCE/STL_FAULT per affected stream.

#### Improved fault alert message

Chain fault alerts now clearly state the fault location and impact:

> **Chain fault in 'Absolute' — signal lost at 'Absolute Pre - London' (site: London - Livewire, stream: Absolute Pre - London). This is the first failed point in the chain. 1 downstream node(s) may also be affected.**

The fault point is the first node with no signal — everything upstream of it was alive, everything downstream may be starved as a consequence.

#### Audio evidence clips

When a chain fault is detected, SignalScope automatically clips audio from the two most useful points:

- **Fault node clip** — audio from the stream that went down, capturing the last few seconds before silence. Filename: `YYYYMMDD-HHMMSS_chain_<ChainName>_fault.wav`
- **Last-good node clip** — audio from the node immediately before the fault in the chain, confirming it was still carrying signal. Filename: `YYYYMMDD-HHMMSS_chain_<ChainName>_last_good.wav`

Both clips are saved to the relevant stream's `alert_snippets/` folder and appear in Hub Reports with playback and download links. The chain name is embedded in the filename so there is no ambiguity when a stream appears in multiple chains.

Recovery events are also logged against all local chain nodes so the timeline in Hub Reports shows a complete fault and recovery picture per stream.

#### Chain fault logging to alert history

CHAIN_FAULT events now appear in Hub Reports alongside all other alert types:

- **⛓ Chain Faults** summary card — click to filter the table to chain events only
- **Chain column** — every row shows which broadcast chain the stream or event belongs to (green ⛓ badge)
- **Chain filter dropdown** — filter the entire table to show only events associated with a specific chain, including both CHAIN_FAULT events and individual stream alerts from streams in that chain
- CHAIN_FAULT rows are visually distinct with a red left border and their own badge colour

#### Broadcast Chains — Design & UX Improvements

- **Chains page redesign** — now fully matches the hub/dashboard visual design: same CSS variables, background watermark, `border-radius:14px` cards with box-shadow, `border-left:4px` status strip, matching button styles
- **Card status colouring** — chain cards now update their left border colour live (green = OK, red = FAULT, grey = unknown) as the chain status changes, matching the hub site card pattern
- **Edit and Delete buttons fixed** — were silently blocked by CSP inline-handler hash validation; moved to `data-*` attributes with delegated event listeners
- **📌 Set buttons fixed** — same CSP fix applied to RDS and DAB service name Set/Update buttons on hub stream cards

### DAB Mux Startup Reliability

- **Service count stabilisation** — `_poll_mux` now waits for the service count to be **identical across two consecutive 5-second polls** before announcing the mux as ready, rather than announcing on the first non-empty batch. Prevents monitoring threads from starting before welle-cli has finished enumerating all services on large multiplexes
- **Service lookup deadline extended** — the per-stream service lookup window after mux-ready has been increased from 8 to 20 seconds, accommodating services that take longer to appear on some hardware

---

## [3.1.0 Phase 3a] — Broadcast Signal Chains

A new **Broadcast Chains** page (hub-only) lets you model the physical signal path of any service as an ordered chain of monitoring points — from studio through STL, transmitter sites, and DAB mux — and immediately see where a fault has occurred.

### What it does

- **Visual chain diagram** — each node is a live status box; green = audio present, red = fault, grey = site offline, amber = unknown
- **Fault location** — the hub walks the chain left to right; the first node that is down or offline is identified as the fault point and marked with a **⚠ Fault here** badge
- **Downstream suppression** — nodes after the fault point are greyed out; a fault upstream means their status is indeterminate
- **Live level display** — each node shows the current dBFS level, refreshed every 5 seconds via AJAX without a page reload
- **CHAIN_FAULT alert** — fires through all configured notification channels (email, Teams, Pushover, webhooks) when a chain transitions from OK to fault; subject line is `CHAIN FAULT — <chain name>`, body names the exact fault node, site, and stream
- **CHAIN RECOVERED alert** — fires when a faulted chain returns to fully OK, so you know when the issue has resolved without checking the dashboard
- **Hub site rules** — `CHAIN_FAULT` is included in the default forwarding types; you can enable/disable it per-site in hub settings like any other alert type

### Setting up a chain

1. Go to **Hub → Broadcast Chains** in the top navigation
2. Click **+ New Chain**
3. Give the chain a name, e.g. `Cool FM Distribution`
4. Click **+ Add Node** for each point in the signal path:
   - **Site dropdown** — choose `This node (local)` for streams running on the hub machine itself, or any connected remote site by name
   - **Stream dropdown** — populated automatically from the selected site's available streams
   - **Label field** — optional friendly name shown on the node box, e.g. `Manchester TX`; defaults to the stream name if left blank
5. Nodes are ordered left to right — add them in signal flow order (source first, destinations last)
6. Click **💾 Save Chain**

### Example chain layouts

**Studio → STL → Transmitter:**
```
[Studio Feed (local)] → [STL Monitor (Site: STL Node)] → [TX Air Monitor (Site: Manchester TX)]
```
If the STL monitor goes down but the Studio Feed is healthy, the fault marker appears on the STL node — pointing directly at the STL link rather than requiring you to check each site manually.

**Multi-TX same service:**
```
[Cool FM DAB (Site: NI DAB Hub)] → [Cool FM FM Site 1 (Site: Manchester TX)] → [Cool FM FM Site 2 (Site: Liverpool TX)]
```
If Site 1 is up but Site 2 goes silent, the chain shows fault at `Liverpool TX` — you get a named TX site in the alert rather than a generic silence notification.

**DAB mux chain:**
```
[Studio Playout (local)] → [DAB Mux Input (Site: Mux Node)] → [Cool FM DAB (Site: NI DAB Hub)] → [Downtown Country DAB (Site: NI DAB Hub)]
```

### Alert example

When a fault is detected you will receive:

> **Subject:** `CHAIN FAULT — Cool FM Distribution`
> **Body:** `Chain fault in 'Cool FM Distribution' — fault at 'Manchester TX' (site: Manchester TX, stream: Cool FM FM)`

When the chain recovers:

> **Subject:** `CHAIN RECOVERED — Cool FM Distribution`
> **Body:** `Chain 'Cool FM Distribution' has recovered — all nodes OK.`

### Fault detection logic

A node is considered **down** if:
- Its stream's audio level is ≤ −55 dBFS (silence floor), **or**
- It is a DAB stream and `dab_ok` is false (service missing from ensemble)

A node is **offline** if its site has not sent a heartbeat within the site timeout window.

Chains are evaluated every **30 seconds** by a background thread on the hub. A `CHAIN_FAULT` alert fires on the OK → fault transition only (not repeatedly while the fault persists), and a `CHAIN RECOVERED` alert fires on the fault → OK transition.

---

## [3.1.0 Phase 3b] — Extended Alerts & Local Audio Input

### Composite Alert Classification — DAB & RTP

The silence alert classification introduced in 3.0 for FM sources now extends to DAB and RTP/Livewire:

- **DAB_AUDIO_FAULT** — fires when a DAB stream goes silent while the mux is locked and SNR is healthy (≥ 5 dB); indicates a studio or playout fault on that service rather than an RF or receiver problem
- **RTP_FAULT** — fires when a Livewire/AES67 stream goes silent with ≥ 10% concurrent packet loss; distinguishes a network fault from a genuine content silence
- Both alert types are included in `_HUB_DEFAULT_FORWARD_TYPES` and the hub site rules checkbox list, so they propagate through the hub to email/Teams/Pushover exactly like FM composite faults

Full composite alert matrix:

| Alert | Source | Condition |
|---|---|---|
| `STUDIO_FAULT` | FM | Silence + carrier + RDS present → playout failure |
| `STL_FAULT` | FM | Silence + carrier healthy but RDS absent → STL/link failure |
| `TX_DOWN` | FM | Silence + weak/no carrier + no RDS → transmitter/RF failure |
| `DAB_SERVICE_MISSING` | DAB | Ensemble locked but service gone from mux |
| `DAB_AUDIO_FAULT` | DAB | Silence + mux locked + SNR ≥ 5 dB → studio/playout fault |
| `RTP_FAULT` | Livewire/AES67 | Silence + ≥ 10% packet loss → network fault |

### Local Sound Device Input (ALSA/PulseAudio)

- **New input type** — "Local Sound Device" added to the Add Input form alongside Livewire/RTP/HTTP, DAB, and FM
- **Device picker** — clicking the type reveals a drop-down populated from `/api/sound_devices`; a Refresh button re-queries the OS at any time
- **ALSA/PulseAudio support** — captures from any input device (microphone, line-in, USB audio, loopback) via the `sounddevice` Python library (PortAudio backend)
- **Address format** — stored as `sound://<device_index>` (e.g. `sound://2`); device index is an integer from the OS device list
- **Full pipeline** — captured audio feeds into the same `analyse_chunk()` pipeline as all other source types: level, LUFS, AI, silence/clip/hiss alerts, SLA tracking
- **Installer** — `libportaudio2` added to the apt package list (installed on both fresh installs and updates); `sounddevice` added to the pip install line

### Extended Trend Analysis

- **Day-of-week baseline** — in addition to the hour-of-day baseline, trend analysis now builds a 168-bucket (day × hour) model from 28 days of history; used when a bucket has ≥ 10 samples, otherwise falls back to the 14-day hour-only baseline
- **Sustained deviation scoring** — the trend badge escalates from amber to red when a stream has been continuously above or below the ±1.5σ band for ≥ 10 consecutive minutes; duration shown in the badge (e.g. `📉 Lower than usual (−2.3σ, 14 min)`)
- **Baseline type indicator** — badge shows `·dow` suffix when the day-of-week model is active
- **API** — `/api/trend/<stream>` returns `baseline_type` (`dow_hour` or `hour`), `sustained_minutes`, and the full 168-bucket baseline table

---

## [3.1.0 Phase 2] — Metric History & Trend Analysis

### SQLite Metric History
- **`metrics_history.db`** — a local SQLite database is created automatically on first start (no migration needed for existing installs); no new Python dependencies (`sqlite3` is built-in)
- **Per-stream time-series storage** — `level_dbfs`, `lufs_m/s/i`, `fm_signal_dbm`, `dab_snr`, `dab_ok`, `rtp_loss_pct`, and `rtp_jitter_ms` are written once per minute per stream
- **Hub aggregation** — hub-mode nodes write metrics for all connected remote sites on every approved heartbeat (keyed as `SiteName/StreamName`), so a hub-only machine with no local streams still accumulates full history
- **90-day rolling retention** — rows older than 90 days are pruned automatically once per day; configurable via `METRICS_RETENTION_DAYS`

### Signal History Charts
- **📈 Signal History** — collapsible chart on every stream card in the hub dashboard and replica page; lazy-loaded when opened, no page refresh required
- **Range selector** — 1 h / 6 h / 24 h buttons reload the chart without a page reload
- **Metric selector** — Level dBFS, FM Signal dBm, DAB SNR, LUFS Momentary / Short-term / Integrated, RTP Jitter; only metrics relevant to the stream type are shown
- **Canvas-rendered** — lightweight inline canvas chart with no external dependencies; works fully offline on LAN installations
- **Trend reference band** — when viewing Level dBFS, a dashed yellow line and shaded ±1σ band shows the expected level range for the current hour of day (requires ≥10 data points; see Trend Analysis below)

### Availability Timeline
- **24 h availability bar** — a thin colour-coded timeline bar sits below the level bar on every hub stream card and replica page card, auto-loaded on page render
- **Click to cycle** — click the bar to cycle between 24 h → 1 h → 6 h → 24 h views; the label on the left updates to match
- **Colour coding**: 🟢 green = signal present, 🔴 red = silence / audio floor, 🟡 amber = DAB service missing (ensemble locked but service absent), ⬛ dark = no data
- **API** — `/api/timeline/<stream>?hours=24` returns bucketed segments (1-min / 5-min / 15-min buckets depending on time range)

### Trend & Pattern Analysis
- **Hour-of-day baseline** — a 14-day rolling baseline is computed per stream per hour of day using an efficient SQLite GROUP BY query (no per-row Python processing)
- **Deviation detection** — current level is compared to the baseline mean; deviations beyond ±1.5σ trigger a `lower_than_usual` or `higher_than_usual` status
- **Stream card badge** — `📉 Lower than usual (-2.1σ)` shown in amber, `📈 Higher than usual (+1.7σ)` shown in blue; hidden when within normal range or when there is insufficient history (< 10 data points for the current hour)
- **AJAX-safe** — trend badges survive the hub dashboard's 5-second AJAX refresh cycle; results are cached in JS memory and re-applied after each `hubRefresh()` call
- **API** — `/api/trend/<stream>` returns `status`, `deviation` (σ), `current_level`, `baseline` (mean/std/n), and the full 24-hour baseline table for all hours

### Metric History API
- **`/api/metrics/<stream>?metric=level_dbfs&hours=24`** — returns `[[ts, value], …]` points and `available_metrics` list; hub uses `site/stream` path format
- **`/api/timeline/<stream>?hours=24`** — availability segments with bucket size adaptive to the requested time range
- **`/api/trend/<stream>`** — current-hour deviation analysis vs 14-day baseline

---

## [3.0.3–3.0.5] — Hub Approval, Remote Source Management & Stability Fixes

### Hub: Site Approval (3.0.3)
- **New sites require explicit approval** — when a client connects for the first time the hub holds it in a *Pending Approval* state; no data is processed and no commands are delivered until a hub admin clicks **✓ Approve** on the hub dashboard
- **Old-build detection** — clients running a build older than 3.0.3 (which predate the approval system) are flagged with an **⚠ Update Required** banner instead of an Approve button; the operator is prompted to update the site before adopting it
- **Reject** button dismisses an unwanted connection request without approving it

### Hub: Site Persistence (3.0.3)
- **No auto-prune** — sites are never automatically removed regardless of how long they have been offline; only the explicit **✕ Remove** button deletes a site from the hub
- **Remove button fixed** — modern browsers block `confirm()` on LAN/HTTP origins; replaced with an inline confirmation bar using delegated event listeners

### Hub: Remote Source Management (3.0.3)
- **Add sources from the hub** — hub operators can add RTP, HTTP, FM, and DAB sources to any connected client directly from the hub dashboard without logging into the client
- **FM-specific fields** — selecting FM reveals frequency (MHz), PPM offset, and dongle serial fields; the correct `fm://<freq>?serial=...&ppm=...` device address is built automatically
- **DAB scan and bulk-add** — selecting DAB reveals a channel/PPM/serial scan panel; clicking **🔍 Scan Mux** queries the client's welle-cli session and returns all services on the multiplex; select any or all and click **➕ Add Selected Services** — each service is added with its broadcast name and a correctly-formed `dab://<Service>?channel=<CH>` device address
- **Name field hidden for DAB** — station names come from the scan result; manual name entry and the generic Add Source button are hidden when DAB is selected
- **DAB device_index format fixed** — hub-added DAB sources now produce `dab://ServiceName?channel=12D` (matching the local add form) instead of the incorrect `dab://12D` that was produced previously

### Hub Dashboard UX (3.0.3)
- **Open Dashboard opens in same tab** — removed `target="_blank"` from the replica dashboard link
- **Auto-refresh pauses when panel is open or inputs are dirty** — the 15-second hub replica page refresh no longer wipes form inputs mid-edit

### Stream Comparator Fixes (3.0.3)
- **Cards now show PRE / POST badges** — stream cards with a comparison role display a coloured PRE or POST badge
- **Dashboard 500 fixed** — the index route was only passing 3 fields in `comparators_data` but the template accessed 10+ fields; all fields now passed, eliminating silent Jinja2 `UndefinedError`
- **Configuration hint** — if streams have comparison roles configured but no active pair exists, a guidance panel is shown explaining what to check

### Settings Discoverability (3.0.3)
- **Update and Backup accessible from every settings tab** — a ⬇ Backup link and 🔄 Update button are present in the action row of every settings panel; no longer necessary to scroll to the Maintenance tab to check for updates or download a backup

### Installer Fixes (3.0.3–3.0.5)
- **Raspberry Pi 5 overclock suppressed** — the installer no longer offers overclock settings when Pi 5 is detected (overclock is not supported on Pi 5 via this method)
- **Sudo prompt timing fixed** — the sudo password prompt now appears only after all interactive questions have been answered, preventing the password from being entered into the wrong field
- **Local file tie-breaking** — if a local `signalscope.py` in the current directory has the same version as the installed copy, the installer now prefers the local file (prompting a reinstall) rather than reporting "already up to date"
- **psutil added** to the core pip install line for hub CPU / memory stats

### Hub Dashboard Crash Fixes (3.0.4–3.0.5)
- **500 after site removal fixed** — pending site stubs lack `streams`, `ptp`, `comparators` etc.; the template now skips those sections entirely for pending sites via `{% if not _pending %}` guards
- **500 after site approval fixed** — between approval and the client's next full heartbeat, the site dict is still a minimal stub; `hub_dashboard()` now sets safe defaults (`streams=[]`, `ptp=…`) so the page renders cleanly immediately after approval
- **psutil hub stats** — hub CPU and RAM usage now displayed in the hub summary bar (requires psutil, installed automatically from 3.0.5)

---

## [3.0.1–3.0.2] — Composite Logic Alerts, DAB Service Missing & Hub Notification Delegation

### Composite Logic Alerts (FM)
- **STUDIO_FAULT** — silence detected while carrier and RDS are healthy; points to a studio/console fault upstream of the transmitter
- **STL_FAULT** — silence with carrier present but RDS absent; indicates a studio-to-transmitter link failure
- **TX_DOWN** — silence with weak or absent carrier; indicates transmitter or antenna failure
- All three replace the generic SILENCE alert for FM streams with an RTL-SDR source, giving engineers an immediate fault location rather than just a silence notification

### DAB Service Missing Alert
- **DAB_SERVICE_MISSING** — fires when the DAB ensemble is locked but the configured service disappears from the multiplex; useful for detecting mux software faults while the RF path remains healthy

### RTP Jitter Metric
- RFC 3550-style inter-arrival time jitter tracked per Livewire/AES67 stream
- Displayed live on each stream card (hidden when zero)
- Colour-coded: green below 5 ms, amber above

### Hub Notification Delegation (3.0.2)
- **Suppress local notifications** — new per-client setting; when a client is connected to a hub, all email/webhook/Pushover alerts are suppressed locally and delegated to the hub instead
- **Per-site alert rules on hub** — hub operators can configure forwarding rules on a per-client-site basis: enable/disable forwarding and select which alert types to forward (from the full type list)
- Deduplication by event UUID prevents duplicate notifications when a client reconnects

---

## [2.6.56–2.6.67] — LUFS Monitoring, Alert Escalation, Stream Comparator & Self-Update

### LUFS / EBU R128 Loudness Monitoring
- **True peak alert (LUFS_TP)** — alert when the true peak level exceeds a configurable dBTP threshold (default −1.0 dBTP); fires per chunk
- **Integrated loudness alert (LUFS_I)** — alert when the 30-second rolling integrated loudness deviates from a configurable EBU R128 target (default −23 LUFS ± 3 LU)
- K-weighting filter applied in real-time via biquad cascade; no additional Python dependencies
- Displayed on stream cards with momentary, short-term, and integrated LUFS values

### Alert Escalation
- **Escalation alerts** — re-notify via all configured channels (email, webhook, Pushover) if an alert remains unacknowledged after a configurable number of minutes (per stream); 0 = off
- Escalation uses the same cooldown deduplication as standard alerts

### Stream Comparator
- **Pre/post processing comparison** — pair any two streams (e.g. studio feed vs. air monitor) and SignalScope will cross-correlate them to measure processing delay
- **Processor failure detection** — alerts (CMP_ALERT) when the post-processing stream goes silent while the pre-processing stream has audio
- **Gain drift detection** — alerts when the level difference between pre and post streams exceeds a threshold, indicating compressor or AGC issues
- **Dropout discrimination** — distinguishes single-path RTP loss from full processing chain failure
- Comparator status and delay shown on the dashboard

### In-App Self-Update
- **Apply Update & Restart** button in the Maintenance panel checks GitHub for a newer version and, on confirmation, downloads the new `signalscope.py`, validates it with `py_compile`, replaces the running file, and sends SIGTERM — systemd/watchdog handles the restart automatically
- No `sudo` required; only the app's own Python file is replaced

### PTP Configurable Thresholds
- PTP offset and jitter alert/warn thresholds are now configurable in the Settings UI (in µs) rather than being compile-time constants
- Defaults remain 5 ms warn / 50 ms alert for offset and 2 ms / 10 ms for jitter — appropriate for NTP-synced passive observers
- Guidance note in the settings explains how to tighten thresholds for a true PTP-slaved system

### Installer: Raspberry Pi Overclock
- Installer detects Raspberry Pi 3 and 4 and offers optional overclock settings at install/update time
- Pi 3: arm_freq=1450 MHz, over_voltage=2, gpu_freq=500
- Pi 4: arm_freq=2000 MHz, over_voltage=6, gpu_freq=750
- Pi 5 is detected and excluded (overclock not supported via this method on Pi 5)
- Settings are written idempotently to `/boot/firmware/config.txt` or `/boot/config.txt`

### Installer: Nginx Repair Flow
- On update runs where an existing nginx config is detected, the installer now checks config validity (`nginx -t`) and certificate presence
- **Broken config** (test fails or cert missing): warns the user and prompts to remove and start the nginx setup from scratch, pre-filling the previous FQDN
- **Healthy config**: shows the current FQDN and asks if the user wants to reconfigure
- Previously the installer silently skipped nginx on all update runs, with no way to fix a failed Let's Encrypt setup without manually removing files

---

## [2.6.52–2.6.55] — Hub Reports, Backup & CSRF Fixes

### Hub Reports
- **Alert clip download** — each clip row on the hub reports page now has a ⬇ download button alongside the audio player, allowing engineers to save alert WAV files directly from the hub

### Settings
- **Backup & Export** — new panel at the bottom of Settings page; one click downloads a timestamped ZIP containing `lwai_config.json` and all trained AI model files (`ai_models/`), making migration and backup straightforward

### CSRF fixes (all templates)
- **Universal CSRF meta tag** — `<meta name="csrf-token">` added to every template that was missing it (`SETTINGS_TPL`, `REPORTS_TPL`, `INPUT_LIST_TPL`, `INPUT_FORM_TPL`, `HUB_REPORTS_TPL`); eliminates CSRF validation failures on DAB bulk-add, settings test-notify, and hub alert acknowledgement

---

## [2.6.51] — Security Hardening, Hub Command Delivery & DAB Improvements

### Security Hardening
- **Path traversal fix** — `clips_delete` now validates stream name and filename against the snippet directory boundary using `os.path.abspath` checks, matching the existing `clips_serve` pattern
- **DAB channel whitelist** — `/api/dab/test` now validates the channel parameter against an explicit allowlist of valid DAB channels; PPM offset is validated as a signed integer within ±1000
- **SDR scan authentication** — `/api/sdr/scan` now requires a valid login session
- **Flask secret key hardening** — secret key file is created with `0o600` permissions; `Content-Disposition` filenames are sanitised before being sent in headers

### Hub Improvements
- **Remote start/stop control** — hub operators can now start or stop monitoring on any client node directly from the hub dashboard; commands are delivered securely via the heartbeat ACK
- **Reliable command delivery** — hub-controlled fields (`relay_bitrate`, pending commands) are now explicitly preserved across heartbeat updates so queued commands are never silently dropped
- **Hub replica cards fixed** — `get_site()` now computes `online`, `age_s`, `health_pct`, and `latency_ms` dynamically, matching `get_sites()`; replica page cards now populate correctly
- **CSRF fixed across all hub templates** — CSRF token is written to a `csrf_token` cookie via an `after_request` hook; all hub JavaScript now reads the token from the cookie first, eliminating template-specific meta-tag misses

### DAB Improvements
- **Shared mux stability on Pi 4** — `welle-cli` processes are now started with elevated scheduling priority (`nice -10`) to reduce CPU contention when running 4+ DAB services simultaneously on ARM hardware

---

## [2.6.41–2.6.50] — Hub Dashboard Reliability, DAB Fixes & RDS Improvements

### Hub Dashboard
- **Live card updates working** — fixed a silent JavaScript error (`lastAlertState` undefined) that was preventing all AJAX DOM updates on the hub page
- **Cache-busting on `/hub/data`** — added `Cache-Control: no-store` headers and `?_=timestamp` fetch parameters to prevent NGINX/browser caching stale data
- **Reliable polling loop** — switched from `setInterval` to recursive `setTimeout` via `.finally()` to prevent timer stacking on slow connections
- **Instant refresh on tab focus** — Page Visibility API handler fires `hubRefresh` immediately when switching back to the hub tab
- **Reload-loop guard** — prevents "new site appeared" reloads from triggering more than once every 30 seconds
- **Start/Stop buttons** — remote monitoring control buttons use `data-` attributes and event delegation to avoid HTML injection issues with site names containing spaces

### DAB Improvements
- **Bulk-add service fix** — service names were being URL-encoded in JavaScript but not decoded in `_run_dab`; fixed with `urllib.parse.unquote()`
- **DAB add form UX** — name field and rule-based alert settings are hidden when DAB source type is selected
- **DAB station list styling** — service rows now match the app's blue theme
- **DLS text parsing** — `welle-cli` returns `dynamicLabel` as a JSON object; fixed to extract the `label` key
- **DLS display** — DLS text on hub cards uses the same scrolling marquee as RDS RadioText

### RDS / Metadata
- **RDS RadioText scrolling restored** — hub cards check `fm_rds_rt || dab_dls` in both template and AJAX refresh loop
- **DLS shown for DAB on hub cards** — `sc-rt-row` classes added to DAB DLS rows for live AJAX updates

### Monitoring
- **Clip threshold default** changed from `-3.0 dBFS` to `-1.0 dBFS` for more accurate clipping detection

### Hub Audio
- **Alert audio playback behind reverse proxy** — relay client sends an empty EOF chunk after WAV delivery so the hub closes the relay slot immediately rather than waiting for proxy timeout

---

## [2.6] — Dashboard Redesign, Hub Improvements & Monitoring Reliability

### UI Improvements
- Moveable dashboard cards
- Improved layout and spacing
- Cleaner hub dashboard
- Improved top navigation and logo rendering

### Hub Improvements
- **Hub-only mode** removes the local dashboard
- Ability to remove dead clients
- Improved client visibility
- More metadata displayed on hub cards

### Metadata Enhancements
- Improved **RDS handling**
- Proper **RDS name locking**
- **RDS RadioText display**
- Improved DAB metadata support

### Monitoring Improvements
- Improved monitor reliability
- Better SDR restart handling
- Improved audio stream stability

### Stability Fixes
- Fixed setup wizard authentication bug
- Improved session handling
- Better fresh-install startup reliability
