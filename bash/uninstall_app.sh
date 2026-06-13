#!/bin/bash
set -euo pipefail

PURGE_DATA=0
YES=0
WIZARD=0
BACKGROUND=0
DRY_RUN=0

APP_ROOT="${HASHCAT_WPA_APP_ROOT:-/opt/hashcat-wpa-server}"
DATA_DIR="${HASHCAT_WPA_DATA_DIR:-/var/lib/hashcat-wpa-server}"
LOG_DIR="${HASHCAT_WPA_LOG_DIR:-/var/log/hashcat-wpa-server}"
SERVICE_NAME="${HASHCAT_WPA_SERVICE_NAME:-hashcat-wpa-server.service}"
SERVICE_FILE="${HASHCAT_WPA_SERVICE_FILE:-/etc/systemd/system/${SERVICE_NAME}}"
CLI_LINK="${HASHCAT_WPA_CLI_LINK:-/usr/local/bin/crackserver}"
SUDOERS_FILE="${HASHCAT_WPA_SUDOERS_FILE:-/etc/sudoers.d/hashcat-tailscale}"
APP_USER="${HASHCAT_WPA_APP_USER:-hashcat}"
PACKAGE_NAME="${HASHCAT_WPA_PACKAGE_NAME:-hashcat-wpa-server}"
LOG_FILE="${HASHCAT_WPA_UNINSTALL_LOG_FILE:-/tmp/hashcat-wpa-uninstall.log}"
CRITICAL_REMOVAL_PATHS=(
    /
    /bin
    /boot
    /dev
    /etc
    /etc/sudoers.d
    /etc/systemd
    /etc/systemd/system
    /home
    /lib
    /lib64
    /opt
    /proc
    /root
    /run
    /sbin
    /sys
    /tmp
    /usr
    /usr/bin
    /usr/local
    /usr/local/bin
    /var
    /var/lib
    /var/log
)

for arg in "$@"; do
    case "$arg" in
        --purge-data)
            PURGE_DATA=1
            ;;
        --keep-data)
            PURGE_DATA=0
            ;;
        --yes|-y)
            YES=1
            ;;
        --wizard|--interactive)
            WIZARD=1
            ;;
        --background)
            BACKGROUND=1
            ;;
        --dry-run)
            DRY_RUN=1
            ;;
        --help|-h)
            cat <<EOF
Usage: uninstall_app.sh [--wizard] [--yes] [--purge-data|--keep-data] [--background] [--dry-run]

Default behavior keeps $DATA_DIR and $LOG_DIR.
Use --purge-data only when you want to delete users, captures, databases, results, and logs.

Current paths:
  Application: $APP_ROOT
  Data:        $DATA_DIR
  Logs:        $LOG_DIR
  Service:     $SERVICE_FILE
  CLI:         $CLI_LINK
EOF
            exit 0
            ;;
        *)
            echo "[!] Unknown option: $arg"
            exit 2
            ;;
    esac
done

confirm() {
    local prompt="$1"
    local reply
    read -r -p "$prompt " reply
    [[ "${reply:-}" =~ ^[Yy]$ ]]
}

quote_cmd() {
    local arg
    local sep=""
    for arg in "$@"; do
        printf '%s%s' "$sep" "$arg"
        sep=" "
    done
    printf '\n'
}

run_cmd() {
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '[dry-run] '
        quote_cmd "$@"
    else
        "$@"
    fi
}

systemctl_maybe() {
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '[dry-run] systemctl '
        quote_cmd "$@"
        return 0
    fi
    if command -v systemctl >/dev/null 2>&1; then
        systemctl "$@" >/dev/null 2>&1 || true
    else
        echo "[*] systemctl is not available; skipping: systemctl $*"
    fi
}

normalize_removal_path() {
    local path="$1"
    while [ "${#path}" -gt 1 ] && [ "${path%/}" != "$path" ]; do
        path="${path%/}"
    done
    printf '%s' "$path"
}

