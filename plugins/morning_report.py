# morning_report.py — Daily Morning Briefing plugin for SignalScope
# Drop alongside signalscope.py; auto-discovered on next start.
# Hub-only.

SIGNALSCOPE_PLUGIN = {
    "id":       "morning_report",
    "label":    "Morning Report",
    "url":      "/hub/morning-report",
    "icon":     "📰",
    "hub_only": True,
    "version":  "1.2.6",
}

import os, json, time, threading, datetime, sqlite3, statistics
from collections import defaultdict

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))   # plugins/ dir
_APP_DIR  = os.path.dirname(_BASE_DIR)                    # parent app dir (where shared data files live)
_CFG_PATH    = os.path.join(_BASE_DIR, "morning_report_cfg.json")
_CACHE_PATH  = os.path.join(_BASE_DIR, "morning_report_cache.json")
_METRICS_DB  = os.path.join(_APP_DIR,  "metrics_history.db")
_ALERT_LOG   = os.path.join(_APP_DIR,  "alert_log.json")
_SLA_PATH    = os.path.join(_APP_DIR,  "sla_data.json")

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

def _chain_id_to_name_map(monitor) -> dict:
    """Return {chain_id: chain_name} from the live SignalScope config.
    Falls back to chain_id itself when no match is found."""
    mapping = {}
    try:
        if monitor is not None:
            for c in (monitor.app_cfg.signal_chains or []):
                cid = c.get("id", "")
                name = c.get("name", cid)
                if cid:
                    mapping[cid] = name
    except Exception:
        pass
    return mapping


def _normalise_fault_row(row: dict, id_map: dict) -> dict:
    """Translate DB column names to the field names used by the report.

    The chain_fault_log table uses:
      chain_id, ts_start, ts_recovered, fault_node_label, fault_site
    The report code expects:
      chain_name, fault_ts, recovery_ts, duration_s, fault_node, site
    """
    cid  = row.get("chain_id", "")
    ts_s = row.get("ts_start") or 0
    ts_r = row.get("ts_recovered")
    dur  = (ts_r - ts_s) if (ts_r and ts_s and ts_r > ts_s) else 0.0
    return {
        "chain_name":   id_map.get(cid, cid),   # UUID → readable name, fallback UUID
        "fault_ts":     ts_s,
        "recovery_ts":  ts_r,
        "duration_s":   dur,
        "fault_node":   row.get("fault_node_label", ""),
        "site":         row.get("fault_site", ""),
    }


def _query_chain_faults(day_start: float, day_end: float, id_map: dict = None) -> list:
    """Return chain_fault_log rows for the given epoch window."""
    if id_map is None:
        id_map = {}
    if not os.path.exists(_METRICS_DB):
        return []
    try:
        with sqlite3.connect(_METRICS_DB, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                """SELECT chain_id, ts_start, ts_recovered, fault_node_label, fault_site
                   FROM chain_fault_log
                   WHERE ts_start >= ? AND ts_start < ?
                   ORDER BY ts_start""",
                (day_start, day_end)
            )
            return [_normalise_fault_row(dict(r), id_map) for r in cur.fetchall()]
    except Exception:
        return []


def _query_chain_faults_range(start: float, end: float, id_map: dict = None) -> list:
    if id_map is None:
        id_map = {}
    if not os.path.exists(_METRICS_DB):
        return []
    try:
        with sqlite3.connect(_METRICS_DB, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                """SELECT chain_id, ts_start, ts_recovered, fault_node_label, fault_site
                   FROM chain_fault_log
                   WHERE ts_start >= ? AND ts_start < ?
                   ORDER BY ts_start""",
                (start, end)
            )
            return [_normalise_fault_row(dict(r), id_map) for r in cur.fetchall()]
    except Exception:
        return []


