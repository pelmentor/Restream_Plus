"""Schema bring-up, version reconciliation, and crash-recovery tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.config import AppSettings
from app.crypto import verify_password
from app.db import (
    SCHEMA_VERSION,
    SchemaVersionMismatchError,
    create_engine,
    initialize_schema,
    make_sessionmaker,
)
from app.db.models import (
    AuditLogORM,
    Base,
    SessionsHistoryORM,
    SettingsORM,
    UserORM,
)
from app.db.schema_init import ADMIN_USERNAME
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tests.db.conftest import build_settings


@pytest.mark.asyncio
async def test_fresh_init_creates_all_tables(engine: AsyncEngine, settings: AppSettings) -> None:
    result = await initialize_schema(engine, settings)
    assert result.fresh is True
    assert result.recovered_session_ids == ()
    assert result.admin_password_set is False

    expected_tables = {orm.__tablename__ for orm in Base.__subclasses__()}
    # Plus the bookkeeping table SQLite manages for AUTOINCREMENT.
    expected_tables.add("sqlite_sequence")
    async with engine.connect() as conn:
        rows = (
            await conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")
        ).all()
    actual_tables = {row[0] for row in rows}
    assert expected_tables.issubset(
        actual_tables
    ), f"missing tables: {expected_tables - actual_tables}"


@pytest.mark.asyncio
async def test_user_version_pragma_is_set(engine: AsyncEngine, settings: AppSettings) -> None:
    await initialize_schema(engine, settings)
    async with engine.connect() as conn:
        result = await conn.exec_driver_sql("PRAGMA user_version")
        assert int(result.scalar_one()) == SCHEMA_VERSION


@pytest.mark.asyncio
async def test_seed_inserts_singleton_settings_and_admin(
    engine: AsyncEngine,
    settings: AppSettings,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as s:
        settings_rows = (await s.execute(select(SettingsORM))).scalars().all()
        user_rows = (await s.execute(select(UserORM))).scalars().all()

    assert len(settings_rows) == 1
    assert settings_rows[0].id == 1
    assert settings_rows[0].first_run_complete == 0
    assert len(settings_rows[0].ingest_key_current) >= 16

    assert len(user_rows) == 1
    assert user_rows[0].username == ADMIN_USERNAME
    assert user_rows[0].password_hash is None


@pytest.mark.asyncio
async def test_seed_with_admin_password_writes_hash(tmp_path: Path) -> None:
    settings = build_settings(
        tmp_path,
        admin_password=SecretStr("correct-horse-battery"),
    )
    engine = create_engine(settings)
    try:
        result = await initialize_schema(engine, settings)
        assert result.admin_password_set is True

        sm = make_sessionmaker(engine)
        async with sm() as s:
            user = (await s.execute(select(UserORM))).scalar_one()
            settings_row = (await s.execute(select(SettingsORM))).scalar_one()
        assert user.password_hash is not None
        verification = verify_password(user.password_hash, "correct-horse-battery")
        assert verification.ok is True
        assert settings_row.first_run_complete == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_idempotent_init_on_second_boot(engine: AsyncEngine, settings: AppSettings) -> None:
    first = await initialize_schema(engine, settings)
    second = await initialize_schema(engine, settings)
    assert first.fresh is True
    assert second.fresh is False


@pytest.mark.asyncio
async def test_version_mismatch_refuses_to_start(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    engine = create_engine(settings)
    try:
        await initialize_schema(engine, settings)
        # Simulate an older database by clobbering user_version.
        async with engine.begin() as conn:
            await conn.exec_driver_sql("PRAGMA user_version = 999")
        with pytest.raises(SchemaVersionMismatchError) as exc_info:
            await initialize_schema(engine, settings)
        msg = str(exc_info.value)
        # The error must name the export+reimport procedure so an
        # operator reading logs knows what to do.
        assert "export" in msg.lower()
        assert "reimport" in msg.lower() or "import" in msg.lower()
        assert "999" in msg
        assert str(SCHEMA_VERSION) in msg
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_crash_recovery_patches_orphan_run_sessions(
    initialized_engine: AsyncEngine,
    settings: AppSettings,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """An interrupted run-session (ended_at IS NULL) is closed on next boot."""
    # Inject an orphan row directly — this represents the on-disk state
    # left behind when the control plane crashed mid-stream.
    orphan_id = "run-orphan-1"
    started_at = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    async with sessionmaker() as s, s.begin():
        s.add(
            SessionsHistoryORM(
                id=orphan_id,
                started_at=started_at,
                ended_at=None,
                end_reason=None,
                notes_json="{}",
            )
        )

    result = await initialize_schema(initialized_engine, settings)
    assert orphan_id in result.recovered_session_ids

    async with sessionmaker() as s:
        patched = (
            await s.execute(select(SessionsHistoryORM).where(SessionsHistoryORM.id == orphan_id))
        ).scalar_one()
        audit_rows = (
            (
                await s.execute(
                    select(AuditLogORM).where(
                        AuditLogORM.event_type == "interrupted_session_recovered"
                    )
                )
            )
            .scalars()
            .all()
        )

    assert patched.ended_at == started_at
    assert patched.end_reason == "control_plane_crash"
    assert len(audit_rows) == 1
    audit_data = json.loads(audit_rows[0].data_json)
    assert audit_data["session_id"] == orphan_id
    assert audit_data["reason"] == "control_plane_crash"
    # The recovery records `started_at` (not an "estimated_end_at"), because
    # status snapshots are WebSocket-only and not persisted — so any end-time
    # guess is just `started_at`. A `note` field documents this.
    assert audit_data["started_at"] == started_at.isoformat()
    assert "note" in audit_data


@pytest.mark.asyncio
async def test_reset_admin_password_updates_existing_hash(tmp_path: Path) -> None:
    """ADR-0005 reset flow: env vars on subsequent boot rewrite the hash."""
    # Initial boot: no admin password.
    settings_a = build_settings(tmp_path)
    engine = create_engine(settings_a)
    try:
        await initialize_schema(engine, settings_a)
    finally:
        await engine.dispose()

    # Subsequent boot with both env vars set.
    settings_b = build_settings(
        tmp_path,
        admin_password=SecretStr("brand-new-password-123"),
        reset_admin_password=True,
    )
    engine = create_engine(settings_b)
    try:
        result = await initialize_schema(engine, settings_b)
        assert result.fresh is False
        assert result.admin_password_set is True

        sm = make_sessionmaker(engine)
        async with sm() as s:
            user = (await s.execute(select(UserORM))).scalar_one()
            settings_row = (await s.execute(select(SettingsORM))).scalar_one()
        assert user.password_hash is not None
        assert verify_password(user.password_hash, "brand-new-password-123").ok is True
        assert settings_row.first_run_complete == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_orm_models_match_schema_sql(
    initialized_engine: AsyncEngine,
) -> None:
    """Catch drift: ORM-declared tables vs. tables in the on-disk schema.

    ADR-0007: schema.sql is the source of truth. If a column is added
    to schema.sql but not to models.py (or vice-versa), this test
    fails loudly rather than at INSERT time in some other test.
    """
    orm_tables = {orm.__tablename__ for orm in Base.__subclasses__()}
    async with initialized_engine.connect() as conn:
        rows = (
            await conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")
        ).all()
    sql_tables = {row[0] for row in rows} - {"sqlite_sequence"}
    assert orm_tables == sql_tables, (
        f"drift between ORM and schema.sql:\n"
        f"  only-in-ORM: {orm_tables - sql_tables}\n"
        f"  only-in-SQL: {sql_tables - orm_tables}"
    )

    # And every column declared in ORM must exist in schema.sql with
    # the same name (we don't compare types, since SQLAlchemy types
    # render differently — `INTEGER` vs `INT`).
    async with initialized_engine.connect() as conn:
        for orm_cls in Base.__subclasses__():
            table = orm_cls.__tablename__
            decl_columns = {col.name for col in orm_cls.__table__.columns}
            actual_rows = (await conn.exec_driver_sql(f"PRAGMA table_info({table})")).all()
            actual_columns = {row[1] for row in actual_rows}
            assert decl_columns == actual_columns, (
                f"column drift on {table}:\n"
                f"  only-in-ORM: {decl_columns - actual_columns}\n"
                f"  only-in-SQL: {actual_columns - decl_columns}"
            )
