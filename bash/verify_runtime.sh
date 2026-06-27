#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$SCRIPT_DIR/common.sh"

APP_ROOT="${HASHCAT_WPA_APP_ROOT:-/opt/hashcat-wpa-server}"
VENV_PYTHON="$APP_ROOT/venv/bin/python"

echo "Hashcat WPA Server runtime check"
echo "architecture=$(detect_machine_arch) raw=$(uname -m)"

if command -v hashcat >/dev/null 2>&1; then
    echo "hashcat=$(command -v hashcat) version=$(hashcat --version 2>&1 || true)"
else
    echo "hashcat=missing"
fi

if [ -x "$VENV_PYTHON" ]; then
    echo "python=$VENV_PYTHON"
    cd "$APP_ROOT"
    "$VENV_PYTHON" - <<'PY'
from app.utils import settings

print(f"app_arm_safe_mode={settings.is_arm_host()}")
try:
    settings.enabled_hashcat_device_ids = lambda _settings=None: ["1"]
    print("sample_hashcat_args=" + " ".join(settings.apply_hashcat_limits([])))
except Exception as exc:
    print(f"sample_hashcat_args_error={exc}")
PY
else
    echo "python=missing:$VENV_PYTHON"
fi

if command -v hashcat >/dev/null 2>&1; then
    echo "--- hashcat backend info ---"
    hashcat -I 2>&1 || true
fi

if is_arm_arch; then
    echo "result=ARM supported via CPU-safe Hashcat mode by default. AMD OpenCL setup can be attempted when an AMD GPU and distro packages are available."
elif is_amd64_arch; then
    echo "result=amd64 supported via normal Hashcat flow; GPU acceleration depends on installed drivers."
else
    echo "result=unknown architecture; installer may work, but Hashcat compatibility is not guaranteed."
fi
