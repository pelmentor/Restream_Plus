# Phase 10 — Container (locked design)

**Status:** Locked 2026-05-16. Binding for Phase 10 code.
**Synthesis of:** two agency-agent design passes — Software Architect
+ Backend Architect — per Rule №3. UX/UI personas were skipped: Phase
10 ships no UI surface (Dockerfile + nginx.conf + entrypoint.sh + s6
services + healthcheck.sh).

Phase 10 packages everything Phases 1–9 produced into one OCI image
that runs `nginx-rtmp` + the FastAPI control plane under `s6-overlay`,
publishes on `:1935` (RTMP) + `:8000` (HTTP), persists to a single
`/data` volume, and survives `docker stop -t 30` without losing the
session-end audit row. No code changes to `app/` beyond a single
load-bearing tweak to `app/main.py::main()` (§M) and a single
production-default flip in `app/config.py` (§Q1).

---

## A. Image build topology

Four named stages, ordered so cache invalidation in one stage doesn't
ripple into the others:

```
frontend-builder   FROM node:22-alpine@sha256:...     →  /web/dist
nginx-builder      FROM debian:12-slim@sha256:...     →  /usr/sbin/nginx + /etc/nginx/{mime.types,nginx.conf is overlaid later}
backend-builder    FROM python:3.12-slim@sha256:...   →  /opt/venv
runtime            FROM debian:12-slim@sha256:...     ←  COPY from all three
```

Runtime apt-installs `ffmpeg`, `ca-certificates`, `curl`, `tzdata`,
`libcap2-bin` (for `setcap` step), `tini` (kept as a vestigial reaper
for `docker exec` debugging; s6 is PID 1), and `s6`-runtime libs that
the s6-overlay tarball expects. Nothing else.

### Pinning policy (the load-bearing part)

| What                       | How                                                                                           |
| -------------------------- | --------------------------------------------------------------------------------------------- |
| Every `FROM` base image    | `<name>:<tag>@sha256:<digest>` — tag floats, digest doesn't.                                  |
| nginx source               | `ARG NGINX_VERSION=1.26.x` + SHA-256 of the tarball verified at build time.                   |
| nginx-rtmp-module          | `arut/nginx-rtmp-module` at a specific commit SHA, recorded in `docker/nginx-rtmp.sha`.       |
| s6-overlay                 | `ARG S6_OVERLAY_VERSION=3.2.x` + SHA-256 of the noarch + per-arch tarballs.                   |
| Python deps                | `uv lock` → `uv export --frozen --no-hashes > docker/requirements.lock`. `pip install` from that lock file in `backend-builder`. `uv` itself is NOT in the runtime image. |
| apt packages               | Pin major (e.g., `ffmpeg=7:5.1.*`); accept floating patch (Debian point releases are security-only). `--no-install-recommends` everywhere. |

**Choice of nginx track:** 1.26 (stable), not 1.28 (mainline). Homelab
appliance, not a CDN.

**Choice of nginx-rtmp fork:** `arut/nginx-rtmp-module` per
ADR-0003's explicit naming. The `sergey-dryabzhinsky` fork is more
actively patched but ADR-0003 doesn't bless it; switching is a future
ADR amendment, not Phase 10's call to make. See DA-2 below.

**Choice of `uv`:** Phase 1's lockfile is already `uv.lock`; doing
anything else duplicates a source of truth. The runtime image ships
the venv only — operators can't `pip install --force-reinstall` to
patch a venv in-place (which is the right answer; they should redeploy).

### Rejected alternatives

- **Combined `builder` stage.** A Python dep change would rebuild
  nginx. Cache invalidation discipline is the single most under-valued
  lever for build speed.
- **`node:22-bookworm` for the frontend.** ~250 MB heavier, no native
  modules per ADR-0002, slows the cold-cache build by ~40s. Alpine
  wins for builder-only roles where ABI doesn't bleed into runtime.
- **Alpine runtime.** glibc → musl ABI break, ffmpeg packaging differs,
  nginx-rtmp build flags shift. `debian:12-slim` is sticky for v1.
- **Floating `master` checkout of nginx-rtmp-module.** Two builds a
  week apart produce different binaries; CVE attribution becomes
  "what was in our build last Tuesday?"

### Invariants

- **I-A1**: Every `FROM` is pinned by `@sha256:` digest.
- **I-A2**: `NGINX_VERSION` + `NGINX_RTMP_MODULE_SHA` are top-of-file
  `ARG`s. Touching one in a release PR requires touching the other.
- **I-A3**: `uv` is invoked with `--frozen` against `uv.lock` (or the
  exported lock). The runtime image does not contain `uv`.
- **I-A4**: Runtime base is `debian:12-slim`. Not alpine.
- **I-A5**: `pip install` in `backend-builder` uses
  `--no-cache-dir --no-deps -r /tmp/requirements.lock`.

---

## B. Image-size discipline

Target: ≤400 MB uncompressed, ≤180 MB compressed (ADR-0004).
Realistic budget:

| Layer                           | Budget  |
| ------------------------------- | ------- |
| `debian:12-slim` base           | ~75 MB  |
| ffmpeg + codec libs (apt)       | ~95 MB  |
| nginx binary + runtime libs     | ~10 MB  |
| Python 3.12 + stdlib (slim)     | ~50 MB  |
| `/opt/venv` (deps)              | ~120 MB |
| `/app` (Python + SPA dist)      | ~25 MB  |
| s6-overlay v3                   | ~8 MB   |
| **Total**                       | **~383 MB** |
| **Headroom**                    | **~17 MB**  |

### Levers (in payoff order)

1. **`--no-install-recommends`** (saves ~80 MB on naive ffmpeg).
2. **Single-RUN apt with cleanup tail** (`apt-get clean && rm -rf
   /var/lib/apt/lists/*`).
3. **`.dockerignore`** excludes `tests/`, `docs/`, `.git/`,
   `web/node_modules/`, `__pycache__/`, `*.pyc`, `htmlcov/`,
   `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`.
4. **Selective `COPY --from`**: only `/usr/sbin/nginx` +
   `/etc/nginx/mime.types` from `nginx-builder`. NOT
   `/etc/nginx/html/` (default landing page).
5. **`PYTHONDONTWRITEBYTECODE=1`** in runtime ENV.
6. **Strip ffmpeg docs/man** (`rm -rf /usr/share/doc/ffmpeg*
   /usr/share/man/*/ffmpeg*`) — ~5 MB.

### What we won't strip

- `coreutils`, `bash`, `procps`, `curl` — operator debugging at 3am is
  worth ~12 MB.
- `ca-certificates` — required for outbound TLS to Twitch/YT/Kick.

### Invariants

- **I-B1**: Every `RUN apt-get install` uses `--no-install-recommends`
  and ends with cleanup in the same RUN.
- **I-B2**: `.dockerignore` includes the 9 entries in lever #3.
- **I-B3**: CI fails if final image > 420 MB uncompressed (5% over
  target — alarms before contract breach).
- **I-B4**: No `__pycache__` in `/opt/venv` (post-build smoke test).

---

## C. s6-overlay v3 service topology

```
/etc/s6-overlay/s6-rc.d/
├── init-data/                                 (type=oneshot, no deps)
│   ├── type                                   ("oneshot")
│   ├── up                                     (mkdir+chown script)
│   └── down                                   (empty — chown is idempotent)
├── nginx/                                     (type=longrun)
│   ├── type                                   ("longrun")
│   ├── dependencies.d/init-data               (marker file)
│   ├── run                                    (wrapper polls port, signals fd 3)
│   ├── finish                                 (sleep 2 — crash backoff)
│   ├── notification-fd                        ("3\n")
│   ├── down-signal                            ("SIGQUIT" — graceful drain)
│   └── timeout-finish                         ("10000")
├── control-plane/                             (type=longrun)
│   ├── type                                   ("longrun")
│   ├── dependencies.d/nginx                   (marker file)
│   ├── dependencies.d/init-data               (marker file — defense in depth)
│   ├── run                                    (exec s6-setuidgid restream python -m app.main)
│   ├── finish                                 (sleep 2 — crash backoff)
│   ├── notification-fd                        ("3\n")
│   ├── down-signal                            ("SIGTERM")  ← uvicorn-handled
│   └── timeout-finish                         ("20000")    ← ≥ LIFESPAN_SHUTDOWN_GRACE + 5s
└── user/contents.d/
    ├── init-data
    ├── nginx
    └── control-plane
```

### `init-data` as a oneshot (NOT in the entrypoint)

