"""Health endpoints — /livez (always 200), /readyz + /healthz (composite)."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from app.fanout._testing import FakeSupervisor


@pytest.mark.asyncio
async def test_livez_returns_200(client: httpx.AsyncClient) -> None:
    r = await client.get("/livez")
    assert r.status_code == 200
    assert r.json() == {"status": "alive", "checks": {}}


@pytest.mark.asyncio
async def test_readyz_returns_200_when_healthy(client: httpx.AsyncClient) -> None:
    r = await client.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["checks"]["db"]["ok"] is True
    assert body["checks"]["supervisor"]["ok"] is True
    assert body["checks"]["passphrase"]["ok"] is True
    # Event bus check is always ok (non-gating); detail shows count.
    assert body["checks"]["event_bus"]["ok"] is True


@pytest.mark.asyncio
async def test_readyz_returns_503_when_heartbeat_stale(
    client: httpx.AsyncClient,
    fake_supervisor: FakeSupervisor,
) -> None:
    fake_supervisor.freeze_heartbeat(timedelta(seconds=30))
    r = await client.get("/readyz")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["supervisor"]["ok"] is False


@pytest.mark.asyncio
async def test_healthz_is_alias_for_readyz(client: httpx.AsyncClient) -> None:
    r1 = await client.get("/readyz")
    r2 = await client.get("/healthz")
    assert r1.status_code == r2.status_code
    # Same composite shape.
    assert set(r1.json()["checks"].keys()) == set(r2.json()["checks"].keys())
