#!/bin/bash
set -e
set -o pipefail

GPU_DRIVER_STATUS="not-run"
TAILSCALE_STATUS="not-run"
PROGRESS_FILE="${HASHCAT_WPA_PROGRESS_FILE:-/var/log/hashcat-wpa-server/app_update.progress}"
NVIDIA_PROGRESS_FILE="${HASHCAT_WPA_NVIDIA_PROGRESS_FILE:-/var/log/hashcat-wpa-server/nvidia_install.progress}"

write_progress() {
    local state="$1"
    local percent="$2"
    local message="$3"
    mkdir -p "$(dirname "$PROGRESS_FILE")"
    printf '%s|%s|%s\n' "$state" "$percent" "$message" > "$PROGRESS_FILE"
}

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    UI_RESET=$'\033[0m'
    UI_BOLD=$'\033[1m'
    UI_DIM=$'\033[2m'
    UI_CYAN=$'\033[36m'
    UI_GREEN=$'\033[32m'
    UI_AMBER=$'\033[33m'
    UI_RED=$'\033[31m'
else
    UI_RESET=""
    UI_BOLD=""
    UI_DIM=""
    UI_CYAN=""
    UI_GREEN=""
    UI_AMBER=""
    UI_RED=""
fi

ui_info() { printf '%b[*]%b %s\n' "$UI_CYAN" "$UI_RESET" "$*"; }
ui_success() { printf '%b[+]%b %s\n' "$UI_GREEN" "$UI_RESET" "$*"; }
ui_warn() { printf '%b[!]%b %s\n' "$UI_AMBER" "$UI_RESET" "$*"; }
ui_error() { printf '%b[!]%b %s\n' "$UI_RED" "$UI_RESET" "$*"; }
ui_step() {
    local step="$1"
    shift
    printf '%b[%s]%b %s\n' "$UI_CYAN" "$step" "$UI_RESET" "$*"
}
ui_kv() {
    local key="$1"
    local value="$2"
    printf '  %b%-18s%b %s\n' "$UI_DIM" "${key}:" "$UI_RESET" "$value"
}
ui_command() {
    printf '  %b%s%b\n' "$UI_DIM" "$*" "$UI_RESET"
}
ui_heading() {
    printf '\n%b%s%b\n' "$UI_BOLD$UI_CYAN" "$*" "$UI_RESET"
    printf '%b%s%b\n' "$UI_DIM" "===========================================================================" "$UI_RESET"
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

    ui_error "${service_name} did not become active after installation."
    systemctl --no-pager --full status "$service_name" || true
    exit 1
}

