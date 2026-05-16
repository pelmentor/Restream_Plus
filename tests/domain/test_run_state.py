"""RunState transition machine tests.

Every legal transition is tabulated; every illegal pair raises the
typed exception. Together these tests pin the entire state machine.
"""

from __future__ import annotations

import pytest
from app.domain.errors import (
    IllegalRunStateTransitionError,
    IllegalStateTransitionError,
)
from app.domain.run_state import (
    RunAction,
    RunState,
    is_legal,
    legal_actions,
    transition,
)

LEGAL: list[tuple[RunState, RunAction, RunState]] = [
    (RunState.OFFLINE, RunAction.START_REQUESTED, RunState.STARTING),
    (RunState.STARTING, RunAction.WORKERS_SPAWNED, RunState.ARMED),
    (RunState.ARMED, RunAction.OBS_PUBLISH_BEGAN, RunState.LIVE),
    (RunState.LIVE, RunAction.OBS_PUBLISH_ENDED, RunState.ARMED),
    (RunState.STARTING, RunAction.STOP_REQUESTED, RunState.STOPPING),
    (RunState.ARMED, RunAction.STOP_REQUESTED, RunState.STOPPING),
    (RunState.LIVE, RunAction.STOP_REQUESTED, RunState.STOPPING),
    (RunState.STOPPING, RunAction.WORKERS_DRAINED, RunState.OFFLINE),
    (RunState.ERROR, RunAction.ERROR_CLEARED, RunState.OFFLINE),
    (RunState.STARTING, RunAction.UNRECOVERABLE_FAILED, RunState.ERROR),
    (RunState.ARMED, RunAction.UNRECOVERABLE_FAILED, RunState.ERROR),
    (RunState.LIVE, RunAction.UNRECOVERABLE_FAILED, RunState.ERROR),
    (RunState.STOPPING, RunAction.UNRECOVERABLE_FAILED, RunState.ERROR),
]


class TestLegalTransitions:
    @pytest.mark.parametrize(("state", "action", "expected"), LEGAL)
    def test_each_legal_transition(
        self,
        state: RunState,
        action: RunAction,
        expected: RunState,
    ) -> None:
        assert transition(state, action) is expected

    def test_every_listed_pair_passes_is_legal(self) -> None:
        for state, action, _expected in LEGAL:
            assert is_legal(state, action) is True, (state, action)


class TestIllegalTransitions:
    def test_every_unlisted_pair_raises(self) -> None:
        legal_pairs = {(s, a) for s, a, _ in LEGAL}
        for state in RunState:
            for action in RunAction:
                if (state, action) in legal_pairs:
                    continue
                with pytest.raises(IllegalRunStateTransitionError) as ei:
                    transition(state, action)
                assert ei.value.state is state
                assert ei.value.action is action

    def test_offline_rejects_obs_publish_began(self) -> None:
        with pytest.raises(IllegalRunStateTransitionError):
            transition(RunState.OFFLINE, RunAction.OBS_PUBLISH_BEGAN)

    def test_error_only_clears(self) -> None:
        # The only legal action from ERROR is ERROR_CLEARED.
        for action in RunAction:
            if action is RunAction.ERROR_CLEARED:
                continue
            with pytest.raises(IllegalRunStateTransitionError):
                transition(RunState.ERROR, action)

    def test_offline_rejects_unrecoverable_failed(self) -> None:
        # Nothing's running; you can't fail unrecoverably.
        with pytest.raises(IllegalRunStateTransitionError):
            transition(RunState.OFFLINE, RunAction.UNRECOVERABLE_FAILED)

    def test_caught_by_base_class(self) -> None:
        with pytest.raises(IllegalStateTransitionError):
            transition(RunState.OFFLINE, RunAction.OBS_PUBLISH_BEGAN)


class TestLegalActions:
    def test_offline_only_allows_start(self) -> None:
        assert legal_actions(RunState.OFFLINE) == frozenset({RunAction.START_REQUESTED})

    def test_error_only_allows_error_cleared(self) -> None:
        assert legal_actions(RunState.ERROR) == frozenset({RunAction.ERROR_CLEARED})

    def test_starting_allows_three_actions(self) -> None:
        assert legal_actions(RunState.STARTING) == frozenset(
            {
                RunAction.WORKERS_SPAWNED,
                RunAction.STOP_REQUESTED,
                RunAction.UNRECOVERABLE_FAILED,
            }
        )

    def test_legal_actions_consistent_with_is_legal(self) -> None:
        for state in RunState:
            for action in RunAction:
                assert is_legal(state, action) is (action in legal_actions(state))


class TestStateGraphProperties:
    def test_offline_is_reachable_from_stopping(self) -> None:
        assert transition(RunState.STOPPING, RunAction.WORKERS_DRAINED) is RunState.OFFLINE

    def test_error_is_terminal_until_cleared(self) -> None:
        # ERROR_CLEARED is the only escape.
        new = transition(RunState.ERROR, RunAction.ERROR_CLEARED)
        assert new is RunState.OFFLINE
