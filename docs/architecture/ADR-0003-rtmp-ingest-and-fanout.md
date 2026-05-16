# ADR-0003: RTMP ingest + fan-out — nginx-rtmp + per-target ffmpeg supervisor

## Status
Accepted — 2026-05-15

## Context

The product needs to:

1. Receive a single RTMP push from OBS.
2. Fan it out to ≤ ~6 RTMP/RTMPS targets (Twitch, YouTube, Kick, VK Live,
   plus user-defined).
3. Give the user per-target control: enable/disable, status (live? errored?
   reconnecting?), bitrate / dropped frames, last error.
4. Survive a target disconnecting (other targets unaffected). Reconnect
   that target with bounded backoff. Do not crash the others.

There are several patterns. The choice is load-bearing because it shapes
the entire control plane.

### Pattern A — `nginx-rtmp` with built-in `push` directives

`nginx-rtmp` natively supports `push rtmp://target.example/app/key;` in
its application block. Add one push line per target, reload nginx, done.

- ✅ Zero glue code.
- ❌ Per-target on/off requires editing nginx.conf and reloading nginx —
  not safe to do every time the user flips a Settings toggle.
- ❌ Per-target status is essentially invisible. The XML stat endpoint
  reports aggregate pushed bytes but not "did this target reject our
  connection 30s ago".
- ❌ Reconnect logic is opaque — `push` just retries forever on the same
  socket. No backoff, no inspectability.
- ❌ Cannot pass per-target ffmpeg options (e.g., `-flvflags no_duration_
  filesize` for VK quirks, see ADR-0003-research-notes).

This is the "quick" choice. Per RULE №1 it is rejected.

### Pattern B — nginx-rtmp `exec_push` (spawn ffmpeg per target)

`nginx-rtmp` can spawn an external program (ffmpeg) per push via
`exec_push`. ffmpeg pulls from local RTMP and pushes to the target.

- ✅ Per-target ffmpeg = per-target options, status, restart.
- ⚠️ nginx owns the ffmpeg child process — when we want to stop a target,
  we can't directly signal "this specific child". Workaround: write a
  per-target stub script that watches a flag file. Crutch territory.
- ⚠️ Reconfiguration still requires nginx reload (config changes when
  the user adds a new target).

This pattern is workable but the ownership inversion (nginx supervising
ffmpeg, but the control plane wanting to be the source of truth) creates
coordination overhead that compounds.

### Pattern C — nginx-rtmp ingest only, control-plane spawns ffmpeg workers

The chosen pattern.

- nginx-rtmp listens on `:1935`, accepts exactly one publish at
  `rtmp://*/live/<ingest-key>`.
- `on_publish` fires a webhook (`POST /internal/rtmp/publish`) to the
  control plane. Control plane validates the key and either approves or
  rejects (returning 4xx kills the connection at nginx).
- The control plane, in its own process, supervises one ffmpeg per
  enabled target. Each ffmpeg pulls from
  `rtmp://127.0.0.1:1935/live/<ingest-key>` (loopback — no per-target
  output config in nginx) and pushes to its remote target.
- The control plane is the single source of truth: which targets exist,
  which are enabled, when to start/stop, when to back off.

### Pattern D — Custom RTMP server in Python/Go (no nginx-rtmp)

There are pure-Python RTMP libraries (`pyrtmp`) and Go ones. Tempting
because we'd own the whole stack.

- ❌ Production-grade RTMP handling (chunk stream demux, AAC bitstream
  edge cases, edge-case OBS behavior) is years of work. nginx-rtmp has
  had a decade of streamers stress-testing it.
- ❌ For VK Live specifically, OBS's RTMP dialect interacts in subtle ways
  with non-nginx ingest. We rely on nginx-rtmp being the boring,
  battle-tested choice.

### Pattern E — MediaMTX / SRS (Simple Realtime Server)

Modern alternatives.

- MediaMTX (Go) has a clean HTTP API for runtime configuration, supports
  many protocols beyond RTMP.
