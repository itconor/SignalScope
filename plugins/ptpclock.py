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
    "id":      "ptpclock",
    "label":   "PTP Clock",
    "url":     "/hub/ptpclock",
    "icon":    "",
    "version": "1.4.1",
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

# Per-preset mic state: {preset_id: {live, since, last_duration, updated}}
_mic_states = {}
_mic_lock = threading.Lock()

def _get_mic(preset_id):
    with _mic_lock:
        return _mic_states.get(preset_id, {"live": False, "since": 0, "last_duration": 0, "updated": 0})

def _set_mic(preset_id, live):
    now = _time.time()
    with _mic_lock:
        state = _mic_states.get(preset_id, {"live": False, "since": 0, "last_duration": 0, "updated": 0})
        was_live = state["live"]
        if live and not was_live:
            state["live"] = True
            state["since"] = now
        elif not live and was_live:
            state["live"] = False
            state["last_duration"] = int(now - state["since"])
            state["since"] = 0
        state["updated"] = now
        _mic_states[preset_id] = state

# ═══════════════════════════════════════════════════════════════════════════════
#  NTP OFFSET CHECK (fallback when PTP not available)
# ═══════════════════════════════════════════════════════════════════════════════

_ntp_cache = {"offset_ms": None, "server": "", "ts": 0}

# ── Client clock presets cache (hub-side) ─────────────────────────
_client_clocks_cache = {"data": [], "ts": 0}
_client_clocks_lock = threading.Lock()

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


def _fetch_client_clocks(hub_server):
    """Fetch clock presets from all connected client sites. Cached for 30s."""
    now = _time.time()
    with _client_clocks_lock:
        if now - _client_clocks_cache["ts"] < 30:
            return _client_clocks_cache["data"]
    # Fetch in background-ish (blocking but fast — clients are on LAN)
    results = []
    if not hub_server:
        return results
    try:
        import urllib.request
        with hub_server._lock:
            sites = {k: dict(v) for k, v in hub_server._sites.items() if v.get("_approved")}
        for site_name, sdata in sites.items():
            addr = sdata.get("_client_addr", "")
            if not addr:
                continue
            url = f"{addr}/api/hub/ptpclock/settings"
            try:
                req = urllib.request.Request(url, headers={"Accept": "application/json"})
                resp = urllib.request.urlopen(req, timeout=3)
                d = json.loads(resp.read())
                presets = d.get("presets", [])
                for p in presets:
                    p["_site"] = site_name
                    p["_client_addr"] = addr
                results.extend(presets)
            except Exception:
                pass  # client may not have the plugin or be unreachable
    except Exception:
        pass
    with _client_clocks_lock:
        _client_clocks_cache["data"] = results
        _client_clocks_cache["ts"] = _time.time()
    return results


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
@media(max-width:480px){.modes{grid-template-columns:1fr}}
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
<div style="display:flex;align-items:center;gap:12px;margin-bottom:6px">
  <a href="/" style="color:#64748b;text-decoration:none;font-size:20px" title="Back to SignalScope">&larr;</a>
  <h1 style="margin:0">PTP Clock</h1>
</div>
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
  <div class="mode-card" data-mode="led">
    <div class="icon">&#128308;</div>
    <div class="name">LED Studio</div>
    <div class="desc">LED-style digital display with seconds ring</div>
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
      <input type="text" id="f-tz" list="tz-list" value="{{settings.timezone}}" placeholder="Start typing...">
      <div class="help">IANA timezone. Leave blank for server default.</div>
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
    <div class="preset-row" style="display:flex;align-items:center;gap:8px;padding:8px 0;border-bottom:1px solid #1e293b;flex-wrap:wrap">
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
      <div><label>Timezone</label><input type="text" id="np-tz" list="tz-list" placeholder="Start typing..."></div>
      <div><label>Mode</label><select id="np-mode"><option value="digital">Digital</option><option value="studio">Studio (Analog)</option><option value="led">LED Studio</option></select></div>
    </div>
    <div class="row">
      <div>
        <label>Logo</label>
        <input type="file" id="np-logo" accept="image/*" style="font-size:12px;color:#94a3b8">
        <div class="help">Optional. PNG or JPG for the clock face.</div>
      </div>
      <div>
        <label>Audio Level Meters</label>
        <div id="meter-list" style="margin-bottom:6px"></div>
        <div style="display:flex;gap:6px">
          <select id="np-stream-sel" style="flex:1"><option value="">Select stream...</option>
          {% for s in streams %}<option value="{{s}}">{{s}}</option>{% endfor %}
          </select>
          <input type="text" id="np-stream-label" placeholder="Custom label (optional)" style="flex:1">
          <button type="button" class="btn" style="font-size:11px;padding:4px 10px;white-space:nowrap" id="np-add-meter">+ Add</button>
        </div>
        <div class="help">Add multiple meters. Custom label overrides the stream name on the display.</div>
      </div>
    </div>
    <div style="margin-top:8px;font-size:12px;color:#64748b">Colours (optional):</div>
    <div class="row" style="margin-top:4px">
      <div><label style="margin-top:0">Background</label><input type="color" id="np-bg" value="#0b1222" style="width:100%;height:32px;border:1px solid #334155;border-radius:4px;cursor:pointer"></div>
      <div><label style="margin-top:0">Accent / Hands</label><input type="color" id="np-accent" value="#3b82f6" style="width:100%;height:32px;border:1px solid #334155;border-radius:4px;cursor:pointer"></div>
      <div><label style="margin-top:0">Text</label><input type="color" id="np-text" value="#e0e6f0" style="width:100%;height:32px;border:1px solid #334155;border-radius:4px;cursor:pointer"></div>
      <div><label style="margin-top:0">Muted</label><input type="color" id="np-muted" value="#64748b" style="width:100%;height:32px;border:1px solid #334155;border-radius:4px;cursor:pointer"></div>
    </div>
    <button class="btn" id="add-preset-btn" style="margin-top:10px">Add Clock</button>
    <span class="saved" id="preset-ok">Added</span>
  </div>
