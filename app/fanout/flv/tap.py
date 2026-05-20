"""FLV tap — the multi-track ingest fork (ADR-0016 §1, Option A).

`FlvTap` runs a dedicated `ffmpeg -i <ingest> -c copy -f flv pipe:1`
subprocess that reads the operator's RTMP push off the loopback ingest
and re-muxes it (no transcode — ADR-0008 `-c copy` lock) to an FLV byte
stream on stdout. The supervisor (Phase C, slice C1) consumes
`chunks()`, feeds the bytes to `app.fanout.flv.parser`, and routes
per-track output to the per-target workers via their
`WorkerSpec.stdin_provider` queues.

Option A vs Option B: the Phase A pilot validates that ffmpeg's
`-c copy -f flv` forwards Enhanced-RTMP multi-track tags
(`PACKETTYPE_MULTITRACK = 6`,
`reference/obs-studio/plugins/obs-outputs/flv-mux.c:442-501`) intact
rather than silently dropping them. If the pilot shows ffmpeg drops
them, ADR-0016 escalates to Option B (a native Python RTMP client) and
this module is replaced. Until then Option A is the design.

Scope of THIS module: subprocess lifecycle + byte streaming + stderr
drain only. It does NOT demux, classify tracks, restart on failure, or
emit bus events — those are the supervisor's responsibility (slice C1).
On unexpected ffmpeg exit (ingest disconnect, crash), `chunks()` simply
ends; the supervisor decides whether to restart.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from collections.abc import AsyncIterator, Mapping
from datetime import timedelta
from typing import Final

import structlog

from app.fanout.process import ProcessSpawner, SpawnedProcess

_logger = structlog.get_logger(__name__)

DEFAULT_READ_CHUNK_SIZE: Final[int] = 65536
"""stdout read granularity. 64 KiB balances syscall overhead against
the latency of holding bytes before the demuxer sees them. The demuxer
is a push-parser, so chunk boundaries never split a tag incorrectly —
it buffers a partial tag across reads."""

_STDERR_RING_LINES: Final[int] = 50
"""Bound on retained stderr lines for diagnostics. The tap reads from
the loopback ingest and writes to a pipe — neither argv embeds a
credential — so stderr cannot leak secrets and needs no RedactionSink.
Draining it IS still required: a full stderr pipe blocks ffmpeg."""

TAP_ARGS: Final[tuple[str, ...]] = (
    "ffmpeg",
    "-hide_banner",
    "-loglevel",
    "error",
    "-nostats",
    # Pull as fast as the stream arrives, never pre-buffer — the tap is
    # latency-critical because every downstream worker waits on it.
    "-rtmp_buffer",
    "100",
    "-fflags",
    "+nobuffer",
    "-flags",
    "low_delay",
)
"""Input-side flags for the tap. Distinct from `ffmpeg_args.FFMPEG_BASE_ARGS`
(the per-target worker baseline) because the tap must NOT use
`-progress pipe:1` — pipe:1 carries the FLV byte stream here, not
ffmpeg progress frames."""


def build_tap_argv(ingest_url: str) -> list[str]:
    """Compose the tap's ffmpeg argv.

    Reads `ingest_url` (the loopback RTMP the operator publishes to),
    copies every stream without transcoding, muxes to FLV, and writes
    to stdout (`pipe:1`).

    `-flush_packets 1` + `-flvflags no_duration_filesize` mark the FLV
    as a live stream and push every packet immediately (no output
    buffering), matching the per-target worker behaviour so the tap
    adds no latency of its own.
    """
    if not ingest_url:
        raise ValueError("ingest_url must not be empty")
    return [
        *TAP_ARGS,
        "-i",
        ingest_url,
        "-c",
        "copy",
        "-f",
        "flv",
        "-flvflags",
        "no_duration_filesize",
        "-flush_packets",
        "1",
        "pipe:1",
    ]


class FlvTapError(RuntimeError):
    """Raised on tap lifecycle misuse (double-start, stream-before-start)."""


class FlvTap:
    """Lifecycle wrapper around the tap ffmpeg subprocess.

    Usage (the supervisor, slice C1):

        tap = FlvTap(ingest_url=loopback, process_spawner=spawner)
        await tap.start()
        async for chunk in tap.chunks():
            demuxer.feed(chunk)
        await tap.stop(grace=timedelta(seconds=5))

    Single-use: a stopped tap is not restartable — the supervisor
    constructs a fresh `FlvTap` per run.
    """

    def __init__(
        self,
        *,
        ingest_url: str,
        process_spawner: ProcessSpawner,
        env: Mapping[str, str] | None = None,
        read_chunk_size: int = DEFAULT_READ_CHUNK_SIZE,
    ) -> None:
        if read_chunk_size <= 0:
            raise ValueError("read_chunk_size must be > 0")
        self._ingest_url = ingest_url
        self._spawner = process_spawner
        self._env: dict[str, str] = dict(env or {})
        self._read_chunk_size = read_chunk_size
        self._proc: SpawnedProcess | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_ring: deque[str] = deque(maxlen=_STDERR_RING_LINES)
        self._started = False
        self._stopped = False

    @property
    def returncode(self) -> int | None:
        """The tap process exit code, or None while running / never started."""
        return self._proc.returncode if self._proc is not None else None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    async def start(self) -> None:
        """Spawn the tap ffmpeg and begin draining stderr.

        Idempotency: raises `FlvTapError` on a second call. The
        supervisor constructs one tap per run; a double-start is a bug.
        """
        if self._started:
            raise FlvTapError("FlvTap.start() called twice")
        if self._stopped:
            raise FlvTapError("FlvTap is single-use; cannot start after stop")
        self._started = True
        argv = build_tap_argv(self._ingest_url)
        self._proc = await self._spawner.spawn(argv=argv, env=self._env)
        self._stderr_task = asyncio.create_task(self._drain_stderr(), name="flv-tap-stderr")
        _logger.info("flv_tap_started", pid=self._proc.pid)

    async def chunks(self) -> AsyncIterator[bytes]:
        """Yield FLV byte chunks from the tap's stdout until EOF.

        EOF arrives when ffmpeg exits (clean stop, ingest disconnect, or
        crash). The iterator simply ends — restart policy is the
        supervisor's call. Caller MUST have awaited `start()` first.

        NOT safe to iterate from more than one task simultaneously:
        concurrent iterators race on the same `StreamReader` and each
        sees a fragmented byte stream. The supervisor uses a single
        consumer (slice C1).
        """
        if self._proc is None:
            raise FlvTapError("FlvTap.chunks() called before start()")
        stdout = self._proc.stdout
        if stdout is None:  # pragma: no cover - spawner always pipes stdout
            raise FlvTapError("tap process has no stdout pipe")
        while True:
            chunk = await stdout.read(self._read_chunk_size)
            if not chunk:
                return
            yield chunk

    async def stop(self, *, grace: timedelta = timedelta(seconds=5)) -> None:
        """Terminate the tap: SIGTERM, wait `grace`, then SIGKILL.

        Safe to call before `start()` (no-op) and more than once
        (subsequent calls are no-ops). Cancels the stderr drain task and
        awaits final process exit so no zombie is left.
        """
        if self._stopped:
            return
        self._stopped = True
        proc = self._proc
        if proc is None:
            return
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=grace.total_seconds())
            except TimeoutError:
                _logger.warning("flv_tap_sigkill", pid=proc.pid, grace_s=grace.total_seconds())
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                # Unbounded by design: on POSIX a SIGKILL'd process is always
                # reaped by the kernel, so wait() cannot block indefinitely.
                # (Test fakes must call trigger_exit() to mirror that reap —
                # see tests/fanout/_fakes.py FakeProcess.kill.)
                await proc.wait()
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stderr_task
        _logger.info("flv_tap_stopped", pid=proc.pid, returncode=proc.returncode)

    def recent_stderr(self) -> tuple[str, ...]:
        """Diagnostic snapshot of the last ≤ `_STDERR_RING_LINES` stderr lines."""
        return tuple(self._stderr_ring)

    async def _drain_stderr(self) -> None:
        """Continuously read stderr so a full pipe never blocks ffmpeg.

        Retains the last N lines for diagnostics. No RedactionSink: the
        tap argv embeds no credential, so stderr cannot carry one.
        """
        proc = self._proc
        if proc is None or proc.stderr is None:  # pragma: no cover
            return
        stderr = proc.stderr
        try:
            while True:
                line = await stderr.readline()
                if not line:
                    return
                self._stderr_ring.append(line.decode("utf-8", errors="replace").rstrip("\n"))
        except asyncio.CancelledError:
            raise
        except Exception:
            # A drain read can fail on a torn-down transport. Swallowing into
            # the Task would surface only as "Task exception was never
            # retrieved" at GC — log it instead. The tap keeps running; stderr
            # is diagnostic, not load-bearing.
            _logger.exception("flv_tap_stderr_drain_error", pid=proc.pid)
