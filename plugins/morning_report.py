# morning_report.py — Daily Morning Briefing plugin for SignalScope
# Drop alongside signalscope.py; auto-discovered on next start.
# Hub-only.

SIGNALSCOPE_PLUGIN = {
    "id":       "morning_report",
    "label":    "Morning Report",
    "url":      "/hub/morning-report",
    "icon":     "📰",
    "hub_only": True,
    "version":  "1.1.0",
}

import os, json, time, threading, datetime, sqlite3, statistics
from collections import defaultdict

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_CFG_PATH    = os.path.join(_BASE_DIR, "morning_report_cfg.json")
_CACHE_PATH  = os.path.join(_BASE_DIR, "morning_report_cache.json")
_METRICS_DB  = os.path.join(_BASE_DIR, "metrics_history.db")
_ALERT_LOG   = os.path.join(_BASE_DIR, "alert_log.json")
_SLA_PATH    = os.path.join(_BASE_DIR, "sla_data.json")

_DEFAULT_CFG = {"report_time": "06:00"}

# Module-level cached report data and generation lock
_report_cache    = {}          # loaded from _CACHE_PATH at startup
_report_lock     = threading.Lock()
_scheduler_started = False


# ── Config helpers ─────────────────────────────────────────────────────────────

def _load_cfg() -> dict:
    try:
        if os.path.exists(_CFG_PATH):
            with open(_CFG_PATH) as f:
                return {**_DEFAULT_CFG, **json.load(f)}
    except Exception:
        pass
    return dict(_DEFAULT_CFG)


