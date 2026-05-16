"""Run-sessions repository — live-stream session lifecycle.

ADR-0007 distinguishes:
- HTTP sessions  → `sessions` table → `app.repositories.sessions`
- RUN sessions   → `sessions_history` table → THIS module

A run-session starts when the user clicks START and ends on STOP, on
error, or on control-plane crash (via the crash-recovery path in
`schema_init`).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator
from sqlalchemy import select, update

from app.db.models import SessionsHistoryORM

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession


VALID_END_REASONS: Final[frozenset[str]] = frozenset(
    {"normal", "user_stop", "control_plane_crash", "error"}
)


class RunSessionDTO(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: str
    started_at: AwareDatetime
    ended_at: AwareDatetime | None
    end_reason: str | None
    notes: dict[str, Any]

    @field_validator("notes", mode="before")
    @classmethod
    def _decode_json(cls, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            decoded = json.loads(value)
            if not isinstance(decoded, dict):
                raise ValueError("sessions_history.notes_json must decode to a JSON object")
            return decoded
        raise TypeError(f"notes must be str or dict, got {type(value).__name__}")

    @classmethod
    def from_orm(cls, row: SessionsHistoryORM) -> RunSessionDTO:
        return cls.model_validate(
            {
                "id": row.id,
                "started_at": row.started_at,
                "ended_at": row.ended_at,
                "end_reason": row.end_reason,
                "notes": row.notes_json,
            }
        )


class SessionsHistoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def start(self, *, notes: dict[str, Any] | None = None) -> RunSessionDTO:
        """Open a new run-session. `ended_at` and `end_reason` start NULL."""
        run_id = uuid.uuid4().hex
        row = SessionsHistoryORM(
            id=run_id,
            started_at=datetime.now(tz=UTC),
            ended_at=None,
            end_reason=None,
            notes_json=json.dumps(notes or {}, separators=(",", ":"), default=str),
        )
        self._s.add(row)
        await self._s.flush()
        return RunSessionDTO.from_orm(row)

    async def end(
        self,
        run_id: str,
        *,
        end_reason: str,
        at: datetime | None = None,
        notes_patch: dict[str, Any] | None = None,
    ) -> RunSessionDTO | None:
        """Close a run-session. `end_reason` must be one of `VALID_END_REASONS`."""
        if end_reason not in VALID_END_REASONS:
            raise ValueError(f"end_reason {end_reason!r} not one of {sorted(VALID_END_REASONS)}")
        actual_at = at if at is not None else datetime.now(tz=UTC)
        if actual_at.tzinfo is None:
            raise ValueError("at must be timezone-aware")

        values: dict[str, Any] = {"ended_at": actual_at, "end_reason": end_reason}
        if notes_patch is not None:
            current = await self.get(run_id)
            if current is None:
                return None
            merged = dict(current.notes)
            merged.update(notes_patch)
            values["notes_json"] = json.dumps(merged, separators=(",", ":"), default=str)
        await self._s.execute(
            update(SessionsHistoryORM).where(SessionsHistoryORM.id == run_id).values(**values)
        )
        return await self.get(run_id)

    async def get(self, run_id: str) -> RunSessionDTO | None:
        result = await self._s.execute(
            select(SessionsHistoryORM).where(SessionsHistoryORM.id == run_id)
        )
        row = result.scalar_one_or_none()
        return RunSessionDTO.from_orm(row) if row is not None else None

    async def get_active(self) -> RunSessionDTO | None:
        """The single in-flight run-session, if any.

        Single-writer model means at most one row has `ended_at IS NULL`
        at any time. If you see more, schema_init's crash-recovery did
        not run.
        """
        result = await self._s.execute(
            select(SessionsHistoryORM)
            .where(SessionsHistoryORM.ended_at.is_(None))
            .order_by(SessionsHistoryORM.started_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return RunSessionDTO.from_orm(row) if row is not None else None

    async def list_recent(
        self,
        limit: int = 50,
        *,
        before: datetime | None = None,
    ) -> Sequence[RunSessionDTO]:
        """Return up to `limit` rows ordered by `started_at DESC`.

        `before` is a cursor — when supplied, only rows with
        `started_at < before` are returned. Phase 6 code review H-3:
        the cursor must be pushed into the SQL, NOT applied in
        Python after a wider fetch — otherwise the "is there a next
        page?" signal is wrong whenever an in-window row falls out
        of the filter.
        """
        if limit <= 0:
            raise ValueError("limit must be positive")
        if before is not None and before.tzinfo is None:
            raise ValueError("before must be timezone-aware")
        stmt = select(SessionsHistoryORM).order_by(SessionsHistoryORM.started_at.desc())
        if before is not None:
            stmt = stmt.where(SessionsHistoryORM.started_at < before)
        stmt = stmt.limit(limit)
        result = await self._s.execute(stmt)
        return [RunSessionDTO.from_orm(row) for row in result.scalars().all()]
