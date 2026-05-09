import collections
import datetime
import html
import logging
import os
import re

import requests
from pwnagotchi import plugins

# ---------------------------------------------------------------------------
# In-memory upload event log (most-recent-first, capped at 40 entries)
# ---------------------------------------------------------------------------
_MAX_LOG = 40
_upload_log = collections.deque(maxlen=_MAX_LOG)


def _log_event(ok: bool, message: str):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _upload_log.appendleft({"ts": ts, "ok": ok, "message": message})
    if ok:
        logging.info("[HashcatWPAServer] %s", message)
    else:
        logging.error("[HashcatWPAServer] %s", message)


# ---------------------------------------------------------------------------

class PwnagotchiHashcatWPA(plugins.Plugin):
    __author__ = 'EpicNori (via Antigravity AI)'
    __version__ = '1.2.0'
    __license__ = 'GPL3'
    __description__ = 'Uploads captured handshakes automatically to a self-hosted hashcat-wpa-server instance.'

    def __init__(self):
        self.ready = False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _base_url(self):
        return (self.options.get('url') or '').rstrip('/')

    def _upload_url(self):
        base = self._base_url()
        return f"{base}/api/upload" if base else ''

    # ------------------------------------------------------------------
    # Config auto-injection
    # ------------------------------------------------------------------

    _CONFIG_PATH = '/etc/pwnagotchi/config.toml'

    _DEFAULT_CONFIG_BLOCK = """
# --- pwnagotchi_hashcat_wpa plugin (auto-added on first start) ---
main.plugins.pwnagotchi_hashcat_wpa.enabled = true
main.plugins.pwnagotchi_hashcat_wpa.url = "http://100.x.x.x:9111"
main.plugins.pwnagotchi_hashcat_wpa.username = "admin"
main.plugins.pwnagotchi_hashcat_wpa.password = "changeme"
# ----------------------------------------------------------------
"""

    def _inject_default_config(self):
        """Append default plugin keys to config.toml if they are not already present."""
        config_path = self._CONFIG_PATH
        try:
            content = open(config_path).read() if os.path.exists(config_path) else ''
            if re.search(r'main\.plugins\.pwnagotchi_hashcat_wpa\.', content):
                return  # already configured – nothing to do
            with open(config_path, 'a') as f:
                f.write(self._DEFAULT_CONFIG_BLOCK)
            logging.info(
                "[HashcatWPAServer] Default plugin config injected into %s. "
                "Please edit the URL, username, and password to match your server.",
                config_path
            )
        except Exception as exc:
            logging.warning("[HashcatWPAServer] Could not inject default config: %s", exc)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_loaded(self):
        self._inject_default_config()
        if not self.options.get('url') or not self.options.get('username') or not self.options.get('password'):
            logging.warning(
                "[HashcatWPAServer] URL, username, or password not configured. "
                "Edit %s and restart pwnagotchi.",
                self._CONFIG_PATH
            )
            self.ready = False
            return
        self.ready = True
        logging.info("[HashcatWPAServer] Plugin loaded. Upload target: %s", self._upload_url())

    # ------------------------------------------------------------------
    # Handshake upload
    # ------------------------------------------------------------------

    def on_handshake(self, agent, filename, access_point, client_station):
        if not self.ready:
            _log_event(False, f"Skipped {os.path.basename(filename)}: plugin not ready (check config).")
            return

        url      = self._upload_url()
        username = self.options.get('username', '')
        password = self.options.get('password', '')
        basename = os.path.basename(filename)

        try:
            with open(filename, 'rb') as fh:
                response = requests.post(
                    url,
                    auth=(username, password),
                    files={'capture': (basename, fh, 'application/vnd.tcpdump.pcap')},
                    timeout=30,
                )
            if response.status_code == 200:
                _log_event(True, f"Uploaded {basename} → task scheduled. Server: {response.text[:120]}")
            else:
                _log_event(
                    False,
                    f"Upload of {basename} failed: HTTP {response.status_code} — {response.text[:200]}"
                )
        except Exception as exc:
            _log_event(False, f"Upload of {basename} raised exception: {exc}")

    # ------------------------------------------------------------------
    # Web UI
    # ------------------------------------------------------------------

    def _render_log_rows(self):
        if not _upload_log:
            return '<tr><td colspan="3" style="text-align:center;color:#6b7280;padding:18px;">No uploads recorded yet.</td></tr>'
        rows = []
        for entry in _upload_log:
            color  = '#4ade80' if entry['ok'] else '#f87171'
            badge  = '✓ OK'   if entry['ok'] else '✗ ERR'
            msg    = html.escape(entry['message'])
            ts     = html.escape(entry['ts'])
            rows.append(
                f'<tr>'
                f'<td style="padding:6px 10px;color:#9ca3af;font-size:0.78rem;white-space:nowrap;">{ts}</td>'
                f'<td style="padding:6px 10px;"><span style="color:{color};font-weight:600;">{badge}</span></td>'
                f'<td style="padding:6px 10px;word-break:break-word;font-size:0.82rem;">{msg}</td>'
                f'</tr>'
            )
        return ''.join(rows)

    def _render_page(self, test_result=None):
        base_url    = html.escape(self._base_url() or '(not set)')
        upload_url  = html.escape(self._upload_url() or '(not set)')
        username    = html.escape(self.options.get('username') or '(not set)')
        status_text = 'Ready' if self.ready else 'Config incomplete'
        status_dot  = '#4ade80' if self.ready else '#f87171'

        test_html = ''
        if test_result:
            rc = '#4ade80' if test_result.get('ok') else '#f87171'
            test_html = (
                f'<div style="margin-top:14px;padding:12px 16px;border-radius:10px;'
                f'background:#1e293b;border:1px solid #334155;">'
                f'<span style="color:{rc};font-weight:600;">⬤ {html.escape(test_result.get("label",""))}</span>'
                f'<span style="color:#94a3b8;margin-left:10px;">{html.escape(test_result.get("message",""))}</span>'
                f'</div>'
            )

        log_rows = self._render_log_rows()

        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Hashcat WPA · Pwnagotchi Plugin</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:'Segoe UI',system-ui,sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;padding:24px 16px}}
    .card{{background:#1e293b;border:1px solid #334155;border-radius:16px;padding:24px;max-width:860px;margin:0 auto 24px}}
    h1{{font-size:1.55rem;font-weight:700;color:#f1f5f9;margin-bottom:4px}}
    h2{{font-size:1.05rem;font-weight:600;color:#cbd5e1;margin:22px 0 10px}}
    .subtitle{{color:#64748b;font-size:0.88rem;margin-bottom:22px}}
    .badge{{display:inline-flex;align-items:center;gap:6px;padding:5px 12px;border-radius:8px;
            background:#0f172a;border:1px solid #334155;font-size:0.85rem}}
    .dot{{width:8px;height:8px;border-radius:50%;display:inline-block}}
    .row{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:6px;align-items:baseline}}
    .label{{color:#64748b;font-size:0.82rem;min-width:110px}}
    code{{background:#0f172a;color:#7dd3fc;padding:2px 7px;border-radius:5px;font-size:0.82rem}}
    .btn{{display:inline-block;padding:9px 18px;border-radius:10px;background:#3b82f6;color:#fff;
          text-decoration:none;font-size:0.88rem;font-weight:600;border:none;cursor:pointer;
          transition:background .15s}}
    .btn:hover{{background:#2563eb}}
    pre{{background:#0f172a;color:#a5f3fc;padding:16px;border-radius:10px;font-size:0.8rem;
         overflow-x:auto;white-space:pre-wrap;word-break:break-word;border:1px solid #334155}}
    table{{width:100%;border-collapse:collapse;font-size:0.82rem}}
    thead th{{text-align:left;padding:7px 10px;color:#64748b;font-weight:500;
              border-bottom:1px solid #334155;font-size:0.78rem;text-transform:uppercase;letter-spacing:.05em}}
    tbody tr:nth-child(even){{background:#0f172a22}}
    tbody tr:hover{{background:#0f172a55}}
    ol{{padding-left:20px;color:#94a3b8;line-height:1.85}}
    ol code{{color:#7dd3fc}}
    .tip{{color:#475569;font-size:0.8rem;margin-top:14px}}
  </style>
</head>
<body>
  <div class="card">
    <h1>🔐 Hashcat WPA Server</h1>
    <p class="subtitle">Pwnagotchi plugin — auto-uploads handshakes for cracking</p>

    <div class="badge">
      <span class="dot" style="background:{status_dot}"></span>
      <span style="color:#e2e8f0">{status_text}</span>
    </div>

    <h2>Current Settings</h2>
    <div class="row"><span class="label">Server URL</span><code>{base_url}</code></div>
    <div class="row"><span class="label">Upload URL</span><code>{upload_url}</code></div>
    <div class="row"><span class="label">Username</span><code>{username}</code></div>

    <div style="margin-top:16px">
      <a href="/plugins/pwnagotchi_hashcat_wpa/test" class="btn">⚡ Test Connection</a>
    </div>
    {test_html}
  </div>

  <div class="card">
    <h2 style="margin-top:0">📋 Upload Log</h2>
    <p class="subtitle">Last {_MAX_LOG} upload events (most recent first)</p>
    <table>
      <thead><tr><th>Time</th><th>Status</th><th>Detail</th></tr></thead>
      <tbody>{log_rows}</tbody>
    </table>
  </div>

  <div class="card">
    <h2 style="margin-top:0">⚙️ Quick Setup</h2>
    <p style="color:#64748b;font-size:0.85rem;margin-bottom:10px">
      Add these lines to <code>/etc/pwnagotchi/config.toml</code> and restart:
    </p>
    <pre>main.plugins.pwnagotchi_hashcat_wpa.enabled = true
main.plugins.pwnagotchi_hashcat_wpa.url = "http://100.x.x.x:9111"
main.plugins.pwnagotchi_hashcat_wpa.username = "admin"
main.plugins.pwnagotchi_hashcat_wpa.password = "changeme"</pre>

    <h2>🌐 Easy Tailscale Path</h2>
    <ol>
      <li>Install Tailscale on the hashcat server and on the Pwnagotchi.</li>
      <li>Log both devices into the same tailnet (<code>tailscale up</code>).</li>
      <li>Use the server's Tailscale IP in the plugin URL — e.g. <code>http://100.x.x.x:9111</code>.</li>
      <li>Turn on phone BT tethering when mobile so the Pwnagotchi has internet.</li>
    </ol>

    <p class="tip">Tip: the cracking mode &amp; devices are controlled by the server's Admin → Settings page.</p>
  </div>
</body>
</html>"""

    def on_webhook(self, path, request):
        test_result = None
        normalized_path = (path or '').strip('/')

        if normalized_path == 'test':
            base_url = self._base_url()
            if not base_url:
                test_result = {
                    'ok': False,
                    'label': 'Not configured',
                    'message': 'The plugin URL is not set in config.toml.',
                }
            else:
                try:
                    response = requests.get(base_url, timeout=10, allow_redirects=True)
                    ok = response.status_code < 500
                    test_result = {
                        'ok': ok,
                        'label': 'Connected' if ok else 'Server error',
                        'message': f'HTTP {response.status_code} from {base_url}',
                    }
                except Exception as exc:
                    test_result = {
                        'ok': False,
                        'label': 'Unreachable',
                        'message': f'Could not reach {base_url}: {exc}',
                    }

        return self._render_page(test_result=test_result)
