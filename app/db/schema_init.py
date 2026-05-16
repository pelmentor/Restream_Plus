"""First-boot schema bring-up, version check, and crash recovery.

Per ADR-0007:

- Schema lives in `schema.sql` (single source of truth).
- On fresh DB (user_version == 0): execute `schema.sql`, set user_version,
  seed the singleton settings row and the admin user row.
- On a matching version (user_version == SCHEMA_VERSION): no-op.
- On a mismatched version: refuse to start with a clear error pointing
  to the export/reimport procedure (Rule №2: no auto-migration).
- After version reconciliation, scan `sessions_history` for rows with
  `ended_at IS NULL` (control-plane crashed mid-stream) and patch them
  with `end_reason = 'control_plane_crash'`, recording an audit event.

The reset-admin-password flow per ADR-0005 also lands here: if
`RESTREAM_RESET_ADMIN_PASSWORD=1` and a new password is supplied, the
admin row is updated on every boot.

Schema bring-up uses the synchronous `sqlite3` driver directly (via
`asyncio.to_thread`) so we can call `executescript` — the canonical
multi-statement entry point that handles comments, string literals,
and trigger bodies. The async engine is used only for the seed,
reset, and crash-recovery paths, which are typed ORM operations.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

from sqlalchemy import insert, select, update

from app.crypto import hash_password
from app.db.engine import PRAGMAS
from app.db.models import AuditLogORM, SessionsHistoryORM, SettingsORM, UserORM

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

    from app.config import AppSettings


SCHEMA_VERSION: Final[int] = 1
"""Compiled-in schema-shape version. Bump on any change to `schema.sql`.

