# ADR-0016: Enhanced RTMP multi-track ingest and track-aware fanout

## Status
Accepted (design-only) — 2026-05-19. Implementation is gated on the
Phase A pilot test passing (see §12). No code from this ADR has been
written yet.

## Supersedes / depends
- Supersedes ADR-0003 §"Open questions" Phase 13 entry (the 2026-05-19
  amendment to ADR-0003 already announced this redirect).
- Depends on ADR-0003 (RTMP ingest + fanout), ADR-0008 (worker
  abstraction), ADR-0012 (MediaMTX dev side-car), ADR-0015 (auto-run-on-
  publish).
- Source-of-truth on the wire format: [docs/research/obs-multitrack-feasibility.md](../research/obs-multitrack-feasibility.md).
- Source-of-truth on platform compatibility: this ADR §6 (table below)
  and the matrix maintained in the research doc.

## Context

[ADR-0003 §"Open questions" amendment 2026-05-19](ADR-0003-rtmp-ingest-and-fanout.md)
established that OBS *can* emit Veovera Enhanced RTMP Y2023 multi-track
tags against a Custom RTMP service via the `custom_config_only` escape
hatch [reference/obs-studio/frontend/utility/MultitrackVideoOutput.cpp:413-420](../../reference/obs-studio/frontend/utility/MultitrackVideoOutput.cpp#L413):
when the operator supplies a `multitrack_video_config_override` JSON in
OBS settings, OBS skips the proprietary
`multitrack_video_configuration_url` POST and emits multi-track FLV
tags directly to the custom ingest URL.

The current architecture spawns one ffmpeg-copy worker per enabled
target, all reading from `rtmp://127.0.0.1:1935/live/<key>`. ffmpeg's
mainline FLV demuxer drops unrecognised `PACKETTYPE_MULTITRACK = 6`
(video) and `AUDIO_PACKETTYPE_MULTITRACK = 5` (audio) tags with a
warning. Today we'd silently corrupt any multi-track stream that hit
us; tomorrow we want to consume it intelligently.

**The value of receiving multi-track is NOT to forward multi-track
downstream.** Per the parallel platform research:

- **Twitch** accepts multi-track only via their proprietary Enhanced
  Broadcasting service flow (POST to `multitrack_video_configuration_url`),
  which is not externally documented and which Restream_Plus cannot
  impersonate from a Custom Service context.
- **YouTube** accepts H.264, HEVC, AV1 over Enhanced RTMP on its
  standard RTMPS ingest, but multi-track tag consumption is unattested.
- **All other platforms** (Kick, VK, Facebook, Trovo, DLive, Rumble,
  Restream.io as a relay) accept only legacy single-track H.264 + AAC
  RTMP.

The value is **OBS encodes the ABR ladder once, Restream_Plus picks
the right rendition per target**. A `{1080p@6Mbps, 720p@4Mbps,
480p@2Mbps}` ladder lets YouTube take 6 Mbps while VK takes 2 Mbps,
all from one OBS push with zero re-encode on our side.

The OBS Custom Service implementation
[reference/obs-studio/plugins/rtmp-services/rtmp-custom.c:4-41](../../reference/obs-studio/plugins/rtmp-services/rtmp-custom.c#L4)
carries only `server / key / use_auth / username / password` — no
codec list, no `multitrack_video_configuration_url`, no constraints on
the wire format. This is what makes the `custom_config_only` escape
hatch necessary in the first place: from OBS's perspective a Custom
Service is a pure RTMP target, and everything else (codec choice,
ladder shape, multi-track topology) is the operator's responsibility,
materialised through the `multitrack_video_config_override` JSON
([reference/obs-studio/frontend/settings/OBSBasicSettings.cpp:5631](../../reference/obs-studio/frontend/settings/OBSBasicSettings.cpp#L5631)).

This ADR scopes the demuxer, distribution layer, supervisor wiring,
per-target assignment, codec compatibility, and lifecycle integration
required for that. The `-c copy` lock (ADR-0008) and circuit-breaker
parameters (ADR-0003) are preserved unchanged.

## Decision (summary)

1. **Ingest tap: Option A (ffmpeg as an FLV tap).** One long-lived
   ffmpeg process per session reads from the ingest loopback URL with
   `-c copy -f flv`, writing raw FLV bytes to a Python TCP listener on
   loopback. Python owns the demuxer.
2. **Demuxer: always-in-path, near-zero overhead for single-track.**
   The `FlvDemuxer` push-parser is always instantiated; on first tag
   it classifies the stream as `single_track` or `multi_track`. In
   `single_track`, the tap is stopped within ~1 s and workers read
   from the loopback URL exactly as today.
3. **Per-track distribution: stdin pipe to per-target ffmpeg workers.**
   No secondary loopback RTMP republish. For multi-track mode, the
   supervisor writes each target's filtered FLV stream directly to its
   ffmpeg worker's stdin.
4. **Track assignment: `settings_json["track_preference"]` per target.**
   No new column. `"auto"` (default) or an integer track ID.
5. **Codec compatibility: fail-loud.** If the selected track's codec is
   not accepted by the target type, the target moves to
   `DISABLED_MISCONFIGURED` with a UI banner. No silent fallback.
6. **Twitch multi-track downstream: explicitly out of scope.** Twitch
   gets one rendition over its standard ingest, same as YouTube/Kick/VK.

## Rationale

### 1. Why ffmpeg-tap over Python RTMP client

Three options for getting raw FLV tags into Python:

| Option | Trade-off |
|---|---|
| **A — ffmpeg tap** (chosen) | One extra ffmpeg per session; reuses already-trusted nginx-rtmp / MediaMTX ingest path; ffmpeg's RTMP client handles OBS dialect, AMF negotiation, chunk-stream state. |
| **B — Python RTMP client** | Eliminates the tap process; but every public Python RTMP library (`pyrtmp`, `python-rtmp`) is unmaintained and predates the Y2023 spec. Hand-rolling the RTMP chunk stream is hundreds of lines of load-bearing state-machine code for zero feature gain. |
| **C — Extend MediaMTX (Go)** | Forks our infra surface massively; we'd own an upstream divergence. |
| **D — Replace MediaMTX with our own RTMP server** | Years of scope creep; rejected. |

Option A's cost is encapsulated in a new `FlvTap` module behind a
`Protocol`. Switching to Option B later is a single-file swap with no
supervisor changes.

**One open uncertainty** (the Phase A pilot resolves it): does ffmpeg's
`-c copy` *input* path drop or forward `PACKETTYPE_MULTITRACK = 6`
tags? Per the research doc §4, nginx-rtmp / MediaMTX pass the bytes
opaquely as RTMP VideoData messages; ffmpeg's RTMP client should
receive them verbatim and write them to FLV output without inspection
during `-c copy`. **If the pilot shows ffmpeg drops them, Option A
fails and we fall back to Option B with ≈2 weeks added scope.**

### 2. Why always-on demuxer with single-track fast path

Two alternatives:

| Approach | Cost |
|---|---|
| **Always-on demuxer** (chosen) | ~1 s extra session-start latency for single-track (tap process to confirm mode); uniform code path. |
| **Conditional: detect multi-track from a side-channel before spawning the tap** | No reliable side-channel — OBS's webhook payload doesn't announce multi-track. The only authoritative source is the FLV byte stream itself. |

The always-on path also handles the fail-safe case: if the tap process
fails to start (e.g., ffmpeg binary missing), the supervisor logs the
failure and proceeds with single-track-assumed behaviour. Multi-track
becomes degraded-to-single-track; the operator sees a warning but the
stream isn't dropped.

### 3. Why stdin-pipe over secondary RTMP republish

For multi-track distribution to per-target workers:

| Approach | Cost |
|---|---|
| **Stdin pipe** (chosen) | `WorkerSpec` gains optional `stdin_provider: asyncio.Queue[bytes \| None]`; zero secondary loopback hops; tighter coupling between demuxer and worker. |
| **Republish each track as a new RTMP path** | Each tag traverses two TCP loopback hops (tap→demuxer→republish→worker); MediaMTX/nginx-rtmp need publish-from-loopback auth changes; nginx-rtmp's `application live { }` block doesn't naturally namespace `live/<key>/track-<id>`. |

Stdin removes two RTMP hops and one round of FLV mux/demux. The cost is
that `WorkerSpec` becomes non-pickleable (contains a `Queue`), but
nothing pickles `WorkerSpec` today.

### 4. Why fail-loud codec compatibility

When a target's selected track is in a codec the target rejects (e.g.,
HEVC track selected for Twitch, which accepts only H.264 on its
standard ingest):

| Choice | Trade-off |
|---|---|
| **(a) silent fallback** to highest-compatible track | Operator's explicit selection is ignored without warning; misconfigurations stream forever to the wrong rendition. |
| **(b) fail-loud** (chosen) — target goes to `DISABLED_MISCONFIGURED` | Operator sees the error and fixes it. Auto-mode is unaffected (auto-select always picks a compatible track). |
| **(c) reject entire run** with banner | Single misconfigured target shouldn't disable the stream. |

Auto-mode forgives: the auto-select algorithm filters by codec first
(see §5), so a sensible default never triggers fail-loud. Fail-loud
only fires on explicit operator misconfiguration.

### 5. Why Twitch multi-track downstream is out of scope

OBS's Twitch service entry declares the Enhanced Broadcasting endpoint
directly:
[reference/obs-studio/plugins/rtmp-services/data/services.json:9-11](../../reference/obs-studio/plugins/rtmp-services/data/services.json#L9):

```json
"multitrack_video_configuration_url": "https://ingest.twitch.tv/api/v3/GetClientConfiguration",
"multitrack_video_name": "Enhanced Broadcasting",
"multitrack_video_learn_more_link": "https://help.twitch.tv/s/article/multiple-encodes",
```

The flow that follows once a non-Custom Twitch service is selected:
1. OBS POSTs hardware/bandwidth telemetry to that URL (see
   [reference/obs-studio/frontend/utility/GoLiveAPI_Network.cpp](../../reference/obs-studio/frontend/utility/GoLiveAPI_Network.cpp)).
2. Twitch returns a session-specific ingest URL + bitrate ladder.
3. OBS pushes a single Enhanced RTMP stream containing all renditions
   to that URL using the FLV layout from
   [reference/obs-studio/plugins/obs-outputs/flv-mux.c:442-501](../../reference/obs-studio/plugins/obs-outputs/flv-mux.c#L442).

The Twitch API is proprietary, the partner agreement gates access,
and the HEVC support is currently NVIDIA RTX-only (per platform
research: NVIDIA blog 2024-09-17; AV1 not yet accepted on either the
TEB or standard ingest paths). A Restream_Plus implementation would
need to:
- POST to `https://ingest.twitch.tv/api/v3/GetClientConfiguration` per
  session — first HTTP-out integration we'd carry beyond auth.
- Match Twitch's expected client identity / telemetry format.
- Maintain compatibility as Twitch evolves the API.

For now: Twitch gets one track over its standard
`rtmp://live-<pop>.twitch.tv/app` ingest (44 POPs declared in
[services.json:12-196](../../reference/obs-studio/plugins/rtmp-services/data/services.json#L12)),
same as everyone else. If Twitch publishes the partner API, this is
an ADR-0017 amendment, not a rearchitect — the `WorkerSpec` already
supports input streams, a Twitch-Enhanced worker is a new worker type.

## 6. Codec compatibility per target

Extends `TargetTypeSpec` ([app/domain/target_types.py:41](../../app/domain/target_types.py#L41)):

```python
@dataclass(frozen=True, slots=True)
class TargetTypeSpec:
    ...
    accepted_video_codecs: frozenset[bytes]  # FourCCs; empty = any
```

Initial values are pinned directly to OBS's
`reference/obs-studio/plugins/rtmp-services/data/services.json` (the
shallow clone at the 2026-05-19 timestamp; pin a commit SHA when the
ADR moves to Implemented status):

| Target type | Accepted video codecs | OBS source pin |
|---|---|---|
| `twitch` | `{b"avc1"}` | [services.json:204-206](../../reference/obs-studio/plugins/rtmp-services/data/services.json#L204): `"supported video codecs": ["h264"]`. The Multitrack/TEB endpoint (services.json:9) is the only place HEVC ingest can happen on Twitch today, and we don't speak that protocol (§5). |
| `youtube` | `{b"avc1", b"hvc1", b"av01"}` | [services.json:244-248](../../reference/obs-studio/plugins/rtmp-services/data/services.json#L244): `"supported video codecs": ["h264", "hevc", "av1"]` on the RTMPS ingest. YouTube auto-detects via E-RTMP FourCC at `rtmps://a.rtmps.youtube.com:443/live2` (services.json:252). |
| `kick` | `{b"avc1"}` | Kick is absent from `services.json` (Custom URL only); platform research confirms AWS IVS upstream rejects non-H.264. |
| `vk_live` | `{b"avc1"}` | VK is absent from `services.json`; community guides confirm H.264-only over RTMP/RTMPS. |
| `custom` | `frozenset()` | OBS rtmp-custom service [rtmp-custom.c:4-41](../../reference/obs-studio/plugins/rtmp-services/rtmp-custom.c#L4) carries no codec metadata; operator owns endpoint compatibility. Empty set = accept any. |

Audio is `b"mp4a"` (AAC) for all platforms; no platform in scope
accepts Opus or any other codec over E-RTMP today. Treated as a hard
constant rather than a per-target field.

These values are compile-time constants in `TARGET_TYPE_SPECS`;
changing them requires a code change and an ADR amendment, not a
runtime setting. **AV1 received from OBS can only be forwarded to
YouTube.** If an operator's OBS ladder is all-AV1 and they enable any
target other than YouTube, the non-YouTube targets fail codec-compat
and move to `DISABLED_MISCONFIGURED` — operator must adjust the OBS
ladder or disable those targets.

**Why we don't transcode HEVC/AV1 → H.264 on our side.** Restream.io
does exactly this (per platform research) and it's their value-add.
For us it would mean dragging x264 into the supervisor, which is a
Rule №2 smell (a heavy codec dependency carried for the edge case
where an operator configures incompatible codecs). The honest UX is:
gate at config-time, surface the conflict, let the operator fix it in
OBS. We stay `-c copy` everywhere.

## 7. Demuxer module spec

**New module:** [app/fanout/flv_demuxer.py](../../app/fanout/flv_demuxer.py) (to be created).

Push-parser with O(1) per-tag classification:

```python
@dataclass(frozen=True, slots=True)
class FlvTag:
    tag_type: int          # 8 = audio, 9 = video, 18 = script
    timestamp_ms: int
    stream_id: int         # always 0 in FLV
    track_id: int          # 0 for single-track and legacy tags
    codec_fourcc: bytes    # b"avc1" / b"hvc1" / b"av01" / b"mp4a", or empty
    packet_type: int       # E-RTMP PACKETTYPE_*, or -1 for legacy
    is_multitrack: bool
    payload: bytes
    is_sequence_header: bool

class FlvDemuxer:
    def feed(self, chunk: bytes) -> list[FlvTag]: ...
    @property
    def stream_mode(self) -> Literal["unknown", "single_track", "multi_track"]: ...
```

**Multi-track detection** on each tag (per [flv-mux.c:42-66](../../reference/obs-studio/plugins/obs-outputs/flv-mux.c#L42)):
- Video tag: byte 0 = `VideoTagHeader`. `(byte0 & 0x80)` set → E-RTMP.
  If packet-type bits `[0..3] == 6` → `PACKETTYPE_MULTITRACK`; parse
  byte 1 (`MultitrackType`), bytes 2–5 (`CodecFourCC`), byte 6 (`TrackID`).
- Audio tag: `(byte0 & 0xF0) == 0x90` → `AUDIO_HEADER_EX`. If
  `(byte0 & 0x0F) == 5` → `AUDIO_PACKETTYPE_MULTITRACK`; same layout.
- Legacy tags (no high-nibble set) → `track_id = 0`, `is_multitrack = False`.

`stream_mode` is sticky: first multi-track tag → permanent
`multi_track`. First legacy/single-track tag with no prior multi-track →
permanent `single_track`. Mode flip mid-session = warning log +
continue.

**Sequence headers** (`PACKETTYPE_SEQ_START = 0`): demuxer marks
`is_sequence_header = True` and forwards the payload. Resolution
extraction is delegated to a separate `SequenceHeaderParser` invoked
once per track per session (parses SPS for AVC, VPS+SPS for HEVC,
OBU sequence header for AV1).

**`onMetaData` AMF** (tag type 18): forwarded with `track_id = 0`,
`is_multitrack = False`. Used as cross-check for resolution/framerate
against SPS-derived values; SPS wins on conflict.

**Unknown codec FourCCs**: log structured warning `flv_unknown_fourcc`,
forward the tag with raw FourCC bytes. The distributor handles
codec-compat enforcement; the demuxer never drops a tag for codec
reasons.

**Malformed tags**: log `flv_malformed_tag` with byte offset, skip,
hunt for next FLV signature. Skip-not-fatal: a single corrupt tag
does not terminate the session.

**Back-pressure**: per-track `asyncio.Queue(maxsize=256)` per target.
Slow target's queue fills, oldest tag dropped with counter increment
(same drop-oldest semantics as `EventBus.dropped_total`). Wedged target
does not stop others.

## 8. Per-track loopback distribution

Per §3 above: stdin-pipe to per-target ffmpeg workers.

**Worker argv change** ([app/fanout/ffmpeg_args.py:70](../../app/fanout/ffmpeg_args.py#L70)):

When `spec.stdin_provider is not None`:
```
ffmpeg
  -hide_banner -loglevel error -nostats -progress pipe:1
  -rtmp_buffer 100 -fflags +nobuffer -flags low_delay
  -f flv -i pipe:0
  -c copy
  -f flv -flvflags no_duration_filesize -flush_packets 1
  <output_url>
```

When `spec.stdin_provider is None` (single-track mode, current
behaviour): unchanged — `-i rtmp://127.0.0.1:1935/live/<key>`.

**Worker spec change** ([app/fanout/worker.py:42](../../app/fanout/worker.py#L42)):
```python
@dataclass(frozen=True, slots=True)
class WorkerSpec:
    ...
    stdin_provider: asyncio.Queue[bytes | None] | None = None
```

**Worker process change** ([app/fanout/ffmpeg_worker.py](../../app/fanout/ffmpeg_worker.py)):
when `stdin_provider` is set, `_run_one_spawn` spawns a concurrent
stdin-writer task that dequeues `bytes` from `stdin_provider` and
writes to `process.stdin`. A `None` sentinel signals EOF → close
stdin, ffmpeg flushes and exits.

**Single-track zero-overhead invariant:** in single-track mode, the
tap is stopped within ~1 s of session start and workers pull from the
loopback URL exactly as today. The demuxer's queues are never
attached to a worker's stdin. The single-track code path executes
zero new ffmpeg argv tokens and zero new asyncio tasks beyond what
exists today.

## 9. Track manifest

**New bus event** ([app/fanout/event_bus.py:117](../../app/fanout/event_bus.py#L117)):

```python
@dataclass(frozen=True, slots=True)
class TrackInfo:
    track_id: int
    codec_fourcc: bytes
    media_type: Literal["video", "audio"]
    width: int | None
    height: int | None
    framerate: float | None
    observed_kbps: float | None

@dataclass(frozen=True, slots=True)
class TrackManifest:
    session_id: str
    tracks: tuple[TrackInfo, ...]
    detected_at: datetime

@dataclass(frozen=True, slots=True)
class TrackManifestEvent:
    manifest: TrackManifest
    is_update: bool  # False on first arrival; True on bitrate-update re-fire
```

Added to `BusEvent.payload` tagged union. Existing WS broadcaster
fans all `BusEvent` types to clients; no broadcaster change required.

**`observed_kbps`** starts `None` (sequence headers arrive before
data). After 3 s of data, the demuxer publishes
`TrackManifestEvent(is_update=True)`. Targets with `"auto"` assignment
re-evaluate at that point.

**Supervisor wiring** ([app/fanout/supervisor.py](../../app/fanout/supervisor.py),
`_start_run_locked`):
1. Start tap process.
2. Connect demuxer socket.
3. Decrypt credentials (existing).
4. Await `stream_mode` determination (bounded by `STREAM_MODE_TIMEOUT = 5s`).
5. If `single_track`: spawn workers with `input_url = loopback_url`,
   stop tap. Behaviour identical to today.
6. If `multi_track`: await `TrackManifest` (bounded by
   `TRACK_MANIFEST_TIMEOUT = 5s`); spawn workers with `stdin_provider`
   set per track assignment.
7. Transition `STARTING → ARMED` (existing); `ARMED → LIVE` follows
   per ADR-0015 drain.

**Manifest timeout failure**: if neither timeout fires correctly, the
session enters `ERROR` (same path as credential-decrypt failure today,
[supervisor.py:447-473](../../app/fanout/supervisor.py#L447)).

## 10. Per-target track assignment

**Storage**: `TargetDTO.settings["track_preference"]` —
existing `settings_json` TEXT column. No new column, no migration.

```json
{ "track_preference": "auto" }   // default; auto-select by codec+kbps
{ "track_preference": 1 }        // explicit track ID
```

**Existing rows** default to `"auto"` via `settings.get("track_preference", "auto")`.

**Auto-selection algorithm** (resolves at manifest arrival, re-resolves
on bitrate update):
1. Filter `TrackManifest.tracks` to video tracks (audio tracks follow
   their video track's assignment).
2. Filter to tracks in `target_type.accepted_video_codecs`.
3. Sort by `observed_kbps` descending (None last); tie-break by
   `track_id` descending (OBS encodes descending bitrate by track ID
   convention).
4. Pick first. Log
   `track_auto_resolved, target_id=..., resolved_track_id=..., reason=kbps|fallback`.

**Explicit-track-not-present failure**: if `track_preference = 1` and
the manifest lacks track 1, the target goes to
`DISABLED_MISCONFIGURED` with reason `"configured track 1 not present
in manifest"`. Operator must change the assignment.

**UI surface** (Phase D):
- `TrackSelector` component (`web/src/components/TrackSelector.tsx`):
  shows "Auto (requires multi-track OBS)" when no manifest is
  available; shows select with one entry per video track when manifest
  is available (e.g., `"Track 1 — 1080p60, ~6 000 kbps (avc1)"`).
- Embedded in `TargetDetails` for target types where multi-track is
  meaningful (everything except `custom`, where the operator owns
  endpoint compatibility).

## 11. Default behaviour for single-track (the 95% case)

The `FlvDemuxerSession` is instantiated for every session. For
single-track streams:

1. First tag arrives (legacy FLV, no `0x80` / `0x90` high nibble).
2. `stream_mode` becomes `"single_track"` immediately.
3. Tap is stopped (≤ 1 s after session start).
4. All target workers spawn with `input_url = loopback_url` and
   `stdin_provider = None` — current code path.
5. Workers pull from the loopback exactly as today.

**Latency cost for single-track:** zero. Workers start immediately
(they don't wait for the tap); the tap's `single_track` confirmation
happens concurrently in the background and stops the tap when done.
If the tap fails to start at all, the supervisor logs a warning and
proceeds with single-track assumption — fail-safe.

**CPU cost for single-track:** one extra ffmpeg process for ≤ 1
second at session start. Negligible.

**Latency cost for multi-track:** worker spawn waits for the
`TrackManifest` (bounded at 5 s; typically arrives within the first
few keyframes, ~100 ms at 30 fps). Acceptable.

## 12. Phasing

### Phase A — Pilot (≤ 1 day, MANDATORY prerequisite)

**No application code changes.** Standalone script + manual operator run.

Files to create:
- [scripts/pilot_ertmp.py](../../scripts/pilot_ertmp.py) — reads a
  pre-captured FLV file (or live ffmpeg-tap stdout) and reports tag
  classification. Default approach: operator runs `ffmpeg -i
  rtmp://127.0.0.1:1935/live/<key> -c copy -f flv capture.flv` while
  OBS publishes with `multitrack_video_config_override` set; pilot
  script then parses `capture.flv` offline.

**Pilot exit gates:**
- [ ] OBS with `custom_config_only` + a test JSON sends
  `PACKETTYPE_MULTITRACK = 6` tags (verified by pilot script log).
- [ ] ffmpeg `-c copy` tap forwards the multi-track bytes intact
  (verified by `capture.flv` containing E-RTMP multi-track tags after
  ffmpeg processed them).
- [ ] Tag layout matches [flv-mux.c:442-501](../../reference/obs-studio/plugins/obs-outputs/flv-mux.c#L442)
  exactly (codec FourCC at correct offset, track ID byte present).
- [ ] Commit the captured `tests/fixtures/multitrack_pilot.flv` (if
  not too large; otherwise pin sha256 + size in CI fixture spec).
- [ ] Identify the JSON schema for `multitrack_video_config_override`
  empirically (the schema is undocumented; either capture a real
  Twitch session and extract it from OBS logs, or iterate by
  experiment).

**If item 2 fails** (ffmpeg drops multi-track tags during `-c copy`):
escalate to Option B (Python RTMP client) in Phase B; add ~2 weeks
scope.

### Phase B — Demuxer module (~1 week)

Files to create:
- [app/fanout/flv_demuxer.py](../../app/fanout/flv_demuxer.py)
- [app/fanout/flv_tap.py](../../app/fanout/flv_tap.py)
- [app/fanout/sequence_header_parser.py](../../app/fanout/sequence_header_parser.py)
- [tests/fanout/test_flv_demuxer.py](../../tests/fanout/test_flv_demuxer.py)

Files to modify:
- [app/fanout/event_bus.py](../../app/fanout/event_bus.py) —
  add `TrackInfo`, `TrackManifest`, `TrackManifestEvent` to
  `BusEvent.payload` union.
- [app/fanout/worker.py](../../app/fanout/worker.py) —
  add `stdin_provider` to `WorkerSpec`.
- [app/fanout/ffmpeg_args.py](../../app/fanout/ffmpeg_args.py) —
  `pipe:0` branch in `build_argv`.
- [app/domain/target_types.py](../../app/domain/target_types.py) —
  `accepted_video_codecs` per target type with the table from §6.

**Exit gate:** mypy clean, demuxer unit tests pass against synthetic
fixtures + the pilot fixture if available. No supervisor changes yet.

### Phase C — Supervisor wiring (~1 week)

Files to modify:
- [app/fanout/supervisor.py](../../app/fanout/supervisor.py) —
  `_start_run_locked` instantiates `FlvTap` and waits for `stream_mode`;
  `_stop_run_locked` tears down tap and sends EOF sentinels;
  `_spawn_workers_for_target` resolves track assignment and codec
  compat from the manifest.
- [app/fanout/ffmpeg_worker.py](../../app/fanout/ffmpeg_worker.py) —
  stdin-writer task in `_run_one_spawn` when `spec.stdin_provider` set.
- [app/repositories/_converters.py](../../app/repositories/_converters.py) —
  pass `track_preference` through to domain `Target`.

Files to create:
- [tests/fanout/test_supervisor_multitrack.py](../../tests/fanout/test_supervisor_multitrack.py) —
  integration tests with `FakeTap` + `FakeStdinWorker`.

**Smallest end-to-end working slice:** Twitch (H.264, auto-select
track 0) + YouTube (auto-select highest-bitrate H.264 or HEVC). OBS
sends two renditions via the pilot's known-good JSON. Twitch worker
pushes track 0 to its standard ingest; YouTube worker pushes the
highest-compatible track to its RTMPS ingest. Both succeed. This
proves the architecture without exercising all targets or all codecs.

**Exit gate:** smallest slice green; mypy 72/72 still passes; existing
780 backend tests still pass; new multitrack tests pass.

### Phase D — UI (~3 days)

Files to create:
- [web/src/components/TrackSelector.tsx](../../web/src/components/TrackSelector.tsx)
- [web/src/hooks/useTrackManifest.ts](../../web/src/hooks/useTrackManifest.ts)

Files to modify:
- [web/src/components/TargetDetails.tsx](../../web/src/components/TargetDetails.tsx) —
  embed `TrackSelector` when applicable.
- [app/api/targets.py](../../app/api/targets.py) — validate
  `track_preference` in `PATCH /api/targets/<id>`.
- [app/domain/target_types.py](../../app/domain/target_types.py) —
  expose `accepted_video_codecs` in API response.

**Exit gate:** vitest 37+/37+ green; typecheck clean; build ≤ 256 KB
gzipped.

### Phase E — Hardening (~3 days)

- Full SPS/VPS/OBU resolution + framerate extraction in
  `sequence_header_parser.py`.
- Hypothesis fuzz harness for `FlvDemuxer.feed` (random byte sequences
  must never raise).
- Long-session memory profile (1000+ tags, no growth).
- Codec-compat test coverage for all target types.
- Track-disappeared-mid-session integration test.
- Demuxer-crash recovery integration test.

**Exit gate:** all hardening tests green; ADR-0016 marked
"Implemented" with the implementation commit SHA.

## Consequences

### Positive
- Operators with multi-track OBS get optimal renditions per platform
  without transcoding on Restream_Plus's side.
- Single-track operators see zero behaviour change (within 1 s of
  session start; tap is stopped after mode detection).
- `-c copy` lock (ADR-0008), circuit-breaker config (ADR-0003), and
  `MAX_LIFETIME_SPAWN_ATTEMPTS = 1000` (slice 10) all preserved.
- Phased delivery — each phase is independently testable and
  releasable.

### Negative
- Phase A pilot is a hard prerequisite. If ffmpeg drops multi-track
  tags during `-c copy`, Option A fails and Phase B grows by ~2 weeks
  (Python RTMP client).
- The OBS `multitrack_video_config_override` JSON schema is
  undocumented — operator must reverse-engineer it from Twitch session
  captures or iterate by experiment. This is operator setup, not code
  scope, but it's friction.
- Single-track sessions gain ≤ 1 s of startup latency for tap mode
  detection. Bounded by `STREAM_MODE_TIMEOUT = 5 s`.
- `WorkerSpec` becomes non-pickleable (contains an `asyncio.Queue`).
  Nothing pickles it today, but future serialisation paths need to
  exclude `stdin_provider`.
- AV1 received from OBS can only be forwarded to YouTube. Operators
  configuring AV1 ladders must enable only YouTube; other targets will
  fail-loud (this is the intended behaviour per §6 but worth surfacing
  in UI copy in Phase D).

### Neutral
- The OBS-side `multitrack_video_config_override` is a per-operator
  setup step. Restream_Plus has no way to push a default to OBS — we
  can ship a sample JSON in [docs/research/obs-multitrack-feasibility.md](../research/obs-multitrack-feasibility.md)
  or `docs/ops/obs-multitrack-setup.md` but the operator has to paste
  it into their own OBS settings.

## Cross-references

- ADR-0003 — RTMP ingest + fanout (this ADR inherits the loopback URL
  and circuit-breaker model).
- ADR-0008 — Worker abstraction (this ADR adds `stdin_provider` to
  `WorkerSpec` without changing the `-c copy` lock).
- ADR-0012 — MediaMTX dev side-car (this ADR uses the same ingest
  surface; no MediaMTX changes required).
- ADR-0015 — Auto-run-on-publish (this ADR integrates with
  `_start_run_locked` and `_stop_run_locked`).
- [docs/research/obs-multitrack-feasibility.md](../research/obs-multitrack-feasibility.md) —
  authoritative on OBS wire format and the `custom_config_only`
  escape hatch.
- [reference/obs-studio/plugins/obs-outputs/flv-mux.c:42-66](../../reference/obs-studio/plugins/obs-outputs/flv-mux.c#L42) —
  authoritative on E-RTMP constants and tag layout (subject to OBS
  upstream changes; pin commit SHA in Phase B unit test fixtures).
- Veovera Enhanced RTMP v2 specification (Y2026 stable, released
  2026-01-31): https://veovera.org/docs/enhanced/enhanced-rtmp-v2.html.

## Implementation status

This ADR is **design-only**. No code from any phase has been written.
Implementation begins with Phase A on operator authorisation.
