# meterwall.py — Full-screen Meter Wall plugin for SignalScope
# Drop alongside signalscope.py; auto-discovered on next start.

SIGNALSCOPE_PLUGIN = {
    "id":       "meterwall",
    "label":    "Meter Wall",
    "url":      "/hub/meterwall",
    "icon":     "📊",
    "hub_only": False,
    "version":  "1.1.1",
}


def register(app, ctx):
    login_required = ctx["login_required"]
    monitor        = ctx["monitor"]
    hub_server     = ctx.get("hub_server")
    BUILD          = ctx["BUILD"]

    from flask import jsonify, render_template_string

    # ── Page ─────────────────────────────────────────────────────────────────
    @app.get("/hub/meterwall")
    @login_required
    def meterwall_page():
        return render_template_string(_METERWALL_TPL, build=BUILD)

    # ── Live data ─────────────────────────────────────────────────────────────
    @app.get("/api/meterwall/data")
    @login_required
    def meterwall_data():
        cfg = monitor.app_cfg
        sites_out = []

        # Hub / both — remote sites via heartbeat cache
        if hub_server and cfg.hub.mode in ("hub", "both"):
            for s in hub_server.get_sites():
                streams_out = []
                for st in s.get("streams", []):
                    np_ = (st.get("fm_rds_ps") or st.get("dab_dls") or "")
                    streams_out.append({
                        "name":         st.get("name", "?"),
                        "level_dbfs":   st.get("level_dbfs", -90.0),
                        "peak_dbfs":    st.get("peak_dbfs", -90.0),
                        "ai_status":    st.get("ai_status", ""),
                        "rtp_loss_pct": st.get("rtp_loss_pct", 0.0),
                        "lufs_i":       st.get("lufs_i", -70.0),
                        "now_playing":  np_[:40] if np_ else "",
                    })
                sites_out.append({
                    "site":    s.get("site", "?"),
                    "online":  s.get("online", False),
                    "streams": streams_out,
                })

        # Client / standalone / both — gather local monitor inputs
        if cfg.hub.mode in ("client", "standalone", "both"):
            try:
                inputs = (getattr(monitor, "inputs",  None)
                          or getattr(monitor, "_inputs", None) or [])
                local = []
                for inp in inputs:
                    np_ = (getattr(inp, "_fm_rds_ps", "")
                           or getattr(inp, "_dab_dls",   "") or "")
                    local.append({
                        "name":         getattr(inp, "name", "?"),
                        "level_dbfs":   round(getattr(inp, "_last_level_dbfs", -90.0), 1),
                        "peak_dbfs":    round(getattr(inp, "_last_peak_dbfs",  -90.0), 1),
                        "ai_status":    getattr(inp, "_ai_status",    ""),
                        "rtp_loss_pct": round(getattr(inp, "_rtp_loss_pct", 0.0), 1),
                        "lufs_i":       round(getattr(inp, "_lufs_i", -70.0), 1),
                        "now_playing":  np_[:40] if np_ else "",
                    })
                if local:
                    label = cfg.hub.site_name or "Local"
                    if label not in {s["site"] for s in sites_out}:
                        sites_out.insert(0, {
                            "site":    label,
                            "online":  True,
                            "streams": local,
                        })
            except Exception:
                pass

        return jsonify({"sites": sites_out})


# ── Template ──────────────────────────────────────────────────────────────────
_METERWALL_TPL = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="csrf-token" content="{{csrf_token()}}">
<title>Meter Wall — SignalScope</title>
<style nonce="{{csp_nonce()}}">
:root{
  --bg:#07142b;--sur:#0d2346;--bor:#17345f;
  --acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;
  --tx:#eef5ff;--mu:#8aa4c8;
  --mc-w:140px;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;overflow:hidden}
body{
  font-family:system-ui,sans-serif;
  background:var(--bg);color:var(--tx);font-size:13px;
  display:flex;flex-direction:column;
}

