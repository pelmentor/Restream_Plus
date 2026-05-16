"""Target-level health aggregation tests per ADR-0008."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.domain.health import TargetHealthLevel, aggregate_target_health
from app.domain.target import Target
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
    backup_enabled: bool = False,
) -> Target:
    return Target(
        id="tgt-1",
        type=target_type,
        label="Test",
        url="rtmp://example/app",
        enabled=True,
        backup_enabled=backup_enabled,
        has_active_credential=True,
    )


class TestSingleWorkerTargets:
    @pytest.mark.parametrize(
        "target_type",
        [TargetType.TWITCH, TargetType.KICK, TargetType.VK_LIVE, TargetType.CUSTOM],
    )
    def test_running_is_healthy(self, target_type: TargetType) -> None:
        t = _t(target_type)
        t.ingest_snapshot(_snap(WorkerRole.PRIMARY, WorkerState.RUNNING))
        h = aggregate_target_health(t)
        assert h.level is TargetHealthLevel.HEALTHY
        assert h.healthy_roles == frozenset({WorkerRole.PRIMARY})
        assert h.unhealthy_roles == frozenset()
        assert h.is_live is True

    @pytest.mark.parametrize(
        "target_type",
        [TargetType.TWITCH, TargetType.KICK, TargetType.VK_LIVE, TargetType.CUSTOM],
    )
    def test_reconnecting_is_unhealthy(self, target_type: TargetType) -> None:
        t = _t(target_type)
        t.ingest_snapshot(_snap(WorkerRole.PRIMARY, WorkerState.RECONNECTING))
        h = aggregate_target_health(t)
        assert h.level is TargetHealthLevel.UNHEALTHY
        assert h.is_live is False

    @pytest.mark.parametrize(
        "target_type",
        [TargetType.TWITCH, TargetType.KICK, TargetType.VK_LIVE, TargetType.CUSTOM],
    )
    def test_no_snapshot_is_unhealthy(self, target_type: TargetType) -> None:
        t = _t(target_type)
        h = aggregate_target_health(t)
        assert h.level is TargetHealthLevel.UNHEALTHY
        assert h.healthy_roles == frozenset()
        assert h.unhealthy_roles == frozenset({WorkerRole.PRIMARY})


class TestYouTubeBackupAggregation:
    """ADR-0008 §`youtube`: primary OR backup."""

    def test_both_healthy_is_healthy(self) -> None:
        t = _t(TargetType.YOUTUBE, backup_enabled=True)
        t.ingest_snapshot(_snap(WorkerRole.PRIMARY, WorkerState.RUNNING))
        t.ingest_snapshot(_snap(WorkerRole.BACKUP, WorkerState.RUNNING))
        h = aggregate_target_health(t)
        assert h.level is TargetHealthLevel.HEALTHY
        assert h.unhealthy_roles == frozenset()

    def test_one_down_is_degraded(self) -> None:
        t = _t(TargetType.YOUTUBE, backup_enabled=True)
        t.ingest_snapshot(_snap(WorkerRole.PRIMARY, WorkerState.RUNNING))
        t.ingest_snapshot(_snap(WorkerRole.BACKUP, WorkerState.RECONNECTING))
        h = aggregate_target_health(t)
        assert h.level is TargetHealthLevel.DEGRADED
        assert h.healthy_roles == frozenset({WorkerRole.PRIMARY})
        assert h.unhealthy_roles == frozenset({WorkerRole.BACKUP})
        assert h.is_live is True  # OR-aggregation keeps the target live.

    def test_both_down_is_unhealthy(self) -> None:
        t = _t(TargetType.YOUTUBE, backup_enabled=True)
        t.ingest_snapshot(_snap(WorkerRole.PRIMARY, WorkerState.FAILED_OPEN))
        t.ingest_snapshot(_snap(WorkerRole.BACKUP, WorkerState.FAILED_OPEN))
        h = aggregate_target_health(t)
        assert h.level is TargetHealthLevel.UNHEALTHY
        assert h.is_live is False

    def test_youtube_without_backup_uses_primary_only(self) -> None:
        t = _t(TargetType.YOUTUBE, backup_enabled=False)
        t.ingest_snapshot(_snap(WorkerRole.PRIMARY, WorkerState.RUNNING))
        # BACKUP snapshot exists but isn't expected ⇒ ignored by aggregation.
        t.ingest_snapshot(_snap(WorkerRole.BACKUP, WorkerState.FAILED_OPEN))
        h = aggregate_target_health(t)
        assert h.level is TargetHealthLevel.HEALTHY
        assert h.unhealthy_roles == frozenset()


class TestIsLiveProperty:
    def test_healthy_is_live(self) -> None:
        t = _t()
        t.ingest_snapshot(_snap(WorkerRole.PRIMARY, WorkerState.RUNNING))
        assert aggregate_target_health(t).is_live is True

    def test_degraded_is_live(self) -> None:
        t = _t(TargetType.YOUTUBE, backup_enabled=True)
        t.ingest_snapshot(_snap(WorkerRole.PRIMARY, WorkerState.RUNNING))
        t.ingest_snapshot(_snap(WorkerRole.BACKUP, WorkerState.STOPPING))
        assert aggregate_target_health(t).is_live is True

    def test_unhealthy_is_not_live(self) -> None:
        t = _t()
        t.ingest_snapshot(_snap(WorkerRole.PRIMARY, WorkerState.STOPPING))
        assert aggregate_target_health(t).is_live is False
