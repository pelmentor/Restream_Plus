"""Subprocess spawn abstraction (ADR-0001 amended 2026-05-16).

ADR-0001's open question on `asyncio.subprocess` vs `anyio.open_process`
is resolved in favor of stdlib `asyncio.create_subprocess_exec`. This
module exposes that decision behind a `ProcessSpawner` Protocol so
tests can substitute a `FakeProcessSpawner` without monkey-patching.

The production runtime is the Linux container (s6-overlay supervises
us). On Windows (the developer's host), this module is import-clean
but its `spawn()` method only ever runs in test paths via the
`FakeProcessSpawner` — we deliberately do NOT add a Windows shim,
because the only production target is Linux and dev tests substitute
the spawner entirely.

`SpawnedProcess` is a Protocol over the small subset of
`asyncio.subprocess.Process` we use; sticking to the Protocol means
`FakeProcess` in tests doesn't have to inherit from any concrete class.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Protocol


class SpawnedProcess(Protocol):
    """The subset of `asyncio.subprocess.Process` we depend on.

    `stdout` / `stderr` are pipe readers (we never spawn a process
    without piping both). `pid` is None during/after termination on
    some platforms; treat None as "process is gone".
    """

    @property
    def pid(self) -> int | None: ...
    @property
    def stdout(self) -> asyncio.StreamReader | None: ...
    @property
    def stderr(self) -> asyncio.StreamReader | None: ...
    @property
    def returncode(self) -> int | None: ...

    def send_signal(self, sig: int) -> None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    async def wait(self) -> int: ...


class ProcessSpawner(Protocol):
    """Spawns a child process with stdout+stderr piped.

    Implementations: `AsyncioProcessSpawner` (production) and the
    test fake under `tests/fanout/_fakes.py`.
    """

    async def spawn(
        self,
        *,
        argv: Sequence[str],
        env: Mapping[str, str],
    ) -> SpawnedProcess: ...


class AsyncioProcessSpawner:
    """Production spawner over `asyncio.create_subprocess_exec`.

    POSIX-only behavior we depend on:

    - `start_new_session=True` puts the child in its own process group
      so a SIGTERM we send to the child does not bleed to siblings or
      parent. This is critical: ffmpeg's signal handling is per-pid,
      and a stray SIGTERM could otherwise tear down the whole
      supervisor on a buggy nginx-rtmp.
    - `stdin=DEVNULL` so the child can never block on stdin EOF.
    - `stdout=PIPE`, `stderr=PIPE` because we consume both (progress
      on stdout, structured stderr through `RedactionSink`).
    """

    async def spawn(
        self,
        *,
        argv: Sequence[str],
        env: Mapping[str, str],
    ) -> SpawnedProcess:
        if not argv:
            raise ValueError("argv must not be empty")
        return await asyncio.create_subprocess_exec(
            *argv,
            env=dict(env),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            # Default in CPython 3.7+ but pinned here so a future
            # `pass_fds=...` addition can't silently start leaking
            # DB/KEK/socket fds into ffmpeg.
            close_fds=True,
        )
