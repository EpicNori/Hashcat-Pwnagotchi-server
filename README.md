# Hashcat WPA/WPA2 Server 🚀

[![Version](https://img.shields.io/badge/version-1.2.0-blue.svg)](https://github.com/EpicNori/Hashcat-Pwnagotchi-server)
[![Debian](https://img.shields.io/badge/platform-Debian%20%7C%20Ubuntu-orange.svg)](https://www.debian.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A production-grade, automated WPA/WPA2 handshake cracking server. Designed for native Linux hardware, this server provides a high-performance web dashboard to manage distributed cracking tasks across multiple GPUs and CPUs.

---

## Acknowledgement

Special thanks to **Danylo Ulianych** ([dizcza/hashcat-wpa-server](https://github.com/dizcza/hashcat-wpa-server)), the original creator of the upstream project that made this repository possible.

All changes and adaptations in this repository were done by **EpicNori**, with respect and appreciation for the original architecture, idea, and open source foundation created by the upstream author.

---

## ⚡ Quick Start (Automated Installer)

The absolute easiest way to deploy the server on **Debian, Ubuntu, or Kali Linux**. This one-liner handles all dependencies, Python environments, and background system services automatically.

```bash
curl -sL https://raw.githubusercontent.com/EpicNori/Hashcat-Pwnagotchi-server/main/install.sh | sudo bash
```

**Your dashboard will be available at:** `http://localhost:9111`

---

## 🛠️ Global CLI: `crackserver`

Once installed, managing your server from the terminal is effortless. The installer registers a global command that works from anywhere:

- `crackserver start/stop/restart` - Manage the background service.
- `crackserver update` - **Safe Update**: Pulls latest code without ever touching your user data or passwords.
- `crackserver status` - Check health and see the last few logs.
- `crackserver dashboard` - Quickly find your local and network IP addresses.
- `crackserver reset` - **Factory Reset**: Performs a clean wipe of all user data and databases.
- `crackserver logs` - Follow the real-time cracking output.

---

## 💎 Key Features

### 🎮 Precision Hardware Management
Take absolute control of your silicon. The server **auto-discovers** every GPU and CPU in your machine.
- **Granular Targeting**: Select specific GPUs/CPUs for every task. Dedicate your powerful card to big lists while your integrated graphics handles the fast ones.
- **Global Defaults**: Set a "Workhorse" GPU policy in the Admin settings for automated tasks (Pwnagotchi/API).
- **Dynamic Threading**: Set individual % intensity for every device to keep your system responsive.

### 🌡️ Intelligent Thermal Safety
Monitor your hardware in real-time. The server includes a built-in **Thermal Watchdog** that protects your hardware:
- **CPU & GPU Protection**: Automatically pauses or terminates tasks if user-defined temperature thresholds are exceeded.
- **Hardware Integration**: Directly leverages `--gpu-temp-abort` for hardware-level safety during cracking loops.

### 📟 Pwnagotchi Native Support
Seamlessly connect your AI companion. 
- Integrated **Documentation Page** within the web UI.
- Automatic handshake uploads via Bluetooth, WiFi, or Ethernet.
- Config-ready snippets provided directly in the dashboard.
- **Automated Routing**: Handshakes from Pwnagotchi are automatically routed to your predefined "Workhorse" GPUs.

### 📊 Advanced Dashboard & Search
- **Failure Tracking**: New "Failed" box to quickly identify captures with no results.
- **Bulk Downloads**: Download all processed keys, or just your own, with a single button.
- **Instant Search**: Find any BSSID, ESSID, or User across thousands of captures.

### 🔒 Enterprise Account Security
- Full **Administrator Console** for managing user operators.
- Mandatory password confirmation and secure password hashing.
- Role-based file protection (Admin sees everything, Users see only their tasks).

### 🌐 Secure Remote Access (Tailscale)
Integrated support for [Tailscale](https://tailscale.com/). Connect your server to a private VPN mesh with one click, allowing you to monitor and upload handshakes from anywhere in the world without port forwarding.

---

## 💾 Core Principles: Data Persistence

Your data is sacred. We follow a strict separation of concerns that ensures your cracking results are never lost:
- **Application Logic**: Lives in `/opt/hashcat-wpa-server` (Replaced during updates).
- **User Data**: Lives in `/var/lib/hashcat-wpa-server/` (Never touched by updates).
- **Service Management**: Controlled by a native `systemd` daemon running as a dedicated `hashcat` system user.

---

## 📂 Supported Formats

Supports every modern Hashcat format:
- **.22000** (Modern EAPOL/PMKID Combo)
- **.pcapng** (hcxdumptool native)
- **.cap / .pcap** (airodump-ng)
- **.hccapx / .2500** (Legacy EAPOL)
- **.pmkid / .16800** (Legacy PMKID)

---

## 👨‍💻 Contributing & Development

To run the development server locally:
1. `pip install -r requirements.txt`
2. `python run.py`

**Credits**: Built upon the foundational work of the original `hashcat-wpa-server` project by Danylo Ulianych (`dizcza`), with the changes in this repository carried out by EpicNori.
