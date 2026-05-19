"""Auth router: login / logout / change-password / unlock / reprompt.

Two routers exported:

  `auth_router`   mounted at `/api/auth` — login, logout, logout-all,
                  change-password, reprompt grant issuance.
  `unlock_router` mounted at `/api/unlock` — the ONLY endpoint that
                  works in locked mode (per `LockedModeMiddleware`
                  allowlist).

Per Phase 6 design memo §Q7: `BackgroundTasks` schedules the post-
login Argon2id rehash AFTER the response body is sent so the
~100 ms cost is paid off the request hot path. The closure captures
the sessionmaker (NOT a session — that one is already closed by the
time the BG task runs) and uses `asyncio.to_thread` for the actual
Argon2 work, mirroring the verifier's threading pattern.

Per Phase 6 design memo §Q5: unlock has its own rate limiter, NOT
shared with login. 3/IP/15min per ADR-0010.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Final

import structlog
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Request,
    Response,
    status,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import SettingsDep
from app.api.errors import ErrorCode, http_exception
from app.api.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    RepromptGrantResponse,
    RepromptIssueRequest,
    UnlockRequest,
    UnlockResponse,
    UserSummary,
)
from app.auth.deps import (
    AuthenticatedRequestDep,
    AuthStateDep,
    ClientIpDep,
    SessionDep,
    SessionServiceDep,
)
from app.auth.key_material import KeyMaterial, KeyMaterialState
from app.auth.rate_limit import LoginRateLimiter
from app.auth.reprompts import REPROMPT_GRANT_TTL_SECONDS, RepromptScope
from app.auth.sessions import (
    InvalidCredentialsError,
    RateLimitedError,
    build_cookie_delete_kwargs,
    build_cookie_set_kwargs,
    session_cookie_name,
)
from app.crypto.passwords import hash_password, verify_password
from app.repositories.audit_log import AuditLogRepository
from app.repositories.users import UsersRepository

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncSession

_logger = structlog.get_logger(__name__)

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])
unlock_router = APIRouter(prefix="/api/unlock", tags=["auth"])

CHANGE_PASSWORD_TIMING_FLOOR_SECONDS: Final[float] = 0.30
"""Wall-clock floor on `change_password` so the wrong-current-password
path takes the same time as the success path.

The success path runs Argon2 verify (~100 ms) + Argon2 hash (~100 ms) +
session revoke + audit (~5 ms). The failure path returns after verify
(~100 ms) without the hash. Without padding, an attacker with a
stolen reprompt grant could distinguish "wrong current password" from
"correct" via timing — collapsing the reprompt's defence-in-depth.
Per Phase 6 security review H-5. The 300 ms ceiling is generous
relative to Argon2's natural latency."""

REPROMPT_TIMING_FLOOR_SECONDS: Final[float] = 0.30
"""Slice 7 / Hex Audit BA-F3: wall-clock floor on `issue_reprompt_grant`
so the wrong-password path and the success path are indistinguishable
by timing.

Both paths execute Argon2 verify (~100 ms). The success path also
calls `RepromptStore.issue` (~0 ms in-memory) before returning; the
failure path returns after verify. The natural delta is sub-millisecond
but a 300 ms floor erases any residual signal. Reused value (mirrors
`CHANGE_PASSWORD_TIMING_FLOOR_SECONDS`) so future Argon2 cost changes
update both knobs together."""


# ----------------------------------------------------------------------
# /api/auth/login (+ background rehash)
# ----------------------------------------------------------------------


@auth_router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    response: Response,
    background: BackgroundTasks,
    session_service: SessionServiceDep,
    state: AuthStateDep,
    client_ip: ClientIpDep,
    settings: SettingsDep,
    user_agent: Annotated[str | None, Header(alias="User-Agent")] = None,
) -> LoginResponse:
    try:
        outcome = await session_service.login(
            username=body.username,
            password=body.password,
            ip=client_ip,
            user_agent=user_agent,
        )
    except RateLimitedError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ErrorCode.RATE_LIMITED.value,
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except InvalidCredentialsError as exc:
        raise http_exception(ErrorCode.INVALID_CREDENTIALS, status.HTTP_401_UNAUTHORIZED) from exc

    response.set_cookie(
        **build_cookie_set_kwargs(outcome.cookie_value, secure=settings.cookie_secure)  # type: ignore[arg-type]
    )

    if outcome.needs_rehash:
        # Closes Phase-3 deferred gap per Phase 6 design memo §Q7.
        # Runs AFTER the response is sent (Starlette guarantees this
        # via the BackgroundTasks contract); idempotent under races;
        # failure-tolerant.
        background.add_task(
            _rehash_admin_password,
            sessionmaker=state.sessionmaker,
            user_id=outcome.user.id,
            plaintext=body.password,
        )

    return LoginResponse(
        user=UserSummary(
            id=outcome.user.id,
            username=outcome.user.username,
            last_login_at=outcome.user.last_login_at,
        )
    )


