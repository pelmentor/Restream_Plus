"""Process-wide settings, loaded from `RESTREAM_*` environment variables.

The settings object is the only place the env is read. Everything
downstream (DB engine, crypto, HTTP server, supervisor) takes an
explicit `AppSettings` parameter — no module-level env reads, no
hidden globals.

ADR references:
- ADR-0001 (stack)
- ADR-0005 (auth bootstrap env vars)
- ADR-0006 (passphrase is root of trust)
- ADR-0007 (data dir layout, log retention)
- ADR-0010 (passphrase source modes — env vs paste)

`SecretStr` is used for every credential-like field so that an
accidental `repr()` / structlog event / FastAPI debug dump prints
`SecretStr('**********')` rather than the value itself. Callers
unwrap via `.get_secret_value()` only at the point of use.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

PassphraseSource = Literal["env", "paste"]
"""Selector for ADR-0010 passphrase-source mode.

`env`   — read `RESTREAM_MASTER_PASSPHRASE`; service comes up unattended.
`paste` — interactive unlock; service runs locked until a human posts to /unlock.

`sealed` is reserved in the ADR for a future TPM/keyring mode; it is not
yet a legal value and is intentionally omitted from this Literal so that
`RESTREAM_PASSPHRASE_SOURCE=sealed` fails fast with a validation error
rather than silently behaving like one of the other modes.
"""

LogFormat = Literal["json", "console"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

MIN_PASSPHRASE_LENGTH = 12
"""Floor on master-passphrase length.

