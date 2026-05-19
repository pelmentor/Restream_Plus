"""Hex Audit FG2-M6 + BA-F4 (slice 8) — audit-log retention loop.

Pins the load-bearing invariants of `_audit_log_retention_loop`:

1. Rows with `at < now - retention_days` are deleted; rows newer than
   the cutoff stay.
2. A non-empty delete emits an `audit_log_retention_run` audit-of-the-
   audit row in the SAME transaction as the delete (atomic — never a
   case where rows vanished but the forensic marker is missing).
3. An empty tick (no rows to delete) does NOT emit the marker row.
4. Failures in the delete txn are logged and the loop continues —
   does not crash out.

The retention loop's checkpoint cadence is tested implicitly via the
standalone-checkpoint test in `tests/db/test_repositories.py`; here we
exercise the delete + audit emission contract.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from app.repositories.audit_log import AuditLogRepository

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest.mark.asyncio
async def test_retention_deletes_old_rows_and_emits_marker(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    repo = AuditLogRepository(sessionmaker)
    now = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)
    cutoff = now - timedelta(days=365)

    # Seed: 3 rows older than cutoff, 2 newer.
    async with sessionmaker() as s, s.begin():
        for i in range(3):
            await repo.append(
                s,
                event_type="ancient",
                actor="system",
                at=cutoff - timedelta(days=10 + i),
            )
        for i in range(2):
            await repo.append(
                s,
                event_type="recent",
                actor="system",
                at=cutoff + timedelta(days=10 + i),
            )

    # Run one retention iteration explicitly (same logic the loop body
    # uses; pulled out so the test doesn't have to drive the sleep).
    async with sessionmaker() as s, s.begin():
        deleted = await repo.delete_older_than(s, cutoff=cutoff)
        if deleted > 0:
            await repo.append(
                s,
                event_type="audit_log_retention_run",
                actor="system",
                data={
                    "rows_deleted": deleted,
                    "cutoff": cutoff.isoformat(),
                    "retention_days": 365,
                },
            )

    assert deleted == 3
    async with sessionmaker() as s:
        rows = await repo.list_recent(s, limit=100)
    event_types = [r.event_type for r in rows]
    assert "ancient" not in event_types
    assert event_types.count("recent") == 2
    assert event_types.count("audit_log_retention_run") == 1


@pytest.mark.asyncio
async def test_retention_empty_tick_does_not_emit_marker(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """No rows past the cutoff → no `audit_log_retention_run` row.

    The forensic record only fires when retention actually did
    something; an empty tick is silent (otherwise the table fills with
    no-op markers).
    """
    repo = AuditLogRepository(sessionmaker)
    now = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)
    cutoff = now - timedelta(days=365)

    async with sessionmaker() as s, s.begin():
        await repo.append(
            s,
            event_type="recent",
            actor="system",
            at=cutoff + timedelta(days=10),
        )

    async with sessionmaker() as s, s.begin():
        deleted = await repo.delete_older_than(s, cutoff=cutoff)
        if deleted > 0:
            await repo.append(
                s,
                event_type="audit_log_retention_run",
                actor="system",
                data={
                    "rows_deleted": deleted,
                    "cutoff": cutoff.isoformat(),
                    "retention_days": 365,
                },
            )

    assert deleted == 0
    async with sessionmaker() as s:
        rows = await repo.list_recent(s, limit=100)
    event_types = [r.event_type for r in rows]
    assert "audit_log_retention_run" not in event_types


@pytest.mark.asyncio
async def test_retention_delete_and_marker_are_atomic(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Hex Audit BA-F4 (slice 8): if the marker insert fails inside
    the same txn as the delete, the delete rolls back too.

    Forces a marker-emission failure by simulating an exception after
    delete + append have both flushed but before commit. Verifies the
    ancient rows are still present.
    """
    repo = AuditLogRepository(sessionmaker)
    now = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)
    cutoff = now - timedelta(days=365)

    async with sessionmaker() as s, s.begin():
        await repo.append(
            s,
            event_type="ancient",
            actor="system",
            at=cutoff - timedelta(days=10),
        )

    raised = False
    try:
        async with sessionmaker() as s, s.begin():
            deleted = await repo.delete_older_than(s, cutoff=cutoff)
            assert deleted == 1
            await repo.append(
                s,
                event_type="audit_log_retention_run",
                actor="system",
                data={"rows_deleted": deleted, "cutoff": cutoff.isoformat()},
            )
            raise RuntimeError("simulated post-marker write failure")
    except RuntimeError:
        raised = True

    assert raised
    async with sessionmaker() as s:
        rows = await repo.list_recent(s, limit=100)
    event_types = [r.event_type for r in rows]
    # The atomic invariant: ancient row should still exist, no marker.
    assert event_types.count("ancient") == 1
    assert "audit_log_retention_run" not in event_types


