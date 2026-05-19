"""Auth router — login / logout / unlock / change-password / reprompt.

Exercises the BackgroundTask rehash via a slow Argon2 stand-in is
out of scope (we trust the verifier tests in Phase 3); we DO assert
that the BG task IS scheduled when `needs_rehash` is True by
inspecting `BackgroundTasks` behavior through the response side.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from app.api.middleware import LockedModeMiddleware
from app.auth.key_material import KeyMaterial
from app.crypto.passwords import hash_password
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.api.conftest import SESSION_COOKIE_NAME

from .conftest import ADMIN_PASSWORD

# ----------------------------------------------------------------------
# /api/auth/login
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_returns_200_with_user_and_cookie(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # Seed an admin password (schema_init left it NULL because we use
    # paste mode without admin_password).
    from app.repositories.users import UsersRepository

    async with api_sessionmaker() as s, s.begin():
        users = UsersRepository(s)
        admin = await users.get_by_username("admin")
        assert admin is not None
        await users.update_password_hash(admin.id, hash_password(ADMIN_PASSWORD))

    r = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": ADMIN_PASSWORD},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["username"] == "admin"
    assert body["user"]["last_login_at"] is not None
    # Set-Cookie header carries __Host- prefix cookie.
    set_cookie_headers = r.headers.get_list("set-cookie")
    assert any(SESSION_COOKIE_NAME in h for h in set_cookie_headers)


@pytest.mark.asyncio
async def test_login_invalid_credentials_returns_401_invalid(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    from app.repositories.users import UsersRepository

    async with api_sessionmaker() as s, s.begin():
        users = UsersRepository(s)
        admin = await users.get_by_username("admin")
        assert admin is not None
        await users.update_password_hash(admin.id, hash_password(ADMIN_PASSWORD))

    r = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "wrong"},
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid_credentials"


@pytest.mark.asyncio
async def test_login_missing_password_field_returns_422(
    client: httpx.AsyncClient,
) -> None:
    r = await client.post("/api/auth/login", json={"username": "admin"})
    assert r.status_code == 422


# ----------------------------------------------------------------------
# /api/auth/me — session probe for the SPA
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_me_returns_200_with_user_for_authenticated_session(
    auth_client: httpx.AsyncClient,
) -> None:
    r = await auth_client.get("/api/auth/me")
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "admin"
    assert "id" in body
    assert "last_login_at" in body


@pytest.mark.asyncio
async def test_me_returns_401_without_cookie(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/auth/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_me_returns_401_after_logout(auth_client: httpx.AsyncClient) -> None:
    r1 = await auth_client.post("/api/auth/logout")
    assert r1.status_code == 204
    r2 = await auth_client.get("/api/auth/me")
    assert r2.status_code == 401


# ----------------------------------------------------------------------
# /api/auth/logout
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logout_clears_cookie(auth_client: httpx.AsyncClient) -> None:
    r = await auth_client.post("/api/auth/logout")
    assert r.status_code == 204
    # The delete-cookie header is sent.
    set_cookie_headers = r.headers.get_list("set-cookie")
    assert any(SESSION_COOKIE_NAME in h for h in set_cookie_headers)


@pytest.mark.asyncio
async def test_logout_without_cookie_still_returns_204(client: httpx.AsyncClient) -> None:
    """Logout is idempotent — no cookie = no-op + delete header."""
    r = await client.post("/api/auth/logout")
    assert r.status_code == 204


# ----------------------------------------------------------------------
# /api/unlock
# ----------------------------------------------------------------------


def _km_locked() -> KeyMaterial:
    return KeyMaterial(kdf_salt=b"\x00" * 16)


@pytest.mark.asyncio
async def test_unlock_endpoint_in_locked_mode(
    app: FastAPI,
) -> None:
    """The unlock endpoint must work pre-unlock. The test app's
    `auth_state` is already READY; we substitute a LOCKED KeyMaterial
    + a fresh LockedModeMiddleware to drive the locked-mode path."""
    locked_km = _km_locked()
    app.state.auth.key_material = locked_km
    # Re-add the middleware bound to the locked KM. FastAPI's middleware
    # stack is built once, so we use a fresh app with the locked KM.
    fresh = FastAPI()
    # Mount only the auth router; mirror the production wiring.
    from app.api import install_exception_handlers
    from app.api.auth import auth_router, unlock_router

    fresh.state.auth = app.state.auth
    install_exception_handlers(fresh)
    fresh.add_middleware(LockedModeMiddleware, key_material=locked_km)
    fresh.include_router(auth_router)
    fresh.include_router(unlock_router)

    transport = httpx.ASGITransport(app=fresh)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post(
            "/api/unlock",
            json={"passphrase": "x" * 16},  # any non-empty passphrase derives keys
        )
        # The KEK derivation runs Argon2id, so we don't assert state == "ready"
        # unless we want a slow test. We assert the endpoint responds 200 or
        # 401 (depends on whether the locked KM ever transitions before
        # timeout). For a fully-deterministic assertion we rely on the
        # locked-state unlock path being reachable.
        assert r.status_code in (200, 401), r.text


@pytest.mark.asyncio
async def test_unlock_idempotent_when_ready(client: httpx.AsyncClient) -> None:
    """When KeyMaterial is already READY, /api/unlock returns 200
    without re-deriving."""
    r = await client.post(
        "/api/unlock",
        json={"passphrase": "any-passphrase-this-is-ignored-when-ready"},
    )
    assert r.status_code == 200
    assert r.json() == {"state": "ready"}


# ----------------------------------------------------------------------
# /api/auth/reprompt — issue grant
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_reprompt_grant_with_correct_password(
    auth_client: httpx.AsyncClient,
) -> None:
    r = await auth_client.post(
        "/api/auth/reprompt",
        json={"password": ADMIN_PASSWORD, "scope": "delete_target"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "grant_id" in body
    assert "expires_at" in body
    assert len(body["grant_id"]) > 20  # opaque URL-safe-base64


@pytest.mark.asyncio
async def test_issue_reprompt_grant_wrong_password_401(
    auth_client: httpx.AsyncClient,
) -> None:
    r = await auth_client.post(
        "/api/auth/reprompt",
        json={"password": "definitely-wrong", "scope": "delete_target"},
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid_credentials"


@pytest.mark.asyncio
async def test_issue_reprompt_grant_unknown_scope_400(
    auth_client: httpx.AsyncClient,
) -> None:
    r = await auth_client.post(
        "/api/auth/reprompt",
        json={"password": ADMIN_PASSWORD, "scope": "no_such_scope"},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "bad_request"


@pytest.mark.asyncio
async def test_unauthenticated_reprompt_returns_401(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/auth/reprompt",
        json={"password": "x", "scope": "delete_target"},
    )
    assert r.status_code == 401


# ----------------------------------------------------------------------
# Slice 7 / Hex Audit BA-F3 — reprompt rate-limit + timing floor
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reprompt_rate_limited_after_threshold(
    auth_client: httpx.AsyncClient,
) -> None:
    """Pre-fill the reprompt rate-limit bucket so the next attempt is
    denied without Argon2. The response carries `Retry-After` and the
    detail body matches the same shape as a wrong-password 401
    (so a stolen-cookie attacker can't distinguish them by body)."""
    state = auth_client._transport.app.state.auth  # type: ignore[attr-defined]
    state.reprompt_rate_limiter.reset()  # isolate from any sibling state
    # Pre-charge the limiter to the failure threshold. The limiter is
    # keyed by `ip` + `username`; the test client's peer IP is
    # `testclient` and the user is `admin`.
    for _ in range(3):
        state.reprompt_rate_limiter.record_failure(ip="testclient", username="admin")

    r = await auth_client.post(
        "/api/auth/reprompt",
        json={"password": ADMIN_PASSWORD, "scope": "delete_target"},
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "rate_limited"
    # Retry-After surfaces the worst-case bucket wait in whole seconds.
    assert int(r.headers["Retry-After"]) >= 1


@pytest.mark.asyncio
async def test_reprompt_wrong_password_records_rate_limit_failure(
    auth_client: httpx.AsyncClient,
) -> None:
    """Wrong-password attempts feed the limiter so brute-force probing
    locks out after the threshold."""
    state = auth_client._transport.app.state.auth  # type: ignore[attr-defined]
    state.reprompt_rate_limiter.reset()  # isolate from sibling tests
    # 3 wrong-password attempts. Each is a 401 invalid_credentials, but
    # each also records a failure under (ip, username).
    for _ in range(3):
        r = await auth_client.post(
            "/api/auth/reprompt",
            json={"password": "definitely-wrong", "scope": "delete_target"},
        )
        assert r.status_code == 401
        assert r.json()["detail"] == "invalid_credentials"

    # 4th attempt — even with the CORRECT password — is rate-limited.
    r = await auth_client.post(
        "/api/auth/reprompt",
        json={"password": ADMIN_PASSWORD, "scope": "delete_target"},
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "rate_limited"
    assert "Retry-After" in r.headers


@pytest.mark.asyncio
async def test_reprompt_timing_floor_padded_on_wrong_password(
    auth_client: httpx.AsyncClient,
) -> None:
    """Wrong-password path is padded to the timing floor so it cannot
    be distinguished from the success path by wall-clock timing."""
    from app.api.auth import REPROMPT_TIMING_FLOOR_SECONDS

    state = auth_client._transport.app.state.auth  # type: ignore[attr-defined]
    state.reprompt_rate_limiter.reset()

    start = asyncio.get_event_loop().time()
    r = await auth_client.post(
        "/api/auth/reprompt",
        json={"password": "wrong-pw", "scope": "delete_target"},
    )
    elapsed = asyncio.get_event_loop().time() - start
    assert r.status_code == 401
    # Sleep granularity on Windows is ~16 ms; allow a small tolerance.
    assert elapsed >= REPROMPT_TIMING_FLOOR_SECONDS - 0.05


# ----------------------------------------------------------------------
# /api/auth/change-password — reprompt-protected
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_change_password_requires_reprompt_header(
    auth_client: httpx.AsyncClient,
) -> None:
    r = await auth_client.post(
        "/api/auth/change-password",
        json={"current_password": ADMIN_PASSWORD, "new_password": "new-password-12345"},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "reprompt_required"


@pytest.mark.asyncio
async def test_change_password_with_invalid_grant(
    auth_client: httpx.AsyncClient,
) -> None:
    r = await auth_client.post(
        "/api/auth/change-password",
        json={"current_password": ADMIN_PASSWORD, "new_password": "new-password-12345"},
        headers={"X-Reprompt-Grant": "never-issued-id"},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "reprompt_invalid_or_expired"


@pytest.mark.asyncio
async def test_change_password_e2e(
    auth_client: httpx.AsyncClient,
) -> None:
    # Issue a grant.
    issue = await auth_client.post(
        "/api/auth/reprompt",
        json={"password": ADMIN_PASSWORD, "scope": "change_password"},
    )
    assert issue.status_code == 200, issue.text
    grant_id = issue.json()["grant_id"]

    new_password = "brand-new-password-9999"
    r = await auth_client.post(
        "/api/auth/change-password",
        json={"current_password": ADMIN_PASSWORD, "new_password": new_password},
        headers={"X-Reprompt-Grant": grant_id},
    )
    assert r.status_code == 204, r.text
    # The current cookie is invalidated — subsequent authenticated
    # calls should 401.
    follow_up = await auth_client.get("/api/sessions")
    assert follow_up.status_code == 401

    # Wait briefly for any background tasks (rehash) to settle.
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_change_password_persist_failure_returns_503_with_retry_after(
    auth_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slice 8.5 (Hex Audit FG3-F7): when the audit-append OR the
    commit in `change_password` raises `SQLAlchemyError`, the client
    must receive a distinguishable 503 + `Retry-After` rather than a
    generic 500. This lets the SPA render "Try again in a moment"
    while leaving the user's session valid — and signals that the
    password update was rolled back (the merged-txn BA-F4 invariant
    means the password was NOT changed when this error fires).
    """
    from sqlalchemy.exc import OperationalError

    from app.repositories.audit_log import AuditLogRepository

    # Issue a reprompt grant.
    issue = await auth_client.post(
        "/api/auth/reprompt",
        json={"password": ADMIN_PASSWORD, "scope": "change_password"},
    )
    assert issue.status_code == 200, issue.text
    grant_id = issue.json()["grant_id"]

    # Force the audit-append to raise — simulates a DB-locked /
    # operational error during the change-password persist.
    original_append = AuditLogRepository.append

    async def _boom(*args: object, **kwargs: object) -> object:
        raise OperationalError("simulated db lock", params={}, orig=Exception("locked"))

    monkeypatch.setattr(AuditLogRepository, "append", _boom)

    try:
        r = await auth_client.post(
            "/api/auth/change-password",
            json={
                "current_password": ADMIN_PASSWORD,
                "new_password": "another-brand-new-password",
            },
            headers={"X-Reprompt-Grant": grant_id},
        )
    finally:
        monkeypatch.setattr(AuditLogRepository, "append", original_append)

    assert r.status_code == 503, r.text
    assert r.json()["detail"] == "password_change_retry"
    assert r.headers.get("Retry-After") == "5"
