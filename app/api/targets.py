"""Targets router: CRUD + credential set/clear.

Delete is reprompt-protected per ADR-0005 — losing a target's config
is a destructive operation. Setting a credential is NOT reprompted —
the user is already authenticated, and adding/replacing a key is a
normal-path operation in the Settings UI.

Credential lifetime is determined by the target's TYPE (per ADR-0009);
the handler reads `lifetime_for(type)` rather than letting the caller
choose. VK is `per_session`, everyone else is `persistent`.

When a target is mutated while a run is active, the supervisor is
notified via `enable_target` / `disable_target` so workers come and
go in lockstep with the DB. The supervisor's own methods are no-ops
when no run is active, so this is safe to call unconditionally.
"""

from __future__ import annotations

import secrets
from typing import Annotated

import structlog
from fastapi import APIRouter, Header, Response, status

from app.api.deps import SupervisorDep
from app.api.errors import ErrorCode, http_exception
from app.api.schemas import (
    CredentialSetRequest,
    CredentialSummary,
    RevealCredentialResponse,
    Target,
    TargetCreateRequest,
    TargetUpdateRequest,
)
from app.auth.deps import (
    AuthenticatedRequestDep,
    AuthStateDep,
    DerivedKeysDep,
    SessionDep,
)
from app.auth.reprompts import RepromptScope
from app.crypto.aead import AEADDecryptionError, decrypt_credential, encrypt_credential
from app.crypto.kdf import KDF_SALT_LENGTH
from app.domain.credential_policy import lifetime_for
from app.domain.target_types import TARGET_TYPE_SPECS, TargetType
from app.domain.worker_state import WorkerRole
from app.repositories.audit_log import AuditLogRepository
from app.repositories.credentials import CredentialDTO, CredentialsRepository
from app.repositories.targets import TargetDTO, TargetsRepository

_logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/targets", tags=["targets"])


# ----------------------------------------------------------------------
# Projection helper: TargetDTO + CredentialDTO → Target wire schema
# ----------------------------------------------------------------------


def _project_target(dto: TargetDTO, cred: CredentialDTO | None) -> Target:
    known_types = {t.value for t in TargetType}
    target_type = TargetType(dto.type) if dto.type in known_types else TargetType.CUSTOM
    return Target(
        id=dto.id,
        type=target_type,
        label=dto.label,
        url=dto.url,
        enabled=dto.enabled,
        settings=dto.settings,
        has_credential=cred is not None,
        credential_last4=cred.last4 if cred is not None else None,
        created_at=dto.created_at,
        updated_at=dto.updated_at,
    )


def _validate_target_type(value: str) -> TargetType:
    try:
        return TargetType(value)
    except ValueError as exc:
        raise http_exception(ErrorCode.UNKNOWN_TARGET_TYPE, status.HTTP_400_BAD_REQUEST) from exc


# ----------------------------------------------------------------------
# GET /api/targets — list all
# ----------------------------------------------------------------------


@router.get("", response_model=list[Target])
async def list_targets(
    _: AuthenticatedRequestDep,
    session: SessionDep,
) -> list[Target]:
    targets_repo = TargetsRepository(session)
    creds_repo = CredentialsRepository(session)
    targets = await targets_repo.list_all()
    out: list[Target] = []
    for t in targets:
        cred = await creds_repo.get_for_target(t.id)
        out.append(_project_target(t, cred))
    return out


# ----------------------------------------------------------------------
# GET /api/targets/{target_id} — single
# ----------------------------------------------------------------------


@router.get("/{target_id}", response_model=Target)
async def get_target(
    target_id: str,
    _: AuthenticatedRequestDep,
    session: SessionDep,
) -> Target:
    targets_repo = TargetsRepository(session)
    dto = await targets_repo.get_by_id(target_id)
    if dto is None:
        raise http_exception(ErrorCode.TARGET_NOT_FOUND, status.HTTP_404_NOT_FOUND)
    cred = await CredentialsRepository(session).get_for_target(target_id)
    return _project_target(dto, cred)


# ----------------------------------------------------------------------
# POST /api/targets — create
# ----------------------------------------------------------------------


