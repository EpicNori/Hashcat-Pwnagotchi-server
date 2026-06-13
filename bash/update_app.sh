#!/bin/bash
set -e
set -o pipefail

UPDATE_LOG="/var/log/hashcat-wpa-server/updater.log"
PROGRESS_FILE="${HASHCAT_WPA_PROGRESS_FILE:-/var/log/hashcat-wpa-server/app_update.progress}"
NVIDIA_PROGRESS_FILE="${HASHCAT_WPA_NVIDIA_PROGRESS_FILE:-/var/log/hashcat-wpa-server/nvidia_install.progress}"

write_progress() {
  local state="$1"
  local percent="$2"
  local message="$3"
  local progress_file="${HASHCAT_WPA_PROGRESS_FILE:-$PROGRESS_FILE}"
  mkdir -p "$(dirname "$progress_file")"
  printf '%s|%s|%s\n' "$state" "$percent" "$message" > "$progress_file"
}

export HASHCAT_WPA_PROGRESS_FILE="$PROGRESS_FILE"
export HASHCAT_WPA_NVIDIA_PROGRESS_FILE="$NVIDIA_PROGRESS_FILE"

run_update_job() {
  set -e
  set -o pipefail
  mkdir -p /var/log/hashcat-wpa-server
  exec > "$UPDATE_LOG" 2>&1
  trap 'write_progress failed 0 "The update failed. Check the updater log for details."' ERR
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
}

echo "Starting application update..."
# We use systemd-run to spawn the update in a NEW transient service.
# This ensures that when we call "systemctl stop", it DOES NOT kill the update process!
UNIT_NAME="hashcat-server-updater-$(date +%s)"
JOB_COMMAND="$(declare -f write_progress); $(declare -f run_update_job); run_update_job"

if command -v systemd-run >/dev/null 2>&1 && pidof systemd >/dev/null 2>&1; then
  systemctl reset-failed "hashcat-server-updater.service" >/dev/null 2>&1 || true
  systemd-run \
    --unit="$UNIT_NAME" \
    --setenv=HASHCAT_WPA_PROGRESS_FILE="$PROGRESS_FILE" \
    --setenv=HASHCAT_WPA_NVIDIA_PROGRESS_FILE="$NVIDIA_PROGRESS_FILE" \
    --setenv=UPDATE_LOG="$UPDATE_LOG" \
    bash -c "$JOB_COMMAND" > /dev/null 2>&1
else
  echo "systemd-run is not available; using nohup fallback updater."
  write_progress running 5 "Starting updater without systemd"
  nohup env \
    HASHCAT_WPA_PROGRESS_FILE="$PROGRESS_FILE" \
    HASHCAT_WPA_NVIDIA_PROGRESS_FILE="$NVIDIA_PROGRESS_FILE" \
    UPDATE_LOG="$UPDATE_LOG" \
    bash -c "$JOB_COMMAND" > /dev/null 2>&1 &
fi

echo "Update process spawned in the background."
exit 0
