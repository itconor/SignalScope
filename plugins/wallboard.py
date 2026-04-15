# wallboard.py — Broadcast Wallboard for SignalScope
# Visually striking TV display with chain status, station logos, live meters.
# Drop into the plugins/ subdirectory.

import os, json, re, time as _time

SIGNALSCOPE_PLUGIN = {
    "id":       "wallboard",
    "label":    "Wallboard",
    "url":      "/hub/wallboard",
    "icon":     "📺",
    "hub_only": True,
    "version":  "2.0.0",
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


def register(app, ctx):
    login_required = ctx["login_required"]
    csrf_protect   = ctx["csrf_protect"]
    monitor        = ctx["monitor"]
    hub_server     = ctx.get("hub_server")
    BUILD          = ctx["BUILD"]

    from flask import jsonify, render_template_string, request, send_file

    # ── Page ─────────────────────────────────────────────────────────────
    @app.get("/hub/wallboard")
    @login_required
    def wallboard_page():
        return render_template_string(_TPL, build=BUILD)

    # ── Data endpoint ────────────────────────────────────────────────────
    @app.get("/api/wallboard/data")
    @login_required
    def wallboard_data():
        cfg = monitor.app_cfg
        now = _time.time()
        sites_out = []

        # Hub sites
        if hub_server and cfg.hub.mode in ("hub", "both"):
            try:
                raw = hub_server.get_sites() if callable(getattr(hub_server, "get_sites", None)) else []
            except Exception:
                raw = []
            for sd in (raw or []):
                if not isinstance(sd, dict) or not sd.get("_approved", True):
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

        # Local monitor
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

        # Chain logo flags
        chain_logos = {}
        for ch in (cfg.signal_chains or []):
            cid = ch.get("id", "")
            if cid:
                chain_logos[cid] = _has_logo(cid)

        # Recent alerts for ticker
        alerts_out = []
        for a in _load_alerts(20):
            atype = (a.get("type") or "").upper()
            alerts_out.append({
                "time":   a.get("time", 0),
                "site":   (a.get("site") or "").strip(),
                "stream": (a.get("stream") or "").strip(),
                "type":   atype,
                "msg":    (a.get("msg") or a.get("message") or atype).strip(),
                "ok":     atype in ("RECOVERY", "AUDIO_RESTORED", "CHAIN_OK",
                                    "CHAIN_RECOVERED"),
            })

        return jsonify({
            "sites": sites_out,
            "chain_logos": chain_logos,
            "alerts": alerts_out,
            "config": _cfg_load(),
        })

    # ── Config ───────────────────────────────────────────────────────────
    @app.get("/api/wallboard/config")
    @login_required
    def wallboard_config_get():
        return jsonify(_cfg_load())

    @app.post("/api/wallboard/config")
    @login_required
    @csrf_protect
    def wallboard_config_save():
        _cfg_save(request.get_json(force=True))
        return jsonify({"ok": True})

    # ── Logo upload / serve / delete ─────────────────────────────────────
    @app.post("/api/wallboard/logo/<chain_id>")
    @login_required
    @csrf_protect
    def wallboard_logo_upload(chain_id):
        if not re.match(r'^[a-zA-Z0-9_-]+$', chain_id):
            return jsonify({"error": "Invalid ID"}), 400
        f = request.files.get("logo")
        if not f or not f.filename:
            return jsonify({"error": "No file"}), 400
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
            return jsonify({"error": "Unsupported format"}), 400
        f.seek(0, 2)
        if f.tell() > 2 * 1024 * 1024:
            return jsonify({"error": "File too large (max 2 MB)"}), 400
        f.seek(0)
        os.makedirs(_LOGO_DIR, exist_ok=True)
        for old in os.listdir(_LOGO_DIR):
            if old.split(".")[0] == chain_id:
                os.remove(os.path.join(_LOGO_DIR, old))
        f.save(os.path.join(_LOGO_DIR, chain_id + ext))
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
    @csrf_protect
    def wallboard_logo_delete(chain_id):
        if not re.match(r'^[a-zA-Z0-9_-]+$', chain_id):
            return jsonify({"error": "Invalid ID"}), 400
        for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
            path = os.path.join(_LOGO_DIR, chain_id + ext)
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
  --bg:#030b18;--sur:rgba(10,28,58,.82);--sur2:#0f2847;
  --bor:#1a3a6a;--bor2:rgba(0,184,255,.12);
  --acc:#00b8ff;--ok:#00e676;--wn:#ffc107;--al:#ff1744;
  --tx:#f0f6ff;--mu:#6b8fb5;
  --mc-w:160px;--radius:14px;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;overflow:hidden}
body{
  font-family:'Inter',system-ui,-apple-system,sans-serif;
  background:var(--bg);color:var(--tx);font-size:13px;
  display:flex;flex-direction:column;
  -webkit-user-select:none;user-select:none;
}
body::before{
  content:'';position:fixed;inset:0;pointer-events:none;z-index:0;
  background:radial-gradient(ellipse at 50% 0%,rgba(10,30,70,.4) 0%,transparent 60%);
}
#wb-hdr,#wb-content,#wb-ticker,#wb-drawer,#wb-overlay{position:relative;z-index:1}

