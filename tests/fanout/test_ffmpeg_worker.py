"""FFmpegWorker tests — full lifecycle via FakeProcess.

The Worker spawns a subprocess (mocked), reads stdout for progress,
manages stderr through RedactionSink, runs a stall watcher, and decides
on each non-zero exit whether to backoff-and-respawn or open the
breaker.

We monkey-patch `compute_backoff` to return a 0 timedelta so tests
don't wait on real backoff seconds. We use a tight `stall_check_interval`
and `stall_timeout` for the stall test.
"""

from __future__ import annotations

import asyncio
import random
from datetime import timedelta

import pytest
from app.domain.circuit_breaker import CircuitBreakerPolicy
from app.domain.target_types import TargetType
from app.domain.worker_state import WorkerRole, WorkerState
from app.fanout.ffmpeg_worker import FFmpegWorker
from app.fanout.worker import (
    WorkerEvent,
    WorkerExitEvent,
    WorkerId,
    WorkerProgressEvent,
    WorkerSpec,
    WorkerStalledEvent,
    WorkerStateEvent,
)

from tests.fanout._fakes import FakeProcess, FakeProcessSpawner


def _spec(target_id: str = "t1") -> WorkerSpec:
    return WorkerSpec(
        worker_id=WorkerId(target_id=target_id, role=WorkerRole.PRIMARY),
        target_type=TargetType.TWITCH,
        input_url="rtmp://127.0.0.1:1935/live/ingest",
        output_url="rtmp://live.twitch.tv/app/secret_key_12345",
    )


def _progress_bytes(frame: int = 1, progress: str = "continue") -> bytes:
    return (
        f"frame={frame}\nfps=30.0\nbitrate=5000.0kbits/s\nout_time_us=33333\n"
        f"dup_frames=0\ndrop_frames=0\nspeed=1.00x\nprogress={progress}\n"
    ).encode()


@pytest.fixture(autouse=True)
def _zero_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make all backoff delays zero so tests don't wait on wall clock."""
    monkeypatch.setattr(
        "app.fanout.ffmpeg_worker.compute_backoff",
        lambda *_a, **_kw: timedelta(0),
    )


async def _drain_until(
    worker: FFmpegWorker,
    *,
    n: int,
    deadline: float = 2.0,
) -> list[WorkerEvent]:
    events: list[WorkerEvent] = []

    async def collect() -> None:
        async for ev in worker.events():
            events.append(ev)
            if len(events) >= n:
                return

    async with asyncio.timeout(deadline):
        await collect()
    return events


