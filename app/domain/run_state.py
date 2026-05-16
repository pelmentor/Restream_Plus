"""System-wide RunState machine per ADR-0003.

Pure-functional: `transition(state, action) -> new_state`. The state IS
data (it's persisted in `sessions_history` and broadcast over WebSocket),
so coupling state-as-data to state-as-method would warp serialization.
The transition table is exhaustively enumerable for property tests.
"""

from __future__ import annotations

from enum import Enum
from typing import Final

from app.domain.errors import IllegalRunStateTransitionError


class RunState(str, Enum):
    OFFLINE = "offline"
    STARTING = "starting"
    ARMED = "armed"
    LIVE = "live"
    STOPPING = "stopping"
    ERROR = "error"


class RunAction(str, Enum):
    START_REQUESTED = "start_requested"
    WORKERS_SPAWNED = "workers_spawned"
    OBS_PUBLISH_BEGAN = "obs_publish_began"
    OBS_PUBLISH_ENDED = "obs_publish_ended"
    STOP_REQUESTED = "stop_requested"
    WORKERS_DRAINED = "workers_drained"
    UNRECOVERABLE_FAILED = "unrecoverable_failed"
    ERROR_CLEARED = "error_cleared"


_TRANSITIONS: Final[dict[tuple[RunState, RunAction], RunState]] = {
    (RunState.OFFLINE, RunAction.START_REQUESTED): RunState.STARTING,
    (RunState.STARTING, RunAction.WORKERS_SPAWNED): RunState.ARMED,
    (RunState.ARMED, RunAction.OBS_PUBLISH_BEGAN): RunState.LIVE,
    (RunState.LIVE, RunAction.OBS_PUBLISH_ENDED): RunState.ARMED,
    (RunState.STARTING, RunAction.STOP_REQUESTED): RunState.STOPPING,
    (RunState.ARMED, RunAction.STOP_REQUESTED): RunState.STOPPING,
    (RunState.LIVE, RunAction.STOP_REQUESTED): RunState.STOPPING,
    (RunState.STOPPING, RunAction.WORKERS_DRAINED): RunState.OFFLINE,
    (RunState.ERROR, RunAction.ERROR_CLEARED): RunState.OFFLINE,
    # UNRECOVERABLE_FAILED is legal from any non-terminal active state.
    # ERROR is terminal-pending-clear; OFFLINE has nothing to fail.
    (RunState.STARTING, RunAction.UNRECOVERABLE_FAILED): RunState.ERROR,
    (RunState.ARMED, RunAction.UNRECOVERABLE_FAILED): RunState.ERROR,
    (RunState.LIVE, RunAction.UNRECOVERABLE_FAILED): RunState.ERROR,
    (RunState.STOPPING, RunAction.UNRECOVERABLE_FAILED): RunState.ERROR,
}


def transition(state: RunState, action: RunAction) -> RunState:
    """Compute the next RunState. Raises `IllegalRunStateTransition` if not legal."""
    try:
        return _TRANSITIONS[(state, action)]
    except KeyError as exc:
        raise IllegalRunStateTransitionError(state=state, action=action) from exc


def is_legal(state: RunState, action: RunAction) -> bool:
    """Pre-check for callers that want to branch without catching."""
    return (state, action) in _TRANSITIONS


def legal_actions(state: RunState) -> frozenset[RunAction]:
    """All actions accepted from `state`. Useful for UI affordance generation."""
    return frozenset(action for (s, action) in _TRANSITIONS if s is state)
