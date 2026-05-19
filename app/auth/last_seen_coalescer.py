"""Write-coalescer for `sessions.last_seen_at`.

Hex Audit BA-F6 + FG2-H1 + FG2-M7 (slice 7). The HTTP cookie-auth
path (`SessionAuthService.verify_cookie`) and the WebSocket upgrade
path (`/ws`) both call `HttpSessionsRepository.touch_last_seen` on
every request / accept. A normal SPA page-load issues ~10-20 fetches
through the session cookie → 10-20 SQLite write transactions for what
is effectively the same "this session is alive right now" signal.
SQLite is a single-writer DB and the audit-log path also writes, so
the contention is real.

This module collapses repeated touches per `token_hash` into at most
one write per `LAST_SEEN_COALESCE_WINDOW_SECONDS`. The state is
in-memory (OrderedDict keyed by token_hash), FIFO-capped to bound
memory under a flood of stolen-cookie reconnect attempts.

Single-event-loop assumption: this module mutates an OrderedDict
without a lock. Production runs uvicorn `--workers 1` (ADR-0001), so
all coroutines share one event loop and reads + writes between
`await` points are atomic. Multi-worker support would require either
a per-worker coalescer (relaxed invariant — at most N writes per
window where N = worker count) or a Redis-style shared counter; this
module is the single-worker design.

Crash semantics: in-memory state is lost on restart. After a restart
every active session contributes one extra UPDATE on its next
verify_cookie / ws-accept call — a tiny one-shot cost dwarfed by the
ongoing savings. No flush-on-shutdown.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Final

import structlog

_logger = structlog.get_logger(__name__)

LAST_SEEN_COALESCE_WINDOW_SECONDS: Final[float] = 60.0
"""How long a `touch_last_seen` write is suppressed after the last
write for the same `token_hash`. 60 s matches the human-perception
"is this session active right now?" granularity the sessions list UI
cares about, and a typical SPA page-load's 10-20 cookie-auth fetches
collapse to one DB write. Well below any idle-timeout (default 600 s)
so the column stays a useful liveness signal."""

MAX_TRACKED_SESSIONS: Final[int] = 10_000
"""Hard cap on the number of `token_hash` keys tracked at once. A
single-user product has one or two active sessions in practice; the
cap is purely defensive against a stolen-cookie reconnect-loop
attacker churning `token_hash`es. Mirrors
`LoginRateLimiter.MAX_TRACKED_BUCKETS`. Eviction drops the oldest
entry (LRU-of-write semantics, not LRU-of-touch); an evicted entry
just causes one extra DB write on next verify, not a security
regression."""

LAST_SEEN_TOUCH_TIMEOUT_SECONDS: Final[float] = 2.0
"""Timeout wrapper for the SQLite write that follows
`should_write() → True`. SQLite is a single-writer DB; if the audit
loop or some other writer holds the lock, the touch would otherwise
block the WS upgrade or HTTP request indefinitely. 2 s is generous
vs. p99 write latency yet tight enough to bound stall under DB
pressure. On timeout, the touch is logged as a warning and the
calling handler proceeds — `last_seen_at` is observability, not
load-bearing for any auth decision (FG2-M7 fold)."""


class LastSeenCoalescer:
    """In-memory `token_hash → last-write-monotonic` map with TTL skip.

    Construction:
      coalescer = LastSeenCoalescer()
      # or in tests:
      coalescer = LastSeenCoalescer(window_seconds=0.1, max_entries=8)

    Usage (HTTP + WS):
      if coalescer.should_write(token_hash):
          await repo.touch_last_seen(token_hash, now)
          coalescer.mark_written(token_hash)

    The two-step API (`should_write` → caller-writes → `mark_written`)
    is deliberate. A single `should_write_and_mark` method would
    over-mark on a writer that ultimately fails (timeout, DB error),
    leaving the in-memory state declaring "we wrote within the
    window" when in fact no row was updated. With the split, the
    caller marks ONLY on a successful write; a failed write retries
    on the next call.
    """

    __slots__ = ("_window", "_max_entries", "_clock", "_last_written")

    def __init__(
        self,
        *,
        window_seconds: float = LAST_SEEN_COALESCE_WINDOW_SECONDS,
        max_entries: int = MAX_TRACKED_SESSIONS,
        clock: object = None,
    ) -> None:
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._window = window_seconds
        self._max_entries = max_entries
        self._clock = clock if callable(clock) else time.monotonic
        self._last_written: OrderedDict[bytes, float] = OrderedDict()

    def should_write(self, token_hash: bytes) -> bool:
        """True iff no recent write recorded for this session.

        Read-only; never materialises an entry. A True return obliges
        the caller to attempt the actual UPDATE and then call
        `mark_written` on success.
        """
        if not isinstance(token_hash, bytes):
            raise TypeError("token_hash must be bytes")
        last = self._last_written.get(token_hash)
        if last is None:
            return True
        return (self._clock() - last) >= self._window

    def mark_written(self, token_hash: bytes) -> None:
        """Record that a write just succeeded for this session.

        Enforces `max_entries` via FIFO eviction (drop the oldest
        insertion). Touch (`move_to_end`) on every mark so the
        eviction order reflects activity, not original creation time.
        """
        if not isinstance(token_hash, bytes):
            raise TypeError("token_hash must be bytes")
        now = self._clock()
        if token_hash in self._last_written:
            self._last_written[token_hash] = now
            self._last_written.move_to_end(token_hash)
            return
        if len(self._last_written) >= self._max_entries:
            self._last_written.popitem(last=False)
        self._last_written[token_hash] = now

    def forget(self, token_hash: bytes) -> None:
        """Drop the entry for a revoked session.

        Called from `SessionAuthService.revoke` so the next reuse of
        the same cookie value (vanishingly unlikely given 32 random
        bytes, but cheap to handle) lands a write immediately rather
        than being suppressed by a stale entry. Idempotent.
        """
        if not isinstance(token_hash, bytes):
            raise TypeError("token_hash must be bytes")
        self._last_written.pop(token_hash, None)

    def reset(self) -> None:
        """Drop all state. Test helper."""
        self._last_written.clear()


__all__ = [
    "LAST_SEEN_COALESCE_WINDOW_SECONDS",
    "LAST_SEEN_TOUCH_TIMEOUT_SECONDS",
    "LastSeenCoalescer",
    "MAX_TRACKED_SESSIONS",
]
