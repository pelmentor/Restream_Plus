"""Domain-layer state-machine exceptions.

Per Phase 4 design memo Q2: a single base class plus two typed subclasses,
so call sites can `except IllegalStateTransition` once at the boundary
*or* narrow on a specific machine. The exception fields carry only enum
values — no URLs, no keys, no usernames — so the structlog redaction
processor passes them through unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.domain.run_state import RunAction, RunState
    from app.domain.worker_state import WorkerAction, WorkerState


class IllegalStateTransitionError(Exception):
    """Base class. Catch this at the layer boundary; narrow below for routing."""

    machine: str

    def __init__(self, *, machine: str, state_value: str, action_value: str) -> None:
        super().__init__(
            f"illegal {machine} transition: {state_value} -[{action_value}]-> ?",
        )
        self.machine = machine
        self.state_value = state_value
        self.action_value = action_value


class IllegalRunStateTransitionError(IllegalStateTransitionError):
    """Run-state machine refused a transition. Usually a control-plane bug."""

    state: RunState
    action: RunAction

    def __init__(self, *, state: RunState, action: RunAction) -> None:
        super().__init__(
            machine="run",
            state_value=state.value,
            action_value=action.value,
        )
        self.state = state
        self.action = action


class IllegalWorkerStateTransitionError(IllegalStateTransitionError):
    """Worker-state machine refused a transition. The supervisor stops this
    Worker; the run continues."""

    state: WorkerState
    action: WorkerAction

    def __init__(self, *, state: WorkerState, action: WorkerAction) -> None:
        super().__init__(
            machine="worker",
            state_value=state.value,
            action_value=action.value,
        )
        self.state = state
        self.action = action