</div>

{% if client_clocks %}
<div class="card">
  <h2>Client Clocks</h2>
  <div class="help" style="margin-bottom:8px">Clocks configured on connected client sites. Open them directly from the hub.</div>
  {% for p in client_clocks %}
  <div style="display:flex;align-items:center;gap:8px;padding:8px 0;border-bottom:1px solid #1e293b">
    <div style="flex:1">
      <div style="font-size:14px;font-weight:600">{{p.name or p.brand or 'Untitled'}}</div>
      <div style="font-size:11px;color:#64748b">{{p._site}} — {{p.mode or 'digital'}} — {{p.brand or 'No brand'}}{% if p.stream %} — Meters: {{p.stream.split(',')|length}}{% endif %}</div>
    </div>
    <a class="btn" style="font-size:11px;padding:4px 10px" href="{{p._client_addr}}/hub/ptpclock/display?preset={{p.id}}" target="_blank">Open on Client</a>
    <a class="btn btn-outline" style="font-size:11px;padding:4px 10px" href="/hub/ptpclock/display?proxy_site={{p._site}}&preset={{p.id}}" target="_blank">Open via Hub</a>
  </div>
  {% endfor %}
</div>
{% endif %}

<div class="card">
  <h2>Quick Links</h2>
  <div class="help" style="margin-bottom:8px">Direct URLs using the settings above (no preset):</div>
  <div id="links" style="font-size:13px;color:#64748b;line-height:2"></div>
</div>

<div class="card">
  <h2>Mic Live API</h2>
  <div class="help" style="margin-bottom:8px">Control the MIC LIVE indicator from external systems (GPIO, automation, mixing desk). Only available on client nodes (not exposed on the hub).</div>
  <div style="font-size:13px;color:#94a3b8;line-height:1.8">
    <div style="margin-bottom:8px"><b>Turn mic ON:</b></div>
    <code style="background:#1e293b;padding:4px 8px;border-radius:4px;font-size:12px;display:block;overflow-x:auto">curl -X POST http://&lt;client-ip&gt;:5000/api/ptpclock/mic/&lt;preset-id&gt; -H "Content-Type: application/json" -d '{"live": true}'</code>
    <div style="margin-top:8px;margin-bottom:8px"><b>Turn mic OFF:</b></div>
    <code style="background:#1e293b;padding:4px 8px;border-radius:4px;font-size:12px;display:block;overflow-x:auto">curl -X POST http://&lt;client-ip&gt;:5000/api/ptpclock/mic/&lt;preset-id&gt; -H "Content-Type: application/json" -d '{"live": false}'</code>
    <div style="margin-top:8px;margin-bottom:8px"><b>Check status:</b></div>
    <code style="background:#1e293b;padding:4px 8px;border-radius:4px;font-size:12px;display:block;overflow-x:auto">curl http://&lt;client-ip&gt;:5000/api/ptpclock/mic/&lt;preset-id&gt;</code>
    <div style="margin-top:12px;color:#64748b">
      <div>Replace <code>&lt;preset-id&gt;</code> with the clock's preset ID (shown in the URL when opened).</div>
      <div style="margin-top:4px">Use <code>/api/ptpclock/mic</code> (no preset ID) for the default clock.</div>
      <div style="margin-top:4px">When live: red pulsing bar with count-up timer. When off: shows duration of last live session.</div>
    </div>
  </div>
</div>

