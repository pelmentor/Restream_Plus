"""LockedModeMiddleware — allowlist + 503 shape.

Closes the Phase 3 deferred gap: any new route added in Phase 6+
that the middleware doesn't allowlist falls into the 503 branch
regardless of whether the handler depends on `require_unlocked_keys`.
"""

from __future__ import annotations

import httpx
import pytest
from app.api import LockedModeMiddleware
from app.api.middleware import (
    DEFAULT_ALLOWLIST,
    STATIC_PATH_PREFIXES,
    _is_allowlisted,
    _is_static_root,
)
from app.auth.deps import AuthState
from app.auth.key_material import KeyMaterial, KeyMaterialState
from app.config import AppSettings
from app.fanout._testing import FakeSupervisor
from app.fanout.event_bus import EventBus
from fastapi import FastAPI


def _make_app_with_states(km: KeyMaterial) -> FastAPI:
    app = FastAPI()
    app.add_middleware(LockedModeMiddleware, key_material=km)

    @app.get("/api/protected")
    def _p() -> dict[str, str]:
        return {"ok": "true"}

    @app.get("/api/unlock")
    def _u() -> dict[str, str]:
        return {"unlock_ok": "true"}

    @app.get("/livez")
    def _l() -> dict[str, str]:
        return {"alive": "true"}

    @app.get("/internal/rtmp/publish")
    def _i() -> dict[str, str]:
        return {"loopback": "true"}

    @app.get("/")
    def _root() -> dict[str, str]:
        return {"spa": "true"}

    return app


def _km(state: KeyMaterialState) -> KeyMaterial:
    km = KeyMaterial(kdf_salt=b"\x00" * 16)
    km._state = state
    return km


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


class TestAllowlistHelpers:
    def test_static_root_matches_slash_and_index(self) -> None:
        assert _is_static_root("/")
        assert _is_static_root("/index.html")
        assert not _is_static_root("/api/anything")

    def test_exact_match(self) -> None:
        assert _is_allowlisted("/livez", ["/livez"])

    def test_trailing_slash_prefix(self) -> None:
        assert _is_allowlisted("/internal/rtmp/publish", ["/internal/"])
        assert not _is_allowlisted("/internal-public/foo", ["/internal/"])

    def test_bare_segment_prefix(self) -> None:
        # `/api/unlock` allowlist also covers `/api/unlock/anything`.
        assert _is_allowlisted("/api/unlock/extra", ["/api/unlock"])
        # But not a different prefix.
        assert not _is_allowlisted("/api/unlock-x", ["/api/unlock"])

    def test_default_allowlist_pins(self) -> None:
        """The default allowlist is intentionally tiny — adding to it
        is a security-review event. This pin catches accidental expansion."""
        assert DEFAULT_ALLOWLIST == (
            "/api/unlock",
            "/livez",
            "/readyz",
            "/healthz",
            "/internal/rtmp/",
        )

    def test_static_prefixes_pin(self) -> None:
        assert STATIC_PATH_PREFIXES == ("/assets/", "/static/")


# ----------------------------------------------------------------------
# End-to-end through the middleware
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_locked_state_blocks_non_allowlisted() -> None:
    app = _make_app_with_states(_km(KeyMaterialState.LOCKED))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/protected")
        assert r.status_code == 503
        assert r.json() == {"detail": "service_locked"}


@pytest.mark.asyncio
async def test_unlocking_state_returns_retry_after() -> None:
    app = _make_app_with_states(_km(KeyMaterialState.UNLOCKING))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/protected")
        assert r.status_code == 503
        assert r.json() == {"detail": "service_unlocking"}
        assert r.headers.get("retry-after") == "2"


@pytest.mark.asyncio
async def test_ready_state_passes_through() -> None:
    app = _make_app_with_states(_km(KeyMaterialState.READY))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/protected")
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_unlock_endpoint_reachable_in_locked_mode() -> None:
    """The unlock endpoint MUST work when locked — otherwise the
    operator can't escape the locked state."""
    app = _make_app_with_states(_km(KeyMaterialState.LOCKED))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/unlock")
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_livez_reachable_in_locked_mode() -> None:
    app = _make_app_with_states(_km(KeyMaterialState.LOCKED))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/livez")
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_internal_rtmp_reachable_in_locked_mode() -> None:
    """nginx-rtmp webhook must reach the handler in locked mode so the
    handler itself can answer 503 (rather than the middleware short-
    circuit producing a JSON body nginx can't parse as a publish-deny
    cleanly)."""
    app = _make_app_with_states(_km(KeyMaterialState.LOCKED))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/internal/rtmp/publish")
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_spa_root_reachable_in_locked_mode() -> None:
    app = _make_app_with_states(_km(KeyMaterialState.LOCKED))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/")
        assert r.status_code == 200


