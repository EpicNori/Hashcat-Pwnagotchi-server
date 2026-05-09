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

The plugin installs enabled and creates default settings on first start. Click `pwnagotchi_hashcat_wpa` in the Pwnagotchi plugins page, replace `100.x.x.x` with your server IP, then press **Save config** and **Test connection**.

### Manual fallback

If you prefer manual installation, place the Python plugin file inside your Pwnagotchi's custom plugins directory.

Common Jayofelony path:

```bash
sudo mkdir -p /usr/local/share/pwnagotchi/custom-plugins
sudo wget https://raw.githubusercontent.com/EpicNori/Hashcat-Pwnagotchi-server/main/pwnagotchi_hashcat_wpa.py -O /usr/local/share/pwnagotchi/custom-plugins/pwnagotchi_hashcat_wpa.py
```

## Step 3: Configure Settings

The easiest way is the plugin setup page. It writes the plugin section into `/etc/pwnagotchi/config.toml` for you.

If you prefer editing the file by hand, the first-start defaults look like this:

```toml
main.plugins.pwnagotchi_hashcat_wpa.enabled = true
main.plugins.pwnagotchi_hashcat_wpa.url = "http://100.x.x.x:9111"
main.plugins.pwnagotchi_hashcat_wpa.username = "admin"
main.plugins.pwnagotchi_hashcat_wpa.password = "changeme"
```
Change `100.x.x.x` to your hashcat server's LAN IP or Tailscale IP.

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

## Optional: simple Tailscale setup

If you want uploads to work while mobile without exposing your hashcat server publicly, install Tailscale on both devices:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Then point the plugin to the hashcat server's Tailscale IP:

```toml
main.plugins.pwnagotchi_hashcat_wpa.url = "http://100.x.x.x:9111"
```

Now, anytime you turn on the native **Pwnagotchi BT-Tether** connection over your phone (meaning the Pwnagotchi possesses an active internet channel), the plugin will watch for new captures. Upon capturing a handshake and verifying connectivity, it will blast the `.pcap` off to the Hashcat server, schedule a task, and your CPU/GPU instance will immediately start cracking!
