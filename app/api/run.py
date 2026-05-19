"""Run state read endpoint: GET /api/run/state.

Per ADR-0015 auto-run-on-publish, the supervisor starts and stops in
response to OBS publish events arriving on the internal RTMP webhook
(prod: `/internal/rtmp/on_publish` + `/on_publish_done`; dev: MediaMTX
`/internal/mtx/auth` + `/internal/mtx/lifecycle`). There are no
manual /api/run/start or /api/run/stop endpoints — the dashboard
shows state and live stats; OBS publishing is the single trigger.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter

from app.api.deps import EventBusDep, SupervisorDep
from app.api.schemas import (
    RunStateView,
    TargetSnapshot,
    WorkerSnapshotView,
)
from app.auth.deps import AuthenticatedRequestDep
from app.domain.target import Target

_logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/run", tags=["run"])


def _project_target(t: Target) -> TargetSnapshot:
    snapshots = tuple(
        WorkerSnapshotView(
            role=s.role,
            state=s.state,
            last_event_at=s.last_event_at,
            last_error=s.last_error,
            breaker_failures_in_window=s.breaker_failures_in_window,
        )
        for role in t.expected_worker_roles()
        if (s := t.worker_set.role(role)) is not None
    )
    return TargetSnapshot(
        target_id=t.id,
        ui_state=t.ui_state(),
        snapshots_by_role=snapshots,
    )


@router.get("/state", response_model=RunStateView)
async def get_state(
    _: AuthenticatedRequestDep,
    supervisor: SupervisorDep,
    bus: EventBusDep,
) -> RunStateView:
    # Read-only projection from the supervisor's in-memory state.
    # `dropped_total` comes from the lifespan-attached EventBus directly
    # (security/code review M-5: do not reach into supervisor._bus —
    # that's a private attribute the supervisor uses for its own
    # bookkeeping and is None until `supervisor.run()` is awaited).
    targets = supervisor.targets()
    return RunStateView(
        run_state=supervisor.run_state(),
        targets=tuple(_project_target(t) for t in targets),
        heartbeat_age_seconds=supervisor.heartbeat_age().total_seconds(),
        dropped_total=bus.dropped_total,
        run_state_changed_at=supervisor.run_state_changed_at(),
    )


__all__ = ["router"]
