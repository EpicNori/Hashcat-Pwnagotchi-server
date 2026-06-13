# Hashcat WPA/WPA2 Server

[![Version](https://img.shields.io/badge/version-1.1.2--alpha-blue.svg)](https://github.com/EpicNori/Hashcat-Pwnagotchi-server)
[![Platform](https://img.shields.io/badge/platform-Linux-orange.svg)](https://github.com/EpicNori/Hashcat-Pwnagotchi-server)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A Linux WPA/WPA2 cracking dashboard built around Hashcat. It gives you a web UI for uploads, task routing, device monitoring, result review, Pwnagotchi uploads, and safe in-place updates while keeping user data separate from application code.

## Acknowledgement

Special thanks to **Danylo Ulianych** and the upstream project [dizcza/hashcat-wpa-server](https://github.com/dizcza/hashcat-wpa-server), which this repository builds upon.

## Quick Start

For Debian, Ubuntu, and Kali:

```bash
curl -sL https://raw.githubusercontent.com/EpicNori/Hashcat-Pwnagotchi-server/main/install.sh | sudo bash
```

The installer also attempts to auto-install GPU runtimes when compatible NVIDIA or AMD hardware is detected on supported Debian-family systems. NVIDIA uses the Debian/Ubuntu driver stack, while AMD uses ROCm/OpenCL packages.
The same installer/update scripts support both `amd64` and `arm64`; ARM hosts install a CPU OpenCL runtime and use the built-in CPU-safe Hashcat mode while automatic NVIDIA/AMD GPU driver setup remains limited to regular `amd64` Debian/Ubuntu systems.
Tailscale is optional; if its bootstrap script is unavailable during install, the server setup continues and you can retry later with `crackserver tailscale` or from Admin Settings.

After installation, the dashboard is available at `http://127.0.0.1:9111`.

## Windows / WSL Quick Start

On Windows, run the server inside WSL 2 with Ubuntu. From an Administrator PowerShell, install Ubuntu first:

```powershell
wsl --install -d Ubuntu
```

After rebooting if Windows asks for it and finishing the first Ubuntu user setup, run this one-liner from PowerShell:

```powershell
wsl -d Ubuntu -u root -- bash -lc "apt-get update && apt-get install -y curl ca-certificates && curl -sL https://raw.githubusercontent.com/EpicNori/Hashcat-Pwnagotchi-server/main/install.sh | bash"
```

Open the dashboard at `http://localhost:9111`. If systemd is disabled in your WSL distro, the installer prints a manual `gunicorn` command to start the server.

For NVIDIA GPUs on WSL, install the CUDA-capable NVIDIA driver on Windows, then run `wsl --shutdown` and start Ubuntu again. Do not install Linux NVIDIA kernel drivers inside WSL; WSL exposes the GPU through the Windows driver bridge.

## Update Workflow

```bash
crackserver update
```

Updates keep persistent users, captures, databases, wordlists, and settings under `/var/lib/hashcat-wpa-server/`.

## Global CLI

- `crackserver setup`
- `crackserver set-login`
- `crackserver upload <capture...>`
- `crackserver tailscale`
- `crackserver cloudflare <hostname>`
- `crackserver start`
- `crackserver stop`
- `crackserver restart`
- `crackserver status`
- `crackserver update`
- `crackserver driver-check`
- `crackserver driver-status`
- `crackserver doctor`
- `crackserver dashboard`
- `crackserver logs`
- `crackserver uninstall`

`crackserver uninstall` is an interactive wizard and keeps `/var/lib/hashcat-wpa-server` by default. Use `crackserver uninstall --yes --purge-data` only when you also want to delete users, captures, results, settings, and logs.
Use `crackserver uninstall --yes --dry-run` to preview the app, service, CLI, data, and log paths that would be touched.
The uninstall and reset flows refuse empty, relative, root, and broad system paths.
`crackserver reset` only wipes the configured data directory and prints the path first.
For CLI uploads with shell-special characters, quote the password. If it starts with `-`, use the equals form: `crackserver upload --password='-starts-with-dash' capture.22000`.
CLI uploads can also mirror the GUI job choices:

```bash
crackserver upload --workload normal --wordlist None --rule None capture.22000
crackserver upload --workload rainbow --brain --brain-feature 3 --devices 1,2 capture.22000
```

## Data Persistence

User data is intentionally kept separate from application code:

- App code: `/opt/hashcat-wpa-server`
- User data: `/var/lib/hashcat-wpa-server/`
- Runtime logs: `/var/log/hashcat-wpa-server/`

Run `crackserver driver-check` to install or repair supported NVIDIA/AMD GPU runtimes, then run `crackserver doctor` to verify architecture, Hashcat backend visibility, and the active ARM/amd64 runtime path.

Safe updates replace the application layer only.

## Key Features

- Auto-detects CPUs and GPUs for task routing
- Per-device targeting and intensity controls
- Optional spare-device queue scheduling
- Queue reordering controls for scheduled handshakes
- Safe update flow that preserves user data
- Web UI for uploads, cracking progress, results, and user management
- Default device and work-mode policy for API and Pwnagotchi uploads
- Built-in fallback wordlist installation from the dashboard
- Optional user-provided wordlist generator scripts
- Public Website setup through Cloudflare Tunnel for HTTPS uploads without a VPN client on the Pwnagotchi
- Tailscale integration for private VPN deployments

## Pwnagotchi Plugin Install

The repository exposes the Pwnagotchi uploader plugin at the repo root so Jayofelony Pwnagotchi can install it through the built-in plugin manager.

Add this repository URL to your existing `main.custom_plugin_repos` list in `/etc/pwnagotchi/config.toml`:

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

The plugin installs enabled and creates safe placeholder settings. Open the Pwnagotchi plugins page, click `pwnagotchi_hashcat_wpa`, replace `100.x.x.x` with your server's LAN IP, Tailscale IP, or Cloudflare Tunnel hostname, then press **Save config** and **Test connection**.

The first-start defaults are:

```toml
main.plugins.pwnagotchi_hashcat_wpa.enabled = true
main.plugins.pwnagotchi_hashcat_wpa.url = "http://100.x.x.x:9111"
main.plugins.pwnagotchi_hashcat_wpa.username = "admin"
main.plugins.pwnagotchi_hashcat_wpa.password = "changeme"
main.plugins.pwnagotchi_hashcat_wpa.handshake_dir = "/home/pi/handshakes"
main.plugins.pwnagotchi_hashcat_wpa.upload_existing = true
main.plugins.pwnagotchi_hashcat_wpa.batch_size = 8
```

Recommended access choices:

- `http://192.168.x.x:9111` for local LAN-only uploads
- `http://100.x.x.x:9111` for private uploads over Tailscale
- `https://upload.example.com` for mobile uploads through Public Website / Cloudflare Tunnel

The Admin Settings page includes a Public Website helper that installs/starts `cloudflared` from a Cloudflare Tunnel token and shows the exact Pwnagotchi plugin URL to paste into the plugin setup page. It also includes a Tailscale helper that can install/connect Tailscale, show the detected server Tailscale IP, and provide the private plugin URL.

For the exact display-debugging workflow used on the connected Jayofelony Pwnagotchi, see [PWNAGOTCHI_DISPLAY_WORKFLOW.md](ai%20information/PWNAGOTCHI_DISPLAY_WORKFLOW.md).

## Supported Formats

The app accepts modern Hashcat and common capture formats:

- `.22000`
- `.22001`
- `.pcapng`
- `.cap`
- `.pcap`
- `.hccapx`
- `.2500`
- `.2501`
- `.pmkid`
- `.16800`
- `.16801`

Uploads are converted to `.22000` when the required Linux conversion tools are available.

## Upload Modes

- `Rainbow` - builds an ESSID-specific WPA PMK cache from previously cracked keys and checks it with Hashcat mode 22001
- `Normal` - extended attack chain that continues until the task is completed, cracked, or cancelled

## Wordlists

- Built-in fallback wordlists can be installed directly from the upload page
- User wordlists live under `~/.hashcat/wpa-server/wordlists`
- User generator scripts are supported
- Supported generator extensions are `.sh`, `.bash`, and `.py`

## Development

For local development on Linux:

```bash
pip install -r requirements.txt
python -m flask --app app.run run --debug
```

Production deployments use `gunicorn`.
