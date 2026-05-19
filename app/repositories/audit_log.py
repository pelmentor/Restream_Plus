"""Audit log repository — append-only, transactional per ADR-0007.

Hex Audit BA-F4 (slice 8): the repository exposes TWO append methods.

- `append(session, ...)` — canonical path. Adds the audit row to the
  caller's `AsyncSession` BEFORE the caller commits. Both the business
  write and the forensic row land in ONE physical transaction or
  neither does. No `wal_checkpoint(TRUNCATE)` runs in this path —
  per-append checkpoint is incompatible with caller-owned txn lifecycle
  and is replaced by the periodic retention loop (see §"Retention").

- `append_standalone(...)` — reserved for callers that legitimately
  have no business transaction: the `app_started` lifespan emit, the
  supervisor's lifecycle helper, and (only as a fallback for unusual
  paths) background work that decides to write the audit row in
  isolation. Opens its own short-lived txn, then runs
  `wal_checkpoint(TRUNCATE)` post-commit.

Call-site rule: if you have a `SessionDep`, you MUST call `append`.
The type system enforces it — `append` requires a session argument,
`append_standalone` does not.

§"Retention" (Hex Audit FG2-M6):
- `delete_older_than(session, cutoff)` deletes rows with `at < cutoff`.
  Returns the number of deleted rows. Caller commits.
- A periodic loop in `app/main.py` (`_audit_log_retention_loop`)
  drives this at a 6 h cadence, with the cutoff computed from
  `settings.audit_log_retention_days` (default 365, configurable via
  `RESTREAM_AUDIT_LOG_RETENTION_DAYS`).

The retention loop ALSO doubles as the periodic
`wal_checkpoint(TRUNCATE)` driver — one loop, both responsibilities,
matching SQLite's auto-checkpoint posture but with TRUNCATE semantics
to bound WAL growth.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

import structlog
from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator
from sqlalchemy import delete, select, text

from app.db.models import AuditLogORM

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_WAL_CHECKPOINT_TRUNCATE = text("PRAGMA wal_checkpoint(TRUNCATE)")
_logger = structlog.get_logger(__name__)

DEFAULT_AUDIT_LOG_RETENTION_DAYS: Final[int] = 365
"""Default retention window. Operator overrides via
`RESTREAM_AUDIT_LOG_RETENTION_DAYS` (see `app/config.py`). Floor 30,
ceiling 3650 (10 y) — both enforced at config-load time."""


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
        session: AsyncSession,
        *,
        event_type: str,
        actor: str | None = None,
        target_id: str | None = None,
        data: dict[str, Any] | None = None,
        at: datetime | None = None,
    ) -> AuditLogEntryDTO:
        """Insert one audit row in the CALLER's transaction.

        The caller's `session.commit()` (or the request dependency's
        commit-on-success teardown) atomically persists both the
        business write and the audit row. If the caller rolls back —
        either explicitly or by raising — the audit row rolls back too.

        Does NOT run `wal_checkpoint(TRUNCATE)`; the periodic retention
        loop owns checkpoint cadence.
        """
        actual_at = at if at is not None else datetime.now(tz=UTC)
        if actual_at.tzinfo is None:
            raise ValueError("at must be timezone-aware")

        row = AuditLogORM(
            at=actual_at,
            event_type=event_type,
            actor=actor,
            target_id=target_id,
            data_json=json.dumps(data or {}, separators=(",", ":"), default=str),
        )
        session.add(row)
        # Flush to populate row.id without committing — the caller's
        # commit (or dep teardown) finalises the txn.
        await session.flush()
        return AuditLogEntryDTO.from_orm(row)

    async def append_standalone(
        self,
        *,
        event_type: str,
        actor: str | None = None,
        target_id: str | None = None,
        data: dict[str, Any] | None = None,
        at: datetime | None = None,
        checkpoint: bool = True,
    ) -> AuditLogEntryDTO:
        """Insert one audit row in its OWN transaction, then checkpoint WAL.

        Reserved for callers that have no business transaction to share
        (lifespan startup, supervisor lifecycle, periodic loops that
        deliberately keep the audit row separate from the work).
        Failure raises — there is no implicit suppress here either; the
        caller decides whether to swallow.

        `checkpoint=False` skips the post-commit `wal_checkpoint(TRUNCATE)`.
        Used by hot-path callers (e.g., the retention loop emits its own
        `audit_log_retention_run` row and runs the checkpoint itself).
        """
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
            dto = AuditLogEntryDTO.from_orm(row)

        if checkpoint:
            await self._run_wal_checkpoint(reason=f"append_standalone:{event_type}")

        return dto

    async def delete_older_than(
        self,
        session: AsyncSession,
        *,
        cutoff: datetime,
        exclude_event_types: tuple[str, ...] = (),
    ) -> int:
        """Delete rows with `at < cutoff`. Returns the deleted-row count.

        Caller controls the transaction. Used by
        `_audit_log_retention_loop` in `app/main.py`.

        `exclude_event_types` keeps rows whose `event_type` is in the
        tuple regardless of age. Slice 8.5 fix-up for hex-audit
        finding FG3-F4 (audit-of-audit row participating in
        retention): the retention loop excludes `audit_log_retention_run`
        so the maintenance markers persist as a long-horizon record of
        when retention ran.
        """
        if cutoff.tzinfo is None:
            raise ValueError("cutoff must be timezone-aware")
        stmt = delete(AuditLogORM).where(AuditLogORM.at < cutoff)
        if exclude_event_types:
            stmt = stmt.where(AuditLogORM.event_type.notin_(exclude_event_types))
        result = await session.execute(stmt)
        # SQLAlchemy returns CursorResult for DML; rowcount is the
        # supported affected-rows count. The static `Result` type
        # doesn't expose it; cast through Any to keep mypy quiet
        # without dragging in `CursorResult` at type-check time.
        rowcount: int = getattr(result, "rowcount", 0) or 0
        return rowcount

    async def run_wal_checkpoint(self, *, reason: str) -> None:
        """Run `PRAGMA wal_checkpoint(TRUNCATE)` in a dedicated session.

        Called by the periodic retention loop after a non-empty delete
        (and on the empty-delete tick once per day to bound WAL growth
        under heavy read-only traffic). `reason` is included in any
        `wal_checkpoint_truncate_busy` warning to disambiguate why the
        checkpoint ran.
        """
        await self._run_wal_checkpoint(reason=reason)

    async def _run_wal_checkpoint(self, *, reason: str) -> None:
        async with self._sf() as session:
            result = await session.execute(_WAL_CHECKPOINT_TRUNCATE)
            checkpoint_row = result.fetchone()
        if checkpoint_row is None:
            return
        busy, log_pages, checkpointed = (
            checkpoint_row[0],
            checkpoint_row[1],
            checkpoint_row[2],
        )
        if busy:
            # Hex Audit BA2-F8 (slice 10): `reason` is the only field
            # that meaningfully correlates this warning with the calling
            # context. The pre-slice-10 fields `audit_event_type` and
            # `audit_id` reported the row that was JUST committed before
            # the checkpoint fired — but the checkpoint observes the
            # WAL globally, so a concurrent writer that landed an
            # append between commit and checkpoint would make those
            # fields point at the wrong row. `reason` (a static caller-
            # supplied label like "retention_run" or "app_started")
            # carries no such risk of misleading correlation.
            _logger.warning(
                "wal_checkpoint_truncate_busy",
                busy=int(busy),
                log_pages=int(log_pages),
                checkpointed=int(checkpointed),
                reason=reason,
                note=(
                    "audit row is durable in WAL but TRUNCATE could "
                    "not complete; a long-lived reader is likely holding "
                    "a shared lock"
                ),
            )

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
        """Return audit rows scoped to `target_id`, newest-first.

        Matches BOTH `audit_log.target_id` (the FK column, indexed) AND
        `data_json -> '$.target_id'` (JSON1 path extract). The latter
        covers `target_deleted` rows whose FK column is intentionally
        NULL (passing `target_id` at INSERT time would violate the
        deferred FK check against the just-deleted target row; the
        deleted id lives in `data_json` instead). Slice 8.5 fix-up
        for hex-audit finding FG3-F6 — without the JSON1 path,
        `list_by_target` silently dropped every deletion event.

        The JSON1 branch table-scans (no functional index on
        `data_json`), so this is a forensic query — not hot-path.
        Acceptable cost: audit log is bounded by retention (default
        365 days). For deployments where the scan cost becomes an
        issue, add `CREATE INDEX idx_audit_log_data_target_id ON
        audit_log (json_extract(data_json, '$.target_id'))` in a
        future schema bump.
        """
        from sqlalchemy import func, or_

        if limit <= 0:
            raise ValueError("limit must be positive")
        result = await session.execute(
            select(AuditLogORM)
            .where(
                or_(
                    AuditLogORM.target_id == target_id,
                    func.json_extract(AuditLogORM.data_json, "$.target_id") == target_id,
                )
            )
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
