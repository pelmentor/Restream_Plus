"""Run router — async 202 start/stop + state read."""

from __future__ import annotations

import asyncio

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
