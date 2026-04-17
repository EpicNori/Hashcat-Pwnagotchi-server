#!/bin/bash
set -e

echo "Starting application update..."
# We use systemd-run to spawn the update in a NEW transient service.
# This ensures that when we call "systemctl stop", it DOES NOT kill the update process!
systemd-run --unit=hashcat-server-updater --remain-after-exit bash -c '
  sleep 3
  export DEBIAN_FRONTEND=noninteractive
  echo "[*] Updater: Ensuring dpkg is clean..."
  dpkg --configure -a || true
  
  echo "[*] Updater: Fetching latest code and installing..."
  curl -sL https://raw.githubusercontent.com/EpicNori/hashcat-wpa-server/master/update.sh | bash
  
  echo "[*] Updater: FINISHED. Server should be back online now."
' > /dev/null 2>&1

echo "Update process spawned in the background."
exit 0
