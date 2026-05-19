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

**Code reconciliation (slice 9 — SA-F5 ADR closure).** The numeric
defaults above are codified in the immutable `CircuitBreakerPolicy`
dataclass at `app/domain/circuit_breaker.py`:

```python
@dataclass(frozen=True, slots=True)
class CircuitBreakerPolicy:
    max_failures: int = 30                          # ADR-0003 default N
    window: timedelta = timedelta(minutes=5)        # ADR-0003 default window
    healthy_reset_after: timedelta = timedelta(seconds=60)  # 60 s healthy → clear counter
```

`CircuitBreaker.should_open(now)` returns
`BreakerVerdict.OPEN` once `failure_count_in_window(now) >= max_failures`
(strict `>=`, not `>`); the supervisor then routes the Worker to
`WorkerState.FAILED_OPEN`. `record_healthy(now)` clears the
failure deque on the first observation that crosses the
`healthy_reset_after` boundary (not on every healthy heartbeat —
the timer arms on the first call after a failure and fires once).
`reset()` is the user-initiated "Retry now" path: clears failures
AND the healthy timer, then the next `record_failure` rearms.

The defaults are dataclass fields, so a future test or experimental
deployment can pass a custom `CircuitBreakerPolicy(max_failures=10,
window=timedelta(minutes=2))` to the `CircuitBreaker` constructor;
production wiring uses `field(default_factory=CircuitBreakerPolicy)`
and never overrides. Changing the production defaults is an ADR
amendment (this section), NOT a runtime knob — the breaker exists
to keep us from being a noisy neighbor; making it operator-tunable
would let one operator's experimental "more aggressive retry"
choice push our IP onto a platform-side ban list shared by every
deployment.

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
- The `EventBus` is single-drainer by design (one consumer = `ws_broadcaster`).
  When/if a `/metrics` endpoint lands (see ADR-0011 §"Open questions"),
  this becomes the real blocker — a second drainer would race the
  broadcaster on the deque. The cheap fix is to introduce a fan-in
  layer (the bus drains once, broadcasts to N internal subscribers),
  or to push `HostStatsEvent` to both via a tee. Capture the
  ADR-amend at that point so the next architect doesn't rediscover
  the single-drainer invariant. (Added 2026-05-17 after the live-stats
  feature review surfaced it.)

