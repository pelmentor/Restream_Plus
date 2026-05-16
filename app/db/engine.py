"""Async SQLAlchemy engine factory for SQLite (aiosqlite).

The pragmas defined in ADR-0007 are applied on every new connection
via a SQLAlchemy `connect` event hook. Per-connection pragmas
(`foreign_keys`, `synchronous`, `busy_timeout`, `temp_store`, `cache_size`,
`mmap_size`) are SET each time. The database-wide `journal_mode = WAL`
is set on the first connection that touches the file; subsequent
connections will see it as already active.

Public surface:
    create_engine(settings) -> AsyncEngine
    PRAGMAS  -- the single source of truth, mirrored from ADR-0007.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

if TYPE_CHECKING:  # pragma: no cover
    from app.config import AppSettings


PRAGMAS: Final[dict[str, str]] = {
    # Documented at ADR-0007 §"Engine configuration". Kept in lockstep
    # with the ADR; an integration test verifies each pragma is active
    # after engine creation.
    "journal_mode": "WAL",
    "synchronous": "NORMAL",
    "foreign_keys": "ON",
    "busy_timeout": "5000",
    "temp_store": "MEMORY",
    "mmap_size": "67108864",
    "cache_size": "-32000",
}


def _apply_pragmas(dbapi_conn: Any, _connection_record: Any) -> None:
    """SQLAlchemy `connect` hook — runs once per pooled connection.

    aiosqlite hands us the underlying sync sqlite3 connection here, so
    we use the sync cursor API. `PRAGMA journal_mode` is the only
    statement that returns a row; we read it to confirm WAL took effect.
    """
    cursor = dbapi_conn.cursor()
    try:
        for key, value in PRAGMAS.items():
            cursor.execute(f"PRAGMA {key} = {value}")
            if key == "journal_mode":
                # journal_mode is the only pragma that returns the
                # active mode after being set. Capture it so a
                # misconfigured filesystem (e.g., a network share that
                # silently downgrades to journal_mode=DELETE) does not
                # go unnoticed.
                row = cursor.fetchone()
                actual = (row[0] if row else "").lower()
                if actual != value.lower():
                    raise RuntimeError(
                        f"PRAGMA journal_mode={value!r} did not take effect; "
                        f"SQLite reports journal_mode={actual!r}. "
                        f"Filesystem may not support WAL (network mounts often do not)."
                    )
    finally:
        cursor.close()


def create_engine(settings: AppSettings) -> AsyncEngine:
    """Construct the async engine bound to `settings.db_path`.

    The engine is a process-global resource. `app.main` constructs it
    once at startup, hands it to `schema_init.initialize_schema`, then
    threads it into the repositories via the session factory.
    """
    url = f"sqlite+aiosqlite:///{settings.db_path}"
    engine = create_async_engine(
        url,
        future=True,
        # echo=False is the default; left explicit so a future debugging
        # session can flip it without searching for a default-override.
        echo=False,
    )
    # The connect hook runs against the synchronous DBAPI connection
    # that aiosqlite wraps, so register on engine.sync_engine.
    event.listen(engine.sync_engine, "connect", _apply_pragmas)
    return engine
