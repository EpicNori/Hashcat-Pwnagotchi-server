#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${HASHCAT_WPA_APP_ROOT:-${APP_ROOT:-/opt/hashcat-wpa-server}}"
DATA_DIR="${HASHCAT_WPA_SERVER_HOME:-${HASHCAT_WPA_DATA_DIR:-/data}}"
LOG_DIR="${HASHCAT_WPA_LOG_DIR:-/var/log/hashcat-wpa-server}"

export HASHCAT_WPA_APP_ROOT="$APP_ROOT"
export HASHCAT_WPA_INSTALL_ROOT="${HASHCAT_WPA_INSTALL_ROOT:-$APP_ROOT}"
export HASHCAT_WPA_SERVER_HOME="$DATA_DIR"
export HASHCAT_WPA_DATA_DIR="$DATA_DIR"
export HASHCAT_WPA_LOG_DIR="$LOG_DIR"
export HASHCAT_WPA_PROGRESS_FILE="${HASHCAT_WPA_PROGRESS_FILE:-$LOG_DIR/app_update.progress}"
export HASHCAT_WPA_NVIDIA_PROGRESS_FILE="${HASHCAT_WPA_NVIDIA_PROGRESS_FILE:-$LOG_DIR/nvidia_install.progress}"
export HASHCAT_WPA_GPU_PROGRESS_FILE="${HASHCAT_WPA_GPU_PROGRESS_FILE:-$LOG_DIR/nvidia_install.progress}"
export HOME="${HOME:-$DATA_DIR}"
export FLASK_APP="${FLASK_APP:-app:app}"
export LOG_CONSOLE="${LOG_CONSOLE:-1}"
export POTFILE_DISABLE="${POTFILE_DISABLE:-0}"

case ":${PYTHONPATH:-}:" in
    *":$APP_ROOT:"*) ;;
    *) export PYTHONPATH="$APP_ROOT${PYTHONPATH:+:$PYTHONPATH}" ;;
esac

mkdir -p \
    "$DATA_DIR" \
    "$DATA_DIR/brain" \
    "$DATA_DIR/captures" \
    "$DATA_DIR/database" \
    "$DATA_DIR/recovery" \
    "$DATA_DIR/wordlists" \
    "$LOG_DIR/supervisor" \
    "$APP_ROOT/logs/supervisor" \
    "$HOME/.hashcat"

if [ ! -e "$HOME/.hashcat/wpa-server" ]; then
    ln -s "$DATA_DIR" "$HOME/.hashcat/wpa-server" 2>/dev/null || true
fi

if [ ! -s "$DATA_DIR/benchmark.csv" ]; then
    printf 'container,0\n' > "$DATA_DIR/benchmark.csv"
fi

chmod 700 "$DATA_DIR" "$DATA_DIR/brain" 2>/dev/null || true

cd "$APP_ROOT"

if [ "${HASHCAT_WPA_RUN_MIGRATIONS:-1}" != "0" ]; then
    migrations_dir="$DATA_DIR/database/migrations"

    if [ ! -d "$migrations_dir" ]; then
        if ! HASHCAT_WPA_SKIP_STARTUP_MAINTENANCE=1 flask db init --directory="$migrations_dir"; then
            echo "[entrypoint] Database migration init was skipped."
        fi
    fi

    if ! HASHCAT_WPA_SKIP_STARTUP_MAINTENANCE=1 flask db migrate --directory="$migrations_dir" -m "container startup"; then
        echo "[entrypoint] No new database migration was generated."
    fi

    if ! HASHCAT_WPA_SKIP_STARTUP_MAINTENANCE=1 flask db upgrade --directory="$migrations_dir"; then
        echo "[entrypoint] Database upgrade was skipped; app startup compatibility checks will still run."
    fi
fi

exec "$@"