The entrypoint runs as root (PID 1 inherits root), but it runs *before*
s6 logging is up. A oneshot inside the s6 graph runs at the right
phase (after s6 init, before any longrun), logs to the same s6 log
channel as everything else, and surfaces failure as
`s6-svstat` failure. Better diagnosability for ~15 lines of `up`
script.

Operations:
- `mkdir -p /data/logs /data/tls`.
- `chown -R 10001:10001 /data`.
- `chmod 750 /data /data/logs /data/tls`.

Idempotent — re-runs every boot are no-ops on a stable mount.

### `notification-fd` on control-plane — semantics

s6 file `control-plane/notification-fd` contains `3\n`. After
`supervisor.wait_ready()` returns (and after uvicorn has bound the
socket), `app/main.py::main()` writes `\n` to fd 3 and closes it. s6
then marks `control-plane` as `ready`.

**Critical for paste mode:** the s6-ready signal fires regardless of
KeyMaterial unlock state. `supervisor.wait_ready()` succeeds whether
or not the KEK is loaded; the supervisor's TaskGroup is alive either
way. The s6-ready contract is "process is past boot and is now
answering requests" — which is `/livez` semantics, not `/readyz`. This
amends the parent prompt's hint that suggested gating s6-ready on
`/readyz=200` (which would be impossible in paste mode until the
operator pastes — and there's no operator if the orchestrator gates
restarts on s6-ready). Surface to ADR-0011 via DA-1 below.

### Wiring fd 3 from uvicorn into `app/main.py`

uvicorn doesn't expose a "post-startup hook" directly, but our lifespan
runs `await supervisor.wait_ready()` inside `__aenter__` before
`yield`. Right after `wait_ready` succeeds (just before `yield`), we
write to fd 3. The write happens once per process lifetime; on
shutdown nothing needs to be undone.

```python
# app/main.py (illustrative — see §M for the exact diff)
try:
    await supervisor.wait_ready(timeout=LIFESPAN_SUPERVISOR_READY_TIMEOUT)
    _notify_s6_ready()       # writes "\n" to fd 3 if it's open, else no-op
except BaseException:
    ...
```

`_notify_s6_ready()` is a small helper guarded by `try/except OSError`
— if fd 3 isn't open (dev runs, tests, etc.) the write is a no-op.

### `nginx/run` polls the port before signaling ready

The naive `exec /usr/sbin/nginx -g 'daemon off;'` signals s6-ready
the moment the binary execs, BEFORE the listen socket is up. That
opens a ~50 ms window where control-plane could try
`_check_nginx_rtmp()` and see "connection refused".

Wrapper:

```sh
#!/usr/bin/with-contenv sh
# /etc/s6-overlay/s6-rc.d/nginx/run
/usr/sbin/nginx -g 'daemon off;' &
NGINX_PID=$!
# Poll loopback up to 5s; signal ready on fd 3 once accepting.
for _ in $(seq 1 50); do
    if nc -z 127.0.0.1 1935 2>/dev/null; then
        printf '\n' >&3
        wait "$NGINX_PID"
        exit $?
    fi
    sleep 0.1
done
kill "$NGINX_PID" 2>/dev/null || true
exit 1
```

`nc` (BusyBox netcat) ships with the runtime image; install via apt if
not bundled with the base.

### Rejected alternatives

- **`init-permissions` as a separate oneshot.** Folding into
  `init-data` keeps the graph small; both are "boot-time fs prep,
  needs root".
- **`db-migrate` oneshot.** Schema init lives in the Python lifespan
  (`app/db.initialize_schema`). A second path is duplicate code with a
  "which one wins" question.
- **No notification-fd.** s6 would fall back to "ready when started"
  which misleads `s6-svstat`.

### Invariants

- **I-C1**: Exactly the four s6 nodes above (`init-data`, `nginx`,
  `control-plane`, `user`). Additions require an ADR-0004 amendment.
- **I-C2**: `control-plane` depends (transitively or directly) on
  `init-data` so `/data` is writable before any longrun starts.
- **I-C3**: `notification-fd` files contain `3\n` (single digit + newline).
  `app/main.py` writes to fd 3 after `supervisor.wait_ready()` succeeds,
  **regardless of KeyMaterial unlock state**.
- **I-C4**: No APPLICATION code runs as root. control-plane drops to
  `restream` via `s6-setuidgid` BEFORE python exec. nginx WORKERS drop
  to `restream` via the `user restream;` directive in nginx.conf;
  nginx MASTER stays as root by design (conventional + required by
  the `user` directive's semantics). The `setcap` on nginx (§G) is
  pre-applied for future privileged-port use; not load-bearing today.

---

## D. SIGTERM choreography

Authoritative signal flow on `docker stop -t 30 restream-plus`:

```
t=0:        Docker → SIGTERM → PID 1 (s6-svscan)
t=0+ε:      s6 walks deps leaf-first → control-plane SIGTERM
              (because control-plane depends-on nginx — leaf = control-plane)
t=0+ε:      uvicorn signal handler → FastAPI lifespan __aexit__ runs
              → supervisor.stop(grace=LIFESPAN_SHUTDOWN_GRACE=15s)
              → SIGTERM to each ffmpeg worker (parallel; 5s grace per ADR-0003)
              → SIGKILL stragglers at 5s outer
              → cancel ws_broadcaster / reprompt_pruner / kdf-cleanup
              → engine.dispose()
t≤15s:      control-plane exits 0
t≈15s:      s6 → SIGQUIT → nginx (graceful drain — workers already gone)
t≤25s:      nginx exits 0
t≈25s:      init-data has no shutdown action
t≈25s:      s6-svscan exits → container exits
t≤30s:      Docker grace satisfied; no SIGKILL needed
```

**Direction note (load-bearing for future contributors):**
`control-plane depends-on nginx` reads as "control-plane needs nginx
first" — and that's correct for **startup** order. On **shutdown**, s6
walks the graph **reverse-topologically**, so control-plane stops
first. This is what we want — ffmpeg workers (children of
control-plane) need their upstream (nginx) alive while they finish
their final RTMP push frames to the remote platforms. Killing nginx
first would force every worker to die from a broken pipe, garble the
last seconds of every outbound stream, and race the audit-write.

### Per-longrun signal + timeout matrix

| Service       | down-signal | timeout-finish | Why                                                  |
| ------------- | ----------- | -------------- | ---------------------------------------------------- |
| control-plane | SIGTERM     | 20000 ms       | uvicorn handles SIGTERM. 20s = `LIFESPAN_SHUTDOWN_GRACE` (15s) + 5s slack. |
| nginx         | SIGQUIT     | 10000 ms       | SIGQUIT is nginx's graceful-drain signal (SIGTERM is fast-shutdown that drops connections). |

### Docker default vs our 30s requirement

`docker stop` default `--time=10` SIGKILLs PID 1 after 10s. Our
choreography needs ~25-30s. **We cannot change Docker's default from
the image side.** The image-level `STOPSIGNAL` directive sets the
signal (already `SIGTERM` by default), not the timeout.

**Operator advice — load-bearing for docs:**

> Always invoke `docker stop -t 30 restream-plus` (or compose
> `stop_grace_period: 30s`). The default 10-second grace cuts your
> outbound stream mid-frame and skips the session-end audit row.

This goes in: `README.md` Quick Start callout; `docs/ops/deployment.md`
(Phase 12); and the bundled `compose.yaml` (Phase 12) with
`stop_grace_period: 30s` baked in.

### `finish` scripts for restart backoff

s6's default for a crashing longrun is immediate restart, no backoff.
We add a 2-second sleep in the `finish` script for both longruns to
avoid tight restart loops in pathological cases (e.g., wrong
passphrase → control-plane crash → restart → crash).

```sh
#!/usr/bin/with-contenv sh
# nginx/finish, control-plane/finish — identical
exec sleep 2
```

### Rejected alternatives

- **Shrink `LIFESPAN_SHUTDOWN_GRACE` to fit Docker's 10s default.**
  Sacrifices the worker drain we explicitly designed in ADR-0003.
  Wrong fix.
- **`STOPSIGNAL SIGQUIT`** at the Dockerfile level. s6 receives the
  container-stop signal; it doesn't forward SIGQUIT as graceful-drain
  semantics. The per-service `down-signal` files are the right knob.

### Invariants

- **I-D1**: `control-plane/timeout-finish = 20000` ms. Must always be
  ≥ `LIFESPAN_SHUTDOWN_GRACE.total_seconds() * 1000 + 5000`. A CI
  test parses both files and asserts.
- **I-D2**: `nginx/down-signal = SIGQUIT` (not default SIGTERM).
- **I-D3**: `nginx/timeout-finish = 10000` ms.
- **I-D4**: README Quick Start AND `docs/ops/deployment.md` AND the
  bundled `compose.yaml` document `docker stop -t 30` /
  `stop_grace_period: 30s`. Load-bearing.
- **I-D5**: Both longruns have a `finish` script with ≥2s sleep.

---

## E. Passphrase modes from one image

**The entrypoint does NOT branch on `RESTREAM_PASSPHRASE_SOURCE`.** The
control plane's lifespan does (already implemented at
`app/main.py:193`). Both modes use the same image, same s6 graph, same
entrypoint, same `python -m app.main`.

