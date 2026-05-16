"""Domain layer — pure-Python state machines and value objects.

Import rule (enforced by `tests/domain/test_no_outward_imports.py`):
modules under `app.domain` may import from stdlib, pydantic, and other
`app.domain` modules. They MUST NOT import from `app.repositories`,
`app.db`, `app.crypto`, `app.auth`, `asyncio`, or `subprocess`.
"""

from app.domain.circuit_breaker import (
    BreakerVerdict,
    CircuitBreaker,
    CircuitBreakerPolicy,
)
from app.domain.credential_policy import CredentialLifetime, lifetime_for
from app.domain.errors import (
    IllegalRunStateTransitionError,
    IllegalStateTransitionError,
    IllegalWorkerStateTransitionError,
)
from app.domain.health import (
    TargetHealth,
    TargetHealthLevel,
    aggregate_target_health,
)
from app.domain.run_state import (
    RunAction,
    RunState,
)
from app.domain.run_state import (
    is_legal as run_action_is_legal,
)
from app.domain.run_state import (
    legal_actions as legal_run_actions,
)
from app.domain.run_state import (
    transition as run_transition,
)
from app.domain.target import Target, TargetUiState, WorkerSet, compute_ui_state
from app.domain.target_types import (
    TARGET_TYPE_SPECS,
    FormField,
    TargetType,
    TargetTypeSpec,
    spec_for,
)
from app.domain.worker_state import (
    WorkerAction,
    WorkerRole,
    WorkerSnapshot,
    WorkerState,
)
from app.domain.worker_state import (
    is_legal as worker_action_is_legal,
)
from app.domain.worker_state import (
    legal_actions as legal_worker_actions,
)
from app.domain.worker_state import (
    transition as worker_transition,
)

__all__ = [
    "TARGET_TYPE_SPECS",
    "BreakerVerdict",
    "CircuitBreaker",
    "CircuitBreakerPolicy",
    "CredentialLifetime",
    "FormField",
    "IllegalRunStateTransitionError",
    "IllegalStateTransitionError",
    "IllegalWorkerStateTransitionError",
    "RunAction",
    "RunState",
    "Target",
    "TargetHealth",
    "TargetHealthLevel",
    "TargetType",
    "TargetTypeSpec",
    "TargetUiState",
    "WorkerAction",
    "WorkerRole",
    "WorkerSet",
    "WorkerSnapshot",
    "WorkerState",
    "aggregate_target_health",
    "compute_ui_state",
    "legal_run_actions",
    "legal_worker_actions",
    "lifetime_for",
    "run_action_is_legal",
    "run_transition",
    "spec_for",
    "worker_action_is_legal",
    "worker_transition",
]
