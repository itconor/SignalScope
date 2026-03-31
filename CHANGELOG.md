# SignalScope Changelog

---

## [3.4.110] - 2026-03-31

### Fixed
- **FM inputs fail to start after monitor restart — "Dongle already in use"** — `AppMonitor.stop_monitoring()` joins each stream thread with a 2 s timeout. If the `rtl_fm` subprocess (or `welle-cli`) hasn't fully terminated within that window, the FM thread's `finally: lease.__exit__()` call hasn't run yet, leaving the dongle serial registered in `sdr_manager._owners`. On the next monitor start every FM stream immediately sees its assigned dongle as "already in use by FM:..." (its own previous claim) and fails to connect. Added `SdrDeviceManager.release_all()` which clears `_owners` and `_dab_owners` under the registry lock, and called it unconditionally in `stop_monitoring()` immediately after the thread-join loop. Since all stop flags have already been set and all joins have been waited on, this is safe — any thread still running will attempt to `release()` again when it eventually finishes, which is a no-op on an already-empty dict.

---

## [3.4.109] - 2026-03-31

### Fixed
- **Producer View — no audio for remote-node chain faults (presenter.py v1.3.6)** — the original `CHAIN_FAULT` alert log entry has `clip: ""` when the faulty node is on a client site rather than the hub (the clip is saved asynchronously via a `save_clip` command). The uploaded clip creates a second alert log entry with the same event `id` but `stream = f"{site} / {stream}"` — which may not match the `allowed_chains` filter used by producer users when the chain label includes an equipment suffix (e.g. `"NI DAB / Downtown Radio - MIXXA01"` vs `"NI DAB / Downtown Radio"`). Result: the filtered incident event list had no clip URL and no play button appeared.

  Fix: `_build_clip_index()` reads the **full** alert log (bypassing `allowed_chains`) and builds a dict of `event_id → clip URL`. `_build_incidents()` now calls this once per request and uses it as a fallback: if none of the filtered incident events have a clip URL, it looks up each event's `id` in the index and uses the first match found. This finds uploaded clips regardless of whether the stream name in the uploaded entry exactly matches the chain label used in the `allowed_chains` filter.

---

## [3.4.108] - 2026-03-31

### Changed
- **Meter Wall plugin v1.1.0 — real-time bar animation** — the Meter Wall was polling `/api/meterwall/data` (heartbeat data, updated every ~10 s) once per second, so bar movement was visually sluggish. Added a secondary fast-poll of `/api/hub/live_levels` at 150 ms (5 Hz) that updates only the bar height, peak-hold marker, and dB text on each card. Metadata (now playing, LUFS-I, AI status, RTP loss) continues to come from the 1 s metadata poll. When the live poll is active (`_liveActive = true`), `updateCard` skips bar/peak/level writes to prevent the slower metadata cycle from flickering stale heartbeat values over the live data. The card key scheme (`site|stream`) matches directly between the two data sources so no mapping layer is needed.

---

## [3.4.107] - 2026-03-31

### Fixed
- **Chain fault clips too short (e.g. 6 s when set to 30 s)** — onset clips in `_fire_chain_fault` were calling `_save_alert_wav` with no `_chunks` argument, causing it to read from `_audio_buffer`. That buffer is sized to the stream's `alert_wav_duration` (default 5 s), so clips were always limited to ~5–6 s regardless of the chain's `clip_seconds` setting. Fixed by passing `_chunks=list(_lc._stream_buffer)` (the 60 s rolling buffer) as the audio source — the same approach used by silence onset clips. `STREAM_BUFFER_SECONDS` raised from 20 s to 60 s so clips configured up to 60 s get their full duration.

- **Producer (presenter) plugin — fault clips showing no audio** — the `CHAIN_FAULT` alert log entry stores `stream = chain_label` (the chain name) rather than the actual stream name. The clip URL built by the presenter used the chain name as the folder, but local hub clips are saved under `alert_snippets/{safe_stream_name}/` — no site prefix, stream name only. `hub_proxy_alert_clip` constructed `_safe_key = "_hub__<chain_name>"` which never matched the real folder. Fixed by adding a fallback scan of all `alert_snippets/` subdirectories when the initial key lookup returns no data for the `(hub)` pseudo-site — the clip is found by filename regardless of which subfolder it lives in.

---

## [3.4.106] - 2026-03-31

### Fixed
- **DAB — libmpg123 Layer I decode errors flooding the log** — `[src/libmpg123/layer1.c:check_balloc():30] error: Illegal bit allocation value` and `Aborting layer I decoding after step one` are per-frame MPEG Layer I decode errors produced by welle-cli's internal mpg123 decoder under marginal signal conditions. They are not fatal — welle-cli recovers on the next good frame — but they match the `"error"` filter in the welle-cli stderr reader and were appearing in the log at high frequency. Added `layer1.c`, `illegal bit allocation`, `aborting layer i decoding`, and `int123_do_layer1` to the existing noise-suppression list (same treatment as `SyncOnPhase failed` etc.). The log now stays clean; welle-cli continues normal operation.

### Notes
- **Codec plugin 404 on hub** — if `[Codec] Push to hub failed: HTTP Error 404: NOT FOUND` appears on a client node, the Codec Monitor plugin is not installed on the hub. Install it on the hub via **Settings → Plugins → Check GitHub for plugins**. The plugin must be present on both the hub and client nodes for cross-site codec status aggregation to work.

---

## [3.4.105] - 2026-03-31

### Fixed
- **Hub live meters zero for ~60 s after restart** — on restart the monitoring loop's per-stream `_last_level_dbfs` starts at the field default (`-120.0`). The `_live_loop` was sending that default immediately, and the hub's live-push merger stored it over the valid levels that had been restored from `hub_state.json`, dropping all bars to 0 %. Added `_has_real_level` flag to `InputConfig` (default `False`). `analyse_chunk` sets it `True` on the first real measurement; the monitor-stop reset path clears it alongside `_last_level_dbfs`. `_live_loop` now sends `null` for streams where `_has_real_level` is still `False` — the hub merger ignores `null`, so restored state is preserved until the monitoring loop produces its first real level, which is typically within one or two seconds of stream connect.

---

## [3.4.101] - 2026-03-31

### Fixed
- **Hub live level meters — wrong data source** — `/api/hub/live_levels` was reading from `hub_live_fanout._live_state` (a separate fan-out cache written only by SSE push frames) instead of `hub_server._sites` (the dict that the broadcast chain engine reads from, updated by both 10 s heartbeats and the 5 Hz live-push Option B merge). Changed to read directly from `hub_server._sites` under its own lock — the exact same source that makes chain fault detection real-time.

---

## [3.4.100] - 2026-03-31

### Fixed
- **Hub dashboard live meters never updated — root cause** — `_live_loop` on the client was gated behind `cfg.hub.live_view`. If that setting wasn't explicitly enabled on the client machine, no live frames were ever pushed regardless of what was configured on the hub. The gate has been removed: clients now always push live metric frames at 5 Hz whenever a `hub_url` is configured. Low-bandwidth mode clients remain exempt. At ~200 bytes × 5 pushes/s this is under 1 KB/s — trivial for any normal connection.

---

## [3.4.99] - 2026-03-31

### Fixed
- **Replica page solid green (regression from 3.4.97)** — the replica page CSS changed `.lbar-track` to `position:absolute;inset:0` but the replica page HTML had no `.lbar-outer` positioned parent. The absolutely positioned track expanded to fill the whole page. Fixed by wrapping the replica page level bar in `.lbar-outer` (same structure as hub dashboard).
- **Hub dashboard live levels not updating** — polling only started when the hub's own `live_view` setting was `True`. This is the wrong gate: the HUB machine doesn't need its own setting; it just needs client sites to be pushing. Live polling now always starts on page load. If no sites are pushing, the poll returns empty JSON and nothing animates (no harm done).
- **⚡ Live pill** — removed the `{% if live_view %}` server-side condition. The pill is always in the DOM but hidden; JS shows it the moment live data arrives and hides it again if no data is received for 5 s (client disconnected).

---

## [3.4.98] - 2026-03-31

### Fixed
- **Hub dashboard level bar missing after 3.4.97** — the new `.lbar-outer` wrapper div was added to the hub dashboard stream card HTML but the corresponding CSS was only added to the hub replica page stylesheet (a separate template). The hub dashboard has its own `<style>` block; `.lbar-outer` and `.lbar-peak` CSS rules are now correctly added there too.
- **Raw float values written back by `hubRefresh`** — every 5–15 s, `hubRefresh` called `lval.textContent = lev + ' dB'` where `lev` is a raw float from the JSON API, overwriting the nicely rounded live-poll value with e.g. `-14.41819217610082 dB`. Fixed to `lev.toFixed(1)`.

---

## [3.4.97] - 2026-03-31

### Changed
- **Live View — replaced SSE with polling for reliable real-time meters** — SSE connections can be buffered or dropped by nginx proxies. The browser now polls a new `GET /api/hub/live_levels` endpoint every 150 ms instead, which is a plain HTTP GET and works through any proxy configuration.
- **Live View — push rate increased from 1 Hz to 5 Hz** — client `_live_loop` now pushes every 0.2 s. `HUB_LIVE_RATE_LIMIT_RPM` raised to 600 to match.
- **Hub dashboard — PPM-style level meters** — level bar now has proper broadcast-style behaviour: instant attack (bar snaps up immediately), slow exponential decay (0.6 s ease-out when falling), 2-second peak-hold white marker that decays after hold expires. Track background shows colour zones (normal / warning −20 dBFS / clip −9 dBFS) for at-a-glance headroom reading.

---

## [3.4.96] - 2026-03-31

### Fixed
- **Live View level display showing excessive decimal places** — `_applyLiveFrame` was setting the level value span to the raw float (e.g. `-13.9211857 dB`). Fixed: JS now uses `.toFixed(1)` so values display as e.g. `-13.9 dB`. The static heartbeat render in the hub template was also unrounded (`{{lev}} dB`); fixed to `{{lev|round(1)}} dB`.
- **Live View indicator hard to notice** — `⚡ Live` pill in summary bar was dim grey when not flashing. Now always green; flashes white→green on each received frame. Level value spans also flash briefly bright on update, making 1 Hz updates visually obvious.

---

## [3.4.95] - 2026-03-31

### Fixed
- **Hub dashboard 500 when Live View is enabled** — `_live_loop` used `_level_dbfs` / `_peak_dbfs` attribute names, but `InputConfig` stores them as `_last_level_dbfs` / `_last_peak_dbfs`. The `getattr` fallback returned `None`, which was merged into `hub_server._sites` via the live push path and then caused `TypeError: unsupported operand type(s) for +: 'NoneType' and 'int'` in the hub template level-bar calculation `(lev + 80)`. Fixed: `_live_loop` now uses the correct attribute names matching `_build_payload`. Additionally, the live push merger now guards against `None` values so a future attribute mismatch can never overwrite valid heartbeat data.

### Added
- **Hub dashboard — level bars update at 1 Hz when Live View is active** — hub site card level bar fills and dB values now update in real time via `_applyLiveFrame`. ID attributes (`lvl_*`, `lvlv_*`) added to the level bar elements in the hub template using the same key scheme as the JS updater.
- **Hub dashboard — ⚡ Live indicator** — when Live View is enabled, a `⚡ Live` badge appears in the hub summary bar and flashes green briefly each time a live frame is received, confirming that data is flowing.

---

## [3.4.94] - 2026-03-31

### Fixed
- **Hub dashboard — all cards pulsing, buttons unresponsive, update button missing (regression since 3.4.90)** — the live-view IIFE added in 3.4.90 ran at script-parse time while the `<script>` block is still inside `<head>`. At that point `document.body` is `null`; accessing `.getAttribute(...)` on it threw a `TypeError` that aborted execution of the rest of the script block — meaning: DOMContentLoaded listener never registered, `hubRefresh()` never called, skeleton class never removed, no button event listeners attached. Fixed by moving the `data-live-view` attribute read inside a `window.addEventListener('load', ...)` handler where `document.body` is always available.

---

## [3.4.93] - 2026-03-31

### Added
- **Hub replica page — Live View toggle** — admins can now enable or disable the 1 Hz live metric push on any remote client directly from that client's replica page, without needing to log into the client machine. The button shows the current state (⚡ Live View: ON / 💤 Live View: OFF) reported by the client in its heartbeat. Clicking it queues a `set_live_view` command delivered on the next heartbeat cycle (~5 s). The client persists the change to its local config so it survives restarts. Toggle is admin-only and only shown when the site is online.
- **`set_live_view` hub command** — new client command type processed by `HubClient._cmd_set_live_view`. Updates `cfg.hub.live_view` and saves config. The `_live_loop` picks up the change within 5 s.
- **`live_view` field in client heartbeat** — clients now report their current `live_view` setting in every heartbeat payload, so the hub can display the correct toggle state on the replica page.

---

## [3.4.92] - 2026-03-31

### Fixed
- **Hub dashboard — update button slow to appear after hub restart** — when Live View SSE was enabled, the `onopen` handler delayed `hubRefresh` by 30 seconds, meaning the ⬆ Update button didn't appear until 30 s after sites came back online post-restart. Fixed: SSE `onopen` now triggers a structural refresh after 1.5 s. Subsequent structural polls use 15 s when live view is active (down from the accidental 5 s after the first 30 s fire) — still materially less load than the 5 s default, but fast enough that online-state and version-badge changes appear promptly.
- **Producer View — station cards stuck pulsing after hub restart** — if the first `loadChainStatus` fetch failed (hub briefly unavailable during restart), the `.catch` handler only updated the refresh dot label and left the skeleton placeholder divs pulsing indefinitely (next retry in 15 s). Fixed: on catch, skeleton containers are immediately replaced with a plain "retrying in 15 s…" message so the user sees a human-readable state rather than stuck loading animation.

### Plugin: Producer View 1.3.5
- Skeleton cards cleared on API error (see above).

---

## [3.4.91] - 2026-03-31

### Changed
- **Broadcast chains now evaluate against sub-second live data** — when live view mode is enabled, the hub previously stored live metric frames only in `HubLiveFanout` (used for SSE delivery to browsers), leaving `api_chains_status` to read from `hub_server._sites` which is only updated by the 5 s heartbeat. `POST /api/v1/live_push` now also merges the slim frame's stream-level fields (`level_dbfs`, `peak_dbfs`, `silence_active`, `ai_status`, `lufs_m`, `lufs_s`) directly into `hub_server._sites[site]["streams"]` under the write lock on every 1 Hz push. Chain fault evaluation in `api_chains_status` now reacts to silence, AI status changes, and level drops within ~1 second rather than waiting up to 10 s. All other fields in `_sites` (history, comparators, system info, `_approved`, etc.) are untouched.

---

## [3.4.90] - 2026-03-31

### Added
- **High-bandwidth live view mode** — new opt-in hub setting that pushes slim metric frames (level_dbfs, peak, LUFS, silence, AI status) from each client to the hub at 1 Hz. The hub fans these out to authenticated browsers via SSE (`GET /hub/stream/events`), enabling sub-second level bar and status updates without waiting for the 5s heartbeat + 5s browser poll cycle. The existing heartbeat is unchanged — it continues to carry full payloads, commands, and ACKs. Live view automatically falls back to normal 5s polling if the SSE connection fails. Disabled automatically when `low_bw` mode is active for a site.
- **`HubLiveFanout`** — new thread-safe fan-out class using `threading.Condition` per site; negligible memory footprint.
- **`HubClient._live_loop()`** — lightweight background thread pushing ~200-byte JSON frames at 1 Hz, using the same HMAC signing as the heartbeat.
- **`POST /api/v1/live_push`** — new hub route with a separate 180 RPM rate limiter (vs 60 RPM for heartbeat). Signature verification and nonce replay protection identical to heartbeat.
- **`GET /hub/stream/events`** — SSE endpoint with `X-Accel-Buffering: no` for nginx compatibility. Respects per-user site access controls. Falls back to keepalive comments every ~5s when no updates arrive.

---

## [3.4.89] - 2026-03-31

### Fixed
- **User permission changes now take effect immediately** — site, chain, and plugin access lists were only written to the session at login time, so editing a user's permissions required them to log out and back in before anything changed. `login_required` now refreshes these three fields from the live user account on every authenticated request. The legacy admin account (not in the user manager) is unaffected.

---

## [3.4.88] - 2026-03-31

### Fixed
- **Settings → Users — "tick to restrict" labels were backwards** — Site Access, Chain Access, and Plugin Access fields all said "tick to restrict" but the actual behaviour is "tick to allow" (checked = permitted, unchecked = not permitted; none checked = all permitted). Labels now correctly read "tick to allow; none ticked = all allowed". Users who were set up under the old labelling will have their permissions reversed from what was intended — re-check their settings.

### Changed
- **Producer View station cards now driven by broadcast chains** — "Your Stations" section no longer reads from `/hub/data` (all streams, all sites). It now reads from `/api/chains/status` which already filters to the user's allowed chains. Each card represents one broadcast chain. Site shown is the last (RX) node's site. Removes the `/hub/data` fetch from Producer View entirely.
- **Listener stream filtering by chain nodes** — when a user has site or chain access restrictions, the Listener now fetches `/api/chains/status` alongside `/hub/data` and filters to only streams that appear as nodes in the user's allowed chains. Users with no restrictions continue to see all streams as before.

---

## [Producer View 1.3.1] - 2026-03-31

### Added
- **Ticket system banner** — a persistent "Have a concern? Open a Ticket" banner is shown on every Producer View page when a ticket URL is configured. Tapping the button opens the URL in a new tab. Admins see a URL input field below the banners to set or clear the URL; it is saved to `presenter_config.json` and takes effect on next page load. Leaving the URL blank hides the banner entirely.

---

## [Producer View 1.3.0] - 2026-03-31

### Changed
- **Pending/adbreak states no longer shown as faults** — the status hero and station cards previously flagged chains in `pending` or `adbreak` (the countdown confirmation window) as "Signal Issue". Producer View now only raises an alert when a chain is in full `fault` state. During the confirmation window the chain shows as "On Air" — producers are only notified once the engineer-configured fault delay has expired and a real fault is confirmed.

---

## [Producer View 1.2.9] - 2026-03-31

### Changed
- **Station cards driven by chain status** — replaced stream-based cards (from `/hub/data`) with chain-based cards (from `/api/chains/status`). Shows only chains the logged-in user has access to. Each card is one broadcast chain; status (On Air / Signal Issue / Checking) reflects `display_status`. Site shown is taken from the last leaf node in the chain.

---

## [Listener 1.1.3] - 2026-03-31

### Fixed
- **Stream list filtered to allowed chains when user has restrictions** — users with site or chain access restrictions now see only the streams that are nodes in their permitted chains. Users with full access (no restrictions) continue to see everything.

---

## [3.4.87] - 2026-03-31

### Added
- **Restart button on Plugins page** — a **↺ Restart SignalScope** button now appears at the bottom of Settings → Plugins. Calls `/api/admin/restart`, disables itself with a "Restarting…" label, and reloads the page after 8 seconds. Removes the need to go to Process Controls just to restart after installing or removing a plugin.

---

## [Listener 1.1.2] - 2026-03-31

### Added
- **Producer View button in header** — when the Producer View plugin is installed, a **🎙 Producer View** button appears in the Listener header (matching the style of the Listen Live button in Producer View). Only shown when the `/presenter` route exists.

---

## [Producer View 1.2.8] - 2026-03-31

### Changed
- **Clip buttons now labelled by type** — clips on fault cards show **▶ Fault clip** and clips on recovery cards show **▶ Recovery clip**, so producers can immediately see whether they're about to hear audio going off-air or audio coming back. The label is restored when the clip is stopped.

---

## [Producer View 1.2.7] - 2026-03-31

### Fixed
- **Station name extraction now strips distribution path** — chain names like `"Northern Ireland DAB / Downtown Radio"` and `"London - Livewire / Downtown Radio"` now both resolve to `"Downtown Radio"` (the part after the last ` / `). Previously only the equipment serial suffix was stripped, so the full chain name was shown verbatim, giving each distribution path a separate line in the fault detail.
- **Infrastructure/feeder chains suppressed from incident labels** — when a site-level feeder chain (e.g. `"London"`, no ` / ` in its name) faults at the same time as downstream station chains (e.g. `"London - Livewire / Downtown Radio"`), the feeder is hidden from the incident label. The producer sees only the station brand that went off air, not the cascade of infrastructure nodes that triggered with it.
- Combined effect: a fault that previously showed "London and 2 other stations" → "London / Northern Ireland DAB / Downtown Radio / London - Livewire / Downtown Radio" now correctly shows "Downtown Radio has a signal issue".

---

## [Producer View 1.2.6] - 2026-03-31

### Fixed
- **Status hero now checks chain status, not stream status** — the "All stations are on air / signal issue" block at the top of the Producer View now fetches `/api/chains/status` directly instead of reading stream-level data from `/hub/data`. It only shows chains the logged-in user has permission to see (filtered by their `allowed_chains` assignment). Previously it read all streams from all sites on the hub regardless of the user's chain permissions, causing it to show unrelated alerts.
- **Hero shows faulted chain name(s) directly** — when a chain is in fault, pending, or adbreak state the headline names the specific chain(s) affected. Single fault: "CoolFM has a signal issue". Two faults: "CoolFM and Downtown Radio have a signal issue". More: "CoolFM and N other chains".

---

## [Producer View 1.2.3] - 2026-03-31

### Changed
- **Equipment names stripped from headlines** — chain names like "London - Livewire / Downtown Radio - LONCTAXMQ05" now display as "London - Livewire / Downtown Radio". Equipment suffixes (codec serials, processor names, etc.) are removed before showing to producers. Recognises all-caps serials and keywords like Processor, Primary, Secondary, Backup, Encoder, Codec, STL, DAB, FM, TX, Mux, Transmitter, Receiver, etc.
- **De-duplicated station list** — when multiple chains for the same station fault at once, the "affected stations" list shows each unique station name once, not every chain. The "(N chains affected)" count is gone from headlines.
- **Expand only shown when genuinely needed** — "▾ Show affected stations" only appears when a single incident genuinely spans more than one distinct station after de-duplication.

---

## [Producer View 1.2.1] - 2026-03-30

### Changed
- **Language** — "signal chains" renamed to "stations" throughout the producer view. Section heading, greeting subtitle, all-clear banner, expand button, and fault/recovery event text all say "station" instead of "chain". Producers don't need to know what a signal chain is.

---

## [Producer View 1.2.0] - 2026-03-30

### Changed
- **Incident grouping** — fault events that occur within a 2-minute window are now collapsed into a single card. When an outage hits multiple chains simultaneously (e.g. 3 encoders + a DAB chain all failing at 4:28 PM), the producer sees one entry — "Station fault — London - Livewire / Downtown Radio (3 stations affected)" — instead of a wall of individual chain entries. Recoveries are grouped the same way.
- **Common-prefix naming** — the grouped entry label is the longest common prefix of all affected chain names, so "London - Livewire / Downtown Radio - LONCTAXZC03, LONCTAXMQ05, Quant C2" becomes "London - Livewire / Downtown Radio (3 stations affected)".
- **Expandable detail** — producers can tap "▾ Show affected stations" to see the full list of individual chain names within an incident group.
- **One clip per incident** — only the first available clip in a group is shown; no more multiple play buttons for the same outage.

