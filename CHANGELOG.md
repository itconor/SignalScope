# SignalScope Changelog

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
