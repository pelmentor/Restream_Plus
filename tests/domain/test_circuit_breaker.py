"""CircuitBreaker tests — sliding window, healthy reset, property-based."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.domain.circuit_breaker import (
    BreakerVerdict,
    CircuitBreaker,
    CircuitBreakerPolicy,
)
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

T0 = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)


def _at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


class TestPolicyDefaults:
    def test_defaults_match_adr_0003(self) -> None:
        policy = CircuitBreakerPolicy()
        assert policy.max_failures == 30
        assert policy.window == timedelta(minutes=5)
        assert policy.healthy_reset_after == timedelta(seconds=60)


class TestVerdictBasic:
    def test_fresh_breaker_is_closed(self) -> None:
        cb = CircuitBreaker(policy=CircuitBreakerPolicy())
        assert cb.should_open(_at(0)) is BreakerVerdict.CLOSED

    def test_opens_after_max_failures(self) -> None:
        cb = CircuitBreaker(policy=CircuitBreakerPolicy(max_failures=3))
        cb.record_failure(_at(0))
        cb.record_failure(_at(1))
        assert cb.should_open(_at(1)) is BreakerVerdict.CLOSED
        cb.record_failure(_at(2))
        assert cb.should_open(_at(2)) is BreakerVerdict.OPEN

    def test_failures_outside_window_evicted(self) -> None:
        cb = CircuitBreaker(
            policy=CircuitBreakerPolicy(max_failures=2, window=timedelta(seconds=10))
        )
        cb.record_failure(_at(0))
        cb.record_failure(_at(1))
        # Both inside window → OPEN.
        assert cb.should_open(_at(2)) is BreakerVerdict.OPEN
        # Both have aged out → CLOSED again.
        assert cb.should_open(_at(20)) is BreakerVerdict.CLOSED

    def test_failure_count_reflects_window(self) -> None:
        cb = CircuitBreaker(
            policy=CircuitBreakerPolicy(max_failures=10, window=timedelta(seconds=10))
        )
        cb.record_failure(_at(0))
        cb.record_failure(_at(5))
        cb.record_failure(_at(8))
        assert cb.failure_count_in_window(_at(8)) == 3
        # At t=11, t=0 has aged out (cutoff is t=1).
        assert cb.failure_count_in_window(_at(11)) == 2


class TestHealthyReset:
    def test_healthy_for_full_duration_clears_failures(self) -> None:
        cb = CircuitBreaker(
            policy=CircuitBreakerPolicy(
                max_failures=5,
                window=timedelta(minutes=10),
                healthy_reset_after=timedelta(seconds=60),
            )
        )
        for i in range(4):
            cb.record_failure(_at(i))
        # Healthy started at t=10, still under reset window:
        cb.record_healthy(_at(10))
        cb.record_healthy(_at(30))
        assert cb.failure_count_in_window(_at(30)) == 4
        # Healthy crosses 60s threshold:
        cb.record_healthy(_at(70))
        assert cb.failure_count_in_window(_at(70)) == 0

    def test_failure_during_healthy_window_restarts_timer(self) -> None:
        cb = CircuitBreaker(
            policy=CircuitBreakerPolicy(
                max_failures=10,
                window=timedelta(minutes=10),
                healthy_reset_after=timedelta(seconds=60),
            )
        )
        cb.record_failure(_at(0))
        cb.record_failure(_at(1))
        cb.record_healthy(_at(10))
        cb.record_failure(_at(20))  # restart timer
        cb.record_healthy(_at(30))
        # 30 - 30 = 0 < 60 — failures still present.
        assert cb.failure_count_in_window(_at(40)) == 3

    def test_first_healthy_alone_does_not_clear(self) -> None:
        cb = CircuitBreaker(policy=CircuitBreakerPolicy(max_failures=10))
        cb.record_failure(_at(0))
        cb.record_healthy(_at(1))
        assert cb.failure_count_in_window(_at(1)) == 1


class TestUserReset:
    def test_reset_clears_failures(self) -> None:
        cb = CircuitBreaker(policy=CircuitBreakerPolicy(max_failures=2))
        cb.record_failure(_at(0))
        cb.record_failure(_at(1))
        assert cb.should_open(_at(1)) is BreakerVerdict.OPEN
        cb.reset()
        assert cb.should_open(_at(1)) is BreakerVerdict.CLOSED
        assert cb.failure_count_in_window(_at(1)) == 0

    def test_reset_clears_healthy_timer(self) -> None:
        cb = CircuitBreaker(policy=CircuitBreakerPolicy(max_failures=10))
        cb.record_healthy(_at(0))
        cb.reset()
        cb.record_healthy(_at(120))
        # Reset wiped the prior healthy_since; this is now the new first.
        cb.record_failure(_at(120))
        assert cb.failure_count_in_window(_at(120)) == 1


class TestPropertyBased:
    """ADR-0003: 'after N failed attempts within the window → OPEN, stays OPEN.'"""

    @given(
        n_extra=st.integers(min_value=0, max_value=20),
        spacing_ms=st.integers(min_value=1, max_value=2000),
    )
    @settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_at_least_max_failures_within_window_opens(
        self,
        n_extra: int,
        spacing_ms: int,
    ) -> None:
        policy = CircuitBreakerPolicy(max_failures=5, window=timedelta(seconds=300))
        spacing_seconds = spacing_ms / 1000
        # Encode the test invariant explicitly: every failure must land
        # inside `window`. If the parameter ranges later widen, hypothesis
        # rejects examples that violate the assumption rather than
        # silently producing false-negative breaker reports.
        total_span_seconds = (policy.max_failures + n_extra - 1) * spacing_seconds
        assume(total_span_seconds < policy.window.total_seconds())
        cb = CircuitBreaker(policy=policy)
        for i in range(policy.max_failures + n_extra):
            cb.record_failure(_at(i * spacing_seconds))
        last_t = _at(total_span_seconds)
        assert cb.should_open(last_t) is BreakerVerdict.OPEN

    @given(n=st.integers(min_value=0, max_value=29))
    def test_below_threshold_stays_closed(self, n: int) -> None:
        policy = CircuitBreakerPolicy(max_failures=30)
        cb = CircuitBreaker(policy=policy)
        for i in range(n):
            cb.record_failure(_at(i))
        assert cb.should_open(_at(n)) is BreakerVerdict.CLOSED

    @given(
        seconds_old=st.integers(min_value=301, max_value=3600),
        n=st.integers(min_value=30, max_value=60),
    )
    def test_old_failures_evicted_from_window(
        self,
        seconds_old: int,
        n: int,
    ) -> None:
        # 30+ failures all older than the 5-minute window → CLOSED.
        policy = CircuitBreakerPolicy(max_failures=30, window=timedelta(minutes=5))
        cb = CircuitBreaker(policy=policy)
        for i in range(n):
            cb.record_failure(_at(-seconds_old - i))
        assert cb.should_open(_at(0)) is BreakerVerdict.CLOSED


@pytest.mark.parametrize("verdict", list(BreakerVerdict))
def test_verdict_enum_values(verdict: BreakerVerdict) -> None:
    assert verdict.value in {"closed", "open"}