---

## [3.4.86] - 2026-03-31

### Fixed
- **Edit User button submits the page** — Edit and Delete buttons in the Users table are generated dynamically via JS and were missing `type="button"`. Inside a `<form>`, a `<button>` without an explicit type defaults to `type="submit"`, so every click reloaded the page instead of opening the edit form. Fixed by adding `type="button"` to both buttons.

### Changed
- **Security tab renamed to "Users & Roles"** — the Settings sidebar button now reads `👥 Users & Roles` to match the section heading that was already inside the panel.
- **Web UI Authentication section cleaned up** — the old single-user username/password form fields have been removed from Settings. User credentials are now managed exclusively through Settings → Users & Roles. The "Require login" checkbox and the lockout/session settings remain. The orphaned `chkPwMatch()` JS validation function (which referenced the removed inputs) has also been removed.

---

## [3.4.85] - 2026-03-30

### Added
- **Chain access control per user** — `UserAccount` gains a `chains` whitelist field alongside `sites` and `plugins`. Assign specific chains to a user in Settings → Users; empty list = all chains permitted. On login, `allowed_chains` is stored in the session. `broadcast_chains()` and `api_chains_status()` both filter chain lists by `allowed_chains` for non-admin users, so operators only see the chains they're responsible for.
- **`/api/hub/chain_names` endpoint** — returns the sorted list of configured chain names for the Settings Users form chain-access checkboxes.
- **Chain column in Users table** — Settings → Users table now shows each user's chain whitelist (or "All" if unrestricted).
- **Chain access checkboxes in user form** — tick individual chains to restrict a user; none ticked = all chains.

---

## [Producer View 1.1.0] - 2026-03-30

### Changed
- **Renamed** — plugin label is now "Producer View"; role label is "Producer".
- **Chain-only events** — fault history now shows only `CHAIN_FAULT`, `CHAIN_RECOVERED`, and `CHAIN_FLAPPING` events. Silence, RTP, STL, and other stream-level alerts are filtered out — producers see only chain-level signal faults.
- **Deduplication** — if the same chain fires the same fault type multiple times within a 5-minute window, only the most recent occurrence is shown. Eliminates the repeated-fault noise visible in the previous version.
- **Chain filtering** — events are filtered by the user's `allowed_chains` permission (set in Settings → Users). If the user has no chain restrictions, all chains are shown.
- **Clip play buttons** — events that have an associated audio clip now show a "▶ Play clip" button. Tap to play inline; tap again to stop. Shows elapsed/total time while playing.
- **All-clear copy** — updated to "All signal chains are running normally" to match the chain-only scope.

---

## [Presenter 1.0.0] - 2026-03-30

### Added
- **Presenter plugin** — simplified hub view for presenters and producers. Shows station status cards (On Air / Signal Issue / Not Available) with live level indicators, and a plain-English fault/recovery history drawn from the alert log. Site-filtered: presenters only see stations assigned to their user account. All-clear banner when no active faults. Auto-refreshes every 30 seconds.
- **Plugin-role integration** — declaring `user_role=True` + `role_label="Presenter"` in `SIGNALSCOPE_PLUGIN` causes the role to appear in Settings → Users dropdown (requires SignalScope 3.4.84+). Users assigned the Presenter role are forwarded directly to `/presenter` on login and cannot access the main hub.
- **Plain-English fault labels** — technical event types (CHAIN_FAULT, SILENCE, STL_FAULT, TX_DOWN, etc.) are translated to plain language: "Signal chain fault", "audio silence", "STL link fault", "transmitter fault", etc.

---

## [Listener 1.1.1] - 2026-03-30

### Fixed
- **Station names truncating** — card name was single-line with ellipsis; changed to `-webkit-line-clamp:2` so full names like "CoolFM - LONCTAXMQ05" wrap to two lines instead of being cut off.

### Added
- **Connecting state feedback** — tapping Listen immediately shows "⏳ Connecting…" with a pulsing blue button and animated border ring so presenters know something is happening. At 10 s a hint appears below the button: "Starting up… tap Stop if you want to cancel". At 10 s the button changes to "⏳ Still loading…" and the hint updates to "This can take up to 30 seconds — tap Stop to cancel". The button remains tappable throughout so presenters can cancel.

### Changed
- **Visual polish** — richer card hover effect (subtle blue glow overlay), avatar scales and tilts slightly on hover, now-playing bar has a green glowing top border, listen/stop buttons have improved depth and active-press feedback.

---

## [Listener 1.1.0] - 2026-03-30

### Added
- **⭐ My Stations (favourites)** — tap the star on any card to pin it to a permanent "My Stations" row at the top of the page, persisted to localStorage. Survives page reloads and browser restarts.
- **Search bar** — instant text filter; type any part of a station name to narrow the list. "No stations match" message shown if search finds nothing.
- **Resume last station** — on page load, a banner offers to resume whichever station was playing when the tab was last closed. Dismiss with "Not now" to clear the suggestion.

### Changed
- **Simplified status language** — "● On Air" / "⚠ Signal Issue" / "⚡ Caution" / "○ Not Available" replacing technical badge text.
- **Larger touch targets** — Listen/Stop button padding increased, font size 15 px, height ~52 px for easy tapping in studio environments.
- **Now-playing bar** shows live RDS/DAB "what's on" text alongside station name, updates every 10 s.
- **Badge updates in-place** — status badge text and colour update on each poll cycle without a full card re-render.

### Fixed
- **Streams not playing (regression from 1.0.1)** — `_client_idx` could be JSON `null`, which passed the `!== undefined` check and was stored as `null`; the audio URL became `/stream/null/live` (404). Changed to `!= null` which correctly catches both `null` and `undefined`.
- **Level / status / online state not updating** — `pollLevels` was still matching streams by sorted array position instead of `_client_idx`, so no stream ever matched and all poll updates were silently dropped. Fixed to use `_safeIdx(s, i)` matching `loadStreams`.
- **Click handler accumulation** — `renderContent` was re-attaching `_onCardClick` on every render without removing the old one. Content node is now replaced (cloneNode) on each render to cleanly strip listeners before re-attaching once.

---

## [Listener 1.0.1] - 2026-03-30

### Fixed
- **Wrong stream played when clicking a station card** — `/hub/data` re-sorts streams by alert priority before returning them, so the `forEach` array position (used as the stream index in the audio URL) no longer matched the actual config index. Fix: use `_client_idx` from the stream object (set by the client at heartbeat time, survives all hub-side sorting) instead of the loop counter.

---

## [3.4.84] - 2026-03-30

### Added
- **Plugin-role user accounts** — plugins can now declare themselves as a user role by adding `"user_role": True` and `"role_label": "Presenter"` to their `SIGNALSCOPE_PLUGIN` dict. When any such plugin is installed, its role appears automatically in the Role dropdown on the Settings → Users form (under a "── Plugin Roles ──" separator). Users assigned a plugin role are subject to full site-access filtering (same `allowed_sites` whitelist as viewer accounts). On login, plugin-role users are forwarded directly to the plugin's URL — they never see the hub dashboard or client view. Navigating to `/` or `/hub` while logged in as a plugin role also redirects immediately to the plugin. All write operations are blocked (same `_rbac_enforce_readonly` path as viewer role). New `/api/hub/plugin_roles` endpoint returns the list of role-capable plugins for Settings page JS.

---

## [3.4.83] - 2026-03-30

### Fixed
- **Hiss detection too sensitive — constant false alerts** — default `hiss_rise_db` raised from 12 dB to 20 dB, and `hiss_min_duration` raised from 3 s to 10 s. The old defaults fired on normal bright broadcast content (sibilance, cymbals, high-energy music). The new defaults require a sustained 20 dB HF spike lasting at least 10 seconds — a genuine fault condition. Existing inputs that have already been saved with the old values are unaffected; only inputs created from scratch use the new defaults.

---

## [3.4.82] - 2026-03-30

### Fixed
- **Edit/Delete chain buttons and ⚙ Sources panel still visible for non-admin users** — the API endpoints were protected by `@admin_required` (3.4.81) but the Broadcast Chains and Hub Dashboard templates still rendered the `✎ Edit`, `✕ Delete`, and `⚙ Sources` buttons plus the full Source Management panel for all authenticated users. Now passes `is_admin` to both templates and wraps those controls in `{% if is_admin %}` so non-admin users see a clean read-only view.

---

## [3.4.81] - 2026-03-30

### Fixed
- **Viewer and operator accounts could still edit broadcast chains and add/remove sources** — the following write endpoints only had `@login_required` with no role check, so non-admin users could reach them despite the `_rbac_enforce_readonly` before-request hook. Added `@admin_required` to all eight structural write routes: `POST /api/chains`, `DELETE /api/chains/<id>`, `POST /api/hub/site/.../input/add|remove|enable|disable`, `POST /inputs/add_dab_bulk`, `POST /inputs/<idx>/delete`.

---

## [Listener 1.0.0] - 2026-03-30

### New Plugin
- **Listener** — live stream monitoring page designed for presenters and producers. Station cards with coloured avatars, animated level meters, live/alert/offline badges, and a single-tap 🎧 Listen button. Auto-reconnects on stream interruption with retry counter. Animated equalizer + volume control in a slide-up now-playing bar. Screen wake-lock while listening. Streams are filtered by the logged-in user's site access permissions. Step-by-step help guide built in. Mobile-responsive.

---

## [3.4.80] - 2026-03-30

### Improved / Fixed
- **User form — site checkboxes now load correctly** — switched from `get_sites()` (which builds a heavyweight result dict) to directly reading `hub_server._sites.keys()` under the lock, which is guaranteed to contain all registered site names.
- **User form — plugin access is now checkboxes** — replaced the comma-separated plugin text input with a scrollable checkbox list of installed + active plugins (loaded from `/api/plugins`), matching the same UX as site access. Plugin icon and name shown on each checkbox.

---

## [3.4.79] - 2026-03-30

### Fixed
- **User form — site checkboxes always show "No sites registered"** — `api_hub_site_names` was looking for `s["site_name"]` but `get_sites()` stores the site name under `s["site"]`. Every site was silently filtered out.

---

## [3.4.78] - 2026-03-30

### Fixed
- **Bad gateway / 502 on all pages after 3.4.75 upgrade** — the CSP response header had grown to several KB due to ~60 SHA-256 hashes for every `onclick=` attribute across all templates. nginx's header buffer (`proxy_buffer_size`, default 4 KB) could not fit the header and returned 502 to browsers. Fixed by replacing `script-src-attr 'unsafe-hashes' [60+ hashes]` with `script-src-attr 'unsafe-inline'`. The nonce-protected `<script>` blocks remain the primary XSS defence. Removed the `_compute_csp_hashes()` startup function (no longer needed).

---

## [3.4.77] - 2026-03-30

### Fixed
- **Service fails to start after 3.4.75/3.4.76 update** — `@app.before_request` was placed on `_rbac_enforce_readonly` at module level before `app = Flask(__name__)` was defined (line 3371 vs 14367), causing an immediate `NameError: name 'app' is not defined` on startup. Fixed by removing the decorator from the function definition and registering it with `app.before_request(_rbac_enforce_readonly)` after the Flask app is created.

---

## [Logger 1.5.16] - 2026-03-30

### Fixed
- **Syntax error on load** — orphaned `except Exception: pass` fragment at line 385 (remnant from an earlier edit) caused a Python `unexpected indent` error, preventing logger.py from loading entirely. Removed the stray fragment; the surrounding `try/except/finally` block is now structurally correct.

---

## [3.4.76] - 2026-03-30

### Improved
- **User management — site access checkboxes** — the Site Access field in the add/edit user form is now a scrollable checkbox list showing all connected hub sites. Sites are loaded live from the hub on form open; pre-existing site restrictions are pre-ticked. Saving collects the ticked site names directly. "No sites ticked" still means unrestricted access to all sites. Requires hub mode (standalone nodes show "No sites registered yet").

---

## [3.4.75] - 2026-03-30

### Fixed
- **Updater not offering newer versions to users on 3.1.x** — the updater used the GitHub `/releases/latest` API endpoint which only returns non-pre-release releases. If any releases between the user's installed version and the current version were published as "pre-release" on GitHub, the API would silently return an older version as "latest stable" — causing users to be incorrectly told they are up to date. Fixed by switching to the `/releases?per_page=50` list endpoint which returns all releases including pre-releases, then selecting the highest semver tag found regardless of pre-release status.

---

## [Logger 1.5.15] - 2026-03-30

### Added / Fixed
- **Raw export (hub mode)** — new "Raw (fast)" format option. The client node streams raw segment files (no ffmpeg required on client), the hub pipes them through its own ffmpeg for precise mark-in/out trimming using stream-copy (no re-encoding on either side). Output is in the stream's native recording format (MP3/AAC/Opus). Cross-segment marks work correctly — all overlapping segments are included and trimmed to the exact mark range.
- **Fixed: zero-byte export files** — the relay download generator previously exited immediately when the client sent an EOF signal (`slot.closed = True`) before the generator had drained the queue. Changed to an unconditional loop that breaks only when the queue is empty AND the slot is closed.
- **Fixed: EOF not signalled** — `_hub_export_clip` now always sends an empty-body POST after finishing (raw or ffmpeg path), signalling EOF to the relay slot so the download generator exits immediately rather than waiting 30 seconds.

---

## [Logger 1.5.14] - 2026-03-30

### Improved
- **Export clip shows live progress** — the Export Clip button now cycles through clear phase labels: "⏳ Requesting…" → "⏳ Connecting…" → "⏳ Receiving… 1.2 MB" (live byte count) → "✅ Done!". A status line below the export bar mirrors the same state. Works in both direct and hub modes. The save dialog now appears only after all bytes have been received (Blob URL approach) so the browser never downloads an empty or partial file.

---

## [Logger 1.5.13] - 2026-03-30

### Fixed
- **Hub clip export still produced 0-byte files** — `ListenSlot.SLOT_TIMEOUT` is 30 seconds. The slot reaper evicts slots where `last_chunk` hasn't been updated within that window. The export download generator was waiting up to 60 s for the client to start pushing, but not touching `last_chunk` during that wait. After ~40 s the slot was removed from the registry; the client's next `audio_chunk` POST returned 404; `_audio_post` returned False; the client thread stopped. Fix: the generator now sets `slot.last_chunk = time.time()` on every `queue.Empty` timeout, keeping the slot alive in the registry for as long as the browser's download connection is open.

---

## [Logger 1.5.12] - 2026-03-30

### Fixed
- **Hub clip export produced 0-byte files** — `ListenSlot.get()` raises `queue.Empty` on timeout rather than returning `None`. The download generator caught this as a generic `Exception` and broke out of the loop immediately, before the client had time to receive the command and start ffmpeg. Fixed by catching `queue.Empty` explicitly and continuing to wait (up to 60 s for first byte, 30 s inactivity after that). Added `import queue` to the module-level imports.

---

## [Logger 1.5.11] - 2026-03-30

### Added
- **Clip export now works in hub mode** — the Export Clip button is no longer greyed out when a hub site is selected. In hub mode, the hub queues an `export_clip` command to the client node; the client runs ffmpeg locally against its own recordings and streams the encoded bytes back to the hub through the existing relay infrastructure; the hub serves them to the browser as a file download with the correct MIME type and filename. All three formats (MP3, AAC, Opus) and multi-segment exports work identically to direct-mode export.

---

## [Logger 1.5.10] - 2026-03-30

### Fixed
- **Clip export / playback broken when ffmpeg is not in PATH** — `shutil.which("ffmpeg")` returns `None` when SignalScope runs as a launchd service or the desktop app is launched from Finder/Dock (minimal PATH). All ffmpeg lookup calls now go through a new `_find_ffmpeg()` helper that additionally checks `/opt/homebrew/bin` (macOS Apple Silicon), `/usr/local/bin` (macOS Intel/Homebrew), `/opt/local/bin` (MacPorts), `/snap/bin` (Linux snap), and `C:\ffmpeg\bin` / `C:\Program Files\ffmpeg\bin` (Windows). The old `shutil.which("ffmpeg") or "ffmpeg"` fallback pattern would silently pass bare `"ffmpeg"` to subprocess, causing `[Errno 2] No such file or directory: 'ffmpeg'`.
- **Export endpoint now returns a human-readable error instead of an exception** — `_export_clip()` now checks for ffmpeg before building the command and catches `FileNotFoundError`, returning a 500 JSON with install instructions rather than an unhandled Python traceback.

---

## [SignalScope 3.4.73] - 2026-03-30

### Fixed
- **Silence clips no longer start mid-silence** — onset alert clips for silence faults now use a snapshot of `_stream_buffer` (20 s rolling buffer) instead of `_audio_buffer`. At the moment silence is confirmed, `_stream_buffer` contains ~17 s of pre-fault audio followed by the silence onset, so the clip starts with normal programme audio and you can hear the exact moment signal was lost.
- **Chain clips now have a consistent, configurable duration** — added a **Clip duration** field to the chain builder (Timing & Behaviour panel, default 0 = system default 10 s, max 300 s). Onset clips, recovery clips, and remote `save_clip` commands all use the chain-configured value. Previously the onset clip was always hard-coded to 10 s regardless of fault duration or chain preference. The auto-expand logic for the Timing panel now also triggers when clip duration is non-zero.

---

## [AzuraCast 1.0.0] - 2026-03-30

### Added
- **New plugin: AzuraCast integration** — polls AzuraCast web radio servers for live station data. Station cards show current track, artist, album art URL, live progress bar (updates every second client-side), next track, live/AutoDJ status, listener count, and linked SignalScope input. Fires `AZURACAST_FAULT` / `AZURACAST_RECOVERY` alerts on station online/offline transitions. Optionally fires `AZURACAST_SILENCE` when a station is broadcasting but its linked SignalScope input is in silence. Hub overview aggregates all stations across all connected sites. Supports multiple AzuraCast servers, optional Bearer API key auth. No extra pip packages required.

---

## [SignalScope 3.4.72] - 2026-03-30

### Changed
- **AzuraCast plugin added** to plugins.json registry — installable from Settings → Plugins.

---

## [SignalScope 3.4.71] - 2026-03-30

### Changed
- **Icecast plugin: rethemed to match app design system** — replaces all bespoke colours and button classes with the app's CSS variables (`--bg`, `--sur`, `--bor`, `--acc`, etc.), card structure (`.ch` header + `.cb` body), button classes (`.btn.bp`, `.btn.bd`, `.btn.bg`, `.btn.bs`), and badge classes (`.b-ok`, `.b-al`, `.b-mu`). Background, header gradient, tables, forms, and modal all match the rest of the app.
- **Icecast plugin: hub mode no longer shows local server controls** — on a pure hub node the `/icecast` route now redirects to `/hub/icecast` (the overview page). Hub operators see status and stream management for all connected client sites; they cannot accidentally start a local Icecast server on the hub machine.

---

## [SignalScope 3.4.70] - 2026-03-30

### Changed
- **Plugins moved to `plugins/` subdirectory** — all plugin `.py` files now live in a dedicated `plugins/` directory alongside `signalscope.py` instead of cluttering the app root. On first startup after upgrading from an older version, any plugin files found in the app root are automatically moved to `plugins/` (including their associated `.json` config files) — no manual action required. Install and remove routes updated to target `plugins/`.
- **Icecast plugin: universal input via `_stream_buffer` PCM tap** — replaced the old device-type-detection approach (which only worked for HTTP inputs) with the same snapshot+anchor `_stream_buffer` drain loop used by the Logger plugin. Every input type SignalScope monitors (FM/RTL-SDR, DAB, ALSA, RTP, HTTP) now works as an Icecast source. Stream management refactored from subprocesses to self-restarting `IcecastStreamThread` instances.
- **Icecast plugin: stereo support** — new per-stream `Stereo` toggle. HTTP inputs: native stereo preserved by passing the URL directly to ffmpeg. FM/DAB/ALSA/RTP inputs: mono PCM tap with optional `-ac 2` dual-mono upmix (L=R). Input dropdown shows `[URL — native stereo]` or `[PCM tap]` hint so the source capability is visible before configuring.

---

## [Logger 1.5.9] - 2026-03-29

### Fixed
- **Hub relay "no audio" / silence injection back on** — `kind="file"` was passed to `_listen_registry.create()` in 1.5.8, but `slot.kind` is not necessarily stored/accessible, so `getattr(slot, "kind", "scanner")` defaulted to `"scanner"` → `is_pcm = True` → silence injection re-enabled → OGG corruption back. Fix: reverted to `kind="scanner"` (stable, known to work); file relay slots are now tracked in a module-level `_file_relay_slots` set. The `relay_stream` generator checks `slot_id in _file_relay_slots` (not `slot.kind`) to determine file vs PCM mode. Set entry is removed in the generator's `finally` block.
- **Relay EOF too aggressive** — 5 s inactivity timeout after first data could fire during normal playback network jitter or during an ffmpeg seek startup delay. Increased to 15 s.
- **OGG copy-seek broken output** — `ffmpeg -ss X -c copy -f ogg pipe:1` for Opus sources emits `Could not update timestamps for skipped samples` and may produce output that QMediaPlayer rejects. Changed to `-f matroska` (MKV) which handles `-c copy` correctly for all codecs and produces a valid streamable container that Qt Multimedia's FFmpeg backend supports natively.

---

## [Logger 1.5.8] - 2026-03-29

### Fixed
- **Hub relay OGG `cannot find sync word` / `End of file`** — the `relay_stream` generator was designed for live PCM and injected `\x00 × 9600` silence bytes whenever no data arrived for > 1 s. For raw OGG/MP3 file streaming this corrupted the container: QMediaPlayer's FFmpeg received null bytes before the `OggS` sync word and rejected the stream. Root causes: (a) silence injection poisons non-PCM containers; (b) the client-side `stream_file` command can take up to 3 s to arrive (hub polling interval), during which the generator was already injecting silence. Fix: `relay_stream` now checks `slot.kind`. Slots created by `play_file` use `kind="file"` (was `kind="scanner"`). In file mode: no silence injection; waits patiently (polls every 0.3 s) up to 20 s for the first chunk; after data starts flowing, closes the stream cleanly after 5 s of inactivity (signals end-of-file). PCM/live mode (`kind="scanner"`) behaviour is unchanged.

---

## [Logger 1.5.7] - 2026-03-29

