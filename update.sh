#!/bin/bash
set -e
set -o pipefail

# CrackServer Safe Update Utility
# This script ONLY updates the application logic and binaries.
# Your User Data, Databases, and Handshakes are TIED to /var/lib/hashcat-wpa-server/
# and are NEVER touched by this script.

SERVICE_NAME="hashcat-wpa-server.service"
PROGRESS_FILE="${HASHCAT_WPA_PROGRESS_FILE:-/var/log/hashcat-wpa-server/app_update.progress}"
MANUAL_RESTART_REQUIRED=0

write_progress() {
    local state="$1"
    local percent="$2"
    local message="$3"
    mkdir -p "$(dirname "$PROGRESS_FILE")"
    printf '%s|%s|%s\n' "$state" "$percent" "$message" > "$PROGRESS_FILE"
}

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    UI_RESET=$'\033[0m'
    UI_BOLD=$'\033[1m'
    UI_DIM=$'\033[2m'
    UI_CYAN=$'\033[36m'
    UI_GREEN=$'\033[32m'
    UI_AMBER=$'\033[33m'
    UI_RED=$'\033[31m'
else
    UI_RESET=""
    UI_BOLD=""
    UI_DIM=""
    UI_CYAN=""
    UI_GREEN=""
    UI_AMBER=""
    UI_RED=""
fi

ui_info() { printf '%b[*]%b %s\n' "$UI_CYAN" "$UI_RESET" "$*"; }
ui_success() { printf '%b[+]%b %s\n' "$UI_GREEN" "$UI_RESET" "$*"; }
ui_warn() { printf '%b[!]%b %s\n' "$UI_AMBER" "$UI_RESET" "$*"; }
ui_error() { printf '%b[!]%b %s\n' "$UI_RED" "$UI_RESET" "$*"; }
ui_step() {
    local step="$1"
    shift
    printf '%b[%s]%b %s\n' "$UI_CYAN" "$step" "$UI_RESET" "$*"
}
ui_kv() {
    local key="$1"
    local value="$2"
    printf '  %b%-18s%b %s\n' "$UI_DIM" "${key}:" "$UI_RESET" "$value"
}
ui_heading() {
    printf '\n%b%s%b\n' "$UI_BOLD$UI_CYAN" "$*" "$UI_RESET"
    printf '%b%s%b\n' "$UI_DIM" "===========================================================================" "$UI_RESET"
}

restart_service() {
    if ! pidof systemd >/dev/null; then
        ui_warn "Systemd is not running, so the background service cannot be restarted automatically."
        ui_warn "Restart the manual gunicorn process to load the updated code."
        MANUAL_RESTART_REQUIRED=1
        return 0
    fi

    ui_info "Reloading and restarting ${SERVICE_NAME}..."
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    systemctl restart "$SERVICE_NAME"
    write_progress running 90 "Restarting the server"

    ui_info "Verifying service health..."
    sleep 2
    if ! systemctl is-active --quiet "$SERVICE_NAME"; then
        ui_error "${SERVICE_NAME} failed to start after update."
        write_progress failed 0 "The server failed to restart after the update."
        systemctl status "$SERVICE_NAME" --no-pager || true
        exit 1
    fi

    ui_success "${SERVICE_NAME} is active."
}

ui_heading "Hashcat WPA Safe Update"
ui_kv "Data" "preserved"
ui_kv "Target service" "$SERVICE_NAME"
ui_step "01/04" "Checking previous installation"
write_progress running 15 "Checking the current installation"

if [ ! -d "/opt/hashcat-wpa-server" ]; then
    ui_error "Error: Server is not installed. Please use the main installer first."
    write_progress failed 0 "No existing installation was found."
    exit 1
fi

# Run the standard installer - it is already programmed to be non-destructive to user data
ui_step "02/04" "Downloading and applying update"
write_progress running 35 "Downloading and installing the updated application"
curl -sL https://raw.githubusercontent.com/EpicNori/Hashcat-Pwnagotchi-server/main/install.sh | bash

ui_step "03/04" "Restarting service"
restart_service

if [ "$MANUAL_RESTART_REQUIRED" -eq 1 ]; then
    ui_info "Update complete. All user data and settings have been preserved."
    ui_warn "Manual restart required because systemd is not running."
    write_progress success 100 "Update complete. Restart the manual gunicorn process to load it."
else
    ui_success "Update complete. All user data and settings have been preserved."
    write_progress success 100 "Application update completed successfully"
fi
ui_step "04/04" "Finished"
