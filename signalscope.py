# -*- coding: utf-8 -*-
SETTINGS_TPL = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Settings</title>
<style nonce="{{csp_nonce()}}">
:root{--bg:#0d0f14;--sur:#161a22;--bor:#252b38;--acc:#4f9cf9;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#e2e8f0;--mu:#64748b}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:var(--bg);color:var(--tx);font-size:14px}
header{background:var(--sur);border-bottom:1px solid var(--bor);padding:11px 20px;display:flex;align-items:center;gap:10px}
header h1{font-size:16px;font-weight:700}
.btn{display:inline-block;padding:5px 12px;border-radius:6px;font-size:13px;cursor:pointer;border:none;text-decoration:none}
.bp{background:var(--acc);color:#fff}.bg{background:var(--bor);color:var(--tx)}
.bd{background:var(--al);color:#fff}.bw{background:#7c2d12;color:#fca5a5}
.bo{background:#78350f;color:#fde68a}.bs{padding:3px 9px;font-size:12px}
.nav-active{background:var(--acc)!important;color:#fff!important}
.pg{display:flex;min-height:calc(100vh - 46px)}
.sb{width:182px;flex-shrink:0;background:var(--sur);border-right:1px solid var(--bor);padding:10px 0;position:sticky;top:0;height:calc(100vh - 46px);overflow-y:auto}
.tb{display:flex;align-items:center;gap:8px;width:100%;padding:9px 15px;background:none;border:none;border-left:3px solid transparent;color:var(--mu);font-size:13px;cursor:pointer;text-align:left;transition:background .12s,color .12s}
.tb:hover{background:#1a2030;color:var(--tx)}
.tb.on{background:#1a2030;color:var(--tx);border-left-color:var(--acc);font-weight:600}
.ct{flex:1;padding:26px;max-width:680px}
.pn{display:none}.pn.on{display:block}
label{display:block;margin-top:13px;color:var(--mu);font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.05em}
input[type=text],input[type=number],input[type=password],input[type=email]{width:100%;margin-top:4px;padding:8px 10px;background:#1e2433;border:1px solid var(--bor);border-radius:6px;color:var(--tx);font-size:14px}
.cr{display:flex;align-items:center;gap:8px;margin-top:10px}input[type=checkbox]{width:16px;height:16px;accent-color:var(--acc)}
.sec{margin-top:22px;padding-top:14px;border-top:1px solid var(--bor);font-weight:600;font-size:14px}
.sec:first-of-type{margin-top:0;padding-top:0;border-top:none}
.help{font-size:12px;color:var(--mu);margin-top:4px;line-height:1.5}
.act{margin-top:22px;padding-top:16px;border-top:1px solid var(--bor);display:flex;gap:8px}
.fl{list-style:none;margin-bottom:14px}.fl li{padding:8px 12px;border-radius:6px;background:#1e3a5f;border-left:3px solid var(--acc);margin-bottom:5px;font-size:13px}
</style><link rel="icon" type="image/x-icon" href="/favicon.ico"></head><body>
<script nonce="{{csp_nonce()}}">
function _csrfFetch(url,opts){
  opts=opts||{};
  if(!opts.headers)opts.headers={};
  var t=(document.querySelector('meta[name="csrf-token"]') || {}).content||"";
  opts.headers["X-CSRFToken"]=t;
  return fetch(url,opts);
}
function st(id){
  document.querySelectorAll('.pn').forEach(function(p){p.classList.remove('on');});
  document.querySelectorAll('.tb').forEach(function(b){b.classList.remove('on');});
  var p=document.getElementById('p-'+id),b=document.getElementById('b-'+id);
  if(p)p.classList.add('on');if(b)b.classList.add('on');
  history.replaceState(null,'','#'+id);
}
document.addEventListener('DOMContentLoaded',function(){
  st(location.hash.replace('#','')||'notif');
  if(typeof updateHubPanels==='function')updateHubPanels();
});
</script>
{{ topnav("settings") }}
<div class="pg">
<nav class="sb">
  <button class="tb" id="b-notif" onclick="st('notif')">🔔 Notifications</button>
  <button class="tb" id="b-hub"   onclick="st('hub')"  >🛰 Hub &amp; Network</button>
  <button class="tb" id="b-sec"   onclick="st('sec')"  >🔐 Security</button>
  <button class="tb" id="b-gen"   onclick="st('gen')"  >⚙ General</button>
  <button class="tb" id="b-maint" onclick="st('maint')">🗂 Maintenance</button>
  <button class="tb" id="b-sdr"   onclick="st('sdr')"  >📻 SDR Devices</button>
</nav>
<div class="ct">
{% with m=get_flashed_messages() %}{% if m %}<ul class="fl">{% for x in m %}<li>{{x}}</li>{% endfor %}</ul>{% endif %}{% endwith %}
<form method="post"><input type="hidden" name="_csrf_token" value="{{csrf_token()}}">
<div class="pn" id="p-notif">
  <div class="sec">📧 Email Alerts</div>
          <div class="cr"><input type="checkbox" name="email_enabled" value="1" {{'checked' if cfg.email.enabled}}><label style="margin:0;text-transform:none">Enable email alerts</label></div>
          <label>SMTP Host<input type="text" name="smtp_host" value="{{cfg.email.smtp_host}}"></label>
          <label>SMTP Port<input type="number" name="smtp_port" value="{{cfg.email.smtp_port}}"></label>
          <div class="cr"><input type="checkbox" name="use_tls" value="1" {{'checked' if cfg.email.use_tls}}><label style="margin:0;text-transform:none">Use TLS</label></div>
          <label>Username<input type="text" name="email_user" value="{{cfg.email.username}}"></label>
          <label>Password<input type="password" name="email_pass" value="{{cfg.email.password}}"></label>
          <label>From<input type="email" name="from_addr" value="{{cfg.email.from_addr}}"></label>
          <label>To<input type="email" name="to_addr" value="{{cfg.email.to_addr}}"></label>
  <div class="sec">🔔 Pushover Notifications</div>
          <div class="cr"><input type="checkbox" name="pv_enabled" value="1" {{'checked' if cfg.pushover.enabled}}><label style="margin:0;text-transform:none">Enable Pushover notifications</label></div>
          <label>User Key<input type="text" name="pv_user_key" value="{{cfg.pushover.user_key}}" placeholder="Your Pushover user key"></label>
          <label>App Token<input type="text" name="pv_app_token" value="{{cfg.pushover.app_token}}" placeholder="Your application API token"></label>
          <label>Priority for WARN
            <select name="pv_pri_warn" style="width:100%;margin-top:4px;padding:8px 10px;background:#1e2433;border:1px solid var(--bor);border-radius:6px;color:var(--tx);font-size:14px">
              <option value="-2" {{'-2'==cfg.pushover.priority_warn|string and 'selected' or ''}}>Lowest (silent)</option>
              <option value="-1" {{'-1'==cfg.pushover.priority_warn|string and 'selected' or ''}}>Low</option>
              <option value="0" {{'0'==cfg.pushover.priority_warn|string and 'selected' or ''}}>Normal</option>
              <option value="1" {{'1'==cfg.pushover.priority_warn|string and 'selected' or ''}}>High</option>
            </select>
          </label>
          <label>Priority for ALERT
            <select name="pv_pri_alert" style="width:100%;margin-top:4px;padding:8px 10px;background:#1e2433;border:1px solid var(--bor);border-radius:6px;color:var(--tx);font-size:14px">
              <option value="-1" {{'-1'==cfg.pushover.priority_alert|string and 'selected' or ''}}>Low</option>
              <option value="0" {{'0'==cfg.pushover.priority_alert|string and 'selected' or ''}}>Normal</option>
              <option value="1" {{'1'==cfg.pushover.priority_alert|string and 'selected' or ''}}>High</option>
              <option value="2" {{'2'==cfg.pushover.priority_alert|string and 'selected' or ''}}>Emergency (requires acknowledgement)</option>
            </select>
          </label>
          <p class="help">Get your keys at <a href="https://pushover.net" target="_blank" style="color:var(--acc)">pushover.net</a></p>
  <div class="sec">🔗 Webhook Routing</div>
          <div class="cr"><input type="checkbox" name="webhook_enabled" value="1" {{'checked' if cfg.webhook.enabled}}>
            <label style="margin:0;text-transform:none">Enable webhook notifications</label></div>
        
          <p class="help" style="margin-top:8px">
            Define one or more routing rules below. Each alert is matched against all rules in order — every matching rule receives the alert.
            Leave filter fields blank to match everything. If no rule matches, the fallback URL is used.
          </p>
        
          {# ── Routing rules table ── #}
          <div style="margin-top:12px">
            <div style="display:grid;grid-template-columns:0.8fr 1.5fr 0.6fr 0.8fr 0.8fr 0.8fr 0.6fr auto;gap:6px;align-items:center;
                        font-size:11px;color:var(--mu);padding:0 4px;margin-bottom:4px">
              <span>Name</span><span>Webhook URL</span><span>Style</span>
              <span>Streams (comma-sep, blank=all)</span><span>Types (comma-sep, blank=all)</span>
              <span>Sites (comma-sep, blank=all)</span><span>Severity</span><span></span>
            </div>
            <div id="wh-routes">
            {% for i, r in cfg.webhook.routes | enumerate %}
            <div class="wh-row" style="display:grid;grid-template-columns:0.8fr 1.5fr 0.6fr 0.8fr 0.8fr 0.8fr 0.6fr auto;gap:6px;align-items:center;margin-bottom:6px">
              <input type="text" name="wr_name_{{i}}"    value="{{r.name}}"    placeholder="e.g. Breakfast"
                     style="padding:6px 8px;background:#1e2433;border:1px solid var(--bor);border-radius:5px;color:var(--tx);font-size:12px;width:100%">
              <input type="text" name="wr_url_{{i}}"     value="{{r.url}}"     placeholder="https://…"
                     style="padding:6px 8px;background:#1e2433;border:1px solid var(--bor);border-radius:5px;color:var(--tx);font-size:12px;width:100%">
              <select name="wr_style_{{i}}" style="padding:6px 8px;background:#1e2433;border:1px solid var(--bor);border-radius:5px;color:var(--tx);font-size:12px;width:100%">
                <option value="teams" {{'selected' if r.teams_style}}>Teams Card</option>
                <option value="slack" {{'selected' if not r.teams_style}}>Plain text</option>
              </select>
              <input type="text" name="wr_streams_{{i}}" value="{{r.filter_streams | join(', ')}}" placeholder="blank = all"
                     style="padding:6px 8px;background:#1e2433;border:1px solid var(--bor);border-radius:5px;color:var(--tx);font-size:12px;width:100%">
              <input type="text" name="wr_types_{{i}}"   value="{{r.filter_types | join(', ')}}"   placeholder="blank = all"
                     style="padding:6px 8px;background:#1e2433;border:1px solid var(--bor);border-radius:5px;color:var(--tx);font-size:12px;width:100%">
              <input type="text" name="wr_sites_{{i}}"   value="{{r.filter_sites | join(', ')}}"   placeholder="blank = all"
                     style="padding:6px 8px;background:#1e2433;border:1px solid var(--bor);border-radius:5px;color:var(--tx);font-size:12px;width:100%">
              <select name="wr_severity_{{i}}" style="padding:6px 8px;background:#1e2433;border:1px solid var(--bor);border-radius:5px;color:var(--tx);font-size:12px;width:100%">
                <option value=""      {{'selected' if not r.filter_severity}}>Both</option>
                <option value="ALERT" {{'selected' if r.filter_severity=='ALERT'}}>Alert</option>
                <option value="WARN"  {{'selected' if r.filter_severity=='WARN'}}>Warn</option>
              </select>
              <button type="button" onclick="this.closest('.wh-row').remove()"
                      style="padding:5px 9px;background:#7c2d12;color:#fca5a5;border:none;border-radius:5px;cursor:pointer;font-size:13px">✕</button>
            </div>
            {% endfor %}
            </div>
            <button type="button" onclick="addRoute()"
                    style="margin-top:4px;padding:6px 14px;background:#1e3a5f;color:var(--acc);border:1px solid var(--acc);border-radius:6px;cursor:pointer;font-size:12px">+ Add route</button>
          </div>
        
          <div style="margin-top:14px;padding:10px 12px;background:#1e2433;border:1px solid var(--bor);border-radius:6px">
            <div style="font-size:12px;font-weight:600;color:var(--mu);margin-bottom:6px">Fallback (used when no route matches)</div>
            <div class="cr" style="margin-bottom:6px">
              <input type="checkbox" name="webhook_teams_style" value="1" {{'checked' if cfg.webhook.teams_style}}>
              <label style="margin:0;text-transform:none;font-size:12px">MS Teams Adaptive Card format
                <span style="font-size:11px;color:var(--mu)">(uncheck for plain text / Slack)</span></label>
            </div>
            <input type="text" name="webhook_url" value="{{cfg.webhook.url}}"
                   placeholder="Fallback URL — used if no route above matches (leave blank to drop unmatched alerts)"
                   style="width:100%;padding:7px 10px;background:#141820;border:1px solid var(--bor);border-radius:6px;color:var(--tx);font-size:13px">
          </div>
        
          <p class="help" style="margin-top:6px">
            Available alert types: SILENCE, CLIP, HISS, AI_ALERT, AI_WARN, RTP_LOSS, RTP_LOSS_WARN, PTP_OFFSET, PTP_JITTER, PTP_LOST, PTP_GM_CHANGE, CMP_ALERT
          </p>
        
          <script nonce="{{csp_nonce()}}">
          var _routeIdx = {{cfg.webhook.routes | length}};
          function addRoute(){
            var i = _routeIdx++;
            var row = document.createElement('div');
            row.className = 'wh-row';
            row.style.cssText = 'display:grid;grid-template-columns:0.8fr 1.5fr 0.6fr 0.8fr 0.8fr 0.8fr 0.6fr auto;gap:6px;align-items:center;margin-bottom:6px';
            var inp = 'padding:6px 8px;background:#1e2433;border:1px solid #252b38;border-radius:5px;color:#e2e8f0;font-size:12px;width:100%';
            row.innerHTML =
              '<input type="text" name="wr_name_'+i+'" placeholder="e.g. Engineering" style="'+inp+'">'
              +'<input type="text" name="wr_url_'+i+'" placeholder="https://…" style="'+inp+'">'
              +'<select name="wr_style_'+i+'" style="'+inp+'"><option value="teams">Teams Card</option><option value="slack">Plain text</option></select>'
              +'<input type="text" name="wr_streams_'+i+'" placeholder="blank = all" style="'+inp+'">'
              +'<input type="text" name="wr_types_'+i+'"   placeholder="blank = all" style="'+inp+'">'
              +'<input type="text" name="wr_sites_'+i+'"   placeholder="blank = all" style="'+inp+'">'
              +'<select name="wr_severity_'+i+'" style="'+inp+'"><option value="">Both</option><option value="ALERT">Alert</option><option value="WARN">Warn</option></select>'
              +'<button type="button" onclick="this.closest(\'.wh-row\').remove()" style="padding:5px 9px;background:#7c2d12;color:#fca5a5;border:none;border-radius:5px;cursor:pointer;font-size:13px">\u2715</button>';
            document.getElementById('wh-routes').appendChild(row);
          }
          </script>
  <div class="sec">🔔 Test Notifications</div>
          <p class="help" style="margin-bottom:10px">Send a test alert to verify your notification settings are working.</p>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            {%if cfg.email.enabled and cfg.email.smtp_host%}
            <button type="button" class="btn bg" onclick="testNotify('email')">📧 Test Email</button>
            {%endif%}
            {%if cfg.webhook.enabled and cfg.webhook.url or cfg.webhook.routes%}
            <button type="button" class="btn bg" onclick="testNotify('webhook')">🔗 Test Webhook</button>
            {%endif%}
            {%if cfg.pushover.enabled and cfg.pushover.user_key%}
            <button type="button" class="btn bg" onclick="testNotify('pushover')">📱 Test Pushover</button>
            {%endif%}
            {%if not (cfg.email.enabled or cfg.webhook.enabled or cfg.pushover.enabled)%}
            <span style="color:var(--mu);font-size:12px">No notification methods configured yet.</span>
            {%endif%}
          </div>
          <div id="test-notify-result" style="margin-top:8px;font-size:12px;display:none"></div>
          <script nonce="{{csp_nonce()}}">
          function testNotify(channel){
            var el = document.getElementById('test-notify-result');
            el.style.display=''; el.style.color='var(--mu)'; el.textContent='Sending…';
            _csrfFetch('/settings/test-notify?channel='+channel, {method:'POST'})
              .then(r=>r.json()).then(function(d){
                el.style.color = d.ok ? 'var(--ok)' : 'var(--al)';
                el.textContent = d.ok ? '✓ '+d.message : '✗ '+d.message;
              }).catch(function(e){ el.style.color='var(--al)'; el.textContent='✗ Request failed: '+e; });
          }
          </script>
  <div class="act"><button class="btn bp" type="submit">Save</button><a class="btn bg" href="/">Cancel</a></div>
</div>
<div class="pn" id="p-hub">
  <div class="sec">🌐 Network Interfaces</div>
          <label>Audio interface IP (for multicast reception)<input type="text" name="audio_ip" value="{{cfg.network.audio_interface_ip}}" placeholder="0.0.0.0"></label>
          <p class="help">Use 0.0.0.0 to accept on any interface.</p>
          <label>Management interface IP (for outbound alerts)<input type="text" name="mgmt_ip" value="{{cfg.network.management_interface_ip}}" placeholder="0.0.0.0"></label>
  <div class="sec">📡 PTP Clock Thresholds</div>
  <p class="help" style="margin-bottom:8px">This monitor is a passive PTP observer, not a slave — absolute offset depends on NTP accuracy. Set thresholds to match your network. Defaults are suitable for NTP-synced systems (±5 ms warn, ±50 ms alert).</p>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
    <label>Offset warn (µs)
      <input type="number" name="ptp_offset_warn_us" value="{{cfg.ptp_offset_warn_us}}" min="100" max="1000000">
    </label>
    <label>Offset alert (µs)
      <input type="number" name="ptp_offset_alert_us" value="{{cfg.ptp_offset_alert_us}}" min="100" max="1000000">
    </label>
    <label>Jitter warn (µs)
      <input type="number" name="ptp_jitter_warn_us" value="{{cfg.ptp_jitter_warn_us}}" min="100" max="1000000">
    </label>
    <label>Jitter alert (µs)
      <input type="number" name="ptp_jitter_alert_us" value="{{cfg.ptp_jitter_alert_us}}" min="100" max="1000000">
    </label>
  </div>
  <p class="help">1000 µs = 1 ms. For a PTP-slaved system you could tighten these to 500/5000 µs. For NTP-only, keep at 5000/50000.</p>
  <div class="sec">🛰 Multi-Site Hub</div>
        
          <label>Mode
            <select name="hub_mode" id="hub_mode_sel" onchange="updateHubPanels()"
                    style="width:100%;margin-top:4px;padding:8px 10px;background:#1e2433;border:1px solid var(--bor);border-radius:6px;color:var(--tx);font-size:14px">
              <option value="client" {{'selected' if cfg.hub.mode=='client'}}>Client — this site sends data to a central hub</option>
              <option value="hub"    {{'selected' if cfg.hub.mode=='hub'}}>Hub — this machine receives data from all sites</option>
              <option value="both"   {{'selected' if cfg.hub.mode=='both'}}>Both — client + hub on this machine</option>
            </select>
          </label>
        
          {# ── Client panel ── #}
          <div id="hub_client_panel" style="margin-top:12px;padding:12px;background:#0d1520;border:1px solid var(--bor);border-radius:8px">
            <div style="font-size:12px;font-weight:600;color:var(--acc);margin-bottom:10px">📡 Client Settings</div>
            <label>Site Name
              <input type="text" name="hub_site_name" value="{{cfg.hub.site_name}}"
                     placeholder="e.g. Cool FM Belfast — shown on the hub dashboard">
            </label>
            <label style="margin-top:8px;display:block">Hub URL
              <input type="text" name="hub_url" value="{{cfg.hub.hub_url}}"
                     placeholder="https://hub.yourdomain.com  or  http://hub-server-ip">
            </label>
            <p class="help">The address of the hub server. If the hub has a certificate, use <code style="background:#1e2433;padding:1px 5px;border-radius:3px">https://hub.yourdomain.com</code>. Without a cert, use <code style="background:#1e2433;padding:1px 5px;border-radius:3px">http://hub-server-ip</code> (hub runs on port 80 automatically).</p>
          </div>
        
          {# ── Hub panel ── #}
          <div id="hub_server_panel" style="margin-top:12px;padding:12px;background:#0d1520;border:1px solid var(--bor);border-radius:8px">
            <div style="font-size:12px;font-weight:600;color:#86efac;margin-bottom:10px">🛰 Hub Server Settings</div>
            <p class="help" style="margin-bottom:8px">
              This machine will listen for heartbeats from client sites on port 5000 at
              <code style="background:#1e2433;padding:1px 6px;border-radius:4px">/api/v1/heartbeat</code>.
              Make sure this port is accessible from all client sites.
            </p>
          </div>
        
          {# ── Shared secret — applies to BOTH sides, always shown prominently ── #}
          <div style="margin-top:12px;padding:12px;background:#1a1020;border:2px solid
               {{'#22c55e' if cfg.hub.secret_key|length >= 16 else ('#f59e0b' if cfg.hub.secret_key else '#ef4444')}};border-radius:8px">
            <div style="font-size:12px;font-weight:600;margin-bottom:8px;color:
                 {{'var(--ok)' if cfg.hub.secret_key|length >= 16 else ('var(--wn)' if cfg.hub.secret_key else 'var(--al)')}}">
              🔑 Shared Secret Key
              — must be identical on the hub server AND every client site
            </div>
            <input type="password" name="hub_secret" value="{{cfg.hub.secret_key}}"
                   placeholder="Enter a strong secret — minimum 16 characters"
                   style="width:100%;padding:8px 10px;background:#141820;border:1px solid var(--bor);border-radius:6px;color:var(--tx);font-size:13px">
            <p style="margin-top:8px;font-size:12px;color:
                 {{'var(--ok)' if cfg.hub.secret_key|length >= 16 else ('var(--wn)' if cfg.hub.secret_key else 'var(--al)')}}">
              {%if cfg.hub.secret_key|length >= 16%}
                ✓ Strong secret set — heartbeats are HMAC-signed, replay-protected, and payload-encrypted.
              {%elif cfg.hub.secret_key%}
                ⚠ Secret too short — use at least 16 characters for adequate security.
              {%else%}
                ✗ No secret set — connections are unsigned and unencrypted. Set this on the hub first, then on all client sites.
              {%endif%}
            </p>
          </div>
        
          <div class="cr" style="margin-top:8px">
            <input type="checkbox" name="hub_enabled" value="1" {{'checked' if cfg.hub.enabled}}>
            <label style="margin:0;text-transform:none;font-size:12px;color:var(--mu)">
              Mark as hub-reporting enabled (informational — heartbeats send whenever Mode and Hub URL are set)
            </label>
          </div>
        
          <script nonce="{{csp_nonce()}}">
          function updateHubPanels(){
            var mode = document.getElementById('hub_mode_sel').value;
            var cp = document.getElementById('hub_client_panel');
            var sp = document.getElementById('hub_server_panel');
            cp.style.display = (mode==='client'||mode==='both') ? '' : 'none';
            sp.style.display = (mode==='hub'   ||mode==='both') ? '' : 'none';
          }
          document.addEventListener('DOMContentLoaded', updateHubPanels);
          </script>
  <div class="sec">🔒 HTTPS / Let's Encrypt</div>
          {% if cfg.hub.mode in ('hub','both') %}
          <div style="padding:12px;background:#0d1520;border:1px solid var(--bor);border-radius:8px;margin-bottom:8px">
            <div style="font-size:12px;font-weight:600;color:var(--acc);margin-bottom:10px">Current status</div>
            <div id="acme-cert-info" style="font-size:12px;color:var(--mu);margin-bottom:10px">
              {% if cfg.tls_enabled and cfg.tls_domain %}
                <span style="color:var(--ok)">✓ HTTPS active</span> — {{cfg.tls_domain}}
                <span style="margin-left:12px;color:var(--mu)">(restart app to apply changes)</span>
              {% else %}
                <span style="color:var(--mu)">HTTP only — no certificate configured</span>
              {% endif %}
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px">
              <label>Domain (FQDN)
                <input type="text" id="acme_domain" value="{{cfg.tls_domain}}"
                       placeholder="hub.yourdomain.com"
                       style="width:100%;padding:7px 10px;background:#141820;border:1px solid var(--bor);border-radius:6px;color:var(--tx);font-size:13px;margin-top:4px">
              </label>
              <label>Contact email (optional)
                <input type="text" id="acme_email" value=""
                       placeholder="admin@yourdomain.com"
                       style="width:100%;padding:7px 10px;background:#141820;border:1px solid var(--bor);border-radius:6px;color:var(--tx);font-size:13px;margin-top:4px">
              </label>
            </div>
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
              <button type="button" class="btn bp" onclick="acmeIssue()">🔒 Get Certificate</button>
              <button type="button" class="btn bg" onclick="acmeIssue(true)" style="font-size:11px">Use staging (test)</button>
              <button type="button" class="btn bg" onclick="acmeClear()" style="font-size:11px;color:var(--wn)">↺ Clear &amp; retry fresh</button>
              {% if cfg.tls_enabled %}
              <button type="button" class="btn bw" onclick="tlsToggle(false)">Disable HTTPS</button>
              {% elif cfg.tls_cert_path %}
              <button type="button" class="btn bg" onclick="tlsToggle(true)">Enable HTTPS</button>
              {% endif %}
            </div>
            <p class="help" style="margin-top:8px">The hub already listens on port 80 (HTTP) so the ACME challenge is served automatically — just ensure port 80 is open in your firewall. Certificate auto-renews when less than 30 days remain.</p>
            <div id="acme-log" style="margin-top:10px;padding:8px;background:#060810;border-radius:5px;font-family:monospace;font-size:11px;color:#64748b;max-height:140px;overflow-y:auto;display:none"></div>
          </div>
          <script nonce="{{csp_nonce()}}">
          function acmeClear(){
            if(!confirm("Clear cached ACME account key? This forces a completely fresh certificate request.")) return;
            _csrfFetch('/settings/acme-clear',{method:'POST'})
              .then(r=>r.json()).then(function(d){
                var logEl=document.getElementById('acme-log');
                logEl.style.display='';
                logEl.textContent=d.message;
              });
          }
          function acmeIssue(staging){
            var domain  = document.getElementById('acme_domain').value.trim();
            var email   = document.getElementById('acme_email').value.trim();
            var logEl   = document.getElementById('acme-log');
            if(!domain){ alert('Enter a domain name first.'); return; }
            logEl.style.display=''; logEl.textContent='Starting…';
            var fd = new FormData();
            fd.append('tls_domain', domain);
            fd.append('tls_email',  email);
            if(staging) fd.append('staging','1');
            _csrfFetch('/settings/acme-issue',{method:'POST',body:fd})
              .then(r=>r.json()).then(function(d){
                logEl.textContent = d.message;
                if(d.ok) pollAcmeStatus();
              });
          }
          function pollAcmeStatus(){
            var logEl = document.getElementById('acme-log');
            logEl.style.display='';
            var iv = setInterval(function(){
              fetch('/settings/acme-status').then(r=>r.json()).then(function(d){
                logEl.innerHTML = d.log.join('<br>');
                logEl.scrollTop = logEl.scrollHeight;
                if(d.status !== 'running'){ clearInterval(iv); }
              });
            }, 2000);
          }
          function tlsToggle(enable){
            var fd = new FormData(); fd.append('enable', enable?'1':'0');
            _csrfFetch('/settings/tls-toggle',{method:'POST',body:fd})
              .then(r=>r.json()).then(function(d){ alert(d.message); location.reload(); });
          }
          // Poll if currently running
          {% if acme_running %}pollAcmeStatus();{% endif %}
          </script>
          {% else %}
          <p class="help">Let's Encrypt is only available on instances configured in Hub or Both mode (requires a public FQDN).</p>
          {% endif %}
  <div class="act"><button class="btn bp" type="submit">Save</button><a class="btn bg" href="/">Cancel</a></div>
</div>
<div class="pn" id="p-sec">
  <div class="sec">🔐 Web UI Authentication</div>
          <div class="cr"><input type="checkbox" name="auth_enabled" value="1" {{'checked' if cfg.auth.enabled}}><label style="margin:0;text-transform:none">Require login to access dashboard</label></div>
          <label>Username<input type="text" name="auth_username" value="{{cfg.auth.username}}"></label>
          <label>New Password (leave blank to keep current)<input type="password" name="auth_password" placeholder="Minimum 8 characters"></label>
          <p class="help">Password is stored as a salted PBKDF2-SHA256 hash (260,000 rounds). After enabling auth, reload the page and you will be prompted to log in.</p>
          <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-top:8px">
            <label>Max login attempts
              <input type="number" name="login_max_attempts" value="{{cfg.login_max_attempts}}" min="3" max="100">
              <span class="help" style="display:block;margin-top:2px">Failed attempts before lockout</span>
            </label>
            <label>Lockout duration (mins)
              <input type="number" name="login_lockout_mins" value="{{cfg.login_lockout_mins}}" min="1" max="1440">
              <span class="help" style="display:block;margin-top:2px">How long to block the IP</span>
            </label>
            <label>Session timeout (hours)
              <input type="number" name="session_timeout_hrs" value="{{cfg.session_timeout_hrs}}" min="1" max="168">
              <span class="help" style="display:block;margin-top:2px">Auto logout after inactivity</span>
            </label>
          </div>
  <div class="act"><button class="btn bp" type="submit">Save</button><a class="btn bg" href="/">Cancel</a></div>
</div>
<div class="pn" id="p-gen">
  <div class="sec">📊 SLA Reporting</div>
          <label>SLA Target (%)<input type="number" name="sla_target" value="{{cfg.sla_target_pct}}" min="0" max="100" step="0.1"></label>
          <p class="help">Streams below this uptime percentage will be flagged as missing SLA. <a href="/sla" style="color:var(--acc)">View SLA Dashboard →</a></p>
  <div class="sec">🎵 Now Playing</div>
          <label>Country Code<input type="text" name="nowplaying_country" value="{{cfg.nowplaying_country}}" placeholder="GB"></label>
          <p class="help">Planet Radio API country code (GB, SE, etc). Used to fetch station list.</p>
  <div class="sec">📅 Daily Report</div>
          <label>Send time (HH:MM local)
            <input type="text" name="daily_report_time" value="{{cfg.daily_report_time}}"
                   placeholder="06:00" pattern="[0-2][0-9]:[0-5][0-9]"
                   style="width:120px">
          </label>
          <p class="help">Daily summary email is sent at this time each day. Requires email to be configured. Uses 24-hour local time.</p>
  <div class="act"><button class="btn bp" type="submit">Save</button><a class="btn bg" href="/">Cancel</a></div>
</div>
<div class="pn" id="p-maint">
  <div class="sec">🗂 Maintenance</div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
            <label>Alert log max events
              <input type="number" name="alert_log_max" value="{{cfg.alert_log_max}}" min="100" max="100000" step="100">
              <span class="help" style="display:block;margin-top:2px">alert_log.json is pruned to this size (0 = unlimited)</span>
            </label>
            <label>Clip max age (days)
              <input type="number" name="clip_max_age_days" value="{{cfg.clip_max_age_days}}" min="0" max="365">
              <span class="help" style="display:block;margin-top:2px">Clips older than this are deleted (0 = keep forever)</span>
            </label>
            <label>Max clips per stream
              <input type="number" name="clip_max_per_stream" value="{{cfg.clip_max_per_stream}}" min="0" max="10000">
              <span class="help" style="display:block;margin-top:2px">Oldest clips removed when over limit (0 = unlimited)</span>
            </label>
          </div>
  <div class="act"><button class="btn bp" type="submit">Save</button><a class="btn bg" href="/">Cancel</a></div>
</div>
<div class="pn" id="p-sdr">
  <div class="sec">📻 SDR Devices</div>
  <p class="help" style="margin-bottom:10px">Register RTL-SDR dongles by serial number so inputs can reference them reliably regardless of USB port order. Program a serial once with <code style="background:#1e2433;padding:1px 6px;border-radius:3px">rtl_eeprom -d 0 -s "MY_DONGLE"</code>, then unplug and replug.</p>
  <div id="sdr-connected" style="margin-bottom:10px;font-size:12px;color:var(--mu)">
    <button type="button" class="btn bg bs" onclick="sdrScan()">🔍 Scan for dongles</button>
    <span id="sdr-scan-result" style="margin-left:8px"></span>
  </div>
  <table style="width:100%;border-collapse:collapse;font-size:13px" id="sdr-table">
    <thead><tr style="color:var(--mu);font-size:11px;text-transform:uppercase">
      <th style="padding:4px 8px;text-align:left">Serial</th>
      <th style="padding:4px 8px;text-align:left">Role</th>
      <th style="padding:4px 8px;text-align:left">PPM</th>
      <th style="padding:4px 8px;text-align:left">Label</th>
      <th style="padding:4px 8px"></th>
    </tr></thead>
    <tbody id="sdr-rows">
    {% for dev in cfg.sdr_devices %}
    <tr style="border-top:1px solid var(--bor)">
      <td style="padding:6px 8px"><input type="text" name="sdr_serial" value="{{dev.serial}}" style="width:160px;font-family:monospace;font-size:12px;padding:4px 6px;background:#1e2433;border:1px solid var(--bor);border-radius:4px;color:var(--tx)"></td>
      <td style="padding:6px 8px">
        <select name="sdr_role" style="padding:4px 6px;background:#1e2433;border:1px solid var(--bor);border-radius:4px;color:var(--tx);font-size:13px">
          <option value="none" {{'selected' if dev.role=='none'}}>None</option>
          <option value="dab"  {{'selected' if dev.role=='dab' }}>DAB</option>
          <option value="fm"   {{'selected' if dev.role=='fm'  }}>FM</option>
        </select>
      </td>
      <td style="padding:6px 8px"><input type="number" name="sdr_ppm" value="{{dev.ppm}}" style="width:70px;padding:4px 6px;background:#1e2433;border:1px solid var(--bor);border-radius:4px;color:var(--tx);font-size:13px" min="-150" max="150"></td>
      <td style="padding:6px 8px"><input type="text" name="sdr_label" value="{{dev.label}}" placeholder="e.g. Studio 1 DAB" style="width:160px;padding:4px 6px;background:#1e2433;border:1px solid var(--bor);border-radius:4px;color:var(--tx);font-size:13px"></td>
      <td style="padding:6px 8px"><button type="button" class="btn bw bs" onclick="this.closest('tr').remove()">✕</button></td>
    </tr>
    {% endfor %}
    </tbody>
  </table>
  <button type="button" class="btn bg bs" style="margin-top:8px" onclick="sdrAddRow()">+ Add device</button>
  <p class="help" style="margin-top:6px">PPM = frequency correction for your specific dongle. Run <code style="background:#1e2433;padding:1px 5px;border-radius:3px">rtl_test -p</code> for ~5 minutes to measure it.</p>
  <div id="sdr-warnings" style="margin-top:8px"></div>
  <script nonce="{{csp_nonce()}}">
  function sdrAddRow(serial, role, ppm, label){
    serial=serial||''; role=role||'none'; ppm=ppm||0; label=label||'';
    var tr=document.createElement('tr');
    tr.style.borderTop='1px solid var(--bor)';
    tr.innerHTML='<td style="padding:6px 8px"><input type="text" name="sdr_serial" value="'+serial+'" style="width:160px;font-family:monospace;font-size:12px;padding:4px 6px;background:#1e2433;border:1px solid var(--bor);border-radius:4px;color:var(--tx)"></td>'
      +'<td style="padding:6px 8px"><select name="sdr_role" style="padding:4px 6px;background:#1e2433;border:1px solid var(--bor);border-radius:4px;color:var(--tx);font-size:13px">'
      +'<option value="none"'+(role==="none"?" selected":"")+'">None</option>'
      +'<option value="dab"'+(role==="dab"?" selected":"")+'">DAB</option>'
      +'<option value="fm"'+(role==="fm"?" selected":"")+'">FM</option>'
      +'</select></td>'
      +'<td style="padding:6px 8px"><input type="number" name="sdr_ppm" value="'+ppm+'" style="width:70px;padding:4px 6px;background:#1e2433;border:1px solid var(--bor);border-radius:4px;color:var(--tx);font-size:13px" min="-150" max="150"></td>'
      +'<td style="padding:6px 8px"><input type="text" name="sdr_label" value="'+label+'" placeholder="e.g. Studio 1 DAB" style="width:160px;padding:4px 6px;background:#1e2433;border:1px solid var(--bor);border-radius:4px;color:var(--tx);font-size:13px"></td>'
      +'<td style="padding:6px 8px"><button type="button" class="btn bw bs" onclick="this.closest(&quot;tr&quot;).remove()">✕</button></td>';
    document.getElementById('sdr-rows').appendChild(tr);
  }
  function sdrScan(){
    var el=document.getElementById('sdr-scan-result');
    var warn=document.getElementById('sdr-warnings');
    el.textContent='Scanning…'; el.style.color='var(--mu)';
    _csrfFetch('/api/sdr/scan').then(function(r){return r.json();}).then(function(d){
      if(!d.devices || d.devices.length===0){
        el.textContent='No dongles found — check USB and rtl-sdr installation.';
        el.style.color='var(--wn)'; return;
      }
      el.textContent=d.devices.length+' dongle(s) found.';
      el.style.color='var(--ok)';
      // Show unregistered dongle warnings
      var regSerials=Array.from(document.querySelectorAll('[name=sdr_serial]')).map(function(i){return i.value.trim();});
      warn.innerHTML='';
      d.devices.forEach(function(dev){
        if(!regSerials.includes(dev.serial)){
          var w=document.createElement('div');
          w.style.cssText='padding:8px 12px;background:#2a1a0f;border-left:3px solid var(--wn);border-radius:4px;margin-bottom:4px;font-size:12px';
          w.innerHTML='⚠ Unregistered dongle: <b style="font-family:monospace">'+dev.serial+'</b> ('+dev.name+')'
            +' &nbsp;<button type="button" class="btn bg bs sdr-register-btn" data-serial="'+dev.serial+'">Register</button>';
          warn.appendChild(w);
        }
      });
      // Wire register buttons via delegation — avoids dynamic onclick CSP issue
      warn.querySelectorAll('.sdr-register-btn').forEach(function(btn){
        btn.addEventListener('click', function(){ sdrAddRow(this.dataset.serial); });
      });
      if(!warn.children.length){
        warn.innerHTML='<p style="font-size:12px;color:var(--ok)">✓ All connected dongles are registered.</p>';
      }
    }).catch(function(e){el.textContent='Scan failed: '+e; el.style.color='var(--al)';});
  }
  </script>

  <div class="act"><button class="btn bp" type="submit">Save</button><a class="btn bg" href="/">Cancel</a></div>
</div>
</form>
</div></div>

</body></html>"""#!/usr/bin/env python3
"""
SignalScope
===================
Monitors Axia Livewire (stream IDs) and raw multicast RTP streams.
Uses a local ONNX autoencoder for zero-training-required glitch detection.

Each stream learns its own "normal" baseline over 5 minutes, then continuously
monitors for deviations — silence, hiss, clipping, dropouts, distortion etc.
Models are saved as .onnx files and persist across restarts.

Author: Built on core from ITConor / JPDesignsNI
"""

import os, sys, json, math, time, wave, socket, struct, threading, io, hashlib, functools, queue
import selectors
import collections, urllib.request, urllib.error, urllib.parse, datetime, base64
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple, Any

import numpy as np
from flask import Flask, jsonify, request, render_template_string, redirect, url_for, flash, Response, send_from_directory, make_response

# ─── Optional deps — checked at runtime ───────────────────────────────────────

def _try_import(name):
    try:
        import importlib
        return importlib.import_module(name)
    except ImportError:
        return None

# ─── Constants ────────────────────────────────────────────────────────────────

BUILD                  = "SignalScope-2.5.37"
SAMPLE_RATE            = 48000
CHUNK_DURATION         = 0.5
CHUNK_SIZE             = int(SAMPLE_RATE * CHUNK_DURATION)
ALERT_COOLDOWN         = 60.0
STREAM_BUFFER_SECONDS  = 20.0
ALERT_BUFFER_SECONDS   = 8.0
LIVE_PLAYOUT_BUFFER_SECS = 1.5  # extra browser listen jitter buffer on Linux/VMs

LEARN_DURATION_SECONDS = 86400.0      # 24 hours
AI_ANALYSIS_INTERVAL   = 5.0
AI_FEATURE_DIM         = 14           # stable content-agnostic features (reduced from 20)
AI_HIDDEN_DIM          = 7
ANOMALY_WARN_THRESHOLD = 3.2          # z-score → WARN (raised to reduce music/speech false positives)
ANOMALY_ALERT_THRESHOLD= 5.5          # z-score → ALERT
AI_CONFIRM_WINDOWS     = 3            # consecutive high-z windows required before alerting
MIN_TRAINING_SAMPLES   = 60

# ─── RTP / comparison / report constants ─────────────────────────────────────

RTP_LOSS_WARN_PCT       = 0.5           # % packet loss → warn
RTP_LOSS_ALERT_PCT      = 2.0           # % packet loss → alert
COMPARE_SEARCH_SECS     = 12.0          # cross-correlation search window (s)
COMPARE_INTERVAL        = 10.0          # how often to re-align (s)
COMPARE_SILENCE_THRESH  = -55.0         # dBFS below which stream counts as silent

# ─── Hub / multi-site constants ───────────────────────────────────────────────

HUB_HEARTBEAT_INTERVAL  = 5.0           # seconds between client→hub pushes
HUB_SITE_TIMEOUT        = 30.0          # seconds before a site is marked offline
HUB_PORT                = 5001          # hub dashboard port
HUB_API_VERSION         = "v1"
HUB_TIMESTAMP_TOLERANCE = 30    # max age (s) of a signed request
HUB_RATE_LIMIT_RPM      = 60    # max heartbeats per minute per site key

# ─── PTP constants ───────────────────────────────────────────────────────────────

PTP_MULTICAST_IP       = "224.0.1.129"
PTP_EVENT_PORT         = 319           # Sync messages
PTP_GENERAL_PORT       = 320           # Follow_Up, Announce messages
PTP_SYNC_TIMEOUT       = 4.0           # seconds before "sync loss" alert
PTP_OFFSET_WARN_US     = 5000          # µs — offset warn threshold (5ms, NTP-realistic)
PTP_OFFSET_ALERT_US    = 50000         # µs — offset alert threshold (50ms, real problem)
PTP_JITTER_WARN_US     = 2000          # µs — jitter warn threshold
PTP_JITTER_ALERT_US    = 10000         # µs — jitter alert threshold
PTP_HISTORY_LEN        = 60            # samples kept for jitter calculation

# ─── Base dir ─────────────────────────────────────────────────────────────────

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_PATH = os.path.join(BASE_DIR, "lwai_config.json")
MODELS_DIR  = os.path.join(BASE_DIR, "ai_models")
STATIC_DIR  = os.path.join(BASE_DIR, "static")
LOGO_FILE   = "signalscope_logo.png"
ICON_FILE   = "signalscope_icon.ico"
ICON_PNG    = "signalscope_icon.png"
os.makedirs(MODELS_DIR, exist_ok=True)

# ─── Config models ────────────────────────────────────────────────────────────

@dataclass
class InputConfig:
    name: str
    device_index: str

    enabled: bool = True
    alert_on_silence: bool = True
    alert_on_hiss:    bool = True
    alert_on_clip:    bool = True
    ai_monitor:       bool = True

    silence_threshold_dbfs: float = -55.0
    silence_min_duration:   float = 3.0
    hiss_hf_band_hz:        float = 6000.0
    hiss_rise_db:           float = 12.0
    hiss_min_duration:      float = 3.0
    clip_threshold_dbfs:    float = -3.0
    clip_window_seconds:    float = 2.0
    clip_count_threshold:   int   = 3
    cascade_parent:          Optional[str] = None
    cascade_suppress_alerts: bool          = False
    compare_peer:            Optional[str] = None
    compare_role:            str           = ""
    compare_gain_alert_db:   float         = 3.0   # alert if gain drifts more than this from baseline
    nowplaying_station_id:   str           = ""   # Planet Radio station rpuid

    # RTP tracking (runtime)
    _rtp_last_seq:      int   = field(default=-1,   init=False, repr=False)
    _rtp_total:         int   = field(default=0,    init=False, repr=False)
    _rtp_lost:          int   = field(default=0,    init=False, repr=False)
    _rtp_loss_pct:      float = field(default=0.0,  init=False, repr=False)
    _rtp_loss_window:   int   = field(default=0,    init=False, repr=False)
    _rtp_total_window:  int   = field(default=0,    init=False, repr=False)
    # DAB status (populated for dab:// sources)
    _dab_snr:           float = field(default=0.0,  init=False, repr=False)
    _dab_sig:           float = field(default=0.0,  init=False, repr=False)
    _dab_ensemble:      str   = field(default="",   init=False, repr=False)
    _dab_service:       str   = field(default="",   init=False, repr=False)
    _dab_ok:            bool  = field(default=False, init=False, repr=False)
    # FM status (populated for fm:// sources)
    _fm_freq_mhz:       float = field(default=0.0,  init=False, repr=False)
    _fm_signal_dbm:     float = field(default=-120.0, init=False, repr=False)
    _fm_snr_db:         float = field(default=0.0,  init=False, repr=False)
    _fm_stereo:         bool  = field(default=False, init=False, repr=False)
    _fm_rds_ps:         str   = field(default="",   init=False, repr=False)
    _fm_rds_rt:         str   = field(default="",   init=False, repr=False)
    _fm_rds_ok:         bool  = field(default=False, init=False, repr=False)
    _fm_rds_status:     str   = field(default="No lock", init=False, repr=False)
    _fm_rds_valid_groups:int  = field(default=0, init=False, repr=False)
    _fm_rds_metric:     float = field(default=0.0, init=False, repr=False)
    _fm_rds_best_phase: int   = field(default=-1, init=False, repr=False)
    _fm_rds_last_good:  float = field(default=0.0, init=False, repr=False)
    _fm_backend:        str   = field(default="",   init=False, repr=False)
    # SLA tracking (runtime)
    _sla_monitored_s:   float = field(default=0.0,  init=False, repr=False)
    _sla_alert_s:       float = field(default=0.0,  init=False, repr=False)
    _sla_events:        List  = field(default_factory=list, init=False, repr=False)
    _sla_month:         str   = field(default="",   init=False, repr=False)

    # runtime state
    _silence_secs:      float = field(default=0.0,    init=False, repr=False)
    _hiss_secs:         float = field(default=0.0,    init=False, repr=False)
    _hf_baseline:       Optional[float] = field(default=None, init=False, repr=False)
    _mid_baseline:      Optional[float] = field(default=None, init=False, repr=False)
    _clip_count:        int   = field(default=0,      init=False, repr=False)
    _clip_window_start: float = field(default=0.0,    init=False, repr=False)
    _last_alerts:       Dict[str,float] = field(default_factory=dict, init=False, repr=False)
    _last_level_dbfs:   float = field(default=-120.0, init=False, repr=False)
    _history:           List[Dict] = field(default_factory=list, init=False, repr=False)
    _audio_buffer:      Optional[object] = field(default=None, init=False, repr=False)
    _stream_buffer:     Optional[object] = field(default=None, init=False, repr=False)
    _livewire_mode:     str  = field(default="",   init=False, repr=False)
    _ai_status:         str  = field(default="",   init=False, repr=False)
    _ai_phase:          str  = field(default="idle", init=False, repr=False)
    _ai_learn_start:    float = field(default=0.0, init=False, repr=False)
    _ai_learn_samples:  List  = field(default_factory=list, init=False, repr=False)
    _ai_last_run:       float = field(default=0.0, init=False, repr=False)
    _ai_error_mean:     float = field(default=0.0, init=False, repr=False)
    _ai_error_std:      float = field(default=1.0, init=False, repr=False)
    _ai_session:        Optional[object] = field(default=None, init=False, repr=False)
    _ai_retrain_flag:   bool  = field(default=False, init=False, repr=False)
    _baseline_learning_remaining: float = field(default=5.0, init=False, repr=False)


@dataclass
class AuthConfig:
    enabled:        bool = False
    username:       str  = "admin"
    password_hash:  str  = ""   # werkzeug pbkdf2 hash (legacy: plain sha256 hex)
    first_login:    bool = True  # force password change on first login


@dataclass
class EmailConfig:
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    use_tls: bool = True
    username: str = ""
    password: str = ""
    from_addr: str = ""
    to_addr: str = ""


@dataclass
class PushoverConfig:
    enabled:    bool = False
    user_key:   str  = ""
    app_token:  str  = ""
    # priority: -2=lowest -1=low 0=normal 1=high 2=emergency
    priority_warn:  int = 0
    priority_alert: int = 1


@dataclass
class WebhookRoute:
    """A single routing rule. Empty filter fields mean 'match all'."""
    name:          str  = ""        # human label, e.g. "Breakfast team"
    url:           str  = ""        # Teams/Slack webhook URL
    teams_style:   bool = True      # True = Adaptive Card, False = plain text
    # Filters — all are OR-within, AND-between (empty = wildcard)
    filter_streams: list = field(default_factory=list)  # stream names, empty = all
    filter_types:   list = field(default_factory=list)  # alert types, empty = all
    filter_severity: str = ""   # "ALERT", "WARN", or "" = both
    filter_sites:   list = field(default_factory=list)  # hub site names, empty = all

    def matches(self, stream: str, alert_type: str, site: str = "") -> bool:
        """Return True if this route should receive the given alert."""
        if self.filter_sites and site and not any(
                s.lower() in site.lower() for s in self.filter_sites if s):
            return False
        if self.filter_streams and not any(
                s.lower() in stream.lower() for s in self.filter_streams if s):
            return False
        if self.filter_types and not any(
                t.lower() in alert_type.lower() for t in self.filter_types if t):
            return False
        if self.filter_severity:
            sev = self.filter_severity.upper()
            if sev == "ALERT" and "WARN" in alert_type.upper() and "ALERT" not in alert_type.upper():
                return False
            if sev == "WARN" and ("ALERT" in alert_type.upper() or
                                   any(x in alert_type.upper() for x in ("SILENCE","CLIP","RTP_LOSS","PTP_LOST","CMP"))):
                return False
        return True


@dataclass
class WebhookConfig:
    enabled:      bool  = False
    url:          str   = ""        # fallback URL — used when no route matches
    teams_style:  bool  = True      # fallback card style
    routes:       list  = field(default_factory=list)  # list of WebhookRoute

    def matching_routes(self, stream: str, alert_type: str, site: str = ""):
        """Return all routes that match. Falls back to default URL if none match."""
        matched = [r for r in self.routes if r.url and r.matches(stream, alert_type, site)]
        if matched:
            return matched
        # Fallback: return a synthetic route using the default URL
        if self.url:
            return [WebhookRoute(name="default", url=self.url, teams_style=self.teams_style)]
        return []


@dataclass
class NetworkConfig:
    audio_interface_ip: str = "0.0.0.0"
    management_interface_ip: str = "0.0.0.0"
    audio_interface_name: str = "any"
    management_interface_name: str = "any"


@dataclass
class HubConfig:
    mode:       str  = "client"   # "client" | "hub" | "both"
    site_name:  str  = ""         # shown on hub dashboard (client mode)
    hub_url:    str  = ""         # e.g. http://1.2.3.4:5001  (client mode)
    secret_key: str  = ""         # shared secret (both sides must match)
    enabled:    bool = False      # hub push enabled?


@dataclass
class SdrDevice:
    """Represents a registered RTL-SDR dongle."""
    serial:   str  = ""        # programmed serial (e.g. "DAB_DONGLE_1")
    role:     str  = "none"    # "dab" | "fm" | "none"
    ppm:      int  = 0         # frequency correction in parts-per-million
    label:    str  = ""        # friendly name shown in UI


@dataclass
class AppConfig:
    inputs:      List[InputConfig] = field(default_factory=list)
    sdr_devices: List[SdrDevice]   = field(default_factory=list)
    email: EmailConfig = field(default_factory=EmailConfig)
    pushover: PushoverConfig = field(default_factory=PushoverConfig)
    webhook: WebhookConfig = field(default_factory=WebhookConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    hub: HubConfig = field(default_factory=HubConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    sla_target_pct:      float = 99.9
    nowplaying_country:  str   = "GB"
    alert_wav_duration:  float = 5.0
    daily_report_time:   str   = "06:00"  # HH:MM local
    alert_log_max:       int   = 10000    # max events kept in alert_log.json
    clip_max_age_days:   int   = 30       # 0 = keep forever
    clip_max_per_stream: int   = 200      # 0 = unlimited
    # PTP thresholds (µs) — configurable so passive observers can tune
    ptp_offset_warn_us:  int = 5000
    ptp_offset_alert_us: int = 50000
    ptp_jitter_warn_us:  int = 2000
    ptp_jitter_alert_us: int = 10000
    # First-run wizard
    wizard_done:   bool = False   # True once setup wizard has been completed
    # HTTPS / Let's Encrypt
    tls_domain:    str  = ""     # FQDN for Let's Encrypt cert
    tls_cert_path: str  = ""     # path to fullchain.pem
    tls_key_path:  str  = ""     # path to privkey.pem
    tls_enabled:   bool = False  # serve HTTPS instead of HTTP
    # Login security
    login_max_attempts: int   = 10    # lockout after N failures
    login_lockout_mins: int   = 15    # lockout duration in minutes
    session_timeout_hrs: int  = 12    # hours before session expires

# ─── Config persistence ───────────────────────────────────────────────────────

def _harden_config_permissions():
    """Restrict config file permissions to owner-read-only on Linux.
    Prevents other users on the same system reading plaintext credentials.
    """
    import stat
    if os.path.exists(CONFIG_PATH):
        try:
            os.chmod(CONFIG_PATH, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
        except Exception:
            pass  # Windows or permission error — non-fatal


def load_config() -> AppConfig:
    if not os.path.exists(CONFIG_PATH):
        cfg = AppConfig(); save_config(cfg); return cfg
    _harden_config_permissions()
    try:
        with open(CONFIG_PATH) as f:
            raw = json.load(f)
    except Exception as e:
        print(f"[CONFIG] Load failed ({e}), using defaults.")
        return AppConfig()

    # Load SDR devices
    sdr_devices = []
    for d in raw.get("sdr_devices", []):
        sdr_devices.append(SdrDevice(
            serial=d.get("serial",""),
            role=d.get("role","none"),
            ppm=int(d.get("ppm",0)),
            label=d.get("label",""),
        ))

    inputs = []
    for item in raw.get("inputs", []):
        inputs.append(InputConfig(
            name=item["name"], device_index=str(item["device_index"]),
            enabled=item.get("enabled", True),
            alert_on_silence=item.get("alert_on_silence", True),
            alert_on_hiss=item.get("alert_on_hiss", True),
            alert_on_clip=item.get("alert_on_clip", True),
            ai_monitor=item.get("ai_monitor", True),
            silence_threshold_dbfs=item.get("silence_threshold_dbfs", -55.0),
            silence_min_duration=item.get("silence_min_duration", 3.0),
            hiss_hf_band_hz=item.get("hiss_hf_band_hz", 6000.0),
            hiss_rise_db=item.get("hiss_rise_db", 12.0),
            hiss_min_duration=item.get("hiss_min_duration", 3.0),
            clip_threshold_dbfs=item.get("clip_threshold_dbfs", -3.0),
            clip_window_seconds=item.get("clip_window_seconds", 2.0),
            clip_count_threshold=item.get("clip_count_threshold", 3),
            cascade_parent=item.get("cascade_parent"),
            cascade_suppress_alerts=item.get("cascade_suppress_alerts", False),
            compare_peer=item.get("compare_peer"),
            compare_role=item.get("compare_role",""),
            compare_gain_alert_db=float(item.get("compare_gain_alert_db", 3.0)),
            nowplaying_station_id=item.get("nowplaying_station_id",""),
        ))
    e = raw.get("email", {}); w = raw.get("webhook", {}); n = raw.get("network", {}); h = raw.get("hub", {}); pv = raw.get("pushover", {}); au = raw.get("auth", {})
    return AppConfig(
        inputs=inputs,
        sdr_devices=sdr_devices,
        email=EmailConfig(
            enabled=e.get("enabled",False), smtp_host=e.get("smtp_host",""),
            smtp_port=e.get("smtp_port",587), use_tls=e.get("use_tls",True),
            username=e.get("username",""), password=e.get("password",""),
            from_addr=e.get("from_addr",""), to_addr=e.get("to_addr",""),
        ),
        webhook=WebhookConfig(
            enabled=w.get("enabled", False),
            url=w.get("url", ""),
            teams_style=w.get("teams_style", True),
            routes=[WebhookRoute(
                name=r.get("name",""),
                url=r.get("url",""),
                teams_style=r.get("teams_style", True),
                filter_streams=r.get("filter_streams",[]),
                filter_types=r.get("filter_types",[]),
                filter_severity=r.get("filter_severity",""),
                filter_sites=r.get("filter_sites",[]),
            ) for r in w.get("routes",[])],
        ),
        pushover=PushoverConfig(
            enabled=pv.get("enabled",False), user_key=pv.get("user_key",""),
            app_token=pv.get("app_token",""),
            priority_warn=pv.get("priority_warn",0), priority_alert=pv.get("priority_alert",1),
        ),
        network=NetworkConfig(
            audio_interface_ip=n.get("audio_interface_ip","0.0.0.0"),
            management_interface_ip=n.get("management_interface_ip","0.0.0.0"),
            audio_interface_name=n.get("audio_interface_name","any"),
            management_interface_name=n.get("management_interface_name","any"),
        ),
        hub=HubConfig(
            mode=h.get("mode","client"), site_name=h.get("site_name",""),
            hub_url=h.get("hub_url",""), secret_key=h.get("secret_key",""),
            enabled=h.get("enabled",False),
        ),
        auth=AuthConfig(
            enabled=au.get("enabled",False), username=au.get("username","admin"),
            password_hash=au.get("password_hash",""), first_login=au.get("first_login",True),
        ),
        sla_target_pct=raw.get("sla_target_pct", 99.9),
        nowplaying_country=raw.get("nowplaying_country","GB"),
        alert_wav_duration=raw.get("alert_wav_duration", 5.0),
        daily_report_time=raw.get("daily_report_time","06:00"),
        alert_log_max=int(raw.get("alert_log_max",10000)),
        clip_max_age_days=int(raw.get("clip_max_age_days",30)),
        clip_max_per_stream=int(raw.get("clip_max_per_stream",200)),
        ptp_offset_warn_us=int(raw.get("ptp_offset_warn_us",5000)),
        ptp_offset_alert_us=int(raw.get("ptp_offset_alert_us",50000)),
        ptp_jitter_warn_us=int(raw.get("ptp_jitter_warn_us",2000)),
        ptp_jitter_alert_us=int(raw.get("ptp_jitter_alert_us",10000)),
        wizard_done=raw.get("wizard_done", False),
        tls_domain=raw.get("tls_domain",""),
        tls_cert_path=raw.get("tls_cert_path",""),
        tls_key_path=raw.get("tls_key_path",""),
        tls_enabled=raw.get("tls_enabled",False),
        login_max_attempts=int(raw.get("login_max_attempts",10)),
        login_lockout_mins=int(raw.get("login_lockout_mins",15)),
        session_timeout_hrs=int(raw.get("session_timeout_hrs",12)),
    )


def save_config(cfg: AppConfig):
    data = {
        "alert_wav_duration": cfg.alert_wav_duration,
        "daily_report_time": cfg.daily_report_time,
        "alert_log_max": cfg.alert_log_max,
        "clip_max_age_days": cfg.clip_max_age_days,
        "clip_max_per_stream": cfg.clip_max_per_stream,
        "ptp_offset_warn_us": cfg.ptp_offset_warn_us,
        "ptp_offset_alert_us": cfg.ptp_offset_alert_us,
        "ptp_jitter_warn_us": cfg.ptp_jitter_warn_us,
        "ptp_jitter_alert_us": cfg.ptp_jitter_alert_us,
        "wizard_done": cfg.wizard_done,
        "tls_domain": cfg.tls_domain, "tls_cert_path": cfg.tls_cert_path,
        "tls_key_path": cfg.tls_key_path, "tls_enabled": cfg.tls_enabled,
        "login_max_attempts": cfg.login_max_attempts,
        "login_lockout_mins": cfg.login_lockout_mins,
        "session_timeout_hrs": cfg.session_timeout_hrs,
        "sdr_devices": [
            {"serial": d.serial, "role": d.role, "ppm": d.ppm, "label": d.label}
            for d in cfg.sdr_devices
        ],
        "inputs": [{
            "name": i.name, "device_index": i.device_index, "enabled": i.enabled,
            "alert_on_silence": i.alert_on_silence, "alert_on_hiss": i.alert_on_hiss,
            "alert_on_clip": i.alert_on_clip, "ai_monitor": i.ai_monitor,
            "silence_threshold_dbfs": i.silence_threshold_dbfs,
            "silence_min_duration": i.silence_min_duration,
            "hiss_hf_band_hz": i.hiss_hf_band_hz, "hiss_rise_db": i.hiss_rise_db,
            "hiss_min_duration": i.hiss_min_duration,
            "clip_threshold_dbfs": i.clip_threshold_dbfs,
            "clip_window_seconds": i.clip_window_seconds,
            "clip_count_threshold": i.clip_count_threshold,
            "cascade_parent": i.cascade_parent,
            "cascade_suppress_alerts": i.cascade_suppress_alerts,
            "compare_peer": i.compare_peer,
            "compare_role": i.compare_role,
            "compare_gain_alert_db": i.compare_gain_alert_db,
            "nowplaying_station_id": i.nowplaying_station_id,
        } for i in cfg.inputs],
        "email": {
            "enabled": cfg.email.enabled, "smtp_host": cfg.email.smtp_host,
            "smtp_port": cfg.email.smtp_port, "use_tls": cfg.email.use_tls,
            "username": cfg.email.username, "password": cfg.email.password,
            "from_addr": cfg.email.from_addr, "to_addr": cfg.email.to_addr,
        },
        "webhook": {
            "enabled":     cfg.webhook.enabled,
            "url":         cfg.webhook.url,
            "teams_style": cfg.webhook.teams_style,
            "routes": [
                {"name": r.name, "url": r.url, "teams_style": r.teams_style,
                 "filter_streams": r.filter_streams, "filter_types": r.filter_types,
                 "filter_severity": r.filter_severity, "filter_sites": r.filter_sites}
                for r in cfg.webhook.routes
            ],
        },
        "pushover": {
            "enabled": cfg.pushover.enabled, "user_key": cfg.pushover.user_key,
            "app_token": cfg.pushover.app_token,
            "priority_warn": cfg.pushover.priority_warn,
            "priority_alert": cfg.pushover.priority_alert,
        },
        "network": {
            "audio_interface_ip": cfg.network.audio_interface_ip,
            "management_interface_ip": cfg.network.management_interface_ip,
            "audio_interface_name": cfg.network.audio_interface_name,
            "management_interface_name": cfg.network.management_interface_name,
        },
        "hub": {
            "mode": cfg.hub.mode, "site_name": cfg.hub.site_name,
            "hub_url": cfg.hub.hub_url, "secret_key": cfg.hub.secret_key,
            "enabled": cfg.hub.enabled,
        },
        "auth": {
            "enabled": cfg.auth.enabled, "username": cfg.auth.username,
            "password_hash": cfg.auth.password_hash, "first_login": cfg.auth.first_login,
        },
        "sla_target_pct": cfg.sla_target_pct,
        "nowplaying_country": cfg.nowplaying_country,
    }
    with open(CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)
    _harden_config_permissions()

# ─── Helpers ──────────────────────────────────────────────────────────────────

def dbfs(rms: float) -> float:
    return 20.0 * math.log10(max(float(rms), 1e-10))

def _safe_name(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in "-_").strip() or "stream"

def _model_path(name: str) -> str:
    return os.path.join(MODELS_DIR, f"{_safe_name(name)}.onnx")

def _stats_path(name: str) -> str:
    return os.path.join(MODELS_DIR, f"{_safe_name(name)}_stats.json")

ALERT_LOG_PATH  = os.path.join(BASE_DIR, "alert_log.json")
HUB_STATE_PATH  = os.path.join(BASE_DIR, "hub_state.json")  # persists site data across hub restarts
_alert_log_lock = threading.Lock()

def _alert_log_append(event: dict):
    """Append one event to the alert log, pruning if over the configured limit."""
    try:
        with _alert_log_lock:
            with open(ALERT_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(event) + "\n")
        # Prune if over limit (read current size without the lock held)
        _alert_log_prune()
    except Exception:
        pass

_alert_log_last_prune = 0.0

def _alert_log_prune():
    """Trim alert_log.json to cfg.alert_log_max lines. Runs at most once per minute."""
    global _alert_log_last_prune
    if time.time() - _alert_log_last_prune < 60:
        return
    try:
        cfg = monitor.app_cfg
        limit = cfg.alert_log_max if cfg.alert_log_max > 0 else 10000
        with _alert_log_lock:
            if not os.path.exists(ALERT_LOG_PATH): return
            with open(ALERT_LOG_PATH, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) > limit:
                with open(ALERT_LOG_PATH, "w", encoding="utf-8") as f:
                    f.writelines(lines[-limit:])
        _alert_log_last_prune = time.time()
    except Exception:
        pass

def _alert_log_load(limit: int = 2000) -> List[dict]:
    """Read the last `limit` events from the alert log."""
    if not os.path.exists(ALERT_LOG_PATH):
        return []
    try:
        with _alert_log_lock:
            with open(ALERT_LOG_PATH, "r", encoding="utf-8") as f:
                lines = f.readlines()
        events = []
        for line in lines[-limit:]:
            line = line.strip()
            if line:
                try: events.append(json.loads(line))
                except: pass
        return list(reversed(events))   # newest first
    except Exception:
        return []

def _add_history(cfg: InputConfig, kind: str, msg: str, clip_path: str = ""):
    """Append a rich event record to per-input history and persistent alert log."""
    ptp = monitor.ptp if (monitor and hasattr(monitor, 'ptp')) else None
    event = {
        "ts":            time.strftime("%Y-%m-%d %H:%M:%S"),
        "stream":        cfg.name,
        "type":          kind,
        "message":       msg,
        "level_dbfs":    round(cfg._last_level_dbfs, 1),
        "rtp_loss_pct":  round(cfg._rtp_loss_pct, 2),
        "clip":          os.path.basename(clip_path) if clip_path else "",
        "ptp_state":     ptp.state       if ptp else "",
        "ptp_offset_us": round(ptp.offset_us, 1)  if ptp else 0,
        "ptp_drift_us":  round(ptp.drift_us,  1)  if ptp else 0,
        "ptp_jitter_us": round(ptp.jitter_us, 1)  if ptp else 0,
        "ptp_gm":        ptp.gm_id       if ptp else "",
    }
    cfg._history.append(event)
    if len(cfg._history) > 300:
        cfg._history = cfg._history[-300:]
    _alert_log_append(event)

# ─── Auth helpers ─────────────────────────────────────────────────────────────

def _safe_next() -> str:
    """Return the next= redirect target only if it is a safe relative path.
    Prevents open redirect attacks via crafted login URLs.
    """
    from urllib.parse import urlparse
    target = request.args.get("next", "")
    if not target:
        return url_for("index")
    parsed = urlparse(target)
    # Reject anything with a scheme or netloc — must be relative path only
    if parsed.scheme or parsed.netloc:
        _log_security(f"Open redirect attempt blocked: {target!r} from {request.remote_addr}")
        return url_for("index")
    return target


def _hash_password(pw: str) -> str:
    """Hash a password using werkzeug pbkdf2-sha256 (salted, slow, safe)."""
    from werkzeug.security import generate_password_hash
    return generate_password_hash(pw, method="pbkdf2:sha256:260000")


def _check_password(pw: str, hashed: str) -> bool:
    """Verify a password against its hash.
    Handles both new werkzeug pbkdf2 hashes and legacy plain SHA-256 hashes
    so existing users are not locked out on upgrade. Legacy hashes are
    automatically re-hashed with pbkdf2 on next successful login.
    """
    from werkzeug.security import check_password_hash
    # New-style: werkzeug hash (starts with "pbkdf2:" or "scrypt:" etc.)
    if hashed.startswith("pbkdf2:") or hashed.startswith("scrypt:"):
        return check_password_hash(hashed, pw)
    # Legacy: plain SHA-256 hex (64 lowercase hex chars)
    if len(hashed) == 64 and all(c in "0123456789abcdef" for c in hashed):
        return hashlib.sha256(pw.encode()).hexdigest() == hashed
    return False

# ─── CSRF protection ─────────────────────────────────────────────────────────

def _csrf_token() -> str:
    """Generate (or retrieve) a per-session CSRF token."""
    from flask import session
    if "_csrf" not in session:
        session["_csrf"] = hashlib.sha256(os.urandom(32)).hexdigest()
    return session["_csrf"]


def _csrf_valid() -> bool:
    """Check the CSRF token submitted with a POST request."""
    from flask import session, request
    token = session.get("_csrf", "")
    if not token:
        return False
    # Accept token from form field OR X-CSRFToken header (for AJAX)
    submitted = request.form.get("_csrf_token","") or request.headers.get("X-CSRFToken","")
    return _hmac.compare_digest(token, submitted)


def csrf_protect(f):
    """Decorator that validates CSRF token on POST/PUT/DELETE requests."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        from flask import session
        cfg = monitor.app_cfg
        # Only enforce when auth is enabled — unauthenticated instances are
        # LAN-only so CSRF is less critical, but still generate tokens.
        # On some first-run / clean-install flows auth is enabled mid-wizard,
        # and the browser can legitimately submit a valid-looking CSRF token
        # before the session has persisted one yet. In that narrow case, if the
        # user is already authenticated, adopt the submitted token as the
        # session token for this browser session.
        if request.method in ("POST","PUT","DELETE","PATCH"):
            if cfg.auth.enabled:
                if not session.get("_csrf") and session.get("logged_in"):
                    submitted = request.form.get("_csrf_token","") or request.headers.get("X-CSRFToken","")
                    if submitted:
                        session["_csrf"] = submitted
                if not _csrf_valid():
                    _log_security(f"CSRF validation failed for {request.path} from {request.remote_addr}")
                    return jsonify({"error": "CSRF validation failed"}), 403
        return f(*args, **kwargs)
    return decorated


def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        from flask import session
        cfg = monitor.app_cfg

        # During first-run setup, allow the setup wizard and setup API calls to
        # complete even if authentication has just been enabled mid-wizard.
        # Otherwise the final /setup/complete POST can be bounced to /login
        # before wizard_done is written, which causes the setup loop.
        if (not cfg.wizard_done) and (
            request.path.startswith("/setup") or request.path.startswith("/api/setup/")
        ):
            return f(*args, **kwargs)

        if not cfg.auth.enabled:
            return f(*args, **kwargs)
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        # Session timeout check
        timeout_hrs = cfg.session_timeout_hrs or 12
        login_ts = session.get("login_ts", 0)
        if time.time() - login_ts > timeout_hrs * 3600:
            session.clear()
            flash(f"Session expired after {timeout_hrs}h. Please log in again.")
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated


# ── Per-request CSP nonce ────────────────────────────────────────────────────
def _compute_csp_hashes() -> str:
    """Compute SHA-256 hashes of all inline event handler values found in
    all templates. Called once at startup so the CSP hash list is always
    in sync with the actual template content — no manual updates needed.
    Uses two passes: double-quoted attributes (may contain single quotes)
    and single-quoted attributes (no single quotes inside)."""
    import re as _re, hashlib as _hl, base64 as _b64
    src_text = open(__file__, encoding="utf-8").read()
    hashes = set()
    for tpl in _re.findall(r'[A-Z_]+_TPL\s*=\s*r"""(.*?)"""', src_text, _re.DOTALL):
        # Double-quoted: onclick="st('notif')" — single quotes allowed inside
        for handler in _re.findall(r'on(?:click|change)="([^"]+)"', tpl):
            digest = _hl.sha256(handler.encode()).digest()
            hashes.add(f"'sha256-{_b64.b64encode(digest).decode()}'")
        # Single-quoted: onclick='simpleFunc()' — no inner single quotes
        for handler in _re.findall(r"on(?:click|change)='([^']+)'", tpl):
            digest = _hl.sha256(handler.encode()).digest()
            hashes.add(f"'sha256-{_b64.b64encode(digest).decode()}'")
    return " ".join(sorted(hashes))

_CSP_HANDLER_HASHES = _compute_csp_hashes()


def _csp_nonce() -> str:
    """Return (or generate) the CSP nonce for the current request.
    Stored in Flask's request context so it's consistent within one response.
    """
    from flask import g
    if not hasattr(g, "_csp_nonce"):
        g._csp_nonce = base64.b64encode(os.urandom(16)).decode()
    return g._csp_nonce


def _apply_security_headers(response):
    """Add security headers to all HTML responses."""
    cfg = monitor.app_cfg
    response.headers["X-Frame-Options"]        = "SAMEORIGIN"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"]         = "strict-origin-when-cross-origin"
    # X-XSS-Protection is legacy — CSP is the modern replacement
    # Only set CSP on HTML responses to avoid breaking JSON/audio/binary endpoints
    ct = response.content_type or ""
    if "html" in ct:
        nonce = _csp_nonce()
        csp = (
            "default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}'; "
            "style-src 'self' 'unsafe-inline'; "
            # unsafe-inline for style= attributes; nonce covers <style nonce="{{csp_nonce()}}"> blocks
            f"script-src-attr 'unsafe-hashes' {_CSP_HANDLER_HASHES}; "
            "img-src 'self' data: https://listenapi.planetradio.co.uk; "
            "media-src 'self' blob:; "
            "connect-src 'self'; "
            "font-src 'self'; "
            "frame-ancestors 'self'; "
            "form-action 'self'"
        )
        response.headers["Content-Security-Policy"] = csp
    if cfg.tls_enabled:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

# ─── Now Playing poller ───────────────────────────────────────────────────────

def _normalize_nowplaying_artwork_url(url: str) -> str:
    url = str(url or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://"):
        return "https://" + url[len("http://"):]
    return url

def _fetch_nowplaying_artwork_bytes(url: str):
    url = _normalize_nowplaying_artwork_url(url)
    if not url:
        return None, None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SignalScope/2.5.37"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
            ctype = resp.headers.get_content_type() or "image/jpeg"
        return data, ctype
    except Exception:
        return None, None

class NowPlayingPoller:
    """Polls Planet Radio API every 30s and caches results."""
    INTERVAL = 30.0

    def __init__(self):
        self._stations: List[dict] = []
        self._nowplaying: dict = {}
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._country = "GB"
        self._last_error = ""
        self._last_raw_keys: List[str] = []
        self._last_fetch_at = ""

    def start(self, country="GB"):
        self._country = country
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="NowPlaying")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def get_stations(self) -> List[dict]:
        with self._lock: return list(self._stations)

    def get_nowplaying(self, rpuid: str) -> dict:
        with self._lock: return dict(self._nowplaying.get(rpuid, {}))

    # Known Planet Radio station codes → human readable names
    STATION_NAMES = {
        "abr": "Absolute Radio", "ab7": "Absolute Radio 70s", "ab8": "Absolute Radio 80s (Scotland)",
        "ab9": "Absolute Radio 90s (Scotland)", "ab0": "Absolute Radio 00s (Scotland)",
        "ab1": "Absolute Radio 10s", "ab2": "Absolute Radio 20s",
        "abc": "Absolute Radio Classic Rock", "abk": "Absolute Radio UK",
        "cl1": "Clyde 1", "cay": "Clyde 2", "coo": "Cool FM",
        "cwf": "Country Hits Radio", "dra": "Downtown Radio",
        "dco": "Downtown Country", "fo1": "Forth 1", "fo2": "Forth 2",
        "gh0": "Greatest Hits Radio", "gnw": "Greatest Hits Radio (Network)",
        "g6s": "Greatest Hits Radio 60s", "70s": "Greatest Hits Radio 70s",
        "80s": "Greatest Hits Radio 80s", "ghz": "Greatest Hits Radio (Scotland)",
        "gni": "Greatest Hits Radio (NI)", "ghm": "Greatest Hits Radio (Manchester)",
        "gde": "Greatest Hits Radio (Devon)", "ghg": "Greatest Hits Radio (Glasgow)",
        "ghd": "Greatest Hits Radio (Dundee)", "ghn": "Greatest Hits Radio (Newcastle)",
        "ghp": "Greatest Hits Radio (Plymouth)", "gwd": "Greatest Hits Radio (Wales)",
        "htr": "Heat Radio", "hit": "Hits Radio", "hed": "Hits Radio (Edinburgh)",
        "h90": "Hits Radio 90s", "h00": "Hits Radio 00s", "hch": "Hits Radio Chilled",
        "hi4": "Hits Radio Pride", "hrt": "Hits Radio (Manchester)",
        "hra": "Hits Radio (Aberdeen)", "hrk": "Hits Radio (Kent)",
        "hrb": "Hits Radio Bauer", "hrq": "Hits Radio (Leeds)",
        "jaz": "Jazz FM", "ker": "Kerrang! Radio",
        "ki1": "KISS", "ki2": "KISS Dance", "ki3": "KISS Fresh",
        "ki5": "KISS Extra", "ktb": "KISSTORY",
        "mag": "Magic", "mel": "Magic Mellow", "mso": "Magic Soul",
        "mmu": "Magic at the Movies", "mrs": "Magic Radio Scotland",
        "mf1": "Metro Radio", "no1": "Nation Radio Scotland",
        "pln": "Planet Rock", "ta1": "TFM",
    }

    def _fetch(self):
        url = f"https://listenapi.planetradio.co.uk/api9.2/stations_nowplaying/{self._country}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "SignalScope/2.5.37"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw_bytes = resp.read()
            data = json.loads(raw_bytes.decode())
            self._last_fetch_at = time.strftime("%H:%M:%S")

            items = data if isinstance(data, list) else data.get("data", data.get("stations", [data]))
            if items:
                self._last_raw_keys = list(items[0].keys()) if isinstance(items[0], dict) else []

            stations = []
            nowplaying = {}
            for s in items:
                if not isinstance(s, dict): continue
                code = str(s.get("stationCode","")).strip()
                if not code: continue
                # Use known name map, fall back to uppercased code
                name = self.STATION_NAMES.get(code, code.upper())
                stations.append({"rpuid": code, "name": name})

                np  = s.get("stationNowPlaying") or {}
                air = s.get("stationOnAir") or {}
                nowplaying[code] = {
                    "artist":  str(np.get("nowPlayingArtist","")).strip(),
                    "title":   str(np.get("nowPlayingTrack","")).strip(),
                    "show":    str(air.get("episodeTitle","")).strip(),
                    "artwork": _normalize_nowplaying_artwork_url(np.get("nowPlayingImage","")),
                }

            with self._lock:
                if stations:
                    self._stations  = sorted(stations, key=lambda x: x["name"])
                    self._nowplaying = nowplaying
                    self._last_error = ""
                else:
                    self._last_error = f"0 stations parsed. Keys: {self._last_raw_keys}"

        except Exception as e:
            self._last_error = f"{type(e).__name__}: {e}"

    def _loop(self):
        self._fetch()
        while not self._stop.wait(self.INTERVAL):
            self._fetch()

nowplaying_poller = NowPlayingPoller()

# ─── SLA tracker ─────────────────────────────────────────────────────────────

SLA_PATH = os.path.join(BASE_DIR, "sla_data.json")

def _sla_load() -> dict:
    try:
        if os.path.exists(SLA_PATH):
            with open(SLA_PATH) as f: return json.load(f)
    except: pass
    return {}

def _sla_save(data: dict):
    try:
        with open(SLA_PATH,"w") as f: json.dump(data, f, indent=2)
    except: pass

def _sla_update(cfg: InputConfig, elapsed_s: float, in_alert: bool):
    """Called every chunk from analyse_chunk to accumulate SLA data."""
    month = time.strftime("%Y-%m")
    if cfg._sla_month != month:
        cfg._sla_month = month
        cfg._sla_monitored_s = 0.0
        cfg._sla_alert_s     = 0.0
        cfg._sla_events      = []
    cfg._sla_monitored_s += elapsed_s
    if in_alert: cfg._sla_alert_s += elapsed_s

def sla_pct(cfg: InputConfig) -> float:
    if cfg._sla_monitored_s < 1.0: return 100.0
    return 100.0 * (1.0 - cfg._sla_alert_s / cfg._sla_monitored_s)
    if len(cfg._history) > 300:
        cfg._history = cfg._history[-300:]

def _make_wav_bytes(audio: np.ndarray) -> bytes:
    pcm = (np.clip(audio.astype(np.float32), -1.0, 1.0) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE); wf.writeframes(pcm.tobytes())
    return buf.getvalue()

def _safe_label(label: str) -> str:
    """Strip any characters that are not safe in a Windows/Linux filename."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in label).strip("_")[:40]

def _clip_cleanup(stream_name: str):
    """Prune old/excess clips for a stream based on config limits."""
    try:
        cfg = monitor.app_cfg
        max_age  = cfg.clip_max_age_days
        max_clip = cfg.clip_max_per_stream
        clip_dir = os.path.join(BASE_DIR, "alert_snippets", _safe_name(stream_name))
        if not os.path.exists(clip_dir): return
        clips = sorted(
            [os.path.join(clip_dir, f) for f in os.listdir(clip_dir) if f.endswith(".wav")],
            key=os.path.getmtime
        )
        # Prune by age
        if max_age > 0:
            cutoff = time.time() - max_age * 86400
            for p in clips:
                if os.path.getmtime(p) < cutoff:
                    try: os.remove(p)
                    except: pass
                else: break
            clips = [p for p in clips if os.path.exists(p)]
        # Prune by count
        if max_clip > 0 and len(clips) > max_clip:
            for p in clips[:len(clips) - max_clip]:
                try: os.remove(p)
                except: pass
    except Exception:
        pass

def _save_alert_wav(cfg: InputConfig, label: str, duration: float = 5.0) -> Optional[str]:
    # Prune old clips before saving a new one
    threading.Thread(target=_clip_cleanup, args=(cfg.name,), daemon=True).start()
    out = os.path.join(BASE_DIR, "alert_snippets")
    os.makedirs(out, exist_ok=True)
    if not cfg._audio_buffer: return None
    chunks = list(cfg._audio_buffer)
    if not chunks: return None
    audio = np.concatenate(chunks)[-int(SAMPLE_RATE * duration):]
    safe_lbl = _safe_label(label)
    path = os.path.join(out, f"{time.strftime('%Y%m%d-%H%M%S')}_{_safe_name(cfg.name)}_{safe_lbl}.wav")
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    try:
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE); wf.writeframes(pcm.tobytes())
        return path
    except Exception as e:
        print(f"[WARN] Could not save alert WAV: {e}")
        return None

# ─── Feature extraction ───────────────────────────────────────────────────────

def extract_features(audio: np.ndarray) -> np.ndarray:
    """Extract 14 content-agnostic features for the autoencoder.

    Key design principle: features must be STABLE across normal content variation
    (music vs speech vs jingles) and only deviate on genuine faults.

    Removed from v1: raw spectral band ratios (sub/lo/mid/hi/air) — these shift
    dramatically between music and speech and cause constant false positives.
    Kept: level, dynamics, DC, clipping, spectral flatness (fault indicator),
    rolloff variance (content-stable shape descriptor), and noise floor estimate.
    """
    n = len(audio)
    if n < 64:
        return np.zeros(AI_FEATURE_DIM, dtype=np.float32)

    rms  = float(np.sqrt(np.mean(audio**2) + 1e-12))
    peak = float(np.max(np.abs(audio)))
    lvl  = float(np.clip((dbfs(rms)  + 80.0) / 80.0, 0.0, 1.0))
    pk   = float(np.clip((dbfs(peak) + 80.0) / 80.0, 0.0, 1.0))
    # Crest factor: high = transient/music, moderate = speech, very high = distortion
    crest = float(np.clip(peak / (rms + 1e-10) / 30.0, 0.0, 1.0))
    dc    = float(np.clip(abs(np.mean(audio)) / 0.05, 0.0, 1.0))   # DC offset (fault)
    clip_frac = float(np.clip(np.mean(np.abs(audio) >= 0.98) / 0.01, 0.0, 1.0))  # clipping

    # Short-term level variance (dropout / intermittent fault detection)
    frame = 480; nf = n // frame
    if nf > 1:
        frames = audio[:nf*frame].reshape(nf, frame)
        frms   = np.sqrt(np.mean(frames**2, axis=1) + 1e-12)
        rms_var  = float(np.clip(np.std(frms) / (np.mean(frms) + 1e-12) / 2.0, 0.0, 1.0))
        # Minimum sub-frame level — detects dropout within a chunk even if average is OK
        min_frm  = float(np.clip((dbfs(float(np.min(frms))) + 80.0) / 80.0, 0.0, 1.0))
    else:
        rms_var = 0.0; min_frm = lvl

    # Spectral features — computed on log-PSD to be more content-agnostic
    win  = np.hanning(n).astype(np.float32)
    X    = np.abs(np.fft.rfft(audio * win))
    freq = np.fft.rfftfreq(n, d=1.0/SAMPLE_RATE)
    psd  = (X**2).astype(np.float64)
    tot  = float(np.sum(psd) + 1e-12)

    # Spectral flatness: near 0 = tonal/sine wave or silence, near 1 = white noise.
    # Genuine faults (tone injection, hiss) push this to extremes. Normal content is stable.
    psd_nz = np.maximum(psd[psd > 0], 1e-20)
    flat   = float(np.clip(
        np.exp(np.mean(np.log(psd_nz))) / (np.mean(psd_nz) + 1e-12), 0.0, 1.0))

    # 85th-percentile rolloff frequency — content type shifts this but it's
    # still useful as a fault indicator when normalised to the stream's own baseline.
    cum     = np.cumsum(psd)
    ri      = np.searchsorted(cum, 0.85 * tot)
    rolloff = float(freq[min(ri, len(freq)-1)]) / 24000.0

    # Noise floor estimate: median of the bottom 10% of spectral bins (fault = elevated)
    low_psd    = np.sort(psd)[:max(1, len(psd)//10)]
    noise_floor= float(np.clip(10.0 * np.log10(np.mean(low_psd) + 1e-20) / -100.0, 0.0, 1.0))

    # Very low frequency energy ratio (hum / mains buzz detector)
    hum = float(np.sum(psd[(freq >= 40) & (freq < 130)]) / tot)
    hum = float(np.clip(hum / 0.3, 0.0, 1.0))

    # High-frequency energy ratio (hiss detector) — normalised so normal HF doesn't trigger
    hf  = float(np.sum(psd[freq >= 10000]) / tot)
    hf  = float(np.clip(hf / 0.5, 0.0, 1.0))

    # Zero-crossing rate — very high = distortion/clipping artefact, very low = silence/DC
    zcr = float(np.mean(np.diff(np.sign(audio)) != 0))

    return np.array([
        lvl, pk, crest, dc, clip_frac,      # [0-4]  level / dynamics / fault indicators
        rms_var, min_frm,                    # [5-6]  short-term stability
        flat, rolloff, noise_floor,          # [7-9]  spectral shape (content-agnostic)
        hum, hf, zcr,                        # [10-12] specific fault signatures
        lvl * (1.0 - flat),                  # [13]   active signal sanity (0 if silence or pure noise)
    ], dtype=np.float32)

# ─── Autoencoder: train (pure numpy Adam) + export as ONNX ────────────────────

def _relu(x): return np.maximum(0.0, x)


def _train_autoencoder(samples: np.ndarray, log_fn):
    N = len(samples)
    log_fn(f"[AI] Training autoencoder on {N} samples…")
    rng = np.random.default_rng(42)
    W1 = rng.normal(0, 0.1, (AI_FEATURE_DIM, AI_HIDDEN_DIM)).astype(np.float32)
    b1 = np.zeros(AI_HIDDEN_DIM, dtype=np.float32)
    W2 = rng.normal(0, 0.1, (AI_HIDDEN_DIM, AI_FEATURE_DIM)).astype(np.float32)
    b2 = np.zeros(AI_FEATURE_DIM, dtype=np.float32)

    lr=1e-3; beta1=0.9; beta2=0.999; eps=1e-8; t=0
    ms = [np.zeros_like(p) for p in (W1,b1,W2,b2)]
    vs = [np.zeros_like(p) for p in (W1,b1,W2,b2)]
    idx = np.arange(N); bsz = min(64, N)

    for ep in range(200):
        rng.shuffle(idx)
        total = 0.0
        for s in range(0, N, bsz):
            batch = samples[idx[s:s+bsz]]; B = len(batch)
            h_pre = batch @ W1 + b1
            h     = _relu(h_pre)
            out   = h @ W2 + b2
            diff  = out - batch
            loss  = float(np.mean(diff**2)); total += loss
            t += 1
            dout = (2.0/(B*AI_FEATURE_DIM)) * diff
            dW2  = h.T @ dout;    db2 = dout.sum(0)
            dh   = dout @ W2.T;   dh_pre = dh * (h_pre > 0).astype(np.float32)
            dW1  = batch.T @ dh_pre; db1 = dh_pre.sum(0)
            for i,(p,g) in enumerate(zip([W1,b1,W2,b2],[dW1,db1,dW2,db2])):
                ms[i] = beta1*ms[i] + (1-beta1)*g
                vs[i] = beta2*vs[i] + (1-beta2)*g**2
                mh = ms[i]/(1-beta1**t); vh = vs[i]/(1-beta2**t)
                p -= lr * mh / (np.sqrt(vh)+eps)
        if ep % 50 == 0:
            log_fn(f"[AI]   epoch {ep:3d}  loss={total:.5f}")

    log_fn("[AI] Training complete.")
    return W1, b1, W2, b2


def _build_onnx(W1, b1, W2, b2) -> bytes:
    """
    Build ONNX model bytes using the onnx library.
    onnxruntime ships with onnx as a dependency so this is always available
    when onnxruntime is installed — including inside a PyInstaller exe.
    """
    from onnx import numpy_helper, TensorProto, helper
    X   = helper.make_tensor_value_info("X",   TensorProto.FLOAT, [1, AI_FEATURE_DIM])
    Out = helper.make_tensor_value_info("Out", TensorProto.FLOAT, [1, AI_FEATURE_DIM])
    def _t(name, arr):
        return numpy_helper.from_array(arr.astype(np.float32), name=name)
    nodes = [
        helper.make_node("MatMul", ["X",     "W1"], ["h_pre"]),
        helper.make_node("Add",    ["h_pre", "b1"], ["h_b"]),
        helper.make_node("Relu",   ["h_b"],         ["h"]),
        helper.make_node("MatMul", ["h",     "W2"], ["o_pre"]),
        helper.make_node("Add",    ["o_pre", "b2"], ["Out"]),
    ]
    graph = helper.make_graph(nodes, "ae", [X], [Out],
                              [_t("W1",W1), _t("b1",b1), _t("W2",W2), _t("b2",b2)])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    return model.SerializeToString()


def _infer(sess, feats: np.ndarray) -> np.ndarray:
    return sess.run(None, {"X": feats.reshape(1,-1).astype(np.float32)})[0].reshape(-1)


def _recon_error(sess, feats: np.ndarray) -> float:
    return float(np.mean((feats - _infer(sess, feats))**2))


def _classify(feats: np.ndarray, recon: np.ndarray) -> str:
    """Classify anomaly type from feature deviations.
    Feature indices: [0]lvl [1]pk [2]crest [3]dc [4]clip_frac
                     [5]rms_var [6]min_frm [7]flat [8]rolloff [9]noise_floor
                     [10]hum [11]hf [12]zcr [13]sanity
    """
    diff = np.abs(feats - recon)
    # Hard fault checks first (feature value, not just deviation)
    if feats[6] < 0.05 and feats[0] < 0.15:   return "dropout"
    if feats[0] < 0.05:                         return "silence / very low level"
    if feats[4] > 0.3 or diff[4] > 0.2:        return "clipping / distortion"
    if feats[3] > 0.5 or diff[3] > 0.3:        return "DC offset"
    if feats[10] > 0.6 and diff[10] > 0.2:     return "mains hum"
    if feats[11] > 0.7 and diff[11] > 0.2:     return "hiss / HF noise"
    if diff[5] > 0.3 or diff[6] > 0.3:         return "level instability"
    if diff[9] > 0.25:                          return "elevated noise floor"
    if diff[7] > 0.2:                           return "unusual spectral texture"
    if diff[2] > 0.25:                          return "dynamic range anomaly"
    return "audio characteristic change"       # vague by design — not "anomaly"

# ─── Per-stream AI lifecycle ──────────────────────────────────────────────────

class StreamAI:
    def __init__(self, cfg: InputConfig, log_fn, sender):
        self.cfg=cfg; self.log=log_fn; self.sender=sender

    def _load(self) -> bool:
        ort = _try_import("onnxruntime")
        if not ort: self.cfg._ai_phase="no_onnx"; self.cfg._ai_status="Install onnxruntime to enable AI"; return False
        mp=_model_path(self.cfg.name); sp=_stats_path(self.cfg.name)
        if not (os.path.exists(mp) and os.path.exists(sp)): return False
        try:
            self.cfg._ai_session = ort.InferenceSession(mp)
            with open(sp) as f: st=json.load(f)
            self.cfg._ai_error_mean=st["mean"]; self.cfg._ai_error_std=st["std"]
            self.cfg._ai_phase="ready"; self.cfg._ai_status="Model loaded — monitoring"
            self.log(f"[AI:{self.cfg.name}] Loaded existing model.")
            return True
        except Exception as e:
            self.log(f"[AI:{self.cfg.name}] Load failed: {e}"); return False

    def start(self):
        if _try_import("onnxruntime") is None:
            self.cfg._ai_phase="no_onnx"
            self.cfg._ai_status="ⓘ pip install onnxruntime  to enable AI"
            return
        if self.cfg._ai_retrain_flag:
            self.cfg._ai_retrain_flag=False; self._begin_learn(); return
        if not self._load(): self._begin_learn()

    def _begin_learn(self):
        self.cfg._ai_phase="learning"
        self.cfg._ai_learn_start=time.time()
        self.cfg._ai_learn_samples=[]
        self.cfg._ai_status=f"Learning baseline… (0/{int(LEARN_DURATION_SECONDS//3600)}h {int((LEARN_DURATION_SECONDS%3600)//60)}m)"
        self.log(f"[AI:{self.cfg.name}] Learning phase started.")

    def feed(self, audio: np.ndarray):
        if not self.cfg.ai_monitor: return
        p = self.cfg._ai_phase
        if p == "no_onnx": return
        if p == "learning": self._feed_learn(audio)
        elif p == "ready":  self._feed_infer(audio)

    def _feed_learn(self, audio: np.ndarray):
        self.cfg._ai_learn_samples.append(extract_features(audio))
        elapsed = time.time() - self.cfg._ai_learn_start
        pct = min(100, int(elapsed / LEARN_DURATION_SECONDS * 100))
        self.cfg._ai_status = f"Learning baseline… {pct}% ({int(elapsed//60)}/{int(LEARN_DURATION_SECONDS//60)} min)"
        if elapsed >= LEARN_DURATION_SECONDS:
            self._finish_learn()

    def _finish_learn(self):
        samples = np.array(self.cfg._ai_learn_samples, dtype=np.float32)
        if len(samples) < MIN_TRAINING_SAMPLES:
            self.log(f"[AI:{self.cfg.name}] Too few samples ({len(samples)}), restarting.")
            self._begin_learn(); return
        try:
            W1,b1,W2,b2 = _train_autoencoder(samples, self.log)
            onnx_bytes   = _build_onnx(W1,b1,W2,b2)
            mp = _model_path(self.cfg.name)
            with open(mp,"wb") as f: f.write(onnx_bytes)
            ort  = _try_import("onnxruntime")
            sess = ort.InferenceSession(mp)
            errs = [_recon_error(sess, s) for s in samples]
            mean_e = float(np.mean(errs))
            std_e  = max(float(np.std(errs)), 1e-6)
            with open(_stats_path(self.cfg.name),"w") as f:
                json.dump({"mean":mean_e,"std":std_e,"n":len(samples)},f)
            self.cfg._ai_session=sess; self.cfg._ai_error_mean=mean_e
            self.cfg._ai_error_std=std_e; self.cfg._ai_phase="ready"
            self.cfg._ai_status="Model trained — monitoring"
            self.log(f"[AI:{self.cfg.name}] Ready. Baseline {mean_e:.5f} ± {std_e:.5f}")
        except Exception as e:
            self.cfg._ai_status=f"Training failed: {e}"
            self.log(f"[AI:{self.cfg.name}] Training error: {e}")

    def _feed_infer(self, audio: np.ndarray):
        cfg = self.cfg
        if not cfg._ai_session: return
        feats = extract_features(audio)
        err   = _recon_error(cfg._ai_session, feats)
        z     = (err - cfg._ai_error_mean) / (cfg._ai_error_std + 1e-10)

        # ── Sustained-window confirmation ──────────────────────────────────────
        # Single-window spikes from content transitions should not alert.
        # We require AI_CONFIRM_WINDOWS consecutive windows above threshold.
        if not hasattr(cfg, '_ai_consec_warn'):  cfg._ai_consec_warn  = 0
        if not hasattr(cfg, '_ai_consec_alert'): cfg._ai_consec_alert = 0

        if z >= ANOMALY_ALERT_THRESHOLD:
            cfg._ai_consec_alert += 1
            cfg._ai_consec_warn  += 1
        elif z >= ANOMALY_WARN_THRESHOLD:
            cfg._ai_consec_alert  = 0
            cfg._ai_consec_warn  += 1
        else:
            cfg._ai_consec_alert  = 0
            cfg._ai_consec_warn   = 0

        if cfg._ai_consec_alert >= AI_CONFIRM_WINDOWS:
            status = "ALERT"
            reason = _classify(feats, _infer(cfg._ai_session, feats))
        elif cfg._ai_consec_warn >= AI_CONFIRM_WINDOWS:
            status = "WARN"
            reason = _classify(feats, _infer(cfg._ai_session, feats))
        else:
            status = "OK"; reason = ""

        cfg._ai_status = f"[{status}] z={z:.1f}{' — '+reason if reason else ''}"

        if status in ("ALERT", "WARN"):
            # Only fire once per sustained event, not every window
            if cfg._ai_consec_warn == AI_CONFIRM_WINDOWS or cfg._ai_consec_alert == AI_CONFIRM_WINDOWS:
                key = f"AI_{reason}"; now = time.time()
                cd  = ALERT_COOLDOWN if status == "ALERT" else ALERT_COOLDOWN * 2
                if now - cfg._last_alerts.get(key, 0) >= cd:
                    cfg._last_alerts[key] = now
                    msg = f"AI {status} on '{cfg.name}': {reason} (score {z:.1f}σ)"
                    snippet = _save_alert_wav(cfg, f"ai_{_safe_label(reason)}")
                    _add_history(cfg, f"AI_{status}", msg, clip_path=snippet or "")
                    self.log(f"[AI:{cfg.name}] {msg}")
                    self.sender.send(f"AI {status} — {cfg.name}: {reason}", msg, snippet,
                             alert_type=f"AI_{status}", stream=cfg.name,
                             level_dbfs=float(cfg._last_level_dbfs))
        else:
            # Adaptive baseline: update mean AND std continuously on clean windows.
            # Using a faster adapt rate than before so the baseline tracks slow
            # long-term drift in the stream's normal characteristics.
            alpha = 0.002   # ~500-window half-life (~41 min at 5s intervals)
            cfg._ai_error_mean = (1-alpha) * cfg._ai_error_mean + alpha * err
            # Online std via exponential moving variance
            if not hasattr(cfg, '_ai_error_var'): cfg._ai_error_var = cfg._ai_error_std**2
            cfg._ai_error_var = (1-alpha) * cfg._ai_error_var + alpha * (err - cfg._ai_error_mean)**2
            cfg._ai_error_std = max(float(np.sqrt(cfg._ai_error_var)), 1e-6)

# ─── Alerting ─────────────────────────────────────────────────────────────────

class AlertSender:
    def __init__(self, cfg: AppConfig, log_fn):
        self.cfg=cfg; self.log=log_fn

    def send(self, subject, body, attachment=None, priority=None,
             alert_type: str = "", stream: str = "",
             level_dbfs: float = None, ptp_state: str = ""):
        self.log(f"[ALERT] {subject}")
        self._email(subject, body, attachment)
        self._webhook(subject, body, alert_type=alert_type, stream=stream,
                      level_dbfs=level_dbfs, ptp_state=ptp_state)
        self._pushover(subject, body, priority)

    def send_warn(self, subject, body, attachment=None):
        self.send(subject, body, attachment, priority=self.cfg.pushover.priority_warn)

    def send_alert(self, subject, body, attachment=None):
        self.send(subject, body, attachment, priority=self.cfg.pushover.priority_alert)

    def _email(self, subject, body, attachment):
        ec=self.cfg.email
        if not ec.enabled or not ec.smtp_host: return
        try:
            import smtplib
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText
            from email.mime.application import MIMEApplication
            msg=MIMEMultipart(); msg["Subject"]=subject
            msg["From"]=ec.from_addr; msg["To"]=ec.to_addr
            msg.attach(MIMEText(body))
            if attachment and os.path.exists(attachment):
                with open(attachment,"rb") as f:
                    p=MIMEApplication(f.read(),Name=os.path.basename(attachment))
                p["Content-Disposition"]=f'attachment; filename="{os.path.basename(attachment)}"'
                msg.attach(p)
            mgmt=self.cfg.network.management_interface_ip
            src=(mgmt,0) if mgmt!="0.0.0.0" else None
            with smtplib.SMTP(ec.smtp_host,ec.smtp_port,source_address=src) as s:
                if ec.use_tls: s.starttls()
                if ec.username: s.login(ec.username,ec.password)
                s.sendmail(ec.from_addr,[ec.to_addr],msg.as_string())
        except Exception as e: self.log(f"[EMAIL] {e}")

    def _webhook(self, subject: str, body: str, alert_type: str = "",
                  stream: str = "", level_dbfs: float = None, ptp_state: str = ""):
        wc = self.cfg.webhook
        if not wc.enabled: return
        site = self.cfg.hub.site_name or ""
        routes = wc.matching_routes(stream, alert_type, site)
        if not routes: return
        for route in routes:
            try:
                if route.teams_style:
                    payload = self._build_teams_card(subject, body, alert_type,
                                                     stream, level_dbfs, ptp_state)
                else:
                    payload = {"text": f"**{subject}**\n\n{body}"}
                data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                urllib.request.urlopen(
                    urllib.request.Request(route.url, data=data,
                        headers={"Content-Type": "application/json"}), timeout=10)
                self.log(f"[WEBHOOK→{route.name or route.url[:40]}] {subject}")
            except Exception as e:
                self.log(f"[WEBHOOK→{route.name or 'default'}] {e}")

    @staticmethod
    def _build_teams_card(subject: str, body: str, alert_type: str = "",
                          stream: str = "", level_dbfs: float = None, ptp_state: str = "") -> dict:
        """Build an MS Teams Adaptive Card payload.

        Mirrors the card structure used by notify-msteams.py but built in pure
        Python rather than Jinja templates, so no extra dependencies needed.
        Uses the Workflow/Power Automate webhook format which expects:
          { "type": "message", "attachments": [{ "contentType": "application/vnd.microsoft.card.adaptive", ... }] }
        """
        # ── Severity colour (matches Teams theme colours) ──────────────────────
        tl = alert_type.lower()
        if any(x in tl for x in ("alert", "silence", "clip", "rtp_loss", "ptp_lost", "cmp")):
            colour = "attention"   # red
        elif any(x in tl for x in ("warn", "hiss", "rtp_loss_warn")):
            colour = "warning"     # amber
        else:
            colour = "good"        # green

        colour_hex = {"attention": "FF3B30", "warning": "FF9500", "good": "34C759"}.get(colour, "4F9CF9")

        # ── Header icon by type ────────────────────────────────────────────────
        icon = "🔔"
        if "silence"  in tl: icon = "🔇"
        elif "clip"   in tl: icon = "📈"
        elif "hiss"   in tl: icon = "〰️"
        elif "ai"     in tl: icon = "🤖"
        elif "rtp"    in tl: icon = "📦"
        elif "ptp"    in tl: icon = "🕐"
        elif "cmp"    in tl: icon = "🔀"

        # ── Build fact rows ────────────────────────────────────────────────────
        facts = []
        if stream:
            facts.append({"title": "Stream", "value": stream})
        if alert_type:
            facts.append({"title": "Type", "value": alert_type})
        if level_dbfs is not None and level_dbfs > -120:
            facts.append({"title": "Level", "value": f"{level_dbfs:.1f} dBFS"})
        if ptp_state:
            facts.append({"title": "PTP", "value": ptp_state})
        facts.append({"title": "Detail", "value": body})
        facts.append({"title": "Time", "value": time.strftime("%Y-%m-%d %H:%M:%S")})

        # ── Adaptive Card body ─────────────────────────────────────────────────
        card_body = [
            {
                "type": "Container",
                "style": colour,
                "items": [
                    {
                        "type": "ColumnSet",
                        "columns": [
                            {
                                "type": "Column",
                                "width": "auto",
                                "items": [{"type": "TextBlock", "text": icon, "size": "Large"}]
                            },
                            {
                                "type": "Column",
                                "width": "stretch",
                                "items": [
                                    {
                                        "type": "TextBlock",
                                        "text": subject,
                                        "weight": "Bolder",
                                        "size": "Medium",
                                        "wrap": True,
                                        "color": colour
                                    },
                                    {
                                        "type": "TextBlock",
                                        "text": f"SignalScope  •  {time.strftime('%H:%M:%S')}",
                                        "size": "Small",
                                        "isSubtle": True
                                    }
                                ]
                            }
                        ]
                    }
                ]
            },
            {
                "type": "FactSet",
                "facts": facts
            }
        ]

        adaptive_card = {
            "type": "AdaptiveCard",
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.4",
            "body": card_body
        }

        # Workflow webhook format (replaces legacy Office 365 connector format)
        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    "content": adaptive_card
                }
            ]
        }

    def _pushover(self, subject, body, priority=None):
        pc = self.cfg.pushover
        if not pc.enabled or not pc.user_key or not pc.app_token: return
        if priority is None: priority = pc.priority_alert
        try:
            # Emergency priority requires retry/expire params
            params = {
                "token":   pc.app_token,
                "user":    pc.user_key,
                "title":   subject[:250],
                "message": body[:1024],
                "priority": str(priority),
            }
            if priority == 2:  # emergency — must have retry + expire
                params["retry"]  = "60"
                params["expire"] = "3600"
            data = urllib.parse.urlencode(params).encode()
            urllib.request.urlopen(
                urllib.request.Request(
                    "https://api.pushover.net/1/messages.json",
                    data=data,
                    headers={"Content-Type":"application/x-www-form-urlencoded"},
                ), timeout=10)
            self.log(f"[PUSHOVER] Sent: {subject}")
        except Exception as e: self.log(f"[PUSHOVER] {e}")

# ─── Rule-based analysis ──────────────────────────────────────────────────────

def analyse_chunk(cfg: InputConfig, sender: AlertSender, log_fn,
                  data: np.ndarray, elapsed: float, wav_dur: float,
                  all_inputs: List[InputConfig]):
    if not data.size: return
    rms=float(np.sqrt(np.mean(data**2))); lev=dbfs(rms)
    cfg._last_level_dbfs=lev
    if not cfg.enabled: return
    in_alert = lev <= cfg.silence_threshold_dbfs
    _sla_update(cfg, elapsed, in_alert)

    if cfg.cascade_suppress_alerts and cfg.cascade_parent and all_inputs:
        parent=next((i for i in all_inputs if i.name==cfg.cascade_parent),None)
        if parent and parent._last_level_dbfs<=parent.silence_threshold_dbfs:
            cfg._silence_secs=0.0; return

    # Silence
    if cfg.alert_on_silence:
        cfg._silence_secs = cfg._silence_secs+elapsed if lev<=cfg.silence_threshold_dbfs else 0.0
        if cfg._silence_secs>=cfg.silence_min_duration:
            now=time.time()
            if now-cfg._last_alerts.get("SILENCE",0)>=ALERT_COOLDOWN:
                cfg._last_alerts["SILENCE"]=now
                msg=f"Silence on '{cfg.name}' for {cfg._silence_secs:.1f}s ({lev:.1f} dBFS)"
                clip=_save_alert_wav(cfg,"silence",wav_dur)
                _add_history(cfg,"SILENCE",msg,clip_path=clip or "")
                sender.send(f"SILENCE on {cfg.name}", msg, clip,
                    alert_type="SILENCE", stream=cfg.name, level_dbfs=lev)
                log_fn(f"[ALERT] {msg}")
            cfg._silence_secs=0.0

    # Clip
    if cfg.alert_on_clip:
        peak_db=dbfs(float(np.max(np.abs(data)))); now=time.time()
        if peak_db>=cfg.clip_threshold_dbfs:
            if now-cfg._clip_window_start>cfg.clip_window_seconds:
                cfg._clip_count=0; cfg._clip_window_start=now
            cfg._clip_count+=1
            if cfg._clip_count>=cfg.clip_count_threshold:
                if now-cfg._last_alerts.get("CLIP",0)>=ALERT_COOLDOWN:
                    cfg._last_alerts["CLIP"]=now
                    msg=f"Clipping on '{cfg.name}' — {cfg._clip_count}x in {cfg.clip_window_seconds}s (peak {peak_db:.1f} dBFS)"
                    clip=_save_alert_wav(cfg,"clip",wav_dur)
                    _add_history(cfg,"CLIP",msg,clip_path=clip or "")
                    sender.send(f"CLIP on {cfg.name}", msg, clip,
                    alert_type="CLIP", stream=cfg.name, level_dbfs=peak_db)
                    log_fn(f"[ALERT] {msg}")
                cfg._clip_count=0

    # Hiss
    if cfg.alert_on_hiss:
        n=len(data); win=np.hanning(n); psd=np.abs(np.fft.rfft(data*win))**2
        freq=np.fft.rfftfreq(n,d=1.0/SAMPLE_RATE); tot=float(np.sum(psd)+1e-12)
        hf =float(np.sum(psd[freq>=cfg.hiss_hf_band_hz])/tot)
        mid=float(np.sum(psd[(freq>=1000)&(freq<cfg.hiss_hf_band_hz)])/tot)
        if cfg._baseline_learning_remaining>0:
            cfg._hf_baseline  = hf  if cfg._hf_baseline  is None else 0.9*cfg._hf_baseline +0.1*hf
            cfg._mid_baseline = mid if cfg._mid_baseline is None else 0.9*cfg._mid_baseline+0.1*mid
            cfg._baseline_learning_remaining=max(0.0,cfg._baseline_learning_remaining-elapsed)
        elif cfg._hf_baseline and cfg._hf_baseline>1e-10:
            hf_db =20*math.log10(max(hf, 1e-10)/max(cfg._hf_baseline, 1e-10))
            mid_db=20*math.log10(max(mid,1e-10)/max(cfg._mid_baseline,1e-10))
            if hf_db>=cfg.hiss_rise_db and mid_db<0.5:
                cfg._hiss_secs=min(cfg._hiss_secs+elapsed,cfg.hiss_min_duration*2)
            else:
                cfg._hiss_secs=max(0.0,cfg._hiss_secs-elapsed)
            if cfg._hiss_secs>=cfg.hiss_min_duration:
                now=time.time()
                if now-cfg._last_alerts.get("HISS",0)>=ALERT_COOLDOWN:
                    cfg._last_alerts["HISS"]=now
                    msg=f"Hiss/HF noise on '{cfg.name}' — HF up {hf_db:.1f}dB for {cfg._hiss_secs:.1f}s"
                    clip=_save_alert_wav(cfg,"hiss",wav_dur)
                    _add_history(cfg,"HISS",msg,clip_path=clip or "")
                    sender.send(f"HISS on {cfg.name}", msg, clip,
                    alert_type="HISS", stream=cfg.name,
                    level_dbfs=float(cfg._last_level_dbfs))
                    log_fn(f"[ALERT] {msg}")
                cfg._hiss_secs=0.0

# ─── RTP / Livewire receive ───────────────────────────────────────────────────

def _lw_ip(sid: int) -> str:
    return f"239.192.{(sid>>8)&0xFF}.{sid&0xFF}"

def _parse_device(dev: str) -> Tuple[str,int]:
    s=dev.strip()
    if "." in s:
        if ":" in s: ip,p=s.rsplit(":",1); return ip,int(p)
        return s,5004
    return _lw_ip(int(s)),5004

def _detect_fmt(psz,pt):
    if pt in (10,11):
        ch=2 if pt==10 else 1; return "L16",2,ch,f"AES67 L16 {'stereo' if ch==2 else 'mono'}"
    KNOWN={72:("LIVEWIRE",3,2,"Native Livewire 0.25ms"),144:("LIVEWIRE",3,2,"Native Livewire 0.5ms"),
           288:("AES67",3,2,"AES67 1ms"),1440:("LIVEWIRE",3,2,"Standard Livewire 5ms"),
           576:("LIVEWIRE",3,4,"Native Livewire 4ch"),864:("LIVEWIRE",3,6,"Native Livewire 6ch"),
           1152:("LIVEWIRE",3,8,"Native Livewire 8ch")}
    if psz in KNOWN: return KNOWN[psz]
    if psz%6==0: return "LIVEWIRE",3,2,f"Native Livewire {psz//6/48:.2f}ms"
    if psz%4==0: return "L16",2,2,f"AES67 L16 {psz//4/48:.2f}ms"
    if psz%2==0: return "L16",2,2,"L16 (inferred)"
    return "LIVEWIRE",3,2,"Unknown (defaulted)"

def _decode(payload,fmt,ch):
    try:
        if fmt in ("LIVEWIRE","L24","AES67"):
            raw=bytearray(payload); ns=len(raw)//(3*ch); vals=[]
            for i in range(ns*ch):
                b0,b1,b2=raw[i*3],raw[i*3+1],raw[i*3+2]
                v=(b0<<16)|(b1<<8)|b2
                if v&0x800000: v-=0x1000000
                vals.append(v/8388608.0)
            samp=np.array(vals,dtype=np.float32)
        else:
            samp=np.frombuffer(payload,dtype=">i2").astype(np.float32)/32768.0
        if ch>1:
            if samp.size%ch!=0: return None
            samp=samp.reshape(-1,ch).mean(axis=1)
        return samp
    except: return None

# ─── Monitor manager ──────────────────────────────────────────────────────────

# ─── PTP Monitor ─────────────────────────────────────────────────────────────

class PTPMonitor:
    """
    Listens to PTP (IEEE 1588 / AES67) multicast on 224.0.1.129.
    Parses Sync + Follow_Up messages to measure offset and jitter.
    Does NOT require linuxptp or any external tool — pure raw socket.

    Offset calculation:
      When we receive a Sync (or Follow_Up with precise originTimestamp),
      we compare the PTP origin timestamp to our local wall clock.
      This gives a rough offset — not as accurate as a full PTP slave
      implementation (we don't send Delay_Req) but good enough to detect
      drift, jitter and sync loss.
    """

    def __init__(self, log_fn, sender):
        self.log    = log_fn
        self.sender = sender

        # Status visible to the dashboard
        self.status:    str   = "Waiting for PTP…"
        self.state:     str   = "idle"   # idle | ok | warn | alert | lost
        self.gm_id:     str   = ""       # grandmaster clock identity (hex)
        self.domain:    int   = 0
        self.offset_us: float = 0.0      # latest offset µs
        self.jitter_us: float = 0.0      # rolling std-dev of offset
        self.last_sync: float = 0.0      # time.time() of last Sync received
        self.history:   List[Dict] = []

        self._offsets: collections.deque = collections.deque(maxlen=PTP_HISTORY_LEN)
        self._offset_baseline: Optional[float] = None
        self.drift_us: float = 0.0
        self._last_alerts: Dict[str,float] = {}
        self._pending_sync: Dict[int, float] = {}  # seq_id → local rx time
        self._seq_origin:   Dict[int, int]   = {}  # seq_id → origin ns (Follow_Up)

    # ── PTP packet parsing ────────────────────────────────────────────────────

    @staticmethod
    def _parse_header(data: bytes):
        """Parse the 44-byte PTP v2 common header. Returns dict or None."""
        if len(data) < 44:
            return None
        msg_type   = data[0] & 0x0F
        version    = data[1] & 0x0F
        if version != 2:
            return None
        msg_len    = struct.unpack_from(">H", data, 2)[0]
        domain     = data[4]
        flags      = struct.unpack_from(">H", data, 6)[0]
        correction = struct.unpack_from(">q", data, 8)[0]   # ns << 16
        src_id     = data[20:28].hex()   # clockIdentity
        src_port   = struct.unpack_from(">H", data, 28)[0]
        seq_id     = struct.unpack_from(">H", data, 30)[0]
        ctrl       = data[32]
        return {
            "type": msg_type, "domain": domain, "flags": flags,
            "correction_ns": correction >> 16,
            "src_id": src_id, "src_port": src_port,
            "seq_id": seq_id, "ctrl": ctrl,
        }

    @staticmethod
    def _parse_timestamp(data: bytes, offset: int) -> int:
        """
        Parse a 10-byte PTP v2 timestamp at offset → nanoseconds since epoch.
        Layout: [4 bytes seconds-high][2 bytes seconds-low][4 bytes nanoseconds]
        """
        if len(data) < offset + 10:
            return 0
        secs  = struct.unpack_from(">IH", data, offset)
        secs_val = (secs[0] << 16) | secs[1]
        ns    = struct.unpack_from(">I", data, offset + 6)[0]
        return secs_val * 1_000_000_000 + ns

    @staticmethod
    def _parse_clock_id(data: bytes, offset: int) -> str:
        if len(data) < offset + 8:
            return ""
        return data[offset:offset+8].hex()

    # ── Core analysis ─────────────────────────────────────────────────────────

    def _process_offset(self, origin_ns: int, correction_ns: int, gm_id: str, domain: int):
        """Called with a known origin timestamp — compute offset vs local clock."""
        if origin_ns <= 0:
            return
        local_ns  = int(time.time() * 1e9)
        # Axia sends TAI timestamps; system clock is UTC. TAI is currently 37s ahead.
        TAI_OFFSET_NS = 37 * 1_000_000_000
        offset_ns = local_ns - (origin_ns - TAI_OFFSET_NS) - correction_ns
        offset_us = offset_ns / 1000.0
        # Sanity guard — >5s after TAI correction means something is wrong
        if abs(offset_us) > 5_000_000:
            self.log(f"[PTP] Offset {offset_us/1e6:.2f}s out of range — skipping")
            return
        # We are not a PTP slave so the absolute offset vs local clock is meaningless.
        # Instead we track a slow-moving baseline and alert on DRIFT from that baseline.
        if not hasattr(self, '_offset_baseline') or self._offset_baseline is None:
            self._offset_baseline = offset_us   # seed baseline on first sample
        # EMA baseline — alpha=0.05 adapts over ~20 samples, fast enough
        # to track NTP corrections without false drift alarms
        self._offset_baseline = 0.95 * self._offset_baseline + 0.05 * offset_us
        # drift = deviation from rolling baseline
        self.drift_us = offset_us - self._offset_baseline
        self.offset_us = offset_us
        self.domain    = domain
        self.last_sync = time.time()

        # Grandmaster change detection — also re-seeds baseline
        if self.gm_id and self.gm_id != gm_id:
            self.log(f"[PTP] Grandmaster changed: {self.gm_id} → {gm_id}")
            self._add_history("PTP_GM_CHANGE",
                f"Grandmaster changed from {self.gm_id} to {gm_id}")
            self._offset_baseline = offset_us  # re-seed on GM change
        self.gm_id = gm_id

        self._offsets.append(offset_us)
        if len(self._offsets) >= 3:
            self.jitter_us = float(np.std(list(self._offsets)))

        self._evaluate()

    def _evaluate(self):
        """Check thresholds and alert on DRIFT from baseline and jitter.
        We are not a PTP slave so absolute offset is meaningless —
        we watch for sudden changes which indicate a real clock event."""
        drift   = abs(getattr(self, 'drift_us', 0.0))
        jitter  = self.jitter_us
        now     = time.time()

        cfg = monitor.app_cfg
        o_warn  = cfg.ptp_offset_warn_us
        o_alert = cfg.ptp_offset_alert_us
        j_warn  = cfg.ptp_jitter_warn_us
        j_alert = cfg.ptp_jitter_alert_us
        if drift >= o_alert or jitter >= j_alert:
            new_state = "alert"
        elif drift >= o_warn or jitter >= j_warn:
            new_state = "warn"
        else:
            new_state = "ok"

        self.state = new_state

        off_sign = "+" if self.offset_us >= 0 else ""
        drift_sign = "+" if getattr(self,'drift_us',0) >= 0 else ""
        self.status = (
            f"[{new_state.upper()}] "
            f"Offset: {off_sign}{self.offset_us/1000:.3f} ms  "
            f"Drift: {drift_sign}{getattr(self,'drift_us',0)/1000:.3f} ms  "
            f"Jitter: {jitter/1000:.3f} ms  "
            f"GM: {self.gm_id[:16]}  Domain: {self.domain}"
        )

        if new_state in ("alert", "warn"):
            reasons = []
            if drift >= o_warn:
                reasons.append(f"drift {getattr(self,'drift_us',0)/1000:.2f} ms")
            if jitter >= j_warn:
                reasons.append(f"jitter {jitter/1000:.2f} ms")

            key = f"PTP_{new_state}"
            cd  = ALERT_COOLDOWN if new_state == "alert" else ALERT_COOLDOWN * 2
            if now - self._last_alerts.get(key, 0) >= cd:
                self._last_alerts[key] = now
                msg = f"PTP {new_state.upper()}: {', '.join(reasons)}"
                self._add_history(f"PTP_{new_state.upper()}", msg)
                self.log(f"[PTP] {msg}")
                self.sender.send(f"PTP {new_state.upper()} — clock issue", msg,
                             alert_type=f"PTP_{new_state.upper()}", ptp_state=new_state)

    def _check_sync_loss(self):
        """Called periodically — fires alert if no Sync seen recently."""
        if self.last_sync == 0:
            return   # haven't seen any PTP yet
        gap = time.time() - self.last_sync
        if gap > PTP_SYNC_TIMEOUT:
            self.state  = "lost"
            self.status = f"[LOST] No PTP Sync for {gap:.0f}s"
            key = "PTP_LOST"
            now = time.time()
            if now - self._last_alerts.get(key, 0) >= ALERT_COOLDOWN:
                self._last_alerts[key] = now
                msg = f"PTP Sync loss — no message for {gap:.0f}s"
                self._add_history("PTP_LOST", msg)
                self.log(f"[PTP] {msg}")
                self.sender.send("PTP SYNC LOSS", msg,
                             alert_type="PTP_LOST", ptp_state="lost")

    def _add_history(self, kind: str, msg: str):
        self.history.append({
            "timestamp": time.strftime("%H:%M:%S"),
            "type": kind, "message": msg,
        })
        if len(self.history) > 200:
            self.history = self.history[-200:]

    # ── Receive loop ──────────────────────────────────────────────────────────

    def run(self, stop_evt: threading.Event, iface_ip: str = "0.0.0.0"):
        """Main receive loop — runs in its own thread."""
        self.log("[PTP] Starting monitor on 224.0.1.129:319/320")

        # We listen on the event port (319) for Sync,
        # and general port (320) for Follow_Up / Announce
        socks = []
        for port in (PTP_EVENT_PORT, PTP_GENERAL_PORT):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try: s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except AttributeError: pass
                s.bind(("", port))
                mreq = struct.pack("4s4s",
                    socket.inet_aton(PTP_MULTICAST_IP),
                    socket.inet_aton(iface_ip))
                s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                s.settimeout(1.0)
                socks.append(s)
                self.log(f"[PTP] Joined {PTP_MULTICAST_IP}:{port}")
            except Exception as e:
                self.log(f"[PTP] Socket {port} failed: {e}")

        if not socks:
            self.state  = "lost"
            self.status = "Could not open PTP sockets — check network interface"
            return

        last_loss_check = time.time()

        while not stop_evt.is_set():
            import select as _select
            try:
                readable, _, _ = _select.select(socks, [], [], 1.0)
            except Exception:
                time.sleep(0.5); continue

            for s in readable:
                try:
                    data, addr = s.recvfrom(1024)
                except Exception:
                    continue

                hdr = self._parse_header(data)
                if hdr is None:
                    continue

                msg_type = hdr["type"]
                seq_id   = hdr["seq_id"]
                domain   = hdr["domain"]

                # MSG_TYPE 0x0 = Sync, 0x8 = Follow_Up, 0xB = Announce


                if msg_type == 0x0:   # Sync
                    self.last_sync = time.time()
                    self.gm_id = hdr["src_id"]
                    if self.state == "idle":
                        self.state  = "ok"
                        self.status = f"PTP Sync received — GM: {hdr['src_id'][:16]}"
                    two_step = bool(hdr["flags"] & 0x0200)
                    if two_step:
                        self._pending_sync[seq_id] = time.time()
                    else:
                        origin_ns  = self._parse_timestamp(data, 44)
                        correction = hdr["correction_ns"]
                        self._process_offset(origin_ns, correction, hdr["src_id"], domain)

                elif msg_type == 0x8:  # Follow_Up
                    # Axia Mix Engine: originTimestamp at byte 34 (immediately after 34-byte header)
                    origin_ns  = self._parse_timestamp(data, 34)
                    correction = hdr["correction_ns"]
                    gm_id      = hdr["src_id"]
                    if seq_id in self._pending_sync:
                        del self._pending_sync[seq_id]
                    self._process_offset(origin_ns, correction, gm_id, domain)

                elif msg_type == 0xB:  # Announce — update GM from grandmasterIdentity
                    # grandmasterIdentity is at byte 44+20 = 64 in Announce body
                    gm_id = self._parse_clock_id(data, 64)
                    if gm_id and gm_id != self.gm_id:
                        if self.gm_id:
                            msg = f"Grandmaster changed: {self.gm_id} → {gm_id}"
                            self._add_history("PTP_GM_CHANGE", msg)
                            self.log(f"[PTP] {msg}")
                        self.gm_id = gm_id

            # Expire stale pending syncs
            now = time.time()
            self._pending_sync = {k: v for k,v in self._pending_sync.items()
                                  if now - v < 2.0}

            # Periodic sync-loss check
            if now - last_loss_check >= 2.0:
                self._check_sync_loss()
                last_loss_check = now

        for s in socks:
            try: s.close()
            except: pass
        self.log("[PTP] Monitor stopped.")


# ─── Stream Comparator (pre/post with cross-correlation alignment) ─────────────

class StreamComparator:
    """
    Compares two streams (pre and post processing) by:
    1. Cross-correlating to find the delay offset (up to COMPARE_SEARCH_SECS)
    2. Continuously monitoring for divergence at that offset:
       - Post silent while pre has audio → processor failure
       - Dropout on one but not other → RTP loss on one path
    """

    def __init__(self, pre: InputConfig, post: InputConfig, log_fn, sender):
        self.pre    = pre
        self.post   = post
        self.log    = log_fn
        self.sender = sender
        self.delay_samples: int   = 0
        self.aligned:       bool  = False
        self.status:        str   = "Finding delay…"
        self._last_alerts:  Dict[str,float] = {}
        self._last_align:   float = 0.0
        # Live metrics updated every COMPARE_INTERVAL
        self.delay_ms:      float = 0.0
        self.correlation:   float = 0.0    # 0.0–1.0 normalised xcorr peak
        self.pre_dbfs:      float = -80.0
        self.post_dbfs:     float = -80.0
        self.gain_diff_db:  float = 0.0    # post - pre in dB (positive = louder out)
        self.cmp_history:   list  = []     # last 8 CMP events

    def _xcorr_delay(self) -> Optional[tuple]:
        """Cross-correlate pre and post to find delay.
        Returns (delay_samples, correlation_0_to_1) or None."""
        if not self.pre._stream_buffer or not self.post._stream_buffer:
            return None
        pre_chunks  = list(self.pre._stream_buffer)
        post_chunks = list(self.post._stream_buffer)
        if not pre_chunks or not post_chunks:
            return None
        pre_audio  = np.concatenate(pre_chunks).astype(np.float32)
        post_audio = np.concatenate(post_chunks).astype(np.float32)
        use = SAMPLE_RATE * 5
        pre_seg  = pre_audio[-use:]  if len(pre_audio)  > use else pre_audio
        post_seg = post_audio[-use:] if len(post_audio) > use else post_audio
        min_len = min(len(pre_seg), len(post_seg))
        if min_len < SAMPLE_RATE:
            return None
        pre_seg  = pre_seg[:min_len]
        post_seg = post_seg[:min_len]
        pre_std  = float(np.std(pre_seg)  + 1e-10)
        post_std = float(np.std(post_seg) + 1e-10)
        pre_n    = pre_seg  / pre_std
        post_n   = post_seg / post_std
        n    = len(pre_n) + len(post_n) - 1
        nfft = 1 << (n - 1).bit_length()
        Pre  = np.fft.rfft(pre_n,  nfft)
        Post = np.fft.rfft(post_n, nfft)
        xcorr = np.fft.irfft(np.conj(Pre) * Post, nfft)[:n]
        max_lag   = int(COMPARE_SEARCH_SECS * SAMPLE_RATE)
        best_lag  = int(np.argmax(xcorr[: max_lag + 1]))
        peak_corr = float(xcorr[best_lag]) / float(min_len)   # normalise to -1..1
        peak_corr = float(np.clip(peak_corr, 0.0, 1.0))
        return best_lag, peak_corr

    def _log_cmp_event(self, msg: str):
        """Append to local cmp_history (capped at 8) and the stream history."""
        ts = time.strftime("%H:%M:%S")
        self.cmp_history.append({"ts": ts, "msg": msg})
        if len(self.cmp_history) > 8:
            self.cmp_history.pop(0)

    def update(self):
        """Call periodically. Re-aligns if needed, then checks divergence."""
        now = time.time()
        if now - self._last_align > COMPARE_INTERVAL or not self.aligned:
            result = self._xcorr_delay()
            if result is not None:
                delay, corr = result
                self.delay_samples = delay
                self.correlation   = corr
                self.aligned       = True
                self._last_align   = now
                self.delay_ms      = delay / SAMPLE_RATE * 1000
                self.log(f"[CMP:{self.pre.name}→{self.post.name}] "
                         f"Delay {self.delay_ms:.0f} ms  corr {corr:.2f}")
            else:
                self.status = "Waiting for audio buffers…"
                return

        if not self.aligned:
            return

        # Get aligned windows for comparison (last 2 seconds of aligned audio)
        window = SAMPLE_RATE * 2
        pre_chunks  = list(self.pre._stream_buffer)
        post_chunks = list(self.post._stream_buffer)
        if not pre_chunks or not post_chunks:
            return
        pre_audio  = np.concatenate(pre_chunks).astype(np.float32)
        post_audio = np.concatenate(post_chunks).astype(np.float32)

        # Align: trim pre by delay to match post
        if self.delay_samples > 0 and len(pre_audio) > self.delay_samples + window:
            pre_aligned = pre_audio[-(window + self.delay_samples): -self.delay_samples]
        else:
            pre_aligned = pre_audio[-window:]
        post_aligned = post_audio[-window:]
        min_len = min(len(pre_aligned), len(post_aligned))
        if min_len < SAMPLE_RATE // 2:
            return
        pre_aligned  = pre_aligned[:min_len]
        post_aligned = post_aligned[:min_len]

        pre_rms  = max(float(np.sqrt(np.mean(pre_aligned**2))),  1e-10)
        post_rms = max(float(np.sqrt(np.mean(post_aligned**2))), 1e-10)
        self.pre_dbfs   = 20 * math.log10(pre_rms)
        self.post_dbfs  = 20 * math.log10(post_rms)
        self.gain_diff_db = round(self.post_dbfs - self.pre_dbfs, 1)

        pre_has_audio  = self.pre_dbfs  > COMPARE_SILENCE_THRESH
        post_has_audio = self.post_dbfs > COMPARE_SILENCE_THRESH

        # Alert: post silent, pre has audio → processor/path failure
        if pre_has_audio and not post_has_audio:
            key = "CMP_POST_SILENT"
            if now - self._last_alerts.get(key, 0) >= ALERT_COOLDOWN:
                self._last_alerts[key] = now
                msg = (f"POST ‘{self.post.name}’ silent while PRE ‘{self.pre.name}’ "
                       f"has audio ({self.pre_dbfs:.1f} dBFS) — possible processor failure")
                self.status = f"⚠ POST SILENT"
                _add_history(self.pre, "CMP_ALERT", msg)
                self._log_cmp_event("⚠ POST silent / processor failure")
                self.log(f"[CMP] {msg}")
                snippet = _save_alert_wav(self.post, "compare_post_silent")
                self.sender.send(f"Stream Compare ALERT — {self.post.name} silent", msg, snippet,
                             alert_type="CMP_ALERT", stream=self.post.name,
                             level_dbfs=self.post_dbfs)

        # Alert: pre has dropout but post doesn't → RTP loss on pre path
        elif post_has_audio and not pre_has_audio:
            key = "CMP_PRE_SILENT"
            if now - self._last_alerts.get(key, 0) >= ALERT_COOLDOWN:
                self._last_alerts[key] = now
                msg = (f"PRE ‘{self.pre.name}’ silent/dropout while POST "
                       f"‘{self.post.name}’ has audio — possible RTP loss on pre path")
                self.status = f"⚠ PRE DROPOUT"
                _add_history(self.post, "CMP_ALERT", msg)
                self._log_cmp_event("⚠ PRE dropout / RTP loss")
                self.log(f"[CMP] {msg}")
                snippet = _save_alert_wav(self.pre, "compare_pre_dropout")
                self.sender.send(f"Stream Compare ALERT — {self.pre.name} dropout", msg, snippet,
                             alert_type="CMP_ALERT", stream=self.pre.name,
                             level_dbfs=self.pre_dbfs)

        else:
            # Update gain-change detection: warn if gain shifts by >3 dB from baseline
            if not hasattr(self, "_baseline_gain_diff"):
                self._baseline_gain_diff = self.gain_diff_db
            gain_drift = abs(self.gain_diff_db - self._baseline_gain_diff)
            gain_alert_thresh = getattr(self.pre, "compare_gain_alert_db", 3.0)
            if gain_drift > gain_alert_thresh:
                key = "CMP_GAIN_SHIFT"
                if now - self._last_alerts.get(key, 0) >= ALERT_COOLDOWN * 4:
                    self._last_alerts[key] = now
                    msg = (f"Gain shift on ‘{self.post.name}’: "
                           f"{self.gain_diff_db:+.1f} dB vs baseline {self._baseline_gain_diff:+.1f} dB")
                    self._log_cmp_event(f"⚠ Gain shift {self.gain_diff_db:+.1f} dB")
                    _add_history(self.pre, "CMP_ALERT", msg)
                    self.log(f"[CMP] {msg}")
                    self.sender.send(f"Stream Compare — Gain shift on {self.post.name}", msg, None,
                             alert_type="CMP_ALERT", stream=self.post.name,
                             level_dbfs=self.post_dbfs)
            elif gain_drift < 1.0:
                # Only update baseline during stable periods
                self._baseline_gain_diff = 0.98 * self._baseline_gain_diff + 0.02 * self.gain_diff_db

            corr_label = (
                "excellent" if self.correlation >= 0.85 else
                "good"      if self.correlation >= 0.65 else
                "weak"      if self.correlation >= 0.40 else
                "poor — streams may not match"
            )
            self.status = "OK"
            if self.correlation < 0.40 and pre_has_audio and post_has_audio:
                key = "CMP_LOW_CORR"
                if now - self._last_alerts.get(key, 0) >= ALERT_COOLDOWN * 6:
                    self._last_alerts[key] = now
                    msg = (f"Low correlation ({self.correlation:.2f}) between ‘{self.pre.name}’ "
                           f"and ‘{self.post.name}’ — streams may have diverged")
                    self._log_cmp_event(f"⚠ Low corr {self.correlation:.2f}")
                    _add_history(self.pre, "CMP_ALERT", msg)
                    self.log(f"[CMP] {msg}")
                    self.sender.send(f"Stream Compare — Low correlation {self.post.name}", msg, None,
                             alert_type="CMP_ALERT", stream=self.post.name)


@dataclass
class DabSharedSession:
    key: tuple
    serial: Optional[str]
    device_idx: int
    channel: str
    dab_port: int
    ppm: int = 0
    proc: Any = None
    lease: Any = None
    ready: threading.Event = field(default_factory=threading.Event)
    stop_evt: threading.Event = field(default_factory=threading.Event)
    mux: dict = field(default_factory=dict)
    failed: bool = False
    refcount: int = 0
    consumers: set = field(default_factory=set)
    stderr_thread: Any = None
    poll_thread: Any = None


class MonitorManager:
    def __init__(self):
        self.app_cfg=load_config()
        self._lock=threading.Lock(); self._running=False
        self._threads: List[threading.Thread]=[]
        self._stop_flags: List[threading.Event]=[]
        self._log=collections.deque(maxlen=2000)
        self._stream_ais: Dict[str,StreamAI]={}
        self.ptp: Optional[PTPMonitor]=None
        self._comparators: List[StreamComparator]=[]
        self._report_sent_date: str=""
        self._hub_client: Optional[HubClient]=None
        self._dab_sessions: Dict[tuple, DabSharedSession]={}
        self._dab_sessions_lock=threading.Lock()

    def log(self,msg):
        line=f"[{time.strftime('%H:%M:%S')}] {msg}"
        self._log.append(line); print(line,flush=True)

    def get_logs(self,n=200): return list(self._log)[-n:]
    def is_running(self):
        with self._lock: return self._running

    def start_hub_client(self):
        """Start the hub heartbeat client immediately at app startup, independent of monitoring."""
        cfg = self.app_cfg
        if cfg.hub.mode in ("client","both") and cfg.hub.hub_url:
            if self._hub_client is None:
                self._hub_client = HubClient(lambda: self.app_cfg, self)
                self._hub_client.start()
                self.log(f"[Hub] Client started → {cfg.hub.hub_url}")

    def start_monitoring(self):
        with self._lock:
            if self._running: self.log("Already running."); return
            self._threads.clear(); self._stop_flags.clear(); self._stream_ais.clear()
            sender=AlertSender(self.app_cfg,self.log)

            udp_inputs=[]
            for cfg in self.app_cfg.inputs:
                if not cfg.enabled: continue
                cfg._audio_buffer =collections.deque(maxlen=int(SAMPLE_RATE*ALERT_BUFFER_SECONDS /CHUNK_SIZE)+2)
                cfg._stream_buffer=collections.deque(maxlen=int(SAMPLE_RATE*STREAM_BUFFER_SECONDS/CHUNK_SIZE)+2)
                cfg._baseline_learning_remaining=5.0; cfg._hf_baseline=None
                cfg._silence_secs=0.0; cfg._hiss_secs=0.0
                cfg._ai_status=""; cfg._ai_phase="idle"; cfg._ai_learn_samples=[]

                ai=StreamAI(cfg,self.log,sender); self._stream_ais[cfg.name]=ai; ai.start()
                dev=(cfg.device_index or '').strip().lower()
                if dev.startswith('dab://') or dev.startswith('fm://') or dev.startswith('http://') or dev.startswith('https://'):
                    stop=threading.Event(); self._stop_flags.append(stop)
                    t=threading.Thread(target=self._run_input,args=(cfg,sender,stop),daemon=True)
                    self._threads.append(t)
                else:
                    udp_inputs.append(cfg)

            if udp_inputs:
                udp_stop=threading.Event(); self._stop_flags.append(udp_stop)
                udp_t=threading.Thread(target=self._run_udp_inputs,args=(udp_inputs,sender,udp_stop),daemon=True)
                self._threads.append(udp_t)

            ai_stop=threading.Event(); self._stop_flags.append(ai_stop)
            ai_t=threading.Thread(target=self._ai_loop,args=(ai_stop,),daemon=True)
            self._threads.append(ai_t)
            # PTP monitor thread
            sender2=AlertSender(self.app_cfg,self.log)
            self.ptp=PTPMonitor(self.log, sender2)
            ptp_stop=threading.Event(); self._stop_flags.append(ptp_stop)
            ptp_t=threading.Thread(
                target=self.ptp.run,
                args=(ptp_stop, self.app_cfg.network.audio_interface_ip or "0.0.0.0"),
                daemon=True)
            self._threads.append(ptp_t)

            for t in self._threads: t.start()
            self._running=True
            self.log(f"[Monitor] Started — {sum(1 for i in self.app_cfg.inputs if i.enabled)} stream(s).")
            # Hub client is started independently at app startup via start_hub_client()
            # so it works even when monitoring is not yet running.
            # Re-start it here in case it was stopped with a previous stop_monitoring call.
            if self._hub_client is None:
                self.start_hub_client()

    def stop_monitoring(self):
        with self._lock:
            if not self._running: return
            for e in self._stop_flags: e.set()
            for t in self._threads: t.join(timeout=2.0)
            self._threads.clear(); self._stop_flags.clear()
            if self._hub_client:
                self._hub_client.stop(); self._hub_client=None
            self._running=False; self.log("[Monitor] Stopped.")

    def request_retrain(self, name: str):
        for inp in self.app_cfg.inputs:
            if inp.name==name:
                inp._ai_retrain_flag=True
                for p in (_model_path(name),_stats_path(name)):
                    try:
                        if os.path.exists(p): os.remove(p)
                    except: pass
                self.log(f"[AI] Retrain flagged for '{name}'.")
                return

    def _dab_session_key(self, serial, device_idx, channel):
        return (serial or f"idx:{device_idx}", str(channel).upper())

    def _copy_dab_metrics_from_mux(self, cfg, mux):
        try:
            demod = mux.get("demodulator", {})
            cfg._dab_snr = float(demod.get("snr", cfg._dab_snr))
        except Exception:
            pass
        try:
            sig = mux.get("demodulator", {}).get("sigLevel")
            if sig is not None:
                cfg._dab_sig = float(sig)
        except Exception:
            pass
        try:
            ens = mux.get("ensemble", {})
            lbl = ens.get("label", {})
            cfg._dab_ensemble = (lbl.get("label", "") or lbl.get("shortlabel", "")).strip()
        except Exception:
            pass

    def _find_dab_service_in_mux(self, mux, service):
        service_l = (service or "").strip().lower()
        for svc in mux.get("services", []):
            svc_lbl = svc.get("label", {})
            svc_name = (svc_lbl.get("label", "") or svc_lbl.get("shortlabel", "")).strip()
            if svc_name.lower() == service_l or service_l in svc_name.lower():
                return str(svc.get("sid", "")), svc_name
        return None, None

    def _start_dab_session(self, session, owner_name):
        import socket as _sock
        import subprocess
        import urllib.request as _ur

        name = owner_name
        with _sock.socket() as _s:
            _s.bind(("", 0))
            session.dab_port = _s.getsockname()[1]

        _wb = _find_binary("welle-cli") or "welle-cli"
        driver = f"rtl_sdr,{session.device_idx}" if session.device_idx and str(session.device_idx) != "0" else "rtl_sdr"
        cmd = [_wb, "-w", str(session.dab_port), "-c", session.channel, "-C", "1", "-g", "-1", "-F", driver]
        if session.ppm:
            self.log(f"[{name}] DAB: ignoring ppm={session.ppm} for welle-cli startup (not passing it as gain)")
        self.log(f"[{name}] DAB shared: launching {' '.join(cmd)}")

        try:
            session.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        except Exception as e:
            session.failed = True
            session.ready.set()
            self.log(f"[{name}] Failed to launch welle-cli: {e}")
            raise

        def _read_stderr():
            fatal_markers = (
                "usb_claim_interface error",
                "opening rtl-sdr failed",
                "error while opening device",
                "inputfactory:error while opening device",
            )
            try:
                for raw in session.proc.stderr:
                    try:
                        line = raw.decode(errors="ignore").strip()
                    except Exception:
                        line = str(raw).strip()
                    if not line:
                        continue
                    lower = line.lower()
                    if "error" in lower or "failed" in lower or "pll not locked" in lower:
                        self.log(f"[DAB {session.channel}] {line}")
                    if any(marker in lower for marker in fatal_markers):
                        session.failed = True
                        session.ready.set()
                        try:
                            session.stop_evt.set()
                        except Exception:
                            pass
                        return
            except Exception as e:
                try:
                    out_q.put_nowait({"diag": f"RDS proc error: {e}"})
                except Exception:
                    pass

        def _poll_mux():
            deadline = time.time() + 45
            announced = False
            while not session.stop_evt.is_set():
                if session.proc.poll() is not None:
                    session.failed = True
                    session.ready.set()
                    if not announced:
                        self.log(f"[DAB {session.channel}] welle-cli exited unexpectedly")
                    return
                if session.failed:
                    return
                try:
                    with _ur.urlopen(f"http://localhost:{session.dab_port}/mux.json", timeout=2) as r:
                        mux = json.loads(r.read())
                    if not isinstance(mux, dict):
                        raise ValueError("invalid mux payload")
                    services = mux.get("services", []) or []
                    session.mux = mux
                    if services:
                        if not session.ready.is_set():
                            session.ready.set()
                            announced = True
                            self.log(f"[DAB {session.channel}] shared mux ready on port {session.dab_port} ({len(services)} services)")
                        time.sleep(5)
                        continue
                    if time.time() >= deadline and not announced:
                        session.failed = True
                        session.ready.set()
                        self.log(f"[DAB {session.channel}] mux endpoint came up but no services appeared before timeout")
                        return
                except Exception:
                    if time.time() >= deadline and not announced:
                        session.failed = True
                        session.ready.set()
                        self.log(f"[DAB {session.channel}] shared mux session timed out waiting for services")
                        return
                time.sleep(1)

        session.stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        session.stderr_thread.start()
        session.poll_thread = threading.Thread(target=_poll_mux, daemon=True)
        session.poll_thread.start()

    def _get_or_create_dab_session(self, serial, device_idx, channel, ppm, owner_name):
        key = self._dab_session_key(serial, device_idx, channel)
        with self._dab_sessions_lock:
            session = self._dab_sessions.get(key)
            if session is not None:
                session.refcount += 1
                session.consumers.add(owner_name)
                return session

            # Important: do not claim/open the SDR in Python for DAB.
            # welle-cli must open the dongle itself.
            session = DabSharedSession(key=key, serial=serial, device_idx=int(device_idx),
                                       channel=str(channel).upper(), dab_port=0, ppm=int(ppm or 0))
            self._dab_sessions[key] = session
            session.refcount = 1
            session.consumers.add(owner_name)

        try:
            self._start_dab_session(session, owner_name)
            return session
        except Exception:
            with self._dab_sessions_lock:
                self._dab_sessions.pop(key, None)
            raise

    def _stop_dab_session(self, session):
        try:
            session.stop_evt.set()
        except Exception:
            pass
        for p in [getattr(session, 'proc', None)]:
            if not p:
                continue
            try:
                p.terminate()
                p.wait(timeout=2)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    def _release_dab_session(self, session, owner_name):
        stop_now = False
        with self._dab_sessions_lock:
            cur = self._dab_sessions.get(session.key)
            if cur is not session:
                return
            session.consumers.discard(owner_name)
            session.refcount = max(0, session.refcount - 1)
            if session.refcount == 0:
                self._dab_sessions.pop(session.key, None)
                stop_now = True
        if stop_now:
            self.log(f"[DAB {session.channel}] stopping shared mux session")
            self._stop_dab_session(session)

    def _run_dab(self, cfg, sender, stop_evt):
        """
        Decode a DAB service via a shared welle-cli mux session.
        Multiple inputs on the same serial+channel reuse one tuner/process.
        """
        import urllib.request as _ur
        import subprocess as _sp

        name = cfg.name
        spec = cfg.device_index.strip()[6:]
        service = spec.split("?", 1)[0].strip()

        freq = None
        channel_name = None
        serial = None
        ppm = 0
        if "?" in spec:
            for part in spec.split("?", 1)[1].split("&"):
                if part.startswith("freq="):
                    freq = part.split("=", 1)[1]
                elif part.startswith("channel="):
                    channel_name = part.split("=", 1)[1].strip()
                elif part.startswith("serial="):
                    serial = part.split("=", 1)[1].strip()
                elif part.startswith("ppm="):
                    try:
                        ppm = int(part.split("=", 1)[1])
                    except Exception:
                        pass

        if not _find_binary("welle-cli"):
            self.log(f"[{name}] welle-cli not found on PATH. Install welle.io to use DAB sources.")
            cfg._livewire_mode = "DAB (welle-cli missing)"
            cfg._dab_ok = False
            return

        device_idx = 0
        if serial:
            try:
                device_idx = int(sdr_manager.resolve_index(serial))
                self.log(f"[{name}] DAB: resolved serial {serial!r} → device {device_idx}")
            except SdrNotFoundError as e:
                self.log(f"[{name}] DAB: {e}")
                cfg._livewire_mode = "DAB (dongle not found)"
                cfg._dab_ok = False
                return
            except SdrBusyError:
                # Another input may already own a shared session for this mux; continue.
                pass

        if not ppm and serial:
            for dev in self.app_cfg.sdr_devices:
                if dev.serial == serial:
                    ppm = dev.ppm
                    break

        _FREQ_TO_CHANNEL = {
            "174928":"5A","177008":"5B","178352":"5C","179200":"5D",
            "183648":"6A","185360":"6B","187072":"6C","188928":"6D",
            "194064":"7A","195776":"7B","197648":"7C","199360":"7D",
            "202928":"8A","204640":"8B","206352":"8C","208064":"8D",
            "211280":"9A","213360":"9B","214928":"9C","216928":"9D",
            "220352":"10A","222064":"10B","223936":"10C","225648":"10D",
            "229072":"11A","230784":"11B","232496":"11C","234208":"11D",
            "237488":"12A","239200":"12B","240912":"12C","242624":"12D",
        }
        if channel_name:
            channel = channel_name
        elif freq:
            channel = _FREQ_TO_CHANNEL.get(str(freq), freq)
        else:
            channel = "5C"

        cfg._livewire_mode = f"DAB: {service}"
        session = None
        try:
            # DAB inputs can start at the same time. Be patient and retry so a
            # consumer can attach to the mux session that eventually wins.
            startup_deadline = time.time() + 30
            sid = None
            svc_name = None
            audio_url = None
            attempt = 0
            while time.time() < startup_deadline and not stop_evt.is_set():
                attempt += 1
                if attempt > 1:
                    self.log(f"[{name}] DAB: retrying shared mux attach (attempt {attempt})")
                    time.sleep(1.0)
                try:
                    session = self._get_or_create_dab_session(serial, device_idx, channel, ppm, name)
                except SdrNotFoundError as e:
                    self.log(f"[{name}] DAB: {e}")
                    cfg._livewire_mode = "DAB (dongle not found)"
                    cfg._dab_ok = False
                    return
                except SdrBusyError as e:
                    self.log(f"[{name}] DAB: {e}")
                    cfg._livewire_mode = "DAB (dongle in use)"
                    cfg._dab_ok = False
                    return

                if not session.ready.wait(timeout=min(12, max(1, int(startup_deadline - time.time())))) or session.failed:
                    self.log(f"[{name}] DAB: shared mux session did not become ready")
                    try:
                        self._release_dab_session(session, name)
                    except Exception:
                        pass
                    session = None
                    continue

                mux = session.mux or {}
                self._copy_dab_metrics_from_mux(cfg, mux)
                sid, svc_name = self._find_dab_service_in_mux(mux, service)
                if not sid:
                    # Give the mux a little extra time to populate services.
                    deadline = time.time() + 8
                    while time.time() < deadline and not stop_evt.is_set():
                        mux = session.mux or {}
                        self._copy_dab_metrics_from_mux(cfg, mux)
                        sid, svc_name = self._find_dab_service_in_mux(mux, service)
                        if sid:
                            break
                        time.sleep(1)
                if sid:
                    cfg._dab_service = svc_name or service
                    cfg._dab_ok = True
                    audio_url = f"http://localhost:{session.dab_port}/mp3/{sid}"
                    self.log(f"[{name}] DAB: streaming audio from {audio_url} (shared mux {channel})")
                    break

                self.log(f"[{name}] DAB: could not find service {service!r} in mux {channel}; will retry")
                try:
                    self._release_dab_session(session, name)
                except Exception:
                    pass
                session = None

            if not session or not sid or not audio_url:
                self.log(f"[{name}] DAB: failed to attach to shared mux {channel}")
                cfg._dab_ok = False
                return

            stream_ready = False
            ready_deadline = time.time() + 15
            while time.time() < ready_deadline and not stop_evt.is_set():
                if session.proc.poll() is not None:
                    self.log(f"[{name}] DAB: welle-cli exited before audio stream became ready")
                    return
                try:
                    with _ur.urlopen(audio_url, timeout=3) as _trig:
                        probe = _trig.read(32768)
                        if probe and len(probe) >= 4096:
                            stream_ready = True
                            self.log(f"[{name}] DAB: audio endpoint ready ({len(probe)} bytes)")
                            break
                except Exception as e:
                    self.log(f"[{name}] DAB: waiting for audio endpoint: {e}")
                time.sleep(0.5)

            if not stream_ready or stop_evt.is_set():
                self.log(f"[{name}] DAB: audio stream never became ready")
                return

            ffmpeg_bin = _find_binary("ffmpeg") or "ffmpeg"
            ff_cmd = [ffmpeg_bin, "-loglevel", "warning", "-i", audio_url,
                      "-f", "s16le", "-ar", str(SAMPLE_RATE),
                      "-ac", "1", "-"]
            try:
                ff_proc = _sp.Popen(ff_cmd, stdout=_sp.PIPE, stderr=_sp.PIPE, bufsize=0)
            except Exception as e:
                self.log(f"[{name}] DAB: ffmpeg failed: {e}")
                return

            def _read_ffmpeg_stderr():
                try:
                    for raw in ff_proc.stderr:
                        line = raw.decode(errors="ignore").strip()
                        if line:
                            self.log(f"[{name}] DAB/ffmpeg: {line}")
                except Exception:
                    pass

            ff_stderr_t = threading.Thread(target=_read_ffmpeg_stderr, daemon=True)
            ff_stderr_t.start()

            CHUNK_BYTES = int(SAMPLE_RATE * CHUNK_DURATION) * 2
            buf = bytearray()
            pcm_started = False
            pcm_deadline = time.time() + 15
            while not stop_evt.is_set():
                try:
                    if session.proc.poll() is not None:
                        self.log(f"[{name}] DAB: welle-cli stopped")
                        break
                    if ff_proc.poll() is not None:
                        self.log(f"[{name}] DAB: ffmpeg stopped")
                        break
                    chunk = ff_proc.stdout.read(4096)
                    if not chunk:
                        if (not pcm_started) and time.time() > pcm_deadline:
                            self.log(f"[{name}] DAB: ffmpeg connected but no PCM arrived")
                            break
                        time.sleep(0.1)
                        continue
                    pcm_started = True
                    buf.extend(chunk)
                    while len(buf) >= CHUNK_BYTES:
                        raw = bytes(buf[:CHUNK_BYTES])
                        del buf[:CHUNK_BYTES]
                        samp = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
                        samp = np.clip(samp - np.mean(samp), -1.0, 1.0)
                        cfg._stream_buffer.append(samp.copy())
                        cfg._audio_buffer.append(samp.copy())
                        cfg._live_chunk_seq = getattr(cfg, "_live_chunk_seq", 0) + 1
                        analyse_chunk(cfg, sender,
                                      lambda m, n=name: self.log(f"[{n}] {m}"),
                                      samp, CHUNK_DURATION,
                                      self.app_cfg.alert_wav_duration,
                                      self.app_cfg.inputs)
                    if int(time.time()) % 10 == 0:
                        mux = session.mux or {}
                        self._copy_dab_metrics_from_mux(cfg, mux)
                except Exception as e:
                    self.log(f"[{name}] DAB: audio loop error: {e}")
                    time.sleep(0.25)

            try:
                ff_proc.terminate()
                ff_proc.wait(timeout=2)
            except Exception:
                try:
                    ff_proc.kill()
                except Exception:
                    pass
        finally:
            if session is not None:
                self._release_dab_session(session, name)
            self.log(f"[{name}] DAB thread stopped.")

    def _run_fm(self, cfg, sender, stop_evt):
        """
        Receive and demodulate an FM broadcast stream.

        Source format:
            fm://96.7                          any available FM dongle
            fm://96.7?serial=FM_DONGLE_1               specific dongle by serial
            fm://96.7?serial=FM_DONGLE_1&ppm=5         with explicit PPM override
            fm://96.7?serial=FM_DONGLE_1&gain=35.4     with manual tuner gain (dB)
            fm://96.7?serial=FM_DONGLE_1&backend=rtl_fm   force rtl_fm backend
            fm://96.7?serial=FM_DONGLE_1&backend=pyrtlsdr force pyrtlsdr backend

        Backend selection:
            auto      — prefer rtl_fm, fall back to pyrtlsdr
            rtl_fm    — force rtl_fm
            pyrtlsdr  — force pyrtlsdr, fall back to rtl_fm if unavailable

        Audio output: 48kHz mono float32 into existing analyse_chunk pipeline.
        """
        import shutil
        name = cfg.name
        spec = cfg.device_index.strip()[5:]   # strip "fm://"
        freq_str = spec.split("?")[0].strip()

        try:
            cfg._fm_freq_mhz = float(freq_str)
        except ValueError:
            self.log(f"[{name}] FM: invalid frequency {freq_str!r}")
            return

        freq_hz = int(cfg._fm_freq_mhz * 1e6)
        cfg._livewire_mode = f"FM: {cfg._fm_freq_mhz:.1f} MHz"

        # Parse query params
        serial = None
        ppm    = 0
        gain   = None
        backend = "auto"
        for part in (spec.split("?", 1)[1].split("&") if "?" in spec else []):
            if part.startswith("serial="):
                serial = part.split("=", 1)[1].strip()
            elif part.startswith("ppm="):
                try: ppm = int(part.split("=", 1)[1])
                except: pass
            elif part.startswith("gain="):
                try: gain = float(part.split("=", 1)[1])
                except: pass
            elif part.startswith("backend="):
                try: backend = urllib.parse.unquote_plus(part.split("=", 1)[1]).strip().lower()
                except Exception: pass

        if backend in ("rtlfm", "rtl-fm"):
            backend = "rtl_fm"
        elif backend not in ("auto", "rtl_fm", "pyrtlsdr"):
            backend = "auto"

        # Resolve PPM from device registry if not overridden
        if not ppm and serial:
            for dev in monitor.app_cfg.sdr_devices:
                if dev.serial == serial:
                    ppm = dev.ppm; break

        # Claim the dongle
        device_idx = 0
        lease      = None
        if serial:
            try:
                lease = sdr_manager.claim(serial, owner=f"FM:{name}")
                device_idx = lease.index
                self.log(f"[{name}] FM: claimed dongle {serial!r} → index {device_idx}")
            except SdrNotFoundError as e:
                self.log(f"[{name}] FM: {e}")
                cfg._livewire_mode = "FM (dongle not found)"
                return
            except SdrBusyError as e:
                self.log(f"[{name}] FM: {e}")
                cfg._livewire_mode = "FM (dongle in use)"
                return

        try:
            if backend == "rtl_fm":
                self.log(f"[{name}] FM: backend forced to rtl_fm")
                self._run_fm_rtlsdr(cfg, sender, stop_evt, freq_hz, device_idx, ppm, gain)
                return

            if backend == "pyrtlsdr":
                self.log(f"[{name}] FM: backend forced to pyrtlsdr")
                if self._run_fm_pyrtlsdr(cfg, sender, stop_evt, freq_hz, device_idx, ppm, gain):
                    return
                self.log(f"[{name}] FM: pyrtlsdr unavailable — falling back to rtl_fm")
                self._run_fm_rtlsdr(cfg, sender, stop_evt, freq_hz, device_idx, ppm, gain)
                return

            # auto: prefer rtl_fm for stable audio, fall back to pyrtlsdr
            self.log(f"[{name}] FM: backend auto — preferring rtl_fm")
            if _find_binary("rtl_fm"):
                self._run_fm_rtlsdr(cfg, sender, stop_evt, freq_hz, device_idx, ppm, gain)
                return

            self.log(f"[{name}] FM: rtl_fm unavailable — trying pyrtlsdr")
            if self._run_fm_pyrtlsdr(cfg, sender, stop_evt, freq_hz, device_idx, ppm, gain):
                return

            self.log(f"[{name}] FM: no usable backend found")
            cfg._livewire_mode = "FM (no backend)"
        finally:
            if lease:
                lease.__exit__(None, None, None)

    # ── pyrtlsdr backend ──────────────────────────────────────────────────────

    def _run_fm_pyrtlsdr(self, cfg, sender, stop_evt,
                          freq_hz: int, device_idx: int, ppm: int,
                          gain: float | None = None) -> bool:
        """
        Demodulate FM using pyrtlsdr (pure Python I/Q).
        Returns True if it ran (even if it errored), False if pyrtlsdr unavailable.
        Provides: audio, signal strength (dBm), SNR, stereo detection, RDS PS/RT.
        """
        try:
            from rtlsdr import RtlSdr
        except ImportError:
            return False

        name = cfg.name
        cfg._fm_backend = "pyrtlsdr"

        # RTL-SDR sample rate for FM: request 2.4 MHz, then read back the
        # actual rate the driver accepted. Some dongle/driver combos coerce
        # this, and assuming an exact 2.400 MHz can make playback speed wrong.
        SDR_FS_REQ = 240_000
        AUDIO_FS   = 48_000

        sdr = None
        try:
            sdr = RtlSdr(device_index=device_idx)
            sdr.sample_rate = SDR_FS_REQ
            actual_sdr_fs = int(round(float(sdr.sample_rate)))
            sdr.center_freq = freq_hz

            try:
                # Disable RTL AGC first so manual tuner gain actually takes effect.
                # When no manual gain is requested we leave the tuner in automatic mode.
                sdr.set_agc_mode(False)
            except Exception:
                pass

            try:
                if gain is None:
                    # Fixed gain is more predictable for broadcast FM and tends to
                    # behave better for both audio and RDS than tuner auto mode.
                    sdr.gain = 38.0
                    gain_mode = "manual-default(38.0 dB)"
                else:
                    sdr.gain = float(gain)
                    gain_mode = f"manual({float(gain):.1f} dB)"
            except Exception as e:
                self.log(f"[{name}] FM pyrtlsdr: gain set failed ({gain!r}): {e}")
                try:
                    sdr.gain = 38.0
                    gain_mode = "manual-fallback(38.0 dB)"
                except Exception as e2:
                    self.log(f"[{name}] FM pyrtlsdr: fallback gain set failed: {e2}")
                    gain_mode = "unknown"

            if ppm:
                try:
                    sdr.freq_correction = ppm
                except Exception as e:
                    self.log(f"[{name}] FM pyrtlsdr: could not apply ppm correction {ppm}: {e}")

            self.log(f"[{name}] FM pyrtlsdr: {freq_hz/1e6:.3f} MHz  "
                     f"fs_req={SDR_FS_REQ/1e6:.3f}MS/s  fs_actual={actual_sdr_fs/1e6:.3f}MS/s  ppm={ppm}  gain={gain_mode}")

            # Read buffer: ~100ms of I/Q at 2.4MS/s = 240000 complex samples
            READ_SIZE = 65536   # balance USB overhead and realtime DSP cadence at 240 kS/s

            # RDS state / audio DSP state
            _audio_accum = np.empty(0, dtype=np.float32)
            _fm_started = False
            cfg._fm_rds_buf = None
            cfg._fm_rds_fs = actual_sdr_fs
            cfg._fm_rds_bits = list(getattr(cfg, "_fm_rds_bits", []) or [])
            import multiprocessing as _mp, queue as _queue
            _rds_in_q = _mp.Queue(maxsize=1)
            _rds_out_q = _mp.Queue(maxsize=4)
            try:
                _rds_in_q.cancel_join_thread()
                _rds_out_q.cancel_join_thread()
            except Exception:
                pass
            _rds_proc = _mp.Process(target=self._fm_rds_proc_worker, args=(_rds_in_q, _rds_out_q), daemon=True)
            _rds_proc.start()
            try:
                self.log(f"[{name}] RDS proc started pid={_rds_proc.pid}")
            except Exception:
                pass
            cfg._fm_rds_status = getattr(cfg, "_fm_rds_status", "Detecting…")
            cfg._fm_rds_metric = getattr(cfg, "_fm_rds_metric", 0.0)
            cfg._fm_rds_best_phase = getattr(cfg, "_fm_rds_best_phase", -1)
            cfg._fm_rds_valid_groups = getattr(cfg, "_fm_rds_valid_groups", 0)
            FM_PREBUFFER_CHUNKS = 3
            MAX_FM_BUFFER = CHUNK_SIZE * 10
            _deemph_prev = 0.0
            _dc_prev_in = 0.0
            _dc_prev_out = 0.0
            _audio_lpf_b = None
            _audio_lpf_a = None
            _audio_lpf_zi = None
            _dc_b = None
            _dc_a = None
            _dc_zi = None
            _deemph_b = None
            _deemph_a = None
            _deemph_zi = None

            # Gentle deemphasis for UK/Europe FM broadcast (50 µs)
            _deemph_alpha = math.exp(-1.0 / (AUDIO_FS * 50e-6))
            # Simple DC blocker coefficient
            _dc_r = 0.995
            _dc_b = np.asarray([1.0, -1.0], dtype=np.float32)
            _dc_a = np.asarray([1.0, -_dc_r], dtype=np.float32)
            _deemph_b = np.asarray([1.0 - _deemph_alpha], dtype=np.float32)
            _deemph_a = np.asarray([1.0, -_deemph_alpha], dtype=np.float32)

            while not stop_evt.is_set():
                try:
                    iq = sdr.read_samples(READ_SIZE)
                    iq = np.asarray(iq, dtype=np.complex64)
                except Exception as e:
                    self.log(f"[{name}] FM read error: {e}")
                    time.sleep(0.5)
                    continue

                # ── Signal strength ───────────────────────────────────────────
                power_w            = float(np.mean(np.abs(iq) ** 2))
                cfg._fm_signal_dbm = float(10 * np.log10(power_w + 1e-12) + 30)

                # ── Wideband FM demodulation ──────────────────────────────────
                # Keep the previously-working discriminator, but make the audio
                # chain gentler and better behaved rather than rewriting FM DSP
                # wholesale in one jump.
                # Use a raw phase-difference discriminator. The earlier
                # normalised-IQ path made the FM audio sound "digitally encoded"
                # / phasey on some dongles even when rtl_fm sounded fine.
                try:
                    demod = np.angle(iq[1:] * np.conj(iq[:-1])).astype(np.float32, copy=False)
                except Exception as e:
                    self.log(f"[{name}] FM demod error: {e}")
                    time.sleep(0.5)
                    continue

                try:
                    # Conservative scaling so strong stations do not immediately
                    # slam into full scale before deemphasis/limiting.
                    demod *= actual_sdr_fs / (2 * np.pi * 140_000.0)

                    # Keep the demodulated FM at the *actual* SDR sample rate and
                    # avoid intermediate-rate assumptions.
                    demod_fs = float(actual_sdr_fs)
                except Exception as e:
                    self.log(f"[{name}] FM post-demod scale error: {e}")
                    time.sleep(0.5)
                    continue

                try:
                    # ── Noise floor for SNR estimate ──────────────────────────────
                    signal_pwr = float(np.var(demod))
                    noise_pwr  = float(np.var(np.diff(demod))) / 2 + 1e-12
                    cfg._fm_snr_db = float(10 * np.log10(max(signal_pwr, 1e-12) / noise_pwr))
                except Exception as e:
                    self.log(f"[{name}] FM snr error: {e}")
                    time.sleep(0.5)
                    continue

                try:
                    # ── Stereo pilot detection (19 kHz) ──────────────────────────
                    fft_size = min(8192, len(demod))
                    fft_mag  = np.abs(np.fft.rfft(demod[:fft_size]))
                    pilot_bin = int(19_000 * fft_size / max(float(demod_fs), 1.0))
                    if 0 < pilot_bin < len(fft_mag):
                        pilot_pwr = float(fft_mag[pilot_bin])
                        noise_ref = float(np.median(fft_mag[max(0, pilot_bin-10):pilot_bin] + 1e-6))
                        cfg._fm_stereo = pilot_pwr > noise_ref * 5
                except Exception as e:
                    self.log(f"[{name}] FM stereo error: {e}")
                    time.sleep(0.5)
                    continue

                try:
                    # ── RDS handoff (separate process; latest slice only) ─────────────
                    cfg._fm_rds_fs = int(demod_fs)
                    _slice = np.asarray(demod[-32768:], dtype=np.float32).copy()
                    try:
                        while True:
                            _latest = _rds_out_q.get_nowait()
                            if "diag" in _latest:
                                self.log(f"[{name}] {_latest['diag']}")
                                continue
                            cfg._fm_rds_status = _latest.get("status", getattr(cfg, "_fm_rds_status", ""))
                            cfg._fm_rds_metric = _latest.get("metric", getattr(cfg, "_fm_rds_metric", 0.0))
                            cfg._fm_rds_best_phase = _latest.get("phase", getattr(cfg, "_fm_rds_best_phase", -1))
                            cfg._fm_rds_valid_groups = _latest.get("valid", getattr(cfg, "_fm_rds_valid_groups", 0))
                            cfg._fm_rds_ps = _latest.get("ps", getattr(cfg, "_fm_rds_ps", ""))
                            cfg._fm_rds_rt = _latest.get("rt", getattr(cfg, "_fm_rds_rt", ""))
                            cfg._fm_rds_last_good = _latest.get("last_good", getattr(cfg, "_fm_rds_last_good", 0.0))
                            cfg._fm_rds_bp_rms = _latest.get("bp", getattr(cfg, "_fm_rds_bp_rms", 0.0))
                            cfg._fm_rds_bb_rms = _latest.get("bb", getattr(cfg, "_fm_rds_bb_rms", 0.0))
                            cfg._fm_rds_sym_metric = _latest.get("sym", getattr(cfg, "_fm_rds_sym_metric", 0.0))
                            try:
                                import time as _time
                                _now = _time.time()
                                _sig = (cfg._fm_rds_status, round(float(cfg._fm_rds_metric), 4), int(cfg._fm_rds_best_phase), int(cfg._fm_rds_valid_groups))
                                if _sig != getattr(cfg, "_fm_rds_last_sig", None) or (_now - float(getattr(cfg, "_fm_rds_last_log", 0.0) or 0.0)) >= 3.0:
                                    cfg._fm_rds_last_sig = _sig
                                    cfg._fm_rds_last_log = _now
                                    self.log(f"[{name}] RDS result: status={cfg._fm_rds_status} metric={cfg._fm_rds_metric:.6f} phase={cfg._fm_rds_best_phase} valid={cfg._fm_rds_valid_groups}")
                            except Exception:
                                pass
                    except Exception:
                        pass
                    try:
                        import time as _time
                        _now = _time.time()
                        if (_now - float(getattr(cfg, "_fm_rds_handoff_last", 0.0) or 0.0)) >= 2.0:
                            cfg._fm_rds_handoff_last = _now
                            self.log(f"[{name}] RDS handoff: n={len(_slice)} fs={int(demod_fs)}")
                        _rds_in_q.put_nowait((_slice, int(demod_fs)))
                    except Exception:
                        try:
                            _rds_in_q.get_nowait()
                        except Exception:
                            pass
                        try:
                            _rds_in_q.put_nowait((_slice, int(demod_fs)))
                        except Exception:
                            pass
                except Exception as e:
                    self.log(f"[{name}] FM rds handoff error: {e}")

                # ── Make proper mono FM audio and decimate directly 960 kHz → 48 kHz ──
                # Keep this path computationally cheap: stateful LPF followed by
                # clean integer decimation by 20. This removes the heavier resample
                # stage that was falling behind realtime.
                audio_if = demod.astype(np.float32, copy=False)
                target_audio_fs = AUDIO_FS
                decim_to_audio = max(1, int(round(float(demod_fs) / float(target_audio_fs))))
                if decim_to_audio < 1:
                    decim_to_audio = 1
                target_cut = 15_000.0
                if audio_if.size:
                    try:
                        raise ImportError("force audio fallback")
                        from scipy.signal import butter, sosfilt, sosfilt_zi, lfilter, lfilter_zi
                        if (_audio_lpf_b is None or _audio_lpf_a is None or
                            int(round(demod_fs)) != getattr(self, '_fm_dbg_lpf_fs', None) or
                            decim_to_audio != getattr(self, '_fm_dbg_lpf_decim', None)):
                            cutoff = min(target_cut, 0.45 * float(target_audio_fs))
                            wn = cutoff / max(float(demod_fs) / 2.0, 1.0)
                            wn = min(max(wn, 1e-4), 0.99)
                            sos = butter(6, wn, btype="low", output="sos")
                            _audio_lpf_b = np.asarray(sos, dtype=np.float32)
                            _audio_lpf_a = None
                            try:
                                zi = sosfilt_zi(_audio_lpf_b).astype(np.float32)
                                _audio_lpf_zi = zi * float(audio_if[0] if audio_if.size else 0.0)
                            except Exception:
                                _audio_lpf_zi = None
                            self._fm_dbg_lpf_fs = int(round(demod_fs))
                            self._fm_dbg_lpf_decim = decim_to_audio
                        if _audio_lpf_zi is not None:
                            audio_if, _audio_lpf_zi = sosfilt(_audio_lpf_b, audio_if, zi=_audio_lpf_zi)
                        else:
                            audio_if = sosfilt(_audio_lpf_b, audio_if)
                        audio_if = np.asarray(audio_if, dtype=np.float32)
                    except Exception:
                        # Fallback FIR if SciPy SOS is unavailable.
                        taps = 121
                        fc = min(target_cut, 0.45 * float(target_audio_fs)) / max(float(demod_fs), 1.0)
                        n = np.arange(taps, dtype=np.float32) - (taps - 1) / 2.0
                        h = 2.0 * fc * np.sinc(2.0 * fc * n)
                        w = np.hamming(taps).astype(np.float32)
                        h = (h * w).astype(np.float32)
                        h /= np.sum(h) + 1e-12
                        audio_if = np.convolve(audio_if, h, mode="same").astype(np.float32, copy=False)

                # Direct integer decimation to the final 48 kHz audio rate.
                if decim_to_audio > 1:
                    audio = audio_if[::decim_to_audio].astype(np.float32, copy=False)
                else:
                    audio = audio_if.astype(np.float32, copy=False)

                if audio.size:
                    # Vectorised IIR stages are much cheaper than Python per-sample
                    # loops and keep the FM path closer to realtime.
                    try:
                        raise ImportError("force audio fallback")
                        if _dc_zi is None:
                            _dc_zi = lfilter_zi(_dc_b, _dc_a).astype(np.float32) * float(audio[0])
                        audio, _dc_zi = lfilter(_dc_b, _dc_a, audio, zi=_dc_zi)
                        audio = np.asarray(audio, dtype=np.float32)

                        if _deemph_zi is None:
                            _deemph_zi = lfilter_zi(_deemph_b, _deemph_a).astype(np.float32) * float(audio[0])
                        audio, _deemph_zi = lfilter(_deemph_b, _deemph_a, audio, zi=_deemph_zi)
                        audio = np.asarray(audio, dtype=np.float32)
                    except Exception:
                        # Fallback to the original scalar path if SciPy stateful
                        # filtering is unavailable for any reason.
                        out = np.empty_like(audio)
                        x_prev = _dc_prev_in
                        y_prev = _dc_prev_out
                        for i, x in enumerate(audio):
                            y = x - x_prev + _dc_r * y_prev
                            out[i] = y
                            x_prev = float(x)
                            y_prev = float(y)
                        _dc_prev_in = x_prev
                        _dc_prev_out = y_prev
                        audio = out

                        out = np.empty_like(audio)
                        y_prev = _deemph_prev
                        a = _deemph_alpha
                        b = 1.0 - a
                        for i, x in enumerate(audio):
                            y_prev = a * y_prev + b * float(x)
                            out[i] = y_prev
                        _deemph_prev = float(y_prev)
                        audio = out

                    # Remove residual DC and scale gently rather than trying to
                    # run right up to full scale.
                    audio = audio - np.mean(audio)
                    peak = float(np.max(np.abs(audio)) + 1e-9)
                    if peak > 0.85:
                        audio = audio * (0.85 / peak)

                    # Soft limiting instead of flat clipping.
                    audio = np.tanh(audio * 1.2) / np.tanh(1.2)
                    audio = np.clip(audio, -0.95, 0.95).astype(np.float32, copy=False)


                    # Accumulate FM output and only emit exact half-second blocks.
                    # Prebuffer a few chunks so small cadence variations in the
                    # SDR read/demod path do not sound like sped-up/glitchy audio.
                    if _audio_accum.size:
                        _audio_accum = np.concatenate((_audio_accum, audio))
                    else:
                        _audio_accum = audio

                    if len(_audio_accum) > MAX_FM_BUFFER:
                        _audio_accum = _audio_accum[-MAX_FM_BUFFER:]

                    if not _fm_started and len(_audio_accum) >= (CHUNK_SIZE * FM_PREBUFFER_CHUNKS):
                        _fm_started = True

                    while _fm_started and len(_audio_accum) >= CHUNK_SIZE:
                        chunk = _audio_accum[:CHUNK_SIZE].copy()
                        _audio_accum = _audio_accum[CHUNK_SIZE:]
                        try:
                            _rms = float(np.sqrt(np.mean(np.square(chunk)))) if len(chunk) else 0.0
                            _peak = float(np.max(np.abs(chunk))) if len(chunk) else 0.0
                        except Exception:
                            pass
                        cfg._stream_buffer.append(chunk.copy())
                        cfg._audio_buffer.append(chunk.copy())
                        cfg._live_chunk_seq = getattr(cfg, "_live_chunk_seq", 0) + 1
                        analyse_chunk(cfg, sender,
                                      lambda m, n=name: self.log(f"[{n}] {m}"),
                                      chunk, CHUNK_DURATION,
                                      self.app_cfg.alert_wav_duration,
                                      self.app_cfg.inputs)

        except Exception as e:
            self.log(f"[{name}] FM pyrtlsdr error: {e}")
        finally:
            try:
                if '_rds_in_q' in locals():
                    try:
                        _rds_in_q.put_nowait(None)
                    except Exception:
                        pass
                if '_rds_proc' in locals() and _rds_proc.is_alive():
                    _rds_proc.join(timeout=0.5)
                    if _rds_proc.is_alive():
                        _rds_proc.terminate()
            except Exception:
                pass
            if sdr:
                try: sdr.close()
                except: pass

        return True

    
    



    def _fm_rds_detect_light(self, demod, sdr_fs, cfg):
        """
        Lightweight RDS detector for subprocess use.
        Detects 57 kHz subcarrier presence and a rough symbol-phase metric
        without attempting full group parsing, so it cannot stall audio.
        """
        import numpy as np
        x = np.asarray(demod, dtype=np.float32).reshape(-1)
        fs = float(max(sdr_fs, 1))
        if x.size < 4096:
            cfg._fm_rds_status = "No lock"
            cfg._fm_rds_metric = 0.0
            cfg._fm_rds_best_phase = -1
            cfg._fm_rds_bp_rms = 0.0
            cfg._fm_rds_bb_rms = 0.0
            cfg._fm_rds_sym_metric = 0.0
            return

        # Mix the 57 kHz RDS region to baseband.
        n = np.arange(x.size, dtype=np.float64)
        osc = np.exp(-1j * 2.0 * np.pi * 57000.0 * n / fs)
        bb = x.astype(np.float64, copy=False) * osc

        # Very cheap LPF via moving average.
        taps = 96
        k = np.ones(taps, dtype=np.float64) / float(taps)
        bb_i = np.convolve(bb.real, k, mode="same")
        bb_q = np.convolve(bb.imag, k, mode="same")
        bb_lp = bb_i + 1j * bb_q

        # Diagnostics.
        cfg._fm_rds_bp_rms = float(np.sqrt(np.mean(np.square(x))) + 1e-12)
        cfg._fm_rds_bb_rms = float(np.sqrt(np.mean(np.abs(bb_lp) ** 2)) + 1e-12)
        cfg._fm_rds_metric = cfg._fm_rds_bb_rms

        # Crude phase/symbol metric: examine several phases at ~202 samples/symbol.
        sps = max(8, int(round(fs / 1187.5)))
        best_phase = -1
        best_metric = 0.0
        for ph in range(min(sps, 32)):
            sy = bb_lp[ph::sps]
            if sy.size < 8:
                continue
            d = sy[1:] * np.conj(sy[:-1])
            score = float(np.mean(np.abs(np.real(d))))
            if score > best_metric:
                best_metric = score
                best_phase = ph
        cfg._fm_rds_sym_metric = float(best_metric)
        cfg._fm_rds_best_phase = int(best_phase)

        # Conservative statusing only. Do not invent PS/RT without real group decode.
        if cfg._fm_rds_bb_rms > 0.003 or best_metric > 0.003:
            cfg._fm_rds_status = "Carrier seen, no valid groups"
        else:
            cfg._fm_rds_status = "No lock"


    def _fm_rds_decode_bounded(self, demod, sdr_fs, rds_bits, cfg):
        """
        Try full RDS decode on a small slice without using signal-based timeouts.
        This avoids corrupting SciPy internals while still keeping heavy work modest.
        """
        import numpy as np
        try:
            demod = np.asarray(demod, dtype=np.float32)
            return self._fm_decode_rds(demod, int(sdr_fs), rds_bits, cfg)
        except Exception as e:
            try:
                # Keep the light-detector status rather than forcing hard errors into
                # the UI every time a speculative full-decode pass fails.
                cfg._fm_rds_status = getattr(cfg, "_fm_rds_status", "Carrier seen, no valid groups") or "Carrier seen, no valid groups"
                self.log(f"[{getattr(cfg, 'name', 'FM')}] RDS bounded decode error: {e}")
            except Exception:
                pass
            return rds_bits

    def _fm_rds_proc_worker(self, in_q, out_q):
        """
        Separate-process RDS worker.
        Receives (demod, fs) tuples, decodes best-effort RDS, emits latest status/state dict.
        """
        import time
        import numpy as np
        class _Cfg: pass
        cfg = _Cfg()
        cfg.name = "FM"
        rds_bits = []
        _last_diag = 0.0
        _decode_every = 10
        _decode_ctr = 0
        while True:
            item = in_q.get()
            if item is None:
                break
            try:
                demod, fs = item
                now = time.time()
                if (now - _last_diag) >= 2.0:
                    out_q.put_nowait({"diag": f"RDS proc recv: n={len(demod)} fs={int(fs)}"})
                    _last_diag = now

                # Always run the lightweight detector so UI remains responsive.
                self._fm_rds_detect_light(np.asarray(demod, dtype=np.float32), int(fs), cfg)

                # Only attempt full group parsing occasionally, and only when the
                # light detector says there is enough 57 kHz energy to be worth trying.
                _decode_ctr += 1
                if (getattr(cfg, "_fm_rds_metric", 0.0) or 0.0) > 0.0035 and _decode_ctr >= _decode_every:
                    _decode_ctr = 0
                    # Smaller bounded slice for the heavy parser.
                    rds_bits = self._fm_rds_decode_bounded(np.asarray(demod[-6144:], dtype=np.float32),
                                                           int(fs), rds_bits, cfg, timeout_s=0.20)
                    if len(rds_bits) > 6000:
                        rds_bits = rds_bits[-3000:]

                out_q.put_nowait({
                    "status": getattr(cfg, "_fm_rds_status", ""),
                    "metric": float(getattr(cfg, "_fm_rds_metric", 0.0) or 0.0),
                    "phase": int(getattr(cfg, "_fm_rds_best_phase", -1) or -1),
                    "valid": int(getattr(cfg, "_fm_rds_valid_groups", 0) or 0),
                    "ps": getattr(cfg, "_fm_rds_ps", "") or "",
                    "rt": getattr(cfg, "_fm_rds_rt", "") or "",
                    "last_good": float(getattr(cfg, "_fm_rds_last_good", 0.0) or 0.0),
                    "bp": float(getattr(cfg, "_fm_rds_bp_rms", 0.0) or 0.0),
                    "bb": float(getattr(cfg, "_fm_rds_bb_rms", 0.0) or 0.0),
                    "sym": float(getattr(cfg, "_fm_rds_sym_metric", 0.0) or 0.0),
                })
            except Exception:
                pass

    def _fm_rds_worker(self, cfg, stop_evt):
        """
        Background RDS worker for FM inputs.
        Consumes only the latest demod slice so audio never blocks on RDS.
        """
        import time
        rds_bits = list(getattr(cfg, "_fm_rds_bits", []) or [])
        while not stop_evt.is_set():
            demod = getattr(cfg, "_fm_rds_buf", None)
            fs = getattr(cfg, "_fm_rds_fs", 0)
            cfg._fm_rds_buf = None
            if demod is None or not fs:
                time.sleep(0.05)
                continue
            try:
                rds_bits = self._fm_decode_rds(demod, int(fs), rds_bits, cfg)
                cfg._fm_rds_bits = rds_bits[-6000:]
                time.sleep(0.02)
            except Exception as e:
                try:
                    cfg._fm_rds_status = f"RDS err: {e}"
                    self.log(f"[{getattr(cfg, 'name', 'FM')}] RDS worker error: {e}")
                except Exception:
                    pass
                time.sleep(0.1)

    def _fm_decode_rds(self, demod: np.ndarray, sdr_fs: int,
                        rds_bits: list, cfg) -> list:
        """
        More robust best-effort RDS decoder.

        Pipeline:
          discriminator/composite -> 57 kHz band-pass -> complex mix-down
          -> ~2.4 kHz LPF -> resample to 19 kHz
          -> differential BPSK symbol extraction at 16 samples/symbol
          -> CRC/offset checked group parsing.

        The UI is only updated after repeated agreement so junk text is suppressed.
        """
        try:
            from scipy.signal import butter, lfilter, lfilter_zi, resample_poly
            import numpy as np
            import math
            import time
        except Exception as e:
            try:
                cfg._fm_rds_status = f"RDS err: {e}"
                cfg._fm_rds_metric = 0.0
                cfg._fm_rds_best_phase = -1
                self.log(f"[{getattr(cfg, 'name', 'FM')}] RDS decode error: {e}")
            except Exception:
                pass
            return rds_bits

        try:
            fs = float(max(sdr_fs, 1))

            if not hasattr(cfg, "_fm_rds_phase"):
                cfg._fm_rds_phase = 0.0

            # Persistent pre-mix 57 kHz band-pass.
            if (not hasattr(cfg, "_fm_rds_bp_b") or not hasattr(cfg, "_fm_rds_bp_a") or
                    getattr(cfg, "_fm_rds_bp_fs", None) != int(round(fs))):
                nyq = max(fs / 2.0, 1.0)
                lo = max(54000.0 / nyq, 1e-4)
                hi = min(60000.0 / nyq, 0.999)
                if hi <= lo:
                    cfg._fm_rds_status = "No lock"
                    cfg._fm_rds_metric = 0.0
                    cfg._fm_rds_best_phase = -1
                    return rds_bits
                b_bp, a_bp = butter(4, [lo, hi], btype="bandpass")
                cfg._fm_rds_bp_b = np.asarray(b_bp, dtype=np.float64)
                cfg._fm_rds_bp_a = np.asarray(a_bp, dtype=np.float64)
                zi_bp = lfilter_zi(cfg._fm_rds_bp_b, cfg._fm_rds_bp_a)
                cfg._fm_rds_bp_zi = zi_bp.astype(np.float64)
                cfg._fm_rds_bp_fs = int(round(fs))

            # Baseband LPF after mix-down.
            if (not hasattr(cfg, "_fm_rds_b") or not hasattr(cfg, "_fm_rds_a") or
                    getattr(cfg, "_fm_rds_lp_fs", None) != int(round(fs))):
                nyq = max(fs / 2.0, 1.0)
                cutoff = min(2400.0, nyq * 0.45)
                b, a = butter(4, cutoff / nyq, btype="low")
                cfg._fm_rds_b = np.asarray(b, dtype=np.float64)
                cfg._fm_rds_a = np.asarray(a, dtype=np.float64)
                zi = lfilter_zi(cfg._fm_rds_b, cfg._fm_rds_a)
                cfg._fm_rds_zi_i = zi.astype(np.float64)
                cfg._fm_rds_zi_q = zi.astype(np.float64)
                cfg._fm_rds_lp_fs = int(round(fs))

            # UI/diagnostic state.
            if not hasattr(cfg, "_fm_rds_ps_counts"):
                cfg._fm_rds_ps_counts = {}
            if not hasattr(cfg, "_fm_rds_rt_counts"):
                cfg._fm_rds_rt_counts = {}
            if not hasattr(cfg, "_fm_rds_last_good"):
                cfg._fm_rds_last_good = 0.0
            if not hasattr(cfg, "_fm_rds_ok"):
                cfg._fm_rds_ok = False
            if not hasattr(cfg, "_fm_rds_ps"):
                cfg._fm_rds_ps = ""
            if not hasattr(cfg, "_fm_rds_rt"):
                cfg._fm_rds_rt = ""
            if not hasattr(cfg, "_fm_rds_valid_groups"):
                cfg._fm_rds_valid_groups = 0
            if not hasattr(cfg, "_fm_rds_candidates"):
                cfg._fm_rds_candidates = 0
            if not hasattr(cfg, "_fm_rds_status"):
                cfg._fm_rds_status = "No lock"
            if not hasattr(cfg, "_fm_rds_metric"):
                cfg._fm_rds_metric = 0.0
            if not hasattr(cfg, "_fm_rds_best_phase"):
                cfg._fm_rds_best_phase = -1
            if not hasattr(cfg, "_fm_rds_bp_rms"):
                cfg._fm_rds_bp_rms = 0.0

            x = np.asarray(demod, dtype=np.float64).reshape(-1)
            if x.size < 512:
                cfg._fm_rds_status = "No lock"
                cfg._fm_rds_metric = 0.0
                cfg._fm_rds_best_phase = -1
                return rds_bits

            # Isolate the 57 kHz RDS region before mixing.
            x_bp, cfg._fm_rds_bp_zi = lfilter(cfg._fm_rds_bp_b, cfg._fm_rds_bp_a, x, zi=cfg._fm_rds_bp_zi)
            cfg._fm_rds_bp_rms = float(np.sqrt(np.mean(np.square(x_bp))) + 0.0)

            # Downconvert 57 kHz to baseband with phase continuity.
            n = np.arange(len(x_bp), dtype=np.float64)
            w = 2.0 * np.pi * 57000.0 / fs
            phase0 = float(cfg._fm_rds_phase)
            osc = np.exp(-1j * (phase0 + w * n))
            cfg._fm_rds_phase = float((phase0 + w * len(x_bp)) % (2.0 * np.pi))
            bb = x_bp * osc

            # LPF I/Q to recover BPSK baseband.
            i_filt, cfg._fm_rds_zi_i = lfilter(cfg._fm_rds_b, cfg._fm_rds_a, bb.real, zi=cfg._fm_rds_zi_i)
            q_filt, cfg._fm_rds_zi_q = lfilter(cfg._fm_rds_b, cfg._fm_rds_a, bb.imag, zi=cfg._fm_rds_zi_q)
            bb_lp = i_filt + 1j * q_filt

            metric = float(np.sqrt(np.mean(np.abs(bb_lp) ** 2))) if bb_lp.size else 0.0
            cfg._fm_rds_metric = metric

            # Resample to 19 kHz so we get exactly 16 samples/symbol.
            fs_in = int(round(fs))
            g = math.gcd(fs_in, 19000)
            up = 19000 // g
            down = fs_in // g
            bb19 = resample_poly(bb_lp, up, down)
            if bb19.size < 64:
                cfg._fm_rds_status = "No lock" if metric < 0.002 else "Carrier seen"
                cfg._fm_rds_best_phase = -1
                return rds_bits

            # Choose the best 16-sample symbol phase.
            SPS = 16
            best_phase = -1
            best_metric = -1.0
            best_sy = None
            for ph in range(SPS):
                sy = bb19[ph::SPS]
                if sy.size < 16:
                    continue
                d = sy[1:] * np.conj(sy[:-1])
                score = float(np.mean(np.abs(np.real(d))))
                if score > best_metric:
                    best_metric = score
                    best_phase = ph
                    best_sy = sy

            cfg._fm_rds_best_phase = int(best_phase)
            cfg._fm_rds_candidates += 1

            if best_sy is None or best_metric <= 0:
                cfg._fm_rds_status = "No lock" if metric < 0.002 else "Carrier seen, no valid groups"
                return rds_bits

            if metric < 0.002 and best_metric < 0.002:
                cfg._fm_rds_status = "No lock"
            elif best_metric < 0.01:
                cfg._fm_rds_status = "Carrier seen, no valid groups"
            else:
                cfg._fm_rds_status = f"Detecting… ({cfg._fm_rds_candidates} candidates)"

            # Differential BPSK decode.
            d = best_sy[1:] * np.conj(best_sy[:-1])
            bits = (np.real(d) < 0.0).astype(np.uint8).tolist()
            rds_bits.extend(bits)

            if len(rds_bits) > 12000:
                rds_bits = rds_bits[-6000:]

            # CRC / offset checked group parsing.
            POLY = 0x5B9
            OFF_A = 0x0FC
            OFF_B = 0x198
            OFF_C = 0x168
            OFF_CP = 0x350
            OFF_D = 0x1B4
            allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 .,/?:;!+-&'()[]")

            def bits_to_int(bitseq):
                v = 0
                for b in bitseq:
                    v = (v << 1) | int(b)
                return v

            def crc10(word16):
                reg = word16 << 10
                top = 1 << 25
                while reg >= top:
                    shift = reg.bit_length() - 26
                    reg ^= (POLY << (shift + 10))
                return reg & 0x3FF

            def check_block(bits26):
                if len(bits26) != 26:
                    return (False, None, None)
                raw = bits_to_int(bits26)
                data = (raw >> 10) & 0xFFFF
                check = raw & 0x3FF
                synd = crc10(data) ^ check
                if synd == OFF_A:  return (True, 'A', data)
                if synd == OFF_B:  return (True, 'B', data)
                if synd == OFF_C:  return (True, 'C', data)
                if synd == OFF_CP: return (True, "C'", data)
                if synd == OFF_D:  return (True, 'D', data)
                return (False, None, None)

            now = time.time()
            valid_this_call = 0
            i = 0
            max_scan = max(0, len(rds_bits) - 104)
            while i <= max_scan:
                okA, typA, blkA = check_block(rds_bits[i:i+26])
                okB, typB, blkB = check_block(rds_bits[i+26:i+52])
                okC, typC, blkC = check_block(rds_bits[i+52:i+78])
                okD, typD, blkD = check_block(rds_bits[i+78:i+104])

                if okA and okB and okD and typA == 'A' and typB == 'B' and typD == 'D' and typC in ('C', "C'"):
                    valid_this_call += 1
                    cfg._fm_rds_valid_groups += 1
                    cfg._fm_rds_last_good = now
                    cfg._fm_rds_ok = True
                    group_type = (blkB >> 12) & 0xF
                    ver_b = (blkB >> 11) & 0x1

                    if group_type == 0:
                        idx = blkB & 0x3
                        c1 = chr((blkD >> 8) & 0xFF)
                        c2 = chr(blkD & 0xFF)
                        if c1 in allowed and c2 in allowed:
                            current = list((cfg._fm_rds_ps or " " * 8).ljust(8)[:8])
                            pos = idx * 2
                            if pos + 1 < 8:
                                current[pos] = c1
                                current[pos + 1] = c2
                                cand = "".join(current).strip()
                                if cand:
                                    cfg._fm_rds_ps_counts[cand] = cfg._fm_rds_ps_counts.get(cand, 0) + 1
                                    if cfg._fm_rds_ps_counts[cand] >= 2:
                                        cfg._fm_rds_ps = cand
                                        cfg._fm_rds_status = f"Locked ({cfg._fm_rds_valid_groups} valid)"

                    if group_type == 2:
                        seg = blkB & 0xF
                        chars = []
                        if ver_b == 0:
                            chars.extend([chr((blkC >> 8) & 0xFF), chr(blkC & 0xFF),
                                          chr((blkD >> 8) & 0xFF), chr(blkD & 0xFF)])
                        else:
                            chars.extend([chr((blkD >> 8) & 0xFF), chr(blkD & 0xFF)])
                        if all(ch in allowed for ch in chars):
                            cur = list((cfg._fm_rds_rt or " " * 64).ljust(64)[:64])
                            base = seg * (4 if ver_b == 0 else 2)
                            for j, ch in enumerate(chars):
                                if base + j < len(cur):
                                    cur[base + j] = ch
                            cand = "".join(cur).rstrip()
                            if cand:
                                cfg._fm_rds_rt_counts[cand] = cfg._fm_rds_rt_counts.get(cand, 0) + 1
                                if cfg._fm_rds_rt_counts[cand] >= 2:
                                    cfg._fm_rds_rt = cand
                                    cfg._fm_rds_status = f"Locked ({cfg._fm_rds_valid_groups} valid)"

                    i += 104
                else:
                    i += 1

            if valid_this_call == 0:
                age = (now - float(cfg._fm_rds_last_good)) if getattr(cfg, "_fm_rds_last_good", 0) else None
                if cfg._fm_rds_metric < 0.002 and cfg._fm_rds_bp_rms < 0.002:
                    cfg._fm_rds_status = "No lock"
                elif age is not None and age < 10:
                    cfg._fm_rds_status = "Carrier seen, no valid groups"
                elif cfg._fm_rds_metric >= 0.002 or cfg._fm_rds_bp_rms >= 0.002:
                    cfg._fm_rds_status = "Carrier seen"
                else:
                    cfg._fm_rds_status = "No lock"

            return rds_bits

        except Exception:
            try:
                cfg._fm_rds_status = "No lock"
                cfg._fm_rds_metric = 0.0
                cfg._fm_rds_best_phase = -1
            except Exception:
                pass
            return rds_bits
    def _run_fm_rtlsdr(self, cfg, sender, stop_evt,
                        freq_hz: int, device_idx: int, ppm: int,
                        gain: float | None = None):
        """
        FM demodulation via rtl_fm subprocess with redsea RDS decode.

        Audio path:
            rtl_fm (171 kHz MPX S16LE) -> Python resample -> 48 kHz monitor audio

        RDS path:
            rtl_fm (same MPX) -> redsea -r 171000 -> newline-delimited JSON
        """
        import subprocess, threading, json
        name = cfg.name
        cfg._fm_backend = "rtl_fm"

        if not _find_binary("rtl_fm"):
            self.log(f"[{name}] FM: rtl_fm not found. Install rtl-sdr.")
            cfg._livewire_mode = "FM (no backend)"
            return

        redsea_bin = _find_binary("redsea")
        if not redsea_bin:
            self.log(f"[{name}] FM: redsea not found. Install redsea for RDS decoding.")

        rtl_cmd = [
            "rtl_fm",
            "-f", str(freq_hz),
            "-M", "fm",
            "-l", "0",
            "-A", "std",
            "-s", "171000",
            "-F", "9",
            "-d", str(device_idx),
            "-"
        ]
        if ppm:
            rtl_cmd += ["-p", str(ppm)]
        rtl_cmd += ["-g", str(gain if gain is not None else 38.0)]

        self.log(f"[{name}] FM rtl_fm: {' '.join(rtl_cmd)}")

        try:
            proc = subprocess.Popen(
                rtl_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
        except Exception as e:
            self.log(f"[{name}] FM: failed to launch rtl_fm: {e}")
            return

        redsea_proc = None
        redsea_thread = None

        def _apply_redsea_json(obj):
            try:
                from collections import Counter

                ps = str(obj.get("ps") or obj.get("partial_ps") or "").strip()
                rt = str(obj.get("radiotext") or obj.get("partial_radiotext") or "").strip()
                pi = obj.get("pi") or ""
                group = obj.get("group") or ""

                # ---- Stabilise PS (prefer longer full names, ignore short partials) ----
                if not hasattr(cfg, "_fm_ps_hist"):
                    cfg._fm_ps_hist = []
                if ps:
                    current_ps = str(getattr(cfg, "_fm_rds_ps", "") or "").strip()
                    if not (current_ps and len(ps) < len(current_ps)):
                        cfg._fm_ps_hist.append(ps)
                        cfg._fm_ps_hist = cfg._fm_ps_hist[-12:]
                        stable_ps, count = Counter(cfg._fm_ps_hist).most_common(1)[0]
                        if count >= 3 and len(stable_ps) >= max(6, len(current_ps)):
                            cfg._fm_rds_ps = stable_ps

                # ---- Stabilise RadioText ----
                if not hasattr(cfg, "_fm_rt_hist"):
                    cfg._fm_rt_hist = []
                if rt:
                    current_rt = str(getattr(cfg, "_fm_rds_rt", "") or "").strip()
                    if not (current_rt and len(rt) < max(8, len(current_rt)//2)):
                        cfg._fm_rt_hist.append(rt)
                        cfg._fm_rt_hist = cfg._fm_rt_hist[-10:]
                        stable_rt, count = Counter(cfg._fm_rt_hist).most_common(1)[0]
                        if count >= 2 or len(stable_rt) >= 12:
                            cfg._fm_rds_rt = stable_rt
                if pi:
                    cfg._fm_rds_pi = str(pi).strip()
                if group:
                    cfg._fm_rds_group = str(group).strip()

                cfg._fm_rds_status = "RDS decoded"
                cfg._fm_rds_ok = True
                cfg._fm_rds_metric = 1.0
                cfg._fm_rds_best_phase = 0
                cfg._fm_rds_valid_groups = int(getattr(cfg, "_fm_rds_valid_groups", 0) or 0) + 1
                cfg._fm_rds_last_good = time.time()
            except Exception:
                pass

        def _redsea_reader():
            try:
                for raw in iter(redsea_proc.stdout.readline, b""):
                    if stop_evt.is_set():
                        break
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        _apply_redsea_json(obj)
                    except Exception:
                        # Keep stderr/json oddities visible but compact.
                        try:
                            self.log(f"[{name}] redsea: {line[:200]}")
                        except Exception:
                            pass
            except Exception as e:
                try:
                    self.log(f"[{name}] redsea reader stopped: {e}")
                except Exception:
                    pass

        if redsea_bin:
            try:
                redsea_cmd = [redsea_bin, "-r", "171000", "-o", "json", "-p"]
                redsea_proc = subprocess.Popen(
                    redsea_cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=0,
                )
                redsea_thread = threading.Thread(target=_redsea_reader, daemon=True)
                redsea_thread.start()
                cfg._fm_rds_status = "redsea started"
                self.log(f"[{name}] redsea: {' '.join(redsea_cmd)}")
            except Exception as e:
                self.log(f"[{name}] redsea launch failed: {e}")
                redsea_proc = None
                cfg._fm_rds_status = "redsea launch failed"
                cfg._fm_rds_ok = False
        else:
            cfg._fm_rds_status = "redsea not installed"
            cfg._fm_rds_ok = False

        IN_RATE = 171000
        OUT_RATE = SAMPLE_RATE
        READ_BYTES = 4096
        BLOCK_BYTES = 17100 * 2  # ~100 ms of S16LE MPX
        mux_buf = bytearray()
        out_buf = np.empty(0, dtype=np.float32)
        cfg._fm_signal_dbm = float(getattr(cfg, "_fm_signal_dbm", -120.0) or -120.0)
        cfg._fm_snr_db = float(getattr(cfg, "_fm_snr_db", 0.0) or 0.0)
        cfg._fm_stereo = bool(getattr(cfg, "_fm_stereo", False))
        cfg._fm_rds_ok = bool(getattr(cfg, "_fm_rds_ok", False))

        try:
            from scipy.signal import resample_poly
            _have_scipy_resampler = True
        except Exception:
            _have_scipy_resampler = False

        def _mpx_to_audio(samples_f32: np.ndarray) -> np.ndarray:
            x = np.asarray(samples_f32, dtype=np.float32)
            if x.size == 0:
                return x
            if _have_scipy_resampler:
                # 171000 -> 48000 exactly
                y = resample_poly(x, 16, 57).astype(np.float32, copy=False)
            else:
                n_out = max(1, int(round(x.size * OUT_RATE / float(IN_RATE))))
                src = np.arange(x.size, dtype=np.float32)
                dst = np.linspace(0, max(0, x.size - 1), n_out, dtype=np.float32)
                y = np.interp(dst, src, x).astype(np.float32, copy=False)

            if y.size:
                y = y - np.mean(y)
                peak = float(np.max(np.abs(y)) + 1e-9)
                if peak > 0.95:
                    y = y * (0.95 / peak)
                y = np.clip(y, -1.0, 1.0).astype(np.float32, copy=False)
            return y

        def _update_mpx_metrics(samples_f32: np.ndarray):
            try:
                x = np.asarray(samples_f32, dtype=np.float32)
                if x.size < 2048:
                    return
                rms = float(np.sqrt(np.mean(np.square(x))) + 1e-12)
                cfg._fm_signal_dbm = float(20.0 * np.log10(rms + 1e-12))

                # Pilot estimate from the 19 kHz tone in the MPX stream.
                win = np.hanning(x.size).astype(np.float32)
                X = np.fft.rfft(x * win)
                freqs = np.fft.rfftfreq(x.size, d=1.0 / IN_RATE)
                mag = np.abs(X)
                pilot_band = (freqs >= 18850.0) & (freqs <= 19150.0)
                noise_band = ((freqs >= 17000.0) & (freqs <= 18000.0)) | ((freqs >= 20000.0) & (freqs <= 21000.0))
                pilot = float(np.max(mag[pilot_band])) if np.any(pilot_band) else 0.0
                noise = float(np.median(mag[noise_band])) if np.any(noise_band) else 1e-9
                pilot_db = 20.0 * np.log10((pilot + 1e-12) / (noise + 1e-12))
                cfg._fm_snr_db = float(max(0.0, pilot_db))
                cfg._fm_stereo = bool(pilot_db >= 8.0)
            except Exception:
                pass

        try:
            while not stop_evt.is_set():
                chunk = proc.stdout.read(READ_BYTES)
                if not chunk:
                    time.sleep(0.05)
                    continue

                if redsea_proc and redsea_proc.stdin:
                    try:
                        redsea_proc.stdin.write(chunk)
                        redsea_proc.stdin.flush()
                    except Exception:
                        redsea_proc = None
                        cfg._fm_rds_status = "redsea stream failed"
                        cfg._fm_rds_ok = False

                mux_buf.extend(chunk)

                while len(mux_buf) >= BLOCK_BYTES:
                    raw = bytes(mux_buf[:BLOCK_BYTES])
                    del mux_buf[:BLOCK_BYTES]

                    samp = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
                    _update_mpx_metrics(samp)
                    audio = _mpx_to_audio(samp)
                    if not audio.size:
                        continue

                    if out_buf.size:
                        out_buf = np.concatenate((out_buf, audio))
                    else:
                        out_buf = audio

                    while out_buf.size >= CHUNK_SIZE:
                        frame = out_buf[:CHUNK_SIZE].copy()
                        out_buf = out_buf[CHUNK_SIZE:]
                        cfg._stream_buffer.append(frame.copy())
                        cfg._audio_buffer.append(frame.copy())
                        cfg._live_chunk_seq = getattr(cfg, "_live_chunk_seq", 0) + 1
                        analyse_chunk(
                            cfg, sender,
                            lambda m, n=name: self.log(f"[{n}] {m}"),
                            frame, CHUNK_DURATION,
                            self.app_cfg.alert_wav_duration,
                            self.app_cfg.inputs,
                        )
        except Exception as e:
            self.log(f"[{name}] FM read error: {e}")
        finally:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

            try:
                if redsea_proc and redsea_proc.stdin:
                    redsea_proc.stdin.close()
            except Exception:
                pass
            try:
                if redsea_proc:
                    redsea_proc.terminate()
            except Exception:
                pass
            try:
                if redsea_proc:
                    redsea_proc.wait(timeout=2)
            except Exception:
                try:
                    redsea_proc.kill()
                except Exception:
                    pass

        self.log(f"[{name}] FM rtl_fm thread stopped.")
    def _run_http(self, cfg, sender, stop_evt):
        """Receive an HTTP/HTTPS audio stream (MP3, AAC, OGG, etc) via ffmpeg → PCM."""
        import subprocess, shutil
        name = cfg.name
        url  = cfg.device_index.strip()
        cfg._livewire_mode = f"HTTP: {url}"

        if not _find_binary("ffmpeg"):
            self.log(f"[{name}] ffmpeg not found on PATH — install ffmpeg to use HTTP streams.")
            cfg._livewire_mode = "HTTP (ffmpeg missing)"
            return

        # ffmpeg: read URL → raw signed-16 LE PCM, mono, 48 kHz on stdout
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-reconnect", "1", "-reconnect_streamed", "1",
            "-reconnect_delay_max", "10",
            "-i", url,
            "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "pipe:1",
        ]

        CHUNK_BYTES = int(SAMPLE_RATE * CHUNK_DURATION) * 2   # s16le = 2 bytes/sample

        while not stop_evt.is_set():
            proc = None
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        bufsize=CHUNK_BYTES * 4)
                self.log(f"[{name}] ffmpeg connected → {url}")
                buf = bytearray()

                while not stop_evt.is_set():
                    chunk = proc.stdout.read(4096)
                    if not chunk:
                        time.sleep(0.05); continue
                    buf.extend(chunk)
                    while len(buf) >= CHUNK_BYTES:
                        raw = bytes(buf[:CHUNK_BYTES])
                        del buf[:CHUNK_BYTES]
                        samp = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
                        samp = np.clip(samp - np.mean(samp), -1.0, 1.0)
                        cfg._stream_buffer.append(samp.copy())
                        cfg._audio_buffer.append(samp.copy())
                        cfg._live_chunk_seq = getattr(cfg, '_live_chunk_seq', 0) + 1
                        analyse_chunk(cfg, sender,
                                      lambda m, n=name: self.log(f"[{n}] {m}"),
                                      samp, CHUNK_DURATION,
                                      self.app_cfg.alert_wav_duration,
                                      self.app_cfg.inputs)

            except Exception as e:
                self.log(f"[{name}] HTTP stream error: {e}")
            finally:
                if proc:
                    try: proc.kill()
                    except: pass
                    proc.wait()

            if not stop_evt.is_set():
                self.log(f"[{name}] Disconnected — reconnecting in 5s…")
                stop_evt.wait(5)

        self.log(f"[{name}] HTTP stream thread stopped.")

    def _open_udp_socket_for_cfg(self, cfg):
        name = cfg.name
        try:
            mc_ip, port = _parse_device(cfg.device_index)
        except Exception as e:
            self.log(f"[{name}] Bad device_index: {e}")
            return None
        iface = self.app_cfg.network.audio_interface_ip or "0.0.0.0"
        is_multicast = mc_ip.startswith(("224.", "225.", "226.", "239."))
        self.log(f"[{name}] {'Joining multicast' if is_multicast else 'Unicast UDP bind'} {mc_ip}:{port}")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 256 * 1024 * 1024)
            except Exception:
                pass
            if is_multicast:
                sock.bind(("", port))
                mreq = struct.pack("4s4s", socket.inet_aton(mc_ip), socket.inet_aton(iface))
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                if iface != "0.0.0.0":
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(iface))
            else:
                bind_ip = mc_ip if iface == "0.0.0.0" else iface
                sock.bind((bind_ip, port))
            sock.setblocking(False)
            try:
                actual = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
                self.log(f"[{name}] UDP receive buffer {actual}")
            except Exception:
                pass
            return sock
        except Exception as e:
            self.log(f"[{name}] Socket failed: {e}")
            return None

    def _handle_udp_packet(self, st, packet, sender):
        cfg = st['cfg']
        name = cfg.name
        if len(packet) <= 12:
            return

        # Keep a short RTP sequence reorder buffer so packets are processed in
        # sequence order rather than raw arrival order. This is important on
        # Linux/VMware where small bursts or mild reordering are common.
        reorder = st.setdefault('reorder', {})
        max_reorder = int(st.get('max_reorder', 16))
        rtp_seq = struct.unpack_from(">H", packet, 2)[0]
        if st.get('expected_seq') is None:
            st['expected_seq'] = rtp_seq

        # recvfrom_into reuses the same backing buffer, so persist packet bytes.
        if rtp_seq not in reorder:
            reorder[rtp_seq] = bytes(packet)

        def _process_ordered(pkt_bytes, seqno):
            if cfg._rtp_last_seq >= 0:
                expected = (cfg._rtp_last_seq + 1) & 0xFFFF
                if seqno != expected:
                    gap = (seqno - expected) & 0xFFFF
                    if 0 < gap < 1000:
                        cfg._rtp_lost += gap
                        cfg._rtp_loss_window += gap
                cfg._rtp_total += 1
                cfg._rtp_total_window += 1
                if cfg._rtp_total_window >= 1000:
                    cfg._rtp_loss_pct = 100.0 * cfg._rtp_loss_window / cfg._rtp_total_window
                    cfg._rtp_loss_window = 0
                    cfg._rtp_total_window = 0
                    if cfg._rtp_loss_pct >= RTP_LOSS_ALERT_PCT:
                        key = "RTP_LOSS_ALERT"
                        if time.time() - cfg._last_alerts.get(key,0) >= ALERT_COOLDOWN:
                            cfg._last_alerts[key] = time.time()
                            msg = f"RTP packet loss {cfg._rtp_loss_pct:.1f}% on '{cfg.name}'"
                            clip = _save_alert_wav(cfg, "rtp_loss")
                            _add_history(cfg, "RTP_LOSS", msg, clip_path=clip or "")
                            sender.send(f"RTP Loss — {cfg.name}", msg, clip,
                                alert_type="RTP_LOSS", stream=cfg.name,
                                level_dbfs=float(cfg._last_level_dbfs))
                    elif cfg._rtp_loss_pct >= RTP_LOSS_WARN_PCT:
                        key = "RTP_LOSS_WARN"
                        if time.time() - cfg._last_alerts.get(key,0) >= ALERT_COOLDOWN*2:
                            cfg._last_alerts[key] = time.time()
                            msg = f"RTP packet loss {cfg._rtp_loss_pct:.1f}% on '{cfg.name}'"
                            clip = _save_alert_wav(cfg, "rtp_loss_warn")
                            _add_history(cfg, "RTP_LOSS_WARN", msg, clip_path=clip or "")
            cfg._rtp_last_seq = seqno
            rtp_cc = pkt_bytes[0] & 0x0F
            rtp_ext = (pkt_bytes[0] >> 4) & 0x01
            rtp_pt = pkt_bytes[1] & 0x7F
            hdr = 12 + rtp_cc * 4
            if rtp_ext and len(pkt_bytes) >= hdr + 4:
                hdr += 4 + (((pkt_bytes[hdr+2] << 8) | pkt_bytes[hdr+3]) * 4)
            if len(pkt_bytes) <= hdr:
                return
            payload = pkt_bytes[hdr:]
            if not st['detected']:
                fmt, bps, ch, mode = _detect_fmt(len(payload), rtp_pt)
                st['fmt'], st['bps'], st['ch'] = fmt, bps, ch
                st['needed'] = int(SAMPLE_RATE * CHUNK_DURATION) * ch * bps
                st['detected'] = True
                cfg._livewire_mode = mode
                self.log(f"[{name}] {mode} (PT={rtp_pt}, {len(payload)}B)")
            st['buf'].extend(payload)
            while len(st['buf']) >= st['needed']:
                samp = _decode(bytes(st['buf'][:st['needed']]), st['fmt'], st['ch'])
                del st['buf'][:st['needed']]
                if samp is None:
                    continue
                samp = np.clip(samp - np.mean(samp), -1.0, 1.0)
                cfg._stream_buffer.append(samp.copy())
                cfg._audio_buffer.append(samp.copy())
                cfg._live_chunk_seq = getattr(cfg, '_live_chunk_seq', 0) + 1
                analyse_chunk(cfg, sender, lambda m, n=name: self.log(f"[{n}] {m}"),
                              samp, CHUNK_DURATION, self.app_cfg.alert_wav_duration, self.app_cfg.inputs)

        # Drain contiguous packets in sequence order.
        while st['expected_seq'] in reorder:
            seqno = st['expected_seq']
            pkt = reorder.pop(seqno)
            _process_ordered(pkt, seqno)
            st['expected_seq'] = (st['expected_seq'] + 1) & 0xFFFF

        # If the reorder window fills, advance until we reach the earliest
        # packet we do have. Count the skipped sequence numbers as lost.
        if len(reorder) >= max_reorder:
            keys = list(reorder.keys())
            best_seq = min(keys, key=lambda s: ((s - st['expected_seq']) & 0xFFFF))
            gap = (best_seq - st['expected_seq']) & 0xFFFF
            if 0 < gap < 1000:
                cfg._rtp_lost += gap
                cfg._rtp_loss_window += gap
            st['expected_seq'] = best_seq
            while st['expected_seq'] in reorder:
                seqno = st['expected_seq']
                pkt = reorder.pop(seqno)
                _process_ordered(pkt, seqno)
                st['expected_seq'] = (st['expected_seq'] + 1) & 0xFFFF

    def _run_udp_inputs(self, cfgs, sender, stop_evt):
        selector = selectors.DefaultSelector()
        active = 0
        recvbuf = bytearray(4096)
        iface = self.app_cfg.network.audio_interface_ip or "0.0.0.0"

        # State for each logical stream stays separate; only the socket is shared.
        def _mk_state(cfg, sock):
            return {
                'cfg': cfg,
                'sock': sock,
                'fmt': None,
                'bps': 3,
                'ch': 2,
                'needed': 0,
                'buf': bytearray(),
                'detected': False,
                'reorder': {},
                'expected_seq': None,
                'max_reorder': 16,
            }

        def _is_multicast(ip):
            return ip.startswith(("224.", "225.", "226.", "227.", "228.", "229.", "230.", "231.", "232.", "233.", "234.", "235.", "236.", "237.", "238.", "239."))

        # Build one shared socket per UDP port for multicast groups; keep unicast one-socket-per-input.
        shared_by_port = {}
        states = {}

        for cfg in cfgs:
            try:
                mc_ip, port = _parse_device(cfg.device_index)
            except Exception as e:
                self.log(f"[{cfg.name}] Bad device_index: {e}")
                continue

            if _is_multicast(mc_ip):
                entry = shared_by_port.get(port)
                if entry is None:
                    try:
                        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        try:
                            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 256 * 1024 * 1024)
                        except Exception:
                            pass
                        # Required to learn which multicast group this packet was sent to.
                        try:
                            sock.setsockopt(socket.IPPROTO_IP, socket.IP_PKTINFO, 1)
                        except Exception:
                            pass
                        sock.bind(("", port))
                        sock.setblocking(False)
                        entry = {'sock': sock, 'port': port, 'by_group': {}}
                        shared_by_port[port] = entry
                        selector.register(sock, selectors.EVENT_READ, ('shared-mcast', entry))
                        active += 1
                        try:
                            actual = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
                            self.log(f"[RTP:{port}] Shared multicast socket receive buffer {actual}")
                        except Exception:
                            pass
                    except Exception as e:
                        self.log(f"[{cfg.name}] Shared multicast socket failed: {e}")
                        continue

                try:
                    mreq = struct.pack("4s4s", socket.inet_aton(mc_ip), socket.inet_aton(iface))
                    entry['sock'].setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                except OSError as e:
                    # Ignore duplicate joins for same group on restart edge cases.
                    self.log(f"[{cfg.name}] multicast join {mc_ip}:{port} → {e}")
                st = _mk_state(cfg, entry['sock'])
                entry['by_group'][mc_ip] = st
                self.log(f"[{cfg.name}] Shared multicast join {mc_ip}:{port}")
            else:
                sock = self._open_udp_socket_for_cfg(cfg)
                if not sock:
                    continue
                st = _mk_state(cfg, sock)
                states[sock] = st
                selector.register(sock, selectors.EVENT_READ, ('unicast', st))
                self.log(f"[{cfg.name}] Listening — detecting format…")
                active += 1

        if not active and not shared_by_port:
            self.log('[RTP] No UDP inputs could be opened.')
            return

        mcast_streams = sum(len(e['by_group']) for e in shared_by_port.values())
        unicast_streams = len(states)
        self.log(f"[RTP] Central receiver active for {mcast_streams + unicast_streams} UDP input(s) across {len(shared_by_port)} shared multicast port(s).")

        while not stop_evt.is_set():
            try:
                events = selector.select(timeout=1.0)
            except Exception as e:
                self.log(f"[RTP] select error: {e}")
                time.sleep(0.2)
                continue

            for key, _ in events:
                kind, data = key.data
                sock = key.fileobj
                drained = 0

                if kind == 'shared-mcast':
                    entry = data
                    while drained < 128 and not stop_evt.is_set():
                        try:
                            nbytes, ancdata, _flags, _addr = sock.recvmsg_into([recvbuf], 256)
                        except BlockingIOError:
                            break
                        except Exception as e:
                            self.log(f"[RTP:{entry['port']}] recv: {e}")
                            break
                        if nbytes <= 0:
                            break

                        dst_ip = None
                        for level, ctype, cdata in ancdata:
                            if level == socket.IPPROTO_IP and ctype == getattr(socket, 'IP_PKTINFO', -1) and len(cdata) >= 12:
                                try:
                                    _ifindex, _spec, addr = struct.unpack('I4s4s', cdata[:12])
                                    dst_ip = socket.inet_ntoa(addr)
                                except Exception:
                                    pass
                        if not dst_ip:
                            # Fallback: if only one group on this port, dispatch to it.
                            if len(entry['by_group']) == 1:
                                dst_ip = next(iter(entry['by_group']))
                        st = entry['by_group'].get(dst_ip)
                        if st is not None:
                            self._handle_udp_packet(st, memoryview(recvbuf)[:nbytes], sender)
                        drained += 1
                else:
                    st = data
                    while drained < 64 and not stop_evt.is_set():
                        try:
                            nbytes, _addr = sock.recvfrom_into(recvbuf)
                        except BlockingIOError:
                            break
                        except Exception as e:
                            self.log(f"[{st['cfg'].name}] recv: {e}")
                            break
                        if nbytes <= 0:
                            break
                        self._handle_udp_packet(st, memoryview(recvbuf)[:nbytes], sender)
                        drained += 1

        # Cleanup
        for sock, st in list(states.items()):
            try:
                selector.unregister(sock)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass
            self.log(f"[{st['cfg'].name}] Thread stopped.")

        for entry in list(shared_by_port.values()):
            try:
                selector.unregister(entry['sock'])
            except Exception:
                pass
            try:
                entry['sock'].close()
            except Exception:
                pass
            for st in entry['by_group'].values():
                self.log(f"[{st['cfg'].name}] Thread stopped.")

        try:
            selector.close()
        except Exception:
            pass

    def _run_input(self, cfg, sender, stop_evt):
        name=cfg.name
        # Check for DAB source
        if cfg.device_index.strip().lower().startswith("dab://"):
            self._run_dab(cfg, sender, stop_evt)
            return
        # Check for FM source
        if cfg.device_index.strip().lower().startswith("fm://"):
            self._run_fm(cfg, sender, stop_evt)
            return
        # Check for HTTP/HTTPS stream
        dev = cfg.device_index.strip()
        if dev.lower().startswith("http://") or dev.lower().startswith("https://"):
            self._run_http(cfg, sender, stop_evt)
            return
        try: mc_ip,port=_parse_device(cfg.device_index)
        except Exception as e: self.log(f"[{name}] Bad device_index: {e}"); return
        iface=self.app_cfg.network.audio_interface_ip or "0.0.0.0"
        # Detect unicast vs multicast
        is_multicast = mc_ip.startswith("224.") or mc_ip.startswith("225.") or \
                       mc_ip.startswith("226.") or mc_ip.startswith("239.")
        self.log(f"[{name}] {'Joining multicast' if is_multicast else 'Unicast UDP bind'} {mc_ip}:{port}")
        try:
            sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM,socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            if is_multicast:
                sock.bind(("",port))
                mreq=struct.pack("4s4s",socket.inet_aton(mc_ip),socket.inet_aton(iface))
                sock.setsockopt(socket.IPPROTO_IP,socket.IP_ADD_MEMBERSHIP,mreq)
                if iface!="0.0.0.0":
                    sock.setsockopt(socket.IPPROTO_IP,socket.IP_MULTICAST_IF,socket.inet_aton(iface))
            else:
                # Unicast — bind to the specific IP:port
                bind_ip = mc_ip if iface=="0.0.0.0" else iface
                sock.bind((bind_ip, port))
            sock.settimeout(1.0)
        except Exception as e: self.log(f"[{name}] Socket failed: {e}"); return

        fmt=None; bps=3; ch=2; needed=0; buf=bytearray(); detected=False
        self.log(f"[{name}] Listening — detecting format…")

        while not stop_evt.is_set():
            try: packet,_=sock.recvfrom(2048)
            except socket.timeout: continue
            except Exception as e: self.log(f"[{name}] recv: {e}"); time.sleep(0.5); continue
            if len(packet)<=12: continue
            rtp_seq = struct.unpack_from(">H", packet, 2)[0]
            # RTP packet loss tracking
            if cfg._rtp_last_seq >= 0:
                expected = (cfg._rtp_last_seq + 1) & 0xFFFF
                if rtp_seq != expected:
                    gap = (rtp_seq - expected) & 0xFFFF
                    if 0 < gap < 1000:   # ignore reorder / wrap artifacts
                        cfg._rtp_lost += gap
                        cfg._rtp_loss_window += gap
                cfg._rtp_total += 1
                cfg._rtp_total_window += 1
                if cfg._rtp_total_window >= 1000:
                    cfg._rtp_loss_pct = 100.0 * cfg._rtp_loss_window / cfg._rtp_total_window
                    cfg._rtp_loss_window = 0
                    cfg._rtp_total_window = 0
                    if cfg._rtp_loss_pct >= RTP_LOSS_ALERT_PCT:
                        key = "RTP_LOSS_ALERT"
                        if time.time() - cfg._last_alerts.get(key,0) >= ALERT_COOLDOWN:
                            cfg._last_alerts[key] = time.time()
                            msg = f"RTP packet loss {cfg._rtp_loss_pct:.1f}% on '{cfg.name}'"
                            clip = _save_alert_wav(cfg, "rtp_loss")
                            _add_history(cfg, "RTP_LOSS", msg, clip_path=clip or "")
                            sender.send(f"RTP Loss — {cfg.name}", msg, clip,
                                alert_type="RTP_LOSS", stream=cfg.name,
                                level_dbfs=float(cfg._last_level_dbfs))
                    elif cfg._rtp_loss_pct >= RTP_LOSS_WARN_PCT:
                        key = "RTP_LOSS_WARN"
                        if time.time() - cfg._last_alerts.get(key,0) >= ALERT_COOLDOWN*2:
                            cfg._last_alerts[key] = time.time()
                            msg = f"RTP packet loss {cfg._rtp_loss_pct:.1f}% on '{cfg.name}'"
                            clip = _save_alert_wav(cfg, "rtp_loss_warn")
                            _add_history(cfg, "RTP_LOSS_WARN", msg, clip_path=clip or "")
            cfg._rtp_last_seq = rtp_seq
            rtp_cc=packet[0]&0x0F; rtp_ext=(packet[0]>>4)&0x01; rtp_pt=packet[1]&0x7F
            hdr=12+rtp_cc*4
            if rtp_ext and len(packet)>=hdr+4:
                hdr+=4+(((packet[hdr+2]<<8)|packet[hdr+3])*4)
            if len(packet)<=hdr: continue
            payload=packet[hdr:]
            if not detected:
                fmt,bps,ch,mode=_detect_fmt(len(payload),rtp_pt)
                cfg._livewire_mode=mode; needed=int(SAMPLE_RATE*CHUNK_DURATION)*ch*bps
                detected=True; self.log(f"[{name}] {mode} (PT={rtp_pt}, {len(payload)}B)")
            buf.extend(payload)
            while len(buf)>=needed:
                samp=_decode(bytes(buf[:needed]),fmt,ch); del buf[:needed]
                if samp is None: continue
                samp=np.clip(samp-np.mean(samp),-1.0,1.0)
                cfg._stream_buffer.append(samp.copy()); cfg._audio_buffer.append(samp.copy())
                cfg._live_chunk_seq = getattr(cfg, '_live_chunk_seq', 0) + 1
                analyse_chunk(cfg,sender,lambda m,n=name:self.log(f"[{n}] {m}"),
                              samp,CHUNK_DURATION,self.app_cfg.alert_wav_duration,self.app_cfg.inputs)
        try: sock.close()
        except: pass
        self.log(f"[{name}] Thread stopped.")

    def _ai_loop(self, stop_evt):
        self.log("[AI] Loop started.")
        while not stop_evt.is_set():
            for cfg in list(self.app_cfg.inputs):
                if not cfg.enabled or not cfg.ai_monitor or cfg._audio_buffer is None: continue
                now=time.time()
                if now-cfg._ai_last_run<AI_ANALYSIS_INTERVAL: continue
                cfg._ai_last_run=now
                chunks=list(cfg._audio_buffer)
                if not chunks: continue
                audio=np.concatenate(chunks)
                target=int(SAMPLE_RATE*3.0)
                if len(audio)<int(SAMPLE_RATE*0.5): continue
                audio=audio[-target:] if len(audio)>target else audio
                ai=self._stream_ais.get(cfg.name)
                if ai:
                    try: ai.feed(audio)
                    except Exception as e: self.log(f"[AI:{cfg.name}] {e}")
            # Update stream comparators
            for comp in self._comparators:
                try: comp.update()
                except Exception as e: self.log(f"[CMP] Error: {e}")
            # Daily report check
            try: self._check_daily_report()
            except Exception as e: self.log(f"[REPORT] {e}")
            time.sleep(1.0)
        self.log("[AI] Loop stopped.")

    def _check_daily_report(self):
        if not self.app_cfg.email.enabled or not self.app_cfg.email.smtp_host: return
        now_str = time.strftime("%H:%M")
        today   = time.strftime("%Y-%m-%d")
        if now_str == self.app_cfg.daily_report_time and self._report_sent_date != today:
            self._report_sent_date = today
            self._send_daily_report(today)

    def _send_daily_report(self, date: str):
        lines = [f"SignalScope — Daily Report {date}", "="*50, ""]
        for inp in self.app_cfg.inputs:
            lines.append(f"Stream: {inp.name}")
            total = inp._rtp_total; lost = inp._rtp_lost
            pct = 100.0*lost/total if total>0 else 0.0
            lines.append(f"  RTP: {total:,} pkts  Lost: {lost:,}  Loss: {pct:.2f}%")
            lines.append(f"  AI: {inp._ai_status or 'N/A'}  Phase: {inp._ai_phase}")
            recent=[h for h in inp._history if h.get("type","") in
                    ("AI_ALERT","AI_WARN","SILENCE","CLIP","HISS","RTP_LOSS","CMP_ALERT")]
            if recent:
                lines.append(f"  Alerts ({len(recent)}):")
                for h in recent[-10:]:
                    lines.append(f"    [{h['timestamp']}] [{h['type']}] {h['message'][:80]}")
            else:
                lines.append("  No alerts")
            lines.append("")
        if self.ptp:
            lines.append("PTP Clock:")
            lines.append(f"  State: {self.ptp.state}  GM: {self.ptp.gm_id}")
            lines.append(f"  Offset: {self.ptp.offset_us/1000:.3f} ms  Jitter: {self.ptp.jitter_us/1000:.3f} ms")
            for h in self.ptp.history[-5:]:
                lines.append(f"  [{h['timestamp']}] {h['message'][:80]}")
            lines.append("")
        for comp in self._comparators:
            lines.append(f"Comparison '{comp.pre.name}' → '{comp.post.name}': {comp.status}")
        lines.append("")
        AlertSender(self.app_cfg, self.log).send(
            f"Daily Report — {date}", "\n".join(lines))
        self.log(f"[REPORT] Daily report sent for {date}")

# ─── Hub client — pushes heartbeat to central hub ─────────────────────────────

# ─── Hub security — HMAC signing, replay protection, rate limiting, encryption ─

import hmac as _hmac


def _derive_key(secret: str, purpose: str) -> bytes:
    """Derive a purpose-specific 256-bit key from the shared secret."""
    return hashlib.sha256(f"{secret}:{purpose}".encode()).digest()


def hub_sign_payload(secret: str, payload_bytes: bytes, ts: float) -> str:
    """HMAC-SHA256 over timestamp+payload. Secret never sent on wire."""
    key = _derive_key(secret, "signing")
    msg = f"{ts:.0f}:".encode() + payload_bytes
    return _hmac.new(key, msg, hashlib.sha256).hexdigest()


def hub_verify_signature(secret: str, payload_bytes: bytes,
                         sig: str, ts: float):
    """Verify HMAC signature and timestamp freshness. Returns (ok, reason)."""
    now = time.time()
    if abs(now - ts) > HUB_TIMESTAMP_TOLERANCE:
        return False, f"timestamp out of window ({abs(now-ts):.0f}s)"
    expected = hub_sign_payload(secret, payload_bytes, ts)
    if not _hmac.compare_digest(expected, sig):
        return False, "invalid signature"
    return True, ""


# ── Hub payload encryption ───────────────────────────────────────────────────
# Uses AES-256-GCM (AEAD) when the cryptography package is available.
# AES-GCM provides both confidentiality and integrity in one standard primitive,
# replacing the previous custom SHA-256 keystream XOR.
# Falls back to the keystream XOR if cryptography is not installed.
_HUB_CRYPTO_VERSION_AES = b"\x02"   # prefix byte: 0x02 = AES-GCM
_HUB_CRYPTO_VERSION_XOR = b"\x01"   # prefix byte: 0x01 = legacy XOR


def hub_encrypt_payload(secret: str, plaintext: bytes) -> bytes:
    """
    Encrypt payload using AES-256-GCM (preferred) or SHA-256 keystream XOR (fallback).
    Format: [1-byte version][12-byte nonce][ciphertext+16-byte GCM tag]  (AES-GCM)
         or [1-byte version][16-byte salt][ciphertext]                   (legacy XOR)
    """
    key = _derive_key(secret, "encryption")
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = os.urandom(12)
        ct    = AESGCM(key).encrypt(nonce, plaintext, None)
        return _HUB_CRYPTO_VERSION_AES + nonce + ct
    except ImportError:
        # Fall back to legacy XOR — still provides confidentiality, just not
        # with a standard AEAD primitive
        salt = os.urandom(16)
        return _HUB_CRYPTO_VERSION_XOR + salt + _keystream_xor(key, salt, plaintext)


def hub_decrypt_payload(secret: str, data: bytes) -> bytes:
    """Decrypt payload produced by hub_encrypt_payload. Handles both versions."""
    if len(data) < 2:
        raise ValueError("payload too short")
    version = data[:1]
    key = _derive_key(secret, "encryption")
    if version == _HUB_CRYPTO_VERSION_AES:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            if len(data) < 29:  # 1 + 12 + 16 minimum
                raise ValueError("AES-GCM payload too short")
            nonce = data[1:13]
            ct    = data[13:]
            return AESGCM(key).decrypt(nonce, ct, None)
        except ImportError:
            raise RuntimeError("cryptography package required to decrypt AES-GCM payload")
    elif version == _HUB_CRYPTO_VERSION_XOR:
        if len(data) < 18:
            raise ValueError("XOR payload too short")
        return _keystream_xor(key, data[1:17], data[17:])
    else:
        # Pre-versioned legacy format (no prefix byte) — treat whole thing as XOR
        if len(data) < 17:
            raise ValueError("payload too short")
        return _keystream_xor(key, data[:16], data[16:])


def _keystream_xor(key: bytes, iv: bytes, data: bytes) -> bytes:
    """Legacy XOR fallback. Kept for backward compatibility with older clients."""
    out = bytearray(len(data))
    offset = 0
    ctr = 0
    while offset < len(data):
        block = hashlib.sha256(key + iv + ctr.to_bytes(4, "big")).digest()
        for b in block:
            if offset >= len(data):
                break
            out[offset] = data[offset] ^ b
            offset += 1
        ctr += 1
    return bytes(out)


class HubRateLimiter:
    """Token-bucket rate limiter per client key.
    Keyed by site_name when available (reliable across NAT/proxies),
    falling back to IP. Allows HUB_RATE_LIMIT_RPM per minute per key.
    """
    def __init__(self):
        self._hits: Dict[str, list] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if now - t < 60.0]
            if len(hits) >= HUB_RATE_LIMIT_RPM:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True

    def cleanup(self):
        now = time.time()
        with self._lock:
            stale = [k for k, hits in self._hits.items()
                     if not any(now - t < 60.0 for t in hits)]
            for k in stale: del self._hits[k]


class HubNonceStore:
    """Tracks recently seen nonces to block exact replays within the time window."""
    def __init__(self):
        self._seen: Dict[str, float] = {}
        self._lock = threading.Lock()

    def check_and_store(self, nonce: str) -> bool:
        """Returns True if nonce is fresh (not a replay)."""
        now = time.time()
        with self._lock:
            cutoff = now - HUB_TIMESTAMP_TOLERANCE * 2
            self._seen = {n: t for n, t in self._seen.items() if t > cutoff}
            if nonce in self._seen:
                return False
            self._seen[nonce] = now
            return True


def _find_binary(name: str) -> str:
    """Find a system binary by name, searching system PATH even when running
    inside a virtualenv where the venv bin/ directory shadows the system PATH.
    Returns the full path or empty string if not found."""
    import shutil as _sh
    # First try the normal way
    found = _sh.which(name)
    if found:
        return found
    # Search standard system paths explicitly (bypasses venv PATH restriction)
    import os as _os
    for directory in ["/usr/bin", "/usr/local/bin", "/bin",
                      "/usr/sbin", "/snap/bin"]:
        candidate = _os.path.join(directory, name)
        if _os.path.isfile(candidate) and _os.access(candidate, _os.X_OK):
            return candidate
    return ""


hub_rate_limiter = HubRateLimiter()
hub_nonce_store  = HubNonceStore()

# Background cleanup
def _security_cleanup():
    while True:
        time.sleep(120)
        hub_rate_limiter.cleanup()
threading.Thread(target=_security_cleanup, daemon=True, name="SecCleanup").start()


class HubClient:
    """Runs in a background thread on each site, POSTing status to the hub."""

    # Connection state constants
    STATE_CONNECTING   = "connecting"
    STATE_CONNECTED    = "connected"
    STATE_DEGRADED     = "degraded"     # sending but getting errors
    STATE_DISCONNECTED = "disconnected"

    def __init__(self, cfg_fn, monitor_ref):
        self._cfg_fn    = cfg_fn
        self._monitor   = monitor_ref
        self._thread    = None
        self._stop      = threading.Event()
        # Observability
        self.state      = self.STATE_CONNECTING
        self.last_ack   = 0.0     # unix timestamp of last successful ACK
        self.last_error = ""
        self.sent_total = 0
        self.fail_total = 0
        # Resilience — outage queue
        self._queue: list = []    # payloads built during hub outage (max 60)
        self._backoff   = 0       # consecutive failures → backoff multiplier
        self._lock      = threading.Lock()

    @staticmethod
    def _normalise_url(url: str) -> str:
        """Ensure hub URL has a scheme. Defaults to https for public hosts, http for IPs."""
        url = url.strip()
        if not url:
            return url
        if "://" in url:
            return url
        # No scheme — guess based on whether it looks like a hostname or IP
        import re as _re
        is_ip = bool(_re.match(r'\d+\.\d+\.\d+\.\d+', url.split(":")[0]))
        scheme = "http" if is_ip else "https"
        return f"{scheme}://{url}"

    def start(self):
        self.state = self.STATE_CONNECTING
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="HubClient")
        self._thread.start()

    def stop(self):
        self._stop.set()
        self.state = self.STATE_DISCONNECTED

    def _build_payload(self) -> dict:
        cfg  = self._cfg_fn()
        mon  = self._monitor
        ptp  = mon.ptp
        streams = []
        for inp in cfg.inputs:
            sla_pct = None
            if inp._sla_monitored_s:
                total = inp._sla_monitored_s + inp._sla_alert_s
                sla_pct = round(inp._sla_monitored_s / max(total, 1) * 100, 3)
            streams.append({
                "name":              inp.name,
                "enabled":           inp.enabled,
                "device_index":      inp.device_index,
                "level_dbfs":        round(inp._last_level_dbfs, 1),
                "ai_status":         inp._ai_status,
                "ai_phase":          inp._ai_phase,
                "ai_monitor":        inp.ai_monitor,
                "ai_learn_start":    getattr(inp, "_ai_learn_start", 0),
                "rtp_loss_pct":      round(inp._rtp_loss_pct, 2),
                "rtp_total":         inp._rtp_total,
                "format":            inp._livewire_mode or "—",
                "sla_pct":           sla_pct,
                "alert_on_silence":  inp.alert_on_silence,
                "alert_on_hiss":     inp.alert_on_hiss,
                "alert_on_clip":     inp.alert_on_clip,
                "dab_snr":           round(inp._dab_snr, 1),
                "dab_sig":           round(inp._dab_sig, 1),
                "dab_ensemble":      inp._dab_ensemble,
                "dab_service":       inp._dab_service,
                "dab_ok":            inp._dab_ok,
                "fm_freq_mhz":       inp._fm_freq_mhz,
                "fm_signal_dbm":     round(inp._fm_signal_dbm, 1),
                "fm_snr_db":         round(inp._fm_snr_db, 1),
                "fm_stereo":         inp._fm_stereo,
                "fm_rds_ps":         inp._fm_rds_ps,
                "fm_rds_rt":         getattr(inp, "_fm_rds_rt", ""),
                "fm_rds_ok":         inp._fm_rds_ok,
                "fm_rds_status":     getattr(inp, "_fm_rds_status", "No lock"),
                "fm_rds_valid":      int(getattr(inp, "_fm_rds_valid_groups", 0)),
                "history":           list(inp._history)[-8:],
                "nowplaying_station_id": inp.nowplaying_station_id,
            })
        comparators = []
        for c in mon._comparators:
            comparators.append({
                "pre_name":     c.pre.name,
                "post_name":    c.post.name,
                "status":       c.status,
                "aligned":      c.aligned,
                "delay_ms":     round(c.delay_ms, 0),
                "correlation":  round(c.correlation, 3),
                "pre_dbfs":     round(c.pre_dbfs, 1),
                "post_dbfs":    round(c.post_dbfs, 1),
                "gain_diff_db": round(c.gain_diff_db, 1),
                "gain_alert_db": getattr(c.pre, "compare_gain_alert_db", 3.0),
                "cmp_history":  c.cmp_history[-6:],
            })
        # Recent alerts for hub reports page (last 50 — keep payload lean)
        _raw_alerts = _alert_log_load(50)
        recent_alerts = [{k: (v[:200] if isinstance(v,str) else v)
                          for k, v in e.items()} for e in _raw_alerts]
        # Clip counts per stream
        clips_dir = os.path.join(BASE_DIR, "alert_snippets")
        clip_counts = {}
        if os.path.exists(clips_dir):
            for stream_dir in os.listdir(clips_dir):
                dp = os.path.join(clips_dir, stream_dir)
                if os.path.isdir(dp):
                    clip_counts[stream_dir] = len([f for f in os.listdir(dp) if f.endswith(".wav")])
        for s in streams:
            s["clip_count"] = clip_counts.get(_safe_name(s["name"]), 0)
        # Client reports its own listen address so hub proxy routes work
        # regardless of what port the client is actually on.
        # Prefer the management IP from network config; fall back to hostname.
        mgmt_ip = cfg.network.management_interface_ip
        if not mgmt_ip or mgmt_ip == "0.0.0.0":
            try:
                # Best-effort: connect to hub and see which local IP is used
                hub_host = cfg.hub.hub_url.split("//")[-1].split(":")[0].split("/")[0]
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as _s:
                    _s.connect((hub_host, 80))
                    mgmt_ip = _s.getsockname()[0]
            except Exception:
                mgmt_ip = socket.gethostname()
        my_port = 5000  # client always listens on 5000 in client/both mode
        return {
            "api":         HUB_API_VERSION,
            "site":        cfg.hub.site_name or socket.gethostname(),
            "build":       BUILD,
            "ts":          time.time(),
            "running":     mon.is_running(),
            "learn_dur":   LEARN_DURATION_SECONDS,
            # self_url is always http — client sites are behind NAT/firewall
            # and never run TLS. Hub uses this to proxy audio back to the client.
            "self_url":    f"http://{mgmt_ip}:{my_port}",
            "streams":     streams,
            "comparators":  comparators,
            "recent_alerts": recent_alerts,
            "ptp": {
                "state":     ptp.state     if ptp else "idle",
                "status":    ptp.status    if ptp else "—",
                "offset_us": ptp.offset_us if ptp else 0,
                "drift_us":  ptp.drift_us  if ptp else 0,
                "jitter_us": ptp.jitter_us if ptp else 0,
                "gm_id":     ptp.gm_id     if ptp else "",
                "domain":    ptp.domain    if ptp else 0,
                "last_sync": ptp.last_sync if ptp else 0,
            } if mon.is_running() else {"state":"idle","status":"Not running","offset_us":0,"drift_us":0,"jitter_us":0,"gm_id":"","domain":0,"last_sync":0},
        }

    def _handle_listen_requests(self, cfg, listen_requests: list):
        """
        Called when the hub ACK contains listen_requests.
        Spawns a pusher thread for each new slot that isn't already being served.
        """
        with self._lock:
            active = getattr(self, "_active_slots", set())
            if not hasattr(self, "_active_slots"):
                self._active_slots = set()
            active = self._active_slots

        for req in listen_requests:
            slot_id    = req.get("slot_id","")
            stream_idx = req.get("stream_idx", 0)
            if not slot_id or slot_id in active:
                continue
            with self._lock:
                self._active_slots.add(slot_id)
            print(f"[HubRelay] Starting push for slot {slot_id} stream {stream_idx}")
            t = threading.Thread(
                target=self._push_audio,
                args=(cfg, slot_id, stream_idx),
                daemon=True,
                name=f"Relay-{slot_id[:6]}"
            )
            t.start()

    def _push_audio(self, cfg, slot_id: str, stream_idx: int):
        """
        Encode audio from the requested stream as MP3 and POST chunks to the hub.
        Runs until the hub stops accepting (slot expired or browser disconnected).
        """
        import subprocess, shutil
        hub_url  = HubClient._normalise_url(cfg.hub.hub_url).rstrip("/")
        chunk_url = f"{hub_url}/api/{HUB_API_VERSION}/audio_chunk/{slot_id}"
        inps = cfg.inputs
        if stream_idx < 0 or stream_idx >= len(inps):
            print(f"[HubRelay] Invalid stream index {stream_idx}")
            with self._lock: self._active_slots.discard(slot_id)
            return

        inp = inps[stream_idx]

        if not _find_binary("ffmpeg"):
            print(f"[HubRelay] ffmpeg not found, cannot relay")
            with self._lock: self._active_slots.discard(slot_id)
            return

        sr  = SAMPLE_RATE
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "s16le", "-ar", str(sr), "-ac", "1", "-i", "pipe:0",
            "-f", "mp3", "-b:a", "128k", "-reservoir", "0", "pipe:1",
        ]
        proc = subprocess.Popen(cmd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, bufsize=0)

        stop_event = threading.Event()

        def _pcm(c):
            return (np.clip(c, -1., 1.) * 32767).astype(np.int16).tobytes()

        def writer():
            try:
                # Seed 3s of backlog for instant playback
                if inp._stream_buffer:
                    seed_n = int(3.0 / CHUNK_DURATION)
                    for c in list(inp._stream_buffer)[-seed_n:]:
                        proc.stdin.write(_pcm(c))
                sent_seq = getattr(inp, "_live_chunk_seq", 0)
                while not stop_event.is_set():
                    cur = getattr(inp, "_live_chunk_seq", 0)
                    if cur > sent_seq:
                        n = min(cur - sent_seq, 50)
                        if inp._stream_buffer:
                            for c in list(inp._stream_buffer)[-n:]:
                                proc.stdin.write(_pcm(c))
                        sent_seq = cur
                    else:
                        time.sleep(0.02)
            except Exception:
                pass
            finally:
                try: proc.stdin.close()
                except: pass

        wt = threading.Thread(target=writer, daemon=True)
        wt.start()

        consecutive_fails = 0
        try:
            while not stop_event.is_set():
                data = proc.stdout.read(4096)
                if not data:
                    break
                try:
                    ts_c   = time.time()
                    nonce_c= hashlib.md5(os.urandom(8)).hexdigest()[:16]
                    sig_c  = hub_sign_payload(cfg.hub.secret_key, data, ts_c) if cfg.hub.secret_key else ""
                    req = urllib.request.Request(
                        chunk_url, data=data,
                        headers={
                            "Content-Type":  "application/octet-stream",
                            "X-Hub-Sig":     sig_c,
                            "X-Hub-Ts":      f"{ts_c:.0f}",
                            "X-Hub-Nonce":   nonce_c,
                        },
                        method="POST"
                    )
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        body = json.loads(resp.read())
                        if body.get("error") == "slot not found or expired":
                            print(f"[HubRelay] Slot {slot_id[:6]} expired, stopping")
                            break
                        consecutive_fails = 0
                except Exception as e:
                    consecutive_fails += 1
                    if consecutive_fails >= 5:
                        print(f"[HubRelay] Too many push failures for {slot_id[:6]}: {e}")
                        break
        finally:
            stop_event.set()
            try: proc.kill(); proc.wait(timeout=2)
            except: pass
            with self._lock:
                self._active_slots.discard(slot_id)
            print(f"[HubRelay] Push complete for slot {slot_id[:6]}")

    def _send_one(self, url: str, payload_bytes: bytes, secret: str = "") -> dict:
        """Sign, encrypt and send a payload. Returns full ACK body.
        If the URL is http:// and gets a redirect or connection error,
        automatically retries on https:// so clients survive a hub TLS upgrade.
        """
        # Auto-upgrade http → https on redirect or connection refused
        if url.startswith("http://"):
            try:
                return self._do_send(url, payload_bytes, secret)
            except Exception as e:
                err = str(e).lower()
                # Try https if http looks like it was refused or redirected
                if any(x in err for x in ("connection refused","remotedisconnected",
                                           "ssl","handshake","wrong version")):
                    https_url = "https://" + url[7:]
                    print(f"[HubClient] Retrying on HTTPS: {https_url[:60]}")
                    return self._do_send(https_url, payload_bytes, secret)
                raise
        return self._do_send(url, payload_bytes, secret)

    def _do_send(self, url: str, payload_bytes: bytes, secret: str = "") -> dict:
        """Internal: sign, encrypt and POST one payload."""
        ts    = time.time()
        nonce = hashlib.md5(os.urandom(8)).hexdigest()[:16]
        sig   = hub_sign_payload(secret, payload_bytes, ts) if secret else ""
        if secret:
            body_to_send = hub_encrypt_payload(secret, payload_bytes)
            content_type = "application/octet-stream"
        else:
            body_to_send = payload_bytes
            content_type = "application/json"
        req = urllib.request.Request(url, data=body_to_send,
            headers={
                "Content-Type":    content_type,
                "X-Hub-Sig":       sig,
                "X-Hub-Ts":        f"{ts:.0f}",
                "X-Hub-Nonce":     nonce,
                "X-Hub-Version":   HUB_API_VERSION,
            }, method="POST")
        try:
            resp_obj = urllib.request.urlopen(req, timeout=10)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise RateLimitError("Hub rate limited (429) — backing off")
            raise
        with resp_obj as resp:
            # ACK may also be encrypted if secret is set
            raw = resp.read()
            ct  = resp.headers.get("Content-Type","")
            if secret and "octet-stream" in ct:
                raw = hub_decrypt_payload(secret, raw)
            ack = json.loads(raw)
            if not ack.get("ok"):
                raise RuntimeError(f"Hub rejected: {ack}")
        return ack

    def _loop(self):
        MAX_QUEUE   = 60          # max queued payloads during outage (~5 min at 5s)
        BASE_WAIT   = HUB_HEARTBEAT_INTERVAL
        MAX_BACKOFF = 60.0        # cap backoff at 60s

        while not self._stop.is_set():
            cfg = self._cfg_fn()
            if not cfg.hub.hub_url:
                self._stop.wait(BASE_WAIT)
                continue

            url = self._normalise_url(cfg.hub.hub_url).rstrip("/") + f"/api/{HUB_API_VERSION}/heartbeat"

            # Build current payload and enqueue it
            try:
                payload_bytes = json.dumps(self._build_payload()).encode()
                kb = len(payload_bytes) / 1024
                if kb > 50:
                    print(f"[HubClient] Warning: large payload {kb:.1f}KB")
                with self._lock:
                    self._queue.append(payload_bytes)
                    if len(self._queue) > MAX_QUEUE:
                        self._queue.pop(0)  # drop oldest
            except Exception as e:
                print(f"[HubClient] Payload build failed: {e}\n" + __import__("traceback").format_exc())
                self._stop.wait(BASE_WAIT)
                continue

            # Attempt to flush the queue
            flushed = 0
            with self._lock:
                queue_snapshot = list(self._queue)

            success   = False
            last_body = {}
            for i, pb in enumerate(queue_snapshot):
                try:
                    last_body = self._send_one(url, pb, secret=cfg.hub.secret_key)
                    flushed += 1
                    success = True
                except RateLimitError as e:
                    # 429 — jump backoff to max immediately, drop queue
                    self.last_error = str(e)
                    if self.last_error != getattr(self, "_prev_err", ""):
                        print(f"[HubClient] {e} — waiting before retry")
                        self._prev_err = self.last_error
                    self._backoff = 10  # max backoff immediately
                    break
                except Exception as e:
                    self.last_error = str(e)
                    if self.last_error != getattr(self, "_prev_err", ""):
                        print(f"[HubClient] Send failed: {self.last_error}")
                        self._prev_err = self.last_error
                    break

            with self._lock:
                if flushed:
                    self._queue = self._queue[flushed:]

            # Update state and backoff
            if success:
                self.last_ack   = time.time()
                self.sent_total += flushed
                self._backoff    = 0
                self.last_error  = ""
                self._prev_err   = ""
                self.state       = self.STATE_CONNECTED
                # Handle reverse relay requests from hub (NAT traversal)
                listen_reqs = last_body.get("listen_requests", [])
                if listen_reqs:
                    self._handle_listen_requests(cfg, listen_reqs)
            else:
                self.fail_total += 1
                self._backoff    = min(self._backoff + 1, 10)
                age = time.time() - self.last_ack if self.last_ack else 999
                self.state = (self.STATE_DEGRADED if age < HUB_SITE_TIMEOUT
                              else self.STATE_DISCONNECTED)

            wait = min(BASE_WAIT * (1.5 ** self._backoff), MAX_BACKOFF) if self._backoff else BASE_WAIT
            self._stop.wait(wait)


# ─── Hub server — aggregates heartbeats from all clients ──────────────────────

class HubServer:
    """Runs on the central VPS. Receives heartbeats, serves hub dashboard."""

    def __init__(self):
        self._sites:  Dict[str, dict] = {}
        self._lock    = threading.Lock()
        self._secret: str = ""
        self._load_state()   # restore across restarts

    def set_secret(self, secret: str):
        self._secret = secret

    # ── Persistence ──────────────────────────────────────────────────────────
    def _load_state(self):
        """Load previously seen sites from disk so hub survives restarts."""
        try:
            if os.path.exists(HUB_STATE_PATH):
                with open(HUB_STATE_PATH, "r") as f:
                    saved = json.load(f)
                for name, data in saved.items():
                    # Mark as stale so they show as offline until next heartbeat
                    data["_received"] = data.get("_received", 0)
                    data.pop("_client_addr", None)   # IP may have changed
                    self._sites[name] = data
                print(f"[HubServer] Loaded {len(saved)} site(s) from state file")
        except Exception as e:
            print(f"[HubServer] Could not load state: {e}")

    def _save_snapshot(self, snapshot: dict):
        """Persist a snapshot of site data to disk. Called from ingest thread.
        Sites not seen in >7 days are pruned from the state file.
        """
        try:
            save = {}
            cutoff = time.time() - 7 * 86400
            for name, data in snapshot.items():
                if data.get("_received", 0) < cutoff:
                    continue  # prune stale site
                save[name] = {k: v for k, v in data.items()
                              if k not in ("_client_addr",)}
            with open(HUB_STATE_PATH, "w") as f:
                json.dump(save, f)
        except Exception as e:
            print(f"[HubServer] Could not save state: {e}")

    # ── Ingest ───────────────────────────────────────────────────────────────
    def ingest(self, payload: dict, client_ip: str = "") -> bool:
        """Store a heartbeat. Signature already verified by route handler."""
        site = payload.get("site", "unknown")
        now  = time.time()

        # Use self_url from payload if provided, otherwise fall back to IP:5000
        self_url = payload.get("self_url","").strip()
        if not self_url and client_ip:
            self_url = f"http://{client_ip}:5000"
        # Always force http for _client_addr — client sites do not run TLS
        if self_url.startswith("https://"):
            self_url = "http://" + self_url[8:]

        with self._lock:
            prev = self._sites.get(site, {})
            last_rx = prev.get("_received", 0)
            gap     = now - last_rx if last_rx else 0
            missed  = max(0, int(gap / HUB_HEARTBEAT_INTERVAL) - 1) if last_rx else 0
            consecutive_missed = prev.get("_consecutive_missed", 0)
            consecutive_missed = consecutive_missed + missed if missed else 0

            stored = {k: v for k, v in payload.items() if k != "secret"}
            stored.update({
                "_received":           now,
                "_client_addr":        self_url,
                "_consecutive_missed": consecutive_missed,
                "_total_missed":       prev.get("_total_missed", 0) + missed,
                "_total_received":     prev.get("_total_received", 0) + 1,
                "_first_seen":         prev.get("_first_seen", now),
            })
            self._sites[site] = stored
            # Snapshot outside lock scope for persistence
            snapshot = dict(self._sites)

        # Persist asynchronously — pass snapshot so no lock needed in thread
        threading.Thread(target=self._save_snapshot, args=(snapshot,),
                         daemon=True, name="HubSave").start()
        return True

    # ── Query ─────────────────────────────────────────────────────────────────
    def get_sites(self) -> List[dict]:
        now = time.time()
        with self._lock:
            result = []
            for name, data in self._sites.items():
                age    = now - data.get("_received", 0)
                online = age < HUB_SITE_TIMEOUT
                # Health score: % of expected heartbeats received
                total_rx  = data.get("_total_received", 0)
                total_miss= data.get("_total_missed", 0)
                health    = round(total_rx / max(total_rx + total_miss, 1) * 100, 1)
                result.append({**data, "online": online, "age_s": round(age, 1),
                                "health_pct": health,
                                "consecutive_missed": data.get("_consecutive_missed", 0)})
            return sorted(result, key=lambda x: x["site"])

    def get_site(self, name: str) -> Optional[dict]:
        with self._lock:
            return self._sites.get(name)


hub_server = HubServer()


# ─── Reverse audio relay — allows hub to request audio from NAT'd clients ─────

class ListenSlot:
    """
    Represents one browser listener waiting for audio from a remote client.
    The hub creates a slot when a browser hits /hub/site/.../live.
    The slot ID is sent to the client in the next heartbeat ACK.
    The client pushes MP3 chunks to /api/v1/audio_chunk/<slot_id>.
    The hub streams those chunks to the browser.
    """
    SLOT_TIMEOUT = 30.0   # drop slot if no chunks arrive within this many seconds

    def __init__(self, slot_id: str, site: str, stream_idx: int):
        self.slot_id    = slot_id
        self.site       = site
        self.stream_idx = stream_idx
        self.created    = time.time()
        self.last_chunk = time.time()
        self.q: queue.Queue = queue.Queue(maxsize=200)
        self.closed     = False

    def put(self, data: bytes):
        self.last_chunk = time.time()
        try:
            self.q.put_nowait(data)
        except queue.Full:
            # Drop oldest chunk to make room
            try: self.q.get_nowait()
            except queue.Empty: pass
            try: self.q.put_nowait(data)
            except queue.Full: pass

    def get(self, timeout=5.0):
        return self.q.get(timeout=timeout)

    @property
    def stale(self):
        return time.time() - self.last_chunk > self.SLOT_TIMEOUT


class ListenSlotRegistry:
    """Thread-safe registry of active listen slots."""

    def __init__(self):
        self._slots: Dict[str, ListenSlot] = {}
        self._lock  = threading.Lock()
        # Start background reaper
        threading.Thread(target=self._reap, daemon=True, name="SlotReaper").start()

    def create(self, site: str, stream_idx: int) -> ListenSlot:
        slot_id = hashlib.md5(f"{site}{stream_idx}{time.time()}".encode()).hexdigest()[:12]
        slot = ListenSlot(slot_id, site, stream_idx)
        with self._lock:
            self._slots[slot_id] = slot
        return slot

    def get(self, slot_id: str) -> Optional[ListenSlot]:
        with self._lock:
            return self._slots.get(slot_id)

    def remove(self, slot_id: str):
        with self._lock:
            self._slots.pop(slot_id, None)

    def pending_for_site(self, site: str) -> List[dict]:
        """Return list of {slot_id, stream_idx} for slots awaiting a client push."""
        with self._lock:
            return [
                {"slot_id": s.slot_id, "stream_idx": s.stream_idx}
                for s in self._slots.values()
                if s.site == site and not s.closed and not s.stale
            ]

    def _reap(self):
        while True:
            time.sleep(10)
            with self._lock:
                stale = [sid for sid, s in self._slots.items() if s.stale or s.closed]
                for sid in stale:
                    self._slots.pop(sid, None)
            if stale:
                print(f"[SlotReaper] Reaped {len(stale)} stale slot(s)")


listen_registry = ListenSlotRegistry()

# ─── Flask + templates ────────────────────────────────────────────────────────


# ─── ACME / Let's Encrypt client ──────────────────────────────────────────────
# Pure-Python ACME v2 using the cryptography package (already a Flask dep).
# Handles account registration, HTTP-01 challenge, cert issuance and renewal.

# cryptography is imported lazily inside AcmeClient — app starts even without it
def _require_cryptography():
    """Import cryptography package. Raises clear error if not installed."""
    try:
        from cryptography.hazmat.primitives.asymmetric import rsa, padding
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        return rsa, padding, hashes, serialization, x509, NameOID
    except ImportError:
        raise RuntimeError(
            "The 'cryptography' package is required for Let's Encrypt.\n"
            "Install it with:  pip install cryptography"
        )
ACME_DIRECTORY = "https://acme-v02.api.letsencrypt.org/directory"
ACME_STAGING   = "https://acme-staging-v02.api.letsencrypt.org/directory"
CERT_DIR       = os.path.join(BASE_DIR, "certs")
ACME_LOG: list = []          # in-memory log shown in the UI

# HTTP-01 challenge token store — { token: key_authorisation }
_acme_challenges: dict = {}


def _acme_log(msg: str):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    ACME_LOG.append(line)
    if len(ACME_LOG) > 200: ACME_LOG.pop(0)
    print(f"[ACME] {msg}")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _jwk_from_key(key) -> dict:
    rsa, padding, hashes, serialization, x509, NameOID = _require_cryptography()
    pub = key.public_key()
    nums = pub.public_numbers()
    def _int_b64(n, length=256):
        return _b64url(n.to_bytes(length, "big").lstrip(b"\x00") or b"\x00")
    return {
        "kty": "RSA",
        "n":   _int_b64(nums.n, (nums.n.bit_length() + 7) // 8),
        "e":   _int_b64(nums.e, (nums.e.bit_length() + 7) // 8),
    }


def _jwk_thumbprint(key) -> bytes:
    jwk = _jwk_from_key(key)
    canonical = json.dumps({"e": jwk["e"], "kty": jwk["kty"], "n": jwk["n"]},
                           separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(canonical).digest()


def _jws(key, url: str, payload, nonce: str, kid: str = "") -> dict:
    rsa, padding, hashes, serialization, x509, NameOID = _require_cryptography()
    header = {"alg": "RS256", "nonce": nonce, "url": url}
    if kid:
        header["kid"] = kid
    else:
        header["jwk"] = _jwk_from_key(key)
    hdr_b64 = _b64url(json.dumps(header, separators=(",",":")).encode())
    if payload is None:
        pay_b64 = ""
    else:
        pay_b64 = _b64url(json.dumps(payload, separators=(",",":")).encode())
    signing_input = f"{hdr_b64}.{pay_b64}".encode()
    sig = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return {"protected": hdr_b64, "payload": pay_b64, "signature": _b64url(sig)}


# Sentinel for ACME POST-as-GET requests (signed POST with empty payload)
_ACME_POST_AS_GET = object()

def _acme_request(url: str, body=None, *, nonce: str = "",
                  key=None, kid: str = "", get_nonce_url: str = "",
                  _retry: bool = True) -> tuple:
    """Make an ACME request. Returns (response_dict, headers, new_nonce).
    Auto-retries once on badNonce using the fresh nonce from the error response.
    """
    if body is _ACME_POST_AS_GET:
        # POST-as-GET: signed POST with empty (None) payload
        jws = _jws(key, url, None, nonce, kid)
        data = json.dumps(jws).encode()
        req = urllib.request.Request(url, data=data,
            headers={"Content-Type": "application/jose+json"}, method="POST")
    elif body is not None and key:
        jws = _jws(key, url, body, nonce, kid)
        data = json.dumps(jws).encode()
        headers = {"Content-Type": "application/jose+json"}
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    else:
        req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw      = resp.read()
            hdrs     = dict(resp.headers)
            new_nonce= hdrs.get("Replay-Nonce","")
            ct       = hdrs.get("Content-Type","")
            try:
                result = json.loads(raw) if raw else {}
            except Exception:
                result = {"_raw": raw.decode(errors="replace")}
            return result, hdrs, new_nonce
    except urllib.error.HTTPError as e:
        raw  = e.read()
        hdrs = dict(e.headers)
        new_nonce = hdrs.get("Replay-Nonce","")
        try:
            result = json.loads(raw)
        except Exception:
            result = {"_raw": raw.decode(errors="replace")}
        _acme_log(f"HTTP {e.code} from {e.url[:60]}: {result}")
        # Auto-retry on badNonce — LE provides a fresh nonce in the error headers
        if (_retry and body is not None
                and isinstance(result, dict)
                and result.get("type","").endswith("badNonce")
                and new_nonce):
            _acme_log(f"Retrying with fresh nonce…")
            return _acme_request(url, body, nonce=new_nonce, key=key,
                                 kid=kid, _retry=False)
        return result, hdrs, new_nonce


class AcmeClient:
    """
    Minimal ACME v2 client.  Handles:
    - Account key generation / registration
    - HTTP-01 challenge (challenge token served by Flask on port 80)
    - CSR generation
    - Certificate download and storage
    - Auto-renewal (checks daily, renews if <30 days remain)
    """

    def __init__(self, staging: bool = False):
        self.staging   = staging
        self.directory: dict = {}
        self.nonce:     str  = ""
        self.account_url: str = ""
        self._lock     = threading.Lock()
        self._status   = "idle"   # idle | running | ok | error
        self._thread: threading.Thread = None

    @property
    def status(self): return self._status

    def _ensure_cert_dir(self):
        os.makedirs(CERT_DIR, exist_ok=True)

    def _account_key_path(self):
        return os.path.join(CERT_DIR, "account.key")

    def _load_or_create_account_key(self):
        rsa, padding, hashes, serialization, x509, NameOID = _require_cryptography()
        path = self._account_key_path()
        if os.path.exists(path):
            with open(path, "rb") as f:
                return serialization.load_pem_private_key(f.read(), password=None)
        _acme_log("Generating 2048-bit RSA account key…")
        key = rsa.generate_private_key(65537, 2048)
        with open(path, "wb") as f:
            f.write(key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption()
            ))
        return key

    def _get_directory(self):
        url = ACME_STAGING if self.staging else ACME_DIRECTORY
        _acme_log(f"Fetching ACME directory from {url}")
        result, _, _ = _acme_request(url)
        self.directory = result
        _acme_log("Directory OK")

    def _get_nonce(self):
        url = self.directory.get("newNonce","")
        _, hdrs, nonce = _acme_request(url)
        self.nonce = nonce or hdrs.get("Replay-Nonce","")

    def _register_account(self, key, email: str):
        _acme_log("Registering / retrieving ACME account…")
        payload = {"termsOfServiceAgreed": True}
        if email:
            payload["contact"] = [f"mailto:{email}"]
        result, hdrs, nonce = _acme_request(
            self.directory["newAccount"], payload,
            nonce=self.nonce, key=key)
        self.nonce = nonce
        self.account_url = hdrs.get("Location","")
        status = result.get("status","")
        _acme_log(f"Account status: {status}  url: {self.account_url[:60]}")
        if status not in ("valid","created"):
            raise RuntimeError(f"Account registration failed: {result}")

    def _new_order(self, key, domain: str):
        _acme_log(f"Creating order for {domain}…")
        payload = {"identifiers": [{"type":"dns","value":domain}]}
        result, hdrs, nonce = _acme_request(
            self.directory["newOrder"], payload,
            nonce=self.nonce, key=key, kid=self.account_url)
        self.nonce = nonce
        order_url = hdrs.get("Location","")
        _acme_log(f"Order created: {order_url[-40:]}")
        return result, order_url

    def _get_http01_challenge(self, key, authz_url: str) -> tuple:
        result, _, nonce = _acme_request(authz_url, None, nonce=self.nonce,
                                          key=key, kid=self.account_url)
        self.nonce = nonce
        for ch in result.get("challenges",[]):
            if ch.get("type") == "http-01":
                token   = ch["token"]
                thumbpr = _b64url(_jwk_thumbprint(key))
                key_auth= f"{token}.{thumbpr}"
                return ch["url"], token, key_auth
        raise RuntimeError("No http-01 challenge found in authorisation")

    def _respond_to_challenge(self, key, ch_url: str):
        _acme_log("Responding to HTTP-01 challenge…")
        result, _, nonce = _acme_request(ch_url, {}, nonce=self.nonce,
                                          key=key, kid=self.account_url)
        self.nonce = nonce
        _acme_log(f"Challenge response: {result}")

    def _wait_for_valid(self, key, url: str, label: str, timeout: int = 120):
        deadline = time.time() + timeout
        while time.time() < deadline:
            # ACME v2 requires POST-as-GET for polling — signed POST with None payload
            result, _, nonce = _acme_request(url, _ACME_POST_AS_GET,
                                              nonce=self.nonce, key=key,
                                              kid=self.account_url)
            self.nonce = nonce
            status = result.get("status","")
            err    = result.get("error",{})
            # Log challenges detail when stuck pending
            for ch in result.get("challenges",[]):
                if ch.get("type") == "http-01":
                    ch_err = ch.get("error",{})
                    ch_st  = ch.get("status","")
                    if ch_err:
                        _acme_log(f"  http-01 challenge error: {ch_err}")
                    elif ch_st:
                        _acme_log(f"  http-01 challenge status: {ch_st}")
            if err:
                _acme_log(f"{label} status: {status}  error: {err}")
            else:
                _acme_log(f"{label} status: {status}")
            if status == "valid":
                return result
            if status in ("invalid","revoked","deactivated"):
                raise RuntimeError(f"{label} failed: {err or status}")
            time.sleep(5)
        raise RuntimeError(f"{label} timed out after {timeout}s")

    def _make_csr(self, domain: str) -> tuple:
        """Generate a domain private key and CSR. Returns (key_pem, csr_der)."""
        rsa, padding, hashes, serialization, x509, NameOID = _require_cryptography()
        _acme_log("Generating domain key and CSR…")
        dom_key = rsa.generate_private_key(65537, 2048)
        csr = (x509.CertificateSigningRequestBuilder()
               .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain)]))
               .add_extension(x509.SubjectAlternativeName([x509.DNSName(domain)]), critical=False)
               .sign(dom_key, hashes.SHA256()))
        key_pem = dom_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption())
        csr_der = csr.public_bytes(serialization.Encoding.DER)
        return key_pem, csr_der

    def _finalise_order(self, key, finalise_url: str, csr_der: bytes):
        _acme_log("Finalising order…")
        payload = {"csr": _b64url(csr_der)}
        result, _, nonce = _acme_request(finalise_url, payload,
                                          nonce=self.nonce, key=key,
                                          kid=self.account_url)
        self.nonce = nonce
        return result

    def _download_cert(self, key, cert_url: str) -> bytes:
        _acme_log("Downloading certificate…")
        # LE returns PEM directly — use raw HTTP to avoid JSON parsing
        import ssl as _ssl2
        ctx = _ssl2.create_default_context()
        jws = _jws(key, cert_url, None, self.nonce, self.account_url)
        data = json.dumps(jws).encode()
        req = urllib.request.Request(cert_url, data=data,
            headers={"Content-Type": "application/jose+json",
                     "Accept": "application/pem-certificate-chain"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                self.nonce = resp.headers.get("Replay-Nonce", self.nonce)
                raw = resp.read()
                _acme_log(f"Certificate download: {len(raw)} bytes")
                if b"BEGIN CERTIFICATE" in raw:
                    return raw
                raise RuntimeError(f"Unexpected cert response: {raw[:200]}")
        except urllib.error.HTTPError as e:
            body = e.read()
            self.nonce = e.headers.get("Replay-Nonce", self.nonce)
            raise RuntimeError(f"Cert download failed {e.code}: {body[:200]}")

    def issue(self, domain: str, email: str = "") -> bool:
        """Run full certificate issuance flow. Returns True on success."""
        with self._lock:
            if self._status == "running":
                _acme_log("Already running")
                return False
            self._status = "running"
        try:
            self._ensure_cert_dir()
            key = self._load_or_create_account_key()
            self._get_directory()
            self._get_nonce()
            self._register_account(key, email)
            order, order_url = self._new_order(key, domain)

            for authz_url in order.get("authorizations",[]):
                ch_url, token, key_auth = self._get_http01_challenge(key, authz_url)
                _acme_challenges[token] = key_auth
                _acme_log(f"Challenge token set: {token[:20]}…")
                _acme_log(f"Key auth value: {key_auth[:40]}…")
                # Self-test: verify the token is reachable before telling LE
                _test_url = f"http://{domain}/.well-known/acme-challenge/{token}"
                _acme_log(f"Self-test: GET {_test_url}")
                try:
                    import urllib.request as _ur
                    with _ur.urlopen(_ur.Request(_test_url), timeout=10) as _r:
                        _body = _r.read().decode().strip()
                        if _body == key_auth:
                            _acme_log("Self-test PASSED — challenge token reachable")
                        else:
                            _acme_log(f"Self-test MISMATCH — got: {_body[:60]}")
                            raise RuntimeError(f"Challenge self-test failed: response does not match key_auth")
                except Exception as _e:
                    _acme_log(f"Self-test FAILED: {_e}")
                    raise RuntimeError(f"Challenge not reachable at {_test_url}: {_e}\n"
                                       f"Ensure port 80 is open and {domain} resolves to this server.")
                try:
                    # Refresh nonce immediately before responding to avoid badNonce
                    self._get_nonce()
                    self._respond_to_challenge(key, ch_url)
                    time.sleep(3)  # brief pause before LE validates
                    self._wait_for_valid(key, authz_url, "Authorisation")
                finally:
                    _acme_challenges.pop(token, None)

            key_pem, csr_der = self._make_csr(domain)
            order = self._finalise_order(key, order["finalize"], csr_der)
            order = self._wait_for_valid(key, order_url, "Order")

            cert_url = order.get("certificate","")
            if not cert_url:
                raise RuntimeError("No certificate URL in completed order")

            # Try downloading via POST-as-GET
            cert_pem = self._download_cert(key, cert_url)

            # Save files
            cert_path = os.path.join(CERT_DIR, "fullchain.pem")
            key_path  = os.path.join(CERT_DIR, "privkey.pem")
            with open(cert_path, "wb") as f: f.write(cert_pem)
            with open(key_path,  "wb") as f: f.write(key_pem)
            os.chmod(key_path, 0o600)

            # Update config
            cfg = monitor.app_cfg
            cfg.tls_cert_path = cert_path
            cfg.tls_key_path  = key_path
            cfg.tls_domain    = domain
            cfg.tls_enabled   = True
            save_config(cfg)

            _acme_log(f"Certificate issued and saved. Restart the app to enable HTTPS.")
            self._status = "ok"
            return True

        except Exception as e:
            _acme_log(f"ERROR: {e}")
            import traceback as _tb
            _acme_log(_tb.format_exc().split("\n")[-2])
            self._status = "error"
            return False

    def clear_state(self):
        """Delete cached account key and cert to force completely fresh issuance."""
        self._ensure_cert_dir()
        for f in ["account.key"]:
            p = os.path.join(CERT_DIR, f)
            if os.path.exists(p):
                os.remove(p)
                _acme_log(f"Cleared {f}")
        self.account_url = ""
        self.nonce = ""
        _acme_log("State cleared — next issuance will create a fresh account and order")

    def start_issue_thread(self, domain: str, email: str = ""):
        self._thread = threading.Thread(
            target=self.issue, args=(domain, email),
            daemon=True, name="AcmeIssue")
        self._thread.start()

    def check_renewal(self):
        """Check if the cert expires within 30 days and renew if so."""
        cfg = monitor.app_cfg
        if not cfg.tls_enabled or not cfg.tls_cert_path:
            return
        if not os.path.exists(cfg.tls_cert_path):
            return
        try:
            _, _, _, _, x509, _ = _require_cryptography()
            with open(cfg.tls_cert_path, "rb") as f:
                cert = x509.load_pem_x509_certificate(f.read())
            expires = cert.not_valid_after_utc.replace(tzinfo=None)
            days_left = (expires - datetime.datetime.utcnow()).days
            _acme_log(f"Cert expires in {days_left} days ({expires.strftime('%Y-%m-%d')})")
            if days_left < 30:
                _acme_log("Cert expires soon — renewing…")
                self.start_issue_thread(cfg.tls_domain)
        except Exception as e:
            _acme_log(f"Renewal check failed: {e}")


acme_client = AcmeClient()


def _acme_renewal_loop():
    """Check cert renewal once per day."""
    while True:
        time.sleep(86400)
        acme_client.check_renewal()

threading.Thread(target=_acme_renewal_loop, daemon=True, name="AcmeRenewal").start()


# ─── Login brute-force protection ─────────────────────────────────────────────

class LoginLimiter:
    """Tracks failed login attempts per IP with lockout."""
    def __init__(self):
        self._attempts: Dict[str, list]  = {}  # ip → [timestamps]
        self._locked:   Dict[str, float] = {}  # ip → locked_until_ts
        self._lock = threading.Lock()

    def record_failure(self, ip: str, max_attempts: int, lockout_mins: int):
        now = time.time()
        with self._lock:
            hits = [t for t in self._attempts.get(ip,[]) if now - t < 3600]
            hits.append(now)
            self._attempts[ip] = hits
            if len(hits) >= max_attempts:
                self._locked[ip] = now + lockout_mins * 60
                _log_security(f"Login lockout: {ip} after {len(hits)} failures")

    def is_locked(self, ip: str) -> float:
        """Returns seconds remaining in lockout, or 0 if not locked."""
        now = time.time()
        with self._lock:
            until = self._locked.get(ip, 0)
            if until > now:
                return until - now
            if ip in self._locked:
                del self._locked[ip]
            return 0.0

    def clear(self, ip: str):
        with self._lock:
            self._attempts.pop(ip, None)
            self._locked.pop(ip, None)


login_limiter = LoginLimiter()


def _log_security(msg: str):
    print(f"[Security] {msg}", flush=True)




# ─── SDR Device Registry ──────────────────────────────────────────────────────



class SdrDeviceManager:
    """
    Manages exclusive access to RTL-SDR dongles by serial number.
    Serial numbers are stable across USB reconnects; device indices are not.

    Usage:
        with sdr_manager.claim(serial) as idx:
            # idx is the current device index for this serial
            subprocess.run(["rtl_fm", "-d", str(idx), ...])
        # device released automatically on exit

    If the dongle is already claimed, claim() raises SdrBusyError.
    If the serial is not found in connected hardware, raises SdrNotFoundError.
    """

    def __init__(self):
        self._lock    = threading.Lock()
        self._owners: Dict[str, str] = {}   # serial → owner label
        self._index_cache: Dict[str, int] = {}  # serial → last known index
        self._cache_ts: float = 0.0

    # ── Hardware scanning ──────────────────────────────────────────────────────

    def scan(self, force: bool = False) -> List[Dict]:
        """
        Scan for connected RTL-SDR dongles using rtl_test.
        Returns list of dicts: {index, serial, name, manufacturer}
        Results are cached for 10 seconds unless force=True.
        """
        import shutil, subprocess as _sp
        now = time.time()
        if not force and hasattr(self, '_scan_cache') and now - self._cache_ts < 10:
            return self._scan_cache

        devices = []
        if not _find_binary("rtl_test") and not _find_binary("rtl_eeprom"):
            self._scan_cache = devices
            self._cache_ts   = now
            return devices

        tool = "rtl_test" if _find_binary("rtl_test") else "rtl_eeprom"
        try:
            # rtl_test -t lists devices then exits quickly
            result = _sp.run([tool, "-t"] if tool == "rtl_test" else [tool],
                             capture_output=True, text=True, timeout=8)
            output = result.stderr + result.stdout
            # Parse lines like:
            #   0:  Realtek, RTL2838UHIDIR, SN: 00000001
            #   Found 2 device(s):
            import re as _re
            for m in _re.finditer(
                    r'(\d+):\s+([^,]+),\s+([^,]+),\s+SN:\s*(\S*)', output):
                idx, mfr, name, serial = m.groups()
                serial = serial.strip() or f"unknown_{idx}"
                devices.append({
                    "index":        int(idx),
                    "serial":       serial,
                    "name":         name.strip(),
                    "manufacturer": mfr.strip(),
                })
                self._index_cache[serial] = int(idx)
        except Exception as e:
            print(f"[SdrMgr] Scan error: {e}")

        self._scan_cache = devices
        self._cache_ts   = now
        return devices

    def resolve_index(self, serial: str) -> int:
        """
        Return the current device index for a given serial number.
        Raises SdrNotFoundError if the dongle is not connected.
        """
        devices = self.scan()
        for d in devices:
            if d["serial"] == serial:
                return d["index"]
        # Try cache if scan gave nothing useful
        if serial in self._index_cache:
            raise SdrNotFoundError(
                f"Dongle '{serial}' was previously at index "
                f"{self._index_cache[serial]} but is not currently connected."
            )
        raise SdrNotFoundError(f"Dongle with serial '{serial}' not found.")

    # ── Exclusive access ───────────────────────────────────────────────────────

    def claim(self, serial: str, owner: str = "") -> "SdrLease":
        """
        Claim exclusive access to a dongle by serial number.
        Returns an SdrLease context manager that resolves the device index
        and releases the claim when exited.
        Raises SdrBusyError if the dongle is already in use.
        Raises SdrNotFoundError if the dongle is not connected.
        """
        with self._lock:
            existing = self._owners.get(serial)
            if existing:
                raise SdrBusyError(
                    f"Dongle '{serial}' is already in use by '{existing}'."
                )
            idx = self.resolve_index(serial)
            self._owners[serial] = owner or serial
        return SdrLease(self, serial, idx)

    def release(self, serial: str):
        """Release a previously claimed dongle."""
        with self._lock:
            self._owners.pop(serial, None)

    def status(self) -> Dict[str, str]:
        """Return dict of serial → current owner for all claimed dongles."""
        with self._lock:
            return dict(self._owners)

    def connected_serials(self) -> List[str]:
        """Return list of serials of currently connected dongles."""
        return [d["serial"] for d in self.scan()]

    def unregistered(self, registered_serials: List[str]) -> List[Dict]:
        """Return connected dongles whose serial is not in the registered list."""
        reg_set = set(registered_serials)
        return [d for d in self.scan() if d["serial"] not in reg_set]


class SdrLease:
    """Context manager returned by SdrDeviceManager.claim()."""
    def __init__(self, manager: SdrDeviceManager, serial: str, index: int):
        self._manager = manager
        self.serial   = serial
        self.index    = index

    def __enter__(self):
        return self.index

    def __exit__(self, *_):
        self._manager.release(self.serial)

    def __repr__(self):
        return f"SdrLease(serial={self.serial!r}, index={self.index})"


class RateLimitError(RuntimeError):
    """Raised when the hub returns HTTP 429 — client should back off."""


class SdrBusyError(RuntimeError):
    """Raised when a requested dongle is already claimed by another input."""

class SdrNotFoundError(RuntimeError):
    """Raised when a requested dongle serial is not found in connected hardware."""


# Global singleton
sdr_manager = SdrDeviceManager()


app=Flask(__name__)

@app.route("/favicon.ico")
def favicon():
    return send_from_directory(STATIC_DIR, ICON_FILE, mimetype="image/vnd.microsoft.icon")

app.jinja_env.filters["enumerate"] = enumerate

# Apply security headers to every response
app.after_request(_apply_security_headers)

@app.context_processor
def _inject_csrf():
    """Make csrf_token() and csp_nonce() available in all Jinja templates."""
    return {"csrf_token": _csrf_token, "csp_nonce": _csp_nonce}

# Secure session cookie config
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# SESSION_COOKIE_SECURE is set at startup once we know if TLS/auth is active
# (see entry point — set True when cert loaded or auth enabled on HTTPS)

@app.context_processor
def _inject_nav():
    """Inject topnav() and hub_mode into every template."""
    try:
        mode  = monitor.app_cfg.hub.mode
        build_ = BUILD
        running_ = monitor.is_running()
    except Exception:
        mode = "client"; build_ = BUILD; running_ = False

    def topnav(active=""):
        show_hub = mode in ("hub", "both")
        def _a(page, label, href):
            cls = "btn bg bs nav-active" if active == page else "btn bg bs"
            return f'<a class="{cls}" href="{href}">{label}</a>'
        start_stop = ""
        if active == "dashboard":
            if running_:
                start_stop = '<form method="post" action="/stop" style="margin:0"><input type="hidden" name="_csrf_token" value="{{csrf_token()}}"><button class="btn bd bs">⏹ Stop</button></form>'
            else:
                start_stop = '<form method="post" action="/start" style="margin:0"><input type="hidden" name="_csrf_token" value="{{csrf_token()}}"><button class="btn bp bs">▶ Start</button></form>'
        hub_link = (_a("hub", "Hub", "/hub") + _a("hub_reports", "Hub Reports", "/hub/reports")) if show_hub else ""
        return (
            '<header>'
            '<a href="/" style="text-decoration:none;display:flex;align-items:center;gap:10px">'
            '<img src="/static/signalscope_logo.png" alt="SignalScope" style="height:36px;width:auto;display:block">'
            f'<span style="font-weight:700;font-size:15px;color:var(--tx)">{build_}</span>'
            '</a>'
            '<nav style="display:flex;gap:5px;align-items:center;flex-wrap:wrap;margin-left:auto">'
            + start_stop
            + _a("dashboard", "Dashboard", "/")
            + _a("inputs",    "Inputs",    "/inputs")
            + _a("reports",   "Reports",   "/reports")
            + _a("sla",       "SLA",       "/sla")
            + hub_link
            + _a("settings",  "Settings",  "/settings")
            + '<form method="post" action="/logout" style="margin:0">'+'<input type="hidden" name="_csrf_token" value="{{csrf_token()}}">'+'<button class="btn bg bs" style="color:var(--mu)">Logout</button></form>'
            + '</nav></header>'
        )

    from markupsafe import Markup
    def topnav_safe(active=""):
        return Markup(topnav(active))

    return {"hub_mode": mode, "topnav": topnav_safe}
_sk_path = os.path.join(BASE_DIR, ".flask_secret")
if os.path.exists(_sk_path):
    with open(_sk_path,"rb") as _f: app.secret_key = _f.read()
else:
    app.secret_key = os.urandom(32)
    with open(_sk_path,"wb") as _f: _f.write(app.secret_key)
monitor=MonitorManager()

MAIN_TPL = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>SignalScope</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style nonce="{{csp_nonce()}}">
:root{--bg:#0d0f14;--sur:#161a22;--bor:#252b38;--acc:#4f9cf9;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#e2e8f0;--mu:#64748b}
*{box-sizing:border-box;margin:0;padding:0}body{font-family:system-ui,sans-serif;background:var(--bg);color:var(--tx);font-size:14px}a{color:var(--acc);text-decoration:none}
header{background:var(--sur);border-bottom:1px solid var(--bor);padding:11px 20px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
header h1{font-size:17px;font-weight:700}.badge{font-size:11px;padding:2px 8px;border-radius:999px;background:#1e3a5f;color:var(--acc)}
.nav-active{background:var(--acc)!important;color:#fff!important}
nav{display:flex;gap:6px;margin-left:auto;flex-wrap:wrap}
.btn{display:inline-block;padding:5px 12px;border-radius:6px;font-size:13px;cursor:pointer;border:none;text-decoration:none}
.bp{background:var(--acc);color:#fff}.bd{background:var(--al);color:#fff}.bg{background:var(--bor);color:var(--tx)}
.bs{padding:3px 9px;font-size:12px}
main{padding:16px;max-width:1440px;margin:0 auto}
.fl{list-style:none;margin-bottom:10px}.fl li{padding:8px 12px;border-radius:6px;background:#1e3a5f;border-left:3px solid var(--acc);margin-bottom:5px;font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:12px}
/* ── Card ── */
.card{background:var(--sur);border:1px solid var(--bor);border-radius:10px;overflow:hidden}
.card.al-alert{border-color:#991b1b}.card.al-warn{border-color:#92400e}
.ch{padding:9px 13px;display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--bor);background:#11151d}
.dot{width:9px;height:9px;border-radius:50%;flex-shrink:0}
.dok{background:var(--ok)}.dwn{background:var(--wn)}.dal{background:var(--al);animation:p 1s infinite}.did{background:var(--mu)}.dlr{background:#38bdf8}
@keyframes p{0%,100%{opacity:1}50%{opacity:.3}}
/* ── Level bar ── */
.lbar-wrap{padding:8px 13px;border-bottom:1px solid var(--bor);display:flex;align-items:center;gap:8px}
.lbar-track{flex:1;height:7px;background:#1a1f2e;border-radius:4px;overflow:hidden}
.lbar-fill{height:7px;border-radius:4px;transition:width .4s,background .4s;min-width:2px}
.lbar-val{font-size:12px;font-weight:600;min-width:62px;text-align:right;font-variant-numeric:tabular-nums}
/* ── Rows ── */
.rows{padding:8px 13px}
.row{display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-bottom:1px solid var(--bor);font-size:12px;gap:8px}
.row:last-child{border:none}
.rl{color:var(--mu);flex-shrink:0}
.rv{text-align:right;word-break:break-word}
/* ── AI badge ── */
.aib{margin:0 13px 10px;padding:7px 9px;border-radius:6px;font-size:11px;line-height:1.5;border:1px solid var(--bor)}
.aok{background:#0f2318;border-color:#166534}.awn{background:#271a06;border-color:#92400e}
.aal{background:#2a0a0a;border-color:#991b1b}.alr{background:#0c1f38;border-color:#1d4ed8}.aid{background:var(--sur)}
/* ── Listen strip ── */
.listen-strip{padding:7px 13px;border-top:1px solid var(--bor);display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.listen-strip audio{flex:1;min-width:0;height:28px}
/* ── History ── */
.hist-wrap{border-top:1px solid var(--bor)}
.hist-toggle{width:100%;padding:5px 13px;background:none;border:none;color:var(--mu);font-size:11px;text-align:left;cursor:pointer;display:flex;justify-content:space-between}
.hist-toggle:hover{background:#1a1f2e}
.hist{max-height:90px;overflow-y:auto;font-size:11px;color:var(--mu)}
.hev{padding:3px 13px;border-bottom:1px solid var(--bor)}
.hSILENCE,.hAI_ALERT,.hRTP_LOSS{color:#f87171}.hCLIP{color:#fb923c}.hHISS,.hRTP_LOSS_WARN{color:#fbbf24}.hAI_WARN{color:#fcd34d}
/* ── Now Playing ── */
.np-strip{padding:7px 13px;border-top:1px solid var(--bor);display:flex;gap:9px;align-items:center;min-height:52px}
.np-art{width:38px;height:38px;border-radius:4px;object-fit:cover;display:none;flex-shrink:0}
.np-text{min-width:0}
.np-title{font-size:12px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.np-sub{font-size:11px;color:var(--mu);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
/* ── Progress bar ── */
.pb{background:var(--bor);border-radius:4px;height:4px;margin:4px 13px 8px;overflow:hidden}
.pbi{height:4px;border-radius:4px;background:var(--acc);transition:width .6s}
/* ── PTP / Log ── */
.mr{display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid var(--bor);font-size:13px}.mr:last-child{border:none}.ml{color:var(--mu)}
.cb{padding:11px 13px}
.logbox{background:var(--sur);border:1px solid var(--bor);border-radius:8px;padding:12px;font-family:monospace;font-size:11px;height:190px;overflow-y:auto;margin-top:12px;white-space:pre-wrap;word-break:break-all}
.st{font-size:12px;font-weight:600;color:var(--mu);letter-spacing:.06em;text-transform:uppercase;margin:14px 0 8px}
</style><link rel="icon" type="image/x-icon" href="/favicon.ico"></head><body>
{{ topnav("dashboard") }}
<main>
{% with m=get_flashed_messages() %}{% if m %}<ul class="fl">{% for x in m %}<li>{{x}}</li>{% endfor %}</ul>{% endif %}{% endwith %}
{% if not inputs %}<p style="color:var(--mu);margin:24px 0">No inputs. <a href="/inputs/add">Add one →</a></p>
{% else %}
  <div class="st">Streams ({{inputs|length}})</div>
  <div class="grid">
  {% for idx,inp in inputs %}
    {% set ai=inp._ai_status %}{% set ph=inp._ai_phase %}
    {% set ac='aid' %}
    {% if '[ALERT]' in ai %}{% set ac='aal' %}{% elif '[WARN]' in ai %}{% set ac='awn' %}{% elif '[OK]' in ai %}{% set ac='aok' %}{% elif 'Learning' in ai %}{% set ac='alr' %}{% endif %}
    {% set dc='did' %}
    {% if running and inp.enabled %}
      {% if '[ALERT]' in ai %}{% set dc='dal' %}{% elif '[WARN]' in ai %}{% set dc='dwn' %}{% elif ph=='learning' %}{% set dc='dlr' %}{% else %}{% set dc='dok' %}{% endif %}
    {% endif %}
    {% set lev=inp._last_level_dbfs %}
    {% set lpct=[(lev+80)/80*100,100]|min|int %}
    {% set lcol='var(--al)' if lev<=-55 else ('var(--wn)' if lev<=-20 else 'var(--ok)') %}

    <div class="card" id="card_{{idx}}" data-name="{{inp.name}}" data-dev="{{inp.device_index}}">
      {# ── Header ── #}
      <div class="ch">
        <div class="dot {{dc}}" id="dot_{{idx}}"></div>
        <strong style="font-size:13px">{{inp.name}}</strong>
        <span style="font-size:11px;color:var(--mu);margin-left:auto;overflow:hidden;text-overflow:ellipsis;max-width:140px;white-space:nowrap" title="{{inp.device_index}}">
          {{inp.device_index[:30]}}{{'…' if inp.device_index|length>30 else ''}}
        </span>
        <a class="btn bg bs" href="/inputs/{{idx}}/edit" style="flex-shrink:0">Edit</a>
      </div>

      {# ── Level bar ── #}
      <div class="lbar-wrap">
        <span style="font-size:11px;color:var(--mu);width:32px">Lvl</span>
        <div class="lbar-track">
          <div class="lbar-fill" id="lbar_{{idx}}" style="width:{{lpct}}%;background:{{lcol}}"></div>
        </div>
        <span class="lbar-val" id="lval_{{idx}}" style="color:{{lcol}}">{{lev|round(1)}} dB</span>
      </div>

      {# ── Info rows ── #}
      <div class="rows" id="rows_{{idx}}">
        <div class="row">
          <span class="rl">Format</span>
          <span class="rv" id="fmt_{{idx}}" style="font-size:11px;color:var(--mu)">{{inp._livewire_mode or '—'}}</span>
        </div>
        <div class="row">
          <span class="rl">RTP Loss</span>
          <span class="rv" id="rtp_{{idx}}" style="color:{%if inp._rtp_loss_pct>=2.0%}var(--al){%elif inp._rtp_loss_pct>=0.5%}var(--wn){%else%}var(--ok){%endif%}">
            {{inp._rtp_loss_pct|round(2)}}%
            <span style="color:var(--mu);font-size:11px"> {{inp._rtp_total|int}} pkts</span>
          </span>
        </div>
        <div class="row">
          <span class="rl">SLA</span>
          <span class="rv" id="sla_{{idx}}" style="font-size:12px;color:var(--ok)">—</span>
        </div>
        <div class="row">
          <span class="rl">Alerts</span>
          <span class="rv" style="font-size:12px">
            {%if inp.alert_on_silence%}<span title="Silence">🔇</span>{%endif%}
            {%if inp.alert_on_hiss%}<span title="Hiss">〰</span>{%endif%}
            {%if inp.alert_on_clip%}<span title="Clip">📈</span>{%endif%}
            {%if inp.ai_monitor%}<span title="AI">🤖</span>{%endif%}
          </span>
        </div>
        {% if inp.device_index.lower().startswith('dab://') %}
        <div class="row"><span class="rl">DAB SNR</span>
          <span class="rv" id="dab_snr_{{idx}}" style="color:{%if inp._dab_snr>=12%}var(--ok){%elif inp._dab_snr>=6%}var(--wn){%else%}var(--al){%endif%}">{{inp._dab_snr|round(1)}} dB</span></div>
        <div class="row"><span class="rl">Signal</span><span class="rv" id="dab_sig_{{idx}}">{{inp._dab_sig|round(1)}} dBm</span></div>
        <div class="row"><span class="rl">Ensemble</span><span class="rv" id="dab_ens_{{idx}}" style="font-size:11px">{{inp._dab_ensemble or '—'}}</span></div>
        {% endif %}
        {% if inp.device_index.lower().startswith('fm://') %}
        <div class="row"><span class="rl">Level</span>
          <span class="rv" id="fm_sig_{{idx}}" style="color:{%if inp._fm_signal_dbm>=-18%}var(--ok){%elif inp._fm_signal_dbm>=-28%}var(--wn){%else%}var(--al){%endif%}">{{inp._fm_signal_dbm|round(1)}} dBFS</span></div>
        <div class="row"><span class="rl">Pilot</span>
          <span class="rv" id="fm_snr_{{idx}}" style="color:{%if inp._fm_snr_db>=12%}var(--ok){%elif inp._fm_snr_db>=6%}var(--wn){%else%}var(--al){%endif%}">{{inp._fm_snr_db|round(1)}} dB</span></div>
        <div class="row"><span class="rl">Stereo</span>
          <span class="rv" id="fm_stereo_{{idx}}" style="color:{%if inp._fm_stereo%}var(--ok){%else%}var(--mu){%endif%}">{%if inp._fm_stereo%}✓ Stereo{%else%}Mono{%endif%}</span></div>
        <div class="row"><span class="rl">RDS</span>
          <span class="rv" id="fm_rds_{{idx}}" style="color:{% if inp._fm_rds_ok %}var(--ok){% else %}var(--mu){% endif %};font-size:11px">{{ inp._fm_rds_ps or (inp._fm_rds_status|default('No lock', true)) }}</span></div>
        <div class="row"><span class="rl">Text</span>
          <span class="rv" id="fm_rt_{{idx}}" style="font-size:11px">{{ inp._fm_rds_rt or '—' }}</span></div>
        {% endif %}
      </div>

      {# ── AI status ── #}
      {% if inp.ai_monitor %}
      <div class="aib {{ac}}" id="aib_{{idx}}">🤖 {{ai if ai else ('Waiting for stream…' if running else 'Not running')}}</div>
      {% if ph=='learning' %}
        {% set pct=[((now-inp._ai_learn_start)/learn_dur*100)|int,100]|min %}
        <div class="pb"><div class="pbi" id="aipb_{{idx}}" style="width:{{pct}}%"></div></div>
      {% endif %}
      {% if ph=='ready' and running %}
        <div style="margin:2px 13px 8px">
          <form method="post" action="/ai/retrain/{{idx}}" style="margin:0"><input type="hidden" name="_csrf_token" value="{{csrf_token()}}">
            <button class="btn bg" style="font-size:11px;padding:2px 8px">↺ Retrain AI</button>
          </form>
        </div>
      {% endif %}
      {% endif %}

      {# ── Now Playing ── #}
      {% if inp.nowplaying_station_id %}
      <div class="np-strip" id="np_{{idx}}">
        <img class="np-art" id="np_art_{{idx}}" src="" alt="">
        <div class="np-text">
          <div class="np-title" id="np_title_{{idx}}">Loading…</div>
          <div class="np-sub" id="np_artist_{{idx}}"></div>
          <div class="np-sub" id="np_show_{{idx}}" style="color:var(--acc)"></div>
        </div>
      </div>
      <script nonce="{{csp_nonce()}}">(function(){
        function fetchNP(){fetch('/api/nowplaying/{{inp.nowplaying_station_id}}').then(r=>r.json()).then(function(d){
          document.getElementById('np_title_{{idx}}').textContent=d.title||'—';
          document.getElementById('np_artist_{{idx}}').textContent=d.artist||'';
          document.getElementById('np_show_{{idx}}').textContent=d.show||'';
          var a=document.getElementById('np_art_{{idx}}');
          if(d.artwork){a.src='/api/nowplaying_art/{{inp.nowplaying_station_id}}?ts='+Date.now();a.style.display='block';} else { a.removeAttribute('src'); a.style.display='none'; }
        }).catch(function(){});}
        fetchNP();setInterval(fetchNP,30000);
      })();</script>
      {% endif %}

      {# ── Listen strip ── #}
      {% if running and inp.enabled %}
      <div class="listen-strip">
        <select id="dur_{{idx}}" style="padding:3px 5px;font-size:12px;background:#1e2433;border:1px solid var(--bor);border-radius:4px;color:var(--tx)">
          <option value="5">5s</option><option value="10" selected>10s</option>
          <option value="20">20s</option><option value="30">30s</option>
        </select>
        <a href="#" class="btn bg bs" data-idx="{{idx}}" data-action="clip">⬇ Clip</a>
        <button class="btn bp bs" id="livebtn_{{idx}}" data-idx="{{idx}}" data-action="live">▶ Live</button>
        <audio id="live_{{idx}}" style="display:none;flex:1;min-width:0;height:28px" controls></audio>
      </div>
      {% endif %}

      {# ── Saved clips ── #}
      <div class="hist-wrap">
        <button class="hist-toggle" data-idx="{{idx}}" data-action="clips">
          <span>💾 Saved Clips</span><span id="clips_arrow_{{idx}}">▶</span>
        </button>
        <div id="clips_panel_{{idx}}" style="display:none">
          <div id="clips_list_{{idx}}" style="font-size:12px;color:var(--mu);padding:6px 13px">Loading…</div>
        </div>
      </div>

      {# ── Event history ── #}
      <div class="hist-wrap">
        <button class="hist-toggle" data-idx="{{idx}}" data-action="hist">
          <span>📋 Recent Events</span><span id="hist_arrow_{{idx}}">▶</span>
        </button>
        <div id="hist_panel_{{idx}}" style="display:none">
          <div class="hist" id="hist_{{idx}}">
            {% for ev in inp._history[-5:]|reverse %}
            <div class="hev h{{ev.type}}">
              <span style="color:var(--mu)">{{ev.ts[-8:]}}</span>
              <span style="margin:0 4px;opacity:.5">[{{ev.type}}]</span>
              {{ev.message[:90]}}{{'…' if ev.message|length>90 else ''}}
            </div>
            {% else %}
            <div style="padding:6px 13px;color:var(--mu)">No events yet</div>
            {% endfor %}
          </div>
        </div>
      </div>

    </div>
  {% endfor %}
  </div>
{% endif %}

<div class="st">PTP Clock</div>
<div id="ptpcard" class="card" style="max-width:600px;margin-bottom:12px">
  <div class="ch">
    <div class="dot" id="ptpdot" style="background:var(--mu)"></div>
    <strong>IEEE 1588 / PTP</strong>
    <span style="font-size:11px;color:var(--mu);margin-left:auto">224.0.1.129</span>
  </div>
  <div class="cb">
    <div class="mr"><span class="ml">Status</span><span id="ptpstatus" style="font-size:12px">—</span></div>
    <div class="mr"><span class="ml">Offset</span><span id="ptpoffset">—</span></div>
    <div class="mr"><span class="ml">Jitter</span><span id="ptpjitter">—</span></div>
    <div class="mr"><span class="ml">Grandmaster</span><span id="ptpgm" style="font-family:monospace;font-size:12px">—</span></div>
    <div class="mr"><span class="ml">Domain</span><span id="ptpdomain">—</span></div>
    <div class="hist" id="ptphist"></div>
  </div>
</div>

{% if hub_client_enabled %}
<div class="st">Hub Connection</div>
<div id="hubconn-card" class="card" style="max-width:600px;margin-bottom:12px">
  <div class="ch">
    <div class="dot" id="hubconn-dot" style="background:var(--mu)"></div>
    <strong>Hub Reporting</strong>
    <span style="font-size:11px;color:var(--mu);margin-left:auto" id="hubconn-url">{{hub_url}}</span>
  </div>
  <div class="cb">
    <div class="mr"><span class="ml">State</span><span id="hubconn-state">—</span></div>
    <div class="mr"><span class="ml">Last ACK</span><span id="hubconn-ack">—</span></div>
    <div class="mr"><span class="ml">Sent</span><span id="hubconn-sent">—</span></div>
    <div class="mr"><span class="ml">Failed</span><span id="hubconn-failed">—</span></div>
    <div class="mr"><span class="ml">Queued</span><span id="hubconn-queued">—</span></div>
    <div class="mr" id="hubconn-err-row" style="display:none">
      <span class="ml">Last error</span>
      <span id="hubconn-err" style="color:var(--al);font-size:11px">—</span>
    </div>
  </div>
</div>
{% endif %}

{% if comparators %}
<div class="st">Stream Comparison</div>
<div class="grid" style="margin-bottom:12px" id="cmp-grid">
{% for cmp in comparators %}
<div class="card" id="cmpcard-{{loop.index0}}">
  <div class="ch">
    <div class="dot {% if 'SILENT' in cmp.status or 'DROPOUT' in cmp.status or 'shift' in cmp.status %}dal{% elif cmp.status=='OK' %}dok{% else %}did{% endif %}"
         id="cmpdot-{{loop.index0}}"></div>
    <strong>{{cmp.pre_name}} → {{cmp.post_name}}</strong>
    <span style="font-size:11px;color:var(--mu);margin-left:auto">Pre / Post Compare</span>
  </div>
  <div class="cb">
    {# Correlation bar #}
    <div class="mr" style="flex-direction:column;align-items:stretch;gap:4px">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span class="ml">Correlation</span>
        <span id="cmp-corr-val-{{loop.index0}}" style="font-size:12px;font-weight:600;color:{% if cmp.correlation>=0.85 %}#4ade80{% elif cmp.correlation>=0.65 %}#facc15{% elif cmp.correlation>=0.40 %}#fb923c{% else %}#f87171{% endif %}">
          {{(cmp.correlation*100)|round|int}}%
        </span>
      </div>
      <div style="background:#1e2433;border-radius:4px;height:8px;overflow:hidden">
        <div id="cmp-corr-bar-{{loop.index0}}"
             style="height:100%;border-radius:4px;transition:width 0.5s;
                    width:{{(cmp.correlation*100)|round|int}}%;
                    background:{% if cmp.correlation>=0.85 %}#4ade80{% elif cmp.correlation>=0.65 %}#facc15{% elif cmp.correlation>=0.40 %}#fb923c{% else %}#f87171{% endif %}">
        </div>
      </div>
    </div>
    {# Level meters — pre and post side by side #}
    <div style="display:flex;gap:8px;margin-top:6px">
      <div style="flex:1">
        <div style="font-size:10px;color:var(--mu);margin-bottom:3px">PRE (source)</div>
        <div style="background:#1e2433;border-radius:3px;height:6px;overflow:hidden">
          <div id="cmp-pre-bar-{{loop.index0}}"
               style="height:100%;border-radius:3px;transition:width 0.4s;
                      background:#60a5fa;
                      width:{{[(cmp.pre_dbfs+60)/60*100,0]|max|round|int}}%">
          </div>
        </div>
        <div id="cmp-pre-val-{{loop.index0}}" style="font-size:10px;color:var(--mu);margin-top:2px">
          {{cmp.pre_dbfs|round(1)}} dBFS
        </div>
      </div>
      <div style="flex:1">
        <div style="font-size:10px;color:var(--mu);margin-bottom:3px">POST (output)</div>
        <div style="background:#1e2433;border-radius:3px;height:6px;overflow:hidden">
          <div id="cmp-post-bar-{{loop.index0}}"
               style="height:100%;border-radius:3px;transition:width 0.4s;
                      background:#a78bfa;
                      width:{{[(cmp.post_dbfs+60)/60*100,0]|max|round|int}}%">
          </div>
        </div>
        <div id="cmp-post-val-{{loop.index0}}" style="font-size:10px;color:var(--mu);margin-top:2px">
          {{cmp.post_dbfs|round(1)}} dBFS
        </div>
      </div>
    </div>
    {# Delay + gain diff row #}
    <div style="display:flex;gap:8px;margin-top:8px">
      <div class="mr" style="flex:1;margin-top:0">
        <span class="ml">Delay</span>
        <span id="cmp-delay-{{loop.index0}}" style="font-size:12px">
          {% if cmp.aligned %}{{cmp.delay_ms|round|int}} ms{% else %}&mdash;{% endif %}
        </span>
      </div>
      <div class="mr" style="flex:1;margin-top:0">
        <span class="ml">Gain diff</span>
        <span id="cmp-gain-{{loop.index0}}"
              style="font-size:12px;font-weight:600;
                     color:{% if cmp.gain_diff_db|abs > 3 %}#fb923c{% else %}var(--tx){% endif %}">
          {% if cmp.aligned %}{{"%+.1f"|format(cmp.gain_diff_db)}} dB{% else %}&mdash;{% endif %}
        </span>
      </div>
    </div>
    {# Status row #}
    <div class="mr">
      <span class="ml">Status</span>
      <span id="cmp-status-{{loop.index0}}" style="font-size:12px">{{cmp.status}}</span>
    </div>
    {# Event history #}
    {% if cmp.cmp_history %}
    <details style="margin-top:6px">
      <summary style="font-size:11px;color:var(--mu);cursor:pointer">Recent events</summary>
      <div id="cmp-hist-{{loop.index0}}" style="font-size:11px;color:var(--mu);margin-top:4px;line-height:1.6">
        {% for ev in cmp.cmp_history|reverse %}
        <div>{{ev.ts}} &mdash; {{ev.msg}}</div>
        {% endfor %}
      </div>
    </details>
    {% else %}
    <div id="cmp-hist-{{loop.index0}}" style="display:none"></div>
    {% endif %}
  </div>
</div>
{% endfor %}
</div>
{% endif %}
<div class="st">Log</div>
<div class="logbox" id="lb">{{log_text}}</div>
</main>
<script nonce="{{csp_nonce()}}">
var running={{running|lower}};

// ── Card live update ────────────────────────────────────────────────────────
function setEl(id,val){ var e=document.getElementById(id); if(e) e.textContent=val; }
function setHTML(id,val){ var e=document.getElementById(id); if(e) e.innerHTML=val; }
function setStyle(id,prop,val){ var e=document.getElementById(id); if(e) e.style[prop]=val; }

function dotClass(inp){
  var ai=inp.ai_status||'', ph=inp.ai_phase||'';
  if(!running) return 'dot did';
  if(ai.indexOf('[ALERT]')>=0) return 'dot dal';
  if(ai.indexOf('[WARN]')>=0)  return 'dot dwn';
  if(ph==='learning') return 'dot dlr';
  return 'dot dok';
}

function rtpColor(pct){ return pct>=2.0?'var(--al)':pct>=0.5?'var(--wn)':'var(--ok)'; }
function levelColor(db){ return db<=-55?'var(--al)':db<=-20?'var(--wn)':'var(--ok)'; }
function slaColor(pct){ return pct>=99.9?'var(--ok)':pct>=99.0?'var(--wn)':'var(--al)'; }
function aiClass(ai){
  if((ai||'').indexOf('[ALERT]')>=0) return 'aib aal';
  if((ai||'').indexOf('[WARN]')>=0)  return 'aib awn';
  if((ai||'').indexOf('[OK]')>=0)    return 'aib aok';
  if((ai||'').indexOf('Learning')>=0)return 'aib alr';
  return 'aib aid';
}

var HIST_COLORS={'SILENCE':'#f87171','AI_ALERT':'#f87171','RTP_LOSS':'#f87171',
  'CLIP':'#fb923c','HISS':'#fbbf24','AI_WARN':'#fcd34d','RTP_LOSS_WARN':'#fbbf24'};

function updateCards(inputs){
  inputs.forEach(function(inp, idx){
    // Dot
    var dot=document.getElementById('dot_'+idx);
    if(dot) dot.className=dotClass(inp);

    // Level bar
    var db=inp.level_dbfs, col=levelColor(db);
    var pct=Math.min(Math.max((db+80)/80*100,0),100);
    setStyle('lbar_'+idx,'width',pct.toFixed(1)+'%');
    setStyle('lbar_'+idx,'background',col);
    setStyle('lval_'+idx,'color',col);
    setEl('lval_'+idx, db.toFixed(1)+' dB');

    // Format / mode
    setEl('fmt_'+idx, inp.livewire_mode||'—');

    // RTP
    var rtp=document.getElementById('rtp_'+idx);
    if(rtp){
      rtp.style.color=rtpColor(inp.rtp_loss_pct);
      rtp.innerHTML=inp.rtp_loss_pct.toFixed(2)+'% <span style="color:var(--mu);font-size:11px">'+inp.rtp_total+' pkts</span>';
    }

    // SLA
    var slaEl=document.getElementById('sla_'+idx);
    if(slaEl){
      slaEl.textContent=inp.sla_pct.toFixed(3)+'%';
      slaEl.style.color=slaColor(inp.sla_pct);
    }

    // DAB
    if(inp.dab_snr!==undefined){
      var snrEl=document.getElementById('dab_snr_'+idx);
      if(snrEl){
        snrEl.textContent=inp.dab_snr.toFixed(1)+' dB';
        snrEl.style.color=inp.dab_snr>=12?'var(--ok)':inp.dab_snr>=6?'var(--wn)':'var(--al)';
      }
      setEl('dab_sig_'+idx, inp.dab_sig.toFixed(1)+' dBm');
      setEl('dab_ens_'+idx, inp.dab_ensemble||'—');
    }

    // FM
    if(inp.fm_freq_mhz){
      var fmSig=document.getElementById('fm_sig_'+idx);
      if(fmSig){
        fmSig.textContent=inp.fm_signal_dbm.toFixed(1)+' dBm';
        fmSig.style.color=inp.fm_signal_dbm>=-70?'var(--ok)':inp.fm_signal_dbm>=-85?'var(--wn)':'var(--al)';
      }
      var fmSnr=document.getElementById('fm_snr_'+idx);
      if(fmSnr){
        fmSnr.textContent=inp.fm_snr_db.toFixed(1)+' dB';
        fmSnr.style.color=inp.fm_snr_db>=20?'var(--ok)':inp.fm_snr_db>=10?'var(--wn)':'var(--al)';
      }
      var fmSt=document.getElementById('fm_stereo_'+idx);
      if(fmSt){
        fmSt.textContent=inp.fm_stereo?'✓ Stereo':'Mono';
        fmSt.style.color=inp.fm_stereo?'var(--ok)':'var(--mu)';
      }
      var fmRds=document.getElementById('fm_rds_'+idx);
      if(fmRds){
        fmRds.textContent = inp.fm_rds_ps || inp.fm_rds_status || 'No lock';
        var fmRt=document.getElementById('fm_rt_'+idx);
        if(fmRt){ fmRt.textContent = inp.fm_rds_rt || '—'; }
        fmRds.style.color = inp.fm_rds_ok ? 'var(--ok)' : (inp.fm_rds_status && inp.fm_rds_status.indexOf('Detect')===0 ? 'var(--wn)' : 'var(--mu)');
      }
    }

    // AI badge
    var aib=document.getElementById('aib_'+idx);
    if(aib){
      aib.className=aiClass(inp.ai_status);
      aib.textContent='🤖 '+(inp.ai_status||'Waiting for stream…');
    }

    // History
    var histOpen=document.getElementById('hist_panel_'+idx);
    if(histOpen && histOpen.style.display!=='none'){
      var hist=document.getElementById('hist_'+idx);
      if(hist && inp.history && inp.history.length){
        hist.innerHTML=inp.history.slice().reverse().map(function(e){
          var col=HIST_COLORS[e.type]||'var(--mu)';
          var ts=(e.ts||'').slice(-8);
          return '<div class="hev"><span style="color:var(--mu)">'+ts+'</span>'
            +'<span style="margin:0 4px;opacity:.5">['+e.type+']</span>'
            +'<span style="color:'+col+'">'+e.message.slice(0,90)+'</span></div>';
        }).join('');
      }
    }
  });
}

// ── Main refresh ────────────────────────────────────────────────────────────

function corrColor(c){
  return c>=0.85?'#4ade80':c>=0.65?'#facc15':c>=0.40?'#fb923c':'#f87171';
}

function updateComparators(cmps){
  cmps.forEach(function(c,i){
    var dot=document.getElementById('cmpdot-'+i);
    if(dot){
      var alert=(c.status.indexOf('SILENT')>=0||c.status.indexOf('DROPOUT')>=0||c.status.indexOf('shift')>=0);
      var ok=(c.status==='OK');
      dot.className='dot '+(alert?'dal':ok?'dok':'did');
    }
    // Correlation bar + value
    var pct=Math.round(c.correlation*100);
    var col=corrColor(c.correlation);
    var cv=document.getElementById('cmp-corr-val-'+i);
    if(cv){cv.textContent=pct+'%';cv.style.color=col;}
    var cb=document.getElementById('cmp-corr-bar-'+i);
    if(cb){cb.style.width=pct+'%';cb.style.background=col;}
    // Pre bar
    var prePct=Math.max((c.pre_dbfs+60)/60*100,0);
    var pb=document.getElementById('cmp-pre-bar-'+i);
    if(pb) pb.style.width=Math.round(prePct)+'%';
    var pv=document.getElementById('cmp-pre-val-'+i);
    if(pv) pv.textContent=c.pre_dbfs.toFixed(1)+' dBFS';
    // Post bar
    var postPct=Math.max((c.post_dbfs+60)/60*100,0);
    var qb=document.getElementById('cmp-post-bar-'+i);
    if(qb) qb.style.width=Math.round(postPct)+'%';
    var qv=document.getElementById('cmp-post-val-'+i);
    if(qv) qv.textContent=c.post_dbfs.toFixed(1)+' dBFS';
    // Delay
    var dl=document.getElementById('cmp-delay-'+i);
    if(dl) dl.textContent=c.aligned?(Math.round(c.delay_ms)+' ms'):'—';
    // Gain diff
    var gd=document.getElementById('cmp-gain-'+i);
    if(gd){
      gd.textContent=c.aligned?((c.gain_diff_db>=0?'+':'')+c.gain_diff_db.toFixed(1)+' dB'):'—';
      gd.style.color=Math.abs(c.gain_diff_db)>(c.gain_alert_db||3)?'#fb923c':'var(--tx)';
    }
    // Status
    var st=document.getElementById('cmp-status-'+i);
    if(st) st.textContent=c.status;
    // History
    var hist=document.getElementById('cmp-hist-'+i);
    if(hist&&c.cmp_history&&c.cmp_history.length){
      hist.style.display='';
      hist.innerHTML=c.cmp_history.slice().reverse().map(function(e){
        return '<div>'+e.ts+' — '+e.msg+'</div>';
      }).join('');
    }
  });
}

function refresh(){
  fetch('/status.json').then(r=>r.json()).then(function(d){
    // Log
    var lb=document.getElementById('lb');
    if(lb){lb.textContent=d.logs.join('\n');lb.scrollTop=99999;}

    // Cards
    if(d.inputs) updateCards(d.inputs);

    // Comparators
    if(d.comparators) updateComparators(d.comparators);

    // Hub connection status
    var hb = d.hub;
    if(hb && document.getElementById('hubconn-dot')){
      var hdot = document.getElementById('hubconn-dot');
      var stateCol = {connected:'var(--ok)', degraded:'var(--wn)',
                      disconnected:'var(--al)', connecting:'var(--acc)', disabled:'var(--mu)'};
      hdot.style.background = stateCol[hb.state] || 'var(--mu)';
      var stateLabel = {connected:'Connected ✓', degraded:'Degraded ⚠',
                        disconnected:'Disconnected ✗', connecting:'Connecting…', disabled:'Disabled'};
      setEl('hubconn-state', stateLabel[hb.state] || hb.state);
      document.getElementById('hubconn-state').style.color = stateCol[hb.state] || 'var(--mu)';
      if(hb.last_ack){
        var ago = Math.round(Date.now()/1000 - hb.last_ack);
        setEl('hubconn-ack', ago < 5 ? 'just now' : ago < 60 ? ago+'s ago' : Math.round(ago/60)+'m ago');
      } else { setEl('hubconn-ack', 'never'); }
      setEl('hubconn-sent',   hb.sent   || 0);
      setEl('hubconn-failed', hb.failed  || 0);
      setEl('hubconn-queued', hb.queued  || 0);
      var errRow = document.getElementById('hubconn-err-row');
      if(hb.last_error && errRow){
        errRow.style.display = '';
        setEl('hubconn-err', hb.last_error);
      } else if(errRow) { errRow.style.display = 'none'; }
    }

    // PTP
    var p=d.ptp; if(!p) return;
    var dot=document.getElementById('ptpdot');
    if(dot){
      var cols={'ok':'var(--ok)','warn':'var(--wn)','alert':'var(--al)','lost':'var(--al)','idle':'var(--mu)'};
      dot.style.background=cols[p.state]||'var(--mu)';
      dot.style.animation=(p.state==='alert'||p.state==='lost')?'p 1s infinite':'';
    }
    setEl('ptpstatus', p.status||'—');
    var offEl=document.getElementById('ptpoffset');
    if(offEl) offEl.textContent=p.offset_us!==undefined
      ?((p.offset_us>=0?'+':'')+(p.offset_us/1000).toFixed(3)+' ms  (drift: '+(p.drift_us>=0?'+':'')+(p.drift_us/1000).toFixed(3)+' ms)'):'—';
    setEl('ptpjitter', p.jitter_us?(p.jitter_us/1000).toFixed(3)+' ms':'—');
    setEl('ptpgm', p.gm_id||'—');
    setEl('ptpdomain', p.domain!==undefined?p.domain:'—');
    var hist=document.getElementById('ptphist');
    if(hist&&p.history&&p.history.length){
      hist.innerHTML=p.history.slice().reverse().map(function(e){
        return '<div class="hev hPTP_'+(e.type||'').replace('PTP_','')+'">'+e.timestamp+' ['+e.type+'] '+e.message+'</div>';
      }).join('');
    }
  }).catch(function(){});}

if(running) setInterval(refresh,3000);
refresh();

// ── Toggles ─────────────────────────────────────────────────────────────────
// CSRF helper — reads token from meta tag injected by server
function _csrfHeaders(){
  var t=(document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  return {"X-CSRFToken": t, "Content-Type": "application/json"};
}
function _csrfFetch(url, opts){
  opts = opts || {};
  if(!opts.headers) opts.headers = {};
  var t=(document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  opts.headers["X-CSRFToken"] = t;
  return fetch(url, opts);
}
function toggleLive(idx,btn){
  var audio=document.getElementById('live_'+idx);
  if(!audio) return;
  if(audio.style.display==='none'||!audio.src){
    audio.src='/stream/'+idx+'/live';
    audio.style.display='block';
    audio.play().catch(function(){});
    btn.textContent='⏹ Stop'; btn.style.background='var(--al)';
  } else {
    audio.pause(); audio.src=''; audio.style.display='none';
    btn.textContent='▶ Live'; btn.style.background='';
  }
}
function toggleHist(idx){
  var panel=document.getElementById('hist_panel_'+idx);
  var arrow=document.getElementById('hist_arrow_'+idx);
  if(!panel) return;
  var open=panel.style.display==='none';
  panel.style.display=open?'block':'none';
  if(arrow) arrow.textContent=open?'▼':'▶';
}
function toggleClips(idx){
  var panel=document.getElementById('clips_panel_'+idx);
  var arrow=document.getElementById('clips_arrow_'+idx);
  var cards=document.querySelectorAll('.card[data-name]');
  var streamName=cards[idx]?cards[idx].getAttribute('data-name'):'';
  if(!panel) return;
  var open=panel.style.display==='none';
  panel.style.display=open?'block':'none';
  if(arrow) arrow.textContent=open?'▼':'▶';
  if(open) loadClips(idx,streamName);
}

// ── Event delegation for data-action buttons (CSP-safe, no inline onclick) ──
document.addEventListener('click', function(e){
  var btn = e.target.closest('[data-action]');
  if(!btn) return;
  var action = btn.dataset.action;
  var idx    = btn.dataset.idx;

  if(action === 'live'){
    var site = btn.dataset.site;
    if(site !== undefined){
      // Hub live button
      toggleLive(btn.dataset.sidx, site, btn);
    } else {
      toggleLive(idx, btn);
    }
  } else if(action === 'clips'){
    toggleClips(idx);
  } else if(action === 'hist'){
    toggleHist(idx);
  } else if(action === 'clip'){
    // Clip download
    var dur = document.getElementById('dur_'+idx);
    var secs = dur ? dur.value : '5';
    window.location = '/stream/'+idx+'/audio.wav?seconds='+secs;
  }
});

function loadClips(idx,streamName){
  var listEl=document.getElementById('clips_list_'+idx);
  fetch('/clips/'+encodeURIComponent(streamName))
    .then(function(r){return r.json();})
    .then(function(clips){
      if(!clips.length){listEl.innerHTML='<em>No saved clips yet</em>';return;}
      var html='';
      clips.forEach(function(c){
        html+='<div style="border-bottom:1px solid var(--bor);padding:6px 0">'
          +'<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:4px">'
          +'<span><b>'+c.alert_type+'</b> — '+c.ts+' ('+c.size_kb+'KB)</span>'
          +'<span style="display:flex;gap:4px">'
          +'<a href="/clips/'+encodeURIComponent(streamName)+'/'+encodeURIComponent(c.filename)+'" download class="btn bg" style="font-size:11px;padding:2px 7px">⬇</a>'
          +'<button class="btn" style="background:#3a0f0f;font-size:11px;padding:2px 7px" onclick="deleteClip(\''+encodeURIComponent(streamName)+'\',\''+encodeURIComponent(c.filename)+'\','+idx+')">🗑</button>'
          +'</span></div>'
          +'<audio controls src="/clips/'+encodeURIComponent(streamName)+'/'+encodeURIComponent(c.filename)+'" style="width:100%;height:28px;margin-top:4px"></audio>'
          +'</div>';
      });
      listEl.innerHTML=html;
    })
    .catch(function(){listEl.innerHTML='<em style="color:var(--al)">Failed to load clips</em>';});
}
function deleteClip(streamEnc,fileEnc,idx){
  if(!confirm('Delete this clip?')) return;
  fetch('/clips/'+streamEnc+'/'+fileEnc,{method:'DELETE'})
    .then(function(r){return r.json();})
    .then(function(j){if(j.ok) loadClips(idx,decodeURIComponent(streamEnc));});
}
</script>
</body></html>"""

REPORTS_TPL = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Alert Reports — SignalScope</title>
<style nonce="{{csp_nonce()}}">
:root{--bg:#0d0f14;--sur:#161a22;--bor:#252b38;--acc:#4f9cf9;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#e2e8f0;--mu:#64748b}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:var(--bg);color:var(--tx);font-size:13px}
header{background:var(--sur);border-bottom:1px solid var(--bor);padding:11px 20px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
header h1{font-size:16px;font-weight:700}
.badge{font-size:11px;padding:2px 8px;border-radius:999px;background:#1e3a5f;color:var(--acc)}
.nav-active{background:var(--acc)!important;color:#fff!important}
.btn{display:inline-block;padding:5px 12px;border-radius:6px;font-size:12px;cursor:pointer;border:none;text-decoration:none;font-weight:500}
.bg{background:var(--bor);color:var(--tx)}.bp{background:var(--acc);color:#fff}.bd{background:var(--al);color:#fff}.bw{background:#7c2d12;color:#fca5a5}.bs{padding:3px 9px;font-size:12px}.nav-active{background:var(--acc)!important;color:#fff!important}
.bw{background:#7c2d12;color:#fca5a5}.bo{background:#78350f;color:#fde68a}
.bd{background:var(--al);color:#fff}.bs{padding:3px 9px;font-size:12px}.nav-active{background:var(--acc)!important;color:#fff!important}
main{padding:16px 20px}
.filters{display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap;align-items:center}
.filters select,.filters input{background:#1e2433;border:1px solid var(--bor);border-radius:6px;color:var(--tx);padding:5px 9px;font-size:12px}
.filters label{color:var(--mu);font-size:12px}
.summary{display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap}
.sc{background:var(--sur);border:1px solid var(--bor);border-radius:8px;padding:10px 14px;min-width:100px}
.sc-val{font-size:22px;font-weight:700}.sc-lbl{font-size:11px;color:var(--mu);margin-top:2px}
table{width:100%;border-collapse:collapse}
thead th{text-align:left;padding:7px 10px;background:var(--sur);border-bottom:2px solid var(--bor);font-size:11px;color:var(--mu);text-transform:uppercase;letter-spacing:.05em;white-space:nowrap;position:sticky;top:0;z-index:2}
tbody td{padding:8px 10px;border-bottom:1px solid var(--bor);vertical-align:top}
tr:hover td{background:#1a1f2e}
.type-badge{display:inline-block;padding:2px 7px;border-radius:999px;font-size:11px;font-weight:600;white-space:nowrap}
.t-silence{background:#1e2433;color:#93c5fd}
.t-clip{background:#3a1e1e;color:#fca5a5}
.t-hiss{background:#2a2a1e;color:#fde68a}
.t-rtp{background:#1e2433;color:#c4b5fd}
.t-ai_alert{background:#3a1e1e;color:#fca5a5}
.t-ai_warn{background:#3a2a1e;color:#fde68a}
.t-ptp{background:#1e3a2a;color:#86efac}
.t-cmp{background:#2a1e3a;color:#d8b4fe}
.t-other{background:var(--bor);color:var(--mu)}
.ptp-block{background:#0d1520;border-radius:5px;padding:5px 8px;margin-top:4px;font-size:11px;color:var(--mu);display:grid;grid-template-columns:1fr 1fr;gap:2px 12px}
.ptp-block span{white-space:nowrap}.ptp-v{color:var(--tx)}
.level-bar{display:inline-block;width:60px;height:6px;background:var(--bor);border-radius:3px;vertical-align:middle;margin-left:4px}
.level-fill{height:6px;border-radius:3px}
audio{height:28px;width:200px;accent-color:var(--acc);vertical-align:middle}
.clip-btn{display:inline-flex;align-items:center;gap:4px;padding:3px 8px;border-radius:5px;background:#1e3a5f;color:var(--acc);font-size:11px;cursor:pointer;border:none}
.no-data{text-align:center;padding:48px;color:var(--mu)}
.page-info{color:var(--mu);font-size:12px;margin-left:auto}
</style>
<link rel="icon" type="image/x-icon" href="/favicon.ico"></head><body>
{{ topnav("reports") }}
<div style="padding:8px 20px;background:var(--sur);border-bottom:1px solid var(--bor);display:flex;gap:7px;align-items:center;flex-wrap:wrap">
  <span style="font-size:13px;font-weight:600">📋 Alert Reports</span>
  <div style="margin-left:auto;display:flex;gap:7px">
    <a href="/reports.csv" class="btn bp">⬇ CSV</a>
    <form method="post" action="/reports/clear" style="display:inline"><input type="hidden" name="_csrf_token" value="{{csrf_token()}}"><button class="btn bw" type="submit" onclick="return confirm('Clear all alert history?')">🗑 Clear</button></form>
  </div>
</div>

<main>
  {# Summary cards #}
  <div class="summary">
    <div class="sc"><div class="sc-val" id="sc-total">{{total}}</div><div class="sc-lbl">Total Events</div></div>
    <div class="sc"><div class="sc-val" id="sc-critical" style="color:var(--al)">{{counts.get('SILENCE',0)+counts.get('AI_ALERT',0)+counts.get('RTP_LOSS',0)}}</div><div class="sc-lbl">🔴 Critical</div></div>
    <div class="sc"><div class="sc-val" id="sc-warn" style="color:var(--wn)">{{counts.get('AI_WARN',0)+counts.get('RTP_LOSS_WARN',0)+counts.get('HISS',0)}}</div><div class="sc-lbl">🟡 Warnings</div></div>
    <div class="sc"><div class="sc-val" id="sc-rtp" style="color:var(--acc)">{{counts.get('RTP_LOSS',0)+counts.get('RTP_LOSS_WARN',0)}}</div><div class="sc-lbl">📦 RTP Loss</div></div>
    <div class="sc"><div class="sc-val" id="sc-ptp" style="color:#86efac">{{counts.get('PTP_OFFSET',0)+counts.get('PTP_JITTER',0)+counts.get('PTP_LOST',0)+counts.get('PTP_GM_CHANGE',0)}}</div><div class="sc-lbl">🕐 PTP Events</div></div>
    <div class="sc"><div class="sc-val" id="sc-clips">{{with_clips}}</div><div class="sc-lbl">🎵 With Clips</div></div>
  </div>

  {# Filters #}
  <div class="filters">
    <label>Stream
      <select id="f_stream" onchange="applyFilters()">
        <option value="">All streams</option>
        {% for s in streams %}<option value="{{s}}">{{s}}</option>{% endfor %}
      </select>
    </label>
    <label>Type
      <select id="f_type" onchange="applyFilters()">
        <option value="">All types</option>
        {% for t in types %}<option value="{{t}}">{{t}}</option>{% endfor %}
      </select>
    </label>
    <label>From <input type="datetime-local" id="f_from" onchange="applyFilters()"></label>
    <label>To   <input type="datetime-local" id="f_to"   onchange="applyFilters()"></label>
    <label><input type="checkbox" id="f_clips" onchange="applyFilters()"> Clips only</label>
    <span class="page-info" id="row_count"></span>
  </div>

  {# Events table #}
  <div class="table-wrap">
  <table id="evt_table">
    <thead>
      <tr>
        <th style="width:135px">Time</th>
        <th style="width:130px">Stream</th>
        <th style="width:100px">Type</th>
        <th>Detail</th>
        <th style="width:80px">Level</th>
        <th style="width:75px">RTP Loss</th>
        <th>PTP @ Alert</th>
        <th style="width:220px">Clip</th>
      </tr>
    </thead>
    <tbody id="evt_body">
    {% for e in events %}
    {% set tl = e.type.lower() %}
    {% set tc = 't-silence' if tl=='silence' else ('t-clip' if tl=='clip' else ('t-hiss' if tl=='hiss' else ('t-rtp' if 'rtp' in tl else ('t-ai_alert' if tl=='ai_alert' else ('t-ai_warn' if tl=='ai_warn' else ('t-ptp' if 'ptp' in tl else ('t-cmp' if 'cmp' in tl else 't-other'))))))) %}
    <tr data-stream="{{e.stream}}" data-type="{{e.type}}" data-ts="{{e.ts}}" data-clip="{{e.clip}}">
      <td style="color:var(--mu);font-size:12px;white-space:nowrap">{{e.ts}}</td>
      <td><strong>{{e.stream}}</strong></td>
      <td><span class="type-badge {{tc}}">{{e.type}}</span></td>
      <td style="font-size:12px">{{e.message}}</td>
      <td>
        {% if e.level_dbfs and e.level_dbfs > -120 %}
        <span style="font-size:12px;color:{{'var(--al)' if e.level_dbfs<=-55 else 'var(--ok)'}}">{{e.level_dbfs}} dB</span>
        <span class="level-bar"><span class="level-fill" style="width:{{[(e.level_dbfs+80)/80*100,100]|min|int}}%;background:{{'var(--al)' if e.level_dbfs<=-55 else 'var(--ok)'}}"></span></span>
        {% else %}—{% endif %}
      </td>
      <td>
        {% if e.rtp_loss_pct > 0 %}
        <span style="color:{{'var(--al)' if e.rtp_loss_pct>=2 else 'var(--wn)'}}">{{e.rtp_loss_pct}}%</span>
        {% else %}—{% endif %}
      </td>
      <td>
        {% if e.ptp_state %}
        <div class="ptp-block">
          <span>State <span class="ptp-v" style="color:{{'var(--ok)' if e.ptp_state=='locked' else 'var(--wn)'}}">{{e.ptp_state}}</span></span>
          <span>GM <span class="ptp-v" style="font-size:10px">{{(e.ptp_gm or '—')[:12]}}</span></span>
          <span>Offset <span class="ptp-v">{{(e.ptp_offset_us/1000)|round(2)}} ms</span></span>
          <span>Drift <span class="ptp-v" style="color:{{'var(--ok)' if e.ptp_drift_us|abs < 1000 else 'var(--wn)'}}">{{(e.ptp_drift_us/1000)|round(2)}} ms</span></span>
          <span>Jitter <span class="ptp-v">{{(e.ptp_jitter_us/1000)|round(2)}} ms</span></span>
        </div>
        {% else %}<span style="color:var(--mu);font-size:11px">—</span>{% endif %}
      </td>
      <td>
        {% if e.clip %}
        <audio controls preload="none" src="/clips/{{e.stream}}/{{e.clip}}"></audio>
        {% else %}<span style="color:var(--mu);font-size:11px">—</span>{% endif %}
      </td>
    </tr>
    {% else %}
    <tr><td colspan="8" class="no-data">No alert events recorded yet. Events appear here once monitoring starts and alerts fire.</td></tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
</main>

<script nonce="{{csp_nonce()}}">
function typeCls(t){
  t=(t||'').toLowerCase();
  if(t==='silence') return 't-silence';
  if(t==='clip') return 't-clip';
  if(t==='hiss') return 't-hiss';
  if(t.includes('rtp')) return 't-rtp';
  if(t==='ai_alert') return 't-ai_alert';
  if(t==='ai_warn') return 't-ai_warn';
  if(t.includes('ptp')) return 't-ptp';
  if(t.includes('cmp')) return 't-cmp';
  return 't-other';
}
function applyFilters(){
  var fs = document.getElementById('f_stream').value.toLowerCase();
  var ft = document.getElementById('f_type').value.toLowerCase();
  var ff = document.getElementById('f_from').value;
  var ft2= document.getElementById('f_to').value;
  var fc = document.getElementById('f_clips').checked;
  var rows = document.querySelectorAll('#evt_body tr[data-stream]');
  var vis = 0;
  rows.forEach(function(r){
    var s = (r.dataset.stream||'').toLowerCase();
    var t = (r.dataset.type||'').toLowerCase();
    var ts= r.dataset.ts||'';
    var c = r.dataset.clip||'';
    var show = true;
    if(fs && s!==fs) show=false;
    if(ft && !t.includes(ft)) show=false;
    if(ff && ts < ff.replace('T',' ')) show=false;
    if(ft2 && ts > ft2.replace('T',' ')) show=false;
    if(fc && !c) show=false;
    r.style.display = show ? '' : 'none';
    if(show) vis++;
  });
  document.getElementById('row_count').textContent = vis+' event'+(vis!==1?'s':'')+' shown';
}
function rowHTML(e){
  var tl=(e.type||'').toLowerCase();
  var tc=typeCls(tl);
  var lvl='—';
  if(e.level_dbfs && e.level_dbfs > -120){
    var pct=Math.min(Math.max((e.level_dbfs+80)/80*100,0),100);
    var lc=e.level_dbfs<=-55?'var(--al)':'var(--ok)';
    lvl='<span style="font-size:12px;color:'+lc+'">'+e.level_dbfs+' dB</span>'
       +'<span class="level-bar"><span class="level-fill" style="width:'+Math.round(pct)+'%;background:'+lc+'"></span></span>';
  }
  var rtp='—';
  if(e.rtp_loss_pct>0){
    var rc=e.rtp_loss_pct>=2?'var(--al)':'var(--wn)';
    rtp='<span style="color:'+rc+'">'+e.rtp_loss_pct+'%</span>';
  }
  var ptp='<span style="color:var(--mu);font-size:11px">—</span>';
  if(e.ptp_state){
    var sc=e.ptp_state==='locked'?'var(--ok)':'var(--wn)';
    var dc=Math.abs(e.ptp_drift_us||0)<1000?'var(--ok)':'var(--wn)';
    var gmShort=(e.ptp_gm||'—').substring(0,12);
    ptp='<div class="ptp-block">'
       +'<span>State <span class="ptp-v" style="color:'+sc+'">'+e.ptp_state+'</span></span>'
       +'<span>GM <span class="ptp-v" style="font-size:10px">'+gmShort+'</span></span>'
       +'<span>Offset <span class="ptp-v">'+((e.ptp_offset_us||0)/1000).toFixed(2)+' ms</span></span>'
       +'<span>Drift <span class="ptp-v" style="color:'+dc+'">'+((e.ptp_drift_us||0)/1000).toFixed(2)+' ms</span></span>'
       +'<span>Jitter <span class="ptp-v">'+((e.ptp_jitter_us||0)/1000).toFixed(2)+' ms</span></span>'
       +'</div>';
  }
  var clip='<span style="color:var(--mu);font-size:11px">—</span>';
  if(e.clip){
    clip='<audio controls preload="none" src="/clips/'+encodeURIComponent(e.stream)+'/'+e.clip+'" style="height:28px;width:200px;accent-color:var(--acc);vertical-align:middle"></audio>';
  }
  return '<tr data-stream="'+escAttr(e.stream)+'" data-type="'+escAttr(e.type)+'" data-ts="'+escAttr(e.ts||'')+'" data-clip="'+escAttr(e.clip||'')+'">'
    +'<td style="color:var(--mu);font-size:12px;white-space:nowrap">'+escHTML(e.ts||'')+'</td>'
    +'<td><strong>'+escHTML(e.stream||'')+'</strong></td>'
    +'<td><span class="type-badge '+tc+'">'+escHTML(e.type||'')+'</span></td>'
    +'<td style="font-size:12px">'+escHTML(e.message||'')+'</td>'
    +'<td>'+lvl+'</td>'
    +'<td>'+rtp+'</td>'
    +'<td>'+ptp+'</td>'
    +'<td>'+clip+'</td>'
    +'</tr>';
}
function escHTML(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function escAttr(s){return String(s).replace(/"/g,'&quot;');}

function refreshReports(){
  fetch('/reports/data').then(function(r){return r.json();}).then(function(d){
    // Update summary cards
    var counts=d.counts||{};
    var els={
      'sc-critical': (counts['SILENCE']||0)+(counts['AI_ALERT']||0)+(counts['RTP_LOSS']||0),
      'sc-warn':     (counts['AI_WARN']||0)+(counts['RTP_LOSS_WARN']||0)+(counts['HISS']||0),
      'sc-rtp':      (counts['RTP_LOSS']||0)+(counts['RTP_LOSS_WARN']||0),
      'sc-ptp':      (counts['PTP_OFFSET']||0)+(counts['PTP_JITTER']||0)+(counts['PTP_LOST']||0)+(counts['PTP_GM_CHANGE']||0),
      'sc-total':    d.total||0,
      'sc-clips':    d.with_clips||0
    };
    Object.keys(els).forEach(function(id){
      var el=document.getElementById(id);
      if(el) el.textContent=els[id];
    });

    // Rebuild only new rows (rows that don't already exist by ts+stream key)
    var body=document.getElementById('evt_body');
    if(!body) return;
    var existing=new Set();
    body.querySelectorAll('tr[data-stream]').forEach(function(r){
      existing.add((r.dataset.ts||'')+'|'+(r.dataset.stream||'')+'|'+(r.dataset.type||''));
    });
    var newRows=[];
    (d.events||[]).forEach(function(e){
      var key=(e.ts||'')+'|'+(e.stream||'')+'|'+(e.type||'');
      if(!existing.has(key)) newRows.push(e);
    });
    if(newRows.length===0) return;  // nothing changed — don't touch the DOM at all

    // Prepend new rows at top (events are newest-first from the API)
    var fragment=document.createDocumentFragment();
    newRows.forEach(function(e){
      var tmp=document.createElement('tbody');
      tmp.innerHTML=rowHTML(e);
      fragment.appendChild(tmp.firstChild);
    });
    // Remove "no events yet" placeholder if present
    var placeholder=body.querySelector('td[colspan]');
    if(placeholder) placeholder.closest('tr').remove();
    body.insertBefore(fragment, body.firstChild);
    applyFilters();  // re-apply current filters to include new rows
  }).catch(function(){});  // silent fail — just wait for next tick
}

window.addEventListener('DOMContentLoaded', function(){
  applyFilters();
  setInterval(refreshReports, 15000);
});
</script>
</body></html>"""

SLA_TPL = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>SLA Dashboard</title>
<style nonce="{{csp_nonce()}}">:root{--bg:#0d0f14;--sur:#161a22;--bor:#252b38;--acc:#4f9cf9;--ok:#22c55e;--al:#ef4444;--tx:#e2e8f0;--mu:#64748b}
*{box-sizing:border-box;margin:0;padding:0}body{font-family:system-ui,sans-serif;background:var(--bg);color:var(--tx);font-size:14px}
header{background:var(--sur);border-bottom:1px solid var(--bor);padding:11px 20px;display:flex;align-items:center;gap:10px}
header h1{font-size:16px;font-weight:700}.badge{font-size:11px;padding:2px 8px;border-radius:999px;background:#1e3a5f;color:var(--acc)}
.nav-active{background:var(--acc)!important;color:#fff!important}
.btn{display:inline-block;padding:5px 12px;border-radius:6px;font-size:13px;cursor:pointer;border:none;text-decoration:none}
.bg{background:var(--bor);color:var(--tx)}.bp{background:var(--acc);color:#fff}.bd{background:var(--al);color:#fff}.bw{background:#7c2d12;color:#fca5a5}.bs{padding:3px 9px;font-size:12px}.nav-active{background:var(--acc)!important;color:#fff!important}
main{padding:22px;max-width:900px;margin:0 auto}
table{width:100%;border-collapse:collapse;margin-top:16px}
th{text-align:left;padding:8px 12px;background:var(--sur);border-bottom:2px solid var(--bor);font-size:12px;color:var(--mu);text-transform:uppercase;letter-spacing:.05em}
td{padding:10px 12px;border-bottom:1px solid var(--bor);font-size:13px}
tr:hover td{background:#1a1f2e}
.bar-wrap{background:var(--bor);border-radius:999px;height:8px;width:120px;display:inline-block;vertical-align:middle;margin-left:8px}
.bar-fill{height:8px;border-radius:999px;transition:width .3s}
.ok-color{color:var(--ok)}.al-color{color:var(--al)}
.tag{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:600}
.tag-ok{background:#1e3a1e;color:var(--ok)}.tag-fail{background:#3a1e1e;color:var(--al)}
</style><link rel="icon" type="image/x-icon" href="/favicon.ico"></head><body>
{{ topnav("sla") }}
<div style="padding:8px 20px;background:var(--sur);border-bottom:1px solid var(--bor);display:flex;gap:7px;align-items:center">
  <span style="font-size:13px;font-weight:600">📊 SLA Dashboard</span>
  <span style="color:var(--mu);font-size:12px;margin-left:4px">Target: {{target}}%</span>
  <div style="margin-left:auto"><a href="/sla.csv" class="btn bp">⬇ Export CSV</a></div>
</div>
<main>
  <table>
    <thead>
      <tr>
        <th>Stream</th><th>Month</th><th>Uptime</th><th>Monitored</th><th>Downtime</th><th>SLA</th>
      </tr>
    </thead>
    <tbody>
    {% for r in rows %}
    <tr>
      <td><strong>{{r.name}}</strong></td>
      <td style="color:var(--mu)">{{r.month}}</td>
      <td>
        <span class="{{'ok-color' if r.ok else 'al-color'}}">{{r.pct}}%</span>
        <span class="bar-wrap"><span class="bar-fill" style="width:{{[r.pct,100]|min}}%;background:{{'var(--ok)' if r.ok else 'var(--al)'}};"></span></span>
      </td>
      <td style="color:var(--mu)">{{r.mon_h}} h</td>
      <td style="color:var(--mu)">{{r.down_m}} min</td>
      <td><span class="tag {{'tag-ok' if r.ok else 'tag-fail'}}">{{'✓ Met' if r.ok else '✗ Missed'}}</span></td>
    </tr>
    {% else %}
    <tr><td colspan="6" style="text-align:center;color:var(--mu);padding:32px">No data yet — start monitoring to begin SLA tracking.</td></tr>
    {% endfor %}
    </tbody>
  </table>
  <p style="margin-top:14px;font-size:12px;color:var(--mu)">SLA resets monthly. Downtime counted when stream level is at or below the silence threshold.</p>
</main></body></html>"""

INPUT_LIST_TPL = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Inputs</title>
<style nonce="{{csp_nonce()}}">:root{--bg:#0d0f14;--sur:#161a22;--bor:#252b38;--acc:#4f9cf9;--tx:#e2e8f0;--mu:#64748b}
*{box-sizing:border-box;margin:0;padding:0}body{font-family:system-ui,sans-serif;background:var(--bg);color:var(--tx);font-size:14px}
header{background:var(--sur);border-bottom:1px solid var(--bor);padding:11px 20px;display:flex;align-items:center;gap:10px}
header h1{font-size:16px;font-weight:700}.btn{display:inline-block;padding:5px 12px;border-radius:6px;font-size:13px;cursor:pointer;border:none;text-decoration:none}
.bp{background:var(--acc);color:#fff}.bg{background:var(--bor);color:var(--tx)}.bd{background:var(--al);color:#fff}.bw{background:#7c2d12;color:#fca5a5}.bs{padding:3px 9px;font-size:12px}.nav-active{background:var(--acc)!important;color:#fff!important}.bd{background:var(--al);color:#fff}.bw{background:#7c2d12;color:#fca5a5}.bs{padding:3px 9px;font-size:12px}.nav-active{background:var(--acc)!important;color:#fff!important}
main{padding:20px;max-width:900px;margin:0 auto}
table{width:100%;border-collapse:collapse;background:var(--sur);border-radius:8px;overflow:hidden}
th,td{padding:10px 14px;text-align:left;border-bottom:1px solid var(--bor)}th{color:var(--mu);font-weight:600;font-size:12px;text-transform:uppercase}
.fl{list-style:none;margin-bottom:10px}.fl li{padding:8px 12px;border-radius:6px;background:#1e3a5f;border-left:3px solid var(--acc);margin-bottom:5px}</style><link rel="icon" type="image/x-icon" href="/favicon.ico"></head><body>
{{ topnav("inputs") }}
<div style="padding:8px 20px;background:var(--sur);border-bottom:1px solid var(--bor);display:flex;align-items:center">
  <span style="font-size:13px;font-weight:600">Inputs</span>
  <a class="btn bp" style="margin-left:auto" href="/inputs/add">+ Add</a>
</div>
<main>
{% with m=get_flashed_messages() %}{% if m %}<ul class="fl">{% for x in m %}<li>{{x}}</li>{% endfor %}</ul>{% endif %}{% endwith %}
{% if inputs %}
<table><thead><tr><th>#</th><th>Name</th><th>Stream</th><th>Alerts</th><th>AI Model</th><th></th></tr></thead><tbody>
{% for i,inp in inputs %}<tr>
  <td>{{i+1}}</td><td>{{inp.name}}</td>
  <td style="font-family:monospace;font-size:12px">{{inp.device_index}}</td>
  <td style="font-size:12px">{%if inp.alert_on_silence%}Silence {%endif%}{%if inp.alert_on_hiss%}Hiss {%endif%}{%if inp.alert_on_clip%}Clip{%endif%}</td>
  <td style="font-size:12px">{%if inp.ai_monitor%}{%if model_exists[i]%}✅ Ready{%else%}⏳ Needs training{%endif%}{%else%}—{%endif%}</td>
  <td style="display:flex;gap:5px">
    <a class="btn bg" href="/inputs/{{i}}/edit" style="font-size:12px;padding:3px 9px">Edit</a>
    <form method="post" action="/inputs/{{i}}/delete" style="margin:0"><input type="hidden" name="_csrf_token" value="{{csrf_token()}}"><button class="btn bd" style="font-size:12px;padding:3px 9px">Del</button></form>
  </td>
</tr>{% endfor %}
</tbody></table>
{% else %}<p style="color:#64748b;margin:24px 0">No inputs yet.</p>{% endif %}
</main></body></html>"""


# ─── First-run setup wizard ───────────────────────────────────────────────────

SETUP_TPL = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>SignalScope — Setup</title>
<meta name="csrf-token" content="{{csrf_token()}}">
<style nonce="{{csp_nonce()}}">
:root{--bg:#0d0f14;--sur:#161a22;--bor:#252b38;--acc:#4f9cf9;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#e2e8f0;--mu:#64748b}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:var(--bg);color:var(--tx);font-size:14px;min-height:100vh}
.header{background:var(--sur);border-bottom:1px solid var(--bor);padding:14px 24px;display:flex;align-items:center;gap:12px}
.header h1{font-size:18px;font-weight:700}.header .sub{font-size:13px;color:var(--mu)}
.layout{display:flex;min-height:calc(100vh - 50px)}
.steps{width:220px;flex-shrink:0;background:var(--sur);border-right:1px solid var(--bor);padding:20px 0}
.step-btn{display:flex;align-items:center;gap:10px;width:100%;padding:10px 16px;background:none;border:none;border-left:3px solid transparent;color:var(--mu);font-size:13px;cursor:pointer;text-align:left}
.step-btn.active{background:#1a2030;color:var(--tx);border-left-color:var(--acc);font-weight:600}
.step-btn.done{color:var(--ok)}.step-btn.done .step-icon::after{content:"✓"}
.step-icon{width:22px;height:22px;border-radius:50%;background:var(--bor);display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;flex-shrink:0}
.step-btn.active .step-icon{background:var(--acc);color:#fff}
.step-btn.done .step-icon{background:var(--ok);color:#fff}
.content{flex:1;padding:32px;max-width:720px}
.pn{display:none}.pn.active{display:block}
h2{font-size:20px;font-weight:700;margin-bottom:8px;color:var(--tx)}
.sub-h{font-size:13px;color:var(--mu);margin-bottom:24px;line-height:1.6}
.check-row{display:flex;align-items:center;gap:12px;padding:12px 14px;background:var(--sur);border:1px solid var(--bor);border-radius:8px;margin-bottom:8px}
.check-icon{font-size:18px;width:28px;text-align:center;flex-shrink:0}
.check-label{flex:1}
.check-name{font-weight:600;font-size:13px}
.check-desc{font-size:12px;color:var(--mu);margin-top:2px}
.check-action{flex-shrink:0}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}
.badge-ok{background:#14532d;color:#86efac}
.badge-warn{background:#451a03;color:#fde68a}
.badge-err{background:#450a0a;color:#fca5a5}
.badge-na{background:var(--bor);color:var(--mu)}
label{display:block;margin-top:14px;color:var(--mu);font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.05em}
input[type=text],input[type=password],select{width:100%;margin-top:4px;padding:9px 11px;background:#1e2433;border:1px solid var(--bor);border-radius:6px;color:var(--tx);font-size:14px}
.cr{display:flex;align-items:center;gap:8px;margin-top:10px}input[type=checkbox]{width:16px;height:16px;accent-color:var(--acc)}
.help{font-size:12px;color:var(--mu);margin-top:5px;line-height:1.5}
.btn{display:inline-block;padding:8px 18px;border-radius:6px;font-size:13px;cursor:pointer;border:none;text-decoration:none;font-weight:600}
.bp{background:var(--acc);color:#fff}.bg{background:var(--bor);color:var(--tx)}
.act{margin-top:28px;display:flex;gap:10px;align-items:center}
code{background:#1e2433;padding:1px 7px;border-radius:4px;font-family:monospace;font-size:12px}
.cmd-block{background:#060810;border:1px solid var(--bor);border-radius:6px;padding:10px 14px;font-family:monospace;font-size:12px;color:#94a3b8;margin:8px 0;position:relative}
.cmd-block .copy-btn{position:absolute;top:6px;right:8px;padding:2px 8px;font-size:11px;cursor:pointer;background:var(--bor);border:none;border-radius:4px;color:var(--tx)}
.warn-box{padding:10px 14px;background:#2a1a0f;border-left:3px solid var(--wn);border-radius:4px;font-size:12px;margin:10px 0}
.ok-box{padding:10px 14px;background:#0d2010;border-left:3px solid var(--ok);border-radius:4px;font-size:12px;margin:10px 0}
</style><link rel="icon" type="image/x-icon" href="/favicon.ico"></head><body>

<div class="header">
  <span style="font-size:28px">🎙</span>
  <div style="display:flex;align-items:center;gap:12px">
    <img src="/static/signalscope_logo.png" alt="SignalScope" style="height:46px;width:auto;display:block">
    <div><h1>SignalScope</h1><div class="sub">Setup Wizard — {{build}}</div></div>
  </div>
</div>

<div class="layout">
<nav class="steps">
  <button class="step-btn active" id="sb-0" onclick="goStep(0)"><span class="step-icon">1</span>Welcome</button>
  <button class="step-btn"        id="sb-1" onclick="goStep(1)"><span class="step-icon">2</span>Dependencies</button>
  <button class="step-btn"        id="sb-2" onclick="goStep(2)"><span class="step-icon">3</span>SDR Setup</button>
  <button class="step-btn"        id="sb-3" onclick="goStep(3)"><span class="step-icon">4</span>Instance Config</button>
  <button class="step-btn"        id="sb-4" onclick="goStep(4)"><span class="step-icon">5</span>Security</button>
  <button class="step-btn"        id="sb-5" onclick="goStep(5)"><span class="step-icon">6</span>Done</button>
</nav>

<div class="content">

<!-- ── Step 0: Welcome ─────────────────────────────────────────────────────── -->
<div class="pn active" id="pn-0">
  <h2>Welcome to SignalScope</h2>
  <div class="sub-h">This wizard will help you get set up in a few minutes. You can skip any step and change everything later in Settings.</div>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:24px">
    <div style="padding:14px;background:var(--sur);border:1px solid var(--bor);border-radius:8px">
      <div style="font-size:20px;margin-bottom:6px">📡</div>
      <div style="font-weight:600;font-size:13px;margin-bottom:4px">Stream Monitoring</div>
      <div style="font-size:12px;color:var(--mu)">Livewire, AES67, DAB, FM, and HTTP audio streams with silence, clip, and AI anomaly detection</div>
    </div>
    <div style="padding:14px;background:var(--sur);border:1px solid var(--bor);border-radius:8px">
      <div style="font-size:20px;margin-bottom:6px">🛰</div>
      <div style="font-weight:600;font-size:13px;margin-bottom:4px">Multi-Site Hub</div>
      <div style="font-size:12px;color:var(--mu)">Central dashboard aggregating status from remote client sites with encrypted heartbeats</div>
    </div>
    <div style="padding:14px;background:var(--sur);border:1px solid var(--bor);border-radius:8px">
      <div style="font-size:20px;margin-bottom:6px">📻</div>
      <div style="font-weight:600;font-size:13px;margin-bottom:4px">RTL-SDR (DAB &amp; FM)</div>
      <div style="font-size:12px;color:var(--mu)">Monitor DAB multiplexes and FM stations with signal strength, SNR, stereo and RDS decoding</div>
    </div>
    <div style="padding:14px;background:var(--sur);border:1px solid var(--bor);border-radius:8px">
      <div style="font-size:20px;margin-bottom:6px">🔔</div>
      <div style="font-weight:600;font-size:13px;margin-bottom:4px">Alerts</div>
      <div style="font-size:12px;color:var(--mu)">Email, Pushover, MS Teams and Slack webhooks with per-channel routing rules</div>
    </div>
  </div>

  <div class="act">
    <button class="btn bp" onclick="goStep(1)">Get Started →</button>
    <a href="/" class="btn bg">Skip wizard</a>
  </div>
</div>

<!-- ── Step 1: Dependencies ────────────────────────────────────────────────── -->
<div class="pn" id="pn-1">
  <h2>Dependencies</h2>
  <div class="sub-h">Checking what's installed. Core dependencies are required; SDR and HTTPS tools are optional.</div>

  <div id="dep-list">
    <div class="check-row"><span class="check-icon">⏳</span><div class="check-label"><div class="check-name">Checking…</div></div></div>
  </div>

  <div id="dep-summary" style="margin-top:16px;display:none"></div>

  <div class="act">
    <button class="btn bg" onclick="goStep(0)">← Back</button>
    <button class="btn bp" id="dep-next" onclick="goStep(2)">Next →</button>
  </div>
</div>

<!-- ── Step 2: SDR Setup ───────────────────────────────────────────────────── -->
<div class="pn" id="pn-2">
  <h2>SDR Device Setup</h2>
  <div class="sub-h">If you're using RTL-SDR dongles for DAB or FM monitoring, follow these steps. Skip this step if you're only monitoring Livewire, RTP, or HTTP streams.</div>

  <div id="sdr-steps">

    <details open style="margin-bottom:12px">
      <summary style="cursor:pointer;font-weight:600;padding:10px 0;border-bottom:1px solid var(--bor)">
        1. Blacklist kernel driver (Linux only)
      </summary>
      <div style="padding:12px 0">
        <p style="font-size:13px;margin-bottom:8px">The default Linux kernel driver (<code>dvb_usb_rtl28xxu</code>) conflicts with rtl-sdr. Check and blacklist it:</p>
        <div class="cmd-block">lsmod | grep dvb_usb_rtl28xxu<button class="copy-btn" onclick="copyCmd(this)">Copy</button></div>
        <p style="font-size:12px;color:var(--mu);margin:4px 0 8px">If the above shows output, the driver is loaded. Run the following to blacklist it:</p>
        <div class="cmd-block">echo 'blacklist dvb_usb_rtl28xxu' | sudo tee /etc/modprobe.d/rtlsdr.conf
sudo modprobe -r dvb_usb_rtl28xxu
sudo update-initramfs -u<button class="copy-btn" onclick="copyCmd(this)">Copy</button></div>
        <div class="warn-box">⚠ Replug all RTL-SDR dongles after blacklisting the driver.</div>
      </div>
    </details>

    <details style="margin-bottom:12px">
      <summary style="cursor:pointer;font-weight:600;padding:10px 0;border-bottom:1px solid var(--bor)">
        2. Detect connected dongles
      </summary>
      <div style="padding:12px 0">
        <div class="cmd-block">rtl_test -t<button class="copy-btn" onclick="copyCmd(this)">Copy</button></div>
        <p style="font-size:12px;color:var(--mu);margin:8px 0">Expected output:</p>
        <div class="cmd-block" style="color:#64748b">Found 2 device(s):
  0:  Realtek, RTL2838UHIDIR, SN: DAB_DONGLE_1
  1:  Realtek, RTL2838UHIDIR, SN: FM_DONGLE_1</div>
        <div style="margin-top:10px;display:flex;gap:8px;align-items:center">
          <button class="btn bg" style="font-size:12px" onclick="runSdrScan()">🔍 Scan now</button>
          <span id="sdr-detect-result" style="font-size:12px;color:var(--mu)"></span>
        </div>
        <div id="sdr-detected-list" style="margin-top:10px"></div>
      </div>
    </details>

    <details style="margin-bottom:12px">
      <summary style="cursor:pointer;font-weight:600;padding:10px 0;border-bottom:1px solid var(--bor)">
        3. Program serial numbers (recommended)
      </summary>
      <div style="padding:12px 0">
        <p style="font-size:13px;margin-bottom:8px">Program a persistent serial number so the app can find each dongle regardless of which USB port it's in. Plug in <em>one dongle at a time</em> for each command:</p>
        <div class="cmd-block">rtl_eeprom -d 0 -s "DAB_DONGLE_1"<button class="copy-btn" onclick="copyCmd(this)">Copy</button></div>
        <div class="cmd-block">rtl_eeprom -d 0 -s "FM_DONGLE_1"<button class="copy-btn" onclick="copyCmd(this)">Copy</button></div>
        <div class="warn-box">⚠ Unplug and replug each dongle after writing the serial number.</div>
      </div>
    </details>

    <details style="margin-bottom:12px">
      <summary style="cursor:pointer;font-weight:600;padding:10px 0;border-bottom:1px solid var(--bor)">
        4. Measure PPM correction
      </summary>
      <div style="padding:12px 0">
        <p style="font-size:13px;margin-bottom:8px">Cheap RTL-SDR dongles have a small frequency offset. Measure it for accurate tuning (run for at least 3 minutes):</p>
        <div class="cmd-block">rtl_test -p<button class="copy-btn" onclick="copyCmd(this)">Copy</button></div>
        <p style="font-size:12px;color:var(--mu);margin-top:8px">Note the cumulative PPM value and enter it when registering the dongle in Settings → Hub &amp; Network → SDR Devices.</p>
      </div>
    </details>

    <details style="margin-bottom:12px">
      <summary style="cursor:pointer;font-weight:600;padding:10px 0;border-bottom:1px solid var(--bor)">
        5. Verify welle-cli (DAB only)
      </summary>
      <div style="padding:12px 0">
        <p style="font-size:13px;margin-bottom:8px">Test that welle-cli can tune to a DAB multiplex and find services:</p>
        <div class="cmd-block">welle-cli -v<button class="copy-btn" onclick="copyCmd(this)">Copy</button></div>
        <div class="cmd-block"># Tune to 5C (BBC National) and list services — Ctrl+C after ~15s
welle-cli -c 5C -p 0 --http-port 7979 &
sleep 12 &amp;&amp; curl -s http://localhost:7979/api/services | python3 -m json.tool
kill %1<button class="copy-btn" onclick="copyCmd(this)">Copy</button></div>
        <div style="margin-top:10px;display:flex;gap:8px;align-items:center">
          <button class="btn bg" style="font-size:12px" onclick="verifyWelle()">▶ Verify welle-cli</button>
          <span id="welle-result" style="font-size:12px;color:var(--mu)"></span>
        </div>
      </div>
    </details>

  </div>

  <div class="act">
    <button class="btn bg" onclick="goStep(1)">← Back</button>
    <button class="btn bp" onclick="goStep(3)">Next →</button>
    <a href="#" onclick="goStep(3)" style="font-size:12px;color:var(--mu);text-decoration:none">Skip (no SDR dongles)</a>
  </div>
</div>

<!-- ── Step 3: Instance Config ─────────────────────────────────────────────── -->
<div class="pn" id="pn-3">
  <h2>Instance Configuration</h2>
  <div class="sub-h">Basic settings for this instance. You can change all of these later in Settings.</div>

  <form id="config-form">
  <input type="hidden" name="_csrf_token" value="{{csrf_token()}}">

  <label>Site Name
    <input type="text" name="site_name" value="{{cfg.hub.site_name}}"
           placeholder="e.g. Cool FM Belfast — shown on hub dashboard">
  </label>
  <p class="help">Identifies this instance on the multi-site hub dashboard.</p>

  <label style="margin-top:16px">Mode
    <select name="hub_mode" id="wiz_hub_mode" onchange="wizModeChanged()"
            style="width:100%;margin-top:4px;padding:9px 11px;background:#1e2433;border:1px solid var(--bor);border-radius:6px;color:var(--tx)">
      <option value="client" {{'selected' if cfg.hub.mode=='client'}}>Client — this site sends data to a central hub</option>
      <option value="hub"    {{'selected' if cfg.hub.mode=='hub'}}>Hub — this machine receives data from all sites</option>
      <option value="both"   {{'selected' if cfg.hub.mode=='both'}}>Both — client + hub on this machine</option>
    </select>
  </label>

  <div id="wiz_hub_url_wrap" style="display:none;margin-top:10px">
    <label>Hub URL
      <input type="text" name="hub_url" value="{{cfg.hub.hub_url}}"
             placeholder="https://hub.yourdomain.com  or  http://192.168.1.10">
    </label>
    <p class="help">Address of the hub server this client reports to.</p>
  </div>

  <label style="margin-top:16px">Shared Hub Secret
    <input type="password" name="hub_secret" value="{{cfg.hub.secret_key}}"
           placeholder="Minimum 16 characters — must match on hub and all clients">
  </label>
  <p class="help">Used for HMAC signing and payload encryption of hub heartbeats. Leave blank if not using the hub.</p>

  <script nonce="{{csp_nonce()}}">
  function wizModeChanged(){
    var el = document.getElementById('wiz_hub_mode');
    if(!el) return;
    document.getElementById('wiz_hub_url_wrap').style.display =
      (el.value==='client' || el.value==='both') ? '' : 'none';
  }
  wizModeChanged();
  </script>
  </form>

  <div class="act">
    <button class="btn bg" onclick="goStep(2)">← Back</button>
    <button class="btn bp" onclick="saveConfig()">Save &amp; Next →</button>
  </div>
</div>

<!-- ── Step 4: Security ────────────────────────────────────────────────────── -->
<div class="pn" id="pn-4">
  <h2>Security</h2>
  <div class="sub-h">Set up login credentials. Highly recommended if this instance is accessible from the network.</div>

  <form id="auth-form">
  <input type="hidden" name="_csrf_token" value="{{csrf_token()}}">

  <div class="cr" style="margin-top:0">
    <input type="checkbox" name="auth_enabled" value="1" id="wiz_auth_en"
           {{'checked' if cfg.auth.enabled}} onchange="wizAuthChanged()">
    <label style="margin:0;text-transform:none;font-weight:600" for="wiz_auth_en">Require login to access dashboard</label>
  </div>

  <div id="wiz_auth_fields" style="margin-top:14px">
    <label>Username
      <input type="text" name="auth_username" value="{{cfg.auth.username or 'admin'}}"
             autocomplete="username">
    </label>
    <label>Password
      <input type="password" name="auth_password" placeholder="Minimum 8 characters"
             autocomplete="new-password">
    </label>
    <label>Confirm Password
      <input type="password" name="auth_confirm" placeholder="Re-enter password"
             autocomplete="new-password">
    </label>
    <p class="help">Stored as PBKDF2-SHA256 (260,000 rounds). Leave password blank to keep current password.</p>
  </div>

  <script nonce="{{csp_nonce()}}">
  function wizAuthChanged(){
    var el = document.getElementById('wiz_auth_en');
    if(!el) return;
    document.getElementById('wiz_auth_fields').style.display =
      el.checked ? '' : 'none';
  }
  wizAuthChanged();
  </script>
  </form>

  <div class="act">
    <button class="btn bg" onclick="goStep(3)">← Back</button>
    <button class="btn bp" onclick="saveAuth()">Save &amp; Next →</button>
    <a href="#" onclick="goStep(5)" style="font-size:12px;color:var(--mu);text-decoration:none">Skip</a>
  </div>
</div>

<!-- ── Step 5: Done ────────────────────────────────────────────────────────── -->
<div class="pn" id="pn-5">
  <h2>You're all set! 🎉</h2>
  <div class="sub-h">SignalScope is ready to use. Here's what to do next:</div>

  <div style="display:flex;flex-direction:column;gap:10px;margin-bottom:28px">
    <div style="padding:14px;background:var(--sur);border:1px solid var(--bor);border-radius:8px;display:flex;gap:14px;align-items:flex-start">
      <span style="font-size:22px;flex-shrink:0">1️⃣</span>
      <div>
        <div style="font-weight:600;font-size:13px;margin-bottom:3px">Add your streams</div>
        <div style="font-size:12px;color:var(--mu)">Go to Inputs → Add Input. Enter a Livewire stream ID, RTP address, DAB service, FM frequency, or HTTP URL.</div>
      </div>
    </div>
    <div style="padding:14px;background:var(--sur);border:1px solid var(--bor);border-radius:8px;display:flex;gap:14px;align-items:flex-start">
      <span style="font-size:22px;flex-shrink:0">2️⃣</span>
      <div>
        <div style="font-weight:600;font-size:13px;margin-bottom:3px">Configure notifications</div>
        <div style="font-size:12px;color:var(--mu)">Settings → Notifications — set up email, Pushover, or webhook alerts. Use Test Notifications to verify.</div>
      </div>
    </div>
    <div style="padding:14px;background:var(--sur);border:1px solid var(--bor);border-radius:8px;display:flex;gap:14px;align-items:flex-start">
      <span style="font-size:22px;flex-shrink:0">3️⃣</span>
      <div>
        <div style="font-weight:600;font-size:13px;margin-bottom:3px">Start monitoring</div>
        <div style="font-size:12px;color:var(--mu)">Click ▶ Start on the dashboard. The AI needs 24 hours to learn each stream's normal baseline before anomaly alerts are active.</div>
      </div>
    </div>
    {% if cfg.hub.mode in ('hub','both') %}
    <div style="padding:14px;background:var(--sur);border:1px solid var(--bor);border-radius:8px;display:flex;gap:14px;align-items:flex-start">
      <span style="font-size:22px;flex-shrink:0">4️⃣</span>
      <div>
        <div style="font-weight:600;font-size:13px;margin-bottom:3px">Get an HTTPS certificate</div>
        <div style="font-size:12px;color:var(--mu)">Settings → Hub &amp; Network → HTTPS / Let's Encrypt. Enter your domain and click Get Certificate.</div>
      </div>
    </div>
    {% endif %}
  </div>

  <form method="post" action="/setup/complete">
    <input type="hidden" name="_csrf_token" value="{{csrf_token()}}">
    <div class="act">
      <button class="btn bp" type="submit" style="font-size:15px;padding:10px 24px">Go to Dashboard →</button>
    </div>
  </form>
</div>

</div><!-- content -->
</div><!-- layout -->

<script nonce="{{csp_nonce()}}">
var _currentStep = 0;

function _csrfFetch(url, opts){
  opts = opts || {};
  if(!opts.headers) opts.headers = {};
  var t = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
  opts.headers['X-CSRFToken'] = t;
  return fetch(url, opts);
}

function goStep(n){
  document.querySelectorAll('.pn').forEach(function(p){ p.classList.remove('active'); });
  document.querySelectorAll('.step-btn').forEach(function(b){ b.classList.remove('active'); });
  document.getElementById('pn-'+n).classList.add('active');
  document.getElementById('sb-'+n).classList.add('active');
  _currentStep = n;
  if(n===1) checkDeps();
}

// ── Dependency checker ────────────────────────────────────────────────────────
function checkDeps(){
  document.getElementById('dep-list').innerHTML =
    '<div class="check-row"><span class="check-icon">⏳</span>'
    +'<div class="check-label"><div class="check-name">Checking dependencies…</div></div></div>';
  _csrfFetch('/api/setup/deps').then(function(r){ return r.json(); }).then(function(d){
    renderDeps(d);
  }).catch(function(e){
    document.getElementById('dep-list').innerHTML =
      '<div class="check-row"><span class="check-icon">✗</span>'
      +'<div class="check-label"><div class="check-name">Check failed: '+e+'</div></div></div>';
  });
}

function renderDeps(d){
  var html = '';
  d.forEach(function(item){
    var icon  = item.status==='ok' ? '✅' : item.status==='warn' ? '⚠️' : item.optional ? '⬜' : '❌';
    var badge = item.status==='ok' ? '<span class="badge badge-ok">Installed</span>'
      : item.status==='warn' ? '<span class="badge badge-warn">'+item.version+'</span>'
      : item.optional ? '<span class="badge badge-na">Optional</span>'
      : '<span class="badge badge-err">Missing</span>';
    var action = '';
    if(item.status !== 'ok' && item.install){
      action = '<div class="cmd-block" style="margin:6px 0 0;font-size:11px">'+item.install
               +'<button class="copy-btn" onclick="copyCmd(this)">Copy</button></div>';
    }
    html += '<div class="check-row">'
      +'<span class="check-icon">'+icon+'</span>'
      +'<div class="check-label">'
        +'<div class="check-name">'+item.name+' '+badge+'</div>'
        +'<div class="check-desc">'+item.desc+'</div>'
        +(item.version && item.status==='ok' ? '<div style="font-size:11px;color:var(--mu);margin-top:2px">'+item.version+'</div>' : '')
        +action
      +'</div>'
    +'</div>';
  });
  document.getElementById('dep-list').innerHTML = html;

  var missing = d.filter(function(i){ return i.status==='err' && !i.optional; });
  var optional_missing = d.filter(function(i){ return i.status==='err' && i.optional; });
  var sumEl = document.getElementById('dep-summary');
  sumEl.style.display = '';
  if(!missing.length){
    sumEl.innerHTML = '<div class="ok-box">✓ All required dependencies are installed.'
      +(optional_missing.length ? ' '+optional_missing.length+' optional package(s) not installed.' : '')+'</div>';
  } else {
    sumEl.innerHTML = '<div class="warn-box">⚠ '+missing.length+' required dependency missing. '
      +'Install it then refresh this page.</div>';
  }
}

// ── SDR scanner ───────────────────────────────────────────────────────────────
function runSdrScan(){
  var el = document.getElementById('sdr-detect-result');
  var list = document.getElementById('sdr-detected-list');
  el.textContent = 'Scanning…'; el.style.color = 'var(--mu)';
  _csrfFetch('/api/sdr/scan').then(function(r){ return r.json(); }).then(function(d){
    if(!d.devices || !d.devices.length){
      el.textContent = 'No dongles found.'; el.style.color = 'var(--wn)';
      list.innerHTML = ''; return;
    }
    el.textContent = d.devices.length+' dongle(s) found.'; el.style.color = 'var(--ok)';
    var html = '';
    d.devices.forEach(function(dev){
      html += '<div class="check-row" style="margin-top:6px">'
        +'<span class="check-icon">📻</span>'
        +'<div class="check-label">'
          +'<div class="check-name">'+dev.serial+' <span class="badge badge-ok">Index '+dev.index+'</span></div>'
          +'<div class="check-desc">'+dev.manufacturer+' '+dev.name+'</div>'
        +'</div></div>';
    });
    list.innerHTML = html;
  }).catch(function(e){
    el.textContent = 'Scan failed: '+e; el.style.color = 'var(--al)';
  });
}

function verifyWelle(){
  var el = document.getElementById('welle-result');
  el.textContent = 'Checking…'; el.style.color = 'var(--mu)';
  _csrfFetch('/api/setup/verify-welle').then(function(r){ return r.json(); }).then(function(d){
    el.textContent = d.ok ? '✓ '+d.message : '✗ '+d.message;
    el.style.color = d.ok ? 'var(--ok)' : 'var(--al)';
  }).catch(function(e){
    el.textContent = '✗ '+e; el.style.color = 'var(--al)';
  });
}

// ── Config save ───────────────────────────────────────────────────────────────
function saveConfig(){
  var fd = new FormData(document.getElementById('config-form'));
  _csrfFetch('/api/setup/config', {method:'POST', body:fd}).then(function(r){ return r.json(); })
    .then(function(d){
      if(d.ok) goStep(4);
      else alert('Save failed: '+d.message);
    });
}

function saveAuth(){
  var fd = new FormData(document.getElementById('auth-form'));
  var pw  = fd.get('auth_password');
  var pw2 = fd.get('auth_confirm');
  if(pw && pw !== pw2){ alert('Passwords do not match.'); return; }
  if(pw && pw.length < 8){ alert('Password must be at least 8 characters.'); return; }
  _csrfFetch('/api/setup/auth', {method:'POST', body:fd}).then(function(r){ return r.json(); })
    .then(function(d){
      if(d.ok) goStep(5);
      else alert('Save failed: '+d.message);
    });
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function copyCmd(btn){
  var text = btn.parentElement.childNodes[0].textContent.trim();
  navigator.clipboard.writeText(text).then(function(){
    var orig = btn.textContent; btn.textContent = 'Copied!';
    setTimeout(function(){ btn.textContent = orig; }, 1500);
  });
}

</script>
</body></html>"""


INPUT_FORM_TPL = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{{title}}</title>
<style nonce="{{csp_nonce()}}">:root{--bg:#0d0f14;--sur:#161a22;--bor:#252b38;--acc:#4f9cf9;--tx:#e2e8f0;--mu:#64748b}
*{box-sizing:border-box;margin:0;padding:0}body{font-family:system-ui,sans-serif;background:var(--bg);color:var(--tx);font-size:14px}
header{background:var(--sur);border-bottom:1px solid var(--bor);padding:11px 20px;display:flex;align-items:center;gap:10px}
header h1{font-size:16px;font-weight:700}.btn{display:inline-block;padding:5px 12px;border-radius:6px;font-size:13px;cursor:pointer;border:none;text-decoration:none}
.bp{background:var(--acc);color:#fff}.bg{background:var(--bor);color:var(--tx)}.bd{background:var(--al);color:#fff}.bw{background:#7c2d12;color:#fca5a5}.bs{padding:3px 9px;font-size:12px}.nav-active{background:var(--acc)!important;color:#fff!important}.bd{background:var(--al);color:#fff}.bw{background:#7c2d12;color:#fca5a5}.bs{padding:3px 9px;font-size:12px}.nav-active{background:var(--acc)!important;color:#fff!important}
main{padding:22px;max-width:560px;margin:0 auto}
label{display:block;margin-top:13px;color:var(--mu);font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.05em}
input[type=text],input[type=number],select{width:100%;margin-top:4px;padding:8px 10px;background:#1e2433;border:1px solid var(--bor);border-radius:6px;color:var(--tx);font-size:14px}
.cr{display:flex;align-items:center;gap:8px;margin-top:10px}input[type=checkbox]{width:16px;height:16px;accent-color:var(--acc)}
.help{font-size:12px;color:var(--mu);margin-top:4px;line-height:1.5}
.sec{margin-top:18px;padding-top:13px;border-top:1px solid var(--bor);font-weight:600;font-size:14px}
.act{margin-top:20px;display:flex;gap:8px}</style><link rel="icon" type="image/x-icon" href="/favicon.ico"></head><body>
{{ topnav("inputs") }}
<div style="padding:8px 20px;background:var(--sur);border-bottom:1px solid var(--bor);display:flex;align-items:center">
  <span style="font-size:13px;font-weight:600">{{title}}</span>
  <a class="btn bg" style="margin-left:auto" href="/inputs">← Back</a>
</div>
<main><form method="post"><input type="hidden" name="_csrf_token" value="{{csrf_token()}}">
  <label>Name<input type="text" name="name" value="{{inp.name}}" required placeholder="e.g. TX Output"></label>
  <!-- Source type selector -->
  <label>Source Type
    <select id="src_type" onchange="srcTypeChanged()" style="width:100%;margin-top:4px;padding:8px 10px;background:#1e2433;border:1px solid var(--bor);border-radius:6px;color:var(--tx);font-size:14px">
      <option value="other"  id="opt_other">Livewire / RTP / HTTP</option>
      <option value="dab"    id="opt_dab">DAB (RTL-SDR via welle-cli)</option>
      <option value="fm"     id="opt_fm">FM (RTL-SDR)</option>
    </select>
  </label>

  <!-- Generic source address (Livewire / RTP / HTTP) -->
  <div id="src_other">
    <label>Stream ID, Address or URL
      <input type="text" id="device_index_other" placeholder="21811  or  239.192.85.19:5004  or  http://stream.example.com/live.mp3">
    </label>
    <p class="help">Livewire stream ID (auto-maps to multicast), raw IP:port, or HTTP/HTTPS URL. HTTP requires ffmpeg on PATH.</p>
  </div>

  <!-- DAB picker -->
  <div id="src_dab" style="display:none">
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px">
      <label>Channel
        <select id="dab_channel" style="width:100%;margin-top:4px;padding:8px 10px;background:#1e2433;border:1px solid var(--bor);border-radius:6px;color:var(--tx);font-size:13px">
          <option value="5A">5A (174.928 MHz)</option>
          <option value="5B">5B (177.008 MHz)</option>
          <option value="5C">5C (178.352 MHz)</option>
          <option value="5D">5D (179.200 MHz)</option>
          <option value="6A">6A (183.648 MHz)</option>
          <option value="6B">6B (185.360 MHz)</option>
          <option value="6C">6C (187.072 MHz)</option>
          <option value="6D">6D (188.928 MHz)</option>
          <option value="7A">7A (194.064 MHz)</option>
          <option value="7B">7B (195.776 MHz)</option>
          <option value="7C">7C (197.648 MHz)</option>
          <option value="7D">7D (199.360 MHz)</option>
          <option value="8A">8A (202.928 MHz)</option>
          <option value="8B">8B (204.640 MHz)</option>
          <option value="8C">8C (206.352 MHz)</option>
          <option value="8D">8D (208.064 MHz)</option>
          <option value="9A">9A (211.280 MHz)</option>
          <option value="9B">9B (213.360 MHz)</option>
          <option value="9C">9C (214.928 MHz)</option>
          <option value="9D">9D (216.928 MHz)</option>
          <option value="10A">10A (220.352 MHz)</option>
          <option value="10B">10B (222.064 MHz)</option>
          <option value="10C">10C (223.936 MHz)</option>
          <option value="10D">10D (225.648 MHz)</option>
          <option value="11A">11A (229.072 MHz)</option>
          <option value="11B">11B (230.784 MHz)</option>
          <option value="11C">11C (232.496 MHz)</option>
          <option value="11D">11D (234.208 MHz)</option>
          <option value="12A">12A (237.488 MHz)</option>
          <option value="12B">12B (239.200 MHz)</option>
          <option value="12C">12C (240.912 MHz)</option>
          <option value="12D">12D (242.624 MHz)</option>
        </select>
      </label>
      <label>Dongle (serial)
        <select id="dab_serial" style="width:100%;margin-top:4px;padding:8px 10px;background:#1e2433;border:1px solid var(--bor);border-radius:6px;color:var(--tx);font-size:13px">
          <option value="">Any available</option>
          {% for dev in sdr_devices %}
          {% if dev.role in ("dab","none") %}
          <option value="{{dev.serial}}">{{dev.label or dev.serial}} ({{dev.role}})</option>
          {% endif %}{% endfor %}
        </select>
      </label>
    </div>
    <div style="margin-top:10px;display:flex;align-items:center;gap:10px">
      <button type="button" class="btn bp" id="dab_scan_btn" onclick="dabScan()">🔍 Scan multiplex</button>
      <span id="dab_scan_status" style="font-size:12px;color:var(--mu)"></span>
    </div>
    <div id="dab_service_wrap" style="display:none;margin-top:10px">
      <label>Station
        <select id="dab_service" onchange="dabServiceSelected()" style="width:100%;margin-top:4px;padding:8px 10px;background:#1e2433;border:1px solid var(--bor);border-radius:6px;color:var(--tx);font-size:14px">
          <option value="">— Select station —</option>
        </select>
      </label>
      <div id="dab_service_info" style="margin-top:6px;font-size:12px;color:var(--mu)"></div>
    </div>
    <p class="help" style="margin-top:8px">Requires welle-cli and an RTL-SDR dongle. Register dongles in Settings → Hub &amp; Network → SDR Devices.</p>
  </div>

  <!-- FM source -->
  <div id="src_fm" style="display:none">
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:10px;margin-top:10px">
      <label>Frequency (MHz)
        <input type="number" id="fm_freq" step="0.1" min="76" max="108" placeholder="96.7" style="width:100%;margin-top:4px;padding:8px 10px;background:#1e2433;border:1px solid var(--bor);border-radius:6px;color:var(--tx);font-size:14px">
      </label>
      <label>Dongle (serial)
        <select id="fm_serial" style="width:100%;margin-top:4px;padding:8px 10px;background:#1e2433;border:1px solid var(--bor);border-radius:6px;color:var(--tx);font-size:13px">
          <option value="">Any available</option>
          {% for dev in sdr_devices %}
          {% if dev.role in ("fm","none") %}
          <option value="{{dev.serial}}">{{dev.label or dev.serial}} ({{dev.role}})</option>
          {% endif %}{% endfor %}
        </select>
      </label>
      <label>Gain (dB)
        <input type="number" id="fm_gain" step="0.1" min="0" max="49.6" placeholder="38.0" style="width:100%;margin-top:4px;padding:8px 10px;background:#1e2433;border:1px solid var(--bor);border-radius:6px;color:var(--tx);font-size:14px">
      </label>
      <label>Backend
        <select id="fm_backend" style="width:100%;margin-top:4px;padding:8px 10px;background:#1e2433;border:1px solid var(--bor);border-radius:6px;color:var(--tx);font-size:13px">
          <option value="auto">Auto (prefer rtl_fm)</option>
          <option value="rtl_fm">rtl_fm (recommended)</option>
          <option value="pyrtlsdr">pyrtlsdr</option>
        </select>
      </label>
    </div>
    <p class="help" style="margin-top:8px">FM backend can be selected per input. <code style="background:#1e2433;padding:1px 5px;border-radius:3px">rtl_fm</code> is recommended for stable audio. <code style="background:#1e2433;padding:1px 5px;border-radius:3px">pyrtlsdr</code> is available for development work on metrics/RDS. Leave gain blank for current automatic behaviour, or set a manual tuner gain such as 38&ndash;42 dB. Register dongles in Settings → Hub &amp; Network → SDR Devices.</p>
  </div>

  <!-- Hidden actual field submitted with the form -->
  <input type="hidden" name="device_index" id="device_index" value="{{inp.device_index}}">

  <script nonce="{{csp_nonce()}}">
  function _csrfFetch(url,opts){
    opts=opts||{};
    if(!opts.headers)opts.headers={};
    var t=(document.querySelector('meta[name="csrf-token"]') || {}).content||"";
    opts.headers["X-CSRFToken"]=t;
    return fetch(url,opts);
  }
  // Initialise source type from existing value
  (function(){
    var v = document.getElementById("device_index").value;
    var sel = document.getElementById("src_type");
    if(v.toLowerCase().startsWith("dab://")){
      sel.value = "dab";
      // Pre-fill channel if ?freq= is present
      var m = v.match(/\?freq=(\d+)/);
      // Pre-fill service name
      var svc = v.slice(6).split("?")[0].trim();
      if(svc){
        var opt = document.createElement("option");
        opt.value = svc; opt.textContent = svc; opt.selected = true;
        document.getElementById("dab_service").appendChild(opt);
        document.getElementById("dab_service_wrap").style.display = "";
      }
      // Extract serial if present
      var sm = v.match(/serial=([^&?]+)/);
      if(sm) document.getElementById("dab_serial").value = sm[1];
    } else if(v.toLowerCase().startsWith("fm://")){
      sel.value = "fm";
      var freq = v.slice(5).split("?")[0].trim();
      document.getElementById("fm_freq").value = freq;
      var sm2 = v.match(/serial=([^&?]+)/);
      if(sm2) document.getElementById("fm_serial").value = sm2[1];
      var gm2 = v.match(/gain=([^&?]+)/);
      if(gm2) document.getElementById("fm_gain").value = decodeURIComponent(gm2[1]);
      var bm2 = v.match(/backend=([^&?]+)/);
      if(bm2) document.getElementById("fm_backend").value = decodeURIComponent(bm2[1]);
    } else {
      sel.value = "other";
      document.getElementById("device_index_other").value = v;
    }
    srcTypeChanged();
  })();

  function srcTypeChanged(){
    var t = document.getElementById("src_type").value;
    document.getElementById("src_other").style.display = t==="other" ? "" : "none";
    document.getElementById("src_dab").style.display   = t==="dab"   ? "" : "none";
    document.getElementById("src_fm").style.display    = t==="fm"    ? "" : "none";
    updateHiddenField();
  }

  function updateHiddenField(){
    var t = document.getElementById("src_type").value;
    var val = "";
    if(t === "other"){
      val = document.getElementById("device_index_other").value.trim();
    } else if(t === "dab"){
      var svc = document.getElementById("dab_service").value.trim();
      if(svc){
        val = "dab://" + svc;
        var ch  = document.getElementById("dab_channel").value;
        var ser = document.getElementById("dab_serial").value.trim();
        var params = [];
        // Store channel NAME for reliable tuning (welle-cli uses names not freqs)
        var chOpt = document.getElementById("dab_channel");
        if(chOpt && chOpt.value){
          params.push("channel=" + chOpt.value);
          // Also store freq for reference
          var mf = chOpt.selectedOptions[0].textContent.match(/(\d+\.\d+)/);
          if(mf) params.push("freq=" + Math.round(parseFloat(mf[1]) * 1000));
        }
        if(ser) params.push("serial=" + ser);
        if(params.length) val += "?" + params.join("&");
      }
    } else if(t === "fm"){
      var freq = document.getElementById("fm_freq").value.trim();
      if(freq){
        val = "fm://" + freq;
        var ser2 = document.getElementById("fm_serial").value.trim();
        var gain2 = document.getElementById("fm_gain").value.trim();
        var backend2 = document.getElementById("fm_backend").value.trim();
        var params = [];
        if(ser2) params.push("serial=" + encodeURIComponent(ser2));
        if(gain2) params.push("gain=" + encodeURIComponent(gain2));
        if(backend2 && backend2 !== "auto") params.push("backend=" + encodeURIComponent(backend2));
        if(params.length) val += "?" + params.join("&");
      }
    }
    document.getElementById("device_index").value = val;
  }

  // Wire up live updates
  ["device_index_other","fm_freq","fm_serial","fm_gain","fm_backend","dab_serial","dab_channel"]
    .forEach(function(id){
      var el = document.getElementById(id);
      if(el) el.addEventListener("input", updateHiddenField);
      if(el) el.addEventListener("change", updateHiddenField);
    });

  function dabServiceSelected(){
    updateHiddenField();
    // Show service info
    var sel = document.getElementById("dab_service");
    var opt = sel.selectedOptions[0];
    var info = document.getElementById("dab_service_info");
    if(opt && opt.dataset.info) info.textContent = opt.dataset.info;
    else info.textContent = "";
  }

  function dabScan(){
    var ch  = document.getElementById("dab_channel").value;
    var ser = document.getElementById("dab_serial").value.trim();
    var btn = document.getElementById("dab_scan_btn");
    var st  = document.getElementById("dab_scan_status");
    var wrap= document.getElementById("dab_service_wrap");
    btn.disabled = true;
    st.style.color = "var(--mu)";
    st.textContent = "Scanning " + ch + "… (up to 60s, waiting for sync)";
    var url = "/api/dab/scan?channel=" + encodeURIComponent(ch);
    if(ser) url += "&serial=" + encodeURIComponent(ser);
    var controller = new AbortController();
    var fetchTimeout = setTimeout(function(){ controller.abort(); }, 75000);
    _csrfFetch(url, {signal: controller.signal}).then(function(r){
      clearTimeout(fetchTimeout); return r.json();
    }).then(function(d){
      btn.disabled = false;
      if(d.error){
        st.style.color = "var(--al)";
        var msg = "✗ " + d.error;
        if(d.debug_stderr && d.debug_stderr.length){
          msg += " | welle-cli: " + d.debug_stderr.slice(-3).join(" | ");
        }
        st.textContent = msg;
        return;
      }
      st.style.color = "var(--ok)";
      st.textContent = "✓ " + d.ensemble
        + " — SNR: " + (d.snr||0).toFixed(1) + " dB"
        + " — " + d.services.length + " service(s)";
      // Populate service dropdown
      var sel = document.getElementById("dab_service");
      var prev = sel.value;
      sel.innerHTML = '<option value="">— Select station —</option>';
      d.services.forEach(function(svc){
        var opt = document.createElement("option");
        opt.value = svc.name;
        opt.textContent = svc.name + (svc.bitrate ? "  (" + svc.bitrate + "kbps" + (svc.stereo?" stereo":"") + ")" : "");
        opt.dataset.info = (svc.bitrate ? svc.bitrate + "kbps" : "") + (svc.stereo ? "  Stereo" : "  Mono");
        if(svc.name === prev) opt.selected = true;
        sel.appendChild(opt);
      });
      wrap.style.display = "";
      updateHiddenField();
    }).catch(function(e){
      clearTimeout(fetchTimeout);
      btn.disabled = false;
      st.style.color = "var(--al)";
      st.textContent = e.name === "AbortError"
        ? "✗ Timed out — no response after 45s"
        : "✗ Request failed: " + e;
    });
  }
  </script>


  <div class="sec">Rule-based alerts</div>
  <div class="cr"><input type="checkbox" name="alert_on_silence" value="1" {{'checked' if inp.alert_on_silence}}><label style="margin:0;text-transform:none">Silence / low level</label></div>
  <div class="cr"><input type="checkbox" name="alert_on_hiss" value="1" {{'checked' if inp.alert_on_hiss}}><label style="margin:0;text-transform:none">Hiss / HF noise</label></div>
  <div class="cr"><input type="checkbox" name="alert_on_clip" value="1" {{'checked' if inp.alert_on_clip}}><label style="margin:0;text-transform:none">Clipping / over-mod</label></div>
  <label>Silence threshold (dBFS)<input type="number" name="silence_threshold_dbfs" value="{{inp.silence_threshold_dbfs}}" step="0.5"></label>
  <label>Silence min duration (s)<input type="number" name="silence_min_duration" value="{{inp.silence_min_duration}}" step="0.5" min="0.5"></label>
  <label>Clip threshold (dBFS)<input type="number" name="clip_threshold_dbfs" value="{{inp.clip_threshold_dbfs}}" step="0.5"></label>

  <div class="sec">🤖 Local AI (ONNX autoencoder)</div>
  <div class="cr"><input type="checkbox" name="ai_monitor" value="1" {{'checked' if inp.ai_monitor}}><label style="margin:0;text-transform:none">Enable AI monitoring</label></div>
  <p class="help">Learns what "normal" sounds like over 5 minutes. Detects dropouts, clipping, hiss, noise bursts, distortion, spectral shifts — entirely locally, no internet required.<br>Requires: <code>pip install onnxruntime</code></p>

  <div class="sec">Stream Comparison</div>
  <label>Compare role
    <select name="compare_role" style="width:100%;margin-top:4px;padding:8px 10px;background:#1e2433;border:1px solid var(--bor);border-radius:6px;color:var(--tx)">
      <option value="" {{'selected' if not inp.compare_role}}>— Not in a comparison pair —</option>
      <option value="pre"  {{'selected' if inp.compare_role=='pre'}}>Pre-processing (source)</option>
      <option value="post" {{'selected' if inp.compare_role=='post'}}>Post-processing (output)</option>
    </select>
  </label>
  <label>Compare peer (other stream in pair)
    <select name="compare_peer">
      <option value="">— None —</option>
      {% for n in all_names %}{% if n!=inp.name %}<option value="{{n}}" {{'selected' if inp.compare_peer==n}}>{{n}}</option>{% endif %}{% endfor %}
    </select>
  </label>
  <label style="margin-top:8px;display:block">Gain shift alert threshold (dB)
    <input type="number" name="compare_gain_alert_db" min="1" max="30" step="0.5"
           value="{{inp.compare_gain_alert_db if inp.compare_gain_alert_db is defined else 3.0}}"
           style="width:100%;margin-top:4px;padding:8px 10px;background:#1e2433;border:1px solid var(--bor);border-radius:6px;color:var(--tx)">
  </label>
  <p class="help">Set one stream as Pre, the other as Post, and select each other as the peer. The monitor will auto-align for delay up to {{cmp_search}}s. The gain shift threshold controls how much the output level is allowed to drift from its learned baseline before alerting — set this higher (e.g. 4&ndash;6 dB) for chains with dynamic loudness processing.</p>

  <div class="sec">Cascade suppression</div>
  <label>Suppress if this upstream stream is silent
    <select name="cascade_parent">
      <option value="">— None —</option>
      {% for n in all_names %}{% if n!=inp.name %}<option value="{{n}}" {{'selected' if inp.cascade_parent==n}}>{{n}}</option>{% endif %}{% endfor %}
    </select>
  </label>
  <div class="cr"><input type="checkbox" name="cascade_suppress_alerts" value="1" {{'checked' if inp.cascade_suppress_alerts}}><label style="margin:0;text-transform:none">Enable cascade suppression</label></div>

  <div class="sec">🎵 Now Playing (Planet Radio)</div>
  <label>Station
    <select name="nowplaying_station_id" id="np_select" style="width:100%;margin-top:4px;padding:8px 10px;background:#1e2433;border:1px solid var(--bor);border-radius:6px;color:var(--tx);font-size:14px">
      <option value="">— None —</option>
    </select>
  </label>
  <p class="help">Select a Planet Radio station to show Now Playing info on this stream's card.</p>
  <script nonce="{{csp_nonce()}}">
  (function(){
    fetch('/api/nowplaying_stations').then(r=>r.json()).then(function(stations){
      var sel = document.getElementById('np_select');
      var cur = '{{inp.nowplaying_station_id}}';
      stations.forEach(function(s){
        var opt = document.createElement('option');
        opt.value = s.rpuid; opt.textContent = s.name;
        if(s.rpuid === cur) opt.selected = true;
        sel.appendChild(opt);
      });
    }).catch(function(){});
  })();
  </script>

  <div class="act"><button class="btn bp" type="submit">Save</button><a class="btn bg" href="/inputs">Cancel</a></div>
</form></main></body></html>"""


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/")
@login_required
def index():
    _csrf_token()  # ensure the dashboard always has a persisted CSRF token
    # Redirect to setup wizard on first run
    if not monitor.app_cfg.wizard_done:
        return redirect(url_for("setup_wizard"))
    comparators_data = [
        {"pre_name": c.pre.name, "post_name": c.post.name, "status": c.status}
        for c in monitor._comparators
    ]
    hc  = monitor._hub_client
    hub_cfg = monitor.app_cfg.hub
    return render_template_string(MAIN_TPL, comparators=comparators_data, build=BUILD,
        running=monitor.is_running(), inputs=list(enumerate(monitor.app_cfg.inputs)),
        log_text="\n".join(monitor.get_logs(150)),
        now=time.time(), learn_dur=LEARN_DURATION_SECONDS,
        hub_client_enabled=bool(hub_cfg.hub_url and hub_cfg.mode in ("client","both")),
        hub_url=hub_cfg.hub_url)

@app.post("/start")
@login_required
@csrf_protect
def route_start():
    monitor.app_cfg=load_config(); monitor.start_monitoring()
    flash("Monitoring started."); return redirect(url_for("index"))

@app.post("/stop")
@login_required
@csrf_protect
def route_stop():
    monitor.stop_monitoring(); flash("Monitoring stopped."); return redirect(url_for("index"))

@app.get("/status.json")
@login_required
def status_json():
    ptp = monitor.ptp
    def inp_dict(i):
        return {
            "name":         i.name,
            "level_dbfs":   round(i._last_level_dbfs, 1),
            "ai_status":    i._ai_status,
            "ai_phase":     i._ai_phase,
            "livewire_mode":i._livewire_mode,
            "rtp_loss_pct": round(i._rtp_loss_pct, 2),
            "rtp_total":    i._rtp_total,
            "dab_snr":      round(i._dab_snr, 1),
            "dab_sig":      round(i._dab_sig, 1),
            "dab_ensemble": i._dab_ensemble,
            "dab_service":  i._dab_service,
            "dab_ok":       i._dab_ok,
            "fm_freq_mhz":  i._fm_freq_mhz,
            "fm_signal_dbm":round(i._fm_signal_dbm, 1),
            "fm_snr_db":    round(i._fm_snr_db, 1),
            "fm_stereo":    i._fm_stereo,
            "fm_rds_ps":    i._fm_rds_ps,
            "fm_rds_rt":    i._fm_rds_rt,
            "fm_rds_ok":    i._fm_rds_ok,
            "fm_rds_status": getattr(i, "_fm_rds_status", "No lock"),
            "fm_rds_valid": int(getattr(i, "_fm_rds_valid_groups", 0)),
            "fm_backend":   i._fm_backend,
            "sla_pct":      round(sla_pct(i), 3),
            "history":      i._history[-5:],
            "alert_on_silence": i.alert_on_silence,
            "alert_on_hiss":    i.alert_on_hiss,
            "alert_on_clip":    i.alert_on_clip,
            "ai_monitor":       i.ai_monitor,
            "device_index":     i.device_index,
        }
    def cmp_dict(c):
        return {
            "pre_name":     c.pre.name,
            "post_name":    c.post.name,
            "status":       c.status,
            "aligned":      c.aligned,
            "delay_ms":     round(c.delay_ms, 0),
            "correlation":  round(c.correlation, 3),
            "pre_dbfs":     round(c.pre_dbfs, 1),
            "post_dbfs":    round(c.post_dbfs, 1),
            "gain_diff_db": round(c.gain_diff_db, 1),
            "gain_alert_db": getattr(c.pre, "compare_gain_alert_db", 3.0),
            "cmp_history":  c.cmp_history[-8:],
        }

    hc = monitor._hub_client
    hub_status = {
        "enabled":    bool(monitor.app_cfg.hub.hub_url and
                          monitor.app_cfg.hub.mode in ("client","both")),
        "state":      hc.state      if hc else "disabled",
        "last_ack":   hc.last_ack   if hc else 0,
        "last_error": hc.last_error if hc else "",
        "sent":       hc.sent_total if hc else 0,
        "failed":     hc.fail_total if hc else 0,
        "queued":     len(hc._queue) if hc else 0,
        "hub_url":    monitor.app_cfg.hub.hub_url,
    }
    return jsonify({
        "running": monitor.is_running(),
        "logs":    monitor.get_logs(150),
        "inputs":  [inp_dict(i) for i in monitor.app_cfg.inputs],
        "comparators": [cmp_dict(c) for c in monitor._comparators],
        "hub":     hub_status,
        "ptp": {
            "state":     ptp.state     if ptp else "idle",
            "status":    ptp.status    if ptp else "—",
            "offset_us": ptp.offset_us if ptp else 0,
            "drift_us":  ptp.drift_us  if ptp else 0,
            "jitter_us": ptp.jitter_us if ptp else 0,
            "gm_id":     ptp.gm_id     if ptp else "",
            "domain":    ptp.domain    if ptp else 0,
            "last_sync": ptp.last_sync if ptp else 0,
            "history":   (ptp.history[-5:] if ptp else []),
        } if monitor.is_running() else {"state":"idle","status":"Not running","offset_us":0,"jitter_us":0,"gm_id":"","domain":0,"last_sync":0,"history":[]},
    })

@app.get("/inputs")
@login_required
def inputs_list():
    inp=monitor.app_cfg.inputs
    return render_template_string(INPUT_LIST_TPL,inputs=list(enumerate(inp)),
        model_exists=[os.path.exists(_model_path(i.name)) for i in inp])

def _inp_from_form(f):
    return InputConfig(
        name=f.get("name","").strip(), device_index=f.get("device_index","").strip(),
        enabled=True, alert_on_silence=bool(f.get("alert_on_silence")),
        alert_on_hiss=bool(f.get("alert_on_hiss")), alert_on_clip=bool(f.get("alert_on_clip")),
        ai_monitor=bool(f.get("ai_monitor")),
        silence_threshold_dbfs=float(f.get("silence_threshold_dbfs",-55.0)),
        silence_min_duration=float(f.get("silence_min_duration",3.0)),
        clip_threshold_dbfs=float(f.get("clip_threshold_dbfs",-3.0)),
        cascade_parent=f.get("cascade_parent") or None,
        cascade_suppress_alerts=bool(f.get("cascade_suppress_alerts")),
        compare_peer=f.get("compare_peer") or None,
        compare_role=f.get("compare_role",""),
        compare_gain_alert_db=float(f.get("compare_gain_alert_db") or 3.0),
        nowplaying_station_id=f.get("nowplaying_station_id",""),
    )

@app.route("/inputs/add", methods=["GET","POST"])
@login_required
@csrf_protect
def input_add():
    if request.method=="POST":
        inp=_inp_from_form(request.form); monitor.app_cfg.inputs.append(inp)
        save_config(monitor.app_cfg); flash(f"Added '{inp.name}'."); return redirect(url_for("inputs_list"))
    return render_template_string(INPUT_FORM_TPL, cmp_search=int(COMPARE_SEARCH_SECS),title="Add Input",
        sdr_devices=monitor.app_cfg.sdr_devices,
        inp=InputConfig(name="",device_index=""),all_names=[i.name for i in monitor.app_cfg.inputs])

@app.route("/inputs/<int:idx>/edit", methods=["GET","POST"])
@login_required
@csrf_protect
def input_edit(idx):
    inps=monitor.app_cfg.inputs
    if idx<0 or idx>=len(inps): flash("Invalid."); return redirect(url_for("inputs_list"))
    if request.method=="POST":
        inp=_inp_from_form(request.form); inps[idx]=inp; save_config(monitor.app_cfg)
        flash(f"Updated '{inp.name}'."); return redirect(url_for("inputs_list"))
    return render_template_string(INPUT_FORM_TPL, cmp_search=int(COMPARE_SEARCH_SECS),title="Edit Input",
        sdr_devices=monitor.app_cfg.sdr_devices,
        inp=inps[idx],all_names=[i.name for i in inps])

@app.post("/inputs/<int:idx>/delete")
@login_required
@csrf_protect
def input_delete(idx):
    inps=monitor.app_cfg.inputs
    if 0<=idx<len(inps):
        name=inps[idx].name; del inps[idx]; save_config(monitor.app_cfg); flash(f"Deleted '{name}'.")
    return redirect(url_for("inputs_list"))

@app.post("/ai/retrain/<int:idx>")
@login_required
@csrf_protect
def ai_retrain(idx):
    inps=monitor.app_cfg.inputs
    if 0<=idx<len(inps):
        name=inps[idx].name; monitor.request_retrain(name)
        if monitor.is_running():
            sender=AlertSender(monitor.app_cfg,monitor.log)
            ai=StreamAI(inps[idx],monitor.log,sender)
            monitor._stream_ais[name]=ai; ai.start()
            flash(f"Retraining started for '{name}'.")
        else:
            flash(f"Retrain flagged for '{name}' — starts on next monitoring start.")
    return redirect(url_for("index"))

@app.post("/settings/test-notify")
@login_required
@csrf_protect
def settings_test_notify():
    """Send a test notification on the requested channel."""
    channel = request.args.get("channel","email")
    cfg = monitor.app_cfg
    sender = AlertSender(cfg, monitor.log)
    subject = "SignalScope — Test Notification"
    body    = (f"This is a test notification from {cfg.hub.site_name or 'SignalScope'}. "
               f"If you received this, {channel} notifications are working correctly. "
               f"Sent at {time.strftime('%Y-%m-%d %H:%M:%S')}.")
    try:
        if channel == "email":
            if not cfg.email.enabled or not cfg.email.smtp_host:
                return jsonify({"ok":False,"message":"Email not configured"})
            sender._email(subject, body, None)
            return jsonify({"ok":True,"message":f"Test email sent to {cfg.email.to_addr}"})
        elif channel == "webhook":
            if not cfg.webhook.enabled and not cfg.webhook.routes:
                return jsonify({"ok":False,"message":"Webhook not configured"})
            sender._webhook(subject, body, alert_type="TEST", stream="test")
            return jsonify({"ok":True,"message":"Test webhook sent"})
        elif channel == "pushover":
            if not cfg.pushover.enabled or not cfg.pushover.user_key:
                return jsonify({"ok":False,"message":"Pushover not configured"})
            sender._pushover(subject, body)
            return jsonify({"ok":True,"message":"Test Pushover notification sent"})
        else:
            return jsonify({"ok":False,"message":f"Unknown channel: {channel}"})
    except Exception as e:
        return jsonify({"ok":False,"message":str(e)})


@app.route("/settings", methods=["GET","POST"])
@login_required
@csrf_protect
def settings():
    cfg=monitor.app_cfg
    if request.method=="POST":
        f=request.form
        cfg.email.enabled=bool(f.get("email_enabled")); cfg.email.smtp_host=f.get("smtp_host","").strip()
        cfg.email.smtp_port=int(f.get("smtp_port",587)); cfg.email.use_tls=bool(f.get("use_tls"))
        cfg.email.username=f.get("email_user","").strip(); cfg.email.password=f.get("email_pass","")
        cfg.email.from_addr=f.get("from_addr","").strip(); cfg.email.to_addr=f.get("to_addr","").strip()
        cfg.webhook.enabled     = bool(f.get("webhook_enabled"))
        cfg.webhook.url         = f.get("webhook_url","").strip()
        cfg.webhook.teams_style = bool(f.get("webhook_teams_style"))
        # Parse dynamic webhook routing rows
        routes = []
        i = 0
        while True:
            url = f.get(f"wr_url_{i}", "").strip()
            name = f.get(f"wr_name_{i}", "").strip()
            if url is None and name is None:
                break          # no more rows
            if url or name:    # skip completely empty rows
                streams_raw = f.get(f"wr_streams_{i}", "").strip()
                types_raw   = f.get(f"wr_types_{i}",   "").strip()
                sites_raw   = f.get(f"wr_sites_{i}",   "").strip()
                routes.append(WebhookRoute(
                    name     = name,
                    url      = url,
                    teams_style = f.get(f"wr_style_{i}","teams") == "teams",
                    filter_streams  = [s.strip() for s in streams_raw.split(",") if s.strip()],
                    filter_types    = [t.strip() for t in types_raw.split(",")   if t.strip()],
                    filter_sites    = [s.strip() for s in sites_raw.split(",")   if s.strip()],
                    filter_severity = f.get(f"wr_severity_{i}","").strip(),
                ))
            i += 1
            if i > 50: break   # safety cap
        cfg.webhook.routes = routes
        cfg.pushover.enabled=bool(f.get("pv_enabled"))
        cfg.pushover.user_key=f.get("pv_user_key","").strip()
        cfg.pushover.app_token=f.get("pv_app_token","").strip()
        cfg.pushover.priority_warn=int(f.get("pv_pri_warn",0))
        cfg.pushover.priority_alert=int(f.get("pv_pri_alert",1))
        cfg.network.audio_interface_ip=f.get("audio_ip","0.0.0.0").strip()
        cfg.network.management_interface_ip=f.get("mgmt_ip","0.0.0.0").strip()
        cfg.hub.enabled=bool(f.get("hub_enabled")); cfg.hub.mode=f.get("hub_mode","client")
        cfg.hub.site_name=f.get("hub_site_name","").strip()
        _raw_url = f.get("hub_url","").strip()
        cfg.hub.hub_url = HubClient._normalise_url(_raw_url) if _raw_url else ""
        cfg.hub.secret_key=f.get("hub_secret","").strip()
        # Auth
        cfg.auth.enabled  = bool(f.get("auth_enabled"))
        cfg.auth.username = f.get("auth_username","admin").strip() or "admin"
        new_pw = f.get("auth_password","").strip()
        if new_pw:
            if len(new_pw) < 8:
                flash("Password must be at least 8 characters — not saved.")
            else:
                cfg.auth.password_hash = _hash_password(new_pw)
                cfg.auth.first_login   = False
        # SDR devices
        serials = f.getlist("sdr_serial")
        roles   = f.getlist("sdr_role")
        ppms    = f.getlist("sdr_ppm")
        labels  = f.getlist("sdr_label")
        cfg.sdr_devices = []
        for serial, role, ppm, label in zip(serials, roles, ppms, labels):
            serial = serial.strip()
            if serial:  # skip blank rows
                try: ppm_int = int(ppm)
                except: ppm_int = 0
                cfg.sdr_devices.append(SdrDevice(
                    serial=serial, role=role,
                    ppm=ppm_int, label=label.strip()))
        # PTP thresholds
        try: cfg.ptp_offset_warn_us  = max(100, int(f.get("ptp_offset_warn_us",  5000)))
        except: pass
        try: cfg.ptp_offset_alert_us = max(100, int(f.get("ptp_offset_alert_us", 50000)))
        except: pass
        try: cfg.ptp_jitter_warn_us  = max(100, int(f.get("ptp_jitter_warn_us",  2000)))
        except: pass
        try: cfg.ptp_jitter_alert_us = max(100, int(f.get("ptp_jitter_alert_us", 10000)))
        except: pass
        # Login security
        try: cfg.login_max_attempts = max(3,   int(f.get("login_max_attempts", 10)))
        except: pass
        try: cfg.login_lockout_mins = max(1,   int(f.get("login_lockout_mins", 15)))
        except: pass
        try: cfg.session_timeout_hrs= max(1,   int(f.get("session_timeout_hrs", 12)))
        except: pass
        # SLA + nowplaying
        try: cfg.sla_target_pct = float(f.get("sla_target", 99.9))
        except: pass
        cfg.nowplaying_country = f.get("nowplaying_country","GB").strip() or "GB"
        # Daily report
        rt = f.get("daily_report_time","06:00").strip()
        import re as _re
        if _re.match(r"^[0-2][0-9]:[0-5][0-9]$", rt): cfg.daily_report_time = rt
        # Maintenance
        try: cfg.alert_log_max = max(0, int(f.get("alert_log_max", 10000)))
        except: pass
        try: cfg.clip_max_age_days = max(0, int(f.get("clip_max_age_days", 30)))
        except: pass
        try: cfg.clip_max_per_stream = max(0, int(f.get("clip_max_per_stream", 200)))
        except: pass
        save_config(cfg); flash("Settings saved."); return redirect(url_for("settings"))
    return render_template_string(SETTINGS_TPL, cfg=cfg,
        acme_running=(acme_client.status=="running"))

@app.get("/stream/<int:idx>/audio.wav")
@login_required
def stream_audio(idx):
    inps=monitor.app_cfg.inputs
    if idx<0 or idx>=len(inps): return "Not found",404
    inp=inps[idx]
    if not inp._stream_buffer: return "No data — stream not yet receiving audio",503
    chunks=list(inp._stream_buffer)
    if not chunks: return "No data — buffer empty",503

    # ?seconds=N controls how much audio to return (max 20s = buffer size)
    try: secs = min(float(request.args.get("seconds", 10)), STREAM_BUFFER_SECONDS)
    except: secs = 10.0
    secs = max(1.0, secs)

    audio = np.concatenate(chunks)[-int(SAMPLE_RATE * secs):]
    safe  = _safe_name(inp.name)
    ts    = time.strftime("%Y%m%d-%H%M%S")
    fname = f"{safe}_{ts}_{int(secs)}s.wav"

    return Response(
        _make_wav_bytes(audio.astype(np.float32)),
        mimetype="audio/wav",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )

# ─── Live audio stream (chunked WAV for browser <audio> tag) ─────────────────

@app.get("/stream/<int:idx>/live")
@login_required
def stream_live(idx):
    """
    Streams audio as a continuous MP3 response via a local ffmpeg encoder.
    MP3 is the only format all browsers play reliably as a live stream.
    The ffmpeg process reads raw PCM s16le from a pipe and outputs MP3 frames.
    """
    import subprocess, shutil
    inps = monitor.app_cfg.inputs
    if idx < 0 or idx >= len(inps):
        return "Not found", 404
    inp = inps[idx]

    # Fallback to WAV streaming if ffmpeg isn't available
    has_ffmpeg = _find_binary("ffmpeg") is not None

    if not has_ffmpeg:
        # WAV fallback (works in most browsers on a LAN, may stall in Chrome)
        def gen_wav():
            import struct as _struct, collections as _collections
            sr = SAMPLE_RATE; ch = 1; bps = 2
            yield (
                b'RIFF' + _struct.pack('<I', 0xFFFFFFFF) +
                b'WAVE' +
                b'fmt ' + _struct.pack('<IHHIIHH', 16, 1, ch, sr, sr*ch*bps, ch*bps, 16) +
                b'data' + _struct.pack('<I', 0xFFFFFFFF)
            )
            def _pcm(c): return (np.clip(c,-1.,1.)*32767).astype(np.int16).tobytes()

            prefill_n = max(2, int(LIVE_PLAYOUT_BUFFER_SECS / CHUNK_DURATION))
            q = _collections.deque()
            if inp._stream_buffer:
                seed_n = max(prefill_n, int(3.0 / CHUNK_DURATION))
                for c in list(inp._stream_buffer)[-seed_n:]:
                    q.append(c.copy())

            seen_seq = getattr(inp, '_live_chunk_seq', 0)
            next_send = time.monotonic()

            while True:
                cur = getattr(inp, '_live_chunk_seq', 0)
                if cur > seen_seq and inp._stream_buffer:
                    n = min(cur - seen_seq, 50)
                    for c in list(inp._stream_buffer)[-n:]:
                        q.append(c.copy())
                    seen_seq = cur

                if len(q) < prefill_n:
                    time.sleep(0.02)
                    next_send = time.monotonic() + 0.01
                    continue

                now = time.monotonic()
                if now < next_send:
                    time.sleep(min(0.02, next_send - now))
                    continue

                if q:
                    yield _pcm(q.popleft())
                    next_send += CHUNK_DURATION
                else:
                    time.sleep(0.02)
        return Response(gen_wav(), mimetype="audio/wav",
            headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

    def generate_mp3():
        sr = SAMPLE_RATE
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "s16le", "-ar", str(sr), "-ac", "1", "-i", "pipe:0",
            "-f", "mp3", "-b:a", "128k", "-reservoir", "0", "pipe:1",
        ]
        proc = subprocess.Popen(cmd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, bufsize=0)

        def _pcm_raw(c):
            return (np.clip(c, -1., 1.) * 32767).astype(np.int16).tobytes()

        def writer():
            import collections as _collections
            try:
                prefill_n = max(2, int(LIVE_PLAYOUT_BUFFER_SECS / CHUNK_DURATION))
                q = _collections.deque()

                # seed backlog so browser decoder starts immediately and playback stays
                # slightly behind the live edge, masking bursty packet arrival timing.
                if inp._stream_buffer:
                    seed_n = max(prefill_n, int(3.0 / CHUNK_DURATION))
                    for c in list(inp._stream_buffer)[-seed_n:]:
                        q.append(c.copy())

                seen_seq = getattr(inp, '_live_chunk_seq', 0)
                next_send = time.monotonic()

                while True:
                    cur = getattr(inp, '_live_chunk_seq', 0)
                    if cur > seen_seq and inp._stream_buffer:
                        n = min(cur - seen_seq, 50)
                        for c in list(inp._stream_buffer)[-n:]:
                            q.append(c.copy())
                        seen_seq = cur

                    if len(q) < prefill_n:
                        time.sleep(0.02)
                        next_send = time.monotonic() + 0.01
                        continue

                    now = time.monotonic()
                    if now < next_send:
                        time.sleep(min(0.02, next_send - now))
                        continue

                    if q:
                        proc.stdin.write(_pcm_raw(q.popleft()))
                        next_send += CHUNK_DURATION
                    else:
                        time.sleep(0.02)
            except Exception:
                pass
            finally:
                try: proc.stdin.close()
                except: pass

        import threading as _t
        wt = _t.Thread(target=writer, daemon=True)
        wt.start()

        try:
            while True:
                data = proc.stdout.read(4096)
                if not data:
                    break
                yield data
        finally:
            try: proc.kill(); proc.wait()
            except: pass

    return Response(
        generate_mp3(),
        mimetype="audio/mpeg",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
            "icy-name": inp.name,
        },
    )

@app.get("/clips/<path:stream_name>")
@login_required
def clips_list(stream_name):
    """Return JSON list of saved alert clips for a stream."""
    snip_dir = os.path.join(BASE_DIR, "alert_snippets")
    if not os.path.exists(snip_dir):
        return jsonify([])
    safe = _safe_name(stream_name)
    files = []
    for fn in sorted(os.listdir(snip_dir), reverse=True):
        if not fn.endswith(".wav"): continue
        if safe.lower() not in fn.lower(): continue
        path = os.path.join(snip_dir, fn)
        # Parse filename: YYYYMMDD-HHMMSS_StreamName_alerttype.wav
        parts = fn.replace(".wav","").split("_")
        ts_str = parts[0] if parts else ""
        alert_type = parts[-1] if len(parts) > 1 else "unknown"
        try:
            ts = time.mktime(time.strptime(ts_str, "%Y%m%d-%H%M%S"))
            ts_fmt = time.strftime("%d %b %Y %H:%M:%S", time.localtime(ts))
        except:
            ts_fmt = ts_str
        size_kb = os.path.getsize(path) // 1024
        files.append({
            "filename": fn,
            "ts": ts_fmt,
            "alert_type": alert_type.replace("_"," ").upper(),
            "size_kb": size_kb,
        })
    return jsonify(files)

@app.get("/clips/<path:stream_name>/<filename>")
@login_required
def clips_serve(stream_name, filename):
    """Serve a saved alert clip WAV file."""
    snip_dir = os.path.join(BASE_DIR, "alert_snippets")
    path = os.path.join(snip_dir, filename)
    # Security: stay within snip_dir
    if not os.path.abspath(path).startswith(os.path.abspath(snip_dir)):
        return "Forbidden", 403
    if not os.path.exists(path):
        return "Not found", 404
    with open(path, "rb") as f:
        data = f.read()
    return Response(data, mimetype="audio/wav",
        headers={"Content-Disposition": f'inline; filename="{filename}"'})

@app.delete("/clips/<path:stream_name>/<filename>")
@login_required
def clips_delete(stream_name, filename):
    """Delete a saved alert clip."""
    snip_dir = os.path.join(BASE_DIR, "alert_snippets")
    path = os.path.join(snip_dir, filename)
    if not os.path.abspath(path).startswith(os.path.abspath(snip_dir)):
        return jsonify({"error":"forbidden"}), 403
    try:
        os.remove(path)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─── Auth routes ─────────────────────────────────────────────────────────────

LOGIN_TPL = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Login — SignalScope</title>
<style nonce="{{csp_nonce()}}">:root{--bg:#0d0f14;--sur:#161a22;--bor:#252b38;--acc:#4f9cf9;--tx:#e2e8f0;--mu:#64748b}
*{box-sizing:border-box;margin:0;padding:0}body{font-family:system-ui,sans-serif;background:var(--bg);color:var(--tx);font-size:14px;display:flex;align-items:center;justify-content:center;min-height:100vh}
.box{background:var(--sur);border:1px solid var(--bor);border-radius:12px;padding:32px;width:100%;max-width:380px}
h1{font-size:20px;margin-bottom:6px;text-align:center}
.sub{color:var(--mu);font-size:13px;text-align:center;margin-bottom:24px}
label{display:block;margin-top:14px;color:var(--mu);font-size:12px;font-weight:600;text-transform:uppercase}
input[type=text],input[type=password]{width:100%;margin-top:4px;padding:9px 11px;background:#1e2433;border:1px solid var(--bor);border-radius:7px;color:var(--tx);font-size:14px}
.btn{width:100%;margin-top:20px;padding:10px;background:var(--acc);color:#fff;border:none;border-radius:7px;font-size:14px;font-weight:600;cursor:pointer}
.err{margin-top:12px;padding:9px 12px;background:#3a0f0f;border-left:3px solid #ef4444;border-radius:5px;font-size:13px;color:#fca5a5}
.warn{margin-top:12px;padding:9px 12px;background:#2a1a0f;border-left:3px solid #f59e0b;border-radius:5px;font-size:13px;color:#fde68a}
.ok{margin-top:12px;padding:9px 12px;background:#1a2a0f;border-left:3px solid #22c55e;border-radius:5px;font-size:13px;color:#86efac}
</style><link rel="icon" type="image/x-icon" href="/favicon.ico"></head><body>
<div class="box">
  <div style="text-align:center;margin-bottom:10px"><img src="/static/signalscope_logo.png" alt="SignalScope" style="max-width:220px;width:100%;height:auto"></div>
  <h1 style="display:none">SignalScope</h1>
  <div class="sub">{{build}}</div>
  {% if locked %}
  <div class="warn">🔒 Too many failed attempts.<br>Access locked — try again in {{locked_mins}} minute{{'' if locked_mins==1 else 's'}}.</div>
  {% else %}
  {% if error %}<div class="err">⚠ {{error}}</div>{% endif %}
  {% if first_login %}<div class="ok">First login — please set a new password below.</div>{% endif %}
  <form method="post" autocomplete="on"><input type="hidden" name="_csrf_token" value="{{csrf_token()}}">
    <label>Username<input type="text" name="username" value="{{username or ''}}" autofocus autocomplete="username"></label>
    <label>Password<input type="password" name="password" autocomplete="current-password"></label>
    {% if first_login %}
    <label>New Password<input type="password" name="new_password" autocomplete="new-password" placeholder="Minimum 8 characters"></label>
    <label>Confirm Password<input type="password" name="confirm_password" autocomplete="new-password"></label>
    {% endif %}
    <button class="btn" type="submit">{% if first_login %}Set Password & Sign In{% else %}Sign In{% endif %}</button>
  </form>
  {% endif %}
</div></body></html>"""

@app.get("/.well-known/acme-challenge/<token>")
def acme_challenge(token):
    """Serve ACME HTTP-01 challenge tokens. Must be accessible on port 80."""
    key_auth = _acme_challenges.get(token)
    if key_auth:
        return key_auth, 200, {"Content-Type": "text/plain"}
    return "Not found", 404


@app.post("/settings/acme-clear")
@login_required
@csrf_protect
def settings_acme_clear():
    """Clear cached ACME account key to force fresh issuance."""
    acme_client.clear_state()
    return jsonify({"ok": True, "message": "State cleared. Click Get Certificate to start fresh."})


@app.post("/settings/acme-issue")
@login_required
@csrf_protect
def settings_acme_issue():
    """Start Let's Encrypt certificate issuance."""
    cfg    = monitor.app_cfg
    domain = request.form.get("tls_domain","").strip()
    email  = request.form.get("tls_email","").strip()
    staging= request.form.get("staging","") == "1"
    if not domain:
        return jsonify({"ok":False,"message":"Domain name required"})
    if cfg.hub.mode not in ("hub","both"):
        return jsonify({"ok":False,"message":"Let's Encrypt is only available on hub instances"})
    acme_client.staging = staging
    acme_client.start_issue_thread(domain, email)
    return jsonify({"ok":True,"message":f"Certificate issuance started for {domain}"})


@app.get("/settings/acme-status")
@login_required
def settings_acme_status():
    """Return current ACME status and log."""
    cfg = monitor.app_cfg
    cert_info = {}
    if cfg.tls_cert_path and os.path.exists(cfg.tls_cert_path):
        try:
            from cryptography import x509 as _x509
            with open(cfg.tls_cert_path,"rb") as f:
                cert = _x509.load_pem_x509_certificate(f.read())
            expires = cert.not_valid_after_utc.replace(tzinfo=None)
            cert_info = {
                "domain":  cfg.tls_domain,
                "expires": expires.strftime("%Y-%m-%d"),
                "days":    (expires - datetime.datetime.utcnow()).days,
            }
        except Exception as e:
            cert_info = {"error": str(e)}
    return jsonify({
        "status":    acme_client.status,
        "log":       ACME_LOG[-30:],
        "cert":      cert_info,
        "enabled":   cfg.tls_enabled,
        "domain":    cfg.tls_domain,
    })


@app.post("/settings/tls-toggle")
@login_required
@csrf_protect
def settings_tls_toggle():
    """Enable or disable HTTPS. Requires restart to take effect."""
    cfg = monitor.app_cfg
    enable = request.form.get("enable","") == "1"
    if enable and (not cfg.tls_cert_path or not os.path.exists(cfg.tls_cert_path)):
        return jsonify({"ok":False,"message":"No certificate found. Issue one first."})
    cfg.tls_enabled = enable
    save_config(cfg)
    state = "enabled" if enable else "disabled"
    return jsonify({"ok":True,"message":f"HTTPS {state}. Restart the app to apply."})


@app.get("/api/sdr/scan")
def api_sdr_scan():
    """Scan for connected RTL-SDR dongles and return their details."""
    devices = sdr_manager.scan(force=True)
    claimed  = sdr_manager.status()
    cfg      = monitor.app_cfg
    reg_serials = {d.serial for d in cfg.sdr_devices}
    result = []
    for d in devices:
        result.append({
            "index":        d["index"],
            "serial":       d["serial"],
            "name":         d["name"],
            "manufacturer": d["manufacturer"],
            "registered":   d["serial"] in reg_serials,
            "in_use":       claimed.get(d["serial"]),
            "role":         next((r.role for r in cfg.sdr_devices
                                  if r.serial == d["serial"]), "none"),
        })
    return jsonify({
        "devices":    result,
        "rtl_test":   bool(__import__("shutil").which("rtl_test")),
        "welle_cli":  bool(__import__("shutil").which("welle-cli")),
    })



@app.get("/api/dab/test")
@login_required
def api_dab_test():
    """Diagnostic: run welle-cli for 5 seconds and return all output.
    Helps identify correct flags and whether the HTTP API starts."""
    import shutil, subprocess as _sp
    channel = request.args.get("channel", "5C").strip().upper()
    device  = request.args.get("device", "0").strip()
    ppm     = request.args.get("ppm", "0").strip()

    if not _find_binary("welle-cli"):
        return jsonify({"error": "welle-cli not found"})

    # Get help output to show available flags
    help_out = ""
    for help_flag in ["-h", "--help", "-?"]:
        try:
            hr = _sp.run(["welle-cli", help_flag],
                         capture_output=True, text=True, timeout=3)
            out = (hr.stdout + hr.stderr).strip()
            if out and "invalid option" not in out.lower():
                help_out = out
                break
            elif out:
                help_out = out  # keep even if error, may have usage info
        except Exception as e:
            help_out = f"help failed: {e}"

    cmd = ["welle-cli", "-w", "7979", "-c", channel, "-g", "-1", "-F", "rtl_sdr"]
    if ppm and ppm != "0":
        cmd += ["-g", ppm]

    try:
        proc = _sp.Popen(cmd, stdout=_sp.PIPE, stderr=_sp.PIPE)
        try:
            stdout, stderr = proc.communicate(timeout=25)
        except _sp.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()

        return jsonify({
            "cmd":      " ".join(cmd),
            "stdout":   stdout.decode(errors="ignore").split("\n")[-20:],
            "stderr":   stderr.decode(errors="ignore").split("\n")[-20:],
            "returncode": proc.returncode,
            "help":     help_out.split("\n"),
        })
    except Exception as e:
        return jsonify({"error": str(e), "cmd": " ".join(cmd),
                        "help": help_out.split("\n")})


@app.get("/api/dab/scan")
@login_required
def api_dab_scan():
    """
    Scan a DAB multiplex using welle-cli in HTTP server mode.
    Query params:
        channel  — DAB channel name e.g. 5C  (required)
        serial   — dongle serial number       (optional, uses first available if omitted)
        ppm      — frequency correction       (optional, uses registry value if omitted)
    Returns JSON: {ensemble, snr, services: [{name, sid, bitrate, stereo, ok}]}
    """
    import shutil, subprocess as _sp, urllib.request as _ur

    channel = request.args.get("channel", "").strip().upper()
    serial  = request.args.get("serial",  "").strip()
    ppm     = request.args.get("ppm",     "").strip()

    if not channel:
        return jsonify({"error": "channel parameter required"}), 400

    if not _find_binary("welle-cli"):
        return jsonify({"error": "welle-cli not found — install welle.io"}), 503

    # Resolve device index
    device_idx = "0"
    if serial:
        try:
            device_idx = str(sdr_manager.resolve_index(serial))
        except SdrNotFoundError as e:
            return jsonify({"error": str(e)}), 404
        except SdrBusyError as e:
            return jsonify({"error": str(e)}), 409

    # Resolve PPM from registry if not explicitly given
    if not ppm and serial:
        for dev in monitor.app_cfg.sdr_devices:
            if dev.serial == serial:
                ppm = str(dev.ppm)
                break

    # Welle-cli scan port — use a fixed high port
    WELLE_PORT = 7979

    # Build command — avoid --no-stdout (not all builds support it)
    # Use -d (lowercase) for device index — standard across PPA and built versions
    # stdout → DEVNULL, capture stderr for diagnostics
    # Per welle-cli syntax: "welle-cli -w <port> [OPTION]" — -w MUST come first
    # -u disables coarse corrector, helps dongles with large frequency offsets
    welle_bin = _find_binary("welle-cli")
    if not welle_bin:
        return jsonify({"error": "welle-cli not found on system PATH or /usr/bin"}), 503
    cmd = [welle_bin, "-w", str(WELLE_PORT), "-c", channel, "-g", "-1"]
    if device_idx and device_idx != "0":
        cmd += ["-F", f"rtl_sdr,{device_idx}"]
    else:
        cmd += ["-F", "rtl_sdr"]

    proc = None
    stderr_lines = []
    try:
        # Log exact command for diagnosis
        cmd_str = " ".join(cmd)
        print(f"[DABScan] Launching: {cmd_str}")

        proc = _sp.Popen(cmd, stdout=_sp.PIPE, stderr=_sp.PIPE)
        print(f"[DABScan] PID: {proc.pid}")

        # Read stderr in a background thread so it doesn't block
        import threading as _th
        def _read_stderr():
            for line in proc.stderr:
                decoded = line.decode(errors="ignore").strip()
                if decoded:
                    stderr_lines.append(decoded)
                    print(f"[DABScan stderr] {decoded}")
        _th.Thread(target=_read_stderr, daemon=True).start()

        # Also read stdout in background
        stdout_lines = []
        def _read_stdout():
            for line in proc.stdout:
                decoded = line.decode(errors="ignore").strip()
                if decoded:
                    stdout_lines.append(decoded)
                    print(f"[DABScan stdout] {decoded}")
        _th.Thread(target=_read_stdout, daemon=True).start()

        # Brief startup check — crash detection only
        time.sleep(2.0)
        poll_result = proc.poll()
        print(f"[DABScan] After 2s: poll={poll_result}, stderr={stderr_lines[-3:]}")
        if poll_result is not None:
            err_out = stderr_lines[-10:] if stderr_lines else ["unknown error"]
            return jsonify({"error": "welle-cli exited immediately",
                            "debug_stderr": err_out, "debug_cmd": cmd,
                            "returncode": poll_result}), 500

        available_urls = {}  # populated during polling

        # Poll for up to 60s — sync can take 15-20s on weaker signals
        deadline = time.time() + 60
        services  = []
        ensemble  = {}

        while time.time() < deadline:
            time.sleep(1)
            # Check welle-cli hasn't crashed
            if proc.poll() is not None:
                err_out = " | ".join(stderr_lines[-3:]) if stderr_lines else "crashed"
                return jsonify({"error": f"welle-cli crashed: {err_out}"}), 500
            try:
                # Try both API path variants (Ubuntu vs welle.io PPA)
                # Ubuntu build: /api/mux.json, /api/stations.json (or /api/services)
                # welle-cli Ubuntu build uses /mux.json for everything
                # Services are nested inside the mux JSON
                ensemble_urls = [
                    f"http://localhost:{WELLE_PORT}/mux.json",
                    f"http://localhost:{WELLE_PORT}/api/mux.json",
                ]
                # Fetch /mux.json — contains ensemble + all services
                mux_data = None
                for eurl in ensemble_urls:
                    try:
                        with _ur.urlopen(eurl, timeout=2) as r:
                            mux_data = json.loads(r.read())
                        break
                    except Exception:
                        continue

                if not mux_data:
                    continue  # not ready yet

                # Extract ensemble — mux_data.ensemble.label.label
                ens_obj = mux_data.get("ensemble", {})
                ens_lbl = ens_obj.get("label", {})
                ensemble = {
                    "ensembleLabel": (ens_lbl.get("label", "") or
                                     ens_lbl.get("shortlabel", "")).strip(),
                    "snr": mux_data.get("demodulator", {}).get("snr", 0),
                }

                # Services are in mux_data["services"] array
                # Each service: label.label, components[].subchannel.bitrate,
                #                channels (1=mono, 2=stereo), audiolevel.left >= 0 = active
                for svc in mux_data.get("services", []):
                    lbl  = svc.get("label", {})
                    name = (lbl.get("label", "") or lbl.get("shortlabel", "")).strip()
                    if not name:
                        continue
                    # Get bitrate from first audio component
                    bitrate  = 0
                    is_audio = False
                    for c in svc.get("components", []):
                        if c.get("transportmode") == "audio":
                            is_audio = True
                            bitrate  = c.get("subchannel", {}).get("bitrate", 0)
                            break
                    if not is_audio and svc.get("components"):
                        continue  # data-only service
                    stereo  = svc.get("channels", 1) > 1
                    active  = svc.get("audiolevel", {}).get("left", -1) >= 0
                    services.append({
                        "name":    name,
                        "label":   lbl.get("label", name).strip(),
                        "sid":     svc.get("sid", ""),
                        "bitrate": bitrate,
                        "stereo":  stereo,
                        "mode":    svc.get("mode", ""),
                        "ok":      active,
                    })
                if services:
                    break  # got useful data
            except Exception:
                pass  # not ready yet

        if not services:
            # Return full stderr for diagnosis
            err_detail = stderr_lines[-10:] if stderr_lines else ["(no stderr output)"]
            return jsonify({
                "error": f"No services found on channel {channel}",
                "debug_stderr": err_detail,
                "debug_cmd": cmd,
                "available_urls": available_urls,
                "hint": "Check signal and see debug_stderr + available_urls"
            }), 404

        return jsonify({
            "channel":  channel,
            "ensemble": ensemble.get("ensembleLabel") or ensemble.get("name", channel),
            "snr":      round(float(ensemble.get("snr", 0)), 1),
            "services": sorted(services, key=lambda s: s["name"].lower()),
        })

    except Exception as e:
        return jsonify({"error": f"Scan failed: {e}"}), 500

    finally:
        if proc:
            try: proc.terminate()
            except: pass
            try: proc.wait(timeout=3)
            except: proc.kill()



# ─── Setup wizard routes ──────────────────────────────────────────────────────

@app.get("/setup")
@login_required
def setup_wizard():
    """First-run setup wizard."""
    _csrf_token()  # ensure a session CSRF token exists during wizard flows
    cfg = monitor.app_cfg
    return render_template_string(SETUP_TPL,
        cfg=cfg, build=BUILD,
        sdr_devices=cfg.sdr_devices)


@app.post("/setup/complete")
@login_required
@csrf_protect
def setup_complete():
    """Mark wizard as done and redirect to dashboard."""
    cfg = monitor.app_cfg
    cfg.wizard_done = True
    save_config(cfg)
    flash("Setup complete. Welcome to SignalScope!")
    return redirect(url_for("index"))


@app.get("/api/setup/deps")
@login_required
def api_setup_deps():
    """Return dependency check results for the wizard."""
    import shutil, importlib

    def _pkg_version(name):
        try:
            return importlib.metadata.version(name)
        except Exception:
            return None

    def _cmd_version(cmd, args=("--version",)):
        try:
            import subprocess as _sp
            r = _sp.run([cmd] + list(args), capture_output=True, text=True, timeout=5)
            first = (r.stdout + r.stderr).strip().split("\n")[0]
            return first[:60] if first else "found"
        except Exception:
            return None

    deps = [
        # Required
        {
            "name":     "Python",
            "desc":     "Runtime — required",
            "optional": False,
            "status":   "ok",
            "version":  f"Python {__import__('sys').version.split()[0]}",
            "install":  None,
        },
        {
            "name":     "Flask",
            "desc":     "Web framework — required",
            "optional": False,
            "status":   "ok" if _pkg_version("flask") else "err",
            "version":  f"v{_pkg_version('flask')}" if _pkg_version("flask") else None,
            "install":  "pip install flask",
        },
        {
            "name":     "NumPy",
            "desc":     "Audio processing — required",
            "optional": False,
            "status":   "ok" if _pkg_version("numpy") else "err",
            "version":  f"v{_pkg_version('numpy')}" if _pkg_version("numpy") else None,
            "install":  "pip install numpy",
        },
        {
            "name":     "ONNX Runtime",
            "desc":     "AI model inference — required for AI monitoring",
            "optional": False,
            "status":   "ok" if _pkg_version("onnxruntime") else "err",
            "version":  f"v{_pkg_version('onnxruntime')}" if _pkg_version("onnxruntime") else None,
            "install":  "pip install onnxruntime onnx",
        },
        # Recommended
        {
            "name":     "waitress",
            "desc":     "Production WSGI server — recommended",
            "optional": True,
            "status":   "ok" if _pkg_version("waitress") else "err",
            "version":  f"v{_pkg_version('waitress')}" if _pkg_version("waitress") else None,
            "install":  "pip install waitress",
        },
        # Optional tools
        {
            "name":     "ffmpeg",
            "desc":     "HTTP/HTTPS stream decoding and live audio — optional",
            "optional": True,
            "status":   "ok" if _find_binary("ffmpeg") else "err",
            "version":  _cmd_version("ffmpeg", ("-version",))[:40] if _find_binary("ffmpeg") else None,
            "install":  "sudo apt install ffmpeg  # Linux\n# or: winget install ffmpeg  # Windows",
        },
        {
            "name":     "cryptography",
            "desc":     "Let's Encrypt certificate issuance — optional",
            "optional": True,
            "status":   "ok" if _pkg_version("cryptography") else "err",
            "version":  f"v{_pkg_version('cryptography')}" if _pkg_version("cryptography") else None,
            "install":  "pip install cryptography",
        },
        {
            "name":     "rtl-sdr tools",
            "desc":     "RTL-SDR command line tools (rtl_test, rtl_eeprom) — needed for DAB/FM",
            "optional": True,
            "status":   "ok" if _find_binary("rtl_test") else "err",
            "version":  "found" if _find_binary("rtl_test") else None,
            "install":  "sudo apt install rtl-sdr  # Linux",
        },
        {
            "name":     "pyrtlsdr",
            "desc":     "Python RTL-SDR library for FM monitoring with signal metrics",
            "optional": True,
            "status":   "ok" if _pkg_version("pyrtlsdr") else "err",
            "version":  f"v{_pkg_version('pyrtlsdr')}" if _pkg_version("pyrtlsdr") else None,
            "install":  "pip install pyrtlsdr",
        },
        {
            "name":     "welle-cli",
            "desc":     "DAB demodulator — needed for DAB stream monitoring",
            "optional": True,
            "status":   "ok" if _find_binary("welle-cli") else "err",
            "version":  _cmd_version("welle-cli") if _find_binary("welle-cli") else None,
            "install":  "sudo add-apt-repository ppa:jonelo/welle-io\nsudo apt install welle-cli",
        },
    ]
    return jsonify(deps)


@app.get("/api/setup/verify-welle")
@login_required
def api_setup_verify_welle():
    """Quick welle-cli sanity check — version and binary existence."""
    import shutil, subprocess as _sp
    if not _find_binary("welle-cli"):
        return jsonify({"ok": False, "message": "welle-cli not found on PATH"})
    try:
        # welle-cli uses -v not --version; also try no args as fallback
        # (it prints version info to stderr on startup)
        r = _sp.run(["welle-cli", "-v"], capture_output=True, text=True, timeout=5)
        output = (r.stdout + r.stderr).strip()
        if "invalid option" in output.lower() or not output:
            # Some builds use --version or just print on startup
            r2 = _sp.run(["welle-cli"], capture_output=True, text=True, timeout=3)
            output = (r2.stdout + r2.stderr).strip()
        ver = output.split("\n")[0][:80] if output else "welle-cli found"
        return jsonify({"ok": True, "message": ver or "welle-cli found"})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)})


@app.post("/api/setup/config")
@login_required
@csrf_protect
def api_setup_config():
    """Save basic instance config from wizard step 3."""
    cfg = monitor.app_cfg
    f   = request.form
    cfg.hub.site_name  = f.get("site_name", "").strip()
    cfg.hub.mode       = f.get("hub_mode", "client")
    raw_url            = f.get("hub_url", "").strip()
    cfg.hub.hub_url    = HubClient._normalise_url(raw_url) if raw_url else ""
    cfg.hub.secret_key = f.get("hub_secret", "").strip()
    save_config(cfg)
    return jsonify({"ok": True})


@app.post("/api/setup/auth")
@login_required
@csrf_protect
def api_setup_auth():
    """Save auth config from wizard step 4.

    If auth is enabled during the wizard, treat the current browser session as
    authenticated immediately. Otherwise the final /setup/complete POST is
    forced through login_required, gets bounced to /login, and wizard_done is
    never written until the user goes through the wizard a second time.
    """
    from flask import session
    cfg = monitor.app_cfg
    f   = request.form
    cfg.auth.enabled  = bool(f.get("auth_enabled"))
    cfg.auth.username = f.get("auth_username", "admin").strip() or "admin"
    pw = f.get("auth_password", "").strip()
    if pw:
        if len(pw) < 8:
            return jsonify({"ok": False, "message": "Password must be at least 8 characters"})
        cfg.auth.password_hash = _hash_password(pw)
        cfg.auth.first_login   = False
    save_config(cfg)

    if cfg.auth.enabled:
        session["logged_in"] = True
        session["login_ts"]  = time.time()
        # Keep the existing CSRF token if present; if not, create one now so
        # the final wizard form submit remains valid after auth is enabled.
        session.setdefault("_csrf", hashlib.sha256(os.urandom(32)).hexdigest())

    return jsonify({"ok": True})


@app.route("/login", methods=["GET","POST"])
def login():
    from flask import session
    cfg = monitor.app_cfg
    if not cfg.auth.enabled:
        session["logged_in"] = True
        session["login_ts"]  = time.time()
        session.setdefault("_csrf", hashlib.sha256(os.urandom(32)).hexdigest())
        return redirect(url_for("index"))

    ip    = request.remote_addr or "unknown"
    error = None
    first = cfg.auth.first_login

    # Check lockout
    locked_secs = login_limiter.is_locked(ip)
    if locked_secs > 0:
        mins = int(locked_secs // 60) + 1
        return render_template_string(LOGIN_TPL, locked=True, locked_mins=mins,
                                      first_login=False, username="", build=BUILD,
                                      error=None), 429

    if request.method == "POST":
        f = request.form
        uname = f.get("username","").strip()
        pw    = f.get("password","")

        def _fail(msg):
            login_limiter.record_failure(ip, cfg.login_max_attempts, cfg.login_lockout_mins)
            _log_security(f"Failed login for '{uname}' from {ip}")
            return msg

        if uname != cfg.auth.username:
            error = _fail("Invalid username or password.")
        elif first:
            if cfg.auth.password_hash and not _check_password(pw, cfg.auth.password_hash):
                error = _fail("Invalid username or password.")
            else:
                np = f.get("new_password","")
                cp = f.get("confirm_password","")
                if len(np) < 8:
                    error = "New password must be at least 8 characters."
                elif np != cp:
                    error = "Passwords do not match."
                else:
                    cfg.auth.password_hash = _hash_password(np)
                    cfg.auth.first_login   = False
                    save_config(cfg)
                    login_limiter.clear(ip)
                    session["logged_in"] = True
                    session["login_ts"]  = time.time()
                    session.setdefault("_csrf", hashlib.sha256(os.urandom(32)).hexdigest())
                    flash("Password set. Welcome!")
                    return redirect(_safe_next())
        else:
            if not _check_password(pw, cfg.auth.password_hash):
                error = _fail("Invalid username or password.")
            else:
                login_limiter.clear(ip)
                _log_security(f"Successful login for '{uname}' from {ip}")
                # Transparently upgrade legacy SHA-256 hash to pbkdf2 on login
                if (len(cfg.auth.password_hash) == 64 and
                        all(c in "0123456789abcdef" for c in cfg.auth.password_hash)):
                    cfg.auth.password_hash = _hash_password(pw)
                    save_config(cfg)
                    _log_security(f"Upgraded password hash to pbkdf2 for '{uname}'")
                session["logged_in"] = True
                session["login_ts"]  = time.time()
                session.setdefault("_csrf", hashlib.sha256(os.urandom(32)).hexdigest())
                # If login was triggered by the final setup POST being bounced to /login,
                # complete the wizard here because the original target only accepts POST.
                next_url = _safe_next()
                if next_url == "/setup/complete":
                    cfg.wizard_done = True
                    save_config(cfg)
                    flash("Welcome to SignalScope!")
                    return redirect(url_for("index"))
                return redirect(next_url)

    return render_template_string(LOGIN_TPL, error=error, first_login=first,
                                  username=request.form.get("username",""),
                                  locked=False, locked_mins=0, build=BUILD)


@app.route("/logout", methods=["GET","POST"])
def logout():
    """Accept both GET and POST for compatibility, but POST is preferred.
    GET is kept so existing bookmarks and nav links still work.
    """
    from flask import session
    _log_security(f"Logout from {request.remote_addr}")
    session.clear()
    return redirect(url_for("login"))

# ─── SLA dashboard ────────────────────────────────────────────────────────────

@app.get("/sla")
@login_required
def sla_dashboard():
    cfg = monitor.app_cfg
    target = cfg.sla_target_pct
    rows = []
    for inp in cfg.inputs:
        pct  = sla_pct(inp)
        mon_h = inp._sla_monitored_s / 3600.0
        down_m = inp._sla_alert_s / 60.0
        rows.append({
            "name":    inp.name,
            "pct":     round(pct, 3),
            "target":  target,
            "ok":      pct >= target,
            "mon_h":   round(mon_h, 1),
            "down_m":  round(down_m, 1),
            "month":   inp._sla_month or time.strftime("%Y-%m"),
        })
    return render_template_string(SLA_TPL, rows=rows, target=target, build=BUILD)

@app.get("/sla.csv")
@login_required
def sla_csv():
    cfg = monitor.app_cfg
    lines = ["Stream,Month,Uptime %,Target %,Monitored (h),Downtime (min),Met SLA"]
    for inp in cfg.inputs:
        pct = sla_pct(inp)
        lines.append(f"{inp.name},{inp._sla_month or time.strftime('%Y-%m')},"
                     f"{pct:.3f},{cfg.sla_target_pct},"
                     f"{inp._sla_monitored_s/3600:.1f},{inp._sla_alert_s/60:.1f},"
                     f"{'Yes' if pct>=cfg.sla_target_pct else 'No'}")
    return Response("\n".join(lines), mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="sla_{time.strftime("%Y%m")}.csv"'})

# ─── Now Playing API ─────────────────────────────────────────────────────────

@app.get("/api/nowplaying_stations")
@login_required
def api_nowplaying_stations():
    return jsonify(nowplaying_poller.get_stations())

@app.get("/api/nowplaying/<rpuid>")
@login_required
def api_nowplaying_track(rpuid):
    return jsonify(nowplaying_poller.get_nowplaying(rpuid))

@app.get("/api/nowplaying_art/<rpuid>")
@login_required
def api_nowplaying_art(rpuid):
    d = nowplaying_poller.get_nowplaying(rpuid)
    art = _normalize_nowplaying_artwork_url(d.get("artwork", ""))
    payload, ctype = _fetch_nowplaying_artwork_bytes(art)
    if not payload:
        return ("", 404)
    resp = make_response(payload)
    resp.headers["Content-Type"] = ctype or "image/jpeg"
    resp.headers["Cache-Control"] = "public, max-age=300"
    return resp

@app.get("/api/nowplaying_all")
@login_required
def api_nowplaying_all():
    """Returns nowplaying data for all inputs that have a station assigned."""
    result = {}
    for inp in monitor.app_cfg.inputs:
        if inp.nowplaying_station_id:
            result[inp.name] = nowplaying_poller.get_nowplaying(inp.nowplaying_station_id)
    return jsonify(result)

@app.get("/api/nowplaying_debug")
@login_required
def api_nowplaying_debug():
    """Returns raw API response and parsed state for debugging."""
    return jsonify({
        "stations_count": len(nowplaying_poller.get_stations()),
        "stations_sample": nowplaying_poller.get_stations()[:5],
        "last_error": nowplaying_poller._last_error,
        "last_raw_keys": nowplaying_poller._last_raw_keys,
        "last_fetch_at": nowplaying_poller._last_fetch_at,
    })

# ─── Reports routes ───────────────────────────────────────────────────────────

@app.get("/reports")
@login_required
def reports():
    events = _alert_log_load(2000)
    # Summary counts
    counts = {}
    for e in events:
        counts[e.get("type","")] = counts.get(e.get("type",""), 0) + 1
    streams = sorted(set(e.get("stream","") for e in events if e.get("stream")))
    types   = sorted(set(e.get("type","")   for e in events if e.get("type")))
    with_clips = sum(1 for e in events if e.get("clip"))
    return render_template_string(REPORTS_TPL,
        events=events, total=len(events), counts=counts,
        streams=streams, types=types, with_clips=with_clips, build=BUILD)

@app.get("/reports/data")
@login_required
def reports_data():
    """JSON endpoint for the reports page live refresh — returns events + summary counts."""
    events = _alert_log_load(2000)
    counts = {}
    for e in events:
        counts[e.get("type","")] = counts.get(e.get("type",""), 0) + 1
    with_clips = sum(1 for e in events if e.get("clip"))
    return jsonify({
        "events":     events,
        "total":      len(events),
        "counts":     counts,
        "with_clips": with_clips,
    })

@app.get("/reports.csv")
@login_required
def reports_csv():
    events = _alert_log_load(10000)
    cols = ["ts","stream","type","message","level_dbfs","rtp_loss_pct",
            "ptp_state","ptp_offset_us","ptp_drift_us","ptp_jitter_us","ptp_gm","clip"]
    lines = [",".join(cols)]
    for e in events:
        lines.append(",".join(
            f'"{str(e.get(c,"")).replace(chr(34), chr(39))}"' for c in cols
        ))
    return Response("\n".join(lines), mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="alerts_{time.strftime("%Y%m%d_%H%M")}.csv"'})

@app.post("/reports/clear")
@login_required
@csrf_protect
def reports_clear():
    try:
        with _alert_log_lock:
            open(ALERT_LOG_PATH, "w").close()
        # Also clear in-memory history
        for inp in monitor.app_cfg.inputs:
            inp._history.clear()
        flash("Alert log cleared.")
    except Exception as e:
        flash(f"Clear failed: {e}")
    return redirect(url_for("reports"))

# ─── Hub API routes (active when mode == hub or both) ────────────────────────

@app.post(f"/api/{HUB_API_VERSION}/heartbeat")
def hub_heartbeat():
    cfg    = monitor.app_cfg
    secret = cfg.hub.secret_key
    if cfg.hub.mode not in ("hub","both"):
        return jsonify({"error":"not a hub"}), 404

    # ── Rate limiting ─────────────────────────────────────────────────────────
    # Key by X-Forwarded-For if set (reverse proxy), else remote_addr
    # Site name extracted post-decryption for accurate per-site limiting
    forwarded = request.headers.get("X-Forwarded-For", "")
    client_ip = forwarded.split(",")[0].strip() if forwarded else (request.remote_addr or "")

    # ── Read raw body (may be encrypted) ─────────────────────────────────────
    raw_body = request.get_data()

    # ── Signature + timestamp verification (if secret configured) ────────────
    if secret:
        sig   = request.headers.get("X-Hub-Sig","")
        ts_h  = request.headers.get("X-Hub-Ts","0")
        nonce = request.headers.get("X-Hub-Nonce","")

        if not sig or not nonce:
            return jsonify({"error":"missing security headers"}), 403

        try:
            ts = float(ts_h)
        except ValueError:
            return jsonify({"error":"invalid timestamp"}), 403

        # Replay check
        if not hub_nonce_store.check_and_store(nonce):
            return jsonify({"error":"replay detected"}), 403

        # Decrypt if Content-Type says octet-stream
        ct = request.content_type or ""
        if "octet-stream" in ct:
            try:
                raw_body = hub_decrypt_payload(secret, raw_body)
            except Exception as e:
                return jsonify({"error":f"decryption failed: {e}"}), 403

        # Verify HMAC over decrypted body
        ok, reason = hub_verify_signature(secret, raw_body, sig, ts)
        if not ok:
            print(f"[HubSec] Signature rejected from {client_ip}: {reason}")
            return jsonify({"error":"forbidden","reason":reason}), 403

    # ── Parse JSON payload ────────────────────────────────────────────────────
    try:
        payload = json.loads(raw_body) if raw_body else {}
    except Exception:
        return jsonify({"error":"bad json"}), 400

    # ── Rate limiting — keyed by site name for accuracy across NAT ───────────
    # Use site name from payload (more reliable than IP when clients share NAT)
    site_key = payload.get("site", "") or client_ip
    if not hub_rate_limiter.allow(site_key):
        return jsonify({"error":"rate limit exceeded"}), 429

    # ── Ingest ────────────────────────────────────────────────────────────────
    hub_server.set_secret(secret)
    if not hub_server.ingest(payload, client_ip=client_ip):
        return jsonify({"error":"forbidden"}), 403

    # ── Build ACK — encrypt if secret is set ─────────────────────────────────
    site_name   = payload.get("site","")
    listen_reqs = listen_registry.pending_for_site(site_name)
    ack = {"ok": True, "listen_requests": listen_reqs}
    ack_bytes = json.dumps(ack).encode()

    if secret:
        enc = hub_encrypt_payload(secret, ack_bytes)
        return Response(enc, content_type="application/octet-stream")
    return jsonify(ack)

@app.get("/hub")
@login_required
def hub_dashboard():
    cfg = monitor.app_cfg
    if cfg.hub.mode not in ("hub","both"):
        return "This instance is not configured as a hub. Set mode to 'hub' or 'both' in Settings.", 404
    hub_server.set_secret(cfg.hub.secret_key)
    sites = hub_server.get_sites()
    for s in sites:
        streams = s.get("streams", [])
        if not s["online"]:
            s["site_status"] = "offline"
        elif any("ALERT" in st.get("ai_status","") for st in streams):
            s["site_status"] = "ALERT"
        elif any("WARN" in st.get("ai_status","") for st in streams):
            s["site_status"] = "WARN"
        else:
            s["site_status"] = "OK"
    def _ago(s):
        s = int(s or 0)
        if s < 5:   return "just now"
        if s < 60:  return f"{s}s ago"
        return f"{s//60}m ago"
    def _fmt(ts):
        if not ts: return "—"
        return time.strftime("%H:%M:%S", time.localtime(ts))
    def _ai_class(s):
        s = s or ""
        if "ALERT" in s: return "ai-al"
        if "WARN"  in s: return "ai-wn"
        return "ai-ok"
    def _rtp_class(p):
        p = float(p or 0)
        if p >= 2:   return "rtp-al"
        if p >= 0.5: return "rtp-wn"
        return "rtp-ok"
    return render_template_string(HUB_TPL, sites=sites, build=BUILD, now=time.time(),
        mode_both=(cfg.hub.mode=="both"), ago=_ago, fmt=_fmt,
        aiClass=_ai_class, rtpClass=_rtp_class,
    )

@app.post(f"/api/{HUB_API_VERSION}/audio_chunk/<slot_id>")
def hub_audio_chunk(slot_id):
    """Receive a signed audio chunk from a client and feed it to the waiting browser."""
    cfg    = monitor.app_cfg
    secret = cfg.hub.secret_key
    client_ip = request.remote_addr or ""

    # Rate limit
    if not hub_rate_limiter.allow(client_ip):
        return jsonify({"error":"rate limit exceeded"}), 429

    # Verify signature on audio chunks if secret is configured
    if secret:
        sig   = request.headers.get("X-Hub-Sig","")
        ts_h  = request.headers.get("X-Hub-Ts","0")
        nonce = request.headers.get("X-Hub-Nonce","")
        if not sig or not nonce:
            return jsonify({"error":"missing security headers"}), 403
        try:
            ts = float(ts_h)
        except ValueError:
            return jsonify({"error":"invalid timestamp"}), 403
        if not hub_nonce_store.check_and_store(nonce):
            return jsonify({"error":"replay detected"}), 403
        data = request.get_data()
        ok, reason = hub_verify_signature(secret, data, sig, ts)
        if not ok:
            return jsonify({"error":"forbidden","reason":reason}), 403
    else:
        data = request.get_data()

    slot = listen_registry.get(slot_id)
    if not slot:
        return jsonify({"error": "slot not found or expired"}), 404
    if data:
        slot.put(data)
    return jsonify({"ok": True, "queued": slot.q.qsize()})


@app.get("/hub/debug")
@login_required
def hub_debug():
    """Diagnostic: show raw stored site data as JSON."""
    cfg = monitor.app_cfg
    if cfg.hub.mode not in ("hub","both"):
        return jsonify({"error":"not a hub"}), 404
    hub_server.set_secret(cfg.hub.secret_key)
    sites = hub_server.get_sites()
    summary = []
    for s in sites:
        summary.append({
            "site":          s.get("site"),
            "online":        s.get("online"),
            "age_s":         s.get("age_s"),
            "running":       s.get("running"),
            "stream_count":  len(s.get("streams",[])),
            "streams":       [{"name": st.get("name"), "enabled": st.get("enabled"),
                               "level": st.get("level_dbfs")} for st in s.get("streams",[])],
            "payload_keys":  list(s.keys()),
        })
    return jsonify(summary)

@app.get("/hub/data")
@login_required
def hub_data():
    """JSON endpoint for hub page live refresh."""
    cfg = monitor.app_cfg
    if cfg.hub.mode not in ("hub","both"):
        return jsonify({"error":"not a hub"}), 404
    hub_server.set_secret(cfg.hub.secret_key)
    sites = hub_server.get_sites()
    for s in sites:
        streams = s.get("streams", [])
        if not s["online"]:
            s["site_status"] = "offline"
        elif any("ALERT" in st.get("ai_status","") for st in streams):
            s["site_status"] = "ALERT"
        elif any("WARN" in st.get("ai_status","") for st in streams):
            s["site_status"] = "WARN"
        else:
            s["site_status"] = "OK"
    return jsonify({"sites": sites, "hub_build": BUILD})

@app.get("/hub/site/<path:site_name>/stream/<int:sidx>/live")
@login_required
def hub_proxy_live(site_name, sidx):
    """
    Stream live audio from a remote client site.

    Two modes:
    1. Direct pull (client reachable from hub) — connect straight to client URL.
    2. Relay mode (client behind NAT) — create a listen slot, signal the client
       via the next heartbeat ACK, then stream chunks the client pushes back.
    """
    cfg = monitor.app_cfg
    if cfg.hub.mode not in ("hub","both"):
        return "Not a hub", 404
    site = hub_server.get_site(site_name)
    if not site:
        return "Site not found", 404

    client_addr = site.get("_client_addr","")

    # ── Try direct pull first ─────────────────────────────────────────────────
    if client_addr:
        url = f"{client_addr}/stream/{sidx}/live"
        try:
            # Only use direct pull if the endpoint is genuinely reachable and returns audio.
            # This avoids treating login pages / redirects / HTML errors as a valid live stream.
            class _NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, hdrs, newurl):
                    return None

            opener = urllib.request.build_opener(_NoRedirect)
            test_req = urllib.request.Request(url, method="HEAD")
            with opener.open(test_req, timeout=3) as _test:
                ct = (_test.headers.get("Content-Type", "") or "").split(";")[0].strip().lower()
                status = getattr(_test, "status", 200)
                if status != 200 or not ct.startswith("audio/"):
                    raise RuntimeError(f"direct pull not suitable (status={status}, content-type={ct or 'unknown'})")
                mime = ct or "audio/mpeg"

            def generate_direct():
                try:
                    with urllib.request.urlopen(urllib.request.Request(url), timeout=15) as resp:
                        ct2 = (resp.headers.get("Content-Type", "") or "").split(";")[0].strip().lower()
                        if ct2 and not ct2.startswith("audio/"):
                            raise RuntimeError(f"direct pull returned non-audio content-type {ct2}")
                        while True:
                            chunk = resp.read(8192)
                            if not chunk:
                                break
                            yield chunk
                except Exception as e:
                    print(f"[HubProxy] Direct stream error {site_name}/{sidx}: {e}")
                    return

            return Response(generate_direct(), mimetype=mime,
                headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no",
                         "Transfer-Encoding":"chunked"})
        except Exception as e:
            print(f"[HubProxy] Falling back to relay for {site_name}/{sidx}: {e}")
            pass  # Not directly reachable / not audio — fall through to relay mode

    # ── Relay mode — client is behind NAT ────────────────────────────────────
    slot = listen_registry.create(site_name, sidx)
    print(f"[HubProxy] Relay slot {slot.slot_id} created for {site_name}/stream/{sidx}")

    def generate_relay():
        # Wait up to 12s for the client to start pushing (covers 2 heartbeat cycles)
        deadline = time.time() + 12.0
        started  = False
        try:
            while True:
                try:
                    chunk = slot.get(timeout=2.0)
                    started = True
                    yield chunk
                except queue.Empty:
                    if slot.closed:
                        break
                    if not started and time.time() > deadline:
                        print(f"[HubProxy] Relay slot {slot.slot_id} timed out waiting for client")
                        break
                    if started and slot.stale:
                        break
        finally:
            slot.closed = True
            listen_registry.remove(slot.slot_id)
            print(f"[HubProxy] Relay slot {slot.slot_id} closed")

    return Response(generate_relay(), mimetype="audio/mpeg",
        headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no",
                 "Transfer-Encoding":"chunked"})

@app.get("/hub/site/<path:site_name>/stream/<int:sidx>/clip")
@login_required
def hub_proxy_clip(site_name, sidx):
    """Proxy a WAV clip download from a client site through the hub."""
    cfg = monitor.app_cfg
    if cfg.hub.mode not in ("hub","both"):
        return "Not a hub", 404
    site = hub_server.get_site(site_name)
    if not site:
        return "Site not found", 404
    client_addr = site.get("_client_addr","")
    if not client_addr:
        return "Client address unknown", 503
    secs = request.args.get("seconds","10")
    url  = f"{client_addr}/stream/{sidx}/audio.wav?seconds={secs}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        return Response(data, mimetype="audio/wav",
            headers={"Content-Disposition":f'attachment; filename="hub_{site_name}_{sidx}.wav"'})
    except Exception as e:
        return f"Could not fetch clip from client: {e}", 502

@app.get("/hub/site/<path:site_name>/alerts/clip/<stream_name>/<filename>")
@login_required
def hub_proxy_alert_clip(site_name, stream_name, filename):
    """Proxy a saved alert clip WAV from a client site through the hub."""
    cfg = monitor.app_cfg
    if cfg.hub.mode not in ("hub","both"):
        return "Not a hub", 404
    site = hub_server.get_site(site_name)
    if not site:
        return "Site not found", 404
    client_addr = site.get("_client_addr","")
    if not client_addr:
        return "Client address unknown", 503
    url = f"{client_addr}/clips/{urllib.parse.quote(stream_name)}/{urllib.parse.quote(filename)}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=15) as resp:
            data = resp.read()
        return Response(data, mimetype="audio/wav",
            headers={"Content-Disposition": f'attachment; filename="{filename}"',
                     "Cache-Control": "public, max-age=3600"})
    except Exception as e:
        return f"Could not fetch clip: {e}", 502


@app.get("/hub/reports")
@login_required
def hub_reports():
    """Merged alert reports from all connected sites."""
    cfg = monitor.app_cfg
    if cfg.hub.mode not in ("hub","both"):
        return "Not a hub", 404
    hub_server.set_secret(cfg.hub.secret_key)
    sites = hub_server.get_sites()

    # Merge all sites' recent_alerts, tagging each with site name and client_addr
    all_events = []
    for s in sites:
        client_addr = s.get("_client_addr","")
        site_name   = s.get("site","?")
        for ev in s.get("recent_alerts", []):
            merged = dict(ev)
            merged["_site"]        = site_name
            merged["_client_addr"] = client_addr
            merged["_online"]      = s.get("online", False)
            all_events.append(merged)

    # Sort newest first
    all_events.sort(key=lambda e: e.get("ts",""), reverse=True)

    # Filter params
    f_site   = request.args.get("site","")
    f_stream = request.args.get("stream","")
    f_type   = request.args.get("type","")

    site_names  = sorted(set(e["_site"]   for e in all_events))
    stream_names= sorted(set(e.get("stream","") for e in all_events if e.get("stream")))
    type_names  = sorted(set(e.get("type","")   for e in all_events if e.get("type")))

    counts = {}
    for e in all_events:
        counts[e.get("type","")] = counts.get(e.get("type",""), 0) + 1
    with_clips = sum(1 for e in all_events if e.get("clip"))

    return render_template_string(HUB_REPORTS_TPL,
        events=all_events, total=len(all_events),
        counts=counts, site_names=site_names,
        stream_names=stream_names, type_names=type_names,
        with_clips=with_clips, build=BUILD,
        f_site=f_site, f_stream=f_stream, f_type=f_type,
    )


# Update heartbeat ingestion to record client IP
# client_ip is now passed directly in hub_heartbeat — no monkey-patch needed

HUB_REPORTS_TPL = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Hub Alert Reports — SignalScope</title>
<style nonce="{{csp_nonce()}}">
:root{--bg:#0d0f14;--sur:#161a22;--bor:#252b38;--acc:#4f9cf9;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#e2e8f0;--mu:#64748b}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:var(--bg);color:var(--tx);font-size:13px}
.badge{font-size:11px;padding:2px 8px;border-radius:999px;background:#1e3a5f;color:var(--acc)}
.btn{display:inline-block;padding:5px 12px;border-radius:6px;font-size:12px;cursor:pointer;border:none;text-decoration:none;font-weight:500}
.bg{background:var(--bor);color:var(--tx)}.bp{background:var(--acc);color:#fff}.bw{background:#7c2d12;color:#fca5a5}
.bd{background:var(--al);color:#fff}.bs{padding:3px 9px;font-size:12px}.nav-active{background:var(--acc)!important;color:#fff!important}
.ss-header{background:linear-gradient(180deg,#111827,#0b1220)!important;border-bottom:1px solid var(--bor)!important;padding:14px 22px!important;display:flex;align-items:center;gap:16px;flex-wrap:wrap;box-shadow:0 3px 14px rgba(0,0,0,.28)}
.ss-brand{display:flex;align-items:center;gap:14px;color:var(--tx)}
.ss-logo{height:72px;width:auto;display:block;filter:drop-shadow(0 2px 6px rgba(0,0,0,.45))}
.ss-brandcopy{display:flex;flex-direction:column;gap:2px}
.ss-title{font-size:24px;font-weight:800;letter-spacing:.2px;line-height:1}
.ss-subtitle{font-size:12px;color:var(--mu);letter-spacing:.08em;text-transform:uppercase}
.ss-build{font-size:11px;padding:2px 8px;border-radius:999px;background:#1e3a5f;color:var(--acc)}
.ss-nav{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-left:auto}
body.report-page .ss-logo{height:42px;max-height:42px}
body.report-page .ss-title{font-size:20px}
main{padding:18px 20px 24px}
.report-hero{display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap;padding:14px 18px;background:linear-gradient(180deg,#161a22,#131722);border-bottom:1px solid var(--bor)}
.report-title{font-size:20px;font-weight:800;line-height:1.1}
.report-sub{font-size:12px;color:var(--mu);margin-top:4px}
.hero-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.filters{display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap;align-items:center;background:#121722;border:1px solid var(--bor);border-radius:12px;padding:12px}
.filters select,.filters input{background:#1e2433;border:1px solid var(--bor);border-radius:6px;color:var(--tx);padding:6px 10px;font-size:12px}
.filters label{color:var(--mu);font-size:12px}
.summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:14px}
.sc{background:linear-gradient(180deg,#161a22,#131722);border:1px solid var(--bor);border-radius:12px;padding:12px 14px;box-shadow:0 4px 10px rgba(0,0,0,.18)}
.sc-val{font-size:24px;font-weight:800;line-height:1.1}.sc-lbl{font-size:11px;color:var(--mu);margin-top:4px;text-transform:uppercase;letter-spacing:.08em}
.metrics-strip{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}.metric-chip{padding:8px 10px;border:1px solid var(--bor);border-radius:999px;background:#121722;font-size:12px}.metric-chip strong{color:var(--tx)}
.table-wrap{border:1px solid var(--bor);border-radius:12px;overflow:hidden;background:var(--sur);box-shadow:0 4px 10px rgba(0,0,0,.18)}
table{width:100%;border-collapse:collapse}
thead th{text-align:left;padding:8px 10px;background:#151b26;border-bottom:2px solid var(--bor);font-size:11px;color:var(--mu);text-transform:uppercase;letter-spacing:.05em;white-space:nowrap;position:sticky;top:0;z-index:2}
tbody td{padding:8px 10px;border-bottom:1px solid var(--bor);vertical-align:top}
tr:hover td{background:#1a1f2e}
.type-badge{display:inline-block;padding:2px 7px;border-radius:999px;font-size:11px;font-weight:600;white-space:nowrap}
.site-badge{display:inline-block;padding:2px 7px;border-radius:999px;font-size:11px;background:#1e2a3a;color:var(--acc);white-space:nowrap}
.offline-site{opacity:0.6}
.t-silence{background:#1e2433;color:#93c5fd}.t-clip{background:#3a1e1e;color:#fca5a5}
.t-hiss{background:#2a2a1e;color:#fde68a}.t-rtp{background:#1e2433;color:#c4b5fd}
.t-ai_alert{background:#3a1e1e;color:#fca5a5}.t-ai_warn{background:#3a2a0f;color:#fde68a}
.t-ptp{background:#1e3a2a;color:#86efac}.t-cmp{background:#2a1e3a;color:#d8b4fe}
.t-other{background:var(--bor);color:var(--mu)}
.level-bar{display:inline-block;width:60px;height:6px;background:var(--bor);border-radius:3px;vertical-align:middle;margin-left:4px}
.level-fill{height:6px;border-radius:3px}
audio{height:28px;width:200px;accent-color:var(--acc);vertical-align:middle}
.no-data{text-align:center;padding:48px;color:var(--mu)}
.page-info{color:var(--mu);font-size:12px;margin-left:auto}
</style>
<link rel="icon" type="image/x-icon" href="/favicon.ico"></head><body class="report-page">
{{ topnav("hub_reports") }}
<div class="report-hero">
  <div>
    <div class="report-title">Hub Alert Reports</div>
    <div class="report-sub">Multi-site alert timeline, clip playback, and network event summary for SignalScope.</div>
  </div>
  <div class="hero-actions">
    <span class="badge">{{total}} events from {{site_names|length}} site{{"s" if site_names|length!=1 else ""}}</span>
    <a href="/hub" class="btn bg">← Hub</a>
  </div>
</div>

<main>
  <div class="summary">
    <div class="sc"><div class="sc-val" id="sc-total">{{total}}</div><div class="sc-lbl">Total Events</div></div>
    <div class="sc"><div class="sc-val" id="sc-critical" style="color:var(--al)">{{counts.get('SILENCE',0)+counts.get('AI_ALERT',0)+counts.get('RTP_LOSS',0)}}</div><div class="sc-lbl">🔴 Critical</div></div>
    <div class="sc"><div class="sc-val" id="sc-warn" style="color:var(--wn)">{{counts.get('AI_WARN',0)+counts.get('RTP_LOSS_WARN',0)+counts.get('HISS',0)}}</div><div class="sc-lbl">🟡 Warnings</div></div>
    <div class="sc"><div class="sc-val" style="color:var(--acc)">{{counts.get('RTP_LOSS',0)+counts.get('RTP_LOSS_WARN',0)}}</div><div class="sc-lbl">📦 RTP Loss</div></div>
    <div class="sc"><div class="sc-val" style="color:#86efac">{{counts.get('PTP_OFFSET',0)+counts.get('PTP_JITTER',0)+counts.get('PTP_LOST',0)+counts.get('PTP_GM_CHANGE',0)}}</div><div class="sc-lbl">🕐 PTP Events</div></div>
    <div class="sc"><div class="sc-val">{{with_clips}}</div><div class="sc-lbl">🎵 With Clips</div></div>
  </div>

  <div class="metrics-strip">
    <div class="metric-chip"><strong>{{counts.get('SILENCE',0)}}</strong> Silence</div>
    <div class="metric-chip"><strong>{{counts.get('CLIP',0)}}</strong> Clip</div>
    <div class="metric-chip"><strong>{{counts.get('HISS',0)}}</strong> Hiss</div>
    <div class="metric-chip"><strong>{{counts.get('AI_ALERT',0)+counts.get('AI_WARN',0)}}</strong> AI Flags</div>
    <div class="metric-chip"><strong>{{counts.get('CMP_ALERT',0)}}</strong> Comparison</div>
  </div>

  <div class="filters">
    <label>Site
      <select id="f_site" onchange="applyFilters()">
        <option value="">All sites</option>
        {% for s in site_names %}<option value="{{s}}" {{'selected' if f_site==s}}>{{s}}</option>{% endfor %}
      </select>
    </label>
    <label>Stream
      <select id="f_stream" onchange="applyFilters()">
        <option value="">All streams</option>
        {% for s in stream_names %}<option value="{{s}}" {{'selected' if f_stream==s}}>{{s}}</option>{% endfor %}
      </select>
    </label>
    <label>Type
      <select id="f_type" onchange="applyFilters()">
        <option value="">All types</option>
        {% for t in type_names %}<option value="{{t}}" {{'selected' if f_type==t}}>{{t}}</option>{% endfor %}
      </select>
    </label>
    <label>From <input type="datetime-local" id="f_from" onchange="applyFilters()"></label>
    <label>To   <input type="datetime-local" id="f_to"   onchange="applyFilters()"></label>
    <label><input type="checkbox" id="f_clips" onchange="applyFilters()"> Clips only</label>
    <span class="page-info" id="row_count"></span>
  </div>

  <div class="table-wrap">
  <table id="evt_table">
    <thead>
      <tr>
        <th style="width:130px">Time</th>
        <th style="width:110px">Site</th>
        <th style="width:120px">Stream</th>
        <th style="width:95px">Type</th>
        <th>Detail</th>
        <th style="width:75px">Level</th>
        <th style="width:70px">RTP Loss</th>
        <th style="width:220px">Clip</th>
      </tr>
    </thead>
    <tbody id="evt_body">
    {% for e in events %}
    {% set tl = e.type.lower() if e.type else '' %}
    {% set tc = 't-silence' if tl=='silence' else ('t-clip' if tl=='clip' else ('t-hiss' if tl=='hiss' else ('t-rtp' if 'rtp' in tl else ('t-ai_alert' if tl=='ai_alert' else ('t-ai_warn' if tl=='ai_warn' else ('t-ptp' if 'ptp' in tl else ('t-cmp' if 'cmp' in tl else 't-other'))))))) %}
    <tr data-site="{{e._site}}" data-stream="{{e.stream or ''}}" data-type="{{e.type or ''}}" data-ts="{{e.ts or ''}}" data-clip="{{e.clip or ''}}" class="{{'offline-site' if not e._online else ''}}">
      <td style="color:var(--mu);font-size:12px;white-space:nowrap">{{e.ts or ''}}</td>
      <td><span class="site-badge">{{e._site}}</span></td>
      <td><strong>{{e.stream or ''}}</strong></td>
      <td><span class="type-badge {{tc}}">{{e.type or ''}}</span></td>
      <td style="font-size:12px">{{e.message or ''}}</td>
      <td>
        {% if e.level_dbfs and e.level_dbfs > -120 %}
        <span style="font-size:12px;color:{{'var(--al)' if e.level_dbfs<=-55 else 'var(--ok)'}}">{{e.level_dbfs}} dB</span>
        <span class="level-bar"><span class="level-fill" style="width:{{[(e.level_dbfs+80)/80*100,100]|min|int}}%;background:{{'var(--al)' if e.level_dbfs<=-55 else 'var(--ok)'}}"></span></span>
        {% else %}—{% endif %}
      </td>
      <td>
        {% if e.rtp_loss_pct and e.rtp_loss_pct > 0 %}
        <span style="color:{{'var(--al)' if e.rtp_loss_pct>=2 else 'var(--wn)'}}">{{e.rtp_loss_pct}}%</span>
        {% else %}—{% endif %}
      </td>
      <td>
        {% if e.clip and e._client_addr %}
        <audio controls preload="none"
          src="/hub/site/{{e._site|urlencode}}/alerts/clip/{{e.stream|urlencode}}/{{e.clip}}">
        </audio>
        {% elif e.clip %}
        <span style="color:var(--mu);font-size:11px">clip on site (offline)</span>
        {% else %}<span style="color:var(--mu);font-size:11px">—</span>{% endif %}
      </td>
    </tr>
    {% else %}
    <tr><td colspan="8" class="no-data">No alert events from connected sites yet.</td></tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
</main>

<script nonce="{{csp_nonce()}}">
function applyFilters(){
  var fs = document.getElementById('f_site').value.toLowerCase();
  var fst= document.getElementById('f_stream').value.toLowerCase();
  var ft = document.getElementById('f_type').value.toLowerCase();
  var ff = document.getElementById('f_from').value;
  var ft2= document.getElementById('f_to').value;
  var fc = document.getElementById('f_clips').checked;
  var rows = document.querySelectorAll('#evt_body tr[data-stream]');
  var vis = 0;
  rows.forEach(function(r){
    var site = (r.dataset.site||'').toLowerCase();
    var s = (r.dataset.stream||'').toLowerCase();
    var t = (r.dataset.type||'').toLowerCase();
    var ts= r.dataset.ts||'';
    var c = r.dataset.clip||'';
    var show = true;
    if(fs  && site!==fs) show=false;
    if(fst && s!==fst) show=false;
    if(ft  && !t.includes(ft)) show=false;
    if(ff  && ts < ff.replace('T',' ')) show=false;
    if(ft2 && ts > ft2.replace('T',' ')) show=false;
    if(fc  && !c) show=false;
    r.style.display = show ? '' : 'none';
    if(show) vis++;
  });
  document.getElementById('row_count').textContent = vis+' event'+(vis!==1?'s':'')+' shown';
}
window.addEventListener('DOMContentLoaded', function(){
  // Pre-set filter dropdowns from URL params
  var p = new URLSearchParams(window.location.search);
  if(p.get('site'))  document.getElementById('f_site').value   = p.get('site');
  if(p.get('stream'))document.getElementById('f_stream').value = p.get('stream');
  if(p.get('type'))  document.getElementById('f_type').value   = p.get('type');
  applyFilters();
});
</script>
</body></html>"""

# ─── Hub dashboard template ───────────────────────────────────────────────────

HUB_TPL = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>SignalScope — Hub</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style nonce="{{csp_nonce()}}">
:root{--bg:#0d0f14;--sur:#161a22;--bor:#252b38;--acc:#4f9cf9;--ok:#22c55e;--wn:#f59e0b;--al:#ef4444;--tx:#e2e8f0;--mu:#64748b}
*{box-sizing:border-box;margin:0;padding:0}body{font-family:system-ui,sans-serif;background:var(--bg);color:var(--tx);font-size:14px}
header{background:var(--sur);border-bottom:1px solid var(--bor);padding:11px 20px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
header h1{font-size:17px;font-weight:700}.badge{font-size:11px;padding:2px 8px;border-radius:999px;background:#1e3a5f;color:var(--acc)}
.nav-active{background:var(--acc)!important;color:#fff!important}
nav a{color:var(--tx);font-size:13px;padding:5px 10px;border-radius:6px;background:var(--bor);text-decoration:none}
main{padding:18px;max-width:1400px;margin:0 auto}
.site-card{background:var(--sur);border:1px solid var(--bor);border-radius:10px;margin-bottom:20px;overflow:hidden}
.site-header{padding:12px 16px;display:flex;align-items:center;gap:10px;border-bottom:1px solid var(--bor);flex-wrap:wrap}
.site-name{font-size:16px;font-weight:700}
.site-meta{color:var(--mu);font-size:12px;margin-left:auto}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block;flex-shrink:0}
.dot-ok{background:var(--ok)}.dot-wn{background:var(--wn)}.dot-al{background:var(--al)}.dot-off{background:var(--mu)}
.dok{background:var(--ok)}.dwn{background:var(--wn)}.dal{background:var(--al)}.dlr{background:var(--acc)}.did{background:var(--mu)}
.streams{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px;padding:14px}
.sc{background:#1a1f2e;border:1px solid var(--bor);border-radius:8px;overflow:hidden}
.sc-name{font-weight:600;font-size:13px;padding:10px 10px 4px;display:flex;align-items:center;gap:6px}
.sc-row{display:flex;justify-content:space-between;font-size:12px;color:var(--mu);margin-top:4px}
.sc-row span{color:var(--tx)}
.rtp-ok{color:var(--ok)}.rtp-wn{color:var(--wn)}.rtp-al{color:var(--al)}
.ai-ok{color:var(--ok)}.ai-wn{color:var(--wn)}.ai-al{color:var(--al)}
.lbar-wrap{display:flex;align-items:center;gap:6px}
.lbar-track{flex:1;height:6px;background:var(--bor);border-radius:3px;overflow:hidden}
.lbar-fill{height:6px;border-radius:3px;transition:width .4s}
.lbar-val{font-size:12px;font-weight:600;width:64px;text-align:right}
.aib{margin:6px 10px;padding:5px 8px;border-radius:5px;font-size:12px}
.aok{background:#1e3a1e;color:var(--ok)}.awn{background:#3a2a0f;color:var(--wn)}.aal{background:#3a0f0f;color:var(--al)}.alr{background:#1e2a3a;color:var(--acc)}.aid{background:var(--bor);color:var(--mu)}
.pb{height:4px;background:var(--bor);margin:0 10px 4px;border-radius:2px}
.pbi{height:4px;background:var(--acc);border-radius:2px;transition:width .5s}
.np-strip{display:flex;align-items:center;gap:8px;padding:6px 10px;border-top:1px solid var(--bor);background:#0d1218}
.np-art{width:36px;height:36px;border-radius:4px;object-fit:cover}
.np-text{flex:1;min-width:0}
.np-title{font-size:12px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.np-sub{font-size:11px;color:var(--mu);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.listen-strip{display:flex;align-items:center;gap:5px;padding:6px 10px;border-top:1px solid var(--bor);flex-wrap:wrap}
.hist-wrap{border-top:1px solid var(--bor)}
.hist-toggle{width:100%;text-align:left;background:none;border:none;color:var(--mu);font-size:12px;padding:6px 10px;cursor:pointer}
.hist-toggle:hover{color:var(--tx)}
.hev{padding:4px 10px;border-bottom:1px solid var(--bor);font-size:11px;color:var(--mu)}
.hSILENCE,.hsilence{color:#93c5fd}.hCLIP,.hclip{color:#fca5a5}.hHISS,.hhiss{color:#fde68a}
.hAI_ALERT,.hai_alert{color:#fca5a5}.hAI_WARN,.hai_warn{color:#fde68a}
.hRTP_LOSS,.hrtp_loss{color:#c4b5fd}.hPTP,.hptp{color:#86efac}
.btn{display:inline-block;padding:4px 10px;border-radius:5px;font-size:11px;cursor:pointer;border:none;text-decoration:none}
.bp{background:var(--acc);color:#fff}.bg{background:var(--bor);color:var(--tx)}.bd{background:var(--al);color:#fff}.bw{background:#7c2d12;color:#fca5a5}.bs{padding:3px 9px;font-size:12px}.nav-active{background:var(--acc)!important;color:#fff!important}
.ptp-bar{padding:10px 16px;border-top:1px solid var(--bor);display:flex;gap:16px;flex-wrap:wrap;font-size:12px;color:var(--mu)}
.ptp-bar span{color:var(--tx)}
.offline-banner{padding:18px;text-align:center;color:var(--al);font-size:13px;background:#1a0a0a;border-radius:6px;margin:10px 14px}
.hist{display:none;padding:10px 16px;border-top:1px solid var(--bor);font-size:12px}
.hist.open{display:block}
.hist-entry{padding:4px 0;border-bottom:1px solid var(--bor);color:var(--mu)}
.hist-entry b{color:var(--tx)}
.no-sites{padding:60px;text-align:center;color:var(--mu)}
.rtp-ok{color:var(--ok)}.rtp-wn{color:var(--wn)}.rtp-al{color:var(--al)}
.rds-rt-wrap{display:block;max-width:170px;overflow:hidden;white-space:nowrap;text-align:right}
.rds-rt-static{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rds-rt-scroll{display:inline-flex;align-items:center;white-space:nowrap;min-width:max-content;animation:rds-rt-marquee 14s linear infinite}
.rds-rt-scroll span{display:inline-block;padding-right:2.5rem}
@keyframes rds-rt-marquee{0%{transform:translateX(0)}100%{transform:translateX(-50%)}}
</style>
<script nonce="{{csp_nonce()}}">
function aiClass(s){return s.includes('ALERT')?'ai-al':s.includes('WARN')?'ai-wn':'ai-ok';}
function rtpClass(p){return p>=2?'rtp-al':p>=0.5?'rtp-wn':'rtp-ok';}
function dotClass(s){return s==='OK'?'dot-ok':s.includes('WARN')?'dot-wn':s.includes('ALERT')?'dot-al':'dot-off';}
function siteStatus(streams){
  if(streams.some(s=>s.ai_status.includes('ALERT')))return 'ALERT';
  if(streams.some(s=>s.ai_status.includes('WARN')))return 'WARN';
  return 'OK';
}
function escapeHtml(s){return String(s).replace(/[&<>"']/g,function(c){return({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]);});}
function fmt(ts){if(!ts)return'—';const d=new Date(ts*1000);return d.toLocaleTimeString();}
function ago(s){if(s<5)return'just now';if(s<60)return s+'s ago';return Math.round(s/60)+'m ago';}
function toggleHist(id){const el=document.getElementById(id);el.classList.toggle('open');}
let liveAudio={};
function toggleLive(siteIdx,streamIdx,btn){
  const key=siteIdx+'-'+streamIdx;
  const audioId='audio-'+key;
  let el=document.getElementById(audioId);
  if(liveAudio[key]){
    liveAudio[key]=false;
    if(el){el.pause();el.src='';el.remove();}
    btn.textContent='▶ Live';btn.className='btn bp';
    return;
  }
  liveAudio[key]=true;
  btn.textContent='⏹ Stop';btn.className='btn bg';
  const url=btn.dataset.url;
  if(!el){el=document.createElement('audio');el.id=audioId;el.controls=true;
    el.autoplay=true;el.style.cssText='height:24px;flex:1;min-width:0;margin-top:4px';
    btn.parentNode.appendChild(el);}
  // Set type so browser knows codec before data arrives
  el.type='audio/mpeg';
  el.src=url;
  el.load();
  el.play().catch(()=>{});
}// ── Hub AJAX refresh ──────────────────────────────────────────
function hubRefresh(){
  fetch('/hub/data').then(r=>r.json()).then(data=>{
    data.sites.forEach(function(site){
      var sid = 'site-' + site.site.replace(/ /g,'_').replace(/[.]/g,'_').replace(/-/g,'_');
      var card = document.getElementById(sid);
      if(!card) return;
      var dot = card.querySelector('.site-header .dot');
      if(dot) dot.className='dot dot-'+(site.site_status==='OK'?'ok':site.site_status==='WARN'?'wn':site.site_status==='ALERT'?'al':'off');
      var meta = card.querySelector('.site-meta');
      if(meta) meta.textContent = 'Last seen: ' + agoJS(site.age_s);
      // Update version badge colour against hub build
      var vbadge = card.querySelector('.site-version-badge');
      if(vbadge && site.build){
        var match = (site.build === data.hub_build);
        vbadge.style.background = match ? '#1e2433' : '#3a2a0f';
        vbadge.title = match ? 'Version match' : 'Version mismatch — hub is '+data.hub_build;
      }
      (site.streams||[]).forEach(function(s, i){
        var sc = card.querySelector('.sc[data-idx="'+i+'"]');
        if(!sc) return;
        var ai = s.ai_status || '';
        var ph = s.ai_phase  || '';
        var sdot = sc.querySelector('.dot');
        if(sdot){ var dc='did'; if(ai.includes('[ALERT]'))dc='dal'; else if(ai.includes('[WARN]'))dc='dwn'; else if(ph==='learning')dc='dlr'; else if(s.enabled)dc='dok'; sdot.className='dot '+dc; }
        var lev = s.level_dbfs;
        var lpct = Math.min(Math.max((lev+80)/80*100,0),100);
        var lcol = lev<=-55?'var(--al)':lev<=-20?'var(--wn)':'var(--ok)';
        var lbar = sc.querySelector('.sc-lbar'); if(lbar){lbar.style.width=lpct+'%';lbar.style.background=lcol;}
        var lval = sc.querySelector('.sc-level'); if(lval){lval.textContent=lev+' dB';lval.style.color=lcol;}
        var fmt = sc.querySelector('.sc-fmt'); if(fmt) fmt.textContent=s.format||'—';
        var rtRow = sc.querySelector('.sc-rt-row');
        var rtWrap = sc.querySelector('.sc-rt-wrap');
        var rtText = sc.querySelector('.sc-rt-text');
        var rtVal = (s.fm_rds_rt || '').trim();
        if(rtRow && rtWrap){
          rtRow.style.display = rtVal ? '' : 'none';
          rtWrap.title = rtVal;
          if(rtText){
            if(rtVal.length > 40){
              rtText.className = 'rds-rt-scroll sc-rt-text';
              rtText.innerHTML = '<span>🎵 '+escapeHtml(rtVal)+'</span><span aria-hidden="true">🎵 '+escapeHtml(rtVal)+'</span>';
            }else{
              rtText.className = 'rds-rt-static sc-rt-text';
              rtText.textContent = rtVal ? '🎵 ' + rtVal : '—';
            }
          }
        }
        var rtp = sc.querySelector('.sc-rtp');
        if(rtp){ rtp.textContent=s.rtp_loss_pct+'%'; rtp.className='sc-rtp '+(s.rtp_loss_pct>=2?'rtp-al':s.rtp_loss_pct>=0.5?'rtp-wn':'rtp-ok'); }
        var sla = sc.querySelector('.sc-sla');
        if(sla && s.sla_pct!=null){ sla.textContent=s.sla_pct+'%'; sla.style.color=s.sla_pct>=99?'var(--ok)':'var(--wn)'; }
        var aib = sc.querySelector('.aib');
        if(aib){
          var ac='aid'; if(ph==='learning')ac='alr'; else if(ai.includes('[ALERT]'))ac='aal'; else if(ai.includes('[WARN]'))ac='awn'; else if(ai.includes('[OK]'))ac='aok';
          aib.className='aib '+ac;
          aib.textContent='🤖 '+(ai||'Waiting…');
        }
      });
      var banner = card.querySelector('.offline-banner');
      var streams = card.querySelector('.streams');
      if(!site.online){
        if(!banner){ banner=document.createElement('div'); banner.className='offline-banner'; var sh=card.querySelector('.site-header'); if(sh) sh.after(banner); }
        banner.textContent = '⚠ No heartbeat for '+Math.round(site.age_s)+'s — site may be down';
        if(streams) streams.style.opacity='0.35';
      } else {
        if(banner) banner.remove();
        if(streams) streams.style.opacity='';
      }
    });
    var knownIds = Array.from(document.querySelectorAll('.site-card')).map(c=>c.id);
    var incoming = data.sites.map(s=>'site-'+s.site.replace(/ /g,'_').replace(/[.]/g,'_').replace(/-/g,'_'));
    // Only reload if new site appeared AND we already have cards rendered
    if(knownIds.length > 0 && incoming.some(id=>!knownIds.includes(id))) location.reload();
  }).catch(()=>{});
}
function agoJS(s){ s=Math.round(s||0); if(s<5)return'just now'; if(s<60)return s+'s ago'; return Math.round(s/60)+'m ago'; }

// CSP-safe button wiring for hub listen buttons
document.addEventListener('click', function(e){
  var btn = e.target.closest('[data-action]');
  if(!btn) return;
  if(btn.dataset.action !== 'live') return;
  e.preventDefault();
  toggleLive(btn.dataset.sidx, btn.dataset.site, btn);
});

// Only start polling after the DOM is fully rendered
document.addEventListener('DOMContentLoaded', function(){
  setTimeout(function(){ setInterval(hubRefresh, 5000); }, 2000);
});
</script>
<link rel="icon" type="image/x-icon" href="/favicon.ico"></head><body>
{{ topnav("hub") }}
<div style="padding:8px 20px;background:var(--sur);border-bottom:1px solid var(--bor);display:flex;gap:7px;align-items:center">
  <span style="font-size:13px;font-weight:600">🛰 Hub Dashboard</span>
  <span class="badge">{{sites|length}} site{{"s" if sites|length!=1 else ""}}</span>
  <div style="margin-left:auto"><a href="/hub/reports" class="btn bp bs">📋 Hub Reports</a></div>
</div>
<main>
{% if not sites %}
  <div class="no-sites">
    <div style="font-size:48px;margin-bottom:12px">📡</div>
    <div style="font-size:18px;font-weight:600;margin-bottom:8px">Waiting for sites</div>
    <div>No heartbeats received yet. Configure each site to point to this hub and enable hub reporting in their Settings.</div>
  </div>
{% else %}
{% for site in sites %}
{% set st = site.site_status %}
<div class="site-card" id="site-{{site.site|replace(' ','_')|replace('.','_')|replace('-','_')}}">
  <div class="site-header">
    <span class="dot {{'dot-ok' if st=='OK' else 'dot-wn' if st=='WARN' else 'dot-al' if st=='ALERT' else 'dot-off'}}"></span>
    <span class="site-name">{{site.site}}</span>
    {% if site.online %}
      <span class="badge" style="background:{{'#1e3a5f' if st=='OK' else '#3a2a0f' if st=='WARN' else '#3a0f0f'}}">
        {{st}}
      </span>
      {% if site.running %}
      <span class="badge" style="background:#1e2a1e">▶ Running</span>
      {% else %}
      <span class="badge" style="background:#2a2a1e;color:var(--mu)">⏸ Stopped</span>
      {% endif %}
      {% if site.build %}
      <span class="badge site-version-badge" style="background:{{' #1e2433' if site.build==build else '#3a2a0f'}};font-size:10px"
            title="{{'Version match' if site.build==build else 'Version mismatch — hub is '+build}}">{{site.build}}</span>
      {% endif %}
    {% else %}
      <span class="badge" style="background:#2a1e1e;color:var(--al)">OFFLINE</span>
    {% endif %}
    <span class="site-meta">Last seen: {{ago(site.age_s)}}</span>
    {% if site.health_pct is defined %}
    <span class="site-meta" style="margin-left:8px;color:{{'var(--ok)' if site.health_pct>=99 else ('var(--wn)' if site.health_pct>=95 else 'var(--al)')}}" title="Heartbeat reliability">⚡ {{site.health_pct}}%</span>
    {% endif %}
    {% if site.consecutive_missed > 0 %}
    <span class="site-meta" style="color:var(--wn);margin-left:4px" title="Consecutive missed heartbeats">⚠ {{site.consecutive_missed}} missed</span>
    {% endif %}
  </div>

  {% if not site.online %}
    <div class="offline-banner">⚠ No heartbeat received for {{site.age_s}}s — site may be down</div>
  {% else %}
    <div class="streams">
    {% for s in site.streams %}
    {% set i = loop.index0 %}
    {% set ai = s.ai_status or 'Idle' %}
    {% set lev = s.level_dbfs %}
    {% set lpct = [(lev+80)/80*100, 100]|min|int %}
    {% set lcol = 'var(--al)' if lev<=-55 else ('var(--wn)' if lev<=-20 else 'var(--ok)') %}
    {% set ai  = s.ai_status or '' %}
    {% set ph  = s.ai_phase  or '' %}
    {% set dc  = 'dok' %}
    {% if '[ALERT]' in ai %}{% set dc='dal' %}{% elif '[WARN]' in ai %}{% set dc='dwn' %}{% elif ph=='learning' %}{% set dc='dlr' %}{% endif %}
    <div class="sc" data-idx="{{i}}">

      {# Header #}
      <div class="sc-name">
        <span class="dot {{dc}}"></span>
        <strong style="font-size:12px">{{s.name}}</strong>
        <span style="font-size:10px;color:var(--mu);margin-left:auto;overflow:hidden;text-overflow:ellipsis;max-width:110px;white-space:nowrap">{{s.device_index or ''}}</span>
      </div>

      {# Level bar #}
      <div class="lbar-wrap" style="padding:4px 10px">
        <span style="font-size:11px;color:var(--mu);width:28px">Lvl</span>
        <div class="lbar-track"><div class="lbar-fill sc-lbar" style="width:{{lpct}}%;background:{{lcol}}"></div></div>
        <span class="sc-level lbar-val" style="color:{{lcol}}">{{lev}} dB</span>
      </div>

      {# Info rows #}
      <div style="padding:0 10px 4px">
        <div class="sc-row">Format <span class="sc-fmt" style="font-size:11px;color:var(--mu)">{{s.format or '—'}}</span></div>
        <div class="sc-row">RTP Loss
          <span class="sc-rtp {{rtpClass(s.rtp_loss_pct)}}">{{s.rtp_loss_pct}}%
            <span style="color:var(--mu);font-size:11px"> {{s.rtp_total or 0}} pkts</span>
          </span>
        </div>
        {% if s.sla_pct is not none %}
        <div class="sc-row">SLA <span class="sc-sla" style="color:{{'var(--ok)' if s.sla_pct>=99 else 'var(--wn)'}}">{{s.sla_pct}}%</span></div>
        {% endif %}
        <div class="sc-row">Alerts
          <span style="font-size:12px">
            {%if s.alert_on_silence%}<span title="Silence">🔇</span>{%endif%}
            {%if s.alert_on_hiss%}<span title="Hiss">〰</span>{%endif%}
            {%if s.alert_on_clip%}<span title="Clip">📈</span>{%endif%}
            {%if s.ai_monitor%}<span title="AI">🤖</span>{%endif%}
          </span>
        </div>
        {% if s.device_index and s.device_index.lower().startswith('dab://') %}
        <div class="sc-row">DAB SNR <span style="color:{{'var(--ok)' if s.dab_snr>=12 else 'var(--wn)' if s.dab_snr>=6 else 'var(--al)'}}">📶 {{s.dab_snr}} dB</span></div>
        <div class="sc-row">Signal  <span>
          {% set db = s.dab_sig|default(-120) %}
          {% set bars = 0 %}
          {% if db >= -55 %}{% set bars = 5 %}
          {% elif db >= -65 %}{% set bars = 4 %}
          {% elif db >= -75 %}{% set bars = 3 %}
          {% elif db >= -85 %}{% set bars = 2 %}
          {% elif db >= -95 %}{% set bars = 1 %}
          {% endif %}
          📶{{"▮"*bars}}{{"▯"*(5-bars)}} {{s.dab_sig}} dBm
        </span></div>
        <div class="sc-row">Ensemble <span style="font-size:11px">{{s.dab_ensemble or '—'}}</span></div>
        <div class="sc-row">Service <span style="font-size:11px">{{s.dab_service or '—'}}</span></div>
        {% endif %}
        {% if s.device_index and s.device_index.lower().startswith('fm://') %}
        <div class="sc-row">Signal  <span>
          {% set db = s.fm_signal_dbm|default(-120) %}
          {% set bars = 0 %}
          {% if db >= -55 %}{% set bars = 5 %}
          {% elif db >= -65 %}{% set bars = 4 %}
          {% elif db >= -75 %}{% set bars = 3 %}
          {% elif db >= -85 %}{% set bars = 2 %}
          {% elif db >= -95 %}{% set bars = 1 %}
          {% endif %}
          📶{{"▮"*bars}}{{"▯"*(5-bars)}} {{s.fm_signal_dbm}} dBFS
        </span></div>
        <div class="sc-row">Pilot <span style="color:{{'var(--ok)' if s.fm_snr_db>=12 else 'var(--wn)' if s.fm_snr_db>=6 else 'var(--al)'}}">{{s.fm_snr_db}} dB</span></div>
        <div class="sc-row">Audio <span>{% if s.fm_stereo %}🔊 Stereo{% else %}🔈 Mono{% endif %}</span></div>
        <div class="sc-row">RDS <span style="color:{{'var(--ok)' if s.fm_rds_ok else 'var(--mu)'}}">{% if s.fm_rds_ok %}📡 {{s.fm_rds_ps or 'Locked'}}{% else %}— No RDS{% endif %}</span></div>
        <div class="sc-row sc-rt-row" {% if not s.fm_rds_rt %}style="display:none"{% endif %}>Text
          <span class="rds-rt-wrap sc-rt-wrap" style="font-size:11px" title="{{s.fm_rds_rt or ''}}">
            {% if s.fm_rds_rt and s.fm_rds_rt|length > 40 %}
            <span class="rds-rt-scroll sc-rt-text">
              <span>🎵 {{s.fm_rds_rt}}</span>
              <span aria-hidden="true">🎵 {{s.fm_rds_rt}}</span>
            </span>
            {% else %}
            <span class="rds-rt-static sc-rt-text">{% if s.fm_rds_rt %}🎵 {{s.fm_rds_rt}}{% else %}—{% endif %}</span>
            {% endif %}
          </span>
        </div>
        {% endif %}
      </div>

      {# AI status bar #}
      {% if s.ai_monitor %}
      {% set ac = 'alr' if ph=='learning' else ('aal' if '[ALERT]' in ai else ('awn' if '[WARN]' in ai else ('aok' if '[OK]' in ai else 'aid'))) %}
      <div class="aib {{ac}} sc-ai">🤖 {{ai if ai else ('Waiting…' if site.running else 'Not running')}}</div>
      {% if ph=='learning' and s.ai_learn_start %}
        {% set pct = [(now - s.ai_learn_start) / site.learn_dur * 100, 100]|min|int %}
        <div class="pb"><div class="pbi" style="width:{{pct}}%"></div></div>
      {% endif %}
      {% endif %}

      {# Now Playing — fetched from remote site's API via hub proxy #}
      {% if s.nowplaying_station_id %}
      <div class="np-strip" id="np_{{loop.index0}}_{{i}}">
        <img class="np-art" id="npa_{{loop.index0}}_{{i}}" src="" alt="" style="display:none">
        <div class="np-text">
          <div class="np-title" id="npt_{{loop.index0}}_{{i}}">Loading…</div>
          <div class="np-sub"   id="npar_{{loop.index0}}_{{i}}"></div>
          <div class="np-sub"   id="nps_{{loop.index0}}_{{i}}" style="color:var(--acc)"></div>
        </div>
      </div>
      <script nonce="{{csp_nonce()}}">(function(){
        var sid='{{loop.index0}}',si='{{i}}',rpuid='{{s.nowplaying_station_id}}';
        function fnp(){fetch('/api/nowplaying/'+rpuid).then(r=>r.json()).then(function(d){
          document.getElementById('npt_'+sid+'_'+si).textContent=d.title||'—';
          document.getElementById('npar_'+sid+'_'+si).textContent=d.artist||'';
          document.getElementById('nps_'+sid+'_'+si).textContent=d.show||'';
          var a=document.getElementById('npa_'+sid+'_'+si);
          if(d.artwork){a.src='/api/nowplaying_art/'+encodeURIComponent(rpuid)+'?ts='+Date.now();a.style.display='block';} else { a.removeAttribute('src'); a.style.display='none'; }
        }).catch(()=>{});}
        fnp();setInterval(fnp,30000);
      })();</script>
      {% endif %}

      {# Listen / Clip strip #}
      <div class="listen-strip">
        <select id="hdur_{{loop.index0}}_{{i}}" style="padding:3px 5px;font-size:11px;background:#1e2433;border:1px solid var(--bor);border-radius:4px;color:var(--tx)">
          <option value="5">5s</option><option value="10" selected>10s</option>
          <option value="20">20s</option><option value="30">30s</option>
        </select>
        <a class="btn bg bs" href="/hub/site/{{site.site|urlencode}}/stream/{{i}}/clip?seconds=10" download>⬇ Clip</a>
        <button class="btn bp bs" data-sidx="{{loop.index0}}" data-site="{{i}}" data-action="live"
          data-url="/hub/site/{{site.site|urlencode}}/stream/{{i}}/live">▶ Live</button>
        <audio id="hlive_{{loop.index0}}_{{i}}" style="display:none;flex:1;min-width:0;height:24px" controls></audio>
      </div>

      {# Clip count badge #}
      {% if s.clip_count %}
      <div style="padding:4px 10px;font-size:11px;color:var(--mu);border-top:1px solid var(--bor)">
        💾 {{s.clip_count}} saved clip{{"s" if s.clip_count!=1 else ""}} on site
        <a href="/hub/reports?site={{site.site|urlencode}}&stream={{s.name|urlencode}}" style="color:var(--acc);margin-left:6px">View in reports →</a>
      </div>
      {% endif %}

      {# Recent events #}
      {% if s.history %}
      <div class="hist-wrap">
        <button class="hist-toggle" onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display?'':'block'">
          📋 Recent Events ({{s.history|length}})
        </button>
        <div style="display:none">
          {% for ev in s.history|reverse %}
          <div class="hev h{{ev.type|lower}}">
            <span style="color:var(--mu)">{{ev.ts[-8:] if ev.ts else ''}}</span>
            <span style="margin:0 4px;opacity:.5">[{{ev.type}}]</span>
            {{ev.message[:90] if ev.message else ''}}
          </div>
          {% endfor %}
        </div>
      </div>
      {% endif %}
    </div>
    {% else %}
    <div style="padding:20px;text-align:center;color:var(--mu);font-size:13px">
      {% if not site.running %}
      ⏸ Monitoring not started on this site
      {% else %}
      No inputs configured on this site
      {% endif %}
    </div>
    {% endfor %}
    </div>


    {# Comparators #}
    {% if site.comparators %}
    <div style="padding:10px 16px;border-top:1px solid var(--bor)">
      <div style="font-size:11px;color:var(--mu);margin-bottom:8px;font-weight:600">🔀 STREAM COMPARATORS</div>
      {% for c in site.comparators %}
      <div style="background:#0d1520;border-radius:6px;padding:8px 10px;margin-bottom:6px;font-size:12px">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
          <strong>{{c.pre_name}}</strong>
          <span style="color:var(--mu)">→</span>
          <strong>{{c.post_name}}</strong>
          <span style="margin-left:auto;font-size:11px;color:{{'var(--ok)' if c.status=='OK' else 'var(--al)'}}">{{c.status}}</span>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px 16px;color:var(--mu)">
          <span>Pre level  <span style="color:var(--tx)">{{c.pre_dbfs}} dBFS</span></span>
          <span>Post level <span style="color:var(--tx)">{{c.post_dbfs}} dBFS</span></span>
          <span>Gain diff  <span style="color:{{'#fb923c' if c.gain_diff_db|abs > c.gain_alert_db else 'var(--tx)'}}">{{'+' if c.gain_diff_db>=0 else ''}}{{c.gain_diff_db}} dB</span></span>
          <span>Delay      <span style="color:var(--tx)">{{c.delay_ms}} ms</span></span>
        </div>
        {% if c.aligned %}
        <div style="margin-top:6px">
          <span style="font-size:11px;color:var(--mu)">Correlation </span>
          {% set cc = c.correlation %}
          {% set cw = (cc * 100)|int %}
          {% set ccol = 'var(--ok)' if cc>=0.85 else ('var(--wn)' if cc>=0.65 else ('#fb923c' if cc>=0.40 else 'var(--al)')) %}
          <div style="display:inline-block;background:var(--bor);border-radius:3px;width:80px;height:6px;vertical-align:middle;margin:0 6px">
            <div style="width:{{cw}}%;height:6px;border-radius:3px;background:{{ccol}}"></div>
          </div>
          <span style="color:{{ccol}}">{{(cc*100)|round(0)|int}}%</span>
        </div>
        {% endif %}
        {% if c.cmp_history %}
        <div style="margin-top:6px;font-size:11px;color:var(--mu)">
          {% for ev in c.cmp_history|reverse %}
          <div>{{ev}}</div>
          {% endfor %}
        </div>
        {% endif %}
      </div>
      {% endfor %}
    </div>
    {% endif %}

    {# PTP bar #}
    {% set ptp = site.ptp %}
    <div class="ptp-bar">
      <span style="color:var(--mu)">🕐 PTP:</span>
      <span>State <span style="color:{{'var(--ok)' if ptp.state=='locked' else 'var(--wn)'}}">{{ptp.state}}</span></span>
      <span>Offset <span>{{(ptp.offset_us/1000)|round(1)}} ms</span></span>
      <span>Drift  <span style="color:{{'var(--ok)' if ptp.drift_us|abs < 1000 else 'var(--wn)'}}">{{(ptp.drift_us/1000)|round(1)}} ms</span></span>
      <span>Jitter <span>{{(ptp.jitter_us/1000)|round(1)}} ms</span></span>
      <span>GM <span style="font-size:11px">{{ptp.gm_id or '—'}}</span></span>
      <span>Last sync <span>{{fmt(ptp.last_sync)}}</span></span>
    </div>
  {% endif %}
</div>
{% endfor %}
{% endif %}
</main></body></html>"""

# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__=="__main__":
    ort=_try_import("onnxruntime")
    if ort is None:
        print("[!] onnxruntime not found — AI disabled.  pip install onnxruntime")
    else:
        print(f"[OK] onnxruntime {ort.__version__}")

    # Start now playing poller immediately so dropdown works before monitoring starts
    nowplaying_poller.start(monitor.app_cfg.nowplaying_country or "GB")
    print(f"[NowPlaying] Poller started (country={monitor.app_cfg.nowplaying_country or 'GB'})")

    # Start hub client immediately (independent of monitoring start)
    monitor.start_hub_client()

    cfg  = monitor.app_cfg
    mode = cfg.hub.mode
    import ssl as _ssl

    # ── Port and SSL selection ──────────────────────────────────────────────────
    # Hub/both with cert  → HTTPS on 443, HTTP→HTTPS redirect on 80
    # Hub/both no cert    → HTTP on 80
    # Client              → HTTP on 5000 (behind NAT, not public-facing)

    is_hub   = mode in ("hub", "both")
    ssl_ctx  = None
    port     = 5000  # default for client mode

    if is_hub:
        tls_ready = (cfg.tls_enabled and cfg.tls_cert_path and cfg.tls_key_path
                     and os.path.exists(cfg.tls_cert_path)
                     and os.path.exists(cfg.tls_key_path))

        if cfg.tls_enabled and not tls_ready:
            print(f"[{BUILD}] WARNING: TLS enabled but cert files not found — falling back to HTTP on port 80")

        if tls_ready:
            port = 443
        else:
            port = 80

        # ── Check port binding BEFORE starting any threads ───────────────────
        import socket as _sock, sys as _sys
        for _p in ([443, 80] if tls_ready else [80]):
            try:
                _s = _sock.socket(); _s.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 1)
                _s.bind(("0.0.0.0", _p)); _s.close()
            except OSError:
                print(f"[{BUILD}] ✗ Cannot bind port {_p} — insufficient permissions.")
                print(f"[{BUILD}]   Find your real Python binary:  readlink -f $(which python3)")
                print(f"[{BUILD}]   Then run:  sudo setcap cap_net_bind_service=+ep /path/to/python3.x")
                _sys.exit(1)

        if tls_ready:
            ssl_ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
            ssl_ctx.load_cert_chain(cfg.tls_cert_path, cfg.tls_key_path)
            app.config["SESSION_COOKIE_SECURE"] = True
            print(f"[{BUILD}] HTTPS on port 443  ({cfg.tls_domain})")

            # ── HTTP→HTTPS redirect + ACME challenge server on port 80 ──────────
            from flask import Flask as _Flask, redirect as _redirect
            _redirect_app = _Flask("_redirect")

            @_redirect_app.route("/.well-known/acme-challenge/<token>")
            def _acme_pass(token):
                key_auth = _acme_challenges.get(token)
                if key_auth:
                    return key_auth, 200, {"Content-Type": "text/plain"}
                return "Not found", 404

            @_redirect_app.route("/", defaults={"path": ""})
            @_redirect_app.route("/<path:path>")
            def _http_redirect(path):
                target = f"https://{cfg.tls_domain}" + ("/" + path if path else "/")
                return _redirect(target, 301)

            def _run_redirect():
                try:
                    from waitress import serve as _ws
                    _ws(_redirect_app, host="0.0.0.0", port=80,
                        threads=2, ident="redirect")
                except ImportError:
                    _redirect_app.run(host="0.0.0.0", port=80,
                                      debug=False, use_reloader=False)

            threading.Thread(target=_run_redirect, daemon=True, name="HTTP-Redirect").start()
            print(f"[{BUILD}] HTTP→HTTPS redirect on port 80")

        else:
            print(f"[{BUILD}] HTTP on port 80")
            if cfg.auth.enabled:
                print(f"[{BUILD}] WARNING: Auth is enabled but running HTTP — session cookie is not Secure. Enable HTTPS for production use.")
            print(f"[{BUILD}] Tip: get a cert in Settings → HTTPS to enable HTTPS on port 443")

        suffix = "/hub" if mode == "hub" else "  (hub at /hub)"
        proto  = "https" if tls_ready else "http"
        label  = "HUB" if mode == "hub" else "CLIENT+HUB"
        print(f"[{BUILD}] {label} mode — {proto}://0.0.0.0:{port}{suffix}")

    else:
        # Client mode — stays on 5000, behind NAT
        print(f"[{BUILD}] CLIENT mode — http://0.0.0.0:{port}")

    # ── Server startup ───────────────────────────────────────────────────────
    # Use waitress (production WSGI) if available and no SSL needed.
    # Fall back to Flask dev server when SSL is active (waitress doesn't
    # handle TLS directly) or if waitress isn't installed.
    if ssl_ctx:
        # HTTPS — Flask dev server with SSL context
        # For production scale, put nginx/caddy in front instead
        print(f"[{BUILD}] Starting Flask+SSL server on port {port}")
        app.run(host="0.0.0.0", port=port, debug=False, threaded=True,
                ssl_context=ssl_ctx)
    else:
        try:
            from waitress import serve
            from waitress.adjustments import Adjustments
            # Waitress config — tune for broadcast monitoring workload:
            #   threads=8     : handles concurrent browser tabs + hub clients
            #   channel_timeout=120: long-lived audio streams need more time
            #   asyncore_use_poll=True: more efficient on Linux
            print(f"[{BUILD}] Starting waitress WSGI server on port {port} (threads=8)")
            serve(app,
                  host="0.0.0.0",
                  port=port,
                  threads=8,
                  channel_timeout=120,
                  asyncore_use_poll=True,
                  ident=BUILD)
        except ImportError:
            print(f"[{BUILD}] waitress not found — using Flask dev server")
            print(f"[{BUILD}] Install for better performance:  pip install waitress")
            app.run(host="0.0.0.0", port=port, debug=False, threaded=True)