"""Target-level health aggregation per ADR-0008.

Distinct from `Target.ui_state()` — health answers "is data flowing?",
ui_state answers "what does the Dashboard tile show?". The supervisor's
RunState aggregator consumes `is_live`; the WebSocket payload consumes
the full `TargetHealth` dataclass so the UI can render "Backup ingest
is down" without re-fetching snapshots.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.domain.target import Target
from app.domain.worker_state import WorkerRole, WorkerState


class TargetHealthLevel(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True, slots=True)
class TargetHealth:
    level: TargetHealthLevel
    healthy_roles: frozenset[WorkerRole]
    unhealthy_roles: frozenset[WorkerRole]

    @property
    def is_live(self) -> bool:
        """True iff at least one expected worker is delivering."""
        return self.level is not TargetHealthLevel.UNHEALTHY


def aggregate_target_health(target: Target) -> TargetHealth:
    expected = target.expected_worker_roles()
    healthy: frozenset[WorkerRole] = frozenset(
        r
        for r in expected
        if (snap := target.worker_set.role(r)) is not None and snap.state is WorkerState.RUNNING
    )
    unhealthy = expected - healthy
    if not healthy:
        level = TargetHealthLevel.UNHEALTHY
    elif healthy == expected:
        level = TargetHealthLevel.HEALTHY
    else:
        level = TargetHealthLevel.DEGRADED
    return TargetHealth(level=level, healthy_roles=healthy, unhealthy_roles=unhealthy)
