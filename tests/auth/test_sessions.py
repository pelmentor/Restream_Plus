"""Tests for cookie-session login, verify, revoke."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from app.auth.key_material import DerivedKeys
from app.auth.rate_limit import LoginRateLimiter
from app.auth.sessions import (
    DEFAULT_CONSTANT_TIME_FLOOR_SECONDS,
    SESSION_COOKIE_LIFETIME,
    SESSION_COOKIE_NAME,
    InvalidCredentialsError,
    RateLimitedError,
    SessionAuthService,
    build_cookie_delete_kwargs,
    build_cookie_set_kwargs,
    compute_session_token_hash,
    generate_session_cookie_value,
)
from app.crypto import hash_password
from app.repositories.sessions import HttpSessionsRepository
from app.repositories.users import UsersRepository
from sqlalchemy.ext.asyncio import AsyncSession

ADMIN_PASSWORD = "admin-password-test-12345"


@pytest_asyncio.fixture
async def users_repo(session: AsyncSession) -> UsersRepository:
    return UsersRepository(session)


@pytest_asyncio.fixture
async def sessions_repo(session: AsyncSession) -> HttpSessionsRepository:
    return HttpSessionsRepository(session)


@pytest_asyncio.fixture
async def service(
    users_repo: UsersRepository,
    sessions_repo: HttpSessionsRepository,
    derived_keys: DerivedKeys,
    rate_limiter: LoginRateLimiter,
) -> SessionAuthService:
    return SessionAuthService(
        users=users_repo,
        sessions=sessions_repo,
        keys=derived_keys,
        rate_limiter=rate_limiter,
        # Speed up the test suite — the timing-floor logic itself is
        # tested separately below.
        constant_time_floor_seconds=0.0,
    )


class TestCookieValueGeneration:
    def test_generates_url_safe_string(self) -> None:
        value = generate_session_cookie_value()
        # token_urlsafe(32) → 43 chars.
        assert len(value) == 43
        assert all(c.isalnum() or c in "-_" for c in value)

    def test_generates_distinct_values(self) -> None:
        n = 100
        values = {generate_session_cookie_value() for _ in range(n)}
        assert len(values) == n


class TestComputeSessionTokenHash:
    def test_deterministic_for_same_inputs(self) -> None:
        key = b"H" * 32
        cookie = "some-cookie-value"
        h1 = compute_session_token_hash(cookie, key)
        h2 = compute_session_token_hash(cookie, key)
        assert h1 == h2
        assert len(h1) == 32

    def test_different_cookies_yield_different_hashes(self) -> None:
        key = b"H" * 32
        h1 = compute_session_token_hash("cookie-1", key)
        h2 = compute_session_token_hash("cookie-2", key)
        assert h1 != h2

    def test_different_keys_yield_different_hashes(self) -> None:
        cookie = "fixed-cookie"
        h1 = compute_session_token_hash(cookie, b"K" * 32)
        h2 = compute_session_token_hash(cookie, b"L" * 32)
        assert h1 != h2

    def test_empty_cookie_rejected(self) -> None:
        with pytest.raises(ValueError, match="cookie_value must not be empty"):
            compute_session_token_hash("", b"K" * 32)

    def test_non_str_cookie_rejected(self) -> None:
        with pytest.raises(TypeError):
            compute_session_token_hash(b"bytes-not-str", b"K" * 32)  # type: ignore[arg-type]


class TestCookieAttributes:
    def test_secure_mode_uses_host_prefix_invariants(self) -> None:
        kwargs = build_cookie_set_kwargs("some-cookie-value", secure=True)
        assert kwargs["key"] == "__Host-rp_session"
        assert kwargs["value"] == "some-cookie-value"
        assert kwargs["secure"] is True
        assert kwargs["httponly"] is True
        assert kwargs["samesite"] == "lax"
        assert kwargs["path"] == "/"
        # `__Host-` prefix forbids Domain; we never set it.
        assert "domain" not in kwargs

    def test_insecure_mode_drops_host_prefix_and_secure_flag(self) -> None:
        # v1.1.2: when `RESTREAM_COOKIE_SECURE=false` (HTTP-only LAN
        # setups), the cookie name MUST drop `__Host-` and the Secure
        # flag MUST be omitted — otherwise every modern browser silently
        # rejects the cookie and login is broken.
        kwargs = build_cookie_set_kwargs("some-cookie-value", secure=False)
        assert kwargs["key"] == "rp_session"
        assert kwargs["secure"] is False
        # Other defenses still in place.
        assert kwargs["httponly"] is True
        assert kwargs["samesite"] == "lax"
        assert kwargs["path"] == "/"
        assert "domain" not in kwargs

    def test_set_kwargs_max_age_matches_lifetime(self) -> None:
        kwargs = build_cookie_set_kwargs("value", secure=True, lifetime=timedelta(seconds=60))
        assert kwargs["max_age"] == 60

    def test_delete_kwargs_mirrors_attributes_secure(self) -> None:
        kwargs = build_cookie_delete_kwargs(secure=True)
        assert kwargs["key"] == SESSION_COOKIE_NAME
        assert kwargs["secure"] is True
        assert kwargs["httponly"] is True
        assert kwargs["samesite"] == "lax"
        assert kwargs["path"] == "/"
        assert "value" not in kwargs
        assert "max_age" not in kwargs

    def test_delete_kwargs_mirrors_attributes_insecure(self) -> None:
        kwargs = build_cookie_delete_kwargs(secure=False)
        assert kwargs["key"] == "rp_session"
        assert kwargs["secure"] is False


class TestLoginHappyPath:
    @pytest.mark.asyncio
    async def test_login_succeeds_with_correct_password(
        self,
        service: SessionAuthService,
        users_repo: UsersRepository,
    ) -> None:
        outcome = await service.login(
            username="admin",
            password=ADMIN_PASSWORD,
            ip="127.0.0.1",
            user_agent="pytest/1.0",
        )
        assert len(outcome.cookie_value) == 43
        assert outcome.user.username == "admin"
        assert outcome.user.last_login_at is not None
        assert outcome.session.user_agent == "pytest/1.0"
        assert outcome.session.ip == "127.0.0.1"
        # `needs_rehash` defaults False for a freshly seeded password
        # that matches current parameters.
        assert outcome.needs_rehash is False

        # The cookie value HMACs to the persisted token_hash.
        expected_hash = compute_session_token_hash(outcome.cookie_value, b"H" * 32)
        assert outcome.session.token_hash == expected_hash

        # last_login_at was updated in the DB too.
        fetched = await users_repo.get_by_username("admin")
        assert fetched is not None
        assert fetched.last_login_at is not None

    @pytest.mark.asyncio
    async def test_login_normalizes_username(
        self,
        service: SessionAuthService,
    ) -> None:
        """`Admin` (mixed case), `ADMIN` (upper), and `  admin  ` (padded)
        all resolve to the same seeded `admin` row via NFKC + casefold +
        strip."""
        for variant in ("Admin", "ADMIN", "  admin  ", "ＡＤＭＩＮ"):  # noqa: RUF001 — fullwidth ASCII is intentional
            outcome = await service.login(username=variant, password=ADMIN_PASSWORD, ip="127.0.0.1")
            assert outcome.user.username == "admin"

    @pytest.mark.asyncio
    async def test_lookalike_username_does_not_match(self, service: SessionAuthService) -> None:
        # Turkish dotless-i is a legitimately different Unicode letter
        # from Latin i under casefold semantics — the normalization
        # does NOT unify them. Verifying this prevents a future fix
        # from over-broadening normalization and accidentally making
        # the lookalike log in as `admin`.
        with pytest.raises(InvalidCredentialsError):
            await service.login(
                username="admın",  # noqa: RUF001 — dotless-i is the test input
                password=ADMIN_PASSWORD,
                ip="127.0.0.1",
            )

    @pytest.mark.asyncio
    async def test_session_lifetime_matches_default(self, service: SessionAuthService) -> None:
        outcome = await service.login(username="admin", password=ADMIN_PASSWORD, ip="127.0.0.1")
        now = datetime.now(tz=UTC)
        expected = now + SESSION_COOKIE_LIFETIME
        delta = abs((outcome.session.expires_at - expected).total_seconds())
        assert delta < 5


class TestLoginFailures:
    @pytest.mark.asyncio
    async def test_wrong_password_rejected(self, service: SessionAuthService) -> None:
        with pytest.raises(InvalidCredentialsError, match="invalid_credentials"):
            await service.login(username="admin", password="WRONG", ip="127.0.0.1")

    @pytest.mark.asyncio
    async def test_unknown_username_rejected(self, service: SessionAuthService) -> None:
        with pytest.raises(InvalidCredentialsError):
            await service.login(username="not-a-user", password=ADMIN_PASSWORD, ip="127.0.0.1")

    # NOTE: a `test_user_with_no_password_rejected` case lived here pre-
    # v1.0.0 to verify the login path rejected `password_hash IS NULL`.
    # Since SCHEMA_VERSION=2 the NOT NULL constraint on `users.password_hash`
    # makes this state unreachable through any test path (the ORM
    # IntegrityError fires before `login()` is invoked). The defensive
    # `password_hash is None` check in `_login_body` remains as defense-
    # in-depth but has no constructible test input.


class TestRateLimitIntegration:
    @pytest.mark.asyncio
    async def test_rate_limited_login_raises_before_argon2(
        self,
        users_repo: UsersRepository,
        sessions_repo: HttpSessionsRepository,
        derived_keys: DerivedKeys,
    ) -> None:
        """After hitting the failure threshold, further attempts return
        immediately with `RateLimitedError` (no Argon2 burned).
        """
        rl = LoginRateLimiter(failure_limit=2, window_seconds=60)
        svc = SessionAuthService(
            users=users_repo,
            sessions=sessions_repo,
            keys=derived_keys,
            rate_limiter=rl,
            constant_time_floor_seconds=0.0,
        )
        # Two wrong-password attempts to fill the bucket.
        for _ in range(2):
            with pytest.raises(InvalidCredentialsError):
                await svc.login(username="admin", password="WRONG", ip="9.9.9.9")
        # Third attempt — even with the CORRECT password — is denied.
        with pytest.raises(RateLimitedError) as excinfo:
            await svc.login(username="admin", password=ADMIN_PASSWORD, ip="9.9.9.9")
        assert excinfo.value.retry_after_seconds >= 1

    @pytest.mark.asyncio
    async def test_rate_limit_uses_normalized_username_bucket(
        self,
        users_repo: UsersRepository,
        sessions_repo: HttpSessionsRepository,
        derived_keys: DerivedKeys,
    ) -> None:
        """Case-variation usernames share a bucket."""
        rl = LoginRateLimiter(failure_limit=2, window_seconds=60)
        svc = SessionAuthService(
            users=users_repo,
            sessions=sessions_repo,
            keys=derived_keys,
            rate_limiter=rl,
            constant_time_floor_seconds=0.0,
        )
        # First failure under `ADMIN`, second under `Admin` — both
        # bucket as `admin`.
        with pytest.raises(InvalidCredentialsError):
            await svc.login(username="ADMIN", password="WRONG", ip="1.1.1.1")
        with pytest.raises(InvalidCredentialsError):
            await svc.login(username="Admin", password="WRONG", ip="2.2.2.2")
        # Third attempt from a fresh IP, lowercase form — username
        # bucket is full, so denied.
        with pytest.raises(RateLimitedError):
            await svc.login(username="admin", password="WRONG", ip="3.3.3.3")


class TestConstantTimeFloor:
    @pytest.mark.asyncio
    async def test_unknown_user_padded_to_floor(
        self,
        users_repo: UsersRepository,
        sessions_repo: HttpSessionsRepository,
        derived_keys: DerivedKeys,
        rate_limiter: LoginRateLimiter,
    ) -> None:
        floor = 0.20
        svc = SessionAuthService(
            users=users_repo,
            sessions=sessions_repo,
            keys=derived_keys,
            rate_limiter=rate_limiter,
            constant_time_floor_seconds=floor,
        )
        start = asyncio.get_event_loop().time()
        with pytest.raises(InvalidCredentialsError):
            await svc.login(username="ghost", password="any-12345678", ip="127.0.0.1")
        elapsed = asyncio.get_event_loop().time() - start
        assert elapsed >= floor - 0.02
        assert elapsed < floor + 1.0

    @pytest.mark.asyncio
    async def test_floor_zero_returns_promptly(
        self,
        users_repo: UsersRepository,
        sessions_repo: HttpSessionsRepository,
        derived_keys: DerivedKeys,
        rate_limiter: LoginRateLimiter,
    ) -> None:
        svc = SessionAuthService(
            users=users_repo,
            sessions=sessions_repo,
            keys=derived_keys,
            rate_limiter=rate_limiter,
            constant_time_floor_seconds=0.0,
        )
        start = asyncio.get_event_loop().time()
        with pytest.raises(InvalidCredentialsError):
            await svc.login(username="ghost", password="any-12345678", ip="127.0.0.1")
        elapsed = asyncio.get_event_loop().time() - start
        assert elapsed < 1.0

    def test_default_floor_constant_above_argon2(self) -> None:
        assert DEFAULT_CONSTANT_TIME_FLOOR_SECONDS >= 0.2


class TestCookieVerification:
    @pytest.mark.asyncio
    async def test_valid_cookie_returns_user_and_touches_last_seen(
        self, service: SessionAuthService
    ) -> None:
        outcome = await service.login(username="admin", password=ADMIN_PASSWORD, ip="127.0.0.1")
        await asyncio.sleep(0.01)
        result = await service.verify_cookie(outcome.cookie_value)
        assert result is not None
        user, sess = result
        assert user.username == "admin"
        assert sess.token_hash == outcome.session.token_hash
        assert sess.last_seen_at.replace(microsecond=0) >= outcome.session.created_at.replace(
            microsecond=0
        )

    @pytest.mark.asyncio
    async def test_unknown_cookie_returns_none(self, service: SessionAuthService) -> None:
        assert await service.verify_cookie("not-a-real-cookie") is None

    @pytest.mark.asyncio
    async def test_expired_cookie_returns_none_and_deletes(
        self,
        users_repo: UsersRepository,
        sessions_repo: HttpSessionsRepository,
        derived_keys: DerivedKeys,
        rate_limiter: LoginRateLimiter,
        session: AsyncSession,
    ) -> None:
        svc = SessionAuthService(
            users=users_repo,
            sessions=sessions_repo,
            keys=derived_keys,
            rate_limiter=rate_limiter,
            constant_time_floor_seconds=0.0,
            cookie_lifetime=timedelta(seconds=-1),
        )
        outcome = await svc.login(username="admin", password=ADMIN_PASSWORD, ip="127.0.0.1")
        await session.commit()
        result = await svc.verify_cookie(outcome.cookie_value)
        assert result is None
        # Row was deleted lazily on the expired lookup.
        assert await sessions_repo.get_by_token_hash(outcome.session.token_hash) is None


class TestRevocation:
    @pytest.mark.asyncio
    async def test_revoke_makes_cookie_invalid(self, service: SessionAuthService) -> None:
        outcome = await service.login(username="admin", password=ADMIN_PASSWORD, ip="127.0.0.1")
        await service.revoke(outcome.cookie_value)
        assert await service.verify_cookie(outcome.cookie_value) is None

    @pytest.mark.asyncio
    async def test_revoke_unknown_cookie_is_noop(self, service: SessionAuthService) -> None:
        await service.revoke("not-real")

    @pytest.mark.asyncio
    async def test_revoke_all_for_user_clears_every_session(
        self,
        service: SessionAuthService,
        sessions_repo: HttpSessionsRepository,
    ) -> None:
        first = await service.login(username="admin", password=ADMIN_PASSWORD, ip="127.0.0.1")
        second = await service.login(username="admin", password=ADMIN_PASSWORD, ip="127.0.0.1")
        assert first.cookie_value != second.cookie_value

        count = await service.revoke_all_for_user(first.user.id)
        assert count == 2
        assert await service.verify_cookie(first.cookie_value) is None
        assert await service.verify_cookie(second.cookie_value) is None


class TestNeedsRehashFlag:
    @pytest.mark.asyncio
    async def test_needs_rehash_surfaces_on_outcome(
        self,
        users_repo: UsersRepository,
        sessions_repo: HttpSessionsRepository,
        derived_keys: DerivedKeys,
        rate_limiter: LoginRateLimiter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When verify_password says the hash needs rehashing, the
        flag propagates to LoginOutcome — but no inline rehash happens
        (so login wall-clock time is independent of the flag value).
        """
        from app.crypto.passwords import PasswordVerificationResult, verify_password

        called_hash_password = False

        def fake_verify(stored: str, password: str) -> PasswordVerificationResult:
            real = verify_password(stored, password)
            return PasswordVerificationResult(ok=real.ok, needs_rehash=real.ok)

        def fake_hash(p: str) -> str:
            nonlocal called_hash_password
            called_hash_password = True
            return hash_password(p)

        monkeypatch.setattr("app.auth.sessions.verify_password", fake_verify)
        monkeypatch.setattr("app.auth.sessions.hash_password", fake_hash)

        svc = SessionAuthService(
            users=users_repo,
            sessions=sessions_repo,
            keys=derived_keys,
            rate_limiter=rate_limiter,
            constant_time_floor_seconds=0.0,
        )
        outcome = await svc.login(username="admin", password=ADMIN_PASSWORD, ip="127.0.0.1")
        assert outcome.needs_rehash is True
        # `hash_password` was NOT called inline — Phase 6 will schedule
        # the rehash via BackgroundTasks (sec review #4).
        assert called_hash_password is False