### Added
- **Exact-time seeking in hub relay mode** — `play_file` endpoint now extracts `seek_s` from the request body and passes it in the `stream_file` command to the client. `_push_file_to_relay()` accepts `seek_s`: when > 0.5 s, ffmpeg is used to seek to the position and re-mux in the original container format (`-c copy -f {fmt} pipe:1`) before streaming bytes to the relay slot. When seek_s ≤ 0.5 s the file is sent as-is (no transcoding overhead for near-start seeks). Single-node mode passes `seek_s` through the `audio_file` query string; the player seeks natively via HTTP Range / `setPosition`.

---

## [Logger 1.5.6] - 2026-03-29

### Added
- **`/api/mobile/logger/play_file`** — new mobile API endpoint for desktop player audio. Creates a relay slot and queues a `stream_file` command to the client node, which sends the original OGG/MP3/FLAC file bytes through the relay (no PCM transcoding). In single-node mode returns a direct `/audio_file` URL instead.
- **`/api/mobile/logger/audio_file`** — serves the original audio file (OGG/MP3/FLAC/WAV) with mobile Bearer token auth and HTTP Range support. Used by the desktop player in single-node hub mode.
- **`stream_file` hub command** — client-side handler in the hub poller; pushes raw file bytes to the relay slot in 64 KB chunks using the existing `_audio_post` infrastructure.
- **`_push_file_to_relay()`** — sends raw audio file bytes to a relay slot without any FFmpeg transcoding.

---

## [Logger 1.5.5] - 2026-03-29

### Changed
- **Per-instance sidecar JSON replaces shared `metadata.db`** — SQLite file locking is unreliable on network shares (SMB/NFS), causing `database is locked` errors even with WAL mode and intra-process serialisation. Replaced with per-instance sidecar JSON files: each logger writes only its own `{rec_root}/{slug}/{date}/meta_{owner}.json` (where `owner` is derived from the site name). Readers scan all `meta_*.json` files in the day directory and merge events by `(ts, type)` key. Atomic `os.replace()` write from a `.tmp` file prevents partial reads. No cross-process locking required because each process owns exactly one file. Existing local SQLite data is seeded into sidecar files on startup (idempotent).

---

## [Logger 1.5.4] - 2026-03-29

### Fixed
- **`database is locked` errors on shared metadata.db** — the seed, live writes, and reads all opened separate SQLite connections simultaneously from different threads, causing lock contention. Fixed with a per-recording-root `threading.Lock` (`_smd_lock`) that serialises all same-process access to each `metadata.db`. Additionally, `PRAGMA journal_mode=WAL` is now only issued on first creation of the file — setting it on every open also requires a brief exclusive lock, compounding the contention.

---

## [Logger 1.5.3] - 2026-03-29

### Fixed
- **Upgrade path with multiple logger instances** — `_seed_shared_meta_dbs()` in 1.5.2 skipped seeding if `metadata.db` already existed in the recording root. This meant whichever instance upgraded first seeded its events, and the second instance's metadata was never merged in. Fixed by running `INSERT OR IGNORE` on every startup (idempotent — primary key `stream, ts, type` prevents duplicates). Each instance independently merges its own local SQLite events into the shared DB, so both instances' metadata is present regardless of upgrade order.

---

## [Logger 1.5.2] - 2026-03-29

### Fixed
- **Shared directory metadata missing** — when multiple logger instances write to the same recording root (NFS/SMB share), browsing a stream recorded by another instance showed audio segments correctly but no metadata (show name, song/artist, presenter, mic events). Root cause: metadata was only written to the local SQLite DB of each instance. Fix: `_meta_write()` now also writes to a shared `metadata.db` (SQLite with WAL mode) alongside `catalog.json` in each recording root. Any instance that shares that filesystem can read it.
- **Hub mode metadata missing for catalog streams** — the hub poller metadata command handler also now uses the shared DB, so if the client node is itself using a shared directory, its response to the hub includes all streams' metadata (not just its own).
- **Hub legacy site-selector: `_hubSite` reset on stream switch** — when a site was selected via the site drop-down (legacy path), the stream options were built without `dataset.site`, causing `_hubSite` to silently clear to `''` when the user switched streams. This caused `loadMeta()` to call the local endpoint instead of the hub endpoint. Fixed by setting `o.dataset.site = _hubSite` on stream options built from the legacy path.
- **Startup migration** — on first run after upgrade, `_seed_shared_meta_dbs()` copies all existing local SQLite metadata events to the new shared `metadata.db` files (one per recording root). Runs once; skipped on subsequent starts if the file already exists.

---

## [Codec Monitor 1.0.5] - 2026-03-29

### Fixed
- **Nav links broken on client nodes** — Overview, Reports, and Settings links in the Codec Monitor nav bar were hardcoded to `/hub/...` routes which do not exist on client-only nodes. Links are now resolved at render time: hub nodes use `/hub/status`, `/hub/reports`, `/hub/settings`; client nodes use `/`, `/reports`, `/settings`.

---

## [Codec Monitor 1.0.4] - 2026-03-29

### Changed
- **Removed `hub_only` restriction** — Codec Monitor now runs on all node types (hub, client, both). Codec devices are configured per-node so they can be placed alongside the equipment they monitor on the local LAN.
- **Client → hub push architecture** — client nodes poll their local codec devices and push aggregated status to the hub every 15 s via a signed HMAC POST to `/api/codecs/client_status`. The hub caches the latest status per site and merges it with its own local devices on the `/api/codecs/status` response.
- **Multi-site card grouping** — when the hub aggregates codecs from multiple sites, the web dashboard groups cards under site-name section headers. Each site renders its own sub-grid so the layout remains consistent at all viewport widths.
- **Stale indicator** — remote-site cards that have not received a push update in more than 90 s display a `stale` amber badge on the card header, clearly indicating that the status may be out of date (e.g. client node offline).

---

## [Codec Monitor 1.0.3] - 2026-03-29

### Added
- **Dual-codec support (Prodys Quantum ST / APT WorldCast)** — physical units with two codecs per box (Codec A left, Codec B right on the web interface) now display as a split A/B card. Each channel has its own status dot, remote name, and detail. The card header badge shows the worst state of the two. Per-channel `CODEC_FAULT` / `CODEC_RECOVERY` alerts are labelled "Codec A" / "Codec B" so Reports clearly identifies which channel faulted.
- **SNMP trap receiver** — listens on UDP port 10162 (configurable via `codec_trap_port` file alongside `codec_devices.json`; change to 162 if running as root). Configure your Quantum ST / APT device to send traps to the hub IP on this port. Trap-triggered state changes update the dashboard and fire alerts instantly without waiting for the next poll cycle. Channel inference from trap OID patterns — OIDs with A/1 suffix → Codec A, B/2 suffix → Codec B. Falls back to raw keyword parsing if pysnmp is unavailable.
- **Mobile API dual-codec fields** — `/api/mobile/codecs/status` now includes `dual_codec: true`, `codec_a: {state, state_label, detail, remote, duration_s}` and `codec_b: {...}` for dual-codec devices. Top-level `state` is the worst of A/B for backwards-compatible simple checks.
- **Dual HTML scraper** — for HTTP-polled dual-codec devices, the page is fetched once and split into A/B halves. Strategy 1: look for explicit `id`/`class` attributes containing `codec-a`, `channel-b`, `encoder1`, `portA`, etc. Strategy 2: split at the page midpoint and parse each half independently.

---

## [Codec Monitor 1.0.2] - 2026-03-29

### Added
- **Device page proxy** — each codec card now has a 🌐 button that opens the device's own web interface inside a modal iframe, served through a SignalScope server-side proxy. The user logs in normally through the device's native UI; the server captures the session cookie in a per-device `CookieJar`. All subsequent status polls reuse that session automatically — no credentials need to be stored in SignalScope for devices with custom login screens (Prodys Quantum ST, etc.).
- **Session indicator** — a green `● session` badge appears under the device type label when a live session is present. The proxy modal shows "Session active" or "No session — log in below" in its header bar.
- **Clear session button** — in the proxy modal, clears the stored cookie jar and reloads the device page so the user can re-authenticate if the session expires.
- HTML link rewriting in the proxy rewrites `href`, `src`, and `action` attributes so all navigation (links, form submissions, images, CSS, JS) stays inside the proxy. A `<base target="_self">` is injected so anchor clicks remain in the iframe rather than breaking out.

---

## [Codec Monitor 1.0.1] - 2026-03-29

### Fixed
- **HTTP Digest auth support** — devices that challenge with `WWW-Authenticate: Digest` (some Comrex and Tieline models) would get a 401 back and report offline even with correct credentials. Replaced the pre-emptive Basic `Authorization` header with a proper `HTTPBasicAuthHandler` + `HTTPDigestAuthHandler` opener so both auth schemes are handled automatically. On a 401 challenge urllib retries with the correct scheme. Devices with no auth are unaffected.

---

## [Codec Monitor 1.0.0] - 2026-03-29

### Added
- **New plugin: Codec Monitor** — real-time connection monitor for broadcast contribution codecs. Supported device types: **Comrex** ACCESS NX / BRIC-Link (HTTP status-page scraping), **Tieline** Gateway / Bridge-IT / ViA (HTTP + XML scraping), **Prodys Quantum ST** (SNMP v2c primary, HTTP fallback), **APT / WorldCast Quantum** (SNMP v2c primary, HTTP fallback), **TCP Ping Only** (basic reachability), and **Custom** (user-configured HTTP endpoint).
- Each device is polled on a configurable interval (default 30 s). States: `Connected`, `Idle / Ready`, `Disconnected`, `Offline`, `Error`, `Unknown`.
- **Alert integration** — fires `CODEC_FAULT` alert into the SignalScope Reports page when a device goes offline or disconnects; fires `CODEC_RECOVERY` when it comes back. Alerts carry the associated stream name so they appear alongside chain events.
- **Force-check button** — manually trigger an immediate poll on any device from the dashboard.
- **Remote name extraction** — where the device response includes a connected peer name it is shown on the card alongside the status.
- **Mobile API** — `GET /api/mobile/codecs/status` (Bearer token auth) returns JSON array with `id`, `name`, `type`, `state`, `state_label`, `detail`, `remote`, `last_checked`, `last_change`, `duration_s` for every configured device. Suitable for an iOS Codecs tab.
- SNMP requires `pysnmp` (`pip install pysnmp`). If not installed the plugin falls back to HTTP + TCP for SNMP-type devices automatically — no crash.

---

## [SignalScope 3.4.62] - 2026-03-29

### Fixed
- **Hub Reports duplicate clip rows** — When a client uploaded a clip, `hub_clip_upload` wrote a new entry to the hub alert log using a freshly generated UUID instead of the client's original event ID (`entry_id`). Because the IDs never matched, `hub_reports()`'s `seen_ids` deduplication never fired, and every uploaded clip appeared twice — once from the site's `recent_alerts` heartbeat (site row) and again from the hub alert log (`(hub)` row). Fixed: hub alert log entry now reuses `entry_id` from the upload payload when present, allowing the existing deduplication to suppress the duplicate. Falls back to a fresh UUID for old clients that don't send `entry_id`.

---

## [Logger 1.4.28] - 2026-03-29

### Fixed
- **Long-poll deadlock and syntax error** — All `_hub_set_pending()` calls were incorrectly placed inside `with _hub_logger_lock:` blocks. Since `_hub_set_pending()` itself acquires the same non-reentrant lock, every hub play/days/segments/metadata request deadlocked immediately. The hub play endpoint also had a missing closing `)` on the call that prevented the plugin from loading entirely. All six call sites fixed — `_hub_set_pending()` moved outside the lock block in each case. Plugin now loads and long-polling works correctly.

---

## [Logger 1.4.27] - 2026-03-29

### Changed
- **Long-polling for hub commands** — Client polling thread no longer sleeps 3 s between polls. The hub holds `GET /api/logger/hub/poll/<site>` open for up to 25 s using a `threading.Event`; `_hub_set_pending()` calls `evt.set()` to wake the connection the instant a command is queued. Result: hub play/stop/metadata commands reach the client in milliseconds instead of up to 3 s. Only sleeps 2 s on error before retrying.

---

## [Logger 1.4.26] - 2026-03-29

### Fixed
- **Duplicate audio when clicking timeline rapidly** — `connectAudio()` cancelled the fetch reader on a new play request but left already-scheduled `AudioBufferSource` nodes running (Web Audio nodes continue playing after the reader is gone). Added `_activeSrcs` array tracking all scheduled sources; `_stopHubAudio()` calls `.stop()` on each to kill them immediately. Sources are removed from the array via their `onended` callback.

---

## [Logger 1.4.25] - 2026-03-29

### Fixed
- **Silent playback after clicking timeline** — `startHubPlay()` called `gainNode.gain.setValueAtTime(0)` to mute old audio before the new stream started. If the generation counter discarded the POST response (rapid clicks, network error) `connectAudio()` was never called to restore gain, leaving it permanently at 0. Fixed: removed gain muting from `startHubPlay()`; `_stopHubAudio()` and `connectAudio()` now use `cancelScheduledValues(0) + gain.value = X` for reliable immediate gain changes that can't be stranded.

---

## [Logger 1.4.24] - 2026-03-29

### Fixed
- **Clicking timeline spawned multiple concurrent audio streams** — Rapid clicks fired multiple `POST /api/logger/hub/play` requests; each `.then()` called `connectAudio()` when it resolved, resulting in simultaneous PCM streams. Fixed with a generation counter (`_playGen`): each `startHubPlay()` increments the counter and captures the current value; the `.then()` callback checks `gen !== _playGen` and discards responses from superseded requests. Play button shows ⏳ immediately while the POST is in flight; `disabled` prevents a second tap landing before the first resolves.

---

## [Logger 1.4.23] - 2026-03-28

### Fixed
- **Hub remote playback slow to start** — `_PRE` (audio pre-buffer) was 5.0 s — copied from the live scanner where WAN jitter makes it necessary. For recorded playback 1 s is sufficient. Reduced to 1.0 s; audio now begins within ~1 s of clicking play.
- **Pause/spacebar didn't stop audio** — Play button handler only cancelled the fetch reader, leaving already-scheduled `AudioBufferSource` nodes playing for up to 5 s. `_stopHubAudio()` now sets gain to 0 immediately via `gainNode.gain.setValueAtTime(0, currentTime)`.
- **Spacebar spawned a second player** — Play button had no toggle: pressing it always ran stop-only code with no resume path. Added `_hubIsPlaying` / `_hubPlayPending` flags; pressing play/space when stopped resumes from `_hubPlayOffset` (position saved on pause); `_hubPlayPending` guard prevents a double-start if play is tapped twice before the POST response arrives.

---

## [Logger 1.4.21] - 2026-03-28

### Fixed
- **Mobile relay stream endpoint** — Added `/api/mobile/logger/relay_stream/<slot_id>` with Bearer token auth so the iOS app can stream PCM audio without needing a web session cookie.
- **Direct PCM stream in local/non-hub mode** — `POST /api/mobile/logger/play` now returns a direct stream URL in local mode instead of requiring a hub relay slot.
- **`_safe(filename)` path bug** — filename was not sanitised before being joined to the recordings path, allowing path components to escape the recordings directory.
- **Segments local mode crash** — `_hub_logger_segs.values()` called on a plain dict instead of the expected list in local-mode fallback path.

---

## [DAB Scanner 1.0.28] - 2026-03-28

### Fixed
- **Dongle selector showed DAB-role dongles** — `/api/hub/dab/devices` previously returned both `dab_serials` (fixed background decoding dongles) and `scanner_serials`. DAB-role dongles are permanently assigned to background decoding and must not be grabbed by the DAB Scanner UI. Endpoint now returns `scanner_serials` only, matching the FM Scanner behaviour.

---

## [SignalScope 3.4.61] - 2026-03-28

### Fixed
- Broadcast Chains page 500 error — comparator dicts saved before `from_sub`/`to_sub` fields were introduced caused a Jinja2 `UndefinedError` at render time. Fixed by defaulting both fields to `none` in the template (`| default(none)`).

---

## [SignalScope 3.4.60] - 2026-03-28

### Added
- Public `/privacy` route — privacy policy page served without login, styled to match the SignalScope UI. Use `https://your-hub/privacy` as the App Store privacy policy URL.
- Privacy Policy link added to all page footers.

---

## [SignalScope 3.4.58] - 2026-03-28

### Fixed
- **Plugin ctx missing `mobile_api_required`** — `_load_plugins()` now passes `mobile_api_required` through the `ctx` dict so plugins can correctly authenticate `/api/mobile/...` routes using the app's Bearer token rather than the web session cookie.

---

## [Logger 1.4.20] - 2026-03-28

### Fixed
- **Mobile API routes used wrong auth decorator** — All eight `/api/mobile/logger/...` routes were decorated with `@login_req` (session-based web auth) instead of `@mobile_api_req` (Bearer-token auth). With `@login_req`: if web auth was disabled the routes were completely open; if enabled the iOS app's Bearer token was silently ignored and every request received a login-page redirect. All mobile routes now use `mobile_api_required` — requires the mobile API to be enabled in Settings and validates the token with constant-time comparison. Falls back to `login_required` on older hub versions that don't yet pass `mobile_api_required` through ctx.

---

## [Logger 1.4.19] - 2026-03-28

### Added
- **Mobile API for iOS app** — Added nine new routes under `/api/mobile/logger/` to support the iOS app Logger tab. `GET /api/mobile/logger/status` returns a presence check so the app can detect whether the Logger plugin is installed. `GET /api/mobile/logger/sites` lists hub-connected sites that have logger streams. `GET /api/mobile/logger/streams` returns available streams (optionally filtered by site). `GET /api/mobile/logger/days` returns available recording dates for a stream/site pair, with `pending: true` polling support for hub mode where the hub must first request the data from the remote client. `GET /api/mobile/logger/segments` returns the list of five-minute recording segments for a given stream/date, also with pending-poll support. `GET /api/mobile/logger/metadata` returns show/track/mic metadata events for a day from the local SQLite database (local mode) or the hub cache (hub mode). `POST /api/mobile/logger/play` creates a relay slot, sends a play command to the client site, and returns the PCM stream URL for the iOS `PCMStreamPlayer`. `POST /api/mobile/logger/stop` closes the relay slot and cancels playback. All routes require Bearer token authentication (`login_req`).

---

## [Latency 1.0.1] - 2026-03-28

### Fixed
- **Comparator data not appearing** — The poller read `comp.get("pre")` / `comp.get("post")` but the SignalScope heartbeat payload uses `pre_name` / `post_name`. Updated to read `pre_name`/`post_name` with fallback to bare `pre`/`post` for forward-compatibility. Also added an `aligned` guard so comparators that haven't yet cross-correlated (delay_ms = 0.0) are skipped rather than polluting the history database with false zero-delay readings.
- **Wrong colour palette** — Plugin used a purple/indigo theme (`--ac:#6366f1`, dark grey backgrounds). Replaced with the standard SignalScope navy/cyan palette (`--bg:#07142b`, `--ac:#17a8ff`, etc.) across both the main page template and the Settings page template.
- **Sparkline line colour** — SVG polyline stroke was hardcoded `#6366f1` (indigo). Changed to `#17a8ff` (SignalScope cyan) to match the rest of the UI.

---

## [Logger 1.4.18] - 2026-03-28

### Fixed
- **Logger — Opus recording produces empty files (rc=234)** — `libopus` only accepts sample rates of 8000, 12000, 16000, 24000, or 48000 Hz. The recording command hardcoded `-ar 44100` which is not a valid Opus rate, causing ffmpeg to silently fail with "Nothing was written into output file". Fixed by adding a per-format output sample rate to `_REC_FORMATS` (MP3/AAC use 44100, Opus uses 48000) and substituting it into both the recording command and the LQ quality-downgrade re-encode command.

---

## [Logger 1.4.17] - 2026-03-28

### Added
- **Logger — Per-stream recording format (MP3 / AAC / Opus)** — Each stream in Settings now has a Format selector alongside the existing bitrate controls. **MP3** is the default and unchanged. **AAC** (`.aac`, ADTS container) uses the `aac` encoder — roughly half the storage of MP3 at the same perceived quality, so a 128k AAC recording is comparable to a 192–256k MP3. **Opus** (`.opus`, OGG container) uses `libopus` — the most efficient option, with quality at 64–96k that rivals MP3 at 192k+. All three formats are fully supported through the rest of the pipeline: the segment filesystem scan, `_fname_to_secs`, the quality-downgrade re-encoder, the disk-usage counter, the audio serve endpoint, and the export function all handle `.mp3`, `.aac`, and `.opus` files correctly. Export stream-copy optimisation (instant, no re-encode) is preserved for MP3→MP3; all other export format combinations re-encode via ffmpeg as before.

---

## [Logger 1.4.16] - 2026-03-28

### Added
- **Logger — Export format selector (MP3 / AAC / Opus)** — A format dropdown next to the Export Clip button lets you choose between three formats. MP3 is the default and uses a stream copy (no re-encode, instant). AAC (`.m4a`) re-encodes at 128 kbps — roughly half the file size of MP3 at equivalent perceived quality, with universal browser and device support including older Safari. Opus (`.webm`) re-encodes at 96 kbps — the most efficient option, approximately half the size of AAC again, supported in all modern browsers (Chrome, Firefox, Edge, Safari 16.4+). The downloaded filename extension matches the chosen format.

---

## [Logger 1.4.15] - 2026-03-28

### Fixed
- **Logger — right-click mark-in/out now works on the green audio bar** — The day-bar waveform overview was incorrectly exempted from the contextmenu handler. Removed the guard so right-clicking anywhere in the full timeline area (including the audio waveform bar) sets mark-in/mark-out.

---

## [Logger 1.4.14] - 2026-03-28

### Fixed
- **Logger — right-click mark-in/out now works in Safari** — Safari requires `contextmenu` to be intercepted at the `document` level for `preventDefault()` to reliably suppress the browser menu. Moved the listener from `.tl-scroll-wrap` to `document` with a target check (`e.target.closest('.tl-scroll-wrap')`); behaviour is otherwise identical.

---

## [Logger 1.4.13] - 2026-03-28

### Added
- **Logger — Right-click on timeline to set mark-in / mark-out** — Right-clicking anywhere in the zoomed timeline overview (show/track/mic bands) sets export markers directly from the overview without needing the Mark In / Mark Out buttons. First right-click sets the in-point (clearing any previous out-point); second right-click sets the out-point. If you right-click again after both markers are placed, a new in-point is started. Clicking before the current in-point moves the in-point instead. Works at any zoom level and correctly accounts for the horizontal scroll position. The day-bar audio waveform is exempt (right-clicking there has no effect, preserving its normal behaviour).

### Fixed
- **Logger — Show name band no longer fragments into repeated short blocks** — The metadata API re-emits the current show name on every 30-second poll, creating dozens of duplicate adjacent `show` events. `_renderShowBand` now merges consecutive events with the same `show_name + presenter` key into a single continuous block, so each show appears as one unbroken span that stretches from its start time to the next different show.

---

## [Logger 1.4.12] - 2026-03-28

