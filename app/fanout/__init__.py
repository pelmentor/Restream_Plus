"""Fan-out layer — supervisor + per-target FFmpeg workers + redaction.

Per ADR-0003 (control-plane is the supervisor) and ADR-0008 (Worker
protocol + RedactionSink). Phase 5 ships:

- The pure-CPU primitives (`RedactionSink`, `ProgressParser`, `compute_backoff`).
- The subprocess abstraction (`ProcessSpawner`, `AsyncioProcessSpawner`).
- The `Worker` Protocol + `WorkerSpec` + `WorkerEvent` payloads.
- The `EventBus` (single-producer, single-drainer, drop-oldest).
- The `FFmpegWorker` (v1 concrete worker; lifecycle + stall + breaker).
- The `Supervisor` (run-state machine, target reconciliation, two-TaskGroup
  shutdown choreography).

This package may import from `app.domain` (the domain layer is pure and
stable), `app.repositories` (DTOs only), `app.crypto` (AEAD decrypt of
stream keys), `app.auth.key_material` (KEK access), and
`app.logging_setup` (CredentialRegistry). It must NOT import from
`app.api` — that direction is reserved for Phase 6 wiring; an AST-walk
test (`tests/fanout/test_no_inward_imports.py`) enforces it.
"""

from app.fanout.backoff import (
    BACKOFF_BASE,
    BACKOFF_CAP,
    JITTER_PCT,
    compute_backoff,
)
from app.fanout.event_bus import (
    EVENT_BUS_CAPACITY,
    BusEvent,
    DropAlertEvent,
    EventBus,
    RunStateChangedEvent,
    TargetSnapshotEvent,
)
from app.fanout.ffmpeg_args import FFMPEG_BASE_ARGS, build_argv
from app.fanout.ffmpeg_urls import compose_out_url
from app.fanout.ffmpeg_worker import FFmpegWorker
from app.fanout.process import (
    AsyncioProcessSpawner,
    ProcessSpawner,
    SpawnedProcess,
)
from app.fanout.progress_parser import (
    ProgressFrame,
    ProgressParseError,
    ProgressParser,
)
from app.fanout.redaction import (
    MAX_LINE_LEN,
    REDACTION_PLACEHOLDER,
    RedactionSink,
)
from app.fanout.supervisor import Supervisor
from app.fanout.worker import (
    Worker,
    WorkerErrorEvent,
    WorkerEvent,
    WorkerEventKind,
    WorkerExitEvent,
    WorkerId,
    WorkerProgressEvent,
    WorkerSpec,
    WorkerStalledEvent,
    WorkerStateEvent,
)

__all__ = [
    "BACKOFF_BASE",
    "BACKOFF_CAP",
    "EVENT_BUS_CAPACITY",
    "FFMPEG_BASE_ARGS",
    "JITTER_PCT",
    "MAX_LINE_LEN",
    "REDACTION_PLACEHOLDER",
    "AsyncioProcessSpawner",
    "BusEvent",
    "DropAlertEvent",
    "EventBus",
    "FFmpegWorker",
    "ProcessSpawner",
    "ProgressFrame",
    "ProgressParseError",
    "ProgressParser",
    "RedactionSink",
    "RunStateChangedEvent",
    "SpawnedProcess",
    "Supervisor",
    "TargetSnapshotEvent",
    "Worker",
    "WorkerErrorEvent",
    "WorkerEvent",
    "WorkerEventKind",
    "WorkerExitEvent",
    "WorkerId",
    "WorkerProgressEvent",
    "WorkerSpec",
    "WorkerStalledEvent",
    "WorkerStateEvent",
    "build_argv",
    "compose_out_url",
    "compute_backoff",
]
