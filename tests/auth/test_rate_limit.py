"""Tests for the paired-bucket sliding-window rate limiter and the
trusted-proxy client-IP extractor."""

from __future__ import annotations

import ipaddress

import pytest
from app.auth.rate_limit import (
    DEFAULT_FAILURE_LIMIT,
    DEFAULT_WINDOW_SECONDS,
    UNKNOWN_CLIENT_IP,
    LoginRateLimiter,
    RateLimitDecision,
    extract_client_ip,
)


def _clock(value: list[float]) -> object:
    """A 1-element mutable container that returns its current value
    on each call; tests increment it to advance the clock without
    relying on real time."""

    def now() -> float:
        return value[0]

    return now


class TestLoginRateLimiterConstruction:
    def test_invalid_failure_limit_rejected(self) -> None:
        with pytest.raises(ValueError, match="failure_limit"):
            LoginRateLimiter(failure_limit=0)

    def test_invalid_window_rejected(self) -> None:
        with pytest.raises(ValueError, match="window_seconds"):
            LoginRateLimiter(window_seconds=0)


class TestPerIpBucket:
    def test_under_limit_allowed(self) -> None:
        t = [1000.0]
        rl = LoginRateLimiter(failure_limit=3, window_seconds=60, clock=_clock(t))
        for _ in range(2):
            rl.record_failure(ip="1.2.3.4", username="admin")
        decision = rl.check(ip="1.2.3.4", username="admin")
        assert decision.allowed is True

    def test_at_limit_denied(self) -> None:
        t = [1000.0]
        rl = LoginRateLimiter(failure_limit=3, window_seconds=60, clock=_clock(t))
        for _ in range(3):
            rl.record_failure(ip="1.2.3.4", username="admin")
        decision = rl.check(ip="1.2.3.4", username="admin")
        assert decision.allowed is False
        assert decision.retry_after_seconds >= 1

    def test_window_expiry_resets(self) -> None:
        t = [1000.0]
        rl = LoginRateLimiter(failure_limit=2, window_seconds=60, clock=_clock(t))
        rl.record_failure(ip="1.2.3.4", username="admin")
        rl.record_failure(ip="1.2.3.4", username="admin")
        assert rl.check(ip="1.2.3.4", username="admin").allowed is False
        t[0] += 61  # advance past the window
        assert rl.check(ip="1.2.3.4", username="admin").allowed is True


class TestPerUsernameBucket:
    def test_different_ips_share_username_bucket(self) -> None:
        """Distributed attack: 3 different IPs each fail once → the
        username bucket fills, and a 4th IP is blocked."""
        t = [1000.0]
        rl = LoginRateLimiter(failure_limit=3, window_seconds=60, clock=_clock(t))
        rl.record_failure(ip="1.0.0.1", username="admin")
        rl.record_failure(ip="2.0.0.2", username="admin")
        rl.record_failure(ip="3.0.0.3", username="admin")
        decision = rl.check(ip="4.0.0.4", username="admin")
        assert decision.allowed is False
        assert decision.retry_after_seconds >= 1

    def test_different_username_unaffected(self) -> None:
        t = [1000.0]
        rl = LoginRateLimiter(failure_limit=3, window_seconds=60, clock=_clock(t))
        for _ in range(3):
            rl.record_failure(ip="1.2.3.4", username="admin")
        # Different username, same IP — IP bucket is full, but check
        # is for a different (ip, username). Wait — actually the IP
        # bucket counts ALL attempts from that IP regardless of
        # username, so this should still be denied.
        decision = rl.check(ip="1.2.3.4", username="other")
        assert decision.allowed is False
        # But from a different IP and different username: allowed.
        assert rl.check(ip="9.9.9.9", username="other").allowed is True


