#!/bin/bash
set -e

NVIDIA_DRIVER_STATUS="not-needed"

ensure_service_running() {
    local service_name="$1"

    if ! command -v systemctl >/dev/null 2>&1 || ! pidof systemd >/dev/null; then
        return 0
    fi

    systemctl daemon-reload
    systemctl enable "$service_name"
    systemctl restart "$service_name"

    for _ in $(seq 1 15); do
        if systemctl is-active --quiet "$service_name"; then
            return 0
        fi
        sleep 1
    done

    echo "[!] ${service_name} did not become active after installation."
    systemctl --no-pager --full status "$service_name" || true
    exit 1
}

os_id_like_contains() {
    local needle="$1"
    [[ " ${ID_LIKE:-} " == *" ${needle} "* ]]
}

has_nvidia_gpu() {
    if ! command -v lspci >/dev/null 2>&1; then
        return 1
    fi

    lspci -nn | grep -Eqi '((VGA|3D|Display).*(NVIDIA|GeForce|Quadro|Tesla))|((NVIDIA|GeForce|Quadro|Tesla).*(VGA|3D|Display))'
}

install_nvidia_drivers_if_needed() {
    if command -v nvidia-smi >/dev/null 2>&1 && lsmod | grep -q '^nvidia'; then
        NVIDIA_DRIVER_STATUS="already-installed"
        echo "[*] NVIDIA GPU runtime already appears to be installed."
        return 0
    fi

    if ! has_nvidia_gpu; then
        return 0
    fi

    echo "[*] NVIDIA GPU detected. Attempting automatic driver installation..."

    if [ -r /etc/os-release ]; then
        # shellcheck disable=SC1091
        . /etc/os-release
    fi

    if [ "${ID:-}" = "ubuntu" ] || os_id_like_contains "ubuntu"; then
        apt-get install -y ubuntu-drivers-common
        if ubuntu-drivers autoinstall; then
            NVIDIA_DRIVER_STATUS="installed"
            return 0
        fi

        echo "[!] ubuntu-drivers autoinstall failed, falling back to apt package installation..."
        if apt-get install -y nvidia-driver; then
            NVIDIA_DRIVER_STATUS="installed"
            return 0
        fi
    elif [ "${ID:-}" = "debian" ] || [ "${ID:-}" = "kali" ] || os_id_like_contains "debian"; then
        apt-get install -y "linux-headers-$(uname -r)" || true
        if apt-get install -y nvidia-driver firmware-misc-nonfree; then
            NVIDIA_DRIVER_STATUS="installed"
            return 0
        fi

        echo "[!] Full Debian-family NVIDIA package set failed, retrying with the base driver package..."
        if apt-get install -y nvidia-driver; then
            NVIDIA_DRIVER_STATUS="installed"
            return 0
        fi
    else
        echo "[!] NVIDIA GPU detected, but this installer only knows how to auto-install drivers on Debian-family Linux."
        NVIDIA_DRIVER_STATUS="manual-required"
        return 0
    fi

    echo "[!] NVIDIA GPU detected, but the driver installation step did not complete successfully."
    echo "[!] The server was installed, but you may need to install the NVIDIA driver manually before GPU cracking works."
    NVIDIA_DRIVER_STATUS="manual-required"
}

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
apt-get install -y curl git dpkg-dev debhelper pciutils python3 python3-venv systemd hashcat hcxtools
install_nvidia_drivers_if_needed

echo "[*] Cloning the extremely fast hashcat-wpa-server..."
cd /tmp
rm -rf hashcat-wpa-build-env
mkdir hashcat-wpa-build-env
cd hashcat-wpa-build-env

git clone https://github.com/EpicNori/Hashcat-Pwnagotchi-server.git
cd Hashcat-Pwnagotchi-server

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
ensure_service_running "hashcat-wpa-server.service"

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

if [ "$NVIDIA_DRIVER_STATUS" = "installed" ]; then
    echo "[+] NVIDIA drivers were installed automatically for detected GPU hardware."
    echo "[+] A reboot may still be required before Hashcat can use the GPU."
elif [ "$NVIDIA_DRIVER_STATUS" = "already-installed" ]; then
    echo "[+] NVIDIA GPU runtime was already available on this machine."
elif [ "$NVIDIA_DRIVER_STATUS" = "manual-required" ]; then
    echo "[!] NVIDIA GPU detected, but driver setup still needs manual attention before GPU cracking will work."
fi

echo "[+] "
echo "[+] Web Interface URL:   http://127.0.0.1:9111"
echo "[+] Network Access:      http://$(hostname -I | awk '{print $1}'):9111"
echo "[+] "
echo "[+] Default Login User:  admin"
echo "[+] Default Password:    changeme"
echo "=========================================================================="
