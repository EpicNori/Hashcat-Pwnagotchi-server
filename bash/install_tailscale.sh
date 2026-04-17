#!/bin/bash
set -e

# This script is called via root/sudo from the web interface
AUTH_KEY="$1"

# Check if tailscale is already installed
if ! command -v tailscale >/dev/null 2>&1; then
    echo "Installing Tailscale..."
    curl -fsSL https://tailscale.com/install.sh | sh
fi

if [ -n "$AUTH_KEY" ]; then
    echo "Authenticating Tailscale..."
    tailscale up --authkey="$AUTH_KEY" --reset
else
    # Just bring it up if it was previously configured
    tailscale up
fi

echo "Tailscale is now active."
