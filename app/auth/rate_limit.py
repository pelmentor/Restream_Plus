"""Login-attempt rate limiting + reverse-proxy client-IP extraction.

Per ADR-0005 §"Boot-time KEK availability and locked-mode endpoint
surface" (rate-limit paragraph). The policy was tightened by the
Backend Architect agent ideation: two independent sliding-window
counters (per-IP and per-username) where **both** must be under
threshold for an attempt to proceed.

IP-only buckets fail against distributed attackers (a botnet of
unlimited size sees no rate limit on the admin account);
username-only buckets invite trivial DoS lockouts of the admin.
Pairing them: a single-IP attacker stops at 5 attempts (per-IP
ceiling), a distributed attacker stops at 5 attempts against the
attacked username (per-username ceiling). Cost: one extra dict
lookup per check.

State is in-memory, volatile across process restarts. ADR-0005
documents this explicitly — persisting the counter would be a write
per failed login, its own attack surface, and the worst case is one
extra 15-minute window of attempts after a crash. The rate-limiter
is one layer among many (Argon2id, constant-time floor, audit log).

Client-IP extraction follows the rightmost-untrusted-hop rule:
trust `X-Forwarded-For` only when the request's immediate peer IP
is inside the operator-configured `RESTREAM_TRUSTED_PROXIES` CIDR
list; walk the forwarded chain right-to-left, drop trusted hops,
return the first non-trusted entry. A deployment that has not
configured `RESTREAM_TRUSTED_PROXIES` ignores the header entirely
(default empty → fail-closed).
"""

from __future__ import annotations

import ipaddress
import math
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from app.auth.usernames import normalize_username

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterable, Sequence

    from app.config import IpNetwork


DEFAULT_FAILURE_LIMIT: Final[int] = 5
"""ADR-0005: '5 attempts per IP per 15 minutes'. The same limit
applies to the per-username bucket per the Phase 3 architect memo."""

DEFAULT_WINDOW_SECONDS: Final[float] = 15 * 60.0
"""15-minute sliding window per ADR-0005."""

UNKNOWN_CLIENT_IP: Final[str] = "unknown"
"""Sentinel used in rate-limit bucket keys when no peer IP is
discernible (e.g., Starlette's `request.client` is None during some
test transports). Treats anonymous peers as a single shared bucket
so they share the same rate ceiling — fail-closed."""

