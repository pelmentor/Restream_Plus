# syntax=docker/dockerfile:1.7
#
# Restream_Plus — single-image build
#
# Four stages (Phase 10 design memo §A):
#   1. frontend-builder  (node:22-alpine)      → /web/dist
#   2. nginx-builder     (debian:12-slim)      → /usr/sbin/nginx + /etc/nginx/mime.types
#   3. backend-builder   (python:3.12-slim)    → /opt/venv
#   4. runtime           (debian:12-slim)      ← COPYs from all three
#
# Multi-arch ready (§L): builders pin --platform=$BUILDPLATFORM; runtime
# inherits $TARGETPLATFORM. Phase 10 ships amd64 only; Phase 11's CI
# extends to arm64 via the same Dockerfile.
#
# PINNING (§A, I-A1 aspirational): production images SHOULD pin every
# FROM by @sha256:<digest>. Phase 10 ships with tag pins; Phase 11 CI
# tightens via a Renovate/Dependabot bump flow (DEFER-OR-AMEND-4 of
# the memo).

ARG BUILDPLATFORM
ARG TARGETPLATFORM
ARG TARGETARCH
ARG TARGETOS

# Pin versions / SHAs in one place so release PRs touch a single block.
# CI populates every value via --build-arg from .github/checksums.env
# (PA-13, PA-14). Phase 10 defaults are retained here for local-dev
# trust-on-first-build; CI rejects empty SHA values pre-build.
#
# R-14: NGINX_RTMP_MODULE_SHA bumped from the Phase 10 value
# (9879e1de32b2624e44ec8d99a9e286d59c41bd54 — does NOT exist on GitHub)
# to v1.2.2 release commit (23e1873aa62acb58b7881eed2a501f5bf35b82e9).
ARG NGINX_VERSION=1.26.2
ARG NGINX_RTMP_MODULE_SHA=23e1873aa62acb58b7881eed2a501f5bf35b82e9
ARG S6_OVERLAY_VERSION=3.2.0.2

# Reviewer H1 (Phase 10 security review) — supply-chain integrity.
# Each *_SHA256 ARG, when set to a non-empty value, gates the
# corresponding tarball through `sha256sum -c -` immediately after
# download. Production builds (Phase 11 CI + release flow) MUST set
# every SHA256 ARG via `--build-arg ...=<sha>`. Local development
# builds leave them empty for the trust-on-first-build path; the
# build prints a warning so the operator sees integrity is unchecked.
#
# CI sources values from .github/checksums.env (PA-14).
# To bootstrap or refresh locally: scripts/refresh-checksums.sh.
ARG NGINX_SHA256=""
ARG NGINX_RTMP_MODULE_TARBALL_SHA256=""
ARG S6_OVERLAY_NOARCH_SHA256=""
ARG S6_OVERLAY_X86_64_SHA256=""
ARG S6_OVERLAY_AARCH64_SHA256=""

# Phase 11 PB-14: SOURCE_DATE_EPOCH for reproducible image timestamps.
# CI sets this from `git log -1 --pretty=%ct`. Local builds leave it
# empty; BuildKit then uses build-time as the fallback.
ARG SOURCE_DATE_EPOCH=""

# ===========================================================================
# Stage 1 — frontend-builder
# Builds the React SPA into /web/dist.
# ===========================================================================
FROM --platform=$BUILDPLATFORM node:22-alpine AS frontend-builder

WORKDIR /web

# Copy only the dep manifests first so a code-only change doesn't bust
# the npm-install cache layer.
COPY web/package.json web/package-lock.json* ./
# PB-15 — verify every installed npm package's published signature against
# npm's keyserver. Catches malicious republishes under existing version
# tags (the 2024 ultralytics / tj-actions class of compromise).
RUN npm ci --no-audit --no-fund \
 && npm audit signatures

# Now the source.
COPY web/ ./
RUN npm run build

# ===========================================================================
# Stage 2 — nginx-builder
# Compiles nginx from source with nginx-rtmp-module statically linked.
# We choose source build (not apt's nginx + apt's libnginx-mod-rtmp) because
# the Debian-shipped module is too old for the directives we use (see
# ADR-0003 §"Hard kill" + Phase 10 design memo §A).
# ===========================================================================
FROM --platform=$BUILDPLATFORM debian:12-slim AS nginx-builder

ARG NGINX_VERSION
ARG NGINX_RTMP_MODULE_SHA
ARG NGINX_SHA256
ARG NGINX_RTMP_MODULE_TARBALL_SHA256

# hadolint ignore=DL3008,DL3015
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        libpcre3-dev \
        libssl-dev \
        zlib1g-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp/build