def _query_all_chains(id_map: dict = None) -> list:
    """Return all distinct chain names (resolved via id_map) from the fault log."""
    if id_map is None:
        id_map = {}
    if not os.path.exists(_METRICS_DB):
        return []
    try:
        with sqlite3.connect(_METRICS_DB, timeout=10) as conn:
            cur = conn.execute(
                "SELECT DISTINCT chain_id FROM chain_fault_log ORDER BY chain_id"
            )
            return [id_map.get(r[0], r[0]) for r in cur.fetchall()]
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

    # Build chain_id → chain_name map for resolving UUIDs from the fault log DB
    _id_map = _chain_id_to_name_map(monitor)

    # Build stream → chain mapping early so faults_by_stream_day can use chain names
    # as keys (needed for the 7-day average lookups in the chain health table).
    _stream_to_chain_early: dict = {}
    _live_chain_names_early: set = set()
    if monitor is not None:
        try:
            cfg_ss = monitor.app_cfg
            for c in (cfg_ss.signal_chains or []) if cfg_ss else []:
                cname = (c.get("name") or "").strip()
                if cname:
                    _live_chain_names_early.add(cname)
                for node in (c.get("nodes") or []):
                    sname = (node.get("stream") or "").strip()
                    if sname and cname:
                        _stream_to_chain_early[sname] = cname
        except Exception:
            pass

    # Yesterday's events only (from alert_log)
    y_events = [e for e in all_events if ystart_e <= e.get("_ts_epoch", 0) <= yend_e]

    # Last 30 days fault counts per chain per day (for 7-day averages).
    # Keys are chain names, not raw stream names, so chain health averages are accurate.
    faults_by_stream_day = defaultdict(lambda: defaultdict(int))
    for ev in all_events:
        if ev.get("type") == "CHAIN_FAULT":
            continue  # chain_fault_log is the authoritative chain fault source
        ep = ev.get("_ts_epoch", 0)
        if ep > 0:
            d = datetime.datetime.fromtimestamp(ep).date()
            raw = ev.get("stream") or ev.get("chain_name") or ""
            if not raw:
                continue
            # Map stream name → chain name; only count for configured chains
            key = _stream_to_chain_early.get(raw, raw)
            if _live_chain_names_early and key not in _live_chain_names_early:
                continue  # skip orphan streams/chains
            faults_by_stream_day[key][d] += 1
    # Yesterday's fault events grouped by chain/stream
    fault_events_y = [e for e in y_events if e.get("type") in (
        "CHAIN_FAULT", "SILENCE", "STUDIO_FAULT", "STL_FAULT",
        "TX_DOWN", "DAB_AUDIO_FAULT", "RTP_FAULT", "RTP_LOSS", "AI_ALERT"
    )]

    # Chain fault log from SQLite for yesterday
    chain_faults_y = _query_chain_faults(ystart_e, yend_e, _id_map)
    # Last 30 days chain faults (for averages)
    chain_faults_30 = _query_chain_faults_range(time.time() - 30 * 86400, ystart_e, _id_map)

    # Incorporate 30-day chain_fault_log (SQLite) per-day counts into faults_by_stream_day
    # so that the 7-day average in the chain health table reflects chain-level DB data too.
    for row in chain_faults_30:
        cname = row.get("chain_name", "")
        ft    = row.get("fault_ts", 0)
        if cname and ft and (_live_chain_names_early and cname in _live_chain_names_early
                             or not _live_chain_names_early):
            d = datetime.datetime.fromtimestamp(ft).date()
            faults_by_stream_day[cname][d] += 1

    all_chains_db = _query_all_chains(_id_map)

    # Re-use the already-computed mappings built before faults_by_stream_day
    live_chain_names = _live_chain_names_early
    stream_to_chain  = _stream_to_chain_early

    # ── all_chain_names — anchored to configured chains only ──────────────────
    # IMPORTANT: never include raw stream names from the alert log, and never
    # include orphan chain IDs from old DB rows that no longer exist in the
    # current config. Either of those inflates the chain count far beyond what
    # the user actually has set up.
    if live_chain_names:
        # Primary path: use exactly the chains the user has configured.
        # Still include any historical chains from the DB whose UUID resolves
        # to a name in the live config (they're the same chains, just older rows).
        all_chain_names = live_chain_names
    else:
        # Fallback when monitor is unavailable (edge case): use DB-derived names
        chain_names_db = set(all_chains_db)
        chain_names_30 = set(r["chain_name"] for r in chain_faults_30)
        all_chain_names = chain_names_db | chain_names_30

    # ── Downtime by chain — configured chains only ────────────────────────────
    downtime_by_chain = defaultdict(float)
    for row in chain_faults_y:
        cname = row["chain_name"]
        if cname in all_chain_names:
            dur = row.get("duration_s") or 0
            downtime_by_chain[cname] += dur

    total_downtime_s = sum(downtime_by_chain.values())
    total_downtime_min = round(total_downtime_s / 60, 1)

    # ── Faults per chain yesterday ────────────────────────────────────────────
    # Primary source: chain_fault_log (SQLite) — one row per chain outage.
    # Do NOT add raw stream names as chain keys; that caused the "18 chains"
    # inflation. Stream-level events (SILENCE etc.) are attributed via
    # stream_to_chain and only counted when the owning chain has NO chain_fault_log
    # entries (standalone-monitored streams not part of a comparator chain).
    # CHAIN_FAULT events in the alert log are skipped entirely — they are already
    # captured by chain_fault_log, so counting both would double every fault.
    faults_per_chain_y = defaultdict(int)
    for row in chain_faults_y:
        cname = row["chain_name"]
        if cname in all_chain_names:
            faults_per_chain_y[cname] += 1

    # Supplement with stream-level silence/fault events attributed to configured chains.
    # Only adds a count for chains that have NO chain_fault_log entry (avoids
    # double-counting chains that already have authoritative DB records).
    for ev in fault_events_y:
        if ev.get("type") == "CHAIN_FAULT":
            continue  # already in chain_fault_log; skip
        s = ev.get("stream") or ""
        if not s:
            continue
        target = stream_to_chain.get(s)
        if target and target in all_chain_names and faults_per_chain_y[target] == 0:
            faults_per_chain_y[target] += 1

    # total_faults = sum of per-chain counts (deduped, configured chains only)
    total_faults = sum(faults_per_chain_y.values())

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
        if row["chain_name"] not in all_chain_names:
            continue
        ft = row.get("fault_ts", 0)
        if ft:
            h = datetime.datetime.fromtimestamp(ft).hour
            hourly_counts[h] += 1
    for ev in fault_events_y:
        if ev.get("type") == "CHAIN_FAULT":
            continue  # already counted from chain_fault_log above
        s = ev.get("stream") or ""
        target = stream_to_chain.get(s) if s else None
        if target and target in all_chain_names:
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
                d_faults = _query_chain_faults(d_start, d_end, _id_map)
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

    # ── Automation Health (Zetta) ──────────────────────────────────────────────
    # Zetta-specific event types from the alert log
    _ZETTA_TYPES = {"ZETTA_MODE_CHANGE", "ZETTA_FAILOVER", "ZETTA_GAP_LOW"}
    zetta_events_y = [e for e in y_events if e.get("type") in _ZETTA_TYPES]

    _zh_mode_changes = [
        {"stream": e.get("stream", ""), "message": e.get("message", ""), "ts": e.get("ts", "")}
        for e in zetta_events_y if e.get("type") == "ZETTA_MODE_CHANGE"
    ]
    _zh_failovers = [
        {"stream": e.get("stream", ""), "message": e.get("message", ""), "ts": e.get("ts", "")}
        for e in zetta_events_y if e.get("type") == "ZETTA_FAILOVER"
    ]
    _zh_gap_warnings = [
        {"stream": e.get("stream", ""), "message": e.get("message", ""), "ts": e.get("ts", "")}
        for e in zetta_events_y if e.get("type") == "ZETTA_GAP_LOW"
    ]

    # CHAIN_FAULT events stamped with Zetta context
    _chain_fault_events_y = [e for e in y_events if e.get("type") == "CHAIN_FAULT"]
    _ad_break_faults = [e for e in _chain_fault_events_y if e.get("zetta_is_spot")]
    _manual_faults   = [e for e in _chain_fault_events_y
                        if (e.get("zetta_mode") and e.get("zetta_mode") != "Auto"
                            and not e.get("zetta_is_spot"))]

    automation_health = {
        "mode_changes":      _zh_mode_changes,
        "failovers":         _zh_failovers,
        "gap_warnings":      _zh_gap_warnings,
        "ad_break_faults":   len(_ad_break_faults),
        "manual_faults":     len(_manual_faults),
        "total_auto_events": len(zetta_events_y),
        "has_zetta_data":    bool(zetta_events_y or _ad_break_faults or _manual_faults),
    }

    # ── Studio Moves (from Studio Board plugin STUDIO_MOVE events) ─────────────
    # Collect yesterday's STUDIO_MOVE events and group them into a per-studio
    # timeline so the report shows which brand was live in each studio and when.
    studio_move_events = [
        e for e in y_events if e.get("type") == "STUDIO_MOVE"
    ]
    # Build a structured list: [{time, studio, brand, prev_brand, message}, ...]
    studio_moves = []
    for e in studio_move_events:
        studio_moves.append({
            "ts":           e.get("ts", ""),
            "time":         (e.get("ts") or "")[-8:],     # HH:MM:SS part
            "studio":       e.get("studio_name") or "",
            "brand":        e.get("brand_name") or "",
            "prev_brand":   e.get("prev_brand") or "",
            "message":      e.get("message") or "",
            "chains":       e.get("brand_chains") or [],
        })
    # Per-studio summary: {studio_name: [{brand, from_time, to_time}, ...]}
    _studio_timeline: dict = {}
    for mv in studio_moves:
        sname = mv["studio"] or "Unknown Studio"
        _studio_timeline.setdefault(sname, []).append(mv)
    studio_activity = {
        "moves":         studio_moves,
        "timeline":      _studio_timeline,
        "total_moves":   len(studio_moves),
        "has_data":      bool(studio_moves),
    }

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
        "automation_health": automation_health,
        "studio_activity": studio_activity,
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

