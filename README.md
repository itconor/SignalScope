# SignalScope

SignalScope is a **web-based radio monitoring and signal analysis platform** designed for broadcast engineers and SDR enthusiasts.

It can ingest **FM, DAB and Livewire/AES67 audio streams**, analyse them in real time, and present the results in a modern web dashboard.
The system supports both **stand-alone monitoring nodes and distributed hub deployments** for network-wide signal monitoring.

SignalScope is written in **Python (Flask)** and designed to run easily on **Linux servers, VMs, and small systems like Raspberry Pi**.

---

## Install in 30 seconds

```bash
/bin/bash <(curl -fsSL https://raw.githubusercontent.com/itconor/SignalScope/main/install_signalscope.sh)
```

---

# 🚀 Quick Install

Clone the repository and run the installer:

```bash
git clone https://github.com/itconor/SignalScope.git
cd SignalScope
bash install_signalscope.sh
```

The installer will:

- Detect existing installations and offer to update in-place
- Install system dependencies
- Create the Python virtual environment
- Install required Python packages
- Configure the systemd service and self-healing watchdog
- Optionally configure NGINX as a reverse proxy
- Start SignalScope

Once complete, open:

```text
http://localhost:5000
```

The setup wizard will guide you through the rest.

---

# ⚡ One-Line Install

You can also install SignalScope directly using `curl`:

```bash
curl -sSL https://raw.githubusercontent.com/itconor/SignalScope/main/install_signalscope.sh | bash
```

### Optional safer version

If you prefer to inspect the script first:

```bash
curl -O https://raw.githubusercontent.com/itconor/SignalScope/main/install_signalscope.sh
bash install_signalscope.sh
```

---

# ✨ What's New in 3.0 (3.0.1–3.0.2)

## Composite Logic Alerts (FM)
- **STUDIO_FAULT** — silence detected while carrier and RDS are healthy; points to a studio/console fault upstream of the transmitter
- **STL_FAULT** — silence with carrier present but RDS absent; indicates a studio-to-transmitter link failure
- **TX_DOWN** — silence with weak or absent carrier; indicates transmitter or antenna failure
- All three replace the generic SILENCE alert for FM streams with an RTL-SDR source, giving engineers an immediate fault location rather than just a silence notification

## DAB Service Missing Alert
- **DAB_SERVICE_MISSING** — fires when the DAB ensemble is locked but the configured service disappears from the multiplex; useful for detecting mux software faults while the RF path remains healthy

## RTP Jitter Metric
- RFC 3550-style inter-arrival time jitter tracked per Livewire/AES67 stream
- Displayed live on each stream card (hidden when zero)
- Colour-coded: green below 5 ms, amber above

## Hub Notification Delegation (3.0.2)
- **Suppress local notifications** — new per-client setting; when a client is connected to a hub, all email/webhook/Pushover alerts are suppressed locally and delegated to the hub instead
- **Per-site alert rules on hub** — hub operators can configure forwarding rules on a per-client-site basis: enable/disable forwarding and select which alert types to forward (from the full type list)
- Deduplication by event UUID prevents duplicate notifications when a client reconnects

---

# ✨ What's New in 2.6.56–2.6.67

## LUFS / EBU R128 Loudness Monitoring
- **True peak alert (LUFS_TP)** — alert when the true peak level exceeds a configurable dBTP threshold (default −1.0 dBTP); fires per chunk
- **Integrated loudness alert (LUFS_I)** — alert when the 30-second rolling integrated loudness deviates from a configurable EBU R128 target (default −23 LUFS ± 3 LU)
- K-weighting filter applied in real-time via biquad cascade; no additional Python dependencies
- Displayed on stream cards with momentary, short-term, and integrated LUFS values

## Alert Escalation
- **Escalation alerts** — re-notify via all configured channels (email, webhook, Pushover) if an alert remains unacknowledged after a configurable number of minutes (per stream); 0 = off
- Escalation uses the same cooldown deduplication as standard alerts

## Stream Comparator
- **Pre/post processing comparison** — pair any two streams (e.g. studio feed vs. air monitor) and SignalScope will cross-correlate them to measure processing delay
- **Processor failure detection** — alerts (CMP_ALERT) when the post-processing stream goes silent while the pre-processing stream has audio
- **Gain drift detection** — alerts when the level difference between pre and post streams exceeds a threshold, indicating compressor or AGC issues
- **Dropout discrimination** — distinguishes single-path RTP loss from full processing chain failure
- Comparator status and delay shown on the dashboard

