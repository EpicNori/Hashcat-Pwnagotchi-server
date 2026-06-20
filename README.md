# Hashcat WPA/WPA2 Server

[![Version](https://img.shields.io/badge/version-1.1.2--alpha-blue.svg)](https://github.com/EpicNori/Hashcat-Pwnagotchi-server)
[![Platform](https://img.shields.io/badge/platform-Linux-orange.svg)](https://github.com/EpicNori/Hashcat-Pwnagotchi-server)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A Linux WPA/WPA2 cracking dashboard built around Hashcat. It provides a web UI for capture uploads, task routing, device monitoring, result review, Pwnagotchi uploads, and safe in-place updates while keeping user data separate from application code.

## Contents

- [Features](#features)
- [Install](#install)
  - [Easy Docker / CasaOS Install](#easy-docker--casaos-install)
  - [Debian, Ubuntu, Kali](#debian-ubuntu-kali)
  - [Windows / WSL 2](#windows--wsl-2)
  - [Docker](#docker)
  - [CasaOS / ZimaOS Custom Install](#casaos--zimaos-custom-install)
- [Updates](#updates)
- [Data Persistence](#data-persistence)
- [CLI](#cli)
- [Pwnagotchi Plugin](#pwnagotchi-plugin)
- [Supported Capture Formats](#supported-capture-formats)
- [Attack Modes](#attack-modes)
- [Wordlists](#wordlists)
- [Development](#development)
- [Acknowledgement](#acknowledgement)

## Features

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

## Install

### Easy Docker / CasaOS Install

This is the simplest path if you already have Docker or CasaOS running. Open the terminal on the server and run:

```bash
curl -fsSL https://raw.githubusercontent.com/EpicNori/Hashcat-Pwnagotchi-server/main/docker/easy-install.sh | bash
```

The script asks for an admin password, downloads the project, builds the Docker image locally, starts the container, and prints the dashboard URL.

For NVIDIA GPU mode, use:

```bash
curl -fsSL https://raw.githubusercontent.com/EpicNori/Hashcat-Pwnagotchi-server/main/docker/easy-install.sh | HASHCAT_WPA_DOCKER_GPU=1 bash
```

Open the dashboard at:

```text
http://SERVER_IP:9111
```

### Debian, Ubuntu, Kali

```bash
curl -sL https://raw.githubusercontent.com/EpicNori/Hashcat-Pwnagotchi-server/main/install.sh | sudo bash
```

After installation, open:

```text
http://127.0.0.1:9111
```

The installer attempts to auto-install GPU runtimes when compatible NVIDIA or AMD hardware is detected on supported Debian-family systems. NVIDIA uses the Debian/Ubuntu driver stack, while AMD uses ROCm/OpenCL packages.

The installer and update scripts support both `amd64` and `arm64`. ARM hosts install a CPU OpenCL runtime and use the built-in CPU-safe Hashcat mode. Automatic NVIDIA/AMD GPU driver setup remains limited to regular `amd64` Debian/Ubuntu systems.

Tailscale is optional. If its bootstrap script is unavailable during install, the server setup continues and you can retry later with `crackserver tailscale` or from Admin Settings.

### Windows / WSL 2

On Windows, run the server inside WSL 2 with Ubuntu. From an Administrator PowerShell, install Ubuntu first:

```powershell
wsl --install -d Ubuntu
```

After rebooting if Windows asks for it and finishing the first Ubuntu user setup, run:

```powershell
wsl -d Ubuntu -u root -- bash -lc "apt-get update && apt-get install -y curl ca-certificates && curl -sL https://raw.githubusercontent.com/EpicNori/Hashcat-Pwnagotchi-server/main/install.sh | bash"
```

Open:

```text
http://localhost:9111
```

If systemd is disabled in your WSL distro, the installer prints a manual `gunicorn` command to start the server.

For NVIDIA GPUs on WSL, install the CUDA-capable NVIDIA driver on Windows, then run `wsl --shutdown` and start Ubuntu again. Do not install Linux NVIDIA kernel drivers inside WSL; WSL exposes the GPU through the Windows driver bridge.

### Docker

The Docker setup runs the web server, Nginx, Hashcat brain, persistent database, captures, and logs inside containers. It works as a CPU-safe default and can be started with NVIDIA GPU access when the Docker host has the NVIDIA Container Toolkit installed.

Set a strong `HASHCAT_ADMIN_PASSWORD`. Compose intentionally refuses to start without it.

CPU / generic Docker server:

```bash
HASHCAT_ADMIN_USER=admin HASHCAT_ADMIN_PASSWORD='change-me-now' \
docker compose -f docker/docker-compose.yml up -d --build
```

NVIDIA GPU server:

```bash
HASHCAT_ADMIN_USER=admin HASHCAT_ADMIN_PASSWORD='change-me-now' \
docker compose -f docker/docker-compose.yml -f docker/docker-compose.gpu.yml up -d --build
```

Open:

```text
http://SERVER_IP:9111
```

Default Docker volumes:

- `hashcat-wpa-data` mounted at `/data`
- `hashcat-wpa-logs` mounted at `/var/log/hashcat-wpa-server`

Bind data and logs to host folders instead:

```bash
HASHCAT_WPA_DOCKER_DATA="$PWD/docker-data" \
HASHCAT_WPA_DOCKER_LOGS="$PWD/docker-logs" \
HASHCAT_ADMIN_USER=admin HASHCAT_ADMIN_PASSWORD='change-me-now' \
docker compose -f docker/docker-compose.yml up -d --build
```

Useful Docker commands:

```bash
docker compose -f docker/docker-compose.yml logs -f
docker compose -f docker/docker-compose.yml restart
docker compose -f docker/docker-compose.yml down
```

### CasaOS / ZimaOS Custom Install

CasaOS Custom Install can use [docker/docker-compose.casaos.yml](docker/docker-compose.casaos.yml). It includes `x-casaos` metadata, stores data under `/DATA/AppData/$AppID`, exposes the dashboard on port `9111`, and builds the Docker image locally from this GitHub repository. No paid registry, GHCR image, or GitHub Actions build is required.

The first CasaOS install can take several minutes because the image builds on the CasaOS server.

#### Option A: Compose / YAML Custom Install

Use this if CasaOS shows a large compose or YAML paste box.

- App name / title: `Hashcat Pwnagotchi Server`
- App ID, if CasaOS asks: `hashcat-pwnagotchi-server`
- Compose / YAML box: paste the contents of [docker/docker-compose.casaos.yml](docker/docker-compose.casaos.yml)
- Environment variables, if CasaOS asks separately:
  - `HASHCAT_ADMIN_USER=admin`
  - `HASHCAT_ADMIN_PASSWORD=<your strong password>`
  - `TZ=<your timezone>`, for example `Europe/Berlin`

If CasaOS validates the compose before showing environment fields and complains that `HASHCAT_ADMIN_PASSWORD` is missing, replace this line in the pasted YAML:

```yaml
HASHCAT_ADMIN_PASSWORD: ${HASHCAT_ADMIN_PASSWORD:?Set HASHCAT_ADMIN_PASSWORD in CasaOS app settings before install}
```

with a strong password:

```yaml
HASHCAT_ADMIN_PASSWORD: "change-me-now"
```

Do not leave `change-me-now` as the real password.

#### Option B: Manual App Installation Form

Use this if CasaOS shows individual fields like `Docker-Image`, `Tag`, `Web UI`, `Port`, `Speicher`, and `Umgebungsvariablen`.

Because this form requires an existing image name and does not build from GitHub by itself, build the image once on the CasaOS server first:

```bash
cd /DATA/AppData
git clone https://github.com/EpicNori/Hashcat-Pwnagotchi-server.git
cd Hashcat-Pwnagotchi-server
docker build -f docker/Dockerfile -t hashcat-pwnagotchi-server:1.1.2-alpha .
```

Then fill the CasaOS form like this:

| CasaOS field | Value |
| --- | --- |
| `Docker-Image` | `hashcat-pwnagotchi-server` |
| `Tag` | `1.1.2-alpha` |
| `Title` | `Hashcat Pwnagotchi Server` |
| `Icon-URL` | `https://cdn.jsdelivr.net/gh/EpicNori/Hashcat-Pwnagotchi-server@main/app/static/pwnagotchi-device.svg` |
| `Web UI` | `http://<CASAOS_IP>:9111/` |
| `Netzwerk` | `bridge` |
| `Containername` | `hashcat-pwnagotchi-server` |
| `Neustartrichtlinie` | `unless-stopped` |
| `Privilegiert` | off |
| `CPU-Anteile` | `Mittel` or `Hoch` |
| `Speicherlimit` | at least `2048 MB`; `4096 MB` is better |
| `Container-Befehl` | leave empty |
| `Container-Funktionen (cap-add)` | leave empty |

Add this port mapping:

| Host port | Container port | Protocol |
| --- | --- | --- |
| `9111` | `80` | `TCP` |

Add these storage mounts:

| Host path | Container path |
| --- | --- |
| `/DATA/AppData/hashcat-pwnagotchi-server/data` | `/data` |
| `/DATA/AppData/hashcat-pwnagotchi-server/logs` | `/var/log/hashcat-wpa-server` |

Add these environment variables:

| Name | Value |
| --- | --- |
| `HASHCAT_ADMIN_USER` | `admin` |
| `HASHCAT_ADMIN_PASSWORD` | `<your strong password>` |
| `HASHCAT_WPA_SERVER_HOME` | `/data` |
| `HASHCAT_WPA_DATA_DIR` | `/data` |
| `HASHCAT_WPA_LOG_DIR` | `/var/log/hashcat-wpa-server` |
| `HOME` | `/data` |
| `TERM` | `xterm` |
| `TZ` | `Europe/Berlin` or your timezone |

Leave `Devices` empty for CPU-only mode. GPU passthrough on CasaOS is host-specific and should be configured only after the CPU container starts successfully.

#### CasaOS compose install flow

1. Open CasaOS.
2. Go to App Store.
3. Choose Custom Install.
4. Paste the compose from [docker/docker-compose.casaos.yml](docker/docker-compose.casaos.yml).
5. Set `HASHCAT_ADMIN_PASSWORD` to a strong password.
6. Start the install and wait for the first local build to finish.
7. Open `http://CASAOS_IP:9111`.

Manual test command on a CasaOS host:

```bash
HASHCAT_ADMIN_PASSWORD='change-me-now' AppID=hashcat-pwnagotchi-server \
docker compose -f docker/docker-compose.casaos.yml up -d --build
```

This CasaOS app definition is CPU-safe and declares `amd64` support. NVIDIA GPU use on CasaOS requires host NVIDIA drivers plus NVIDIA Container Toolkit, then an advanced compose edit to add GPU access.

## Updates

For Debian/package installations:

```bash
crackserver update
```

Updates keep persistent users, captures, databases, wordlists, and settings under `/var/lib/hashcat-wpa-server/`.

Docker and CasaOS users should rebuild/recreate the container from the current repository when updating. Persistent data stays in the configured Docker volume or `/DATA/AppData/$AppID`.

## Data Persistence

Native Linux install:

- App code: `/opt/hashcat-wpa-server`
- User data: `/var/lib/hashcat-wpa-server/`
- Runtime logs: `/var/log/hashcat-wpa-server/`

Docker install:

- User data: `/data`
- Runtime logs: `/var/log/hashcat-wpa-server`

CasaOS install:

- User data: `/DATA/AppData/$AppID/data`
- Runtime logs: `/DATA/AppData/$AppID/logs`

Run `crackserver driver-check` to install or repair supported NVIDIA/AMD GPU runtimes, then run `crackserver doctor` to verify architecture, Hashcat backend visibility, and the active ARM/amd64 runtime path.

Safe updates replace the application layer only.

## CLI

```bash
crackserver setup
crackserver set-login
crackserver upload <capture...>
crackserver tailscale
crackserver cloudflare <hostname>
crackserver start
crackserver stop
crackserver restart
crackserver status
crackserver update
crackserver driver-check
crackserver driver-status
crackserver doctor
crackserver dashboard
crackserver logs
crackserver uninstall
```

`crackserver uninstall` is an interactive wizard and keeps `/var/lib/hashcat-wpa-server` by default. Use `crackserver uninstall --yes --purge-data` only when you also want to delete users, captures, results, settings, and logs.

Use `crackserver uninstall --yes --dry-run` to preview the app, service, CLI, data, and log paths that would be touched.

The uninstall and reset flows refuse empty, relative, root, and broad system paths. `crackserver reset` only wipes the configured data directory and prints the path first.

For CLI uploads with shell-special characters, quote the password. If it starts with `-`, use the equals form:

```bash
crackserver upload --password='-starts-with-dash' capture.22000
```

CLI uploads can also mirror the GUI job choices:

```bash
crackserver upload --workload normal --wordlist None --rule None capture.22000
crackserver upload --workload rainbow --brain --brain-feature 3 --devices 1,2 capture.22000
```

## Pwnagotchi Plugin

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

First-start defaults:

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

## Supported Capture Formats

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

## Attack Modes

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

## Acknowledgement

Special thanks to **Danylo Ulianych** and the upstream project [dizcza/hashcat-wpa-server](https://github.com/dizcza/hashcat-wpa-server), which this repository builds upon.
