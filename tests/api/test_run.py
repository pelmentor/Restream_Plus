"""Run router — GET /api/run/state.

Per ADR-0015 auto-run-on-publish there are no manual /api/run/start or
/api/run/stop endpoints; the supervisor starts and stops in response to
OBS publish events on the internal RTMP webhook. These tests cover the
remaining state-read surface plus the negative cases that prove the
removed POST routes are not registered.
"""

from __future__ import annotations

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
async def test_state_endpoint_reflects_publish_driven_transition(
    auth_client: httpx.AsyncClient,
    fake_supervisor: FakeSupervisor,
) -> None:
    # Before any publish webhook fires.
    r0 = await auth_client.get("/api/run/state")
    assert r0.json()["run_state_changed_at"] is None

    # ADR-0015: webhook drives OFFLINE → STARTING → ARMED → LIVE.
    fake_supervisor.notify_obs_publish_began()

    r1 = await auth_client.get("/api/run/state")
    body = r1.json()
    assert body["run_state"] == RunState.LIVE.value
    assert body["run_state_changed_at"] is not None
    # ISO 8601 with offset (AwareDatetime).
    assert "T" in body["run_state_changed_at"]


@pytest.mark.asyncio
async def test_start_endpoint_is_not_registered(
    auth_client: httpx.AsyncClient,
) -> None:
    # ADR-0015 auto-run-on-publish: removing the manual control plane.
    r = await auth_client.post("/api/run/start")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_stop_endpoint_is_not_registered(
    auth_client: httpx.AsyncClient,
) -> None:
    r = await auth_client.post("/api/run/stop")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_unauthenticated_state_401(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/run/state")
    assert r.status_code == 401