/* ═══ Header ═══ */
#wb-hdr{
  flex-shrink:0;padding:7px 16px;
  background:linear-gradient(180deg,rgba(10,28,58,.97),rgba(6,16,36,.97));
  border-bottom:1px solid var(--bor);
  display:flex;align-items:center;gap:12px;flex-wrap:wrap;
  transition:opacity .45s,transform .45s;
}
#wb-hdr.hide{opacity:0;transform:translateY(-100%);pointer-events:none}
.wb-title{font-size:16px;font-weight:800;letter-spacing:.02em;display:flex;align-items:center;gap:6px}
.wb-meta{font-size:11px;color:var(--mu)}
#wb-alert-badge{
  background:var(--al);color:#fff;border-radius:999px;
  padding:2px 10px;font-size:11px;font-weight:700;display:none;
  animation:badge-pulse 1.4s ease-in-out infinite;
}
#wb-alert-badge.show{display:inline-block}
@keyframes badge-pulse{0%,100%{opacity:1}50%{opacity:.6}}
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
  font-size:15px;font-weight:700;font-variant-numeric:tabular-nums;
  color:var(--tx);min-width:68px;text-align:right;
}

/* ═══ Content ═══ */
#wb-content{flex:1;display:flex;flex-direction:column;overflow:hidden}

/* ═══ Chain strip ═══ */
#wb-chains{
  flex-shrink:0;padding:12px 16px 8px;
  display:flex;gap:12px;overflow-x:auto;overflow-y:hidden;
  scrollbar-width:none;
}
#wb-chains::-webkit-scrollbar{display:none}
#wb-chains:empty{padding:0}

.ch-card{
  min-width:175px;max-width:240px;flex-shrink:0;
  background:var(--sur);border:1px solid var(--bor2);
  border-radius:var(--radius);padding:14px 16px 12px;
  display:flex;flex-direction:column;align-items:center;gap:8px;
  position:relative;overflow:hidden;
  transition:border-color .4s,box-shadow .4s;
}
.ch-card::before{
  content:'';position:absolute;top:0;left:0;right:0;height:1px;
  background:linear-gradient(90deg,transparent 10%,rgba(0,184,255,.25) 50%,transparent 90%);
}
.ch-card.ch-ok{box-shadow:0 2px 16px rgba(0,0,0,.3),inset 0 1px 0 rgba(0,230,118,.06)}
.ch-card.ch-fault{
  border-color:rgba(255,23,68,.45);
  box-shadow:0 0 28px rgba(255,23,68,.12),0 2px 16px rgba(0,0,0,.3);
  animation:ch-glow 2s ease-in-out infinite;
}
@keyframes ch-glow{
  0%,100%{box-shadow:0 0 28px rgba(255,23,68,.12),0 2px 16px rgba(0,0,0,.3)}
  50%{box-shadow:0 0 44px rgba(255,23,68,.2),0 2px 16px rgba(0,0,0,.3)}
}
.ch-card.ch-unknown{border-color:var(--bor);opacity:.65}

