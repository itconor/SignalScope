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
    "version": "1.1.1",
}

import json, os, struct, sys, threading, time, urllib.request, uuid
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
_alert_cfg  = {}       # live copy of alert config, updated on save


# ── Helpers ───────────────────────────────────────────────────────────────────

def _lw_ip(cid: int) -> str:
    """Derive the RTP multicast IP from a Livewire stream ID."""
    return f"239.192.{(cid >> 8) & 0xFF}.{cid & 0xFF}"

def _load_cfg() -> dict:
    global _alert_cfg
    try:
        with open(_CFG_PATH) as f:
            d = json.load(f)
            _alert_cfg = d
            return d
    except Exception:
        return {}

def _save_cfg(d: dict):
    global _alert_cfg
    existing = _load_cfg()
    existing.update(d)
    _alert_cfg = existing
    with open(_CFG_PATH, "w") as f:
        json.dump(existing, f, indent=2)

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


# ── Binary LWAP TLV parser ────────────────────────────────────────────────────
#
# Axia Livewire nodes send binary TLV packets to 239.192.255.3:4001.
#
# Packet layout (reference: github.com/nick-prater/read_lw_sources):
#   16-byte header  [0x03 0x00 0x02 0x07 | counter(4) | padding(8)]
#   Sequence of phrases:  [4-byte ASCII opcode] [1-byte type] [N-byte value]
#
# Type codes:
#   0x00 / 0x07  →  1 byte  (uint8)
#   0x01         →  4 bytes (uint32 big-endian OR IPv4 address)
#   0x03         →  2-byte length prefix + N bytes of string (NUL-terminated)
#   0x06 / 0x08  →  2 bytes (uint16 big-endian)
#   0x09         →  8 bytes (padding, ignored)
#
# Key opcodes:
#   ADVT  advertisement type  0x01=full (has sources), 0x02=summary (heartbeat only)
#   INIP  node IPv4 address
#   ATRN  node display name (string)
#   PSID  Livewire channel number (uint32)
#   FSID  source multicast IPv4 address
#   PSNM  source display name (string)
#
# Type 2 (ADVT=0x02) summary packets carry no source data — they are frequent
# heartbeats confirming a node is alive.  Type 1 (ADVT=0x01) full packets
# contain complete source sections and are sent on startup, on config change,
# and periodically (typically every 30–60 seconds).

def _parse_lwap(data: bytes, sender_ip: str):
    """
    Parse a binary Axia LWAP TLV packet.

    Returns a dict:
        {
          "adv_type":  1 (full) or 2 (summary),
          "node_ip":   "10.x.x.x",
          "node_name": "StA-Node1",
          "sources":   [{"cid": 6031, "name": "PGM1", "multicast": "239.192.23.143"}, ...]
        }
    Returns None if the packet is not a valid binary LWAP packet.
    """
    if len(data) < 20 or data[0] != 0x03:
        return None

    pos       = 16   # skip 16-byte header
    adv_type  = 0
    node_ip   = sender_ip
    node_name = sender_ip
    sources   = []
    cur_src   = None   # source being assembled (set when PSID seen)

    while pos + 5 <= len(data):
        # ── read opcode ───────────────────────────────────────────────────
        try:
            opcode = data[pos:pos+4].decode("ascii")
        except Exception:
            break
        typ  = data[pos+4]
        pos += 5

        # ── read value ────────────────────────────────────────────────────
        val_bytes = None
        val_int   = None

        if typ in (0x00, 0x07):          # 1-byte integer
            if pos + 1 > len(data): break
            val_int   = data[pos]
            val_bytes = data[pos:pos+1]
            pos += 1
        elif typ == 0x01:                # 4-byte (IP or uint32)
            if pos + 4 > len(data): break
            val_bytes = data[pos:pos+4]
            val_int   = int.from_bytes(val_bytes, "big")
            pos += 4
        elif typ == 0x03:                # length-prefixed string
            if pos + 2 > len(data): break
            slen = (data[pos] << 8) | data[pos+1]
            pos += 2
            if pos + slen > len(data): break
            val_bytes = data[pos:pos+slen]
            pos += slen
        elif typ in (0x06, 0x08):        # 2-byte integer
            if pos + 2 > len(data): break
            val_bytes = data[pos:pos+2]
            val_int   = (data[pos] << 8) | data[pos+1]
            pos += 2
        elif typ == 0x09:                # 8-byte padding
            pos += 8
            continue
        else:
            break   # unknown type — stop

        # ── dispatch ──────────────────────────────────────────────────────
        if opcode == "ADVT":
            adv_type = val_int or 0

        elif opcode == "INIP" and val_bytes and len(val_bytes) == 4:
            node_ip = ".".join(str(b) for b in val_bytes)

        elif opcode == "ATRN" and val_bytes:
            # NUL-terminated string; strip padding
            name = val_bytes.split(b"\x00")[0].decode("latin-1", errors="ignore").strip()
            if name:
                node_name = name

        elif opcode == "PSID" and val_bytes and len(val_bytes) == 4:
            cid = int.from_bytes(val_bytes, "big")
            if cid > 0:
                cur_src = {"cid": cid, "name": f"Source {cid}", "multicast": _lw_ip(cid)}
                sources.append(cur_src)

        elif opcode == "FSID" and val_bytes and len(val_bytes) == 4 and cur_src:
            mc = ".".join(str(b) for b in val_bytes)
            if mc not in ("0.0.0.0", "239.192.0.0"):
                cur_src["multicast"] = mc

        elif opcode == "PSNM" and val_bytes and cur_src:
            name = val_bytes.split(b"\x00")[0].decode("latin-1", errors="ignore").strip()
            if name:
                cur_src["name"] = name

    return {"adv_type": adv_type, "node_ip": node_ip, "node_name": node_name, "sources": sources}


