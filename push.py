# push.py — SignalScope Push Server Plugin
#
# Install this plugin on the hub that acts as a push server (e.g. hub.signalscope.site).
# Other SignalScope instances route their push notifications here via push_server_url.
# This hub holds the APNs (.p8 key) and FCM (service-account JSON) credentials and
# performs the actual delivery to iOS / Android devices.
#
# Route exposed:
#   POST /api/push/v1/send  — receive alert + tokens, deliver via APNs/FCM
#   GET  /hub/push          — admin UI (credentials + delivery log)
#   POST /hub/push/save     — save credentials

SIGNALSCOPE_PLUGIN = {
    "id":       "push",
    "label":    "Push Server",
    "url":      "/hub/push",
    "icon":     "📡",
    "hub_only": True,
    "version":  "1.0.2",
}

# ─── plugin version ───────────────────────────────────────────────────────────
_PLUGIN_VERSION = "1.0.2"

import json as _json
import os as _os
import pathlib as _pathlib
import threading as _threading
import time as _time

# ─── config file (lives alongside signalscope.py / push.py) ──────────────────
_cfg_path: "_pathlib.Path|None" = None
_cfg_lock = _threading.Lock()
_cfg: dict = {}

# Delivery log — kept in memory, last 200 entries
_log_lock = _threading.Lock()
_delivery_log: list = []
_MAX_LOG = 200

# JWT / access-token caches (same pattern as signalscope.py)
_apns_jwt_cache: dict = {"token": "", "generated_at": 0.0, "cache_key": ""}
_apns_tokens_lock = _threading.Lock()
_fcm_token_cache: dict = {"token": "", "generated_at": 0.0, "cache_key": ""}


# ─── helpers ──────────────────────────────────────────────────────────────────

def _load_cfg(path: "_pathlib.Path") -> dict:
    try:
        return _json.loads(path.read_text()) if path.exists() else {}
    except Exception:
        return {}


def _save_cfg(path: "_pathlib.Path", data: dict):
    path.write_text(_json.dumps(data, indent=2))


def _log_delivery(msg: str):
    entry = {"ts": _time.strftime("%Y-%m-%d %H:%M:%S"), "msg": msg}
    with _log_lock:
        _delivery_log.append(entry)
        if len(_delivery_log) > _MAX_LOG:
            del _delivery_log[:-_MAX_LOG]


# ─── APNs ─────────────────────────────────────────────────────────────────────

def _apns_make_jwt(key_id: str, team_id: str, key_pem: str) -> str:
    import base64, json as _j
    from cryptography.hazmat.primitives import hashes as _ch, serialization as _cs
    from cryptography.hazmat.primitives.asymmetric import ec as _ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature as _dds
    hdr = base64.urlsafe_b64encode(_j.dumps({"alg": "ES256", "kid": key_id}).encode()).rstrip(b"=").decode()
    pay = base64.urlsafe_b64encode(_j.dumps({"iss": team_id, "iat": int(_time.time())}).encode()).rstrip(b"=").decode()
    msg = f"{hdr}.{pay}".encode()
    priv = _cs.load_pem_private_key(key_pem.encode(), password=None)
    sig_der = priv.sign(msg, _ec.ECDSA(_ch.SHA256()))
    r, s = _dds(sig_der)
    raw_sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    sig = base64.urlsafe_b64encode(raw_sig).rstrip(b"=").decode()
    return f"{hdr}.{pay}.{sig}"


def _get_apns_jwt(key_id: str, team_id: str, key_pem: str) -> str:
    now = _time.time()
    cache_key = f"{key_id}:{team_id}"
    if (now - _apns_jwt_cache["generated_at"] > 55 * 60
            or not _apns_jwt_cache["token"]
            or _apns_jwt_cache.get("cache_key") != cache_key):
        _apns_jwt_cache["token"] = _apns_make_jwt(key_id, team_id, key_pem)
        _apns_jwt_cache["generated_at"] = now
        _apns_jwt_cache["cache_key"] = cache_key
    return _apns_jwt_cache["token"]


