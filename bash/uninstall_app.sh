#!/bin/bash
set -euo pipefail

PURGE_DATA=0
YES=0
WIZARD=0
BACKGROUND=0
LOG_FILE="/tmp/hashcat-wpa-uninstall.log"

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
        --help|-h)
            cat <<'EOF'
Usage: uninstall_app.sh [--wizard] [--yes] [--purge-data|--keep-data] [--background]

Default behavior keeps /var/lib/hashcat-wpa-server and /var/log/hashcat-wpa-server.
Use --purge-data only when you want to delete users, captures, databases, results, and logs.
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

run_uninstall() {
    if [ "$EUID" -ne 0 ]; then
        echo "[!] This script must be run as root."
        exit 1
    fi

    export DEBIAN_FRONTEND=noninteractive
    echo "[*] Stopping Hashcat WPA Server..."
    systemctl stop hashcat-wpa-server.service >/dev/null 2>&1 || true
    systemctl disable hashcat-wpa-server.service >/dev/null 2>&1 || true

    echo "[*] Removing application package while preserving data..."
    if dpkg -s hashcat-wpa-server >/dev/null 2>&1; then
        dpkg --remove hashcat-wpa-server
    else
        echo "[*] Debian package is not registered; removing installed app files directly."
        rm -rf /opt/hashcat-wpa-server
        rm -f /etc/systemd/system/hashcat-wpa-server.service
        rm -f /usr/local/bin/crackserver
    fi

    rm -f /etc/sudoers.d/hashcat-tailscale
    systemctl daemon-reload >/dev/null 2>&1 || true

    if [ "$PURGE_DATA" -eq 1 ]; then
        echo "[!] Purging user data, captures, results, settings, and logs..."
        rm -rf /var/lib/hashcat-wpa-server
        rm -rf /var/log/hashcat-wpa-server
        if id "hashcat" >/dev/null 2>&1; then
            userdel hashcat >/dev/null 2>&1 || true
        fi
    else
        echo "[+] Data kept in /var/lib/hashcat-wpa-server."
        echo "[+] Logs kept in /var/log/hashcat-wpa-server."
    fi

    echo "[+] Uninstall complete."
}

if [ "$WIZARD" -eq 1 ] && [ "$YES" -eq 0 ]; then
    echo "====================================================="
    echo "  Hashcat WPA Server Uninstall"
    echo "====================================================="
    echo "This removes the application and background service."
    echo "User data is kept unless you explicitly choose to purge it."
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

if [ "$BACKGROUND" -eq 1 ] || { [ ! -t 0 ] && [ "$WIZARD" -eq 0 ]; }; then
    echo "[*] Starting uninstall in the background..."
    nohup bash "$0" --yes "$([ "$PURGE_DATA" -eq 1 ] && echo --purge-data || echo --keep-data)" > "$LOG_FILE" 2>&1 &
    echo "[+] Uninstall process spawned. Log: $LOG_FILE"
    exit 0
fi

run_uninstall
