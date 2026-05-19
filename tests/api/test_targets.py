"""Targets router — CRUD + credential set/clear + reset-worker.

The reprompt-protected delete is exercised end-to-end (issue grant →
use grant → delete). The supervisor is the FakeSupervisor from the
conftest; tests assert `supervisor.calls` to verify the handler-to-
supervisor wiring without spawning ffmpeg.
"""

from __future__ import annotations

import httpx
import pytest
from app.fanout._testing import FakeSupervisor

from .conftest import ADMIN_PASSWORD


async def _issue_grant(client: httpx.AsyncClient, scope: str) -> str:
    r = await client.post(
        "/api/auth/reprompt",
        json={"password": ADMIN_PASSWORD, "scope": scope},
    )
    assert r.status_code == 200, r.text
    return str(r.json()["grant_id"])


# ----------------------------------------------------------------------
# CRUD
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unauthenticated_list_returns_401(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/targets")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_empty_list(auth_client: httpx.AsyncClient) -> None:
    r = await auth_client.get("/api/targets")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_create_target_returns_201(
    auth_client: httpx.AsyncClient,
    fake_supervisor: FakeSupervisor,
) -> None:
    r = await auth_client.post(
        "/api/targets",
        json={
            "type": "twitch",
            "label": "Main",
            "url": "rtmp://live.twitch.tv/app",
            "enabled": True,
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["type"] == "twitch"
    assert body["label"] == "Main"
    assert body["enabled"] is True
    assert body["has_credential"] is False
    # Supervisor was notified.
    assert any(call[0] == "enable_target" for call in fake_supervisor.calls)


@pytest.mark.asyncio
async def test_create_target_unknown_type_returns_422(auth_client: httpx.AsyncClient) -> None:
    """Pydantic rejects unknown enum values at validation; 422 (not 400)."""
    r = await auth_client.post(
        "/api/targets",
        json={"type": "myspace", "label": "x", "url": "rtmp://x"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_get_missing_target_404(auth_client: httpx.AsyncClient) -> None:
    r = await auth_client.get("/api/targets/does-not-exist")
    assert r.status_code == 404
    assert r.json()["detail"] == "target_not_found"


@pytest.mark.asyncio
async def test_update_target(auth_client: httpx.AsyncClient) -> None:
    created = await auth_client.post(
        "/api/targets",
        json={"type": "twitch", "label": "Old", "url": "rtmp://a"},
    )
    target_id = created.json()["id"]

    r = await auth_client.patch(
        f"/api/targets/{target_id}",
        json={"label": "New"},
    )
    assert r.status_code == 200
    assert r.json()["label"] == "New"


@pytest.mark.asyncio
async def test_delete_target_requires_reprompt(auth_client: httpx.AsyncClient) -> None:
    created = await auth_client.post(
        "/api/targets",
        json={"type": "twitch", "label": "x", "url": "rtmp://x"},
    )
    target_id = created.json()["id"]
    r = await auth_client.delete(f"/api/targets/{target_id}")
    assert r.status_code == 403
    assert r.json()["detail"] == "reprompt_required"


@pytest.mark.asyncio
async def test_delete_target_with_valid_grant(
    auth_client: httpx.AsyncClient,
    fake_supervisor: FakeSupervisor,
) -> None:
    created = await auth_client.post(
        "/api/targets",
        json={"type": "twitch", "label": "x", "url": "rtmp://x"},
    )
    target_id = created.json()["id"]
    grant = await _issue_grant(auth_client, "delete_target")

    r = await auth_client.delete(
        f"/api/targets/{target_id}",
        headers={"X-Reprompt-Grant": grant},
    )
    assert r.status_code == 204
    # Supervisor disable_target was called.
    assert any(
        call == ("disable_target", {"target_id": target_id}) for call in fake_supervisor.calls
    )
    # Subsequent GET is 404.
    follow = await auth_client.get(f"/api/targets/{target_id}")
    assert follow.status_code == 404


# ----------------------------------------------------------------------
# Credential set / clear
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_credential_returns_summary(
    auth_client: httpx.AsyncClient,
    fake_supervisor: FakeSupervisor,
) -> None:
    created = await auth_client.post(
        "/api/targets",
        json={"type": "twitch", "label": "x", "url": "rtmp://x"},
    )
    target_id = created.json()["id"]

    r = await auth_client.put(
        f"/api/targets/{target_id}/credential",
        json={"stream_key": "live_12345_AbCdEf"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["last4"] == "CdEf"
    assert body["lifetime"] == "persistent"
    # GET shows has_credential = True.
    g = await auth_client.get(f"/api/targets/{target_id}")
    assert g.json()["has_credential"] is True
    assert g.json()["credential_last4"] == "CdEf"
    # Supervisor was notified to re-pick up the credential.
    assert any(call[0] == "enable_target" for call in fake_supervisor.calls)


@pytest.mark.asyncio
async def test_vk_credential_has_per_session_lifetime(auth_client: httpx.AsyncClient) -> None:
    created = await auth_client.post(
        "/api/targets",
        json={"type": "vk_live", "label": "v", "url": "rtmp://vk"},
    )
    target_id = created.json()["id"]
    r = await auth_client.put(
        f"/api/targets/{target_id}/credential",
        json={"stream_key": "vk-session-key"},
    )
    assert r.status_code == 200
    assert r.json()["lifetime"] == "per_session"


@pytest.mark.asyncio
async def test_clear_credential_requires_reprompt(
    auth_client: httpx.AsyncClient,
) -> None:
    """Security review H-3: clearing a credential silently knocks the
    target offline. Reprompt is required."""
    created = await auth_client.post(
        "/api/targets",
        json={"type": "kick", "label": "k", "url": "rtmps://kick"},
    )
    target_id = created.json()["id"]
    r = await auth_client.delete(f"/api/targets/{target_id}/credential")
    assert r.status_code == 403
    assert r.json()["detail"] == "reprompt_required"


@pytest.mark.asyncio
async def test_clear_credential_with_grant_idempotent(
    auth_client: httpx.AsyncClient,
) -> None:
    created = await auth_client.post(
        "/api/targets",
        json={"type": "kick", "label": "k", "url": "rtmps://kick"},
    )
    target_id = created.json()["id"]
    # Set then clear.
    await auth_client.put(
        f"/api/targets/{target_id}/credential",
        json={"stream_key": "k-key-abcdefgh"},
    )
    grant1 = await _issue_grant(auth_client, "clear_credential")
    r1 = await auth_client.delete(
        f"/api/targets/{target_id}/credential",
        headers={"X-Reprompt-Grant": grant1},
    )
    assert r1.status_code == 204
    # Second clear is idempotent (target still exists, no credential).
    grant2 = await _issue_grant(auth_client, "clear_credential")
    r2 = await auth_client.delete(
        f"/api/targets/{target_id}/credential",
        headers={"X-Reprompt-Grant": grant2},
    )
    assert r2.status_code == 204


# ----------------------------------------------------------------------
# Reset worker
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_worker_requires_reprompt(auth_client: httpx.AsyncClient) -> None:
    """Hex Audit BA-F13 (slice 10): no grant → 403 reprompt_required."""
    created = await auth_client.post(
        "/api/targets",
        json={"type": "twitch", "label": "x", "url": "rtmp://x"},
    )
    target_id = created.json()["id"]
    r = await auth_client.post(
        f"/api/targets/{target_id}/reset-worker?role=primary",
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "reprompt_required"


@pytest.mark.asyncio
async def test_reset_worker_calls_supervisor(
    auth_client: httpx.AsyncClient,
    fake_supervisor: FakeSupervisor,
) -> None:
    # Hex Audit BA-F13 (slice 10): the reset-worker endpoint is
    # reprompt-protected. Tests that exercise the happy path must
    # issue a `reset_target_worker`-scoped grant first.
    created = await auth_client.post(
        "/api/targets",
        json={"type": "twitch", "label": "x", "url": "rtmp://x"},
    )
    target_id = created.json()["id"]
    grant = await _issue_grant(auth_client, "reset_target_worker")
    r = await auth_client.post(
        f"/api/targets/{target_id}/reset-worker?role=primary",
        headers={"X-Reprompt-Grant": grant},
    )
    assert r.status_code == 204
    assert any(
        call[0] == "reset_target_worker" and call[1]["target_id"] == target_id
        for call in fake_supervisor.calls
    )
