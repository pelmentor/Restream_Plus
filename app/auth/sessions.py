"""Cookie session orchestration: login, verify, revoke.

Cookie value is opaque random (`secrets.token_urlsafe(32)`, 43 chars,
256 bits of entropy). The DB row is keyed by the HMAC-SHA256
fingerprint of the cookie value under `session-token-mac-v1` per
ADR-0005 §"Session validation" + ADR-0006 §"Key derivation". The HMAC
key lives only in process memory, so a stolen DB backup yields
fingerprints, not cookie values that could be replayed.

This module produces business-domain values (the cookie string, the
session DTO, the freshly-touched user DTO). It does NOT speak HTTP —
attaching the cookie to a response, returning JSON, mapping
exceptions to status codes, is the FastAPI layer's job at
`app/auth/deps.py` and `app/api/auth.py` (Phase 6).

Login timing & rate limiting:

  - Argon2id verify is constant-time relative to the supplied
    password but takes ~100 ms.
  - Username-existence lookup is fast — would otherwise leak whether
    a username exists via timing.
  - DB writes after a successful verify (session insert,
    last_login_at update) also contribute to wall-clock time.

  We close the username-existence side channel by:
    (a) Always running a verify_password call, against a sentinel
        "burnt" hash if the username does not exist or has no
        password set. The dummy hash is lazily computed on first
        login so import time stays cheap.
    (b) Padding the *failure* path to a constant-time floor (default
        250 ms) with `asyncio.sleep` if the body finished earlier.
    (c) NOT doing inline password-rehash on successful login. A
        `needs_rehash` flag is surfaced on `LoginOutcome` for an
        out-of-band consumer (Phase 6's BackgroundTasks or a
        scheduled maintenance job). This removes the ~100 ms timing
        spike that previously distinguished a "valid login that
        triggered a rehash" from "valid login that didn't".

  Belt + suspenders. Either one alone would mostly work; both
  together make the rate limiter the dominant defence and remove the
  timing channel as a separate concern.

Rate limiting is enforced HERE rather than at the HTTP layer (per
the Phase 3 security review CRIT finding). Embedding the
`LoginRateLimiter` in `SessionAuthService.login()` makes it
structurally impossible for a future HTTP handler to forget the
check; the limiter is a constructor-required dependency, so a
`SessionAuthService` cannot exist without one. ADR-0005's
"5 attempts per (ip, username) per 15 min" is enforced by:

  - `check()` before any Argon2 work — denied attempts return fast
    with `RateLimitedError(retry_after_seconds)`; an attacker who
    has hit the ceiling burns no server CPU.
  - `record_failure()` after `InvalidCredentialsError` from the body.

A `Retry-After`-bearing 401/429 from `RateLimitedError.retry_after_seconds`
is the HTTP layer's job to assemble.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

import structlog

from app.auth.usernames import normalize_username
from app.crypto import hash_password, verify_password

if TYPE_CHECKING:  # pragma: no cover
    from app.auth.key_material import DerivedKeys
    from app.auth.rate_limit import LoginRateLimiter
    from app.repositories.sessions import HttpSessionDTO, HttpSessionsRepository
    from app.repositories.users import UserDTO, UsersRepository

_logger = structlog.get_logger(__name__)

SESSION_COOKIE_NAME: Final[str] = "__Host-rp_session"
"""HTTP cookie name. The `__Host-` prefix is enforced by browsers:
it requires `Secure`, `Path=/`, and no `Domain` attribute, refusing
any Set-Cookie that violates these. Combined with `HttpOnly` and
`SameSite=Lax`, this gives us the strongest stock cookie posture
without needing a CSRF token (per Backend Architect Q3)."""

SESSION_COOKIE_LIFETIME: Final[timedelta] = timedelta(days=30)
"""30-day cookie. Matches ADR-0005 §"Login flow"."""

SESSION_COOKIE_VALUE_RANDOM_BYTES: Final[int] = 32
"""`secrets.token_urlsafe(32)` produces 43 base64url chars = 256 bits
of entropy. Refers to bytes-of-randomness, not output-string length."""

DEFAULT_CONSTANT_TIME_FLOOR_SECONDS: Final[float] = 0.25
"""Floor on the wall-clock duration of `login()`.

