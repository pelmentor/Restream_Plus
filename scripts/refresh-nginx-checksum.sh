#!/usr/bin/env bash
# scripts/refresh-nginx-checksum.sh — GPG-verify the nginx tarball, then emit
# the SHA-256. MANUAL PROCEDURE — not invoked from CI.
#
# Use this script when bumping NGINX_VERSION in .github/checksums.env to
# verify the upstream signature out-of-band before committing the new SHA.
#
# Per docs/architecture/phase-11-design-memo.md §R-13, the CI gate is the
# committed SHA-256 in .github/checksums.env; GPG verification happens once,
# by a human, at bump time, so the chain of trust starts from a known good
# release signed by the nginx team.
#
# Prerequisites:
#   - gpg installed
#   - nginx signing keys imported. The current keys are:
#       Maxim Dounin       <mdounin@mdounin.ru>     (B0F4253373F8F6F510D42178520A9993A1C052F8)
#       Sergey Kandaurov   <pluknet@nginx.com>      (13C82A63B603576156E30A4EA0EA981B66B0D967)
#       Konstantin Pavlov  <thresh@nginx.com>       (8540DDF6D71A4D5C7F4FCBC9E37BC2DC026CE32C)
#       Roman Arutyunyan   <arut@nginx.com>         (43387825DDB1BB97EC36BA5D007C8D7C15D87369)
#   See https://nginx.org/en/pgp_keys.html for the canonical key list +
#   fingerprints; fetch + verify fingerprints before importing.
#
# Usage:
#   scripts/refresh-nginx-checksum.sh 1.26.2

set -euo pipefail

VERSION="${1:?usage: $0 <nginx-version>}"
TARBALL="nginx-${VERSION}.tar.gz"
BASE_URL="https://nginx.org/download"

tmpdir="$(mktemp -d)"
trap 'rm -rf "${tmpdir}"' EXIT

echo ">>> Fetching ${TARBALL} + .asc signature..."
curl -fsSL "${BASE_URL}/${TARBALL}"     -o "${tmpdir}/${TARBALL}"
curl -fsSL "${BASE_URL}/${TARBALL}.asc" -o "${tmpdir}/${TARBALL}.asc"

echo ">>> Verifying GPG signature..."
if ! gpg --verify "${tmpdir}/${TARBALL}.asc" "${tmpdir}/${TARBALL}"; then
    echo "ERROR: GPG verification failed. Do NOT use this tarball." >&2
    echo "       Either the signing key is not in your keyring, or the" >&2
    echo "       tarball has been tampered with. See script header." >&2
    exit 1
fi

echo ">>> GPG OK. SHA-256:"
sha256sum "${tmpdir}/${TARBALL}" | awk '{print $1}'

echo ""
echo "Commit this SHA to .github/checksums.env NGINX_SHA256 alongside the version bump."