def _send_apns_batch(tokens_ios: list, title: str, body: str, data: dict,
                     key_id: str, team_id: str, bundle_id: str, key_pem: str) -> list:
    """Deliver to iOS tokens. Returns list of dead token strings to remove."""
    import urllib.request as _ur, urllib.error as _ue
    dead = []
    if not (key_id and team_id and bundle_id and key_pem):
        _log_delivery("APNs: credentials not configured — skipped")
        return dead
    try:
        jwt = _get_apns_jwt(key_id, team_id, key_pem)
    except Exception as e:
        _log_delivery(f"APNs: JWT error — {e}")
        return dead

    for entry in tokens_ios:
        token = entry if isinstance(entry, str) else entry.get("token", "")
        sandbox = True if isinstance(entry, str) else bool(entry.get("sandbox", True))
        if not token:
            continue
        host = "api.sandbox.push.apple.com" if sandbox else "api.push.apple.com"
        try:
            import http.client as _hc
            conn = _hc.HTTPSConnection(host, 443)
            payload = _json.dumps({
                "aps": {
                    "alert": {"title": title, "body": body},
                    "sound": "default",
                }
            } | ({"data": data} if data else {})).encode()
            headers = {
                "authorization": f"bearer {jwt}",
                "apns-topic":    bundle_id,
                "apns-push-type": "alert",
                "content-type":  "application/json",
            }
            conn.request("POST", f"/3/device/{token}", payload, headers)
            resp = conn.getresponse()
            if resp.status == 200:
                _log_delivery(f"APNs ✔ {token[:12]}… — {title!r}")
            elif resp.status == 410:
                _log_delivery(f"APNs ✘ 410 (gone) {token[:12]}…")
                dead.append(token)
            else:
                err = resp.read().decode(errors="replace")[:120]
                _log_delivery(f"APNs ✘ {resp.status} {token[:12]}…: {err}")
                if resp.status == 400 and "BadDeviceToken" in err:
                    dead.append(token)
        except Exception as e:
            _log_delivery(f"APNs error {token[:12]}…: {e}")
    return dead


# ─── FCM ──────────────────────────────────────────────────────────────────────

def _fcm_make_jwt(client_email: str, private_key_pem: str) -> str:
    import json as _j, base64 as _b64
    from cryptography.hazmat.primitives import serialization as _sl, hashes as _h
    from cryptography.hazmat.primitives.asymmetric import padding as _pad
    header  = {"alg": "RS256", "typ": "JWT"}
    now     = int(_time.time())
    payload = {
        "iss":   client_email,
        "scope": "https://www.googleapis.com/auth/firebase.messaging",
        "aud":   "https://oauth2.googleapis.com/token",
        "iat":   now, "exp": now + 3600,
    }
    def _b64url(d):
        return _b64.urlsafe_b64encode(_j.dumps(d, separators=(",",":")).encode()).rstrip(b"=").decode()
    unsigned = f"{_b64url(header)}.{_b64url(payload)}".encode()
    key = _sl.load_pem_private_key(private_key_pem.encode(), password=None)
    sig = key.sign(unsigned, _pad.PKCS1v15(), _h.SHA256())
    return f"{unsigned.decode()}.{_b64.urlsafe_b64encode(sig).rstrip(b'=').decode()}"


def _get_fcm_token(sa_json_str: str) -> str:
    import json as _j, urllib.request as _ur, urllib.parse as _up
    sa = _j.loads(sa_json_str)
    email = sa.get("client_email", "")
    key   = sa.get("private_key",  "")
    now   = _time.time()
    if (_fcm_token_cache.get("token")
            and _fcm_token_cache.get("cache_key") == email
            and now - _fcm_token_cache.get("generated_at", 0) < 55 * 60):
        return _fcm_token_cache["token"]
    jwt_str = _fcm_make_jwt(email, key)
    data = _up.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion":  jwt_str,
    }).encode()
    req  = _ur.Request("https://oauth2.googleapis.com/token", data=data, method="POST")
    tok  = _j.loads(_ur.urlopen(req, timeout=15).read()).get("access_token", "")
    _fcm_token_cache.update({"token": tok, "generated_at": now, "cache_key": email})
    return tok


