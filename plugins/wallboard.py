# wallboard.py — SignalScope Wall Monitor for Yodeck / broadcast TV displays
# Drop into the plugins/ subdirectory.
#
# Shows a full-screen, auto-refreshing broadcast wall display:
#   • Live animated VU meters (150 ms cadence via /api/hub/live_levels)
#   • Per-stream cards: ON AIR / SILENCE / OFFLINE states
#   • FM: RDS PS + RadioText (now playing), frequency, stereo, SNR
#   • DAB: service name + DLS now-playing text, SNR, bitrate
#   • HTTP/RTP: stream name, LUFS, AI status
#   • SLA uptime %, LUFS metering, AI monitor badge
#   • Bottom ticker cycling recent alerts

SIGNALSCOPE_PLUGIN = {
    "id":       "wallboard",
    "label":    "Wall Monitor",
    "url":      "/hub/wallboard",
    "icon":     "📺",
    "hub_only": True,
}

import time   as _time
import json   as _json
import os     as _os
import re     as _re

_BASE_DIR  = _os.path.dirname(_os.path.abspath(__file__))
_APP_DIR   = _os.path.dirname(_BASE_DIR)
_ALERT_LOG = _os.path.join(_APP_DIR, "alert_log.json")


def _load_alerts(limit=30):
    try:
        with open(_ALERT_LOG) as _f:
            data = _json.load(_f)
        data.sort(key=lambda e: e.get("time", 0), reverse=True)
        return data[:limit]
    except Exception:
        return []


def _dtype(device_index):
    """Return 'fm', 'dab', 'http', 'rtp', 'alsa', or 'other'."""
    d = (device_index or "").lower()
    if d.startswith("fm://"):     return "fm"
    if d.startswith("dab://"):    return "dab"
    if d.startswith("http"):      return "http"
    if d.startswith("rtp://"):    return "rtp"
    if d.startswith("alsa://"):   return "alsa"
    return "other"


