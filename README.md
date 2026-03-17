
# SignalScope

SignalScope is a **web-based radio monitoring and signal analysis platform** designed for broadcast engineers and SDR enthusiasts.

It can ingest FM, DAB and Livewire/AES67 audio streams, analyse them in real time, and present the results in a modern web dashboard. The system supports both **stand-alone monitoring nodes and distributed hub deployments** for network-wide signal monitoring.

SignalScope is written in **Python (Flask)** and designed to run easily on **Linux servers, VMs, and small systems like Raspberry Pi**.

---

# What’s New in 2.6

Version **2.6** focuses on usability improvements, hub monitoring enhancements, and better stability.

## UI Improvements
- Improved dashboard layout
- Moveable dashboard cards
- Improved top navigation and logo rendering
- Cleaner hub dashboard layout

## Hub Improvements
- Hub-only mode removes the local dashboard and shows the hub view
- Improved client visibility
- Ability to remove dead clients
- More metadata displayed on hub cards

## Metadata Enhancements
- Improved **RDS handling**
- Proper **RDS name locking**
- **RDS RadioText display**
- DAB metadata support
- Additional fields displayed when available

## Monitoring Improvements
- Improved monitor reliability
- Better handling of SDR restarts
- Improved stream handling

## Stability Fixes
- Fixed setup wizard authentication bug
- Improved session handling
- Better startup reliability on fresh installs

---

# Features

## Real-Time Signal Monitoring
- FM monitoring via RTL-SDR
- DAB monitoring
- Livewire / AES67 stream monitoring

## Metadata Detection
- RDS Program Service
- RDS RadioText
- DAB service metadata

## Distributed Monitoring
- Multi-node monitoring network
- Central **SignalScope Hub**
- Remote client reporting

## Web Dashboard
- Real-time monitoring interface
- Stream listen buttons
- Signal metadata display
- Card-based monitoring layout

## Network Friendly
- Works behind reverse proxies
- NAT friendly hub communication
- Low bandwidth client reporting

---

# Architecture

SignalScope uses a **hub and client model**.

```
 SDR Node
    │
    │ metadata + audio analysis
    ▼
SignalScope Client
    │
    │ HTTP reporting
    ▼
SignalScope Hub
    │
    ▼
Web Dashboard
```

Multiple monitoring nodes can report to a central hub for **network-wide monitoring**.

---

# Installation

SignalScope runs on **Ubuntu / Debian systems**.

## 1. Install dependencies

```bash
sudo apt update
sudo apt install -y \
python3 \
python3-venv \
rtl-sdr \
welle.io \
git
```

## 2. Clone the repository

```bash
git clone https://github.com/itconor/SignalScope.git
cd SignalScope
```

## 3. Create Python environment

```bash
python3 -m venv venv
source venv/bin/activate
```

## 4. Install Python dependencies

```bash
pip install -r requirements.txt
```

## 5. Run SignalScope

```bash
python signalscope.py
```

SignalScope will start on:

```
http://localhost:5000
```

The setup wizard will launch on first run.

---

# First Run Setup

The wizard will guide you through:

1. SDR configuration
2. Hub configuration (optional)
3. Authentication setup (optional)
4. Monitoring settings

Once completed, the dashboard will load automatically.

---

# Hub Mode

SignalScope can operate as a **hub server** receiving data from multiple monitoring nodes.

Hub features:

- Central monitoring dashboard
- Aggregated station data
- Remote node visibility
- Client health monitoring

---

# SDR Hardware

Supported SDR devices include:

- RTL-SDR
- RTL-SDR Blog V3/V4
- Generic RTL2832U dongles

---

# Project Status

SignalScope is under active development.

New features and improvements are being added regularly.

---

# Contributing

Pull requests and suggestions are welcome.

If you encounter issues please open a GitHub issue.

---

# License

MIT License