MAX_TRACKED_BUCKETS: Final[int] = 10_000
"""Hard cap on the number of live buckets in EACH of the per-IP and
per-username dicts. Without a cap an attacker submitting login
attempts with rotating spoofed `X-Forwarded-For` IPs (when trusted
proxies are configured) or rotating usernames could materialize
10M+ permanent dict entries, costing ~600 B/key and OOM-killing the
process. Hex Audit footgun-hunter F2 (2026-05-18). 10k × 2 dicts
× ~600 B = ~12 MB ceiling; far in excess of any legitimate workload."""


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Outcome of `LoginRateLimiter.check()`.

    `retry_after_seconds` is meaningful only when `allowed=False`. It
    is the number of whole seconds (rounded up) until at least one
    slot opens in the tighter of the two buckets — a value safe to
    surface in an HTTP `Retry-After` header. The minimum is 1 to
    avoid emitting `Retry-After: 0` for a sub-second remainder.
    """

    allowed: bool
    retry_after_seconds: int


class LoginRateLimiter:
    """Paired sliding-window failure counters: per-IP AND per-username.

    Thread-safe via a single `threading.Lock`. Lock contention is a
    non-issue for a single-user app — the limiter is touched at most
    once per login attempt.

    Implementation detail: each bucket is a `deque` of failure
    timestamps (monotonic seconds). On every `check`, expired
    timestamps are pruned from the left of each relevant bucket
    before the count is read. `record_failure` appends and prunes.
    Memory is O(failure_limit) per active bucket per window.

    Username keys go through `normalize_username` (NFKC + casefold +
    strip) before bucketing so an attacker can't multiply attempts by
    varying case or by injecting Unicode lookalikes (`ADMİN` vs
    `admin`) — per Phase 3 security review finding #3.
    """

    def __init__(
        self,
        *,
        failure_limit: int = DEFAULT_FAILURE_LIMIT,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        clock: object = None,
        max_buckets: int = MAX_TRACKED_BUCKETS,
    ) -> None:
        if failure_limit < 1:
            raise ValueError("failure_limit must be >= 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        if max_buckets < 1:
            raise ValueError("max_buckets must be >= 1")
        self._limit = failure_limit
        self._window = window_seconds
        # `clock` is `monotonic` in production; tests can inject a
        # callable that returns a controlled value. Typed loosely so
        # the test fixture's `Callable[[], float]` works without an
        # extra runtime check.
        self._clock = clock if callable(clock) else time.monotonic
        # Hex Audit CR2-H3 (2026-05-18): the cap is stored on `self`
        # so tests can construct a small-cap limiter explicitly
        # (`LoginRateLimiter(max_buckets=5)`) instead of monkey-patching
        # the module-level constant — the patching pattern only worked
        # because `_materialize` referenced `MAX_TRACKED_BUCKETS` as a
        # bare-name global at call time, and it would silently break if
        # the cap ever moved to an instance attribute or closure.
        self._max_buckets = max_buckets
        self._lock = threading.Lock()
        # OrderedDict (instead of defaultdict): `check()` reads via
        # `.get()` and never materializes a bucket on miss; only
        # `record_failure()` creates buckets, with FIFO eviction when
        # the cap is hit. Empty buckets are deleted as soon as pruning
        # drains them. See Hex Audit footgun-hunter F2.
        self._by_ip: OrderedDict[str, deque[float]] = OrderedDict()
        self._by_username: OrderedDict[str, deque[float]] = OrderedDict()

    def check(self, *, ip: str, username: str) -> RateLimitDecision:
        """Is this `(ip, username)` allowed to attempt right now?

        Side effect: prunes expired entries from both buckets when they
        exist. Does NOT materialize new buckets on miss — that prevents
        a flood of one-shot probes from inflating memory. Does NOT
        record an attempt — call `record_failure` after a failed login.
        """
        username_key = normalize_username(username)
        with self._lock:
            now = self._clock()
            ip_bucket = self._by_ip.get(ip)
            uname_bucket = self._by_username.get(username_key)
            if ip_bucket is not None:
                self._prune_or_evict(self._by_ip, ip, ip_bucket, now)
                ip_bucket = self._by_ip.get(ip)
            if uname_bucket is not None:
                self._prune_or_evict(self._by_username, username_key, uname_bucket, now)
                uname_bucket = self._by_username.get(username_key)

            ip_count = len(ip_bucket) if ip_bucket is not None else 0
            uname_count = len(uname_bucket) if uname_bucket is not None else 0
            ip_full = ip_count >= self._limit
            uname_full = uname_count >= self._limit

            if not ip_full and not uname_full:
                return RateLimitDecision(allowed=True, retry_after_seconds=0)

            # Either bucket alone is sufficient to deny. The
            # retry-after surfaces the *worst* case so the operator's
            # automation honors it.
            retry_candidates: list[float] = []
            if ip_full and ip_bucket is not None:
                retry_candidates.append(self._oldest_expiry(ip_bucket, now))
            if uname_full and uname_bucket is not None:
                retry_candidates.append(self._oldest_expiry(uname_bucket, now))
            seconds = max(retry_candidates) if retry_candidates else 0.0
            retry_after = max(1, math.ceil(seconds))
            return RateLimitDecision(allowed=False, retry_after_seconds=retry_after)

    def record_failure(self, *, ip: str, username: str) -> None:
        """Record one failed attempt against both `(ip, username)` buckets.

        Materializes the bucket if absent, enforcing `MAX_TRACKED_BUCKETS`
        via FIFO eviction (drop the least-recently-touched bucket).
        Touch (`move_to_end`) on every insert so the eviction order
        reflects activity, not original creation time.
        """
        username_key = normalize_username(username)
        with self._lock:
            now = self._clock()
            ip_bucket = self._materialize(self._by_ip, ip)
            uname_bucket = self._materialize(self._by_username, username_key)
            self._prune(ip_bucket, now)
            self._prune(uname_bucket, now)
            ip_bucket.append(now)
            uname_bucket.append(now)
            # Recording made these buckets MRU; refresh their position
            # so the FIFO eviction targets genuinely cold keys.
            self._by_ip.move_to_end(ip)
            self._by_username.move_to_end(username_key)

    def reset(self) -> None:
        """Drop all in-memory buckets. Test helper."""
        with self._lock:
            self._by_ip.clear()
            self._by_username.clear()

    def _materialize(
        self, store: OrderedDict[str, deque[float]], key: str
    ) -> deque[float]:
        """Get-or-create a bucket, evicting the oldest if at cap."""
        bucket = store.get(key)
        if bucket is not None:
            return bucket
        if len(store) >= self._max_buckets:
            # FIFO eviction — drop the least-recently-touched key. The
            # evicted bucket's history is lost, which is the price of
            # the memory cap; the alternative is OOM.
            store.popitem(last=False)
        bucket = deque()
        store[key] = bucket
        return bucket

    def _prune(self, bucket: deque[float], now: float) -> None:
        threshold = now - self._window
        while bucket and bucket[0] <= threshold:
            bucket.popleft()

    def _prune_or_evict(
        self,
        store: OrderedDict[str, deque[float]],
        key: str,
        bucket: deque[float],
        now: float,
    ) -> None:
        """Prune expired entries; delete the bucket entirely if empty.

        Without the delete, a bucket whose contents pruned to zero would
        linger forever (the read-side never deletes), giving back the
        unbounded-growth footgun the cap closes. Deleting on empty is
        the load-bearing companion to `MAX_TRACKED_BUCKETS`.
        """
        self._prune(bucket, now)
        if not bucket:
            store.pop(key, None)

    def _oldest_expiry(self, bucket: deque[float], now: float) -> float:
        """Seconds until the oldest entry in `bucket` falls out of window.

        Caller must have called `_prune` already, so `bucket[0]` is
        the earliest timestamp still inside the window.
        """
        return (bucket[0] + self._window) - now


def extract_client_ip(
    *,
    peer_ip: str | None,
    forwarded_for: str | None,
    trusted_proxies: Sequence[IpNetwork] | None,
) -> str:
    """Determine the request's client IP under the trusted-proxy rule.

    Algorithm (Backend Architect Q4):

    1. If the peer is missing entirely → return UNKNOWN_CLIENT_IP.
    2. If no trusted proxies are configured, or the peer is not in
       the trusted set, ignore X-Forwarded-For and return the peer.
    3. Otherwise, walk X-Forwarded-For right-to-left: skip entries
       whose IP is in the trusted set; the first non-trusted entry
       is the client. If every entry is trusted, fall back to the
       peer (the immediate hop *is* a trusted proxy talking on behalf
       of itself).

    Returns a string suitable for storage in `sessions.ip` and for
    use as a rate-limit bucket key. Malformed entries in
    `X-Forwarded-For` are skipped.
    """
    if not peer_ip:
        return UNKNOWN_CLIENT_IP

    if not trusted_proxies or not _ip_in_networks(peer_ip, trusted_proxies):
        return peer_ip

    if not forwarded_for:
        return peer_ip

    hops = [hop.strip() for hop in forwarded_for.split(",") if hop.strip()]
    for hop in reversed(hops):
        # Some proxies emit `for=1.2.3.4:5678` (port suffix) or bracketed
        # IPv6 (`[::1]:5678`). Strip both before parsing.
        cleaned = _strip_port(hop)
        if not _is_valid_ip(cleaned):
            continue
        if not _ip_in_networks(cleaned, trusted_proxies):
            return cleaned

    return peer_ip


def _is_valid_ip(candidate: str) -> bool:
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return True


def _ip_in_networks(candidate: str, networks: Iterable[IpNetwork]) -> bool:
    try:
        addr = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return any(addr in net for net in networks)


def _strip_port(hop: str) -> str:
    """Strip a `:port` suffix (IPv4) or `[addr]:port` brackets (IPv6).

    No-op when the input is already a parseable bare IP. The order
    matters: per Phase 3 security review #2, the naive `count(":") == 1`
    rule mis-handles unbracketed `IPv6:port` forms like `2001:db8::1:443`
    (4 colons → unchanged → wrong bucket). We resolve by parsing FIRST
    with `ipaddress.ip_address`: if the input is already a valid bare
    IP, keep it. Otherwise fall back to the IPv4-with-port /
    bracketed-IPv6 strippers.

    The remaining ambiguity — a proxy that emits a bare-IPv6 string
    WITH a trailing `:port` (no brackets) — is structurally
    unresolvable. The caller cannot tell `2001:db8::443` (bare IPv6
    ending in `:443`) from `2001:db8:::443` (bare IPv6 + port).
    We choose to preserve the input in that case so `_is_valid_ip`
    rejects it as malformed, and the malformed entry is then skipped
    in the walk — better to drop an entry than to bucket two distinct
    clients under the same key.
    """
    # Already a parseable bare IP (covers canonical IPv4 and IPv6
    # without port). Leave it alone.
    if _is_valid_ip(hop):
        return hop

    # Bracketed IPv6 (with or without port suffix): `[addr]:port` or
    # `[addr]`.
    if hop.startswith("[") and "]" in hop:
        end = hop.index("]")
        return hop[1:end]

    # IPv4 with port: exactly one colon and the left side is a valid
    # IPv4.
    if hop.count(":") == 1:
        host, _, _port = hop.partition(":")
        if _is_valid_ip(host):
            return host

    return hop


__all__ = [
    "DEFAULT_FAILURE_LIMIT",
    "DEFAULT_WINDOW_SECONDS",
    "LoginRateLimiter",
    "RateLimitDecision",
    "UNKNOWN_CLIENT_IP",
    "extract_client_ip",
]
