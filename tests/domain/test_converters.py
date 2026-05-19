"""TargetDTO/CredentialDTO -> Target adapter tests.

Lives under tests/domain/ even though the converter lives in
app/repositories/_converters.py — the conversion is the boundary
between persistence and domain, and the consumers care about the
domain shape.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.target_types import TargetType
from app.repositories._converters import target_dto_to_domain
from app.repositories.credentials import CredentialDTO
from app.repositories.targets import TargetDTO

T0 = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)


def _target_dto(
    target_type: str = "twitch",
    *,
    enabled: bool = True,
    settings: dict[str, object] | None = None,
) -> TargetDTO:
    return TargetDTO.model_validate(
        {
            "id": "tgt-1",
            "type": target_type,
            "label": "Twitch main",
            "url": "rtmp://live.twitch.tv/app",
            "enabled": enabled,
            "settings": settings or {},
            "created_at": T0,
            "updated_at": T0,
        }
    )


def _credential_dto() -> CredentialDTO:
    return CredentialDTO.model_validate(
        {
            "id": "cred-1",
            "target_id": "tgt-1",
            "lifetime": "persistent",
            "ciphertext": b"\x00" * 32,
            "nonce": b"\x00" * 12,
            "salt": b"\x00" * 16,
            "last4": "ABCD",
            "created_at": T0,
            "expires_at": None,
        }
    )


class TestBasicConversion:
    def test_target_with_credential(self) -> None:
        domain = target_dto_to_domain(_target_dto(), _credential_dto())
        assert domain.id == "tgt-1"
        assert domain.type is TargetType.TWITCH
        assert domain.label == "Twitch main"
        assert domain.url == "rtmp://live.twitch.tv/app"
        assert domain.enabled is True
        assert domain.has_active_credential is True

    def test_target_without_credential(self) -> None:
        domain = target_dto_to_domain(_target_dto(), credential=None)
        assert domain.has_active_credential is False

    def test_disabled_target(self) -> None:
        domain = target_dto_to_domain(_target_dto(enabled=False))
        assert domain.enabled is False


class TestBackupEnabledExtraction:
    """Hex Audit SA-F14 (slice 10): the per-target `backup_enabled` JSON
    flag is gated by `youtube_backup_enabled` (config-level kwarg).
    Tests pass the kwarg explicitly when verifying the per-target flag
    is honored; the default (False) verifies the gate is closed.
    """

    def test_backup_enabled_true_with_config_gate_open(self) -> None:
        domain = target_dto_to_domain(
            _target_dto(target_type="youtube", settings={"backup_enabled": True}),
            youtube_backup_enabled=True,
        )
        assert domain.backup_enabled is True

    def test_backup_enabled_true_but_config_gate_closed_yields_false(self) -> None:
        # SA-F14 gate: even with the per-target flag set, the
        # config-level flag must also be on. Default is False.
        domain = target_dto_to_domain(
            _target_dto(target_type="youtube", settings={"backup_enabled": True}),
        )
        assert domain.backup_enabled is False

    def test_backup_enabled_false(self) -> None:
        domain = target_dto_to_domain(
            _target_dto(target_type="youtube", settings={"backup_enabled": False}),
            youtube_backup_enabled=True,
        )
        assert domain.backup_enabled is False

    def test_backup_missing_defaults_false(self) -> None:
        domain = target_dto_to_domain(
            _target_dto(target_type="youtube", settings={}),
            youtube_backup_enabled=True,
        )
        assert domain.backup_enabled is False

    def test_backup_non_bool_value_coerced(self) -> None:
        # bool() of a non-empty string → True; of empty → False.
        # We tolerate sloppy persisted values rather than crashing.
        domain = target_dto_to_domain(
            _target_dto(target_type="youtube", settings={"backup_enabled": "yes"}),
            youtube_backup_enabled=True,
        )
        assert domain.backup_enabled is True


class TestTypeStringValidation:
    def test_valid_type_string(self) -> None:
        for type_str in ("twitch", "youtube", "kick", "vk_live", "custom"):
            domain = target_dto_to_domain(_target_dto(type_str))
            assert domain.type is TargetType(type_str)

    def test_invalid_type_raises(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="not a valid TargetType"):
            target_dto_to_domain(_target_dto("nonexistent_platform"))
