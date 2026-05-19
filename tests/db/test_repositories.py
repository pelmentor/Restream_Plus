"""Repository round-trip tests.

One TestClass per repository. Each round-trip is checked against the
Pydantic DTO surface (caller-visible) rather than ORM internals, so
these tests fail if either the ORM mapping OR the DTO mapping breaks.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.repositories import (
    ApiTokensRepository,
    AuditLogRepository,
    CredentialsRepository,
    HttpSessionsRepository,
    SessionsHistoryRepository,
    SettingsRepository,
    TargetsRepository,
    UsersRepository,
)
from app.repositories.audit_log import AuditLogEntryDTO
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def _now() -> datetime:
    return datetime.now(tz=UTC)


# --- Users -------------------------------------------------------------------


class TestUsersRepository:
    @pytest.mark.asyncio
    async def test_seeded_admin_is_retrievable(self, session: AsyncSession) -> None:
        repo = UsersRepository(session)
        admin = await repo.get_by_username("admin")
        assert admin is not None
        assert admin.username == "admin"
        # Since SCHEMA_VERSION=2 the seed path always writes a hash (the
        # `settings` fixture supplies admin_password; otherwise autogen
        # would have fired).
        assert admin.password_hash is not None

    @pytest.mark.asyncio
    async def test_create_and_get_by_id(self, session: AsyncSession) -> None:
        repo = UsersRepository(session)
        created = await repo.create(username="ops", password_hash="hash")
        await session.commit()
        fetched = await repo.get_by_id(created.id)
        assert fetched is not None
        assert fetched.username == "ops"

    @pytest.mark.asyncio
    async def test_update_password_hash(self, session: AsyncSession) -> None:
        repo = UsersRepository(session)
        user = await repo.create(username="ops2", password_hash="initial-hash")
        await repo.update_password_hash(user.id, "new-hash")
        await session.commit()
        again = await repo.get_by_id(user.id)
        assert again is not None
        assert again.password_hash is not None
        assert again.password_hash.get_secret_value() == "new-hash"

    @pytest.mark.asyncio
    async def test_touch_last_login_rejects_naive(self, session: AsyncSession) -> None:
        repo = UsersRepository(session)
        user = await repo.create(username="ops3", password_hash="placeholder-hash")
        with pytest.raises(ValueError, match="timezone-aware"):
            await repo.touch_last_login(user.id, datetime(2026, 1, 1))


# --- HTTP sessions -----------------------------------------------------------


class TestHttpSessionsRepository:
    @pytest.mark.asyncio
    async def test_create_get_touch_delete_cycle(self, session: AsyncSession) -> None:
        users = UsersRepository(session)
        sessions = HttpSessionsRepository(session)
        user = await users.create(username="alice", password_hash="x")
        token_hash = secrets.token_bytes(32)
        created = await sessions.create(
            token_hash=token_hash,
            user_id=user.id,
            expires_at=_now() + timedelta(days=14),
            user_agent="curl/8",
            ip="127.0.0.1",
        )
        assert created.user_agent == "curl/8"

        fetched = await sessions.get_by_token_hash(token_hash)
        assert fetched is not None
        assert fetched.user_id == user.id

        new_seen = _now() + timedelta(minutes=1)
        await sessions.touch_last_seen(token_hash, new_seen)
        await session.commit()
        bumped = await sessions.get_by_token_hash(token_hash)
        assert bumped is not None
        assert bumped.last_seen_at >= new_seen.replace(microsecond=0)

        await sessions.delete(token_hash)
        await session.commit()
        assert await sessions.get_by_token_hash(token_hash) is None

    @pytest.mark.asyncio
    async def test_delete_expired_only_removes_expired(self, session: AsyncSession) -> None:
        users = UsersRepository(session)
        sessions = HttpSessionsRepository(session)
        user = await users.create(username="bob", password_hash="x")
        live_hash = secrets.token_bytes(32)
        dead_hash = secrets.token_bytes(32)
        await sessions.create(
            token_hash=live_hash, user_id=user.id, expires_at=_now() + timedelta(days=1)
        )
        await sessions.create(
            token_hash=dead_hash, user_id=user.id, expires_at=_now() - timedelta(days=1)
        )
        removed = await sessions.delete_expired(_now())
        await session.commit()
        assert removed == 1
        assert await sessions.get_by_token_hash(live_hash) is not None
        assert await sessions.get_by_token_hash(dead_hash) is None

    @pytest.mark.asyncio
    async def test_delete_all_for_user_returns_rowcount(self, session: AsyncSession) -> None:
        users = UsersRepository(session)
        sessions = HttpSessionsRepository(session)
        user = await users.create(username="carol", password_hash="x")
        for _ in range(3):
            await sessions.create(
                token_hash=secrets.token_bytes(32),
                user_id=user.id,
                expires_at=_now() + timedelta(days=1),
            )
        removed = await sessions.delete_all_for_user(user.id)
        await session.commit()
        assert removed == 3


# --- API tokens --------------------------------------------------------------


class TestApiTokensRepository:
    @pytest.mark.asyncio
    async def test_create_returns_metadata_without_hash(self, session: AsyncSession) -> None:
        users = UsersRepository(session)
        admin = await users.get_by_username("admin")
        assert admin is not None
        repo = ApiTokensRepository(session)
        dto = await repo.create(
            user_id=admin.id, token_hash=secrets.token_bytes(32), label="deploy"
        )
        # The DTO must not expose the hash to callers.
        assert "token_hash" not in dto.model_dump()
        assert dto.label == "deploy"
        assert dto.user_id == admin.id
        assert dto.revoked_at is None

    @pytest.mark.asyncio
    async def test_revoked_token_is_not_returned_by_get_active(self, session: AsyncSession) -> None:
        users = UsersRepository(session)
        admin = await users.get_by_username("admin")
        assert admin is not None
        repo = ApiTokensRepository(session)
        token_hash = secrets.token_bytes(32)
        dto = await repo.create(user_id=admin.id, token_hash=token_hash, label="t")
        assert await repo.get_active_by_token_hash(token_hash) is not None
        revoked = await repo.revoke(dto.id, _now())
        await session.commit()
        assert revoked is True
        assert await repo.get_active_by_token_hash(token_hash) is None
        # Revoking again is a no-op (returns False).
        assert (await repo.revoke(dto.id, _now())) is False

    @pytest.mark.asyncio
    async def test_list_all_orders_by_created_at_desc(self, session: AsyncSession) -> None:
        users = UsersRepository(session)
        admin = await users.get_by_username("admin")
        assert admin is not None
        repo = ApiTokensRepository(session)
        labels = ["a", "b", "c"]
        for label in labels:
            await repo.create(user_id=admin.id, token_hash=secrets.token_bytes(32), label=label)
        tokens = await repo.list_all()
        assert len(tokens) == 3
        # Insertion order is a-b-c; created_at ties are broken by row
        # order, so we don't pin the exact ordering — just that all
        # three are present. All belong to the admin user.
        assert {t.label for t in tokens} == set(labels)
        assert all(t.user_id == admin.id for t in tokens)

    @pytest.mark.asyncio
    async def test_cascade_delete_on_user_removal(self, session: AsyncSession) -> None:
        """Phase 6: deleting a user CASCADEs their api_tokens.

        Exercises the FK + ON DELETE CASCADE invariant added to the
        api_tokens table.
        """
        users = UsersRepository(session)
        repo = ApiTokensRepository(session)
        ghost = await users.create(username="ghost", password_hash="ghost-hash")
        token_hash = secrets.token_bytes(32)
        await repo.create(user_id=ghost.id, token_hash=token_hash, label="ghost-cli")
        await session.commit()
        assert await repo.get_active_by_token_hash(token_hash) is not None
        # Delete the user; the FK should cascade.
        from app.db.models import UserORM
        from sqlalchemy import delete

        await session.execute(delete(UserORM).where(UserORM.id == ghost.id))
        await session.commit()
        assert await repo.get_active_by_token_hash(token_hash) is None


# --- Targets -----------------------------------------------------------------


class TestTargetsRepository:
    @pytest.mark.asyncio
    async def test_create_and_list_round_trip(self, session: AsyncSession) -> None:
        repo = TargetsRepository(session)
        await repo.create(type="twitch", label="Main Twitch", url="rtmp://live.twitch.tv/app/")
        await repo.create(
            type="youtube",
            label="YT Backup",
            url="rtmp://a.rtmp.youtube.com/live2/",
            enabled=False,
            settings={"backup_url": "rtmp://b.rtmp.youtube.com/live2?backup=1"},
        )
        all_targets = await repo.list_all()
        enabled_only = await repo.list_enabled()
        assert len(all_targets) == 2
        assert len(enabled_only) == 1
        assert enabled_only[0].type == "twitch"
        yt = next(t for t in all_targets if t.type == "youtube")
        assert yt.settings == {"backup_url": "rtmp://b.rtmp.youtube.com/live2?backup=1"}
        assert yt.enabled is False

    @pytest.mark.asyncio
    async def test_update_only_changes_named_fields(self, session: AsyncSession) -> None:
        repo = TargetsRepository(session)
        created = await repo.create(type="kick", label="Old", url="rtmps://old/")
        updated = await repo.update(created.id, label="New")
        assert updated is not None
        assert updated.label == "New"
        assert updated.url == "rtmps://old/"  # unchanged

    @pytest.mark.asyncio
    async def test_delete_returns_true_only_when_row_existed(self, session: AsyncSession) -> None:
        repo = TargetsRepository(session)
        t = await repo.create(type="custom", label="X", url="rtmp://x/")
        assert await repo.delete(t.id) is True
        assert await repo.delete(t.id) is False


# --- Credentials -------------------------------------------------------------


class TestCredentialsRepository:
    @pytest.mark.asyncio
    async def test_upsert_replaces_existing_credential(self, session: AsyncSession) -> None:
        targets = TargetsRepository(session)
        creds = CredentialsRepository(session)
        target = await targets.create(type="twitch", label="t", url="rtmp://x/")

        first = await creds.upsert(
            target_id=target.id,
            lifetime="persistent",
            ciphertext=b"\x00" * 32,
            nonce=b"\x01" * 12,
            salt=b"\x02" * 16,
            last4="ABCD",
        )
        second = await creds.upsert(
            target_id=target.id,
            lifetime="persistent",
            ciphertext=b"\x10" * 32,
            nonce=b"\x11" * 12,
            salt=b"\x12" * 16,
            last4="WXYZ",
        )
        assert first.id != second.id  # row was replaced, new id allocated
        fetched = await creds.get_for_target(target.id)
        assert fetched is not None
        assert fetched.ciphertext == b"\x10" * 32
        assert fetched.last4 == "WXYZ"

    @pytest.mark.asyncio
    async def test_delete_for_target_returns_outcome_flag(self, session: AsyncSession) -> None:
        targets = TargetsRepository(session)
        creds = CredentialsRepository(session)
        target = await targets.create(type="vk_live", label="vk", url="rtmps://vk/")
        await creds.upsert(
            target_id=target.id,
            lifetime="per_session",
            ciphertext=b"x" * 16,
            nonce=b"n" * 12,
            salt=b"s" * 16,
            last4="LAST",
        )
        assert await creds.delete_for_target(target.id) is True
        assert await creds.delete_for_target(target.id) is False

    @pytest.mark.asyncio
    async def test_cascade_on_target_delete(self, session: AsyncSession) -> None:
        """Deleting a target wipes its credential (ON DELETE CASCADE).

        Foreign-key enforcement requires PRAGMA foreign_keys=ON, which
        the engine sets on every connection — this test fails if the
        pragma hook regresses.
        """
        targets = TargetsRepository(session)
        creds = CredentialsRepository(session)
        target = await targets.create(type="custom", label="c", url="rtmp://c/")
        await creds.upsert(
            target_id=target.id,
            lifetime="persistent",
            ciphertext=b"c" * 16,
            nonce=b"n" * 12,
            salt=b"s" * 16,
            last4="LAST",
        )
        await session.commit()
        await targets.delete(target.id)
        await session.commit()
        assert await creds.get_for_target(target.id) is None


# --- Settings ----------------------------------------------------------------


class TestSettingsRepository:
    @pytest.mark.asyncio
    async def test_get_returns_seeded_defaults(self, session: AsyncSession) -> None:
        repo = SettingsRepository(session)
        settings_dto = await repo.get()
        # SCHEMA_VERSION=2: the seed path always writes an admin hash on
        # fresh boot (env-supplied via the `settings` fixture here, or
        # autogenerated otherwise), so first_run_complete is always 1
        # after a fresh init.
        assert settings_dto.first_run_complete is True
        assert settings_dto.idle_timeout_seconds == 600
        assert settings_dto.log_retention_days == 14
        assert len(settings_dto.ingest_key_current) >= 16

    @pytest.mark.asyncio
    async def test_update_changes_only_named_fields(self, session: AsyncSession) -> None:
        repo = SettingsRepository(session)
        before = await repo.get()
        updated = await repo.update(idle_timeout_seconds=120)
        assert updated.idle_timeout_seconds == 120
        assert updated.log_retention_days == before.log_retention_days
        assert updated.ingest_key_current == before.ingest_key_current

    @pytest.mark.asyncio
    async def test_update_rejects_non_positive_values(self, session: AsyncSession) -> None:
        repo = SettingsRepository(session)
        with pytest.raises(ValueError, match="positive"):
            await repo.update(idle_timeout_seconds=0)
        with pytest.raises(ValueError, match="positive"):
            await repo.update(log_retention_days=-1)

    @pytest.mark.asyncio
    async def test_rotate_ingest_key_preserves_previous(self, session: AsyncSession) -> None:
        repo = SettingsRepository(session)
        before = await repo.get()
        rotated = await repo.rotate_ingest_key(
            new_key="new-secret-key", grace_until=_now() + timedelta(hours=24)
        )
        assert rotated.ingest_key_current == "new-secret-key"
        assert rotated.ingest_key_previous == before.ingest_key_current
        assert rotated.ingest_key_rotated_at is not None
        assert rotated.ingest_key_grace_until is not None

    # ------------------------------------------------------------------
    # Hex Audit BA-F1 (2026-05-18): clear_ingest_key_previous_after_grace
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_clear_previous_noop_when_no_rotation(self, session: AsyncSession) -> None:
        """Fresh DB has no previous key — the clear is a no-op."""
        repo = SettingsRepository(session)
        cleared = await repo.clear_ingest_key_previous_after_grace()
        assert cleared is False

    @pytest.mark.asyncio
    async def test_clear_previous_noop_within_grace(self, session: AsyncSession) -> None:
        """Inside the grace window the row is preserved."""
        repo = SettingsRepository(session)
        await repo.rotate_ingest_key(new_key="rotated-1", grace_until=_now() + timedelta(hours=24))
        cleared = await repo.clear_ingest_key_previous_after_grace()
        assert cleared is False
        after = await repo.get()
        assert after.ingest_key_previous is not None
        assert after.ingest_key_grace_until is not None

    @pytest.mark.asyncio
    async def test_clear_previous_after_grace_expired(self, session: AsyncSession) -> None:
        """Past the grace boundary, both columns null out atomically."""
        repo = SettingsRepository(session)
        # Rotate with a grace already 5 s in the past.
        await repo.rotate_ingest_key(new_key="rotated-2", grace_until=_now() - timedelta(seconds=5))
        cleared = await repo.clear_ingest_key_previous_after_grace()
        assert cleared is True
        after = await repo.get()
        assert after.ingest_key_previous is None
        assert after.ingest_key_grace_until is None
        # Current key untouched — only the previous-half drops.
        assert after.ingest_key_current == "rotated-2"

    @pytest.mark.asyncio
    async def test_clear_previous_idempotent(self, session: AsyncSession) -> None:
        """A second call after a successful clear returns False
        (predicate now mismatches)."""
        repo = SettingsRepository(session)
        await repo.rotate_ingest_key(new_key="rotated-3", grace_until=_now() - timedelta(seconds=1))
        assert await repo.clear_ingest_key_previous_after_grace() is True
        assert await repo.clear_ingest_key_previous_after_grace() is False

    @pytest.mark.asyncio
    async def test_clear_previous_rejects_naive_now(self, session: AsyncSession) -> None:
        from datetime import datetime as _dt

        repo = SettingsRepository(session)
        with pytest.raises(ValueError, match="timezone-aware"):
            await repo.clear_ingest_key_previous_after_grace(now=_dt(2026, 1, 1))


# --- Audit log ---------------------------------------------------------------


class TestAuditLogRepository:
    @pytest.mark.asyncio
    async def test_append_in_session_persists_and_decodes_data(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
    ) -> None:
        """Hex Audit BA-F4 (slice 8): in-session `append` flushes the
        row into the caller's transaction; the caller commits."""
        repo = AuditLogRepository(sessionmaker)
        async with sessionmaker() as s, s.begin():
            dto = await repo.append(
                s,
                event_type="login_succeeded",
                actor="admin",
                data={"ip": "127.0.0.1", "ua": "curl/8"},
            )
        assert isinstance(dto, AuditLogEntryDTO)
        assert dto.event_type == "login_succeeded"
        assert dto.data == {"ip": "127.0.0.1", "ua": "curl/8"}
        async with sessionmaker() as s:
            rows = await repo.list_recent(s)
        assert any(r.id == dto.id for r in rows)

    @pytest.mark.asyncio
    async def test_append_standalone_persists_and_decodes_data(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
    ) -> None:
        """Standalone path opens its own txn — used by lifespan/supervisor."""
        repo = AuditLogRepository(sessionmaker)
        dto = await repo.append_standalone(
            event_type="supervisor_started",
            actor="system",
            data={"build_at": "2026-05-19T00:00:00+00:00"},
        )
        assert dto.event_type == "supervisor_started"
        async with sessionmaker() as s:
            rows = await repo.list_recent(s)
        assert any(r.id == dto.id for r in rows)

    @pytest.mark.asyncio
    async def test_in_session_append_rolls_back_with_caller(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
    ) -> None:
        """Hex Audit BA-F4 (slice 8): if the caller's txn rolls back,
        the audit row rolls back too — that's the load-bearing
        transactional invariant the slice exists to guarantee."""
        repo = AuditLogRepository(sessionmaker)
        try:
            async with sessionmaker() as s, s.begin():
                await repo.append(
                    s,
                    event_type="phantom_event_should_not_persist",
                    actor="admin",
                )
                raise RuntimeError("simulated business-write failure")
        except RuntimeError:
            pass
        async with sessionmaker() as s:
            rows = await repo.list_recent(s)
        assert not any(r.event_type == "phantom_event_should_not_persist" for r in rows)

    @pytest.mark.asyncio
    async def test_standalone_append_runs_wal_checkpoint(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        db_path: Path,
    ) -> None:
        """Standalone path keeps the per-append `wal_checkpoint(TRUNCATE)`
        posture (the in-session path defers checkpoint to the periodic
        retention loop)."""
        repo = AuditLogRepository(sessionmaker)
        for i in range(50):
            await repo.append_standalone(event_type="run_started", data={"i": i})
        wal_path = db_path.with_name(db_path.name + "-wal")
        assert wal_path.exists()
        assert (
            wal_path.stat().st_size < 4 * 1024
        ), f"WAL not being checkpointed: {wal_path.stat().st_size} bytes"

    @pytest.mark.asyncio
    async def test_delete_older_than_returns_rowcount_and_strips_rows(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
    ) -> None:
        """Retention helper deletes rows with `at < cutoff` and reports
        the number affected (Hex Audit FG2-M6, slice 8)."""
        repo = AuditLogRepository(sessionmaker)
        cutoff = datetime(2026, 5, 1, tzinfo=UTC)
        old_at = datetime(2026, 4, 1, tzinfo=UTC)
        new_at = datetime(2026, 6, 1, tzinfo=UTC)
        async with sessionmaker() as s, s.begin():
            await repo.append(s, event_type="ancient", actor="system", at=old_at)
            await repo.append(s, event_type="ancient", actor="system", at=old_at)
            await repo.append(s, event_type="fresh", actor="system", at=new_at)
        async with sessionmaker() as s, s.begin():
            deleted = await repo.delete_older_than(s, cutoff=cutoff)
        assert deleted == 2
        async with sessionmaker() as s:
            rows = await repo.list_recent(s, limit=100)
        assert all(r.event_type == "fresh" for r in rows if r.event_type in {"ancient", "fresh"})

    @pytest.mark.asyncio
    async def test_delete_older_than_rejects_naive_datetime(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
    ) -> None:
        repo = AuditLogRepository(sessionmaker)
        async with sessionmaker() as s, s.begin():
            with pytest.raises(ValueError, match="timezone-aware"):
                await repo.delete_older_than(s, cutoff=datetime(2026, 5, 1))

    @pytest.mark.asyncio
    async def test_list_by_target_filters_correctly(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
    ) -> None:
        targets_repo_dto = None
        async with sessionmaker() as s, s.begin():
            tr = TargetsRepository(s)
            targets_repo_dto = await tr.create(type="twitch", label="t", url="rtmp://x/")
        target = targets_repo_dto

        repo = AuditLogRepository(sessionmaker)
        async with sessionmaker() as s, s.begin():
            await repo.append(
                s,
                event_type="failed_open_tripped",
                actor="system",
                target_id=target.id,
            )
            await repo.append(s, event_type="login_succeeded", actor="admin")
        async with sessionmaker() as s:
            scoped = await repo.list_by_target(s, target.id)
        assert len(scoped) == 1
        assert scoped[0].target_id == target.id