- SRS (C++) is feature-rich and has WebRTC bridges.

Both are credible, and either could substitute nginx-rtmp. We pick
nginx-rtmp because:
- The `on_publish` / `on_publish_done` HTTP webhook contract is exactly
  what we need (auth handshake + lifecycle signal) and is stable.
- The smaller surface area = fewer features we don't use that could
  break or be misconfigured.
- nginx-rtmp's process model is well-understood under s6.

MediaMTX or SRS are the obvious **revisit triggers** if nginx-rtmp ever
proves to have a fatal quirk for one of the target platforms.

## Decision

**Pattern C: nginx-rtmp for ingest only; the FastAPI control plane is the
sole supervisor of per-target Workers. The Worker abstraction (ADR-0008)
separates the lifecycle/state-machine concern from the concrete
implementation (`FFmpegWorker` for v1).**

### State naming (per F# SA-A2)

Three distinct state types, kept at distinct layers and never collapsed:

- `RunState` — system-wide. Values: `offline | starting | armed | live |
  stopping | error`. Triggered by START/STOP and by ingest lifecycle.
- `TargetEnabled` — per-target policy. Values: `enabled | disabled`. Set
  in Settings or via the Dashboard per-tile toggle.
- `WorkerState` — runtime, per `Worker` instance. Values: `idle |
  starting | running | reconnecting | failed_open | stopping`. Owned by
  the supervisor.

### RunState transitions

```
                         ┌────────────────────────────┐
                         ▼                            │
   offline ──▶ starting ──▶ armed ──▶ live ──▶ stopping ──▶ offline
                              │         │                      ▲
                              │         └──────────────────────┘
                              └─────── (OBS connects) ─────────┘
                                                       
   * → error  (on unrecoverable failure; user must clear it)
```

