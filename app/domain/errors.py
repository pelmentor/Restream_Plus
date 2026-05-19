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


class RunBlockedByRotationError(Exception):
    """`start_run` refused because a passphrase rotation is in flight.

    Distinct from `IllegalRunStateTransitionError` so the supervisor-task
    exception funnel (`_log_supervisor_task_exception` in `app/api/run.py`)
    can log at info-level rather than error-level: rotation contention
    is an expected outcome, not a bug. The REST handler maps this to
    `RUN_ACTIVE` (HTTP 409) via the same code path it uses for the
    advisory pre-check; the supervisor side IS the authoritative check
    (run.py advisory check has a TOCTOU window with rotate-passphrase).

    Hex Audit FG2-H3 (2026-05-18): the lock-ordering rule is now
    `supervisor._lock` BEFORE `state.rotation_lock`. The supervisor
    acquires rotation_lock under its own lock; rotate-passphrase
    acquires supervisor._lock first (via `acquire_state_lock()`) to
    check run-state, then grabs rotation_lock. Either ordering refuses
    the other cleanly.
    """
