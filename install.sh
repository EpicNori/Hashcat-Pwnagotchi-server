#!/bin/bash
set -e

# Ensure script is being run as root
if [ "$EUID" -ne 0 ]; then
  echo "[!] Please run this installation script as root (sudo bash install.sh)"
  exit 1
fi

echo "[*] Ensuring package manager is in a clean state (Waiting for locks)..."
while fuser /var/lib/dpkg/lock >/dev/null 2>&1 ; do
    echo "[*] Waiting for other software managers to finish..."
    sleep 5
done

export DEBIAN_FRONTEND=noninteractive
dpkg --configure -a || true
apt-get install -f -y || true

echo "[*] Updating package list and installing build dependencies..."
apt-get update
apt-get install -y git dpkg-dev debhelper python3 python3-venv systemd hashcat hcxtools

echo "[*] Cloning the extremely fast hashcat-wpa-server..."
cd /tmp
rm -rf hashcat-wpa-build-env
mkdir hashcat-wpa-build-env
cd hashcat-wpa-build-env

git clone https://github.com/EpicNori/hashcat-wpa-server.git
cd hashcat-wpa-server

echo "[*] Compiling the automated Debian package..."
chmod +x debian/rules
dpkg-buildpackage -us -uc -b

echo "[*] Installing to the system..."
cd ..
# Ensure tailscale is installed for remote access
if ! command -v tailscale >/dev/null 2>&1; then
    echo "[*] Installing Tailscale for remote VPN access using official script..."
    curl -fsSL https://tailscale.com/install.sh | sh
fi

echo "[*] Unpacking and configuring the server files..."
dpkg -i hashcat-wpa-server_*.deb || apt-get install -f -y

# Explicitly ensure service is up after dpkg finish
echo "[*] Finalizing service state..."
systemctl daemon-reload
systemctl enable hashcat-wpa-server.service
systemctl restart hashcat-wpa-server.service

echo "[*] Cleaning up build files..."
cd /tmp
rm -rf hashcat-wpa-build-env

# Attempt to open the firewall port safely if UFW is installed
if command -v ufw >/dev/null 2>&1; then
    echo "[*] Opening port 9111 on local UFW firewall..."
    ufw allow 9111/tcp >/dev/null 2>&1 || true
fi

echo ""
echo "=========================================================================="
if ! pidof systemd >/dev/null; then
    echo "[!] WARNING: Systemd is not running (Are you on WSL or Docker?)."
    echo "    The automatic background service could not be started."
    echo ""
    echo "    To start the server MANUALLY, run:"
    echo "    sudo -u hashcat /opt/hashcat-wpa-server/venv/bin/gunicorn --chdir /opt/hashcat-wpa-server app:app --bind 0.0.0.0:9111"
else
    echo "[+] SUCCESS! hashcat-wpa-server has been installed and is now fully running!"
    echo "[+] No further configuration is needed. It automatically runs in the background."
fi

echo "[+] "
echo "[+] Web Interface URL:   http://127.0.0.1:9111"
echo "[+] Network Access:      http://$(hostname -I | awk '{print $1}'):9111"
echo "[+] "
echo "[+] Default Login User:  admin"
echo "[+] Default Password:    changeme"
echo "=========================================================================="
