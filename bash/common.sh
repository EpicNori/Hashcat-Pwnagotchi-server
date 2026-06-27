#!/bin/bash

detect_machine_arch() {
    local machine
    machine="$(uname -m 2>/dev/null || true)"
    case "$machine" in
        x86_64|amd64)
            echo "amd64"
            ;;
        aarch64|arm64)
            echo "arm64"
            ;;
        armv7l|armhf)
            echo "arm"
            ;;
        *)
            echo "$machine"
            ;;
    esac
}

is_amd64_arch() {
    [ "$(detect_machine_arch)" = "amd64" ]
}

is_arm_arch() {
    case "$(detect_machine_arch)" in
        arm64|arm) return 0 ;;
        *) return 1 ;;
    esac
}

is_wsl_host() {
    grep -qiE '(microsoft|wsl)' /proc/sys/kernel/osrelease /proc/version 2>/dev/null
}

gpu_lspci_lines() {
    if [ -n "${HASHCAT_WPA_TEST_LSPCI_OUTPUT:-}" ]; then
        printf '%s\n' "$HASHCAT_WPA_TEST_LSPCI_OUTPUT"
        return 0
    fi
    if command -v lspci >/dev/null 2>&1; then
        lspci -nn 2>/dev/null | grep -Ei '(VGA|3D|Display|NVIDIA|AMD|ATI|Radeon)' || true
    fi
}

has_pci_vendor() {
    local expected_vendor="$1"
    local vendor_file
    if [ "${HASHCAT_WPA_IGNORE_HOST_PCI:-0}" = "1" ]; then
        return 1
    fi
    for vendor_file in /sys/bus/pci/devices/*/vendor; do
        [ -r "$vendor_file" ] || continue
        if grep -qi "^${expected_vendor}$" "$vendor_file"; then
            return 0
        fi
    done
    return 1
}

nvidia_smi_cmd() {
    if [ "${HASHCAT_WPA_IGNORE_HOST_GPU_TOOLS:-0}" = "1" ]; then
        return 1
    fi
    if command -v nvidia-smi >/dev/null 2>&1; then
        command -v nvidia-smi
        return 0
    fi
    if [ -x /usr/lib/wsl/lib/nvidia-smi ]; then
        echo /usr/lib/wsl/lib/nvidia-smi
        return 0
    fi
    return 1
}

has_nvidia_gpu() {
    local smi
    if [ "${HASHCAT_WPA_IGNORE_HOST_GPU_TOOLS:-0}" != "1" ] && smi="$(nvidia_smi_cmd 2>/dev/null)" && "$smi" -L >/dev/null 2>&1; then
        return 0
    fi
    if gpu_lspci_lines | grep -Eqi '((VGA|3D|Display).*(NVIDIA|GeForce|Quadro|Tesla))|((NVIDIA|GeForce|Quadro|Tesla).*(VGA|3D|Display))'; then
        return 0
    fi
    has_pci_vendor "0x10de"
}

has_amd_gpu() {
    if [ "${HASHCAT_WPA_IGNORE_HOST_GPU_TOOLS:-0}" != "1" ] && command -v rocm-smi >/dev/null 2>&1 && rocm-smi --showproductname >/dev/null 2>&1; then
        return 0
    fi
    if gpu_lspci_lines | grep -Eiq '(^|[^[:alnum:]])(AMD|ATI|Radeon)([^[:alnum:]]|$)'; then
        return 0
    fi
    has_pci_vendor "0x1002"
}

cloudflared_asset_for_arch() {
    case "$(detect_machine_arch)" in
        amd64) echo "cloudflared-linux-amd64" ;;
        arm64) echo "cloudflared-linux-arm64" ;;
        arm) echo "cloudflared-linux-arm" ;;
        *) return 1 ;;
    esac
}

supports_debian_nvidia_autoinstall() {
    # Debian/Ubuntu NVIDIA package automation is reliable for regular amd64
    # hosts. ARM devices such as Raspberry Pi/Jetson need vendor-specific GPU
    # stacks, so the shared installer must not try amd64 driver packages there.
    is_amd64_arch
}

supports_debian_amd_autoinstall() {
    # AMD's upstream ROCm apt repository is amd64-only, but Debian-family
    # distro packages can still provide OpenCL/ROCm runtimes on ARM hosts.
    case "$(detect_machine_arch)" in
        amd64|arm64|arm) return 0 ;;
        *) return 1 ;;
    esac
}