assert_safe_path() {
    local path="${1:-}"
    local label="$2"
    local normalized
    local critical_path
    if [ -z "$path" ]; then
        echo "[!] Refusing to remove unsafe $label path: '${path:-<empty>}'"
        exit 1
    fi
    normalized="$(normalize_removal_path "$path")"
    if [[ "$normalized" != /* ]]; then
        echo "[!] Refusing to remove non-absolute $label path: '$path'"
        exit 1
    fi
    for critical_path in "${CRITICAL_REMOVAL_PATHS[@]}"; do
        if [ "$normalized" = "$critical_path" ]; then
            echo "[!] Refusing to remove unsafe $label path: '$normalized'"
            exit 1
        fi
    done
}

remove_tree() {
    local path="$1"
    local label="$2"
    assert_safe_path "$path" "$label"
    run_cmd rm -rf -- "$path"
}

remove_file() {
    local path="$1"
    local label="$2"
    assert_safe_path "$path" "$label"
    run_cmd rm -f -- "$path"
}

run_uninstall() {
    if [ "$EUID" -ne 0 ] && [ "$DRY_RUN" -eq 0 ]; then
        echo "[!] This script must be run as root."
        exit 1
    fi

    export DEBIAN_FRONTEND=noninteractive
    echo "[*] Stopping Hashcat WPA Server..."
    systemctl_maybe stop "$SERVICE_NAME"
    systemctl_maybe disable "$SERVICE_NAME"

    echo "[*] Removing application package while preserving data..."
    if command -v dpkg >/dev/null 2>&1 && dpkg -s "$PACKAGE_NAME" >/dev/null 2>&1; then
        run_cmd dpkg --remove "$PACKAGE_NAME"
    else
        echo "[*] Debian package is not registered; removing installed app files directly."
        remove_tree "$APP_ROOT" "application"
        remove_file "$SERVICE_FILE" "service"
        remove_file "$CLI_LINK" "CLI"
    fi

    remove_file "$SUDOERS_FILE" "sudoers"
    systemctl_maybe daemon-reload

    if [ "$PURGE_DATA" -eq 1 ]; then
        echo "[!] Purging user data, captures, results, settings, and logs..."
        remove_tree "$DATA_DIR" "data"
        remove_tree "$LOG_DIR" "logs"
        if [ "$DRY_RUN" -eq 1 ]; then
            echo "[dry-run] userdel $APP_USER"
        elif id "$APP_USER" >/dev/null 2>&1; then
            userdel "$APP_USER" >/dev/null 2>&1 || true
        fi
    else
        echo "[+] Data kept in $DATA_DIR."
        echo "[+] Logs kept in $LOG_DIR."
    fi

    echo "[+] Uninstall complete."
}

if [ "$WIZARD" -eq 1 ] && [ "$YES" -eq 0 ]; then
    echo "====================================================="
    echo "  Hashcat WPA Server Uninstall"
    echo "====================================================="
    echo "This removes the application and background service."
    echo "Default: keep users, captures, results, settings, and logs."
    echo "Data: $DATA_DIR"
    echo "Logs: $LOG_DIR"
    if ! confirm "Continue uninstall? [y/N]"; then
        echo "Cancelled."
        exit 0
    fi
    if confirm "Delete users, captures, results, settings, and logs too? [y/N]"; then
        PURGE_DATA=1
    fi
fi

if [ "$YES" -eq 0 ] && [ "$WIZARD" -eq 0 ] && [ -t 0 ]; then
    echo "[!] Refusing non-wizard interactive uninstall."
    echo "    Use: uninstall_app.sh --wizard"
    exit 2
fi

if [ "$BACKGROUND" -eq 1 ] || { [ "$YES" -eq 0 ] && [ ! -t 0 ] && [ "$WIZARD" -eq 0 ]; }; then
    purge_arg="--keep-data"
    dry_args=()
    if [ "$PURGE_DATA" -eq 1 ]; then
        purge_arg="--purge-data"
    fi
    if [ "$DRY_RUN" -eq 1 ]; then
        dry_args=(--dry-run)
    fi
    mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true
    echo "[*] Starting uninstall in the background..."
    nohup env \
        HASHCAT_WPA_APP_ROOT="$APP_ROOT" \
        HASHCAT_WPA_DATA_DIR="$DATA_DIR" \
        HASHCAT_WPA_LOG_DIR="$LOG_DIR" \
        HASHCAT_WPA_SERVICE_NAME="$SERVICE_NAME" \
        HASHCAT_WPA_SERVICE_FILE="$SERVICE_FILE" \
        HASHCAT_WPA_CLI_LINK="$CLI_LINK" \
        HASHCAT_WPA_SUDOERS_FILE="$SUDOERS_FILE" \
        HASHCAT_WPA_APP_USER="$APP_USER" \
        HASHCAT_WPA_PACKAGE_NAME="$PACKAGE_NAME" \
        HASHCAT_WPA_UNINSTALL_LOG_FILE="$LOG_FILE" \
        bash "$0" --yes "$purge_arg" "${dry_args[@]}" > "$LOG_FILE" 2>&1 &
    echo "[+] Uninstall process spawned. Log: $LOG_FILE"
    exit 0
fi

run_uninstall
