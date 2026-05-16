"""WorkerState transition machine tests, including the BreakerVerdict-routed reconnect."""

from __future__ import annotations

import pytest
from app.domain.circuit_breaker import BreakerVerdict
from app.domain.errors import (
    IllegalStateTransitionError,
    IllegalWorkerStateTransitionError,
)
from app.domain.worker_state import (
    WorkerAction,
    WorkerState,
    is_legal,
    legal_actions,
    transition,
)

LEGAL_TABLE: list[tuple[WorkerState, WorkerAction, WorkerState]] = [
    (WorkerState.IDLE, WorkerAction.START, WorkerState.STARTING),
    (WorkerState.STARTING, WorkerAction.PROCESS_HEALTHY, WorkerState.RUNNING),
    (WorkerState.STARTING, WorkerAction.PROCESS_EXITED, WorkerState.RECONNECTING),
    (WorkerState.STARTING, WorkerAction.STOP_REQUESTED, WorkerState.STOPPING),
    (WorkerState.RUNNING, WorkerAction.PROCESS_EXITED, WorkerState.RECONNECTING),
    (WorkerState.RUNNING, WorkerAction.STOP_REQUESTED, WorkerState.STOPPING),
    (WorkerState.RECONNECTING, WorkerAction.PROCESS_HEALTHY, WorkerState.RUNNING),
    (WorkerState.RECONNECTING, WorkerAction.STOP_REQUESTED, WorkerState.STOPPING),
    (WorkerState.STOPPING, WorkerAction.STOPPED, WorkerState.IDLE),
    (WorkerState.FAILED_OPEN, WorkerAction.USER_RESET, WorkerState.STARTING),
    (WorkerState.FAILED_OPEN, WorkerAction.STOP_REQUESTED, WorkerState.STOPPING),
]


class TestLegalTransitions:
    @pytest.mark.parametrize(("state", "action", "expected"), LEGAL_TABLE)
    def test_each_legal_transition(
        self,
        state: WorkerState,
        action: WorkerAction,
        expected: WorkerState,
    ) -> None:
        assert transition(state, action) is expected


class TestIllegalTransitions:
    def test_every_unlisted_table_pair_raises(self) -> None:
        legal_pairs = {(s, a) for s, a, _ in LEGAL_TABLE}
        for state in WorkerState:
            for action in WorkerAction:
                if action is WorkerAction.RECONNECT:
                    continue  # tested separately — needs verdict
                if (state, action) in legal_pairs:
                    continue
                with pytest.raises(IllegalWorkerStateTransitionError):
                    transition(state, action)

    def test_caught_by_base_class(self) -> None:
        with pytest.raises(IllegalStateTransitionError):
            transition(WorkerState.IDLE, WorkerAction.PROCESS_HEALTHY)

    def test_failed_open_rejects_process_healthy(self) -> None:
        # Cannot recover from failed_open without an explicit USER_RESET.
        with pytest.raises(IllegalWorkerStateTransitionError):
            transition(WorkerState.FAILED_OPEN, WorkerAction.PROCESS_HEALTHY)

    def test_idle_rejects_stop_requested(self) -> None:
        # Cannot stop a never-started worker.
        with pytest.raises(IllegalWorkerStateTransitionError):
            transition(WorkerState.IDLE, WorkerAction.STOP_REQUESTED)


class TestReconnectVerdictRouting:
    def test_open_routes_to_failed_open(self) -> None:
        new = transition(
            WorkerState.RECONNECTING,
            WorkerAction.RECONNECT,
            verdict=BreakerVerdict.OPEN,
        )
        assert new is WorkerState.FAILED_OPEN

    def test_closed_stays_in_reconnecting(self) -> None:
        new = transition(
            WorkerState.RECONNECTING,
            WorkerAction.RECONNECT,
            verdict=BreakerVerdict.CLOSED,
        )
        assert new is WorkerState.RECONNECTING

    def test_reconnect_without_verdict_raises_typed_exception(self) -> None:
        # Unifies the failure surface: a caller catching
        # IllegalStateTransitionError gets all three failure modes.
        with pytest.raises(IllegalWorkerStateTransitionError) as ei:
            transition(WorkerState.RECONNECTING, WorkerAction.RECONNECT)
        assert ei.value.state is WorkerState.RECONNECTING
        assert ei.value.action is WorkerAction.RECONNECT

    def test_reconnect_from_non_reconnecting_state_is_illegal(self) -> None:
        for state in WorkerState:
            if state is WorkerState.RECONNECTING:
                continue
            with pytest.raises(IllegalWorkerStateTransitionError):
                transition(state, WorkerAction.RECONNECT, verdict=BreakerVerdict.CLOSED)


class TestLegalActions:
    def test_idle_only_allows_start(self) -> None:
        assert legal_actions(WorkerState.IDLE) == frozenset({WorkerAction.START})

    def test_reconnecting_includes_reconnect(self) -> None:
        actions = legal_actions(WorkerState.RECONNECTING)
        assert WorkerAction.RECONNECT in actions
        assert WorkerAction.PROCESS_HEALTHY in actions
        assert WorkerAction.STOP_REQUESTED in actions

    def test_failed_open_only_resets_or_stops(self) -> None:
        assert legal_actions(WorkerState.FAILED_OPEN) == frozenset(
            {WorkerAction.USER_RESET, WorkerAction.STOP_REQUESTED}
        )

    def test_stopping_only_allows_stopped(self) -> None:
        assert legal_actions(WorkerState.STOPPING) == frozenset({WorkerAction.STOPPED})

    def test_legal_actions_consistent_with_is_legal(self) -> None:
        for state in WorkerState:
            for action in WorkerAction:
                assert is_legal(state, action) is (action in legal_actions(state))
