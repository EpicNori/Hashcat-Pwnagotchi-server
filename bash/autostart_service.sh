#!/bin/bash

set -e

SERVICE_NAME="hashcat-wpa-server.service"

case "$1" in
    enable)
        systemctl enable "$SERVICE_NAME"
        echo "enabled"
        ;;
    disable)
        systemctl disable "$SERVICE_NAME"
        echo "disabled"
        ;;
    status)
        if systemctl is-enabled --quiet "$SERVICE_NAME"; then
            echo "enabled"
        else
            echo "disabled"
        fi
        ;;
    *)
        echo "Usage: $0 {enable|disable|status}" >&2
        exit 1
        ;;
esac
