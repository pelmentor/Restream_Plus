"""Pin the error-code wire contract.

The frontend (Phase 7) treats every `ErrorCode` value as a stable
string — a rename here is a breaking change. The test enumerates
the codes Phase 6 ships with so a `git log -p` on a rename PR
surfaces the change at review time.
"""

from __future__ import annotations

from app.api.errors import (
    ERROR_CODES,
    ErrorCode,
    http_exception,
    install_exception_handlers,
)
from app.domain.errors import IllegalRunStateTransitionError
from app.domain.run_state import RunAction, RunState
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

PHASE_6_CODES_FROZEN: frozenset[str] = frozenset(
    {
        "service_locked",
        "service_unlocking",
        "unlock_failed",
        "unlock_rate_limited",
        "unauthorized",
        "invalid_credentials",
        "rate_limited",
        "reprompt_required",
        "reprompt_invalid_or_expired",
        "target_not_found",
        "target_in_use",
        "target_misconfigured",
        "credential_required",
        "unknown_target_type",
        "invalid_target_url",
        "illegal_run_state",
        "ingest_key_invalid",
        "api_token_not_found",
        "auth_state_not_initialised",
        "not_found",
        "bad_request",
        # Phase 9 additions — see phase-9-design-memo §K. Renames here
        # are frontend-breaking; new codes append.
        "credential_not_found",
        "new_passphrase_too_weak",
        "same_passphrase",
        "run_active",
        "session_not_found",
    }
)
"""The shipping set of stable error codes through Phase 9. New codes
append; renames break the frontend's `messages.ts` mapping. The frozen
set is declared in the test (not imported from `app/api/errors.py`) so
an accidental rename in the source flips the test at PR time."""


def test_error_codes_contract_is_pinned() -> None:
    """Asserts every Phase 6 wire code is present and no extras leaked.

    Add a NEW code? Update `PHASE_6_CODES_FROZEN` in this test (and
    document the addition in the Phase 6 §"what landed" docs). Rename
    a code? Same — but flag the frontend break in the PR description.
    """
    actual = frozenset(ERROR_CODES)
    extra = actual - PHASE_6_CODES_FROZEN
    missing = PHASE_6_CODES_FROZEN - actual
    assert not missing, f"frozen codes missing from ErrorCode enum: {sorted(missing)}"
    assert (
        not extra
    ), f"new codes added to ErrorCode without updating PHASE_6_CODES_FROZEN: {sorted(extra)}"


def test_error_code_values_unique() -> None:
    """No two enum members share a wire string."""
    values = [c.value for c in ErrorCode]
    assert len(values) == len(set(values))


def test_http_exception_helper_uses_enum_value() -> None:
    exc = http_exception(ErrorCode.TARGET_NOT_FOUND, status.HTTP_404_NOT_FOUND)
    assert exc.status_code == 404
    assert exc.detail == "target_not_found"


def test_illegal_run_state_transition_returns_409() -> None:
    """The registered handler maps the domain exception to a 409 +
    `illegal_run_state`. Round-tripped through a probe app so the
    integration is exercised end-to-end."""
    app = FastAPI()
    install_exception_handlers(app)

    @app.get("/probe")
    def _probe() -> None:
        raise IllegalRunStateTransitionError(state=RunState.ARMED, action=RunAction.START_REQUESTED)

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/probe")
        assert r.status_code == 409
        assert r.json() == {"detail": "illegal_run_state"}


def test_error_code_strenum_inherits_str() -> None:
    """The enum is a str-enum so `code.value` and `f"{code}"` work."""
    assert isinstance(ErrorCode.SERVICE_LOCKED.value, str)
    assert isinstance(ErrorCode.SERVICE_LOCKED, str)


def test_error_codes_tuple_matches_enum() -> None:
    """`ERROR_CODES` is derived from the enum members; size matches."""
    assert len(ERROR_CODES) == len(list(ErrorCode))
