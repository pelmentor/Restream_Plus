"""FastAPI dependency helpers — the boundary between auth business
logic and the HTTP surface.

This module is the only place in `app.auth` that imports FastAPI.
Everything else is plain Python + repository / crypto primitives so
the business logic stays testable without spinning up a server.

The dependency contract:

  `Depends(get_auth_state)`         → `AuthState` (the lifespan-built bundle).
  `Depends(get_key_material)`       → the singleton.
  `Depends(require_unlocked_keys)`  → `DerivedKeys` (raises 503 when locked).
  `Depends(get_db_session)`         → an `AsyncSession`, lifespan-scoped.
  `Depends(get_session_service)`    → `SessionAuthService` built per-request.
  `Depends(get_api_token_service)`  → `ApiTokenService` built per-request.
  `Depends(get_client_ip)`          → string per the trusted-proxy walk.
  `Depends(get_current_user_via_cookie)` →
       `AuthenticatedRequest | None`; reads the `__Host-rp_session` cookie.
  `Depends(get_current_user_via_bearer)` →
       `AuthenticatedRequest | None`; reads `Authorization: Bearer ...`.
  `Depends(require_authenticated)`  →
       `AuthenticatedRequest`; either auth method; 401 otherwise.
  `Depends(require_reprompt_grant(scope))` →
       parametrised dep factory; consumes a single-use grant or 403.

The FastAPI app (`app/main.py`, Phase 6) is responsible for
constructing the `AuthState` in `lifespan` and attaching it to
`app.state.auth`. Until Phase 6 lands, these deps are unit-tested
against a minimal FastAPI test app at `tests/auth/test_deps.py`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Final

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.api_tokens import ApiTokenService, extract_bearer_from_authorization
from app.auth.key_material import (
    DerivedKeys,
    KeyMaterial,
    KeyMaterialState,
    ServiceLockedError,
)
from app.auth.last_seen_coalescer import LastSeenCoalescer
from app.auth.rate_limit import LoginRateLimiter, extract_client_ip
from app.auth.reprompts import RepromptScope, RepromptStore
from app.auth.sessions import (
    MAX_SESSION_COOKIE_VALUE_LENGTH,
    SessionAuthService,
    session_cookie_name,
)
from app.config import AppSettings
from app.repositories.api_tokens import ApiTokensRepository
from app.repositories.sessions import HttpSessionsRepository
from app.repositories.users import UsersRepository

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

    from app.config import IpNetwork
    from app.repositories.api_tokens import ApiTokenDTO
    from app.repositories.sessions import HttpSessionDTO
    from app.repositories.users import UserDTO


RETRY_AFTER_LOCKED_SECONDS: Final[int] = 2
"""Seconds to wait before re-trying during the `UNLOCKING` window.

Argon2id at the configured cost (128 MiB / 3 / 2) takes ~hundreds of
ms; 2 s is a comfortable upper bound that doesn't busy-loop clients."""


class AuthMethod(StrEnum):
    """Which auth path produced the authenticated request."""

    COOKIE = "cookie"
    BEARER = "bearer"


@dataclass(frozen=True, slots=True)
class AuthenticatedRequest:
    """The result of `require_authenticated`. Holds the authenticated
    user plus the artefact that proved it (the session row for cookie
    auth, the API-token DTO for bearer auth).

    `via` is meant primarily for audit logging and admin-UI telemetry
    so an operator can tell, after the fact, whether a destructive
    change was driven by a browser session or a script's bearer.
    """

    user: UserDTO
    via: AuthMethod
    session: HttpSessionDTO | None
    api_token: ApiTokenDTO | None


