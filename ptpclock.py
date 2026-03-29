# ptpclock.py — PTP Wall Clock plugin for SignalScope
#
# GPS-accurate full-screen clock for broadcast studios.
#
# Routes:
#   /hub/ptpclock              — clock menu (pick mode, set brand/tz)
#   /hub/ptpclock/display      — clock display (params from menu or URL)
#   /api/hub/ptpclock/time     — JSON time endpoint (polled by display)
#   /api/hub/ptpclock/settings — GET/POST plugin settings (brand, tz, ntp, logo)
#   /api/hub/ptpclock/logo     — POST logo upload, GET serve logo
#   /api/ptpclock/mic          — POST mic live state (client-only REST API)
#   /api/hub/ptpclock/mic      — GET mic state from hub (proxied from client)

import os, json, time as _time, threading

SIGNALSCOPE_PLUGIN = {
    "id":    "ptpclock",
    "label": "PTP Clock",
    "url":   "/hub/ptpclock",
    "icon":  "",
}

# ═══════════════════════════════════════════════════════════════════════════════
#  SETTINGS PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════════

_SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ptpclock_settings.json")
_LOGO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ptpclock_logos")
_DEFAULT_SETTINGS = {
    "brand": "",
    "timezone": "",
    "ntp_server": "",
    "logo_filename": "",
    "presets": [],  # [{id, name, brand, timezone, mode, logo_filename}]
}

def _load_settings():
    try:
        with open(_SETTINGS_FILE) as f:
            d = json.load(f)
        return {**_DEFAULT_SETTINGS, **d}
    except Exception:
        return dict(_DEFAULT_SETTINGS)

def _save_settings(s):
    try:
        with open(_SETTINGS_FILE, "w") as f:
            json.dump(s, f, indent=2)
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════════════════════
#  MIC LIVE STATE (client-side only, polled by hub display)
# ═══════════════════════════════════════════════════════════════════════════════

_mic_state = {
    "live": False,
    "since": 0.0,       # time.time() when mic went live
    "last_duration": 0,  # seconds of last mic-live session
    "updated": 0.0,
}
_mic_lock = threading.Lock()

# ═══════════════════════════════════════════════════════════════════════════════
#  NTP OFFSET CHECK (fallback when PTP not available)
# ═══════════════════════════════════════════════════════════════════════════════

_ntp_cache = {"offset_ms": None, "server": "", "ts": 0}

def _check_ntp_offset(server):
    """Quick NTP offset check using ntpdate or sntp."""
    import subprocess
    if not server:
        return None
    now = _time.time()
    if now - _ntp_cache["ts"] < 10 and _ntp_cache["server"] == server:
        return _ntp_cache["offset_ms"]
    for cmd in [
        ["sntp", "-S", server],
        ["ntpdate", "-q", server],
        ["chronyd", "-Q", f"server {server}"],
    ]:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                # Parse offset from output
                import re
                m = re.search(r'([+-]?\d+\.?\d*)\s', r.stdout)
                if m:
                    off = float(m.group(1)) * 1000  # to ms
                    _ntp_cache.update(offset_ms=round(off, 3), server=server, ts=now)
                    return round(off, 3)
        except Exception:
            continue
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  MENU TEMPLATE
# ═══════════════════════════════════════════════════════════════════════════════

