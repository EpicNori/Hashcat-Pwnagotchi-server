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