@dataclass(slots=True)
class AuthState:
    """Lifespan-built singleton bundle, attached to `app.state.auth`.

    Each field is a long-lived object whose lifecycle is owned by the
    FastAPI `lifespan` context manager (Phase 6):

      - `key_material`         — derives once at boot (env mode) or on
                                 /api/unlock (paste mode); lives the
                                 whole process lifetime.
      - `sessionmaker`         — SQLAlchemy `async_sessionmaker` already
                                 bound to the engine; used to open a
                                 fresh `AsyncSession` per request.
      - `rate_limiter`         — sliding-window store for login attempts.
      - `unlock_rate_limiter`  — separate sliding-window store for unlock
                                 attempts (3/IP/15min per ADR-0010).
                                 Kept distinct from `rate_limiter` so
                                 unlock probing does not lock out future
                                 logins (Phase 6 design memo §Q5).
      - `reprompt_rate_limiter` — separate sliding-window store for
                                 /api/auth/reprompt attempts (3/IP/15min,
                                 tighter than login's 5 since the user
                                 is already authenticated and the only
                                 threat is a stolen-cookie attacker
                                 brute-forcing the admin password from
                                 inside the session). Hex Audit BA-F3
                                 (slice 7).
      - `reprompts`            — single shared in-memory grant store.
      - `last_seen_coalescer`  — process-wide write-coalescer for
                                 `sessions.last_seen_at`, shared by
                                 HTTP auth and the WS upgrade path
                                 (Hex Audit BA-F6 / FG2-H1 / FG2-M7,
                                 slice 7).
      - `trusted_proxies`      — frozen tuple from settings.
    """

    key_material: KeyMaterial
    sessionmaker: async_sessionmaker[AsyncSession]
    rate_limiter: LoginRateLimiter
    unlock_rate_limiter: LoginRateLimiter
    reprompt_rate_limiter: LoginRateLimiter
    reprompts: RepromptStore
    last_seen_coalescer: LastSeenCoalescer
    trusted_proxies: Sequence[IpNetwork]
    rotation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    """Phase 9: process-wide lock held by the rotate-passphrase handler.
    The supervisor's `start_run` consults `rotation_lock.locked()` and
    raises `RUN_ACTIVE` if held — prevents a concurrent START from
    spawning workers that hold OLD `DerivedKeys` while the rotation
    in-flight re-keys credentials underneath them."""


def get_auth_state(request: Request) -> AuthState:
    """Read the lifespan-built bundle off the FastAPI app.

    Treated as a hard invariant: if `app.state.auth` is missing or
    has the wrong type, the lifespan didn't run (mis-configured test
    app, partially-initialised process). A 500 with a clear message
    is correct — better than a confusing `AttributeError` further down.
    """
    state = getattr(request.app.state, "auth", None)
    if not isinstance(state, AuthState):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="auth_state_not_initialised",
        )
    return state


AuthStateDep = Annotated[AuthState, Depends(get_auth_state)]


def get_key_material(state: AuthStateDep) -> KeyMaterial:
    return state.key_material


KeyMaterialDep = Annotated[KeyMaterial, Depends(get_key_material)]


def require_unlocked_keys(key_material: KeyMaterialDep) -> DerivedKeys:
    """Return `DerivedKeys`, or raise 503 with a `Retry-After: 2`.

    Maps:
      - `LOCKED`    → 503 `service_locked` (no Retry-After: requires
                     operator action via /api/unlock).
      - `UNLOCKING` → 503 `service_unlocking` + `Retry-After: 2`.
      - `READY`     → the keys.

    The two states use distinct detail strings so the unlock screen
    and the rest of the app can render different copy.
    """
    state = key_material.state
    if state == KeyMaterialState.UNLOCKING:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="service_unlocking",
            headers={"Retry-After": str(RETRY_AFTER_LOCKED_SECONDS)},
        )
    try:
        return key_material.require_ready()
    except ServiceLockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="service_locked",
        ) from exc


DerivedKeysDep = Annotated[DerivedKeys, Depends(require_unlocked_keys)]


async def get_db_session(state: AuthStateDep) -> AsyncIterator[AsyncSession]:
    """Open one `AsyncSession` per request and commit/rollback on exit.

    Commit-on-success keeps the handler code clean — repositories
    flush, this dep commits. On exception, the rollback path runs and
    the exception bubbles to the framework's error handler. Handlers
    that need finer-grained transaction control (e.g., multi-step
    operations that should be atomic together) can take a
    `SessionDep` *and* call `session.begin_nested()` / explicit
    `commit()` themselves; the outer commit-on-success is harmless in
    that case because all the work is already flushed-and-committed
    by the nested transaction context.
    """
    async with state.sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