# Fetch nginx + nginx-rtmp-module. Each fetched as a tarball; nothing
# floats — both pinned by version (nginx) and commit SHA (the module).
# Reviewer H1: SHA-256 verification gates each download when the
# corresponding *_SHA256 ARG is set. Empty ARG = trust-on-first-build
# with a stderr warning so the gap is loud.
RUN curl -fsSL "https://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz" \
        -o nginx.tar.gz \
 && if [ -n "${NGINX_SHA256}" ]; then \
        echo "${NGINX_SHA256} *nginx.tar.gz" | sha256sum -c -; \
    else \
        echo "WARNING: NGINX_SHA256 unset — tarball integrity not verified." >&2; \
    fi \
 && tar -xzf nginx.tar.gz \
 && curl -fsSL \
        "https://github.com/arut/nginx-rtmp-module/archive/${NGINX_RTMP_MODULE_SHA}.tar.gz" \
        -o rtmp.tar.gz \
 && if [ -n "${NGINX_RTMP_MODULE_TARBALL_SHA256}" ]; then \
        echo "${NGINX_RTMP_MODULE_TARBALL_SHA256} *rtmp.tar.gz" | sha256sum -c -; \
    else \
        echo "WARNING: NGINX_RTMP_MODULE_TARBALL_SHA256 unset — module tarball integrity not verified." >&2; \
    fi \
 && tar -xzf rtmp.tar.gz \
 && mv "nginx-rtmp-module-${NGINX_RTMP_MODULE_SHA}" nginx-rtmp-module

# Configure with the modules we need; explicitly drop the ones we don't.
# stream + stream_ssl are OFF for v1 (no RTMPS-from-OBS per ADR-0003
# threat model + Phase 10 §H DA-1).
WORKDIR /tmp/build/nginx-${NGINX_VERSION}
# hadolint ignore=DL3003
RUN ./configure \
        --prefix=/etc/nginx \
        --sbin-path=/usr/sbin/nginx \
        --conf-path=/etc/nginx/nginx.conf \
        --pid-path=/run/nginx/nginx.pid \
        --error-log-path=/data/logs/nginx-error.log \
        --http-log-path=/data/logs/nginx-access.log \
        --with-cc-opt='-O2 -fstack-protector-strong' \
        --add-module=/tmp/build/nginx-rtmp-module \
        --without-http_rewrite_module \
        --without-http_proxy_module \
        --without-http_uwsgi_module \
        --without-http_scgi_module \
        --without-http_fastcgi_module \
        --without-http_memcached_module \
        --without-stream \
        --without-mail_pop3_module \
        --without-mail_imap_module \
        --without-mail_smtp_module \
 && make -j"$(nproc)" \
 && make install

# ===========================================================================
# Stage 3 — backend-builder
# Build a pinned venv at /opt/venv from docker/requirements.hashes.lock.
# Generated by `uv pip compile --generate-hashes --universal` per
# scripts/regenerate-lockfile.sh; lockfile contains transitive closure
# with per-platform hashes. PB-1 + PB-2: --require-hashes is the only
# install mode (no --no-deps fallback path).
# ===========================================================================
FROM --platform=$BUILDPLATFORM python:3.12-slim AS backend-builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build deps for cffi/cryptography wheels in case prebuilt manylinux/musl
# wheels aren't available for the target arch.
# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
        libssl-dev \
 && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

# PB-1: --require-hashes enforces every package + every wheel SHA-256.
# PB-17: no --no-deps (--require-hashes implies an exhaustive closure).
COPY docker/requirements.hashes.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock

# ===========================================================================
# Stage 4 — runtime
# debian:12-slim base + apt-only runtime deps + COPYed artifacts.
# Final image target: ≤400 MB uncompressed (CI fails at 420 MB per I-B3).
# ===========================================================================
FROM debian:12-slim AS runtime

ARG S6_OVERLAY_VERSION
ARG S6_OVERLAY_NOARCH_SHA256
ARG S6_OVERLAY_X86_64_SHA256
ARG S6_OVERLAY_AARCH64_SHA256
ARG TARGETARCH

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    # ADR-0010 default — operator overrides if using paste mode.
    RESTREAM_PASSPHRASE_SOURCE=env