/* ── Header ────────────────────────────────────────────────────────────── */
#mw-hdr{
  flex-shrink:0;
  padding:7px 14px;
  background:linear-gradient(180deg,rgba(10,31,65,.97),rgba(9,24,48,.97));
  border-bottom:1px solid var(--bor);
  display:flex;align-items:center;gap:10px;flex-wrap:wrap;
  transition:opacity .4s;z-index:10;
}
#mw-hdr.hide{opacity:0;pointer-events:none}
.mw-title{font-size:15px;font-weight:800;letter-spacing:.02em}
.mw-meta{font-size:11px;color:var(--mu)}
.mw-ctrl{margin-left:auto;display:flex;gap:7px;align-items:center;flex-wrap:wrap}
.btn{
  display:inline-flex;align-items:center;gap:4px;
  padding:4px 10px;border-radius:7px;font-size:12px;font-weight:600;
  cursor:pointer;border:none;color:var(--tx);background:var(--bor);
  text-decoration:none;transition:filter .12s,background .12s;
  white-space:nowrap;
}
.btn:hover{filter:brightness(1.2)}
.btn.bp{background:var(--acc);color:#fff}
.btn.active{background:var(--acc);color:#fff}
#mw-clock{
  font-size:14px;font-weight:700;font-variant-numeric:tabular-nums;
  color:var(--tx);min-width:64px;text-align:right;
}
#mw-alert-badge{
  background:var(--al);color:#fff;border-radius:999px;
  padding:2px 9px;font-size:11px;font-weight:700;display:none;
  animation:badge-pulse 1.2s ease-in-out infinite;
}
#mw-alert-badge.show{display:inline-block}
@keyframes badge-pulse{0%,100%{opacity:1}50%{opacity:.65}}

/* ── Scroll area ──────────────────────────────────────────────────────── */
#mw-scroll{flex:1;overflow-y:auto;overflow-x:hidden;padding:12px 12px 20px}

/* ── Site section ─────────────────────────────────────────────────────── */
.mw-site{margin-bottom:14px}
.mw-site-hdr{
  display:flex;align-items:center;gap:7px;
  font-size:10px;font-weight:700;color:var(--mu);
  text-transform:uppercase;letter-spacing:.09em;
  margin-bottom:8px;padding-bottom:4px;border-bottom:1px solid var(--bor);
}
.mw-sdot{width:7px;height:7px;border-radius:50%;background:var(--ok);flex-shrink:0}
.mw-sdot.off{background:var(--al)}

/* ── Meter grid ───────────────────────────────────────────────────────── */
.mw-grid{
  display:grid;gap:8px;
  grid-template-columns:repeat(auto-fill,minmax(var(--mc-w),1fr));
}

/* ── Meter card ───────────────────────────────────────────────────────── */
.mc{
  background:var(--sur);border:1px solid var(--bor);
  border-radius:10px;overflow:hidden;
  display:flex;flex-direction:column;min-width:0;
  transition:border-color .2s,box-shadow .2s;
}
.mc.mc-alert{
  border-color:var(--al);
  box-shadow:0 0 0 2px rgba(239,68,68,.28);
  animation:mc-pulse 1.3s ease-in-out infinite;
}
.mc.mc-warn{border-color:var(--wn);box-shadow:0 0 0 2px rgba(245,158,11,.2)}
.mc.mc-offline{opacity:.45;filter:grayscale(.4)}
@keyframes mc-pulse{
  0%,100%{box-shadow:0 0 0 2px rgba(239,68,68,.28)}
  50%{box-shadow:0 0 0 6px rgba(239,68,68,.18)}
}

/* Card name strip */
.mc-head{
  padding:6px 9px 5px;background:rgba(0,0,0,.2);
  border-bottom:1px solid rgba(255,255,255,.05);
}
.mc-name{
  font-size:11px;font-weight:700;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  line-height:1.3;
}
.mc-sub{
  font-size:9px;color:var(--mu);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  line-height:1.2;margin-top:1px;
}

/* Meter body */
.mc-body{
  flex:1;display:flex;flex-direction:column;
  align-items:center;padding:10px 10px 6px;gap:6px;
}

