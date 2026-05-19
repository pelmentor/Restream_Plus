"""Test surface for the Supervisor — `FakeSupervisor`.

Mirrors Phase 5's pattern of putting test fakes alongside the
production code they support (the `FakeProcessSpawner` at
`tests/fanout/_fakes.py` is for the process boundary; this one is
for the supervisor's public surface).

`FakeSupervisor` implements the exact public method set the Phase 6
HTTP handlers call:

  - `start_run` / `stop_run`
  - `enable_target` / `disable_target` / `reset_target_worker`
  - `notify_obs_publish_began` / `notify_obs_publish_ended`
  - `run_state` / `targets`
  - `heartbeat_age` / `heartbeat_is_stale`
  - `recent_log_lines`
  - `run` / `wait_ready` / `stop` (lifespan-facing)

Tests can call `inject_target_snapshot` to push a `TargetSnapshotEvent`
onto the real bus, drive `set_run_state` to force a `RunStateChangedEvent`,
and assert that handlers see the projected wire shape.

Underscore-prefix module name marks this as test-only — Phase 7
frontend code or app code MUST NOT import this.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from app.domain.run_state import RunState
from app.domain.target import Target
from app.domain.worker_state import WorkerRole
from app.fanout.event_bus import (
    BusEvent,
    EventBus,
    RunStateChangedEvent,
    TargetSnapshotEvent,
)
from app.fanout.supervisor import HEARTBEAT_STALE_AFTER

if TYPE_CHECKING:  # pragma: no cover
    from app.domain.target import TargetUiState
    from app.domain.worker_state import WorkerSnapshot


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class FakeSupervisor:
    """Deterministic in-memory supervisor for HTTP-layer tests.

    Operations are synchronous in the test's view (no Argon2, no
    subprocess, no DB); state transitions go through the real
    `RunState` machine for fidelity but skip all the spawn-side
    machinery.
    """

    def __init__(
        self,
        *,
        bus: EventBus | None = None,
    ) -> None:
        self._run_state: RunState = RunState.OFFLINE
        self._run_state_changed_at: datetime | None = None
        self._targets: dict[str, Target] = {}
        self._bus: EventBus | None = bus
        self._heartbeat_ns: int = time.monotonic_ns()
        self._stop_event = asyncio.Event()
        self._ready_event = asyncio.Event()
        self.calls: list[tuple[str, dict[str, object]]] = []
        """Test-visible journal of every public method invocation."""

    # ------------------------------------------------------------------
    # Lifecycle (mirrors the real Supervisor.run/wait_ready/stop)
    # ------------------------------------------------------------------

    async def run(self, bus: EventBus) -> None:
        """Park until stop(); record the bus reference."""
        self._bus = bus
        self._heartbeat_ns = time.monotonic_ns()
        self._ready_event.set()
        await self._stop_event.wait()

    async def wait_ready(self, timeout: float) -> None:  # noqa: ASYNC109 — matches real Supervisor signature
        async with asyncio.timeout(timeout):
            await self._ready_event.wait()

    async def stop(self, grace: timedelta) -> None:
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Run control (called by handlers)
    # ------------------------------------------------------------------

    async def start_run(self) -> None:
        self.calls.append(("start_run", {}))
        if self._run_state != RunState.OFFLINE:
            from app.domain.errors import IllegalRunStateTransitionError
            from app.domain.run_state import RunAction

            raise IllegalRunStateTransitionError(
                state=self._run_state, action=RunAction.START_REQUESTED
            )
        self._transition_to(RunState.STARTING, cause="user pressed START")
        self._transition_to(RunState.ARMED, cause="(fake) workers spawned")

    async def stop_run(
        self, *, grace: timedelta = timedelta(seconds=5), reason: str = "user_stop"
    ) -> None:
        self.calls.append(("stop_run", {"grace": grace.total_seconds(), "reason": reason}))
        if self._run_state in (RunState.OFFLINE, RunState.ERROR):
            return
        self._transition_to(RunState.STOPPING, cause=f"stop requested ({reason})")
        self._transition_to(RunState.OFFLINE, cause="all workers drained")

    async def enable_target(self, target_id: str) -> None:
        self.calls.append(("enable_target", {"target_id": target_id}))

    async def disable_target(self, target_id: str) -> None:
        self.calls.append(("disable_target", {"target_id": target_id}))

    async def reset_target_worker(self, target_id: str, role: WorkerRole) -> None:
        self.calls.append(("reset_target_worker", {"target_id": target_id, "role": role.value}))

    @asynccontextmanager
    async def acquire_state_lock(self) -> AsyncIterator[None]:
        """Mirror of `Supervisor.acquire_state_lock` for rotate-passphrase
        ordering tests. The fake has no underlying state to protect, so
        the cm is a no-op — but the rotate-passphrase handler imports
        `supervisor.acquire_state_lock` and asserting the call shape
        matches production is the whole point of FakeSupervisor."""
        self.calls.append(("acquire_state_lock", {}))
        yield

    def notify_obs_publish_began(self) -> None:
        self.calls.append(("notify_obs_publish_began", {}))
        if self._run_state == RunState.ARMED:
            self._transition_to(RunState.LIVE, cause="OBS started publishing")

    def notify_obs_publish_ended(self) -> None:
        self.calls.append(("notify_obs_publish_ended", {}))
        if self._run_state == RunState.LIVE:
            self._transition_to(RunState.ARMED, cause="OBS stopped publishing")

    # ------------------------------------------------------------------
    # Read surface (mirrors Supervisor.run_state / targets / heartbeat)
    # ------------------------------------------------------------------

    def run_state(self) -> RunState:
        return self._run_state

    def run_state_changed_at(self) -> datetime | None:
        return self._run_state_changed_at

    def targets(self) -> tuple[Target, ...]:
        return tuple(self._targets.values())

    def heartbeat_age(self) -> timedelta:
        elapsed_ns = time.monotonic_ns() - self._heartbeat_ns
        return timedelta(microseconds=elapsed_ns / 1000)

    def heartbeat_is_stale(self) -> bool:
        return self.heartbeat_age() > HEARTBEAT_STALE_AFTER

    def recent_log_lines(
        self, target_id: str, role: WorkerRole = WorkerRole.PRIMARY
    ) -> tuple[bytes, ...]:
        _ = target_id, role
        return ()

    # ------------------------------------------------------------------
    # Test-only helpers
    # ------------------------------------------------------------------

    def add_target(self, target: Target) -> None:
        """Register a domain Target so `targets()` returns it."""
        self._targets[target.id] = target

    def freeze_heartbeat(self, age: timedelta) -> None:
        """Force heartbeat_age() to report a specific value (for /readyz
        tests that need to drive the stale path)."""
        self._heartbeat_ns = time.monotonic_ns() - int(age.total_seconds() * 1_000_000_000)

    def inject_target_snapshot(
        self,
        target_id: str,
        ui_state: TargetUiState,
        snapshots_by_role: tuple[WorkerSnapshot, ...] = (),
    ) -> None:
        """Push a TargetSnapshotEvent onto the real bus so WS / HTTP
        handlers see it as if the real supervisor had emitted it."""
        if self._bus is None:
            return
        self._bus.put(
            BusEvent(
                at=_utc_now(),
                monotonic_ns=time.monotonic_ns(),
                target_id=target_id,
                payload=TargetSnapshotEvent(
                    target_id=target_id,
                    ui_state=ui_state,
                    snapshots_by_role=snapshots_by_role,
                ),
            )
        )

    def _transition_to(self, new_state: RunState, *, cause: str) -> None:
        previous = self._run_state
        self._run_state = new_state
        self._run_state_changed_at = _utc_now()
        self._heartbeat_ns = time.monotonic_ns()
        if self._bus is None:
            return
        self._bus.put(
            BusEvent(
                at=_utc_now(),
                monotonic_ns=time.monotonic_ns(),
                target_id=None,
                payload=RunStateChangedEvent(
                    new_state=new_state, previous_state=previous, cause=cause
                ),
            )
        )


__all__ = ["FakeSupervisor"]
