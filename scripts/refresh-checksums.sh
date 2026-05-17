#!/usr/bin/env bash
# scripts/refresh-checksums.sh — recompute the five tarball SHA-256s in
# .github/checksums.env after a nginx/nginx-rtmp/s6-overlay version bump.
#
# Invoked by:
#   - Local dev after manually bumping a *_VERSION or *_SHA field
#   - Renovate's postUpgradeTasks hook (.github/renovate.json5) when its
#     customManagers update one of the version fields in this file
#
# SECURITY (H3, H4 sec-reviewer):
#   Version fields enter this script from Renovate's customManagers, which
#   pull tag/release names directly from upstream GitHub APIs. A compromised
#   upstream that publishes a tag with shell-metachars (e.g. `v3.2.0.2;rm
#   -rf /`) could inject commands via either the curl URL interpolation
#   below or via `set -a; . .github/checksums.env; set +a` callers in the
#   workflows. We defend with strict regex validation on every loaded
#   field BEFORE any interpolation, and we use a safe KEY=VALUE line
#   parser instead of shell-sourcing the env file.
#
# This is the only mechanism that writes .github/checksums.env's SHA
# fields. Human edits to those fields are emergency-override only and
# should be flagged in commit messages.

set -euo pipefail

readonly CHECKSUMS_FILE=".github/checksums.env"

# Strict format regexes. Reject anything else — Renovate's bot is a less
# trusted boundary than this script.
readonly RE_SEMVER='^[0-9]+(\.[0-9]+){2,3}$'
readonly RE_GIT_SHA='^[a-f0-9]{40}$'
readonly RE_HASH256='^[a-f0-9]{64}$'

if [ ! -f "${CHECKSUMS_FILE}" ]; then
    echo "ERROR: ${CHECKSUMS_FILE} not found (run from repo root)" >&2
    exit 1
fi

# Safe parser — read KEY=VALUE pairs without shell evaluation.
# Validates each field's format against the regex set above.
parse_checksums_env() {
    NGINX_VERSION=""
    NGINX_RTMP_MODULE_SHA=""
    S6_OVERLAY_VERSION=""

    while IFS='=' read -r raw_key raw_value; do
        case "${raw_key}" in
            ''|\#*) continue ;;
        esac
        key="${raw_key// /}"
        value="${raw_value}"
        # Strip surrounding whitespace + any trailing CR (Windows checkout).
        value="${value%$'\r'}"
        case "${key}" in
            NGINX_VERSION|S6_OVERLAY_VERSION)
                printf '%s' "${value}" | grep -qE "${RE_SEMVER}" \
                    || { echo "ERROR: ${key}='${value}' is not a valid semver" >&2; exit 1; }
                ;;
            NGINX_RTMP_MODULE_SHA)
                printf '%s' "${value}" | grep -qE "${RE_GIT_SHA}" \
                    || { echo "ERROR: ${key}='${value}' is not a 40-char hex sha" >&2; exit 1; }
                ;;
            NGINX_SHA256|NGINX_RTMP_MODULE_TARBALL_SHA256|S6_OVERLAY_NOARCH_SHA256|S6_OVERLAY_X86_64_SHA256|S6_OVERLAY_AARCH64_SHA256)
                # SHA-256 fields are output, not input — we don't need to
                # validate their current values, but we won't interpolate them.
                continue
                ;;
            *)
                echo "ERROR: unknown key '${key}' in ${CHECKSUMS_FILE}" >&2
                exit 1
                ;;
        esac
        case "${key}" in
            NGINX_VERSION)           NGINX_VERSION="${value}" ;;
            NGINX_RTMP_MODULE_SHA)   NGINX_RTMP_MODULE_SHA="${value}" ;;
            S6_OVERLAY_VERSION)      S6_OVERLAY_VERSION="${value}" ;;
        esac
    done < "${CHECKSUMS_FILE}"

    : "${NGINX_VERSION:?NGINX_VERSION missing in ${CHECKSUMS_FILE}}"
    : "${NGINX_RTMP_MODULE_SHA:?NGINX_RTMP_MODULE_SHA missing in ${CHECKSUMS_FILE}}"
    : "${S6_OVERLAY_VERSION:?S6_OVERLAY_VERSION missing in ${CHECKSUMS_FILE}}"
}

parse_checksums_env

tmpdir="$(mktemp -d)"
trap 'rm -rf "${tmpdir}"' EXIT

compute_sha256() {
    # $1 = URL (already-validated components)
    curl -fsSL "$1" -o "${tmpdir}/blob"
    sha256sum "${tmpdir}/blob" | cut -d' ' -f1
}

fetch_sha256_sibling() {
    # $1 = URL of the .sha256 sibling file (s6-overlay style: "<hash>  <filename>")
    curl -fsSL "$1" | awk '{print $1}'
}

# Validate every computed SHA before writing — defense in depth against
# transient curl/sha256sum oddities.
validate_sha() {
    printf '%s' "$1" | grep -qE "${RE_HASH256}" \
        || { echo "ERROR: malformed SHA-256: '$1'" >&2; exit 1; }
}

write_field() {
    # $1 = field name (whitelisted), $2 = SHA-256 value (already validated)
    awk -v k="$1" -v v="$2" 'BEGIN{FS=OFS="="} $1==k {$2=v} 1' "${CHECKSUMS_FILE}" > "${CHECKSUMS_FILE}.new"
    mv "${CHECKSUMS_FILE}.new" "${CHECKSUMS_FILE}"
}

echo "Computing NGINX_SHA256 (nginx ${NGINX_VERSION})..."
nginx_sha=$(compute_sha256 \
    "https://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz")
validate_sha "${nginx_sha}"

echo "Computing NGINX_RTMP_MODULE_TARBALL_SHA256 (commit ${NGINX_RTMP_MODULE_SHA:0:7})..."
rtmp_sha=$(compute_sha256 \
    "https://github.com/arut/nginx-rtmp-module/archive/${NGINX_RTMP_MODULE_SHA}.tar.gz")
validate_sha "${rtmp_sha}"

echo "Fetching S6_OVERLAY_NOARCH_SHA256 (s6-overlay v${S6_OVERLAY_VERSION})..."
s6_noarch=$(fetch_sha256_sibling \
    "https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-noarch.tar.xz.sha256")
validate_sha "${s6_noarch}"

echo "Fetching S6_OVERLAY_X86_64_SHA256..."
s6_x86_64=$(fetch_sha256_sibling \
    "https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-x86_64.tar.xz.sha256")
validate_sha "${s6_x86_64}"

echo "Fetching S6_OVERLAY_AARCH64_SHA256..."
s6_aarch64=$(fetch_sha256_sibling \
    "https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-aarch64.tar.xz.sha256")
validate_sha "${s6_aarch64}"

write_field NGINX_SHA256 "${nginx_sha}"
write_field NGINX_RTMP_MODULE_TARBALL_SHA256 "${rtmp_sha}"
write_field S6_OVERLAY_NOARCH_SHA256 "${s6_noarch}"
write_field S6_OVERLAY_X86_64_SHA256 "${s6_x86_64}"
write_field S6_OVERLAY_AARCH64_SHA256 "${s6_aarch64}"

# Re-parse to confirm the rewrite still validates (defense in depth — if
# this script ever introduces a bug that smuggles bad content, we
# catch it before commit).
parse_checksums_env
echo "OK: refreshed ${CHECKSUMS_FILE}"
