# Restream_Plus

Self-hosted RTMP restreamer. One stream from OBS in, fanned out to Twitch,
YouTube Live, Kick, and VK Video Live. Web control panel with a single
START/STOP and per-target settings. Ships as one Docker image, pullable
from GitHub Container Registry.

---

## Quick start

### Option 1 — `docker run` (single-line, no extra files)

```bash
docker run -d \
  --name restream-plus \
  --restart unless-stopped \
  -p 1935:1935 \
  -p 8000:8000 \
  -v restream-data:/data \
  --stop-timeout 30 \
  -e RESTREAM_MASTER_PASSPHRASE='change-me-to-something-long-and-random' \
  ghcr.io/pelmentor/restream-plus:v1.1.0
```

Notes on the flags:
- `-p 1935:1935` — RTMP ingest (OBS pushes here).
- `-p 8000:8000` — web panel + REST + WebSocket. Front it with Caddy /
  nginx / Traefik if you want TLS + a hostname (recipes in
  [docs/ops/deployment.md](docs/ops/deployment.md) §Reverse proxies).
- `-v restream-data:/data` — named volume holds the SQLite DB,
  encrypted credentials, audit log, KDF salt, and nginx logs. **Back
  this up** ([docs/ops/backup-restore.md](docs/ops/backup-restore.md)).
- `--stop-timeout 30` — gives the supervisor 20s to drain ffmpeg
  workers + 10s for nginx. Docker's default 10s tears the container
  apart mid-flight.
- `--restart unless-stopped` — auto-restart on host reboot.
- `RESTREAM_MASTER_PASSPHRASE` — derives the AES-256-GCM key that
  encrypts stream keys at rest. **Pick something long and random and
  keep it backed up out-of-band** — losing it loses every encrypted
  stream key in the volume.
- Pin `:v1.1.0` (immutable). `:latest` and `:v1` float to subsequent
  releases; use only if you actively want to track those.

### Option 2 — Docker Compose (recommended for hosts you'll touch twice)

A canonical compose file ships at
[docs/ops/compose.yaml](docs/ops/compose.yaml). Adapt the image tag
and the passphrase:

```bash
curl -O https://raw.githubusercontent.com/pelmentor/Restream_Plus/main/docs/ops/compose.yaml
# Edit compose.yaml: set image tag (:v1.1.0) and adjust the env-file path.
echo "RESTREAM_MASTER_PASSPHRASE=$(openssl rand -hex 32)" | sudo tee /etc/restream/env > /dev/null
sudo chmod 600 /etc/restream/env
docker compose up -d
```

### Verify the image signature (recommended)

Every release is cosign-keyless-signed via GitHub Actions OIDC.
Confirms the image came from this repo's `release.yml`:

```bash
cosign verify \
  --certificate-identity-regexp '^https://github\.com/pelmentor/Restream_Plus/\.github/workflows/release\.yml@refs/tags/v[0-9.]+$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/pelmentor/restream-plus:v1.1.0
```

Requires `cosign` 2.2+.

### After the container is up

- OBS Studio → Settings → Stream → Service: Custom; Server:
  `rtmp://<docker-host>:1935/live`; Stream Key: shown in the web panel
  under *Settings → General*.
- Open the panel at `http://<docker-host>:8000/`.
- Log in as `admin`. On first boot, the container generates a 20-char
  admin password and prints it to stdout exactly once — grab it from
  `docker logs restream-plus 2>&1 | grep -A 8 'FIRST BOOT'`. To skip
  the autogen, set `-e RESTREAM_ADMIN_PASSWORD='your-pw'` on first
  boot. To rotate later, change it from *Settings → Change Password*
  in the UI (or restart with `-e RESTREAM_RESET_ADMIN_PASSWORD=1`
  paired with a new `RESTREAM_ADMIN_PASSWORD`).
- Configure each target (URL + stream key) and toggle them on.
- Click **START** in the panel. Then start streaming from OBS.

### Upgrading from a prior version

In-place pull + restart works for any non-schema-bump release (v1.1.0
is one). Full procedure including the export → reimport flow for
schema bumps: [docs/ops/upgrade.md](docs/ops/upgrade.md).

## Why this exists

Commercial restream services (Restream.io, StreamYard) work, but you
hand them your stream and your stream keys. This is the same thing,
self-hosted, on hardware you control, for the cost of bandwidth.

## Architecture in one paragraph

OBS pushes RTMP to nginx-rtmp inside the container. nginx hits a
control-plane webhook (FastAPI) to authenticate the publish. The
control plane supervises one `ffmpeg -c copy` worker per enabled
target; each worker pulls from `rtmp://127.0.0.1/live/in` and pushes
to a remote platform with passthrough (no transcoding). A React SPA
shows live status over WebSocket. SQLite persists targets, settings,
sessions, and audit; stream keys are encrypted at rest with AES-256-GCM
using a key derived from `RESTREAM_MASTER_PASSPHRASE`.

Full details: [docs/architecture/system-overview.md](docs/architecture/system-overview.md).

## Documentation

- [System overview](docs/architecture/system-overview.md) — C4 diagrams, data flow, failure modes.
- [ADRs](docs/architecture/) — load-bearing decisions, one per file.
- [Platform reference](docs/architecture/platforms-reference.md) — RTMP endpoints + VK quirks.
- [UX flows](docs/ui/ux-flows.md) — information architecture, every screen.
- [Design system](docs/ui/design-system.md) — tokens, components, theming.
- [Deployment](docs/ops/deployment.md) — production deployment guide.

## Project rules

This codebase is built under two non-negotiable rules:

1. **No quick-and-dirty fixes.** Crutchless solutions only, even if it
   takes weeks. Workarounds get refused.
2. **No migration baggage.** Greenfield design. No back-compat shims.

## License

Apache-2.0. See [LICENSE](LICENSE).

## Status

**v1.1.0 — current release.** Image at
`ghcr.io/pelmentor/restream-plus:v1.1.0`, cosign-keyless-signed,
multi-arch (amd64 + arm64), pulled from GitHub Container Registry.
v1.0.0 shipped 2026-05-17 with all 12 planned phases of
`docs/CODE_PLAN.md` complete (ADRs 0001–0011, backend Phases 1–6,
frontend Phases 7–9, container Phase 10, CI/CD Phase 11, ops docs
Phase 12); v1.1.0 (2026-05-18) added the live dashboard stats
feature (per-target bitrate + drops, host CPU%, ingest, sparklines)
on top of v1.0.0 with no breaking changes. Release notes for each
version live under [docs/releases/](docs/releases/). For the full
post-ship chronology including the three v1.0.0 stillborn-and-recut
iterations that surfaced latent `release.yml` drift, see
[docs/SESSION_HANDOFF.md](docs/SESSION_HANDOFF.md).
