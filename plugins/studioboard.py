# studioboard.py — Studio Board for SignalScope
# Large-format display for outside broadcast studios.
# Drop into the plugins/ subdirectory.

import os, json, re, time as _time, uuid as _uuid

SIGNALSCOPE_PLUGIN = {
    "id":       "studioboard",
    "label":    "Studio Board",
    "url":      "/hub/studioboard",
    "icon":     "🎙",
    "hub_only": True,
    "version":  "1.2.0",
}

_BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
_APP_DIR   = os.path.dirname(_BASE_DIR)
_CFG_PATH  = os.path.join(_BASE_DIR, "studioboard_cfg.json")
_ART_DIR   = os.path.join(_BASE_DIR, "studioboard_art")

# ── Config helpers ──────────────────────────────────────────────

def _cfg_load():
    try:
        with open(_CFG_PATH) as f:
            return json.load(f)
    except Exception:
        return {"studios": []}

def _cfg_save(c):
    with open(_CFG_PATH, "w") as f:
        json.dump(c, f, indent=2)

def _get_studio(cfg, studio_id):
    for s in cfg.get("studios", []):
        if s.get("id") == studio_id:
            return s
    return None


# ── Plugin registration ─────────────────────────────────────────

def register(app, ctx):
    login_required = ctx["login_required"]
    csrf_protect   = ctx["csrf_protect"]
    monitor        = ctx["monitor"]
    hub_server     = ctx.get("hub_server")
    BUILD          = ctx["BUILD"]

    from flask import (jsonify, render_template_string, request,
                       send_file, g, session, make_response)

    # ── Kiosk header stripping (same pattern as wallboard) ──────
    _KIOSK_PREFIXES = ("/studioboard/tv", "/studioboard/art",
                       "/api/studioboard/")

    def _sb_kiosk_headers(response):
        is_kiosk = getattr(g, '_sb_kiosk', False)
        if not is_kiosk:
            for pfx in _KIOSK_PREFIXES:
                if request.path.startswith(pfx):
                    is_kiosk = True
                    break
        if is_kiosk:
            for h in ('X-Frame-Options', 'Content-Security-Policy',
                      'X-Content-Type-Options', 'Referrer-Policy',
                      'Strict-Transport-Security'):
                response.headers.pop(h, None)
            response.headers['Access-Control-Allow-Origin'] = '*'
        return response
    try:
        app.after_request_funcs.setdefault(None, []).insert(0, _sb_kiosk_headers)
    except Exception:
        app.after_request(_sb_kiosk_headers)

    # ── Token validation (reuse mobile API token) ───────────────
    def _validate_token():
        import hmac as _hmac_mod
        token = request.args.get("token", "").strip() \
             or request.args.get("api_key", "").strip()
        if not token:
            return False
        try:
            expected = str(getattr(getattr(monitor.app_cfg, "mobile_api",
                                           None), "token", "") or "").strip()
        except Exception:
            return False
        return bool(expected and _hmac_mod.compare_digest(expected, token))

    # ── Token before_request (same pattern as wallboard) ────────
    @app.before_request
    def _sb_token_before():
        tok = request.args.get("token", "").strip() \
           or request.args.get("api_key", "").strip()
        if not tok:
            return
        g._sb_kiosk = True
        if session.get("logged_in"):
            return
        if _validate_token():
            session["logged_in"] = True
            session["login_ts"]  = _time.time()
            session["username"]  = "studioboard"
            session["role"]      = "viewer"
            if not session.get("_csrf"):
                import hashlib
                session["_csrf"] = hashlib.sha256(os.urandom(32)).hexdigest()

    # ══════════════════════════════════════════════════════════════
    # ADMIN PAGE — /hub/studioboard
    # ══════════════════════════════════════════════════════════════

    @app.get("/hub/studioboard")
    @login_required
    def studioboard_admin():
        return render_template_string(_ADMIN_TPL, build=BUILD)

    # ══════════════════════════════════════════════════════════════
    # DISPLAY FRONTEND — /studioboard/tv?token=X&studio=ID
    # ══════════════════════════════════════════════════════════════

    @app.get("/studioboard/tv")
    def studioboard_tv():
        g._sb_kiosk = True
        cfg = monitor.app_cfg
        token = request.args.get("token", "").strip() \
             or request.args.get("api_key", "").strip()
        if cfg.auth.enabled and not _validate_token():
            return ('<h2>Token required</h2>'
                    '<p>Use: /studioboard/tv?token=YOUR_TOKEN&amp;studio=STUDIO_ID</p>'), 403
        studio_id = request.args.get("studio", "").strip()
        resp = make_response(render_template_string(
            _TV_TPL, build=BUILD, wb_token=token, studio_id=studio_id))
        resp.headers.pop('X-Frame-Options', None)
        resp.headers.pop('Content-Security-Policy', None)
        resp.headers.pop('X-Content-Type-Options', None)
        resp.headers.pop('Referrer-Policy', None)
        resp.headers.pop('Strict-Transport-Security', None)
        resp.headers['Access-Control-Allow-Origin'] = '*'
        resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        session.modified = False
        return resp

    # ══════════════════════════════════════════════════════════════
    # API ROUTES
    # ══════════════════════════════════════════════════════════════

    @app.get("/api/studioboard/config")
    @login_required
    def sb_config_get():
        return jsonify(_cfg_load())

    @app.post("/api/studioboard/config")
    @login_required
    def sb_config_save():
        _cfg_save(request.get_json(force=True))
        return jsonify({"ok": True})

    @app.get("/api/studioboard/stations")
    @login_required
    def sb_stations():
        """Return available chains, inputs, and Planet Radio stations."""
        cfg = monitor.app_cfg
        chains = [{"id": ch.get("id", ""), "name": ch.get("name", "")}
                  for ch in (cfg.signal_chains or [])]
        # Build input list from hub sites
        inputs = []
        if hub_server:
            try:
                for sd in (hub_server.get_sites() or []):
                    site = sd.get("name") or sd.get("site", "")
                    for s in (sd.get("streams") or []):
                        name = (s.get("name") or "").strip()
                        if name:
                            inputs.append({"key": site + "|" + name,
                                           "site": site, "name": name})
            except Exception:
                pass
        # Planet Radio stations are fetched by the JS from
        # /api/nowplaying_stations (main app endpoint)
        return jsonify({"chains": chains, "inputs": inputs})

    # ── Studio CRUD ─────────────────────────────────────────────

    @app.post("/api/studioboard/studio")
    @login_required
    def sb_studio_create():
        data = request.get_json(force=True)
        cfg = _cfg_load()
        studio = {
            "id": str(_uuid.uuid4())[:8],
            "name": (data.get("name") or "New Studio").strip(),
            "color": data.get("color", "#17a8ff"),
            "chains": [],
            "inputs": [],
            "np_rpuid": "",
            "freq": "",
            "show_artwork": {},
            "mic_live": False,
        }
        cfg.setdefault("studios", []).append(studio)
        _cfg_save(cfg)
        return jsonify({"ok": True, "studio": studio})

    @app.post("/api/studioboard/studio/<studio_id>")
    @login_required
    def sb_studio_update(studio_id):
        data = request.get_json(force=True)
        cfg = _cfg_load()
        studio = _get_studio(cfg, studio_id)
        if not studio:
            return jsonify({"error": "Studio not found"}), 404
        for key in ("name", "color", "chains", "inputs", "np_rpuid",
                    "freq", "show_artwork"):
            if key in data:
                studio[key] = data[key]
        _cfg_save(cfg)
        return jsonify({"ok": True, "studio": studio})

    @app.delete("/api/studioboard/studio/<studio_id>")
    @login_required
    def sb_studio_delete(studio_id):
        cfg = _cfg_load()
        cfg["studios"] = [s for s in cfg.get("studios", [])
                          if s.get("id") != studio_id]
        _cfg_save(cfg)
        return jsonify({"ok": True})

    # ── Mic Live REST API ───────────────────────────────────────

    @app.post("/api/studioboard/mic/<studio_id>")
    def sb_mic_live(studio_id):
        """Set mic live status. Accepts JSON {live: true/false}.
        Auth via mobile API token in query string or header."""
        if not _validate_token():
            token_hdr = request.headers.get("Authorization", "")
            if token_hdr.startswith("Bearer "):
                # Check bearer token
                import hmac as _hmac_mod
                tok = token_hdr[7:].strip()
                try:
                    expected = str(getattr(getattr(monitor.app_cfg, "mobile_api",
                                                   None), "token", "") or "").strip()
                    if not (expected and _hmac_mod.compare_digest(expected, tok)):
                        return jsonify({"error": "Unauthorized"}), 403
                except Exception:
                    return jsonify({"error": "Unauthorized"}), 403
            else:
                return jsonify({"error": "Unauthorized"}), 403
        data = request.get_json(force=True)
        cfg = _cfg_load()
        studio = _get_studio(cfg, studio_id)
        if not studio:
            return jsonify({"error": "Studio not found"}), 404
        studio["mic_live"] = bool(data.get("live", False))
        _cfg_save(cfg)
        return jsonify({"ok": True, "mic_live": studio["mic_live"]})

    # ── Chain assignment REST API ───────────────────────────────

    @app.post("/api/studioboard/studio/<studio_id>/chains")
    def sb_assign_chains(studio_id):
        """Assign chains to a studio via REST API."""
        if not _validate_token():
            return jsonify({"error": "Unauthorized"}), 403
        data = request.get_json(force=True)
        cfg = _cfg_load()
        studio = _get_studio(cfg, studio_id)
        if not studio:
            return jsonify({"error": "Studio not found"}), 404
        studio["chains"] = data.get("chains", [])
        _cfg_save(cfg)
        return jsonify({"ok": True, "chains": studio["chains"]})

    # ── Log seen show names (called by display JS) ──────────────

    @app.post("/api/studioboard/seen_show/<studio_id>")
    @login_required
    def sb_seen_show(studio_id):
        """Log a show name so the admin can see it for artwork mapping."""
        data = request.get_json(force=True)
        show = (data.get("show") or "").strip()
        if not show:
            return jsonify({"ok": True})
        cfg = _cfg_load()
        studio = _get_studio(cfg, studio_id)
        if not studio:
            return jsonify({"ok": True})
        seen = studio.setdefault("seen_shows", [])
        if show not in seen:
            seen.insert(0, show)
            if len(seen) > 50:
                seen[:] = seen[:50]
            _cfg_save(cfg)
        return jsonify({"ok": True})

    # ── Show artwork upload ─────────────────────────────────────

    @app.post("/api/studioboard/artwork/<studio_id>/<show_name>")
    @login_required
    def sb_artwork_upload(studio_id, show_name):
        f = request.files.get("artwork")
        if not f or not f.filename:
            return jsonify({"error": "No file"}), 400
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp"):
            return jsonify({"error": "Unsupported format"}), 400
        data = f.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            return jsonify({"error": "File too large (max 2 MB)"}), 400
        os.makedirs(_ART_DIR, exist_ok=True)
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', show_name)[:50]
        fname = studio_id + "_" + safe_name + ext
        # Remove old files for this show
        for old in os.listdir(_ART_DIR):
            if old.startswith(studio_id + "_" + safe_name + "."):
                os.remove(os.path.join(_ART_DIR, old))
        with open(os.path.join(_ART_DIR, fname), "wb") as out:
            out.write(data)
        # Update config
        cfg = _cfg_load()
        studio = _get_studio(cfg, studio_id)
        if studio:
            studio.setdefault("show_artwork", {})[show_name] = fname
            _cfg_save(cfg)
        return jsonify({"ok": True, "filename": fname})

    @app.get("/studioboard/art/<path:filename>")
    @login_required
    def sb_artwork_serve(filename):
        safe = re.sub(r'[^a-zA-Z0-9._-]', '', filename)
        path = os.path.join(_ART_DIR, safe)
        if os.path.exists(path):
            return send_file(path, max_age=300)
        return '', 404

    # ── Live data endpoint for display ──────────────────────────

    @app.get("/api/studioboard/data")
    @login_required
    def sb_data():
        cfg_app = monitor.app_cfg
        sb_cfg = _cfg_load()
        now = _time.time()
        studios_out = []

        # Get chain status
        chain_status = {}
        try:
            for chain in (cfg_app.signal_chains or []):
                cid = chain.get("id", "")
                if hub_server and cid:
                    maint = hub_server._chain_maintenance.get(cid, {})
                    result = hub_server.eval_chain(chain, maintenance=maint)
                    internal_state = hub_server._chain_fault_state.get(cid)
                    if internal_state == "alerted":
                        result["display_status"] = "fault"
                    elif internal_state == "pending":
                        result["display_status"] = "pending"
                    else:
                        result["display_status"] = result.get("status", "unknown")
                    chain_status[cid] = result
        except Exception:
            pass

        # Get live levels from hub
        levels = {}
        if hub_server:
            try:
                with hub_server._lock:
                    for site_name, sdata in hub_server._sites.items():
                        if not sdata.get("_approved"):
                            continue
                        for s in (sdata.get("streams") or []):
                            key = site_name + "|" + (s.get("name") or "")
                            levels[key] = {
                                "level_dbfs": s.get("level_dbfs", -90.0),
                                "peak_dbfs": s.get("peak_dbfs", -90.0),
                                "level_dbfs_l": s.get("level_dbfs_l"),
                                "level_dbfs_r": s.get("level_dbfs_r"),
                                "stereo": bool(s.get("stereo")),
                                "silence_active": bool(s.get("silence_active")),
                            }
            except Exception:
                pass

        # Now playing data is fetched by the JS from /api/nowplaying/<rpuid>

        for studio in sb_cfg.get("studios", []):
            sid = studio.get("id", "")
            # Resolve chains
            s_chains = []
            for cid in (studio.get("chains") or []):
                cs = chain_status.get(cid, {})
                s_chains.append({
                    "id": cid,
                    "name": cs.get("name", cid),
                    "status": cs.get("display_status", "unknown"),
                    "sla_pct": cs.get("sla_pct"),
                })

            # Resolve inputs
            s_inputs = []
            for ikey in (studio.get("inputs") or []):
                lev = levels.get(ikey, {})
                parts = ikey.split("|", 1)
                s_inputs.append({
                    "key": ikey,
                    "name": parts[1] if len(parts) > 1 else ikey,
                    "site": parts[0] if len(parts) > 1 else "",
                    **lev,
                })

            studios_out.append({
                "id": sid,
                "name": studio.get("name", ""),
                "color": studio.get("color", "#17a8ff"),
                "freq": studio.get("freq", ""),
                "mic_live": studio.get("mic_live", False),
                "chains": s_chains,
                "inputs": s_inputs,
                "np_rpuid": studio.get("np_rpuid", ""),
                "show_artwork_map": studio.get("show_artwork", {}),
                "seen_shows": studio.get("seen_shows", []),
            })

        return jsonify({"studios": studios_out})