# ── Alert helpers ─────────────────────────────────────────────────────────────

def _fire_lw_stale_alert(log_fn, node_name: str, cid: int, source_name: str, age_s: int):
    """
    Fire a LW_STALE alert through the configured channels.
    Uses the same sys.modules pattern as azuracast.py to avoid circular imports.
    """
    subject = f"Livewire stale: {source_name} (ch {cid})"
    body    = (f"Node: {node_name}\n"
               f"Source: {source_name} (channel {cid})\n"
               f"Last seen: {age_s}s ago")

    # 1. Append to SignalScope alert log (shows in Hub Reports)
    for mod in sys.modules.values():
        if hasattr(mod, "_alert_log_append") and hasattr(mod, "hub_reports"):
            try:
                mod._alert_log_append({
                    "id":            str(uuid.uuid4()),
                    "ts":            time.strftime("%Y-%m-%d %H:%M:%S"),
                    "stream":        source_name,
                    "type":          "LW_STALE",
                    "message":       body,
                    "level_dbfs":    None,
                    "rtp_loss_pct":  None,
                    "rtp_jitter_ms": None,
                    "clip":          "",
                    "ptp_state":     "",
                    "ptp_offset_us": 0,
                    "ptp_drift_us":  0,
                    "ptp_jitter_us": 0,
                    "ptp_gm":        "",
                })
            except Exception as e:
                log_fn(f"[Livewire] Alert log error: {e}")
            break

    # 2. Send notifications through selected channels
    acfg = _alert_cfg
    for mod in sys.modules.values():
        if hasattr(mod, "AlertSender") and hasattr(mod, "hub_reports"):
            try:
                # We need monitor.app_cfg; find it via the AppMonitor singleton
                for m2 in sys.modules.values():
                    if hasattr(m2, "AppMonitor"):
                        monitor_inst = getattr(m2, "_monitor_instance", None)
                        if monitor_inst is None:
                            break
                        cfg = monitor_inst.app_cfg
                        sender = mod.AlertSender(cfg, log_fn)
                        if acfg.get("alert_email", True):
                            sender._email(subject, body, None)
                        if acfg.get("alert_webhook", True):
                            sender._webhook(subject, body,
                                            alert_type="LW_STALE",
                                            stream=source_name)
                        if acfg.get("alert_push", True):
                            sender._pushover(subject, body, None)
                        break
            except Exception as e:
                log_fn(f"[Livewire] Notification error: {e}")
            break


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
        self._nodes:   dict = {}   # node_ip → {last_seen, name}
        self._lock       = threading.Lock()
        self._stop       = threading.Event()
        self._thread     = None
        # Stale alert tracking
        self._stale_alerted: set = set()   # cids already alerted
        self._last_alert_check   = 0.0
        # Diagnostics
        self._pkt_rx        = 0
        self._pkt_parsed    = 0
        self._pkt_type1     = 0
        self._sock_ok       = None
        self._last_raw      = b""
        self._last_adv_type = 0

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
        """Return sources sorted by node name then channel ID."""
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
        out.sort(key=lambda s: (s.get("node_name", "").lower(), s.get("cid", 0)))
        return out

    def get_stats(self) -> dict:
        now = time.time()
        with self._lock:
            total  = len(self._sources)
            online = sum(1 for s in self._sources.values()
                         if now - s["last_seen"] <= self.timeout)
            nodes  = len(self._nodes)
        return {"total": total, "online": online, "stale": total - online, "nodes": nodes}

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
        with self._lock:
            nodes = {ip: n.get("name", ip) for ip, n in self._nodes.items()}
        return {
            "socket_ok":     self._sock_ok,
            "pkt_rx":        self._pkt_rx,
            "pkt_parsed":    self._pkt_parsed,
            "pkt_type1":     self._pkt_type1,
            "last_adv_type": self._last_adv_type,
            "last_raw_hex":  last_hex,
            "last_raw_txt":  last_txt,
            "iface_ip":      self.iface_ip,
            "nodes":         nodes,
            "sources":       len(self._sources),
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
        self._last_alert_check = time.time()
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
            # Periodic stale alert check every 60 seconds
            now = time.time()
            if now - self._last_alert_check >= 60.0:
                self._check_stale_alerts(now)
                self._last_alert_check = now
        try:
            sock.close()
        except Exception:
            pass

    def _process(self, data: bytes, sender_ip: str):
        p = _parse_lwap(data, sender_ip)
        if p is None:
            return

        self._pkt_parsed    += 1
        self._last_adv_type  = p["adv_type"]
        if p["adv_type"] == 1:
            self._pkt_type1 += 1
        node_ip   = p["node_ip"]
        node_name = p["node_name"]
        now       = time.time()

        with self._lock:
            # Always update node last-seen (both type 1 and type 2 packets)
            if node_ip not in self._nodes:
                self.log(f"[Livewire] Node online: {node_name} ({node_ip})")
            self._nodes[node_ip] = {"last_seen": now, "name": node_name}

            # Only type 1 (full advertisement) packets carry source data
            if p["adv_type"] == 1 and p["sources"]:
                # Replace all sources previously known from this node
                for cid in [c for c, s in self._sources.items()
                             if s.get("node_ip") == node_ip]:
                    del self._sources[cid]

                for src in p["sources"]:
                    cid   = src["cid"]
                    first = self._sources[cid]["first_seen"] \
                            if cid in self._sources else now
                    self._sources[cid] = {
                        "cid":        cid,
                        "name":       src["name"],
                        "node_name":  node_name,
                        "node_ip":    node_ip,
                        "multicast":  src["multicast"],
                        "last_seen":  now,
                        "first_seen": first,
                    }
                    # Clear stale alert flag if recovered
                    self._stale_alerted.discard(cid)
                self.log(
                    f"[Livewire] {node_name} ({node_ip}): "
                    f"{len(p['sources'])} sources"
                )

    def _check_stale_alerts(self, now: float):
        """Fire LW_STALE alerts for newly stale sources (once per stale episode)."""
        if not _alert_cfg.get("alert_on_stale"):
            return
        with self._lock:
            sources_snap = list(self._sources.items())
        for cid, src in sources_snap:
            age = now - src["last_seen"]
            is_stale = age > self.timeout
            if is_stale and cid not in self._stale_alerted:
                self._stale_alerted.add(cid)
                self.log(
                    f"[Livewire] Stale alert: ch={cid} '{src['name']}' "
                    f"({int(age)}s since last seen)"
                )
                try:
                    _fire_lw_stale_alert(
                        self.log,
                        src.get("node_name", ""),
                        cid,
                        src.get("name", f"ch {cid}"),
                        int(age),
                    )
                except Exception as e:
                    self.log(f"[Livewire] Alert fire error: {e}")
            elif not is_stale:
                self._stale_alerted.discard(cid)


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
/* Search bar */
.lw-search-bar{margin-bottom:16px;display:flex;gap:10px;align-items:center}
.lw-search{flex:1;max-width:400px;background:#0d1e40;border:1px solid var(--bor);border-radius:8px;color:var(--tx);padding:8px 12px;font-size:13px}
.lw-search:focus{outline:none;border-color:var(--acc)}
.lw-search-count{font-size:12px;color:var(--mu)}
/* Node accordion */
.lw-node{border-bottom:1px solid var(--bor)}
.lw-node:last-child{border-bottom:none}
.node-hdr{padding:10px 14px;display:flex;align-items:center;gap:8px;cursor:pointer;background:rgba(14,34,66,.4);user-select:none;transition:background .15s}
.node-hdr:hover{background:rgba(23,52,95,.5)}
.node-name{font-weight:700;font-size:13px}
.node-ip{font-size:11px;color:var(--mu);margin-left:4px;font-family:monospace}
.node-arrow{margin-left:auto;font-size:11px;color:var(--mu);transition:transform .2s;flex-shrink:0}
.node-open>.node-hdr .node-arrow{transform:rotate(90deg)}
.node-body{display:none;overflow:auto}
.node-open>.node-body{display:block}
/* Sortable columns */
th.sortable{cursor:pointer;user-select:none;white-space:nowrap}
th.sortable:hover{color:var(--acc)}
th.sort-asc::after{content:' ▲';color:var(--acc)}
th.sort-desc::after{content:' ▼';color:var(--acc)}
/* Alert config */
.alert-channel-label{display:flex;align-items:center;gap:8px;cursor:pointer;margin-bottom:8px;font-size:13px;font-weight:normal}
.alert-channel-label input{width:auto}
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
function _esc(s){var d=document.createElement('div');d.textContent=String(s);return d.innerHTML;}
function _escA(s){return String(s).replace(/"/g,'&quot;').replace(/'/g,'&#39;');}

/* ── Node grouping ─────────────────────────────────────────────────────── */
function _cmpIp(a,b){
  var ap=(a||'0.0.0.0').split('.').map(Number);
  var bp=(b||'0.0.0.0').split('.').map(Number);
  for(var i=0;i<4;i++){if(ap[i]!==bp[i])return ap[i]-bp[i];}
  return 0;
}

function _groupNodes(srcs){
  var nodes={}, order=[];
  for(var i=0;i<srcs.length;i++){
    var s=srcs[i];
    var key=(s.node_ip||'')+'||'+(s.node_name||'');
    if(!nodes[key]){
      nodes[key]={name:s.node_name||s.node_ip||'Unknown',ip:s.node_ip||'',sources:[]};
      order.push(key);
    }
    nodes[key].sources.push(s);
  }
  order.sort(function(a,b){return _cmpIp(nodes[a].ip,nodes[b].ip);});
  return {nodes:nodes,order:order};
}

/* ── Sort state ────────────────────────────────────────────────────────── */
var _sortState={};  /* nodeKey → {col,dir} */

function _sortSources(srcs,col,dir){
  var sorted=srcs.slice();
  sorted.sort(function(a,b){
    var av=a[col], bv=b[col];
    if(typeof av==='string') av=av.toLowerCase();
    if(typeof bv==='string') bv=bv.toLowerCase();
    if(av<bv) return dir==='asc'?-1:1;
    if(av>bv) return dir==='asc'?1:-1;
    return 0;
  });
  return sorted;
}

/* ── Accordion toggle + persistent open state ──────────────────────────── */
var _nodeOpenState={};  /* nodeKey → bool, persists across refreshes */

function _toggleNode(hdr){
  var node=hdr.parentElement;
  node.classList.toggle('node-open');
  var key=node.dataset.nodeKey;
  if(key) _nodeOpenState[key]=node.classList.contains('node-open');
}

/* ── Search ────────────────────────────────────────────────────────────── */
function _applySearch(q){
  q=q.toLowerCase().trim();
  var total=0, shown=0;
  document.querySelectorAll('.lw-src-row').forEach(function(r){
    total++;
    var txt=(r.dataset.search||'').toLowerCase();
    var vis=!q||txt.indexOf(q)!==-1;
    r.style.display=vis?'':'none';
    if(vis) shown++;
  });
  /* Show/hide entire node blocks if all their rows are hidden */
  document.querySelectorAll('.lw-node').forEach(function(n){
    if(!q){n.style.display='';return;}
    var hasVis=false;
    n.querySelectorAll('.lw-src-row').forEach(function(r){if(r.style.display!=='none')hasVis=true;});
    /* Also match node name/ip itself */
    var nHdr=n.querySelector('.node-hdr');
    var nTxt=nHdr?(nHdr.textContent||'').toLowerCase():'';
    if(!hasVis&&nTxt.indexOf(q)!==-1){
      n.querySelectorAll('.lw-src-row').forEach(function(r){r.style.display='';});
      hasVis=true;
    }
    n.style.display=hasVis?'':'none';
  });
  /* Also hide empty site cards on hub */
  document.querySelectorAll('.lw-site-card').forEach(function(c){
    if(!q){c.style.display='';return;}
    var hasVis=false;
    c.querySelectorAll('.lw-node').forEach(function(n){if(n.style.display!=='none')hasVis=true;});
    c.style.display=hasVis?'':'none';
  });
  var cnt=document.getElementById('lw-search-count');
  if(cnt) cnt.textContent=q?(shown+' result'+(shown!==1?'s':'')):'';
}

/* ── Render rows for a node ────────────────────────────────────────────── */
function _renderRows(tbodyId, srcs, showAdd, site, isLocal){
  var tbody=document.getElementById(tbodyId);
  if(!tbody) return;
  var html='';
  for(var j=0;j<srcs.length;j++){
    var s=srcs[j];
    var searchTxt=[s.node_name||'',s.node_ip||'',s.name||'',String(s.cid),s.multicast||''].join(' ').toLowerCase();
    html+='<tr class="lw-src-row" data-search="'+_escA(searchTxt)+'">';
    html+='<td><code>'+s.cid+'</code></td>';
    html+='<td>'+_esc(s.name||'')+'</td>';
    html+='<td><code>'+_esc(s.multicast||'')+'</code></td>';
    html+='<td class="ts">'+_fmtAge(s.age_s||0)+'</td>';
    html+='<td>'+_pill(s.status||'stale')+'</td>';
    if(showAdd){
      html+='<td>'
           +'<button class="btn bp bs lw-add-btn"'
           +' data-site="'+_escA(site||'')+'"'
           +' data-local="'+(isLocal?'1':'0')+'"'
           +' data-cid="'+s.cid+'"'
           +' data-name="'+_escA(s.name||('LW-'+s.cid))+'"'
           +'>+ Input</button>'
           +'</td>';
    }
    html+='</tr>';
  }
  tbody.innerHTML=html;
}

/* ── Render accordion nodes for a source list ──────────────────────────── */
function _renderNodeAccordion(containerId, srcs, showAdd, site, isLocal, expandFirst){
  var el=document.getElementById(containerId);
  if(!el) return;
  if(!srcs||!srcs.length){
    el.innerHTML='<div class="empty">No sources discovered yet.</div>';
    return;
  }
  var grp=_groupNodes(srcs), html='';
  for(var ni=0;ni<grp.order.length;ni++){
    var key=grp.order[ni], nd=grp.nodes[key];
    var on=nd.sources.filter(function(s){return s.status==='online';}).length;
    var st=nd.sources.filter(function(s){return s.status==='stale';}).length;
    var tbId='tb-'+btoa(key).replace(/[^a-zA-Z0-9]/g,'').slice(0,16)+'_'+ni;
    /* Use persisted state; default first node open only on very first render */
    if(!(key in _nodeOpenState)) _nodeOpenState[key]=(ni===0);
    var openCls=_nodeOpenState[key]?' node-open':'';
    html+='<div class="lw-node'+openCls+'" data-node-key="'+_escA(key)+'">';
    html+='<div class="node-hdr" onclick="_toggleNode(this)">';
    html+='<span class="node-name">'+_esc(nd.name)+'</span>';
    html+='<span class="node-ip">'+_esc(nd.ip)+'</span>';
    html+='<span class="ch-pills" style="margin-left:12px">';
    if(on) html+='<span class="badge b-ok">'+on+' online</span>';
    if(st) html+='<span class="badge b-al">'+st+' stale</span>';
    if(!on&&!st) html+='<span class="badge b-mu">no sources</span>';
    html+='</span>';
    html+='<span class="node-arrow">▶</span>';
    html+='</div>';
    html+='<div class="node-body">';
    html+='<table><thead><tr>';
    html+='<th class="sortable" data-col="cid" data-node="'+_escA(key)+'" data-tbody="'+tbId+'">Stream ID</th>';
    html+='<th>Friendly Name</th>';
    html+='<th>Multicast</th>';
    html+='<th class="sortable" data-col="age_s" data-node="'+_escA(key)+'" data-tbody="'+tbId+'">Last Seen</th>';
    html+='<th class="sortable" data-col="status" data-node="'+_escA(key)+'" data-tbody="'+tbId+'">Status</th>';
    if(showAdd) html+='<th></th>';
    html+='</tr></thead>';
    html+='<tbody id="'+tbId+'"></tbody>';
    html+='</table>';
    html+='</div></div>';
  }
  el.innerHTML=html;

  /* Render rows and attach sort handlers */
  for(var ni2=0;ni2<grp.order.length;ni2++){
    var key2=grp.order[ni2], nd2=grp.nodes[key2];
    var ss=_sortState[key2];
    var sorted=ss?_sortSources(nd2.sources,ss.col,ss.dir):nd2.sources;
    var tbId2='tb-'+btoa(key2).replace(/[^a-zA-Z0-9]/g,'').slice(0,16)+'_'+ni2;
    _renderRows(tbId2, sorted, showAdd, site, isLocal);
  }

  /* Sort column click handler (delegated) */
  el.querySelectorAll('th.sortable').forEach(function(th){
    th.addEventListener('click',function(){
      var col=th.dataset.col, nk=th.dataset.node, tb=th.dataset.tbody;
      var cur=_sortState[nk]||{col:'cid',dir:'asc'};
      var newDir=(cur.col===col&&cur.dir==='asc')?'desc':'asc';
      _sortState[nk]={col:col,dir:newDir};
      /* Update all th classes in this thead */
      th.closest('thead').querySelectorAll('th.sortable').forEach(function(h){
        h.classList.remove('sort-asc','sort-desc');
      });
      th.classList.add(newDir==='asc'?'sort-asc':'sort-desc');
      /* Re-render tbody */
      var nd3=null;
      for(var k in ({__dummy:1})){} /* scope trick */
      document.querySelectorAll('.lw-node').forEach(function(n){
        if(n.dataset.nodeKey===nk){
          /* find sources from current rendered rows */
        }
      });
      /* Get sources from stored _grpCache */
      if(_grpCache[nk]){
        _renderRows(tb, _sortSources(_grpCache[nk],col,newDir), showAdd, site, isLocal);
      }
      /* Re-apply search */
      var sq=document.getElementById('lw-search');
      if(sq&&sq.value) _applySearch(sq.value);
    });
  });
}

var _grpCache={};  /* nodeKey → sources array, for sort re-render */

function _buildGrpCache(srcs){
  _grpCache={};
  var grp=_groupNodes(srcs);
  for(var ni=0;ni<grp.order.length;ni++){
    var key=grp.order[ni];
    _grpCache[key]=grp.nodes[key].sources;
  }
}
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

  <div class="lw-search-bar">
    <input type="search" class="lw-search" id="lw-search"
           placeholder="Search node, name, stream ID, multicast…"
           oninput="_applySearch(this.value)">
    <span class="lw-search-count" id="lw-search-count"></span>
  </div>

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

  <div class="card">
    <div class="ch">🔔 Alert Configuration</div>
    <div class="cb">
      <label class="alert-channel-label" style="margin-bottom:12px;font-weight:600">
        <input type="checkbox" id="alert-on-stale" {{'checked' if alert_on_stale else ''}}>
        Alert when a source goes stale
      </label>
      <div id="alert-channels" style="margin-left:20px;{{'opacity:.4;pointer-events:none' if not alert_on_stale else ''}}">
        <p style="font-size:11px;color:var(--mu);margin-bottom:10px">Alert channels (uses Settings configuration):</p>
        <label class="alert-channel-label">
          <input type="checkbox" id="alert-email" {{'checked' if alert_email else ''}}> 📧 Email
        </label>
        <label class="alert-channel-label">
          <input type="checkbox" id="alert-webhook" {{'checked' if alert_webhook else ''}}> 🔗 Webhook
        </label>
        <label class="alert-channel-label">
          <input type="checkbox" id="alert-push" {{'checked' if alert_push else ''}}> 📱 Push notification
        </label>
      </div>
      <button class="btn bp bs" id="alert-save-btn" style="margin-top:12px">Save</button>
    </div>
  </div>
</main>
<script nonce="{{csp_nonce()}}">
""" + _SHARED_JS + r"""
var _isHub = true;
var _allSrcs = {};  /* site → flat source list for cache */

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
    html+='<div class="card lw-site-card">';
    html+='<div class="ch">📡 '+_esc(site);
    html+='<span class="ch-pills">';
    if(on)  html+='<span class="badge b-ok">'+on+' online</span>';
    if(st)  html+='<span class="badge b-al">'+st+' stale</span>';
    if(!on&&!st) html+='<span class="badge b-mu">no sources</span>';
    if(updAge!==null) html+='<span class="ts" style="margin-left:8px">updated '+_fmtAge(updAge)+'</span>';
    html+='</span></div>';
    html+='<div id="site-nodes-'+i+'"></div>';
    html+='</div>';
    _allSrcs[site]={idx:i,srcs:srcs};
  }
  document.getElementById('sites').innerHTML=html;

  /* Render accordion nodes per site */
  for(var si=0;si<names.length;si++){
    var sname=names[si], sd2=_allSrcs[sname];
    _buildGrpCache(sd2.srcs);
    _renderNodeAccordion('site-nodes-'+sd2.idx, sd2.srcs, false, sname, false, true);
  }

  /* Re-apply search */
  var sq=document.getElementById('lw-search');
  if(sq&&sq.value.trim()) _applySearch(sq.value);
}

document.getElementById('cfg-save-btn').addEventListener('click',function(){
  var csrf=_getCsrf();
  fetch('/api/livewire/config',{method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRFToken':csrf},
    body:JSON.stringify({source_timeout:parseInt(document.getElementById('cfg-timeout').value)||300})
  }).then(function(r){return r.json();})
  .then(function(d){_showMsg(d.ok?'Config saved.':'Error: '+(d.error||'?'),!d.ok);});
});

document.getElementById('alert-on-stale').addEventListener('change',function(){
  var ch=document.getElementById('alert-channels');
  ch.style.opacity=this.checked?'1':'0.4';
  ch.style.pointerEvents=this.checked?'':'none';
});
document.getElementById('alert-save-btn').addEventListener('click',function(){
  var csrf=_getCsrf();
  fetch('/api/livewire/alert_config',{method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRFToken':csrf},
    body:JSON.stringify({
      alert_on_stale: document.getElementById('alert-on-stale').checked,
      alert_email:    document.getElementById('alert-email').checked,
      alert_webhook:  document.getElementById('alert-webhook').checked,
      alert_push:     document.getElementById('alert-push').checked,
    })
  }).then(function(r){return r.json();})
  .then(function(d){_showMsg(d.ok?'Alert config saved.':'Error: '+(d.error||'?'),!d.ok);});
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

  <div class="lw-search-bar">
    <input type="search" class="lw-search" id="lw-search"
           placeholder="Search node, name, stream ID, multicast…"
           oninput="_applySearch(this.value)">
    <span class="lw-search-count" id="lw-search-count"></span>
  </div>

  <div class="card">
    <div class="ch">
      📡 Local sources
      <span class="ch-pills" id="local-pills"></span>
    </div>
    <div id="local-nodes"></div>
  </div>

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

  <div class="card">
    <div class="ch">🔔 Alert Configuration</div>
    <div class="cb">
      <label class="alert-channel-label" style="margin-bottom:12px;font-weight:600">
        <input type="checkbox" id="alert-on-stale" {{'checked' if alert_on_stale else ''}}>
        Alert when a source goes stale
      </label>
      <div id="alert-channels" style="margin-left:20px;{{'opacity:.4;pointer-events:none' if not alert_on_stale else ''}}">
        <p style="font-size:11px;color:var(--mu);margin-bottom:10px">Alert channels (uses Settings configuration):</p>
        <label class="alert-channel-label">
          <input type="checkbox" id="alert-email" {{'checked' if alert_email else ''}}> 📧 Email
        </label>
        <label class="alert-channel-label">
          <input type="checkbox" id="alert-webhook" {{'checked' if alert_webhook else ''}}> 🔗 Webhook
        </label>
        <label class="alert-channel-label">
          <input type="checkbox" id="alert-push" {{'checked' if alert_push else ''}}> 📱 Push notification
        </label>
      </div>
      <button class="btn bp bs" id="alert-save-btn" style="margin-top:12px">Save</button>
    </div>
  </div>
</main>
<script nonce="{{csp_nonce()}}">
""" + _SHARED_JS + r"""
var _isHub = false;
var _localSrcs = [];

function _renderLocal(srcs){
  _localSrcs = srcs;
  var on=srcs.filter(function(s){return s.status==='online';}).length;
  var st=srcs.filter(function(s){return s.status==='stale';}).length;
  var pills='';
  if(on) pills+='<span class="badge b-ok">'+on+' online</span> ';
  if(st) pills+='<span class="badge b-al">'+st+' stale</span>';
  if(!on&&!st) pills+='<span class="badge b-mu">no sources</span>';
  document.getElementById('local-pills').innerHTML=pills;
  _buildGrpCache(srcs);
  _renderNodeAccordion('local-nodes', srcs, true, '', true, true);
  var sq=document.getElementById('lw-search');
  if(sq&&sq.value.trim()) _applySearch(sq.value);
}

/* + Input click handler */
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
  }).catch(function(e2){btn.disabled=false;_showMsg('Request failed: '+e2,true);});
});

