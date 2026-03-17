# SignalScope | v2.5.37 Technical Reference

SignalScope  
Version 2.5.37  
Technical Reference & Deployment Guide

SignalScope is a self-contained Python application for monitoring Axia Livewire, AES67, DAB, FM, and HTTP audio streams. It combines rule-based alerting with per-stream AI anomaly detection, a multi-site hub, RTL-SDR support for broadcast monitoring, and a browser-based setup and management interface.

The application has been rebranded from **Livewire AI Monitor** to **SignalScope**. Older references to `LivewireAIMonitor.py` should now be considered legacy; the primary application file is now **`signalscope.py`**.

---

## 1. Overview

SignalScope is designed for radio engineering and technical monitoring workflows. It can monitor:

- **Axia Livewire streams**
- **AES67 / RTP multicast or unicast streams**
- **HTTP / HTTPS audio streams**
- **DAB radio services via RTL-SDR and `welle-cli`**
- **FM radio services via RTL-SDR, `rtl_fm`, and `redsea`**

It provides:

- AI-assisted anomaly detection using **ONNX Runtime**
- Silence, hiss, and clipping detection
- RTP packet loss monitoring
- Passive PTP clock monitoring
- Multi-site hub mode with secure heartbeats
- Stream cards with live status, FM/DAB stats, now playing, and alert state
- Audio clip capture for incidents
- Live listen support
- First-run setup flow and web UI

---

## 2. Requirements & Installation

### 2.1 Quick install (Linux)

```bash
git clone https://github.com/itconor/SignalScope
cd SignalScope
bash install_signalscope.sh
```

For SDR support:

```bash
bash install_signalscope.sh --sdr
```

The installer now installs the **systemd service by default**.

To skip service installation:

```bash
bash install_signalscope.sh --sdr --no-service
```

To force reinstall / overwrite existing files:

```bash
bash install_signalscope.sh --sdr --force
```

### 2.2 Installer behaviour

The Linux installer can:

- create the Python virtual environment
- install Python dependencies
- install SDR dependencies
- install and enable the SignalScope systemd service by default
- ensure **`pyrtlsdr==0.2.93`** is installed
- fetch **`signalscope.py`** and assets from GitHub if they are missing locally
- install the `static/` directory if required

### 2.3 Manual install

Core Python packages:

```bash
pip install flask numpy onnxruntime onnx waitress cryptography pyrtlsdr==0.2.93
```

Ubuntu / Debian packages:

```bash
sudo apt update
sudo apt install -y ffmpeg rtl-sdr librtlsdr-dev git rsync python3-venv
```

For DAB support:

```bash
sudo apt install -y welle-cli
```

> Note: older versions of the project documented a third-party PPA for `welle-cli`. Current builds use the Ubuntu package where available.

### 2.4 First run

```bash
python3 signalscope.py
```

SignalScope starts the web interface on port **5000** in client mode unless configured otherwise.

On first run, the setup flow can be completed in the browser. If needed, revisit setup from:

```text
/setup
```

---

## 3. Default Service Configuration (Linux)

Example systemd unit:

```ini
[Unit]
Description=SignalScope
After=network-online.target

[Service]
ExecStart=/opt/signalscope/venv/bin/python3 /opt/signalscope/signalscope.py
WorkingDirectory=/opt/signalscope
Restart=on-failure
User=signalscope
AmbientCapabilities=CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
```

If binding to ports **80** or **443**, Python requires `cap_net_bind_service`:

```bash
readlink -f "$(which python3)"
sudo setcap cap_net_bind_service=+ep /usr/bin/python3.x
```

---

## 4. Input Sources

Each monitored stream is added through the web interface under **Inputs → Add Input**.

### Supported source types

| Source Type | How to configure |
|---|---|
| Livewire / RTP / HTTP | Plain address such as `21811`, `239.192.85.19:5004`, or `http://stream.example.com/live` |
| DAB | Select channel, dongle, scan multiplex, then pick a service |
| FM | Enter frequency in MHz and select dongle |

