"""Shared Phase 6 test fixtures.

`_make_test_app` mounts every Phase 6 router on a fresh FastAPI app
and wires `app.state` the same way the real lifespan does — minus
the asyncio TaskGroup, which would otherwise need its own scaffold
per test. The `LockedModeMiddleware` is registered up front so the
allowlist invariants get exercised.

Use the `client` fixture for unauthenticated requests, and
`auth_client` for cookie-authenticated ones. Both use
`httpx.AsyncClient(ASGITransport)` so no real port binding happens.

The `FakeSupervisor` is attached as `app.state.supervisor`; tests can
read `app.state.supervisor.calls` to assert handler-to-supervisor
wiring without spawning ffmpeg.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
import pytest_asyncio
from app.api import LockedModeMiddleware, install_exception_handlers
from app.api.api_tokens_api import router as api_tokens_router
from app.api.auth import auth_router, unlock_router
from app.api.health import router as health_router
from app.api.internal_mtx import router as internal_mtx_router
from app.api.internal_rtmp import router as internal_rtmp_router
from app.api.run import router as run_router
from app.api.security_api import about_router, security_router
from app.api.sessions_history import router as sessions_history_router
from app.api.settings_api import router as settings_router
from app.api.targets import router as targets_router
from app.api.ws import WsRegistry
from app.api.ws import router as ws_router
from app.auth.deps import AuthState
from app.auth.key_material import DerivedKeys, KeyMaterial, KeyMaterialState
from app.auth.last_seen_coalescer import LastSeenCoalescer
from app.auth.rate_limit import LoginRateLimiter
from app.auth.reprompts import RepromptStore
from app.auth.sessions import session_cookie_name
from app.config import AppSettings
from app.crypto.passwords import hash_password
from app.db import create_engine, initialize_schema, make_sessionmaker
from app.fanout._testing import FakeSupervisor
from app.fanout.event_bus import EventBus
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

if TYPE_CHECKING:  # pragma: no cover
    pass


ADMIN_PASSWORD = "admin-password-phase6-test"

# Tests run with `cookie_secure=False` per _build_settings; use the
# matching cookie name (`rp_session`, no `__Host-` prefix) everywhere
# in the test suite so httpx persists the cookie naturally on http://.
SESSION_COOKIE_NAME = session_cookie_name(secure=False)


def _build_settings(tmp_path: Path, **overrides: object) -> AppSettings:
    base: dict[str, object] = {
        "passphrase_source": "paste",
        "data_dir": tmp_path,
        "admin_password": ADMIN_PASSWORD,
        # Phase 10 §Q1: production default is True, but tests don't run
        # nginx — opt out to keep /readyz green in the suite. Individual
        # tests that want to exercise the nginx probe override.
        "healthz_check_nginx": False,
        # v1.1.2: the test client runs over http:// so the default
        # secure cookie (`__Host-` prefix + Secure flag) would be
        # dropped by httpx — every test before this added a manual
        # cookie-injection workaround in conftest. By flipping
        # cookie_secure=False the cookie name becomes `rp_session`
        # (no `__Host-`), Secure flag is omitted, and httpx persists
        # the cookie naturally on plain HTTP. Tests that want to
        # exercise the secure cookie path explicitly override.
        "cookie_secure": False,
    }
    base.update(overrides)
    return AppSettings(**base)  # type: ignore[arg-type]


@pytest.fixture
def api_settings(tmp_path: Path) -> AppSettings:
    return _build_settings(tmp_path)


@pytest_asyncio.fixture
async def api_engine(api_settings: AppSettings) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(api_settings)
    try:
        await initialize_schema(engine, api_settings)
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def api_sessionmaker(
    api_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return make_sessionmaker(api_engine)


@pytest_asyncio.fixture
async def admin_user_id_fixture(
    api_sessionmaker: async_sessionmaker[AsyncSession],
) -> str:
    """The schema-init-seeded admin id. Same idea as
    `tests/auth/conftest.py::admin_user_id` but local to this suite
    to avoid cross-conftest fragility."""
    from app.repositories.users import UsersRepository

    async with api_sessionmaker() as s:
        admin = await UsersRepository(s).get_by_username("admin")
        assert admin is not None
        return admin.id


@pytest.fixture
def derived_keys_fixture() -> DerivedKeys:
    return DerivedKeys(
        stream_key_kek=b"S" * 32,
        api_token_mac_key=b"A" * 32,
        session_token_mac_key=b"H" * 32,
    )


@pytest_asyncio.fixture
async def ready_key_material(
    derived_keys_fixture: DerivedKeys,
) -> KeyMaterial:
    """KeyMaterial pre-forced to READY, bypassing Argon2id.

    The tests use deterministic test keys; the real KeyMaterial.unlock
    path is exercised by `tests/auth/test_key_material.py` already, so
    we don't pay the ~100 ms Argon2 cost per test here.
    """
    km = KeyMaterial(kdf_salt=b"\x00" * 16)
    km._keys = derived_keys_fixture
    km._state = KeyMaterialState.READY
    return km


@pytest_asyncio.fixture
async def reprompts() -> RepromptStore:
    return RepromptStore()


@pytest_asyncio.fixture
async def event_bus() -> EventBus:
    return EventBus()


@pytest_asyncio.fixture
async def fake_supervisor(event_bus: EventBus) -> FakeSupervisor:
    return FakeSupervisor(bus=event_bus)


@pytest_asyncio.fixture
async def auth_state(
    ready_key_material: KeyMaterial,
    api_sessionmaker: async_sessionmaker[AsyncSession],
    reprompts: RepromptStore,
) -> AuthState:
    return AuthState(
        key_material=ready_key_material,
        sessionmaker=api_sessionmaker,
        rate_limiter=LoginRateLimiter(),
        unlock_rate_limiter=LoginRateLimiter(failure_limit=3, window_seconds=900.0),
        reprompt_rate_limiter=LoginRateLimiter(failure_limit=3, window_seconds=900.0),
        reprompts=reprompts,
        last_seen_coalescer=LastSeenCoalescer(),
        trusted_proxies=(),
    )


def _make_test_app(
    *,
    settings: AppSettings,
    auth_state: AuthState,
    supervisor: FakeSupervisor,
    bus: EventBus,
) -> FastAPI:
    """Mount every Phase 6 router on a fresh app, skipping the lifespan.

    The lifespan-side wiring (Supervisor TaskGroup, ws_broadcaster,
    reprompt_pruner) is exercised by `tests/api/test_main.py`; per-route
    tests skip it and inject the wiring directly so tests stay sub-second.
    """
    app = FastAPI()
    app.state.settings = settings
    app.state.auth = auth_state
    app.state.supervisor = supervisor
    app.state.event_bus = bus
    app.state.ws_registry = WsRegistry()
    app.state.supervisor_tasks = set()

    install_exception_handlers(app)
    app.add_middleware(LockedModeMiddleware, key_material=auth_state.key_material)

    app.include_router(auth_router)
    app.include_router(unlock_router)
    app.include_router(targets_router)
    app.include_router(run_router)
    app.include_router(settings_router)
    app.include_router(api_tokens_router)
    app.include_router(sessions_history_router)
    app.include_router(internal_rtmp_router)
    app.include_router(internal_mtx_router)
    app.include_router(security_router)
    app.include_router(about_router)
    app.include_router(ws_router)
    app.include_router(health_router)
    return app


@pytest_asyncio.fixture
async def app(
    api_settings: AppSettings,
    auth_state: AuthState,
    fake_supervisor: FakeSupervisor,
    event_bus: EventBus,
) -> FastAPI:
    """The test FastAPI app — fresh per test."""
    a = _make_test_app(
        settings=api_settings,
        auth_state=auth_state,
        supervisor=fake_supervisor,
        bus=event_bus,
    )
    # Phase 9 GET /api/about reads `app.state.started_at` directly. The
    # lifespan captures it in production; tests bypass the lifespan, so
    # we set it here. Pinned to an obviously-fake distant-past timestamp
    # so uptime calculations in tests are non-zero and predictable.
    from datetime import UTC, datetime

    a.state.started_at = datetime(2026, 5, 16, 0, 0, 0, tzinfo=UTC)
    return a


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """`base_url` uses `https://` so the Secure-only `__Host-` cookie is
    sent on subsequent requests. ASGITransport doesn't actually open a
    socket — `https://` here is just the URL scheme that satisfies
    httpx's cookie-jar Secure flag."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as c:
        yield c


@pytest_asyncio.fixture
async def auth_client(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[AsyncSession],
) -> httpx.AsyncClient:
    """Pre-authenticated client. The bootstrap admin row was seeded
    with NULL password (env-mode disabled), so we set a password
    inline and then log in. Tests get a client with the session
    cookie already attached."""
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
    assert r.status_code == 200, r.text
    # The Set-Cookie header carries the session value; httpx's
    # AsyncClient auto-stores it into client.cookies for subsequent
    # requests when the cookie attributes match the base_url. The
    # __Host- prefix requires Secure + Path=/; httpx may strip it
    # because testserver is http://. Set it manually as a fallback.
    if SESSION_COOKIE_NAME not in client.cookies:
        # Extract from Set-Cookie header.
        from http.cookies import SimpleCookie

        sc = SimpleCookie()
        for header in r.headers.get_list("set-cookie"):
            sc.load(header)
        if SESSION_COOKIE_NAME in sc:
            client.cookies.set(SESSION_COOKIE_NAME, sc[SESSION_COOKIE_NAME].value)
    return client
