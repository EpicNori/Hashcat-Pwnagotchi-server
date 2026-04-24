#!/bin/bash
set -e

NVIDIA_DRIVER_STATUS="not-needed"
PROGRESS_FILE="${HASHCAT_WPA_PROGRESS_FILE:-/var/log/hashcat-wpa-server/app_update.progress}"
NVIDIA_PROGRESS_FILE="${HASHCAT_WPA_NVIDIA_PROGRESS_FILE:-/var/log/hashcat-wpa-server/nvidia_install.progress}"

write_progress() {
    local state="$1"
    local percent="$2"
    local message="$3"
    mkdir -p "$(dirname "$PROGRESS_FILE")"
    printf '%s|%s|%s\n' "$state" "$percent" "$message" > "$PROGRESS_FILE"
}

write_nvidia_progress() {
    local state="$1"
    local percent="$2"
    local message="$3"
    mkdir -p "$(dirname "$NVIDIA_PROGRESS_FILE")"
    printf '%s|%s|%s\n' "$state" "$percent" "$message" > "$NVIDIA_PROGRESS_FILE"
}

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
        write_nvidia_progress success 100 "NVIDIA drivers are already installed"
        return 0
    fi

    if ! has_nvidia_gpu; then
        write_nvidia_progress not-applicable 100 "No NVIDIA GPU detected"
        return 0
    fi

    echo "[*] NVIDIA GPU detected. Attempting automatic driver installation..."
    write_nvidia_progress running 10 "Detecting the Linux distribution"

    if [ -r /etc/os-release ]; then
        # shellcheck disable=SC1091
        . /etc/os-release
    fi

    if [ "${ID:-}" = "ubuntu" ] || os_id_like_contains "ubuntu"; then
        write_nvidia_progress running 35 "Installing Ubuntu driver helpers"
        apt-get install -y ubuntu-drivers-common
        write_nvidia_progress running 60 "Installing NVIDIA drivers"
        if ubuntu-drivers autoinstall; then
            NVIDIA_DRIVER_STATUS="installed"
            write_nvidia_progress success 100 "NVIDIA drivers installed successfully"
            return 0
        fi

        echo "[!] ubuntu-drivers autoinstall failed, falling back to apt package installation..."
        write_nvidia_progress running 70 "Retrying with the NVIDIA package"
        if apt-get install -y nvidia-driver; then
            NVIDIA_DRIVER_STATUS="installed"
            write_nvidia_progress success 100 "NVIDIA drivers installed successfully"
            return 0
        fi
    elif [ "${ID:-}" = "debian" ] || [ "${ID:-}" = "kali" ] || os_id_like_contains "debian"; then
        write_nvidia_progress running 35 "Installing kernel headers and NVIDIA packages"
        apt-get install -y "linux-headers-$(uname -r)" || true
        write_nvidia_progress running 60 "Installing NVIDIA drivers"
        if apt-get install -y nvidia-driver firmware-misc-nonfree; then
            NVIDIA_DRIVER_STATUS="installed"
            write_nvidia_progress success 100 "NVIDIA drivers installed successfully"
            return 0
        fi

        echo "[!] Full Debian-family NVIDIA package set failed, retrying with the base driver package..."
        write_nvidia_progress running 70 "Retrying the base NVIDIA driver package"
        if apt-get install -y nvidia-driver; then
            NVIDIA_DRIVER_STATUS="installed"
            write_nvidia_progress success 100 "NVIDIA drivers installed successfully"
            return 0
        fi
    else
        echo "[!] NVIDIA GPU detected, but this installer only knows how to auto-install drivers on Debian-family Linux."
        NVIDIA_DRIVER_STATUS="manual-required"
        write_nvidia_progress not-applicable 100 "Automatic NVIDIA installation is not supported on this Linux distribution"
        return 0
    fi

    echo "[!] NVIDIA GPU detected, but the driver installation step did not complete successfully."
    echo "[!] The server was installed, but you may need to install the NVIDIA driver manually before GPU cracking works."
    NVIDIA_DRIVER_STATUS="manual-required"
    write_nvidia_progress failed 0 "Automatic NVIDIA driver installation did not complete successfully"
}

# Ensure script is being run as root
if [ "$EUID" -ne 0 ]; then
  echo "[!] Please run this installation script as root (sudo bash install.sh)"
  exit 1
fi

echo "[*] Ensuring package manager is in a clean state (Waiting for locks)..."
write_progress running 5 "Preparing the Linux package manager"
while fuser /var/lib/dpkg/lock >/dev/null 2>&1 ; do
    echo "[*] Waiting for other software managers to finish..."
    sleep 5
done

export DEBIAN_FRONTEND=noninteractive
dpkg --configure -a || true
apt-get install -f -y || true

echo "[*] Updating package list and installing build dependencies..."
write_progress running 15 "Installing application dependencies"
apt-get update
apt-get install -y curl git dpkg-dev debhelper pciutils python3 python3-venv systemd hashcat hcxtools
install_nvidia_drivers_if_needed

echo "[*] Cloning the extremely fast hashcat-wpa-server..."
write_progress running 35 "Downloading the latest application source"
cd /tmp
rm -rf hashcat-wpa-build-env
mkdir hashcat-wpa-build-env
cd hashcat-wpa-build-env

git clone https://github.com/EpicNori/Hashcat-Pwnagotchi-server.git
cd Hashcat-Pwnagotchi-server

echo "[*] Compiling the automated Debian package..."
write_progress running 55 "Building the application package"
chmod +x debian/rules
dpkg-buildpackage -us -uc -b

echo "[*] Installing to the system..."
write_progress running 75 "Installing the built package"
cd ..
# Ensure tailscale is installed for remote access
if ! command -v tailscale >/dev/null 2>&1; then
    echo "[*] Installing Tailscale for remote VPN access using official script..."
    write_progress running 82 "Installing Tailscale"
    curl -fsSL https://tailscale.com/install.sh | sh
fi

echo "[*] Unpacking and configuring the server files..."
write_progress running 88 "Applying the package to the system"
dpkg -i hashcat-wpa-server_*.deb || apt-get install -f -y

# Explicitly ensure service is up after dpkg finish
echo "[*] Finalizing service state..."
write_progress running 95 "Starting the server"
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
write_progress success 100 "Linux install completed successfully"

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
