import html
import logging
import os

import requests
from pwnagotchi import plugins


class PwnagotchiHashcatWPA(plugins.Plugin):
    __author__ = 'EpicNori (via Antigravity AI)'
    __version__ = '1.1.0'
    __license__ = 'GPL3'
    __description__ = 'Uploads captured handshakes automatically to a self-hosted hashcat-wpa-server instance over Bluetooth Tethering.'

    def __init__(self):
        self.ready = False

    def _base_url(self):
        return (self.options.get('url') or '').rstrip('/')

    def _upload_url(self):
        url = self._base_url()
        if url and not url.endswith('/api/upload'):
            url = url + '/api/upload'
        return url

    def _render_page(self, test_result=None):
        base_url = html.escape(self._base_url() or '(not set)')
        upload_url = html.escape(self._upload_url() or '(not set)')
        username = html.escape(self.options.get('username') or '(not set)')
        status_text = "Ready" if self.ready else "Config incomplete"
        status_color = "#2e7d32" if self.ready else "#c62828"

        test_html = ""
        if test_result:
            result_color = "#2e7d32" if test_result.get("ok") else "#c62828"
            test_html = f"""
            <div style="margin-top:16px;padding:12px;border-radius:10px;background:#f7f7f7;border:1px solid #ddd;">
              <strong style="color:{result_color};">Connectivity test: {html.escape(test_result.get('label', 'Unknown'))}</strong>
              <div style="margin-top:6px;">{html.escape(test_result.get('message', ''))}</div>
            </div>
            """

        return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Hashcat WPA Server</title>
</head>
<body style="font-family:Arial,sans-serif;background:#f3f4f6;color:#1f2937;margin:0;">
  <div style="max-width:820px;margin:0 auto;padding:24px;">
    <div style="background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:24px;box-shadow:0 8px 24px rgba(0,0,0,0.06);">
      <h1 style="margin:0 0 8px 0;">Hashcat WPA Server</h1>
      <p style="margin:0 0 18px 0;color:#4b5563;">Upload captured handshakes from your Pwnagotchi to your cracking server.</p>

      <div style="padding:12px 14px;border-radius:10px;background:#f9fafb;border:1px solid #e5e7eb;">
        <strong>Status:</strong>
        <span style="color:{status_color};">{status_text}</span>
      </div>

      <h2 style="margin:22px 0 8px 0;">Current Settings</h2>
      <div style="line-height:1.7;">
        <div><strong>Server URL:</strong> <code>{base_url}</code></div>
        <div><strong>Upload URL:</strong> <code>{upload_url}</code></div>
        <div><strong>Username:</strong> <code>{username}</code></div>
      </div>

      <div style="margin-top:18px;">
        <a href="/plugins/pwnagotchi_hashcat_wpa/test" style="display:inline-block;padding:10px 14px;border-radius:10px;background:#111827;color:#fff;text-decoration:none;">Test Server Connection</a>
      </div>
      {test_html}

      <h2 style="margin:22px 0 8px 0;">Quick Setup</h2>
      <pre style="white-space:pre-wrap;word-wrap:break-word;background:#111827;color:#f9fafb;padding:14px;border-radius:10px;">main.plugins.pwnagotchi_hashcat_wpa.enabled = true
main.plugins.pwnagotchi_hashcat_wpa.url = "http://100.x.x.x:9111"
main.plugins.pwnagotchi_hashcat_wpa.username = "admin"
main.plugins.pwnagotchi_hashcat_wpa.password = "changeme"</pre>

      <h2 style="margin:22px 0 8px 0;">Easy Tailscale Path</h2>
      <ol style="padding-left:20px;line-height:1.7;">
        <li>Install Tailscale on the hashcat server and on the Pwnagotchi.</li>
        <li>Log both devices into the same tailnet.</li>
        <li>Use the server's Tailscale IP in the plugin URL, like <code>http://100.x.x.x:9111</code>.</li>
        <li>Turn on phone tethering when you are mobile so the Pwnagotchi has internet.</li>
      </ol>

      <p style="margin-top:18px;color:#6b7280;">Tip: if this page opens from the plugins list, the web UI hook is working correctly.</p>
    </div>
  </div>
</body>
</html>"""

    def on_loaded(self):
        if 'url' not in self.options or 'username' not in self.options or 'password' not in self.options:
            logging.error("[HashcatWPAServer] URL, username, or password not set in config.toml")
            self.ready = False
            return
        self.ready = True
        logging.info("[HashcatWPAServer] Plugin successfully loaded.")

    def on_webhook(self, path, request):
        test_result = None
        normalized_path = (path or '').strip('/')

        if normalized_path == 'test':
            base_url = self._base_url()
            if not base_url:
                test_result = {
                    "ok": False,
                    "label": "Failed",
                    "message": "The plugin URL is not configured yet."
                }
            else:
                try:
                    response = requests.get(base_url, timeout=10, allow_redirects=True)
                    test_result = {
                        "ok": response.status_code < 500,
                        "label": "OK" if response.status_code < 500 else "Failed",
                        "message": f"Server responded with HTTP {response.status_code} from {base_url}."
                    }
                except Exception as exc:
                    test_result = {
                        "ok": False,
                        "label": "Failed",
                        "message": f"Could not reach {base_url}: {exc}"
                    }

        return self._render_page(test_result=test_result)

    def on_handshake(self, agent, filename, access_point, client_station):
        try:
            url = self._upload_url()
            username = self.options.get('username')
            password = self.options.get('password')

            with open(filename, 'rb') as f:
                files = {'capture': (os.path.basename(filename), f, 'application/vnd.tcpdump.pcap')}
                response = requests.post(
                    url,
                    auth=(username, password),
                    files=files,
                    timeout=30
                )
            if response.status_code == 200:
                logging.info(f"[HashcatWPAServer] Successfully uploaded {filename}")
            else:
                logging.error(f"[HashcatWPAServer] Failed to upload {filename}. Server responded with Status: {response.status_code}")
        except Exception as e:
            logging.error(f"[HashcatWPAServer] Exception during upload: {e}")
