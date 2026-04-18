# Hashcat WPA/WPA2 Server

[![Version](https://img.shields.io/badge/version-1.2.0-blue.svg)](https://github.com/EpicNori/Hashcat-Pwnagotchi-server)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20Windows-orange.svg)](https://github.com/EpicNori/Hashcat-Pwnagotchi-server)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A production-grade WPA/WPA2 cracking dashboard built around Hashcat. It gives you a web UI for uploads, task routing, device monitoring, result review, and safe in-place updates while keeping user data separate from application code.

## Acknowledgement

Special thanks to **Danylo Ulianych** and the upstream project [dizcza/hashcat-wpa-server](https://github.com/dizcza/hashcat-wpa-server), which this repository builds upon.

## Quick Start

### Linux one-liner

For Debian, Ubuntu, and Kali:

```bash
curl -sL https://raw.githubusercontent.com/EpicNori/Hashcat-Pwnagotchi-server/main/install.sh | sudo bash
```

### Windows one-liner

Run this from an elevated PowerShell window:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/EpicNori/Hashcat-Pwnagotchi-server/main/install.ps1 | iex"
```

After installation, the dashboard is available at `http://127.0.0.1:9111`.

## Update Workflow

Both platforms now follow the same high-level workflow:

- Install with a one-liner.
- Keep application code separate from persistent data.
- Update safely without deleting users, captures, or databases.
- Run the dashboard automatically in the background after install and after reboot.

### Linux update

```bash
crackserver update
```

### Windows update

```powershell
crackserver update
```

The Windows installer also drops a machine-wide `crackserver` wrapper into PATH.

## Global CLI

### Linux

- `crackserver start`
- `crackserver stop`
- `crackserver restart`
- `crackserver status`
- `crackserver update`
- `crackserver dashboard`
- `crackserver logs`

### Windows

- `crackserver start`
- `crackserver stop`
- `crackserver restart`
- `crackserver status`
- `crackserver update`
- `crackserver dashboard`
- `crackserver logs`
- `crackserver enable-autostart`
- `crackserver disable-autostart`
- `crackserver uninstall`

## Windows Layout

The Windows installer uses a production-style split under `C:\ProgramData\HashcatWPAServer`:

- `current` - current application code
- `venv` - Python virtual environment
- `data` - persistent application state
- `logs` - dashboard and updater logs
- `bin` - `crackserver` command wrapper

Autostart is handled with a Scheduled Task so the dashboard comes back after reboot.

## Data Persistence

User data is intentionally kept separate from application code:

- Linux app code: `/opt/hashcat-wpa-server`
- Linux user data: `/var/lib/hashcat-wpa-server/`
- Windows app code: `C:\ProgramData\HashcatWPAServer\current`
- Windows user data: `C:\ProgramData\HashcatWPAServer\data`

Safe updates replace the application layer only.

## Important Windows Notes

The Windows installer automates the dashboard, virtual environment, autostart, CLI wrapper, and update path. For actual cracking workloads, you should still make sure the cracking toolchain is available in `PATH`:

- `hashcat.exe` for cracking and benchmarks
- `hcxpcapngtool.exe` and `hcxhashtool.exe` for converting and splitting raw capture uploads
- `hcxmactool.exe` if you want legacy `.hccapx` or `.pmkid` conversion support

If those tools are missing, the dashboard still installs and runs, but cracking features that depend on them will not work until they are installed.

## Key Features

- Auto-detects CPUs and GPUs for task routing
- Per-device targeting and intensity controls
- Safe update flow that preserves user data
- Web UI for uploads, cracking progress, results, and user management
- Default device and work-mode policy for API and Pwnagotchi uploads
- Built-in fallback wordlist installation from the dashboard
- Optional user-provided wordlist generator scripts
- Tailscale integration on Linux deployments

## Supported Formats

The app accepts modern Hashcat and common capture formats:

- `.22000`
- `.pcapng`
- `.cap`
- `.pcap`
- `.hccapx`
- `.2500`
- `.pmkid`
- `.16800`

Uploads are converted to `.22000` when the required conversion tools are available.

## Upload Modes

- `Low` - conservative chain for lighter systems
- `Fast` - short optimized chain that respects runtime limits
- `Normal` - extended attack chain that continues until the task is completed, cracked, or cancelled

## Wordlists

- Built-in fallback wordlists can be installed directly from the upload page
- User wordlists live under `~/.hashcat/wpa-server/wordlists`
- User generator scripts are supported
- Supported generator extensions now include `.sh`, `.bash`, `.py`, `.ps1`, `.cmd`, and `.bat`

## Development

For local development:

```bash
pip install -r requirements.txt
python -m flask --app app.run run --debug
```

On Windows, the production installer serves the app with `waitress`. On Linux, the packaged deployment continues to use `gunicorn`.