/* Vertical bar track */
.mtr-wrap{
  position:relative;width:100%;max-width:52px;
  flex:1;min-height:130px;
  /* Faint zone tints on empty scale */
  background:linear-gradient(to top,
    rgba(34,197,94,.1)  0%   77.5%,
    rgba(245,158,11,.1) 77.5% 88.75%,
    rgba(239,68,68,.1)  88.75% 100%
  );
  border-radius:4px;overflow:hidden;
}
/* Zone colour band along right edge */
.mtr-wrap::after{
  content:'';position:absolute;top:0;right:0;bottom:0;width:3px;
  background:linear-gradient(to top,
    #22c55e 0% 77.5%,
    #f59e0b 77.5% 88.75%,
    #ef4444 88.75% 100%
  );
  opacity:.35;pointer-events:none;
}
/* Gradient fill — colour is baked into gradient so it shifts as level rises */
.mtr-fill{
  position:absolute;bottom:0;left:0;right:0;
  background:linear-gradient(to top,
    #22c55e 0%    77.5%,
    #f59e0b 77.5% 88.75%,
    #ef4444 88.75% 100%
  );
  /* no CSS transition — rAF loop handles smooth animation */
  border-radius:4px 4px 0 0;
}
/* Peak hold line */
.mtr-peak{
  position:absolute;left:-1px;right:-1px;height:2px;
  background:#fff;border-radius:1px;opacity:.82;
  /* no CSS transition — rAF loop handles smooth animation */
}

/* Level readout */
.mc-lev{
  font-size:13px;font-weight:700;font-variant-numeric:tabular-nums;
  color:var(--tx);letter-spacing:-.01em;min-width:70px;text-align:center;
}
.mc-lev.lc-low{color:var(--mu)}
.mc-lev.lc-warn{color:var(--wn)}
.mc-lev.lc-alert{color:var(--al)}

/* LUFS-I row */
.mc-lufs{font-size:10px;color:var(--mu);text-align:center}

/* Now Playing */
.mc-np{
  font-size:10px;color:var(--acc);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  max-width:100%;padding:0 8px;text-align:center;
}

/* Status footer */
.mc-foot{
  display:flex;align-items:center;justify-content:space-between;
  padding:4px 9px 7px;gap:4px;
}
.sp{
  display:inline-flex;align-items:center;gap:3px;
  padding:2px 7px;border-radius:999px;
  font-size:10px;font-weight:700;line-height:1.4;
}
.sp-ok{background:rgba(34,197,94,.14);color:var(--ok)}
.sp-al{background:rgba(239,68,68,.18);color:var(--al)}
.sp-wn{background:rgba(245,158,11,.15);color:var(--wn)}
.sp-si{background:var(--bor);color:var(--mu)}
.mc-rtp{font-size:10px;color:var(--wn)}

/* Empty state */
.mw-empty{
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  height:220px;gap:10px;color:var(--mu);
}
.mw-empty-ico{font-size:52px;opacity:.25}

/* Fullscreen: hide scrollbar chrome */
:fullscreen #mw-scroll,:-webkit-full-screen #mw-scroll{padding:14px}
</style>
<link rel="icon" type="image/x-icon" href="/static/signalscope_icon.png">
</head>
<body>

<div id="mw-hdr">
  <span class="mw-title">📊 Meter Wall</span>
  <span class="mw-meta" id="mw-meta">Loading…</span>
  <span id="mw-alert-badge">⚠ ALERTS</span>
  <div class="mw-ctrl">
    <span style="font-size:11px;color:var(--mu)">Size</span>
    <button class="btn" id="btn-sm" title="Compact (key: 1)">S</button>
    <button class="btn active" id="btn-md" title="Normal (key: 2)">M</button>
    <button class="btn" id="btn-lg" title="Large (key: 3)">L</button>
    <button class="btn" id="btn-sort" title="Sort by level (key: S)">↕ Level</button>
    <a class="btn" href="/">⌂ Dashboard</a>
    <button class="btn bp" id="btn-fs" title="Fullscreen (key: F)">⛶ Full</button>
    <span id="mw-clock">--:--:--</span>
  </div>
</div>

<div id="mw-scroll">
  <div id="mw-root"></div>
</div>

