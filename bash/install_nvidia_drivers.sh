#!/bin/bash
set -e

ACTION="${1:-check}"
PROGRESS_FILE="${HASHCAT_WPA_NVIDIA_PROGRESS_FILE:-/var/log/hashcat-wpa-server/nvidia_install.progress}"

write_progress() {
    local state="$1"
    local percent="$2"
    local message="$3"
    mkdir -p "$(dirname "$PROGRESS_FILE")"
    printf '%s|%s|%s\n' "$state" "$percent" "$message" > "$PROGRESS_FILE"
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

drivers_ready() {
    command -v nvidia-smi >/dev/null 2>&1 && lsmod | grep -q '^nvidia'
}

case "$ACTION" in
    status)
        if has_nvidia_gpu; then
            if drivers_ready; then
                echo "visible:nvidia-gpu driver:installed"
                write_progress success 100 "NVIDIA drivers are already installed"
            else
                echo "visible:nvidia-gpu driver:missing"
                write_progress idle 0 "NVIDIA drivers are missing"
            fi
        else
            echo "visible:no-nvidia-gpu driver:not-applicable"
            write_progress not-applicable 100 "No NVIDIA GPU detected"
        fi
        exit 0
        ;;
    check)
        if drivers_ready; then
            echo "NVIDIA drivers already appear to be installed."
            write_progress success 100 "NVIDIA drivers are already installed"
            exit 0
        fi

        if ! has_nvidia_gpu; then
            echo "No NVIDIA GPU was detected on this system."
            write_progress not-applicable 100 "No NVIDIA GPU detected"
            exit 0
        fi

        if [ -r /etc/os-release ]; then
            # shellcheck disable=SC1091
            . /etc/os-release
        fi

        export DEBIAN_FRONTEND=noninteractive
        write_progress running 10 "Updating package lists"
        apt-get update

        if [ "${ID:-}" = "ubuntu" ] || os_id_like_contains "ubuntu"; then
            write_progress running 30 "Installing Ubuntu driver helpers"
            apt-get install -y ubuntu-drivers-common pciutils
            write_progress running 65 "Installing NVIDIA drivers"
            ubuntu-drivers autoinstall || apt-get install -y nvidia-driver
        elif [ "${ID:-}" = "debian" ] || [ "${ID:-}" = "kali" ] || os_id_like_contains "debian"; then
            write_progress running 30 "Installing Debian driver packages"
            apt-get install -y pciutils "linux-headers-$(uname -r)" || true
            write_progress running 65 "Installing NVIDIA drivers"
            apt-get install -y nvidia-driver firmware-misc-nonfree || apt-get install -y nvidia-driver
        else
            echo "NVIDIA GPU detected, but this helper only supports Debian-family Linux for automatic installation."
            write_progress not-applicable 100 "Automatic NVIDIA installation is not supported on this Linux distribution"
            exit 0
        fi

        echo "NVIDIA driver installation completed. A reboot may be required before the GPU becomes available to Hashcat."
        write_progress success 100 "NVIDIA driver installation completed"
        ;;
    *)
        echo "Usage: install_nvidia_drivers.sh [check|status]"
        exit 1
        ;;
esac
