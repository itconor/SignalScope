# livewire.py  ──  Axia Livewire source discovery plugin for SignalScope
# Drop into plugins/. No third-party packages required.
#
# Listens on 239.192.255.3:4001 (LWAP) for Axia Livewire source advertisements.
# Client nodes discover sources locally and push them to the hub every 30 s.
# Hub stores per-site source tables and serves a unified overview page.
# "Create Input" buttons add Livewire RTP inputs directly from the source table.
#
# Authors: Conor Ewings (ITConor) & James Pyper (JPDesignsNI)

SIGNALSCOPE_PLUGIN = {
    "id":      "livewire",
    "label":   "Livewire",
    "url":     "/livewire",
    "icon":    "🔗",
    "version": "1.0.3",
}

import json, os, struct, threading, time, urllib.request
import select as _sel
import socket as _sock
from flask import jsonify, redirect, render_template_string, request

_BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
_CFG_PATH  = os.path.join(_BASE_DIR, "livewire_cfg.json")
_DATA_PATH = os.path.join(_BASE_DIR, "livewire_data.json")

_LWAP_GROUP    = "239.192.255.3"
_LWAP_PORT     = 4001
_PUSH_INTERVAL = 30    # seconds between client→hub data pushes
_DEF_TIMEOUT   = 300   # seconds before a source is marked stale

_lw_monitor = None
_hub_data   = {}       # site_name → {sources:[...], updated_at:float}
_hub_lock   = threading.Lock()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _lw_ip(cid: int) -> str:
    """Derive the RTP multicast IP from a Livewire stream ID."""
    return f"239.192.{(cid >> 8) & 0xFF}.{cid & 0xFF}"

def _load_cfg() -> dict:
    try:
        with open(_CFG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}

def _save_cfg(d: dict):
    with open(_CFG_PATH, "w") as f:
        json.dump(d, f, indent=2)

def _load_hub_data():
    global _hub_data
    try:
        with open(_DATA_PATH) as f:
            _hub_data = json.load(f)
    except Exception:
        _hub_data = {}

def _save_hub_data():
    try:
        with open(_DATA_PATH, "w") as f:
            json.dump(_hub_data, f, indent=2)
    except Exception:
        pass


# ── LWAP parser ───────────────────────────────────────────────────────────────

def _parse_lwap(data: bytes, sender_ip: str):
    """
    Parse one Livewire Advertisement Protocol UDP datagram.

    Real LWAP packets are NUL-byte or newline-separated ASCII key=value
    pairs (NOT a verb-based format).  Known fields:
        ch=<channel>      — Livewire channel number (required)
        srcn=<name>       — human-readable source name
        src=<addr>        — multicast address (dotted quad)
        rate=<hz>         — sample rate, e.g. 48000
        fmt=<fmt>         — e.g. L24, L20, MP3
        type=<n>          — 0=standard, 1=surround

    Returns a dict or None if the packet can't be parsed / has no channel.
    """
    try:
        text = data.decode("utf-8", errors="ignore")
    except Exception:
        return None

    # Split on both NUL bytes and newlines
    pairs = text.replace("\x00", "\n").splitlines()

    fields: dict = {}
    for pair in pairs:
        pair = pair.strip()
        if "=" not in pair:
            continue
        k, _, v = pair.partition("=")
        fields[k.strip().lower()] = v.strip()

    if not fields:
        return None

    # Channel number is required
    try:
        cid = int(fields.get("ch", ""))
    except (ValueError, TypeError):
        return None

    # Multicast address comes directly from the packet's src= field.
    # Fall back to deriving it from the channel ID if absent.
    address = fields.get("src", "") or _lw_ip(cid)

    # Source name: srcn= preferred, fall back to src= (address as label)
    name = fields.get("srcn", "") or fields.get("src", "")

    return {
        "cid":       cid,
        "name":      name,
        "node_name": sender_ip,   # no node-name field in LWAP; use sender IP
        "node_ip":   sender_ip,
        "multicast": address,
        "sample_rate": _safe_int(fields.get("rate", "48000"), 48000),
        "fmt":         fields.get("fmt", ""),
        "src_type":    _safe_int(fields.get("type", "0"), 0),
    }