@router.post("", response_model=Target, status_code=status.HTTP_201_CREATED)
async def create_target(
    body: TargetCreateRequest,
    auth: AuthenticatedRequestDep,
    state: AuthStateDep,
    session: SessionDep,
    supervisor: SupervisorDep,
) -> Target:
    # The schema already constrained `type` to TargetType, so the
    # validate call is a no-op for happy path — but TargetType("custom")
    # is the only string that needs `compose_out_url`'s rtmp(s):// check,
    # and that lives in compose. Defer URL validation to credential-set
    # time so empty-credential targets can be created freely.
    _ = TARGET_TYPE_SPECS[body.type]  # raises KeyError on the impossible enum gap

    targets_repo = TargetsRepository(session)
    dto = await targets_repo.create(
        type=body.type.value,
        label=body.label,
        url=body.url,
        enabled=body.enabled,
        settings=body.settings,
    )
    # Hex Audit BA-F4 (slice 8): audit append participates in the
    # business transaction so the forensic row and the business write
    # commit atomically (or roll back together on failure).
    audit = AuditLogRepository(state.sessionmaker)
    await audit.append(
        session,
        event_type="target_created",
        actor=auth.user.username,
        target_id=dto.id,
        data={"type": body.type.value, "label": body.label},
    )
    # Commit BEFORE the supervisor call so the supervisor's own session
    # observes the just-created target (SQLite is single-writer; we
    # cannot read uncommitted state across sessions).
    await session.commit()

    # If a run is active and the new target is enabled, the supervisor
    # checks for an active credential before spawning; with none yet,
    # this is a no-op until the user sets the credential.
    if body.enabled:
        await supervisor.enable_target(dto.id)
    return _project_target(dto, cred=None)


# ----------------------------------------------------------------------
# PATCH endpoint — partial target update
# ----------------------------------------------------------------------


@router.patch("/{target_id}", response_model=Target)
async def update_target(
    target_id: str,
    body: TargetUpdateRequest,
    auth: AuthenticatedRequestDep,
    state: AuthStateDep,
    session: SessionDep,
    supervisor: SupervisorDep,
) -> Target:
    targets_repo = TargetsRepository(session)
    existing = await targets_repo.get_by_id(target_id)
    if existing is None:
        raise http_exception(ErrorCode.TARGET_NOT_FOUND, status.HTTP_404_NOT_FOUND)

    updated = await targets_repo.update(
        target_id,
        label=body.label,
        url=body.url,
        enabled=body.enabled,
        settings=body.settings,
    )
    if updated is None:  # pragma: no cover — race between get and update
        raise http_exception(ErrorCode.TARGET_NOT_FOUND, status.HTTP_404_NOT_FOUND)

    # Hex Audit BA-F4 (slice 8): audit in the business txn.
    audit = AuditLogRepository(state.sessionmaker)
    await audit.append(
        session,
        event_type="target_updated",
        actor=auth.user.username,
        target_id=target_id,
        data=dict(body.model_dump(exclude_unset=True)),
    )
    await session.commit()

    # Track enable/disable transitions for the supervisor.
    if body.enabled is not None:
        if body.enabled and not existing.enabled:
            await supervisor.enable_target(target_id)
        elif (not body.enabled) and existing.enabled:
            await supervisor.disable_target(target_id)
    elif existing.enabled:
        # Other fields (label/url/settings) changed; in a running state
        # the supervisor needs to re-spawn against the new URL. Cycle
        # the target.
        await supervisor.disable_target(target_id)
        await supervisor.enable_target(target_id)

    cred = await CredentialsRepository(session).get_for_target(target_id)
    return _project_target(updated, cred)


# ----------------------------------------------------------------------
# DELETE /api/targets/{target_id} — reprompt-protected
# ----------------------------------------------------------------------


