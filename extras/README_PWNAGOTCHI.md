# Pwnagotchi Auto-Upload Plugin Guide

This official plugin automatically uploads your intercepted WPA/WPA2 handshakes (`.pcap` files) securely to your **Hashcat WPA Server** instance so they can be cracked automatically!

Instead of manually hooking your Pwnagotchi to a PC via USB inside a browser and downloading files one by one, your little companion will intelligently use Bluetooth tethering (which shares your phone's internet data connection) to POST the handshakes natively to the server the second they are captured.

## Step 1: Prepare the File

First, grab the custom plugin file from this repository: [`pwnagotchi_hashcat_wpa.py`](pwnagotchi_hashcat_wpa.py)

## Step 2: Transfer to Pwnagotchi

You need to place the Python plugin logically inside your Pwnagotchi's `custom-plugins` directory.

### Method A: Via Web UI
If you have the "webcfg" or built-in file manager plugins installed, simply navigate to your Pwnagotchi Web Interface and upload the `pwnagotchi_hashcat_wpa.py` straight into the custom plugins folder.

### Method B: Via Terminal (SSH/SCP)
If connected via USB data cable, open your computer's terminal:
1. Transfer the file:
   ```bash
   scp pwnagotchi_hashcat_wpa.py pi@10.0.0.2:/home/pi/custom-plugins/
   ```
2. *(Alternatively)* SSH into the Pwnagotchi and download it natively via wget:
   ```bash
   ssh pi@10.0.0.2
   cd /home/pi/custom-plugins/
   wget https://raw.githubusercontent.com/EpicNori/hashcat-wpa-server/master/extras/pwnagotchi_hashcat_wpa.py
   ```
*(Note: Ensure `/home/pi/custom-plugins/` exists and is defined as the plugins directory inside your configurations).*

## Step 3: Configure Settings

You must tell the plugin your server's IP and basic login credentials.

Access your `config.toml` (Usually located at `/etc/pwnagotchi/config.toml` or editable directly via the Web UI). Add the following parameters at the bottom:

```toml
main.plugins.hashcatwpaserver.enabled = true
main.plugins.hashcatwpaserver.url = "http://<YOUR_HASHCAT_LINUX_SERVER_IP>:9111"
main.plugins.hashcatwpaserver.username = "admin"
main.plugins.hashcatwpaserver.password = "changeme"
```
*(Make sure to change `YOUR_HASHCAT_LINUX_SERVER_IP` and the password accordingly).*

## Step 4: Run

Restart your Pwnagotchi to fully initialize the plugin:
```bash
sudo systemctl restart pwnagotchi
```

Now, anytime you turn on the native **Pwnagotchi BT-Tether** connection over your phone (meaning the Pwnagotchi possesses an active internet channel), the plugin will watch for new captures. Upon capturing a handshake and verifying connectivity, it will blast the `.pcap` off to the Hashcat server, schedule a task, and your CPU/GPU instance will immediately start cracking!
