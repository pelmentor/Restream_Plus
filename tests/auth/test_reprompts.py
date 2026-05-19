"""Tests for the in-memory reprompt grant store."""

from __future__ import annotations

import pytest
from app.auth.reprompts import (
    REPROMPT_GRANT_ID_BYTES,
    REPROMPT_GRANT_TTL_SECONDS,
    RepromptScope,
    RepromptStore,
)


def _clock(value: list[float]) -> object:
    def now() -> float:
        return value[0]

    return now


class TestRepromptStoreConstruction:
    def test_invalid_ttl_rejected(self) -> None:
        with pytest.raises(ValueError, match="ttl_seconds"):
            RepromptStore(ttl_seconds=0)
        with pytest.raises(ValueError, match="ttl_seconds"):
            RepromptStore(ttl_seconds=-1)

    def test_invalid_max_grants_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_grants"):
            RepromptStore(max_grants=0)
        with pytest.raises(ValueError, match="max_grants"):
            RepromptStore(max_grants=-1)


class TestUnboundedGrowthFootgun:
    """Hex Audit footgun-hunter FG2-C4 (2026-05-18): the `_grants` dict
    must not grow without bound under a runaway issuer between
    `prune_expired` ticks. Sibling of `LoginRateLimiter` MAX_TRACKED_BUCKETS
    coverage."""

    def test_issue_evicts_oldest_when_at_cap(self) -> None:
        store = RepromptStore(max_grants=5)
        ids: list[str] = []
        for _ in range(7):  # 7 issues, cap is 5
            ids.append(store.issue(user_id="u1", scope=RepromptScope.DELETE_TARGET))
        # FIFO: first two evicted, last five retained.
        assert len(store._grants) == 5
        for gid in ids[:2]:
            assert (
                store.consume(grant_id=gid, user_id="u1", scope=RepromptScope.DELETE_TARGET)
                is False
            )
        # The latest grant must still be consumable.
        assert (
            store.consume(grant_id=ids[-1], user_id="u1", scope=RepromptScope.DELETE_TARGET) is True
        )

    def test_cap_holds_under_high_volume(self) -> None:
        store = RepromptStore(max_grants=100)
        for _ in range(5_000):
            store.issue(user_id="u1", scope=RepromptScope.DELETE_TARGET)
        assert len(store._grants) == 100


class TestIssue:
    def test_returns_url_safe_grant_id(self) -> None:
        store = RepromptStore()
        gid = store.issue(user_id="u1", scope=RepromptScope.REVEAL_STREAM_KEY)
        assert isinstance(gid, str)
        assert len(gid) > 0
        assert all(c.isalnum() or c in "-_" for c in gid)

    def test_distinct_ids(self) -> None:
        store = RepromptStore()
        ids = {store.issue(user_id="u1", scope=RepromptScope.DELETE_TARGET) for _ in range(50)}
        assert len(ids) == 50

    def test_rejects_non_enum_scope(self) -> None:
        store = RepromptStore()
        with pytest.raises(TypeError, match="RepromptScope"):
            store.issue(user_id="u1", scope="reveal_stream_key")  # type: ignore[arg-type]

    def test_rejects_empty_user_id(self) -> None:
        store = RepromptStore()
        with pytest.raises(ValueError, match="user_id"):
            store.issue(user_id="", scope=RepromptScope.DELETE_TARGET)


