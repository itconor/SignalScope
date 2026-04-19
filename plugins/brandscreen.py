# brandscreen.py — SignalScope Brand Screen plugin
# Animated full-screen studio branding display with live now-playing,
# broadcast clock, orbit rings / particle backgrounds, and message-from-hub.
# Drop into the plugins/ subdirectory.

import os, json, uuid, threading, mimetypes
from flask import request, jsonify, render_template_string, send_file, session

SIGNALSCOPE_PLUGIN = {
    "id":       "brandscreen",
    "label":    "Brand Screen",
    "url":      "/hub/brandscreen",
    "icon":     "📺",
    "hub_only": True,
    "version":  "1.0.0",
}

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_CFG_PATH = os.path.join(_BASE_DIR, "brandscreen_cfg.json")
_LOGO_DIR = os.path.join(_BASE_DIR, "brandscreen_logos")
_LOCK     = threading.Lock()

os.makedirs(_LOGO_DIR, exist_ok=True)

# ─────────────────────────────────────────────── config helpers ───────────────

def _cfg_load():
    try:
        with open(_CFG_PATH) as f:
            return json.load(f)
    except Exception:
        return {"stations": []}

def _cfg_save(cfg):
    with _LOCK:
        with open(_CFG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)

def _get_station(cfg, sid):
    for s in cfg.get("stations", []):
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
        "token":              str(uuid.uuid4()).replace("-", ""),
    }

# ─────────────────────────────────────── now-playing resolver ─────────────────

def _resolve_np(station, monitor):
    """Return {title, artist, is_spot} or None."""
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
    """Return (r, g, b) from a #RRGGBB string."""
    h = (h or "#000000").lstrip("#")
    return int(h[:2], 16), int(h[2:4], 16), int(h[4:], 16)

# ──────────────────────────────────────────────── admin template ──────────────