def get_session_service(
    session: SessionDep,
    keys: DerivedKeysDep,
    state: AuthStateDep,
) -> SessionAuthService:
    """Per-request construction. Cheap (no IO until the service is called).

    The `state.rate_limiter` is the process-wide singleton, NOT a
    per-request limiter. Embedding it here makes ADR-0005's
    brute-force defence a structural property of every login —
    `SessionAuthService` cannot be constructed without one (Phase 3
    security review CRIT). The `last_seen_coalescer` is similarly
    process-wide; injected here so HTTP cookie verification shares
    the same suppression window as the WS upgrade path (Hex Audit
    BA-F6 / FG2-H1 / FG2-M7, slice 7).
    """
    return SessionAuthService(
        users=UsersRepository(session),
        sessions=HttpSessionsRepository(session),
        keys=keys,
        rate_limiter=state.rate_limiter,
        last_seen_coalescer=state.last_seen_coalescer,
    )


def get_api_token_service(
    session: SessionDep,
    keys: DerivedKeysDep,
) -> ApiTokenService:
    return ApiTokenService(
        tokens=ApiTokensRepository(session),
        keys=keys,
    )


SessionServiceDep = Annotated[SessionAuthService, Depends(get_session_service)]
ApiTokenServiceDep = Annotated[ApiTokenService, Depends(get_api_token_service)]


def get_client_ip(
    request: Request,
    state: AuthStateDep,
    x_forwarded_for: Annotated[str | None, Header(alias="X-Forwarded-For")] = None,
) -> str:
    """Resolve the request's client IP under the trusted-proxy rule.

    Centralised so every place that needs the client IP (rate limit,
    session.ip column, audit log, login UI surface) speaks the same
    answer.
    """
    peer = request.client.host if request.client is not None else None
    return extract_client_ip(
        peer_ip=peer,
        forwarded_for=x_forwarded_for,
        trusted_proxies=state.trusted_proxies,
    )


ClientIpDep = Annotated[str, Depends(get_client_ip)]


async def get_current_user_via_cookie(
    request: Request,
    session_service: SessionServiceDep,
) -> AuthenticatedRequest | None:
    """Read the session cookie and verify it.

    The cookie name is dynamic — `__Host-rp_session` in secure mode
    (the default), `rp_session` when `RESTREAM_COOKIE_SECURE=false`.
    Pulled from `app.state.settings` rather than a Depends-injected
    SettingsDep so this stays usable from places that don't have a
    Depends context (and so a minimal test app without the full
    lifespan can still mount this dep).

    If `app.state.settings` isn't populated (minimal test apps,
    legacy callers), we fall back to the secure (production) default
    so we never silently accept a `rp_session` cookie when the
    operator expected `__Host-` enforcement.

    Returns None when the cookie is absent or invalid; the caller
    decides whether that's a hard 401 (`require_authenticated`) or a
    soft "anonymous" (a public endpoint).
    """
    settings = getattr(request.app.state, "settings", None)
    secure = settings.cookie_secure if isinstance(settings, AppSettings) else True
    cookie_name = session_cookie_name(secure=secure)
    cookie_value = request.cookies.get(cookie_name)
    if not cookie_value:
        return None
    # Hex Audit BA-F19 (slice 10): reject over-long cookie values BEFORE
    # the HMAC; treat as anonymous (same as a missing cookie). The HMAC
    # itself enforces this internally but we short-circuit here so the
    # work cost stays O(1).
    if len(cookie_value) > MAX_SESSION_COOKIE_VALUE_LENGTH:
        return None
    result = await session_service.verify_cookie(cookie_value)
    if result is None:
        return None
    user, http_session = result
    return AuthenticatedRequest(
        user=user, via=AuthMethod.COOKIE, session=http_session, api_token=None
    )