Internally, all source types are stored as a `device_index` string.

### Internal address formats

| Format | Description |
|---|---|
| `21811` | Livewire stream ID (auto-maps to `239.192.x.y:5004`) |
| `239.192.85.19:5004` | Raw multicast or unicast RTP/UDP |
| `http://stream.example.com/mp3` | HTTP or HTTPS stream decoded via `ffmpeg` |
| `dab://BBC Radio 4?freq=178352&serial=DAB_DONGLE_1` | DAB service |
| `fm://96.7?serial=FM_DONGLE_1` | FM frequency in MHz |

---

## 5. Alert Types

| Alert | Triggered when |
|---|---|
| Silence | Level at or below silence threshold for minimum duration |
| Clip | Peak level at or above clip threshold N times in window |
| Hiss | High-frequency energy above learned baseline |
| AI Warn | Reconstruction error exceeds warn threshold for 3 consecutive windows |
| AI Alert | Reconstruction error exceeds alert threshold for 3 consecutive windows |
| RTP Loss | Packet loss exceeds configured threshold |
| PTP Offset | Clock drift from rolling baseline exceeds threshold |
| PTP Jitter | Standard deviation of recent offsets exceeds threshold |
| PTP Lost | No sync messages seen for more than 4 seconds |
| PTP GM Change | IEEE 1588 grandmaster identity changes |
| Comparator | Correlation drop, gain shift, or silence on a pre/post pair |

All alerts use a **60-second cooldown** per type per stream by default. Alert WAV clips are saved automatically.

---

## 6. AI Anomaly Detection

### 6.1 How it works

Each stream trains a compact ONNX autoencoder over a baseline period. The model measures reconstruction error across content-agnostic audio features. If the model can no longer reconstruct expected behaviour, SignalScope treats that as a possible anomaly.

### 6.2 Features considered

- level
- peak
- crest factor
- DC offset
- clip fraction
- RMS variance
- minimum frame level
- spectral flatness
- spectral rolloff
- noise floor
- hum energy (50/60 Hz)
- high-frequency ratio
- zero-crossing rate

### 6.3 False positive reduction

SignalScope reduces nuisance alerts by using:

- conservative thresholds
- 3 consecutive abnormal windows before triggering
- adaptive clean-window baseline updates

Allow the full learning period before depending on AI alerts, especially after adding a new source or changing programme processing.

---

## 7. Stream Comparator

The comparator monitors a **pre/post processing pair** by cross-correlating the two signals with automatic delay compensation.

| Metric | Description |
|---|---|
| Correlation | Normalised cross-correlation between pre and post |
| Delay (ms) | Measured pre→post delay |
| Gain diff | Post minus pre level in dB |

For heavily processed chains, a gain shift threshold of **4–6 dB** is often more practical than a tighter default.

---

## 8. RTL-SDR Monitoring (DAB & FM)

SignalScope supports low-cost RTL-SDR dongles for off-air monitoring.

Each dongle should be registered in the **SDR Device Registry** so it can be identified by serial number regardless of USB port.

---

## 9. Linux RTL-SDR Setup

### Step 1 — Install packages

```bash
sudo apt install -y rtl-sdr librtlsdr-dev welle-cli
pip install pyrtlsdr==0.2.93
```

### Step 2 — Blacklist the conflicting kernel driver

```bash
echo 'blacklist dvb_usb_rtl28xxu' | sudo tee /etc/modprobe.d/rtlsdr.conf
sudo modprobe -r dvb_usb_rtl28xxu
sudo update-initramfs -u
```

Unplug and replug the dongles after doing this.

### Step 3 — Verify dongles are detected

```bash
rtl_test -t
```

Expected example:

```text
Found 2 device(s):
0: Realtek, RTL2838UHIDIR, SN: 00000001
1: Realtek, RTL2838UHIDIR, SN: 00000002
```

### Step 4 — Program serial numbers

Plug in one dongle at a time:

```bash
# DAB dongle
rtl_eeprom -d 0 -s "DAB_DONGLE_1"

# FM dongle
rtl_eeprom -d 0 -s "FM_DONGLE_1"
```