def _safe_int(s: str, default: int) -> int:
    try:
        return int(s)
    except (TypeError, ValueError):
        return default


# ── Monitor ───────────────────────────────────────────────────────────────────

class _LivewireMonitor:
    """
    Passive Livewire Advertisement Protocol listener.
    Runs on all node types (hub, client, standalone).
    """

    def __init__(self, iface_ip: str, timeout: int, log_fn):
        self.iface_ip    = iface_ip or "0.0.0.0"
        self.timeout     = int(timeout or _DEF_TIMEOUT)
        self.log         = log_fn
        self._sources: dict = {}
        self._lock       = threading.Lock()
        self._stop       = threading.Event()
        self._thread     = None
        # Diagnostics
        self._pkt_rx     = 0    # total UDP packets received
        self._pkt_parsed = 0    # packets that parsed successfully
        self._sock_ok    = None # True/False after open attempt
        self._last_raw   = b""  # raw bytes of most recent packet

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="LWMonitor")
        self._thread.start()
        self.log(f"[Livewire] Monitor started ({_LWAP_GROUP}:{_LWAP_PORT} iface={self.iface_ip})")

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)

    def get_sources(self) -> list:
        """Return sources sorted by node name then channel name."""
        now = time.time()
        with self._lock:
            out = []
            for src in self._sources.values():
                age = now - src["last_seen"]
                out.append({
                    **src,
                    "age_s":  int(age),
                    "status": "online" if age <= self.timeout else "stale",
                })
        out.sort(key=lambda s: (s.get("node_name", "").lower(), s.get("name", "").lower()))
        return out

    def get_stats(self) -> dict:
        now = time.time()
        with self._lock:
            total  = len(self._sources)
            online = sum(1 for s in self._sources.values()
                         if now - s["last_seen"] <= self.timeout)
        return {"total": total, "online": online, "stale": total - online}

    # ── internals ────────────────────────────────────────────────────────────

    def get_debug(self) -> dict:
        """Diagnostic snapshot for /api/livewire/debug."""
        last_hex = self._last_raw[:256].hex() if self._last_raw else ""
        last_txt = ""
        if self._last_raw:
            try:
                last_txt = self._last_raw[:256].decode("utf-8", errors="replace") \
                               .replace("\x00", "·")
            except Exception:
                pass
        return {
            "socket_ok":   self._sock_ok,
            "pkt_rx":      self._pkt_rx,
            "pkt_parsed":  self._pkt_parsed,
            "last_raw_hex": last_hex,
            "last_raw_txt": last_txt,
            "iface_ip":    self.iface_ip,
            "sources":     len(self._sources),
        }

    def _open_socket(self):
        try:
            s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM, _sock.IPPROTO_UDP)
            s.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 1)
            try:
                s.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEPORT, 1)
            except AttributeError:
                pass   # Windows
            s.bind(("0.0.0.0", _LWAP_PORT))
            mreq = struct.pack("4s4s",
                               _sock.inet_aton(_LWAP_GROUP),
                               _sock.inet_aton(self.iface_ip))
            s.setsockopt(_sock.IPPROTO_IP, _sock.IP_ADD_MEMBERSHIP, mreq)
            self._sock_ok = True
            return s
        except Exception as e:
            self._sock_ok = False
            self.log(f"[Livewire] Socket open failed: {e}")
            return None

    def _run(self):
        sock = self._open_socket()
        if sock is None:
            self.log("[Livewire] Multicast socket unavailable — source discovery inactive.")
            return
        while not self._stop.is_set():
            try:
                ready, _, _ = _sel.select([sock], [], [], 1.0)
            except Exception:
                time.sleep(0.5)
                continue
            if ready:
                try:
                    data, addr = sock.recvfrom(4096)
                    self._pkt_rx  += 1
                    self._last_raw = data
                    self._process(data, addr[0])
                except Exception as e:
                    if not self._stop.is_set():
                        self.log(f"[Livewire] recv error: {e}")
        try:
            sock.close()
        except Exception:
            pass

    def _process(self, data: bytes, sender_ip: str):
        p = _parse_lwap(data, sender_ip)
        if not p:
            return
        self._pkt_parsed += 1
        cid = p["cid"]
        now = time.time()
        with self._lock:
            ex = self._sources.get(cid)
            if ex:
                ex["last_seen"] = now
                if p["name"]:
                    ex["name"] = p["name"]
                ex["multicast"] = p["multicast"] or ex["multicast"]
                ex["node_ip"]   = sender_ip
                # Clear stale flag on refresh
                if ex.get("stale"):
                    self.log(f"[Livewire] Source recovered: ID={cid} '{ex['name']}'")
                    ex["stale"] = False
            else:
                self._sources[cid] = {
                    "cid":         cid,
                    "name":        p["name"],
                    "node_name":   sender_ip,
                    "node_ip":     sender_ip,
                    "multicast":   p["multicast"],
                    "sample_rate": p.get("sample_rate", 48000),
                    "fmt":         p.get("fmt", ""),
                    "last_seen":   now,
                    "first_seen":  now,
                    "stale":       False,
                }
                self.log(
                    f"[Livewire] New source: ID={cid} "
                    f"'{p['name']}' @ {p['multicast']}"
                )


