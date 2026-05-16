"""Domain exception class tests — base/subclass routing + message shape."""

from __future__ import annotations

import pytest
from app.domain.errors import (
    IllegalRunStateTransitionError,
    IllegalStateTransitionError,
    IllegalWorkerStateTransitionError,
)
from app.domain.run_state import RunAction, RunState
from app.domain.worker_state import WorkerAction, WorkerState


class TestExceptionHierarchy:
    def test_run_subclass_is_caught_by_base(self) -> None:
        with pytest.raises(IllegalStateTransitionError):
            raise IllegalRunStateTransitionError(
                state=RunState.OFFLINE, action=RunAction.OBS_PUBLISH_BEGAN
            )

    def test_worker_subclass_is_caught_by_base(self) -> None:
        with pytest.raises(IllegalStateTransitionError):
            raise IllegalWorkerStateTransitionError(
                state=WorkerState.IDLE, action=WorkerAction.PROCESS_HEALTHY
            )

    def test_subclasses_do_not_overlap(self) -> None:
        run_exc = IllegalRunStateTransitionError(
            state=RunState.OFFLINE, action=RunAction.STOP_REQUESTED
        )
        worker_exc = IllegalWorkerStateTransitionError(
            state=WorkerState.IDLE, action=WorkerAction.STOP_REQUESTED
        )
        assert isinstance(run_exc, IllegalRunStateTransitionError)
        assert not isinstance(run_exc, IllegalWorkerStateTransitionError)
        assert isinstance(worker_exc, IllegalWorkerStateTransitionError)
        assert not isinstance(worker_exc, IllegalRunStateTransitionError)


class TestMessageShape:
    def test_run_message_includes_state_and_action(self) -> None:
        exc = IllegalRunStateTransitionError(
            state=RunState.OFFLINE, action=RunAction.OBS_PUBLISH_BEGAN
        )
        text = str(exc)
        assert "run" in text
        assert "offline" in text
        assert "obs_publish_began" in text

    def test_worker_message_includes_state_and_action(self) -> None:
        exc = IllegalWorkerStateTransitionError(
            state=WorkerState.IDLE, action=WorkerAction.PROCESS_HEALTHY
        )
        text = str(exc)
        assert "worker" in text
        assert "idle" in text
        assert "process_healthy" in text

    def test_machine_attribute(self) -> None:
        run_exc = IllegalRunStateTransitionError(
            state=RunState.OFFLINE, action=RunAction.STOP_REQUESTED
        )
        worker_exc = IllegalWorkerStateTransitionError(
            state=WorkerState.IDLE, action=WorkerAction.STOP_REQUESTED
        )
        assert run_exc.machine == "run"
        assert worker_exc.machine == "worker"

    def test_state_and_action_attributes_carry_enums(self) -> None:
        exc = IllegalRunStateTransitionError(
            state=RunState.OFFLINE, action=RunAction.OBS_PUBLISH_BEGAN
        )
        assert exc.state is RunState.OFFLINE
        assert exc.action is RunAction.OBS_PUBLISH_BEGAN
