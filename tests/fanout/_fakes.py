"""Test fakes for the fanout layer.

`FakeProcess` implements `SpawnedProcess` over an asyncio.StreamReader
pair the test feeds bytes into. `FakeProcessSpawner` returns a queue of
pre-staged FakeProcess instances so a test can simulate "fail twice
then succeed" without spawning real ffmpeg.

Importable but NOT a test module (`_fakes.py` underscore prefix).
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Mapping, Sequence


class FakeProcess:
    """Implements `app.fanout.process.SpawnedProcess`.

    The test drives stdout/stderr by calling `feed_stdout` / `feed_stderr`
    and triggers exit by `trigger_exit(rc)`. Signals received are
    recorded for assertions.
    """

    def __init__(
        self,
        *,
        pid: int = 12345,
        exit_on_signal: bool = False,
    ) -> None:
        self._pid: int | None = pid
        self._stdout = asyncio.StreamReader()
        self._stderr = asyncio.StreamReader()
        self.signals_received: list[int] = []
        self.terminated = False
        self.killed = False
        self._returncode: int | None = None
        self._exit_event = asyncio.Event()
        self._exit_on_signal = exit_on_signal

    @property
    def pid(self) -> int | None:
        return self._pid

    @property
    def stdout(self) -> asyncio.StreamReader | None:
        return self._stdout

    @property
    def stderr(self) -> asyncio.StreamReader | None:
        return self._stderr

    @property
    def returncode(self) -> int | None:
        return self._returncode

    def feed_stdout(self, data: bytes) -> None:
        self._stdout.feed_data(data)

    def feed_stderr(self, data: bytes) -> None:
        self._stderr.feed_data(data)

    def close_stdout(self) -> None:
        self._stdout.feed_eof()

    def close_stderr(self) -> None:
        self._stderr.feed_eof()

    def send_signal(self, sig: int) -> None:
        self.signals_received.append(sig)
        if self._exit_on_signal and self._returncode is None:
            self.trigger_exit(rc=-sig)

    def terminate(self) -> None:
        self.terminated = True
        self.send_signal(signal.SIGTERM)

    def kill(self) -> None:
        self.killed = True
        # Use SIGTERM as a stand-in on Windows (no SIGKILL).
        self.send_signal(getattr(signal, "SIGKILL", signal.SIGTERM))

    async def wait(self) -> int:
        await self._exit_event.wait()
        return self._returncode if self._returncode is not None else 0

    def trigger_exit(self, rc: int = 0) -> None:
        if self._returncode is not None:
            return
        self._returncode = rc
        self._exit_event.set()
        self.close_stdout()
        self.close_stderr()


class FakeProcessSpawner:
    """Returns pre-staged `FakeProcess` instances in order.

    Construct with `FakeProcessSpawner([proc1, proc2, ...])`; calls to
    `spawn(...)` pop from the front. Running out raises RuntimeError so
    a test that under-stages fails loudly rather than hanging.
    """

    def __init__(self, processes: Sequence[FakeProcess]) -> None:
        self._queue: list[FakeProcess] = list(processes)
        self.spawn_calls: list[tuple[Sequence[str], Mapping[str, str]]] = []

    async def spawn(
        self,
        *,
        argv: Sequence[str],
        env: Mapping[str, str],
    ) -> FakeProcess:
        if not self._queue:
            raise RuntimeError("FakeProcessSpawner exhausted; test under-staged")
        self.spawn_calls.append((tuple(argv), dict(env)))
        return self._queue.pop(0)
