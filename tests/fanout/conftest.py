"""Fixtures for the fanout test suite.

Reuses the persistence + auth patterns from `tests/db` and `tests/auth`.
The `key_material_ready` fixture pays the Argon2id cost once per test
module by caching at module scope.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from app.auth.key_material import KeyMaterial
from app.config import AppSettings
from app.db import create_engine, initialize_schema, make_sessionmaker
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

_FIXED_KDF_SALT = b"\x00" * 16
_FIXED_PASSPHRASE = "test-passphrase-for-fanout-suite"


def _build_settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        passphrase_source="paste",
        data_dir=tmp_path,
        # Supply admin_password so schema_init takes the env-set path
        # rather than autogen-+-print to test stdout.
        admin_password="fanout-test-admin-pw-12345",
        # Phase 10 §Q1 / I-Q1: production default flipped to True; this
        # suite does not run nginx — opt out to match the contract.
        healthz_check_nginx=False,
    )


@pytest.fixture
def settings(tmp_path: Path) -> AppSettings:
    return _build_settings(tmp_path)


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
async def key_material_ready() -> KeyMaterial:
    """Real `KeyMaterial` in READY state. Pays one Argon2id pass.

    Function-scoped because `KeyMaterial` is per-process global from
    the application's perspective, and we want full test isolation.
    The ~100 ms cost is acceptable; tests that don't need it use the
    sync FFmpegWorker fixture in `test_ffmpeg_worker.py`.
    """
    km = KeyMaterial(kdf_salt=_FIXED_KDF_SALT)
    await km.unlock(_FIXED_PASSPHRASE)
    return km