def register(app, ctx):
    from flask import render_template_string, jsonify

    login_required = ctx["login_required"]
    hub_server     = ctx["hub_server"]
    monitor        = ctx["monitor"]
    BUILD          = ctx["BUILD"]

    @app.get("/hub/wallboard")
    @login_required
    def wallboard_page():
        return render_template_string(_WALL_TPL, BUILD=BUILD)

    @app.get("/api/wallboard/data")
    @login_required
    def wallboard_data():
        # ── gather site / stream data ──────────────────────────────────────────
        try:
            raw = (hub_server.get_sites()
                   if callable(getattr(hub_server, "get_sites", None))
                   else None)
            if raw is None:
                raw = list(getattr(hub_server, "_sites", {}).values())
        except Exception:
            raw = []

        now = _time.time()
        sites_out = []

        for sd in (raw or []):
            if not isinstance(sd, dict):
                continue
            if not sd.get("_approved"):
                continue

            site_name = sd.get("name") or sd.get("site", "")
            last_seen = sd.get("_received") or sd.get("last_seen", 0) or 0
            online    = (now - last_seen) < 30

            streams_out = []
            for s in (sd.get("streams") or []):
                if not isinstance(s, dict):
                    continue
                name  = (s.get("name") or "").strip()
                if not name:
                    continue
                di    = s.get("device_index", "")
                dt    = _dtype(di)

                # ── identity / now-playing ─────────────────────────────────
                if dt == "fm":
                    display_name = (s.get("fm_rds_ps") or "").strip() or name
                    now_playing  = (s.get("fm_rds_rt") or "").strip()
                    detail       = f"{di.replace('fm://','').strip()} MHz"
                    is_stereo    = bool(s.get("fm_stereo") or s.get("fm_stereo_blend", 0) > 0.3)
                    signal_info  = {
                        "snr_db":  s.get("fm_snr_db"),
                        "sig_dbm": s.get("fm_signal_dbm"),
                        "pilot":   s.get("fm_pilot_pct"),
                    }
                elif dt == "dab":
                    display_name = (s.get("dab_service") or "").strip() or name
                    now_playing  = (s.get("dab_dls") or "").strip()
                    detail       = f"DAB {s.get('dab_bitrate','') or ''} kbps".strip()
                    is_stereo    = bool(s.get("dab_stereo"))
                    signal_info  = {
                        "snr_db":  s.get("dab_snr"),
                        "sig_dbm": None,
                        "ber":     s.get("dab_ber"),
                    }
                else:
                    display_name = name
                    now_playing  = ""
                    detail       = dt.upper() if dt not in ("other","alsa") else ""
                    is_stereo    = bool(s.get("stereo"))
                    signal_info  = {}

                # If RDS RadioText is stale, clear it
                if dt == "fm":
                    stale = s.get("fm_rds_rt_stale_mins")
                    if stale is not None and stale > 5:
                        now_playing = ""

                streams_out.append({
                    "name":          name,
                    "display_name":  display_name,
                    "device_type":   dt,
                    "detail":        detail,
                    "now_playing":   now_playing,
                    "is_stereo":     is_stereo,
                    "signal":        signal_info,
                    # levels (fallback from heartbeat; live_levels overrides at 150 ms)
                    "level_dbfs":    s.get("level_dbfs"),
                    "peak_dbfs":     s.get("peak_dbfs"),
                    "level_dbfs_l":  s.get("level_dbfs_l"),
                    "level_dbfs_r":  s.get("level_dbfs_r"),
                    "silence":       bool(s.get("silence_active")),
                    "lufs_s":        s.get("lufs_s"),
                    "lufs_i":        s.get("lufs_i"),
                    "ai_status":     (s.get("ai_status") or "").strip(),
                    "sla_pct":       s.get("sla_pct"),
                    "glitch_recent": s.get("glitch_recent", 0),
                    "rds_ok":        s.get("fm_rds_ok"),
                })

            if streams_out:
                sites_out.append({
                    "name":    site_name,
                    "online":  online,
                    "age_s":   round(now - last_seen, 1),
                    "streams": streams_out,
                })

        sites_out.sort(key=lambda s: (0 if s["online"] else 1, s["name"].lower()))

        # ── alerts ────────────────────────────────────────────────────────────
        alerts_out = []
        for a in _load_alerts(25):
            atype = (a.get("type") or "").upper()
            alerts_out.append({
                "time":   a.get("time", 0),
                "site":   (a.get("site") or "").strip(),
                "stream": (a.get("stream") or "").strip(),
                "type":   atype,
                "msg":    (a.get("msg") or a.get("message") or atype).strip(),
                "ok":     atype in ("RECOVERY", "AUDIO_RESTORED", "CHAIN_OK"),
            })

        return jsonify({"ts": now, "sites": sites_out, "alerts": alerts_out})


# ─────────────────────────────────────────────────────────────────────────────
# HTML template
# ─────────────────────────────────────────────────────────────────────────────
_WALL_TPL = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<title>Wall Monitor — SignalScope</title>
<style nonce="{{csp_nonce()}}">
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
*{box-sizing:border-box;margin:0;padding:0;-webkit-user-select:none;user-select:none}
html,body{height:100%;overflow:hidden}
body{
  background:radial-gradient(circle at top,#12376f 0%,var(--bg) 38%,#05101f 100%);
  color:var(--tx);font-family:system-ui,sans-serif;font-size:13px;
  display:flex;flex-direction:column;
}

/* ── TOP BAR ─────────────────────────────────────────────────────────────── */
#topbar{
  flex-shrink:0;
  background:linear-gradient(180deg,rgba(10,31,65,.98),rgba(9,24,48,.98));
  border-bottom:1px solid var(--bor);
  padding:10px 20px;
  display:flex;align-items:center;justify-content:space-between;gap:16px;
}
#tb-left{display:flex;align-items:center;gap:14px}
#tb-title{font-size:15px;font-weight:800;color:var(--acc);letter-spacing:.08em;text-transform:uppercase}
#tb-subtitle{font-size:11px;color:var(--mu);letter-spacing:.05em}
#tb-clock{
  font-size:32px;font-weight:200;letter-spacing:.06em;
  font-variant-numeric:tabular-nums;color:var(--tx);
}
#tb-right{display:flex;align-items:center;gap:10px;flex-shrink:0}
.tb-badge{
  background:var(--sur);border:1px solid var(--bor);border-radius:6px;
  padding:4px 10px;font-size:10px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
}
.tb-badge.live{color:var(--ok);border-color:rgba(34,197,94,.35)}
.tb-badge.alert{color:var(--al);border-color:rgba(239,68,68,.35)}

