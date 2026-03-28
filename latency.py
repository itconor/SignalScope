# latency.py — Signal Path Latency Tracking plugin for SignalScope
# Drop alongside signalscope.py; auto-discovered on next start.
# Hub-only. Tracks comparator-measured audio delay across all connected sites.

SIGNALSCOPE_PLUGIN = {
    "id":       "latency",
    "label":    "Latency",
    "url":      "/hub/latency",
    "icon":     "⏱",
    "hub_only": True,
    "version":  "1.0.0",
}

import os, json, time, threading, sqlite3
from collections import defaultdict

_BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
_DB_PATH    = os.path.join(_BASE_DIR, "latency_history.db")
_CFG_PATH   = os.path.join(_BASE_DIR, "latency_cfg.json")

_POLL_INTERVAL_S  = 30
_RETENTION_DAYS   = 90
_BASELINE_WINDOW  = 1000       # readings per comparator pair for baseline median
_MIN_READINGS_FOR_BASELINE = 10  # need at least this many before computing baseline
_DEFAULT_ALERT_THRESHOLD_MS = 500.0
_DRIFT_THRESHOLD_MS          = 200.0

# Module-level state
_db_lock    = threading.Lock()
_alerts     = {}          # (site, pre, post) → {ts, delta_ms, delay_ms}
_baselines  = {}          # (site, pre, post) → median delay_ms (float)
_latest     = {}          # (site, pre, post) → {delay_ms, gain_diff_db, ts, status}
_poller_started = False


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS latency_readings(
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            site         TEXT NOT NULL,
            pre_stream   TEXT NOT NULL,
            post_stream  TEXT NOT NULL,
            delay_ms     REAL NOT NULL,
            gain_diff_db REAL,
            ts           INTEGER NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_lat_key_ts
        ON latency_readings(site, pre_stream, post_stream, ts)
    """)
    conn.commit()
    return conn


def _db_insert(site: str, pre: str, post: str, delay_ms: float,
               gain_diff_db, ts: int):
    with _db_lock:
        conn = _db_connect()
        try:
            conn.execute(
                """INSERT INTO latency_readings(site,pre_stream,post_stream,delay_ms,gain_diff_db,ts)
                   VALUES(?,?,?,?,?,?)""",
                (site, pre, post, delay_ms, gain_diff_db, ts)
            )
            conn.commit()
        finally:
            conn.close()


def _db_prune():
    cutoff = int(time.time()) - _RETENTION_DAYS * 86400
    with _db_lock:
        conn = _db_connect()
        try:
            conn.execute("DELETE FROM latency_readings WHERE ts < ?", (cutoff,))
            conn.commit()
        finally:
            conn.close()


def _db_get_recent(site: str, pre: str, post: str,
                   limit: int = _BASELINE_WINDOW) -> list:
    """Return the `limit` most recent readings for a comparator pair."""
    with _db_lock:
        conn = _db_connect()
        try:
            cur = conn.execute(
                """SELECT delay_ms, gain_diff_db, ts FROM latency_readings
                   WHERE site=? AND pre_stream=? AND post_stream=?
                   ORDER BY ts DESC LIMIT ?""",
                (site, pre, post, limit)
            )
            return [{"delay_ms": r[0], "gain_diff_db": r[1], "ts": r[2]}
                    for r in cur.fetchall()]
        finally:
            conn.close()


def _db_get_range(site: str, pre: str, post: str,
                  since_ts: int, limit: int = 2000) -> list:
    """Return readings newer than since_ts, oldest first."""
    with _db_lock:
        conn = _db_connect()
        try:
            cur = conn.execute(
                """SELECT delay_ms, gain_diff_db, ts FROM latency_readings
                   WHERE site=? AND pre_stream=? AND post_stream=? AND ts >= ?
                   ORDER BY ts ASC LIMIT ?""",
                (site, pre, post, since_ts, limit)
            )
            return [{"delay_ms": r[0], "gain_diff_db": r[1], "ts": r[2]}
                    for r in cur.fetchall()]
        finally:
            conn.close()


# ── Config helpers ─────────────────────────────────────────────────────────────

def _load_cfg() -> dict:
    try:
        if os.path.exists(_CFG_PATH):
            with open(_CFG_PATH) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_cfg(cfg: dict):
    try:
        with open(_CFG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


def _pair_key_str(site: str, pre: str, post: str) -> str:
    return f"{site}||{pre}||{post}"


def _get_threshold(cfg: dict, site: str, pre: str, post: str) -> float:
    key = _pair_key_str(site, pre, post)
    pair_cfg = cfg.get("pairs", {}).get(key, {})
    return float(pair_cfg.get("alert_threshold_ms", _DEFAULT_ALERT_THRESHOLD_MS))


def _is_enabled(cfg: dict, site: str, pre: str, post: str) -> bool:
    key = _pair_key_str(site, pre, post)
    pair_cfg = cfg.get("pairs", {}).get(key, {})
    return bool(pair_cfg.get("alerting_enabled", True))


# ── Baseline computation ───────────────────────────────────────────────────────

def _compute_baseline(site: str, pre: str, post: str):
    rows = _db_get_recent(site, pre, post, limit=_BASELINE_WINDOW)
    if len(rows) < _MIN_READINGS_FOR_BASELINE:
        return None
    values = sorted(r["delay_ms"] for r in rows)
    mid = len(values) // 2
    if len(values) % 2 == 0:
        return (values[mid - 1] + values[mid]) / 2.0
    return float(values[mid])


# ── Poller thread ──────────────────────────────────────────────────────────────

def _poller_loop(hub_server, monitor):
    prune_counter = 0
    while True:
        time.sleep(_POLL_INTERVAL_S)
        try:
            cfg = _load_cfg()
            now_ts = int(time.time())
            prune_counter += 1
            if prune_counter >= 288:   # ~once per day
                prune_counter = 0
                _db_prune()

            sites = hub_server.get_sites() if hub_server else []
            for site_data in sites:
                site_name = site_data.get("site", "?")
                comparators = site_data.get("comparators", [])
                if not comparators:
                    continue

                for comp in comparators:
                    pre  = comp.get("pre", "")
                    post = comp.get("post", "")
                    delay_ms = comp.get("delay_ms")
                    gain_diff = comp.get("gain_diff_db")
                    if pre and post and delay_ms is not None:
                        try:
                            delay_f = float(delay_ms)
                        except (TypeError, ValueError):
                            continue

                        # Store reading
                        _db_insert(site_name, pre, post, delay_f,
                                   float(gain_diff) if gain_diff is not None else None,
                                   now_ts)

                        # Compute/refresh baseline
                        key = (site_name, pre, post)
                        baseline = _compute_baseline(site_name, pre, post)
                        _baselines[key] = baseline

                        # Determine status
                        if baseline is not None:
                            delta = abs(delay_f - baseline)
                            threshold = _get_threshold(cfg, site_name, pre, post)
                            if delta >= threshold:
                                status = "alert"
                            elif delta >= _DRIFT_THRESHOLD_MS:
                                status = "drifting"
                            else:
                                status = "stable"
                        else:
                            delta  = None
                            status = "stable"

                        _latest[key] = {
                            "delay_ms":    delay_f,
                            "gain_diff_db": float(gain_diff) if gain_diff is not None else None,
                            "ts":          now_ts,
                            "status":      status,
                            "delta":       delta,
                        }

                        # Alert logging
                        if (status == "alert"
                                and _is_enabled(cfg, site_name, pre, post)):
                            prev_alert = _alerts.get(key)
                            # Only fire once per alert state entry (cooldown 5 min)
                            if (prev_alert is None
                                    or now_ts - prev_alert.get("ts", 0) > 300):
                                _alerts[key] = {
                                    "ts":       now_ts,
                                    "delta_ms": delta,
                                    "delay_ms": delay_f,
                                }
                                monitor.log(
                                    f"[Latency] ALERT {site_name} {pre}→{post}: "
                                    f"delay {delay_f:.0f} ms "
                                    f"(baseline {baseline:.0f} ms, "
                                    f"delta {delta:.0f} ms)"
                                )
                        elif status != "alert":
                            _alerts.pop(key, None)

        except Exception as e:
            try:
                monitor.log(f"[Latency] Poller error: {e}")
            except Exception:
                pass


# ── SVG sparkline ──────────────────────────────────────────────────────────────

def _make_sparkline(readings, baseline,
                    width=200, height=40):
    """Generate a 200×40 SVG polyline from up to 48 readings."""
    if not readings:
        return f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}"></svg>'

    # Take last 48 readings, oldest first
    pts = readings[-48:]
    delays = [r["delay_ms"] for r in pts]

    if len(delays) < 2:
        delays = delays * 2   # avoid degenerate single-point line

    mn = min(delays)
    mx = max(delays)
    if baseline is not None:
        mn = min(mn, baseline)
        mx = max(mx, baseline)

    rng = mx - mn
    if rng < 1.0:
        rng = 1.0

    # Add 20% padding
    pad_y = rng * 0.20
    y_min = mn - pad_y
    y_max = mx + pad_y
    y_rng = y_max - y_min
    if y_rng < 1.0:
        y_rng = 1.0

    n = len(delays)
    xs = [round(i * (width - 2) / max(n - 1, 1) + 1, 1) for i in range(n)]

    def to_y(v):
        return round(height - 2 - (v - y_min) / y_rng * (height - 4), 1)

    points_str = " ".join(f"{xs[i]},{to_y(delays[i])}" for i in range(n))

    svg_parts = [
        f'<svg width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" '
        f'style="display:block;overflow:visible">'
    ]

    # Baseline horizontal dashed line
    if baseline is not None:
        by = to_y(baseline)
        svg_parts.append(
            f'<line x1="1" y1="{by}" x2="{width-1}" y2="{by}" '
            f'stroke="rgba(148,163,184,.5)" stroke-width="1" '
            f'stroke-dasharray="3,3"/>'
        )

    # Data polyline
    svg_parts.append(
        f'<polyline points="{points_str}" '
        f'fill="none" stroke="#6366f1" stroke-width="1.5" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
    )

    svg_parts.append('</svg>')
    return "".join(svg_parts)


# ── Register ───────────────────────────────────────────────────────────────────

def register(app, ctx):
    global _poller_started

    login_required = ctx["login_required"]
    csrf_protect   = ctx["csrf_protect"]
    monitor        = ctx["monitor"]
    hub_server     = ctx.get("hub_server")
    BUILD          = ctx["BUILD"]

    from flask import render_template_string, request, jsonify

    # Ensure DB is initialised
    try:
        conn = _db_connect()
        conn.close()
    except Exception as e:
        monitor.log(f"[Latency] DB init error: {e}")

    # Start poller
    if not _poller_started:
        _poller_started = True
        t = threading.Thread(
            target=_poller_loop,
            args=(hub_server, monitor),
            daemon=True,
            name="LatencyPoller",
        )
        t.start()

    # ── Page ───────────────────────────────────────────────────────────────────

    @app.get("/hub/latency")
    @login_required
    def latency_page():
        cfg = _load_cfg()

        # Build comparator cards
        cards = []
        for (site, pre, post), info in sorted(_latest.items(),
                                               key=lambda x: (x[0][0], x[0][1], x[0][2])):
            baseline  = _baselines.get((site, pre, post))
            delay_ms  = info["delay_ms"]
            delta     = info.get("delta")
            status    = info["status"]
            gain_diff = info.get("gain_diff_db")
            alert_ts  = _alerts.get((site, pre, post), {}).get("ts")

            # Generate sparkline from 24h of data
            since_24h = int(time.time()) - 86400
            history   = _db_get_range(site, pre, post, since_ts=since_24h, limit=200)
            sparkline = _make_sparkline(history, baseline)

            pair_key = _pair_key_str(site, pre, post)
            threshold = _get_threshold(cfg, site, pre, post)
            alerting  = _is_enabled(cfg, site, pre, post)

            cards.append({
                "site":       site,
                "pre":        pre,
                "post":       post,
                "pair_key":   pair_key,
                "delay_ms":   round(delay_ms, 1),
                "baseline":   round(baseline, 1) if baseline is not None else None,
                "delta":      round(delta, 1) if delta is not None else None,
                "status":     status,
                "gain_diff":  round(gain_diff, 1) if gain_diff is not None else None,
                "sparkline":  sparkline,
                "threshold_ms": threshold,
                "alerting":   alerting,
                "alert_ts":   alert_ts,
            })

        return render_template_string(_LATENCY_TPL, cards=cards, build=BUILD)

    # ── Settings ───────────────────────────────────────────────────────────────

    @app.get("/hub/latency/settings")
    @login_required
    def latency_settings_page():
        cfg = _load_cfg()
        # Build list of known pairs
        pairs = []
        for (site, pre, post) in sorted(_latest.keys(),
                                         key=lambda k: (k[0], k[1], k[2])):
            pk = _pair_key_str(site, pre, post)
            pair_cfg = cfg.get("pairs", {}).get(pk, {})
            pairs.append({
                "pair_key":         pk,
                "site":             site,
                "pre":              pre,
                "post":             post,
                "threshold_ms":     pair_cfg.get("alert_threshold_ms", _DEFAULT_ALERT_THRESHOLD_MS),
                "alerting_enabled": pair_cfg.get("alerting_enabled", True),
            })
        return render_template_string(_LATENCY_SETTINGS_TPL,
                                       pairs=pairs, build=BUILD, saved=False)

    @app.post("/hub/latency/settings")
    @login_required
    @csrf_protect
    def latency_settings_save():
        cfg = _load_cfg()
        pairs_cfg = cfg.setdefault("pairs", {})

        # Parse form fields: threshold_<pairkey_hash> and alerting_<pairkey_hash>
        # We pass pair_key as a form field name directly (URL-encoded)
        # Form fields are named: threshold_<n> / alerting_<n> / pk_<n>
        n = 0
        while True:
            pk_field = request.form.get(f"pk_{n}")
            if pk_field is None:
                break
            threshold_str = request.form.get(f"threshold_{n}", str(_DEFAULT_ALERT_THRESHOLD_MS))
            alerting_str  = request.form.get(f"alerting_{n}", "1")
            try:
                threshold = max(10.0, float(threshold_str))
            except (ValueError, TypeError):
                threshold = _DEFAULT_ALERT_THRESHOLD_MS
            alerting = (alerting_str == "1")
            pairs_cfg[pk_field] = {
                "alert_threshold_ms": threshold,
                "alerting_enabled":   alerting,
            }
            n += 1

        _save_cfg(cfg)
        monitor.log("[Latency] Settings saved")

        # Re-render with saved=True
        pairs = []
        for (site, pre, post) in sorted(_latest.keys(),
                                         key=lambda k: (k[0], k[1], k[2])):
            pk = _pair_key_str(site, pre, post)
            pair_cfg = pairs_cfg.get(pk, {})
            pairs.append({
                "pair_key":         pk,
                "site":             site,
                "pre":              pre,
                "post":             post,
                "threshold_ms":     pair_cfg.get("alert_threshold_ms", _DEFAULT_ALERT_THRESHOLD_MS),
                "alerting_enabled": pair_cfg.get("alerting_enabled", True),
            })
        return render_template_string(_LATENCY_SETTINGS_TPL,
                                       pairs=pairs, build=BUILD, saved=True)

    # ── API: status ────────────────────────────────────────────────────────────

    @app.get("/api/latency/status")
    @login_required
    def latency_api_status():
        result = []
        for (site, pre, post), info in sorted(_latest.items(),
                                               key=lambda x: (x[0][0], x[0][1], x[0][2])):
            baseline = _baselines.get((site, pre, post))
            result.append({
                "site":        site,
                "pre_stream":  pre,
                "post_stream": post,
                "delay_ms":    info["delay_ms"],
                "baseline_ms": baseline,
                "delta_ms":    info.get("delta"),
                "status":      info["status"],
                "gain_diff_db": info.get("gain_diff_db"),
                "ts":          info["ts"],
            })
        return jsonify(result)

    # ── API: history ───────────────────────────────────────────────────────────

    @app.get("/api/latency/history")
    @login_required
    def latency_api_history():
        site  = request.args.get("site", "")
        pre   = request.args.get("pre", "")
        post  = request.args.get("post", "")
        hours = int(request.args.get("hours", 24))
        if not (site and pre and post):
            return jsonify([])
        since_ts = int(time.time()) - hours * 3600
        rows = _db_get_range(site, pre, post, since_ts=since_ts, limit=5000)
        return jsonify(rows)


# ── Templates ──────────────────────────────────────────────────────────────────

_LATENCY_TPL = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<title>Signal Path Latency — SignalScope</title>
<style nonce="{{csp_nonce()}}">
:root{
  --bg:#0f1117;--bg2:#1a1d28;--bg3:#22263a;--bd:#2e3250;
  --tx:#e2e8f0;--mu:#94a3b8;--ok:#22c55e;--al:#ef4444;--wa:#f59e0b;--ac:#6366f1;
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:var(--bg);color:var(--tx);font-size:13px;line-height:1.5}
a{color:var(--ac);text-decoration:none}a:hover{text-decoration:underline}
.btn{display:inline-flex;align-items:center;gap:5px;padding:5px 14px;border-radius:7px;font-size:12px;font-weight:600;cursor:pointer;border:none;color:var(--tx);background:var(--bd);text-decoration:none;transition:filter .12s}
.btn:hover{filter:brightness(1.2)}.btn.bp{background:var(--ac);color:#fff}
.topbar{display:flex;align-items:center;gap:10px;padding:10px 20px;background:var(--bg2);border-bottom:1px solid var(--bd);flex-wrap:wrap}
.topbar-title{font-size:15px;font-weight:800}.topbar-right{margin-left:auto;display:flex;gap:8px;flex-wrap:wrap}
.page{max-width:1100px;margin:0 auto;padding:20px 16px 40px}
/* Summary table */
.sum-wrap{overflow-x:auto;margin-bottom:28px}
table{width:100%;border-collapse:collapse;font-size:12px}
th{background:var(--bg3);padding:7px 10px;text-align:left;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:var(--mu);white-space:nowrap}
td{padding:7px 10px;border-bottom:1px solid var(--bd);vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:rgba(255,255,255,.03)}
/* Status badges */
.badge{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:999px;font-size:10px;font-weight:700}
.badge-stable{background:rgba(34,197,94,.14);color:var(--ok)}
.badge-drifting{background:rgba(245,158,11,.14);color:var(--wa)}
.badge-alert{background:rgba(239,68,68,.18);color:var(--al)}
.badge-unknown{background:var(--bg3);color:var(--mu)}
/* Comparator cards */
.sec{margin-bottom:30px}
.sec-hdr{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:var(--mu);margin-bottom:12px;padding-bottom:6px;border-bottom:1px solid var(--bd)}
.cards{display:flex;flex-direction:column;gap:12px}
.comp-card{background:var(--bg2);border:1px solid var(--bd);border-radius:10px;border-left:4px solid var(--bd);overflow:hidden}
.comp-card.status-stable{border-left-color:var(--ok)}
.comp-card.status-drifting{border-left-color:var(--wa)}
.comp-card.status-alert{border-left-color:var(--al)}
.comp-head{display:flex;align-items:center;gap:12px;padding:12px 16px;cursor:pointer;user-select:none}
.comp-site{font-size:10px;color:var(--mu);white-space:nowrap}
.comp-pair{font-weight:700;font-size:13px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.comp-delay{font-size:24px;font-weight:800;font-variant-numeric:tabular-nums;line-height:1;white-space:nowrap}
.comp-delay-unit{font-size:11px;font-weight:400;color:var(--mu);margin-left:2px}
.comp-delta{font-size:11px;margin-top:2px;font-variant-numeric:tabular-nums}
.delta-up{color:var(--al)}.delta-dn{color:var(--ok)}.delta-eq{color:var(--mu)}
.comp-gain{font-size:11px;color:var(--mu);white-space:nowrap}
.expand-btn{font-size:14px;color:var(--mu);flex-shrink:0;transition:transform .2s}
.expand-btn.open{transform:rotate(180deg)}
/* Detail panel */
.comp-detail{display:none;padding:12px 16px 16px;border-top:1px solid var(--bd);background:rgba(0,0,0,.15)}
.comp-detail.open{display:block}
.detail-sparkline{margin-bottom:10px}
.detail-meta{display:flex;flex-wrap:wrap;gap:12px;font-size:11px;color:var(--mu)}
.detail-meta span b{color:var(--tx);font-variant-numeric:tabular-nums}
/* No data */
.no-data{color:var(--mu);font-style:italic;font-size:12px;margin:20px 0}
/* Auto refresh badge */
#refresh-ts{font-size:11px;color:var(--mu);margin-left:8px}
</style>
<link rel="icon" type="image/x-icon" href="/static/signalscope_icon.png">
</head>
<body>

<div class="topbar">
  <span class="topbar-title">⏱ Signal Path Latency</span>
  <span id="refresh-ts"></span>
  <div class="topbar-right">
    <a class="btn" href="/hub/latency/settings">⚙ Settings</a>
    <a class="btn" href="/api/latency/status" target="_blank">JSON Status</a>
    <a class="btn" href="/">⌂ Dashboard</a>
  </div>
</div>

<div class="page">

{% if not cards %}
<div class="no-data">
  No comparator data found. Comparators are reported in site heartbeats under the <code>comparators</code> key. Waiting for connected sites to report latency data…
</div>
{% else %}

{# ── Summary Table ── #}
<div class="sec">
  <div class="sec-hdr">Comparator Summary</div>
  <div class="sum-wrap">
    <table>
      <thead>
        <tr>
          <th>Site</th>
          <th>Pre → Post</th>
          <th>Current Delay</th>
          <th>Baseline</th>
          <th>Delta</th>
          <th>Gain Diff</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {% for c in cards %}
        <tr>
          <td style="color:var(--mu)">{{c.site}}</td>
          <td><b>{{c.pre}}</b> → <b>{{c.post}}</b></td>
          <td style="font-variant-numeric:tabular-nums;font-weight:700">
            {{c.delay_ms}} ms
          </td>
          <td style="font-variant-numeric:tabular-nums;color:var(--mu)">
            {% if c.baseline is not none %}{{c.baseline}} ms{% else %}—{% endif %}
          </td>
          <td style="font-variant-numeric:tabular-nums">
            {% if c.delta is not none %}
              {% if c.delta > 200 %}
              <span style="color:var(--al)">↑ +{{c.delta}} ms</span>
              {% elif c.delta > 50 %}
              <span style="color:var(--wa)">~ {{c.delta}} ms</span>
              {% else %}
              <span style="color:var(--ok)">≈ {{c.delta}} ms</span>
              {% endif %}
            {% else %}—{% endif %}
          </td>
          <td style="font-variant-numeric:tabular-nums;color:var(--mu)">
            {% if c.gain_diff is not none %}{{c.gain_diff}} dB{% else %}—{% endif %}
          </td>
          <td>
            <span class="badge badge-{{c.status}}">
              {% if c.status == 'stable' %}● STABLE
              {% elif c.status == 'drifting' %}~ DRIFTING
              {% elif c.status == 'alert' %}⚠ ALERT
              {% else %}? UNKNOWN{% endif %}
            </span>
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>

{# ── Per-comparator cards ── #}
<div class="sec">
  <div class="sec-hdr">Comparator Detail</div>
  <div class="cards" id="comp-cards">
    {% for c in cards %}
    <div class="comp-card status-{{c.status}}" id="card-{{loop.index}}">
      <div class="comp-head" data-idx="{{loop.index}}">
        <div>
          <div class="comp-site">{{c.site}}</div>
          <div class="comp-pair" title="{{c.pre}} → {{c.post}}">{{c.pre}} → {{c.post}}</div>
        </div>
        <div>
          <div>
            <span class="comp-delay">{{c.delay_ms}}</span><span class="comp-delay-unit">ms</span>
          </div>
          {% if c.delta is not none %}
          <div class="comp-delta {% if c.delta > 200 %}delta-up{% elif c.delta < 50 %}delta-eq{% else %}delta-dn{% endif %}">
            {% if c.delta > 0 %}↑{% else %}↓{% endif %} {{c.delta}} ms from baseline
          </div>
          {% else %}
          <div class="comp-delta delta-eq">No baseline yet</div>
          {% endif %}
        </div>
        {% if c.gain_diff is not none %}
        <div class="comp-gain">Gain diff: {{c.gain_diff}} dB</div>
        {% endif %}
        <span class="badge badge-{{c.status}}">
          {% if c.status == 'stable' %}● STABLE
          {% elif c.status == 'drifting' %}~ DRIFTING
          {% elif c.status == 'alert' %}⚠ ALERT
          {% else %}—{% endif %}
        </span>
        <span class="expand-btn" id="exp-{{loop.index}}">▼</span>
      </div>
      <div class="comp-detail" id="det-{{loop.index}}">
        <div class="detail-sparkline">
          {{c.sparkline|safe}}
          <div style="font-size:9px;color:var(--mu);margin-top:3px">Last 24 h — dashed line = baseline</div>
        </div>
        <div class="detail-meta">
          <span>Baseline: <b>{% if c.baseline is not none %}{{c.baseline}} ms{% else %}Accumulating…{% endif %}</b></span>
          <span>Alert threshold: <b>{{c.threshold_ms}} ms</b></span>
          <span>Alerting: <b>{% if c.alerting %}enabled{% else %}disabled{% endif %}</b></span>
          {% if c.alert_ts %}
          <span style="color:var(--al)">Last alert: <b>{{c.alert_ts | int | string}}</b></span>
          {% endif %}
        </div>
      </div>
    </div>
    {% endfor %}
  </div>
</div>

{% endif %}

</div>{# .page #}

<script nonce="{{csp_nonce()}}">
(function(){
  // Expand/collapse detail panels
  document.getElementById('comp-cards') && document.getElementById('comp-cards').addEventListener('click', function(e){
    var head = e.target.closest('.comp-head');
    if(!head) return;
    var idx = head.dataset.idx;
    var det = document.getElementById('det-' + idx);
    var exp = document.getElementById('exp-' + idx);
    if(!det) return;
    var open = det.classList.toggle('open');
    if(exp) exp.classList.toggle('open', open);
  });

  // Auto-refresh every 30s
  function _updateTs(){
    var el = document.getElementById('refresh-ts');
    if(el){
      var d = new Date();
      el.textContent = 'Updated ' + d.toLocaleTimeString();
    }
  }
  _updateTs();
  setInterval(function(){ window.location.reload(); }, 30000);
})();
</script>
</body>
</html>"""


_LATENCY_SETTINGS_TPL = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<title>Latency Settings — SignalScope</title>
<style nonce="{{csp_nonce()}}">
:root{--bg:#0f1117;--bg2:#1a1d28;--bg3:#22263a;--bd:#2e3250;--tx:#e2e8f0;--mu:#94a3b8;--ok:#22c55e;--al:#ef4444;--wa:#f59e0b;--ac:#6366f1}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:var(--bg);color:var(--tx);font-size:13px;line-height:1.5}
.btn{display:inline-flex;align-items:center;gap:5px;padding:5px 14px;border-radius:7px;font-size:12px;font-weight:600;cursor:pointer;border:none;color:var(--tx);background:var(--bd);text-decoration:none;transition:filter .12s}
.btn:hover{filter:brightness(1.2)}.btn.bp{background:var(--ac);color:#fff}
.topbar{display:flex;align-items:center;gap:10px;padding:10px 20px;background:var(--bg2);border-bottom:1px solid var(--bd)}
.topbar-title{font-size:15px;font-weight:800}.topbar-right{margin-left:auto;display:flex;gap:8px}
.page{max-width:800px;margin:24px auto;padding:0 16px 40px}
.form-card{background:var(--bg2);border:1px solid var(--bd);border-radius:10px;padding:20px 24px;margin-bottom:16px}
.sec-hdr{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:var(--mu);margin-bottom:12px}
table{width:100%;border-collapse:collapse;font-size:12px}
th{background:var(--bg3);padding:7px 10px;text-align:left;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:var(--mu)}
td{padding:7px 10px;border-bottom:1px solid var(--bd)}
tr:last-child td{border-bottom:none}
input[type=number]{background:var(--bg3);border:1px solid var(--bd);border-radius:5px;color:var(--tx);padding:4px 8px;font-size:12px;width:100px}
input[type=number]:focus{outline:none;border-color:var(--ac)}
.saved-banner{background:rgba(34,197,94,.15);border:1px solid var(--ok);border-radius:7px;padding:8px 14px;margin-bottom:16px;font-size:12px;color:var(--ok)}
select{background:var(--bg3);border:1px solid var(--bd);border-radius:5px;color:var(--tx);padding:4px 8px;font-size:12px}
select:focus{outline:none;border-color:var(--ac)}
.no-data{color:var(--mu);font-style:italic;font-size:12px;margin-top:16px}
</style>
<link rel="icon" type="image/x-icon" href="/static/signalscope_icon.png">
</head>
<body>
<div class="topbar">
  <span class="topbar-title">⏱ Latency Settings</span>
  <div class="topbar-right">
    <a class="btn" href="/hub/latency">← Back</a>
    <a class="btn" href="/">⌂ Dashboard</a>
  </div>
</div>
<div class="page">
  {% if saved %}
  <div class="saved-banner">✓ Settings saved.</div>
  {% endif %}

  <form method="post" action="/hub/latency/settings">
    <input type="hidden" name="_csrf_token" value="{{csrf_token()}}">
    <div class="form-card">
      <div class="sec-hdr">Per-Comparator Alert Configuration</div>
      {% if pairs %}
      <table>
        <thead>
          <tr>
            <th>Site</th>
            <th>Pre Stream</th>
            <th>Post Stream</th>
            <th>Alert Threshold (ms)</th>
            <th>Alerting</th>
          </tr>
        </thead>
        <tbody>
          {% for p in pairs %}
          <input type="hidden" name="pk_{{loop.index0}}" value="{{p.pair_key}}">
          <tr>
            <td style="color:var(--mu)">{{p.site}}</td>
            <td>{{p.pre}}</td>
            <td>{{p.post}}</td>
            <td>
              <input type="number" name="threshold_{{loop.index0}}" value="{{p.threshold_ms | int}}"
                     min="10" max="60000" step="10">
            </td>
            <td>
              <select name="alerting_{{loop.index0}}">
                <option value="1" {% if p.alerting_enabled %}selected{% endif %}>Enabled</option>
                <option value="0" {% if not p.alerting_enabled %}selected{% endif %}>Disabled</option>
              </select>
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
      {% else %}
      <div class="no-data">No comparator pairs discovered yet. Settings will appear once sites report comparator data.</div>
      {% endif %}
    </div>
    {% if pairs %}
    <button type="submit" class="btn bp">Save Settings</button>
    {% endif %}
  </form>
</div>
</body>
</html>"""
