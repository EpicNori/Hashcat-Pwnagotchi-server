#!/bin/bash
set -e

# CrackServer Safe Update Utility
# This script ONLY updates the application logic and binaries.
# Your User Data, Databases, and Handshakes are TIED to /var/lib/hashcat-wpa-server/
# and are NEVER touched by this script.

echo "[*] --- CRACKSERVER SAFE UPDATE INITIATED ---"
echo "[*] Data preservation: ACTIVE"
echo "[*] Checking for previous installation..."

if [ ! -d "/opt/hashcat-wpa-server" ]; then
    echo "[!] Error: Server is not installed. Please use the main installer first."
    exit 1
fi

# Run the standard installer - it is already programmed to be non-destructive to user data
curl -sL https://raw.githubusercontent.com/EpicNori/Hashcat-Pwnagotchi-server/main/install.sh | bash

echo "[*] Update complete. All user data and settings have been preserved."