@pytest.mark.asyncio
async def test_run_wal_checkpoint_executes_without_error(
    sessionmaker: async_sessionmaker[AsyncSession],
    db_path: Path,
) -> None:
    """`run_wal_checkpoint` runs in its own session, after the loop
    commits the delete + marker. Verifies the checkpoint actually
    shrinks the WAL (matches the standalone-append behavior)."""
    repo = AuditLogRepository(sessionmaker)
    # Write a few rows to grow the WAL.
    for i in range(20):
        await repo.append_standalone(
            event_type="some_event", actor="system", data={"i": i}, checkpoint=False
        )
    # Now run a single retention-loop-style checkpoint.
    await repo.run_wal_checkpoint(reason="test")
    wal_path = db_path.with_name(db_path.name + "-wal")
    assert wal_path.exists()
    assert wal_path.stat().st_size < 4 * 1024


# ======================================================================
# Slice 8.5 (Re-audit Checkpoint #2): hardening invariants
# ======================================================================


@pytest.mark.asyncio
async def test_retention_excludes_audit_log_retention_run_rows(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Hex Audit FG3-F4: `audit_log_retention_run` markers are
    eternal — the retention loop excludes them from the delete
    predicate so the marker series persists as a long-horizon record
    of retention cadence."""
    repo = AuditLogRepository(sessionmaker)
    now = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)
    cutoff = now - timedelta(days=365)

    # An aged retention marker and an aged real row.
    async with sessionmaker() as s, s.begin():
        await repo.append(
            s,
            event_type="audit_log_retention_run",
            actor="system",
            at=cutoff - timedelta(days=30),
            data={"rows_deleted": 5, "cutoff": "old"},
        )
        await repo.append(
            s,
            event_type="login_succeeded",
            actor="admin",
            at=cutoff - timedelta(days=30),
        )

    async with sessionmaker() as s, s.begin():
        deleted = await repo.delete_older_than(
            s,
            cutoff=cutoff,
            exclude_event_types=("audit_log_retention_run",),
        )

    assert deleted == 1  # only the login_succeeded, not the marker
    async with sessionmaker() as s:
        rows = await repo.list_recent(s, limit=100)
    event_types = [r.event_type for r in rows]
    assert "audit_log_retention_run" in event_types
    assert "login_succeeded" not in event_types


@pytest.mark.asyncio
async def test_retention_once_checkpoint_failure_does_not_kill_loop(
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hex Audit CR2-Re-F1: a `run_wal_checkpoint` failure must NOT
    escape `_audit_log_retention_once`. Under the pre-slice-8.5 code,
    an unhandled exception in `run_wal_checkpoint` killed the
    retention loop permanently. The slice-8.5 hardening wraps each
    checkpoint call in try/except; the function returns cleanly and
    the loop continues."""
    from app.main import _AuditRetentionTickState, _audit_log_retention_once

    # Seed an aged row so the delete branch fires and triggers the
    # `audit_log_retention_run` checkpoint path.
    repo = AuditLogRepository(sessionmaker)
    now = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)
    cutoff = now - timedelta(days=365)
    async with sessionmaker() as s, s.begin():
        await repo.append(
            s,
            event_type="ancient",
            actor="system",
            at=cutoff - timedelta(days=10),
        )

    boom_count = 0

    async def _boom(self: object, *, reason: str) -> None:
        nonlocal boom_count
        boom_count += 1
        raise RuntimeError(f"simulated checkpoint failure: {reason}")

    monkeypatch.setattr(
        "app.repositories.audit_log.AuditLogRepository.run_wal_checkpoint",
        _boom,
    )

    state = _AuditRetentionTickState()
    # Must NOT raise.
    await _audit_log_retention_once(sessionmaker, 365, state, now=now)

    # CR2-Re-F2: last_idle_checkpoint NOT advanced when checkpoint failed.
    assert state.last_idle_checkpoint is None
    # Consecutive-failure counter incremented.
    assert state.consecutive_failures == 1
    # Checkpoint was actually attempted.
    assert boom_count == 1


