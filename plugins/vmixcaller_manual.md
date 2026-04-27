# vMix Caller — SignalScope Plugin Manual

**Plugin version:** 1.7.3  
**Applies to:** SignalScope 3.5+

---

## Contents

1. [What It Does](#1-what-it-does)
2. [How It Works](#2-how-it-works)
3. [Prerequisites](#3-prerequisites)
4. [Installation](#4-installation)
5. [Initial Setup](#5-initial-setup)
6. [Zoom API Integration](#6-zoom-api-integration)
7. [Zoom Participant Management](#7-zoom-participant-management)
8. [Zoom Webhooks](#8-zoom-webhooks)
9. [Video Preview — SRT Bridge](#9-video-preview--srt-bridge)
10. [Video Preview — NDI](#10-video-preview--ndi)
11. [The Client Operator Page](#11-the-client-operator-page)
12. [The Hub Page](#12-the-hub-page)
13. [The Presenter Page](#13-the-presenter-page)
14. [Saved Meetings](#14-saved-meetings)
15. [Keyboard Shortcuts](#15-keyboard-shortcuts)
16. [Troubleshooting](#16-troubleshooting)

---

## 1. What It Does

The vMix Caller plugin gives you full control of Zoom meetings from within SignalScope — both via the vMix API (call controls, participants, put on air) and directly through the Zoom API (create meetings, view live and upcoming meetings, manage participants, end meetings).

**Client node (primary operator surface):**
- Full meeting controls — join, leave, mute self, stop camera, mute all guests
- Live participants list pulled directly from vMix
- One-click Put On Air per caller (fires `ZoomSelectParticipantByName`)
- Zoom API meetings panel — upcoming and live meetings with Join, End, and +Save buttons
- Create new Zoom meetings on demand
- Live Zoom participant management — mute individuals, remove participants, admit from waiting room
- Manage saved meeting presets

**Hub page:**
- Zoom API credentials configuration (Account ID, Client ID, Client Secret)
- Zoom webhook endpoint configuration for real-time participant events
- Zoom meetings overview — same list visible on all client nodes
- Live Zoom participant panel (appears once a meeting is active)
- Site selector and vMix config push
- Saved meetings management

**Presenter page (studio machine bookmark):**
- **Studio picker** — on first visit, shows all configured studios; presenter selects theirs
- Each presenter independently picks their studio — multiple studios can be active simultaneously
- One-click join from saved meetings list
- Full-screen caller video feed with Hear Caller audio
- In-call controls: mute, camera, leave
- Reconnect button
- No technical knowledge required

---

## 2. How It Works

```
Hub (internet)                         Client site (studio LAN)
──────────────────────────             ──────────────────────────────────────
Holds Zoom API credentials  ←───────  Client polls /api/vmixcaller/cmd
Calls Zoom REST API                    every 3 s, executes against
Caches meetings (60 s)                 local vMix API, reports back

Zoom meetings data          ────────→  Client page (primary UI)
served to client via                   Operator joins/ends meetings,
HMAC-signed proxy requests             browses participants, creates meetings

vMix commands queued        ←───────  Client reports vMix status,
in _pending_cmd[site]                  participants every ~12 s

Zoom webhook events         ────────→  Hub invalidates participant cache,
(real-time from Zoom)                  browser polls and refreshes within 15 s
```

**Three layers of control run in parallel:**

| Layer | What it does | Who uses it |
|-------|-------------|-------------|
| **vMix API** | Join/leave/mute/camera inside vMix, read participants | Client node talks to local vMix |
| **Zoom API** | List, create, end meetings; manage cloud participants | Hub calls Zoom; client proxies through hub |
| **Hub command bus** | Relay vMix commands hub→client over NAT | Hub queues; client polls and executes |

The hub **never** calls the client directly. All communication is client-initiated (polling). This works through NAT with no firewall changes needed on the client side.

Zoom credentials live only on the hub. Client nodes proxy Zoom requests to the hub using HMAC-signed requests — credentials are never transmitted to or stored on client nodes.

---

## 3. Prerequisites

| Requirement | Details |
|-------------|---------|
| SignalScope hub | Public internet or LAN, version 3.5+ |
| SignalScope client node | At the same site as vMix, connected to hub |
| vMix | With a Zoom input already configured |
| Zoom Server-to-Server OAuth app | For Zoom API features (optional but recommended) |
| SRS Docker or NDI (optional) | For caller video preview |

The vMix Caller plugin requires hub/client mode. It will not function usefully in standalone mode.

**Teams note:** Teams has no vMix API for call control. Teams NDI feeds already in vMix can be monitored passively but cannot be joined or controlled by this plugin.

---

## 4. Installation

1. In SignalScope, go to **Settings → Plugins**
2. Click **Check GitHub for plugins**
3. Find **vMix Caller** and click **⬇ Install**
4. Restart SignalScope when prompted
5. The **vMix Caller** item appears in the navigation bar on both hub and client nodes

---

## 5. Initial Setup

### Step 1 — Hub: Site & vMix Configuration

1. Open **vMix Caller** on the hub
2. Under **Site & Settings**, select the site node running alongside vMix from the **vMix Site** dropdown
3. If you have multiple vMix machines, configure **multiple instances** — each instance gets its own name, vMix IP/port, input number, and bridge URL
4. For each instance, enter:
   - **Instance name** (e.g. "Studio 1", "OB Unit") — this is the studio name presenters see
   - **vMix IP** — as seen from the client node (usually `127.0.0.1` if vMix is on the same machine, otherwise the LAN IP of the vMix PC)
   - **vMix Port** — default is `8088`
   - **vMix Input #** — the input number of the Zoom source in vMix
5. Click **💾 Save & Push to Site**

The vMix IP, port, and input are sent to the client node automatically on save.

### Step 2 — Client Node: Verify

Browse to **vMix Caller** on the client node. The page shows:
- The hub it is connected to
- The active vMix instance and its current IP/port
- A **Test vMix** button — click to confirm the client can reach vMix

If the test fails:
- Confirm vMix is running
- Check the IP and port are correct (default vMix API port is **8088**)
- Check Windows Firewall is not blocking port 8088

### Step 3 — vMix: Confirm Zoom Input

Ensure you have a Zoom input in vMix with callers visible. The plugin reads participants from the vMix XML status feed. No special vMix configuration is needed beyond the normal Zoom integration. The vMix API must be enabled (it is on by default — vMix Settings → Web Controller).

---

## 6. Zoom API Integration

The Zoom API integration lets you view, create, and end Zoom meetings directly from the vMix Caller interface, without needing to open the Zoom web portal or app. The hub acts as the API bridge — credentials never leave the hub.

### 6.1 Create a Zoom Server-to-Server OAuth App

1. Sign in at [marketplace.zoom.us](https://marketplace.zoom.us)
2. Go to **Develop → Build App**
3. Choose **Server-to-Server OAuth** and create the app
4. Under **Scopes**, add:
   - `meeting:read:admin`
   - `meeting:write:admin`
5. Note your **Account ID**, **Client ID**, and **Client Secret**
6. Activate the app

### 6.2 Enter Credentials on the Hub

1. Open **vMix Caller** on the hub
2. Find the **Zoom API** card
3. Enter your **Account ID**, **Client ID**, and **Client Secret**
4. Click **Save & Test**

The hub tests the credentials immediately and shows the connected account name and email. A green dot confirms the connection is working. Credentials are stored on the hub only and never sent to client nodes.

Bearer tokens are cached for up to one hour and refreshed automatically 30 seconds before expiry.

### 6.3 Zoom Meetings Panel

Once credentials are configured, the **Zoom Meetings** panel appears on both the hub page and the client operator page. It shows all upcoming and currently live meetings fetched from the Zoom API (refreshed every 60 seconds automatically, or manually with the ↻ button).

Each meeting row shows:
- Live/scheduled status indicator
- Meeting topic and ID
- Scheduled start time (for upcoming meetings)
- **LIVE** badge for in-progress meetings

**Available actions per meeting:**

| Button | Action |
|--------|--------|
| **Join** | Sends the join command to vMix for the selected instance |
| **End** | Ends the meeting for all participants (confirmation prompt shown) |
| **+Save** | Adds the meeting to your saved meetings presets for the Presenter page |

When exactly one meeting is live, the participant panel auto-activates for that meeting without requiring a manual selection.

### 6.4 Creating a New Meeting

Click **+ Create** in the Zoom Meetings panel header to expand the creation form:

| Field | Description |
|-------|-------------|
| **Topic** | Meeting name visible to participants |
| **Passcode** | Optional — leave blank for no passcode |
| **Duration (min)** | 15–480 minutes, default 60 |
| **Waiting room** | Tick to require host approval before participants join |

Click **▶ Start Now & Join in vMix** to create the meeting and immediately send the join command to vMix. The meetings list refreshes automatically after 2 seconds.

### 6.5 How the Hub Proxy Works

When a client node needs Zoom data or wants to perform a Zoom action, it sends a request to the hub:

- **Read** (meetings list, participants): client sends a HMAC-signed GET — the hub checks site approval and HMAC signature, then returns cached data
- **Write** (create/end/mute/remove): client sends an approval-gated POST — the hub verifies site approval, calls the Zoom API, and returns the result

The browser on the client node only ever talks to the client's own local Flask server. Zoom API calls are never made from the browser directly.

---

## 7. Zoom Participant Management

Once a meeting is joined (or auto-detected as live), a **Zoom Participants** card appears on the hub and client pages. It shows everyone in the call and anyone waiting in the waiting room.

### Participant List

Each participant row shows:
- 🔊 or 🔇 — whether audio is active or muted
- 📹 — if video is on
- **HOST** badge for the meeting host
- **Mute** / **Req. Unmute** button
- **Remove** button (not shown for host)

The list refreshes automatically every 15 seconds while a meeting is active. Click **↻ Refresh** to force an immediate update.

### Mute / Unmute

- **Mute**: immediately mutes the participant's audio at the Zoom cloud level.
- **Req. Unmute**: sends an unmute request — the participant must accept it on their end (Zoom does not allow hosts to force-unmute guests).

### Remove a Participant

Click **Remove** then confirm. The participant is disconnected from the Zoom meeting immediately.

### Waiting Room

If your meeting has a waiting room enabled, a **Waiting Room** section appears above the participant list when anyone is queued:

- Click **Admit** next to an individual to let them in
- Click **Admit All** to admit everyone waiting at once

The waiting room section hides automatically when it is empty.

### Active Meeting Tracking

The participant card activates automatically when you:
- Join via the Zoom Meetings list (the meeting is tracked from the Join click)
- Use the saved meetings Quick Join
- Use manual join (enter a Meeting ID and click Join)
- When exactly one meeting is detected as live at page load

Click **Leave** to leave the meeting — this also hides the participant card.

---

## 8. Zoom Webhooks

Webhooks give the participant panel near-real-time updates (within 15 seconds of any join, leave, or waiting room change) instead of relying purely on polling.

Webhooks are **optional** — the participant panel works by polling every 15 seconds even without them. Webhooks simply make it more responsive.

### 8.1 Add an Event Subscription in Zoom

1. In your Zoom Server-to-Server OAuth app at [marketplace.zoom.us](https://marketplace.zoom.us), go to **Feature → Event Subscriptions**
2. Click **+ Add Event Subscription**
3. Set the **Endpoint URL** to the value shown in the **Webhook Endpoint URL** field on the hub's Zoom API card (it looks like `https://your-hub/api/vmixcaller/zoom_webhook`)
4. Under **Event types**, subscribe to:
   - Meeting → Participant/Host joined meeting
   - Meeting → Participant/Host left meeting
   - Meeting → Participant joined waiting room
   - Meeting → Participant left waiting room
   - Meeting → Meeting started
   - Meeting → Meeting ended
5. Save the subscription — Zoom will send a URL validation request automatically

### 8.2 Set the Webhook Secret Token on the Hub

1. Open **vMix Caller** on the hub, find the **Zoom API** card
2. In the **Webhook Secret Token** field, paste the **Secret Token** shown by Zoom for the event subscription (under the subscription's details)
3. Click **Save & Test** to save it alongside your other credentials

When a secret token is configured, all incoming webhook requests are validated via HMAC-SHA256 before being processed. Without a token, the endpoint accepts all requests (development mode only — always configure a token in production).

### 8.3 What Happens on a Webhook Event

| Event | Effect |
|-------|--------|
| Participant joined/left | Participant cache invalidated; next browser poll returns fresh data |
| Participant joined/left waiting room | Waiting room cache invalidated |
| Meeting started | Meetings list cache invalidated |
| Meeting ended | Meetings list and participant caches both invalidated |

### 8.4 Testing the Webhook

After saving the secret token and creating the event subscription, Zoom sends an automatic URL validation request to confirm the endpoint is reachable. This is handled silently by the plugin. No action is needed.

To verify webhooks are arriving, watch the participant panel — when a new participant joins Zoom you should see the list update within a few seconds of the next 15-second poll cycle.

---

## 9. Video Preview — SRT Bridge

Caller video preview is **optional**. All meeting controls and the Zoom API features work without it.

To enable the SRT bridge preview, run an SRS Docker container that receives an SRT stream from vMix and converts it to HLS.

### Option A — Bridge on the Studio LAN *(recommended)*

Run the bridge on any Ubuntu machine on the **same LAN as vMix** — the SignalScope client node works well for this.

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
| Latency | `500` ms |
| Quality | H264 2 Mbps AAC 128 kbps (or as required) |

**3. Set the Bridge URL in the plugin:**

On the hub operator page, select the instance, set the **Preview Mode** to **SRT Bridge**, and enter:
```
http://192.168.13.2:8080/live/caller.m3u8
```

Click **💾 Save & Push to Site**.

**4. HTTPS hubs — presenter page URL:**

If your hub uses HTTPS, open the presenter page from the **client node URL**, not the hub URL. This prevents the browser blocking HTTP video as mixed content:
```
http://[client-node-ip]:[port]/hub/vmixcaller/presenter
```

---

### Option B — Bridge on the Hub Server

Use this if presenters will be accessing the page from outside the studio LAN, or if you prefer to centralise everything on the hub.

vMix pushes SRT over the internet to the hub's public IP. The hub requires **UDP port 10080** open in your firewall.

**1. Start the SRS container on the hub server:**

```bash
docker run -d --name srs-srt --restart unless-stopped \
  -p 10080:10080/udp -p 127.0.0.1:8080:8080 \
  ossrs/srs:5 ./objs/srs -c conf/srt.conf
```

Port 8080 is bound to `127.0.0.1` only — SignalScope proxies it to authenticated browsers. Port 10080 (SRT input) is open so vMix can push from the studio.

**2. Configure vMix SRT output:**

Same as Option A but set the Hostname to the **hub's public IP**.

**3. Set the Bridge URL:**

```
http://127.0.0.1:8080/live/caller.m3u8
```

The presenter opens: `https://your-hub/hub/vmixcaller/presenter`

---

## 10. Video Preview — NDI

NDI preview is an alternative to the SRT bridge that requires no Docker and no port forwarding. The client node running alongside vMix receives the NDI output directly, encodes it to HLS, and pushes segments to the hub.

### Requirements

- `ndi-python` installed on the **client node** (`pip install ndi-python` — the NDI runtime is bundled, no separate SDK download needed)
- Alternatively, use the SignalScope installer with the `--ndi` flag: it will prompt you during setup
- Linux x86-64 or aarch64 (Raspberry Pi 64-bit) — Windows is not currently supported for NDI relay
- vMix NDI output enabled for the Zoom caller input

### Setup

1. In vMix, enable NDI output for the Zoom input. Note the **NDI source name** (typically `VMIX-PC (vMix - Input N - Zoom)` — visible in any NDI source browser)

2. On the hub operator page, select the instance and set **Preview Mode** to **NDI**

3. In the **NDI Source Name** field, type the source name exactly as it appears in vMix — or click **Discover** to scan the LAN and pick from a live list

   > **Discover** on a hub node proxies the scan to the connected client site so you see sources visible at the studio, not the hub machine

4. Click **💾 Save & Push to Site**

### How It Works

Once configured, the client node:
1. Opens the NDI source via `ndi-python`
2. Encodes video to HLS using ffmpeg via a named pipe
3. Pushes TS segments to the hub over the existing HMAC-authenticated channel
4. The hub serves a synthetic HLS manifest to authenticated browsers

The presenter page loads the video through the hub's authenticated proxy — no LAN access needed from the presenter's browser, and no HTTP/HTTPS mixed-content issues.

### NDI vs SRT Bridge Comparison

| | SRT Bridge | NDI |
|-|-----------|-----|
| Docker required | Yes | No |
| Port forwarding | UDP 10080 for hub-server option | None |
| LAN reach needed | Browser must reach SRS (Option A) or hub does (Option B) | None — hub serves all browsers |
| Platform | Any | Linux x64/aarch64 only |
| Setup complexity | Medium | Low |

---

## 11. The Client Operator Page

The client node page is the **primary control surface** for day-to-day operation. Navigate to **vMix Caller** on the client node machine (the one running alongside vMix).

The client page has the full admin UI — everything available on the hub page is also here, plus direct vMix status.

### Status Bar

| Indicator | Meaning |
|-----------|---------|
| Green dot | Connected to hub, vMix reachable |
| Amber dot | Connected to hub, waiting for first vMix report |
| Red dot | vMix unreachable — check IP, port, or whether vMix is running |

The status bar shows the vMix version and the current IP/port the client is using.

### Instance Selector

If multiple vMix instances are configured, a row of buttons at the top lets you switch between them. The active instance is highlighted. Switching takes effect immediately — the Participants panel refreshes for the new instance.

### Meeting Controls

**Joining a meeting:**
1. Enter the **Meeting ID** (spaces are fine — vMix strips them)
2. Enter the **Passcode** if required
3. Set a **Display Name** for vMix to use in the meeting
4. Click **📞 Join Meeting**

Once joined, the Join form is replaced by call controls:

| Button | Action |
|--------|--------|
| Mute Self | Toggle vMix microphone mute |
| Stop Camera | Toggle vMix camera on/off |
| Mute All | Mute all guest participants |
| Leave | End the call in vMix |

Commands are queued on the hub and executed by the client node within 3 seconds.

### Participants Panel

Participants are pulled directly from the vMix XML status feed and updated every ~12 seconds. Click **↻ Refresh** to force an immediate update.

**Put On Air:** Click **📺 Put On Air** next to a participant's name. This fires `ZoomSelectParticipantByName` in vMix, bringing that caller's video to the foreground.

**Manual Add:** If a participant doesn't appear in the list, type their name and click **+ Add** to add them manually for the session.

### Zoom Meetings Panel

The Zoom API meetings panel on the client page works identically to the hub page. See [Section 6](#6-zoom-api-integration) for full details. No Zoom credentials are needed or stored on the client node.

### Zoom Participants Panel

See [Section 7 — Zoom Participant Management](#7-zoom-participant-management). The panel appears automatically once a meeting is active.

### Saved Meetings

The lower section shows the saved meetings library, with **📞 Join** buttons for each preset. See [Section 14 — Saved Meetings](#14-saved-meetings).

---

## 12. The Hub Page

The hub page is primarily used for initial configuration and Zoom API setup. Day-to-day operation happens on the client page.

### What the hub page is for

- **Zoom API credentials** — enter Account ID, Client ID, Client Secret once; test the connection
- **Webhook configuration** — set the Webhook Secret Token for real-time participant events
- **Site & Settings** — choose which site to target, configure vMix instances (name, IP, port, input, preview mode), push config to client nodes
- **Zoom meetings overview** — same meetings list as the client page, useful for monitoring
- **Zoom participants** — live participant management once a meeting is active
- **Saved meetings management** — add/remove meeting presets

The hub page has the same meeting controls and participants panel as the client page, operated from the hub for situations where a hub operator needs to intervene remotely.

---

## 13. The Presenter Page

The presenter page is designed to be bookmarked on the studio's presentation machine — no SignalScope knowledge needed.

**URL:**
- HTTPS hub with LAN SRT bridge: `http://[client-node-ip]:[port]/hub/vmixcaller/presenter`
- NDI preview, hub-server SRT bridge, or HTTP hub: `https://your-hub/hub/vmixcaller/presenter`

### Studio Picker

When multiple vMix instances (studios) are configured, the presenter page opens with a **studio selection screen** instead of going straight to the call controls.

Each studio appears as a card showing:
- The studio name (from the vMix instance name)
- The vMix machine IP

The presenter clicks **🎙 Enter Studio** for their studio. The page then loads with the correct video feed and vMix connection for that studio.

**Key behaviours:**
- Selection is per-session and in the URL (`?inst=<id>`) — multiple presenters can be in different studios simultaneously without affecting each other
- A **◂ Studios** link appears top-right so the presenter can return to the picker to switch studios
- If only one studio is configured, the picker is skipped entirely

### Video Feed

The top of the page shows the full-width caller video. While no call is active, a waiting overlay is shown. Once vMix begins receiving the stream, the overlay clears automatically.

If no Bridge URL or NDI source is configured, a notice is shown but meeting controls still work — video preview is optional.

### Hear Caller Audio

Below the video, a **🔇 Hear Caller** button lets the presenter monitor the caller's audio feed directly in the browser. Click again to silence it.

### Saved Meetings

Each saved meeting appears as a card showing:
- Meeting name (e.g. "Monday Panel")
- Meeting ID

Click **📞 Join** to send the join command to vMix immediately. All join buttons are disabled while a call is active to prevent accidental double-joining.

### In-Call Controls

Once a meeting is joined, a toolbar appears:

| Control | Action |
|---------|--------|
| ● ON CALL badge | Visual confirmation the call is active |
| 📡 ON AIR badge | Pulses when a caller is live in vMix (ZoomSelectParticipantByName active) |
| Mute Self | Toggle microphone mute in vMix |
| Leave | End the call in vMix |
| Reconnect | Rejoin the same meeting ID if the call drops |

### Manual Join

For meetings not in the saved list, tap **＋ Join a different meeting…** below the presets. Enter Meeting ID and Passcode, then tap Join.

---

## 14. Saved Meetings

Saved meetings are named presets combining a meeting ID and passcode. They appear on the presenter page and operator pages for one-click joining.

### Adding a Meeting Manually

On the hub or client operator page, find the **Saved Meetings** card:

1. Enter a **Name** (e.g. "Monday Panel") — this is what the presenter sees
2. Enter the **Meeting ID**
3. Enter the **Passcode** (optional)
4. Enter a **Display Name** for vMix (optional — defaults to "Guest Producer")
5. Click **+ Add**

The meeting appears on the presenter page immediately. No restart required.

### Importing from the Zoom API

If the Zoom API is configured, click **+Save** on any meeting in the Zoom Meetings panel. The meeting topic becomes the preset name and the meeting ID and passcode are filled in automatically.

### Removing a Meeting

Click **✕** next to any saved meeting. The change takes effect immediately.

---

## 15. Keyboard Shortcuts

These shortcuts work on the hub, client, and presenter pages when no text input is focused:

| Key | Action |
|-----|--------|
| **M** | Mute / unmute self |
| **C** | Stop / start camera |

---

## 16. Troubleshooting

### Status dot stays grey / "No site selected"

- Select a site in the Site & Settings dropdown on the hub
- Click **💾 Save & Push to Site** after selecting
- Wait up to 15 s for the client to check in

### "vMix unreachable" on the status bar

- On the client node, open vMix Caller and click **🔌 Test vMix**
- Confirm vMix is running and the API is enabled (vMix Settings → Web Controller)
- If vMix is on the same PC as the client node, use IP `127.0.0.1` and port `8088`
- Check Windows Firewall is not blocking port 8088

### Participants list is empty after joining

- Wait ~12 s for the first client report after joining
- Click **↻ Refresh** to force an update
- Confirm the **vMix Input #** matches the Zoom input number in vMix
- Confirm the Zoom call is active and callers have joined inside vMix

### Zoom participant card not appearing

- The card shows only when a meeting is active (joined via any Join button on the page)
- If you joined from vMix directly without using the plugin, click a Join button in the plugin to register the meeting as active
- If only one Zoom meeting is live on the account, the card activates automatically on page load

### Zoom meetings panel shows "Not configured on hub"

- Open vMix Caller on the hub and enter Zoom API credentials (see [Section 6.1](#61-create-a-zoom-server-to-server-oauth-app))
- Click **Save & Test** — a green dot and account name confirm the connection

### Zoom "Save & Test" fails / red dot after saving

- Double-check the Account ID, Client ID, and Client Secret — copy them fresh from marketplace.zoom.us
- Confirm the Zoom app has been **activated** (not just created) in the Marketplace
- Confirm the scopes `meeting:read:admin` and `meeting:write:admin` are added to the app

### Zoom meetings list is empty or stale

- Click **↻ Refresh** in the Zoom Meetings panel header to force a fresh fetch from the API
- Meetings are cached for 60 seconds — if you just created a meeting externally, wait or refresh manually
- Confirm the Zoom account has upcoming or live meetings — the API only returns meetings for the authenticated user

### Mute/remove participant buttons fail

- Confirm the Zoom API credentials include the `meeting:write:admin` scope
- Zoom does not allow force-unmuting guests — the "Req. Unmute" button sends a request that the participant must accept

### Webhooks not arriving / participant panel slow to update

- Check the **Endpoint URL** in the Zoom event subscription matches what is shown in the plugin's Webhook Endpoint URL field exactly (including `https://` and no trailing slash)
- Confirm the **Webhook Secret Token** in the plugin matches the **Secret Token** shown in the Zoom event subscription settings
- Use Zoom's built-in **Send Test** button in the event subscription to verify the endpoint is reachable
- If your hub is behind a reverse proxy, ensure it passes the `x-zm-signature` and `x-zm-request-timestamp` headers through unchanged

### Presenter page shows "Select your studio" instead of going to the controls

- This is the intended studio picker — it appears when multiple vMix instances are configured
- Click **🎙 Enter Studio** on the correct studio card
- To go back and switch studios, click **◂ Studios** top-right

### Video feed shows "Stream unavailable" (SRT Bridge)

- Confirm the SRS Docker container is running: `docker ps | grep srs-srt`
- Confirm vMix SRT output is enabled and shows a green indicator in vMix
- Check the Bridge URL in the plugin settings matches the SRS machine's IP and port
- For Option A (LAN bridge) with an HTTPS hub: ensure the presenter page is opened from the **client node URL**, not the hub URL

### NDI preview not working

- Confirm `ndi-python` is installed on the client node: `python3 -c "import ndi; print('ok')"` (run as the SignalScope user)
- Confirm the NDI source name exactly matches what vMix advertises — use **Discover** to check
- NDI is only supported on Linux x86-64 and aarch64; Windows is not currently supported
- Check the client node logs for `[NDI]` lines which show the connection status

### Video plays but is black or frozen

- Check the Zoom input in vMix is receiving live video (not a blank caller screen)
- For SRT: try restarting the SRS container: `docker restart srs-srt`
- Reload the presenter page to reinitialise the video player

### Commands are slow / controls don't respond

- Commands are executed within 3 s (one client poll cycle) — a brief delay is normal
- If controls are consistently unresponsive, check the client node is online and connected to the hub (green dot on the status bar)

### vMix IP/port changes not applying on the client

- Settings are pushed on save. After clicking **💾 Save & Push to Site**, wait up to 3 s for the client to collect the command
- The status bar on the client page shows the IP the client is currently using — confirm it matches what you set

---

*vMix Caller is a SignalScope plugin by Conor Ewings. For issues, visit the [SignalScope GitHub repository](https://github.com/itconor/SignalScope).*