Unplug and replug after writing the EEPROM.

### Step 5 — Measure PPM correction

```bash
rtl_test -p
```

Let it run for several minutes and note the cumulative PPM value.

### Step 6 — Verify `welle-cli`

```bash
welle-cli --version
welle-cli -c 5C -p 0 --http-port 7979 &
sleep 12 && curl -s http://localhost:7979/api/services | python3 -m json.tool
kill %1
```

---

## 10. DAB Monitoring

DAB inputs use **RTL-SDR + `welle-cli`**.

### Add a DAB input

In **Inputs → Add Input**:

- choose **DAB** as the source type
- select the Band III channel
- select the registered dongle
- click **Scan Multiplex**
- choose a service from the discovered list
- save the input

Stored format example:

```text
dab://BBC Radio 4?freq=178352&serial=DAB_DONGLE_1
```

### Example UK DAB channel reference

| Channel | Frequency |
|---|---|
| 5A | 174.928 MHz |
| 5B | 177.008 MHz |
| 5C | 178.352 MHz |
| 5D | 180.064 MHz |
| 10A | 209.936 MHz |
| 11B | 218.640 MHz |
| 12B | 225.648 MHz |

> Exact local multiplex availability varies by area.

### DAB metrics shown on cards / hub

Depending on reception and service state, SignalScope can display:

- ensemble name
- selected service
- signal indication
- SNR / quality
- service audio state

---

## 11. FM Monitoring

FM monitoring uses an RTL-SDR dongle and the following chain:

```text
RTL-SDR → rtl_fm → redsea → Python parser → SignalScope UI → Hub
```

SignalScope extracts and distributes:

- **signal level / received power**
- **SNR**
- **stereo / mono indication**
- **RDS PS** (station name)
- **RDS RadioText**
- **RDS PI and group data**

### Add an FM input

In **Inputs → Add Input**:

- choose **FM** as the source type
- enter frequency in MHz
- choose a registered FM dongle
- save the input

Stored format example:

```text
fm://96.7?serial=FM_DONGLE_1
```

### FM metrics shown on cards / hub

| Metric | Description |
|---|---|
| Signal (dBm) | Raw received signal power |
| SNR (dB) | Signal-to-noise ratio |
| Stereo | 19 kHz pilot detection / stereo indication |
| RDS PS | Programme Service / station name |
| RadioText | Scrolling song / show / metadata text |
| Backend | `rtl_fm` + `redsea` pipeline |

### RDS support

Recent SignalScope updates include:

- stable **RDS PS decoding**
- **RadioText stabilisation**
- improved **hub card rendering** for RDS data
- scrolling **RadioText** on hub cards when text is long

---

## 12. SDR Device Registry

Register each dongle under **Settings → Hub & Network → SDR Devices**.

| Field | Description |
|---|---|
| Serial | The value programmed with `rtl_eeprom` |
| Role | DAB, FM, or unassigned |
| PPM | Frequency correction from `rtl_test -p` |
| Label | Friendly display name |

This registry prevents two inputs from claiming the same dongle at once.

---

## 13. RTL-SDR Troubleshooting

### No dongles detected

- On Linux, check whether `dvb_usb_rtl28xxu` is still loaded
- Replug the dongle after blacklisting
- Confirm detection with `rtl_test -t`

### DAB scan finds no services

- weak signal
- wrong multiplex/channel
- PPM too far off
- driver conflict still active

### FM audio works but RDS is missing

Check:

- `redsea` is installed and executable
- the FM pipeline is starting correctly
- the input is receiving a strong enough signal for RDS lock
- the metadata is reaching the hub payload (`fm_rds_ps`, `fm_rds_rt`)

### Dongle in use

A dongle is already claimed by another input. Use role-based filtering and assign separate devices.

---

## 14. PTP Clock Monitoring

SignalScope passively monitors IEEE 1588 / AES67 PTP multicast on:

- `224.0.1.129:319`
- `224.0.1.129:320`

