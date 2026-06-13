#!/bin/bash
set -e
set -o pipefail

GPU_DRIVER_STATUS="not-run"
PROGRESS_FILE="${HASHCAT_WPA_PROGRESS_FILE:-/var/log/hashcat-wpa-server/app_update.progress}"
NVIDIA_PROGRESS_FILE="${HASHCAT_WPA_NVIDIA_PROGRESS_FILE:-/var/log/hashcat-wpa-server/nvidia_install.progress}"

write_progress() {
    local state="$1"
    local percent="$2"
    local message="$3"
    mkdir -p "$(dirname "$PROGRESS_FILE")"
    printf '%s|%s|%s\n' "$state" "$percent" "$message" > "$PROGRESS_FILE"
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

install_optional_package() {
    local package_name="$1"
    local purpose="$2"

    if apt-cache show "$package_name" >/dev/null 2>&1; then
        apt-get install -y "$package_name"
    else
        echo "[!] Optional package '$package_name' is not available from the configured apt repositories."
        echo "[!] Skipping $purpose."
    fi
}

detect_machine_arch() {
    local machine
    machine="$(uname -m 2>/dev/null || true)"
    case "$machine" in
        x86_64|amd64) echo "amd64" ;;
        aarch64|arm64) echo "arm64" ;;
        armv7l|armhf) echo "arm" ;;
        *) echo "$machine" ;;
    esac
}

# Ensure script is being run as root
if [ "$EUID" -ne 0 ]; then
  echo "[!] Please run this installation script as root (sudo bash install.sh)"
  exit 1
fi

echo "[*] Ensuring package manager is in a clean state (Waiting for locks)..."
echo "[*] Detected machine architecture: $(detect_machine_arch) ($(uname -m))"
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
apt-get install -y curl git dpkg-dev debhelper pciutils python3 python3-venv systemd hashcat hcxtools ocl-icd-libopencl1
install_optional_package "pocl-opencl-icd" "CPU OpenCL runtime installation"
install_optional_package "clinfo" "OpenCL diagnostics installation"

echo "[*] Cloning the extremely fast hashcat-wpa-server..."
write_progress running 35 "Downloading the latest application source"
cd /tmp
rm -rf hashcat-wpa-build-env
mkdir hashcat-wpa-build-env
cd hashcat-wpa-build-env

git clone https://github.com/EpicNori/Hashcat-Pwnagotchi-server.git
cd Hashcat-Pwnagotchi-server

echo "[*] Checking NVIDIA/AMD GPU driver readiness..."
write_progress running 45 "Checking NVIDIA/AMD GPU driver readiness"
if HASHCAT_WPA_NVIDIA_PROGRESS_FILE="$NVIDIA_PROGRESS_FILE" HASHCAT_WPA_APP_USER=hashcat bash bash/install_gpu_drivers.sh check; then
    GPU_DRIVER_STATUS="checked"
else
    GPU_DRIVER_STATUS="manual-required"
    echo "[!] GPU driver check did not complete. The server install will continue."
fi

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

if [ "$GPU_DRIVER_STATUS" = "checked" ]; then
    echo "[+] NVIDIA/AMD GPU driver check completed."
    echo "[+] A reboot may still be required before Hashcat can use a newly installed GPU runtime."
elif [ "$GPU_DRIVER_STATUS" = "manual-required" ]; then
    echo "[!] GPU driver setup needs manual attention before GPU cracking will work."
fi

echo "[+] "
echo "[+] Web Interface URL:   http://127.0.0.1:9111"
echo "[+] Network Access:      http://$(hostname -I | awk '{print $1}'):9111"
echo "[+] "
echo "[+] Default Login User:  admin"
echo "[+] Default Password:    changeme"
echo "=========================================================================="