MENU_TPL = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>PTP Clock — SignalScope</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<style nonce="{{csp_nonce()}}">
*{margin:0;padding:0;box-sizing:border-box}
html,body{min-height:100%;background:#0a0e1a;color:#e0e6f0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
.wrap{max-width:720px;margin:0 auto;padding:30px 20px}
h1{font-size:24px;font-weight:600;margin-bottom:6px}
.sub{color:#64748b;font-size:14px;margin-bottom:30px}
.card{background:#0f172a;border:1px solid #1e293b;border-radius:8px;padding:20px;margin-bottom:16px}
.card h2{font-size:16px;font-weight:600;margin-bottom:12px;color:#94a3b8}
.modes{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:24px}
.mode-card{background:#0f172a;border:2px solid #1e293b;border-radius:12px;padding:24px 16px;text-align:center;cursor:pointer;transition:all 0.2s}
.mode-card:hover{border-color:#3b82f6;background:#0f1729}
.mode-card .icon{font-size:48px;margin-bottom:8px}
.mode-card .name{font-size:16px;font-weight:600;margin-bottom:4px}
.mode-card .desc{font-size:12px;color:#64748b}
label{display:block;font-size:13px;color:#94a3b8;margin-bottom:4px;margin-top:12px}
input[type=text],select{width:100%;padding:8px 10px;background:#1e293b;border:1px solid #334155;border-radius:4px;color:#f1f5f9;font-size:14px}
input[type=text]:focus,select:focus{outline:none;border-color:#3b82f6}
.row{display:flex;gap:12px}
.row>*{flex:1}
.btn{display:inline-block;padding:8px 20px;background:#3b82f6;color:#fff;border:none;border-radius:4px;font-size:14px;cursor:pointer;text-decoration:none}
.btn:hover{background:#2563eb}
.btn-outline{background:transparent;border:1px solid #334155;color:#94a3b8}
.btn-outline:hover{border-color:#3b82f6;color:#f1f5f9}
.preview{margin-top:16px;text-align:center}
.preview a{color:#3b82f6;font-size:13px}
.logo-area{display:flex;align-items:center;gap:12px;margin-top:8px}
.logo-preview{width:48px;height:48px;border-radius:4px;background:#1e293b;display:flex;align-items:center;justify-content:center;overflow:hidden}
.logo-preview img{max-width:100%;max-height:100%}
.help{font-size:11px;color:#475569;margin-top:4px}
.saved{color:#22c55e;font-size:13px;display:none;margin-left:8px}
</style>
</head>
<body>
<div class="wrap">
<h1>PTP Clock</h1>
<div class="sub">GPS-accurate wall clock for broadcast studios</div>

<div class="modes" id="modes">
  <div class="mode-card" data-mode="digital">
    <div class="icon">&#128337;</div>
    <div class="name">Digital</div>
    <div class="desc">Large HH:MM:SS with tenths, UTC + local time</div>
  </div>
  <div class="mode-card" data-mode="studio">
    <div class="icon">&#128344;</div>
    <div class="name">Studio</div>
    <div class="desc">Analog broadcast clock with sweep second hand</div>
  </div>
</div>

<div class="card">
  <h2>Settings</h2>
  <div class="row">
    <div>
      <label>Station Name / Brand</label>
      <input type="text" id="f-brand" value="{{settings.brand}}" placeholder="e.g. Cool FM">
    </div>
    <div>
      <label>Timezone</label>
      <input type="text" id="f-tz" value="{{settings.timezone}}" placeholder="e.g. Europe/London">
      <div class="help">IANA timezone name. Leave blank for server default.</div>
    </div>
  </div>
  <label>NTP Server (fallback if PTP unavailable)</label>
  <input type="text" id="f-ntp" value="{{settings.ntp_server}}" placeholder="e.g. 192.168.0.113">
  <div class="help">Used to show NTP offset when no PTP grandmaster is detected.</div>

  <label>Station Logo</label>
  <div class="logo-area">
    <div class="logo-preview" id="logo-prev">
      {% if settings.logo_filename %}<img src="/api/hub/ptpclock/logo" id="logo-img">{% else %}<span style="color:#475569;font-size:11px">None</span>{% endif %}
    </div>
    <input type="file" id="f-logo" accept="image/*" style="font-size:12px;color:#94a3b8">
    {% if settings.logo_filename %}<button class="btn btn-outline" style="font-size:11px;padding:4px 10px" id="logo-rm">Remove</button>{% endif %}
  </div>
  <div class="help">PNG or JPG. Displayed on the studio clock face.</div>

  <div style="margin-top:16px">
    <button class="btn" id="save-btn">Save Settings</button>
    <span class="saved" id="save-ok">Saved</span>
  </div>
</div>

<div class="card">
  <h2>Saved Clocks</h2>
  <div class="help" style="margin-bottom:8px">Create multiple branded clocks for different studios or stations. Each gets its own URL for kiosk displays.</div>
  <div id="presets">
    {% for p in settings.presets %}
    <div class="preset-row" style="display:flex;align-items:center;gap:8px;padding:8px 0;border-bottom:1px solid #1e293b">
      <div style="flex:1">
        <div style="font-size:14px;font-weight:600">{{p.name or p.brand or 'Untitled'}}</div>
        <div style="font-size:11px;color:#64748b">{{p.mode or 'digital'}} — {{p.brand or 'No brand'}} — {{p.timezone or 'Server TZ'}}{% if p.stream %} — Level: {{p.stream}}{% endif %}</div>
      </div>
      <a class="btn" style="font-size:11px;padding:4px 10px" href="/hub/ptpclock/display?preset={{p.id}}" target="_blank">Open</a>
      <button class="btn btn-outline" style="font-size:11px;padding:4px 10px" data-delete-preset="{{p.id}}">Delete</button>
    </div>
    {% endfor %}
    {% if not settings.presets %}<div style="color:#475569;font-size:13px;padding:8px 0">No saved clocks yet</div>{% endif %}
  </div>
  <div style="margin-top:12px;border-top:1px solid #1e293b;padding-top:12px">
    <div style="font-size:13px;font-weight:600;margin-bottom:8px">Add New Clock</div>
    <div class="row">
      <div><label style="margin-top:0">Name</label><input type="text" id="np-name" placeholder="e.g. Studio A Clock"></div>
      <div><label style="margin-top:0">Brand</label><input type="text" id="np-brand" placeholder="e.g. Cool FM"></div>
    </div>
    <div class="row">
      <div><label>Timezone</label><input type="text" id="np-tz" placeholder="e.g. Europe/London"></div>
      <div><label>Mode</label><select id="np-mode"><option value="digital">Digital</option><option value="studio">Studio</option></select></div>
    </div>
    <div class="row">
      <div>
        <label>Logo</label>
        <input type="file" id="np-logo" accept="image/*" style="font-size:12px;color:#94a3b8">
        <div class="help">Optional. PNG or JPG for the studio clock face.</div>
      </div>
      <div>
        <label>Audio Level (stream)</label>
        <select id="np-stream"><option value="">None</option>
        {% for s in streams %}<option value="{{s}}">{{s}}</option>{% endfor %}
        </select>
        <div class="help">Show a live level meter on the clock display.</div>
      </div>
    </div>
    <button class="btn" id="add-preset-btn" style="margin-top:10px">Add Clock</button>
    <span class="saved" id="preset-ok">Added</span>
  </div>
</div>

<div class="card">
  <h2>Quick Links</h2>
  <div class="help" style="margin-bottom:8px">Direct URLs using the settings above (no preset):</div>
  <div id="links" style="font-size:13px;color:#64748b;line-height:2"></div>
</div>

</div>
<script nonce="{{csp_nonce()}}">
function _getCsrf(){
  return (document.querySelector('meta[name="csrf-token"]')||{}).content
      ||(document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/)||[])[1]||'';
}
function buildUrl(mode){
  var b=document.getElementById('f-brand').value.trim();
  var t=document.getElementById('f-tz').value.trim();
  var u='/hub/ptpclock/display?mode='+mode;
  if(b)u+='&brand='+encodeURIComponent(b);
  if(t)u+='&tz='+encodeURIComponent(t);
  return u;
}
function updateLinks(){
  var el=document.getElementById('links');
  var base=location.origin;
  el.innerHTML='<div><b>Digital:</b> <a href="'+buildUrl('digital')+'">'+base+buildUrl('digital')+'</a></div>'
    +'<div><b>Studio:</b> <a href="'+buildUrl('studio')+'">'+base+buildUrl('studio')+'</a></div>';
}
updateLinks();
document.getElementById('f-brand').addEventListener('input',updateLinks);
document.getElementById('f-tz').addEventListener('input',updateLinks);

// Mode cards launch display
document.getElementById('modes').addEventListener('click',function(e){
  var card=e.target.closest('.mode-card');
  if(card)window.open(buildUrl(card.dataset.mode),'_blank');
});

// Save settings
document.getElementById('save-btn').addEventListener('click',function(){
  var body={
    brand:document.getElementById('f-brand').value.trim(),
    timezone:document.getElementById('f-tz').value.trim(),
    ntp_server:document.getElementById('f-ntp').value.trim()
  };
  fetch('/api/hub/ptpclock/settings',{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':_getCsrf()},body:JSON.stringify(body),credentials:'same-origin'})
  .then(function(r){return r.json()}).then(function(d){
    if(d.ok){var s=document.getElementById('save-ok');s.style.display='inline';setTimeout(function(){s.style.display='none'},2000)}
  });
});

// Logo upload
document.getElementById('f-logo').addEventListener('change',function(){
  var file=this.files[0];if(!file)return;
  var fd=new FormData();fd.append('logo',file);
  fetch('/api/hub/ptpclock/logo',{method:'POST',headers:{'X-CSRFToken':_getCsrf()},body:fd,credentials:'same-origin'})
  .then(function(r){return r.json()}).then(function(d){
    if(d.ok){location.reload()}
  });
});

// Logo remove
var rmBtn=document.getElementById('logo-rm');
if(rmBtn)rmBtn.addEventListener('click',function(){
  fetch('/api/hub/ptpclock/logo',{method:'DELETE',headers:{'X-CSRFToken':_getCsrf()},credentials:'same-origin'})
  .then(function(r){return r.json()}).then(function(d){if(d.ok)location.reload()});
});

// ── Presets ───────────────────────────────────────────────────────
document.getElementById('add-preset-btn').addEventListener('click',function(){
  var name=document.getElementById('np-name').value.trim();
  var brand=document.getElementById('np-brand').value.trim();
  var tz=document.getElementById('np-tz').value.trim();
  var mode=document.getElementById('np-mode').value;
  var stream=document.getElementById('np-stream').value;
  var logoFile=document.getElementById('np-logo').files[0];

  function doCreate(logoFilename){
    fetch('/api/hub/ptpclock/presets',{method:'POST',
      headers:{'Content-Type':'application/json','X-CSRFToken':_getCsrf()},
      body:JSON.stringify({name:name,brand:brand,timezone:tz,mode:mode,stream:stream,logo_filename:logoFilename||''}),
      credentials:'same-origin'
    }).then(function(r){return r.json()}).then(function(d){
      if(d.ok)location.reload();
    });
  }

  if(logoFile){
    var fd=new FormData();fd.append('logo',logoFile);
    fetch('/api/hub/ptpclock/logo/upload',{method:'POST',headers:{'X-CSRFToken':_getCsrf()},body:fd,credentials:'same-origin'})
    .then(function(r){return r.json()}).then(function(d){doCreate(d.filename||'')});
  }else{doCreate('')}
});

document.getElementById('presets').addEventListener('click',function(e){
  var del=e.target.closest('[data-delete-preset]');
  if(del){
    fetch('/api/hub/ptpclock/presets/'+del.dataset.deletePreset,{method:'DELETE',
      headers:{'X-CSRFToken':_getCsrf()},credentials:'same-origin'})
    .then(function(r){return r.json()}).then(function(d){if(d.ok)location.reload()});
  }
});
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════════════════════
#  DISPLAY TEMPLATE
# ═══════════════════════════════════════════════════════════════════════════════

DISPLAY_TPL = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{{brand or 'PTP Clock'}} — SignalScope</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<style nonce="{{csp_nonce()}}">
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%;overflow:hidden;background:#0a0e1a;color:#e0e6f0;font-family:'SF Mono','Consolas','Menlo',monospace}
body{display:flex;flex-direction:column;align-items:center;justify-content:center;user-select:none}

/* ── Digital Mode ──────────────────────────────────────────────── */
.digital{display:none;flex-direction:column;align-items:center;justify-content:center;width:100%;height:100%}
.d-logo{max-height:6vh;max-width:30vw;margin-bottom:1vh;object-fit:contain}
.d-brand{font-size:clamp(14px,2.5vw,28px);color:#64748b;letter-spacing:0.15em;text-transform:uppercase;margin-bottom:1vh}
.d-row{display:flex;gap:clamp(20px,6vw,80px);align-items:flex-start;justify-content:center}
.d-col{text-align:center}
.d-label{font-size:clamp(12px,1.8vw,22px);color:#64748b;letter-spacing:0.2em;text-transform:uppercase;margin-bottom:0.5vh}
.d-time{font-size:clamp(48px,12vw,160px);font-weight:200;letter-spacing:0.02em;line-height:1;color:#f1f5f9}
.d-tenths{font-size:clamp(24px,5vw,64px);font-weight:200;color:#475569;vertical-align:super;margin-left:2px}
.d-date{font-size:clamp(14px,2.2vw,26px);color:#64748b;margin-top:2vh;letter-spacing:0.1em}

/* ── Studio Mode ───────────────────────────────────────────────── */
.studio{display:none;flex-direction:column;align-items:center;justify-content:center;width:100%;height:100%}
.s-brand{font-size:clamp(14px,2.5vw,28px);color:#94a3b8;letter-spacing:0.15em;text-transform:uppercase;margin-bottom:1vh}
.s-canvas-wrap{position:relative;width:min(65vh,65vw);height:min(65vh,65vw)}
.s-canvas-wrap canvas{width:100%;height:100%}
.s-utc{font-size:clamp(18px,3vw,36px);color:#f1f5f9;margin-top:1.5vh;letter-spacing:0.05em}
.s-date{font-size:clamp(12px,1.8vw,20px);color:#64748b;margin-top:0.5vh;letter-spacing:0.1em}

/* ── Mic Live ──────────────────────────────────────────────────── */
.mic-bar{display:none;position:fixed;top:0;left:0;right:0;padding:10px 20px;text-align:center;font-size:clamp(16px,3vw,32px);font-weight:700;letter-spacing:0.15em;text-transform:uppercase;z-index:100;transition:all 0.3s}
.mic-bar.live{display:block;background:#dc2626;color:#fff;animation:mic-pulse 1.5s ease-in-out infinite}
.mic-bar.off{display:block;background:#1e293b;color:#64748b;font-weight:400;font-size:clamp(12px,2vw,20px)}
@keyframes mic-pulse{0%,100%{opacity:1}50%{opacity:0.7}}
.mic-timer{font-size:clamp(12px,1.5vw,18px);font-weight:400;margin-left:12px;opacity:0.8}

/* ── Level Meter ───────────────────────────────────────────────── */
.level-wrap{position:fixed;left:16px;bottom:40px;top:60px;width:32px;display:none;flex-direction:column;align-items:center}
.level-wrap.active{display:flex}
.level-track{flex:1;width:16px;background:#1e293b;border-radius:8px;position:relative;overflow:hidden}
.level-fill{position:absolute;bottom:0;left:0;right:0;background:linear-gradient(to top,#22c55e 0%,#22c55e 60%,#f59e0b 80%,#ef4444 100%);border-radius:8px;transition:height 0.15s}
.level-peak{position:absolute;left:0;right:0;height:2px;background:#f1f5f9;transition:bottom 0.05s}
.level-val{font-size:10px;color:#64748b;margin-top:4px;font-family:monospace}
.level-name{font-size:9px;color:#475569;writing-mode:vertical-rl;text-orientation:mixed;margin-top:4px;max-height:80px;overflow:hidden}

/* ── Status Bar ────────────────────────────────────────────────── */
.status-bar{position:fixed;bottom:0;left:0;right:0;display:flex;align-items:center;justify-content:center;gap:clamp(10px,3vw,40px);padding:8px 16px;background:rgba(10,14,26,0.85);border-top:1px solid #1e293b;font-size:clamp(10px,1.4vw,14px);color:#64748b}
.ptp-dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:4px}
.ptp-dot.ok{background:#22c55e}.ptp-dot.warn{background:#f59e0b}.ptp-dot.alert,.ptp-dot.lost{background:#ef4444}.ptp-dot.idle{background:#475569}
.ptp-val{color:#94a3b8}

/* ── Mode switcher ─────────────────────────────────────────────── */
.mode-sw{position:fixed;top:12px;right:12px;display:flex;gap:6px;opacity:0.15;transition:opacity 0.3s;z-index:101}
.mode-sw:hover{opacity:1}
.mode-sw button{background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:4px;padding:4px 10px;font-size:11px;cursor:pointer}
.mode-sw button.active{background:#334155;color:#f1f5f9}
</style>
</head>
<body>

<!-- Mic Live Bar -->
<div class="mic-bar" id="mic-bar"></div>

<!-- Digital Clock -->
<div class="digital" id="v-digital">
  {% if has_logo %}<img class="d-logo" src="/api/hub/ptpclock/logo" alt="">{% endif %}
  <div class="d-brand" id="d-brand"></div>
  <div class="d-row">
    <div class="d-col">
      <div class="d-label">UTC</div>
      <div class="d-time" id="d-utc">--:--:--<span class="d-tenths">.--</span></div>
    </div>
    <div class="d-col">
      <div class="d-label" id="d-tz-label">LOCAL</div>
      <div class="d-time" id="d-local">--:--:--<span class="d-tenths">.--</span></div>
    </div>
  </div>
  <div class="d-date" id="d-date">---</div>
</div>

<!-- Studio Clock -->
<div class="studio" id="v-studio">
  <div class="s-brand" id="s-brand"></div>
  <div class="s-canvas-wrap">
    <canvas id="s-canvas" width="800" height="800"></canvas>
  </div>
  <div class="s-utc" id="s-utc">--:--:--</div>
  <div class="s-date" id="s-date">---</div>
</div>

<!-- Level Meter -->
<div class="level-wrap" id="level-wrap">
  <div class="level-track">
    <div class="level-fill" id="level-fill" style="height:0%"></div>
    <div class="level-peak" id="level-peak" style="bottom:0%"></div>
  </div>
  <div class="level-val" id="level-val">---</div>
  <div class="level-name" id="level-name"></div>
</div>

<!-- Status Bar -->
<div class="status-bar">
  <span><span class="ptp-dot idle" id="ptp-dot"></span> <span id="sync-label">PTP</span> <span class="ptp-val" id="ptp-state">---</span></span>
  <span>Offset <span class="ptp-val" id="ptp-off">---</span></span>
  <span>Jitter <span class="ptp-val" id="ptp-jit">---</span></span>
  <span>GM <span class="ptp-val" id="ptp-gm">---</span></span>
</div>

<!-- Mode Switcher -->
<div class="mode-sw">
  <button id="btn-digital" data-mode="digital">Digital</button>
  <button id="btn-studio" data-mode="studio">Studio</button>
  <button data-mode="menu" style="font-size:10px">Menu</button>
</div>

<script nonce="{{csp_nonce()}}">
var _mode='digital',_brand='{{brand|e}}',_tz='{{tz|e}}';
var _hasLogo={{has_logo|tojson}};
var _presetId='{{preset_id|e}}';
var _stream='{{stream|e}}';
var _levelDb=-120,_peakDb=-120,_peakDecay=0;
var _utcH=0,_utcM=0,_utcS=0,_utcMs=0;
var _locH=0,_locM=0,_locS=0,_locMs=0;
var _dateStr='',_tzLabel='LOCAL';
var _ptpState='idle',_ptpOff=0,_ptpJit=0,_ptpGm='';
var _ntpOff=null,_syncType='PTP';
var _serverT=0,_clientT=0;
var _micLive=false,_micSince=0,_micLastDur=0;
var _logoImg=null;

// ── Mode switching ────────────────────────────────────────────────
function setMode(m){
  if(m==='menu'){window.location='/hub/ptpclock';return}
  _mode=m;
  document.getElementById('v-digital').style.display=m==='digital'?'flex':'none';
  document.getElementById('v-studio').style.display=m==='studio'?'flex':'none';
  document.querySelectorAll('.mode-sw button').forEach(function(b){b.className=b.dataset.mode===m?'active':''});
  if(m==='studio')resizeCanvas();
  var u=new URL(location);u.searchParams.set('mode',m);history.replaceState(null,'',u);
}
document.querySelector('.mode-sw').addEventListener('click',function(e){
  var btn=e.target.closest('button');
  if(btn&&btn.dataset.mode)setMode(btn.dataset.mode);
});

// ── Branding ──────────────────────────────────────────────────────
if(_brand){
  document.getElementById('d-brand').textContent=_brand;
  document.getElementById('s-brand').textContent=_brand;
  document.title=_brand+' — PTP Clock';
}

// ── Timezone label ────────────────────────────────────────────────
if(_tz){
  try{
    var _test=new Date().toLocaleString('en-GB',{timeZone:_tz,timeZoneName:'short'});
    _tzLabel=_test.split(' ').pop()||_tz;
  }catch(e){_tzLabel=_tz;}
  document.getElementById('d-tz-label').textContent=_tzLabel;
}

// ── Preload logo for studio canvas ────────────────────────────────
if(_hasLogo){
  _logoImg=new Image();
  _logoImg.src='/api/hub/ptpclock/logo'+(_presetId?'?preset='+_presetId:'');
}

// ── Level meter ───────────────────────────────────────────────────
if(_stream){
  document.getElementById('level-wrap').className='level-wrap active';
  document.getElementById('level-name').textContent=_stream;
  setInterval(function(){
    fetch('/api/hub/ptpclock/level?stream='+encodeURIComponent(_stream),{credentials:'same-origin'})
    .then(function(r){return r.json()}).then(function(d){
      if(d.level_dbfs!==null){
        _levelDb=d.level_dbfs;
        if(d.peak_dbfs!==null&&d.peak_dbfs>_peakDb)_peakDb=d.peak_dbfs;
        var pct=Math.max(0,Math.min(100,((_levelDb+60)/60)*100));
        var ppct=Math.max(0,Math.min(100,((_peakDb+60)/60)*100));
        document.getElementById('level-fill').style.height=pct+'%';
        document.getElementById('level-peak').style.bottom=ppct+'%';
        document.getElementById('level-val').textContent=_levelDb.toFixed(1);
        _peakDb-=0.5;if(_peakDb<_levelDb)_peakDb=_levelDb;
      }
    }).catch(function(){});
  },200);
}

// ── Poll server time ──────────────────────────────────────────────
function poll(){
  fetch('/api/hub/ptpclock/time',{credentials:'same-origin'}).then(function(r){return r.json()}).then(function(d){
    _serverT=d.unix;_clientT=performance.now()/1000;
    _dateStr=d.date;
    _tzLabel=d.tz_label||_tzLabel;
    document.getElementById('d-tz-label').textContent=_tzLabel;

    // PTP or NTP status
    if(d.ptp&&d.ptp.state!=='idle'){
      _syncType='PTP';_ptpState=d.ptp.state||'idle';
      _ptpOff=d.ptp.offset_us||0;_ptpJit=d.ptp.jitter_us||0;_ptpGm=d.ptp.gm_id||'';
    }else if(d.ntp){
      _syncType='NTP';_ptpState=d.ntp.state||'ok';
      _ptpOff=d.ntp.offset_ms?d.ntp.offset_ms*1000:0;_ptpJit=0;_ptpGm=d.ntp.server||'';
    }else{
      _syncType='PTP';_ptpState=d.ptp?d.ptp.state:'idle';
      _ptpOff=0;_ptpJit=0;_ptpGm='';
    }

    // Mic state
    if(d.mic!==undefined&&d.mic!==null){
      _micLive=d.mic.live;_micSince=d.mic.since||0;_micLastDur=d.mic.last_duration||0;
    }

    // Update status bar
    document.getElementById('sync-label').textContent=_syncType;
    var dot=document.getElementById('ptp-dot');
    dot.className='ptp-dot '+_ptpState;
    document.getElementById('ptp-state').textContent=_ptpState.toUpperCase();
    document.getElementById('ptp-off').textContent=(_ptpOff/1000).toFixed(3)+' ms';
    document.getElementById('ptp-jit').textContent=_syncType==='PTP'?(_ptpJit/1000).toFixed(3)+' ms':'---';
    document.getElementById('ptp-gm').textContent=_ptpGm?(_syncType==='NTP'?_ptpGm:_ptpGm.substring(0,16)):'---';
  }).catch(function(){});
}
poll();
setInterval(poll,200);

// ── Mic bar update ────────────────────────────────────────────────
function updateMic(){
  var bar=document.getElementById('mic-bar');
  if(_micLive){
    var elapsed=Math.floor((_serverT+(performance.now()/1000-_clientT))-_micSince);
    if(elapsed<0)elapsed=0;
    var mm=Math.floor(elapsed/60),ss=elapsed%60;
    bar.className='mic-bar live';
    bar.innerHTML='MIC LIVE<span class="mic-timer">'+mm+':'+(ss<10?'0':'')+ss+'</span>';
  }else if(_micLastDur>0){
    var mm2=Math.floor(_micLastDur/60),ss2=_micLastDur%60;
    bar.className='mic-bar off';
    bar.textContent='Last live: '+mm2+':'+(ss2<10?'0':'')+ss2;
  }else{
    bar.className='mic-bar';bar.textContent='';
  }
}
setInterval(updateMic,500);

// ── Render loop ───────────────────────────────────────────────────
function render(){
  var elapsed=performance.now()/1000-_clientT;
  var now=_serverT+elapsed;
  if(_serverT===0){requestAnimationFrame(render);return}
  var utcDate=new Date(now*1000);
  _utcH=utcDate.getUTCHours();_utcM=utcDate.getUTCMinutes();
  _utcS=utcDate.getUTCSeconds();_utcMs=utcDate.getUTCMilliseconds();
  var locStr=null;
  if(_tz){try{locStr=utcDate.toLocaleTimeString('en-GB',{timeZone:_tz,hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'})}catch(e){}}
  if(locStr){var pp=locStr.split(':');_locH=parseInt(pp[0]);_locM=parseInt(pp[1]);_locS=parseInt(pp[2]);_locMs=_utcMs}
  else{_locH=utcDate.getHours();_locM=utcDate.getMinutes();_locS=utcDate.getSeconds();_locMs=utcDate.getMilliseconds()}
  if(_mode==='digital')renderDigital();else renderStudio();
  document.getElementById((_mode==='studio'?'s':'d')+'-date').textContent=_dateStr;
  requestAnimationFrame(render);
}
function pad2(n){return n<10?'0'+n:''+n}
function renderDigital(){
  document.getElementById('d-utc').innerHTML=pad2(_utcH)+':'+pad2(_utcM)+':'+pad2(_utcS)+'<span class="d-tenths">.'+Math.floor(_utcMs/100)+'</span>';
  document.getElementById('d-local').innerHTML=pad2(_locH)+':'+pad2(_locM)+':'+pad2(_locS)+'<span class="d-tenths">.'+Math.floor(_locMs/100)+'</span>';
}

// ── Studio analog clock ───────────────────────────────────────────
var _cvs,_ctx2d,_cw;
function resizeCanvas(){
  _cvs=document.getElementById('s-canvas');
  var wrap=_cvs.parentElement;var sz=Math.min(wrap.clientWidth,wrap.clientHeight);
  _cvs.width=sz*2;_cvs.height=sz*2;_cvs.style.width=sz+'px';_cvs.style.height=sz+'px';
  _ctx2d=_cvs.getContext('2d');_cw=sz*2;
}
window.addEventListener('resize',function(){if(_mode==='studio')resizeCanvas()});

function renderStudio(){
  if(!_ctx2d)resizeCanvas();
  var c=_ctx2d,w=_cw,r=w/2;
  c.clearRect(0,0,w,w);c.save();c.translate(r,r);

  // Face
  c.beginPath();c.arc(0,0,r*0.95,0,Math.PI*2);c.fillStyle='#0f172a';c.fill();
  c.strokeStyle='#334155';c.lineWidth=r*0.01;c.stroke();

  // Hour markers
  for(var i=0;i<12;i++){
    var a=i*Math.PI/6-Math.PI/2;var isQ=i%3===0;
    var len=isQ?r*0.12:r*0.06;var thick=isQ?r*0.025:r*0.012;
    var x1=Math.cos(a)*(r*0.82),y1=Math.sin(a)*(r*0.82);
    var x2=Math.cos(a)*(r*0.82+len),y2=Math.sin(a)*(r*0.82+len);
    c.beginPath();c.moveTo(x1,y1);c.lineTo(x2,y2);
    c.strokeStyle=isQ?'#f1f5f9':'#94a3b8';c.lineWidth=thick;c.lineCap='round';c.stroke();
  }
  // Minute ticks
  for(var i=0;i<60;i++){if(i%5===0)continue;
    var a=i*Math.PI/30-Math.PI/2;
    c.beginPath();c.moveTo(Math.cos(a)*(r*0.88),Math.sin(a)*(r*0.88));
    c.lineTo(Math.cos(a)*(r*0.91),Math.sin(a)*(r*0.91));
    c.strokeStyle='#475569';c.lineWidth=r*0.005;c.stroke();
  }

  // Logo on face
  if(_logoImg&&_logoImg.complete&&_logoImg.naturalWidth>0){
    var lsz=r*0.25;var aspect=_logoImg.naturalWidth/_logoImg.naturalHeight;
    var lw,lh;if(aspect>1){lw=lsz;lh=lsz/aspect}else{lh=lsz;lw=lsz*aspect}
    c.drawImage(_logoImg,-lw/2,-r*0.35-lh/2,lw,lh);
  }else if(_brand){
    c.font='600 '+Math.round(r*0.08)+'px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';
    c.fillStyle='#64748b';c.textAlign='center';c.textBaseline='middle';
    c.fillText(_brand,0,-r*0.3);
  }

  // "PTP" / "NTP" label
  c.font='600 '+Math.round(r*0.05)+'px monospace';
  c.fillStyle='#334155';c.textAlign='center';c.textBaseline='middle';
  c.fillText(_syncType,0,r*0.35);

  // Hands
  var sec=_utcS+_utcMs/1000,min=_utcM+sec/60,hr=(_utcH%12)+min/60;
  // Hour
  var ha=hr*Math.PI/6-Math.PI/2;
  c.beginPath();c.moveTo(0,0);c.lineTo(Math.cos(ha)*r*0.5,Math.sin(ha)*r*0.5);
  c.strokeStyle='#f1f5f9';c.lineWidth=r*0.04;c.lineCap='round';c.stroke();
  // Minute
  var ma=min*Math.PI/30-Math.PI/2;
  c.beginPath();c.moveTo(0,0);c.lineTo(Math.cos(ma)*r*0.7,Math.sin(ma)*r*0.7);
  c.strokeStyle='#e2e8f0';c.lineWidth=r*0.025;c.lineCap='round';c.stroke();
  // Second (sweep)
  var sa=sec*Math.PI/30-Math.PI/2;
  c.beginPath();c.moveTo(Math.cos(sa+Math.PI)*r*0.1,Math.sin(sa+Math.PI)*r*0.1);
  c.lineTo(Math.cos(sa)*r*0.82,Math.sin(sa)*r*0.82);
  c.strokeStyle='#ef4444';c.lineWidth=r*0.01;c.lineCap='round';c.stroke();
  // Center dot
  c.beginPath();c.arc(0,0,r*0.025,0,Math.PI*2);c.fillStyle='#ef4444';c.fill();

  c.restore();
  document.getElementById('s-utc').textContent=pad2(_utcH)+':'+pad2(_utcM)+':'+pad2(_utcS)+'.'+Math.floor(_utcMs/100);
}

// ── Init ──────────────────────────────────────────────────────────
var urlMode=new URLSearchParams(location.search).get('mode')||'{{initial_mode|e}}'||'digital';
setMode(urlMode);
requestAnimationFrame(render);
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════════════════════
#  REGISTER
# ═══════════════════════════════════════════════════════════════════════════════

def register(app, ctx):
    from flask import request, jsonify, render_template_string, Response

    monitor        = ctx["monitor"]
    login_required = ctx["login_required"]
    csrf_protect   = ctx["csrf_protect"]
    BUILD          = ctx["BUILD"]

    # ── Menu page ─────────────────────────────────────────────────────
    @app.get("/hub/ptpclock")
    @login_required
    def ptpclock_menu():
        settings = _load_settings()
        # Gather available stream names for the level meter dropdown
        streams = []
        cfg = monitor.app_cfg
        for inp in cfg.inputs:
            if inp.enabled:
                streams.append(inp.name)
        hub = ctx.get("hub_server")
        if hub:
            with hub._lock:
                for sname, sdata in hub._sites.items():
                    for sd in sdata.get("streams", []):
                        n = sd.get("name", "")
                        if n and n not in streams:
                            streams.append(n)
        streams.sort()
        return render_template_string(MENU_TPL, settings=settings, build=BUILD, streams=streams)

    # ── Display page ──────────────────────────────────────────────────
    @app.get("/hub/ptpclock/display")
    @login_required
    def ptpclock_display():
        settings = _load_settings()
        preset_id = request.args.get("preset", "")
        preset = None
        if preset_id:
            preset = next((p for p in settings.get("presets", []) if p.get("id") == preset_id), None)
        if preset:
            brand = preset.get("brand", "")
            tz    = preset.get("timezone", "")
            has_logo = bool(preset.get("logo_filename"))
            mode  = preset.get("mode", "digital")
            stream = preset.get("stream", "")
        else:
            brand = request.args.get("brand", settings.get("brand", ""))
            tz    = request.args.get("tz", settings.get("timezone", ""))
            has_logo = bool(settings.get("logo_filename"))
            mode  = request.args.get("mode", "digital")
            stream = request.args.get("stream", "")
        return render_template_string(DISPLAY_TPL, brand=brand, tz=tz,
                                       has_logo=has_logo, build=BUILD,
                                       initial_mode=mode, stream=stream,
                                       preset_id=preset_id)

    # ── Time API ──────────────────────────────────────────────────────
    @app.get("/api/hub/ptpclock/time")
    @login_required
    def ptpclock_time():
        now = _time.time()
        utc = _time.gmtime(now)
        ms  = int((now % 1) * 1000)
        loc = _time.localtime(now)
        tz_name = _time.strftime("%Z", loc) or "LOCAL"
        date_str = _time.strftime("%A %d %B %Y", utc)

        # PTP data
        ptp_data = None
        ptp = getattr(monitor, "ptp", None)
        if ptp:
            last_sync_ago = now - ptp.last_sync if ptp.last_sync > 0 else -1
            ptp_data = {
                "state":         ptp.state,
                "offset_us":     round(ptp.offset_us, 1),
                "drift_us":      round(ptp.drift_us, 1),
                "jitter_us":     round(ptp.jitter_us, 1),
                "gm_id":         ptp.gm_id,
                "domain":        ptp.domain,
                "last_sync_ago": round(last_sync_ago, 1),
            }

        # NTP fallback
        ntp_data = None
        settings = _load_settings()
        ntp_srv = settings.get("ntp_server", "")
        if ntp_srv and (not ptp or ptp.state == "idle"):
            off = _check_ntp_offset(ntp_srv)
            if off is not None:
                ntp_data = {"state": "ok", "offset_ms": off, "server": ntp_srv}

        # Mic state
        with _mic_lock:
            mic_data = None
            if _mic_state["updated"] > 0:
                mic_data = {
                    "live": _mic_state["live"],
                    "since": _mic_state["since"],
                    "last_duration": _mic_state["last_duration"],
                }

        return jsonify({
            "utc":      _time.strftime("%H:%M:%S", utc) + f".{ms:03d}",
            "local":    _time.strftime("%H:%M:%S", loc) + f".{ms:03d}",
            "date":     date_str,
            "unix":     round(now, 3),
            "tz_label": tz_name,
            "ptp":      ptp_data,
            "ntp":      ntp_data,
            "mic":      mic_data,
        })

    # ── Settings API ──────────────────────────────────────────────────
    @app.get("/api/hub/ptpclock/settings")
    @login_required
    def ptpclock_settings_get():
        return jsonify(_load_settings())

    @app.post("/api/hub/ptpclock/settings")
    @login_required
    @csrf_protect
    def ptpclock_settings_post():
        data = request.get_json(silent=True) or {}
        s = _load_settings()
        for k in ("brand", "timezone", "ntp_server"):
            if k in data:
                s[k] = str(data[k]).strip()
        _save_settings(s)
        return jsonify({"ok": True})

    # ── Logo upload/serve/delete ──────────────────────────────────────
    @app.post("/api/hub/ptpclock/logo")
    @login_required
    @csrf_protect
    def ptpclock_logo_upload():
        f = request.files.get("logo")
        if not f or not f.filename:
            return jsonify({"ok": False, "error": "No file"}), 400
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"):
            return jsonify({"ok": False, "error": "Invalid file type"}), 400
        os.makedirs(_LOGO_DIR, exist_ok=True)
        # Remove old logo
        s = _load_settings()
        old = s.get("logo_filename")
        if old:
            try: os.unlink(os.path.join(_LOGO_DIR, old))
            except Exception: pass
        fname = f"logo{ext}"
        f.save(os.path.join(_LOGO_DIR, fname))
        s["logo_filename"] = fname
        _save_settings(s)
        return jsonify({"ok": True, "filename": fname})

    @app.get("/api/hub/ptpclock/logo")
    @login_required
    def ptpclock_logo_serve():
        s = _load_settings()
        # Check for preset-specific logo
        preset_id = request.args.get("preset", "")
        fname = ""
        if preset_id:
            preset = next((p for p in s.get("presets", []) if p.get("id") == preset_id), None)
            if preset:
                fname = preset.get("logo_filename", "")
        if not fname:
            fname = s.get("logo_filename", "")
        if not fname:
            return Response("", status=404)
        path = os.path.join(_LOGO_DIR, fname)
        if not os.path.isfile(path):
            return Response("", status=404)
        ext = os.path.splitext(fname)[1].lower()
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "svg": "image/svg+xml", "gif": "image/gif", "webp": "image/webp"
                }.get(ext.lstrip("."), "application/octet-stream")
        with open(path, "rb") as fh:
            data = fh.read()
        return Response(data, mimetype=mime,
                        headers={"Cache-Control": "public, max-age=3600"})

    @app.delete("/api/hub/ptpclock/logo")
    @login_required
    @csrf_protect
    def ptpclock_logo_delete():
        s = _load_settings()
        fname = s.get("logo_filename", "")
        if fname:
            try: os.unlink(os.path.join(_LOGO_DIR, fname))
            except Exception: pass
        s["logo_filename"] = ""
        _save_settings(s)
        return jsonify({"ok": True})

    # ── Preset CRUD ─────────────────────────────────────────────────
    @app.post("/api/hub/ptpclock/presets")
    @login_required
    @csrf_protect
    def ptpclock_preset_add():
        import uuid
        data = request.get_json(silent=True) or {}
        s = _load_settings()
        presets = s.get("presets", [])
        p = {
            "id": str(uuid.uuid4())[:8],
            "name": str(data.get("name", "")).strip(),
            "brand": str(data.get("brand", "")).strip(),
            "timezone": str(data.get("timezone", "")).strip(),
            "mode": str(data.get("mode", "digital")).strip(),
            "logo_filename": str(data.get("logo_filename", "")).strip(),
            "stream": str(data.get("stream", "")).strip(),
        }
        presets.append(p)
        s["presets"] = presets
        _save_settings(s)
        return jsonify({"ok": True, "id": p["id"]})

    @app.delete("/api/hub/ptpclock/presets/<pid>")
    @login_required
    @csrf_protect
    def ptpclock_preset_delete(pid):
        s = _load_settings()
        presets = s.get("presets", [])
        target = next((p for p in presets if p.get("id") == pid), None)
        if target:
            # Remove preset logo if it has one
            logo = target.get("logo_filename", "")
            if logo:
                try: os.unlink(os.path.join(_LOGO_DIR, logo))
                except Exception: pass
            s["presets"] = [p for p in presets if p.get("id") != pid]
            _save_settings(s)
        return jsonify({"ok": True})

    # ── Logo upload (for presets — saves with unique name) ────────────
    @app.post("/api/hub/ptpclock/logo/upload")
    @login_required
    @csrf_protect
    def ptpclock_logo_upload_generic():
        import uuid
        f = request.files.get("logo")
        if not f or not f.filename:
            return jsonify({"ok": False, "error": "No file"}), 400
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"):
            return jsonify({"ok": False, "error": "Invalid file type"}), 400
        os.makedirs(_LOGO_DIR, exist_ok=True)
        fname = f"logo_{uuid.uuid4().hex[:8]}{ext}"
        f.save(os.path.join(_LOGO_DIR, fname))
        return jsonify({"ok": True, "filename": fname})

    # ── Level meter API (stream levels for display) ───────────────────
    @app.get("/api/hub/ptpclock/level")
    @login_required
    def ptpclock_level():
        stream_name = request.args.get("stream", "")
        if not stream_name:
            return jsonify({"level_dbfs": None})
        # Check local inputs
        cfg = monitor.app_cfg
        for inp in cfg.inputs:
            if inp.name == stream_name and inp.enabled:
                return jsonify({
                    "level_dbfs": round(inp._last_level_dbfs, 1),
                    "peak_dbfs": round(inp._last_peak_dbfs, 1),
                    "stream": stream_name,
                })
        # Check hub sites
        hub = ctx.get("hub_server")
        if hub:
            with hub._lock:
                for sname, sdata in hub._sites.items():
                    for sd in sdata.get("streams", []):
                        if sd.get("name") == stream_name:
                            return jsonify({
                                "level_dbfs": round(float(sd.get("level_dbfs", -120)), 1),
                                "peak_dbfs": round(float(sd.get("peak_dbfs", -120)), 1),
                                "stream": stream_name,
                                "site": sname,
                            })
        return jsonify({"level_dbfs": None, "stream": stream_name})

    # ── Mic Live API (client-only, not exposed on hub) ────────────────
    # This endpoint is called by external systems (e.g. studio desk GPIO,
    # automation system) to signal mic live/off state.
    cfg = monitor.app_cfg
    if cfg.hub.mode not in ("hub",):
        @app.post("/api/ptpclock/mic")
        def ptpclock_mic_post():
            data = request.get_json(silent=True) or {}
            live = bool(data.get("live", False))
            now = _time.time()
            with _mic_lock:
                was_live = _mic_state["live"]
                if live and not was_live:
                    _mic_state["live"] = True
                    _mic_state["since"] = now
                elif not live and was_live:
                    _mic_state["live"] = False
                    dur = int(now - _mic_state["since"])
                    _mic_state["last_duration"] = dur
                    _mic_state["since"] = 0
                _mic_state["updated"] = now
            return jsonify({"ok": True, "live": live})

        @app.get("/api/ptpclock/mic")
        def ptpclock_mic_get():
            with _mic_lock:
                return jsonify({
                    "live": _mic_state["live"],
                    "since": _mic_state["since"],
                    "last_duration": _mic_state["last_duration"],
                })