/* ── GRID ────────────────────────────────────────────────────────────────── */
#grid{
  flex:1;overflow:hidden;
  padding:10px;
  display:grid;
  grid-template-columns:repeat(auto-fill,minmax(260px,1fr));
  grid-auto-rows:1fr;
  gap:9px;
  align-content:start;
}

/* ── CARD ────────────────────────────────────────────────────────────────── */
.card{
  background:var(--sur);border:1px solid var(--bor);border-radius:12px;
  overflow:hidden;display:flex;flex-direction:column;
  transition:border-color .4s,box-shadow .4s;
}
.card.live{border-color:rgba(34,197,94,.35);box-shadow:0 0 0 1px rgba(34,197,94,.08) inset}
.card.silent{
  border-color:rgba(239,68,68,.55);
  box-shadow:0 0 12px rgba(239,68,68,.12),0 0 0 1px rgba(239,68,68,.1) inset;
  animation:card-pulse 1.8s ease-in-out infinite;
}
.card.offline{border-color:rgba(139,164,200,.15);opacity:.5}
@keyframes card-pulse{
  0%,100%{box-shadow:0 0 12px rgba(239,68,68,.12),0 0 0 1px rgba(239,68,68,.1) inset}
  50%{box-shadow:0 0 22px rgba(239,68,68,.22),0 0 0 1px rgba(239,68,68,.15) inset}
}

