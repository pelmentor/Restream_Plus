"""Exponential-with-jitter backoff schedule per ADR-0003.

Pure stateless function: `compute_backoff(attempt)` returns a
`timedelta` that the caller (the Worker) sleeps before its next spawn
attempt. State (the attempt counter, the breaker) lives on the Worker;
this module only knows how to do the arithmetic.

Constants are module-level so tests can pin them. A custom `rng` may
be passed (a seeded `random.Random`) so tests are deterministic.
"""

from __future__ import annotations

import random
from datetime import timedelta
from typing import Final

BACKOFF_BASE: Final[timedelta] = timedelta(seconds=1)
"""Base delay for attempt=0. ADR-0003 fixes this at 1 s."""

BACKOFF_CAP: Final[timedelta] = timedelta(seconds=60)
"""Maximum delay after exponential growth. ADR-0003 fixes this at 60 s."""

JITTER_PCT: Final[float] = 0.20
"""±20% jitter applied to the exponential value. ADR-0003 fixes this."""


def compute_backoff(
    attempt: int,
    *,
    base: timedelta = BACKOFF_BASE,
    cap: timedelta = BACKOFF_CAP,
    jitter_pct: float = JITTER_PCT,
    rng: random.Random | None = None,
) -> timedelta:
    """Return the delay before the next spawn attempt.

    Schedule: `min(cap, base * 2^attempt) +/- jitter_pct%`.

    `attempt` is zero-indexed: the first failure → `compute_backoff(0)`
    delay before the second spawn. The result is clamped at 0
    (negative jitter on a near-zero base could otherwise produce
    negative deltas).
    """
    if attempt < 0:
        raise ValueError("attempt must be >= 0")
    if jitter_pct < 0:
        raise ValueError("jitter_pct must be >= 0")
    cap_seconds = cap.total_seconds()
    base_seconds = base.total_seconds()
    raw = min(cap_seconds, base_seconds * (2**attempt))
    r = rng if rng is not None else random
    jitter = raw * jitter_pct * (2 * r.random() - 1)
    return timedelta(seconds=max(0.0, raw + jitter))
