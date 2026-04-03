# SignalScope Player

Standalone desktop playback client for SignalScope logger recordings. Browse and play back archived audio without running a full SignalScope instance.

**Requires logger plugin v1.6.0 or later.**

## Features

- Two connection modes: **Hub** (remote via API) or **Direct** (local/SMB recordings folder)
- 24-hour timeline with colour-coded 5-minute segment blocks:
  - 🟢 Green = OK · 🟡 Amber = some silence · 🔴 Red = mostly silent · 🔵 Blue = gap/incomplete · ▪ Dark = no recording
  - Red strip at block base shows silence proportion within the segment
- Silence ranges visualised on the scrub bar as dark red zones — see exactly where silence occurred before scrubbing to it
- **Stereo stream support** — `n_ch` read from logger catalog; stereo streams marked `◈` in the stream list and show a STEREO badge in the player bar. Export preserves `-ac 2` for stereo sources.
- Hub mode uses `/api/mobile/logger/play_file` to relay the original codec (MP3/AAC/Opus) rather than raw PCM — better compatibility and native codec quality
- Day bar overview with playback head and mark in/out indicators
- Metadata overlays: track, show, and mic-live bands
- Mark in/out with clip export (direct mode, requires ffmpeg)
- Auto-advances to next segment on playback completion
- Volume slider in player bar
- Keyboard shortcuts: **Space** play/pause · **← →** seek ±10 s · **↑ ↓** prev/next segment
- Dark theme matching the SignalScope web UI
- Saves connection settings between sessions

## Requirements

- Python 3.10+
- PySide6
- ffmpeg (for export only)

## Install

```
pip install PySide6
```

## Run

```
python signalscope_player.py
```

A connection dialog opens with two tabs:

### Hub Mode

Connect to a SignalScope hub remotely. Enter:

- **Hub URL** — e.g. `https://hub.example.com`
- **API Token** — from SignalScope Settings > Mobile API

Uses the mobile API with Bearer token auth. Streams are loaded from the hub's merged catalog (all sites). Audio is relayed via `play_file` → `relay_stream` (native codec; no PCM transcoding).

### Direct Mode

Open a recordings directory on a local or network drive. Click **Browse** and select the folder containing stream subdirectories (e.g. `S:\storage\logger_recordings` or `/media/storage/logger_recordings`).

The app reads `catalog.json` (written by the logger plugin) to discover streams and their channel count. If no catalog exists, it falls back to listing subdirectories. Silence ranges are loaded from `logger_index.db` when present.

## Package as .exe

```
pip install pyinstaller
pyinstaller --onefile --windowed --name "SignalScopePlayer" signalscope_player.py
```

The executable will be in `dist/SignalScopePlayer.exe`.

## How It Works

```
Hub mode:    App  -->  /api/mobile/logger/catalog    -->  stream list (with n_ch)
                  -->  /api/mobile/logger/days        -->  date list
                  -->  /api/mobile/logger/segments    -->  segment grid + silence ranges
                  -->  /api/mobile/logger/metadata    -->  track/show/mic bands
                  -->  /api/mobile/logger/play_file   -->  relay slot (native codec)
                  -->  /api/mobile/logger/relay_stream/<id>  -->  audio playback

Direct mode: App  -->  catalog.json                  -->  stream list (with n_ch)
                  -->  {root}/{slug}/                -->  date directories
                  -->  {root}/{slug}/{date}/*.mp3    -->  segment files
                  -->  logger_index.db               -->  silence ranges & metadata
```

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| Space | Play / Pause |
| ← | Seek back 10 s |
| → | Seek forward 10 s |
| ↑ | Previous segment |
| ↓ | Next segment |

## Settings

Connection details are saved to `~/.signalscope_player.json` and restored on next launch.

## Changelog

### 1.1.0
- Silence ranges visualised on scrub bar (dark red zones)
- Stereo stream detection via `n_ch` from logger catalog
- Gap segment colour (dark blue) distinct from "no recording"
- Red silence strip at base of each segment block in the grid
- Hub mode audio via `play_file` relay (native codec, replaces raw PCM stream)
- Volume slider
- Keyboard shortcuts: Space, ← →, ↑ ↓
- Silence and gap info in segment tooltips
- `DirectDataSource` passes through `n_ch` from catalog.json
- `silence_ranges` decoded in hub mode (was missing)
- Stereo preserved in ffmpeg export (`-ac 2`)
- Mic band shows `presenter` name when available
- Version badge in connection dialog

### 1.0.0
- Initial release
