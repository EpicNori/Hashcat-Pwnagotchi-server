# Pwnagotchi Auto-Upload Plugin Guide

This official plugin automatically uploads your intercepted WPA/WPA2 handshakes (`.pcap` files) securely to your **Hashcat WPA Server** instance so they can be cracked automatically.

Instead of manually hooking your Pwnagotchi to a PC via USB inside a browser and downloading files one by one, your little companion will intelligently use Bluetooth tethering (which shares your phone's internet data connection) to POST the handshakes natively to the server the second they are captured.

## Step 1: Prepare the File

First, grab the custom plugin file from this repository: [`pwnagotchi_hashcat_wpa.py`](pwnagotchi_hashcat_wpa.py)

## Step 2: Install on Pwnagotchi

### Recommended: install with the built-in plugin manager

Jayofelony Pwnagotchi supports custom plugin repositories. Add this repository URL to your existing `main.custom_plugin_repos` list in `/etc/pwnagotchi/config.toml`:

```toml
main.custom_plugin_repos = [
    "https://github.com/EpicNori/Hashcat-Pwnagotchi-server/archive/refs/heads/main.zip"
]
```

Then run:

```bash
sudo pwnagotchi plugins update
sudo pwnagotchi plugins install pwnagotchi_hashcat_wpa
```

### Manual fallback

If you prefer manual installation, place the Python plugin file inside your Pwnagotchi's custom plugins directory.

Common Jayofelony path:

```bash
sudo mkdir -p /usr/local/share/pwnagotchi/custom-plugins
sudo wget https://raw.githubusercontent.com/EpicNori/Hashcat-Pwnagotchi-server/main/pwnagotchi_hashcat_wpa.py -O /usr/local/share/pwnagotchi/custom-plugins/pwnagotchi_hashcat_wpa.py
```

## Step 3: Configure Settings

You must tell the plugin your server's IP and basic login credentials.

Access your `config.toml` (Usually located at `/etc/pwnagotchi/config.toml` or editable directly via the Web UI). Add the following parameters at the bottom:

```toml
main.plugins.pwnagotchi_hashcat_wpa.enabled = true
main.plugins.pwnagotchi_hashcat_wpa.url = "http://<YOUR_HASHCAT_LINUX_SERVER_IP>:9111"
main.plugins.pwnagotchi_hashcat_wpa.username = "admin"
main.plugins.pwnagotchi_hashcat_wpa.password = "changeme"
```
*(Make sure to change `YOUR_HASHCAT_LINUX_SERVER_IP` and the password accordingly).*

### How task mode is chosen

The plugin only uploads the capture and credentials. The actual cracking mode and target devices are controlled by the server:

- The server's **Admin Settings** page defines the default devices used for `Pwnagotchi/API` uploads.
- The server's **Default Work Mode (for Pwnagotchi/API)** setting defines whether uploaded captures run in `Low`, `Fast`, or `Normal` mode.
- In `Normal` mode, the server keeps working the full extended attack chain until the task is completed, cracked, or manually cancelled.

## Step 4: Run

Restart your Pwnagotchi to fully initialize the plugin:
```bash
sudo systemctl restart pwnagotchi
```

Now, anytime you turn on the native **Pwnagotchi BT-Tether** connection over your phone (meaning the Pwnagotchi possesses an active internet channel), the plugin will watch for new captures. Upon capturing a handshake and verifying connectivity, it will blast the `.pcap` off to the Hashcat server, schedule a task, and your CPU/GPU instance will immediately start cracking!
