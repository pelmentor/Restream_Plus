"""Short-lived (60 s) scope-bound grants for sensitive actions.

Per ADR-0005 §"AuthReprompt grant tokens". The user clicks a
destructive button ("Reveal stream key", "Delete target", "Rotate
passphrase"); the UI prompts for the admin password; on success the
server issues an opaque grant ID with a specific scope; the
subsequent destructive API call carries that grant ID and the server
consumes it (one-shot). The grant is bound to the user who proved
the password AND to the scope the user proved it for — re-prompting
to reveal a stream key does not authorise deleting a target.

The Backend Architect memo (Phase 3 ideation) settled on in-memory
storage with an asyncio cleanup task:

  - 60 seconds is short enough that a process-restart-during-window
    is a non-event (the user simply re-enters their password).
  - DB-backed would mean a write per sensitive op, plus DELETE or a
    `consumed_at` column with cleanup — no win.
  - A stateless encrypted token cannot satisfy single-use semantics
    without also maintaining a revocation set, which is just stateful
    storage again.

Single-worker assumption: this module uses an in-process dict, so
two uvicorn workers do NOT share grants. Production runs uvicorn with
`--workers 1` (the default and the only supported value for v1 per
ADR-0001); if that ever changes, this module needs replacement, not
adjustment.

Concurrency is single-event-loop (asyncio); no thread lock is needed.
The dict and lifecycle methods are still synchronous (no `await`)
because there's no I/O involved — `async def` would only add
overhead.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

import structlog

_logger = structlog.get_logger(__name__)

REPROMPT_GRANT_TTL_SECONDS: Final[float] = 60.0
"""ADR-0005: 60-second grant lifetime. Short enough that loss-on-restart
is a non-event; long enough that the user has time to confirm the
prompt and click the destructive button."""

REPROMPT_GRANT_ID_BYTES: Final[int] = 32
"""`secrets.token_urlsafe(32)` → 43 chars URL-safe. Grant IDs are
opaque to the client and never reused."""

MAX_TRACKED_GRANTS: Final[int] = 10_000
"""Hex Audit footgun-hunter FG2-C4 (2026-05-18): hard cap on the
`_grants` dict. Sibling of `LoginRateLimiter.MAX_TRACKED_BUCKETS` —
an authenticated client bug (runaway useEffect, malicious script)
that re-issues grants in a tight loop would otherwise grow the dict
without bound between `prune_expired` ticks (30 s per Phase-6
lifespan). FIFO eviction by insertion order via `OrderedDict.popitem(last=False)`:
the oldest unconsumed grant is dropped when the cap is hit. 10 000
× 43-byte grant IDs + RepromptGrant payload ≈ a few MB — comfortably
above any legitimate operator's working set (one operator, one
session, one or two reprompts pending at a time)."""


class RepromptScope(StrEnum):
    """Scopes a reprompt grant can authorise.

    Extending this enum is the canonical way to introduce a new
    sensitive action; the type system then forces every call site to
    handle the new scope. Strings (instead of integers) so audit-log
    entries and structlog events stay readable.
    """

    REVEAL_STREAM_KEY = "reveal_stream_key"
    REGENERATE_INGEST_KEY = "regenerate_ingest_key"
    ROTATE_PASSPHRASE = "rotate_passphrase"  # noqa: S105 — scope value, not a secret
    CHANGE_PASSWORD = "change_password"  # noqa: S105 — scope value, not a secret
    DELETE_TARGET = "delete_target"
    REVOKE_API_TOKEN = "revoke_api_token"  # noqa: S105 — scope value, not a secret
    CLEAR_CREDENTIAL = "clear_credential"
    """Added Phase 6 (security review H-3): clearing a credential silently
    drops every active stream to that target. A stolen-cookie attacker
    without this gate can knock all targets offline. Same UX shape as
    DELETE_TARGET, so the prompt copy mirrors that flow."""
    REVEAL_INGEST_KEY = "reveal_ingest_key"
    """Phase 9: the ingest-key reveal endpoint at
    `POST /api/settings/reveal-ingest-key` requires a fresh password
    reprompt. Distinct from REVEAL_STREAM_KEY because the threat
    profile differs — a leaked ingest key only enables loopback
    publishing inside the container, while a platform stream key
    enables publishing to the third-party platform under the
    operator's identity. Scope-separation per ADR-0005."""
    RESET_TARGET_WORKER = "reset_target_worker"
    """Hex Audit BA-F13 (slice 10): the per-target 'Retry now' endpoint
    at `POST /api/targets/{target_id}/reset-worker` clears a FAILED_OPEN
    breaker counter and re-enters the reconnect loop. Without a reprompt,
    a stolen-cookie attacker can spam the reset on a flapping target,
    defeating the circuit breaker's "no noisy neighbor" protection
    (ADR-0003 §Reconnect policy) and earning the operator an IP-ban
    from the target platform. Scope-bound so a reveal-stream-key grant
    cannot be re-used as a reset grant."""


