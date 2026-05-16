"""Settings + API tokens + sessions-history routers.

Compact coverage of the secondary routers; the major behaviours
(reprompt protection, audit) are exercised in test_targets.py and
test_auth.py.
"""

from __future__ import annotations

import httpx
import pytest

from .conftest import ADMIN_PASSWORD


async def _issue(client: httpx.AsyncClient, scope: str) -> str:
    r = await client.post(
        "/api/auth/reprompt",
        json={"password": ADMIN_PASSWORD, "scope": scope},
    )
    assert r.status_code == 200, r.text
    return str(r.json()["grant_id"])


# ----------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_settings_masks_ingest_key(auth_client: httpx.AsyncClient) -> None:
    r = await auth_client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    # No `ingest_key_current` field — only the last4.
    assert "ingest_key_current" not in body
    assert "ingest_key_last4" in body
    assert len(body["ingest_key_last4"]) <= 4


@pytest.mark.asyncio
async def test_patch_settings_updates_values(auth_client: httpx.AsyncClient) -> None:
    r = await auth_client.patch("/api/settings", json={"idle_timeout_seconds": 1200})
    assert r.status_code == 200
    assert r.json()["idle_timeout_seconds"] == 1200


@pytest.mark.asyncio
async def test_rotate_ingest_key_returns_plaintext_once(
    auth_client: httpx.AsyncClient,
) -> None:
    grant = await _issue(auth_client, "regenerate_ingest_key")
    r = await auth_client.post(
        "/api/settings/rotate-ingest-key",
        headers={"X-Reprompt-Grant": grant},
    )
    assert r.status_code == 200
    body = r.json()
    assert "plaintext" in body
    assert "last4" in body
    assert body["plaintext"].endswith(body["last4"])
    # Subsequent GET shows the new last4 + has_previous=True.
    g = await auth_client.get("/api/settings")
    assert g.json()["ingest_key_last4"] == body["last4"]
    assert g.json()["ingest_key_has_previous"] is True


# ----------------------------------------------------------------------
# API tokens
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_token_returns_plaintext_once(
    auth_client: httpx.AsyncClient,
) -> None:
    r = await auth_client.post(
        "/api/security/tokens",
        json={"label": "deploy-cli"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["plaintext"].startswith("rpat_")
    assert body["token"]["label"] == "deploy-cli"
    # List shows the token metadata, no plaintext.
    listing = await auth_client.get("/api/security/tokens")
    assert listing.status_code == 200
    assert len(listing.json()) == 1
    assert "token_hash" not in listing.json()[0]


@pytest.mark.asyncio
async def test_revoke_token_requires_reprompt(
    auth_client: httpx.AsyncClient,
) -> None:
    created = await auth_client.post(
        "/api/security/tokens",
        json={"label": "to-revoke"},
    )
    token_id = created.json()["token"]["id"]
    r = await auth_client.delete(f"/api/security/tokens/{token_id}")
    assert r.status_code == 403
    assert r.json()["detail"] == "reprompt_required"


@pytest.mark.asyncio
async def test_revoke_token_with_grant(auth_client: httpx.AsyncClient) -> None:
    created = await auth_client.post(
        "/api/security/tokens",
        json={"label": "to-revoke"},
    )
    token_id = created.json()["token"]["id"]
    grant = await _issue(auth_client, "revoke_api_token")
    r = await auth_client.delete(
        f"/api/security/tokens/{token_id}",
        headers={"X-Reprompt-Grant": grant},
    )
    assert r.status_code == 204


# ----------------------------------------------------------------------
# Sessions history
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sessions_history_empty_initially(auth_client: httpx.AsyncClient) -> None:
    r = await auth_client.get("/api/sessions")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["next_before"] is None
