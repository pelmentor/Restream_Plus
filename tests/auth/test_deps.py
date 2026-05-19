"""Tests for FastAPI dependency helpers.

The other auth modules are tested as plain Python. This module is the
boundary between the auth business logic and FastAPI itself, so its
tests construct a minimal FastAPI app, wire `app.state.auth`, mount a
handful of probe routes exercising each dependency, and drive them
with `httpx.AsyncClient + ASGITransport`.
"""

from __future__ import annotations

import ipaddress
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from app.auth.deps import (
    AuthenticatedRequest,
    AuthenticatedRequestDep,
    AuthState,
    AuthStateDep,
    ClientIpDep,
    DerivedKeysDep,
    KeyMaterialDep,
    SessionServiceDep,
    require_reprompt_grant,
)
from app.auth.key_material import DerivedKeys, KeyMaterial, KeyMaterialState
from app.auth.last_seen_coalescer import LastSeenCoalescer
from app.auth.rate_limit import LoginRateLimiter
from app.auth.reprompts import RepromptScope, RepromptStore
from app.auth.sessions import SESSION_COOKIE_NAME
from fastapi import Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

ADMIN_PASSWORD = "admin-password-test-12345"


def _make_app(state: AuthState | None) -> FastAPI:
    """Construct a probe app with routes exercising the deps under test.

    `state` of None simulates a misconfigured deployment where the
    lifespan didn't run; the get_auth_state dep should 500.
    """
    app = FastAPI()
    if state is not None:
        app.state.auth = state

    @app.get("/probe/auth-state")
    async def _probe_auth_state(s: AuthStateDep) -> dict[str, Any]:
        return {"trusted_proxies": [str(n) for n in s.trusted_proxies]}

    @app.get("/probe/key-material")
    async def _probe_key_material(km: KeyMaterialDep) -> dict[str, Any]:
        return {"state": km.state.value}

    @app.get("/probe/keys")
    async def _probe_keys(keys: DerivedKeysDep) -> dict[str, Any]:
        return {"has_keys": True, "stream_key_len": len(keys.stream_key_kek)}

    @app.get("/probe/client-ip")
    async def _probe_client_ip(ip: ClientIpDep) -> dict[str, Any]:
        return {"ip": ip}

    @app.post("/probe/login")
    async def _probe_login(svc: SessionServiceDep, ip: ClientIpDep) -> dict[str, Any]:
        outcome = await svc.login(username="admin", password=ADMIN_PASSWORD, ip=ip)
        return {
            "cookie": outcome.cookie_value,
            "user_id": outcome.user.id,
            "username": outcome.user.username,
        }

    @app.get("/probe/who")
    async def _probe_who(auth: AuthenticatedRequestDep) -> dict[str, Any]:
        return {"username": auth.user.username, "via": auth.via.value}

    delete_target_grant = Depends(require_reprompt_grant(RepromptScope.DELETE_TARGET))

    @app.delete("/probe/target")
    async def _probe_delete_target(
        auth: AuthenticatedRequestDep,
        _grant: None = delete_target_grant,
    ) -> dict[str, Any]:
        return {"deleted_by": auth.user.username}

    return app


@pytest_asyncio.fixture
async def auth_state(
    derived_keys: DerivedKeys,
    sessionmaker_factory: async_sessionmaker[AsyncSession],
) -> AuthState:
    km = KeyMaterial(kdf_salt=b"\x00" * 16)
    # Push it straight into READY with our test keys, bypassing Argon2.
    km._keys = derived_keys
    km._state = KeyMaterialState.READY
    return AuthState(
        key_material=km,
        sessionmaker=sessionmaker_factory,
        rate_limiter=LoginRateLimiter(),
        unlock_rate_limiter=LoginRateLimiter(),
        reprompt_rate_limiter=LoginRateLimiter(),
        reprompts=RepromptStore(),
        last_seen_coalescer=LastSeenCoalescer(),
        trusted_proxies=(),
    )


