#!/bin/bash
set -euo pipefail

ACTION="${1:-check}"
PROGRESS_FILE="${HASHCAT_WPA_GPU_PROGRESS_FILE:-${HASHCAT_WPA_NVIDIA_PROGRESS_FILE:-/var/log/hashcat-wpa-server/nvidia_install.progress}}"
APP_USER="${HASHCAT_WPA_APP_USER:-hashcat}"
ROCM_VERSION="${HASHCAT_WPA_ROCM_VERSION:-7.2.4}"
DRY_RUN="${HASHCAT_WPA_DRY_RUN:-0}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$SCRIPT_DIR/common.sh"

write_progress() {
    local state="$1"
    local percent="$2"
    local message="$3"
    mkdir -p "$(dirname "$PROGRESS_FILE")"
    printf '%s|%s|%s\n' "$state" "$percent" "$message" > "$PROGRESS_FILE"
}

load_os_release() {
    if [ -n "${HASHCAT_WPA_TEST_OS_RELEASE:-}" ] && [ -r "$HASHCAT_WPA_TEST_OS_RELEASE" ]; then
        # shellcheck disable=SC1090
        . "$HASHCAT_WPA_TEST_OS_RELEASE"
    elif [ -r /etc/os-release ]; then
        # shellcheck disable=SC1091
        . /etc/os-release
    fi
}

os_id_like_contains() {
    local needle="$1"
    [[ " ${ID_LIKE:-} " == *" ${needle} "* ]]
}

run_cmd() {
    if [ "$DRY_RUN" = "1" ]; then
        printf '+'
        printf ' %q' "$@"
        printf '\n'
    else
        "$@"
    fi
}

apt_has_package() {
    local package_name="$1"
    if [ -n "${HASHCAT_WPA_FAKE_APT_PACKAGES:-}" ]; then
        [[ " ${HASHCAT_WPA_FAKE_APT_PACKAGES} " == *" ${package_name} "* ]]
        return
    fi
    apt-cache show "$package_name" >/dev/null 2>&1
}

install_existing_packages() {
    local package_name
    local packages=()
    for package_name in "$@"; do
        if apt_has_package "$package_name"; then
            packages+=("$package_name")
        fi
    done
    if [ "${#packages[@]}" -eq 0 ]; then
        return 1
    fi
    run_cmd apt-get install -y "${packages[@]}"
}

nvidia_runtime_ready() {
    local smi
    smi="$(nvidia_smi_cmd 2>/dev/null || true)"
    [ -n "$smi" ] && "$smi" -L >/dev/null 2>&1
}

amd_runtime_ready() {
    if command -v rocminfo >/dev/null 2>&1 && rocminfo >/dev/null 2>&1; then
        return 0
    fi
    if command -v clinfo >/dev/null 2>&1 && clinfo 2>/dev/null | grep -Eiq 'AMD|Radeon|ROCm'; then
        return 0
    fi
    return 1
}

add_app_user_to_gpu_groups() {
    local group_name
    if ! id "$APP_USER" >/dev/null 2>&1; then
        return 0
    fi
    for group_name in render video; do
        if getent group "$group_name" >/dev/null 2>&1; then
            run_cmd usermod -aG "$group_name" "$APP_USER"
        fi
    done
}

register_rocm_repo_if_supported() {
    load_os_release
    local rocm_codename=""

    case "${ID:-}:${VERSION_CODENAME:-}:${VERSION_ID:-}" in
        ubuntu:noble:*|ubuntu::24.04) rocm_codename="noble" ;;
        ubuntu:jammy:*|ubuntu::22.04) rocm_codename="jammy" ;;
        debian:trixie:*|debian::13) rocm_codename="noble" ;;
        debian:bookworm:*|debian::12|kali:*:*) rocm_codename="jammy" ;;
        *)
            echo "[!] AMD ROCm automatic repository setup is not supported for ${PRETTY_NAME:-this Linux distribution}."
            return 1
            ;;
    esac

    echo "[*] Registering AMD ROCm ${ROCM_VERSION} repositories for ${rocm_codename}."
    if [ "$DRY_RUN" = "1" ]; then
        echo "+ install -d -m 0755 /etc/apt/keyrings"
        echo "+ curl -fsSL https://repo.radeon.com/rocm/rocm.gpg.key | gpg --dearmor > /etc/apt/keyrings/rocm.gpg"
        echo "+ write /etc/apt/sources.list.d/rocm.list for ${rocm_codename}"
        echo "+ write /etc/apt/preferences.d/rocm-pin-600"
        return 0
    fi

    install -d -m 0755 /etc/apt/keyrings
    curl -fsSL https://repo.radeon.com/rocm/rocm.gpg.key | gpg --dearmor > /etc/apt/keyrings/rocm.gpg
    cat > /etc/apt/sources.list.d/rocm.list <<EOF
deb [arch=amd64 signed-by=/etc/apt/keyrings/rocm.gpg] https://repo.radeon.com/rocm/apt/${ROCM_VERSION} ${rocm_codename} main
deb [arch=amd64 signed-by=/etc/apt/keyrings/rocm.gpg] https://repo.radeon.com/graphics/${ROCM_VERSION}/ubuntu ${rocm_codename} main
EOF
    cat > /etc/apt/preferences.d/rocm-pin-600 <<'EOF'
Package: *
Pin: release o=repo.radeon.com
Pin-Priority: 600
EOF
}