def _send_fcm_batch(tokens_android: list, title: str, body: str, data: dict,
                    project_id: str, sa_json: str) -> list:
    """Deliver to Android tokens. Returns list of dead token strings to remove."""
    import urllib.request as _ur
    dead = []
    if not (project_id and sa_json):
        _log_delivery("FCM: credentials not configured — skipped")
        return dead
    try:
        token = _get_fcm_token(sa_json)
    except Exception as e:
        _log_delivery(f"FCM: access token error — {e}")
        return dead

    url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
    for entry in tokens_android:
        tok = entry.get("token", "") if isinstance(entry, dict) else str(entry)
        if not tok:
            continue
        payload = _json.dumps({
            "message": {
                "token": tok,
                "notification": {"title": title, "body": body},
                "data": {k: str(v) for k, v in (data or {}).items()},
                "android": {"priority": "high",
                            "notification": {"channel_id": "faults"}},
            }
        }).encode()
        req = _ur.Request(url, data=payload, method="POST", headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
        })
        try:
            _ur.urlopen(req, timeout=10)
            _log_delivery(f"FCM ✔ {tok[:12]}… — {title!r}")
        except _ur.HTTPError as e:
            err = e.read().decode(errors="replace")[:120]
            _log_delivery(f"FCM ✘ {e.code} {tok[:12]}…: {err}")
            if e.code in (400, 404):
                dead.append(tok)
        except Exception as e:
            _log_delivery(f"FCM error {tok[:12]}…: {e}")
    return dead


# ─── HTML template ────────────────────────────────────────────────────────────