No PTP slave daemon is required.

This is a passive observer, so absolute offset quality depends on the host's own time synchronisation.

### Fields displayed

| Field | Description |
|---|---|
| Grandmaster ID | Current PTP grandmaster identity |
| Offset (µs) | Deviation from rolling baseline |
| Drift (µs) | Rate of change |
| Jitter (µs) | Standard deviation over recent samples |
| State | `idle`, `locking`, `locked`, `warn`, `alert`, or `lost` |

---

## 15. AES67 / RTP Monitoring

SignalScope monitors RTP streams for:

- packet loss percentage
- stream availability
- audio level and silence state
- alerting thresholds

These are useful for AES67 contribution and monitoring paths, STL links, and multicast distribution.

---

## 16. Multi-Site Hub

### 16.1 Modes

| Mode | Behaviour |
|---|---|
| Client | Sends heartbeats to hub, usually on port 5000 |
| Hub | Receives from client sites and serves `/hub` |
| Both | Local monitor and hub on one machine |

### 16.2 Data sent to the hub

Typical payload fields include:

```json
{
  "name": "Cool FM 97.4mhz",
  "level_dbfs": -18.4,
  "rtp_loss_pct": 0.0,
  "fm_signal_dbm": -62.0,
  "fm_snr_db": 24.3,
  "fm_stereo": true,
  "fm_rds_ps": "COOL FM",
  "fm_rds_rt": "Artist - Title",
  "dab_snr": 18.0,
  "dab_sig": 72,
  "dab_ensemble": "Digital One",
  "dab_service": "Cool FM"
}
```

### 16.3 Security layers

- HMAC-SHA256 signing
- timestamp validation
- nonce tracking
- AES-256-GCM encrypted payloads where available
- request rate limiting

### 16.4 NAT traversal

When a client sits behind NAT, the hub can use reverse relay for live listen traffic.

---

## 17. Now Playing Integration

SignalScope can display external now playing metadata, including **Planet Radio** data on stream cards and hub cards.

Recent updates include:

- improved now playing text handling
- image loading fixes via local artwork proxying
- better behaviour when artwork is unavailable

---

## 18. HTTPS & Let's Encrypt

Hub instances can obtain and renew Let's Encrypt certificates from within the app.

### Behaviour by mode

| Mode | Port |
|---|---|
| Hub / Both without cert | 80 (HTTP) |
| Hub / Both with cert | 443 (HTTPS) with 80 redirect |
| Client | 5000 (HTTP) |

---

## 19. Security

| Feature | Detail |
|---|---|
| Passwords | PBKDF2-SHA256 salted hashes |
| CSRF | Per-session CSRF protection |
| CSP | Content-Security-Policy with nonces |
| Session cookies | `HttpOnly`, `SameSite=Lax`, `Secure` on HTTPS |
| Brute force | Lockout after repeated failures |
| Config file | Restricted permissions on save/load |
| Hub crypto | Authenticated encrypted payloads |

---

## 20. Notifications

Supported notification targets include:

- email
- Pushover
- webhooks for Slack / Teams style workflows

Alert WAV clips can be attached where appropriate.

---

## 21. SLA Dashboard & Reports

SignalScope includes:

- uptime / SLA dashboard
- monthly reset behaviour
- alert log and filtering
- CSV export
- merged reports on hub instances

---

## 22. File Layout

| Path | Purpose |
|---|---|
| `signalscope.py` | Main single-file application |
| `install_signalscope.sh` | Linux installer |
| `lwai_config.json` | Application configuration |
| `alert_log.json` | Persistent alert log |
| `hub_state.json` | Stored hub state |
| `.flask_secret` | Flask session secret |
| `ai_models/` | Per-stream ONNX model files and baseline stats |
| `alert_snippets/` | Saved alert WAV clips |
| `certs/` | TLS material for hub mode |
| `venv/` | Python virtual environment |
| `static/` | CSS, JS, images, and frontend assets |

---

## 23. HTTP API Reference

### 23.1 Client routes

