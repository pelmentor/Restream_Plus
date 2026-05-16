"""Shared pytest fixtures.

The crypto tests do not need I/O — each module is unit-testable as a
pure function. The fixtures here are conveniences shared across
several test modules (a clean env, a temporary data dir, a fresh
credential registry).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from app.logging_setup import get_credential_registry


@pytest.fixture
def restream_clean_env(monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: PT004 — side-effect fixture used via `usefixtures`
    """Strip every RESTREAM_* var from os.environ for the test scope.

    The test suite must not be sensitive to the developer's shell env. Tests
    that need a particular value set it explicitly via `monkeypatch.setenv`.
    """
    for key in list(os.environ):
        if key.upper().startswith("RESTREAM_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """A fresh `/data`-equivalent directory.

    Pre-creates `logs/` so log-writing code paths don't need to create it.
    """
    (tmp_path / "logs").mkdir()
    return tmp_path


@pytest.fixture
def fresh_credential_registry() -> Iterator[None]:  # noqa: PT004 — side-effect fixture used via `usefixtures`
    """Ensure the global credential registry starts and ends empty.

    The redaction processor reads the global registry, so test isolation
    requires we wipe it. Runs `clear()` before and after the test.
    """
    registry = get_credential_registry()
    registry.clear()
    yield
    registry.clear()
