"""WebSocket — cookie auth + envelope + state.full snapshot.

Starlette's `TestClient` is sync and speaks the in-memory ASGI WS
protocol; we drive WS tests through it. The login flow runs through
`auth_client` (async) first to seed the admin password and capture
the session cookie; we then re-use the cookie value in a sync
`TestClient` to open the WS.
"""

from __future__ import annotations

import httpx
import pytest
from app.auth.key_material import KeyMaterialState
from app.domain.run_state import RunState
from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.api.conftest import SESSION_COOKIE_NAME


def _ws_client_with_cookie(app: FastAPI, cookie_value: str | None) -> TestClient:
    c = TestClient(app, base_url="https://testserver")
    if cookie_value:
        c.cookies.set(SESSION_COOKIE_NAME, cookie_value)
    return c


@pytest.mark.asyncio
async def test_ws_rejects_unauthenticated_connect(
    app: FastAPI,
    auth_client: httpx.AsyncClient,  # ensures admin password seeded
) -> None:
    """No cookie → close 4401 (custom auth-fail code)."""
    _ = auth_client  # fixture seeds password but we deliberately don't reuse the cookie
    c = _ws_client_with_cookie(app, None)
    with pytest.raises(WebSocketDisconnect) as exc_info, c.websocket_connect("/ws"):
        pass
    assert exc_info.value.code == 4401


@pytest.mark.asyncio
async def test_ws_authenticated_connect_receives_state_full(
    app: FastAPI,
    auth_client: httpx.AsyncClient,
) -> None:
    cookie = auth_client.cookies.get(SESSION_COOKIE_NAME)
    assert cookie, "auth_client should have logged in by now"
    c = _ws_client_with_cookie(app, cookie)
    with c.websocket_connect("/ws") as ws:
        initial = ws.receive_json()
        assert initial["v"] == 1
        assert initial["event"] == "state.full"
        assert initial["data"]["run_state"] == RunState.OFFLINE.value
        assert initial["data"]["targets"] == []
        assert initial["data"]["dropped_total"] == 0
        # Phase 8 §A: field present, None on cold-boot.
        assert "run_state_changed_at" in initial["data"]
        assert initial["data"]["run_state_changed_at"] is None


@pytest.mark.asyncio
async def test_ws_rejects_in_locked_mode(
    app: FastAPI,
    auth_client: httpx.AsyncClient,
) -> None:
    """When KeyMaterial is LOCKED, the WS handler closes with 4503
    even if the cookie is otherwise valid."""
    cookie = auth_client.cookies.get(SESSION_COOKIE_NAME)
    assert cookie
    # Flip to LOCKED after login.
    app.state.auth.key_material._state = KeyMaterialState.LOCKED
    c = _ws_client_with_cookie(app, cookie)
    with pytest.raises(WebSocketDisconnect) as exc_info, c.websocket_connect("/ws"):
        pass
    assert exc_info.value.code == 4503
