"""Target aggregate + WorkerSet + TargetUiState.

Phase 4 design memo Q4-Q5: `Target` is mutable only through
`ingest_snapshot`; everything else is set at construction. `ui_state()`
is computed from the current snapshots via an explicit precedence ladder
in `compute_ui_state` so the precedence reads top-to-bottom as the spec.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.domain.target_types import TargetType
from app.domain.worker_state import WorkerRole, WorkerSnapshot, WorkerState


class TargetUiState(str, Enum):
    DISABLED = "disabled"
    DISABLED_MISCONFIGURED = "disabled_misconfigured"
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"
    ERRORED = "errored"
    FAILED_OPEN = "failed_open"


@dataclass(slots=True)
class WorkerSet:
    """Collection of role-keyed worker snapshots.

    Stores the most recent snapshot per role. The aggregate may carry
    snapshots for roles outside `Target.expected_worker_roles()` if a
    config change races the supervisor's worker tear-down; consumers
    that care about expected-role-only health should use
    `aggregate_target_health` (in `app.domain.health`) rather than
    iterating `snapshots` directly.
    """

    snapshots: dict[WorkerRole, WorkerSnapshot] = field(default_factory=dict)

    def role(self, role: WorkerRole) -> WorkerSnapshot | None:
        return self.snapshots.get(role)


@dataclass(slots=True)
class Target:
    """Domain aggregate. Built from `TargetDTO` + optional `CredentialDTO`
    via `app.repositories._converters.target_dto_to_domain`.

    `has_active_credential` is the *fact* that a credential row exists
    for this target. The aggregate never carries the secret itself.
    """

    id: str
    type: TargetType
    label: str
    url: str
    enabled: bool
    backup_enabled: bool
    has_active_credential: bool
    worker_set: WorkerSet = field(default_factory=WorkerSet)

    def ingest_snapshot(self, snapshot: WorkerSnapshot) -> None:
        self.worker_set.snapshots[snapshot.role] = snapshot

    def expected_worker_roles(self) -> frozenset[WorkerRole]:
        if self.type is TargetType.YOUTUBE and self.backup_enabled:
            return frozenset({WorkerRole.PRIMARY, WorkerRole.BACKUP})
        return frozenset({WorkerRole.PRIMARY})

    def ui_state(self) -> TargetUiState:
        return compute_ui_state(self)


def compute_ui_state(t: Target) -> TargetUiState:
    """Precedence ladder. Top wins. Read top-to-bottom as the spec.

    1. DISABLED — user-toggled off, regardless of config or runtime.
    2. DISABLED_MISCONFIGURED — required credential absent.
    3. IDLE — no snapshot has been ingested yet (worker hasn't run).
    4. FAILED_OPEN — any expected worker has tripped its breaker.
    5. STARTING — any expected worker is starting.
    6. RUNNING — every expected worker is running.
    7. DEGRADED — YouTube-with-backup, one of two workers down (other healthy).
    8. ERRORED — fallback (reconnecting / stopping / mixed unhealthy).
    """
    if not t.enabled:
        return TargetUiState.DISABLED

    if not _credentials_satisfied(t):
        return TargetUiState.DISABLED_MISCONFIGURED

    expected = t.expected_worker_roles()
    snapshots = {r: t.worker_set.role(r) for r in expected}

    if all(s is None for s in snapshots.values()):
        return TargetUiState.IDLE

    states = {r: (s.state if s is not None else WorkerState.IDLE) for r, s in snapshots.items()}

    if any(st is WorkerState.FAILED_OPEN for st in states.values()):
        return TargetUiState.FAILED_OPEN

    if any(st is WorkerState.STARTING for st in states.values()):
        return TargetUiState.STARTING

    healthy = {r for r, st in states.items() if st is WorkerState.RUNNING}
    if healthy == expected:
        return TargetUiState.RUNNING

    # YouTube-with-backup: one worker still delivering ⇒ degraded, not errored.
    # The OR-aggregation keeps the target "live"; UI signals lost redundancy.
    if t.type is TargetType.YOUTUBE and t.backup_enabled and healthy:
        return TargetUiState.DEGRADED

    return TargetUiState.ERRORED


def _credentials_satisfied(t: Target) -> bool:
    """A target is misconfigured iff it lacks an active credential.

    Phase 4 conservative rule: every type — VK included — requires
    `has_active_credential=True` to clear this gate. VK collects its
    per-session key on the Dashboard before START; until that paste
    persists a row, the tile sits in DISABLED_MISCONFIGURED.
    """
    return t.has_active_credential
