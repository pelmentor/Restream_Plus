"""Per-Worker WorkerState machine + WorkerRole + WorkerSnapshot.

`WorkerSnapshot` is the *value object* the supervisor (Phase 5) hands
to the domain layer. It carries everything `Target.ui_state()` and
`aggregate_target_health()` need, without dragging asyncio or subprocess
into the domain.

State transitions are pure-functional. The reconnect path takes a
`BreakerVerdict` (from `circuit_breaker.py`) so the routing decision
(reconnecting vs. failed_open) is made by composing two pure functions
rather than by injecting a clock into the state machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Final

from app.domain.circuit_breaker import BreakerVerdict
from app.domain.errors import IllegalWorkerStateTransitionError


class WorkerRole(str, Enum):
    PRIMARY = "primary"
    BACKUP = "backup"


class WorkerState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    RECONNECTING = "reconnecting"
    FAILED_OPEN = "failed_open"
    STOPPING = "stopping"


class WorkerAction(str, Enum):
    START = "start"
    PROCESS_HEALTHY = "process_healthy"
    PROCESS_EXITED = "process_exited"
    RECONNECT = "reconnect"
    STOP_REQUESTED = "stop_requested"
    STOPPED = "stopped"
    USER_RESET = "user_reset"


_TRANSITIONS: Final[dict[tuple[WorkerState, WorkerAction], WorkerState]] = {
    (WorkerState.IDLE, WorkerAction.START): WorkerState.STARTING,
    (WorkerState.STARTING, WorkerAction.PROCESS_HEALTHY): WorkerState.RUNNING,
    (WorkerState.STARTING, WorkerAction.PROCESS_EXITED): WorkerState.RECONNECTING,
    (WorkerState.STARTING, WorkerAction.STOP_REQUESTED): WorkerState.STOPPING,
    (WorkerState.RUNNING, WorkerAction.PROCESS_EXITED): WorkerState.RECONNECTING,
    (WorkerState.RUNNING, WorkerAction.STOP_REQUESTED): WorkerState.STOPPING,
    (WorkerState.RECONNECTING, WorkerAction.PROCESS_HEALTHY): WorkerState.RUNNING,
    (WorkerState.RECONNECTING, WorkerAction.STOP_REQUESTED): WorkerState.STOPPING,
    (WorkerState.STOPPING, WorkerAction.STOPPED): WorkerState.IDLE,
    (WorkerState.FAILED_OPEN, WorkerAction.USER_RESET): WorkerState.STARTING,
    (WorkerState.FAILED_OPEN, WorkerAction.STOP_REQUESTED): WorkerState.STOPPING,
}


def transition(
    state: WorkerState,
    action: WorkerAction,
    *,
    verdict: BreakerVerdict | None = None,
) -> WorkerState:
    """Compute the next WorkerState.

    `verdict` is required iff `action is WorkerAction.RECONNECT`. It
    routes the worker either back through reconnect (CLOSED) or into
    failed_open (OPEN). Every other action ignores `verdict`.

    Every refusal — wrong (state, action) pair OR RECONNECT without a
    verdict — raises `IllegalWorkerStateTransitionError`, so call sites
    can `except IllegalStateTransitionError` once and cover all paths.
    """
    if action is WorkerAction.RECONNECT:
        if state is not WorkerState.RECONNECTING or verdict is None:
            raise IllegalWorkerStateTransitionError(state=state, action=action)
        return (
            WorkerState.FAILED_OPEN if verdict is BreakerVerdict.OPEN else WorkerState.RECONNECTING
        )
    try:
        return _TRANSITIONS[(state, action)]
    except KeyError as exc:
        raise IllegalWorkerStateTransitionError(state=state, action=action) from exc


def is_legal(state: WorkerState, action: WorkerAction) -> bool:
    if action is WorkerAction.RECONNECT:
        return state is WorkerState.RECONNECTING
    return (state, action) in _TRANSITIONS


def legal_actions(state: WorkerState) -> frozenset[WorkerAction]:
    actions = {action for (s, action) in _TRANSITIONS if s is state}
    if state is WorkerState.RECONNECTING:
        actions.add(WorkerAction.RECONNECT)
    return frozenset(actions)


@dataclass(frozen=True, slots=True)
class WorkerSnapshot:
    """Domain-layer view of a Worker's current state.

    Phase 5's concrete `Worker` (FFmpegWorker for v1) projects into this
    on every event. The Target aggregate stores at most one snapshot per
    role and consults it when computing UI state and health.

    `last_error` arrives already redacted (RedactionSink in Phase 5).
    `breaker_failures_in_window` is the count from `CircuitBreaker.
    failure_count_in_window(now)` at the moment the snapshot was built;
    it's a UI-surface number, not authoritative for routing decisions.
    """

    role: WorkerRole
    state: WorkerState
    last_event_at: datetime
    last_error: str | None
    breaker_failures_in_window: int