class TestConsume:
    def test_valid_grant_consumes(self) -> None:
        store = RepromptStore()
        gid = store.issue(user_id="u1", scope=RepromptScope.DELETE_TARGET)
        assert store.consume(grant_id=gid, user_id="u1", scope=RepromptScope.DELETE_TARGET) is True

    def test_single_use(self) -> None:
        store = RepromptStore()
        gid = store.issue(user_id="u1", scope=RepromptScope.DELETE_TARGET)
        assert store.consume(grant_id=gid, user_id="u1", scope=RepromptScope.DELETE_TARGET) is True
        # Second attempt against the same grant fails.
        assert store.consume(grant_id=gid, user_id="u1", scope=RepromptScope.DELETE_TARGET) is False

    def test_unknown_grant_returns_false(self) -> None:
        store = RepromptStore()
        assert (
            store.consume(
                grant_id="never-issued",
                user_id="u1",
                scope=RepromptScope.DELETE_TARGET,
            )
            is False
        )

    def test_wrong_user_returns_false_and_burns(self) -> None:
        """A probing attacker using a stolen grant ID cannot reuse it
        even when they get the user wrong on first try."""
        store = RepromptStore()
        gid = store.issue(user_id="u1", scope=RepromptScope.DELETE_TARGET)
        assert store.consume(grant_id=gid, user_id="u2", scope=RepromptScope.DELETE_TARGET) is False
        # Even the correct user can no longer consume — the wrong
        # attempt burned the grant.
        assert store.consume(grant_id=gid, user_id="u1", scope=RepromptScope.DELETE_TARGET) is False

    def test_wrong_scope_returns_false_and_burns(self) -> None:
        store = RepromptStore()
        gid = store.issue(user_id="u1", scope=RepromptScope.DELETE_TARGET)
        assert (
            store.consume(grant_id=gid, user_id="u1", scope=RepromptScope.REVEAL_STREAM_KEY)
            is False
        )
        assert store.consume(grant_id=gid, user_id="u1", scope=RepromptScope.DELETE_TARGET) is False

    def test_expired_grant_returns_false(self) -> None:
        t = [1000.0]
        store = RepromptStore(ttl_seconds=60, clock=_clock(t))
        gid = store.issue(user_id="u1", scope=RepromptScope.DELETE_TARGET)
        t[0] = 1061.0  # 61 s elapsed > 60 s TTL
        assert store.consume(grant_id=gid, user_id="u1", scope=RepromptScope.DELETE_TARGET) is False

    def test_consume_rejects_non_enum_scope(self) -> None:
        store = RepromptStore()
        gid = store.issue(user_id="u1", scope=RepromptScope.DELETE_TARGET)
        with pytest.raises(TypeError, match="RepromptScope"):
            store.consume(grant_id=gid, user_id="u1", scope="delete_target")  # type: ignore[arg-type]


class TestPruneExpired:
    def test_drops_only_expired(self) -> None:
        t = [1000.0]
        store = RepromptStore(ttl_seconds=60, clock=_clock(t))
        old = store.issue(user_id="u1", scope=RepromptScope.DELETE_TARGET)
        t[0] = 1050.0
        new = store.issue(user_id="u1", scope=RepromptScope.DELETE_TARGET)
        t[0] = 1061.0  # `old` is expired (61 s elapsed); `new` (11 s) is fresh.
        pruned = store.prune_expired()
        assert pruned == 1
        # `new` still consumable, `old` is gone.
        assert store.consume(grant_id=new, user_id="u1", scope=RepromptScope.DELETE_TARGET) is True
        assert store.consume(grant_id=old, user_id="u1", scope=RepromptScope.DELETE_TARGET) is False

    def test_idempotent(self) -> None:
        store = RepromptStore()
        assert store.prune_expired() == 0
        store.issue(user_id="u1", scope=RepromptScope.DELETE_TARGET)
        assert store.prune_expired() == 0


class TestReset:
    def test_reset_drops_everything(self) -> None:
        store = RepromptStore()
        gid = store.issue(user_id="u1", scope=RepromptScope.DELETE_TARGET)
        store.reset()
        assert store.consume(grant_id=gid, user_id="u1", scope=RepromptScope.DELETE_TARGET) is False


class TestDefaultConstants:
    def test_ttl_matches_adr(self) -> None:
        assert REPROMPT_GRANT_TTL_SECONDS == 60.0

    def test_grant_id_entropy(self) -> None:
        """32 random bytes = 256 bits, plenty for a 60-second grant."""
        assert REPROMPT_GRANT_ID_BYTES >= 16
