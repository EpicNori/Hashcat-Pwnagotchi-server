#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$SCRIPT_DIR/common.sh"

# Called via root/sudo from the web interface. The tunnel token is read from
# stdin so it is not saved in the app settings file.
PUBLIC_HOSTNAME="${1:-}"
TOKEN="${CLOUDFLARE_TUNNEL_TOKEN:-}"

if [ -z "$TOKEN" ] && [ ! -t 0 ]; then
    TOKEN="$(cat | tr -d '\r\n')"
fi

if [ -z "$TOKEN" ] && [ -t 0 ]; then
    read -r -s -p "Cloudflare Tunnel token: " TOKEN
    echo
fi

if [ -z "$PUBLIC_HOSTNAME" ]; then
    echo "Usage: install_cloudflare_tunnel.sh <public-hostname>"
    exit 2
fi

if [ -z "$TOKEN" ]; then
    echo "Cloudflare Tunnel token is required."
    exit 2
fi

install_cloudflared() {
    if command -v cloudflared >/dev/null 2>&1; then
        return
    fi

    if ! asset="$(cloudflared_asset_for_arch)"; then
        echo "Unsupported architecture for automatic cloudflared install: $(uname -m)"
        exit 1
    fi

    tmp="$(mktemp)"
    curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/${asset}" -o "$tmp"
    install -m 0755 "$tmp" /usr/local/bin/cloudflared
    rm -f "$tmp"
}

install_cloudflared

if ! command -v systemctl >/dev/null 2>&1 || ! pidof systemd >/dev/null 2>&1; then
    echo "Systemd is not running, so the Cloudflare Tunnel service cannot be installed automatically."
    echo "Run this on a normal systemd Linux host, or start cloudflared manually with your tunnel token."
    exit 1
fi

if systemctl list-unit-files cloudflared.service >/dev/null 2>&1; then
    systemctl stop cloudflared >/dev/null 2>&1 || true
    cloudflared service uninstall >/dev/null 2>&1 || true
fi

cloudflared service install "$TOKEN"
systemctl enable --now cloudflared

echo "Cloudflare Tunnel connector is installed for https://${PUBLIC_HOSTNAME}"