Mode switching is `docker stop && docker run -e
RESTREAM_PASSPHRASE_SOURCE=paste <other env...>` — no rebuild.

### What the entrypoint MUST NOT do

- **Never echo `RESTREAM_MASTER_PASSPHRASE`.** No `set -x`. No
  `printenv | grep RESTREAM_`. No `env > /tmp/debug.env`. The env var
  appears in `/proc/<pid>/environ` and `docker inspect`; anything else
  is a new leak surface we own.
- **Never write the passphrase to disk.** Python reads it directly
  from its own env.
- **Never log the passphrase on failure.** No `if [ -z "$FOO" ]; then
  echo "FOO is required, got: $FOO"; fi` anti-pattern.

### What the entrypoint MUST do

- `exec /init` (the s6 init binary) so PID 1 is s6, not bash. Without
  `exec`, bash stays as PID 1, doesn't reap zombies, doesn't forward
  signals correctly.
- `umask 027` so any file the python process creates in `/data` is not
  world-readable.
- One fail-fast gate (§K) for the single misconfiguration that yields
  the worst operator experience (env-mode source + missing passphrase
  → otherwise-buried pydantic stack trace).

### Invariants

- **I-E1**: Entrypoint script never emits the VALUE of
  `RESTREAM_MASTER_PASSPHRASE` (the variable NAME may appear in the
  fail-fast empty-check; the value must never be echoed). CI grep:
  `grep -E '\$\{?RESTREAM_MASTER_PASSPHRASE\}?\b' docker/entrypoint.sh`
  returns only the `[ -z "${RESTREAM_MASTER_PASSPHRASE:-}" ]` form (a
  test, not an emit). No `echo`/`printf`/`printenv`/`env` line may
  reference the variable.
- **I-E2**: Entrypoint's last line is `exec /init`. Anything after is
  dead code.
- **I-E3**: Entrypoint never uses `set -x`. `set -eu` is required.
- **I-E4**: Mode switching needs no rebuild.

---

## F. Volume + permissions topology

`/data` is the single bind-mountable volume. Runtime user is
`restream`, **uid 10001, gid 10001**, no login shell, no password.

### Why uid 10001