| Route | Description |
|---|---|
| `GET /` | Dashboard |
| `GET /status.json` | Live status JSON |
| `POST /start` / `POST /stop` | Start / stop monitoring |
| `GET /stream/<idx>/live` | Live MP3 audio |
| `GET /stream/<idx>/audio.wav` | Download WAV clip |
| `GET /reports` | Reports page |
| `GET /reports.csv` | Reports CSV export |
| `GET /sla` | SLA page |
| `GET+POST /settings` | Settings page |
| `GET /api/sdr/scan` | Scan connected RTL-SDR devices |
| `GET /api/dab/scan` | Scan DAB multiplex |
| `GET /setup` | First-run setup |
| `GET /api/nowplaying_art/<id>` | Local artwork proxy for now playing images |

### 23.2 Hub routes

| Route | Description |
|---|---|
| `GET /hub` | Hub dashboard |
| `GET /hub/data` | Hub data JSON |
| `GET /hub/reports` | Merged hub reports |
| `POST /api/v1/heartbeat` | Receive client heartbeat |
| `POST /api/v1/audio_chunk/<slot>` | Receive relay audio from client |
| `GET /hub/site/<site>/stream/<idx>/live` | Proxy or relay live audio |

---

## 24. Key Defaults

| Setting | Default |
|---|---|
| AI learning period | 24 hours |
| Alert cooldown | 60 seconds |
| Alert clip length | 8 seconds |
| Hub heartbeat | 5 seconds |
| Hub site timeout | 30 seconds |
| Session timeout | 12 hours |
| Login max attempts | 10 |
| Lockout duration | 15 minutes |

---

## 25. Troubleshooting

### Port 80 / 443 permission denied

```bash
readlink -f "$(which python3)"
sudo setcap cap_net_bind_service=+ep /usr/bin/python3.x
```

### Hub client not appearing

- verify hub URL includes `http://` or `https://`
- verify shared secret matches on both ends
- verify DNS resolution and connectivity

### Temporary failure in name resolution

This indicates the client cannot currently resolve the hub hostname. Check:

- DNS settings
- local gateway / internet connectivity
- whether the target hub hostname resolves externally

### Connection refused to hub

The client can resolve the hostname but cannot connect to the service. Check:

- hub service is running
- firewall / NAT rules
- correct port and protocol

### Now playing image shows `?` / broken artwork

Check:

- `make_response` is imported if using the artwork proxy route
- the app route `/api/nowplaying_art/<id>` is reachable
- browser cache is cleared after deployment

### DAB or FM issues

Re-check SDR setup, serial numbers, PPM correction, and signal strength.

---

## 26. Build Information

| Property | Value |
|---|---|
| Product | SignalScope |
| Version | 2.5.37 |
| Runtime | Python 3.12 recommended |
| Core deps | `flask`, `numpy`, `onnxruntime`, `onnx`, `waitress`, `cryptography` |
| SDR Python dep | `pyrtlsdr==0.2.93` |
| FM chain | `rtl_fm` + `redsea` |
| DAB backend | `welle-cli` |
| AI model | ONNX autoencoder |
| Hub encryption | AES-256-GCM with fallback mode |
| Password hashing | PBKDF2-SHA256 |
| Layout | Large single-file Python app plus static assets |

---

## 27. Rebrand Notes

SignalScope is the current project name.

Legacy references that may still appear in older docs, logs, or file names include:

- `Livewire AI Monitor`
- `LivewireAIMonitor.py`
- `LWAI`

Where possible, these should now be interpreted as:

- **SignalScope**
- **`signalscope.py`**

---

## 28. Summary

SignalScope is a practical broadcast engineering monitor that now covers:

- off-air **FM** monitoring with **RDS PS + RadioText**
- **DAB** monitoring with service selection and multiplex scanning
- **Axia Livewire / AES67 / RTP / HTTP** source monitoring
- AI-assisted fault detection
- secure multi-site hub operation
- now playing metadata and artwork handling
- Linux service deployment with an installer that can self-fetch the app if required

