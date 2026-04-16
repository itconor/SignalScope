# wallboard.py — Broadcast Wallboard for SignalScope
# TV-optimised display: chain status cards with logos, live meters, alert ticker.
# Drop into the plugins/ subdirectory.

import os, json, re, time as _time

SIGNALSCOPE_PLUGIN = {
    "id":       "wallboard",
    "label":    "Wallboard",
    "url":      "/hub/wallboard",
    "icon":     "📺",
    "hub_only": True,
    "version":  "3.5.1",
}

_BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
_APP_DIR   = os.path.dirname(_BASE_DIR)
_LOGO_DIR  = os.path.join(_BASE_DIR, "wallboard_logos")
_CFG_PATH  = os.path.join(_BASE_DIR, "wallboard_cfg.json")
_ALERT_LOG = os.path.join(_APP_DIR, "alert_log.json")


def _cfg_load():
    try:
        with open(_CFG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}

def _cfg_save(c):
    with open(_CFG_PATH, "w") as f:
        json.dump(c, f, indent=2)

def _has_logo(chain_id):
    for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
        if os.path.exists(os.path.join(_LOGO_DIR, chain_id + ext)):
            return True
    return False

def _load_alerts(limit=20):
    try:
        with open(_ALERT_LOG) as f:
            data = json.load(f)
        data.sort(key=lambda e: e.get("time", 0), reverse=True)
        return data[:limit]
    except Exception:
        return []

def _dtype(device_index):
    d = (device_index or "").lower()
    if d.startswith("fm://"):   return "fm"
    if d.startswith("dab://"):  return "dab"
    if d.startswith("http"):    return "http"
    if d.startswith("rtp://"):  return "rtp"
    if d.startswith("alsa://"): return "alsa"
    return "other"


_QR_DIR = os.path.join(_BASE_DIR, "wallboard_qr")

def _ensure_qr(chain_id, url):
    """Generate a QR code SVG file on disk if it doesn't exist or the
    URL has changed. Returns the file path."""
    os.makedirs(_QR_DIR, exist_ok=True)
    svg_path = os.path.join(_QR_DIR, chain_id + ".svg")
    url_path = os.path.join(_QR_DIR, chain_id + ".url")
    # Check if already generated for this URL
    try:
        if os.path.exists(svg_path) and os.path.exists(url_path):
            with open(url_path) as f:
                if f.read().strip() == url:
                    return svg_path
    except Exception:
        pass
    # Generate — try SVG first (no Pillow needed), fall back to PNG
    try:
        import qrcode
        qr = qrcode.QRCode(version=None,
                            error_correction=qrcode.constants.ERROR_CORRECT_L,
                            box_size=10, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        try:
            import qrcode.image.svg
            img = qr.make_image(
                image_factory=qrcode.image.svg.SvgPathImage)
            with open(svg_path, "wb") as f:
                img.save(f)
        except Exception:
            # SVG factory not available — try PNG and convert path
            png_path = svg_path.replace(".svg", ".png")
            img = qr.make_image(fill_color="black", back_color="white")
            img.save(png_path)
            svg_path = png_path
        with open(url_path, "w") as f:
            f.write(url)
    except Exception as e:
        print(f"[Wallboard] QR generation failed for {chain_id}: {e}")
    return svg_path


def register(app, ctx):
    login_required = ctx["login_required"]
    csrf_protect   = ctx["csrf_protect"]
    monitor        = ctx["monitor"]
    hub_server     = ctx.get("hub_server")
    BUILD          = ctx["BUILD"]

    from flask import jsonify, render_template_string, request, send_file, g

    # After-request hook: re-apply relaxed headers for kiosk route.
    # Flask calls after_request handlers in REVERSE registration order.
    # Plugins register AFTER signalscope, so our handler runs FIRST and
    # signalscope's _apply_security_headers runs SECOND — overwriting us.
    # Fix: insert at position 0 in the handler list so after reversal
    # our handler runs LAST (after signalscope has set its headers).
    # Paths that MUST be embeddable in an iframe (Yodeck, Screenly, etc.)
    _KIOSK_PREFIXES = ("/wallboard/tv", "/wallboard/logo", "/wallboard/brand",
                       "/wallboard/asset", "/wallboard/qr", "/wallboard/play",
                       "/api/wallboard/")

    def _wallboard_kiosk_headers(response):
        # Strip all security headers for any kiosk-path request OR when
        # the route explicitly set g._wb_kiosk.  This must fire even on
        # error responses (403, 500) so the signage player can display them.
        is_kiosk = getattr(g, '_wb_kiosk', False)
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
            # Allow any origin to embed and fetch from this page
            response.headers['Access-Control-Allow-Origin'] = '*'
        return response
    try:
        app.after_request_funcs.setdefault(None, []).insert(0, _wallboard_kiosk_headers)
    except Exception:
        app.after_request(_wallboard_kiosk_headers)

    def _validate_wb_token():
        """Validate mobile API token from query string. Returns True if valid."""
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

    # ── Token-based auth for every request (Yodeck / kiosk iframes) ──
    # Embedded browsers block third-party cookies so session auth fails
    # on sub-requests (fetch, img src, css url).  If ?token= is present
    # and valid, set session["logged_in"] in-memory for THIS request so
    # @login_required passes without needing a persisted cookie.
    @app.before_request
    def _wb_token_before():
        from flask import session as _sess
        tok = request.args.get("token", "").strip() \
           or request.args.get("api_key", "").strip()
        if not tok:
            return
        # Mark kiosk mode so after_request strips security headers
        g._wb_kiosk = True
        if _sess.get("logged_in"):
            return
        if _validate_wb_token():
            _sess["logged_in"] = True
            _sess["login_ts"]  = _time.time()
            _sess["username"]  = "wallboard"
            _sess["role"]      = "viewer"
            if not _sess.get("_csrf"):
                import hashlib
                _sess["_csrf"] = hashlib.sha256(os.urandom(32)).hexdigest()

    @app.get("/hub/wallboard")
    def wallboard_page():
        from flask import session
        if _validate_wb_token():
            session["logged_in"] = True
            session["login_ts"] = _time.time()
            session["username"] = "wallboard"
            session["role"] = "viewer"
            if not session.get("_csrf"):
                import hashlib
                session["_csrf"] = hashlib.sha256(
                    os.urandom(32)).hexdigest()
        cfg = monitor.app_cfg
        if cfg.auth.enabled and not session.get("logged_in"):
            from flask import redirect, url_for
            return redirect(url_for("login", next=request.path))
        token = request.args.get("token", "").strip() \
             or request.args.get("api_key", "").strip() or ""
        url_overrides = {}
        if request.args.get("show_qr") == "1":
            url_overrides["show_qr"] = True
        if request.args.get("bauer") == "1":
            url_overrides["bauer_mode"] = True
        return render_template_string(_TPL, build=BUILD, wb_token=token,
                                      url_overrides=json.dumps(url_overrides))

    # ── Kiosk / TV route ────────────────────────────────────────────
    # Requires a valid mobile API token: /wallboard/tv?token=YOUR_TOKEN
    # The token is embedded in the page and passed on every sub-request
    # so the wallboard works in iframe-based signage players (Yodeck)
    # where third-party cookies are blocked.
    @app.get("/wallboard/tv")
    def wallboard_tv():
        g._wb_kiosk = True
        cfg = monitor.app_cfg
        token = request.args.get("token", "").strip() \
             or request.args.get("api_key", "").strip()
        # If auth is enabled, require a valid token
        if cfg.auth.enabled and not _validate_wb_token():
            from flask import make_response
            resp = make_response(
                '<h2>Wallboard token required</h2>'
                '<p>Use: /wallboard/tv?token=YOUR_MOBILE_API_TOKEN</p>'
                '<p>The token is your Mobile API token from '
                'Settings.</p>', 403)
            resp.headers.pop('X-Frame-Options', None)
            resp.headers.pop('Content-Security-Policy', None)
            return resp
        from flask import make_response
        # URL params can override config: &show_qr=1, &bauer=1, etc.
        url_overrides = {}
        if request.args.get("show_qr") == "1":
            url_overrides["show_qr"] = True
        if request.args.get("bauer") == "1":
            url_overrides["bauer_mode"] = True
        resp = make_response(
            render_template_string(_TPL, build=BUILD, wb_token=token,
                                  url_overrides=json.dumps(url_overrides)))
        # Belt-and-suspenders: set iframe-friendly headers directly on
        # the response object so they survive any after-request handler
        # ordering issues.  The kiosk after_request handler also strips
        # these, but setting them here guarantees correctness.
        resp.headers.pop('X-Frame-Options', None)
        resp.headers.pop('Content-Security-Policy', None)
        resp.headers.pop('X-Content-Type-Options', None)
        resp.headers.pop('Referrer-Policy', None)
        resp.headers.pop('Strict-Transport-Security', None)
        resp.headers['Access-Control-Allow-Origin'] = '*'
        resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        resp.headers['Pragma'] = 'no-cache'
        # Prevent Flask from setting a session cookie on kiosk responses —
        # some embedded browsers (Yodeck) reject pages that set third-party
        # cookies inside an iframe.
        from flask import session as _ks
        _ks.modified = False
        return resp

    @app.get("/api/wallboard/data")
    @login_required
    def wallboard_data():
        cfg = monitor.app_cfg
        now = _time.time()
        sites_out = []

        if hub_server and cfg.hub.mode in ("hub", "both"):
            try:
                raw = hub_server.get_sites() if callable(getattr(hub_server, "get_sites", None)) else []
            except Exception:
                raw = []
            for sd in (raw or []):
                if not isinstance(sd, dict):
                    continue
                site_name = sd.get("name") or sd.get("site", "")
                last_seen = sd.get("_received") or sd.get("last_seen", 0) or 0
                online = (now - last_seen) < 30
                streams = []
                for s in (sd.get("streams") or []):
                    if not isinstance(s, dict):
                        continue
                    name = (s.get("name") or "").strip()
                    if not name:
                        continue
                    di = s.get("device_index", "")
                    dt = _dtype(di)
                    np_ = ""
                    if dt == "fm":
                        np_ = (s.get("fm_rds_ps") or "").strip()
                        if not np_:
                            np_ = (s.get("fm_rds_rt") or "").strip()
                    elif dt == "dab":
                        np_ = (s.get("dab_dls") or "").strip()
                    stereo = bool(s.get("stereo") or s.get("fm_stereo"))
                    streams.append({
                        "name":           name,
                        "level_dbfs":     s.get("level_dbfs", -90.0),
                        "peak_dbfs":      s.get("peak_dbfs", -90.0),
                        "ai_status":      s.get("ai_status", ""),
                        "silence_active": bool(s.get("silence_active", False)),
                        "rtp_loss_pct":   s.get("rtp_loss_pct", 0.0),
                        "lufs_i":         s.get("lufs_i", -70.0),
                        "now_playing":    np_[:60] if np_ else "",
                        "stereo":         stereo,
                        "level_dbfs_l":   s.get("level_dbfs_l") if stereo else None,
                        "level_dbfs_r":   s.get("level_dbfs_r") if stereo else None,
                        "dtype":          dt,
                    })
                if streams:
                    sites_out.append({
                        "site": site_name, "online": online, "streams": streams,
                    })

        if cfg.hub.mode in ("client", "standalone", "both"):
            try:
                inputs = getattr(monitor, "inputs", None) \
                         or getattr(monitor, "_inputs", None) or []
                local = []
                for inp in inputs:
                    np_ = getattr(inp, "_fm_rds_ps", "") \
                          or getattr(inp, "_dab_dls", "") or ""
                    stereo = (getattr(inp, "stereo", False)
                              and getattr(inp, "_audio_channels", 1) == 2)
                    dev = getattr(inp, "device_index", "")
                    local.append({
                        "name":           getattr(inp, "name", "?"),
                        "level_dbfs":     round(getattr(inp, "_last_level_dbfs", -90.0), 1),
                        "peak_dbfs":      round(getattr(inp, "_last_peak_dbfs",  -90.0), 1),
                        "ai_status":      getattr(inp, "_ai_status", ""),
                        "silence_active": bool(getattr(inp, "_silence_active", False)),
                        "rtp_loss_pct":   round(getattr(inp, "_rtp_loss_pct", 0.0), 1),
                        "lufs_i":         round(getattr(inp, "_lufs_i", -70.0), 1),
                        "now_playing":    np_[:60] if np_ else "",
                        "stereo":         bool(stereo),
                        "level_dbfs_l":   round(getattr(inp, "_last_level_dbfs_l", -90.0), 1)
                                          if stereo else None,
                        "level_dbfs_r":   round(getattr(inp, "_last_level_dbfs_r", -90.0), 1)
                                          if stereo else None,
                        "dtype":          _dtype(dev),
                    })
                if local:
                    label = cfg.hub.site_name or "Local"
                    if label not in {s["site"] for s in sites_out}:
                        sites_out.insert(0, {
                            "site": label, "online": True, "streams": local,
                        })
            except Exception:
                pass

        chain_logos = {}
        token = request.args.get("token", "").strip() \
             or request.args.get("api_key", "").strip() or ""
        tk = ("?token=" + token) if token else ""
        for ch in (cfg.signal_chains or []):
            cid = ch.get("id", "")
            if cid:
                chain_logos[cid] = _has_logo(cid)
                # Always pre-generate QR codes (tiny SVG files)
                try:
                    play_url = request.host_url.rstrip("/") \
                             + "/wallboard/play/" + cid + tk
                    _ensure_qr(cid, play_url)
                except Exception:
                    pass

        alerts_out = []
        chain_fault_types = {"CHAIN_FAULT", "CHAIN_OK", "CHAIN_RECOVERED"}
        chain_faults = []
        all_alerts = _load_alerts(200)
        for a in all_alerts:
            atype = (a.get("type") or "").upper()
            is_ok = atype in ("RECOVERY", "AUDIO_RESTORED", "CHAIN_OK",
                              "CHAIN_RECOVERED")
            entry = {
                "time":     a.get("time", 0),
                "site":     (a.get("site") or "").strip(),
                "stream":   (a.get("stream") or "").strip(),
                "type":     atype,
                "msg":      (a.get("msg") or a.get("message") or atype).strip(),
                "ok":       is_ok,
                "chain_id": (a.get("chain_id") or "").strip(),
            }
            alerts_out.append(entry)
            if atype in chain_fault_types and entry["chain_id"]:
                chain_faults.append(entry)

        return jsonify({
            "sites": sites_out,
            "chain_logos": chain_logos,
            "alerts": alerts_out[:20],
            "chain_faults": chain_faults,
            "config": _cfg_load(),
        })

    @app.get("/api/wallboard/config")
    @login_required
    def wallboard_config_get():
        return jsonify(_cfg_load())

    @app.post("/api/wallboard/config")
    @login_required
    def wallboard_config_save():
        _cfg_save(request.get_json(force=True))
        return jsonify({"ok": True})

    @app.post("/api/wallboard/logo/<chain_id>")
    @login_required
    def wallboard_logo_upload(chain_id):
        if not re.match(r'^[a-zA-Z0-9_-]+$', chain_id):
            return jsonify({"error": "Invalid ID"}), 400
        f = request.files.get("logo")
        if not f or not f.filename:
            return jsonify({"error": "No file"}), 400
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
            return jsonify({"error": "Unsupported format"}), 400
        # Read into memory to check size (avoids seek issues on WSGI streams)
        data = f.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            return jsonify({"error": "File too large (max 2 MB)"}), 400
        os.makedirs(_LOGO_DIR, exist_ok=True)
        for old in os.listdir(_LOGO_DIR):
            if old.split(".")[0] == chain_id:
                os.remove(os.path.join(_LOGO_DIR, old))
        with open(os.path.join(_LOGO_DIR, chain_id + ext), "wb") as out:
            out.write(data)
        return jsonify({"ok": True})

    @app.get("/wallboard/logo/<chain_id>")
    @login_required
    def wallboard_logo_serve(chain_id):
        if not re.match(r'^[a-zA-Z0-9_-]+$', chain_id):
            return '', 404
        for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
            path = os.path.join(_LOGO_DIR, chain_id + ext)
            if os.path.exists(path):
                return send_file(path, max_age=300)
        return '', 404

    @app.delete("/api/wallboard/logo/<chain_id>")
    @login_required
    def wallboard_logo_delete(chain_id):
        if not re.match(r'^[a-zA-Z0-9_-]+$', chain_id):
            return jsonify({"error": "Invalid ID"}), 400
        for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
            path = os.path.join(_LOGO_DIR, chain_id + ext)
            if os.path.exists(path):
                os.remove(path)
        return jsonify({"ok": True})

    # ── Brand logo (page branding — e.g. company logo in header) ─────
    @app.post("/api/wallboard/brand")
    @login_required
    def wallboard_brand_upload():
        f = request.files.get("logo")
        if not f or not f.filename:
            return jsonify({"error": "No file"}), 400
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
            return jsonify({"error": "Unsupported format"}), 400
        data = f.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            return jsonify({"error": "File too large (max 2 MB)"}), 400
        os.makedirs(_LOGO_DIR, exist_ok=True)
        for old in os.listdir(_LOGO_DIR):
            if old.startswith("_brand."):
                os.remove(os.path.join(_LOGO_DIR, old))
        with open(os.path.join(_LOGO_DIR, "_brand" + ext), "wb") as out:
            out.write(data)
        return jsonify({"ok": True})

    @app.get("/wallboard/brand")
    @login_required
    def wallboard_brand_serve():
        for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
            path = os.path.join(_LOGO_DIR, "_brand" + ext)
            if os.path.exists(path):
                return send_file(path, max_age=60)
        return '', 404

    # ── Bauer brand assets (fonts, logos) ────────────────────────────
    @app.get("/wallboard/asset/<path:filename>")
    @login_required
    def wallboard_asset_serve(filename):
        safe = re.sub(r'[^a-zA-Z0-9._-]', '', filename)
        path = os.path.join(_LOGO_DIR, safe)
        if os.path.exists(path):
            return send_file(path, max_age=86400)
        return '', 404

    # ── QR code generator (server-side, no external API) ─────────
    @app.get("/wallboard/qr/<chain_id>")
    @login_required
    def wallboard_qr_serve(chain_id):
        """Serve a pre-generated QR code file — same pattern as logos."""
        if not re.match(r'^[a-zA-Z0-9_-]+$', chain_id):
            return '', 404
        for ext, mime in [(".svg", "image/svg+xml"), (".png", "image/png")]:
            path = os.path.join(_QR_DIR, chain_id + ext)
            if os.path.exists(path):
                return send_file(path, mimetype=mime, max_age=60)
        return '', 404

    # ── Mobile play page — linked from QR codes ──────────────────
    @app.get("/wallboard/play/<chain_id>")
    def wallboard_play(chain_id):
        if not re.match(r'^[a-zA-Z0-9_-]+$', chain_id):
            return '', 404
        g._wb_kiosk = True
        cfg = monitor.app_cfg
        # Token auth or session
        if cfg.auth.enabled:
            from flask import session
            if not _validate_wb_token() and not session.get("logged_in"):
                return 'Unauthorised', 403
            if _validate_wb_token():
                session["logged_in"] = True
                session["login_ts"] = _time.time()
                session["username"] = "wallboard"
                session["role"] = "viewer"
        # Find the chain and its last node's live_url
        chain = None
        for ch in (cfg.signal_chains or []):
            if ch.get("id") == chain_id:
                chain = ch
                break
        if not chain:
            return 'Chain not found', 404
        chain_name = chain.get("name", "Station")
        # Find logo
        has_logo = _has_logo(chain_id)
        token = request.args.get("token", "").strip() \
             or request.args.get("api_key", "").strip() or ""
        tk = ("?token=" + token) if token else ""
        logo_url = ("/wallboard/logo/" + chain_id + tk) if has_logo else ""
        return render_template_string(_PLAY_TPL,
            chain_id=chain_id, chain_name=chain_name,
            logo_url=logo_url, tk=tk, build=BUILD)

    @app.delete("/api/wallboard/brand")
    @login_required
    def wallboard_brand_delete():
        for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
            path = os.path.join(_LOGO_DIR, "_brand" + ext)
            if os.path.exists(path):
                os.remove(path)
        return jsonify({"ok": True})


# ═══════════════════════════════════════════════════════════════════════════
_TPL = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="csrf-token" content="{{csrf_token()}}">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wallboard — SignalScope</title>
<link rel="icon" type="image/x-icon" href="/static/signalscope_icon.png">
<style nonce="{{csp_nonce()}}">
:root{
  --bg:#07142b;--sur:#0d2346;--bor:#17345f;
  --acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;
  --tx:#eef5ff;--mu:#8aa4c8;
  --mc-w:155px;--radius:16px;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;overflow:hidden}
body{
  font-family:system-ui,-apple-system,sans-serif;
  background:radial-gradient(ellipse at 50% -5%,#14397a 0%,var(--bg) 38%,#040d1c 100%);
  color:var(--tx);font-size:13px;
  display:flex;flex-direction:column;
  -webkit-user-select:none;user-select:none;
}
/* Subtle animated ambient glow */
body::after{
  content:'';position:fixed;inset:0;pointer-events:none;z-index:0;
  background:
    radial-gradient(ellipse 600px 300px at 20% 15%,rgba(23,168,255,.04),transparent),
    radial-gradient(ellipse 500px 250px at 80% 85%,rgba(34,197,94,.03),transparent);
  animation:ambient 20s ease-in-out infinite alternate;
}
@keyframes ambient{0%{opacity:.6}100%{opacity:1}}
#wb-hdr,#wb-content,#wb-ticker,#wb-drawer,#wb-overlay{position:relative;z-index:1}

/* ═══ Header ═══ */
#wb-hdr{
  flex-shrink:0;padding:8px 20px;
  background:linear-gradient(180deg,rgba(10,31,65,.97),rgba(9,24,48,.97));
  border-bottom:1px solid var(--bor);
  display:flex;align-items:center;gap:14px;flex-wrap:wrap;
  transition:opacity .45s,transform .45s;z-index:50;
  backdrop-filter:blur(8px);
}
#wb-hdr.hide{opacity:0;transform:translateY(-100%);pointer-events:none}
.wb-logo{font-size:20px}
.wb-titles{display:flex;flex-direction:column;gap:1px}
.wb-title{font-size:15px;font-weight:800;letter-spacing:.01em}
.wb-sub{font-size:10px;color:var(--mu);letter-spacing:.04em}
.wb-ctrl{margin-left:auto;display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.btn{
  display:inline-flex;align-items:center;gap:4px;
  padding:5px 11px;border-radius:8px;font-size:12px;font-weight:600;
  cursor:pointer;border:none;color:var(--tx);background:var(--bor);
  text-decoration:none;transition:filter .12s,background .12s;white-space:nowrap;
  font-family:inherit;
}
.btn:hover{filter:brightness(1.25)}
.btn.bp{background:var(--acc);color:#fff}
.btn.bd{background:var(--al);color:#fff}
.btn.active{background:var(--acc);color:#fff}
.btn.bs{padding:3px 8px;font-size:11px;border-radius:6px}
#wb-clock{
  font-size:30px;font-weight:200;font-variant-numeric:tabular-nums;
  color:var(--tx);letter-spacing:.05em;min-width:110px;text-align:right;
  text-shadow:0 0 20px rgba(23,168,255,.15);
}

/* ═══ Content ═══ */
#wb-content{flex:1;display:flex;flex-direction:column;overflow:hidden}

/* ═══ Hero status ═══ */
#wb-hero{
  margin:10px 20px 0;border-radius:18px;
  padding:16px 28px;display:flex;align-items:center;gap:20px;
  transition:background .5s,border-color .5s,box-shadow .5s;flex-shrink:0;
}
#wb-hero.ok{
  background:linear-gradient(135deg,rgba(34,197,94,.12),rgba(34,197,94,.04));
  border:1.5px solid rgba(34,197,94,.35);
  box-shadow:0 0 30px rgba(34,197,94,.05);
}
#wb-hero.fault{
  background:linear-gradient(135deg,rgba(239,68,68,.1),rgba(239,68,68,.04));
  border:1.5px solid rgba(239,68,68,.4);
  box-shadow:0 0 30px rgba(239,68,68,.06);
  animation:hero-border 2.5s ease-in-out infinite;
}
@keyframes hero-border{
  0%,100%{box-shadow:0 0 30px rgba(239,68,68,.06)}
  50%{box-shadow:0 0 50px rgba(239,68,68,.12)}
}
#wb-hero.loading{background:rgba(23,52,95,.35);border:1.5px solid var(--bor)}
.hero-icon{font-size:42px;flex-shrink:0;line-height:1}
#wb-hero.fault .hero-icon{animation:hero-pulse 2s ease-in-out infinite}
@keyframes hero-pulse{0%,100%{transform:scale(1)}50%{transform:scale(1.12)}}
.hero-body{flex:1;min-width:0}
.hero-title{font-size:20px;font-weight:800;letter-spacing:-.02em;margin-bottom:3px}
#wb-hero.ok .hero-title{color:var(--ok)}
#wb-hero.fault .hero-title{color:var(--al)}
#wb-hero.loading .hero-title{color:var(--mu)}
.hero-sub{font-size:13px;color:var(--mu)}
.hero-badge{font-size:13px;font-weight:700;padding:8px 18px;border-radius:12px;flex-shrink:0;letter-spacing:.02em}
#wb-hero.ok .hero-badge{background:rgba(34,197,94,.15);color:var(--ok);border:1px solid rgba(34,197,94,.3)}
#wb-hero.fault .hero-badge{background:rgba(239,68,68,.15);color:var(--al);border:1px solid rgba(239,68,68,.3)}

/* ═══ Chain strip ═══ */
#wb-chains{
  flex-shrink:0;padding:12px 20px 8px;
  display:flex;gap:14px;overflow-x:auto;overflow-y:hidden;
  scrollbar-width:none;
}
#wb-chains::-webkit-scrollbar{display:none}
#wb-chains:empty{padding:0}