.ch-logo{width:52px;height:52px;border-radius:10px;object-fit:contain;background:rgba(255,255,255,.04)}
.ch-logo-ph{
  width:52px;height:52px;border-radius:10px;
  background:linear-gradient(135deg,var(--sur2),rgba(6,16,36,.6));
  border:1px dashed rgba(107,143,181,.25);
  display:flex;align-items:center;justify-content:center;font-size:22px;opacity:.5;
}
.ch-name{
  font-size:13px;font-weight:700;text-align:center;line-height:1.25;
  max-width:100%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.ch-badge{
  display:inline-flex;align-items:center;gap:5px;
  padding:3px 10px;border-radius:20px;
  font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;
}
.ch-badge.b-ok{background:rgba(0,230,118,.1);color:var(--ok);border:1px solid rgba(0,230,118,.2)}
.ch-badge.b-fault{
  background:rgba(255,23,68,.1);color:var(--al);border:1px solid rgba(255,23,68,.3);
  animation:badge-blink 1.5s ease-in-out infinite;
}
.ch-badge.b-unk{background:rgba(107,143,181,.1);color:var(--mu);border:1px solid rgba(107,143,181,.2)}
@keyframes badge-blink{0%,100%{opacity:1}50%{opacity:.65}}
.ch-bdot{width:7px;height:7px;border-radius:50%;background:currentColor;box-shadow:0 0 5px currentColor}

.ch-nodes{display:flex;align-items:center;gap:3px;flex-wrap:wrap;justify-content:center}
.ch-nd{width:7px;height:7px;border-radius:50%;transition:background .3s,box-shadow .3s}
.ch-nd.n-ok{background:var(--ok);box-shadow:0 0 4px rgba(0,230,118,.5)}
.ch-nd.n-down,.ch-nd.n-fault{background:var(--al);box-shadow:0 0 6px rgba(255,23,68,.6);animation:nd-p 1s ease infinite}
.ch-nd.n-offline{background:var(--wn);box-shadow:0 0 4px rgba(255,193,7,.4)}
.ch-nd.n-unknown,.ch-nd.n-maintenance{background:var(--mu)}
@keyframes nd-p{0%,100%{transform:scale(1)}50%{transform:scale(1.4);opacity:.6}}
.ch-arrow{color:var(--mu);font-size:7px;opacity:.35;line-height:1}

/* ═══ Meter scroll ═══ */
#wb-scroll{flex:1;overflow-y:auto;overflow-x:hidden;padding:4px 16px 16px}

.wb-site{margin-bottom:14px}
.wb-site-hdr{
  display:flex;align-items:center;gap:7px;
  font-size:10px;font-weight:700;color:var(--mu);
  text-transform:uppercase;letter-spacing:.09em;
  margin-bottom:8px;padding-bottom:4px;border-bottom:1px solid rgba(26,58,106,.5);
}
.wb-sdot{width:7px;height:7px;border-radius:50%;background:var(--ok);flex-shrink:0}
.wb-sdot.off{background:var(--al)}

.wb-grid{
  display:grid;gap:8px;
  grid-template-columns:repeat(auto-fill,minmax(var(--mc-w),1fr));
}

/* ═══ Meter card ═══ */
.mc{
  background:var(--sur);border:1px solid var(--bor);
  border-radius:12px;overflow:hidden;
  display:flex;flex-direction:column;min-width:0;
  transition:border-color .3s,box-shadow .3s;
}
.mc.mc-alert{border-color:var(--al);box-shadow:0 0 18px rgba(255,23,68,.1);animation:mc-gl 1.5s ease-in-out infinite}
@keyframes mc-gl{0%,100%{box-shadow:0 0 18px rgba(255,23,68,.1)}50%{box-shadow:0 0 28px rgba(255,23,68,.18)}}
.mc.mc-warn{border-color:var(--wn);box-shadow:0 0 12px rgba(255,193,7,.08)}
.mc.mc-silent{border-color:rgba(255,23,68,.3)}
.mc.mc-ok{border-color:rgba(0,230,118,.15);box-shadow:inset 0 1px 0 rgba(0,230,118,.06)}

.mc-head{padding:7px 10px 5px;border-bottom:1px solid rgba(255,255,255,.04)}
.mc-name{font-size:11.5px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.3}
.mc-sub{font-size:9px;color:var(--mu);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px}

.mc-body{flex:1;display:flex;flex-direction:column;align-items:center;padding:10px 10px 6px;gap:6px}

.mtr-wrap{
  position:relative;width:100%;max-width:48px;
  flex:1;min-height:120px;
  background:rgba(0,0,0,.3);border-radius:5px;overflow:hidden;
  border:1px solid rgba(255,255,255,.03);
}
.mtr-wrap::before,.mtr-wrap::after{
  content:'';position:absolute;left:0;right:0;height:1px;
  background:rgba(255,255,255,.05);z-index:2;pointer-events:none;
}
.mtr-wrap::before{top:25%}
.mtr-wrap::after{top:7.5%}
.mtr-fill{
  position:absolute;bottom:0;left:0;right:0;
  background:linear-gradient(to top,#00e676 0%,#00c853 65%,#ffc107 78%,#ff9800 88%,#ff1744 96%);
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
  background:rgba(0,0,0,.3);border-radius:4px;overflow:hidden;
  border:1px solid rgba(255,255,255,.03);
}
.mtr-ch-label{font-size:9px;font-weight:700;color:var(--mu);line-height:1}
.mc:not([data-stereo="1"]) .mtr-stereo{display:none}
.mc[data-stereo="1"] .mtr-mono{display:none}

.mc-lev{
  font-size:14px;font-weight:700;font-variant-numeric:tabular-nums;
  letter-spacing:-.02em;min-width:76px;text-align:center;
}
.mc-lev.lc-low{color:var(--mu)}
.mc-lev.lc-warn{color:var(--wn)}
.mc-lev.lc-alert{color:var(--al)}

.mc-lufs{font-size:10px;color:var(--mu);text-align:center}
.mc-np{
  font-size:10px;color:var(--acc);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  max-width:100%;padding:0 8px;text-align:center;
}
.mc-foot{display:flex;align-items:center;justify-content:space-between;padding:4px 9px 6px;gap:4px}
.sp{
  display:inline-flex;align-items:center;gap:3px;
  padding:2px 7px;border-radius:999px;font-size:10px;font-weight:700;line-height:1.4;
}
.sp-ok{background:rgba(0,230,118,.12);color:var(--ok)}
.sp-al{background:rgba(255,23,68,.14);color:var(--al)}
.sp-wn{background:rgba(255,193,7,.12);color:var(--wn)}
.sp-si{background:rgba(107,143,181,.1);color:var(--mu)}
.mc-rtp{font-size:10px;color:var(--wn)}

.wb-empty{
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  height:250px;gap:12px;color:var(--mu);
}
.wb-empty-ico{font-size:56px;opacity:.2}

/* ═══ Alert ticker ═══ */
#wb-ticker{
  flex-shrink:0;background:rgba(4,12,26,.97);
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
.tk-al{color:var(--al)}
.tk-ok{color:var(--ok)}

/* ═══ Settings drawer ═══ */
#wb-overlay{position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:190;opacity:0;pointer-events:none;transition:opacity .3s}
#wb-overlay.show{opacity:1;pointer-events:auto}
#wb-drawer{
  position:fixed;top:0;right:0;bottom:0;width:340px;
  background:linear-gradient(180deg,rgba(10,28,58,.97),rgba(4,12,26,.98));
  backdrop-filter:blur(16px);
  border-left:1px solid var(--bor);z-index:200;
  transform:translateX(100%);transition:transform .35s cubic-bezier(.4,0,.2,1);
  display:flex;flex-direction:column;box-shadow:-6px 0 28px rgba(0,0,0,.4);
}
#wb-drawer.open{transform:translateX(0)}
.dr-hdr{padding:14px 16px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--bor)}
.dr-title{font-size:14px;font-weight:700}
.dr-body{flex:1;overflow-y:auto;padding:12px 16px}
.dr-section{margin-bottom:18px}
.dr-stitle{font-size:10px;font-weight:700;color:var(--mu);text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px}
.dr-row{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px}
.dr-toggle{display:flex;align-items:center;gap:7px;font-size:12px;cursor:pointer;margin-bottom:6px}
.dr-toggle input{accent-color:var(--acc);width:15px;height:15px}