_TPL = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="csrf-token" content="{{csrf_token()}}">
<title>Push Server — SignalScope</title>
<style nonce="{{csp_nonce()}}">
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:radial-gradient(circle at top,#12376f 0%,var(--bg) 38%,#05101f 100%);color:var(--tx);font-size:14px;min-height:100vh}
header{background:linear-gradient(180deg,rgba(10,31,65,.96),rgba(9,24,48,.96));border-bottom:1px solid var(--bor);padding:12px 20px;display:flex;align-items:center;gap:10px}
header h1{font-size:16px;font-weight:700}
a.back{color:var(--acc);text-decoration:none;font-size:13px}
.wrap{max-width:860px;margin:0 auto;padding:24px 20px}
.card{background:var(--sur);border:1px solid var(--bor);border-radius:12px;padding:20px;margin-bottom:20px}
h2{font-size:15px;font-weight:700;margin-bottom:14px;color:var(--acc)}
label{display:flex;flex-direction:column;gap:4px;margin-bottom:12px;font-size:13px;color:var(--mu)}
label span{color:var(--tx);font-weight:500}
input,textarea{background:#071428;border:1px solid var(--bor);border-radius:8px;color:var(--tx);padding:8px 10px;font-size:13px;width:100%;outline:none}
input:focus,textarea:focus{border-color:var(--acc)}
textarea{font-family:ui-monospace,monospace;font-size:11px;resize:vertical}
.btn{display:inline-block;padding:6px 16px;border-radius:8px;font-size:13px;cursor:pointer;border:none;font-weight:600}
.bp{background:var(--acc);color:#fff}.bg{background:var(--bor);color:var(--tx)}
.ok{color:var(--ok)}.al{color:var(--al)}.mu{color:var(--mu)}
.stat{padding:10px 14px;border:1px solid var(--bor);border-radius:8px;background:#0c1f40;font-size:13px;margin-bottom:12px}
.log-entry{font-size:12px;font-family:ui-monospace,monospace;padding:4px 0;border-bottom:1px solid #0d2040;color:var(--mu)}
.log-entry .ts{color:#4a6a9a;margin-right:8px}
</style></head><body>
<header>
  <a class="back" href="/">← Back</a>
  <h1>📡 Push Server</h1>
  <span style="margin-left:auto;font-size:12px;color:var(--mu)">v{{version}}</span>
</header>
<div class="wrap">

  <div class="card">
    <h2>Status</h2>
    <div class="stat">
      {% if apns_ok %}<span class="ok">✔ APNs configured</span>{% else %}<span class="al">✘ APNs not configured</span>{% endif %}
      &nbsp;·&nbsp;
      {% if fcm_ok %}<span class="ok">✔ FCM configured</span>{% else %}<span class="al">✘ FCM not configured</span>{% endif %}
    </div>
    <p style="font-size:12px;color:var(--mu)">This hub is acting as a push server. Other SignalScope installations should set their <strong>Push Server URL</strong> to <code style="background:#071428;padding:1px 5px;border-radius:4px">{{request.host_url.rstrip('/')}}</code> in Settings → Notifications.</p>
    {% if migrate_available %}
    <div style="margin-top:14px;padding:12px 14px;border:1px solid var(--wn);border-radius:8px;background:#1a1000">
      <p style="font-size:13px;color:var(--wn);margin-bottom:10px">⬆ <strong>Existing credentials found</strong> in SignalScope Settings → Notifications. Click below to copy them here in one go — no re-typing needed.</p>
      <button id="migrate-btn" class="btn" style="background:var(--wn);color:#000">⬆ Migrate from existing settings</button>
    </div>
    {% endif %}
    {% if migrate_done %}
    <div style="margin-top:14px;padding:10px 14px;border:1px solid var(--ok);border-radius:8px;background:#001a08;font-size:13px;color:var(--ok)">
      ✔ Credentials migrated successfully. You can now clear the APNs/FCM fields in Settings → Notifications.
    </div>
    {% endif %}
    {% if saved %}
    <div style="margin-top:14px;padding:10px 14px;border:1px solid var(--ok);border-radius:8px;background:#001a08;font-size:13px;color:var(--ok)">
      ✔ Credentials saved.
    </div>
    {% endif %}
  </div>

  <div id="push-creds-form">
    <div class="card">
      <h2>🔔 APNs Credentials (iOS)</h2>
      <p style="font-size:12px;color:var(--mu);margin-bottom:14px">Get from <strong>developer.apple.com → Keys</strong>. Create a key with Apple Push Notifications service enabled.</p>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
        <label><span>Key ID</span><input id="apns_key_id" value="{{cfg.apns_key_id}}" placeholder="ABC1234DEF" maxlength="10" style="font-family:monospace"></label>
        <label><span>Team ID</span><input id="apns_team_id" value="{{cfg.apns_team_id}}" placeholder="XYZ9876543" maxlength="10" style="font-family:monospace"></label>
        <label style="grid-column:1/-1"><span>Bundle ID</span><input id="apns_bundle_id" value="{{cfg.apns_bundle_id}}" placeholder="com.example.SignalScope"></label>
      </div>
      <label><span>.p8 Private Key <em style="font-weight:400;color:var(--mu)">(leave blank to keep existing)</em></span>
        <textarea id="apns_key_pem" rows="6" placeholder="-----BEGIN PRIVATE KEY-----&#10;…&#10;-----END PRIVATE KEY-----"></textarea>
      </label>
    </div>

    <div class="card">
      <h2>🤖 FCM Credentials (Android)</h2>
      <p style="font-size:12px;color:var(--mu);margin-bottom:14px">Get from <strong>console.firebase.google.com → Project settings → Service accounts → Generate new private key</strong>.</p>
      <label><span>Firebase Project ID</span><input id="fcm_project_id" value="{{cfg.fcm_project_id}}" placeholder="my-project-12345" style="font-family:monospace"></label>
      <label><span>Service Account JSON <em style="font-weight:400;color:var(--mu)">(leave blank to keep existing — current: {{cfg.fcm_service_account_json[:40]+'…' if cfg.fcm_service_account_json else 'not set'}})</em></span>
        <textarea id="fcm_service_account_json" rows="5" placeholder='{"type":"service_account","project_id":"...",...}'></textarea>
      </label>
    </div>

    <div style="display:flex;gap:10px">
      <button id="save-btn" class="btn bp">💾 Save credentials</button>
      <a class="btn bg" href="/">Cancel</a>
    </div>
  </div>

  <div class="card" style="margin-top:20px">
    <h2>📋 Recent Deliveries</h2>
    {% if log %}
      {% for e in log %}
      <div class="log-entry"><span class="ts">{{e.ts}}</span>{{e.msg}}</div>
      {% endfor %}
    {% else %}
      <p class="mu" style="font-size:13px">No deliveries yet.</p>
    {% endif %}
  </div>

</div>
<script nonce="{{csp_nonce()}}">
function _csrf(){
  return (document.querySelector('meta[name="csrf-token"]')||{}).content
        ||(document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/)||[])[1]||'';
}
function _val(id){var e=document.getElementById(id);return e?e.value:'';};

var _sb=document.getElementById('save-btn');
if(_sb){_sb.addEventListener('click',function(){
  _sb.disabled=true;_sb.textContent='Saving…';
  var body=JSON.stringify({
    apns_key_id:_val('apns_key_id'),apns_team_id:_val('apns_team_id'),
    apns_bundle_id:_val('apns_bundle_id'),apns_key_pem:_val('apns_key_pem'),
    fcm_project_id:_val('fcm_project_id'),fcm_service_account_json:_val('fcm_service_account_json')
  });
  fetch('/hub/push/save',{method:'POST',headers:{'X-CSRFToken':_csrf(),'Content-Type':'application/json'},body:body,credentials:'same-origin'})
    .then(function(r){if(r.ok){window.location='/hub/push?saved=1';}else{r.text().then(function(t){alert('Save failed: '+t);_sb.disabled=false;_sb.textContent='💾 Save credentials';});}})
    .catch(function(e){alert('Save error: '+e);_sb.disabled=false;_sb.textContent='💾 Save credentials';});
});}

var _mb=document.getElementById('migrate-btn');
if(_mb){_mb.addEventListener('click',function(){
  _mb.disabled=true;_mb.textContent='Migrating…';
  fetch('/hub/push/migrate',{method:'POST',headers:{'X-CSRFToken':_csrf(),'Content-Type':'application/json'},body:'{}',credentials:'same-origin'})
    .then(function(r){if(r.ok){window.location='/hub/push?migrated=1';}else{r.text().then(function(t){alert('Migration failed: '+t);_mb.disabled=false;_mb.textContent='⬆ Migrate from existing settings';});}})
    .catch(function(e){alert('Migration error: '+e);_mb.disabled=false;_mb.textContent='⬆ Migrate from existing settings';});
});}
</script>
</body></html>"""


# ─── register ─────────────────────────────────────────────────────────────────

def register(app, ctx):
    global _cfg_path, _cfg

    from flask import request, jsonify, render_template_string, redirect

    login_required  = ctx["login_required"]
    csrf_protect    = ctx["csrf_protect"]
    monitor         = ctx["monitor"]

    # Config file lives alongside signalscope.py
    _cfg_path = _pathlib.Path(__file__).with_name("push_config.json")
    with _cfg_lock:
        _cfg = _load_cfg(_cfg_path)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _get(key, default=""):
        return _cfg.get(key, default)

    class _CfgProxy:
        apns_key_id            = property(lambda s: _get("apns_key_id"))
        apns_team_id           = property(lambda s: _get("apns_team_id"))
        apns_bundle_id         = property(lambda s: _get("apns_bundle_id"))
        apns_key_pem           = property(lambda s: _get("apns_key_pem"))
        fcm_project_id         = property(lambda s: _get("fcm_project_id"))
        fcm_service_account_json = property(lambda s: _get("fcm_service_account_json"))

    # ── routes ────────────────────────────────────────────────────────────────

    @app.get("/hub/push")
    @login_required
    def push_admin():
        with _cfg_lock:
            c = dict(_cfg)
        proxy = _CfgProxy()
        apns_ok = bool(c.get("apns_key_id") and c.get("apns_team_id")
                       and c.get("apns_bundle_id") and c.get("apns_key_pem"))
        fcm_ok  = bool(c.get("fcm_project_id") and c.get("fcm_service_account_json"))
        with _log_lock:
            log = list(reversed(_delivery_log))
        # Show migrate banner if signalscope.py still has credentials not yet copied here
        ma = getattr(monitor.app_cfg, "mobile_api", None)
        migrate_available = bool(
            ma and not apns_ok and not fcm_ok and (
                getattr(ma, "apns_key_id", "") or getattr(ma, "fcm_project_id", "")
            )
        )
        migrate_done = request.args.get("migrated") == "1"
        saved        = request.args.get("saved")    == "1"
        return render_template_string(_TPL, cfg=proxy, apns_ok=apns_ok,
                                      fcm_ok=fcm_ok, log=log, version=_PLUGIN_VERSION,
                                      migrate_available=migrate_available,
                                      migrate_done=migrate_done, saved=saved)

    @app.post("/hub/push/migrate")
    @login_required
    @csrf_protect
    def push_migrate():
        """Copy APNs/FCM credentials from the running SignalScope config into push_config.json."""
        ma = getattr(monitor.app_cfg, "mobile_api", None)
        if not ma:
            return jsonify({"ok": False, "error": "no config"}), 500
        with _cfg_lock:
            if getattr(ma, "apns_key_id", ""):
                _cfg["apns_key_id"]    = getattr(ma, "apns_key_id",    "")
                _cfg["apns_team_id"]   = getattr(ma, "apns_team_id",   "")
                _cfg["apns_bundle_id"] = getattr(ma, "apns_bundle_id", "")
                _cfg["apns_key_pem"]   = getattr(ma, "apns_key_pem",   "")
            if getattr(ma, "fcm_project_id", ""):
                _cfg["fcm_project_id"]           = getattr(ma, "fcm_project_id",           "")
                _cfg["fcm_service_account_json"] = getattr(ma, "fcm_service_account_json", "")
            _apns_jwt_cache.update({"token": "", "generated_at": 0.0, "cache_key": ""})
            _fcm_token_cache.update({"token": "", "generated_at": 0.0, "cache_key": ""})
            _save_cfg(_cfg_path, _cfg)
        monitor.log("[Push] Credentials migrated from SignalScope config → push_config.json")
        return jsonify({"ok": True})

    @app.post("/hub/push/save")
    @login_required
    @csrf_protect
    def push_save():
        try:
            f = request.get_json(force=True) or {}
        except Exception:
            f = {}
        with _cfg_lock:
            _cfg["apns_key_id"]    = str(f.get("apns_key_id",    "")).strip()
            _cfg["apns_team_id"]   = str(f.get("apns_team_id",   "")).strip()
            _cfg["apns_bundle_id"] = str(f.get("apns_bundle_id", "")).strip()
            pem = str(f.get("apns_key_pem", "")).strip()
            if pem:
                _cfg["apns_key_pem"] = pem
            pid = str(f.get("fcm_project_id", "")).strip()
            if pid:
                _cfg["fcm_project_id"] = pid
            sa  = str(f.get("fcm_service_account_json", "")).strip()
            if sa:
                _cfg["fcm_service_account_json"] = sa
            _apns_jwt_cache.update({"token": "", "generated_at": 0.0, "cache_key": ""})
            _fcm_token_cache.update({"token": "", "generated_at": 0.0, "cache_key": ""})
            _save_cfg(_cfg_path, _cfg)
        monitor.log("[Push] Credentials saved")
        return jsonify({"ok": True})

    @app.post("/api/push/v1/send")
    def push_v1_send():
        """Receive a push notification request from a remote SignalScope hub
        and deliver it to the provided iOS / Android device tokens using
        APNs and FCM credentials configured on this server.

        Request body (JSON):
            title          str
            body           str
            data           dict  (all values coerced to str)
            tokens_ios     list  (str or {"token":…,"sandbox":bool})
            tokens_android list  (str or {"token":…})

        Response (JSON):
            ok             bool
            sent_ios       int
            sent_android   int
            dead_tokens    list[str]
        """
        try:
            body_bytes = request.get_data()
            req = _json.loads(body_bytes)
        except Exception:
            return jsonify({"ok": False, "error": "bad JSON"}), 400

        title          = str(req.get("title", ""))
        body_text      = str(req.get("body",  ""))
        data           = {k: str(v) for k, v in (req.get("data") or {}).items()}
        tokens_ios     = req.get("tokens_ios",     []) or []
        tokens_android = req.get("tokens_android", []) or []

        with _cfg_lock:
            c = dict(_cfg)

        dead_all = []

        # APNs delivery
        dead_ios = _send_apns_batch(
            tokens_ios, title, body_text, data,
            key_id    = c.get("apns_key_id",    ""),
            team_id   = c.get("apns_team_id",   ""),
            bundle_id = c.get("apns_bundle_id", ""),
            key_pem   = c.get("apns_key_pem",   ""),
        )
        dead_all.extend(dead_ios)

        # FCM delivery
        dead_android = _send_fcm_batch(
            tokens_android, title, body_text, data,
            project_id = c.get("fcm_project_id",           ""),
            sa_json    = c.get("fcm_service_account_json", ""),
        )
        dead_all.extend(dead_android)

        sent_ios     = max(0, len(tokens_ios)     - len(dead_ios))
        sent_android = max(0, len(tokens_android) - len(dead_android))

        monitor.log(f"[Push] Delivered: ios={sent_ios}/{len(tokens_ios)} "
                    f"android={sent_android}/{len(tokens_android)} "
                    f"dead={len(dead_all)}")

        return jsonify({
            "ok":           True,
            "sent_ios":     sent_ios,
            "sent_android": sent_android,
            "dead_tokens":  dead_all,
        })