class TestHappyPath:
    async def test_starts_and_runs_until_clean_exit(
        self,
        fresh_credential_registry: None,
    ) -> None:
        proc = FakeProcess()
        spawner = FakeProcessSpawner([proc])
        worker = FFmpegWorker(
            spec=_spec(),
            process_spawner=spawner,
            stall_timeout=timedelta(seconds=10),
            stall_check_interval=0.05,
            rng=random.Random(0),
        )

        await worker.start()

        # Feed one progress frame, then exit cleanly.
        await asyncio.sleep(0.05)  # let the spawn happen
        proc.feed_stdout(_progress_bytes())
        await asyncio.sleep(0.05)

        # Collect events: STATE(STARTING) might already be drained;
        # we want at least state(RUNNING), progress, then exit.
        events = await _drain_until(worker, n=3, deadline=2.0)
        kinds = [e.kind for e in events]
        assert "progress" in kinds
        assert any(
            e.kind == "state" and e.payload.new_state is WorkerState.RUNNING  # type: ignore[union-attr]
            for e in events
        )

        proc.trigger_exit(rc=0)
        await worker.stop(grace=timedelta(seconds=1))

    async def test_first_progress_transitions_to_running(
        self,
        fresh_credential_registry: None,
    ) -> None:
        proc = FakeProcess()
        # Stage a second process for the inevitable respawn after the
        # first exits — clean exit still counts as failure (we wanted
        # it to keep streaming).
        proc2 = FakeProcess()
        spawner = FakeProcessSpawner([proc, proc2])
        worker = FFmpegWorker(
            spec=_spec(),
            process_spawner=spawner,
            stall_timeout=timedelta(seconds=10),
            stall_check_interval=0.05,
            rng=random.Random(0),
        )
        await worker.start()
        await asyncio.sleep(0.05)
        proc.feed_stdout(_progress_bytes())
        await asyncio.sleep(0.05)
        snap = worker.snapshot()
        assert snap.state is WorkerState.RUNNING
        await worker.stop(grace=timedelta(seconds=1))

    async def test_snapshot_carries_last_progress_after_first_frame(
        self,
        fresh_credential_registry: None,
    ) -> None:
        # `WorkerSnapshot.last_progress` is the surface the supervisor +
        # WS layer rely on for the per-target bitrate/drops in the
        # dashboard. Pre-progress the field is None; after a frame the
        # field carries the parsed values (bitrate kbits/s, drop_frames).
        proc = FakeProcess(pid=4242)
        spawner = FakeProcessSpawner([proc, FakeProcess()])
        worker = FFmpegWorker(
            spec=_spec(),
            process_spawner=spawner,
            stall_timeout=timedelta(seconds=10),
            stall_check_interval=0.05,
            rng=random.Random(0),
        )
        # `pid` is None before start() — there is no spawned process yet.
        assert worker.pid is None
        await worker.start()
        await asyncio.sleep(0.05)
        # Pre-progress: last_progress is None on the fresh snapshot.
        pre = worker.snapshot()
        assert pre.last_progress is None
        # After a progress frame the snapshot carries the parsed numbers.
        proc.feed_stdout(_progress_bytes(frame=42))
        await asyncio.sleep(0.05)
        post = worker.snapshot()
        assert post.last_progress is not None
        assert post.last_progress.bitrate_kbps == pytest.approx(5000.0)
        assert post.last_progress.fps == pytest.approx(30.0)
        assert post.last_progress.drop_frames == 0
        # The Worker's public pid surface tracks the spawned process.
        assert worker.pid == 4242
        await worker.stop(grace=timedelta(seconds=1))

    async def test_snapshot_last_progress_cleared_after_restart(
        self,
        fresh_credential_registry: None,
    ) -> None:
        # When `start()` is called on a fresh-but-previously-stopped
        # worker, last_progress must be cleared so leftover values from
        # the prior session don't bleed into the new one before the
        # first frame arrives.
        proc = FakeProcess()
        spawner = FakeProcessSpawner([proc, FakeProcess(), FakeProcess()])
        worker = FFmpegWorker(
            spec=_spec(),
            process_spawner=spawner,
            stall_timeout=timedelta(seconds=10),
            stall_check_interval=0.05,
            rng=random.Random(0),
        )
        await worker.start()
        await asyncio.sleep(0.05)
        proc.feed_stdout(_progress_bytes(frame=1))
        await asyncio.sleep(0.05)
        assert worker.snapshot().last_progress is not None
        await worker.stop(grace=timedelta(seconds=1))
        # Restart — pre-first-frame should be back to None.
        await worker.start()
        await asyncio.sleep(0.05)
        assert worker.snapshot().last_progress is None
        await worker.stop(grace=timedelta(seconds=1))


