"""FFmpegWorker — v1 concrete implementation of the Worker protocol.

One outbound RTMP push, supervised by an internal asyncio TaskGroup
during each spawn iteration:

  - `_stdout_pump`: reads stdout, feeds `ProgressParser`, emits
    `WorkerProgressEvent` per frame, updates `_last_progress_at_ns`.
  - `_stderr_pump`: reads stderr, feeds `RedactionSink`, appends each
    redacted line to the per-worker ring buffer and emits a
    structured log event. Lines are NEVER persisted untruncated;
    overflow is force-split.
  - `_stall_watcher`: a 1-Hz tick that compares wall-monotonic age
    of the last progress event against `STALL_TIMEOUT`; on exceed,
    emits `WorkerStalledEvent` and terminates the process so the
    lifecycle loop treats it as a failed run.
  - `_wait_for_exit`: blocks on `process.wait()`; when it returns,
    the TaskGroup tears down the other three.

The `_lifecycle_loop` (top-level background task created in `start()`)
spawns ffmpeg, runs the pump TaskGroup until exit, consults the
breaker, and either backs off + respawns, transitions to FAILED_OPEN,
or exits cleanly on `stop()`.

Circuit breaker integration: the breaker lives on the Worker. After
each non-zero exit while not stopping, `record_failure(now)` is
called and `should_open(now)` consulted; the verdict routes the
WorkerState transition. `record_healthy(now)` fires from the stdout
pump on each progress frame (the running indicator).
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import signal
import time
from collections import deque
from collections.abc import AsyncIterator, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Final

import anyio
import structlog
from anyio.streams.memory import (
    MemoryObjectReceiveStream,
    MemoryObjectSendStream,
)

from app.domain.circuit_breaker import (
    BreakerVerdict,
    CircuitBreaker,
    CircuitBreakerPolicy,
)
from app.domain.errors import IllegalWorkerStateTransitionError
from app.domain.worker_state import (
    WorkerAction,
    WorkerProgressSnapshot,
    WorkerSnapshot,
    WorkerState,
    transition,
)
from app.fanout.backoff import compute_backoff
from app.fanout.ffmpeg_args import build_argv
from app.fanout.process import ProcessSpawner, SpawnedProcess
from app.fanout.progress_parser import (
    ProgressFrame,
    ProgressParseError,
    ProgressParser,
)
from app.fanout.redaction import RedactionSink
from app.fanout.worker import (
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

_logger = structlog.get_logger(__name__)

STALL_TIMEOUT: Final[timedelta] = timedelta(seconds=5)
"""ADR-0008: no progress event in 5 s ⇒ stall ⇒ terminate + reconnect."""

EVENT_CHANNEL_BUFFER: Final[int] = 64
"""Bounded buffer on the per-worker event channel. The supervisor must
drain faster than ffmpeg emits; if the supervisor stalls, the worker's
emit blocks. We log a watermark warning at slow sends."""

LOG_RING_LINES: Final[int] = 200
"""Per-worker stderr line retention for the WS slide-out (Phase 6)."""

_HARD_KILL_GRACE: Final[float] = 2.0
"""After SIGKILL, wait this long for the OS to reap before giving up."""

_SLOW_EMIT_WARN: Final[float] = 0.1
"""Log a `worker_event_backpressure` warning if a single event send
takes longer than this many seconds — helps diagnose a wedged drainer."""


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class FFmpegWorker:
    """v1 Worker implementation.

    All collaborators are injected (process spawner, clocks, breaker
    policy, RNG) so tests can drive deterministic scenarios without
    spawning real subprocesses or waiting on real wall-clock time.
    """

    def __init__(
        self,
        *,
        spec: WorkerSpec,
        process_spawner: ProcessSpawner,
        env: Mapping[str, str] | None = None,
        breaker_policy: CircuitBreakerPolicy | None = None,
        stall_timeout: timedelta = STALL_TIMEOUT,
        clock: Callable[[], datetime] = _utc_now,
        monotonic_clock_ns: Callable[[], int] = time.monotonic_ns,
        rng: random.Random | None = None,
        stall_check_interval: float = 1.0,
    ) -> None:
        self._spec = spec
        self._process_spawner = process_spawner
        self._env = dict(env) if env is not None else {}
        self._breaker = CircuitBreaker(
            policy=breaker_policy if breaker_policy is not None else CircuitBreakerPolicy()
        )
        self._stall_timeout = stall_timeout
        self._clock = clock
        self._monotonic_clock_ns = monotonic_clock_ns
        self._rng = rng
        self._stall_check_interval = stall_check_interval

        self._state: WorkerState = WorkerState.IDLE
        self._attempt: int = 0
        self._last_event_at: datetime = clock()
        self._last_error: str | None = None
        self._last_progress_at_ns: int = monotonic_clock_ns()
        # Most recent ffmpeg progress frame snapshot, surfaced via
        # `snapshot().last_progress` for UI rendering. Cleared on each
        # `start()` so reconnect leftovers don't bleed into a new session.
        self._last_progress: WorkerProgressSnapshot | None = None
        self._log_ring: deque[bytes] = deque(maxlen=LOG_RING_LINES)
        self._redaction = RedactionSink(worker_url=spec.output_url)

        send, receive = anyio.create_memory_object_stream[WorkerEvent](
            max_buffer_size=EVENT_CHANNEL_BUFFER
        )
        self._send_stream: MemoryObjectSendStream[WorkerEvent] = send
        self._receive_stream: MemoryObjectReceiveStream[WorkerEvent] = receive

        self._process: SpawnedProcess | None = None
        self._stop_requested = asyncio.Event()
        self._user_reset_event = asyncio.Event()
        self._lifecycle_task: asyncio.Task[None] | None = None
        self._started_at: datetime | None = None

    # ------------------------------------------------------------------
    # Worker protocol
    # ------------------------------------------------------------------

    @property
    def id(self) -> WorkerId:
        return self._spec.worker_id

    @property
    def spec(self) -> WorkerSpec:
        return self._spec

    @property
    def pid(self) -> int | None:
        """Live OS pid of the current ffmpeg subprocess, or None.

        Used by the host-stats sampler to read `/proc/<pid>/stat` for
        per-worker CPU%. Returns None when no process is currently
        spawned (idle, between reconnects, post-stop).
        """
        proc = self._process
        if proc is None:
            return None
        return proc.pid

    def snapshot(self) -> WorkerSnapshot:
        """Build a fresh `WorkerSnapshot` from current internal state.

        The snapshot is the only data the domain layer (Phase 4) accepts
        about a Worker; the supervisor calls this on every drained event
        and pumps into `Target.ingest_snapshot()`.
        """
        return WorkerSnapshot(
            role=self._spec.worker_id.role,
            state=self._state,
            last_event_at=self._last_event_at,
            last_error=self._last_error,
            breaker_failures_in_window=self._breaker.failure_count_in_window(self._clock()),
            last_progress=self._last_progress,
        )

    def events(self) -> AsyncIterator[WorkerEvent]:
        """Single-subscriber async iterator over WorkerEvents.

        Returns the anyio receive stream directly; multiple consumers
        will RACE — the supervisor's per-worker pump task is the only
        intended consumer.
        """
        return _stream_to_aiter(self._receive_stream)

    async def start(self) -> None:
        """Schedule the lifecycle loop and return.

        Idempotent: a second call while running is a no-op. Called by
        the supervisor's reconciler when a Target with this Worker
        becomes enabled.
        """
        if self._lifecycle_task is not None and not self._lifecycle_task.done():
            return
        self._stop_requested.clear()
        self._user_reset_event.clear()
        self._started_at = self._clock()
        # Clear any leftover progress snapshot so a freshly-started
        # session does not surface a previous run's last bitrate while
        # the new ffmpeg is still negotiating.
        self._last_progress = None
        self._lifecycle_task = asyncio.create_task(
            self._lifecycle_loop(),
            name=f"ffmpeg-worker-lifecycle:{self._spec.worker_id.target_id}:{self._spec.worker_id.role.value}",
        )

    async def stop(self, grace: timedelta) -> None:
        """Graceful shutdown: SIGTERM → wait → SIGKILL.

        Sequence:
          1. Mark stop requested. The lifecycle loop will not respawn.
          2. If a process is alive, send SIGTERM and wait up to `grace`.
          3. If still alive after grace, SIGKILL and wait `_HARD_KILL_GRACE`.
          4. Wait for the lifecycle task to wind down (small timeout).
          5. Close the send stream so consumers of `events()` see end.
        """
        self._stop_requested.set()
        proc = self._process
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError, OSError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=grace.total_seconds())
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError, OSError):
                    proc.kill()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(proc.wait(), timeout=_HARD_KILL_GRACE)
        # Wake any user_reset waiters so the lifecycle loop can exit.
        self._user_reset_event.set()
        if self._lifecycle_task is not None and not self._lifecycle_task.done():
            try:
                await asyncio.wait_for(self._lifecycle_task, timeout=_HARD_KILL_GRACE)
            except TimeoutError:
                self._lifecycle_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                    await asyncio.wait_for(self._lifecycle_task, timeout=1.0)
        # Close both ends of the channel so consumers see EndOfStream
        # and anyio doesn't raise a finalizer warning during GC.
        await self._send_stream.aclose()
        await self._receive_stream.aclose()

    async def user_reset(self) -> None:
        """User-initiated 'Retry now' from FAILED_OPEN.

        Clears the breaker, zeros the attempt counter, and signals the
        lifecycle loop to leave the FAILED_OPEN waiting state and
        re-enter STARTING. No-op if not in FAILED_OPEN.
        """
        if self._state is not WorkerState.FAILED_OPEN:
            return
        self._breaker.reset()
        self._attempt = 0
        self._user_reset_event.set()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _lifecycle_loop(self) -> None:
        """The respawn-until-stopped loop.

        Each iteration: spawn ffmpeg, run pumps until exit, consult
        breaker, decide next move (respawn-with-backoff, FAILED_OPEN,
        or clean stop). Exits when `_stop_requested` is set or after
        FAILED_OPEN is cleared by a `stop()`.
        """
        try:
            await self._set_state(WorkerState.STARTING, WorkerAction.START, cause="initial start")
            while not self._stop_requested.is_set():
                await self._run_one_spawn()
                if self._stop_requested.is_set():
                    break

                now = self._clock()
                # Every iteration that reaches here counts as a failure:
                # a spawn-side exception, a non-zero exit, AND a zero
                # exit all mean the worker is not currently broadcasting
                # despite being supposed to. Distinguishing them in the
                # breaker would understate sustained outages.
                self._breaker.record_failure(now)

                verdict = self._breaker.should_open(now)
                self._last_error = self._last_error or "process exited unexpectedly"
                if self._state is WorkerState.STARTING:
                    # Spawn or near-immediate exit before we ever saw
                    # PROCESS_HEALTHY: route via STARTING → RECONNECTING.
                    await self._set_state(
                        WorkerState.RECONNECTING,
                        WorkerAction.PROCESS_EXITED,
                        cause="process exited during startup",
                    )
                elif self._state is WorkerState.RUNNING:
                    await self._set_state(
                        WorkerState.RECONNECTING,
                        WorkerAction.PROCESS_EXITED,
                        cause="process exited",
                    )
                # Apply RECONNECT verdict (CLOSED → stay reconnecting,
                # OPEN → FAILED_OPEN). Worker is now in RECONNECTING.
                await self._apply_reconnect_verdict(verdict, now)

                if self._state is WorkerState.FAILED_OPEN:
                    # Wait for either user_reset() or stop().
                    self._user_reset_event.clear()
                    await self._user_reset_event.wait()
                    if self._stop_requested.is_set():
                        break
                    # user_reset() already cleared breaker + attempt and
                    # set the event; transition out of FAILED_OPEN here.
                    await self._set_state(
                        WorkerState.STARTING,
                        WorkerAction.USER_RESET,
                        cause="user reset",
                    )
                    continue

                # Backoff before next spawn (we're in RECONNECTING).
                delay = compute_backoff(self._attempt, rng=self._rng)
                self._attempt += 1
                # Wait on the delay, but break early on stop.
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._stop_requested.wait(),
                        timeout=delay.total_seconds(),
                    )

            # Loop exit via stop_requested.
            if self._state not in (WorkerState.STOPPING, WorkerState.IDLE):
                await self._set_state(
                    WorkerState.STOPPING,
                    WorkerAction.STOP_REQUESTED,
                    cause="stop requested",
                )
            if self._state is WorkerState.STOPPING:
                await self._set_state(
                    WorkerState.IDLE,
                    WorkerAction.STOPPED,
                    cause="stopped",
                )
        except asyncio.CancelledError:
            _logger.warning(
                "ffmpeg_worker_lifecycle_cancelled",
                worker_id=self._worker_id_log(),
            )
            raise
        except Exception:
            _logger.exception(
                "ffmpeg_worker_lifecycle_unhandled",
                worker_id=self._worker_id_log(),
            )
            raise

    async def _apply_reconnect_verdict(
        self,
        verdict: BreakerVerdict,
        now: datetime,
    ) -> None:
        try:
            new_state = transition(
                self._state,
                WorkerAction.RECONNECT,
                verdict=verdict,
            )
        except IllegalWorkerStateTransitionError:
            # If we're not in RECONNECTING (already STOPPING perhaps), no-op.
            return
        if new_state is self._state:
            return
        cause = f"breaker {verdict.value} ({self._breaker.failure_count_in_window(now)} failures)"
        await self._emit_state_change(new_state, cause=cause)
        self._state = new_state

    async def _run_one_spawn(self) -> None:
        """Spawn ffmpeg and run the pumps until exit or stop.

        On a spawn-side failure, an error event is emitted and the
        method returns; the lifecycle loop then routes through the
        breaker the same way as a non-zero exit (post-Phase-5 review
        fix M-3: spawn failure and exit are equivalent failure causes).
        """
        try:
            self._process = await self._process_spawner.spawn(
                argv=build_argv(self._spec),
                env=self._env,
            )
        except (OSError, ValueError) as exc:
            self._last_error = f"spawn failed: {type(exc).__name__}"
            await self._emit_error(self._last_error, fatal=False)
            return

        self._last_progress_at_ns = self._monotonic_clock_ns()
        spawn_at_mono = self._monotonic_clock_ns()
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self._stdout_pump(), name="stdout")
                tg.create_task(self._stderr_pump(), name="stderr")
                tg.create_task(self._stall_watcher(), name="stall")
                tg.create_task(self._wait_for_exit(), name="exit")
        except* Exception as eg:
            for nested in eg.exceptions:
                _logger.exception(
                    "ffmpeg_worker_pump_unhandled",
                    worker_id=self._worker_id_log(),
                    error=type(nested).__name__,
                )

        proc = self._process
        rc = proc.returncode if proc is not None else None
        sig: int | None = None
        if rc is not None and rc < 0:
            sig = -rc
            rc = None
        duration_ns = self._monotonic_clock_ns() - spawn_at_mono
        await self._emit(
            WorkerExitEvent(
                returncode=rc,
                signal=sig,
                duration=timedelta(microseconds=duration_ns / 1000),
            ),
            kind="exited",
        )
        self._process = None

    # ------------------------------------------------------------------
    # Internal pumps
    # ------------------------------------------------------------------

    async def _stdout_pump(self) -> None:
        """Read ffmpeg stdout, parse progress frames, emit events."""
        proc = self._process
        if proc is None or proc.stdout is None:
            return
        parser = ProgressParser()
        try:
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    return
                try:
                    frames = list(parser.feed(chunk))
                except ProgressParseError as exc:
                    self._last_error = f"progress parse: {exc}"
                    await self._emit_error(self._last_error, fatal=False)
                    parser = ProgressParser()
                    continue
                for frame in frames:
                    self._last_progress_at_ns = self._monotonic_clock_ns()
                    if self._state is WorkerState.STARTING:
                        await self._set_state(
                            WorkerState.RUNNING,
                            WorkerAction.PROCESS_HEALTHY,
                            cause="first progress frame",
                        )
                    elif self._state is WorkerState.RECONNECTING:
                        await self._set_state(
                            WorkerState.RUNNING,
                            WorkerAction.PROCESS_HEALTHY,
                            cause="progress resumed after reconnect",
                        )
                        # Reconnect succeeded; reset attempt counter.
                        self._attempt = 0
                    if self._state is WorkerState.RUNNING:
                        self._breaker.record_healthy(self._clock())
                    await self._emit_progress(frame)
        except (asyncio.CancelledError, anyio.EndOfStream):
            raise
        except Exception as exc:
            self._last_error = f"stdout pump: {type(exc).__name__}"
            await self._emit_error(self._last_error, fatal=False)

    async def _stderr_pump(self) -> None:
        """Read ffmpeg stderr through RedactionSink → ring + structlog."""
        proc = self._process
        if proc is None or proc.stderr is None:
            return
        try:
            while True:
                chunk = await proc.stderr.read(4096)
                if not chunk:
                    for line in self._redaction.flush():
                        self._consume_stderr_line(line)
                    return
                for line in self._redaction.feed(chunk):
                    self._consume_stderr_line(line)
        except (asyncio.CancelledError, anyio.EndOfStream):
            raise
        except Exception as exc:
            await self._emit_error(f"stderr pump: {type(exc).__name__}", fatal=False)

    def _consume_stderr_line(self, line: bytes) -> None:
        self._log_ring.append(line)
        # The structlog event_dict is also passed through the
        # CredentialRegistry-based redaction processor for defence in
        # depth (the registry catches credentials we may have missed
        # at the byte-stream layer).
        _logger.info(
            "ffmpeg_stderr",
            worker_id=self._worker_id_log(),
            line=line.decode("utf-8", errors="replace"),
        )

    async def _stall_watcher(self) -> None:
        """1-Hz tick checking time since last progress frame.

        Exits when the process dies (cleanly or otherwise) so the
        wrapping TaskGroup can complete; if a stall is detected first,
        terminates the process and exits.
        """
        try:
            while True:
                await asyncio.sleep(self._stall_check_interval)
                proc = self._process
                if proc is None or proc.returncode is not None:
                    return
                elapsed_ns = self._monotonic_clock_ns() - self._last_progress_at_ns
                if elapsed_ns / 1e9 > self._stall_timeout.total_seconds():
                    await self._emit(
                        WorkerStalledEvent(
                            no_progress_for=timedelta(microseconds=elapsed_ns / 1000),
                        ),
                        kind="stalled",
                    )
                    with contextlib.suppress(ProcessLookupError, OSError):
                        proc.terminate()
                    return
        except asyncio.CancelledError:
            raise

    async def _wait_for_exit(self) -> None:
        proc = self._process
        if proc is None:
            return
        # `wait()` is the only authoritative source for exit; the
        # TaskGroup cancels the other pumps once we return.
        try:
            await proc.wait()
        except asyncio.CancelledError:
            raise

    # ------------------------------------------------------------------
    # Event emission
    # ------------------------------------------------------------------

    async def _set_state(
        self,
        new_state: WorkerState,
        action: WorkerAction,
        *,
        cause: str,
    ) -> None:
        try:
            verified = transition(self._state, action)
        except IllegalWorkerStateTransitionError:
            _logger.warning(
                "ffmpeg_worker_illegal_transition",
                worker_id=self._worker_id_log(),
                from_state=self._state.value,
                action=action.value,
                target_state=new_state.value,
            )
            return
        if verified is not new_state:
            _logger.warning(
                "ffmpeg_worker_state_action_mismatch",
                worker_id=self._worker_id_log(),
                from_state=self._state.value,
                action=action.value,
                computed=verified.value,
                requested=new_state.value,
            )
            new_state = verified
        await self._emit_state_change(new_state, cause=cause)
        self._state = new_state

    async def _emit_state_change(self, new_state: WorkerState, *, cause: str) -> None:
        await self._emit(
            WorkerStateEvent(
                new_state=new_state,
                previous_state=self._state,
                cause=cause,
            ),
            kind="state",
        )

    async def _emit_progress(self, frame: ProgressFrame) -> None:
        now = self._clock()
        # Snapshot the progress fields for `snapshot().last_progress`
        # BEFORE awaiting `_emit` — the assignment is atomic, and a
        # supervisor that calls `snapshot()` between the emit and the
        # store would otherwise see a stale value.
        self._last_progress = WorkerProgressSnapshot(
            bitrate_kbps=frame.bitrate_kbps,
            fps=frame.fps,
            drop_frames=frame.drop_frames,
            speed=frame.speed,
            at=now,
        )
        await self._emit(
            WorkerProgressEvent(
                frame=frame.frame,
                fps=frame.fps,
                bitrate_kbps=frame.bitrate_kbps,
                out_time_us=frame.out_time_us,
                dup_frames=frame.dup_frames,
                drop_frames=frame.drop_frames,
                speed=frame.speed,
                progress=frame.progress,
            ),
            kind="progress",
        )

    async def _emit_error(self, message: str, *, fatal: bool) -> None:
        await self._emit(
            WorkerErrorEvent(message=message, fatal=fatal),
            kind="error",
        )

    async def _emit(
        self,
        payload: (
            WorkerStateEvent
            | WorkerProgressEvent
            | WorkerErrorEvent
            | WorkerExitEvent
            | WorkerStalledEvent
        ),
        *,
        kind: WorkerEventKind,
    ) -> None:
        now = self._clock()
        self._last_event_at = now
        ev = WorkerEvent(
            worker_id=self._spec.worker_id,
            at=now,
            monotonic_ns=self._monotonic_clock_ns(),
            kind=kind,
            payload=payload,
        )
        send_start = self._monotonic_clock_ns()
        try:
            await self._send_stream.send(ev)
        except anyio.BrokenResourceError:
            # Receiver closed; drop silently. This happens during stop().
            return
        send_elapsed = (self._monotonic_clock_ns() - send_start) / 1e9
        if send_elapsed > _SLOW_EMIT_WARN:
            _logger.warning(
                "worker_event_backpressure",
                worker_id=self._worker_id_log(),
                kind=kind,
                elapsed_seconds=send_elapsed,
            )

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def recent_log_lines(self) -> tuple[bytes, ...]:
        """Snapshot the per-worker stderr ring (for the WS slide-out)."""
        return tuple(self._log_ring)

    def _worker_id_log(self) -> str:
        return f"{self._spec.worker_id.target_id}:{self._spec.worker_id.role.value}"


async def _stream_to_aiter(
    stream: MemoryObjectReceiveStream[WorkerEvent],
) -> AsyncIterator[WorkerEvent]:
    """Adapt the anyio receive stream into a plain `AsyncIterator`.

    `MemoryObjectReceiveStream` already supports `async for`; this
    wrapper exists so `events()`'s return type matches the Worker
    Protocol exactly without exposing the anyio class. Wraps the
    iteration in `async with` so the stream is closed promptly when
    the consumer breaks out (otherwise anyio raises a finalizer
    ResourceWarning on GC, which our `filterwarnings = ["error"]`
    pytest config promotes to a test failure).
    """
    async with stream:
        try:
            async for ev in stream:
                yield ev
        except (anyio.EndOfStream, anyio.ClosedResourceError):
            return


# Re-exported for callers that want to send a SIGTERM constant by name.
SIGTERM: Final[int] = signal.SIGTERM
SIGKILL: Final[int] = getattr(signal, "SIGKILL", signal.SIGTERM)
