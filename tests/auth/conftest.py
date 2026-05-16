"""Shared fixtures for the auth test suite.

Reuses the persistence fixtures from `tests/db/conftest.py` for tests
that exercise the repository-backed paths (sessions, api_tokens).
The `derived_keys` and `key_material_ready` fixtures construct
deterministic key material so tests can predict HMAC outputs and
avoid the ~100 ms Argon2 cost.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from app.auth.key_material import DerivedKeys, KeyMaterial
from app.auth.rate_limit import LoginRateLimiter
from app.config import AppSettings
from app.db import create_engine, initialize_schema, make_sessionmaker
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

if TYPE_CHECKING:  # pragma: no cover
    pass


def build_settings(tmp_path: Path, **overrides: object) -> AppSettings:
    """Mirror of tests/db/conftest.py:build_settings.

    Duplicated rather than imported because pytest's collection order
    is not guaranteed and importing across conftests is fragile.
    """
    base: dict[str, object] = {
        "passphrase_source": "paste",
        "data_dir": tmp_path,
        "admin_password": "admin-password-test-12345",
        # Phase 10 §Q1 / I-Q1: production default flipped to True; this
        # suite does not run nginx — opt out to match the contract.
        "healthz_check_nginx": False,
    }
    base.update(overrides)
    return AppSettings(**base)  # type: ignore[arg-type]


@pytest.fixture
def derived_keys() -> DerivedKeys:
    """Deterministic, all-different-bytes key material.

    The three subkeys are visually distinct so a test that
    accidentally MACs with the wrong key produces a different
    fingerprint than expected.
    """
    return DerivedKeys(
        stream_key_kek=b"S" * 32,
        api_token_mac_key=b"A" * 32,
        session_token_mac_key=b"H" * 32,
    )


@pytest.fixture
def kdf_salt() -> bytes:
    """Deterministic 16-byte salt for KeyMaterial tests."""
    return b"\x00" * 16


@pytest.fixture
def key_material_locked(kdf_salt: bytes) -> KeyMaterial:
    """Fresh `KeyMaterial` in LOCKED state."""
    return KeyMaterial(kdf_salt=kdf_salt)


@pytest.fixture
def rate_limiter() -> LoginRateLimiter:
    """Fresh `LoginRateLimiter` per test for isolation.

    The default 5/15-min thresholds are wide enough that individual
    tests don't trip them; tests that exercise the limiter
    explicitly construct their own with tighter values."""
    return LoginRateLimiter()


@pytest.fixture
def settings(tmp_path: Path) -> AppSettings:
    return build_settings(tmp_path)


@pytest_asyncio.fixture
async def engine(settings: AppSettings) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(settings)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def initialized_engine(
    engine: AsyncEngine, settings: AppSettings
) -> AsyncIterator[AsyncEngine]:
    await initialize_schema(engine, settings)
    yield engine


@pytest_asyncio.fixture
async def sessionmaker_factory(
    initialized_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return make_sessionmaker(initialized_engine)


@pytest_asyncio.fixture
async def session(
    sessionmaker_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with sessionmaker_factory() as s:
        yield s


@pytest_asyncio.fixture
async def admin_user_id(
    sessionmaker_factory: async_sessionmaker[AsyncSession],
) -> str:
    """The id of the bootstrap admin row. Tests that create api_tokens
    need this to satisfy the `api_tokens.user_id` FK (Phase 6 schema
    change). `schema_init` seeds a single admin row with a fresh
    `uuid.uuid4().hex` id, so we look it up by username rather than
    pinning a value."""
    from app.repositories.users import UsersRepository

    async with sessionmaker_factory() as s:
        users = UsersRepository(s)
        row = await users.get_by_username("admin")
        assert row is not None, "schema_init must have seeded the admin row"
        return row.id
