#!/bin/bash
# run on server

set -euo pipefail

DATA_DIR="${HASHCAT_WPA_SERVER_HOME:-${HASHCAT_WPA_DATA_DIR:-$HOME/.hashcat/wpa-server}}"
BRAIN_DIR="$DATA_DIR/brain"
PASSWORD_FILE="$BRAIN_DIR/hashcat_brain_password"

mkdir -p "$BRAIN_DIR"
cd "$BRAIN_DIR"

if [ ! -s "$PASSWORD_FILE" ]; then
    set +o pipefail
    brain_password="$(LC_ALL=C tr -dc '[:alnum:]' < /dev/urandom | head -c20)"
    set -o pipefail
    printf '%s\n' "$brain_password" > "$PASSWORD_FILE"
    chmod 600 "$PASSWORD_FILE" || true
fi

exec hashcat --brain-server --brain-password="$(cat "$PASSWORD_FILE")"
