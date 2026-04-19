# brandscreen.py — SignalScope Brand Screen plugin
# Animated full-screen studio branding display with studios, stations,
# live now-playing, orbit rings, SSE-driven instant assignment changes,
# brand-hued backgrounds, and audio-level reactive animations.
# Drop into the plugins/ subdirectory.

import os, json, uuid, threading, mimetypes, functools, colorsys, time as _time, hashlib
import queue as _queue
from flask import (request, jsonify, render_template_string,
                   send_file, session, Response, stream_with_context, g, make_response)

SIGNALSCOPE_PLUGIN = {
    "id":       "brandscreen",
    "label":    "Brand Screen",
    "url":      "/hub/brandscreen",
    "icon":     "📺",
    "hub_only": True,
    "version":  "1.2.5",
}

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_CFG_PATH = os.path.join(_BASE_DIR, "brandscreen_cfg.json")
_LOGO_DIR = os.path.join(_BASE_DIR, "brandscreen_logos")
_LOCK     = threading.Lock()
_NOTIFY   = {}              # studio_id → list[queue.Queue]
_NLOCK    = threading.Lock()

os.makedirs(_LOGO_DIR, exist_ok=True)

# ─────────────────────────────────────────────── config helpers ───────────────

def _cfg_load():
    try:
        with open(_CFG_PATH) as f:
            return json.load(f)
    except Exception:
        return {"stations": [], "studios": [], "api_key": ""}

def _cfg_save(cfg):
    with _LOCK:
        with open(_CFG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)

def _get_station(cfg, sid):
    for s in cfg.get("stations", []):
        if s.get("id") == sid:
            return s
    return None

def _get_studio(cfg, sid):
    for s in cfg.get("studios", []):
        if s.get("id") == sid:
            return s
    return None

def _new_station():
    return {
        "id":                 str(uuid.uuid4())[:8],
        "name":               "My Station",
        "enabled":            True,
        "brand_colour":       "#17a8ff",
        "accent_colour":      "#ffffff",
        "bg_style":           "particles",
        "logo_anim":          "orbit",
        "show_clock":         True,
        "show_on_air":        True,
        "show_now_playing":   True,
        "np_source":          "none",
        "np_zetta_key":       "",
        "np_api_url":         "",
        "np_api_title_path":  "now_playing.song.title",
        "np_api_artist_path": "now_playing.song.artist",
        "np_manual":          "",
        "message":            "",
        "level_key":          "",   # "site|stream" for audio reactivity
    }

def _new_studio():
    return {
        "id":         str(uuid.uuid4())[:8],
        "name":       "Studio",
        "station_id": "",
        "token":      str(uuid.uuid4()).replace("-", ""),
    }

def _ensure_api_key(cfg):
    if not cfg.get("api_key"):
        cfg["api_key"] = str(uuid.uuid4()).replace("-", "")
        _cfg_save(cfg)
    return cfg["api_key"]

# ─────────────────────────────── brand palette derivation ────────────────────

def _brand_palette(hex_colour):
    """
    Derive background colours from the brand hue so the whole screen
    feels 'in' the brand colour, not just dark with coloured accents.
    Returns dict with bg_deep, bg_dark, bg_mid as hex strings and
    bg_deep_rgb as comma-separated r,g,b.
    """
    r, g, b = _hex_rgb(hex_colour)
    # Achromatic (white/grey/black) — fall back to neutral dark
    if max(r, g, b) - min(r, g, b) < 12:
        return {
            "bg_deep": "#030509", "bg_dark": "#060c18",
            "bg_mid":  "#0a1328", "bg_deep_rgb": "3,5,9",
        }
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    sat = min(max(s, 0.55), 0.95)

    def _hsv(val):
        rr, gg, bb = colorsys.hsv_to_rgb(h, sat, val)
        return (int(rr * 255), int(gg * 255), int(bb * 255))

    dp = _hsv(0.06)   # very deep — near black but hue-tinted
    dk = _hsv(0.13)   # dark base
    md = _hsv(0.22)   # mid — visible in gradient centre

    def _hex3(t): return f"#{t[0]:02x}{t[1]:02x}{t[2]:02x}"
    return {
        "bg_deep":     _hex3(dp),
        "bg_dark":     _hex3(dk),
        "bg_mid":      _hex3(md),
        "bg_deep_rgb": f"{dp[0]},{dp[1]},{dp[2]}",
    }

# ─────────────────────────────────────── SSE notification ────────────────────

def _notify_studio(studio_id):
    with _NLOCK:
        qs = list(_NOTIFY.get(studio_id, []))
    for q in qs:
        try:
            q.put_nowait("assignment_changed")
        except _queue.Full:
            pass

def _sse_stream(studio_id):
    q = _queue.Queue(maxsize=8)
    with _NLOCK:
        _NOTIFY.setdefault(studio_id, []).append(q)
    try:
        yield "data: connected\n\n"
        while True:
            try:
                msg = q.get(timeout=25)
                yield f"data: {msg}\n\n"
            except _queue.Empty:
                yield ": keepalive\n\n"
    finally:
        with _NLOCK:
            try:
                _NOTIFY[studio_id].remove(q)
            except (ValueError, KeyError):
                pass

# ─────────────────────────────────────── API key auth ────────────────────────

