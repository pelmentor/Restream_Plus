"""Bounded supervisor → API event bus (ADR-0003 §"Concurrency").

ADR-0003 mandates a bounded queue between the supervisor and the API
groups: the supervisor MUST NEVER block on a slow consumer. We use a
custom `EventBus` over a `collections.deque(maxlen=N)` rather than
`asyncio.Queue` because Queue does not natively support drop-oldest.

Single producer (the supervisor) and a single drainer (Phase 6's API
task that fans events out to per-WS-client queues). The drop counter
is monotonic and process-lifetime; a non-zero counter is a signal to
investigate, not necessarily an alert. `/readyz` reads it (Phase 6).

Event ordering matters for the WS consumer (a `state` event after
`progress` must arrive after it), so we use one queue carrying a
tagged-union `BusEvent`, not N queues per kind.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

from app.domain.run_state import RunState
from app.domain.target import TargetUiState
from app.domain.worker_state import WorkerSnapshot
from app.fanout.worker import WorkerEvent, WorkerId

EVENT_BUS_CAPACITY: Final[int] = 10_000
"""ADR-0003 fixes this. With ≤ 6 targets producing ~1 progress event
per second per worker, 10 000 entries is ~25 minutes of headroom even
if the API drainer is wedged."""

EVENT_BUS_DROP_WINDOW_SECONDS: Final[float] = 60.0
"""Hex Audit BA-F9 (slice 10): rolling-window for the `dropped_recent`
metric. The lifetime `dropped_total` counter is monotonic and never
clears, so "drops happened weeks ago but the bus is healthy now" looks
identical to "the bus is dropping now" to anything reading
`dropped_total`. A 60-second tumbling window distinguishes the two:
`dropped_recent` is 0 when no drops have happened in the last minute,
non-zero when drops are currently in flight."""


@dataclass(frozen=True, slots=True)
class RunStateChangedEvent:
    """System-wide RunState transition. Broadcast to every WS client."""

    new_state: RunState
    previous_state: RunState
    cause: str


@dataclass(frozen=True, slots=True)
class TargetSnapshotEvent:
    """A Target's aggregated UI state changed. Carries the most recent
    per-role `WorkerSnapshot` so the WS consumer can render without a
    DB round-trip.
    """

    target_id: str
    ui_state: TargetUiState
    snapshots_by_role: tuple[WorkerSnapshot, ...]


@dataclass(frozen=True, slots=True)
class DropAlertEvent:
    """Emitted when the bus drops events. Carries the new total."""

    dropped_total: int


@dataclass(frozen=True, slots=True)
class HostCpuByTarget:
    """Per-(target_id, role) CPU% sample inside a `HostStatsEvent`.

    `cpu_pct` is normalized to 0-100% of one host (i.e. divided by
    `os.cpu_count()`) so values are comparable to `cpu_total_pct` on
    the carrying event.
    """

    worker_id: WorkerId
    cpu_pct: float


@dataclass(frozen=True, slots=True)
class HostStatsEvent:
    """Host-level periodic sample: process-tree CPU%, RSS, ingest bitrate.

    Produced by the supervisor's `_host_stats_loop` at a fixed tick (2 s
    default — fast enough for the operator's eye, slow enough that CPU
    deltas are meaningful). Subscribers render this as the always-visible
    header chip + ingest/egress pair in the hero card.

    `ingest_kbps` is None when nginx-rtmp's stat endpoint is unreachable
    or no publisher is currently connected — the UI must NOT render
    "0 Mbps" in that case, which would imply the show is dark.
    """

    cpu_total_pct: float
    cpu_by_target: tuple[HostCpuByTarget, ...]
    rss_bytes: int
    ingest_kbps: float | None


@dataclass(frozen=True, slots=True)
class TrackInfo:
    """One track in a multi-track FLV manifest (ADR-0016 Phase C)."""

    track_id: int
    """0..255, allocated by OBS per-encoder. Track 0 is the "primary" by
    convention — every legacy single-track stream is `track_id=0`."""

    codec_fourcc: str
    """Lowercase ASCII FourCC: `avc1` / `hvc1` / `av01` for video,
    `mp4a` for audio. Drawn from `flv-mux.c:91-128` (video) and
    `flv-mux.c:75-89` (audio)."""

    kind: Literal["video", "audio"]

    def __post_init__(self) -> None:
        # Wire-derived `track_id` is uint8; explicit guard catches a future
        # caller (test helper, supervisor synthetic event) that passes an
        # out-of-range value instead of letting it propagate silently to the
        # bus + frontend.
        if not 0 <= self.track_id <= 255:
            raise ValueError(f"track_id must be 0..255, got {self.track_id}")


@dataclass(frozen=True, slots=True)
class TrackManifestEvent:
    """ADR-0016 Phase C: emitted by the supervisor's FLV tap when a new
    track-set is observed for the current run (initial detection on the
    first key tag after `STARTING → ARMED`, and re-emission whenever the
    set changes mid-run — e.g., operator toggles a renditions in OBS).

    Consumers:
      - The Phase D frontend's `useTrackManifest` hook renders the
        per-target track-selector dropdown.
      - The supervisor itself reads back the manifest to drive
        per-target stdin-provider routing.

    `tracks` is an ORDERED tuple — order is "first seen on the wire",
    which OBS empirically emits as `track_id` ascending. Consumers MUST
    NOT depend on order for correctness; the tuple shape is for stable
    serialisation across WS deliveries.
    """

    tracks: tuple[TrackInfo, ...]


@dataclass(frozen=True, slots=True)
class BusEvent:
    """The single tagged-union envelope on the bus. Ordering preserved."""

    at: datetime
    monotonic_ns: int
    target_id: str | None
    payload: (
        WorkerEvent
        | RunStateChangedEvent
        | TargetSnapshotEvent
        | DropAlertEvent
        | HostStatsEvent
        | TrackManifestEvent
    )


class EventBus:
    """Bounded queue with drop-oldest semantics, single drainer.

    Producer side (the supervisor) calls `put(event)` from inside the
    asyncio event loop — the call is `O(1)` and never awaits. If the
    deque is at capacity, the OLDEST entry is evicted (deque(maxlen)
    semantics) and the drop counter is incremented.

    Consumer side (the API drainer) calls `async for event in bus.drain()`
    in exactly one task. Multiple drainers will RACE on the same deque;
    do not do this.
    """

    __slots__ = (
        "_buf",
        "_capacity",
        "_cond",
        "_drop_timestamps",
        "_drop_window_seconds",
        "_dropped_total",
        "_monotonic",
    )

    def __init__(
        self,
        *,
        capacity: int = EVENT_BUS_CAPACITY,
        drop_window_seconds: float = EVENT_BUS_DROP_WINDOW_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be > 0")
        if drop_window_seconds <= 0:
            raise ValueError("drop_window_seconds must be > 0")
        self._buf: deque[BusEvent] = deque(maxlen=capacity)
        self._capacity = capacity
        self._cond = asyncio.Condition()
        self._dropped_total = 0
        # Hex Audit BA-F9 (slice 10): rolling-window drop timestamps.
        # Each drop appends one float; the window prunes lazily on
        # read so a long-quiet bus pays no cost.
        self._drop_timestamps: deque[float] = deque()
        self._drop_window_seconds = drop_window_seconds
        self._monotonic = monotonic

    @property
    def dropped_total(self) -> int:
        """Monotonic count of events dropped due to overflow.

        Process-lifetime; never reset at runtime. /readyz reads this in
        Phase 6 to surface "we're losing events" to the operator.
        """
        return self._dropped_total

    @property
    def dropped_recent(self) -> int:
        """Drops in the rolling `EVENT_BUS_DROP_WINDOW_SECONDS` window.

        Hex Audit BA-F9 (slice 10): distinguishes "currently degraded"
        from "was-degraded-once-ages-ago". The UI banner reads this
        rather than `dropped_total` so a one-time historical spike
        doesn't show a permanent red bar.
        """
        cutoff = self._monotonic() - self._drop_window_seconds
        while self._drop_timestamps and self._drop_timestamps[0] < cutoff:
            self._drop_timestamps.popleft()
        return len(self._drop_timestamps)

    @property
    def capacity(self) -> int:
        return self._capacity

    def put(self, event: BusEvent) -> None:
        """Producer-side. Sync, O(1), never blocks.

        On overflow: deque drops the oldest entry implicitly via
        `maxlen`; we increment the drop counter explicitly AND append
        a timestamp to the rolling window (BA-F9). The producer
        (supervisor) MUST then call `notify()` from the event loop
        so any waiting drainer wakes — `put` is sync so a thread
        could in principle call it, but in practice the supervisor is
        single-loop, so the notify can be folded into the producer call
        site via `loop.call_soon(_notify_task)`.
        """
        if len(self._buf) >= self._capacity:
            self._dropped_total += 1
            self._drop_timestamps.append(self._monotonic())
        self._buf.append(event)

    async def notify(self) -> None:
        """Wake any drainer blocked on `drain()`. The supervisor calls
        this after one or more `put()`s in the same event-loop tick."""
        async with self._cond:
            self._cond.notify_all()

    async def drain(self) -> AsyncIterator[BusEvent]:
        """Single-subscriber drainer. Yields events as they arrive.

        Pops the entire buffered batch in one go to reduce wake churn;
        callers process the batch sequentially. Cancellation closes the
        async iterator cleanly via the standard generator protocol.

        Phase-5 scope: there is exactly one drainer (the API task that
        broadcasts to WS clients). Per-WS-client fan-out queues with
        their own per-client backpressure / disconnect watermark are a
        Phase-6 concern; the docstring at the top of this module
        describes that future shape but it is NOT yet implemented.
        """
        while True:
            async with self._cond:
                while not self._buf:
                    await self._cond.wait()
                batch = list(self._buf)
                self._buf.clear()
            for ev in batch:
                yield ev
