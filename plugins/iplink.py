# iplink.py — WebRTC IP Link (ipDTL-style browser contribution codec)
# Drop into the plugins/ subdirectory.
#
# The hub creates named "rooms" with a shareable talent URL.
# Any browser (phone, laptop, tablet) opens the link — no download required.
# Low-latency Opus audio, bidirectional IFB/talkback, live level meters, RTT stats.
# Signalling is pure HTTP polling — no WebSocket dependency.

SIGNALSCOPE_PLUGIN = {
    "id":      "iplink",
    "label":   "IP Link",
    "url":     "/hub/iplink",
    "icon":    "🎙",
    "version": "1.1.29",
    "hub_only": True,
}

import json
import os
import threading
import time
import uuid

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_lock   = threading.Lock()
_rooms  = {}      # room_id -> room dict  (see _new_room())
_log    = None    # set in register()

_ROOM_EXPIRE_S    = 7200   # expire idle rooms after 2 h
_STUN_SERVERS     = ["stun:stun.l.google.com:19302", "stun:stun1.l.google.com:19302"]
_STUN             = _STUN_SERVERS   # alias used in route templates

_SIP_CFG_PATH = os.path.join(_BASE_DIR, "iplink_sip_cfg.json")
_SIP_CFG_DEFAULT = {
    "enabled":      False,
    "server":       "",     # wss://pbx.example.com:8089/ws
    "username":     "",
    "password":     "",
    "domain":       "",     # SIP domain / realm (often same as PBX hostname)
    "display_name": "Studio",
}


def _load_sip_cfg() -> dict:
    try:
        with open(_SIP_CFG_PATH) as fh:
            d = json.load(fh)
        cfg = dict(_SIP_CFG_DEFAULT)
        cfg.update({k: v for k, v in d.items() if k in _SIP_CFG_DEFAULT})
        return cfg
    except Exception:
        return dict(_SIP_CFG_DEFAULT)


def _save_sip_cfg(cfg: dict):
    with open(_SIP_CFG_PATH, "w") as fh:
        json.dump(cfg, fh, indent=2)

# Quality presets (communicated to hub JS to set Opus parameters)
_QUALITY = {
    "voice":     {"maxBitrate": 64000,  "stereo": False, "label": "Voice (64 kbps mono)"},
    "broadcast": {"maxBitrate": 128000, "stereo": True,  "label": "Broadcast (128 kbps stereo)"},
    "hifi":      {"maxBitrate": 256000, "stereo": True,  "label": "Hi-Fi (256 kbps stereo)"},
}


# ─── Room helpers ─────────────────────────────────────────────────────────────

def _new_room(name: str, quality: str = "broadcast") -> dict:
    return {
        "id":           str(uuid.uuid4()),
        "name":         name,
        "quality":      quality if quality in _QUALITY else "broadcast",
        "created":      time.time(),
        "last_active":  time.time(),
        "status":       "waiting",        # waiting | offer_received | connected | disconnected
        "offer":        None,             # SDP string from talent
        "answer":       None,             # SDP string from hub
        "talent_ice":   [],               # list of ICE candidate JSON strings (talent→hub)
        "hub_ice":      [],               # list of ICE candidate JSON strings (hub→talent)
        "talent_level": 0.0,
        "hub_level":    0.0,
        "hub_muted":    False,
        "talent_ip":    "",
        "connected_at": None,
        "disconnected_at": None,
        "stats":        {},               # RTT, bitrate, etc. from talent
    }


def _room_age_s(room: dict) -> float:
    return round(time.time() - room["created"])


def _touch(room: dict):
    room["last_active"] = time.time()


def _room_public(room: dict) -> dict:
    """Subset of room dict safe to return to API callers."""
    q = _QUALITY.get(room["quality"], _QUALITY["broadcast"])
    r = dict(room)
    r.pop("offer", None)   # never send SDP to listing endpoints
    r.pop("answer", None)
    r["quality_label"] = q["label"]
    r["talent_ice_count"] = len(room["talent_ice"])
    r["hub_ice_count"]    = len(room["hub_ice"])
    r["age_s"]            = _room_age_s(room)
    if room["connected_at"]:
        r["duration_s"] = round(time.time() - room["connected_at"])
    return r


def _cleanup_thread():
    while True:
        time.sleep(60)
        cutoff = time.time() - _ROOM_EXPIRE_S
        with _lock:
            expired = [k for k, v in _rooms.items() if v["last_active"] < cutoff]
            for k in expired:
                del _rooms[k]
            if expired and _log:
                _log(f"[IPLink] Expired {len(expired)} idle room(s)")


# ─── Templates ────────────────────────────────────────────────────────────────