- **Phase 13 multi-track ingest exploration (2026-05-18) — DEFERRED until
  upstream catches up.** The operator asked for Enhanced RTMP multi-track
  ingest (OBS sends N renditions; we pass multi-track through to Twitch
  and extract the fattest track for YouTube/Kick/VK). A full architecture
  pass found TWO independent upstream blockers that no amount of code
  on our side can route around without crutches the operator explicitly
  rejected (Rule №1):

    1. **OBS gates the Multitrack Video toggle on
       `multitrack_video_configuration_url` in the service entry's
       `services.json`** (commit "Don't attempt multitrack without
       config url", Aug 2025). The bundled "Custom" service has no such
       field. Pointing OBS at our `rtmp://host:1935/live/<key>` via
       Custom Service today means **the Multitrack Video toggle is
       greyed out** — the operator literally cannot send multi-track
       to us. Workarounds (ship a services.json fragment for manual
       install; submit Restream_Plus upstream to OBS) are either fragile
       or slow. See feature requests
       https://ideas.obsproject.com/posts/2684/ +
       https://ideas.obsproject.com/posts/2743/.

    2. **ffmpeg stable cannot emit Enhanced RTMP multi-track FLV
       output.** Multi-track FLV encode lives in BtbN's `enhanced-flv`
       branch, not in mainline 7.x. Even if (1) were solved, we could
       not forward multi-track to Twitch with the Debian-packaged
       ffmpeg we run today; we'd have to vendor nightlies.

  **Revisit triggers**: (a) OBS adds Custom Service support for
  `multitrack_video_configuration_url` (track
  https://ideas.obsproject.com/posts/2684/ + the
  https://github.com/obsproject/obs-studio/pull/10845 schema thread);
  (b) ffmpeg upstream merges the BtbN `enhanced-flv` multitrack-FLV
  output path into mainline. Both blockers need to clear before we
  re-open this — partial work (e.g., just swapping nginx-rtmp → MediaMTX
  to be "ready") was rejected as premature scope.

  **Implication for ADR-0008**: `-c copy` lock stays intact — there
  is no transcoding tier on the roadmap. The operator's RTX 5060 is
  expected to do all encoding inside OBS; this server is and remains
  a pure passthrough relay. (Confirmed by the operator: "не нужны мне
  костыли никакие и компромиссы".)

  Full research report (multi-track ingest server candidates, MediaMTX
  multitrack PR history, OBS source citations, Twitch Enhanced
  Broadcasting protocol details) was generated in-session and lives
  only in the conversation transcript; key URLs above + the file
  references in `docs/SESSION_HANDOFF.md` §"Phase 13 multi-track
  exploration" are the durable artifacts.

  **Empirical confirmation (2026-05-18 same day)**: the operator
  built the dev-only MediaMTX sidecar (see `app/api/internal_mtx.py`
  + `dev/mediamtx.yml` + `start.py`) and tried OBS → `:1935` → fanout
  with Multitrack Video toggled ON in the OBS Custom Service. OBS
  failed to start ("Output failure / No URL available for current
  service"). With Multitrack Video toggled OFF, OBS sent a standard
  single-track RTMP publish; MediaMTX accepted, our auth webhook
  matched the ingest key, supervisor notified, ffmpeg workers fanned
  out cleanly. This is exactly the blocker-1 behaviour the research
  predicted, observed end-to-end. Conclusion stands: re-open Phase
  13 only when OBS Custom Service supports `multitrack_video_configuration_url`
  AND ffmpeg mainline merges the BtbN `enhanced-flv` branch.

  **Amendment (2026-05-19) — original conclusion superseded.** The
  OBS source was cloned into `reference/obs-studio/` (gitignored) and
  read directly. The deferral was based on a black-box test of the
  *default* Custom-Service flow; it missed the `custom_config_only`
  escape hatch [reference/obs-studio/frontend/utility/MultitrackVideoOutput.cpp:413-420](../../reference/obs-studio/frontend/utility/MultitrackVideoOutput.cpp#L413):

  ```cpp
  const bool custom_config_only =
      auto_config_url.isEmpty()
      && custom_config.has_value()
      && strcmp(obs_service_get_id(service), "rtmp_custom") == 0;
  if (!custom_config_only) {
      // remote POST to multitrack_video_configuration_url
  }
  ```

  When the operator pastes a configuration JSON into the OBS
  `multitrack_video_config_override` field (the field is rendered only
  when `IsCustomService() && enableMultitrackVideo`, per
  [OBSBasicSettings.cpp:5631](../../reference/obs-studio/frontend/settings/OBSBasicSettings.cpp#L5631)),
  OBS bypasses the remote configuration call entirely and emits
  Veovera Enhanced RTMP Y2023 multitrack tags directly to the custom
  ingest URL. The 2026-05-18 "Output failure" was caused by the
  operator not supplying the override JSON — `FailedToStartStream.NoConfig`
  at [MultitrackVideoOutput.cpp:449](../../reference/obs-studio/frontend/utility/MultitrackVideoOutput.cpp#L449).

  Blocker-1 (the original "OBS gates Multitrack on
  `multitrack_video_configuration_url`") is therefore **only a default-
  path constraint, not an absolute gate** — a fact the 2026-05-18
  research missed. Blocker-2 (ffmpeg mainline lacking enhanced-flv
  multitrack output) remains relevant only if we wanted to *emit*
  multitrack downstream; for *ingest*, nginx-rtmp / MediaMTX pass the
  binary E-RTMP tags through opaquely. The work for Restream_Plus is
  a server-side FLV demuxer that recognises
  `PACKETTYPE_MULTITRACK = 6` (video) and `AUDIO_PACKETTYPE_MULTITRACK = 5`
  (audio) tags from [reference/obs-studio/plugins/obs-outputs/flv-mux.c:42-66](../../reference/obs-studio/plugins/obs-outputs/flv-mux.c#L42),
  plus a track-aware fanout layer.

  **Full research write-up + code references** are durably captured in
  [docs/research/obs-multitrack-feasibility.md](../research/obs-multitrack-feasibility.md).
  That document is the single source of truth on this topic going
  forward; the 2026-05-18 bullets above remain in place as a historical
  artifact (showing what was concluded before the source-level
  investigation) but **MUST NOT be cited as the current position**.

  **Architecture decision** — superseded into
  [ADR-0016: Enhanced RTMP multi-track ingest and track-aware fanout](ADR-0016-enhanced-rtmp-multitrack-ingest.md)
  (Accepted design-only, 2026-05-19). ADR-0016 spells out the demuxer
  module, per-target track assignment, codec compatibility table
  pinned to OBS's `services.json`, fail-loud policy, and phased
  delivery plan (A pilot → B demuxer → C supervisor wiring → D UI →
  E hardening). Implementation is gated on the Phase A pilot test —
  no Phase B+ code begins until OBS is empirically confirmed to emit
  `PACKETTYPE_MULTITRACK = 6` tags through the `custom_config_only`
  path and ffmpeg's `-c copy` tap forwards them intact.