## In-App Self-Update
- **Apply Update & Restart** button in the Maintenance panel checks GitHub for a newer version and, on confirmation, downloads the new `signalscope.py`, validates it with `py_compile`, replaces the running file, and sends SIGTERM — systemd/watchdog handles the restart automatically
- No `sudo` required; only the app's own Python file is replaced

## PTP Configurable Thresholds
- PTP offset and jitter alert/warn thresholds are now configurable in the Settings UI (in µs) rather than being compile-time constants
- Defaults remain 5 ms warn / 50 ms alert for offset and 2 ms / 10 ms for jitter — appropriate for NTP-synced passive observers
- Guidance note in the settings explains how to tighten thresholds for a true PTP-slaved system

## Installer: Raspberry Pi Overclock
- Installer detects Raspberry Pi 3 and 4 and offers optional overclock settings at install/update time
- Pi 3: arm_freq=1450 MHz, over_voltage=2, gpu_freq=500
- Pi 4: arm_freq=2000 MHz, over_voltage=6, gpu_freq=750
- Pi 5 is detected and excluded (overclock not supported via this method on Pi 5)
- Settings are written idempotently to `/boot/firmware/config.txt` or `/boot/config.txt`

## Installer: Nginx Repair Flow
- On update runs where an existing nginx config is detected, the installer now checks config validity (`nginx -t`) and certificate presence
- **Broken config** (test fails or cert missing): warns the user and prompts to remove and start the nginx setup from scratch, pre-filling the previous FQDN
- **Healthy config**: shows the current FQDN and asks if the user wants to reconfigure
- Previously the installer silently skipped nginx on all update runs, with no way to fix a failed Let's Encrypt setup without manually removing files

---

# ✨ What's New in 2.6.52–2.6.55

## Hub Reports
- **Alert clip download** — each clip row on the hub reports page now has a ⬇ download button alongside the audio player, allowing engineers to save alert WAV files directly from the hub

## Settings
- **Backup & Export** — new panel at the bottom of Settings page; one click downloads a timestamped ZIP containing `lwai_config.json` and all trained AI model files (`ai_models/`), making migration and backup straightforward

## CSRF fixes (all templates)
- **Universal CSRF meta tag** — `<meta name="csrf-token">` added to every template that was missing it (`SETTINGS_TPL`, `REPORTS_TPL`, `INPUT_LIST_TPL`, `INPUT_FORM_TPL`, `HUB_REPORTS_TPL`); eliminates CSRF validation failures on DAB bulk-add, settings test-notify, and hub alert acknowledgement

---

# ✨ What's New in 2.6.51

## Security Hardening
- **Path traversal fix** — `clips_delete` now validates stream name and filename against the snippet directory boundary using `os.path.abspath` checks, matching the existing `clips_serve` pattern
- **DAB channel whitelist** — `/api/dab/test` now validates the channel parameter against an explicit allowlist of valid DAB channels; PPM offset is validated as a signed integer within ±1000
- **SDR scan authentication** — `/api/sdr/scan` now requires a valid login session
- **Flask secret key hardening** — secret key file is created with `0o600` permissions; `Content-Disposition` filenames are sanitised before being sent in headers

## Hub Improvements
- **Remote start/stop control** — hub operators can now start or stop monitoring on any client node directly from the hub dashboard; commands are delivered securely via the heartbeat ACK
- **Reliable command delivery** — hub-controlled fields (`relay_bitrate`, pending commands) are now explicitly preserved across heartbeat updates so queued commands are never silently dropped
- **Hub replica cards fixed** — `get_site()` now computes `online`, `age_s`, `health_pct`, and `latency_ms` dynamically, matching `get_sites()`; replica page cards now populate correctly
- **CSRF fixed across all hub templates** — CSRF token is written to a `csrf_token` cookie via an `after_request` hook; all hub JavaScript now reads the token from the cookie first, eliminating template-specific meta-tag misses

## DAB Improvements
- **Shared mux stability on Pi 4** — `welle-cli` processes are now started with elevated scheduling priority (`nice -10`) to reduce CPU contention when running 4+ DAB services simultaneously on ARM hardware

---

# ✨ What's New in 2.6.41–2.6.50

