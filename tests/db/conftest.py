"""Fixtures for the persistence test suite.

Each test gets a fresh on-disk SQLite database (in `tmp_path`) so:
- WAL files (.db-wal, .db-shm) live alongside the main file just like
  in production, exposing any pragma/filesystem mismatch.
- Tests cannot leak state through a shared in-memory cache.
- Engine disposal at teardown releases Windows file locks before the
  tmp_path cleanup runs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from app.config import AppSettings
from app.db import create_engine, initialize_schema, make_sessionmaker
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker


def build_settings(tmp_path: Path, **overrides: object) -> AppSettings:
    """Construct an AppSettings rooted at tmp_path with paste-mode defaults.

    By default `admin_password` is left UNSET so callers exercise the
    ADR-0005 autogen path. Tests that don't care about the autogen
    behaviour should use the `settings` fixture (or pass an explicit
    `admin_password=` override) to silence the boot-time banner.
    """
    base: dict[str, object] = {
        "passphrase_source": "paste",
        "data_dir": tmp_path,
        # Phase 10 §Q1 / I-Q1: production default flipped to True; this
        # suite does not run nginx — opt out to match the contract.
        "healthz_check_nginx": False,
    }
    base.update(overrides)
    return AppSettings(**base)  # type: ignore[arg-type]


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "restream.db"


@pytest.fixture
def settings(tmp_path: Path) -> AppSettings:
    """Default settings: env-supplied admin_password so the autogen
    banner does not spam test stdout. Schema-init tests that exercise
    the autogen path construct settings via `build_settings()` directly."""
    return build_settings(tmp_path, admin_password="db-conftest-admin-pw-12345")


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
    """Engine with schema initialized (fresh DB, seeded rows)."""
    await initialize_schema(engine, settings)
    yield engine


@pytest_asyncio.fixture
async def sessionmaker(
    initialized_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return make_sessionmaker(initialized_engine)


@pytest_asyncio.fixture
async def session(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as s:
        yield s