- Below 65534 (so it's not `nobody`).
- Above 1000 (so it doesn't collide with typical host uids — `alex` at
  1001 won't share file ownership with the container).
- Round number so operators can `chown -R 10001:10001 /host/path`
  without checking docs.
- **Load-bearing**: changing the uid breaks every operator who has
  pre-chowned a host directory. Treat as one-way.

### chown location

| Place                | Verdict                                                                                |
| -------------------- | -------------------------------------------------------------------------------------- |
| Dockerfile `RUN chown` | Doesn't work — bind-mount overlays image ownership. Rejected.                        |
| Entrypoint           | Works. Pre-s6 logs are harder to attribute. Acceptable fallback.                       |
| `init-data` oneshot  | Runs at the right phase (after s6 up, before longruns), logs to s6 channel, surfaces failure as `s6-svstat` failure. **Chosen.** |

### First-boot sequence

1. Container starts. PID 1 = s6-svscan (root).
2. `init-data` runs as root: `mkdir -p /data/logs /data/tls`, idempotent
   `chown -hR 10001:10001 /data` (skipped if already uid 10001 per M3
   below), `chmod 0750 /data /data/logs /data/logs/control-plane /data/tls`.
3. `nginx` longrun starts. The s6 `run` script runs as root (s6's default
   uid for longruns). It execs `/usr/sbin/nginx`, whose master process
   inherits root. nginx.conf's `user restream;` directive drops the
   WORKER processes to `restream` (uid 10001). This is conventional
   nginx — `user` only affects workers; master always holds the uid
   that exec'd it. Master-as-root is the de-facto-correct model for
   nginx; switching the master to `restream` via `s6-setuidgid` makes
   the `user` directive a no-op (nginx warns about this) without
   meaningful security gain.
4. `control-plane` longrun starts via `s6-setuidgid restream
   /opt/venv/bin/python -m app.main`. The privilege drop happens
   BEFORE `exec`, so the python process never sees root.
5. After step 4, only nginx MASTER + s6-svscan + s6 supervisors run as
   root. The nginx master is a small, audited C binary that
   ADR-0003 trusts; the s6 daemons run a tiny pre-emptive scheduler
   loop. No application code runs as root.

### M1 — corrected nginx master uid claim

Reviewer M1 (Phase 10 security review) caught an earlier draft of
this section claiming the nginx master ran as `restream`. That was
wrong — see step 3 above. The `setcap cap_net_bind_service=+ep` on
`/usr/sbin/nginx` (§G) is currently **unused** (1935 + 8000 are both
>1024 + nginx master runs as root anyway) and is pre-applied for
forward compatibility with a future TLS panel on :443. If we ever
add `s6-setuidgid restream` to `docker/s6/nginx/run`, the `setcap`
becomes load-bearing AND the `user restream;` directive in nginx.conf
must be removed (nginx warns "the 'user' directive makes sense only
if the master process runs with super-user privileges" otherwise).
Do not change the nginx longrun's privilege model without amending
nginx.conf in lockstep.

### Operator-facing behavior

If the operator mounts a host directory owned by a different uid,
`init-data`'s chown will succeed inside the container (changing both
the container's view and the on-host file ownership). Document this
in Phase 12's `docs/ops/deployment.md`:

> The container takes ownership of `/data` on every start (chowns to
> uid 10001). To pre-empt, run `chown -R 10001:10001 /your/host/path`
> before first start.

### Invariants

- **I-F1**: Runtime user is `restream`, uid 10001, gid 10001.
- **I-F2**: `/data` exists in the image as an empty directory owned by
  `restream:restream` 0750 (so anonymous volumes work without
  `init-data` chowning).
- **I-F3**: `init-data` is idempotent.
- **I-F4**: No code path chowns `/data` to root post-`init-data`.
- **I-F5**: `/app` is owned by `root:root` 0755 (defense in depth — a
  compromised control-plane can't modify its own code).

---

## G. `CAP_NET_BIND_SERVICE` for nginx

### Decision

Apply `setcap cap_net_bind_service=+ep /usr/sbin/nginx` in the runtime
stage Dockerfile (AFTER the `COPY --from=nginx-builder`).

This lets nginx bind privileged ports without running master as root.
nginx.conf top-level `user restream;` matches the runtime user; we
eliminate the conventional "master root, workers nobody" split.

### A nuance worth recording

`:1935` is **above** 1024, so v1 ports (1935 RTMP + 8000 HTTP) don't
actually require `CAP_NET_BIND_SERVICE`. We apply the cap **for
forward compatibility** with a future TLS panel on :443. Keeping it
now means the day we expose :443 (likely after the RTMPS-from-OBS work
per ADR-0003 §"Open questions"), no filesystem-state change in the
image is needed.

The cap costs one line in the Dockerfile and a small `libcap2-bin`
install. It does **not** weaken security: the kernel only checks the
cap on `bind()` to ports < 1024; on >1024 binds, the cap is a no-op.

### Rejected alternatives

- **Bind nginx to :11935 and remap via Docker `-p`.** Two problems:
  - The loopback URL inside the container is
    `rtmp://127.0.0.1:1935/...` (per `app/main.py:245`). We'd have to
    either change the loopback URL to :11935 (couples control plane to
    container network shape) or run nginx on both (useless complication).
  - OBS users expect `rtmp://server:1935/...`. Surfacing :11935 leaks
    an internal abstraction.
- **Drop the cap (don't `setcap`).** Works for v1 (ports >1024), but
  on the day we expose :443 we need a filesystem-state change. Cheap
  to keep; minor pre-investment.

### Rootless-podman footgun

Some container runtimes (`podman --rootless`) strip filesystem
capabilities. Docker does not. If a user hits a bind error under
rootless podman, the workaround is `--cap-add=NET_BIND_SERVICE`. Note
in `docs/ops/deployment.md`.

### Invariants

- **I-G1**: `setcap cap_net_bind_service=+ep /usr/sbin/nginx` runs in
  the runtime stage AFTER the nginx binary is copied in.
- **I-G2**: nginx.conf top-level `user restream;`.
- **I-G3**: Loopback URL `rtmp://127.0.0.1:1935/...` and nginx
  `listen 1935;` must change in lockstep (single source of truth: the
  port number 1935 appears in `app/main.py::_read_ingest_key_now`,
  nginx.conf `listen`, nginx.conf `allow play 127.0.0.1`).
- **I-G4**: A CI test runs the built image and asserts
  `getcap /usr/sbin/nginx` returns `cap_net_bind_service+ep`.
- **I-G5**: ops docs note the rootless-podman `--cap-add` workaround.

---

## H. Healthcheck contract

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD /usr/local/bin/healthcheck.sh
```

```sh
#!/bin/sh
# /usr/local/bin/healthcheck.sh
# Phase 10: HEALTHCHECK target. Probes /livez only — ADR-0011 §"/livez"
# is the unconditional probe. /readyz would fail forever in paste mode
# until a human unlocks, marking the container "unhealthy" perpetually
# and breaking compose's depends_on / service_healthy gate for any
# downstream that legitimately works while locked.
exec curl -fsS --max-time 2 http://127.0.0.1:8000/livez >/dev/null
```

### Parameter justifications

| Param            | Value | Why                                                                                          |
| ---------------- | ----- | -------------------------------------------------------------------------------------------- |
| `--interval`     | 30s   | Liveness is cheap; 5-min MTTD on a wedged HTTP server is fine; faster is noise.              |
| `--timeout`      | 5s    | `/livez` returns in ~100 ms typical. 5s is huge — covers GC pauses or paging events.        |
| `--start-period` | 60s   | Cold boot = schema init + Argon2id derivation (~1.5s typical, 3-5s on Raspberry Pi class) + supervisor startup. 30s is too tight if the host is loaded; 60s is safe headroom. |
| `--retries`      | 3     | Absorbs a single transient miss during s6 respawn. 3×30s = 90s before flip to unhealthy.    |

### Why `/livez` not `/readyz`

`/livez` is **unconditional** (returns 200 if the process is up).
`/readyz` requires the passphrase loaded — in paste mode that's 503
forever until human action. Some orchestrators (compose, Swarm)
interpret prolonged unhealthy as "restart it"; a restart loop while
the operator is mid-paste is unrecoverable.

`/readyz` is for reverse proxies and operators, NOT for Docker
HEALTHCHECK.

### First-boot semantics

- Schema init mid-first-boot: <1s typical, up to 5s on slow disk.
  Until lifespan reaches `yield`, uvicorn doesn't accept connections;
  `curl` returns connection-refused; Docker treats this as `starting`
  (not `unhealthy`) during `--start-period`. Operators with compose
  `depends_on: condition: service_healthy` simply wait.
- Control-plane crash → s6 respawn (~1-2s window) → `curl` fails →
  with `--retries=3 --interval=30s`, three consecutive failures across
  90s absorb the bounce.
- Paste mode → `/livez` returns 200 unconditionally. Container is
  "healthy" before unlock — correct, because the unlock endpoint is
  the entire reason the container is running.

### Rejected alternatives

- **`/readyz` in HEALTHCHECK.** See above — paste mode wedge.
- **`wget --spider`.** `wget` isn't in `debian:12-slim` by default;
  `curl` already is and operators use it for debugging anyway.

### Invariants

- **I-H1**: HEALTHCHECK probes `/livez` exactly. CI grep:
  `grep -E '/(readyz|healthz)' docker/healthcheck.sh` returns zero.
- **I-H2**: `--start-period ≥ 60s`. Argon2id on slow hardware + cold
  schema init can't be tighter.
- **I-H3**: `--retries ≥ 3`.
- **I-H4**: `curl` is present in the runtime image.
- **I-H5**: Healthcheck queries `127.0.0.1` (loopback), never an
  external interface.

---

## I. nginx.conf — log redaction

The ingest key (= stream name) travels in nginx-rtmp's access_log via
the `$name` variable. nginx-rtmp's log_format directive lets us
construct an access-log line that **omits `$name`** by hardcoding the
field to `"REDACTED"`. The http access_log (which only carries the
panel's HTTP traffic, not RTMP) uses `$safe_uri` derived via `map`.

### RTMP access log

```nginx
rtmp {
    log_format compact '$remote_addr [$time_local] $command '
                       'app="$app" name="REDACTED" '
                       'bytes_in=$bytes_received bytes_out=$bytes_sent '
                       'time=$session_time';
    access_log /data/logs/nginx-access.log compact;
    ...
}
```

### HTTP access log (panel + diagnostics only)

```nginx
http {
    map $uri $safe_uri {
        ~^/live/.+  /live/<redacted>;
        default     $uri;
    }
    log_format redacted '$remote_addr - - [$time_local] '
                        '"$request_method $safe_uri $server_protocol" '
                        '$status $body_bytes_sent';
    # access_log off; for the internal stub-status server (§J).
}
```

### error_log

nginx's `error_log` format is **not** customizable per directive.
nginx-rtmp logs publish/connect events at `info` level with `name=...`
included. Mitigation:

```nginx
error_log /data/logs/nginx-error.log warn;
```

At `warn`, the routine `publish: name='<ingest-key>'` lines (which are
`info`) are dropped. Real errors still surface.

### Residual leak surface

- `error_log` lines at `warn`+ may, in edge cases, include `name=...`
  context (e.g., a publish that fails mid-handshake).
- `/proc/<pid>/cmdline` for nginx workers — visible only to the same
  uid inside the container.
- The OBS-side config screen — operator's responsibility.

### Operator-doc requirement

**DEPLOY-DOC-MUST-COVER §I** for `docs/ops/deployment.md`:

> Treat `/data/logs/nginx-error.log` as containing potential secret
> material — in rare error paths the OBS ingest key appears in
> `name='...'` or `tcurl='...'` context fields. Do not paste excerpts
> into public issues without redacting. The panel's "Regenerate ingest
> key" button is the proper recovery if a key has been exposed.

### Invariants

- **I-I1**: Only `redacted` (http) and `compact` (rtmp) log formats
  are used. CI grep: no `combined` or `main` format references.
- **I-I2**: `error_log` level is `warn`. CI grep:
  `grep -E 'error_log\s+.*\s+(notice|info|debug)' docker/nginx.conf`
  returns zero.
- **I-I3**: No `debug_connection` directives in nginx.conf (would
  re-enable info-level logging for selected peers).
- **I-I4**: A Phase 11 container smoke test publishes a stream and
  asserts the ingest key does NOT appear in `nginx-access.log`.

---

## J. nginx.conf — RTMP block essentials

```nginx
user restream;
worker_processes 1;
pid /run/nginx/nginx.pid;
error_log /data/logs/nginx-error.log warn;

events {
    worker_connections 256;
}

http {
    map $uri $safe_uri {
        ~^/live/.+  /live/<redacted>;
        default     $uri;
    }
    log_format redacted '$remote_addr - - [$time_local] '
                        '"$request_method $safe_uri $server_protocol" '
                        '$status $body_bytes_sent';
    access_log off;

    server {
        listen 127.0.0.1:8888;        # diagnostics only — loopback
        location /nginx_status {
            stub_status;
            allow 127.0.0.1;
            deny all;
        }
    }
}

rtmp {
    log_format compact '$remote_addr [$time_local] $command '
                       'app="$app" name="REDACTED" '
                       'bytes_in=$bytes_received bytes_out=$bytes_sent '
                       'time=$session_time';
    access_log /data/logs/nginx-access.log compact;

    server {
        listen 1935;
        chunk_size 4096;

        application live {
            live on;
            record off;

            # Publish gate: ANY publish allowed at nginx layer.
            # Real auth is in the on_publish webhook (checks ingest key).
            allow publish all;

            # Play gate: only loopback ffmpeg workers may pull.
            allow play 127.0.0.1;
            deny play all;

            # nginx-rtmp posts application/x-www-form-urlencoded with
            # name=<stream>&app=live&addr=<peer>&clientid=<n>&flashver=<>&...
            # The FastAPI handler in app/api/internal_rtmp.py reads `name`
            # via Form(alias="name"). Do NOT switch to JSON — handler
            # does not accept application/json.
            #
            # COUPLING: the host:port `http://127.0.0.1:8000` matches
            # `app/config.py::AppSettings.bind_port`. If bind_port ever
            # changes, change here too.
            on_publish      http://127.0.0.1:8000/internal/rtmp/publish;
            on_publish_done http://127.0.0.1:8000/internal/rtmp/publish_done;

            idle_streams        off;
            wait_key            on;
            drop_idle_publisher 10s;
            max_streams         1;       # exactly one publish on `live`
            meta                copy;    # passthrough; no rewrite
            play_restart        off;     # control plane owns reconnect
        }
    }
}
```

### Per-directive rationale

| Directive            | Setting | Why                                                                                       |
| -------------------- | ------- | ----------------------------------------------------------------------------------------- |
| `worker_processes`   | 1       | One ingest + ~6 loopback consumers. Multi-worker adds coordination, no throughput gain.   |
| `chunk_size`         | 4096    | Matches OBS default.                                                                      |
| `wait_key`           | on      | First frame is a keyframe (no garbage on player start).                                  |
| `idle_streams`       | off     | Stream goes away when publisher disconnects (no zombie streams).                          |
| `drop_idle_publisher`| 10s     | Idle publisher socket → drop. Matches supervisor's "OBS idle" semantics.                 |
| `max_streams`        | 1       | Exactly one concurrent publish allowed on `live` application. A 32-slot default (which is what nginx-rtmp ships) is one stuck publisher away from 31 zombies. |
| `meta`               | copy    | Passthrough; nothing rewrites metadata. `meta on` adds latency for no benefit.            |
| `play_restart`       | off     | nginx-side play-restart races our supervisor's circuit-breaker reconnect (ADR-0003).      |
| `allow play 127.0.0.1; deny play all` | both | External plays denied; loopback workers permitted. Threat-model load-bearing. |
| `allow publish all`  | all     | nginx layer ungated; auth lives in on_publish webhook.                                    |

### internal `on_publish` round-trip — exact path verification

Verified at memo-write time:
- `app/api/internal_rtmp.py:41`: `router = APIRouter(prefix="/internal/rtmp", tags=["internal"])`.
- `app/api/internal_rtmp.py:76`: `@router.post("/publish", ...)` → full path `/internal/rtmp/publish`.
- `app/api/internal_rtmp.py:112`: `@router.post("/publish_done", ...)` → full path `/internal/rtmp/publish_done`.
- `app/main.py:169`: `app.include_router(internal_rtmp_router)`.
- `app/config.py:83`: `bind_host="0.0.0.0"` (wildcard, includes 127.0.0.1).
- `app/config.py:84`: `bind_port=8000`.
- `_assert_loopback` accepts `127.0.0.0/8`, `::1`, `::ffff:127.0.0.1`.

Both ends agree. CI integration test (Phase 11) reads `docker/nginx.conf`
and the live FastAPI app's routes, asserts both URLs match exactly.

### Stub-status server (loopback :8888)

Optional but kept — diagnostic value during `nginx looks wedged`
incidents outweighs the tiny attack surface (only reachable via
`docker exec` since :8888 isn't published).

### Invariants

- **I-J1**: `application live {}` block matches the spec above. Changes
  require an ADR-0003 amendment.
- **I-J2**: No `push` directive anywhere in nginx.conf. Fan-out lives
  in the control plane.
- **I-J3**: on_publish URL is `http://127.0.0.1:8000/internal/rtmp/publish`.
  Coupling to `app/config.py::bind_port` documented in a comment.
- **I-J4**: `allow play 127.0.0.1; deny play all;` never relaxed.
- **I-J5**: `max_streams 1` — bumping requires an ADR amendment.
- **I-J6**: `play_restart off` — `play_restart on` would race the
  supervisor's circuit breaker.
- **I-J7**: CI integration test cross-checks router paths vs nginx.conf
  URLs.

---

## K. Entrypoint script responsibilities

Minimal. ≤20 lines including blanks and comments.

```sh
#!/bin/sh
# docker/entrypoint.sh — PID 1 briefly; execs to s6 init.
set -eu
umask 027

# Fail-fast for the single misconfiguration that yields the worst
# operator experience: container starts, control-plane crashes on first
# boot with a pydantic ValidationError buried in JSON-formatted logs.
# Operators read the first line of stderr from `docker logs`; give them
# something useful. Pydantic still runs the same check as defense in
# depth — this is the friendly outer message.
if [ "${RESTREAM_PASSPHRASE_SOURCE:-env}" = "env" ] \
   && [ -z "${RESTREAM_MASTER_PASSPHRASE:-}" ]; then
    echo "ERROR: RESTREAM_PASSPHRASE_SOURCE=env (default) but " \
         "RESTREAM_MASTER_PASSPHRASE is unset." >&2
    echo "       Either set the passphrase, or use " \
         "RESTREAM_PASSPHRASE_SOURCE=paste." >&2
    exit 1
fi

exec /init
```

### What the entrypoint MUST NOT do

- chown `/data` — that's `init-data`'s job (§F).
- mkdir `/data/logs` — same.
- Detect first-boot vs nth-boot.
- Echo env vars (§E).
- Set defaults for env vars. Pydantic defaults are the source of truth.

### Invariants

- **I-K1**: Entrypoint script is ≤25 lines (including blanks +
  comments). If it grows past that, the something-is-wrong instinct
  should fire — every line at this layer is duplicated logic with
  pydantic.
- **I-K2**: Last line is `exec /init`.
- **I-K3**: Exactly one fail-fast gate (env-mode passphrase). Other
  validations stay in pydantic.
- **I-K4**: Entrypoint runs as root (s6 needs root to install signal
  handlers). Privilege drop happens per-service.

---

## L. CI build determinism (forward-looking for Phase 11)

Phase 10 builds amd64 only but the Dockerfile is multi-arch-ready:

- `FROM --platform=$BUILDPLATFORM` on all builder stages.
- Runtime stage omits `--platform` (inherits `$TARGETPLATFORM`).
- `ARG`s declared at top: `BUILDPLATFORM`, `TARGETPLATFORM`,
  `TARGETARCH`, `TARGETOS` — even if unused in the amd64-only build.
- No hardcoded arch strings in `RUN`. s6-overlay tarball uses
  `${TARGETARCH}` interpolation. s6's arch names are kernel-style
  (`x86_64`, `aarch64`) — a small `case` translates from Docker's
  `amd64`/`arm64`.

### Phase 11 risk register (called out now)

- **nginx + nginx-rtmp-module under qemu-user-static for aarch64**:
  builds cleanly; **slow** (5-10 min vs ~30s native). Phase 11
  considers (a) cross-compile via BuildKit sysroot (fiddly for C); (b)
  GHA `ubuntu-24.04-arm` runners (faster, doubles runner cost); (c)
  accept qemu slowness (simplest).
- **ffmpeg from apt** is multi-arch native. No risk.
- **s6-overlay** ships precompiled per-arch tarballs. No risk.
- **Python `cryptography` wheels** for `manylinux2014_aarch64` exist.
  Confirm `uv` picks the wheel and doesn't build from source.

### Invariants

- **I-L1**: Builder `FROM`s use `--platform=$BUILDPLATFORM`. Runtime
  omits the directive.
- **I-L2**: No hardcoded arch strings in `RUN` commands.
- **I-L3**: All four platform `ARG`s declared at top of Dockerfile.
- **I-L4**: Phase 11 plan acknowledges qemu-arm64-nginx build cost
  (~15-min budget per arch).

---

## M. uvicorn config — workers=1 LOAD-BEARING; s6-ready signal hook

### `app/main.py::main()` amendment

Add `workers=1` explicitly (currently implicit — uvicorn default) so
it's grep-able as a tripwire:

```python
# app/main.py::main()
uvicorn.run(
    "app.main:create_app",
    host=settings.bind_host,
    port=settings.bind_port,
    factory=True,
    log_config=None,
    access_log=False,
    workers=1,  # LOAD-BEARING — see Phase 10 design memo §M and ADR-0001
)
```

**Why workers=1 forever**: KeyMaterial is per-process (passphrase
unlock state per worker); supervisor is per-process; in-memory
rate-limit state is per-process. Two workers = two ffmpeg fan-outs of
the same publish, two webhook acks, two WS broadcast paths. ADR-0001
§"Single process" is the authority.

### s6-ready signal — fd 3 write helper

Add to `app/main.py` (lifespan body, just before `yield`):

```python
def _notify_s6_ready() -> None:
    """Phase 10 §C: write to s6-overlay's notification-fd if open.

    s6's `notification-fd` mechanism: the service's `notification-fd`
    file contains `3\\n`; s6 then awaits a write to fd 3 before
    marking the service `ready`. In non-s6 environments (dev runs,
    tests), fd 3 isn't open — the OSError is swallowed and the
    process boots normally.
    """
    try:
        os.write(3, b"\n")
    except OSError:
        pass  # fd 3 not open — dev / test, no notification
```

Called inside the lifespan after `await supervisor.wait_ready(...)`
returns successfully, before `yield`. Regardless of unlock state
(paste mode still signals ready — see §C).

### Invariants

- **I-M1**: `app/main.py::main()` passes `workers=1` explicitly. CI
  grep: `grep -E 'workers\s*=\s*1\b' app/main.py` returns a match.
  Any `workers >1` triggers CI failure.
- **I-M2**: `docker/s6/control-plane/run` invokes `python -m app.main`
  (NOT `uvicorn` directly). Single source of truth for uvicorn config.
- **I-M3**: `_notify_s6_ready()` is called once per lifespan after
  `wait_ready` succeeds and before `yield`. Tests assert it's invoked
  exactly once during normal startup (mock `os.write` and count).

---

## N. structlog output — JSON to stdout; s6-log captures to /data

structlog writes JSON to stdout (per `RESTREAM_LOG_FORMAT=json`
default). s6 captures each longrun's stdout via per-service `log/`
companion services that pipe to `/data/logs/<service>/` as
s6-log-managed rotating directories.

```
/etc/s6-overlay/s6-rc.d/
├── control-plane/
│   ├── log/
│   │   ├── type        ("longrun")
│   │   └── run         (exec s6-setuidgid restream s6-log -bp /data/logs/control-plane)
│   └── ... (other files as in §C)
└── nginx/
    └── ... (no `log/` — nginx writes its own files directly per nginx.conf)
```

This produces:

```
/data/logs/
├── nginx-access.log               (nginx writes directly, redacted format)
├── nginx-error.log                (nginx writes directly, warn level)
├── control-plane/                 (s6-log rotating directory)
│   ├── current
│   └── @<timestamp>.s
└── target-<id>/                   (per-worker, control-plane owns)
    ├── current
    └── @<timestamp>.s
```

### Trade against ADR-0004's "single file" spec

ADR-0004's volume layout spec listed `control-plane.log` and
`target-<id>.log` as single files. s6-log's native idiom is a
directory of rotated files (`current` + `@<timestamp>.s`). Honoring
the ADR literally would require a sidecar that tails s6-log dirs into
a single file — ugly. The ADR amendment is **DEFER-OR-AMEND-3**
below; for Phase 10 the design memo overrides as a layout refinement.

### Why both stdout AND on-disk

- `docker logs restream-plus` shows live structlog JSON
  (operator's first-line debugging).
- `/data/logs/control-plane/current` survives `docker rm` + recreate;
  operator can grep history after recreating from a new image tag.
- 2× storage for log bytes — acceptable for structlog JSON at INFO.

### Invariants

- **I-N1**: structlog writes JSON to stdout in production.
- **I-N2**: nginx writes access/error logs directly to
  `/data/logs/nginx-{access,error}.log` (not via s6-log — nginx owns
  its own rotation via logrotate-style external tools or `nginx -s
  reopen`).
- **I-N3**: control-plane has a `log/` companion longrun service that
  runs `s6-log -bp /data/logs/control-plane`. The log dir lives in
  `/data/logs/control-plane/`, not `/data/logs/control-plane.log`.
- **I-N4**: Per-worker target log directories
  (`/data/logs/target-<id>/`) are created by the supervisor in
  `app/fanout/`, not by s6. s6 has no opinion about them.

---

## O. Boot-failure semantics

| Failure                                       | Outcome                                                                         |
| --------------------------------------------- | ------------------------------------------------------------------------------- |
| Wrong passphrase (env mode)                   | `key_material.unlock()` raises → lifespan fails → uvicorn exits 1 → s6 restarts control-plane → 2s `finish`-sleep avoids tight loop. Logs show the auth error. Operator fixes env. |
| Wrong passphrase (paste mode)                 | Never tried at boot; control-plane stays running, locked. UI shows paste screen; rate-limit (3/15min/IP) prevents brute-force. |
| nginx fails to bind :1935 (host port conflict)| nginx exits 1 → s6 restarts → 2s `finish`-sleep. control-plane KEEPS RUNNING (its dependency is "started", not "stays up"). `/readyz` returns 503 because `healthz_check_nginx=true` and TCP connect fails. Reverse proxies see not-ready and stop routing. **Correct**: honest signaling, no cascade restart. |
| `/data` read-only or full                     | `initialize_schema` raises → lifespan never completes → uvicorn exits 1 → s6 restarts forever. Operator sees schema-init error in logs. No corruption (transactional). |
| `/data` doesn't exist                         | Docker creates the mount point; `init-data` chowns; boot proceeds.              |
| Argon2id >60s on impossibly slow hardware     | Lifespan blocks; uvicorn never binds; Docker healthcheck fails after `start-period+90s`; container marked unhealthy. Rare; documented in ops troubleshooting. |

### s6 dependency: nginx failure does NOT kill control-plane

Intentional. s6 dependencies are "started, not running". Once nginx
started (even if it later crashes), control-plane is allowed to start;
control-plane then runs independently. If nginx crashes, s6 restarts
nginx without touching control-plane. The honest state is communicated
via `/readyz` (`rtmp_ingest.ok=false`); the reverse proxy backs off;
nginx recovers; `/readyz` flips green. No thundering-herd restart.

### Invariants

- **I-O1**: All longruns have a `finish` script with ≥2s sleep
  (avoids tight restart loops).
- **I-O2**: s6 dependency model is "started, not running" — service
  failures don't cascade kills. Documented in `docs/ops/deployment.md`.
- **I-O3**: `healthz_check_nginx` defaults to **True** in production
  (see §Q1 — `app/config.py` default flip). Without this, `/readyz`
  doesn't probe nginx and §O's "nginx down ≠ /readyz red" contract
  breaks silently.
- **I-O4**: No per-service Docker restart policy inside the container.
  Container-level restart is the operator's policy.

---

## P. Reversibility audit

### Easy to change (patch release: semver Z)

- nginx version bump within 1.26.x.
- nginx-rtmp-module SHA bump.
- s6-overlay version bump within 3.x.
- ffmpeg version bump via apt.
- HEALTHCHECK parameter tuning.
- `timeout-finish` values (two files).
- nginx.conf tweaks that don't touch on_publish URL or `:1935`.
- Entrypoint script (within the env-var contract).
- `init-data` oneshot.

### Sticky (load-bearing for v1)

- `debian:12-slim` runtime base (glibc → musl ABI break; ffmpeg
  packaging; nginx-rtmp build flags).
- `/data` as single volume path.
- Loopback `127.0.0.1:1935` everywhere.
- uid 10001 for `restream` user (operators have host directories
  pre-chowned).
- One-image, one-volume topology.
- `arut/nginx-rtmp-module` fork choice.
- Two-process model (adding a third = ADR-0004 amendment + s6 graph
  change).

### Heuristic

If changing X requires operators to do anything beyond `docker pull &&
docker stop && docker run`, X is sticky.

### Invariants

- **I-P1**: Sticky items are listed in `docs/architecture/system-overview.md`
  (or equivalent) as a "load-bearing decisions" section.
- **I-P2**: Patch releases MAY change easy items, MUST NOT change
  sticky items.
- **I-P3**: Minor releases that touch sticky items require an ADR
  amendment AND a migration guide in `docs/ops/upgrade.md` (Phase 12).

---

## Q. Production-default tweaks

### Q1. `app/config.py::AppSettings.healthz_check_nginx` default flips to True

Current default `False` is correct for dev (no nginx sibling). Phase
10 ships the s6 bundle where nginx IS a sibling — the production
default should be `True`. Dev/test code that doesn't want the probe
must explicitly set `RESTREAM_HEALTHZ_CHECK_NGINX=false`.

```python
# app/config.py
healthz_check_nginx: bool = True
"""Whether /readyz probes nginx-rtmp via TCP connect to 127.0.0.1:1935.

Default True for production (Phase 10 ships the s6 bundle that puts
nginx in the same container). Dev runs without a nginx sibling must
set RESTREAM_HEALTHZ_CHECK_NGINX=false to silence the probe."""
```

Tests that don't run nginx will need `RESTREAM_HEALTHZ_CHECK_NGINX=false`
in their `AppSettings(...)` construction or via env. The Phase 6 test
suite already exercises `/readyz` extensively and asserts ok-status
without nginx — those tests need a sweep to set the flag explicitly.

### Q2. `app/main.py::main()` adds `workers=1` (see §M)

Tripwire for the load-bearing single-process invariant.

### Q3. (No other code changes)

Phase 10 is otherwise pure ops surface. No ADR-numbered changes; no
schema bump; no DB migrations.

### Invariants

- **I-Q1**: `app/config.py` default for `healthz_check_nginx` is
  `True`. Tests that don't run nginx pass `healthz_check_nginx=False`
  explicitly. Phase 11 CI runs the test suite without the flag set
  and accepts that nginx-less tests must opt out.
- **I-Q2**: `app/main.py::main()` passes `workers=1` explicitly (I-M1).

---

## R. Acceptance criteria (extends CODE_PLAN.md Phase 10)

The CODE_PLAN.md acceptance list is augmented with:

- `docker build` succeeds locally on a Windows host with BuildKit.
- Final image ≤ 400 MB uncompressed (CI fails ≥ 420 MB per I-B3).
- `docker run -e RESTREAM_MASTER_PASSPHRASE='...'` brings up both
  processes; panel reachable on `:8000`; OBS publishes on `:1935`.
- `docker stop -t 30` triggers graceful shutdown; logs show:
  - lifespan `supervisor.stop(grace=15s)` ran;
  - each worker received SIGTERM and exited within 5s;
  - audit row `session_end` (if a run was active) was persisted;
  - nginx received SIGQUIT and drained.
- `docker ps` shows `healthy` within `start-period + interval` (~90s)
  of `docker run` in env mode.
- `docker ps` shows `starting` (NEVER `unhealthy`) during paste-mode
  locked state.
- `docker inspect --format '{{.Config.Healthcheck.Test}}'` includes
  `/livez` (not `/readyz`).
- `getcap /usr/sbin/nginx` (via `docker exec`) returns
  `cap_net_bind_service+ep`.
- `id restream` (via `docker exec`) returns uid 10001, gid 10001.
- Stream-key contained in `/data/logs/nginx-access.log` after a test
  publish: zero matches.
- Files in `/data/logs/control-plane/` exist after the container has
  been up for >30s.

---

## DEFER-OR-AMEND

Four ADR amendments surfaced by the design pass. None block Phase 10
shipping — but Phase 12 (docs) must address them before declaring v1
done.

### DA-1: ADR-0011 — s6-ready vs `/readyz` contract for paste mode

ADR-0011 §"GET /readyz" lists "Passphrase is loaded" as a gating
check. The parent prompt for Phase 10 said "control-plane signals
ready only after `/readyz` returns 200 once" — which would gate s6
readiness on a human pasting the passphrase. In paste mode that's
never met until human action.

**Amendment:** ADR-0011 gets a new paragraph in §"Implementation notes":

> The s6-overlay `notification-fd` signal is fired by the control plane
> when `supervisor.wait_ready()` succeeds AND uvicorn has bound its
> socket — regardless of KeyMaterial unlock state. The s6 ready signal
> means "the process is past boot and is now answering requests"
> (`/livez` semantics), not "the process is doing its job" (`/readyz`
> semantics). Gating s6 readiness on `/readyz=200` would block s6
> dependents in paste mode forever.

Amendment dated 2026-05-16 (Phase 10).

### DA-2: ADR-0003 — nginx-rtmp fork pick

ADR-0003 §"Open questions" names `arut/nginx-rtmp-module` as "the de
facto maintained one" but doesn't pick a SHA. The `sergey-dryabzhinsky`
fork has more patches but isn't blessed.

**Phase 10 decision:** Pin to `arut/nginx-rtmp-module` at a specific
SHA (recorded in `docker/nginx-rtmp.sha`). This honors ADR-0003's
explicit naming. Switching forks is a future ADR amendment.

No ADR text change for Phase 10. If `arut` proves abandoned in
practice (CVEs unfixed), revisit in a follow-up ADR.

### DA-3: ADR-0004 — `/data/logs/` directory layout

ADR-0004's "Volume layout" spec listed:

```
/data/logs/
├── control-plane.log
└── target-<id>.log
```

s6-log's native idiom is a directory of rotated files. Phase 10 ships:

```
/data/logs/
├── nginx-access.log               (single file — nginx owns rotation)
├── nginx-error.log                (single file)
├── control-plane/                 (s6-log directory)
│   ├── current
│   └── @<timestamp>.s
└── target-<id>/                   (control-plane-owned directory)
    ├── current
    └── @<timestamp>.s
```

**Amendment:** ADR-0004 §"Volume layout" updated to reflect directory
layout for s6-managed services. nginx files remain single files (nginx
manages its own rotation).

### DA-4: ADR-0004 / ADR-0010 — `docker stop -t 30` contract

No ADR explicitly names that operators must use `docker stop -t 30`.
ADR-0003 §"Hard kill" mentions a 15s control-plane grace. Operators
on Docker default 10s see "appliance cuts mid-stream on shutdown" with
no idea why.

**Amendment:** ADR-0004 gets a "Shutdown grace contract" subsection
naming the 30s total budget (20s control-plane + 10s nginx) and the
operator advice. Same advice in README Quick Start callout.

Cross-link from ADR-0010 §"Mode 2" since it interacts with the
unlock-while-locked-mode flow.

### DA-5 (review-driven, Phase 10): pip `--require-hashes` lockfile

Security reviewer H2 (Phase 10 review): `pip install --no-deps -r
docker/requirements.lock` pins versions but not bytewise hashes. A
re-publish-with-same-version-number compromise (or PyPI account
takeover on a small-maintainer transitive like `argon2-cffi-bindings`
or `python-multipart`) ships a tampered wheel without violating the
version pin. ADR-0006's entire encryption story trusts these wheels.

**Phase 10 ships WITHOUT** `--require-hashes`. **Phase 11 CI** must
add the hash-pinning step:

1. Generate the hashed lockfile via `pip-compile --generate-hashes
   pyproject.toml -o docker/requirements.lock` (or equivalent
   `uv pip compile --generate-hashes`).
2. Switch the Dockerfile `pip install` to
   `pip install --no-cache-dir --no-deps --require-hashes -r
   /tmp/requirements.lock`.
3. Wire Renovate / Dependabot to bump the hashed lockfile.

Rationale for deferring: hash generation requires resolving against
PyPI, which the Phase 10 dev host couldn't do. The change is one
line in the Dockerfile + a generated lockfile; the Phase 11 work
naturally absorbs it as part of the CI dep-bump flow (DEFER-OR-AMEND-4
of this memo).

### DA-6 (review-driven, Phase 10): tarball SHA-256 verification

Security reviewer H1 (Phase 10 review): nginx + nginx-rtmp-module +
s6-overlay tarballs were initially fetched over TLS with no
integrity check. **Partially mitigated** in Phase 10 — the Dockerfile
now has `ARG <X>_SHA256=""` slots and conditional `sha256sum -c`
gates that fire when the ARG is populated. When the ARG is empty
(local dev builds), a stderr warning makes the gap loud but doesn't
fail the build.

**Phase 11 CI** must pass `--build-arg NGINX_SHA256=<sha>
--build-arg NGINX_RTMP_MODULE_TARBALL_SHA256=<sha> --build-arg
S6_OVERLAY_NOARCH_SHA256=<sha> --build-arg
S6_OVERLAY_X86_64_SHA256=<sha> --build-arg
S6_OVERLAY_AARCH64_SHA256=<sha>` so every release-tag build is
integrity-verified. Renovate's dep-bump PRs also bump the SHAs.

### DA-7: ADR-0010 — `docker inspect` env exposure

ADR-0010 §"Mode 1 trade given" mentions `/proc/<pid>/environ`,
`docker inspect`, and shell history as exposure surfaces. Confirm the
text already names `docker inspect`; if not, add. Operator advice:
prefer `--env-file /etc/restream.env` with `chmod 600` over `-e` flags
for `RESTREAM_MASTER_PASSPHRASE`. Docker secrets is the long-term answer
but is compose-only and adds operational complexity.

---

## Summary of locked invariants

| ID    | Constraint                                                                                  |
| ----- | ------------------------------------------------------------------------------------------- |
| I-A1  | All `FROM` lines pinned by `@sha256:` digest.                                               |
| I-A2  | nginx + nginx-rtmp-module versions are single-source-of-truth ARGs.                          |
| I-A3  | `uv --frozen`; `uv` NOT in runtime image.                                                    |
| I-A4  | Runtime base = `debian:12-slim` (not alpine).                                                |
| I-A5  | `pip install` in backend-builder uses `--no-cache-dir --no-deps -r requirements.lock`.       |
| I-B1  | Every `apt-get install` uses `--no-install-recommends` + cleanup tail in same RUN.           |
| I-B2  | `.dockerignore` includes all 9 listed entries.                                              |
| I-B3  | CI fails if image > 420 MB uncompressed.                                                    |
| I-B4  | No `__pycache__` in `/opt/venv`.                                                            |
| I-C1  | Exactly four s6 nodes (init-data, nginx, control-plane, user bundle).                        |
| I-C2  | control-plane (transitively) depends on init-data.                                          |
| I-C3  | `notification-fd = 3\n`; signal ready after `supervisor.wait_ready()` regardless of unlock. |
| I-C4  | No APPLICATION code runs as root (nginx master stays root by design; workers + python drop).|
| I-D1  | `control-plane/timeout-finish = 20000` ms; ≥ LIFESPAN_SHUTDOWN_GRACE + 5s slack.            |
| I-D2  | `nginx/down-signal = SIGQUIT`.                                                              |
| I-D3  | `nginx/timeout-finish = 10000` ms.                                                          |
| I-D4  | README + ops docs + compose.yaml document `docker stop -t 30`.                              |
| I-D5  | All longruns have `finish` script with ≥2s sleep.                                           |
| I-E1  | Entrypoint never EMITS the value of `RESTREAM_MASTER_PASSPHRASE` (NAME-only in fail-fast).   |
| I-E2  | Entrypoint ends with `exec /init`.                                                          |
| I-E3  | Entrypoint never uses `set -x`; uses `set -eu`.                                             |
| I-E4  | Mode switching needs no rebuild.                                                            |
| I-F1  | Runtime user = `restream`, uid 10001, gid 10001.                                            |
| I-F2  | `/data` exists in image as `restream:restream` 0750.                                        |
| I-F3  | `init-data` is idempotent.                                                                  |
| I-F4  | No code chowns `/data` to root post-init.                                                   |
| I-F5  | `/app` is `root:root` 0755 (read-only at runtime).                                          |
| I-G1  | `setcap cap_net_bind_service=+ep /usr/sbin/nginx` in runtime stage.                          |
| I-G2  | nginx.conf has `user restream;`.                                                            |
| I-G3  | Loopback `rtmp://127.0.0.1:1935` is single source of truth.                                  |
| I-G4  | CI test asserts `getcap /usr/sbin/nginx` after build.                                        |
| I-G5  | Rootless-podman `--cap-add=NET_BIND_SERVICE` workaround documented.                          |
| I-H1  | HEALTHCHECK probes `/livez`, NOT `/readyz`.                                                 |
| I-H2  | `--start-period ≥ 60s`.                                                                     |
| I-H3  | `--retries ≥ 3`.                                                                            |
| I-H4  | `curl` present in runtime.                                                                  |
| I-H5  | Healthcheck queries `127.0.0.1`.                                                            |
| I-I1  | Only `redacted` / `compact` log formats used; no `combined` / `main`.                       |
| I-I2  | `error_log` level is `warn`.                                                                |
| I-I3  | No `debug_connection` directives.                                                           |
| I-I4  | Phase 11 smoke test asserts ingest key absent from `nginx-access.log`.                      |
| I-J1  | `application live {}` matches §J spec; changes need ADR-0003 amend.                          |
| I-J2  | No `push` directive anywhere.                                                               |
| I-J3  | on_publish URL is `http://127.0.0.1:8000/internal/rtmp/publish`.                            |
| I-J4  | `allow play 127.0.0.1; deny play all;` never relaxed.                                       |
| I-J5  | `max_streams 1`.                                                                            |
| I-J6  | `play_restart off`.                                                                         |
| I-J7  | CI integration test cross-checks router paths vs nginx.conf URLs.                            |
| I-K1  | Entrypoint script ≤25 lines.                                                                |
| I-K2  | Entrypoint ends with `exec /init`.                                                          |
| I-K3  | Exactly one fail-fast gate (env-mode passphrase).                                           |
| I-K4  | Entrypoint runs as root.                                                                    |
| I-L1  | Builder `FROM`s use `--platform=$BUILDPLATFORM`; runtime omits.                              |
| I-L2  | No hardcoded arch strings in `RUN`.                                                         |
| I-L3  | All four platform `ARG`s declared at Dockerfile top.                                        |
| I-L4  | Phase 11 plan acknowledges qemu-arm64-nginx ~15-min budget.                                 |
| I-M1  | `app/main.py::main()` passes `workers=1` explicitly (CI grep tripwire).                      |
| I-M2  | `docker/s6/control-plane/run` invokes `python -m app.main`, not `uvicorn` directly.          |
| I-M3  | `_notify_s6_ready()` called once per lifespan after `wait_ready` success, before `yield`.   |
| I-N1  | structlog writes JSON to stdout in production.                                              |
| I-N2  | nginx writes access/error logs directly to `/data/logs/nginx-{access,error}.log`.            |
| I-N3  | control-plane has `log/` companion s6 service writing to `/data/logs/control-plane/`.        |
| I-N4  | `/data/logs/target-<id>/` is supervisor-owned, not s6-owned.                                |
| I-O1  | All longruns have `finish` script with ≥2s sleep.                                           |
| I-O2  | s6 deps are "started", not "running"; failures don't cascade kills.                          |
| I-O3  | `app/config.py::healthz_check_nginx` defaults to `True` in production.                       |
| I-O4  | No per-service Docker restart policy inside container.                                       |
| I-P1  | Sticky items documented in `docs/architecture/system-overview.md`.                          |
| I-P2  | Patch releases don't touch sticky items.                                                    |
| I-P3  | Minor releases that touch sticky items need migration guide.                                |
| I-Q1  | `healthz_check_nginx` default is `True`; nginx-less tests opt out explicitly.                |
| I-Q2  | `app/main.py::main()` passes `workers=1` (alias of I-M1).                                    |

---

## File map (Phase 10 deliverables)

```
docker/
├── Dockerfile                          # 4-stage multi-arch-ready
├── nginx.conf                          # §J authoritative shape
├── entrypoint.sh                       # §K minimal (≤20 lines)
├── healthcheck.sh                      # §H — /livez only
├── nginx-rtmp.sha                      # pinned nginx-rtmp-module commit
├── requirements.lock                   # uv-exported, --frozen-compatible
└── s6/
    ├── init-data/
    │   ├── type                        # "oneshot"
    │   ├── up                          # mkdir+chown script
    │   └── down                        # empty
    ├── nginx/
    │   ├── type                        # "longrun"
    │   ├── dependencies.d/init-data    # marker file
    │   ├── run                         # port-poll wrapper (signals fd 3)
    │   ├── finish                      # `exec sleep 2` backoff
    │   ├── notification-fd             # "3\n"
    │   ├── down-signal                 # "SIGQUIT"
    │   └── timeout-finish              # "10000"
    ├── control-plane/
    │   ├── type                        # "longrun"
    │   ├── dependencies.d/nginx        # marker
    │   ├── dependencies.d/init-data    # marker
    │   ├── run                         # exec s6-setuidgid restream python -m app.main
    │   ├── finish                      # `exec sleep 2`
    │   ├── notification-fd             # "3\n"
    │   ├── down-signal                 # "SIGTERM"
    │   ├── timeout-finish              # "20000"
    │   └── log/
    │       ├── type                    # "longrun"
    │       └── run                     # exec s6-setuidgid restream s6-log -bp /data/logs/control-plane
    └── user/contents.d/
        ├── init-data                   # marker (empty)
        ├── nginx                       # marker
        └── control-plane               # marker

app/
├── main.py                             # ADD workers=1 + _notify_s6_ready() (§M)
└── config.py                           # FLIP healthz_check_nginx default to True (§Q1)
```

Tests existing in `tests/api/test_health.py` that exercise `/readyz`
without nginx need a sweep to pass `healthz_check_nginx=False` (Q1).
Phase 10 will fix any breakage during implementation.

---

## Closing note

The Phase 10 surface looks deceptively small. The trap is that every
decision here is harder to change after the first release than before.
uid 10001, `/data`, `:1935`, `debian:12-slim`, the s6 graph — these
are the bones of the v1 line.

The 60 invariants above (I-A1 through I-Q2) are the test-suite
specification: each is a unit test, an integration test, or a CI grep
check. The first time we ship a release with a broken invariant, a
homelab user at 3am with a stuck stream and an empty audit log will
tell us.

Headline operational risks ordered by likelihood:

1. **§D timeout misalignment.** Operator omits `-t 30`, loses the audit
   row and worker grace window. Documentation is the only mitigation;
   the technical workaround sacrifices the drain we designed.
2. **§F /data ownership.** Most common first-boot failure for new
   operators. `init-data` is the fix; do not punt to docs.
3. **§M workers=1.** Most likely "improvement" a future contributor
   will make. Tripwire it; comment it loudly; fail CI on violation.
4. **§Q1 healthz_check_nginx default flip.** Tests that previously
   passed will start failing because they don't run nginx. Sweep them
   during implementation.

Everything else follows from the ADRs.
