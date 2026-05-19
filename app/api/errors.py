"""Error code constants and exception → HTTPException handlers.

Per the Phase 6 design memo §Q10: the wire shape is the Starlette
default `{"detail": "<code>"}` for application errors and the FastAPI
default `{"detail": [{...validation errors...}]}` for 422. A custom
envelope would force every handler through a single dispatch path
without any concrete benefit — the frontend already switches on
shape (string vs list) to render, and `detail` is the stable contract
the API publishes.

The codes listed in `ERROR_CODES` are pinned by a unit test
(`tests/api/test_errors.py`) so renaming any one of them is a
breaking change the test suite catches.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.domain.errors import (
    IllegalRunStateTransitionError,
    IllegalStateTransitionError,
    IllegalWorkerStateTransitionError,
)

if TYPE_CHECKING:  # pragma: no cover
    from fastapi import FastAPI


class ErrorCode(StrEnum):
    """The set of `detail` strings any handler may return.

    Extending this enum is the canonical way to introduce a new
    failure mode. The wire value is stable — frontend pins these
    strings in `web/src/messages.ts` (Phase 7) to render localized
    copy. A rename here is a frontend-breaking change.
    """

    # Liveness / passphrase
    SERVICE_LOCKED = "service_locked"
    SERVICE_UNLOCKING = "service_unlocking"
    UNLOCK_FAILED = "unlock_failed"
    UNLOCK_RATE_LIMITED = "unlock_rate_limited"

    # Auth
    UNAUTHORIZED = "unauthorized"
    INVALID_CREDENTIALS = "invalid_credentials"
    RATE_LIMITED = "rate_limited"
    REPROMPT_REQUIRED = "reprompt_required"
    REPROMPT_INVALID_OR_EXPIRED = "reprompt_invalid_or_expired"

    # Targets / credentials
    TARGET_NOT_FOUND = "target_not_found"
    TARGET_IN_USE = "target_in_use"
    TARGET_MISCONFIGURED = "target_misconfigured"
    CREDENTIAL_REQUIRED = "credential_required"
    UNKNOWN_TARGET_TYPE = "unknown_target_type"
    INVALID_TARGET_URL = "invalid_target_url"

    # Run control
    ILLEGAL_RUN_STATE = "illegal_run_state"

    # Settings / ingest key
    INGEST_KEY_INVALID = "ingest_key_invalid"

    # API tokens
    API_TOKEN_NOT_FOUND = "api_token_not_found"  # noqa: S105

    # Phase 9 — credentials reveal, passphrase rotate, sessions
    CREDENTIAL_NOT_FOUND = "credential_not_found"
    NEW_PASSPHRASE_TOO_WEAK = "new_passphrase_too_weak"  # noqa: S105 — error code value
    SAME_PASSPHRASE = "same_passphrase"  # noqa: S105 — error code value
    RUN_ACTIVE = "run_active"
    SESSION_NOT_FOUND = "session_not_found"

    # Structural failures
    AUTH_STATE_NOT_INITIALISED = "auth_state_not_initialised"
    NOT_FOUND = "not_found"
    BAD_REQUEST = "bad_request"

    # Slice 8.5 (Hex Audit FG3-F7): persist-failure code for
    # change-password, distinguishable from generic 500. The merged-
    # txn refactor (BA-F4) means an audit-write failure rolls back the
    # password update; the user retried experiences "looks like 500
    # but old password still works", which is a confusion-prone UX.
    # 503 + Retry-After + this distinct code lets the SPA show
    # "Try again in a moment" instead of a generic "Something went
    # wrong".
    PASSWORD_CHANGE_RETRY = "password_change_retry"  # noqa: S105


# Frozen tuple form for the test that pins the Phase 6 shipping set.
# Codes added by future phases append to this; renames break the
# frontend contract and are caught by `tests/api/test_errors.py`.
ERROR_CODES: tuple[str, ...] = tuple(c.value for c in ErrorCode)


async def _illegal_run_state_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """`IllegalRunStateTransitionError` → 409 `illegal_run_state`.

    The supervisor raises this synchronously from `start_run` / `stop_run`
    when the current `RunState` does not accept the requested action
    (e.g., a second START before STOP completes). 409 Conflict is the
    HTTP idiom for "the resource is in a state that disallows this
    operation". `IllegalWorkerStateTransitionError` is treated
    identically since it surfaces from the same code path on rare
    races where a worker has already transitioned past the action.
    """
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": ErrorCode.ILLEGAL_RUN_STATE.value},
    )


def install_exception_handlers(app: FastAPI) -> None:
    """Wire the typed-exception → HTTPException translations on `app`.

    Most handlers raise `HTTPException(detail=ErrorCode.X.value)`
    directly; this function only registers the few cross-cutting
    translations that don't fit a per-handler pattern.
    """
    app.add_exception_handler(IllegalRunStateTransitionError, _illegal_run_state_handler)
    app.add_exception_handler(IllegalWorkerStateTransitionError, _illegal_run_state_handler)
    app.add_exception_handler(IllegalStateTransitionError, _illegal_run_state_handler)


def http_exception(code: ErrorCode, status_code: int) -> HTTPException:
    """Tight constructor for the common pattern.

    `raise http_exception(ErrorCode.TARGET_NOT_FOUND, 404)` reads
    cleaner than `raise HTTPException(404, detail=ErrorCode.TARGET_NOT_FOUND.value)`
    and forces every handler through the enum (a typo can't compile).
    """
    return HTTPException(status_code=status_code, detail=code.value)


__all__ = [
    "ERROR_CODES",
    "ErrorCode",
    "http_exception",
    "install_exception_handlers",
]