Argon2 verify at the ADR-0005 cost (~100 ms) sets the natural minimum.
A 250 ms floor sits comfortably above that without being annoying
for legitimate users. Tuned for the username-existence side channel
(see module docstring), not as a brute-force defence — the rate
limiter does that."""


@dataclass(frozen=True, slots=True)
class LoginOutcome:
    """Successful login result.

    `cookie_value` is the opaque random string that goes into the
    `Set-Cookie` response header (the HTTP layer wraps it with the
    attributes from `build_cookie_set_kwargs`). `session` and `user`
    are the freshly-persisted DTOs; the user DTO reflects the
    `last_login_at = now` update applied during this call.

    `needs_rehash` is True iff the verifier saw the stored password
    hash was created with parameters weaker than the current ones —
    the Phase 6 HTTP layer should schedule a background `hash_password`
    + `update_password_hash` rehash. We deliberately do NOT rehash
    inline to keep the login path's wall-clock time independent of
    whether a rehash is needed (closes a timing channel from the
    Phase 3 security review).
    """

    cookie_value: str
    session: HttpSessionDTO
    user: UserDTO
    needs_rehash: bool


class LoginError(RuntimeError):
    """Base class for login failures."""


class InvalidCredentialsError(LoginError):
    """Authentication failed for any reason after the constant-time
    floor has elapsed.

    Deliberately collapses all failure modes (no such user, wrong
    password, missing hash) so the API layer can return a single
    `401 invalid_credentials` body — no information leak between
    cases.
    """


class RateLimitedError(LoginError):
    """The login was denied by the rate limiter before any verify ran.

    The HTTP layer maps this to a 429 (or 401 with a `Retry-After:
    <retry_after_seconds>` header per ADR-0005, which dictates 401 +
    Retry-After). The exception carries the wait so the response can
    be assembled without re-querying the limiter.

    Distinguished from `InvalidCredentialsError` so the HTTP layer
    can emit `Retry-After`; the user-visible body should still be
    indistinguishable from a regular failure (it is — both are 401
    with a single error code).
    """

    def __init__(self, *, retry_after_seconds: int) -> None:
        super().__init__("rate_limited")
        self.retry_after_seconds = retry_after_seconds


@functools.cache
def _dummy_password_hash() -> str:
    """Argon2id hash used to absorb verify_password() work when the
    supplied username does not exist or has no password set.

    Cached on first call (not at module import) so importing
    `app.auth.sessions` for type-only use does not pay the ~100 ms
    Argon2 cost. Production warms the cache by calling this once
    during FastAPI lifespan startup; tests warm it on their first
    `login()` call.

    The plaintext source is meaningless — it never authenticates
    anything — but it must produce a hash that will *always* fail to
    verify against a user-supplied password. Using a random 32-byte
    string makes that overwhelmingly likely (collision probability is
    2^-256).
    """
    return hash_password(secrets.token_urlsafe(32))


def generate_session_cookie_value() -> str:
    """Fresh opaque cookie value: 43 chars of base64url random.

    `secrets.token_urlsafe(32)` is the canonical Python entry point for
    URL-safe high-entropy tokens.
    """
    return secrets.token_urlsafe(SESSION_COOKIE_VALUE_RANDOM_BYTES)


def compute_session_token_hash(cookie_value: str, mac_key: bytes) -> bytes:
    """HMAC-SHA256(mac_key, utf-8(cookie_value)) → 32-byte digest.

    The DB stores this fingerprint at `sessions.token_hash` (BLOB
    PRIMARY KEY). Looking up a session means recomputing the
    fingerprint and doing a direct primary-key read — no scan.
    """
    if not isinstance(cookie_value, str):
        raise TypeError("cookie_value must be a string")
    if len(cookie_value) == 0:
        raise ValueError("cookie_value must not be empty")
    if not isinstance(mac_key, bytes):
        raise TypeError("mac_key must be bytes")
    return hmac.new(mac_key, cookie_value.encode("utf-8"), hashlib.sha256).digest()


def build_cookie_set_kwargs(
    cookie_value: str,
    *,
    lifetime: timedelta = SESSION_COOKIE_LIFETIME,
) -> dict[str, object]:
    """Return the kwargs FastAPI/Starlette `response.set_cookie()` wants.

    Centralized here so every Set-Cookie path has the same posture: the
    `__Host-` prefix invariants (`Secure`, `Path=/`, no Domain),
    `HttpOnly`, `SameSite=Lax`. Lifetime is in seconds for the
    `Max-Age` attribute.
    """
    return {
        "key": SESSION_COOKIE_NAME,
        "value": cookie_value,
        "max_age": int(lifetime.total_seconds()),
        "secure": True,
        "httponly": True,
        "samesite": "lax",
        "path": "/",
    }


def build_cookie_delete_kwargs() -> dict[str, object]:
    """Kwargs for `response.delete_cookie()` mirroring `build_cookie_set_kwargs`.

    Same attributes the cookie was set with (otherwise some user
    agents refuse the delete). No `value` / `max_age` because
    delete_cookie emits `Max-Age=0` itself.
    """
    return {
        "key": SESSION_COOKIE_NAME,
        "secure": True,
        "httponly": True,
        "samesite": "lax",
        "path": "/",
    }


class SessionAuthService:
    """Coordinates login, cookie verification, and revocation.

    Takes the `DerivedKeys` *value* rather than the `KeyMaterial`
    *service* — the higher-level FastAPI dependency at
    `app/auth/deps.py` resolves `key_material.require_ready()` once
    per request and feeds the keys to this service. Tests construct a
    `DerivedKeys` with deterministic bytes and skip the unlock path
    entirely.

    The `rate_limiter` is **required**, not optional. ADR-0005's
    brute-force defence is enforced by every `login()` call, so a
    `SessionAuthService` constructed without a limiter cannot run
    against a real user request. Unit tests that don't care about
    rate limiting construct a fresh `LoginRateLimiter()` (cheap —
    in-memory dicts).
    """

    def __init__(
        self,
        *,
        users: UsersRepository,
        sessions: HttpSessionsRepository,
        keys: DerivedKeys,
        rate_limiter: LoginRateLimiter,
        constant_time_floor_seconds: float = DEFAULT_CONSTANT_TIME_FLOOR_SECONDS,
        cookie_lifetime: timedelta = SESSION_COOKIE_LIFETIME,
    ) -> None:
        if constant_time_floor_seconds < 0:
            raise ValueError("constant_time_floor_seconds must be >= 0")
        self._users = users
        self._sessions = sessions
        self._keys = keys
        self._rate_limiter = rate_limiter
        self._floor_s = constant_time_floor_seconds
        self._cookie_lifetime = cookie_lifetime

    async def login(
        self,
        *,
        username: str,
        password: str,
        ip: str,
        user_agent: str | None = None,
    ) -> LoginOutcome:
        """Authenticate username+password → fresh session.

        Order of operations:

        1. Normalize the username (NFKC + casefold + strip) so the
           rate-limit bucket and the DB lookup agree on identity.
        2. Check the rate limiter — if denied, raise
           `RateLimitedError` immediately without spending Argon2.
        3. Always invoke Argon2 verify exactly once (against the
           user's real hash, or against the cached dummy hash if no
           user exists or the user has no password set).
        4. On Argon2 fail: record a rate-limit failure and raise
           `InvalidCredentialsError`.
        5. On Argon2 success: persist a new session row, update
           `users.last_login_at`, return the cookie value + DTOs +
           the `needs_rehash` flag.

        Wall-clock duration of failure paths is padded to
        `constant_time_floor_seconds` with `asyncio.sleep` if the
        body finished earlier. The success path may exceed the floor
        because session-row insert + last_login_at update take
        additional DB time; the security review (Phase 3) accepted
        this as bounded by SQLite WAL latency (~5-10 ms).
        """
        normalized = normalize_username(username)

        # Step 1: rate-limit gate, BEFORE Argon2. A denied attempt
        # spends no server CPU on key derivation.
        decision = self._rate_limiter.check(ip=ip, username=normalized)
        if not decision.allowed:
            _logger.info(
                "login_rate_limited",
                ip=ip,
                normalized_username_prefix=normalized[:3],
                retry_after_seconds=decision.retry_after_seconds,
            )
            raise RateLimitedError(retry_after_seconds=decision.retry_after_seconds)

        start = time.perf_counter()
        try:
            return await self._login_body(
                normalized_username=normalized,
                password=password,
                user_agent=user_agent,
                ip=ip,
            )
        except InvalidCredentialsError:
            # Record the failure under the same bucket the check used.
            # Must happen BEFORE the floor sleep so two near-simultaneous
            # rapid-fire attempts both count.
            self._rate_limiter.record_failure(ip=ip, username=normalized)
            raise
        finally:
            elapsed = time.perf_counter() - start
            remaining = self._floor_s - elapsed
            if remaining > 0:
                await asyncio.sleep(remaining)

    async def _login_body(
        self,
        *,
        normalized_username: str,
        password: str,
        user_agent: str | None,
        ip: str | None,
    ) -> LoginOutcome:
        user = await self._users.get_by_username(normalized_username)

        stored_hash: str | None = None
        if user is not None and user.password_hash is not None:
            stored_hash = user.password_hash.get_secret_value()

        hash_to_verify = stored_hash if stored_hash is not None else _dummy_password_hash()
        verify_result = await asyncio.to_thread(verify_password, hash_to_verify, password)

        if user is None or stored_hash is None or not verify_result.ok:
            raise InvalidCredentialsError("invalid_credentials")

        # Rehash is NOT applied inline (per Phase 3 security review).
        # `needs_rehash` propagates to LoginOutcome; Phase 6 schedules
        # a background rehash via FastAPI's `BackgroundTasks` so the
        # login response's wall-clock time does not depend on whether
        # a rehash is required.
        needs_rehash = verify_result.needs_rehash

        cookie_value = generate_session_cookie_value()
        token_hash = compute_session_token_hash(cookie_value, self._keys.session_token_mac_key)
        now = datetime.now(tz=UTC)
        expires_at = now + self._cookie_lifetime

        session = await self._sessions.create(
            token_hash=token_hash,
            user_id=user.id,
            expires_at=expires_at,
            user_agent=user_agent,
            ip=ip,
        )
        await self._users.touch_last_login(user.id, now)

        # Reflect the just-applied `last_login_at` without a refetch.
        # The frozen UserDTO supports model_copy out of the box.
        touched_user = user.model_copy(update={"last_login_at": now})

        _logger.info(
            "login_succeeded",
            user_id=user.id,
            session_token_fingerprint=token_hash[:4].hex(),
            ip=ip,
            needs_rehash=needs_rehash,
        )
        return LoginOutcome(
            cookie_value=cookie_value,
            session=session,
            user=touched_user,
            needs_rehash=needs_rehash,
        )

    async def verify_cookie(self, cookie_value: str) -> tuple[UserDTO, HttpSessionDTO] | None:
        """Cookie → (user, session) on success, `None` on any failure.

        Touches `last_seen_at` on success. Lazily deletes expired or
        orphaned (no matching user) session rows on failed lookup so
        the DB doesn't accumulate dead state between scheduled
        purges.
        """
        token_hash = compute_session_token_hash(cookie_value, self._keys.session_token_mac_key)
        session = await self._sessions.get_by_token_hash(token_hash)
        if session is None:
            return None
        now = datetime.now(tz=UTC)
        if session.expires_at <= now:
            await self._sessions.delete(token_hash)
            return None
        user = await self._users.get_by_id(session.user_id)
        if user is None:
            await self._sessions.delete(token_hash)
            return None
        await self._sessions.touch_last_seen(token_hash, now)
        return user, session

    async def revoke(self, cookie_value: str) -> None:
        """Delete the session row matching this cookie. Idempotent."""
        token_hash = compute_session_token_hash(cookie_value, self._keys.session_token_mac_key)
        await self._sessions.delete(token_hash)

    async def revoke_all_for_user(self, user_id: str) -> int:
        """Implements ADR-0005's "log out everywhere" button. Returns count."""
        return await self._sessions.delete_all_for_user(user_id)


__all__ = [
    "DEFAULT_CONSTANT_TIME_FLOOR_SECONDS",
    "InvalidCredentialsError",
    "LoginError",
    "LoginOutcome",
    "RateLimitedError",
    "SESSION_COOKIE_LIFETIME",
    "SESSION_COOKIE_NAME",
    "SESSION_COOKIE_VALUE_RANDOM_BYTES",
    "SessionAuthService",
    "build_cookie_delete_kwargs",
    "build_cookie_set_kwargs",
    "compute_session_token_hash",
    "generate_session_cookie_value",
]