- `armed` (NEW per F# UX-B.armed): START succeeded, workers spawned and
  waiting for the loopback RTMP pull to deliver frames. OBS is not
  publishing yet. UI shows "Waiting for OBS to start streaming".
- `live`: OBS is publishing and at least one Target has at least one
  Worker in `running`.
- On OBS disconnect: stays in `live` for the configured idle timeout
  (default 60 s, ADR's original spec), then transitions back to `armed`.
  If OBS does not resume within a second, longer idle timeout (default
  300 s), the run transitions to `stopping`.

### WorkerState transitions (per Worker, owned by Supervisor)

```
   idle ──▶ starting ──▶ running ──▶ reconnecting ──▶ running
                              │           │
                              │           └──▶ failed_open  (circuit-breaker tripped)
                              ▼
                          stopping ──▶ idle
```

- `failed_open` (NEW per F# BA-D3 circuit breaker): after N (configurable,
  default 30) failed reconnect attempts within a 5-minute window, the
  Worker stops auto-retrying and surfaces a hard error in the UI. The
  user can click "Retry now" to push it back through `starting`. This
  prevents us from acting as a bad actor toward the target platform.
- `reconnecting` carries the attempt counter; reset after 60 s of
  healthy `running`.

### Worker invocation (FFmpegWorker v1)

The concrete argv for `FFmpegWorker` lives in ADR-0008 §"Server-computed
args". Key points:

- Args are *locked*: server-computed from a typed struct. No user
  override for codec / encode / container flags (F# SA-E.locked-args).
- Buffer flags spec'd: `-rtmp_buffer 100 -fflags +nobuffer -flags
  low_delay -flush_packets 1` (F# SA-E.buf). Each Worker has its own
  loopback pull session; one slow target does not affect others.
- Stderr passes through the `RedactionSink` (ADR-0008 §"log-redaction
  sink") before being written to disk or surfaced to the UI.

### Target-level health aggregation (per F# BA-5)

A `Target` may own 1..N Workers (see ADR-0008 §"WorkerSet"). The
Dashboard subscribes to *target-level* health, computed per-type:

| Target type | Aggregation                                                   |
| ----------- | ------------------------------------------------------------- |
| `twitch`    | `worker[primary].healthy` (1 worker)                          |
| `youtube`   | `primary.healthy OR backup.healthy` (when backup configured)  |
| `kick`      | `worker[primary].healthy`                                     |
| `vk_live`   | `worker[primary].healthy`                                     |
| `custom`    | `worker[primary].healthy`                                     |

The supervisor publishes worker-level events on its internal queue; an
aggregator transforms those into target-level events for the WebSocket.

### nginx-rtmp config (essentials)

```nginx
rtmp {
    # Access log goes to a sink that strips the URL path under /live/.
    # The redaction is enforced in the nginx access_log format; see
    # docker/nginx.conf for the live config. Per F# BA-1, we accept the
    # stable ingest key but tighten EVERY log channel.
    access_log /data/logs/nginx-access.log redacted;
    error_log  /data/logs/nginx-error.log warn;

    server {
        listen 1935;
        chunk_size 4096;

        application live {
            live on;
            record off;
            allow publish all;          # gated by on_publish below
            deny play all;              # nothing pulls from outside —
                                        # only the loopback ffmpegs do,
                                        # and they go via 127.0.0.1 which
                                        # we allow with a separate rule
            allow play 127.0.0.1;

            on_publish http://127.0.0.1:8000/internal/rtmp/publish;
            on_publish_done http://127.0.0.1:8000/internal/rtmp/publish_done;

            # No `push` lines. Fan-out is owned by the control plane.
            idle_streams off;
            wait_key on;                # don't deliver until a keyframe

            # Per F# SA-E.dup: reject a second concurrent publish on the
            # same name. nginx-rtmp has the `drop_idle_publisher` and
            # bookkeeping for active publishes; the control plane also
            # rejects in `on_publish` if it sees an active session for
            # the same ingest key.
            drop_idle_publisher 10s;
        }
    }
}
```

### Ingest credential threat model (per F# BA-1)

The ingest credential is the stream name (`<ingest-key>`). It is stable
by design — OBS users save it once and re-use it. Backend Architect
review flagged that it travels through several channels:

1. **nginx access log** — mitigated by the `redacted` log format that
   strips the URL path under `/live/`.
2. **nginx error log** — mitigated by configuring nginx to omit URL
   paths in error context (error log format limitation; see the
   `docker/nginx.conf` comments).
3. **Worker's `/proc/<pid>/cmdline`** — visible to the same uid only.
   The whole container runs as the `restream` uid; only the control
   plane and its child workers run under it. Accepted as in-scope-of-
   container-trust.
4. **LAN traffic from OBS to server** — unencrypted RTMP. Mitigation
   path is **RTMPS-from-OBS**: nginx-rtmp's stock build does not
   accept RTMPS; the `nginx-rtmp-module` fork plus a TLS-terminating
   stream proxy does. Deferred to a future ADR; documented as the
   recommended path for users who push from a different host.

The *rejected* alternative was rotating the stream name with an HMAC
token (Backend Architect §B.1). The new value would travel in the
same channels, breaking OBS UX without changing the underlying
threat. Log discipline + RTMPS is the proper fix.

### Reconnect policy + circuit breaker (F# BA-D3)

When a Worker's underlying process exits non-zero while the RunState is
`live` or `armed`:

- Compute backoff: `min(60s, base * 2^attempt)` with `base = 1s`,
  jittered ±20%.
- After backoff, respawn. Increment attempt counter.
- Reset attempt counter to zero after 60 s of healthy `running`.
- After **N consecutive failed attempts within a window** (default
  `N=30, window=5min`), transition the Worker to `failed_open`. Stop
  retrying. Surface a hard error in the UI: "Target `<label>` is
  unhealthy after 30 failed attempts; click Retry to resume."
- Reset from `failed_open` is user-initiated only — a "Retry now"
  click on the target tile clears the counter and re-enters
  `starting`.

This prevents us from hammering a target platform indefinitely and
earning rate-limits or IP bans that would affect other targets sharing
the egress.

### Concurrency, backpressure, and event loop split (F# BA-2)

The control plane runs **two top-level asyncio `TaskGroup`s**, with a
bounded queue between them:

- **Supervisor group** owns:
  - Worker subprocess lifecycle (start/stop, signal handling).
  - `-progress` parsing per Worker.
  - Aggregation into target-level events.
  - Heartbeat for ADR-0011 readyz probe.
- **API group** owns:
  - HTTP handlers.
  - WebSocket connections.
  - Auth, rate-limit, session.

Communication is one-way: Supervisor → API via a single
`asyncio.Queue(maxsize=10000)`. The API group drains the queue and
fans out to WebSocket clients. If the queue fills (drowning consumers),
the Supervisor drops the oldest events with a counter that surfaces in
`/readyz` — *the Supervisor never blocks on a slow consumer.*

This is the explicit fix for the "single event loop" backpressure risk.
The two groups still share the asyncio event loop (one process, one
loop), but the bounded queue is the contract that prevents one group
from starving the other.

### Hard kill (F# BA-2b shutdown hook)

On SIGTERM to the control plane (s6 shutdown, or container stop), the
shutdown choreography is wrapped in `try`/`finally` semantics so that
exit on either normal or exceptional paths is identical:

1. Mark RunState = `stopping`. Broadcast to WebSocket.
2. Enter an `asyncio.TaskGroup` whose body issues `worker.stop(grace=
   timedelta(seconds=5))` for every Worker concurrently.
3. The TaskGroup's `__aexit__` blocks until all stops complete or the
   10 s outer timeout fires.
4. After timeout: SIGKILL any stragglers, log loudly.
5. Persist session-end audit row. The audit write is `synchronous`
   (ADR-0007).
6. Close DB, stop HTTP server, exit.

PID 1's death does **not** by itself reap our children — Linux does
not guarantee parent-death signals. We do it explicitly.

## Consequences

**Easier:**
- Clean ownership: control plane owns workers; nginx is a dumb ingest.
- Per-target on/off is "spawn or kill one Worker" — milliseconds, no
  reload, no config-file rewrite, no race with other targets.
- Status, errors, backoff are observable in the control plane process
  because the control plane *is* the supervisor.
- ffmpeg is the most battle-tested RTMP push client in existence.
  Platform-specific quirks (VK Live's flv flag, YouTube's keyframe
  expectations) are configurable per Worker type (ADR-0008).
- Two task groups + bounded queue: the API never starves the
  Supervisor and vice versa. Drop counter surfaces a real problem
  rather than silent jitter.
- Circuit breaker prevents the appliance from becoming a "noisy
  neighbor" to target platforms during sustained outages.

**Harder:**
- The control plane has a non-trivial state machine: `RunState` ×
  `WorkerState` × per-target enabled × N targets. Worth a dedicated
  module with explicit unit tests on every transition.
- Two TaskGroups with backpressure-aware IPC is one more thing to
  reason about than a single global event loop. The contract (bounded
  queue, drop-oldest, surface in `/readyz`) is small enough to fit on
  one page.
- Logging: ffmpeg stderr is verbose AND can leak keys. We pipe it
  through `RedactionSink` (ADR-0008) into per-target rotating log
  files at `/data/logs/target-<id>.log`, and surface only a
  structured summary on the WebSocket.
- Ingest key handling: discussed in the threat-model subsection
  above. Logs are redacted; LAN encryption is via future RTMPS.

**Rejected alternatives:** See Patterns A, B, D, E above.

## Open questions / revisit triggers

- If a target platform requires features ffmpeg cannot do passthrough
  (e.g., per-target bitrate caps), this ADR is unaffected — we add a
  transcoding tier in front of the per-target workers in a separate ADR.
- If nginx-rtmp ever becomes unmaintained (its upstream has been quiet
  for years; the `arut/nginx-rtmp-module` fork is the de facto
  maintained one), we migrate to MediaMTX. The webhook contract is
  similar enough that the control plane changes are local.
