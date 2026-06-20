#!/usr/bin/env bash
set -euo pipefail

APP_NAME="hashcat-pwnagotchi-server"
REPO_URL="${HASHCAT_WPA_REPO_URL:-https://github.com/EpicNori/Hashcat-Pwnagotchi-server.git}"
REPO_ARCHIVE="${HASHCAT_WPA_REPO_ARCHIVE:-https://github.com/EpicNori/Hashcat-Pwnagotchi-server/archive/refs/heads/main.tar.gz}"
BRANCH="${HASHCAT_WPA_BRANCH:-main}"
PORT="${HASHCAT_WPA_PORT:-9111}"
ADMIN_USER="${HASHCAT_ADMIN_USER:-admin}"
USE_GPU="${HASHCAT_WPA_DOCKER_GPU:-0}"

usage() {
    cat <<EOF
Easy Docker installer for Hashcat Pwnagotchi Server.

Usage:
  curl -fsSL https://raw.githubusercontent.com/EpicNori/Hashcat-Pwnagotchi-server/main/docker/easy-install.sh | bash

Optional environment variables:
  HASHCAT_ADMIN_PASSWORD    Required admin password. If omitted, you will be prompted.
  HASHCAT_ADMIN_USER        Admin username. Default: admin
  HASHCAT_WPA_PORT          Host port. Default: 9111
  HASHCAT_WPA_DOCKER_GPU    Set to 1 to include docker-compose.gpu.yml
  HASHCAT_WPA_DOCKER_APP_DIR Install/source directory
EOF
}

info() {
    printf '[*] %s\n' "$*"
}

die() {
    printf '[!] %s\n' "$*" >&2
    exit 1
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    usage
    exit 0
fi

if [ -z "${HASHCAT_WPA_DOCKER_APP_DIR:-}" ]; then
    if [ -d /DATA/AppData ]; then
        APP_ROOT="/DATA/AppData/$APP_NAME"
        APP_DIR="$APP_ROOT/source"
    else
        APP_ROOT="$HOME/$APP_NAME"
        APP_DIR="$APP_ROOT"
    fi
else
    APP_DIR="$HASHCAT_WPA_DOCKER_APP_DIR"
    APP_ROOT="$(dirname "$APP_DIR")"
fi

DATA_DIR="${HASHCAT_WPA_DOCKER_DATA:-$APP_ROOT/data}"
LOGS_DIR="${HASHCAT_WPA_DOCKER_LOGS:-$APP_ROOT/logs}"
TZ_VALUE="${TZ:-$(cat /etc/timezone 2>/dev/null || printf 'UTC')}"

if docker info >/dev/null 2>&1; then
    DOCKER=(docker)
elif command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
    DOCKER=(sudo docker)
else
    die "Docker is not running or this user cannot access it. Start Docker or run this script as a user with Docker access."
fi

if "${DOCKER[@]}" compose version >/dev/null 2>&1; then
    COMPOSE=("${DOCKER[@]}" compose)
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE=(docker-compose)
else
    die "Docker Compose is missing. Install the Docker Compose plugin first."
fi

if [ -z "${HASHCAT_ADMIN_PASSWORD:-}" ]; then
    if [ ! -r /dev/tty ]; then
        die "Set HASHCAT_ADMIN_PASSWORD before running this script."
    fi
    printf 'Choose admin password for the web UI: ' >/dev/tty
    stty -echo </dev/tty
    IFS= read -r HASHCAT_ADMIN_PASSWORD </dev/tty
    stty echo </dev/tty
    printf '\n' >/dev/tty
fi

if [ "${#HASHCAT_ADMIN_PASSWORD}" -lt 8 ]; then
    die "HASHCAT_ADMIN_PASSWORD must be at least 8 characters."
fi

case "$HASHCAT_ADMIN_PASSWORD" in
    *"'"*|*$'\n'*)
        die "Use an admin password without single quotes or newlines for the generated .env file."
        ;;
esac

fetch_source() {
    if [ -d "$APP_DIR/.git" ]; then
        info "Updating existing source checkout at $APP_DIR"
        git -C "$APP_DIR" fetch origin "$BRANCH"
        git -C "$APP_DIR" checkout "$BRANCH"
        git -C "$APP_DIR" pull --ff-only origin "$BRANCH"
        return
    fi

    if [ -d "$APP_DIR" ] && [ -n "$(find "$APP_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
        die "$APP_DIR already exists and is not a git checkout. Move it away or set HASHCAT_WPA_DOCKER_APP_DIR."
    fi

    mkdir -p "$(dirname "$APP_DIR")"

    if command -v git >/dev/null 2>&1; then
        info "Cloning source into $APP_DIR"
        git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
        return
    fi

    if ! command -v curl >/dev/null 2>&1 || ! command -v tar >/dev/null 2>&1; then
        die "Need git, or curl plus tar, to download the source."
    fi

    info "Downloading source archive into $APP_DIR"
    tmp_dir="$(mktemp -d)"
    trap 'rm -rf "$tmp_dir"' EXIT
    curl -fsSL "$REPO_ARCHIVE" -o "$tmp_dir/source.tar.gz"
    mkdir -p "$APP_DIR"
    tar -xzf "$tmp_dir/source.tar.gz" --strip-components=1 -C "$APP_DIR"
}

fetch_source

mkdir -p "$DATA_DIR" "$LOGS_DIR"

cat > "$APP_DIR/.env" <<EOF
HASHCAT_ADMIN_USER='$ADMIN_USER'
HASHCAT_ADMIN_PASSWORD='$HASHCAT_ADMIN_PASSWORD'
HASHCAT_WPA_PORT='$PORT'
HASHCAT_WPA_DOCKER_DATA='$DATA_DIR'
HASHCAT_WPA_DOCKER_LOGS='$LOGS_DIR'
TZ='$TZ_VALUE'
EOF

compose_files=(-f docker/docker-compose.yml)
if [ "$USE_GPU" = "1" ]; then
    compose_files+=(-f docker/docker-compose.gpu.yml)
fi

info "Building and starting the Docker container"
(
    cd "$APP_DIR"
    "${COMPOSE[@]}" "${compose_files[@]}" up -d --build
)

server_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
if [ -z "$server_ip" ]; then
    server_ip="SERVER_IP"
fi

cat <<EOF

[+] Hashcat Pwnagotchi Server is starting.

Open:
  http://$server_ip:$PORT

Login:
  Username: $ADMIN_USER
  Password: the password you just set

Useful commands:
  cd "$APP_DIR"
  ${COMPOSE[*]} -f docker/docker-compose.yml logs -f
  ${COMPOSE[*]} -f docker/docker-compose.yml restart
  ${COMPOSE[*]} -f docker/docker-compose.yml down
EOF