Argon2id makes brute-force expensive but does not make short passphrases
secure. A 12-char minimum is short enough to allow strong human-typed
passphrases and long enough to refuse obviously-trivial ones. The unlock
UI surfaces this same constraint.
"""

MIN_ADMIN_PASSWORD_LENGTH = 12


class AppSettings(BaseSettings):
    """All runtime configuration.

    Immutable after construction (`frozen=True`). To change a setting,
    restart the process — there is no live-reload path because the
    crypto key derivation is bound to boot-time inputs.
    """

    model_config = SettingsConfigDict(
        env_prefix="RESTREAM_",
        env_file=None,
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    passphrase_source: PassphraseSource = "env"
    master_passphrase: SecretStr | None = None

    admin_password: SecretStr | None = None
    """First-boot admin password (`RESTREAM_ADMIN_PASSWORD`).

    `None` means **autogenerate** on first boot per ADR-0005 §"Bootstrap" —
    the seed path in `app/db/schema_init.py` generates a 20-char URL-safe
    passphrase and prints it to stdout exactly once. On subsequent boots
    the env var is ignored unless paired with `RESTREAM_RESET_ADMIN_PASSWORD=1`.
    """

    reset_admin_password: bool = False

    data_dir: Path = Path("/data")

    bind_host: str = "0.0.0.0"  # noqa: S104 — container binds inside namespace
    bind_port: Annotated[int, Field(ge=1, le=65535)] = 8000

    log_level: LogLevel = "INFO"
    log_format: LogFormat = "json"

    healthz_check_nginx: bool = True
    """Whether /readyz probes nginx-rtmp via TCP connect to 127.0.0.1:1935.

    Default True for production (Phase 10 ships the s6 bundle that
    puts nginx in the same container — `/readyz` MUST surface nginx
    failures, otherwise reverse proxies route traffic to a server with
    no ingest). Dev runs without an nginx sibling must opt out via
    `RESTREAM_HEALTHZ_CHECK_NGINX=false`. The test suite does this in
    `tests/api/conftest.py::_build_settings`."""

    trusted_proxies: Annotated[tuple[IpNetwork, ...], NoDecode] = ()
    """Operator-supplied CIDR list of trusted reverse-proxy peers.

    Per ADR-0005 §"Boot-time KEK availability and locked-mode endpoint
    surface" (rate-limit identity paragraph). When the request peer is
    in this set, `X-Forwarded-For` is honored using the rightmost-untrusted
    walk; otherwise the header is ignored entirely.

    `NoDecode` disables pydantic-settings' JSON-parsing of env values
    for this field, so the raw `"127.0.0.1/32,10.0.0.0/8"` string
    reaches `_parse_trusted_proxies` below instead of failing JSON parse.

    Env var: `RESTREAM_TRUSTED_PROXIES=127.0.0.1/32,10.0.0.0/8` (comma list).
    Default empty so a misconfigured deployment fails closed (rate-limits
    all requests as the same peer IP — annoying but safe — rather than
    trusting forged headers from the public internet).
    """

    @field_validator("trusted_proxies", mode="before")
    @classmethod
    def _parse_trusted_proxies(cls, value: object) -> tuple[IpNetwork, ...] | object:
        """Accept a comma-separated CIDR string or a tuple/list of networks.

        Empty string and missing value both produce an empty tuple. Each
        entry is parsed with `ipaddress.ip_network(strict=False)` so
        host-only addresses (e.g., `127.0.0.1`) are accepted and
        normalized to `/32` (or `/128` for IPv6).
        """
        if value is None or value == "":
            return ()
        if isinstance(value, str):
            entries = [s.strip() for s in value.split(",") if s.strip()]
            parsed: list[IpNetwork] = []
            for entry in entries:
                try:
                    parsed.append(ipaddress.ip_network(entry, strict=False))
                except (ValueError, TypeError) as exc:
                    raise ValueError(
                        f"RESTREAM_TRUSTED_PROXIES entry {entry!r} is not a valid "
                        f"CIDR or IP address: {exc}"
                    ) from exc
            return tuple(parsed)
        return value

    @model_validator(mode="after")
    def _validate_passphrase_source_invariants(self) -> AppSettings:
        """Enforce ADR-0010 'no silent fallbacks'.

        env mode requires the passphrase. paste mode forbids it, so that a
        deployer who flips the source to paste cannot leave the env var
        present and accidentally fall back to env.
        """
        if self.passphrase_source == "env":  # noqa: S105 — Literal value, not a secret
            if self.master_passphrase is None:
                raise ValueError(
                    "RESTREAM_PASSPHRASE_SOURCE=env requires "
                    "RESTREAM_MASTER_PASSPHRASE to be set."
                )
            secret = self.master_passphrase.get_secret_value()
            if len(secret) < MIN_PASSPHRASE_LENGTH:
                raise ValueError(
                    f"RESTREAM_MASTER_PASSPHRASE must be at least "
                    f"{MIN_PASSPHRASE_LENGTH} characters."
                )
        elif self.passphrase_source == "paste":  # noqa: S105 — Literal value, not a secret
            if self.master_passphrase is not None:
                raise ValueError(
                    "RESTREAM_PASSPHRASE_SOURCE=paste forbids "
                    "RESTREAM_MASTER_PASSPHRASE; remove it to avoid ambiguity."
                )
        return self

    @model_validator(mode="after")
    def _validate_admin_password_length(self) -> AppSettings:
        """If `RESTREAM_ADMIN_PASSWORD` is supplied at all, refuse short values.

        Absent → autogenerated by the seed path (see field docstring above).
        A *set* value shorter than the floor is almost certainly an operator
        mistake we want to catch at boot rather than after the admin hash is
        committed to disk.
        """
        if self.admin_password is None:
            return self
        if len(self.admin_password.get_secret_value()) < MIN_ADMIN_PASSWORD_LENGTH:
            raise ValueError(
                f"RESTREAM_ADMIN_PASSWORD must be at least "
                f"{MIN_ADMIN_PASSWORD_LENGTH} characters."
            )
        return self

    @model_validator(mode="after")
    def _validate_reset_flag_pairing(self) -> AppSettings:
        """ADR-0005: `RESET_ADMIN_PASSWORD=1` is meaningless without a new value."""
        if self.reset_admin_password and self.admin_password is None:
            raise ValueError(
                "RESTREAM_RESET_ADMIN_PASSWORD=1 requires "
                "RESTREAM_ADMIN_PASSWORD to be set to the new value."
            )
        return self

    @property
    def kdf_salt_path(self) -> Path:
        """Persistent random salt for the Argon2id passphrase → root_key derivation.

        Per ADR-0006. The schema_init module owns creation; AppSettings just
        knows where it lives so other modules don't hardcode the path.
        """
        return self.data_dir / ".kdf_salt"

    @property
    def kdf_salt_previous_path(self) -> Path:
        """24-hour rotation grace-window salt, per ADR-0006 rotation procedure."""
        return self.data_dir / ".kdf_salt.previous"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "restream.db"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"
