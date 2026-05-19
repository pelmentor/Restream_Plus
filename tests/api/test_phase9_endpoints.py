"""Phase 9 endpoint tests — reveal-credential, reveal-ingest-key,
rotate-passphrase, sessions list/revoke, /api/about.

Reveal endpoints reuse the existing `ready_key_material` fixture (the
KEK is fixed `b"S"*32`, salt fixed). Rotate-passphrase needs a REAL
unlocked KeyMaterial so `verify_passphrase` exercises the actual
Argon2id path — that's the only test class that pays the ~700 ms
derivation cost; the others stay sub-second.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from app.api import LockedModeMiddleware, install_exception_handlers
from app.api.api_tokens_api import router as api_tokens_router
from app.api.auth import auth_router, unlock_router
from app.api.health import router as health_router
from app.api.internal_rtmp import router as internal_rtmp_router
from app.api.run import router as run_router
from app.api.security_api import about_router, security_router
from app.api.sessions_history import router as sessions_history_router
from app.api.settings_api import router as settings_router
from app.api.targets import router as targets_router
from app.api.ws import WsRegistry
from app.api.ws import router as ws_router
from app.auth.deps import AuthState
from app.auth.key_material import KeyMaterial, load_or_create_kdf_salt
from app.auth.last_seen_coalescer import LastSeenCoalescer
from app.auth.rate_limit import LoginRateLimiter
from app.auth.reprompts import RepromptStore
from app.config import AppSettings
from app.crypto.aead import build_credential_aad, encrypt
from app.crypto.passwords import hash_password
from app.db import create_engine, initialize_schema, make_sessionmaker
from app.fanout._testing import FakeSupervisor
from app.fanout.event_bus import EventBus
from app.repositories.audit_log import AuditLogRepository
from app.repositories.credentials import CredentialsRepository
from app.repositories.targets import TargetsRepository
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.api.conftest import SESSION_COOKIE_NAME

from .conftest import ADMIN_PASSWORD

# ======================================================================
# Helpers
# ======================================================================


async def _issue(client: httpx.AsyncClient, scope: str) -> str:
    r = await client.post(
        "/api/auth/reprompt",
        json={"password": ADMIN_PASSWORD, "scope": scope},
    )
    assert r.status_code == 200, r.text
    return str(r.json()["grant_id"])


async def _create_target_with_credential(
    sessionmaker_: async_sessionmaker[AsyncSession],
    *,
    plaintext_key: str,
    stream_key_kek: bytes,
    target_type: str = "twitch",
    label: str = "test target",
) -> tuple[str, str]:
    """Persist a target + its AEAD-wrapped credential. Returns (target_id, last4)."""
    async with sessionmaker_() as s, s.begin():
        targets = TargetsRepository(s)
        target_dto = await targets.create(
            type=target_type,
            label=label,
            url="rtmps://test.example/app",
            enabled=True,
            settings={},
        )
        creds = CredentialsRepository(s)
        salt = secrets.token_bytes(16)
        aad = build_credential_aad(salt)
        nonce, ct = encrypt(stream_key_kek, plaintext_key.encode("utf-8"), aad)
        last4 = plaintext_key[-4:]
        await creds.upsert(
            target_id=target_dto.id,
            lifetime="persistent",
            ciphertext=ct,
            nonce=nonce,
            salt=salt,
            last4=last4,
        )
    return target_dto.id, last4


# ======================================================================
# POST /api/targets/{id}/reveal-credential
# ======================================================================


@pytest.mark.asyncio
async def test_reveal_credential_happy_path(
    auth_client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    target_id, last4 = await _create_target_with_credential(
        api_sessionmaker,
        plaintext_key="live_test_1234567890_PLAIN_ABCD",
        stream_key_kek=b"S" * 32,
    )
    grant = await _issue(auth_client, "reveal_stream_key")
    r = await auth_client.post(
        f"/api/targets/{target_id}/reveal-credential",
        headers={"X-Reprompt-Grant": grant},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plaintext"] == "live_test_1234567890_PLAIN_ABCD"
    assert body["last4"] == last4


@pytest.mark.asyncio
async def test_reveal_credential_missing_grant_403(
    auth_client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    target_id, _ = await _create_target_with_credential(
        api_sessionmaker,
        plaintext_key="anything-1234",
        stream_key_kek=b"S" * 32,
    )
    r = await auth_client.post(f"/api/targets/{target_id}/reveal-credential")
    assert r.status_code == 403
    assert r.json()["detail"] == "reprompt_required"


@pytest.mark.asyncio
async def test_reveal_credential_wrong_scope_403(
    auth_client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    target_id, _ = await _create_target_with_credential(
        api_sessionmaker,
        plaintext_key="anything-1234",
        stream_key_kek=b"S" * 32,
    )
    # Use a delete_target scope on a reveal endpoint.
    grant = await _issue(auth_client, "delete_target")
    r = await auth_client.post(
        f"/api/targets/{target_id}/reveal-credential",
        headers={"X-Reprompt-Grant": grant},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "reprompt_invalid_or_expired"


@pytest.mark.asyncio
async def test_reveal_credential_target_missing_404(
    auth_client: httpx.AsyncClient,
) -> None:
    grant = await _issue(auth_client, "reveal_stream_key")
    r = await auth_client.post(
        "/api/targets/nonexistent/reveal-credential",
        headers={"X-Reprompt-Grant": grant},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "target_not_found"


@pytest.mark.asyncio
async def test_reveal_credential_credential_absent_404(
    auth_client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # Create a target WITHOUT a credential row (e.g. VK between sessions).
    async with api_sessionmaker() as s, s.begin():
        targets = TargetsRepository(s)
        dto = await targets.create(
            type="vk_live",
            label="vk-target",
            url="rtmp://ovsu.okcdn.ru/input/",
            enabled=True,
            settings={},
        )
    grant = await _issue(auth_client, "reveal_stream_key")
    r = await auth_client.post(
        f"/api/targets/{dto.id}/reveal-credential",
        headers={"X-Reprompt-Grant": grant},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "credential_not_found"


@pytest.mark.asyncio
async def test_reveal_credential_audit_carries_last4_only(
    auth_client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    target_id, last4 = await _create_target_with_credential(
        api_sessionmaker,
        plaintext_key="audit-test-ZZZZ",
        stream_key_kek=b"S" * 32,
    )
    grant = await _issue(auth_client, "reveal_stream_key")
    await auth_client.post(
        f"/api/targets/{target_id}/reveal-credential",
        headers={"X-Reprompt-Grant": grant},
    )
    # Inspect audit log.
    async with api_sessionmaker() as s:
        rows = await AuditLogRepository(api_sessionmaker).list_recent(s, limit=10)
    reveal_rows = [r for r in rows if r.event_type == "credential_revealed"]
    assert len(reveal_rows) == 1
    audit_row = reveal_rows[0]
    assert audit_row.data.get("last4") == last4
    # The plaintext must NEVER appear in any audit field.
    serialized = repr(audit_row.data)
    assert "audit-test-ZZZZ" not in serialized


# ======================================================================
# POST /api/settings/reveal-ingest-key
# ======================================================================


@pytest.mark.asyncio
async def test_reveal_ingest_key_happy_path(
    auth_client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # Read the bootstrap-generated ingest key so the test doesn't pin
    # to a magic string.
    from app.repositories.settings_repo import SettingsRepository

    async with api_sessionmaker() as s:
        settings_dto = await SettingsRepository(s).get()
    expected = settings_dto.ingest_key_current

    grant = await _issue(auth_client, "reveal_ingest_key")
    r = await auth_client.post(
        "/api/settings/reveal-ingest-key",
        headers={"X-Reprompt-Grant": grant},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plaintext"] == expected
    assert body["last4"] == expected[-4:]


@pytest.mark.asyncio
async def test_reveal_ingest_key_wrong_scope_403(
    auth_client: httpx.AsyncClient,
) -> None:
    grant = await _issue(auth_client, "regenerate_ingest_key")  # mismatched
    r = await auth_client.post(
        "/api/settings/reveal-ingest-key",
        headers={"X-Reprompt-Grant": grant},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "reprompt_invalid_or_expired"


@pytest.mark.asyncio
async def test_reveal_ingest_key_no_grant_403(
    auth_client: httpx.AsyncClient,
) -> None:
    r = await auth_client.post("/api/settings/reveal-ingest-key")
    assert r.status_code == 403
    assert r.json()["detail"] == "reprompt_required"


# ======================================================================
# GET /api/security/sessions + DELETE /api/security/sessions/{fp}
# ======================================================================


@pytest.mark.asyncio
async def test_list_http_sessions_current_session_marked(
    auth_client: httpx.AsyncClient,
) -> None:
    r = await auth_client.get("/api/security/sessions")
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) >= 1
    current = [row for row in rows if row["is_current"]]
    assert len(current) == 1
    assert len(current[0]["id_fingerprint"]) == 16


@pytest.mark.asyncio
async def test_revoke_http_session_404_unknown_fingerprint(
    auth_client: httpx.AsyncClient,
) -> None:
    r = await auth_client.delete("/api/security/sessions/" + "0" * 16)
    assert r.status_code == 404
    assert r.json()["detail"] == "session_not_found"


@pytest.mark.asyncio
async def test_revoke_own_session_clears_cookie(
    auth_client: httpx.AsyncClient,
) -> None:
    # Find own fingerprint via the list endpoint.
    sessions = (await auth_client.get("/api/security/sessions")).json()
    current = next(s for s in sessions if s["is_current"])
    r = await auth_client.delete(f"/api/security/sessions/{current['id_fingerprint']}")
    assert r.status_code == 204
    # The response carries a Set-Cookie that deletes the session cookie.
    set_cookie = r.headers.get("set-cookie", "")
    assert SESSION_COOKIE_NAME in set_cookie


@pytest.mark.asyncio
async def test_revoke_other_session_does_not_clear_current_cookie(
    auth_client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[AsyncSession],
    admin_user_id_fixture: str,
) -> None:
    # Create a SECOND session row by hand (simulating another device).
    from datetime import timedelta

    from app.repositories.sessions import HttpSessionsRepository

    async with api_sessionmaker() as s, s.begin():
        repo = HttpSessionsRepository(s)
        # `token_hash` is opaque to revoke — we just need a row.
        other = await repo.create(
            token_hash=b"\xab" * 32,
            user_id=admin_user_id_fixture,
            expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
            user_agent="other-device",
            ip="10.0.0.1",
        )
    other_fp = hashlib.sha256(other.token_hash).hexdigest()[:16]

    r = await auth_client.delete(f"/api/security/sessions/{other_fp}")
    assert r.status_code == 204
    # No Set-Cookie deletion since current session is not the revoked one.
    set_cookie = r.headers.get("set-cookie", "")
    assert SESSION_COOKIE_NAME not in set_cookie


# ======================================================================
# GET /api/about
# ======================================================================


@pytest.mark.asyncio
async def test_about_returns_version_and_uptime(
    auth_client: httpx.AsyncClient,
) -> None:
    r = await auth_client.get("/api/about")
    assert r.status_code == 200, r.text
    body = r.json()
    # Track the project's canonical version string rather than a hardcoded
    # literal — keeps this test in sync with `pyproject.toml` bumps.
    from app.version import __version__ as _expected_version

    assert body["version"] == _expected_version
    assert isinstance(body["build_sha"], str)
    assert isinstance(body["uptime_seconds"], int | float)
    assert body["uptime_seconds"] >= 0
    assert isinstance(body["last_reboots"], list)


@pytest.mark.asyncio
async def test_about_last_reboots_reflects_app_started_events(
    auth_client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # The test conftest does NOT run the lifespan, so no app_started
    # events exist by default. Insert two synthetic ones and check the
    # endpoint picks them up newest-first.
    repo = AuditLogRepository(api_sessionmaker)
    older = datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC)
    newer = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)
    # Hex Audit BA-F4 (slice 8): direct seeding uses `append_standalone`.
    await repo.append_standalone(event_type="app_started", actor="system", at=older)
    await repo.append_standalone(event_type="app_started", actor="system", at=newer)
    r = await auth_client.get("/api/about")
    assert r.status_code == 200
    reboots = r.json()["last_reboots"]
    assert len(reboots) >= 2
    # Newest first.
    assert reboots[0].startswith("2026-05-15")
    assert reboots[1].startswith("2026-05-14")


# ======================================================================
# POST /api/security/rotate-passphrase — REAL Argon2 path
# ======================================================================


@pytest_asyncio.fixture
async def real_unlocked_app(
    tmp_path: Path,
    fake_supervisor: FakeSupervisor,
    event_bus: EventBus,
) -> AsyncIterator[FastAPI]:
    """A FastAPI app with a REALLY-unlocked KeyMaterial.

    rotate-passphrase tests need verify_passphrase to exercise the
    actual Argon2id path — the conftest's `ready_key_material` short-
    circuits to fixed test keys, which makes verify always fail.

    The trade-off: each test in this fixture pays ~2 Argon2id passes
    (~1.5 s). Reused across the rotate-passphrase test class to keep
    the suite under control.
    """
    s = AppSettings(  # type: ignore[arg-type]
        passphrase_source="paste",
        data_dir=tmp_path,
        admin_password=ADMIN_PASSWORD,
        # Phase 10 §Q1: production default flipped to True; this fixture
        # doesn't run nginx, so opt out (matches conftest's _build_settings).
        healthz_check_nginx=False,
    )
    engine = create_engine(s)
    await initialize_schema(engine, s)
    sm = make_sessionmaker(engine)

    # Seed the admin password.
    from app.repositories.users import UsersRepository

    async with sm() as session, session.begin():
        users = UsersRepository(session)
        admin = await users.get_by_username("admin")
        assert admin is not None
        await users.update_password_hash(admin.id, hash_password(ADMIN_PASSWORD))

    kdf_salt = load_or_create_kdf_salt(s.kdf_salt_path)
    km = KeyMaterial(kdf_salt=kdf_salt)
    initial_passphrase = "initial-master-passphrase-32-chars"
    await km.unlock(initial_passphrase)

    reprompts = RepromptStore()
    state = AuthState(
        key_material=km,
        sessionmaker=sm,
        rate_limiter=LoginRateLimiter(),
        unlock_rate_limiter=LoginRateLimiter(failure_limit=3, window_seconds=900.0),
        reprompt_rate_limiter=LoginRateLimiter(failure_limit=3, window_seconds=900.0),
        reprompts=reprompts,
        last_seen_coalescer=LastSeenCoalescer(),
        trusted_proxies=(),
    )
    app = FastAPI()
    app.state.settings = s
    app.state.auth = state
    app.state.supervisor = fake_supervisor
    app.state.event_bus = event_bus
    app.state.ws_registry = WsRegistry()
    app.state.supervisor_tasks = set()
    app.state.started_at = datetime(2026, 5, 16, 0, 0, 0, tzinfo=UTC)
    app.state.initial_passphrase = initial_passphrase  # type: ignore[attr-defined]
    app.state.sessionmaker_ = sm  # type: ignore[attr-defined]
    install_exception_handlers(app)
    app.add_middleware(LockedModeMiddleware, key_material=km)
    app.include_router(auth_router)
    app.include_router(unlock_router)
    app.include_router(targets_router)
    app.include_router(run_router)
    app.include_router(settings_router)
    app.include_router(api_tokens_router)
    app.include_router(sessions_history_router)
    app.include_router(internal_rtmp_router)
    app.include_router(security_router)
    app.include_router(about_router)
    app.include_router(ws_router)
    app.include_router(health_router)
    try:
        yield app
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def real_unlocked_client(
    real_unlocked_app: FastAPI,
) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=real_unlocked_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as c:
        # Login.
        r = await c.post(
            "/api/auth/login",
            json={"username": "admin", "password": ADMIN_PASSWORD},
        )
        assert r.status_code == 200
        if SESSION_COOKIE_NAME not in c.cookies:
            from http.cookies import SimpleCookie

            sc = SimpleCookie()
            for header in r.headers.get_list("set-cookie"):
                sc.load(header)
            if SESSION_COOKIE_NAME in sc:
                c.cookies.set(SESSION_COOKIE_NAME, sc[SESSION_COOKIE_NAME].value)
        yield c


@pytest.mark.asyncio
async def test_rotate_passphrase_happy_path_rewraps_credentials(
    real_unlocked_app: FastAPI,
    real_unlocked_client: httpx.AsyncClient,
) -> None:
    sm: async_sessionmaker[AsyncSession] = real_unlocked_app.state.sessionmaker_
    initial: str = real_unlocked_app.state.initial_passphrase
    km: KeyMaterial = real_unlocked_app.state.auth.key_material

    # Seed two credentials so the loop has work.
    plaintexts = ["live_AAAAAAAAAAAA_1234", "live_BBBBBBBBBBBB_5678"]
    target_ids: list[str] = []
    for i, pt in enumerate(plaintexts):
        t_id, _ = await _create_target_with_credential(
            sm,
            plaintext_key=pt,
            stream_key_kek=km.require_ready().stream_key_kek,
            label=f"target-{i}",
        )
        target_ids.append(t_id)

    old_salt = real_unlocked_app.state.settings.kdf_salt_path.read_bytes()

    grant = await _issue(real_unlocked_client, "rotate_passphrase")
    new_passphrase = "rotated-master-passphrase-32-chars"
    r = await real_unlocked_client.post(
        "/api/security/rotate-passphrase",
        json={"old_passphrase": initial, "new_passphrase": new_passphrase},
        headers={"X-Reprompt-Grant": grant},
    )
    assert r.status_code == 204, r.text

    # Salt file changed.
    new_salt = real_unlocked_app.state.settings.kdf_salt_path.read_bytes()
    assert new_salt != old_salt
    # .previous file exists with the old salt.
    prev_path = real_unlocked_app.state.settings.kdf_salt_previous_path
    assert prev_path.exists()
    assert prev_path.read_bytes() == old_salt

    # In-process KeyMaterial holds new keys: verify_passphrase succeeds
    # for the new value and fails for the old.
    assert await km.verify_passphrase(new_passphrase) is True
    assert await km.verify_passphrase(initial) is False

    # Sessions table is empty for the admin user (reviewer M-2 follow-up:
    # the previous assertion used `session_token_mac_key.hex()` as the
    # `user_id` and trivially returned no rows. Look up the admin's
    # real ID and assert).
    from app.repositories.sessions import HttpSessionsRepository
    from app.repositories.users import UsersRepository

    async with sm() as s:
        admin = await UsersRepository(s).get_by_username("admin")
        assert admin is not None
        active = await HttpSessionsRepository(s).list_active_for_user(
            admin.id, datetime.now(tz=UTC)
        )
    assert len(active) == 0

    # Credentials re-wrapped: decrypt with NEW key works for both.
    # Slice 7 / BA-F5: rotate-passphrase now writes v2 AAD on rewrap, so
    # the verification path uses `decrypt_credential` which transparently
    # handles v1 or v2.
    from app.crypto.aead import decrypt_credential

    async with sm() as s:
        for tid, original_pt in zip(target_ids, plaintexts, strict=True):
            cred = await CredentialsRepository(s).get_for_target(tid)
            assert cred is not None
            recovered = decrypt_credential(
                km.require_ready().stream_key_kek,
                cred.nonce,
                cred.ciphertext,
                cred.salt,
                tid,
            ).decode("utf-8")
            assert recovered == original_pt


@pytest.mark.asyncio
async def test_rotate_passphrase_emits_two_phase_audit(
    real_unlocked_app: FastAPI,
    real_unlocked_client: httpx.AsyncClient,
) -> None:
    """Slice 8.5 (Hex Audit BA2-F2 + FG3-F3 STRONG): the rotate-
    passphrase happy path now emits TWO audit rows:

    1. `passphrase_rotation_started` INSIDE the rewrap txn (atomic
       with the credential rewrap + delete-all sessions/tokens).
    2. `passphrase_rotated` via `append_standalone` AFTER the
       salt-file rename + in-process key swap both succeed.

    On the orphan-rename path (salt-file rename fails), an additional
    `passphrase_rotation_orphaned` row records the divergence between
    DB state and on-disk state. The two-phase shape prevents the
    forensic-record-lies failure mode flagged in re-audit
    Checkpoint #2.
    """
    sm: async_sessionmaker[AsyncSession] = real_unlocked_app.state.sessionmaker_
    initial: str = real_unlocked_app.state.initial_passphrase

    grant = await _issue(real_unlocked_client, "rotate_passphrase")
    new_passphrase = "rotated-master-passphrase-32-chars"
    r = await real_unlocked_client.post(
        "/api/security/rotate-passphrase",
        json={"old_passphrase": initial, "new_passphrase": new_passphrase},
        headers={"X-Reprompt-Grant": grant},
    )
    assert r.status_code == 204, r.text

    async with sm() as s:
        rows = await AuditLogRepository(sm).list_recent(s, limit=20)

    started = [r for r in rows if r.event_type == "passphrase_rotation_started"]
    rotated = [r for r in rows if r.event_type == "passphrase_rotated"]
    orphaned = [r for r in rows if r.event_type == "passphrase_rotation_orphaned"]

    assert len(started) == 1, "passphrase_rotation_started must land in-txn"
    assert len(rotated) == 1, "passphrase_rotated must land post-rename"
    assert len(orphaned) == 0, "no orphan on happy path"
    # Both audit rows carry the rewrap/revoke counters.
    for row in (started[0], rotated[0]):
        assert "credentials_rewrapped_count" in row.data
        assert "sessions_revoked_count" in row.data
        assert "api_tokens_revoked_count" in row.data


@pytest.mark.asyncio
async def test_rotate_passphrase_orphan_rename_emits_audit_and_raises(
    real_unlocked_app: FastAPI,
    real_unlocked_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slice 8.5 reviewer-pass note: the forensically critical
    orphan-rename branch (the post-commit `Path.replace` raises
    `OSError`) was previously untested. This pins the load-bearing
    invariant: when the rename fails, the audit log carries both
    `passphrase_rotation_started` (in-txn commit succeeded) AND
    `passphrase_rotation_orphaned` (post-commit failure noted) — but
    NOT `passphrase_rotated`. The 500 returned to the client signals
    rotation failure; the audit row matches reality (DB credentials
    are new but on-disk salt is old).
    """
    sm: async_sessionmaker[AsyncSession] = real_unlocked_app.state.sessionmaker_
    initial: str = real_unlocked_app.state.initial_passphrase

    # Force the salt-file rename to fail. The rename is `new_path.replace(salt_path)`
    # at security_api.py — monkeypatching `pathlib.Path.replace` is the cleanest
    # way to simulate the OS-level failure.
    from pathlib import Path

    original_replace = Path.replace

    def _boom_replace(self: Path, target: object) -> object:  # noqa: ARG001
        if self.suffix == ".new":
            raise OSError("simulated rename failure")
        return original_replace(self, target)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "replace", _boom_replace)

    grant = await _issue(real_unlocked_client, "rotate_passphrase")
    new_passphrase = "orphan-test-passphrase-32-chars"
    r = await real_unlocked_client.post(
        "/api/security/rotate-passphrase",
        json={"old_passphrase": initial, "new_passphrase": new_passphrase},
        headers={"X-Reprompt-Grant": grant},
    )
    # The orphan path re-raises OSError -> generic 500.
    assert r.status_code == 500

    async with sm() as s:
        rows = await AuditLogRepository(sm).list_recent(s, limit=20)

    started = [r for r in rows if r.event_type == "passphrase_rotation_started"]
    rotated = [r for r in rows if r.event_type == "passphrase_rotated"]
    orphaned = [r for r in rows if r.event_type == "passphrase_rotation_orphaned"]

    # Audit reality:
    # - `started` row landed atomically with the rewrap+delete-all
    #   inside the session.begin block (txn committed before rename).
    # - `orphaned` row landed post-commit via append_standalone.
    # - `rotated` row did NOT land (would imply the full rotation
    #   succeeded — it didn't).
    assert len(started) == 1, "rotation_started must land in-txn even on orphan"
    assert len(orphaned) == 1, "orphaned must land on rename failure"
    assert len(rotated) == 0, "rotated must NOT land when rename fails"
    assert orphaned[0].data.get("old_salt_hex") is not None
    assert orphaned[0].data.get("new_salt_hex") is not None


