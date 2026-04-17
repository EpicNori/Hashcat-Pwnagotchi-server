#!/bin/bash
set -e

UPDATE_LOG="/var/log/hashcat-wpa-server/updater.log"

echo "Starting application update..."
# We use systemd-run to spawn the update in a NEW transient service.
# This ensures that when we call "systemctl stop", it DOES NOT kill the update process!
systemd-run --unit=hashcat-server-updater --remain-after-exit bash -c '
  mkdir -p /var/log/hashcat-wpa-server
  exec > "'"$UPDATE_LOG"'" 2>&1
  echo "===== $(date) ====="
  sleep 3
  export DEBIAN_FRONTEND=noninteractive
  echo "[*] Updater: Ensuring dpkg is clean..."
  dpkg --configure -a || true
  
  echo "[*] Updater: Fetching latest code and installing..."
  curl -sL https://raw.githubusercontent.com/EpicNori/Hashcat-Pwnagotchi-server/main/update.sh | bash
  
  echo "[*] Updater: FINISHED. Server should be back online now."
' > /dev/null 2>&1

echo "Update process spawned in the background."
exit 0
