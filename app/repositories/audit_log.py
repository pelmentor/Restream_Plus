"""Audit log repository — append-only, with WAL checkpoint per ADR-0007.

ADR-0007 §"Audit log scope":
- Append is followed by `PRAGMA wal_checkpoint(TRUNCATE)` to guarantee
  durability across OS-level power loss. At our write rate (≤ 1 write/s
  typical) the checkpoint cost is negligible.
- Audit entries are NEVER updated or deleted; only `append` and read
  operations exist.

Constructor takes the `async_sessionmaker` (factory) rather than a
single `AsyncSession`. Each `append` runs in its own short-lived
transaction so the caller's business-work transaction (if any) is
neither held open by the checkpoint nor surprised by an inner commit.
Read methods accept a session via parameter so they can participate in
a caller-owned transaction without surprise.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator
from sqlalchemy import select, text

from app.db.models import AuditLogORM

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_WAL_CHECKPOINT_TRUNCATE = text("PRAGMA wal_checkpoint(TRUNCATE)")
_logger = structlog.get_logger(__name__)


class AuditLogEntryDTO(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: int
    at: AwareDatetime
    event_type: str
    actor: str | None
    target_id: str | None
    data: dict[str, Any]

    @field_validator("data", mode="before")
    @classmethod
    def _decode_json(cls, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            decoded = json.loads(value)
            if not isinstance(decoded, dict):
                raise ValueError("audit_log.data_json must decode to a JSON object")
            return decoded
        raise TypeError(f"data must be str or dict, got {type(value).__name__}")

    @classmethod
    def from_orm(cls, row: AuditLogORM) -> AuditLogEntryDTO:
        return cls.model_validate(
            {
                "id": row.id,
                "at": row.at,
                "event_type": row.event_type,
                "actor": row.actor,
                "target_id": row.target_id,
                "data": row.data_json,
            }
        )


class AuditLogRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sf = sessionmaker

    async def append(
        self,
        *,
        event_type: str,
        actor: str | None = None,
        target_id: str | None = None,
        data: dict[str, Any] | None = None,
        at: datetime | None = None,
    ) -> AuditLogEntryDTO:
        """Insert one audit row in its own transaction, then checkpoint WAL."""
        actual_at = at if at is not None else datetime.now(tz=UTC)
        if actual_at.tzinfo is None:
            raise ValueError("at must be timezone-aware")

        async with self._sf() as session, session.begin():
            row = AuditLogORM(
                at=actual_at,
                event_type=event_type,
                actor=actor,
                target_id=target_id,
                data_json=json.dumps(data or {}, separators=(",", ":"), default=str),
            )
            session.add(row)
            await session.flush()
            # Capture data needed for the DTO before commit; row.id is
            # populated by flush.
            dto = AuditLogEntryDTO.from_orm(row)

        # Checkpoint runs *after* the commit so the row is already in
        # the WAL when wal_checkpoint(TRUNCATE) copies it to the main
        # database file. SQLite returns (busy, log_pages, checkpointed):
        # `busy=1` means a reader/writer prevented full truncation —
        # the row is still durable in the WAL, but the operator needs
        # to know so they can investigate held-open read transactions.
        async with self._sf() as session:
            result = await session.execute(_WAL_CHECKPOINT_TRUNCATE)
            checkpoint_row = result.fetchone()
        if checkpoint_row is not None:
            busy, log_pages, checkpointed = (
                checkpoint_row[0],
                checkpoint_row[1],
                checkpoint_row[2],
            )
            if busy:
                _logger.warning(
                    "wal_checkpoint_truncate_busy",
                    busy=int(busy),
                    log_pages=int(log_pages),
                    checkpointed=int(checkpointed),
                    audit_event_type=event_type,
                    audit_id=dto.id,
                    note=(
                        "audit row is durable in WAL but TRUNCATE could "
                        "not complete; a long-lived reader is likely holding "
                        "a shared lock"
                    ),
                )

        return dto

    async def list_recent(
        self, session: AsyncSession, *, limit: int = 100
    ) -> Sequence[AuditLogEntryDTO]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        result = await session.execute(
            select(AuditLogORM).order_by(AuditLogORM.id.desc()).limit(limit)
        )
        return [AuditLogEntryDTO.from_orm(row) for row in result.scalars().all()]

    async def list_by_target(
        self, session: AsyncSession, target_id: str, *, limit: int = 100
    ) -> Sequence[AuditLogEntryDTO]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        result = await session.execute(
            select(AuditLogORM)
            .where(AuditLogORM.target_id == target_id)
            .order_by(AuditLogORM.id.desc())
            .limit(limit)
        )
        return [AuditLogEntryDTO.from_orm(row) for row in result.scalars().all()]

    async def list_app_starts(
        self, session: AsyncSession, *, limit: int = 5
    ) -> tuple[datetime, ...]:
        """Return the `at` timestamps of the N most-recent `app_started`
        audit events, newest first (Phase 9 GET /api/about.last_reboots).

        The current boot is one of them — the lifespan startup emits an
        `app_started` event so `last_reboots[0]` equals
        `app.state.started_at` modulo the microsecond write gap.
        """
        if limit <= 0:
            raise ValueError("limit must be positive")
        result = await session.execute(
            select(AuditLogORM.at)
            .where(AuditLogORM.event_type == "app_started")
            .order_by(AuditLogORM.id.desc())
            .limit(limit)
        )
        return tuple(row[0] for row in result.all())
