"""Backoff tests: deterministic with seeded RNG, bounded under jitter."""

from __future__ import annotations

import random
from datetime import timedelta

import pytest
from app.fanout.backoff import (
    BACKOFF_BASE,
    BACKOFF_CAP,
    JITTER_PCT,
    compute_backoff,
)
from hypothesis import given, settings
from hypothesis import strategies as st


class TestConstructor:
    def test_rejects_negative_attempt(self) -> None:
        with pytest.raises(ValueError, match="attempt must be"):
            compute_backoff(-1)

    def test_rejects_negative_jitter(self) -> None:
        with pytest.raises(ValueError, match="jitter_pct"):
            compute_backoff(0, jitter_pct=-0.1)


class TestPinnedConstants:
    def test_base_is_one_second(self) -> None:
        assert timedelta(seconds=1) == BACKOFF_BASE

    def test_cap_is_sixty_seconds(self) -> None:
        assert timedelta(seconds=60) == BACKOFF_CAP

    def test_jitter_is_twenty_pct(self) -> None:
        assert pytest.approx(0.20) == JITTER_PCT


class TestDeterministic:
    def test_seeded_rng_is_reproducible(self) -> None:
        rng_a = random.Random(42)
        rng_b = random.Random(42)
        for attempt in range(8):
            assert compute_backoff(attempt, rng=rng_a) == compute_backoff(attempt, rng=rng_b)

    def test_zero_jitter_is_exact(self) -> None:
        assert compute_backoff(0, jitter_pct=0.0) == timedelta(seconds=1)
        assert compute_backoff(1, jitter_pct=0.0) == timedelta(seconds=2)
        assert compute_backoff(2, jitter_pct=0.0) == timedelta(seconds=4)
        assert compute_backoff(8, jitter_pct=0.0) == timedelta(seconds=60)  # capped
        assert compute_backoff(20, jitter_pct=0.0) == timedelta(seconds=60)  # capped


class TestBounds:
    @settings(max_examples=200)
    @given(attempt=st.integers(min_value=0, max_value=20))
    def test_within_jitter_band_of_raw(self, attempt: int) -> None:
        rng = random.Random(attempt * 7 + 1)
        delay = compute_backoff(attempt, rng=rng)
        cap = BACKOFF_CAP.total_seconds()
        base = BACKOFF_BASE.total_seconds()
        raw = min(cap, base * (2**attempt))
        lo = max(0.0, raw * (1 - JITTER_PCT))
        hi = raw * (1 + JITTER_PCT)
        seconds = delay.total_seconds()
        assert lo <= seconds <= hi, f"{seconds} not in [{lo}, {hi}] for attempt={attempt}"

    @settings(max_examples=200)
    @given(attempt=st.integers(min_value=0, max_value=20))
    def test_never_negative(self, attempt: int) -> None:
        rng = random.Random()
        for _ in range(5):
            assert compute_backoff(attempt, rng=rng) >= timedelta(0)

    def test_capped_at_high_attempts(self) -> None:
        rng = random.Random(0)
        # Attempt 30 would otherwise be 2^30 seconds (~34 years).
        delay = compute_backoff(30, rng=rng)
        assert delay <= BACKOFF_CAP * (1 + JITTER_PCT)
