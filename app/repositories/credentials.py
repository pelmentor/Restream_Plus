"""Credentials repository — encrypted stream-key material per ADR-0006/0009.

The repository stores opaque AEAD outputs (ciphertext, nonce, salt).
It does NOT decrypt — that's the job of a higher service layer (Phase 3+)
which combines this repo with `app.crypto`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from pydantic import AwareDatetime, BaseModel, ConfigDict
from sqlalchemy import delete, select, update
from sqlalchemy.engine.cursor import CursorResult

from app.db.models import CredentialORM

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncSession


class CredentialDTO(BaseModel):
    """Frozen view over a `credentials` row.

    `lifetime` is the string value of the enum from ADR-0009
    (`persistent` | `per_session` | `refresh_token`). The domain layer
    in Phase 4 will define the enum and the policy that maps it to
    behaviour; this DTO carries it as a string to keep the persistence
    layer self-contained.
    """

    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: str
    target_id: str
    lifetime: str
    ciphertext: bytes
    nonce: bytes
    salt: bytes
    last4: str
    created_at: AwareDatetime
    expires_at: AwareDatetime | None


class CredentialsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def upsert(
        self,
        *,
        target_id: str,
        lifetime: str,
        ciphertext: bytes,
        nonce: bytes,
        salt: bytes,
        last4: str,
        expires_at: datetime | None = None,
    ) -> CredentialDTO:
        """Replace the (single) active credential for a target.

        ADR-0009: each target has one or zero active credentials. We
        delete the existing row (if any) and insert a new one in the
        same transaction.
        """
        if expires_at is not None and expires_at.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")
        if len(last4) > 4:
            raise ValueError("last4 must be at most 4 characters")
        await self._s.execute(delete(CredentialORM).where(CredentialORM.target_id == target_id))
        cred_id = uuid.uuid4().hex
        row = CredentialORM(
            id=cred_id,
            target_id=target_id,
            lifetime=lifetime,
            ciphertext=ciphertext,
            nonce=nonce,
            salt=salt,
            last4=last4,
            created_at=datetime.now(tz=UTC),
            expires_at=expires_at,
        )
        self._s.add(row)
        await self._s.flush()
        return CredentialDTO.model_validate(row)

    async def get_for_target(self, target_id: str) -> CredentialDTO | None:
        result = await self._s.execute(
            select(CredentialORM).where(CredentialORM.target_id == target_id)
        )
        row = result.scalar_one_or_none()
        return CredentialDTO.model_validate(row) if row is not None else None

    async def delete_for_target(self, target_id: str) -> bool:
        """Delete the active credential for a target (used at session END
        for `per_session` lifetime per ADR-0009). Returns True iff a
        row was removed.
        """
        result = cast(
            "CursorResult[Any]",
            await self._s.execute(
                delete(CredentialORM).where(CredentialORM.target_id == target_id)
            ),
        )
        return result.rowcount > 0

    async def list_all_for_rewrap(self) -> tuple[CredentialDTO, ...]:
        """Return every credential row for the rotate-passphrase re-wrap
        loop (Phase 9). Sorted by id for deterministic test runs.

        The caller decrypts each row's ciphertext with the OLD KEK and
        re-encrypts with the NEW KEK + a fresh per-row salt; this
        method just yields the inputs.
        """
        result = await self._s.execute(select(CredentialORM).order_by(CredentialORM.id))
        return tuple(CredentialDTO.model_validate(row) for row in result.scalars().all())

    async def rewrap(
        self,
        *,
        credential_id: str,
        ciphertext: bytes,
        nonce: bytes,
        salt: bytes,
    ) -> None:
        """Re-wrap a single credential row's AEAD material (Phase 9
        rotate-passphrase). Statement-level UPDATE so the caller can
        wrap N of these in a single transaction.

        `last4`, `lifetime`, `expires_at`, `target_id`, `created_at`
        are intentionally untouched — the plaintext is the same, only
        the wrapping key changed.
        """
        await self._s.execute(
            update(CredentialORM)
            .where(CredentialORM.id == credential_id)
            .values(ciphertext=ciphertext, nonce=nonce, salt=salt)
        )