def _make_api_auth():
    def _api_auth(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            if session.get("logged_in"):
                return f(*args, **kwargs)
            auth  = request.headers.get("Authorization", "")
            token = auth.removeprefix("Bearer ").strip() if auth else ""
            cfg   = _cfg_load()
            key   = cfg.get("api_key", "")
            if key and token == key:
                return f(*args, **kwargs)
            return jsonify({"error": "Unauthorized"}), 401
        return wrapper
    return _api_auth

# ─────────────────────────────────────── now-playing resolver ─────────────────

def _resolve_np(station, monitor):
    src = station.get("np_source", "none")
    try:
        if src == "zetta":
            key  = station.get("np_zetta_key", "")
            data = getattr(monitor, "_zetta_live_station_data", lambda: {})()
            np   = (data.get(key) or {}).get("now_playing") or {}
            if np:
                return {
                    "title":   (np.get("raw_title") or np.get("title") or "").strip(),
                    "artist":  (np.get("raw_artist") or "").strip(),
                    "is_spot": int(np.get("asset_type") or 0) == 2,
                }
            return {"title": "", "artist": "", "is_spot": False}
        elif src == "json_api":
            import urllib.request
            url = station.get("np_api_url", "")
            if url:
                r = urllib.request.urlopen(url, timeout=5)
                d = json.loads(r.read())
                def _dig(obj, path):
                    for k in (path or "").split("."):
                        obj = (obj or {}).get(k, "") if isinstance(obj, dict) else ""
                    return str(obj or "")
                return {
                    "title":   _dig(d, station.get("np_api_title_path", "")),
                    "artist":  _dig(d, station.get("np_api_artist_path", "")),
                    "is_spot": False,
                }
        elif src == "manual":
            return {"title": station.get("np_manual", ""), "artist": "", "is_spot": False}
    except Exception:
        pass
    return None

# ─────────────────────────────────────────────── logo helpers ─────────────────

def _logo_file(sid):
    for ext in ("png", "svg", "jpg", "jpeg", "webp", "gif"):
        p = os.path.join(_LOGO_DIR, f"{sid}.{ext}")
        if os.path.exists(p):
            return p, ext
    return None, None

def _hex_rgb(h):
    h = (h or "#000000").lstrip("#")
    return int(h[:2], 16), int(h[2:4], 16), int(h[4:], 16)

# ──────────────────────────────────────────────── admin template ──────────────

_ADMIN_TPL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<title>Brand Screen</title>
<style nonce="{{csp_nonce()}}">
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(circle at top,#12376f 0%,var(--bg) 38%,#05101f 100%);color:var(--tx);font-family:system-ui,sans-serif;font-size:13px;min-height:100vh}
header{background:linear-gradient(180deg,rgba(10,31,65,.96),rgba(9,24,48,.96));border-bottom:1px solid var(--bor);padding:12px 20px;display:flex;align-items:center;gap:12px}
.btn{border:none;border-radius:8px;padding:5px 12px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;text-decoration:none;display:inline-block}
.btn:hover{filter:brightness(1.15)}.bp{background:var(--acc);color:#fff}.bd{background:var(--al);color:#fff}.bg{background:#132040;color:var(--tx)}.bs{font-size:11px;padding:3px 9px}
main{max-width:960px;margin:0 auto;padding:20px 16px}
.tab-nav{display:flex;gap:4px;margin-bottom:20px;border-bottom:1px solid var(--bor)}
.tab-btn{background:none;border:none;color:var(--mu);font-size:13px;font-weight:600;padding:8px 18px;cursor:pointer;border-radius:8px 8px 0 0;border-bottom:2px solid transparent;margin-bottom:-1px;font-family:inherit}
.tab-btn:hover{color:var(--tx)}.tab-btn.active{color:var(--acc);border-bottom-color:var(--acc);background:rgba(23,168,255,.06)}
.tab-panel{display:none}.tab-panel.active{display:block}
.sc{background:var(--sur);border:1px solid var(--bor);border-radius:10px;margin-bottom:10px;overflow:hidden}
.sc-head{display:flex;align-items:center;gap:12px;padding:11px 14px}
.sc-logo{width:80px;height:44px;border-radius:6px;background:#0a1a3a;border:1px solid var(--bor);display:flex;align-items:center;justify-content:center;overflow:hidden;flex-shrink:0}
.sc-logo img{max-width:100%;max-height:100%;object-fit:contain}
.sc-meta{flex:1;min-width:0}.sc-name{font-weight:700;font-size:14px;margin-bottom:2px}.sc-sub{font-size:11px;color:var(--mu)}
.sc-actions{display:flex;gap:6px;align-items:center;flex-shrink:0}
.sc-body{padding:14px;border-top:1px solid var(--bor);display:none}.sc-body.open{display:block}
.field{display:flex;flex-direction:column;gap:4px;margin-bottom:12px}
.field label{font-size:11px;color:var(--mu);font-weight:600;text-transform:uppercase;letter-spacing:.05em}
input[type=text],input[type=url],select{background:#0d1e40;border:1px solid var(--bor);border-radius:6px;color:var(--tx);padding:6px 9px;font-size:13px;font-family:inherit;width:100%}
input[type=text]:focus,input[type=url]:focus,select:focus{border-color:var(--acc);outline:none}
input[type=color]{background:#0d1e40;border:1px solid var(--bor);border-radius:6px;padding:2px 4px;height:32px;cursor:pointer;width:100%}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}
.sep{border:none;border-top:1px solid var(--bor);margin:12px 0}
.slabel{font-size:11px;font-weight:700;color:var(--mu);text-transform:uppercase;letter-spacing:.08em;margin-bottom:10px}
.logo-prev{width:120px;height:60px;border-radius:6px;background:#0a1a3a;border:1px solid var(--bor);display:flex;align-items:center;justify-content:center;overflow:hidden;margin-bottom:8px}
.logo-prev img{max-width:100%;max-height:100%;object-fit:contain}
.row-url{font-family:monospace;font-size:11px;color:var(--acc);word-break:break-all;background:#071428;border:1px solid var(--bor);border-radius:6px;padding:7px 10px;cursor:pointer;margin-top:5px}
.tok-row{display:flex;gap:6px;align-items:center}
.tok-row input{font-family:monospace;font-size:11px;color:var(--mu)}
.msg-box{border-radius:8px;padding:10px 14px;margin-bottom:14px;font-size:12px;display:none}
.msg-ok{background:#0f2318;color:var(--ok);border:1px solid #166534}
.msg-err{background:#2a0a0a;color:var(--al);border:1px solid #991b1b}
.badge{display:inline-flex;align-items:center;padding:2px 8px;border-radius:20px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.06em}
.b-ok{background:rgba(34,197,94,.12);color:var(--ok);border:1px solid rgba(34,197,94,.25)}
.b-mu{background:rgba(138,164,200,.1);color:var(--mu);border:1px solid rgba(138,164,200,.2)}
.cb-row{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.cb-row input[type=checkbox]{width:14px;height:14px;accent-color:var(--acc);flex-shrink:0}
.np-fields{display:none}.np-fields.open{display:block}
.hint{font-size:11px;color:var(--mu);margin-top:4px}
.swatch{width:14px;height:14px;border-radius:3px;flex-shrink:0;border:1px solid rgba(255,255,255,.15)}
.studio-assign{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--mu)}
.empty-state{text-align:center;padding:48px 24px;color:var(--mu)}
pre.api-eg{background:#050e20;border:1px solid var(--bor);border-radius:8px;padding:14px;font-size:12px;color:#8ad;overflow-x:auto;white-space:pre-wrap;line-height:1.6;margin-top:8px}
.api-key-val{font-family:monospace;font-size:12px;background:#050e20;border:1px solid var(--bor);border-radius:6px;padding:8px 12px;color:var(--acc);word-break:break-all}
table{width:100%;border-collapse:collapse}
th{color:var(--mu);font-size:11px;text-transform:uppercase;letter-spacing:.05em;text-align:left;padding:6px 8px;border-bottom:1px solid var(--bor)}
td{padding:6px 8px;font-size:12px;border-bottom:1px solid rgba(23,52,95,.4)}
td code{font-size:11px;background:#050e20;padding:2px 6px;border-radius:4px;color:var(--acc)}
.lev-badge{display:inline-flex;align-items:center;gap:5px;font-size:10px;color:var(--ok);background:rgba(34,197,94,.1);border:1px solid rgba(34,197,94,.2);border-radius:20px;padding:1px 8px;margin-left:6px}
</style>
</head>
<body>
<header>
  <span style="font-size:20px">📺</span>
  <span style="font-weight:700;font-size:15px">Brand Screen</span>
  <span style="color:var(--mu);font-size:12px;margin-left:4px">Studios &amp; stations</span>
</header>
<main>
  <div id="msg" class="msg-box"></div>
  <nav class="tab-nav" id="tab-nav">
    <button class="tab-btn active" data-tab="studios">Studios</button>
    <button class="tab-btn" data-tab="stations">Stations</button>
    <button class="tab-btn" data-tab="api">REST API</button>
  </nav>

  <div class="tab-panel active" id="tp-studios">
    <div style="margin-bottom:14px"><button class="btn bp" id="add-studio-btn">＋ Add Studio</button></div>
    <div id="studio-list"></div>
  </div>

  <div class="tab-panel" id="tp-stations">
    <div style="margin-bottom:14px"><button class="btn bp" id="add-station-btn">＋ Add Station</button></div>
    <div id="station-list"></div>
  </div>

  <div class="tab-panel" id="tp-api">
    <div class="sc"><div class="sc-body open">
      <div class="slabel">API Key</div>
      <div class="api-key-val" id="api-key-display">{{api_key|e}}</div>
      <div style="display:flex;gap:8px;margin-top:10px">
        <button class="btn bg bs" data-action="copy-api-key">Copy</button>
        <button class="btn bd bs" data-action="regen-api-key">Regenerate</button>
      </div>
    </div></div>
    <div class="sc"><div class="sc-body open">
      <div class="slabel">Change assignment</div>
      <pre class="api-eg" id="eg-assign">PUT {{origin}}/api/brandscreen/studio/{studio_id}/station
Authorization: Bearer {{api_key|e}}
Content-Type: application/json

{"station_id": "{station_id}"}
</pre>
      <p class="hint" style="margin-top:6px">The browser display updates instantly via SSE — no reload needed. Send <code>{"station_id":""}</code> to unassign.</p>
      <hr class="sep">
      <div class="slabel" style="margin-bottom:8px">List studios &amp; stations</div>
      <pre class="api-eg">GET {{origin}}/api/brandscreen/studios
Authorization: Bearer {{api_key|e}}</pre>
      <hr class="sep">
      <div class="slabel" style="margin-bottom:8px">Reference</div>
      <table><thead><tr><th>Name</th><th>Type</th><th>ID</th></tr></thead>
      <tbody id="api-ref-body"></tbody></table>
    </div></div>
  </div>
</main>

<input type="file" id="logo-input" accept="image/png,image/svg+xml,image/jpeg,image/webp,image/gif" style="display:none">

<script nonce="{{csp_nonce()}}">
var _studios  = {{studios_json|safe}};
var _stations = {{stations_json|safe}};
var _zetStations = [];
var _streams  = {{streams_json|safe}};
var _currentLogoSid = null;

function _csrf(){ return (document.querySelector('meta[name="csrf-token"]')||{}).content||''; }
function _post(url,data){ return fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':_csrf()},body:JSON.stringify(data)}); }
function _del(url){ return fetch(url,{method:'DELETE',headers:{'X-CSRFToken':_csrf()}}); }
function _msg(txt,ok){
  var el=document.getElementById('msg');
  el.className='msg-box '+(ok?'msg-ok':'msg-err');
  el.textContent=txt; el.style.display='block';
  clearTimeout(el._t); el._t=setTimeout(function(){el.style.display='none';},4500);
}
function _esc(s){var d=document.createElement('div');d.appendChild(document.createTextNode(s||''));return d.innerHTML;}
function _v(id){var el=document.getElementById(id);return el?(el.type==='checkbox'?el.checked:el.value):null;}
function _stById(id){return _stations.find(function(s){return s.id===id;})||null;}
function _sdById(id){return _studios.find(function(s){return s.id===id;})||null;}
var _BG_L={particles:'Particles',aurora:'Aurora',waves:'Waves',minimal:'Minimal'};
var _AN_L={orbit:'Orbit rings',pulse:'Pulse',glow:'Glow',float:'Float',none:'Static'};
var _NP_L={zetta:'Zetta',json_api:'JSON API',manual:'Manual',none:'None'};

document.getElementById('tab-nav').addEventListener('click',function(e){
  var btn=e.target.closest('.tab-btn'); if(!btn) return;
  document.querySelectorAll('.tab-btn').forEach(function(b){b.classList.remove('active');});
  document.querySelectorAll('.tab-panel').forEach(function(p){p.classList.remove('active');});
  btn.classList.add('active');
  document.getElementById('tp-'+btn.dataset.tab).classList.add('active');
});

// ── Studios ───────────────────────────────────────────────────────────────
function renderStudios(){
  var el=document.getElementById('studio-list');
  if(!_studios.length){
    el.innerHTML='<div class="empty-state"><div style="font-size:36px;margin-bottom:10px">🖥️</div>No studios yet.<br><span style="font-size:12px;margin-top:6px;display:block">A studio is a physical display screen. Add one and assign it a station.</span></div>';
    return;
  }
  el.innerHTML=_studios.map(function(sd){
    var st=_stById(sd.station_id||'');
    var assignHtml=st
      ?'<span class="swatch" style="background:'+_esc(st.brand_colour)+'"></span>'+_esc(st.name)
      :'<span style="opacity:.5">— unassigned —</span>';
    return '<div class="sc" id="sd-'+sd.id+'">'
      +'<div class="sc-head">'
      +'<div style="flex:1;min-width:0">'
      +'<div class="sc-name">'+_esc(sd.name)+'</div>'
      +'<div class="studio-assign" style="margin-top:3px">'+assignHtml+'</div>'
      +'</div>'
      +'<div class="sc-actions">'
      +'<button class="btn bg bs" data-action="toggle-sd" data-sid="'+sd.id+'">Edit</button>'
      +'<a class="btn bg bs" href="/brandscreen/studio/'+sd.id+'?token='+sd.token+'" target="_blank">Preview ↗</a>'
      +'<button class="btn bd bs" data-action="del-sd" data-sid="'+sd.id+'">Delete</button>'
      +'</div></div>'
      +'<div class="sc-body" id="sdb-'+sd.id+'">'+_studioForm(sd)+'</div>'
      +'</div>';
  }).join('');
}
function _studioForm(sd){
  var stOpts=_stations.map(function(st){
    return '<option value="'+st.id+'"'+(sd.station_id===st.id?' selected':'')+'>'+_esc(st.name)+'</option>';
  }).join('');
  var screenUrl=location.origin+'/brandscreen/studio/'+sd.id+'?token='+sd.token;
  return '<div class="grid2" style="margin-bottom:12px">'
    +'<div class="field"><label>Studio Name</label><input type="text" id="sd-name-'+sd.id+'" value="'+_esc(sd.name)+'"></div>'
    +'<div class="field"><label>Assigned Station</label><select id="sd-st-'+sd.id+'"><option value="">— none —</option>'+stOpts+'</select></div>'
    +'</div>'
    +'<div class="slabel">Screen URL</div>'
    +'<div class="tok-row" style="margin-bottom:6px"><input type="text" value="'+_esc(sd.token)+'" readonly>'
    +'<button class="btn bg bs" data-action="regen-sd-tok" data-sid="'+sd.id+'">Regenerate</button></div>'
    +'<div class="row-url" id="sd-url-'+sd.id+'" title="Click to copy">'+_esc(screenUrl)+'</div>'
    +'<p class="hint" style="margin-top:5px">Open full-screen on your studio display. Token authenticates automatically.</p>'
    +'<div style="display:flex;gap:8px;margin-top:14px">'
    +'<button class="btn bp" data-action="save-sd" data-sid="'+sd.id+'">Save</button>'
    +'<button class="btn bg" data-action="toggle-sd" data-sid="'+sd.id+'">Cancel</button>'
    +'</div>';
}

// ── Stations ──────────────────────────────────────────────────────────────
function renderStations(){
  var el=document.getElementById('station-list');
  if(!_stations.length){
    el.innerHTML='<div class="empty-state"><div style="font-size:36px;margin-bottom:10px">📡</div>No stations yet.<br><span style="font-size:12px;margin-top:6px;display:block">A station is a brand config (logo, colours, animations). Assign it to one or more studios.</span></div>';
    return;
  }
  el.innerHTML=_stations.map(function(s){
    var lImg=s._has_logo?'<img src="/api/brandscreen/logo/'+s.id+'?t='+Date.now()+'" alt="">':'<span style="font-size:20px;opacity:.25">📺</span>';
    var levBadge=s.level_key?'<span class="lev-badge">⚡ Audio reactive</span>':'';
    return '<div class="sc" id="st-'+s.id+'">'
      +'<div class="sc-head">'
      +'<div class="sc-logo">'+lImg+'</div>'
      +'<div class="sc-meta">'
      +'<div class="sc-name" style="display:flex;align-items:center;gap:8px">'
      +'<span class="swatch" style="background:'+_esc(s.brand_colour)+'"></span>'+_esc(s.name)+levBadge+'</div>'
      +'<div class="sc-sub">'+(_BG_L[s.bg_style]||s.bg_style)+' · '+(_AN_L[s.logo_anim]||s.logo_anim)+' · NP: '+(_NP_L[s.np_source]||s.np_source)+'</div>'
      +'</div>'
      +'<div class="sc-actions">'
      +'<span class="badge '+(s.enabled?'b-ok':'b-mu')+'">'+(s.enabled?'On':'Off')+'</span>'
      +'<button class="btn bg bs" data-action="toggle-st" data-sid="'+s.id+'">Edit</button>'
      +'<button class="btn bd bs" data-action="del-st" data-sid="'+s.id+'">Delete</button>'
      +'</div></div>'
      +'<div class="sc-body" id="stb-'+s.id+'">'+_stationForm(s)+'</div>'
      +'</div>';
  }).join('');
}

function _stationForm(s){
  var zetOpts=_zetStations.map(function(z){
    return '<option value="'+_esc(z.key)+'"'+(s.np_zetta_key===z.key?' selected':'')+'>'+_esc(z.name)+'</option>';
  }).join('');
  var streamOpts=_streams.map(function(st){
    return '<option value="'+_esc(st.key)+'"'+(s.level_key===st.key?' selected':'')+'>'+_esc(st.label)+'</option>';
  }).join('');
  return '<div class="grid2">'
    +'<div class="field"><label>Station Name</label><input type="text" id="f-name-'+s.id+'" value="'+_esc(s.name)+'"></div>'
    +'<div class="field" style="justify-content:flex-end"><label style="margin-bottom:6px">Status</label>'
    +'<label style="display:flex;align-items:center;gap:8px;cursor:pointer">'
    +'<input type="checkbox" id="f-en-'+s.id+'"'+(s.enabled?' checked':'')+' style="accent-color:var(--acc);width:16px;height:16px">'
    +'<span>Enabled</span></label></div></div>'
    +'<div class="grid2">'
    +'<div class="field"><label>Brand Colour</label><input type="color" id="f-brand-'+s.id+'" value="'+_esc(s.brand_colour)+'"></div>'
    +'<div class="field"><label>Accent Colour</label><input type="color" id="f-accent-'+s.id+'" value="'+_esc(s.accent_colour)+'"></div>'
    +'</div>'
    +'<hr class="sep">'
    +'<div class="slabel">Animation</div>'
    +'<div class="grid2">'
    +'<div class="field"><label>Background</label><select id="f-bg-'+s.id+'">'
    +'<option value="particles"'+(s.bg_style==='particles'?' selected':'')+'>✦ Particles</option>'
    +'<option value="aurora"'+(s.bg_style==='aurora'?' selected':'')+'>◎ Aurora</option>'
    +'<option value="waves"'+(s.bg_style==='waves'?' selected':'')+'>⌇ Waves</option>'
    +'<option value="minimal"'+(s.bg_style==='minimal'?' selected':'')+'>▪ Minimal</option>'
    +'</select></div>'
    +'<div class="field"><label>Logo Animation</label><select id="f-anim-'+s.id+'">'
    +'<option value="orbit"'+(s.logo_anim==='orbit'?' selected':'')+'>⊙ Orbit rings</option>'
    +'<option value="pulse"'+(s.logo_anim==='pulse'?' selected':'')+'>◉ Pulse</option>'
    +'<option value="glow"'+(s.logo_anim==='glow'?' selected':'')+'>✦ Glow</option>'
    +'<option value="float"'+(s.logo_anim==='float'?' selected':'')+'>↕ Float</option>'
    +'<option value="none"'+(s.logo_anim==='none'?' selected':'')+'>— Static</option>'
    +'</select></div></div>'
    +'<div class="grid3" style="margin-bottom:8px">'
    +'<div class="cb-row"><input type="checkbox" id="f-clk-'+s.id+'"'+(s.show_clock?' checked':'')+'><label for="f-clk-'+s.id+'">Clock</label></div>'
    +'<div class="cb-row"><input type="checkbox" id="f-oair-'+s.id+'"'+(s.show_on_air?' checked':'')+'><label for="f-oair-'+s.id+'">On Air Badge</label></div>'
    +'<div class="cb-row"><input type="checkbox" id="f-np-'+s.id+'"'+(s.show_now_playing?' checked':'')+'><label for="f-np-'+s.id+'">Now Playing</label></div>'
    +'</div>'
    +'<hr class="sep">'
    +'<div class="slabel">Audio Level <span style="font-weight:400;font-size:10px;text-transform:none;letter-spacing:0">— drives orbit speed, glow, pulse rate &amp; particle intensity</span></div>'
    +'<div class="field"><label>Input Stream</label>'
    +'<select id="f-lvkey-'+s.id+'">'
    +'<option value="">— none (static animations) —</option>'
    +streamOpts
    +'</select>'
    +(streamOpts?'':'<p class="hint">No streams detected from hub — check that sites are connected and sending heartbeats.</p>')
    +'</div>'
    +'<hr class="sep">'
    +'<div class="slabel">Logo</div>'
    +'<div class="logo-prev" id="lp-'+s.id+'">'+(s._has_logo?'<img src="/api/brandscreen/logo/'+s.id+'?t='+Date.now()+'" alt="">':'<span style="font-size:28px;opacity:.2">📺</span>')+'</div>'
    +'<div style="display:flex;gap:8px;align-items:center">'
    +'<button class="btn bg bs" data-action="upload-logo" data-sid="'+s.id+'">Upload Logo</button>'
    +(s._has_logo?'<button class="btn bd bs" data-action="del-logo" data-sid="'+s.id+'">Remove</button>':'')
    +'</div>'
    +'<p class="hint" style="margin-top:5px">PNG with transparent background recommended. SVG, JPG, WebP also supported.</p>'
    +'<hr class="sep">'
    +'<div class="slabel">Now Playing Source</div>'
    +'<div class="field"><label>Source</label>'
    +'<select id="f-npsrc-'+s.id+'" data-np-sel="'+s.id+'">'
    +'<option value="none"'+(s.np_source==='none'?' selected':'')+'>None</option>'
    +'<option value="zetta"'+(s.np_source==='zetta'?' selected':'')+'>Zetta</option>'
    +'<option value="json_api"'+(s.np_source==='json_api'?' selected':'')+'>JSON API</option>'
    +'<option value="manual"'+(s.np_source==='manual'?' selected':'')+'>Manual text</option>'
    +'</select></div>'
    +'<div class="np-fields'+(s.np_source==='zetta'?' open':'')+'" id="npf-zetta-'+s.id+'">'
    +'<div class="field"><label>Zetta Station</label><select id="f-zpkey-'+s.id+'"><option value="">— choose —</option>'+zetOpts+'</select>'
    +(zetOpts?'':'<p class="hint">No Zetta stations found.</p>')+'</div></div>'
    +'<div class="np-fields'+(s.np_source==='json_api'?' open':'')+'" id="npf-json-'+s.id+'">'
    +'<div class="field"><label>API URL</label><input type="url" id="f-npurl-'+s.id+'" value="'+_esc(s.np_api_url)+'"></div>'
    +'<div class="grid2"><div class="field"><label>Title path</label><input type="text" id="f-nptpath-'+s.id+'" value="'+_esc(s.np_api_title_path)+'"></div>'
    +'<div class="field"><label>Artist path</label><input type="text" id="f-npapath-'+s.id+'" value="'+_esc(s.np_api_artist_path)+'"></div></div>'
    +'<p class="hint">Dot notation, e.g. <code>now_playing.song.title</code></p></div>'
    +'<div class="np-fields'+(s.np_source==='manual'?' open':'')+'" id="npf-manual-'+s.id+'">'
    +'<div class="field"><label>Display text</label><input type="text" id="f-npman-'+s.id+'" value="'+_esc(s.np_manual)+'"></div></div>'
    +'<hr class="sep">'
    +'<div class="slabel">Live Message</div>'
    +'<div class="field"><label>Message (amber banner — blank to clear)</label>'
    +'<input type="text" id="f-msg-'+s.id+'" value="'+_esc(s.message)+'" maxlength="200"></div>'
    +'<div style="display:flex;gap:8px;margin-top:14px">'
    +'<button class="btn bp" data-action="save-st" data-sid="'+s.id+'">Save</button>'
    +'<button class="btn bg" data-action="toggle-st" data-sid="'+s.id+'">Cancel</button>'
    +'</div>';
}

function renderApiRef(){
  var rows='';
  _studios.forEach(function(sd){rows+='<tr><td>'+_esc(sd.name)+'</td><td style="color:var(--wn)">Studio</td><td><code>'+_esc(sd.id)+'</code></td></tr>';});
  _stations.forEach(function(st){rows+='<tr><td>'+_esc(st.name)+'</td><td style="color:var(--acc)">Station</td><td><code>'+_esc(st.id)+'</code></td></tr>';});
  document.getElementById('api-ref-body').innerHTML=rows;
}

// ── Actions ───────────────────────────────────────────────────────────────
function _addStudio(){
  _post('/api/brandscreen/studio',{}).then(function(r){return r.json();}).then(function(d){
    if(d.error){_msg(d.error,false);return;}
    _studios.push(d.studio); renderStudios(); renderApiRef();
    document.getElementById('sdb-'+d.studio.id).classList.add('open');
    _msg('Studio created.',true);
  });
}
function _saveSd(sid){
  var sd=_sdById(sid); if(!sd) return;
  _post('/api/brandscreen/studio/'+sid,{name:_v('sd-name-'+sid)||sd.name,station_id:_v('sd-st-'+sid)||''})
    .then(function(r){return r.json();}).then(function(d){
      if(d.error){_msg(d.error,false);return;}
      Object.assign(sd,d.studio); renderStudios(); renderApiRef(); _msg('Saved.',true);
    });
}
function _delSd(sid){
  if(!confirm('Delete studio?')) return;
  _del('/api/brandscreen/studio/'+sid).then(function(r){return r.json();}).then(function(d){
    if(d.error){_msg(d.error,false);return;}
    _studios=_studios.filter(function(s){return s.id!==sid;}); renderStudios(); renderApiRef(); _msg('Deleted.',true);
  });
}
function _regenSdTok(sid){
  if(!confirm('Regenerate token? The current URL will stop working.')) return;
  _post('/api/brandscreen/studio/'+sid+'/regen_token',{}).then(function(r){return r.json();}).then(function(d){
    if(d.error){_msg(d.error,false);return;}
    var sd=_sdById(sid); if(sd) sd.token=d.token;
    renderStudios(); document.getElementById('sdb-'+sid).classList.add('open'); _msg('Token regenerated.',true);
  });
}
function _addStation(){
  _post('/api/brandscreen/station',{}).then(function(r){return r.json();}).then(function(d){
    if(d.error){_msg(d.error,false);return;}
    _stations.push(d.station); renderStations(); renderApiRef();
    document.getElementById('stb-'+d.station.id).classList.add('open'); _msg('Station created.',true);
  });
}
function _saveSt(sid){
  var s=_stById(sid); if(!s) return;
  var data={
    name:_v('f-name-'+sid)||s.name, enabled:!!_v('f-en-'+sid),
    brand_colour:_v('f-brand-'+sid)||s.brand_colour, accent_colour:_v('f-accent-'+sid)||s.accent_colour,
    bg_style:_v('f-bg-'+sid)||s.bg_style, logo_anim:_v('f-anim-'+sid)||s.logo_anim,
    show_clock:!!_v('f-clk-'+sid), show_on_air:!!_v('f-oair-'+sid), show_now_playing:!!_v('f-np-'+sid),
    level_key:_v('f-lvkey-'+sid)||'',
    np_source:_v('f-npsrc-'+sid)||'none', np_zetta_key:_v('f-zpkey-'+sid)||'',
    np_api_url:_v('f-npurl-'+sid)||'', np_api_title_path:_v('f-nptpath-'+sid)||'',
    np_api_artist_path:_v('f-npapath-'+sid)||'', np_manual:_v('f-npman-'+sid)||'',
    message:_v('f-msg-'+sid)||'',
  };
  _post('/api/brandscreen/station/'+sid, data).then(function(r){return r.json();}).then(function(d){
    if(d.error){_msg(d.error,false);return;}
    Object.assign(s,d.station); renderStations(); renderStudios(); _msg('Saved.',true);
  });
}
function _delSt(sid){
  if(!confirm('Delete this station? Studios assigned to it will become unassigned.')) return;
  _del('/api/brandscreen/station/'+sid).then(function(r){return r.json();}).then(function(d){
    if(d.error){_msg(d.error,false);return;}
    _stations=_stations.filter(function(s){return s.id!==sid;});
    _studios.forEach(function(sd){if(sd.station_id===sid)sd.station_id='';});
    renderStations(); renderStudios(); renderApiRef(); _msg('Deleted.',true);
  });
}
function _uploadLogo(sid){ _currentLogoSid=sid; document.getElementById('logo-input').value=''; document.getElementById('logo-input').click(); }
function _doUpload(file){
  if(!file||!_currentLogoSid) return;
  var sid=_currentLogoSid;
  var fd=new FormData(); fd.append('logo',file);
  fetch('/api/brandscreen/logo/'+sid,{method:'POST',headers:{'X-CSRFToken':_csrf()},body:fd})
    .then(function(r){return r.json();}).then(function(d){
      if(d.error){_msg(d.error,false);return;}
      var s=_stById(sid); if(s) s._has_logo=true; renderStations(); _msg('Logo uploaded.',true);
    });
}
function _delLogo(sid){
  if(!confirm('Remove logo?')) return;
  _del('/api/brandscreen/logo/'+sid).then(function(r){return r.json();}).then(function(d){
    if(d.error){_msg(d.error,false);return;}
    var s=_stById(sid); if(s) s._has_logo=false; renderStations(); _msg('Logo removed.',true);
  });
}
function _npSrcChanged(sid){
  var src=_v('f-npsrc-'+sid);
  ['zetta','json_api','manual'].forEach(function(k){
    var el=document.getElementById('npf-'+k+'-'+sid);
    if(el) el.className='np-fields'+(src===k?' open':'');
  });
}
function _copyApiKey(){
  var val=document.getElementById('api-key-display').textContent;
  navigator.clipboard&&navigator.clipboard.writeText(val.trim()); _msg('Copied.',true);
}
function _regenApiKey(){
  if(!confirm('Regenerate? All integrations using the current key will break.')) return;
  _post('/api/brandscreen/regen_api_key',{}).then(function(r){return r.json();}).then(function(d){
    if(d.error){_msg(d.error,false);return;}
    document.getElementById('api-key-display').textContent=d.api_key;
    var eg=document.getElementById('eg-assign');
    eg.textContent=eg.textContent.replace(/Bearer \S+/,'Bearer '+d.api_key); _msg('Regenerated.',true);
  });
}

document.addEventListener('click',function(e){
  var btn=e.target.closest('[data-action]'); if(!btn) return;
  var a=btn.dataset.action, sid=btn.dataset.sid;
  if(a==='toggle-sd'){ var el=document.getElementById('sdb-'+sid); if(el)el.classList.toggle('open'); }
  else if(a==='save-sd') _saveSd(sid);
  else if(a==='del-sd')  _delSd(sid);
  else if(a==='regen-sd-tok') _regenSdTok(sid);
  else if(a==='toggle-st'){ var el2=document.getElementById('stb-'+sid); if(el2)el2.classList.toggle('open'); }
  else if(a==='save-st') _saveSt(sid);
  else if(a==='del-st')  _delSt(sid);
  else if(a==='upload-logo') _uploadLogo(sid);
  else if(a==='del-logo')    _delLogo(sid);
  else if(a==='copy-api-key')  _copyApiKey();
  else if(a==='regen-api-key') _regenApiKey();
  var url=e.target.closest('.row-url');
  if(url){ navigator.clipboard&&navigator.clipboard.writeText(url.textContent.trim()); _msg('URL copied.',true); }
});
document.getElementById('add-studio-btn').addEventListener('click',_addStudio);
document.getElementById('add-station-btn').addEventListener('click',_addStation);
document.getElementById('logo-input').addEventListener('change',function(){ _doUpload(this.files[0]); });
document.addEventListener('change',function(e){
  if(e.target.dataset.npSel) _npSrcChanged(e.target.dataset.npSel);
});

// Fetch Zetta stations from status_full (same source as studioboard)
fetch('/api/zetta/status_full',{credentials:'same-origin'})
  .then(function(r){return r.ok?r.json():{};}).catch(function(){return{};})
  .then(function(d){
    _zetStations=[];
    ((d.instances)||[]).forEach(function(inst){
      Object.keys(inst.stations||{}).forEach(function(sid){
        var stn=inst.stations[sid];
        _zetStations.push({key:inst.id+':'+sid,
          name:(inst.name||inst.id)+' / '+(stn.station_name||stn.name||sid)});
      });
    });
    _zetStations.sort(function(a,b){return a.name<b.name?-1:1;});
    renderStudios(); renderStations(); renderApiRef();
  });
</script>
</body>
</html>"""

# ──────────────────────────────────────────────── screen template ─────────────

_SCREEN_TPL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{sname|e}}</title>
<style nonce="{{csp_nonce()}}">
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:100%;height:100%;overflow:hidden;font-family:system-ui,sans-serif;color:#fff}

/* ── Brand-derived base colour ──────────────────────────────────────────── */
/* bg_deep / bg_mid are computed from the brand colour's hue in Python,
   so the whole environment feels 'in' the brand colour, not just dark */
body{background:{{bg_deep}}}
:root{
  --brand:{{brand|e}};--brand-rgb:{{brand_rgb|e}};--accent:{{accent|e}};
  --bg-deep:{{bg_deep|e}};--bg-dark:{{bg_dark|e}};--bg-mid:{{bg_mid|e}};
}

/* ── Backgrounds ─────────────────────────────────────────────────────────── */
canvas#cv{position:fixed;inset:0;width:100%;height:100%;z-index:0;display:none}

/* Particles: deep brand-hued radial so particles glow against a coloured sky */
.bg-particles-base{position:fixed;inset:0;z-index:0;
  background:radial-gradient(circle at 50% 42%,var(--bg-mid) 0%,var(--bg-dark) 45%,var(--bg-deep) 100%)}

/* Aurora: vivid radial blooms at high opacity */
.bg-aurora{position:fixed;inset:0;z-index:0;background:var(--bg-dark);
  animation:aurora-drift 16s ease-in-out infinite alternate}
.bg-aurora::before{content:'';position:fixed;inset:0;z-index:0;
  background:
    radial-gradient(ellipse 75% 65% at 18% 22%,rgba(var(--brand-rgb),.58) 0%,transparent 62%),
    radial-gradient(ellipse 65% 58% at 82% 78%,rgba(var(--brand-rgb),.48) 0%,transparent 62%),
    radial-gradient(ellipse 90% 75% at 50% 50%,rgba(var(--brand-rgb),.28) 0%,transparent 68%)}
@keyframes aurora-drift{
  0%  {filter:hue-rotate(0deg)   brightness(1)}
  100%{filter:hue-rotate(12deg)  brightness(1.08)}}

/* Waves */
.bg-waves{position:fixed;inset:0;z-index:0;
  background:linear-gradient(180deg,var(--bg-mid) 0%,var(--bg-dark) 100%)}
.wave-wrap{position:fixed;bottom:0;left:0;width:100%;overflow:hidden;z-index:1;pointer-events:none}
.wave-wrap svg{display:block;width:200%}
.wave-wrap svg.w1{animation:wave-slide 9s  linear infinite}
.wave-wrap svg.w2{animation:wave-slide 13s linear infinite reverse;opacity:.65}
@keyframes wave-slide{0%{transform:translateX(0)}100%{transform:translateX(-50%)}}

/* Minimal */
.bg-minimal{position:fixed;inset:0;z-index:0;
  background:radial-gradient(circle at 50% 40%,var(--bg-mid) 0%,var(--bg-dark) 55%,var(--bg-deep) 100%)}

/* ── Screen layout ───────────────────────────────────────────────────────── */
#screen{position:relative;z-index:2;width:100%;height:100vh;display:flex;flex-direction:column;
  animation:px-drift 90s step-end infinite;transition:opacity .55s ease}
@keyframes px-drift{0%,100%{transform:translate(0,0)}25%{transform:translate(1px,0)}50%{transform:translate(1px,1px)}75%{transform:translate(0,1px)}}
#screen.fade-out{opacity:0}

/* ── Top bar ─────────────────────────────────────────────────────────────── */
#top-bar{display:flex;align-items:flex-start;justify-content:space-between;padding:22px 32px 0;flex-shrink:0}
#on-air{display:none;align-items:center;gap:8px;
  background:rgba(239,68,68,.14);border:1.5px solid rgba(239,68,68,.5);border-radius:8px;
  padding:6px 18px;font-size:12px;font-weight:800;letter-spacing:.18em;text-transform:uppercase;color:#ef4444}
#on-air.vis{display:flex}
.oa-dot{width:8px;height:8px;border-radius:50%;background:#ef4444;animation:oab 1.2s ease-in-out infinite}
@keyframes oab{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.22;transform:scale(.65)}}
#clock-wrap{text-align:right;display:none}
#clock-wrap.vis{display:block}
#clock-time{font-size:30px;font-weight:200;letter-spacing:.08em;font-variant-numeric:tabular-nums;color:rgba(255,255,255,.92)}
#clock-date{font-size:11px;color:rgba(255,255,255,.35);letter-spacing:.07em;text-transform:uppercase;margin-top:2px}

/* ── Centre / logo zone ──────────────────────────────────────────────────── */
#centre{flex:1;display:flex;align-items:center;justify-content:center;position:relative;overflow:visible}

/* Audio-reactive centre bloom — always present, driven by JS */
#lev-bloom{position:absolute;width:80vw;height:80vw;border-radius:50%;pointer-events:none;z-index:3;
  background:radial-gradient(circle,rgba(var(--brand-rgb),1) 0%,rgba(var(--brand-rgb),.7) 18%,transparent 68%);
  filter:blur(32px);transform:scale(0.05);opacity:0.3;will-change:transform,opacity}
/* Full-screen level-reactive background brightening */
#bg-pulse{position:fixed;inset:0;z-index:1;pointer-events:none;
  background:radial-gradient(ellipse 90% 80% at 50% 48%,rgba(var(--brand-rgb),.18) 0%,transparent 72%);
  opacity:0;will-change:opacity}

.logo-zone{position:relative;display:flex;align-items:center;justify-content:center;z-index:10}
/* Logo fills the stage — large is the point */
#logo-img{
  width:68vw;max-width:1100px;
  max-height:52vh;
  object-fit:contain;
  display:block;
  filter:drop-shadow(0 0 40px rgba(var(--brand-rgb),.25));
  transition:transform .09s ease-out,filter .09s ease-out;
  will-change:transform,filter}
#logo-ph{font-size:clamp(60px,12vw,160px);opacity:.1;z-index:10}

/* ── Orbit rings — scaled in vw so they wrap the large logo ─────────────── */
.orbit-wrap{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;pointer-events:none;z-index:6;overflow:visible}
.orb{position:absolute;border-radius:50%;border:1.5px solid var(--brand)}
/* Ring 1: outer — solid, clockwise */
.orb1{width:84vw;height:34vw;opacity:.52;animation:orb-s1 10s linear infinite}
.orb1::before{content:'';position:absolute;width:clamp(8px,1.1vw,18px);height:clamp(8px,1.1vw,18px);
  border-radius:50%;background:var(--brand);
  box-shadow:0 0 clamp(10px,2vw,28px) clamp(4px,.8vw,12px) var(--brand);
  top:calc(-1 * clamp(4px,.55vw,9px));left:calc(50% - clamp(4px,.55vw,9px))}
/* Ring 2: inner — dashed, counter-clockwise */
.orb2{width:63vw;height:26vw;opacity:.3;border-style:dashed;animation:orb-s2 16s linear infinite reverse}
.orb2::before{content:'';position:absolute;width:clamp(6px,.9vw,14px);height:clamp(6px,.9vw,14px);
  border-radius:50%;background:var(--brand);
  box-shadow:0 0 clamp(8px,1.5vw,20px) clamp(3px,.6vw,8px) var(--brand);
  bottom:calc(-1 * clamp(3px,.45vw,7px));left:calc(50% - clamp(3px,.45vw,7px))}
@keyframes orb-s1{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}
@keyframes orb-s2{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}

/* ── Pulse rings ─────────────────────────────────────────────────────────── */
.pulse-wrap{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;pointer-events:none;z-index:6;overflow:visible}
.prng{position:absolute;width:48vw;height:48vw;border-radius:50%;
  border:2px solid var(--brand);opacity:0;animation:pulse-out 3.2s ease-out infinite}
.prng:nth-child(2){animation-delay:1.07s}.prng:nth-child(3){animation-delay:2.13s}
@keyframes pulse-out{0%{transform:scale(.45);opacity:.8}100%{transform:scale(1.8);opacity:0}}

/* ── Logo animations ─────────────────────────────────────────────────────── */
.la-float .logo-zone{animation:lo-float 4s ease-in-out infinite}
@keyframes lo-float{0%,100%{transform:translateY(0)}50%{transform:translateY(-18px)}}
/* Glow: base drop-shadow always present via logo-img filter; JS intensifies it via --glow-size */
.la-glow #logo-img{transition:filter .1s linear}

/* ── Now-playing lower third ─────────────────────────────────────────────── */
#lower{flex-shrink:0;padding:0 48px 30px;display:none;flex-direction:column;align-items:center;text-align:center}
#lower.vis{display:flex}
#np-line{width:56px;height:1.5px;background:linear-gradient(90deg,transparent,var(--brand),transparent);margin-bottom:10px}
#np-label{font-size:9px;font-weight:700;letter-spacing:.22em;color:var(--brand);text-transform:uppercase;margin-bottom:10px}
#np-title{font-size:22px;font-weight:300;letter-spacing:.02em;color:rgba(255,255,255,.95);margin-bottom:5px;max-width:82vw;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#np-artist{font-size:14px;color:rgba(255,255,255,.42);max-width:60vw;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#np-spot{display:none;margin-top:10px;padding:3px 16px;border-radius:20px;background:rgba(245,158,11,.13);color:#f59e0b;border:1px solid rgba(245,158,11,.35);font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase}
#np-spot.vis{display:inline-block}

/* ── Message banner ──────────────────────────────────────────────────────── */
#msg-bar{flex-shrink:0;display:none;align-items:center;justify-content:center;gap:10px;background:rgba(245,158,11,.92);padding:11px 28px;font-size:15px;font-weight:600;color:#1a0900;animation:msg-flash 2.5s ease-in-out infinite}
#msg-bar.vis{display:flex}
@keyframes msg-flash{0%,100%{opacity:1}50%{opacity:.8}}

/* ── Waiting (no station) ────────────────────────────────────────────────── */
#waiting{position:fixed;inset:0;display:none;align-items:center;justify-content:center;flex-direction:column;gap:16px;color:rgba(255,255,255,.3);z-index:20}
#waiting.vis{display:flex}
</style>
</head>

<body>
{% if bg_style == 'particles' %}
<div class="bg-particles-base"></div>
<canvas id="cv"></canvas>
{% elif bg_style == 'aurora' %}
<div class="bg-aurora"></div>
{% elif bg_style == 'waves' %}
<div class="bg-waves"></div>
<div class="wave-wrap">
  <svg class="w1" viewBox="0 0 1440 110" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="none">
    <path d="M0,55 C240,95 480,15 720,55 C960,95 1200,15 1440,55 L1440,110 L0,110Z" fill="rgba({{brand_rgb}},0.38)"/>
    <path d="M0,70 C360,30 720,90 1080,55 C1260,38 1380,65 1440,60 L1440,110 L0,110Z" fill="rgba({{brand_rgb}},0.22)"/>
  </svg>
  <svg class="w2" viewBox="0 0 1440 80" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="none">
    <path d="M0,40 C180,72 540,10 720,40 C900,70 1260,10 1440,40 L1440,80 L0,80Z" fill="rgba({{brand_rgb}},0.28)"/>
  </svg>
</div>
{% else %}
<div class="bg-minimal"></div>
{% endif %}

<div id="screen">
  <div id="top-bar">
    <div id="on-air"><div class="oa-dot"></div>ON AIR</div>
    <div id="clock-wrap"><div id="clock-time">--:--:--</div><div id="clock-date"></div></div>
  </div>

  <div id="bg-pulse"></div>
  <div id="centre">
    <div id="lev-bloom"></div>
    {% if logo_anim == 'orbit' %}
    <div class="orbit-wrap"><div class="orb orb1"></div><div class="orb orb2"></div></div>
    {% elif logo_anim == 'pulse' %}
    <div class="pulse-wrap"><div class="prng"></div><div class="prng"></div><div class="prng"></div></div>
    {% endif %}
    <div class="logo-zone">
      {% if has_logo %}<img id="logo-img" src="/api/brandscreen/logo/{{station_id|e}}" alt="{{sname|e}}">
      {% elif station_id %}<div id="logo-ph">📺</div>
      {% endif %}
    </div>
  </div>

  <div id="lower">
    <div id="np-line"></div>
    <div id="np-label">Now Playing</div>
    <div id="np-title">—</div>
    <div id="np-artist"></div>
    <div id="np-spot">Ad Break</div>
  </div>
  <div id="msg-bar"><span>📢</span><span id="msg-txt"></span></div>
</div>
<div id="waiting">
  <div style="font-size:40px">🖥️</div>
  <div style="font-size:16px">{{studio_name|e}}</div>
  <div style="font-size:13px">Waiting for station assignment…</div>
</div>

<script nonce="{{csp_nonce()}}">
var _studioId  = '{{studio_id|e}}';
var _stationId = '{{station_id|e}}';
var _bgStyle   = '{{bg_style|e}}';
var _brandRgb  = '{{brand_rgb|e}}';
var _logoAnim  = '{{logo_anim|e}}';
var _showClock = {{show_clock|lower}};
var _showOair  = {{show_on_air|lower}};
var _showNP    = {{show_now_playing|lower}};
var _levelKey  = '{{level_key|e}}';   // "site|stream" or ""
var _hasStation= !!_stationId;

if(!_hasStation){ document.getElementById('waiting').classList.add('vis'); }

// ── Clock ───────────────────────────────────────────────────────────────────
if(_showClock && _hasStation){
  document.getElementById('clock-wrap').classList.add('vis');
  var _D=['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
  var _M=['January','February','March','April','May','June','July','August','September','October','November','December'];
  function _tick(){
    var n=new Date();
    document.getElementById('clock-time').textContent=
      String(n.getHours()).padStart(2,'0')+':'+String(n.getMinutes()).padStart(2,'0')+':'+String(n.getSeconds()).padStart(2,'0');
    document.getElementById('clock-date').textContent=_D[n.getDay()]+' '+n.getDate()+' '+_M[n.getMonth()]+' '+n.getFullYear();
  }
  _tick(); setInterval(_tick, 1000);
}

// ── Particle canvas ─────────────────────────────────────────────────────────
if(_bgStyle==='particles' && _hasStation){
  var cv=document.getElementById('cv');
  var ctx=cv.getContext('2d');
  var _rgb=_brandRgb.split(',').map(Number);
  function _resize(){cv.width=window.innerWidth;cv.height=window.innerHeight;}
  _resize(); window.addEventListener('resize',_resize);
  cv.style.display='block';
  var pts=[];
  function _pt(){
    return{x:Math.random()*cv.width,y:cv.height+Math.random()*140,
      r:1.2+Math.random()*3.2,sp:0.3+Math.random()*0.7,
      dr:(Math.random()-.5)*0.3,al:0.3+Math.random()*0.55};
  }
  for(var i=0;i<100;i++){var p=_pt();if(i<55)p.y=Math.random()*cv.height;pts.push(p);}
  var _speedMult=1;
  function _drawPts(){
    ctx.clearRect(0,0,cv.width,cv.height);
    pts.forEach(function(p){
      p.y-=p.sp*_speedMult; p.x+=p.dr;
      if(p.y<-10||p.x<-10||p.x>cv.width+10) Object.assign(p,_pt());
      ctx.beginPath(); ctx.arc(p.x,p.y,p.r,0,Math.PI*2);
      ctx.fillStyle='rgba('+_rgb[0]+','+_rgb[1]+','+_rgb[2]+','+p.al+')'; ctx.fill();
    });
    requestAnimationFrame(_drawPts);
  }
  _drawPts();
}

// ── Audio-level reactive animations ─────────────────────────────────────────
// _lev     — slow smooth tracker (background, gradual shifts)
// _levSnap — fast snap tracker (beat-responsive elements)
var _lev     = 0;
var _levSnap = 0;
var _bloom   = document.getElementById('lev-bloom');
var _bgPulse = document.getElementById('bg-pulse');
var _orb1    = document.querySelector('.orb1');
var _orb2    = document.querySelector('.orb2');
var _prngs   = document.querySelectorAll('.prng');
var _logoImg = document.getElementById('logo-img');

function _applyLevel(raw){
  // Slow tracker: fast attack / very slow decay — holds the "energy floor"
  _lev     = _lev     * (raw > _lev     ? 0.55 : 0.18) + raw * (raw > _lev     ? 0.45 : 0.82) * 0;
  _lev     = _lev     + (raw - _lev)     * (raw > _lev     ? 0.45 : 0.18);
  // Fast snap tracker: responds to every beat and dip quickly
  _levSnap = _levSnap + (raw - _levSnap) * (raw > _levSnap ? 0.65 : 0.38);

  // ── Bloom: large blurred glow behind logo — most visible effect
  // Scale 0.1 (silence) → 3.2 (peak); bright inner core always shows
  var bScale = 0.12 + _levSnap * 3.1;
  var bOp    = Math.min(0.95, 0.28 + _levSnap * 0.70);
  _bloom.style.transform = 'scale(' + bScale.toFixed(3) + ')';
  _bloom.style.opacity   = bOp.toFixed(3);

  // ── Background screen brightening (full-screen radial wash)
  if(_bgPulse) _bgPulse.style.opacity = (_levSnap * 0.85).toFixed(3);

  // ── Logo scale pulse — most perceptible on a big image
  if(_logoImg){
    var logoScale = 1.0 + _levSnap * 0.09;
    var glowPx  = Math.round(30 + _levSnap * 200);
    var glowOp  = Math.min(1, 0.2 + _levSnap * 0.85);
    var glowOp2 = Math.min(0.7, _levSnap * 0.6);
    _logoImg.style.transform = 'scale(' + logoScale.toFixed(4) + ')';
    _logoImg.style.filter =
      'drop-shadow(0 0 ' + glowPx + 'px rgba(' + _brandRgb + ',' + glowOp.toFixed(2) + '))' +
      ' drop-shadow(0 0 ' + Math.round(glowPx * 2.5) + 'px rgba(' + _brandRgb + ',' + glowOp2.toFixed(2) + '))';
  }

  // ── Orbit: dramatically wider speed range (15s silence → 1.2s peak)
  if(_orb1){ _orb1.style.animationDuration = Math.max(1.2, 15 - _levSnap * 13.8).toFixed(2) + 's'; }
  if(_orb2){ _orb2.style.animationDuration = Math.max(2.0, 22 - _levSnap * 20).toFixed(2)   + 's'; }

  // ── Pulse rings: wide range (3.5s → 0.25s)
  if(_prngs.length){
    var pd = Math.max(0.25, 3.5 - _levSnap * 3.25).toFixed(3) + 's';
    _prngs.forEach(function(p){ p.style.animationDuration = pd; });
  }

  // ── Particle speed (particles background)
  if(_bgStyle==='particles') _speedMult = 1 + _levSnap * 8;
}

// Poll live_levels at 150 ms when a stream is configured
if(_levelKey && _hasStation){
  var _levErr = 0;
  function _pollLevel(){
    fetch('/api/hub/live_levels',{credentials:'same-origin'})
      .then(function(r){return r.json();})
      .then(function(d){
        _levErr = 0;
        var e = d[_levelKey];
        if(e && e.level_dbfs != null){
          // Map -60 dBFS→0.0,  0 dBFS→1.0
          var raw = Math.max(0, Math.min(1, (e.level_dbfs + 60) / 60));
          _applyLevel(raw);
        } else {
          _applyLevel(0);
        }
      })
      .catch(function(){ _levErr++; if(_levErr>10) _applyLevel(0); });
  }
  _pollLevel(); setInterval(_pollLevel, 150);
}

// ── Now-playing poll ────────────────────────────────────────────────────────
function _applyNP(d){
  if(_showNP && _hasStation){
    document.getElementById('lower').classList.add('vis');
    var np=d.np||{};
    var title=np.title||'', artist=np.artist||'', isSpot=np.is_spot||false;
    document.getElementById('np-title').textContent = title||'—';
    var aEl=document.getElementById('np-artist');
    aEl.textContent=artist; aEl.style.display=artist?'':'none';
    isSpot ? document.getElementById('np-spot').classList.add('vis') : document.getElementById('np-spot').classList.remove('vis');
    if(_showOair){
      (title&&!isSpot) ? document.getElementById('on-air').classList.add('vis') : document.getElementById('on-air').classList.remove('vis');
    }
  }
  var msg=(d.message||'').trim();
  var mb=document.getElementById('msg-bar');
  if(msg){document.getElementById('msg-txt').textContent=msg; mb.classList.add('vis');}
  else{mb.classList.remove('vis');}
}
function _pollNP(){
  if(!_stationId) return;
  fetch('/api/brandscreen/data/'+_stationId,{credentials:'same-origin'})
    .then(function(r){return r.json();}).then(_applyNP).catch(function(){});
}
if(_hasStation){ _pollNP(); setInterval(_pollNP, 10000); }

// ── SSE — instant studio assignment updates ─────────────────────────────────
if(_studioId){
  var _es = new EventSource('/api/brandscreen/events/studio/'+_studioId,{withCredentials:true});
  _es.onmessage = function(e){
    if(e.data==='assignment_changed'){
      document.getElementById('screen').classList.add('fade-out');
      setTimeout(function(){ location.replace(location.href); }, 580);
    }
  };
}
</script>
</body>
</html>"""

# ────────────────────────────────────────────────── register ──────────────────

def register(app, ctx):
    login_required = ctx["login_required"]
    csrf_protect   = ctx["csrf_protect"]
    monitor        = ctx["monitor"]
    hub_server     = ctx["hub_server"]

    api_auth = _make_api_auth()

    # ── Kiosk: strip security headers so Yodeck can load the page ────────────
    _KIOSK_PREFIXES = ("/brandscreen/", "/api/brandscreen/")

    def _bs_kiosk_headers(response):
        is_kiosk = getattr(g, "_bs_kiosk", False)
        if not is_kiosk:
            for pfx in _KIOSK_PREFIXES:
                if request.path.startswith(pfx):
                    is_kiosk = True
                    break
        if is_kiosk:
            for h in ("X-Frame-Options", "Content-Security-Policy",
                      "X-Content-Type-Options", "Referrer-Policy",
                      "Strict-Transport-Security"):
                response.headers.pop(h, None)
            response.headers["Access-Control-Allow-Origin"] = "*"
        return response

    try:
        app.after_request_funcs.setdefault(None, []).insert(0, _bs_kiosk_headers)
    except Exception:
        app.after_request(_bs_kiosk_headers)

    # ── Token-based kiosk auth ────────────────────────────────────────────────
    def _validate_bs_token(token):
        """Return True if token matches any studio or station token in config."""
        if not token:
            return False
        cfg = _cfg_load()
        for s in list(cfg.get("stations", [])) + list(cfg.get("studios", [])):
            if s.get("token") == token:
                return True
        return False

    @app.before_request
    def _bs_token_before():
        # Fire for any path that carries a token — kiosk pages + any API
        # calls the JS makes that include the token as a query param
        token = (request.args.get("token") or "").strip()
        if not token:
            return
        g._bs_kiosk = True
        if session.get("logged_in"):
            return
        if _validate_bs_token(token):
            session["logged_in"] = True
            session["login_ts"]  = _time.time()
            session["username"]  = "brandscreen"
            session["role"]      = "viewer"
            if not session.get("_csrf"):
                session["_csrf"] = hashlib.sha256(os.urandom(32)).hexdigest()

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _screen_params(st, studio_id="", studio_name=""):
        """Build render_template_string kwargs for the screen template."""
        brand  = (st or {}).get("brand_colour", "#17a8ff")
        accent = (st or {}).get("accent_colour", "#ffffff")
        r, g, b = _hex_rgb(brand)
        pal    = _brand_palette(brand)
        sid    = (st or {}).get("id", "") if st else ""
        p, _   = _logo_file(sid) if sid else (None, None)
        lk     = (st or {}).get("level_key", "")
        return dict(
            studio_id=studio_id, studio_name=studio_name,
            station_id=sid,
            sname=(st or {}).get("name", studio_name),
            brand=brand, accent=accent,
            brand_rgb=f"{r},{g},{b}",
            bg_deep=pal["bg_deep"], bg_dark=pal["bg_dark"], bg_mid=pal["bg_mid"],
            bg_style=(st or {}).get("bg_style", "minimal"),
            logo_anim=(st or {}).get("logo_anim", "none"),
            show_clock=(st or {}).get("show_clock", True),
            show_on_air=(st or {}).get("show_on_air", True),
            show_now_playing=(st or {}).get("show_now_playing", True),
            has_logo=p is not None,
            level_key=lk,
        )

    def _get_streams():
        streams = []
        try:
            for sd in (hub_server.get_sites() or []):
                site = sd.get("name") or sd.get("site", "")
                for s in (sd.get("streams") or []):
                    name = (s.get("name") or "").strip()
                    if name:
                        key = f"{site}|{name}"
                        streams.append({"key": key, "site": site,
                                        "stream": name,
                                        "label": f"{site} / {name}"})
        except Exception:
            pass
        return sorted(streams, key=lambda x: x["label"])

    # ── Admin page ────────────────────────────────────────────────────────────
    @app.get("/hub/brandscreen")
    @login_required
    def bs_admin():
        cfg      = _cfg_load()
        stations = [dict(s) for s in cfg.get("stations", [])]
        for s in stations:
            p, _ = _logo_file(s["id"])
            s["_has_logo"] = p is not None
        api_key = _ensure_api_key(cfg)
        return render_template_string(
            _ADMIN_TPL,
            stations_json=json.dumps(stations),
            studios_json=json.dumps(cfg.get("studios", [])),
            streams_json=json.dumps(_get_streams()),
            api_key=api_key,
            origin=request.host_url.rstrip("/"),
        )

    def _kiosk_response(html):
        """Wrap an HTML string in a kiosk-safe Response: strip all security
        headers, set CORS + Cache-Control, and suppress session-cookie writing.
        Mirrors the approach used by studioboard_tv()."""
        resp = make_response(html)
        for h in ("X-Frame-Options", "Content-Security-Policy",
                  "X-Content-Type-Options", "Referrer-Policy",
                  "Strict-Transport-Security"):
            resp.headers.pop(h, None)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        # Do NOT persist the session cookie — the token in the URL is the
        # sole auth mechanism for kiosk/Yodeck mode.
        session.modified = False
        return resp

    # ── Studio screen ─────────────────────────────────────────────────────────
    @app.get("/brandscreen/studio/<studio_id>")
    def bs_studio_screen(studio_id):
        g._bs_kiosk = True
        app_cfg = monitor.app_cfg
        token   = (request.args.get("token") or "").strip()
        if getattr(getattr(app_cfg, "auth", None), "enabled", False):
            if not session.get("logged_in") and not _validate_bs_token(token):
                return ("<h2>Token required</h2>"
                        "<p>Open this URL with <code>?token=YOUR_TOKEN</code></p>"), 403
        cfg    = _cfg_load()
        studio = _get_studio(cfg, studio_id)
        if not studio:
            return "Studio not found", 404
        sid = studio.get("station_id", "")
        st  = _get_station(cfg, sid) if sid else None
        if st and not st.get("enabled", True):
            st = None
        return _kiosk_response(render_template_string(
            _SCREEN_TPL,
            **_screen_params(st, studio_id=studio_id, studio_name=studio.get("name", "")),
        ))

    # ── Direct station screen (backward compat) ───────────────────────────────
    @app.get("/brandscreen/<station_id>")
    def bs_station_screen(station_id):
        if station_id == "studio":
            return "Not found", 404
        g._bs_kiosk = True
        app_cfg = monitor.app_cfg
        token   = (request.args.get("token") or "").strip()
        if getattr(getattr(app_cfg, "auth", None), "enabled", False):
            if not session.get("logged_in") and not _validate_bs_token(token):
                return ("<h2>Token required</h2>"
                        "<p>Open this URL with <code>?token=YOUR_TOKEN</code></p>"), 403
        cfg = _cfg_load()
        s   = _get_station(cfg, station_id)
        if not s:
            return "Station not found", 404
        if not s.get("enabled", True):
            return "Screen disabled", 403
        return _kiosk_response(render_template_string(_SCREEN_TPL, **_screen_params(s)))

    # ── SSE ───────────────────────────────────────────────────────────────────
    @app.get("/api/brandscreen/events/studio/<studio_id>")
    def bs_events(studio_id):
        return Response(
            stream_with_context(_sse_stream(studio_id)),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Now-playing data (public) ─────────────────────────────────────────────
    @app.get("/api/brandscreen/data/<station_id>")
    def bs_data(station_id):
        cfg = _cfg_load()
        s   = _get_station(cfg, station_id)
        if not s:
            return jsonify({"error": "not found"}), 404
        np_data = _resolve_np(s, monitor) if s.get("show_now_playing") else None
        return jsonify({"enabled": s.get("enabled", True), "np": np_data, "message": s.get("message", "")})

    # ── REST API: list ────────────────────────────────────────────────────────
    @app.get("/api/brandscreen/studios")
    @api_auth
    def bs_list():
        cfg = _cfg_load()
        return jsonify({
            "studios":  cfg.get("studios", []),
            "stations": [{k: v for k, v in s.items() if not k.startswith("_")}
                         for s in cfg.get("stations", [])],
        })

    # ── REST API: assign station ──────────────────────────────────────────────
    @app.route("/api/brandscreen/studio/<studio_id>/station", methods=["PUT", "POST"])
    @api_auth
    def bs_assign(studio_id):
        cfg    = _cfg_load()
        studio = _get_studio(cfg, studio_id)
        if not studio:
            return jsonify({"error": "Studio not found"}), 404
        data       = request.get_json(force=True) or {}
        station_id = (data.get("station_id") or "").strip()
        if station_id and not _get_station(cfg, station_id):
            return jsonify({"error": "Station not found"}), 404
        studio["station_id"] = station_id
        _cfg_save(cfg)
        _notify_studio(studio_id)
        return jsonify({"ok": True, "studio_id": studio_id, "station_id": station_id})

    # ── Logo ──────────────────────────────────────────────────────────────────
    @app.get("/api/brandscreen/logo/<station_id>")
    def bs_logo_get(station_id):
        p, ext = _logo_file(station_id)
        if not p:
            return "", 404
        mt, _ = mimetypes.guess_type(f"x.{ext}")
        return send_file(p, mimetype=mt or "image/png")

    @app.post("/api/brandscreen/logo/<station_id>")
    @login_required
    @csrf_protect
    def bs_logo_upload(station_id):
        cfg = _cfg_load()
        if not _get_station(cfg, station_id):
            return jsonify({"error": "Station not found"}), 404
        f = request.files.get("logo")
        if not f:
            return jsonify({"error": "No file"}), 400
        fn  = (f.filename or "").lower()
        ext = fn.rsplit(".", 1)[-1] if "." in fn else ""
        if ext not in ("png", "svg", "jpg", "jpeg", "webp", "gif"):
            return jsonify({"error": "Use PNG, SVG, JPG or WebP"}), 400
        for e in ("png", "svg", "jpg", "jpeg", "webp", "gif"):
            old = os.path.join(_LOGO_DIR, f"{station_id}.{e}")
            if os.path.exists(old):
                os.remove(old)
        f.save(os.path.join(_LOGO_DIR, f"{station_id}.{ext}"))
        return jsonify({"ok": True})

    @app.delete("/api/brandscreen/logo/<station_id>")
    @login_required
    @csrf_protect
    def bs_logo_delete(station_id):
        for e in ("png", "svg", "jpg", "jpeg", "webp", "gif"):
            p = os.path.join(_LOGO_DIR, f"{station_id}.{e}")
            if os.path.exists(p):
                os.remove(p)
        return jsonify({"ok": True})

    # ── Studio CRUD ───────────────────────────────────────────────────────────
    @app.post("/api/brandscreen/studio")
    @login_required
    @csrf_protect
    def bs_studio_create():
        cfg = _cfg_load()
        s   = _new_studio()
        cfg.setdefault("studios", []).append(s)
        _cfg_save(cfg)
        return jsonify({"ok": True, "studio": s})

    @app.post("/api/brandscreen/studio/<studio_id>")
    @login_required
    @csrf_protect
    def bs_studio_save(studio_id):
        cfg = _cfg_load()
        s   = _get_studio(cfg, studio_id)
        if not s:
            return jsonify({"error": "Studio not found"}), 404
        data = request.get_json(force=True) or {}
        for k in ("name", "station_id"):
            if k in data:
                s[k] = data[k]
        _cfg_save(cfg)
        _notify_studio(studio_id)
        return jsonify({"ok": True, "studio": s})

    @app.delete("/api/brandscreen/studio/<studio_id>")
    @login_required
    @csrf_protect
    def bs_studio_delete(studio_id):
        cfg = _cfg_load()
        cfg["studios"] = [s for s in cfg.get("studios", []) if s.get("id") != studio_id]
        _cfg_save(cfg)
        return jsonify({"ok": True})

    @app.post("/api/brandscreen/studio/<studio_id>/regen_token")
    @login_required
    @csrf_protect
    def bs_studio_regen(studio_id):
        cfg = _cfg_load()
        s   = _get_studio(cfg, studio_id)
        if not s:
            return jsonify({"error": "Studio not found"}), 404
        s["token"] = str(uuid.uuid4()).replace("-", "")
        _cfg_save(cfg)
        return jsonify({"ok": True, "token": s["token"]})

    # ── Station CRUD ──────────────────────────────────────────────────────────
    @app.post("/api/brandscreen/station")
    @login_required
    @csrf_protect
    def bs_station_create():
        cfg = _cfg_load()
        s   = _new_station()
        cfg.setdefault("stations", []).append(s)
        _cfg_save(cfg)
        s["_has_logo"] = False
        return jsonify({"ok": True, "station": s})

    @app.post("/api/brandscreen/station/<station_id>")
    @login_required
    @csrf_protect
    def bs_station_save(station_id):
        cfg = _cfg_load()
        s   = _get_station(cfg, station_id)
        if not s:
            return jsonify({"error": "Station not found"}), 404
        data    = request.get_json(force=True) or {}
        allowed = [
            "name", "enabled", "brand_colour", "accent_colour",
            "bg_style", "logo_anim", "show_clock", "show_on_air", "show_now_playing",
            "level_key", "np_source", "np_zetta_key", "np_api_url",
            "np_api_title_path", "np_api_artist_path", "np_manual", "message",
        ]
        for k in allowed:
            if k in data:
                s[k] = data[k]
        _cfg_save(cfg)
        p, _ = _logo_file(station_id)
        s["_has_logo"] = p is not None
        return jsonify({"ok": True, "station": s})

    @app.delete("/api/brandscreen/station/<station_id>")
    @login_required
    @csrf_protect
    def bs_station_delete(station_id):
        cfg = _cfg_load()
        cfg["stations"] = [s for s in cfg.get("stations", []) if s.get("id") != station_id]
        for sd in cfg.get("studios", []):
            if sd.get("station_id") == station_id:
                sd["station_id"] = ""
                _notify_studio(sd["id"])
        _cfg_save(cfg)
        for e in ("png", "svg", "jpg", "jpeg", "webp", "gif"):
            p = os.path.join(_LOGO_DIR, f"{station_id}.{e}")
            if os.path.exists(p):
                os.remove(p)
        return jsonify({"ok": True})

    # ── API key ───────────────────────────────────────────────────────────────
    @app.post("/api/brandscreen/regen_api_key")
    @login_required
    @csrf_protect
    def bs_regen_api_key():
        cfg = _cfg_load()
        cfg["api_key"] = str(uuid.uuid4()).replace("-", "")
        _cfg_save(cfg)
        return jsonify({"ok": True, "api_key": cfg["api_key"]})