async def _rehash_admin_password(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    user_id: str,
    plaintext: str,
) -> None:
    """Background body. Idempotent under concurrent logins; failure-tolerant.

    The plaintext lives in Python's request-scope GC for the lifetime
    of this coroutine. ADR-0006's threat model already accepts
    in-process plaintext, so no extra zeroization is performed
    (mirroring `app/auth/sessions.py::_login_body`).
    """
    try:
        new_hash = await asyncio.to_thread(hash_password, plaintext)
        # Hex Audit BA-F4 (slice 8): rehash + audit in ONE txn so a
        # mid-write failure rolls back both. Background-task fire-and-
        # forget posture is preserved (the outer except still swallows
        # the failure into a structured-log line — no client to fail
        # toward).
        async with sessionmaker() as session, session.begin():
            users = UsersRepository(session)
            await users.update_password_hash(user_id, new_hash)
            audit = AuditLogRepository(sessionmaker)
            await audit.append(session, event_type="password_rehashed", actor="system")
    except Exception:
        _logger.exception("password_rehash_failed", user_id=user_id)


# ----------------------------------------------------------------------
# /api/auth/me — session probe for the SPA
# ----------------------------------------------------------------------


@auth_router.get("/me", response_model=UserSummary)
async def me(auth: AuthenticatedRequestDep) -> UserSummary:
    """Return the authenticated user, or 401 if the cookie is missing/expired.

    Load-bearing for the Phase 7 frontend's `<RequireAuth>` guard: on a hard
    reload of the SPA, this is how the client learns whether its
    `__Host-rp_session` cookie still resolves to a live session without
    issuing a side-effect probe against an unrelated endpoint.
    """
    return UserSummary(
        id=auth.user.id,
        username=auth.user.username,
        last_login_at=auth.user.last_login_at,
    )


# ----------------------------------------------------------------------
# /api/auth/logout + /logout-all
# ----------------------------------------------------------------------


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    session_service: SessionServiceDep,
    settings: SettingsDep,
) -> Response:
    cookie_name = session_cookie_name(secure=settings.cookie_secure)
    cookie_value = request.cookies.get(cookie_name)
    if cookie_value:
        await session_service.revoke(cookie_value)
    response.delete_cookie(**build_cookie_delete_kwargs(secure=settings.cookie_secure))  # type: ignore[arg-type]
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@auth_router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    auth: AuthenticatedRequestDep,
    response: Response,
    session_service: SessionServiceDep,
    settings: SettingsDep,
) -> Response:
    """Revoke every cookie session for the user. The current cookie is
    among them; the client should treat the call as a forced logout
    everywhere."""
    await session_service.revoke_all_for_user(auth.user.id)
    response.delete_cookie(**build_cookie_delete_kwargs(secure=settings.cookie_secure))  # type: ignore[arg-type]
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


# ----------------------------------------------------------------------
# /api/auth/change-password — reprompt-protected
# ----------------------------------------------------------------------