async def get_current_user_via_bearer(
    api_token_service: ApiTokenServiceDep,
    session: SessionDep,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> AuthenticatedRequest | None:
    """Read `Authorization: Bearer ...` and verify it.

    Returns None when the header is absent, malformed, or doesn't
    match any active token. Bearer requests are CSRF-immune by
    construction (browsers don't auto-attach `Authorization`), so the
    cookie posture above doesn't apply here.

    Loads the owner via `token_dto.user_id` (the `api_tokens.user_id`
    FK closed the Phase-3 deferred gap of hardcoding "admin"); a token
    whose user row has been deleted resolves to None (fail-closed),
    even though the ON DELETE CASCADE makes that state unreachable
    via the normal API surface.

    Implementation note (Phase 3 code review #1): the `session`
    parameter and the `api_token_service`'s internal session are the
    SAME `AsyncSession` instance, thanks to FastAPI's dependency
    deduplication — `SessionDep` is the same `Annotated[...,
    Depends(get_db_session)]` reused by `get_api_token_service`,
    and FastAPI caches deps per-request when the same call is
    reached through multiple paths. Removing the `session`
    parameter, or renaming `SessionDep` so the two sites no longer
    share the alias, would silently open a second DB session here
    and lose this guarantee. Do not refactor without preserving the
    deduplication invariant.
    """
    plaintext = extract_bearer_from_authorization(authorization)
    if plaintext is None:
        return None
    token_dto = await api_token_service.verify_bearer(plaintext)
    if token_dto is None:
        return None
    users = UsersRepository(session)
    user = await users.get_by_id(token_dto.user_id)
    if user is None:
        # The FK CASCADE means deleting a user removes their tokens
        # atomically, so seeing a verified-active token with no user
        # row indicates a corrupted state. Fail closed.
        return None
    return AuthenticatedRequest(user=user, via=AuthMethod.BEARER, session=None, api_token=token_dto)


async def require_authenticated(
    via_cookie: Annotated[AuthenticatedRequest | None, Depends(get_current_user_via_cookie)],
    via_bearer: Annotated[AuthenticatedRequest | None, Depends(get_current_user_via_bearer)],
) -> AuthenticatedRequest:
    """401 unless either the cookie or the bearer authenticates.

    Cookie is checked first; bearer second. The order is observable
    in `AuthenticatedRequest.via` so the audit log records which path
    drove the request.
    """
    if via_cookie is not None:
        return via_cookie
    if via_bearer is not None:
        return via_bearer
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="unauthorized",
        headers={"WWW-Authenticate": 'Bearer realm="restream-plus"'},
    )


AuthenticatedRequestDep = Annotated[AuthenticatedRequest, Depends(require_authenticated)]


def require_reprompt_grant(
    scope: RepromptScope,
) -> Callable[[AuthenticatedRequestDep, AuthStateDep, str | None], Awaitable[None]]:
    """Build a dep that consumes a single-use reprompt grant for `scope`.

    Usage on a handler:

        @router.delete("/api/targets/{target_id}")
        async def delete_target(
            target_id: str,
            _: Annotated[None, Depends(require_reprompt_grant(RepromptScope.DELETE_TARGET))],
            ...
        ) -> Response:
            ...

    The grant ID arrives in the `X-Reprompt-Grant` header. A missing
    or invalid grant produces a 403; valid grants are consumed
    server-side (one-shot) before the handler body runs.
    """

    async def _dep(
        auth: AuthenticatedRequestDep,
        state: AuthStateDep,
        x_reprompt_grant: Annotated[str | None, Header(alias="X-Reprompt-Grant")] = None,
    ) -> None:
        # `async def` (not `def`) per Phase 3 code review #3: FastAPI
        # runs sync deps in a thread pool, which would contradict the
        # documented single-event-loop concurrency model of
        # `RepromptStore` (`app/auth/reprompts.py` module docstring).
        # Keeping this on the event loop honors that invariant.
        if not x_reprompt_grant:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="reprompt_required",
            )
        ok = state.reprompts.consume(
            grant_id=x_reprompt_grant,
            user_id=auth.user.id,
            scope=scope,
        )
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="reprompt_invalid_or_expired",
            )

    return _dep


__all__ = [
    "AuthMethod",
    "AuthState",
    "AuthStateDep",
    "AuthenticatedRequest",
    "AuthenticatedRequestDep",
    "ApiTokenServiceDep",
    "ClientIpDep",
    "DerivedKeysDep",
    "KeyMaterialDep",
    "RETRY_AFTER_LOCKED_SECONDS",
    "SessionDep",
    "SessionServiceDep",
    "get_api_token_service",
    "get_auth_state",
    "get_client_ip",
    "get_current_user_via_bearer",
    "get_current_user_via_cookie",
    "get_db_session",
    "get_key_material",
    "get_session_service",
    "require_authenticated",
    "require_reprompt_grant",
    "require_unlocked_keys",
]