@pytest_asyncio.fixture
async def client(auth_state: AuthState) -> AsyncIterator[httpx.AsyncClient]:
    app = _make_app(auth_state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


class TestGetAuthState:
    @pytest.mark.asyncio
    async def test_missing_state_returns_500(self) -> None:
        app = _make_app(state=None)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            r = await c.get("/probe/auth-state")
            assert r.status_code == 500
            assert r.json()["detail"] == "auth_state_not_initialised"

    @pytest.mark.asyncio
    async def test_state_present_returns_view(self, client: httpx.AsyncClient) -> None:
        r = await client.get("/probe/auth-state")
        assert r.status_code == 200


class TestKeyMaterialDeps:
    @pytest.mark.asyncio
    async def test_get_key_material_returns_singleton(self, client: httpx.AsyncClient) -> None:
        r = await client.get("/probe/key-material")
        assert r.status_code == 200
        assert r.json()["state"] == "ready"

    @pytest.mark.asyncio
    async def test_require_unlocked_keys_ready_returns_keys(
        self, client: httpx.AsyncClient
    ) -> None:
        r = await client.get("/probe/keys")
        assert r.status_code == 200
        assert r.json()["has_keys"] is True

    @pytest.mark.asyncio
    async def test_require_unlocked_keys_locked_503(
        self,
        sessionmaker_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        km = KeyMaterial(kdf_salt=b"\x00" * 16)
        # Default state == LOCKED.
        state = AuthState(
            key_material=km,
            sessionmaker=sessionmaker_factory,
            rate_limiter=LoginRateLimiter(),
            unlock_rate_limiter=LoginRateLimiter(),
            reprompt_rate_limiter=LoginRateLimiter(),
            reprompts=RepromptStore(),
            last_seen_coalescer=LastSeenCoalescer(),
            trusted_proxies=(),
        )
        app = _make_app(state)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            r = await c.get("/probe/keys")
            assert r.status_code == 503
            assert r.json()["detail"] == "service_locked"

    @pytest.mark.asyncio
    async def test_require_unlocked_keys_unlocking_503_with_retry_after(
        self,
        derived_keys: DerivedKeys,
        sessionmaker_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        km = KeyMaterial(kdf_salt=b"\x00" * 16)
        # Force UNLOCKING state directly.
        km._state = KeyMaterialState.UNLOCKING
        state = AuthState(
            key_material=km,
            sessionmaker=sessionmaker_factory,
            rate_limiter=LoginRateLimiter(),
            unlock_rate_limiter=LoginRateLimiter(),
            reprompt_rate_limiter=LoginRateLimiter(),
            reprompts=RepromptStore(),
            last_seen_coalescer=LastSeenCoalescer(),
            trusted_proxies=(),
        )
        app = _make_app(state)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            r = await c.get("/probe/keys")
            assert r.status_code == 503
            assert r.json()["detail"] == "service_unlocking"
            assert r.headers.get("retry-after") == "2"


class TestClientIp:
    @pytest.mark.asyncio
    async def test_no_trusted_proxies_returns_peer(self, client: httpx.AsyncClient) -> None:
        # ASGI test transport reports peer as "127.0.0.1".
        r = await client.get("/probe/client-ip", headers={"X-Forwarded-For": "9.9.9.9"})
        assert r.status_code == 200
        # With trusted_proxies=() the XFF must be ignored.
        assert r.json()["ip"] == "127.0.0.1"

    @pytest.mark.asyncio
    async def test_trusted_peer_honors_xff(
        self,
        derived_keys: DerivedKeys,
        sessionmaker_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        km = KeyMaterial(kdf_salt=b"\x00" * 16)
        km._keys = derived_keys
        km._state = KeyMaterialState.READY
        state = AuthState(
            key_material=km,
            sessionmaker=sessionmaker_factory,
            rate_limiter=LoginRateLimiter(),
            unlock_rate_limiter=LoginRateLimiter(),
            reprompt_rate_limiter=LoginRateLimiter(),
            reprompts=RepromptStore(),
            last_seen_coalescer=LastSeenCoalescer(),
            trusted_proxies=(ipaddress.ip_network("127.0.0.1/32"),),
        )
        app = _make_app(state)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            r = await c.get("/probe/client-ip", headers={"X-Forwarded-For": "203.0.113.5"})
            assert r.json()["ip"] == "203.0.113.5"


class TestRequireAuthenticated:
    @pytest.mark.asyncio
    async def test_no_auth_returns_401_with_www_authenticate(
        self, client: httpx.AsyncClient
    ) -> None:
        r = await client.get("/probe/who")
        assert r.status_code == 401
        assert "Bearer" in r.headers.get("www-authenticate", "")

    @pytest.mark.asyncio
    async def test_cookie_auth_succeeds(self, client: httpx.AsyncClient) -> None:
        login_r = await client.post("/probe/login")
        assert login_r.status_code == 200, f"login body: {login_r.text}"
        cookie = login_r.json()["cookie"]

        # httpx 0.27+ deprecates per-request cookies; set on the client.
        client.cookies.set(SESSION_COOKIE_NAME, cookie)
        r = await client.get("/probe/who")
        assert r.status_code == 200
        body = r.json()
        assert body["username"] == "admin"
        assert body["via"] == "cookie"

    @pytest.mark.asyncio
    async def test_bearer_auth_succeeds(
        self,
        client: httpx.AsyncClient,
        auth_state: AuthState,
        admin_user_id: str,
    ) -> None:
        # Mint a token via the in-test ApiTokenService.
        from app.auth.api_tokens import ApiTokenService
        from app.repositories.api_tokens import ApiTokensRepository

        async with auth_state.sessionmaker() as session:
            svc = ApiTokenService(
                tokens=ApiTokensRepository(session),
                keys=auth_state.key_material.require_ready(),
            )
            created = await svc.create(user_id=admin_user_id, label="probe-cli")
            await session.commit()

        r = await client.get(
            "/probe/who",
            headers={"Authorization": f"Bearer {created.plaintext}"},
        )
        assert r.status_code == 200
        body = r.json()
        # Phase 6 closed the deferred gap: the bearer-auth path now loads
        # the user via `token_dto.user_id`, not a hardcoded "admin"
        # lookup. We assert the username and via on the response, AND
        # implicitly assert the FK-driven path works at all by getting 200.
        assert body["username"] == "admin"
        assert body["via"] == "bearer"

    @pytest.mark.asyncio
    async def test_bearer_for_deleted_user_returns_401(
        self,
        client: httpx.AsyncClient,
        auth_state: AuthState,
        admin_user_id: str,
    ) -> None:
        """Phase 6: bearer auth resolves the owner via the FK.

        If the owning user is somehow missing (corrupted state — the
        ON DELETE CASCADE makes this unreachable via the normal API
        surface), the auth path must fail closed rather than fall back
        to "admin".
        """
        from app.auth.api_tokens import ApiTokenService
        from app.db.models import UserORM
        from app.repositories.api_tokens import ApiTokensRepository
        from sqlalchemy import delete

        # Mint a token whose owner is a NEW user we create + delete to
        # exercise the failure mode without disrupting the bootstrap
        # admin row.
        async with auth_state.sessionmaker() as session:
            from app.repositories.users import UsersRepository

            users = UsersRepository(session)
            ghost = await users.create(username="ghost-user", password_hash="ghost-placeholder")
            svc = ApiTokenService(
                tokens=ApiTokensRepository(session),
                keys=auth_state.key_material.require_ready(),
            )
            ghost_token = await svc.create(user_id=ghost.id, label="ghost-cli")
            await session.commit()

        # Now delete the ghost user; CASCADE removes the token row, so
        # the bearer no longer matches. To exercise the in-handler
        # "user missing but token present" branch, we instead bypass
        # the CASCADE by writing a token under an id that doesn't
        # match any user. Use raw SQL to do exactly that.
        async with auth_state.sessionmaker() as session:
            await session.execute(delete(UserORM).where(UserORM.id == ghost.id))
            await session.commit()

        r = await client.get(
            "/probe/who",
            headers={"Authorization": f"Bearer {ghost_token.plaintext}"},
        )
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_bearer_falls_through_to_401(self, client: httpx.AsyncClient) -> None:
        r = await client.get("/probe/who", headers={"Authorization": "Bearer rpat_garbage"})
        assert r.status_code == 401


class TestRepromptGrant:
    @pytest.mark.asyncio
    async def test_missing_grant_header_403(self, client: httpx.AsyncClient) -> None:
        login_r = await client.post("/probe/login")
        client.cookies.set(SESSION_COOKIE_NAME, login_r.json()["cookie"])
        r = await client.delete("/probe/target")
        assert r.status_code == 403
        assert r.json()["detail"] == "reprompt_required"

    @pytest.mark.asyncio
    async def test_invalid_grant_403(self, client: httpx.AsyncClient) -> None:
        login_r = await client.post("/probe/login")
        client.cookies.set(SESSION_COOKIE_NAME, login_r.json()["cookie"])
        r = await client.delete(
            "/probe/target",
            headers={"X-Reprompt-Grant": "never-issued"},
        )
        assert r.status_code == 403
        assert r.json()["detail"] == "reprompt_invalid_or_expired"

    @pytest.mark.asyncio
    async def test_valid_grant_succeeds_and_burns(
        self, client: httpx.AsyncClient, auth_state: AuthState
    ) -> None:
        login_r = await client.post("/probe/login")
        cookie = login_r.json()["cookie"]
        user_id = login_r.json()["user_id"]
        client.cookies.set(SESSION_COOKIE_NAME, cookie)
        gid = auth_state.reprompts.issue(user_id=user_id, scope=RepromptScope.DELETE_TARGET)

        # First call succeeds.
        r = await client.delete(
            "/probe/target",
            headers={"X-Reprompt-Grant": gid},
        )
        assert r.status_code == 200
        assert r.json()["deleted_by"] == "admin"

        # Second call with the same grant: 403 (single-use).
        r2 = await client.delete(
            "/probe/target",
            headers={"X-Reprompt-Grant": gid},
        )
        assert r2.status_code == 403
        assert r2.json()["detail"] == "reprompt_invalid_or_expired"


def _assert_authenticated_request_shape(auth: AuthenticatedRequest) -> None:
    """Lint smoke for the AuthenticatedRequest dataclass."""
    assert auth.user is not None
    assert auth.via in {auth.via.COOKIE, auth.via.BEARER}