Per Rule №2 there is no Alembic / migrations chain. A bumped version
causes the boot to refuse to start with a message pointing the
operator at the export → reimport procedure.
"""

ADMIN_USERNAME: Final[str] = "admin"
INGEST_KEY_BYTES: Final[int] = 24  # 32 chars URL-safe base64, ample entropy.

_SCHEMA_SQL_PATH: Final[Path] = Path(__file__).with_name("schema.sql")


class SchemaVersionMismatchError(RuntimeError):
    """Raised when the on-disk schema version disagrees with the binary.

    Per ADR-0007 the resolution is operator-driven (export + reimport),
    not an in-process migration. The exception message names the
    procedure so the operator sees it in the logs without spelunking.
    """

    def __init__(self, *, on_disk: int, expected: int) -> None:
        self.on_disk = on_disk
        self.expected = expected
        super().__init__(
            f"DB schema version mismatch: on-disk={on_disk}, expected={expected}. "
            f"Restream_Plus does not auto-migrate (ADR-0007, Rule №2). "
            f"To upgrade, export with the old image, start the new image "
            f"against an empty data dir, then reimport. See docs/ops/upgrade.md."
        )


@dataclass(frozen=True, slots=True)
class InitResult:
    """Outcome of `initialize_schema`.

    - `fresh`: True iff this boot created the schema and seed rows.
    - `recovered_session_ids`: IDs of run-sessions that were patched
      from `ended_at IS NULL` → `'control_plane_crash'`.
    - `admin_password_set`: True iff an admin password was written
      during this boot (either initial seed or reset).
    """

    fresh: bool
    recovered_session_ids: tuple[str, ...]
    admin_password_set: bool


async def initialize_schema(engine: AsyncEngine, settings: AppSettings) -> InitResult:
    """Bring the database up to the expected schema and recover crashes."""
    fresh = await asyncio.to_thread(_sync_bootstrap_schema, settings.db_path)

    admin_password_set = False
    if fresh:
        async with engine.begin() as conn:
            admin_password_set = await _seed_initial_rows(conn, settings)
    elif settings.reset_admin_password and settings.admin_password is not None:
        async with engine.begin() as conn:
            await _reset_admin_password(conn, settings)
        admin_password_set = True

    recovered = await _recover_interrupted_sessions(engine)

    return InitResult(
        fresh=fresh,
        recovered_session_ids=recovered,
        admin_password_set=admin_password_set,
    )


def _sync_bootstrap_schema(db_path: Path) -> bool:
    """Synchronously open the DB, apply pragmas, execute schema.sql if fresh.

    Returns True iff this run created the schema (the DB was empty).
    Runs under `asyncio.to_thread` so it doesn't block the event loop.

    Using the sync `sqlite3` driver here lets us call
    `Connection.executescript`, which is the canonical multi-statement
    entry point. The async engine's pragma hook applies the same
    pragmas to its own connections; doing them here too is required so
    `journal_mode = WAL` is set before the schema-creating writes.
    """
    schema_sql = _SCHEMA_SQL_PATH.read_text(encoding="utf-8")
    conn = sqlite3.connect(str(db_path))
    try:
        for key, value in PRAGMAS.items():
            cursor = conn.execute(f"PRAGMA {key} = {value}")
            if key == "journal_mode":
                row = cursor.fetchone()
                actual = (row[0] if row else "").lower()
                if actual != value.lower():
                    raise RuntimeError(
                        f"PRAGMA journal_mode={value!r} did not take effect; "
                        f"SQLite reports journal_mode={actual!r}. "
                        f"Filesystem may not support WAL."
                    )
        on_disk_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if on_disk_version == 0:
            conn.executescript(schema_sql)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
            return True
        if on_disk_version != SCHEMA_VERSION:
            raise SchemaVersionMismatchError(on_disk=on_disk_version, expected=SCHEMA_VERSION)
        return False
    finally:
        conn.close()


async def _seed_initial_rows(conn: AsyncConnection, settings: AppSettings) -> bool:
    """Insert the singleton settings row and the admin user row.

    Returns True iff an admin password hash was written during this seed.
    """
    import secrets

    now = datetime.now(tz=UTC)

    password_hash: str | None = None
    if settings.admin_password is not None:
        password_hash = await asyncio.to_thread(
            hash_password, settings.admin_password.get_secret_value()
        )

    await conn.execute(
        insert(SettingsORM).values(
            id=1,
            first_run_complete=1 if password_hash is not None else 0,
            idle_timeout_seconds=600,
            log_retention_days=14,
            ingest_key_current=secrets.token_urlsafe(INGEST_KEY_BYTES),
            ingest_key_previous=None,
            ingest_key_rotated_at=None,
            ingest_key_grace_until=None,
            updated_at=now,
        )
    )

    await conn.execute(
        insert(UserORM).values(
            id=uuid.uuid4().hex,
            username=ADMIN_USERNAME,
            password_hash=password_hash,
            created_at=now,
            last_login_at=None,
        )
    )

    return password_hash is not None


async def _reset_admin_password(conn: AsyncConnection, settings: AppSettings) -> None:
    """Apply `RESTREAM_RESET_ADMIN_PASSWORD=1` per ADR-0005."""
    assert settings.admin_password is not None  # validated by AppSettings
    new_hash = await asyncio.to_thread(hash_password, settings.admin_password.get_secret_value())
    await conn.execute(
        update(UserORM).where(UserORM.username == ADMIN_USERNAME).values(password_hash=new_hash)
    )
    # Reset also clears the first-run flag (a reset implies the operator
    # is bringing the system back into a usable state).
    await conn.execute(update(SettingsORM).where(SettingsORM.id == 1).values(first_run_complete=1))


async def _recover_interrupted_sessions(engine: AsyncEngine) -> tuple[str, ...]:
    """Patch sessions_history rows with `ended_at IS NULL` per ADR-0007.

    For each orphaned run-session:
    1. Append an `audit_log` row of type `interrupted_session_recovered`
       referencing the prior session id. The audit `data_json` includes
       `started_at` (not an "estimated end" — we have no post-start
       timestamp, so any end-time guess is a lower bound; we record
       the start and let the operator interpret).
    2. UPDATE the row to set `ended_at = started_at` (the lower-bound
       estimate per ADR-0007 — status snapshots are WebSocket-only and
       not persisted) and `end_reason = 'control_plane_crash'`.

    Returns the ids of the patched sessions, in the order patched.
    """
    recovered: list[str] = []
    async with engine.begin() as conn:
        result = await conn.execute(
            select(SessionsHistoryORM.id, SessionsHistoryORM.started_at).where(
                SessionsHistoryORM.ended_at.is_(None)
            )
        )
        rows = result.all()
        now = datetime.now(tz=UTC)
        for row in rows:
            session_id: str = row[0]
            started_at: datetime = row[1]
            await conn.execute(
                insert(AuditLogORM).values(
                    at=now,
                    event_type="interrupted_session_recovered",
                    actor="system",
                    target_id=None,
                    data_json=json.dumps(
                        {
                            "session_id": session_id,
                            "started_at": started_at.isoformat(),
                            "reason": "control_plane_crash",
                            "note": (
                                "ended_at set to started_at; status snapshots "
                                "are WebSocket-only and not persisted, so the "
                                "true end time is unknown"
                            ),
                        },
                        separators=(",", ":"),
                    ),
                )
            )
            await conn.execute(
                update(SessionsHistoryORM)
                .where(SessionsHistoryORM.id == session_id)
                .values(ended_at=started_at, end_reason="control_plane_crash")
            )
            recovered.append(session_id)
    return tuple(recovered)