# Runtime apt packages: ffmpeg (passthrough relay), ca-certificates (TLS
# to Twitch/YT/Kick), curl (operator debug + HEALTHCHECK), tzdata
# (Python tz-aware datetime in /api/about), libcap2-bin (setcap step
# below), netcat-openbsd (nginx readiness poll wrapper §C), tini
# (vestigial reaper — kept for `docker exec` debugging, ~100 KB),
# Python 3.12 runtime (matches the backend-builder's interpreter).
# hadolint ignore=DL3008,DL3015
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        ffmpeg \
        libcap2-bin \
        libpcre3 \
        libssl3 \
        netcat-openbsd \
        python3.12 \
        tini \
        tzdata \
        xz-utils \
        zlib1g \
 && rm -rf /usr/share/doc/ffmpeg* /usr/share/man/*/ffmpeg* \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# Create the runtime user. uid 10001 is LOAD-BEARING per Phase 10 §F.
# Operators with pre-existing /data mounts chown to this uid; do not
# change it without a major version bump.
RUN groupadd --system --gid 10001 restream \
 && useradd  --system --uid 10001 --gid 10001 \
             --home-dir /app --shell /usr/sbin/nologin restream

# Install s6-overlay v3. Two tarballs: noarch + per-arch.
# TARGETARCH is Docker's (`amd64`/`arm64`); s6 uses kernel arch
# (`x86_64`/`aarch64`). Translate.
# Reviewer H1: per-tarball SHA-256 verification gated by build-args.
RUN case "${TARGETARCH}" in \
        amd64)   S6_ARCH=x86_64;  S6_ARCH_SHA="${S6_OVERLAY_X86_64_SHA256}"  ;; \
        arm64)   S6_ARCH=aarch64; S6_ARCH_SHA="${S6_OVERLAY_AARCH64_SHA256}" ;; \
        *)       echo "Unsupported TARGETARCH=${TARGETARCH}" >&2; exit 1 ;; \
    esac \
 && curl -fsSL -o /tmp/s6-noarch.tar.xz \
        "https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-noarch.tar.xz" \
 && if [ -n "${S6_OVERLAY_NOARCH_SHA256}" ]; then \
        echo "${S6_OVERLAY_NOARCH_SHA256} */tmp/s6-noarch.tar.xz" | sha256sum -c -; \
    else \
        echo "WARNING: S6_OVERLAY_NOARCH_SHA256 unset — tarball integrity not verified." >&2; \
    fi \
 && curl -fsSL -o /tmp/s6-arch.tar.xz \
        "https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-${S6_ARCH}.tar.xz" \
 && if [ -n "${S6_ARCH_SHA}" ]; then \
        echo "${S6_ARCH_SHA} */tmp/s6-arch.tar.xz" | sha256sum -c -; \
    else \
        echo "WARNING: S6_OVERLAY_${S6_ARCH}_SHA256 unset — tarball integrity not verified." >&2; \
    fi \
 && tar -C / -Jxpf /tmp/s6-noarch.tar.xz \
 && tar -C / -Jxpf /tmp/s6-arch.tar.xz \
 && rm /tmp/s6-noarch.tar.xz /tmp/s6-arch.tar.xz

# Copy nginx binary + minimal config tree from nginx-builder.
COPY --from=nginx-builder /usr/sbin/nginx /usr/sbin/nginx
COPY --from=nginx-builder /etc/nginx/mime.types /etc/nginx/mime.types

# Phase 10 §G — grant CAP_NET_BIND_SERVICE so nginx can run as the
# `restream` user (not root) AND bind privileged ports if/when we
# expose one (future :443 panel). Currently unused (1935 + 8000 are
# both >1024) but pre-applied for forward compatibility. The cap is
# only checked on bind() to <1024, so this is a no-op on the v1
# port set.
#
# Note: any `docker exec -u 0` that replaces this binary loses the
# cap silently — re-apply with the same setcap command after.
#
# Reviewer L3 — build-time tripwire so a Dockerfile reorder that
# accidentally setcaps a different binary, or a `libcap2-bin` removal,
# fails the build instead of producing a silently-incapable image
# (which v1 wouldn't notice because 1935+8000 are >1024, but the
# future :443 panel would).
RUN setcap cap_net_bind_service=+ep /usr/sbin/nginx \
 && getcap /usr/sbin/nginx | grep -q cap_net_bind_service

# Copy the pinned venv. Built on Linux, runs on Linux — paths are
# venv-relative, no rebuild needed at COPY time.
COPY --from=backend-builder /opt/venv /opt/venv

# Application code. Owned by root:root 0755 — read-only at runtime
# (defense in depth per I-F5).
WORKDIR /app
COPY app/ /app/app/

# Built frontend assets. Served via FastAPI's StaticFiles in a future
# step (currently the SPA is served by the same uvicorn process; the
# index.html lives under /app/web/dist/).
COPY --from=frontend-builder /web/dist /app/web/dist/

# nginx config + s6 service tree + entrypoint + healthcheck.
COPY docker/nginx.conf /etc/nginx/nginx.conf
COPY docker/s6 /etc/s6-overlay/s6-rc.d
COPY docker/entrypoint.sh /entrypoint.sh
COPY docker/healthcheck.sh /usr/local/bin/healthcheck.sh
RUN chmod 0755 /entrypoint.sh /usr/local/bin/healthcheck.sh \
 # s6 expects the longrun `run` + `finish` scripts to be executable.
 && find /etc/s6-overlay/s6-rc.d -type f \
        \( -name run -o -name finish -o -name up -o -name down \) \
        -exec chmod 0755 {} +

# Pre-create /data as the volume mount point. Anonymous volumes
# inherit this ownership; bind-mounts are chowned by `init-data` on
# first boot (per Phase 10 §F).
RUN mkdir -p /data /data/logs /data/tls /run/nginx \
 && chown -R restream:restream /data /run/nginx \
 && chmod 0750 /data /data/logs /data/tls

# Ports
EXPOSE 1935/tcp 8000/tcp

VOLUME ["/data"]

# Entrypoint runs as root briefly (chown handled by init-data oneshot,
# not here — see Phase 10 §K). Last line execs /init so PID 1 = s6.
ENTRYPOINT ["/entrypoint.sh"]

# HEALTHCHECK contract per Phase 10 §H. /livez (NOT /readyz) because
# /readyz fails in paste mode forever until human action.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD /usr/local/bin/healthcheck.sh