install_nvidia_stack() {
    if nvidia_runtime_ready; then
        echo "[+] NVIDIA runtime is already available."
        write_progress success 100 "NVIDIA GPU runtime is already available"
        return 0
    fi

    if is_wsl_host; then
        echo "[!] WSL detected with NVIDIA hardware, but Linux kernel drivers must not be installed inside WSL."
        echo "[!] Install or update the CUDA-capable NVIDIA Windows driver, then run: wsl --shutdown"
        write_progress not-applicable 100 "Install the NVIDIA WSL driver on the Windows host"
        return 0
    fi

    if ! supports_debian_nvidia_autoinstall; then
        echo "[!] NVIDIA GPU detected, but automatic Linux driver installation is only supported on amd64 Debian/Ubuntu hosts."
        write_progress not-applicable 100 "NVIDIA auto-install is not supported on this architecture"
        return 0
    fi

    load_os_release
    echo "[*] NVIDIA GPU detected. Installing Linux NVIDIA driver stack."
    write_progress running 10 "Installing NVIDIA driver prerequisites"
    run_cmd apt-get update

    if [ "${ID:-}" = "ubuntu" ] || os_id_like_contains "ubuntu"; then
        run_cmd apt-get install -y pciutils ubuntu-drivers-common
        write_progress running 55 "Installing recommended Ubuntu NVIDIA compute driver"
        run_cmd ubuntu-drivers install --gpgpu || run_cmd ubuntu-drivers autoinstall || install_existing_packages \
            nvidia-driver nvidia-driver-575 nvidia-driver-570 nvidia-driver-565 nvidia-driver-560 \
            nvidia-driver-550 nvidia-driver-535 nvidia-driver-525 nvidia-driver-470
    elif [ "${ID:-}" = "debian" ] || [ "${ID:-}" = "kali" ] || os_id_like_contains "debian"; then
        run_cmd apt-get install -y pciutils "linux-headers-$(uname -r)" || true
        write_progress running 55 "Installing Debian NVIDIA driver packages"
        install_existing_packages nvidia-driver firmware-misc-nonfree || install_existing_packages nvidia-driver
    else
        echo "[!] NVIDIA GPU detected, but automatic installation only supports Debian-family Linux right now."
        write_progress not-applicable 100 "Automatic NVIDIA installation is not supported on this distribution"
        return 0
    fi

    echo "[+] NVIDIA driver installation completed. Reboot may be required before Hashcat sees the GPU."
    write_progress success 100 "NVIDIA driver installation completed"
}

install_amd_stack() {
    if amd_runtime_ready; then
        echo "[+] AMD ROCm/OpenCL runtime is already available."
        write_progress success 100 "AMD GPU runtime is already available"
        add_app_user_to_gpu_groups
        return 0
    fi

    if is_wsl_host; then
        echo "[!] WSL detected with AMD hardware. Automatic Linux ROCm driver installation is only supported on normal Linux hosts."
        write_progress not-applicable 100 "AMD WSL GPU setup requires host-specific ROCm support"
        return 0
    fi

    if ! supports_debian_amd_autoinstall; then
        echo "[!] AMD GPU detected, but automatic ROCm/OpenCL installation is only supported on amd64 Debian/Ubuntu hosts."
        write_progress not-applicable 100 "AMD ROCm auto-install is not supported on this architecture"
        return 0
    fi

    load_os_release
    echo "[*] AMD GPU detected. Installing ROCm/OpenCL runtime for Hashcat."
    write_progress running 10 "Installing AMD GPU prerequisites"
    run_cmd apt-get update
    install_existing_packages pciutils curl ca-certificates gnupg ocl-icd-libopencl1 clinfo || true

    write_progress running 45 "Installing AMD ROCm/OpenCL runtime"
    if ! install_existing_packages rocm-opencl-runtime rocminfo rocm-smi; then
        if register_rocm_repo_if_supported; then
            run_cmd apt-get update
            install_existing_packages rocm-opencl-runtime rocminfo rocm-smi || install_existing_packages rocm-opencl-runtime
        else
            install_existing_packages rocm-opencl-icd rocminfo rocm-smi || install_existing_packages mesa-opencl-icd
        fi
    fi

    add_app_user_to_gpu_groups
    echo "[+] AMD ROCm/OpenCL runtime installation completed. Reboot may be required before Hashcat sees the GPU."
    write_progress success 100 "AMD ROCm/OpenCL runtime installation completed"
}

show_status() {
    local detected=0
    if has_nvidia_gpu; then
        detected=1
        if nvidia_runtime_ready; then
            echo "visible:nvidia-gpu driver:installed"
            write_progress success 100 "NVIDIA GPU runtime is available"
        else
            echo "visible:nvidia-gpu driver:missing"
            write_progress idle 0 "NVIDIA GPU detected, driver/runtime missing"
        fi
    fi
    if has_amd_gpu; then
        detected=1
        if amd_runtime_ready; then
            echo "visible:amd-gpu driver:installed"
            write_progress success 100 "AMD GPU runtime is available"
        else
            echo "visible:amd-gpu driver:missing"
            write_progress idle 0 "AMD GPU detected, ROCm/OpenCL runtime missing"
        fi
    fi
    if [ "$detected" -eq 0 ]; then
        echo "visible:no-gpu driver:not-applicable"
        write_progress not-applicable 100 "No NVIDIA or AMD GPU detected"
    fi
}

case "$ACTION" in
    status)
        show_status
        ;;
    check)
        detected=0
        if has_nvidia_gpu; then
            detected=1
            install_nvidia_stack
        fi
        if has_amd_gpu; then
            detected=1
            install_amd_stack
        fi
        if [ "$detected" -eq 0 ]; then
            echo "No NVIDIA or AMD GPU was detected on this system."
            write_progress not-applicable 100 "No NVIDIA or AMD GPU detected"
        fi
        ;;
    *)
        echo "Usage: install_gpu_drivers.sh [check|status]"
        exit 1
        ;;
esac