@dataclass(frozen=True, slots=True)
class RepromptGrant:
    """One issued grant. Kept private to this module — callers don't
    construct these. Tests that need to introspect can read
    `RepromptStore._grants` directly."""

    user_id: str
    scope: RepromptScope
    issued_at: float
    """`time.monotonic()` value, NOT a wall-clock time. Used only for
    TTL math; immune to clock changes."""


class RepromptStore:
    """In-memory store of unconsumed reprompt grants.

    Grant lifecycle:
        issue(user_id, scope)  -> grant_id
        consume(grant_id, user_id, scope) -> True iff:
          - grant exists, AND
          - grant.user_id == user_id, AND
          - grant.scope == scope, AND
          - now - grant.issued_at < TTL
          The grant is popped on consume (single-use).

    Periodic `prune_expired()` calls keep the dict from growing
    unboundedly under abandoned grants. The main app's lifespan task
    schedules this in Phase 6; for tests, call it explicitly. A
    second defence — `MAX_TRACKED_GRANTS` FIFO eviction in `issue` —
    closes the gap between prune ticks against an authenticated
    client bug or malicious in-loop reprompt-issuer
    (Hex Audit FG2-C4).

    Failure modes for `consume` collapse to a single False return —
    "wrong user" and "wrong scope" and "expired" are indistinguishable
    to the caller, who returns the same 403 either way.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = REPROMPT_GRANT_TTL_SECONDS,
        clock: object = None,
        max_grants: int = MAX_TRACKED_GRANTS,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        if max_grants <= 0:
            raise ValueError("max_grants must be > 0")
        self._ttl = ttl_seconds
        self._clock = clock if callable(clock) else time.monotonic
        self._max_grants = max_grants
        # OrderedDict so the cap is enforced with cheap FIFO eviction
        # (`popitem(last=False)` drops the oldest insertion).
        self._grants: OrderedDict[str, RepromptGrant] = OrderedDict()

    def issue(self, *, user_id: str, scope: RepromptScope) -> str:
        """Mint a new grant. Returns the opaque grant ID."""
        if not isinstance(scope, RepromptScope):
            raise TypeError("scope must be a RepromptScope")
        if not user_id:
            raise ValueError("user_id must be a non-empty string")
        grant_id = secrets.token_urlsafe(REPROMPT_GRANT_ID_BYTES)
        # FG2-C4: evict the oldest entry BEFORE inserting so the dict
        # size invariant holds at all observation points. A legitimate
        # operator never hits this cap (one human, max ~2 grants in
        # flight); the cap is purely a defence against runaway issuers
        # in the 30 s window between `prune_expired` ticks.
        if len(self._grants) >= self._max_grants:
            self._grants.popitem(last=False)
        self._grants[grant_id] = RepromptGrant(
            user_id=user_id, scope=scope, issued_at=self._clock()
        )
        _logger.info(
            "reprompt_grant_issued",
            # Log a SHA-256 fingerprint of the grant ID rather than a
            # prefix of the plaintext (Phase 3 security review #9). The
            # fingerprint is grep-able for correlating issue ↔ consume
            # in audit trails without exposing any portion of the
            # plaintext that an attacker could use to narrow a search.
            grant_id_fingerprint=hashlib.sha256(grant_id.encode("utf-8")).hexdigest()[:12],
            user_id=user_id,
            scope=scope.value,
        )
        return grant_id

    def consume(self, *, grant_id: str, user_id: str, scope: RepromptScope) -> bool:
        """Consume a grant. Returns True iff valid; pops on success."""
        if not isinstance(scope, RepromptScope):
            raise TypeError("scope must be a RepromptScope")
        grant = self._grants.pop(grant_id, None)
        if grant is None:
            return False
        now = self._clock()
        if now - grant.issued_at >= self._ttl:
            # Expired. Already popped above; nothing else to do.
            return False
        # The `.pop()` above already removed the grant. So when user/
        # scope don't match, returning False also burns it — which is
        # correct: a probing attacker cannot reuse a stolen grant ID
        # even against the wrong scope.
        return grant.user_id == user_id and grant.scope == scope

    def prune_expired(self) -> int:
        """Drop all expired grants. Returns the number pruned.

        Safe to call from any asyncio cycle; runs in O(N) over
        currently-held grants. For the single-user product the dict
        is tiny in practice.
        """
        now = self._clock()
        threshold = now - self._ttl
        expired = [gid for gid, g in self._grants.items() if g.issued_at <= threshold]
        for gid in expired:
            self._grants.pop(gid, None)
        return len(expired)

    def reset(self) -> None:
        """Drop everything. Test helper."""
        self._grants.clear()


__all__ = [
    "MAX_TRACKED_GRANTS",
    "REPROMPT_GRANT_ID_BYTES",
    "REPROMPT_GRANT_TTL_SECONDS",
    "RepromptScope",
    "RepromptStore",
]