### Fixed
- **Logger — spacebar no longer scrolls the page** — Pressing Space to toggle play/pause was triggering the browser's default scroll-down behaviour, jumping the user to the bottom of the page. A `keydown` listener now intercepts the Space key (when focus is not in an input, textarea, or select) and calls `preventDefault()` before firing the play button — so spacebar toggles playback without any page movement.

---

## [Logger 1.4.11] - 2026-03-28

### Fixed
- **Logger — day-bar audio overview now scrolls in sync with show/track/mic bands** — When click-and-drag panning a zoomed timeline, the audio waveform overview bar (day-bar) previously stayed fixed while the show-name, track, and mic bands scrolled beneath it. Root cause: the day-bar was outside the `.tl-scroll-wrap` / `#tl-zoom-content` container. Fix: moved `#day-bar` inside `#tl-zoom-content` so all timeline elements — overview bar, time axis, show band, mic band, and track band — share a single scrollable/zoomable container and move together.

---

## [Logger 1.4.10] - 2026-03-28

### Added
- **Logger — Click-and-drag pan on zoomed timeline** — At any zoom level > 1× the timeline overview area (day-bar, show band, mic band, track band) can be panned by clicking and dragging left or right. Cursor changes to a grab hand while hovering and a grabbing hand while dragging. The day-bar scrub interaction is preserved — clicking directly on the day-bar still seeks playback rather than panning. Touch drag is also supported.

---

## [Logger 1.4.9] - 2026-03-28

### Fixed
- **Logger — zoom/expand now applies to the overview (day-bar + bands), not the hour grid** — 1.4.8 incorrectly put the hour grid inside the horizontal-scroll zoom container, so zooming moved the "bottom table with times" rather than the top overview area. The hour grid is now always fixed-width and unaffected by zoom. The zoom and expand controls affect only the day-bar overview, show band, mic band, and track band. Expand also grows the day-bar height from 30 px to 80 px. A thin separator divides the zoomed overview from the fixed hour grid below.

---

## [Logger 1.4.8] - 2026-03-28

### Added
- **Logger — Timeline zoom (1×/2×/4×/8×) and row expand** — Four zoom preset buttons in the timeline header horizontally zoom the hour grid, show-name band, mic band, and track band together. At 2× you see 12 hours at a time; at 8× individual 5-minute blocks are large enough to clearly read song and show labels. The time axis, all three metadata bands, and the hour grid scroll horizontally in sync inside a dedicated scroll wrapper; the day-bar minimap and header controls stay fixed at full width. An ↕ Expand toggle alongside the zoom buttons doubles the row height (22 px → 40 px) and the band heights proportionally, making show and track labels visually prominent.

---

## [Zetta 1.0.2 · Morning Report 1.0.2] - 2026-03-28

### Changed
- **Style overhaul — Zetta and Morning Report now match the SignalScope palette** — Both plugins previously declared their own `:root` CSS variables (`--bg:#0f1117`, `--ac:#6366f1` indigo, etc.) that produced a clashing near-black / purple colour scheme instead of SignalScope's navy / cyan design system.

  Changes applied to both plugins:
  - `:root` block replaced with the standard SignalScope palette (`--bg:#07142b`, `--sur:#0d2346`, `--bor:#17345f`, `--acc:#17a8ff` cyan, `--wn:#f59e0b`, etc.)
  - All `var(--bg2)` → `var(--sur)`, `var(--bg3)` → `#173a69` (input blue), `var(--bd)` → `var(--bor)`, `var(--ac)` → `var(--acc)`, `var(--wa)` → `var(--wn)`
  - Topbar background `#1a1d28` → `var(--sur)` — matches the SignalScope header
  - Buttons use `--acc` (cyan) instead of `--ac` (indigo)
  - Heatmap cells (Morning Report) use cyan-tinted `rgba(23,168,255,…)` instead of indigo `rgba(99,102,241,…)`
  - `pattern-item.color-blue` uses `var(--acc)`, inputs use `#173a69` background and `var(--acc)` focus ring
  - Hardcoded Zetta card border colours `#b45309` / `#15803d` replaced with `var(--wn)` / `var(--ok)`

---

## [Logger 1.4.7] - 2026-03-28

### Added
- **Logger — Mic on-air REST API + timeline band** — New `POST /api/logger/mic` endpoint records mic-on/off events on the timeline. Accepts `{"stream":"slug","state":"on","label":"Studio A"}` with optional `ts` (Unix timestamp; defaults to server time). Auth: logged-in browser session or `Authorization: Bearer <key>` using a configurable Mic API Key set in Logger Settings. Events appear as a thin green band between the show-name band and the song-track band — green spans show exactly when each mic was live; hovering shows the mic label and start time. A still-open mic\_on with no subsequent mic\_off renders with a brighter shade to indicate the mic is currently live.

  **Example:**
  ```sh
  curl -X POST https://hub/api/logger/mic \
    -H "Authorization: Bearer mysecret" \
    -H "Content-Type: application/json" \
    -d '{"stream":"capital_london","state":"on","label":"Studio 1"}'
  ```

---

## [Logger 1.4.6] - 2026-03-28

### Added
- **Logger — Per-song track band with exact start/stop times** — A new amber track band now appears between the show-name band and the hour grid. Each song is rendered as a precisely positioned span from its exact start timestamp to the moment the next track begins, mirroring how the show-name band works. Hovering a span shows `HH:MM:SS — Artist — Title`. Block-level tooltips on the 5-minute hour grid now also show the exact `HH:MM:SS` start time for every track that changed within that block (previously only the first track was named, with no timestamp).

---

## [Logger 1.4.5] - 2026-03-28

### Added
- **Logger — Planet Radio station dropdown in settings** — The "Now Playing URL" text input in each stream's settings card now has a "Planet Radio Station" dropdown above it (populated from SignalScope's existing `/api/nowplaying_stations` endpoint). Selecting a station automatically fills the URL field with the local `/api/nowplaying/{rpuid}` route. Selecting "— Custom URL —" clears the field. Falls back gracefully to the plain text input when the endpoint is unavailable or returns no stations. The existing `_parse_nowplaying()` handler already parses the `{artist, title, show}` response format, so no backend changes are needed.

---

## [3.4.56] - 2026-03-28

### Fixed
- **Morning Report 1.0.1 — Page always returned 500** — The heatmap template used `{{h:02d}}` (Python f-string format spec) which is not valid Jinja2 syntax. Jinja2 treats the `:` as an unexpected token and raises a TemplateSyntaxError on every page render. The page was never functional since the first commit.

  Fix: replaced `{{h:02d}}` with `{{ "%02d" % h }}` in both the heatmap cell `title` attribute and the hour label below the grid.

  **Rule**: Jinja2 template expressions `{{ }}` do not support Python's format mini-language (`{value:spec}`). Use `{{ "%02d" % value }}` or `{{ value | string | rjust(2, "0") }}` for zero-padded integers.

- **Logger 1.4.3 — Settings panel hangs if disk scan fails** — `api_logger_status` iterated over all recordings with `f.stat().st_size` inside a bare loop with no exception handling. A file deleted between `rglob` discovery and `stat()` (e.g. by the concurrent maintenance thread), an unreachable network share, or a permission error would raise an `OSError` that Flask caught as a 500. The `/api/logger/status` call in the settings panel `Promise.all` then returned an HTML or JSON error body; `r.json()` either threw or returned an error object, rejecting the promise and leaving the UI frozen at "Calculating disk usage…" with no streams or base directories shown.

  Fix: wrapped the entire disk-scan loop in nested `try/except OSError` blocks so individual file errors are skipped silently. The outer `except Exception` catches any unexpected failure and logs it. Added `.catch()` to `loadSettingsPanel`'s `Promise.all` so any remaining API failure now shows "⚠ Settings failed to load — check server logs" instead of a frozen spinner.

  **Rule**: Any route that scans the filesystem with `rglob` MUST wrap `f.stat()` in `try/except OSError` — files can be deleted between discovery and stat. Always add `.catch()` to `Promise.all` calls in the UI.

---

## [3.4.55] - 2026-03-28

### Fixed
- **Logger 1.4.2 — Changing base directory hangs forever (deadlock)** — Moving stream recordings after a base-directory change in Settings caused the page to hang indefinitely ("Moving recordings…" for 30+ minutes with nothing actually copied). Root cause: `api_logger_save_config` held `_cfg_lock` and then called `_rec_root()`, which also tries to acquire `_cfg_lock`. Python's `threading.Lock` is not reentrant — the same thread blocks on itself permanently. The save response never returned and nothing was moved.

  Fix: `_rec_root()` is now called **before** the `with _cfg_lock:` detection block, storing the result in `default_old_root`. The lock block then uses the pre-computed value instead of re-entering the lock. The log also now prints the resolved source and destination paths at the point of queuing each move, making future path issues diagnosable without staring at a hung browser tab.

  **Rule**: Never call `_rec_root()`, `_stream_rec_root()`, or any function that acquires `_cfg_lock` from inside a `with _cfg_lock:` block — Python's `threading.Lock` is not reentrant and will deadlock.

---

## [3.4.54] - 2026-03-28

### Fixed
- **Logger 1.4.1 — Recording directory move blocks forever on large archives** — Changing a stream's base directory assignment called `_move_stream_recordings` synchronously inside the Flask save-config request handler. For a cross-filesystem move (e.g. moving months of recordings to a NAS or different drive) `shutil.move` performs a full copy-then-delete; at typical NAS speeds this takes many minutes, holding the HTTP connection open and leaving the browser stuck on "Moving recordings…" with the destination still empty.

  Fix: config is now saved **first** (new recordings immediately land in the correct location), then the move runs in a **background thread** (`LoggerMoveRecordings`). The save POST returns instantly with `{"ok": true, "moving": true}`. A new `GET /api/logger/move_status` endpoint reports `{active, done, total, current, error}`. The Settings JS polls this endpoint every 1.5 s and shows live progress ("Moving recordings… 2/3 · StreamName (66%)") until the move finishes, then shows "✓ Saved — N streams moved".

  **Rule**: Never call `shutil.move` synchronously in a Flask request handler when the source may be large or cross-filesystem. Always background the move and return immediately.

---

## [3.4.53] - 2026-03-28

### Added
- **Logger 1.4.0 — Now-playing metadata integration** — The timeline now shows what show and song was playing on each stream throughout the day, sourced from a configurable now-playing API or from live DLS/RDS data.
  - **Per-stream "Now Playing URL"** setting added to each stream card in Logger Settings. Accepts any JSON endpoint that returns artist/title/show info. Supports Planet Radio/Bauer nested format (`data.now.title`, `data.schedule.current.title`), Triton Digital (`now_playing.song`), and generic flat JSON with `title`/`artist`/`show` keys. Leave blank to use DLS (DAB) or RDS (FM) text from the SignalScope monitor.
  - **`_MetaPoller` background thread** polls the configured URL every 30 seconds per stream. Writes change events to a new `metadata_log` SQLite table (columns: stream, ts, type, title, artist, show_name, presenter, raw). Events are only written when the title/artist or show name actually changes, so the log stays compact.
  - **Show band** — a coloured strip just above the block grid shows the current show name across its full duration for the day. Purple/violet tint with the show name (and presenter if available) as text. Show spans are sized proportionally to the full 24-hour day.
  - **Track blocks** — timeline blocks containing at least one logged track event are tinted amber/gold (distinct from the green/orange/red silence status colours). Hovering a track block shows the artist and title in the tooltip alongside the existing silence info.
  - **`GET /api/logger/metadata/<slug>/<date>`** — returns all metadata events for a stream on a given date as a JSON array with `ts_s` (seconds since midnight UTC), `type` (`track` or `show`), `title`, `artist`, `show_name`, `presenter`.
  - **Hub relay** — in hub mode the browser polls `/api/logger/hub/metadata/<site>/<slug>/<date>`, which queues a `metadata` command for the client via the existing hub↔client poll pattern. The client queries its local `metadata_log` and POSTs the results back. The same retry/pending logic as days/segments is used.
  - **`metadata_log` table** added to `logger_index.db` automatically on startup. No migration needed — `CREATE TABLE IF NOT EXISTS` is used.

---

## [3.4.52] - 2026-03-28

### Added
- **Logger 1.3.0 — Named base directories with per-stream assignment and automatic move-on-change** — Settings now has a "Base Directories" section where any number of named storage locations can be defined (e.g. "Local SSD" → `/var/recordings`, "NAS" → `/mnt/nas`). Each stream card gains a "Base Directory" dropdown; selecting a directory routes that stream's recordings to that location under a subdirectory named after the stream slug. The global "Default Path" remains as the fallback for streams set to "Default". When a stream's base directory assignment is changed and saved, the existing recording tree is automatically moved to the new location (date sub-directories are merged rather than overwritten if the destination already has data for that date). Disk usage totals in the status card are summed across all known root paths. Maintenance (retention pruning and LQ downgrade) runs across all roots.

---

## [3.4.51] - 2026-03-28

### Fixed
- **Logger 1.2.3 — WAN audio buffer underruns during hub remote playback** — Remote playback sounded choppy when the client was on a high-latency WAN link. The previous code sent one 9 600-byte PCM block (0.1 s of audio) per HTTP POST. With a 150–300 ms round-trip to the remote client, each synchronous POST consumed more wall-clock time than the audio it delivered, so the relay buffer drained faster than it filled.
  - `_push_audio_to_relay` now batches **16 chunks per POST (1.6 s of audio)**. At 300 ms RTT each POST delivers 5× more audio than it takes to send, keeping the relay buffer 4–8 s ahead of playback even across a WAN link.
  - Push rate raised to **3× real-time** (was 1.15×) to build the buffer faster on playback start.
  - A new helper `_audio_post()` encapsulates HMAC signing and error handling, returning `False` on slot-closed (404) to stop the push loop cleanly.
  - Browser PCM pre-buffer raised from **1.0 s → 5.0 s** (`_PRE`) to absorb jitter before the Web Audio scheduler runs dry.

---

## [3.4.50] - 2026-03-28

