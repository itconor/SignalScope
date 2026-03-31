# listener.py — drop into the plugins/ subdirectory
# Presenter / Producer stream listener — live audio relay, hub-only

SIGNALSCOPE_PLUGIN = {
    "id":       "listener",
    "label":    "Listen",
    "url":      "/listener",
    "icon":     "🎧",
    "hub_only": True,
    "version":  "1.1.2",
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
body{background:radial-gradient(circle at top,#12376f 0%,var(--bg) 38%,#05101f 100%);color:var(--tx);font-family:system-ui,sans-serif;font-size:14px;min-height:100vh;padding-bottom:130px}
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
.hdr-producer{font-size:13px;font-weight:700;color:#fff;background:linear-gradient(135deg,#1a7fe8,#17a8ff);padding:8px 18px;border-radius:20px;text-decoration:none;display:flex;align-items:center;gap:7px;box-shadow:0 2px 12px rgba(23,168,255,.35);transition:filter .2s,box-shadow .2s}
.hdr-producer:hover{filter:brightness(1.1);box-shadow:0 4px 18px rgba(23,168,255,.5)}

/* ── Greeting ── */
.greeting{padding:28px 24px 6px;max-width:1400px;margin:0 auto}
.greeting-title{font-size:24px;font-weight:700;letter-spacing:-.02em;margin-bottom:4px}
.greeting-sub{font-size:14px;color:var(--mu)}

/* ── Resume banner ── */
#resume-bar{background:rgba(23,168,255,.1);border:1px solid rgba(23,168,255,.3);border-radius:12px;padding:12px 18px;margin:14px 24px 0;max-width:1400px;margin-left:auto;margin-right:auto;display:flex;align-items:center;gap:14px}
#resume-bar button{border:none;border-radius:8px;padding:7px 16px;font-size:13px;font-weight:700;cursor:pointer;font-family:inherit}
.resume-yes{background:var(--acc);color:#fff}
.resume-yes:hover{filter:brightness(1.1)}
.resume-no{background:rgba(23,52,95,.6);color:var(--mu);border:1px solid var(--bor)!important}
.resume-no:hover{color:var(--tx)}

/* ── Search ── */
.search-wrap{padding:14px 24px 0;max-width:1400px;margin:0 auto}
.search-box{width:100%;padding:11px 16px 11px 42px;background:#0d1e40;border:1px solid var(--bor);border-radius:12px;color:var(--tx);font-size:14px;font-family:inherit;outline:none;transition:border-color .2s}
.search-box:focus{border-color:var(--acc)}
.search-box::placeholder{color:var(--mu)}
.search-icon{position:absolute;left:14px;top:50%;transform:translateY(-50%);font-size:16px;pointer-events:none}
.search-rel{position:relative}

/* ── Help bar ── */
.help-bar{background:rgba(23,168,255,.07);border:1px solid rgba(23,168,255,.2);border-radius:12px;padding:12px 18px;margin:14px 24px 0;max-width:1400px;margin-left:auto;margin-right:auto;display:flex;align-items:flex-start;gap:12px;cursor:pointer;transition:background .2s}
.help-bar:hover{background:rgba(23,168,255,.12)}
.help-bar-icon{font-size:18px;flex-shrink:0;margin-top:1px}
.help-bar-text{flex:1}
.help-bar-text strong{font-size:13px;color:var(--acc)}
.help-bar-text p{font-size:12px;color:var(--mu);margin-top:3px;line-height:1.5}
.help-steps{display:none;margin-top:10px;padding-top:10px;border-top:1px solid var(--bor)}
.help-steps.open{display:block}
.help-step{display:flex;align-items:flex-start;gap:10px;margin-bottom:8px;font-size:12px;color:var(--mu)}
.help-num{background:var(--acc);color:#fff;width:20px;height:20px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;flex-shrink:0}

/* ── Message bar ── */
#msg-bar{height:32px;display:flex;align-items:center;padding:0 28px;font-size:12px;opacity:0;transition:opacity .3s;max-width:1400px;margin:0 auto}

/* ── Section header ── */
.sec-hdr{padding:20px 24px 10px;max-width:1400px;margin:0 auto;display:flex;align-items:center;gap:10px}
.sec-hdr-name{font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.08em}
.sec-hdr-name.favs{color:#f59e0b}
.sec-hdr-name.site{color:var(--mu)}
.site-badge{font-size:11px;font-weight:600;padding:2px 8px;border-radius:20px}
.site-online{background:rgba(34,197,94,.12);color:var(--ok);border:1px solid rgba(34,197,94,.3)}
.site-offline{background:rgba(138,164,200,.08);color:var(--mu);border:1px solid rgba(138,164,200,.2)}

/* ── Stream grid ── */
.stream-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px;padding:0 24px 8px;max-width:1400px;margin:0 auto}

/* ── Stream card ── */
.stream-card{background:var(--sur);border:1.5px solid var(--bor);border-radius:20px;padding:22px;cursor:pointer;transition:border-color .25s,box-shadow .25s,transform .15s,background .25s;position:relative;overflow:hidden;-webkit-tap-highlight-color:transparent}
.stream-card::after{content:"";position:absolute;inset:0;border-radius:20px;background:transparent;transition:background .2s;pointer-events:none}
.stream-card:hover{border-color:var(--acc);transform:translateY(-3px);box-shadow:0 14px 36px rgba(0,0,0,.5)}
.stream-card:hover::after{background:rgba(23,168,255,.04)}
.stream-card:active{transform:translateY(-1px)}
.stream-card.playing{border-color:var(--ok);box-shadow:0 0 0 1px var(--ok),0 12px 36px rgba(34,197,94,.22);background:linear-gradient(160deg,#0d2346 0%,#092a16 100%)}
.stream-card.connecting{border-color:var(--acc);animation:card-connect 1.8s ease-in-out infinite;box-shadow:0 0 0 1px rgba(23,168,255,.4)}
@keyframes card-connect{0%,100%{box-shadow:0 0 0 1px rgba(23,168,255,.3),0 6px 24px rgba(23,168,255,.1)}50%{box-shadow:0 0 0 2px rgba(23,168,255,.6),0 8px 28px rgba(23,168,255,.25)}}
.stream-card.offline{opacity:.5}
.stream-card.offline:hover{transform:none;border-color:var(--bor);box-shadow:none;cursor:not-allowed}

/* ── Fav button ── */
.fav-btn{position:absolute;top:14px;right:14px;background:none;border:none;font-size:18px;cursor:pointer;opacity:.35;transition:opacity .2s,transform .15s;line-height:1;padding:2px}
.fav-btn:hover{opacity:.8;transform:scale(1.2)}
.fav-btn.active{opacity:1}

/* ── Avatar ── */
.avatar{width:58px;height:58px;border-radius:16px;display:flex;align-items:center;justify-content:center;font-size:23px;font-weight:800;color:#fff;flex-shrink:0;text-shadow:0 1px 4px rgba(0,0,0,.4);box-shadow:0 4px 14px rgba(0,0,0,.3);transition:transform .2s}
.stream-card:hover .avatar{transform:scale(1.06) rotate(-2deg)}

/* ── Card text ── */
.card-top{display:flex;align-items:flex-start;gap:14px;margin-bottom:14px}
.card-meta{flex:1;min-width:0;padding-right:22px}
.card-name{font-size:16px;font-weight:700;line-height:1.25;margin-bottom:4px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;word-break:break-word}
.card-site{font-size:12px;color:var(--mu);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:6px}
.card-on-air{font-size:12px;color:var(--acc);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-height:16px;font-style:italic}

/* ── Level meter ── */
.level-wrap{display:flex;align-items:flex-end;gap:3px;height:22px;margin-bottom:18px;padding:0 2px}
.lv{width:6px;border-radius:3px;transition:height .12s,background .12s;flex-shrink:0}

/* ── Equalizer animation ── */
@keyframes eq1{0%,100%{height:4px}30%{height:20px}60%{height:8px}}
@keyframes eq2{0%,100%{height:10px}40%{height:4px}70%{height:18px}}
@keyframes eq3{0%,100%{height:18px}20%{height:6px}80%{height:14px}}
@keyframes eq4{0%,100%{height:6px}50%{height:22px}}
@keyframes eq5{0%,100%{height:14px}35%{height:4px}75%{height:20px}}
@keyframes eq6{0%,100%{height:8px}45%{height:16px}85%{height:4px}}
@keyframes eq7{0%,100%{height:16px}25%{height:4px}65%{height:12px}}
@keyframes eq8{0%,100%{height:4px}55%{height:18px}}

/* ── Listen button ── */
.listen-btn{width:100%;padding:14px;border-radius:14px;border:none;font-size:15px;font-weight:700;cursor:pointer;font-family:inherit;transition:all .2s;display:flex;align-items:center;justify-content:center;gap:10px;letter-spacing:.01em;position:relative;overflow:hidden}
.listen-btn::after{content:"";position:absolute;inset:0;background:rgba(255,255,255,0);transition:background .15s}
.listen-btn:active::after{background:rgba(255,255,255,.12)}
.listen-btn.start{background:linear-gradient(135deg,#1565d8,var(--acc));color:#fff;box-shadow:0 4px 16px rgba(23,168,255,.4)}
.listen-btn.start:hover{box-shadow:0 6px 22px rgba(23,168,255,.6);transform:translateY(-2px)}
.listen-btn.stop{background:linear-gradient(135deg,#157a42,var(--ok));color:#fff;box-shadow:0 4px 16px rgba(34,197,94,.35)}
.listen-btn.stop:hover{box-shadow:0 6px 22px rgba(34,197,94,.55);transform:translateY(-2px)}
.listen-btn.connecting{background:linear-gradient(135deg,#1a3566,#1e4a8a);color:var(--acc);box-shadow:none;cursor:default;animation:btn-pulse 1.4s ease-in-out infinite}
@keyframes btn-pulse{0%,100%{opacity:.75}50%{opacity:1}}
.listen-btn:disabled{opacity:.4;cursor:not-allowed;transform:none!important;box-shadow:none!important;background:#1a3050}
/* ── Connection hint ── */
.conn-hint{font-size:12px;color:var(--mu);text-align:center;margin-top:9px;min-height:16px;line-height:1.4;transition:opacity .3s}

/* ── Badge ── */
.badge{font-size:11px;font-weight:600;padding:3px 10px;border-radius:20px;white-space:nowrap;flex-shrink:0}
.b-ok{background:rgba(34,197,94,.12);color:var(--ok);border:1px solid rgba(34,197,94,.3)}
.b-al{background:rgba(245,158,11,.12);color:var(--wn);border:1px solid rgba(245,158,11,.35)}
.b-wn{background:rgba(245,158,11,.12);color:var(--wn);border:1px solid rgba(245,158,11,.3)}
.b-mu{background:rgba(138,164,200,.08);color:var(--mu);border:1px solid rgba(138,164,200,.2)}

/* ── Now-playing bar ── */
#now-bar{position:fixed;bottom:0;left:0;right:0;background:linear-gradient(0deg,rgba(5,16,31,.99) 0%,rgba(9,26,54,.98) 100%);border-top:2px solid var(--ok);padding:0 24px;display:flex;align-items:center;gap:18px;height:78px;transform:translateY(100%);transition:transform .35s cubic-bezier(.4,0,.2,1);z-index:100;backdrop-filter:blur(16px);box-shadow:0 -4px 30px rgba(34,197,94,.12)}
#now-bar.up{transform:translateY(0)}
.np-eq{display:flex;align-items:flex-end;gap:3px;height:24px;width:32px;flex-shrink:0}
.np-bar{width:4px;border-radius:2px;background:var(--ok)}
.np-info{flex:1;min-width:0}
.np-name{font-size:15px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.np-meta{font-size:12px;color:var(--mu);margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.np-vol{display:flex;align-items:center;gap:8px;flex-shrink:0}
.np-vol-icon{font-size:18px;cursor:pointer;min-width:24px;text-align:center}
input[type=range]{-webkit-appearance:none;appearance:none;height:4px;border-radius:2px;background:rgba(23,52,95,.9);outline:none;width:100px;cursor:pointer}
input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:18px;height:18px;border-radius:50%;background:var(--acc);cursor:pointer;box-shadow:0 0 6px rgba(23,168,255,.5)}
input[type=range]::-moz-range-thumb{width:18px;height:18px;border-radius:50%;background:var(--acc);cursor:pointer;border:none}
.np-stop{background:rgba(239,68,68,.15);color:var(--al);border:1px solid rgba(239,68,68,.3);padding:9px 18px;border-radius:10px;font-size:13px;font-weight:700;cursor:pointer;font-family:inherit;transition:all .2s;white-space:nowrap}
.np-stop:hover{background:rgba(239,68,68,.28)}

/* ── Empty / loading ── */
.empty{text-align:center;padding:60px 24px;color:var(--mu)}
.empty-icon{font-size:48px;margin-bottom:16px;opacity:.4}
.empty-title{font-size:18px;font-weight:600;color:var(--tx);margin-bottom:8px}
.empty-desc{font-size:14px;line-height:1.7}
.no-results{text-align:center;padding:40px 24px;color:var(--mu);max-width:1400px;margin:0 auto}
.skeleton{animation:pulse 1.5s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:.4}50%{opacity:.7}}
.skel-card{background:var(--sur);border:1.5px solid var(--bor);border-radius:18px;padding:22px;height:210px}
.skel-row{background:rgba(23,52,95,.6);border-radius:6px;margin-bottom:10px}

/* ── Retry banner ── */
.retry-banner{background:rgba(245,158,11,.1);border:1px solid rgba(245,158,11,.3);border-radius:10px;padding:10px 16px;font-size:13px;color:var(--wn);display:flex;align-items:center;gap:10px;margin:0 24px 12px;max-width:1400px;margin-left:auto;margin-right:auto}

/* ── Scrollbar ── */
::-webkit-scrollbar{width:6px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:var(--bor);border-radius:3px}

/* ── Responsive ── */
@media(max-width:640px){
  .stream-grid{grid-template-columns:1fr;padding:0 14px 8px}
  .greeting{padding:18px 16px 6px}
  .sec-hdr{padding:16px 16px 8px}
  .search-wrap{padding:12px 16px 0}
  .help-bar,#resume-bar{margin:12px 16px 0}
  .hdr{padding:12px 16px}
  .greeting-title{font-size:20px}
  #now-bar{padding:0 14px;gap:12px}
  input[type=range]{width:75px}
  .listen-btn{font-size:16px;padding:16px}
}
</style>
</head>
<body>

<!-- ── Header ── -->
<header class="hdr">
  <span class="hdr-logo">🎧</span>
  <div>
    <div class="hdr-title">Listener</div>
    <div class="hdr-sub">Live streams</div>
  </div>
  <div class="hdr-right">
    {% if has_presenter %}<a href="/presenter" class="hdr-producer">🎙 Producer View</a>{% endif %}
    {% if username %}<div class="hdr-user">👤 {{username}}</div>{% endif %}
    <a href="/" class="hdr-back">← Back</a>
  </div>
</header>

<!-- ── Greeting ── -->
<div class="greeting">
  <div class="greeting-title" id="greeting-text">Good day 👋</div>
  <div class="greeting-sub">Tap a station card to start listening.</div>
</div>

<!-- ── Resume last station banner ── -->
<div id="resume-bar" style="display:none">
  <span style="font-size:18px">▶</span>
  <div style="flex:1">
    <div style="font-size:13px;font-weight:700;color:var(--acc)">Resume where you left off?</div>
    <div style="font-size:12px;color:var(--mu);margin-top:2px" id="resume-name"></div>
  </div>
  <button class="resume-yes" id="resume-yes-btn">▶ Resume</button>
  <button class="resume-no" id="resume-no-btn" style="background:none;border:none!important;color:var(--mu);font-size:12px;cursor:pointer;padding:6px 10px">Not now</button>
</div>

<!-- ── Search ── -->
<div class="search-wrap">
  <div class="search-rel">
    <span class="search-icon">🔍</span>
    <input id="search-input" class="search-box" type="text" placeholder="Search stations…" autocomplete="off" spellcheck="false">
  </div>
</div>

<!-- ── Help bar ── -->
<div class="help-bar" id="help-toggle">
  <div class="help-bar-icon">💡</div>
  <div class="help-bar-text">
    <strong>How to use the Listener</strong>
    <p id="help-p">Tap to see quick-start guide</p>
    <div class="help-steps" id="help-steps">
      <div class="help-step"><div class="help-num">1</div><div>Find your station below. Use the search box if you have a lot of stations.</div></div>
      <div class="help-step"><div class="help-num">2</div><div>Tap the blue <strong>🎧 Listen</strong> button on a station card to start audio.</div></div>
      <div class="help-step"><div class="help-num">3</div><div>A green bar appears at the bottom of the screen showing what is playing. Use the volume slider to adjust level.</div></div>
      <div class="help-step"><div class="help-num">4</div><div>To stop, tap <strong>⏹ Stop</strong> on the card or the <strong>Stop</strong> button in the green bar.</div></div>
      <div class="help-step"><div class="help-num">5</div><div>Tap ⭐ on any station card to save it to <em>My Stations</em> so it always appears at the top.</div></div>
      <div class="help-step"><div class="help-num">6</div><div>Stations marked <span class="badge b-mu" style="font-size:10px">○ Not Available</span> are currently off-air. Try again later or contact your engineer.</div></div>
    </div>
  </div>
</div>

<!-- ── Message bar ── -->
<div id="msg-bar"></div>

<!-- ── Stream content ── -->
<div id="content"></div>

<!-- ── Retry banner ── -->
<div id="retry-banner" class="retry-banner" style="display:none">
  ⚠ <span id="retry-text">Stream interrupted — reconnecting…</span>
</div>

<!-- ── Now-playing bar ── -->
<div id="now-bar">
  <div class="np-eq" id="np-eq">
    <div class="np-bar" id="npb1" style="height:10px"></div>
    <div class="np-bar" id="npb2" style="height:18px"></div>
    <div class="np-bar" id="npb3" style="height:6px"></div>
    <div class="np-bar" id="npb4" style="height:14px"></div>
    <div class="np-bar" id="npb5" style="height:8px"></div>
  </div>
  <div class="np-info">
    <div class="np-name" id="np-name">—</div>
    <div class="np-meta" id="np-meta"></div>
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
  ['#1a7fe8','#17a8ff'],
  ['#16a047','#22c55e'],
  ['#c87f0a','#f59e0b'],
  ['#9333e8','#a855f7'],
  ['#d91a6e','#ec4899'],
  ['#0d9488','#14b8a6'],
  ['#c2440f','#f97316'],
  ['#c81e1e','#ef4444'],
];
var POLL_MS  = 10000;
var RETRY_MS = 6000;
var LS_FAVS  = 'ss_listener_favs';
var LS_LAST  = 'ss_listener_last';

// ── State ──────────────────────────────────────────────────────────────────
var _audio            = null;
var _playing          = null;   // {site, idx, name, url}
var _connecting       = false;  // true between startAudio and first 'playing' event
var _connectSlow      = false;  // true after 10s without audio
var _connectSlowTimer = null;
var _streams          = [];     // flat list, idx = real config index
var _lastBySite       = null;   // last rendered bySite map
var _pollTimer        = null;
var _retryTimer       = null;
var _retryCount       = 0;
var _helpOpen         = false;
var _searchText       = '';
var _favs             = {};     // keyed by site+'|'+idx

// ── Favourites persistence ─────────────────────────────────────────────────
function _loadFavs(){
  try{ _favs = JSON.parse(localStorage.getItem(LS_FAVS)||'{}'); }catch(e){ _favs={}; }
}
function _saveFavs(){
  try{ localStorage.setItem(LS_FAVS, JSON.stringify(_favs)); }catch(e){}
}
function _favKey(site,idx){ return site+'|'+idx; }
function _isFav(site,idx){ return !!_favs[_favKey(site,idx)]; }
function _toggleFav(site,idx){
  var k=_favKey(site,idx);
  if(_favs[k]) delete _favs[k]; else _favs[k]=true;
  _saveFavs();
  if(_lastBySite) renderContent(_lastBySite);
}

// ── Last-playing persistence ───────────────────────────────────────────────
function _storeLastPlaying(p){
  try{ localStorage.setItem(LS_LAST, JSON.stringify(p)); }catch(e){}
}
function _clearLastPlaying(){
  try{ localStorage.removeItem(LS_LAST); }catch(e){}
}
function _loadLastPlaying(){
  try{ return JSON.parse(localStorage.getItem(LS_LAST)||'null'); }catch(e){ return null; }
}

// ── Greeting ───────────────────────────────────────────────────────────────
(function(){
  var h=new Date().getHours();
  var g=h<12?'Good morning':h<17?'Good afternoon':'Good evening';
  var user='{{username|e}}';
  document.getElementById('greeting-text').textContent=g+(user?', '+user:'')+' 👋';
})();

// ── Resume banner ──────────────────────────────────────────────────────────
(function(){
  var last=_loadLastPlaying();
  if(!last||!last.site||last.idx==null) return;
  var bar=document.getElementById('resume-bar');
  document.getElementById('resume-name').textContent=last.name+' — '+last.site;
  bar.style.display='flex';
  document.getElementById('resume-yes-btn').addEventListener('click',function(){
    bar.style.display='none';
    // Wait for streams to load, then start playing
    _pendingResume=last;
  });
  document.getElementById('resume-no-btn').addEventListener('click',function(){
    bar.style.display='none';
    _clearLastPlaying();
  });
})();
var _pendingResume=null;

// ── Search ─────────────────────────────────────────────────────────────────
document.getElementById('search-input').addEventListener('input',function(){
  _searchText=this.value.trim().toLowerCase();
  if(_lastBySite) renderContent(_lastBySite);
});

// ── Help toggle ────────────────────────────────────────────────────────────
document.getElementById('help-toggle').addEventListener('click',function(){
  _helpOpen=!_helpOpen;
  document.getElementById('help-steps').classList.toggle('open',_helpOpen);
  document.getElementById('help-p').textContent=_helpOpen?'Tap to collapse':'Tap to see quick-start guide';
});

// ── Utilities ──────────────────────────────────────────────────────────────
function _esc(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function _colorFor(name){
  var h=0; for(var i=0;i<name.length;i++) h=(h*31+name.charCodeAt(i))&0x7fffffff;
  return AVATAR_COLORS[h%AVATAR_COLORS.length];
}
function _dbPct(db){
  if(db===null||db===undefined||isNaN(db)) return 0;
  return Math.max(0,Math.min(100,(parseFloat(db)+60)/60*100));
}
function _lvColor(pct){ return pct>75?'var(--al)':pct>50?'var(--wn)':'var(--ok)'; }
function _encodeSite(s){ return s.split('/').map(encodeURIComponent).join('/'); }
// Safe index: use _client_idx when present and not null/undefined, else fallback
function _safeIdx(s,fallback){ return (s._client_idx!=null)?s._client_idx:fallback; }

// ── Message ────────────────────────────────────────────────────────────────
var _msgT=null;
function showMsg(text,type){
  var el=document.getElementById('msg-bar');
  el.textContent=text;
  el.style.color=type==='ok'?'var(--ok)':type==='warn'?'var(--wn)':'var(--al)';
  el.style.opacity='1';
  clearTimeout(_msgT);
  _msgT=setTimeout(function(){ el.style.opacity='0'; },3500);
}

// ── Load streams ───────────────────────────────────────────────────────────
_loadFavs();

function loadStreams(initial){
  if(initial) showSkeleton();
  fetch('/hub/data',{credentials:'same-origin'})
    .then(function(r){ if(!r.ok) throw new Error(r.status); return r.json(); })
    .then(function(d){
      var sites=d.sites||[];
      _streams=[];
      var bySite={};
      sites.forEach(function(site){
        var sname=site.site||site.name||'?';
        bySite[sname]={online:site.online,streams:[]};
        (site.streams||[]).forEach(function(s,i){
          if(!s.enabled) return;
          var ci=_safeIdx(s,i);
          var obj={
            site:   sname,
            idx:    ci,
            name:   s.name||('Stream '+(i+1)),
            level:  s.level_dbfs,
            status: s.ai_status||'OK',
            online: !!site.online,
            rds:    s.fm_rds_ps||'',
            dab:    s.dab_service||'',
            label:  s.label||'',
          };
          _streams.push(obj);
          bySite[sname].streams.push(obj);
        });
      });
      _lastBySite=bySite;
      renderContent(bySite);
      schedulePoll();
      // Auto-resume if pending
      if(_pendingResume){
        var p=_pendingResume; _pendingResume=null;
        var match=_streams.find(function(x){ return x.site===p.site&&x.idx===p.idx; });
        if(match) startAudio(match.site,match.idx,match.name);
      }
    })
    .catch(function(){
      if(initial){
        document.getElementById('content').innerHTML=
          '<div class="empty"><div class="empty-icon">📡</div>'
          +'<div class="empty-title">Could not load streams</div>'
          +'<div class="empty-desc">Check your connection and <a href="" onclick="location.reload();return false">refresh the page</a>.</div></div>';
      }
    });
}

// ── Skeleton ───────────────────────────────────────────────────────────────
function showSkeleton(){
  var html='<div class="stream-grid">';
  for(var i=0;i<6;i++) html+='<div class="skel-card skeleton">'
    +'<div class="skel-row" style="height:56px;width:56px;border-radius:15px;margin-bottom:14px"></div>'
    +'<div class="skel-row" style="height:16px;width:65%"></div>'
    +'<div class="skel-row" style="height:12px;width:40%;margin-top:6px"></div>'
    +'<div class="skel-row" style="height:22px;margin-top:16px"></div>'
    +'<div class="skel-row" style="height:48px;margin-top:18px;border-radius:14px"></div>'
    +'</div>';
  html+='</div>';
  document.getElementById('content').innerHTML=html;
}

// ── Render content ─────────────────────────────────────────────────────────
function renderContent(bySite){
  var siteNames=Object.keys(bySite).sort();
  var hasAny=false;
  siteNames.forEach(function(sn){ if(bySite[sn].streams.length) hasAny=true; });
  if(!hasAny){
    document.getElementById('content').innerHTML=
      '<div class="empty"><div class="empty-icon">🔇</div>'
      +'<div class="empty-title">No streams available</div>'
      +'<div class="empty-desc">There are no live streams assigned to your account.<br>Contact your administrator if you believe this is an error.</div></div>';
    _rebindClicks();
    return;
  }

  // Apply search filter
  var filtered={};
  siteNames.forEach(function(sn){
    var sg=bySite[sn];
    var streams=_searchText
      ? sg.streams.filter(function(s){ return s.name.toLowerCase().indexOf(_searchText)>=0; })
      : sg.streams;
    filtered[sn]={online:sg.online,streams:streams};
  });

  var html='';

  // Favourites section
  var favStreams=[];
  siteNames.forEach(function(sn){
    filtered[sn].streams.forEach(function(s){
      if(_isFav(s.site,s.idx)) favStreams.push(s);
    });
  });
  if(favStreams.length){
    html+='<div class="sec-hdr"><div class="sec-hdr-name favs">⭐ My Stations</div></div>';
    html+='<div class="stream-grid">';
    favStreams.forEach(function(s){ html+=renderCard(s); });
    html+='</div>';
  }

  // Site sections
  var allFiltered=true;
  siteNames.forEach(function(sn){
    var sg=filtered[sn];
    if(!sg.streams.length) return;
    allFiltered=false;
    html+='<div class="sec-hdr">'
      +'<div class="sec-hdr-name site">📍 '+_esc(sn)+'</div>'
      +'<span class="badge site-badge '+(sg.online?'site-online':'site-offline')+'">'
      +(sg.online?'● Online':'○ Offline')+'</span>'
      +'</div>';
    html+='<div class="stream-grid">';
    sg.streams.forEach(function(s){ html+=renderCard(s); });
    html+='</div>';
  });

  if(allFiltered && _searchText){
    html+='<div class="no-results">No stations match "<strong>'+_esc(_searchText)+'</strong>"</div>';
  }

  document.getElementById('content').innerHTML=html;
  _rebindClicks();
}

// Single persistent click handler — replaced each render to avoid accumulation
var _clickBound=false;
function _rebindClicks(){
  var el=document.getElementById('content');
  if(!el) return;
  // Replace node to cleanly strip old listeners
  var fresh=el.cloneNode(false);
  fresh.innerHTML=el.innerHTML;
  el.parentNode.replaceChild(fresh,el);
  fresh.addEventListener('click',_onCardClick);
}

// ── Render a single card ───────────────────────────────────────────────────
function renderCard(s){
  var isPlaying=_playing&&_playing.site===s.site&&_playing.idx===s.idx;
  var col=_colorFor(s.name);
  var initial=(s.name.match(/[A-Z0-9]/i)||[s.name[0]||'?'])[0].toUpperCase();
  var pct=_dbPct(s.level);
  var fav=_isFav(s.site,s.idx);

  // Status badge — plain English, not technical terms
  var badge='';
  if(!s.online){
    badge='<span class="badge b-mu">○ Not Available</span>';
  } else if(s.status==='ALERT'){
    badge='<span class="badge b-al">⚠ Signal Issue</span>';
  } else if(s.status==='WARN'){
    badge='<span class="badge b-wn">⚡ Caution</span>';
  } else {
    badge='<span class="badge b-ok">● On Air</span>';
  }

  // What's on air line
  var sub=s.rds||s.dab||s.label||'';

  // Level bars
  var bars='';
  for(var b=0;b<8;b++){
    var style;
    if(isPlaying){
      style='animation:eq'+(b+1)+' '+(0.55+b*0.07).toFixed(2)+'s infinite ease-in-out;'
           +'background:'+col[1]+';height:4px';
    } else {
      var frac=1-b*0.09;
      var bh=s.online?Math.max(2,pct/100*20*frac):2;
      style='height:'+bh.toFixed(1)+'px;background:'+_lvColor(pct);
    }
    bars+='<div class="lv" style="'+style+'"></div>';
  }

  var btnClass=isPlaying?'stop':'start';
  var btnText =isPlaying?'⏹&nbsp;&nbsp;Stop':'🎧&nbsp;&nbsp;Listen';
  var disabled=(!s.online&&!isPlaying)?' disabled':'';

  return '<div class="stream-card'+(isPlaying?' playing':'')+(s.online?'':' offline')+'"'
    +' data-site="'+_esc(s.site)+'" data-idx="'+s.idx+'" data-name="'+_esc(s.name)+'">'
    +'<button class="fav-btn'+(fav?' active':'')+'" data-action="fav"'
    +' data-fav-site="'+_esc(s.site)+'" data-fav-idx="'+s.idx+'"'
    +' title="'+(fav?'Remove from My Stations':'Add to My Stations')+'">'
    +(fav?'⭐':'☆')+'</button>'
    +'<div class="card-top">'
    +'<div class="avatar" style="background:linear-gradient(135deg,'+col[0]+','+col[1]+')">'+_esc(initial)+'</div>'
    +'<div class="card-meta">'
    +'<div class="card-name">'+_esc(s.name)+'</div>'
    +'<div class="card-site">'+_esc(s.site)+'</div>'
    +'<div class="card-on-air">'+_esc(sub)+'</div>'
    +'</div>'
    +badge
    +'</div>'
    +'<div class="level-wrap">'+bars+'</div>'
    +'<button class="listen-btn '+btnClass+'"'+disabled+' data-action="listen">'+btnText+'</button>'
    +'<div class="conn-hint"></div>'
    +'</div>';
}

// ── Card click delegation ──────────────────────────────────────────────────
function _onCardClick(e){
  // Fav toggle
  var fb=e.target.closest('[data-action="fav"]');
  if(fb){ _toggleFav(fb.dataset.favSite, parseInt(fb.dataset.favIdx,10)); return; }

  // Listen / stop
  var btn=e.target.closest('[data-action="listen"]');
  if(!btn||btn.disabled) return;
  var card=btn.closest('.stream-card');
  if(!card) return;
  var site=card.dataset.site;
  var idx =parseInt(card.dataset.idx,10);
  var name=card.dataset.name;
  if(_playing&&_playing.site===site&&_playing.idx===idx){
    stopAudio();
  } else {
    startAudio(site,idx,name);
  }
}

// ── Start audio ────────────────────────────────────────────────────────────
function startAudio(site,idx,name){
  stopAudio(true);
  clearRetry();

  var url='/hub/site/'+_encodeSite(site)+'/stream/'+idx+'/live';
  _playing={site:site,idx:idx,name:name,url:url};
  _connecting=true;
  _connectSlow=false;
  _storeLastPlaying(_playing);

  // "Still loading" after 10 s
  _connectSlowTimer=setTimeout(function(){
    if(_connecting&&_playing&&_playing.site===site&&_playing.idx===idx){
      _connectSlow=true;
      refreshCards();
      showMsg('⏳ Still loading — hang on, this can take up to 30 seconds…','warn');
    }
  },10000);

  _audio=new Audio();
  _audio.preload='none';
  _audio.volume=parseFloat(document.getElementById('vol-slider').value);
  _audio.addEventListener('error',_onAudioError);
  _audio.addEventListener('ended',_onAudioEnded);
  _audio.addEventListener('playing',function(){
    clearTimeout(_connectSlowTimer);
    _connecting=false;
    _connectSlow=false;
    hideRetry();
    _retryCount=0;
    refreshCards();
  });
  _audio.src=url;
  _audio.play().catch(function(){});

  // Find on-air text for now-playing bar
  var match=_streams.find(function(x){ return x.site===site&&x.idx===idx; });
  var sub=match?(match.rds||match.dab||match.label||''):'';
  showNowPlaying(name,site,sub);
  refreshCards();
  showMsg('▶ Connecting to '+name+'…','ok');
}

// ── Audio events ───────────────────────────────────────────────────────────
function _onAudioError(){
  if(!_playing) return;
  _retryCount++;
  var wait=Math.min(30,Math.ceil(RETRY_MS/1000*_retryCount));
  showRetry('Connection lost — retrying in '+wait+'s (attempt '+_retryCount+')');
  var p=_playing;
  _retryTimer=setTimeout(function(){
    if(_playing&&_playing.site===p.site&&_playing.idx===p.idx) startAudio(p.site,p.idx,p.name);
  },wait*1000);
}
function _onAudioEnded(){ if(_playing) _onAudioError(); }

// ── Stop audio ─────────────────────────────────────────────────────────────
function stopAudio(silent){
  clearRetry();
  clearTimeout(_connectSlowTimer);
  _connecting=false;
  _connectSlow=false;
  if(_audio){
    _audio.removeEventListener('error',_onAudioError);
    _audio.removeEventListener('ended',_onAudioEnded);
    try{ _audio.pause(); }catch(e){}
    _audio.src='';
    _audio=null;
  }
  _playing=null;
  _clearLastPlaying();
  hideNowPlaying();
  if(!silent){ refreshCards(); showMsg('⏹ Stopped','warn'); }
  _releaseWakeLock();
}

function clearRetry(){
  clearTimeout(_retryTimer); _retryTimer=null;
}

// ── Now-playing bar ────────────────────────────────────────────────────────
var _eqRaf=null;
function showNowPlaying(name,site,sub){
  document.getElementById('np-name').textContent=name;
  document.getElementById('np-meta').textContent=(sub?sub+' · ':'')+site;
  document.getElementById('now-bar').classList.add('up');
  _startEqAnim();
  _acquireWakeLock();
}
function hideNowPlaying(){
  document.getElementById('now-bar').classList.remove('up');
  _stopEqAnim();
}
function _startEqAnim(){
  _stopEqAnim();
  var bars=[document.getElementById('npb1'),document.getElementById('npb2'),
            document.getElementById('npb3'),document.getElementById('npb4'),
            document.getElementById('npb5')];
  var phases=bars.map(function(_,i){ return i*0.6; });
  var t0=performance.now();
  function frame(t){
    var elapsed=(t-t0)/1000;
    bars.forEach(function(b,i){
      var h=12+10*Math.sin(elapsed*3+phases[i]);
      b.style.height=h.toFixed(1)+'px';
    });
    _eqRaf=requestAnimationFrame(frame);
  }
  _eqRaf=requestAnimationFrame(frame);
}
function _stopEqAnim(){
  if(_eqRaf){ cancelAnimationFrame(_eqRaf); _eqRaf=null; }
  ['npb1','npb2','npb3','npb4','npb5'].forEach(function(id,i){
    var el=document.getElementById(id);
    if(el) el.style.height=['10px','18px','6px','14px','8px'][i];
  });
}

// ── Retry banner ───────────────────────────────────────────────────────────
function showRetry(msg){ var b=document.getElementById('retry-banner'); b.style.display='flex'; document.getElementById('retry-text').textContent=msg; }
function hideRetry(){ document.getElementById('retry-banner').style.display='none'; }

// ── Refresh card visuals (no re-render) ────────────────────────────────────
function refreshCards(){
  _streams.forEach(function(s){
    var card=document.querySelector('.stream-card[data-site="'+s.site.replace(/"/g,'&quot;')+'"][data-idx="'+s.idx+'"]');
    if(!card) return;
    var isConnecting=_connecting&&_playing&&_playing.site===s.site&&_playing.idx===s.idx;
    var isPlaying=!_connecting&&_playing&&_playing.site===s.site&&_playing.idx===s.idx;
    card.classList.toggle('playing',isPlaying);
    card.classList.toggle('connecting',isConnecting);
    var btn=card.querySelector('.listen-btn');
    if(btn){
      if(isPlaying){
        btn.className='listen-btn stop';
        btn.innerHTML='⏹&nbsp;&nbsp;Stop';
        btn.disabled=false;
      } else if(isConnecting){
        btn.className='listen-btn connecting';
        btn.innerHTML=_connectSlow?'⏳&nbsp;&nbsp;Still loading…':'⏳&nbsp;&nbsp;Connecting…';
        btn.disabled=false; // allow tap-to-cancel
      } else {
        btn.className='listen-btn start';
        btn.innerHTML='🎧&nbsp;&nbsp;Listen';
        btn.disabled=!s.online;
      }
    }
    var hint=card.querySelector('.conn-hint');
    if(hint){
      if(isConnecting&&_connectSlow) hint.textContent='This can take up to 30 seconds — tap Stop to cancel';
      else if(isConnecting) hint.textContent='Starting up… tap Stop if you want to cancel';
      else hint.textContent='';
    }
    var bars=card.querySelectorAll('.lv');
    var pct=_dbPct(s.level);
    var col=_colorFor(s.name)[1];
    bars.forEach(function(bar,b){
      if(isPlaying||isConnecting){
        bar.style.animation='eq'+(b+1)+' '+(0.55+b*0.07).toFixed(2)+'s infinite ease-in-out';
        bar.style.background=col;
        bar.style.height='4px';
      } else {
        bar.style.animation='';
        var bh=s.online?Math.max(2,_dbPct(s.level)/100*20*(1-b*0.09)):2;
        bar.style.height=bh.toFixed(1)+'px';
        bar.style.background=_lvColor(pct);
      }
    });
  });
}

// ── Poll for level updates ─────────────────────────────────────────────────
function schedulePoll(){
  clearTimeout(_pollTimer);
  _pollTimer=setTimeout(pollLevels,POLL_MS);
}
function pollLevels(){
  fetch('/hub/data',{credentials:'same-origin'})
    .then(function(r){ return r.json(); })
    .then(function(d){
      (d.sites||[]).forEach(function(site){
        var sname=site.site||'';
        (site.streams||[]).forEach(function(s,i){
          var ci=_safeIdx(s,i);  // use real config index, matching loadStreams
          var match=_streams.find(function(x){ return x.site===sname&&x.idx===ci; });
          if(match){
            match.level =s.level_dbfs;
            match.status=s.ai_status||'OK';
            match.online=!!site.online;
            match.rds   =s.fm_rds_ps||'';
            match.dab   =s.dab_service||'';
            // Update now-playing meta if this is the active stream
            if(_playing&&_playing.site===sname&&_playing.idx===ci){
              var sub=match.rds||match.dab||match.label||'';
              document.getElementById('np-meta').textContent=(sub?sub+' · ':'')+sname;
            }
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
    var isPlaying=_playing&&_playing.site===s.site&&_playing.idx===s.idx;
    if(isPlaying) return;
    var card=document.querySelector('.stream-card[data-site="'+s.site.replace(/"/g,'&quot;')+'"][data-idx="'+s.idx+'"]');
    if(!card) return;
    var bars=card.querySelectorAll('.lv');
    var pct=_dbPct(s.level);
    bars.forEach(function(bar,b){
      bar.style.height=(s.online?Math.max(2,pct/100*20*(1-b*0.09)):2).toFixed(1)+'px';
      bar.style.background=_lvColor(pct);
    });
    var rdsEl=card.querySelector('.card-on-air');
    if(rdsEl) rdsEl.textContent=s.rds||s.dab||s.label||'';
    // Update badge
    var badge=card.querySelector('.badge:not(.site-badge)');
    if(badge){
      if(!s.online){ badge.className='badge b-mu'; badge.textContent='○ Not Available'; }
      else if(s.status==='ALERT'){ badge.className='badge b-al'; badge.textContent='⚠ Signal Issue'; }
      else if(s.status==='WARN'){ badge.className='badge b-wn'; badge.textContent='⚡ Caution'; }
      else{ badge.className='badge b-ok'; badge.textContent='● On Air'; }
    }
    // Update disabled state of listen button
    var btn=card.querySelector('.listen-btn');
    if(btn&&!isPlaying) btn.disabled=!s.online;
  });
}

// ── Volume ─────────────────────────────────────────────────────────────────
document.getElementById('vol-slider').addEventListener('input',function(){
  var v=parseFloat(this.value);
  if(_audio) _audio.volume=v;
  document.getElementById('vol-icon').textContent=v>0.6?'🔊':v>0.1?'🔉':'🔇';
});
document.getElementById('vol-icon').addEventListener('click',function(){
  var sl=document.getElementById('vol-slider');
  if(sl.value>0){ sl._last=sl.value; sl.value=0; }
  else{ sl.value=sl._last||0.85; }
  sl.dispatchEvent(new Event('input'));
});

// ── Stop button ────────────────────────────────────────────────────────────
document.getElementById('np-stop').addEventListener('click',function(){ stopAudio(); });

// ── Spacebar shortcut ──────────────────────────────────────────────────────
document.addEventListener('keydown',function(e){
  if(e.code==='Space'&&e.target.tagName!=='BUTTON'&&e.target.tagName!=='INPUT'){
    e.preventDefault();
    if(_playing) stopAudio(); else if(_streams.length) showMsg('Tap a station card to start listening','warn');
  }
});

// ── Wake lock ──────────────────────────────────────────────────────────────
var _wakeLock=null;
function _acquireWakeLock(){ if('wakeLock' in navigator) navigator.wakeLock.request('screen').then(function(wl){ _wakeLock=wl; }).catch(function(){}); }
function _releaseWakeLock(){ if(_wakeLock){ _wakeLock.release().catch(function(){}); _wakeLock=null; } }

// ── Kick off ──────────────────────────────────────────────────────────────
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
        has_presenter = any(
            str(rule) == "/presenter"
            for rule in app.url_map.iter_rules()
        )
        return render_template_string(
            _LISTENER_TPL,
            BUILD         = BUILD,
            username      = username,
            has_presenter = has_presenter,
        )