# ── Shared CSS ────────────────────────────────────────────────────────────────

_CSS = """
:root{--bg:#07142b;--sur:#0d2346;--bor:#17345f;--acc:#17a8ff;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#eef5ff;--mu:#8aa4c8}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;font-size:13px;background:radial-gradient(circle at top,#12376f 0%,var(--bg) 38%,#05101f 100%);color:var(--tx)}
header{background:linear-gradient(180deg,rgba(10,31,65,.96),rgba(9,24,48,.96));border-bottom:1px solid var(--bor);padding:12px 20px;display:flex;align-items:center;gap:12px}
.btn{display:inline-flex;align-items:center;border:none;border-radius:8px;padding:5px 12px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;text-decoration:none;white-space:nowrap}
.btn:hover{filter:brightness(1.15)}
.bg{background:var(--bor);color:var(--tx)}.bp{background:var(--acc);color:#fff}.bd{background:var(--al);color:#fff}
.bs{padding:3px 9px;font-size:12px}
.nav-active{background:var(--acc)!important;color:#fff!important}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px}
.b-ok{background:#0f2318;color:var(--ok);border:1px solid #166534}
.b-al{background:#2a0a0a;color:var(--al);border:1px solid #991b1b}
.b-mu{background:#0d1e40;color:var(--mu);border:1px solid var(--bor)}
main{padding:24px 20px 48px;max-width:1100px;margin:0 auto}
.ph{margin-bottom:20px}
.ph h1{font-size:22px;font-weight:800;letter-spacing:-.02em}
.ph p{color:var(--mu);margin-top:4px;font-size:12px}
.card{background:var(--sur);border:1px solid var(--bor);border-radius:12px;overflow:hidden;margin-bottom:16px}
.ch{padding:9px 14px;display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--bor);background:linear-gradient(180deg,#143766,#102b54);font-size:12px;font-weight:700;color:var(--acc);text-transform:uppercase;letter-spacing:.06em}
.ch-pills{margin-left:auto;display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.cb{padding:14px}
table{width:100%;border-collapse:collapse}
th{color:var(--mu);font-size:11px;text-transform:uppercase;letter-spacing:.05em;padding:6px 10px;border-bottom:1px solid var(--bor);text-align:left;font-weight:600}
td{padding:8px 10px;border-bottom:1px solid rgba(23,52,95,.35);font-size:13px;vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:rgba(23,52,95,.35)}
.mu{color:var(--mu)} .ts{font-size:11px;color:var(--mu)}
.field{display:flex;flex-direction:column;gap:4px;margin-bottom:12px}
.field label{font-size:11px;color:var(--mu);font-weight:600;text-transform:uppercase;letter-spacing:.05em}
input[type=text],input[type=number]{background:#0d1e40;border:1px solid var(--bor);border-radius:6px;color:var(--tx);padding:6px 9px;font-size:13px;width:100%}
input:focus{outline:none;border-color:var(--acc)}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
#msg{padding:10px 14px;border-radius:8px;margin-bottom:14px;display:none;font-weight:600;font-size:13px}
.msg-ok{background:#0f2318;color:var(--ok);border:1px solid #166534;display:block!important}
.msg-err{background:#2a0a0a;color:var(--al);border:1px solid #991b1b;display:block!important}
.empty{padding:24px;text-align:center;color:var(--mu);font-size:13px}
code{font-family:monospace;font-size:12px;color:var(--mu)}
"""