/* card header */
.ch{
  padding:8px 12px;
  display:flex;align-items:center;gap:7px;
  background:linear-gradient(180deg,#143766,#102b54);
  border-bottom:1px solid var(--bor);
  flex-shrink:0;
}
.card.live .ch{background:linear-gradient(180deg,#0e3e1a,#0a2c13);border-bottom-color:rgba(34,197,94,.25)}
.card.silent .ch{background:linear-gradient(180deg,#3d0e0e,#2c0909);border-bottom-color:rgba(239,68,68,.3)}
.card.offline .ch{opacity:.6}

.dot{
  width:9px;height:9px;border-radius:50%;flex-shrink:0;
  background:var(--mu);
}
.card.live .dot{background:var(--ok);box-shadow:0 0 6px var(--ok);animation:dot-live 2.5s ease-in-out infinite}
.card.silent .dot{background:var(--al);animation:dot-al 1s ease-in-out infinite}
@keyframes dot-live{0%,100%{box-shadow:0 0 4px var(--ok)}50%{box-shadow:0 0 10px var(--ok),0 0 18px rgba(34,197,94,.25)}}
@keyframes dot-al{0%,100%{box-shadow:0 0 5px var(--al);opacity:1}50%{box-shadow:0 0 14px var(--al);opacity:.55}}

.ch-name{font-size:13px;font-weight:700;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ch-badge{
  font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.07em;
  padding:2px 7px;border-radius:4px;flex-shrink:0;
}
.badge-live{background:rgba(34,197,94,.15);color:var(--ok);border:1px solid rgba(34,197,94,.3)}
.badge-silent{background:rgba(239,68,68,.15);color:var(--al);border:1px solid rgba(239,68,68,.3);animation:blink .9s step-end infinite}
.badge-offline{background:rgba(139,164,200,.08);color:var(--mu);border:1px solid rgba(139,164,200,.2)}
.badge-type{background:rgba(23,168,255,.1);color:var(--acc);border:1px solid rgba(23,168,255,.2);margin-left:2px}
@keyframes blink{50%{opacity:0}}

/* meters section */
.meters{padding:9px 12px 6px;display:flex;flex-direction:column;gap:5px;flex-shrink:0}
.meter-row{display:flex;align-items:center;gap:6px}
.meter-lbl{font-size:9px;color:var(--mu);font-weight:700;width:12px;text-align:center;flex-shrink:0}
.meter-track{
  flex:1;height:11px;background:rgba(0,0,0,.45);border-radius:3px;overflow:hidden;
  border:1px solid rgba(255,255,255,.04);
}
.meter-fill{
  height:100%;border-radius:2px;
  transform-origin:left center;transform:scaleX(0);
  transition:transform .12s ease-out;
}
.fill-safe{background:linear-gradient(90deg,#166534,#22c55e)}
.fill-warm{background:linear-gradient(90deg,#92400e,#f59e0b)}
.fill-hot {background:linear-gradient(90deg,#991b1b,#ef4444)}
.meter-val{font-size:10px;font-variant-numeric:tabular-nums;color:var(--mu);width:42px;text-align:right;flex-shrink:0;font-weight:600}

/* silence overlay */
.silence-row{
  display:none;padding:5px 12px;
  background:rgba(239,68,68,.08);
  border-top:1px solid rgba(239,68,68,.2);
  text-align:center;font-size:10px;font-weight:800;
  color:var(--al);text-transform:uppercase;letter-spacing:.12em;flex-shrink:0;
}
.card.silent .silence-row{display:block}

/* now playing / metadata */
.meta{padding:7px 12px 7px;border-top:1px solid var(--bor);flex:1;min-height:0;display:flex;flex-direction:column;gap:3px}
.now-playing{
  font-size:12px;font-weight:600;color:var(--tx);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  min-height:16px;
}
.np-icon{margin-right:4px;opacity:.7}
.station-row{
  font-size:10px;color:var(--mu);
  display:flex;align-items:center;gap:6px;flex-wrap:wrap;
}
.stereo-tag{color:var(--acc);font-size:9px;font-weight:700;letter-spacing:.06em}
.sig-good{color:var(--ok)}
.sig-warn{color:var(--wn)}
.sig-bad {color:var(--al)}

/* stats footer */
.card-ft{
  padding:5px 12px;background:rgba(0,0,0,.25);
  border-top:1px solid var(--bor);
  display:flex;align-items:center;justify-content:space-between;gap:8px;
  flex-shrink:0;
}
.ft-l,.ft-r{font-size:9px;color:var(--mu);display:flex;align-items:center;gap:6px}
.ft-sla-ok{color:var(--ok)}
.ft-sla-warn{color:var(--wn)}
.ft-sla-bad{color:var(--al)}
.ft-ai-ok{color:var(--ok)}
.ft-ai-bad{color:var(--al)}
.ft-lufs{font-variant-numeric:tabular-nums}

/* OFFLINE overlay */
.offline-over{
  display:none;padding:16px;text-align:center;
  color:var(--mu);font-size:11px;flex:1;align-items:center;justify-content:center;
}
.card.offline .offline-over{display:flex}
.card.offline .meters,.card.offline .meta,.card.offline .card-ft{opacity:.25}

/* ── ALERTS TICKER ───────────────────────────────────────────────────────── */
#ticker-bar{
  flex-shrink:0;
  background:rgba(4,12,26,.97);
  border-top:1px solid var(--bor);
  padding:5px 0;
  display:flex;align-items:center;gap:0;overflow:hidden;
  height:28px;
}
#ticker-label{
  background:var(--wn);color:#000;
  font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.08em;
  padding:3px 10px;flex-shrink:0;height:100%;
  display:flex;align-items:center;
}
#ticker-scroll{flex:1;overflow:hidden;position:relative;height:18px}
#ticker-inner{
  position:absolute;top:0;left:0;
  white-space:nowrap;
  display:flex;align-items:center;gap:0;
  animation:scroll-ticker 40s linear infinite;
}
@keyframes scroll-ticker{0%{transform:translateX(0)}100%{transform:translateX(-50%)}}
.tick-item{
  font-size:11px;color:var(--mu);
  padding-right:50px;
  display:inline-flex;align-items:center;gap:5px;
}
.tick-time{color:var(--wn);font-variant-numeric:tabular-nums;font-weight:600}
.tick-site{font-weight:600;color:var(--tx)}
.tick-al{color:var(--al)}
.tick-ok{color:var(--ok)}
.tick-sep{color:var(--bor);padding:0 8px}

/* ── No-data state ───────────────────────────────────────────────────────── */
.no-data{
  grid-column:1/-1;display:flex;flex-direction:column;
  align-items:center;justify-content:center;gap:14px;
  padding:40px;color:var(--mu);font-size:13px;
}
.no-data-icon{font-size:52px;opacity:.35}
</style>
</head>
<body>

<!-- TOP BAR -->
<div id="topbar">
  <div id="tb-left">
    <span id="tb-title">📡 Wall Monitor</span>
    <span id="tb-subtitle">SignalScope</span>
  </div>
  <div id="tb-clock">--:--:--</div>
  <div id="tb-right">
    <span class="tb-badge" id="tb-streams">-- streams</span>
    <span class="tb-badge" id="tb-silent" style="display:none"></span>
    <span class="tb-badge live">LIVE</span>
  </div>
</div>

<!-- STREAM GRID -->
<div id="grid">
  <div class="no-data">
    <div class="no-data-icon">📡</div>
    <div>Connecting to SignalScope…</div>
  </div>
</div>

<!-- ALERTS TICKER -->
<div id="ticker-bar">
  <div id="ticker-label">ALERTS</div>
  <div id="ticker-scroll">
    <div id="ticker-inner"><span class="tick-item">No recent alerts</span></div>
  </div>
</div>

<script nonce="{{csp_nonce()}}">
(function(){
'use strict';

// ── utilities ─────────────────────────────────────────────────────────────
function esc(s){return(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function lkey(site,stream){return(site+'|'+stream).replace(/[^a-zA-Z0-9|]/g,'_')}
var FLOOR=-60;
function dbFrac(db){
  if(db==null)return 0;
  return Math.max(0,Math.min(1,(db-FLOOR)/(0-FLOOR)));
}
function dbClass(db){
  if(db==null||db<-15)return'fill-safe';
  if(db<-6)return'fill-warm';
  return'fill-hot';
}
function fmtDb(db){return db!=null?db.toFixed(1):'--'}
function fmtTime(ts){
  var d=new Date(ts*1000);
  return('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2);
}
function fmtAge(s){
  if(s<60)return Math.round(s)+'s';
  if(s<3600)return Math.round(s/60)+'m';
  return Math.round(s/3600)+'h';
}

// ── clock ─────────────────────────────────────────────────────────────────
function tick(){
  var d=new Date();
  document.getElementById('tb-clock').textContent=
    ('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2)+':'+('0'+d.getSeconds()).slice(-2);
}
setInterval(tick,1000);tick();

// ── state ─────────────────────────────────────────────────────────────────
var _meta={};       // lkey → {silence,stereo,device_type,...}
var _domKeys=[];    // current ordered list of lkey values in DOM

// ── card builder ──────────────────────────────────────────────────────────
function meterRow(k,ch){
  var sfx=ch?'_'+ch:'';
  return '<div class="meter-row">'
    +'<div class="meter-lbl">'+esc(ch)+'</div>'
    +'<div class="meter-track"><div class="meter-fill fill-safe" id="mf_'+k+sfx+'" style="transform:scaleX(0)"></div></div>'
    +'<div class="meter-val" id="mv_'+k+sfx+'">--</div>'
    +'</div>';
}

function typeIcon(dt){
  if(dt==='fm')return'📻';
  if(dt==='dab')return'📡';
  if(dt==='http'||dt==='rtp')return'🌐';
  return'🔊';
}

function buildCard(site,s){
  var k=lkey(site.name,s.name);
  var state=!site.online?'offline':(s.silence?'silent':'live');
  var badge=state==='live'?'<span class="ch-badge badge-live">ON AIR</span>'
    :state==='silent'?'<span class="ch-badge badge-silent">SILENCE</span>'
    :'<span class="ch-badge badge-offline">OFFLINE</span>';
  var tbadge='<span class="ch-badge badge-type">'+esc(s.device_type.toUpperCase())+'</span>';

  // meters
  var meters='';
  if(s.is_stereo&&s.device_type==='fm'){
    meters=meterRow(k,'L')+meterRow(k,'R');
  } else {
    meters=meterRow(k,'');
  }

  // now playing
  var npIcon=typeIcon(s.device_type);
  var npText=s.now_playing?'<span class="np-icon">'+npIcon+'</span>'+esc(s.now_playing)
    :(s.display_name!==s.name?'<span style="opacity:.6">'+esc(s.display_name)+'</span>':'');

  // station row
  var stRow='';
  if(s.detail) stRow+=esc(s.detail);
  if(s.is_stereo) stRow+=(stRow?'<span style="opacity:.35"> · </span>':'')+'<span class="stereo-tag">STEREO</span>';
  if(s.signal){
    var snr=s.signal.snr_db;
    if(snr!=null){
      var sc=snr>20?'sig-good':snr>10?'sig-warn':'sig-bad';
      stRow+='<span class="'+sc+'">'+snr.toFixed(0)+'dB</span>';
    }
  }

  // stats footer
  var sla='';
  if(s.sla_pct!=null){
    var sc2=s.sla_pct>99?'ft-sla-ok':s.sla_pct>95?'ft-sla-warn':'ft-sla-bad';
    sla='<span class="'+sc2+'" title="SLA">'+s.sla_pct.toFixed(1)+'%</span>';
  }
  var lufs='<span class="ft-lufs" id="lufs_'+k+'">'+(s.lufs_s!=null?s.lufs_s.toFixed(1)+' LUFS':'')+'</span>';
  var ai='';
  if(s.ai_status){
    var ok=s.ai_status.toLowerCase().indexOf('norm')>=0||s.ai_status==='ok';
    ai='<span class="'+(ok?'ft-ai-ok':'ft-ai-bad')+'" title="AI Monitor">🤖 '+esc(s.ai_status)+'</span>';
  }

  return '<div class="card '+state+'" id="card_'+k+'" data-key="'+k+'">'
    +'<div class="ch">'
    +'<div class="dot"></div>'
    +'<div class="ch-name" title="'+esc(s.name)+'">'+esc(s.display_name||s.name)+'</div>'
    +badge+tbadge
    +'</div>'
    +'<div class="meters">'+meters+'</div>'
    +'<div class="silence-row">⚠ SILENCE DETECTED</div>'
    +'<div class="meta">'
    +'<div class="now-playing" id="np_'+k+'">'+npText+'</div>'
    +'<div class="station-row" id="sr_'+k+'">'+stRow+'</div>'
    +'</div>'
    +'<div class="card-ft">'
    +'<div class="ft-l" id="ftl_'+k+'">'+sla+lufs+'</div>'
    +'<div class="ft-r" id="ftr_'+k+'">'+ai+'<span id="fts_'+k+'" style="opacity:.5">'+esc(site.name)+'</span></div>'
    +'</div>'
    +'<div class="offline-over">No signal from<br><b>'+esc(site.name)+'</b><br><small>Last seen '+fmtAge(site.age_s)+' ago</small></div>'
    +'</div>';
}

// ── update a single level bar ─────────────────────────────────────────────
function setBar(k,ch,db){
  var sfx=ch?'_'+ch:'';
  var fill=document.getElementById('mf_'+k+sfx);
  var val =document.getElementById('mv_'+k+sfx);
  if(!fill)return;
  var frac=dbFrac(db);
  fill.style.transform='scaleX('+frac+')';
  fill.className='meter-fill '+dbClass(db);
  if(val)val.textContent=fmtDb(db);
}

// ── metadata poll (every 4 s) ─────────────────────────────────────────────
var _silentCount=0;

function refreshMeta(){
  fetch('/api/wallboard/data',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      var grid=document.getElementById('grid');
      var newKeys=[];
      var allStreams=[];
      _meta={};
      _silentCount=0;

      (d.sites||[]).forEach(function(site){
        (site.streams||[]).forEach(function(s){
          var k=lkey(site.name,s.name);
          newKeys.push(k);
          _meta[k]=s;
          if(s.silence&&site.online)_silentCount++;
          allStreams.push({site:site,stream:s,key:k});
        });
      });

      // rebuild grid only if card set changed
      var changed=newKeys.join(',')!==_domKeys.join(',');
      if(changed){
        _domKeys=newKeys;
        var html='';
        allStreams.forEach(function(x){html+=buildCard(x.site,x.stream);});
        grid.innerHTML=html||'<div class="no-data"><div class="no-data-icon">📡</div><div>No streams connected</div></div>';
      } else {
        // in-place update: card state, now-playing, stats
        allStreams.forEach(function(x){
          var site=x.site,s=x.stream,k=x.key;
          var card=document.getElementById('card_'+k);
          if(!card)return;
          var state=!site.online?'offline':(s.silence?'silent':'live');
          card.className='card '+state;
          // now playing
          var npEl=document.getElementById('np_'+k);
          if(npEl){
            var npIcon=typeIcon(s.device_type);
            npEl.innerHTML=s.now_playing?'<span class="np-icon">'+npIcon+'</span>'+esc(s.now_playing)
              :(s.display_name!==s.name?'<span style="opacity:.6">'+esc(s.display_name)+'</span>':'');
          }
          // LUFS
          var lufsEl=document.getElementById('lufs_'+k);
          if(lufsEl)lufsEl.textContent=s.lufs_s!=null?s.lufs_s.toFixed(1)+' LUFS':'';
          // heartbeat levels (overridden by livePoll if active)
          if(!_liveActive&&s.level_dbfs!=null){
            if(s.is_stereo&&(s.level_dbfs_l!=null)){
              setBar(k,'L',s.level_dbfs_l);
              setBar(k,'R',s.level_dbfs_r);
            } else {
              setBar(k,'',s.level_dbfs);
            }
          }
        });
      }

      // header badges
      var total=newKeys.length;
      document.getElementById('tb-streams').textContent=total+' stream'+(total!==1?'s':'');
      var silBadge=document.getElementById('tb-silent');
      if(_silentCount>0){
        silBadge.textContent=_silentCount+' SILENT';
        silBadge.style.display='';
        silBadge.className='tb-badge alert';
      } else {
        silBadge.style.display='none';
      }

      // alerts ticker
      buildTicker(d.alerts||[]);
    })
    .catch(function(){});
}

// ── live levels (every 150 ms) ────────────────────────────────────────────
var _liveActive=false;

function livePoll(){
  fetch('/api/hub/live_levels',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(data){
      _liveActive=true;
      Object.keys(data).forEach(function(k){
        var ld=data[k];
        var st=_meta[k];
        if(!st)return;
        if(st.is_stereo&&ld.level_dbfs_l!=null){
          setBar(k,'L',ld.level_dbfs_l);
          setBar(k,'R',ld.level_dbfs_r);
        } else {
          setBar(k,'',ld.level_dbfs);
        }
        // live silence state
        if(ld.silence_active!==undefined){
          var card=document.getElementById('card_'+k);
          if(card&&card.className.indexOf('offline')<0){
            card.className='card '+(ld.silence_active?'silent':'live');
          }
        }
      });
    })
    .catch(function(){});
}

// ── ticker builder ────────────────────────────────────────────────────────
function buildTicker(alerts){
  var el=document.getElementById('ticker-inner');
  if(!alerts||!alerts.length){
    el.innerHTML='<span class="tick-item">No recent alerts</span>';
    return;
  }
  var items=alerts.slice(0,15).map(function(a){
    var cls=a.ok?'tick-ok':'tick-al';
    var site=a.site||(a.stream?'':'-');
    var label=site+(a.stream?' · '+a.stream:'');
    var msg=a.msg||a.type;
    return '<span class="tick-item">'
      +'<span class="tick-time">'+fmtTime(a.time)+'</span>'
      +'<span class="tick-site">'+esc(label)+'</span>'
      +'<span class="tick-sep">—</span>'
      +'<span class="'+cls+'">'+esc(msg)+'</span>'
      +'</span>';
  });
  // duplicate for seamless loop
  var all=items.concat(items).join('');
  el.innerHTML=all;
  // Recalculate animation duration based on content width
  el.style.animation='none';
  el.offsetWidth; // force reflow
  var w=el.scrollWidth/2;
  var dur=Math.max(20,w/60);  // ~60px/s scroll speed
  el.style.animation='scroll-ticker '+dur+'s linear infinite';
}

// ── boot ──────────────────────────────────────────────────────────────────
refreshMeta();
setInterval(refreshMeta,4000);
setInterval(livePoll,150);

})();
</script>
</body>
</html>"""
