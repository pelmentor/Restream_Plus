"""Tests for `LastSeenCoalescer` (Hex Audit BA-F6, slice 7).

Pure-Python unit tests with an injected clock — no DB, no asyncio.
Behavioral invariants:

  - `should_write` returns True before any `mark_written` for that key.
  - `should_write` returns False inside the suppression window.
  - `should_write` returns True again after the window elapses.
  - `mark_written` enforces FIFO eviction at `max_entries`.
  - `forget` clears the entry so the next `should_write` returns True.
  - Type checks reject non-bytes keys.
"""

from __future__ import annotations

import pytest
from app.auth.last_seen_coalescer import (
    LAST_SEEN_COALESCE_WINDOW_SECONDS,
    LastSeenCoalescer,
)


class FakeClock:
    """Monotonically-advancing clock the test can step explicitly."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestShouldWriteAndMarkWritten:
    def test_initial_should_write_true(self) -> None:
        coalescer = LastSeenCoalescer()
        assert coalescer.should_write(b"\x00" * 32) is True

    def test_after_mark_written_within_window_should_write_false(self) -> None:
        clock = FakeClock()
        coalescer = LastSeenCoalescer(window_seconds=60.0, clock=clock)
        coalescer.mark_written(b"abc")
        clock.advance(59.0)
        assert coalescer.should_write(b"abc") is False

    def test_after_window_elapses_should_write_true(self) -> None:
        clock = FakeClock()
        coalescer = LastSeenCoalescer(window_seconds=60.0, clock=clock)
        coalescer.mark_written(b"abc")
        clock.advance(60.0)
        # `>= window` is the threshold per the implementation.
        assert coalescer.should_write(b"abc") is True

    def test_different_keys_are_independent(self) -> None:
        coalescer = LastSeenCoalescer(window_seconds=60.0)
        coalescer.mark_written(b"key-a")
        assert coalescer.should_write(b"key-b") is True
        assert coalescer.should_write(b"key-a") is False

    def test_mark_written_re_arms_window(self) -> None:
        """A second mark_written within the window doesn't end suppression
        immediately — the window is measured from the latest mark."""
        clock = FakeClock()
        coalescer = LastSeenCoalescer(window_seconds=60.0, clock=clock)
        coalescer.mark_written(b"k")
        clock.advance(30.0)
        coalescer.mark_written(b"k")  # second mark resets timer
        clock.advance(45.0)
        # 45s after second mark — still inside the 60s window.
        assert coalescer.should_write(b"k") is False


class TestEvictionCap:
    def test_fifo_eviction_drops_oldest(self) -> None:
        clock = FakeClock()
        coalescer = LastSeenCoalescer(window_seconds=60.0, max_entries=3, clock=clock)
        coalescer.mark_written(b"a")
        coalescer.mark_written(b"b")
        coalescer.mark_written(b"c")
        # Insert a 4th — `a` is oldest insert, gets evicted.
        coalescer.mark_written(b"d")
        # `a` was evicted, so its next should_write returns True.
        assert coalescer.should_write(b"a") is True
        # `b`, `c`, `d` are still tracked.
        assert coalescer.should_write(b"b") is False
        assert coalescer.should_write(b"c") is False
        assert coalescer.should_write(b"d") is False

    def test_re_marking_existing_key_does_not_evict(self) -> None:
        clock = FakeClock()
        coalescer = LastSeenCoalescer(window_seconds=60.0, max_entries=2, clock=clock)
        coalescer.mark_written(b"a")
        coalescer.mark_written(b"b")
        # Re-mark an existing key — should NOT trigger an eviction.
        coalescer.mark_written(b"a")
        # Both still present.
        assert coalescer.should_write(b"a") is False
        assert coalescer.should_write(b"b") is False

    def test_re_mark_promotes_to_mru(self) -> None:
        """`mark_written` on an existing key moves it to MRU position so
        the cold key is the FIFO eviction target."""
        clock = FakeClock()
        coalescer = LastSeenCoalescer(window_seconds=60.0, max_entries=2, clock=clock)
        coalescer.mark_written(b"a")
        coalescer.mark_written(b"b")
        coalescer.mark_written(b"a")  # `a` becomes MRU; `b` is now LRU
        coalescer.mark_written(b"c")  # `b` evicted, not `a`
        assert coalescer.should_write(b"a") is False
        assert coalescer.should_write(b"b") is True  # evicted
        assert coalescer.should_write(b"c") is False


class TestForget:
    def test_forget_clears_state(self) -> None:
        coalescer = LastSeenCoalescer(window_seconds=60.0)
        coalescer.mark_written(b"k")
        assert coalescer.should_write(b"k") is False
        coalescer.forget(b"k")
        assert coalescer.should_write(b"k") is True

    def test_forget_unknown_key_is_idempotent(self) -> None:
        coalescer = LastSeenCoalescer(window_seconds=60.0)
        # Should not raise.
        coalescer.forget(b"never-seen")


class TestTypeChecks:
    def test_should_write_rejects_non_bytes(self) -> None:
        coalescer = LastSeenCoalescer()
        with pytest.raises(TypeError):
            coalescer.should_write("not-bytes")  # type: ignore[arg-type]

    def test_mark_written_rejects_non_bytes(self) -> None:
        coalescer = LastSeenCoalescer()
        with pytest.raises(TypeError):
            coalescer.mark_written("not-bytes")  # type: ignore[arg-type]

    def test_forget_rejects_non_bytes(self) -> None:
        coalescer = LastSeenCoalescer()
        with pytest.raises(TypeError):
            coalescer.forget("not-bytes")  # type: ignore[arg-type]

    def test_constructor_rejects_zero_window(self) -> None:
        with pytest.raises(ValueError, match="window_seconds must be > 0"):
            LastSeenCoalescer(window_seconds=0.0)

    def test_constructor_rejects_zero_max_entries(self) -> None:
        with pytest.raises(ValueError, match="max_entries must be >= 1"):
            LastSeenCoalescer(max_entries=0)


class TestDefaultWindow:
    def test_default_window_is_one_minute(self) -> None:
        # The default value is the contract: a future change should
        # update this test consciously.
        assert LAST_SEEN_COALESCE_WINDOW_SECONDS == 60.0
