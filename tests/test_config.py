"""AppSettings env binding + validation.

Focus areas:
- Defaults reflect ADR choices.
- env mode requires a passphrase; paste mode forbids it (ADR-0010).
- SecretStr hides its value in repr (so a structlog dump of `settings`
  cannot leak the master passphrase).
- Reset flag pairing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.config import (
    MIN_ADMIN_PASSWORD_LENGTH,
    MIN_PASSPHRASE_LENGTH,
    AppSettings,
)
from pydantic import SecretStr, ValidationError


@pytest.mark.usefixtures("restream_clean_env")
class TestDefaults:
    def test_paste_mode_loads_without_passphrase(self) -> None:
        settings = AppSettings(passphrase_source="paste")
        assert settings.passphrase_source == "paste"
        assert settings.master_passphrase is None
        assert settings.bind_host == "0.0.0.0"
        assert settings.bind_port == 8000
        assert settings.log_level == "INFO"
        assert settings.log_format == "json"

    def test_data_dir_paths_are_consistent(self) -> None:
        settings = AppSettings(passphrase_source="paste", data_dir="/tmp/x")
        assert settings.db_path == settings.data_dir / "restream.db"
        assert settings.kdf_salt_path == settings.data_dir / ".kdf_salt"
        assert settings.kdf_salt_previous_path == settings.data_dir / ".kdf_salt.previous"
        assert settings.log_dir == settings.data_dir / "logs"

    def test_default_data_dir_is_slash_data(self) -> None:
        # ADR-0007 contract: the volume mount point is /data. A refactor that
        # changed the default to anything else would break the documented
        # docker run / deployment instructions.
        settings = AppSettings(passphrase_source="paste")
        assert settings.data_dir == Path("/data")


@pytest.mark.usefixtures("restream_clean_env")
class TestPassphraseSourceInvariants:
    def test_env_mode_requires_passphrase(self) -> None:
        with pytest.raises(ValidationError, match="requires"):
            AppSettings(passphrase_source="env", master_passphrase=None)

    def test_env_mode_rejects_short_passphrase(self) -> None:
        too_short = "x" * (MIN_PASSPHRASE_LENGTH - 1)
        with pytest.raises(ValidationError, match=str(MIN_PASSPHRASE_LENGTH)):
            AppSettings(
                passphrase_source="env",
                master_passphrase=SecretStr(too_short),
            )

    def test_env_mode_accepts_minimum_length_passphrase(self) -> None:
        ok = "x" * MIN_PASSPHRASE_LENGTH
        settings = AppSettings(
            passphrase_source="env",
            master_passphrase=SecretStr(ok),
        )
        assert settings.master_passphrase is not None
        assert settings.master_passphrase.get_secret_value() == ok

    def test_paste_mode_refuses_passphrase_set(self) -> None:
        # ADR-0010 — refuse ambiguity, do not silently ignore.
        with pytest.raises(ValidationError, match="forbids"):
            AppSettings(
                passphrase_source="paste",
                master_passphrase=SecretStr("any-non-none-value-12chars"),
            )

    def test_unknown_passphrase_source_is_rejected(self) -> None:
        # `sealed` is reserved in the ADR but not yet a legal value.
        with pytest.raises(ValidationError):
            AppSettings(passphrase_source="sealed")


@pytest.mark.usefixtures("restream_clean_env")
class TestEnvBinding:
    def test_env_vars_are_read_with_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RESTREAM_PASSPHRASE_SOURCE", "env")
        monkeypatch.setenv("RESTREAM_MASTER_PASSPHRASE", "correct-horse-battery-staple")
        monkeypatch.setenv("RESTREAM_BIND_PORT", "9001")
        monkeypatch.setenv("RESTREAM_LOG_FORMAT", "console")

        settings = AppSettings()

        assert settings.passphrase_source == "env"
        assert settings.master_passphrase is not None
        assert settings.master_passphrase.get_secret_value() == "correct-horse-battery-staple"
        assert settings.bind_port == 9001
        assert settings.log_format == "console"

    def test_env_var_names_are_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("restream_passphrase_source", "paste")
        settings = AppSettings()
        assert settings.passphrase_source == "paste"


@pytest.mark.usefixtures("restream_clean_env")
class TestSecretMasking:
    def test_repr_masks_master_passphrase(self) -> None:
        settings = AppSettings(
            passphrase_source="env",
            master_passphrase=SecretStr("super-secret-value-not-leaked"),
        )
        rendered = repr(settings)
        assert "super-secret-value-not-leaked" not in rendered
        assert "**********" in rendered

    def test_model_dump_masks_master_passphrase(self) -> None:
        settings = AppSettings(
            passphrase_source="env",
            master_passphrase=SecretStr("super-secret-value-not-leaked"),
        )
        dumped = settings.model_dump()
        # The value type is preserved (SecretStr), not the cleartext.
        assert isinstance(dumped["master_passphrase"], SecretStr)
        assert "super-secret-value-not-leaked" not in str(dumped)


@pytest.mark.usefixtures("restream_clean_env")
class TestAdminPasswordRules:
    def test_short_admin_password_is_rejected(self) -> None:
        too_short = "x" * (MIN_ADMIN_PASSWORD_LENGTH - 1)
        with pytest.raises(ValidationError, match=str(MIN_ADMIN_PASSWORD_LENGTH)):
            AppSettings(
                passphrase_source="paste",
                admin_password=SecretStr(too_short),
            )

    def test_reset_flag_requires_password_value(self) -> None:
        with pytest.raises(ValidationError, match="RESET_ADMIN_PASSWORD"):
            AppSettings(
                passphrase_source="paste",
                reset_admin_password=True,
                admin_password=None,
            )

    def test_reset_flag_with_password_is_accepted(self) -> None:
        settings = AppSettings(
            passphrase_source="paste",
            reset_admin_password=True,
            admin_password=SecretStr("new-admin-passphrase-1"),
        )
        assert settings.reset_admin_password is True


@pytest.mark.usefixtures("restream_clean_env")
class TestTrustedProxies:
    def test_default_is_empty_tuple(self) -> None:
        settings = AppSettings(passphrase_source="paste")
        assert settings.trusted_proxies == ()

    def test_empty_string_parses_to_empty_tuple(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RESTREAM_PASSPHRASE_SOURCE", "paste")
        monkeypatch.setenv("RESTREAM_TRUSTED_PROXIES", "")
        settings = AppSettings()
        assert settings.trusted_proxies == ()

    def test_single_cidr_parses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RESTREAM_PASSPHRASE_SOURCE", "paste")
        monkeypatch.setenv("RESTREAM_TRUSTED_PROXIES", "127.0.0.1/32")
        settings = AppSettings()
        assert len(settings.trusted_proxies) == 1
        assert str(settings.trusted_proxies[0]) == "127.0.0.1/32"

    def test_single_host_normalised_to_cidr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RESTREAM_PASSPHRASE_SOURCE", "paste")
        monkeypatch.setenv("RESTREAM_TRUSTED_PROXIES", "127.0.0.1")
        settings = AppSettings()
        assert str(settings.trusted_proxies[0]) == "127.0.0.1/32"

    def test_multiple_entries_parse(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RESTREAM_PASSPHRASE_SOURCE", "paste")
        monkeypatch.setenv("RESTREAM_TRUSTED_PROXIES", "127.0.0.1/32, 10.0.0.0/8")
        settings = AppSettings()
        assert len(settings.trusted_proxies) == 2

    def test_ipv6_cidr_parses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RESTREAM_PASSPHRASE_SOURCE", "paste")
        monkeypatch.setenv("RESTREAM_TRUSTED_PROXIES", "::1/128, fe80::/10")
        settings = AppSettings()
        assert len(settings.trusted_proxies) == 2

    def test_garbage_entry_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RESTREAM_PASSPHRASE_SOURCE", "paste")
        monkeypatch.setenv("RESTREAM_TRUSTED_PROXIES", "not-an-ip")
        with pytest.raises(ValidationError, match="not a valid CIDR"):
            AppSettings()
