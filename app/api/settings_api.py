"""Settings router: GET / PATCH / rotate ingest key.

The ingest key is masked in GET responses — only `last4` and
`has_previous` metadata leak. The only place the plaintext is
returned is the `POST /api/settings/rotate-ingest-key` endpoint, and
even there it lands in a one-time response body (the UI surfaces it
via the Phase 9 `OneTimeRevealBanner`).

Per ADR-0003 the previous key remains accepted by the on_publish
webhook until `grace_until`. The rotate endpoint sets the grace
window via `SettingsRepository.rotate_ingest_key`, which uses a
column self-reference for an atomic statement-level update.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Final

import structlog
from fastapi import APIRouter, Header, status

from app.api.errors import ErrorCode, http_exception
from app.api.schemas import (
    IngestKeyRevealResponse,
    IngestKeyRotatedResponse,
    SettingsUpdateRequest,
    SettingsView,
)
from app.auth.deps import (
    AuthenticatedRequestDep,
    AuthStateDep,
    SessionDep,
)
from app.auth.reprompts import RepromptScope
from app.db.schema_init import INGEST_KEY_BYTES
from app.repositories.audit_log import AuditLogRepository
from app.repositories.settings_repo import SettingsDTO, SettingsRepository

_logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])


INGEST_KEY_GRACE_DEFAULT: Final[timedelta] = timedelta(minutes=10)
"""How long the previous ingest key keeps working after a rotation.

10 minutes is short enough that a leaked key gets revoked quickly,
long enough for a human to copy the new key into OBS without dropping
their stream. ADR-0003 §"Ingest key rotation" leaves the value to
the implementation; this is the implementation choice."""


def _project_settings(dto: SettingsDTO) -> SettingsView:
    current = dto.ingest_key_current
    last4 = current[-4:] if len(current) >= 4 else current
    return SettingsView(
        first_run_complete=dto.first_run_complete,
        idle_timeout_seconds=dto.idle_timeout_seconds,
        log_retention_days=dto.log_retention_days,
        ingest_key_last4=last4,
        ingest_key_has_previous=dto.ingest_key_previous is not None,
        ingest_key_rotated_at=dto.ingest_key_rotated_at,
        ingest_key_grace_until=dto.ingest_key_grace_until,
        updated_at=dto.updated_at,
    )


# ----------------------------------------------------------------------
# GET /api/settings
# ----------------------------------------------------------------------


@router.get("", response_model=SettingsView)
async def get_settings(
    _: AuthenticatedRequestDep,
    session: SessionDep,
) -> SettingsView:
    return _project_settings(await SettingsRepository(session).get())


# ----------------------------------------------------------------------
# PATCH /api/settings (benign — no reprompt)
# ----------------------------------------------------------------------


@router.patch("", response_model=SettingsView)
async def update_settings(
    body: SettingsUpdateRequest,
    auth: AuthenticatedRequestDep,
    state: AuthStateDep,
    session: SessionDep,
) -> SettingsView:
    updated = await SettingsRepository(session).update(
        idle_timeout_seconds=body.idle_timeout_seconds,
        log_retention_days=body.log_retention_days,
    )
    # Hex Audit BA-F4 (slice 8): audit in the business txn.
    audit = AuditLogRepository(state.sessionmaker)
    await audit.append(
        session,
        event_type="settings_updated",
        actor=auth.user.username,
        data=dict(body.model_dump(exclude_unset=True)),
    )
    await session.commit()
    return _project_settings(updated)


# ----------------------------------------------------------------------
# POST /api/settings/rotate-ingest-key — reprompt-protected
# ----------------------------------------------------------------------


@router.post(
    "/rotate-ingest-key",
    response_model=IngestKeyRotatedResponse,
)
async def rotate_ingest_key(
    auth: AuthenticatedRequestDep,
    state: AuthStateDep,
    session: SessionDep,
    x_reprompt_grant: Annotated[str | None, Header(alias="X-Reprompt-Grant")] = None,
) -> IngestKeyRotatedResponse:
    if not x_reprompt_grant:
        raise http_exception(ErrorCode.REPROMPT_REQUIRED, status.HTTP_403_FORBIDDEN)
    if not state.reprompts.consume(
        grant_id=x_reprompt_grant,
        user_id=auth.user.id,
        scope=RepromptScope.REGENERATE_INGEST_KEY,
    ):
        raise http_exception(ErrorCode.REPROMPT_INVALID_OR_EXPIRED, status.HTTP_403_FORBIDDEN)

    new_key = secrets.token_urlsafe(INGEST_KEY_BYTES)
    now = datetime.now(tz=UTC)
    grace_until = now + INGEST_KEY_GRACE_DEFAULT
    updated = await SettingsRepository(session).rotate_ingest_key(
        new_key=new_key, grace_until=grace_until, now=now
    )
    # Hex Audit BA-F4 (slice 8): audit in the business txn.
    audit = AuditLogRepository(state.sessionmaker)
    await audit.append(
        session,
        event_type="ingest_key_rotated",
        actor=auth.user.username,
        data={"new_last4": new_key[-4:], "grace_until": grace_until.isoformat()},
    )
    await session.commit()

    last4 = new_key[-4:]
    # The `updated` DTO has the masked snapshot; the plaintext is
    # returned ONLY here, ONCE.
    _ = updated
    return IngestKeyRotatedResponse(
        plaintext=new_key,
        last4=last4,
        grace_until=grace_until,
    )


# ----------------------------------------------------------------------
# POST /api/settings/reveal-ingest-key — reprompt-protected (Phase 9)
# ----------------------------------------------------------------------


@router.post("/reveal-ingest-key", response_model=IngestKeyRevealResponse)
async def reveal_ingest_key(
    auth: AuthenticatedRequestDep,
    state: AuthStateDep,
    session: SessionDep,
    x_reprompt_grant: Annotated[str | None, Header(alias="X-Reprompt-Grant")] = None,
) -> IngestKeyRevealResponse:
    """Surface the plaintext ingest key after a fresh AuthReprompt grant.

    Distinct from `rotate-ingest-key`: this endpoint does NOT mutate the
    key, only reveals it. The plaintext lives in
    `settings.ingest_key_current` (plaintext TEXT column — nginx-rtmp's
    on_publish webhook needs it without the unlocked KEK per
    phase-9-design-memo §I.2). The audit row carries `last4` only;
    the plaintext is never logged.
    """
    if not x_reprompt_grant:
        raise http_exception(ErrorCode.REPROMPT_REQUIRED, status.HTTP_403_FORBIDDEN)
    if not state.reprompts.consume(
        grant_id=x_reprompt_grant,
        user_id=auth.user.id,
        scope=RepromptScope.REVEAL_INGEST_KEY,
    ):
        raise http_exception(ErrorCode.REPROMPT_INVALID_OR_EXPIRED, status.HTTP_403_FORBIDDEN)

    dto = await SettingsRepository(session).get()
    plaintext = dto.ingest_key_current
    last4 = plaintext[-4:] if len(plaintext) >= 4 else plaintext

    # Hex Audit BA-F4 (slice 8): audit in the business txn — closes the
    # forensic gap where the read happened but the audit row could
    # silently fail on its own session.
    audit = AuditLogRepository(state.sessionmaker)
    await audit.append(
        session,
        event_type="ingest_key_revealed",
        actor=auth.user.username,
        data={"last4": last4},
    )
    await session.commit()
    return IngestKeyRevealResponse(plaintext=plaintext, last4=last4)


__all__ = ["router"]