install_optional_package() {
    local package_name="$1"
    local purpose="$2"

    if apt-cache show "$package_name" >/dev/null 2>&1; then
        apt-get install -y "$package_name"
    else
        ui_warn "Optional package '$package_name' is not available from the configured apt repositories."
        ui_warn "Skipping $purpose."
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

install_tailscale_for_remote_access() {
    if [ "${HASHCAT_WPA_SKIP_TAILSCALE_INSTALL:-0}" = "1" ]; then
        TAILSCALE_STATUS="skipped"
        ui_info "Skipping optional Tailscale install because HASHCAT_WPA_SKIP_TAILSCALE_INSTALL=1."
        return 0
    fi

    if command -v tailscale >/dev/null 2>&1; then
        TAILSCALE_STATUS="already-installed"
        ui_success "Tailscale is already installed."
        return 0
    fi

    ui_info "Installing Tailscale for optional remote VPN access..."
    write_progress running 82 "Installing optional Tailscale connector"
    if curl -fsSL https://tailscale.com/install.sh | sh; then
        TAILSCALE_STATUS="installed"
        ui_success "Tailscale installed. Run 'crackserver tailscale' when you want to connect it."
        return 0
    fi

    TAILSCALE_STATUS="manual-required"
    ui_warn "Optional Tailscale install failed. The server install will continue."
    ui_warn "You can retry later with: crackserver tailscale"
    return 0
}

if [ "${HASHCAT_WPA_INSTALL_SOURCE_ONLY:-0}" = "1" ]; then
    return 0 2>/dev/null || exit 0
fi

# Ensure script is being run as root
if [ "$EUID" -ne 0 ]; then
  ui_error "Please run this installation script as root (sudo bash install.sh)"
  exit 1
fi

ui_heading "Hashcat WPA Server Install"
ui_kv "Architecture" "$(detect_machine_arch) ($(uname -m))"
ui_kv "Progress file" "$PROGRESS_FILE"
ui_step "01/09" "Preparing package manager state"
write_progress running 5 "Preparing the Linux package manager"
while fuser /var/lib/dpkg/lock >/dev/null 2>&1 ; do
    ui_info "Waiting for other software managers to finish..."
    sleep 5
done

export DEBIAN_FRONTEND=noninteractive
dpkg --configure -a || true
apt-get install -f -y || true

ui_step "02/09" "Installing application dependencies"
write_progress running 15 "Installing application dependencies"
apt-get update
apt-get install -y curl git dpkg-dev debhelper pciutils python3 python3-venv systemd hashcat hcxtools ocl-icd-libopencl1
install_optional_package "pocl-opencl-icd" "CPU OpenCL runtime installation"
install_optional_package "clinfo" "OpenCL diagnostics installation"

ui_step "03/09" "Downloading application source"
write_progress running 35 "Downloading the latest application source"
cd /tmp
rm -rf hashcat-wpa-build-env
mkdir hashcat-wpa-build-env
cd hashcat-wpa-build-env

git clone https://github.com/EpicNori/Hashcat-Pwnagotchi-server.git
cd Hashcat-Pwnagotchi-server

ui_step "04/09" "Checking NVIDIA/AMD GPU driver readiness"
write_progress running 45 "Checking NVIDIA/AMD GPU driver readiness"
if HASHCAT_WPA_NVIDIA_PROGRESS_FILE="$NVIDIA_PROGRESS_FILE" HASHCAT_WPA_APP_USER=hashcat bash bash/install_gpu_drivers.sh check; then
    GPU_DRIVER_STATUS="checked"
else
    GPU_DRIVER_STATUS="manual-required"
    ui_warn "GPU driver check did not complete. The server install will continue."
fi

ui_step "05/09" "Building Debian package"
write_progress running 55 "Building the application package"
chmod +x debian/rules
dpkg-buildpackage -us -uc -b

ui_step "06/09" "Installing package"
write_progress running 75 "Installing the built package"
cd ..
install_tailscale_for_remote_access

ui_step "07/09" "Applying package configuration"
write_progress running 88 "Applying the package to the system"
dpkg -i hashcat-wpa-server_*.deb || apt-get install -f -y

# Explicitly ensure service is up after dpkg finish
ui_step "08/09" "Starting service"
write_progress running 95 "Starting the server"
ensure_service_running "hashcat-wpa-server.service"

ui_step "09/09" "Cleaning up"
cd /tmp
rm -rf hashcat-wpa-build-env

# Attempt to open the firewall port safely if UFW is installed
if command -v ufw >/dev/null 2>&1; then
    ui_info "Opening port 9111 on local UFW firewall..."
    ufw allow 9111/tcp >/dev/null 2>&1 || true
fi

echo ""
echo "=========================================================================="
if ! pidof systemd >/dev/null; then
    ui_warn "WARNING: Systemd is not running (Are you on WSL or Docker?)."
    echo "    The automatic background service could not be started."
    echo ""
    echo "    To start the server MANUALLY, run:"
    ui_command "sudo -u hashcat /opt/hashcat-wpa-server/venv/bin/gunicorn --chdir /opt/hashcat-wpa-server app:app --bind 0.0.0.0:9111"
else
    ui_success "SUCCESS! hashcat-wpa-server has been installed and is now fully running!"
    ui_success "No further configuration is needed. It automatically runs in the background."
fi
write_progress success 100 "Linux install completed successfully"

if [ "$GPU_DRIVER_STATUS" = "checked" ]; then
    ui_success "NVIDIA/AMD GPU driver check completed."
    ui_success "A reboot may still be required before Hashcat can use a newly installed GPU runtime."
elif [ "$GPU_DRIVER_STATUS" = "manual-required" ]; then
    ui_warn "GPU driver setup needs manual attention before GPU cracking will work."
fi

if [ "$TAILSCALE_STATUS" = "manual-required" ]; then
    ui_warn "Optional Tailscale setup was skipped after an install error."
    ui_warn "Retry it later from the web Settings page or with: crackserver tailscale"
elif [ "$TAILSCALE_STATUS" = "skipped" ]; then
    ui_info "Optional Tailscale setup was skipped by configuration."
fi

echo ""
ui_heading "Access"
ui_kv "Web Interface URL" "http://127.0.0.1:9111"
ui_kv "Network Access" "http://$(hostname -I | awk '{print $1}'):9111"
ui_kv "Default Login User" "admin"
ui_kv "Default Password" "changeme"
echo "=========================================================================="