## Hub Dashboard
- **Live card updates working** — fixed a silent JavaScript error (`lastAlertState` undefined) that was preventing all AJAX DOM updates on the hub page
- **Cache-busting on `/hub/data`** — added `Cache-Control: no-store` headers and `?_=timestamp` fetch parameters to prevent NGINX/browser caching stale data
- **Reliable polling loop** — switched from `setInterval` to recursive `setTimeout` via `.finally()` to prevent timer stacking on slow connections
- **Instant refresh on tab focus** — Page Visibility API handler fires `hubRefresh` immediately when switching back to the hub tab
- **Reload-loop guard** — prevents "new site appeared" reloads from triggering more than once every 30 seconds
- **Start/Stop buttons** — remote monitoring control buttons use `data-` attributes and event delegation to avoid HTML injection issues with site names containing spaces

## DAB Improvements
- **Bulk-add service fix** — service names were being URL-encoded in JavaScript but not decoded in `_run_dab`; fixed with `urllib.parse.unquote()`
- **DAB add form UX** — name field and rule-based alert settings are hidden when DAB source type is selected
- **DAB station list styling** — service rows now match the app's blue theme
- **DLS text parsing** — `welle-cli` returns `dynamicLabel` as a JSON object; fixed to extract the `label` key
- **DLS display** — DLS text on hub cards uses the same scrolling marquee as RDS RadioText

## RDS / Metadata
- **RDS RadioText scrolling restored** — hub cards check `fm_rds_rt || dab_dls` in both template and AJAX refresh loop
- **DLS shown for DAB on hub cards** — `sc-rt-row` classes added to DAB DLS rows for live AJAX updates

## Monitoring
- **Clip threshold default** changed from `-3.0 dBFS` to `-1.0 dBFS` for more accurate clipping detection

## Hub Audio
- **Alert audio playback behind reverse proxy** — relay client sends an empty EOF chunk after WAV delivery so the hub closes the relay slot immediately rather than waiting for proxy timeout

---

# ✨ What's New in 2.6

## UI Improvements
- Moveable dashboard cards
- Improved layout and spacing
- Cleaner hub dashboard
- Improved top navigation and logo rendering

## Hub Improvements
- **Hub-only mode** removes the local dashboard
- Ability to remove dead clients
- Improved client visibility
- More metadata displayed on hub cards

## Metadata Enhancements
- Improved **RDS handling**
- Proper **RDS name locking**
- **RDS RadioText display**
- Improved DAB metadata support

## Monitoring Improvements
- Improved monitor reliability
- Better SDR restart handling
- Improved audio stream stability

## Stability Fixes
- Fixed setup wizard authentication bug
- Improved session handling
- Better fresh-install startup reliability

---

# 📡 Features

## Real-Time Signal Monitoring
- FM monitoring via **RTL-SDR**
- **DAB monitoring** with bulk service add
- **Livewire / AES67 stream monitoring**

## Metadata Detection
- RDS Program Service name
- RDS RadioText (scrolling display)
- DAB DLS now-playing text (scrolling display)
- DAB ensemble, service, mode, bitrate, signal strength

## Alerting & AI
- Rule-based alerts: silence, clipping, hiss
- **Composite FM fault classification** — silence alerts automatically diagnose STUDIO_FAULT, STL_FAULT, or TX_DOWN based on carrier and RDS state
- **DAB_SERVICE_MISSING** — alert when a service vanishes from a locked ensemble
- **LUFS / EBU R128 loudness monitoring** — true peak and integrated loudness alerts per stream
- **Alert escalation** — re-notify if an alert remains unacknowledged after N minutes
- **AI anomaly detection** — per-stream ONNX autoencoder, 24-hour learning phase
- Email, webhook (MS Teams Adaptive Cards), and Pushover notifications
- **SLA tracking** — monthly per-stream uptime percentage

## Distributed Monitoring
- Multi-node monitoring with a central **SignalScope Hub**
- Remote client reporting with HMAC-SHA256 + AES-256-GCM encryption
- Hub relay for audio playback through NAT / reverse proxies
- **Remote start/stop** — hub operators can control monitoring state on client nodes
- **Hub notification delegation** — clients can suppress local alerts and let the hub handle all notifications with per-site forwarding rules
- **Stream comparator** — cross-correlate pre/post processing pairs to detect processor failures, gain drift, and single-path dropouts

## Web Dashboard
- Real-time monitoring interface with live AJAX updates
- Stream listen buttons (live audio in browser)
- Signal metadata display with scrolling RDS/DLS text
- Card-based monitoring layout with drag-to-reorder
- Wall mode for NOC/control room displays

## Backup & Migration
- **One-click backup** — download a ZIP of your config + all trained AI models from the Settings page
- Timestamped archive (`signalscope_backup_YYYYMMDD_HHMMSS.zip`) — extract into a new install to migrate instantly

