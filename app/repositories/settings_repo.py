"""Singleton settings row.

There is exactly one row, with id = 1, enforced by a CHECK constraint
in `schema.sql`. The DTO mirrors that shape.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator
from sqlalchemy import select, update

from app.db.models import SettingsORM

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncSession


class SettingsDTO(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    first_run_complete: bool
    idle_timeout_seconds: int
    log_retention_days: int
    ingest_key_current: str
    ingest_key_previous: str | None
    ingest_key_rotated_at: AwareDatetime | None
    ingest_key_grace_until: AwareDatetime | None
    updated_at: AwareDatetime

    @field_validator("first_run_complete", mode="before")
    @classmethod
    def _coerce_bool(cls, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return bool(value)
        raise TypeError(f"first_run_complete must be int or bool, got {type(value).__name__}")


class SettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self) -> SettingsDTO:
        """Return the singleton settings row.

        Raises `RuntimeError` if the row is missing — that means
        `schema_init` did not run, which is a bootstrap bug, not a
        recoverable state.
        """
        result = await self._s.execute(select(SettingsORM).where(SettingsORM.id == 1))
        row = result.scalar_one_or_none()
        if row is None:
            raise RuntimeError(
                "settings singleton row is missing; schema_init must run "
                "before any repository call"
            )
        return SettingsDTO.model_validate(row)

    async def update(
        self,
        *,
        first_run_complete: bool | None = None,
        idle_timeout_seconds: int | None = None,
        log_retention_days: int | None = None,
    ) -> SettingsDTO:
        values: dict[str, Any] = {"updated_at": datetime.now(tz=UTC)}
        if first_run_complete is not None:
            values["first_run_complete"] = 1 if first_run_complete else 0
        if idle_timeout_seconds is not None:
            if idle_timeout_seconds <= 0:
                raise ValueError("idle_timeout_seconds must be positive")
            values["idle_timeout_seconds"] = idle_timeout_seconds
        if log_retention_days is not None:
            if log_retention_days <= 0:
                raise ValueError("log_retention_days must be positive")
            values["log_retention_days"] = log_retention_days
        await self._s.execute(update(SettingsORM).where(SettingsORM.id == 1).values(**values))
        return await self.get()

    async def rotate_ingest_key(
        self,
        *,
        new_key: str,
        grace_until: datetime,
        now: datetime | None = None,
    ) -> SettingsDTO:
        """Replace ingest_key_current; keep the prior value as previous.

        The previous key remains accepted by the on_publish webhook
        until `grace_until` (per ADR-0003 ingest-key rotation flow).

        The previous-value assignment uses a SQL column self-reference
        (`SET ingest_key_previous = ingest_key_current, ...`) so the
        rotation is atomic at the statement level — no read-then-write
        window can interleave a second rotation that loses the chain.
        """
        if grace_until.tzinfo is None:
            raise ValueError("grace_until must be timezone-aware")
        actual_now = now if now is not None else datetime.now(tz=UTC)
        if actual_now.tzinfo is None:
            raise ValueError("now must be timezone-aware")

        await self._s.execute(
            update(SettingsORM)
            .where(SettingsORM.id == 1)
            .values(
                ingest_key_previous=SettingsORM.ingest_key_current,
                ingest_key_current=new_key,
                ingest_key_rotated_at=actual_now,
                ingest_key_grace_until=grace_until,
                updated_at=actual_now,
            )
        )
        return await self.get()