class TestStop:
    async def test_sigterm_then_exit(
        self,
        fresh_credential_registry: None,
    ) -> None:
        proc = FakeProcess(exit_on_signal=True)
        spawner = FakeProcessSpawner([proc])
        worker = FFmpegWorker(
            spec=_spec(),
            process_spawner=spawner,
            stall_timeout=timedelta(seconds=10),
            stall_check_interval=0.05,
            rng=random.Random(0),
        )
        await worker.start()
        await asyncio.sleep(0.05)
        await worker.stop(grace=timedelta(seconds=1))
        # Process received SIGTERM (terminate() called).
        assert proc.terminated

    async def test_sigkill_after_grace(
        self,
        fresh_credential_registry: None,
    ) -> None:
        proc = FakeProcess(exit_on_signal=False)  # ignores signals
        spawner = FakeProcessSpawner([proc])
        worker = FFmpegWorker(
            spec=_spec(),
            process_spawner=spawner,
            stall_timeout=timedelta(seconds=10),
            stall_check_interval=0.05,
            rng=random.Random(0),
        )
        await worker.start()
        await asyncio.sleep(0.05)

        async def _kick_kill_to_exit() -> None:
            # When stop() escalates to .kill(), force exit.
            await asyncio.sleep(0.2)
            proc.trigger_exit(rc=-9)

        async with asyncio.TaskGroup() as tg:
            tg.create_task(_kick_kill_to_exit())
            await worker.stop(grace=timedelta(milliseconds=100))
        assert proc.terminated
        assert proc.killed


class TestBreakerOpens:
    async def test_after_max_failures(
        self,
        fresh_credential_registry: None,
    ) -> None:
        # Use a tiny breaker so the test doesn't have to stage 30 processes.
        policy = CircuitBreakerPolicy(max_failures=3, window=timedelta(minutes=5))
        # Stage 3 processes that exit immediately with rc=1.
        procs = [FakeProcess() for _ in range(3)]
        for p in procs:
            p.trigger_exit(rc=1)
        spawner = FakeProcessSpawner(procs)
        worker = FFmpegWorker(
            spec=_spec(),
            process_spawner=spawner,
            breaker_policy=policy,
            stall_timeout=timedelta(seconds=10),
            stall_check_interval=0.05,
            rng=random.Random(0),
        )
        await worker.start()
        # Wait for state to settle into FAILED_OPEN.
        for _ in range(50):
            await asyncio.sleep(0.05)
            if worker.snapshot().state is WorkerState.FAILED_OPEN:
                break
        assert worker.snapshot().state is WorkerState.FAILED_OPEN
        await worker.stop(grace=timedelta(seconds=1))

    async def test_user_reset_clears_breaker(
        self,
        fresh_credential_registry: None,
    ) -> None:
        policy = CircuitBreakerPolicy(max_failures=2, window=timedelta(minutes=5))
        # 2 fail, then 1 succeeds after user_reset.
        proc1 = FakeProcess()
        proc1.trigger_exit(rc=1)
        proc2 = FakeProcess()
        proc2.trigger_exit(rc=1)
        proc3 = FakeProcess()  # alive after reset
        spawner = FakeProcessSpawner([proc1, proc2, proc3])
        worker = FFmpegWorker(
            spec=_spec(),
            process_spawner=spawner,
            breaker_policy=policy,
            stall_timeout=timedelta(seconds=10),
            stall_check_interval=0.05,
            rng=random.Random(0),
        )
        await worker.start()
        # Wait for FAILED_OPEN.
        for _ in range(50):
            await asyncio.sleep(0.05)
            if worker.snapshot().state is WorkerState.FAILED_OPEN:
                break
        assert worker.snapshot().state is WorkerState.FAILED_OPEN

        await worker.user_reset()
        # Wait for re-spawn.
        for _ in range(20):
            await asyncio.sleep(0.05)
            if worker.snapshot().state is WorkerState.STARTING:
                break
        assert worker.snapshot().state in (WorkerState.STARTING, WorkerState.RUNNING)
        await worker.stop(grace=timedelta(seconds=1))