# ══════════════════════════════════════════════════════════════════
# ADMIN TEMPLATE
# ══════════════════════════════════════════════════════════════════

_ADMIN_TPL = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="csrf-token" content="{{csrf_token()}}">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Studio Board Admin — SignalScope</title>
<style nonce="{{csp_nonce()}}">
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--tx);font-size:13px;min-height:100vh}
header{background:linear-gradient(180deg,rgba(10,31,65,.96),rgba(9,24,48,.96));border-bottom:1px solid var(--bor);padding:12px 20px;display:flex;align-items:center;gap:12px}
.hdr-title{font-size:16px;font-weight:800}
.hdr-sub{font-size:11px;color:var(--mu)}
.btn{display:inline-flex;align-items:center;gap:4px;padding:6px 14px;border-radius:8px;font-size:12px;font-weight:600;cursor:pointer;border:none;color:var(--tx);background:var(--bor);text-decoration:none;font-family:inherit}
.btn:hover{filter:brightness(1.2)}.btn.bp{background:var(--acc);color:#fff}.btn.bd{background:var(--al);color:#fff}
.btn.bs{padding:4px 10px;font-size:11px;border-radius:6px}
.main{padding:20px;max-width:900px;margin:0 auto}
.card{background:var(--sur);border:1px solid var(--bor);border-radius:12px;margin-bottom:16px;overflow:hidden}
.ch{padding:10px 14px;display:flex;align-items:center;gap:10px;border-bottom:1px solid var(--bor);background:linear-gradient(180deg,#143766,#102b54);font-size:12px;font-weight:700;color:var(--acc);text-transform:uppercase;letter-spacing:.06em}
.cb{padding:14px}
.field{display:flex;flex-direction:column;gap:4px;margin-bottom:12px}
.field label{font-size:11px;color:var(--mu);font-weight:600;text-transform:uppercase;letter-spacing:.05em}
.field input,.field select{background:#0d1e40;border:1px solid var(--bor);border-radius:6px;color:var(--tx);padding:7px 10px;font-size:13px;font-family:inherit}
.field input:focus,.field select:focus{border-color:var(--acc);outline:none}
.field input[type="color"]{width:50px;height:32px;padding:2px;cursor:pointer}
.row{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end}
.row>.field{flex:1;min-width:150px}
.tag{display:inline-flex;align-items:center;gap:4px;background:rgba(23,168,255,.12);color:var(--acc);padding:3px 8px;border-radius:6px;font-size:11px;font-weight:600}
.tag .x{cursor:pointer;opacity:.6;font-size:13px}.tag .x:hover{opacity:1}
.tags{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}
.empty{color:var(--mu);font-size:12px;text-align:center;padding:40px 20px}
.studio-hdr{display:flex;align-items:center;gap:10px;flex:1}
.studio-color{width:14px;height:14px;border-radius:50%;flex-shrink:0;border:2px solid rgba(255,255,255,.15)}
.art-grid{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
.art-item{display:flex;align-items:center;gap:8px;background:rgba(255,255,255,.03);border:1px solid var(--bor);border-radius:8px;padding:6px 10px}
.art-item img{width:40px;height:40px;border-radius:6px;object-fit:cover}
.art-item .art-name{font-size:11px;font-weight:600}
.msg{padding:8px 14px;border-radius:8px;font-size:12px;margin-bottom:12px}
.msg-ok{background:#0f2318;color:var(--ok);border:1px solid #166534}
.msg-err{background:#2a0a0a;color:var(--al);border:1px solid #991b1b}
select[multiple]{min-height:100px}
</style>
</head>
<body>
<header>
  <span style="font-size:22px">🎙</span>
  <div>
    <div class="hdr-title">Studio Board</div>
    <div class="hdr-sub">Configure studio displays for SignalScope</div>
  </div>
  <div style="margin-left:auto;display:flex;gap:8px">
    <a class="btn bs" href="/">← Hub</a>
    <button class="btn bp bs" id="btn-add-studio">+ Add Studio</button>
  </div>
</header>
<div class="main">
  <div id="studios-list"><div class="empty">Loading…</div></div>
</div>

<script nonce="{{csp_nonce()}}">
(function(){
'use strict';
var _studios=[], _chains=[], _inputs=[], _npStations=[];
function _e(s){return(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function _csrf(){return(document.querySelector('meta[name="csrf-token"]')||{}).content||''}
function _post(url,data){return fetch(url,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':_csrf()},body:JSON.stringify(data)})}
function _del(url){return fetch(url,{method:'DELETE',credentials:'same-origin',headers:{'X-CSRFToken':_csrf()}})}

function loadAll(){
  Promise.all([
    fetch('/api/studioboard/config',{credentials:'same-origin'}).then(function(r){return r.json()}),
    fetch('/api/studioboard/stations',{credentials:'same-origin'}).then(function(r){return r.json()}),
    fetch('/api/nowplaying_stations',{credentials:'same-origin'}).then(function(r){return r.ok?r.json():[]}).catch(function(){return[]})
  ]).then(function(res){
    _studios=(res[0].studios||[]);
    _chains=res[1].chains||[];_inputs=res[1].inputs||[];
    _npStations=res[2]||[];
    render();
  });
}

function render(){
  var el=document.getElementById('studios-list');
  if(!_studios.length){el.innerHTML='<div class="empty">No studios configured yet. Click "+ Add Studio" to get started.</div>';return}
  // All-studios display URL
  var html='<div class="card"><div class="cb" style="padding:10px 14px">'
    +'<div style="font-size:12px;font-weight:700;color:var(--acc);margin-bottom:4px">All Studios Display URL (Yodeck)</div>'
    +'<div style="font-size:12px;color:var(--mu);word-break:break-all">/studioboard/tv?token=YOUR_TOKEN</div>'
    +'<div style="font-size:10px;color:var(--mu);margin-top:4px">Shows all studios in a grid. Add <code style="color:var(--acc)">&amp;studio=ID</code> for a single studio.</div>'
    +'</div></div>';
  _studios.forEach(function(st){
    html+='<div class="card" data-sid="'+_e(st.id)+'">';
    html+='<div class="ch"><div class="studio-hdr"><span class="studio-color" style="background:'+_e(st.color)+'"></span>'+_e(st.name)+'</div>';
    html+='<button class="btn bd bs" data-delete="'+_e(st.id)+'">Delete</button></div>';
    html+='<div class="cb">';
    // Name + colour
    html+='<div class="row"><div class="field"><label>Studio Name</label><input data-field="name" data-sid="'+_e(st.id)+'" value="'+_e(st.name)+'"></div>';
    html+='<div class="field"><label>Colour</label><input type="color" data-field="color" data-sid="'+_e(st.id)+'" value="'+_e(st.color||'#17a8ff')+'"></div>';
    html+='<div class="field"><label>Frequency</label><input data-field="freq" data-sid="'+_e(st.id)+'" value="'+_e(st.freq||'')+'" placeholder="97.4 FM | DAB"></div></div>';

    // Chains
    html+='<div class="field"><label>Broadcast Chains</label>';
    html+='<select data-chains="'+_e(st.id)+'" multiple>';
    _chains.forEach(function(ch){
      var sel=(st.chains||[]).indexOf(ch.id)>=0?' selected':'';
      html+='<option value="'+_e(ch.id)+'"'+sel+'>'+_e(ch.name)+'</option>';
    });
    html+='</select></div>';

    // Inputs for meters
    html+='<div class="field"><label>Level Meter Inputs</label>';
    html+='<select data-inputs="'+_e(st.id)+'" multiple>';
    _inputs.forEach(function(inp){
      var sel=(st.inputs||[]).indexOf(inp.key)>=0?' selected':'';
      html+='<option value="'+_e(inp.key)+'"'+sel+'>'+_e(inp.site)+' — '+_e(inp.name)+'</option>';
    });
    html+='</select></div>';

    // Planet Radio
    html+='<div class="field"><label>Now Playing (Planet Radio)</label>';
    html+='<select data-np="'+_e(st.id)+'">';
    html+='<option value="">— None —</option>';
    _npStations.forEach(function(s){
      var sel=(st.np_rpuid===s.rpuid)?' selected':'';
      html+='<option value="'+_e(s.rpuid)+'"'+sel+'>'+_e(s.name)+'</option>';
    });
    html+='</select></div>';

    // Show artwork
    html+='<div class="field"><label>Show Artwork / Presenter Images</label>';
    html+='<div style="font-size:11px;color:var(--mu);margin-bottom:6px">Upload images matched to show names from the Planet Radio API. When a show is on air, its image appears on the display.</div>';
    var artMap=st.show_artwork||{};
    if(Object.keys(artMap).length){
      html+='<div class="art-grid">';
      Object.keys(artMap).forEach(function(showName){
        html+='<div class="art-item"><img src="/studioboard/art/'+_e(artMap[showName])+'" alt=""><div><div class="art-name">'+_e(showName)+'</div></div></div>';
      });
      html+='</div>';
    }
    // Seen show names — logged automatically from the API
    var seen=st.seen_shows||[];
    if(seen.length){
      html+='<div style="margin-top:8px"><div style="font-size:10px;color:var(--mu);margin-bottom:4px">Recently seen shows (click to select):</div><div class="tags">';
      seen.forEach(function(s){
        var hasArt=artMap[s]?'  ✓':'';
        html+='<span class="tag" data-seen-show="'+_e(st.id)+'" data-show="'+_e(s)+'" style="cursor:pointer">'+_e(s)+hasArt+'</span>';
      });
      html+='</div></div>';
    }
    html+='<div style="margin-top:8px;display:flex;gap:8px;align-items:center">';
    html+='<input data-art-show="'+_e(st.id)+'" placeholder="Show name (exact match)" style="background:#0d1e40;border:1px solid var(--bor);border-radius:6px;color:var(--tx);padding:5px 8px;font-size:12px;flex:1;font-family:inherit">';
    html+='<button class="btn bp bs" data-art-upload="'+_e(st.id)+'">Upload Image</button></div>';
    html+='</div>';

    // Save button
    html+='<div style="margin:12px 0 8px"><button class="btn bp" data-save-studio="'+_e(st.id)+'">Save Changes</button>'
      +'<span class="save-msg" data-save-msg="'+_e(st.id)+'" style="margin-left:10px;font-size:12px;color:var(--ok);display:none">Saved!</span></div>';

    // Mic Live API info
    html+='<div class="field"><label>Mic Live API</label>';
    html+='<div style="font-size:11px;color:var(--mu)">POST <code style="color:var(--acc)">/api/studioboard/mic/'+_e(st.id)+'?token=YOUR_TOKEN</code> with <code style="color:var(--acc)">{"live": true}</code> or <code style="color:var(--acc)">{"live": false}</code></div>';
    html+='</div>';

    // Display URL
    html+='<div class="field"><label>Display URL (for Yodeck)</label>';
    html+='<div style="font-size:11px;color:var(--acc);word-break:break-all">/studioboard/tv?token=YOUR_TOKEN&amp;studio='+_e(st.id)+'</div>';
    html+='</div>';

    html+='</div></div>';
  });
  el.innerHTML=html;
}

function saveStudio(sid,field,value){
  var data={};data[field]=value;
  _post('/api/studioboard/studio/'+encodeURIComponent(sid),data).then(function(){loadAll()});
}

document.getElementById('btn-add-studio').addEventListener('click',function(){
  _post('/api/studioboard/studio',{name:'New Studio'}).then(function(){loadAll()});
});

function saveFullStudio(sid){
  var card=document.querySelector('.card[data-sid="'+sid+'"]');if(!card)return;
  var data={};
  var nameInput=card.querySelector('[data-field="name"]');if(nameInput)data.name=nameInput.value;
  var colorInput=card.querySelector('[data-field="color"]');if(colorInput)data.color=colorInput.value;
  var freqInput=card.querySelector('[data-field="freq"]');if(freqInput)data.freq=freqInput.value;
  var chainsSel=card.querySelector('[data-chains]');if(chainsSel)data.chains=Array.from(chainsSel.selectedOptions).map(function(o){return o.value});
  var inputsSel=card.querySelector('[data-inputs]');if(inputsSel)data.inputs=Array.from(inputsSel.selectedOptions).map(function(o){return o.value});
  var npSel=card.querySelector('[data-np]');if(npSel)data.np_rpuid=npSel.value;
  _post('/api/studioboard/studio/'+encodeURIComponent(sid),data).then(function(){
    var msg=document.querySelector('[data-save-msg="'+sid+'"]');
    if(msg){msg.style.display='inline';setTimeout(function(){msg.style.display='none'},2000)}
    loadAll();
  });
}

document.getElementById('studios-list').addEventListener('click',function(e){
  var saveBtn=e.target.closest('[data-save-studio]');
  if(saveBtn){saveFullStudio(saveBtn.dataset.saveStudio);return}
  var del=e.target.closest('[data-delete]');
  if(del){if(confirm('Delete this studio?'))_del('/api/studioboard/studio/'+encodeURIComponent(del.dataset.delete)).then(function(){loadAll()});return}
  var seenTag=e.target.closest('[data-seen-show]');
  if(seenTag){var sid=seenTag.dataset.seenShow;var showName=seenTag.dataset.show;
    var inp=document.querySelector('input[data-art-show="'+sid+'"]');
    if(inp)inp.value=showName;return}
  var artBtn=e.target.closest('[data-art-upload]');
  if(artBtn){
    var sid=artBtn.dataset.artUpload;
    var showInput=document.querySelector('input[data-art-show="'+sid+'"]');
    var showName=(showInput?showInput.value:'').trim();
    if(!showName){alert('Enter the show name first');return}
    var inp=document.createElement('input');inp.type='file';inp.accept='image/*';
    inp.onchange=function(){if(!inp.files[0])return;var fd=new FormData();fd.append('artwork',inp.files[0]);
      fetch('/api/studioboard/artwork/'+encodeURIComponent(sid)+'/'+encodeURIComponent(showName),
        {method:'POST',credentials:'same-origin',headers:{'X-CSRFToken':_csrf()},body:fd})
        .then(function(r){return r.json()}).then(function(d){if(d.ok)loadAll()});
    };inp.click();
  }
});

/* Changes saved via the Save button — no auto-save on change */

loadAll();
})();
</script>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════════
# TV DISPLAY TEMPLATE
# ══════════════════════════════════════════════════════════════════

_TV_TPL = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="csrf-token" content="{{csrf_token()}}">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Studio Board — SignalScope</title>
<link rel="icon" type="image/x-icon" href="/static/signalscope_icon.png">
<style nonce="{{csp_nonce()}}">
@font-face{font-family:'BauerMediaSans';src:url('/wallboard/asset/BauerMediaSans-Regular.otf{% if wb_token %}?token={{wb_token}}{% endif %}') format('opentype');font-weight:400;font-style:normal;font-display:swap}
@font-face{font-family:'BauerMediaSans';src:url('/wallboard/asset/BauerMediaSans-Bold.otf{% if wb_token %}?token={{wb_token}}{% endif %}') format('opentype');font-weight:700;font-style:normal;font-display:swap}
@font-face{font-family:'BauerMediaSans';src:url('/wallboard/asset/BauerMediaSans-Light.otf{% if wb_token %}?token={{wb_token}}{% endif %}') format('opentype');font-weight:300;font-style:normal;font-display:swap}
:root{--bg:#4700A3;--acc:#fff;--ok:#22c55e;--al:#ef4444;--tx:#fff;--mu:rgba(255,255,255,.55)}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;overflow:hidden}
body{
  font-family:'BauerMediaSans',system-ui,sans-serif;
  background:var(--bg);color:var(--tx);
  display:flex;flex-direction:column;
  -webkit-user-select:none;user-select:none;
}
body::after{
  content:'';position:fixed;inset:0;pointer-events:none;z-index:0;
  background:radial-gradient(ellipse 800px 400px at 30% 20%,rgba(88,0,202,.4),transparent),
    radial-gradient(ellipse 600px 300px at 70% 80%,rgba(63,20,156,.3),transparent);
}
.sb-wrap{position:relative;z-index:1;flex:1;display:flex;flex-direction:column;padding:24px 32px}

/* ═══ Header ═══ */
.sb-hdr{display:flex;align-items:center;gap:16px;margin-bottom:20px}
.sb-studio-name{font-size:36px;font-weight:700;letter-spacing:-.02em}
.sb-freq{font-size:14px;color:var(--mu);font-weight:400}
.sb-status{margin-left:auto;display:flex;align-items:center;gap:12px}
.sb-chain-badge{
  display:flex;align-items:center;gap:6px;
  padding:8px 18px;border-radius:12px;font-size:14px;font-weight:700;
}
.sb-chain-badge.ok{background:rgba(34,197,94,.15);color:var(--ok);border:1px solid rgba(34,197,94,.3)}
.sb-chain-badge.fault{background:rgba(239,68,68,.15);color:var(--al);border:1px solid rgba(239,68,68,.35);animation:sb-blink 1.2s ease-in-out infinite}
@keyframes sb-blink{0%,100%{opacity:1}50%{opacity:.55}}
.sb-chain-dot{width:10px;height:10px;border-radius:50%;background:currentColor;box-shadow:0 0 8px currentColor}

/* ═══ Mic Live ═══ */
.sb-mic{
  padding:14px 32px;border-radius:16px;text-align:center;
  font-size:28px;font-weight:700;letter-spacing:.06em;
  transition:all .3s ease;margin-bottom:20px;
}
.sb-mic.live{
  background:linear-gradient(135deg,#c81e1e,#ef4444);color:#fff;
  box-shadow:0 0 40px rgba(239,68,68,.3),0 0 80px rgba(239,68,68,.15);
  animation:mic-pulse 1.5s ease-in-out infinite;
}
.sb-mic.clear{
  background:rgba(255,255,255,.06);color:rgba(255,255,255,.25);
  border:1px solid rgba(255,255,255,.1);
}
@keyframes mic-pulse{0%,100%{box-shadow:0 0 40px rgba(239,68,68,.3),0 0 80px rgba(239,68,68,.15)}50%{box-shadow:0 0 60px rgba(239,68,68,.5),0 0 120px rgba(239,68,68,.25)}}

/* ═══ Show / Now Playing ═══ */
.sb-show{
  flex:1;display:flex;align-items:center;gap:32px;
  padding:20px 0;min-height:0;
}
.sb-art{
  width:220px;height:220px;border-radius:24px;object-fit:cover;flex-shrink:0;
  box-shadow:0 8px 40px rgba(0,0,0,.4);border:2px solid rgba(255,255,255,.1);
  background:rgba(255,255,255,.06);
}
.sb-art-placeholder{
  width:220px;height:220px;border-radius:24px;flex-shrink:0;
  background:rgba(255,255,255,.06);border:2px solid rgba(255,255,255,.06);
  display:flex;align-items:center;justify-content:center;
  font-size:64px;opacity:.3;
}
.sb-info{flex:1;min-width:0;display:flex;flex-direction:column;gap:8px}
.sb-show-name{font-size:28px;font-weight:700;letter-spacing:-.01em}
.sb-track{font-size:22px;font-weight:300;color:rgba(255,255,255,.85);margin-top:4px}
.sb-artist{font-size:18px;font-weight:700;color:#fff}
.sb-np-label{font-size:11px;color:var(--mu);text-transform:uppercase;letter-spacing:.06em;font-weight:700;margin-top:12px}

/* ═══ Meters ═══ */
.sb-meters{margin-top:auto;padding-top:16px}
.sb-meter{display:flex;align-items:center;gap:12px;margin-bottom:8px}
.sb-meter-label{font-size:12px;font-weight:600;color:var(--mu);min-width:180px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sb-meter-bar{flex:1;height:20px;background:rgba(255,255,255,.06);border-radius:6px;overflow:hidden;position:relative}
.sb-meter-fill{
  height:100%;border-radius:6px;
  background:linear-gradient(90deg,#22c55e 0%,#22c55e 75%,#f59e0b 87%,#ef4444 100%);
  transition:width .15s ease;
}
.sb-meter-peak{
  position:absolute;top:0;bottom:0;width:3px;background:#fff;border-radius:2px;
  box-shadow:0 0 6px rgba(255,255,255,.5);transition:left .15s ease;
}
.sb-meter-val{font-size:14px;font-weight:700;font-variant-numeric:tabular-nums;min-width:70px;text-align:right}
.sb-meter-val.silent{color:var(--mu)}

/* ═══ Multi-studio grid ═══ */
.sb-grid{flex:1;display:grid;gap:16px;padding:0;min-height:0}
.sb-grid-card{
  background:rgba(255,255,255,.06);border:1.5px solid rgba(255,255,255,.1);
  border-radius:20px;padding:20px;display:flex;flex-direction:column;
  overflow:hidden;position:relative;
}
.sb-grid-card .sb-mic{font-size:18px;padding:8px 16px;border-radius:10px;margin-bottom:12px}
.sb-grid-card .sb-art{width:120px;height:120px;border-radius:16px}
.sb-grid-card .sb-art-placeholder{width:120px;height:120px;border-radius:16px;font-size:40px}
.sb-grid-card .sb-show-name{font-size:20px}
.sb-grid-card .sb-track{font-size:16px}
.sb-grid-card .sb-artist{font-size:14px}
.sb-grid-card .sb-studio-name{font-size:22px}

/* ═══ Clock ═══ */
.sb-clock{font-size:28px;font-weight:300;font-variant-numeric:tabular-nums;letter-spacing:.05em;text-align:right}
.sb-clock-date{font-size:11px;color:var(--mu);text-align:right}
</style>
</head>
<body>
<div class="sb-wrap">
  <div id="sb-content">
    <div style="text-align:center;padding:60px;color:var(--mu)">
      <div style="font-size:48px;margin-bottom:12px">🎙</div>
      <div style="font-size:18px;font-weight:700">Connecting…</div>
    </div>
  </div>
</div>

<script nonce="{{csp_nonce()}}">
(function(){
'use strict';
var _wbTk='{{wb_token|default("")}}';
var _studioId='{{studio_id|default("")}}';
function _tkUrl(u){if(!_wbTk)return u;return u+(u.indexOf('?')>=0?'&':'?')+'token='+encodeURIComponent(_wbTk)}
function _e(s){return(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}

var POLL_MS=1500,NP_MS=10000,DB_FLOOR=-80;
var _lastData=null,_npData={},_seenShows={};

function levToW(db){return Math.max(0,Math.min(100,(db-DB_FLOOR)/(-DB_FLOOR)*100))}
function fmtLev(db){if(db<=DB_FLOOR)return'— dB';return(db>=0?'+':'')+db.toFixed(1)+' dB'}

function _getNp(studio){
  var rpuid=studio.np_rpuid||'';
  return rpuid?(_npData[rpuid]||{}):{};
}
function _getShowArt(studio){
  var np=_getNp(studio);
  var showName=(np.show||'').trim();
  // 1. Uploaded artwork matched by show name (highest priority)
  var uploaded=(studio.show_artwork_map||{})[showName];
  if(uploaded)return {type:'file',src:uploaded};
  // 2. Show image from Planet Radio API
  if(np.show_image)return {type:'url',src:np.show_image};
  // 3. Track artwork
  if(np.artwork)return {type:'url',src:np.artwork};
  return null;
}

function renderSingle(studio){
  var el=document.getElementById('sb-content');
  var np=_getNp(studio);
  var chainHtml='';
  (studio.chains||[]).forEach(function(ch){
    var cls=ch.status==='fault'?'fault':'ok';
    var txt=ch.status==='fault'?'FAULT':'ON AIR';
    chainHtml+='<div class="sb-chain-badge '+cls+'"><span class="sb-chain-dot"></span>'+_e(ch.name)+' — '+txt+'</div>';
  });

  var micCls=studio.mic_live?'live':'clear';
  var micTxt=studio.mic_live?'MIC LIVE':'CLEAR';

  var artHtml='';
  var art=_getShowArt(studio);
  if(art&&art.type==='file'){
    artHtml='<img class="sb-art" src="'+_tkUrl('/studioboard/art/'+_e(art.src))+'" alt="">';
  }else if(art&&art.type==='url'){
    artHtml='<img class="sb-art" src="'+_e(art.src)+'" alt="">';
  }else{
    artHtml='<div class="sb-art-placeholder">🎙</div>';
  }

  var npHtml='';
  if(np.show){
    npHtml+='<div class="sb-show-name">'+_e(np.show)+'</div>';
  }
  if(np.title||np.artist){
    npHtml+='<div class="sb-np-label">Now Playing</div>';
    if(np.artist)npHtml+='<div class="sb-artist">'+_e(np.artist)+'</div>';
    if(np.title)npHtml+='<div class="sb-track">'+_e(np.title)+'</div>';
  }

  var metersHtml='';
  (studio.inputs||[]).forEach(function(inp){
    var lev=inp.level_dbfs!=null?inp.level_dbfs:DB_FLOOR;
    var peak=inp.peak_dbfs!=null?inp.peak_dbfs:DB_FLOOR;
    var w=levToW(lev),pw=levToW(peak);
    var cls=lev<=DB_FLOOR?'silent':'';
    metersHtml+='<div class="sb-meter">'
      +'<div class="sb-meter-label">'+_e(inp.name)+'</div>'
      +'<div class="sb-meter-bar"><div class="sb-meter-fill" style="width:'+w+'%"></div>'
      +'<div class="sb-meter-peak" style="left:'+pw+'%"></div></div>'
      +'<div class="sb-meter-val '+cls+'">'+fmtLev(lev)+'</div></div>';
  });

  var html='<div class="sb-hdr">'
    +'<div><div class="sb-studio-name">'+_e(studio.name)+'</div>'
    +(studio.freq?'<div class="sb-freq">'+_e(studio.freq)+'</div>':'')+'</div>'
    +'<div class="sb-status">'+chainHtml+'</div>'
    +'<div><div class="sb-clock" id="sb-clock"></div><div class="sb-clock-date" id="sb-clock-date"></div></div></div>'
    +'<div class="sb-mic '+micCls+'">'+micTxt+'</div>'
    +'<div class="sb-show">'+artHtml+'<div class="sb-info">'+npHtml+'</div></div>'
    +'<div class="sb-meters">'+metersHtml+'</div>';
  el.innerHTML=html;
  _tick();
}

function renderGrid(studios){
  var el=document.getElementById('sb-content');
  var n=studios.length;
  var cols=n<=2?n:n<=4?2:3;
  var html='<div class="sb-grid" style="grid-template-columns:repeat('+cols+',1fr)">';
  studios.forEach(function(studio){
    var np=_getNp(studio);
    var micCls=studio.mic_live?'live':'clear';
    var micTxt=studio.mic_live?'MIC LIVE':'CLEAR';
    var chainSt='ok';
    (studio.chains||[]).forEach(function(ch){if(ch.status==='fault')chainSt='fault'});

    var artHtml='';
    var art=_getShowArt(studio);
    if(art&&art.type==='file'){artHtml='<img class="sb-art" src="'+_tkUrl('/studioboard/art/'+_e(art.src))+'" alt="">';}
    else if(art&&art.type==='url'){artHtml='<img class="sb-art" src="'+_e(art.src)+'" alt="">';}
    else{artHtml='<div class="sb-art-placeholder">🎙</div>';}

    html+='<div class="sb-grid-card" style="border-color:rgba('+_hexToRgb(studio.color||'#17a8ff')+',.3)">'
      +'<div style="display:flex;align-items:center;gap:12px;margin-bottom:8px">'
      +'<div class="sb-studio-name" style="flex:1">'+_e(studio.name)+'</div>'
      +'<div class="sb-chain-badge '+chainSt+'"><span class="sb-chain-dot"></span>'+(chainSt==='fault'?'FAULT':'ON AIR')+'</div></div>'
      +'<div class="sb-mic '+micCls+'">'+micTxt+'</div>'
      +'<div class="sb-show" style="gap:16px">'+artHtml
      +'<div class="sb-info">'
      +(np.show?'<div class="sb-show-name">'+_e(np.show)+'</div>':'')
      +(np.artist?'<div class="sb-artist">'+_e(np.artist)+'</div>':'')
      +(np.title?'<div class="sb-track">'+_e(np.title)+'</div>':'')
      +'</div></div>';

    // Meters
    (studio.inputs||[]).forEach(function(inp){
      var lev=inp.level_dbfs!=null?inp.level_dbfs:DB_FLOOR;
      var w=levToW(lev);
      html+='<div class="sb-meter"><div class="sb-meter-label">'+_e(inp.name)+'</div>'
        +'<div class="sb-meter-bar"><div class="sb-meter-fill" style="width:'+w+'%"></div></div>'
        +'<div class="sb-meter-val'+(lev<=DB_FLOOR?' silent':'')+'">'+fmtLev(lev)+'</div></div>';
    });

    html+='</div>';
  });
  html+='</div>';
  el.innerHTML=html;
}

function _hexToRgb(hex){hex=hex.replace('#','');if(hex.length===3)hex=hex[0]+hex[0]+hex[1]+hex[1]+hex[2]+hex[2];var n=parseInt(hex,16);return((n>>16)&255)+','+((n>>8)&255)+','+(n&255)}

function poll(){
  fetch(_tkUrl('/api/studioboard/data'),{credentials:'same-origin'})
    .then(function(r){return r.json()})
    .then(function(d){
      _lastData=d;
      var studios=d.studios||[];
      if(_studioId){
        var match=null;
        studios.forEach(function(s){if(s.id===_studioId)match=s});
        if(match)renderSingle(match);
        else document.getElementById('sb-content').innerHTML='<div style="text-align:center;padding:60px;color:var(--mu)"><div style="font-size:18px;font-weight:700">Studio not found: '+_e(_studioId)+'</div></div>';
      }else{
        if(studios.length===1)renderSingle(studios[0]);
        else if(studios.length)renderGrid(studios);
      }
    }).catch(function(){});
}

var _DAYS=['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
var _MONTHS=['January','February','March','April','May','June','July','August','September','October','November','December'];
function _tick(){
  var d=new Date(),h=d.getHours(),m=d.getMinutes(),s=d.getSeconds();
  var ce=document.getElementById('sb-clock');
  if(ce)ce.textContent=(h<10?'0':'')+h+':'+(m<10?'0':'')+m+':'+(s<10?'0':'')+s;
  var de=document.getElementById('sb-clock-date');
  if(de)de.textContent=_DAYS[d.getDay()]+' '+d.getDate()+' '+_MONTHS[d.getMonth()];
}
setInterval(_tick,1000);_tick();

function npPoll(){
  if(!_lastData)return;
  (_lastData.studios||[]).forEach(function(st){
    var rpuid=st.np_rpuid;if(!rpuid)return;
    fetch(_tkUrl('/api/nowplaying/'+encodeURIComponent(rpuid)),{credentials:'same-origin'})
      .then(function(r){return r.ok?r.json():{}})
      .then(function(d){
        _npData[rpuid]=d;
        // Log seen show name
        var show=(d.show||'').trim();
        if(show&&!_seenShows[st.id+'|'+show]){
          _seenShows[st.id+'|'+show]=true;
          fetch(_tkUrl('/api/studioboard/seen_show/'+encodeURIComponent(st.id)),
            {method:'POST',credentials:'same-origin',
             headers:{'Content-Type':'application/json'},
             body:JSON.stringify({show:show})}).catch(function(){});
        }
      }).catch(function(){});
  });
}
poll();npPoll();setInterval(poll,POLL_MS);setInterval(npPoll,NP_MS);
})();
</script>
</body>
</html>"""
