# presenter.py — drop into the plugins/ subdirectory
# Producer / Presenter view — simplified chain status and fault summary, hub-only

SIGNALSCOPE_PLUGIN = {
    "id":         "presenter",
    "label":      "Producer View",
    "url":        "/producer",
    "icon":       "🎙",
    "hub_only":   True,
    "user_role":  True,
    "role_label": "Producer",
    "version":    "1.4.0",
}

import json, os, time, urllib.parse
from datetime import datetime

# Module-level references set by register() — avoids passing them into every helper
_metrics_db  = None
_hub_server  = None
_monitor_ref = None

# ─── Plugin config (ticket URL etc.) ─────────────────────────────────────────

_PRESENTER_CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'presenter_config.json')

def _read_cfg():
    try:
        with open(_PRESENTER_CFG) as f:
            return json.load(f)
    except Exception:
        return {}

def _write_cfg(data):
    try:
        with open(_PRESENTER_CFG, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

# ─── Fault log DB helpers ─────────────────────────────────────────────────────
# Reads from the same SQLite fault log that Broadcast Chains uses.
# Clips are already correctly resolved in the DB (back-patched when uploaded).


def _common_prefix(names):
    """Find the longest common prefix of a list of chain names, stripped of trailing separators."""
    if not names:
        return 'Unknown'
    if len(names) == 1:
        return names[0]
    prefix = ''
    for i in range(len(min(names, key=len))):
        ch = names[0][i]
        if all(n[i] == ch for n in names):
            prefix += ch
        else:
            break
    # Strip trailing " - ", " — ", " / ", spaces, dashes
    return prefix.rstrip(' \t-—/') or names[0]


def _fault_clip_url(clips, prefer_recovery=False):
    """Pick the best clip URL from a fault log clips list.

    Fault log clips: [{key, fname, label, node_label, pos, status}, ...]
    URL pattern: /api/chains/clip/{key}/{fname}
    """
    if not clips:
        return None
    # For recovery rows prefer last_good/recovery labels; for fault rows prefer fault
    preferred = ('last_good', 'recovery') if prefer_recovery else ('fault',)
    chosen = None
    for cl in clips:
        key   = cl.get('key',   '')
        fname = cl.get('fname', '')
        if not key or not fname:
            continue
        label = cl.get('label', '') or cl.get('status', '')
        if label in preferred:
            chosen = cl
            break
    if chosen is None:
        # Fall back to any clip with key+fname
        chosen = next((cl for cl in clips if cl.get('key') and cl.get('fname')), None)
    if chosen is None:
        return None
    return (f"/api/chains/clip/{urllib.parse.quote(chosen['key'], safe='')}/"
            f"{urllib.parse.quote(chosen['fname'], safe='')}")


def _station_name(chain_name):
    """Extract the station brand from a chain name.

    Chain naming convention: "[Site/Distribution] / [Station Brand] - [Equipment]"
    e.g. "London - Livewire / Downtown Radio - LONCTAXMQ05"
          → step 1 (strip equipment): "London - Livewire / Downtown Radio"
          → step 2 (extract brand after /): "Downtown Radio"

    e.g. "Northern Ireland DAB / Downtown Radio"
          → step 1 (no equipment suffix): "Northern Ireland DAB / Downtown Radio"
          → step 2 (extract brand after /): "Downtown Radio"

    e.g. "CoolFM - Primary"
          → step 1 (strip equipment): "CoolFM"
          → step 2 (no /): "CoolFM"

    If there's no recognisable equipment suffix and no '/', the full name is returned.
    """
    import re as _re
    # Step 1 — strip equipment suffix (last " - <equipment>" segment)
    _EQUIPMENT_PAT = _re.compile(
        r'^([A-Z0-9]{4,}|.*\b(Processor|Primary|Secondary|Backup|Encoder|'
        r'Codec|Quant|STL|DAB|FM|TX|RX|C[0-9]+|Mux|Multiplexer|Router|'
        r'Switch|Amp|Satellite|MPX|AES|SDI|Livebox|Transmitter|Receiver)\b.*)$',
        _re.IGNORECASE,
    )
    parts = chain_name.rsplit(' - ', 1)
    if len(parts) == 2 and _EQUIPMENT_PAT.match(parts[1].strip()):
        name = parts[0].strip()
    else:
        name = chain_name

    # Step 2 — extract station brand from "[Distribution] / [Brand]"
    if ' / ' in name:
        name = name.rsplit(' / ', 1)[1].strip()

    return name


def _friendly_time_unix(ts: float) -> str:
    try:
        t = datetime.fromtimestamp(ts)
        today = datetime.now().date()
        delta = (today - t.date()).days
        h = t.hour % 12 or 12
        ampm = 'AM' if t.hour < 12 else 'PM'
        tstr = f'{h}:{t.minute:02d} {ampm}'
        if delta == 0:
            return f'Today at {tstr}'
        elif delta == 1:
            return f'Yesterday at {tstr}'
        else:
            return t.strftime('%A') + f' at {tstr}'
    except Exception:
        return ''


def _friendly_time(ts_str):
    try:
        return _friendly_time_unix(datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S').timestamp())
    except Exception:
        return ts_str or ''


def _build_incidents(allowed_chains=None, max_age_h=24):
    """Return chain incidents from the fault log DB, newest-first.

    Reads from the same SQLite source as the Broadcast Chains fault viewer.
    Clips are already correctly resolved in the DB so no alert-log scanning needed.
    """
    chains_cfg = getattr(getattr(_monitor_ref, 'app_cfg', None), 'signal_chains', None) or []
    cutoff     = time.time() - max_age_h * 3600
    result     = []

    for chain in chains_cfg:
        cid   = chain.get('id', '')
        cname = chain.get('name', '')
        if not cid or not cname:
            continue
        if allowed_chains is not None and cname not in allowed_chains:
            continue

        entries = _metrics_db.fault_log_load(cid, limit=15)

        # Overlay in-memory entries for freshness — clips arrive asynchronously
        if _hub_server:
            mem_entries = _hub_server._chain_fault_log.get(cid, [])
            if mem_entries:
                mem_by_id = {e['id']: e for e in mem_entries if e.get('id')}
                for entry in entries:
                    fid = entry.get('id', '')
                    if fid and fid in mem_by_id:
                        mem_clips = mem_by_id[fid].get('clips') or []
                        db_clips  = entry.get('clips') or []
                        entry['clips'] = mem_clips if len(mem_clips) >= len(db_clips) else db_clips

        station = _station_name(cname)

        for entry in entries:
            ts_start     = entry.get('ts_start') or 0
            ts_recovered = entry.get('ts_recovered')
            clips        = entry.get('clips') or []

            if ts_start < cutoff:
                continue

            # Fault row
            result.append({
                '_ts_unix':  ts_start,
                'kind':      'fault',
                'text':      f"Station fault — {station}",
                'time':      _friendly_time_unix(ts_start),
                'type':      'CHAIN_FAULT',
                'clip_url':  _fault_clip_url(clips, prefer_recovery=False),
                'clip_label': '▶ Fault clip',
                'chains':    [],
            })

            # Recovery row (only when recovered)
            if ts_recovered and ts_recovered >= cutoff:
                result.append({
                    '_ts_unix':  ts_recovered,
                    'kind':      'recovery',
                    'text':      f"Station recovered — {station}",
                    'time':      _friendly_time_unix(ts_recovered),
                    'type':      'CHAIN_RECOVERED',
                    'clip_url':  _fault_clip_url(clips, prefer_recovery=True),
                    'clip_label': '▶ Recovery clip',
                    'chains':    [],
                })

    result.sort(key=lambda e: e['_ts_unix'], reverse=True)
    return result[:40]


# ─── Template ─────────────────────────────────────────────────────────────────

_PRODUCER_TPL = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Producer View — SignalScope</title>
<meta name="csrf-token" content="{{csrf_token()}}">
<link rel="icon" type="image/x-icon" href="/static/signalscope_icon.png">
<style nonce="{{csp_nonce()}}">
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(circle at top,#12376f 0%,var(--bg) 38%,#05101f 100%);color:var(--tx);font-family:system-ui,sans-serif;font-size:14px;min-height:100vh}

/* ── Header ── */
.hdr{background:linear-gradient(180deg,rgba(10,31,65,.97),rgba(9,24,48,.97));border-bottom:1px solid var(--bor);padding:14px 24px;display:flex;align-items:center;gap:14px;position:sticky;top:0;z-index:50;backdrop-filter:blur(8px)}
.hdr-logo{font-size:22px}
.hdr-title{font-size:17px;font-weight:700;letter-spacing:-.02em}
.hdr-sub{font-size:11px;color:var(--mu);margin-top:1px}
.hdr-right{margin-left:auto;display:flex;align-items:center;gap:10px}
.hdr-user{font-size:12px;color:var(--mu);background:rgba(23,52,95,.6);padding:5px 12px;border-radius:20px;border:1px solid var(--bor)}
.hdr-signout{font-size:12px;color:var(--mu);background:rgba(23,52,95,.35);padding:5px 12px;border-radius:20px;border:1px solid var(--bor);text-decoration:none;transition:color .2s}
.hdr-signout:hover{color:var(--tx)}
.hdr-powered{font-size:11px;color:var(--mu);opacity:.55;text-decoration:none;letter-spacing:.03em;white-space:nowrap;transition:opacity .2s}
.hdr-powered:hover{opacity:.9}
@media(max-width:700px){.hdr-powered{display:none}}
.hdr-listen{font-size:13px;font-weight:700;color:#fff;background:linear-gradient(135deg,#1a7fe8,#17a8ff);padding:8px 18px;border-radius:20px;text-decoration:none;display:flex;align-items:center;gap:7px;box-shadow:0 2px 12px rgba(23,168,255,.35);transition:filter .2s,box-shadow .2s}
.hdr-listen:hover{filter:brightness(1.1);box-shadow:0 4px 18px rgba(23,168,255,.5)}

/* ── Live status hero ── */
.status-hero{margin:20px 24px 0;max-width:1400px;margin-left:auto;margin-right:auto;border-radius:20px;padding:28px 32px;display:flex;align-items:center;gap:24px;transition:background .4s,border-color .4s}
.status-hero.ok{background:linear-gradient(135deg,rgba(34,197,94,.13),rgba(34,197,94,.07));border:1.5px solid rgba(34,197,94,.4)}
.status-hero.fault{background:linear-gradient(135deg,rgba(245,158,11,.13),rgba(245,158,11,.07));border:1.5px solid rgba(245,158,11,.45)}
.status-hero.loading{background:rgba(23,52,95,.4);border:1.5px solid var(--bor)}
.sh-icon{font-size:52px;line-height:1;flex-shrink:0;transition:transform .3s}
.status-hero.ok    .sh-icon{animation:none}
.status-hero.fault .sh-icon{animation:sh-pulse 2s ease-in-out infinite}
@keyframes sh-pulse{0%,100%{transform:scale(1)}50%{transform:scale(1.08)}}
.sh-body{flex:1;min-width:0}
.sh-title{font-size:22px;font-weight:800;letter-spacing:-.02em;margin-bottom:5px;line-height:1.2}
.status-hero.ok    .sh-title{color:var(--ok)}
.status-hero.fault .sh-title{color:var(--wn)}
.status-hero.loading .sh-title{color:var(--mu)}
.sh-sub{font-size:13px;color:var(--mu);line-height:1.5}
.sh-badge{flex-shrink:0;font-size:13px;font-weight:700;padding:8px 18px;border-radius:12px;text-align:center;min-width:80px}
.status-hero.ok    .sh-badge{background:rgba(34,197,94,.15);color:var(--ok);border:1px solid rgba(34,197,94,.3)}
.status-hero.fault .sh-badge{background:rgba(245,158,11,.15);color:var(--wn);border:1px solid rgba(245,158,11,.3)}
@media(max-width:640px){
  .status-hero{margin:14px 16px 0;padding:20px;gap:16px}
  .sh-icon{font-size:40px}
  .sh-title{font-size:18px}
  .sh-badge{display:none}
}

/* ── Ticket banner ── */
.ticket-banner{margin:16px 24px 0;max-width:1400px;margin-left:auto;margin-right:auto;background:linear-gradient(135deg,rgba(34,197,94,.10),rgba(34,197,94,.05));border:1px solid rgba(34,197,94,.25);border-radius:16px;padding:18px 24px;display:flex;align-items:center;gap:18px}
.ticket-banner-icon{font-size:36px;flex-shrink:0}
.ticket-banner-body{flex:1;min-width:0}
.ticket-banner-title{font-size:15px;font-weight:700;color:var(--tx);margin-bottom:3px}
.ticket-banner-sub{font-size:12px;color:var(--mu)}
.ticket-banner-btn{flex-shrink:0;background:linear-gradient(135deg,#166534,#22c55e);color:#fff;font-weight:700;font-size:14px;padding:11px 22px;border-radius:12px;text-decoration:none;display:flex;align-items:center;gap:8px;box-shadow:0 2px 12px rgba(34,197,94,.3);transition:filter .2s,box-shadow .2s;white-space:nowrap}
.ticket-banner-btn:hover{filter:brightness(1.1);box-shadow:0 4px 18px rgba(34,197,94,.45)}
/* ── Admin config panel ── */
.admin-cfg{margin:24px 24px 0;max-width:1400px;margin-left:auto;margin-right:auto;background:rgba(23,52,95,.35);border:1px solid var(--bor);border-radius:14px;padding:16px 20px}
.admin-cfg-title{font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--mu);margin-bottom:10px}
@media(max-width:640px){
  .ticket-banner{margin:12px 16px 0;padding:14px 16px;flex-wrap:wrap}
  .ticket-banner-btn{width:100%;justify-content:center;margin-top:10px}
  .admin-cfg{margin:16px 16px 0}
}

/* ── Greeting ── */
.greeting{padding:28px 24px 6px;max-width:1400px;margin:0 auto}
.greeting-title{font-size:24px;font-weight:700;letter-spacing:-.02em;margin-bottom:4px}
.greeting-sub{font-size:14px;color:var(--mu)}

/* ── Refresh indicator ── */
.refresh-row{display:flex;align-items:center;gap:8px;padding:10px 24px 0;max-width:1400px;margin:0 auto;font-size:12px;color:var(--mu)}
.refresh-dot{width:7px;height:7px;border-radius:50%;background:var(--mu);flex-shrink:0;transition:background .3s}
.refresh-dot.live{background:var(--ok);animation:dot-pulse 2s ease-in-out infinite}
@keyframes dot-pulse{0%,100%{opacity:.5}50%{opacity:1}}

/* ── Section ── */
.section{padding:20px 24px 4px;max-width:1400px;margin:0 auto}
.section-title{font-size:12px;font-weight:700;color:var(--mu);text-transform:uppercase;letter-spacing:.1em;margin-bottom:14px;display:flex;align-items:center;gap:8px}
.section-title span{font-weight:400;text-transform:none;letter-spacing:0;color:#4a6080}

/* ── All-clear banner ── */
.all-clear{background:linear-gradient(135deg,rgba(34,197,94,.1),rgba(34,197,94,.06));border:1px solid rgba(34,197,94,.3);border-radius:18px;padding:28px 32px;display:flex;align-items:center;gap:22px;margin:0 24px 4px;max-width:1400px;margin-left:auto;margin-right:auto}
.all-clear-icon{font-size:44px;line-height:1;flex-shrink:0}
.all-clear-title{font-size:20px;font-weight:700;color:var(--ok);margin-bottom:4px}
.all-clear-sub{font-size:13px;color:var(--mu);line-height:1.5}

/* ── Event list ── */
.event-list{display:flex;flex-direction:column;gap:10px;margin:0 24px;max-width:1400px;margin-left:auto;margin-right:auto}
.event-card{padding:14px 18px;border-radius:14px;border:1px solid}
.event-card.fault{background:rgba(245,158,11,.08);border-color:rgba(245,158,11,.3)}
.event-card.recovery{background:rgba(34,197,94,.07);border-color:rgba(34,197,94,.25)}
.event-card.warn{background:rgba(245,158,11,.06);border-color:rgba(245,158,11,.2)}

/* ── Event top row ── */
.event-top{display:flex;align-items:flex-start;gap:14px}
.event-icon{font-size:20px;line-height:1;flex-shrink:0;margin-top:1px}
.event-body{flex:1;min-width:0}
.event-text{font-size:14px;font-weight:600;line-height:1.3;margin-bottom:3px}
.event-card.fault .event-text{color:#fde68a}
.event-card.recovery .event-text{color:#86efac}
.event-card.warn .event-text{color:#fde68a}
.event-time{font-size:12px;color:var(--mu)}

/* ── Chain detail expand ── */
.chain-detail-btn{background:none;border:none;color:var(--mu);font-size:11px;cursor:pointer;padding:4px 0 0;text-decoration:underline;font-family:inherit;display:inline-block}
.chain-detail-btn:hover{color:var(--tx)}
.chain-detail{display:none;margin-top:8px;padding:8px 10px;background:rgba(0,0,0,.2);border-radius:8px;border:1px solid rgba(255,255,255,.05)}
.chain-detail.open{display:block}
.chain-detail li{font-size:12px;color:var(--mu);padding:2px 0;list-style:none}
.chain-detail li::before{content:'• ';color:var(--wn)}
.event-card.recovery .chain-detail li::before{color:var(--ok)}

/* ── Clip player ── */
.clip-row{margin-top:10px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.clip-btn{background:rgba(23,168,255,.12);border:1px solid rgba(23,168,255,.3);border-radius:8px;color:var(--acc);font-size:12px;font-weight:600;padding:5px 14px;cursor:pointer;display:flex;align-items:center;gap:6px;font-family:inherit;transition:background .2s,border-color .2s}
.clip-btn:hover{background:rgba(23,168,255,.22)}
.clip-btn.playing{background:rgba(34,197,94,.14);border-color:rgba(34,197,94,.35);color:var(--ok)}
.clip-time{font-size:11px;color:var(--mu)}

/* ── Station grid ── */
.station-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px;padding:0 24px 28px;max-width:1400px;margin:0 auto}

/* ── Station card ── */
.station-card{background:var(--sur);border:1.5px solid var(--bor);border-radius:18px;padding:20px;transition:border-color .2s,box-shadow .2s,transform .15s;position:relative;overflow:hidden}
.station-card:hover{border-color:rgba(23,168,255,.4);transform:translateY(-2px);box-shadow:0 10px 28px rgba(0,0,0,.4)}
.station-card.status-ok{border-color:rgba(34,197,94,.25)}
.station-card.status-alert{border-color:rgba(245,158,11,.4);background:linear-gradient(160deg,#0d2346,#2a1e08)}
.station-card.status-offline{opacity:.5}
.s-avatar{width:52px;height:52px;border-radius:14px;display:flex;align-items:center;justify-content:center;font-size:20px;font-weight:800;color:#fff;flex-shrink:0;box-shadow:0 4px 12px rgba(0,0,0,.3);transition:transform .2s;text-shadow:0 1px 3px rgba(0,0,0,.3)}
.station-card:hover .s-avatar{transform:scale(1.06) rotate(-2deg)}
.s-top{display:flex;align-items:flex-start;gap:13px;margin-bottom:16px}
.s-meta{flex:1;min-width:0}
.s-name{font-size:15px;font-weight:700;line-height:1.25;margin-bottom:3px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;word-break:break-word}
.s-site{font-size:11px;color:var(--mu);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:5px}
.s-onair{font-size:11px;color:var(--acc);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-height:14px;font-style:italic}
.s-status{display:flex;align-items:center;justify-content:center;padding:10px;border-radius:12px;font-size:14px;font-weight:700;gap:7px;letter-spacing:.01em}
.s-status.ok{background:rgba(34,197,94,.12);color:var(--ok);border:1px solid rgba(34,197,94,.3)}
.s-status.alert{background:rgba(245,158,11,.12);color:var(--wn);border:1px solid rgba(245,158,11,.3)}
.s-status.offline{background:rgba(138,164,200,.07);color:var(--mu);border:1px solid rgba(138,164,200,.2)}

/* ── Skeleton ── */
.skeleton{animation:pulse 1.5s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:.4}50%{opacity:.7}}
.skel-card{height:170px;background:var(--sur);border:1.5px solid var(--bor);border-radius:18px}
.skel-row{background:rgba(23,52,95,.55);border-radius:6px;margin-bottom:8px}

::-webkit-scrollbar{width:6px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:var(--bor);border-radius:3px}

@media(max-width:640px){
  .station-grid{grid-template-columns:1fr 1fr;gap:10px;padding:0 14px 20px}
  .greeting,.section{padding-left:16px;padding-right:16px}
  .hdr{padding:12px 16px}
  .greeting-title{font-size:20px}
  .all-clear{margin:0 16px 4px;padding:20px}
  .all-clear-icon{font-size:34px}
  .all-clear-title{font-size:17px}
  .event-list{margin:0 16px}
  .refresh-row{padding:8px 16px 0}
}
@media(max-width:400px){.station-grid{grid-template-columns:1fr}}
</style>
</head>
<body>

<header class="hdr">
  <span class="hdr-logo">🎙</span>
  <div>
    <div class="hdr-title">Producer View</div>
    <div class="hdr-sub" id="hdr-station-count">Loading…</div>
  </div>
  <div style="flex:1"></div>
  <a href="/" class="hdr-powered">Powered by SignalScope</a>
  <div class="hdr-right">
    {% if has_listener %}<a href="/listener" class="hdr-listen">🎧 Listen Live</a>{% endif %}
    {% if username %}<div class="hdr-user">👤 {{username}}</div>{% endif %}
    <a href="/logout" class="hdr-signout" onclick="return confirm('Sign out?')">Sign out</a>
  </div>
</header>

<div class="greeting">
  <div class="greeting-title" id="greeting-text">Good day 👋</div>
  <div class="greeting-sub">Here is a live overview of your stations.</div>
</div>

<div class="status-hero loading" id="status-hero">
  <div class="sh-icon" id="sh-icon">⏳</div>
  <div class="sh-body">
    <div class="sh-title" id="sh-title">Checking station status…</div>
    <div class="sh-sub" id="sh-sub">Connecting to hub, please wait.</div>
  </div>
  <div class="sh-badge" id="sh-badge"></div>
</div>

{% if ticket_url %}
<div class="ticket-banner">
  <div class="ticket-banner-icon">🎫</div>
  <div class="ticket-banner-body">
    <div class="ticket-banner-title">Have a concern or need to report an issue?</div>
    <div class="ticket-banner-sub">Your broadcast engineering team can be reached via the support ticket system.</div>
  </div>
  <a href="{{ticket_url|e}}" target="_blank" rel="noopener" class="ticket-banner-btn">🎫 Open a Ticket</a>
</div>
{% endif %}

{% if is_admin %}
<div class="admin-cfg">
  <div class="admin-cfg-title">⚙ Admin — Ticket System URL</div>
  <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
    <input id="ticket-url-input" type="url" placeholder="https://helpdesk.example.com/new-ticket"
      value="{{ticket_url|e}}"
      style="flex:1;min-width:260px;background:#0d1e40;border:1px solid var(--bor);border-radius:6px;color:var(--tx);padding:7px 10px;font-size:13px">
    <button type="button" id="ticket-save-btn" class="btn bp bs">Save URL</button>
    <span id="ticket-save-status" style="font-size:12px;color:var(--mu)"></span>
  </div>
  <p style="font-size:11px;color:var(--mu);margin:6px 0 0">Leave blank to hide the ticket banner from producers.</p>
</div>
{% endif %}

<div class="refresh-row">
  <div class="refresh-dot" id="refresh-dot"></div>
  <span id="refresh-label">Connecting…</span>
</div>

<div class="section" style="padding-bottom:0">
  <div class="section-title">Station Faults <span>last 24 hours</span></div>
</div>
<div id="events-wrap">
  <div style="height:70px;border-radius:14px;margin:0 24px;max-width:1352px" class="skeleton"></div>
</div>

<div class="section" style="margin-top:12px">
  <div class="section-title">Your Stations</div>
</div>
<div id="stations-wrap">
  <div class="station-grid">
    {% for _ in range(4) %}
    <div class="skel-card skeleton">
      <div style="display:flex;gap:12px;padding:20px 20px 0">
        <div class="skel-row" style="width:52px;height:52px;border-radius:14px;flex-shrink:0"></div>
        <div style="flex:1"><div class="skel-row" style="height:14px;width:70%"></div><div class="skel-row" style="height:11px;width:45%;margin-top:4px"></div></div>
      </div>
      <div class="skel-row" style="height:44px;border-radius:12px;margin:16px 20px 20px"></div>
    </div>
    {% endfor %}
  </div>
</div>

<script nonce="{{csp_nonce()}}">
(function(){
'use strict';

var AVATAR_COLORS=[
  ['#1a7fe8','#17a8ff'],['#16a047','#22c55e'],['#c87f0a','#f59e0b'],
  ['#9333e8','#a855f7'],['#d91a6e','#ec4899'],['#0d9488','#14b8a6'],
  ['#c2440f','#f97316'],['#c81e1e','#ef4444'],
];
var REFRESH_MS=30000;
var _faultsTimer=null,_chainTimer=null;
var _clipAudio=null,_clipBtn=null,_clipTimeEl=null;

// ── Greeting ──────────────────────────────────────────────────────────────
(function(){
  var h=new Date().getHours();
  var g=h<12?'Good morning':h<17?'Good afternoon':'Good evening';
  var u='{{username|e}}';
  document.getElementById('greeting-text').textContent=g+(u?', '+u:'')+' 👋';
})();

// ── Utilities ─────────────────────────────────────────────────────────────
function _esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function _colorFor(name){
  var h=0;for(var i=0;i<name.length;i++)h=(h*31+name.charCodeAt(i))&0x7fffffff;
  return AVATAR_COLORS[h%AVATAR_COLORS.length];
}
function _setRefresh(live,label){
  var dot=document.getElementById('refresh-dot');
  dot.className='refresh-dot'+(live?' live':'');
  document.getElementById('refresh-label').textContent=label;
}
function _fmtTime(d){
  var h=d.getHours()%12||12,m=d.getMinutes(),ap=d.getHours()<12?'AM':'PM';
  return h+':'+(m<10?'0':'')+m+' '+ap;
}

// ── Clip player ───────────────────────────────────────────────────────────
function _stopClip(){
  if(_clipAudio){_clipAudio.pause();_clipAudio.src='';_clipAudio=null;}
  if(_clipBtn){_clipBtn.className='clip-btn';_clipBtn.innerHTML=_clipBtn.dataset.label||'▶ Play clip';_clipBtn=null;}
  if(_clipTimeEl){_clipTimeEl.textContent='';_clipTimeEl=null;}
}
function _playClip(url,btn,timeEl){
  var same=(_clipBtn===btn);
  _stopClip();
  if(same) return;   // toggle off
  _clipAudio=new Audio(url);
  _clipBtn=btn; _clipTimeEl=timeEl;
  btn.className='clip-btn playing'; btn.innerHTML='⏹ Stop';
  _clipAudio.addEventListener('timeupdate',function(){
    if(!_clipTimeEl) return;
    var t=_clipAudio.currentTime,d=_clipAudio.duration||0;
    _clipTimeEl.textContent=d?Math.floor(t)+'s / '+Math.floor(d)+'s':Math.floor(t)+'s';
  });
  _clipAudio.addEventListener('ended',_stopClip);
  _clipAudio.addEventListener('error',function(){
    _stopClip();
    if(timeEl) timeEl.textContent='Could not load clip';
  });
  _clipAudio.play().catch(function(){});
}

// ── Event rendering ───────────────────────────────────────────────────────
document.getElementById('events-wrap').addEventListener('click',function(e){
  // Clip play button
  var cb=e.target.closest('.clip-btn');
  if(cb){
    var url=cb.dataset.url;
    var timeEl=cb.parentNode.querySelector('.clip-time');
    if(url) _playClip(url,cb,timeEl||null);
    return;
  }
  // Chain detail expand/collapse
  var db=e.target.closest('.chain-detail-btn');
  if(db){
    var det=db.parentNode.querySelector('.chain-detail');
    if(det){
      var open=det.classList.toggle('open');
      db.textContent=open?'▴ Hide stations':'▾ Show affected stations';
    }
  }
});

function renderEvents(events){
  var wrap=document.getElementById('events-wrap');
  if(!events.length){
    wrap.innerHTML=
      '<div class="all-clear">'
      +'<div class="all-clear-icon">✅</div>'
      +'<div><div class="all-clear-title">All stations are running normally</div>'
      +'<div class="all-clear-sub">No station faults in the last 24 hours.<br>If you have any concerns, contact your broadcast engineer.</div>'
      +'</div></div>';
    return;
  }
  var icons={fault:'⚠️',recovery:'✅',warn:'⚡'};
  var html='<div class="event-list">';
  events.forEach(function(ev){
    var kind=_esc(ev.kind||'fault');
    var detailHtml='';
    if(ev.chains&&ev.chains.length>1){
      var items=ev.chains.map(function(n){return '<li>'+_esc(n)+'</li>';}).join('');
      detailHtml='<button class="chain-detail-btn">▾ Show affected stations</button>'
        +'<ul class="chain-detail">'+items+'</ul>';
    }
    var clipHtml='';
    if(ev.clip_url){
      var clipLbl=_esc(ev.clip_label||'▶ Play clip');
      clipHtml='<div class="clip-row">'
        +'<button class="clip-btn" data-url="'+_esc(ev.clip_url)+'" data-label="'+clipLbl+'">'+clipLbl+'</button>'
        +'<span class="clip-time"></span>'
        +'</div>';
    }
    html+='<div class="event-card '+kind+'">'
      +'<div class="event-top">'
      +'<div class="event-icon">'+icons[ev.kind||'fault']+'</div>'
      +'<div class="event-body">'
      +'<div class="event-text">'+_esc(ev.text)+'</div>'
      +'<div class="event-time">'+_esc(ev.time)+'</div>'
      +detailHtml
      +clipHtml
      +'</div></div></div>';
  });
  html+='</div>';
  wrap.innerHTML=html;
}

// ── Chain status + station cards ──────────────────────────────────────────
function loadChainStatus(){
  fetch('/api/chains/status',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      var chains=d.results||[];
      updateHero(chains);
      renderChainCards(chains);
      clearTimeout(_chainTimer);
      _chainTimer=setTimeout(loadChainStatus,REFRESH_MS);
    })
    .catch(function(){
      _setRefresh(false,'Could not reach server — retrying…');
      // Replace any skeleton placeholders so the user sees a message instead
      // of indefinitely pulsing white boxes (e.g. immediately after hub restart).
      var sw=document.getElementById('stations-wrap');
      if(sw && sw.querySelector('.skeleton')){
        sw.innerHTML='<div style="text-align:center;padding:40px 24px;color:var(--mu)">Could not reach server — retrying in 15 s…</div>';
      }
      var ew=document.getElementById('events-wrap');
      if(ew && ew.querySelector('.skeleton')){
        ew.innerHTML='<div style="padding:12px 24px;color:var(--mu);font-size:12px">—</div>';
      }
      clearTimeout(_chainTimer);
      _chainTimer=setTimeout(loadChainStatus,15000);
    });
}

function renderChainCards(chains){
  // Update header count
  var ok      = chains.filter(function(c){var ds=c.display_status||c.status;return ds!=='fault';}).length;
  var faulted = chains.filter(function(c){return (c.display_status||c.status)==='fault';}).length;
  var parts=[];
  if(ok)      parts.push(ok+' on air');
  if(faulted) parts.push(faulted+' issue'+(faulted>1?'s':''));
  document.getElementById('hdr-station-count').textContent=parts.join(' · ')||'No chains';
  _setRefresh(true,'Live · Updated at '+_fmtTime(new Date()));

  var wrap=document.getElementById('stations-wrap');
  if(!chains.length){
    wrap.innerHTML='<div style="text-align:center;padding:40px 24px;color:var(--mu)">No chains available</div>';
    return;
  }
  var html='<div class="station-grid">';
  chains.forEach(function(c){
    var name=c.name||'Unknown';
    var ds=c.display_status||c.status||'unknown';
    var col=_colorFor(name);
    var init=(name.match(/[A-Z0-9]/i)||[name[0]||'?'])[0].toUpperCase();
    // Site from the last leaf node (has site field)
    var site='';
    var nodes=c.nodes||[];
    for(var i=nodes.length-1;i>=0;i--){if(nodes[i].site){site=nodes[i].site;break;}}
    var cls,badge;
    if(ds==='fault'){
      cls='status-alert';badge='<div class="s-status alert">⚠ &nbsp;Signal Issue</div>';
    } else if(ds==='ok'||ds==='pending'||ds==='adbreak'){
      cls='status-ok';badge='<div class="s-status ok">● &nbsp;On Air</div>';
    } else {
      cls='status-offline';badge='<div class="s-status offline">○ &nbsp;Unknown</div>';
    }
    html+='<div class="station-card '+cls+'">'
      +'<div class="s-top">'
      +'<div class="s-avatar" style="background:linear-gradient(135deg,'+col[0]+','+col[1]+')">'+_esc(init)+'</div>'
      +'<div class="s-meta"><div class="s-name">'+_esc(name)+'</div>'
      +'<div class="s-site">'+_esc(site)+'</div>'
      +'</div></div>'+badge+'</div>';
  });
  html+='</div>';
  wrap.innerHTML=html;
}

function updateHero(chains){
  var hero  = document.getElementById('status-hero');
  var icon  = document.getElementById('sh-icon');
  var title = document.getElementById('sh-title');
  var sub   = document.getElementById('sh-sub');
  var badge = document.getElementById('sh-badge');
  if(!chains.length){
    hero.className  ='status-hero loading';
    icon.textContent='📡';
    title.textContent='No chains configured';
    sub.textContent  ='No chain data is available. Check your hub connection.';
    badge.textContent='';
    return;
  }
  var faulted=chains.filter(function(c){
    return (c.display_status||c.status)==='fault';
  });
  if(faulted.length===0){
    hero.className  ='status-hero ok';
    icon.textContent='✅';
    title.textContent='All stations are on air';
    sub.textContent  =chains.length+' chain'+(chains.length>1?'s':'')+' running normally. No action needed.';
    badge.textContent='All clear';
  } else if(faulted.length===1){
    hero.className  ='status-hero fault';
    icon.textContent='⚠️';
    title.textContent=faulted[0].name+' has a signal issue';
    sub.textContent  ='Your broadcast engineer has been alerted. Check the fault history below for details.';
    badge.textContent='1 issue';
  } else {
    hero.className  ='status-hero fault';
    icon.textContent='⚠️';
    var names=faulted.slice(0,2).map(function(c){return c.name;}).join(' and ');
    if(faulted.length>2) names=faulted[0].name+' and '+(faulted.length-1)+' other chains';
    title.textContent=names+' have a signal issue';
    sub.textContent  =faulted.length+' chains affected. Your broadcast engineer has been alerted.';
    badge.textContent=faulted.length+' issues';
  }
}

// ── Fault events ──────────────────────────────────────────────────────────
function loadFaults(){
  fetch('/api/producer/faults',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      renderEvents(d.events||[]);
      clearTimeout(_faultsTimer);
      _faultsTimer=setTimeout(loadFaults,REFRESH_MS);
    })
    .catch(function(){
      clearTimeout(_faultsTimer);
      _faultsTimer=setTimeout(loadFaults,15000);
    });
}

// ── Admin ticket URL save ─────────────────────────────────────────────────
(function(){
  var btn=document.getElementById('ticket-save-btn');
  if(!btn) return;
  btn.addEventListener('click',function(){
    var url=(document.getElementById('ticket-url-input').value||'').trim();
    var st=document.getElementById('ticket-save-status');
    st.textContent='Saving…'; st.style.color='var(--mu)';
    fetch('/api/producer/config',{method:'POST',credentials:'same-origin',
      headers:{'Content-Type':'application/json',
               'X-CSRFToken':(document.querySelector('meta[name="csrf-token"]')||{}).content||''},
      body:JSON.stringify({ticket_url:url})})
      .then(function(r){return r.json();})
      .then(function(d){
        if(d.ok){
          st.textContent='Saved ✓'; st.style.color='var(--ok)';
          setTimeout(function(){window.location.reload();},800);
        } else {
          st.textContent='Error: '+(d.error||'?'); st.style.color='var(--al)';
        }
      })
      .catch(function(){st.textContent='Network error'; st.style.color='var(--al)';});
  });
})();

// ── Kick off ──────────────────────────────────────────────────────────────
loadChainStatus();
loadFaults();

})();
</script>
</body>
</html>
"""

# ─── Plugin registration ───────────────────────────────────────────────────

def register(app, ctx):
    global _metrics_db, _hub_server, _monitor_ref
    from flask import render_template_string, session, jsonify, request

    login_required  = ctx["login_required"]
    csrf_protect    = ctx["csrf_protect"]
    hub_server      = ctx["hub_server"]
    BUILD           = ctx["BUILD"]

    _metrics_db  = ctx["metrics_db"]
    _hub_server  = hub_server
    _monitor_ref = ctx["monitor"]

    @app.get("/producer")
    @login_required
    def producer_page():
        username = session.get("username", "")
        is_admin = session.get("role", "") == "admin"
        has_listener = any(
            str(rule) == "/listener"
            for rule in app.url_map.iter_rules()
        )
        ticket_url = _read_cfg().get("ticket_url", "")
        return render_template_string(
            _PRODUCER_TPL, BUILD=BUILD, username=username,
            has_listener=has_listener,
            ticket_url=ticket_url,
            is_admin=is_admin,
        )

    @app.get("/presenter")
    @login_required
    def producer_page_legacy():
        from flask import redirect
        return redirect("/producer", code=301)

    @app.post("/api/producer/config")
    @login_required
    @csrf_protect
    def producer_config_save():
        if session.get("role", "") != "admin":
            return jsonify({"ok": False, "error": "Admin only"}), 403
        data = request.get_json(silent=True) or {}
        cfg  = _read_cfg()
        cfg["ticket_url"] = (data.get("ticket_url") or "").strip()
        _write_cfg(cfg)
        return jsonify({"ok": True})

    @app.get("/api/producer/faults")
    @login_required
    def producer_faults():
        allowed_chains = session.get("allowed_chains", [])
        _filter = set(allowed_chains) if allowed_chains else None

        incidents = _build_incidents(allowed_chains=_filter)
        # Strip internal _ts_unix before sending to client
        result = [{k: v for k, v in inc.items() if k != '_ts_unix'}
                  for inc in incidents]
        return jsonify({"events": result, "ok": True})