@auth_router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePasswordRequest,
    auth: AuthenticatedRequestDep,
    state: AuthStateDep,
    session: SessionDep,
    session_service: SessionServiceDep,
    response: Response,
    settings: SettingsDep,
    x_reprompt_grant: Annotated[str | None, Header(alias="X-Reprompt-Grant")] = None,
) -> Response:
    """Reprompt-protected per ADR-0005. Revokes ALL sessions for the
    user after the change so every device must re-authenticate (the
    current device's cookie is cleared by `delete_cookie` so the SPA
    sees the forced logout immediately).
    """
    if not x_reprompt_grant:
        raise http_exception(ErrorCode.REPROMPT_REQUIRED, status.HTTP_403_FORBIDDEN)
    if not state.reprompts.consume(
        grant_id=x_reprompt_grant, user_id=auth.user.id, scope=RepromptScope.CHANGE_PASSWORD
    ):
        raise http_exception(ErrorCode.REPROMPT_INVALID_OR_EXPIRED, status.HTTP_403_FORBIDDEN)

    # Wrap the body in a constant-time floor — security review H-5.
    start = time.perf_counter()
    try:
        # Verify the supplied current password as final guard. The reprompt
        # already proved knowledge at issue-time; this catches a UI-bug
        # case where a stolen grant gets used with a different payload.
        # `password_hash` is non-optional since SCHEMA_VERSION=2.
        verify_result = await asyncio.to_thread(
            verify_password,
            auth.user.password_hash.get_secret_value(),
            body.current_password,
        )
        if not verify_result.ok:
            raise http_exception(ErrorCode.INVALID_CREDENTIALS, status.HTTP_401_UNAUTHORIZED)

        new_hash = await asyncio.to_thread(hash_password, body.new_password)
        users = UsersRepository(session)
        await users.update_password_hash(auth.user.id, new_hash)
        await session_service.revoke_all_for_user(auth.user.id)
        # Hex Audit BA-F4 (slice 8): audit in the business txn. The
        # prior "commit BEFORE audit to avoid SQLite write-lock
        # deadlock" pattern documented the BA-F4 bug — two writers
        # against a single-writer DB. ONE writer now: append to the
        # same session, commit once.
        #
        # Slice 8.5 (Hex Audit FG3-F7): a `SQLAlchemyError` from the
        # audit-append OR the commit rolls back the password update.
        # Under a generic 500 path the client sees an opaque error
        # with the normal timing floor — INDISTINGUISHABLE from a
        # successful change in client-side behavior. Distinguish it
        # via `PASSWORD_CHANGE_RETRY` + 503 + `Retry-After` so the SPA
        # can render "Try again in a moment" rather than "Something
        # went wrong" while leaving the existing session valid.
        audit = AuditLogRepository(state.sessionmaker)
        try:
            await audit.append(session, event_type="password_changed", actor=auth.user.username)
            await session.commit()
        except (SQLAlchemyError, OSError, TimeoutError) as exc:
            # Slice 8.5 reviewer-pass note: `SQLAlchemyError` alone is
            # too narrow — `session.commit()` can also surface
            # `OSError` on disk-full / fs-level WAL failures, and
            # `TimeoutError` on async-driver wait-timeouts. The merged
            # BA-F4 txn already guarantees ATOMICITY (no password-
            # changed + 503 split is possible — `commit()` failure
            # rolls back BOTH writes); this widening only improves UX
            # disambiguation so the SPA gets the same distinguishable
            # 503 across all transient persist failures.
            _logger.critical(
                "password_change_persist_failed",
                exc_type=type(exc).__name__,
                user_id=auth.user.id,
                note=(
                    "audit-append or commit failed; password update "
                    "rolled back. Client receives 503 + Retry-After."
                ),
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=ErrorCode.PASSWORD_CHANGE_RETRY.value,
                headers={"Retry-After": "5"},
            ) from exc

        response.delete_cookie(**build_cookie_delete_kwargs(secure=settings.cookie_secure))  # type: ignore[arg-type]
        response.status_code = status.HTTP_204_NO_CONTENT
        return response
    finally:
        elapsed = time.perf_counter() - start
        remaining = CHANGE_PASSWORD_TIMING_FLOOR_SECONDS - elapsed
        if remaining > 0:
            await asyncio.sleep(remaining)


# ----------------------------------------------------------------------
# /api/auth/reprompt — issue a single-use grant
# ----------------------------------------------------------------------


def _scope_or_400(scope: str) -> RepromptScope:
    try:
        return RepromptScope(scope)
    except ValueError as exc:
        raise http_exception(ErrorCode.BAD_REQUEST, status.HTTP_400_BAD_REQUEST) from exc


