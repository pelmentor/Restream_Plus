"""Worker Protocol + WorkerSpec + WorkerEvent payloads (ADR-0008).

A `Worker` is one outbound push of the local RTMP feed to one URL.
v1 has one implementation: `FFmpegWorker`. The Protocol exists so the
supervisor's tests substitute a `FakeWorker` without spawning a real
subprocess, and so a future `WHIPWorker` (WebRTC ingest) drops in
without supervisor changes.

The event channel is a single-subscriber `anyio` memory object stream;
the supervisor consumes it in a per-worker pump task. Multi-subscriber
fan-out is the supervisor's job (it owns the `EventBus`).

Snapshot semantics: the Worker maintains its own internal state and
exposes a `snapshot()` method that builds a fresh `WorkerSnapshot` on
demand. The supervisor reads it on every drained event and pumps into
`Target.ingest_snapshot()`. State lives next to the thing that mutates
it; the supervisor stays a pure orchestrator.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal, Protocol

from app.domain.target_types import TargetType
from app.domain.worker_state import WorkerRole, WorkerSnapshot, WorkerState

WorkerEventKind = Literal["state", "progress", "error", "exited", "stalled"]


@dataclass(frozen=True, slots=True)
class WorkerId:
    """Identity of a Worker. Per-process unique by (target_id, role)."""

    target_id: str
    role: WorkerRole


@dataclass(frozen=True, slots=True)
class WorkerSpec:
    """Everything the Worker needs to run, computed by the supervisor.

    `output_url` already embeds the plaintext stream key. The
    supervisor MUST register the plaintext key in `CredentialRegistry`
    BEFORE constructing the spec so any incidental log site is
    redacted; `RedactionSink` strips bytes that flow through stderr.

    `stdin_provider` is the multi-track distribution channel (ADR-0016
    Phase C). When `None` (the single-track default — every worker today),
    ffmpeg reads from `input_url` directly. When populated, the
    supervisor pumps per-target FLV bytes into the Queue and ffmpeg is
    invoked with `-f flv -i pipe:0`; a `None` sentinel on the Queue
    signals "no more bytes" (graceful EOF to the worker). The Queue
    itself is mutable, but the dataclass field is frozen — only the
    supervisor mutates the Queue contents.
    """

    worker_id: WorkerId
    target_type: TargetType
    input_url: str
    output_url: str
    stdin_provider: asyncio.Queue[bytes | None] | None = field(default=None)


@dataclass(frozen=True, slots=True)
class WorkerStateEvent:
    """A WorkerState transition. `cause` is human-readable and arrives
    already redacted (the Worker constructs it from internal state, not
    from raw stderr)."""

    new_state: WorkerState
    previous_state: WorkerState
    cause: str


@dataclass(frozen=True, slots=True)
class WorkerProgressEvent:
    """One ffmpeg progress frame, surfaced through to the bus."""

    frame: int
    fps: float
    bitrate_kbps: float
    out_time_us: int
    dup_frames: int
    drop_frames: int
    speed: float
    progress: Literal["continue", "end"]


@dataclass(frozen=True, slots=True)
class WorkerErrorEvent:
    """A non-fatal or fatal error. `message` is REDACTED (passed through
    `RedactionSink` if it originated from stderr)."""

    message: str
    fatal: bool


@dataclass(frozen=True, slots=True)
class WorkerExitEvent:
    """The child process exited. `returncode is None` means the process
    was killed by a signal that the OS reports separately."""

    returncode: int | None
    signal: int | None
    duration: timedelta


@dataclass(frozen=True, slots=True)
class WorkerStalledEvent:
    """No progress frame for `no_progress_for` — distinct from `error`
    because Phase 6 may render it differently."""

    no_progress_for: timedelta


@dataclass(frozen=True, slots=True)
class WorkerEvent:
    """Outer envelope carried over the worker's event channel.

    `monotonic_ns` is for stall arithmetic the supervisor does locally;
    `at` is wall-clock for log/audit/UI. They diverge under clock skew;
    do not mix them.
    """

    worker_id: WorkerId
    at: datetime
    monotonic_ns: int
    kind: WorkerEventKind
    payload: (
        WorkerStateEvent
        | WorkerProgressEvent
        | WorkerErrorEvent
        | WorkerExitEvent
        | WorkerStalledEvent
    )


class Worker(Protocol):
    """Protocol every fan-out worker implementation satisfies.

    Lifecycle:
      worker = FFmpegWorker(spec=..., process_spawner=..., ...)
      await worker.start()                # transitions IDLE → STARTING
      async for event in worker.events(): # supervisor pumps events
          ...
      await worker.stop(grace=...)        # graceful SIGTERM → SIGKILL
    """

    @property
    def id(self) -> WorkerId: ...
    @property
    def spec(self) -> WorkerSpec: ...

    def snapshot(self) -> WorkerSnapshot: ...
    def events(self) -> AsyncIterator[WorkerEvent]: ...

    async def start(self) -> None: ...
    async def stop(self, grace: timedelta) -> None: ...
    async def user_reset(self) -> None: ...