class TestUsernameNormalization:
    def test_case_variations_share_bucket(self) -> None:
        """Username 'Admin' and 'admin' must share the same bucket."""
        t = [1000.0]
        rl = LoginRateLimiter(failure_limit=2, window_seconds=60, clock=_clock(t))
        rl.record_failure(ip="1.1.1.1", username="ADMIN")
        rl.record_failure(ip="2.2.2.2", username="Admin")
        # Username bucket now has 2 entries (both normalized to "admin").
        decision = rl.check(ip="3.3.3.3", username="admin")
        assert decision.allowed is False


class TestRetryAfter:
    def test_retry_after_is_at_least_one(self) -> None:
        """Even when the remaining window is sub-second, return >= 1."""
        t = [1000.0]
        rl = LoginRateLimiter(failure_limit=1, window_seconds=60, clock=_clock(t))
        rl.record_failure(ip="1.1.1.1", username="admin")
        t[0] = 1059.5  # 0.5s left in window
        decision = rl.check(ip="1.1.1.1", username="admin")
        assert decision.allowed is False
        assert decision.retry_after_seconds == 1

    def test_retry_after_uses_worst_bucket(self) -> None:
        """When both buckets are full but with different age profiles,
        retry-after reflects the bucket that takes longer to free up."""
        t = [1000.0]
        rl = LoginRateLimiter(failure_limit=1, window_seconds=100, clock=_clock(t))
        rl.record_failure(ip="1.1.1.1", username="admin")
        # Move 50 s into the window, then record a failure with the
        # same IP but a different username.
        t[0] = 1050.0
        rl.record_failure(ip="1.1.1.1", username="other")
        # Now both the IP and the "other" username buckets are
        # at limit; for (1.1.1.1, "other"), the IP bucket entry is at
        # t=1000, "other" username at t=1050. The IP bucket frees first
        # (at t=1100); the "other" username at t=1150.
        # Worst case (the username bucket) → retry_after ≈ 100 s.
        decision = rl.check(ip="1.1.1.1", username="other")
        assert decision.allowed is False
        # Max age: 100 seconds away.
        assert decision.retry_after_seconds == 100


class TestReset:
    def test_reset_drops_everything(self) -> None:
        t = [1000.0]
        rl = LoginRateLimiter(failure_limit=1, window_seconds=60, clock=_clock(t))
        rl.record_failure(ip="1.1.1.1", username="admin")
        rl.reset()
        assert rl.check(ip="1.1.1.1", username="admin").allowed is True


class TestUnboundedGrowthFootgun:
    """Hex Audit footgun-hunter F2 (2026-05-18): the limiter must not let
    bucket dicts grow without bound under a flood of rotating keys.
    """

    def test_check_only_does_not_materialize_buckets(self) -> None:
        # `check` is non-mutating: a probe with no prior failure must
        # NOT inflate `_by_ip` or `_by_username`. The earlier
        # `defaultdict(deque)` design materialized empty buckets on
        # every check, giving an attacker an O(unique-IPs) memory cost
        # at zero attempt cost.
        rl = LoginRateLimiter()
        for i in range(500):
            rl.check(ip=f"10.0.0.{i}", username=f"u{i}")
        assert len(rl._by_ip) == 0
        assert len(rl._by_username) == 0

    def test_empty_buckets_pruned_after_window(self) -> None:
        # A bucket that pruned to empty must be deleted, not lingered.
        # Without this, only the cap (10_000) bounds memory; with it,
        # cold keys vacate on the first read after their window expires.
        t = [1000.0]
        rl = LoginRateLimiter(failure_limit=3, window_seconds=60, clock=_clock(t))
        rl.record_failure(ip="1.2.3.4", username="alice")
        assert "1.2.3.4" in rl._by_ip
        t[0] += 120  # past the window
        rl.check(ip="1.2.3.4", username="alice")
        assert "1.2.3.4" not in rl._by_ip
        assert "alice" not in rl._by_username

    def test_bucket_count_capped_at_max_tracked_buckets(self) -> None:
        # Hard ceiling: MAX_TRACKED_BUCKETS. Under a flood of unique
        # IPs, dict size stays bounded; oldest entries get FIFO-evicted.
        from app.auth.rate_limit import MAX_TRACKED_BUCKETS

        rl = LoginRateLimiter()
        for i in range(MAX_TRACKED_BUCKETS + 250):
            rl.record_failure(ip=f"10.{i // 65536}.{(i // 256) % 256}.{i % 256}", username=f"u{i}")
        assert len(rl._by_ip) == MAX_TRACKED_BUCKETS
        assert len(rl._by_username) == MAX_TRACKED_BUCKETS

    def test_eviction_drops_oldest_keeps_recent(self) -> None:
        # The (cap+1)th distinct key evicts the FIRST one; the most
        # recent N keys stay. Hex Audit CR2-H3 (2026-05-18): the cap
        # is a constructor parameter now, so the test injects a small
        # cap directly instead of monkey-patching the module global
        # (the prior test only worked because `_materialize` read the
        # constant as a bare-name global; it would have silently broken
        # if the cap ever moved onto `self`).
        rl = LoginRateLimiter(max_buckets=5)
        for i in range(7):  # 7 distinct keys, cap is 5
            rl.record_failure(ip=f"10.0.0.{i}", username=f"u{i}")
        assert len(rl._by_ip) == 5
        # First two keys evicted, last five retained.
        assert "10.0.0.0" not in rl._by_ip
        assert "10.0.0.1" not in rl._by_ip
        assert "10.0.0.6" in rl._by_ip

    def test_max_buckets_zero_rejected(self) -> None:
        import pytest

        with pytest.raises(ValueError, match=">= 1"):
            LoginRateLimiter(max_buckets=0)