### Fixed
- **Logger 1.2.2 — Hub poll URL fails when site name contains spaces** — The client poller built the poll URL as a raw f-string: `/api/logger/hub/poll/Northern Ireland DAB`. Python's `urllib.request` rejects URLs with literal spaces, so every poll attempt raised `URL can't contain control characters` and the client never received any commands — causing the timeline, dates, and audio to all silently fail. Fix: `urllib.parse.quote(site, safe="")` is applied to the site name before embedding it in the poll URL. Flask's `<path:site>` route converter URL-decodes it back to the original string on the hub, so the lookup in `_hub_logger_pending` continues to work correctly.

---

## [3.4.49] - 2026-03-28

### Fixed
- **Logger 1.2.1 — Hub remote playback debugging & reliability** — Hub remote playback (days/segments/audio) was silent after stream selection. Fixes in this release:
  - Comprehensive `_log()` tracing added throughout the command/poll/result cycle on both hub and client — every queue, dispatch, and result-store operation is now visible in the SignalScope log panel for diagnosis.
  - `_push_audio_to_relay` now pushes audio at **1.15× real-time** instead of as-fast-as-possible. Unbounded push speed could flood the relay slot queue and cause the browser's Web Audio pump to schedule the entire file in one burst, leading to silence or distorted playback.
  - `apiDays` and `apiSegments` JS polling loops now encode the slug with `encodeURIComponent` (consistent with site encoding) and display a "Fetching from remote site… (n)" status message in the sidebar so the user can see progress. A 20-attempt (~60s) timeout replaces the previous infinite retry, showing a diagnostic message if the client never responds.
  - Hub-status message is cleared when site or stream changes.

---

## [3.4.48] - 2026-03-28

### Added
- **Logger 1.2.0 — Hub remote playback** — When the logger plugin is installed on a hub, the Logger page now aggregates recorded streams from every connected client site. A "Site" selector appears above the stream picker; choosing a remote site loads its available days and segment timeline. Playback relays audio from the client to the hub using the existing PCM relay infrastructure — the full timeline UI (day bar, mark in/out, export) works identically for remote streams. Client nodes with the logger installed register their stream list with the hub on startup and respond to metadata and playback commands via the standard client-polls-hub pattern, requiring no direct hub→client connections.

---

## [3.4.47] - 2026-03-28

### Changed
- **Logger plugin 1.1.0 — unified continuous timeline UI** — Complete UX overhaul of the compliance logger playback interface:
  - **Full-day seekable bar**: A new thin horizontal bar above the block grid shows all 288 five-minute segments as a single strip coloured by silence status. Click or drag anywhere on the bar to jump to that exact time. A green playhead tracks current position; blue/amber in/out markers and a shaded range region update live.
  - **Wall-clock timestamps everywhere**: The player now shows the real recorded time (`HH:MM:SS`) rather than position within the current 5-minute clip. In/Out labels show exact wall-clock times (e.g. `In: 14:32:18 · Out: 15:04:45 · Dur: 32:27`).
  - **Cross-block mark in/out**: In and Out points persist when switching segments. You can mark in on one block, navigate to any other block, and mark out — the selection spans the full range. Timeline blocks within the selection get a blue tint overlay.
  - **Cross-block export**: Export already stitched multi-segment files on the server; the client now correctly passes the marks regardless of which block is currently loaded.
  - **Auto-advance playback**: When a segment finishes, playback automatically continues into the next consecutive recorded segment without user interaction.

---

## [3.4.46] - 2026-03-28

### Fixed
- **DAB Scanner — opens and partially functions when no DAB dongle is configured** — `GET /api/mobile/dab/sites` previously included dongles in `scanner` role alongside `dab` role dongles. If a site had only FM/scanner dongles, the iOS DAB Scanner would show the site picker and attempt to scan rather than displaying the "not available" card. Fix: endpoint now only returns sites where at least one dongle has `role = "dab"`. Sites with only `scanner` role dongles are excluded — the iOS unavailable card is shown correctly.

---

## [3.4.45] - 2026-03-28

### Added
- **Mobile API — glitch data on streams and nodes** — `GET /api/mobile/hub/overview` now includes `glitch_count` per stream. `_mobile_node_summary` now includes `glitch_count` and `rtp_loss_pct` per chain node so the iOS app can show glitch and RTP badges on individual chain nodes.
- **Mobile API — health score on chains** — `_mobile_chain_summary` now includes `health_score` and `health_label` so the iOS app receives and can display the chain health score (was always in the iOS model, now actually populated by the API).
- **Mobile API — A/B groups endpoint** — New `GET /api/mobile/ab_groups` returns all configured A/B failover groups with live status (`ok`/`warn`/`fault`/`unknown`), active role, per-chain health booleans, and chain names. Used by the new iOS A/B Groups tab.
- **iOS — glitch count badges** — Stream rows in the Sites tab show an orange ⚡ glitch count when > 0. Chain node detail rows show glitch count and RTP loss % with severity colour coding.
- **iOS — A/B Groups tab** — New tab in the iOS app showing all configured A/B failover groups with status badge, active/standby chain labels, per-chain status dots, and notes. Pull-to-refresh.
- **iOS — GLITCH event type** — Report event cards show GLITCH and AUDIO_GLITCH_SUSTAINED with an orange `bolt.fill` badge rather than the default grey.

### Fixed
- **Mobile API — site_status reflects actual stream alerts** — `api_mobile_hub_overview` previously set `site_status` based only on AI anomaly state (`ai_status`). Now also considers actual stream alert status (silence, fault) and glitch activity, so the app correctly shows a site as alert when there is a silence fault even if AI is fine.

---

## [3.4.44] - 2026-03-28

### Fixed
- **Hub dashboard — CPU / RAM pills appear on a separate row** — the CPU and RAM `<div class="sum-pill">` elements were placed outside the `.hub-summary` container, so the browser rendered them as block-level elements on their own row beneath the other summary pills. Moved them inside `.hub-summary` as inline `<span class="sum-pill">` elements, matching the clock / build / sites / offline / alert / warn / stale pills. They now sit on the same summary row and are conditionally rendered (hidden when metrics are unavailable).

---

## [3.4.43] - 2026-03-28

### Fixed
- **Clip cleanup deletes unuploaded clips** — `_clip_cleanup` enforced a 200-clips-per-stream limit by deleting the oldest files, regardless of whether they had been confirmed uploaded to the hub (`.hub` marker). Clips still pending in the upload queue had their WAV files removed before the queue consumer could send them, making them permanently inaccessible on both client and hub. Fix: `_clip_cleanup` now only considers clips that have a `.hub` sidecar (confirmed on hub) when applying age and count limits. Unconfirmed clips are never deleted — `_sync_pending_clips` needs them to retry. Also removes the companion `.hub`/`.meta` sidecars when deleting an eligible confirmed clip (was only removing the audio file before).
- **Glitch clips uploaded to hub classified as CHAIN_FAULT** — the label-to-alert-type mapping in `hub_clip_upload` had no `"glitch"` case, so glitch clips fell through to the `else` branch and were stored in the hub alert log as `CHAIN_FAULT` events. They now appear correctly as `GLITCH` events in hub Reports.

---

## [3.4.42] - 2026-03-27

### Fixed
- **Broadcast Chains — badly-segued ad breaks trigger false FAULT** — when ad spots are poorly edited (brief silence at the end of one spot before the next begins), the chain briefly recovered from the `"pending"` confirmation window back to `"ok"`, then immediately re-entered `"pending"` with a fresh countdown. Enough inter-ad gaps could exhaust the full confirmation window and fire a real FAULT alert for what was clearly an ongoing ad break.

  Fix: **ad-break gap stitching**. When a chain recovers within the confirmation window from an adbreak-candidate fault, the system now stores the original `_chain_fault_since` timestamp and the recovery time. If the chain re-faults within the configurable **Ad-break gap tolerance** (default 5 s) and is still an adbreak candidate, the original clock is restored rather than starting a fresh countdown. All the inter-ad silences collectively eat into one shared confirmation window — the fault can only fire if the *total* codec-silent time (minus brief inter-ad audio) exceeds `min_fault_seconds`. Normal audio recovery beyond the tolerance clears the stitching state and subsequent faults get a fresh timer as before.

### Added
- **Chain builder — Ad-break gap tolerance setting** — new per-chain setting in the Timing & Behaviour panel (default 5 s, range 0–30 s). Controls the maximum inter-ad silence gap that will be stitched into one confirmation window. Set to 0 to disable stitching and restore the old per-silence-period behaviour.

---

## [3.4.41] - 2026-03-27

### Fixed
- **Broadcast Chains — ad break at start of ads misdetected as FAULT** — the secondary ad-break candidate test (`eval_chain`) required that *no* downstream node was faulted. In a typical chain topology (Studio Codecs → Pass-through Router → Audio Processing → Broadcast TX) the pass-through router immediately downstream of the codec stack *mirrors* the studio silence — it has no audio of its own, so it also reads below the silence threshold for the brief lag before the ad-server kicks in. This caused `_any_post_down = True`, blocking `adbreak_candidate = True` even though Audio Processing and Broadcast TX were both healthy with audio. The result: the chain went straight to FAULT (red) instead of AD BREAK (amber) for a few minutes until the 330 s confirmation window elapsed, at which point the fault self-healed — repeating on every ad break.

  Fix: changed the test from "no downstream node is faulted" to "at least one downstream node is confirmed OK". If any node further in the chain is actively carrying audio, the broadcast chain is still alive from another source (automation/ad-server) and the codec silence is an ad break. Only when *every* downstream node is also dark is it treated as a genuine cascading outage.

---

## [3.4.40] - 2026-03-27

### Fixed
- **Hub page search never worked** — `initCardSearch` used `card.innerText` which deliberately skips `display:none` elements. The stream detail panel (`.sc-detail`) is hidden by default and contains almost everything users want to search: format ("DAB", "FM"), alert details, device IDs, RTP stats, AI status history. The placeholder even advertised "FM, DAB, alerts…" none of which were reachable. Fixed: switched to `card.textContent` so all text — visible or not — is included in the match.
- **Search shows no feedback on zero results** — added an inline "No sites or streams matching X" hint row that appears inside the grid when the current query hides every card, and disappears when the query is cleared.
- **Search filter lost after polling updates** — `hubRefresh` now re-applies the active search filter after each AJAX update cycle, so cards that gain or lose matching text stay correctly filtered without requiring a keypress.
- **First site connecting never triggers page reload** — the previous `hasNew` guard required `knownIds.length > 0`, meaning a hub page loaded with 0 sites would never auto-reload when the first site connected. Removed the guard; the 30 s flood-control timer is sufficient.

---

## [3.4.39] - 2026-03-27

### Changed
- **Hub Wall — broadcast chain layout redesign** (clearer left→right signal flow)
  - Chain flow is now `flex-wrap: nowrap` with horizontal scroll — the signal path always reads left to right, never wraps onto a second line
  - Stacked-position sub-nodes replaced with compact horizontal rows (`.wc-srow`): status dot, stream name, mini level bar, dB value — all in one tight line instead of a tall column widget
  - Stack-mode label (`ANY SILENT = FAULT`) moved to the top of each stack group with a separator line, so you read the logic before the nodes
  - Position number labels (`P1`, `P2`, `P3`…) above each position column make it easy to identify where in the chain each group sits
  - Arrow glyphs between positions styled softer (muted, smaller) so node content reads first
  - Chain card minimum width widened (`360px → 460px`) to fit typical 4–5 node chains on large displays without scrolling
  - Thin horizontal scrollbar (4 px) appears inside the flow strip if a chain has many positions — non-interactive on wall kiosks, still useful on desktop

---

## [3.4.38] - 2026-03-27

### Fixed
- **Broadcast Chains — "New A/B Group" button did nothing**: All A/B group JS functions (`abgOpenNew`, `abgEdit`, `abgDelete`, `abgToggleActive`, `abgPoll`) were accidentally placed in `HUB_WALL_TPL` (the hub dashboard template) while all the HTML — the modal, the button, and the A/B group cards — is in `BROADCAST_CHAINS_TPL`. Clicking the button silently threw `ReferenceError: abgOpenNew is not defined`. Fixed by moving the entire A/B group JS block into the chains page template where it belongs, and removing the orphaned copy from the hub wall template (which also had a secondary crash: `document.getElementById('abg-save-btn')` returned null and threw on page load).
- **A/B group RX stream dropdown always empty**: `/api/chains/streams` returns `{"options":[...]}` but the JS called `.forEach()` directly on the response object (treating it as an array). Fixed: now uses `resp.options || resp || []`.
- **Save errors now surfaced**: added `.catch()` to the A/B group save fetch so network failures show an error instead of silently doing nothing.

---

## [3.4.37] - 2026-03-27

### Changed
- **Broadcast Chains — fault replay timeline redesign**
  - Per-position color palette: fault = red, last good = green, each recovery position gets its own distinct hue (amber/cyan/purple/pink/orange/teal…), applied consistently to both the timeline bar and its player row
  - Solid timeline bars: fault/last-good at full opacity, recovery bars at 60% — critical fault point stands out immediately
  - Colored label text below each timeline bar (was grey for all)
  - Colored arrows between bars, tinted to the preceding node's color
  - Colored left border on every player row — visually connects the clip back to its timeline position
  - Status pill badge replaces the tiny colored dot: "FAULT POINT", "LAST GOOD", "RECOVERY POS0" etc. in the position color
  - Verbose chain name stripped from clip labels: "CHAIN COOLFMBROADCASTCHAIN RECOVERY POS0" → "RECOVERY POS0"
  - Inline section headers group the player list: "⚡ Fault Point" / "✓ Last Good" / "🔗 Recovery Chain" (sorted order preserved)

## [3.4.36] - 2026-03-27

### Added
- **Glitch detection: four new false-positive discriminators**
  - **Recovery rate filter** — audio must snap back abruptly after the dip (measured over 0.5–3 s after recovery). Gradual recoveries (next song fading in) are rejected. Reuses the same dBFS/s threshold as onset.
  - **Silence floor requirement** (`glitch_floor_db`, default 15 dB) — the dip must reach within N dB of the silence threshold. Real dropouts go to near-silence; quiet musical passages rarely do. Configurable, 0 = disabled.
  - **Dip floor tracking** — minimum level during the dip is tracked and used for the floor check, so a dip that briefly touches the threshold but doesn't go deep is correctly rejected.
  - **Pre-dip trend rejection** (`glitch_pre_trend_db`, default 4 dB) — if the level was already declining by this many dB in the 2–5 s before the threshold crossing, the dip is treated as a content fade and ignored entirely. Catches slow fades that still appear abrupt at the exact crossing instant.
  - All three of onset rate / floor depth / recovery rate must pass for a glitch to be counted.

## [3.4.35] - 2026-03-27

### Fixed
- **Glitch detector false-triggering on song transitions** — the fade vs glitch discrimination was measuring the drop rate using the sample immediately before the threshold crossing (~50 ms ago). At that timescale, even a 4-second gradual song fade reads as 60+ dBFS/s at the instant it crosses the threshold — indistinguishable from a real glitch. Fixed by measuring the approach rate over the 0.5–3 s window before the crossing. Song fades now correctly read as 2–10 dBFS/s; real glitches (packet loss, STL, encoder) read as 40–200+ dBFS/s. Updated settings UI description accordingly.

## [3.4.34] - 2026-03-27

### Fixed
- **Low-bandwidth mode can't be unchecked (definitive fix)** — the `offsetParent` JS approach added in 3.4.33 did not work because the settings tab system hides panels via CSS class (`.pn` without `.on`), not `display:none`, so `offsetParent` was never `null`. Root cause: the settings template contains two copies of the hub panel (lines ~312 and ~557) sharing the same `<form>`. The second copy always submitted its `hub_low_bw` value regardless of visibility. Fixed by removing the duplicate `hub_low_bw` checkbox from the second hub panel entirely — the first instance (in the client/both panel) is the canonical one and only one copy should exist in the form.

## [3.4.33] - 2026-03-27

### Fixed
- **Low-bandwidth mode (and other hub checkboxes) can't be unchecked** — the settings form contains two parallel copies of the hub settings panel (one shown per mode via JS). Both copies share the same `<form>`, so a checked-but-hidden duplicate always submitted `value="1"`, overriding the user's unchecked value. Fixed by adding a form `submit` listener that disables all non-hidden inputs whose `offsetParent` is `null` (i.e. inside a `display:none` ancestor) before the form is serialised. This correctly suppresses any collapsed panel's inputs regardless of which setting is duplicated.

## [3.4.32] - 2026-03-27

### Fixed
- **Low-bandwidth mode can't be turned off from client** — heartbeat payload was reporting `self._low_bw OR cfg.hub.low_bw` (the effective value), which included the hub-pushed `self._low_bw`. This created a feedback loop: hub pushes `true` → client echoes `true` back in heartbeat → hub stores `true` forever, even after hub_site_rules was cleared. Fixed by reporting only `cfg.hub.low_bw` (the locally saved setting) in the heartbeat payload. The hub's own hub_site_rules is the authoritative source for the hub-side override.

## [3.4.31] - 2026-03-27

### Fixed
- **Low-bandwidth mode resets on restart** — `low_bw` was missing from both `save_config()` and `load_config()`, so the setting was never written to disk and always loaded as `False`. Added to both paths.

## [3.4.30] - 2026-03-27

### Fixed
- **Low-bandwidth mode — hub now aware of client-side setting**: Client heartbeat payload now includes `low_bw: true/false` (effective value combining local config and hub-pushed override). Hub uses this to:
  - **Show the `📶 Low BW` badge** even when low-bw was set locally on the client, not via hub Settings.
  - **Adjust missed-heartbeat calculation**: uses 30 s interval (not 5 s) when computing `consecutive_missed` for low-bw sites, so a 30 s gap between heartbeats no longer counts as ~5 missed.
  - **Adjust site-offline timeout**: low-bw sites are not marked OFFLINE until 90 s without a heartbeat (up from 30 s).
  - **Adjust STALE threshold**: STALE badge only shows after 60 s of silence for low-bw sites (not 10 s).

## [3.4.29] - 2026-03-27

### Added
- **Low-bandwidth badge on hub dashboard** — site cards now show a `📶 Low BW` badge (green tint) when low-bandwidth mode is enabled for that site, either via hub per-site rules or the client's local setting. Badge is added/removed dynamically by the AJAX refresh so it reflects changes without a page reload.

## [3.4.28] - 2026-03-27

### Added
- **Low-bandwidth mode** — for sites with data caps or metered connections:
  - **Per-site from hub**: Settings → Hub Server → Per-Site Alert Rules — each connected site now has a "Low-bandwidth mode" checkbox. When enabled the hub pushes `low_bw: true` in every heartbeat ACK for that site.
  - **Local on client**: Settings → Hub → Low-bandwidth mode checkbox. Effective immediately without a hub restart.
  - **Effect on client**: heartbeat interval increases from ~5 s to 30 s; automatic clip uploads and periodic clip sync are suspended.
  - **On-demand clip delivery**: clips are not lost — when a clip is viewed in Reports or fault replay, the hub creates a relay slot and the client streams the clip file on demand. `alert_wav` added to `_HANDLED_KINDS` so relay requests work. Relay timeout raised from 25 s to 35 s to accommodate the slower heartbeat.

## [3.4.27] - 2026-03-26

### Added
- **Persistent hub mini player** — the live audio mini player now survives navigation between hub pages (dashboard, reports, broadcast chains, plugins). When a stream is started the URL, title, and site name are saved to `sessionStorage`. The player is injected by `topnav()` on every hub page; on load it restores from `sessionStorage` and reconnects to the same relay slot URL automatically. Stopping via the ⏹ button or the stream ending clears `sessionStorage` so the player does not reappear. Duplicate mini-player CSS and HTML removed from `HUB_TPL` — now emitted exclusively by `topnav()`.

## [3.4.26] - 2026-03-26

### Fixed
- **PTP monitor accuracy and false warnings**:
  - **`pmc` integration** — on startup a background thread polls `pmc -u -b 0 'GET CURRENT_DATA_SET'` every 10 s. If linuxptp/ptp4l is running, this gives the actual path-delay-compensated `offsetFromMaster` and `meanPathDelay` directly from the PTP slave daemon. Data source switches automatically; logged as "switching to accurate slave-mode data".
  - **Passive-mode jitter threshold raised** — the raw-socket passive listener has no `Delay_Req` so its jitter figure reflects path-delay variance, not clock instability. Jitter warn raised from 2 ms → 50 ms in passive mode to stop false `warn` states (6 ms passive jitter is normal). In `pmc_mode` the configured threshold is used unchanged.
  - **Template state colour bug fixed** — was checking `ptp.state=='locked'` which can never match (states are `ok/warn/alert/lost/idle`), so state was always shown in amber. Now uses a proper three-way: ok→green, alert/lost→red, otherwise amber.
  - **Offset label** — shown as `Offset~` with a tooltip explaining path-delay is not compensated in passive mode; shown as `Offset` in pmc mode. Path delay displayed separately when available.
  - **`passive` label** shown next to state when not in pmc mode so it's clear the data is approximate.

## [3.4.25] - 2026-03-26

### Fixed
- **Broadcast chain comparator chip colour for processed paths** — chain diagram used fixed thresholds (`pct>=80` → green) regardless of whether a processor was detected. A healthy 80% processed path showed orange. Now uses relaxed thresholds for processed paths (≥50% → green, ≥30% → amber) matching the Python-side labels, and tightened thresholds for clean paths (≥65% → green, ≥40% → amber). Tooltip text updated from stale "GCC-PHAT · envelope/waveform" to "block-RMS processed/clean".

## [3.4.24] - 2026-03-26

### Changed
- **Stream Comparators panel redesign** — cards now match the hub's stream-card design language: `background:#123764`, `border:1px solid var(--bor)`, `border-radius:10px`, `box-shadow`, and a coloured left-border accent (green/muted/red based on status). Header row has a status dot + separator line. Correlation bar uses a flex layout with a wider track.
- **Status colour fixed** — was checking `c.status == 'OK'` which always failed for the verbose status strings (e.g. `"OK (excellent block-RMS processed corr)"`), causing every comparator to show in red. Now uses `c.status.startswith('OK')` → green, "Finding…" → muted, otherwise red.
- **Processed-path correlation bar uses correct thresholds** — on processed paths the bar turns green at ≥50% (matching the relaxed Python thresholds) instead of showing misleading amber/orange on a healthy path. Unprocessed paths retain the original ≥65% threshold for green.

## [3.4.23] - 2026-03-26

### Fixed
- **StreamComparator — startup false alerts for gain-shift and low correlation**: Extended the startup grace period from 20 s to 35 s and applied it to both the gain-shift and low-correlation alerts (previously only low-correlation was gated). Root cause: `_baseline_gain_diff` was initialised lazily on the very first `update()` call while the level estimate was still settling, locking in a wrong reference and immediately triggering a "Gain shift" alert. Fixed by initialising `_baseline_gain_diff = None` in `__init__` and only setting the baseline after the 35 s warmup window.

## [3.4.22] - 2026-03-26

### Added
- **Automatic 24-hour pruning of uploaded clips** — the client now runs a `ClipPrune` background task every ~100 s (offset from `ClipSync` by 50 s to avoid races). Any clip that has a `.hub` upload-confirmed marker and is older than 24 hours is deleted, along with its `.hub` and `.meta` sidecar files. Once a clip is safely on the hub it no longer needs to live on disk. Applies retroactively — `.hub` markers written by previous versions are equally eligible, so clips already uploaded before this update are cleaned up on the first pass without any manual action.

## [3.4.21] - 2026-03-26

### Fixed
- **StreamComparator — relaxed thresholds for processed audio paths**: When `processed=True` (AGC/limiting detected), correlation label thresholds are lowered (excellent ≥75%, good ≥50%, weak ≥30%) and the low-correlation alert fires below 30% instead of 40%. Previously 61–70% on a processed path was labelled "weak" and triggered a false alert; it is now correctly "good".
- **StreamComparator — startup grace period**: Low-correlation alerts are suppressed for the first `_CORR_BLOCKS × 100 ms + 5 s` (~20 s) after the comparator is created. During this window there are not yet enough blocks for a stable Pearson estimate, causing spurious "Low corr 0.09" alerts on every restart.

## [3.4.20] - 2026-03-26

### Fixed
- **StreamComparator — block-RMS xcorr replaces GCC-PHAT + multi-window envelope**:
  - **GCC-PHAT** was returning lag=0 for heavily processed broadcast audio (AGC/limiting destroys phase coherence → flat cross-spectrum → argmax trivially returns 0). Replaced with **block-level RMS cross-correlation**: audio is chunked into 100 ms blocks, block-RMS sequences are cross-correlated. Programme structure (speech/music/silence transitions) survives any amount of processing and produces a clear xcorr peak at the correct delay.
  - **Negative lag formula bug** in `_block_delay`: `-(argmax + 1)` was used instead of `-(max_lag - argmax)`. For an actual lag of −27 blocks with `max_lag=100` and `argmax=73`, the code returned −74 instead of −27. Now correctly computes `best_neg_lag = -(max_lag - best_neg_i)`.
  - **Block Pearson alignment direction bug** in `_block_pearson`: for lag=+L, the code used `a = pre_blocks[lag:]` paired with `post_blocks[:n]`, which pairs content ~5.4 s apart instead of matched content. Correct: `a = pre_blocks[-(n+lag):-lag]`, `b = post_blocks[-n:]`. Same fix applied symmetrically for negative lag.
  - **2-second sub-window instability** removed: 100 ms smoothed envelope has ~5 Hz bandwidth → only ~10 independent samples per 2 s window → Pearson std error ±0.22 → frequent 0% readings. Replaced with a single Pearson over `_CORR_BLOCKS=150` blocks (15 s) for a stable estimate.
- **Processor detection thresholds broadened**: compression ratio window widened to `[0.35, 2.80]` (was `[0.50, 2.0]`) to avoid false positives on stations with moderate processing.

## [3.4.19] - 2026-03-26

### Changed
- **StreamComparator — full algorithm rewrite for accuracy**:
  - **GCC-PHAT delay detection** replaces plain xcorr. The phase-transform whitens the cross-spectrum so the lag peak is sharp and accurate even through heavily processed/compressed audio.
  - **Post-alignment Pearson correlation** replaces the xcorr-peak-divided-by-length heuristic for a mathematically correct 0–1 agreement score on clean paths.
  - **Envelope correlation fallback** — for processed paths, correlates amplitude envelopes (100 ms smoothing) instead of raw waveforms. Programme content shape (speech/music/silence timing) is preserved through AGC/compression/limiting where sample-level correlation is inherently low.
  - **Multi-window median stability** — correlation is computed over 5 non-overlapping 2-second windows and the median is reported. Removes transient spikes from ad break edges and short-term mismatches.
  - **Automatic processor detection** via two independent signals: (1) gain gap `|pre_dBFS − post_dBFS| > 8 dB`, (2) compression ratio `post_env_std / pre_env_std < 0.50` (heavy limiting squashes envelope variance). Either signal flags `processed=True` and triggers the envelope correlation path.
  - New exported fields in heartbeat: `processed` (bool), `compression_ratio` (float).
  - Chain diagram badge updated: `🎧⚙` for local audio with processor detected, with compression ratio in tooltip. `🎧` for clean local audio.

## [3.4.18] - 2026-03-26

### Fixed
- **Chain comparators broken for stack positions** — when a comparator referenced a position group containing multiple nodes (a "stack"), `chain_nodes[fi]` was `{type:'stack', nodes:[...]}` which has no `site`/`stream` keys. `_chain_correlate_nodes` returned `no_data` for every stack-based comparator pair. Fixed by resolving stack nodes to their first sub-node before correlation.

### Added
- **On-demand client-side comparators** — when the hub detects a broadcast-chain comparator pair where both nodes are on the same remote site, it automatically sends a `cmp_pair` command to that site via the heartbeat ACK. The client spawns a `StreamComparator` locally (real-time FFT cross-correlation at full audio resolution). Results appear in the next heartbeat and the hub uses them in place of 1-minute metric_history averages. The chain diagram shows the **🎧** badge indicating live audio data with latency (ms), gain difference (dB), and alignment status in the tooltip. Re-requests are rate-limited to once every 5 minutes, only when the comparator is absent (e.g. after a client restart).

## [3.4.17] - 2026-03-26

### Added
- **Local audio comparator shortcut in chain correlator** — when both chain comparator nodes live on the same remote site, `_chain_correlate_nodes` now checks the site's live heartbeat comparator data first. Real-time cross-correlation (correlation, delay_ms, gain_diff_db, aligned) from the client is used in place of metric_history averages. Chain diagram shows 🎧 badge with tooltip showing latency and gain diff.

## [3.4.7] - 2026-03-26

### Fixed
- **Hub Reports — AUDIO_GLITCH / AUDIO_GLITCH_SUSTAINED / AUDIO_FLATNESS missing from Type filter dropdown** — the type filter is built from the union of dynamic event types in the current window plus a fixed constant set (`_SILENCE_TYPES`). The three new glitch/flatness types were never added to that constant, so the dropdown only showed them when a glitch event happened to be within the most recent 50 heartbeat events. Fixed by adding all three to the constant set so they always appear in the filter.
- **Hub not forwarding glitch/flatness alerts from remote clients** — `AUDIO_GLITCH_SUSTAINED` and `AUDIO_FLATNESS` were not in `_HUB_DEFAULT_FORWARD_TYPES`, so the hub silently dropped them instead of forwarding via email/push/webhook. `AUDIO_GLITCH` (per-glitch clips, no notification intended) is intentionally left out of defaults but is now in `_ALL_ALERT_TYPES` so it can be selectively enabled per-site in hub site rules.

## [3.4.6] - 2026-03-26

### Added
- **Sustained glitching escalation alert** (`AUDIO_GLITCH_SUSTAINED`) — a second, louder alert tier that fires when glitches are hammering continuously. Uses a configurable longer look-back window (default 10 dropouts in 10 min). Includes an audio clip. Re-fires every 10 minutes while the condition persists. The existing first-tier alert (`AUDIO_GLITCH`) is unchanged. The timestamp prune window is now extended to cover whichever of the two windows is longer so both tiers always have the data they need.

## [3.4.5] - 2026-03-26

### Changed
- **Glitch detection: per-glitch audio clip capture** — every confirmed glitch now saves a short audio clip (up to 12 s, rate-limited to one clip per 30 s) and logs it to Hub Reports as `AUDIO_GLITCH`. The clip captures context before, during, and after the dropout so the exact audio artifact is preserved. External notifications (push/email/webhook) are unchanged — they still only fire after the N-glitches-in-window threshold is reached. This separates *evidence capture* (always, silent) from *alerting* (only when persistent).

## [3.4.4] - 2026-03-26

### Added
- **Audio glitch / short dropout detection** — new per-input feature (`glitch_detect`). Maintains a rolling 60-second level reference; when a sample drops more than `glitch_drop_db` (default 18 dB) below that reference and recovers within `glitch_max_seconds` (default 8 s), it is counted as a glitch rather than silence. Fires `AUDIO_GLITCH` alert after N events in a configurable sliding window. Longer drops still trigger the existing silence alarm.
- **Audio flatness / static detection** — new per-input feature (`flatness_detect`). Monitors the level range (max−min) over a rolling window; if the stream stays above the silence threshold but has less than `flatness_min_range_db` (default 2 dB) of variation for `flatness_min_seconds` (default 300 s), fires `AUDIO_FLATNESS` alert (and recovers automatically when dynamics return). Catches constant static, frozen audio, stuck encoders, and looping content.
- **Chain diagram badges** — both signals surface visually on broadcast chain nodes: ⚡ glitch count (last 5 min) and 〰 Static. Chain header shows ⚡ Glitching / 〰 Static detected summary badges when any node in the chain is affected.
- **Hub telemetry** — glitch count and flatness state are included in every client heartbeat, stored in `metric_history` as `glitch_count` and `flatness_flag` (local and remote), and available on the hub chain diagram in real time.
- **Settings UI** — both features are configurable per-input under Settings → Inputs with show/hide sub-fields toggled by their enable checkboxes.

