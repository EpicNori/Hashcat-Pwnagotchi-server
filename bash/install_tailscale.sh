#!/bin/bash
set -e
set -o pipefail

# This script is called via root/sudo from the web interface or CLI.
# Prefer stdin so auth keys do not have to appear in process arguments.
AUTH_KEY="${1:-}"
DRY_RUN="${HASHCAT_WPA_DRY_RUN:-0}"
if [ -z "$AUTH_KEY" ] && ! [ -t 0 ]; then
    AUTH_KEY="$(cat | tr -d '\r\n')"
fi

# Check if tailscale is already installed
if ! command -v tailscale >/dev/null 2>&1; then
    echo "Installing Tailscale..."
    if [ "$DRY_RUN" = "1" ]; then
        echo "[dry-run] curl -fsSL https://tailscale.com/install.sh | sh"
    else
        curl -fsSL https://tailscale.com/install.sh | sh
    fi
fi

if [ -n "$AUTH_KEY" ]; then
    echo "Authenticating Tailscale..."
    if [ "$DRY_RUN" = "1" ]; then
        echo "[dry-run] tailscale up --authkey=<redacted> --reset"
    else
        tailscale up --authkey="$AUTH_KEY" --reset
    fi
else
    # Just bring it up if it was previously configured
    if [ "$DRY_RUN" = "1" ]; then
        echo "[dry-run] tailscale up"
    else
        tailscale up
    fi
fi

if [ "$DRY_RUN" = "1" ]; then
    echo "Tailscale would be active."
else
    echo "Tailscale is now active."
fi