## Security
- CSRF protection on all state-changing routes
- PBKDF2-SHA256 password hashing with session timeouts and login rate limiting
- Hub communication: HMAC signing, AES-256-GCM payload encryption, 30-second replay protection window, 60 RPM rate limiting
- Path traversal protection on all file-serving routes
- Input validation and channel whitelisting on SDR API endpoints

## Network Friendly
- Works behind reverse proxies (NGINX, Caddy, etc.)
- NAT-friendly hub communication
- Low bandwidth client reporting
- `ProxyFix` middleware with correct header forwarding

---

# 🖥 Dashboard

Example dashboard layout showing monitored stations and metadata.

_Add screenshot here_

```text
docs/images/dashboard.png
```

---

# 🌐 Hub Dashboard

The hub dashboard aggregates data from multiple SignalScope clients across the network.

_Add screenshot here_

```text
docs/images/hub-dashboard.png
```

---

# 🏗 Architecture

SignalScope uses a **hub and client monitoring model**.

```mermaid
flowchart LR
    A[RF / Audio Sources\nFM / DAB / Livewire / AES67]
    B[SignalScope Client Node 1]
    C[SignalScope Client Node 2]
    D[SignalScope Client Node 3]
    E[Central SignalScope Hub]
    F[Hub Dashboard]
    G[Engineers / Operators]

    A --> B
    A --> C
    A --> D

    B -->|Heartbeat: metadata + status + alerts| E
    C -->|Heartbeat: metadata + status + alerts| E
    D -->|Heartbeat: metadata + status + alerts| E

    E -->|ACK: relay config + remote commands| B
    E -->|ACK: relay config + remote commands| C
    E -->|ACK: relay config + remote commands| D

    E --> F
    F --> G
```

Each client monitors local RF or IP audio sources and reports status, metadata, and monitoring data back to a central hub. The hub can issue commands back to clients on heartbeat ACKs — including remote start/stop of monitoring.

---

# 📻 Supported Inputs

| Source | Supported |
|------|------|
| RTL-SDR FM | ✅ |
| DAB via SDR | ✅ |
| Livewire audio streams | ✅ |
| AES67 streams | ✅ |
| Remote hub clients | ✅ |

---

# 🧰 Installation (Manual)

SignalScope runs on **Ubuntu / Debian systems**.

## Install dependencies

```bash
sudo apt update
sudo apt install -y \
python3 \
python3-venv \
rtl-sdr \
welle.io \
git
```

## Clone repository

```bash
git clone https://github.com/itconor/SignalScope.git
cd SignalScope
```

## Run installer

```bash
bash install_signalscope.sh
```

---

# ⚙ First Run Setup

The setup wizard will guide you through:

1. SDR configuration
2. Hub configuration (optional)
3. Authentication setup
4. Monitoring settings

After setup completes the **dashboard will load automatically**.

---

# 🌍 Hub Mode

SignalScope can operate as a **central hub server** receiving data from multiple monitoring nodes.

Hub features:

- Central monitoring dashboard with live AJAX updates (no page refresh needed)
- Aggregated station data with per-stream level bars, AI status and RDS/DLS text
- Remote node visibility with latency, heartbeat health %, and last-seen indicators
- **Remote start/stop** — control monitoring state on client nodes from the hub UI
- Client health monitoring with consecutive missed heartbeat tracking
- Alert sound and card flash on new ALERT/WARN events
- Wall mode for large-screen / NOC deployments
- Encrypted hub relay for audio playback from behind NAT

---

# 🔧 Watchdog

The installer configures a **systemd watchdog timer** that runs every minute and independently monitors:

- **SignalScope app** on port 5000 — restarts the `signalscope` service if unresponsive
- **NGINX** on ports 443/80 — restarts nginx if configured and unresponsive

Each service is monitored and restarted independently. Watchdog events are logged via `logger` and visible in the system journal:

```bash
journalctl -t signalscope-watchdog
```

---

# 📻 Supported SDR Hardware

- RTL-SDR
- RTL-SDR Blog V3
- RTL-SDR Blog V4
- Generic RTL2832U dongles

---

# 🛠 Project Status

SignalScope is under **active development**.

Current build: **SignalScope-3.0.2**

New features and improvements are added regularly.

---

# 🤝 Contributing

Pull requests and suggestions are welcome.

If you encounter issues please open a **GitHub issue**.

---

# 📜 License

MIT License
