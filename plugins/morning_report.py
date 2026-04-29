# morning_report.py — Daily Morning Briefing plugin for SignalScope
# Drop alongside signalscope.py; auto-discovered on next start.
# Hub-only.

SIGNALSCOPE_PLUGIN = {
    "id":       "morning_report",
    "label":    "Morning Report",
    "url":      "/hub/morning-report",
    "icon":     "📰",
    "hub_only": True,
    "version":  "1.3.4",
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
_NOTES_PATH  = os.path.join(_APP_DIR,  "chain_notes.json")

_DEFAULT_CFG = {"report_time": "06:00", "email_recipients": ""}

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
    import tempfile
    tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(_CFG_PATH), suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp_path, _CFG_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
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


def _load_chain_notes() -> dict:
    """Return {fault_log_id: {text, by, ts, edited_at}} from chain_notes.json."""
    try:
        if os.path.exists(_NOTES_PATH):
            with open(_NOTES_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


# ── Email ──────────────────────────────────────────────────────────────────────

def _build_email_html(report: dict) -> str:
    """Build a self-contained HTML email for the morning report.
    All styles are inline for email client compatibility.
    Matches the dark SignalScope colour scheme.
    """
    import html as _html

    def e(v):
        return _html.escape(str(v) if v is not None else "")

    # Palette
    BG = "#07142b"; SUR = "#0d2346"; BOR = "#17345f"; ACC = "#17a8ff"
    OK = "#22c55e"; WN = "#f59e0b"; AL = "#ef4444"; TX = "#eef5ff"; MU = "#8aa4c8"
    SUR2 = "#122548"

    HL_COL = {"ok": OK,        "wn": WN,        "al": AL}
    HL_BG  = {"ok": "#0b2918", "wn": "#241a05", "al": "#230a0a"}
    TREND_BG  = {"ok": "#0f3320", "al": "#2a0a0a", "wn": "#2a1a00", "mu": "#0f1e35"}
    TREND_COL = {"ok": OK,        "al": AL,         "wn": WN,        "mu": MU}
    PCOL = {"red": AL, "amber": WN, "blue": ACC, "green": OK}

    hc     = report.get("headline_color", "ok")
    hl_col = HL_COL.get(hc, OK)
    hl_bg  = HL_BG.get(hc, "#0b2918")

    out = []
    a = out.append

    # ── Doctype / head ──
    a('<!DOCTYPE html><html lang="en"><head>')
    a('<meta charset="utf-8">')
    a('<meta name="viewport" content="width=device-width,initial-scale=1.0">')
    a('<meta name="color-scheme" content="dark">')
    a('<meta name="supported-color-schemes" content="dark">')
    a('<title>Morning Report &mdash; ' + e(report.get("covers_label", "")) + '</title>')
    a('<style>body,table,td{-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%}</style>')
    a('</head>')
    a('<body style="margin:0;padding:0;background:' + BG + ';font-family:system-ui,-apple-system,Helvetica Neue,Arial,sans-serif;color:' + TX + ';font-size:13px">')

    # Outer wrapper
    a('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:' + BG + '">')
    a('<tr><td align="center" style="padding:24px 16px">')

    # Inner 680px content table
    a('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:680px;width:100%">')

    # ── HEADER BAR ──
    a('<tr><td style="background:linear-gradient(180deg,#1a3a7a,#0d2346);border:1px solid ' + BOR + ';border-radius:10px 10px 0 0;padding:18px 24px">')
    a('<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>')
    a('<td style="vertical-align:middle"><span style="font-size:22px">&#128240;</span>'
      ' <span style="font-size:17px;font-weight:700;color:' + TX + ';letter-spacing:.01em">Morning Report</span></td>')
    a('<td align="right" style="vertical-align:middle"><span style="font-size:12px;color:' + MU + '">'
      + e(report.get("covers_label", "")) + '</span></td>')
    a('</tr></table></td></tr>')

    # ── HEADLINE BANNER ──
    a('<tr><td style="padding:0">')
    a('<div style="background:' + hl_bg + ';border-left:4px solid ' + hl_col
      + ';padding:14px 20px;font-size:14px;font-weight:600;color:' + hl_col + ';line-height:1.4;margin:0">'
      + e(report.get("headline", "")) + '</div>')
    a('</td></tr>')

    # ── DIVIDER ──
    a('<tr><td style="height:3px;background:' + BOR + '"></td></tr>')

    # ── AT A GLANCE ──
    aag = report.get("at_a_glance", {})
    if aag:
        tf = aag.get("total_faults", 0)
        tc = aag.get("total_chains", 0)
        dm = aag.get("total_downtime_min", 0)
        cleanest = aag.get("cleanest_chain")
        worst    = aag.get("worst_chain")

        a('<tr><td style="background:' + SUR + ';border:1px solid ' + BOR + ';border-top:none;padding:16px 20px">')
        a('<div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:' + MU
          + ';margin-bottom:14px;padding-bottom:8px;border-bottom:1px solid ' + BOR + '">Yesterday at a Glance</div>')
        a('<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>')

        def _stat_cell(val, col, lbl, small=False):
            fs = "14px" if small else "26px"
            a('<td width="25%" style="text-align:center;padding:4px">')
            a('<div style="background:' + BG + ';border:1px solid ' + BOR + ';border-radius:8px;padding:14px 8px">')
            a('<div style="font-size:' + fs + ';font-weight:800;color:' + col + ';line-height:1.2">' + e(val) + '</div>')
            a('<div style="font-size:10px;color:' + MU + ';margin-top:4px">' + lbl + '</div>')
            a('</div></td>')

        _stat_cell(tf, AL if tf > 0 else OK, "Audio Interruptions")
        _stat_cell(tc, TX, "Chains Monitored")
        _stat_cell(dm, WN if dm > 0 else OK, "Minutes Off-Air")
        if cleanest:
            _stat_cell(cleanest, OK, "Best Performing", small=True)
        elif worst:
            wf = aag.get("worst_chain_faults", 0)
            _stat_cell(worst, AL, "Most Issues (" + str(wf) + ")", small=True)
        else:
            a('<td width="25%"></td>')

        a('</tr></table></td></tr>')

    # ── CHAIN HEALTH TABLE ──
    chain_health = report.get("chain_health", [])
    if chain_health:
        TH = 'style="padding:7px 10px;text-align:left;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:' + MU + ';border-bottom:2px solid ' + BOR + ';white-space:nowrap;background:' + SUR2 + '"'
        a('<tr><td style="background:' + SUR + ';border:1px solid ' + BOR + ';border-top:none;padding:16px 20px">')
        a('<div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:' + MU
          + ';margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid ' + BOR + '">Audio Chain Health</div>')
        a('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">')
        a('<tr>')
        for h in ["Chain", "Interruptions", "Off-Air", "On-Air&nbsp;%", "7-Day&nbsp;Avg", "vs&nbsp;Usual"]:
            a('<th ' + TH + '>' + h + '</th>')
        a('</tr>')
        for i, row in enumerate(chain_health):
            rb = SUR2 if i % 2 == 0 else SUR
            TD = 'style="padding:7px 10px;border-bottom:1px solid ' + BOR + ';font-size:12px;background:' + rb + '"'
            a('<tr>')
            a('<td ' + TD + '><b style="color:' + TX + '">' + e(row.get("name","")) + '</b></td>')
            fy = row.get("faults_y", 0)
            a('<td ' + TD + '>'
              + ('<span style="color:' + OK + '">&#10003; None</span>' if fy == 0
                 else '<span style="color:' + AL + ';font-weight:700">' + e(fy) + '</span>')
              + '</td>')
            dm2 = row.get("downtime_min", 0)
            a('<td ' + TD + '>'
              + ('<span style="color:' + WN + '">' + e(dm2) + '&nbsp;min</span>' if dm2 > 0
                 else '<span style="color:' + OK + '">&mdash;</span>')
              + '</td>')
            upt = row.get("uptime_pct", 100.0)
            uc = OK if upt >= 99.9 else (MU if upt >= 99.0 else AL)
            a('<td ' + TD + '><span style="color:' + uc + ';font-weight:700">' + e(upt) + '%</span></td>')
            avg7 = row.get("avg7", 0)
            a('<td ' + TD + '><span style="color:' + MU + '">'
              + (e(avg7) + '/day' if avg7 > 0 else '<span style="color:' + OK + '">No history</span>')
              + '</span></td>')
            tc_name = row.get("trend_color", "mu")
            a('<td ' + TD + '><span style="background:' + TREND_BG.get(tc_name, TREND_BG["mu"])
              + ';color:' + TREND_COL.get(tc_name, MU)
              + ';border-radius:10px;padding:2px 8px;font-size:10px;font-weight:700">'
              + e(row.get("trend_label","")) + '</span></td>')
            a('</tr>')
        a('</table></td></tr>')

    # ── OUTAGE DETAIL LOG ──
    od_list = report.get("outage_detail", [])
    if od_list:
        a('<tr><td style="background:' + SUR + ';border:1px solid ' + BOR + ';border-top:none;padding:16px 20px">')
        a('<div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:' + MU
          + ';margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid ' + BOR + '">&#128269; Outage Detail Log</div>')

        for od in od_list:
            a('<div style="background:' + BG + ';border:1px solid ' + BOR + ';border-radius:8px;margin-bottom:12px;overflow:hidden">')
            # card header
            a('<div style="background:linear-gradient(180deg,#1a2f58,#122548);padding:10px 14px;border-bottom:1px solid ' + BOR + '">')
            a('<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>')
            a('<td style="vertical-align:middle">'
              '<span style="font-weight:700;font-size:13px;color:' + WN + '">' + e(od.get("chain_name","")) + '</span>')
            if od.get("fault_time_str"):
                a(' <span style="font-size:11px;color:' + MU + '">' + e(od["fault_time_str"]))
                if od.get("recovery_time_str") and od["recovery_time_str"] != "Ongoing":
                    a(' &mdash; ' + e(od["recovery_time_str"]))
                elif od.get("recovery_time_str") == "Ongoing":
                    a(' &mdash; <span style="color:' + AL + '">Ongoing</span>')
                a('</span>')
            a('</td>')
            if od.get("duration_str"):
                a('<td align="right" style="vertical-align:middle">'
                  '<span style="background:rgba(245,158,11,.15);color:' + WN
                  + ';border-radius:10px;padding:2px 9px;font-size:11px;font-weight:700">'
                  + e(od["duration_str"]) + '</span></td>')
            a('</tr></table></div>')

            # card body
            a('<div style="padding:12px 14px">')

            def _od_row(lbl, val):
                a('<div style="margin-bottom:6px;padding-bottom:6px;border-bottom:1px solid rgba(255,255,255,.04)">'
                  '<div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:' + MU + '">' + lbl + '</div>'
                  '<div style="font-size:12px;color:' + TX + '">' + val + '</div>'
                  '</div>')

            if od.get("fault_node") or od.get("fault_site"):
                parts_v = []
                if od.get("fault_node"): parts_v.append(e(od["fault_node"]))
                if od.get("fault_site"): parts_v.append(e(od["fault_site"]))
                _od_row("Fault Detected At", " &mdash; ".join(parts_v))
            if od.get("fault_stream"):  _od_row("Monitoring Stream", e(od["fault_stream"]))
            if od.get("message"):       _od_row("Fault Context",     e(od["message"]))

            # Automation context
            if od.get("zetta_computer") or od.get("zetta_mode") or od.get("zetta_title") or od.get("zetta_artist"):
                a('<div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:' + ACC
                  + ';margin:10px 0 6px;padding-top:8px;border-top:1px solid ' + BOR + '">Automation Context</div>')
                if od.get("zetta_computer"): _od_row("Zetta Computer",   e(od["zetta_computer"]))
                if od.get("zetta_mode"):     _od_row("Automation Mode",  e(od["zetta_mode"]))
                if od.get("zetta_title") or od.get("zetta_artist"):
                    if od.get("zetta_artist") and od.get("zetta_title"):
                        znp = e(od["zetta_artist"]) + " &mdash; " + e(od["zetta_title"])
                    elif od.get("zetta_title"):  znp = e(od["zetta_title"])
                    else:                        znp = e(od["zetta_artist"])
                    _od_row("What Was Playing", znp)

            # Studios
            studios = od.get("studios", {})
            if studios:
                a('<div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:' + ACC
                  + ';margin:10px 0 6px;padding-top:8px;border-top:1px solid ' + BOR + '">Studio at Time of Fault</div>')
                for sname, bname in studios.items():
                    _od_row(e(sname), e(bname) if bname else '<span style="color:' + MU + '">&mdash;</span>')

            if od.get("zetta_is_spot"):
                a('<div style="background:rgba(245,158,11,.1);border:1px solid rgba(245,158,11,.4);border-radius:6px;'
                  'padding:6px 10px;font-size:11px;color:' + WN + ';margin-top:8px">'
                  'Fault occurred during an ad break &mdash; likely expected</div>')
            if od.get("cascaded_from"):
                a('<div style="font-size:11px;color:' + MU + ';margin-top:6px;font-style:italic">'
                  'Cascaded from: ' + e(od["cascaded_from"]) + '</div>')
            if od.get("note_text"):
                a('<div style="background:rgba(23,168,255,.07);border:1px solid rgba(23,168,255,.25);'
                  'border-radius:6px;padding:8px 12px;margin-top:10px">')
                a('<div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:' + ACC
                  + ';margin-bottom:4px">&#9999;&#65039; Engineering Note</div>')
                a('<div style="font-size:12px;color:' + TX + ';white-space:pre-wrap;word-break:break-word">'
                  + e(od["note_text"]) + '</div>')
                note_meta = []
                if od.get("note_by"): note_meta.append("Added by " + e(od["note_by"]))
                if od.get("note_ts"): note_meta.append(e(od["note_ts"]))
                if note_meta:
                    a('<div style="font-size:10px;color:' + MU + ';margin-top:4px">'
                      + " &middot; ".join(note_meta) + '</div>')
                a('</div>')

            a('</div>')  # card body
            a('</div>')  # card

        a('</td></tr>')

    # ── PATTERNS ──
    patterns = report.get("patterns", [])
    if patterns:
        a('<tr><td style="background:' + SUR + ';border:1px solid ' + BOR + ';border-top:none;padding:16px 20px">')
        a('<div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:' + MU
          + ';margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid ' + BOR + '">What Stood Out Yesterday</div>')
        for p in patterns:
            pc = PCOL.get(p.get("color", "blue"), ACC)
            a('<div style="background:' + BG + ';border-left:3px solid ' + pc
              + ';border-radius:4px;padding:10px 14px;margin-bottom:8px;font-size:12px;color:' + TX + '">'
              + e(p.get("text","")) + '</div>')
        a('</td></tr>')

    # ── AUTOMATION HEALTH ──
    ah = report.get("automation_health", {})
    if ah and ah.get("has_zetta_data"):
        n_fo = len(ah.get("failovers", []))
        n_mc = len(ah.get("mode_changes", []))
        n_gw = len(ah.get("gap_warnings", []))
        n_ab = ah.get("ad_break_faults", 0)
        n_mf = ah.get("manual_faults", 0)

        a('<tr><td style="background:' + SUR + ';border:1px solid ' + BOR + ';border-top:none;padding:16px 20px">')
        a('<div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:' + MU
          + ';margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid ' + BOR + '">&#129302; Automation Health</div>')
        a('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:14px"><tr>')
        for val, col, lbl in [
            (n_fo, AL if n_fo else OK, "Failovers"),
            (n_mc, WN if n_mc else OK, "Mode Changes"),
            (n_gw, WN if n_gw else OK, "GAP Warnings"),
            (n_ab, MU, "Ad Break Faults"),
            (n_mf, MU, "Manual Mode"),
        ]:
            a('<td width="20%" style="text-align:center;padding:4px">'
              '<div style="background:' + BG + ';border:1px solid ' + BOR + ';border-radius:8px;padding:12px 6px">'
              '<div style="font-size:22px;font-weight:800;color:' + col + '">' + e(val) + '</div>'
              '<div style="font-size:10px;color:' + MU + ';margin-top:3px">' + lbl + '</div>'
              '</div></td>')
        a('</tr></table>')

        def _ah_table(events, color, title):
            if not events: return
            TH2 = 'style="padding:6px 8px;text-align:left;font-size:10px;font-weight:700;text-transform:uppercase;color:' + MU + ';border-bottom:1px solid ' + BOR + '"'
            a('<div style="margin-bottom:12px">'
              '<div style="font-size:11px;font-weight:700;color:' + color + ';margin-bottom:6px">' + title + '</div>')
            a('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">')
            a('<tr><th ' + TH2 + '>Time</th><th ' + TH2 + '>Chain</th><th ' + TH2 + '>Detail</th></tr>')
            for ev in events:
                TD2 = 'style="padding:6px 8px;border-bottom:1px solid ' + BOR + ';font-size:12px"'
                a('<tr>'
                  '<td ' + TD2 + '><span style="color:' + MU + '">' + e(ev.get("ts","")) + '</span></td>'
                  '<td ' + TD2 + '><b>' + e(ev.get("stream","")) + '</b></td>'
                  '<td ' + TD2 + '><span style="color:' + MU + '">' + e(ev.get("message","")) + '</span></td>'
                  '</tr>')
            a('</table></div>')

        _ah_table(ah.get("failovers",[]),     AL, "&#128308; Failover Events")
        _ah_table(ah.get("mode_changes",[]),  WN, "&#9888;&#65039; Mode Changes")
        _ah_table(ah.get("gap_warnings",[]),  WN, "&#9201; GAP Warnings")

        if n_ab:
            a('<div style="padding:8px 12px;background:#1a0a2e;border:1px solid #4c1d95;border-radius:8px;font-size:12px;color:' + TX + ';margin-bottom:6px">'
              '&#8505;&#65039; <b>' + e(n_ab) + '</b> fault' + ('s' if n_ab != 1 else '')
              + ' occurred during an ad break &mdash; expected, not genuine audio loss.</div>')
        if n_mf:
            a('<div style="padding:8px 12px;background:' + SUR2 + ';border:1px solid ' + BOR + ';border-radius:8px;font-size:12px;color:' + TX + '">'
              '&#8505;&#65039; <b>' + e(n_mf) + '</b> fault' + ('s' if n_mf != 1 else '')
              + ' occurred in Manual mode &mdash; may represent intentional off-air periods.</div>')
        a('</td></tr>')

    # ── STUDIO ACTIVITY ──
    sa = report.get("studio_activity", {})
    if sa and sa.get("has_data") and sa.get("timeline"):
        a('<tr><td style="background:' + SUR + ';border:1px solid ' + BOR + ';border-top:none;padding:16px 20px">')
        a('<div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:' + MU
          + ';margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid ' + BOR + '">&#127897; Studio Activity</div>')
        TH_SA = 'style="padding:6px 8px;text-align:left;font-size:10px;font-weight:700;text-transform:uppercase;color:' + MU + ';border-bottom:1px solid ' + BOR + ';background:' + SUR2 + '"'
        for studio_name, moves in sa["timeline"].items():
            if not moves: continue
            a('<div style="font-size:12px;font-weight:700;color:' + ACC + ';margin-bottom:6px">&#128250; ' + e(studio_name) + '</div>')
            a('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin-bottom:14px">')
            a('<tr><th ' + TH_SA + '>Time</th><th ' + TH_SA + '>On Air</th><th ' + TH_SA + '>Was</th><th ' + TH_SA + '>Chains</th></tr>')
            for mv in moves:
                brand_col = TX if mv.get("brand") else MU
                brand_val = e(mv["brand"]) if mv.get("brand") else '&#8212; Automation &#8212;'
                TD_SA = 'style="padding:6px 8px;border-bottom:1px solid ' + BOR + ';font-size:12px"'
                a('<tr>'
                  '<td ' + TD_SA + '><span style="color:' + MU + '">' + e(mv.get("time","")) + '</span></td>'
                  '<td ' + TD_SA + '><b style="color:' + brand_col + '">' + brand_val + '</b></td>'
                  '<td ' + TD_SA + '><span style="color:' + MU + '">' + e(mv.get("prev_brand") or "&#8212;") + '</span></td>'
                  '<td ' + TD_SA + '><span style="color:' + MU + ';font-size:11px">'
                  + e(", ".join(mv.get("chains") or []) or "&#8212;") + '</span></td>'
                  '</tr>')
            a('</table>')
        a('</td></tr>')

    # ── STREAM QUALITY ──
    sq = report.get("stream_quality", [])
    if sq:
        a('<tr><td style="background:' + SUR + ';border:1px solid ' + BOR + ';border-top:none;padding:16px 20px">')
        a('<div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:' + MU
          + ';margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid ' + BOR + '">Live Stream Quality</div>')
        a('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">')
        TH_SQ = 'style="padding:6px 10px;text-align:left;font-size:10px;font-weight:700;text-transform:uppercase;color:' + MU + ';border-bottom:2px solid ' + BOR + ';background:' + SUR2 + '"'
        a('<tr><th ' + TH_SQ + '>Stream</th><th ' + TH_SQ + '>Site</th><th ' + TH_SQ + '>Glitches</th>'
          '<th ' + TH_SQ + '>Loudness</th><th ' + TH_SQ + '>Packet Loss</th><th ' + TH_SQ + '>Silence Events</th></tr>')
        for i, st in enumerate(sq):
            rb = SUR2 if i % 2 == 0 else SUR
            TD_SQ = 'style="padding:6px 10px;border-bottom:1px solid ' + BOR + ';font-size:12px;background:' + rb + '"'
            gc = st.get("glitch_count", 0)
            glitch_html = ('<span style="color:' + OK + '">None</span>' if gc == 0
                           else '<span style="color:' + WN + '">' + e(gc) + '</span>')
            lufs = st.get("lufs_i")
            lufs_html = (e(round(lufs, 1)) + ' LUFS' if lufs is not None and lufs > -70
                         else '<span style="color:' + MU + '">&#8212;</span>')
            rtp = st.get("rtp_loss_pct")
            if rtp is not None:
                rtp_html = ('<span style="color:' + OK + '">' if rtp < 0.1 else '<span style="color:' + WN + '">') + e(round(rtp, 2)) + '%</span>'
            else:
                rtp_html = '<span style="color:' + MU + '">&#8212;</span>'
            sc = st.get("silence_count", 0)
            sil_html = ('<span style="color:' + OK + '">None</span>' if sc == 0
                        else '<span style="color:' + AL + '">' + e(sc) + '</span>')
            a('<tr>'
              '<td ' + TD_SQ + '><b style="color:' + TX + '">' + e(st.get("name","")) + '</b></td>'
              '<td ' + TD_SQ + '><span style="color:' + MU + '">' + e(st.get("site","")) + '</span></td>'
              '<td ' + TD_SQ + '>' + glitch_html + '</td>'
              '<td ' + TD_SQ + '>' + lufs_html + '</td>'
              '<td ' + TD_SQ + '>' + rtp_html + '</td>'
              '<td ' + TD_SQ + '>' + sil_html + '</td>'
              '</tr>')
        a('</table></td></tr>')

    # ── HOURLY HEATMAP (table representation — CSS grids unsupported in email) ──
    hourly = report.get("hourly_counts", [])
    if hourly and any(h > 0 for h in hourly):
        HM_BG = ["#17345f", "rgba(23,168,255,.3)", "rgba(23,168,255,.55)", "rgba(239,68,68,.55)"]
        a('<tr><td style="background:' + SUR + ';border:1px solid ' + BOR + ';border-top:none;padding:16px 20px">')
        a('<div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:' + MU
          + ';margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid ' + BOR + '">Audio Interruptions by Hour</div>')
        a('<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>')
        for h in range(24):
            cnt = hourly[h] if h < len(hourly) else 0
            c   = min(cnt, 3)
            bg  = HM_BG[c]
            txt = str(cnt) if cnt > 0 else ""
            a('<td width="4%" style="text-align:center;padding:1px">'
              '<div style="background:' + bg + ';border-radius:3px;height:28px;line-height:28px;'
              'font-size:9px;font-weight:600;color:rgba(255,255,255,.8)">' + txt + '</div>'
              '<div style="font-size:8px;color:' + MU + ';text-align:center;margin-top:2px">'
              + str(h).zfill(2) + '</div></td>')
        a('</tr></table></td></tr>')

    # ── FOOTER ──
    a('<tr><td style="background:' + BG + ';border:1px solid ' + BOR
      + ';border-top:none;border-radius:0 0 10px 10px;padding:14px 20px">')
    a('<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>')
    a('<td style="font-size:11px;color:' + MU + '">')
    footer_parts = []
    if report.get("generated_ts"):
        footer_parts.append("Generated: <b style='color:" + TX + "'>" + e(report["generated_ts"]) + "</b>")
    if report.get("covers_date"):
        footer_parts.append("Covers: <b style='color:" + TX + "'>" + e(report["covers_date"]) + "</b>")
    a(" &nbsp;&middot;&nbsp; ".join(footer_parts))
    a('</td><td align="right" style="font-size:11px;color:rgba(148,163,184,.35)">SignalScope Morning Report</td>')
    a('</tr></table></td></tr>')

    # Close outer tables
    a('</table>')
    a('</td></tr></table>')
    a('</body></html>')

    return "".join(out)


def _send_report_email(report: dict, monitor, recipients_str: str) -> tuple:
    """Send the morning report as an HTML email.

    Uses the SMTP credentials from monitor.app_cfg.email.
    Returns (success: bool, error_message: str).
    """
    recipients = [r.strip() for r in recipients_str.replace(",", "\n").splitlines() if r.strip()]
    if not recipients:
        return False, "No recipients configured"

    ec = monitor.app_cfg.email
    if not getattr(ec, "enabled", False):
        return False, "Email alerts are disabled in SignalScope settings"
    if not getattr(ec, "smtp_host", ""):
        return False, "No SMTP host configured in SignalScope settings"

    subject = "Morning Report — " + (report.get("covers_label") or report.get("covers_date") or "Yesterday")
    html_body = _build_email_html(report)

    # Plain-text fallback
    tf = (report.get("at_a_glance") or {}).get("total_faults", 0)
    text_body = (
        "Morning Report\n"
        + report.get("headline", "") + "\n\n"
        + ("All clear — no audio interruptions yesterday.\n" if tf == 0
           else str(tf) + " audio interruption(s) yesterday.\n")
        + "\nView the full report in SignalScope."
    )

    try:
        import smtplib, ssl
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = getattr(ec, "from_addr", "") or getattr(ec, "username", "")
        msg["To"]      = ", ".join(recipients)

        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html",  "utf-8"))

        host = getattr(ec, "smtp_host", "")
        port = int(getattr(ec, "smtp_port", 587))
        user = getattr(ec, "username", "")
        pw   = getattr(ec, "password", "")
        tls  = getattr(ec, "use_tls", True)

        if tls and port == 465:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=15) as s:
                if user: s.login(user, pw)
                s.sendmail(msg["From"], recipients, msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=15) as s:
                s.ehlo()
                if tls:
                    s.starttls(context=ssl.create_default_context())
                    s.ehlo()
                if user: s.login(user, pw)
                s.sendmail(msg["From"], recipients, msg.as_string())

        return True, ""
    except Exception as ex:
        return False, str(ex)


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


def _normalise_fault_row_full(row: dict, id_map: dict) -> dict:
    """Like _normalise_fault_row but also carries the extended Zetta/context columns.

    The extra columns (fault_stream, zetta_computer, zetta_mode, zetta_now_playing,
    zetta_is_spot, message, cascaded_from) may not exist on older DBs — they are
    accessed with .get() so missing keys just return ''/0/None gracefully.
    """
    base = _normalise_fault_row(row, id_map)
    # Parse zetta_now_playing JSON string → title + artist
    _znp_raw  = row.get("zetta_now_playing") or ""
    _znp_title  = ""
    _znp_artist = ""
    if _znp_raw:
        try:
            _znp = json.loads(_znp_raw)
            _znp_title  = (_znp.get("title")  or "").strip()
            _znp_artist = (_znp.get("artist") or "").strip()
        except Exception:
            pass
    base.update({
        "fault_stream":    row.get("fault_stream", ""),
        "zetta_computer":  row.get("zetta_computer", ""),
        "zetta_mode":      row.get("zetta_mode", ""),
        "zetta_title":     _znp_title,
        "zetta_artist":    _znp_artist,
        "zetta_is_spot":   bool(row.get("zetta_is_spot", 0)),
        "fault_log_id":    row.get("id", ""),   # PK — used to join engineering notes
        "message":         row.get("message", ""),
        "cascaded_from":   row.get("cascaded_from", ""),
    })
    return base


def _studio_at_time(all_move_events: list, fault_ts_epoch: float) -> dict:
    """Return {studio_name: brand_name} for each studio at the given epoch.

    For each studio, finds the last STUDIO_MOVE event whose _ts_epoch is at or
    before fault_ts_epoch.  Events that have no studio_name are skipped.
    """
    # Collect the most recent move per studio up to fault_ts_epoch
    studio_brand: dict = {}
    for ev in all_move_events:
        if ev.get("type") != "STUDIO_MOVE":
            continue
        ev_ts = ev.get("_ts_epoch", 0)
        if ev_ts > fault_ts_epoch:
            continue
        sname = (ev.get("studio_name") or "").strip()
        if not sname:
            continue
        # Keep the latest event per studio (events are sorted oldest-first)
        studio_brand[sname] = (ev.get("brand_name") or "").strip()
    return studio_brand


def _query_chain_faults(day_start: float, day_end: float, id_map: dict = None) -> list:
    """Return chain_fault_log rows for the given epoch window."""
    if id_map is None:
        id_map = {}
    if not os.path.exists(_METRICS_DB):
        return []
    # Try extended columns first; fall back to the original 5-column query on old DBs.
    # id is included so the caller can join engineering notes from chain_notes.json.
    try:
        with sqlite3.connect(_METRICS_DB, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                """SELECT id, chain_id, ts_start, ts_recovered, fault_node_label, fault_site,
                          fault_stream, zetta_computer, zetta_mode, zetta_now_playing,
                          zetta_is_spot, message, cascaded_from
                   FROM chain_fault_log
                   WHERE ts_start >= ? AND ts_start < ?
                   ORDER BY ts_start""",
                (day_start, day_end)
            )
            return [_normalise_fault_row_full(dict(r), id_map) for r in cur.fetchall()]
    except Exception:
        pass
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
    # Try extended columns first; fall back to the original 5-column query on old DBs.
    try:
        with sqlite3.connect(_METRICS_DB, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                """SELECT id, chain_id, ts_start, ts_recovered, fault_node_label, fault_site,
                          fault_stream, zetta_computer, zetta_mode, zetta_now_playing,
                          zetta_is_spot, message, cascaded_from
                   FROM chain_fault_log
                   WHERE ts_start >= ? AND ts_start < ?
                   ORDER BY ts_start""",
                (start, end)
            )
            return [_normalise_fault_row_full(dict(r), id_map) for r in cur.fetchall()]
    except Exception:
        pass
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

    # "Best Performing Chain" = a chain with ZERO faults (truly clean).
    # Never use min(faults_per_chain_y, ...) — that dict only contains chains
    # that actually had faults, so its minimum is still a faulty chain.
    # If every configured chain had at least one fault, suppress the card.
    _truly_clean = sorted(c for c in all_chain_names if faults_per_chain_y.get(c, 0) == 0)
    cleanest = _truly_clean[0] if _truly_clean else None

    worst = max(faults_per_chain_y, key=faults_per_chain_y.get) if faults_per_chain_y else None

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

    # ── Outage Detail Log ──────────────────────────────────────────────────────
    # One entry per chain_fault_log row for yesterday, with full context.
    # all_events already has STUDIO_MOVE entries from up to 30 days back, which
    # is all we need for the studio-at-time cross-reference.
    chain_notes = _load_chain_notes()   # {fault_log_id: {text, by, ts, edited_at}}
    outage_detail = []
    for row in chain_faults_y:
        cname = row.get("chain_name", "")
        if cname not in all_chain_names:
            continue

        ts_s = row.get("fault_ts", 0)
        ts_r = row.get("recovery_ts")
        dur_s = row.get("duration_s") or 0

        # Format times
        if ts_s:
            fault_time_str = datetime.datetime.fromtimestamp(ts_s).strftime("%H:%M:%S")
        else:
            fault_time_str = ""

        if ts_r:
            recovery_time_str = datetime.datetime.fromtimestamp(ts_r).strftime("%H:%M:%S")
        else:
            recovery_time_str = "Ongoing"

        # Human-readable duration
        if dur_s >= 3600:
            _dh = int(dur_s // 3600)
            _dm = int((dur_s % 3600) // 60)
            duration_str = f"{_dh}h {_dm}m" if _dm else f"{_dh}h"
        elif dur_s >= 60:
            _dm = int(dur_s // 60)
            _ds = int(dur_s % 60)
            duration_str = f"{_dm}m {_ds}s" if _ds else f"{_dm}m"
        elif dur_s > 0:
            duration_str = f"{int(dur_s)}s"
        else:
            duration_str = ""

        # Studio context at the fault time
        studios = _studio_at_time(all_events, ts_s) if ts_s else {}

        # Engineering note (added by operators via the fault log UI)
        _flog_id  = row.get("fault_log_id", "")
        _note_rec = chain_notes.get(_flog_id, {}) if _flog_id else {}
        note_text = (_note_rec.get("text") or "").strip()
        note_by   = (_note_rec.get("by")   or "").strip()
        note_ts   = (_note_rec.get("edited_at") or _note_rec.get("ts") or "").strip()

        outage_detail.append({
            "chain_name":         cname,
            "fault_time_str":     fault_time_str,
            "recovery_time_str":  recovery_time_str,
            "duration_str":       duration_str,
            "fault_node":         row.get("fault_node", ""),
            "fault_site":         row.get("site", ""),
            "fault_stream":       row.get("fault_stream", ""),
            "zetta_computer":     row.get("zetta_computer", ""),
            "zetta_mode":         row.get("zetta_mode", ""),
            "zetta_title":        row.get("zetta_title", ""),
            "zetta_artist":       row.get("zetta_artist", ""),
            "zetta_is_spot":      bool(row.get("zetta_is_spot", False)),
            "message":            row.get("message", ""),
            "cascaded_from":      row.get("cascaded_from", ""),
            "studios":            studios,
            "note_text":          note_text,
            "note_by":            note_by,
            "note_ts":            note_ts,
        })

    # Sort by fault time ascending
    outage_detail.sort(key=lambda x: x["fault_time_str"])

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
        "outage_detail":   outage_detail,
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
            # Send email if recipients are configured
            _cfg2 = _load_cfg()
            _rcpt = _cfg2.get("email_recipients", "").strip()
            if _rcpt:
                _ok, _err = _send_report_email(report, monitor, _rcpt)
                if _ok:
                    _n = len([r for r in _rcpt.replace(",", "\n").splitlines() if r.strip()])
                    monitor.log(f"[MorningReport] Report emailed to {_n} recipient(s)")
                else:
                    monitor.log(f"[MorningReport] Email failed: {_err}")
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

    # Determine whether this node is a hub (or standalone) vs a pure client node.
    # On client nodes the scheduler must NOT run — it reads shared app data files
    # (metrics_history.db, alert_log.json) that only exist on the hub, and writing
    # a report cache on every client would waste I/O and produce empty reports.
    cfg_ss = monitor.app_cfg
    mode   = getattr(getattr(cfg_ss, "hub", None), "mode", "standalone") or "standalone"
    is_hub = mode in ("hub", "both", "standalone")

    if not is_hub:
        # Client node: register stub routes only so Flask doesn't 404 if somehow
        # reached, but do NOT start the scheduler or load any cached report.

        @app.get("/hub/morning-report")
        @login_required
        def morning_report_page():
            return "<p style='font-family:sans-serif;padding:2em'>Morning Report is only available on the hub.</p>", 200

        @app.post("/api/morning-report/generate")
        @login_required
        @csrf_protect
        def morning_report_generate():
            return jsonify({"ok": False, "error": "Morning Report runs on the hub only."}), 403

        @app.get("/hub/morning-report/settings")
        @login_required
        def morning_report_settings_page():
            return "<p style='font-family:sans-serif;padding:2em'>Morning Report settings are only available on the hub.</p>", 200

        @app.post("/hub/morning-report/settings")
        @login_required
        @csrf_protect
        def morning_report_settings_save():
            return jsonify({"ok": False, "error": "Morning Report runs on the hub only."}), 403

        monitor.log("[MorningReport] Client node — scheduler not started")
        return

    # ── Hub / standalone only ──────────────────────────────────────────────────

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
        # Recipients: strip, deduplicate, normalise to one-per-line
        raw_rcpt = request.form.get("email_recipients", "")
        rcpt_list = [r.strip() for r in raw_rcpt.replace(",", "\n").splitlines() if r.strip()]
        cfg["email_recipients"] = "\n".join(rcpt_list)
        _save_cfg(cfg)
        monitor.log(f"[MorningReport] Settings saved — report_time={rt}, recipients={len(rcpt_list)}")
        return render_template_string(_SETTINGS_TPL, cfg=cfg, build=BUILD, saved=True)

    @app.post("/api/morning-report/send-email")
    @login_required
    @csrf_protect
    def morning_report_send_email():
        with _report_lock:
            report = dict(_report_cache)
        if not report:
            return jsonify({"ok": False, "error": "No report generated yet — click Regenerate first"}), 400
        cfg = _load_cfg()
        rcpt_str = cfg.get("email_recipients", "").strip()
        if not rcpt_str:
            return jsonify({"ok": False, "error": "No recipients configured — add them in Morning Report settings"}), 400
        ok, err = _send_report_email(report, monitor, rcpt_str)
        if ok:
            count = len([r for r in rcpt_str.replace(",", "\n").splitlines() if r.strip()])
            monitor.log(f"[MorningReport] Manual email send to {count} recipient(s)")
            return jsonify({"ok": True, "count": count})
        return jsonify({"ok": False, "error": err}), 500


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
/* Outage detail cards */
.od-card{background:var(--sur);border:1px solid var(--bor);border-radius:10px;overflow:hidden;margin-bottom:14px}
.od-head{display:flex;align-items:center;gap:10px;padding:10px 14px;background:linear-gradient(180deg,#1a2f58,#122548);border-bottom:1px solid var(--bor);flex-wrap:wrap}
.od-chain{font-weight:700;font-size:13px;color:var(--wn)}
.od-timerange{font-size:11px;color:var(--mu);font-variant-numeric:tabular-nums}
.od-dur{display:inline-block;background:rgba(245,158,11,.15);color:var(--wn);border-radius:10px;padding:2px 9px;font-size:11px;font-weight:700;white-space:nowrap}
.od-body{padding:12px 14px}
.od-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px 20px}
@media(max-width:600px){.od-grid{grid-template-columns:1fr}}
.od-row{display:flex;flex-direction:column;gap:1px;padding:4px 0;border-bottom:1px solid rgba(255,255,255,.04)}
.od-row:last-child{border-bottom:none}
.od-lbl{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:var(--mu)}
.od-val{font-size:12px;color:var(--tx)}
.od-sub-hdr{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:var(--acc);margin:10px 0 6px;padding-top:8px;border-top:1px solid var(--bor)}
.od-spot-notice{background:rgba(245,158,11,.1);border:1px solid rgba(245,158,11,.4);border-radius:6px;padding:6px 10px;font-size:11px;color:var(--wn);margin-top:8px}
.od-cascade-note{font-size:11px;color:var(--mu);margin-top:6px;font-style:italic}
.od-eng-note{background:rgba(23,168,255,.07);border:1px solid rgba(23,168,255,.25);border-radius:6px;padding:8px 12px;margin-top:10px}
.od-eng-note-hdr{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:var(--acc);margin-bottom:4px}
.od-eng-note-text{font-size:12px;color:var(--tx);white-space:pre-wrap;word-break:break-word}
.od-eng-note-meta{font-size:10px;color:var(--mu);margin-top:4px}
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

{# ── Outage Detail Log ── #}
{% set od_list = report.get('outage_detail', []) %}
{% if od_list %}
<div class="sec">
  <div class="sec-hdr">🔍 Outage Detail Log</div>
  <p style="font-size:11px;color:var(--mu);margin-bottom:14px">One card per recorded outage yesterday. Times are local server time.</p>
  {% for od in od_list %}
  <div class="od-card">
    <div class="od-head">
      <span class="od-chain">{{od.chain_name}}</span>
      {% if od.fault_time_str %}
      <span class="od-timerange">
        {{od.fault_time_str}}
        {% if od.recovery_time_str and od.recovery_time_str != 'Ongoing' %} — {{od.recovery_time_str}}
        {% elif od.recovery_time_str == 'Ongoing' %} — <span style="color:var(--al)">Ongoing</span>
        {% endif %}
      </span>
      {% endif %}
      {% if od.duration_str %}<span class="od-dur">{{od.duration_str}}</span>{% endif %}
    </div>
    <div class="od-body">
      <div class="od-grid">
        {% if od.fault_node or od.fault_site %}
        <div class="od-row">
          <span class="od-lbl">Fault Detected At</span>
          <span class="od-val">{% if od.fault_node %}{{od.fault_node}}{% endif %}{% if od.fault_node and od.fault_site %} — {% endif %}{% if od.fault_site %}{{od.fault_site}}{% endif %}</span>
        </div>
        {% endif %}
        {% if od.fault_stream %}
        <div class="od-row">
          <span class="od-lbl">Monitoring Stream</span>
          <span class="od-val">{{od.fault_stream}}</span>
        </div>
        {% endif %}
        {% if od.message %}
        <div class="od-row">
          <span class="od-lbl">Fault Context</span>
          <span class="od-val">{{od.message}}</span>
        </div>
        {% endif %}
      </div>
      {% if od.zetta_computer or od.zetta_mode or od.zetta_title or od.zetta_artist %}
      <div class="od-sub-hdr">Automation Context</div>
      <div class="od-grid">
        {% if od.zetta_computer %}
        <div class="od-row">
          <span class="od-lbl">Zetta Computer</span>
          <span class="od-val">{{od.zetta_computer}}</span>
        </div>
        {% endif %}
        {% if od.zetta_mode %}
        <div class="od-row">
          <span class="od-lbl">Automation Mode</span>
          <span class="od-val">{{od.zetta_mode}}</span>
        </div>
        {% endif %}
        {% if od.zetta_title or od.zetta_artist %}
        <div class="od-row">
          <span class="od-lbl">What Was Playing</span>
          <span class="od-val">
            {% if od.zetta_artist and od.zetta_title %}{{od.zetta_artist}} — {{od.zetta_title}}
            {% elif od.zetta_title %}{{od.zetta_title}}
            {% elif od.zetta_artist %}{{od.zetta_artist}}
            {% endif %}
          </span>
        </div>
        {% endif %}
      </div>
      {% endif %}
      {% if od.studios %}
      <div class="od-sub-hdr">Studio at Time of Fault</div>
      <div class="od-grid">
        {% for studio_name, brand_name in od.studios.items() %}
        <div class="od-row">
          <span class="od-lbl">{{studio_name}}</span>
          <span class="od-val">{% if brand_name %}{{brand_name}}{% else %}<span style="color:var(--mu)">—</span>{% endif %}</span>
        </div>
        {% endfor %}
      </div>
      {% endif %}
      {% if od.zetta_is_spot %}
      <div class="od-spot-notice">Fault occurred during an ad break — likely expected</div>
      {% endif %}
      {% if od.cascaded_from %}
      <div class="od-cascade-note">Cascaded from: {{od.cascaded_from}}</div>
      {% endif %}
      {% if od.note_text %}
      <div class="od-eng-note">
        <div class="od-eng-note-hdr">✏️ Engineering Note</div>
        <div class="od-eng-note-text">{{od.note_text}}</div>
        {% if od.note_by or od.note_ts %}
        <div class="od-eng-note-meta">
          {% if od.note_by %}Added by {{od.note_by}}{% endif %}
          {% if od.note_ts %} · {{od.note_ts}}{% endif %}
        </div>
        {% endif %}
      </div>
      {% endif %}
    </div>
  </div>
  {% endfor %}
</div>
{% endif %}

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
.page{max-width:640px;margin:30px auto;padding:0 16px}
.form-card{background:var(--sur);border:1px solid var(--bor);border-radius:10px;padding:20px 24px;margin-bottom:16px}
.card-hdr{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--acc);margin-bottom:16px;padding-bottom:10px;border-bottom:1px solid var(--bor)}
.form-row{margin-bottom:18px}
label{display:block;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--mu);margin-bottom:5px}
input[type=time]{background:#173a69;border:1px solid var(--bor);border-radius:6px;color:var(--tx);padding:6px 10px;font-size:13px;width:160px}
input[type=time]:focus{outline:none;border-color:var(--acc)}
textarea{width:100%;background:#0d1e40;border:1px solid var(--bor);border-radius:6px;color:var(--tx);padding:8px 10px;font-size:13px;resize:vertical;font-family:inherit}
textarea:focus{outline:none;border-color:var(--acc)}
.help{font-size:11px;color:var(--mu);margin-top:5px;line-height:1.5}
.saved-banner{background:rgba(34,197,94,.12);border:1px solid var(--ok);border-radius:7px;padding:8px 14px;margin-bottom:16px;font-size:12px;color:var(--ok)}
.send-row{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:4px}
#email-status{font-size:12px}
.status-ok{color:var(--ok)}.status-err{color:var(--al)}
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
    <div class="card-hdr">⏰ Schedule</div>
    <form method="post" action="/hub/morning-report/settings">
      <input type="hidden" name="_csrf_token" value="{{csrf_token()}}">
      <div class="form-row">
        <label for="report_time">Daily Report Generation Time</label>
        <input type="time" id="report_time" name="report_time" value="{{cfg.report_time}}">
        <div class="help">
          The report is automatically generated each day at this local time, covering the previous calendar day.
          If email recipients are configured below, the report is emailed at the same time.
        </div>
      </div>

      <div class="form-row">
        <label for="email_recipients">Email Recipients</label>
        <textarea id="email_recipients" name="email_recipients" rows="5"
          placeholder="engineer@station.com&#10;manager@station.com">{{cfg.get('email_recipients','')}}</textarea>
        <div class="help">
          One address per line. The full Morning Report is emailed to these addresses automatically when generated.<br>
          Uses the SMTP server configured in <b style="color:var(--tx)">Settings → Notifications → Email</b>.
          Leave blank to disable email delivery.
        </div>
      </div>

      <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
        <button type="submit" class="btn bp">Save Settings</button>
      </div>
    </form>
  </div>

  <div class="form-card">
    <div class="card-hdr">📧 Email</div>
    <p class="help" style="margin-bottom:14px">Send the current cached report to the configured recipients immediately. Useful for testing your SMTP setup.</p>
    <div class="send-row">
      <button type="button" class="btn" id="btn-test-email">📧 Send Report Now</button>
      <span id="email-status"></span>
    </div>
  </div>

</div>

<script nonce="{{csp_nonce()}}">
(function(){
  function _getCsrf(){
    return (document.querySelector('meta[name="csrf-token"]')||{}).content
        || (document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/)||[])[1]
        || '';
  }
  document.getElementById('btn-test-email').addEventListener('click', function(){
    var btn = this;
    var st  = document.getElementById('email-status');
    btn.disabled = true;
    btn.textContent = '📧 Sending…';
    st.textContent = '';
    st.className = '';
    fetch('/api/morning-report/send-email', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {'X-CSRFToken': _getCsrf(), 'Content-Type': 'application/json'},
      body: JSON.stringify({})
    }).then(function(r){ return r.json(); })
      .then(function(d){
        btn.disabled = false;
        btn.textContent = '📧 Send Report Now';
        if(d.ok){
          st.textContent = '✓ Sent to ' + d.count + ' recipient' + (d.count === 1 ? '' : 's');
          st.className = 'status-ok';
        } else {
          st.textContent = '✗ ' + (d.error || 'Failed');
          st.className = 'status-err';
        }
      })
      .catch(function(e){
        btn.disabled = false;
        btn.textContent = '📧 Send Report Now';
        st.textContent = '✗ Request failed';
        st.className = 'status-err';
      });
  });
})();
</script>
</body>
</html>"""