class TestRateLimitDecision:
    def test_allowed_decision_zero_retry(self) -> None:
        d = RateLimitDecision(allowed=True, retry_after_seconds=0)
        assert d.allowed is True
        assert d.retry_after_seconds == 0


class TestDefaultConstants:
    def test_defaults_match_adr(self) -> None:
        assert DEFAULT_FAILURE_LIMIT == 5
        assert DEFAULT_WINDOW_SECONDS == 15 * 60


class TestExtractClientIp:
    def test_no_peer_returns_unknown(self) -> None:
        assert (
            extract_client_ip(peer_ip=None, forwarded_for=None, trusted_proxies=())
            == UNKNOWN_CLIENT_IP
        )
        assert (
            extract_client_ip(peer_ip="", forwarded_for=None, trusted_proxies=())
            == UNKNOWN_CLIENT_IP
        )

    def test_no_trusted_proxies_returns_peer(self) -> None:
        assert (
            extract_client_ip(peer_ip="1.2.3.4", forwarded_for="9.9.9.9", trusted_proxies=())
            == "1.2.3.4"
        )

    def test_peer_not_in_trusted_set_returns_peer(self) -> None:
        trusted = (ipaddress.ip_network("127.0.0.1/32"),)
        assert (
            extract_client_ip(
                peer_ip="1.2.3.4",
                forwarded_for="9.9.9.9",
                trusted_proxies=trusted,
            )
            == "1.2.3.4"
        )

    def test_trusted_peer_with_single_forwarded_hop(self) -> None:
        trusted = (ipaddress.ip_network("127.0.0.1/32"),)
        assert (
            extract_client_ip(
                peer_ip="127.0.0.1",
                forwarded_for="203.0.113.1",
                trusted_proxies=trusted,
            )
            == "203.0.113.1"
        )

    def test_walks_right_to_left_skipping_trusted(self) -> None:
        trusted = (
            ipaddress.ip_network("127.0.0.1/32"),
            ipaddress.ip_network("10.0.0.0/8"),
        )
        # Chain: client(203.0.113.5) → edge(10.0.0.1) → local(127.0.0.1)
        # X-Forwarded-For = "203.0.113.5, 10.0.0.1"
        # We walk right-to-left: 10.0.0.1 is trusted, skip;
        # 203.0.113.5 is not trusted, return it.
        assert (
            extract_client_ip(
                peer_ip="127.0.0.1",
                forwarded_for="203.0.113.5, 10.0.0.1",
                trusted_proxies=trusted,
            )
            == "203.0.113.5"
        )

    def test_all_hops_trusted_falls_back_to_peer(self) -> None:
        trusted = (
            ipaddress.ip_network("127.0.0.1/32"),
            ipaddress.ip_network("10.0.0.0/8"),
        )
        assert (
            extract_client_ip(
                peer_ip="127.0.0.1",
                forwarded_for="10.1.2.3, 10.4.5.6",
                trusted_proxies=trusted,
            )
            == "127.0.0.1"
        )

    def test_malformed_entries_skipped(self) -> None:
        trusted = (ipaddress.ip_network("127.0.0.1/32"),)
        assert (
            extract_client_ip(
                peer_ip="127.0.0.1",
                forwarded_for="garbage, 203.0.113.1",
                trusted_proxies=trusted,
            )
            == "203.0.113.1"
        )

    def test_ipv4_with_port_stripped(self) -> None:
        trusted = (ipaddress.ip_network("127.0.0.1/32"),)
        assert (
            extract_client_ip(
                peer_ip="127.0.0.1",
                forwarded_for="203.0.113.1:12345",
                trusted_proxies=trusted,
            )
            == "203.0.113.1"
        )

    def test_ipv6_bracketed_with_port_stripped(self) -> None:
        trusted = (ipaddress.ip_network("127.0.0.1/32"),)
        assert (
            extract_client_ip(
                peer_ip="127.0.0.1",
                forwarded_for="[2001:db8::1]:12345",
                trusted_proxies=trusted,
            )
            == "2001:db8::1"
        )

    def test_bare_ipv6_without_port_preserved(self) -> None:
        """Per Phase 3 sec review #2: a parseable bare IPv6 must
        round-trip unchanged. The previous `count(":") == 1` rule
        worked for canonical IPv6 only by accident (multiple colons
        → unchanged → OK). The new rule parses first via
        `ipaddress.ip_address` so the behavior is intentional.
        """
        trusted = (ipaddress.ip_network("127.0.0.1/32"),)
        assert (
            extract_client_ip(
                peer_ip="127.0.0.1",
                forwarded_for="2001:db8::1",
                trusted_proxies=trusted,
            )
            == "2001:db8::1"
        )

    def test_ambiguous_unbracketed_ipv6_with_trailing_group(self) -> None:
        """`2001:db8::1:443` is ambiguous — either a valid IPv6 with
        last group `0x443` (which `ipaddress.ip_address` accepts) or
        an unbracketed IPv6+port. The new rule preserves the
        parseable-as-IP form so the rate-limit bucket is stable per
        client even if a misbehaving proxy varies the "port" suffix.
        Bracketed `[2001:db8::1]:443` is the canonical way to send
        IPv6 + port; an unbracketed form is treated as the address.
        """
        trusted = (ipaddress.ip_network("127.0.0.1/32"),)
        # The address parses as a valid IPv6, so it's kept verbatim.
        result = extract_client_ip(
            peer_ip="127.0.0.1",
            forwarded_for="2001:db8::1:443",
            trusted_proxies=trusted,
        )
        assert result == "2001:db8::1:443"

    def test_ipv6_trusted_peer(self) -> None:
        trusted = (ipaddress.ip_network("::1/128"),)
        assert (
            extract_client_ip(
                peer_ip="::1",
                forwarded_for="203.0.113.1",
                trusted_proxies=trusted,
            )
            == "203.0.113.1"
        )

    def test_empty_forwarded_for_returns_peer(self) -> None:
        trusted = (ipaddress.ip_network("127.0.0.1/32"),)
        assert (
            extract_client_ip(
                peer_ip="127.0.0.1",
                forwarded_for="",
                trusted_proxies=trusted,
            )
            == "127.0.0.1"
        )
