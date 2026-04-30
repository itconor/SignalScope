# SignalScope Changelog

---

### Audio Router 1.2.6 — 2026-04-30

**Fix: hub crash cycle and chipmunk audio — _stream_buf_chunks rewrite**

**Root cause 1 — hub kill / 8-second restart cycle:**
`_stream_buf_chunks` called `len(inp._stream_buffer)` without guarding
against `None`. `InputConfig._stream_buffer` defaults to `None` until the
monitoring loop initialises it. If `_start_source_route_buffered` ran before
the monitoring loop had started (stream not yet connected), the `len(None)`
raised `TypeError`. The `_reader` thread caught this, called `bc.close()` and
set `stop`. Eight seconds later `_poll_and_execute` restarted the route.
Each close/reopen cycle freed the hub_stream Waitress thread briefly (hub
"recovers"), then re-held it (hub "dies again"). Fix: guard `buf is None`
with a `stop.wait(0.1)` retry — no crash, generator simply waits.

**Root cause 2 — chipmunk / double-speed audio:**
The function read from `_stream_buffer` (always mono float32) but then
applied stereo deinterleaving `arr[0::2] + arr[1::2]` when `_audio_channels == 2`.
This treated the mono data as stereo, halving the sample count before
converting to int16. Ffmpeg received half-length chunks and played them at
2× speed (chipmunk). Fix: for stereo sources use `_audio_buffer` (true L/R
interleaved float32); for mono sources use `_stream_buffer`. Remove the
deinterleave logic entirely.

**Stereo passthrough (Livewire source):**
Output is now always stereo-interleaved s16le. Stereo sources pass through
L and R unchanged. Mono sources are duplicated to L=R so the dest ffmpeg
always receives valid 2-channel PCM. All ffmpeg commands updated from
`-ac 1` to `-ac 2`. `X-Audio-Format` header updated to `s16le/48000/2`.

---

### Audio Router 1.2.5 — 2026-04-30

**Fix: hub overload / Waitress thread exhaustion from hub_stream blocking**

- Root cause: `_StreamBroadcaster.consumer()` uses `_cond.wait(timeout=2.0)`.
  When used inside a Flask streaming response (`hub_stream`, `direct_stream`),
  the generator blocks for up to 2 seconds without yielding. Waitress holds
  its worker thread for the entire block duration. If no audio is flowing
  (source hasn't started yet), the generator loops indefinitely — holding the
  thread forever. With ffmpeg `-reconnect 1`, each reconnect piles on a new
  thread. The thread pool exhausts and the hub stops serving other requests.
- Fix: added `_StreamBroadcaster.consumer_with_keepalive(catchup=0, interval=0.1)`.
  Waits at most `interval` (0.1 s) between yields. On timeout with no real
  audio, yields a silence chunk (`bytes(9600)`) so the Waitress thread returns
  to a yield point — allowing client disconnect detection and freeing the
  thread for other requests. When audio flows, the `notify_all()` on each
  push wakes the consumer immediately (no added latency).
- `hub_stream` and `direct_stream` both now use `consumer_with_keepalive()`.
- Removed the explicit silence-prime `yield bytes(9600)` from 1.2.4 — it is
  no longer needed since the keepalive handles the first-byte problem.

---

### Audio Router 1.2.4 — 2026-04-30

**Fix: hub_stream 5XX — prime connection to prevent nginx proxy_read_timeout**

- Root cause: `audiorouter_hub_stream` generator blocked in `bc.consumer()` waiting
  for the first audio chunk from the source. Flask/Werkzeug does not call
  `start_response()` (commit HTTP headers) until the generator yields its first
  byte. If the source is slow to start, nginx `proxy_read_timeout` fires before
  any bytes are yielded and the dest ffmpeg receives a 5XX reply.
- Fix: generator now yields `bytes(9600)` (one block of silence) immediately before
  entering the consumer loop, committing headers instantly regardless of source
  readiness.
- Changed `bc.consumer()` to `bc.consumer(catchup=0)` so dest starts from live
  data only (avoids replaying a burst of stale buffered chunks on connect).
- Added `X-Accel-Buffering: no` response header to disable nginx proxy buffering
  for the streaming response. Removed redundant `Transfer-Encoding: chunked`
  (Flask sets this automatically for generator responses).

---

### Audio Router 1.2.3 — 2026-04-30

**Fix: hub relay 404 — replace scanner-slot relay with hub-side broadcaster**

- Root cause: `listen_registry` scanner slots expire when idle. The hub
  creates them at startup via `_restore_slots()`, but DAB (and other slow-
  starting inputs) can take 2+ minutes to begin pushing audio. By that time
  the slot has expired, `listen_registry.get(slot_id)` returns None, and
  `/api/audiorouter/hub_stream/` returns 404 indefinitely.
- Fix: removed all scanner-relay-slot dependency from the audio relay path.
  The hub now holds a `_StreamBroadcaster` per route in `_hub_broadcasters`
  (never expires — lives as long as the hub process).
  - Source nodes POST raw PCM to `POST /api/audiorouter/push_chunk/<rid>?token=HMAC`
    (new endpoint, replaces `/api/v1/audio_chunk/{slot_id}`)
  - Dest `GET /api/audiorouter/hub_stream/<rid>?token=HMAC` reads from the
    hub broadcaster — created lazily on first push or first consumer connect,
    so the dest can connect before the source starts and will just block until
    audio arrives
- `_start_dest_route`: hub_stream_url is always valid (computed from hub_url
  + rid + token); no longer requires `slot_id` to be non-empty
- `_start_source_route` and `_start_source_route_buffered`: removed
  `slot_id` guard; both now push to `push_chunk` URL
- Scanner relay slots (`_route_slots`, `_restore_slots`, `_ensure_relay_slot`)
  are kept for backward-compat but no longer carry audio
- Hub broadcaster closed on route disable and delete

**Note**: the HUB must be updated to 1.2.3. The old hub code does not have
`/api/audiorouter/push_chunk/` or the broadcaster-backed `hub_stream/`.

---

### Audio Router 1.2.2 — 2026-04-30

**Fix: dest client hub relay exits silently — scanner stream requires login**

- `/hub/scanner/stream/<slot_id>` is protected by `@login_required` (browser
  session cookie). ffmpeg on the dest node has no session cookie — it receives
  a redirect to the login page, tries to decode HTML as s16le PCM, and exits
  cleanly (exit code 0, no error logged). Dest routes therefore restarted
  in a tight loop every ~8 s indefinitely.
- Fix: added `GET /api/audiorouter/hub_stream/<rid>?token=HMAC` to the audio
  router plugin itself. Authenticated with the same per-route HMAC token
  already used for the direct P2P stream — no browser session needed.
  Reads from the ListenSlot queue directly (`slot.get(timeout=1.0)`).
- `_start_dest_route` now builds a token-authenticated hub stream URL
  (`/api/audiorouter/hub_stream/<rid>?token=...`) and uses it as the hub
  relay fallback instead of `/hub/scanner/stream/...`.
- The scanner relay URL (`/hub/scanner/stream/`) is no longer used by the
  audio router at all — it was only ever a relay path for the audio, never
  the right auth mechanism for machine-to-machine use.

---

### Audio Router 1.2.1 — 2026-04-30

**Fix: Livewire (and all monitored) sources fail with ffmpeg ALSA error**

- `_input_type()` returned `"alsa"` for a numeric device_index (e.g. `"7503"`,
  a Livewire stream ID). ffmpeg then ran `-f alsa -i 7503` → exit 251 "Input/output
  error". Same failure would hit any ALSA or RTP/Livewire source.
- Root fix: `_start_route` now always tries the PCM buffer path first for
  source/local roles when `_find_input` finds the stream in SignalScope's
  monitoring loop. SignalScope already decodes the audio; re-opening the raw device
  is both unnecessary and likely to fail (monitoring loop holds it open).
  Direct ffmpeg is now a fallback only for streams not actively monitored locally.
- Covers all input types uniformly: Livewire (numeric ID), ALSA, DAB, FM,
  HTTP, RTP — any stream SignalScope monitors works as a route source.
- `_stream_buf_chunks` now downmixes stereo chunks to mono. `_audio_channels`
  is read from the InputConfig; interleaved L/R float32 is averaged to mono
  before int16 conversion. Previously stereo chunks (Livewire, ALSA stereo)
  would have produced double-length mono at half the correct pitch.

---

### Audio Router 1.2.0 — 2026-04-30

**UI overhaul: rich route cards with per-side status, ffmpeg error capture**

- Routes panel replaced with card-per-route layout (`.rcard`). Each card shows
  SOURCE and DESTINATION sides independently with their own status badges
  (Active / Connecting / Error / Idle), last-seen timestamps, multicast address,
  and "via direct / via hub" tag on the destination side.
- `_route_status` restructured from `{rid: flat}` to `{rid: {site: {...}}}`.
  Source and destination now each own their entry — a source reporting "active"
  can no longer be overwritten by the destination reporting "idle", which was the
  primary cause of cross-site routes blipping to active then back to idle.
- ffmpeg stderr no longer goes to `/dev/null`. New `_drain_stderr()` helper
  thread captures the last 20 lines from every ffmpeg process and logs them on
  non-zero exit — giving visible evidence of exactly why a route died.
- `audiorouter_routes_list` returns `source_status` and `dest_status` as
  separate objects; overall status is derived (active wins, then connecting, then
  error, then idle) rather than being first-write-wins.
- `audiorouter_poll` reads `direct_url` via `_route_status[rid][source_site]`
  (nested path) so the source's URL is never masked by a dest write.
- Auto-refresh interval reduced from 10 s to 5 s.

---

### Audio Router 1.1.4 — 2026-04-30

**Fix: hub self-poll "Connection refused" / routes stay idle**

- `_self_url()` was returning `http://127.0.0.1:8080` as its loopback
  fallback — but SignalScope always binds on port **5000**. The hub's
  client thread polled a port where nothing was listening, getting
  "Connection refused" on every cycle. Added `_SS_PORT = 5000` constant
  used by both `_self_url()` and `_my_direct_url()`.
- Consequence: any route where the hub machine is the source or destination
  never started (hub could not poll its own routes). Cross-site routes
  between two remote clients were unaffected — those clients poll the hub
  at the configured `hub_url`, which was always correct.

---

### Audio Router 1.1.3 — 2026-04-30

**DAB and FM sources now supported**

- Routes with a `dab://` or `fm://` source no longer fail with "source type
  not supported". SignalScope already decodes DAB/FM audio in its monitoring
  loop — the router now reads from `inp._stream_buffer` (float32 PCM chunks,
  ~0.5 s each) and converts to int16 LE bytes in-process.
- For same-site (local) routes: PCM bytes are piped directly to ffmpeg stdin
  (`-f s16le -ar 48000 -ac 1 -i pipe:0`), which re-encodes to Livewire L24
  stereo RTP. No welle-cli or rtl_fm interaction needed.
- For cross-site (source) routes: a `_SentinelProc` occupies `_active_procs`
  so the "already running" guard works; the buffer reader drives the same
  `_StreamBroadcaster` fan-out as the ffmpeg-based source path, giving both
  hub relay and direct P2P to the dest node.
- `numpy` required for float32 → int16 conversion (already present in any
  SignalScope install). Falls back to "numpy required" error if missing.

---

### Audio Router 1.1.2 — 2026-04-30

**Fix: client thread not starting on hub-mode nodes**

- The routing client thread only started when `mode` was `client`, `both`,
  or `standalone`. Pure `hub` mode was excluded, so hub machines could
  never execute routes involving their own streams — the thread never ran,
  no log messages appeared, and all routes stayed "idle" indefinitely.
- Fix: `hub` mode is now included in the condition. The hub polls itself
  via its own loopback URL and executes local/source/dest roles for any
  routes where the hub's `site_name` is the source or destination.

---

### Audio Router 1.1.1 — 2026-04-30

**Fix: routes stuck at "idle"**

- Removed `@login_required` from `/api/audiorouter/poll` — this is a
  machine-to-machine API called by client nodes that have no browser
  session. With auth enabled every poll was silently redirected to the
  login page, leaving all routes permanently idle.
- Hub startup now re-creates relay slots for all enabled cross-site routes
  (`_restore_slots` thread). Previously `_route_slots` was in-memory only,
  so a server restart left cross-site routes without a slot and the source
  client would report "No relay slot assigned yet" on every poll until the
  route was toggled off and on again.

---

### Audio Router 1.1.0 — 2026-04-29

**P2P direct routing — hub relay is now last resort**

- Cross-site routes now try direct P2P before hub relay. Source node starts a
  `_StreamBroadcaster` fan-out: a single ffmpeg process feeds both the hub relay
  sender thread and a new `/api/audiorouter/stream/<route_id>` HTTP endpoint
  (authenticated via a stable per-route HMAC token — no login session required).
- Source reports its direct URL (`http://<lan-ip>:<port>/…?token=…`) to the hub
  via the status API. Hub passes it to the dest client in the next poll response.
- Dest probes the direct URL (2.5 s timeout) before deciding which path to use.
  Routing priority: (1) same-site local, (2) direct P2P, (3) hub relay.
- Hub relay sender always runs concurrently, so a brief P2P outage degrades
  gracefully to hub relay without restarting ffmpeg.
- Fixed `_report_status` to accept and forward a `direct_url` field.
- Fixed `_start_route` to pass `cfg_ss` to `_start_source_route` so the
  reported direct URL uses the node's LAN IP rather than 127.0.0.1.

---

### Audio Router 1.0.0 — 2026-04-29

**New plugin: Broadcast Audio Router**

- Route any monitored input on any connected SignalScope site to a Livewire
  multicast output on any connected site. Named routes are configured in a
  hub UI; client nodes execute them autonomously via short-poll.
- **Same-site routes**: client runs ffmpeg locally — input → Livewire L24
  stereo 48 kHz RTP multicast (`pcm_s24be`, payload type 97).
- **Cross-site routes**: hub creates a relay slot (`listen_registry.create`).
  Source client encodes the input to 16-bit LE mono 48 kHz PCM, posts
  HMAC-signed 9600-byte chunks to `/api/v1/audio_chunk/<slot_id>`. Dest
  client fetches raw PCM from `/hub/scanner/stream/<slot_id>` and pipes
  it to ffmpeg → Livewire RTP multicast.
- Supports HTTP, HTTPS, SRT, RTSP, and ALSA inputs. FM and DAB inputs are
  reported as unsupported.
- Hub page: add/enable/disable/delete routes, live status badges (Active /
  Connecting / Error / Disabled / Idle), multicast address preview.
- Client status reported back to hub via `POST /api/audiorouter/client_status`
  after each state change.
- Config stored atomically in `plugins/audiorouter_cfg.json`.
- No SSE — uses short-poll everywhere (8 s client poll, 10 s hub UI refresh).
- Requires ffmpeg on client nodes.

---

### Logger 1.6.7 — 2026-04-29

**Fix: downgrade loop for recordings with no DB record**

- `_maybe_downgrade` was stuck in an infinite re-encode loop for any audio file
  that had no matching row in the `segments` table (common for recordings made
  before the current DB, or after a DB rebuild).
- The `UPDATE segments SET quality='low'` succeeded at the file level but
  matched 0 rows, so the next maintenance cycle found `quality != 'low'` again
  and re-encoded the same files endlessly.
- Fix: after a successful downgrade, if `rowcount == 0`, insert a stub record
  `(stream, date, filename, start_s=0.0, quality='low')` via `INSERT OR IGNORE`
  so future passes skip the already-downgraded file.

---

### Studio Board 3.15.3 — 2026-04-29

**AzuraCast now-playing on Studio Board TV displays**

- When a studio's broadcast chain has an AzuraCast station linked (via `input_name`)
  and no Zetta is configured, the Studio Board TV display now shows AzuraCast
  now-playing data in the lower panel: artist, title, playlist label, album artwork
  (from AzuraCast or Planet Radio fallback), progress bar, countdown timer, next track,
  and a live streamer badge (🔴 LIVE) when the station is in live mode.
- Priority: Zetta (full sequencer) → AzuraCast NP → idle message. Zetta always wins.
- Progress bar and countdown update at the same poll cadence as Zetta data.

---

### SignalScope 3.5.191 — 2026-04-29

**AzuraCast now-playing integrated into chain fault annotation**

- `_fire_chain_fault()` now reads `monitor._azuracast_chain_state` as a fallback when
  no Zetta is linked to a chain. If an AzuraCast station is linked via `input_name`,
  the track playing at fault time is captured into `zetta_now_playing` (same field,
  displayed with `[AzuraCast]` tag) — visible in chain fault history, morning report,
  and the fault log table.
- Live streamer state is also captured: `🔴 LIVE — <name> [AzuraCast]`.
- Broadcast Chains API now includes `az_now_playing` dict for each chain that has an
  AzuraCast station linked, for use by the Broadcast Chains page and any future UI.

---

### AzuraCast 1.3.1 — 2026-04-29

**Added: `monitor._azuracast_chain_state` — chain-level now-playing for cross-plugin use**

- New `_rebuild_az_chain_state()` function builds a dict keyed by `chain_id`,
  mirroring the shape of `monitor._zetta_chain_state`. Populated by matching each
  chain node's stream name against AzuraCast stations' `input_name` field —
  no extra config required.
- Called after every poll cycle; set as `monitor._azuracast_chain_state`.
- Initialised to `{}` at plugin load so consumers can always safely `getattr` it.
- Fields: `now_playing` (title, artist, playlist, art_url, asset_type=0), 
  `playing_next`, `remaining_seconds`, `duration_seconds`, `elapsed_seconds`,
  `is_live`, `streamer_name`, `station_name`, `is_online`, `listeners`, `ts`, `source`.

---

### Wallboard 3.16.1 — 2026-04-29

**Added: AzuraCast now-playing on chain cards**

- Chain cards now show AzuraCast now-playing when no Zetta is linked to a chain.
  Priority: Zetta (full sequencer) → AzuraCast → Planet Radio text fallback.
- AzuraCast display shows artist, title, playlist name, a progress bar (elapsed/duration),
  and the next track. Live streams show 🔴 LIVE with the streamer name.
- Data comes from `monitor._azuracast_chain_state` via the `/api/wallboard/data`
  endpoint (`azuracast` key alongside `zetta`).

---

### AzuraCast 1.3.0 — 2026-04-29

**Added: Auto-create monitored input from AzuraCast stream URL on station add**

- Each station in discovery results now shows an "Add HTTP stream as monitored input"
  section (only when AzuraCast returns a listen_url). Stream URL is pre-filled and
  editable; stereo checkbox included. When confirmed, both the AzuraCast station
  record AND a SignalScope HTTP input are created in one click. The new input is
  automatically linked to the station for silence detection.
- The `/api/azuracast/discover` endpoint now accepts `server_id` as an alternative
  to URL + API key, so saved-server credentials never need to be re-entered.
- "🔍 Discover more" button added to each saved server in the server list — triggers
  discovery immediately using stored credentials; pre-fills the URL field so Confirm
  Add still works correctly.
- `doDiscover`/`doDiscoverSaved` refactored around shared `_showDiscoverResult(d)`
  to eliminate code duplication.

---

### AzuraCast 1.2.3 — 2026-04-29

**Fixed: Discover Stations button still did nothing after 1.2.2**

- All plugin functions (`doDiscover`, `doAddStation`, etc.) are defined inside an IIFE
  `(function(){ 'use strict'; ... })()` for encapsulation. HTML `onclick="doDiscover(this)"`
  looks up the name in `window` scope, where it doesn't exist → `ReferenceError`.
- Fix: removed the `onclick=` attribute, added `id="disc-btn"`, and wired the click
  listener inside the IIFE's init section alongside the other delegated listeners.

---

### AzuraCast 1.2.2 — 2026-04-29

**Fixed: Discover Stations button non-functional (SyntaxError on page load)**

- The Confirm Add and Cancel buttons inside the discovery results table used `onclick=` with
  `\'` escapes inside a Python `"""` template string. Python renders `\'` as `'` (not `\'`),
  producing broken JS string literals (`',''`) that caused `SyntaxError: Unexpected string`
  at page load — preventing the entire script block from executing.
- Fix: replaced `onclick=` handlers with `data-idx` / `data-station-id` attributes and
  extended the existing `disc-result` click delegation to handle `.disc-confirm-btn` and
  `.disc-cancel-btn`, matching the pattern already used for the Add button.

---

### Morning Report 1.3.4 — 2026-04-29

**Fixed: Three missing sections in HTML email + atomic config saves**

- Email now includes Studio Activity table (brand-to-studio moves per studio)
- Email now includes Stream Quality table (glitches, loudness, packet loss, silence events per stream)
- Email now includes Hourly Interruptions heatmap (24-cell table with colour-coded fault counts — CSS grids unsupported in email clients so rendered as a table)
- `_save_cfg()` now uses `tempfile.mkstemp` + `os.replace` atomic write pattern — prevents an empty config file if the server crashes mid-write

---

### Morning Report 1.3.3 — 2026-04-29

**Added: HTML email delivery**

Morning Report can now email the full report as a rich HTML email automatically when it is generated each morning.

- **Settings → Morning Report → Email Recipients** — one address per line; leave blank to disable
- Uses the hub's existing SMTP configuration from **Settings → Notifications → Email** — no separate SMTP setup needed
- Email is a self-contained dark HTML document with inline styles: headline banner, at-a-glance stats, chain health table, outage detail cards (including engineering notes), automation health, and patterns sections
- Plain-text fallback included for clients that don't render HTML
- **Send Report Now** button on the settings page lets you test delivery immediately using the current cached report
- Scheduler re-reads the recipient list each morning so changes to settings take effect without a restart

---

### Morning Report 1.3.2 — 2026-04-29

**Fixed: Scheduler thread and hub routes no longer start on client nodes**

`hub_only: True` in `SIGNALSCOPE_PLUGIN` only hides the nav item — it does not prevent the plugin from being loaded and `register()` from running on every node. The `_scheduler_loop` thread was starting unconditionally, querying `metrics_history.db` and `alert_log.json` that don't exist on client machines and producing empty reports every morning.

`register()` now reads the node `mode` before doing anything hub-specific. On client nodes the scheduler thread is never started and the hub routes return a plain "hub only" message. All report generation, cache loading, and settings persist only on hub/standalone nodes.

---

### SignalScope-3.5.190 — 2026-04-29

**Improved: Chain fault alert messages — plain English, no redundancy**

Before: `Chain fault in 'Downtown Radio' — signal lost at 'London - Livewire/Downtown Radio - LONCTAXMQ05' (site: London - Livewire, stream: Downtown Radio - LONCTAXMQ05). This is the first failed point in the chain. 3 downstream position(s) also affected.`

After: `Downtown Radio — Audio lost at Downtown Radio - LONCTAXMQ05 (London - Livewire). 3 further positions in the chain also lost audio.`

Changes across all fault message paths:
- Removed "Chain fault in" opener — chain name is the subject, not a prefix
- Removed `(site: X, stream: Y)` parenthetical — the node label already contains this info; repeating it is pure clutter
- Removed "This is the first failed point in the chain." — always true for the detected fault, adds no information
- "signal lost at" → "Audio lost at" — clearer to non-technical staff
- "is not reporting" → "has gone offline" — simpler language for offline nodes
- "3 downstream position(s) also affected" → "3 further positions in the chain also lost audio" — plain English
- Downstream note when some feeds still OK: "Audio confirmed OK further down the chain: [names]"
- Stack partial fault: "partial fault at [pos]: [faulted], ([ok] still OK)"

---

### Morning Report 1.3.1 — 2026-04-29

**Added: Engineering notes shown in outage detail cards**

When an operator has added a note to a chain fault (via the fault log UI or the iOS app), it now appears in the outage detail card as a highlighted "Engineering Note" block — including the note text, who added it, and when. The note is pulled from `chain_notes.json` using the fault's primary key (`id`) which is now also selected in the `chain_fault_log` query.

---

### Morning Report 1.3.0 — 2026-04-29

**Added: Per-outage detail log**

New "Outage Detail Log" section appears when chains had outages yesterday. Each outage gets a card showing: fault start/end time, duration, which node and site detected it, monitoring stream. If Zetta data is present: Zetta computer/instance name, automation mode (Auto/Manual/Voice Track), what was playing (title + artist), and an amber notice when the fault occurred during an ad break. Studio context: cross-references STUDIO_MOVE events to show which brand was live in each studio at the moment of the fault. Cascaded faults are labelled.

**Fixed: "Best Performing Chain" showing the chain with outages**

`min(faults_per_chain_y, ...)` only considered chains that actually had faults — so when only one chain faulted it appeared as both "Best" and "Worst". Fixed: "Best Performing Chain" now requires a chain with zero faults. If every monitored chain had at least one interruption the card is suppressed entirely.

---

### SignalScope-3.5.189 — 2026-04-29

**Fixed: Comparator correlation chips stuck at "…" — never update**

The Jinja template built element IDs in the form `corr_{id}_{from_idx}_{from_sub|'x'}_{to_idx}_{to_sub|'x'}` (e.g. `corr_abc_0_x_1_x`). The JavaScript `getElementById` lookup was constructed as `corr_{id}_{from_idx}_{to_idx}` — omitting both `from_sub` and `to_sub` segments. Every lookup returned `null`, so the `.cv` span value never changed from the placeholder "…" and the chip colour class never updated.

Fix: JS now mirrors the template exactly — `corr_+chain.id+'_'+from_idx+'_'+(from_sub??'x')+'_'+to_idx+'_'+(to_sub??'x')`.

---

### SignalScope-3.5.188 — 2026-04-29

**Fixed: Chain builder "Save Chain" button hidden behind Privacy Policy footer**

`body>*{position:relative;z-index:1}` creates a stacking context on every direct body child. `<main>` and `<footer>` both had z-index:1; since `<footer>` appears later in the DOM it painted on top of `<main>` at the same z-index level. The chain builder overlay (`.builder-drawer`, `position:fixed;z-index:950`) lives inside `<main>`'s stacking context — its internal z-index:950 wins within `<main>` but `<main>` as a whole still loses to `<footer>` at the body level. Result: the footer's "Privacy Policy" text rendered over the Save Chain / Cancel row at the bottom of the builder.

Fix: added `footer{z-index:0}` on the chains page so `<main>` (z-index:1) paints above `<footer>` (z-index:0), and the full builder overlay is unobscured.

---

### SignalScope-3.5.187 — 2026-04-29

**Fixed: DAB crashes when system clock is corrected by NTP/PTP step**

NTP (or chrony) corrects large clock offsets by *stepping* `CLOCK_REALTIME` forward. All DAB timeout deadlines used `time.time()` (wall clock), so a +66 s NTP step made every active deadline appear expired in zero real time. The `pcm_deadline` (15 s to receive first audio from ffmpeg) expired immediately, `audio_lost = True` broke the inner audio loop for every DAB consumer, all consumers called `_release_dab_session`, and when the last consumer released, `_stop_dab_session` killed rtl_tcp — exactly matching the `[rtl_tcp] Signal caught, exiting!` crash signature seen after a machine boots with the system clock significantly wrong.

Fix: every deadline/timeout in the DAB monitoring code now uses `time.monotonic()` (immune to wall-clock steps). Affected paths:
- `_poll_mux` — mux availability deadline
- `_dab_usb_backoffs` — USB error backoff timer
- Consumer startup deadline (180 s)
- USB backoff comparison
- `session.ready.wait()` timeout
- Service-in-mux extra wait deadline (20 s)
- Audio probe `ready_deadline` (660 s)
- `pcm_deadline` (15 s to first PCM chunk from ffmpeg)
- `recover_deadline` (20 s audio endpoint recovery)

Timestamps written to metrics DB, HMAC signing, and DLS last-change tracking remain as `time.time()` (they record wall-clock UTC times, not durations).

---

### SignalScope-3.5.186 — 2026-04-29

**Fixed: DAB "Too many open files" after days of uptime**

- `_elevate_priority` (the `preexec_fn` run inside the welle-cli child process at launch) now calls `resource.setrlimit(RLIMIT_NOFILE, (65536, 65536))` to raise the FD limit from the default ~1024 to 65536. After days of continuous operation, welle-cli's embedded HTTP server accumulates socket FDs from prewarm keep-alives, mux.json polls, and ffmpeg reconnects. Hitting the limit caused `accept failed: Too many open files` to repeat every second and DAB audio to drop.
- Falls back gracefully: if 65536 exceeds the system hard limit, the limit is raised to the hard limit maximum instead. The parent SignalScope process is unaffected.
- `"too many open files"` added to the welle-cli stderr fatal-marker list — if the error occurs despite the raised limit (e.g. running pre-fix binary on a very old session), the DAB shared session is treated as failed and a fresh welle-cli is spawned automatically.

---

### SignalScope-3.5.185 + Studio Board 3.15.2 + Morning Report 1.2.6 — 2026-04-29

**Studio moves logged to Hub Reports and Morning Report**

*Studio Board 3.15.2*
- Every brand-to-studio assignment and studio clear now writes a `STUDIO_MOVE` event to the hub alert log. The event records the studio name, the incoming brand, the outgoing brand, and the chains carried by that brand.
- Fires from both the REST assignment endpoint (`/api/studioboard/studio/<id>/brand`) and the general studio update endpoint.

*SignalScope-3.5.185 (main app)*
- `STUDIO_MOVE` added to the always-present type filter list in Hub Reports — it appears in the Type dropdown even when no recent moves are in the visible window.
- New `t-studio` badge style (light blue pill) for `STUDIO_MOVE` events in the reports table.
- **Fixed: Hub Reports TIME column was truncated** — "2026-04-29 ..." cut off the time portion. The timestamp is now displayed on two lines (date / time) so both are always fully visible without widening the column.

*Morning Report 1.2.6*
- New **🎙 Studio Activity** section at the bottom of each daily report. Shows a per-studio table of brand assignments from the previous day: time of move, brand that went on air, brand that was displaced, and which broadcast chains it carried. Only appears when Studio Board is active and moves were recorded.

---

### SignalScope-3.5.184 — 2026-04-28

**Chain builder redesigned as full-screen two-column overlay**

- The chain builder now opens as a full-screen overlay (replaces the narrow 540 px side drawer). Fade-in/scale animation replaces the slide-in.
- Signal path configuration (preview, chain name, positions, advanced settings) occupies the left column; the right column is always visible and contains Signal Comparators and Alert Timing.
- Comparators are now a permanent, always-visible panel — never hidden in a collapsed section. No more "where did my comparators go?" confusion.
- Alert Timing fields moved from the pinned strip above the footer into the always-visible right column.

---

### Studio Board 3.15.1 — 2026-04-28

- TV display: "UP NEXT" booking badge renamed to "NEXT BOOKING"
- Admin UI: integration renamed from "Liveread" to "Airwave" throughout (tab label, card headers, descriptive text, column header, JS comments)

---

### AzuraCast 1.2.1 — 2026-04-28

**Fix: "Discover Stations" did nothing**

The plugin called `_btnLoad()`, `_btnReset()`, and `_ssConfirm()` — helpers that only exist in the main SignalScope template and are unavailable on standalone plugin pages. `_btnLoad()` threw a `ReferenceError` immediately, killing the function before the fetch ever ran. The "Discovering…" text would appear but the API call never fired.

Fix: added lightweight local definitions of all three helpers at the top of the plugin's `<script>` block.

---

### SignalScope-3.5.183 — 2026-04-28

**Fix: Chain editor — comparators section hidden after rebuild**

The "Signal Comparators" collapsible in the chain builder started collapsed with no count badge, so when editing a chain with comparators configured, the rows were populated but invisible — it looked as though the comparators had been deleted. Three changes:

- The section now auto-expands when opening a chain that has comparators. Closing and reopening a new/empty chain collapses it back.
- A blue pill badge (e.g. "2") appears in the header whenever comparators are present, updating as rows are added or removed.
- `from_sub` and `to_sub` (stack sub-node indices) are now correctly saved and round-tripped. Previously only `from_idx`/`to_idx` were saved, losing the sub-index on every save.
- The identity check for duplicate comparators now also considers sub-index, so two different nodes within the same stack can be compared.

**Fix: Chain editor footer obscured by live audio mini-player**

When clicking a chain node's live-listen button while the chain builder was open, the audio mini-player (`position:fixed;bottom:0;z-index:9999`) covered the drawer's Save/Cancel footer. `_startListen` now bumps the builder drawer's `bottom` by 64 px when the mini-player is shown, and resets it when the mini-player is dismissed.

---

### SignalScope-3.5.182 — 2026-04-28

**Fix: 500 Internal Server Error on `/hub` after fresh restart**

`level_dbfs` can be `None` in the heartbeat payload when a stream hasn't yet produced a real audio measurement (since 3.5.170, `_build_payload()` sends `None` instead of `-120.0` to avoid overwriting hub_state.json with uninitialised values). The HUB_TPL and HUB_WALL_TPL Jinja2 templates computed `(lev + 80)` directly without a None guard — causing `TypeError: unsupported operand type(s) for +: 'NoneType' and 'int'` and HTTP 500 on every `/hub` page load until the first audio measurement arrived.

Fix: both template locations now use `{% set lev = s.level_dbfs if s.level_dbfs is not none else -120.0 %}`.

---

### Studio Board 3.15.0 — 2026-04-28

**Feature: Liveread Studio Bookings integration**

When a studio has no brand assigned, the TV display now shows the next confirmed booking pulled from the [Liveread](https://liveread.bauerni.co.uk) Studio Bookings API, so staff can see at a glance what session is coming up (or in progress) without switching to another system.

**Admin — new "Liveread" tab:**
- Enter the API URL (pre-filled to the production endpoint) and your Bearer token
- "Test & Load Studios" button validates credentials and fetches the studio list from Liveread
- Map each SignalScope studio to its Liveread counterpart with per-row dropdowns
- Mapping is saved independently of the API credentials so you can change the token without remapping

**TV display — cleared studio card:**
- When studio has no brand assigned AND a Liveread mapping exists:
  - **"NOW IN STUDIO"** badge (green) if a booking is currently in progress (within start–end time window)
  - **"UP NEXT"** badge (blue) for the next upcoming booking today or this week
  - Shows: booking title, presenter name, time range (e.g. `09:00 – 11:00`)
- Disappears when a brand is assigned (occupied studio shows full brand panel as before)
- Booking data is cached on the server for 5 minutes to avoid hammering the Liveread API on every TV poll

**Backend:**
- `GET /api/studioboard/liveread/studios` — server-side proxy to Liveread API (avoids CORS; admin UI calls this)
- `POST /api/studioboard/liveread/config` — saves URL, token, and studio mapping into `studioboard_cfg.json` under a `liveread` key
- `sb_data()` includes `next_booking` per studio when a mapping is configured

---

### SignalScope-3.5.181 — 2026-04-28

**Fix: Orphaned "Ongoing" fault still not closed after 3.5.180 restart**

3.5.180 correctly restored `_chain_fault_state[cid] = "alerted"` in `_load_fault_log_from_db()`, but the chain monitor's warmup block (which runs once on first pass) unconditionally overwrites `_chain_fault_state` based purely on the current chain status, then `continue`s — bypassing the recovery code path entirely. For a chain that was faulted at restart time but has since recovered (`curr == "ok"`), the warmup set state to `"ok"` without ever writing `ts_recovered`, leaving the fault "Ongoing" indefinitely.

Fix: in the warmup `else` branch (chain currently healthy), check whether the fault log has an open entry (`ts_recovered is None`). If so, write `ts_recovered` and call `fault_log_set_recovered()` right there, silently (no recovery notification — avoids spurious alerts for old faults). A log line confirms the close. On the next monitor tick the chain is cleanly in `"ok"` state and new faults can fire normally.

---

### SignalScope-3.5.180 — 2026-04-28

**Fix: Chain fault "Ongoing" forever after server restart — missed CHAIN_FAULT alerts**

`_chain_fault_state` (the in-memory fault state machine) was not restored from the database on startup. After a restart, every chain defaulted to state `"ok"` regardless of what the DB showed. Two consequences:

1. **Stuck "Ongoing" faults** — a chain whose fault was still open at restart time would see `prev == "ok"` on the next monitor tick, so `_do_chain_recovery()` was never called, `ts_recovered` was never written, and the fault remained "Ongoing" in the UI indefinitely.
2. **Missed new CHAIN_FAULT alerts** — while a chain was stuck in the "alerted" state (open DB row, but state machine said "ok"), silence events on the same chain would fire `_do_chain_recovery()` for an already-closed fault rather than raising a fresh `CHAIN_FAULT`. Subsequent silence was silently ignored.

Fix: `_load_fault_log_from_db()` now also restores `_chain_fault_state[cid] = "alerted"` for any chain whose most recent fault log entry has `ts_recovered is None`. A startup log line lists all chains restored to open-fault state. On the next monitor tick, the recovery path fires normally (writes `ts_recovered`, clears the fault), or if the chain is genuinely still faulted a new alert is raised.

---

### Brand Screen 1.3.12 — 2026-04-28

**Fix: Full-screen logo mode showed colour bars around the logo**

`#fslogo-img` was constrained to `max-width:88vw;max-height:88vh`, leaving a hard-coded 6% gap on every side that displayed as the brand background colour. Changed to `width:100vw;height:100vh;object-fit:contain` — the image now fills the full viewport and `object-fit:contain` handles aspect ratio preservation naturally, with no artificial margin.

---

### AzuraCast 1.2.0 — 2026-04-28

**Feature: Song history, listener sparkline, queue, mount breakdown, station controls, webhooks**

- **Song history** — each station card now has a collapsible "Recent tracks" section showing the last 15 songs with play times, fetched every 60 s via `/api/station/{id}/history`.
- **Listener sparkline** — a 70×16 px trend chart sits next to the listener count badge, showing the last 30 min of audience data sampled on every poll cycle.
- **Upcoming queue** — collapsible "Up next" section showing the next 8 AutoDJ tracks (requires station-admin API key; silently absent without one).
- **Mount/stream breakdown** — collapsible "Streams" section showing listener count per mount point format (MP3, AAC, HLS, etc.), sourced from `/api/station/{id}/listeners` (requires admin key).
- **⏭ Skip / 🔄 Restart controls** — appear on station cards only when an API key is configured. Skip jumps to the next AutoDJ track; Restart triggers a full station broadcasting restart (with confirmation prompt). Routes: `POST /api/azuracast/control/skip` and `POST /api/azuracast/control/restart`.
- **Webhook receiver** — `POST /api/azuracast/webhook/{server_id}/{station_id}`. Configure in AzuraCast → Station → Webhooks → Generic (POST). Webhook URL shown with a Copy button per station in the settings drawer. Receives NowPlaying payloads for instant updates between poll cycles; fires fault/recovery alerts on online-state transitions.

---

### AzuraCast 1.1.0 — 2026-04-28

**Fix: Discovery endpoint, HTTP error messages, and atomic config saves**

- **Discovery now uses `/api/nowplaying`** instead of `/api/stations`. The `/api/stations` endpoint returns `NowPlayingStation` objects which have no `listeners` or `is_online` fields — the discovery table always showed 0 listeners and "Online" for every station regardless of actual state. `/api/nowplaying` returns full NowPlaying objects with real listener counts and live online status.
- **HTTP error handling improved** in the per-station poller: HTTP 404 now shows a plain-English message ("Station not found — check station ID and enable_public_api setting") instead of a raw urllib error; 401/403 also have specific messages.
- **Dual auth headers** — `X-API-Key` is now sent alongside `Authorization: Bearer` for maximum compatibility across AzuraCast versions.
- **Atomic config saves** — `_save_cfg` now uses `tempfile.mkstemp` + `os.replace` to prevent config corruption if the process is killed mid-write.

---

### Wallboard 3.16.0 — 2026-04-28

**Feature: Staff message board**

Operators can now post a short message (up to 280 characters) directly from the Wallboard settings drawer (⚙ → Staff Message section). The message appears as a prominent amber banner between the status hero strip and the chain cards — visible to everyone in the office. Posted time is shown below the message text. Messages auto-expire after 24 hours. The banner is theme-aware (amber tint in dark/bauer mode, orange in corp/light mode). Clearing is immediate — just press ✕ Clear or post an empty message.

---

### SignalScope-3.5.179 — 2026-04-28

**Fix: Thread-safety race in `hub_alert_poll()` / `HubAlertFanout` cleanup**

`hub_alert_poll()` was reading `hub_alert_fanout._queue` directly without holding the condition lock, creating a race with `push()` writing to the same list. Replaced with a new thread-safe `get_since(seq)` method that acquires the lock before reading. The old `get_events()` blocking method (used by the now-removed SSE endpoint) was removed. Also CLAUDE.md updated with two architectural rules from recent sessions.

---

### Wallboard 3.15.2 — 2026-04-28

**Fix: Non-atomic config save / alert ticker sort order**

`_cfg_save` now writes to a temp file and uses `os.replace` for an atomic swap — prevents config corruption if the server is killed mid-write (same fix as Studio Board 3.14.20). `_load_alerts` was sorting on `e.get("time", 0)` but the correct key is `"ts"` — alerts were in storage order rather than newest-first.

---

### Push Server 1.0.9 — 2026-04-28

**Fix: Non-atomic config save**

`_save_cfg` now writes to a temp file and uses `os.replace` for an atomic swap — prevents credential loss if the server is killed mid-write.

---

### SignalScope-3.5.178 — 2026-04-28

**Fix: Server unresponsive / sites losing connectivity under load (SSE thread exhaustion)**

Waitress was configured with `threads=24`. Each SSE connection holds one thread indefinitely. With multiple hub browser tabs, Studio Board TV displays, and Brand Screen displays all open simultaneously, the thread pool was exhausted — client heartbeats and page loads couldn't get a thread, causing sites to go offline and pages to fail.

- **`/hub/alert/events` SSE removed** and replaced with `GET /api/hub/alert_poll?since=<seq>` short-poll. The hub page polls every 5 s (15 s on error); each call completes instantly and releases its thread. This eliminates 1 persistent thread per hub browser tab. Chain fault notifications fire within 5 s of the event, which is indistinguishable in practice from instant.
- **`/hub/stream/events`** (dead code — never referenced by any template or plugin; live levels have always been delivered by polling `/api/hub/live_levels`) **removed**.
- **Waitress threads raised 24 → 64** to give more headroom for Studio Board and Brand Screen SSE connections that remain (one permanent thread per TV display is unavoidable with SSE).

**Rule**: Never add an SSE endpoint for data that can be delivered by short-polling. SSE holds a Waitress thread permanently. Short-poll releases it after each response. Reserve SSE only for cases where instant push with no latency is essential (Studio Board config change notifications) and the connection count is bounded.

---

### SignalScope-3.5.177 — 2026-04-28

**Fix: Browser notification test shows success even when OS suppresses the notification**

`new Notification()` never throws an exception when the OS silently discards the notification (macOS Focus/DND, Chrome quiet-notifications mode). The test button was immediately showing "✓ Test notification sent" without confirming the notification was actually displayed.

Fixed: the test now uses `n.onshow` to confirm the notification appeared; until then shows "Waiting…". `n.onerror` shows the actual error. A 4 s timeout detects silent suppression and shows a specific warning pointing to OS notification settings → Chrome → banners. Each test click uses a unique tag so the browser never deduplicates consecutive clicks.

---

### SignalScope-3.5.176 — 2026-04-28

**Feature: Browser notifications for chain faults**

- **Settings → Notifications → Browser Notifications**: new section with an Enable button that calls `Notification.requestPermission()` and stores the preference in `localStorage`. Includes a Test button and a Disable button. Shows permission status (enabled / denied / not yet requested).
- Hub dashboard connects to a new SSE stream (`/hub/alert/events`) when browser notifications are enabled. Chain fault and recovery events are pushed in real time via the new `HubAlertFanout` class (separate from the live-level fanout). The SSE stream auto-reconnects after 15 s on error.
- Chain fault → `⚠ Chain Fault: <name>` with `requireInteraction: true` (stays until dismissed). Chain recovery → `✅ Recovered: <name>` (auto-closes after 8 s). Browser-notification events fire immediately on every fault/recovery regardless of email/push cooldown — same events the alert log captures.
- Re-connects automatically when the tab becomes visible again, so granting permission in the Settings tab and switching back to the dashboard activates it without a page reload.

---

### Push Server 1.0.8 — 2026-04-27

**Fix: Migrate button still does nothing after 1.0.7**

The 1.0.7 fix added shim functions (`_btnLoad`, `_btnReset`, `_ssToast`) immediately below a JavaScript block comment: `/* Shims for topnav helpers — this page uses a custom header, not {{topnav()}} */`. The `{{topnav()}}` expression inside the comment was evaluated by Jinja2 (topnav is a registered context processor available in all templates), inserting the full navigation HTML — several kilobytes of HTML including `*/` characters from CSS rules, URLs, and embedded JS. The `*/` terminated the block comment prematurely, causing a JavaScript syntax error that silently prevented every function definition that followed from being parsed. The shims were never defined; clicking Migrate still threw `ReferenceError`. Fixed by removing the Jinja2 expression from the comment.

---

### Push Server 1.0.7 — 2026-04-27

**Fix: Migrate button does nothing**

The Migrate and error-handling paths called `_btnLoad()`, `_btnReset()`, and `_ssToast()` — helpers defined by the main app's topnav JS. The Push Server page uses a custom header (not `{{topnav(...)}}`), so those functions were never defined. Clicking Migrate threw a `ReferenceError` immediately, the `fetch()` call never ran, and nothing happened. Save-button error messages were also silently swallowed. Added inline shims for all three functions to the plugin's own `<script>` block.

---

### Studio Board 3.14.21 — 2026-04-27

**Fix: Boards freeze momentarily after mic-live change**

- 3.14.20 moved mic-live state to memory and eliminated the `_cfg_save` disk write on every mic toggle. As a side effect, the SSE `config_changed` notification now fires *instantly* (no longer after the slow disk write completes). All connected TV boards respond simultaneously by fetching `/api/studioboard/data`, which calls `eval_chain()` on every chain — serialised through `hub_server._lock`. The burst of concurrent requests under that lock caused the momentary freeze.
- Fixed `_cfg_save`: JSON is now written to a **unique temp file** (via `tempfile.mkstemp`) before acquiring the lock, so the I/O happens outside any contention window. The lock is held only for the two `os.replace()` rename calls (microseconds). Concurrent saves no longer race each other for a shared `.tmp` filename.
- Removed the redundant `_cfg_load()` disk read from `sb_mic_live` — the mic endpoint only updates the in-memory dict; reading the full config just to validate the studio ID was unnecessary I/O on every toggle.

---

### Studio Board 3.14.20 — 2026-04-27

**Fix: Config wipe after days of uptime**

- `_cfg_save` opened the JSON file with `"w"` (truncating it to 0 bytes immediately) then wrote the new content. Any crash, OOM-kill, or `systemctl restart` between truncation and write completion left the file empty or partially written. On next startup `json.load` failed and `_cfg_load` returned `{"studios": [], "brands": []}`, silently wiping all studios and brands.
- Fixed with atomic writes: config is now written to a `.tmp` file first, the existing file is then rotated to `.bak`, and finally the `.tmp` is renamed into place. All three operations are protected by a module-level lock. `os.replace()` is atomic on POSIX (rename syscall) so there is no window in which a partial file can be observed.
- `_cfg_load` now falls back to `.bak` (the previous good save) if the main file is missing or corrupt. Any server restart following a write-interrupted crash will load from the backup automatically.
- **Mic-live writes eliminated**: every mic-on/mic-off toggle previously rewrote the entire config to disk, dramatically increasing the crash-during-write probability during broadcasts. Mic-live is now held in a module-level memory dict (`_mic_live_state`) and never touches the file. State is cleared on server restart (expected: the desk must re-trigger mic-live after a restart).

---

### SignalScope-3.5.175 — 2026-04-27

**Fix: Chain fault push notifications not delivered when Push Server URL is configured**

- `CHAIN_RECOVERED` notifications were sent via `_send_apns_push()` (direct APNs, always uses local credentials, ignores Push Server URL setting). `CHAIN_FAULT` notifications were sent via `_dispatch_push()` (respects Push Server URL, routes through relay when set). This asymmetry meant: users with Push Server URL set received recovery notifications (direct APNs worked) but never received fault notifications (relay didn't know their credentials).
- Fixed: recovery notifications now use `_dispatch_push()` matching the fault path. Both notification types now route through the same channel. **If you have local APNs credentials configured and fault notifications weren't arriving, clear the Push Server URL field in Settings → Mobile API and save — your direct credentials will then be used for all notifications.**
- Also fixed: recovery notifications were never sent to Android (FCM). `_dispatch_push()` handles both APNs and FCM; `_send_apns_push()` was iOS-only.

---

### vMix Caller 1.7.3 — 2026-04-27

**Feature: Studio picker on Presenter View**

The Presenter View (`/hub/vmixcaller/presenter`) now opens a studio selection screen when multiple vMix instances are configured. Studio names come directly from the configured vMix instance names.

- **Studio picker**: a grid of cards — one per vMix instance — shown when the presenter first opens the page. Each card shows the instance name and vMix IP. Clicking "Enter Studio" navigates to `?inst=<id>`.
- **Per-session selection**: instance choice is in the URL (`?inst=<id>`), not a shared global setting. Multiple presenters can independently be in different studios at the same time.
- **Single-instance setup**: if only one vMix instance is configured, the picker is skipped entirely and the presenter lands directly on the full page (no change in behaviour).
- **Studio name in header**: once a studio is selected, the header title shows the studio name and the sub-line shows "Ready to join" as normal.
- **◂ Studios back link**: a small "◂ Studios" link replaces the SignalScope link in the header when multiple studios exist, letting the presenter switch studios easily.
- **Correct video preview per studio**: each studio loads the video preview URL from its own `bridge_url`, not the globally active instance.
- **Correct vMix input routing**: commands (join/leave/mute) automatically include the selected studio's `vmix_input` index, so the right Zoom caller input in vMix is controlled.

---

### vMix Caller 1.7.2 — 2026-04-27

**Feature: Zoom participant management (Phase 2) + Webhook receiver (Phase 3)**

Phase 2 — Live participant panel:
- **Zoom Participants card**: appears automatically once a meeting is joined (or auto-detected when exactly one live meeting is running). Shows every in-call participant with audio/video status icons.
- **Mute / Request Unmute**: per-participant button to mute (or request unmute for those already muted).
- **Remove participant**: remove from meeting with a confirmation prompt. Not available for the host.
- **Waiting Room section**: appears when participants are queued. Admit individually or Admit All in one click.
- **15-second auto-refresh**: participant list polls automatically while a meeting is active; manual Refresh button available.
- Active meeting tracked in browser via `setActiveMeeting()` — called automatically on Join from meetings list, manual join, or saved preset join. Cleared on Leave.
- Hub/client proxy: client nodes proxy participant requests to the hub via HMAC-signed `zoom_hub_participants` endpoint — credentials never leave the hub.

Phase 3 — Webhook receiver:
- **Webhook endpoint** at `/api/vmixcaller/zoom_webhook` — configure in your Zoom S2S OAuth app (Event Subscriptions).
- **URL display field** in the Zoom API Configuration card (hub only), populated from `window.location.origin`. Copy-to-clipboard button.
- **Webhook Secret Token** field (password input): enter the Zoom-generated secret. When set, all incoming webhooks are validated via HMAC-SHA256 (`v0=HMAC(secret, "v0:{ts}:{body}")`).
- Handles Zoom URL validation challenge automatically (`endpoint.url_validation`).
- Participant join/leave events invalidate the participant cache immediately — next 15-second poll delivers fresh data.
- Meeting started/ended events invalidate the meetings list cache.
- `/api/vmixcaller/zoom_webhook_counter` endpoint — browser can poll this to detect any new webhook event without a full data reload.
- Subscribe to: *Meeting → Participant/Host joined, left, waiting room joined/left, meeting started/ended*.

---

### vMix Caller 1.7.1 — 2026-04-27

**Feature: NDI video preview mode**

Alternative to the SRT/Docker bridge — the client node receives the vMix NDI output directly over the LAN, encodes it to HLS via ffmpeg named pipes, and pushes segments to the hub. No Docker, no SRS, no port forwarding required.

- **Preview Mode toggle**: Per-instance radio button in the Instance Settings panel (SRT Bridge / NDI). Existing SRT bridge setups are unchanged.
- **NDI Source Name field**: Type the source directly (e.g. `VMIX-PC (vMix - Input 1 - Zoom)`) or click **Discover** to scan the LAN and pick from a live list.
- **Discover button**: Triggers a 4-second LAN scan via `ndi-python` and presents results as a temporary dropdown. On hub nodes the scan is proxied to the configured client site via HMAC-signed request so NDI sources visible at the studio are returned.
- **Installer prompt**: `install_signalscope.sh` now asks "Install NDI support for vMix Caller video preview?" during fresh install. Also supports `--ndi` / `--no-ndi` CLI flags.
- Graceful degradation: NDI mode silently falls back to a retry loop when `ndi-python` is not installed or the NDI source is not found. The SRT path is completely unaffected.

---

### vMix Caller 1.7.0 — 2026-04-27

**Feature: Zoom API integration (Phase 1)**

Hub acts as a Zoom Server-to-Server OAuth bridge — client nodes are the primary operator surface and proxy all Zoom data and actions through the hub. No Zoom credentials are ever stored on or exposed to client nodes.

- **Hub credentials panel**: Enter Zoom S2S OAuth Account ID, Client ID, and Client Secret in the vMix Caller hub page. "Save & Test" verifies the credentials and shows the connected account name and email.
- **Meetings list** (hub + client): Live list of upcoming and in-progress Zoom meetings fetched from the Zoom API (60 s cache, manual refresh). Each row shows live/scheduled status, meeting ID, scheduled time, and per-meeting **Join**, **End**, and **+Save** buttons.
- **Create meeting** inline form: Topic, passcode (optional), duration, and waiting room toggle. "Start Now & Join in vMix" creates the meeting and immediately joins it via the existing vMix Zoom function.
- **End meeting**: Confirmation prompt then ends the meeting for all participants via Zoom API.
- **+Save to presets**: Adds any listed meeting to the existing saved-meetings list for the Presenter View.
- **Hub ↔ client proxy**: Client node Flask routes proxy all Zoom requests to the hub using HMAC-signed GET (read) and approval-only POST (write). The browser never talks to the hub directly — no CORS issues.
- Token cache: S2S Bearer tokens cached for up to 1 hour (refreshed 30 s before expiry), protected by a threading lock.
- All existing vMix controls, video relay, saved meetings, and participants functionality is unchanged.

---

### Studio Board 3.14.19 — 2026-04-27

**Fix: emoji not rendering on Yodeck / Raspberry Pi displays + default cleared-studio logo**

**Emoji → standard Unicode / text replacements (Yodeck / Pi fix):**

Yodeck players run Chromium on Raspberry Pi OS which lacks a colour emoji font by default. Several emoji used in `_TV_TPL` appeared as blank boxes on those devices:
- Removed `📡` prefix from the status message bar (text label is sufficient)
- Replaced `🎙` (U+1F399) in the "Connecting…" splash with `◎` (U+25CE — standard Unicode ring, no emoji font needed)
- Removed `🎙` prefix from the VOICE TRACKING badge (the amber pulsing badge styling is already visually distinctive)
- Replaced `🎤` (U+1F3A4) in the Zetta no-artwork placeholder with `♪` (U+266A — standard Unicode music note)
- Stripped surrogate-pair emoji from all 24 IDLE automation messages (decorative suffixes only — the text remains)

**Default cleared-studio logo:**

Studios with no brand assigned ("Studio Free") now support a default logo image displayed in the cleared state. Configured in **Admin → Brands → Cleared Studio Default Logo**:
- Upload a PNG, JPEG, WebP, or SVG image (max 2 MB) — stored as `_default_logo.{ext}` in `studioboard_art/`
- The logo appears centred in the studio card whenever the studio is cleared and not in VT mode
- Hidden during VOICE TRACKING (amber badge takes priority)
- Upload / Remove buttons in the Brands admin tab; no page reload needed
- Logo responds to live poll updates — shown/hidden within the next 10 s poll cycle if changed

---

### vMix Caller 1.6.7 — 2026-04-27

**Fix: client page call controls silently do nothing / better vMix API diagnostics**

Three improvements to the client node page (`_CLIENT_TPL`):

1. **Meeting ID space stripping** — users often paste meeting IDs formatted as "123 456 7890". The spaces were being URL-encoded and sent to vMix verbatim, causing vMix to silently reject the join (HTTP 200 returned but Zoom doesn't connect). `joinWith` now strips all whitespace from the meeting ID before sending.

2. **vMix response body visibility** — when vMix returns a non-empty response body to a function call (which can happen on errors like "Zoom input not found" or "invalid meeting ID"), it was previously discarded and the JS always showed the generic "Joining…" message. `joinWith` now shows the vMix response body if non-empty, coloured red if it contains "error" or "false", so silent API failures are immediately visible.

3. **Test vMix shows address** — `testVmix` result now includes the IP:port being tested (e.g. "✓ vMix reachable (192.168.1.5:8088) — v27.0.0.122"), confirming which machine is being reached and helping diagnose "wrong vMix" scenarios.

---

### vMix Caller 1.6.6 — 2026-04-26

**Fix: Join meeting buttons and all call controls did nothing on hub page**

The hub page's `sendCmd` override checked that a site was selected in the dropdown but never included the selected site in the POST body sent to `/api/vmixcaller/function`. The backend read `target_site` from the saved config file — if the operator hadn't explicitly saved an instance with that site, the backend returned "No target site selected — save settings first" and queued nothing. Similarly, `loadState()` polled `/api/vmixcaller/state` without passing the current site, so the vMix connection status always reflected the *saved* target site rather than whatever was selected in the dropdown.

Fixed by:
- Hub `sendCmd` override now passes `site: siteVal` in the POST body (and duplicates the full `_post` + error-show chain to avoid calling the base helper that lacks site)
- `loadState()` passes `?site=<selected>` as a query param on every state poll
- `vmixcaller_function` backend reads `data.get("site")` first, falls back to saved config
- `vmixcaller_state` backend reads `request.args.get("site")` first, falls back to saved config

Commands and status now work immediately when a site is chosen in the dropdown, without requiring a prior "Save Instance" step.

---

### vMix Caller 1.6.5 — 2026-04-26

**Fix: Save Instance returns SyntaxError (405 Method Not Allowed)**

The `_post()` JS helper always sends HTTP POST, but the `/api/vmixcaller/instances/<id>` route was registered with `methods=["PUT"]` only. Flask returned a 405 HTML error page; the JS `.then(r => r.json())` chain failed to parse it with `SyntaxError: The string did not match the expected pattern`. Fixed by registering the route for both POST and PUT.

---

### vMix Caller 1.6.4 — 2026-04-26

**Fix: JS syntax error broke all vMix connection and video preview**

A stray `var` keyword was left before a comment line in `_JS_HELPERS` during the 1.6.2 Zoom API fix:

```javascript
var // vMix API: ZoomJoinMeeting Value = "MeetingID,Password"...
var _lastJoin={...};
```

`var` expects an identifier; finding another `var` keyword after the comment is a syntax error. The entire `_JS_HELPERS` script block crashed before any function was defined, so `loadState()` was never called (status stuck at "Checking vMix"), `initPreview()` never ran (video stuck at "Waiting for caller"), and no controls worked. Fixed by removing the stray `var`.

---

### Morning Report 1.2.5 — 2026-04-26

**Fix: Chain count and fault count inflated by stream-level events and old DB data**

The report was showing e.g. "55 interruptions across 18 chains" when only 3 chains were configured. Three compounding bugs fixed:

- **`all_chain_names` included raw stream names**: Every SILENCE/STUDIO_FAULT/TX_DOWN event in the alert log has a `stream` field (e.g. "BBC R2 128k"). The old code unioned these stream names with chain names, making each monitored stream appear as a separate "chain". With 15 streams across 3 chains, that produced 18 "chains". Fix: `all_chain_names` is now anchored to the live configured chains from `monitor.app_cfg.signal_chains`. Orphan stream names and old DB chain IDs that no longer exist in config are excluded.
- **`faults_per_chain_y` added stream names as chain keys**: The alert log loop added every stream name from SILENCE/STUDIO_FAULT events as a separate fault counter, again inflating counts. Fix: stream-level events are now mapped back to their owning chain via a `stream_to_chain` map (built by walking each chain's node list). Only attributed to a chain if the chain is configured and has no chain_fault_log entry (to avoid double-counting).
- **`total_faults` double-counted every chain fault**: `len(fault_events_y) + len(chain_faults_y)` summed alert log events (which include CHAIN_FAULT type) AND chain_fault_log SQLite entries — the same fault appeared in both. Fix: `total_faults = sum(faults_per_chain_y.values())` from the deduplicated per-chain counts. CHAIN_FAULT events from the alert log are skipped entirely (chain_fault_log is the authoritative source).
- **7-day averages used stream names as keys**: `faults_by_stream_day` was keyed by raw stream names, but the chain health table looked up by chain name — always returning 0. Fix: events are now keyed by chain name via `stream_to_chain`; chain_fault_log 30-day data is also incorporated into the per-day counts.

---

### vMix Caller 1.6.3 — 2026-04-26

**Fix: Top nav bar broken on vMix admin page**

Added `header{display:flex;align-items:center;gap:12px;padding:0}` to the plugin CSS. The `topnav()` function renders a `<header>` with no `display:flex` inline style; without this rule the logo, version pill, and nav stack vertically instead of laying out horizontally. Fixes the version number pushing the nav bar down on the hub admin page.

---

### vMix Caller 1.6.2 — 2026-04-26

**Fix: Zoom controls corrected against official vMix API (v28/v29)**

Cross-referenced all Zoom functions against the official vMix Shortcut Function Reference. Multiple bugs found and fixed:

- **`ZoomJoinMeeting` wrong separator**: Value must be `MeetingID,Password` (comma-separated per official API). Plugin was sending `MeetingID|Password|DisplayName` with pipes. All call sites (joinWith, reconnect) and the Python backend shim updated.
- **`ZoomMuteSelf` toggle broken**: Always called `ZoomMuteSelf` for both mute and unmute. The official API has two separate functions — `ZoomMuteSelf` to mute and `ZoomUnMuteSelf` to unmute. Fixed to call the correct function based on current mute state.
- **`ZoomMuteAllParticipants` removed**: Does not exist in the official vMix API. Mute All button removed from hub, client, and presenter pages.
- **`ZoomStopVideo`/`ZoomStartVideo` removed**: Do not exist in the official vMix API. Stop Camera button and `stopCamera()` function removed from all pages. `C` keyboard shortcut removed.
- **Display name removed from join form**: `ZoomJoinMeeting` does not accept a display name — the Zoom account name configured inside vMix is used. Removed the "Display Name" / "Your Name" field from manual join forms on all three pages to avoid misleading users.
- **Python backend pipe-shim removed**: The `vmixcaller_function` handler had a backwards-compat block that split pipe-delimited values into Value2/Value3 params. Now that JS sends the correct comma format, this shim is no longer needed and was removed.

Remaining official vMix Zoom API: `ZoomJoinMeeting` (Value=ID,Pass), `ZoomMuteSelf`, `ZoomUnMuteSelf`, `ZoomSelectParticipantByName` (Value=Name). `ZoomLeaveMeeting` is kept (not in official docs but may work in some vMix versions).

---

### vMix Caller 1.6.1 — 2026-04-26

**Fix: Client page instance management fully functional**

The 1.6.0 client page had the instance management HTML card but the JavaScript was incomplete — `_instances`, `_activeInstId`, `saveInstance`, `newInstance`, `deleteInstance`, and `_getInstById` were only defined in the hub template. Clicking Save/New/Delete on the client page silently failed; the instance selector `change` event was not wired up; `_populateInstForm` was not called on load.

Fix: all instance management functions and variables are now also defined in the client template's script block. On DOMContentLoaded the form is pre-populated from the active instance and the selector is wired to update the form on change. Client `saveInstance` calls `PUT /api/vmixcaller/instances/<id>` with `activate:true` (no site push — the client manages its own config directly).

---

### vMix Caller 1.6.0 — 2026-04-26

**Feature: Multiple saved vMix instances — one SRS handles all streams by name**

Each saved instance bundles a vMix connection (IP, port, Zoom input) with a preview URL (SRT/WebRTC bridge stream). One SRS Docker container handles multiple instances via different stream names — e.g. `webrtc://192.168.1.50/live/caller1`, `.../caller2`, `.../caller3` — same ports, different paths.

- **Saved instances**: create, rename, edit, delete from the hub operator page. Old single-config installations migrate automatically to a "Default" instance on first load.
- **Hub page**: new "Site & Instance" card — instance selector dropdown, editable fields (name, vMix IP, port, Zoom input, Preview URL), "Save Instance & Push to Site" sends the active instance's config to the client node.
- **Presenter page**: instance pill selector strip shown above the video hero when more than one instance is configured. Clicking a pill activates that instance and reloads the video preview.
- **Client page**: same instance pill selector above the preview card.
- **API**: `GET/PUT/DELETE /api/vmixcaller/instances/<id>`, `POST /api/vmixcaller/instances/<id>/activate`.
- All existing code (client polling thread, video relay, `__set_config__` push) continues to work unchanged via backwards-compat flat-field injection.

---

### SignalScope-3.5.174 — 2026-04-26

**Fix: CHAIN_FAULT notifications suppressed by cooldown when chain had already recovered (orphan CHAIN_RECOVERED)**

Root cause: `_zetta_on_bypass` (added with Zetta integration) bypasses the confirmation window for non-ad-break faults, causing brief faults to fire `CHAIN_FAULT` immediately and stamp `_chain_last_alert_ts`. If the chain recovered and then faulted again within the cooldown window, the second `CHAIN_FAULT` was suppressed — but `_chain_last_recovery` had been updated when the chain came back. After the cooldown window elapsed, the recovery notification passed the timestamp check and fired, giving an orphan `CHAIN_RECOVERED` with no preceding fault alert.

Fix: the cooldown suppression in `_fire_chain_fault` now checks whether a recovery has occurred since the last notification. If `_chain_last_recovery[cid] > _chain_last_alert_ts[cid]`, the chain has recovered and re-faulted — a new fault cycle — so the cooldown is bypassed and the fault always alerts. Cooldown is only applied when `last_recovery <= last_alert` (same fault cycle, no recovery in between, e.g. flapping).

Also reverted the incorrect `_chain_fault_notified` dict introduced in 3.5.173, which suppressed recovery notifications whenever a fault was suppressed — making a bad situation worse by silencing valid recovery alerts.

---

### SignalScope-3.5.173 — 2026-04-26

**Fix: CHAIN_RECOVERED firing without a preceding CHAIN_FAULT notification** *(superseded by 3.5.174)*

---

### vMix Caller 1.5.23 — 2026-04-26

**Fix: ZoomJoinMeeting passes Value/Value2/Value3 correctly; fix ZoomStopCamera and ZoomMuteAll function names; add Reconnect button; vMix input accepts names**

- **ZoomJoinMeeting**: vMix API requires meeting ID as `Value`, password as `Value2`, display name as `Value3`. The JS was passing these pipe-delimited into a single `Value` which vMix silently ignored. Fixed in `_vmix_fn` (added `value2`/`value3` URL params) and the server-side handler now splits the pipe-delimited value before calling vMix. The hub→client command queue and client execution path also updated.
- **ZoomStopCamera → ZoomStopVideo / ZoomStartVideo**: `ZoomStopCamera` is not a documented vMix function. `stopCamera()` now sends `ZoomStopVideo` or `ZoomStartVideo` depending on current camera state.
- **ZoomMuteAll → ZoomMuteAllParticipants**: corrected to the documented vMix function name.
- **Reconnect button**: presenter page shows a 🔄 Reconnect button after the first join. Stores last meeting ID/password/name and re-sends ZoomJoinMeeting. Hidden until a meeting has been joined in this session.
- **vMix input accepts names**: input field changed from `type="number"` to `type="text"` — vMix accepts either an integer index or the input name string (e.g. "Zoom Call 1"). `saveConfig` JS updated to pass string names through without parseInt coercion.
- **Teams clarification**: updated plugin description — Teams has no vMix call-control API; Teams NDI feeds can be monitored passively but not joined/controlled by this plugin.
- **Participant timing hint**: note added below participant list that Zoom may take 15–30 s to populate after joining.

### vMix Caller 1.5.22 — 2026-04-26

**Fix: "Hear Caller" button missing from presenter page**

The presenter page's audio button was inside `#call-bar` which is only shown when a call is active. Moved it below the video hero so it is always accessible. Removed the duplicate button from the call bar.

### vMix Caller 1.5.21 — 2026-04-26

**Fix: "Hear Caller" audio — use separate Audio element instead of vid.muted**

All previous approaches (vid.muted toggle, vid.play() calls, removing HTML muted attribute) failed because Chrome's autoplay policy refuses to let a video element that was started muted suddenly output audio, regardless of how `muted` is set. Fix: `toggleAudio()` now creates a dedicated `new Audio()` element connected to only the audio tracks from the stream's MediaStream and calls `play()` directly inside the user-gesture click handler. This is completely independent of the `<video>` element's mute state. Added "Hear Caller" button to the client and hub pages (previously only existed on the presenter page). Shared `toggleAudio`/`_syncAudioBtn` moved to `_JS_HELPERS` so all three pages use the same implementation. `_teardownPreview` cleans up the audio element when the stream tears down.

### vMix Caller 1.5.20 — 2026-04-26

**Fix: "Hear Caller" audio still silent — HTML `muted` attribute resets mute state on play()**

Root cause: the `<video>` element had the HTML `muted` attribute, which sets `defaultMuted = true`. In Chrome/Safari, any `play()` call on such an element resets `vid.muted` back to `true`. So `toggleAudio()` set `vid.muted = false`, then `vid.play()` immediately re-muted it.

Fix:
- Removed the HTML `muted` attribute from all three `<video id="pvid">` elements (so `defaultMuted = false`)
- Set `vid.muted = true` via JavaScript in `_startWhep()` before the first `play()` call — autoplay still works (element is muted), but `defaultMuted` stays `false`
- Removed the `vid.play()` call from `toggleAudio()` — with `defaultMuted = false`, simply setting `vid.muted = false` is sufficient and no longer risks a re-mute

### vMix Caller 1.5.19 — 2026-04-26

**Fix: "Hear Caller" button produces no audio (WebRTC) — root cause**

The real cause: `ontrack` fires once per transceiver (audio then video). The previous code relied on `e.streams[0]` and re-assigned `vid.srcObject` on each event. When the SRS/vMix server returns no MSID in its SDP answer (common), `e.streams` is empty for the audio track — the guard `if(e.streams&&e.streams[0])` silently skipped it — and `vid.srcObject` was overwritten by the video-only stream on the video track event. Result: the audio track was never attached to the element.

Fix: create one `MediaStream` upfront, assign it to `vid.srcObject` once, and call `_ms.addTrack(e.track)` for every arriving track. Both audio and video are always present in the element's source regardless of whether the server includes MSIDs.

### vMix Caller 1.5.18 — 2026-04-26

**Fix: "Hear Caller" button produces no audio (WebRTC)**

Clicking "Hear Caller" to unmute the caller video/audio feed produced no sound even though the video was playing correctly. Root cause: setting `vid.muted = false` alone does not restart the audio pipeline in Chrome or Safari when the `<video>` element was initially started in the `muted` autoplay state. Fixed by calling `vid.play()` immediately after unmuting within the user-gesture context so the browser activates the audio track.

---

### SignalScope-3.5.172 — 2026-04-26

**Feature: Studio Board assignments on Broadcast Chains page**

Each chain card on the Broadcast Chains page now shows a `📺 Studio Name` badge when the chain is assigned to one or more studios in the Studio Board plugin configuration. The `/api/chains/status` endpoint includes a `studios` list in each chain result (empty list when Studio Board is not installed or the chain has no studio assignment). Gracefully absent when the Studio Board plugin is not loaded.

---

### Studio Board 3.14.18 — 2026-04-26

**Feature: Backup & Restore**

New Backup tab in the Studio Board admin page. "Download Backup" produces a ZIP containing `studioboard_cfg.json` and all show artwork images — all studio IDs, brand IDs, and API tokens are preserved exactly. "Restore from ZIP" uploads the backup, validates the structure, writes the config and artwork, and reloads all connected TV displays immediately via SSE. Restore is blocked for viewer-role accounts by the existing RBAC middleware.

---

### Studio Board 3.14.17 — 2026-04-26

**Feature: Expose chain→studio assignments to Broadcast Chains page**

Studio Board now publishes a `monitor._studioboard_chain_studios` dict (`{chain_id: [studio_name, ...]}`) that is rebuilt from config on every save. The Broadcast Chains API (`/api/chains/status`) reads this and includes a `studios` list in each chain result. Each chain card on the Broadcast Chains page now shows a `📺 Studio Name` badge when the chain is assigned to one or more studios in the Studio Board configuration.

---

### SignalScope-3.5.171 — 2026-04-26

**Fix: Chain confirmation window bypassed when no mixin node is configured**

Previously, `min_fault_seconds` (the confirmation window) was always respected regardless of whether a mixin node was set on the chain. This meant a chain with `min_fault_seconds = 330` but no mixin node would silently swallow any outage shorter than 330 seconds — never alerting — because the confirmation window serves no ad-break suppression purpose without a mixin point.

Fix: added `_no_mixin_bypass` condition. When a chain has no mixin node (`mixin_node_idx` is unset) and the fault is not a stack-based ad-break candidate, the confirmation window is bypassed and the fault alerts immediately. Chains that rely on `min_fault_seconds` purely for brief-dropout suppression are unaffected as long as they have a mixin node configured.

Logged as: `[Chain] 'Name' — no mix-in node configured, bypassing Ns confirmation window.`

---

### Logger 1.6.4 — 2026-04-26

**Bug fix: Client nodes no longer behave as hub instances**

Logger was using `hub_server is not None` / `hub_server is None` to detect whether it was running on a hub or client node. However, `hub_server` in the plugin `ctx` is always a `HubServer()` instance — never `None` — so every node evaluated as a hub. Client nodes would enter hub-mode code paths, fail to register with the remote hub, and appear stuck on "connecting to recordings".

Fix: `register()` now derives `is_hub` from the `mode` string in `monitor.app_cfg` (`mode in ("hub", "both")`), which is the correct way to detect hub vs client mode in SignalScope plugins. All 12 affected checks throughout `register()` have been updated.

---

### vMix Caller 1.5.15 — 2026-04-26

**Redesign: Presenter page matches Producer View design language + locked-down navigation**

The presenter page (`/hub/vmixcaller/presenter`) has been redesigned to match the Producer View plugin's visual style, and all links to the rest of SignalScope have been removed.

Design changes:
- **Custom sticky header** replaces the full SignalScope navbar. Shows "Caller" title and a live status sub-line ("Ready to join" → "● On call" in green). The only link outside the page is a subtle "SignalScope" watermark. No nav links, no route back to the hub or settings.
- **In-call action bar** replaces the old floating call-bar. When a meeting starts it appears as a styled green-accented panel with Mute Self, Stop Camera, and Leave buttons. Collapses when the meeting ends. The manual join section also auto-collapses when a call starts.
- **Video hero** is full-width with a prominent 16:9 aspect-ratio card, rounded corners, drop shadow — the video takes centre stage.
- **Meeting cards** displayed in a responsive grid (like the Producer View station cards). Each card has a coloured gradient avatar initial, meeting name, ID, and a full-width "📞 Join" button. Gradient avatars cycle through 7 brand colours keyed by position.
- **Manual join** collapses to a subtle underlined link; expands to a clean card with larger inputs.
- **Keyboard hint footer** (M / C) subtle, very low opacity — visible if you know to look, unobtrusive otherwise.

Removed:
- `{{topnav("vmixcaller")|safe}}` — no SignalScope navigation bar
- "⚙ Hub Controls" link in the meetings card header
- All `href="/hub/vmixcaller"` anchor tags on the presenter page

---

### vMix Caller 1.5.14 — 2026-04-25

**Feature: Client node page now has full admin UI**

The client node page (`/hub/vmixcaller` on a client node) has been completely rewritten to match the hub operator page in functionality. The old minimal config-only page is replaced with:

- **Status bar** — live vMix connection indicator (green/amber/red dot + version), polled directly from vMix every 8 s via the new `GET /api/vmixcaller/local_state` endpoint. No 12-second hub reporting delay.
- **Meeting Controls card** — join form (Meeting ID, Passcode, Display Name) + in-call buttons (Mute Self, Stop Camera, Mute All, Leave). Commands execute directly against local vMix (no hub round-trip needed).
- **Participants card** — live list from vMix XML, with Put On Air and manual-add, refreshed on each state poll.
- **Saved Meetings card** — full add/delete/join-with-one-click management, shared with the hub and presenter page.
- **vMix Settings card** — IP, port, input number, Preview URL, Save and Test buttons (all via `data-action=` — CSP-safe).
- **Presenter View** link in the status bar header.
- Uses `_JS_HELPERS` (shared JS block) — identical meeting control logic, keyboard shortcuts (M/C), and `setMeetingState` as the hub and presenter pages.

New backend endpoint:
- `GET /api/vmixcaller/local_state` — fetches vMix XML directly, returns `{ok, ts, version, participants}`. Available on all node modes (hub, client, standalone).

---

### vMix Caller 1.5.13 — 2026-04-25

**Fix: relay sits at "Connecting" forever — two bugs**

1. **`parsed` undefined for webrtc:// bridge URLs (NameError, silent)**: The relay loop
   sets `base` and `manifest_url` for `webrtc://` URLs but never set `parsed`. The
   subsequent line `manifest_dir = parsed.path.rsplit(...)` raised `NameError` every
   cycle, caught silently by the outer `except Exception: pass`. No segments were ever
   fetched or pushed to the hub. Fix: add `parsed = urlparse(manifest_url)` immediately
   after deriving the manifest URL in the `webrtc://` branch.

2. **Hub waits for `relay_active: true` report before loading HLS**: The client only
   sends its full status report every 12 seconds, so the hub wouldn't start loading
   `relay.m3u8` for up to 20 seconds after the relay started. Fix: `toggleRelay()` now
   calls `initPreview('/hub/vmixcaller/video/relay.m3u8')` immediately when the relay is
   requested. hls.js handles the empty-but-valid manifest gracefully and retries until
   segments arrive (typically 3–6 s). The "● Live" badge still uses `relay_active` from
   the periodic report — it just no longer gates the video load.

---

### vMix Caller 1.5.12 — 2026-04-25

**Feature: On-demand HLS relay from client node to hub**

Hub operators can now watch the caller video preview from the hub page, even when SRS is on the client LAN and the hub browser can't reach it directly.

How it works:
- A **"📡 Stream to hub"** button appears in the Caller Preview card header whenever the hub can't reach SRS directly (i.e. when the Preview URL is a `webrtc://` LAN address or similar). It's hidden when the hub already has a local preview.
- Clicking it sends a `relay: "start"` command to the client node via the existing 3-second poll. The button immediately shows "Connecting…".
- The client node starts pulling HLS segments from SRS (`http://HOST:8080/APP/STREAM.m3u8`, derived automatically from the `webrtc://` bridge URL) and pushing them to the hub every ~1.5 s.
- Once the client confirms `relay_active: true` in its next status report (~12 s), the hub preview switches to the buffered HLS stream at `/hub/vmixcaller/video/relay.m3u8` and the button shows "● Live".
- **"⏹ Stop stream"** stops the relay immediately, flushes buffered segments, and the hub preview returns to its previous state.
- The relay only runs when explicitly requested — zero bandwidth overhead at rest.
- `webrtc://HOST/APP/STREAM` bridge URLs are now automatically converted to `http://HOST:8080/APP/STREAM.m3u8` for the relay loop (SRS exposes both protocols simultaneously on the same stream).
- Relay thread now also starts on `mode=both` nodes.

---

### vMix Caller 1.5.11 — 2026-04-25

**Fix: WebRTC preview "failed to fetch" — CORS/mixed-content on direct WHEP call**

The browser was calling the SRS WHEP endpoint (`http://192.168.x.x:1985/rtc/v1/whep/`) directly from JavaScript. SRS does not send `Access-Control-Allow-Origin` headers on its WHEP endpoint, so the browser blocked the cross-origin POST. On HTTPS-hosted SignalScope instances it also fails as mixed content (HTTP fetch from an HTTPS page). Both produce the same generic "failed to fetch" error.

Fix: added `POST /api/vmixcaller/whep_proxy?url=<encoded>` route. The browser now sends the SDP offer to SignalScope (same origin — no CORS); SignalScope forwards it server-to-server to SRS and returns the SDP answer. The RTCPeerConnection, ICE, and video track reception still happen natively in the browser — only the SDP exchange is proxied. Works on hub, client, and standalone nodes.

---

### vMix Caller 1.5.10 — 2026-04-25

**Fix: WebRTC/HLS preview overlay never hides — video appears blank**

All three preview overlay divs (`_PRESENTER_TPL`, `_HUB_TPL`, `_CLIENT_TPL`) had `id="pvw-ov"` but were missing `class="pvw-ov"`. The CSS rule `.pvw-ov.hidden{display:none}` is a compound class selector — it only matches elements that have **both** the `pvw-ov` class and the `hidden` class. Because the element had no class at all, `ov.classList.add('hidden')` added the `hidden` class but the CSS rule never matched, so the overlay remained visible. The video was playing underneath but the camera icon and status message sat on top, making the preview appear completely blank.

Fix: added `class="pvw-ov"` to all three overlay divs so the CSS selector works correctly.

---

### vMix Caller 1.5.9 — 2026-04-25

**Fix: ALL buttons dead on every page since 1.5.3 — root cause found**

Root cause confirmed by inspecting the live page source. Jinja2's HTML autoescape was converting the `"` characters in `{{video_url_json}}` to `&#34;`, so the rendered JavaScript was:

```
var _videoUrl = &#34;/hub/vmixcaller/video/relay.m3u8&#34;;
```

The `&` at column 16 (right after `var _videoUrl = `) is a `SyntaxError: Unexpected token '&'` that kills the entire `<script>` block before a single function is defined. Every button on every page silently did nothing — no console error visible unless DevTools was open.

Fix: `{{video_url_json|safe}}` in all three templates (`_HUB_TPL`, `_PRESENTER_TPL`, `_CLIENT_TPL`). The `|safe` filter tells Jinja2 the value is already safe and must not be HTML-escaped. `json.dumps()` output used in a `<script>` block does not need HTML escaping.

This bug was present from 1.5.3 (when video preview was introduced) through 1.5.8.

---

### vMix Caller 1.5.8 — 2026-04-25

**Fix: ALL buttons broken on hub, presenter, and client pages (CSP)**

Root cause confirmed: SignalScope's CSP `script-src-attr 'unsafe-hashes'` policy only hashes `onclick=` values from its own core templates at startup. Plugin templates are never scanned, so **every single** `onclick=` attribute in every vMix Caller page was silently blocked by the browser — no JS error, buttons just did nothing.

Fix: removed every `onclick=` attribute from all three templates (`_HUB_TPL`, `_PRESENTER_TPL`, `_CLIENT_TPL`). Replaced with `data-action="fnName"` attributes. Added a single delegated `click` listener to `_JS_HELPERS` that dispatches by `btn.dataset.action` — this lives inside a nonce-protected `<script>` block, which CSP always allows. `onkeydown=` inline handlers (Enter-to-submit on text inputs) also removed and replaced with explicit `addEventListener` calls in `DOMContentLoaded`.

---

### vMix Caller 1.5.7 — 2026-04-25

**Fix: Save and Test vMix buttons broken on client node page (CSP)**

Root cause: `_CLIENT_TPL` buttons had `onclick="saveClient()"` and `onclick="testLocal()"` attributes. SignalScope's CSP policy (`script-src-attr 'unsafe-hashes'`) only hashes onclick values known at startup from main-app templates — plugin template onclick values are never pre-hashed, so the browser silently blocked both click handlers on every page load.

Fix: removed `onclick=` from both buttons; wired them instead via `addEventListener` inside the nonce-protected `<script>` block, which is always allowed by CSP regardless of hashing. Also wrapped the `initPreview` DOMContentLoaded call in `try/catch` for robustness.

---

### vMix Caller 1.5.6 — 2026-04-25

**Feature: native WebRTC preview via WHEP (SRS)**

The Preview URL field now accepts three formats:

- `webrtc://host/app/stream` — **new native WebRTC mode** (recommended). JavaScript parses the URL, constructs the SRS WHEP endpoint (`http://host:1985/rtc/v1/whep/?app=APP&stream=STREAM`), and plays directly into a `<video>` element via `RTCPeerConnection`. No iframe, no relay needed. Zero latency. Works on any Chromium or Firefox browser.
- `http://host:8080/players/rtc_player.html?...` — SRS player page embedded in an `<iframe>` (legacy).
- `http://host:8080/live/caller.m3u8` — HLS stream via hls.js (original mode, still fully supported).

Mode is detected automatically from the URL. Config field label updated to "Preview URL" across hub, client, and presenter pages.

`_compute_video_url` updated: `webrtc://` and player-page URLs are passed through as-is to the browser for client nodes; hub nodes with LAN addresses get `""` (browser on remote hub can't reach LAN SRS via WebRTC).

`initPreview` refactored with `_teardownPreview` (cleans up both `RTCPeerConnection` and hls.js instance on re-init) and `_startWhep` helper. Both `_JS_HELPERS` (hub/presenter) and the `_CLIENT_TPL` inline copy are updated identically.

The SRT Bridge Setup guide in the hub page is updated to lead with WebRTC / SRS WebRTC player, with HLS as the secondary option.

---

### vMix Caller 1.5.5 — 2026-04-25

**Fix: Save and Test vMix buttons still broken on client page**

Root cause: the 1.5.3 rewrite injected `_JS_HELPERS` (the full hub/presenter JS block — meeting state management, keyboard shortcuts, `sendCmd`, etc.) into `_CLIENT_TPL`. This code is designed for hub/presenter pages which have the matching DOM elements; loading it on the simpler client page caused a runtime JavaScript error that silently killed the entire script block before `saveClient` and `testLocal` were reached.

Fix: rewrote `_CLIENT_TPL` as a self-contained page without `_JS_HELPERS`. The script block contains only what the client page needs: `_csrf()`, `showMsg()`, `initPreview()` (inline copy for the video preview), `saveClient()`, `testLocal()`, and the DOMContentLoaded preview init. `saveClient()` and `testLocal()` are restored to the proven 1.5.2 style (direct `fetch` calls, not abstracted through `_post()`).

The video preview (`initPreview`, hls.js, preview card) introduced in 1.5.3 is retained.

---

### vMix Caller 1.5.4 — 2026-04-25

**Fix: Save and Test vMix buttons unresponsive on LAN-only client nodes**

Client nodes on studio LANs without internet access could not reach the hls.js CDN. The blocking `<script src="...hls.min.js">` tag (no `defer`) caused the browser to stall HTML parsing indefinitely, so the inline script block containing `saveClient()` and `testLocal()` never executed — both buttons appeared to do nothing.

Fix: added `defer` to the hls.js `<script>` tag in all three templates (`_PRESENTER_TPL`, `_HUB_TPL`, `_CLIENT_TPL`). With `defer`, the CDN fetch happens in the background without blocking parsing; inline scripts execute normally even if the CDN is unreachable. The video preview is unaffected — hls.js still loads before `DOMContentLoaded` when the CDN is available.

---

### vMix Caller 1.5.3 — 2026-04-25

**Feature: video preview on client node page**

The client node's vMix Caller page (`/hub/vmixcaller` on a client-mode node) now shows the full caller video preview, matching the hub operator page and presenter page. Previously the client page showed only the config fields with no video.

Changes:
- Added hls.js CDN script to `_CLIENT_TPL` head (same as hub/presenter pages)
- Added 16:9 `pvw-wrap` preview card at the top of the client page
- Replaced inline `_csrf`/`showMsg` with the shared `_JS_HELPERS` block (includes `initPreview`, hls.js wiring, keyboard shortcuts)
- `saveClient()` now re-fetches `/api/vmixcaller/video_url` after save and calls `initPreview()` — bridge URL change is reflected immediately without a page reload
- Route passes `video_url_json` (computed via `_compute_video_url(cfg, False)`) so the preview initialises on page load when a bridge URL is already configured
- Overlay message shows "Configure a Bridge URL below" when no bridge is set vs "Waiting for caller…" when one is configured

---

### vMix Caller 1.5.2 — 2026-04-25

**Fix: HLS video not playing in Chrome/Edge; hub shows no video when bridge_url only set on client**

Two bugs fixed:

**1. Chrome/Edge don't support HLS natively.** The `<video src="...m3u8">` approach only works in Safari. vMix runs on Windows (Chrome/Edge) — setting `vid.src` to an HLS manifest produces a silent failure. Fixed by loading **hls.js** from the jsDelivr CDN (nonce-authenticated via SignalScope's CSP). `initPreview()` now tries `Hls.isSupported()` first (Chrome/Edge/Firefox), falls back to `vid.canPlayType('application/vnd.apple.mpegurl')` (Safari), and shows an error if neither works.

**2. Hub shows no video when bridge_url was set only on the client config page.** The hub's presenter and operator pages rendered `video_url_json` as empty because the hub's own `vmixcaller_config.json` had no `bridge_url`. This caused `initPreview('')` → no video, even though the relay buffer was full. Fixed by:
- New `_compute_video_url(cfg, is_hub_node)` helper: returns `/hub/vmixcaller/video/relay.m3u8` for hub nodes regardless of whether `bridge_url` is configured (the relay buffer is always the right source for hub users). Returns the direct proxy path for client nodes.
- Both page routes now pass `video_url_json` (the pre-computed proxy URL) to the template. JS uses `_videoUrl = {{video_url_json}}` directly — no client-side `_proxyUrl()` conversion needed.
- New `GET /api/vmixcaller/video_url` endpoint so `saveConfig()` can fetch the correct URL after a config change and re-init the video without a page reload.
- Proxy route refactored: `relay.m3u8` always serves synthetic manifest from buffer; TS segment requests (`seg/N.ts`) always from buffer; other `.m3u8` paths on a localhost bridge proxy directly to SRS. Empty relay buffer returns a valid empty manifest (not 503) so hls.js retries rather than giving a fatal error.

### vMix Caller 1.5.1 — 2026-04-25

**Fix: bridge_url never reached the client — video broken everywhere**

Root cause: `bridge_url` was saved in the hub's `vmixcaller_config.json` but never pushed to the client node. This broke three things simultaneously:

1. **Client presenter page** showed "contact your engineer" because `cfg.bridge_url` was empty on the client
2. **Video relay thread** (`_video_relay_loop`) read `bridge_url` from its own config, found nothing, and never started — so the hub's relay buffer stayed empty
3. **Hub presenter/operator page** returned 503 for every video request because the relay buffer was empty

Three fixes:

- `vmixcaller_save_config`: now always queues a `__set_config__` command for the target site whenever any config is saved (previously only triggered on `vmix_ip` / `vmix_port` changes). The command now includes all four fields: `vmix_ip`, `vmix_port`, `vmix_input`, `bridge_url`.
- `_start_client_thread` `__set_config__` handler: now saves all four fields (previously only `vmix_ip` and `vmix_port`).
- `_CLIENT_TPL`: bridge URL field added to the client config page so it's visible, shows the pushed value, and can be set manually as a fallback.

**To apply:** on the hub, open vMix Caller, confirm the Bridge URL is set, then click **Save & Push to Site**. The client will receive the bridge URL on its next poll (~3 s) and the relay will start automatically.

### vMix Caller 1.5.0 — 2026-04-25

**HLS video relay — hub remote users get live caller video**

Previously the hub's video proxy returned 503 immediately for LAN bridge URLs because the internet-facing hub can't reach a studio LAN IP. Remote users on the hub saw no video.

Fix: the client node now runs a dedicated `VmixCallerVideoRelay` background thread. It polls the local LAN bridge manifest every 1.5 s, fetches each new TS segment, and POSTs it to `POST /api/vmixcaller/video_push` on the hub. The hub buffers the last 6 segments (~12–18 s) in memory. When a remote browser requests `/hub/vmixcaller/video/live/caller.m3u8`, the hub generates a synthetic HLS manifest pointing to buffered segment URLs (`/hub/vmixcaller/video/seg/N.ts`), which are served directly from the buffer.

Result: the same `/hub/vmixcaller/video/...` URL works everywhere:
- **Hub (remote users)** — served from relay buffer, ~5–10 s startup delay while buffer fills
- **Client node (LAN users)** — proxied directly to LAN bridge, instant, no relay overhead
- **Hub with localhost bridge** — proxied directly as before

Video relay thread only starts on `mode == "client"` nodes (not hub/both, which can reach the bridge directly).

### vMix Caller 1.4.1 — 2026-04-25

**Fix: hub video proxy causes timeout traceback for LAN bridge URLs**

When the bridge URL is a LAN IP (e.g. `http://192.168.13.2:8080/...`), the hub node is on the internet and cannot reach it. Previously the proxy route attempted a connection anyway, timed out after 8 s, then called `abort(502)` which Flask logs as a full traceback — one per HLS segment request, filling the log with noise.

Fix: `vmixcaller_video_proxy` now checks `is_hub` and the bridge URL hostname before attempting any connection. If the hub detects a non-localhost bridge URL it immediately returns a plain `503` response with no connection attempt, no timeout delay, and no traceback. The browser video element shows the unavailable overlay cleanly. Error returns across the board changed from `abort()` to `Response()` to suppress Flask's exception logging.

### vMix Caller 1.4.0 — 2026-04-25

**HTTPS-safe video proxy — no cert required for LAN bridges**

- `_proxyUrl()` now always routes video through the local SignalScope proxy (`/hub/vmixcaller/video/<path>`) rather than trying to detect LAN vs localhost. The proxy route is registered on **all** nodes (hub and client), so:
  - **Hub with localhost bridge**: hub proxy fetches from 127.0.0.1 — works as before
  - **Hub with LAN bridge + HTTPS**: hub proxy fails (can't reach LAN), but the presenter opens the presenter page from the **client node** URL (HTTP, same LAN). The client's proxy fetches from the LAN bridge — no mixed content, no cert, no config changes needed
- Setup guide updated with a clear "Presenter bookmark — hub on HTTPS" section explaining the client node URL pattern (`http://client-node-ip:port/hub/vmixcaller/presenter`)
- Warning on the presenter page updated to point to the client node solution rather than suggesting self-signed certs
- Tooltip added to hub "Presenter View" button flagging the client node URL for HTTPS + LAN bridge setups

### vMix Caller 1.3.0 — 2026-04-25

**vMix IP/port config from hub, dual bridge modes, correct setup guide**

- Hub operator page now has **vMix IP and Port fields** — entering them and clicking Save pushes the values to the client node automatically via the command channel (no need to visit the client machine). Client's current reported IP/port shown in the status bar after first contact.
- **Dual bridge modes** — `_proxyUrl()` auto-detects which to use:
  - `http://127.0.0.1:8080/...` → routes through the authenticated hub proxy (`/hub/vmixcaller/video/<path>`). Use when SRS runs on the hub server.
  - Any LAN/remote IP → browser accesses directly. Use when SRS runs on the same LAN as vMix (Option A — recommended). No hub-side port needed.
- **Setup guide** rewritten to show Option A (LAN bridge, vMix Caller mode) as the recommended approach, with correct vMix SRT settings (Type: Caller, Hostname: LAN IP, Stream ID: `#!::h=live/caller,m=publish`). Option B (bridge on hub server) described as an alternative.
- Client node status report now includes `vmix_ip` and `vmix_port` so hub operator can verify the config that was applied.
- Client node page updated to note that IP/port can be pushed remotely from the hub.

### vMix Caller 1.2.0 — 2026-04-25

**Presenter view and saved meetings**

- New **Presenter page** at `/hub/vmixcaller/presenter` — clean, bookmark-friendly page designed for the studio "email machine". No technical config visible; just the video feed and the tools a presenter needs
- **Saved meetings list** — operator adds named meetings (name, meeting ID, passcode, display name) on the hub page. Presenter sees each meeting as a one-click row; tapping Join fires `ZoomJoinMeeting` immediately and shows the video feed
- **In-call toolbar** appears automatically once joined: On Air badge, Mute, Camera, and Leave buttons. All join buttons disabled while a call is active to prevent double-joining
- **Manual join** section collapsed behind a toggle link on the presenter page — keeps the UI clean for typical use but accessible when needed
- Saved meetings also available from the hub operator page for back-of-house joins
- `saved_meetings` list persisted in `vmixcaller_config.json` alongside existing scalar settings
- New API endpoints: `GET /api/vmixcaller/meetings`, `POST /api/vmixcaller/meetings`, `DELETE /api/vmixcaller/meetings/<idx>`

### vMix Caller 1.1.0 — 2026-04-25

**Hub/client architecture — vMix lives at the site, hub is the control surface**

- Hub page now shows a **site selector** dropdown (populated from all approved connected sites) instead of a direct vMix IP field — the operator picks which site node is running alongside vMix
- Commands (join, leave, mute, camera, put-on-air) are **queued on the hub** via `_pending_cmd[site]` and collected by the site client on its next poll — works through NAT, no direct hub→site HTTP needed
- **Client background thread** starts automatically on any `client` or `both` mode node that has a hub URL. Polls `GET /api/vmixcaller/cmd` (X-Site header) every 3 s, executes the command against the local vMix API, and POSTs the result + latest participants immediately back to `POST /api/vmixcaller/report`
- **Periodic participant refresh** — client polls vMix XML every 12 s and reports participants to hub; hub browser polls `GET /api/vmixcaller/state` every 8 s to update the panel without the operator having to click Refresh
- **Client config page** at `/hub/vmixcaller` on client nodes — shows current hub URL and a form to set the local vMix IP/port with a Test Connection button
- Standalone mode still works as direct vMix control (original v1.0.0 behaviour, no site selector)
- vMix IP/port config removed from hub page — those fields now live on the client config page where they belong

### vMix Caller 1.0.0 — 2026-04-25

**New plugin — manage Zoom/Teams callers in vMix from the hub dashboard**

- Join / leave Zoom meetings via vMix's `ZoomJoinMeeting` / `ZoomLeaveMeeting` API
- Mute self, stop camera, mute all guests — with button-state toggle tracking
- Participants list pulled from the vMix XML status feed via a server-side Python proxy (no CORS)
- One-click **Put On Air** fires `ZoomSelectParticipantByName` to vMix instantly
- Manual `+ Add` button as fallback when the vMix XML feed isn't accessible
- Live connection dot — tests `http://vmix-ip:port/API/` on save and page load
- Optional caller video preview: configure a bridge URL (e.g. HLS from SRS Docker container); the `<video>` tag plays natively in Safari; an in-page setup guide covers the one-time Docker command for Ubuntu
- Config saved to `plugins/vmixcaller_config.json` (IP, port, input number, preview URL)
- Keyboard shortcuts: **M** = mute self, **C** = toggle camera (ignored when focus is in a text field)
- All vMix calls proxied through the SignalScope backend — no CORS issues regardless of where the hub is accessed from

---

### 3.5.170 — 2026-04-25

**Fix: hub level bars drop to zero shortly after page load, take a long time to recover**

Root cause: `_build_payload()` (used for the full ~10 s heartbeat) always sent
`level_dbfs = round(inp._last_level_dbfs, 1)` regardless of whether the monitoring loop
had processed any audio yet. `_last_level_dbfs` defaults to `-120.0`, so the first
heartbeat after a monitor restart overwrote the valid values that `_load_state()` had
just restored from `hub_state.json` with `-120.0` — causing all bars to collapse to zero
within ~10 s of page load, where they would stay until real audio data arrived (~20–60 s
or more later).

The 3.4.105 fix already applied the `_has_real_level` guard to `_live_loop` (the 5 Hz
live-push path), but the heartbeat payload was never updated.

Two-part fix:
- `_build_payload()`: `level_dbfs`, `peak_dbfs`, `level_dbfs_l`, `level_dbfs_r` are now
  sent as `None` when `inp._has_real_level` is `False` (monitoring loop hasn't processed
  audio yet). Mirrors the existing guard in `_live_loop`.
- `ingest()`: after storing the heartbeat payload, a merge step walks the new streams list
  and, for any stream where `level_dbfs` is `None`, restores the last-known value from
  `prev` (the previous heartbeat / hub_state.json). Mirrors the "never overwrite a valid
  numeric field with None" rule already enforced in `hub_live_push`.

Result: from the moment the hub page loads, bars show the last-known levels from
hub_state.json and update smoothly as soon as real audio data starts flowing — no more
zero-bar window after a monitor restart or server reboot.

---

### Brand Screen 1.3.11 — 2026-04-25

**Full-screen logo mode — static image display with no backgrounds or animations**

- New `full_screen_logo` boolean field on station config (default `false`)
- Checkbox in the station editor Animation section: "Full-screen logo mode — show logo only, no background effects, animations, clock, or now-playing"
- When enabled and a logo is uploaded, the screen displays only the uploaded logo centred on a solid brand-dark background — all backgrounds, particle canvas, orbit rings, pulse rings, reactive overlays, clock, On Air badge, now-playing, and message banner are suppressed
- Anti-burn-in pixel drift (px-drift keyframe) still applies to the logo layer
- Full-screen takeover still works on top (z-index 50 above the logo layer at z-index 25)
- SSE-driven reload still works (the `#screen` div remains in the DOM underneath)
- Useful for events or shows where a static brand image is required without any motion
- Particle canvas, level poll, NP poll, and clock are all skipped in JS when `_fsLogo` is true (saves CPU on Pi/Yodeck kiosk displays)

---

### Brand Screen 1.3.10 — 2026-04-25

**Station-level takeover — fires on all screens showing that station**

- New `POST /api/brandscreen/station/{station_id}/takeover` endpoint: sends a full-screen takeover to every studio currently displaying that station simultaneously. Useful for sending a brand-wide message (e.g. "OFF AIR") across all screens for a given station in one call
- New `DELETE /api/brandscreen/station/{station_id}/takeover` — clears the station-level takeover from all affected screens. Studios with a studio-level override in place are not touched
- New `GET /api/brandscreen/station/{station_id}/takeover` — query active state of the station-level takeover
- `GET /api/brandscreen/studio/{studio_id}/takeover` (used on page load) now also checks the station-level takeover as a fallback: if no studio-level takeover is active, a station-level one for the assigned station is returned instead — so the screen always restores the correct state after reload
- Studio-level takeovers always take priority over station-level ones on the same screen
- REST API tab in the admin page updated with examples for both target types

---

### Studio Board 3.14.16 — 2026-04-25

**Fix audio reactivity causing animation glitches and screen flashes**

- Root cause 1: `_meterRaf` (60 fps) was calling `document.querySelectorAll('[data-k="..."]')` on every frame for every tracked stream. That is a full DOM scan at 60 fps — it forces synchronous style recalculation and interrupts the GPU-composited CSS animations (waves, pulse, drift), causing visible stuttering and flashes
- Root cause 2: `.vf` had `transition:height .1s linear` and `.vp` had `transition:bottom .1s linear`. The JS RAF loop already does EMA attack/decay smoothing, so the CSS transition was doubling the smoothing delay and forcing an extra paint pass on every JS write
- Fix: element references are now cached in `_levEls` / `_peakEls` maps by `data-k`/`data-p` key when the DOM is built. The RAF loop does a direct O(1) array lookup per element — no DOM query on any animation frame
- Fix: CSS transitions removed from `.vf` and `.vp` — RAF smoothing alone is sufficient and avoids the redundant repaint cycle
- Cache is rebuilt (via `_cacheEls()`) whenever the DOM is regenerated (studio/chain assignment changes)

---

### Brand Screen 1.3.9 — 2026-04-25

**Mic live takeover suppression**

- Each Brand Screen studio can now be linked to a Studio Board studio via a new **"Mic Live — Link to Studio Board Studio"** dropdown in the studio edit form
- When the linked studio has a mic live, any full-screen takeover is silently **suppressed** — it does not appear on screen while a presenter mic is hot. The brand screen continues showing normally
- If a takeover arrives while the mic is live it is **held pending**; the moment the mic goes down the takeover shows immediately
- If a takeover is already visible when the mic goes live it is **hidden and parked** — restored automatically when the mic clears
- The background monitor thread (`bs-mic-monitor`) polls `studioboard_cfg.json` every 2 s and fires `mic_live` / `mic_down` SSE events to the screen browser instantly
- New `GET /api/brandscreen/studio/<id>/mic_state` endpoint so the correct state is restored on page load or reload (mic state is fetched before takeover state to ensure suppression applies correctly)
- No studioboard plugin dependency — reads the config file directly; if studioboard is not installed, mic suppression is simply inactive

---

### Zetta Integration 2.1.26 / Studio Board 3.14.15 — 2026-04-25

**Correct Zetta Chain Type handling (Stop / Segue / Auto Post / Link-Song)**

- Added `_parse_chain_type()` helper that normalises the Zetta "Chain Type" field from any representation (string or numeric) to a small integer: `0`=Segue, `1`=Stop, `2`=Auto Post, `3`=Link-Song
- Both SOAP parsers (raw XML and zeep) now search for `ChainType` first (correct Zetta field name, confirmed from Zetta documentation), then fall back to the previous candidates
- String values like `"Stop"`, `"Segue"`, `"Auto Post"`, `"AutoPost"`, `"Link-Song"`, `"LinkSong"` are all handled case-insensitively; unrecognised numeric values are passed through as-is
- Studio Board countdown icon updated: Stop → ⏹ amber; Segue / Auto Post / Link-Song → ⏭ green; field absent → falls back to station-mode indicator (3.14.13 behaviour)

---

### Zetta Integration 2.1.25 / Studio Board 3.14.14 — 2026-04-25

**Per-cart segue/stop type parsed and shown on presenter countdown**

- Both Zetta event parsers (raw XML path and zeep path) now attempt to read the per-cart segue type from the `GetStationFull` SOAP response. Field names tried in order: `SegueType`, `Segue`, `TransitionType`, `NextEventType` — whichever the installed Zetta version exposes. Value is `0` = chain/segue (auto-starts next cart), `1` = stop, `null` = field not present in this Zetta SOAP endpoint.
- `segue_type` is included in every parsed `now_playing` and queue-item dict and flows through to Studio Board via `/api/studioboard/data`.
- Studio Board countdown segue icon now uses `now_playing.segue_type` when available: ⏭ green for chain, ⏹ amber for stop. Falls back to station-mode-based display (added 3.14.13) when `segue_type` is `null` — so if your Zetta SOAP doesn't expose the field, behaviour is unchanged.
- **If the icon doesn't populate**: open Settings → Zetta → debug tab, call `GetStationFull` with your station ID, and look for the segue field name in the Event objects in the raw JSON. Report the field name and it can be added to the candidate list.

---

### Studio Board 3.14.13 — 2026-04-25

**Segue / chain indicator on presenter countdown**

- A mode icon now appears beside the big countdown timer, at near-matching size, showing whether the Zetta sequencer will chain automatically or stop for the presenter:
  - ⏭ green — **Automation** mode: sequencer will chain to the next item automatically
  - ⏹ amber — **Manual** mode: sequencer will stop after this track; presenter needs to take action
  - ⏯ blue — **Live Assist** mode: presenter-driven / mixed
  - Off Air / unknown: icon hidden
- Icon sourced from Zetta's `mode` field (same field that drives the existing mode badges); no extra API calls
- CSS `.cnt-seg` shares the `.cnt-row` flex container with the countdown number; inherits `transition:color` so mode changes animate smoothly

---

### Studio Board 3.14.12 — 2026-04-25

**Fix: Zetta countdown timer ~10 seconds behind**

- `remaining_seconds` is now freshened on the server at HTTP-response time (using `play_start_time`) rather than being left at the stale Zetta-poll value. Previously the countdown could be up to one Zetta poll interval behind before the first JS tick
- JS countdown now uses a purely client-side `_dataFetchTs` timestamp (recorded when `poll()` resolves) as the elapsed-time reference, eliminating any server ↔ client clock-skew error
- Both the 500 ms countdown interval and the immediate post-render paint now use `_dataFetchTs`; `zd.ts` is no longer used for elapsed calculation

---

### Studio Board 3.14.7 — 2026-04-21

**Cleared studio panel redesign — studio name and status at top, larger and more prominent**

- Studio name is now pinned to the top of every cleared-studio card as the first and largest element (`clamp(28px–46px)`, bold white) — previously it was centred in the middle at a small muted size
- **STUDIO FREE** badge moved under the studio name in the header, full-width with large text (`clamp(16px–24px)`) and a stronger green background — previously a tiny pill centred mid-card
- **VOICE TRACKING** badge redesigned: fixed wrong emoji (was 🎼, now 🎙), full card-width amber block with large text (`clamp(26px–42px)`), stronger glow animation — much harder to miss
- Two-section layout: fixed header (name + status) with a centred body below (clock / VT badge / automation label / mic button)

---

### SignalScope-3.5.169 — 2026-04-21

**Fix: Zetta ad-break keyword matching for beds with punctuated titles**

- Keywords like `"sport bed"` now use word-based matching instead of exact substring matching. `"sport bed"` now correctly matches `"Cool FM - Sport - Bed Only"`, `"Downtown - Agri News - Bed Only"`, `"Cool 22 - Business News Bed - 1 min bed"`, etc. — any title containing all the words in the phrase regardless of hyphens, dashes, or other punctuation between them
- Global defaults `"news bed"` and `"sport bed"` remain always active; previously neither matched typical station bed naming conventions
- **New: per-chain custom keywords** — chain settings now include an "Ad-break title keywords (Zetta)" textarea. Add one phrase per line for station-specific beds (e.g. `agri news bed`, `traffic bed`). Words are matched independently so formatting doesn't matter

**Fix: Spurious CHAIN_FAULT + CHAIN_RECOVERY at end of Zetta-confirmed ad breaks**

- When the Zetta spot latch was active (chain held in "pending"), the `_chain_fault_since` clock was set only once at the start of the silence. When the latch expired (30 s after Zetta's last confirmed spot), `elapsed` equalled the full ad-break duration — which always exceeded `min_fault_seconds` for any break > the configured window, firing CHAIN_FAULT immediately followed by CHAIN_RECOVERY as the break ended
- Fixed: `_chain_fault_since` is now updated on every cycle while the Zetta spot latch is active. When the latch expires, `elapsed` is ~10–30 s (one poll interval), so the normal confirmation window runs fresh from the end of the break rather than from when silence first began

---

### Brand Screen 1.3.7 — 2026-04-21

**Feature: Settings changes update TV displays instantly — no manual refresh needed**

- Saving any station setting (bg style, logo animation, toggles, colours, now-playing source, etc.) now fires a `settings_changed` SSE event to all studio screens showing that station
- Uploading or deleting a logo also triggers the same instant reload
- TV screen fades out and reloads in ~580 ms — same smooth transition as brand assignment changes
- Previously, changes required a manual page refresh on each Yodeck screen

**Fix: Yodeck/Raspberry Pi animation flashing — three root causes eliminated**

- **Orbit rings (worst offender)**: `box-shadow` on the glowing dot `::before` pseudo-element inside a rotating `will-change:transform` layer forces a full re-rasterise every frame — the GPU cannot composite it. Replaced with a `radial-gradient` dot baked into the layer texture (zero per-frame cost). The dot is wider to maintain the glow appearance.
- **Aurora preset**: `filter:hue-rotate()+brightness()` animated on a full-screen fixed element triggers a per-frame GPU filter pass on Pi. Replaced with `opacity` oscillation on `::before` — compositor-only, no repaint.
- **Grid preset**: `background-position` animation is not GPU-compositable — the browser repaints the grid plane every frame at 60 fps. Moved the scrolling grid pattern into a `::before` pseudo-element that scrolls via `transform:translateY` instead (composited by GPU, no repaint). Also removed `overflow:hidden` from `.bg-grid` for the same Pi stacking-context reason as beams/burst.

---

### Studio Board 3.14.6 — 2026-04-21

**Tweak: cleared studio card uses default blue; adds pulsing "STUDIO FREE" availability badge**

- Card background restored to the studio's own default blue (no longer inherits the last brand colour)
- Added a green pulsing "● STUDIO FREE" pill at the top of cleared panels — makes it immediately obvious the studio is available
- The STUDIO FREE badge hides when Voice Tracking is detected (replaced by the amber VOICE TRACKING badge)

---

### Studio Board 3.14.5 — 2026-04-21

**Feature: Cleared/in-automation studio panel redesign with Voice Tracking detection**

- Cleared studios now show a styled holding screen instead of the old "AVAILABLE" placeholder: studio name in muted uppercase, a large broadcast clock, and the station that was pushed to automation (with amber "IN AUTOMATION" sub-label)
- **Voice Tracking detection**: if the mic went live after the studio was cleared and within the last 5 minutes, the clock is replaced by a pulsing amber "🎼 VOICE TRACKING" badge — indicating the room is in use for VT even though no chain is assigned
- Mic button always visible on cleared studios — presenters recording VT can toggle it as normal
- Card background uses `auto_brand_color` (the last-assigned brand's colour) for visual continuity when cleared
- Removed orphaned `_FREE_MSG` fun-message array and old `.free-icon/.free-lbl/.free-msg` template

---

### Studio Board 3.14.4 — 2026-04-21

**Fix: logo corners no longer clipped — removed border-radius from .logo**

- `border-radius:16px` on `.logo` was clipping the image at all four corners, cutting off any logo design elements near the edges (e.g. Downtown bottom-right). Station logos are transparent PNGs designed to fill their canvas and should not have rounded corners applied.

---

### Studio Board 3.14.3 — 2026-04-21

**Feature: SSE instant update — TV display reacts immediately to config changes**

- All connected TV browsers now hold a persistent `EventSource` to `GET /api/studioboard/events`
- Server fires `config_changed` via SSE whenever brand assignment, mic live, message, brand colour/name, or any studio config changes
- TV JS calls `poll()` immediately on receipt — no waiting for the 1.5 s interval
- Mirrors BrandScreen SSE pattern; auto-reconnects on error after 5 s
- Triggers: `sb_studio_update`, `sb_studio_assign_brand`, `sb_mic_live`, `sb_brand_update`, `sb_brand_delete`, `sb_message_set`, `sb_message_clear`

---

### Studio Board 3.14.2 — 2026-04-21

**Fix: ON AIR badges now centred under the mic button**

- `[id^="badges"]` container was `display:block` with no explicit width, causing badge children to left-drift relative to the centred `.mic` pill above. Now a flex column with `width:100%;align-items:center` so badges sit centred on the same axis.

---

### Studio Board 3.14.1 — 2026-04-21

**Fix: brand assignment now exclusive — moving a brand clears it from its previous studio**

- `POST /api/studioboard/studio/<id>/brand` and the admin Save Studio form both now clear the assigned brand from any other studio that held it before setting it on the target. A brand can only be active on one studio at a time.

---

### Studio Board 3.14.0 — 2026-04-20

**Feature: Brands — station presets, new admin UI, brand assignment REST API**

Splits the flat per-studio config into two separate concepts:

- **Studios** — physical screens: name, assigned brand, message, show artwork
- **Brands** — station presets: colour, chains, inputs, Planet Radio feed,
  frequency, Zetta station, Follow Zetta assignment

**Seamless migration**: On first startup after upgrade, brand fields are
automatically lifted from each studio into a new Brand object and linked
back. No data is lost. The TV display is unaffected.

New admin tabs:
- **Studios** — assign a brand preset to each screen, send messages, manage artwork
- **Brands** — create/edit/delete presets with all station config fields
- **API** — reference docs for all REST APIs with live studio/brand IDs

New REST endpoint:
`POST /api/studioboard/studio/{studio_id}/brand?token=TOKEN`
Body: `{"brand_name": "Cool FM"}` or `{"brand_id": "abc123"}` or `{"brand_id": ""}` to unassign.
Instant display update — automation systems can switch brand in one call.

---

### Studio Board 3.13.6 — 2026-04-20

**Fix: countdown timer repositioned below presenter image/name**

The big Zetta countdown timer (`.cnt-wrap`) was rendered between the chain
badges and the presenter image. Moved it to after the show name (`.shw`) so
the order is: logo → station → studio → frequency → mic → badges → divider →
presenter image → show name → **countdown** → now-playing section. The timer is
now visually attached to the presenter/show block rather than floating between
metadata and artwork.

**Fix: stale presenter images on Yodeck kiosk**

Root cause: race condition between the server image-download cycle (every 60 s)
and the browser requesting a new cache-busted URL. When a show changed and
Planet Radio returned a new `episodeImageUrl`, the browser immediately computed
a new `?v=<hash>` (from the source URL) and fetched the server cache endpoint.
If the background poller hadn't yet downloaded the new image, the server
returned the OLD image under the NEW cache-bust URL. The browser (and Yodeck's
Chromium) then stored that wrong image for up to 5 minutes (`max-age=300`),
so the display showed the previous presenter even after the server caught up.

Fix: the `?v=` cache-bust parameter is now driven by `show_img_ts` — a
server-side Unix timestamp written to `_img_cache` each time a new presenter
image is successfully downloaded. `/api/studioboard/data` includes
`show_img_ts` per studio. The browser only requests a new image URL AFTER the
server confirms the new file is on disk, eliminating the race entirely.

---

### Brand Screen 1.3.6 — 2026-04-20

**Fix: admin page completely broken after 1.3.5 (JS syntax error)**

In Python triple-quoted strings `\"\"\"…\"\"\"`, the sequence `\'` produces a
plain `'` — the backslash is stripped. The hint text in `_studioForm` contained
`studio's`, which rendered as a literal apostrophe inside a single-quoted
JavaScript string, terminating the string early. The rest of the `<script>`
block failed to parse: no stations or studios rendered, no buttons responded.

Fix: replaced `\'` with the HTML entity `&#39;` so the apostrophe is safe
inside both the Python string and the JS string.

---

### Brand Screen 1.3.5 — 2026-04-20

**Feature: full-screen takeover REST API per studio**

External applications can now push a full-screen text overlay to any Brand
Screen studio via a REST API call, then clear it when done — without touching
the station assignment or reloading the display.

- `POST /api/brandscreen/studio/{studio_id}/takeover` with `{"title":"…","text":"…"}` — immediately overlays the display with large title (brand colour, `clamp(48px,9vw,160px)`) and body text (white, `clamp(22px,4vw,72px)`). Background uses the station's brand-derived palette so the colour scheme is preserved. Delivered to the browser instantly via SSE — no reload.
- `DELETE /api/brandscreen/studio/{studio_id}/takeover` — clears the overlay and returns to normal branding.
- `GET /api/brandscreen/studio/{studio_id}/takeover` — returns current takeover state (`{"active": true/false, "title", "text"}`). Used on page load to restore an active takeover after a browser reload.
- All three routes accept either a Bearer API key or an authenticated session.
- The active takeover is stored in memory (`_takeovers` dict); it survives SSE reconnects (page-load fetch restores it) but clears on plugin/server restart.
- Admin UI: each studio card now has **Title** and **Body text** fields plus **▶ Send Takeover** / **✕ Clear** buttons below the screen URL section. REST API tab documents the new endpoints.
- Screen template: `#takeover` overlay div, `_showTakeover(title,text)` and `_clearTakeover()` JS functions; SSE `onmessage` handles `takeover:` and `takeover_clear` message types.

---

### Brand Screen 1.3.4 — 2026-04-20

**Fix: part-of-screen black flash on Raspberry Pi / Yodeck players**

Diagnosed from video footage: the left-centre area of the screen flashed black
for one frame at irregular intervals, visible only on Yodeck (Pi) not desktop.
Root cause: GPU compositor texture eviction due to memory pressure.

On Raspberry Pi Chromium, every element with `filter:blur()` AND `will-change`
is promoted to its own GPU compositor layer. The blur kernel requires the GPU
texture to be larger than the element's DOM size (3× blur radius of padding on
all sides). When the Pi GPU memory budget is exhausted, Chromium evicts the
lowest-priority layer texture and renders it as solid black for that frame.

**Textures removed:**
- `#lev-bloom` `filter:blur(32px)` — this single element at 80vw width produced
  a `(1536+192)²≈11.9 MB` GPU texture. When evicted, a large dark circle
  appeared over the centre of the screen.
- `.beam` `filter:blur(22px)` — four beam elements × ~1.4 MB each = ~5.6 MB.
  The beam at `left:13%` was the first to be evicted, causing the dark patch
  seen in the left-centre of the screen in the video.
- `#lev-bloom-core` `filter:blur(4px)` — smaller contribution but freed.

**Compensations:**
- `#lev-bloom` gradient extended to 78% transparent (was 68%) for a softer
  natural fade without the blur.
- Beam width increased from `6vw` to `9vw` with `border-radius` rounding and
  a softer gradient to approximate the blurred spotlight look.
- `#lev-bloom-core` gradient stop pushed to 72% for smoother fade.

**`overflow:hidden` removed from `.bg-beams` and `.bg-burst`** — on Pi
Chromium, an `overflow:hidden` parent that contains `will-change:transform`
children creates a compositor clipping stacking context. When the clipping
layer fails to render (GPU OOM), the entire clipped region is filled with
black for one frame. The viewport already clips any beams or burst rays that
extend outside its bounds, so `overflow:hidden` was redundant.

---

### Brand Screen 1.3.3 — 2026-04-20

**Fix: animation glitching on Raspberry Pi / Yodeck players**

Several per-frame GPU operations were causing visible glitching on Pi-based
Chromium kiosk players (Yodeck) while appearing fine in desktop browsers:

- **Dynamic `drop-shadow()` removed from logo JS filter** — the logo was being
  given two `drop-shadow()` calls with radii up to 575 px on every frame. On Pi,
  `drop-shadow` requires rendering the element alpha to an off-screen buffer and
  applying a Gaussian blur — at 575 px radius this is catastrophic at 60 fps.
  The reactive glow is already provided by `#lev-bloom` / `#lev-bloom-core` which
  use `transform` + `opacity` (GPU-composited, zero repaint cost). Logo filter
  is now `brightness()` + `saturate()` only — both single-pass GPU operations.

- **Beams/haze/grid switched from `filter` mutation to `opacity` mutation** —
  `blur()` is fixed in CSS and the GPU caches the blurred texture. Changing only
  `opacity` reuses that texture (compositor pass only, no repaint). Previously
  `style.filter = 'blur(22px) brightness(X)'` / `'blur(88px) brightness(X)'`
  forced full repaint of each element every frame.

- **Background `hue-rotate()` removed** — applying `hue-rotate()` to a
  full-screen background element every frame performs a pixel-level colour
  transform on the entire viewport on each tick.

- **Three separate `requestAnimationFrame` loops merged into one** — `_spinLoop`,
  `_bounceLoop`, and `_levRaf` were three independent RAF registrations. Merged
  into a single `_raf()` loop that runs EMA smoothing, spin/bounce logic, and
  `_applyEffects()` in one callback.

- **CSS `transition` removed from `#vignette` and `#beat-flash`** — these
  elements are updated at 60 fps by the RAF loop, so the CSS transitions were
  redundant and created new transition instances on every frame.

---

### SignalScope-3.5.168 — 2026-04-20

**Fix: treat "news bed" / "sport bed" titles as spots for chain silence suppression**

News and sport beds played from a split/mixin point during a news bulletin are not
always tagged `asset_type=2` in Zetta (they may be music category or untagged).
This caused the chain to alert on the bed silence even though the mixin was
deliberately playing the bed.

Fix: `_zetta_spot_raw` now also fires when the Zetta `now_playing` title contains
`"news bed"` or `"sport bed"` (case-insensitive substring match). This feeds into
the same spot latch and post-spot grace machinery, so the chain behaves identically
to a normal ad break for the full 30 s latch + 90 s post-spot grace window.

---

### SignalScope-3.5.167 — 2026-04-20

**Fix: Zetta ad-break exit false silence alert (Zetta pre-roll)**

Chains using Zetta automation with local ad insertion (mixin architecture) could
incorrectly fire a silence alert immediately after an ad break ended. Root cause:
Zetta advances "now playing" to the next non-ad track before the last spot has
finished airing (pre-roll), so `asset_type` stops being 2 while ads are still
playing. With broadcast/PDM delays this pre-roll can exceed the 30 s spot latch +
20 s SOAP-lag grace, causing a false alert.

Fix: added a **post-spot grace window** (`_ZETTA_POST_SPOT_GRACE_S = 90 s`). When
`_chain_zetta_spot_latch_ts[cid]` shows a confirmed spot within the last 90 s, the
ad-break grace extends from 20 s to 90 s (mixin-healthy path). The original 20 s
SOAP-lag grace is still used when no recent spot is on record. Log messages now
indicate which grace mode fired ("post-spot/pre-roll" vs "SOAP lag") for easier
diagnosis.

---

### Brand Screen 1.3.2 / Producer View 1.4.3 / Listener 1.1.7 — 2026-04-20

**Feature: cross-navigation between Producer, Listener, and Brand Screen admin**

- **Producer View** header: new purple **📺 Brand Screen** button appears alongside "Listen Live" when the Brand Screen plugin is installed. Routes to `/hub/brandscreen`.
- **Listener** header: same purple **📺 Brand Screen** button alongside "Producer View".
- **Brand Screen admin**: header redesigned to match the Producer View / Listener style exactly — sticky gradient header, same font sizes, `hdr-powered` link, and back-nav pill. New **🎙 Producer** and **🎧 Listen** nav buttons appear conditionally when those plugins are installed.

**Feature: Brand Screen admin first-time onboarding panel**

A "Getting started" panel is shown automatically when no stations or studios have been configured yet. Numbered steps explain the concepts clearly:
1. Create a Station (brand configuration — logo, colours, animations)
2. Upload a logo and set brand colour (full background theme auto-derived)
3. Create a Studio (a physical display screen in your building)
4. Assign station to studio, copy the Screen URL, open full-screen on the display

Onboard action buttons ("Create First Station / Studio") switch to the relevant tab and open the new item's edit panel immediately. The panel disappears once any station or studio exists.

Tab labels updated with emoji and live count chips (e.g. "🖥 Studios  2") so the layout is immediately clear to new users.

---

### Brand Screen 1.3.1 — 2026-04-20

**Fix: audio-reactive animation jitter — 60 fps RAF smoothing**

Three root causes combined to produce visible jitter in the Brand Screen animations:

1. **150 ms poll drove all effects directly** — `_applyLevel(raw)` was called from `setInterval(..., 150)`, so every visual effect (logo scale, bloom, orbit rings, etc.) jumped to a new value 6–7 times per second instead of moving smoothly.

2. **CSS `transition` fought JS updates** — `#logo-img` had `transition:transform .09s ease-out,filter .09s ease-out`. With the JS overwriting the property every 150 ms, the browser restarted the CSS transition each poll tick, creating a 90 ms interpolation that was immediately interrupted. Removed; smoothing is now entirely JS/RAF.

3. **Pulse rings `animationDuration` changed every poll** — any change to `animationDuration` causes the browser to restart the CSS keyframe animation from 0%, producing a visible flash every 150 ms. Replaced with `pulse-wrap` opacity scaling — the animation always runs at its fixed 3.2 s cadence; audio level scales how visible the rings are.

Fix: decoupled data acquisition from rendering:
- `_pollLevel()` now only sets `_rawLev` (the target) every 150 ms — no visual changes.
- A new `(function _levRaf(){...})(requestAnimationFrame)` loop runs at 60 fps, advances `_lev` / `_levSnap` EMAs toward `_rawLev` each frame, and calls `_applyEffects()`.
- EMA coefficients converted from per-150ms to per-frame: `alpha_frame = 1 - (1-alpha_150)^(1/9)`. Ambient tracker: attack 0.45→0.066, decay 0.18→0.022. Beat tracker: attack 0.65→0.12, decay 0.38→0.055. Overall response feel is preserved.
- `_pulseWrap` DOM ref cached at startup (no per-frame querySelector).

---

### SignalScope-3.5.166 — 2026-04-20

**Fix: Hub Reports table — huge gap caused by long site/stream names**

Root cause: `.site-badge` had `white-space:nowrap` but no `max-width`, so a site name like `(Northern Ireland - Cool FM & National DAB)` forced the Site column to ~320 px. With `table-layout:auto` (the default), this content-driven expansion overrode the `<th style="width:110px">` hint, blowing out the table layout and creating a large blank gap.

Fixes:
- `table{table-layout:fixed}` — column widths are now set by `<th>` declarations, not by cell content.
- `.site-badge{display:block;overflow:hidden;text-overflow:ellipsis;max-width:100%}` — long site names truncate with `…` within the column width. Full name shown on hover via `title` attribute.
- Stream column cell: same `white-space:nowrap;overflow:hidden;text-overflow:ellipsis` + `title` tooltip.
- Column widths rebalanced: Site 110 → 160 px; Time 130 → 110 px; gives Detail column more working room.
- Detail cell: `word-break:break-word` so long fault messages (e.g. DLS text) wrap cleanly.

---

### SignalScope-3.5.165 — 2026-04-20

**Fix: Zetta ad-break grace period now only applies when mix-in is healthy**

The 20 s grace period introduced in 3.5.164 was applied to all `pending_adbreak` chains regardless of downstream state, adding an unwanted 20 s delay to genuine faults.

Fix: grace only fires when `not mixin_is_down and not any_post_mixin_fault` — i.e. there is still audio present after the mix-in point, which is the strong indicator that the pre-mixin silence is an ad break. When the mix-in feed is also silent or a post-mixin node is faulted, there can be no ad fill and the chain fires immediately as before. Because `effective_mixin_is_down` and `effective_post_mixin` would bypass the confirmation window on the very next line anyway, the two code paths are now consistent.

---

### SignalScope-3.5.164 — 2026-04-19

**Fix: false CHAIN_FAULT alerts at start of ad breaks (Zetta SOAP poll lag)**

Root cause: when silence was first detected at the start of an ad break, `_zetta_spot_raw` was `False` because the Zetta SOAP poller hadn't yet returned `asset_type==2` for the new spot (typically 1–3 poll cycles, ~3–10 s). In the "pending" evaluation path, the check `if _zetta_on and not _zetta_spot and pending_adbreak` immediately set `elapsed = min_fault_secs`, bypassing the confirmation window entirely and firing the alert within seconds. Zetta would then confirm the ad break a few seconds later — too late.

Fix: introduced `_chain_zetta_no_spot_since` dict (per-chain, per-HubServer). The "Zetta says not a spot → fire" logic now requires Zetta to have said "not a spot" for `_ZETTA_GRACE_S = 20` consecutive seconds before it forces the fault. This gives the SOAP poller enough time to confirm `asset_type==2` at the start of a break. If Zetta does confirm the break (spot latch fires at line ~16264), `_chain_zetta_no_spot_since` is reset immediately. The timer is also reset when the chain returns to "ok" so the next fault gets a clean window. For genuine faults (Zetta consistently says "not a spot" for 20+ seconds), the early-fire logic still works — just with a 20 s minimum delay rather than immediately.

**Feature: Zetta now-playing shown in chain fault history and Hub Reports**

Each chain fault log entry now records what was playing in Zetta at the moment the fault fired (`zetta_now_playing`). Stored in SQLite (new `chain_fault_log.zetta_now_playing` column with auto-migration), the in-memory fault log, and `alert_log.json`. Displayed:
- Broadcast Chains fault history: 🎵 "Title — Artist" below the fault-point cell (alongside existing AD BREAK / mode / machine badges). AD BREAK entries show the purple badge instead of a track name (already confirmed by `zetta_is_spot`).
- Hub Reports Detail column: same 🎵 subtitle under the fault message.

---

### SignalScope-3.5.163 — 2026-04-19

**Fix: Hub Reports — duplicate alert rows when client and hub both hold the same event**

When a fault occurs on a client node the client includes the event in its heartbeat `recent_alerts`. The same event is also uploaded to the hub (as a clip or via `_alert_log_append`) and written to `alert_log.json`. Previously `hub_reports()` added the hub-log copy first and then added the client-heartbeat copy again because the `seen_ids` dedup set was only populated after the client pass. Result: every faulted stream showed the same row twice — once with a direct clip URL (hub copy) and once without (client heartbeat copy).

Three-part fix:
1. **`_add_history` writes a `.meta` sidecar** alongside each silence/AI clip WAV on the client node, recording the `entry_id` generated when the alert-log event is created.
2. **Queue drain reads the `.meta` sidecar** so `_upload_clip_inner` receives the original `entry_id` instead of `""`. The hub then stores the clip against the existing event rather than creating a new UUID — both copies share the same ID.
3. **`hub_reports()` processes hub `alert_log.json` first** (Pass 1) and client `recent_alerts` second (Pass 2). Any event whose ID is already in `seen_ids` from Pass 1 is skipped in Pass 2. Hub events are preferred because they carry direct clip URLs; proxied client URLs are only used as fallback when the hub does not hold the clip.

---

### studioboard v3.13.5 — 2026-04-19

**Fix: waves invisible on 1080p TV — SVG height mismatch**

Root cause: `.col-wave svg` had no explicit height, so the browser sized each SVG by its viewBox aspect ratio (`width:200%` on a 1920px display → SVG width=3840px → height=3840×110/1440=293px). The container was only 130px tall at `bottom:0`, so the wave fill area (lower half of the viewBox, pixels 146–293px from SVG top) sat entirely below the container bottom edge and was clipped by the card's `overflow:hidden`. Only the transparent top of the SVG was visible — appearing as nothing or a 1–2px sliver.

Fix: added `height:100%` to `.col-wave svg` so the SVG always fills the container exactly. Container height raised from `130px` to `40vh` (432px on a 1080p display). Wave fill now occupies the bottom 216px of each card (~20% of card height) — clearly visible as a background effect behind presenter content.

---

### studioboard v3.13.4 — 2026-04-19

**Remove clock header bar — clock moves to top-left of first card**

`#sb-hdr` (42 px header stripe with clock and date) removed entirely, freeing the full viewport height for studio cards. Clock and date are now rendered as `.card-clock` — `position:absolute; top:14px; left:16px; z-index:3` inside card 0 only. Text uses `rgba(255,255,255,.9)` with a soft `text-shadow` for readability against the brand-coloured card background. Corp theme variant inverts to dark text. `_sbTick()` unchanged — targets the same `#sb-hdr-clock` / `#sb-hdr-date` IDs.

---

### studioboard v3.13.3 — 2026-04-19

**Rework: waves move to background layer, change colour per card**

- Removed the full-page `#page-waves` overlay (was at z-index:2 above all card content). Replaced with per-card `<div class="col-wave">` elements at z-index:0 inside each `.col` — behind `.mp`/`.rp` content (z-index:1), so presenter photos, text, and meters all appear in front of the wave.
- Each card's wave SVG is 200vw wide, offset left by the card's viewport x position (`left:-Xpx`) so all cards share the same wave coordinate space. The card's existing `overflow:hidden` clips the wave to that column. All cards use the same CSS `@keyframes pw-slide` animation with no delay — phase-synchronised → seamless continuous wave sweep across the full display.
- Each card injects its own brand colour (vivid, ~25-27% opacity) into its wave paths via `_posColWaves()`. The wave changes colour cleanly at each card boundary.
- Card background is now fully opaque (removed `.78/.76` alpha that existed to let the old global wave bleed through). `_posColWaves()` is called after DOM rebuild (`requestAnimationFrame`) and on `window.resize`.
- No `mix-blend-mode` — plain alpha compositing over the solid dark brand background gives a clear but subtle animated undulation.

---

### studioboard v3.13.2 — 2026-04-19

**Fix: waves invisible — wave fill was using dark brand shade instead of vivid brand colour**

Root cause: `_updatePageWaves()` updated wave path fills with `rgba(bg.rgb, opacity)` where `bg.rgb` is the *dark* derived shade (V≈0.42) of the brand colour — essentially the same dark hue as the card background. With `mix-blend-mode:screen`, a dark source on a dark destination produces minimal brightening (imperceptible glow). Fix: wave paths now use `rgba(RGB(color), opacity)` — the vivid brand colour itself (e.g. `#17a8ff` at full brightness and saturation). Screen-blending a bright, saturated colour over a dark card surface produces a clearly visible glow that sweeps across all studios as the wave animates.

---

### studioboard v3.13.1 — 2026-04-19

**Fix: waves invisible — move to z-index:2 with mix-blend-mode:screen**

Waves were at z-index:0 (behind `#sb` at z-index:1). With cards at 78% alpha, only 22% showed through, and the wave colour was nearly identical to the dark card background — imperceptible. Fix: `#page-waves` raised to z-index:2 (above cards) with `mix-blend-mode:screen`. Screen blending brightens whatever dark surface the wave passes over without hiding card content. Wave path fill opacity raised to 0.72/0.45/0.58 (was 0.52/0.30/0.40) so the screen blend has enough light intensity to register clearly on dark brand-coloured card backgrounds.

---

### studioboard v3.13.0 — 2026-04-19

**Feature: brandscreen-style card colours + single flowing waves background**

**Card backgrounds (Goal 1 — brandscreen colour approach)**

Previously cards used `rgba(brand, .18/.10)` tint over a near-black `rgba(7,14,38,.88)` base — effectively just dark navy with a faint hue hint. Now cards use `_deriveBg()`, a JS port of brandscreen's `_derive_brand_bg()` Python function. It derives a `dark` (V≈0.42) and `mid` (V≈0.58) shade in exactly the brand hue at boosted saturation — the same values brandscreen uses to fill the whole screen. Cards are now clearly and visibly the brand colour, not just barely-tinted black.

**Page-wide waves (Goal 2 — single effect flowing across all cards)**

Two fixed elements added before the header in the body:
- `#page-bg`: full-viewport fixed gradient (`mid → dark` in first studio's brand colour); updated by `_updatePageWaves()` on every `render()` call.
- `#page-waves`: fixed bottom-anchored wave SVGs (3 paths, same shapes as brandscreen Waves preset), animated with `pw-slide` (9 s / 13 s reverse). Wave fill colours are set from the first studio's brand colour RGB.

Cards have 78/76% alpha backgrounds (down from ~88% opaque) so the waves are visible flowing through all card backgrounds simultaneously — a single continuous animation spanning the full display width. Cards still clearly show their individual brand colours via the derived dark/mid shades, top border line (`--cc`), and top radial glow (`--cg`).

Bauer theme: `#page-bg` update skipped (body class controls the purple), waves still coloured from studio brand. Corp theme: both `#page-bg` and `#page-waves` hidden via CSS (`display:none`), no change to corp appearance.

---

### studioboard v3.12.1 — 2026-04-19

**Fix: cached show image URL missing kiosk token**

In kiosk/Yodeck mode with auth enabled, `showImg.src` was set to `/studioboard/cached_show_img/<rpuid>?v=<hash>` without the `?token=` parameter. The `@login_required` check on that route would redirect the browser to the login page, causing a broken image. Fixed by wrapping the URL with `tk()` — the same helper used by all `fetch()` calls in the TV template — which appends `&token=TOKEN` when a kiosk token is configured.

---

### studioboard v3.12.0 — 2026-04-19

**Feature: server-side presenter/show image cache**

Previously the display set `<img src="https://cdn.planetradio.co.uk/...">` directly. Every time the page loaded (or the display panel rendered) the browser fetched the image fresh from the Planet Radio CDN — slow on first load, and blank if the API was temporarily unavailable.

- New background daemon thread (`SB-ImgCache`) starts at plugin load. After a 5 s startup delay it fetches `listenapi.planetradio.co.uk/api9.2/stations_nowplaying/GB`, extracts the `episodeImageUrl` for every configured studio's `np_rpuid`, downloads the image, and saves it to `plugins/studioboard_img_cache/show_{rpuid}.jpg`. Repeats every 60 s.
- On server restart, existing cached images are pre-loaded from disk into memory — no re-download needed until the source URL changes.
- New route `GET /studioboard/cached_show_img/<rpuid>` serves the cached image with `Cache-Control: public, max-age=300`. Returns 404 only before the first poll completes (~5 s after startup).
- `updateCol` JS: `showImg.src` now points to `/studioboard/cached_show_img/<rpuid>?v=<hash>` instead of the raw CDN URL. The `v` parameter is a djb2 hash of the current source URL — when the show changes (new `episodeImageUrl`), the hash changes, the browser fetches fresh from the local endpoint, which already has the new image downloaded. `onerror` hides the element if the cache is not yet populated.
- `showImgPoll()` continues running unchanged — it keeps `NP[r].show_image` populated (the raw URL is still needed for hash computation).

---

### SignalScope-3.5.162 — 2026-04-19

**Fix: Zetta ad-break suppression — spot latch prevents false CHAIN_FAULT/RECOVERY during ad breaks**

Between consecutive spots in an ad break, Zetta's SOAP sequencer briefly reports `now_playing = None` for ~1 s while it queues the next spot. With the 10 s chain monitor loop and 3 s Zetta poll, this window was wide enough to drop `_zetta_spot` to False, bypassing the suppression guard at line 16202 and firing a CHAIN_FAULT → CHAIN_RECOVERY pair on what was a perfectly normal commercial break.

Fix: added a 30-second spot latch (`_chain_zetta_spot_latch_ts` dict on `HubServer`). When `asset_type == 2` (ASSET_SPOT) is confirmed, the latch timestamp is updated. `_zetta_spot` stays True for the full 30 s following the last confirmed spot, covering inter-ad gaps and SOAP jitter. The latch is cleared immediately when the chain returns to `curr == "ok"` so any genuine fault after the break ends fires normally.

No change to the `asset_type == 2` detection rule — never reads the backend-computed `is_spot` boolean.

---

### brandscreen v1.3.0 — 2026-04-19

**Feature: four new radio-station background styles + three new logo animations**

**New backgrounds** (selectable in Settings → Stations → Background):

- **Beams** — four sweeping concert spotlight columns anchored at the floor, oscillating slowly in angle and opacity. Each beam brightens with audio level at 150 ms. Floor glow at the base for warmth.
- **Grid** — synthwave/broadcast perspective grid receding into the horizon. Grid lines scroll toward the viewer at a steady pace; a horizon glow sits at the vanishing point. Grid line brightness reacts to audio.
- **Burst** — large rotating starburst/sunray conic-gradient pattern. Rotates slowly (70 s/revolution). Ray opacity pulses with audio level.
- **Haze** — three large blurred atmospheric blobs drifting independently around the screen. Blob brightness breathes with audio level. Evokes broadcast studio haze/concert fog.

All new backgrounds inherit the existing audio reactive overlays (bloom, bg-pulse, beat-flash, vignette, hue-rotate) on top of their own per-preset reactivity.

**New logo animations** (selectable in Settings → Stations → Logo Animation):

- **Spin** — continuous rotation driven by a JS RAF loop. Speed scales with audio: 0.25 deg/frame at silence → 3.5 deg/frame at full level. Does not use `animationDuration` so no glitch on update.
- **Glitch** — digital broadcast interference. CSS `steps(1)` keyframe animation with translate + skewX jumps. Runs at a natural broadcast-artifact rate. The existing JS glow/brightness effects still apply on top (filter is not set by the keyframes). Body class `la-glitch` activates it.
- **Bounce** — elastic physics bounce. A JS RAF loop applies gravity (3.8 px/frame) and kicks the logo upward when the fast level snap rises sharply. Bounce strength scales with level. Damped rebound (0.38 coefficient) for a natural feel.

**Also fixed**: `la-float` and `la-glow` CSS rules were defined but never activated — the body class was never set. The `<body>` tag now renders `class="la-{{logo_anim}}"` from the Jinja2 template so all logo-animation CSS rules correctly apply.

**`_applyLevel` transform gating**: The logo scale transform is now skipped for `spin`, `bounce`, and `glitch` modes, preventing the per-mode RAF loops and CSS keyframes from fighting the 150 ms JS update.

### brandscreen v1.2.12 — 2026-04-19

**Fix: orbit rings glitching — caused by animationDuration changes at 150 ms**

Changing `animationDuration` on a running CSS animation every 150 ms causes the browser to restart the animation from the current point with the new duration, producing a visible jump/glitch on every update. Removed all `animationDuration` modifications from `_applyLevel()`. Orbit rings now react to audio via opacity (0.18→0.78 / 0.06→0.44) and a gentle `scale()` on the orbit container (0.94→1.06) — both properties animate smoothly without triggering animation restarts.

### brandscreen v1.2.11 — 2026-04-19

**Fix: audio reactivity never worked — wrong response key format**

Root cause: `/api/hub/live_levels` returns a nested structure:
`{ "site_name": [ {name, level_dbfs, ...}, ... ], ... }`

The JS was doing `d[_levelKey]` where `_levelKey = "site|stream"` — that key never exists in the response. `e` was always `undefined`, the level check always fell through to `_applyLevel(0)`, and `_levSnap` was permanently zero regardless of what audio was playing.

Fix: split `_levelKey` on `|` to get site and stream name separately, then look up `d[site]` to get the array and find the matching stream by name:
```javascript
var _lkSite = _levelKey.slice(0, _levelKey.indexOf('|'));
var _lkName = _levelKey.slice(_levelKey.indexOf('|') + 1);
var siteArr = d[_lkSite];
// find entry where entry.name === _lkName
```

Also: `_applyLevel(0)` is now called once at page load so all effects initialise to their silence-state values immediately.

Reverted the auto-loudest-stream picker added in v1.2.10 — that was an unrequested behaviour change.

### brandscreen v1.2.10 — 2026-04-19

**Fix: audio reactivity never fired — all effects stayed at zero**

Root cause: the level poll was gated on `if(_levelKey && _hasStation)`. `_levelKey` is only set if an audio stream has been explicitly selected in the station's settings (Brand Screen → edit station → Audio Reactive dropdown). If nothing was selected — which is the default — `_levelKey` is an empty string, the condition is false, `_pollLevel()` never runs, and `_applyLevel()` is never called. Every visual effect (bloom, vignette, beat flash, orbit opacity, logo scale, brightness, etc.) requires `_applyLevel()` to set its value, so they all stayed permanently at zero.

Additionally, even when `_applyLevel(0)` would have been called, it was never called once at startup — so the silence-state values (vignette at 0.18, orbit rings at baseline opacity, etc.) were never initialised.

Fixes:
- Poll always runs whenever a station is displayed (`if(_hasStation)`) — no `_levelKey` requirement.
- When `_levelKey` is empty, the poll **auto-selects the loudest stream** on the hub (`Object.keys(d).forEach` scan for highest `level_dbfs`). Reactivity works out of the box with zero configuration. Configuring a specific stream in settings still works and takes priority.
- `_applyLevel(0)` is called once immediately on page load so all effects initialise to their correct silence-state values before the first poll returns.
- `fetch` error handling improved: checks `r.ok` and throws on non-2xx so auth failures (401, 403) are caught by the `.catch` path rather than silently parsed as empty JSON.

### brandscreen v1.2.9 — 2026-04-19

**Fix: majority of screen still black despite brand colour set**

Root cause — three compounding issues:
1. **Palette V values still too low**: the luma-target approach produced colours like `#4d0202` for red's `bg_dark` — clearly dark on a calibrated monitor but reading as near-black on a studio TV at distance.
2. **Body background was `bg_deep`**: body fills screen corners and any area outside the radial gradient ellipse. With near-black `bg_deep`, every corner was black regardless of the gradient.
3. **Gradient ellipses too small**: `circle at 50% 42%` with stop at `bg_deep` at 100% — the majority of the screen area sits in the gradient's outer half where it blends toward bg_deep (near-black).
4. **Vignette at 0.76 opacity × rgba(0,0,0,.9)** was effectively a 68% black overlay on all edges — crushing any brand colour that had managed to survive the gradient.

Fixes:
- **New palette formula**: fixed base V values (dp=0.28, dk=0.42, md=0.58) that are bold enough to register on any TV. A hue-luminance cap (`_cap = max(0.22, 1 - _hl × 1.2)`) scales down V only for naturally bright hues (yellow/lime) that would otherwise blow out — dark hues (red, blue) get the full base V. Results: red `bg_dark` ≈ `#6b1212`, `bg_mid` ≈ `#941313`; blue `bg_dark` ≈ `#12126b`, `bg_mid` ≈ `#131394`; green `bg_dark` ≈ `#125e12`, `bg_mid` ≈ `#138412`.
- **Body background → `bg_dark`**: corners now show brand colour instead of near-black.
- **Gradient ellipses → 110% × 105%**: oversized so `bg_mid` and `bg_dark` fill the entire screen. Third dark stop removed — gradient runs from bg_mid at center to bg_dark at edges (body = bg_dark fills the rest).
- **Aurora blobs raised** to 0.70 / 0.60 / 0.35 opacity (was 0.58 / 0.48 / 0.28) to match brighter base.
- **Vignette**: base opacity 0.76 → 0.18, rgba alpha 0.9 → 0.65. Effective edge darkening: 0.12 (was 0.68). JS range: 0.18→0.01 (was 0.76→0.02). Remains as a subtle depth accent, no longer a black mask.

### brandscreen v1.2.8 — 2026-04-19

**Fix: brand colours still too dark on TV screens (red/blue near-black)**
**Add: dramatically improved audio-reactive effects**

**Background palette fix:**
The v1.2.6 perceptual V compensation used target luminances that were still too low for TV display (dp=0.022, md=0.092). Red and blue hues clamped near black at those values. New targets: dp=0.040, dk=0.090, md=0.180, with per-level V caps (dp=0.45, dk=0.52, md=0.62) so bright hues stay suitably dark while red and blue reach a genuinely visible brand colour (red bg_mid ≈ `#990f0f`, blue bg_mid ≈ `#0a0a9e`).

**New audio-reactive effects (all presets):**
- **Vignette breathing** — dark frame around screen edges lifts as audio energy rises. At silence: logo pops from darkness. At peak: edges open completely — the whole screen floods with light. Most visible effect from 3+ metres.
- **Beat flash** — brand-colour radial wash fires above 0.45 threshold (~0.22 opacity max). Visible colour pulse on loud transients.
- **Inner bloom core** — new `#lev-bloom-core` (16vw, blur:4px): sharp punchy "lamp" at logo centre that flashes with beats, separate from the large soft outer bloom.
- **Bigger logo scale** — 1.0→1.22 (was 1.09). ~2× more visible at studio viewing distance.
- **Logo brightness+saturation pump** — `brightness(1.0→1.60) saturate(1.0→2.10)`. Logo visibly glows and saturates on beats.
- **Orbit ring opacity** — rings fade 0.18→0.92 / 0.08→0.60 with level (quiet: ghostly, loud: blazing) in addition to speed change.
- **Background hue-shift** — all presets except aurora hue-rotate 0→22° with energy. Subtle colour breathing on every beat.
- **Wave amplitude** — waves preset scaleY(1→1.6), anchored at bottom. Waves visibly rise with the music.
- **Particle speed** — raised to 12× at peak (was 8×).
- **NP title glow** — now-playing text gets brand-colour halo pulsing with beats.

### SignalScope-3.5.161 — 2026-04-19

**Fix: sites lose approval state on restart (intermittent)**

Root cause: `_save_snapshot()` runs in a daemon thread. Python kills all daemon threads immediately when the process exits (Ctrl+C, SIGTERM, systemd restart). If a 10-second heartbeat snapshot write happened to be in flight at the moment of shutdown, `hub_state.json` was left truncated. On the next startup `json.load()` threw `JSONDecodeError`; the except silently caught it; `_sites` stayed empty; every previously-approved site had to be re-approved. Only happened intermittently because it required the restart to land during the brief write window.

Fixes:
- `_save_snapshot()` now uses an **atomic write**: JSON is written to `hub_state.json.tmp` first, then `os.replace()` swaps it in. `os.replace()` is atomic on POSIX — the file is always either the previous complete version or the new complete version, never a partial write.
- Before replacing, the current good file is promoted to `hub_state.json.bak` as a fallback.
- `_load_state()` now tries the `.bak` file if the primary is absent or fails to parse, logs a warning, and continues. Previously a single corrupt file silently wiped all site state.

### brandscreen v1.2.7 — 2026-04-19

**Fix: Yodeck still required multiple reloads — JS API calls missing token**

Root cause: `session.modified = False` prevents Flask persisting the session cookie, making the token in the URL the sole auth mechanism. However, the three JS sub-requests the screen page makes (`/api/hub/live_levels`, `/api/brandscreen/data/…`, SSE `/api/brandscreen/events/studio/…`) were constructed as plain URLs with no `?token=` parameter. Without the session cookie and without the token in the URL, these calls all received 401/redirect responses — the screen loaded blank and required a refresh until the browser happened to cache a valid session.

Fix (mirrors wallboard's `wb_token` pattern exactly):
- `_screen_params()` now accepts and returns `kiosk_token` — the raw `?token=` value from the request URL.
- Both screen routes (`/brandscreen/studio/<id>` and `/brandscreen/<id>`) pass `kiosk_token=token` into the template context.
- Template embeds `var _kioskToken = '{{kiosk_token|e}}';` and a `_tk(url)` helper that appends `?token=…` to any URL when `_kioskToken` is set.
- All three JS API calls now use `_tk(url)`: live levels poll, now-playing poll, and SSE EventSource.
- `_bs_token_before` (added v1.2.5) validates any request carrying `?token=` and sets `session["logged_in"]` in-memory for that request, so these sub-requests are now authenticated on every call without needing a cookie.

### brandscreen v1.2.6 — 2026-04-19

**Fix: red (and warm-colour) brand screens appeared nearly black**

Root cause: `_brand_palette()` used fixed V values (0.06 / 0.13 / 0.22) for all hues. At these values, red puts virtually all brightness into the R channel with near-zero G and B, giving bg_mid = `#380303` — indistinguishable from black. Blue at the same V produces a visible dark navy because the G channel carries significant luminance. The perceived darkness of a hue at a given V depends on its relative luminance weight (red ≈ 0.299, green ≈ 0.587, blue ≈ 0.114).

Fix: `_brand_palette()` now computes the hue's relative luminance (`_hl = 0.299·R + 0.587·G + 0.114·B` at full saturation/value) and uses perceptually-normalised V values (`target_luma / _hl`, clamped 0.05–0.35). All hues now achieve approximately the same perceived background darkness:
- Red `#ff0000` bg_mid: was `#380303` (≈ black) → now `#510404` (clearly dark crimson)
- Blue `#17a8ff` bg_mid: minimal change, stays dark navy
- Green bg_mid: stays dark forest green

### brandscreen v1.2.5 — 2026-04-19

**Fix: Yodeck required multiple refreshes before page loaded**

Root cause: `session.modified = False` was missing. On first load, `_bs_token_before` set `session["logged_in"] = True` and Flask wrote a `Set-Cookie` header. Yodeck's browser didn't reliably send that cookie on subsequent requests (JS fetch calls), causing auth to fail until the cookie happened to be present. The page worked "eventually" after several refreshes.

Fixes:
- Added `_kiosk_response()` helper (mirrors studioboard_tv exactly): removes all 5 security headers, sets `Access-Control-Allow-Origin: *` and `Cache-Control: no-cache, no-store, must-revalidate`, and calls `session.modified = False` to suppress `Set-Cookie`. The token in the URL is now the sole auth mechanism — no session cookie needed.
- `_bs_token_before` now fires for any request carrying a `?token=` parameter (not just `/brandscreen/` prefixes), so JS fetch calls that forward the token are also authenticated.
- Both screen routes (`/brandscreen/studio/<id>` and `/brandscreen/<id>`) use `_kiosk_response()`.

### brandscreen v1.2.4 — 2026-04-19

**Yodeck / kiosk browser compatibility**

- Added `_bs_kiosk_headers` after_request hook: strips `X-Frame-Options`, `Content-Security-Policy`, `X-Content-Type-Options`, `Referrer-Policy`, and `Strict-Transport-Security` for all `/brandscreen/` and `/api/brandscreen/` paths, and adds `Access-Control-Allow-Origin: *`. This is the same mechanism used by the Studio Board plugin and is required for Yodeck's Chromium browser to load and display the page.
- Updated `_bs_token_before` to set `g._bs_kiosk = True` and populate the full session (`login_ts`, `username`, `role`, `_csrf`) on token validation — matching Studio Board's pattern.
- Screen routes (`/brandscreen/studio/<id>` and `/brandscreen/<id>`) now do their own auth check instead of using `@login_required`, so a valid `?token=` in the URL is sufficient without a pre-existing session cookie. SSE endpoint likewise no longer requires a session decorator.
- Both screen routes call `make_response()` and explicitly remove security headers on the response object as a belt-and-braces measure alongside the after_request handler.

### brandscreen v1.2.3 — 2026-04-19

**Dramatically more visible audio reactivity**

- Two-tracker EMA system: `_lev` (slow, 0.45 attack / 0.18 decay) holds the energy floor for smooth background effects; `_levSnap` (fast, 0.65 attack / 0.38 decay) snaps to beats and dips quickly for beat-responsive elements.
- **Bloom**: brighter core gradient (100% → 70% → 0%), larger (80vw), `filter:blur(32px)` — now looks like a real light source behind the logo. Scale range 0.12→3.2 (was 0→1.6), opacity 0.28→0.95.
- **Logo scale pulse**: logo scale 1.0→1.09 on every beat — most perceptible effect on a large image. CSS `transition` keeps it smooth.
- **Logo glow**: double drop-shadow, 30px→230px range (was 40px→120px).
- **Orbit speed**: 15s→1.2s range (was 10s→3s) — orbit rings visibly sprint on peaks.
- **Pulse ring tempo**: 3.5s→0.25s (was 3.2s→0.6s).
- **Full-screen background wash** (`#bg-pulse`): brand-coloured radial overlay, opacity 0→0.85, makes the whole screen brighten with the audio.
- **Particle speed**: multiplier 1→9 (was 1→3.5).

### brandscreen v1.2.2 — 2026-04-19

**Fix: Zetta station dropdown always empty**

- Zetta stations were built server-side from `_zetta_live_station_data()`, which only returns stations with an active live poller response. If Zetta hasn't polled yet at page-load time the list was empty.
- Switched to the same approach as studioboard: fetch `/api/zetta/status_full` from the browser on page load, parse `instances[].stations` to build the list, then render. Stations now appear as long as the Zetta plugin is installed and configured, regardless of poll timing.

### brandscreen v1.2.1 — 2026-04-19

**Fix: stream list always empty in audio reactivity dropdown**

- `_get_streams()` was reading `hub_server._sites["inputs"]` — wrong key. The heartbeat data structure uses `"streams"` not `"inputs"`, and each stream dict uses `"name"` not `"stream"`. Switched to `hub_server.get_sites()` (same method studioboard uses) which returns the correctly shaped list. Connected streams now appear in the audio input dropdown in the station admin panel.

### brandscreen v1.2.0 — 2026-04-19

**Brand-hued backgrounds, oversized logo, and audio-level reactivity**

- **Brand colour prominence**: background colours now derived from the station's brand hue using HSV interpolation (`colorsys`). `bg_deep` (V=0.06), `bg_dark` (V=0.13), and `bg_mid` (V=0.22) are computed at the brand hue/saturation — a blue station gives deep navy backgrounds, red gives deep crimson, etc. Aurora blobs, wave fills, and particle colours all inherit the same hue so the entire screen reads as "in brand".
- **Large logo**: logo now fills most of the screen — `width:68vw; max-width:1100px; max-height:52vh`. Orbit rings scaled to `84vw×34vw` and `63vw×26vw` in viewport units so they correctly surround the logo at any screen size. Pulse rings scaled to `48vw`.
- **Audio level reactivity**: assign any SignalScope-monitored audio stream to a station. Screen polls `/api/hub/live_levels` at 150 ms and drives: orbit ring spin speed, pulse ring tempo, logo drop-shadow intensity, particle speed, and a new centre bloom element (`#lev-bloom`) that pulses with the audio level. EMA smoothing (fast attack α=0.45, slow decay α=0.12) gives smooth motion without jitter.
- Level key stored in station config as `level_key` (`"site|stream"` format). Dropdown in admin panel lists all approved sites' active streams.

### brandscreen v1.1.0 — 2026-04-19

**Studios + REST API: instant SSE-driven station assignment changes**

- Added **Studios** — physical display screens, each assigned a Station (brand config). Studio screen URL is `/brandscreen/studio/<id>?token=...`. Direct station URLs (`/brandscreen/<id>`) still work.
- **Instant updates via SSE**: browsers watching a studio screen subscribe to `GET /api/brandscreen/events/studio/<id>`. When the station assignment changes (via UI or REST API), the server fires an SSE event and the browser fades out and reloads with the new station config — no manual refresh needed.
- **REST API** for automation: `PUT /api/brandscreen/studio/<id>/station` with `Authorization: Bearer <api_key>` body `{"station_id": "..."}`. Instant propagation to all connected displays. Documented in the new **REST API** tab of the admin panel.
- Admin panel redesigned into three tabs: **Studios**, **Stations**, **REST API**.
- REST API tab shows the API key, example cURL commands, and a reference table of all studio/station IDs.
- API key auto-generated on first use, shown and regeneratable in admin panel.
- Studio deletion gracefully unassigns from all studios; affected displays update via SSE.

### brandscreen v1.0.0 — 2026-04-19

**New plugin: animated full-screen studio branding display**

- Per-station configuration panel at `/hub/brandscreen` — add/edit/delete stations, upload logos, set colours, choose animations, configure now-playing source.
- **Background styles**: `particles` (floating particle canvas in brand colour), `aurora` (animated radial gradient), `waves` (SVG wave shapes), `minimal` (clean dark gradient).
- **Logo animations**: `orbit` (two counter-rotating elliptical rings with glowing dots), `pulse` (expanding concentric rings), `glow` (pulsing drop-shadow), `float` (gentle vertical drift), `static`.
- **Broadcast clock**: large thin-weight HH:MM:SS with full date, top-right.
- **On Air badge**: red pulsing dot + "ON AIR" text, shown automatically when Zetta reports a non-spot track playing.
- **Now Playing lower third**: source can be Zetta (select station from dropdown), any JSON API URL (with dot-notation field mapping), or manual text. "AD BREAK" badge shown on spot blocks (asset_type == 2).
- **Message-from-hub**: type a message in the admin panel and it appears as a flashing amber banner on the screen. Clear to dismiss.
- **Token-based kiosk auth**: each station gets a unique token URL (`/brandscreen/<id>?token=xxx`) for unauthenticated display use. Token regeneration in admin panel.
- **Anti-burn-in**: `step-end` 90 s pixel drift animation on the screen wrapper.
- Config stored in `plugins/brandscreen_cfg.json`; logos in `plugins/brandscreen_logos/`.

---

### studioboard v3.11.0 / wallboard v3.15.0 — 2026-04-19

**studioboard v3.11.0: themes, clock, big countdown, message-from-hub, anti-burn-in**

- Themeable TV display: `?theme=dark` (default), `?theme=bauer` (Bauer purple brand), `?theme=corp` (clean light-mode). Theme persisted in `sessionStorage` so the URL param only needs to be set once. Dark theme is now a proper dark blue gradient instead of the always-on Bauer purple.
- Clock header bar at the top of the TV display with live HH:MM:SS and full date.
- Big countdown timer for presenters: when Zetta is configured and a track is playing, a large font-size `MM:SS` countdown appears on screen. Turns amber at 30 s, red and pulsing at 15 s.
- Message-from-hub banner: admin can type a message in the studio admin page and send it to the TV display. The message appears as a flashing amber banner at the top of the screen. Clear button removes it.
- Anti-burn-in pixel drift: the `#sb` wrapper shifts 1 px in a 4-step pattern over 90 s using a `step-end` CSS animation.
- New API endpoints: `POST /api/studioboard/message/<studio_id>` and `DELETE /api/studioboard/message/<studio_id>`.

**wallboard v3.15.0: Zetta mode badge, chain reorder, anti-burn-in**

- Zetta mode badge on chain cards: when the Zetta-linked sequencer is in Manual or Voice Track mode (anything other than Auto), a small badge appears in the card header. Amber for Manual, purple for Voice Track.
- Chain card reorder: up/down arrow buttons in the settings drawer allow reordering chain cards on the wallboard. Order is persisted in `localStorage` via `chain_order` in `_cfg`.
- Anti-burn-in pixel drift: `#wb-hdr`, `#wb-content`, and `#wb-ticker` shift 1 px in a 4-step loop over 120 s.

---

### SignalScope-3.5.160 — 2026-04-19

**Fix: replace all `is_spot` boolean reads with `asset_type == 2` check**

The `is_spot` backend-computed boolean was being read directly from `_zetta_chain_state` in several places in signalscope.py. The project rule (mirrored from the JS `asset_type === 2` pattern) is that ad-break detection must always go directly to `asset_type` on the `now_playing` dict — never through a pre-computed boolean.

Fixed locations:
- Chain eval loop: `_zetta_spot` — drives definitive ad-break suppression and confirmation-window bypass logic
- `_fire_chain_fault` `_zetta_fire_stopped` guard — ensures "sequencer stopped" note only fires when chain is NOT in a spot break
- Fault log back-patch `zetta_is_spot` field (in-memory entry and DB via `fault_log_update_meta`)
- Alert log `_alert_log_append` dict `zetta_is_spot` field
- `chain_info.zetta_context["is_spot"]` for shared-fault aggregation carry-through
- Push notification ad-break context suffix
- Chain status API `_zetta_spot_api`

Python pattern: `int((_zcs.get("now_playing") or {}).get("asset_type") or 0) == 2` — safe when `now_playing` is `None` (idle sequencer).

---

### SignalScope-3.5.159 — 2026-04-19

**Fix: spurious chain fault clips at start of every Zetta ad break**

Root cause: when a chain has Zetta linked and `min_fault_secs > 0`, the `_zetta_on` flag (Zetta data present and fresh within 60 s) was included in the condition that bypasses the confirmation window entirely (`_fire_chain_fault` fires on the same evaluation cycle). This is correct for non-adbreak-candidate faults — Zetta confirming "this is not a spot break" means fire immediately. However, Zetta's `is_spot` flag can lag by up to one poll cycle (~3 s) after an ad break starts. During that window: codecs are already silent, `_zetta_on=True`, `_zetta_spot=False` (stale) → confirmation window bypassed → `_fire_chain_fault` called → clips saved → 3 s later Zetta updates and says "ad break active" — too late.

Fix: `_zetta_on` only bypasses the confirmation window for non-adbreak-candidate faults. For adbreak-candidate chains (pre-mixin silence where an ad break is possible), the normal confirmation window runs regardless of Zetta state. The existing `_zetta_on and not _zetta_spot` early-fire path inside the pending block (which fires within ~3–6 s for genuine faults once Zetta confirms the break ended and silence persists) is unaffected.

Net effect: for Zetta-linked chains with `min_fault_secs` set, ad-break-start false positives are fully eliminated. For real faults on adbreak-candidate chains, alerting is delayed by at most one Zetta poll cycle (~3–6 s) beyond the configured `min_fault_secs`.

---

### SignalScope-3.5.158 + morning_report v1.2.4 — 2026-04-18

**Broadcast Chains fault log — Zetta badges**
- Each fault log row now shows the Zetta automation context at fault time: mode name (Auto/Manual/Off Air), machine name, and a purple "AD BREAK" badge if the fault occurred during a spot break.
- Data comes from the new `zetta_mode`, `zetta_is_spot`, `zetta_computer` columns added to `chain_fault_log`. DB migration runs automatically on first start.
- Engineers can instantly see whether a fault was a genuine signal outage or an automation/scheduling issue.

**Push notifications — Zetta context**
- Push notification bodies for chain faults now include Zetta automation context when available: mode name (e.g. "Mode: Manual"), machine name, and "During ad break" suffix.
- Both the direct fault path and the shared-fault aggregation path are enriched.
- Context is appended in brackets after the fault message, keeping the notification concise (180-char limit enforced).

**Morning Report — Automation Health section (morning_report v1.2.4)**
- New "Automation Health" section appears in the morning report when Zetta data is present.
- Counts and lists all Zetta events from yesterday: failovers (machine changeovers), mode changes (Auto → Manual etc.), and GAP warnings.
- Shows count of chain faults that occurred during ad breaks (expected, not genuine loss) and faults that occurred in Manual mode (may be intentional off-air).
- Helps broadcast engineers triage overnight automation issues without digging through raw alert logs.

---

## livewire v1.1.1 — 2026-04-18

### Fixed — accordion state and node sort order

- Accordion open/close state now persists across data refreshes (auto-expand only fires the first time a node is seen)
- Node groups sorted by IP address (numeric) instead of display name

---

## livewire v1.1.0 — 2026-04-18

### Changed — accordion node display, sortable columns, search bar, stale alerts

- Sources are now grouped by node (name + IP) with collapsible accordion cards; first node expanded by default
- Sortable columns: Stream ID (numeric), Last Seen, and Status — click column header to toggle asc/desc
- Search bar filters across node name, IP, source name, stream ID, and multicast address simultaneously
- Hub overview no longer shows "+ Input" buttons (inputs should be added from the client node's page)
- Alert configuration section added to both hub and client pages: master "Alert on stale source" toggle plus per-channel checkboxes (Email, Webhook, Push notification) that use the existing Settings notification configuration
- Stale alerts fire through `_alert_log_append` (visible in Hub Reports) and selected notification channels; alerts de-duplicate per stale episode and reset on source recovery

---

## zetta v2.1.24 — 2026-04-19

### Added — `/zetta` short URL redirects to `/hub/zetta`

Nav link now uses `/zetta` — cleaner URL on both hub and client nodes. `/hub/zetta` continues to work as before so any existing bookmarks are unaffected.

---

## zetta v2.1.23 — 2026-04-19

### Fixed — Zetta nav item now visible on client nodes

Removed `hub_only: True` from `SIGNALSCOPE_PLUGIN`. The Zetta page (`/hub/zetta`) works correctly on client nodes — the flag was preventing the nav item from appearing, forcing users to type the URL manually.

---

## livewire v1.0.4 — 2026-04-18

### Fixed — correct binary TLV parser; passive-only (no TCP polling)

Replaced the text key=value parser with a correct binary TLV implementation matching the actual Axia LWAP protocol (reference: github.com/nick-prater/read_lw_sources). Packets are 16-byte header + sequence of `[4-byte opcode][1-byte type][N-byte value]` phrases. Source data (PSID channel number, PSNM name, FSID multicast address) is extracted passively from **Type 1** (ADVT=0x01) full advertisement packets. **Type 2** (ADVT=0x02) summary packets are heartbeat-only and update node last-seen time without changing the source table. Node display name comes from the ATRN field; node IP from INIP. No TCP connections are made. The debug endpoint now also reports `pkt_type1` and `last_adv_type` to confirm type 1 packets are being received.

---

## livewire v1.0.3 — 2026-04-18

### Fixed — LWAP packet parser rewritten for real protocol format

The previous parser expected a verb-based format (`SRC <id> <name>`) that does not match real Axia Livewire Advertisement Protocol packets. Real LWAP packets are NUL-byte or newline-separated `key=value` pairs: `ch=<channel>`, `srcn=<name>`, `src=<multicast_addr>`, `rate=`, `fmt=`, `type=`. Every real packet was silently rejected, producing an empty source table. The parser now correctly reads these fields. The multicast address is taken directly from the `src=` field rather than being derived from the channel number.

---

### SignalScope-3.5.157 + zetta v2.1.22 + logger v1.6.3 — 2026-04-18

**Fault attribution — Zetta automation state stamped on every chain fault event**
- `CHAIN_FAULT` alert log entries now include `zetta_mode` (Auto/Manual/Off Air etc.), `zetta_is_spot` (was it during an ad break?), and `zetta_computer` (which Zetta machine was running). All empty/false when Zetta is not configured for the chain.
- Makes it immediately clear in the fault history and Reports whether a silence was a genuine signal loss or an automation issue (wrong mode, nothing playing).

**SLA / on-air % adjustment — scheduled off-air excluded**
- When Zetta reports a chain's sequencer as intentionally stopped (OFF_AIR mode, no active track, not a spot break), chain faults during that period no longer count as downtime in the SLA on-air % metric.
- Mirrors the existing ad break overshoot exclusion — overnight shutdowns, daypart endings, and planned maintenance that Zetta knows about are excluded automatically.

**Zetta automation health alerts (zetta v2.1.22)**
- `ZETTA_MODE_CHANGE` — fires when a chain's Zetta sequencer mode changes (e.g. Auto → Manual). Appears in the hub alert log and Reports.
- `ZETTA_FAILOVER` — fires when the Zetta computer name changes for a chain (primary → backup machine failover).
- `ZETTA_GAP_LOW` — fires when GAP drops below 15 s, warning of impending dead air risk if the next break runs late.
- State-change comparison happens inside `_rebuild_chain_zetta_state()` on every Zetta poll cycle (~3 s). First-seen chains are skipped (no false alerts on startup).

**Logger Zetta now-playing (logger v1.6.3)**
- Each recording stream can now have a Zetta Chain assigned as its now-playing source (Settings → Logger → stream card → "Zetta Now-Playing Source" dropdown).
- When assigned and Zetta data is fresh, Zetta track metadata is used instead of a Planet Radio / custom URL fetch — more accurate (directly from automation) with no polling delay.
- Ad breaks (`asset_type 2`) are automatically skipped — compliance log shows music tracks only, not spot blocks.
- Priority order: Zetta (if configured and fresh) → Planet Radio/custom URL → DLS/RDS fallback.

---

### wallboard v3.14.7 + zetta v2.1.21 — 2026-04-18

**Full Zetta sequencer on wallboard chain cards**
- Wallboard chain cards now show the full Zetta sequencer view (same as Studio Board) instead of a single line of text. Displays: artwork placeholder, artist, title, AD badge, progress bar with live countdown, ETM (back-on-air time during ad breaks), queue of upcoming items, and Zetta computer/machine name.
- Progress bar and countdown timer update at ~5 fps via a dedicated `requestAnimationFrame` loop using `data-zet-pf`/`data-zet-tm` attributes — no ID conflicts between multiple chain cards.
- When sequencer is idle (no now_playing), shows the current mode name (e.g. "Auto", "Manual") dimmed in place of artist/title.
- zetta.py `_chain_zetta_state` now includes `queue`, `remaining_seconds`, `duration_seconds`, `etm`, `gap`, `computer_name`, and `station_name` — previously only `now_playing`, `mode`, and `ts` were stored for chains.

---

### wallboard v3.14.6 — 2026-04-18

**Fix Zetta ad detection and empty now-playing on wallboard**
- AD BREAK detection now uses `asset_type === 2` (Zetta's native ASSET_SPOT integer) directly in JS, same approach as Studio Board. Removes the backend-computed `is_spot` flag that was causing false positives.
- Fixed empty now-playing when Zetta has a track playing: the `now_playing` dict sent to the browser now includes `asset_type`, and the JS condition correctly renders track info when `asset_type !== 2`.
- Fixed Planet Radio fallback: previously the fallback only ran when the chain had no Zetta entry at all. Now it also runs when a chain has Zetta data but no active now-playing (e.g. sequencer idle/stopped), so the card is never blank.

---

### zetta v2.1.20 — 2026-04-18

**Fix broken nav header layout**
- `_PAGE_CSS` was missing the `header{display:flex;align-items:center;gap:12px}` rule. Without it the `<header>` element rendered by `topnav()` defaulted to `display:block`, causing the logo, version badge, and nav items to stack vertically into three separate rows. Added the required header flex rule, matching every other SignalScope plugin page.

---

### wallboard v3.14.5 — 2026-04-18

**Zetta now-playing and AD BREAK badge on chain cards**
- Wallboard chain cards now show live Zetta data automatically — no configuration required. The wallboard reads `monitor._zetta_chain_state` (the same chain-keyed dict used for chain fault suppression) and maps it to the correct card by `chain_id`.
- When a chain has an active Zetta station and a track is playing, the track title and artist are shown in the now-playing row of the card (primary source, replaces Planet Radio where both are present).
- When `AssetType == 2` (ASSET_SPOT) — commercial break — the card shows an amber pulsing **AD BREAK** badge instead of a track name.
- Falls back to the existing Planet Radio / custom now-playing source if no Zetta data is available for a chain.
- Data is refreshed at the same cadence as the chain poll (every 10 s).

---

## livewire v1.0.2 — 2026-04-18

### Fixed — hub mode no longer joins Livewire multicast

Pure hub nodes (`mode = hub`) now skip the LWAP listener entirely. The hub is display-only — it receives source tables pushed by client nodes every 30 s and has no reason to join `239.192.255.3:4001`. The hub Livewire page now explains this and points users to configure the audio interface on each client node. `both` mode (hub + local monitoring) still listens as before.

---

## livewire v1.0.1 — 2026-04-18

### Fixed — LWAP multicast join on wrong NIC

The Livewire plugin now reads the audio interface IP directly from **Settings → Hub & Network → Audio interface IP** (the system-wide `network.audio_interface_ip` setting) instead of maintaining its own separate field in plugin config. Previously the plugin defaulted to `0.0.0.0`, which caused `IP_ADD_MEMBERSHIP` to join the `239.192.255.3` LWAP multicast group on the default-route NIC rather than the Livewire audio NIC — resulting in no LWAP packets being received even when Livewire RTP streams were flowing normally. The interface is now shown read-only in the plugin config page with a link to Settings.

---

## zetta v2.1.19 — 2026-04-18

**is_spot uses asset type only — category string matching removed**
- The category fallback introduced in v2.1.18 still risked false positives (e.g. a non-spot item whose category happened to contain a configured spot keyword). `is_spot` is now set solely from `AssetType == 2` (Zetta's own `ASSET_SPOT` integer code) in both the raw-XML and zeep parsers. This is the same signal the Studio Board already uses in its AD badge logic. Category string matching is gone entirely.

---

## SignalScope-3.5.156 — 2026-04-18

### Added — Livewire plugin v1.0.0 + source picker in Add/Edit Input form

New **Livewire** plugin (`plugins/livewire.py`) for Axia Livewire source discovery:
- Passively listens on `239.192.255.3:4001` (LWAP) for Axia Livewire source advertisements on all node types (hub, client, standalone).
- Client nodes push their source table to the hub every 30 s via signed `POST /api/livewire/report`.
- Hub stores per-site source tables in `plugins/livewire_data.json` and serves `/hub/livewire` — per-site cards with online/stale pill badges and a full Node · Stream ID · Friendly Name · Multicast · Last Seen · Status table.
- **Create Input** buttons add Livewire RTP inputs directly from any row (hub routes via `add_input` command; client/standalone calls `/inputs/add_dab_bulk`). Uses the stream ID as `device_index` — SignalScope's `_parse_device()` converts it to `239.192.x.y:5004`.
- Configurable audio interface IP and source stale timeout per node.
- No third-party packages required.

**Settings → Inputs → Add/Edit Input** — when the Livewire plugin is loaded, the "Livewire / RTP / HTTP" source type now shows a **"Select from Livewire discovery"** dropdown above the manual Stream ID field:
- Sources grouped by node in `<optgroup>` labels; stale sources shown in amber with ⚠.
- Selecting a source fills Stream ID, pre-fills Name (if empty), and ticks stereo on.
- The picker is server-side gated (`{% if livewire_available %}`) — it is never rendered if the plugin is not loaded.

---

### zetta v2.1.18 — 2026-04-18

**Faster polling — default interval reduced from 10 s to 3 s**
- The Zetta SOAP poller previously defaulted to 10-second intervals. Combined with the 10-second chain evaluation cycle, worst-case latency from an ad break starting to the chain receiving the suppression signal was ~20 s — long enough for a false fault alert to fire if the confirmation delay was short.
- Default poll interval is now **3 s**. Worst-case is now ~13 s and average is under 10 s.
- Minimum configurable interval lowered from 5 s to 3 s (UI input now enforces `min=3`).
- **Existing installs**: if `poll_interval` is stored as 10 in your config, lower it to 3 in **Settings → Zetta → Edit instance** and save.

**is_spot now uses Zetta asset type as primary check**
- Previously spot detection relied entirely on category string matching (`spot_categories` list). If a track's category field was blank or used unexpected naming, `is_spot` was always `False` — the chain never received the ad-break suppression signal even during a real commercial break.
- Both parsers (`_parse_station_full` raw XML and `_parse_station_full_zeep`) now set `is_spot = True` whenever `AssetType == 2` (Zetta's own `ASSET_SPOT` integer code), regardless of category. Category matching is still applied as a secondary belt-and-braces check.
- This means ad-break chain suppression works correctly even with an empty or unconfigured `spot_categories` list, and is immune to category naming variations across Zetta installations.

---

### studioboard v3.10.5 / zetta v2.1.17 — 2026-04-18

**Larger idle/automation text for TV readability**
- Idle message text increased from 24px → 36px
- Zetta "no track" message increased from 14px → 28px — readable across the room

**Show name / presenter image restored on Zetta studios**
- `showimg` and `shw` elements were missing from the Zetta layout path in `buildCol`, so show names and presenter images never appeared on studios with a Zetta station assigned. Both elements are now always rendered and `updateCol` populates them as before.

**Follow Zetta Assignment — new per-studio toggle**
In Studio Board settings, each studio now has a "Follow Zetta Assignment" section:
- **Toggle**: "Auto-assign chain and level meter based on which station Zetta is running on this computer"
- **Computer name field**: enter the Zetta sequencer computer name (e.g. `BEL-STUDIO1`) — this is the `computer_name` / `ProcessingComputerName` reported by Zetta in its station metadata
- When enabled: the Broadcast Chain and level meter for that studio update automatically whenever Zetta assigns a station to that computer. The manual Chain and Input selections are ignored while active.
- When the computer name is not found in any live Zetta station, the studio shows as free (no chain)
- A small `↻ ZETTA AUTO` badge appears on the TV display when auto-follow is active
- Manual mode (toggle off) works exactly as before — no behaviour change

Level meter auto-assignment uses the **last node** in the matched chain's signal path (the final receive point).

`zetta.py` exposes `monitor._zetta_station_chain_map()` — a callable returning `{iid:sid → chain_id}` from current Zetta config, used by the studioboard to resolve computer_name → chain without importing the Zetta module.

---

### zetta v2.1.16 — 2026-04-18

**Fixed — ETM "Back on air" time shown in UTC instead of London time**

The `TargetGapTimeUtc` field from Zetta is UTC. Both parsers (`_parse_station_full` and `_parse_station_full_zeep`) were formatting it directly with `strftime`, displaying UTC time on the studioboard. Added `_utc_to_london()` (BST/GMT aware, no external dependencies) and `_fmt_london_etm()` helper. Both ETM paths now convert UTC → Europe/London before display. Uses `zoneinfo.ZoneInfo` when available (Python 3.9+), falls back to built-in BST rule calculation.

### studioboard v3.10.4 — 2026-04-18

**Improved — Witty messages rotate every 9 seconds; show in Zetta panel when no track playing**

- Expanded IDLE messages array with DJ-on-mic quips, Make Me A Winner competition lines, and general witty station messages
- Messages now rotate every 9 seconds (previously picked once at random and stuck forever)
- Zetta panel: when no track is in PLAYING state (DJ live on mic, between items), shows rotating witty message instead of the raw Zetta mode name ("Auto"/"automation")

---

## SignalScope-3.5.155 — 2026-04-18

### Fixed — Studioboard AD badge on every track including music (studioboard v3.10.3)

All tracks — music and spots alike — were showing the amber "AD" badge on the studioboard TV page.

**Root cause**: Spot detection was using `is_spot` from the Zetta parsers, which is derived from category name string matching. The matching code had a bug where an empty category string (`""`) always evaluated as a match (`"" in "SPOT"` is `True` in Python), flagging every track with no Zetta category as a spot. Most music tracks have no category in Zetta, so all were flagged.

**Fix**: The studioboard now uses `asset_type === 2` (Zetta's own `ASSET_SPOT` type code) directly from the parsed event data, instead of the derived `is_spot` flag. This is the raw Zetta classification — a song is always `asset_type=1`, a spot is always `asset_type=2`. No string matching, no false positives. Applied to both the now-playing row and the upcoming queue items.

---

## SignalScope-3.5.154 — 2026-04-18

### Fixed — Zetta `is_spot` false positive on music tracks with no category (zetta v2.1.15)

All music tracks with no Zetta category were incorrectly flagged as spots (`is_spot=True`), causing the studioboard to show amber "AD" badges on every song.

**Root cause**: `is_spot` detection used `raw_cat in sc` as part of the bidirectional substring check. In Python, `"" in "SPOT"` evaluates to `True` — so any track where Zetta returns an empty category string was always matched as a spot regardless of content.

**Fix**: Both parsers (`_parse_station_full` and `_parse_station_full_zeep`) now require `raw_cat` to be non-empty before attempting the match: `is_spot = (bool(raw_cat) and any(sc in raw_cat for sc in sc_upper)) if sc_upper else False`. Empty category → `is_spot=False`. Tracks must have a non-empty category that actually contains one of the configured spot-category keywords to be flagged.

---

## SignalScope-3.5.153 — 2026-04-18

### Fixed — Studio Board shows AD BREAK instead of now-playing (studioboard v3.10.2)

The studioboard replaced the entire now-playing display with a full-screen AD BREAK banner when `is_spot=True`. The Zetta plugin never does this — it always shows what's actually playing and colour-codes the row differently for spots.

**Fix**: studioboard now mirrors the Zetta plugin exactly. Always renders `now_playing` title/artist regardless of spot status. When the playing item is a spot: amber left-border on the now-playing row, small "AD" badge, ETM shown inline as "Back on air HH:MM:SS", amber progress bar. Music tracks show normally. Queue always visible below.

---

## SignalScope-3.5.152 — 2026-04-18

### Fixed — Studio Board Zetta data stale / stuck on previous state (studioboard v3.10.1, zetta v2.1.14)

The Studio Board TV page was showing Zetta state from one or more poll cycles ago — e.g. showing AD BREAK when the Zetta plugin showed a song actively playing. The Zetta plugin's sequencer view reads data directly from the live poller state (same source as `/api/zetta/status_full`) and is always current. The studioboard was reading from `_station_zetta_state`, a snapshot dict updated only when `_rebuild_chain_zetta_state()` ran (end of each poll cycle). Between polls, the snapshot was stale.

**Fix**: `zetta.py` now exposes `monitor._zetta_live_station_data` — a callable that reads directly from `_pollers[iid].get_state()` / `_remote_state` at call time, exactly like `status_full` does. The studioboard data endpoint calls this once per request (before the studios loop) and uses the resulting fresh dict instead of `_station_zetta_state`. The stale snapshot path is no longer used for the studioboard.

**Rule**: The studioboard `/api/studioboard/data` endpoint MUST read Zetta state via `monitor._zetta_live_station_data()` — never from `monitor._zetta_station_state` (snapshot). The snapshot is still updated by `_rebuild_chain_zetta_state()` for the chain fault suppression logic in signalscope.py but must not be used for display.

---

## SignalScope-3.5.151 — 2026-04-18

### Fixed — Broadcast Chains shows heuristic countdown during Zetta-confirmed ad break (3.5.151)

When a chain linked to a Zetta station entered a silent-pending state during an ad break, the chain header showed "AD BREAK — 306s" and "↳ Likely ad break — 306s remaining before fault alert" — a heuristic countdown guessing when the break might end. But Zetta is definitive: `is_spot=True` means we already *know* it's an ad break and exactly when it ends.

**Fix**: `api_chains_status()` now reads `monitor._zetta_chain_state` for each chain. When Zetta data is fresh (<60s) and `is_spot=True`, `zetta_suppressed=True` is added to the response and `adbreak_remaining` is set to `null`. The chain page JS reads `chain.zetta_suppressed`:
- Badge: "AD BREAK" (no countdown suffix)
- Footer: "↳ Zetta confirms ad break — fault suppressed"

The heuristic countdown message only appears when Zetta is not linked or Zetta data is stale/absent.

---

## SignalScope-3.5.150 — 2026-04-18

### Fixed — Zetta data frozen on Studio Board TV page (studioboard v3.10.0, zetta v2.1.13)

The Studio Board TV page called `/api/zetta/status_full` directly via `pollZetta()` to retrieve Zetta sequencer state. That endpoint uses `@login_required`, which is incompatible with the studioboard kiosk URL-token auth — the kiosk prefix only covers `/api/studioboard/…`, not `/api/zetta/…`. In kiosk mode (TV screens with no browser session), the Zetta fetch silently failed after the first page load and `_ZD` was never updated again, leaving the Zetta panel permanently frozen.

**Fix**: Zetta station state is now bundled directly into `/api/studioboard/data` (which is in the kiosk prefix):

- `zetta.py`: `_rebuild_chain_zetta_state()` now builds a second dict, `_station_zetta_state` (keyed `"iid:sid"`), containing the full station state for every polled Zetta station. Exposed on monitor via `monitor._zetta_station_state`.
- `signalscope.py`: `MonitorManager.__init__` declares `self._zetta_station_state: dict = {}` so the attribute always exists before the Zetta plugin loads.
- `studioboard.py`: Data endpoint reads `monitor._zetta_station_state` and includes `"zetta": <state>` per studio. TV JS reads `s.zetta` directly from the poll response — `pollZetta()` and the `_ZD`/`_ZT` globals are removed entirely. The 500 ms progress interpolator uses `zd.ts` (Unix timestamp set by the Zetta poller in Python) to extrapolate elapsed time accurately between polls.

**Rule**: Never call `/api/zetta/…` from the Studio Board TV page. Zetta data must be bundled into `/api/studioboard/data` — the only endpoint in the kiosk-auth prefix that the TV page can reach without a browser session.

---

## SignalScope-3.5.149 — 2026-04-18

### Fixed — rtl_tcp crashes every ~2 minutes on Raspberry Pi 5 (3.5.149)

On Raspberry Pi 5, the RP1 USB controller's runtime power management suspends the RTL-SDR dongle mid-session, causing rtl_tcp to exit with "Signal caught, exiting!" approximately every 2 minutes. When rtl_tcp dies, welle-cli loses its TCP connection and exits, tearing down all DAB monitoring streams until the session restarts.

Three changes to reduce impact and recovery time:

**1. rtl_tcp mid-session watchdog thread.** A new `RtlTcpWatchdog-{channel}` thread waits on `rtl_tcp_proc.wait()`. The instant rtl_tcp exits (and `stop_evt` is not set), the watchdog marks `session.failed = True` and fires `stop_evt` — immediately signalling all 8 consumer audio loops to break out and restart. Previously, consumers waited for `_poll_mux` (1 s cadence) to notice welle-cli had exited, adding up to several seconds of lag before recovery began.

**2. "rtl-tcp connection closed" added to welle-cli fatal markers.** When rtl_tcp dies, welle-cli prints "Error: RTL-TCP connection closed" to stderr. This is now treated as a fatal marker by `_read_stderr` — it sets `session.failed = True` and `session.stop_evt` immediately, providing a second fast recovery path alongside the watchdog.

**3. `power/control` added to autosuspend disable.** The sysfs block that runs at session startup now also writes `"on"` to `power/control` (in addition to `autosuspend = -1` and `autosuspend_delay_ms = -1`). Writing `"on"` disables runtime PM at the kernel level and is the most reliable way to prevent autosuspend. The permanent fix remains the udev rule at Settings → Maintenance → USB Autosuspend Fix.

**Rule**: The `RtlTcpWatchdog` thread must check `session.stop_evt.is_set()` before marking the session failed — a normal shutdown terminates rtl_tcp intentionally and should not be propagated as a failure.
**Rule**: "rtl-tcp connection closed" is a fatal welle-cli marker. Never remove it from `fatal_markers` — without it, session failure detection after an rtl_tcp crash falls back to `_poll_mux`'s 1 s cycle.
**Rule**: If rtl_tcp keeps crashing despite these changes, the root cause is USB autosuspend that requires a udev rule to fix permanently. Direct users to Settings → Maintenance → USB Autosuspend Fix.

---

## Studio Board v3.8.0 — 2026-04-18

### Added — Zetta automation queue display (studioboard v3.8.0)

Each studio on the TV display can now show a live queue strip from the Zetta automation system, pulling data from the Zetta plugin already running on the hub.

**Admin page**: A new "Zetta Station (queue display)" dropdown appears per studio, populated from all Zetta instances and stations. Select the station that corresponds to the studio's on-air chain and save.

**TV display**: When a Zetta station is linked, a queue panel is pinned at the bottom of the studio's column:
- **Normal play**: now-playing title with a smooth real-time progress bar (updated every 500 ms between 5 s Zetta polls)
- **Ad break**: amber "⏸ AD BREAK" banner with the ETM (Estimated Time of Music) from Zetta
- **Queue rows**: up to 3 upcoming items showing title and duration, with "NEXT" label on the first. Ad/spot items shown in amber italic.

Progress bar stays smooth between polls by tracking elapsed time since the last fetch and counting down `remaining_seconds` client-side.

**Rule**: `zetta_station_key` is stored as `"instanceId:stationId"` (colon-separated). The TV JS polls `/api/zetta/status_full` every 5 s and builds `_ZD[key]` from it. Never change the key format without updating both the admin select-builder and the TV JS lookup.

---

## SignalScope-3.5.148 — 2026-04-18

### Added — One-time migration of existing level_drift thresholds to new defaults (3.5.148)

On first boot after upgrade, `load_config()` checks every input for the old default values (`level_drift_db == 8.0` and/or `level_drift_min_duration == 60.0`) and updates them to the new defaults (12.0 dB / 180.0 s). The updated config is saved to disk immediately. Inputs where the user had already set custom values (anything other than exactly 8.0 / 60.0) are left unchanged. On every subsequent boot the values are already 12.0/180.0 so the check is a silent no-op.

**Rule**: Do not remove the migration block from `load_config()` — it is safe to leave indefinitely as a no-op once migrated. The exact-equality checks (`== 8.0`, `== 60.0`) ensure only the old defaults are touched.

---

## SignalScope-3.5.147 — 2026-04-18

### Fixed — LEVEL_DRIFT fires repeatedly every ~2 min for as long as drift persists (3.5.147)

Two bugs caused excessive LEVEL_DRIFT noise:

**1. No re-baselining after alert fires.** After LEVEL_DRIFT fires, `_ld_drift_secs` was reset to 0 but the fast and slow EMAs were left diverged. Because the stream had simply settled at a new loudness level, the two EMAs stayed apart and `_ld_drift_secs` quickly accumulated again, firing a second alert ~2 minutes later, then a third, and so on indefinitely until the stream level happened to return to the pre-drift average. This produced streams of identical LEVEL_DRIFT alerts in Reports.

Fix: after LEVEL_DRIFT fires, `cfg._ld_ema_slow = cfg._ld_ema_fast` — the new level is adopted as the new baseline. A follow-up alert only fires if the level drifts *again* from this new reference point. One alert per actual drift event, not one per minute.

**2. Defaults too sensitive for broadcast programming.** Default threshold 8 dB fired on normal inter-song loudness variation. Default min_duration 60 s meant a single loud commercial block was enough to fire.

New defaults: `level_drift_db = 12.0` (was 8.0), `level_drift_min_duration = 180.0 s` (was 60.0 s). Existing per-stream configs are unchanged — only new inputs start with the higher defaults.

**Rule**: After `LEVEL_DRIFT` fires, always re-baseline `cfg._ld_ema_slow = cfg._ld_ema_fast`. Never leave the EMAs diverged after an alert — the new level IS the new normal.

---

## SignalScope-3.5.146 — 2026-04-18

### Fixed — Hub Reports shows CHAIN_FAULT for LEVEL_DRIFT and other non-chain events (3.5.146)

`hub_clip_upload` derived the hub alert type from the clip label using a fragile substring chain that fell through to a hardcoded `"CHAIN_FAULT"` default for anything unrecognised. The label `"level_drift"` matched none of the conditions (`"silence"`, `"clip"`, `"hiss"`, `"rtp_loss"`, `"lufs_*"`, `"ai_"`, `"compare"`, `"glitch"`) so every LEVEL_DRIFT clip arriving at the hub was logged as CHAIN_FAULT. The same bug affected `mains_hum`, `dc_offset`, `phase_reversal`, `overmod`, `mono_on_stereo`, `stereo_imbalance`, `over_compression`, `tone_detect`, `hf_loss`, and `dead_channel`.

Fix: replaced the substring chain with a complete exact-match lookup dict (`_CLIP_LABEL_MAP`) covering all labels used in `_save_alert_wav` calls across the codebase. `ai_*` labels are caught by a `startswith("ai_")` check. `"chain"` in label maps to `CHAIN_FAULT`. Any remaining unknown label derives its type directly from the label string (uppercased) rather than defaulting to CHAIN_FAULT.

**Rule**: Any new `_save_alert_wav(cfg, label, ...)` call site MUST add the label to `_CLIP_LABEL_MAP` in `hub_clip_upload`. Never rely on the fallback for production alert types — the fallback exists only for future unknown labels to produce a meaningful type rather than silently misclassifying as CHAIN_FAULT.

---

## SignalScope-3.5.145 — 2026-04-18

### Fixed — Alert Timing fields mislabelled; "Min silence before alert" was ad break window (3.5.145)

`min_fault_seconds` is the **ad break tolerance window** — how long a pre-mix-in node can be silent before it's treated as a real fault rather than an ad break. `fault_holdoff_seconds` is the true **universal hold-off** before any CHAIN_FAULT fires (all fault types). The pinned Alert Timing panel had these backwards: the "Min silence before alert" label was bound to `min_fault_seconds` (the ad break window), so users setting a 330 s ad break tolerance saw it incorrectly described as their alert delay.

Fix:
- "Min silence before alert (s)" → now bound to `fault_holdoff_seconds` (the true universal alert delay, was buried in Advanced Settings)
- "Max ad break (s)" → now bound to `min_fault_seconds` (ad break tolerance, only active when a mix-in node is set; hint clarifies this)
- `fault_holdoff_seconds` removed from Advanced Settings (now in the pinned panel)

**Rule**: In the chain builder Alert Timing panel, `builder_fault_holdoff` maps to `fault_holdoff_seconds` (universal alert delay) and `builder_min_fault` maps to `min_fault_seconds` (ad break window). Never swap these.

---

## SignalScope-3.5.144 — 2026-04-18

### Fixed — Alert Timing panel invisible when chain drawer has many nodes (3.5.144)

The "Alert Timing" section (Min silence before alert, Confirm recovery, Re-alert after, Ad mix-in node) was inside `drawer-body` — the scrollable flex container. With chains that have 4+ node positions, the body content pushed Alert Timing far below the visible viewport and users could not scroll to it because the drawer footer overlapped.

Fix: Alert Timing is now a `<div class="drawer-timing">` element with `flex-shrink:0` sitting between `drawer-body` and `drawer-footer`. It is pinned above the footer and always visible regardless of how many signal path nodes the chain has. The scrollable body ends at the comparators section; Alert Timing is always in view.

**Rule**: Alert Timing fields (`builder_min_fault`, `builder_min_recovery`, `builder_min_alert_interval`, `builder_mixin_idx`) MUST remain in the `.drawer-timing` fixed panel, NOT inside `.drawer-body`. Moving them back inside the scrollable body causes them to be invisible for chains with ≥4 nodes.

---

## SignalScope-3.5.140 — 2026-04-17

### Fixed — Chain editor silently dropped per-node silence settings on save (3.5.140)

`_clean_single_node()` in `api_chains_save` only preserved `site`, `stream`, `label`, and `machine` — discarding `silence_threshold_dbfs`, `silence_off_threshold_dbfs`, and `offline_notify` that the UI collected and the JS sent. Any per-node threshold override a user configured was lost on every save.

Fix: `_clean_single_node()` now preserves all three fields. Additionally, a new `silence_min_duration` per-node override is supported — the node's "⋯ Options" panel shows a **Min silence (s)** field that controls how long that specific chain position must be silent before it's counted as down, independently of the input's own setting. The chain evaluation (`_eval_one_node`) reads `node.get("silence_min_duration")` as an override for `inp.silence_min_duration` on local inputs.

**Rule**: `_clean_single_node()` MUST preserve `silence_threshold_dbfs`, `silence_off_threshold_dbfs`, `silence_min_duration`, and `offline_notify` in addition to the four identity fields. Never reduce it back to identity-only.

---

## IP Link v1.1.31 — 2026-04-12

### Fixed — SIP call connects but media fails: "Called in wrong state: stable" (plugin v1.1.31)

**Root cause:** RFC 3261 §17.1.1.3 requires the ACK CSeq to exactly match the INVITE CSeq. `_sipSendAck` was calling `_sip.callCsq++` to increment the sequence counter before building the ACK — producing `CSeq: N+1 ACK` instead of `CSeq: N ACK`. The server never matched this to the outstanding INVITE transaction, so it kept retransmitting the 200 OK. Each retransmit triggered the 200 OK handler a second time. The first pass set `RTCPeerConnection` to `stable` via `setRemoteDescription`; the second pass called `setRemoteDescription` again on an already-stable PC, throwing "Failed to set remote answer sdp: Called in wrong state: stable".

**Fix 1 — correct ACK CSeq:** `_sipSendAck` now extracts the CSeq number from the 200 OK response headers (`okMsg.headers['cseq']`) rather than using the local counter. The local `_sip.callCsq` counter is NOT incremented for ACK.

**Fix 2 — duplicate 200 OK guard:** After sending ACK, the handler checks `if(_sip.state==='incall'){ return; }` before attempting `setRemoteDescription`. Server retransmits are silently ACKed without re-processing media negotiation.

---

## IP Link v1.1.28 — 2026-04-12

### Fixed — ACK for 4xx INVITE built from response headers, not nulled _sip state (plugin v1.1.28)

The ACK for non-2xx INVITE responses was built using `_sip.callCid` and `_sip.callFromTag`. By the time the server retransmits the 4xx (because it never received a valid ACK), `_sipCleanupCall()` has already nulled those fields — producing `Call-ID: null`, `From: ...;tag=null`, and Request-URI pointing to self instead of the callee. The server's retransmission loop continued indefinitely.

Fix: build the ACK entirely from the headers in the 4xx response message itself. RFC 3261 §17.1.1.3 specifies that the 4xx echoes Call-ID, From, and CSeq from the original INVITE — so `msg.headers` always has everything needed regardless of `_sip` state.

---

## IP Link v1.1.27 — 2026-04-12

### Debug + RFC compliance: ACK on 4xx INVITE, full SIP traffic logging (plugin v1.1.27)

**RFC 3261 §17.1.1.3 compliance:** Non-2xx final responses to INVITE must be ACKed. We were never sending ACK for 4xx responses — some servers retransmit the error or block the Call-ID until they receive the ACK. Now sends ACK immediately on any 4xx/5xx/6xx INVITE response before cleanup.

**SIP traffic logging:** All sent and received SIP messages are now logged to the browser DevTools console at `debug` level with `[IPLink SIP]` prefix. Filter on "IPLink SIP" in the console to see the full INVITE, REGISTER, and response exchange — makes it possible to diagnose 484 and other server errors.

**484 hint updated:** "check the dial string and SIP Domain/Realm setting".

---

## IP Link v1.1.26 — 2026-04-12

### Fixed — SIP 484 root cause: invalid SIP URI with no host (plugin v1.1.26)

**Root cause of 484 Address Incomplete:** `sip:test2` (no `@host`) is not a valid SIP URI. RFC 3261 requires `sip:user@host`. When we send `INVITE sip:test2`, the server parses `test2` as a *hostname* (no user part), cannot route to a host called `test2`, and returns 484. v1.1.25 made this worse by removing the domain entirely.

**What normal SIP clients do:** They use the server's SIP *realm* — obtained from the `realm=` field in the `WWW-Authenticate` header during REGISTER — as the domain for all call URIs. This is always correct regardless of what the WebSocket hostname is.

**Fix:**
1. `_sipDomain()` now uses: explicit SIP Domain config → realm learned from server's 401 challenge → WS hostname (last resort).
2. On each 401/407 REGISTER challenge, `auth.realm` is stored in `_sip.realm`.
3. `_sip.realm` is reset to `null` on each new `_sipConnect()` so stale realm from a previous server doesn't bleed through.
4. Dial URI always appends domain: `sip:test2@realm` — never bare `sip:test2`.

With a correctly registered SIP account the realm is learned automatically before any call is made, so bare extension dialling (`test2`) just works.

---

## IP Link v1.1.25 — 2026-04-12

### Fixed — SIP 484 on outgoing calls to bare extensions (plugin v1.1.25)

**Problem:** Typing `test2` in the dial box sent `INVITE sip:test2@sip.signalscope.site` because `sipDial` always appended `_sipDomain()`, which falls back to the WebSocket hostname when the SIP Domain field is blank. The server returned 484 Address Incomplete because it routes by extension name only and doesn't recognise that domain as authoritative.

**Fix:** `sipDial` now only appends a domain when the **SIP Domain** field is explicitly configured. With no explicit domain:
- `test2` → `sip:test2` (no domain — server routes by extension)
- `test2@pbx.local` → `sip:test2@pbx.local`
- `sip:test2@pbx.local` → used as-is

If your server needs a specific realm in the URI, set the **SIP Domain / Realm** field in SIP settings.

**Also fixed:** `_sipMungeSdp` now ensures the SDP body always ends with `\r\n` (RFC 4566 requirement). Without it, some SIP servers misparse the Content-Length boundary.

---

## IP Link v1.1.24 — 2026-04-12

### Fixed — same \r\n stripping bug on the answer path (plugin v1.1.24)

`iplink_post_answer` had the identical `.strip()` issue as the offer path fixed in v1.1.23. The hub's answer SDP was stored without its terminal `\r\n`, causing the talent's `setRemoteDescription` to fail on the last line with `a=ssrc:… cname:… Invalid SDP line.`

Fix: `room["answer"] = sdp + "\r\n"`.

---

## IP Link v1.1.23 — 2026-04-12

### Fixed — true root cause of all "Invalid SDP line" WebRTC errors (plugin v1.1.23)

**Root cause (server-side):** `iplink_post_offer` stored the SDP with `.strip()`, which removes the terminal `\r\n`. RFC 4566 requires every SDP line — including the last — to end with CRLF. Chrome's line-oriented SDP parser couldn't find the terminator for the last line and reported it as `"Invalid SDP line"`. Whichever line happened to be last changed the reported error:

- v1.1.20 (stripped `a=ssrc` + codecs): last line was `a=fmtp:111 …` → error on `a=fmtp:111`
- v1.1.21–22 (stripped only `a=ssrc`): last line was `a=fmtp:101 0-16` or similar → error shifted
- v1.1.21 (no strip): last line was `a=ssrc:… msid:UUID UUID` → error on `a=ssrc`

Every version was reporting a different line, but the underlying cause was always the same missing `\r\n`.

**Fix:** `room["offer"] = sdp + "\r\n"` — restore the CRLF that `.strip()` removed.

**JS change:** `_mungeOfferSdp` removed from the room call path. The raw offer (now properly CRLF-terminated) is passed straight to `pc.setRemoteDescription()`. Two WebRTC browsers negotiate natively; any JS rewriting of the SDP only creates new opportunities to break PT references.

`_sipMungeSdp` retained for outgoing SIP INVITEs (strips WebRTC attribute lines that SIP servers reject — no `m=` rewriting).

---

## IP Link v1.1.22 — 2026-04-12

### Fixed — strip only a=ssrc lines, leave everything else intact (plugin v1.1.22)

The actual error from Chrome was:
```
a=ssrc:323010011 msid:bcd1ebfb-... f9b58be9-... Invalid SDP line.
```

This is the `a=ssrc:SSRC msid:STREAM TRACK` two-identifier format. Chrome M130+ removed support for this deprecated Plan B / pre-Unified-Plan construct. Older Chrome versions and Safari still generate it. Chrome M130+ hub rejects it in `setRemoteDescription`.

**Fix:** `_mungeOfferSdp` now does exactly one thing: filter out `a=ssrc` and `a=ssrc-group` lines. All other SDP lines — including `m=`, `a=rtpmap`, `a=fmtp`, `a=rtcp-fb` — are passed through unchanged. This preserves all PT references so Chrome can parse the offer without error.

v1.1.21 went too far by removing all munging; v1.1.22 adds back the one strip that is actually needed.

---

## IP Link v1.1.21 — 2026-04-12

### Fixed — root cause of all "Invalid SDP line" WebRTC errors (plugin v1.1.21)

**Root cause identified:** All previous munge attempts were wrong. The `_sdpClean` function rewrote `m=` lines to remove codecs (rtx, RED, G722, PCMU, etc.) while leaving `a=fmtp` lines for those same PTs in place. Chrome's SDP parser rejects `a=fmtp:PT` when PT is no longer in the `m=` line — hence "Invalid SDP line" on `a=fmtp:111`.

**Fix:** For room-to-room WebRTC calls, the talent's raw offer SDP is now passed **unchanged** to `pc.setRemoteDescription()`. Both ends are WebRTC browsers; they negotiate codecs natively via the offer/answer exchange. No client-side SDP manipulation is needed or safe — any rewriting of `m=` lines or removal of codecs risks creating PT reference inconsistencies.

The entire `_sdpBuildMaps` / `_sdpClean` / `_mungeOfferSdp` machinery has been removed.

**SIP INVITE munge retained** — the hub's Chrome offer does contain WebRTC-specific attribute lines that SIP servers reject. `_sipMungeSdp` now uses a simple line-level strip with **no `m=` rewriting**: strips `a=ssrc`, `a=extmap-allow-mixed`, `a=rtcp-rsize`, `a=rtcp-fb`, and normalises `a=extmap:N/direction` → `a=extmap:N`. Codecs and `m=` lines are left intact.

---

## IP Link v1.1.20 — 2026-04-12

### Fixed — universal unconditional SDP munge for all hub browsers (plugin v1.1.20)

**Root cause of all recurring "Invalid SDP line" errors identified:**

The v1.1.19 fix was wrong. It made the munge Safari-only on the assumption that "Chrome's offer is valid for Chrome". This is no longer true:

- **Chrome M130+** now rejects `a=ssrc:SSRC msid:STREAM TRACK` (the two-identifier format) that other browsers still generate in their offers. Chrome was silently failing for talent contributors using Firefox, older Chrome, or Safari.
- **Safari** rejects `a=rtcp-fb: transport-cc` and similar RTCP feedback lines that Chrome includes.

Neither browser's raw WebRTC offer is accepted cleanly by the other. Browser-detection is the wrong approach — it only fixes the specific browser being tested, not the talent's browser.

**Fix: `_sdpClean` is now unconditional.** The `forSafari` parameter and dead branch have been removed. The full munge always runs:
- Strip `a=ssrc` / `a=ssrc-group` (Chrome M130+ + Safari)
- Strip `a=extmap-allow-mixed` (Safari misreport fix)
- Strip `a=rtcp-rsize` (some Safari builds)
- Strip `a=rtcp-fb:` lines (Safari rejects transport-cc; optional for any browser)
- Normalise `a=extmap:N/direction` → `a=extmap:N`
- Drop `rtx`/`ulpfec`/`flexfec` rtpmap/fmtp lines
- Drop orphaned `a=fmtp` lines with no corresponding `a=rtpmap`
- Rewrite `m=` lines removing dropped PTs

The same unconditional munge is applied to outgoing SIP INVITE SDPs via `_sipMungeSdp`.

---

## IP Link v1.1.19 — 2026-04-12

### Fixed — Chrome WebRTC parse error + SIP 500 on outgoing calls (plugin v1.1.19)

**WebRTC SDP munge is now Safari-only.**  
`_mungeOfferSdp` now detects the hub browser at runtime via `navigator.userAgent`. When the hub is Chrome or Firefox, the raw offer SDP is passed directly to `setRemoteDescription` — no stripping, no rewriting. Chrome's offer is valid for Chrome; the previous munging was accidentally breaking it (stripping things Chrome relied on, causing "Invalid SDP line" errors). Safari still receives the full munge to strip `a=extmap-allow-mixed`, direction specifiers, static-PT rtpmap entries, etc.

**Outgoing SIP INVITE SDP is now cleaned before sending.**  
`_sipMungeSdp` (new) strips Chrome-specific WebRTC junk (`a=ssrc:…`, `a=extmap-allow-mixed`, `rtx`/`ulpfec`/`flexfec` codecs) from the offer SDP before it's embedded in the INVITE body. Many SIP servers — even those with WebRTC support — choke on these lines and return a 500. The essential WebRTC parts (ICE candidates, DTLS fingerprint, Opus codec) are preserved.

**Better SIP error hints.**  
SIP error responses (400–599) now show a plain-English hint: 404 → "extension not found", 486 → "busy", 488 → "server rejected codec/SDP", 500 → "check dial plan / extension exists", etc.

**Code cleanup:** shared `_sdpBuildMaps` + `_sdpClean` helpers eliminate duplication between the WebRTC and SIP munge paths.

---

## IP Link v1.1.18 — 2026-04-11

### Fixed — comprehensive SDP munge to stop whack-a-mole parse failures (plugin v1.1.18)

Root cause of the recurring `a=fmtp:111 … Invalid SDP line` errors (and previous similar reports) identified and fixed:

**`a=extmap-allow-mixed`** — A session-level attribute Chrome adds to all offers. Older Safari WebKit doesn't support it and, critically, **misreports** the resulting parse failure as a different, later line in the SDP (e.g. `a=fmtp:111 minptime=10;useinbandfec=1 Invalid SDP line` even though that line is perfectly valid). Stripping this attribute from offers before `setRemoteDescription` prevents the misreported failures entirely.

**`a=extmap:N/direction` direction specifiers** — Chrome includes direction modifiers on `extmap` lines (`/recvonly`, `/sendonly`, etc.) that are unsupported in older Safari. These are now normalised to plain `a=extmap:N` (bidirectional, universally supported).

**`a=rtcp-rsize`** — Reduced-size RTCP; some Safari builds reject it. Now stripped.

**`rtx` / `ulpfec` / `flexfec`** — Retransmission and FEC codecs added to `_SDP_DROP_CODECS`. Not needed for audio contribution; can confuse some SDP parsers.

**Orphan guard** — `a=fmtp` and `a=rtcp-fb` lines whose payload type has no corresponding `a=rtpmap` line are now stripped. An orphaned fmtp (PT with no rtpmap) causes Chrome to report `Invalid SDP line` for the fmtp line. This catches any non-standard contributor browser that sends malformed SDP.

**m= line hardening** — Static payload types (≤95) with no explicit rtpmap are now also stripped from the `m=` line, not just those with rtpmap entries.

**SDP console logging** — When `setRemoteDescription` fails, the munged SDP that was passed to the browser is now logged to the console (`console.debug`) so it can be inspected in DevTools to diagnose any future issues.

---

## IP Link v1.1.17 — 2026-04-11

### Fixed — drop ALL static payload type rtpmap entries for Safari (plugin v1.1.17)

Safari rejects explicit `a=rtpmap` entries for static payload types (0–95) — PT 9 G722/8000 was the latest. Rather than blacklisting codecs one by one, `_mungeOfferSdp` now drops ALL `a=rtpmap` lines where the payload type is 0–95. These are defined by RFC 3551 and Chrome shouldn't need to list them explicitly. Opus is always a dynamic type (96+) and is unaffected.

---

## IP Link v1.1.16 — 2026-04-11

### Fixed — PCMA/PCMU SDP rejection + SIP CSP patch approach (plugin v1.1.16)

Safari rejects `a=rtpmap:8 PCMA/8000` (G.711). Added PCMA and PCMU to `_SDP_DROP_CODECS` — both are static payload types that Chrome lists explicitly but Safari refuses to parse. Opus remains as the negotiated codec.

SIP CSP fix rewritten: replaced the WSGI middleware approach with `after_request_funcs.insert(0, ...)`. Flask processes `after_request` handlers in reverse list order, so inserting at position 0 guarantees our handler runs last — after SignalScope has already set the `connect-src 'self'` CSP header — extending it to `connect-src 'self' wss:`.

Added `securitypolicyviolation` event listener in JS: if the browser's CSP does block the SIP WebSocket, a specific error is shown ("blocked by browser security policy") rather than the generic WebSocket error.

---

## IP Link v1.1.15 — 2026-04-11

### Fixed — Safari rejects CN/RED codecs in offer SDP (plugin v1.1.15)

Added CN (Comfort Noise, PT 13) and RED (Redundant Audio) to the SDP codec drop list alongside telephone-event. Safari rejects `a=rtpmap:13 CN/8000` with "Invalid SDP line". Neither CN nor RED is needed for audio contribution. The `_SDP_DROP_CODECS` regex now covers all three.

---

## IP Link v1.1.14 — 2026-04-11

### Fixed — Safari rejects telephone-event codec in offer SDP (plugin v1.1.14)

Safari rejected `a=rtpmap:126 telephone-event/8000` with "Invalid SDP line". Rather than patching one line at a time, `_mungeOfferSdp` now does a proper codec removal: finds all `telephone-event` payload type numbers, strips their `a=rtpmap`/`a=fmtp`/`a=rtcp-fb` attribute lines, and removes the payload type numbers from the `m=` line. DTMF is not used in audio contribution so this has no effect on call quality.

---

## IP Link v1.1.13 — 2026-04-11

### Fixed — SIP WebSocket blocked by CSP (plugin v1.1.13)

Root cause: SignalScope's Content-Security-Policy sets `connect-src 'self'`, which the browser enforces by silently killing any WebSocket connection to a different domain (e.g. `wss://sip.signalscope.site:8089`). The server was working correctly — `101 Switching Protocols` confirmed — but the browser never let the connection open.

Fix: wraps `app.wsgi_app` with WSGI middleware that patches `connect-src 'self'` → `connect-src 'self' wss:` in the CSP response header, but only for the `/hub/iplink` page. Runs after all Flask `after_request` handlers so SignalScope's CSP header is set first, then we extend it.

---

## IP Link v1.1.12 — 2026-04-11

### Fixed — Safari rejects all a=ssrc lines, not just msid (plugin v1.1.12)

v1.1.11 only stripped `a=ssrc` lines containing `msid`. Safari also rejects `a=ssrc:N cname:...` and all other `a=ssrc` variants. `_mungeOfferSdp` now strips all `a=ssrc` and `a=ssrc-group` lines from the offer before `setRemoteDescription`.

---

## IP Link v1.1.11 — 2026-04-11

### Fixed — Chrome→Safari SDP parse failure (plugin v1.1.11)

`setRemoteDescription` was throwing `Failed to parse SessionDescription — a=ssrc:N msid:... Invalid SDP line` when the talent used Chrome and the hub used Safari. Chrome generates `a=ssrc` source-attribute lines with `msid` in a format that Safari's WebRTC stack rejects. Fixed by stripping these lines from the offer SDP before passing to `setRemoteDescription` (`_mungeOfferSdp()`). The lines are informational only and not required for the connection.

---

## IP Link v1.1.10 — 2026-04-11

### Fixed — SIP WebSocket error hint improved (plugin v1.1.10)

Replaced the `/ws`-specific hint with actionable diagnostics covering the real common causes: untrusted certificate (with a direct link to open the HTTPS URL in a new tab to accept it), wrong port, server not running, firewall blocking.

---

## IP Link v1.1.9 — 2026-04-11

### Fixed — WebRTC errors now visible in UI; SIP hint improved (plugin v1.1.9)

When a WebRTC connection attempt fails, the error reason is now shown directly in the room card as a red inline message (auto-clears after 10 s). Previously it was silently swallowed by `console.error` only.

Added an outer `.catch()` to the offer-fetch chain in `acceptCall()` — previously, if the `fetch` or `r.json()` call itself failed, `_pcs[roomId]` stayed as `true` with no error shown and no way to retry.

Replaced the blocking `alert('No offer found…')` with the same inline error mechanism.

SIP WebSocket error hint updated: when the URL starts with `wss://`, the hint now specifically calls out the missing `/ws` path that Asterisk/FreePBX requires (e.g. `wss://host:8089/ws`).

---

## IP Link v1.1.8 — 2026-04-11

### Fixed — Accept button no immediate feedback; SIP WebSocket error hint (plugin v1.1.8)

`acceptCall()` was missing the immediate `btn.textContent='⏳ Connecting…'` line after the `_pcs` guard was added in v1.1.7. Button now shows "Connecting…" immediately on click again.

SIP WebSocket error message now includes a specific hint: if the hub is on HTTPS and the configured server URL starts with `ws://` (not `wss://`), the browser blocks the connection as mixed content — the error now says so explicitly. If the URL is already `wss://`, the hint directs to certificate trust issues.

---

## IP Link v1.1.7 — 2026-04-11

### Fixed — Accept button timing window (plugin v1.1.7)

`_pcs[roomId]` was only set after `_getHubMic` callback + `fetch('/offer')` resolved (~1–2 s). The 1.5 s room-list refresh fired inside that window, saw no `_pcs` entry, and re-rendered a fresh Accept button. Fixed: `_pcs[roomId] = true` is set at the very start of `acceptCall()` (replaced with the actual `RTCPeerConnection` once created). Also added a double-click guard (`if(_pcs[roomId]) return`).

---

## IP Link v1.1.6 — 2026-04-11

### Fixed — Accept button reverts to Accept on WebRTC room connection (plugin v1.1.6)

`_renderRooms()` fires every 1.5 s and rebuilds the entire room grid from server data. While `acceptCall()` was negotiating WebRTC (which can take several seconds for ICE gathering), the room status on the server was still `offer_received`. The next poll overwrote the "Connecting…" button with a fresh "Accept" button, making it appear the connection had been rejected.

Fix: `_renderRooms` now checks `_pcs[r.id]` before rendering the Accept button. If a PeerConnection already exists for the room, it renders a disabled "⏳ Connecting…" button instead. `_pcs[roomId]` is deleted on WebRTC failure (catch) and on `connectionState === 'failed'` so the Accept button reappears only when the connection genuinely failed and a retry is needed. Also fixed: only mark the room disconnected on ICE `'failed'` (not transient `'disconnected'`).

---

## IP Link v1.1.5 — 2026-04-11

### Fixed — Accept reverts to incoming / call drops immediately (plugin v1.1.5)

Three bugs combined to cause the "Accept briefly shows connecting then reverts" symptom:

**1. `onconnectionstatechange` tore down on `'disconnected'`** — WebRTC `'disconnected'` is transient; ICE naturally bounces through disconnected while checking candidate pairs. The correct terminal state is `'failed'`. Tearing down on `'disconnected'` caused the call to be ended prematurely. Fixed: both `sipAnswerCall()` and `sipDial()` now only call `_sipCleanupCall()` on `connectionState === 'failed'`.

**2. `.catch()` didn't reset state** — if any step in the answer chain failed (getUserMedia denied, `setRemoteDescription` error, etc.), the catch sent a 500 response but left `_sip.state` as `'incoming'` with `_sip.inInvite` still set. The incoming banner and Accept button therefore reappeared. Fixed: catch now calls `_sipCleanupCall()` + `_sipSetState('registered')` + shows an error message.

**3. No intermediate state during answer** — state stayed `'incoming'` (banner + Accept button visible) throughout the entire getUserMedia/ICE gathering async chain, giving no visual feedback. Fixed: `_sipSetState('dialling')` is called immediately after sending 180 Ringing, hiding the banner and showing the call card while WebRTC negotiation runs.

Also: INVITE guard extended to include `'dialling'` state (re-INVITEs arriving during answer now receive 486 rather than spawning a second incoming call). ACK handler extended to fire from `'dialling'` state in case ACK arrives before the promise chain sets `'incall'`.

---

## IP Link v1.1.4 — 2026-04-11

### Fixed — Talent page stuck at "Initialising…" (plugin v1.1.4)

The contributor/talent page (`/iplink/talent/<id>`) had `<style>` and `<script>` tags without `nonce="{{csp_nonce()}}"`. SignalScope's Content Security Policy requires every inline `<style>` and `<script>` to carry the per-request nonce. Without it the browser silently blocks all CSS and JS — the page renders as unstyled static HTML, `window.addEventListener('load', ...)` never fires, the WebRTC setup never starts, and the status indicator stays permanently at "Initialising…".

Fix: added `nonce="{{csp_nonce()}}"` to both the `<style>` and `<script>` tags in `_TALENT_TPL`.

---

## IP Link v1.1.3 — 2026-04-11

### Fixed — New Room button dead / SIP JS blocked by CSP (plugin v1.1.3)

`csrf_token`, `csp_nonce`, and `topnav` are registered as Jinja2 **context processors** in SignalScope — they are automatically available in every `render_template_string` call without being passed explicitly. Previous code tried to resolve them via `sys.modules` by probing `hasattr(module, "topnav")`. Because `topnav` is an inner closure returned by the context processor (not a module-level attribute), the probe always failed, `_ss` stayed `None`, and all three helpers became no-op lambdas. Passing `csp_nonce=lambda:""` to the template **overrode** the working context-processor version, producing `nonce=""` on the script tag. An empty nonce doesn't match the CSP policy — the browser silently blocked the entire script block. With no JS running: the New Room button did nothing, the SIP dial button was unresponsive, and `_sipLoadCfg()` never fired.

Fix: removed all helper resolution code from `register()` and stopped passing `csp_nonce`, `csrf_token`, and `topnav` to `rts()`. They are injected automatically.

**Rule for all plugins**: never pass `csp_nonce`, `csrf_token`, or `topnav` as explicit keyword arguments to `render_template_string`. They are context processors — passing them explicitly risks overriding the real implementations with stale or incorrect values.

---

## IP Link v1.1.2 — 2026-04-11

### Fixed — "Plugin internal error" on hub page and talent join (plugin v1.1.2)

`_STUN` was referenced in both `iplink_hub()` and `iplink_talent()` route handlers but was never defined — only `_STUN_SERVERS` existed at module level. Every call to either route raised `NameError: name '_STUN' is not defined`, which was caught by `_wrap_view` and returned as `{"error":"Plugin internal error"}`. Fix: added `_STUN = _STUN_SERVERS` alias at module level.

---

## [3.5.138] - 2026-04-11

### Fixed — IP Link hub page error on load (plugin v1.1.1)

`iplink_hub()` tried to import `_csp_nonce`, `csrf_token`, and `topnav` dynamically on every request using `from signalscope import ...` with a `sys.modules` fallback. Both paths could fail at request time — particularly when the plugin is loaded as a sub-module rather than run directly — producing the "IPLink plugin error — could not import SignalScope helpers" 500 error.

Fix: the three helpers are resolved **once at `register()` startup time** (when `sys.modules` is fully populated and `signalscope` is always present) and closed over by the route function. `rts` (`render_template_string`) is also imported once at `register()` time rather than inside the route. A warning is logged if the module cannot be located, but the route no longer 500s.

---

## [3.5.137] - 2026-04-11

### Added — IP Link: SIP client (plugin v1.1.0)

The IP Link plugin now includes a full browser-based SIP softphone alongside the existing WebRTC room system. Hub operators can register with any SIP PBX that supports WebSocket transport (Asterisk, FreeSWITCH, 3CX, etc.) and take/make SIP calls directly from the IP Link page.

**SIP features:**
- **Registration** — connects to any SIP server via `wss://` WebSocket; digest authentication (RFC 3261 MD5 HMAC, with and without QOP); automatic re-registration before expiry; 30-second reconnect retry on disconnect
- **Incoming calls** — pulsing amber banner with caller ID; Answer / Decline buttons; 100 Trying → 180 Ringing → 200 OK with gathered ICE SDP answer; active call card shows remote/mic levels and duration
- **Outgoing calls** — dial field accepts extension, E.164 number, or full `sip:user@domain` URI; ICE gather-then-send (3 s timeout) before INVITE
- **Quality** — hardware echo cancellation and noise suppression via browser constraints; bidirectional Opus audio
- **Call management** — mic mute toggle; hang up; RTT displayed from WebRTC stats; call duration counter
- **Config persistence** — SIP credentials saved to `plugins/iplink_sip_cfg.json` via `GET/POST /api/iplink/sip/config`; password never echoed in GET response; auto-connect on page load when "Auto-connect" checkbox ticked
- **Zero dependencies** — compact pure-JS MD5 (RFC 1321) inlined for digest auth; no CDN, no WebSocket polyfill, no third-party SIP library

**SIP compatibility notes:** Server must support SIP over WebSocket (RFC 7118). In Asterisk this requires `res_http_websocket` + WebSocket transport in `pjsip.conf`. In FreeSWITCH, enable `mod_verto` or `mod_sofia` with WS transport. The plugin sends `Via: SIP/2.0/WS`, `Contact: <sip:user@host;transport=ws>`, and `User-Agent: SignalScope-IPLink/1.1`.

---

## [3.5.136] - 2026-04-11

### Added — IP Link plugin (WebRTC browser contribution codec)

New plugin `plugins/iplink.py` — a browser-based, software-only IP codec in the style of ipDTL. No app download required on talent side; the hub operator shares a URL and the contributor opens it in any modern browser (phone, laptop, tablet).

**Features:**
- **Named rooms** — hub creates rooms with a unique shareable link per talent; multiple rooms can be open simultaneously
- **Pure WebRTC** — low-latency Opus audio with hardware echo cancellation and noise suppression enabled; bidirectional audio for IFB/talkback
- **Quality presets** — Voice (64 kbps mono), Broadcast (128 kbps stereo), Hi-Fi (256 kbps stereo); applied via `RTCRtpSender.setParameters()` after connection
- **HTTP polling signalling** — no WebSocket dependency; SDP offer/answer and ICE candidates exchanged via REST with index-based append-only lists
- **Live level meters** — both talent and hub side level bars update in real time in the hub room cards
- **RTT & packet loss stats** — talent browser reports WebRTC stats (RTT ms, loss %) back to the hub for display
- **IFB mute** — hub operator can mute their own microphone into the IFB return with one click; talent sees "IFB muted" notice
- **Room lifecycle** — rooms expire automatically after 2 hours of inactivity; hub can also delete or reset rooms manually
- **Hub-only nav item** — plugin nav link suppressed in client-only mode (`hub_only: True`)

**Security:** Hub routes protected by `@login_required` + `@csrf_protect`. Talent routes use the room UUID (36-char entropy) as an implicit access token — no login required for talent contributors.

---

## [3.5.135] - 2026-04-11

### Fixed — New TX metrics not showing on main hub page

DAB MER/BER/freq correction and FM MPX power/pilot/carrier offset/deviation histogram/RDS TA/TP/PTY/CT were only rendering in the hub site replica page (HUB_SITE_TPL). The main hub dashboard (HUB_TPL) DAB and FM stats blocks have been updated to match. DLS stale badge also added to the main hub DAB stats block.

---

## [3.5.134] - 2026-04-11

### Added — TX high-site monitoring: 7 new diagnostic metrics

Seven new RF and site-health metrics for monitoring FM/DAB transmitter sites:

**1. CPU temperature on hub site cards**
`_build_system_health()` now reads CPU temperature via `psutil.sensors_temperatures()` with a fallback to `/sys/class/thermal/thermal_zone0/temp` (Raspberry Pi / Linux). The hub site summary bar shows `🌡 XX.X°C` coloured amber ≥65°C, red ≥75°C. `SITE_TEMP_HIGH` and `SITE_DISK_LOW` alert types added (fired with 1-hour cooldown at ≥75°C / ≥90% disk used).

**2. DAB MER + BER + frequency correction**
`_copy_dab_metrics_from_mux()` now extracts `demodulator.mer` (Modulation Error Ratio, dB), `demodulator.viterbiErrorRate`/`ber` (bit error rate), and `demodulator.frequencyCorr` from the welle-cli JSON. All three appear in the DAB stats panel (hub overview + client status page + heartbeat payload) with colour thresholds.

**3. FM RDS extended fields: TA, TP, PTY, CT**
`_apply_redsea_json()` now captures `ta` (Traffic Announcement), `tp` (Traffic Programme), `pty` (Programme Type code 0–31, accepting both `int` and `{"code": N}` forms), and `ct`/`clock_time` (RDS Clock Time, ISO 8601). Displayed in an `RDS+` row when any field is present. TA announcement shown in amber with 📻 icon.

**4. Carrier frequency offset**
Slow EMA (α=0.05, ≈1 s time constant) of the FM discriminator DC mean, scaled to Hz. `_fm_carrier_offset_hz` in `InputConfig`. Shown in FM stats when offset exceeds ±5 Hz; amber ≥±100 Hz indicating off-tune transmitter.

**5. MPX composite power (dBr)**
RMS of the raw MPX discriminator output expressed as dBr (0 dBr = ITU-R BS.412 full-scale modulation). Stored as `_fm_mpx_power_dbr`. Shown in FM stats with colour thresholds.

**6. Pilot injection level (%)**
Stereo pilot (19 kHz) amplitude extracted from the FFT, expressed as a percentage of ±75 kHz deviation. Standard broadcast is ~9%. Below 7% = weak pilot / stereo dropout risk; above 12% = over-modulated. Stored as `_fm_pilot_pct`.

**7. Deviation histogram / compliance log**
Rolling session counters (`_fm_dev_n67`, `_fm_dev_n72`, `_fm_dev_n75`, `_fm_dev_total`) track the percentage of audio time exceeding 67.5 kHz (EBU R68 programme limit), 72 kHz, and 75 kHz (Ofcom hard limit). Shown as `Dev % 67.5:X% 72:X% 75:X%` in FM stats. All counters reset on monitor restart (session-based).

---

## [3.5.133] - 2026-04-09

### Fixed — FM relay via hub drops after 1–2 minutes (stereo/mono transition corrupts MP3 stream)

**Root cause**: When the FM pilot signal is marginal (SNR 8–14 dB), `_fm_stereo` is True but `_stereo_blend` is 0. The monitoring loop cleared `out_buf_L/R` and wrote **mono-format** frames (4 800 samples) into `_audio_buffer`. Any active hub relay started while the pilot was strong enough for stereo uses ffmpeg `-ac 2` (stereo). When the first mono-format frame arrives, ffmpeg interprets 4 800 int16 samples as 2 400 stereo pairs — half the expected duration — producing corrupt/half-speed MP3. The browser decoder silently drops the connection.

The failure mode was timing-dependent: a strong signal kept the relay working until the pilot SNR dipped below 14 dB for even one frame (~100 ms), at which point a single mixed-size chunk broke the ffmpeg stream.

**Fix**: When `_fm_stereo=True` and `_stereo_blend=0` (weak pilot, not force-mono), duplicate `mono_48` to both L and R channels instead of clearing them. `_audio_buffer` now receives consistently stereo-interleaved frames (L=R=mono_48) across the entire blend=0 period. The relay hears correct stereo-bitrate mono (both channels identical) and smoothly transitions to full stereo when the pilot recovers — no reconnect required.

`fm_force_mono` (explicit user toggle) is unaffected: that path still clears L/R to force mono-format chunks in `_audio_buffer`.

**Rule**: Never clear `out_buf_L`/`out_buf_R` in the `_b <= 0.0` branch when `not _force_mono` and `cfg._fm_stereo is True`. Use mono-dup instead. Clearing them while the relay expects stereo-format data produces mixed-size `_audio_buffer` contents that corrupt ffmpeg's fixed `-ac 2` input stream.

---

## [3.5.132] - 2026-04-08

### Added — DLS / RDS RadioText stale detection

DAB DLS (Dynamic Label Segment / now-playing text) and FM RDS RadioText that hasn't changed in 10 minutes are now flagged as stale.

**Hub overview page**: the DLS and RDS Text rows turn amber and display `⏰ Xm stale` when text is unchanged for ≥10 minutes, indicating a likely playout automation or broadcast chain fault.

**Hub Reports**: `DLS_STALE` and `RDS_STALE` alert types added. The hub fires one alert per stream when text goes stale (>10 min unchanged), then respects a 1-hour cooldown before re-alerting. The stale indicator resets immediately when the text changes again. Both types are always visible in the Reports Type filter.

**Client-side tracking**: `InputConfig` gains `_dab_dls_last_change_ts` and `_fm_rds_rt_last_change_ts` runtime fields. The heartbeat payload includes `dab_dls_stale_mins` and `fm_rds_rt_stale_mins` (0.0 when fresh, minutes elapsed when stale). Timestamps are seeded on first observation so DLS/RDS that arrives and never changes correctly triggers the stale alert after 10 minutes.

**Note on "set expected" buttons**: The existing `📌 Set`/`📌 Update` buttons for Expected DAB Service (`expected_dab_service`) and Expected RDS PS name (`expected_fm_rds_ps`) are confirmed present in the hub template. DLS text changes with every song and is not suitable for pinning as a fixed expected value — the staleness timer provides the equivalent monitoring signal.

---

## [3.5.131] - 2026-04-08

### Fixed — Pi 5 DAB: more retries + longer delays for USB autosuspend recovery

The healthy Pi log confirmed that "Signal caught, exiting!" during startup is a real crash (fires before TCP connection), distinct from the normal post-connection cancel-async message. The Pi 5 RP1 USB DMA state IS recoverable without a power cycle — it just needs more attempts and longer waits.

Changes to the rtl_tcp retry loop:
- Max attempts raised from 2 → 5
- Signal-caught delays: 8 s, 10 s, 15 s, 20 s (increasing per retry, previously just 3 s wait shared with -6 errors)
- Busy (-6) delays: 3 s, 5 s, 8 s, 10 s (previously just 3 s)
- Attempt counter logged on each failure so it's clear how many retries remain
- Removed the hardcoded 2 s sleep inside the ioctl reset block (delay now comes from the per-retry table)

---

## [3.5.130] - 2026-04-07

### Fixed — Restore Pi DAB monitor loop to 3.5.126 behaviour (rtl_tcp proxy + DVB unbind)

3.5.127 unified the DAB monitor loop to the x86 path on all platforms. This broke DAB monitoring on Raspberry Pi (direct welle-cli without rtl_tcp fails because Pi apt welle-cli ignores `-F rtl_sdr,N` device selection). Restored:

- Pi-specific rtl_tcp proxy launch (with USB autosuspend disable attempt and ioctl reset on "Signal caught")
- DVB driver unbinding block (`dvb_usb_rtl28xxu`)
- Pi carousel pre-warmer branch (warm only consumer SIDs, not the full ensemble)
- Non-Pi path unchanged: direct welle-cli, no `-C`

---

## [3.5.129] - 2026-04-07

### Fixed — Bulk hub config changes no longer cause rapid-fire monitor restart storm

When toggling stereo (or any restart-triggering field) on multiple inputs simultaneously from the hub, each `set_input_field` command previously called `stop_monitoring()` + `start_monitoring()` immediately. N inputs changed in one heartbeat ACK → N rapid USB open/close cycles → USB stack corruption on Pi 5 RP1 controller.

Fix: `_restart_if_running()` is now debounced. Any number of config changes within a 2-second window are coalesced into a single restart. The same applies to `add_input`, `remove_input`, and `toggle_input` hub commands.

---

## [3.5.128] - 2026-04-07

### Added — ⚡ USB Fix button on hub site cards (push RTL-SDR autosuspend fix remotely)

New **⚡ USB Fix** button on every hub site card. Opens an inline password prompt; sends the udev rule setup command to the remote client via the heartbeat channel — no shell or direct web access to the client needed.

- New client command `setup_usb_autosuspend`: writes `/etc/udev/rules.d/99-rtlsdr-autosuspend.rules` and reloads udev. Tries direct write first (root), falls back to `sudo -S tee` with provided password.
- New hub route `POST /api/hub/site/<site>/setup_usb_autosuspend`
- Result appears in the site log within one heartbeat cycle (~10 s). Replug the dongle or use ⏻ Reboot to activate.

---

## [3.5.127] - 2026-04-07

### Changed — DAB monitor loop unified: Pi now uses same path as x86

Removed all Pi-specific branching from `_start_dab_session`:
- rtl_tcp proxy launch (and all its retry/USB-reset/autosuspend logic) removed
- DVB driver unbinding block removed
- Pre-warmer carousel-mode Pi branch removed (all platforms now warm all services in parallel)

welle-cli is now launched identically on Pi and x86: no `-C`, no `-T`, using `-F rtl_sdr,{device_idx}` directly. Since DAB scans already work via this path on Pi, the monitor loop should too.

---

## [3.5.126] - 2026-04-07

### Added — Version selector: install any GitHub release on a remote client from the hub

A new **📦 Version** button appears on every online hub site card. Clicking it opens an inline panel that fetches all SignalScope releases from GitHub and presents them in a dropdown. The selected version is installed on the remote client via the existing heartbeat command pipeline — the client downloads the raw Python file directly from GitHub, validates it with `py_compile`, replaces itself, and restarts. Works for both upgrades and downgrades.

**New endpoints:**
- `GET /api/hub/releases` — returns all GitHub releases with tag, name, and raw download URL
- `POST /api/hub/site/<site>/install_version` — pushes install command with `direct_url` payload

**New client path:** `_run_direct_update` downloads from the GitHub raw URL directly (no hub relay needed), bypassing the `hub/update/download` endpoint used for hub-pushed updates. URL is validated server-side to only allow `raw.githubusercontent.com/itconor/SignalScope/` URLs.

The site is automatically put into chain maintenance mode (15 min) during install, same as the standard update flow.

---

## [3.5.125] - 2026-04-06

### Fixed — Pi 5 RTL-SDR USB autosuspend kills rtl_tcp ("Signal caught, exiting!")

On Raspberry Pi 5, the RP1 USB controller suspends the RTL-SDR dongle during DMA buffer allocation, causing rtl_tcp to crash immediately with "Signal caught, exiting!". Three-layer fix:

1. **Automatic sysfs attempt** — before each rtl_tcp launch, SignalScope tries to write `-1` to the device's `/sys/.../power/autosuspend` sysfs attribute (no sudo; silent on failure if not writable)
2. **Auto-recovery** — if "Signal caught" is detected in rtl_tcp stderr, a USB device reset is performed via `USBDEVFS_RESET` ioctl (works without root if RTL-SDR udev rules give device node access) before retrying
3. **Permanent fix** — Settings → Maintenance → **⚡ RTL-SDR USB Autosuspend Fix**: enter sudo password once to write `/etc/udev/rules.d/99-rtlsdr-autosuspend.rules` and reload udev. Replug the dongle or reboot to activate. This is the definitive fix.

---

## [3.5.124] - 2026-04-06

### Fixed — Revert Pi DAB to 3.5.114 rtl_tcp-for-all-Pi behaviour

Removes the `_has_fm_inputs` gate introduced in 3.5.118. Pi DAB sessions now always use the rtl_tcp proxy path, exactly as in 3.5.114. The stale-killer fix from 3.5.117 (match on `-d {device_idx}` not on port) is retained.

---

## [3.5.123] - 2026-04-06

### Added — Kill Welle button; Reboot Setup in Settings

**🔌 Kill Welle** button added to hub site cards. Pushes a `kill_welle` command via heartbeat; client runs `pkill -f welle-cli` and `pkill -f rtl_tcp` on its own user's processes — no sudo required. Frees a stuck SDR dongle without rebooting.

**⏻ Remote Reboot Setup** section added to Settings → Maintenance. Enter the machine's sudo password once and click "Setup Reboot Permission" — writes `/etc/sudoers.d/signalscope-reboot` using `sudo -S tee`. After that the hub's ⏻ Reboot button works permanently without any shell access or further configuration.

---

## [3.5.122] - 2026-04-06

### Fixed — Remote reboot: try root, systemctl, sudo in order; no shell setup required

Updated the `_cmd_reboot` handler to try three methods in sequence:
1. **Direct reboot** (`os.system("reboot")`) — used immediately if SignalScope is running as root (common when RTL-SDR USB access requires root)
2. **`systemctl reboot`** — works on modern systemd/Pi OS Bookworm without any extra configuration via polkit/logind
3. **`sudo -n reboot`** — falls back to this only if the above two fail; requires a passwordless sudoers entry

This means no shell setup is needed for the majority of deployments.

## [3.5.121] - 2026-04-06

### Added — Remote OS reboot from hub overview

Added **⏻ Reboot** button to each site card on the hub overview. Clicking it sends a `reboot` command to the remote client via the next heartbeat ACK (~10 s). The client handler tries `systemctl reboot` first (works on most modern systemd systems via polkit/logind without extra config), then falls back to `sudo reboot`. If `sudo reboot` is needed, add a sudoers rule on the client:

```
echo '<user> ALL=(ALL) NOPASSWD: /sbin/reboot' | sudo tee /etc/sudoers.d/signalscope-reboot
```

The button is always visible (not gated on running state) so you can reboot a site that has a hung/stuck process. A confirmation dialog warns that the machine will go offline for ~60 s.

---

## [3.5.120] - 2026-04-06

### Fixed — Pi DAB-only: revert to standard non-Pi welle-cli command

The dedicated Pi-DAB-only branch (`elif _is_raspberry_pi() and not _has_fm_inputs`) is removed. Pi nodes with no FM inputs now fall through to the standard non-Pi path: `welle-cli -F rtl_sdr,{device_idx}`. This is the command that was in use before the rtl_tcp proxy work was added, and it was working correctly. The Airspy probe (`AIRSPY_ERROR_NOT_FOUND`) is benign — welle-cli still finds the RTL-SDR after it. Removing the Pi-specific `-T -C N` flags and the special-case branch restores the behaviour that was confirmed stable.

---

## [3.5.119] - 2026-04-06

### Fixed — Pi DAB-only: use welle-cli natively without -F rtl_sdr

3.5.118 correctly skipped the rtl_tcp proxy for DAB-only Pi nodes but incorrectly fell through to the non-Pi command path, which passed `-F rtl_sdr,N` and was missing the Pi CPU flags (`-T -C N`). Fix: dedicated `elif` branch for Pi+no-FM that uses welle-cli natively without any `-F` flag (Pi welle-cli ignores all device-selection args and opens the first RTL-SDR automatically) while retaining the Pi-specific `-T` (disable TII) and `-C N` (limit encoder slots) flags.

---

## [3.5.118] - 2026-04-06

### Changed — Pi DAB: skip rtl_tcp proxy when no FM inputs are configured

The rtl_tcp proxy was introduced for Raspberry Pi to work around welle-cli's broken device-selection (it always opens USB device 0 regardless of `-F`/`-D` flags). The proxy is only necessary when an FM `rtl_fm` stream is competing for the same USB bus — without FM, welle-cli can open the DAB dongle directly with `-F rtl_sdr,N`.

On DAB-only Pi nodes, running the unnecessary rtl_tcp layer adds a process that is susceptible to USB autosuspend events (Pi librtlsdr prints "Signal caught, exiting!" for USB device errors, not just OS signals), causing repeated DAB session restarts.

Fix: `_start_dab_session` now checks `self.app_cfg.inputs` for any enabled `fm://` inputs. If none are present, the Pi rtl_tcp proxy path is skipped and welle-cli uses `-F rtl_sdr,N` directly — the same path as non-Pi hardware.

---

## [3.5.117] - 2026-04-06

### Fixed — Pi DAB rtl_tcp stale killer matched wrong processes (port=0 / serial not in argv)

The rtl_tcp stale-killer block in `_start_dab_session` had two bugs:

1. **port=0 overkill**: for a brand-new session `session.rtl_tcp_port = 0`. `str(0) = "0"` is a substring of virtually every rtl_tcp command line (`-d 0`, `127.0.0.1`, any port containing the digit 0), so the killer matched and SIGKILL'd **all** running rtl_tcp processes — including unrelated ones on other channels or dongles.

2. **serial never in rtl_tcp argv**: `session.serial` ("DAB_DONGLE_1") does not appear in rtl_tcp's command line. rtl_tcp is launched with `-d INDEX` (a number), not by serial. The serial match therefore never fired.

Fix: replaced both checks with a single `-d {device_idx}` substring match (space-padded to avoid partial matches), which correctly identifies rtl_tcp processes holding the same USB device index.

This bug could have caused rtl_tcp processes for a **working** mux session to be killed when a **different** mux restarted on the same Pi (because the device index=0 appears in both command lines).

---

## [3.5.116] - 2026-04-06

### Fixed — Hub replica page: DAB add doesn't assign dongle serial or PPM

When adding a DAB service from the hub's remote management modal, the dongle serial field was a free-text input defaulting to empty and the PPM defaulted to 0. The new service was added without a serial, so SignalScope could grab any available dongle instead of the correct one.

Fix: the DAB serial field is now a **dropdown** auto-populated from the serials already in use by existing DAB inputs on that client site (parsed from their `dab://…?channel=…&serial=…&ppm=…` `device_index` strings). Selecting a serial from the dropdown also updates the PPM field to match. A "none (auto)" option is available for sites with no existing DAB inputs; on those sites a free-text input is shown as before. The PPM field initial value is also pre-populated from the first existing dongle's PPM when a dropdown is rendered.

---

## [3.5.115] - 2026-04-06

### Fixed — Hub Reports: CHAIN_FAULT chain column showing input name instead of chain name

Per-node `CHAIN_FAULT` events written by `_cmd_save_clip → _add_history` on client nodes have `stream = input_name` (e.g., "Kiss FM"), not the chain name. Hub Reports incorrectly assumed all `CHAIN_FAULT` events have `stream = chain_name`, so the Chain column showed `⛓ Kiss FM` instead of `⛓ Cool FM`. The Chain filter was also inconsistent as a result — filtering by "Cool FM" would miss those per-node entries.

Fix: the `_chain` display field is now resolved correctly for both event sources. If `stream` matches a known chain name it is used directly (the main hub-side CHAIN_FAULT entry). Otherwise a `stream_to_chains` lookup maps the input name to its chain(s) — the same lookup used for all other event types. Events for inputs genuinely not in any chain show `—` in the Chain column.

---

## [3.5.114] - 2026-04-06

### Fixed
- **Mono-on-Stereo false positives on DAB inputs** — DAB joint stereo encodes L/R as mid/side rather than two independent channels. The decoded L and R have near-identical RMS levels even on genuine stereo content, so the level-based correlation estimate always sits near 1.0 and the alert fires constantly. The detector is now suppressed for any input whose `device_index` starts with `dab://`. The Settings UI help text also notes this limitation.

---

## [3.5.113] - 2026-04-06

### Added
- **Detection settings panel on replica page** — the ⚙ button and inline settings panel (added to the main hub overview in 3.5.111) is now also present on every stream card on the hub site replica page (`/hub/site/<name>`).

---

## [3.5.112] - 2026-04-06

### Fixed — Hub replica page 500 when Live View first enabled

When Live View was toggled ON for a site, the client immediately started sending `live_push` frames. If a stream appeared in those frames before the first heartbeat had been received (or between heartbeats), `hub_live_push` injected a minimal stub entry with only `name`, `enabled`, and the fast-changing metric fields. The hub template then accessed fields like `device_index`, `silence_threshold_dbfs`, `alert_on_silence`, etc. that didn't exist on the stub — causing a Jinja2 `UndefinedError` → HTTP 500 on the next replica page load or `/api/hub/site/.../data` poll. The page recovered once a real heartbeat arrived (~10 s) and replaced the stub.

Fix: stub entries created by `hub_live_push` now include safe defaults for all template-accessed fields.

---

## [3.5.111] - 2026-04-06

### Added — Remote detection settings from hub stream cards

Each stream card on the hub overview now has a ⚙ button (in the listen strip) that opens a settings panel inline. From there hub admins can toggle any detection on/off and adjust silence threshold/duration without SSH-ing into the client. Changes are queued as `set_input_field` commands and delivered on the next client heartbeat (~10 s). A green outline flashes on the control to confirm the command was accepted; red means the request failed.

**Toggles**: Silence, Clip, Hiss, Overmod, Mains Hum, DC Offset, Phase Reversal, L/R Imbalance, Mono-on-Stereo, Glitch, Flatness, AI Monitor.

**Numeric fields**: Silence threshold (dBFS), Silence duration (s).

Also expanded the `set_input_field` API and client command handler to support numeric (float) fields in addition to booleans — previously only boolean fields were remotely settable.

---

## [3.5.110] - 2026-04-06

### Fixed — Alert noise reduction for new streams

Several `InputConfig` loader fallback values did not match the dataclass defaults, meaning streams loaded from older config files silently got more aggressive alert settings than intended. Fixed:

| Alert | Old loader fallback | New (matches dataclass) |
|---|---|---|
| `alert_on_hiss` | `True` | `False` |
| `alert_on_hum` | `True` | `False` |
| `alert_on_dc_offset` | `True` | `False` |
| `silence_min_duration` | 3 s | 10 s |
| `overmod_clip_pct` | 20 % | 30 % |

`alert_on_overmod` default changed from `True` → `False` in both the dataclass and loader — broadcast streams are heavily limited and routinely peak near 0 dBFS, making this fire constantly on normal programme audio.

Added a 30-second startup grace period for silence detection. Every stream connect begins with a brief silence while the source buffers/connects; without this, every new/restarted stream fired a spurious SILENCE then `silence_end` clip pair immediately on startup.

---

## [3.5.109] - 2026-04-06

### Fixed
- **Morning Report Regenerate button does nothing** (morning_report v1.2.3) — `_btnLoad`, `_btnReset`, and `_ssToast` were called in the button's click handler but never defined in the plugin template. Clicking the button threw a `ReferenceError` immediately, crashing the handler before the `fetch` was even sent — no spinner, no toast, no reload. Fixed by inlining all three helper functions directly in the plugin's `<script>` block.

---

## [3.5.108] - 2026-04-06

### Fixed
- **`signalscope.backup-*.py` files detected as plugins** — the plugin migration scanner and the `plugins/` loader both checked for the string `"SIGNALSCOPE_PLUGIN"` in a file's source, then skipped only the exact filename `signalscope.py`. Any backup copy (e.g. `signalscope.backup-3.5.104.py`) passed that guard, got migrated into `plugins/`, and appeared as a broken plugin entry. Fix: both scanners now skip any `.py` file whose stem starts with `signalscope` (case-insensitive). `signalscope.backup*.py` also added to `.gitignore`.

---

## [3.5.107] - 2026-04-06

### Fixed
- **Morning Report always showing "All Clear"** (morning_report plugin v1.2.2) — `_BASE_DIR` pointed to the `plugins/` subdirectory. `metrics_history.db`, `alert_log.json`, and `sla_data.json` were therefore looked up in `plugins/` where they do not exist. All three data loaders check `os.path.exists()` before opening and silently return empty results, so every report contained zero faults and the "All Clear" headline was always chosen. Fix: added `_APP_DIR = os.path.dirname(_BASE_DIR)` (the parent app directory) and changed the three shared-data paths to use `_APP_DIR`. Plugin config files (`morning_report_cfg.json`, `morning_report_cache.json`) correctly remain under `_BASE_DIR` in `plugins/`.

---

## [3.5.106] - 2026-04-06

### Removed
- **Per-input Cascade Suppression** — the "Suppress if upstream stream is silent" dropdown and checkbox have been removed from the input edit form. The feature is superseded by Broadcast Chains, which identifies the first failed node in a signal path and suppresses downstream alerts properly. Existing configs that had `cascade_parent` / `cascade_suppress_alerts` set will deserialise cleanly (fields are retained in `InputConfig` for backwards compatibility) but the suppression logic no longer runs. The separate chain-level cascade suppression (suppress chain fault notifications when an upstream chain is also faulted) is unaffected.

---

## [3.5.105] - 2026-04-06

### Fixed
- AI Anomaly Detection help text: corrected learning phase duration from "5 minutes" to "24 hours" in both the Settings UI tooltip and the module docstring

---

## [3.5.104] - 2026-04-06

### Fixed — Logger "Remote site not responding" after client site-name change (logger v1.6.2)

**Root cause:** `hub_catalog_cache.json` persists the hub's merged Logger catalog across restarts. When a client node's site name is changed in Settings, the client re-registers with the hub under the new name. However, the old site name's catalog entries (loaded from disk cache at startup, or added earlier in the same session) remain in `_hub_logger_catalog[old_name]`. Because the catalog endpoint merges all sites with a first-slug-wins rule, the stream still appeared in the UI with `site = old_name`. All subsequent requests (`/api/logger/hub/days/<old_name>/...`) queued commands for `old_name`; the client (now polling as `new_name`) never picked them up, hitting the 20-attempt timeout: "Remote site not responding".

**Fix:** `api_logger_hub_register` now evicts incoming slugs from all other site entries in `_hub_logger_catalog` before inserting the new registration. A slug can only be live on one site; whichever site registers last with that slug is authoritative. Stale entries are logged and the empty old-site dict is removed entirely. The updated catalog snapshot is written to the cache immediately, so the fix persists across the next hub restart without any manual file deletion.

**If you hit this before upgrading:** delete `hub_catalog_cache.json` from the directory where `signalscope.py` lives and restart the hub. The client will re-register within 60 s and the correct site name will be used.

---

## [3.5.103] - 2026-04-06

### Added — Sync Capture: DAW Session export (plugin v1.0.18)

A new **💾 DAW Session** button appears on every capture row that has at least one clip. Clicking it downloads a ZIP containing:

- **All WAV clips** in a `clips/` subdirectory
- **`<label>_reaper.rpp`** — a REAPER project file; each clip becomes a separate track. If alignment has been run the stored source in-point offsets are baked in so every track is pre-aligned when you open the project.
- **`<label>_audition.sesx`** — an Adobe Audition multitrack session file in the same format, using `sourceInPoint` / `sourceOutPoint` attributes to position each clip at the correct in-point on the timeline.

**How alignment interacts with the export:** Running ⇌ Align now writes the computed `offsets`, `durations`, and `ref_filename` to the capture's DB record. The DAW export route reads these without recomputing. Captures exported before alignment is run receive zero offsets (clips land at the start of each track — user aligns manually in the DAW). Re-running alignment with a different reference overwrites the stored offsets; subsequent exports use the latest result.

**Format notes:**
- REAPER `.rpp` uses `SOFFS` at the item level to set the source in-point. All tracks sit at timeline position 0.
- Audition `.sesx` uses `sourceInPoint` / `sourceOutPoint` on each `audioClip`, also all at `start="0"` on the timeline.
- Both files reference clips via the relative path `clips/<filename>` — unzip into a single folder and open from there.

---

## [3.5.102] - 2026-04-06

### Changed — alert defaults tuned for broadcast (new inputs only; existing configs unchanged)

The previous defaults generated excessive alert noise on real broadcast installations. Changes affect newly created inputs only — existing saved configs are untouched.

| Setting | Old default | New default | Reason |
|---|---|---|---|
| `alert_on_hiss` | **on** | **off** | Music (cymbals, sibilance, hi-hats) triggers hiss detection constantly. Enable manually if monitoring silent-source inputs such as talkback or commentary circuits. |
| `ai_monitor` | **on** | **off** | Requires `pip install onnxruntime`. During learning-phase transitions and after programme-format changes it produces false positives. Better as an explicit opt-in once a stream is stable. |
| `silence_min_duration` | **3 s** | **10 s** | Broadcast stations have brief pauses between songs, news reads and idents. 3 s fired alerts on every natural gap; 10 s catches only genuine dead air. |
| `clip_window_seconds` | **2 s** | **10 s** | FM broadcast limiters/processors routinely clip at peaks by design. A 2 s window caught virtually every loud song. |
| `clip_count_threshold` | **3** | **10** | 3 clips in 2 s was trivially exceeded on any heavily compressed station. 10 clips in 10 s targets genuinely runaway clipping. |
| `clip_debounce_seconds` | **30 s** | **120 s** | Previously re-alarmed every 30 s throughout a loud track; 2 min debounce prevents repeated notifications. |
| `alert_on_hum` | **on** | **off** | Modern broadcast equipment rarely introduces mains hum. Enable if running long analogue tie lines or legacy gear. |
| `alert_on_dc_offset` | **on** | **off** | Slight DC offset is endemic in ADCs, codecs and broadcast kit — rarely actionable and difficult to fix remotely. |
| `overmod_clip_pct` | **20 %** | **30 %** | Broadcast limiting regularly pushes audio above −1 dBFS as part of loudness processing; 20 % triggered on heavily processed stations in normal operation. |

---

## [3.5.101] - 2026-04-06

### Fixed
- **Hub overview live bars and L/R not updating for streams with `+`, `&`, or other special characters in their name** (e.g., "BBC Radio 4+"): The Jinja2 template used a chain of `|replace(...)` calls to build element IDs (spaces, slashes, dots, dashes, parentheses), while the JavaScript `_liveKey()` / `_wLiveKey()` functions used a comprehensive regex `[^a-zA-Z0-9|]` → `_`. Any character not in the Jinja2 replace chain (e.g., `+`) was left in the Jinja2 ID but replaced by `_` in the JS key, causing `document.getElementById()` to silently return `null` — live bar fills and L/R bars would never update for those streams. Fix: registered a `safe_lkey` Jinja2 filter that applies the same regex as the JS functions. `HUB_TPL` and `HUB_WALL_TPL` now use `|safe_lkey` on the combined site|stream key, producing IDs that always match what JS looks for.
- **Hub site replica page — L/R bars not appearing for DAB stereo streams until page refresh**: The `.sc-lr-bar` div was inside `{% if _ll is not none and _lr is not none %}`, so if L/R data happened to be `None` at page render time (stream still starting, or race with the heartbeat), the element was never added to the DOM. The 150 ms live poll would find `lrWrap = null` and silently skip all L/R updates. Fix: `.sc-lr-bar` is now always rendered for stereo streams; starts with `display:none` when L/R data is absent and the JS live poll shows it (unchanged behaviour) when real L/R values arrive.

---

## [3.5.100] - 2026-04-06

### Fixed
- **DAB bulk-add ignores stereo checkbox**: when adding multiple DAB services at once via the mux browser, the "Enable stereo capture" checkbox was never read by `dabBulkAdd()` and was not sent to the server. All bulk-added inputs were silently created with `stereo=False`. Fix: `dabBulkAdd()` now reads `#inp_stereo_dab` and includes `stereo: true/false` in each service object sent to `/inputs/add_dab_bulk`. The server route passes the value through to `InputConfig`.

---

## [3.5.99] - 2026-04-06

### Fixed
- **DAB on Pi — rtl_tcp proxy starts but welle-cli never launches (thread hangs forever)**: `readline()` on rtl_tcp's stderr pipe blocks indefinitely once rtl_tcp finishes its startup output and begins listening. The 8-second `_ready_deadline` check only ran at the top of the while loop — it could never interrupt a blocking `readline()`. Additionally, rtl_tcp prints `"listening..."` to **stdout**, which was piped to `/dev/null`, so the string-match approach could never succeed regardless. Fix: stderr is now drained in a background thread (for logging/error detection only). Readiness is determined by attempting a TCP connection to `127.0.0.1:rtl_tcp_port` — when the connect succeeds, rtl_tcp is provably listening and welle-cli can connect. This is reliable regardless of how rtl_tcp routes its output.

---

## [3.5.98] - 2026-04-06

### Fixed
- **DAB on Pi — rtl_tcp proxy opening wrong device (`usb_claim_interface error -6`)**: Three compounding problems fixed:
  1. **rtl_tcp was only used for `device_idx > 0`**: if the DAB dongle resolved to device 0 but another process was using it, welle-cli would still try USB directly. Now rtl_tcp is used for **all** Pi DAB sessions regardless of device index — welle-cli never touches USB on Pi.
  2. **Stale `sdr_manager` cache sending rtl_tcp to wrong device**: the serial→index scan is cached for 10 s. When FM serial assignments change or the monitor restarts, the cached index can point to the wrong dongle (e.g., the FM dongle). Fix: force a fresh scan (`scan(force=True)`) immediately before launching rtl_tcp to get the current live mapping.
  3. **No stale rtl_tcp killer**: leftover rtl_tcp processes from previous crashes could hold a device. Added a stale rtl_tcp killer (analogous to the welle-cli stale killer) before each launch.
  - Also: rtl_tcp now retries once after 3 s if it gets `-6` on first attempt (handles the case where a previous FM process hasn't fully released the device yet).

---

## [3.5.97] - 2026-04-06

### Fixed
- **DAB on Raspberry Pi — audio takes many minutes to start (carousel prewarm)**: on Pi, `_start_dab_session` uses welle-cli's `-C N` carousel flag (N = number of consumers, typically 1). The prewarm was opening persistent connections to ALL services in the mux (e.g. 29 services × 52 s per service in the carousel = up to 25 minutes before the needed service got its turn). Fix: on Pi, the prewarm now waits up to 3 s for consumer threads to register their resolved SIDs (via the new `DabSharedSession.consumer_sids` set), then only warms those specific endpoints. welle-cli's carousel queue contains only the services actually needed, so they are activated immediately (typically ready within 15 s). On non-Pi (full parallel ensemble decode), all services are still pre-warmed as before.

---

## [3.5.96] - 2026-04-06

### Fixed
- **Client status page log — auto-scroll fights manual scroll**: the log box was unconditionally jumping to the bottom on every `/status.json` poll (~5 s interval), making it impossible to scroll up and read older entries. Fixed: auto-scroll now only fires if the user is already at (or within 40 px of) the bottom. If they've scrolled up the position is preserved across refreshes.

---

## [3.5.95] - 2026-04-06

### Fixed
- **DAB + FM on Raspberry Pi — `usb_claim_interface error -6` (definitive fix)**: Root cause conclusively confirmed: the apt-installed `welle-cli` on Raspberry Pi OS **always opens device 0** regardless of any device-selection arguments (`-F rtl_sdr,N`, `-D driver=rtlsdr,serial=X` — all ignored). When FM monitoring holds device 0, welle-cli for the DAB dongle (device 1) still attempts to open device 0, gets error -6, and fails — even though completely different physical dongles with different serials are configured. Proven by the user swapping the two dongles: DAB on device 0 + FM on device 1 worked; the reverse always failed.
  - **Fix**: on Raspberry Pi with `device_idx > 0`, `_start_dab_session` now launches `rtl_tcp -d DEVICE_IDX` first. `rtl_tcp` uses the standard `rtlsdr_open(index)` path which correctly opens the requested device by index (same mechanism as `rtl_fm -d 0`). `welle-cli` then connects via `-F rtl_tcp,127.0.0.1:PORT` and never touches USB directly — its broken device-selection is bypassed entirely.
  - `rtl_tcp` is started before `welle-cli`, with a brief ready-wait watching for "Listening" in stderr. On failure, falls back to direct `-F rtl_sdr,N` with a log warning.
  - `_stop_dab_session` now kills `rtl_tcp_proc` after `welle-cli` exits (killing it first would cause welle-cli to error during shutdown), then waits up to 3 s for clean exit.
  - `DabSharedSession` gains two new fields: `rtl_tcp_proc` (the `rtl_tcp` subprocess) and `rtl_tcp_port` (the chosen local port).
  - Device 0 on Pi (single-dongle or DAB on device 0) is unaffected — continues to use `-T -C N` direct access as before.

---

## [3.5.94] - 2026-04-06

### Fixed
- **DAB + FM on Pi — `usb_claim_interface error -6` (persistent, not just race)**: 3.5.93's stagger proved the timing wasn't the cause — error -6 persisted even 14 seconds after FM was fully running. Two compounding root causes fixed:
  1. **Stale welle-cli process not killed**: the stale-killer matched only `-F rtl_sdr,N` (old format). After the 3.5.92 change to `-D driver=rtlsdr,serial=X`, stale processes from previous monitor runs or the DAB Scanner plugin no longer matched the tag and were not killed. Updated to kill any welle-cli matching old `-F` format, new `-D serial=X` format, or `-c CHANNEL` (catches DAB Scanner leftovers regardless of device args).
  2. **`dvb_usb_rtl28xxu` kernel driver still bound**: before each welle-cli launch on Pi, SignalScope now proactively walks `/sys/bus/usb/devices/` to find the target dongle (by serial), checks each USB interface for a `dvb_usb_rtl28xxu` driver binding, and unbinds it via sysfs (direct write first, `sudo -n tee` fallback — same pattern as the existing usbfs_memory fix). If unbind fails (no sudo), a log message explains the fix (`blacklist dvb_usb_rtl28xxu`).

---

## [3.5.93] - 2026-04-06

### Fixed
- **DAB + FM on Pi — `usb_claim_interface error -6` root cause (timing race)**: The 3.5.92 fix confirmed correct device selection (welle-cli was reaching the right dongle), but error -6 persisted. The welle-cli log now showed `Airspy: airpsy_open failed` immediately before `usb_claim_interface error -6`, confirming welle-cli is trying the correct RTL-SDR device — but the Pi's DWC2 USB host controller rejects simultaneous `libusb_claim_interface` calls to different devices. rtl_fm's USB init (launch → "Sampling at...") takes ~1 s. When welle-cli launches at the same moment (same log second), both call `libusb_claim_interface` concurrently and one fails. Fix: on Raspberry Pi, if FM or scanner streams are active (`sdr_manager.status()` non-empty), `_start_dab_session` waits 3 s before launching welle-cli, ensuring rtl_fm's USB initialisation is complete first.

---

## [3.5.92] - 2026-04-06

### Fixed
- **DAB + FM on Pi — `usb_claim_interface error -6` when serials are different**: `_start_dab_session` was always using `-F rtl_sdr,N` (device index) to tell welle-cli which dongle to use. Some welle-cli builds silently ignore the `,N` device-index suffix and fall back to opening "first available" (device 0). When rtl_fm is already running on device 0, welle-cli then collides with it and gets error -6 — even though FM and DAB are configured with completely different serial numbers. Fix: on Raspberry Pi with a serial number configured, use `-D driver=rtlsdr,serial=SERIAL` instead of `-F rtl_sdr,N`. Serial-based selection is unambiguous and matches the approach already used by the DAB Scanner plugin. `-C` is omitted when `-D` is used (welle-cli rejects both together).

---

## [3.5.91] - 2026-04-06

### Fixed
- **DAB + FM on Pi — `usb_claim_interface error -6` cascade**: when a shared DAB mux session failed (e.g. at startup with FM also running on the same dongle), `_stop_dab_session` was calling `p.kill()` but never calling `p.wait()` after it. The 0.5 s sleep could complete while the killed process was still in the kernel's process table, still holding the USB interface claim. All 8 streams then retried simultaneously; the new welle-cli launch hit the same error -6 because the old process hadn't fully released the USB device.
  - Added `p.wait(timeout=3)` immediately after `p.kill()` — process exit is now confirmed before proceeding
  - Increased USB settle sleep from 0.5 s → 2.0 s after confirmed process exit
  - Increased the shared USB backoff from 3.0 s → 8.0 s to cover the worst-case shutdown path (terminate timeout 2 s + kill wait 3 s + settle 2 s + headroom)

---

## [3.5.90] - 2026-04-06

### Fixed
- **Plugin Manager navbar broken**: standalone hub pages that call `topnav()` need `display:inline-block` and `text-decoration:none` on `.btn` (topnav renders nav links as `<a class="btn ...">`) and a `.nav-active` class. Without these the nav links collapsed and had no active highlight. Added to `_HUB_PLUGINMGR_TPL` CSS; rule added to CLAUDE.md to prevent recurrence.

---

## [3.5.89] - 2026-04-06

### Fixed
- **Plugin Manager UX — remote action feedback**: clicking Update/Install/Remove on a client site now immediately shows a sticky "✓ Command queued for [site] — checking back in ~20 s" banner and replaces the cell with a blue ⏳ Pending badge. The matrix does not auto-refresh until 20 seconds later, giving the client time to heartbeat and apply the change.
- **Plugin Manager — restart state**: cells now distinguish between three installed states: green "v1.0.0 ✓" (running), amber "v1.0.0 ↻ Restart needed" (file on disk but not yet loaded — shown immediately after a remote install before the node restarts), and "—" (not installed). Hub-side installs still auto-refresh after 1.5 s.

---

## [3.5.88] - 2026-04-06

### Added
- **Hub Plugin Manager** (`/hub/plugins`) — built into SignalScope core, no plugin install required. Accessible from the Hub dropdown on any hub node.
  - **Matrix view**: rows = plugins (registry + anything installed anywhere), columns = Hub + every connected client. Each cell shows installed version, update-available badge, or "—" (not installed), with Install / Update / Remove buttons per cell.
  - **Registry panel**: all GitHub-listed plugins shown as cards with a site selector dropdown and one-click install to any site. Registry JSON cached for 5 minutes.
  - **Hub actions**: install/remove applied immediately to the hub's own `plugins/` directory with `py_compile` validation + atomic `os.replace`. Cache invalidated so the next heartbeat reports the updated list.
  - **Client actions**: `plugin_install` / `plugin_remove` commands queued via the existing `push_pending_command` / heartbeat ACK system — no extra poll thread, no extra plugin needed on the client.
- **Heartbeat `installed_plugins` field**: every client now includes a lightweight summary of its installed plugins in each heartbeat payload (cached 60 s). The hub reads this into `_sites[site]["installed_plugins"]` automatically.
- **Client command handlers** `plugin_install` / `plugin_remove`: download URL → `py_compile` validate → `os.replace` into `plugins/`; or `unlink` for remove. Both invalidate the heartbeat cache so the hub sees the change on the next heartbeat cycle.

---

## [Sync Capture plugin 1.0.17] - 2026-04-06

### Changed
- **First-time user onboarding**: 4-step "How it works" banner shown on first visit, dismissed to `localStorage`. Explains the workflow end-to-end in plain English.
- **Step badges**: ① and ② badges on Select Inputs and Capture Settings card headers to indicate order.
- **Inline tooltips**: Duration slider now has a `?` tooltip explaining the rolling buffer concept. Offset, score, LUFS, TP, and overlap badges all have richer plain-English tooltips (e.g. "broadcast standard is −23 LUFS", "values above −1 dBTP may cause distortion").
- **Hint text**: Short helper lines below the stream list, capture button, and storage card explaining what each section does and where files go.
- **Better empty state**: History table now shows an icon, a title, and step-by-step guidance rather than bare "No captures yet".
- **Progress card hint**: Added explanatory text below the progress rows ("Commands sent to all sites — clips upload automatically, usually 10–30 s").

---

## [Sync Capture plugin 1.0.16] - 2026-04-06

### Changed
- **Alignment panel layout**: each track is now two rows — top row has stream info + all pills, bottom row has the audio player + waveform spanning the full width. Eliminates the cramped right-push when many badges are present.
- **Level difference label**: replaced bare `±N dB` with plain-English `↑ N dB louder` / `↓ N dB quieter` / `≈ 0 dB` with a tooltip ("4.1 dB quieter than reference"). Badge is now blue-tinted (`.lvl-ld`) to stand out from the grey LUFS/TP pills. Reference track shows no level badge (it is the reference).

---

## [Sync Capture plugin 1.0.15] - 2026-04-06

### Fixed
- **Alignment failed: undefined is not an object**: `np.log10()` always returns numpy scalar types even when given Python floats. These are not JSON-serializable, causing `jsonify()` to raise a 500 on the async alignment poll endpoint, so `d.result` arrived as `undefined` in the browser. Fixed by wrapping all `np.log10()` calls with `float()` in `_analyse_lufs`, `_octave_bands`, `_stereo_analysis`, and `_compute_alignment`. Also wrapped FFT lag values with `float()` for the same reason.

---

## [Sync Capture plugin 1.0.14] - 2026-04-06

### Fixed
- **Upload 500 error**: `request.get_data(limit=...)` is not a valid Flask parameter — caused `TypeError` → HTTP 500 on every client clip upload. Replaced with `request.get_data()` (size cap enforced by the `Content-Length` pre-check and post-read comparison that were already in place).

---

## [Sync Capture plugin 1.0.13] - 2026-04-06

### Changed / Added
- **Bug fixes**: atomic DB writes (temp + `os.replace()`), upload size limit (`_MAX_UPLOAD_BYTES`), streaming Range-aware clip serve (no full RAM read), bounded processed-capture dedup set (500 entries, FIFO eviction), per-site pending-command queue (list not single slot), validate clip upload against capture's actual selections
- **Audio analysis**: EBU R128 integrated LUFS + true peak dBTP per clip (K-weighting via scipy if available, fallback unweighted); level difference dB vs reference over aligned overlap; sub-sample lag interpolation via parabolic fit around FFT peak; octave-band spectrum (63–16 kHz, 9 bands, bar chart canvas); stereo L/R analysis (L/R dBFS, balance dB, L/R correlation) for stereo clips
- **Broadcast features**: RDS/DLS metadata snapshot at capture time stored in clip record; BWF export endpoint (`/bwf` suffix) with `bext` chunk containing originator, timecode, LUFS
- **UX**: live countdown timer in progress panel; per-clip download (WAV) and BWF download buttons; reference clip selector with re-align; pagination + search on capture history (`?offset&limit&q`); label pre-fill with current HH:MM timestamp
- **Architecture**: async alignment (POST `align_async` + GET `align_result` polling, 202 response); single heapq scheduler thread replaces one-thread-per-capture pattern; JS `Map` cache replaces `data-cap` DOM attribute for cap objects; `ResizeObserver` on waveform canvases replaces global `window.resize` listener; configurable clip storage path (`synccap_cfg.json`) + disk usage badge in header

---

## [3.5.87] - 2026-04-06

### Fixed
- **DAB startup slow (52 s per service) — root cause found and fixed**: The `-C` flag was a red herring. The real problem was two bugs in the consumer-side probe and prewarm:
  1. **Prewarm `timeout=30 s`** was shorter than the 52-s per-service encoder startup time. Every prewarm connection timed out and disconnected before any encoder was ready, making the prewarm do nothing.
  2. **Probe cycling every ~5.5 s** (5 s socket timeout + 0.5 s sleep, from the previous fix) caused constant disconnect/reconnect churn. Each reconnect re-queued the service at the back of welle-cli's internal encoder queue, preventing any service from getting ahead in the queue. This produced the classic 52-s-interval pattern regardless of the `-C` value.
  - **Fix**: Prewarm timeout increased to 70 s (> 52 s startup) with 600 s total hold time, keeping all service connections alive while welle-cli works through the queue. Probe changed to a **persistent single connection** with 70 s per-read timeout — no more disconnect churn. `ready_deadline` extended to 660 s (11 min, covers 12 services × 52 s). Socket timeout exceptions during the wait are no longer logged as errors.

---

## [3.5.86] - 2026-04-06

### Fixed
- **DAB startup still sequential 52 s per service on non-Pi hardware**: `-C 20` (added in 3.5.16 under the incorrect belief it forces parallel decoding) produces the same 52 s / 104 s / 156 s carousel as `-C 1` — welle-cli's `-C N` is always a per-service carousel regardless of N vs service count. Fix: **removed `-C` entirely from the non-Pi `_start_dab_session` command**. Without `-C`, welle-cli decodes the full ensemble simultaneously — all services ready in ~10–15 s. Pi path (`-T -C N`) unchanged.

---

## [3.5.85] - 2026-04-05

### Fixed
- **Unhandled heartbeat commands now logged**: Unknown command types that aren't handled by any built-in handler or registered plugin handler are now logged on the client with a clear message, making it easier to diagnose delivery failures.

---

## [Sync Capture plugin 1.0.12] - 2026-04-06

### Fixed
- **Correlation scores wrong for FM vs DAB comparisons**: Raw Pearson r on PCM samples is destroyed by the processing differences between paths — FM de-emphasis, DAB MPEG/AAC codec quantisation, broadcast limiting and EQ all change every individual sample even when the content is identical. A perfectly-aligned FM vs DAB pair was scoring ~43%. Switched to **RMS envelope correlation**: compute RMS over 50 ms blocks (the loudness contour) then correlate those vectors. The loudness envelope is invariant to codec, EQ and dynamics differences — same programme on FM and DAB produce the same contour. Typical scores now: same content cross-path (FM/DAB/unprocessed) → 80–97%, different content → 15–40%.
- Updated score thresholds to match envelope-score ranges: ≥80% Excellent (green), ≥60% Good (blue), ≥40% Fair (amber), <40% Poor (red).
- Analysis window uses middle 15 s of overlap (was 10 s), trimmed symmetrically to avoid DAB startup transients and FM fade-in at the edges.

---

## [Sync Capture plugin 1.0.11] - 2026-04-06

### Added
- **Correlation scores**: after alignment, each clip receives a Pearson r score computed on the middle 10 s of the aligned overlap window (mean-centred, unit-normalised). The reference clip is always 100%; others show a colour-coded percentage — green ≥88 % (Excellent), blue ≥70 % (Good), amber ≥50 % (Fair), red <50 % (Poor). Scores below 90 % are expected and normal when comparing FM processed vs DAB vs unprocessed feeds, which have different loudness curves, limiting and EQ.
- **Per-clip RMS waveform thumbnails**: each track in the align panel shows a 600-point RMS envelope canvas below its audio player, colour-coded per stream.
- **📊 Compare Waveforms button**: opens an overlay canvas showing all clips' aligned overlap waveforms plotted on a shared timeline with a colour legend, making it easy to spot timing differences and level mismatches visually.

---

## [Sync Capture plugin 1.0.10] - 2026-04-05

### Added
- **Clip alignment via FFT cross-correlation**: Completed captures with 2 or more clips now show an "⇌ Align" button in the clips panel. Clicking it calls `GET /api/synccap/align/<capture_id>` which loads each clip as mono float32, cross-correlates the first 10 s of each against the reference clip, and returns per-clip playback offsets (in seconds). The UI renders a multi-track align panel showing each clip with its offset badge (`+X.XXX s`), a shared overlap duration indicator, and `▶ Play All Aligned` / `■ Stop All` buttons. Play All sets each `<audio>` element's `currentTime` to its offset and calls `play()` simultaneously — giving synchronized playback without any server-side audio processing. Works across clips from different client sites (FM, DAB, IP) sharing the same programme content, handling path latency differences up to ≈5 s.

### Fixed
- **Stereo `_audio_buffer` tail-filtering**: `_capture_input` now scans the stereo `_audio_buffer` from the tail and discards any old mono-sized chunks (produced before FM pilot tone lock-on). Only contiguous same-sized stereo chunks are used. Falls back to mono `_stream_buffer` if the stereo tail covers less than 90 % of the requested duration — guaranteeing full-length clips.

---

## [Sync Capture plugin 1.0.9] - 2026-04-05

### Changed
- **Stereo captures**: Stereo inputs (`_audio_channels == 2` — FM, DAB, HTTP, RTP) now capture from `_audio_buffer` which holds the real interleaved L/R float32 data. Mono inputs continue to use `_stream_buffer` (60 s rolling). Stereo slicing uses frame-based indexing (reshape → slice → flatten) to avoid mid-frame cuts. Falls back to mono `_stream_buffer` if `_audio_buffer` is empty.

---

## [Sync Capture plugin 1.0.8] - 2026-04-05

### Fixed
- **All clips playing at double speed**: `_capture_input` used `_audio_channels` for the WAV channel count. For stereo inputs (FM, DAB) `_audio_channels` is 2, making the WAV header claim stereo — but `_stream_buffer` is always mono regardless of input type (stereo inputs push a mono mix to `_stream_buffer` and keep interleaved stereo in `_audio_buffer`). A mono-data WAV labelled as stereo plays at double speed. Fix: always write `n_ch=1`, matching what `_save_alert_wav` does when given `_stream_buffer` chunks (`_n_ch = 1 if _chunks is not None else ...`).

---

## [Sync Capture plugin 1.0.7] - 2026-04-05

### Changed
- **All inputs always upload as WAV**: ffmpeg MP3 encoding adds latency for all input types (FM and DAB both affected). Removed MP3 compression entirely — all captures upload as WAV. nginx is configured for large uploads so there is no benefit to compressing.

---

## [Sync Capture plugin 1.0.6] - 2026-04-05

### Changed
- **FM inputs always upload as WAV**: FM (`fm://`) inputs now always send WAV. Superseded by v1.0.7 which removes MP3 for all input types.

---

## [Sync Capture plugin 1.0.5] - 2026-04-05

### Fixed
- **Clips panel invisible after capture**: The `.clips-panel` CSS class had `display:none` applied to the `<td>` element itself. When clicking a history row to expand it, the parent `<tr>` became visible but the `<td>` inside remained hidden. Removed `display:none` from the CSS class — the `<tr>` already starts with `style="display:none"` and is the correct visibility gate.
- **Clips don't auto-expand after capture completes**: After a capture finishes, `loadCaptures()` re-rendered the history table but left all rows collapsed. Now passes the just-completed `capture_id` to `loadCaptures()` which auto-expands that row so clips are immediately visible.

---

## [Sync Capture plugin 1.0.4] - 2026-04-05

### Fixed
- **Captures never delivered — root cause: `hub_server is None` always False**: `hub_server` in the plugin ctx is always a `HubServer()` instance (never `None`), so the `if hub_server is None:` check used in v1.0.3 to detect client nodes always evaluated to False. Client nodes were registering hub routes instead of the heartbeat capture handler — the `synccap_capture` command arrived via heartbeat but had no handler registered, and was silently dropped. Fix: replaced `hub_server is None` with a mode-based check (`mode == "client" and bool(hub_url)`), matching the SignalScope convention for detecting client nodes. **This is the root cause of every version of synccap failing to deliver captures.**
- **Poll fallback re-added for belt-and-braces**: The hub now also populates `_pending_cmds[site]` when triggering a capture. A `/api/synccap/cmd` poll endpoint (removed in v1.0.3) is re-added on the hub. Client nodes on cores older than 3.5.84 (where `register_cmd_handler` is unavailable) fall back to polling this endpoint every 3 s. Both delivery paths include deduplication via `_processed_captures`.
- **`capture_at` window increased from +5 s to +15 s**: Heartbeat cycle is ~10 s; 5 s was too short for the command to arrive before the capture window closed. Now +15 s, giving at least one full heartbeat cycle of headroom.

---

## [Sync Capture plugin 1.0.3] - 2026-04-05

### Fixed
- **Captures never delivered to clients — redesigned to use heartbeat commands**: The custom `/api/synccap/cmd` poll endpoint required synccap to be independently installed and running on every client node, and its poller errors were invisible. Replaced entirely with the standard heartbeat command mechanism (`hub_server.push_pending_command` → heartbeat ACK → `monitor._plugin_cmd_handlers`). The hub now pushes `{"type": "synccap_capture", "payload": {...}}` into the site's existing command queue; the client's synccap plugin registers a handler via `ctx["register_cmd_handler"]` that fires when the command arrives in the next heartbeat cycle (~10 s). No separate poll thread, no custom route, no silent failures. Requires SignalScope 3.5.84+.

---

## [Sync Capture plugin 1.0.2] - 2026-04-05

### Fixed
- **Client poller silent failures — root cause of captures never arriving**: The client polling thread had `except Exception: pass` with no logging at all. Any error (SSL, 403, wrong URL, hub not having synccap installed) was silently swallowed every 2 seconds with no trace in the log. Added startup log message `[SyncCap] Client poller started`, error logging that fires when the error message changes (rate-limited to avoid spam), and a recovery message when the hub becomes reachable again. Also stripped trailing slash from `hub_url` before building the command poll URL (a trailing slash would produce a double-slash path, routing to 404 on some proxies).

---

## [Sync Capture plugin 1.0.1] - 2026-04-05

### Fixed
- **Captures always expired with no clips**: Two bugs prevented client audio from reaching the hub. (1) PCM scaling: `_stream_buffer` holds float32 in [−1.0, 1.0]; converting directly to int16 produced values of 0 or ±1 (silence). Fix: scale by 32767 before the cast (`(arr * 32767).astype(np.int16)`). (2) Silent error handling: `except Exception: pass` in `_handle_capture_cmd` swallowed all upload errors with no log output; `if not inp: continue` silently discarded streams not found on the client. All error paths now call `monitor.log()` with the specific failure reason (stream not found, buffer empty, upload exception). Capture receipt and upload success are also logged.

---

## [Sync Capture plugin 1.0.0] - 2026-04-05

### Added
- **Sync Capture plugin** (`synccap.py`): Multi-site synchronized audio capture. Hub page shows all inputs from every connected site grouped by site name; tick any combination, set a label and duration (5–300 s), press Capture. The hub sends a `capture_at` timestamp (now + 5 s) to each client via a lightweight 2-second poll; each client waits until that moment, grabs the last N seconds from its 60-second rolling `_stream_buffer`, compresses to MP3 (via ffmpeg, if available) when the WAV exceeds ~200 KB, and uploads to the hub. Hub-local inputs are captured in-process at the same timestamp with no round-trip. All clips for a session are stored under `plugins/synccap_clips/` and presented together in an expandable row with inline `<audio>` players for side-by-side comparison. Stereo clips are labelled with a STEREO badge. Captures expire after 3 minutes if not all expected clips have arrived. Hub-only plugin.

---

## [3.5.84] - 2026-04-05

### Added
- **Plugin heartbeat command handler registry**: Plugins can now register handlers for custom hub→client command types delivered via the standard heartbeat ACK, without requiring changes to `signalscope.py` per plugin and without a separate poll endpoint. `ctx["register_cmd_handler"](cmd_type, fn)` registers `fn(payload)` to be called on the client when a command of that type arrives in a heartbeat response. Hub side uses `hub_server.push_pending_command(site, {"type": "...", "payload": {...}})`. Unknown command types fall through to `monitor._plugin_cmd_handlers` after all built-in types are checked.

---

## [3.5.83] - 2026-04-05

### Fixed
- **DAB stereo/mono detection — proper fix for 3.5.75 regression**: The original 3.5.75 problem (mono services appearing as stereo) was real and needed fixing. The real bug in 3.5.75 was not the logic but the **default value**: `_dab_stereo` defaulted to `False`, meaning every service appeared mono until mux metadata arrived — permanently for services where welle-cli omits or gives an unexpected value for `channels`. Attempts in 3.5.80 and 3.5.82 to work around this introduced further complexity that failed for the same underlying reason. Proper fix: (1) `_dab_stereo` now defaults to `True` (assume stereo). (2) `_copy_dab_metrics_from_mux` only updates `_dab_stereo` when the mode string is populated, and uses the mode string alone (not the unreliable `channels` field) — `False` only when mode explicitly contains "mono". (3) Branch condition is the clean `cfg._dab_stereo` form restored. Result: stereo services always show L/R; mono services (welle-cli MP2 mode-string "Mono") collapse to mono once metadata arrives.

---

## [3.5.82] - 2026-04-05

### Fixed
- **DAB stereo L/R bars still absent after 3.5.80 fix**: The 3.5.80 `_meta_ready`/`_svc_is_mono` approach failed because `_dab_stereo` was evaluated against `svc.get("channels", 1)` from welle-cli's mux.json. When welle-cli omits the `channels` key (or reports it as 0), the default of 1 gives `1 > 1 = False` → `_dab_stereo = False`. Once `_dab_mode` became non-empty (metadata arrived), `_svc_is_mono = True` for every service regardless of actual stereo content, permanently blocking the stereo path. Fix: reverted the stereo branch to the pre-3.5.75 behaviour — `cfg.stereo` (the user's explicit checkbox) is the sole gate. `_dab_stereo` from mux metadata remains populated for informational display but no longer blocks the processing path.

---

## [3.5.81] - 2026-04-05

### Fixed
- **Hub connection broken / `/status.json` 500 for clients with any non-FM input (3.5.76 regression)**: `_fm_stereo_blend` was added to the heartbeat payload and `inp_dict` in 3.5.76 (`round(inp._fm_stereo_blend, 3)`) but was never added as a default field in `InputConfig`. For FM inputs the FM monitoring loop sets it via `getattr(cfg, "_fm_stereo_blend", 0.0)` at startup. For every other input type (DAB, ALSA, HTTP, RTP) the attribute is never created. Accessing it raised `AttributeError` in both the heartbeat builder and `status_json`, causing: (1) every heartbeat POST to the hub to fail → hub connection appears broken; (2) `/status.json` to return HTTP 500. Fix: add `_fm_stereo_blend: float = field(default=0.0, init=False, repr=False)` to `InputConfig`.

---

## [3.5.80] - 2026-04-05

### Fixed
- **DAB stereo L/R bars broken for all inputs (3.5.75 regression)**: The 3.5.75 mono-service fix added `and cfg._dab_stereo` to the stereo branch condition. `_dab_stereo` defaults to `False` and is only populated from mux.json every 10 seconds. This meant every DAB input with stereo enabled fell into the new `elif` branch (the mono fallback) until metadata arrived — and permanently for any service where welle-cli's `channels` field was absent or reported as 1. The `elif` sets `cfg._audio_channels = 1`, causing `level_dbfs_l` / `level_dbfs_r` to be reported as `null`, blanking the L/R bars on all DAB inputs. Fix: the mono fallback now only fires when `_dab_mode` is populated (metadata has been received) AND it confirms the service is mono (`_dab_stereo = False`). When metadata hasn't arrived yet, the stereo path is used — matching the pre-3.5.75 behaviour and ensuring L/R bars are always shown for stereo-configured inputs.

## [3.5.79] - 2026-04-05

### Fixed
- **FM audio broken by wbfm revert (3.5.78 regression)**: `rtl_fm -M wbfm` forces a hardcoded 32 kHz output rate regardless of the `-s` flag. The Python pipeline expects 171 kHz samples, so every block read the wrong number of bytes, resampling ratios were invalid, and the output was a glitchy mess with no RDS. Reverted to `-M fm -s 171000`. The original `-M fm` analysis in 3.5.78 was incorrect — the pilot SNR meter was already detecting the 19 kHz pilot, proving the MPX composite was always present in the 171 kHz output; `-M fm` at that sample rate outputs the raw FM discriminator without any 15 kHz cap. The `-A std` flag remains removed (de-emphasis at 19 kHz attenuates the pilot by ~16 dB, corrupting subcarrier reconstruction).
- **FM stereo L/R imbalance — pilot amplitude normalisation**: The real cause of the systematic L/R imbalance was `pilot_peak = np.max(np.abs(pilot))` using an instantaneous per-block peak estimate. Under noise, `np.max` is elevated above the true pilot amplitude, making `pilot_n = pilot / pilot_peak` have amplitude < 1. Then `sub38 = 2·pilot_n² − 1` is `2A²cos²(θ) − 1 = (A²−1) + A²·cos(2θ)` instead of `cos(2θ)` — the `(A²−1)` DC offset leaks L+R content into `lmr` asymmetrically, producing L/R level differences that vary with signal content and appear on every station. Fix: replaced `np.max` with a slow EMA of `√2 × RMS(pilot)` (α = 0.05, τ ≈ 20 blocks ≈ 2 s). For a pure sine, `√2 × RMS = peak`, so the normalisation target is identical under clean conditions but is stable against noise spikes.

## [3.5.78] - 2026-04-05

### Fixed
- **FM stereo systematic L/R imbalance**: rtl_fm was launched with `-M fm` (standard mono FM demodulation) — incorrect analysis, see 3.5.79. With no L-R signal present, the pilot demodulation in `_mpx_to_stereo` produced only noise, and `lmr_scaled = lmr × 2` added/subtracted that noise asymmetrically to the L and R channels — causing a systematic and content-dependent apparent imbalance that was identical on every station but not present on a real FM receiver. Fix: changed to `-M wbfm` (wideband FM), which outputs the raw FM discriminator signal at the full 171 kHz MPX composite rate including the pilot tone (19 kHz), the L-R DSB-SC sidebands (23–53 kHz), and RDS (57 kHz). The `-A std` de-emphasis flag has also been removed — applying rtl_fm's audio de-emphasis to the MPX composite would roll off the pilot and L-R subcarrier content, again destroying stereo separation. De-emphasis is correctly applied in Python via `_apply_deemph` after stereo decoding.

## [3.5.77] - 2026-04-05

### Added
- **Over-compression detection** (`alert_on_over_compression`, off by default): Tracks the EMA-smoothed crest factor (peak − RMS dB). When it drops below `over_compression_crest_db` (default 6 dB) for `over_compression_min_duration` (default 120 s), fires `OVER_COMPRESSION`. Catches heavy brickwall limiting or processing chains left too hot — normal programme audio typically has 8–18 dB of crest. EMA resets during silence so re-entry establishes a fresh baseline.
- **Unexpected tone detection** (`alert_on_tone`, off by default): Detects a sustained pure or near-pure tone standing well above the surrounding spectral noise floor. Uses a local SNR approach: compares the peak FFT bin to the median of the surrounding ±500 Hz band (excluding the peak itself). Fires `TONE_DETECT` when the local SNR exceeds `tone_snr_db` (default 30 dB) for `tone_min_duration` (default 10 s). Catches test tones left on air, DTMF bleed, alignment carriers. Configurable frequency range (`tone_min_hz` / `tone_max_hz`). Shares the existing FFT block — no extra compute when hiss or hum detection is also enabled.
- **HF content loss / bandwidth narrowing** (`alert_on_hf_loss`, off by default): Compares the ratio of high-frequency energy (6–16 kHz) to mid-frequency energy (300–6 kHz) against a learned slow EMA baseline (tau ~10 min). Fires `HF_LOSS` when the ratio drops more than `hf_loss_threshold_db` (default 15 dB) below baseline for `hf_loss_min_duration` (default 30 s). Catches telephone-quality feeds accidentally sent to air, codec degradation, or a low-pass fault in processing. Requires ~10 minutes of audio to establish a baseline; warmup counter resets during silence.
- **Dead channel detection** (`alert_on_dead_channel`, on by default): For stereo streams, fires `DEAD_CHANNEL` when one channel drops to or below the silence threshold while the other remains active — indicating a broken cable, failed interface card, or routing fault. Configurable `dead_channel_min_duration` (default 10 s). Distinct from Stereo Imbalance (which requires both channels to be above the silence threshold and computes a dB difference).
- `OVER_COMPRESSION`, `TONE_DETECT`, `HF_LOSS`, `DEAD_CHANNEL` added to Reports `_SILENCE_TYPES` filter.

## [3.5.76] - 2026-04-05

### Added
- **Stereo decoder blend % display on FM input cards**: The FM / RDS stats block now shows a "Blend" row (client status page, hub watch view) indicating what percentage of full L/R stereo separation the decoder is applying. 100% = pilot SNR is strong and full stereo is active. Below 100% = marginal pilot, the decoder is fading toward mono to reduce noise, which can cause uneven L/R noise levels. Colour-coded: green ≥95%, amber ≥50%, red below 50%.
- **`lr_balance` metric** (L − R in dB, signed): Logged to the metrics database for all stereo streams (FM, DAB, ALSA, RTP, HTTP). Positive = L louder, negative = R louder. Available in the hub watch view metrics chart dropdown as "L/R Balance dB". Allows you to graph imbalance over time to distinguish persistent broadcast offsets from momentary content panning.
- **`fm_stereo_blend` metric**: Pilot blend fraction (0.0–1.0) logged to metrics for all FM streams. Available in the chart dropdown as "FM Stereo Blend %". Graph this alongside L/R balance to determine whether imbalance correlates with weak-pilot conditions (decoder issue) or persists at 100% blend (broadcast issue).

## [3.5.75] - 2026-04-05

### Fixed
- **DAB mono service incorrectly flagged as stereo**: welle-cli reports `channels: 2` in mux.json for some mono services (e.g. MP2 streams at 80 kbit/s — the codec uses a stereo frame container even for mono content). The mode string (e.g. "MPEG 1.0 Layer II, 48 kHz Mono @ 80 kbit/s") correctly indicates mono but was previously ignored. Fix: `_dab_stereo` is now set to `False` whenever the mode string contains "mono", regardless of the `channels` field. Additionally, if ffmpeg was launched in 2-channel mode for such a service, the dual-mono PCM is correctly downsampled to true mono (taking the L channel) before analysis, so L/R bars no longer show identical levels and `_audio_channels` is correctly reported as 1.

---

## [3.5.74] - 2026-04-05

### Added
- **Level drift detection** (`alert_on_level_drift`, off by default): Compares a 1-minute fast EMA against a 10-minute slow EMA. When the stream's mean level has quietly shifted by more than `level_drift_db` (default 8 dB) for `level_drift_min_duration` (default 60 s), fires `LEVEL_DRIFT` alert. Catches transmitter gain loss, AGC failure, accidental fader movement. EMAs reset automatically when the stream goes silent to avoid false alarms after pauses.
- **Sustained overmodulation** (`alert_on_overmod`, on by default): Tracks an exponential rolling fraction of clipping chunks over a `overmod_window_seconds` (default 60 s) window. Fires `OVERMOD` when the fraction exceeds `overmod_clip_pct` (default 20%). Distinct from the burst CLIP alert — catches a mixer or processing chain left chronically too hot.
- **Mono-on-stereo detection** (`alert_on_mono_on_stereo`, off by default): For stereo streams, derives approximate L-R cross-correlation mathematically from existing per-channel level readings using `r = (4·P_sum − P_L − P_R) / (2·√(P_L·P_R))`. Fires `MONO_ON_STEREO` when correlation ≥ `mono_on_stereo_corr` (default 0.98) for ≥ `mono_on_stereo_min_duration` (default 60 s). No raw stereo samples needed.
- **Stereo L/R imbalance** (`alert_on_stereo_imbalance`, on by default): For stereo streams, fires `STEREO_IMBALANCE` when |L − R| ≥ `stereo_imbalance_db` (default 6 dB) persists for ≥ `stereo_imbalance_min_duration` (default 30 s). Both channels must individually be above the silence threshold for the check to activate.
- `LEVEL_DRIFT`, `OVERMOD`, `MONO_ON_STEREO`, `STEREO_IMBALANCE` added to Reports `_SILENCE_TYPES` filter.

### Fixed
- **Hiss false alarm during rapid level transitions**: Before firing a HISS alert, the detector now checks if the audio level changed by more than 8 dB in the last 5 seconds. Level transitions shift spectral balance and can cause the HF energy ratio to spike transiently. The alert is suppressed and the counter reset while the level is unstable.

---

## [3.5.73] - 2026-04-05

### Added
- **Mains hum detection (50/60 Hz)**: New `alert_on_hum` check detects mains interference at 50 Hz, 60 Hz, 100 Hz, and 120 Hz. Uses the existing spectral FFT (shared with Hiss detection — no extra compute cost). Compares hum-band energy against the surrounding local noise floor (self-normalising — no baseline learning required). Fires `MAINS_HUM` alert with type, frequency family, and SNR in the message. Configurable threshold and minimum duration. Enabled by default.
- **DC offset detection**: New `alert_on_dc_offset` check tracks an exponential moving average of the raw PCM sample mean. A persistent non-zero mean (faulty ADC, DC-coupled input, capacitor failure) fires `DC_OFFSET` alert with percentage bias and duration. Configurable threshold (% of full scale) and minimum duration. Enabled by default; suppressed on silent streams.
- **Phase reversal detection (stereo streams)**: New `alert_on_phase_reversal` check detects when L and R channels are wired 180° out of phase. L+R cancellation makes the mono mix significantly quieter than either individual channel. Fires `PHASE_REVERSAL` alert with the measured dB of cancellation. Configurable mono-drop threshold and minimum duration. Stereo-only — automatically inactive on mono inputs.
- **`MAINS_HUM`, `DC_OFFSET`, `PHASE_REVERSAL`** added to the Reports type filter `_SILENCE_TYPES` set so they always appear in the Type dropdown regardless of event window.

### Fixed
- **Silence detection hysteresis**: The silence counter reset to 0 the instant level crossed back above `silence_threshold_dbfs`, causing rapid on/off flapping when audio hovered near the boundary. Added `silence_recover_db` field (default 4 dB): silence now only clears when `lev ≥ threshold + recover_db`. The silence recovery clip path also uses the same hysteresis threshold. Configurable per-input in Settings → Monitoring & Alerts.
- **Clip detection debounce**: After a CLIP alert fires, the clip event counter continued accumulating, potentially re-firing as soon as `ALERT_COOLDOWN` expired. Added `clip_debounce_seconds` field (default 30 s): clips are not counted at all during the debounce window. This prevents repeated alert floods during a loud passage that is genuinely clipping throughout. Configurable per-input.

---

## [3.5.72] - 2026-04-05

### Fixed
- **Glitch detection too sensitive — firing on natural audio dynamics**: Multiple compounding issues caused false positives on quiet passages, song fades, and normal broadcast content:
  1. **`glitch_drop_db` default was 18 dB** — a song fade or quiet voiceover easily crosses 18 dB below the 60 s rolling mean. Raised to **30 dB** — the dip must be severe, not just a quiet moment.
  2. **Form save fallback for `glitch_max_seconds` was 8.0 s** — an 8-second dropout is a silence event, not a glitch. Lowered to **1.5 s**. True packet-loss/STL glitches are < 1 s.
  3. **Form save fallback for `glitch_min_drop_rate_dbfs_s` was 12 dBFS/s** — fades and breath pauses hit 12 dBFS/s easily. Raised to **40 dBFS/s** — both onset AND recovery must be abrupt.
  4. **Form save fallback for `glitch_floor_db` was 0 (disabled)** — any depth dip counted. Set to **8 dB** — the dropout must reach within 8 dB of the silence threshold (near-silent).
  5. **Form save fallback for `glitch_pre_trend_db` was 0 (disabled)** — fade rejection was off by default. Set to **4 dB** — rejects dips where the level was already declining.
  6. **No minimum duration** — a single 10 ms measurement spike could register as a glitch. Added `glitch_min_seconds = 0.05 s` (50 ms) — network/codec glitches are ≥ 50 ms; sub-50 ms "dips" are measurement noise.

  **⚠ Existing installations**: these defaults only apply to newly-saved input configs. To get the tighter settings, open each input in Settings → Inputs → Edit, check the Glitch Detection values match the new defaults, and save.

---

## [3.5.71] - 2026-04-05

### Fixed
- **Role change not taking effect until re-login**: When an admin changed a user's role (e.g. "presenter" → "admin"), the `login_required` decorator's live-refresh block updated `allowed_sites/plugins/chains` from the user store but never updated `session["role"]`. So `_current_user_role()` still returned the old role on the next request, causing the user to be redirected to the plugin page (e.g. `/producer`) even though their role had been promoted. Fixed by adding `session["role"] = _ua.role` to the refresh block — role changes now take effect on the very next page load without requiring the user to log out.

---

## [3.5.70] - 2026-04-05

### Fixed
- **About page — broken topnav header**: The `/about` page `<style>` block was missing `.btn`, `.bg`, `.bp`, `.bs`, `.bd`, `.nav-active`, and `header` CSS rules that the topnav nav buttons depend on. Every other page template defines these; the about page didn't, so the nav bar appeared unstyled. Also scoped `a` link styles to `main a` to avoid overriding nav link colours, and renamed `.badge` to `.about-badge` to avoid colliding with the topnav's own `.badge` class.

---

## [3.5.69] - 2026-04-05

### Fixed
- **Producer view — Sign out button does nothing**: The presenter plugin's `_PRODUCER_TPL` has its own custom header (no `topnav()` call), so `_ssConfirm` — which is only injected by `topnav()` — was undefined on the page. The click handler called `e.preventDefault()` (suppressing the `href="/logout"` fallback) then threw an uncaught `ReferenceError` on `_ssConfirm`, leaving the user stuck. Fixed by replacing `_ssConfirm(...)` with a native `window.confirm()` call which works without topnav.

---

## [3.5.68] - 2026-04-05

### Added
- **`/about` page**: New page accessible from the topnav "ℹ About" link. Shows the SignalScope branding and tagline ("Broadcast signal intelligence"), three live stat cards (streams monitored, active plugins, uptime), a System card (build, mode, site name, running since, live health status via `/api/health`), and an Author card (Conor Ewings, conor.ewings@gmail.com, GitHub link, MIT licence). Links to GitHub star/issue pages and the privacy policy in the footer.
- **Page subtitles**: One-sentence descriptions added to three main pages so first-time visitors immediately understand what each section does:
  - Hub Dashboard — *"Live signal monitoring across all connected sites"*
  - Alert Reports — *"Alert history, fault analysis and clip review"*
  - Broadcast Chains — *"Automated fault detection and failover monitoring for broadcast signal chains"*

---

## [3.5.67] - 2026-04-05

### Added
- **`/api/health` — public endpoint**: Removed `@login_required` from the health check endpoint. Unauthenticated callers (UptimeRobot, Nagios, nginx health checks, etc.) now receive a minimal `{"status","build","ts"}` response with the correct HTTP code (200 ok / 503 degraded|error). Authenticated callers still get the full subsystem detail used by the dashboard.
- **Mobile API rate limiting**: `mobile_api_required` now shares the `LoginLimiter` with the web login form. Failed token attempts are counted per IP; the same `login_max_attempts` / `login_lockout_mins` settings apply. Returns HTTP 429 with `retry_after` when locked. Successful authentication clears the counter.
- **Startup topnav JS validation**: `_validate_topnav_js()` runs at startup, generates the topnav `<script>` block inside a test request context, and scans for the adjacent-string-literal pattern (`[A-Za-z0-9_;}\])]""`) that caused the 3.5.65/3.5.66 regressions. Prints `[OK]` on pass, `[!!] FAILED` with sample matches on fail — catches the bug before any request is served.

---

## [3.5.66] - 2026-04-05

### Fixed
- **Save Chain still broken**: Three more adjacent-string-literal JS syntax errors in `topnav()` — `bd.style.cssText`, `box.style.cssText` (both in `_ssConfirm`) had CSS strings split across Python string boundaries without `+`. Same root cause as 3.5.65. All occurrences in the topnav script are now fixed.

---

## [3.5.65] - 2026-04-05

### Fixed
- **Save Chain button does nothing (root cause)**: The `topnav()` script that defines `_ssToast`, `_ssConfirm`, `_btnLoad`, and `_btnReset` had a JS syntax error (`Unexpected string`) caused by Python implicit string concatenation producing adjacent JS string literals with no `+` operator between them. The browser silently refused to execute the entire script block, leaving all four utility functions undefined. `saveChain()` ran but crashed in the `catch` block calling `_btnReset(undefined)` — the error was swallowed silently. Three locations fixed in `topnav()`: `t.style.cssText` continuation lines, and both `_ssc_no`/`_ssc_yes` button `style` attributes split across Python string boundaries. Added defensive `typeof` guards in `saveChain`'s catch block.

---

## [3.5.64] - 2026-04-05

### Fixed
- **Chain builder — Save Chain does nothing**: All error feedback was written to a tiny 12 px grey `#builder_status` span in the drawer footer that was easy to miss. Errors now use `_ssToast` (visible bottom-right toast). Added a `try/catch` wrapper around the entire function to surface unexpected JS exceptions via toast + `console.error`. Replaced arrow-function `.then` chains with plain functions to avoid any parser edge-cases. The page-reload-after-save also moved to a plain `function(){}` callback.
- **Chain builder — node options panel always visible**: `.pos-optional` CSS rule had two `display:` values (`display:none` then `display:grid` in the same declaration). The second value always won, so the Label / Machine tag / Silence threshold panel was always expanded and could not be toggled closed. Fixed by removing the stray `display:grid` from the base rule — only `.pos-optional.open` now sets `display:grid`.
- **Chain builder — "Alert when offline" checkbox/label misaligned**: `.pos-optional label { display:block }` applied to every label inside the options panel, including the inline "Alert when this stream goes offline" label inside a flex row. `display:block` prevented it from sitting next to the checkbox as a flex item. Fixed by narrowing the CSS rule to `.pos-optional .field-lbl` and setting `class="field-lbl"` only on the field-header labels created by `mkField()` — the inline offline label is unaffected.

---

## [3.5.63] - 2026-04-05

### Changed
- **Broadcast Chains — Chain builder redesigned**: Replaced the inline builder panel with a fixed right-side drawer (540 px) that slides in with a smooth transition and closes by clicking the backdrop or the × button. Body scroll locks while the drawer is open.
- **Broadcast Chains — Position cards**: Each signal position is now a self-contained card with a numbered header and reorder controls, replacing the flat list of node rows. Cards show the position number ("Position 1", "Position 2", …) and a ↑/↓ pair to move them up or down.
- **Broadcast Chains — Live chain preview**: A mini chain diagram at the top of the drawer updates in real time as positions are added, removed, or reordered. Each position shows its site, stream, and redundancy label (if set). Clicking a preview node scrolls to the corresponding position card.
- **Broadcast Chains — Timing split into Quick and Advanced**: The three most-used timing fields (Min fault duration, Min recovery duration, Min alert interval) are always visible. Seven less-common fields (Fault hold-off, Mixin, Upstream chain, Fault shift grace, Ad-break gap tolerance, Trend alert, Clip duration) are collapsed into an "Advanced…" disclosure that auto-expands when the chain has non-default values.

---

## [3.5.62] - 2026-04-05

### Fixed
- **Settings Mobile — Disable Token skips confirmation**: `disableMobileApiToken()` looked for a button with `id="mobile-disable-btn"` to pass to `_inlineConfirm`. No element had that ID, so `btn` was always null, the `_inlineConfirm` path was skipped, and the token was disabled immediately without any confirmation dialog. Replaced with `_ssConfirm({danger:true, yesLabel:'Disable'})` which always shows a modal.
- **Settings Security — Delete User submits form**: `userDelete()` used `_inlineConfirm` on the delete button, which is inside the main settings `<form>`. The `ic-ok` button (no `type="button"`) submitted the form on click — "Settings saved" banner appeared instead of the user being deleted. Replaced with `_ssConfirm({danger:true, yesLabel:'Delete'})`.
- **Dashboard — Delete Clip skips confirmation**: `deleteClip()` in MAIN_TPL guarded `_inlineConfirm` with `typeof _inlineConfirm==='function'`. Since `_inlineConfirm` is only defined in SETTINGS_TPL, the guard always failed and clips were deleted immediately without a confirmation dialog. Replaced with `_ssConfirm({danger:true, yesLabel:'Delete'})`.
- **Dashboard — Delete Clip missing CSRF header**: `_doDeleteClip()` sent a `DELETE` fetch with no `X-CSRFToken`. Added `headers:_csrfHeaders()` (already defined in MAIN_TPL).
- **INPUT_FORM_TPL — Now Playing station list null crash**: `loadNowPlayingStations()` called `sel.appendChild(opt)` unconditionally. If the `np_select` element doesn't exist (e.g. for input types that don't show the Now Playing section), this throws a null reference error and the promise silently rejects. Added `if(!sel) return;` guard.
- **HUB_TPL — Dead `_inlineConfirm` definition removed**: `_inlineConfirm` was copied from SETTINGS_TPL into HUB_TPL but was never called anywhere in that template. Removed the dead 12-line function.

---

## [3.5.61] - 2026-04-05

### Fixed
- **Settings — Backup delete submits form instead of confirming**: The backup list delete button used `_inlineConfirm()` whose injected `ic-ok` button has no `type="button"`, causing it to act as `type="submit"` inside the settings `<form>`. Clicking delete submitted the form, showing "Settings saved" and jumping to the top of the page with no delete occurring. Replaced with a self-contained modal (same pattern as the Restart button): all buttons have explicit `type="button"`, modal appended to `document.body`, backdrop closes on `mousedown`.
- **Settings — Backup restore (from disk) submits form**: Same `_inlineConfirm` bug on the ↩ Restore button in the saved-backups list. Replaced with a self-contained modal.
- **Settings — Upload restore button submits form**: The `restore-upload-btn` click handler used `_inlineConfirm` for confirmation. Same bug. Replaced with a self-contained modal.
- **Settings — Rogue ⬇ Backup link on all tabs**: Every settings tab panel (Notifications, Hub, Security, General, Mobile, SDR) had an `<a href="/settings/backup">⬇ Backup</a>` link inside its action bar. The link is only relevant on the Maintenance tab, which already has a prominent Download Backup button in its content. Removed the backup link from all non-Maintenance action bars.
- **Settings restore — CSRF header missing**: `_doRestoreUpload` sent the CSRF token in FormData body only. Added `X-CSRFToken` header so both body and header validation paths are satisfied.

---

## [3.5.60] - 2026-04-05

### Fixed
- **Hub overview — tag filter shows no results**: Stream cards in `HUB_TPL` were missing the `data-tags` attribute. The `applyTagFilter()` JS read `sc.dataset.tags` which was always empty, so every stream was hidden when a tag was clicked. Added `data-tags="{{_stags|e}}"` to the stream card `<div>` in the hub overview template, matching the equivalent attribute already present in `HUB_SITE_TPL`.

---

## [3.5.59] - 2026-04-05

### Changed
- **Restart SignalScope — Maintenance & Plugins tabs**: After clicking Restart the confirm modal now transforms into a countdown display ("Restarting SignalScope… Page will reload in N seconds") rather than closing immediately. Gives clear visual confirmation that the restart was triggered and how long until the page reloads.

---

## [3.5.58] - 2026-04-05

### Fixed
- **Restart SignalScope — Maintenance tab**: Replaced `_ssConfirm` (which closed immediately due to the original click event bubbling to the newly-appended backdrop) with a self-contained inline modal. Modal buttons are `type="button"` so they cannot accidentally submit the settings form. Backdrop closes on `mousedown` (not `click`) to avoid the open/close race.
- **Restart SignalScope — Plugins tab**: Same self-contained modal approach. Also guards against double-open with `_ar-modal` id check.
- **Settings Save button**: Click listener was registered before the button existed in the DOM (script ran at line 396 but button was at line 437). Moved registration inside `DOMContentLoaded` so the element is always available. Dirty-banner input/change listeners moved to same handler.

---

## [3.5.57] - 2026-04-05

### Fixed
- **Restart SignalScope broken in Maintenance tab**: `adminRestart()` used `_inlineConfirm()` — the inline confirm bar's "Confirm" button has no `type="button"` so inside the settings `<form>` it defaulted to `type="submit"`, submitting the settings form and navigating away before the restart POST could complete. Changed to `_ssConfirm()` with `{danger:true, yesLabel:'Restart'}`.
- **Restart SignalScope broken in Plugins tab**: Same function, same `_inlineConfirm` issue. Also the inline bar was inserted into a `display:flex` container causing it to appear and immediately be obscured. Changed to `_ssConfirm()`. Both buttons now also call `_btnLoad`/`_btnReset` for proper loading state.

---

## [3.5.56] - 2026-04-05

### Fixed
- **Restart SignalScope button broken in Maintenance and Plugins tabs**: `adminRestart()` in `INPUT_FORM_TPL` called `_inlineConfirm()` which is only defined in `SETTINGS_TPL` and `HUB_TPL`. In the input/maintenance/plugins context the function was undefined, so clicking Restart did nothing. Changed to `_ssConfirm()` (injected globally by topnav) with `{danger:true, yesLabel:'Restart'}` options.
- **Delete chain / Delete A/B group buttons broken**: `deleteChain()` and `abgDelete()` in `BROADCAST_CHAINS_TPL` called `_inlineConfirm()` which is not defined in that template. Changed to `_ssConfirm()`.
- **Remove input / Restart site buttons broken on hub site page**: Two confirm dialogs in `HUB_SITE_TPL` called `_inlineConfirm()` which is not defined in that template. Changed to `_ssConfirm()`.

---

## [3.5.54] - 2026-04-05

### Fixed
- **Reports — double card border**: removed an orphaned `</div>` that caused the browser to wrap the tab bar, filters, and event table in a phantom element, producing a double card border on the Reports page (Sprint I2)
- **Broadcast Chains — duplicate "Back to Live" button**: removed the toolbar copy of the button; the banner's "⬭ Back to Live" is now the single canonical control for exiting history mode (Sprint I4)

### Changed
- **Broadcast Chains — history datetime input**: `<span>📅 History:</span>` replaced with a proper `<label for="hist_dt">` so the label is programmatically associated with the input; `aria-label` also added directly to the input (Sprint I3)

### Note
- I1 (unsaved changes indicator) completed in v3.5.52; I5 (hub search no-results) was already implemented

---

## [3.5.53] - 2026-04-05

### Changed
- **Chain builder** — auto-focuses the chain name field when the builder panel opens (Sprint G1)
- **A/B Group modal** — auto-focuses the group name field on open for both new and edit flows (Sprint G2)
- **Broadcast Chains keyboard shortcuts** (Sprint G1/G2/G3/G4):
  - `N` — open new chain builder from anywhere on the page
  - `Escape` — dismiss open panels in priority order: Scheduled Maintenance overlay → A/B Group modal → maintenance popover → chain builder
- **Reports keyboard shortcuts** (Sprint G4):
  - `Escape` — reset all active filters when no input is focused; blur active filter input when one is focused
  - `/` — focus the stream filter dropdown from anywhere on the page
- **Note**: Sprint G5 (focus rings) was completed in v3.5.50

---

## [3.5.52] - 2026-04-05

### Changed
- **Settings — hub secret validation**: field gets `minlength="16"` so browsers enforce the minimum natively; the AJAX save handler also checks length client-side and focuses the field with a red border + error toast if too short (Sprint H2)
- **Chain builder — degrading threshold hint**: clarified from "−1 = alert at −1dB/min" to "negative values only · e.g. −1.0 = alert when level falls 1 dB/min" (Sprint H3)
- **Webhook routing rows** — all inputs and selects now carry `aria-label` attributes so screen readers and mobile browsers can identify each column; applies to both server-rendered rows and JS-built rows from `addRoute()` (Sprint H4)
- **Settings — unsaved changes banner**: a sticky "⚠ You have unsaved changes / Save now" banner appears at the top of the content area whenever any form field is edited, and clears automatically after a successful save (Sprint H5)

---

## [3.5.51] - 2026-04-05

### Changed
- **Broadcast Chains empty state** upgraded to the standard `.empty-state` pattern with icon, title, sub-text, and a direct "+ New Chain" button — previously just plain muted text with no call-to-action (Sprint F1)
- **Scheduled Maintenance Windows** loading state replaced with a two-row shimmer skeleton matching the app's `_btnShim` animation — previously a raw "Loading…" text node (Sprint F3)
- **Reports event count** is now server-side rendered as the initial value so it is never blank on page load — previously empty until `DOMContentLoaded` fired (Sprint F4)

### Note
- Sprint F2 (Hub Overview search no-results) was already implemented via `hub-search-hint` in `initCardSearch()` — no change needed

---

## [3.5.50] - 2026-04-05

### Changed
- **Settings Save** is now AJAX — button shows shimmer loading state and a ✓ toast on success instead of a hard page reload with no feedback (Sprint E1)
- **Retrain AI button** converted from `<form method="post">` to an AJAX button with `_ssConfirm` confirmation dialog and `_btnLoad`/`_btnReset` loading state (Sprint E2)
- **Chain builder Save** now shows a `showToast('Chain saved', 'ok')` instead of writing to an inline status text node; button gets loading shimmer during the save (Sprint E3)
- **Delete chain** now shows a `showToast('Chain deleted', 'ok')` after the card is removed from the DOM — previously silent (Sprint E4)
- **Global focus rings** — `.btn:focus-visible` and `.tb:focus-visible` now get a 2 px accent-colour outline, injected once via topnav so it applies across every page (Sprint G5)
- **Reports — Reset filters button** added next to the filter bar; clears all stream/type/date/clips filters with one click (Sprint H1)

---

## [3.5.49] - 2026-04-05

### Fixed
- **Broadcast Chains — SLA History dropdown also toggled Fault History**: The SLA History toggle div shared the `flog-toggle` CSS class with the Fault History toggle. The delegated click listener on `#chains_list` matched both, causing the Fault History panel to open/close whenever the SLA History row was clicked. Fixed by giving the SLA toggle its own `slahist-toggle` class; CSS extended to cover both classes.

### Removed
- **Broadcast Chains — Test Alert button**: Removed the 🧪 Test Alert button and its result banner from the chain list header. The associated JS event listener and result `<div>` are also removed. The `/api/chains/test_alert` backend route is preserved.

---

## [3.5.48] - 2026-04-05

### Changed
- **Form UX — `required` attributes**: Added `required` to mandatory fields that were already JS-validated but had no HTML constraint: codec Name + Host, zetta SOAP URL, AzuraCast server URL. Browser now shows inline validation tooltip before the fetch fires.
- **Form UX — `spellcheck="false" autocomplete="off"`** on all credential/API key fields: Pushover User Key + App Token, Webhook URL (also changed to `type="url"`), AzuraCast API key, Zetta URL, Icecast hostname, push.py APNs Key ID + Team ID + FCM Project ID.
- **Form UX — Icecast passwords** changed to `type="password"` with `autocomplete="new-password"` so browsers don't autofill streaming credentials into the source/admin password fields.
- **Focus rings — `:focus` rules added to main SETTINGS_TPL and two hub sub-templates**: all `<input>` types and `<select>` now get `border-color:var(--acc)` on focus. Previously only some templates had this.
- **Mobile responsiveness**:
  - codec.py: `.row2` (2-column form grid) collapses to 1 column at ≤480 px.
  - icecast.py: server settings grid + add-stream grid collapse to 1 column at ≤540 px; stream table wrapped in `.tbl-wrap` (horizontal scroll on small screens).
  - ptpclock.py: mode-select grid collapses to 1 column at ≤480 px; preset rows get `flex-wrap:wrap`.
  - push.py: APNs credentials grid gets `.grid-2` class with responsive collapse at ≤480 px.

---

## [3.5.47] - 2026-04-05

### Changed
- **Empty states — Push, AzuraCast** — replaced bare italic/plain text "nothing here" messages with centred empty-state panels (icon + title + sub-text with actionable hint):
  - Push plugin deliveries tab: 📭 "No deliveries yet" with explanatory sub-text
  - AzuraCast overview stations grid: 📻 "No stations configured yet" pointing to the Servers panel below
  - AzuraCast overview servers list: 🔌 "No AzuraCast servers added yet" pointing to the Discover form
- **Inline button styles cleaned up — push.py**: Migrate and Test buttons now use `.btn.bp` / `.btn.bg` class system instead of raw inline `style=` colour overrides. Loading shimmer (`_btnLoad`/`_btnReset`) also added to both buttons.
- **`.empty-state` CSS** added to push.py and azuracast.py (both settings and overview templates).

---

## [3.5.46] - 2026-04-05

### Changed
- **Global button loading states** — two new helper functions (`_btnLoad(btn)` / `_btnReset(btn)`) and `.btn-loading` CSS (shimmer animation) injected globally via `topnav()`. While loading: button dims to 72% opacity, gains a travelling shimmer highlight, and is non-interactive (`pointer-events:none`). On completion (success or error) the button is fully restored with its original label. Applied to **25 action buttons** across 9 files:
  - signalscope.py: Test Email/Webhook/Pushover, Save User, Update Password, Disable 2FA, Scan for dongles, Restore backup, Admin Restart, Kill DAB orphans
  - plugins: icecast.py (Start/Stop/Save/Add/Delete streams), codec.py (Save device), ptpclock.py (Save settings, Add preset), morning_report.py (Regenerate), presenter.py (Save), azuracast.py (Discover), zetta.py (Save, Discover, Test, Debug call, WSDL methods)

---

## [3.5.45] - 2026-04-05

### Changed
- **Global toast + confirm modal system** — all browser `alert()` and `confirm()` calls across the entire app (signalscope.py + all plugins) have been replaced with two new global functions injected by `topnav()`:
  - `_ssToast(msg, type, dur)` — animated slide-in toast notification (bottom-right, stacks, auto-dismisses). Types: `'ok'` (green), `'err'` (red), `'warn'` (amber), `'info'` (blue). Click to dismiss early.
  - `_ssConfirm(msg, onYes, opts)` — centred modal confirmation dialog with blurred backdrop, Escape-to-close, backdrop-click-to-close. Options: `danger` (red confirm button), `yesLabel`, `noLabel`, `title`.
  - **32 call sites replaced** across signalscope.py, push.py, dab.py, morning_report.py, sdr.py, presenter.py, codec.py, azuracast.py, icecast.py. No browser `alert()`/`confirm()` dialogs remain.

---

## [3.5.44] - 2026-04-05

### Changed
- **Sources panel converted to centred modal popover** — clicking ⚙ Sources on a replica page now opens a fixed-position modal with a blurred dark backdrop, sticky header, × close button, slide-in animation, Escape-to-close, and backdrop-click-to-close. The panel no longer expands inline at the bottom of the site card. Form inputs enlarged to 13 px and use the standard `#0d1e40` input background. Status messages (`hubMgrMsg`) now use themed `.msg-ok` / `.msg-err` styled boxes instead of plain coloured text.

---

## [3.5.43] - 2026-04-05

### Changed
- **Replica page — Sources button more prominent** — the `⚙ Sources` button on the site replica header is now styled as a primary blue button (`btn bp`) so it stands out clearly from the row of small grey action buttons (Stop, Log, Restart, Backup, Ping, etc.).
- **Replica page — empty state calls to action** — when a site has no streams configured, the replica page previously showed a grey text hint. It now shows a centred empty-state panel with a large `⚙ Add Sources` primary button that opens the Sources panel directly.

---

## [3.5.42] - 2026-04-05

### Fixed
- **Broadcast Chains save fails with "CSRF validation failed"** — `BROADCAST_CHAINS_TPL` had two `var _csrf` declarations across two `<script>` blocks. The first (line 1 of the first block) captured the token as a plain string. The second (added later for the Scheduled Maintenance Windows feature) redeclared `_csrf` as a function. When the second block ran on page load, the function object silently overwrote the string — so `_f()` was sending `function(){...}` as the `X-CSRFToken` header value and every save/delete call failed with a CSRF error. Fix: consolidated into a single `function _csrf(){...}` declaration at the top of the first script block; updated `_f()` to call `_csrf()`; removed the duplicate declaration from the maintenance windows block.

---

## [3.5.41] - 2026-04-05

### Fixed
- **HUB_SITE_TPL nav buttons oversized** — the site replica view (hub single-site page) was missing `.bs`, `.nav-active`, and `.bd` CSS classes. Topnav buttons rendered at full padding size. Also aligned `.btn` base padding to `5px 12px` to match the rest of the app and added `font-family:inherit` and `filter:brightness(1.15)` hover.
- **SLA Dashboard `--wn` undefined** — the SLA dashboard `:root` block was missing `--wn:#f59e0b`. The `.warn-box` element references this variable, so the amber warning highlight would fall back to transparent/inherit on some browsers.

---

## [3.5.40] - 2026-04-05

### Fixed
- **TOTP setup page nav bar oversized** — `TOTP_SETUP_TPL` was missing the `.bs` (small button) class, so the topnav's `btn bg bs` nav buttons rendered at full `5px 12px` padding instead of the compact `3px 9px`. Also added missing `--wn` CSS variable, `.bd` (danger) and `.nav-active` classes, and `flex-wrap:wrap` + `box-shadow` to the `header` CSS rule so the setup page is fully style-consistent with the rest of the app.

### Changed
- **Audit Log moved into Hub dropdown** — the "📋 Audit Log" nav link is now an item inside the Hub ▾ dropdown menu (between Broadcast Chains and the Hub items) rather than a standalone button. In standalone (non-hub) mode it remains a standalone button since there is no Hub dropdown to place it in.

---

## [3.5.39] - 2026-04-05

### Fixed
- **TOTP setup QR code not rendering** — the QR code was generated client-side by loading `qrcode.js` from the jsDelivr CDN. The app's Content Security Policy has no external `script-src` hosts allowlisted, so the CDN script was blocked and users saw "QR library failed to load — use the manual key above." Fix: QR code is now generated server-side in Python by `_make_totp_qr_b64()` (uses `qrcode[pil]`, falls back to `segno`) and embedded directly as a `data:image/png;base64,…` `<img>` tag. No external network request, no CSP issue. The CDN `<script>` block has been removed from `TOTP_SETUP_TPL`. `qrcode[pil]` added to the installer.

---

## [3.5.38] - 2026-04-05

### Fixed
- **TOTP setup page 500 error** — opening Settings → Security → Set Up 2FA produced an `Internal Server Error`. Root cause: `render_template_string` was called with `topnav=topnav` as an explicit keyword argument, but `topnav` is registered as a Jinja2 global (not a local variable in the view function), so Python raised `NameError` before the template was rendered. Fix: removed the redundant `topnav=topnav` kwarg; the Jinja2 global is resolved automatically.

---

## [3.5.37] - 2026-04-05

### Added
- **TOTP two-factor authentication** — per-user TOTP 2FA (Google Authenticator, Authy, 1Password, etc.). Enrol via Settings → Security → Set Up 2FA: a QR code is rendered client-side using qrcode.js and a manual key is shown for manual entry. Entering the correct code activates 2FA on the account. At login, users with 2FA enabled are redirected to a TOTP verification page before their session is created. Disable 2FA at any time with your current password.
- **Remember this device for 30 days** — both the TOTP verification page and the standard login page include a "Remember this device for 30 days" checkbox. On TOTP, ticking it sets a signed `ss_totp_rem` cookie so trusted devices skip the TOTP prompt for 30 days. Tokens are invalidated when 2FA is disabled. All remember tokens are revoked on explicit logout.
- **Audit log** — every significant action (login, logout, settings save, chain create/delete, user create/update/delete, 2FA enable/disable) is appended to `audit_log.json` with timestamp, username, IP, action, and detail. View the last 500 events at `/audit` (admin only) with live client-side filtering by user, action, and detail. Download the full log as CSV via `GET /audit.csv`. An "📋 Audit" link appears in the nav bar for admin users.

---

## [3.5.36] - 2026-04-05

### Added
- **Chain fault log CSV export** — every chain's fault history panel now has a "⬇ CSV" download button. Exports up to 2 000 most recent faults as a CSV file (columns: date/time, fault node, site/stream, duration, RTP loss %, adbreak flag, cascade source, message, engineer notes). Available at `GET /api/chains/<id>/fault_log.csv`.
- **Multi-month SLA history** — input stream SLA is now persisted to a new `stream_sla_history` SQLite table on each monthly rollover. The SLA dashboard page shows all past months per stream in a collapsible row (click any stream row to expand). Broadcast chain SLA history is derived directly from `chain_status` metrics and shown in a new "📊 SLA History" collapsible panel on each chain card, populated from `GET /api/chains/<id>/sla_history?months=12`.
- **Scheduled maintenance windows per chain** — a new "🗓 Windows" button on each chain opens a modal to manage recurring or one-off maintenance windows. Recurring windows fire on selected days of the week at a configured time for a set duration. One-off windows fire at a specific epoch timestamp. Windows can optionally suppress SLA downtime. Windows are persisted in the chain config (`lwai_config.json`) and evaluated every ~30 s in `_chains_monitor_loop`; the existing maintenance/eval mechanism is reused so all downstream suppression behaviour (alerts, badges, etc.) works automatically. REST API: `GET/POST /api/chains/<id>/maintenance_windows`, `PUT/DELETE /api/chains/<id>/maintenance_windows/<wid>`.

---

## [3.5.35] - 2026-04-05

### Added
- **Stream tags** — each input now has a comma-separated Tags field (Settings → Inputs → Edit → 🏷 Tags). Tags are displayed as blue pills on stream cards in the hub overview and on the Settings inputs list. Tags are included in the heartbeat payload so the hub always has up-to-date tag data.
- **Hub tag filter bar** — when any stream has tags configured, a filter bar appears above the site grid with "All" plus one pill per unique tag. Clicking a tag pill hides all stream cards that don't carry that tag and collapses site cards that have no matching visible streams. Clicking "All" restores everything.
- **Mobile responsive layout** — Settings, Hub, and Reports pages are now usable on small screens:
  - Settings: sidebar collapses off-screen on ≤768 px; a ☰ hamburger button (fixed top-right) slides it open/closed
  - Hub: site grid goes single-column on ≤900 px; toolbar stacks vertically, stream grid goes single-column, and header shrinks on ≤640 px
  - Reports: filter bar stacks vertically, filters go full-width, table becomes horizontally scrollable, and the less-critical Level/RTP columns are hidden on ≤640 px

---

## [3.5.34] - 2026-04-05

### Added
- **Metric history CSV export** — new `GET /metrics.csv?stream=X&metric=Y&hours=N` endpoint downloads a time-series CSV (columns: `datetime,ts,stream,metric,value`). Supports up to 720 hours (30 days) of history. A "⬇ CSV" download button appears in the Signal History panel on hub stream cards; its URL automatically tracks the current stream/metric/range selection as you change them.
- **Alert CSV date-range filter** — the "⬇ CSV" button on the Reports page now respects the active from/to date, stream, and type filters. The download link updates live as you adjust filters, so you always export exactly the events shown on screen. The `/reports.csv` endpoint accepts `?from=`, `?to=`, `?stream=`, and `?type=` query parameters directly.

---

## [3.5.33] - 2026-04-04

### Fixed
- **Plugin update/install confirm dialog crushes description text** — clicking "Update to vX" or "Install" inserted the inline confirm bar (`ic-bar`) as an extra flex item inside the plugin row's `display:flex` container. With `min-width:0` on the description div, it could shrink to near zero and wrap every word onto its own line. Fixed by: (1) adding `flex-wrap:wrap` to the plugin row container so the confirm bar can break onto its own line; (2) adding `flex-basis:100%` to the global `.ic-bar` rule so whenever a confirm bar lives inside a flex container it always occupies a full-width row beneath the main content. Applies to both the Settings and Hub templates.

---

## [3.5.32] - 2026-04-04

### Added
- **Reports "Ack" column** — every event row on the Reports page now has an Ack cell. Click "Ack" to acknowledge the alert; the cell updates live to show "✓ acked by {user}" with a timestamp. Ack state is populated from `_history_with_acks()` on both the initial page render and the 15 s live-refresh endpoint (`/reports/data`), so newly arriving events also show their ack status without a page reload.
- **Viewer role cannot acknowledge alerts** — the `/api/alerts/<id>/ack` endpoint now returns HTTP 403 for viewer and plugin-role users. Only admin/operator roles can acknowledge.
- **Hub card ack indicator** — when a stream's current silence event has been acknowledged, a green "✓ acked by {user}" badge appears next to the 🔇 SILENCE badge on the hub overview stream card. Computed at page-render time from the most recent SILENCE alert event per stream.
- **Keyboard shortcut overlay on Hub page** — press `?` on the Hub page to open a modal listing all keyboard shortcuts (R = force refresh, / = search, ? = shortcuts, Esc = close). Press `?` or `Esc` again to dismiss.

### Fixed
- **Inputs page empty-state CTA** — the "No inputs configured yet" empty state on Settings → Inputs previously linked to "Go to Settings → Inputs", creating a circular dead-end. Now shows a direct inline prompt: "Click + Add Input above to add your first stream."

---

## [3.5.31] - 2026-04-04

### Fixed
- **Stereo toggle missing for DAB feeds on hub replica pages** — the stereo toggle button was hidden for both `dab://` and `fm://` sources on the hub overview. DAB stereo capture is a valid user setting (welle-cli decodes stereo when enabled); only FM is correctly excluded (FM stereo is auto-detected via pilot tone and controlled via Force Mono, not the stereo flag). Changed the condition to suppress the button for `fm://` only, so DAB feeds now show the stereo toggle alongside their other controls.

---

## [3.5.30] - 2026-04-04

### Added
- **AI Monitor stereo support** — the AI anomaly monitor now correctly handles stereo streams. Previously, `_ai_loop` read `_audio_buffer` directly; for stereo streams this contains interleaved L/R float32 data that was being processed as a double-length mono signal, producing completely wrong spectral features. The loop now detects `_audio_channels == 2`, deinterleaves the buffer into separate L and R arrays, and uses the mid-mix `(L+R)/2` for the 14 standard features. Two additional stereo-specific features are extracted and appended: **[14] L–R correlation** (normalised to [0,1]; detects one dead channel, phase faults) and **[15] L/R RMS imbalance** (|dBFS_L − dBFS_R| / 20 dB; detects channel balance faults). Stereo streams train and load 16-feature models (`AI_FEATURE_DIM_STEREO = 16`); mono streams continue to use 14-feature models. `_classify` adds stereo-specific fault labels for features [14] and [15]. On the first run after upgrade, any existing mono-trained models for stereo streams will automatically detect the feature-dimension mismatch and begin a fresh learning phase.
- **AI model feature dim stored in stats JSON** — `feat_dim` is now persisted in `<stream>_stats.json` alongside `mean`/`std`/`n`. On load, the ONNX model's input shape is checked against `feat_dim`; a mismatch resets to learning phase rather than crashing.

---

## [3.5.29] - 2026-04-04

### Fixed
- **Broadcast Chains fault log always shows "Error loading fault log"** — `loadFaultLog` called `_relTime()` (added in 3.5.23 for relative timestamps) but `_relTime` was never defined in `BROADCAST_CHAINS_TPL`. Every call threw `ReferenceError: _relTime is not defined`, which was swallowed by the `.catch()` handler and rendered as the error message. Fixed by adding `_relTime` alongside `_esc` and `_fmtDur` in the chains template.

---

## [3.5.28] - 2026-04-04

### Fixed
- **FM stereo broken by de-emphasis (regression from 3.5.27)** — `_apply_deemph` passed `zi` as shape `(1, 1)` to `scipy.signal.lfilter` but the function expects `(1,)` for a 1D signal. The resulting `ValueError` was caught by the stereo path's `except Exception` block, which silently fell back to mono `_mpx_to_audio()` on every chunk. Fixed: `zi=np.array([zi[0]])` (shape `(1,)`) and `zi_new[0]` instead of `zi_new[0, 0]`.

---

## [3.5.27] - 2026-04-04

### Added
- **FM de-emphasis** — first-order IIR low-pass de-emphasis filter applied after FM demodulation and 48 kHz resample. Configurable per-input in Settings → Inputs → Edit (FM section): 50 µs (Europe / Australia / Asia), 75 µs (North America / South Korea), or Off. Stateful filter (persists across chunks) applied independently to L, R, and mono channels. Defaults to 50 µs for new and existing FM inputs. Corrects the FM pre-emphasis applied at the transmitter, restoring flat high-frequency response and reducing perceived hiss.

### Fixed
- **Stereo / device change not taking effect until manual restart** — editing an input's stereo checkbox or device/source and saving now automatically restarts monitoring when those settings change. Previously, the DAB/HTTP/ALSA monitor loop had `_dab_n_ch` / `_http_n_ch` and ffmpeg `-ac` baked in at thread startup; saving stereo=false while the stream was running left ffmpeg outputting stereo-interleaved PCM that was then misinterpreted as double-length mono audio (distorted). Monitoring is now restarted immediately on stereo or device_index change.

---

## [3.5.26] - 2026-04-04

### Fixed
- **Hub "Remove input" button broken** — same missing `});` pattern as 3.5.25: the `hub-remove-input` click listener was missing its closing `});` after the `_inlineConfirm` call. This caused a JS syntax error in the hub script block, breaking all hub management buttons in that block.

---

## [3.5.25] - 2026-04-04

### Fixed
- **Check for Updates / Restart / Kill orphan DAB buttons all broken since 3.5.23** — the backup list delete-button handler (`bk-list-wrap` click listener) was missing its closing `});` after the 3.5.23 inline-confirm refactor. This left a JavaScript syntax error in the entire 1309–1833 script block, silently killing `_csrf()`, `checkForUpdates()`, and the `upd-check-btn` event listener. All buttons depending on that block appeared to do nothing. Fixed by restoring the missing `});` to close the event handler.
- **Restart button TypeError** — `onclick="adminRestart()"` passed no argument; the function tried `_inlineConfirm(null, …)` which threw a TypeError. Fixed by changing the onclick to `adminRestart(this)` so the button element is passed.

---

## [3.5.24] - 2026-04-04

### Fixed
- **Hub live relay stutter / "starts and stops" for stereo DAB streams** — the relay writer in `_push_audio_request` was using `proc.stdout.read(4096)` with a blocking WAN POST after each read. At 256 kbps stereo this meant 4 WAN round trips per 0.5 s audio chunk; on any link with RTT > ~125 ms the hub's relay slot drained faster than it refilled, producing periodic audio dropouts. Fixed by increasing the read size to 16 384 bytes (≈ one full 0.5 s stereo chunk), reducing WAN round trips from 4 to ≤ 1 per chunk. Also increased the relay pre-seed from 3 s to 5 s for additional headroom on high-latency links.

---

## [3.5.23] - 2026-04-04

### Added
- **Stale data banner** — fixed orange bar appears at the top of the Hub page after two consecutive `hubRefresh` failures, showing "⚠ Connection lost — last update Xm ago. Retrying…". Clears automatically on first successful poll. Engineers can immediately see when hub data is frozen rather than unknowingly acting on stale information.
- **Relative timestamps** — Reports page and fault log drawer now show "4m ago", "just now", etc. Hovering the cell shows the full absolute timestamp. Updates every 60 s automatically. Reverts to absolute time for events older than 24 h.
- **Inline confirmations** — every destructive action (`confirm()` dialog) replaced with a slick inline bar that appears directly below the button: Cancel / Confirm. Works on LAN/HTTP where browser `confirm()` is silently blocked. Covers: delete user, delete backup, restore backup, restart, delete chain, delete clip, remove plugin, install/update plugin, hub site remove/restart, hub input remove, A/B group delete, mobile token disable.
- **Keyboard shortcuts on Hub page** — `R` forces an immediate refresh; `Esc` closes any open inline confirmation bars and the live-play panel.
- **Tab title alert state** — Hub page title prefixes `🔴` on any ALERT site, `🟡` on any WARN site (all-clear has no prefix). Engineers see the alert state at a glance in background tabs.
- **Settings scroll preservation** — switching Settings tabs now saves and restores scroll position per-tab in localStorage. No more jumping back to the top when returning to a long Inputs or Hub section.
- **Improved empty states** — clips panel shows "🎙 No clips recorded yet — clips are saved automatically when silence or faults are detected." Fault log drawer shows "✓ No faults recorded for this chain — all clear." instead of blank space.

---

## [3.5.22] - 2026-04-04

### Fixed
- **Wall Mode no longer reloads every 60 seconds** — removed the unconditional `location.reload()` timer. The page now stays live indefinitely. Topology changes (new site or stream appearing) are detected inside the existing 150 ms `_wLivePoll` loop: on first poll a set of known stream keys is built; on subsequent polls if a stream arrives in the API response with no rendered element it triggers a reload. Normal monitoring does not cause any reload.

---

## [3.5.21] - 2026-04-04

### Changed
- **Toast notifications** — settings saves now show a slide-up toast (bottom-right, auto-dismisses after 3.5 s) in addition to the existing banner. Green left-border for success, red for errors.
- **Reports table** — Level (dBFS) and RTP Loss columns hidden by default to reduce density. Click any event row to expand a detail panel showing those values alongside other event metadata.
- **Nav bar icons** — Dashboard (📊), Inputs (🎚), Reports (📋), SLA (📈), Hub (🌐), Broadcast Chains (🔗), Settings (⚙) now have icons for quicker scanning.
- **Settings tabs** — confirmed already have icons; SDR Devices (📻), Plugins (🔌), Hub & Network (🛰), Users & Roles (👥), etc.
- **Main dashboard** — confirmed already uses card grid layout.

---

## [3.5.20] - 2026-04-04

### Added
- **Contextual tooltips on Settings fields** — `ⓘ` icons with hover tooltips on the fields that most often confuse new engineers. SDR Devices table: Serial (when to use vs leave blank), Role (scanner/dab/fm/none explained), PPM (how to measure with rtl_test), Gain (tenths-of-dB scale, recommended starting range, AGC caveat). Hub & Network: Shared Secret Key (purpose, matching requirement, minimum length). Input edit form: Silence Threshold dBFS (typical programme levels, recommended starting value) and Min Duration (avoiding false alarms on quiet passages).
- **Improved empty states** — Dashboard with no inputs configured now shows a centred card with icon, description, and a direct "Go to Settings → Inputs" button. Broadcast Chains with no chains shows a description and pointer to the "+ New Chain" button. Hub overview with no connected clients shows concise connection instructions (set Mode → Client, enter hub URL and secret key).

---

## [3.5.19] - 2026-04-04

### Fixed
- **DAB services still sequential at 52s intervals — welle-cli default is one-at-a-time** — removing `-C 1` in 3.5.16 had no effect because welle-cli's default behaviour (no `-C` flag) is also sequential one-at-a-time activation, producing the same 52 s / 104 s / 156 s pattern. The fix is `-C N` with N large enough to decode all services simultaneously. Non-Pi: added `-C 20` (larger than any standard DAB mux ≤18 services), forcing all encoders to start in parallel. Pi: kept `-T -C N` where N = monitored service count. Added a 0.5 s pause before building the welle-cli command so that all 9 concurrently-starting monitor threads have time to register in `session.consumers` before `len(session.consumers)` is read for the Pi `-C N` value (previously it was always 1 at the moment welle-cli launched).

---

## [3.5.18] - 2026-04-04

### Fixed
- **DAB audio probe — restore ≥4096 byte threshold with accumulated reads** — 3.5.17 lowered the threshold to ≥128 bytes which would have broken weak-signal detection (welle-cli may send a few bytes and stall on a poor signal; ≥128 would falsely declare ready). Root cause was not the threshold itself but how Python reads from streaming HTTP: a single `read(4096)` on a chunked HTTP stream returns as soon as *any* data is in the socket buffer, typically one MP3 frame (~480 bytes), never accumulating to 4096 in one call. Fix: per probe attempt, accumulate reads in a 4.5 s loop until ≥4096 bytes are collected (or the window expires). The ≥4096 threshold is preserved — on a good signal at 128 kbps the loop fills in ~0.25 s; on a weak/stalled signal it exhausts the window and retries.

---

## [3.5.17] - 2026-04-04

### Fixed
- **DAB audio endpoint probe stuck for 52 s — threshold too high** — the initial probe loop required `len(read) >= 4096` bytes before declaring an endpoint ready. welle-cli sends the ID3 header and early MP3 frames in small chunks well below 4096 bytes. The check silently failed on every probe attempt (no exception thrown, no log line, just a small read that didn't hit the threshold) causing a ~52 s busy-loop until a large enough burst finally accumulated. Fixed: threshold lowered to `>= 128` bytes. Any non-trivial response body means the encoder has started. Probe `timeout=3 → 5` for a slightly longer initial read window. Same fix applied to the reconnect-recovery probe.

---

## [3.5.16] - 2026-04-04

### Fixed
- **DAB services sequential (52 s each) — root cause: `-C 1` carousel mode** — with `-C 1`, welle-cli decodes services one at a time, rotating every ~52 s regardless of how many connections are waiting. Services appeared at 52 s, 104 s, 156 s intervals; services beyond the 4th frequently missed the 120 s probe deadline entirely. Fix: removed `-C 1` from the non-Pi welle-cli command in `_start_dab_session`. Without `-C`, welle-cli decodes all services simultaneously — all encoders start in parallel and are ready within seconds. Pi hardware retains `-T -C N` (N = monitored service count) to prevent CPU overload. Prewarm `timeout=5 → 30`: without the carousel, all encoders start in parallel but may take more than 5 s to produce their first output; the longer timeout ensures prewarm connections stay open until real data flows, keeping encoders alive for the probe loop.

---

## [3.5.15] - 2026-04-04

### Fixed
- **Revert prewarm to exact 3.5.5 state** — the 3.5.13 retry loop change to `_warm_one` was incorrect and broke the prewarm behaviour that had been working in 3.5.5. Restored to single-attempt, `timeout=5`, no retry — exactly the 3.5.5 code.

---

## [3.5.14] - 2026-04-04

### Fixed
- **DAB services sequential again after 3.5.6 — restore `-C 1` on non-Pi** — 3.5.6 removed `-C 1` from the monitoring welle-cli command on the incorrect assumption it was the root cause of slow startup. The actual fix was the DabPrewarm introduced in 3.5.5. The proven combination is `-C 1` + DabPrewarm: without `-C 1`, welle-cli's behaviour changes in a way that defeats the prewarm's persistent-connection strategy and services revert to sequential ~52 s-each startup. Restored `-C 1` on non-Pi hardware. Pi hardware uses `-T -C N` (where N = monitored service count) as before.

---

## [3.5.13] - 2026-04-04

### Fixed
- **DAB slow service startup — prewarm silently gave up after 5 s timeout** — `_warm_one` in the DabPrewarm thread used a single `urlopen(timeout=5)` attempt. If welle-cli took more than 5 seconds to start a service's MP3 encoder (common with many simultaneous services), the prewarm silently failed and never retried. With no persistent connection holding the encoder alive, the service fell back to the probe loop's short open-read-close cycles, which can reset the encoder on each attempt. Fixed: `_warm_one` now retries in a `while` loop until the 150 s window expires. `timeout=5 → 10` for more headroom per attempt. Services that take longer to warm now get persistent connections established as soon as the encoder is ready, rather than being abandoned after one failed attempt.

---

## [3.5.12] - 2026-04-04

### Fixed
- **Restore DAB monitoring Pi CPU fix (reverted in error in 3.5.11)** — `_start_dab_session` now correctly adds `-T -C N` on Raspberry Pi, where N = number of consumers on the session at launch. `-T` disables TII decoding; `-C N` limits simultaneous service decoding to only the services actually being monitored. Non-Pi hardware unchanged. The monitoring session uses `-F` for device selection (not `-D`), so there is no `-C`/`-D` conflict here.

---

## [3.5.11] - 2026-04-04

### Fixed
- **Reverted 3.5.9 `_start_dab_session` Pi changes (in error — restored in 3.5.12)**

---

## [3.5.10] - 2026-04-04

### Fixed
- **DAB Scanner plugin — `--C` and `-D` conflict on Raspberry Pi with SDR serial set (dab.py v1.0.33)** — welle-cli rejects `-C` and `-D` together (`Cannot select both -C and -D`). `-D driver=rtlsdr,serial=XXXXX` is required when a specific dongle serial is configured. The v1.0.32 Pi fix unconditionally added `-C 1` before the serial check, causing welle-cli to exit with rc=1 on any Pi with a serial set. Fixed: `-T` is always added on Pi (compatible with `-D`); `-C 1` is only added when no serial is set (single-dongle Pi setups where `-D` is not needed). Multi-dongle Pi setups with a serial get `-T` only — still a meaningful CPU reduction.

---

## [3.5.9] - 2026-04-04

### Fixed
- **DAB monitoring inputs — CPU overload on Raspberry Pi with large muxes** — the same CPU overload that affected the DAB Scanner plugin also affected monitored DAB inputs (`_start_dab_session`). On a Raspberry Pi, welle-cli decoded the entire ensemble simultaneously, saturating the CPU. **Fixed:** `_start_dab_session` now calls `_is_raspberry_pi()` and on Pi adds two flags to the welle-cli command: `-T` (disables TII decoding — not needed for monitoring, saves significant CPU) and `-C N` where N = the number of consumers registered on that session at launch time (so all configured monitored services are decoded, but unused services on the mux are not). Non-Pi hardware is unaffected.

---

## [3.5.8] - 2026-04-04

### Fixed
- **DAB Scanner plugin — CPU overload on Raspberry Pi with large muxes** — on a Raspberry Pi, a large multiplex such as BBC National (~12 services) caused welle-cli to decode all services simultaneously, saturating the Pi's CPU. This corrupted the audio output and prevented playback. **Fixed (dab.py v1.0.32):** `_stream_worker` now auto-detects Raspberry Pi hardware by reading `/proc/device-tree/model` and conditionally adds `-C 1` (single-service decode mode) to the welle-cli command. On a Pi, welle-cli decodes only the one service that has an active HTTP consumer rather than the entire mux in parallel. Non-Pi hardware is unaffected — full parallel decode continues on x86/x64 servers.

---

## [3.5.7] - 2026-04-04

### Fixed
- **PTP Clock (and any plugin using stacked route decorators) always shows "Restart needed"** — `_make_isolated_app._wrap_view` created a new closure on every call. When a plugin used stacked route decorators on the same function (e.g. `@app.post("/api/ptpclock/mic") / @app.post("/api/ptpclock/mic/<preset_id>")` on one `def`), two different wrapper objects were produced. Flask's `add_url_rule` checks `old_func != new_func` for the same endpoint name and raises `AssertionError`. That exception was caught by `_load_plugins`'s outer `except`, so `_plugins.append(info)` never ran — the plugin was permanently absent from `_plugins` and `_scan_installed_plugins()` always returned `active=False`. Fixed: `_wrap_view` now caches by `id(original_fn)` in a closure-level `_wrap_cache` dict. Stacked decorators on the same function always return the same wrapper object, so Flask sees no endpoint conflict.

**Rule**: `_wrap_view` must cache wrappers by `id(view_fn)`. Never create a new closure per call — doing so breaks stacked route decorators.

---

## [3.5.6] - 2026-04-04

### Fixed
- **DAB slow startup root cause — welle-cli `-C 1` carousel mode** — the welle-cli launch command included `-C 1`, which puts welle-cli into carousel mode: it decodes **one programme at a time**, rotating through the ensemble. This is the documented meaning of `-C N` ("number of programmes to decode in a carousel"). With `-C 1`, welle-cli activates one service encoder, holds it for ~52 s, then deactivates it and starts the next — so 9 services take ~8 minutes. The flag was present from the original shared-session implementation and was the sole cause of the sequential 52 s-per-service startup pattern. **Fixed:** removed `-C 1` from the welle-cli command entirely. Without `-C`, welle-cli decodes the **entire ensemble simultaneously**, and all `/mp3/<sid>` endpoints become ready within ~1 mux-lock cycle (~10–15 s). The `DabPrewarm` thread added in 3.5.5 remains as a useful insurance but is no longer the primary mechanism.

---

## [3.5.5] - 2026-04-04

### Fixed
- **DAB slow startup — welle-cli encodes services sequentially (~52 s each)** — welle-cli starts each `/mp3/<sid>` service encoder lazily (on first HTTP connection) and processes them one at a time. With 9 services on the NI 12D multiplex this produced exactly 52 s intervals between "endpoint ready" events (confirmed from logs: +52 s, +104 s, +155 s, +206 s, +258 s...) for a total of ~8 minutes. The Python probe loop (3 s timeout → close → reconnect) kept triggering encode-start/stop cycles but could only get one service ready per 52 s cycle.

  **Fix:** after the shared mux session becomes ready, a new `DabPrewarm` background thread opens **persistent streaming connections** to every service endpoint simultaneously (`/mp3/<sid>` for each SID in `mux.json`). This forces welle-cli to start all service encoders in parallel rather than sequentially. Each connection continuously reads audio data to keep the encoder alive for up to 150 s. Consumer ffmpeg processes connect as second subscribers and see immediate data instead of a cold start. Expected result: all 9 services ready within ~52 s of mux-ready instead of ~8 minutes.

---

## [3.5.4] - 2026-04-04

### Fixed
- **DAB slow startup — audio endpoint probe deadline too short** — each service on a shared DAB multiplex is encoded lazily by welle-cli (encoder starts on first HTTP connection). In a large ensemble (9 services on 12D NI) the encoders start sequentially; the slowest service can take 90–160 s after mux-ready before its `/mp3/<sid>` endpoint delivers 4 KB. The 35 s probe window caused every stream to cycle probe→fail→5 s restart, producing 3+ minutes of cascading retries with services becoming ready one per cycle. Probe deadline raised to **120 s**, covering even the slowest encoder in a single pass. All services in the NI 12D ensemble now become ready within one probe window instead of spreading across 4+ restart cycles.

---

## [3.5.3] - 2026-04-04

### Fixed
- **Logger plugin — spurious watchdog fires on DAB/slow streams (v1.6.1)** — watchdog countdown started immediately at recording session start (`_wdog_last = time.monotonic()`). For DAB streams still initialising (welle-cli mux enumeration can take 45+ seconds), no audio chunks were written for > 30 s and the watchdog killed the recording ffmpeg — producing an empty file and log noise on every segment. Fixed: `_wdog_last` now initialises to `None`; the watchdog only checks the timeout after the first successful write. A stream that never produces audio within a segment exits naturally when the segment ends, not via watchdog.

---

## [3.5.2] - 2026-04-04

### Fixed
- **`/api/health` always reports "Stale streams"** — `_last_level_ts` was read by the health check but never written anywhere in the codebase. `getattr(inp, "_last_level_ts", 0)` always returned `0`, making `age = time.time() - 0` ≈ 1.7 billion seconds — every stream with a real level measurement was permanently flagged as stale regardless of actual health. Fixed: `cfg._last_level_ts = time.time()` is now written alongside `_has_real_level = True` in `analyse_chunk`, updated on every audio chunk (~10 Hz). The health check now correctly flags streams that genuinely stop producing audio for > 120 s.

---

## [3.5.1] - 2026-04-04

### Fixed
- **Hub site replica — history event body blank** — recent events accordion showed blank text because `h.msg`/`h.text` field names don't exist; correct field is `h.message`. All history events now display properly.
- **Hub dashboard — silence animation not live** — `sc-silence` class (amber pulsing level bar) was only applied at Jinja2 render time on the hub dashboard; streams that became silent after page load stayed blue. Fixed: HUB_TPL `_livePoll` now toggles `sc-silence` on each stream card at the same 150 ms cadence as the level bars.
- **Hub dashboard — getting-started HTML not rendered** — step descriptions in the first-time onboarding guide contained HTML (`<strong>` tags) but were rendered without `|safe`, so tag markup appeared literally. Fixed with `{{desc|safe}}`.
- **Hub site replica — 🔇 SILENCE badge not created dynamically** — the badge element was only injected by Jinja2 when `silence_active` was True at page load; streams that went silent after load had no element to show/hide. Fixed: `_livePoll` in HUB_SITE_TPL now creates the badge element on first silence detection and inserts it after the stream name.
- **DAB startup — premature consumer timeout** — `session.ready.wait(timeout=25)` fired before `_poll_mux` could announce ready on complex multiplexes (many services appearing gradually > 25 s). All consumers timed out simultaneously, killed the shared session, and restarted — cycling indefinitely. Timeout raised to 45 s to match the `_poll_mux` 45 s deadline.
- **DAB startup — `SdrBusyError` caused permanent exit** — if the previous DAB session was still releasing the USB device (up to ~2.5 s), the new session's `claim_dab_device` raised `SdrBusyError` and `_run_dab` returned permanently (5 s restart). Fixed: `SdrBusyError` is now retried within the outer startup loop (with a 2 s pause) rather than causing a permanent exit.
- **DAB startup — slow audio endpoint probe** — `_trig.read(32768)` blocked until 32 KB of MP3 data arrived (~2 s per stream at 128 kbps); with many parallel streams this serialised probe completions. Changed to `read(4096)` — enough to satisfy the `>= 4096` check with ~0.25 s of audio.

### Improved
- **DAB startup deadline** — outer retry loop deadline raised from 120 s to 180 s, giving slow-starting systems (marginal signal, large ensembles) more time to attach before the guarded thread restarts.

---

## [3.5.0] - 2026-04-04

### Visual overhaul — look & feel

- **Alert card glow** — site cards in ALERT state have a pulsing red glow (`alertCardPulse` animation); WARN cards have a static amber glow; OFFLINE cards fade to 82% opacity. Stream cards in alert/warn state gain matching subtle glows. Left border widened to 5px across all states.
- **Silence animation** — when a stream is silent, the level bar fill turns amber and pulses (`silencePulse` keyframe); the dB value text turns amber in sync. Applied in both hub dashboard and site replica page via `sc-silence` CSS class toggled by the live poll.
- **Uniform 8px level bars** — level bar track height normalised to 8px across all templates (was 6–7px, inconsistent). Visibly easier to read on high-DPI displays.
- **Primary button elevation** — `.btn.bp` gains `box-shadow: 0 2px 8px rgba(23,168,255,.25)` at rest, deepening on hover. All button definitions unified with `transition: filter .15s, box-shadow .15s`.
- **Tabular-nums globally** — `.sc-row span`, `.lbar-val`, `.sc-level` all get `font-variant-numeric: tabular-nums` so metric values don't shift width as they update. Level value and dB text gain `transition: color 200ms` for smooth colour changes.
- **Stream card hover lift** — `transform: translateY(-1px)` and deeper shadow on hover; transition added for border-colour, box-shadow, and transform.
- **Site card header depth** — subtle top-to-transparent gradient added to site card headers. Site name letter-spacing tightened (`-.01em`) for a cleaner headline feel.
- **AI status bar accent stripe** — `.aib` gains a 3px left border in the state colour (green/amber/red/blue/muted) instead of a plain background fill, giving a VS Code terminal-style indicator.
- **Settings tab active indicator** — active settings tab now shows a 2px accent-blue bottom border pseudo-element in addition to the background change.
- **Reports table** — alternating row backgrounds (`nth-child(even)`), hover highlight, and per-event-type left border colouring (red for alerts/faults/silence, blue for info events, green for recoveries).
- **`.btn-loading` shimmer** — new reusable CSS class adds an animated shimmer to any button during async operations (start/stop/approve/update). Used in `sendSiteCommand` and `pushSiteUpdate`.

---

## [3.4.167] - 2026-04-04

### Fixed
- **Hub site replica — stereo input 500 error** — `|min|max(0)|int` Jinja2 filter chain failed when applied to a float (Jinja2 `max` filter requires an iterable). Fixed to `[[val, 100]|min, 0]|max|int`. Stereo inputs with L/R data no longer crash the replica page.

### Improved
- **Hub dashboard — first-time onboarding empty state** — replaced bare "Waiting for sites" placeholder with a 4-step numbered getting-started guide: configure hub mode, install clients, set shared secret, wait for heartbeat.
- **Hub dashboard — pending approval redesign** — approval banner now shows "🔔 New site requesting access" heading, names the connecting site, explains what approving means, and has full-size Approve/Reject buttons.
- **Hub dashboard — alert CTA** — "🔍 View alerts →" pill appears in the summary bar when any sites are in alert state; links directly to Hub Reports.
- **Hub dashboard — status tooltips** — STALE pill, site health %, and latency all get descriptive `title=` tooltips.
- **Hub dashboard — "Open Dashboard" renamed to "📋 View Site"** — clearer label for the site replica link.
- **Hub dashboard — status badge tooltip** — OK/WARN/ALERT/OFFLINE badge explains each state.
- **Hub dashboard — drag hint hidden with one site** — "Drag site cards to reorder" only shown when 2+ sites are connected.
- **Hub dashboard — STALE badge tooltip** — explains the badge means heartbeat delayed, not necessarily down.
- **Hub dashboard — Wall Mode tooltip** — explains the mode is for large monitoring screens.
- **Hub dashboard — version mismatch tooltip** — explains client/hub version difference and suggests using Update.
- **Hub dashboard — always-visible stream mini-status** — AI ALERT/WARN/Learning pills, SIL/HISS/CLIP alert badges, and 🔇 Silent indicator shown on collapsed stream cards without needing to expand.
- **Hub dashboard — last event one-liner** — most recent history event shown below the level bar on each stream card.
- **Hub dashboard — search `/` shortcut** — pressing `/` anywhere on the hub page focuses the search input.
- **Hub dashboard — problems-only filter visual state** — "Show only problem sites" button turns amber when the filter is active.
- **Hub dashboard — comparator "FIND" renamed** — displays "Searching…" with an explanatory tooltip instead of the opaque "FIND" status.
- **Hub dashboard — Start/Stop button tooltips** — hover text explains what each command does.
- **Hub dashboard — alert ticker clickable** — clicking the alert ticker navigates to Hub Reports.

---

## [3.4.166] - 2026-04-04

### Improved
- **Hub site replica — silence indicator** — pulsing amber 🔇 SILENCE badge appears on stream cards in real-time (via the 150 ms live-levels poll) when a stream is in silence; hides automatically when audio resumes.
- **Hub site replica — refined sync indicator** — replaced "Updating in Xs" countdown text with a colour-coded pulse dot: green/normal while live, fast-pulse blue while syncing, amber when audio is paused, red if sync fails; "⚠ Live data paused" badge appears after 3 consecutive live-level poll failures.
- **Hub site replica — input enable/disable toggle** — each stream card now has an inline "✅ Enabled / ⏸ Disabled" toggle in the card body, queuing `enable_input` or `disable_input` commands to the remote client on the next heartbeat; state stays in sync via the 10 s metadata poll.
- **Hub site replica — stereo toggle** — inline 🔊 ON / 🔈 OFF stereo toggle on stream cards for HTTP/RTP/ALSA/Livewire inputs; queues `set_input_field` command to the remote client; both enable and stereo toggles update in `siteDataUpdate()`.
- **New `set_input_field` remote command** — generic hub→client command to set any whitelisted boolean field (`stereo`, `fm_force_mono`, `alert_on_silence`, `alert_on_hiss`, `alert_on_clip`, `ai_monitor`, `enabled`) on a named input; saves config and restarts monitoring. New hub endpoint: `POST /api/hub/site/<name>/input/set_field`.
- **Group 5 — SMTP port help text** — `<small>` help text added beneath the SMTP Port field in Settings: 587 STARTTLS (recommended) · 465 SSL · 25 plain (avoid).

---

## [3.4.165] - 2026-04-04

### Improved
- **Hub site replica — tooltip annotations** — added descriptive `title=` tooltips to latency, RTP loss/jitter, SLA, DAB SNR, FM pilot/signal/deviation, AI status bar, and AI learning progress bar on the replica page.
- **Hub overview — tooltips** — SLA %, RTP Loss, and AI status bar on the main hub dashboard now carry `title=` tooltips consistent with the replica page.
- **Settings — tooltips** — SMTP port field, webhook severity filter, and Low Bandwidth checkbox now have descriptive tooltips.
- **Smooth accordion transitions** — stream history and recent-alerts accordions animate open/close via CSS `max-height` transition instead of instant `display:none/block` toggling. Arrow indicator rotates 180° when expanded.
- **Always-visible alerts section** — recent-alerts accordion is always rendered; shows "No recent alerts recorded" empty state when empty. Toggle button is faded and disabled when there are no alerts.
- **No-streams empty state** — site replica and hub stream cards show a centred placeholder message when no streams are configured, rather than an empty card body.

---

## [3.4.164] - 2026-04-04

### Improved
- **Hub site replica — live level bars at 150 ms** — level bars on the hub site replica page now update at the same 150 ms cadence as the main hub dashboard, using `/api/hub/live_levels`. Includes peak-hold decay and L/R stereo bars. The 10 s metadata poll still handles AI status, RTP, SLA, counts, and system stats.

---

## [3.4.163] - 2026-04-04

### Improved
- **Hub site replica page — live AJAX update** — replaced `location.reload()` every 15 s with a fetch-based in-place update. Stream level bars, L/R bars, LUFS, AI status, RTP loss/jitter, SLA, site status dot/badge, alert/warn/ok counts, system stats (disk/CPU/RAM/uptime), and "Last seen" all update without a page reload. Scroll position, open accordions (DAB/FM stats), audio playback, and open source-management panels are no longer disrupted. New `/api/hub/site/<name>/data` JSON endpoint powers the updates (10 s interval; pauses while audio is playing).

---

## [3.4.162] - 2026-04-04

### Fixed
- **`/api/health` DB check** — `MetricsDB.query()` was called with wrong argument order (time-range style instead of `stream, metric, hours`), causing a 500 on every health poll. Fixed to `query("__health_check__", "level_dbfs", 0.01)`.

---

## [3.4.161] - 2026-04-04

### Fixed
- **Plugin proxy completeness** — `delete`, `patch`, and `put` shorthand methods added to `_PluginAppProxy` so all HTTP methods on plugin-registered routes are wrapped by the error-isolation catcher. Previously only `get`, `post`, and `route` were covered; `ptpclock`'s DELETE routes bypassed the wrapper.

---

## [3.4.160] - 2026-04-04

### Added
- **`/api/health` endpoint** — machine-readable subsystem status (database, monitor threads, hub heartbeat, plugins, disk). Returns `{"status":"ok"|"degraded"|"error", "issues":[...], "subsystems":{...}}`. Returns HTTP 503 when degraded/error, compatible with uptime-monitoring tools (UptimeRobot, Nagios, etc.)
- **Dashboard health banner** — all pages poll `/api/health` every 60 s and show a red banner listing active issues (stale streams, DB errors, hub disconnected, plugin errors). Clears automatically when resolved.
- **Plugin runtime isolation** — plugin route handlers wrapped in a per-plugin exception catcher. Unhandled errors return `500 {"ok":false}` JSON instead of crashing the Waitress worker thread. Errors logged to `_plugin_runtime_errors` and surfaced in `/api/health`.

---

## [3.4.159] - 2026-04-03

### Fixed / Improved
- **Atomic config writes** — `save_config`, alert-ack, alert-feedback, chain-notes, SLA, AI-feedback, and user-manager writes now use `_atomic_json_write` (temp file + `os.replace`) to prevent partial writes from corrupting persistent JSON state files.
- **Guarded thread auto-restart** — `_guarded_thread` wrapper added; `_run_input`, `_run_udp_inputs`, `_ai_loop`, `HubClient._loop`, `HubClient._live_loop`, and `_chains_monitor_loop` now restart automatically after an unexpected crash instead of dying silently.
- **Stream reconnect backoff** — HTTP stream reconnect sleep replaced with exponential backoff (5 s → 10 → 20 → … → 300 s cap), reset on first successful audio. DAB ffmpeg-launch failures also back off exponentially.
- **ffmpeg/subprocess cleanup** — DAB ffmpeg instance cleanup now uses `kill` + `wait(timeout=3)` via a `_ff_proc_ref` guard to ensure the subprocess is always reaped. FM and HTTP already had `try/finally` cleanup.
- **SQLite retry-with-backoff** — `MetricsDB._db_execute` and `_db_executemany` methods added; all execute calls in write/update/query paths now retry up to 5 times on "database is locked", with exponential sleep (0.1 s, 0.2 s, 0.4 s, 0.8 s) before propagating.
- **Hub heartbeat silent-spin fix** — `HubClient._loop` now logs a warning once when `hub_url` is not configured and sleeps 30 s per iteration (was `BASE_WAIT` = 5 s with no log). The existing outer catch-all exception handler already meets spec requirements.

---

## [3.4.158] - 2026-04-03

### Added
- **Stereo L/R Signal History charts** — `level_dbfs_l` and `level_dbfs_r` are now recorded in the metrics database at every flush, both for local streams (`_metrics_flush`, when `_audio_channels == 2`) and hub-relayed remote streams (`_flush_site_metrics`). The metric API allowlist includes both fields. Signal History dropdowns on the hub overview and replica page now show **L Channel dBFS** and **R Channel dBFS** options for FM and stereo streams, rendered in sky-blue and slate-blue.
- **Hub Reports L/R level column** — alert log entries now store `level_dbfs_l` / `level_dbfs_r` (when a stereo stream is active). The Level column in Hub Reports and the client status page alert history panel show the channel breakdown (`L -18 / R -19 dB`) below the mono bar for any event recorded on a stereo stream.
- **Listener plugin stereo L/R bars** (v1.1.6) — stream cards for stereo FM feeds now render a compact L / R dual-bar strip beneath the equaliser bars. Bars animate during poll updates at the normal poll cadence. The `stereo` flag in stream objects now also activates for `fm://` device-index streams (matching the behaviour added for hub cards in 3.4.136). L/R fill colours follow the same `_lvColor` scheme as the mono bars.

---

## [3.4.157] - 2026-04-03

### Fixed
- **Replica page missing L/R audio level bars** — stereo and FM streams showed only the mono RMS bar on the replica page; the L/R bar present on the hub overview and watch view was never added. The replica page now renders an L/R bar row beneath the main level bar for any stream where `level_dbfs_l` / `level_dbfs_r` are present (stereo inputs and FM inputs with pilot lock). Values refresh with the 15 s page auto-reload.

---

## [3.4.156] - 2026-04-03

### Fixed
- **FM deviation not shown on hub cards** — `fm_deviation_peak_khz` and `fm_over_ofcom` were included in the client heartbeat payload but never rendered in the hub overview stream card or the replica page FM/RDS stats block. Both templates now show a **Deviation** row (e.g. `±45.2 kHz`) in green/amber/red, with an ⚠ OFCOM warning when peak deviation exceeds ±75 kHz.

---

## [3.4.155] - 2026-04-03

### Fixed
- **Replica page play buttons playing the wrong card** — the click handler looked up the audio element by ID (`rep_live_{{ci}}`) but the element's ID used the loop index (`rep_live_{{i}}`). When `_client_idx` and the loop position differed, it found a neighbouring card's audio element and played that instead.

### Improved
- **Site-wide persistent mini-player** — `toggleLive`, `_closeHubMiniPlayer`, `_hubGuardPlay` and `_hmpActive` moved from the hub overview template into `topnav()` so they are globally available on every hub page. The replica page play buttons now use `data-action="live"` (same as hub overview) and route directly into the shared mini-player rather than per-card inline `<audio>` elements. Navigating from the replica page to Broadcast Chains, Reports, or any other hub page while audio is playing no longer stops playback — the mini-player persists via `sessionStorage` across all navigation. The replica page per-card `<audio>` elements and the `data-rep-live` click handler have been removed; the auto-refresh now checks the mini-player instead.

---

## [3.4.154] - 2026-04-03

### Fixed
- **Chain fault log and annotations missing from backup/restore** — three JSON files (`chain_notes.json`, `alert_acks.json`, `alert_feedback.json`) were never included in any backup ZIP. All three are now backed up in both the legacy browser-download route and the background save-to-disk job, and restored correctly.
- **metrics_history.db restore silently discarding fault log rows** — the restore code set `metrics_db._conn = None` without calling `.close()`, leaving the SQLite WAL and SHM journal files on disk. On next open SQLite applied the stale old-database WAL on top of the freshly restored file, rolling back or corrupting the chain fault log table. Fix: acquire `metrics_db._lock`, call `.close()` on the live connection, then delete any `-wal` and `-shm` journal files before extracting the restored DB.

---

## [3.4.153] - 2026-04-03

### Fixed
- **Users missing from backup/restore** — `signalscope_users.json` (all user accounts, roles, password hashes) is now included in every backup ZIP (config-only and full). On restore, the file is written with `chmod 600` and `user_manager.load()` is called immediately so the running instance picks up the restored accounts without a restart. Restore summary now includes "users restored" in the completion message.

---

## [3.4.152] - 2026-04-03

### Fixed
- **Backup/restore poll error hardening** — all four fetch calls in the backup/restore panel (start backup, poll backup, start restore, poll restore) now check `r.redirected || !r.ok` before calling `r.json()`. If the server redirects to the login page (session expired) or returns a non-2xx status, the error message now reads "Session expired — reload page" or "HTTP 404" instead of Safari's cryptic "The string did not match the expected pattern." JSON parse error.

---

## [3.4.151] - 2026-04-03

### Added
- **Restore from saved backup** — each saved backup in the on-disk list now has an "↩ Restore" button. Clicking it starts a background restore job (same polling pattern as the backup job) with live progress: "Restoring logger recordings: 1,234 / 8,640 (14%)". A confirmation dialog warns that config, databases and audio files will be overwritten and monitoring will restart. Progress bar and result message appear inline below the list.
- Core restore logic extracted into `_do_restore_from_zip(zip_path, progress_cb)` helper — both the browser-upload path (`POST /settings/restore`) and the new disk path (`POST /settings/restore/from_disk`) call it, eliminating duplicated code. New routes: `POST /settings/restore/from_disk`, `GET /settings/restore/job/<job_id>`.

---

## [3.4.150] - 2026-04-03

### Improved
- **Richer backup progress for audio phases** — the save-to-disk job now pre-scans each audio directory before adding files, so it knows both the file count and total bytes in advance. Progress messages show exact counts and data volumes: `Adding logger recordings: 1,234 / 8,640 (14%) · 4.21 GB of 31.50 GB`. A live progress bar appears below the message and grows as files are written. Current ZIP size on disk is shown at all times (`ZIP on disk: 3.8 GB`). Progress updates every 1 second (was: one static message per phase). Poll interval reduced from 2 s to 1 s for snappier feedback.

---

## [3.4.149] - 2026-04-03

### Added
- **Full backup with audio** — "Include audio" checkbox next to the "Save to disk (SSH)" button. When checked, the backup ZIP also includes `alert_snippets/` (all alert WAV/MP3 clips) and the logger recordings directory (resolved from `plugins/logger_config.json` → `rec_dir`, defaulting to `plugins/logger_recordings/`). Audio files are stored uncompressed (`ZIP_STORED`) for speed. A full backup with months of recordings can be many GB and take several minutes.
- **Background job with progress polling** — the save-to-disk backup now runs in a background thread. The browser receives a `job_id` immediately and polls `GET /settings/backup/job/<id>` every 2 seconds, showing live progress ("Writing config…", "Snapshotting metrics database…", "Adding logger recordings…"). The button stays disabled with a spinner until the job completes or errors.
- **Restore includes audio** — `settings_restore` now recognises `alert_snippets/` and `logger_recordings/` entries in a full backup ZIP and restores them to the correct locations (alert clips → `BASE_DIR/alert_snippets/`; recordings → the current `rec_dir` configured in `logger_config.json`). The restore summary reports counts: "N alert clip(s) restored", "N logger recording(s) restored".
- New route: `GET /settings/backup/job/<job_id>` — returns `{status, progress, filename, path, size_mb, error}`.

---

## [3.4.148] - 2026-04-03

### Added
- **Save backup to disk (SSH)** — new "💾 Save to disk (SSH)" button in Settings → Maintenance alongside the existing Download button. Clicking it generates the backup ZIP on the server itself (in `BASE_DIR/backups/`) without sending it through the browser, completely avoiding nginx proxy timeouts that occur when streaming large archives. On completion the UI shows the full server path and an `scp` command to copy it off. A list of previously saved backup files (path, size, date) is shown below with delete buttons. Three new routes: `POST /settings/backup/save`, `GET /settings/backup/list`, `POST /settings/backup/delete`.

---

## [3.4.147] - 2026-04-03

### Changed
- **FM Force Mono is now a persisted input config option** — moved from a runtime-only toggle button (lost on restart) to a proper checkbox in the input configuration page (Settings → Inputs → Edit). The setting is saved to `config.json` and applied permanently on every monitoring start. When active, a small notice ("🔇 Force Mono active") appears on the client status page with a link to the input config to change it. The runtime API endpoint (`POST /api/fm/force_mono/<idx>`) and its JS toggle handler have been removed.

---

## [3.4.146] - 2026-04-03

### Fixed
- **FM stereo R-channel distortion (stale SOS filter state on blend re-entry)** — the 3.4.144 stereo blend fix introduced a deeper regression: `_mpx_to_stereo` was only called when `_stereo_blend > 0.0`. When blend was 0 (poor signal), the SOS filter states (`zi_lpr`, `zi_pilot`, `zi_lmr`) were never updated. On the first call after blend rose above 0 the stale state produced a transient spike in `pilot_peak`, causing `pilot_n = pilot / pilot_peak` to have a very small amplitude, adding a DC offset to `sub38 = 2·pilot_n²−1`. That DC term contaminated `lmr` with L+R content via `lmr_raw = samp * sub38`, so the R channel (`lpr − lmr×2`) received distorted programme. This explains why one dongle "worked for a bit then went bad again" after 3.4.144 was deployed.
  - **Fix**: `_mpx_to_stereo` now takes **no** `blend` parameter and always computes raw `lmr * 2.0`. It is called on **every** MPX block whenever `cfg._fm_stereo` is True, keeping all filter states continuously updated regardless of pilot quality. Blending toward mono is applied **externally** in the calling code: `L_out = blend*L_48 + (1−blend)*mono_48`, `R_out = blend*R_48 + (1−blend)*mono_48`. `mono_48 = (L+R)/2` is the perfect noise-cancelling blend reference because L-R noise terms cancel in the sum.

### Added
- **Force Mono button** on the FM / RDS stats panel (client status page). Pressing it sets `cfg._fm_force_mono = True`, bypassing stereo output entirely regardless of pilot SNR — both channels receive the clean L+R mono mix. Press again to re-enable stereo auto-detection. State resets when monitoring is restarted. Useful for marginal-coverage dongles where even blended stereo remains audibly noisy. The button appears red (🔇 Mono forced) when active.
- **FM Level display fixed to dBFS scale** — the JavaScript poll update was incorrectly displaying the FM signal level in dBm units with dBm-appropriate colour thresholds (−70/−85). Corrected to dBFS label and thresholds (−18/−28) to match the Jinja2 template and the 3.4.145 log-message fix.

---

## [3.4.145] - 2026-04-03

### Fixed
- **FM fault log messages showed "dBm" instead of "dBFS"** — `_fm_signal_dbm` is the RMS of the normalised float32 MPX samples (range 0→full scale = 0 dBFS), not an absolute RF power reading. Three alert log messages for STUDIO_FAULT, STL_FAULT and TX_DOWN incorrectly appended "dBm"; corrected to "dBFS". The on-screen dashboard display already showed "dBFS" correctly.

---

## [3.4.144] - 2026-04-03

### Fixed
- **FM stereo right channel distorted on weak/marginal signals** — on fringe coverage or multipath conditions the pilot SNR is low, producing a noisy `sub38` 38 kHz carrier (via `2·cos²(θ)−1` frequency doubling). That noise gets multiplied through the full MPX and is *subtracted* in the R channel matrix (`L+R − noisy·L-R`). Because subtraction amplifies relative noise more than addition, the R channel becomes a distorted mess while the L channel still sounds like programme. This is a known FM tuner problem; the standard solution is **stereo blend/fade**:
  - `_fm_stereo_blend` — new per-stream attribute (0.0 = full mono, 1.0 = full stereo) updated each MPX block from the pilot FFT SNR measurement
  - Below 14 dB pilot SNR: blend = 0.0 → pure mono (L channel copy to both sides)
  - Above 26 dB pilot SNR: blend = 1.0 → full stereo
  - 14–26 dB: blend rises linearly — smooth fade, no hard switching
  - Block-level pilot amplitude threshold raised from 0.005 to 0.02 (requires a cleaner pilot before attempting sub38 generation)
  - `_mpx_to_stereo` now accepts a `blend` parameter and scales the L-R component by it before the matrix; when blend reaches 0.0 both channels receive the identical L+R mono signal

---

## [3.4.143] - 2026-04-03

### Fixed
- **Backup button fails for large databases** — the backup route built the entire ZIP archive in a `BytesIO` buffer in RAM. A large `metrics_history.db` (e.g. 500 MB+) caused the process to exhaust available memory and the download to fail silently. Fix: the ZIP is now written to a named temp file on disk using `zipfile.ZipFile(tmp_path, "w", ...)`, then streamed to the browser via `send_file` with an `after_this_request` cleanup hook. There is no practical size limit.
- **logger_index.db not included in backup/restore** — the Logger plugin's SQLite database (`plugins/logger_index.db`, containing all segment metadata, SHA-256 checksums, export audit log, and metadata) was not included in the Settings → Backup ZIP. It is now backed up using the same WAL-safe `sqlite3.backup()` hot-copy as `metrics_history.db`. Restore now also handles `logger_index.db` from the ZIP.
- **Restore reads entire ZIP into RAM** — `settings_restore` did `f.read(512 * 1024 * 1024)` before inspecting the ZIP, loading the whole file into memory. Fix: the upload is saved to a temp disk file via `f.save(tmp_path)` first, then opened with `zipfile.ZipFile(tmp_path)`. Database entries within the ZIP are extracted directly to disk via `zf.extract()` rather than `zf.read()`.

---

## [3.4.142] - 2026-04-03

### Improved (Logger plugin v1.6.0 — compliance hardening)
- **ffmpeg watchdog** — a background watchdog thread monitors each recorder's stdin write timestamp. If no audio data is written for 30 seconds (hung ffmpeg, codec deadlock, network stall) the process is killed and the recording loop restarts cleanly. Previously a hung ffmpeg would block a recorder thread forever with no alarm.
- **Gap detection** — at the start of each 5-minute segment, the recorder checks whether the immediately preceding slot exists in the database. If it doesn't (and this recorder has previously written segments), a `quality='gap'` sentinel row is written. On startup/restart, `_recover_startup_segments()` scans today's expected slots and writes gap markers for any that have no audio file on disk. Gap blocks appear red (dashed border) on the timeline so engineers immediately see coverage holes.
- **Disk space alerts** — the maintenance loop now checks free disk space on every recording root. Below 5 GB free: warning logged every hour. Below 500 MB free: `_disk_critical` flag set, recording paused with a critical log entry, and the Logger UI shows a prominent red banner. The status API (`/api/logger/status`) now returns `disk_free_bytes`, `disk_warning`, and `disk_critical`.
- **SHA-256 segment checksums** — after each 5-minute segment is successfully written, a SHA-256 hex digest is computed and stored in the `segments` DB table (`sha256` column, added via `ALTER TABLE` migration for existing installations) and written as a `.sha256` sidecar file alongside the audio. The filesystem fallback in `_get_segments()` reads `.sha256` sidecars for segments not yet in the DB.
- **Export audit log** — every export (local, hub, and mobile) is recorded in a new `export_audit` SQLite table with timestamp, username (from Flask session), stream, date, time range, format, client IP, and hub/local flag. New route `GET /api/logger/audit` returns the log as JSON (default 200 entries, max 1000).
- **Per-stream silence detection config** — `silence_threshold_dbfs` (default −55 dBFS) and `silence_min_duration_s` (default 1.0 s) are now configurable per stream in `logger_config.json` and exposed via the settings API. Enables tuning sensitivity for talk-radio vs music streams.
- **`metadata_log` auto-pruning** — the maintenance loop now deletes `metadata_log` rows older than each stream's `retain_days`. Previously the table grew indefinitely; on a busy station with 30-second polling it accumulates ~2,880 rows/day/stream.
- **Hub reconnect resilience** — `_hub_logger_poller` now uses exponential backoff (2 → 4 → 8 → … → 60 s) on connection errors and resets to 2 s on the first successful poll. The thread never exits regardless of hub availability, so local recording always continues and hub sync resumes automatically.
- **Startup segment recovery** — `_recover_startup_segments()` runs 3 seconds after startup in a background thread. It registers any audio files present on disk that are not in the DB (written during a previous crash) and inserts gap markers for expected time slots that have no corresponding file.

---

## [3.4.141] - 2026-04-03

### Fixed
- **Morning Report only showing chains that have had historical faults** — `all_chain_names` was built from `chain_fault_log` (SQLite) and `alert_log` events only. A newly-configured chain, or one that has been running cleanly with no faults, had zero entries in either source and was completely absent from every section of the report. Fix: `_generate_report()` now also reads `monitor.app_cfg.signal_chains` (the live Broadcast Chains configuration) and adds every configured chain name to `all_chain_names`. Chains with no fault history appear in the health table as "✓ None — 100% on-air" as expected.

### Improved (Morning Report plugin v1.1.0 — non-technical UX)
- **Plain-English headline banner** added at the top of the report: green "Clean day" banner when there are no faults; amber/red summary with interruption count and total off-air time when faults exist.
- **Plain-language section and column headers**: "Audio Interruptions" (was "Total Faults"), "Time Off-Air" (was "Downtime"), "On-Air %" (was "SLA Yesterday"), "Usual Daily Avg" (was "7-day Avg/day"), "Compared to Usual" (was "Trend"), "Longest Gap" (was "Longest Outage").
- **Traffic-light trend indicators**: the "↑/→/↓" arrows are replaced with colour-coded pill badges — 🔴 "Worse than usual", 🟢 "Better than usual", ⚪ "Normal".
- **Uptime percentage** replaces raw downtime minutes in the chain table — shows "100.0%" for clean chains and a red percentage for chains with outages. Tooltip shows the exact percentage.
- **Human-readable longest outage** — "3m 12s" / "1h 4m" instead of raw minutes.
- **Plain-English pattern messages** with emoji indicators (✅ clean, ⚠️ streak broken, 🔴 above average, 🕐 clustering, 🔁 recurring, ✅ overnight clean).
- **Stream Quality section** retitled "Live Stream Quality"; field labels translated — "Loudness level" (was "LUFS-I"), "Network packet loss" (was "RTP Loss"), "Audio glitches" (was "Glitches"); zero values shown in green, problem values in amber/red.
- **Tooltip guide** added under each section explaining the metrics in plain English for non-technical readers (loudness target range, what packet loss means, etc.).
- **At a Glance** card labels updated: "Audio Interruptions", "Audio Chains Monitored", "Minutes Off-Air (total)", "Best Performing Chain", "Most Issues".

---

## [3.4.140] - 2026-04-03

### Fixed
- **Soundcard (ALSA) inputs stuck in AI training forever** — `_run_sound()` wrote every audio chunk to `_stream_buffer` but never to `_audio_buffer`. The AI loop reads exclusively from `_audio_buffer`, so for soundcard inputs it always found an empty deque, skipped the input, and `ai.feed()` was never called. After 24 hours `_finish_learn()` ran with zero accumulated samples, failed the `MIN_TRAINING_SAMPLES` check, and called `_begin_learn()` again — resetting the timer and repeating indefinitely. Fix: one line added to `_run_sound()` to append each chunk to `_audio_buffer` alongside the existing `_stream_buffer` append. All other input types (FM, DAB, HTTP, RTP) already populated both buffers.

---

## [3.4.139] - 2026-04-03

### Fixed
- **Hub Play button and level meters blocked by fetch storm on page load** — Root cause identified via browser performance profiling: `DOMContentLoaded` fires at ~1.6 s but immediately triggers two bulk fetch blasts: 30 timeline canvas loads (`setTimeout(forEach(_tlLoad), 600ms)`) and 30 trend API calls (`setTimeout(_loadTrends, 1200ms)`). With 60+ concurrent requests hitting the server, the browser's connection pool is saturated. Any subsequent `fetch()` call — including the `_hubGuardPlay` auth pre-flight needed to start the player — queues behind them and takes 75–106 seconds to complete. The page shows `window.load` at ~91 seconds, confirming the fetch storm.

  Three changes:
  1. **Timeline canvases are now lazy** — the `setTimeout(forEach(_tlLoad), 600)` bulk load is removed. Timelines now load only when a stream's detail section is opened (`_loadDetailResources()` called in the expand click handler and for any already-open sections on DOMContentLoaded).
  2. **Trend loads are now staggered** — `_loadTrends()` no longer fires all 30 fetches simultaneously. It schedules them 800 ms apart (stream 0 at t=8 s, stream 1 at t=8.8 s, etc.), giving the live polls and Play button plenty of clear runway. The 5-minute refresh interval is unchanged.
  3. **`_loadDetailResources(detail)`** — new helper called on expand/expand-all. Loads all unloaded `canvas.sc-tl` (guarded by `data-loaded` attribute) and fetches trend for that stream.

---

## [3.4.138] - 2026-04-03

### Fixed
- **Hub Play button broken after 3.4.137** — Two regressions introduced by the previous commit:
  1. The `_startLiveView()` call added to the `<head>` script fired 150 ms live-level polls before any HTML was in the DOM. On page load this flooded the browser connection pool with concurrent fetches, so the `_hubGuardPlay` auth pre-flight fetch queued behind them and never resolved — the player callback never fired. Reverted: `_startLiveView()` is called only in `DOMContentLoaded` where it was before.
  2. The trend-reapply `hubRefresh` wrapper (`var _origHubRefresh = hubRefresh; hubRefresh = function(){ return _origHubRefresh.apply(this,arguments).then ? … }`) called `.then` directly on the return value of `hubRefresh`, which returns `undefined`. This threw `TypeError: Cannot read properties of undefined (reading 'then')` on every single structural refresh, generating a noisy exception storm in the console. The broken wrapper was removed — the second wrapper (lines below) already handles trend reapplication correctly via `.finally`.

---

## [3.4.137] - 2026-04-03

### Fixed
- **Hub dashboard Play button and level meters now work immediately on page load** — Three issues were delaying interactivity until the entire page had finished loading:
  1. `.site-card.skeleton::after` covered the full card with `inset:0` and no `pointer-events:none`, physically blocking all clicks (including the ▶ Live Play button) until `DOMContentLoaded` removed the skeleton class. Fixed by adding `pointer-events:none` to the skeleton overlay — clicks now pass through immediately.
  2. `_startLiveView()` was only called from `DOMContentLoaded`, so level bar polling never started until every last byte of HTML was parsed. Fixed by calling `_startLiveView()` immediately at the end of the `<head>` script block — polling fires during page parse, meters animate as soon as elements are painted. The `DOMContentLoaded` call is harmlessly no-op'd by the `if (_liveActive) return` guard.
  3. Every stream with a `nowplaying_station_id` emitted its own inline `<script>` block inside the body HTML, each one pausing the HTML parser to compile and execute JS. With many streams these added up. Fixed by replacing all per-stream inline scripts with a single `data-rpuid` attribute on the `.np-strip` div, and one batched `<script>` at the end of the body that queries all `[data-rpuid]` elements and starts their polling loops in a single pass.

---

## [3.4.136] - 2026-04-03

### Fixed
- **FM stereo L/R bars now appear on hub dashboard, Watch view, and client status page** — All three dashboard templates previously gated the L/R bar DOM elements with `{% if inp.stereo %}` / `{% if s.get('stereo') %}`. FM RTL-SDR inputs never set the `stereo` config flag (it's a user checkbox only used by ALSA/DAB/HTTP/RTP inputs), so the DOM elements were never rendered even after the stereo decoder produced real L/R data. Fix: each template condition now also fires for FM inputs (`inp.device_index.lower().startswith('fm://')` / `dtype == 'fm'`). The L/R bar wrapper starts `display:none` and is made visible by the live-update JS as soon as the first real L/R measurement arrives — so the bars only appear once the stereo decoder has actually locked onto the pilot tone.

---

## [3.4.135] - 2026-04-03

### Added
- **FM input stereo decoding** — RTL-SDR FM monitoring inputs now decode true stereo audio when a stereo pilot tone is detected (pilot SNR ≥ 8 dB). A stateful 4th-order Butterworth SOS filter chain bandpasses the 19 kHz pilot, frequency-doubles it to a 38 kHz subcarrier (cos(2θ) = 2cos²(θ)−1), demodulates the DSB-SC L-R signal, and matrices it with L+R to recover L and R. L/R are resampled to 48 kHz and stored in `_audio_buffer` as stereo-interleaved chunks; `_stream_buffer` receives the mono mix for relay/comparator/chain clips. L/R PPM levels are computed and sent at 5 Hz. Falls back to mono when no pilot or scipy unavailable.
- **FM stereo live streaming and relay** — `stream_live()`, the hub relay writer, and the WAV clip download now all stream stereo for FM inputs when a pilot is detected. No config change needed — stereo is automatic.

---

## [3.4.134] - 2026-04-03

### Plugins
- **Logger v1.5.32** — fixed root cause of hub catalog never loading. `checkHubMode()` was called at line 3784 in the IIFE init block, but `_catSpinFrames`, `_catSpinTimer`, and `_catSpinIdx` are declared via `var` at line 3807+. JavaScript `var` hoisting only hoists the declaration, not the assignment — so `_catSpinFrames` was `undefined` when `_startCatalogSpinner` ran, causing `_catSpinFrames[0]` to throw `TypeError: Cannot read properties of undefined (reading '0')`. Fix: moved the `if(_IS_HUB_NODE){ checkHubMode(); }` call to after all hub-mode variables and functions are fully declared (after line 3900).
- **Logger v1.5.31** — two follow-on fixes: (1) `_startCatalogSpinner` guard strengthened to `if(sel && sel.options && sel.options[0])` — the previous `if(sel && sel.options[0])` still threw if `sel.options` itself was undefined (non-select element or browser quirk). (2) `_CATALOG_STALE_S` increased from 7200 s (2 hours) to 30 days — catalog entries are written on every 5-minute segment save and on startup seeding, but if recording stops and the hub is browsed more than 2 hours later all entries were silently filtered as stale, causing the hub catalog to show empty even though recordings existed on disk.
- **Logger v1.5.30** — fixed IIFE crash that broke all UI interactions once playback started. `_startCatalogSpinner` accessed `sel.options[0].textContent` after checking `sel.options.length <= 1` — when the select had zero options (before `loadStreams` ran), `options[0]` was `undefined` and threw a TypeError. Because this is called from `checkHubMode()` inside the synchronous IIFE init block, the uncaught exception stopped the IIFE from continuing — the play button click listener, day-bar click listener, and spacebar keydown handler were never attached. The audio element's own listeners (set up earlier by `setupAudio()`) still worked, so a segment could be started by clicking a block, but pause, timeline seek, and spacebar all did nothing. Fix: changed guard to `if(sel && sel.options[0])` and wrapped the `checkHubMode()` call in try-catch so any future errors in that path cannot crash the init block.
- **Logger v1.5.29** — fixed two regressions introduced in v1.5.28: (1) `var _IS_HUB_NODE` was declared 20 lines after `if(_IS_HUB_NODE){ checkHubMode(); }` — JavaScript hoisting meant the variable was `undefined` at the point of the check, so `checkHubMode()` never ran even on hub nodes, causing the hub to always show "Select a stream." (2) `checkHubMode()` was still called unconditionally on every node — now correctly gated on `_IS_HUB_NODE` (server-rendered `true`/`false` via Jinja2), so local/client nodes never fire the catalog spinner at all.
- **Logger v1.5.28** — fixed hub Logger opening with "Select a stream" and never populating. Two bugs: (1) `checkHubMode()` was called on every node type — on a non-hub node the `/api/logger/hub/catalog` endpoint returns 404 which was previously treated as a retryable error, spinning for up to 40 seconds before giving up. Fixed by inspecting the HTTP status code before parsing JSON — 404 exits immediately. (2) `_hub_logger_catalog` was in-memory only; after a hub restart the catalog was empty until all clients re-registered (up to 60 s). Fixed by persisting the catalog to `hub_catalog_cache.json` in the plugin directory on every client registration and loading it at startup — the catalog is available immediately on the first page visit after a hub restart. Retry window also extended from 40 s to 90 s to safely cover the 60 s client re-registration interval.

---

## [3.4.134] - 2026-04-03

### Fixed
- **Hub Play button loads forever when not logged in** — `<audio>` elements silently follow login redirects and receive HTML with no error feedback. Added `/api/auth_ping` (returns 200/401 JSON, never redirects) and `_hubGuardPlay(fn)` on the hub dashboard. Unauthenticated users clicking Play are now redirected to `/login?next=<current page>` instead of seeing an infinite loading state. Main monitor page and Broadcast Chains audio are unaffected.

---

## [3.4.133] - 2026-04-02

### Fixed
- **Live audio Play button silently fails when not logged in** — when accessing the hub from outside and not authenticated, clicking any Play button caused the `<audio>` element to silently follow the login redirect, receive an HTML page instead of audio, and fail with no feedback. The user saw a working Play button but heard nothing and was never prompted to log in. Root cause: `<audio src=...>` follows 302 redirects automatically and cannot surface the resulting HTTP status code via `onerror`. Fix: added `/api/auth_ping` (returns 200/401 JSON, never redirects) and a `_guardPlay(fn)` helper that pre-checks auth before starting audio. If the user is not logged in, `_guardPlay` redirects them to `/login?next=<current page>` instead of silently failing. Applied to both `toggleLive` (main monitor page) and `_startListen` (chains/hub mini-player). *(Note: this build contained a regression — see 3.4.134.)*

### Plugins
- **FM Scanner v1.0.4** — fixed two state-machine stuck states that required disconnect/reconnect to recover: (1) Re-tune failure (`doTune()` returning `!ok`) only set `freqSub` to "Tune failed" but left `_state` in `'streaming'`/`'connecting'` with a stale audio slot — the stream appeared live but no audio was flowing. Fix: on any tune failure, call `doStop()` then auto-restart to the requested frequency after 800 ms (mirrors what the user was doing manually). (2) Band scan failure showed a blocking `alert()` then left the status as "Idle — pick a site and connect" with no indication of why or what to do next. Fix: replaced `alert()` with an inline red message in the scan-status area ("Scan failed: … — press Connect to resume") that auto-clears after 6 seconds.

---

## [3.4.132] - 2026-04-02

### Fixed
- **Livewire/AES67 jitter reads worse after adding more streams** — the shared multicast socket receiver batches packets from all groups together. When 5+ streams send packets simultaneously they queue in the kernel buffer; `time.monotonic()` was called when userspace processed each packet, not when it arrived. For the last packet in a batch, the measured inter-arrival time included the processing delay of all the packets before it, inflating the jitter EWMA even though the network was fine and no audio was affected. Fix: `SO_TIMESTAMPNS` (Linux kernel timestamping) is now enabled on the shared multicast socket. The nanosecond receive timestamp is extracted from the `recvmsg` ancillary data alongside the existing `IP_PKTINFO` group demux. `_handle_udp_packet` uses the kernel timestamp when provided, falling back to `time.monotonic()` for unicast sockets and non-Linux kernels. Also added a clock-domain reset guard so the first packet after the switch from monotonic to wall-clock doesn't produce a spurious huge inter-arrival value.

---

## [3.4.131] - 2026-04-02

### Fixed
- **Fault holdoff cancels ad break timer** — `fault_holdoff_seconds` was applied unconditionally to all chain faults including ad break candidates. Because the holdoff fires first and delays the fault entering the ad break confirmation window (`min_fault_secs`), the ad break timer only started counting after the holdoff expired — effectively adding the two values together and breaking ad break learning/p95 logic. Fix: ad break candidates (where a mixin node is healthy downstream) bypass the holdoff entirely so the confirmation window starts immediately as it always did. The holdoff only applies to genuine non-ad-break faults such as brief segue silences.

---

## [3.4.130] - 2026-04-02

### Fixed
- **HTTP stream inputs freeze permanently on stream loss** — when an HTTP/HTTPS input stream dropped, ffmpeg entered its internal reconnect retry loop and held the stdout pipe open indefinitely without producing audio. The monitoring loop's `select()` returned no data each second, but since ffmpeg never exited there was no EOF to break the inner loop. The result was levels permanently frozen at the last real measurement until the monitor was manually restarted. The existing `_HTTP_STALL_SECS` detection correctly identified the stall and cleared `_has_real_level`, but then just issued `continue` — waiting forever for either data or ffmpeg to exit, neither of which came. Fix: when the stall threshold (now 8 s) is exceeded, the stuck ffmpeg process is killed and the inner loop is broken. The outer reconnect loop then waits 5 s and starts a fresh ffmpeg process. Total worst-case downtime: ~13 s. Also reduced `-reconnect_delay_max` from 10 to 5 since application-level restart now takes over before ffmpeg's own retry would complete.

### Plugins
- **Meter Wall v1.1.2** — stereo streams now show split L and R level bars side by side. Stereo flag and per-channel dBFS values are read from `/api/hub/live_levels` at the 150 ms fast-poll cadence — same rate as the main RMS bar. Cards switch between mono and stereo layouts automatically as live data arrives. Peak hold and decay are tracked independently per channel. `/api/meterwall/data` also returns `stereo`, `level_dbfs_l`, `level_dbfs_r` for the initial render.
- **Listener v1.1.5** — station cards show a `◈ STEREO` badge for stereo streams; the now-playing bar appends `· ◈ STEREO` to the metadata line.
- **Logger v1.5.20** — records stereo MP3 when the stream is configured for stereo and reporting `_audio_channels == 2` at segment start; channel count locked for the full segment. Catalog stores `n_ch`; hub play route passes it to the relay pusher which uses the correct ffmpeg channel flags and per-channel chunk sizing. Hub browser PCM pump decodes stereo interleaved data correctly. Stereo streams marked `◈` in the stream selector dropdown.
- **Logger v1.5.21** — display timezone is now auto-detected from the server system clock (`datetime.now().astimezone()`), which respects DST transitions automatically. The manual "Display UTC Offset" number input has been removed from Settings. The timezone label (e.g. `UTC+1 (BST)`) is shown as read-only info and refreshes on every page load. `parseFloat` replaces `parseInt` when applying the offset so half-hour zones (e.g. India UTC+5:30) are handled correctly.
- **Logger v1.5.22** — UI overhaul for non-technical users: day bar doubled in height (72 px) for a larger scrub target; hour grid blocks taller (26 px); hover time-tip on the day bar shows local wall-clock time as the mouse moves; scroll-wheel zoom on the timeline (1×–16×, zoom centred on mouse position) replacing the fixed 1×/2×/4×/8× buttons with a continuous +/− pair; scrub progress bar removed (day bar is now the visual playhead); playback clock enlarged; "Mark In/Out" buttons renamed to "Mark Start/End"; dismissable explainer hint strip below the day bar ("Click to jump · Right-click to set clip markers · Scroll to zoom") that auto-fades after 20 s and respects sessionStorage dismissal.
- **Logger v1.5.23** — all hover/title timestamps on timeline blocks, track band spans, and mic band spans now apply the local UTC offset so they match the wall-clock time shown on the hour labels. Previously all tooltip times were raw UTC regardless of the BST/timezone setting.
- **Logger v1.5.24** — playhead drag: the green head marker on the day bar now has a 16 px hit area with a circular handle and `ew-resize` cursor; mousedown on it initiates a drag-to-seek without interfering with right-click markers. Direct-mode export: `fmt=raw` is now accepted and maps to stream-copy (no re-encode, same as before the UI default was changed); ffmpeg stderr is now surfaced in the error message instead of the opaque "ffmpeg failed". Export format dropdown default restored to Raw (fast).
- **Logger v1.5.25** — fixed export failing with `[mp3] Invalid audio stream` when the stream is recorded in AAC or Opus format. Root cause: `_EXPORT_FMTS["mp3"]` defaulted to `-c copy` (stream-copy), which only works when the source is already MP3. Fix: the MP3 entry now defaults to `-c:a libmp3lame -b:a 128k` (transcode); the existing stream-copy fast-path is retained when source is confirmed MP3. "Raw (fast)" export now uses `_RAW_CONTAINER_MAP` to detect the actual source codec and serves it in the matching container (MP3→mp3, AAC→adts, Opus→ogg) with `-c copy`, rather than blindly remapping to MP3 format.
- **Logger v1.5.26** — fixed hub Logger requiring multiple page visits before sites and recordings appear. Three root causes: (1) `checkHubMode()` returned immediately on empty catalog with no retry — if opened before the first client heartbeat (~10 s after startup) the dropdown stayed blank. Fix: retries at 3/5/8/15/30 s intervals until the catalog populates. (2) Race between `checkHubMode()` and `loadStreams()` — both overwrote the stream selector; if `loadStreams()` resolved after the hub catalog populated it wiped the entries. Fix: `loadStreams()` guards on `_isHubCatalogPopulated` and returns early if the hub catalog owns the dropdown. (3) No auto-select after catalog populated — user had to manually click a stream. Fix: `_populateCatalogSel()` auto-selects the first stream and calls `loadDays()`. Catalog also refreshes every 30 s so newly connected sites appear without a page reload.
- **Logger v1.5.27** — while waiting for sites to register, the stream selector now shows "finding streams…" and an animated spinner with "Connecting to recording sites…" / "Waiting for sites to connect… (N)" appears below it so non-technical users can see progress. Retry delays tightened to 2/3/5/10/20 s (was 3/5/8/15/30). After all retries exhausted, shows "No connected sites found. Check client nodes are running." instead of silently doing nothing. Three root causes: (1) `checkHubMode()` returned immediately on empty catalog with no retry — if opened before the first client heartbeat (~10 s after startup) the dropdown stayed blank. Fix: retries at 3/5/8/15/30 s intervals until the catalog populates. (2) Race between `checkHubMode()` and `loadStreams()` — both overwrote the stream selector; if `loadStreams()` resolved after the hub catalog populated it wiped the entries. Fix: `loadStreams()` guards on `_isHubCatalogPopulated` and returns early if the hub catalog owns the dropdown. (3) No auto-select after catalog populated — user had to manually click a stream. Fix: `_populateCatalogSel()` auto-selects the first stream and calls `loadDays()`. Catalog also refreshes every 30 s so newly connected sites appear without a page reload. Root cause: `_EXPORT_FMTS["mp3"]` defaulted to `-c copy` (stream-copy), which only works when the source is already MP3. Fix: the MP3 entry now defaults to `-c:a libmp3lame -b:a 128k` (transcode); the existing stream-copy fast-path is retained when source is confirmed MP3. "Raw (fast)" export now uses `_RAW_CONTAINER_MAP` to detect the actual source codec and serves it in the matching container (MP3→mp3, AAC→adts, Opus→ogg) with `-c copy`, rather than blindly remapping to MP3 format.

---

## [3.4.129] - 2026-04-02

### Fixed
- **Hub L/R stereo bars don't live-update** — `level_dbfs_l` and `level_dbfs_r` were absent from the slim 5 Hz live-push frame that the client POSTs to `/api/v1/live_push`, and absent from the `_LIVE_STREAM_FIELDS` merger list on the hub. L/R values only arrived via the 10-second heartbeat, so the bars appeared frozen between heartbeats. Fix: both fields are now included in the `_live_loop` frame (sent only when `_has_real_level` and `_audio_channels == 2`) and in `_LIVE_STREAM_FIELDS` so the merger updates `hub_server._sites` on every live push. L/R bars now update at the same 150 ms cadence as the main RMS bar.

---

## [3.4.128] - 2026-04-01

### Fixed
- **Hub "Play" button produces no audio for stereo streams** — two code paths both had the same root cause: sending mono PCM data to an ffmpeg process started with `-ac 2` (stereo). When ffmpeg receives half the expected bytes per time unit it either plays at half speed or produces a corrupt MP3 stream that browsers silently refuse to decode.
  - **`stream_live` (`/stream/<idx>/live`)**: `_live_buf()` fell back to `_stream_buffer` (mono chunks) whenever `_audio_buffer` was momentarily empty, even though ffmpeg was already running with `-ac 2`. Fix: when `_live_n_ch == 2`, `_live_buf()` now always returns `_audio_buffer` regardless of whether it is currently empty. The writer loop simply waits for the first stereo chunk to arrive — this is safe because `_audio_buffer` fills within one CHUNK_DURATION (0.5 s) of stream connect.
  - **Hub relay (`kind="live"` in `_push_audio_request`)**: the relay writer always read from `_stream_buffer` (mono) with `ffmpeg -ac 1`, regardless of whether the stream was configured for stereo. Hub-relayed audio was always mono — browsers playing a stream tagged as stereo received a mono MP3 but the audio element sometimes stalled on MIME/format inconsistency. Fix: relay now checks `inp.stereo and inp._audio_channels == 2`; if true, reads from `_audio_buffer` with `ffmpeg -ac 2 -b:a 256k` (mirrors `stream_live`). Added `_relay_live_buf()` helper that — like the fixed `_live_buf()` — never mixes channel counts.

---

## [3.4.127] - 2026-04-01

### Fixed
- **Stereo stream listen plays at half speed** — the live stream relay (`/stream/<idx>/live`) and WAV download (`/stream/<idx>/audio.wav`) read from `_stream_buffer`, which now stores stereo interleaved chunks (2× the samples). The browser / ffmpeg received those chunks with `-ac 1` and played them at half speed — classic "tape running out of batteries" symptom. Fix: `_stream_buffer` is now always **mono** (safe for relay, comparators, AI, chain clips). Stereo interleaved data is stored only in `_audio_buffer` (alert WAV clips). `stream_live` and `stream_audio` read from `_audio_buffer` when `cfg.stereo=True` and tell ffmpeg `-ac 2`, serving correct stereo MP3/WAV.
- **`_save_alert_wav` stereo framing wrong when called with external `_chunks`** — chain clips pass `_chunks=list(cfg._stream_buffer)` (now always mono), but `_audio_channels` could still be 2 for a stereo stream, causing the mono chain clip data to be mistreated as stereo. Fix: `_n_ch` is now forced to 1 whenever `_chunks` is supplied by the caller.

### Added
- **Stereo live listening** — the `/stream/<idx>/live` MP3 stream is now served at 256 kbps stereo when the stream has stereo enabled, giving full left/right separation in the browser audio player.

---

## [3.4.126] - 2026-04-01

### Added
- **Stereo capture for DAB streams** — "Enable stereo capture" checkbox now appears in DAB stream settings. When ticked, ffmpeg requests 2-channel output from welle-cli's MP3 endpoint; the pipeline splits L/R, applies per-channel DC removal, and stores interleaved stereo in buffers for clips. Mono mix passed to alerting unchanged. L/R bars appear on hub stream cards and client status page. Services broadcast in mono are unaffected — enabling stereo on a mono service just produces identical L and R channels.

---

## [3.4.125] - 2026-04-01

### Added
- **Stereo capture for Livewire / RTP / HTTP streams** — per-stream toggle in stream settings. When enabled, 2-channel sources deliver separate L and R audio rather than being downmixed to mono. Changes throughout the pipeline:
  - `_decode()` gains a `want_stereo` flag: returns raw interleaved L,R samples instead of the mono average.
  - Livewire/RTP path and HTTP/ffmpeg path both detect stereo, compute per-channel RMS (`_level_dbfs_l/r`), apply per-channel DC removal, store interleaved frames in `_stream_buffer` / `_audio_buffer`, and pass `(L+R)/2` mono mix to `analyse_chunk` (alerting unchanged).
  - `_save_alert_wav` detects stereo frames via `_audio_channels` and writes a proper 2-channel WAV.
  - `_try_encode_mp3` gains an `n_ch` parameter so WAV→MP3 pre-compression works for stereo clips.
  - `_upload_clip_inner` reads channel count from the WAV header before encoding, passing it to `_try_encode_mp3`.
  - Heartbeat and live-levels API expose `level_dbfs_l`, `level_dbfs_r`, and `stereo` for each stream.
  - Hub stream wall and hub overview stream cards show an inline L/R bar pair below the main RMS bar when stereo is active; updated at live-poll rate (150 ms).
  - Client status page also shows L/R bars for stereo streams.

---

## [3.4.124] - 2026-04-01

### Added
- **FM frequency deviation monitoring** — every FM stream now measures peak deviation (kHz) from the raw MPX discriminator output at 171 kHz. Shown in the FM / RDS stats panel on the stream card. Colour-coded: green < 70 kHz, amber 70–75 kHz, red > 75 kHz with a ⚠ OFCOM badge when the Ofcom limit is exceeded. Updated live on each poll cycle. Also stored in the metrics database for trending.

---

## [3.4.123] - 2026-04-01

### Added
- **Broadcast chains: Fault hold-off (s)** — new per-chain setting that delays ALL CHAIN_FAULT alerts by a configurable number of seconds, regardless of fault position. Unlike the existing "Fault confirmation" (which only applies to pre-mix-in / ad-break candidate faults), the hold-off applies universally — including post-mix-in faults that would otherwise fire immediately. Useful for chains where brief on-air silences from poorly-segued songs should not trigger alerts unless the outage persists. Found in the chain builder under Timing & Behaviour. 0 = off (default).

### Fixed
- **Auto-maintenance clear race condition** — the 60-second settle timer started by `start_monitoring()` could fire and clear maintenance even if `stop_monitoring()` had run again in the meantime (e.g. stop → start → stop in quick succession). Fixed by versioning the timer with a timestamp; the clear thread silently aborts if the timestamp no longer matches.
- **usbfs fix log spam** — when `rtl_fm` encountered USB buffer allocation errors it printed multiple "usbfs" and "zero-copy" stderr lines, each triggering a separate fix attempt and "usbfs fix failed" log entry. The fix is now attempted only once per stream connection, producing a single log line.

---

## [3.4.122] - 2026-04-01

### Fixed
- **Broadcast chain clips inconsistent length — recovery and last_good clips short** — `_schedule_chain_recovery_clips` (local nodes) called `_save_alert_wav` without a `_chunks` argument, falling back to `_audio_buffer` which defaults to 10 s. Recovery clips therefore captured however much audio happened to be in that 10 s ring at save time — giving 5 s, 14 s, or occasionally 30 s clips at random. Root cause: the `_audio_buffer` expansion (`_ensure_alert_buffer_capacity`) only changes the `maxlen`; it cannot backfill audio that wasn't recorded into it before the fault. Fix: `_schedule_chain_recovery_clips` now snapshots `_stream_buffer` (60 s rolling, always full after the first minute) and passes it as `_chunks`, guaranteeing the full configured clip duration. The same bug existed in `_cmd_save_clip` for remote `last_good` and `recovery` clips (`_fault_chunks` was `None` for any non-fault status); fixed by snapshotting `_stream_buffer` for all clip types unconditionally.

---

## [3.4.121] - 2026-04-01

### Added
- **Auto-maintenance when monitor is stopped** — when a client (or the hub itself) deliberately stops monitoring, all chain nodes belonging to that site are automatically placed in maintenance mode so the chain monitor does not fire false CHAIN_FAULT alerts while monitoring is intentionally off. When monitoring restarts, maintenance is cleared automatically after a 60-second settle so streams have time to connect and audio levels to stabilise before chain evaluation resumes. The settle timer is versioned — a rapid stop → restart → stop cycle cannot leave nodes unprotected.
  - Remote clients: detected via the `running` field in heartbeats (`True → False` sets maintenance; `False → True` triggers the 60-second settle then clears)
  - Local hub nodes (hub-as-both mode): hooked directly into `stop_monitoring()` and `start_monitoring()`

---

## [3.4.120] - 2026-04-01

### Improved
- **Wall mode: 150 ms live level updates** — stream status card bars and chain node mini-bars now poll `/api/hub/live_levels` at 150 ms (same as the hub dashboard), replacing the heartbeat-stale 5 s chain-poll or 60 s page-reload update cycle. CSS transitions on node bars reduced from 0.9 s to 0.2 s.
- **Wall mode: broadcast chains stacked vertically** — chains display in a single column so the full left-to-right node flow is always visible without adjacent chains competing for horizontal space.
- **Wall mode: ad-break countdown no longer shows FAULT** — server-rendered chain cards now use `display_status` (same field the JS poll uses) so chains in ad-break confirmation show "AD BREAK" rather than "FAULT" from the moment the page loads.
- **Wall mode: SignalScope styling** — CSS variables updated to match the main app (`--bg`, `--sur`, `--bor`, `--acc`, `--tx`, `--mu`).

---

## [3.4.119] - 2026-03-31

### Improved
- **Diagnostic logging for short chain fault clips** — `_save_alert_wav` now logs a warning when the actual clip duration is more than 0.5s shorter than requested. The log message (visible in the server log panel) names the stream and shows the available vs requested duration. Short clips occur when `_stream_buffer` hasn't yet accumulated enough audio — most commonly in the first 30 seconds after monitoring starts, after an HTTP stream reconnect, or if ffmpeg took several seconds to establish the initial connection. The clip is still saved; only its length is affected.

---

## [3.4.118] - 2026-04-01

### Fixed
- **Hub page meters take ~2 s to settle on load — peak drops to zero then jumps back** — `_livePeaks` started empty on page load, so the first live poll always set `prev.pct = 0`. After the 2 s hold timer fired it slid the peak marker to `left: 0%` via a CSS `ease-in` animation. The next poll (150 ms later) snapped it back, causing a visible drop-to-bottom-then-recover cycle every 2 s until audio settled. Fixed: (1) peak state is seeded from the server-rendered bar position on first sight so there is no reset; (2) decay is now JS-driven inside the poll loop (smooth 8%/s ramp toward the current level, not to zero), removing the CSS `transition: left 1.5s ease-in` entirely; (3) decay floor is the current RMS level, not zero — the peak marker never drops below the live bar.

---

## [3.4.117] - 2026-03-31

### Fixed
- **HTTP stream stall shows frozen stale levels on hub** — `proc.stdout.read(4096)` blocked indefinitely when a stream connection stayed open but sent no audio (e.g. dead Icecast source). `_has_real_level` remained `True` so the hub displayed the last measured level rather than clearing to null. Fixed with `select.select` polling at 1s intervals: if no data arrives for 10 s, `_has_real_level` is set `False` so the hub reverts to null levels. Also clears `_has_real_level` immediately on disconnect before the 5 s reconnect wait, so levels don't stay frozen during the gap. Popen `bufsize` changed to 0 (unbuffered) so `select` accurately reflects OS-level data availability.

---

## [3.4.116] - 2026-03-31

### Fixed
- **Hub site cards disappear on hover / reject confirmation unclickable** — CSS `columns` layout clips `transform: translateY(-1px)` at column boundaries, causing cards to vanish on hover. The same clipping hid the reject confirmation bar when the user moved the mouse to click "Yes, Remove". Removed the `transform` from `.site-card:hover`; box-shadow change alone gives sufficient hover feedback and is not affected by column clipping.

---

## [3.4.115] - 2026-03-31

### Fixed
- **Denied/rejected sites reappear on next heartbeat** — `hub_remove_site` previously deleted the site from `_sites` entirely. When the client sent its next heartbeat, `ingest()` treated it as a brand-new site and created a fresh pending entry, making the site reappear. Fix: instead of deleting, mark with `_denied: True` (minimal stub saved to hub_state.json). `ingest()` now detects denied sites and silently returns 'pending' without creating a new pending entry. `get_sites()` skips denied sites so they never appear in the hub dashboard. Denial persists across hub restarts.
- **Hub dashboard layout — tall site cards push subsequent sites far down** — the site grid used `display:grid` with `repeat(auto-fit, minmax(420px, 1fr))`. With an even number of columns, a tall card in row 1 forced the next card into row 2, leaving empty space to the right of shorter cards. Switched to CSS `columns: 420px` (column layout) so cards flow top-to-bottom within each column before starting the next, packing short cards neatly under tall ones without gaps.

---

## [3.4.114] - 2026-03-31

### Fixed
- **Chain fault clip captures post-fault silence instead of fault onset** — `_cmd_save_clip` for "fault" clips previously waited `clip_dur` seconds then saved from `_audio_buffer`. By that point the fault onset had scrolled off the buffer and the clip contained only post-fault silence or recovery audio. Fixed identically to the 3.4.73 silence clip fix: for "fault" status clips, `_stream_buffer` is snapshotted immediately on command receipt (before any delay) and passed as `_chunks` to `_save_alert_wav`. The clip now contains ~20 s of pre-fault programme audio followed by the fault onset. The per-position upload stagger is preserved; the `clip_dur` wait is removed for fault clips since the pre-fault data is already in the snapshot.

---

## [3.4.113] - 2026-03-31

### Fixed
- **Producer View — clips now reliably shown for all chain faults (presenter.py v1.4.0)** — rewrote the data layer to read from the SQLite fault log DB (same source as the Broadcast Chains fault viewer) instead of parsing alert_log.json. The DB already has correct clip paths back-patched when clips arrive from remote nodes, so no alert-log ID matching is needed. Also reverted the 3.4.112 `_fire_chain_fault` UUID change which would have broken hub_reports clip deduplication. `metrics_db` is now exposed in the plugin context (`ctx["metrics_db"]`).

---

## [3.4.112] - 2026-03-31

### Fixed
- **Producer View — no audio for remote-node chain faults (root cause)** — `_fire_chain_fault` always generated a fresh `uuid.uuid4()` for the CHAIN_FAULT alert log entry, regardless of the `entry_id` argument (the fault-log UUID). The `save_clip` command sent to the client uses `entry_id`, and `hub_clip_upload` writes the uploaded clip to the alert log with `id = entry_id`. Because the two UUIDs never matched, `_build_clip_index` could not find the uploaded clip by event ID and the play button never appeared in Producer View. Fix: alert log entry now uses `entry_id if entry_id else str(uuid.uuid4())` so the IDs are consistent end-to-end.

---

## [3.4.111] - 2026-03-31

### Added
- **Auto-detect and fix USB buffer limit for multi-dongle RTL-SDR setups** — when multiple RTL-SDR dongles are in use on Linux, the default `usbfs_memory_mb=16` kernel limit causes rtl_fm to fail with "Failed to allocate zero-copy buffer". SignalScope now detects and fixes this automatically in three ways:
  1. **Auto-fix on error**: the rtl_fm stderr reader detects the zero-copy/usbfs error message and immediately attempts to write `0` to `/sys/module/usbcore/parameters/usbfs_memory_mb`. Succeeds when SignalScope runs as root (typical service install).
  2. **Settings → SDR Devices warning banner**: when Scan for Dongles is clicked, the response now includes the current `usbfs_memory_mb` value. If it is non-zero a warning banner appears with a **Fix Now** button that calls `POST /api/sdr/usbfs_fix`. On success the banner turns green; on permission failure the exact manual command is shown.
  3. **`/api/sdr/usbfs_fix` endpoint**: can also be called directly. Returns `{ok, message}`.

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
