"""Run control: /api/run/start, /api/run/stop, /api/run/state.

Per Phase 6 design memo §Q11: start/stop are **async (202 Accepted)**
— the supervisor call is scheduled as a fire-and-forget task tracked
in `app.state.supervisor_tasks`. The client watches the WS for the
`run.state.changed` event to see the transition complete.

Illegal-state transitions are caught BEFORE the task is scheduled:
the supervisor's `start_run` / `stop_run` raise
`IllegalRunStateTransitionError` synchronously when the current
`RunState` does not accept the action, and the global exception
handler (registered in `app/api/errors.py`) translates that to a
409. We pre-check the legal transitions here so we can return 409
without scheduling a doomed task.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Request, status

from app.api.deps import EventBusDep, SupervisorDep
from app.api.errors import ErrorCode, http_exception
from app.api.schemas import (
    RunActionAcceptedResponse,
    RunStateView,
    TargetSnapshot,
    WorkerSnapshotView,
)
from app.auth.deps import AuthenticatedRequestDep, AuthStateDep
from app.domain.run_state import RunAction, is_legal
from app.domain.target import Target

if TYPE_CHECKING:  # pragma: no cover
    pass

_logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/run", tags=["run"])


_STOP_GRACE = timedelta(seconds=10)
"""Per-worker SIGTERM→SIGKILL window for shutdown via the REST stop
endpoint. Matches the supervisor's WORKER_GRACE default; bumped here
to 10s so a user who clicks STOP gets a comfortable drain window."""


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


# ----------------------------------------------------------------------
# GET /api/run/state — read current run + per-target snapshot
# ----------------------------------------------------------------------


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


# ----------------------------------------------------------------------
# POST /api/run/start — schedule start, return 202
# ----------------------------------------------------------------------


@router.post(
    "/start",
    response_model=RunActionAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_run(
    request: Request,
    _: AuthenticatedRequestDep,
    state: AuthStateDep,
    supervisor: SupervisorDep,
) -> RunActionAcceptedResponse:
    previous = supervisor.run_state()
    if not is_legal(previous, RunAction.START_REQUESTED):
        raise http_exception(ErrorCode.ILLEGAL_RUN_STATE, status.HTTP_409_CONFLICT)
    # Phase 9 — rotate-passphrase holds `state.rotation_lock` for the
    # duration of the re-wrap. A concurrent START in that window would
    # spawn workers that hold OLD `DerivedKeys` while the rotation
    # re-keys credentials underneath them. Refuse with 409 RUN_ACTIVE
    # (the canonical "stop the stream first" code; phase-9-design-memo
    # §I.3 step 2).
    if state.rotation_lock.locked():
        raise http_exception(ErrorCode.RUN_ACTIVE, status.HTTP_409_CONFLICT)
    _spawn_supervisor_task(request, supervisor.start_run(), name="run-start")
    return RunActionAcceptedResponse(previous_state=previous)


# ----------------------------------------------------------------------
# POST /api/run/stop — schedule stop, return 202
# ----------------------------------------------------------------------


@router.post(
    "/stop",
    response_model=RunActionAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def stop_run(
    request: Request,
    _: AuthenticatedRequestDep,
    supervisor: SupervisorDep,
) -> RunActionAcceptedResponse:
    previous = supervisor.run_state()
    if not is_legal(previous, RunAction.STOP_REQUESTED):
        raise http_exception(ErrorCode.ILLEGAL_RUN_STATE, status.HTTP_409_CONFLICT)
    _spawn_supervisor_task(
        request,
        supervisor.stop_run(grace=_STOP_GRACE, reason="user_stop"),
        name="run-stop",
    )
    return RunActionAcceptedResponse(previous_state=previous)


# ----------------------------------------------------------------------
# Helper: schedule + track supervisor-action tasks
# ----------------------------------------------------------------------


def _spawn_supervisor_task(request: Request, coro: Coroutine[Any, Any, Any], *, name: str) -> None:
    """Schedule a fire-and-forget supervisor coroutine, holding a strong
    reference on `app.state.supervisor_tasks` so the task isn't
    garbage-collected before it completes (asyncio's task ref is
    weak — same fix as `supervisor.py::_pending_notifies`).
    """
    bucket: set[asyncio.Task[Any]] | None = getattr(request.app.state, "supervisor_tasks", None)
    if bucket is None:
        bucket = set()
        request.app.state.supervisor_tasks = bucket
    task = asyncio.create_task(coro, name=name)
    bucket.add(task)
    task.add_done_callback(bucket.discard)


__all__ = ["router"]
