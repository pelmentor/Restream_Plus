# ADR-0008: Worker abstraction & log-redaction sink

## Status
Accepted — 2026-05-15

## Context

ADR-0003 originally specified `ffmpeg ... -c copy ...` as the fan-out
implementation, with the control plane supervising the processes. The
Software Architect review (F# SA-D2) flagged that this conflates two
concerns:

1. *Worker* — the entity that pushes the local RTMP feed to one remote
   endpoint, with a lifecycle (`starting → running → reconnecting → …`),
   a status stream, and a graceful-shutdown contract.
2. *FFmpegWorker* — a concrete way to be a Worker, with a specific binary,
   argv shape, and stdout-progress format.

The same review surfaced two adjacent issues:

- **Worker-set per Target** (F# BA-5): YouTube backup-ingest is 1 Target
  → 2 Workers. VK is 1 Target → 1 Worker with a per-session credential
  hook. The state machine in ADR-0003 was 1:1, with no aggregation rule.
- **Log redaction invariant** (F# SA-A leak): ffmpeg writes the output
  URL with the embedded stream key to stderr on error. Per-target log
  files at `/data/logs/target-<id>.log` must never persist plaintext
  keys, by construction — not by hope.

This ADR factors the abstraction out. ADR-0003 is amended to reference
it.

## Decision

### The `Worker` protocol

```python
class WorkerSpec(BaseModel):
    target_id: TargetId
    worker_role: WorkerRole       # "primary" | "backup" (YouTube case)
    url: HttpUrl                  # already includes the credential
    args: list[str]               # SERVER-COMPUTED, frozen — see below
    env: dict[str, str]

class WorkerEvent(BaseModel):
    worker_id: WorkerId
    at: AwareDatetime
    kind: Literal["state", "progress", "error", "exited"]
    data: WorkerStateEvent | WorkerProgressEvent | WorkerErrorEvent | WorkerExitEvent

class Worker(Protocol):
    """One outbound push of the local RTMP feed to one URL."""

    @property
    def id(self) -> WorkerId: ...
    @property
    def spec(self) -> WorkerSpec: ...
    @property
    def state(self) -> WorkerState: ...   # see ADR-0003 amended

    async def start(self) -> None: ...
    async def stop(self, grace: timedelta) -> None: ...
    def events(self) -> AsyncIterator[WorkerEvent]: ...
```

v1 has one implementation: `FFmpegWorker`. Future implementations
(`WHIPWorker` for WebRTC ingest, etc.) implement the same protocol.

### The `WorkerSet` aggregate per Target

A `Target` owns a `WorkerSet`:

```python
class WorkerSet:
    target_id: TargetId
    workers: dict[WorkerRole, Worker]   # at least one; YouTube has two

    def target_health(self) -> TargetHealth: ...
```

The aggregation rule from worker health → target health is type-specific:

| Target type | Aggregation rule                                              |
| ----------- | ------------------------------------------------------------- |
| `twitch`    | `worker[primary].healthy` (single worker)                     |
| `youtube`   | If backup configured: `primary OR backup`. Else: primary only. |
| `kick`      | `worker[primary].healthy`                                     |
| `vk_live`   | `worker[primary].healthy`                                     |
| `custom`    | `worker[primary].healthy`                                     |

Adding YouTube backup is a Settings toggle that spawns/teardown's the
backup worker; the Target lifecycle does not change.

### Server-computed args (F# SA-E.locked-args)

`WorkerSpec.args` is computed server-side from a typed `FFmpegArgsPlan`
struct. No user-supplied string ever lands in `args` except the values
of `url` (the full destination URL, including credential). Specifically
forbidden in user input: codec flags, encode flags, bitrate flags,
container flags. The user controls **which target** and **whether
enabled**; the application controls **how to push**.

```python
# Concrete spec, not user-tunable. Locked. Per F# SA-E.buf.
FFMPEG_BASE_ARGS = [
    "ffmpeg",
    "-hide_banner",
    "-loglevel", "error",
    "-nostats",
    "-progress", "pipe:1",
    "-rtmp_buffer", "100",          # ms — small input buffer, low latency
    "-fflags", "+nobuffer",         # don't pre-buffer beyond what's needed
    "-flags", "low_delay",
    "-i", LOOPBACK_RTMP_URL,        # rtmp://127.0.0.1:1935/live/<ingest>
    "-c", "copy",                   # PASSTHROUGH — see ADR-0003
    "-f", "flv",
    "-flvflags", "no_duration_filesize",
    "-flush_packets", "1",
    OUT_URL,                        # computed from target + credential
]
```

These flags collectively say: pull as fast as the stream comes in, never
re-encode, push every packet to the output as it arrives, mark the FLV
container as a live stream. They are the same for every target type in
v1. Target-type-specific overrides (if VK needs `-vk_thing 1`, etc.)
land here in `FFMPEG_BASE_ARGS_BY_TYPE`, never in user config.

### Progress parsing

`ffmpeg -progress pipe:1` emits key=value lines on stdout, terminated by
`progress=continue` or `progress=end` every interval (default 1 s with
the flags above). The parser is a small state machine that:

1. Reads lines bounded by a 4 KiB buffer.
2. Accumulates key=value pairs until a `progress=` line ends the frame.
3. Emits one `WorkerProgressEvent` per frame.

If stdout is silent for > `STALL_TIMEOUT` (default 5 s), the supervisor
treats it as a stall and transitions the worker to `error`.

### The log-redaction sink (F# SA-A leak)

Every Worker's stderr (and any other byte stream that might contain a
URL with an embedded key) passes through a `RedactionSink` before being
written to **any** sink: in Phase 5 that means a per-worker in-memory
ring buffer (cap ~200 lines, surfaced to the WS slide-out) and a
`structlog` event per line. **Disk writes to
`/data/logs/target-<id>.log` with rotation are deferred to Phase 9
(container)** — the redaction contract is identical, only the set of
destinations expands. The `RedactionSink` is composed with the
process-global `CredentialRegistry` from `app/logging_setup.py`:
the supervisor registers the plaintext stream key in the registry
**before** spawning the worker (so any structured log event anywhere
in the process is also redacted via the structlog processor), and
unregisters on worker exit. The two layers cover different attack
surfaces — raw stderr bytes vs. structured logging — and are NOT
redundant.

```python
class RedactionSink:
    """Strips credentials from byte streams.

    The redactor knows the URL of the worker it's wrapped around. It
    matches that URL's path-component-with-key against incoming bytes
    and replaces the key segment with '<REDACTED>'. Additionally, a
    generic pattern matches `rtmp(s)://[^/]+/[^/]+/[A-Za-z0-9_-]{8,}`
    and redacts the trailing segment.

    Tested with each platform's URL shapes plus property-based tests
    that randomly insert any stored credential value into a fake
    stderr stream and assert it does not appear in the sink output.
    """

    def __init__(self, *, worker_url: str): ...
    def write(self, data: bytes) -> bytes: ...
```

The invariant — *no per-target log file ever contains a key in
plaintext* — is asserted in tests with property-based generation. CI
fails if the invariant breaks.

### Crash on supervisor exit (F# BA-2b)

The supervisor's main task is wrapped in an `asyncio.TaskGroup` such
that, on cancellation or unhandled exception, the `finally` block
issues:

```python
async with asyncio.TaskGroup() as tg:
    for worker in self._workers.values():
        tg.create_task(worker.stop(grace=timedelta(seconds=5)))
```

The teardown runs even when the control plane is being shut down by
SIGTERM (s6 sends it on container stop). PID 1's death does not by
itself reap our children — we must do it explicitly. This is the
"asyncio shutdown hook" the review demanded.

## Consequences

**Easier:**
- Adding a new worker type (WHIPWorker for WebRTC, etc.) is a new
  module that implements the `Worker` protocol. No changes to the
  supervisor, state machine, or WebSocket layer.
- YouTube backup-ingest fits naturally as a `worker_role="backup"` —
  no special case in the lifecycle code.
- Log redaction has a single chokepoint that's tested explicitly.

**Harder:**
- One layer of indirection between Supervisor and the ffmpeg process.
  Worth it; the alternative is to bake ffmpeg-isms into the
  supervisor, which we'd have to undo on the first non-ffmpeg target.

**Rejected alternatives:**

- **Keep ffmpeg-specific supervisor; add a separate "WHIP supervisor"
  later** — duplicates state-machine logic. Reuse via the abstraction
  is the cheaper path overall.
- **Allow users to override ffmpeg args** — F# SA-E.locked-args.
  Convenience for power users, but it would resurrect the
  no-transcoding rule as a user-violatable convention. Rejected.

## Open questions / revisit triggers

- If a new target type genuinely needs custom ffmpeg args beyond what
  `FFMPEG_BASE_ARGS_BY_TYPE` covers, the args plan grows a per-type
  hook. Not a redesign.
- If stderr redaction proves too aggressive (e.g., legitimate diagnostic
  text matches the URL-with-key pattern), the redactor adds an
  allowlist for known-safe substrings — but the default is
  redact-then-confirm, never the inverse.

- **2026-05-18: `-c copy` lock survives the Phase 13 multi-track
  exploration.** A full architecture pass on accepting Enhanced RTMP
  multi-track ingest + forwarding multi-track to Twitch was deferred
  pending two upstream unblocks (OBS Custom Service
  `multitrack_video_configuration_url` support + ffmpeg mainline
  merging enhanced-flv multitrack encode). See
  ADR-0003 §"Open questions" entry of the same date for the full
  reasoning. Net for this ADR: there is no transcoding tier on the
  roadmap, and the server-computed `FFMPEG_BASE_ARGS` chain
  ending in `-c copy` remains the single source of truth. When the
  upstream blockers clear and Phase 13 reopens, the *track-selection*
  computation (`-map 0:v:<chosen-idx>` based on a per-target
  `max_height` setting) is the only addition this ADR would absorb;
  the `-c copy` lock itself does not loosen.
