"""Worker circuit breaker policy per ADR-0003.

Separated from the WorkerState machine on purpose (Phase 4 design memo Q3):
the breaker takes wall-clock time as input, the state machine does not.
The state machine consumes the breaker's `BreakerVerdict` enum.

Contract (ADR-0003):
- After N (default 30) failed attempts within a 5-minute sliding window,
  `should_open()` returns OPEN. The supervisor routes the worker to
  `WorkerState.FAILED_OPEN`.
- After `healthy_reset_after` (default 60 s) of *uninterrupted* healthy
  running, the failure count resets. Any failure between `record_healthy`
  calls re-arms the timer.
- `reset()` is the user-initiated "Retry now" path.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum


class BreakerVerdict(str, Enum):
    CLOSED = "closed"
    OPEN = "open"


@dataclass(frozen=True, slots=True)
class CircuitBreakerPolicy:
    max_failures: int = 30
    window: timedelta = timedelta(minutes=5)
    healthy_reset_after: timedelta = timedelta(seconds=60)


@dataclass(slots=True)
class CircuitBreaker:
    """Mutable breaker. One instance per Worker; the supervisor owns it."""

    policy: CircuitBreakerPolicy = field(default_factory=CircuitBreakerPolicy)
    _failures: deque[datetime] = field(default_factory=deque)
    _last_healthy_since: datetime | None = None

    def record_failure(self, now: datetime) -> None:
        self._evict_old(now)
        self._failures.append(now)
        self._last_healthy_since = None

    def record_healthy(self, now: datetime) -> None:
        """Record an observation that the worker is in `running` state.

        Call this on every healthy heartbeat (not only on first). When
        the *first* healthy observation since the last failure crosses
        the policy's `healthy_reset_after` boundary, the failure count
        is cleared.
        """
        if self._last_healthy_since is None:
            self._last_healthy_since = now
            return
        if now - self._last_healthy_since >= self.policy.healthy_reset_after:
            self._failures.clear()

    def should_open(self, now: datetime) -> BreakerVerdict:
        self._evict_old(now)
        return (
            BreakerVerdict.OPEN
            if len(self._failures) >= self.policy.max_failures
            else BreakerVerdict.CLOSED
        )

    def failure_count_in_window(self, now: datetime) -> int:
        """For UI surfacing — '27 of 30 attempts failed in the last 5 min'."""
        self._evict_old(now)
        return len(self._failures)

    def reset(self) -> None:
        """User-initiated 'Retry now' — clears failures and the healthy timer."""
        self._failures.clear()
        self._last_healthy_since = None

    def _evict_old(self, now: datetime) -> None:
        cutoff = now - self.policy.window
        while self._failures and self._failures[0] < cutoff:
            self._failures.popleft()
