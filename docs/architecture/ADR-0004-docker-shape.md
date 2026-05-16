# ADR-0004: Docker shape — single image with s6-overlay

## Status
Accepted — 2026-05-15

## Context

The product is delivered as "pull this image, run it, open the panel". The
user is a streamer, not a Kubernetes operator. The deployment surface must
be as small as possible:

- One `docker run` command (or one compose service) gets a working system.
- One image to pull from `ghcr.io/<user>/restream-plus`.
- One persistent volume for SQLite + per-target logs.
- One environment variable to set (the master passphrase).

The container has to run, simultaneously, two long-lived processes:

- `nginx` (with the RTMP module compiled in).
- The Python control plane (`uvicorn`).

Plus zero-to-six ffmpeg workers, which the control plane spawns and owns —
*not* a process supervisor's responsibility.

## Decision

**One image. Two top-level supervised processes managed by `s6-overlay` v3.
ffmpeg workers are children of the control plane and are not s6-managed.**

```
restream-plus image
├── Base: debian:12-slim
│   - ffmpeg (apt, the recent backport from deb-multimedia or compiled fresh
│     during the build — final choice deferred to the Dockerfile work)
│   - nginx (compiled from source with nginx-rtmp-module to get a recent
│     nginx + the module in one go — the Debian-packaged nginx-rtmp is
│     too old for our config style)
│   - python 3.12 slim runtime (from python:3.12-slim multi-stage builder)
│   - s6-overlay v3
├── /etc/s6-overlay/s6-rc.d/
│   ├── nginx/             (long-running, depends on nothing)
│   └── control-plane/     (long-running, depends on nginx being up)
├── /app/                  (the Python control plane + built static SPA)
└── /data/                 (volume mount: SQLite DB + per-target logs)
```

### Why s6-overlay specifically

The naive instinct for "two processes in one container" is to write a
`CMD ["sh", "-c", "nginx & python app.py"]`. This is wrong:

- No PID 1 reaping → zombie processes accumulate.
- One service dies, the other keeps running, container looks healthy. Bad.
- SIGTERM handling on container stop is unpredictable.

The historical fixes were `supervisord` and `runit`. `s6-overlay` is the
modern choice for containerized init:

- Real PID 1 (`s6-svscan`) does proper zombie reaping.
- Service dependencies (control-plane waits for nginx to be ready before
  starting).
- Single-shot init scripts (e.g., generate self-signed cert if absent,
  initialize SQLite schema on first boot) compose naturally as
  `oneshot` services.
- Graceful shutdown: SIGTERM to the container propagates to all
  long-running services with a configurable grace period.

`s6-overlay` is not a "crutch" — it's what the just-do-it-properly answer
looks like for the "two processes in one container" problem. The
alternative is a multi-container deployment, which we explicitly rejected
below.

### Image build pipeline

Multi-stage Dockerfile, three stages:

1. **`frontend-builder`** (`node:22-alpine`): `npm ci`, `npm run build`,
   emits `/web/dist/`.
2. **`backend-builder`** (`python:3.12-slim`): installs Poetry/uv, resolves
   and exports a pinned `requirements.txt`, installs into a venv at
   `/opt/venv`.
3. **`runtime`** (`debian:12-slim`): apt-installs `ffmpeg`, copies a
   freshly-built `nginx + nginx-rtmp-module` (built in a fourth `nginx-
   builder` stage from `debian:12-slim` to keep the runtime layer clean of
   build tools), copies the venv and the SPA, installs `s6-overlay`,
   configures s6 services, runs as a non-root user `restream` (uid 10001).

### Volume layout (mounted at `/data` by convention)

```
/data/
├── restream.db                 # SQLite, encrypted secrets inside, plus
│                               # everything else cleartext (no platform
│                               # uses our DB; encryption is for the
│                               # secrets-stolen-DB-dump scenario)
├── ingest.key                  # the OBS ingest key (regeneratable via UI)
├── logs/
│   ├── nginx-access.log
│   ├── nginx-error.log
│   ├── control-plane.log
│   └── target-<id>.log
└── tls/                        # optional, if user supplies own cert
    ├── server.crt
    └── server.key
```

## Consequences

**Easier:**
- One `docker pull && docker run`. The user does not need compose,
  Kubernetes, or even compose-spec literacy.
- Atomic upgrades: pull a new tag, `docker run` it, done. State is in the
  volume, not the image.
- Local development mirrors production: same image, mount source over
  `/app` for hot-reload.

**Harder:**
- Larger image than a hypothetical "just the API in alpine" build. We
  optimize: multi-stage build, runtime layer omits build toolchains,
  `python:3.12-slim` base + only the deps we need. Target final size
  ≤ 400 MB uncompressed, ≤ 180 MB compressed.
- Updating nginx or nginx-rtmp-module independently of the control plane
  is impossible (one image, one tag). Acceptable — both move slowly and a
  full image rebuild on a tag is cheap in CI.

**Rejected alternatives:**
- **docker-compose with separate services (nginx-rtmp + backend + web)**:
  cleaner separation, but worse UX (user has to fetch a compose file
  *and* keep three image tags in sync). For a single-machine
  self-hosted appliance, the operational burden of compose outweighs the
  architectural cleanliness.
- **One image with supervisord**: works, but s6-overlay has better
  shutdown semantics and dependency ordering. supervisord was the right
  answer in 2015; in 2026 it isn't.
- **Bare CMD with `&`**: rejected above — zombie reaping, no health.
- **Run as root**: rejected — drop to uid 10001 immediately after s6
  init steps (which need root for nginx to bind :1935 — fixed by
  granting CAP_NET_BIND_SERVICE on the nginx binary, then dropping caps).

## Open questions / revisit triggers

- If we ever need to scale to multiple nodes (we don't), this whole
  ADR is wrong — and per F# SA-C.image, the move would be **more than
  one decision**. The single-image / loopback assumption is wired
  into nginx-rtmp's `allow play 127.0.0.1`, the KEK living in-process
  with the supervisor, and the s6 service topology. Calling this
  decision "reversible" understates the cost. Treat it as load-bearing.
- If GHCR's pull rate limits become a problem (they aren't, for a
  hobbyist user), we'd publish to Docker Hub in parallel via the same
  GHA workflow.
