"""Run router — async 202 start/stop + state read."""

from __future__ import annotations

import asyncio
import contextlib

import httpx
import pytest
from app.domain.run_state import RunState
from app.fanout._testing import FakeSupervisor


@pytest.mark.asyncio
async def test_state_endpoint_returns_offline_initially(
    auth_client: httpx.AsyncClient,
) -> None:
    r = await auth_client.get("/api/run/state")
    assert r.status_code == 200
    body = r.json()
    assert body["run_state"] == RunState.OFFLINE.value
    assert body["targets"] == []
    assert "heartbeat_age_seconds" in body
    assert body["dropped_total"] == 0
    # Phase 8 §A: None until the supervisor has ever transitioned.
    assert body["run_state_changed_at"] is None


@pytest.mark.asyncio
async def test_state_endpoint_includes_run_state_changed_at_after_start(
    auth_client: httpx.AsyncClient,
    fake_supervisor: FakeSupervisor,
) -> None:
    # Before any transition.
    r0 = await auth_client.get("/api/run/state")
    assert r0.json()["run_state_changed_at"] is None

    # Drive a transition.
    await auth_client.post("/api/run/start")
    await asyncio.sleep(0.05)

    r1 = await auth_client.get("/api/run/state")
    body = r1.json()
    assert body["run_state"] == RunState.ARMED.value
    assert body["run_state_changed_at"] is not None
    # ISO 8601 with offset (AwareDatetime).
    assert "T" in body["run_state_changed_at"]
    _ = fake_supervisor  # touched via the HTTP path


@pytest.mark.asyncio
async def test_start_returns_202_and_schedules_supervisor(
    auth_client: httpx.AsyncClient,
    fake_supervisor: FakeSupervisor,
) -> None:
    r = await auth_client.post("/api/run/start")
    assert r.status_code == 202
    body = r.json()
    assert body["accepted"] is True
    assert body["previous_state"] == RunState.OFFLINE.value
    # Yield so the fire-and-forget task runs.
    await asyncio.sleep(0.05)
    assert any(call[0] == "start_run" for call in fake_supervisor.calls)
    # FakeSupervisor synchronously transitions to ARMED on start_run.
    assert fake_supervisor.run_state() == RunState.ARMED


@pytest.mark.asyncio
async def test_double_start_returns_409(
    auth_client: httpx.AsyncClient,
    fake_supervisor: FakeSupervisor,
) -> None:
    r1 = await auth_client.post("/api/run/start")
    assert r1.status_code == 202
    await asyncio.sleep(0.05)
    assert fake_supervisor.run_state() == RunState.ARMED
    # Second START is illegal from ARMED.
    r2 = await auth_client.post("/api/run/start")
    assert r2.status_code == 409
    assert r2.json()["detail"] == "illegal_run_state"


@pytest.mark.asyncio
async def test_stop_from_armed_returns_202(
    auth_client: httpx.AsyncClient,
    fake_supervisor: FakeSupervisor,
) -> None:
    await auth_client.post("/api/run/start")
    await asyncio.sleep(0.05)
    assert fake_supervisor.run_state() == RunState.ARMED
    r = await auth_client.post("/api/run/stop")
    assert r.status_code == 202
    assert r.json()["previous_state"] == RunState.ARMED.value
    await asyncio.sleep(0.05)
    assert fake_supervisor.run_state() == RunState.OFFLINE


@pytest.mark.asyncio
async def test_stop_when_offline_returns_409(auth_client: httpx.AsyncClient) -> None:
    r = await auth_client.post("/api/run/stop")
    assert r.status_code == 409
    assert r.json()["detail"] == "illegal_run_state"


@pytest.mark.asyncio
async def test_unauthenticated_start_401(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/run/start")
    assert r.status_code == 401


# ----------------------------------------------------------------------
# Hex Audit BA-F2 (2026-05-18): fire-and-forget done-callback logging
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_log_supervisor_task_exception_silent_on_cancelled() -> None:
    """A cancelled task is silent — propagation is the caller's job."""

    from app.api.run import _log_supervisor_task_exception

    async def _noop() -> None:
        await asyncio.sleep(0)

    task = asyncio.create_task(_noop(), name="x")
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    # No raise expected:
    _log_supervisor_task_exception(task)


@pytest.mark.asyncio
async def test_log_supervisor_task_exception_demotes_illegal_transition() -> None:
    """`IllegalRunStateTransitionError` is the expected race outcome
    on a concurrent START / STOP; we log at info, not error.

    Uses `structlog.testing.capture_logs()` because the project's
    structlog setup writes directly to sys.stderr via
    `PrintLoggerFactory` — `caplog` (which intercepts stdlib logging)
    sees nothing.
    """
    import structlog.testing
    from app.api.run import _log_supervisor_task_exception
    from app.domain.errors import IllegalRunStateTransitionError
    from app.domain.run_state import RunAction, RunState

    async def _raises() -> None:
        raise IllegalRunStateTransitionError(state=RunState.ARMED, action=RunAction.START_REQUESTED)

    with structlog.testing.capture_logs() as captured:
        task = asyncio.create_task(_raises(), name="run-start-race")
        with contextlib.suppress(IllegalRunStateTransitionError):
            await task
        _log_supervisor_task_exception(task)

    race_lost = [r for r in captured if r.get("event") == "supervisor_task_race_lost"]
    assert race_lost, f"Expected race-lost info event; got {captured}"
    assert race_lost[0]["log_level"] == "info"
    assert race_lost[0]["task_name"] == "run-start-race"
    assert race_lost[0]["state"] == "armed"
    assert race_lost[0]["action"] == "start_requested"


@pytest.mark.asyncio
async def test_log_supervisor_task_exception_surfaces_other_errors() -> None:
    """Any non-Illegal exception is logged at error with traceback so
    a supervisor crash isn't lost behind the 202 response."""
    import structlog.testing
    from app.api.run import _log_supervisor_task_exception

    async def _boom() -> None:
        raise RuntimeError("synthetic supervisor blow-up")

    with structlog.testing.capture_logs() as captured:
        task = asyncio.create_task(_boom(), name="run-start-crash")
        with contextlib.suppress(RuntimeError):
            await task
        _log_supervisor_task_exception(task)

    failed = [r for r in captured if r.get("event") == "supervisor_task_failed"]
    assert failed, f"Expected supervisor_task_failed event; got {captured}"
    assert failed[0]["log_level"] == "error"
    assert failed[0]["task_name"] == "run-start-crash"