_SHARED_JS = r"""
function _getCsrf(){
  return (document.querySelector('meta[name="csrf-token"]')||{}).content
      || (document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/)||[])[1]||'';
}
function _showMsg(txt,err){
  var el=document.getElementById('msg');
  el.textContent=txt; el.className=err?'msg-err':'msg-ok';
  setTimeout(function(){el.className='';el.textContent='';},5000);
}
function _fmtAge(s){
  if(s<60) return s+'s ago';
  if(s<3600) return Math.floor(s/60)+'m ago';
  return Math.floor(s/3600)+'h '+Math.floor((s%3600)/60)+'m ago';
}
function _pill(status){
  return '<span class="badge '+(status==='online'?'b-ok':'b-al')+'">'+status+'</span>';
}
function _esc(s){var d=document.createElement('div');d.textContent=s;return d.innerHTML;}
function _escA(s){return s.replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
"""


# ── Hub page template ─────────────────────────────────────────────────────────

_HUB_TPL = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Livewire — SignalScope</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<style nonce="{{csp_nonce()}}">""" + _CSS + r"""</style>
</head><body>
{{topnav("livewire")|safe}}
<main>
  <div class="ph">
    <h1>🔗 Livewire Sources</h1>
    <p>Axia Livewire source discovery — all connected sites</p>
  </div>
  <div id="msg"></div>
  <div id="sites"></div>

  <div class="card">
    <div class="ch">⚙ Configuration</div>
    <div class="cb">
      <p style="font-size:12px;color:var(--mu);margin-bottom:14px">
        This hub does not listen for LWAP multicast. Source data is pushed here by each
        connected client node every {{push_interval}}s. Configure the audio interface and
        stale timeout on each client's Livewire page.
      </p>
      <div class="field">
        <label>Source stale timeout (seconds)</label>
        <input type="number" id="cfg-timeout" min="30" max="3600" value="{{timeout}}" style="max-width:160px">
      </div>
      <button class="btn bp bs" id="cfg-save-btn">Save</button>
    </div>
  </div>
</main>
<script nonce="{{csp_nonce()}}">
""" + _SHARED_JS + r"""
var _isHub = {{is_hub|lower}};

function _renderSites(data){
  var names=Object.keys(data).sort(), html='';
  if(!names.length){
    document.getElementById('sites').innerHTML=
      '<div class="card"><div class="cb"><div class="empty">'
      +'No Livewire data yet — waiting for sites to report in.</div></div></div>';
    return;
  }
  for(var i=0;i<names.length;i++){
    var site=names[i], sd=data[site];
    var srcs=sd.sources||[], on=sd.online||0, st=sd.stale||0;
    var updAge=sd.updated_at?Math.floor(Date.now()/1000-sd.updated_at):null;
    html+='<div class="card">';
    html+='<div class="ch">📡 '+_esc(site);
    html+='<span class="ch-pills">';
    if(on) html+='<span class="badge b-ok">'+on+' online</span> ';
    if(st) html+='<span class="badge b-al">'+st+' stale</span>';
    if(!on&&!st) html+='<span class="badge b-mu">no sources</span>';
    if(updAge!==null) html+='<span class="ts" style="margin-left:8px">updated '+_fmtAge(updAge)+'</span>';
    html+='</span></div>';
    html+='<div class="cb">';
    if(!srcs.length){
      html+='<div class="empty">No sources discovered on this site yet.</div>';
    } else {
      html+='<table><thead><tr>'
           +'<th>Node</th><th>Stream ID</th><th>Friendly Name</th>'
           +'<th>Multicast</th><th>Last Seen</th><th>Status</th><th></th>'
           +'</tr></thead><tbody>';
      for(var j=0;j<srcs.length;j++){
        var s=srcs[j];
        var local=sd._local?'1':'0';
        html+='<tr>'
             +'<td>'+_esc(s.node_name||'')+' <div class="ts mu">'+_esc(s.node_ip||'')+'</div></td>'
             +'<td><code>'+s.cid+'</code></td>'
             +'<td>'+_esc(s.name||'')+'</td>'
             +'<td><code>'+_esc(s.multicast||'')+'</code></td>'
             +'<td class="ts">'+_fmtAge(s.age_s||0)+'</td>'
             +'<td>'+_pill(s.status||'stale')+'</td>'
             +'<td>'
             +'<button class="btn bp bs lw-add-btn"'
             +' data-site="'+_escA(site)+'"'
             +' data-local="'+local+'"'
             +' data-cid="'+s.cid+'"'
             +' data-name="'+_escA(s.name||('LW-'+s.cid))+'"'
             +'>+ Input</button>'
             +'</td></tr>';
      }
      html+='</tbody></table>';
    }
    html+='</div></div>';
  }
  document.getElementById('sites').innerHTML=html;
}

