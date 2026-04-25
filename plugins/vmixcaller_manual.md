# vMix Caller — SignalScope Plugin Manual

**Plugin version:** 1.4.0  
**Applies to:** SignalScope 3.5+

---

## Contents

1. [What It Does](#1-what-it-does)
2. [How It Works](#2-how-it-works)
3. [Prerequisites](#3-prerequisites)
4. [Installation](#4-installation)
5. [Initial Setup](#5-initial-setup)
6. [Video Preview — SRT Bridge](#6-video-preview--srt-bridge)
7. [The Hub Operator Page](#7-the-hub-operator-page)
8. [The Presenter Page](#8-the-presenter-page)
9. [Saved Meetings](#9-saved-meetings)
10. [Keyboard Shortcuts](#10-keyboard-shortcuts)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. What It Does

The vMix Caller plugin lets your SignalScope hub operator manage Zoom and Teams callers directly from the hub dashboard without touching vMix. The presenter in the studio gets a clean, one-click page to join meetings and see the caller video.

**Operator (hub):**
- Select the site node that is running alongside vMix
- Join and leave Zoom/Teams meetings
- Mute self, stop camera, mute all guests
- See a live participants list pulled from vMix
- Put a caller on air with one click (fires `ZoomSelectParticipantByName`)
- Manage a library of saved meetings

**Presenter (studio):**
- One-click join from a saved meetings list
- Full-screen caller video feed
- Simple in-call controls: mute, camera, leave
- No technical knowledge required

---

## 2. How It Works

```
Hub (internet)                    Client site (studio LAN)
─────────────────────             ──────────────────────────────────
Hub operator page          ←───   Client node polls for commands
queues commands                   every 3 s, executes against
in _pending_cmd[site]             local vMix, reports participants
                                  and status back every ~12 s

Hub browser polls          ←───   POST /api/vmixcaller/report
/api/vmixcaller/state             (participants, vMix version,
every 8 s for updates             connection status)
```

The hub **never** calls the client directly. Commands flow hub → client poll → local vMix. This works through NAT with no firewall changes needed on the client side.

vMix IP and port are configured on the hub operator page and automatically pushed to the client node when you save settings.

---

## 3. Prerequisites

| Requirement | Details |
|-------------|---------|
| SignalScope hub | Public internet or LAN, version 3.5+ |
| SignalScope client node | At the same site as vMix, connected to hub |
| vMix | With a Zoom or Teams input already configured |
| Docker (optional) | On a LAN machine, for caller video preview |

The vMix Caller plugin is **hub/client only**. It will not function usefully in standalone mode.

---

## 4. Installation

1. In SignalScope, go to **Settings → Plugins**
2. Click **Check GitHub for plugins**
3. Find **vMix Caller** and click **⬇ Install**
4. Restart SignalScope when prompted
5. The **vMix Caller** item appears in the navigation bar on both the hub and client nodes

---

## 5. Initial Setup

### Step 1 — Hub: Site & Settings

1. Open **vMix Caller** on the hub
2. Under **Site & Settings**, select the site node that is running alongside vMix from the **vMix Site** dropdown
3. Enter the **vMix IP** and **Port** of the vMix machine as seen from the client node (usually `127.0.0.1` if vMix is on the same machine as the client node, otherwise the LAN IP of the vMix PC)
4. Set **vMix Input #** to the input number of the Zoom/Teams input in vMix
5. Click **💾 Save & Push to Site**

The vMix IP and port are sent to the client node automatically on save. The status bar will show the reported IP once the client checks in (within ~12 s).

### Step 2 — Client Node: Verify

On the client node, browse to **vMix Caller**. The client config page shows:
- The hub URL it is connected to
- The current vMix IP and port (set from the hub, or editable locally)
- A **Test vMix** button — click this to confirm the client can reach vMix

If the test fails, check:
- vMix is running
- The IP and port are correct (default vMix API port is **8088**)
- Windows Firewall is not blocking port 8088

### Step 3 — vMix: Zoom/Teams Input

Ensure you have a Zoom or Teams input in vMix with callers visible. The plugin reads participants from the vMix XML status feed and lists them in the Participants panel.

No special vMix configuration is needed beyond the normal Zoom integration. The vMix API must be enabled (it is on by default).

---

## 6. Video Preview — SRT Bridge

The caller video preview is **optional**. Meeting controls (join, leave, mute, put on air) work without it.

To enable the preview, you run an SRS Docker container that receives an SRT stream from vMix and converts it to HLS, which SignalScope then proxies to the browser.

### Option A — Bridge on the Studio LAN *(recommended)*

Run the bridge on any Ubuntu machine on the **same LAN as vMix** — this can be the SignalScope client node itself.

**1. Start the SRS container:**

```bash
docker run -d --name srs-srt --restart unless-stopped \
  -p 10080:10080/udp -p 8080:8080 \
  ossrs/srs:5 ./objs/srs -c conf/srt.conf
```

**2. Configure vMix SRT output:**

In vMix, open the Zoom input settings → **Output** → enable **SRT Output**:

| Field | Value |
|-------|-------|
| Type | Caller |
| Hostname | LAN IP of the bridge machine (e.g. `192.168.13.2`) |
| Port | `10080` |
| Stream ID | `#!::h=live/caller,m=publish` |
| Latency | `500` (ms) |
| Quality | H264 2mbps AAC 128kbps (or as required) |

**3. Set the Bridge URL in the plugin:**

On the hub operator page, enter the bridge URL:
```
http://192.168.13.2:8080/live/caller.m3u8
```
(Replace `192.168.13.2` with the actual LAN IP of the machine running Docker.)

Click **💾 Save & Push to Site**.

**4. Presenter page URL — HTTPS hubs:**

If your hub uses HTTPS, the presenter must open the presenter page from the **client node** URL, not the hub URL. This avoids the browser blocking HTTP video as mixed content. Give the presenter this bookmark:

```
http://[client-node-ip]:[port]/hub/vmixcaller/presenter
```

The video is proxied through the client node's local SignalScope instance, which can reach the LAN bridge. No certificates or firewall changes are needed.

---

### Option B — Bridge on the Hub Server

Use this if the presenter will be accessing the presenter page from outside the studio LAN (e.g. a remote producer), or if you prefer to centralise everything on the hub.

vMix pushes SRT over the internet to the hub's public IP. The hub requires **UDP port 10080** open in your firewall.

**1. Start the SRS container on the hub server:**

```bash
docker run -d --name srs-srt --restart unless-stopped \
  -p 10080:10080/udp -p 127.0.0.1:8080:8080 \
  ossrs/srs:5 ./objs/srs -c conf/srt.conf
```

Note: port 8080 is bound to `127.0.0.1` only — SignalScope proxies it to authenticated browsers. Port 10080 (SRT input) is open so vMix can push from outside.

**2. Configure vMix SRT output:**

Same as Option A but set the Hostname to the **hub's public IP**.

**3. Set the Bridge URL:**

```
http://127.0.0.1:8080/live/caller.m3u8
```

SignalScope detects the localhost URL and proxies the stream through `/hub/vmixcaller/video/...`. The video is only accessible to authenticated SignalScope sessions.

The presenter can use the hub URL directly — `https://your-hub/hub/vmixcaller/presenter` — because the hub is serving the video through its own HTTPS endpoint.

---

## 7. The Hub Operator Page

Navigate to **vMix Caller** on the hub. This is the back-of-house control surface.

### Status Bar

The top bar shows the connection state of the selected site:

| Indicator | Meaning |
|-----------|---------|
| 🟢 Green dot | Client connected, vMix reachable |
| 🟡 Amber dot | Client connected, waiting for first report |
| 🔴 Red dot | vMix unreachable — check IP, port, or vMix running state |
| No site selected | Choose a site in the Settings card |

The status bar also shows the vMix version and the IP/port the client is currently reporting, confirming that the pushed config was applied.

### Site & Settings

| Field | Description |
|-------|-------------|
| vMix Site | The SignalScope client node running alongside vMix |
| vMix IP | IP of the vMix machine as seen from the client node |
| vMix Port | vMix API port (default: 8088) |
| vMix Input # | Input number of the Zoom/Teams source in vMix |
| Bridge URL | Internal URL of the SRS HLS output (for video preview) |

Click **💾 Save & Push to Site** to save and immediately send the vMix IP/port to the client node.

### Meeting Controls

**Joining a meeting:**
1. Enter the **Meeting ID** (spaces are fine — vMix strips them)
2. Enter the **Passcode** if required
3. Set a **Display Name** for vMix to use in the meeting
4. Click **📞 Join Meeting**

Once joined, the Join button is replaced by call controls:

| Button | Action |
|--------|--------|
| 🔇 Mute Self | Toggle vMix's own microphone mute |
| 📷 Stop Camera | Toggle vMix's camera on/off |
| 🔇 Mute All | Mute all guest participants |
| 📴 Leave | End the meeting in vMix |

> Commands are queued on the hub and executed by the client node on its next poll (within 3 s).

### Participants Panel

Once the client has reported participants (within ~12 s of joining), they appear here with mute and on-air status badges.

**Put On Air:** Click the **📺 Put On Air** button next to a participant's name. This fires `ZoomSelectParticipantByName` in vMix, bringing that caller's video to the foreground.

**Manual Add:** If a participant doesn't appear (e.g. the vMix XML feed isn't returning them), type their name in the box and click **+ Add** to add them manually for the session.

The panel refreshes automatically every ~12 s. Click **↻ Refresh** to force an immediate update.

### Saved Meetings

The lower section of the hub page shows the saved meetings library. See [Section 9 — Saved Meetings](#9-saved-meetings).

---

## 8. The Presenter Page

The presenter page is a clean, technical-free interface designed to be bookmarked on the studio's presentation machine.

**URL:**
- Default: `https://your-hub/hub/vmixcaller/presenter`
- If hub uses HTTPS with a LAN bridge: `http://client-node-ip:port/hub/vmixcaller/presenter`

### Video Feed

The top of the page shows the full-width caller video feed. While no call is active, the feed shows a waiting overlay. Once vMix starts receiving the SRT stream, the overlay disappears automatically.

If no Bridge URL has been configured, a warning banner is shown. Meeting controls still work — video preview is optional.

### Saved Meetings

Below the video, each saved meeting appears as a row:
- **Meeting name** (e.g. "Monday Panel")
- **Meeting ID** (shown beneath the name)
- **📞 Join** button

Clicking **Join** immediately sends the join command to vMix. The video feed activates and the in-call toolbar appears.

All join buttons are disabled while a call is active to prevent accidental double-joining.

### In-Call Toolbar

Once a meeting is joined, a toolbar appears at the top of the page:

| Control | Action |
|---------|--------|
| **● ON CALL** badge | Visual confirmation a meeting is active |
| 🔇 Mute Self | Toggle microphone mute in vMix |
| 📷 Stop Camera | Toggle camera in vMix |
| 📴 Leave | End the meeting |

### Manual Join

For meetings not in the saved list, tap **＋ Join a different meeting…** below the saved meetings card. A form expands with fields for Meeting ID, Passcode, and Display Name. Tap **✕ Cancel** to collapse it.

---

## 9. Saved Meetings

Saved meetings are managed on the hub operator page and appear on the presenter page.

### Adding a Meeting

On the hub operator page, scroll to the **📋 Saved Meetings** card:

1. Enter a **Name** (e.g. "Monday Panel") — this is what the presenter sees
2. Enter the **Meeting ID**
3. Enter the **Passcode** (optional)
4. Click **+ Add**

The meeting appears immediately on the presenter page. No restart required.

> **Tip:** The display name used when joining (e.g. "Guest Producer") is set per meeting but defaults to "Guest Producer" if left blank. Edit this in the add meeting form's Display Name field if you add one in a future update.

### Deleting a Meeting

On the hub operator page, click **✕** next to any saved meeting to remove it. The change is immediate.

### Joining from the Hub

The operator can also join a saved meeting directly from the hub page using the **📞 Join** button next to each saved meeting. The selected site must be chosen first.

---

## 10. Keyboard Shortcuts

These shortcuts work on both the hub operator page and the presenter page, provided a text input is not focused:

| Key | Action |
|-----|--------|
| **M** | Mute / unmute self |
| **C** | Stop / start camera |

---

## 11. Troubleshooting

### "No site selected" / status dot stays grey

- Check a site is chosen in the Site & Settings dropdown on the hub
- Click **💾 Save & Push to Site** after selecting the site
- Wait up to 15 s for the client to check in

### Status shows "vMix unreachable"

- On the client node, go to vMix Caller → click **🔌 Test vMix**
- Check vMix is running and the API is enabled (vMix Settings → Web Controller)
- Verify the vMix IP and port — if vMix is on the same PC as the client node, use `127.0.0.1:8088`
- Check Windows Firewall is not blocking port 8088

### Participants list is empty after joining

- Wait ~12 s for the client to report (the first report after a join may take one full poll cycle)
- Click **↻ Refresh** to force an update
- Confirm the **vMix Input #** matches the Zoom/Teams input number in vMix
- Check the Zoom call is active and callers have joined in vMix

### Video feed shows "Stream unavailable"

- Confirm the SRS Docker container is running: `docker ps | grep srs-srt`
- Confirm vMix SRT output is enabled and connected (green indicator in vMix)
- Check the Bridge URL entered matches the SRS machine's IP and port
- For Option A (LAN bridge): confirm the presenter is accessing the presenter page from the **client node URL**, not the hub URL (especially important for HTTPS hubs)

### Video plays but is black / frozen

- In vMix, check the Zoom input is receiving video (not a blank caller)
- Try restarting the SRS container: `docker restart srs-srt`
- In the presenter page, reload the page to reinitialise the video element

### Commands are slow / participants don't update

- Commands are executed within 3 s of the hub receiving them (one client poll cycle)
- Participant updates happen every ~12 s. This is normal — click **↻ Refresh** for an immediate poll

### vMix IP/port settings not applying on the client

- The config is pushed on save. After clicking **💾 Save & Push to Site**, wait up to 3 s for the client to collect the command, then check the status bar — it shows the IP the client is reporting
- Alternatively, set the IP directly on the client node's vMix Caller config page and click **💾 Save**

---

*vMix Caller is a SignalScope plugin by Conor Ewings. For issues, visit the SignalScope GitHub repository.*