@router.delete(
    "/{target_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_target(
    target_id: str,
    auth: AuthenticatedRequestDep,
    state: AuthStateDep,
    session: SessionDep,
    supervisor: SupervisorDep,
    x_reprompt_grant: Annotated[str | None, Header(alias="X-Reprompt-Grant")] = None,
) -> Response:
    if not x_reprompt_grant:
        raise http_exception(ErrorCode.REPROMPT_REQUIRED, status.HTTP_403_FORBIDDEN)
    if not state.reprompts.consume(
        grant_id=x_reprompt_grant, user_id=auth.user.id, scope=RepromptScope.DELETE_TARGET
    ):
        raise http_exception(ErrorCode.REPROMPT_INVALID_OR_EXPIRED, status.HTTP_403_FORBIDDEN)

    targets_repo = TargetsRepository(session)
    existing = await targets_repo.get_by_id(target_id)
    if existing is None:
        raise http_exception(ErrorCode.TARGET_NOT_FOUND, status.HTTP_404_NOT_FOUND)

    # Stop workers FIRST so the supervisor doesn't race the DB delete.
    await supervisor.disable_target(target_id)
    deleted = await targets_repo.delete(target_id)
    if not deleted:  # pragma: no cover — race
        raise http_exception(ErrorCode.TARGET_NOT_FOUND, status.HTTP_404_NOT_FOUND)

    # Hex Audit BA-F4 (slice 8): audit in the business txn. `target_id`
    # is intentionally NOT passed — the row was just deleted in this
    # same transaction and the audit_log FK would refuse the insert.
    # The deleted target's id appears in `data["target_id"]` for
    # trail-readability instead.
    audit = AuditLogRepository(state.sessionmaker)
    await audit.append(
        session,
        event_type="target_deleted",
        actor=auth.user.username,
        data={"target_id": target_id, "label": existing.label},
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------------
# PUT /api/targets/{target_id}/credential — set/replace stream key
# ----------------------------------------------------------------------


@router.put("/{target_id}/credential", response_model=CredentialSummary)
async def set_credential(
    target_id: str,
    body: CredentialSetRequest,
    auth: AuthenticatedRequestDep,
    state: AuthStateDep,
    session: SessionDep,
    keys: DerivedKeysDep,
    supervisor: SupervisorDep,
) -> CredentialSummary:
    """Encrypt + persist a fresh stream key for the target.

    Lifetime is `lifetime_for(target.type)` — the caller never picks it
    (per ADR-0009). VK targets get `per_session`; everyone else
    `persistent`. The supervisor is notified so a running enabled
    target spawns workers against the new key immediately.
    """
    targets_repo = TargetsRepository(session)
    target = await targets_repo.get_by_id(target_id)
    if target is None:
        raise http_exception(ErrorCode.TARGET_NOT_FOUND, status.HTTP_404_NOT_FOUND)

    target_type = _validate_target_type(target.type)
    lifetime = lifetime_for(target_type)

    plaintext = body.stream_key
    salt = secrets.token_bytes(KDF_SALT_LENGTH)
    # Hex Audit BA-F5 (slice 7): always write v2 AAD, which binds
    # target_id. Legacy v1 rows continue to decrypt via the fallback
    # path in `decrypt_credential`; rolling migration happens on the
    # next write (here) or during rotate-passphrase.
    nonce, ciphertext = encrypt_credential(
        keys.stream_key_kek, plaintext.encode("utf-8"), salt, target_id
    )
    last4 = plaintext[-4:] if len(plaintext) >= 4 else plaintext

    creds = CredentialsRepository(session)
    dto = await creds.upsert(
        target_id=target_id,
        lifetime=lifetime.value,
        ciphertext=ciphertext,
        nonce=nonce,
        salt=salt,
        last4=last4,
    )

    # Hex Audit BA-F4 (slice 8): audit in the business txn.
    audit = AuditLogRepository(state.sessionmaker)
    await audit.append(
        session,
        event_type="credential_set",
        actor=auth.user.username,
        target_id=target_id,
        data={"lifetime": lifetime.value, "last4": last4},
    )
    await session.commit()

    if target.enabled:
        # If a run is active, the supervisor picks up the new credential
        # by re-reading the DB on enable_target.
        await supervisor.enable_target(target_id)

    return CredentialSummary(
        last4=dto.last4,
        lifetime=lifetime.value,
        created_at=dto.created_at,
        expires_at=dto.expires_at,
    )


# ----------------------------------------------------------------------
# POST /api/targets/{target_id}/reveal-credential — reprompt-protected (Phase 9)
# ----------------------------------------------------------------------


@router.post(
    "/{target_id}/reveal-credential",
    response_model=RevealCredentialResponse,
)
async def reveal_credential(
    target_id: str,
    auth: AuthenticatedRequestDep,
    state: AuthStateDep,
    session: SessionDep,
    keys: DerivedKeysDep,
    x_reprompt_grant: Annotated[str | None, Header(alias="X-Reprompt-Grant")] = None,
) -> RevealCredentialResponse:
    """AEAD-decrypt and return the stored stream key after a fresh
    AuthReprompt grant (Phase 9; phase-9-design-memo §I.1).

    A VK target between sessions has no credential row — that returns
    404 `CREDENTIAL_NOT_FOUND` with no special-case error (the Settings
    UI for VK doesn't expose the reveal affordance, so a 404 here is a
    UI bug). Plaintext lives in the response and the client's short-
    lived `revealed` SecretField state only; never in the TanStack
    cache, never in any log line. The audit row carries `last4` only.
    """
    if not x_reprompt_grant:
        raise http_exception(ErrorCode.REPROMPT_REQUIRED, status.HTTP_403_FORBIDDEN)
    if not state.reprompts.consume(
        grant_id=x_reprompt_grant,
        user_id=auth.user.id,
        scope=RepromptScope.REVEAL_STREAM_KEY,
    ):
        raise http_exception(ErrorCode.REPROMPT_INVALID_OR_EXPIRED, status.HTTP_403_FORBIDDEN)

    targets_repo = TargetsRepository(session)
    target = await targets_repo.get_by_id(target_id)
    if target is None:
        raise http_exception(ErrorCode.TARGET_NOT_FOUND, status.HTTP_404_NOT_FOUND)

    cred = await CredentialsRepository(session).get_for_target(target_id)
    if cred is None:
        raise http_exception(ErrorCode.CREDENTIAL_NOT_FOUND, status.HTTP_404_NOT_FOUND)

    try:
        plaintext_bytes = decrypt_credential(
            keys.stream_key_kek,
            cred.nonce,
            cred.ciphertext,
            cred.salt,
            target_id,
        )
    except AEADDecryptionError:
        # Corrupted at-rest material or a mid-rotation race; surface a
        # generic 500. Detailed exception type is intentionally not
        # leaked to the wire (audit-log-only).
        _logger.exception(
            "credential_reveal_aead_failed",
            target_id=target_id,
            credential_id=cred.id,
        )
        raise http_exception(ErrorCode.NOT_FOUND, status.HTTP_500_INTERNAL_SERVER_ERROR) from None
    plaintext = plaintext_bytes.decode("utf-8")

    # Hex Audit BA-F4 (slice 8): audit in the business txn.
    audit = AuditLogRepository(state.sessionmaker)
    await audit.append(
        session,
        event_type="credential_revealed",
        actor=auth.user.username,
        target_id=target_id,
        data={"last4": cred.last4},
    )
    await session.commit()
    return RevealCredentialResponse(plaintext=plaintext, last4=cred.last4)


# ----------------------------------------------------------------------
# DELETE /api/targets/{target_id}/credential — clear
# ----------------------------------------------------------------------


@router.delete(
    "/{target_id}/credential",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def clear_credential(
    target_id: str,
    auth: AuthenticatedRequestDep,
    state: AuthStateDep,
    session: SessionDep,
    supervisor: SupervisorDep,
    x_reprompt_grant: Annotated[str | None, Header(alias="X-Reprompt-Grant")] = None,
) -> Response:
    # Reprompt-protected (security review H-3): clearing a credential
    # silently drops all live streams to this target. A stolen-cookie
    # attacker without this gate can knock targets offline at will.
    if not x_reprompt_grant:
        raise http_exception(ErrorCode.REPROMPT_REQUIRED, status.HTTP_403_FORBIDDEN)
    if not state.reprompts.consume(
        grant_id=x_reprompt_grant, user_id=auth.user.id, scope=RepromptScope.CLEAR_CREDENTIAL
    ):
        raise http_exception(ErrorCode.REPROMPT_INVALID_OR_EXPIRED, status.HTTP_403_FORBIDDEN)

    targets_repo = TargetsRepository(session)
    target = await targets_repo.get_by_id(target_id)
    if target is None:
        raise http_exception(ErrorCode.TARGET_NOT_FOUND, status.HTTP_404_NOT_FOUND)

    creds = CredentialsRepository(session)
    removed = await creds.delete_for_target(target_id)
    if not removed:
        # Already absent — idempotent.
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # Hex Audit BA-F4 (slice 8): audit in the business txn.
    audit = AuditLogRepository(state.sessionmaker)
    await audit.append(
        session,
        event_type="credential_cleared",
        actor=auth.user.username,
        target_id=target_id,
    )
    await session.commit()
    # Tear down workers (no credential ⇒ nothing to push).
    await supervisor.disable_target(target_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------------
# POST /api/targets/{target_id}/reset-worker — clear FAILED_OPEN
# ----------------------------------------------------------------------


@router.post(
    "/{target_id}/reset-worker",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def reset_target_worker(
    target_id: str,
    role: WorkerRole,
    auth: AuthenticatedRequestDep,
    state: AuthStateDep,
    supervisor: SupervisorDep,
    x_reprompt_grant: Annotated[str | None, Header(alias="X-Reprompt-Grant")] = None,
) -> Response:
    """User clicked 'Retry now' on a FAILED_OPEN tile. Routes through
    the supervisor's `reset_target_worker` which is a no-op if no
    worker is running for that (target, role).

    Hex Audit BA-F13 (slice 10): reprompt-protected. A stolen-cookie
    attacker without this gate can spam the breaker reset and defeat
    the per-target circuit-breaker's noisy-neighbor protection
    (ADR-0003 §Reconnect policy), earning the operator an IP-ban from
    the target platform.
    """
    if not x_reprompt_grant:
        raise http_exception(ErrorCode.REPROMPT_REQUIRED, status.HTTP_403_FORBIDDEN)
    if not state.reprompts.consume(
        grant_id=x_reprompt_grant,
        user_id=auth.user.id,
        scope=RepromptScope.RESET_TARGET_WORKER,
    ):
        raise http_exception(ErrorCode.REPROMPT_INVALID_OR_EXPIRED, status.HTTP_403_FORBIDDEN)
    await supervisor.reset_target_worker(target_id, role)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
