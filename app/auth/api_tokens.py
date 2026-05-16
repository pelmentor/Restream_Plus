"""Bearer API token orchestration: issue, verify, revoke.

Symmetric to `app.auth.sessions` but for headless / scripted use per
ADR-0005 §"API tokens":

  - Token plaintext is `secrets.token_urlsafe(40)` (~55 chars,
    320 bits of entropy — longer than session cookies because the
    user pastes this into scripts and one-time-reveal flows where
    extra entropy is cheap).
  - HMAC fingerprint stored at `api_tokens.token_hash` (BLOB UNIQUE),
    keyed by `api-token-mac-v1` (ADR-0006 §"Key derivation").
  - Token plaintext is shown to the user **exactly once** at the
    `/api/security/tokens` UI (Phase 9 builds the
    OneTimeRevealBanner) and is never persisted in cleartext.
  - Tokens never expire; the operator revokes them (UI button) by
    setting `revoked_at` rather than deleting the row, so the audit
    trail survives.
  - Transport is `Authorization: Bearer <token>` only — query-param
    auth is rejected per ADR-0005, because query strings end up in
    log surfaces the application redactor cannot see (nginx access
    logs, browser referer, shell history).

This module produces business values; the FastAPI surface
(`app/auth/deps.py`, `app/api/api_tokens_api.py` in Phase 6) speaks
HTTP.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import structlog

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Sequence

    from app.auth.key_material import DerivedKeys
    from app.repositories.api_tokens import ApiTokenDTO, ApiTokensRepository

_logger = structlog.get_logger(__name__)

API_TOKEN_RANDOM_BYTES: Final[int] = 40
"""`secrets.token_urlsafe(40)` produces 54 base64url chars = 320 bits.

ADR-0005 says "40 chars of `secrets.token_urlsafe`", which is the
*output length* the user sees. We're explicit about the input here
because that's what the Python API takes."""

API_TOKEN_PREFIX: Final[str] = "rpat_"
"""Restream-Plus API Token prefix. Lets operators visually
distinguish API tokens from session cookies and from third-party
keys, and gives leak-scanning tools (e.g., GitHub secret scanning) a
stable signature to match. The prefix is part of the token value
that's hashed — changing it is a token-revocation event, not a
display-only knob."""

_TOKEN_PREFIX_RE: Final[re.Pattern[str]] = re.compile(
    rf"^{re.escape(API_TOKEN_PREFIX)}[A-Za-z0-9_-]+$"
)
"""Strict shape: the prefix followed by URL-safe-base64 characters.

Used by `verify_bearer` to short-circuit obvious garbage without
spending an HMAC computation."""

BEARER_AUTHORIZATION_RE: Final[re.Pattern[str]] = re.compile(r"^Bearer +(\S+) *$", re.IGNORECASE)
"""Match `Authorization: Bearer <token>` and capture the token.

Per Phase 3 security review #13: tolerance is tightened from `\\s+` to
literal-space `+`. RFC 7230 disallows obs-fold (`\\r\\n `) in modern
HTTP, and tabs are not idiomatic between an auth scheme and its
credential. Modern HTTP parsers reject folded headers before they
reach here; this regex would also reject them. Case-insensitive on
the scheme keyword per RFC 7235 §2.1."""


@dataclass(frozen=True, slots=True)
class CreatedApiToken:
    """Result of `ApiTokenService.create()`.

    `plaintext` is the only place the bearer value ever exists. The
    HTTP layer puts it in the one-time-reveal response body; nothing
    else persists or returns it. `dto` is the metadata-only DTO that
    populates the token-list UI from then on.
    """

    plaintext: str
    dto: ApiTokenDTO


class InvalidBearerError(RuntimeError):
    """The bearer header was missing, malformed, or the token did not
    match any active row. The FastAPI deps layer maps to
    `HTTPException(401, "invalid_bearer")` with no distinction between
    "wrong format" and "revoked" — leak-resistant by collapsing
    failure modes.
    """


def generate_api_token_plaintext() -> str:
    """Fresh bearer value: `rpat_` + 54 URL-safe random chars.

    The prefix lives outside the random portion so the entropy of the
    token equals the entropy of `secrets.token_urlsafe(40)` (320 bits).
    """
    return API_TOKEN_PREFIX + secrets.token_urlsafe(API_TOKEN_RANDOM_BYTES)


