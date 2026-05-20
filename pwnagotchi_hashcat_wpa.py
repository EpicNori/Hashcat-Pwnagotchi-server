import collections
import datetime
import html
import logging
import os
import re
import time

import requests
from pwnagotchi import plugins
from pwnagotchi.ui.components import LabeledValue
from pwnagotchi.ui.view import BLACK
import pwnagotchi.ui.fonts as fonts


_MAX_LOG = 40
_upload_log = collections.deque(maxlen=_MAX_LOG)
_SUPPORTED_SUFFIXES = ('.cap', '.pcap', '.pcapng', '.hccapx', '.pmkid', '.2500', '.2501', '.16800', '.16801', '.22000', '.22001')
_DEFAULT_HANDSHAKE_DIR = '/home/pi/handshakes'
_STATE_PATH = '/root/.pwnagotchi_hashcat_wpa_uploaded'
_DEFAULT_BATCH_SIZE = 8


def _log_event(ok, message):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _upload_log.appendleft({"ts": ts, "ok": ok, "message": message})
    if ok:
        logging.info("[HashcatWPAServer] %s", message)
    else:
        logging.error("[HashcatWPAServer] %s", message)


class PwnagotchiHashcatWPA(plugins.Plugin):
    __author__ = 'EpicNori (via Antigravity AI)'
    __version__ = '1.4.5'
    __license__ = 'GPL3'
    __description__ = 'Uploads captured WPA/WPA2 handshakes to a self-hosted Hashcat WPA Server.'

    _CONFIG_PATH = '/etc/pwnagotchi/config.toml'
    _PLUGIN_KEY = 'pwnagotchi_hashcat_wpa'
    _PLACEHOLDER_URL = 'http://100.x.x.x:9111'
    _DEFAULTS = {
        'enabled': True,
        'url': _PLACEHOLDER_URL,
        'username': 'admin',
        'password': 'changeme',
        'handshake_dir': _DEFAULT_HANDSHAKE_DIR,
        'upload_existing': True,
        'batch_size': _DEFAULT_BATCH_SIZE,
    }

    def __init__(self):
        self.ready = False
        self._pending_files = collections.OrderedDict()
        self._uploaded_fingerprints = set()
        self._last_status = 'Hashcat WPA ready'
        self._display_key = 'hashcat_wpa_status'

    def _base_url(self):
        return (self.options.get('url') or '').rstrip('/')

    def _upload_url(self):
        base = self._base_url()
        return f"{base}/api/upload" if base else ''

    def _heartbeat_url(self):
        base = self._base_url()
        return f"{base}/api/pwnagotchi/heartbeat" if base else ''

    def _is_placeholder_url(self):
        return self._base_url() in ('', self._PLACEHOLDER_URL)

    def _is_configured(self):
        return (
            not self._is_placeholder_url()
            and bool(self.options.get('username'))
            and bool(self.options.get('password'))
        )

    def _status(self):
        if self._is_placeholder_url():
            return 'Needs server URL', '#f59e0b'
        if not self.options.get('username') or not self.options.get('password'):
            return 'Needs login', '#f59e0b'
        return 'Ready', '#22c55e'

    def _set_status(self, agent, message):
        self._last_status = message
        logging.info("[HashcatWPAServer] %s", message)
        try:
            view = agent.view()
            if hasattr(view, 'set'):
                view.set('status', message)
            if hasattr(view, 'update'):
                view.update(force=True)
        except Exception as exc:
            logging.debug("[HashcatWPAServer] Could not update Pwnagotchi status text: %s", exc)

    def _display_value(self):
        if not self._is_configured():
            return 'SET URL'
        elif self._pending_files:
            return f'QUEUED {len(self._pending_files)}'
        return 'READY'

    def _option_bool(self, key, default=False):
        value = self.options.get(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ('1', 'true', 'yes', 'on')

    def _option_int(self, key, default):
        try:
            return max(1, int(self.options.get(key, default)))
        except (TypeError, ValueError):
            return default

    def _handshake_dir(self):
        return self.options.get('handshake_dir') or _DEFAULT_HANDSHAKE_DIR

    def _file_fingerprint(self, path):
        try:
            stat = os.stat(path)
        except OSError:
            return ''
        return f"{os.path.abspath(path)}|{stat.st_size}|{int(stat.st_mtime)}"

    def _load_uploaded_state(self):
        self._uploaded_fingerprints = set()
        try:
            with open(_STATE_PATH, 'r', encoding='utf-8', errors='replace') as fh:
                self._uploaded_fingerprints = {line.strip() for line in fh if line.strip()}
        except FileNotFoundError:
            pass
        except Exception as exc:
            logging.warning("[HashcatWPAServer] Could not read upload state: %s", exc)

    def _remember_uploaded(self, paths):
        new_items = []
        for path in paths:
            fingerprint = self._file_fingerprint(path)
            if fingerprint and fingerprint not in self._uploaded_fingerprints:
                self._uploaded_fingerprints.add(fingerprint)
                new_items.append(fingerprint)
        if not new_items:
            return
        try:
            state_dir = os.path.dirname(_STATE_PATH)
            if state_dir:
                os.makedirs(state_dir, exist_ok=True)
            with open(_STATE_PATH, 'a', encoding='utf-8') as fh:
                for item in new_items:
                    fh.write(item + '\n')
        except Exception as exc:
            logging.warning("[HashcatWPAServer] Could not write upload state: %s", exc)

    def _already_uploaded(self, path):
        fingerprint = self._file_fingerprint(path)
        return bool(fingerprint and fingerprint in self._uploaded_fingerprints)

    def on_ui_setup(self, ui):
        try:
            ui.add_element(
                self._display_key,
                LabeledValue(
                    label='HWP',
                    value=self._display_value(),
                    position=(8, 96),
                    label_font=fonts.Small,
                    text_font=fonts.Small,
                    color=BLACK,
                ),
            )
        except Exception as exc:
            logging.debug("[HashcatWPAServer] Could not add Pwnagotchi display element: %s", exc)

    def on_ui_update(self, ui):
        try:
            ui.set(self._display_key, self._display_value())
        except Exception as exc:
            logging.debug("[HashcatWPAServer] Could not update Pwnagotchi display element: %s", exc)

    def on_unload(self, ui):
        try:
            ui.remove_element(self._display_key)
        except Exception:
            pass

    def _add_pending_file(self, filename):
        if not filename:
            return
        path = os.path.abspath(filename)
        if not os.path.isfile(path):
            return
        if not path.lower().endswith(_SUPPORTED_SUFFIXES):
            return
        if self._already_uploaded(path):
            return
        self._pending_files[path] = time.time()

    def _scan_existing_handshakes(self, agent=None):
        if not self.ready or not self._option_bool('upload_existing', True):
            return
        handshake_dir = self._handshake_dir()
        if not os.path.isdir(handshake_dir):
            _log_event(False, f"Existing handshake scan skipped: {handshake_dir} does not exist.")
            return

        before = len(self._pending_files)
        candidates = []
        for root, _, filenames in os.walk(handshake_dir):
            for filename in filenames:
                path = os.path.join(root, filename)
                if path.lower().endswith(_SUPPORTED_SUFFIXES):
                    candidates.append(path)
        candidates.sort(key=lambda item: (os.path.getmtime(item), item))
        for path in candidates:
            self._add_pending_file(path)
        added = len(self._pending_files) - before
        if added:
            _log_event(True, f"Queued {added} existing handshake file(s) from {handshake_dir}.")
            if agent is not None:
                self._set_status(agent, f"Queued {added} old handshake(s)")

    def _upload_pending_files(self, agent=None):
        if not self.ready:
            return
        if not self._pending_files:
            return

        batch_size = self._option_int('batch_size', _DEFAULT_BATCH_SIZE)
        paths = list(self._pending_files.keys())[:batch_size]
        url = self._upload_url()
        username = self.options.get('username', '')
        password = self.options.get('password', '')
        opened_files = []
        try:
            if agent is not None:
                self._set_status(agent, f"Wrapping {len(paths)} capture(s) for upload")
            for path in paths:
                opened_files.append(('capture', (os.path.basename(path), open(path, 'rb'), 'application/octet-stream')))

            if agent is not None:
                self._set_status(agent, f"Uploading {len(paths)} capture(s) to server")
            response = requests.post(
                url,
                auth=(username, password),
                files=opened_files,
                timeout=max(30, 20 * len(paths)),
            )
            basenames = ', '.join(os.path.basename(path) for path in paths[:3])
            if len(paths) > 3:
                basenames += f", +{len(paths) - 3} more"
            if response.status_code == 200:
                for path in paths:
                    self._pending_files.pop(path, None)
                self._remember_uploaded(paths)
                _log_event(True, f"Uploaded {len(paths)} capture(s): {basenames}. Server response: {response.text[:120]}")
                if agent is not None:
                    self._set_status(agent, f"Upload complete: {len(paths)} capture(s)")
                if self._pending_files:
                    self._upload_pending_files(agent)
            else:
                _log_event(False, f"Upload batch failed: HTTP {response.status_code} - {response.text[:200]}")
                if agent is not None:
                    self._set_status(agent, f"Upload failed: HTTP {response.status_code}")
        except Exception as exc:
            _log_event(False, f"Upload batch raised exception: {exc}")
            if agent is not None:
                self._set_status(agent, f"Upload failed: {exc}")
        finally:
            for _, file_tuple in opened_files:
                try:
                    file_tuple[1].close()
                except Exception:
                    pass

    def _read_config(self):
        if not os.path.exists(self._CONFIG_PATH):
            return ''
        with open(self._CONFIG_PATH, 'r', encoding='utf-8', errors='replace') as fh:
            return fh.read()

    def _write_config_text(self, content):
        tmp_path = self._CONFIG_PATH + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as fh:
            fh.write(content)
        os.replace(tmp_path, self._CONFIG_PATH)

    def _format_value(self, value):
        if isinstance(value, bool):
            return 'true' if value else 'false'
        return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'

    def _table_bounds(self, content):
        table_re = re.compile(
            rf'(?m)^\s*\[main\.plugins\.{re.escape(self._PLUGIN_KEY)}\]\s*$'
        )
        match = table_re.search(content)
        if not match:
            return None
        next_table = re.search(r'(?m)^\s*\[.+\]\s*$', content[match.end():])
        end = match.end() + next_table.start() if next_table else len(content)
        return match.start(), end

    def _upsert_table_config(self, content, values):
        bounds = self._table_bounds(content)
        if not bounds:
            block = '\n[main.plugins.pwnagotchi_hashcat_wpa]\n'
            block += ''.join(f'{key} = {self._format_value(value)}\n' for key, value in values.items())
            return content.rstrip() + '\n' + block

        start, end = bounds
        block = content[start:end]
        missing = []
        for key, value in values.items():
            line_re = re.compile(rf'(?m)^(\s*){re.escape(key)}\s*=.*$')
            formatted = self._format_value(value)
            block, count = line_re.subn(lambda match: f'{match.group(1)}{key} = {formatted}', block, count=1)
            if count == 0:
                missing.append(f'{key} = {self._format_value(value)}')
        if missing:
            block = block.rstrip() + '\n' + '\n'.join(missing) + '\n'
        return content[:start] + block + content[end:]

    def _upsert_dotted_config(self, content, values):
        prefix = f'main.plugins.{self._PLUGIN_KEY}.'
        missing = []
        for key, value in values.items():
            line_re = re.compile(rf'(?m)^(\s*){re.escape(prefix + key)}\s*=.*$')
            formatted = self._format_value(value)
            content, count = line_re.subn(lambda match: f'{match.group(1)}{prefix}{key} = {formatted}', content, count=1)
            if count == 0:
                missing.append(f'{prefix}{key} = {self._format_value(value)}')
        if missing:
            content = content.rstrip() + '\n\n# pwnagotchi_hashcat_wpa plugin defaults\n'
            content += '\n'.join(missing) + '\n'
        return content

    def _write_plugin_config(self, values):
        content = self._read_config()
        if self._table_bounds(content):
            content = self._upsert_table_config(content, values)
        else:
            content = self._upsert_dotted_config(content, values)
        self._write_config_text(content)

    def _missing_default_values(self, content):
        bounds = self._table_bounds(content)
        if bounds:
            start, end = bounds
            block = content[start:end]
            return {
                key: value for key, value in self._DEFAULTS.items()
                if not re.search(rf'(?m)^\s*{re.escape(key)}\s*=', block)
            }

        prefix = f'main.plugins.{self._PLUGIN_KEY}.'
        return {
            key: value for key, value in self._DEFAULTS.items()
            if not re.search(rf'(?m)^\s*{re.escape(prefix + key)}\s*=', content)
        }

    def _ensure_default_config(self):
        try:
            content = self._read_config()
            missing = self._missing_default_values(content)
            if missing:
                self._write_plugin_config(missing)
                logging.info("[HashcatWPAServer] Added default config block to %s", self._CONFIG_PATH)
        except Exception as exc:
            logging.warning("[HashcatWPAServer] Could not add default config: %s", exc)

    def on_loaded(self):
        self._load_uploaded_state()
        self._ensure_default_config()
        self.ready = self._is_configured()
        if self.ready:
            logging.info("[HashcatWPAServer] Plugin loaded. Upload target: %s", self._upload_url())
            self._scan_existing_handshakes()
            self._upload_pending_files()
        else:
            logging.warning(
                "[HashcatWPAServer] Plugin installed. Set the server URL in %s or use the plugin web UI.",
                self._CONFIG_PATH,
            )

    def on_handshake(self, agent, filename, access_point, client_station):
        if not self.ready:
            _log_event(False, f"Skipped {os.path.basename(filename)}: set the server URL first.")
            self._set_status(agent, "Hashcat upload skipped: configure server")
            return

        basename = os.path.basename(filename)
        self._set_status(agent, f"Queued {basename} for Hashcat upload")
        self._add_pending_file(filename)
        self._upload_pending_files(agent)

    def on_internet_available(self, agent):
        self._scan_existing_handshakes(agent)
        self._upload_pending_files(agent)

    def _render_log_rows(self):
        if not _upload_log:
            return '<tr><td colspan="3" class="empty">No upload attempts yet.</td></tr>'

        rows = []
        for entry in _upload_log:
            badge_class = 'ok' if entry['ok'] else 'err'
            label = 'OK' if entry['ok'] else 'ERR'
            rows.append(
                '<tr>'
                f'<td>{html.escape(entry["ts"])}</td>'
                f'<td><span class="pill {badge_class}">{label}</span></td>'
                f'<td>{html.escape(entry["message"])}</td>'
                '</tr>'
            )
        return ''.join(rows)

    def _render_notice(self, notice):
        if not notice:
            return ''
        cls = 'success' if notice.get('ok') else 'warning'
        return (
            f'<div class="notice {cls}">'
            f'<strong>{html.escape(notice.get("label", ""))}</strong>'
            f'<span>{html.escape(notice.get("message", ""))}</span>'
            '</div>'
        )

    def _render_page(self, notice=None):
        base_url = html.escape(self._base_url() or self._PLACEHOLDER_URL)
        upload_url = html.escape(self._upload_url() or f'{self._PLACEHOLDER_URL}/api/upload')
        heartbeat_url = html.escape(self._heartbeat_url() or f'{self._PLACEHOLDER_URL}/api/pwnagotchi/heartbeat')
        username = html.escape(self.options.get('username') or 'admin')
        password = html.escape(self.options.get('password') or 'changeme')
        status_text, status_color = self._status()
        status_text = html.escape(status_text)
        notice_html = self._render_notice(notice)
        log_rows = self._render_log_rows()
        pending_count = len(self._pending_files)

        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Hashcat WPA Server</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background: #eef2f6;
      color: #172033;
      font-family: "Trebuchet MS", Verdana, sans-serif;
      padding: 14px;
    }}
    main {{ max-width: 980px; margin: 0 auto; }}
    .top {{
      display: grid;
      grid-template-columns: minmax(0, 1.2fr) minmax(260px, .8fr);
      gap: 12px;
      margin-bottom: 12px;
    }}
    .details {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      margin-top: 12px;
    }}
    section {{
      background: #ffffff;
      border: 1px solid #cfd8e3;
      border-radius: 8px;
      padding: 16px;
      box-shadow: 0 1px 2px rgba(15, 23, 42, .06);
    }}
    .card {{
      border: 1px solid #e2e8f0;
      border-radius: 8px;
      padding: 14px;
      background: #f8fbff;
    }}
    h1 {{ margin: 0 0 6px; font-size: 1.45rem; line-height: 1.2; }}
    h2 {{ margin: 0 0 12px; font-size: 1rem; color: #334155; }}
    h3 {{ margin: 0 0 10px; font-size: .92rem; color: #0f172a; }}
    p {{ margin: 0; color: #64748b; line-height: 1.45; }}
    label {{ display: block; margin: 11px 0 5px; font-weight: 700; font-size: .86rem; }}
    input {{
      width: 100%;
      padding: 10px 11px;
      border: 1px solid #b8c4d4;
      border-radius: 6px;
      font-size: .95rem;
      background: #fbfdff;
      color: #172033;
    }}
    input:focus {{ outline: 2px solid #82c7ff; border-color: #2687d9; }}
    button, .button {{
      display: inline-block;
      border: 0;
      border-radius: 6px;
      background: #1770b8;
      color: #fff;
      padding: 10px 13px;
      margin-top: 14px;
      font-weight: 700;
      text-decoration: none;
      cursor: pointer;
      font-size: .92rem;
    }}
    .button.secondary {{ background: #475569; margin-left: 6px; }}
    .status {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 7px 10px;
      border-radius: 999px;
      background: #f8fafc;
      border: 1px solid #dbe4ee;
      font-weight: 700;
      margin-top: 10px;
    }}
    .dot {{ width: 10px; height: 10px; border-radius: 99px; background: {status_color}; }}
    .kv {{ display: grid; grid-template-columns: 98px minmax(0, 1fr); gap: 8px; margin: 9px 0; }}
    .kv span {{ color: #64748b; font-size: .84rem; }}
    code {{
      display: inline-block;
      max-width: 100%;
      overflow-wrap: anywhere;
      background: #edf6ff;
      color: #075985;
      border: 1px solid #cfe7ff;
      border-radius: 5px;
      padding: 2px 5px;
      font-size: .84rem;
    }}
    .notice {{
      margin: 12px 0 0;
      padding: 10px 12px;
      border-radius: 6px;
      display: grid;
      gap: 3px;
    }}
    .notice.success {{ background: #ecfdf5; border: 1px solid #bbf7d0; color: #14532d; }}
    .notice.warning {{ background: #fff7ed; border: 1px solid #fed7aa; color: #7c2d12; }}
    .steps {{ display: grid; gap: 8px; margin-top: 10px; }}
    .step {{ display: grid; grid-template-columns: 28px minmax(0, 1fr); gap: 9px; align-items: start; }}
    .num {{
      height: 24px;
      width: 24px;
      border-radius: 99px;
      background: #dbeafe;
      color: #075985;
      display: grid;
      place-items: center;
      font-weight: 800;
      font-size: .78rem;
    }}
    table {{ width: 100%; border-collapse: collapse; font-size: .84rem; }}
    th {{ text-align: left; color: #64748b; border-bottom: 1px solid #dbe4ee; padding: 8px; }}
    td {{ border-bottom: 1px solid #edf2f7; padding: 8px; vertical-align: top; }}
    .pill {{ border-radius: 99px; padding: 3px 7px; color: #fff; font-weight: 800; font-size: .72rem; }}
    .pill.ok {{ background: #16a34a; }}
    .pill.err {{ background: #dc2626; }}
    .empty {{ text-align: center; color: #64748b; padding: 18px; }}
    @media (max-width: 760px) {{
      .top {{ grid-template-columns: 1fr; }}
      .details {{ grid-template-columns: 1fr; }}
      .kv {{ grid-template-columns: 1fr; gap: 3px; }}
      .button.secondary {{ margin-left: 0; }}
    }}
  </style>
</head>
<body>
<main>
  <div class="top">
    <section>
      <h1>Hashcat WPA Server</h1>
      <p>One place to set the server address, test the link, and watch upload attempts.</p>
      <div class="status"><span class="dot"></span><span>{status_text}</span></div>
      {notice_html}

      <form method="post" action="/plugins/pwnagotchi_hashcat_wpa/save">
        <label for="url">Server URL</label>
        <input id="url" name="url" value="{base_url}" placeholder="https://upload.example.com or http://192.168.x.x:9111">

        <label for="username">Username</label>
        <input id="username" name="username" value="{username}" autocomplete="username">

        <label for="password">Password</label>
        <input id="password" name="password" value="{password}" autocomplete="current-password">

        <button type="submit">Save config</button>
        <a class="button secondary" href="/plugins/pwnagotchi_hashcat_wpa/test">Test connection</a>
      </form>
    </section>

    <section>
      <h2>Current target</h2>
      <div class="kv"><span>Server</span><code>{base_url}</code></div>
      <div class="kv"><span>Upload API</span><code>{upload_url}</code></div>
      <div class="kv"><span>Heartbeat API</span><code>{heartbeat_url}</code></div>
      <div class="kv"><span>Config file</span><code>{self._CONFIG_PATH}</code></div>

      <h2 style="margin-top:18px">Easy setup</h2>
      <div class="steps">
        <div class="step"><div class="num">1</div><p>Install the plugin from the server page.</p></div>
        <div class="step"><div class="num">2</div><p>Replace the placeholder with your Public Website URL, LAN IP, or Tailscale IP.</p></div>
        <div class="step"><div class="num">3</div><p>Use Public Website for normal tethered internet, or Tailscale for a private VPN path.</p></div>
      </div>

      <div class="details">
        <div class="card">
          <h3>Plugin info</h3>
          <div class="kv"><span>Name</span><code>pwnagotchi_hashcat_wpa</code></div>
          <div class="kv"><span>Version</span><code>{self.__version__}</code></div>
          <div class="kv"><span>Status</span><code>{status_text}</code></div>
          <div class="kv"><span>Description</span><code>{html.escape(self.__description__)}</code></div>
        </div>
        <div class="card">
          <h3>Upload info</h3>
          <div class="kv"><span>Pending</span><code>{pending_count}</code></div>
          <div class="kv"><span>Last status</span><code>{html.escape(self._last_status)}</code></div>
          <div class="kv"><span>Upload API</span><code>{upload_url}</code></div>
          <div class="kv"><span>Heartbeat</span><code>{heartbeat_url}</code></div>
        </div>
      </div>
    </section>
  </div>

  <section>
    <h2>Upload log</h2>
    <table>
      <thead><tr><th>Time</th><th>Status</th><th>Detail</th></tr></thead>
      <tbody>{log_rows}</tbody>
    </table>
  </section>
</main>
</body>
</html>"""

    def on_webhook(self, path, request):
        notice = None
        normalized_path = (path or '').strip('/')

        if normalized_path == 'save' and getattr(request, 'method', 'GET') == 'POST':
            url = (request.form.get('url') or '').strip().rstrip('/')
            username = (request.form.get('username') or '').strip()
            password = request.form.get('password') or ''
            if not url.startswith(('http://', 'https://')):
                notice = {'ok': False, 'label': 'Check the URL', 'message': 'Use a full URL like http://100.x.x.x:9111 or https://upload.example.com.'}
            else:
                values = {'enabled': True, 'url': url, 'username': username or 'admin', 'password': password or 'changeme'}
                try:
                    self._write_plugin_config(values)
                    self.options.update(values)
                    self.ready = self._is_configured()
                    notice = {'ok': True, 'label': 'Config saved', 'message': 'The plugin is updated. Restart Pwnagotchi if Webcfg still shows old values.'}
                except Exception as exc:
                    notice = {'ok': False, 'label': 'Could not save', 'message': str(exc)}

        if normalized_path == 'test':
            base_url = self._base_url()
            if self._is_placeholder_url():
                notice = {'ok': False, 'label': 'Set the server URL first', 'message': 'Replace 100.x.x.x with the Public Website URL, real LAN IP, or Tailscale IP.'}
            else:
                try:
                    response = requests.get(base_url, timeout=10, allow_redirects=True)
                    ok = response.status_code < 500
                    notice = {
                        'ok': ok,
                        'label': 'Connected' if ok else 'Server error',
                        'message': f'HTTP {response.status_code} from {base_url}',
                    }
                except Exception as exc:
                    notice = {'ok': False, 'label': 'Unreachable', 'message': f'Could not reach {base_url}: {exc}'}

        return self._render_page(notice=notice)