{# ── Automation Health (Zetta) ── #}
{% set ah = report.automation_health %}
{% if ah and ah.has_zetta_data %}
<div class="sec">
  <div class="sec-hdr">🤖 Automation Health</div>
  <p style="font-size:11px;color:var(--mu);margin-bottom:10px">Events from the Zetta broadcast automation system yesterday. Mode changes and failovers may indicate automation issues. Faults during ad breaks are expected and do not represent genuine audio loss.</p>
  <div class="aag-grid" style="margin-bottom:16px">
    <div class="aag-card">
      <div class="aag-val" style="color:{% if ah.failovers %}var(--al){% else %}var(--ok){% endif %}">{{ah.failovers|length}}</div>
      <div class="aag-lbl">Failover Event{% if ah.failovers|length != 1 %}s{% endif %}</div>
    </div>
    <div class="aag-card">
      <div class="aag-val" style="color:{% if ah.mode_changes %}var(--wn){% else %}var(--ok){% endif %}">{{ah.mode_changes|length}}</div>
      <div class="aag-lbl">Mode Change{% if ah.mode_changes|length != 1 %}s{% endif %}</div>
    </div>
    <div class="aag-card">
      <div class="aag-val" style="color:{% if ah.gap_warnings %}var(--wn){% else %}var(--ok){% endif %}">{{ah.gap_warnings|length}}</div>
      <div class="aag-lbl">GAP Warning{% if ah.gap_warnings|length != 1 %}s{% endif %}</div>
    </div>
    <div class="aag-card">
      <div class="aag-val" style="color:var(--mu)">{{ah.ad_break_faults}}</div>
      <div class="aag-lbl">Faults During Ad Breaks</div>
    </div>
    <div class="aag-card">
      <div class="aag-val" style="color:var(--mu)">{{ah.manual_faults}}</div>
      <div class="aag-lbl">Faults in Manual Mode</div>
    </div>
  </div>
  {% if ah.failovers %}
  <div style="margin-bottom:14px">
    <div style="font-size:11px;font-weight:700;color:var(--al);margin-bottom:6px">🔴 Failover Events</div>
    <div class="tbl-wrap"><table>
      <thead><tr><th>Time</th><th>Chain</th><th>Detail</th></tr></thead>
      <tbody>
        {% for ev in ah.failovers %}
        <tr>
          <td style="white-space:nowrap;color:var(--mu);font-variant-numeric:tabular-nums">{{ev.ts}}</td>
          <td><b>{{ev.stream}}</b></td>
          <td style="color:var(--mu)">{{ev.message}}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table></div>
  </div>
  {% endif %}
  {% if ah.mode_changes %}
  <div style="margin-bottom:14px">
    <div style="font-size:11px;font-weight:700;color:var(--wn);margin-bottom:6px">⚠️ Mode Changes</div>
    <div class="tbl-wrap"><table>
      <thead><tr><th>Time</th><th>Chain</th><th>Detail</th></tr></thead>
      <tbody>
        {% for ev in ah.mode_changes %}
        <tr>
          <td style="white-space:nowrap;color:var(--mu);font-variant-numeric:tabular-nums">{{ev.ts}}</td>
          <td><b>{{ev.stream}}</b></td>
          <td style="color:var(--mu)">{{ev.message}}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table></div>
  </div>
  {% endif %}
  {% if ah.gap_warnings %}
  <div style="margin-bottom:14px">
    <div style="font-size:11px;font-weight:700;color:var(--wn);margin-bottom:6px">⏱ GAP Warnings</div>
    <div class="tbl-wrap"><table>
      <thead><tr><th>Time</th><th>Chain</th><th>Detail</th></tr></thead>
      <tbody>
        {% for ev in ah.gap_warnings %}
        <tr>
          <td style="white-space:nowrap;color:var(--mu);font-variant-numeric:tabular-nums">{{ev.ts}}</td>
          <td><b>{{ev.stream}}</b></td>
          <td style="color:var(--mu)">{{ev.message}}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table></div>
  </div>
  {% endif %}
  {% if ah.ad_break_faults > 0 %}
  <div style="padding:8px 12px;background:rgba(91,33,182,.1);border:1px solid rgba(124,58,237,.3);border-radius:8px;font-size:12px;margin-bottom:6px">
    ℹ️ <b>{{ah.ad_break_faults}}</b> chain fault{% if ah.ad_break_faults != 1 %}s{% endif %} occurred while Zetta was playing an ad break — these are expected and do not represent genuine audio loss.
  </div>
  {% endif %}
  {% if ah.manual_faults > 0 %}
  <div style="padding:8px 12px;background:rgba(138,164,200,.06);border:1px solid var(--bor);border-radius:8px;font-size:12px">
    ℹ️ <b>{{ah.manual_faults}}</b> chain fault{% if ah.manual_faults != 1 %}s{% endif %} occurred while Zetta was in Manual mode — these may represent intentional off-air periods.
  </div>
  {% endif %}
</div>
{% endif %}

{# ── Studio Activity (Studio Board moves) ── #}
{% set sa = report.get('studio_activity') %}
{% if sa and sa.has_data %}
<div class="sec">
  <div class="sec-hdr">🎙 Studio Activity</div>
  <p style="font-size:11px;color:var(--mu);margin-bottom:10px">Brand-to-studio assignments recorded yesterday by the Studio Board plugin. Each row is one move — who was on air in which studio and when.</p>
  {% for studio_name, moves in sa.timeline.items() %}
  <div style="margin-bottom:14px">
    <div style="font-size:12px;font-weight:700;color:var(--acc);margin-bottom:6px">📺 {{studio_name}}</div>
    <div class="tbl-wrap"><table>
      <thead><tr><th>Time</th><th>On Air</th><th>Was</th><th>Chains</th></tr></thead>
      <tbody>
        {% for mv in moves %}
        <tr>
          <td style="white-space:nowrap;color:var(--mu);font-variant-numeric:tabular-nums">{{mv.time}}</td>
          <td><b style="color:{% if mv.brand %}var(--tx){% else %}var(--mu){% endif %}">{{mv.brand if mv.brand else '— Automation —'}}</b></td>
          <td style="color:var(--mu)">{{mv.prev_brand if mv.prev_brand else '—'}}</td>
          <td style="color:var(--mu);font-size:11px">{{mv.chains|join(', ') if mv.chains else '—'}}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table></div>
  </div>
  {% endfor %}
  {% if sa.total_moves == 0 %}
  <div class="no-data">No studio moves recorded yesterday.</div>
  {% endif %}
</div>
{% endif %}

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
  function _btnLoad(btn){
    btn._origTxt = btn.textContent;
    btn.disabled = true;
    btn.textContent = '↻ Generating…';
  }
  function _btnReset(btn){
    btn.disabled = false;
    btn.textContent = btn._origTxt || '↻ Regenerate';
  }
  function _ssToast(msg, type){
    var t = document.getElementById('toast');
    t.textContent = msg;
    t.style.background = (type === 'err') ? '#7f1d1d' : '#14532d';
    t.style.display = 'block';
    setTimeout(function(){ t.style.display = 'none'; }, 3500);
  }

  document.getElementById('btn-regen').addEventListener('click', function(){
    var btn = this;
    _btnLoad(btn);
    fetch('/api/morning-report/generate', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {'X-CSRFToken': _getCsrf(), 'Content-Type': 'application/json'},
      body: JSON.stringify({})
    }).then(function(r){ return r.json(); })
      .then(function(d){
        _btnReset(btn);
        if(d.ok){
          var t = document.getElementById('toast');
          t.textContent = 'Report regenerated';
          t.style.background = '#14532d';
          t.style.display = 'block';
          setTimeout(function(){ t.style.display='none'; window.location.reload(); }, 1200);
        } else {
          _ssToast('Error: ' + (d.error || 'unknown'), 'err');
        }
      })
      .catch(function(e){
        _btnReset(btn);
        _ssToast('Request failed: ' + e, 'err');
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