def compute_api_token_hash(plaintext: str, mac_key: bytes) -> bytes:
    """HMAC-SHA256(mac_key, utf-8(plaintext)) → 32-byte digest.

    The DB stores this fingerprint at `api_tokens.token_hash`. A
    request's bearer is verified by re-hashing under the same key and
    doing a single-row lookup by the UNIQUE column.
    """
    if not isinstance(plaintext, str):
        raise TypeError("plaintext must be a string")
    if len(plaintext) == 0:
        raise ValueError("plaintext must not be empty")
    if not isinstance(mac_key, bytes):
        raise TypeError("mac_key must be bytes")
    return hmac.new(mac_key, plaintext.encode("utf-8"), hashlib.sha256).digest()


def extract_bearer_from_authorization(header_value: str | None) -> str | None:
    """Parse a raw `Authorization` header value → bearer plaintext or None.

    Returns None for any of: missing header, wrong scheme, malformed
    value, or non-matching shape. The caller's `Authorization` header
    is the only acceptable transport; query parameters and form
    fields are explicitly NOT accepted per ADR-0005.
    """
    if header_value is None:
        return None
    match = BEARER_AUTHORIZATION_RE.match(header_value)
    if match is None:
        return None
    token = match.group(1)
    if not _TOKEN_PREFIX_RE.match(token):
        # Wrong shape; don't spend an HMAC on it. The 401 response is
        # the same either way, so this is purely a cost-saving guard
        # against trivial bearer-stuffing.
        return None
    return token


class ApiTokenService:
    """Coordinates API token issuance, verification, and revocation.

    Takes the `DerivedKeys` value rather than the `KeyMaterial`
    service for the same reason `SessionAuthService` does: the deps
    layer resolves `require_ready()` once at the request boundary, and
    tests can inject deterministic keys.
    """

    def __init__(
        self,
        *,
        tokens: ApiTokensRepository,
        keys: DerivedKeys,
    ) -> None:
        self._tokens = tokens
        self._keys = keys

    async def create(self, *, user_id: str, label: str) -> CreatedApiToken:
        """Mint a fresh token owned by `user_id`. Plaintext is returned once.

        The label is the human-readable name the operator typed in the
        UI ("home-cli", "monitoring-script"). Whitespace is trimmed;
        an empty label after trim is rejected to keep the audit trail
        readable. `user_id` is the FK to `users.id`; the auth router
        passes `current_user.id` so the freshly-issued token is owned
        by whoever proved their identity.
        """
        cleaned_label = label.strip() if isinstance(label, str) else ""
        if cleaned_label == "":
            raise ValueError("label must be a non-empty string after stripping whitespace")
        if not user_id:
            raise ValueError("user_id must be a non-empty string")

        plaintext = generate_api_token_plaintext()
        token_hash = compute_api_token_hash(plaintext, self._keys.api_token_mac_key)
        dto = await self._tokens.create(user_id=user_id, token_hash=token_hash, label=cleaned_label)
        _logger.info(
            "api_token_created",
            token_id=dto.id,
            user_id=user_id,
            label=cleaned_label,
            hash_prefix=token_hash[:4].hex(),
        )
        return CreatedApiToken(plaintext=plaintext, dto=dto)

    async def verify_bearer(self, plaintext: str | None) -> ApiTokenDTO | None:
        """Bearer → active token DTO, or None.

        Touches `last_used_at` on success. Returns None for malformed
        bearer (caller maps to 401), missing-from-DB (same), or
        revoked (same). Failure modes collapse so a probe cannot
        distinguish "no such token" from "revoked token" — both produce
        an identical 401.
        """
        if not plaintext:
            return None
        if not _TOKEN_PREFIX_RE.match(plaintext):
            return None

        token_hash = compute_api_token_hash(plaintext, self._keys.api_token_mac_key)
        dto = await self._tokens.get_active_by_token_hash(token_hash)
        if dto is None:
            return None

        await self._tokens.touch_last_used(dto.id, datetime.now(tz=UTC))
        return dto

    async def revoke(self, token_id: str) -> bool:
        """Mark a token revoked. Returns True iff a row was updated."""
        return await self._tokens.revoke(token_id, datetime.now(tz=UTC))

    async def list_all(self) -> Sequence[ApiTokenDTO]:
        """Metadata listing for the Settings UI. No plaintext leaks here —
        the DTO does not expose `token_hash` and never carried the
        plaintext."""
        return await self._tokens.list_all()


__all__ = [
    "API_TOKEN_PREFIX",
    "API_TOKEN_RANDOM_BYTES",
    "ApiTokenService",
    "BEARER_AUTHORIZATION_RE",
    "CreatedApiToken",
    "InvalidBearerError",
    "compute_api_token_hash",
    "extract_bearer_from_authorization",
    "generate_api_token_plaintext",
]
