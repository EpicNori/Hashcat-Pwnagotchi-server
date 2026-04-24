#!/bin/bash
set -e

UPDATE_LOG="/var/log/hashcat-wpa-server/updater.log"
PROGRESS_FILE="${HASHCAT_WPA_PROGRESS_FILE:-/var/log/hashcat-wpa-server/app_update.progress}"

write_progress() {
  local state="$1"
  local percent="$2"
  local message="$3"
  mkdir -p "$(dirname "$PROGRESS_FILE")"
  printf '%s|%s|%s\n' "$state" "$percent" "$message" > "$PROGRESS_FILE"
}

export HASHCAT_WPA_PROGRESS_FILE="$PROGRESS_FILE"
export HASHCAT_WPA_NVIDIA_PROGRESS_FILE="${HASHCAT_WPA_NVIDIA_PROGRESS_FILE:-/var/log/hashcat-wpa-server/nvidia_install.progress}"

echo "Starting application update..."
# We use systemd-run to spawn the update in a NEW transient service.
# This ensures that when we call "systemctl stop", it DOES NOT kill the update process!
systemd-run --unit=hashcat-server-updater --remain-after-exit bash -c '
  mkdir -p /var/log/hashcat-wpa-server
  exec > "'"$UPDATE_LOG"'" 2>&1
  PROGRESS_FILE="${HASHCAT_WPA_PROGRESS_FILE:-/var/log/hashcat-wpa-server/app_update.progress}"
  write_progress() {
    local state="$1"
    local percent="$2"
    local message="$3"
    mkdir -p "$(dirname "$PROGRESS_FILE")"
    printf "%s|%s|%s\n" "$state" "$percent" "$message" > "$PROGRESS_FILE"
  }
  trap "write_progress failed 0 \"The update failed. Check the updater log for details.\"" ERR
  echo "===== $(date) ====="
  write_progress running 5 "Preparing the update service"
  sleep 3
  export DEBIAN_FRONTEND=noninteractive
  echo "[*] Updater: Ensuring dpkg is clean..."
  write_progress running 15 "Ensuring package manager state is clean"
  dpkg --configure -a || true
  
  echo "[*] Updater: Fetching latest code and installing..."
  write_progress running 35 "Downloading the latest application update"
  curl -sL https://raw.githubusercontent.com/EpicNori/Hashcat-Pwnagotchi-server/main/update.sh | bash
  
  echo "[*] Updater: FINISHED. Server should be back online now."
  write_progress success 100 "Application update finished"
' > /dev/null 2>&1

echo "Update process spawned in the background."
exit 0