## [3.4.3] - 2026-03-26

### Fixed
- **Chain comparator always shows 100%** — `_chain_correlate_nodes()` used silence/activity agreement as its primary metric. For continuous 24/7 broadcast streams (both streams always carrying audio) the silence agreement is trivially 100%, so the comparator always returned 100% regardless of whether the two nodes carried the same content. Root cause: the `delta_r` first-difference Pearson was only an *additive bonus* (up to +20 pp) and could never reduce the base `silence_pct * 100` score.

  Fix: replaced the scoring algorithm entirely:
  - **Primary metric (60% weight):** first-difference Pearson (`delta_r`) computed across *all* common 1-minute buckets. Measures whether level *changes* track together, unaffected by AGC/limiting static gain offsets.
  - **Secondary metric (40% weight):** raw-level Pearson (`raw_r`). Useful for lightly-processed streams.
  - Both metrics mapped to `[0, 100]` with negative correlation → 0% (clearly different sources).
  - **Silence-schedule penalty:** only applied when both streams genuinely have silence events (>5% of buckets silent). When streams disagree on *when* silence occurs, score is penalised up to −50 pp. For 24/7 continuous audio (no silence events) this penalty is never applied.
  - Window increased from 10 → 60 minutes; minimum samples raised from 5 → 10 for a more stable estimate.

## [3.4.2] - 2026-03-26

### Added
- **9 broadcast chain reliability improvements** — recovery confirmation window, silence threshold hysteresis, post-recovery fast re-fault, per-chain notification cooldown, shared-fault aggregation, predictive degrading alert, cascade suppression, persistent fault log with new columns (`adbreak_overshoot`, `cascaded_from`, `message`), and node-level offline notification.

## [3.4.1] - 2026-03-26

### Fixed
- **Alert log prune failure now logged** — `_alert_log_prune()` was catching all exceptions silently (`except: pass`). If pruning ever failed (permissions error, disk full, file corruption) the alert log would grow unboundedly with no indication. Exception is now printed to the app log.
- **Hub proxy clip errors now appear in the app log** — several `print()` calls in the clip relay path (`hub_proxy_alert_clip`) were writing to raw stdout instead of `monitor.log()`, so errors never appeared in the Settings → Log panel.
- **Mini-player hover tooltip on long stream names** — the bottom mini-player bar truncates long stream names with ellipsis but had no `title=` attribute, so hovering showed nothing. Both the clip player and hub live player now set `title=` on the stream name and site/timestamp elements so the full text is always accessible on hover.

## [3.4.0] - 2026-03-26

### Changed
- **Remove pyrtlsdr FM backend** — the pure-Python RTL-SDR demodulator (`_run_fm_pyrtlsdr`, ~390 lines) has been removed. The only supported FM backend is now `rtl_fm`. Removes the backend selector from the FM input settings UI, all related JavaScript, and the pyrtlsdr dependency entry from the system requirements page.