def _save_cfg(cfg: dict):
    try:
        with open(_CFG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


# ── Alert log helpers ──────────────────────────────────────────────────────────

def _load_alert_events(days: int = 30) -> list:
    """Load up to 'days' days of events from alert_log.json, oldest-first."""
    if not os.path.exists(_ALERT_LOG):
        return []
    cutoff_ts = time.time() - days * 86400
    events = []
    try:
        with open(_ALERT_LOG, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                # parse ts field "YYYY-MM-DD HH:MM:SS"
                ts_str = ev.get("ts", "")
                try:
                    dt = datetime.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                    ev["_ts_epoch"] = dt.timestamp()
                except Exception:
                    ev["_ts_epoch"] = 0
                if ev["_ts_epoch"] >= cutoff_ts:
                    events.append(ev)
            except Exception:
                pass
    except Exception:
        pass
    # sort oldest first
    events.sort(key=lambda e: e.get("_ts_epoch", 0))
    return events


# ── SLA helpers ────────────────────────────────────────────────────────────────

def _load_sla() -> dict:
    try:
        if os.path.exists(_SLA_PATH):
            with open(_SLA_PATH) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


# ── Metrics DB helpers ─────────────────────────────────────────────────────────

def _query_chain_faults(day_start: float, day_end: float) -> list:
    """Return chain_fault_log rows for the given epoch window."""
    if not os.path.exists(_METRICS_DB):
        return []
    try:
        with sqlite3.connect(_METRICS_DB, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                """SELECT chain_name, fault_ts, recovery_ts, duration_s, fault_node, site
                   FROM chain_fault_log
                   WHERE fault_ts >= ? AND fault_ts < ?
                   ORDER BY fault_ts""",
                (day_start, day_end)
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


def _query_chain_faults_range(start: float, end: float) -> list:
    if not os.path.exists(_METRICS_DB):
        return []
    try:
        with sqlite3.connect(_METRICS_DB, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                """SELECT chain_name, fault_ts, recovery_ts, duration_s, fault_node, site
                   FROM chain_fault_log
                   WHERE fault_ts >= ? AND fault_ts < ?
                   ORDER BY fault_ts""",
                (start, end)
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


def _query_all_chains() -> list:
    """Return all distinct chain_names from the fault log."""
    if not os.path.exists(_METRICS_DB):
        return []
    try:
        with sqlite3.connect(_METRICS_DB, timeout=10) as conn:
            cur = conn.execute("SELECT DISTINCT chain_name FROM chain_fault_log ORDER BY chain_name")
            return [r[0] for r in cur.fetchall()]
    except Exception:
        return []


# ── Report generation ──────────────────────────────────────────────────────────

def _generate_report(hub_server, monitor) -> dict:
    """Build the full morning report dict for yesterday."""
    now   = datetime.datetime.now()
    yesterday = now.date() - datetime.timedelta(days=1)
    ystart = datetime.datetime.combine(yesterday, datetime.time(0, 0, 0))
    yend   = datetime.datetime.combine(yesterday, datetime.time(23, 59, 59, 999999))
    ystart_e = ystart.timestamp()
    yend_e   = yend.timestamp()

    # Load data sources
    all_events = _load_alert_events(days=30)
    sla_data   = _load_sla()

    # Yesterday's events only (from alert_log)
    y_events = [e for e in all_events if ystart_e <= e.get("_ts_epoch", 0) <= yend_e]

    # Last 30 days events split by (stream, day)
    faults_by_stream_day = defaultdict(lambda: defaultdict(int))
    for ev in all_events:
        ep = ev.get("_ts_epoch", 0)
        if ep > 0:
            d = datetime.datetime.fromtimestamp(ep).date()
            s = ev.get("stream") or ev.get("chain_name") or ""
            if s:
                faults_by_stream_day[s][d] += 1

    # Yesterday's fault events grouped by chain/stream
    fault_events_y = [e for e in y_events if e.get("type") in (
        "CHAIN_FAULT", "SILENCE", "STUDIO_FAULT", "STL_FAULT",
        "TX_DOWN", "DAB_AUDIO_FAULT", "RTP_FAULT", "RTP_LOSS", "AI_ALERT"
    )]

    # Chain fault log from SQLite for yesterday
    chain_faults_y = _query_chain_faults(ystart_e, yend_e)
    # Last 30 days chain faults (for averages)
    chain_faults_30 = _query_chain_faults_range(time.time() - 30 * 86400, ystart_e)

    all_chains_db = _query_all_chains()

    # ── Live chain names from monitor config (catches chains with no fault history) ──
    live_chain_names = set()
    if monitor is not None:
        try:
            cfg_ss = monitor.app_cfg
            for c in (cfg_ss.signal_chains or []) if cfg_ss else []:
                n = (c.get("name") or "").strip()
                if n:
                    live_chain_names.add(n)
        except Exception:
            pass

    # ── At a glance ────────────────────────────────────────────────────────────
    total_faults = len(fault_events_y) + len(chain_faults_y)

    # Downtime by chain (from chain_fault_log duration_s)
    downtime_by_chain = defaultdict(float)
    for row in chain_faults_y:
        dur = row.get("duration_s") or 0
        downtime_by_chain[row["chain_name"]] += dur

    total_downtime_s = sum(downtime_by_chain.values())
    total_downtime_min = round(total_downtime_s / 60, 1)

    # Chains monitored = union of chains from DB + alert log
    chain_names_alert = set(
        (e.get("stream") or "") for e in fault_events_y if e.get("stream")
    )
    chain_names_db = set(all_chains_db)
    # Also pull from 30-day history to get all chains seen recently
    chain_names_30 = set(r["chain_name"] for r in chain_faults_30)
    # Include ALL live-configured chains so chains with no fault history still appear
    all_chain_names = chain_names_db | chain_names_30 | chain_names_alert | live_chain_names

    # Faults per chain yesterday (combine alert log + chain_fault_log)
    faults_per_chain_y = defaultdict(int)
    for row in chain_faults_y:
        faults_per_chain_y[row["chain_name"]] += 1
    for ev in fault_events_y:
        s = ev.get("stream") or ""
        if s and s not in faults_per_chain_y:
            faults_per_chain_y[s] += 1

    total_chains = max(len(all_chain_names), 1)

    if faults_per_chain_y:
        cleanest = min(faults_per_chain_y, key=faults_per_chain_y.get)
        worst    = max(faults_per_chain_y, key=faults_per_chain_y.get)
    else:
        cleanest = None
        worst    = None

    # Plain-English headline for non-technical users
    _chains_with_faults = len([c for c in all_chain_names if faults_per_chain_y.get(c, 0) > 0])
    if total_faults == 0:
        headline = f"✅ Clean day — all {total_chains} audio chain{'s' if total_chains != 1 else ''} ran without interruption yesterday."
        headline_color = "ok"
    elif _chains_with_faults == 1:
        headline = (f"⚠️ {total_faults} audio interruption{'s' if total_faults != 1 else ''} "
                    f"detected on 1 chain yesterday"
                    + (f" — {total_downtime_min} min off-air." if total_downtime_min > 0 else "."))
        headline_color = "wn"
    else:
        headline = (f"🔴 {total_faults} audio interruption{'s' if total_faults != 1 else ''} "
                    f"across {_chains_with_faults} chains yesterday"
                    + (f" — {total_downtime_min} min total off-air time." if total_downtime_min > 0 else "."))
        headline_color = "al"

    at_a_glance = {
        "total_faults":        total_faults,
        "total_chains":        total_chains,
        "total_downtime_min":  total_downtime_min,
        "cleanest_chain":      cleanest,
        "worst_chain":         worst,
        "worst_chain_faults":  faults_per_chain_y.get(worst, 0) if worst else 0,
    }

    # ── Chain health table ──────────────────────────────────────────────────────
    # Build 7-day fault counts per chain (the 7 days before yesterday)
    seven_days_ago = yesterday - datetime.timedelta(days=7)
    chain_health_rows = []

    for chain in sorted(all_chain_names):
        fault_count_y = faults_per_chain_y.get(chain, 0)

        # Downtime yesterday from chain_fault_log
        dt_y = downtime_by_chain.get(chain, 0.0)

        # SLA yesterday — look up in sla_data (keyed by stream name → month data)
        sla_y = None
        if chain in sla_data:
            month_key = yesterday.strftime("%Y-%m")
            month_data = sla_data[chain].get(month_key)
            if month_data:
                monitored = month_data.get("monitored_s", 0)
                alert_s   = month_data.get("alert_s", 0)
                if monitored > 0:
                    sla_y = round(100.0 * (1.0 - alert_s / monitored), 2)

        # 7-day average faults/day
        past7_counts = []
        for i in range(1, 8):
            d = yesterday - datetime.timedelta(days=i)
            cnt = faults_by_stream_day.get(chain, {}).get(d, 0)
            past7_counts.append(cnt)
        avg7 = round(statistics.mean(past7_counts), 2) if past7_counts else 0.0

        # Trend: compare yesterday vs avg7
        if fault_count_y > avg7 * 1.3 and fault_count_y > 0:
            trend = "↑"
            trend_label = "Worse than usual"
            trend_color = "al"
        elif fault_count_y < avg7 * 0.7 and avg7 > 0:
            trend = "↓"
            trend_label = "Better than usual"
            trend_color = "ok"
        else:
            trend = "→"
            trend_label = "Normal"
            trend_color = "mu"

        # Longest single outage
        longest_s = 0.0
        for row in chain_faults_y:
            if row["chain_name"] == chain:
                dur = row.get("duration_s") or 0
                if dur > longest_s:
                    longest_s = dur

        # Human-readable longest outage
        if longest_s >= 3600:
            _lh = int(longest_s // 3600)
            _lm = int((longest_s % 3600) // 60)
            longest_hms = f"{_lh}h {_lm}m"
        elif longest_s >= 60:
            _lm = int(longest_s // 60)
            _ls = int(longest_s % 60)
            longest_hms = f"{_lm}m {_ls}s" if _ls else f"{_lm} min"
        elif longest_s > 0:
            longest_hms = f"{int(longest_s)}s"
        else:
            longest_hms = ""

        # Uptime percentage (based on 24-hour day)
        day_s = 86400.0
        uptime_pct = round(100.0 * max(0.0, day_s - dt_y) / day_s, 2)

        chain_health_rows.append({
            "name":         chain,
            "faults_y":     fault_count_y,
            "downtime_min": round(dt_y / 60, 1),
            "uptime_pct":   uptime_pct,
            "sla_y":        sla_y,
            "avg7":         avg7,
            "trend":        trend,
            "trend_label":  trend_label,
            "trend_color":  trend_color,
            "longest_min":  round(longest_s / 60, 1),
            "longest_hms":  longest_hms,
        })

    # ── Hourly heatmap ─────────────────────────────────────────────────────────
    hourly_counts = [0] * 24
    for row in chain_faults_y:
        ft = row.get("fault_ts", 0)
        if ft:
            h = datetime.datetime.fromtimestamp(ft).hour
            hourly_counts[h] += 1
    for ev in fault_events_y:
        ep = ev.get("_ts_epoch", 0)
        if ep:
            h = datetime.datetime.fromtimestamp(ep).hour
            hourly_counts[h] += 1

    # ── Notable patterns ───────────────────────────────────────────────────────
    patterns = []

    # Consecutive clean days (clean streak broken)
    for chain in all_chain_names:
        if faults_per_chain_y.get(chain, 0) == 0:
            continue
        streak = 0
        for i in range(1, 30):
            d = yesterday - datetime.timedelta(days=i)
            if faults_by_stream_day.get(chain, {}).get(d, 0) == 0:
                streak += 1
            else:
                break
        if streak >= 3:
            patterns.append({
                "type":  "streak_broken",
                "text":  f"⚠️ {chain} — clean run ended after {streak} fault-free days in a row",
                "color": "amber",
            })

    # Chains with faults > 2× their 7-day average
    for row in chain_health_rows:
        avg7 = row["avg7"]
        fy   = row["faults_y"]
        if avg7 > 0 and fy >= 2 and fy > 2 * avg7:
            ratio = round(fy / avg7, 1)
            patterns.append({
                "type":  "above_average",
                "text":  f"🔴 {row['name']} had {fy} interruptions yesterday — {ratio}× more than its usual {avg7}/day average",
                "color": "red",
            })

    # Fault clustering in a 3-hour window
    chain_hour_faults = defaultdict(lambda: defaultdict(int))
    for row in chain_faults_y:
        ft = row.get("fault_ts", 0)
        if ft:
            h = datetime.datetime.fromtimestamp(ft).hour
            chain_hour_faults[row["chain_name"]][h] += 1
    for ev in fault_events_y:
        ep = ev.get("_ts_epoch", 0)
        s  = ev.get("stream") or ""
        if ep and s:
            h = datetime.datetime.fromtimestamp(ep).hour
            chain_hour_faults[s][h] += 1

    for chain, hour_map in chain_hour_faults.items():
        total_chain_faults = sum(hour_map.values())
        if total_chain_faults < 3:
            continue
        for h_start in range(24):
            window_count = sum(
                hour_map.get((h_start + dh) % 24, 0) for dh in range(3)
            )
            if window_count >= 3 and window_count > 0.6 * total_chain_faults:
                h_end = (h_start + 3) % 24
                patterns.append({
                    "type":  "clustering",
                    "text":  (f"🕐 {chain} — {window_count} of {total_chain_faults} interruptions "
                              f"clustered between {h_start:02d}:00\u2013{h_end:02d}:00 (possible recurring issue)"),
                    "color": "amber",
                })
                break

    # Recurring same-hour faults (same hour faulted on 3+ of past 7 days)
    for chain, hour_map in chain_hour_faults.items():
        for h, cnt in hour_map.items():
            if cnt == 0:
                continue
            # Check how many of the past 7 days had faults in h ±1 window
            days_with_fault = 0
            for i in range(1, 8):
                d = yesterday - datetime.timedelta(days=i)
                d_start = datetime.datetime.combine(d, datetime.time(0)).timestamp()
                d_end   = d_start + 86400
                d_faults = _query_chain_faults(d_start, d_end)
                for row in d_faults:
                    if row["chain_name"] == chain:
                        fh = datetime.datetime.fromtimestamp(row["fault_ts"]).hour
                        if abs(fh - h) <= 1 or (h == 0 and fh == 23) or (h == 23 and fh == 0):
                            days_with_fault += 1
                            break
            if days_with_fault >= 3:
                patterns.append({
                    "type":  "recurring",
                    "text":  (f"🔁 {chain} has had audio issues around {h:02d}:00 on {days_with_fault} "
                              f"of the last 7 days \u2014 worth investigating"),
                    "color": "blue",
                })

    # Chains with 0 faults all day
    clean_chains = [c for c in all_chain_names if faults_per_chain_y.get(c, 0) == 0]
    for c in clean_chains:
        patterns.append({"type": "clean_day", "text": f"✅ {c} — no interruptions all day", "color": "green"})

    # Overnight clean (00:00-06:00)
    overnight_faults = sum(hourly_counts[0:6])
    if overnight_faults == 0 and total_faults > 0:
        patterns.append({
            "type":  "overnight_clean",
            "text":  "✅ Overnight window (midnight–6 AM) was fault-free",
            "color": "green",
        })

    # Deduplicate patterns (same text)
    seen_texts = set()
    deduped = []
    for p in patterns:
        if p["text"] not in seen_texts:
            seen_texts.add(p["text"])
            deduped.append(p)
    patterns = deduped

    # ── Stream quality summary ─────────────────────────────────────────────────
    stream_quality = []
    if hub_server is not None:
        try:
            sites = hub_server.get_sites()
            for site in sites:
                for st in site.get("streams", []):
                    name = st.get("name", "?")
                    silence_count = sum(
                        1 for e in y_events
                        if e.get("stream") == name and e.get("type") in (
                            "SILENCE", "STUDIO_FAULT", "STL_FAULT", "TX_DOWN"
                        )
                    )
                    stream_quality.append({
                        "name":          name,
                        "site":          site.get("site", "?"),
                        "glitch_count":  st.get("glitch_count", 0),
                        "lufs_i":        st.get("lufs_i"),
                        "rtp_loss_pct":  st.get("rtp_loss_pct"),
                        "silence_count": silence_count,
                    })
        except Exception:
            pass

    # ── Assemble report ────────────────────────────────────────────────────────
    cfg = _load_cfg()
    report_h, report_m = _parse_time(cfg.get("report_time", "06:00"))
    next_gen = _next_run_dt(report_h, report_m)

    report = {
        "generated_ts":    time.strftime("%Y-%m-%d %H:%M:%S"),
        "covers_date":     yesterday.strftime("%Y-%m-%d"),
        "covers_label":    yesterday.strftime("%A, %-d %B %Y"),
        "next_gen":        next_gen.strftime("%Y-%m-%d %H:%M"),
        "headline":        headline,
        "headline_color":  headline_color,
        "at_a_glance":     at_a_glance,
        "chain_health":    chain_health_rows,
        "hourly_counts":   hourly_counts,
        "patterns":        patterns,
        "stream_quality":  stream_quality,
    }
    return report


# ── Scheduler helpers ──────────────────────────────────────────────────────────

def _parse_time(t: str):
    """Parse 'HH:MM' to (hour, minute) ints."""
    try:
        parts = t.strip().split(":")
        return int(parts[0]), int(parts[1])
    except Exception:
        return 6, 0


def _next_run_dt(hour: int, minute: int) -> datetime.datetime:
    """Return the next datetime when hour:minute occurs."""
    now = datetime.datetime.now()
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += datetime.timedelta(days=1)
    return candidate


def _scheduler_loop(hub_server, monitor):
    """Background thread: sleep until report_time, generate, repeat."""
    while True:
        cfg = _load_cfg()
        h, m = _parse_time(cfg.get("report_time", "06:00"))
        next_dt = _next_run_dt(h, m)
        sleep_s = (next_dt - datetime.datetime.now()).total_seconds()
        if sleep_s > 0:
            # Sleep in 60-second chunks so config changes take effect promptly
            slept = 0.0
            while slept < sleep_s:
                chunk = min(60.0, sleep_s - slept)
                time.sleep(chunk)
                slept += chunk
                # Re-read config in case report_time changed
                cfg2 = _load_cfg()
                h2, m2 = _parse_time(cfg2.get("report_time", "06:00"))
                if (h2, m2) != (h, m):
                    break  # reschedule immediately
        try:
            report = _generate_report(hub_server, monitor)
            with _report_lock:
                global _report_cache
                _report_cache = report
            try:
                with open(_CACHE_PATH, "w", encoding="utf-8") as f:
                    json.dump(report, f, indent=2)
            except Exception:
                pass
            monitor.log(f"[MorningReport] Daily report generated for {report.get('covers_date','?')}")
        except Exception as e:
            monitor.log(f"[MorningReport] Generation error: {e}")
        # Small sleep to avoid double-fire on exact boundary
        time.sleep(5)


# ── Register ───────────────────────────────────────────────────────────────────

def register(app, ctx):
    global _report_cache, _scheduler_started

    login_required = ctx["login_required"]
    csrf_protect   = ctx["csrf_protect"]
    monitor        = ctx["monitor"]
    hub_server     = ctx.get("hub_server")
    BUILD          = ctx["BUILD"]

    from flask import render_template_string, request, jsonify, redirect, url_for

    # Load cached report from disk on startup
    with _report_lock:
        if os.path.exists(_CACHE_PATH):
            try:
                with open(_CACHE_PATH) as f:
                    _report_cache = json.load(f)
            except Exception:
                pass

    # Start scheduler thread once
    if not _scheduler_started:
        _scheduler_started = True
        t = threading.Thread(
            target=_scheduler_loop,
            args=(hub_server, monitor),
            daemon=True,
            name="MorningReportScheduler",
        )
        t.start()

    # ── Routes ─────────────────────────────────────────────────────────────────

    @app.get("/hub/morning-report")
    @login_required
    def morning_report_page():
        with _report_lock:
            report = dict(_report_cache)
        return render_template_string(_REPORT_TPL, report=report, build=BUILD)

    @app.post("/api/morning-report/generate")
    @login_required
    @csrf_protect
    def morning_report_generate():
        try:
            rpt = _generate_report(hub_server, monitor)
            with _report_lock:
                global _report_cache
                _report_cache = rpt
            try:
                with open(_CACHE_PATH, "w", encoding="utf-8") as f:
                    json.dump(rpt, f, indent=2)
            except Exception:
                pass
            monitor.log("[MorningReport] Manual regeneration triggered")
            return jsonify({"ok": True, "covers_date": rpt.get("covers_date", "")})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.get("/hub/morning-report/settings")
    @login_required
    def morning_report_settings_page():
        cfg = _load_cfg()
        return render_template_string(_SETTINGS_TPL, cfg=cfg, build=BUILD, saved=False)

    @app.post("/hub/morning-report/settings")
    @login_required
    @csrf_protect
    def morning_report_settings_save():
        cfg = _load_cfg()
        rt = request.form.get("report_time", "06:00").strip()
        # Validate HH:MM format
        try:
            h, m = _parse_time(rt)
            assert 0 <= h <= 23 and 0 <= m <= 59
        except Exception:
            rt = "06:00"
        cfg["report_time"] = rt
        _save_cfg(cfg)
        monitor.log(f"[MorningReport] Report time set to {rt}")
        return render_template_string(_SETTINGS_TPL, cfg=cfg, build=BUILD, saved=True)


# ── Templates ──────────────────────────────────────────────────────────────────

_REPORT_TPL = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<title>Morning Report — SignalScope</title>
<style nonce="{{csp_nonce()}}">
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:var(--bg);color:var(--tx);font-size:13px;line-height:1.5}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
.btn{display:inline-flex;align-items:center;gap:5px;padding:5px 14px;border-radius:7px;font-size:12px;font-weight:600;cursor:pointer;border:none;color:var(--tx);background:var(--bor);text-decoration:none;transition:filter .12s}
.btn:hover{filter:brightness(1.2)}.btn.bp{background:var(--acc);color:#fff}
/* Topbar */
.topbar{display:flex;align-items:center;gap:10px;padding:10px 20px;background:var(--sur);border-bottom:1px solid var(--bor);flex-wrap:wrap}
.topbar-title{font-size:16px;font-weight:700;letter-spacing:.01em}
.topbar-right{margin-left:auto;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
/* Page layout */
.page{max-width:1100px;margin:0 auto;padding:20px 16px 40px}
/* Section */
.sec{margin-bottom:28px}
.sec-hdr{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:var(--mu);margin-bottom:12px;padding-bottom:6px;border-bottom:1px solid var(--bor)}
/* At-a-glance cards */
.aag-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px}
.aag-card{background:var(--sur);border:1px solid var(--bor);border-radius:10px;padding:14px 16px}
.aag-val{font-size:26px;font-weight:800;font-variant-numeric:tabular-nums;line-height:1.1}
.aag-lbl{font-size:11px;color:var(--mu);margin-top:3px}
/* Chain health table */
.tbl-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:12px}
th{background:rgba(0,0,0,.2);padding:7px 10px;text-align:left;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:var(--mu);white-space:nowrap}
td{padding:6px 10px;border-bottom:1px solid var(--bor);vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:rgba(255,255,255,.03)}
.trend-up{color:var(--al)}.trend-dn{color:var(--ok)}.trend-eq{color:var(--mu)}
.sla-ok{color:var(--ok)}.sla-bad{color:var(--al)}.sla-na{color:var(--mu)}
/* Heatmap */
.heatmap{display:grid;grid-template-columns:repeat(24,1fr);gap:3px}
.hm-cell{height:32px;border-radius:4px;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:600;color:rgba(255,255,255,.7);cursor:default}
.hm-cell[data-c="0"]{background:var(--bor)}
.hm-cell[data-c="1"]{background:rgba(23,168,255,.3)}
.hm-cell[data-c="2"]{background:rgba(23,168,255,.55)}
.hm-cell[data-c="3"]{background:rgba(239,68,68,.55)}
.hm-labels{display:grid;grid-template-columns:repeat(24,1fr);gap:3px;margin-top:3px}
.hm-lbl{font-size:9px;color:var(--mu);text-align:center}
/* Patterns */
.pattern-list{display:flex;flex-direction:column;gap:8px}
.pattern-item{background:var(--sur);border-radius:8px;padding:10px 14px;border-left:3px solid var(--bor);font-size:12px}
.pattern-item.color-red{border-left-color:var(--al)}
.pattern-item.color-amber{border-left-color:var(--wn)}
.pattern-item.color-blue{border-left-color:var(--acc)}
.pattern-item.color-green{border-left-color:var(--ok)}
/* Stream quality */
.sq-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:10px}
.sq-card{background:var(--sur);border:1px solid var(--bor);border-radius:8px;padding:12px 14px}
.sq-name{font-weight:700;margin-bottom:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sq-site{font-size:10px;color:var(--mu);margin-bottom:8px}
.sq-row{display:flex;justify-content:space-between;font-size:11px;color:var(--mu);padding:2px 0;border-bottom:1px solid rgba(255,255,255,.04)}
.sq-row:last-child{border-bottom:none}
.sq-row span:last-child{color:var(--tx);font-variant-numeric:tabular-nums}
/* Footer */
.rpt-footer{margin-top:30px;padding-top:16px;border-top:1px solid var(--bor);font-size:11px;color:var(--mu);display:flex;flex-wrap:wrap;gap:12px}
/* No data */
.no-data{color:var(--mu);font-style:italic;font-size:12px}
/* Toast */
#toast{position:fixed;bottom:24px;right:24px;background:var(--ok);color:#fff;padding:9px 18px;border-radius:8px;font-size:13px;font-weight:600;display:none;z-index:999}
/* Headline banner */
.headline{border-radius:10px;padding:14px 18px;font-size:14px;font-weight:600;margin-bottom:20px;line-height:1.4}
.headline.hl-ok{background:rgba(34,197,94,.12);border:1px solid var(--ok);color:var(--ok)}
.headline.hl-wn{background:rgba(245,158,11,.1);border:1px solid var(--wn);color:var(--wn)}
.headline.hl-al{background:rgba(239,68,68,.1);border:1px solid var(--al);color:var(--al)}
/* Trend pill */
.trend-pill{display:inline-block;border-radius:12px;padding:2px 8px;font-size:10px;font-weight:700}
.trend-ok{background:rgba(34,197,94,.15);color:var(--ok)}
.trend-al{background:rgba(239,68,68,.15);color:var(--al)}
.trend-wn{background:rgba(245,158,11,.15);color:var(--wn)}
.trend-mu{background:rgba(138,164,200,.12);color:var(--mu)}
/* Uptime bar */
.upt-bar{display:flex;align-items:center;gap:6px;font-variant-numeric:tabular-nums}
.upt-pct{font-weight:700;font-size:12px}
</style>
<link rel="icon" type="image/x-icon" href="/static/signalscope_icon.png">
</head>
<body>

<div class="topbar">
  <span class="topbar-title">📰 Morning Report</span>
  {% if report %}
  <span style="font-size:11px;color:var(--mu)">Covering: <b style="color:var(--tx)">{{report.covers_label}}</b></span>
  {% endif %}
  <div class="topbar-right">
    <a class="btn" href="/hub/morning-report/settings">⚙ Settings</a>
    <button class="btn bp" id="btn-regen">↻ Regenerate</button>
    <a class="btn" href="/">⌂ Dashboard</a>
  </div>
</div>

<div class="page">

{% if not report %}
<div class="no-data" style="margin-top:40px;text-align:center">
  No report generated yet. Click <b>Regenerate</b> to build the first report.
</div>
{% else %}

{# ── Headline banner ── #}
<div class="headline hl-{{report.headline_color}}">{{report.headline}}</div>

{# ── At a Glance ── #}
<div class="sec">
  <div class="sec-hdr">Yesterday at a Glance — {{report.covers_label}}</div>
  <div class="aag-grid">
    <div class="aag-card">
      <div class="aag-val" style="color:{% if report.at_a_glance.total_faults > 0 %}var(--al){% else %}var(--ok){% endif %}">
        {{report.at_a_glance.total_faults}}
      </div>
      <div class="aag-lbl">Audio Interruptions</div>
    </div>
    <div class="aag-card">
      <div class="aag-val">{{report.at_a_glance.total_chains}}</div>
      <div class="aag-lbl">Audio Chains Monitored</div>
    </div>
    <div class="aag-card">
      <div class="aag-val" style="color:{% if report.at_a_glance.total_downtime_min > 0 %}var(--wn){% else %}var(--ok){% endif %}">
        {{report.at_a_glance.total_downtime_min}}
      </div>
      <div class="aag-lbl">Minutes Off-Air (total)</div>
    </div>
    {% if report.at_a_glance.cleanest_chain %}
    <div class="aag-card">
      <div class="aag-val" style="font-size:15px;color:var(--ok)">{{report.at_a_glance.cleanest_chain}}</div>
      <div class="aag-lbl">Best Performing Chain</div>
    </div>
    {% endif %}
    {% if report.at_a_glance.worst_chain %}
    <div class="aag-card">
      <div class="aag-val" style="font-size:15px;color:var(--al)">{{report.at_a_glance.worst_chain}}</div>
      <div class="aag-lbl">Most Issues ({{report.at_a_glance.worst_chain_faults}} interruption{% if report.at_a_glance.worst_chain_faults != 1 %}s{% endif %})</div>
    </div>
    {% endif %}
  </div>
</div>

{# ── Chain Health Table ── #}
<div class="sec">
  <div class="sec-hdr">Audio Chain Health</div>
  <p style="font-size:11px;color:var(--mu);margin-bottom:10px">Each row is one audio path monitored by SignalScope. <b style="color:var(--tx)">On-Air %</b> = percentage of yesterday the chain was transmitting normally.</p>
  {% if report.chain_health %}
  <div class="tbl-wrap">
    <table>
      <thead>
        <tr>
          <th>Chain Name</th>
          <th>Interruptions</th>
          <th>Time Off-Air</th>
          <th>On-Air %</th>
          <th>Usual Daily Avg</th>
          <th>Compared to Usual</th>
          <th>Longest Gap</th>
        </tr>
      </thead>
      <tbody>
        {% for row in report.chain_health %}
        <tr>
          <td><b>{{row.name}}</b></td>
          <td>
            {% if row.faults_y == 0 %}
              <span style="color:var(--ok)">✓ None</span>
            {% else %}
              <span style="color:var(--al);font-weight:700">{{row.faults_y}}</span>
            {% endif %}
          </td>
          <td style="font-variant-numeric:tabular-nums">
            {% if row.downtime_min > 0 %}
              <span style="color:var(--wn)">{{row.downtime_min}} min</span>
            {% else %}<span style="color:var(--ok)">—</span>{% endif %}
          </td>
          <td>
            <span class="{% if row.uptime_pct >= 99.9 %}sla-ok{% elif row.uptime_pct >= 99.0 %}sla-na{% else %}sla-bad{% endif %}" title="{{row.uptime_pct}}% of the day on-air">
              {{row.uptime_pct}}%
            </span>
          </td>
          <td style="font-variant-numeric:tabular-nums;color:var(--mu)">
            {% if row.avg7 > 0 %}{{row.avg7}}/day{% else %}<span style="color:var(--ok)">No history</span>{% endif %}
          </td>
          <td>
            <span class="trend-pill trend-{{row.trend_color}}">{{row.trend_label}}</span>
          </td>
          <td style="font-variant-numeric:tabular-nums">
            {% if row.longest_hms %}
              <span style="color:var(--wn)">{{row.longest_hms}}</span>
            {% else %}<span style="color:var(--ok)">—</span>{% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% else %}
  <div class="no-data">No audio chains found.</div>
  {% endif %}
</div>

{# ── Fault Timeline Heatmap ── #}
<div class="sec">
  <div class="sec-hdr">Audio Interruptions by Hour of Day</div>
  <p style="font-size:11px;color:var(--mu);margin-bottom:10px">Each block = one hour. Darker red = more interruptions in that hour.</p>
  <div class="heatmap">
    {% for h in range(24) %}
    {% set cnt = report.hourly_counts[h] %}
    {% set c = [0, cnt if cnt <= 3 else 3] | max %}
    <div class="hm-cell" data-c="{{c}}" title="{{ "%02d" % h }}:00 — {{cnt}} fault{% if cnt != 1 %}s{% endif %}">
      {% if cnt > 0 %}{{cnt}}{% endif %}
    </div>
    {% endfor %}
  </div>
  <div class="hm-labels">
    {% for h in range(24) %}
    <div class="hm-lbl">{{ "%02d" % h }}</div>
    {% endfor %}
  </div>
  <div style="margin-top:8px;display:flex;gap:14px;font-size:10px;color:var(--mu);flex-wrap:wrap">
    <span><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:var(--bg3);vertical-align:middle"></span> 0</span>
    <span><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:rgba(99,102,241,.3);vertical-align:middle"></span> 1</span>
    <span><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:rgba(99,102,241,.5);vertical-align:middle"></span> 2</span>
    <span><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:rgba(239,68,68,.5);vertical-align:middle"></span> 3+</span>
  </div>
</div>

{# ── Notable Patterns ── #}
{% if report.patterns %}
<div class="sec">
  <div class="sec-hdr">What Stood Out Yesterday</div>
  <div class="pattern-list">
    {% for p in report.patterns %}
    <div class="pattern-item color-{{p.color}}">{{p.text}}</div>
    {% endfor %}
  </div>
</div>
{% endif %}

{# ── Stream Quality Summary ── #}
{% if report.stream_quality %}
<div class="sec">
  <div class="sec-hdr">Live Stream Quality</div>
  <p style="font-size:11px;color:var(--mu);margin-bottom:10px"><b style="color:var(--tx)">Loudness</b> = how loud the audio sounds on average (target: −14 to −23 LUFS). <b style="color:var(--tx)">Packet Loss</b> = network dropouts for IP streams (0% is ideal). <b style="color:var(--tx)">Silence Events</b> = times the stream went silent.</p>
  <div class="sq-grid">
    {% for st in report.stream_quality %}
    <div class="sq-card">
      <div class="sq-name" title="{{st.name}}">{{st.name}}</div>
      <div class="sq-site">{{st.site}}</div>
      <div class="sq-row"><span>Audio glitches</span><span>{% if st.glitch_count == 0 %}<span style="color:var(--ok)">None</span>{% else %}<span style="color:var(--wn)">{{st.glitch_count}}</span>{% endif %}</span></div>
      <div class="sq-row"><span>Loudness level</span>
        <span>{% if st.lufs_i is not none and st.lufs_i > -70 %}{{st.lufs_i | round(1)}} LUFS{% else %}<span style="color:var(--mu)">—</span>{% endif %}</span>
      </div>
      <div class="sq-row"><span>Network packet loss</span>
        <span>{% if st.rtp_loss_pct is not none %}{%if st.rtp_loss_pct < 0.1 %}<span style="color:var(--ok)">{{st.rtp_loss_pct | round(2)}}%</span>{% else %}<span style="color:var(--wn)">{{st.rtp_loss_pct | round(2)}}%</span>{% endif %}{% else %}<span style="color:var(--mu)">—</span>{% endif %}</span>
      </div>
      <div class="sq-row"><span>Silence events</span><span>{% if st.silence_count == 0 %}<span style="color:var(--ok)">None</span>{% else %}<span style="color:var(--al)">{{st.silence_count}}</span>{% endif %}</span></div>
    </div>
    {% endfor %}
  </div>
</div>
{% endif %}

{# ── Footer ── #}
<div class="rpt-footer">
  <span>Generated: <b>{{report.generated_ts}}</b></span>
  <span>Covers: <b>{{report.covers_date}}</b></span>
  <span>Next generation: <b>{{report.next_gen}}</b></span>
  <span style="margin-left:auto;color:rgba(148,163,184,.4)">{{build}}</span>
</div>

{% endif %}

</div>{# .page #}

<div id="toast">Report regenerated</div>

<script nonce="{{csp_nonce()}}">
(function(){
  function _getCsrf(){
    return (document.querySelector('meta[name="csrf-token"]')||{}).content
        || (document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/)||[])[1]
        || '';
  }

  document.getElementById('btn-regen').addEventListener('click', function(){
    var btn = this;
    btn.disabled = true;
    btn.textContent = '…';
    fetch('/api/morning-report/generate', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {'X-CSRFToken': _getCsrf(), 'Content-Type': 'application/json'},
      body: JSON.stringify({})
    }).then(function(r){ return r.json(); })
      .then(function(d){
        btn.disabled = false;
        btn.textContent = '↻ Regenerate';
        if(d.ok){
          var t = document.getElementById('toast');
          t.style.display = 'block';
          setTimeout(function(){ t.style.display='none'; window.location.reload(); }, 1200);
        } else {
          alert('Error: ' + (d.error || 'unknown'));
        }
      })
      .catch(function(e){
        btn.disabled = false;
        btn.textContent = '↻ Regenerate';
        alert('Request failed: ' + e);
      });
  });
})();
</script>
</body>
</html>"""


_SETTINGS_TPL = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<title>Morning Report Settings — SignalScope</title>
<style nonce="{{csp_nonce()}}">
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:var(--bg);color:var(--tx);font-size:13px}
.btn{display:inline-flex;align-items:center;gap:5px;padding:5px 14px;border-radius:7px;font-size:12px;font-weight:600;cursor:pointer;border:none;color:var(--tx);background:var(--bor);text-decoration:none;transition:filter .12s}
.btn:hover{filter:brightness(1.2)}.btn.bp{background:var(--acc);color:#fff}
.topbar{display:flex;align-items:center;gap:10px;padding:10px 20px;background:var(--sur);border-bottom:1px solid var(--bor)}
.topbar-title{font-size:16px;font-weight:700}.topbar-right{margin-left:auto;display:flex;gap:8px}
.page{max-width:600px;margin:30px auto;padding:0 16px}
.form-card{background:var(--sur);border:1px solid var(--bor);border-radius:10px;padding:20px 24px}
.form-row{margin-bottom:16px}
label{display:block;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--mu);margin-bottom:5px}
input[type=time]{background:#173a69;border:1px solid var(--bor);border-radius:6px;color:var(--tx);padding:6px 10px;font-size:13px;width:160px}
input[type=time]:focus{outline:none;border-color:var(--acc)}
.saved-banner{background:rgba(34,197,94,.12);border:1px solid var(--ok);border-radius:7px;padding:8px 14px;margin-bottom:16px;font-size:12px;color:var(--ok)}
</style>
<link rel="icon" type="image/x-icon" href="/static/signalscope_icon.png">
</head>
<body>
<div class="topbar">
  <span class="topbar-title">📰 Morning Report Settings</span>
  <div class="topbar-right">
    <a class="btn" href="/hub/morning-report">← Back to Report</a>
    <a class="btn" href="/">⌂ Dashboard</a>
  </div>
</div>
<div class="page">
  {% if saved %}
  <div class="saved-banner">✓ Settings saved.</div>
  {% endif %}
  <div class="form-card">
    <form method="post" action="/hub/morning-report/settings">
      <input type="hidden" name="_csrf_token" value="{{csrf_token()}}">
      <div class="form-row">
        <label for="report_time">Daily Report Generation Time</label>
        <input type="time" id="report_time" name="report_time" value="{{cfg.report_time}}">
        <div style="font-size:11px;color:var(--mu);margin-top:5px">
          The report will be automatically generated each day at this local time, covering the previous calendar day.
        </div>
      </div>
      <button type="submit" class="btn bp">Save Settings</button>
    </form>
  </div>
</div>
</body>
</html>"""
