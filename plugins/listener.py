# listener.py — drop into the plugins/ subdirectory
# Presenter / Producer stream listener — live audio relay, hub-only

SIGNALSCOPE_PLUGIN = {
    "id":       "listener",
    "label":    "Listen",
    "url":      "/listener",
    "icon":     "🎧",
    "hub_only": True,
    "version":  "1.0.0",
}

# ─── Template ─────────────────────────────────────────────────────────────────

_LISTENER_TPL = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Listen — SignalScope</title>
<meta name="csrf-token" content="{{csrf_token()}}">
<link rel="icon" type="image/x-icon" href="/static/signalscope_icon.png">
<style nonce="{{csp_nonce()}}">
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(circle at top,#12376f 0%,var(--bg) 38%,#05101f 100%);color:var(--tx);font-family:system-ui,sans-serif;font-size:14px;min-height:100vh;padding-bottom:120px}
a{color:var(--acc);text-decoration:none}

/* ── Header ── */
.hdr{background:linear-gradient(180deg,rgba(10,31,65,.97),rgba(9,24,48,.97));border-bottom:1px solid var(--bor);padding:14px 24px;display:flex;align-items:center;gap:14px;position:sticky;top:0;z-index:50;backdrop-filter:blur(8px)}
.hdr-logo{font-size:22px}
.hdr-title{font-size:18px;font-weight:700;letter-spacing:-.02em}
.hdr-sub{font-size:12px;color:var(--mu);margin-top:1px}
.hdr-right{margin-left:auto;display:flex;align-items:center;gap:10px}
.hdr-user{font-size:12px;color:var(--mu);background:rgba(23,52,95,.6);padding:5px 12px;border-radius:20px;border:1px solid var(--bor)}
.hdr-back{font-size:12px;color:var(--mu);background:rgba(23,52,95,.4);padding:5px 12px;border-radius:20px;border:1px solid var(--bor);text-decoration:none;transition:color .2s}
.hdr-back:hover{color:var(--tx)}

/* ── Hero greeting ── */
.greeting{padding:32px 24px 8px;max-width:1400px;margin:0 auto}
.greeting-title{font-size:26px;font-weight:700;letter-spacing:-.02em;margin-bottom:6px}
.greeting-sub{font-size:14px;color:var(--mu)}

/* ── Help bar ── */
.help-bar{background:rgba(23,168,255,.07);border:1px solid rgba(23,168,255,.2);border-radius:12px;padding:12px 18px;margin:16px 24px 0;max-width:1400px;margin-left:auto;margin-right:auto;display:flex;align-items:flex-start;gap:12px;cursor:pointer;transition:background .2s}
.help-bar:hover{background:rgba(23,168,255,.12)}
.help-bar-icon{font-size:18px;flex-shrink:0;margin-top:1px}
.help-bar-text{flex:1}
.help-bar-text strong{font-size:13px;color:var(--acc)}
.help-bar-text p{font-size:12px;color:var(--mu);margin-top:3px;line-height:1.5}
.help-steps{display:none;margin-top:10px;padding-top:10px;border-top:1px solid var(--bor)}
.help-steps.open{display:block}
.help-step{display:flex;align-items:flex-start;gap:10px;margin-bottom:8px;font-size:12px;color:var(--mu)}
.help-num{background:var(--acc);color:#fff;width:20px;height:20px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;flex-shrink:0}

/* ── Status / message bar ── */
#msg-bar{height:32px;display:flex;align-items:center;padding:0 28px;font-size:12px;opacity:0;transition:opacity .3s;max-width:1400px;margin:0 auto}

/* ── Site section header ── */
.site-hdr{padding:20px 24px 10px;max-width:1400px;margin:0 auto;display:flex;align-items:center;gap:10px}
.site-hdr-name{font-size:13px;font-weight:700;color:var(--mu);text-transform:uppercase;letter-spacing:.08em}
.site-hdr-badge{font-size:11px;font-weight:600;padding:2px 8px;border-radius:20px}
.site-online{background:rgba(34,197,94,.12);color:var(--ok);border:1px solid rgba(34,197,94,.3)}
.site-offline{background:rgba(138,164,200,.08);color:var(--mu);border:1px solid rgba(138,164,200,.2)}

/* ── Stream grid ── */
.stream-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:16px;padding:0 24px 8px;max-width:1400px;margin:0 auto}

/* ── Stream card ── */
.stream-card{background:var(--sur);border:1.5px solid var(--bor);border-radius:18px;padding:22px;cursor:pointer;transition:border-color .2s,box-shadow .2s,transform .15s;position:relative;overflow:hidden;-webkit-tap-highlight-color:transparent}
.stream-card::before{content:"";position:absolute;inset:0;border-radius:18px;background:transparent;transition:background .2s;pointer-events:none}
.stream-card:hover{border-color:var(--acc);transform:translateY(-2px);box-shadow:0 10px 32px rgba(0,0,0,.45)}
.stream-card:active{transform:translateY(0)}
.stream-card.playing{border-color:var(--ok);box-shadow:0 0 0 1px var(--ok),0 10px 32px rgba(34,197,94,.18)}
.stream-card.playing::before{background:rgba(34,197,94,.04)}
.stream-card.offline{opacity:.55}
.stream-card.offline:hover{transform:none;border-color:var(--bor);box-shadow:none;cursor:not-allowed}

/* ── Avatar ── */
.avatar{width:54px;height:54px;border-radius:14px;display:flex;align-items:center;justify-content:center;font-size:20px;font-weight:800;color:#fff;flex-shrink:0;letter-spacing:-.02em;text-shadow:0 1px 3px rgba(0,0,0,.3)}

/* ── Card text ── */
.card-top{display:flex;align-items:flex-start;gap:14px;margin-bottom:16px}
.card-meta{flex:1;min-width:0}
.card-name{font-size:16px;font-weight:700;line-height:1.2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:4px}
.card-site{font-size:12px;color:var(--mu);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:6px}
.card-rds{font-size:11px;color:var(--acc);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-height:14px}

/* ── Level meter ── */
.level-wrap{display:flex;align-items:flex-end;gap:3px;height:22px;margin-bottom:16px;padding:0 2px}
.lv{width:5px;border-radius:3px;transition:height .12s,background .12s;flex-shrink:0}

/* ── Equalizer animation ── */
@keyframes eq1{0%,100%{height:4px}30%{height:18px}60%{height:8px}}
@keyframes eq2{0%,100%{height:10px}40%{height:4px}70%{height:16px}}
@keyframes eq3{0%,100%{height:16px}20%{height:6px}80%{height:12px}}
@keyframes eq4{0%,100%{height:6px}50%{height:20px}}
@keyframes eq5{0%,100%{height:12px}35%{height:4px}75%{height:18px}}
@keyframes eq6{0%,100%{height:8px}45%{height:14px}85%{height:4px}}
@keyframes eq7{0%,100%{height:14px}25%{height:4px}65%{height:10px}}
@keyframes eq8{0%,100%{height:4px}55%{height:16px}}

/* ── Listen button ── */
.listen-btn{width:100%;padding:11px;border-radius:12px;border:none;font-size:14px;font-weight:700;cursor:pointer;font-family:inherit;transition:all .2s;display:flex;align-items:center;justify-content:center;gap:8px;letter-spacing:.01em}
.listen-btn.start{background:linear-gradient(135deg,#1a7fe8,var(--acc));color:#fff;box-shadow:0 3px 12px rgba(23,168,255,.3)}
.listen-btn.start:hover{box-shadow:0 4px 18px rgba(23,168,255,.5);transform:translateY(-1px)}
.listen-btn.stop{background:linear-gradient(135deg,#178a48,var(--ok));color:#fff;box-shadow:0 3px 12px rgba(34,197,94,.25)}
.listen-btn.stop:hover{box-shadow:0 4px 18px rgba(34,197,94,.4);transform:translateY(-1px)}
.listen-btn:disabled{opacity:.45;cursor:not-allowed;transform:none!important;box-shadow:none!important}

/* ── Badge ── */
.badge{font-size:11px;font-weight:600;padding:3px 9px;border-radius:20px;white-space:nowrap}
.b-ok{background:rgba(34,197,94,.12);color:var(--ok);border:1px solid rgba(34,197,94,.3)}
.b-al{background:rgba(239,68,68,.12);color:var(--al);border:1px solid rgba(239,68,68,.3)}
.b-wn{background:rgba(245,158,11,.12);color:var(--wn);border:1px solid rgba(245,158,11,.3)}
.b-mu{background:rgba(138,164,200,.08);color:var(--mu);border:1px solid rgba(138,164,200,.2)}

/* ── Now-playing bar ── */
#now-bar{position:fixed;bottom:0;left:0;right:0;background:linear-gradient(0deg,rgba(7,20,43,.99) 0%,rgba(13,35,70,.98) 100%);border-top:1px solid var(--bor);padding:0 24px;display:flex;align-items:center;gap:18px;height:72px;transform:translateY(100%);transition:transform .3s cubic-bezier(.4,0,.2,1);z-index:100;backdrop-filter:blur(12px)}
#now-bar.up{transform:translateY(0)}
.np-eq{display:flex;align-items:flex-end;gap:3px;height:22px;width:32px;flex-shrink:0}
.np-bar{width:4px;border-radius:2px;background:var(--ok)}
.np-info{flex:1;min-width:0}
.np-name{font-size:15px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.np-site{font-size:12px;color:var(--mu)}
.np-vol{display:flex;align-items:center;gap:8px;flex-shrink:0}
.np-vol-icon{font-size:16px;cursor:pointer;min-width:20px;text-align:center}
input[type=range]{-webkit-appearance:none;appearance:none;height:4px;border-radius:2px;background:rgba(23,52,95,.9);outline:none;width:100px;cursor:pointer}
input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:16px;height:16px;border-radius:50%;background:var(--acc);cursor:pointer;box-shadow:0 0 6px rgba(23,168,255,.5)}
input[type=range]::-moz-range-thumb{width:16px;height:16px;border-radius:50%;background:var(--acc);cursor:pointer;border:none}
.np-stop{background:rgba(239,68,68,.15);color:var(--al);border:1px solid rgba(239,68,68,.3);padding:7px 16px;border-radius:10px;font-size:13px;font-weight:700;cursor:pointer;font-family:inherit;transition:all .2s;white-space:nowrap}
.np-stop:hover{background:rgba(239,68,68,.25)}

/* ── Empty / loading states ── */
.empty{text-align:center;padding:60px 24px;color:var(--mu)}
.empty-icon{font-size:48px;margin-bottom:16px;opacity:.4}
.empty-title{font-size:18px;font-weight:600;color:var(--tx);margin-bottom:8px}
.empty-desc{font-size:14px;line-height:1.6}
.skeleton{animation:pulse 1.5s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:.4}50%{opacity:.7}}
.skel-card{background:var(--sur);border:1.5px solid var(--bor);border-radius:18px;padding:22px;height:200px}
.skel-row{background:rgba(23,52,95,.6);border-radius:6px;margin-bottom:10px}

/* ── Retry banner ── */
.retry-banner{background:rgba(245,158,11,.1);border:1px solid rgba(245,158,11,.3);border-radius:10px;padding:10px 14px;font-size:13px;color:var(--wn);display:flex;align-items:center;gap:10px;margin:0 24px 12px;max-width:1400px;margin-left:auto;margin-right:auto}

/* ── Scrollbar ── */
::-webkit-scrollbar{width:6px;height:6px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--bor);border-radius:3px}

/* ── Responsive ── */
@media(max-width:600px){
  .stream-grid{grid-template-columns:1fr;padding:0 12px 8px}
  .greeting{padding:20px 16px 8px}
  .site-hdr{padding:16px 16px 8px}
  .help-bar{margin:12px 16px 0}
  .hdr{padding:12px 16px}
  .greeting-title{font-size:22px}
  #now-bar{padding:0 16px;gap:12px}
  input[type=range]{width:70px}
}
</style>
</head>
<body>

<!-- ── Header ── -->
<header class="hdr">
  <span class="hdr-logo">🎧</span>
  <div>
    <div class="hdr-title">Listener</div>
    <div class="hdr-sub">Live stream monitor</div>
  </div>
  <div class="hdr-right">
    {% if username %}<div class="hdr-user">👤 {{username}}</div>{% endif %}
    <a href="/" class="hdr-back">← Dashboard</a>
  </div>
</header>

<!-- ── Greeting ── -->
<div class="greeting">
  <div class="greeting-title" id="greeting-text">Good day 👋</div>
  <div class="greeting-sub">Select a stream below to start listening live.</div>
</div>

<!-- ── Help bar ── -->
<div class="help-bar" id="help-toggle">
  <div class="help-bar-icon">💡</div>
  <div class="help-bar-text">
    <strong>How to use the Listener</strong>
    <p>Tap to expand quick-start guide</p>
    <div class="help-steps" id="help-steps">
      <div class="help-step"><div class="help-num">1</div><div>Find your station in the list below. Stations are grouped by site.</div></div>
      <div class="help-step"><div class="help-num">2</div><div>Tap the blue <strong>🎧 Listen</strong> button on the station card to start audio in your browser.</div></div>
      <div class="help-step"><div class="help-num">3</div><div>A green bar will appear at the bottom of your screen showing what is playing. Use the volume slider to adjust level.</div></div>
      <div class="help-step"><div class="help-num">4</div><div>To stop, tap <strong>⏹ Stop Listening</strong> on the card or the red <strong>Stop</strong> button in the bottom bar.</div></div>
      <div class="help-step"><div class="help-num">5</div><div>Stations marked <span class="badge b-mu" style="font-size:10px">○ Offline</span> are not currently available. Try again later.</div></div>
    </div>
  </div>
</div>

<!-- ── Message bar ── -->
<div id="msg-bar"></div>

<!-- ── Stream content ── -->
<div id="content"></div>

<!-- ── Retry banner (hidden) ── -->
<div id="retry-banner" class="retry-banner" style="display:none">
  ⚠ <span id="retry-text">Stream interrupted — reconnecting…</span>
</div>

<!-- ── Now-playing bar ── -->
<div id="now-bar">
  <div class="np-eq" id="np-eq">
    <div class="np-bar" id="npb1" style="height:10px"></div>
    <div class="np-bar" id="npb2" style="height:16px"></div>
    <div class="np-bar" id="npb3" style="height:6px"></div>
    <div class="np-bar" id="npb4" style="height:14px"></div>
    <div class="np-bar" id="npb5" style="height:8px"></div>
  </div>
  <div class="np-info">
    <div class="np-name" id="np-name">—</div>
    <div class="np-site" id="np-site"></div>
  </div>
  <div class="np-vol">
    <span class="np-vol-icon" id="vol-icon">🔊</span>
    <input type="range" id="vol-slider" min="0" max="1" step="0.02" value="0.85">
  </div>
  <button class="np-stop" id="np-stop">⏹ Stop</button>
</div>

<script nonce="{{csp_nonce()}}">
(function(){
'use strict';

// ── Constants ──────────────────────────────────────────────────────────────
var AVATAR_COLORS = [
  ['#1a7fe8','#17a8ff'], // blue
  ['#16a047','#22c55e'], // green
  ['#c87f0a','#f59e0b'], // amber
  ['#9333e8','#a855f7'], // purple
  ['#d91a6e','#ec4899'], // pink
  ['#0d9488','#14b8a6'], // teal
  ['#c2440f','#f97316'], // orange
  ['#c81e1e','#ef4444'], // red
];
var POLL_MS  = 10000;
var RETRY_MS = 6000;

// ── State ──────────────────────────────────────────────────────────────────
var _audio       = null;
var _playing     = null;   // {site, idx, name, url}
var _streams     = [];     // flat list of all stream objects
var _pollTimer   = null;
var _retryTimer  = null;
var _retryCount  = 0;
var _helpOpen    = false;

// ── Greeting ───────────────────────────────────────────────────────────────
(function(){
  var h = new Date().getHours();
  var g = h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening';
  var user = '{{username|e}}';
  document.getElementById('greeting-text').textContent = g + (user ? ', ' + user : '') + ' 👋';
})();

// ── Help toggle ────────────────────────────────────────────────────────────
document.getElementById('help-toggle').addEventListener('click', function(){
  _helpOpen = !_helpOpen;
  var steps = document.getElementById('help-steps');
  var p = this.querySelector('.help-bar-text p');
  steps.classList.toggle('open', _helpOpen);
  if (p) p.textContent = _helpOpen ? 'Tap to collapse' : 'Tap to expand quick-start guide';
});

// ── Utilities ──────────────────────────────────────────────────────────────
function _esc(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function _colorFor(name){
  var h=0; for(var i=0;i<name.length;i++) h=(h*31+name.charCodeAt(i))&0x7fffffff;
  return AVATAR_COLORS[h % AVATAR_COLORS.length];
}
function _dbPct(db){
  if(db===null||db===undefined||isNaN(db)) return 0;
  return Math.max(0, Math.min(100, (parseFloat(db)+60)/60*100));
}
function _lvColor(pct){
  return pct>75 ? 'var(--al)' : pct>50 ? 'var(--wn)' : 'var(--ok)';
}
function _encodeSite(s){
  return s.split('/').map(encodeURIComponent).join('/');
}

// ── Show / hide message ────────────────────────────────────────────────────
var _msgT = null;
function showMsg(text, type){
  var el = document.getElementById('msg-bar');
  el.textContent = text;
  el.style.color = type==='ok'?'var(--ok)':type==='warn'?'var(--wn)':'var(--al)';
  el.style.opacity = '1';
  clearTimeout(_msgT);
  _msgT = setTimeout(function(){ el.style.opacity='0'; }, 3500);
}

// ── Load streams ───────────────────────────────────────────────────────────
function loadStreams(initial){
  if(initial) showSkeleton();
  fetch('/hub/data',{credentials:'same-origin'})
    .then(function(r){ if(!r.ok) throw new Error(r.status); return r.json(); })
    .then(function(d){
      var sites = d.sites || [];
      _streams = [];
      var bySite = {};
      sites.forEach(function(site){
        var sname = site.site || site.name || '?';
        bySite[sname] = { online: site.online, streams: [] };
        (site.streams||[]).forEach(function(s,idx){
          if(!s.enabled) return;
          var obj = {
            site:    sname,
            idx:     idx,
            name:    s.name || ('Stream '+(idx+1)),
            level:   s.level_dbfs,
            status:  s.ai_status || 'OK',
            online:  !!site.online,
            rds:     s.fm_rds_ps  || '',
            dab:     s.dab_service || '',
            label:   s.label || '',
          };
          _streams.push(obj);
          bySite[sname].streams.push(obj);
        });
      });
      renderContent(bySite);
      schedulePoll();
    })
    .catch(function(e){
      if(initial){
        document.getElementById('content').innerHTML =
          '<div class="empty"><div class="empty-icon">📡</div>'
          +'<div class="empty-title">Could not load streams</div>'
          +'<div class="empty-desc">Check your connection and <a href="" onclick="location.reload();return false">refresh the page</a>.</div></div>';
      }
    });
}

// ── Skeleton loading cards ─────────────────────────────────────────────────
function showSkeleton(){
  var html = '<div class="stream-grid">';
  for(var i=0;i<6;i++) html += '<div class="skel-card skeleton">'
    +'<div class="skel-row" style="height:54px;width:54px;border-radius:14px;margin-bottom:14px"></div>'
    +'<div class="skel-row" style="height:16px;width:70%"></div>'
    +'<div class="skel-row" style="height:12px;width:45%"></div>'
    +'<div class="skel-row" style="height:22px;margin-top:16px"></div>'
    +'<div class="skel-row" style="height:40px;margin-top:16px;border-radius:12px"></div>'
    +'</div>';
  html += '</div>';
  document.getElementById('content').innerHTML = html;
}

// ── Render all content ─────────────────────────────────────────────────────
function renderContent(bySite){
  var siteNames = Object.keys(bySite).sort();
  if(!siteNames.length || !_streams.length){
    document.getElementById('content').innerHTML =
      '<div class="empty"><div class="empty-icon">🔇</div>'
      +'<div class="empty-title">No streams available</div>'
      +'<div class="empty-desc">There are no live streams assigned to your account.<br>Contact your administrator if you believe this is an error.</div></div>';
    return;
  }

  var html = '';
  siteNames.forEach(function(sname){
    var sg = bySite[sname];
    if(!sg.streams.length) return;
    var onl = sg.online;
    html += '<div class="site-hdr">'
      +'<div class="site-hdr-name">📍 '+_esc(sname)+'</div>'
      +'<span class="badge site-hdr-badge '+(onl?'site-online':'site-offline')+'">'+(onl?'● Online':'○ Offline')+'</span>'
      +'</div>';
    html += '<div class="stream-grid">';
    sg.streams.forEach(function(s){
      html += renderCard(s);
    });
    html += '</div>';
  });
  document.getElementById('content').innerHTML = html;

  // Re-attach event delegation
  document.getElementById('content').addEventListener('click', _onCardClick);
}

// ── Render a single card ───────────────────────────────────────────────────
function renderCard(s){
  var isPlaying = _playing && _playing.site===s.site && _playing.idx===s.idx;
  var col = _colorFor(s.name);
  var initial = (s.name.match(/[A-Z0-9]/i)||[s.name[0]||'?'])[0].toUpperCase();
  var pct = _dbPct(s.level);

  // Status badge
  var badge = '';
  if(!s.online){
    badge = '<span class="badge b-mu">○ Offline</span>';
  } else if(s.status==='ALERT'){
    badge = '<span class="badge b-al">⚠ Alert</span>';
  } else if(s.status==='WARN'){
    badge = '<span class="badge b-wn">⚡ Caution</span>';
  } else {
    badge = '<span class="badge b-ok">● Live</span>';
  }

  // Sub-info line (RDS / DAB / label)
  var sub = s.rds || s.dab || s.label || '';

  // Level bars (8 bars)
  var bars = '';
  for(var b=0;b<8;b++){
    var style;
    if(isPlaying){
      style = 'animation:eq'+(b+1)+' '+(0.55+b*0.07).toFixed(2)+'s infinite ease-in-out;'
             +'background:'+col[1]+';height:4px';
    } else {
      var frac = 1 - b * 0.09;
      var bh = s.online ? Math.max(2, pct/100 * 20 * frac) : 2;
      style = 'height:'+bh.toFixed(1)+'px;background:'+_lvColor(pct);
    }
    bars += '<div class="lv" style="'+style+'"></div>';
  }

  // Button
  var btnClass = isPlaying ? 'stop' : 'start';
  var btnText  = isPlaying ? '⏹&nbsp;&nbsp;Stop Listening' : '🎧&nbsp;&nbsp;Listen';
  var disabled = (!s.online && !isPlaying) ? ' disabled' : '';

  return '<div class="stream-card'+(isPlaying?' playing':'')+(s.online?'':' offline')+'"'
    +' data-site="'+_esc(s.site)+'" data-idx="'+s.idx+'" data-name="'+_esc(s.name)+'">'
    +'<div class="card-top">'
    +'<div class="avatar" style="background:linear-gradient(135deg,'+col[0]+','+col[1]+')">'+_esc(initial)+'</div>'
    +'<div class="card-meta">'
    +'<div class="card-name">'+_esc(s.name)+'</div>'
    +'<div class="card-site">'+_esc(s.site)+'</div>'
    +'<div class="card-rds">'+_esc(sub)+'</div>'
    +'</div>'
    +badge
    +'</div>'
    +'<div class="level-wrap">'+bars+'</div>'
    +'<button class="listen-btn '+btnClass+'"'+disabled+' data-action="listen">'+btnText+'</button>'
    +'</div>';
}

// ── Card click delegation ──────────────────────────────────────────────────
function _onCardClick(e){
  var btn = e.target.closest('[data-action="listen"]');
  if(!btn || btn.disabled) return;
  var card = btn.closest('.stream-card');
  if(!card) return;
  var site = card.dataset.site;
  var idx  = parseInt(card.dataset.idx, 10);
  var name = card.dataset.name;
  if(_playing && _playing.site===site && _playing.idx===idx){
    stopAudio();
  } else {
    startAudio(site, idx, name);
  }
}

// ── Start audio ────────────────────────────────────────────────────────────
function startAudio(site, idx, name){
  stopAudio(true);
  clearRetry();

  var url = '/hub/site/'+_encodeSite(site)+'/stream/'+idx+'/live';
  _playing = {site:site, idx:idx, name:name, url:url};

  _audio = new Audio();
  _audio.preload  = 'none';
  _audio.autoplay = true;
  _audio.volume   = parseFloat(document.getElementById('vol-slider').value);

  _audio.addEventListener('error', _onAudioError);
  _audio.addEventListener('ended', _onAudioEnded);
  _audio.addEventListener('playing', function(){ hideRetry(); _retryCount=0; });

  _audio.src = url;
  _audio.play().catch(function(){
    // Browser may require user gesture first on some devices — audio will play
    // once the stream data arrives
  });

  showNowPlaying(name, site);
  refreshCards();
  showMsg('▶ Connecting to ' + name + '…', 'ok');
}

// ── Audio event handlers ───────────────────────────────────────────────────
function _onAudioError(){
  if(!_playing) return;
  _retryCount++;
  var wait = Math.min(30, RETRY_MS/1000 * _retryCount);
  showRetry('Stream interrupted — reconnecting in '+wait+' seconds… (attempt '+_retryCount+')');
  var p = _playing;
  _retryTimer = setTimeout(function(){
    if(_playing && _playing.site===p.site && _playing.idx===p.idx){
      startAudio(p.site, p.idx, p.name);
    }
  }, wait*1000);
}

function _onAudioEnded(){
  if(!_playing) return;
  // Live streams shouldn't end — treat as error
  _onAudioError();
}

// ── Stop audio ─────────────────────────────────────────────────────────────
function stopAudio(silent){
  clearRetry();
  if(_audio){
    _audio.removeEventListener('error', _onAudioError);
    _audio.removeEventListener('ended', _onAudioEnded);
    _audio.pause();
    _audio.src = '';
    _audio = null;
  }
  _playing = null;
  _retryCount = 0;
  hideNowPlaying();
  hideRetry();
  refreshCards();
  if(!silent) showMsg('⏹ Stopped.', 'ok');
}

// ── Retry banner ───────────────────────────────────────────────────────────
function showRetry(msg){
  var b = document.getElementById('retry-banner');
  document.getElementById('retry-text').textContent = msg;
  b.style.display = 'flex';
}
function hideRetry(){
  document.getElementById('retry-banner').style.display = 'none';
}
function clearRetry(){
  clearTimeout(_retryTimer);
  _retryTimer = null;
}

// ── Now-playing bar ────────────────────────────────────────────────────────
var _npAnim = null;
function showNowPlaying(name, site){
  document.getElementById('np-name').textContent = name;
  document.getElementById('np-site').textContent = '📍 '+site;
  document.getElementById('now-bar').classList.add('up');
  startNpAnim();
}
function hideNowPlaying(){
  document.getElementById('now-bar').classList.remove('up');
  stopNpAnim();
}
function startNpAnim(){
  var bars = [
    document.getElementById('npb1'), document.getElementById('npb2'),
    document.getElementById('npb3'), document.getElementById('npb4'),
    document.getElementById('npb5'),
  ];
  var phase = [0, 1.2, 2.1, 0.5, 1.8];
  var heights = [10,16,6,14,8];
  function tick(){
    var t = Date.now()/1000;
    bars.forEach(function(b,i){
      var h = 4 + 16*(0.5+0.5*Math.sin(t*3.2*(1+i*0.18)+phase[i]));
      b.style.height = h.toFixed(1)+'px';
    });
    _npAnim = requestAnimationFrame(tick);
  }
  _npAnim = requestAnimationFrame(tick);
}
function stopNpAnim(){
  if(_npAnim){ cancelAnimationFrame(_npAnim); _npAnim=null; }
}

// ── Refresh only changed cards (no full re-render) ─────────────────────────
function refreshCards(){
  _streams.forEach(function(s){
    var card = document.querySelector(
      '.stream-card[data-site="'+s.site.replace(/"/g,'&quot;')+'"][data-idx="'+s.idx+'"]');
    if(!card) return;
    var isPlaying = _playing && _playing.site===s.site && _playing.idx===s.idx;
    card.classList.toggle('playing', isPlaying);

    // Button text + class
    var btn = card.querySelector('.listen-btn');
    if(btn){
      btn.className = 'listen-btn ' + (isPlaying ? 'stop' : 'start');
      btn.innerHTML = isPlaying ? '⏹&nbsp;&nbsp;Stop Listening' : '🎧&nbsp;&nbsp;Listen';
    }

    // Level bars
    var bars = card.querySelectorAll('.lv');
    var pct = _dbPct(s.level);
    var col = _colorFor(s.name)[1];
    bars.forEach(function(bar,b){
      if(isPlaying){
        bar.style.animation = 'eq'+(b+1)+' '+(0.55+b*0.07).toFixed(2)+'s infinite ease-in-out';
        bar.style.background = col;
        bar.style.height = '4px';
      } else {
        bar.style.animation = '';
        var frac = 1-b*0.09;
        var bh = s.online ? Math.max(2, pct/100*20*frac) : 2;
        bar.style.height = bh.toFixed(1)+'px';
        bar.style.background = _lvColor(pct);
      }
    });
  });
}

// ── Poll for level updates ─────────────────────────────────────────────────
function schedulePoll(){
  clearTimeout(_pollTimer);
  _pollTimer = setTimeout(pollLevels, POLL_MS);
}
function pollLevels(){
  fetch('/hub/data',{credentials:'same-origin'})
    .then(function(r){ return r.json(); })
    .then(function(d){
      (d.sites||[]).forEach(function(site){
        var sname = site.site||'';
        (site.streams||[]).forEach(function(s,idx){
          var match = _streams.find(function(x){ return x.site===sname&&x.idx===idx; });
          if(match){
            match.level  = s.level_dbfs;
            match.status = s.ai_status||'OK';
            match.online = !!site.online;
            match.rds    = s.fm_rds_ps||'';
            match.dab    = s.dab_service||'';
          }
        });
      });
      refreshLevelBars();
      schedulePoll();
    })
    .catch(function(){ schedulePoll(); });
}
function refreshLevelBars(){
  _streams.forEach(function(s){
    var isPlaying = _playing && _playing.site===s.site && _playing.idx===s.idx;
    if(isPlaying) return; // leave animated bars alone
    var card = document.querySelector(
      '.stream-card[data-site="'+s.site.replace(/"/g,'&quot;')+'"][data-idx="'+s.idx+'"]');
    if(!card) return;
    var bars = card.querySelectorAll('.lv');
    var pct = _dbPct(s.level);
    bars.forEach(function(bar,b){
      var frac = 1-b*0.09;
      var bh = s.online ? Math.max(2, pct/100*20*frac) : 2;
      bar.style.height = bh.toFixed(1)+'px';
      bar.style.background = _lvColor(pct);
    });
    // Update RDS line if changed
    var rdsEl = card.querySelector('.card-rds');
    if(rdsEl) rdsEl.textContent = s.rds||s.dab||s.label||'';
  });
}

// ── Volume control ─────────────────────────────────────────────────────────
document.getElementById('vol-slider').addEventListener('input', function(){
  var v = parseFloat(this.value);
  if(_audio) _audio.volume = v;
  document.getElementById('vol-icon').textContent = v>0.6?'🔊':v>0.1?'🔉':'🔇';
});
document.getElementById('vol-icon').addEventListener('click', function(){
  var sl = document.getElementById('vol-slider');
  if(sl.value>0){ sl._last=sl.value; sl.value=0; }
  else { sl.value = sl._last||0.85; }
  sl.dispatchEvent(new Event('input'));
});

// ── Stop button in now-playing bar ────────────────────────────────────────
document.getElementById('np-stop').addEventListener('click', function(){ stopAudio(); });

// ── Spacebar shortcut ─────────────────────────────────────────────────────
document.addEventListener('keydown', function(e){
  if(e.code==='Space' && e.target.tagName!=='BUTTON' && e.target.tagName!=='INPUT'){
    e.preventDefault();
    if(_playing) stopAudio();
  }
});

// ── Wake lock (keep screen on while listening, best-effort) ──────────────
var _wakeLock = null;
function _acquireWakeLock(){
  if('wakeLock' in navigator){
    navigator.wakeLock.request('screen').then(function(wl){ _wakeLock=wl; }).catch(function(){});
  }
}
function _releaseWakeLock(){
  if(_wakeLock){ _wakeLock.release().catch(function(){}); _wakeLock=null; }
}

// ── Kick off ─────────────────────────────────────────────────────────────
loadStreams(true);

})();
</script>
</body>
</html>
"""

# ─── Plugin registration ───────────────────────────────────────────────────

def register(app, ctx):
    from flask import render_template_string, session

    login_required = ctx["login_required"]
    BUILD          = ctx["BUILD"]

    @app.get("/listener")
    @login_required
    def listener_page():
        username = session.get("username", "")
        return render_template_string(
            _LISTENER_TPL,
            BUILD    = BUILD,
            username = username,
        )
