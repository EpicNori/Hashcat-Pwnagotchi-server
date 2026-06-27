#!/bin/bash

cli_theme_init() {
    if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
        CLI_RESET=$'\033[0m'
        CLI_BOLD=$'\033[1m'
        CLI_DIM=$'\033[2m'
        CLI_CYAN=$'\033[36m'
        CLI_GREEN=$'\033[32m'
        CLI_AMBER=$'\033[33m'
        CLI_RED=$'\033[31m'
    else
        CLI_RESET=""
        CLI_BOLD=""
        CLI_DIM=""
        CLI_CYAN=""
        CLI_GREEN=""
        CLI_AMBER=""
        CLI_RED=""
    fi
}

cli_heading() {
    printf '\n%b%s%b\n' "$CLI_BOLD$CLI_CYAN" "$*" "$CLI_RESET"
    printf '%b%s%b\n' "$CLI_DIM" "===========================================================================" "$CLI_RESET"
}

cli_section() {
    printf '\n%b%s%b\n' "$CLI_BOLD" "$*" "$CLI_RESET"
}

cli_info() {
    printf '%b[*]%b %s\n' "$CLI_CYAN" "$CLI_RESET" "$*"
}

cli_success() {
    printf '%b[+]%b %s\n' "$CLI_GREEN" "$CLI_RESET" "$*"
}

cli_warn() {
    printf '%b[!]%b %s\n' "$CLI_AMBER" "$CLI_RESET" "$*"
}

cli_error() {
    printf '%b[!]%b %s\n' "$CLI_RED" "$CLI_RESET" "$*"
}

cli_step() {
    local step="$1"
    shift
    printf '%b[%s]%b %s\n' "$CLI_CYAN" "$step" "$CLI_RESET" "$*"
}

cli_kv() {
    local key="$1"
    local value="$2"
    printf '  %b%-14s%b %s\n' "$CLI_DIM" "${key}:" "$CLI_RESET" "$value"
}

cli_command() {
    printf '  %b%s%b\n' "$CLI_DIM" "$*" "$CLI_RESET"
}

cli_theme_init
