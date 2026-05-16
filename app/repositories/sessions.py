"""HTTP sessions repository.

Distinct from *run* sessions (live-stream lifecycle) which live in
`app/repositories/sessions_history.py`. Naming-wise we treat
`HttpSession` and `RunSession` as siblings so callers cannot confuse
the two.

The `token_hash` column is the HMAC fingerprint of the cookie value
per ADR-0005. Phase 3 (auth) is what produces the hash; this repo
treats it as an opaque BLOB key.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from pydantic import AwareDatetime, BaseModel, ConfigDict
from sqlalchemy import delete, select, update
from sqlalchemy.engine.cursor import CursorResult

from app.db.models import SessionORM

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncSession


class HttpSessionDTO(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    token_hash: bytes
    user_id: str
    created_at: AwareDatetime
    expires_at: AwareDatetime
    last_seen_at: AwareDatetime
    user_agent: str | None
    ip: str | None


class HttpSessionsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(
        self,
        *,
        token_hash: bytes,
        user_id: str,
        expires_at: datetime,
        user_agent: str | None = None,
        ip: str | None = None,
    ) -> HttpSessionDTO:
        if expires_at.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")
        now = datetime.now(tz=UTC)
        row = SessionORM(
            token_hash=token_hash,
            user_id=user_id,
            created_at=now,
            expires_at=expires_at,
            last_seen_at=now,
            user_agent=user_agent,
            ip=ip,
        )
        self._s.add(row)
        await self._s.flush()
        return HttpSessionDTO.model_validate(row)

    async def get_by_token_hash(self, token_hash: bytes) -> HttpSessionDTO | None:
        result = await self._s.execute(
            select(SessionORM).where(SessionORM.token_hash == token_hash)
        )
        row = result.scalar_one_or_none()
        return HttpSessionDTO.model_validate(row) if row is not None else None

    async def touch_last_seen(self, token_hash: bytes, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("at must be timezone-aware")
        await self._s.execute(
            update(SessionORM).where(SessionORM.token_hash == token_hash).values(last_seen_at=at)
        )

    async def delete(self, token_hash: bytes) -> None:
        await self._s.execute(delete(SessionORM).where(SessionORM.token_hash == token_hash))

    async def delete_all_for_user(self, user_id: str) -> int:
        """Implements 'log out everywhere' (ADR-0005). Returns rows affected."""
        # `AsyncSession.execute(<DML>)` returns CursorResult at runtime;
        # SQLAlchemy's stubs declare the broader Result here so we cast
        # to access `.rowcount`. Same pattern in the repos below.
        result = cast(
            "CursorResult[Any]",
            await self._s.execute(delete(SessionORM).where(SessionORM.user_id == user_id)),
        )
        return max(0, result.rowcount)

    async def delete_expired(self, now: datetime) -> int:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        result = cast(
            "CursorResult[Any]",
            await self._s.execute(delete(SessionORM).where(SessionORM.expires_at <= now)),
        )
        return max(0, result.rowcount)

    async def list_active_for_user(self, user_id: str, now: datetime) -> tuple[HttpSessionDTO, ...]:
        """Return every non-expired session for a user, newest-seen first.

        Phase 9 GET /api/security/sessions. Filters at the SQL layer so
        we don't carry expired rows into Python only to drop them.
        """
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        result = await self._s.execute(
            select(SessionORM)
            .where(SessionORM.user_id == user_id, SessionORM.expires_at > now)
            .order_by(SessionORM.last_seen_at.desc())
        )
        return tuple(HttpSessionDTO.model_validate(row) for row in result.scalars().all())

    async def delete_all(self) -> int:
        """Bulk-delete every session row (Phase 9 rotate-passphrase).

        Distinct from `delete_all_for_user` (which scopes to one user);
        rotate-passphrase invalidates EVERY session because the
        `session_token_mac_key` derived from the new passphrase will
        no longer validate the existing `token_hash` values.
        """
        result = cast(
            "CursorResult[Any]",
            await self._s.execute(delete(SessionORM)),
        )
        return max(0, result.rowcount)
