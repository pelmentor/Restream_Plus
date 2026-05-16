"""Per-type spec catalogue tests.

Pins URLs against `docs/architecture/platforms-reference.md`. A drift
here is intentional only if the platforms doc moved first.
"""

from __future__ import annotations

import pytest
from app.domain.credential_policy import CredentialLifetime
from app.domain.target_types import (
    TARGET_TYPE_SPECS,
    FormField,
    TargetType,
    TargetTypeSpec,
    spec_for,
)


class TestExhaustiveness:
    def test_every_target_type_has_a_spec(self) -> None:
        missing = set(TargetType) - set(TARGET_TYPE_SPECS)
        assert not missing, f"missing TargetTypeSpec for: {missing}"

    def test_no_orphan_specs(self) -> None:
        # Specs without an enum value would be unreachable.
        orphans = set(TARGET_TYPE_SPECS) - set(TargetType)
        assert not orphans

    def test_spec_for_returns_correct_spec(self) -> None:
        for target_type in TargetType:
            spec = spec_for(target_type)
            assert spec.type is target_type
            assert isinstance(spec, TargetTypeSpec)


class TestPinnedDefaults:
    """Pinned values per platforms-reference.md."""

    def test_twitch_default_url(self) -> None:
        assert spec_for(TargetType.TWITCH).default_url == "rtmp://live.twitch.tv/app"

    def test_youtube_primary_url_uses_rtmps_443(self) -> None:
        assert spec_for(TargetType.YOUTUBE).default_url == "rtmps://a.rtmps.youtube.com:443/live2"

    def test_kick_default_url(self) -> None:
        assert (
            spec_for(TargetType.KICK).default_url
            == "rtmps://fa723fc1b171.global-contribute.live-video.net:443/app"
        )

    def test_vk_default_url(self) -> None:
        assert spec_for(TargetType.VK_LIVE).default_url == "rtmp://ovsu.okcdn.ru/input/"

    def test_custom_default_url_is_empty(self) -> None:
        assert spec_for(TargetType.CUSTOM).default_url == ""


class TestBackupSupport:
    def test_only_youtube_supports_backup(self) -> None:
        assert spec_for(TargetType.YOUTUBE).backup_supported is True
        for target_type in TargetType:
            if target_type is TargetType.YOUTUBE:
                continue
            assert spec_for(target_type).backup_supported is False, target_type


class TestLifetimes:
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
    def test_lifetime_in_spec_matches_credential_policy(
        self,
        target_type: TargetType,
        expected: CredentialLifetime,
    ) -> None:
        assert spec_for(target_type).credential_lifetime is expected


class TestSettingsFields:
    def test_vk_omits_stream_key_field(self) -> None:
        # The VK Settings tab does NOT have a stream-key field per
        # platforms-reference.md "VK design implications" #4.
        assert FormField.STREAM_KEY not in spec_for(TargetType.VK_LIVE).settings_fields

    def test_youtube_includes_backup_field(self) -> None:
        assert FormField.BACKUP in spec_for(TargetType.YOUTUBE).settings_fields

    def test_twitch_includes_region_field(self) -> None:
        # Twitch ingest dropdown per platforms-reference.md.
        assert FormField.REGION in spec_for(TargetType.TWITCH).settings_fields

    def test_persistent_types_include_stream_key_field(self) -> None:
        for target_type in (
            TargetType.TWITCH,
            TargetType.YOUTUBE,
            TargetType.KICK,
            TargetType.CUSTOM,
        ):
            assert FormField.STREAM_KEY in spec_for(target_type).settings_fields, target_type


class TestImmutability:
    def test_specs_are_frozen(self) -> None:
        spec = spec_for(TargetType.TWITCH)
        with pytest.raises(AttributeError):
            spec.display_label = "Twitch (modified)"  # type: ignore[misc]

    def test_settings_fields_is_frozenset(self) -> None:
        assert isinstance(spec_for(TargetType.TWITCH).settings_fields, frozenset)
