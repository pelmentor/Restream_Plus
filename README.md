# Restream_Plus

Self-hosted RTMP restreamer. One stream from OBS in, fanned out to Twitch,
YouTube Live, Kick, and VK Video Live. Web control panel with a single
START/STOP and per-target settings. Ships as one Docker image, pullable
from GitHub Container Registry.

---

## Quick start

```bash
docker run -d \
  --name restream-plus \
  -p 1935:1935 \
  -p 8000:8000 \
  -v restream-data:/data \
  -e RESTREAM_MASTER_PASSPHRASE='change-me-to-something-long-and-random' \
  -e RESTREAM_ADMIN_PASSWORD='change-me-on-first-boot' \
  ghcr.io/<your-user>/restream-plus:latest
```

- OBS Studio → Settings → Stream → Service: Custom; Server:
  `rtmp://<docker-host>:1935/live`; Stream Key: shown in the web panel
  under *Settings → General*.
- Open the panel at `http://<docker-host>:8000/`.
- Log in as `admin` with the password from `RESTREAM_ADMIN_PASSWORD`.
- Configure each target (URL + stream key) and toggle them on.
- Click **START** in the panel. Then start streaming from OBS.

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

v0 — in active development. All 12 planned phases of `docs/CODE_PLAN.md`
are complete (design, scaffold, expert review, ADRs 0001–0011, backend
Phases 1–6, frontend Phases 7–9, container Phase 10, CI/CD Phase 11,
ops docs Phase 12). Source for Phases 0–10 is on GitHub; Phase 11 + 12
local-only pending push. Next move is operator-facing — open
follow-ups in `docs/SESSION_HANDOFF.md` or cut the first `v1.0.0`
tag (which exercises `release.yml` + `docs/ops/release-checklist.md`
for the first time).