_HUB_TPL = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>IP Link — SignalScope</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<style nonce="{{csp_nonce()}}">
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:radial-gradient(circle at top,#12376f 0%,var(--bg) 38%,#05101f 100%);color:var(--tx);font-size:13px}
header{background:linear-gradient(180deg,rgba(10,31,65,.96),rgba(9,24,48,.96));border-bottom:1px solid var(--bor);padding:12px 20px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.btn{display:inline-flex;align-items:center;border:none;border-radius:8px;padding:5px 12px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;text-decoration:none;white-space:nowrap}
.btn:hover{filter:brightness(1.15)}
.bp{background:var(--acc);color:#fff}.bd{background:var(--al);color:#fff}.bg{background:var(--bor);color:var(--tx)}
.bs{padding:3px 9px;font-size:12px}.nav-active{background:var(--acc)!important;color:#fff!important}
main{padding:20px;max-width:1100px;margin:0 auto}
.card{background:var(--sur);border:1px solid var(--bor);border-radius:12px;overflow:hidden;margin-bottom:16px}
.ch{padding:9px 14px;display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--bor);background:linear-gradient(180deg,#143766,#102b54);font-size:12px;font-weight:700;color:var(--acc);text-transform:uppercase;letter-spacing:.06em}
.cb{padding:14px}
.field{display:flex;flex-direction:column;gap:4px;margin-bottom:12px}
label{font-size:11px;color:var(--mu);font-weight:600;text-transform:uppercase;letter-spacing:.05em}
input,select{background:#0d1e40;border:1px solid var(--bor);border-radius:6px;color:var(--tx);padding:6px 9px;font-size:13px;font-family:inherit;width:100%}
input:focus,select:focus{border-color:var(--acc);outline:none}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;background:#1e3a5f;color:var(--acc)}
.b-ok{background:#0f2318;color:var(--ok);border:1px solid #166534}
.b-wn{background:#2a1e06;color:var(--wn);border:1px solid #92400e}
.b-al{background:#2a0a0a;color:var(--al);border:1px solid #991b1b}
.b-mu{background:#1a2040;color:var(--mu)}
/* Room cards */
.room-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px;margin-top:16px}
.room-card{background:var(--sur);border:1px solid var(--bor);border-radius:12px;overflow:hidden}
.room-card.rc-connected{border-color:var(--ok)}
.room-card.rc-offer{border-color:var(--wn);animation:pulse-border 1.5s infinite}
@keyframes pulse-border{0%,100%{border-color:var(--wn)}50%{border-color:#fbbf24}}
.rc-hdr{padding:10px 14px;background:linear-gradient(180deg,#143766,#102b54);display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--bor)}
.rc-name{font-weight:700;font-size:14px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rc-body{padding:12px 14px}
.rc-row{display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-bottom:1px solid rgba(23,52,95,.4);font-size:12px}
.rc-row:last-child{border-bottom:none}
.rc-lbl{color:var(--mu)}
/* Level bars */
.lvl-wrap{display:flex;align-items:center;gap:8px;margin:8px 0}
.lvl-label{font-size:11px;color:var(--mu);width:36px;text-align:right;flex-shrink:0}
.lvl-outer{flex:1;height:8px;background:#0a1628;border-radius:4px;overflow:hidden}
.lvl-fill{height:8px;border-radius:4px;transition:width .15s;background:var(--ok)}
.lvl-val{font-size:11px;width:36px;text-align:left;flex-shrink:0;font-variant-numeric:tabular-nums}
/* Accept call overlay */
.accept-banner{background:linear-gradient(135deg,#2a1e06,#3a2a06);border:1px solid var(--wn);border-radius:8px;padding:10px 14px;margin-bottom:12px;display:flex;align-items:center;gap:10px}
.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.copy-flash{color:var(--ok);font-size:11px;opacity:0;transition:opacity .3s}
.copy-flash.show{opacity:1}
.msg{padding:8px 12px;border-radius:6px;font-size:12px;margin-bottom:10px}
.msg-ok{background:#0f2318;color:var(--ok);border:1px solid #166534}
.msg-err{background:#2a0a0a;color:var(--al);border:1px solid #991b1b}
/* SIP */
.sip-pill{display:inline-flex;align-items:center;gap:5px;padding:3px 10px;border-radius:999px;font-size:11px;font-weight:600;transition:background .3s}
.sip-off{background:#1a2040;color:var(--mu)}
.sip-conn{background:#0f2318;color:var(--ok);border:1px solid #166534}
.sip-ring{background:#2a1e06;color:var(--wn);border:1px solid #92400e;animation:pulse-border 1s infinite}
.sip-incall{background:#0f2318;color:var(--ok);border:1px solid #166534}
.sip-err{background:#2a0a0a;color:var(--al);border:1px solid #991b1b}
.sip-sdot{width:7px;height:7px;border-radius:50%;flex-shrink:0;background:currentColor}
.sip-incoming-banner{background:linear-gradient(135deg,#2a1e06,#3a2a06);border:1px solid var(--wn);border-radius:8px;padding:12px 16px;margin-bottom:14px;display:none;flex-direction:row;align-items:center;gap:12px;animation:pulse-border 1.5s infinite}
.sip-call-card{background:var(--sur);border:2px solid var(--ok);border-radius:12px;overflow:hidden;margin-bottom:16px;display:none}
details>summary{cursor:pointer;font-size:12px;color:var(--mu);font-weight:600;text-transform:uppercase;letter-spacing:.05em;padding:4px 0;list-style:none;display:flex;align-items:center;gap:6px}
details>summary::before{content:'▶';font-size:10px;transition:transform .2s}
details[open]>summary::before{transform:rotate(90deg)}
</style></head><body>
{{ topnav("iplink") | safe }}
<main>
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:10px">
    <div>
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
        <h1 style="font-size:20px;font-weight:700">🎙 IP Link</h1>
        <span class="sip-pill sip-off" id="sipStatusPill"><span class="sip-sdot"></span><span id="sipStatusTxt">SIP: Off</span></span>
      </div>
      <p style="font-size:12px;color:var(--mu);margin-top:4px">Browser-based contribution codec — share a link or take SIP calls from any device</p>
    </div>
    <button class="btn bp" id="createBtn">＋ New Room</button>
  </div>

  <!-- Create room panel -->
  <div id="createPanel" class="card" style="display:none;margin-bottom:16px">
    <div class="ch">📞 Create new room</div>
    <div class="cb">
      <div id="createMsg"></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
        <div class="field">
          <label>Room name</label>
          <input id="cName" placeholder="e.g. Studio A Guest Line" maxlength="50">
        </div>
        <div class="field">
          <label>Audio quality</label>
          <select id="cQuality">
            <option value="voice">Voice (64 kbps mono)</option>
            <option value="broadcast" selected>Broadcast (128 kbps stereo)</option>
            <option value="hifi">Hi-Fi (256 kbps stereo)</option>
          </select>
        </div>
      </div>
      <div style="display:flex;gap:8px">
        <button class="btn bp" id="createSubmit">Create Room</button>
        <button class="btn bg" id="createCancel">Cancel</button>
      </div>
    </div>
  </div>

  <!-- SIP: incoming call banner -->
  <div class="sip-incoming-banner" id="sipIncomingBanner">
    <span style="font-size:22px">📞</span>
    <div style="flex:1">
      <div style="font-weight:700;font-size:14px">Incoming SIP Call</div>
      <div style="font-size:12px;color:var(--wn)" id="sipCallerName">Unknown caller</div>
    </div>
    <button class="btn bp bs" id="sipAnswerBtn">✅ Answer</button>
    <button class="btn bd bs" id="sipRejectBtn">✗ Decline</button>
  </div>

  <!-- SIP: active call card -->
  <div class="sip-call-card" id="sipCallCard">
    <div class="rc-hdr">
      <span style="font-size:18px">☎️</span>
      <span class="rc-name" id="sipCallRemote">SIP Call</span>
      <span class="badge b-ok">🔴 Live</span>
    </div>
    <div class="rc-body">
      <div class="lvl-wrap">
        <span class="lvl-label" style="font-size:10px">Remote</span>
        <div class="lvl-outer"><div class="lvl-fill" id="sipRemoteLvl" style="width:0%"></div></div>
        <span class="lvl-val" id="sipRemoteLvlVal">—</span>
      </div>
      <div class="lvl-wrap">
        <span class="lvl-label" style="font-size:10px">Mic</span>
        <div class="lvl-outer"><div class="lvl-fill" id="sipMicLvl" style="width:0%;background:var(--acc)"></div></div>
        <span class="lvl-val" id="sipMicLvlVal">—</span>
      </div>
      <div class="rc-row"><span class="rc-lbl">Duration</span><span id="sipCallDur">—</span></div>
      <div class="rc-row" id="sipRttRow" style="display:none"><span class="rc-lbl">RTT</span><span id="sipRttVal">—</span></div>
      <div class="actions">
        <button class="btn bg bs" id="sipMuteBtn">🎤 Mic ON</button>
        <button class="btn bd bs" id="sipHangupBtn">✗ Hang Up</button>
      </div>
    </div>
  </div>

  <!-- Room grid (rendered by JS) -->
  <div id="roomGrid"></div>
  <p id="noRooms" style="color:var(--mu);font-size:13px;display:none">No rooms yet — create one to generate a shareable link for your contributor.</p>

  <!-- SIP section: dial + settings -->
  <div class="card" style="margin-top:16px">
    <div class="ch">☎ SIP Calls</div>
    <div class="cb">
      <div style="display:flex;gap:8px;margin-bottom:10px">
        <input id="sipDialInput" placeholder="Extension, number, or sip:user@domain" style="flex:1">
        <button class="btn bp" id="sipDialBtn">📞 Call</button>
      </div>
      <div id="sipCallErrMsg" style="display:none;color:var(--al);font-size:12px;margin-bottom:8px;padding:6px 10px;background:#2a0a0a;border-radius:6px;border:1px solid #991b1b"></div>
      <details id="sipSettingsDetails">
        <summary>SIP Account Settings</summary>
        <div style="margin-top:14px">
          <div id="sipSaveMsg" style="margin-bottom:10px"></div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
            <div class="field" style="grid-column:1/-1">
              <label>WebSocket Server URL</label>
              <input id="sipServer" placeholder="wss://pbx.example.com:8089/ws" autocomplete="off" spellcheck="false">
            </div>
            <div class="field">
              <label>SIP Username</label>
              <input id="sipUser" autocomplete="off" spellcheck="false">
            </div>
            <div class="field">
              <label>Password</label>
              <input id="sipPass" type="password" autocomplete="new-password">
            </div>
            <div class="field">
              <label>SIP Domain / Realm</label>
              <input id="sipDomain" placeholder="pbx.example.com" autocomplete="off" spellcheck="false">
            </div>
            <div class="field">
              <label>Display Name</label>
              <input id="sipDisplayName" value="Studio">
            </div>
          </div>
          <div style="margin-top:4px;margin-bottom:14px">
            <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--tx);cursor:pointer">
              <input type="checkbox" id="sipEnabled"> Auto-connect when page loads
            </label>
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <button class="btn bp" id="sipSaveBtn">💾 Save &amp; Connect</button>
            <button class="btn bg" id="sipDisconnectBtn">Disconnect</button>
          </div>
        </div>
      </details>
    </div>
  </div>

  <!-- Hidden audio elements -->
  <audio id="hubAudio" autoplay playsinline style="display:none"></audio>
  <audio id="sipAudio" autoplay playsinline style="display:none"></audio>
</main>

<script nonce="{{csp_nonce()}}">
var _csrf = (document.querySelector('meta[name="csrf-token"]')||{}).content || '';
function csrfHdr(){ return {'X-CSRFToken': (document.querySelector('meta[name="csrf-token"]')||{}).content||'','Content-Type':'application/json'}; }

// ─── Per-room WebRTC state ───────────────────────────────────────────────────
var _pcs = {};      // room_id → RTCPeerConnection (or true as placeholder)
var _streams = {};  // room_id → local MediaStream (mic)
var _iceIdx = {};   // room_id → last hub_ice index sent / talent_ice index consumed
var _levels = {};   // room_id → AudioContext analyser
var _connErrs = {}; // room_id → error string (shown in card, auto-cleared after 10 s)
var _micStream = null; // shared mic stream for hub side

var STUN = {{stun|tojson}};
var BASE = window.location.origin;

// ─── Mic acquisition (shared across all rooms) ───────────────────────────────
function _getHubMic(cb){
  if(_micStream){ cb(null, _micStream); return; }
  navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true},video:false})
    .then(function(s){ _micStream=s; cb(null,s); })
    .catch(function(e){ cb(e,null); });
}

// ─── SDP munging ─────────────────────────────────────────────────────────────
// (No browser detection needed — SDP munge is applied unconditionally.)

// SDP compatibility shim — applied unconditionally to ALL incoming offers.
// Both Chrome M130+ and Safari reject certain lines in raw WebRTC offers:
//
//  - a=ssrc / a=ssrc-group       Chrome M130+ rejects the deprecated two-ID
//                                "msid:STREAM TRACK" format; Safari rejects all ssrc
//  - a=extmap-allow-mixed        Safari WebKit rejects and MISREPORTS as later lines
//  - a=extmap:N/direction        direction specifiers; Safari rejects
//  - a=rtcp-rsize                Safari rejects
//  - a=rtcp-fb: transport-cc     Safari rejects; optional — call works without it
//  - telephone-event / CN / RED  not needed for contribution
//  - PCMA / PCMU / static PTs    explicit rtpmap confuses parsers
//  - rtx / ulpfec / flexfec      not needed
// Orphan guard: fmtp without a matching rtpmap stripped (would cause parse error).
// ─── SDP normalisation ───────────────────────────────────────────────────────
// Room calls: both ends are WebRTC browsers — the raw offer is passed unchanged
//   to setRemoteDescription().  The server ensures the SDP ends with \r\n so
//   Chrome's line-oriented parser doesn't fail on an unterminated last line.
//   No JS manipulation; any rewriting risks making m= / a=fmtp PT references
//   inconsistent.
//
// SIP calls: the hub's own Chrome offer contains WebRTC-specific extension lines
//   that many SIP servers reject.  _sipMungeSdp strips only those attribute
//   lines — no m= rewriting, no codec removal.
function _sipMungeSdp(sdp){
  var DROP = [
    /^a=ssrc(-group)?:/,
    /^a=extmap-allow-mixed\s*$/,
    /^a=rtcp-rsize\s*$/,
    /^a=rtcp-fb:/,
  ];
  var out = sdp.split(/\r?\n/).filter(function(line){
    return !DROP.some(function(p){ return p.test(line); });
  }).map(function(line){
    // Normalise a=extmap:N/direction URI → a=extmap:N URI
    return line.replace(/^(a=extmap:\d+)\/(?:sendrecv|sendonly|recvonly|inactive)\b/, '$1');
  }).join('\r\n');
  // Ensure SDP ends with \r\n — required by RFC 4566; without it the SIP
  // server's Content-Length parsing may misread the body boundary.
  if(!out.endsWith('\r\n')) out += '\r\n';
  return out;
}

// ─── Hub WebRTC negotiation ──────────────────────────────────────────────────
function _showConnErr(roomId, msg){
  _connErrs[roomId] = msg;
  _refreshRooms();
  setTimeout(function(){ delete _connErrs[roomId]; _refreshRooms(); }, 10000);
}

function acceptCall(roomId){
  if(_pcs[roomId]) return;   // already connecting, ignore double-click
  // Mark as connecting immediately — _renderRooms checks this every 1.5 s
  // and will show "Connecting…" rather than re-rendering the Accept button.
  _pcs[roomId] = true;
  var btn = document.getElementById('accept_'+roomId);
  if(btn){ btn.disabled=true; btn.textContent='⏳ Connecting…'; }

  _getHubMic(function(err, micStream){
    if(err){
      alert('Microphone access denied: '+err.message+'\nYou can still receive audio but cannot send IFB/talkback.');
    }
    // Fetch the talent's offer
    fetch('/api/iplink/room/'+roomId+'/offer', {credentials:'same-origin'})
      .then(function(r){ return r.json(); })
      .then(function(d){
        if(!d.offer){ delete _pcs[roomId]; _showConnErr(roomId,'No offer found — talent may have disconnected. Reset the room and ask them to reconnect.'); return; }
        var pc = new RTCPeerConnection({iceServers: STUN.map(function(u){return{urls:u};})});
        _pcs[roomId] = pc;  // replace truthy placeholder with actual PC
        _iceIdx[roomId] = {sent:0, consumed:0};

        // Add mic track (IFB return to talent)
        if(micStream){
          micStream.getTracks().forEach(function(t){ pc.addTrack(t, micStream); });
        }

        // Receive talent audio
        pc.ontrack = function(e){
          var audio = document.getElementById('hubAudio');
          if(audio && e.streams[0]){
            audio.srcObject = e.streams[0];
            _setupHubMeter(roomId, e.streams[0]);
          }
        };

        // Collect ICE candidates
        pc.onicecandidate = function(e){
          if(!e.candidate) return;
          fetch('/api/iplink/room/'+roomId+'/ice', {
            method:'POST', credentials:'same-origin',
            headers:csrfHdr(),
            body:JSON.stringify({from:'hub', candidate:e.candidate})
          });
        };

        pc.onconnectionstatechange = function(){
          _refreshRooms();
          if(pc.connectionState === 'failed'){
            delete _pcs[roomId];
            fetch('/api/iplink/room/'+roomId+'/status',{method:'POST',credentials:'same-origin',headers:csrfHdr(),body:JSON.stringify({from:'hub',status:'disconnected'})});
          } else if(pc.connectionState === 'disconnected'){
            // Transient — don't tear down yet; ICE may recover
            fetch('/api/iplink/room/'+roomId+'/status',{method:'POST',credentials:'same-origin',headers:csrfHdr(),body:JSON.stringify({from:'hub',status:'disconnected'})});
          }
        };

        // Pass the talent's raw offer directly to setRemoteDescription.
        // Both ends are WebRTC browsers; they negotiate codecs natively.
        // The server guarantees the SDP ends with \r\n so Chrome's parser
        // doesn't fail on an unterminated last line.
        pc.setRemoteDescription({type:'offer', sdp:d.offer})
          .then(function(){ return pc.createAnswer(); })
          .then(function(ans){
            return pc.setLocalDescription(ans).then(function(){ return ans; });
          })
          .then(function(ans){
            return fetch('/api/iplink/room/'+roomId+'/answer',{
              method:'POST', credentials:'same-origin',
              headers:csrfHdr(),
              body:JSON.stringify({answer:ans.sdp})
            });
          })
          .then(function(){
            // Start polling for talent ICE candidates
            _pollTalentIce(roomId);
            // Apply quality settings
            _applyQuality(pc, roomId);
          })
          .catch(function(e){
            console.error('[IPLink] setRemoteDescription error:', e.message);
            console.debug('[IPLink] raw offer SDP:\n', d.offer);
            delete _pcs[roomId];
            _showConnErr(roomId, 'WebRTC failed: '+(e.message||String(e)));
          });
      })
      .catch(function(e){
        // Outer catch: fetch/JSON errors on the offer request
        console.error('Hub offer fetch error:', e);
        delete _pcs[roomId];
        _showConnErr(roomId, 'Could not fetch offer: '+(e.message||String(e)));
      });
  });
}

function _applyQuality(pc, roomId){
  // Set Opus bitrate via RTCRtpSender.setParameters
  fetch('/api/iplink/rooms',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      var room = (d.rooms||[]).find(function(r){return r.id===roomId;});
      if(!room) return;
      var q = {voice:{maxBitrate:64000},broadcast:{maxBitrate:128000},hifi:{maxBitrate:256000}}[room.quality]||{maxBitrate:128000};
      pc.getSenders().forEach(function(s){
        if(s.track && s.track.kind==='audio'){
          var p = s.getParameters();
          if(!p.encodings) p.encodings=[{}];
          p.encodings[0].maxBitrate = q.maxBitrate;
          s.setParameters(p).catch(function(){});
        }
      });
    });
}

function _pollTalentIce(roomId){
  var idx = (_iceIdx[roomId]||{}).consumed || 0;
  fetch('/api/iplink/room/'+roomId+'/ice?from=talent&from_idx='+idx, {credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      var pc = _pcs[roomId];
      if(!pc || pc.connectionState==='closed') return;
      (d.candidates||[]).forEach(function(c){
        pc.addIceCandidate(c).catch(function(){});
      });
      if(_iceIdx[roomId]) _iceIdx[roomId].consumed = d.next_idx || idx;
      // Keep polling while connecting
      if(pc.connectionState !== 'connected' && pc.connectionState !== 'closed'){
        setTimeout(function(){ _pollTalentIce(roomId); }, 600);
      }
    })
    .catch(function(){ setTimeout(function(){ _pollTalentIce(roomId); }, 2000); });
}

// ─── Level meters ────────────────────────────────────────────────────────────
function _setupHubMeter(roomId, stream){
  try{
    var ctx = new(window.AudioContext||window.webkitAudioContext)();
    var src = ctx.createMediaStreamSource(stream);
    var an  = ctx.createAnalyser();
    an.fftSize = 512;
    src.connect(an);
    _levels[roomId] = an;
    var buf = new Uint8Array(an.frequencyBinCount);
    function tick(){
      an.getByteTimeDomainData(buf);
      var sq=0; for(var i=0;i<buf.length;i++){var s=(buf[i]-128)/128;sq+=s*s;}
      var rms = Math.sqrt(sq/buf.length);
      var el = document.getElementById('rc_tlvl_'+roomId);
      if(el){ el.style.width=(Math.min(rms*4,1)*100)+'%'; }
      requestAnimationFrame(tick);
    }
    tick();
  } catch(e){}
}

// ─── Mute toggle ─────────────────────────────────────────────────────────────
function toggleMute(roomId){
  fetch('/api/iplink/room/'+roomId+'/mute',{method:'POST',credentials:'same-origin',headers:csrfHdr()});
  if(_micStream){
    _micStream.getAudioTracks().forEach(function(t){t.enabled=!t.enabled;});
  }
}

// ─── Disconnect ──────────────────────────────────────────────────────────────
function disconnectRoom(roomId){
  var pc=_pcs[roomId];
  if(pc){ pc.close(); delete _pcs[roomId]; }
  fetch('/api/iplink/room/'+roomId+'/status',{method:'POST',credentials:'same-origin',headers:csrfHdr(),body:JSON.stringify({from:'hub',status:'disconnected'})});
}

function deleteRoom(roomId){
  if(!confirm('Delete this room and its shareable link?')) return;
  disconnectRoom(roomId);
  fetch('/api/iplink/room/'+roomId,{method:'DELETE',credentials:'same-origin',headers:csrfHdr()})
    .then(function(){ _refreshRooms(); });
}

function resetRoom(roomId){
  disconnectRoom(roomId);
  fetch('/api/iplink/room/'+roomId+'/reset',{method:'POST',credentials:'same-origin',headers:csrfHdr()})
    .then(function(){ _refreshRooms(); });
}

// ─── Copy link ───────────────────────────────────────────────────────────────
function copyLink(roomId, flashId){
  var url = BASE+'/iplink/join/'+roomId;
  navigator.clipboard.writeText(url).then(function(){
    var el=document.getElementById(flashId);
    if(el){el.classList.add('show');setTimeout(function(){el.classList.remove('show');},2000);}
  }).catch(function(){prompt('Copy this link:',url);});
}

// ─── Room list rendering ─────────────────────────────────────────────────────
function _statusBadge(status){
  var map={
    waiting:    '<span class="badge b-mu">⏳ Waiting for caller</span>',
    offer_received: '<span class="badge b-wn">📲 Incoming call…</span>',
    connected:  '<span class="badge b-ok">🔴 Live</span>',
    disconnected:'<span class="badge b-al">✗ Disconnected</span>',
  };
  return map[status]||('<span class="badge b-mu">'+status+'</span>');
}

function _fmt(s){
  if(!s) return '—';
  var m=Math.floor(s/60), sec=s%60;
  return (m>0?m+'m ':'')+sec+'s';
}

function _renderRooms(rooms){
  var ng=document.getElementById('roomGrid');
  var np=document.getElementById('noRooms');
  if(!rooms.length){ ng.innerHTML=''; np.style.display=''; return; }
  np.style.display='none';
  var html='<div class="room-grid">';
  rooms.forEach(function(r){
    var cls='room-card';
    if(r.status==='connected') cls+=' rc-connected';
    else if(r.status==='offer_received') cls+=' rc-offer';
    var tlvlW = Math.round(r.talent_level*100);
    var hlvlW  = Math.round(r.hub_level*100);
    var talentLvlCol = r.talent_level>0.7?'var(--al)':r.talent_level>0.3?'var(--ok)':'var(--ok)';
    html+='<div class="'+cls+'" id="rc_'+r.id+'">';
    // Header
    html+='<div class="rc-hdr">';
    html+='<span style="font-size:18px">🎙</span>';
    html+='<span class="rc-name" title="'+r.name+'">'+_esc(r.name)+'</span>';
    html+=_statusBadge(r.status);
    html+='</div>';
    // Body
    html+='<div class="rc-body">';
    // Accept banner — show Connecting… if a PeerConnection already exists for this room
    if(r.status==='offer_received'){
      html+='<div class="accept-banner">📲 <span style="flex:1">Incoming call from contributor</span>';
      if(_pcs[r.id]){
        html+='<button class="btn bp bs" disabled>⏳ Connecting…</button>';
      } else {
        html+='<button class="btn bp bs" id="accept_'+r.id+'" onclick="acceptCall(\''+r.id+'\')">✅ Accept</button>';
      }
      html+='</div>';
    }
    if(_connErrs[r.id]){
      html+='<div class="msg msg-err" style="margin:8px 0 0">⚠ '+_esc(_connErrs[r.id])+'</div>';
    }
    // Level meters
    html+='<div class="lvl-wrap"><span class="lvl-label" style="font-size:10px">Talent</span>';
    html+='<div class="lvl-outer"><div class="lvl-fill" id="rc_tlvl_'+r.id+'" style="width:'+tlvlW+'%;background:'+talentLvlCol+'"></div></div>';
    html+='<span class="lvl-val" style="color:'+talentLvlCol+'">'+(r.talent_level>0?Math.round(r.talent_level*100)+'%':'—')+'</span></div>';
    html+='<div class="lvl-wrap"><span class="lvl-label" style="font-size:10px">Hub</span>';
    html+='<div class="lvl-outer"><div class="lvl-fill" id="rc_hlvl_'+r.id+'" style="width:'+hlvlW+'%;background:'+(r.hub_muted?'var(--mu)':'var(--acc)')+'"></div></div>';
    html+='<span class="lvl-val" style="color:var(--mu)">'+(r.hub_muted?'🔇':'')+(r.hub_level>0?Math.round(r.hub_level*100)+'%':'—')+'</span></div>';
    // Stats rows
    html+='<div class="rc-row"><span class="rc-lbl">Quality</span><span>'+_esc(r.quality_label)+'</span></div>';
    if(r.status==='connected' && r.duration_s!=null){
      html+='<div class="rc-row"><span class="rc-lbl">Duration</span><span>'+_fmt(r.duration_s)+'</span></div>';
    }
    if(r.talent_ip){ html+='<div class="rc-row"><span class="rc-lbl">Caller IP</span><span style="font-size:11px">'+_esc(r.talent_ip)+'</span></div>'; }
    if(r.stats && r.stats.rtt_ms!=null){
      html+='<div class="rc-row"><span class="rc-lbl">RTT</span><span style="color:'+(r.stats.rtt_ms>100?'var(--wn)':'var(--ok)')+'">'+r.stats.rtt_ms+' ms</span></div>';
    }
    if(r.stats && r.stats.loss_pct!=null){
      html+='<div class="rc-row"><span class="rc-lbl">Packet loss</span><span style="color:'+(r.stats.loss_pct>1?'var(--al)':r.stats.loss_pct>0?'var(--wn)':'var(--ok)')+'">'+r.stats.loss_pct+'%</span></div>';
    }
    // Actions
    html+='<div class="actions">';
    html+='<button class="btn bg bs" onclick="copyLink(\''+r.id+'\',\'flash_'+r.id+'\')">🔗 Copy Link</button>';
    html+='<span id="flash_'+r.id+'" class="copy-flash">Copied!</span>';
    if(r.status==='connected'){
      html+='<button class="btn bg bs" onclick="toggleMute(\''+r.id+'\')">'+( r.hub_muted?'🔊 Unmute IFB':'🔇 Mute IFB')+'</button>';
      html+='<button class="btn bd bs" onclick="disconnectRoom(\''+r.id+'\')">Disconnect</button>';
    } else if(r.status==='disconnected'||r.status==='offer_received'){
      html+='<button class="btn bg bs" onclick="resetRoom(\''+r.id+'\')">↻ Reset</button>';
    }
    html+='<button class="btn bd bs" onclick="deleteRoom(\''+r.id+'\')" style="margin-left:auto">🗑 Delete</button>';
    html+='</div>';
    html+='</div></div>'; // rc-body, room-card
  });
  html+='</div>';
  ng.innerHTML=html;
}

function _esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

// ─── Room list polling ────────────────────────────────────────────────────────
function _refreshRooms(){
  fetch('/api/iplink/rooms',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){ _renderRooms(d.rooms||[]); })
    .catch(function(){});
}
_refreshRooms();
setInterval(_refreshRooms, 1500);

// ─── Hub-side level reporting ─────────────────────────────────────────────────
setInterval(function(){
  Object.keys(_pcs).forEach(function(roomId){
    var an=_levels[roomId];
    if(!an) return;
    var buf=new Uint8Array(an.frequencyBinCount);
    an.getByteTimeDomainData(buf);
    var sq=0; for(var i=0;i<buf.length;i++){var s=(buf[i]-128)/128;sq+=s*s;}
    var rms=Math.sqrt(sq/buf.length);
    fetch('/api/iplink/room/'+roomId+'/level',{method:'POST',credentials:'same-origin',headers:csrfHdr(),body:JSON.stringify({from:'hub',level:rms})});
  });
}, 300);

// ─── Create room form ─────────────────────────────────────────────────────────
document.getElementById('createBtn').addEventListener('click',function(){
  document.getElementById('createPanel').style.display='';
  document.getElementById('cName').focus();
});
document.getElementById('createCancel').addEventListener('click',function(){
  document.getElementById('createPanel').style.display='none';
  document.getElementById('createMsg').innerHTML='';
});
document.getElementById('createSubmit').addEventListener('click',function(){
  var name=(document.getElementById('cName').value||'').trim();
  var quality=document.getElementById('cQuality').value;
  if(!name){ document.getElementById('createMsg').innerHTML='<div class="msg msg-err">Enter a room name</div>'; return; }
  fetch('/api/iplink/rooms',{method:'POST',credentials:'same-origin',
    headers:csrfHdr(), body:JSON.stringify({name:name,quality:quality})})
    .then(function(r){return r.json();})
    .then(function(d){
      if(d.error){document.getElementById('createMsg').innerHTML='<div class="msg msg-err">'+_esc(d.error)+'</div>'; return;}
      document.getElementById('createPanel').style.display='none';
      document.getElementById('cName').value='';
      document.getElementById('createMsg').innerHTML='';
      _refreshRooms();
      // Auto-copy link
      navigator.clipboard.writeText(BASE+'/iplink/join/'+d.room.id).catch(function(){});
    })
    .catch(function(e){ document.getElementById('createMsg').innerHTML='<div class="msg msg-err">'+e+'</div>'; });
});
document.getElementById('cName').addEventListener('keydown',function(e){if(e.key==='Enter')document.getElementById('createSubmit').click();});

// ════════════════════════════════════════════════════════════════════════════
// SIP CLIENT  (pure JS over WebSocket, no dependencies)
// ════════════════════════════════════════════════════════════════════════════

// ── Compact MD5 (RFC 1321) — required for SIP digest auth ──────────────────
function _md5(s){
  function ad(x,y){var l=(x&0xffff)+(y&0xffff);var m=(x>>16)+(y>>16)+(l>>16);return(m<<16)|(l&0xffff)}
  function rl(n,c){return(n<<c)|(n>>>(32-c))}
  function cm(q,a,b,x,s,t){return ad(rl(ad(ad(a,q),ad(x,t)),s),b)}
  function ff(a,b,c,d,x,s,t){return cm((b&c)|(~b&d),a,b,x,s,t)}
  function gg(a,b,c,d,x,s,t){return cm((b&d)|(c&~d),a,b,x,s,t)}
  function hh(a,b,c,d,x,s,t){return cm(b^c^d,a,b,x,s,t)}
  function ii(a,b,c,d,x,s,t){return cm(c^(b|~d),a,b,x,s,t)}
  function sb(x){var i,n=((x.length+8)>>6)+1,b=new Array(n*16).fill(0);for(i=0;i<x.length;i++)b[i>>2]|=x.charCodeAt(i)<<((i%4)*8);b[x.length>>2]|=0x80<<((x.length%4)*8);b[n*16-2]=x.length*8;return b}
  function rh(n){var hc='0123456789abcdef',j,s='';for(j=0;j<=3;j++)s+=hc[(n>>(j*8+4))&0xf]+hc[(n>>(j*8))&0xf];return s}
  var i,x=sb(s),a=1732584193,b=-271733879,c=-1732584194,d=271733878,oa,ob,oc,od;
  for(i=0;i<x.length;i+=16){oa=a;ob=b;oc=c;od=d;
    a=ff(a,b,c,d,x[i],7,-680876936);d=ff(d,a,b,c,x[i+1],12,-389564586);c=ff(c,d,a,b,x[i+2],17,606105819);b=ff(b,c,d,a,x[i+3],22,-1044525330);
    a=ff(a,b,c,d,x[i+4],7,-176418897);d=ff(d,a,b,c,x[i+5],12,1200080426);c=ff(c,d,a,b,x[i+6],17,-1473231341);b=ff(b,c,d,a,x[i+7],22,-45705983);
    a=ff(a,b,c,d,x[i+8],7,1770035416);d=ff(d,a,b,c,x[i+9],12,-1958414417);c=ff(c,d,a,b,x[i+10],17,-42063);b=ff(b,c,d,a,x[i+11],22,-1990404162);
    a=ff(a,b,c,d,x[i+12],7,1804603682);d=ff(d,a,b,c,x[i+13],12,-40341101);c=ff(c,d,a,b,x[i+14],17,-1502002290);b=ff(b,c,d,a,x[i+15],22,1236535329);
    a=gg(a,b,c,d,x[i+1],5,-165796510);d=gg(d,a,b,c,x[i+6],9,-1069501632);c=gg(c,d,a,b,x[i+11],14,643717713);b=gg(b,c,d,a,x[i],20,-373897302);
    a=gg(a,b,c,d,x[i+5],5,-701558691);d=gg(d,a,b,c,x[i+10],9,38016083);c=gg(c,d,a,b,x[i+15],14,-660478335);b=gg(b,c,d,a,x[i+4],20,-405537848);
    a=gg(a,b,c,d,x[i+9],5,568446438);d=gg(d,a,b,c,x[i+14],9,-1019803690);c=gg(c,d,a,b,x[i+3],14,-187363961);b=gg(b,c,d,a,x[i+8],20,1163531501);
    a=gg(a,b,c,d,x[i+13],5,-1444681467);d=gg(d,a,b,c,x[i+2],9,-51403784);c=gg(c,d,a,b,x[i+7],14,1735328473);b=gg(b,c,d,a,x[i+12],20,-1926607734);
    a=hh(a,b,c,d,x[i+5],4,-378558);d=hh(d,a,b,c,x[i+8],11,-2022574463);c=hh(c,d,a,b,x[i+11],16,1839030562);b=hh(b,c,d,a,x[i+14],23,-35309556);
    a=hh(a,b,c,d,x[i+1],4,-1530992060);d=hh(d,a,b,c,x[i+4],11,1272893353);c=hh(c,d,a,b,x[i+7],16,-155497632);b=hh(b,c,d,a,x[i+10],23,-1094730640);
    a=hh(a,b,c,d,x[i+13],4,681279174);d=hh(d,a,b,c,x[i],11,-358537222);c=hh(c,d,a,b,x[i+3],16,-722521979);b=hh(b,c,d,a,x[i+6],23,76029189);
    a=hh(a,b,c,d,x[i+9],4,-640364487);d=hh(d,a,b,c,x[i+12],11,-421815835);c=hh(c,d,a,b,x[i+15],16,530742520);b=hh(b,c,d,a,x[i+2],23,-995338651);
    a=ii(a,b,c,d,x[i],6,-198630844);d=ii(d,a,b,c,x[i+7],10,1126891415);c=ii(c,d,a,b,x[i+14],15,-1416354905);b=ii(b,c,d,a,x[i+5],21,-57434055);
    a=ii(a,b,c,d,x[i+12],6,1700485571);d=ii(d,a,b,c,x[i+3],10,-1894986606);c=ii(c,d,a,b,x[i+10],15,-1051523);b=ii(b,c,d,a,x[i+1],21,-2054922799);
    a=ii(a,b,c,d,x[i+8],6,1873313359);d=ii(d,a,b,c,x[i+15],10,-30611744);c=ii(c,d,a,b,x[i+6],15,-1560198380);b=ii(b,c,d,a,x[i+13],21,1309151649);
    a=ii(a,b,c,d,x[i+4],6,-145523070);d=ii(d,a,b,c,x[i+11],10,-1120210379);c=ii(c,d,a,b,x[i+2],15,718787259);b=ii(b,c,d,a,x[i+9],21,-343485551);
    a=ad(a,oa);b=ad(b,ob);c=ad(c,oc);d=ad(d,od);
  }
  return rh(a)+rh(b)+rh(c)+rh(d);
}

// ── SIP state ──────────────────────────────────────────────────────────────
var _sip = {
  ws:null, state:'idle', cfg:null,
  regCsq:0, callCsq:0,
  regCid:null, regFromTag:null,
  callCid:null, callFromTag:null, callToTag:null, callUri:null,
  myToTag:null,          // our tag in incoming call responses
  inInvite:null,         // pending incoming INVITE message
  pc:null,               // RTCPeerConnection for SIP call
  micStream:null, remoteAnalyser:null, micAnalyser:null,
  callStart:null, micMuted:false,
  regTimer:null, retryTimer:null, regTimeoutTimer:null, durTimer:null, regAuthAttempts:0,
  realm:null,    // learned from server's 401 WWW-Authenticate realm= during REGISTER
};

// ── Utilities ──────────────────────────────────────────────────────────────
function _sipRand(n){var c='abcdef0123456789',s='';for(var i=0;i<n;i++)s+=c[Math.floor(Math.random()*c.length)];return s;}
function _sipBranch(){return 'z9hG4bK'+_sipRand(12);}
function _sipTag(){return _sipRand(10);}
function _sipCid(host){return _sipRand(14)+'@'+(host||'ss');}

function _sipWsHostname(){try{return new URL(_sip.cfg.server).hostname;}catch(e){return 'ss';}}
function _sipWsHost(){try{var u=new URL(_sip.cfg.server);return u.hostname+(u.port?':'+u.port:'');}catch(e){return 'ss';}}
// Domain priority: explicit config → realm from server's 401 challenge → WS hostname.
// The realm is the correct SIP domain for all URIs (REGISTER, INVITE, From, To).
// Using the WS hostname alone causes 484 when the server's SIP realm differs.
function _sipDomain(){return (_sip.cfg.domain||'').trim()||_sip.realm||_sipWsHostname();}
function _sipSelfUri(){return 'sip:'+_sip.cfg.username+'@'+_sipDomain();}
function _sipContact(){return '<sip:'+_sip.cfg.username+'@'+_sipWsHostname()+';transport=ws>';}

function _sipExtractTag(h){var m=(h||'').match(/;tag=([^\s;,>]+)/);return m?m[1]:null;}
function _sipExtractURI(h){var m=(h||'').match(/<([^>]+)>/);if(m)return m[1];m=(h||'').match(/(sips?:[^\s;,>]+)/);return m?m[1]:(h||'').trim();}
function _sipExtractDisplay(h){var m=(h||'').match(/"([^"]+)"/);return m?m[1]:null;}

function _sipParseWWWAuth(h){
  var r={},re=/(\w+)=(?:"([^"]+)"|([^,\s]+))/g,m;
  while((m=re.exec(h))!==null)r[m[1]]=m[2]!==undefined?m[2]:m[3];
  return r;
}

function _sipDigest(method,uri,auth,user,pass){
  var realm=auth.realm||'',nonce=auth.nonce||'';
  var qop=auth.qop?(auth.qop.split(',')[0].trim()):null;
  var ha1=_md5(user+':'+realm+':'+pass);
  var ha2=_md5(method.toUpperCase()+':'+uri);
  var nc='00000001',cnonce=_sipRand(8),resp;
  if(qop==='auth'||qop==='auth-int'){
    resp=_md5(ha1+':'+nonce+':'+nc+':'+cnonce+':auth:'+ha2);
    return 'Digest username="'+user+'",realm="'+realm+'",nonce="'+nonce+'",uri="'+uri+'",nc='+nc+',cnonce="'+cnonce+'",qop=auth,response="'+resp+'",algorithm=MD5';
  }
  resp=_md5(ha1+':'+nonce+':'+ha2);
  return 'Digest username="'+user+'",realm="'+realm+'",nonce="'+nonce+'",uri="'+uri+'",response="'+resp+'",algorithm=MD5';
}

// ── SIP message builder ────────────────────────────────────────────────────
function _sipBuildReq(method,uri,hdrs,body){
  var lines=[method+' '+uri+' SIP/2.0'];
  Object.keys(hdrs).forEach(function(k){lines.push(k+': '+hdrs[k]);});
  lines.push('Content-Length: '+(body?body.length:0));
  lines.push('');
  if(body)lines.push(body);
  return lines.join('\r\n');
}

// ── SIP message parser ─────────────────────────────────────────────────────
function _sipParse(raw){
  var sep=raw.indexOf('\r\n\r\n'), sepLen=4;
  if(sep<0){sep=raw.indexOf('\n\n'); sepLen=2;}
  var hdrSec=sep>=0?raw.substring(0,sep):raw;
  var body=sep>=0?raw.substring(sep+sepLen):'';
  var lines=hdrSec.split(/\r?\n/);
  var fl=lines[0], hdrs={};
  var compact={v:'via',f:'from',t:'to',m:'contact',i:'call-id',l:'content-length',c:'content-type'};
  for(var i=1;i<lines.length;i++){
    var ln=lines[i]; if(!ln.trim())continue;
    var col=ln.indexOf(':'); if(col<0)continue;
    var k=ln.substring(0,col).trim().toLowerCase();
    var v=ln.substring(col+1).trim();
    k=compact[k]||k;
    hdrs[k]=hdrs[k]!==undefined?hdrs[k]+'\r\n'+v:v;
  }
  var rm=fl.match(/^SIP\/2\.0\s+(\d+)\s+(.*)/);
  return{isResponse:!!rm,status:rm?parseInt(rm[1]):null,reason:rm?rm[2]:null,
         method:!rm?fl.split(' ')[0]:null,uri:!rm?fl.split(' ')[1]:null,
         headers:hdrs,body:body};
}

// ── SIP response builder (echoes request Via/From/To/Call-ID/CSeq) ─────────
function _sipBuildResp(req,status,reason,extraHdrs,body){
  var lines=['SIP/2.0 '+status+' '+reason];
  // Echo Via (all lines)
  var via=req.headers['via']||'';
  via.split('\r\n').forEach(function(v){if(v.trim())lines.push('Via: '+v.trim());});
  // From: copy exactly
  if(req.headers['from'])lines.push('From: '+req.headers['from']);
  // To: add our tag for 2xx
  var toHdr=req.headers['to']||'';
  if(status>=200&&!toHdr.match(/;tag=/)){
    if(!_sip.myToTag)_sip.myToTag=_sipTag();
    toHdr+=';tag='+_sip.myToTag;
  }
  lines.push('To: '+toHdr);
  if(req.headers['call-id'])lines.push('Call-ID: '+req.headers['call-id']);
  if(req.headers['cseq'])   lines.push('CSeq: '+req.headers['cseq']);
  Object.keys(extraHdrs).forEach(function(k){lines.push(k+': '+extraHdrs[k]);});
  lines.push('Content-Length: '+(body?body.length:0));
  lines.push('');
  if(body)lines.push(body);
  return lines.join('\r\n');
}

// ── Low-level send ─────────────────────────────────────────────────────────
function _sipSend(msg){
  if(_sip.ws&&_sip.ws.readyState===1){
    console.debug('[IPLink SIP] >>>\n'+msg);
    _sip.ws.send(msg);
  }
}

// ── REGISTER ───────────────────────────────────────────────────────────────
function _sipRegister(authHdr){
  var cfg=_sip.cfg, dom=_sipDomain(), wshn=_sipWsHostname();
  _sip.regCsq++;
  var hdrs={
    'Via':         'SIP/2.0/WS '+wshn+';branch='+_sipBranch()+';rport',
    'Max-Forwards':'70',
    'From':        '"'+cfg.display_name+'" <'+_sipSelfUri()+'>;tag='+_sip.regFromTag,
    'To':          '<'+_sipSelfUri()+'>',
    'Call-ID':     _sip.regCid,
    'CSeq':        _sip.regCsq+' REGISTER',
    'Contact':     _sipContact()+';+sip.ice',
    'Expires':     '600',
    'Allow':       'INVITE,ACK,CANCEL,OPTIONS,BYE,INFO',
    'User-Agent':  'SignalScope-IPLink/1.1',
  };
  if(authHdr)hdrs['Authorization']=authHdr;
  _sipSend(_sipBuildReq('REGISTER','sip:'+dom,hdrs,''));
  _sipSetState('registering');
  // Timeout: if no response in 15 s, show an error rather than spinning forever
  clearTimeout(_sip.regTimeoutTimer);
  _sip.regTimeoutTimer=setTimeout(function(){
    if(_sip.state==='registering'){
      _sipSetState('error','Registration timed out — no response from server. Check the server URL and that it accepts SIP over WebSocket.');
    }
  },15000);
}

// ── Connect to SIP server ──────────────────────────────────────────────────
// Listen for CSP violations — if the browser blocks the SIP WebSocket due to
// Content-Security-Policy, a securitypolicyviolation event fires before onerror.
var _sipCspBlocked = false;
document.addEventListener('securitypolicyviolation', function(e){
  if(_sip.cfg && _sip.cfg.server && e.blockedURI &&
     _sip.cfg.server.indexOf(e.blockedURI.replace(/^wss?:/,'')) >= 0){
    _sipCspBlocked = true;
    _sipSetState('error','WebSocket blocked by browser security policy (CSP). Reload this page and try again — if it persists, contact your SignalScope administrator.');
  }
});

function _sipConnect(cfg){
  _sipStop();
  _sip.cfg=cfg;
  _sip.realm=null;  // reset; will be learned from server's 401 challenge
  _sip.regCid=_sipCid(_sipDomain());
  _sip.regFromTag=_sipTag();
  _sip.regCsq=0;
  _sipCspBlocked=false;
  _sipSetState('connecting');
  try{
    _sip.ws=new WebSocket(cfg.server,['sip']);
    _sip.ws.onopen=function(){_sipRegister();};
    _sip.ws.onmessage=function(e){_sipHandleMsg(e.data);};
    _sip.ws.onerror=function(ev){
      if(_sipCspBlocked) return; // already shown CSP error
      var hint='';
      if(location.protocol==='https:'&&cfg.server.indexOf('wss://')!==0){
        hint=' — hub is on HTTPS, server URL must use wss:// not ws://';
      } else {
        hint=' — possible causes: (1) cert not trusted — open '+cfg.server.replace('wss://','https://')+' in a new tab; (2) wrong port or server not running; (3) firewall blocking the port';
      }
      _sipSetState('error','WebSocket error'+hint);
    };
    _sip.ws.onclose=function(){
      if(_sip.state==='idle'||_sipCspBlocked)return;
      _sipSetState('error','Disconnected');
      _sip.retryTimer=setTimeout(function(){if(_sip.cfg)_sipConnect(_sip.cfg);},30000);
    };
  }catch(e){_sipSetState('error',e.message);}
}

function _sipStop(){
  clearTimeout(_sip.regTimer);clearTimeout(_sip.retryTimer);clearTimeout(_sip.regTimeoutTimer);clearInterval(_sip.durTimer);
  if(_sip.ws){try{_sip.ws.close();}catch(e){}_sip.ws=null;}
  _sipCleanupCall();
  _sip.state='idle'; _sipUpdateUI();
}

// ── Incoming INVITE ────────────────────────────────────────────────────────
function _sipHandleInvite(msg){
  _sipSend(_sipBuildResp(msg,100,'Trying',{},''));
  _sip.inInvite=msg;
  _sip.myToTag=null;   // will be created on first 2xx
  _sipSetState('incoming');
}

function sipAnswerCall(){
  if(!_sip.inInvite)return;
  var inv=_sip.inInvite;
  _sipSend(_sipBuildResp(inv,180,'Ringing',{'Contact':_sipContact()},''));
  // Hide the incoming banner immediately — use 'dialling' so call card renders
  // with the caller's name while we set up WebRTC (state→'incall' after ACK).
  _sipSetState('dialling');
  navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true},video:false})
    .then(function(stream){
      _sip.micStream=stream;
      _sip.pc=new RTCPeerConnection({iceServers:STUN.map(function(u){return{urls:u};})});
      stream.getTracks().forEach(function(t){_sip.pc.addTrack(t,stream);});
      _sip.pc.ontrack=function(e){
        var a=document.getElementById('sipAudio');
        if(a&&e.streams[0]){a.srcObject=e.streams[0];_sipSetupRemoteMeter(e.streams[0]);}
      };
      // Only tear down on 'failed' — 'disconnected' is temporary and ICE may recover.
      _sip.pc.onconnectionstatechange=function(){
        var cs=_sip.pc.connectionState;
        if(cs==='failed'){
          _sipCleanupCall();_sipSetState('registered');
          _sipShowCallErr('Call ended: connection failed');
        }
      };
      return _sip.pc.setRemoteDescription({type:'offer',sdp:inv.body});
    })
    .then(function(){return _sip.pc.createAnswer();})
    .then(function(ans){return _sip.pc.setLocalDescription(ans);})
    .then(function(){
      return new Promise(function(res){
        if(_sip.pc.iceGatheringState==='complete'){res();return;}
        _sip.pc.onicegatheringstatechange=function(){if(_sip.pc.iceGatheringState==='complete')res();};
        setTimeout(res,3000);
      });
    })
    .then(function(){
      var sdp=_sip.pc.localDescription.sdp;
      _sipSend(_sipBuildResp(inv,200,'OK',{'Contact':_sipContact(),'Content-Type':'application/sdp'},sdp));
      _sip.callCid=inv.headers['call-id']||'';
      _sipSetMicMeter(_sip.micStream);
      _sip.callStart=Date.now();
      _sipSetState('incall');
    })
    .catch(function(e){
      // Properly clean up so the incoming banner doesn't stick around.
      _sipSend(_sipBuildResp(inv,500,'Internal Error',{},''));
      console.error('SIP answer error:',e);
      _sipCleanupCall();
      _sipSetState('registered');
      _sipShowCallErr('Could not answer call: '+(e.message||e));
    });
}

function sipDeclineCall(){
  if(_sip.inInvite)_sipSend(_sipBuildResp(_sip.inInvite,603,'Decline',{},''));
  _sip.inInvite=null;
  _sipSetState('registered');
}

// ── Outgoing INVITE ────────────────────────────────────────────────────────
function sipDial(target){
  var t=(target||'').trim();
  if(!t){_sipShowCallErr('Enter an extension or SIP URI');return;}
  if(_sip.state!=='registered'){_sipShowCallErr('Not registered — check SIP settings');return;}
  var dom=_sipDomain();  // explicit config → learned realm → WS hostname
  // Build the call URI.  A SIP URI must have a host part (sip:user@host).
  // sip:ext with no @host is invalid — the server parses it as host=ext and
  // returns 484.  Always append the domain; _sipDomain() uses the realm
  // learned from the server's REGISTER challenge so it's always correct.
  _sip.callUri = t.match(/^sips?:/i) ? t
               : t.indexOf('@')>=0   ? 'sip:'+t
               : 'sip:'+t+'@'+dom;
  _sip.callCid=_sipCid(dom);
  _sip.callFromTag=_sipTag();
  _sip.callToTag=null;
  _sip.callCsq=0;
  _sip.myToTag=null;
  navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true},video:false})
    .then(function(stream){
      _sip.micStream=stream;
      _sip.pc=new RTCPeerConnection({iceServers:STUN.map(function(u){return{urls:u};})});
      stream.getTracks().forEach(function(t){_sip.pc.addTrack(t,stream);});
      _sip.pc.ontrack=function(e){
        var a=document.getElementById('sipAudio');
        if(a&&e.streams[0]){a.srcObject=e.streams[0];_sipSetupRemoteMeter(e.streams[0]);}
      };
      _sip.pc.onconnectionstatechange=function(){
        var cs=_sip.pc.connectionState;
        if(cs==='failed'){
          _sipCleanupCall();_sipSetState('registered');
          _sipShowCallErr('Call ended: connection failed');
        }
      };
      return _sip.pc.createOffer({offerToReceiveAudio:true});
    })
    .then(function(offer){return _sip.pc.setLocalDescription(offer);})
    .then(function(){
      return new Promise(function(res){
        if(_sip.pc.iceGatheringState==='complete'){res();return;}
        _sip.pc.onicegatheringstatechange=function(){if(_sip.pc.iceGatheringState==='complete')res();};
        setTimeout(res,3000);
      });
    })
    .then(function(){
      _sipSetMicMeter(_sip.micStream);
      _sipSendInvite(_sip.callUri,_sipMungeSdp(_sip.pc.localDescription.sdp));
      _sipSetState('dialling');
    })
    .catch(function(e){_sipShowCallErr('Microphone error: '+e.message);});
}

function _sipSendInvite(uri,sdp){
  var cfg=_sip.cfg, dom=_sipDomain();
  _sip.callCsq++;
  var hdrs={
    'Via':          'SIP/2.0/WS '+_sipWsHostname()+';branch='+_sipBranch()+';rport',
    'Max-Forwards': '70',
    'From':         '"'+cfg.display_name+'" <'+_sipSelfUri()+'>;tag='+_sip.callFromTag,
    'To':           '<'+uri+'>',
    'Call-ID':      _sip.callCid,
    'CSeq':         _sip.callCsq+' INVITE',
    'Contact':      _sipContact(),
    'Allow':        'INVITE,ACK,CANCEL,OPTIONS,BYE',
    'Content-Type': 'application/sdp',
  };
  _sipSend(_sipBuildReq('INVITE',uri,hdrs,sdp));
}

function _sipSendAck(okMsg,uri){
  var cfg=_sip.cfg, dom=_sipDomain();
  _sip.callCsq++;
  var toHdr=okMsg.headers['to']||('<'+uri+'>');
  var hdrs={
    'Via':          'SIP/2.0/WS '+_sipWsHostname()+';branch='+_sipBranch()+';rport',
    'Max-Forwards': '70',
    'From':         '"'+cfg.display_name+'" <'+_sipSelfUri()+'>;tag='+_sip.callFromTag,
    'To':           toHdr,
    'Call-ID':      _sip.callCid,
    'CSeq':         _sip.callCsq+' ACK',
    'Contact':      _sipContact(),
  };
  _sipSend(_sipBuildReq('ACK',uri,hdrs,''));
}

function _sipSendBye(){
  var cfg=_sip.cfg, dom=_sipDomain();
  var uri=_sip.callUri||_sipSelfUri();
  var toHdr='<'+uri+'>'+((_sip.callToTag)?';tag='+_sip.callToTag:'');
  _sip.callCsq++;
  var hdrs={
    'Via':          'SIP/2.0/WS '+_sipWsHostname()+';branch='+_sipBranch()+';rport',
    'Max-Forwards': '70',
    'From':         '"'+cfg.display_name+'" <'+_sipSelfUri()+'>;tag='+(_sip.callFromTag||_sipTag()),
    'To':           toHdr,
    'Call-ID':      _sip.callCid||_sipCid(dom),
    'CSeq':         _sip.callCsq+' BYE',
  };
  _sipSend(_sipBuildReq('BYE',uri,hdrs,''));
}

function sipHangup(){
  if(_sip.state==='incoming'){sipDeclineCall();return;}
  if(_sip.state==='incall'||_sip.state==='dialling')_sipSendBye();
  _sipCleanupCall();
  _sipSetState(_sip.state==='idle'?'idle':'registered');
}

// ── Main message handler ───────────────────────────────────────────────────
function _sipHandleMsg(raw){
  console.debug('[IPLink SIP] <<<\n'+raw);
  var msg=_sipParse(raw);
  if(msg.isResponse){
    var csqH=msg.headers['cseq']||'';
    var method=csqH.replace(/^\d+\s+/,'').toUpperCase().trim();
    var st=msg.status;
    if(method==='REGISTER'){
      clearTimeout(_sip.regTimeoutTimer);  // got a response — cancel the 15 s timeout
      if(st===200){
        _sip.regAuthAttempts=0;
        _sipSetState('registered');
        var exp=parseInt(((msg.headers['contact']||'').match(/expires=(\d+)/)||[])[1]||'600');
        clearTimeout(_sip.regTimer);
        _sip.regTimer=setTimeout(function(){if(_sip.state==='registered')_sipRegister();},exp*900);
      }else if(st===401||st===407){
        _sip.regAuthAttempts=(_sip.regAuthAttempts||0)+1;
        if(_sip.regAuthAttempts>2){
          _sipSetState('error','Authentication failed — check your SIP username and password.');
          return;
        }
        var wwwH=msg.headers['www-authenticate']||msg.headers['proxy-authenticate']||'';
        var auth=_sipParseWWWAuth(wwwH);
        // Learn the server's SIP realm from the challenge — this is the correct
        // domain for all SIP URIs (INVITE Request-URI, From, To).  Store it so
        // that bare extensions dial as sip:ext@realm rather than sip:ext (invalid)
        // or sip:ext@ws-hostname (404/484 if the hostname isn't the SIP realm).
        if(auth.realm && !_sip.realm) _sip.realm = auth.realm;
        var regUri='sip:'+_sipDomain();
        _sipRegister(_sipDigest('REGISTER',regUri,auth,_sip.cfg.username,_sip.cfg.password));
      }else if(st>=400){
        _sipSetState('error','Registration failed ('+st+')');
      }
    }else if(method==='INVITE'){
      if(st===180||st===183){
        _sipSetState('dialling');
        var pill=document.getElementById('sipStatusPill');
        if(pill)pill.querySelector('span:last-child').textContent='SIP: Ringing…';
      }else if(st===200){
        _sip.callToTag=_sipExtractTag(msg.headers['to']||'');
        var sdp=msg.body;
        if(_sip.pc&&sdp){
          _sip.pc.setRemoteDescription({type:'answer',sdp:sdp})
            .then(function(){
              _sipSendAck(msg,_sip.callUri);
              _sip.callStart=Date.now();
              _sipSetState('incall');
            }).catch(function(e){console.error('SIP SDP:',e);});
        }
      }else if(st>=400){
        // RFC 3261 §17.1.1.3: non-2xx INVITE responses MUST be ACKed.
        // Build the ACK from the response headers directly — do NOT use
        // _sip.callCid / _sip.callFromTag because _sipCleanupCall() may have
        // already nulled them (server retransmits the 4xx until it gets an ACK).
        // The 4xx echoes Call-ID, From, CSeq from the original INVITE, so we
        // always have everything we need in msg.headers.
        // Use the To URI from the 4xx response as the ACK Request-URI.
        // _sip.callUri may already be null if _sipCleanupCall() ran on a
        // previous retransmission. The To header reliably contains the callee.
        var _ackUri = _sipExtractURI(msg.headers['to']) || _sip.callUri || _sipSelfUri();
        var _ackHdrs = {
          'Via':          'SIP/2.0/WS '+_sipWsHostname()+';branch='+_sipBranch()+';rport',
          'Max-Forwards': '70',
          'From':         msg.headers['from']  || '',
          'To':           msg.headers['to']    || '',
          'Call-ID':      msg.headers['call-id']|| '',
          'CSeq':         (msg.headers['cseq'] || '1 INVITE').replace(/INVITE$/i, 'ACK'),
          'Contact':      _sipContact(),
        };
        _sipSend(_sipBuildReq('ACK', _ackUri, _ackHdrs, ''));
        var reason=st+' '+(msg.reason||'');
        var hint='';
        if(st===404) hint=' — extension not found on server';
        else if(st===484) hint=' — check the dial string and SIP Domain/Realm setting';
        else if(st===486||st===600) hint=' — destination busy';
        else if(st===403) hint=' — forbidden (check dial permissions)';
        else if(st===488) hint=' — server rejected codec/SDP (check WebRTC support on server)';
        else if(st===500) hint=' — server internal error (check dial plan / extension exists)';
        console.warn('[IPLink SIP] INVITE failed '+reason+' — check browser DevTools console for full SIP traffic (filter: IPLink SIP)');
        _sipCleanupCall();
        _sipSetState('registered');
        _sipShowCallErr('Call failed: '+reason+hint);
      }
    }else if(method==='BYE'){
      if(st===200){_sipCleanupCall();_sipSetState('registered');}
    }
  }else{
    var m=msg.method;
    if(m==='INVITE'){
      if(_sip.state==='incall'||_sip.state==='incoming'||_sip.state==='dialling'){
        // Re-INVITE or second call while busy: decline
        _sipSend(_sipBuildResp(msg,486,'Busy Here',{},''));
      }else{
        _sipHandleInvite(msg);
      }
    }else if(m==='BYE'){
      _sipSend(_sipBuildResp(msg,200,'OK',{},''));
      _sipCleanupCall();_sipSetState('registered');
    }else if(m==='CANCEL'){
      if(_sip.inInvite&&(_sip.inInvite.headers['call-id']||'')===(msg.headers['call-id']||'')){
        _sipSend(_sipBuildResp(msg,200,'OK',{},''));
        _sipSend(_sipBuildResp(_sip.inInvite,487,'Request Terminated',{},''));
        _sip.inInvite=null;_sipSetState('registered');
      }
    }else if(m==='OPTIONS'){
      _sipSend(_sipBuildResp(msg,200,'OK',{'Allow':'INVITE,ACK,CANCEL,OPTIONS,BYE','Accept':'application/sdp'},''));
    }else if(m==='ACK'){
      // ACK to our 200 OK — call now fully established.
      // State is 'incall' if sipAnswerCall() finished before ACK arrived,
      // or 'dialling' if ACK arrived before the promise chain completed.
      if(_sip.state==='incoming'||_sip.state==='dialling'){
        if(!_sip.callStart)_sip.callStart=Date.now();
        _sipSetState('incall');
      }
    }
  }
}

// ── Level meters for SIP ───────────────────────────────────────────────────
function _sipSetMicMeter(stream){
  try{
    var ctx=new(window.AudioContext||window.webkitAudioContext)();
    var src=ctx.createMediaStreamSource(stream);
    var an=ctx.createAnalyser();an.fftSize=512;src.connect(an);
    _sip.micAnalyser=an;
  }catch(e){}
}
function _sipSetupRemoteMeter(stream){
  try{
    var ctx=new(window.AudioContext||window.webkitAudioContext)();
    var src=ctx.createMediaStreamSource(stream);
    var an=ctx.createAnalyser();an.fftSize=512;src.connect(an);
    _sip.remoteAnalyser=an;
  }catch(e){}
}
function _sipReadLevel(an){
  if(!an)return 0;
  var buf=new Uint8Array(an.frequencyBinCount);
  an.getByteTimeDomainData(buf);
  var sq=0;for(var i=0;i<buf.length;i++){var s=(buf[i]-128)/128;sq+=s*s;}
  return Math.sqrt(sq/buf.length);
}

// ── UI update ──────────────────────────────────────────────────────────────
function _sipSetState(state,errMsg){_sip.state=state;_sipUpdateUI(errMsg);}

function _sipUpdateUI(errMsg){
  var s=_sip.state;
  var pill=document.getElementById('sipStatusPill');
  var txt=document.getElementById('sipStatusTxt');
  var banner=document.getElementById('sipIncomingBanner');
  var callCard=document.getElementById('sipCallCard');
  var dialBtn=document.getElementById('sipDialBtn');
  var map={
    idle:      ['sip-off','SIP: Off'],
    connecting:['sip-off','SIP: Connecting…'],
    registering:['sip-off','SIP: Registering…'],
    registered:['sip-conn','SIP: Registered'],
    incoming:  ['sip-ring','SIP: Incoming call'],
    dialling:  ['sip-conn','SIP: Calling…'],
    incall:    ['sip-incall','SIP: On call'],
    error:     ['sip-err','SIP: '+(errMsg||'Error')],
  };
  var info=map[s]||['sip-off',s];
  if(pill){pill.className='sip-pill '+info[0];}
  if(txt){txt.textContent=info[1];}
  // Incoming banner
  if(banner){
    if(s==='incoming'&&_sip.inInvite){
      var fromH=_sip.inInvite.headers['from']||'';
      var disp=_sipExtractDisplay(fromH);
      var uri=_sipExtractURI(fromH).replace(/^sip:/i,'').split('@')[0];
      var name=disp?(disp+' ('+uri+')'):(uri||'Unknown');
      var cn=document.getElementById('sipCallerName');
      if(cn)cn.textContent=name;
      banner.style.display='flex';
    }else{
      banner.style.display='none';
    }
  }
  // Active call card
  if(callCard){
    if(s==='incall'||s==='dialling'){
      callCard.style.display='';
      var remEl=document.getElementById('sipCallRemote');
      if(remEl){
        var r=_sip.callUri||((_sip.inInvite)?_sipExtractURI(_sip.inInvite.headers['from']||''):'');
        remEl.textContent=r.replace(/^sip:/i,'');
      }
    }else{
      callCard.style.display='none';
    }
  }
  // Dial button enable/disable
  if(dialBtn)dialBtn.disabled=(s!=='registered');
  // Stop duration timer if not in call
  if(s!=='incall'&&s!=='dialling'){
    clearInterval(_sip.durTimer);_sip.durTimer=null;
    var dur=document.getElementById('sipCallDur');
    if(dur)dur.textContent='—';
  } else if(!_sip.durTimer){
    _sip.durTimer=setInterval(function(){
      var dur=document.getElementById('sipCallDur');
      if(dur&&_sip.callStart)dur.textContent=_fmt(Math.floor((Date.now()-_sip.callStart)/1000));
    },1000);
  }
}

function _sipShowCallErr(msg){
  var el=document.getElementById('sipCallErrMsg');
  if(!el)return;
  el.textContent=msg;el.style.display='';
  setTimeout(function(){el.style.display='none';},6000);
}

// ── Mic mute toggle ────────────────────────────────────────────────────────
function _sipToggleMute(){
  _sip.micMuted=!_sip.micMuted;
  if(_sip.micStream)_sip.micStream.getAudioTracks().forEach(function(t){t.enabled=!_sip.micMuted;});
  var btn=document.getElementById('sipMuteBtn');
  if(btn)btn.textContent=_sip.micMuted?'🔇 Mic MUTED':'🎤 Mic ON';
  if(_sip.micMuted){var el=document.getElementById('sipMicLvl');if(el)el.style.width='0%';}
}

// ── Cleanup ────────────────────────────────────────────────────────────────
function _sipCleanupCall(){
  clearInterval(_sip.durTimer);_sip.durTimer=null;
  if(_sip.pc){try{_sip.pc.close();}catch(e){}_sip.pc=null;}
  if(_sip.micStream){_sip.micStream.getTracks().forEach(function(t){t.stop();});_sip.micStream=null;}
  var a=document.getElementById('sipAudio');if(a)a.srcObject=null;
  _sip.remoteAnalyser=null;_sip.micAnalyser=null;
  _sip.inInvite=null;_sip.callCid=null;_sip.callFromTag=null;
  _sip.callToTag=null;_sip.callUri=null;_sip.callStart=null;_sip.micMuted=false;
  _sip.myToTag=null;
}

// ── Level meter ticker ─────────────────────────────────────────────────────
setInterval(function(){
  if(_sip.state!=='incall')return;
  var rl=_sipReadLevel(_sip.remoteAnalyser);
  var ml=_sipReadLevel(_sip.micAnalyser);
  var rf=document.getElementById('sipRemoteLvl'),rv=document.getElementById('sipRemoteLvlVal');
  var mf=document.getElementById('sipMicLvl'),mv=document.getElementById('sipMicLvlVal');
  if(rf)rf.style.width=(Math.min(rl*4,1)*100)+'%';
  if(rv)rv.textContent=rl>0?Math.round(rl*100)+'%':'—';
  if(!_sip.micMuted){if(mf)mf.style.width=(Math.min(ml*4,1)*100)+'%';if(mv)mv.textContent=ml>0?Math.round(ml*100)+'%':'—';}
  // RTT from WebRTC stats
  if(_sip.pc){
    _sip.pc.getStats().then(function(report){
      report.forEach(function(r){
        if(r.type==='candidate-pair'&&r.state==='succeeded'&&r.currentRoundTripTime!=null){
          var rtt=Math.round(r.currentRoundTripTime*1000);
          var row=document.getElementById('sipRttRow'),val=document.getElementById('sipRttVal');
          if(row)row.style.display='';
          if(val)val.textContent=rtt+' ms';
          val.style.color=rtt>100?'var(--wn)':'var(--ok)';
        }
      });
    }).catch(function(){});
  }
},200);

// ── SIP config save / load ─────────────────────────────────────────────────
function sipSaveCfg(){
  var cfg={
    enabled:  document.getElementById('sipEnabled').checked,
    server:   document.getElementById('sipServer').value.trim(),
    username: document.getElementById('sipUser').value.trim(),
    password: document.getElementById('sipPass').value,
    domain:   document.getElementById('sipDomain').value.trim(),
    display_name: document.getElementById('sipDisplayName').value.trim()||'Studio',
  };
  if(!cfg.server||!cfg.username){
    var msg=document.getElementById('sipSaveMsg');
    if(msg){msg.innerHTML='<div class="msg msg-err">Server URL and username are required</div>';return;}
  }
  fetch('/api/iplink/sip/config',{method:'POST',credentials:'same-origin',headers:csrfHdr(),body:JSON.stringify(cfg)})
    .then(function(r){return r.json();})
    .then(function(d){
      var msg=document.getElementById('sipSaveMsg');
      if(msg)msg.innerHTML='<div class="msg msg-ok">Saved. Connecting…</div>';
      setTimeout(function(){var m=document.getElementById('sipSaveMsg');if(m)m.innerHTML='';},3000);
      _sipConnect(cfg);
    })
    .catch(function(e){
      var msg=document.getElementById('sipSaveMsg');
      if(msg)msg.innerHTML='<div class="msg msg-err">'+_esc(''+e)+'</div>';
    });
}

function _sipLoadCfg(){
  fetch('/api/iplink/sip/config',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){
      if(d.server){
        document.getElementById('sipServer').value=d.server||'';
        document.getElementById('sipUser').value=d.username||'';
        document.getElementById('sipDomain').value=d.domain||'';
        document.getElementById('sipDisplayName').value=d.display_name||'Studio';
        document.getElementById('sipEnabled').checked=!!d.enabled;
        // Auto-connect if enabled (password returned from server for auto-connect only)
        if(d.enabled&&d.server&&d.username&&d._autopass){
          _sipConnect({server:d.server,username:d.username,password:d._autopass,
                       domain:d.domain,display_name:d.display_name||'Studio'});
        }
      }
    }).catch(function(){});
}

// ── Button wiring ──────────────────────────────────────────────────────────
document.getElementById('sipAnswerBtn').addEventListener('click',sipAnswerCall);
document.getElementById('sipRejectBtn').addEventListener('click',sipDeclineCall);
document.getElementById('sipHangupBtn').addEventListener('click',sipHangup);
document.getElementById('sipMuteBtn').addEventListener('click',_sipToggleMute);
document.getElementById('sipDialBtn').addEventListener('click',function(){
  sipDial(document.getElementById('sipDialInput').value);
});
document.getElementById('sipDialInput').addEventListener('keydown',function(e){
  if(e.key==='Enter')sipDial(this.value);
});
document.getElementById('sipSaveBtn').addEventListener('click',sipSaveCfg);
document.getElementById('sipDisconnectBtn').addEventListener('click',function(){
  _sipStop();
  var msg=document.getElementById('sipSaveMsg');
  if(msg){msg.innerHTML='<div class="msg msg-ok">Disconnected</div>';setTimeout(function(){msg.innerHTML='';},3000);}
});

// ── Init ──────────────────────────────────────────────────────────────────
_sipLoadCfg();
_sipUpdateUI();
</script>
</body></html>"""


# ─── Talent (contributor) page ────────────────────────────────────────────────

_TALENT_TPL = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{{room_name}} — IP Link</title>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<style nonce="{{csp_nonce()}}">
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
body{font-family:system-ui,sans-serif;background:radial-gradient(circle at top,#12376f 0%,var(--bg) 38%,#05101f 100%);color:var(--tx);min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:20px}
.card{background:var(--sur);border:1px solid var(--bor);border-radius:16px;padding:28px 24px;max-width:400px;width:100%;text-align:center;box-shadow:0 8px 32px rgba(0,0,0,.3)}
h1{font-size:22px;font-weight:800;margin-bottom:4px}
.sub{font-size:12px;color:var(--mu);margin-bottom:24px}
.status{font-size:14px;font-weight:700;padding:8px 0;margin-bottom:16px}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:middle}
.dot-wait{background:var(--mu)}
.dot-conn{background:var(--ok)}
.dot-err{background:var(--al)}
.dot-live{background:var(--ok);animation:pulse 1s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
/* Level meter */
.lvl-wrap{display:flex;align-items:center;gap:10px;margin:12px 0}
.lvl-label{font-size:12px;color:var(--mu);width:28px;text-align:right}
.lvl-outer{flex:1;height:12px;background:#0a1628;border-radius:6px;overflow:hidden}
.lvl-fill{height:12px;border-radius:6px;transition:width .1s;background:var(--ok)}
.lvl-val{font-size:12px;width:32px;text-align:left;font-variant-numeric:tabular-nums}
/* Mute button */
.mute-btn{width:100%;padding:14px;border:none;border-radius:10px;font-size:16px;font-weight:700;cursor:pointer;font-family:inherit;margin:14px 0;background:#1e3a5f;color:var(--tx);transition:background .2s}
.mute-btn.muted{background:#2a1010;color:var(--al)}
.mute-btn:active{filter:brightness(.85)}
/* Stats */
.stats{font-size:11px;color:var(--mu);margin-top:12px;line-height:1.8}
.err{color:var(--al);font-size:12px;margin-top:10px;padding:8px;background:#2a0a0a;border-radius:6px;border:1px solid #991b1b}
/* IFB */
.ifb-wrap{margin-top:14px;padding:10px 14px;background:#0a1628;border-radius:8px;font-size:12px;text-align:left}
.ifb-wrap label{color:var(--mu);display:block;margin-bottom:6px}
</style></head><body>
<div class="card" id="mainCard">
  <div style="font-size:36px;margin-bottom:12px">🎙</div>
  <h1 id="roomTitle">{{room_name}}</h1>
  <p class="sub">IP Link — Powered by SignalScope</p>

  <div id="statusLine" class="status">
    <span class="dot dot-wait"></span><span id="statusText">Initialising…</span>
  </div>

  <div id="lvlSection" style="display:none">
    <div class="lvl-wrap">
      <span class="lvl-label">You</span>
      <div class="lvl-outer"><div class="lvl-fill" id="tlvl" style="width:0%"></div></div>
      <span class="lvl-val" id="tlvlVal">—</span>
    </div>
    <div class="lvl-wrap">
      <span class="lvl-label">Hub</span>
      <div class="lvl-outer"><div class="lvl-fill" id="hlvl" style="width:0%;background:var(--acc)"></div></div>
      <span class="lvl-val" id="hlvlVal">—</span>
    </div>
  </div>

  <button class="mute-btn" id="muteBtn" style="display:none" onclick="toggleMute()">🎤 Mic ON</button>

  <div class="stats" id="statsDiv" style="display:none"></div>
  <div class="err" id="errDiv" style="display:none"></div>

  <!-- Hidden audio for IFB (hub talking back) -->
  <audio id="ifbAudio" autoplay playsinline style="display:none"></audio>
</div>

<script nonce="{{csp_nonce()}}">
var ROOM_ID = {{room_id|tojson}};
var STUN    = {{stun|tojson}};
var _pc, _mic, _muted=false, _pollTimer;
var _iceIdx = 0;   // hub ICE candidates consumed so far
var _statsTimer;

function _setStatus(dot, text){
  var sd = document.getElementById('statusLine');
  var dc = sd.querySelector('.dot');
  dc.className = 'dot '+dot;
  document.getElementById('statusText').textContent = text;
}
function _setErr(msg){
  var e=document.getElementById('errDiv');
  e.textContent=msg; e.style.display='';
}
function _apiPost(path, body){
  return fetch('/api/iplink'+path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
}
function _apiGet(path){
  return fetch('/api/iplink'+path);
}

// ─── Start connection ─────────────────────────────────────────────────────────
function _start(){
  _setStatus('dot-wait', 'Requesting microphone…');
  navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true},video:false})
    .then(function(stream){
      _mic = stream;
      _setupMicMeter(stream);
      document.getElementById('lvlSection').style.display='';
      document.getElementById('muteBtn').style.display='';
      _setStatus('dot-wait', 'Connecting…');
      _connect(stream);
    })
    .catch(function(e){
      _setStatus('dot-err', 'Microphone error');
      _setErr('Could not access microphone: '+e.message+'\nCheck your browser permissions.');
    });
}

function _connect(micStream){
  _pc = new RTCPeerConnection({iceServers: STUN.map(function(u){return{urls:u};})});

  // Add mic track
  micStream.getTracks().forEach(function(t){ _pc.addTrack(t, micStream); });

  // Receive IFB (hub talking back)
  _pc.ontrack = function(e){
    var audio = document.getElementById('ifbAudio');
    if(e.streams[0]){ audio.srcObject=e.streams[0]; _setupHubMeter(e.streams[0]); }
  };

  // Collect and send ICE
  _pc.onicecandidate = function(e){
    if(!e.candidate) return;
    _apiPost('/room/'+ROOM_ID+'/ice', {from:'talent', candidate:e.candidate});
  };

  _pc.onconnectionstatechange = function(){
    var s = _pc.connectionState;
    if(s==='connected'){
      _setStatus('dot-live','🔴 Live — connected to studio');
      _startStats();
    } else if(s==='disconnected'||s==='failed'){
      _setStatus('dot-err','Disconnected');
      _stopStats();
    } else if(s==='connecting'){
      _setStatus('dot-wait','Connecting…');
    }
    _apiPost('/room/'+ROOM_ID+'/status',{from:'talent',status:s});
  };

  // Create offer
  _pc.createOffer()
    .then(function(offer){ return _pc.setLocalDescription(offer).then(function(){ return offer; }); })
    .then(function(offer){
      return _apiPost('/room/'+ROOM_ID+'/offer', {offer: offer.sdp});
    })
    .then(function(){
      _setStatus('dot-wait','Waiting for studio to accept…');
      _pollAnswer();
    })
    .catch(function(e){ _setErr('WebRTC error: '+e.message); });
}

function _pollAnswer(){
  _apiGet('/room/'+ROOM_ID+'/answer')
    .then(function(r){ return r.json(); })
    .then(function(d){
      if(d.answer){
        _pc.setRemoteDescription({type:'answer', sdp:d.answer})
          .then(function(){ _pollHubIce(); })
          .catch(function(e){ _setErr('SDP error: '+e.message); });
      } else if(!d.room_exists){
        _setStatus('dot-err','Room not found');
        _setErr('This room has been deleted or expired.');
      } else {
        // Keep polling
        setTimeout(_pollAnswer, 600);
      }
    })
    .catch(function(){ setTimeout(_pollAnswer, 2000); });
}

function _pollHubIce(){
  _apiGet('/room/'+ROOM_ID+'/ice?from=hub&from_idx='+_iceIdx)
    .then(function(r){ return r.json(); })
    .then(function(d){
      (d.candidates||[]).forEach(function(c){
        _pc.addIceCandidate(c).catch(function(){});
      });
      _iceIdx = d.next_idx || _iceIdx;
      if(_pc.connectionState!=='connected'&&_pc.connectionState!=='closed'){
        setTimeout(_pollHubIce, 600);
      }
    })
    .catch(function(){ setTimeout(_pollHubIce, 2000); });
}

// ─── Level meters ─────────────────────────────────────────────────────────────
function _setupMicMeter(stream){
  try{
    var ctx = new(window.AudioContext||window.webkitAudioContext)();
    var src = ctx.createMediaStreamSource(stream);
    var an  = ctx.createAnalyser();
    an.fftSize=512; src.connect(an);
    var buf = new Uint8Array(an.frequencyBinCount);
    function tick(){
      an.getByteTimeDomainData(buf);
      var sq=0; for(var i=0;i<buf.length;i++){var s=(buf[i]-128)/128;sq+=s*s;}
      var rms=Math.sqrt(sq/buf.length);
      if(!_muted){
        var el=document.getElementById('tlvl');
        if(el) el.style.width=(Math.min(rms*4,1)*100)+'%';
        var vl=document.getElementById('tlvlVal');
        if(vl) vl.textContent=rms>0?Math.round(rms*100)+'%':'—';
        // Report level to hub
        _apiPost('/room/'+ROOM_ID+'/level',{from:'talent',level:rms});
      }
      requestAnimationFrame(tick);
    }
    tick();
  } catch(e){}
}

function _setupHubMeter(stream){
  try{
    var ctx = new(window.AudioContext||window.webkitAudioContext)();
    var src = ctx.createMediaStreamSource(stream);
    var an  = ctx.createAnalyser();
    an.fftSize=512; src.connect(an);
    var buf = new Uint8Array(an.frequencyBinCount);
    function tick(){
      an.getByteTimeDomainData(buf);
      var sq=0; for(var i=0;i<buf.length;i++){var s=(buf[i]-128)/128;sq+=s*s;}
      var rms=Math.sqrt(sq/buf.length);
      var el=document.getElementById('hlvl');
      if(el) el.style.width=(Math.min(rms*4,1)*100)+'%';
      var vl=document.getElementById('hlvlVal');
      if(vl) vl.textContent=rms>0?Math.round(rms*100)+'%':'—';
      requestAnimationFrame(tick);
    }
    tick();
  } catch(e){}
}

// ─── Mute ─────────────────────────────────────────────────────────────────────
function toggleMute(){
  _muted=!_muted;
  if(_mic){ _mic.getAudioTracks().forEach(function(t){t.enabled=!_muted;}); }
  var btn=document.getElementById('muteBtn');
  if(_muted){
    btn.textContent='🔇 Mic MUTED — tap to unmute';
    btn.classList.add('muted');
    document.getElementById('tlvl').style.width='0%';
    document.getElementById('tlvlVal').textContent='—';
  } else {
    btn.textContent='🎤 Mic ON';
    btn.classList.remove('muted');
  }
  _apiPost('/room/'+ROOM_ID+'/level',{from:'talent',level:0});
}

// ─── WebRTC stats ─────────────────────────────────────────────────────────────
function _startStats(){
  _statsTimer = setInterval(function(){
    if(!_pc) return;
    _pc.getStats().then(function(report){
      var rtt=null, loss=null, br=null;
      report.forEach(function(r){
        if(r.type==='candidate-pair'&&r.state==='succeeded'&&r.currentRoundTripTime!=null){
          rtt=Math.round(r.currentRoundTripTime*1000);
        }
        if(r.type==='inbound-rtp'&&r.mediaType==='audio'){
          if(r.packetsLost!=null&&r.packetsReceived!=null){
            var total=r.packetsLost+r.packetsReceived;
            loss=total>0?Math.round(r.packetsLost/total*1000)/10:0;
          }
        }
        if(r.type==='outbound-rtp'&&r.bytesSent!=null){br=r.bitrateMean||null;}
      });
      var sd=document.getElementById('statsDiv');
      if(sd){
        var parts=[];
        if(rtt!=null) parts.push('RTT: '+rtt+' ms');
        if(loss!=null) parts.push('Loss: '+loss+'%');
        sd.textContent=parts.join('  ·  ');
        sd.style.display=parts.length?'':'none';
        // POST stats to hub
        if(rtt!=null||loss!=null) _apiPost('/room/'+ROOM_ID+'/stats',{rtt_ms:rtt,loss_pct:loss});
      }
    }).catch(function(){});
  }, 3000);
}
function _stopStats(){ if(_statsTimer){clearInterval(_statsTimer);_statsTimer=null;} }

// ─── Kick off ─────────────────────────────────────────────────────────────────
window.addEventListener('load', function(){
  // Verify room still exists first
  _apiGet('/room/'+ROOM_ID+'/ping')
    .then(function(r){ return r.json(); })
    .then(function(d){
      if(!d.ok){ _setStatus('dot-err','Room not found'); _setErr('This link has expired or been deleted by the studio.'); return; }
      _start();
    })
    .catch(function(){ _setErr('Could not reach the SignalScope hub. Check your connection.'); });
});
</script>
</body></html>"""


# ─── Plugin registration ──────────────────────────────────────────────────────

def register(app, ctx):
    global _log
    login_required = ctx["login_required"]
    csrf_protect   = ctx["csrf_protect"]
    monitor        = ctx["monitor"]
    BUILD          = ctx["BUILD"]
    _log = monitor.log

    try:
        from flask import request, jsonify, render_template_string as rts, make_response
    except ImportError:
        return

    # Start cleanup thread
    threading.Thread(target=_cleanup_thread, daemon=True, name="IPLinkCleanup").start()

    # ── CSP patch — allow wss: on the hub IP Link page ─────────────────────────
    # SignalScope sets connect-src 'self' via an after_request handler. Flask
    # calls after_request handlers in REVERSE registration order, so inserting
    # at position 0 means our handler runs LAST — after SignalScope has already
    # set the CSP header — allowing us to reliably extend connect-src.
    from flask import request as _freq
    def _iplink_csp_patch(response):
        try:
            if _freq.path == "/hub/iplink":
                csp = response.headers.get("Content-Security-Policy", "")
                if csp and "connect-src" in csp and "wss:" not in csp:
                    response.headers["Content-Security-Policy"] = csp.replace(
                        "connect-src 'self'", "connect-src 'self' wss:")
        except Exception:
            pass
        return response
    app.after_request_funcs.setdefault(None, []).insert(0, _iplink_csp_patch)

    # ── Helper ─────────────────────────────────────────────────────────────────
    def _get_room(room_id):
        with _lock:
            return _rooms.get(room_id)

    # ── Hub management page ────────────────────────────────────────────────────
    @app.get("/hub/iplink")
    @login_required
    def iplink_hub():
        # csrf_token, csp_nonce, and topnav are Jinja2 context processors —
        # automatically available in render_template_string; do not pass explicitly.
        return rts(_HUB_TPL, stun=_STUN, build=BUILD)

    # ── Talent page (no login required) ───────────────────────────────────────
    @app.get("/iplink/join/<room_id>")
    def iplink_talent(room_id):
        try:
            from flask import render_template_string as rts
        except Exception:
            return "Error", 500
        room = _get_room(room_id)
        if not room:
            return "<h2 style='font-family:system-ui;padding:40px'>This IP Link room has expired or been deleted.</h2>", 404
        return rts(_TALENT_TPL, room_name=room["name"], room_id=room_id, stun=_STUN)

    # ── Room ping (talent checks room still alive) ────────────────────────────
    @app.get("/api/iplink/room/<room_id>/ping")
    def iplink_ping(room_id):
        room = _get_room(room_id)
        if not room:
            return jsonify({"ok": False}), 404
        _touch(room)
        return jsonify({"ok": True, "name": room["name"]})

    # ── Room CRUD ──────────────────────────────────────────────────────────────
    @app.get("/api/iplink/rooms")
    @login_required
    def iplink_list_rooms():
        with _lock:
            rooms = [_room_public(r) for r in sorted(_rooms.values(), key=lambda x: x["created"], reverse=True)]
        return jsonify({"rooms": rooms})

    @app.post("/api/iplink/rooms")
    @login_required
    @csrf_protect
    def iplink_create_room():
        data = request.get_json(silent=True) or {}
        name = str(data.get("name", "")).strip()[:50]
        quality = str(data.get("quality", "broadcast"))
        if not name:
            return jsonify({"error": "Room name required"}), 400
        room = _new_room(name, quality)
        with _lock:
            _rooms[room["id"]] = room
        _log(f"[IPLink] Room created: '{name}' ({quality}) id={room['id'][:8]}")
        return jsonify({"room": _room_public(room)}), 201

    @app.delete("/api/iplink/room/<room_id>")
    @login_required
    @csrf_protect
    def iplink_delete_room(room_id):
        with _lock:
            room = _rooms.pop(room_id, None)
        if room:
            _log(f"[IPLink] Room deleted: '{room['name']}'")
        return jsonify({"ok": bool(room)})

    @app.post("/api/iplink/room/<room_id>/reset")
    @login_required
    @csrf_protect
    def iplink_reset_room(room_id):
        with _lock:
            room = _rooms.get(room_id)
            if not room:
                return jsonify({"error": "Not found"}), 404
            room["status"]       = "waiting"
            room["offer"]        = None
            room["answer"]       = None
            room["talent_ice"]   = []
            room["hub_ice"]      = []
            room["talent_level"] = 0.0
            room["hub_level"]    = 0.0
            room["connected_at"] = None
            room["stats"]        = {}
            _touch(room)
        return jsonify({"ok": True})

    # ── Signalling ─────────────────────────────────────────────────────────────

    # Talent posts offer (no login — room_id is the token)
    @app.post("/api/iplink/room/<room_id>/offer")
    def iplink_post_offer(room_id):
        with _lock:
            room = _rooms.get(room_id)
            if not room:
                return jsonify({"error": "Room not found"}), 404
            data = request.get_json(silent=True) or {}
            sdp  = str(data.get("offer", "")).strip()
            if not sdp:
                return jsonify({"error": "No SDP"}), 400
            # Restore the CRLF that .strip() removed.  SDP is a line-oriented
            # format; RFC 4566 requires every line — including the last — to end
            # with CRLF.  Without it Chrome reports the last line as "Invalid SDP
            # line" because its parser can't find the line terminator.
            room["offer"]  = sdp + "\r\n"
            room["status"] = "offer_received"
            room["answer"] = None
            room["talent_ice"] = []
            room["hub_ice"]    = []
            room["talent_ip"]  = request.remote_addr or ""
            _touch(room)
        _log(f"[IPLink] Offer received for room '{room['name']}' from {room['talent_ip']}")
        return jsonify({"ok": True})

    # Hub gets talent's offer
    @app.get("/api/iplink/room/<room_id>/offer")
    @login_required
    def iplink_get_offer(room_id):
        room = _get_room(room_id)
        if not room:
            return jsonify({"error": "Not found"}), 404
        return jsonify({"offer": room.get("offer")})

    # Hub posts answer
    @app.post("/api/iplink/room/<room_id>/answer")
    @login_required
    @csrf_protect
    def iplink_post_answer(room_id):
        with _lock:
            room = _rooms.get(room_id)
            if not room:
                return jsonify({"error": "Not found"}), 404
            data = request.get_json(silent=True) or {}
            sdp  = str(data.get("answer", "")).strip()
            if not sdp:
                return jsonify({"error": "No SDP"}), 400
            room["answer"] = sdp + "\r\n"
            room["status"] = "connecting"
            _touch(room)
        return jsonify({"ok": True})

    # Talent polls for hub's answer
    @app.get("/api/iplink/room/<room_id>/answer")
    def iplink_get_answer(room_id):
        room = _get_room(room_id)
        if not room:
            return jsonify({"room_exists": False, "answer": None})
        _touch(room)
        return jsonify({"room_exists": True, "answer": room.get("answer")})

    # Either side posts an ICE candidate
    @app.post("/api/iplink/room/<room_id>/ice")
    def iplink_post_ice(room_id):
        with _lock:
            room = _rooms.get(room_id)
            if not room:
                return jsonify({"error": "Not found"}), 404
            data = request.get_json(silent=True) or {}
            side = str(data.get("from", ""))
            cand = data.get("candidate")
            if not cand:
                return jsonify({"ok": True})   # null candidate = end of candidates
            if side == "talent":
                room["talent_ice"].append(cand)
            elif side == "hub":
                room["hub_ice"].append(cand)
            _touch(room)
        return jsonify({"ok": True})

    # Either side polls for the other's ICE candidates
    @app.get("/api/iplink/room/<room_id>/ice")
    def iplink_get_ice(room_id):
        room = _get_room(room_id)
        if not room:
            return jsonify({"candidates": [], "next_idx": 0})
        side     = request.args.get("from", "talent")    # "talent" or "hub"
        from_idx = int(request.args.get("from_idx", 0))
        with _lock:
            candidates = room["talent_ice"][from_idx:] if side == "talent" else room["hub_ice"][from_idx:]
            next_idx   = from_idx + len(candidates)
        _touch(room)
        return jsonify({"candidates": candidates, "next_idx": next_idx})

    # Level update (either side posts their outgoing level)
    @app.post("/api/iplink/room/<room_id>/level")
    def iplink_post_level(room_id):
        with _lock:
            room = _rooms.get(room_id)
            if not room:
                return jsonify({"error": "Not found"}), 404
            data  = request.get_json(silent=True) or {}
            side  = str(data.get("from", ""))
            level = float(data.get("level", 0.0))
            if side == "talent":
                room["talent_level"] = max(0.0, min(1.0, level))
            elif side == "hub":
                room["hub_level"] = max(0.0, min(1.0, level))
            _touch(room)
        return jsonify({"ok": True})

    # Connection status update
    @app.post("/api/iplink/room/<room_id>/status")
    def iplink_post_status(room_id):
        with _lock:
            room = _rooms.get(room_id)
            if not room:
                return jsonify({"error": "Not found"}), 404
            data   = request.get_json(silent=True) or {}
            side   = str(data.get("from", ""))
            status = str(data.get("status", ""))
            if status == "connected":
                room["status"] = "connected"
                if not room["connected_at"]:
                    room["connected_at"] = time.time()
                _log(f"[IPLink] Connection established: room '{room['name']}'")
            elif status in ("disconnected", "failed"):
                room["status"] = "disconnected"
                room["disconnected_at"] = time.time()
                room["talent_level"] = 0.0
                room["hub_level"]    = 0.0
                if side == "hub":
                    _log(f"[IPLink] Room '{room['name']}' disconnected by hub")
            _touch(room)
        return jsonify({"ok": True})

    # WebRTC stats from talent
    @app.post("/api/iplink/room/<room_id>/stats")
    def iplink_post_stats(room_id):
        with _lock:
            room = _rooms.get(room_id)
            if not room:
                return jsonify({"error": "Not found"}), 404
            data = request.get_json(silent=True) or {}
            room["stats"] = {k: data[k] for k in ("rtt_ms", "loss_pct") if k in data}
            _touch(room)
        return jsonify({"ok": True})

    # Hub mute toggle
    @app.post("/api/iplink/room/<room_id>/mute")
    @login_required
    @csrf_protect
    def iplink_mute(room_id):
        with _lock:
            room = _rooms.get(room_id)
            if not room:
                return jsonify({"error": "Not found"}), 404
            room["hub_muted"] = not room["hub_muted"]
            muted = room["hub_muted"]
        _log(f"[IPLink] Hub IFB {'muted' if muted else 'unmuted'} on room '{room['name']}'")
        return jsonify({"ok": True, "muted": muted})

    # ── SIP config API ─────────────────────────────────────────────────────────
    @app.get("/api/iplink/sip/config")
    @login_required
    def iplink_sip_get_config():
        cfg = _load_sip_cfg()
        out = {k: v for k, v in cfg.items() if k != "password"}
        out["_autopass"] = cfg.get("password", "") if cfg.get("enabled") else ""
        return jsonify(out)

    @app.post("/api/iplink/sip/config")
    @login_required
    @csrf_protect
    def iplink_sip_save_config():
        data = request.get_json(silent=True) or {}
        cfg = _load_sip_cfg()
        for k in ("enabled", "server", "username", "domain", "display_name"):
            if k in data:
                cfg[k] = data[k]
        # Only overwrite password if a non-empty value was sent
        if data.get("password"):
            cfg["password"] = data["password"]
        _save_sip_cfg(cfg)
        _log(f"[IPLink] SIP config saved — server: {cfg.get('server','')}, user: {cfg.get('username','')}")
        return jsonify({"ok": True})

    _log(f"[IPLink] Plugin registered — v1.1.0 — {len(_STUN_SERVERS)} STUN server(s), SIP client enabled")
