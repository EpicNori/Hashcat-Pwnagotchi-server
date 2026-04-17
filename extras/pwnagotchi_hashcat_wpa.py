import os
import requests
import logging
from pwnagotchi import plugins

class HashcatWPAServer(plugins.Plugin):
    __author__ = 'EpicNori (via Antigravity AI)'
    __version__ = '1.0.0'
    __license__ = 'GPL3'
    __description__ = 'Uploads captured handshakes automatically to a self-hosted hashcat-wpa-server instance over Bluetooth Tethering.'

    def __init__(self):
        self.ready = False

    def on_loaded(self):
        if 'url' not in self.options or 'username' not in self.options or 'password' not in self.options:
            logging.error("[HashcatWPAServer] URL, username, or password not set in config.toml")
            return
        logging.info("[HashcatWPAServer] Plugin successfully loaded.")

    def on_handshake(self, agent, filename, access_point, client_station):
        try:
            url = self.options['url']
            if not url.endswith('/api/upload'):
                url = url.rstrip('/') + '/api/upload'
                
            username = self.options.get('username')
            password = self.options.get('password')
            
            # Submitting to the background server with default "fast" settings
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