.dr-chain{display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid rgba(26,58,106,.3)}
.dr-chain:last-child{border-bottom:none}
.dr-ch-logo{width:36px;height:36px;border-radius:8px;object-fit:contain;background:rgba(255,255,255,.04);flex-shrink:0}
.dr-ch-ph{
  width:36px;height:36px;border-radius:8px;flex-shrink:0;
  background:rgba(255,255,255,.04);border:1px dashed rgba(107,143,181,.2);
  display:flex;align-items:center;justify-content:center;font-size:14px;opacity:.4;
}
.dr-ch-info{flex:1;min-width:0}
.dr-ch-name{font-size:12px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dr-ch-actions{display:flex;gap:5px;margin-top:3px}

:fullscreen #wb-scroll,:-webkit-full-screen #wb-scroll{padding:6px 16px 16px}
</style>
</head>
<body>

<header id="wb-hdr">
  <span class="wb-title">📺 Wallboard</span>
  <span class="wb-meta" id="wb-meta">Loading…</span>
  <span id="wb-alert-badge">⚠ ALERTS</span>
  <div class="wb-ctrl">
    <span style="font-size:11px;color:var(--mu)">Size</span>
    <button class="btn bs" id="btn-sm" title="Compact (1)">S</button>
    <button class="btn bs active" id="btn-md" title="Normal (2)">M</button>
    <button class="btn bs" id="btn-lg" title="Large (3)">L</button>
    <button class="btn bs" id="btn-sort" title="Sort by level (S)">↕ Level</button>
    <button class="btn bs" id="btn-cfg" title="Settings (G)">⚙ Settings</button>
    <a class="btn bs" href="/">⌂</a>
    <button class="btn bp bs" id="btn-fs" title="Fullscreen (F)">⛶ Full</button>
    <span id="wb-clock">--:--:--</span>
  </div>
</header>

<div id="wb-content">
  <div id="wb-chains"></div>
  <div id="wb-scroll"><div id="wb-meters"></div></div>
</div>

<div id="wb-ticker">
  <div id="wb-ticker-label">ALERTS</div>
  <div id="wb-ticker-scroll">
    <div id="wb-ticker-inner"><span class="tk-item">No recent alerts</span></div>
  </div>
</div>

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
      <div class="dr-stitle">Display</div>
      <label class="dr-toggle"><input type="checkbox" id="cfg-lufs" checked> Show LUFS-I</label>
      <label class="dr-toggle"><input type="checkbox" id="cfg-np" checked> Show Now Playing</label>
      <label class="dr-toggle"><input type="checkbox" id="cfg-sites" checked> Show Site Headers</label>
      <label class="dr-toggle"><input type="checkbox" id="cfg-ticker" checked> Show Alert Ticker</label>
    </div>
    <div class="dr-section">
      <div class="dr-stitle">Chain Logos</div>
      <div id="dr-chains"><span style="color:var(--mu);font-size:12px">Loading chains…</span></div>
    </div>
  </div>
</aside>

<script nonce="{{csp_nonce()}}">
(function(){
'use strict';
function _esc(s){return(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function _getCsrf(){return(document.querySelector('meta[name="csrf-token"]')||{}).content||(document.cookie.match(/(?:^;\s*)csrf_token=([^;]+)/)||[])[1]||''}

var POLL_MS=1500,LIVE_MS=150,CHAIN_MS=2000;
var PEAK_HOLD=2500,PEAK_RATE=.45,DB_FLOOR=-80,ATTACK_RATE=600,DECAY_RATE=30;
var _sizes={sm:120,md:160,lg:220};

var _cfg={card_size:'md',show_lufs:true,show_np:true,show_sites:true,show_ticker:true,sort_level:false,hidden_chains:[]};
var _peaks={},_sortLev=false,_lastData=null,_lastChains=null,_chainLogos={};
var _liveActive=false,_targetLev={},_dispLev={},_rafTs=null,_cfgLoaded=false;

/* ── Config ── */
function applyConfig(){
  setSize(_cfg.card_size||'md');
  _sortLev=!!_cfg.sort_level;
  document.getElementById('btn-sort').classList.toggle('active',_sortLev);
  document.getElementById('cfg-lufs').checked=_cfg.show_lufs!==false;
  document.getElementById('cfg-np').checked=_cfg.show_np!==false;
  document.getElementById('cfg-sites').checked=_cfg.show_sites!==false;
  document.getElementById('cfg-ticker').checked=_cfg.show_ticker!==false;
  document.querySelectorAll('[data-sz]').forEach(function(b){b.classList.toggle('active',b.dataset.sz===_cfg.card_size)});
  applyVis();
}
function applyVis(){
  document.querySelectorAll('.mc-lufs').forEach(function(e){e.style.display=(_cfg.show_lufs!==false)?'':'none'});
  document.querySelectorAll('.mc-np').forEach(function(e){e.style.display=(_cfg.show_np!==false)?'':'none'});
  document.querySelectorAll('.wb-site-hdr').forEach(function(e){e.style.display=(_cfg.show_sites!==false)?'':'none'});
  document.getElementById('wb-ticker').style.display=(_cfg.show_ticker!==false)?'':'none';
}
function saveConfig(){
  _cfg.sort_level=_sortLev;
  fetch('/api/wallboard/config',{method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRFToken':_getCsrf()},
    body:JSON.stringify(_cfg)}).catch(function(){});
  try{localStorage.setItem('wb_cfg',JSON.stringify(_cfg))}catch(e){}
}

/* ── Clock ── */
function _tick(){var d=new Date(),h=d.getHours(),m=d.getMinutes(),s=d.getSeconds();
  document.getElementById('wb-clock').textContent=(h<10?'0':'')+h+':'+(m<10?'0':'')+m+':'+(s<10?'0':'')+s}
setInterval(_tick,1000);_tick();

/* ── Size ── */
function setSize(sz){
  _cfg.card_size=sz;
  document.documentElement.style.setProperty('--mc-w',_sizes[sz]+'px');
  ['sm','md','lg'].forEach(function(s){document.getElementById('btn-'+s).classList.toggle('active',s===sz)});
  document.querySelectorAll('[data-sz]').forEach(function(b){b.classList.toggle('active',b.dataset.sz===sz)});
}

function toggleSort(){_sortLev=!_sortLev;document.getElementById('btn-sort').classList.toggle('active',_sortLev);if(_lastData)renderMeters(_lastData);saveConfig()}

/* ── Fullscreen ── */
function toggleFs(){var r=document.documentElement;if(!document.fullscreenElement)(r.requestFullscreen||r.webkitRequestFullscreen||function(){}).call(r);else(document.exitFullscreen||document.webkitExitFullscreen||function(){}).call(document)}
var _hideT=null;
function _resetHide(){clearTimeout(_hideT);document.getElementById('wb-hdr').classList.remove('hide');_hideT=setTimeout(function(){document.getElementById('wb-hdr').classList.add('hide')},4000)}
document.addEventListener('fullscreenchange',function(){var inFs=!!document.fullscreenElement;document.getElementById('btn-fs').textContent=inFs?'✕ Exit':'⛶ Full';if(inFs){_resetHide();document.addEventListener('mousemove',_resetHide)}else{clearTimeout(_hideT);document.getElementById('wb-hdr').classList.remove('hide');document.removeEventListener('mousemove',_resetHide)}});

/* ── Drawer ── */
function openDrawer(){document.getElementById('wb-drawer').classList.add('open');document.getElementById('wb-overlay').classList.add('show');renderDrawerChains()}
function closeDrawer(){document.getElementById('wb-drawer').classList.remove('open');document.getElementById('wb-overlay').classList.remove('show')}

/* ── Helpers ── */
function levToH(db){return Math.max(0,Math.min(100,(db-DB_FLOOR)/(-DB_FLOOR)*100))}
function fmtLev(db){if(db<=DB_FLOOR)return'— dB';return(db>=0?'+':'')+db.toFixed(1)+' dB'}
function levCls(db){if(db>=-9)return'lc-alert';if(db>=-18)return'lc-warn';if(db<=-60)return'lc-low';return''}
function _updPeak(key,lev,now){var pk=_peaks[key]||{val:DB_FLOOR,ts:0};if(lev>=pk.val){_peaks[key]={val:lev,ts:now}}else{var el=now-pk.ts;if(el>PEAK_HOLD){_peaks[key]={val:Math.max(DB_FLOOR,pk.val-PEAK_RATE*(el-PEAK_HOLD)/100),ts:pk.ts}}else{_peaks[key]=pk}}return _peaks[key].val}
function fmtTime(ts){var d=new Date(ts*1000);return('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2)}

/* ═══ Chains ═══ */
function renderChains(chains){
  var el=document.getElementById('wb-chains');
  if(!chains||chains.length===0){el.innerHTML='';return}
  var hidden=_cfg.hidden_chains||[];
  var visible=chains.filter(function(c){return hidden.indexOf(c.id)<0});
  if(visible.length===0){el.innerHTML='';return}
  var html='';
  visible.forEach(function(ch){
    var st=ch.display_status||ch.status||'unknown';
    var cls='ch-card ch-'+(st==='ok'?'ok':st==='fault'?'fault':'unknown');
    var hasLogo=_chainLogos[ch.id];
    var logo=hasLogo
      ?'<img class="ch-logo" src="/wallboard/logo/'+_esc(ch.id)+'?_='+(_chainLogos._ts||0)+'" alt="" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\'">'
       +'<div class="ch-logo-ph" style="display:none">📡</div>'
      :'<div class="ch-logo-ph">📡</div>';
    var bCls=st==='ok'?'b-ok':st==='fault'?'b-fault':'b-unk';
    var bTxt=st==='ok'?'ON AIR':st==='fault'?'FAULT':'—';
    var nodes='';
    _flatN(ch.nodes||[]).forEach(function(n,i){
      if(i>0)nodes+='<span class="ch-arrow">▸</span>';
      nodes+='<span class="ch-nd n-'+(n.status||'unknown')+'" title="'+_esc(n.label||n.stream||n.name||'?')+'"></span>';
    });
    html+='<div class="'+cls+'" data-chain-id="'+_esc(ch.id)+'">'+logo
      +'<div class="ch-name" title="'+_esc(ch.name)+'">'+_esc(ch.name)+'</div>'
      +'<div class="ch-badge '+bCls+'"><span class="ch-bdot"></span>'+bTxt+'</div>'
      +'<div class="ch-nodes">'+nodes+'</div></div>';
  });
  el.innerHTML=html;
}
function _flatN(nodes){var out=[];(nodes||[]).forEach(function(n){if(n.type==='stack')(n.nodes||[]).forEach(function(s){out.push(s)});else out.push(n)});return out}

/* ═══ Meters ═══ */
function buildCard(key,st,site){
  var el=document.createElement('div');el.className='mc';el.dataset.key=key;
  el.innerHTML=
    '<div class="mc-head"><div class="mc-name" title="'+_esc(st.name)+'">'+_esc(st.name)+'</div>'
    +'<div class="mc-sub">'+_esc(site)+'</div></div>'
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
  if(!_liveActive){var key=el.dataset.key,now=Date.now();if(key){_targetLev[key]=lev;_updPeak(key,lev,now);
    if(st.stereo&&st.level_dbfs_l!=null){_targetLev[key+'|L']=st.level_dbfs_l;_updPeak(key+'|L',st.level_dbfs_l,now)}
    if(st.stereo&&st.level_dbfs_r!=null){_targetLev[key+'|R']=st.level_dbfs_r;_updPeak(key+'|R',st.level_dbfs_r,now)}}}
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
  var flat=[];(data.sites||[]).forEach(function(site){(site.streams||[]).forEach(function(st){flat.push({st:st,siteName:site.site,online:site.online})})});
  if(_sortLev)flat.sort(function(a,b){return b.st.level_dbfs-a.st.level_dbfs});
  var existing={};root.querySelectorAll('.mc').forEach(function(el){existing[el.dataset.key]=el});var seen={};
  if(_sortLev){
    var fs=root.querySelector('.wb-site.wb-flat');if(!fs){root.innerHTML='';fs=_mkSec('wb-site wb-flat',null);root.appendChild(fs)}
    var mg=fs.querySelector('.wb-grid');
    flat.forEach(function(item){var key=item.siteName+'|'+item.st.name;seen[key]=true;_updPeak(key,item.st.level_dbfs,now);
      var card=existing[key];if(!card)card=buildCard(key,item.st,item.siteName);if(card.parentElement!==mg)mg.appendChild(card);updateCard(card,item.st)});
  }else{
    root.querySelectorAll('.wb-flat').forEach(function(el){el.remove()});
    var siteOrder=(data.sites||[]).map(function(s){return s.site});
    root.querySelectorAll('.wb-site[data-site]').forEach(function(el){if(siteOrder.indexOf(el.dataset.site)===-1)el.remove()});
    var siteMap={};(data.sites||[]).forEach(function(s){siteMap[s.site]=s});
    siteOrder.forEach(function(siteName){var site=siteMap[siteName];var sid='wbs-'+siteName.replace(/[^a-z0-9]/gi,'_');
      var sec=document.getElementById(sid);if(!sec){sec=_mkSec('wb-site',siteName);sec.id=sid;root.appendChild(sec)}
      var dot=sec.querySelector('.wb-sdot');if(dot)dot.className='wb-sdot'+(site.online?'':' off');
      var hdr=sec.querySelector('.wb-site-hdr');if(hdr)hdr.style.display=(_cfg.show_sites!==false)?'':'none';
      var mg=sec.querySelector('.wb-grid');
      (site.streams||[]).forEach(function(st){var key=siteName+'|'+st.name;seen[key]=true;_updPeak(key,st.level_dbfs,now);
        var card=existing[key];if(!card)card=buildCard(key,st,siteName);if(card.parentElement!==mg)mg.appendChild(card);updateCard(card,st)})});
  }
  Object.keys(existing).forEach(function(k){if(!seen[k])existing[k].remove()});
  var total=flat.length,alerts=flat.filter(function(i){return(i.st.ai_status||'').indexOf('[ALERT]')>=0}).length;
  var silences=flat.filter(function(i){return i.st.silence_active}).length;
  document.getElementById('wb-meta').textContent=total+' stream'+(total!==1?'s':'')+' · '+(data.sites||[]).length+' site'+((data.sites||[]).length!==1?'s':'')+(silences?' · '+silences+' silent':'');
  var badge=document.getElementById('wb-alert-badge');badge.textContent='⚠ '+alerts+' ALERT'+(alerts!==1?'S':'');badge.classList.toggle('show',alerts>0);
  applyVis();
  if(total===0)root.innerHTML='<div class="wb-empty"><div class="wb-empty-ico">📡</div><div style="font-size:15px;font-weight:700">No streams found</div><div style="font-size:12px">Connect a site or enable local monitoring.</div></div>';
}

function _mkSec(cls,siteName){var sec=document.createElement('div');sec.className=cls;
  if(siteName!==null){sec.dataset.site=siteName;sec.innerHTML='<div class="wb-site-hdr"><span class="wb-sdot"></span>'+_esc(siteName)+'</div><div class="wb-grid"></div>'}
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
  fetch('/api/wallboard/data',{credentials:'same-origin'}).then(function(r){return r.json()}).then(function(d){
    if(d.config&&!_cfgLoaded){_cfg=Object.assign(_cfg,d.config);applyConfig();_cfgLoaded=true}
    _chainLogos=d.chain_logos||{};_chainLogos._ts=Date.now();
    renderMeters(d);buildTicker(d.alerts||[]);
  }).catch(function(){});
}
function chainPoll(){
  fetch('/api/chains/status',{credentials:'same-origin'}).then(function(r){return r.ok?r.json():Promise.reject()}).then(function(d){
    _lastChains=d.chains||d||[];renderChains(_lastChains);
  }).catch(function(){});
}
function livePoll(){
  fetch('/api/hub/live_levels',{credentials:'same-origin'}).then(function(r){return r.ok?r.json():Promise.reject()}).then(function(data){
    _liveActive=true;var now=Date.now();
    Object.keys(data).forEach(function(siteName){(data[siteName]||[]).forEach(function(s){
      var key=siteName+'|'+s.name,lev=(s.level_dbfs==null)?DB_FLOOR:s.level_dbfs;
      _targetLev[key]=lev;_updPeak(key,lev,now);
      if(s.level_dbfs_l!=null&&s.level_dbfs_r!=null){
        _targetLev[key+'|L']=s.level_dbfs_l;_updPeak(key+'|L',s.level_dbfs_l,now);
        _targetLev[key+'|R']=s.level_dbfs_r;_updPeak(key+'|R',s.level_dbfs_r,now);
        var esc=key.replace(/\\/g,'\\\\').replace(/"/g,'\\"');
        var card=document.querySelector('.mc[data-key="'+esc+'"]');if(card)card.dataset.stereo='1'}
    })});
  }).catch(function(){});
}
poll();chainPoll();livePoll();
setInterval(poll,POLL_MS);setInterval(chainPoll,CHAIN_MS);setInterval(livePoll,LIVE_MS);

/* ═══ Ticker ═══ */
function buildTicker(alerts){
  var el=document.getElementById('wb-ticker-inner');
  if(!alerts||!alerts.length){el.innerHTML='<span class="tk-item">No recent alerts</span>';return}
  var items=alerts.slice(0,15).map(function(a){
    var cls=a.ok?'tk-ok':'tk-al';var label=(a.site||'')+(a.stream?' · '+a.stream:'');
    return '<span class="tk-item"><span class="tk-time">'+fmtTime(a.time)+'</span>'
      +'<span class="tk-site">'+_esc(label)+'</span><span class="tk-sep">—</span>'
      +'<span class="'+cls+'">'+_esc(a.msg||a.type)+'</span></span>'});
  el.innerHTML=items.concat(items).join('');
  el.style.animation='none';el.offsetWidth;
  var dur=Math.max(20,el.scrollWidth/2/60);
  el.style.animation='tk-scroll '+dur+'s linear infinite';
}

/* ═══ Drawer chains ═══ */
function renderDrawerChains(){
  var el=document.getElementById('dr-chains');
  if(!_lastChains||_lastChains.length===0){el.innerHTML='<span style="color:var(--mu);font-size:12px">No chains configured</span>';return}
  var html='';_lastChains.forEach(function(ch){
    var hasLogo=_chainLogos[ch.id];
    var logo=hasLogo?'<img class="dr-ch-logo" src="/wallboard/logo/'+_esc(ch.id)+'?_='+Date.now()+'" alt="">':'<div class="dr-ch-ph">📡</div>';
    html+='<div class="dr-chain">'+logo+'<div class="dr-ch-info"><div class="dr-ch-name">'+_esc(ch.name)+'</div>'
      +'<div class="dr-ch-actions"><button class="btn bp bs" data-upload-logo="'+_esc(ch.id)+'">Upload Logo</button>'
      +(hasLogo?'<button class="btn bd bs" data-rm-logo="'+_esc(ch.id)+'">Remove</button>':'')
      +'</div></div></div>'});
  el.innerHTML=html;
}
function uploadLogo(cid){
  var inp=document.createElement('input');inp.type='file';inp.accept='image/*';
  inp.onchange=function(){if(!inp.files[0])return;var fd=new FormData();fd.append('logo',inp.files[0]);
    fetch('/api/wallboard/logo/'+encodeURIComponent(cid),{method:'POST',credentials:'same-origin',headers:{'X-CSRFToken':_getCsrf()},body:fd})
    .then(function(r){return r.json()}).then(function(d){if(d.ok){_chainLogos[cid]=true;_chainLogos._ts=Date.now();renderDrawerChains();if(_lastChains)renderChains(_lastChains)}}).catch(function(){})};
  inp.click();
}
function removeLogo(cid){
  fetch('/api/wallboard/logo/'+encodeURIComponent(cid),{method:'DELETE',credentials:'same-origin',headers:{'X-CSRFToken':_getCsrf()}})
  .then(function(r){return r.json()}).then(function(d){if(d.ok){delete _chainLogos[cid];_chainLogos._ts=Date.now();renderDrawerChains();if(_lastChains)renderChains(_lastChains)}}).catch(function(){});
}

/* ═══ Events ═══ */
document.getElementById('wb-drawer').addEventListener('click',function(e){
  var ul=e.target.closest('[data-upload-logo]');if(ul){uploadLogo(ul.dataset.uploadLogo);return}
  var rm=e.target.closest('[data-rm-logo]');if(rm){removeLogo(rm.dataset.rmLogo);return}
  var sz=e.target.closest('[data-sz]');if(sz){setSize(sz.dataset.sz);saveConfig()}
});
document.getElementById('cfg-lufs').addEventListener('change',function(){_cfg.show_lufs=this.checked;applyVis();saveConfig()});
document.getElementById('cfg-np').addEventListener('change',function(){_cfg.show_np=this.checked;applyVis();saveConfig()});
document.getElementById('cfg-sites').addEventListener('change',function(){_cfg.show_sites=this.checked;applyVis();saveConfig()});
document.getElementById('cfg-ticker').addEventListener('change',function(){_cfg.show_ticker=this.checked;applyVis();saveConfig()});
document.getElementById('btn-sm').addEventListener('click',function(){setSize('sm');saveConfig()});
document.getElementById('btn-md').addEventListener('click',function(){setSize('md');saveConfig()});
document.getElementById('btn-lg').addEventListener('click',function(){setSize('lg');saveConfig()});
document.getElementById('btn-sort').addEventListener('click',toggleSort);
document.getElementById('btn-cfg').addEventListener('click',openDrawer);
document.getElementById('btn-close-dr').addEventListener('click',closeDrawer);
document.getElementById('wb-overlay').addEventListener('click',closeDrawer);
document.getElementById('btn-fs').addEventListener('click',toggleFs);
document.addEventListener('keydown',function(e){var tag=(e.target.tagName||'').toLowerCase();if(tag==='input'||tag==='textarea')return;
  if(e.key==='f'||e.key==='F')toggleFs();if(e.key==='s'||e.key==='S')toggleSort();
  if(e.key==='g'||e.key==='G'){document.getElementById('wb-drawer').classList.contains('open')?closeDrawer():openDrawer()}
  if(e.key==='Escape')closeDrawer();if(e.key==='1')setSize('sm');if(e.key==='2')setSize('md');if(e.key==='3')setSize('lg')});

/* ── Init ── */
try{var lc=JSON.parse(localStorage.getItem('wb_cfg')||'{}');if(lc.card_size){_cfg=Object.assign(_cfg,lc);applyConfig();_cfgLoaded=true}}catch(e){}
})();
</script>
</body>
</html>"""
