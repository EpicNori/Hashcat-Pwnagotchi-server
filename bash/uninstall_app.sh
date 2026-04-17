#!/bin/bash
set -e

echo "Starting application uninstallation..."
# We use nohup to ensure the uninstall process survives the systemd service termination.
nohup bash -c '
  # Give the web server a few seconds to return the HTTP response back to the user
  sleep 4
  
  # Ensure running as root
  if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root."
    exit 1
  fi
  
  export DEBIAN_FRONTEND=noninteractive
  echo "Purging hashcat-wpa-server Debian package from the system..."
  
  # Tell the Debian package manager to completely erase the application and systemd service
  dpkg --purge hashcat-wpa-server
  
' > /tmp/hashcat-wpa-uninstall.log 2>&1 &

echo "Uninstall process spawned in the background."
exit 0
