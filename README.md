# SignalScope

SignalScope is a **web-based radio monitoring and signal analysis platform** designed for broadcast engineers and SDR enthusiasts.

It can ingest **FM, DAB and Livewire/AES67 audio streams**, analyse them in real time, and present the results in a modern web dashboard.
The system supports both **stand-alone monitoring nodes and distributed hub deployments** for network-wide signal monitoring.

SignalScope is written in **Python (Flask)** and designed to run easily on **Linux servers, VMs, and small systems like Raspberry Pi**.

---

## Install in 30 seconds

```
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

- Install system dependencies
- Create the Python environment
- Install required Python packages
- Configure the service
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

# ✨ What's New in 2.6.41

## Hub Dashboard
- **Live card updates now working** — fixed a silent JavaScript error (`lastAlertState` undefined) that was preventing all AJAX DOM updates on the hub page
- **Cache-busting on `/hub/data`** — added `Cache-Control: no-store` response headers and `?_=timestamp` fetch parameter to prevent NGINX/browser caching stale data
- **Reliable polling loop** — switched from `setInterval` to a recursive `setTimeout` via `.finally()`, preventing timer stacking on slow connections
- **Instant refresh on tab focus** — Page Visibility API handler fires `hubRefresh` immediately when switching back to the hub tab after it has been backgrounded
- **Reload-loop guard** — new `_hubLastReload` guard prevents the "new site appeared" reload from triggering more than once every 30 seconds

## DAB Improvements
- **Bulk-add service fix** — service names were being URL-encoded (`BBC%20Radio%204`) in JavaScript but not decoded in `_run_dab`, causing the monitor to fail finding services on the mux; fixed with `urllib.parse.unquote()`
- **DAB add form UX** — Name field and all rule-based alert settings are now hidden when DAB source type is selected (not applicable for DAB bulk-add)
- **DAB station list styling** — service rows in the DAB add UI now match the app's blue theme (`var(--sur)`, `var(--bor)`, `var(--acc)`) instead of black/grey
- **DLS text parsing** — `welle-cli` returns `dynamicLabel` as a JSON object; fixed to extract the `label` key instead of displaying the raw Python dict string
- **DLS display** — DLS text on hub cards now uses the same scrolling marquee as RDS RadioText; short text truncates cleanly, long text scrolls; no more wrapping or label/value collision

## RDS / Metadata
- **RDS RadioText scrolling restored** — hub cards now check `fm_rds_rt || dab_dls` in both the Jinja template and the AJAX refresh loop
- **DLS shown for DAB on hub cards** — `sc-rt-row` classes added to DAB DLS rows so the AJAX refresh updates them live

## Monitoring
- **Clip threshold default** changed from `-3.0 dBFS` to `-1.0 dBFS` for more accurate clipping detection out of the box

## Hub Audio
- **Alert audio playback behind reverse proxy** — relay client now sends an empty EOF chunk after the WAV file is fully pushed, allowing the hub to close the relay slot immediately rather than waiting for the 30-second proxy timeout

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
- **AI anomaly detection** — per-stream ONNX autoencoder, 24-hour learning phase
- Email, webhook (MS Teams), and Pushover notifications
- **SLA tracking** — monthly per-stream uptime percentage

## Distributed Monitoring
- Multi-node monitoring
- Central **SignalScope Hub**
- Remote client reporting with HMAC + AES-256-GCM encryption
- Hub relay for audio playback through NAT / reverse proxies

## Web Dashboard
- Real-time monitoring interface with live AJAX updates
- Stream listen buttons (live audio in browser)
- Signal metadata display with scrolling text
- Card-based monitoring layout with drag-to-reorder
- Wall mode for NOC/control room displays

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

    B -->|Metadata + status + audio info| E
    C -->|Metadata + status + audio info| E
    D -->|Metadata + status + audio info| E

    E --> F
    F --> G
```

Each client can monitor local RF or IP audio sources and report status, metadata, and monitoring data back to a central hub.

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
3. Authentication setup (optional)
4. Monitoring settings

After setup completes the **dashboard will load automatically**.

---

# 🌍 Hub Mode

SignalScope can operate as a **central hub server** receiving data from multiple monitoring nodes.

Hub features:

- Central monitoring dashboard with live AJAX updates (no page refresh needed)
- Aggregated station data with per-stream level bars, AI status and RDS/DLS text
- Remote node visibility with latency and last-seen indicators
- Client health monitoring
- Alert sound and card flash on new ALERT/WARN events
- Wall mode for large-screen / NOC deployments

---

# 📻 Supported SDR Hardware

- RTL-SDR
- RTL-SDR Blog V3
- RTL-SDR Blog V4
- Generic RTL2832U dongles

---

# 🛠 Project Status

SignalScope is under **active development**.

Current build: **SignalScope-2.6.41**

New features and improvements are added regularly.

---

# 🤝 Contributing

Pull requests and suggestions are welcome.

If you encounter issues please open a **GitHub issue**.

---

# 📜 License

MIT License