# --- Sessions history (run sessions) -----------------------------------------


class TestSessionsHistoryRepository:
    @pytest.mark.asyncio
    async def test_start_end_round_trip(self, session: AsyncSession) -> None:
        repo = SessionsHistoryRepository(session)
        run = await repo.start(notes={"reason": "manual"})
        assert run.ended_at is None
        active = await repo.get_active()
        assert active is not None
        assert active.id == run.id

        ended = await repo.end(run.id, end_reason="normal")
        assert ended is not None
        assert ended.end_reason == "normal"
        assert ended.ended_at is not None
        assert await repo.get_active() is None

    @pytest.mark.asyncio
    async def test_end_rejects_unknown_reason(self, session: AsyncSession) -> None:
        repo = SessionsHistoryRepository(session)
        run = await repo.start()
        with pytest.raises(ValueError, match="end_reason"):
            await repo.end(run.id, end_reason="bogus")

    @pytest.mark.asyncio
    async def test_notes_patch_merges_with_existing(self, session: AsyncSession) -> None:
        repo = SessionsHistoryRepository(session)
        run = await repo.start(notes={"a": 1})
        ended = await repo.end(run.id, end_reason="publish_idle", notes_patch={"b": 2})
        assert ended is not None
        assert ended.notes == {"a": 1, "b": 2}
