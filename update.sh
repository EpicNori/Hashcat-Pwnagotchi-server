#!/bin/bash
set -e

# CrackServer Safe Update Utility
# This script ONLY updates the application logic and binaries.
# Your User Data, Databases, and Handshakes are TIED to /var/lib/hashcat-wpa-server/
# and are NEVER touched by this script.

SERVICE_NAME="hashcat-wpa-server.service"

restart_service() {
    if ! pidof systemd >/dev/null; then
        echo "[!] Systemd is not running, so the background service cannot be restarted automatically."
        return 0
    fi

    echo "[*] Reloading and restarting ${SERVICE_NAME}..."
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    systemctl restart "$SERVICE_NAME"

    echo "[*] Verifying service health..."
    sleep 2
    if ! systemctl is-active --quiet "$SERVICE_NAME"; then
        echo "[!] ${SERVICE_NAME} failed to start after update."
        systemctl status "$SERVICE_NAME" --no-pager || true
        exit 1
    fi

    echo "[+] ${SERVICE_NAME} is active."
}

echo "[*] --- CRACKSERVER SAFE UPDATE INITIATED ---"
echo "[*] Data preservation: ACTIVE"
echo "[*] Checking for previous installation..."

if [ ! -d "/opt/hashcat-wpa-server" ]; then
    echo "[!] Error: Server is not installed. Please use the main installer first."
    exit 1
fi

# Run the standard installer - it is already programmed to be non-destructive to user data
curl -sL https://raw.githubusercontent.com/EpicNori/Hashcat-Pwnagotchi-server/main/install.sh | bash

restart_service

echo "[*] Update complete. All user data and settings have been preserved."
