#!/usr/bin/env bash
# scripts/load-checksums.sh — emit validated KEY=VALUE lines from
# .github/checksums.env to stdout. Used by GHA workflows in place of
# `cat .github/checksums.env >> $GITHUB_ENV` (safe but unvalidated) and
# `set -a; . .github/checksums.env; set +a` (UNSAFE — shell-sources
# attacker-influenceable content per H3 sec-reviewer).
#
# The script:
#   1. Parses .github/checksums.env line-by-line as plain text (NO shell
#      evaluation — no $() expansion, no backtick expansion).
#   2. Validates every value against a strict per-field regex.
#   3. Emits KEY=VALUE on stdout, one per line, suitable for redirection
#      into $GITHUB_ENV.
#
# Any malformed or missing field is fatal — the script exits non-zero
# with a clear error message and the workflow halts BEFORE any tainted
# value can flow into a downstream command.

set -euo pipefail

readonly CHECKSUMS_FILE="${CHECKSUMS_FILE:-.github/checksums.env}"

if [ ! -f "${CHECKSUMS_FILE}" ]; then
    echo "ERROR: ${CHECKSUMS_FILE} not found (run from repo root)" >&2
    exit 1
fi

readonly RE_SEMVER='^[0-9]+(\.[0-9]+){2,3}$'
readonly RE_GIT_SHA='^[a-f0-9]{40}$'
readonly RE_HASH256='^[a-f0-9]{64}$'

declare -A FOUND=()

# Whitelist of allowed keys. Any other key in the file is rejected to
# defend against an attacker silently adding a payload field whose
# format isn't validated.
allowed_key() {
    case "$1" in
        NGINX_VERSION|NGINX_SHA256| \
        NGINX_RTMP_MODULE_SHA|NGINX_RTMP_MODULE_TARBALL_SHA256| \
        S6_OVERLAY_VERSION|S6_OVERLAY_NOARCH_SHA256| \
        S6_OVERLAY_X86_64_SHA256|S6_OVERLAY_AARCH64_SHA256)
            return 0 ;;
    esac
    return 1
}

validate() {
    local key="$1" value="$2"
    case "${key}" in
        NGINX_VERSION|S6_OVERLAY_VERSION)
            printf '%s' "${value}" | grep -qE "${RE_SEMVER}" \
                || { echo "ERROR: ${key}='${value}' is not a valid semver" >&2; exit 1; } ;;
        NGINX_RTMP_MODULE_SHA)
            printf '%s' "${value}" | grep -qE "${RE_GIT_SHA}" \
                || { echo "ERROR: ${key}='${value}' is not a 40-char hex sha" >&2; exit 1; } ;;
        *_SHA256)
            printf '%s' "${value}" | grep -qE "${RE_HASH256}" \
                || { echo "ERROR: ${key}='${value}' is not a 64-char hex hash" >&2; exit 1; } ;;
    esac
}

while IFS='=' read -r raw_key raw_value || [ -n "${raw_key}" ]; do
    case "${raw_key}" in
        ''|\#*) continue ;;
    esac
    key="${raw_key// /}"
    value="${raw_value%$'\r'}"
    if ! allowed_key "${key}"; then
        echo "ERROR: unknown key '${key}' in ${CHECKSUMS_FILE}" >&2
        exit 1
    fi
    if [ -z "${value}" ]; then
        echo "ERROR: empty value for ${key}" >&2
        exit 1
    fi
    validate "${key}" "${value}"
    FOUND[${key}]=1
    printf '%s=%s\n' "${key}" "${value}"
done < "${CHECKSUMS_FILE}"

required=(
    NGINX_VERSION NGINX_SHA256
    NGINX_RTMP_MODULE_SHA NGINX_RTMP_MODULE_TARBALL_SHA256
    S6_OVERLAY_VERSION
    S6_OVERLAY_NOARCH_SHA256 S6_OVERLAY_X86_64_SHA256 S6_OVERLAY_AARCH64_SHA256
)
for k in "${required[@]}"; do
    if [ -z "${FOUND[$k]:-}" ]; then
        echo "ERROR: required key ${k} missing from ${CHECKSUMS_FILE}" >&2
        exit 1
    fi
done