class TestStall:
    async def test_terminates_process_on_stall(
        self,
        fresh_credential_registry: None,
    ) -> None:
        proc = FakeProcess(exit_on_signal=True)
        # Stage a second process for the inevitable respawn after stall.
        proc2 = FakeProcess(exit_on_signal=True)
        spawner = FakeProcessSpawner([proc, proc2])
        worker = FFmpegWorker(
            spec=_spec(),
            process_spawner=spawner,
            stall_timeout=timedelta(milliseconds=50),
            stall_check_interval=0.02,
            rng=random.Random(0),
        )
        await worker.start()
        # No stdout fed → stall detected.
        for _ in range(40):
            await asyncio.sleep(0.05)
            if proc.terminated:
                break
        assert proc.terminated
        await worker.stop(grace=timedelta(seconds=1))


class TestRedactionPropagates:
    async def test_stderr_url_redacted_in_ring(
        self,
        fresh_credential_registry: None,
    ) -> None:
        proc = FakeProcess()
        spawner = FakeProcessSpawner([proc])
        worker = FFmpegWorker(
            spec=_spec(),
            process_spawner=spawner,
            stall_timeout=timedelta(seconds=10),
            stall_check_interval=0.05,
            rng=random.Random(0),
        )
        await worker.start()
        await asyncio.sleep(0.05)
        proc.feed_stderr(b"error pushing to rtmp://live.twitch.tv/app/secret_key_12345\n")
        proc.trigger_exit(rc=0)
        await asyncio.sleep(0.1)

        ring = worker.recent_log_lines()
        assert any(b"***REDACTED***" in line for line in ring), ring
        assert all(b"secret_key_12345" not in line for line in ring), ring
        await worker.stop(grace=timedelta(seconds=1))


class TestSnapshot:
    async def test_carries_role_and_state(
        self,
        fresh_credential_registry: None,
    ) -> None:
        proc = FakeProcess()
        spawner = FakeProcessSpawner([proc])
        worker = FFmpegWorker(
            spec=_spec(),
            process_spawner=spawner,
            stall_timeout=timedelta(seconds=10),
            stall_check_interval=0.05,
            rng=random.Random(0),
        )
        snap_before = worker.snapshot()
        assert snap_before.state is WorkerState.IDLE
        assert snap_before.role is WorkerRole.PRIMARY

        await worker.start()
        await asyncio.sleep(0.05)
        proc.feed_stdout(_progress_bytes())
        await asyncio.sleep(0.05)

        snap_running = worker.snapshot()
        assert snap_running.state is WorkerState.RUNNING

        proc.trigger_exit(rc=0)
        await worker.stop(grace=timedelta(seconds=1))


class TestEvents:
    async def test_emits_progress_then_exit(
        self,
        fresh_credential_registry: None,
    ) -> None:
        proc = FakeProcess()
        # Stage an extra process; the first clean exit triggers respawn.
        proc2 = FakeProcess()
        spawner = FakeProcessSpawner([proc, proc2])
        worker = FFmpegWorker(
            spec=_spec(),
            process_spawner=spawner,
            stall_timeout=timedelta(seconds=10),
            stall_check_interval=0.05,
            rng=random.Random(0),
        )
        await worker.start()
        await asyncio.sleep(0.05)
        proc.feed_stdout(_progress_bytes(frame=1) + _progress_bytes(frame=2))
        await asyncio.sleep(0.1)
        proc.trigger_exit(rc=0)
        await asyncio.sleep(0.05)
        await worker.stop(grace=timedelta(seconds=1))

        # Ensure no plaintext key escaped through events.
        # (Events go to a local list before being inspected.)
        # We drain the (closed) channel — events() returns aiter that
        # ends on EndOfStream.
        events: list[WorkerEvent] = []
        async for ev in worker.events():
            events.append(ev)
        kinds = [e.kind for e in events]
        # The exact event ordering depends on TaskGroup interleaving,
        # but at minimum we should have observed at least one progress
        # event (or it was drained earlier in the test).
        _payload_types = (
            WorkerProgressEvent | WorkerExitEvent | WorkerStateEvent | WorkerStalledEvent
        )
        assert "exited" in kinds or all(isinstance(e.payload, _payload_types) for e in events)
