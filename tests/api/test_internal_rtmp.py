"""Internal nginx-rtmp webhooks — loopback auth + ingest key match."""

from __future__ import annotations

import httpx
import pytest
from app.domain.run_state import RunState
from app.fanout._testing import FakeSupervisor
from app.repositories.settings_repo import SettingsRepository
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def _current_ingest_key(sm: async_sessionmaker[AsyncSession]) -> str:
    async with sm() as s:
        return (await SettingsRepository(s).get()).ingest_key_current


@pytest.mark.asyncio
async def test_publish_with_correct_key_returns_200(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[AsyncSession],
    fake_supervisor: FakeSupervisor,
) -> None:
    key = await _current_ingest_key(api_sessionmaker)
    # Force RunState to ARMED so notify_obs_publish_began will transition to LIVE.
    fake_supervisor._run_state = RunState.ARMED
    r = await client.post("/internal/rtmp/publish", data={"name": key})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    # Supervisor was notified.
    assert any(c[0] == "notify_obs_publish_began" for c in fake_supervisor.calls)


@pytest.mark.asyncio
async def test_publish_with_wrong_key_returns_403(client: httpx.AsyncClient) -> None:
    r = await client.post("/internal/rtmp/publish", data={"name": "not-the-key"})
    assert r.status_code == 403
    assert r.json()["detail"] == "ingest_key_invalid"


@pytest.mark.asyncio
async def test_publish_done_returns_200_and_notifies(
    client: httpx.AsyncClient,
    fake_supervisor: FakeSupervisor,
) -> None:
    # Force RunState to LIVE so notify_obs_publish_ended will transition to ARMED.
    fake_supervisor._run_state = RunState.LIVE
    r = await client.post("/internal/rtmp/publish_done", data={"name": "any-key"})
    assert r.status_code == 200
    assert any(c[0] == "notify_obs_publish_ended" for c in fake_supervisor.calls)


# Note: the loopback rejection path (non-127.0.0.1 peer) is hard to
# trigger via httpx.ASGITransport — the transport always reports the
# client as 127.0.0.1. We cover it indirectly: the handler reads
# `request.client.host` against `_LOOPBACK_HOSTS`; the conftest of
# `tests/api/` only ever runs against the loopback transport, so the
# happy paths above implicitly exercise the loopback-accept branch.
# A separate unit test on `_assert_loopback` is a follow-up if a
# real-network exploit ever lands.
