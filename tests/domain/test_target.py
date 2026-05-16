"""Target aggregate + ui_state ladder tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.domain.target import Target, TargetUiState, WorkerSet, compute_ui_state
from app.domain.target_types import TargetType
from app.domain.worker_state import WorkerRole, WorkerSnapshot, WorkerState

T0 = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)


def _snap(role: WorkerRole, state: WorkerState) -> WorkerSnapshot:
    return WorkerSnapshot(
        role=role,
        state=state,
        last_event_at=T0,
        last_error=None,
        breaker_failures_in_window=0,
    )


def _t(
    target_type: TargetType = TargetType.TWITCH,
    *,
    enabled: bool = True,
    backup_enabled: bool = False,
    has_active_credential: bool = True,
) -> Target:
    return Target(
        id="tgt-1",
        type=target_type,
        label="Test target",
        url="rtmp://example/app",
        enabled=enabled,
        backup_enabled=backup_enabled,
        has_active_credential=has_active_credential,
    )


class TestUiStatePrecedence:
    """Each test asserts the ladder rung ordering claimed in compute_ui_state."""

    def test_disabled_wins_over_misconfigured(self) -> None:
        t = _t(enabled=False, has_active_credential=False)
        assert t.ui_state() is TargetUiState.DISABLED

    def test_misconfigured_when_no_credential(self) -> None:
        t = _t(has_active_credential=False)
        assert t.ui_state() is TargetUiState.DISABLED_MISCONFIGURED

    def test_vk_without_credential_is_misconfigured(self) -> None:
        t = _t(TargetType.VK_LIVE, has_active_credential=False)
        assert t.ui_state() is TargetUiState.DISABLED_MISCONFIGURED

    def test_idle_when_no_snapshots(self) -> None:
        t = _t()
        assert t.ui_state() is TargetUiState.IDLE

    def test_starting_when_any_starting(self) -> None:
        t = _t()
        t.ingest_snapshot(_snap(WorkerRole.PRIMARY, WorkerState.STARTING))
        assert t.ui_state() is TargetUiState.STARTING

    def test_running_when_all_running(self) -> None:
        t = _t()
        t.ingest_snapshot(_snap(WorkerRole.PRIMARY, WorkerState.RUNNING))
        assert t.ui_state() is TargetUiState.RUNNING

    def test_failed_open_wins_over_running(self) -> None:
        t = _t(TargetType.YOUTUBE, backup_enabled=True)
        t.ingest_snapshot(_snap(WorkerRole.PRIMARY, WorkerState.RUNNING))
        t.ingest_snapshot(_snap(WorkerRole.BACKUP, WorkerState.FAILED_OPEN))
        assert t.ui_state() is TargetUiState.FAILED_OPEN

    def test_failed_open_wins_over_starting(self) -> None:
        t = _t(TargetType.YOUTUBE, backup_enabled=True)
        t.ingest_snapshot(_snap(WorkerRole.PRIMARY, WorkerState.STARTING))
        t.ingest_snapshot(_snap(WorkerRole.BACKUP, WorkerState.FAILED_OPEN))
        assert t.ui_state() is TargetUiState.FAILED_OPEN


class TestYouTubeBackupAggregation:
    def test_both_running_is_running(self) -> None:
        t = _t(TargetType.YOUTUBE, backup_enabled=True)
        t.ingest_snapshot(_snap(WorkerRole.PRIMARY, WorkerState.RUNNING))
        t.ingest_snapshot(_snap(WorkerRole.BACKUP, WorkerState.RUNNING))
        assert t.ui_state() is TargetUiState.RUNNING

    def test_one_running_one_reconnecting_is_degraded(self) -> None:
        t = _t(TargetType.YOUTUBE, backup_enabled=True)
        t.ingest_snapshot(_snap(WorkerRole.PRIMARY, WorkerState.RUNNING))
        t.ingest_snapshot(_snap(WorkerRole.BACKUP, WorkerState.RECONNECTING))
        assert t.ui_state() is TargetUiState.DEGRADED

    def test_one_running_one_missing_snapshot_is_degraded(self) -> None:
        # Only PRIMARY snapshot ingested; BACKUP is missing → effectively idle.
        # Some workers are healthy ⇒ DEGRADED (YouTube-only branch).
        t = _t(TargetType.YOUTUBE, backup_enabled=True)
        t.ingest_snapshot(_snap(WorkerRole.PRIMARY, WorkerState.RUNNING))
        assert t.ui_state() is TargetUiState.DEGRADED

    def test_both_reconnecting_is_errored(self) -> None:
        t = _t(TargetType.YOUTUBE, backup_enabled=True)
        t.ingest_snapshot(_snap(WorkerRole.PRIMARY, WorkerState.RECONNECTING))
        t.ingest_snapshot(_snap(WorkerRole.BACKUP, WorkerState.RECONNECTING))
        assert t.ui_state() is TargetUiState.ERRORED

    def test_youtube_without_backup_uses_single_role(self) -> None:
        t = _t(TargetType.YOUTUBE, backup_enabled=False)
        t.ingest_snapshot(_snap(WorkerRole.PRIMARY, WorkerState.RUNNING))
        assert t.ui_state() is TargetUiState.RUNNING


class TestErroredFallback:
    def test_single_worker_reconnecting_is_errored(self) -> None:
        # Twitch (single worker), reconnecting → not running, not starting,
        # not failed_open, not YouTube-with-backup degraded → ERRORED.
        t = _t(TargetType.TWITCH)
        t.ingest_snapshot(_snap(WorkerRole.PRIMARY, WorkerState.RECONNECTING))
        assert t.ui_state() is TargetUiState.ERRORED

    def test_single_worker_stopping_is_errored(self) -> None:
        t = _t(TargetType.KICK)
        t.ingest_snapshot(_snap(WorkerRole.PRIMARY, WorkerState.STOPPING))
        assert t.ui_state() is TargetUiState.ERRORED


class TestExpectedRoles:
    def test_youtube_with_backup_expects_two_roles(self) -> None:
        t = _t(TargetType.YOUTUBE, backup_enabled=True)
        assert t.expected_worker_roles() == frozenset({WorkerRole.PRIMARY, WorkerRole.BACKUP})

    def test_youtube_without_backup_expects_primary_only(self) -> None:
        t = _t(TargetType.YOUTUBE, backup_enabled=False)
        assert t.expected_worker_roles() == frozenset({WorkerRole.PRIMARY})

    @pytest.mark.parametrize(
        "target_type",
        [TargetType.TWITCH, TargetType.KICK, TargetType.VK_LIVE, TargetType.CUSTOM],
    )
    def test_other_types_ignore_backup_flag(self, target_type: TargetType) -> None:
        # backup_enabled=True on a non-YouTube target is meaningless.
        t = _t(target_type, backup_enabled=True)
        assert t.expected_worker_roles() == frozenset({WorkerRole.PRIMARY})


class TestWorkerSet:
    def test_role_returns_none_when_absent(self) -> None:
        ws = WorkerSet()
        assert ws.role(WorkerRole.PRIMARY) is None

    def test_ingest_then_role_returns_snapshot(self) -> None:
        t = _t()
        snap = _snap(WorkerRole.PRIMARY, WorkerState.RUNNING)
        t.ingest_snapshot(snap)
        assert t.worker_set.role(WorkerRole.PRIMARY) is snap

    def test_ingest_overwrites_previous_snapshot(self) -> None:
        t = _t()
        t.ingest_snapshot(_snap(WorkerRole.PRIMARY, WorkerState.STARTING))
        new_snap = _snap(WorkerRole.PRIMARY, WorkerState.RUNNING)
        t.ingest_snapshot(new_snap)
        assert t.worker_set.role(WorkerRole.PRIMARY) is new_snap

    def test_backup_only_running_no_primary_snapshot_is_degraded(self) -> None:
        # Symmetric to test_one_running_one_missing_snapshot_is_degraded:
        # BACKUP RUNNING + missing PRIMARY snapshot still aggregates to DEGRADED.
        t = _t(TargetType.YOUTUBE, backup_enabled=True)
        t.ingest_snapshot(_snap(WorkerRole.BACKUP, WorkerState.RUNNING))
        assert t.ui_state() is TargetUiState.DEGRADED


class TestComputeUiStateFunction:
    """The function is the implementation; ui_state() delegates."""

    def test_function_matches_method(self) -> None:
        t = _t()
        t.ingest_snapshot(_snap(WorkerRole.PRIMARY, WorkerState.RUNNING))
        assert compute_ui_state(t) is t.ui_state()