.cc{
  min-width:240px;max-width:none;flex:1 1 240px;
  background:linear-gradient(160deg,rgba(13,35,70,.92),rgba(8,22,48,.96));
  border:1.5px solid rgba(23,168,255,.12);
  border-radius:20px;padding:20px 22px 16px;
  display:flex;flex-direction:column;align-items:center;gap:10px;
  position:relative;overflow:hidden;
  transition:border-color .4s,box-shadow .4s,transform .25s;
  box-shadow:0 4px 24px rgba(0,0,0,.3),inset 0 1px 0 rgba(255,255,255,.04);
}
.cc:hover{transform:translateY(-3px);box-shadow:0 10px 32px rgba(0,0,0,.4),inset 0 1px 0 rgba(255,255,255,.04)}
/* Top edge shimmer */
.cc::before{
  content:'';position:absolute;top:0;left:0;right:0;height:1px;
  background:linear-gradient(90deg,transparent 5%,rgba(23,168,255,.35) 50%,transparent 95%);
}
/* Bottom glow line */
.cc::after{
  content:'';position:absolute;bottom:-1px;left:20%;right:20%;height:1px;
  background:currentColor;opacity:.08;border-radius:1px;
}
.cc.cc-ok{
  border-color:rgba(34,197,94,.3);
  box-shadow:0 0 24px rgba(34,197,94,.06),0 0 48px rgba(34,197,94,.03),0 4px 24px rgba(0,0,0,.3);
}
.cc.cc-ok::before{background:linear-gradient(90deg,transparent 5%,rgba(34,197,94,.3) 50%,transparent 95%)}
.cc.cc-fault{
  border-color:rgba(239,68,68,.5);
  background:linear-gradient(160deg,rgba(13,35,70,.92),rgba(42,14,14,.85));
  box-shadow:0 0 30px rgba(239,68,68,.12),0 0 60px rgba(239,68,68,.05),0 4px 24px rgba(0,0,0,.3);
  animation:cc-glow 2s ease-in-out infinite;
}
.cc.cc-fault::before{background:linear-gradient(90deg,transparent 5%,rgba(239,68,68,.4) 50%,transparent 95%)}
@keyframes cc-glow{
  0%,100%{box-shadow:0 0 30px rgba(239,68,68,.12),0 0 60px rgba(239,68,68,.05),0 4px 24px rgba(0,0,0,.3)}
  50%{box-shadow:0 0 40px rgba(239,68,68,.2),0 0 80px rgba(239,68,68,.08),0 4px 24px rgba(0,0,0,.3)}
}

/* Visual container — logo/avatar only */
.cc-visual{display:flex;align-items:center;justify-content:center}
.cc-logo{
  width:72px;height:72px;border-radius:16px;object-fit:contain;
  background:rgba(255,255,255,.05);
  box-shadow:0 4px 16px rgba(0,0,0,.3);
  border:1px solid rgba(255,255,255,.06);
  flex-shrink:0;
}
.cc-avatar{
  width:72px;height:72px;border-radius:16px;flex-shrink:0;
  display:flex;align-items:center;justify-content:center;
  font-size:26px;font-weight:800;color:#fff;
  box-shadow:0 4px 16px rgba(0,0,0,.35);
  text-shadow:0 2px 4px rgba(0,0,0,.3);
}
.cc-name{
  font-size:15px;font-weight:800;text-align:center;line-height:1.3;
  max-width:100%;letter-spacing:-.01em;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;
}
/* Status badge */
.cc-status{
  display:flex;align-items:center;gap:6px;
  padding:6px 16px;border-radius:12px;
  font-size:12px;font-weight:800;letter-spacing:.04em;
}
.cc-status.s-ok{
  background:rgba(34,197,94,.15);color:var(--ok);
  border:1px solid rgba(34,197,94,.3);
  box-shadow:0 0 10px rgba(34,197,94,.08);
}
.cc-status.s-fault{
  background:rgba(239,68,68,.15);color:var(--al);
  border:1px solid rgba(239,68,68,.35);
  animation:sb-blink 1.2s ease-in-out infinite;
  box-shadow:0 0 12px rgba(239,68,68,.1);
}
.cc-status.s-unk{background:rgba(138,164,200,.08);color:var(--mu);border:1px solid rgba(138,164,200,.2)}
@keyframes sb-blink{0%,100%{opacity:1}50%{opacity:.55}}
.cc-sdot{width:8px;height:8px;border-radius:50%;background:currentColor;box-shadow:0 0 6px currentColor}

/* Node dots */
.cc-nodes{display:flex;align-items:center;gap:4px;flex-wrap:wrap;justify-content:center;margin-top:2px}
.cc-nd{width:8px;height:8px;border-radius:50%;transition:background .3s}
.cc-nd.ok{background:var(--ok);box-shadow:0 0 5px rgba(34,197,94,.6)}
.cc-nd.down,.cc-nd.fault{background:var(--al);box-shadow:0 0 8px rgba(239,68,68,.6);animation:nd-p 1s ease infinite}
.cc-nd.offline{background:var(--wn);box-shadow:0 0 4px rgba(245,158,11,.4)}
.cc-nd.unknown,.cc-nd.maintenance{background:var(--mu)}
@keyframes nd-p{0%,100%{transform:scale(1)}50%{transform:scale(1.6);opacity:.5}}
.cc-arr{color:var(--mu);font-size:8px;opacity:.35}

