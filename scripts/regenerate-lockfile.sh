#!/usr/bin/env bash
# scripts/regenerate-lockfile.sh — regenerate hash-pinned pip lockfiles.
#
# Invoked by:
#   - Local dev after editing pyproject.toml [project.dependencies]
#     or [project.optional-dependencies] dev
#   - Renovate's postUpgradeTasks hook (.github/renovate.json5) after a
#     Python dep bump
#
# Output:
#   - docker/requirements.hashes.lock     — runtime deps, universal-platform hashes
#   - docker/requirements-dev.hashes.lock — runtime + dev deps, universal-platform hashes
#
# uv version pinning: keep `UV_VERSION` here in sync with the
# `astral-sh/setup-uv` step in .github/workflows/ci.yml. Both must use
# the same uv version, otherwise `backend-lockfile` drift checks flag
# benign tool-version differences as drift.

set -euo pipefail

readonly UV_VERSION="0.11.14"
readonly PYTHON_VERSION="3.12"

if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv not installed. Install via 'pip install uv==${UV_VERSION}'" >&2
    exit 1
fi

# Enforce uv version match — different uv versions can produce subtly
# different lockfile output (sort orders, hash sets), causing the
# `backend-lockfile` CI job to flag benign tool-version differences as
# real drift. Match the pin to the `astral-sh/setup-uv` step in
# .github/workflows/ci.yml (M3 code-reviewer).
actual_uv=$(uv --version 2>/dev/null | awk '{print $2}' || echo unknown)
if [ "${actual_uv}" != "${UV_VERSION}" ]; then
    echo "ERROR: uv ${UV_VERSION} required for reproducible lockfile generation, got ${actual_uv}." >&2
    echo "       Run 'pip install --upgrade uv==${UV_VERSION}' then re-run." >&2
    exit 1
fi

# Delete the existing output files BEFORE re-running uv. If the output
# files exist, `uv pip compile` treats existing pins as preferred and
# refuses to upgrade them even if a newer version is on PyPI — silently
# producing a lockfile that doesn't match what CI generates from scratch.
# `--refresh` additionally bypasses uv's metadata cache (which has been
# observed to lag PyPI on Windows even after `uv cache clean`).
rm -f docker/requirements.hashes.lock docker/requirements-dev.hashes.lock

uv pip compile pyproject.toml \
    --generate-hashes \
    --python-version "${PYTHON_VERSION}" \
    --universal \
    --refresh \
    --output-file docker/requirements.hashes.lock

uv pip compile pyproject.toml \
    --extra dev \
    --generate-hashes \
    --python-version "${PYTHON_VERSION}" \
    --universal \
    --refresh \
    --output-file docker/requirements-dev.hashes.lock

echo "OK: regenerated docker/requirements{,-dev}.hashes.lock"
