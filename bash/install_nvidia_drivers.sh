#!/bin/bash
set -e

ACTION="${1:-check}"

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
            else
                echo "visible:nvidia-gpu driver:missing"
            fi
        else
            echo "visible:no-nvidia-gpu driver:not-applicable"
        fi
        exit 0
        ;;
    check)
        if drivers_ready; then
            echo "NVIDIA drivers already appear to be installed."
            exit 0
        fi

        if ! has_nvidia_gpu; then
            echo "No NVIDIA GPU was detected on this system."
            exit 0
        fi

        if [ -r /etc/os-release ]; then
            # shellcheck disable=SC1091
            . /etc/os-release
        fi

        export DEBIAN_FRONTEND=noninteractive
        apt-get update

        if [ "${ID:-}" = "ubuntu" ] || os_id_like_contains "ubuntu"; then
            apt-get install -y ubuntu-drivers-common pciutils
            ubuntu-drivers autoinstall || apt-get install -y nvidia-driver
        elif [ "${ID:-}" = "debian" ] || [ "${ID:-}" = "kali" ] || os_id_like_contains "debian"; then
            apt-get install -y pciutils "linux-headers-$(uname -r)" || true
            apt-get install -y nvidia-driver firmware-misc-nonfree || apt-get install -y nvidia-driver
        else
            echo "NVIDIA GPU detected, but this helper only supports Debian-family Linux for automatic installation."
            exit 0
        fi

        echo "NVIDIA driver installation completed. A reboot may be required before the GPU becomes available to Hashcat."
        ;;
    *)
        echo "Usage: install_nvidia_drivers.sh [check|status]"
        exit 1
        ;;
esac