_ADMIN_TPL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<title>Brand Screen — Admin</title>
<style nonce="{{csp_nonce()}}">
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(circle at top,#12376f 0%,var(--bg) 38%,#05101f 100%);color:var(--tx);font-family:system-ui,sans-serif;font-size:13px;min-height:100vh}
header{background:linear-gradient(180deg,rgba(10,31,65,.96),rgba(9,24,48,.96));border-bottom:1px solid var(--bor);padding:12px 20px;display:flex;align-items:center;gap:12px}
.btn{border:none;border-radius:8px;padding:5px 12px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;text-decoration:none;display:inline-block}
.btn:hover{filter:brightness(1.15)}.bp{background:var(--acc);color:#fff}.bd{background:var(--al);color:#fff}.bg{background:#132040;color:var(--tx)}.bs{font-size:11px;padding:3px 9px}
.card{background:var(--sur);border:1px solid var(--bor);border-radius:12px;overflow:hidden;margin-bottom:16px}
main{max-width:940px;margin:0 auto;padding:24px 16px}
.sc{background:var(--sur);border:1px solid var(--bor);border-radius:12px;margin-bottom:12px;overflow:hidden}
.sc-head{display:flex;align-items:center;gap:14px;padding:12px 16px}
.sc-logo{width:88px;height:48px;border-radius:6px;background:#0a1a3a;border:1px solid var(--bor);display:flex;align-items:center;justify-content:center;overflow:hidden;flex-shrink:0}
.sc-logo img{max-width:100%;max-height:100%;object-fit:contain}
.sc-meta{flex:1;min-width:0}.sc-name{font-weight:700;font-size:14px;margin-bottom:2px}.sc-sub{font-size:11px;color:var(--mu)}
.sc-actions{display:flex;gap:6px;align-items:center;flex-shrink:0}
.sc-body{padding:16px;border-top:1px solid var(--bor);display:none}.sc-body.open{display:block}
.field{display:flex;flex-direction:column;gap:4px;margin-bottom:12px}
.field label{font-size:11px;color:var(--mu);font-weight:600;text-transform:uppercase;letter-spacing:.05em}
input[type=text],input[type=url],select,textarea{background:#0d1e40;border:1px solid var(--bor);border-radius:6px;color:var(--tx);padding:6px 9px;font-size:13px;font-family:inherit;width:100%}
input[type=text]:focus,input[type=url]:focus,select:focus,textarea:focus{border-color:var(--acc);outline:none}
input[type=color]{background:#0d1e40;border:1px solid var(--bor);border-radius:6px;padding:2px 4px;height:32px;cursor:pointer;width:100%}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}
.sep{border:none;border-top:1px solid var(--bor);margin:14px 0}
.slabel{font-size:11px;font-weight:700;color:var(--mu);text-transform:uppercase;letter-spacing:.08em;margin-bottom:10px}
.logo-preview{width:128px;height:64px;border-radius:6px;background:#0a1a3a;border:1px solid var(--bor);display:flex;align-items:center;justify-content:center;overflow:hidden;margin-bottom:8px}
.logo-preview img{max-width:100%;max-height:100%;object-fit:contain}
.token-row{display:flex;gap:6px;align-items:center}
.token-row input{font-family:monospace;font-size:11px;color:var(--mu)}
.screen-url{font-family:monospace;font-size:11px;color:var(--acc);word-break:break-all;background:#071428;border:1px solid var(--bor);border-radius:6px;padding:8px 10px;margin-top:6px;cursor:pointer}
.msg-box{border-radius:8px;padding:10px 14px;margin-bottom:14px;font-size:12px;display:none}
.msg-ok{background:#0f2318;color:var(--ok);border:1px solid #166534}
.msg-err{background:#2a0a0a;color:var(--al);border:1px solid #991b1b}
.empty-state{text-align:center;padding:60px 24px;color:var(--mu)}
.badge{display:inline-flex;align-items:center;padding:2px 8px;border-radius:20px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.06em}
.b-ok{background:rgba(34,197,94,.12);color:var(--ok);border:1px solid rgba(34,197,94,.25)}
.b-mu{background:rgba(138,164,200,.1);color:var(--mu);border:1px solid rgba(138,164,200,.2)}
.cb-row{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.cb-row input[type=checkbox]{width:14px;height:14px;accent-color:var(--acc);flex-shrink:0}
.np-fields{display:none}.np-fields.open{display:block}
.hint{font-size:11px;color:var(--mu);margin-top:4px}
.colour-swatch{width:16px;height:16px;border-radius:4px;border:1px solid rgba(255,255,255,.2);flex-shrink:0;display:inline-block}
</style>
</head>
<body>
<header>
  <span style="font-size:20px">📺</span>
  <span style="font-weight:700;font-size:15px">Brand Screen</span>
  <span style="color:var(--mu);font-size:12px;margin-left:4px">Animated studio displays</span>
</header>
<main>
  <div id="msg" class="msg-box"></div>
  <div style="margin-bottom:18px">
    <button class="btn bp" id="add-btn">＋ Add Station</button>
  </div>
  <div id="list"></div>
</main>

<input type="file" id="logo-input" accept="image/png,image/svg+xml,image/jpeg,image/webp,image/gif" style="display:none">

<script nonce="{{csp_nonce()}}">
var _stations = {{stations_json|safe}};
var _zetStations = {{zet_json|safe}};
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

var _BG_LABELS   = {particles:'Particles',aurora:'Aurora',waves:'Waves',minimal:'Minimal'};
var _ANIM_LABELS = {orbit:'Orbit rings',pulse:'Pulse',glow:'Glow',float:'Float',none:'Static'};
var _NP_LABELS   = {zetta:'Zetta',json_api:'JSON API',manual:'Manual',none:'None'};

function _esc(s){var d=document.createElement('div');d.appendChild(document.createTextNode(s||''));return d.innerHTML;}
function _sid(s){return s.id;}

function render(){
  var el=document.getElementById('list');
  if(!_stations.length){
    el.innerHTML='<div class="empty-state"><div style="font-size:40px;margin-bottom:12px">📺</div><div>No stations yet — click <b>Add Station</b> to create your first brand screen.</div></div>';
    return;
  }
  el.innerHTML=_stations.map(function(s){
    var lImg=s._has_logo
      ?'<img src="/api/brandscreen/logo/'+s.id+'?t='+Date.now()+'" alt="">'
      :'<span style="font-size:22px;opacity:.25">📺</span>';
    return '<div class="sc" id="sc-'+s.id+'">'
      +'<div class="sc-head">'
      +'<div class="sc-logo">'+lImg+'</div>'
      +'<div class="sc-meta">'
      +'<div class="sc-name">'+_esc(s.name)+'</div>'
      +'<div class="sc-sub">'+(_BG_LABELS[s.bg_style]||s.bg_style)+' &nbsp;·&nbsp; '+(_ANIM_LABELS[s.logo_anim]||s.logo_anim)+' &nbsp;·&nbsp; NP: '+(_NP_LABELS[s.np_source]||s.np_source)+'</div>'
      +'</div>'
      +'<div class="sc-actions">'
      +'<span class="badge '+(s.enabled?'b-ok':'b-mu')+'">'+(s.enabled?'● On':'○ Off')+'</span>'
      +'<button class="btn bg bs" data-action="toggle-edit" data-sid="'+s.id+'">Edit</button>'
      +'<a class="btn bg bs" href="/brandscreen/'+s.id+'?token='+s.token+'" target="_blank">Preview ↗</a>'
      +'<button class="btn bd bs" data-action="delete" data-sid="'+s.id+'">Delete</button>'
      +'</div>'
      +'</div>'
      +'<div class="sc-body" id="scb-'+s.id+'">'
      +_editForm(s)
      +'</div>'
      +'</div>';
  }).join('');
}

function _editForm(s){
  var zetOpts=_zetStations.map(function(z){
    return '<option value="'+_esc(z.key)+'"'+(s.np_zetta_key===z.key?' selected':'')+'>'+_esc(z.name)+'</option>';
  }).join('');
  return '<div class="slabel">Identity</div>'
    +'<div class="grid2">'
    +'<div class="field"><label>Station Name</label><input type="text" id="f-name-'+s.id+'" value="'+_esc(s.name)+'"></div>'
    +'<div class="field" style="justify-content:flex-end">'
    +'<label style="margin-bottom:6px">Screen</label>'
    +'<label style="display:flex;align-items:center;gap:8px;cursor:pointer">'
    +'<input type="checkbox" id="f-en-'+s.id+'" style="accent-color:var(--acc);width:16px;height:16px"'+(s.enabled?' checked':'')+'>'
    +'<span style="font-size:13px">Enabled</span></label>'
    +'</div></div>'
    +'<div class="grid2">'
    +'<div class="field"><label>Brand Colour</label><input type="color" id="f-brand-'+s.id+'" value="'+_esc(s.brand_colour)+'"></div>'
    +'<div class="field"><label>Accent Colour</label><input type="color" id="f-accent-'+s.id+'" value="'+_esc(s.accent_colour)+'"></div>'
    +'</div>'
    +'<hr class="sep">'
    +'<div class="slabel">Animation &amp; Display</div>'
    +'<div class="grid2">'
    +'<div class="field"><label>Background Style</label>'
    +'<select id="f-bg-'+s.id+'">'
    +'<option value="particles"'+(s.bg_style==='particles'?' selected':'')+'>✦ Particles</option>'
    +'<option value="aurora"'+(s.bg_style==='aurora'?' selected':'')+'>◎ Aurora</option>'
    +'<option value="waves"'+(s.bg_style==='waves'?' selected':'')+'>⌇ Waves</option>'
    +'<option value="minimal"'+(s.bg_style==='minimal'?' selected':'')+'>▪ Minimal</option>'
    +'</select></div>'
    +'<div class="field"><label>Logo Animation</label>'
    +'<select id="f-anim-'+s.id+'">'
    +'<option value="orbit"'+(s.logo_anim==='orbit'?' selected':'')+'>⊙ Orbit rings</option>'
    +'<option value="pulse"'+(s.logo_anim==='pulse'?' selected':'')+'>◉ Pulse</option>'
    +'<option value="glow"'+(s.logo_anim==='glow'?' selected':'')+'>✦ Glow</option>'
    +'<option value="float"'+(s.logo_anim==='float'?' selected':'')+'>↕ Float</option>'
    +'<option value="none"'+(s.logo_anim==='none'?' selected':'')+'>— Static</option>'
    +'</select></div>'
    +'</div>'
    +'<div class="grid3" style="margin-bottom:8px">'
    +'<div class="cb-row"><input type="checkbox" id="f-clk-'+s.id+'"'+(s.show_clock?' checked':'')+'><label for="f-clk-'+s.id+'">Broadcast Clock</label></div>'
    +'<div class="cb-row"><input type="checkbox" id="f-oair-'+s.id+'"'+(s.show_on_air?' checked':'')+'><label for="f-oair-'+s.id+'">On Air Badge</label></div>'
    +'<div class="cb-row"><input type="checkbox" id="f-np-'+s.id+'"'+(s.show_now_playing?' checked':'')+'><label for="f-np-'+s.id+'">Now Playing</label></div>'
    +'</div>'
    +'<hr class="sep">'
    +'<div class="slabel">Logo</div>'
    +'<div class="logo-preview" id="lp-'+s.id+'">'
    +(s._has_logo?'<img src="/api/brandscreen/logo/'+s.id+'?t='+Date.now()+'" alt="">':'<span style="font-size:32px;opacity:.2">📺</span>')
    +'</div>'
    +'<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">'
    +'<button class="btn bg bs" data-action="upload-logo" data-sid="'+s.id+'">Upload Logo</button>'
    +(s._has_logo?'<button class="btn bd bs" data-action="del-logo" data-sid="'+s.id+'">Remove</button>':'')
    +'</div>'
    +'<p class="hint" style="margin-top:6px">PNG, SVG, JPG or WebP — transparent background PNG gives best results on any background colour.</p>'
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
    +'<div class="field"><label>Zetta Station</label>'
    +'<select id="f-zpkey-'+s.id+'"><option value="">— choose —</option>'+zetOpts+'</select>'
    +(zetOpts?'':'<p class="hint">No Zetta stations detected. Is the Zetta plugin installed and connected?</p>')
    +'</div></div>'
    +'<div class="np-fields'+(s.np_source==='json_api'?' open':'')+'" id="npf-json-'+s.id+'">'
    +'<div class="field"><label>API URL</label><input type="url" id="f-npurl-'+s.id+'" value="'+_esc(s.np_api_url)+'"></div>'
    +'<div class="grid2">'
    +'<div class="field"><label>Title field (dot path)</label><input type="text" id="f-nptpath-'+s.id+'" value="'+_esc(s.np_api_title_path)+'"></div>'
    +'<div class="field"><label>Artist field (dot path)</label><input type="text" id="f-npapath-'+s.id+'" value="'+_esc(s.np_api_artist_path)+'"></div>'
    +'</div><p class="hint">Example: <code>now_playing.song.title</code></p>'
    +'</div>'
    +'<div class="np-fields'+(s.np_source==='manual'?' open':'')+'" id="npf-manual-'+s.id+'">'
    +'<div class="field"><label>Display Text</label><input type="text" id="f-npman-'+s.id+'" value="'+_esc(s.np_manual)+'" placeholder="Always shown as now-playing text"></div>'
    +'</div>'
    +'<hr class="sep">'
    +'<div class="slabel">Live Message</div>'
    +'<div class="field"><label>Message (amber banner on screen — clear to dismiss)</label>'
    +'<input type="text" id="f-msg-'+s.id+'" value="'+_esc(s.message)+'" placeholder="Leave blank for no banner" maxlength="200">'
    +'</div>'
    +'<hr class="sep">'
    +'<div class="slabel">Screen Access</div>'
    +'<div class="token-row" style="margin-bottom:6px">'
    +'<input type="text" value="'+_esc(s.token)+'" readonly>'
    +'<button class="btn bg bs" data-action="regen-token" data-sid="'+s.id+'">Regenerate</button>'
    +'</div>'
    +'<div class="screen-url" id="surl-'+s.id+'" title="Click to copy">'
    +_esc(location.origin+'/brandscreen/'+s.id+'?token='+s.token)
    +'</div>'
    +'<p class="hint" style="margin-top:6px">Open this URL in a full-screen browser on your studio display. The token authenticates automatically.</p>'
    +'<div style="display:flex;gap:8px;margin-top:16px">'
    +'<button class="btn bp" data-action="save" data-sid="'+s.id+'">Save</button>'
    +'<button class="btn bg" data-action="toggle-edit" data-sid="'+s.id+'">Cancel</button>'
    +'</div>';
}

function _v(id){ var el=document.getElementById(id); return el?(el.type==='checkbox'?el.checked:el.value):null; }
function _sid2s(sid){ return _stations.find(function(s){return s.id===sid;})||null; }

function _toggleEdit(sid){
  var el=document.getElementById('scb-'+sid);
  if(el) el.classList.toggle('open');
}

function _npSrcChanged(sid){
  var src=_v('f-npsrc-'+sid);
  ['zetta','json_api','manual'].forEach(function(k){
    var el=document.getElementById('npf-'+k+'-'+sid);
    if(el){ el.className='np-fields'+(src===k?' open':''); }
  });
}

function _addStation(){
  _post('/api/brandscreen/station',{}).then(function(r){return r.json();}).then(function(d){
    if(d.error){_msg(d.error,false);return;}
    _stations.push(d.station);
    render();
    _toggleEdit(d.station.id);
    _msg('Station created — configure it below.',true);
  }).catch(function(){_msg('Request failed',false);});
}

function _save(sid){
  var s=_sid2s(sid);if(!s)return;
  var data={
    name:               _v('f-name-'+sid)||s.name,
    enabled:            !!_v('f-en-'+sid),
    brand_colour:       _v('f-brand-'+sid)||s.brand_colour,
    accent_colour:      _v('f-accent-'+sid)||s.accent_colour,
    bg_style:           _v('f-bg-'+sid)||s.bg_style,
    logo_anim:          _v('f-anim-'+sid)||s.logo_anim,
    show_clock:         !!_v('f-clk-'+sid),
    show_on_air:        !!_v('f-oair-'+sid),
    show_now_playing:   !!_v('f-np-'+sid),
    np_source:          _v('f-npsrc-'+sid)||'none',
    np_zetta_key:       _v('f-zpkey-'+sid)||'',
    np_api_url:         _v('f-npurl-'+sid)||'',
    np_api_title_path:  _v('f-nptpath-'+sid)||'',
    np_api_artist_path: _v('f-npapath-'+sid)||'',
    np_manual:          _v('f-npman-'+sid)||'',
    message:            _v('f-msg-'+sid)||'',
  };
  _post('/api/brandscreen/station/'+sid,data).then(function(r){return r.json();}).then(function(d){
    if(d.error){_msg(d.error,false);return;}
    Object.assign(s,d.station);
    render(); _toggleEdit(sid);
    _msg('Saved.',true);
  }).catch(function(){_msg('Save failed',false);});
}

function _deleteStation(sid){
  if(!confirm('Delete this station? This cannot be undone.'))return;
  _del('/api/brandscreen/station/'+sid).then(function(r){return r.json();}).then(function(d){
    if(d.error){_msg(d.error,false);return;}
    _stations=_stations.filter(function(s){return s.id!==sid;});
    render(); _msg('Deleted.',true);
  });
}

function _uploadLogo(sid){
  _currentLogoSid=sid;
  document.getElementById('logo-input').value='';
  document.getElementById('logo-input').click();
}

function _doUpload(file){
  if(!file||!_currentLogoSid)return;
  var sid=_currentLogoSid;
  var fd=new FormData(); fd.append('logo',file);
  fetch('/api/brandscreen/logo/'+sid,{method:'POST',headers:{'X-CSRFToken':_csrf()},body:fd})
    .then(function(r){return r.json();}).then(function(d){
      if(d.error){_msg(d.error,false);return;}
      var s=_sid2s(sid);if(s)s._has_logo=true;
      render(); _msg('Logo uploaded.',true);
    });
}

function _delLogo(sid){
  if(!confirm('Remove this logo?'))return;
  _del('/api/brandscreen/logo/'+sid).then(function(r){return r.json();}).then(function(d){
    if(d.error){_msg(d.error,false);return;}
    var s=_sid2s(sid);if(s)s._has_logo=false;
    render(); _msg('Logo removed.',true);
  });
}

function _regenToken(sid){
  if(!confirm('Regenerate token? The current screen URL will stop working.'))return;
  _post('/api/brandscreen/station/'+sid+'/regen_token',{}).then(function(r){return r.json();}).then(function(d){
    if(d.error){_msg(d.error,false);return;}
    var s=_sid2s(sid);if(s)s.token=d.token;
    render(); _toggleEdit(sid);
    _msg('Token regenerated — update the URL on your display.',true);
  });
}

// ── Event delegation ──────────────────────────────────────────────────────────
document.getElementById('add-btn').addEventListener('click',_addStation);

document.getElementById('logo-input').addEventListener('change',function(){
  _doUpload(this.files[0]);
});

document.getElementById('list').addEventListener('click',function(e){
  var btn=e.target.closest('[data-action]');
  if(!btn)return;
  var a=btn.dataset.action, sid=btn.dataset.sid;
  if(a==='toggle-edit')  _toggleEdit(sid);
  else if(a==='save')    _save(sid);
  else if(a==='delete')  _deleteStation(sid);
  else if(a==='upload-logo') _uploadLogo(sid);
  else if(a==='del-logo')    _delLogo(sid);
  else if(a==='regen-token') _regenToken(sid);
});

document.getElementById('list').addEventListener('click',function(e){
  var su=e.target.closest('.screen-url');
  if(su){ navigator.clipboard&&navigator.clipboard.writeText(su.textContent.trim());_msg('URL copied.',true); }
});

document.getElementById('list').addEventListener('change',function(e){
  if(e.target.dataset.npSel) _npSrcChanged(e.target.dataset.npSel);
});

render();
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
html,body{width:100%;height:100%;overflow:hidden;background:#04091a;font-family:system-ui,sans-serif;color:#fff}
:root{--brand:{{brand|e}};--brand-rgb:{{brand_rgb|e}};--accent:{{accent|e}}}

/* ── Background layers ───────────────────────────────────────────────────── */
#cv{position:fixed;inset:0;width:100%;height:100%;z-index:0;display:none}
.bg-aurora{position:fixed;inset:0;z-index:0;
  background:
    radial-gradient(ellipse 60% 50% at 15% 20%,rgba(var(--brand-rgb),.22) 0%,transparent 70%),
    radial-gradient(ellipse 50% 45% at 85% 80%,rgba(var(--brand-rgb),.16) 0%,transparent 70%),
    radial-gradient(ellipse 80% 60% at 50% 110%,rgba(var(--brand-rgb),.10) 0%,transparent 70%),
    #04091a;
  animation:aurora 14s ease-in-out infinite alternate}
@keyframes aurora{
  0%  {background-size:120% 100%,100% 120%,140% 80%;background-position:0% 0%,100% 100%,50% 100%}
  100%{background-size:140% 110%,120% 100%,160% 90%;background-position:20% 10%,80% 90%,60% 100%}}
.bg-waves{position:fixed;inset:0;z-index:0;
  background:radial-gradient(ellipse at top,#0a1830 0%,#04091a 60%)}
.wave-wrap{position:fixed;bottom:0;left:0;width:100%;overflow:hidden;z-index:0}
.wave-wrap svg{display:block;width:200%;animation:wave-slide 10s linear infinite}
.wave-wrap svg+svg{animation-direction:reverse;opacity:.55;margin-top:-30px}
@keyframes wave-slide{0%{transform:translateX(0)}100%{transform:translateX(-50%)}}
.bg-minimal{position:fixed;inset:0;z-index:0;
  background:radial-gradient(ellipse at top,#0a1830 0%,#04091a 55%)}

/* ── Main layout ─────────────────────────────────────────────────────────── */
#screen{position:relative;z-index:1;width:100%;height:100vh;display:flex;flex-direction:column;
  animation:px-drift 90s step-end infinite}
@keyframes px-drift{0%,100%{transform:translate(0,0)}25%{transform:translate(1px,0)}50%{transform:translate(1px,1px)}75%{transform:translate(0,1px)}}

/* ── Top bar ─────────────────────────────────────────────────────────────── */
#top-bar{display:flex;align-items:flex-start;justify-content:space-between;padding:24px 36px 0;flex-shrink:0}

#on-air{display:none;align-items:center;gap:8px;
  background:rgba(239,68,68,.12);border:1.5px solid rgba(239,68,68,.45);border-radius:8px;
  padding:6px 18px;font-size:12px;font-weight:800;letter-spacing:.18em;text-transform:uppercase;color:#ef4444}
#on-air.vis{display:flex}
.oa-dot{width:8px;height:8px;border-radius:50%;background:#ef4444;animation:oab 1.2s ease-in-out infinite}
@keyframes oab{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.25;transform:scale(.7)}}

#clock-wrap{text-align:right;display:none}
#clock-wrap.vis{display:block}
#clock-time{font-size:30px;font-weight:200;letter-spacing:.08em;font-variant-numeric:tabular-nums;
  color:rgba(255,255,255,.9)}
#clock-date{font-size:11px;color:rgba(255,255,255,.35);letter-spacing:.07em;text-transform:uppercase;margin-top:2px}

/* ── Centre / logo zone ──────────────────────────────────────────────────── */
#centre{flex:1;display:flex;align-items:center;justify-content:center;position:relative}
.logo-zone{position:relative;display:flex;align-items:center;justify-content:center}

#logo-img{max-width:300px;max-height:180px;object-fit:contain;position:relative;z-index:10;display:block}
#logo-ph{font-size:90px;opacity:.12;position:relative;z-index:10}

/* ── Orbit rings ─────────────────────────────────────────────────────────── */
.orbit-wrap{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;pointer-events:none}
.orb{position:absolute;border-radius:50%;border:1.5px solid var(--brand)}
.orb1{width:380px;height:150px;opacity:.38;
  animation:orb-s1 10s linear infinite}
.orb1::before{content:'';position:absolute;width:10px;height:10px;border-radius:50%;
  background:var(--brand);box-shadow:0 0 12px 3px var(--brand);
  top:-5px;left:calc(50% - 5px)}
.orb2{width:290px;height:106px;opacity:.22;border-style:dashed;
  animation:orb-s2 15s linear infinite reverse}
.orb2::before{content:'';position:absolute;width:7px;height:7px;border-radius:50%;
  background:var(--brand);box-shadow:0 0 8px 2px var(--brand);
  bottom:-3.5px;left:calc(50% - 3.5px)}
@keyframes orb-s1{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}
@keyframes orb-s2{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}

/* ── Pulse rings ─────────────────────────────────────────────────────────── */
.pulse-wrap{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;pointer-events:none}
.prng{position:absolute;width:220px;height:220px;border-radius:50%;
  border:2px solid var(--brand);opacity:0;
  animation:pulse-out 3.2s ease-out infinite}
.prng:nth-child(2){animation-delay:1.07s}
.prng:nth-child(3){animation-delay:2.13s}
@keyframes pulse-out{0%{transform:scale(.55);opacity:.72}100%{transform:scale(2.1);opacity:0}}

/* ── Logo animations ─────────────────────────────────────────────────────── */
.la-float .logo-zone{animation:lo-float 4s ease-in-out infinite}
@keyframes lo-float{0%,100%{transform:translateY(0)}50%{transform:translateY(-14px)}}
.la-glow #logo-img{animation:lo-glow 2.8s ease-in-out infinite}
@keyframes lo-glow{
  0%,100%{filter:drop-shadow(0 0 4px rgba(var(--brand-rgb),.5))}
  50%    {filter:drop-shadow(0 0 24px rgba(var(--brand-rgb),.9)) drop-shadow(0 0 60px rgba(var(--brand-rgb),.5))}}

/* ── Now-playing lower third ─────────────────────────────────────────────── */
#lower{flex-shrink:0;padding:0 48px 36px;display:none;flex-direction:column;align-items:center;text-align:center}
#lower.vis{display:flex}
#np-line{width:56px;height:1.5px;background:linear-gradient(90deg,transparent,var(--brand),transparent);margin-bottom:10px}
#np-label{font-size:9px;font-weight:700;letter-spacing:.22em;color:var(--brand);text-transform:uppercase;margin-bottom:10px}
#np-title{font-size:24px;font-weight:300;letter-spacing:.02em;color:rgba(255,255,255,.95);margin-bottom:5px;
  max-width:820px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#np-artist{font-size:14px;color:rgba(255,255,255,.45);
  max-width:600px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#np-spot{display:none;margin-top:10px;padding:3px 16px;border-radius:20px;
  background:rgba(245,158,11,.13);color:#f59e0b;border:1px solid rgba(245,158,11,.35);
  font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase}
#np-spot.vis{display:inline-block}

/* ── Message banner ──────────────────────────────────────────────────────── */
#msg-bar{flex-shrink:0;display:none;align-items:center;justify-content:center;gap:10px;
  background:rgba(245,158,11,.92);padding:11px 28px;
  font-size:15px;font-weight:600;color:#1a0900;
  animation:msg-flash 2.5s ease-in-out infinite}
#msg-bar.vis{display:flex}
@keyframes msg-flash{0%,100%{opacity:1}50%{opacity:.8}}
</style>
</head>
<body class="la-{{logo_anim|e}}">

{% if bg_style == 'particles' %}
<canvas id="cv"></canvas>
{% elif bg_style == 'aurora' %}
<div class="bg-aurora"></div>
{% elif bg_style == 'waves' %}
<div class="bg-waves"></div>
<div class="wave-wrap">
  <svg viewBox="0 0 1440 100" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="none">
    <path d="M0,50 C240,90 480,10 720,50 C960,90 1200,10 1440,50 L1440,100 L0,100Z" fill="rgba({{brand_rgb}},0.14)"/>
    <path d="M0,65 C360,25 720,85 1080,50 C1260,35 1380,60 1440,55 L1440,100 L0,100Z" fill="rgba({{brand_rgb}},0.08)"/>
  </svg>
  <svg viewBox="0 0 1440 80" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="none">
    <path d="M0,40 C180,70 540,10 720,40 C900,70 1260,10 1440,40 L1440,80 L0,80Z" fill="rgba({{brand_rgb}},0.10)"/>
  </svg>
</div>
{% else %}
<div class="bg-minimal"></div>
{% endif %}

<div id="screen">
  <div id="top-bar">
    <div id="on-air"><div class="oa-dot"></div>ON AIR</div>
    <div id="clock-wrap">
      <div id="clock-time">--:--:--</div>
      <div id="clock-date"></div>
    </div>
  </div>

  <div id="centre">
    {% if logo_anim == 'orbit' %}
    <div class="orbit-wrap"><div class="orb orb1"></div><div class="orb orb2"></div></div>
    {% elif logo_anim == 'pulse' %}
    <div class="pulse-wrap"><div class="prng"></div><div class="prng"></div><div class="prng"></div></div>
    {% endif %}
    <div class="logo-zone">
      {% if has_logo %}<img id="logo-img" src="/api/brandscreen/logo/{{sid|e}}" alt="{{sname|e}}">
      {% else %}<div id="logo-ph">📺</div>{% endif %}
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

<script nonce="{{csp_nonce()}}">
var _sid      = '{{sid|e}}';
var _bgStyle  = '{{bg_style|e}}';
var _brandRgb = '{{brand_rgb|e}}';
var _showClock= {{show_clock|lower}};
var _showOair = {{show_on_air|lower}};
var _showNP   = {{show_now_playing|lower}};

// ── Clock ──────────────────────────────────────────────────────────────────
if(_showClock){
  var _cwrap=document.getElementById('clock-wrap');
  _cwrap.classList.add('vis');
  var _DAYS=['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
  var _MONTHS=['January','February','March','April','May','June','July','August','September','October','November','December'];
  function _tick(){
    var n=new Date();
    var hh=String(n.getHours()).padStart(2,'0');
    var mm=String(n.getMinutes()).padStart(2,'0');
    var ss=String(n.getSeconds()).padStart(2,'0');
    document.getElementById('clock-time').textContent=hh+':'+mm+':'+ss;
    document.getElementById('clock-date').textContent=_DAYS[n.getDay()]+' '+n.getDate()+' '+_MONTHS[n.getMonth()]+' '+n.getFullYear();
  }
  _tick(); setInterval(_tick,1000);
}

// ── Particles canvas ───────────────────────────────────────────────────────
if(_bgStyle==='particles'){
  var cv=document.getElementById('cv');
  var ctx=cv.getContext('2d');
  var _rgb=_brandRgb.split(',').map(Number);
  var pts=[];
  function _resize(){cv.width=window.innerWidth;cv.height=window.innerHeight;}
  function _pt(){
    return{x:Math.random()*cv.width,y:cv.height+Math.random()*120,
      r:0.8+Math.random()*2.2,sp:0.25+Math.random()*0.55,
      dr:(Math.random()-.5)*0.25,al:0.08+Math.random()*0.38,
      al2:0.08+Math.random()*0.38};
  }
  _resize(); window.addEventListener('resize',_resize);
  cv.style.display='block';
  for(var i=0;i<90;i++){var p=_pt();if(i<45)p.y=Math.random()*cv.height;pts.push(p);}
  function _draw(){
    ctx.clearRect(0,0,cv.width,cv.height);
    pts.forEach(function(p){
      p.y-=p.sp; p.x+=p.dr;
      if(p.y<-8||p.x<-8||p.x>cv.width+8) Object.assign(p,_pt());
      ctx.beginPath();
      ctx.arc(p.x,p.y,p.r,0,Math.PI*2);
      ctx.fillStyle='rgba('+_rgb[0]+','+_rgb[1]+','+_rgb[2]+','+p.al+')';
      ctx.fill();
    });
    requestAnimationFrame(_draw);
  }
  _draw();
}

// ── Live data poll ─────────────────────────────────────────────────────────
function _apply(d){
  // Now playing
  if(_showNP){
    document.getElementById('lower').classList.add('vis');
    var np=d.np||{};
    var title=np.title||'';
    var artist=np.artist||'';
    var isSpot=np.is_spot||false;
    document.getElementById('np-title').textContent=title||'—';
    var aEl=document.getElementById('np-artist');
    aEl.textContent=artist; aEl.style.display=artist?'':'none';
    var sEl=document.getElementById('np-spot');
    isSpot?sEl.classList.add('vis'):sEl.classList.remove('vis');
    if(_showOair){
      var oEl=document.getElementById('on-air');
      (title&&!isSpot)?oEl.classList.add('vis'):oEl.classList.remove('vis');
    }
  }
  // Message banner
  var mb=document.getElementById('msg-bar');
  var mt=document.getElementById('msg-txt');
  var msg=(d.message||'').trim();
  if(msg){mt.textContent=msg;mb.classList.add('vis');}
  else{mb.classList.remove('vis');}
}

function _poll(){
  fetch('/api/brandscreen/data/'+_sid,{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){if(d&&d.enabled!==false)_apply(d);})
    .catch(function(){});
}
_poll(); setInterval(_poll,10000);
</script>
</body>
</html>"""

# ────────────────────────────────────────────────── register ──────────────────

def register(app, ctx):
    login_required = ctx["login_required"]
    csrf_protect   = ctx["csrf_protect"]
    monitor        = ctx["monitor"]

    # ── Token-based kiosk auth for screen pages ───────────────────────────────
    @app.before_request
    def _bs_token_before():
        if request.path.startswith("/brandscreen/"):
            token = (request.args.get("token") or "").strip()
            if token:
                cfg = _cfg_load()
                for s in cfg.get("stations", []):
                    if s.get("token") == token:
                        session["logged_in"] = True
                        break

    # ── Admin page ────────────────────────────────────────────────────────────
    @app.get("/hub/brandscreen")
    @login_required
    def bs_admin():
        cfg      = _cfg_load()
        stations = cfg.get("stations", [])
        for s in stations:
            p, _ = _logo_file(s["id"])
            s["_has_logo"] = p is not None
        # Collect available Zetta stations
        zet = []
        try:
            data = getattr(monitor, "_zetta_live_station_data", lambda: {})()
            for key, sd in (data or {}).items():
                name = sd.get("station_name") or sd.get("name") or key
                zet.append({"key": key, "name": name})
            zet.sort(key=lambda z: z["name"])
        except Exception:
            pass
        return render_template_string(
            _ADMIN_TPL,
            stations_json=json.dumps(stations),
            zet_json=json.dumps(zet),
        )

    # ── Screen display page ───────────────────────────────────────────────────
    @app.get("/brandscreen/<station_id>")
    @login_required
    def bs_screen(station_id):
        cfg = _cfg_load()
        s   = _get_station(cfg, station_id)
        if not s:
            return "Station not found", 404
        if not s.get("enabled", True):
            return "Screen is disabled", 403
        p, _   = _logo_file(station_id)
        brand  = s.get("brand_colour", "#17a8ff")
        accent = s.get("accent_colour", "#ffffff")
        r, g, b = _hex_rgb(brand)
        return render_template_string(
            _SCREEN_TPL,
            sid=station_id,
            sname=s.get("name", ""),
            brand=brand,
            accent=accent,
            brand_rgb=f"{r},{g},{b}",
            bg_style=s.get("bg_style", "particles"),
            logo_anim=s.get("logo_anim", "orbit"),
            show_clock=s.get("show_clock", True),
            show_on_air=s.get("show_on_air", True),
            show_now_playing=s.get("show_now_playing", True),
            has_logo=p is not None,
        )

    # ── Data API (public — screen polls this) ─────────────────────────────────
    @app.get("/api/brandscreen/data/<station_id>")
    def bs_data(station_id):
        cfg = _cfg_load()
        s   = _get_station(cfg, station_id)
        if not s:
            return jsonify({"error": "not found"}), 404
        np_data = _resolve_np(s, monitor) if s.get("show_now_playing") else None
        return jsonify({
            "enabled": s.get("enabled", True),
            "np":      np_data,
            "message": s.get("message", ""),
        })

    # ── Logo serve (public) ───────────────────────────────────────────────────
    @app.get("/api/brandscreen/logo/<station_id>")
    def bs_logo_get(station_id):
        p, ext = _logo_file(station_id)
        if not p:
            return "", 404
        mt, _ = mimetypes.guess_type(f"x.{ext}")
        return send_file(p, mimetype=mt or "image/png")

    # ── Logo upload ───────────────────────────────────────────────────────────
    @app.post("/api/brandscreen/logo/<station_id>")
    @login_required
    @csrf_protect
    def bs_logo_upload(station_id):
        cfg = _cfg_load()
        if not _get_station(cfg, station_id):
            return jsonify({"error": "Station not found"}), 404
        f = request.files.get("logo")
        if not f:
            return jsonify({"error": "No file provided"}), 400
        fn  = (f.filename or "").lower()
        ext = fn.rsplit(".", 1)[-1] if "." in fn else ""
        if ext not in ("png", "svg", "jpg", "jpeg", "webp", "gif"):
            return jsonify({"error": "Unsupported file type (use PNG, SVG, JPG or WebP)"}), 400
        for e in ("png", "svg", "jpg", "jpeg", "webp", "gif"):
            old = os.path.join(_LOGO_DIR, f"{station_id}.{e}")
            if os.path.exists(old):
                os.remove(old)
        f.save(os.path.join(_LOGO_DIR, f"{station_id}.{ext}"))
        return jsonify({"ok": True})

    # ── Logo delete ───────────────────────────────────────────────────────────
    @app.delete("/api/brandscreen/logo/<station_id>")
    @login_required
    @csrf_protect
    def bs_logo_delete(station_id):
        for e in ("png", "svg", "jpg", "jpeg", "webp", "gif"):
            p = os.path.join(_LOGO_DIR, f"{station_id}.{e}")
            if os.path.exists(p):
                os.remove(p)
        return jsonify({"ok": True})

    # ── Station create ────────────────────────────────────────────────────────
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

    # ── Station save ──────────────────────────────────────────────────────────
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
            "np_source", "np_zetta_key", "np_api_url", "np_api_title_path",
            "np_api_artist_path", "np_manual", "message",
        ]
        for k in allowed:
            if k in data:
                s[k] = data[k]
        _cfg_save(cfg)
        p, _ = _logo_file(station_id)
        s["_has_logo"] = p is not None
        return jsonify({"ok": True, "station": s})

    # ── Station delete ────────────────────────────────────────────────────────
    @app.delete("/api/brandscreen/station/<station_id>")
    @login_required
    @csrf_protect
    def bs_station_delete(station_id):
        cfg = _cfg_load()
        cfg["stations"] = [s for s in cfg.get("stations", []) if s.get("id") != station_id]
        _cfg_save(cfg)
        for e in ("png", "svg", "jpg", "jpeg", "webp", "gif"):
            p = os.path.join(_LOGO_DIR, f"{station_id}.{e}")
            if os.path.exists(p):
                os.remove(p)
        return jsonify({"ok": True})

    # ── Token regenerate ──────────────────────────────────────────────────────
    @app.post("/api/brandscreen/station/<station_id>/regen_token")
    @login_required
    @csrf_protect
    def bs_station_regen(station_id):
        cfg = _cfg_load()
        s   = _get_station(cfg, station_id)
        if not s:
            return jsonify({"error": "Station not found"}), 404
        s["token"] = str(uuid.uuid4()).replace("-", "")
        _cfg_save(cfg)
        return jsonify({"ok": True, "token": s["token"]})
