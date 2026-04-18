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
    "version":  "3.10.3",
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
                    "freq", "show_artwork", "zetta_station_key"):
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

    # ── Station assign REST API ─────────────────────────────────
    # POST /api/studioboard/assign
    # Body: {studio_id, chain_id OR chain_name, [color], [clear:true]}
    # Replaces the studio's chain assignment in one call — ideal for
    # automation systems / broadcast consoles that want to say
    # "Studio 1 is now Downtown FM".

    @app.post("/api/studioboard/assign")
    def sb_assign_station():
        """Assign a chain/station to a studio by name or ID.
        Replaces the current chain assignment. Auth via mobile API token."""
        if not _validate_token():
            token_hdr = request.headers.get("Authorization", "")
            if token_hdr.startswith("Bearer "):
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

        data       = request.get_json(force=True)
        studio_id  = (data.get("studio_id")  or "").strip()
        chain_id   = (data.get("chain_id")   or "").strip()
        chain_name = (data.get("chain_name") or "").strip()
        color      = (data.get("color")      or "").strip()
        clear      = bool(data.get("clear", False))

        cfg = _cfg_load()
        studio = _get_studio(cfg, studio_id)
        if not studio:
            return jsonify({"error": "Studio not found"}), 404

        if clear:
            studio["chains"] = []
        else:
            # Resolve chain by name when ID not supplied
            if chain_name and not chain_id:
                for ch in (monitor.app_cfg.signal_chains or []):
                    if ch.get("name", "").lower() == chain_name.lower():
                        chain_id = ch.get("id", "")
                        break
                if not chain_id:
                    return jsonify({"error": f"Chain '{chain_name}' not found"}), 404

            if chain_id:
                studio["chains"] = [chain_id]
                # Auto-borrow colour from wallboard config if not provided
                if not color:
                    try:
                        wb_cfg_path = os.path.join(_BASE_DIR, "wallboard_cfg.json")
                        with open(wb_cfg_path) as fwb:
                            wb_cfg = json.load(fwb)
                        c = (wb_cfg.get("chain_color") or {}).get(chain_id, "")
                        if c:
                            color = c
                    except Exception:
                        pass

        if color:
            studio["color"] = color

        _cfg_save(cfg)
        return jsonify({"ok": True, "studio": studio})

    # ── Artwork proxy (for Yodeck — external URLs may be blocked) ─
    @app.get("/studioboard/np_art/<rpuid>")
    @login_required
    def sb_np_art(rpuid):
        """Proxy Planet Radio artwork through same origin."""
        g._sb_kiosk = True
        try:
            import urllib.request
            # Get the nowplaying data
            url = f"https://listenapi.planetradio.co.uk/api9.2/stations_nowplaying/uk"
            req = urllib.request.Request(url, headers={"User-Agent": "SignalScope"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            items = data if isinstance(data, list) else data.get("data", [])
            for s in items:
                if str(s.get("stationCode", "")).strip() == rpuid:
                    air = s.get("stationOnAir") or {}
                    np = s.get("stationNowPlaying") or {}
                    # Try show image first, then track artwork
                    art_url = (air.get("episodeImageUrl") or air.get("episodeImage")
                              or air.get("brandImageUrl") or air.get("presenterImageUrl")
                              or air.get("showImageUrl") or air.get("imageUrl")
                              or np.get("nowPlayingImage") or "")
                    if art_url:
                        if art_url.startswith("//"): art_url = "https:" + art_url
                        req2 = urllib.request.Request(art_url, headers={"User-Agent": "SignalScope"})
                        with urllib.request.urlopen(req2, timeout=10) as resp2:
                            img_data = resp2.read()
                            ctype = resp2.headers.get_content_type() or "image/jpeg"
                        resp_out = make_response(img_data)
                        resp_out.headers['Content-Type'] = ctype
                        resp_out.headers['Cache-Control'] = 'public, max-age=60'
                        return resp_out
        except Exception:
            pass
        return '', 404

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

        # Read live Zetta station data once per request — uses the same live
        # source as /api/zetta/status_full so the TV page is always current.
        _zetta_live_fn = getattr(monitor, "_zetta_live_station_data", None)
        _zetta_live: dict = {}
        if _zetta_live_fn:
            try:
                _zetta_live = _zetta_live_fn()
            except Exception:
                pass

        # Get chain status
        chain_status = {}
        try:
            for chain in (cfg_app.signal_chains or []):
                cid = chain.get("id", "")
                if hub_server and cid:
                    maint = hub_server._chain_maintenance.get(cid, {})
                    result = hub_server.eval_chain(chain, maintenance=maint)
                    internal_state = hub_server._chain_fault_state.get(cid)
                    # Only show fault when the monitor has confirmed it (alerted)
                    # pending/adbreak/None = still deciding, show as ok
                    if internal_state == "alerted":
                        result["display_status"] = "fault"
                    elif internal_state in ("pending", "adbreak", None):
                        # Chain might look faulted in eval but the monitor
                        # hasn't confirmed yet — could be an ad break
                        raw = result.get("status", "unknown")
                        result["display_status"] = "ok" if raw == "fault" else raw
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

            _zskey = studio.get("zetta_station_key", "")
            _zetta_data = (
                _zetta_live.get(_zskey)
                if _zskey else None
            )
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
                "zetta_station_key": _zskey,
                "zetta": _zetta_data,
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
var _studios=[], _chains=[], _inputs=[], _npStations=[], _zStations=[];
function _e(s){return(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function _csrf(){return(document.querySelector('meta[name="csrf-token"]')||{}).content||''}
function _post(url,data){return fetch(url,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':_csrf()},body:JSON.stringify(data)})}
function _del(url){return fetch(url,{method:'DELETE',credentials:'same-origin',headers:{'X-CSRFToken':_csrf()}})}

function loadAll(){
  Promise.all([
    fetch('/api/studioboard/config',{credentials:'same-origin'}).then(function(r){return r.json()}),
    fetch('/api/studioboard/stations',{credentials:'same-origin'}).then(function(r){return r.json()}),
    fetch('/api/nowplaying_stations',{credentials:'same-origin'}).then(function(r){return r.ok?r.json():[]}).catch(function(){return[]}),
    fetch('/api/zetta/status_full',{credentials:'same-origin'}).then(function(r){return r.ok?r.json():{}}).catch(function(){return{}})
  ]).then(function(res){
    _studios=(res[0].studios||[]);
    _chains=res[1].chains||[];_inputs=res[1].inputs||[];
    _npStations=res[2]||[];
    // Build flat Zetta station list from status_full
    _zStations=[];
    ((res[3]||{}).instances||[]).forEach(function(inst){
      Object.keys(inst.stations||{}).forEach(function(sid){
        _zStations.push({key:inst.id+':'+sid,label:(inst.name||inst.id)+' / '+(inst.stations[sid].station_name||sid)});
      });
    });
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

    // Zetta station
    html+='<div class="field"><label>Zetta Station (queue display)</label>';
    html+='<select data-zetta="'+_e(st.id)+'">';
    html+='<option value="">— None —</option>';
    _zStations.forEach(function(z){
      var sel=(st.zetta_station_key===z.key)?' selected':'';
      html+='<option value="'+_e(z.key)+'"'+sel+'>'+_e(z.label)+'</option>';
    });
    if(!_zStations.length)html+='<option disabled>Zetta plugin not active</option>';
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
    html+='<div class="field"><label>Station Assign API</label>';
    html+='<div style="font-size:11px;color:var(--mu)">POST <code style="color:var(--acc)">/api/studioboard/assign?token=YOUR_TOKEN</code><br>';
    html+='Assign by ID: <code style="color:var(--acc)">{"studio_id":"'+_e(st.id)+'","chain_id":"CHAIN_ID"}</code><br>';
    html+='Assign by name: <code style="color:var(--acc)">{"studio_id":"'+_e(st.id)+'","chain_name":"Downtown FM"}</code><br>';
    html+='Clear / free: <code style="color:var(--acc)">{"studio_id":"'+_e(st.id)+'","clear":true}</code><br>';
    html+='<span style="opacity:.7">Optional: add <code style="color:var(--acc)">"color":"#hex"</code> to override studio colour. Omit to auto-borrow from Wallboard chain colour.</span>';
    html+='</div></div>';

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
  var zettaSel=card.querySelector('[data-zetta]');if(zettaSel)data.zetta_station_key=zettaSel.value;
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
<html lang="en"><head>
<meta charset="utf-8"><meta name="csrf-token" content="{{csrf_token()}}">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Studio Board</title>
<style nonce="{{csp_nonce()}}">
@font-face{font-family:'BM';src:url('/wallboard/asset/BauerMediaSans-Regular.otf{% if wb_token %}?token={{wb_token}}{% endif %}') format('opentype');font-weight:400}
@font-face{font-family:'BM';src:url('/wallboard/asset/BauerMediaSans-Bold.otf{% if wb_token %}?token={{wb_token}}{% endif %}') format('opentype');font-weight:700}
@font-face{font-family:'BM';src:url('/wallboard/asset/BauerMediaSans-Light.otf{% if wb_token %}?token={{wb_token}}{% endif %}') format('opentype');font-weight:300}
:root{--bg:#4700A3;--ok:#22c55e;--al:#ef4444;--tx:#fff;--mu:rgba(255,255,255,.5)}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;overflow:hidden}
body{font-family:'BM',system-ui,sans-serif;background:var(--bg);color:var(--tx);display:flex;-webkit-user-select:none;user-select:none}
body::before{content:'';position:fixed;inset:0;pointer-events:none;z-index:0;
  background:url('/wallboard/asset/_bauer_logo_white.svg{% if wb_token %}?token={{wb_token}}{% endif %}') center center/50% no-repeat;
  opacity:.04}
body::after{content:'';position:fixed;inset:0;pointer-events:none;z-index:0;
  background:radial-gradient(ellipse 900px 500px at 30% 20%,rgba(88,0,202,.4),transparent),
  radial-gradient(ellipse 700px 400px at 70% 80%,rgba(63,20,156,.3),transparent)}
#sb{position:relative;z-index:1;flex:1;display:flex;height:100%}
.cols{display:flex;flex:1;height:100%;max-width:1920px;margin:0 auto}
.col{flex:1;display:flex;position:relative;overflow:hidden;border-right:1px solid rgba(255,255,255,.06)}
.col:last-child{border-right:none}
.col::before{content:'';position:absolute;top:0;left:0;right:0;height:4px;z-index:2;
  background:linear-gradient(90deg,transparent,var(--cc,rgba(255,255,255,.2)),transparent)}
.col::after{content:'';position:absolute;top:0;left:0;right:0;height:200px;pointer-events:none;
  background:radial-gradient(ellipse at 50% 0%,var(--cg,rgba(255,255,255,.04)),transparent 70%)}
.col.fault{background:linear-gradient(180deg,rgba(239,68,68,.06),transparent 40%)}
.col.fault::before{background:linear-gradient(90deg,transparent,rgba(239,68,68,.6),transparent)}
/* MAIN panel — single vertical stack, everything centred */
.mp{flex:1;display:flex;flex-direction:column;align-items:center;
  padding:16px 20px;z-index:1;min-width:0;overflow:hidden}
/* Logo container — fixed height so all columns align below it */
.logo-wrap{height:140px;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-bottom:8px}
.logo{width:90%;max-width:300px;max-height:130px;border-radius:16px;object-fit:contain}
.logo-ph{width:130px;height:130px;border-radius:24px;
  background:rgba(255,255,255,.06);border:2px solid rgba(255,255,255,.06);
  display:flex;align-items:center;justify-content:center;
  font-size:52px;font-weight:800;color:rgba(255,255,255,.25)}
.stn{font-size:32px;font-weight:700;text-align:center;margin-bottom:2px}
.stu{font-size:30px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;
  color:#fff;text-align:center;margin-bottom:6px}
.frq{font-size:13px;color:var(--mu);text-align:center;margin-bottom:8px}
.mic{width:80%;max-width:300px;padding:10px 14px;border-radius:12px;text-align:center;
  font-size:20px;font-weight:700;letter-spacing:.06em;margin-bottom:5px;flex-shrink:0}
.mic.on{background:linear-gradient(135deg,#c81e1e,#ef4444);color:#fff;
  box-shadow:0 0 30px rgba(239,68,68,.3);animation:mp-pulse 1.5s ease-in-out infinite}
.mic.off{background:rgba(255,255,255,.04);color:rgba(255,255,255,.12);
  border:1px solid rgba(255,255,255,.05)}
@keyframes mp-pulse{0%,100%{box-shadow:0 0 30px rgba(239,68,68,.3)}
  50%{box-shadow:0 0 50px rgba(239,68,68,.5)}}
.badge{display:flex;align-items:center;justify-content:center;gap:6px;
  width:90%;padding:8px 14px;border-radius:10px;font-size:16px;
  font-weight:700;margin-bottom:4px;flex-shrink:0;white-space:nowrap}
.badge.ok{background:rgba(34,197,94,.1);color:var(--ok);border:1px solid rgba(34,197,94,.2)}
.badge.ft{background:rgba(239,68,68,.1);color:var(--al);border:1px solid rgba(239,68,68,.25);
  animation:bl 1.2s ease-in-out infinite}
@keyframes bl{0%,100%{opacity:1}50%{opacity:.5}}
.dot{width:7px;height:7px;border-radius:50%;background:currentColor;box-shadow:0 0 5px currentColor}
/* Divider */
.divider{width:50%;height:1px;background:rgba(255,255,255,.08);margin:6px 0;flex-shrink:0}
/* Artwork */
.art{width:min(200px,24vh);height:min(200px,24vh);border-radius:20px;object-fit:cover;flex-shrink:0;
  box-shadow:0 8px 36px rgba(0,0,0,.4);border:2px solid rgba(255,255,255,.1);
  background:rgba(255,255,255,.06);margin-bottom:8px}
.art-ph{width:100px;height:100px;border-radius:18px;flex-shrink:0;
  background:rgba(255,255,255,.03);border:2px solid rgba(255,255,255,.03);
  display:flex;align-items:center;justify-content:center;
  font-size:36px;opacity:.15;margin-bottom:6px}
/* Show name */
.shw{font-size:24px;font-weight:700;text-align:center;width:90%;
  line-height:1.3;margin-bottom:4px;min-height:32px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
/* Now playing divider */
.np-div{width:40%;height:1px;background:rgba(255,255,255,.06);margin:2px 0;flex-shrink:0}
.npl{font-size:12px;color:var(--mu);text-transform:uppercase;letter-spacing:.06em;
  font-weight:700;margin-bottom:2px;min-height:16px}
.anm{font-size:24px;font-weight:700;text-align:center;width:90%;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-height:32px}
.trk{font-size:22px;font-weight:300;color:rgba(255,255,255,.8);text-align:center;
  width:90%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-height:30px}
.idle{font-size:24px;color:rgba(255,255,255,.4);text-align:center;font-style:italic;min-height:32px;line-height:1.4}
/* RIGHT panel — meters */
.rp{width:7%;min-width:80px;flex-shrink:0;display:flex;gap:3px;align-items:stretch;
  padding:16px 6px;z-index:1}
.vm{display:flex;flex-direction:column;align-items:center;gap:2px;flex:1;min-width:0}
.vb{flex:1;width:100%;position:relative;
  background:linear-gradient(to top,rgba(34,197,94,.06) 0% 75%,rgba(245,158,11,.06) 75% 87.5%,rgba(239,68,68,.06) 87.5% 100%);
  border-radius:4px;overflow:hidden;border:1px solid rgba(255,255,255,.03)}
.vb::after{content:'';position:absolute;top:0;right:0;bottom:0;width:2px;
  background:linear-gradient(to top,#22c55e 0% 75%,#f59e0b 75% 87.5%,#ef4444 87.5% 100%);opacity:.2}
.vf{position:absolute;bottom:0;left:0;right:0;
  background:linear-gradient(to top,#22c55e 0%,#22c55e 75%,#f59e0b 82%,#ef4444 96%);
  border-radius:4px 4px 0 0;transition:height .1s linear}
.vp{position:absolute;left:-1px;right:-1px;height:2px;background:#fff;border-radius:1px;
  box-shadow:0 0 3px rgba(255,255,255,.5);transition:bottom .1s linear}
.vl{font-size:10px;font-weight:700;color:var(--mu);text-align:center;white-space:nowrap}
.vs{display:flex;gap:2px;flex:1;width:100%;height:100%}
.vs .vb{flex:1}
/* Zetta strip (legacy — no Zetta station key, just a compact footer) */
.zq{width:92%;margin-top:auto;padding-top:7px;flex-shrink:0;border-top:1px solid rgba(255,255,255,.1)}
.zq-spot{text-align:center;font-size:13px;font-weight:800;letter-spacing:.12em;color:#f59e0b;
  background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.2);border-radius:8px;
  padding:5px 10px;margin-bottom:4px}
.zq-np{margin-bottom:4px}
.zq-np-title{font-size:13px;font-weight:700;overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap;margin-bottom:3px;text-align:center;opacity:.9}
.zq-prog-wrap{height:3px;background:rgba(255,255,255,.1);border-radius:2px;overflow:hidden}
.zq-prog-fill{height:100%;background:rgba(255,255,255,.5);border-radius:2px;transition:width .5s linear}
.zq-row{display:flex;align-items:center;gap:5px;padding:3px 0;
  border-top:1px solid rgba(255,255,255,.05)}
.zq-next{font-size:9px;color:var(--mu);text-transform:uppercase;letter-spacing:.05em;
  flex-shrink:0;min-width:28px}
.zq-row-title{flex:1;font-size:11px;color:rgba(255,255,255,.55);overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.zq-row-spot .zq-row-title{color:rgba(245,158,11,.7);font-style:italic}
.zq-row-dur{font-size:10px;color:var(--mu);flex-shrink:0}
/* ── Full Zetta now-playing section (replaces Planet Radio text when Zetta configured) ── */
.zq-main{width:100%;padding-top:10px;flex:1;display:flex;flex-direction:column;
  align-items:center;min-height:0;border-top:1px solid rgba(255,255,255,.08)}
/* Horizontal now-playing row: artwork thumbnail + text */
.zm-now{display:flex;align-items:center;gap:14px;width:92%;margin-bottom:10px}
.zm-art{width:72px;height:72px;border-radius:12px;object-fit:cover;flex-shrink:0;
  border:2px solid rgba(255,255,255,.12);background:rgba(255,255,255,.06);
  box-shadow:0 4px 20px rgba(0,0,0,.4)}
.zm-art-ph{width:72px;height:72px;border-radius:12px;flex-shrink:0;
  background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.07);
  display:flex;align-items:center;justify-content:center;font-size:28px;opacity:.3}
.zm-text{flex:1;min-width:0}
.zm-mode{font-size:9px;font-weight:800;letter-spacing:.14em;text-transform:uppercase;
  color:rgba(255,255,255,.38);margin-bottom:4px}
.zm-artist{font-size:22px;font-weight:700;overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap;line-height:1.2}
.zm-title{font-size:17px;font-weight:300;color:rgba(255,255,255,.7);
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-top:2px}
/* Progress bar */
.zm-prog-wrap{width:92%;height:4px;background:rgba(255,255,255,.12);
  border-radius:2px;overflow:hidden;margin-bottom:3px}
.zm-prog-fill{height:100%;
  background:linear-gradient(90deg,rgba(255,255,255,.45),rgba(255,255,255,.88));
  border-radius:2px;transition:width .5s linear}
.zm-time{font-size:11px;color:rgba(255,255,255,.32);margin-bottom:10px;
  width:92%;text-align:right}
/* Queue rows */
.zm-queue{width:92%;border-top:1px solid rgba(255,255,255,.08);padding-top:6px}
.zm-q-row{display:flex;align-items:center;gap:6px;padding:5px 0;
  border-bottom:1px solid rgba(255,255,255,.04)}
.zm-q-row:last-child{border-bottom:none}
.zm-q-lbl{font-size:9px;color:rgba(255,255,255,.32);text-transform:uppercase;
  letter-spacing:.07em;flex-shrink:0;min-width:32px;font-weight:700}
.zm-q-title{flex:1;font-size:13px;color:rgba(255,255,255,.52);
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.zm-q-spot .zm-q-title{color:rgba(245,158,11,.62);font-style:italic}
.zm-q-dur{font-size:11px;color:rgba(255,255,255,.28);flex-shrink:0}
/* Ad break — amber tint on now row, inline AD badge */
.zm-now-ad{border-left:3px solid #f59e0b;padding-left:10px;background:rgba(245,158,11,.06);border-radius:8px}
.zm-ad-badge{font-size:10px;font-weight:800;letter-spacing:.1em;color:#f59e0b;
  background:rgba(245,158,11,.18);border:1px solid rgba(245,158,11,.4);
  border-radius:5px;padding:2px 7px;flex-shrink:0;align-self:flex-start}
.zm-prog-ad{background:#f59e0b!important}
.zm-etm{font-size:13px;color:rgba(255,255,255,.42);margin-bottom:6px}
.zm-wait{font-size:14px;color:rgba(255,255,255,.22);text-align:center;
  margin-top:24px;font-style:italic}
/* Free / available studio */
.free-band{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;padding:20px;text-align:center}
.free-icon{font-size:80px;opacity:.35;line-height:1}
.free-lbl{font-size:20px;font-weight:700;text-transform:uppercase;letter-spacing:.15em;color:var(--ok);opacity:.75}
.free-msg{font-size:17px;font-weight:400;color:rgba(255,255,255,.38);line-height:1.6;max-width:85%}
</style></head>
<body>
<div id="sb"><div style="flex:1;display:flex;align-items:center;justify-content:center;color:var(--mu)">
<div style="text-align:center"><div style="font-size:48px;margin-bottom:12px">🎙</div>
<div style="font-size:18px;font-weight:700">Connecting…</div></div></div></div>
<script nonce="{{csp_nonce()}}">
(function(){
'use strict';
var T='{{wb_token|default("")}}',SID='{{studio_id|default("")}}';
function tk(u){if(!T)return u;return u+(u.indexOf('?')>=0?'&':'?')+'token='+encodeURIComponent(T)}
function E(s){return(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function RGB(h){h=h.replace('#','');if(h.length===3)h=h[0]+h[0]+h[1]+h[1]+h[2]+h[2];var n=parseInt(h,16);return((n>>16)&255)+','+((n>>8)&255)+','+(n&255)}

var DB=-80,D=null,NP={},SS={},LL={},_built=false,_idleC={},_artSrc={},_lastSig='';
/* Smooth meter state */
var _targetLev={},_curLev={},_peakHold={},_peakTs={},_lastRaf=0;
var ATTACK_TC=0.05,DECAY_TC=0.7,PEAK_HOLD_MS=2500;
/* Fun messages for unassigned studios */
var _FREE_MSG=['Studio is ready and waiting...','On standby — awaiting the stars!',
  'Signal clear, studio is free','Empty stage, full of potential \uD83C\uDFAC',
  'Mic check: all clear! \uD83C\uDFA4','The show must go on \u2014 just not yet \u2728'];

function lh(d){return Math.max(0,Math.min(100,(d-DB)/(-DB)*100))}
var IDLE=["Probably on an ad break...","Presenter's talking too much!",
  "Getting ready for Make Me a Winner?","Music coming right up!",
  "Hold tight, we'll be right back!","Loading the next banger..."];

function gNp(s){var r=s.np_rpuid||'';return r?(NP[r]||{}):{}}

/* Track structural changes (chain/input assignment) so DOM rebuilds when needed */
function _sig(ss){return ss.map(function(s){return s.id+':'+(s.chains||[]).join(',')+'/'+(s.inputs||[]).join(',')+'/'+(s.zetta_station_key||'')}).join('|')}

/* Build the DOM once, then update in place — no flicker */
function buildCol(s,idx){
  var c=s.color||'#17a8ff',r=RGB(c);
  var fc=(s.chains||[])[0],sn=fc?fc.name:s.name;
  var isEmpty=!(s.chains&&s.chains.length)&&!(s.inputs&&s.inputs.length);
  var lg=fc?'<img class=logo src="'+tk('/wallboard/logo/'+E(fc.id))+'" alt="" onerror="this.outerHTML=\'<div class=logo-ph>'+E(sn[0])+'</div>\'">':'<div class=logo-ph>'+E((sn||'?')[0])+'</div>';
  var mh='';(s.inputs||[]).forEach(function(i){
    var k=i.key||'',st=i.stereo||false,nm=(i.name||'').replace(/^.*?\|/,'').replace(/^.*?-\s*/,'').substring(0,10);
    if(st){mh+='<div class=vm><div class=vs><div class=vb><div class=vf data-k="'+E(k)+'|L"></div><div class=vp data-p="'+E(k)+'|L"></div></div>'
      +'<div class=vb><div class=vf data-k="'+E(k)+'|R"></div><div class=vp data-p="'+E(k)+'|R"></div></div></div><div class=vl>'+E(nm)+'</div></div>'}
    else{mh+='<div class=vm><div class=vb><div class=vf data-k="'+E(k)+'"></div><div class=vp data-p="'+E(k)+'"></div></div><div class=vl>'+E(nm)+'</div></div>'}
  });
  /* Flat dark base + even colour tint — stays true to set colour top to bottom */
  var colBg='linear-gradient(180deg,rgba('+r+',.18) 0%,rgba('+r+',.10) 100%),rgba(7,14,38,.88)';
  var mainContent=isEmpty
    /* Free / available studio */
    ?('<div class=free-band id="free-band'+idx+'">'
      +'<div class=free-icon>\uD83C\uDFA4</div>'
      +'<div class=stu>'+E(s.name)+'</div>'
      +(s.freq?'<div class=frq>'+E(s.freq)+'</div>':'')
      +'<div class=free-lbl>AVAILABLE</div>'
      +'<div class=free-msg id="freemsg'+idx+'"></div>'
      +'</div>')
    /* Occupied studio — full content panel.
       When Zetta is configured the Planet Radio text stack is replaced with the
       compact Zetta now-playing section; the artwork thumbnail is still served
       from the Planet Radio feed since Zetta carries no artwork. */
    :('<div class=mp><div class=logo-wrap>'+lg+'</div>'
      +(fc?'':'<div class=stn style="text-shadow:0 0 20px rgba('+r+',.4)">'+E(sn)+'</div>')
      +'<div class=stu>'+E(s.name)+'</div>'
      +(s.freq?'<div class=frq>'+E(s.freq)+'</div>':'')
      +'<div class="mic off" id="mic'+idx+'">CLEAR</div>'
      +'<div id="badges'+idx+'"></div>'
      +'<div class=divider></div>'
      +(s.zetta_station_key
        /* Zetta layout — artwork thumbnail + full sequencer data */
        ?'<div class=zq-main id="zq'+idx+'"></div>'
        /* Planet Radio layout — show image, now-playing text, legacy Zetta strip */
        :('<img class=art id="showimg'+idx+'" alt="" style="display:none">'
          +'<div class=shw id="shw'+idx+'"></div>'
          +'<div class=np-div></div>'
          +'<img class=art id="art'+idx+'" alt="" style="display:none">'
          +'<div class=npl id="npl'+idx+'"></div>'
          +'<div class=anm id="anm'+idx+'"></div>'
          +'<div class=trk id="trk'+idx+'"></div>'
          +'<div class=idle id="idl'+idx+'"></div>'
          +'<div class=zq id="zq'+idx+'"></div>'))
      +'</div>');
  return '<div class=col id="col'+idx+'" style="--cc:rgba('+r+',.6);--cg:rgba('+r+',.12);background:'+colBg+';border-color:rgba('+r+',.22)">'
    +mainContent
    +((!isEmpty&&mh)?'<div class=rp>'+mh+'</div>':'')+'</div>';
}

function updateCol(s,idx){
  /* Free / available studio — show idle message and bail */
  var isEmpty=!(s.chains&&s.chains.length)&&!(s.inputs&&s.inputs.length);
  var freeMsg=document.getElementById('freemsg'+idx);
  if(freeMsg){
    if(!_idleC[s.id+'|free'])_idleC[s.id+'|free']=_FREE_MSG[idx%_FREE_MSG.length];
    if(freeMsg.textContent!==_idleC[s.id+'|free'])freeMsg.textContent=_idleC[s.id+'|free'];
  }
  if(isEmpty)return;

  var np=gNp(s);
  // Mic
  var mic=document.getElementById('mic'+idx);
  if(mic){mic.className='mic '+(s.mic_live?'on':'off');mic.textContent=s.mic_live?'MIC LIVE':'CLEAR'}
  // Badges
  var bd=document.getElementById('badges'+idx);
  if(bd){var bh='';(s.chains||[]).forEach(function(x){
    bh+='<div class="badge '+(x.status==='fault'?'ft':'ok')+'"><span class=dot></span>'+E(x.name)+' — '+(x.status==='fault'?'FAULT':'ON AIR')+'</div>'});
    if(bd.innerHTML!==bh)bd.innerHTML=bh}
  // Fault class on column
  var col=document.getElementById('col'+idx);
  if(col){var fl=false;(s.chains||[]).forEach(function(x){if(x.status==='fault')fl=true});
    col.classList.toggle('fault',fl)}
  // Show/presenter image
  var showImg=document.getElementById('showimg'+idx);
  if(showImg){
    var si=np.show_image||'';
    if(si){
      if(_artSrc['s'+idx]!==si||showImg.style.display==='none'){
        _artSrc['s'+idx]=si;showImg.src=si;
        showImg.style.display='';
        showImg.onerror=function(){showImg.style.display='none'}
      }
    }
  }
  // Track artwork
  var artEl=document.getElementById('art'+idx);
  if(artEl){
    var ta=np.artwork||'';
    if(ta&&(np.artist||np.title)){
      if(_artSrc['t'+idx]!==ta){artEl.src=ta;_artSrc['t'+idx]=ta;
        artEl.onload=function(){artEl.style.display=''};
        artEl.onerror=function(){artEl.style.display='none'}}
    }else{artEl.style.display='none';_artSrc['t'+idx]=''}
  }
  // Show name
  var shw=document.getElementById('shw'+idx);
  if(shw){var sv=np.show||'';if(shw.textContent!==sv)shw.textContent=sv}
  // Now playing
  var npl=document.getElementById('npl'+idx);
  var anm=document.getElementById('anm'+idx);
  var trk=document.getElementById('trk'+idx);
  var idl=document.getElementById('idl'+idx);
  if(np.artist||np.title){
    if(npl)npl.textContent='Now Playing';
    if(anm){var av=np.artist||'';if(anm.textContent!==av)anm.textContent=av}
    if(trk){var tv=np.title||'';if(trk.textContent!==tv)trk.textContent=tv}
    if(idl)idl.textContent='';
    delete _idleC[s.id];
  }else{
    if(npl)npl.textContent='';if(anm)anm.textContent='';if(trk)trk.textContent='';
    if(np.show&&idl){
      if(!_idleC[s.id])_idleC[s.id]=IDLE[Math.floor(Math.random()*IDLE.length)];
      if(idl.textContent!==_idleC[s.id])idl.textContent=_idleC[s.id];
    }else if(idl)idl.textContent='';
  }
  // Zetta main panel — mirror Zetta plugin: always show now_playing, never hide it
  var zEl=document.getElementById('zq'+idx);
  if(zEl&&s.zetta_station_key){
    var zd=(s.zetta_station_key&&s.zetta)?s.zetta:null;
    var zh='';
    if(!zd){
      zh='<div class="zm-wait">Waiting for Zetta data\u2026</div>';
    }else if(zd.now_playing){
      /* Always show now-playing — spot or music. Amber badge if it's an ad. */
      var _au=np.artwork||''; /* np = gNp(s) declared at top of updateCol */
      var _zart=_au
        ?'<img class="zm-art" src="'+E(_au)+'" alt="" onerror="this.className=\'zm-art zm-art-ph\';this.removeAttribute(\'src\')">'
        :'<div class="zm-art zm-art-ph">\uD83C\uDFA4</div>';
      var _zartist=E(zd.now_playing.raw_artist||zd.now_playing.artist||'');
      var _ztitle=E(zd.now_playing.raw_title||zd.now_playing.title||'');
      /* asset_type 2 = ASSET_SPOT in Zetta — use raw Zetta type, not category string matching */
      var _isSpot=(zd.now_playing.asset_type===2);
      /* Build zh WITHOUT inline progress style so string stays stable between RAF ticks */
      zh='<div class="zm-now'+ (_isSpot?' zm-now-ad':'') +'">'
        +(_isSpot?'<div class="zm-ad-badge">AD</div>':'')
        +_zart
        +'<div class="zm-text">'
        +(_isSpot&&zd.etm?'<div class="zm-mode">Back on air '+E(zd.etm)+'</div>':'')
        +'<div class="zm-artist">'+_zartist+'</div>'
        +'<div class="zm-title">'+_ztitle+'</div>'
        +'</div></div>'
        +'<div class="zm-prog-wrap"><div class="zm-prog-fill'+ (_isSpot?' zm-prog-ad':'') +'" id="zqpf'+idx+'"></div></div>'
        +'<div class="zm-time" id="zqtm'+idx+'"></div>';
      var _nq=zd.queue||[];
      if(_nq.length){
        zh+='<div class="zm-queue">';
        _nq.slice(0,4).forEach(function(q,qi){
          zh+='<div class="zm-q-row'+(q.asset_type===2?' zm-q-spot':'')+'">'
            +'<span class="zm-q-lbl">'+(qi===0?'NEXT':'')+'</span>'
            +'<span class="zm-q-title">'+E(q.title||q.raw_title||'')+'</span>'
            +'<span class="zm-q-dur">'+E(q.duration||'')+'</span>'
            +'</div>';
        });
        zh+='</div>';
      }
    }else{
      zh='<div class="zm-wait">'+E(zd.mode_name||zd.mode||'No sequencer data')+'</div>';
    }
    if(zEl.innerHTML!==zh)zEl.innerHTML=zh;
    /* Immediately paint the progress / countdown after any DOM rebuild */
    if(zd&&zd.now_playing){
      var _ex2=zd.ts?Math.max(0,Date.now()/1000-zd.ts):0;
      var _rem2=Math.max(0,(zd.remaining_seconds||0)-_ex2);
      var _dur2=zd.duration_seconds||0;
      var _pct2=_dur2>0?Math.min(100,(1-_rem2/_dur2)*100):0;
      var _rs2=Math.round(_rem2);
      var _pfEl=document.getElementById('zqpf'+idx);
      if(_pfEl)_pfEl.style.width=_pct2.toFixed(1)+'%';
      var _tmEl=document.getElementById('zqtm'+idx);
      if(_tmEl)_tmEl.textContent='-'+Math.floor(_rs2/60)+':'+(_rs2%60<10?'0':'')+(_rs2%60);
    }
  }
}

function getStudios(){
  if(!D)return[];var ss=D.studios||[];
  if(SID){var m=null;ss.forEach(function(s){if(s.id===SID)m=s});return m?[m]:[]}
  return ss;
}

function render(){
  var ss=getStudios();if(!ss.length)return;
  /* Rebuild DOM when chain/input assignment changes */
  var newSig=_sig(ss);if(newSig!==_lastSig){_built=false;_lastSig=newSig;}
  if(!_built){
    var h='<div class=cols>';ss.forEach(function(s,i){h+=buildCol(s,i)});h+='</div>';
    document.getElementById('sb').innerHTML=h;_built=true;
  }
  ss.forEach(function(s,i){updateCol(s,i)});
}

function poll(){fetch(tk('/api/studioboard/data'),{credentials:'same-origin'})
  .then(function(r){return r.json()}).then(function(d){D=d;render()}).catch(function(){})}

/* Live levels — store targets for smooth RAF animation, no direct DOM writes */
function live(){fetch(tk('/api/hub/live_levels'),{credentials:'same-origin'})
  .then(function(r){return r.ok?r.json():{}}).then(function(d){
    Object.keys(d).forEach(function(site){(d[site]||[]).forEach(function(s){
      var k=site+'|'+s.name;LL[k]=s;
      _targetLev[k]=lh(s.level_dbfs!=null?s.level_dbfs:DB);
      _targetLev[k+'|pk']=lh(s.peak_dbfs!=null?s.peak_dbfs:DB);
      if(s.level_dbfs_l!=null){
        _targetLev[k+'|L']=lh(s.level_dbfs_l);
        _targetLev[k+'|R']=lh(s.level_dbfs_r!=null?s.level_dbfs_r:DB);
        _targetLev[k+'|L|pk']=lh(s.peak_dbfs!=null?s.peak_dbfs:DB);
        _targetLev[k+'|R|pk']=lh(s.peak_dbfs!=null?s.peak_dbfs:DB);
      }
    })})}).catch(function(){})}

/* requestAnimationFrame loop — exponential attack/decay smoothing, peak hold */
function _meterRaf(now){
  requestAnimationFrame(_meterRaf);
  if(!_lastRaf){_lastRaf=now;return;}
  var dt=Math.min((now-_lastRaf)/1000,0.1);_lastRaf=now;
  var keys=Object.keys(_targetLev);
  for(var i=0;i<keys.length;i++){
    var key=keys[i];
    if(key.slice(-3)==='|pk')continue; /* skip peak entries in level loop */
    var t=_targetLev[key]||0,c=_curLev[key]||0;
    var tc=t>c?ATTACK_TC:DECAY_TC;
    c=c+(t-c)*(1-Math.exp(-dt/tc));
    _curLev[key]=c;
    var pct=Math.max(0,Math.min(100,c));
    document.querySelectorAll('[data-k="'+key+'"]').forEach(function(f){f.style.height=pct+'%'});
    /* Peak hold */
    var pkVal=_targetLev[key+'|pk']||0;
    if(pkVal>=(_peakHold[key]||0)){_peakHold[key]=pkVal;_peakTs[key]=now;}
    else if(now-(_peakTs[key]||0)>PEAK_HOLD_MS){_peakHold[key]=Math.max(0,(_peakHold[key]||0)-0.8);}
    var ph=_peakHold[key]||0;
    document.querySelectorAll('[data-p="'+key+'"]').forEach(function(p){
      p.style.bottom=ph+'%';p.style.opacity=ph>1?'.8':'0';
    });
  }
}
requestAnimationFrame(_meterRaf);

function npPoll(){if(!D)return;(D.studios||[]).forEach(function(st){
  var r=st.np_rpuid;if(!r)return;
  fetch(tk('/api/nowplaying/'+encodeURIComponent(r)),{credentials:'same-origin'})
    .then(function(x){return x.ok?x.json():{}}).then(function(d){
      NP[r]=d;var sh=(d.show||'').trim();
      if(sh&&!SS[st.id+'|'+sh]){SS[st.id+'|'+sh]=true;
        fetch(tk('/api/studioboard/seen_show/'+encodeURIComponent(st.id)),
          {method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},
           body:JSON.stringify({show:sh})}).catch(function(){})}
      render()}).catch(function(){})})}
/* Fetch show images directly from Planet Radio API as backup */
function showImgPoll(){
  if(!D)return;
  fetch('https://listenapi.planetradio.co.uk/api9.2/stations_nowplaying/GB')
    .then(function(r){return r.ok?r.json():[]})
    .then(function(data){
      var items=Array.isArray(data)?data:(data.data||data.stations||[]);
      var byCode={};items.forEach(function(s){if(s.stationCode)byCode[s.stationCode]=s});
      (D.studios||[]).forEach(function(st){
        var r=st.np_rpuid;if(!r)return;
        var s=byCode[r];if(!s)return;
        var air=s.stationOnAir||{};
        var imgUrl=air.episodeImageUrl||'';
        if(imgUrl&&NP[r]){NP[r].show_image=imgUrl}
      });
      render();
    }).catch(function(){});
}
/* Smooth progress bar + countdown timer between data polls (500 ms).
   Zetta data now arrives bundled in /api/studioboard/data (poll()), so
   pollZetta() is gone — we just extrapolate elapsed time from zd.ts. */
setInterval(function(){
  if(!D)return;
  (D.studios||[]).forEach(function(s,i){
    if(!s.zetta_station_key)return;
    var zd=s.zetta;
    if(!zd||!zd.now_playing||!zd.ts)return;
    var ex=Math.max(0,Date.now()/1000-zd.ts);
    var rem=Math.max(0,(zd.remaining_seconds||0)-ex);
    var dur=zd.duration_seconds||0;
    var pct=dur>0?(1-rem/dur)*100:0;
    var pfEl=document.getElementById('zqpf'+i);
    if(pfEl)pfEl.style.width=Math.min(100,pct).toFixed(1)+'%';
    var tmEl=document.getElementById('zqtm'+i);
    if(tmEl){var rs=Math.round(rem);tmEl.textContent='-'+Math.floor(rs/60)+':'+(rs%60<10?'0':'')+rs%60;}
  });
},500);
poll();npPoll();live();showImgPoll();
setInterval(poll,1500);setInterval(live,150);setInterval(npPoll,10000);setInterval(showImgPoll,30000);
})();
</script></body></html>"""
