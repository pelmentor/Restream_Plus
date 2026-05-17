"""Unit tests for `_event_to_envelope` + `_snapshot_to_view` in `app/api/ws.py`.

These are pure functions (no FastAPI, no WS, no DB), so we test them
directly. The end-to-end WS coverage lives in `test_ws.py`; this file
narrowly covers the projection from `BusEvent` payloads to the wire-
shaped `WsEnvelope`, especially the new `host.stats` envelope and the
`last_progress` field on the per-target snapshots.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.api.ws import _event_to_envelope, _snapshot_to_view
from app.domain.target import TargetUiState
from app.domain.worker_state import (
    WorkerProgressSnapshot,
    WorkerRole,
    WorkerSnapshot,
    WorkerState,
)
from app.fanout.event_bus import (
    BusEvent,
    HostCpuByTarget,
    HostStatsEvent,
    TargetSnapshotEvent,
)
from app.fanout.worker import WorkerId


def _now() -> datetime:
    return datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)


def test_snapshot_view_carries_last_progress_when_present() -> None:
    snap = WorkerSnapshot(
        role=WorkerRole.PRIMARY,
        state=WorkerState.RUNNING,
        last_event_at=_now(),
        last_error=None,
        breaker_failures_in_window=0,
        last_progress=WorkerProgressSnapshot(
            bitrate_kbps=4500.0,
            fps=29.97,
            drop_frames=2,
            speed=1.0,
            at=_now(),
        ),
    )
    view = _snapshot_to_view(snap)
    assert view.last_progress is not None
    assert view.last_progress.bitrate_kbps == 4500.0
    assert view.last_progress.drop_frames == 2


def test_snapshot_view_omits_last_progress_when_none() -> None:
    snap = WorkerSnapshot(
        role=WorkerRole.PRIMARY,
        state=WorkerState.IDLE,
        last_event_at=_now(),
        last_error=None,
        breaker_failures_in_window=0,
    )
    view = _snapshot_to_view(snap)
    assert view.last_progress is None


def test_target_snapshot_envelope_includes_last_progress_field() -> None:
    snap = WorkerSnapshot(
        role=WorkerRole.PRIMARY,
        state=WorkerState.RUNNING,
        last_event_at=_now(),
        last_error=None,
        breaker_failures_in_window=0,
        last_progress=WorkerProgressSnapshot(
            bitrate_kbps=6000.0,
            fps=30.0,
            drop_frames=0,
            speed=1.0,
            at=_now(),
        ),
    )
    event = BusEvent(
        at=_now(),
        monotonic_ns=0,
        target_id="t1",
        payload=TargetSnapshotEvent(
            target_id="t1",
            ui_state=TargetUiState.RUNNING,
            snapshots_by_role=(snap,),
        ),
    )
    envelope = _event_to_envelope(event)
    assert envelope is not None
    assert envelope.event == "target.snapshot"
    snapshots = envelope.data["snapshots_by_role"]
    assert isinstance(snapshots, tuple)
    first = snapshots[0]
    assert first["last_progress"] is not None
    assert first["last_progress"]["bitrate_kbps"] == 6000.0


def test_host_stats_envelope_serializes_to_wire_shape() -> None:
    event = BusEvent(
        at=_now(),
        monotonic_ns=0,
        target_id=None,
        payload=HostStatsEvent(
            cpu_total_pct=18.5,
            cpu_by_target=(
                HostCpuByTarget(
                    worker_id=WorkerId(target_id="t1", role=WorkerRole.PRIMARY),
                    cpu_pct=12.0,
                ),
                HostCpuByTarget(
                    worker_id=WorkerId(target_id="t2", role=WorkerRole.PRIMARY),
                    cpu_pct=6.5,
                ),
            ),
            rss_bytes=104_857_600,
            ingest_kbps=6000.0,
        ),
    )
    envelope = _event_to_envelope(event)
    assert envelope is not None
    assert envelope.event == "host.stats"
    data = envelope.data
    assert data["cpu_total_pct"] == 18.5
    assert data["rss_bytes"] == 104_857_600
    assert data["ingest_kbps"] == 6000.0
    cpu_list = data["cpu_by_target"]
    assert isinstance(cpu_list, list)
    assert cpu_list[0] == {
        "target_id": "t1",
        "role": "primary",
        "cpu_pct": 12.0,
    }


def test_host_stats_envelope_ingest_can_be_none() -> None:
    # ingest_kbps=None reaches the wire as JSON null so the SPA renders
    # "—" rather than "0 Mbps" (which would mislead the operator into
    # thinking the show is dark).
    event = BusEvent(
        at=_now(),
        monotonic_ns=0,
        target_id=None,
        payload=HostStatsEvent(
            cpu_total_pct=2.0,
            cpu_by_target=(),
            rss_bytes=4096,
            ingest_kbps=None,
        ),
    )
    envelope = _event_to_envelope(event)
    assert envelope is not None
    assert envelope.data["ingest_kbps"] is None