### Fixed
- **Hub Reports clip player route broken for stream names containing "/"** — Jinja2's `urlencode` filter uses `safe='/'`, leaving literal slashes unencoded in clip URLs. The route used `<stream_name>` (no `path:` converter) so Flask split at the slash and returned 404. Route changed to `<path:stream_filename>`; function splits on last `/` to recover stream name and filename. Mobile API `__wrapped__` call updated accordingly.
- **Hub Reports "(hub)" clips returned "Site not found"** — clips recorded on the hub are tagged `_site="(hub)"` in the reports. `hub_server.get_site("(hub)")` returns None (the hub isn't a registered remote client), causing the function to bail before checking the local `alert_snippets` cache. Fix: `(hub)` is now treated as a pseudo-site, bypassing `get_site` and setting `client_addr=""`.
- **Hub Reports remote chain clips 504** — uploaded chain-fault clips are stored as `alert_snippets/{safe(site)}_{safe(stream)}/` but the alert-log entry uses `stream = f"{site} / {stream}"`. For `(hub)` pseudo-site clips, the cache key was built incorrectly as `hub_...`, missing the underscore separator. Fix: split on ` / ` to recover the real site and stream names.
- **Two hub API routes missing `<path:>` converter** — `POST /api/hub/site/<site_name>/update` and `POST /api/hub/site/<site_name>/relay_bitrate` were missing `path:` on the `site_name` parameter, which would break for site names containing `/`. Fixed to `<path:site_name>` to match all other hub site routes.

## [3.3.167] - 2026-03-26

### Fixed
- **Hub Reports — remote chain clips 504 (still can't play after 3.3.166)** — when a client uploads a chain-fault clip, the hub stores it as `alert_snippets/{safe(site)}_{safe(stream)}/` (e.g. `London-Livewire_DowntownCountryDAB/`) and writes an alert-log entry with `stream = f"{site} / {stream}"` (e.g. `"London - Livewire / Downtown Country DAB"`). The clip-serve route tags all hub alert-log events with `_site = "(hub)"`. The local cache lookup was building the key as `hub_{safe("London - Livewire / Downtown Country DAB")}` = `hub_London-LivewireDowntownCountryDAB`, which doesn't match the stored `London-Livewire_DowntownCountryDAB`. Fix: for `(hub)` pseudo-site clips where `stream_name` contains ` / `, split on the first ` / ` to recover the real site and stream, then build the key as `{safe(real_site)}_{safe(real_stream)}`.

## [3.3.166] - 2026-03-26

### Fixed
- **Hub Reports — clip player still 404 for hub-side clips** — clips recorded directly on the hub are tagged `_site = "(hub)"`. The route was fixed in 3.3.165 to handle `/` in stream names, but `hub_server.get_site("(hub)")` returns `None` (the hub isn't a registered remote client), causing the function to return "Site not found" before ever reaching the local `alert_snippets` cache where hub clips live. Fix: `(hub)` is now recognised as a pseudo-site; the `get_site` check is skipped and `client_addr` is set to empty string, allowing the local cache lookup to proceed normally.

## [3.3.165] - 2026-03-26

### Fixed
- **Hub Reports — clip player errors immediately for streams with "/" in the name** — stream names like "Northern Ireland DAB / Absolute 90s" contain a literal `/`. The clip URL was built in the template using Jinja2's `urlencode` filter, which leaves `/` unencoded (it uses `safe='/'`). The Flask route used `<stream_name>` (no `path:` converter), so the unencoded slash was treated as a path separator, routing to a 404 before the audio player even loaded any data. Fix: route changed to `<path:stream_filename>`; the function now splits on the last `/` to recover `stream_name` and `filename`. The direct `__wrapped__` call from the mobile API endpoint is updated to pass a combined path string.

## [3.3.164] - 2026-03-26

### Fixed
- **Hub Reports — Clip column pushed off right edge of page** — the actual problem was never the filter bar. The events table uses `table-layout:auto` (default), so the browser sizes columns based on content. Long stream names ("CoolFM - MSAPPENCLON15") and chain badges ("⛓ Cool FM Broadcast Chain", `white-space:nowrap`) forced the Stream and Chain columns wider than their specified widths, pushing the Clip column off screen. The table wrapper had `overflow:hidden` which clipped the column entirely. Fix: changed `.table-wrap` to `overflow-x:auto` (table scrolls within its container when wider than the viewport), and added `max-width:180px;overflow:hidden;text-overflow:ellipsis` to `.chain-badge` so long chain names are capped and shown with ellipsis rather than forcing the column to expand.

## [3.3.163] - 2026-03-26

### Fixed
- **Hub Reports — filter overflow persists despite CSS constraints** — Chrome's `datetime-local` input has a browser-enforced minimum intrinsic size for its date/time picker UI. This minimum overrides CSS `width` and `max-width` on the input element itself: the page initially renders with the CSS-specified width (correct), then snaps to the browser minimum ~100 ms later (broken). No CSS approach applied to the input itself can reliably prevent this. Fix: the two `datetime-local` inputs have been moved out of `.filters` entirely into a new `.filter-sub` row below the filter bar, alongside the "Clips only" checkbox and the event count. `.filters` now contains only `<select>` elements (Site, Stream, Type, Chain) whose width is fully controllable via CSS. The `.filter-sub` row uses `flex-wrap:wrap` so datetime inputs, checkbox, and count are always visible regardless of viewport width.

## [3.3.162] - 2026-03-26

### Fixed
- **Hub Reports — filter bar still causing horizontal page overflow** — `datetime-local` inputs resist `max-width` in many browsers (they have a browser-enforced minimum display width for the date/time text). Even with `max-width:175px` set, the inputs rendered wider on some systems, overflowing `.filters` → overflowing `<main>` → creating a page-level horizontal scrollbar. When the page scrolled right, all content shifted including the "Clips only" row beneath the filter bar. Fix: added `overflow-x:hidden` to `<main>` (prevents the page ever getting a horizontal scrollbar), added `overflow-x:auto` to `.filters` (the filter bar itself scrolls internally if items are too wide), and pinned datetime inputs to `width:155px` (not just `max-width`) to override the browser minimum.

## [3.3.161] - 2026-03-26

### Fixed
- **Hub Reports — "Clips only" still pushed off right edge** — the previous fix constrained input widths but left the checkbox inside the flex row. On a typical 1280 px display the six filter controls (three selects + two datetime inputs + Clips checkbox) still exceeded the available row width. Fix: "Clips only" checkbox is now in its own row below the filter bar, sharing a flex container with the "N events shown" count (left-aligned checkbox, right-aligned count). The filter bar itself now only contains the select and datetime-local controls, which always fit within 1280 px.
- **Broadcast Chains — fault log auto-refresh jumps scroll and destroys open replay panel** — the staggered refresh used `[20000,40000,70000].forEach(...)` inside `loadFaultLog`, scheduling three new timeouts *every* call. This caused exponential growth (calls at 20 s, 40 s, 60 s, 70 s, 80 s, 110 s …), repeatedly firing `body.innerHTML=html` which destroyed any open replay panel ("closes the dropdown") and reset the browser scroll position to the top. Fix: each call now schedules at most *one* next refresh using a `_nRefresh` counter; total auto-refreshes are capped at three (at approximately +20 s, +60 s, and +170 s from initial open). Additionally, `window.scrollY` is saved and restored around the `innerHTML` replacement, and replacement is skipped entirely when a replay panel (`[id^="rrow_"]`) is open inside the fault log body.

## [3.3.160] - 2026-03-26

### Fixed
- **Hub Reports — "Clips only" filter pushed off right edge** — the filter row contained Site, Stream, Type, Chain, two `datetime-local` inputs, the Clips checkbox, and the row count all in a single flex row. The datetime inputs had no width constraint (browsers render them at ~195 px each), which could overflow the container and push the Clips checkbox off-screen on typical laptop widths. Fix: added `max-width:160px` on filter selects and `max-width:175px` on datetime inputs, moved the "N events shown" count out of the flex row into a dedicated `filter-row-count` div below the filter bar.
- **Broadcast Chains — clicking a fault log row scrolls to top and "closes" the fault panel** — the fault log row click handler called `_enterHistMode(ts)` then `banner.scrollIntoView({behavior:'smooth', block:'nearest'})`. Because the history banner is at the top of the page and the user was scrolled down to the fault log, the scroll jumped to the top and moved the open fault log panel out of view. Fix: removed the auto-scroll; instead the history banner gets a 2 s blue highlight ring to confirm that history mode was entered, without moving the page scroll position.

## [3.3.159] - 2026-03-26

### Fixed
- **Hub Reports — Chain Faults always showed "0"** — `hub_reports()` was only collecting events from site heartbeat `recent_alerts` payloads. `CHAIN_FAULT` events are generated on the hub by `_fire_chain_fault()` → `_alert_log_append()` and are never included in any site's `recent_alerts` in pure hub-mode deployments. Fix: also load the hub's own `alert_log.json` (via `_alert_log_load(2000)`) and merge those events into `all_events`, tagged `_site="(hub)"`. Events already present from a site heartbeat are skipped via `seen_ids` deduplication, so both-mode nodes are not double-counted.
- **Hub Reports — Silence type missing from type filter dropdown** — the type dropdown was built purely from event types present in the current `all_events` window (last 50 events per site from heartbeat). On busy systems, silence events could be displaced from the 50-event window and the SILENCE/STUDIO_FAULT/STL_FAULT/TX_DOWN/DAB_AUDIO_FAULT/RTP_FAULT types would disappear from the filter. Fix: the silence-family types are now always included in `type_names` regardless of whether events with those types are present in the current window.

## [3.3.158] - 2026-03-26

### Changed
- **Type and spacing scale** — added a full CSS custom property scale to the Dashboard and Hub `:root` blocks (`--fs-xs/sm/md/base/lg`, `--lh:1.45`, `--sp-xs/sm/md/lg`, `--r-sm/r/r-lg`). Applied selectively to key components:
  - `body` now has `line-height:var(--lh)` globally
  - `.row` — padding 4px → 5px, font-size uses `--fs-md`, line-height added
  - `.hist` / `.hev` — line-height added, max-height 90px → 110px (shows ~2 more events)
  - `.st` section headers — weight 600 → 700, letter-spacing .06em → .08em, bottom border added for visual anchoring, margin top 14px → 16px
  - `.mr` (PTP/Hub Connection rows) — padding 3px → 5px, line-height added
  - `.grid` card gap 14px → 16px
  - Hub `.sc-row` — margin-top 4px → 5px, line-height added; `.hev` line-height added
  - CSS variable scale available for future use throughout all templates

## [3.3.157] - 2026-03-26

### Changed
- **SVG icons replace emoji in Dashboard stream cards** — the four most prominent emoji in the dashboard stream card UI have been replaced with clean inline SVG icons that render consistently across all platforms:
  - `⬇ Clip` → download arrow SVG
  - `▶ Live` → play triangle SVG
  - `💾 Saved Clips` → floppy disk / save SVG
  - `📋 Recent Events` → list SVG
- Added `_SVG` icon dict and `@app.context_processor` so `{{icons.NAME|safe}}` works in all templates going forward — future icons can be added to one place.
- Added `.ic` CSS utility class for correct inline vertical alignment of SVG icons.

## [3.3.156] - 2026-03-26

### Changed
- **CSS extraction — inline styles replaced with named classes** — extracted the most frequently repeated inline style blocks from the Dashboard and Hub stream card templates into named CSS classes:
  - `.card-dev` — device label in Dashboard card headers (6-property truncation style)
  - `.lbar-mode-label` — RMS/Peak toggle label on the level bar
  - `.rv-mu` — muted small-text value spans (Format row etc.)
  - `.rv-mono` — monospace muted value spans (LUFS row)
  - `.sc-dev` — device label in Hub stream card headers
  - `.sc-name strong` — Hub stream card name font size moved to CSS rule
  - No visual change; HTML templates are cleaner and easier to maintain.

## [3.3.155] - 2026-03-26

### Changed
- **Dashboard — PTP Clock and Hub Connection cards collapse by default** — the PTP Clock and Hub Connection cards at the bottom of the Dashboard now start collapsed, showing only the header (status dot + name). A `▾` chevron expands the full detail. Expand state persisted in `localStorage`. This cleans up the dashboard tail for the common case where these panels don't need constant attention.

## [3.3.154] - 2026-03-26

### Added
- **Hub page — "Expand All / Collapse All" button per site** — each site's summary bar now has an `⊞ Expand All` button (right-aligned). Clicking it expands every stream card in that site at once. When all cards are already open it reads `⊟ Collapse All` and collapses them all. The label also updates when individual cards are toggled so it always reflects the current state.

## [3.3.153] - 2026-03-26

### Changed
- **Hub page stream cards collapse by default** — stream cards on the Hub page now start collapsed showing only the name, status dot, device label, level bar and 24h timeline. A `▾` chevron in the card header expands/collapses all details (info rows, AI status, Now Playing, listen strip, clips, event history, signal history chart). Expand state persisted per-card per-site in `localStorage`. Removed the hard-coded `min-height` on stream cards so collapsed cards are compact.

## [3.3.152] - 2026-03-26

### Changed
- **Stream cards collapse by default — expand with ▾ button** — every input card on the Dashboard now starts collapsed, showing only the header (name, status dot, device label) and the live level bar. A `▾` chevron button in the card header expands/collapses the full detail area (info rows, AI status, Now Playing, listen strip, saved clips, event history). Expand state is persisted per-card in `localStorage` so cards stay open across page reloads. An `⋮` overflow menu replaces the inline Edit button, keeping the header uncluttered while still providing quick access to the edit page.

## [3.3.151] - 2026-03-26

### Fixed
- **Nav dropdown disappears when moving mouse into it** — a 6 px gap between the trigger button and the dropdown panel caused the hover state to break mid-travel. Fixed with a `::before` pseudo-element on `.ss-pdrop` that invisibly bridges the gap. No visual change.

## [3.3.150] - 2026-03-26

### Changed
- **Navigation restructured — grouped dropdowns replace flat button row** — the top nav previously listed every page as a flat row of buttons (Dashboard, Inputs, Reports, SLA, Hub, Hub Reports, Broadcast Chains, Plugins…) which grew unmanageable with plugins. Replaced with four grouped dropdowns:
  - **Monitor ▾** → Dashboard, Inputs
  - **Reports ▾** → Reports, SLA
  - **Hub ▾** → Hub, Hub Reports, Broadcast Chains (hub/both mode only)
  - **Plugins ▾** → existing plugin list (unchanged)
  - Settings and Logout remain as direct buttons
  - Parent group button highlights (`nav-active`) when any child page is active
  - Dropdown CSS promoted to always-on and shared across all groups

## [3.3.149] - 2026-03-26

### Improved
- **Chain fault alerts now report whether downstream nodes still have audio** — previously the fault message said "X downstream position(s) may also be affected" regardless of actual downstream state. The note now distinguishes three cases:
  - *All downstream still up* → `"Audio still present downstream: TX Output, DAB Mux."`
  - *Mixed* → `"Audio still present at TX Output; DAB Mux also affected."`
  - *All downstream also down* → `"X downstream position(s) also affected."`

  This immediately tells the engineer whether the fault is isolated (signal is still on-air somewhere) or has taken down the entire chain end-to-end.

## [3.3.148] - 2026-03-26

### Added
- **Configurable fault-shift grace window per chain** — a new **"Fault shift grace (seconds)"** field in the chain builder (default 0). When the fault position shifts during the confirmation window, this controls how much time the new fault position gets before the alert fires:
  - **0 (default)** — keep the original clock running (3.3.147 behaviour). Best for chains where upstream nodes have intermittent program breaks that would otherwise delay the alert indefinitely.
  - **> 0** — give the new fault position that many seconds before firing (e.g. set to 20 to restore pre-3.3.147 legacy behaviour). Useful if your chain has nodes with genuine heartbeat lag where the old grace window was intentional.

## [3.3.147] - 2026-03-26

### Fixed
- **Genuine chain fault never firing when upstream nodes have intermittent breaks** — the chain monitor runs every 10 s and finds the *first* faulted node. If a downstream node (e.g. a DAB output) has a continuous silence fault, but upstream nodes have occasional short program breaks, the `fault_index` shifts every time a poll catches an upstream node mid-break. The shift handler was backdating `_chain_fault_since` to `now − (min_fault_secs − 20 s)` (a "grace window"), effectively resetting the confirmation timer on every shift. If breaks shift the fault index more often than every 20 s, the timer perpetually resets and the chain **never** fires — even for a node that has been silent for hours.

  The individual stream `DAB_AUDIO_FAULT` alert fired correctly (via `analyse_chunk`) but the `CHAIN_FAULT` never triggered, making the fault look like a one-off stream alert rather than a chain-level problem.

  Fix: when the fault position shifts during the pending window, update the stored fault index for reporting purposes but **leave `_chain_fault_since` unchanged**. The chain has been broken somewhere continuously; the confirmation clock runs from first detection regardless of which node happens to be first-faulted on each poll. The log line now shows `(Xs / Ys elapsed)` so the progression is visible.

## [3.3.146] - 2026-03-26

### Fixed
- **Recovery clip contained almost no restored audio** — the `silence_end` clip introduced in 3.3.145 was saved at the exact frame audio resumed. The rolling buffer at that instant was still almost entirely silence, so the clip showed the outage but only a sample or two of restored audio.

  Fix: the recovery save is deferred by **85 % of `alert_wav_duration`** seconds on a short daemon thread. When the save fires, the buffer contains a brief silence tail (≈15 % of the clip for context) followed by a substantial stretch of restored audio. Example: with a 30 s clip setting, the thread waits 25.5 s and the clip ends 25.5 s into the recovery.

## [3.3.145] - 2026-03-26

### Fixed
- **Silence clips always ending before the silence ends** — the clip was saved the moment silence was *detected* (after `silence_min_duration` seconds). The recording captured audio leading up to the fault onset but the silence was still in progress when the clip ended, making it useless for diagnosing the recovery.

  Fix: when audio resumes after a silence fault, a second **recovery clip** (`silence_end`) is automatically saved. The rolling audio buffer at that moment contains the tail of the outage plus the instant audio comes back, so the clip spans the silence boundary. A matching "Audio restored" history entry is added alongside it. The original start clip is unchanged — together the two clips bracket the full event.

## [3.3.144] - 2026-03-26

### Fixed
- **Chain fault history clips never appearing** — the back-patch block in `hub_clip_upload` had no try/except. Any unhandled exception (e.g. `hub_server` temporarily None at startup, DB contention) caused Flask to return HTTP 500. The client never received a 200, never wrote its `.hub` marker, and kept retrying. Each retry re-ran `_alert_log_append` (adding a duplicate Reports entry) but the back-patch kept crashing → clips accumulated in Reports, fault history showed "No clips" forever.

  Fix:
  - Wrapped the entire back-patch block in `try/except` with a clear `[Hub] Clip back-patch ERROR` log line. Any exception is now caught; the function always returns 200 so the client writes its `.hub` marker and stops retrying.
  - Added an explicit `hub_server is None` guard with its own log line before touching `hub_server._chain_fault_log`.

- **Fault history panel misses staggered clips** — the per-position save stagger added in 3.3.143 means the last clip in a large chain can arrive 30–60 s after the fault. The single 15-second one-shot refresh fired before the last clips arrived. Replaced with three refreshes at 20 s, 40 s, and 70 s so the panel picks up all clips without manual close/reopen.

## [3.3.143] - 2026-03-26

### Fixed
- **Chain fault cascade — CPU spike triggering more faults** — when a chain went down, every node simultaneously saved and uploaded a clip. The burst of concurrent WAV→MP3 compressions spiked CPU enough to cause RTP packet loss, which in turn triggered new chain faults and generated another wave of clips, creating a runaway cascade.

  Three changes to break the cycle:

  1. **Per-position save stagger (`_CLIP_SAVE_STAGGER = 1.5 s`)** — each chain node's clip save is offset by `pos × 1.5 s` so saves are spread across time rather than all firing at once. A 5-node chain that previously all saved at T+5 s now saves at T+5 s, T+6.5 s, T+8 s, T+9.5 s, T+11 s.

  2. **Serial clip uploads (`_clip_upload_sem = 1`)** — reduced from 3 to 1. WAV→MP3 compression is CPU-heavy; allowing 3 simultaneous compressions was the primary cause of the load spike. Clips upload sequentially; the extra seconds of latency is preferable to triggering further faults.

  3. **Auto-clip-queue drain capped at 2 per heartbeat** — the `_hub_clip_queue` drainer previously spawned one upload thread per queued clip all at once. Now at most 2 clips are dispatched per 10 s heartbeat cycle; remaining items drain on subsequent heartbeats.

## [3.3.142] - 2026-03-26

### Fixed
- **Chain clips missing from fault history after sync re-upload** — `_sync_pending_clips` previously uploaded every clip with empty `chain_id`/`chain_name`, so the hub routed them all to Reports-only ("Clip received without chain_id"). Root cause: when the primary upload failed (e.g. network timeout), the chain metadata was never persisted alongside the WAV, so the sync had no way to re-attach it.

  Fix: `_cmd_save_clip` now writes a `.meta` JSON sidecar (e.g. `20260326-072351_chain_DowntownRadioBroadcastChain_pos3.meta`) next to the WAV immediately after saving, containing `chain_id`, `chain_name`, `entry_id`, `node_label`, `pos`, `status`, and `level_dbfs`. `_sync_pending_clips` reads the sidecar when present and passes the full metadata to `_upload_clip_inner`, so re-uploaded chain clips correctly appear in the fault history panel.

- **`.hub` marker path fragility** — changed `clip_path[:-4] + ".hub"` to `os.path.splitext(clip_path)[0] + ".hub"` in both `_upload_clip_inner` and `_sync_pending_clips` for robustness.

## [3.3.141] - 2026-03-26

### Removed
- **Clip Format setting** — the WAV/MP3 clip format selector has been removed from Settings. Local clips are always saved as WAV (for compatibility and lossless preservation). Upload compression is handled automatically: WAV clips larger than ~200 KB are compressed to MP3 before upload regardless of any setting. The `clip_format` field is removed from `AppConfig`, the config serialiser, and the Settings POST handler.

## [3.3.140] - 2026-03-26

### Fixed
- **Clip audio not playing on hub** — root cause identified: nginx's default `client_max_body_size` of 1 MB rejected large WAV clips (a 30-second clip is ~2.75 MB). The upload silently failed with HTTP 413, so the clip never landed on the hub disk and the audio player returned a 404. Fix: WAV clips larger than ~200 KB are now automatically compressed to MP3 before upload (30 s WAV → ~350 KB MP3, well under the limit). If no MP3 encoder is available, the WAV is still sent and a log message explains how to raise `client_max_body_size` in nginx.
- **Hub proxy serves MP3 clips regardless of alert-log extension** — `hub_proxy_alert_clip` now checks both the original extension (`.wav`) and its alternative (`.mp3`) in the local cache. This means audio plays even when the client's alert log recorded a `.wav` name but the upload was transparently compressed to `.mp3`.
- **Clip sync false "uploaded" log** — `_sync_pending_clips` was logging "Clip sync uploaded: …" even when `_upload_clip_inner` returned early on a 4xx error (such as 413). It now only logs success when the upload actually succeeded. Failure details continue to be logged by `_upload_clip_inner`.
- **413 nginx hint** — when a 413 is received the log now includes the payload size and the nginx config directive needed: `client_max_body_size 20m;`.

## [3.3.139] - 2026-03-26

### Fixed
- **APNs bad token auto-removal** — `BadDeviceToken` (400) responses are now handled the same way as 410 Unregistered: the token is immediately removed from the device list and the config is saved. Previously only 410 and `BadDeviceToken` via a 403 environment-flip retry were pruned; a direct 400 `BadDeviceToken` was logged but the dead token stayed in the list, causing two failed push attempts on every subsequent notification.

## [3.3.138] - 2026-03-26

### Fixed
- **Clip filenames now include the stream/input name** — clips are saved as `YYYYMMDD-HHMMSS_StreamName_alerttype.wav` (e.g. `20260326-065351_CoolFM-LONCTAXZC03_silence.wav`) instead of the previous `YYYYMMDD-HHMMSS_alerttype.wav`. Makes downloaded clips self-identifying without needing to inspect the folder they came from.
- **Clip filenames preserved end-to-end** — the client sends its original filename in the upload payload (`filename` field); the hub saves with exactly that name so client and hub directories stay in sync. Falls back to the legacy `{label}_{ts}.{ext}` format for older clients.
- **Sync upload timestamps corrected** — when the periodic sync re-uploads old clips the hub now extracts the original creation time from the embedded timestamp in the filename, so the alert log entry shows the correct clip date rather than the time the sync ran.

## [3.3.137] - 2026-03-26

### Added
- **Clip auto-upload toggle** (`Settings → Hub → Clip Upload`) — new checkbox to disable automatic clip push to the hub. When off, clips are saved on the client only; the hub can still request them via the chain-fault `save_clip` command. Useful on metered links or when you prefer to pull clips manually.
- **Clip sync toggle** — separate checkbox to disable the periodic background re-upload of missed clips (every ~100 s). Can be turned off independently of auto-upload.
- **MP3 clip format** — new `Clip Format` setting. Choosing MP3 encodes clips ~8× smaller before upload and storage. Uses `lameenc` (pure Python — `pip install lameenc`) if available, falls back to `ffmpeg` subprocess, then falls back to WAV if neither encoder is found. Extension is preserved through the upload/save pipeline (`ext` field in the upload payload; hub saves with correct `.mp3` extension).
- **Deferred fault-clip capture** — `save_clip` commands with `status="fault"` now wait `clip_duration` seconds before capturing audio, so the recorded clip contains post-fault content rather than the pre-fault audio that was in the rolling buffer at the moment the hub sent the command. `last_good` clips are still captured immediately (they intentionally record the audio before the fault).

### Fixed
- `_clip_cleanup` and `_sync_pending_clips` now include `.mp3` files alongside `.wav`.

## [3.3.136] - 2026-03-26

### Fixed
- **Clip inline player errors (persistent)** — replaced Werkzeug's `send_file(conditional=True)` with a fully explicit Range-request handler in `_serve_clip_wav()`. Reads the clip into memory, checks the `Range` header manually, and returns a correct `206 Partial Content` response with `Content-Range` / `Accept-Ranges` headers. This removes all dependency on Werkzeug's conditional machinery, which was producing inconsistent results with certain browser/proxy combinations even in 3.1.6.

### Added
- **Periodic clip sync** — clients now periodically re-upload any alert clips that are present on disk but were never confirmed as uploaded to the hub. After each successful upload `_upload_clip_inner` writes a zero-byte `.hub` marker alongside the WAV file; `_sync_pending_clips` (run every ~100 s in the heartbeat thread) scans `alert_snippets/` for WAVs without a marker and uploads them. This ensures clips are never permanently lost due to transient network errors or hub restarts at the moment of a fault.

## [3.3.135] - 2026-03-26

### Improved
- **Add/Edit Input form redesigned** — monitoring and alert settings are now organised into expandable cards instead of a flat list. Each alert type (Silence, Clipping, Hiss, EBU R128, AI) has its own card; enabling the checkbox reveals the relevant parameters and hides them when the check is off. Advanced options (clip length, escalation, stream comparison, cascade suppression, now playing) are collapsed under a single "⚙ Advanced Settings" section that auto-opens when any of those fields are already configured. All form field names and values are unchanged — no server-side changes required.

## [3.3.134] - 2026-03-26

### Added
- **Log viewer in Settings → Log** — new tab in the Settings page showing the last 500 SignalScope log lines in a terminal-style panel. Auto-refreshes every 3 seconds while the tab is open, stops polling when you switch away. Features: live filter box (type `[Clip]`, `[Hub]`, `ERROR`, stream name etc. to narrow down), pause/resume, clear display, copy-all to clipboard, and a scroll-to-bottom button. Lines are colour-coded: red for errors, yellow for warnings, cyan for `[Clip]` events, purple for `[Hub]` events, green for success messages. Backed by new `GET /api/settings/log?n=500&filter=…` endpoint.

## [3.3.133] - 2026-03-26

### Fixed
- **Simultaneous clip uploads overloading hub** — when a broadcast chain fault fires, every chain node tries to upload its audio clip concurrently. With no limit this could exhaust the hub's Waitress thread pool (8 threads), causing the last uploads to queue and potentially timeout, resulting in missing clips on the hub's Reports page and 404 errors in the inline player. Added `_clip_upload_sem` (threading.Semaphore(3)) so at most 3 clips upload concurrently from any one client; the rest wait their turn rather than hammering the hub.
- **Clip player errors on hub Reports page** — replaced the hand-rolled Range-request implementation in `_serve_clip_wav()` with Flask's `send_file(conditional=True)`. Werkzeug's battle-tested `make_conditional()` correctly handles `Range`, `ETag`, `If-None-Match`, and `304 Not Modified` across all browser/player combinations.
- **Diagnostic logging for missing clip files** — `_serve_clip_wav()` now logs the expected path whenever it returns 404, making it easy to confirm in the hub log whether a clip upload arrived on disk. `hub_clip_upload` also logs each successfully saved clip (key/filename/size) so the upload journey is fully traceable.

## [3.3.132] - 2026-03-26

### Fixed
- **Clip audio player errors on Reports page** — all `<audio>` clip elements used `preload="metadata"`, causing every clip on the page to fire a Range request simultaneously when the page loaded. Flask serialises requests, so they queued and timed out, leaving the player in an error state even though individual downloads worked. Changed all clip players to `preload="none"` so no request is made until the user presses play. Also switched the full-file path in `_serve_clip_wav()` from a single `f.read()` to a 64 kB streaming generator to avoid loading large WAV files into memory.

## [3.3.131] - 2026-03-26

### Fixed
- **Inline clip player errors** — `/api/chains/clip/` was using `send_from_directory` which does not reliably return HTTP 206 Partial Content. Chrome and Safari always send `Range: bytes=0-` when opening an `<audio>` element; without a proper 206 response the player errors or stalls while download still worked. Extracted `_serve_clip_wav()` helper with a hand-rolled Range implementation (identical to the working `/clips/` route) and used it in both routes for consistent behaviour.
- **Remote chain fault clips missing or incomplete** — `_upload_clip` had no retry mechanism. Large WAV clips (base64 JSON) over WAN links could exceed the 30 s timeout, silently dropping clips. Now retries up to 3 times with 15 s / 30 s back-off; 4xx errors are not retried; timeout raised to 60 s.
- **Clips disappearing from fault log after hub restart** — `api_chains_fault_log` unconditionally replaced DB clips with in-memory clips. After a hub restart (or after the 25-entry in-memory ring evicted older entries), in-memory had fewer clips than DB, silently wiping already-uploaded remote clips from the response. Now takes whichever list (in-memory or DB) is longer.

## [3.3.130] - 2026-03-26

### Fixed
- **LUFS loudness not shown on hub stream cards** — LUFS values (M/S/I/TP) have been included in every client heartbeat payload since 3.3.77 and stored in `hub_server._sites`, but `HUB_SITE_TPL` never rendered them. Added a compact LUFS row directly below the level bar on each hub stream card, matching the client card layout. Row hides when `lufs_m ≤ −69` (uninitialised default). True Peak coloured amber above −3 dBTP and red above −1 dBTP.

## [logger-1.0.1] - 2026-03-26

### Fixed
- **Logger now records every input type** — previously used a standalone ffmpeg process to re-open the source URL, which failed silently for FM (`fm://`), DAB (`dab://`), sound devices, and any other SignalScope-internal input type. Now taps SignalScope's internal `_stream_buffer` (a rolling deque of float32 PCM chunks at 48 kHz, ~41 s of history) and pipes raw audio to ffmpeg via stdin. Any input that SignalScope monitors can now be logged.
- **Bytes/str TypeError in silence detection** — ffmpeg stderr pipe yielded bytes but silence regex patterns were string patterns, raising `TypeError: cannot use a string pattern on a bytes-like object`. Fixed by decoding each stderr line explicitly with UTF-8.

## [logger-1.0.0] - 2026-03-25

### Added
- **Logger plugin** (`logger.py`) — new compliance logger for continuous 24/7 recording of any monitored stream. Records in 5-minute clock-aligned segments stored as `logger_recordings/{stream}/{YYYY-MM-DD}/HH-MM.mp3`. Key features:
  - **Silence detection** — ffmpeg `silencedetect` filter runs inline during recording; silence timestamps are stored per segment in an SQLite index (`logger_index.db`)
  - **Interactive timeline UI** — 24-hour grid of 288 colour-coded 5-minute blocks (green = OK, amber = partial silence, red = silence, dark = no recording); click any block to load and play it in the browser
  - **Scrubable player** — HTML5 audio with custom scrub bar; Mark In / Mark Out buttons set clip boundaries; Export Clip sends selected range to ffmpeg and downloads an MP3
  - **Quality tiers** — recordings start at a configurable high-quality bitrate (default 128k); a background maintenance thread re-encodes segments older than N days to a lower bitrate (default 48k after 30 days) and prunes segments beyond the retention period (default 90 days)
  - **Per-stream settings** — enable/disable recording independently per stream; configure HQ bitrate, LQ bitrate, LQ-after days, and retention days from the plugin's Settings tab
  - **Opt-in by default** — no streams are recorded until explicitly enabled; a notice banner links directly to Settings
  - **Reconnecting ingest** — ffmpeg uses `-reconnect` / `-reconnect_streamed` flags for HTTP sources; other protocols (RTP, local) supported without reconnect flags
  - **Disk usage reporting** — status endpoint tracks total storage and which streams are actively recording; shown in the header badge and Settings tab
  - **Plugin registry entry** added to `plugins.json` — install via Settings → Plugins → Check GitHub for plugins
  - Requires ffmpeg (already a SignalScope dependency)

## [3.3.129] - 2026-03-25

### Added
- **FCM push notifications for Android** (`signalscope.py`) — full Firebase Cloud Messaging HTTP v1 API support alongside existing APNs. Chain fault alerts and watched-node silence alerts are now delivered to both iOS (APNs) and Android (FCM) devices simultaneously.
- **`fcm_project_id` + `fcm_service_account_json` config fields** — stored in `MobileApiConfig`, saved/loaded from `config.json`.
- **`_send_fcm_push` / `_send_fcm_push_targeted`** — parallel to APNs equivalents; use OAuth2 JWT (RS256) from service account JSON to obtain a 55-min cached access token, then POST to FCM HTTP v1 API. Invalid/unregistered tokens are automatically pruned.
- **Android platform routing in `POST /api/mobile/device_token`** — requests with `"platform": "android"` are stored in `fcm_device_tokens` (separate from APNs tokens) with the same `watched_nodes` + `update_nodes` action support.
- **FCM Settings UI** — new section in Settings → Mobile API below APNs: Project ID field, service account JSON textarea, and a status indicator showing configured state and registered Android token count.

---

## [3.3.128] - 2026-03-25

### Added
- **Server-side push notifications for watched nodes** (`signalscope.py`) — the monitor loop now detects per-node `ok→fault` transitions every evaluation cycle. When a node goes silent/offline, an APNs push notification is sent only to devices that have subscribed to that node label (`"Silence detected: NodeName"`). Uses a new `_send_apns_push_targeted()` helper that sends to a specific subset of token entries rather than all registered devices.
- **Per-device watched-node list** (`signalscope.py`) — `POST /api/mobile/device_token` now accepts a `watched_nodes` field (list of node label strings) stored per token entry. New `action: "update_nodes"` updates just the node list for an already-registered token without changing the sandbox/environment flag. Existing `watched_nodes` are preserved when re-registering a token without supplying them.
- **iOS — sync watched nodes to server** (iOS app) — `APIClient.updateWatchedNodes(_:deviceToken:)` posts node subscription changes immediately when the user toggles a node in Settings → Silence Monitoring. Also synced on every app launch after APNs token registration so the server is always up to date.

---

## [3.3.126] - 2026-03-25

### Added
- **Mobile API — DAB region presets** (`dab.py`, `signalscope.py`) — new `GET /api/hub/dab/regions` endpoint in the DAB plugin returns the `_SCAN_REGIONS` hierarchy (same data used by the web scanner region tree). New mobile wrapper `/api/mobile/dab/regions` exposes this to the iOS app. Mobile `scan` endpoint already accepted a `channels` list — now the iOS app can pass a filtered channel set based on the selected location preset, scanning only the relevant 4–8 channels for a region instead of all 38 European Band III channels. **Requires iOS app update.**

---

## [3.3.125] - 2026-03-25

### Fixed
- **iOS FM Scanner — immediate disconnect on play** (`signalscope.py`) — `api_mobile_hub_scanner_stream` was calling `vf(slot_id)` (the fully decorated `hub_scanner_stream` plugin view), which triggered `@login_required` and redirected to `/login`, causing `URLSession` to complete immediately with no audio. Fixed by accessing `listen_registry.get(slot_id)` directly and inlining the same raw-PCM relay generator (startup silence, 1 s keepalive threshold) — same pattern as the WAV endpoint. `PCMStreamPlayer` on iOS now receives a continuous `application/octet-stream` 16-bit LE PCM feed as expected.
- **iOS DAB Scanner — stream endpoint calls decorated view function** (`signalscope.py`) — `api_mobile_hub_dab_stream` was using `_dab_vf("hub_dab_stream")` to call the unwrapped plugin function. Replaced with direct `listen_registry.get(slot_id)` access and an inlined MP3 relay generator, consistent with the FM stream fix. No decorator unwrapping needed — the slot is accessible from the core signalscope context.
- **iOS DAB Scanner — services never populated after scan** (`signalscope.py`, iOS) — the scan action waited a fixed 30 seconds then fetched services, but a full 38-channel DAB band scan takes 5–15 minutes. Added `/api/mobile/dab/scan_status/<site>` endpoint (delegates to `dab_scan_status`) and updated the iOS `scanAction()` to poll every 5 seconds until `status == "done"`, showing live progress percentage and current channel. Services are loaded immediately after the scan completes.

---

## [3.3.124] - 2026-03-25

### Fixed
- **iOS DAB Scanner — scan/start does nothing, welle-cli never spawned** (`signalscope.py`) — the mobile DAB wrapper endpoints were calling the fully-decorated plugin view functions (`vf()`) which failed silently due to `@csrf_protect` and `@login_required` rejecting the token-authenticated mobile request. Added `_dab_vf()` helper that peels off all `__wrapped__` decorator layers before invoking, so the bare DAB plugin logic runs without CSRF/session checks (mobile token auth already validated by `@mobile_api_required`).

---

## [3.3.123] - 2026-03-25

### Fixed
- **iOS FM Scanner — no audio despite RDS working** (`signalscope.py`) — `AVPlayer` cannot play raw `application/octet-stream` PCM streams. Added `/api/mobile/hub/scanner/stream_wav/<slot_id>` endpoint that wraps the existing PCM relay with a streaming WAV header (RIFF/WAVE, 16-bit LE mono 48 kHz, `0xFFFFFFFF` size markers). All `mobile_stream_url` responses from start/tune/status now point to `stream_wav` so iOS receives a proper `audio/wav` stream it can decode natively.

---

## [3.3.122] - 2026-03-25

### Added
- **Mobile API — FM Scanner**: new endpoints `/api/mobile/scanner/sites`, `start`, `tune`, `stop`, `status` so the iOS app can stream FM radio from any hub-connected RTL-SDR dongle (requires FM Scanner plugin)
- **Mobile API — DAB Scanner**: new endpoints `/api/mobile/dab/sites`, `start`, `stop`, `status`, `services`, `scan` for DAB digital radio streaming (requires DAB Scanner plugin)
- **Mobile API — Maintenance toggle**: `POST /api/mobile/chains/<cid>/maintenance` to enable/clear chain maintenance mode from the iOS app (no CSRF required, token auth)
- **Hub overview enriched**: mobile overview response now includes `rtp_loss_pct`, `rtp_jitter_ms`, `fm_rds_ps`, `fm_rds_rt`, `dab_service`, `dab_dls`, `dab_ensemble`, and `live_url` per stream

---

## [3.3.121] - 2026-03-25

### Fixed
- **Hub reports clips inline player unreliable / slow in Chrome** (`signalscope.py`) — `clips_serve` now properly handles `Range` requests (returns 206 Partial Content), which Chrome's `<audio>` element requires before it will play or show a duration. Added `ETag` (mtime+size) and `Cache-Control: private, max-age=3600` so the browser caches clips locally and avoids re-reading from disk on every interaction. Changed all hub-reports inline `<audio>` elements from `preload="none"` to `preload="metadata"` so the duration bar is populated as soon as the page loads — no need to click play first.

---

## [3.3.120] - 2026-03-25

### Fixed
- **Plugin dropdown overlapping page content** (`signalscope.py`) — the `<header>` element now has `position:relative; z-index:200` establishing a proper stacking context, ensuring the Plugins ▾ dropdown panel renders above all page content rather than behind controls in the body.

---

## [3.3.119] - 2026-03-25

### Fixed
- **Plugin dropdown panel right-aligned (off-screen on narrow viewports)** (`signalscope.py`) — changed from `right:0` to `left:0` anchor so the panel opens left-aligned with the trigger button instead of right-aligned.
- **Plugin dropdown trigger unclickable** (`signalscope.py`) — the trigger `<button>` had `pointer-events:none` inherited; replaced with a `<span>` inside a `tabindex="0"` `<div>` so clicks register correctly.

---

## [3.3.118] - 2026-03-25

### Changed
- **Plugin nav collapsed into hover dropdown** (`signalscope.py`) — the flat row of per-plugin nav buttons is replaced by a single **Plugins ▾** trigger. Hovering (or tabbing) reveals a panel listing all installed plugins. The trigger lights up in accent blue when the current page is a plugin. `:focus-within` makes it keyboard-accessible. The panel is a nonce-gated `<style>` block so it passes CSP without hashes or `unsafe-inline`. If no plugins are installed the trigger is omitted entirely.

---

## [3.3.117] - 2026-03-25

### Fixed
- **DAB Scanner DLS (Dynamic Label Segment) not displaying** (`dab.py` v1.0.26) — in welle-cli HTTP server mode, DLS is not emitted to stderr; it is available in `mux.json` as `svc["dynamicLabel"]` (may be a dict with a `"label"` key). Added `_dls_poller` thread that polls `mux.json` every 5 s and POSTs updates to the hub. `_dls_reader` is now a logging-only stderr drain.

---

## [3.3.116] - 2026-03-25

### Fixed
- **DAB Scanner Northern Ireland channel list incorrect** (`dab.py` v1.0.25) — channels were `["11D","12A","12B","11C"]`; 12A and 11C are not NI muxes. Corrected to `["11D","11A","12B","12D","9A","9C"]` (12D = NI regional/Bauer, 9A = Belfast SSDAB, 9C = UlsterMUX).
- **DAB Scanner audio silent/glitchy in Safari** (`dab.py` v1.0.25) — Safari's `<audio>` element does not handle infinite chunked HTTP streams reliably. Replaced with MediaSource Extensions (MSE): MP3 chunks are appended to a `SourceBuffer` directly, eliminating buffering issues. Falls back to plain `<audio src>` if MSE is unavailable.

---

## [3.3.115] - 2026-03-25

### Fixed
- **DAB Scanner weak muxes not found / poor audio quality** (`dab.py` v1.0.24) — welle-cli was launched without `-g` or `-p` flags, using default gain instead of the configured dongle gain/PPM. Added `_lookup_device()` to read `SdrDevice.gain` and `.ppm` from config; both flags are now passed in scan and stream worker commands.
- **DAB Scanner audio silent in Safari** (`dab.py` v1.0.24) — added `type="audio/mpeg"` to the `<audio>` element; Safari requires an explicit type hint to begin MP3 playback.

---

## [3.3.114] - 2026-03-25

### Fixed
- **DAB Scanner ~50% of audio chunks rejected with HTTP 403** (`dab.py` v1.0.23) — `_sign_chunk` used `f"{ts:.0f}:"` (rounds half-up) but the `X-Hub-Ts` header sent `str(int(ts))` (truncates). The hub recomputes the signature from the header value, so any chunk where the fractional timestamp part was ≥ 0.5 produced a different signature and was rejected. Fixed: use `f"{int(ts)}:"` in `_sign_chunk`.

---

## [3.3.113] - 2026-03-25

### Fixed
- **DAB Scanner `NameError: name '_json' is not defined`** (`dab.py` v1.0.22) — `import json` was used at the top level but the stream worker and other call sites expected `_json`. Renamed to `import json as _json` and updated all bare `json.loads`/`json.dumps` calls.

---

## [3.3.112] - 2026-03-25

### Fixed
- **DAB Scanner audio stream: remove redundant ffmpeg layer** (`dab.py` v1.0.21) — welle-cli's `/mp3/{SID}` endpoint already serves MP3 frames directly; piping through ffmpeg added a failure point with no benefit. Stream worker now opens the URL and relays chunks directly to the hub relay slot. Also logs the full service list from `mux.json` when the requested service name is not found, making name-mismatch problems diagnosable.

---

## [3.3.111] - 2026-03-25

### Diagnostic
- **DAB stream worker: log ffmpeg stderr** (`dab.py` v1.0.20) — ffmpeg stderr was routed to `/dev/null`; connection and codec errors were invisible. Now logged as `[DAB ffmpeg]` lines alongside `[DAB]` and `[DAB welle]`.

---

## [3.3.110] - 2026-03-25

### Fixed
- **DAB stream: service sometimes not found within probe window** (`dab.py` v1.0.19) — probe deadline extended from 15 s to 35 s (matching signalscope.py; some services take 20–25 s after mux becomes ready). Probe exceptions now logged instead of swallowed. SID comparison now uses `str()` wrapping to match signalscope.py's `_find_dab_service_in_mux`. Service name match falls back to substring comparison. Probe retry interval halved from 1.0 s to 0.5 s.

---

## [3.3.109] - 2026-03-25

### Fixed
- **DAB Scanner audio stream produces no output** (`dab.py` v1.0.18) — the `-A rawfile` backend pipes raw PCM to stdout, but the probe + ffmpeg pipeline was not matching signalscope.py's proven approach. Rewrote `_stream_worker` to use welle-cli HTTP server mode (`-w 7980`): launch welle-cli, poll `mux.json` for the SID, probe `/mp3/{SID}`, then stream MP3 chunks directly to the hub relay slot. Eliminates the 35 s silence-then-disconnect that Chrome was exhibiting.

---

## [3.3.108] - 2026-03-25

### Fixed
- **DAB scan returns 0 services after a stream attempt** (`dab.py` v1.0.17) — stream worker used `-A stdout` which is not a valid welle-cli 2.4 audio backend; welle-cli played to ALSA and produced no PCM on stdout. `proc_ffmpeg.stdout.read()` blocked forever, keeping welle-cli running and holding the RTL-SDR dongle. When a scan command arrived, the scan's welle-cli couldn't open the device ("No dongles found") and returned 0 services on all channels. Three fixes: (1) Changed audio backend from `-A stdout` to `-A rawfile` which is the correct welle-cli 2.4 backend for raw PCM output to stdout. (2) Added `select.select()` with 2s timeout around the ffmpeg read so the worker never blocks permanently — if welle-cli exits or the service isn't found, the worker unblocks within 2s and logs the exit. (3) `_dispatch_client_cmd` now calls `_stop_stream()` before starting a scan so any held dongle is released.
- **welle-cli stderr log limit removed** (`dab.py` v1.0.17) — the 30-line unconditional limit caused post-sync messages (service selection, audio start) to be silently dropped. Now logs ALL welle-cli stderr lines unconditionally.

## [3.3.107] - 2026-03-25

### Diagnostic
- **DAB stream worker: log welle-cli stderr and exact command** (`dab.py` v1.0.16) — welle-cli startup errors (bad audio backend, device busy, service not found) were silently discarded by `_dls_reader` since it only processed DLS-pattern lines. Now logs all welle-cli stderr to `monitor.log()` via `[DAB welle]` prefix: first 30 lines unconditionally, then any line containing error/fail/service/audio/sync keywords. Also logs the full welle-cli command at stream start so it can be run manually to test.

## [3.3.106] - 2026-03-25

### Fixed
- **DAB stream times out before welle-cli produces audio** (`dab.py` v1.0.15) — welle-cli with `-A stdout` must acquire DAB sync (up to 15s on marginal signals) before producing any PCM output. Hub's first-chunk deadline was 20s — too tight. Increased to 35s.
- **DAB dongle not appearing in SDR dropdown** (`dab.py` v1.0.15) — `loadDevices()` was calling `/api/hub/scanner/devices/{site}` which only returns `role="scanner"` serials. Added new `/api/hub/dab/devices/{site}` endpoint returning both `dab_serials` and `scanner_serials` (scanner dongles work fine with welle-cli). If only one serial is available it is auto-selected. `signalscope.py` heartbeat now also reports `dab_serials` for `role="dab"` dongles.
- **Chunk POST failures completely silent** (`dab.py` v1.0.15) — exceptions from `urllib.request.urlopen` in the chunk POST loop were swallowed with bare `except: pass`. Now logs the first failure and every 20th subsequent failure via `monitor.log()`. Also logs when welle-cli exits early (service not found / signal lost) and when the first MP3 chunk is ready.

## [3.3.105] - 2026-03-25

### Fixed
- **DAB audio broken — FM Scanner stealing the RTL-SDR dongle** (`dab.py` v1.0.14, `signalscope.py` 3.3.105) — root cause: DAB relay slots were created with `kind="scanner"`. `pending_for_site()` includes all non-stale slots in every heartbeat ACK. The main relay handler (`_handle_listen_requests`) saw `kind="scanner"` and called `_push_scanner_audio()` — which launched `rtl_fm` at the last-tuned FM frequency, grabbed the dongle, and logged `[Scanner] 96.50 MHz device=0 slot=…`. Two changes: (1) DAB plugin now creates slots with `kind="dab"` (a plugin-managed kind that the main relay handler must not intercept); (2) `_handle_listen_requests` in `signalscope.py` now skips any slot whose kind is not in `{"live","scanner","clip"}` — plugin-managed slots post their own audio chunks directly to `/api/v1/audio_chunk/<slot_id>` and need no relay thread. The DAB streaming endpoint check updated from `kind != "scanner"` to `kind != "dab"`.

## [3.3.104] - 2026-03-25

### Fixed
- **DAB Scanner band scan missing weak muxes** (`dab.py` v1.0.13) — scan timing now mirrors signalscope.py's `_dab_quick_probe`: polling starts from second 1 (no fixed startup delay), accepts the first 2 consecutive identical service lists (no minimum wait), 18s timeout per channel (marginal signals need 12–15s to acquire DAB sync). Previously `_STARTUP=2s` + `_MIN_WAIT=8s` + `_STABLE_NEED=3` wasted 8+ seconds before even checking stability, leaving weak signals (which sync at 12–15s) less than 7 seconds of polling time. SIGTERM wait increased from 3s→4s and USB settle from 0.5s→0.8s to give welle-cli time to call `rtlsdr_close()` cleanly between channels.
- **DAB stream start/stop now visible in client log window** (`dab.py` v1.0.13) — `_stream_worker` previously used `print()` (stdout only); now uses `monitor.log()` via a `_log()` helper so "Starting stream", "Stream worker error", and "Stream worker exited" messages appear in Settings → Logs. `monitor` is threaded through `_dispatch_client_cmd` → `_start_stream` → `_stream_worker`.

## [3.3.103] - 2026-03-25

### Fixed
- **DAB Scanner band scan service names** (`dab.py` v1.0.12) — rewrote `_do_scan()` to use welle-cli's built-in HTTP API mode (`-w PORT`) instead of parsing text output. For each channel welle-cli is now launched as `welle-cli -w 7979 -c CH`, and the scanner polls `http://localhost:7979/mux.json` for structured JSON service data — exactly the same approach used by signalscope.py's `_dab_scan_mux()`. Service names come from `svc["label"]["label"]` in clean JSON; no text parsing, no regex, no device-init noise to strip. This correctly handles all welle-cli 2.x backends (RTL-SDR, Airspy, SoapySDR) regardless of what init messages they emit to stdout/stderr.

## [3.3.102] - 2026-03-25

### Fixed
- **DAB Scanner service name parsing** (`dab.py` v1.0.11) — welle-cli 2.x outputs device-backend init messages (RTL_SDR: gain values, Airspy: errors, InputFactory: lines) before any DAB content. These lines were being matched by the service-name regexes, producing garbage results like "30 services" consisting of hex SIDs and init text. Fix: strip all device-init prefixed lines before parsing (RTL_SDR:, Airspy:, SoapySDR:, InputFactory:, etc.) and also reject any captured name that is purely hex/numeric. Added four targeted service-name patterns covering welle-cli 2.x's observed formats: `'Quoted name'`, `Service name: NAME`, `0xSID NAME`, and `NAME (SId 0x...)`. Ensemble name parser now rejects raw hex IDs like "c181". The debug raw-output logger now dumps the cleaned (post-filter) text so the remaining DAB content is visible.

## [3.3.101] - 2026-03-25

### Diagnostic
- **DAB Scanner raw output logging** (`dab.py` v1.0.10) — temporary diagnostic: when a channel produces output but no service names are parsed, the first 800 bytes of raw welle-cli output are logged via `monitor.log()` so the exact output format can be seen in the client's log window. Used to fix the service name regex patterns.

## [3.3.100] - 2026-03-25

### Fixed
- **DAB Scanner scan progress never reaching hub** (`dab.py` v1.0.9) — site names containing spaces (e.g. `Northern Ireland DAB`) were embedded raw into URLs: `…/scan_progress/Northern Ireland DAB`. `urllib.request` throws an exception on the unencoded space, which was silently caught, so every progress push, result push, and DLS update was silently dropped and the hub stayed permanently at 0. Fix: `urllib.parse.quote(site, safe="")` applied to the site name segment of all three client→hub push URLs (`scan_progress`, `scan_result`, `dls`). Flask's `<path:site_name>` routing URL-decodes the segment automatically so the hub receives the correct site name. Added `import urllib.parse`.

## [3.3.99] - 2026-03-25

### Fixed
- **DAB Scanner client log now visible in in-app log window** (`dab.py` v1.0.8) — all client-side `print()` calls in `_client_poller`, `_dispatch_client_cmd`, and `_do_scan` were going to stdout (terminal only) and were invisible in the Settings → Logs tab. Switched all key diagnostic messages to `monitor.log()` by threading `monitor` through to `_dispatch_client_cmd(monitor)` and `_do_scan(..., monitor)`. A `_log()` helper inside `_do_scan` calls both `monitor.log()` and `print()` so nothing is lost. After this update the client's log window will show: `[DAB] Client command poller started`, `[DAB] Received command: scan`, `[DAB] Band scan started: site='...' channels=N welle=/path/to/welle-cli` (or `[DAB] Scan aborted: welle-cli not found in PATH` if welle-cli is missing from the service PATH).

## [3.3.98] - 2026-03-25

### Fixed / Diagnostic
- **DAB Scanner command poll switched from GET to POST** (`dab.py` v1.0.7) — the client's command poll was a GET request with a custom `X-Dab-Site` header. GET requests with custom headers can be silently stripped or blocked by reverse proxies and some middleware. Changed to POST with the site name in the JSON body, consistent with every other working client→hub call (heartbeat, audio chunks, DLS push). The GET route is kept for backward compatibility. Hub-side `monitor.log()` additions: (1) first poll from each client site logs `[DAB] Client 'sitename' poller connected` — visible in the hub's in-app log window so the user can confirm the client is alive; (2) 403 rejections log the exact flag values that caused the rejection; (3) any dispatched command logs its action. Browser `console.log/error` added to `_startScan()` so the browser DevTools console shows whether the scan POST succeeds and what the hub returns.

## [3.3.97] - 2026-03-25

### Fixed
- **DAB Scanner client poller never starting** (`dab.py` v1.0.6) — the poller thread was only created if `cfg.hub.hub_url` was non-empty at the exact moment `register()` ran at plugin load time. If the config was not fully loaded yet, or if `hub_url` was momentarily empty, the thread was never started and no log appeared — the client stayed permanently silent. Fix: the poller thread now starts unconditionally on every machine; the mode/hub_url checks that were the startup gate are now inside the loop, where they log a clear diagnostic and retry every 3 s. Hub-only machines (mode not `client`/`both`) log once that they are idle and then sleep indefinitely. Machines waiting for `hub_url`/`site` to be configured log every 60 s. This means the first thing visible in client logs after install/restart is always `[DAB] Client poller running` followed by either `[DAB] Poller idle: mode='hub'` (on a hub-only machine) or polling activity.

## [3.3.96] - 2026-03-25

### Fixed
- **DAB Scanner scan stuck at 0 — command never reached client** (`dab.py` v1.0.5) — the `/api/hub/dab/cmd` poll endpoint (and all three client→hub push endpoints) checked `sdata.get("_approved")` which is only set `True` when a site has been explicitly approved via the hub admin panel. Sites that heartbeat fine and appear in the DAB Scanner dropdown (which uses `approved` defaulting True) were still returning 403 to the client command poller, so the scan command was never dispatched and `_do_scan()` never ran. Fix: all four client-facing endpoints now accept `_approved OR approved (default True) AND NOT blocked` — consistent with the dropdown's own site filter. The client command poller now logs errors instead of silently swallowing them: 403 responses, HTTP errors, and general exceptions are printed every 20 occurrences; received commands log their action name; startup prints `[DAB] Client poller running`.

## [3.3.95] - 2026-03-25

### Fixed
- **DAB Scanner USB dongle recovery after welle-cli hard-kill** (`dab.py` v1.0.4) — when welle-cli is SIGKILL'd during a channel probe it can leave the RTL2832U firmware in a stuck state that survives even a system reboot (only a physical unplug/replug fixes it). Added `_usb_reset_rtlsdr(serial)`: issues the Linux `USBDEVFS_RESET` ioctl (`0x5514`) to the dongle's `/dev/bus/usb/BUS/DEV` node — the kernel's software equivalent of power-cycling the USB device. Called automatically after any SIGKILL of `proc_welle` in both `_do_scan()` and `_stop_stream()`. The dongle's device node is located by walking `/sys/bus/usb/devices`, matching Realtek vendor `0x0bda` with RTL2832U product IDs and optionally the configured serial number. After the ioctl a 1.5 s settle delay allows the firmware to reinitialise before the next channel is probed. Silent no-op on macOS/Windows or if `fcntl` is unavailable. If the process lacks permission, prints a clear message with the udev rule needed to grant access without running as root.

## [3.3.94] - 2026-03-25

### Fixed
- **DAB Scanner RTL-SDR dongle locked by welle-cli** (`dab.py` v1.0.3) — `welle-cli` could hold the RTL-SDR USB device and not release it on `SIGTERM`, leaving the dongle inaccessible even after the process appeared to exit. Three-part fix: (1) `_do_scan()` now uses a SIGKILL fallback: after `proc.terminate()`, if `proc.wait(timeout=3)` raises `TimeoutExpired`, `proc.kill()` is called to force-kill the process and reclaim the device; (2) the pipe is explicitly drained after each channel so no buffered bytes hold the process open; (3) a 0.5 s settle delay is inserted between channels so the OS and `librtlsdr` can fully release the USB device before the next probe. `_stop_stream()` gets the same SIGKILL fallback for the streaming pipeline. The "Stop Scan" button now also queues an immediate `scan_stop` command in `_hub_pending` so the client kills `_scan_proc` via the 3 s command-poll path rather than waiting up to 12 s for the next channel's progress push.

## [3.3.93] - 2026-03-25

### Added
- **DAB Scanner region-aware scan tree** (`dab.py` v1.0.2) — Band Scan panel now shows a collapsible region hierarchy instead of a single "Scan All" button. Regions: All Europe (36 ch), United Kingdom (10 ch) with sub-regions (Northern Ireland, Scotland, Wales, England National, London, North West, North East, Yorkshire, Midlands, South), Republic of Ireland, Germany, Netherlands, France, Norway, Denmark, Belgium, Switzerland. Any node can be clicked to scan only its specific channel set. Tree auto-expands Europe and UK on load. The browser passes the selected channel list to `/api/hub/dab/scan`; the hub validates channels against `_DAB_CHANNELS` and passes them to the client `_do_scan()` worker, so the progress bar total reflects the actual channel count being scanned (e.g. 4 for Northern Ireland, not 36).

## [3.3.92] - 2026-03-25

### Added / Changed
- **DAB Scanner scan progress UI** (`dab.py` v1.0.1) — Band Scan panel now shows a real-time progress bar (animated width), channel counter ("14 / 36"), current-channel title ("Scanning 11D…"), and live mux chips that appear as each ensemble is found (channel + ensemble name + service count). A **⏹ Stop** button replaces the Scan button during scanning; clicking it signals the client to abort after the current channel finishes. Client-side `_do_scan()` now reads a `stop` flag in the HTTP response from each progress push, breaks immediately, and pushes the partial results. Hub-side: `_hub_scan` state tracks `progress`, `total`, and `muxes` list; new `/api/hub/dab/scan_stop` endpoint; progress push endpoint returns JSON `{"stop": bool}` instead of 204.

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

## SignalScope-3.4.66
- **Fix**: Heartbeat timeout raised 5s → 10s — prevents "stuck at max backoff" after hub restart when response is marginally slow (TLS handshake + cold start). Root cause of one client needing a monitor restart after a hub outage while others self-healed.
- **Fix**: Log a clear WARNING after 10 minutes at max backoff with a hint to restart the monitor, so the condition is visible in logs rather than silently retrying.

## SignalScope-3.4.67
### Security fixes
- **C-1**: `GET /hub/update/download` now returns 403 immediately when no shared secret is configured — previously served full source code to any unauthenticated caller
- **C-2**: `hub_clip_upload`, `hub_backup_upload`, `hub_ping_result`, `hub_log_data` now return 403 when no secret configured — previously all were unauthenticated when secret_key was empty
- **C-2 (replay)**: Added nonce replay protection to `hub_backup_upload`, `hub_ping_result`, `hub_log_data` — previously captured signed requests could be replayed within the 30s HMAC window
- **H-1**: CSRF check now enforced unconditionally on all state-changing requests — previously skipped entirely when `auth.enabled = False`
- **H-3**: `hub_backup_upload` now verifies `X-Hub-Site` is a known approved site before writing — previously any caller could overwrite any site's backup
- **H-4**: Nonce generation replaced `md5(os.urandom(8))[:16]` with `os.urandom(16).hex()` — stronger entropy, no unnecessary MD5
- **H-5**: `/api/dab/scan` now validates `channel` against `_VALID_DAB_CHANNELS` whitelist — previously raw value passed to welle-cli subprocess
- **H-6**: 500 error handler no longer includes raw Python exception in HTML response — logged server-side only

## SignalScope-3.4.68
### Security fixes (medium / low)
- **M-4**: XOR fallback encryption now appends a 32-byte HMAC-SHA256 MAC — ciphertext integrity is verified before decryption when `cryptography` package is not installed
- **M-5**: `sdr.py` spectrum push endpoint now verifies HMAC signature when a shared secret is configured; unknown slot_ids return 404 instead of silently accepting data
- **M-6**: `_safe_name` collision registry prevents two streams with different names but identical stripped forms from sharing the same alert clip directory (colliding names get `_2`, `_3` suffix)
- **M-7**: `hub_clip_upload` now rejects bodies over 10 MB with HTTP 413 before reading request data
- **L-1**: `lwai_config.json` permissions set to 0o600 after backup restore (save_config already did this; restore path was missing it)
- **L-3**: CSRF token helpers now read `<meta name="csrf-token">` before cookie fallback (correct priority — meta tag is server-rendered and always fresh)
- **L-4**: 404 error handler HTML-escapes `request.path` — fixes reflected XSS via crafted URLs
- **L-6**: SDR serial numbers validated against configured device registry in sdr.py before being passed to rtl_sdr/rtl_power subprocess

## SignalScope-3.4.69
### Backward-compatibility fixes for 3.4.68 security changes
- **M-4 fix**: XOR fallback now uses version byte `\x03` (new) instead of `\x01` (legacy). Old `\x01` payloads from pre-3.4.68 clients are still decrypted without MAC verification (read-only compat). Prevents MAC mismatch errors during rolling upgrades for the rare case where `cryptography` package is not installed.
- **M-5 fix (hub)**: Spectrum push endpoint now only rejects a *wrong* HMAC signature — an *absent* signature is allowed through during the upgrade window. Prevents 403 errors from old sdr.py clients that don't yet send `X-Hub-Sig` for spectrum frames.
- **M-5 fix (client)**: `_sdr_worker` now includes `X-Hub-Sig` / `X-Hub-Ts` headers when pushing spectrum frames to the hub, when a shared secret is configured.

## Icecast Plugin 1.0.0
- New plugin: manage Icecast2 streaming servers on client nodes
- Select any monitored input as a stream source (HTTP/HTTPS streams, ALSA devices, RTP)
- Per-stream ffmpeg source processes push to local Icecast2 server (MP3 or OGG/Opus)
- Live status: listener counts, connected state, stream URLs — auto-refreshes every 10s
- Server settings management: port, source password, admin password
- Auto-restarts dead ffmpeg processes for enabled streams
- Hub overview page: all sites' Icecast streams and listener totals in one table
- Hub can push start/stop commands and add new streams to any connected client
- Mobile API endpoint: GET /api/mobile/icecast/streams
