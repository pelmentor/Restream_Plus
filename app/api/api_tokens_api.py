"""API-token management router: list / create / revoke.

The plaintext token is shown EXACTLY ONCE — in the `POST` response
body — and never persisted in cleartext (only the HMAC fingerprint
is stored, per ADR-0005). The Phase-9 UI surfaces the plaintext via
the `OneTimeRevealBanner`.

Revoke is reprompt-protected per ADR-0005: a stolen cookie should
not be able to delete the admin's API tokens silently.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Header, Response, status

from app.api.errors import ErrorCode, http_exception
from app.api.schemas import (
    ApiTokenCreatedResponse,
    ApiTokenCreateRequest,
    ApiTokenView,
)
from app.auth.deps import (
    ApiTokenServiceDep,
    AuthenticatedRequestDep,
    AuthStateDep,
    SessionDep,
)
from app.auth.reprompts import RepromptScope
from app.repositories.api_tokens import ApiTokenDTO
from app.repositories.audit_log import AuditLogRepository

_logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/security/tokens", tags=["api-tokens"])


def _project_token(dto: ApiTokenDTO) -> ApiTokenView:
    return ApiTokenView(
        id=dto.id,
        label=dto.label,
        created_at=dto.created_at,
        last_used_at=dto.last_used_at,
        revoked_at=dto.revoked_at,
    )


@router.get("", response_model=list[ApiTokenView])
async def list_tokens(
    _: AuthenticatedRequestDep,
    api_tokens: ApiTokenServiceDep,
) -> list[ApiTokenView]:
    rows = await api_tokens.list_all()
    return [_project_token(t) for t in rows]


@router.post(
    "",
    response_model=ApiTokenCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_token(
    body: ApiTokenCreateRequest,
    auth: AuthenticatedRequestDep,
    state: AuthStateDep,
    api_tokens: ApiTokenServiceDep,
    session: SessionDep,
) -> ApiTokenCreatedResponse:
    created = await api_tokens.create(user_id=auth.user.id, label=body.label)
    # Commit the request session BEFORE the audit so the audit repo's
    # own short-lived session doesn't race the request write lock.
    await session.commit()
    audit = AuditLogRepository(state.sessionmaker)
    await audit.append(
        event_type="api_token_created",
        actor=auth.user.username,
        data={"token_id": created.dto.id, "label": body.label},
    )
    return ApiTokenCreatedResponse(
        plaintext=created.plaintext,
        token=_project_token(created.dto),
    )


@router.delete(
    "/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def revoke_token(
    token_id: str,
    auth: AuthenticatedRequestDep,
    state: AuthStateDep,
    api_tokens: ApiTokenServiceDep,
    session: SessionDep,
    x_reprompt_grant: Annotated[str | None, Header(alias="X-Reprompt-Grant")] = None,
) -> Response:
    if not x_reprompt_grant:
        raise http_exception(ErrorCode.REPROMPT_REQUIRED, status.HTTP_403_FORBIDDEN)
    if not state.reprompts.consume(
        grant_id=x_reprompt_grant,
        user_id=auth.user.id,
        scope=RepromptScope.REVOKE_API_TOKEN,
    ):
        raise http_exception(ErrorCode.REPROMPT_INVALID_OR_EXPIRED, status.HTTP_403_FORBIDDEN)

    revoked = await api_tokens.revoke(token_id)
    if not revoked:
        raise http_exception(ErrorCode.API_TOKEN_NOT_FOUND, status.HTTP_404_NOT_FOUND)
    await session.commit()
    audit = AuditLogRepository(state.sessionmaker)
    await audit.append(
        event_type="api_token_revoked",
        actor=auth.user.username,
        data={"token_id": token_id},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
