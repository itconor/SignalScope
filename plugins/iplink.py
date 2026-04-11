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
    "version": "1.0.0",
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
</style></head><body>
{{ topnav("iplink") | safe }}
<main>
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
    <div>
      <h1 style="font-size:20px;font-weight:700">🎙 IP Link</h1>
      <p style="font-size:12px;color:var(--mu);margin-top:4px">Browser-based contribution codec — share a link, go live from any device</p>
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

  <!-- Room grid (rendered by JS) -->
  <div id="roomGrid"></div>
  <p id="noRooms" style="color:var(--mu);font-size:13px;display:none">No rooms yet — create one to generate a shareable link for your contributor.</p>

  <!-- Hidden audio elements for hub-side WebRTC -->
  <audio id="hubAudio" autoplay playsinline style="display:none"></audio>
</main>

<script nonce="{{csp_nonce()}}">
var _csrf = (document.querySelector('meta[name="csrf-token"]')||{}).content || '';
function csrfHdr(){ return {'X-CSRFToken': (document.querySelector('meta[name="csrf-token"]')||{}).content||'','Content-Type':'application/json'}; }

// ─── Per-room WebRTC state ───────────────────────────────────────────────────
var _pcs = {};     // room_id → RTCPeerConnection
var _streams = {}; // room_id → local MediaStream (mic)
var _iceIdx = {};  // room_id → last hub_ice index sent / talent_ice index consumed
var _levels = {};  // room_id → AudioContext analyser
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

// ─── Hub WebRTC negotiation ──────────────────────────────────────────────────
function acceptCall(roomId){
  var btn = document.getElementById('accept_'+roomId);
  if(btn){ btn.disabled=true; btn.textContent='Connecting…'; }

  _getHubMic(function(err, micStream){
    if(err){
      alert('Microphone access denied: '+err.message+'\nYou can still receive audio but cannot send IFB/talkback.');
    }
    // Fetch the talent's offer
    fetch('/api/iplink/room/'+roomId+'/offer', {credentials:'same-origin'})
      .then(function(r){ return r.json(); })
      .then(function(d){
        if(!d.offer){ alert('No offer found — talent may have disconnected.'); return; }
        var pc = new RTCPeerConnection({iceServers: STUN.map(function(u){return{urls:u};})});
        _pcs[roomId] = pc;
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
          if(pc.connectionState === 'disconnected' || pc.connectionState === 'failed'){
            fetch('/api/iplink/room/'+roomId+'/status',{method:'POST',credentials:'same-origin',headers:csrfHdr(),body:JSON.stringify({from:'hub',status:'disconnected'})});
          }
        };

        // Set remote offer, create answer
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
          .catch(function(e){ console.error('Hub WebRTC error:', e); });
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
    // Accept banner
    if(r.status==='offer_received'){
      html+='<div class="accept-banner">📲 <span style="flex:1">Incoming call from contributor</span>';
      html+='<button class="btn bp bs" id="accept_'+r.id+'" onclick="acceptCall(\''+r.id+'\')">✅ Accept</button>';
      html+='</div>';
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
</script>
</body></html>"""


# ─── Talent (contributor) page ────────────────────────────────────────────────

_TALENT_TPL = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{{room_name}} — IP Link</title>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<style>
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

<script>
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
        from flask import request, jsonify, render_template_string, make_response
    except ImportError:
        return

    # Start cleanup thread
    threading.Thread(target=_cleanup_thread, daemon=True, name="IPLinkCleanup").start()

    # ── Helper ─────────────────────────────────────────────────────────────────
    def _get_room(room_id):
        with _lock:
            return _rooms.get(room_id)

    # ── Hub management page ────────────────────────────────────────────────────
    @app.get("/hub/iplink")
    @login_required
    def iplink_hub():
        try:
            from flask import render_template_string as rts
            from signalscope import _csp_nonce, csrf_token, topnav  # noqa
        except Exception:
            try:
                import sys
                ss = sys.modules.get("signalscope") or sys.modules.get("__main__")
                _csp_nonce = ss._csp_nonce
                csrf_token  = ss.csrf_token
                topnav = ss.topnav
            except Exception:
                return "IPLink plugin error — could not import SignalScope helpers", 500
        return rts(_HUB_TPL, stun=_STUN, build=BUILD,
                   csp_nonce=_csp_nonce, csrf_token=csrf_token, topnav=topnav)

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
            room["offer"]  = sdp
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
            room["answer"] = sdp
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

    _log(f"[IPLink] Plugin registered — v1.0.0 — {len(_STUN_SERVERS)} STUN server(s)")