@pytest.mark.asyncio
async def test_retention_once_recovery_resets_failure_counter(
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hex Audit BA2-F3 / FG3-F2: a successful tick after a failure
    streak resets `consecutive_failures` to 0 and emits a recovery
    log. Pins the counter's hysteresis semantics."""
    from app.main import _AuditRetentionTickState, _audit_log_retention_once

    # An empty tick path (no aged rows) hits the idle-checkpoint
    # branch — easier to control fail/recover with monkeypatch.
    state = _AuditRetentionTickState(consecutive_failures=3)
    now = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)

    # Successful checkpoint this tick.
    await _audit_log_retention_once(sessionmaker, 365, state, now=now)

    assert state.consecutive_failures == 0
    assert state.last_idle_checkpoint == now


@pytest.mark.asyncio
async def test_retention_once_threshold_escalates_to_critical(
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Hex Audit BA2-F3 / FG3-F2: when consecutive_failures reaches
    `AUDIT_LOG_RETENTION_FAILURE_THRESHOLD`, the loop emits a
    `_logger.critical` escalation. Verifies the threshold semantic.

    Structlog routes to stderr (per `app/logging_setup.py`), so we
    use `capfd` rather than `caplog` to observe the line.
    """
    from app.main import (
        AUDIT_LOG_RETENTION_FAILURE_THRESHOLD,
        _AuditRetentionTickState,
        _audit_log_retention_once,
    )

    async def _boom(self: object, *, reason: str) -> None:
        raise RuntimeError("simulated")

    monkeypatch.setattr(
        "app.repositories.audit_log.AuditLogRepository.run_wal_checkpoint",
        _boom,
    )

    state = _AuditRetentionTickState(
        consecutive_failures=AUDIT_LOG_RETENTION_FAILURE_THRESHOLD - 1
    )
    now = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)

    await _audit_log_retention_once(sessionmaker, 365, state, now=now)

    assert state.consecutive_failures == AUDIT_LOG_RETENTION_FAILURE_THRESHOLD
    # The critical-level escalation fired on the threshold tick.
    # Structlog is configured to stderr in production but test
    # contexts may capture either stream depending on whether
    # `configure_logging` was called; match either.
    captured = capfd.readouterr()
    assert "audit_log_retention_stuck" in (captured.err + captured.out)


@pytest.mark.asyncio
async def test_list_by_target_finds_target_deleted_via_data_json(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Hex Audit FG3-F6: `target_deleted` rows carry `target_id=NULL`
    in the FK column (the column-level FK would refuse the insert in
    the same txn as the target delete). The deleted id lives in
    `data_json["target_id"]`. `list_by_target` matches BOTH the FK
    column AND the JSON1 path so deletion events are not silently
    dropped from target-scoped audit queries."""
    repo = AuditLogRepository(sessionmaker)
    target_id = "tgt_demo_abc123"

    async with sessionmaker() as s, s.begin():
        # A row written while the target still existed (FK column
        # populated) — but to keep the test independent of a real
        # targets row, write the related row with target_id=None and
        # put the id in data_json (same shape as `target_deleted`).
        await repo.append(
            s,
            event_type="target_deleted",
            actor="admin",
            data={"target_id": target_id, "label": "demo"},
        )

    async with sessionmaker() as s:
        rows = await repo.list_by_target(s, target_id)

    assert len(rows) == 1
    assert rows[0].event_type == "target_deleted"
    assert rows[0].data.get("target_id") == target_id