@auth_router.post("/reprompt", response_model=RepromptGrantResponse)
async def issue_reprompt_grant(
    body: RepromptIssueRequest,
    auth: AuthenticatedRequestDep,
    state: AuthStateDep,
    client_ip: ClientIpDep,
) -> RepromptGrantResponse:
    """Mint a single-use 60-second grant for a sensitive operation.

    The user re-types their password as a confirmation step; on
    success we issue a scope-bound grant id that the destructive
    handler will consume via `X-Reprompt-Grant`.

    Failure modes collapse to a single 401 — wrong password, rate-
    limited probing, or locked account return the same body so a probe
    can't distinguish them. Rate-limited responses additionally carry
    a `Retry-After` header so the SPA can render a backoff countdown
    (the header surfaces the same information the SPA would otherwise
    infer from sustained latency anyway). (`password_hash` is
    non-optional since SCHEMA_VERSION=2, so the historical "missing
    hash" failure mode is no longer constructible.)

    Slice 7 / Hex Audit BA-F3: rate-limited by
    `state.reprompt_rate_limiter` (3 attempts per IP+username per
    15 min — tighter than login's 5, since legitimate humans only trip
    this on a typed-wrong-password during a destructive op). Wrapped
    in a constant-time floor (`REPROMPT_TIMING_FLOOR_SECONDS`) so the
    wrong-password and right-password paths are indistinguishable to
    a stolen-cookie attacker probing from inside the session.
    """
    scope = _scope_or_400(body.scope)
    username = auth.user.username

    # Step 1: rate-limit gate, BEFORE Argon2. A denied attempt spends
    # no server CPU and the response is shaped identically to a wrong-
    # password failure (same 401 detail), differing only in the
    # Retry-After header the SPA needs to render a backoff timer.
    decision = state.reprompt_rate_limiter.check(ip=client_ip, username=username)
    if not decision.allowed:
        _logger.info(
            "reprompt_rate_limited",
            ip=client_ip,
            user_id=auth.user.id,
            scope=scope.value,
            retry_after_seconds=decision.retry_after_seconds,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ErrorCode.RATE_LIMITED.value,
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )

    start = time.perf_counter()
    try:
        verify_result = await asyncio.to_thread(
            verify_password,
            auth.user.password_hash.get_secret_value(),
            body.password,
        )
        if not verify_result.ok:
            state.reprompt_rate_limiter.record_failure(ip=client_ip, username=username)
            raise http_exception(ErrorCode.INVALID_CREDENTIALS, status.HTTP_401_UNAUTHORIZED)
        grant_id = state.reprompts.issue(user_id=auth.user.id, scope=scope)
        # Approximate expiry as `now + TTL`; the store keys lifetime on
        # monotonic-clock issuance, so this is the wall-clock equivalent.
        return RepromptGrantResponse(
            grant_id=grant_id,
            expires_at=datetime.now(tz=UTC) + timedelta(seconds=REPROMPT_GRANT_TTL_SECONDS),
        )
    finally:
        elapsed = time.perf_counter() - start
        remaining = REPROMPT_TIMING_FLOOR_SECONDS - elapsed
        if remaining > 0:
            await asyncio.sleep(remaining)


# ----------------------------------------------------------------------
# /api/unlock — the locked-mode escape hatch
# ----------------------------------------------------------------------


def get_unlock_rate_limiter(state: AuthStateDep) -> LoginRateLimiter:
    """The unlock limiter lives on `AuthState.unlock_rate_limiter` —
    constructed in the lifespan with 3 attempts / 15-minute window
    (ADR-0010). Kept distinct from `state.rate_limiter` (which is the
    login limiter) so unlock probing does not lock out future logins
    and vice versa."""
    return state.unlock_rate_limiter


UnlockRateLimiterDep = Annotated[LoginRateLimiter, Depends(get_unlock_rate_limiter)]


def get_key_material_unsafe(state: AuthStateDep) -> KeyMaterial:
    """Distinct from `app.auth.deps.get_key_material` — that one is
    aliased to `KeyMaterialDep` and depends on routes the locked-mode
    middleware blocks. This helper does not gate on READY; the unlock
    endpoint must be able to read the LOCKED state to do its job."""
    return state.key_material


KeyMaterialUnsafeDep = Annotated[KeyMaterial, Depends(get_key_material_unsafe)]


@unlock_router.post("", response_model=UnlockResponse)
async def unlock(
    body: UnlockRequest,
    km: KeyMaterialUnsafeDep,
    limiter: UnlockRateLimiterDep,
    client_ip: ClientIpDep,
) -> UnlockResponse:
    """Unlock the process: derive `DerivedKeys` from the master passphrase.

    Idempotent under READY (the call returns 200 with `state="ready"`
    even if already unlocked). Failures pad to the rate-limit floor —
    the limiter's per-IP bucket protects against passphrase brute-force
    (single-admin app, one operator, no parallel unlock UIs).
    """
    # Bucket every unlock attempt under a sentinel username so the
    # per-username bucket effectively becomes a per-process bucket
    # (single-admin model means there's only one legitimate user).
    decision = limiter.check(ip=client_ip, username="__unlock__")
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=ErrorCode.UNLOCK_RATE_LIMITED.value,
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )

    if km.state == KeyMaterialState.READY:
        return UnlockResponse()
    try:
        await km.unlock(body.passphrase)
    except Exception as exc:
        limiter.record_failure(ip=client_ip, username="__unlock__")
        # Security review L-2: log the type, not `str(exc)` — Argon2 /
        # HKDF exception messages may include parameter values, and disk
        # logs an operator shares for debugging would surface them.
        _logger.warning("unlock_failed", ip=client_ip, exc_type=type(exc).__name__)
        raise http_exception(ErrorCode.UNLOCK_FAILED, status.HTTP_401_UNAUTHORIZED) from exc
    return UnlockResponse()


__all__ = ["auth_router", "unlock_router"]
