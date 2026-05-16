"""CredentialLifetime + lifetime_for tests."""

from __future__ import annotations

import pytest
from app.domain.credential_policy import CredentialLifetime, lifetime_for
from app.domain.target_types import TargetType


class TestCredentialLifetimeEnum:
    def test_values_match_adr_0009(self) -> None:
        assert CredentialLifetime.PERSISTENT.value == "persistent"
        assert CredentialLifetime.PER_SESSION.value == "per_session"
        assert CredentialLifetime.REFRESH_TOKEN.value == "refresh_token"


class TestLifetimeForMapping:
    """ADR-0009 §`TARGET_TYPE_TO_CREDENTIAL_LIFETIME`."""

    @pytest.mark.parametrize(
        ("target_type", "expected"),
        [
            (TargetType.TWITCH, CredentialLifetime.PERSISTENT),
            (TargetType.YOUTUBE, CredentialLifetime.PERSISTENT),
            (TargetType.KICK, CredentialLifetime.PERSISTENT),
            (TargetType.VK_LIVE, CredentialLifetime.PER_SESSION),
            (TargetType.CUSTOM, CredentialLifetime.PERSISTENT),
        ],
    )
    def test_per_type_lifetime(
        self,
        target_type: TargetType,
        expected: CredentialLifetime,
    ) -> None:
        assert lifetime_for(target_type) is expected

    def test_every_target_type_resolved(self) -> None:
        # No KeyError for any enum value.
        for target_type in TargetType:
            assert isinstance(lifetime_for(target_type), CredentialLifetime)
