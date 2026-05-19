#!/bin/bash
set -euo pipefail

# Called via root/sudo from the web interface. The tunnel token is read from
# stdin so it is not saved in the app settings file.
PUBLIC_HOSTNAME="${1:-}"
TOKEN="$(cat | tr -d '\r\n')"

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

    arch="$(uname -m)"
    case "$arch" in
        x86_64|amd64) asset="cloudflared-linux-amd64" ;;
        aarch64|arm64) asset="cloudflared-linux-arm64" ;;
        armv7l|armhf) asset="cloudflared-linux-arm" ;;
        *) echo "Unsupported architecture for automatic cloudflared install: $arch"; exit 1 ;;
    esac

    tmp="$(mktemp)"
    curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/${asset}" -o "$tmp"
    install -m 0755 "$tmp" /usr/local/bin/cloudflared
    rm -f "$tmp"
}

install_cloudflared

if systemctl list-unit-files cloudflared.service >/dev/null 2>&1; then
    systemctl stop cloudflared >/dev/null 2>&1 || true
    cloudflared service uninstall >/dev/null 2>&1 || true
fi

cloudflared service install "$TOKEN"
systemctl enable --now cloudflared

echo "Cloudflare Tunnel connector is installed for https://${PUBLIC_HOSTNAME}"