document.getElementById('cfg-save-btn').addEventListener('click',function(){
  var csrf=_getCsrf();
  fetch('/api/livewire/config',{method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRFToken':csrf},
    body:JSON.stringify({source_timeout:parseInt(document.getElementById('cfg-timeout').value)||300})
  }).then(function(r){return r.json();})
  .then(function(d){_showMsg(d.ok?'Config saved.':'Error: '+(d.error||'?'),!d.ok);});
});

document.getElementById('alert-on-stale').addEventListener('change',function(){
  var ch=document.getElementById('alert-channels');
  ch.style.opacity=this.checked?'1':'0.4';
  ch.style.pointerEvents=this.checked?'':'none';
});
document.getElementById('alert-save-btn').addEventListener('click',function(){
  var csrf=_getCsrf();
  fetch('/api/livewire/alert_config',{method:'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRFToken':csrf},
    body:JSON.stringify({
      alert_on_stale: document.getElementById('alert-on-stale').checked,
      alert_email:    document.getElementById('alert-email').checked,
      alert_webhook:  document.getElementById('alert-webhook').checked,
      alert_push:     document.getElementById('alert-push').checked,
    })
  }).then(function(r){return r.json();})
  .then(function(d){_showMsg(d.ok?'Alert config saved.':'Error: '+(d.error||'?'),!d.ok);});
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
    global _lw_monitor, _hub_data, _alert_cfg

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
            alert_on_stale=bool(pcfg2.get("alert_on_stale", False)),
            alert_email=bool(pcfg2.get("alert_email", True)),
            alert_webhook=bool(pcfg2.get("alert_webhook", True)),
            alert_push=bool(pcfg2.get("alert_push", True)),
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
            alert_on_stale=bool(pcfg2.get("alert_on_stale", False)),
            alert_email=bool(pcfg2.get("alert_email", True)),
            alert_webhook=bool(pcfg2.get("alert_webhook", True)),
            alert_push=bool(pcfg2.get("alert_push", True)),
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

    @app.post("/api/livewire/alert_config")
    @login_required
    @csrf_protect
    def livewire_alert_config_save():
        """Save alert configuration: which channels to notify on stale source."""
        data = request.get_json(silent=True) or {}
        _save_cfg({
            "alert_on_stale": bool(data.get("alert_on_stale", False)),
            "alert_email":    bool(data.get("alert_email",    True)),
            "alert_webhook":  bool(data.get("alert_webhook",  True)),
            "alert_push":     bool(data.get("alert_push",     True)),
        })
        return jsonify({"ok": True})
