"""Targets repository (per ADR-0009 — Credential is its own entity)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator
from sqlalchemy import delete, select, update
from sqlalchemy.engine.cursor import CursorResult

from app.db.models import TargetORM

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession


class TargetDTO(BaseModel):
    """Frozen view over a `targets` row.

    `settings_json` is parsed from TEXT into a dict at the DTO boundary
    so callers see structured data, not a string. A future ADR may
    promote the per-type config to a typed model; for now this is a
    permissive `dict[str, Any]`.
    """

    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: str
    type: str
    label: str
    url: str
    enabled: bool
    settings: dict[str, Any]
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("enabled", mode="before")
    @classmethod
    def _coerce_enabled(cls, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return bool(value)
        raise TypeError(f"enabled must be int or bool, got {type(value).__name__}")

    @field_validator("settings", mode="before")
    @classmethod
    def _decode_settings_json(cls, value: Any) -> dict[str, Any]:
        # When validating from an ORM row, pydantic v2 passes the ORM
        # attribute through. Our ORM exposes the raw TEXT column as
        # `settings_json`, which we expose to the DTO under the
        # canonical attribute name `settings`. Decoding happens here so
        # callers always see a dict.
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            decoded = json.loads(value)
            if not isinstance(decoded, dict):
                raise ValueError("targets.settings_json must decode to a JSON object")
            return decoded
        raise TypeError(f"settings must be str or dict, got {type(value).__name__}")

    @classmethod
    def from_orm(cls, row: TargetORM) -> TargetDTO:
        # We rename `settings_json` -> `settings` at the DTO boundary,
        # so direct from_attributes doesn't suffice; build the dict
        # explicitly.
        return cls.model_validate(
            {
                "id": row.id,
                "type": row.type,
                "label": row.label,
                "url": row.url,
                "enabled": row.enabled,
                "settings": row.settings_json,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
        )


class TargetsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(
        self,
        *,
        type: str,
        label: str,
        url: str,
        enabled: bool = True,
        settings: dict[str, Any] | None = None,
    ) -> TargetDTO:
        target_id = uuid.uuid4().hex
        now = datetime.now(tz=UTC)
        row = TargetORM(
            id=target_id,
            type=type,
            label=label,
            url=url,
            enabled=1 if enabled else 0,
            settings_json=json.dumps(settings or {}, separators=(",", ":")),
            created_at=now,
            updated_at=now,
        )
        self._s.add(row)
        await self._s.flush()
        return TargetDTO.from_orm(row)

    async def get_by_id(self, target_id: str) -> TargetDTO | None:
        result = await self._s.execute(select(TargetORM).where(TargetORM.id == target_id))
        row = result.scalar_one_or_none()
        return TargetDTO.from_orm(row) if row is not None else None

    async def list_all(self) -> Sequence[TargetDTO]:
        result = await self._s.execute(select(TargetORM).order_by(TargetORM.created_at.asc()))
        return [TargetDTO.from_orm(row) for row in result.scalars().all()]

    async def list_enabled(self) -> Sequence[TargetDTO]:
        result = await self._s.execute(
            select(TargetORM).where(TargetORM.enabled == 1).order_by(TargetORM.created_at.asc())
        )
        return [TargetDTO.from_orm(row) for row in result.scalars().all()]

    async def update(
        self,
        target_id: str,
        *,
        label: str | None = None,
        url: str | None = None,
        enabled: bool | None = None,
        settings: dict[str, Any] | None = None,
    ) -> TargetDTO | None:
        values: dict[str, Any] = {"updated_at": datetime.now(tz=UTC)}
        if label is not None:
            values["label"] = label
        if url is not None:
            values["url"] = url
        if enabled is not None:
            values["enabled"] = 1 if enabled else 0
        if settings is not None:
            values["settings_json"] = json.dumps(settings, separators=(",", ":"))
        await self._s.execute(update(TargetORM).where(TargetORM.id == target_id).values(**values))
        return await self.get_by_id(target_id)

    async def delete(self, target_id: str) -> bool:
        result = cast(
            "CursorResult[Any]",
            await self._s.execute(delete(TargetORM).where(TargetORM.id == target_id)),
        )
        return result.rowcount > 0