/* Health bar */
.cc-health{width:100%;height:5px;background:rgba(0,0,0,.35);border-radius:3px;overflow:hidden;margin-top:4px}
.cc-health-fill{height:100%;border-radius:3px;transition:width .6s ease,background .4s}
/* SLA text */
.cc-sla{font-size:10px;color:var(--mu);font-weight:600;font-variant-numeric:tabular-nums;margin-top:-2px}
/* Now playing on chain card */
.cc-np-wrap{width:100%;text-align:center;margin-top:2px}
.cc-np-show{
  font-size:9px;color:var(--mu);font-weight:600;
  text-transform:uppercase;letter-spacing:.05em;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  margin-bottom:2px;
}
.cc-np-track{
  font-size:11px;color:var(--acc);font-weight:600;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  line-height:1.3;
}
.cc-np-artist{color:var(--tx);font-weight:700}
body.corp .cc-np-show{color:#86868b}
body.corp .cc-np-track{color:#0071e3}
body.corp .cc-np-artist{color:#1d1d1f}
/* Artwork — own row between status and now-playing */
.cc-art-wrap{display:flex;justify-content:center;width:100%}
.cc-art{
  width:88px;height:88px;border-radius:14px;object-fit:cover;
  box-shadow:0 4px 18px rgba(0,0,0,.4);
  border:1px solid rgba(255,255,255,.08);
  transition:opacity .5s ease,transform .4s ease;
}
body.corp .cc-art{box-shadow:0 2px 12px rgba(0,0,0,.12);border-color:rgba(0,0,0,.06)}

/* ═══ Meter scroll ═══ */
#wb-scroll{flex:1;overflow-y:auto;overflow-x:hidden;padding:6px 20px 16px}
#wb-scroll::-webkit-scrollbar{width:5px}
#wb-scroll::-webkit-scrollbar-track{background:transparent}
#wb-scroll::-webkit-scrollbar-thumb{background:var(--bor);border-radius:3px}

.wb-site{margin-bottom:12px}
.wb-site-hdr{
  display:flex;align-items:center;gap:7px;
  font-size:10px;font-weight:700;color:var(--mu);
  text-transform:uppercase;letter-spacing:.09em;
  margin-bottom:7px;padding-bottom:4px;border-bottom:1px solid rgba(23,52,95,.5);
}
.wb-sdot{width:7px;height:7px;border-radius:50%;background:var(--ok);flex-shrink:0}
.wb-sdot.off{background:var(--al)}

.wb-grid{display:grid;gap:8px;grid-template-columns:repeat(auto-fill,minmax(var(--mc-w),1fr))}

/* ═══ Meter card ═══ */
.mc{
  background:var(--sur);border:1px solid var(--bor);
  border-radius:12px;overflow:hidden;
  display:flex;flex-direction:column;min-width:0;
  transition:border-color .3s,box-shadow .3s;
}
.mc.mc-alert{border-color:var(--al);box-shadow:0 0 16px rgba(239,68,68,.1);animation:mc-gl 1.5s ease-in-out infinite}
@keyframes mc-gl{0%,100%{box-shadow:0 0 16px rgba(239,68,68,.1)}50%{box-shadow:0 0 26px rgba(239,68,68,.18)}}
.mc.mc-warn{border-color:var(--wn);box-shadow:0 0 10px rgba(245,158,11,.06)}
.mc.mc-silent{border-color:rgba(239,68,68,.3);box-shadow:0 0 12px rgba(239,68,68,.06)}
.mc.mc-ok{border-color:rgba(34,197,94,.2);box-shadow:inset 0 1px 0 rgba(34,197,94,.06)}

.mc-head{padding:7px 10px 5px;border-bottom:1px solid rgba(255,255,255,.04)}
.mc-name{font-size:11.5px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.3}
.mc-sub{font-size:9px;color:var(--mu);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px}

.mc-body{flex:1;display:flex;flex-direction:column;align-items:center;padding:10px 10px 6px;gap:6px}

.mtr-wrap{
  position:relative;width:100%;max-width:48px;
  flex:1;min-height:120px;
  background:linear-gradient(to top,rgba(34,197,94,.06) 0% 75%,rgba(245,158,11,.06) 75% 87.5%,rgba(239,68,68,.06) 87.5% 100%);
  border-radius:5px;overflow:hidden;border:1px solid rgba(255,255,255,.03);
}
.mtr-wrap::after{
  content:'';position:absolute;top:0;right:0;bottom:0;width:3px;
  background:linear-gradient(to top,#22c55e 0% 75%,#f59e0b 75% 87.5%,#ef4444 87.5% 100%);
  opacity:.3;pointer-events:none;
}
.mtr-fill{
  position:absolute;bottom:0;left:0;right:0;
  background:linear-gradient(to top,#22c55e 0%,#22c55e 75%,#f59e0b 82%,#ef4444 96%);
  border-radius:5px 5px 0 0;
}
.mtr-peak{
  position:absolute;left:-1px;right:-1px;height:2px;
  background:#fff;border-radius:1px;opacity:.82;
  box-shadow:0 0 4px rgba(255,255,255,.4);
}

.mtr-stereo{display:flex;gap:5px;width:100%;max-width:52px;flex:1;min-height:120px}
.mtr-ch{display:flex;flex-direction:column;align-items:center;flex:1;gap:2px}
.mtr-lr{
  position:relative;width:100%;flex:1;
  background:linear-gradient(to top,rgba(34,197,94,.06) 0% 75%,rgba(245,158,11,.06) 75% 87.5%,rgba(239,68,68,.06) 87.5% 100%);
  border-radius:4px;overflow:hidden;border:1px solid rgba(255,255,255,.03);
}
.mtr-lr::after{
  content:'';position:absolute;top:0;right:0;bottom:0;width:2px;
  background:linear-gradient(to top,#22c55e 0% 75%,#f59e0b 75% 87.5%,#ef4444 87.5% 100%);
  opacity:.3;pointer-events:none;
}
.mtr-ch-label{font-size:9px;font-weight:700;color:var(--mu);line-height:1}
.mc:not([data-stereo="1"]) .mtr-stereo{display:none}
.mc[data-stereo="1"] .mtr-mono{display:none}

.mc-lev{font-size:14px;font-weight:700;font-variant-numeric:tabular-nums;letter-spacing:-.02em;min-width:76px;text-align:center}
.mc-lev.lc-low{color:var(--mu)}.mc-lev.lc-warn{color:var(--wn)}.mc-lev.lc-alert{color:var(--al)}
.mc-lufs{font-size:10px;color:var(--mu);text-align:center}
.mc-np{font-size:10px;color:var(--acc);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%;padding:0 8px;text-align:center}
.mc-foot{display:flex;align-items:center;justify-content:space-between;padding:4px 9px 6px;gap:4px}
.sp{display:inline-flex;align-items:center;gap:3px;padding:2px 7px;border-radius:999px;font-size:10px;font-weight:700;line-height:1.4}
.sp-ok{background:rgba(34,197,94,.12);color:var(--ok)}
.sp-al{background:rgba(239,68,68,.14);color:var(--al)}
.sp-wn{background:rgba(245,158,11,.12);color:var(--wn)}
.sp-si{background:rgba(138,164,200,.1);color:var(--mu)}
.mc-rtp{font-size:10px;color:var(--wn)}

.wb-empty{display:flex;flex-direction:column;align-items:center;justify-content:center;height:200px;gap:12px;color:var(--mu)}
.wb-empty-ico{font-size:48px;opacity:.2}

/* ═══ Alert ticker ═══ */
#wb-ticker{
  flex-shrink:0;background:rgba(6,16,36,.97);
  border-top:1px solid var(--bor);
  padding:5px 0;display:flex;align-items:center;overflow:hidden;height:28px;
}
#wb-ticker-label{
  background:var(--wn);color:#000;
  font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.08em;
  padding:3px 10px;flex-shrink:0;height:100%;display:flex;align-items:center;
}
#wb-ticker-scroll{flex:1;overflow:hidden;position:relative;height:18px}
#wb-ticker-inner{
  position:absolute;top:0;left:0;white-space:nowrap;
  display:flex;align-items:center;
  animation:tk-scroll 40s linear infinite;
}
@keyframes tk-scroll{0%{transform:translateX(0)}100%{transform:translateX(-50%)}}
.tk-item{font-size:11px;color:var(--mu);padding-right:50px;display:inline-flex;align-items:center;gap:5px}
.tk-time{color:var(--wn);font-variant-numeric:tabular-nums;font-weight:600}
.tk-site{font-weight:600;color:var(--tx)}
.tk-sep{color:var(--bor);padding:0 8px}
.tk-al{color:var(--al)}.tk-ok{color:var(--ok)}

/* ═══ Settings drawer ═══ */
#wb-overlay{position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:190;opacity:0;pointer-events:none;transition:opacity .3s}
#wb-overlay.show{opacity:1;pointer-events:auto}
#wb-drawer{
  position:fixed;top:0;right:0;bottom:0;width:360px;
  background:linear-gradient(180deg,rgba(10,28,58,.98),rgba(5,14,30,.99));
  border-left:1px solid var(--bor);z-index:200;
  transform:translateX(100%);transition:transform .35s cubic-bezier(.4,0,.2,1);
  display:flex;flex-direction:column;box-shadow:-6px 0 28px rgba(0,0,0,.4);
}
#wb-drawer.open{transform:translateX(0)}
.dr-hdr{padding:14px 16px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--bor)}
.dr-title{font-size:14px;font-weight:700}
.dr-body{flex:1;overflow-y:auto;padding:14px 16px}
.dr-section{margin-bottom:20px}
.dr-stitle{font-size:10px;font-weight:700;color:var(--mu);text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px}
.dr-row{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px}
.dr-toggle{display:flex;align-items:center;gap:8px;font-size:12px;cursor:pointer;margin-bottom:7px}
.dr-toggle input{accent-color:var(--acc);width:15px;height:15px}

.dr-chain{display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid rgba(23,52,95,.3)}
.dr-chain:last-child{border-bottom:none}
.dr-ch-logo{width:32px;height:32px;border-radius:8px;object-fit:contain;background:rgba(255,255,255,.04);flex-shrink:0}
.dr-ch-av{
  width:32px;height:32px;border-radius:8px;flex-shrink:0;
  display:flex;align-items:center;justify-content:center;
  font-size:12px;font-weight:800;color:#fff;
}
.dr-ch-info{flex:1;min-width:0}
.dr-ch-name{font-size:12px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dr-ch-actions{display:flex;gap:5px;margin-top:3px}

/* Stream selection */
.dr-stream{display:flex;align-items:center;gap:8px;padding:4px 0}
.dr-stream input{accent-color:var(--acc);width:14px;height:14px;flex-shrink:0}
.dr-stream label{font-size:12px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1}
.dr-stream-site{font-size:9px;color:var(--mu);margin-left:auto;flex-shrink:0}

:fullscreen #wb-scroll,:-webkit-full-screen #wb-scroll{padding:6px 20px 16px}

/* ═══ Bauer Media branded theme ═══ */
@font-face{font-family:'BauerMediaSans';src:url('/wallboard/asset/BauerMediaSans-Regular.otf{% if wb_token %}?token={{wb_token}}{% endif %}') format('opentype');font-weight:400;font-style:normal;font-display:swap}
@font-face{font-family:'BauerMediaSans';src:url('/wallboard/asset/BauerMediaSans-Bold.otf{% if wb_token %}?token={{wb_token}}{% endif %}') format('opentype');font-weight:700;font-style:normal;font-display:swap}
@font-face{font-family:'BauerMediaSans';src:url('/wallboard/asset/BauerMediaSans-Light.otf{% if wb_token %}?token={{wb_token}}{% endif %}') format('opentype');font-weight:300;font-style:normal;font-display:swap}

body.bauer{
  font-family:'BauerMediaSans',system-ui,sans-serif;
  background:#4700A3;color:#fff;
}
body.bauer::after{
  background:
    radial-gradient(ellipse 800px 400px at 30% 20%,rgba(88,0,202,.4),transparent),
    radial-gradient(ellipse 600px 300px at 70% 80%,rgba(63,20,156,.3),transparent);
}
body.bauer #wb-hdr{
  background:linear-gradient(180deg,rgba(72,0,164,.98),rgba(55,0,130,.98));
  border-bottom:1px solid rgba(255,255,255,.12);
}
body.bauer .wb-title{color:#fff}
body.bauer .wb-sub{color:rgba(255,255,255,.6)}
body.bauer #wb-clock{color:#fff;text-shadow:0 0 20px rgba(255,255,255,.15)}
body.bauer .btn{background:rgba(255,255,255,.12);color:#fff}
body.bauer .btn:hover{background:rgba(255,255,255,.2)}
body.bauer .btn.bp,.bauer .btn.active{background:#fff;color:#4700A3}
body.bauer #wb-hero{border-radius:20px}
body.bauer #wb-hero.ok{background:linear-gradient(135deg,rgba(34,197,94,.15),rgba(34,197,94,.06));border-color:rgba(34,197,94,.4)}
body.bauer #wb-hero.fault{background:linear-gradient(135deg,rgba(255,59,48,.15),rgba(255,59,48,.06));border-color:rgba(255,59,48,.45)}
body.bauer #wb-hero.loading{background:rgba(255,255,255,.06);border-color:rgba(255,255,255,.15)}
body.bauer #wb-hero.ok .hero-title{color:#22c55e}
body.bauer #wb-hero.fault .hero-title{color:#ff3b30}
body.bauer #wb-hero.loading .hero-title{color:rgba(255,255,255,.5)}
body.bauer .hero-sub{color:rgba(255,255,255,.55)}
body.bauer #wb-hero.ok .hero-badge{background:rgba(34,197,94,.15);color:#22c55e;border-color:rgba(34,197,94,.3)}
body.bauer #wb-hero.fault .hero-badge{background:rgba(255,59,48,.15);color:#ff3b30;border-color:rgba(255,59,48,.3)}
body.bauer .cc{
  background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);
  box-shadow:0 4px 24px rgba(0,0,0,.2);backdrop-filter:blur(8px);
}
body.bauer .cc::before{background:linear-gradient(90deg,transparent 5%,rgba(255,255,255,.15) 50%,transparent 95%)}
body.bauer .cc:hover{box-shadow:0 8px 32px rgba(0,0,0,.3);background:rgba(255,255,255,.12)}
body.bauer .cc.cc-ok{border-color:rgba(34,197,94,.35)}
body.bauer .cc.cc-ok::before{background:linear-gradient(90deg,transparent 5%,rgba(34,197,94,.25) 50%,transparent 95%)}
body.bauer .cc.cc-fault{
  border-color:rgba(255,59,48,.5);background:rgba(255,59,48,.08);
  box-shadow:0 0 30px rgba(255,59,48,.12),0 4px 24px rgba(0,0,0,.2);
}
body.bauer .cc.cc-fault::before{background:linear-gradient(90deg,transparent 5%,rgba(255,59,48,.35) 50%,transparent 95%)}
body.bauer .cc-name{color:#fff}
body.bauer .cc-status.s-ok{background:rgba(34,197,94,.15);color:#22c55e;border-color:rgba(34,197,94,.3)}
body.bauer .cc-status.s-fault{background:rgba(255,59,48,.15);color:#ff3b30;border-color:rgba(255,59,48,.35)}
body.bauer .cc-np-show{color:rgba(255,255,255,.45)}
body.bauer .cc-np-track{color:rgba(255,255,255,.85)}
body.bauer .cc-np-artist{color:#fff}
body.bauer .cc-sla{color:rgba(255,255,255,.4)}
body.bauer .cc-health{background:rgba(255,255,255,.1)}
body.bauer .wb-site-hdr{color:rgba(255,255,255,.45);border-bottom-color:rgba(255,255,255,.1)}
body.bauer .wb-sdot{background:#22c55e}
body.bauer .wb-sdot.off{background:#ff3b30}
body.bauer .mc{
  background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);
  box-shadow:0 2px 8px rgba(0,0,0,.15);
}
body.bauer .mc.mc-ok{border-color:rgba(34,197,94,.2)}
body.bauer .mc.mc-alert{border-color:rgba(255,59,48,.45);box-shadow:0 0 16px rgba(255,59,48,.12)}
body.bauer .mc.mc-warn{border-color:rgba(255,149,0,.3)}
body.bauer .mc.mc-silent{border-color:rgba(255,59,48,.25)}
body.bauer .mc-head{border-bottom-color:rgba(255,255,255,.06)}
body.bauer .mc-name{color:#fff}
body.bauer .mc-sub{color:rgba(255,255,255,.45)}
body.bauer .mc-lev{color:#fff}
body.bauer .mc-lev.lc-low{color:rgba(255,255,255,.35)}
body.bauer .mc-lev.lc-warn{color:#ff9500}
body.bauer .mc-lev.lc-alert{color:#ff3b30}
body.bauer .mc-lufs{color:rgba(255,255,255,.4)}
body.bauer .mc-np{color:rgba(255,255,255,.7)}
body.bauer .sp-ok{background:rgba(34,197,94,.15);color:#22c55e}
body.bauer .sp-al{background:rgba(255,59,48,.15);color:#ff3b30}
body.bauer .sp-wn{background:rgba(255,149,0,.12);color:#ff9500}
body.bauer .sp-si{background:rgba(255,255,255,.06);color:rgba(255,255,255,.4)}
body.bauer .mtr-wrap,.bauer .mtr-lr{background:rgba(255,255,255,.04);border-color:rgba(255,255,255,.06)}
body.bauer .mtr-wrap::after,.bauer .mtr-lr::after{opacity:.25}
body.bauer #wb-ticker{background:rgba(55,0,130,.95);border-top:1px solid rgba(255,255,255,.1)}
body.bauer #wb-ticker-label{background:#fff;color:#4700A3;font-weight:800}
body.bauer .tk-item{color:rgba(255,255,255,.5)}
body.bauer .tk-site{color:#fff}
body.bauer .tk-al{color:#ff3b30}
body.bauer .tk-ok{color:#22c55e}
body.bauer .tk-time{color:rgba(255,255,255,.7)}
body.bauer #wb-alert-badge{background:#ff3b30}
/* Bauer logo in header */
.wb-bauer-logo{height:36px;object-fit:contain;display:none;margin-right:4px}
body.bauer .wb-bauer-logo{display:block}
body.bauer .wb-logo{display:none}

/* ═══ Corporate / Clean theme ═══ */
body.corp{
  background:#f5f5f7;color:#1d1d1f;
  font-family:'SF Pro Display',system-ui,-apple-system,sans-serif;
}
body.corp::after{display:none}
body.corp #wb-hdr{
  background:#fff;border-bottom:1px solid #e5e5e7;
  backdrop-filter:none;
}
body.corp .wb-title{color:#1d1d1f}
body.corp .wb-sub{color:#86868b}
body.corp #wb-clock{color:#1d1d1f;text-shadow:none}
body.corp .btn{background:#e5e5e7;color:#1d1d1f}
body.corp .btn.bp,.corp .btn.active{background:#0071e3;color:#fff}
body.corp #wb-hero{border-radius:18px}
body.corp #wb-hero.ok{background:linear-gradient(135deg,rgba(52,199,89,.08),rgba(52,199,89,.03));border-color:rgba(52,199,89,.3)}
body.corp #wb-hero.fault{background:linear-gradient(135deg,rgba(255,59,48,.08),rgba(255,59,48,.03));border-color:rgba(255,59,48,.3)}
body.corp #wb-hero.ok .hero-title{color:#34c759}
body.corp #wb-hero.fault .hero-title{color:#ff3b30}
body.corp #wb-hero.ok .hero-badge{background:rgba(52,199,89,.1);color:#34c759;border-color:rgba(52,199,89,.25)}
body.corp #wb-hero.fault .hero-badge{background:rgba(255,59,48,.1);color:#ff3b30;border-color:rgba(255,59,48,.25)}
body.corp .hero-sub{color:#86868b}
body.corp .cc{
  background:#fff;border:1px solid #e5e5e7;
  box-shadow:0 2px 12px rgba(0,0,0,.06);
}
body.corp .cc::before{background:none}
body.corp .cc:hover{box-shadow:0 6px 20px rgba(0,0,0,.1)}
body.corp .cc.cc-ok{border-color:rgba(52,199,89,.3);box-shadow:0 2px 12px rgba(0,0,0,.06)}
body.corp .cc.cc-fault{
  border-color:rgba(255,59,48,.4);background:#fff;
  box-shadow:0 0 20px rgba(255,59,48,.08),0 2px 12px rgba(0,0,0,.06);
}
body.corp .cc-name{color:#1d1d1f}
body.corp .cc-status.s-ok{background:rgba(52,199,89,.1);color:#34c759;border-color:rgba(52,199,89,.25)}
body.corp .cc-status.s-fault{background:rgba(255,59,48,.1);color:#ff3b30;border-color:rgba(255,59,48,.25)}
body.corp .cc-np{color:#0071e3}
body.corp .cc-np-artist{color:#1d1d1f}
body.corp .cc-sla{color:#86868b}
body.corp .cc-health{background:rgba(0,0,0,.06)}
body.corp .wb-site-hdr{color:#86868b;border-bottom-color:#e5e5e7}
body.corp .wb-sdot{background:#34c759}
body.corp .wb-sdot.off{background:#ff3b30}
body.corp .mc{background:#fff;border:1px solid #e5e5e7;box-shadow:0 1px 4px rgba(0,0,0,.04)}
body.corp .mc.mc-ok{border-color:rgba(52,199,89,.2)}
body.corp .mc.mc-alert{border-color:rgba(255,59,48,.4);box-shadow:0 0 12px rgba(255,59,48,.08)}
body.corp .mc.mc-warn{border-color:rgba(255,149,0,.3)}
body.corp .mc.mc-silent{border-color:rgba(255,59,48,.25)}
body.corp .mc-head{border-bottom-color:#f0f0f2}
body.corp .mc-name{color:#1d1d1f}
body.corp .mc-sub{color:#86868b}
body.corp .mc-lev{color:#1d1d1f}
body.corp .mc-lev.lc-low{color:#86868b}
body.corp .mc-lev.lc-warn{color:#ff9500}
body.corp .mc-lev.lc-alert{color:#ff3b30}
body.corp .mc-lufs{color:#86868b}
body.corp .mc-np{color:#0071e3}
body.corp .sp-ok{background:rgba(52,199,89,.1);color:#34c759}
body.corp .sp-al{background:rgba(255,59,48,.1);color:#ff3b30}
body.corp .sp-wn{background:rgba(255,149,0,.1);color:#ff9500}
body.corp .sp-si{background:rgba(142,142,147,.1);color:#8e8e93}
body.corp .mtr-wrap,.corp .mtr-lr{background:rgba(0,0,0,.04);border-color:rgba(0,0,0,.06)}
body.corp .mtr-wrap::after,.corp .mtr-lr::after{opacity:.2}
body.corp #wb-ticker{background:#fff;border-top:1px solid #e5e5e7}
body.corp #wb-ticker-label{background:#ff9500}
body.corp .tk-item{color:#86868b}
body.corp .tk-site{color:#1d1d1f}
body.corp .tk-al{color:#ff3b30}
body.corp .tk-ok{color:#34c759}
body.corp .tk-time{color:#ff9500}
body.corp #wb-alert-badge{background:#ff3b30}
/* Brand logo in header */
.wb-brand{height:32px;object-fit:contain;margin-right:4px;display:none}
body.corp .wb-brand{display:block}
body.has-brand .wb-brand{display:block}
/* Hide header toggle */
#wb-hdr.hdr-hidden{display:none}

/* ═══ Animated now-playing transitions ═══ */
.cc-art{transition:opacity .5s ease,transform .4s ease}
.cc-art.art-entering{opacity:0;transform:scale(.92)}
.cc-np-track,.cc-np-show{transition:opacity .35s ease,transform .35s ease}
.cc-np-track.np-entering,.cc-np-show.np-entering{opacity:0;transform:translateY(8px)}

/* ═══ Auto-scaling chain cards — fill the screen width ═══ */
#wb-chains.cc-count-1 .cc{min-width:420px;max-width:none;flex:1 1 100%}
#wb-chains.cc-count-2 .cc{min-width:320px;max-width:none;flex:1 1 45%}
#wb-chains.cc-count-3 .cc{min-width:280px;max-width:none;flex:1 1 30%}
#wb-chains.cc-count-4 .cc{max-width:none;flex:1 1 22%}
#wb-chains.cc-count-5 .cc{max-width:none;flex:1 1 18%}
#wb-chains.cc-count-1 .cc-name{font-size:22px}
#wb-chains.cc-count-2 .cc-name{font-size:18px}
#wb-chains.cc-count-1 .cc-logo,#wb-chains.cc-count-1 .cc-avatar{width:96px;height:96px;border-radius:20px}
#wb-chains.cc-count-1 .cc-avatar{font-size:36px}
#wb-chains.cc-count-2 .cc-logo,#wb-chains.cc-count-2 .cc-avatar{width:84px;height:84px;border-radius:18px}
#wb-chains.cc-count-2 .cc-avatar{font-size:30px}
#wb-chains.cc-count-1 .cc-art{width:100px;height:100px;border-radius:16px}
#wb-chains.cc-count-2 .cc-art{width:92px;height:92px}

/* ═══ Colour-matched artwork glow ═══ */
.cc[data-glow]{transition:border-color .8s ease,box-shadow .8s ease}

/* ═══ Spotlight hero ═══ */
#wb-spotlight{
  margin:8px 20px 0;border-radius:18px;flex-shrink:0;
  padding:18px 28px;display:none;align-items:center;gap:22px;
  background:linear-gradient(135deg,rgba(13,35,70,.85),rgba(8,22,48,.92));
  border:1.5px solid rgba(23,168,255,.15);
  box-shadow:0 4px 28px rgba(0,0,0,.3);
  overflow:hidden;position:relative;
  transition:opacity .6s ease;
}
#wb-spotlight.active{display:flex}
.sl-art{width:100px;height:100px;border-radius:16px;object-fit:cover;flex-shrink:0;
  box-shadow:0 4px 20px rgba(0,0,0,.5);border:1px solid rgba(255,255,255,.08);
  transition:opacity .6s ease}
.sl-art.sl-entering{opacity:0}
.sl-logo{width:48px;height:48px;border-radius:12px;object-fit:contain;flex-shrink:0;
  background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.06)}
.sl-body{flex:1;min-width:0;transition:opacity .5s ease}
.sl-body.sl-entering{opacity:0}
.sl-station{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--mu);margin-bottom:4px}
.sl-track{font-size:20px;font-weight:800;letter-spacing:-.02em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sl-artist{font-size:15px;color:var(--acc);font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px}
.sl-dots{position:absolute;bottom:8px;right:16px;display:flex;gap:5px}
.sl-dot{width:6px;height:6px;border-radius:50%;background:rgba(255,255,255,.2);transition:background .3s}
.sl-dot.active{background:var(--acc)}
body.bauer .sl-station{color:rgba(255,255,255,.5)}
body.bauer .sl-artist{color:rgba(255,255,255,.75)}
body.bauer #wb-spotlight{background:rgba(255,255,255,.06);border-color:rgba(255,255,255,.1)}

/* ═══ Now-playing history ═══ */
.cc-np-history{width:100%;text-align:center;margin-top:2px}
.cc-np-hist-item{
  font-size:9px;color:var(--mu);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  line-height:1.5;transition:opacity .4s;
}
.cc-np-hist-item:nth-child(2){opacity:.55}
.cc-np-hist-item:nth-child(3){opacity:.3}

/* ═══ Bauer font sizing ═══ */
body.bauer .hero-title{font-family:'BauerMediaSans',system-ui,sans-serif;font-size:26px;font-weight:700;letter-spacing:-.03em}
body.bauer .hero-sub{font-family:'BauerMediaSans',system-ui,sans-serif;font-size:14px;font-weight:300}
body.bauer .hero-badge{font-family:'BauerMediaSans',system-ui,sans-serif;font-size:15px;font-weight:700}
body.bauer .cc-name{font-family:'BauerMediaSans',system-ui,sans-serif;font-size:17px;font-weight:700}
body.bauer .wb-title{font-family:'BauerMediaSans',system-ui,sans-serif;font-size:18px;font-weight:700}
body.bauer #wb-clock{font-family:'BauerMediaSans',system-ui,sans-serif;font-weight:300;font-size:34px}
body.bauer .sl-track{font-family:'BauerMediaSans',system-ui,sans-serif;font-size:24px;font-weight:700}
body.bauer .sl-artist{font-family:'BauerMediaSans',system-ui,sans-serif;font-weight:300}
body.bauer .cc-np-track{font-family:'BauerMediaSans',system-ui,sans-serif}
body.bauer .cc-np-artist{font-family:'BauerMediaSans',system-ui,sans-serif;font-weight:700}
body.bauer .tk-site{font-family:'BauerMediaSans',system-ui,sans-serif}

/* ═══ Fault sparkline ═══ */
.cc-sparkline{width:100%;height:16px;margin-top:2px;position:relative}
.cc-sparkline canvas{width:100%;height:100%;border-radius:3px}

/* ═══ Sound alert toggle ═══ */
#btn-sound{position:relative}
#btn-sound.active{background:var(--al);color:#fff}

/* ═══ Full-screen fault alert ═══ */
#wb-fault-overlay{
  position:fixed;inset:0;z-index:9998;pointer-events:none;
  border:0px solid rgba(239,68,68,0);
  transition:border-width .3s ease,border-color .3s ease;
}
#wb-fault-overlay.active{
  border:6px solid rgba(239,68,68,.7);
  animation:fault-border-pulse 1.5s ease-in-out infinite;
}
@keyframes fault-border-pulse{
  0%,100%{border-color:rgba(239,68,68,.7);box-shadow:inset 0 0 60px rgba(239,68,68,.08)}
  50%{border-color:rgba(239,68,68,.95);box-shadow:inset 0 0 120px rgba(239,68,68,.15)}
}
#wb-fault-banner{
  position:fixed;top:0;left:0;right:0;z-index:9999;
  background:linear-gradient(90deg,#c81e1e,#ef4444,#c81e1e);
  color:#fff;text-align:center;
  font-size:18px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;
  padding:10px 20px;
  transform:translateY(-100%);transition:transform .4s cubic-bezier(.4,0,.2,1);
  box-shadow:0 4px 24px rgba(239,68,68,.4);
  display:flex;align-items:center;justify-content:center;gap:12px;
}
#wb-fault-banner.active{transform:translateY(0)}
#wb-fault-banner .fault-dot{
  width:12px;height:12px;border-radius:50%;background:#fff;
  animation:fault-dot-blink 1s ease-in-out infinite;
}
@keyframes fault-dot-blink{0%,100%{opacity:1}50%{opacity:.3}}
body.bauer #wb-fault-banner{
  background:linear-gradient(90deg,#8b0000,#c81e1e,#8b0000);
  font-family:'BauerMediaSans',system-ui,sans-serif;
}

/* ═══ Hero level pulse ═══ */
#wb-hero.ok{position:relative;overflow:hidden}
#wb-hero .hero-pulse-bg{
  position:absolute;inset:0;pointer-events:none;
  background:radial-gradient(ellipse at 30% 50%,rgba(34,197,94,.15),transparent 70%);
  opacity:0;transition:opacity .15s ease;border-radius:inherit;
}
body.bauer #wb-hero .hero-pulse-bg{
  background:radial-gradient(ellipse at 30% 50%,rgba(34,197,94,.2),transparent 70%);
}

/* ═══ Time-of-day background gradient ═══ */
body.day-grad{transition:background 3s ease}

/* ═══ QR codes on chain cards ═══ */
.cc-qr{margin-top:4px;display:none;justify-content:center}
.cc-qr.qr-visible{display:flex}
</style>
</head>
<body>

<header id="wb-hdr">
  <img class="wb-bauer-logo" src="/wallboard/asset/_bauer_logo_white.svg{% if wb_token %}?token={{wb_token}}{% endif %}" alt="Bauer Media">
  <img class="wb-brand" id="wb-brand-img" src="/wallboard/brand{% if wb_token %}?token={{wb_token}}{% endif %}" alt="" onerror="this.style.display='none'">
  <span class="wb-logo" id="wb-logo-emoji">📺</span>
  <div class="wb-titles">
    <span class="wb-title" id="wb-title-text">Wallboard</span>
    <span class="wb-sub" id="wb-meta">Loading…</span>
  </div>
  <span id="wb-alert-badge" style="background:var(--al);color:#fff;border-radius:999px;padding:2px 10px;font-size:11px;font-weight:700;display:none"></span>
  <div class="wb-ctrl">
    <button class="btn bs" id="btn-sm" title="1">S</button>
    <button class="btn bs active" id="btn-md" title="2">M</button>
    <button class="btn bs" id="btn-lg" title="3">L</button>
    <button class="btn bs" id="btn-sort" title="S">↕ Level</button>
    <button class="btn bs" id="btn-cfg" title="G">⚙</button>
    <a class="btn bs" href="/">⌂</a>
    <button class="btn bs" id="btn-sound" title="A">🔔</button>
    <button class="btn bp bs" id="btn-fs" title="F">⛶ Full</button>
    <span id="wb-clock">--:--</span>
  </div>
</header>

<div id="wb-content">
  <div id="wb-hero" class="loading">
    <div class="hero-pulse-bg" id="hero-pulse-bg"></div>
    <div class="hero-icon" id="hero-icon">⏳</div>
    <div class="hero-body">
      <div class="hero-title" id="hero-title">Connecting…</div>
      <div class="hero-sub" id="hero-sub">Waiting for station status data.</div>
    </div>
    <div class="hero-badge" id="hero-badge"></div>
  </div>
  <div id="wb-spotlight"></div>
  <div id="wb-chains"></div>
  <div id="wb-scroll"><div id="wb-meters"></div></div>
</div>

<div id="wb-ticker">
  <div id="wb-ticker-label">ALERTS</div>
  <div id="wb-ticker-scroll"><div id="wb-ticker-inner"><span class="tk-item">No recent alerts</span></div></div>
</div>

<div id="wb-fault-overlay"></div>
<div id="wb-fault-banner"><span class="fault-dot"></span><span id="fault-banner-text">SIGNAL FAULT</span><span class="fault-dot"></span></div>
<div id="wb-overlay"></div>
<aside id="wb-drawer">
  <div class="dr-hdr">
    <span class="dr-title">Wallboard Settings</span>
    <button class="btn bg bs" id="btn-close-dr">✕ Close</button>
  </div>
  <div class="dr-body">
    <div class="dr-section">
      <div class="dr-stitle">Card Size</div>
      <div class="dr-row">
        <button class="btn bs" data-sz="sm">Small</button>
        <button class="btn bs active" data-sz="md">Medium</button>
        <button class="btn bs" data-sz="lg">Large</button>
      </div>
    </div>
    <div class="dr-section">
      <div class="dr-stitle">Theme</div>
      <label class="dr-toggle"><input type="checkbox" id="cfg-bauer"> Bauer Media branded</label>
      <label class="dr-toggle"><input type="checkbox" id="cfg-corp"> Corporate / clean mode</label>
    </div>
    <div class="dr-section">
      <div class="dr-stitle">Display</div>
      <label class="dr-toggle"><input type="checkbox" id="cfg-lufs" checked> LUFS-I readout</label>
      <label class="dr-toggle"><input type="checkbox" id="cfg-np" checked> Now playing text</label>
      <label class="dr-toggle"><input type="checkbox" id="cfg-sites" checked> Site headers</label>
      <label class="dr-toggle"><input type="checkbox" id="cfg-ticker" checked> Alert ticker</label>
      <label class="dr-toggle"><input type="checkbox" id="cfg-hero" checked> Status hero banner</label>
      <label class="dr-toggle"><input type="checkbox" id="cfg-hide-hdr"> Hide header bar</label>
      <label class="dr-toggle"><input type="checkbox" id="cfg-qr"> Show QR codes on station cards</label>
    </div>
    <div class="dr-section">
      <div class="dr-stitle">Page Branding</div>
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:10px">
        <img id="dr-brand-preview" src="/wallboard/brand{% if wb_token %}?token={{wb_token}}{% endif %}" alt="" style="height:28px;object-fit:contain;border-radius:4px" onerror="this.style.display='none'">
        <button class="btn bp bs" id="btn-brand-upload">Upload Brand Logo</button>
        <button class="btn bd bs" id="btn-brand-remove">Remove</button>
      </div>
      <div style="font-size:10px;color:var(--mu)">Displayed in the header bar. Works in both themes.</div>
    </div>
    <div class="dr-section">
      <div class="dr-stitle">Station Branding</div>
      <div id="dr-chains"><span style="color:var(--mu);font-size:12px">Loading…</span></div>
    </div>
    <div class="dr-section">
      <div class="dr-stitle">Visible Streams <span style="font-weight:400;text-transform:none;letter-spacing:0;color:var(--mu)">(uncheck to hide)</span></div>
      <div id="dr-streams"><span style="color:var(--mu);font-size:12px">Loading…</span></div>
    </div>
  </div>
</aside>

<script nonce="{{csp_nonce()}}">
(function(){
'use strict';
function _e(s){return(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function _csrf(){return(document.querySelector('meta[name="csrf-token"]')||{}).content||(document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/)||[])[1]||''}

/* Token for cookie-less auth (Yodeck / kiosk iframes) */
var _wbTk='{{wb_token|default("")}}';
function _tkUrl(u){if(!_wbTk)return u;return u+(u.indexOf('?')>=0?'&':'?')+'token='+encodeURIComponent(_wbTk)}
/* URL parameter overrides — e.g. &show_qr=1&bauer=1 */
var _urlOverrides={{url_overrides|default("{}")|safe}};

var POLL_MS=1500,LIVE_MS=150,CHAIN_MS=2000;
var PEAK_HOLD=2500,PEAK_RATE=.45,DB_FLOOR=-80,ATTACK_RATE=600,DECAY_RATE=30;
var _sizes={sm:120,md:155,lg:210};
var AVATAR_COLORS=[['#1a7fe8','#17a8ff'],['#16a047','#22c55e'],['#c87f0a','#f59e0b'],['#9333e8','#a855f7'],['#d91a6e','#ec4899'],['#0d9488','#14b8a6'],['#c2440f','#f97316'],['#c81e1e','#ef4444']];

var _cfg={card_size:'md',show_lufs:true,show_np:true,show_sites:true,show_ticker:true,show_hero:true,sort_level:false,hidden_streams:[],corp_mode:false,bauer_mode:false,hide_hdr:false,sound_alert:false,show_qr:false};
var _peaks={},_sortLev=false,_lastData=null,_lastChains=null,_chainLogos={};
var _liveActive=false,_targetLev={},_dispLev={},_rafTs=null,_cfgLoaded=false;
var _allStreams=[];  // for stream selector

/* ═══ Now-playing history ═══ */
var _npHistory={};  // cid → [{artist,title}, ...]
var MAX_NP_HIST=3;

/* ═══ Spotlight ═══ */
var _slIdx=0,_slTimer=null,SL_INTERVAL=15000;

/* ═══ Fault sound ═══ */
var _prevFaultIds={};  // cid → 'fault'|'ok' — track transitions
var _audioCtx=null;
function _playFaultTone(){
  try{
    if(!_audioCtx)_audioCtx=new(window.AudioContext||window.webkitAudioContext)();
    if(_audioCtx.state==='suspended')_audioCtx.resume();
    // Three descending tones — urgent but not jarring
    [0,.15,.3].forEach(function(delay,i){
      var osc=_audioCtx.createOscillator();var g=_audioCtx.createGain();
      osc.connect(g);g.connect(_audioCtx.destination);
      osc.type='sine';osc.frequency.value=[880,660,440][i];
      g.gain.setValueAtTime(.25,_audioCtx.currentTime+delay);
      g.gain.exponentialRampToValueAtTime(.001,_audioCtx.currentTime+delay+.35);
      osc.start(_audioCtx.currentTime+delay);osc.stop(_audioCtx.currentTime+delay+.35);
    });
  }catch(e){}
}

/* ═══ Hero level pulse — breathes with average audio level ═══ */
var _avgLevel=DB_FLOOR;
function _updateHeroPulse(){
  var pb=document.getElementById('hero-pulse-bg');
  if(!pb)return;
  // Map average level to opacity: -60dB → 0, -10dB → 0.9
  var norm=Math.max(0,Math.min(1,(_avgLevel-DB_FLOOR)/(-10-DB_FLOOR)));
  pb.style.opacity=norm*0.9;
}

/* ═══ Time-of-day background gradient ═══ */
function _updateDayGradient(){
  // Skip if Bauer or Corp theme is active — they have their own backgrounds
  if(document.body.classList.contains('bauer')||document.body.classList.contains('corp')){
    document.body.classList.remove('day-grad');
    document.body.style.background='';
    return;
  }
  document.body.classList.add('day-grad');
  var h=new Date().getHours();
  var bg;
  if(h>=6&&h<9) bg='radial-gradient(ellipse at 50% -5%,#2d1b69 0%,#1a0f3d 38%,#0d0820 100%)';
  else if(h>=9&&h<17) bg='radial-gradient(ellipse at 50% -5%,#14397a 0%,#07142b 38%,#040d1c 100%)';
  else if(h>=17&&h<21) bg='radial-gradient(ellipse at 50% -5%,#4a1942 0%,#1a0a2e 38%,#0d0518 100%)';
  else bg='radial-gradient(ellipse at 50% -5%,#0a1628 0%,#050d1c 38%,#020810 100%)';
  document.body.style.background=bg;
}
/* Don't run until config is loaded — otherwise it overrides Bauer/Corp */
setInterval(_updateDayGradient,60000);

/* ═══ QR code — served as static file, same as logos ═══ */

/* ═══ Artwork colour extraction ═══ */
var _glowCanvas=null;
function _extractGlow(img,cb){
  try{
    if(!_glowCanvas){_glowCanvas=document.createElement('canvas');_glowCanvas.width=1;_glowCanvas.height=1}
    var ctx=_glowCanvas.getContext('2d');
    ctx.drawImage(img,0,0,1,1);
    var d=ctx.getImageData(0,0,1,1).data;
    cb(d[0],d[1],d[2]);
  }catch(e){/* cross-origin or tainted canvas — ignore */}
}

function _colorFor(n){var h=0;for(var i=0;i<n.length;i++)h=(h*31+n.charCodeAt(i))&0x7fffffff;return AVATAR_COLORS[h%AVATAR_COLORS.length]}
function _initial(n){return(n.match(/[A-Z0-9]/i)||[n[0]||'?'])[0].toUpperCase()}

/* ── Config ── */
function applyConfig(){
  setSize(_cfg.card_size||'md');_sortLev=!!_cfg.sort_level;
  document.getElementById('btn-sort').classList.toggle('active',_sortLev);
  document.getElementById('cfg-lufs').checked=_cfg.show_lufs!==false;
  document.getElementById('cfg-np').checked=_cfg.show_np!==false;
  document.getElementById('cfg-sites').checked=_cfg.show_sites!==false;
  document.getElementById('cfg-ticker').checked=_cfg.show_ticker!==false;
  document.getElementById('cfg-hero').checked=_cfg.show_hero!==false;
  document.getElementById('cfg-corp').checked=!!_cfg.corp_mode;
  document.getElementById('cfg-bauer').checked=!!_cfg.bauer_mode;
  document.getElementById('cfg-hide-hdr').checked=!!_cfg.hide_hdr;
  var _qrCb=document.getElementById('cfg-qr');if(_qrCb)_qrCb.checked=!!_cfg.show_qr;
  var _sb=document.getElementById('btn-sound');if(_sb)_sb.classList.toggle('active',!!_cfg.sound_alert);
  document.querySelectorAll('[data-sz]').forEach(function(b){b.classList.toggle('active',b.dataset.sz===_cfg.card_size)});
  applyVis();
}
function applyVis(){
  document.querySelectorAll('.mc-lufs').forEach(function(e){e.style.display=(_cfg.show_lufs!==false)?'':'none'});
  document.querySelectorAll('.mc-np').forEach(function(e){e.style.display=(_cfg.show_np!==false)?'':'none'});
  document.querySelectorAll('.wb-site-hdr').forEach(function(e){e.style.display=(_cfg.show_sites!==false)?'':'none'});
  document.getElementById('wb-ticker').style.display=(_cfg.show_ticker!==false)?'':'none';
  document.getElementById('wb-hero').style.display=(_cfg.show_hero!==false)?'':'none';
  // Themes — mutually exclusive: bauer wins if both set
  document.body.classList.toggle('bauer',!!_cfg.bauer_mode);
  document.body.classList.toggle('corp',!!_cfg.corp_mode&&!_cfg.bauer_mode);
  // Hide header
  document.getElementById('wb-hdr').classList.toggle('hdr-hidden',!!_cfg.hide_hdr);
  // Time-of-day gradient
  _updateDayGradient();
}
function saveConfig(){
  _cfg.sort_level=_sortLev;
  fetch(_tkUrl('/api/wallboard/config'),{method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRFToken':_csrf()},
    body:JSON.stringify(_cfg)}).catch(function(){});
  try{localStorage.setItem('wb_cfg',JSON.stringify(_cfg))}catch(e){}
}
function isStreamVisible(site,name){
  var hs=_cfg.hidden_streams||[];if(!hs.length)return true;
  return hs.indexOf(site+'|'+name)<0;
}

/* ── Clock ── */
function _tick(){var d=new Date(),h=d.getHours(),m=d.getMinutes(),s=d.getSeconds();
  document.getElementById('wb-clock').textContent=(h<10?'0':'')+h+':'+(m<10?'0':'')+m+':'+(s<10?'0':'')+s}
setInterval(_tick,1000);_tick();

function setSize(sz){_cfg.card_size=sz;document.documentElement.style.setProperty('--mc-w',_sizes[sz]+'px');
  ['sm','md','lg'].forEach(function(s){document.getElementById('btn-'+s).classList.toggle('active',s===sz)});
  document.querySelectorAll('[data-sz]').forEach(function(b){b.classList.toggle('active',b.dataset.sz===sz)})}
function toggleSort(){_sortLev=!_sortLev;document.getElementById('btn-sort').classList.toggle('active',_sortLev);if(_lastData)renderMeters(_lastData);saveConfig()}

/* ── Fullscreen ── */
function toggleFs(){var r=document.documentElement;if(!document.fullscreenElement)(r.requestFullscreen||r.webkitRequestFullscreen||function(){}).call(r);else(document.exitFullscreen||document.webkitExitFullscreen||function(){}).call(document)}
var _hideT=null;
function _resetHide(){clearTimeout(_hideT);document.getElementById('wb-hdr').classList.remove('hide');_hideT=setTimeout(function(){document.getElementById('wb-hdr').classList.add('hide')},4000)}
document.addEventListener('fullscreenchange',function(){var f=!!document.fullscreenElement;document.getElementById('btn-fs').textContent=f?'✕ Exit':'⛶ Full';if(f){_resetHide();document.addEventListener('mousemove',_resetHide)}else{clearTimeout(_hideT);document.getElementById('wb-hdr').classList.remove('hide');document.removeEventListener('mousemove',_resetHide)}});

/* ── Drawer ── */
function openDrawer(){document.getElementById('wb-drawer').classList.add('open');document.getElementById('wb-overlay').classList.add('show');renderDrawerChains();renderDrawerStreams()}
function closeDrawer(){document.getElementById('wb-drawer').classList.remove('open');document.getElementById('wb-overlay').classList.remove('show')}

/* ── Helpers ── */
function levToH(db){return Math.max(0,Math.min(100,(db-DB_FLOOR)/(-DB_FLOOR)*100))}
function fmtLev(db){if(db<=DB_FLOOR)return'— dB';return(db>=0?'+':'')+db.toFixed(1)+' dB'}
function levCls(db){if(db>=-9)return'lc-alert';if(db>=-18)return'lc-warn';if(db<=-60)return'lc-low';return''}
function _updPk(k,l,n){var p=_peaks[k]||{val:DB_FLOOR,ts:0};if(l>=p.val){_peaks[k]={val:l,ts:n}}else{var e=n-p.ts;if(e>PEAK_HOLD){_peaks[k]={val:Math.max(DB_FLOOR,p.val-PEAK_RATE*(e-PEAK_HOLD)/100),ts:p.ts}}else _peaks[k]=p}return _peaks[k].val}
function fmtTime(ts){var d=new Date(ts*1000);return('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2)}

/* ═══ Hero ═══ */
function updateHero(chains){
  var hero=document.getElementById('wb-hero'),ic=document.getElementById('hero-icon'),
      ti=document.getElementById('hero-title'),su=document.getElementById('hero-sub'),
      ba=document.getElementById('hero-badge');
  var faultOverlay=document.getElementById('wb-fault-overlay');
  var faultBanner=document.getElementById('wb-fault-banner');
  var faultText=document.getElementById('fault-banner-text');
  if(!chains||!chains.length){
    hero.className='loading';ic.textContent='📡';ti.textContent='No stations configured';su.textContent='Add broadcast chains in Settings.';ba.textContent='';
    if(faultOverlay)faultOverlay.classList.remove('active');
    if(faultBanner)faultBanner.classList.remove('active');
    return;
  }
  var faulted=chains.filter(function(c){return(c.display_status||c.status)==='fault'});
  if(!faulted.length){
    hero.className='ok';ic.textContent='✅';
    ti.textContent='All Stations On Air';
    su.textContent=chains.length+' station'+(chains.length>1?'s':'')+' running normally — no action required.';
    ba.textContent='ALL CLEAR';
    if(faultOverlay)faultOverlay.classList.remove('active');
    if(faultBanner)faultBanner.classList.remove('active');
  }else if(faulted.length===1){
    hero.className='fault';ic.textContent='🔴';
    ti.textContent=(faulted[0].name||'Station')+' — SIGNAL FAULT';
    su.textContent='Engineering have been alerted automatically.';
    ba.textContent='1 FAULT';
    if(faultOverlay)faultOverlay.classList.add('active');
    if(faultBanner)faultBanner.classList.add('active');
    if(faultText)faultText.textContent='SIGNAL FAULT — '+(faulted[0].name||'STATION').toUpperCase();
  }else{
    hero.className='fault';ic.textContent='🔴';
    ti.textContent=faulted.length+' STATION FAULTS ACTIVE';
    su.textContent=faulted.map(function(c){return c.name}).slice(0,3).join(', ')+(faulted.length>3?' + '+(faulted.length-3)+' more':'');
    ba.textContent=faulted.length+' FAULTS';
    if(faultOverlay)faultOverlay.classList.add('active');
    if(faultBanner)faultBanner.classList.add('active');
    if(faultText)faultText.textContent=faulted.length+' SIGNAL FAULTS — '+faulted.map(function(c){return(c.name||'?').toUpperCase()}).join(' / ');
  }
}

/* ═══ Chains — DOM-diffing (no innerHTML rebuild, no image flicker) ═══ */
function _ccStatusCls(st){return st==='fault'?'s-fault':(st==='ok'||st==='pending'||st==='adbreak')?'s-ok':'s-unk'}
function _ccStatusTxt(st){return st==='fault'?'⚠ FAULT':(st==='ok'||st==='pending'||st==='adbreak')?'● ON AIR':'○ —'}
function _ccCardCls(st){return'cc cc-'+(st==='ok'||st==='pending'||st==='adbreak'?'ok':st==='fault'?'fault':'')}

function renderChains(chains){
  var el=document.getElementById('wb-chains');
  if(!chains||!chains.length){el.innerHTML='';return}

  // Build map of existing cards
  var existingMap={};
  el.querySelectorAll('.cc[data-cid]').forEach(function(c){existingMap[c.dataset.cid]=c});
  var seenIds={};

  chains.forEach(function(ch){
    var cid=ch.id;
    seenIds[cid]=true;
    var st=ch.display_status||ch.status||'unknown';
    var col=_colorFor(ch.name||'?');
    var rpuid=(_cfg.chain_stations||{})[cid]||'';
    var np=rpuid?(_npData[rpuid]||null):null;
    var hasArt=np&&np.artwork;
    var hasLogo=_chainLogos[cid];

    var card=existingMap[cid];
    if(!card){
      // Create new card with stable structure
      card=document.createElement('div');
      card.dataset.cid=cid;
      card.innerHTML=
        '<div class="cc-visual"></div>'
        +'<div class="cc-name"></div>'
        +'<div class="cc-status"><span class="cc-sdot"></span><span class="cc-stxt"></span></div>'
        +'<div class="cc-art-wrap"></div>'
        +'<div class="cc-np-wrap"></div>'
        +'<div class="cc-np-history"></div>'
        +'<div class="cc-nodes"></div>'
        +'<div class="cc-sparkline"><canvas></canvas></div>'
        +'<div class="cc-health"><div class="cc-health-fill"></div></div>'
        +'<div class="cc-sla"></div>'
        +'<div class="cc-qr"></div>';
      el.appendChild(card);
    }

    // Update class (status glow etc)
    card.className=_ccCardCls(st);

    // Logo or avatar at the top
    var vizEl=card.querySelector('.cc-visual');
    if(hasLogo){
      var logoImg=vizEl.querySelector('.cc-logo');
      var logoUrl=_tkUrl('/wallboard/logo/'+cid);
      if(!logoImg){
        logoImg=document.createElement('img');logoImg.className='cc-logo';logoImg.alt='';
        logoImg.src=logoUrl;
        vizEl.insertBefore(logoImg,vizEl.firstChild);
      }
      var oldAv=vizEl.querySelector('.cc-avatar');if(oldAv)oldAv.remove();
    }else{
      var avEl=vizEl.querySelector('.cc-avatar');
      if(!avEl){
        var oldLogo=vizEl.querySelector('.cc-logo');if(oldLogo)oldLogo.remove();
        avEl=document.createElement('div');avEl.className='cc-avatar';
        avEl.style.background='linear-gradient(135deg,'+col[0]+','+col[1]+')';
        avEl.textContent=_initial(ch.name||'?');
        vizEl.insertBefore(avEl,vizEl.firstChild);
      }
    }

    // Artwork — own row above now-playing, with crossfade
    var artWrap=card.querySelector('.cc-art-wrap');
    var artImg=artWrap?artWrap.querySelector('.cc-art'):null;
    if(hasArt&&artWrap){
      if(!artImg){
        artImg=document.createElement('img');artImg.className='cc-art art-entering';artImg.alt='';
        artWrap.appendChild(artImg);
        setTimeout(function(){artImg.classList.remove('art-entering')},30);
      }
      if(artImg.src!==np.artwork){
        artImg.classList.add('art-entering');
        var _art=artImg;
        setTimeout(function(){
          _art.src=np.artwork;
          _art.onload=function(){
            _art.classList.remove('art-entering');
            _extractGlow(_art,function(r,g,b){
              card.style.borderColor='rgba('+r+','+g+','+b+',.35)';
              card.style.boxShadow='0 0 28px rgba('+r+','+g+','+b+',.15),0 4px 24px rgba(0,0,0,.3)';
              card.dataset.glow='1';
            });
          };
        },250);
      }
    }else if(artWrap){
      if(artImg){artImg.remove()}
      if(card.dataset.glow){delete card.dataset.glow;card.style.borderColor='';card.style.boxShadow=''}
    }

    // Name
    var nameEl=card.querySelector('.cc-name');
    if(nameEl.textContent!==ch.name){nameEl.textContent=ch.name;nameEl.title=ch.name}

    // Status badge
    var statusEl=card.querySelector('.cc-status');
    var sCls=_ccStatusCls(st);
    if(!statusEl.classList.contains(sCls)){statusEl.className='cc-status '+sCls}
    var stxtEl=statusEl.querySelector('.cc-stxt');
    var sTxt=_ccStatusTxt(st);
    if(stxtEl&&stxtEl.textContent!==sTxt)stxtEl.textContent=sTxt;

    // Now playing — with animated transitions and history tracking
    var npWrap=card.querySelector('.cc-np-wrap');
    var npKey=np?(np.artist||'')+'\t'+(np.title||''):'';
    if(np&&(np.artist||np.title||np.show)){
      var npInner='';
      if(np.show)npInner+='<div class="cc-np-show">'+_e(np.show)+'</div>';
      if(np.artist||np.title){
        var track=np.artist?'<span class="cc-np-artist">'+_e(np.artist)+'</span> — '+_e(np.title):_e(np.title);
        npInner+='<div class="cc-np-track">'+track+'</div>';
      }
      if(npWrap._lastNp!==npInner){
        // Track history
        if(npWrap._lastNpKey&&npWrap._lastNpKey!==npKey){
          var hist=_npHistory[cid]||[];
          hist.unshift({artist:npWrap._lastArtist||'',title:npWrap._lastTitle||''});
          if(hist.length>MAX_NP_HIST)hist.length=MAX_NP_HIST;
          _npHistory[cid]=hist;
        }
        npWrap._lastArtist=np.artist||'';npWrap._lastTitle=np.title||'';
        npWrap.innerHTML=npInner;npWrap._lastNp=npInner;
        // Slide-in animation
        npWrap.querySelectorAll('.cc-np-track,.cc-np-show').forEach(function(el){
          el.classList.add('np-entering');
          setTimeout(function(){el.classList.remove('np-entering')},30);
        });
      }
    }else{
      if(npWrap.innerHTML){npWrap.innerHTML='';npWrap._lastNp=''}
    }
    npWrap._lastNpKey=npKey;

    // Now-playing history (last 3 tracks, fading)
    var histEl=card.querySelector('.cc-np-history');
    var hist=_npHistory[cid]||[];
    if(hist.length&&np){
      var hHtml='';hist.forEach(function(h){
        var t=h.artist?(h.artist+' — '+h.title):h.title;
        hHtml+='<div class="cc-np-hist-item">'+_e(t)+'</div>';
      });
      if(histEl._lastH!==hHtml){histEl.innerHTML=hHtml;histEl._lastH=hHtml}
    }else if(histEl.innerHTML){histEl.innerHTML='';histEl._lastH=''}

    // Nodes
    var nodesEl=card.querySelector('.cc-nodes');
    var nodesHtml='';
    _flatN(ch.nodes||[]).forEach(function(n,i){
      if(i>0)nodesHtml+='<span class="cc-arr">▸</span>';
      nodesHtml+='<span class="cc-nd '+(n.status||'unknown')+'" title="'+_e(n.label||n.stream||n.name||'?')+'"></span>';
    });
    if(nodesEl._lastHtml!==nodesHtml){nodesEl.innerHTML=nodesHtml;nodesEl._lastHtml=nodesHtml}

    // Health bar
    var hp=ch.sla_pct;var hpW=hp!=null?Math.max(0,Math.min(100,hp)):100;
    var hpCol=hpW>=99.5?'var(--ok)':hpW>=98?'var(--wn)':'var(--al)';
    var hpFill=card.querySelector('.cc-health-fill');
    if(hpFill){hpFill.style.width=hpW+'%';hpFill.style.background=hpCol}

    // SLA
    var slaEl=card.querySelector('.cc-sla');
    var slaTxt=(hp!=null&&hp<100)?hp.toFixed(1)+'% uptime':'';
    if(slaEl&&slaEl.textContent!==slaTxt)slaEl.textContent=slaTxt;

    // QR code — static file, served exactly like logos
    var qrEl=card.querySelector('.cc-qr');
    if(qrEl){
      if(_cfg.show_qr){
        qrEl.classList.add('qr-visible');
        if(!qrEl._rendered){
          var qrImg=document.createElement('img');
          qrImg.style.cssText='width:80px;height:80px;border-radius:6px;background:#fff;padding:3px';
          qrImg.alt='Scan to listen';
          qrImg.src=_tkUrl('/wallboard/qr/'+cid);
          qrEl.appendChild(qrImg);
          qrEl._rendered=true;
        }
      }else{
        qrEl.classList.remove('qr-visible');
        if(qrEl._rendered){qrEl.innerHTML='';qrEl._rendered=null}
      }
    }
  });

  // Remove cards for chains no longer present
  Object.keys(existingMap).forEach(function(cid){
    if(!seenIds[cid])existingMap[cid].remove();
  });

  // Auto-scale: set count class on container so cards fill the width
  var chainsEl=document.getElementById('wb-chains');
  var n=chains.length;
  ['cc-count-1','cc-count-2','cc-count-3','cc-count-4','cc-count-5'].forEach(function(c){chainsEl.classList.remove(c)});
  if(n<=5)chainsEl.classList.add('cc-count-'+n);

  // Fault sound alert
  if(_cfg.sound_alert){
    chains.forEach(function(ch){
      var st=ch.display_status||ch.status||'unknown';
      var prev=_prevFaultIds[ch.id];
      if(st==='fault'&&prev!=='fault')_playFaultTone();
      _prevFaultIds[ch.id]=st;
    });
  }else{
    chains.forEach(function(ch){_prevFaultIds[ch.id]=ch.display_status||ch.status||'unknown'});
  }

  // Sparklines — draw fault events on each card's canvas
  _drawSparklines(chains);

  // Spotlight — update rotating hero
  _updateSpotlight(chains);
}
function _flatN(nodes){var o=[];(nodes||[]).forEach(function(n){if(n.type==='stack')(n.nodes||[]).forEach(function(s){o.push(s)});else o.push(n)});return o}

/* ═══ Fault sparkline — 24h timeline on each chain card ═══ */
var _faultHistory={}; // cid → [{start,end},...] loaded from alerts
function _loadFaultHistory(alerts){
  var now=Date.now()/1000,cutoff=now-86400;
  var byChain={};
  (alerts||[]).forEach(function(a){
    var cid=a.chain_id;if(!cid)return;
    var t=a.time||0;if(t<cutoff)return;
    if(!byChain[cid])byChain[cid]=[];
    byChain[cid].push({time:t,ok:a.ok});
  });
  // Merge into fault spans per chain
  Object.keys(byChain).forEach(function(cid){
    var evts=byChain[cid].sort(function(a,b){return a.time-b.time});
    var spans=[];var fStart=null;
    evts.forEach(function(e){
      if(!e.ok&&fStart===null)fStart=e.time;
      else if(e.ok&&fStart!==null){spans.push({s:fStart,e:e.time});fStart=null}
    });
    if(fStart!==null)spans.push({s:fStart,e:now});
    _faultHistory[cid]=spans;
  });
}
function _drawSparklines(chains){
  var now=Date.now()/1000,start=now-86400;
  chains.forEach(function(ch){
    var card=document.querySelector('.cc[data-cid="'+ch.id+'"]');
    if(!card)return;
    var cvs=card.querySelector('.cc-sparkline canvas');
    if(!cvs)return;
    var w=cvs.offsetWidth||200,h=cvs.offsetHeight||16;
    if(cvs.width!==w||cvs.height!==h){cvs.width=w;cvs.height=h}
    var ctx=cvs.getContext('2d');
    ctx.clearRect(0,0,w,h);
    // Background — subtle time grid
    ctx.fillStyle='rgba(255,255,255,.03)';ctx.fillRect(0,0,w,h);
    // Hour marks
    ctx.fillStyle='rgba(255,255,255,.06)';
    for(var hr=0;hr<24;hr++){var x=Math.round(hr/24*w);ctx.fillRect(x,0,1,h)}
    // "Now" marker
    ctx.fillStyle='rgba(23,168,255,.3)';ctx.fillRect(w-1,0,1,h);
    // Fault spans
    var spans=_faultHistory[ch.id]||[];
    ctx.fillStyle='rgba(239,68,68,.6)';
    spans.forEach(function(sp){
      var x1=Math.max(0,Math.round((sp.s-start)/86400*w));
      var x2=Math.min(w,Math.round((sp.e-start)/86400*w));
      if(x2>x1)ctx.fillRect(x1,2,Math.max(2,x2-x1),h-4);
    });
    // Current fault — live red bar extending to now
    var st=ch.display_status||ch.status;
    if(st==='fault'){
      var faultStart=ch.fault_since||now-60;
      var fx=Math.max(0,Math.round((faultStart-start)/86400*w));
      ctx.fillStyle='rgba(239,68,68,.8)';ctx.fillRect(fx,1,Math.max(3,w-fx),h-2);
    }
  });
}

/* ═══ Spotlight — rotating now-playing hero ═══ */
function _updateSpotlight(chains){
  // Collect chains with now-playing data
  var items=[];
  (chains||[]).forEach(function(ch){
    var rpuid=(_cfg.chain_stations||{})[ch.id]||'';
    var np=rpuid?(_npData[rpuid]||null):null;
    if(np&&(np.artist||np.title))items.push({chain:ch,np:np,rpuid:rpuid});
  });
  var sl=document.getElementById('wb-spotlight');
  if(items.length<2){sl.classList.remove('active');clearInterval(_slTimer);_slTimer=null;return}
  sl.classList.add('active');
  if(_slIdx>=items.length)_slIdx=0;
  var cur=items[_slIdx];
  // Build/update DOM
  var body=sl.querySelector('.sl-body');
  if(!body){
    sl.innerHTML='<img class="sl-logo" alt=""><img class="sl-art" alt="">'
      +'<div class="sl-body"><div class="sl-station"></div><div class="sl-track"></div><div class="sl-artist"></div></div>'
      +'<div class="sl-dots"></div>';
    body=sl.querySelector('.sl-body');
  }
  var logoEl=sl.querySelector('.sl-logo'),artEl=sl.querySelector('.sl-art');
  var stEl=body.querySelector('.sl-station'),trEl=body.querySelector('.sl-track'),arEl=body.querySelector('.sl-artist');
  var hasLogo=_chainLogos[cur.chain.id];
  if(hasLogo){logoEl.src=_tkUrl('/wallboard/logo/'+cur.chain.id);logoEl.style.display=''}
  else logoEl.style.display='none';
  if(cur.np.artwork){artEl.src=cur.np.artwork;artEl.style.display=''}
  else artEl.style.display='none';
  stEl.textContent=cur.chain.name||'';
  trEl.textContent=cur.np.title||'';
  arEl.textContent=cur.np.artist||'';
  // Dots
  var dots=sl.querySelector('.sl-dots');
  var dHtml='';items.forEach(function(_,i){dHtml+='<span class="sl-dot'+(i===_slIdx?' active':'')+'"></span>'});
  dots.innerHTML=dHtml;
  // Auto-advance timer
  if(!_slTimer)_slTimer=setInterval(function(){
    _slIdx++;if(_slIdx>=items.length)_slIdx=0;
    // Animate transition
    var b=sl.querySelector('.sl-body'),a=sl.querySelector('.sl-art');
    if(b)b.classList.add('sl-entering');if(a)a.classList.add('sl-entering');
    setTimeout(function(){_updateSpotlight(_lastChains)},300);
    setTimeout(function(){
      var b2=sl.querySelector('.sl-body'),a2=sl.querySelector('.sl-art');
      if(b2)b2.classList.remove('sl-entering');if(a2)a2.classList.remove('sl-entering');
    },350);
  },SL_INTERVAL);
}

/* ═══ Meters ═══ */
function buildCard(key,st,site){
  var el=document.createElement('div');el.className='mc';el.dataset.key=key;
  el.innerHTML='<div class="mc-head"><div class="mc-name" title="'+_e(st.name)+'">'+_e(st.name)+'</div>'
    +'<div class="mc-sub">'+_e(site)+'</div></div>'
    +'<div class="mc-body">'
    +'<div class="mtr-wrap mtr-mono"><div class="mtr-fill" style="height:0%"></div><div class="mtr-peak" style="bottom:0%;opacity:0"></div></div>'
    +'<div class="mtr-stereo">'
    +'<div class="mtr-ch"><div class="mtr-lr" data-ch="L"><div class="mtr-fill" style="height:0%"></div><div class="mtr-peak" style="bottom:0%;opacity:0"></div></div><div class="mtr-ch-label">L</div></div>'
    +'<div class="mtr-ch"><div class="mtr-lr" data-ch="R"><div class="mtr-fill" style="height:0%"></div><div class="mtr-peak" style="bottom:0%;opacity:0"></div></div><div class="mtr-ch-label">R</div></div>'
    +'</div>'
    +'<div class="mc-lev lc-low">— dB</div>'
    +'<div class="mc-lufs">LUFS-I —</div></div>'
    +'<div class="mc-np"></div>'
    +'<div class="mc-foot"><span class="sp sp-si">—</span><span class="mc-rtp"></span></div>';
  return el;
}
function updateCard(el,st){
  var lev=st.level_dbfs,isA=(st.ai_status||'').indexOf('[ALERT]')>=0,isW=(st.ai_status||'').indexOf('[WARN]')>=0;
  var isSil=st.silence_active||lev<=-60,isOk=!isA&&!isW&&!isSil&&lev>-60;
  el.classList.toggle('mc-alert',isA);el.classList.toggle('mc-warn',!isA&&isW);
  el.classList.toggle('mc-silent',!isA&&!isW&&isSil);el.classList.toggle('mc-ok',isOk);
  if(st.stereo)el.dataset.stereo='1';else delete el.dataset.stereo;
  /* Always feed poll data into _targetLev as a fallback.  When livePoll
     is active it overwrites at 150 ms — but if livePoll misses a site
     (e.g. token-auth edge case) the 1.5 s poll data keeps bars moving. */
  var key=el.dataset.key,now=Date.now();if(key){_targetLev[key]=lev;_updPk(key,lev,now);
    if(st.stereo&&st.level_dbfs_l!=null){_targetLev[key+'|L']=st.level_dbfs_l;_updPk(key+'|L',st.level_dbfs_l,now)}
    if(st.stereo&&st.level_dbfs_r!=null){_targetLev[key+'|R']=st.level_dbfs_r;_updPk(key+'|R',st.level_dbfs_r,now)}}
  var lufsEl=el.querySelector('.mc-lufs');if(lufsEl){var li=st.lufs_i;lufsEl.textContent=(li&&li>-70)?'LUFS-I '+li.toFixed(1):'LUFS-I —'}
  var npEl=el.querySelector('.mc-np');if(npEl)npEl.textContent=st.now_playing||'';
  var sp=el.querySelector('.sp');
  if(sp){if(isA){sp.className='sp sp-al';sp.textContent='⚠ ALERT'}
    else if(isW){sp.className='sp sp-wn';sp.textContent='⚡ WARN'}
    else if(isSil){sp.className='sp sp-si';sp.textContent='◎ SILENT'}
    else{sp.className='sp sp-ok';sp.textContent='● OK'}}
  var rtpEl=el.querySelector('.mc-rtp');
  if(rtpEl){var rtp=st.rtp_loss_pct||0;if(rtp>0){rtpEl.textContent=rtp.toFixed(1)+'% loss';rtpEl.style.color=rtp>=2?'var(--al)':'var(--wn)'}else rtpEl.textContent=''}
}

function renderMeters(data){
  _lastData=data;var now=Date.now(),root=document.getElementById('wb-meters');
  var flat=[];_allStreams=[];
  (data.sites||[]).forEach(function(site){(site.streams||[]).forEach(function(st){
    _allStreams.push({site:site.site,name:st.name});
    if(isStreamVisible(site.site,st.name))flat.push({st:st,siteName:site.site,online:site.online});
  })});
  if(_sortLev)flat.sort(function(a,b){return b.st.level_dbfs-a.st.level_dbfs});
  var existing={};root.querySelectorAll('.mc').forEach(function(el){existing[el.dataset.key]=el});var seen={};
  if(_sortLev){
    var fs=root.querySelector('.wb-site.wb-flat');if(!fs){root.innerHTML='';fs=_mkSec('wb-site wb-flat',null);root.appendChild(fs)}
    var mg=fs.querySelector('.wb-grid');
    flat.forEach(function(item){var key=item.siteName+'|'+item.st.name;seen[key]=true;_updPk(key,item.st.level_dbfs,now);
      var card=existing[key];if(!card)card=buildCard(key,item.st,item.siteName);if(card.parentElement!==mg)mg.appendChild(card);updateCard(card,item.st)});
  }else{
    root.querySelectorAll('.wb-flat').forEach(function(el){el.remove()});
    var siteOrder=(data.sites||[]).map(function(s){return s.site});
    root.querySelectorAll('.wb-site[data-site]').forEach(function(el){if(siteOrder.indexOf(el.dataset.site)===-1)el.remove()});
    var siteMap={};(data.sites||[]).forEach(function(s){siteMap[s.site]=s});
    siteOrder.forEach(function(siteName){var site=siteMap[siteName];
      var visStreams=(site.streams||[]).filter(function(st){return isStreamVisible(siteName,st.name)});
      if(!visStreams.length)return;
      var sid='wbs-'+siteName.replace(/[^a-z0-9]/gi,'_');
      var sec=document.getElementById(sid);if(!sec){sec=_mkSec('wb-site',siteName);sec.id=sid;root.appendChild(sec)}
      var dot=sec.querySelector('.wb-sdot');if(dot)dot.className='wb-sdot'+(site.online?'':' off');
      var hdr=sec.querySelector('.wb-site-hdr');if(hdr)hdr.style.display=(_cfg.show_sites!==false)?'':'none';
      var mg=sec.querySelector('.wb-grid');
      visStreams.forEach(function(st){var key=siteName+'|'+st.name;seen[key]=true;_updPk(key,st.level_dbfs,now);
        var card=existing[key];if(!card)card=buildCard(key,st,siteName);if(card.parentElement!==mg)mg.appendChild(card);updateCard(card,st)})});
  }
  Object.keys(existing).forEach(function(k){if(!seen[k])existing[k].remove()});
  var total=flat.length,alerts=flat.filter(function(i){return(i.st.ai_status||'').indexOf('[ALERT]')>=0}).length;
  var silences=flat.filter(function(i){return i.st.silence_active}).length;
  var nChains=_lastChains?_lastChains.length:0;
  document.getElementById('wb-meta').textContent=
    (nChains?nChains+' station'+(nChains!==1?'s':'')+' · ':'')+total+' stream'+(total!==1?'s':'')+' · '+(data.sites||[]).length+' site'+((data.sites||[]).length!==1?'s':'')+(silences?' · '+silences+' silent':'');
  var badge=document.getElementById('wb-alert-badge');badge.textContent='⚠ '+alerts+' ALERT'+(alerts!==1?'S':'');badge.style.display=alerts>0?'inline-block':'none';
  applyVis();
  if(total===0&&!_allStreams.length)root.innerHTML='<div class="wb-empty"><div class="wb-empty-ico">📡</div><div style="font-size:15px;font-weight:700">No streams found</div><div style="font-size:12px">Connect a site or enable local monitoring.</div></div>';
}
function _mkSec(cls,siteName){var sec=document.createElement('div');sec.className=cls;
  if(siteName!==null){sec.dataset.site=siteName;sec.innerHTML='<div class="wb-site-hdr"><span class="wb-sdot"></span>'+_e(siteName)+'</div><div class="wb-grid"></div>'}
  else sec.innerHTML='<div class="wb-grid"></div>';return sec}

/* ═══ rAF ═══ */
function _meterRaf(ts){
  var dt=_rafTs?Math.min((ts-_rafTs)/1000,.1):0;_rafTs=ts;
  Object.keys(_targetLev).forEach(function(key){
    var target=_targetLev[key],cur=(_dispLev[key]!=null)?_dispLev[key]:target;
    if(target>cur)cur=Math.min(target,cur+ATTACK_RATE*dt);else cur=Math.max(target,cur-DECAY_RATE*dt);
    _dispLev[key]=cur;
    var chS='';if(key.length>2&&key.charAt(key.length-2)==='|')chS=key.charAt(key.length-1);
    if(chS==='L'||chS==='R'){
      var bk=key.slice(0,-2),esc=bk.replace(/\\/g,'\\\\').replace(/"/g,'\\"');
      var card=document.querySelector('.mc[data-key="'+esc+'"]');if(!card)return;
      var lr=card.querySelector('.mtr-lr[data-ch="'+chS+'"]');if(!lr)return;
      var fill=lr.querySelector('.mtr-fill');if(fill)fill.style.height=levToH(cur)+'%';
      var pk=_peaks[key]?_peaks[key].val:DB_FLOOR,pkEl=lr.querySelector('.mtr-peak');
      if(pkEl){pkEl.style.bottom=levToH(pk)+'%';pkEl.style.opacity=pk>DB_FLOOR?'.82':'0'}return}
    var esc=key.replace(/\\/g,'\\\\').replace(/"/g,'\\"');
    var card=document.querySelector('.mc[data-key="'+esc+'"]');if(!card)return;
    var mono=card.querySelector('.mtr-mono'),fill=mono?mono.querySelector('.mtr-fill'):null;
    if(fill)fill.style.height=levToH(cur)+'%';
    var pk=_peaks[key]?_peaks[key].val:DB_FLOOR,pkEl=mono?mono.querySelector('.mtr-peak'):null;
    if(pkEl){pkEl.style.bottom=levToH(pk)+'%';pkEl.style.opacity=pk>DB_FLOOR?'.82':'0'}
    var levEl=card.querySelector('.mc-lev');if(levEl){levEl.textContent=fmtLev(cur);levEl.className='mc-lev '+levCls(cur)}
  });
  requestAnimationFrame(_meterRaf);
}
requestAnimationFrame(_meterRaf);

/* ═══ Polling ═══ */
function poll(){
  fetch(_tkUrl('/api/wallboard/data'),{credentials:'same-origin'}).then(function(r){return r.json()}).then(function(d){
    if(d.config&&!_cfgLoaded){_cfg=Object.assign(_cfg,d.config,_urlOverrides);applyConfig();_cfgLoaded=true}
    _chainLogos=d.chain_logos||{};_chainLogos._ts=Date.now();
    if(d.chain_faults)_loadFaultHistory(d.chain_faults);
    renderMeters(d);buildTicker(d.alerts||[]);
  }).catch(function(){});
}
function chainPoll(){
  fetch(_tkUrl('/api/chains/status'),{credentials:'same-origin'}).then(function(r){return r.ok?r.json():Promise.reject()}).then(function(d){
    _lastChains=d.results||[];
    updateHero(_lastChains);renderChains(_lastChains);
  }).catch(function(){});
}
function livePoll(){
  fetch(_tkUrl('/api/hub/live_levels'),{credentials:'same-origin'}).then(function(r){return r.ok?r.json():Promise.reject()}).then(function(data){
    _liveActive=true;var now=Date.now();
    var _levSum=0,_levN=0;
    Object.keys(data).forEach(function(siteName){(data[siteName]||[]).forEach(function(s){
      var key=siteName+'|'+s.name,lev=(s.level_dbfs==null)?DB_FLOOR:s.level_dbfs;
      _targetLev[key]=lev;_updPk(key,lev,now);
      if(lev>DB_FLOOR){_levSum+=lev;_levN++}
      if(s.level_dbfs_l!=null&&s.level_dbfs_r!=null){
        _targetLev[key+'|L']=s.level_dbfs_l;_updPk(key+'|L',s.level_dbfs_l,now);
        _targetLev[key+'|R']=s.level_dbfs_r;_updPk(key+'|R',s.level_dbfs_r,now);
        var esc=key.replace(/\\/g,'\\\\').replace(/"/g,'\\"');
        var card=document.querySelector('.mc[data-key="'+esc+'"]');if(card)card.dataset.stereo='1'}
    })});
    if(_levN)_avgLevel=_levSum/_levN;
    _updateHeroPulse();
  }).catch(function(){});
}
/* ═══ Now Playing (Planet Radio) ═══ */
var _npData={};       // rpuid → {artist,title,artwork}
var _npStations=null; // [{rpuid,name},...]
var NP_MS=15000;

function npPoll(){
  var map=_cfg.chain_stations||{};
  var rpuids=[];Object.keys(map).forEach(function(cid){var r=map[cid];if(r&&rpuids.indexOf(r)<0)rpuids.push(r)});
  if(!rpuids.length)return;
  rpuids.forEach(function(rpuid){
    fetch(_tkUrl('/api/nowplaying/'+encodeURIComponent(rpuid)),{credentials:'same-origin'})
      .then(function(r){return r.ok?r.json():Promise.reject()})
      .then(function(d){
        _npData[rpuid]=d;
        if(_lastChains)renderChains(_lastChains);
      }).catch(function(){});
  });
}
function loadNpStations(){
  fetch(_tkUrl('/api/nowplaying_stations'),{credentials:'same-origin'})
    .then(function(r){return r.ok?r.json():Promise.reject()})
    .then(function(d){_npStations=d||[]})
    .catch(function(){_npStations=[]});
}
loadNpStations();

poll();chainPoll();livePoll();npPoll();
setInterval(poll,POLL_MS);setInterval(chainPoll,CHAIN_MS);setInterval(livePoll,LIVE_MS);setInterval(npPoll,NP_MS);

/* ═══ Ticker ═══ */
function buildTicker(alerts){
  var el=document.getElementById('wb-ticker-inner');
  if(!alerts||!alerts.length){el.innerHTML='<span class="tk-item">No recent alerts</span>';return}
  var items=alerts.slice(0,15).map(function(a){
    var cls=a.ok?'tk-ok':'tk-al';var label=(a.site||'')+(a.stream?' · '+a.stream:'');
    return '<span class="tk-item"><span class="tk-time">'+fmtTime(a.time)+'</span>'
      +'<span class="tk-site">'+_e(label)+'</span><span class="tk-sep">—</span>'
      +'<span class="'+cls+'">'+_e(a.msg||a.type)+'</span></span>'});
  el.innerHTML=items.concat(items).join('');
  el.style.animation='none';el.offsetWidth;
  el.style.animation='tk-scroll '+Math.max(20,el.scrollWidth/2/60)+'s linear infinite';
}

/* ═══ Drawer: chains ═══ */
function renderDrawerChains(){
  var el=document.getElementById('dr-chains');
  if(!_lastChains||!_lastChains.length){el.innerHTML='<span style="color:var(--mu);font-size:12px">No chains configured</span>';return}
  var csMap=_cfg.chain_stations||{};
  var html='';_lastChains.forEach(function(ch){
    var hasLogo=_chainLogos[ch.id];var col=_colorFor(ch.name||'?');
    var logo=hasLogo?'<img class="dr-ch-logo" src="'+_tkUrl('/wallboard/logo/'+_e(ch.id)+'?_='+Date.now())+'" alt="">'
      :'<div class="dr-ch-av" style="background:linear-gradient(135deg,'+col[0]+','+col[1]+')">'+_initial(ch.name||'?')+'</div>';
    // Planet Radio station selector
    var curRpuid=csMap[ch.id]||'';
    var opts='<option value="">— None —</option>';
    if(_npStations){_npStations.forEach(function(s){
      opts+='<option value="'+_e(s.rpuid)+'"'+(s.rpuid===curRpuid?' selected':'')+'>'+_e(s.name)+'</option>';
    })}
    var selHtml='<select data-np-chain="'+_e(ch.id)+'" style="background:#0d1e40;border:1px solid var(--bor);border-radius:5px;color:var(--tx);padding:3px 6px;font-size:11px;max-width:160px;font-family:inherit">'+opts+'</select>';
    html+='<div class="dr-chain">'+logo+'<div class="dr-ch-info"><div class="dr-ch-name">'+_e(ch.name)+'</div>'
      +'<div class="dr-ch-actions"><button class="btn bp bs" data-upload-logo="'+_e(ch.id)+'">Upload Logo</button>'
      +(hasLogo?'<button class="btn bd bs" data-rm-logo="'+_e(ch.id)+'">Remove</button>':'')
      +'</div>'
      +'<div style="margin-top:4px;display:flex;align-items:center;gap:6px"><span style="font-size:10px;color:var(--mu);white-space:nowrap">Now Playing:</span>'+selHtml+'</div>'
      +'</div></div>'});
  el.innerHTML=html;
}

/* ═══ Drawer: streams ═══ */
function renderDrawerStreams(){
  var el=document.getElementById('dr-streams');
  if(!_allStreams.length){el.innerHTML='<span style="color:var(--mu);font-size:12px">No streams found</span>';return}
  var hs=_cfg.hidden_streams||[];
  var html='';_allStreams.forEach(function(s){
    var k=s.site+'|'+s.name;var checked=hs.indexOf(k)<0;
    html+='<div class="dr-stream"><input type="checkbox" data-stream-key="'+_e(k)+'"'+(checked?' checked':'')+'>'
      +'<label>'+_e(s.name)+'</label><span class="dr-stream-site">'+_e(s.site)+'</span></div>';
  });
  el.innerHTML=html;
}

function uploadLogo(cid){
  var inp=document.createElement('input');inp.type='file';inp.accept='image/*';
  inp.onchange=function(){if(!inp.files[0])return;var fd=new FormData();fd.append('logo',inp.files[0]);fd.append('_csrf_token',_csrf());
    fetch(_tkUrl('/api/wallboard/logo/'+encodeURIComponent(cid)),{method:'POST',credentials:'same-origin',headers:{'X-CSRFToken':_csrf()},body:fd})
    .then(function(r){return r.json()}).then(function(d){if(d.ok){_chainLogos[cid]=true;_chainLogos._ts=Date.now();renderDrawerChains();if(_lastChains)renderChains(_lastChains)}}).catch(function(){})};
  inp.click();
}
function removeLogo(cid){
  fetch(_tkUrl('/api/wallboard/logo/'+encodeURIComponent(cid)),{method:'DELETE',credentials:'same-origin',headers:{'X-CSRFToken':_csrf()}})
  .then(function(r){return r.json()}).then(function(d){if(d.ok){delete _chainLogos[cid];_chainLogos._ts=Date.now();renderDrawerChains();if(_lastChains)renderChains(_lastChains)}}).catch(function(){});
}

/* ═══ Events ═══ */
document.getElementById('wb-drawer').addEventListener('click',function(e){
  var ul=e.target.closest('[data-upload-logo]');if(ul){uploadLogo(ul.dataset.uploadLogo);return}
  var rm=e.target.closest('[data-rm-logo]');if(rm){removeLogo(rm.dataset.rmLogo);return}
  var sz=e.target.closest('[data-sz]');if(sz){setSize(sz.dataset.sz);saveConfig()}
});
document.getElementById('dr-chains').addEventListener('change',function(e){
  var sel=e.target.closest('[data-np-chain]');if(!sel)return;
  var cid=sel.dataset.npChain,rpuid=sel.value;
  if(!_cfg.chain_stations)_cfg.chain_stations={};
  if(rpuid)_cfg.chain_stations[cid]=rpuid;else delete _cfg.chain_stations[cid];
  saveConfig();npPoll();
});
document.getElementById('dr-streams').addEventListener('change',function(e){
  var cb=e.target.closest('[data-stream-key]');if(!cb)return;
  var k=cb.dataset.streamKey;var hs=_cfg.hidden_streams||[];
  if(cb.checked){hs=hs.filter(function(x){return x!==k})}else{if(hs.indexOf(k)<0)hs.push(k)}
  _cfg.hidden_streams=hs;saveConfig();if(_lastData)renderMeters(_lastData);
});
document.getElementById('cfg-lufs').addEventListener('change',function(){_cfg.show_lufs=this.checked;applyVis();saveConfig()});
document.getElementById('cfg-np').addEventListener('change',function(){_cfg.show_np=this.checked;applyVis();saveConfig()});
document.getElementById('cfg-sites').addEventListener('change',function(){_cfg.show_sites=this.checked;applyVis();saveConfig()});
document.getElementById('cfg-ticker').addEventListener('change',function(){_cfg.show_ticker=this.checked;applyVis();saveConfig()});
document.getElementById('cfg-hero').addEventListener('change',function(){_cfg.show_hero=this.checked;applyVis();saveConfig()});
document.getElementById('cfg-corp').addEventListener('change',function(){_cfg.corp_mode=this.checked;if(this.checked)_cfg.bauer_mode=false;applyConfig();saveConfig()});
document.getElementById('cfg-bauer').addEventListener('change',function(){_cfg.bauer_mode=this.checked;if(this.checked)_cfg.corp_mode=false;applyConfig();saveConfig()});
document.getElementById('cfg-hide-hdr').addEventListener('change',function(){_cfg.hide_hdr=this.checked;applyVis();saveConfig()});
document.getElementById('cfg-qr').addEventListener('change',function(){_cfg.show_qr=this.checked;applyVis();saveConfig();if(_lastChains)renderChains(_lastChains)});
document.getElementById('btn-brand-upload').addEventListener('click',function(){
  var inp=document.createElement('input');inp.type='file';inp.accept='image/*';
  inp.onchange=function(){if(!inp.files[0])return;var fd=new FormData();fd.append('logo',inp.files[0]);fd.append('_csrf_token',_csrf());
    fetch(_tkUrl('/api/wallboard/brand'),{method:'POST',credentials:'same-origin',headers:{'X-CSRFToken':_csrf()},body:fd})
    .then(function(r){return r.json()}).then(function(d){if(d.ok){
      var img=document.getElementById('dr-brand-preview');img.src=_tkUrl('/wallboard/brand?_='+Date.now());img.style.display='';
      var hImg=document.getElementById('wb-brand-img');hImg.src=_tkUrl('/wallboard/brand?_='+Date.now());hImg.style.display='';
      document.body.classList.add('has-brand');
    }}).catch(function(){})};inp.click();
});
document.getElementById('btn-brand-remove').addEventListener('click',function(){
  fetch(_tkUrl('/api/wallboard/brand'),{method:'DELETE',credentials:'same-origin',headers:{'X-CSRFToken':_csrf()}})
  .then(function(r){return r.json()}).then(function(d){if(d.ok){
    document.getElementById('dr-brand-preview').style.display='none';
    document.getElementById('wb-brand-img').style.display='none';
    document.body.classList.remove('has-brand');
  }}).catch(function(){});
});
document.getElementById('btn-sm').addEventListener('click',function(){setSize('sm');saveConfig()});
document.getElementById('btn-md').addEventListener('click',function(){setSize('md');saveConfig()});
document.getElementById('btn-lg').addEventListener('click',function(){setSize('lg');saveConfig()});
document.getElementById('btn-sort').addEventListener('click',toggleSort);
var _sbtn=document.getElementById('btn-sound');
if(_sbtn)_sbtn.addEventListener('click',function(){
  _cfg.sound_alert=!_cfg.sound_alert;
  this.classList.toggle('active',_cfg.sound_alert);
  if(_cfg.sound_alert){_playFaultTone()} // test tone on enable
  saveConfig();
});
document.getElementById('btn-cfg').addEventListener('click',openDrawer);
document.getElementById('btn-close-dr').addEventListener('click',closeDrawer);
document.getElementById('wb-overlay').addEventListener('click',closeDrawer);
document.getElementById('btn-fs').addEventListener('click',toggleFs);
document.addEventListener('keydown',function(e){var tag=(e.target.tagName||'').toLowerCase();if(tag==='input'||tag==='textarea')return;
  if(e.key==='f'||e.key==='F')toggleFs();if(e.key==='s'||e.key==='S')toggleSort();
  if(e.key==='g'||e.key==='G'){document.getElementById('wb-drawer').classList.contains('open')?closeDrawer():openDrawer()}
  if(e.key==='Escape')closeDrawer();if(e.key==='1')setSize('sm');if(e.key==='2')setSize('md');if(e.key==='3')setSize('lg')});

try{var lc=JSON.parse(localStorage.getItem('wb_cfg')||'{}');if(lc.card_size){_cfg=Object.assign(_cfg,lc,_urlOverrides);applyConfig();_cfgLoaded=true}}catch(e){}
})();
</script>
</body>
</html>"""

# ═══ Mobile play page template ═══════════════════════════════════════════
_PLAY_TPL = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<meta name="csrf-token" content="{{csrf_token()}}">
<title>{{chain_name}} — Listen Live</title>
<style nonce="{{csp_nonce()}}">
:root{--bg:#07142b;--acc:#17a8ff;--ok:#22c55e;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;overflow:hidden}
body{
  font-family:system-ui,-apple-system,sans-serif;
  background:radial-gradient(ellipse at 50% 30%,#14397a 0%,var(--bg) 50%,#040d1c 100%);
  color:var(--tx);display:flex;flex-direction:column;align-items:center;justify-content:center;
  padding:24px;gap:20px;
}
.logo{width:120px;height:120px;border-radius:28px;object-fit:contain;
  background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.08);
  box-shadow:0 8px 32px rgba(0,0,0,.4)}
.name{font-size:24px;font-weight:800;text-align:center;letter-spacing:-.02em}
.status{font-size:13px;color:var(--mu);text-align:center}
.play-btn{
  width:80px;height:80px;border-radius:50%;border:none;cursor:pointer;
  background:var(--acc);color:#fff;font-size:32px;
  display:flex;align-items:center;justify-content:center;
  box-shadow:0 4px 24px rgba(23,168,255,.3);
  transition:transform .15s,box-shadow .15s;
}
.play-btn:hover{transform:scale(1.08);box-shadow:0 6px 32px rgba(23,168,255,.4)}
.play-btn:active{transform:scale(.95)}
.play-btn.playing{background:var(--al)}
.eq{display:flex;align-items:flex-end;gap:3px;height:24px;margin-top:8px}
.eq-bar{width:4px;background:var(--acc);border-radius:2px;animation:eq 1s ease-in-out infinite}
.eq-bar:nth-child(1){animation-delay:0s;height:40%}
.eq-bar:nth-child(2){animation-delay:.15s;height:70%}
.eq-bar:nth-child(3){animation-delay:.3s;height:50%}
.eq-bar:nth-child(4){animation-delay:.1s;height:80%}
.eq-bar:nth-child(5){animation-delay:.25s;height:60%}
@keyframes eq{0%,100%{height:20%}50%{height:100%}}
.eq.hidden{visibility:hidden}
.powered{position:fixed;bottom:12px;font-size:10px;color:rgba(255,255,255,.2)}
</style>
</head>
<body>
{% if logo_url %}<img class="logo" src="{{logo_url}}" alt="">{% endif %}
<div class="name">{{chain_name}}</div>
<div class="status" id="status">Tap to listen</div>
<button class="play-btn" id="play-btn">&#9654;</button>
<div class="eq hidden" id="eq">
  <div class="eq-bar"></div><div class="eq-bar"></div><div class="eq-bar"></div>
  <div class="eq-bar"></div><div class="eq-bar"></div>
</div>
<div class="powered">{{build}}</div>
<script nonce="{{csp_nonce()}}">
(function(){
var chainId='{{chain_id}}';
var tk='{{tk}}';
var audio=null,playing=false;
var btn=document.getElementById('play-btn');
var status=document.getElementById('status');
var eq=document.getElementById('eq');

// Fetch chain status to get last node's live_url
function getStreamUrl(cb){
  fetch('/api/chains/status'+(tk||''),{credentials:'same-origin'})
    .then(function(r){return r.json()})
    .then(function(d){
      var chains=d.results||[];
      for(var i=0;i<chains.length;i++){
        if(chains[i].id!==chainId)continue;
        var nodes=[];
        (chains[i].nodes||[]).forEach(function(n){
          if(n.type==='stack')(n.nodes||[]).forEach(function(s){nodes.push(s)});
          else nodes.push(n);
        });
        if(nodes.length){
          var last=nodes[nodes.length-1];
          if(last.live_url){cb(last.live_url+(tk?'&':'?')+tk.slice(1));return}
        }
      }
      status.textContent='Stream not available';
    }).catch(function(){status.textContent='Connection error'});
}

btn.addEventListener('click',function(){
  if(playing){
    if(audio){audio.pause();audio.src=''}
    playing=false;btn.innerHTML='&#9654;';btn.classList.remove('playing');
    status.textContent='Tap to listen';eq.classList.add('hidden');return;
  }
  status.textContent='Connecting…';
  getStreamUrl(function(url){
    audio=new Audio(url);
    audio.addEventListener('playing',function(){
      playing=true;btn.innerHTML='&#9632;';btn.classList.add('playing');
      status.textContent='Playing live';eq.classList.remove('hidden');
    });
    audio.addEventListener('error',function(){
      status.textContent='Playback error — tap to retry';
      playing=false;btn.innerHTML='&#9654;';btn.classList.remove('playing');
      eq.classList.add('hidden');
    });
    audio.play().catch(function(){status.textContent='Tap again to play'});
  });
});
})();
</script>
</body>
</html>"""