</div>
<datalist id="tz-list"></datalist>
<script nonce="{{csp_nonce()}}">
// Populate timezone datalist from Intl API
(function(){
  var dl=document.getElementById('tz-list');
  var recent={{settings.get('recent_timezones',[])|tojson}};
  // Common broadcast timezones first, then all from Intl
  var common=['Europe/London','Europe/Dublin','Europe/Paris','Europe/Berlin','Europe/Amsterdam','Europe/Brussels','Europe/Rome','Europe/Madrid','Europe/Lisbon','Europe/Stockholm','Europe/Helsinki','Europe/Warsaw','Europe/Prague','Europe/Vienna','Europe/Zurich','Europe/Athens','Europe/Moscow','US/Eastern','US/Central','US/Mountain','US/Pacific','America/New_York','America/Chicago','America/Denver','America/Los_Angeles','America/Toronto','America/Vancouver','Asia/Tokyo','Asia/Shanghai','Asia/Singapore','Asia/Dubai','Asia/Kolkata','Australia/Sydney','Australia/Melbourne','Pacific/Auckland','UTC'];
  var all=[];
  try{all=Intl.supportedValuesOf('timeZone')}catch(e){}
  var seen={};var opts=[];
  // Recent first
  recent.forEach(function(tz){if(!seen[tz]){seen[tz]=1;opts.push(tz)}});
  // Then common
  common.forEach(function(tz){if(!seen[tz]){seen[tz]=1;opts.push(tz)}});
  // Then all
  all.forEach(function(tz){if(!seen[tz]){seen[tz]=1;opts.push(tz)}});
  opts.forEach(function(tz){var o=document.createElement('option');o.value=tz;dl.appendChild(o)});
})();

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
  var _sb=this;
  var body={
    brand:document.getElementById('f-brand').value.trim(),
    timezone:document.getElementById('f-tz').value.trim(),
    ntp_server:document.getElementById('f-ntp').value.trim()
  };
  _btnLoad(_sb);
  fetch('/api/hub/ptpclock/settings',{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':_getCsrf()},body:JSON.stringify(body),credentials:'same-origin'})
  .then(function(r){return r.json()}).then(function(d){
    _btnReset(_sb);
    if(d.ok){var s=document.getElementById('save-ok');s.style.display='inline';setTimeout(function(){s.style.display='none'},2000)}
  }).catch(function(){_btnReset(_sb);});
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

// ── Meter list management ─────────────────────────────────────────
var _meterItems=[];
function renderMeterList(){
  var el=document.getElementById('meter-list');
  el.innerHTML=_meterItems.map(function(m,i){
    return '<div style="display:flex;align-items:center;gap:6px;padding:3px 0">'
      +'<span style="font-size:12px;color:#94a3b8;flex:1">'+m.stream.replace(/</g,'&lt;')
      +(m.label?' <span style="color:#64748b">('+m.label.replace(/</g,'&lt;')+')</span>':'')+'</span>'
      +'<button type="button" style="background:none;border:none;color:#ef4444;cursor:pointer;font-size:14px" data-rm-meter="'+i+'">x</button></div>';
  }).join('');
}
document.getElementById('np-add-meter').addEventListener('click',function(){
  var sel=document.getElementById('np-stream-sel');
  var lbl=document.getElementById('np-stream-label');
  if(!sel.value)return;
  _meterItems.push({stream:sel.value,label:lbl.value.trim()});
  sel.value='';lbl.value='';
  renderMeterList();
});
document.getElementById('meter-list').addEventListener('click',function(e){
  var rm=e.target.closest('[data-rm-meter]');
  if(rm){_meterItems.splice(parseInt(rm.dataset.rmMeter),1);renderMeterList()}
});

// ── Presets ───────────────────────────────────────────────────────
document.getElementById('add-preset-btn').addEventListener('click',function(){
  var _apb=this;
  var name=document.getElementById('np-name').value.trim();
  var brand=document.getElementById('np-brand').value.trim();
  var tz=document.getElementById('np-tz').value.trim();
  var mode=document.getElementById('np-mode').value;
  var stream=_meterItems.map(function(m){return m.stream+'|'+m.label}).join(',');
  var logoFile=document.getElementById('np-logo').files[0];

  function doCreate(logoFilename){
    fetch('/api/hub/ptpclock/presets',{method:'POST',
      headers:{'Content-Type':'application/json','X-CSRFToken':_getCsrf()},
      body:JSON.stringify({name:name,brand:brand,timezone:tz,mode:mode,stream:stream,logo_filename:logoFilename||'',
        color_bg:document.getElementById('np-bg').value,
        color_accent:document.getElementById('np-accent').value,
        color_text:document.getElementById('np-text').value,
        color_muted:document.getElementById('np-muted').value}),
      credentials:'same-origin'
    }).then(function(r){return r.json()}).then(function(d){
      _btnReset(_apb);
      if(d.ok)location.reload();
    }).catch(function(){_btnReset(_apb);});
  }

  _btnLoad(_apb);
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
:root{--bg:{{colors.bg}};--accent:{{colors.accent}};--text:{{colors.text}};--muted:{{colors.muted}};--face:#0f172a;--border:#334155;--mark:#94a3b8}
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%;overflow:hidden;background:var(--bg);color:var(--text);font-family:'SF Mono','Consolas','Menlo',monospace}
body{display:flex;flex-direction:column;align-items:center;justify-content:center;user-select:none}

/* ── Digital Mode ──────────────────────────────────────────────── */
.digital{display:none;flex-direction:column;align-items:center;justify-content:center;width:100%;height:100%}
.d-logo{max-height:12vh;max-width:40vw;margin-bottom:1.5vh;object-fit:contain}
.d-brand{font-size:clamp(14px,2.5vw,28px);color:var(--muted);letter-spacing:0.15em;text-transform:uppercase;margin-bottom:1vh}
.d-row{display:flex;gap:clamp(20px,6vw,80px);align-items:flex-start;justify-content:center}
.d-col{text-align:center}
.d-label{font-size:clamp(12px,1.8vw,22px);color:var(--muted);letter-spacing:0.2em;text-transform:uppercase;margin-bottom:0.5vh}
.d-time{font-size:clamp(48px,12vw,160px);font-weight:200;letter-spacing:0.02em;line-height:1;color:var(--text)}
.d-tenths{font-size:clamp(24px,5vw,64px);font-weight:200;color:var(--muted);vertical-align:super;margin-left:2px}
.d-date{font-size:clamp(14px,2.2vw,26px);color:var(--muted);margin-top:2vh;letter-spacing:0.1em}

/* ── Studio Mode ───────────────────────────────────────────────── */
.studio{display:none;flex-direction:column;align-items:center;justify-content:center;width:100%;height:100%}
.s-brand{font-size:clamp(14px,2.5vw,28px);color:var(--muted);letter-spacing:0.15em;text-transform:uppercase;margin-bottom:1vh}
.s-canvas-wrap{position:relative;width:min(65vh,65vw);height:min(65vh,65vw)}
.s-canvas-wrap canvas{width:100%;height:100%}
.s-utc{font-size:clamp(18px,3vw,36px);color:#f1f5f9;margin-top:1.5vh;letter-spacing:0.05em}
.s-date{font-size:clamp(12px,1.8vw,20px);color:#64748b;margin-top:0.5vh;letter-spacing:0.1em}

/* ── LED Dot Clock (Wharton-style) ─────────────────────────────── */
.led{display:none;flex-direction:row;align-items:center;justify-content:center;width:100%;height:100%;gap:clamp(16px,4vw,60px);padding:0 3vw}
.led-canvas-wrap{position:relative;width:min(80vh,50vw);height:min(80vh,50vw);flex-shrink:0}
.led-canvas-wrap canvas{width:100%;height:100%}
.led-side{display:flex;flex-direction:column;align-items:flex-start;justify-content:center;gap:1.5vh;min-width:0}
.led-side-logo{max-height:12vh;max-width:25vw;object-fit:contain}
.led-side-brand{font-size:clamp(18px,3vw,42px);color:var(--text);font-weight:700;letter-spacing:0.08em;text-transform:uppercase;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
.led-side-date{font-size:clamp(12px,1.5vw,22px);color:var(--muted);letter-spacing:0.1em}
.led-side-info{font-size:clamp(11px,1.2vw,16px);color:var(--muted)}
.led-side-info .val{color:var(--text);font-weight:600;font-family:'SF Mono','Consolas',monospace}

/* ── Mic Live ──────────────────────────────────────────────────── */
.mic-bar{display:none;position:fixed;top:0;left:0;right:0;padding:10px 20px;text-align:center;font-size:clamp(16px,3vw,32px);font-weight:700;letter-spacing:0.15em;text-transform:uppercase;z-index:100;transition:all 0.3s}
.mic-bar.live{display:block;background:#dc2626;color:#fff;animation:mic-pulse 1.5s ease-in-out infinite}
.mic-bar.off{display:block;background:#1e293b;color:#64748b;font-weight:400;font-size:clamp(12px,2vw,20px)}
@keyframes mic-pulse{0%,100%{opacity:1}50%{opacity:0.7}}
.mic-timer{font-size:clamp(12px,1.5vw,18px);font-weight:400;margin-left:12px;opacity:0.8}

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
  {% if has_logo %}<img class="d-logo" src="/api/hub/ptpclock/logo?preset={{preset_id}}{{'&proxy_site='+proxy_site if proxy_site else ''}}" alt="">{% endif %}
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

<!-- LED Dot Clock (Wharton-style) -->
<div class="led" id="v-led">
  <div class="led-canvas-wrap">
    <canvas id="led-canvas" width="800" height="800"></canvas>
  </div>
  <div class="led-side" id="led-side">
    {% if has_logo %}<img class="led-side-logo" src="/api/hub/ptpclock/logo?preset={{preset_id}}{{'&proxy_site='+proxy_site if proxy_site else ''}}" alt="">{% endif %}
    <div class="led-side-brand" id="led-brand"></div>
    <div class="led-side-date" id="led-date">---</div>
    <div class="led-side-info">
      <span id="led-tz-lbl">LOCAL</span> <span class="val" id="led-local">--:--:--</span>
    </div>
  </div>
</div>

<!-- Level Meters -->
<div id="level-container" style="position:fixed;left:12px;bottom:40px;top:60px;display:none;gap:8px"></div>

<!-- Status Bar -->
<div class="status-bar">
  <span><span class="ptp-dot idle" id="ptp-dot"></span> <span id="sync-label">PTP</span> <span class="ptp-val" id="ptp-state">---</span></span>
  <span>Offset <span class="ptp-val" id="ptp-off">---</span></span>
  <span>Jitter <span class="ptp-val" id="ptp-jit">---</span></span>
  <span>GM <span class="ptp-val" id="ptp-gm">---</span></span>
  <span style="margin-left:auto;color:#94a3b8;font-size:clamp(8px,1vw,11px);letter-spacing:0.05em">Powered by SignalScope</span>
</div>

<!-- Mode Switcher -->
<div class="mode-sw">
  <button id="btn-digital" data-mode="digital">Digital</button>
  <button id="btn-studio" data-mode="studio">Studio</button>
  <button id="btn-led" data-mode="led">LED</button>
  <button data-mode="menu" style="font-size:10px">Menu</button>
</div>

<script nonce="{{csp_nonce()}}">
var _mode='digital',_brand='{{brand|e}}',_tz='{{tz|e}}';
var _hasLogo={{has_logo|tojson}};
var _presetId='{{preset_id|e}}';
var _stream='{{stream|e}}';
var _C={bg:'{{colors.bg}}',accent:'{{colors.accent}}',text:'{{colors.text}}',muted:'{{colors.muted}}'};
var _proxySite='{{proxy_site|e}}';
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
  document.getElementById('v-led').style.display=m==='led'?'flex':'none';
  document.querySelectorAll('.mode-sw button').forEach(function(b){b.className=b.dataset.mode===m?'active':''});
  if(m==='studio')resizeCanvas();
  if(m==='led')resizeLedCanvas();
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
  document.getElementById('led-brand').textContent=_brand;
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
  var _logoUrl='/api/hub/ptpclock/logo?preset='+(_presetId||'');
  if(_proxySite)_logoUrl+='&proxy_site='+_proxySite;
  _logoImg.src=_logoUrl;
}

// ── Level meters (multiple streams, comma-separated) ─────────────
// Parse stream|label pairs
var _meterDefs=_stream?_stream.split(',').filter(Boolean).map(function(s){
  var parts=s.split('|');return{stream:parts[0],label:parts[1]||parts[0]}
}):[];
var _meters={};
if(_meterDefs.length){
  var container=document.getElementById('level-container');
  container.style.display='flex';
  _meterDefs.forEach(function(def){
    var wrap=document.createElement('div');
    wrap.style.cssText='display:flex;flex-direction:column;align-items:center;width:28px';
    wrap.innerHTML='<div style="flex:1;width:14px;background:#1e293b;border-radius:7px;position:relative;overflow:hidden;min-height:40px">'
      +'<div class="mfill" style="position:absolute;bottom:0;left:0;right:0;background:linear-gradient(to top,#22c55e 0%,#22c55e 60%,#f59e0b 80%,#ef4444 100%);border-radius:7px;transition:height 0.15s;height:0%"></div>'
      +'<div class="mpeak" style="position:absolute;left:0;right:0;height:2px;background:#f1f5f9;bottom:0%;transition:bottom 0.05s"></div>'
      +'</div>'
      +'<div class="mval" style="font-size:9px;color:#64748b;margin-top:3px;font-family:monospace">---</div>'
      +'<div style="font-size:8px;color:#475569;writing-mode:vertical-rl;text-orientation:mixed;margin-top:3px;max-height:70px;overflow:hidden">'+def.label.replace(/</g,'&lt;')+'</div>';
    container.appendChild(wrap);
    _meters[def.stream]={el:wrap,peak:-120};
  });
  setInterval(function(){
    _meterDefs.forEach(function(def){var sn=def.stream;
      fetch('/api/hub/ptpclock/level?stream='+encodeURIComponent(sn),{credentials:'same-origin'})
      .then(function(r){return r.json()}).then(function(d){
        var m=_meters[sn];if(!m||d.level_dbfs===null)return;
        var lev=d.level_dbfs,pk=d.peak_dbfs||lev;
        if(pk>m.peak)m.peak=pk;
        var pct=Math.max(0,Math.min(100,((lev+60)/60)*100));
        var ppct=Math.max(0,Math.min(100,((m.peak+60)/60)*100));
        m.el.querySelector('.mfill').style.height=pct+'%';
        m.el.querySelector('.mpeak').style.bottom=ppct+'%';
        m.el.querySelector('.mval').textContent=lev.toFixed(0);
        m.peak-=0.5;if(m.peak<lev)m.peak=lev;
      }).catch(function(){});
    });
  },250);
}

// ── Poll server time ──────────────────────────────────────────────
function poll(){
  var _timeUrl='/api/hub/ptpclock/time'+(_presetId?'?preset='+_presetId:'');
  fetch(_timeUrl,{credentials:'same-origin'}).then(function(r){return r.json()}).then(function(d){
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
  if(_mode==='digital')renderDigital();
  else if(_mode==='studio')renderStudio();
  else if(_mode==='led')renderLed();
  var dateId=_mode==='studio'?'s-date':(_mode==='led'?'led-date':'d-date');
  var dateEl=document.getElementById(dateId);
  if(dateEl)dateEl.textContent=_dateStr;
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
  c.beginPath();c.arc(0,0,r*0.95,0,Math.PI*2);c.fillStyle=_C.bg;c.fill();
  c.strokeStyle=_C.muted;c.lineWidth=r*0.01;c.stroke();

  // Hour markers
  for(var i=0;i<12;i++){
    var a=i*Math.PI/6-Math.PI/2;var isQ=i%3===0;
    var len=isQ?r*0.12:r*0.06;var thick=isQ?r*0.025:r*0.012;
    var x1=Math.cos(a)*(r*0.82),y1=Math.sin(a)*(r*0.82);
    var x2=Math.cos(a)*(r*0.82+len),y2=Math.sin(a)*(r*0.82+len);
    c.beginPath();c.moveTo(x1,y1);c.lineTo(x2,y2);
    c.strokeStyle=isQ?_C.text:_C.muted;c.lineWidth=thick;c.lineCap='round';c.stroke();
  }
  // Minute ticks
  for(var i=0;i<60;i++){if(i%5===0)continue;
    var a=i*Math.PI/30-Math.PI/2;
    c.beginPath();c.moveTo(Math.cos(a)*(r*0.88),Math.sin(a)*(r*0.88));
    c.lineTo(Math.cos(a)*(r*0.91),Math.sin(a)*(r*0.91));
    c.strokeStyle=_C.muted+'80';c.lineWidth=r*0.005;c.stroke();
  }

  // Logo on face (larger — 40% of radius)
  if(_logoImg&&_logoImg.complete&&_logoImg.naturalWidth>0){
    var lsz=r*0.4;var aspect=_logoImg.naturalWidth/_logoImg.naturalHeight;
    var lw,lh;if(aspect>1){lw=lsz;lh=lsz/aspect}else{lh=lsz;lw=lsz*aspect}
    c.drawImage(_logoImg,-lw/2,-r*0.35-lh/2,lw,lh);
  }else if(_brand){
    c.font='600 '+Math.round(r*0.08)+'px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';
    c.fillStyle=_C.muted;c.textAlign='center';c.textBaseline='middle';
    c.fillText(_brand,0,-r*0.3);
  }

  // "PTP" / "NTP" label
  c.font='600 '+Math.round(r*0.05)+'px monospace';
  c.fillStyle=_C.muted+'80';c.textAlign='center';c.textBaseline='middle';
  c.fillText(_syncType,0,r*0.35);

  // Hands
  var sec=_utcS+_utcMs/1000,min=_utcM+sec/60,hr=(_utcH%12)+min/60;
  // Hour
  var ha=hr*Math.PI/6-Math.PI/2;
  c.beginPath();c.moveTo(0,0);c.lineTo(Math.cos(ha)*r*0.5,Math.sin(ha)*r*0.5);
  c.strokeStyle=_C.text;c.lineWidth=r*0.04;c.lineCap='round';c.stroke();
  // Minute
  var ma=min*Math.PI/30-Math.PI/2;
  c.beginPath();c.moveTo(0,0);c.lineTo(Math.cos(ma)*r*0.7,Math.sin(ma)*r*0.7);
  c.strokeStyle=_C.text;c.lineWidth=r*0.025;c.lineCap='round';c.stroke();
  // Second (sweep)
  var sa=sec*Math.PI/30-Math.PI/2;
  c.beginPath();c.moveTo(Math.cos(sa+Math.PI)*r*0.1,Math.sin(sa+Math.PI)*r*0.1);
  c.lineTo(Math.cos(sa)*r*0.82,Math.sin(sa)*r*0.82);
  c.strokeStyle=_C.accent;c.lineWidth=r*0.01;c.lineCap='round';c.stroke();
  // Center dot
  c.beginPath();c.arc(0,0,r*0.025,0,Math.PI*2);c.fillStyle=_C.accent;c.fill();

  c.restore();
  document.getElementById('s-utc').textContent=pad2(_utcH)+':'+pad2(_utcM)+':'+pad2(_utcS)+'.'+Math.floor(_utcMs/100);
}

// ── LED Wharton-style dot clock ───────────────────────────────────
var _ledCvs,_ledCtx,_ledW;
function resizeLedCanvas(){
  _ledCvs=document.getElementById('led-canvas');
  if(!_ledCvs)return;
  var wrap=_ledCvs.parentElement;
  var sz=Math.min(wrap.clientWidth,wrap.clientHeight);
  _ledCvs.width=sz*2;_ledCvs.height=sz*2;
  _ledCvs.style.width=sz+'px';_ledCvs.style.height=sz+'px';
  _ledCtx=_ledCvs.getContext('2d');_ledW=sz*2;
}
window.addEventListener('resize',function(){if(_mode==='led')resizeLedCanvas()});

// 5x7 dot matrix font for digits and colon
var _DOT_FONT={
  '0':['01110','10001','10011','10101','11001','10001','01110'],
  '1':['00100','01100','00100','00100','00100','00100','01110'],
  '2':['01110','10001','00001','00010','00100','01000','11111'],
  '3':['01110','10001','00001','00110','00001','10001','01110'],
  '4':['00010','00110','01010','10010','11111','00010','00010'],
  '5':['11111','10000','11110','00001','00001','10001','01110'],
  '6':['01110','10001','10000','11110','10001','10001','01110'],
  '7':['11111','00001','00010','00100','01000','01000','01000'],
  '8':['01110','10001','10001','01110','10001','10001','01110'],
  '9':['01110','10001','10001','01111','00001','10001','01110'],
  ':':['000','000','010','000','010','000','000']
};

function renderLed(){
  if(!_ledCtx)resizeLedCanvas();
  if(!_ledCtx)return;
  var c=_ledCtx,w=_ledW,r=w/2;
  c.clearRect(0,0,w,w);

  var accent=_C.accent;
  var dim=_C.muted+'40';  // very dim for inactive dots
  var dotR=w*0.008;        // dot radius

  // ── Outer seconds ring (60 dots in a circle) ────────────────
  var ringR=r*0.88;
  var curSec=_utcS+_utcMs/1000;
  for(var i=0;i<60;i++){
    var a=(i/60)*Math.PI*2-Math.PI/2;
    var x=r+Math.cos(a)*ringR;
    var y=r+Math.sin(a)*ringR;
    var active=i<=Math.floor(curSec);
    var isQuarter=i%15===0;
    var is5=i%5===0;
    var dr=isQuarter?dotR*1.6:(is5?dotR*1.3:dotR);
    c.beginPath();c.arc(x,y,dr,0,Math.PI*2);
    if(active){
      c.fillStyle=accent;c.fill();
      c.shadowColor=accent;c.shadowBlur=dr*3;c.fill();c.shadowBlur=0;
    }else{
      c.fillStyle=dim;c.fill();
    }
  }

  // ── Inner ring (decorative, thinner) ────────────────────────
  var innerR=r*0.72;
  for(var i=0;i<60;i++){
    var a=(i/60)*Math.PI*2-Math.PI/2;
    var x=r+Math.cos(a)*innerR;
    var y=r+Math.sin(a)*innerR;
    var is5=i%5===0;
    if(!is5)continue;
    c.beginPath();c.arc(x,y,dotR*0.8,0,Math.PI*2);
    c.fillStyle=dim;c.fill();
  }

  // ── Dot-matrix time HH:MM ──────────────────────────────────
  var timeStr=pad2(_utcH)+':'+pad2(_utcM);
  var cols=0;
  for(var ci=0;ci<timeStr.length;ci++){
    var ch=timeStr[ci];
    var glyph=_DOT_FONT[ch];
    if(glyph)cols+=glyph[0].length+(ci<timeStr.length-1?1:0);
  }
  var dotSz=w*0.018;
  var gap=dotSz*1.5;
  var totalW=cols*gap;
  var startX=r-totalW/2;
  var startY=r-3.5*gap-gap*0.5;
  var cx2=startX;
  for(var ci=0;ci<timeStr.length;ci++){
    var ch=timeStr[ci];
    var glyph=_DOT_FONT[ch];
    if(!glyph)continue;
    for(var row=0;row<7;row++){
      for(var col=0;col<glyph[row].length;col++){
        var on=glyph[row][col]==='1';
        var dx=cx2+col*gap;
        var dy=startY+row*gap;
        c.beginPath();c.arc(dx,dy,dotSz*0.5,0,Math.PI*2);
        if(on){
          c.fillStyle=accent;c.fill();
          c.shadowColor=accent;c.shadowBlur=dotSz;c.fill();c.shadowBlur=0;
        }else{
          c.fillStyle=dim;c.fill();
        }
      }
    }
    cx2+=glyph[0].length*gap+gap;
  }

  // ── Smaller seconds below ──────────────────────────────────
  var secStr=pad2(_utcS);
  var secDotSz=dotSz*0.6;
  var secGap=secDotSz*1.5;
  var secCols=0;
  for(var si=0;si<secStr.length;si++){
    var g=_DOT_FONT[secStr[si]];
    if(g)secCols+=g[0].length+(si<secStr.length-1?1:0);
  }
  var secStartX=r-secCols*secGap/2;
  var secStartY=startY+8*gap;
  var scx=secStartX;
  for(var si=0;si<secStr.length;si++){
    var g=_DOT_FONT[secStr[si]];
    if(!g)continue;
    for(var row=0;row<7;row++){
      for(var col=0;col<g[row].length;col++){
        var on=g[row][col]==='1';
        c.beginPath();c.arc(scx+col*secGap,secStartY+row*secGap,secDotSz*0.5,0,Math.PI*2);
        if(on){c.fillStyle=accent;c.fill();c.shadowColor=accent;c.shadowBlur=secDotSz*0.6;c.fill();c.shadowBlur=0}
        else{c.fillStyle=dim;c.fill()}
      }
    }
    scx+=g[0].length*secGap+secGap;
  }

  // Update side panel
  var ledLocal=document.getElementById('led-local');
  if(ledLocal)ledLocal.textContent=pad2(_locH)+':'+pad2(_locM)+':'+pad2(_locS);
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
        # Fetch client clocks if we're a hub
        hub = ctx.get("hub_server")
        client_clocks = _fetch_client_clocks(hub) if hub else []
        return render_template_string(MENU_TPL, settings=settings, build=BUILD,
                                       streams=streams, client_clocks=client_clocks)

    # ── Display page ──────────────────────────────────────────────────
    @app.get("/hub/ptpclock/display")
    @login_required
    def ptpclock_display():
        settings = _load_settings()
        preset_id = request.args.get("preset", "")
        proxy_site = request.args.get("proxy_site", "")
        preset = None

        # If proxying a client's preset, fetch it from the client
        if proxy_site and preset_id:
            hub = ctx.get("hub_server")
            if hub:
                try:
                    import urllib.request
                    with hub._lock:
                        sdata = hub._sites.get(proxy_site, {})
                    addr = sdata.get("_client_addr", "")
                    if addr:
                        url = f"{addr}/api/hub/ptpclock/settings"
                        resp = urllib.request.urlopen(url, timeout=3)
                        d = json.loads(resp.read())
                        preset = next((p for p in d.get("presets", []) if p.get("id") == preset_id), None)
                except Exception:
                    pass

        if not preset and preset_id:
            preset = next((p for p in settings.get("presets", []) if p.get("id") == preset_id), None)
        if preset:
            brand = preset.get("brand", "")
            tz    = preset.get("timezone", "")
            has_logo = bool(preset.get("logo_filename"))
            mode  = preset.get("mode", "digital")
            stream = preset.get("stream", "")
            colors = {
                "bg": preset.get("color_bg", "#0b1222"),
                "accent": preset.get("color_accent", "#3b82f6"),
                "text": preset.get("color_text", "#e0e6f0"),
                "muted": preset.get("color_muted", "#64748b"),
            }
        else:
            brand = request.args.get("brand", settings.get("brand", ""))
            tz    = request.args.get("tz", settings.get("timezone", ""))
            has_logo = bool(settings.get("logo_filename"))
            mode  = request.args.get("mode", "digital")
            stream = request.args.get("stream", "")
            colors = {"bg": "#0b1222", "accent": "#3b82f6", "text": "#e0e6f0", "muted": "#64748b"}
        # LED mode defaults to red accent (classic Wharton look)
        if mode == "led" and not preset:
            colors["accent"] = "#ef4444"
        return render_template_string(DISPLAY_TPL, brand=brand, tz=tz,
                                       has_logo=has_logo, build=BUILD,
                                       initial_mode=mode, stream=stream,
                                       preset_id=preset_id, colors=colors,
                                       proxy_site=proxy_site)

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

        # Mic state (per-preset)
        mic_preset = request.args.get("preset", "")
        mic = _get_mic(mic_preset) if mic_preset else _get_mic("default")
        mic_data = None
        if mic["updated"] > 0:
            mic_data = {
                "live": mic["live"],
                "since": mic["since"],
                "last_duration": mic["last_duration"],
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
        # Track recent timezones (most recent first, max 10)
        tz = s.get("timezone", "")
        if tz:
            recent = s.get("recent_timezones", [])
            if tz in recent:
                recent.remove(tz)
            recent.insert(0, tz)
            s["recent_timezones"] = recent[:10]
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
        preset_id = request.args.get("preset", "")
        proxy_site = request.args.get("proxy_site", "")

        # Proxy logo from client site if requested
        if proxy_site:
            hub = ctx.get("hub_server")
            if hub:
                try:
                    import urllib.request
                    with hub._lock:
                        sdata = hub._sites.get(proxy_site, {})
                    addr = sdata.get("_client_addr", "")
                    if addr:
                        url = f"{addr}/api/hub/ptpclock/logo"
                        if preset_id:
                            url += f"?preset={preset_id}"
                        resp = urllib.request.urlopen(url, timeout=5)
                        data = resp.read()
                        ct = resp.headers.get("Content-Type", "image/png")
                        return Response(data, mimetype=ct)
                except Exception:
                    pass
            return Response("", status=404)

        # Local logo lookup
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
            "color_bg": str(data.get("color_bg", "#0b1222")).strip(),
            "color_accent": str(data.get("color_accent", "#3b82f6")).strip(),
            "color_text": str(data.get("color_text", "#e0e6f0")).strip(),
            "color_muted": str(data.get("color_muted", "#64748b")).strip(),
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
    # Called by external systems (GPIO, automation) to signal mic state.
    # Per-preset: POST /api/ptpclock/mic/<preset_id> {"live": true}
    # Default:    POST /api/ptpclock/mic {"live": true}
    cfg = monitor.app_cfg
    if cfg.hub.mode not in ("hub",):
        @app.post("/api/ptpclock/mic")
        @app.post("/api/ptpclock/mic/<preset_id>")
        def ptpclock_mic_post(preset_id="default"):
            data = request.get_json(silent=True) or {}
            live = bool(data.get("live", False))
            _set_mic(preset_id, live)
            return jsonify({"ok": True, "live": live, "preset": preset_id})

        @app.get("/api/ptpclock/mic")
        @app.get("/api/ptpclock/mic/<preset_id>")
        def ptpclock_mic_get(preset_id="default"):
            mic = _get_mic(preset_id)
            return jsonify({
                "live": mic["live"],
                "since": mic["since"],
                "last_duration": mic["last_duration"],
                })
