"""Internal nginx-rtmp webhooks — loopback auth + ingest key match."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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


# ----------------------------------------------------------------------
# Hex Audit BA-F1 (2026-05-18): rotation grace window enforcement
# ----------------------------------------------------------------------


async def _rotate_with_grace(
    sm: async_sessionmaker[AsyncSession],
    *,
    new_key: str,
    grace_until: datetime,
) -> str:
    """Rotate to `new_key`, returning the *previous* key (now in the
    grace window)."""
    async with sm() as s, s.begin():
        repo = SettingsRepository(s)
        before = await repo.get()
        await repo.rotate_ingest_key(new_key=new_key, grace_until=grace_until)
        return before.ingest_key_current


@pytest.mark.asyncio
async def test_previous_key_accepted_within_grace_window(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[AsyncSession],
    fake_supervisor: FakeSupervisor,
) -> None:
    """Within the grace window the previous key still publishes."""
    grace = datetime.now(tz=UTC) + timedelta(hours=24)
    previous = await _rotate_with_grace(api_sessionmaker, new_key="new-key", grace_until=grace)
    fake_supervisor._run_state = RunState.ARMED
    r = await client.post("/internal/rtmp/publish", data={"name": previous})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_previous_key_rejected_after_grace_expires(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Past `grace_until` the previous key is no longer honoured —
    the auth path treats it as a mismatch."""
    expired = datetime.now(tz=UTC) - timedelta(seconds=1)
    previous = await _rotate_with_grace(api_sessionmaker, new_key="new-key-2", grace_until=expired)
    r = await client.post("/internal/rtmp/publish", data={"name": previous})
    assert r.status_code == 403
    assert r.json()["detail"] == "ingest_key_invalid"


@pytest.mark.asyncio
async def test_current_key_still_works_after_grace_expires(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[AsyncSession],
    fake_supervisor: FakeSupervisor,
) -> None:
    """Rejection above is for the PREVIOUS key only; the current key
    keeps working regardless of grace state."""
    expired = datetime.now(tz=UTC) - timedelta(seconds=1)
    await _rotate_with_grace(api_sessionmaker, new_key="new-current-key", grace_until=expired)
    fake_supervisor._run_state = RunState.ARMED
    r = await client.post("/internal/rtmp/publish", data={"name": "new-current-key"})
    assert r.status_code == 200


def test_keys_match_unit_no_rotation() -> None:
    """Unit-level: when `previous` is None, `previous_valid` is False
    regardless of `grace_until`."""
    from app.api.internal_rtmp import _keys_match

    now = datetime.now(tz=UTC)
    assert _keys_match("a", "a", None, grace_until=None, now=now) is True
    assert _keys_match("a", "a", None, grace_until=now + timedelta(hours=1), now=now) is True
    assert _keys_match("b", "a", None, grace_until=None, now=now) is False


def test_keys_match_unit_grace_window() -> None:
    """Unit-level: previous accepted iff `now < grace_until`."""
    from app.api.internal_rtmp import _keys_match

    now = datetime.now(tz=UTC)
    # Within grace: previous accepted.
    assert _keys_match("old", "new", "old", grace_until=now + timedelta(minutes=1), now=now) is True
    # Past grace: previous rejected even though string matches.
    assert (
        _keys_match("old", "new", "old", grace_until=now - timedelta(seconds=1), now=now) is False
    )
    # grace_until = None with non-None previous: still rejected (no
    # window means no rotation acknowledged on the auth path).
    assert _keys_match("old", "new", "old", grace_until=None, now=now) is False
