"""Tests for `app.fanout.flv.tap` — the multi-track ingest fork.

Drives `FlvTap` against the shared `FakeProcess` / `FakeProcessSpawner`
(no real ffmpeg). Covers argv construction, byte streaming, stderr
drain + ring buffer, and the SIGTERM → grace → SIGKILL stop path.
"""

from __future__ import annotations

import asyncio
import signal
from datetime import timedelta

import pytest
from app.fanout.flv.tap import (
    TAP_ARGS,
    FlvTap,
    FlvTapError,
    build_tap_argv,
)

from tests.fanout._fakes import FakeProcess, FakeProcessSpawner

_INGEST = "rtmp://127.0.0.1:1935/live/ingest_test"


# ---------------------------------------------------------------------------
# build_tap_argv


def test_build_tap_argv_shape() -> None:
    argv = build_tap_argv(_INGEST)
    assert argv[0] == "ffmpeg"
    # Input flags come from TAP_ARGS.
    assert argv[: len(TAP_ARGS)] == list(TAP_ARGS)
    # Input URL, copy, FLV mux to stdout.
    assert "-i" in argv
    assert argv[argv.index("-i") + 1] == _INGEST
    assert argv[argv.index("-c") + 1] == "copy"
    assert argv[argv.index("-f") + 1] == "flv"
    assert argv[-1] == "pipe:1"


def test_build_tap_argv_has_no_progress_flag() -> None:
    """pipe:1 carries FLV bytes here, NOT ffmpeg progress — so the
    tap must never request `-progress pipe:1`."""
    argv = build_tap_argv(_INGEST)
    assert "-progress" not in argv


def test_build_tap_argv_rejects_empty_url() -> None:
    with pytest.raises(ValueError, match="ingest_url must not be empty"):
        build_tap_argv("")


# ---------------------------------------------------------------------------
# lifecycle + streaming


@pytest.fixture
async def proc() -> FakeProcess:
    # Async fixture: FakeProcess constructs asyncio.StreamReader(), which
    # needs a running event loop. A sync fixture would build it before the
    # loop exists and trip the "no current event loop" DeprecationWarning
    # (which the project's filterwarnings=error turns into a hard failure).
    #
    # exit_on_signal=True models a well-behaved ffmpeg that exits on SIGTERM,
    # so `tap.stop()` in the happy-path tests completes instead of blocking on
    # `proc.wait()`. The SIGKILL-escalation path is covered separately by
    # `test_stop_sigkills_on_grace_timeout` with `exit_on_signal=False`.
    return FakeProcess(exit_on_signal=True)


@pytest.fixture
async def spawner(proc: FakeProcess) -> FakeProcessSpawner:
    return FakeProcessSpawner([proc])


async def test_start_spawns_with_tap_argv(proc: FakeProcess, spawner: FakeProcessSpawner) -> None:
    tap = FlvTap(ingest_url=_INGEST, process_spawner=spawner)
    await tap.start()
    assert tap.pid == proc.pid
    assert len(spawner.spawn_calls) == 1
    argv, _env = spawner.spawn_calls[0]
    assert list(argv) == build_tap_argv(_INGEST)
    await tap.stop()


async def test_double_start_raises(spawner: FakeProcessSpawner) -> None:
    tap = FlvTap(ingest_url=_INGEST, process_spawner=spawner)
    await tap.start()
    with pytest.raises(FlvTapError, match="called twice"):
        await tap.start()
    await tap.stop()


async def test_chunks_before_start_raises(spawner: FakeProcessSpawner) -> None:
    tap = FlvTap(ingest_url=_INGEST, process_spawner=spawner)
    with pytest.raises(FlvTapError, match="before start"):
        await anext(tap.chunks())


async def test_chunks_streams_stdout_until_eof(
    proc: FakeProcess, spawner: FakeProcessSpawner
) -> None:
    tap = FlvTap(ingest_url=_INGEST, process_spawner=spawner, read_chunk_size=4)
    await tap.start()

    received: list[bytes] = []

    async def consume() -> None:
        async for chunk in tap.chunks():
            received.append(chunk)

    async with asyncio.TaskGroup() as tg:
        tg.create_task(consume())
        await asyncio.sleep(0)
        proc.feed_stdout(b"FLV\x01\x05data")
        await asyncio.sleep(0)
        proc.close_stdout()

    assert b"".join(received) == b"FLV\x01\x05data"
    await tap.stop()