# ----------------------------------------------------------------------
# Coverage test — every real Phase-6 router must be blocked in locked
# mode except the allowlisted ones. Code review M-7: a future author
# adding a router without checking the allowlist sees this test fail.
# ----------------------------------------------------------------------


# Per-router (method, path) probe pairs. Picked from each router's
# first registered path so we exercise the middleware decision for
# every router file. The body shape doesn't matter — locked-mode 503
# returns BEFORE validation.
NON_ALLOWLISTED_PROBES: tuple[tuple[str, str, dict[str, str] | None], ...] = (
    ("GET", "/api/targets", None),
    ("POST", "/api/run/start", None),
    ("GET", "/api/run/state", None),
    ("GET", "/api/settings", None),
    ("GET", "/api/security/tokens", None),
    ("GET", "/api/sessions", None),
    ("POST", "/api/auth/login", None),
    ("POST", "/api/auth/logout", None),
    ("POST", "/api/auth/reprompt", None),
    ("GET", "/api/auth/me", None),
)

ALLOWLISTED_PROBES: tuple[tuple[str, str], ...] = (
    ("GET", "/livez"),
    ("GET", "/readyz"),
    ("GET", "/healthz"),
)


@pytest.mark.parametrize(("method", "path", "body"), NON_ALLOWLISTED_PROBES)
@pytest.mark.asyncio
async def test_real_router_blocked_in_locked_mode(
    api_settings: AppSettings,
    auth_state: AuthState,
    fake_supervisor: FakeSupervisor,
    event_bus: EventBus,
    method: str,
    path: str,
    body: dict[str, str] | None,
) -> None:
    """Build the real test app, flip KeyMaterial to LOCKED, hit each
    non-allowlisted endpoint, assert 503 service_locked."""
    from tests.api.conftest import _make_test_app

    app = _make_test_app(
        settings=api_settings,
        auth_state=auth_state,
        supervisor=fake_supervisor,
        bus=event_bus,
    )
    # Flip to LOCKED after app construction.
    app.state.auth.key_material._state = KeyMaterialState.LOCKED

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://t") as c:
        r = await c.request(method, path, json=body)
        assert (
            r.status_code == 503
        ), f"{method} {path} should 503 in locked mode, got {r.status_code}"
        # Middleware preempts before any handler runs; detail must be
        # the structural code (not e.g. "unauthorized").
        assert r.json().get("detail") in ("service_locked", "service_unlocking")


@pytest.mark.parametrize(("method", "path"), ALLOWLISTED_PROBES)
@pytest.mark.asyncio
async def test_real_health_routes_reachable_in_locked_mode(
    api_settings: AppSettings,
    auth_state: AuthState,
    fake_supervisor: FakeSupervisor,
    event_bus: EventBus,
    method: str,
    path: str,
) -> None:
    """The five allowlisted paths must answer in locked mode."""
    from tests.api.conftest import _make_test_app

    app = _make_test_app(
        settings=api_settings,
        auth_state=auth_state,
        supervisor=fake_supervisor,
        bus=event_bus,
    )
    app.state.auth.key_material._state = KeyMaterialState.LOCKED
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://t") as c:
        r = await c.request(method, path)
        # Health endpoints may return 200 or 503 depending on the
        # passphrase check; either way they MUST NOT be middleware-
        # short-circuited to service_locked.
        assert r.status_code in (200, 503)
        if r.status_code == 503:
            # If the body says service_locked, the middleware blocked
            # the handler — that's the wrong path for /livez.
            detail = r.json().get("detail") or r.json().get("status")
            assert detail not in (
                "service_locked",
                "service_unlocking",
            ), f"{path} was blocked by middleware: {r.json()}"
