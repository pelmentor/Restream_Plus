"""API tokens repository.

Stores HMAC fingerprints of bearer-tokens per ADR-0005. The bearer
value itself is shown to the user exactly once (the `OneTimeRevealBanner`
UI in Phase 9) and never persisted.

DTOs never include the `token_hash` column — listing tokens for the
Settings UI shows only label/last_used metadata, not anything that
could be used to authenticate.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from pydantic import AwareDatetime, BaseModel, ConfigDict
from sqlalchemy import delete, select, update
from sqlalchemy.engine.cursor import CursorResult

from app.db.models import ApiTokenORM

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession


class ApiTokenDTO(BaseModel):
    """Metadata DTO. `token_hash` is intentionally NOT exposed.

    `user_id` IS exposed because `get_current_user_via_bearer` needs it
    to load the owner without the hardcoded "admin" lookup the Phase 3
    deferred gap left behind. Owner identity is not a secret in a
    single-user app and is already implied by the auth artefact itself.
    """

    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: str
    user_id: str
    label: str
    created_at: AwareDatetime
    last_used_at: AwareDatetime | None
    revoked_at: AwareDatetime | None


class ApiTokensRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(self, *, user_id: str, token_hash: bytes, label: str) -> ApiTokenDTO:
        token_id = uuid.uuid4().hex
        now = datetime.now(tz=UTC)
        row = ApiTokenORM(
            id=token_id,
            user_id=user_id,
            token_hash=token_hash,
            label=label,
            created_at=now,
            last_used_at=None,
            revoked_at=None,
        )
        self._s.add(row)
        await self._s.flush()
        return ApiTokenDTO.model_validate(row)

    async def get_active_by_token_hash(self, token_hash: bytes) -> ApiTokenDTO | None:
        """Return the token if it exists AND has not been revoked."""
        result = await self._s.execute(
            select(ApiTokenORM).where(
                ApiTokenORM.token_hash == token_hash,
                ApiTokenORM.revoked_at.is_(None),
            )
        )
        row = result.scalar_one_or_none()
        return ApiTokenDTO.model_validate(row) if row is not None else None

    async def touch_last_used(self, token_id: str, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("at must be timezone-aware")
        await self._s.execute(
            update(ApiTokenORM).where(ApiTokenORM.id == token_id).values(last_used_at=at)
        )

    async def revoke(self, token_id: str, at: datetime) -> bool:
        """Mark a token revoked. Returns True iff a row was updated."""
        if at.tzinfo is None:
            raise ValueError("at must be timezone-aware")
        result = cast(
            "CursorResult[Any]",
            await self._s.execute(
                update(ApiTokenORM)
                .where(ApiTokenORM.id == token_id, ApiTokenORM.revoked_at.is_(None))
                .values(revoked_at=at)
            ),
        )
        return result.rowcount > 0

    async def list_all(self) -> Sequence[ApiTokenDTO]:
        result = await self._s.execute(select(ApiTokenORM).order_by(ApiTokenORM.created_at.desc()))
        return [ApiTokenDTO.model_validate(row) for row in result.scalars().all()]

    async def delete_all(self) -> int:
        """Bulk-delete every api_token row (Phase 9 rotate-passphrase).

        Hard DELETE (not revoke) because re-keying is impossible — the
        `token_hash` is an HMAC of the bearer plaintext under the OLD
        `api_token_mac_key`, and the plaintext is gone (one-time-reveal
        per ADR-0005). Keeping a revoked-row trail adds operator noise
        when `api_token_created` / `api_token_revoked` audit events
        already document who created what when.
        """
        result = cast(
            "CursorResult[Any]",
            await self._s.execute(delete(ApiTokenORM)),
        )
        return max(0, result.rowcount)