async def test_chunks_ends_when_process_exits(
    proc: FakeProcess, spawner: FakeProcessSpawner
) -> None:
    """An ingest disconnect / ffmpeg crash closes stdout; chunks() ends
    without raising — the supervisor decides restart policy."""
    tap = FlvTap(ingest_url=_INGEST, process_spawner=spawner)
    await tap.start()

    received: list[bytes] = []

    async def consume() -> None:
        async for chunk in tap.chunks():
            received.append(chunk)

    async with asyncio.TaskGroup() as tg:
        tg.create_task(consume())
        await asyncio.sleep(0)
        proc.feed_stdout(b"partial")
        await asyncio.sleep(0)
        proc.trigger_exit(rc=1)  # crash: closes stdout + stderr

    assert b"".join(received) == b"partial"
    assert tap.returncode == 1
    await tap.stop()


# ---------------------------------------------------------------------------
# stderr drain


async def test_stderr_is_drained_into_ring(proc: FakeProcess, spawner: FakeProcessSpawner) -> None:
    tap = FlvTap(ingest_url=_INGEST, process_spawner=spawner)
    await tap.start()
    proc.feed_stderr(b"first error line\nsecond error line\n")
    await asyncio.sleep(0.01)
    proc.trigger_exit(rc=1)
    await tap.stop()
    recent = tap.recent_stderr()
    assert "first error line" in recent
    assert "second error line" in recent


async def test_stderr_ring_is_bounded(proc: FakeProcess, spawner: FakeProcessSpawner) -> None:
    tap = FlvTap(ingest_url=_INGEST, process_spawner=spawner)
    await tap.start()
    # Feed more than the ring capacity (50).
    blob = "".join(f"line {i}\n" for i in range(120)).encode()
    proc.feed_stderr(blob)
    await asyncio.sleep(0.02)
    proc.trigger_exit(rc=0)
    await tap.stop()
    recent = tap.recent_stderr()
    assert len(recent) <= 50
    # The most-recent lines are retained, oldest evicted.
    assert "line 119" in recent
    assert "line 0" not in recent


# ---------------------------------------------------------------------------
# stop semantics


async def test_stop_terminates_then_awaits_exit() -> None:
    proc = FakeProcess(exit_on_signal=True)
    sp = FakeProcessSpawner([proc])
    tap = FlvTap(ingest_url=_INGEST, process_spawner=sp)
    await tap.start()
    await tap.stop(grace=timedelta(seconds=1))
    assert proc.terminated is True
    assert signal.SIGTERM in proc.signals_received


async def test_stop_sigkills_on_grace_timeout() -> None:
    # exit_on_signal=False → SIGTERM is ignored; stop() must escalate to kill().
    proc = FakeProcess(exit_on_signal=False)
    sp = FakeProcessSpawner([proc])
    tap = FlvTap(ingest_url=_INGEST, process_spawner=sp)
    await tap.start()

    async def consume() -> None:
        async for _ in tap.chunks():
            pass

    async with asyncio.TaskGroup() as tg:
        tg.create_task(consume())
        await asyncio.sleep(0)
        # When kill() is called, FakeProcess records it; we have to make
        # the process actually exit so stop()'s final wait() returns.
        # Simulate the kernel killing it after kill().
        stop_task = tg.create_task(tap.stop(grace=timedelta(seconds=0.05)))
        await asyncio.sleep(0.1)
        proc.trigger_exit(rc=-9)
        await stop_task

    assert proc.killed is True


async def test_stop_before_start_is_noop(spawner: FakeProcessSpawner) -> None:
    tap = FlvTap(ingest_url=_INGEST, process_spawner=spawner)
    await tap.stop()  # must not raise
    assert tap.returncode is None


async def test_start_after_stop_raises_single_use(spawner: FakeProcessSpawner) -> None:
    """Single-use contract: a tap that has been stopped (even pre-start)
    must refuse a later start() rather than silently spawning a leaked
    process the now-inert stop() can never reap."""
    tap = FlvTap(ingest_url=_INGEST, process_spawner=spawner)
    await tap.stop()
    with pytest.raises(FlvTapError, match="single-use"):
        await tap.start()


async def test_stop_is_idempotent(proc: FakeProcess, spawner: FakeProcessSpawner) -> None:
    tap = FlvTap(ingest_url=_INGEST, process_spawner=spawner)
    await tap.start()
    proc.trigger_exit(rc=0)
    await tap.stop()
    await tap.stop()  # second call is a no-op
    assert proc.signals_received.count(signal.SIGTERM) <= 1


# ---------------------------------------------------------------------------
# construction guards


def test_rejects_nonpositive_chunk_size() -> None:
    # FakeProcessSpawner([]) constructs no StreamReaders, so it's safe to
    # build outside a running loop for this pure-constructor guard test.
    sp = FakeProcessSpawner([])
    with pytest.raises(ValueError, match="read_chunk_size must be > 0"):
        FlvTap(ingest_url=_INGEST, process_spawner=sp, read_chunk_size=0)
