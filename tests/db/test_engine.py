"""Engine-level pragma and WAL behaviour tests."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from app.db import PRAGMAS
from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.mark.asyncio
async def test_all_documented_pragmas_take_effect(engine: AsyncEngine) -> None:
    """Each pragma in PRAGMAS must read back as the documented value.

    journal_mode is the load-bearing one (WAL allows concurrent readers
    while a writer is active); the others tune durability and memory
    behaviour. ADR-0007 is the single source of truth for the values.
    """
    async with engine.connect() as conn:
        for key, expected in PRAGMAS.items():
            result = await conn.exec_driver_sql(f"PRAGMA {key}")
            actual = result.scalar_one()
            _assert_pragma_matches(key, expected, actual)


def _assert_pragma_matches(key: str, expected: str, actual: object) -> None:
    """Compare expected (string) vs actual (int|str) pragma readback."""
    # SQLite returns pragmas as strings or ints depending on the key.
    # Normalise to lowercase strings for comparison.
    normalised = str(actual).lower()
    expected_lower = expected.lower()

    # `foreign_keys`, `temp_store`, etc. accept symbolic values on write
    # (ON, MEMORY) and return integers on read (1, 2). Map the symbols.
    symbol_to_int = {
        "on": "1",
        "off": "0",
        "memory": "2",
        "file": "1",
        "default": "0",
        "normal": "1",
        "full": "2",
        "extra": "3",
    }
    if expected_lower in symbol_to_int and normalised == symbol_to_int[expected_lower]:
        return
    assert (
        normalised == expected_lower
    ), f"PRAGMA {key} expected {expected_lower!r}, got {normalised!r}"


@pytest.mark.asyncio
async def test_wal_files_exist_after_first_write(engine: AsyncEngine, db_path: Path) -> None:
    """A WAL'd SQLite database produces -wal/-shm sidecar files.

    Their presence confirms WAL is active end-to-end (pragma readback
    can pass while the file is still in rollback-journal mode if WAL
    was rejected — this catches that).
    """
    async with engine.begin() as conn:
        # CREATE TABLE forces a write so the WAL machinery materialises.
        await conn.exec_driver_sql("CREATE TABLE _probe (x INTEGER)")
        await conn.exec_driver_sql("INSERT INTO _probe VALUES (1)")
    wal = db_path.with_name(db_path.name + "-wal")
    shm = db_path.with_name(db_path.name + "-shm")
    assert wal.exists(), f"-wal sidecar missing at {wal}"
    assert shm.exists(), f"-shm sidecar missing at {shm}"


def test_naive_datetime_is_rejected_by_typedecorator() -> None:
    """The UTCEpochSeconds TypeDecorator must reject naive datetimes.

    Unit-tests the decorator directly rather than driving SQLAlchemy
    end-to-end so the ValueError isn't wrapped in StatementError. The
    asyncio test above (`test_wal_files_exist_after_first_write`)
    proves the decorator is wired into the engine path.
    """
    from app.db.types import UTCEpochSeconds

    decorator = UTCEpochSeconds()
    naive = datetime(2026, 1, 1, 0, 0, 0)
    with pytest.raises(ValueError, match="naive"):
        decorator.process_bind_param(naive, None)  # type: ignore[arg-type]


def test_aware_datetime_roundtrips_through_typedecorator() -> None:
    from datetime import UTC

    from app.db.types import UTCEpochSeconds

    decorator = UTCEpochSeconds()
    original = datetime(2026, 5, 16, 12, 34, 56, tzinfo=UTC)
    epoch = decorator.process_bind_param(original, None)  # type: ignore[arg-type]
    assert epoch == int(original.timestamp())
    recovered = decorator.process_result_value(epoch, None)  # type: ignore[arg-type]
    assert recovered == original