@pytest.mark.asyncio
async def test_rotate_passphrase_wrong_old_returns_401(
    real_unlocked_client: httpx.AsyncClient,
) -> None:
    grant = await _issue(real_unlocked_client, "rotate_passphrase")
    r = await real_unlocked_client.post(
        "/api/security/rotate-passphrase",
        json={
            "old_passphrase": "wrong-passphrase-1234",
            "new_passphrase": "another-new-passphrase-zzz",
        },
        headers={"X-Reprompt-Grant": grant},
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid_credentials"


@pytest.mark.asyncio
async def test_rotate_passphrase_same_returns_422(
    real_unlocked_app: FastAPI,
    real_unlocked_client: httpx.AsyncClient,
) -> None:
    initial: str = real_unlocked_app.state.initial_passphrase
    grant = await _issue(real_unlocked_client, "rotate_passphrase")
    r = await real_unlocked_client.post(
        "/api/security/rotate-passphrase",
        json={"old_passphrase": initial, "new_passphrase": initial},
        headers={"X-Reprompt-Grant": grant},
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "same_passphrase"


@pytest.mark.asyncio
async def test_rotate_passphrase_run_active_409(
    real_unlocked_app: FastAPI,
    real_unlocked_client: httpx.AsyncClient,
) -> None:
    initial: str = real_unlocked_app.state.initial_passphrase
    # Force the FakeSupervisor to a non-OFFLINE state. FakeSupervisor
    # exposes `_run_state` as a plain attribute; we override the
    # `run_state()` method via a small monkeypatch on the instance.
    from app.domain.run_state import RunState

    fake = real_unlocked_app.state.supervisor
    fake.run_state = lambda: RunState.ARMED  # type: ignore[assignment, method-assign]
    grant = await _issue(real_unlocked_client, "rotate_passphrase")
    r = await real_unlocked_client.post(
        "/api/security/rotate-passphrase",
        json={"old_passphrase": initial, "new_passphrase": "new-pass-32-chars"},
        headers={"X-Reprompt-Grant": grant},
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "run_active"


@pytest.mark.asyncio
async def test_rotate_passphrase_atomicity_on_rewrap_failure(
    monkeypatch: pytest.MonkeyPatch,
    real_unlocked_app: FastAPI,
    real_unlocked_client: httpx.AsyncClient,
) -> None:
    """Inject a rewrap failure after the first row — the DB transaction
    must roll back the first row's update and leave the .kdf_salt file
    untouched. The in-memory KeyMaterial must still hold the OLD keys.
    """
    sm: async_sessionmaker[AsyncSession] = real_unlocked_app.state.sessionmaker_
    initial: str = real_unlocked_app.state.initial_passphrase
    km: KeyMaterial = real_unlocked_app.state.auth.key_material

    # Seed three credentials.
    target_ids: list[str] = []
    plaintexts = ["pt-AAAA-1111", "pt-BBBB-2222", "pt-CCCC-3333"]
    for i, pt in enumerate(plaintexts):
        t_id, _ = await _create_target_with_credential(
            sm,
            plaintext_key=pt,
            stream_key_kek=km.require_ready().stream_key_kek,
            label=f"atom-target-{i}",
        )
        target_ids.append(t_id)

    old_salt_bytes = real_unlocked_app.state.settings.kdf_salt_path.read_bytes()

    # Patch CredentialsRepository.rewrap to fail on the 2nd call.
    call_count = {"n": 0}
    original = CredentialsRepository.rewrap

    async def failing_rewrap(self: Any, **kw: Any) -> Any:
        call_count["n"] += 1
        if call_count["n"] >= 2:
            raise RuntimeError("injected failure")
        return await original(self, **kw)

    monkeypatch.setattr(CredentialsRepository, "rewrap", failing_rewrap)

    grant = await _issue(real_unlocked_client, "rotate_passphrase")
    r = await real_unlocked_client.post(
        "/api/security/rotate-passphrase",
        json={"old_passphrase": initial, "new_passphrase": "new-pass-12345-abcdef"},
        headers={"X-Reprompt-Grant": grant},
    )
    # Surface as 500 (the RuntimeError bubbles).
    assert r.status_code >= 500

    # Salt file unchanged.
    assert real_unlocked_app.state.settings.kdf_salt_path.read_bytes() == old_salt_bytes
    # KeyMaterial still holds OLD keys.
    assert await km.verify_passphrase(initial) is True
    # Credentials decryptable with OLD KEK (rolled back). The fixture
    # creates v1-AAD credentials; `decrypt_credential` transparently
    # falls back to v1 when v2 doesn't match (Hex Audit BA-F5 rolling
    # migration).
    from app.crypto.aead import decrypt_credential

    async with sm() as s:
        for tid, original_pt in zip(target_ids, plaintexts, strict=True):
            cred = await CredentialsRepository(s).get_for_target(tid)
            assert cred is not None
            recovered = decrypt_credential(
                km.require_ready().stream_key_kek,
                cred.nonce,
                cred.ciphertext,
                cred.salt,
                tid,
            ).decode("utf-8")
            assert recovered == original_pt