<script nonce="{{csp_nonce()}}">
(function(){
  /* ── Config ─────────────────────────────────────────────────────────────── */
  var POLL_MS      = 1000;
  var LIVE_MS      = 150;    // fast-poll interval for live level bars
  var PEAK_HOLD    = 2500;   // ms to hold peak before decay starts
  var PEAK_RATE    = 0.45;   // dBFS decay per 100 ms after hold expires
  var DB_FLOOR     = -80.0;  // lower bound of meter scale
  var ATTACK_RATE  = 600;    // dBFS/s — fast attack (practically instant)
  var DECAY_RATE   = 30;     // dBFS/s — smooth decay (~2 s over full 60 dB range)

  /* ── State ──────────────────────────────────────────────────────────────── */
  var _peaks      = {};      // key → {val, ts}
  var _sortLev    = false;
  var _curSize    = 'md';
  var _lastData   = null;
  var _liveActive = false;   // true once /api/hub/live_levels responds successfully
  var _sizes      = {sm: 100, md: 140, lg: 200};
  var _targetLev  = {};      // key → raw dBFS from live poll
  var _dispLev    = {};      // key → currently displayed dBFS (smoothed)
  var _rafTs      = null;    // timestamp of last rAF frame

  /* ── Clock ──────────────────────────────────────────────────────────────── */
  function _tick() {
    var d = new Date(), h = d.getHours(), m = d.getMinutes(), s = d.getSeconds();
    document.getElementById('mw-clock').textContent =
      (h < 10 ? '0' : '') + h + ':' +
      (m < 10 ? '0' : '') + m + ':' +
      (s < 10 ? '0' : '') + s;
  }
  setInterval(_tick, 1000); _tick();

  /* ── Size control ───────────────────────────────────────────────────────── */
  function setSize(sz) {
    _curSize = sz;
    document.documentElement.style.setProperty('--mc-w', _sizes[sz] + 'px');
    ['sm', 'md', 'lg'].forEach(function(s) {
      document.getElementById('btn-' + s).classList.toggle('active', s === sz);
    });
    try { localStorage.setItem('mw_size', sz); } catch(e) {}
  }

  /* ── Sort ───────────────────────────────────────────────────────────────── */
  function toggleSort() {
    _sortLev = !_sortLev;
    document.getElementById('btn-sort').classList.toggle('active', _sortLev);
    if (_lastData) render(_lastData);
  }

  /* ── Fullscreen ─────────────────────────────────────────────────────────── */
  function toggleFs() {
    var root = document.documentElement;
    if (!document.fullscreenElement) {
      (root.requestFullscreen || root.webkitRequestFullscreen || function(){}).call(root);
    } else {
      (document.exitFullscreen || document.webkitExitFullscreen || function(){}).call(document);
    }
  }
  var _hideTimer = null;
  function _resetHide() {
    clearTimeout(_hideTimer);
    document.getElementById('mw-hdr').classList.remove('hide');
    _hideTimer = setTimeout(function() {
      document.getElementById('mw-hdr').classList.add('hide');
    }, 4000);
  }
  document.addEventListener('fullscreenchange', function() {
    var inFs = !!document.fullscreenElement;
    document.getElementById('btn-fs').textContent = inFs ? '✕ Exit' : '⛶ Full';
    if (inFs) {
      _resetHide();
      document.addEventListener('mousemove', _resetHide);
    } else {
      clearTimeout(_hideTimer);
      document.getElementById('mw-hdr').classList.remove('hide');
      document.removeEventListener('mousemove', _resetHide);
    }
  });

  /* ── Level helpers ──────────────────────────────────────────────────────── */
  function levToH(db) {
    return Math.max(0, Math.min(100, (db - DB_FLOOR) / (-DB_FLOOR) * 100));
  }
  function fmtLev(db) {
    if (db <= DB_FLOOR) return '— dB';
    return (db >= 0 ? '+' : '') + db.toFixed(1) + ' dB';
  }
  function levCls(db) {
    if (db >= -9)  return 'lc-alert';
    if (db >= -18) return 'lc-warn';
    if (db <= -60) return 'lc-low';
    return '';
  }
  function _esc(s) {
    return (s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;')
                    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  /* ── Peak tracking ──────────────────────────────────────────────────────── */
  function _updatePeak(key, lev, now) {
    var pk = _peaks[key] || {val: DB_FLOOR, ts: 0};
    if (lev >= pk.val) {
      _peaks[key] = {val: lev, ts: now};
    } else {
      var elapsed = now - pk.ts;
      if (elapsed > PEAK_HOLD) {
        var decay = PEAK_RATE * (elapsed - PEAK_HOLD) / 100;
        _peaks[key] = {val: Math.max(DB_FLOOR, pk.val - decay), ts: pk.ts};
      } else {
        _peaks[key] = pk;
      }
    }
    return _peaks[key].val;
  }

  /* ── Card creation ──────────────────────────────────────────────────────── */
  function buildCard(key, st, site) {
    var el = document.createElement('div');
    el.className = 'mc';
    el.dataset.key = key;
    el.innerHTML =
      '<div class="mc-head">'
      + '<div class="mc-name" title="' + _esc(st.name) + '">' + _esc(st.name) + '</div>'
      + '<div class="mc-sub">' + _esc(site) + '</div>'
      + '</div>'
      + '<div class="mc-body">'
      +   '<div class="mtr-wrap">'
      +     '<div class="mtr-fill" style="height:0%"></div>'
      +     '<div class="mtr-peak" style="bottom:0%;opacity:0"></div>'
      +   '</div>'
      +   '<div class="mc-lev lc-low">— dB</div>'
      +   '<div class="mc-lufs">LUFS-I —</div>'
      + '</div>'
      + '<div class="mc-np"></div>'
      + '<div class="mc-foot"><span class="sp sp-si">—</span><span class="mc-rtp"></span></div>';
    return el;
  }

  function updateCard(el, st, site, peak) {
    var lev     = st.level_dbfs;
    var isAlert = (st.ai_status || '').indexOf('[ALERT]') >= 0;
    var isWarn  = (st.ai_status || '').indexOf('[WARN]')  >= 0;

    el.classList.toggle('mc-alert',  isAlert);
    el.classList.toggle('mc-warn',   !isAlert && isWarn);
    el.classList.toggle('mc-offline', !true); // online always true for local; site handles it

    /* Bar fill / peak / level text — feed _targetLev so the rAF loop animates
       smoothly. When _liveActive the faster livePoll() is already updating
       _targetLev at 150 ms; skip here to avoid overwriting with stale data. */
    if (!_liveActive) {
      var key = el.dataset.key;
      if (key) {
        _targetLev[key] = lev;
        _updatePeak(key, lev, Date.now());
      }
    }

    /* LUFS-I */
    var lufsEl = el.querySelector('.mc-lufs');
    if (lufsEl) {
      var li = st.lufs_i;
      lufsEl.textContent = (li && li > -70) ? 'LUFS-I ' + li.toFixed(1) : 'LUFS-I —';
    }

    /* Now Playing */
    var npEl = el.querySelector('.mc-np');
    if (npEl) npEl.textContent = st.now_playing || '';

    /* Status pill */
    var sp = el.querySelector('.sp');
    if (sp) {
      if (isAlert) {
        sp.className = 'sp sp-al'; sp.textContent = '⚠ ALERT';
      } else if (isWarn) {
        sp.className = 'sp sp-wn'; sp.textContent = '⚡ WARN';
      } else if (lev <= -60) {
        sp.className = 'sp sp-si'; sp.textContent = '◎ SILENT';
      } else {
        sp.className = 'sp sp-ok'; sp.textContent = '● OK';
      }
    }

    /* RTP loss */
    var rtpEl = el.querySelector('.mc-rtp');
    if (rtpEl) {
      var rtp = st.rtp_loss_pct || 0;
      if (rtp > 0) {
        rtpEl.textContent = rtp.toFixed(1) + '% loss';
        rtpEl.style.color = rtp >= 2 ? 'var(--al)' : 'var(--wn)';
      } else {
        rtpEl.textContent = '';
      }
    }
  }

  /* ── Render ─────────────────────────────────────────────────────────────── */
  function render(data) {
    _lastData = data;
    var now   = Date.now();
    var root  = document.getElementById('mw-root');

    /* Flatten for sort / count */
    var flat = [];
    (data.sites || []).forEach(function(site) {
      (site.streams || []).forEach(function(st) {
        flat.push({st: st, siteName: site.site, online: site.online});
      });
    });

    if (_sortLev) {
      flat.sort(function(a, b) { return b.st.level_dbfs - a.st.level_dbfs; });
    }

    /* Build keyed index of existing cards */
    var existing = {};
    root.querySelectorAll('.mc').forEach(function(el) { existing[el.dataset.key] = el; });
    var seen = {};

    if (_sortLev) {
      /* Flat layout — one grid, cards ordered by level */
      var flatSec = root.querySelector('.mw-site.mw-flat');
      if (!flatSec) {
        root.innerHTML = '';
        flatSec = _mkSec('mw-site mw-flat', null);
        root.appendChild(flatSec);
      }
      var mg = flatSec.querySelector('.mw-grid');
      flat.forEach(function(item) {
        var key = item.siteName + '|' + item.st.name;
        seen[key] = true;
        var pk = _updatePeak(key, item.st.level_dbfs, now);
        var card = existing[key];
        if (!card) { card = buildCard(key, item.st, item.siteName); }
        if (card.parentElement !== mg) mg.appendChild(card);
        updateCard(card, item.st, item.siteName, pk);
      });
    } else {
      /* Grouped by site */
      /* Remove flat layout if switching back */
      root.querySelectorAll('.mw-flat').forEach(function(el) { el.remove(); });

      var siteOrder = (data.sites || []).map(function(s) { return s.site; });

      /* Prune removed sites */
      root.querySelectorAll('.mw-site[data-site]').forEach(function(el) {
        if (siteOrder.indexOf(el.dataset.site) === -1) el.remove();
      });

      /* Create / update each site section */
      var siteMap = {};
      (data.sites || []).forEach(function(s) { siteMap[s.site] = s; });

      siteOrder.forEach(function(siteName) {
        var site = siteMap[siteName];
        var sid  = 'mws-' + siteName.replace(/[^a-z0-9]/gi, '_');
        var sec  = document.getElementById(sid);
        if (!sec) {
          sec = _mkSec('mw-site', siteName);
          sec.id = sid;
          root.appendChild(sec);
        }

        /* Update online dot */
        var dot = sec.querySelector('.mw-sdot');
        if (dot) dot.className = 'mw-sdot' + (site.online ? '' : ' off');

        var mg = sec.querySelector('.mw-grid');
        (site.streams || []).forEach(function(st) {
          var key = siteName + '|' + st.name;
          seen[key] = true;
          var pk = _updatePeak(key, st.level_dbfs, now);
          var card = existing[key];
          if (!card) { card = buildCard(key, st, siteName); }
          if (card.parentElement !== mg) mg.appendChild(card);
          updateCard(card, st, siteName, pk);
        });
      });
    }

    /* Prune stale cards */
    Object.keys(existing).forEach(function(k) {
      if (!seen[k]) existing[k].remove();
    });

    /* Header meta */
    var total  = flat.length;
    var alerts = flat.filter(function(i) { return (i.st.ai_status || '').indexOf('[ALERT]') >= 0; }).length;
    document.getElementById('mw-meta').textContent =
      total + ' stream' + (total !== 1 ? 's' : '') +
      ' · ' + (data.sites || []).length + ' site' + ((data.sites || []).length !== 1 ? 's' : '');
    var badge = document.getElementById('mw-alert-badge');
    badge.textContent = '⚠ ' + alerts + ' ALERT' + (alerts !== 1 ? 'S' : '');
    badge.classList.toggle('show', alerts > 0);

    /* Empty state */
    if (total === 0) {
      root.innerHTML =
        '<div class="mw-empty">'
        + '<div class="mw-empty-ico">📡</div>'
        + '<div style="font-size:14px;font-weight:600">No streams found</div>'
        + '<div style="font-size:12px">Connect a site or enable local monitoring.</div>'
        + '</div>';
    }
  }

  /* Build a site section element */
  function _mkSec(cls, siteName) {
    var sec = document.createElement('div');
    sec.className = cls;
    if (siteName !== null) {
      sec.dataset.site = siteName;
      sec.innerHTML =
        '<div class="mw-site-hdr">'
        + '<span class="mw-sdot"></span>'
        + _esc(siteName)
        + '</div>'
        + '<div class="mw-grid"></div>';
    } else {
      sec.innerHTML = '<div class="mw-grid"></div>';
    }
    return sec;
  }

  /* ── Metadata poll (1 Hz) ──────────────────────────────────────────────── */
  function poll() {
    fetch('/api/meterwall/data', {credentials: 'same-origin'})
      .then(function(r) { return r.json(); })
      .then(render)
      .catch(function() {});
  }
  poll();
  setInterval(poll, POLL_MS);

  /* ── Live level fast-poll (5 Hz) ────────────────────────────────────────
     Fetches /api/hub/live_levels (hub push data, updated every ~200 ms)
     and updates only the bar height, peak marker, and dB text on each
     card.  Metadata (now playing, LUFS-I, AI status, RTP loss) continues
     to come from the 1 s metadata poll above.
  ──────────────────────────────────────────────────────────────────────── */
  /* ── rAF meter animation loop ───────────────────────────────────────────── */
  /* Fast attack, smooth decay — like a broadcast PPM meter.
     _targetLev is updated by livePoll(); _dispLev is smoothed here at 60 fps.
     Peak hold tracks the raw target, not the smoothed display. */
  function _meterRaf(ts) {
    var dt = _rafTs ? Math.min((ts - _rafTs) / 1000, 0.1) : 0;
    _rafTs = ts;
    Object.keys(_targetLev).forEach(function(key) {
      var target = _targetLev[key];
      var cur    = (_dispLev[key] != null) ? _dispLev[key] : target;
      if (target > cur) {
        cur = Math.min(target, cur + ATTACK_RATE * dt);   // fast attack
      } else {
        cur = Math.max(target, cur - DECAY_RATE  * dt);   // smooth decay
      }
      _dispLev[key] = cur;

      var esc  = key.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
      var card = document.querySelector('.mc[data-key="' + esc + '"]');
      if (!card) return;

      var fill = card.querySelector('.mtr-fill');
      if (fill) fill.style.height = levToH(cur) + '%';

      var pk   = _peaks[key] ? _peaks[key].val : DB_FLOOR;
      var pkEl = card.querySelector('.mtr-peak');
      if (pkEl) {
        pkEl.style.bottom  = levToH(pk) + '%';
        pkEl.style.opacity = pk > DB_FLOOR ? '0.82' : '0';
      }

      var levEl = card.querySelector('.mc-lev');
      if (levEl) { levEl.textContent = fmtLev(cur); levEl.className = 'mc-lev ' + levCls(cur); }
    });
    requestAnimationFrame(_meterRaf);
  }
  requestAnimationFrame(_meterRaf);

  function livePoll() {
    fetch('/api/hub/live_levels', {credentials: 'same-origin'})
      .then(function(r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function(data) {
        _liveActive = true;
        var now = Date.now();
        Object.keys(data).forEach(function(siteName) {
          (data[siteName] || []).forEach(function(s) {
            var key = siteName + '|' + s.name;
            var lev = (s.level_dbfs == null) ? DB_FLOOR : s.level_dbfs;
            _targetLev[key] = lev;          // rAF loop drives DOM from here
            _updatePeak(key, lev, now);     // peak tracks raw signal, not display
          });
        });
      })
      .catch(function() {});
  }
  livePoll();
  setInterval(livePoll, LIVE_MS);

  /* ── Init ───────────────────────────────────────────────────────────────── */
  (function init() {
    /* Restore saved size */
    try {
      var saved = localStorage.getItem('mw_size');
      if (saved && _sizes[saved]) _curSize = saved;
    } catch(e) {}
    setSize(_curSize);

    /* Button listeners — must be inside IIFE so functions are in scope */
    document.getElementById('btn-sm').addEventListener('click', function() { setSize('sm'); });
    document.getElementById('btn-md').addEventListener('click', function() { setSize('md'); });
    document.getElementById('btn-lg').addEventListener('click', function() { setSize('lg'); });
    document.getElementById('btn-sort').addEventListener('click', toggleSort);
    document.getElementById('btn-fs').addEventListener('click', toggleFs);

    /* Keyboard shortcuts */
    document.addEventListener('keydown', function(e) {
      var tag = (e.target.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea') return;
      if (e.key === 'f' || e.key === 'F') { toggleFs(); }
      if (e.key === 's' || e.key === 'S') { toggleSort(); }
      if (e.key === '1') setSize('sm');
      if (e.key === '2') setSize('md');
      if (e.key === '3') setSize('lg');
    });
  })();
})();
</script>
</body>
</html>"""
