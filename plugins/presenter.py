# presenter.py — drop into the plugins/ subdirectory
# Presenter / Producer view — simplified status and fault summary, hub-only

SIGNALSCOPE_PLUGIN = {
    "id":         "presenter",
    "label":      "Presenter",
    "url":        "/presenter",
    "icon":       "🎙",
    "hub_only":   True,
    "user_role":  True,
    "role_label": "Presenter",
    "version":    "1.0.0",
}

import json, os, time
from datetime import datetime

# ─── Alert log helpers ────────────────────────────────────────────────────────

_ALERT_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'alert_log.json')

_SHOW_TYPES = {
    'CHAIN_FAULT', 'CHAIN_RECOVERED', 'CHAIN_FLAPPING',
    'SILENCE', 'STUDIO_FAULT', 'STL_FAULT',
    'TX_DOWN', 'DAB_AUDIO_FAULT', 'RTP_FAULT',
}
_FAULT_TYPES    = _SHOW_TYPES - {'CHAIN_RECOVERED'}
_RECOVERY_TYPES = {'CHAIN_RECOVERED'}


def _read_recent_events(allowed_streams=None, max_age_h=24, limit=40):
    """Read alert_log.json (newline-delimited JSON) and return recent significant events."""
    try:
        cutoff = time.time() - max_age_h * 3600
        results = []
        with open(_ALERT_LOG, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        for line in reversed(lines):
            if len(results) >= limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            etype = ev.get('type', '')
            if etype not in _SHOW_TYPES:
                continue
            # ts is a string "2026-03-30 14:32:05"
            try:
                ev_ts = datetime.strptime(ev['ts'], '%Y-%m-%d %H:%M:%S').timestamp()
            except Exception:
                continue
            if ev_ts < cutoff:
                continue
            if allowed_streams is not None and ev.get('stream', '') not in allowed_streams:
                continue
            ev['_ts_unix'] = ev_ts
            results.append(ev)
        return results
    except Exception:
        return []


def _friendly_time(ts_str):
    try:
        t = datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')
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
        return ts_str or 'Unknown time'


def _plain_english(ev):
    """Return (kind, text) — kind is 'fault', 'recovery', or 'warn'."""
    etype  = ev.get('type', '')
    stream = ev.get('stream', '') or 'Unknown station'
    labels = {
        'CHAIN_FAULT':     ('fault',    f'Signal chain fault — {stream}'),
        'CHAIN_RECOVERED': ('recovery', f'Signal chain recovered — {stream}'),
        'CHAIN_FLAPPING':  ('warn',     f'Unstable signal — {stream}'),
        'SILENCE':         ('fault',    f'{stream} — audio silence'),
        'STUDIO_FAULT':    ('fault',    f'{stream} — studio feed lost'),
        'STL_FAULT':       ('fault',    f'{stream} — STL link fault'),
        'TX_DOWN':         ('fault',    f'{stream} — transmitter fault'),
        'DAB_AUDIO_FAULT': ('fault',    f'{stream} — DAB audio fault'),
        'RTP_FAULT':       ('fault',    f'{stream} — signal lost'),
    }
    return labels.get(etype, ('fault', f'{stream} — issue detected'))


# ─── Template ─────────────────────────────────────────────────────────────────

_PRESENTER_TPL = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Presenter View — SignalScope</title>
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
.event-card{display:flex;align-items:flex-start;gap:14px;padding:14px 18px;border-radius:14px;border:1px solid}
.event-card.fault{background:rgba(245,158,11,.08);border-color:rgba(245,158,11,.3)}
.event-card.recovery{background:rgba(34,197,94,.07);border-color:rgba(34,197,94,.25)}
.event-card.warn{background:rgba(245,158,11,.06);border-color:rgba(245,158,11,.2)}
.event-icon{font-size:20px;line-height:1;flex-shrink:0;margin-top:1px}
.event-body{flex:1;min-width:0}
.event-text{font-size:14px;font-weight:600;line-height:1.3;margin-bottom:3px}
.event-card.fault .event-text{color:#fde68a}
.event-card.recovery .event-text{color:#86efac}
.event-card.warn .event-text{color:#fde68a}
.event-time{font-size:12px;color:var(--mu)}
.loading-events{color:var(--mu);font-size:13px;padding:16px 24px;max-width:1400px;margin:0 auto}

/* ── Station grid ── */
.station-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px;padding:0 24px 28px;max-width:1400px;margin:0 auto}

/* ── Station card ── */
.station-card{background:var(--sur);border:1.5px solid var(--bor);border-radius:18px;padding:20px;transition:border-color .2s,box-shadow .2s,transform .15s;position:relative;overflow:hidden}
.station-card:hover{border-color:rgba(23,168,255,.4);transform:translateY(-2px);box-shadow:0 10px 28px rgba(0,0,0,.4)}
.station-card.status-ok{border-color:rgba(34,197,94,.25)}
.station-card.status-alert{border-color:rgba(245,158,11,.4);background:linear-gradient(160deg,#0d2346,#2a1e08)}
.station-card.status-offline{opacity:.5}

/* ── Avatar ── */
.s-avatar{width:52px;height:52px;border-radius:14px;display:flex;align-items:center;justify-content:center;font-size:20px;font-weight:800;color:#fff;flex-shrink:0;box-shadow:0 4px 12px rgba(0,0,0,.3);transition:transform .2s;text-shadow:0 1px 3px rgba(0,0,0,.3)}
.station-card:hover .s-avatar{transform:scale(1.06) rotate(-2deg)}

/* ── Card content ── */
.s-top{display:flex;align-items:flex-start;gap:13px;margin-bottom:16px}
.s-meta{flex:1;min-width:0}
.s-name{font-size:15px;font-weight:700;line-height:1.25;margin-bottom:3px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;word-break:break-word}
.s-site{font-size:11px;color:var(--mu);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:5px}
.s-onair{font-size:11px;color:var(--acc);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-height:14px;font-style:italic}

/* ── Status badge (large, card-bottom) ── */
.s-status{display:flex;align-items:center;justify-content:center;padding:10px;border-radius:12px;font-size:14px;font-weight:700;gap:7px;letter-spacing:.01em}
.s-status.ok{background:rgba(34,197,94,.12);color:var(--ok);border:1px solid rgba(34,197,94,.3)}
.s-status.alert{background:rgba(245,158,11,.12);color:var(--wn);border:1px solid rgba(245,158,11,.3)}
.s-status.offline{background:rgba(138,164,200,.07);color:var(--mu);border:1px solid rgba(138,164,200,.2)}

/* ── Skeleton ── */
.skeleton{animation:pulse 1.5s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:.4}50%{opacity:.7}}
.skel-ev{height:70px;background:var(--sur);border-radius:14px;margin-bottom:10px}
.skel-card{height:170px;background:var(--sur);border:1.5px solid var(--bor);border-radius:18px}
.skel-row{background:rgba(23,52,95,.55);border-radius:6px;margin-bottom:8px}

/* ── Scrollbar ── */
::-webkit-scrollbar{width:6px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:var(--bor);border-radius:3px}

/* ── Responsive ── */
@media(max-width:640px){
  .station-grid{grid-template-columns:1fr 1fr;gap:10px;padding:0 14px 20px}
  .greeting{padding:18px 16px 6px}
  .section{padding:16px 16px 4px}
  .hdr{padding:12px 16px}
  .greeting-title{font-size:20px}
  .all-clear{margin:0 16px 4px;padding:20px}
  .all-clear-icon{font-size:34px}
  .all-clear-title{font-size:17px}
  .event-list{margin:0 16px}
  .refresh-row{padding:8px 16px 0}
}
@media(max-width:400px){
  .station-grid{grid-template-columns:1fr}
}
</style>
</head>
<body>

<header class="hdr">
  <span class="hdr-logo">🎙</span>
  <div>
    <div class="hdr-title">Presenter View</div>
    <div class="hdr-sub" id="hdr-station-count">Loading…</div>
  </div>
  <div class="hdr-right">
    {% if username %}<div class="hdr-user">👤 {{username}}</div>{% endif %}
    <a href="/logout" class="hdr-signout" onclick="return confirm('Sign out?')">Sign out</a>
  </div>
</header>

<div class="greeting">
  <div class="greeting-title" id="greeting-text">Good day 👋</div>
  <div class="greeting-sub">Here is a live overview of your stations.</div>
</div>

<div class="refresh-row">
  <div class="refresh-dot" id="refresh-dot"></div>
  <span id="refresh-label">Connecting…</span>
</div>

<!-- ── Events section ── -->
<div class="section" style="padding-bottom:0">
  <div class="section-title">Recent Events <span>last 24 hours</span></div>
</div>
<div id="events-wrap">
  <div class="loading-events skeleton" style="height:70px;border-radius:14px;margin:0 24px;max-width:1352px"></div>
</div>

<!-- ── Stations section ── -->
<div class="section" style="margin-top:12px">
  <div class="section-title">Your Stations</div>
</div>
<div id="stations-wrap">
  <div class="station-grid skeleton" id="skel-grid">
    {% for _ in range(6) %}
    <div class="skel-card skeleton">
      <div style="display:flex;gap:12px;padding:20px 20px 0">
        <div class="skel-row" style="width:52px;height:52px;border-radius:14px;flex-shrink:0"></div>
        <div style="flex:1">
          <div class="skel-row" style="height:14px;width:70%"></div>
          <div class="skel-row" style="height:11px;width:45%;margin-top:4px"></div>
        </div>
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
var _statusTimer=null, _faultsTimer=null;
var _lastStatus=null, _lastFaults=null;

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
function _dbPct(db){return isNaN(parseFloat(db))?0:Math.max(0,Math.min(100,(parseFloat(db)+60)/60*100));}
function _setRefresh(live,label){
  var dot=document.getElementById('refresh-dot');
  dot.className='refresh-dot'+(live?' live':'');
  document.getElementById('refresh-label').textContent=label;
}

// ── Station status ────────────────────────────────────────────────────────
function loadStatus(){
  fetch('/hub/data',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      _lastStatus=d;
      renderStations(d.sites||[]);
      clearTimeout(_statusTimer);
      _statusTimer=setTimeout(loadStatus,REFRESH_MS);
    })
    .catch(function(){
      _setRefresh(false,'Could not reach server — retrying…');
      clearTimeout(_statusTimer);
      _statusTimer=setTimeout(loadStatus,10000);
    });
}

function renderStations(sites){
  var streams=[];
  sites.forEach(function(site){
    (site.streams||[]).forEach(function(s){
      if(!s.enabled) return;
      streams.push({
        name:   s.name||'Stream',
        site:   site.site||'',
        status: s.ai_status||'OK',
        online: !!site.online,
        rds:    s.fm_rds_ps||'',
        dab:    s.dab_service||'',
        label:  s.label||'',
      });
    });
  });

  var onair=streams.filter(function(s){return s.online&&s.status==='OK';}).length;
  var issues=streams.filter(function(s){return s.online&&s.status!=='OK';}).length;
  var offline=streams.filter(function(s){return !s.online;}).length;
  var total=streams.length;

  // Header sub-line
  var parts=[];
  if(onair)   parts.push(onair+' on air');
  if(issues)  parts.push(issues+' issue'+(issues>1?'s':''));
  if(offline) parts.push(offline+' offline');
  document.getElementById('hdr-station-count').textContent=parts.join(' · ')||'No stations';

  var now=new Date();
  _setRefresh(true,'Live · Updated at '+_fmtTime(now));

  if(!streams.length){
    document.getElementById('stations-wrap').innerHTML=
      '<div style="text-align:center;padding:40px 24px;color:var(--mu);font-size:14px">No stations available</div>';
    return;
  }

  var html='<div class="station-grid">';
  streams.forEach(function(s){html+=renderCard(s);});
  html+='</div>';
  document.getElementById('stations-wrap').innerHTML=html;
}

function renderCard(s){
  var col=_colorFor(s.name);
  var init=(s.name.match(/[A-Z0-9]/i)||[s.name[0]||'?'])[0].toUpperCase();
  var sub=s.rds||s.dab||s.label||'';
  var cls,badge;
  if(!s.online){
    cls='status-offline'; badge='<div class="s-status offline">○ &nbsp;Not Available</div>';
  } else if(s.status==='ALERT'||s.status==='WARN'){
    cls='status-alert'; badge='<div class="s-status alert">⚠ &nbsp;Signal Issue</div>';
  } else {
    cls='status-ok'; badge='<div class="s-status ok">● &nbsp;On Air</div>';
  }
  return '<div class="station-card '+cls+'">'
    +'<div class="s-top">'
    +'<div class="s-avatar" style="background:linear-gradient(135deg,'+col[0]+','+col[1]+')">'+_esc(init)+'</div>'
    +'<div class="s-meta">'
    +'<div class="s-name">'+_esc(s.name)+'</div>'
    +'<div class="s-site">'+_esc(s.site)+'</div>'
    +'<div class="s-onair">'+_esc(sub)+'</div>'
    +'</div></div>'
    +badge
    +'</div>';
}

// ── Fault events ──────────────────────────────────────────────────────────
function loadFaults(){
  fetch('/api/presenter/faults',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      _lastFaults=d;
      renderEvents(d.events||[]);
      clearTimeout(_faultsTimer);
      _faultsTimer=setTimeout(loadFaults,REFRESH_MS);
    })
    .catch(function(){
      clearTimeout(_faultsTimer);
      _faultsTimer=setTimeout(loadFaults,15000);
    });
}

function renderEvents(events){
  if(!events.length){
    document.getElementById('events-wrap').innerHTML=
      '<div class="all-clear">'
      +'<div class="all-clear-icon">✅</div>'
      +'<div><div class="all-clear-title">Everything is running normally</div>'
      +'<div class="all-clear-sub">No faults or signal issues have been detected in the last 24 hours.<br>If you have any concerns, contact your broadcast engineer.</div>'
      +'</div></div>';
    return;
  }

  var icons={fault:'⚠️',recovery:'✅',warn:'⚡'};
  var html='<div class="event-list">';
  events.forEach(function(ev){
    html+='<div class="event-card '+_esc(ev.kind)+'">'
      +'<div class="event-icon">'+icons[ev.kind||'fault']+'</div>'
      +'<div class="event-body">'
      +'<div class="event-text">'+_esc(ev.text)+'</div>'
      +'<div class="event-time">'+_esc(ev.time)+'</div>'
      +'</div></div>';
  });
  html+='</div>';
  document.getElementById('events-wrap').innerHTML=html;
}

// ── Time helper ───────────────────────────────────────────────────────────
function _fmtTime(d){
  var h=d.getHours()%12||12,m=d.getMinutes(),ap=d.getHours()<12?'AM':'PM';
  return h+':'+(m<10?'0':'')+m+' '+ap;
}

// ── Kick off ──────────────────────────────────────────────────────────────
loadStatus();
loadFaults();

})();
</script>
</body>
</html>
"""

# ─── Plugin registration ───────────────────────────────────────────────────

def register(app, ctx):
    from flask import render_template_string, session, jsonify

    login_required = ctx["login_required"]
    hub_server      = ctx["hub_server"]
    BUILD           = ctx["BUILD"]

    @app.get("/presenter")
    @login_required
    def presenter_page():
        username = session.get("username", "")
        return render_template_string(_PRESENTER_TPL, BUILD=BUILD, username=username)

    @app.get("/api/presenter/faults")
    @login_required
    def presenter_faults():
        allowed_sites = session.get("allowed_sites", [])

        # Build set of allowed stream/chain names from the user's permitted sites
        allowed_streams = None
        if allowed_sites and hub_server:
            allowed_streams = set()
            for site in hub_server.get_sites():
                sname = site.get("site", "")
                if sname not in allowed_sites:
                    continue
                # Stream names
                for s in site.get("streams", []):
                    if s.get("name"):
                        allowed_streams.add(s["name"])
                # Site name itself may appear as chain stream field
                if sname:
                    allowed_streams.add(sname)

        events = _read_recent_events(allowed_streams=allowed_streams)
        result = []
        for ev in events:
            kind, text = _plain_english(ev)
            result.append({
                "kind": kind,
                "text": text,
                "time": _friendly_time(ev.get("ts", "")),
                "type": ev.get("type", ""),
            })
        return jsonify({"events": result, "ok": True})