document.addEventListener('click',function(e){
  var btn=e.target.closest('.lw-add-btn');
  if(!btn) return;
  var site=btn.dataset.site, cid=parseInt(btn.dataset.cid,10);
  var name=btn.dataset.name, local=btn.dataset.local==='1';
  btn.disabled=true;
  var csrf=_getCsrf(), url, body;
  if(local||!_isHub){
    url='/inputs/add_dab_bulk';
    body=JSON.stringify({services:[{name:name,device_index:String(cid),stereo:true}]});
  } else {
    url='/api/hub/site/'+encodeURIComponent(site)+'/input/add';
    body=JSON.stringify({name:name,device_index:String(cid),stereo:true,
                         alert_on_silence:true,alert_on_clip:true});
  }
  fetch(url,{method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRFToken':csrf},body:body})
  .then(function(r){return r.json();})
  .then(function(d){
    btn.disabled=false;
    if(d.ok||d.added){
      _showMsg('"'+name+'" '+(local||!_isHub
        ?'added to inputs.'
        :'queued for '+site+' — active on next heartbeat.'),false);
    } else {
      _showMsg('Error: '+(d.error||JSON.stringify(d)),true);
    }
  }).catch(function(e){btn.disabled=false;_showMsg('Request failed: '+e,true);});
});

document.getElementById('cfg-save-btn').addEventListener('click',function(){
  var csrf=_getCsrf();
  fetch('/api/livewire/config',{method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRFToken':csrf},
    body:JSON.stringify({source_timeout:parseInt(document.getElementById('cfg-timeout').value)||300})
  }).then(function(r){return r.json();})
  .then(function(d){_showMsg(d.ok?'Config saved.':'Error: '+(d.error||'?'),!d.ok);});
});

function _refresh(){
  fetch('/api/livewire/data',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){_renderSites(d);})
    .catch(function(){});
}
_refresh();
setInterval(_refresh,30000);
</script></body></html>"""


# ── Client / standalone page template ─────────────────────────────────────────

_CLIENT_TPL = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Livewire — SignalScope</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{{csrf_token()}}">
<style nonce="{{csp_nonce()}}">""" + _CSS + r"""</style>
</head><body>
{{topnav("livewire")|safe}}
<main>
  <div class="ph">
    <h1>🔗 Livewire Sources</h1>
    <p>Local Axia Livewire source discovery — <strong>{{site_name}}</strong></p>
  </div>
  <div id="msg"></div>
  <div id="local-tbl"></div>

  <div class="card">
    <div class="ch">⚙ Configuration</div>
    <div class="cb">
      <div class="field" style="margin-bottom:14px">
        <label>Audio interface (multicast reception)</label>
        <div style="display:flex;align-items:center;gap:10px">
          <code style="font-size:13px;color:var(--tx)">{{iface_ip}}</code>
          <a href="/settings#network" class="btn bg bs">Change in Settings ↗</a>
        </div>
        <p style="font-size:11px;color:var(--mu);margin-top:4px">Set under Settings → Hub &amp; Network → Audio interface IP. A restart is required after changing it.</p>
      </div>
      <div class="field">
        <label>Source stale timeout (seconds)</label>
        <input type="number" id="cfg-timeout" min="30" max="3600" value="{{timeout}}" style="max-width:160px">
      </div>
      <button class="btn bp bs" id="cfg-save-btn">Save</button>
    </div>
  </div>
</main>
<script nonce="{{csp_nonce()}}">
""" + _SHARED_JS + r"""
function _renderLocal(srcs){
  var on=srcs.filter(function(s){return s.status==='online';}).length;
  var st=srcs.filter(function(s){return s.status==='stale';}).length;
  var html='<div class="card">';
  html+='<div class="ch">📡 Local sources';
  html+='<span class="ch-pills">';
  if(on) html+='<span class="badge b-ok">'+on+' online</span> ';
  if(st) html+='<span class="badge b-al">'+st+' stale</span>';
  if(!on&&!st) html+='<span class="badge b-mu">no sources</span>';
  html+='</span></div>';
  html+='<div class="cb">';
  if(!srcs.length){
    html+='<div class="empty">No sources discovered yet. '
         +'Listening on {{_LWAP_GROUP}}:{{_LWAP_PORT}} — '
         +'check that this machine can receive Livewire multicast.</div>';
  } else {
    html+='<table><thead><tr>'
         +'<th>Node</th><th>Stream ID</th><th>Friendly Name</th>'
         +'<th>Multicast</th><th>Last Seen</th><th>Status</th><th></th>'
         +'</tr></thead><tbody>';
    for(var j=0;j<srcs.length;j++){
      var s=srcs[j];
      html+='<tr>'
           +'<td>'+_esc(s.node_name||'')+' <div class="ts mu">'+_esc(s.node_ip||'')+'</div></td>'
           +'<td><code>'+s.cid+'</code></td>'
           +'<td>'+_esc(s.name||'')+'</td>'
           +'<td><code>'+_esc(s.multicast||'')+'</code></td>'
           +'<td class="ts">'+_fmtAge(s.age_s||0)+'</td>'
           +'<td>'+_pill(s.status||'stale')+'</td>'
           +'<td>'
           +'<button class="btn bp bs lw-add-btn"'
           +' data-cid="'+s.cid+'"'
           +' data-name="'+_escA(s.name||('LW-'+s.cid))+'"'
           +'>+ Input</button>'
           +'</td></tr>';
    }
    html+='</tbody></table>';
  }
  html+='</div></div>';
  document.getElementById('local-tbl').innerHTML=html;
}

document.addEventListener('click',function(e){
  var btn=e.target.closest('.lw-add-btn');
  if(!btn) return;
  var cid=parseInt(btn.dataset.cid,10), name=btn.dataset.name;
  btn.disabled=true;
  var csrf=_getCsrf();
  fetch('/inputs/add_dab_bulk',{method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRFToken':csrf},
    body:JSON.stringify({services:[{name:name,device_index:String(cid),stereo:true}]})
  }).then(function(r){return r.json();})
  .then(function(d){
    btn.disabled=false;
    if(d.ok||d.added) _showMsg('"'+name+'" added to inputs.',false);
    else _showMsg('Error: '+(d.error||JSON.stringify(d)),true);
  }).catch(function(e){btn.disabled=false;_showMsg('Request failed: '+e,true);});
});

document.getElementById('cfg-save-btn').addEventListener('click',function(){
  var csrf=_getCsrf();
  fetch('/api/livewire/config',{method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRFToken':csrf},
    body:JSON.stringify({
      audio_iface:document.getElementById('cfg-iface').value.trim()||'0.0.0.0',
      source_timeout:parseInt(document.getElementById('cfg-timeout').value)||300
    })
  }).then(function(r){return r.json();})
  .then(function(d){_showMsg(d.ok?'Config saved.':'Error: '+(d.error||'?'),!d.ok);});
});

function _refresh(){
  fetch('/api/livewire/sources_local',{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){_renderLocal(d.sources||[]);})
    .catch(function(){});
}
_refresh();
setInterval(_refresh,15000);
</script></body></html>"""


# ── register ──────────────────────────────────────────────────────────────────

def register(app, ctx):
    global _lw_monitor, _hub_data

    login_required = ctx["login_required"]
    csrf_protect   = ctx["csrf_protect"]
    monitor_ref    = ctx["monitor"]
    hub_server     = ctx["hub_server"]

    cfg_ss    = monitor_ref.app_cfg
    mode      = getattr(getattr(cfg_ss, "hub", None), "mode", "standalone") or "standalone"
    hub_url   = (getattr(getattr(cfg_ss, "hub", None), "hub_url",   "") or "").rstrip("/")
    site_name = (getattr(getattr(cfg_ss, "hub", None), "site_name", "") or _sock.gethostname())

    is_hub    = mode in ("hub", "both")
    is_client = mode == "client" and bool(hub_url)

    # Always use the system audio interface (Settings → Hub & Network → Audio interface IP).
    # This ensures the multicast group is joined on the correct Livewire NIC.
    iface_ip = (getattr(getattr(cfg_ss, "network", None), "audio_interface_ip", "") or "0.0.0.0")

    pcfg    = _load_cfg()
    timeout = int(pcfg.get("source_timeout", _DEF_TIMEOUT))

    # Load persisted hub data if we're holding it
    if is_hub:
        _load_hub_data()

    # Start the LWAP monitor only on nodes that are on the Livewire network
    # (client, standalone, or "both").  Pure hub nodes receive source data
    # from clients via /api/livewire/report — they never join the multicast group.
    if _lw_monitor is not None:
        _lw_monitor.stop()
    if mode != "hub":
        _lw_monitor = _LivewireMonitor(iface_ip, timeout, monitor_ref.log)
        _lw_monitor.start()
    else:
        _lw_monitor = None
        monitor_ref.log("[Livewire] Hub mode — LWAP listener not started (display-only)")

    # Client → hub pusher thread
    if is_client:
        def _pusher():
            import hashlib, hmac as _hmac
            while True:
                time.sleep(_PUSH_INTERVAL)
                try:
                    secret  = (getattr(getattr(cfg_ss, "hub", None), "secret_key", "") or "")
                    sources = _lw_monitor.get_sources()
                    payload = json.dumps({"site": site_name, "sources": sources}).encode()
                    ts      = time.time()
                    sig     = ""
                    if secret:
                        key = hashlib.sha256(f"{secret}:signing".encode()).digest()
                        msg = f"{ts:.0f}:".encode() + payload
                        sig = _hmac.new(key, msg, hashlib.sha256).hexdigest()
                    req = urllib.request.Request(
                        f"{hub_url}/api/livewire/report",
                        data=payload, method="POST",
                        headers={
                            "Content-Type": "application/json",
                            "X-Site":       site_name,
                            "X-Hub-Sig":    sig,
                            "X-Hub-Ts":     f"{ts:.0f}",
                        },
                    )
                    urllib.request.urlopen(req, timeout=10).close()
                except Exception as e:
                    monitor_ref.log(f"[Livewire] Push to hub failed: {e}")

        threading.Thread(target=_pusher, daemon=True, name="LWPusher").start()

    # ── Routes ────────────────────────────────────────────────────────────────

    @app.get("/livewire")
    @login_required
    def livewire_page():
        """Client/standalone view — redirects to hub overview when in hub/both mode."""
        if mode in ("hub", "both"):
            return redirect("/hub/livewire")
        pcfg2 = _load_cfg()
        return render_template_string(
            _CLIENT_TPL,
            site_name=site_name,
            iface_ip=iface_ip,
            timeout=int(pcfg2.get("source_timeout", _DEF_TIMEOUT)),
            _LWAP_GROUP=_LWAP_GROUP,
            _LWAP_PORT=_LWAP_PORT,
        )

    @app.get("/hub/livewire")
    @login_required
    def livewire_hub_page():
        """Hub overview — all sites' source tables."""
        pcfg2 = _load_cfg()
        return render_template_string(
            _HUB_TPL,
            timeout=int(pcfg2.get("source_timeout", _DEF_TIMEOUT)),
            is_hub=is_hub,
            push_interval=_PUSH_INTERVAL,
        )

    @app.post("/api/livewire/report")
    def livewire_report():
        """
        Client → Hub: receive a site's source table.
        Validates site approval and optional HMAC before storing.
        """
        site = request.headers.get("X-Site", "").strip()
        if not site:
            return jsonify({"error": "missing X-Site header"}), 400

        sdata = hub_server._sites.get(site, {})
        if not sdata.get("_approved"):
            return jsonify({"error": "site not approved"}), 403

        secret = (getattr(getattr(cfg_ss, "hub", None), "secret_key", "") or "")
        if secret:
            import hashlib, hmac as _hmac
            raw_body = request.get_data()
            ts_hdr   = request.headers.get("X-Hub-Ts", "0")
            sig_hdr  = request.headers.get("X-Hub-Sig", "")
            key      = hashlib.sha256(f"{secret}:signing".encode()).digest()
            msg      = f"{ts_hdr}:".encode() + raw_body
            expected = _hmac.new(key, msg, hashlib.sha256).hexdigest()
            if not _hmac.compare_digest(sig_hdr, expected):
                return jsonify({"error": "bad signature"}), 403
            data = json.loads(raw_body)
        else:
            data = request.get_json(silent=True) or {}

        sources = data.get("sources", [])
        with _hub_lock:
            _hub_data[site] = {"sources": sources, "updated_at": time.time()}
            _save_hub_data()

        return jsonify({"ok": True, "count": len(sources)})

    @app.get("/api/livewire/data")
    @login_required
    def livewire_data():
        """
        JSON: all sites' source data for the hub overview page.
        Also includes this node's own live sources when hub/both/standalone.
        """
        now      = time.time()
        tout     = _lw_monitor.timeout if _lw_monitor else _DEF_TIMEOUT
        out      = {}

        # Remote client data stored from reports
        with _hub_lock:
            for s, sd in _hub_data.items():
                srcs = sd.get("sources", [])
                # Recompute status against current time
                for src in srcs:
                    age = now - src.get("last_seen", 0)
                    src["status"] = "online" if age <= tout else "stale"
                    src["age_s"]  = int(age)
                out[s] = {
                    "sources":    srcs,
                    "updated_at": sd.get("updated_at", 0),
                    "online":     sum(1 for x in srcs if x["status"] == "online"),
                    "stale":      sum(1 for x in srcs if x["status"] == "stale"),
                }

        # Own local sources (both/standalone only — pure hub never listens)
        if mode in ("both", "standalone") and _lw_monitor:
            own      = _lw_monitor.get_sources()
            own_name = site_name or "(hub)"
            # Don't overwrite a remote-reported entry for the same site name
            if own_name not in out or out[own_name].get("_local"):
                out[own_name] = {
                    "sources":    own,
                    "updated_at": now,
                    "online":     sum(1 for x in own if x["status"] == "online"),
                    "stale":      sum(1 for x in own if x["status"] == "stale"),
                    "_local":     True,
                }

        return jsonify(out)

    @app.get("/api/livewire/sources_local")
    @login_required
    def livewire_sources_local():
        """JSON: this node's locally discovered sources (used by client page and input dropdowns)."""
        sources = _lw_monitor.get_sources() if _lw_monitor else []
        stats   = _lw_monitor.get_stats()   if _lw_monitor else {}
        return jsonify({"sources": sources, "stats": stats})

    @app.get("/api/livewire/debug")
    @login_required
    def livewire_debug():
        """Diagnostic endpoint — shows socket state, packet counts, last raw packet."""
        if _lw_monitor:
            d = _lw_monitor.get_debug()
        else:
            d = {"socket_ok": None, "pkt_rx": 0, "pkt_parsed": 0,
                 "last_raw_hex": "", "last_raw_txt": "", "iface_ip": iface_ip, "sources": 0}
        d["mode"]     = mode
        d["hub_url"]  = hub_url
        d["site"]     = site_name
        return jsonify(d)

    @app.post("/api/livewire/config")
    @login_required
    @csrf_protect
    def livewire_config_save():
        """Save plugin configuration: source_timeout only.
        Audio interface is always read from Settings → Hub & Network."""
        data        = request.get_json(silent=True) or {}
        timeout_new = max(30, min(3600, int(data.get("source_timeout", _DEF_TIMEOUT) or _DEF_TIMEOUT)))
        _save_cfg({"source_timeout": timeout_new})
        if _lw_monitor:
            _lw_monitor.timeout = timeout_new
        return jsonify({"ok": True})
