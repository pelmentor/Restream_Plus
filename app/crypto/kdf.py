"""Key derivation: master passphrase → root_key → purpose-bound subkeys.

Per ADR-0006. Two steps:

  1. Argon2id(passphrase, persistent_salt) → root_key (32 bytes).
     Argon2id is memory-hard, slowing brute-force attempts.
  2. HKDF-SHA256(root_key, info=...) → subkey (32 bytes), once per purpose.
     HKDF separates compromise blast-radius: leaking `stream_key_kek`
     does not leak `api_token_mac_key`.

The two `info` strings are versioned (`-v1`) so that a future change
to either purpose (key length, AAD shape, …) bumps the info string
and forces re-derivation rather than silently breaking on read.

A *different* third info, `session-token-mac-v1`, was added in the
ADR-0006 amendment that accompanied Phase 3 (auth). It keys the HMAC
fingerprint of opaque session cookie values stored at
`sessions.token_hash`. It is structurally unrelated to the dropped
`session-cookie-mac` (which was meant for *signed* cookies, the
Flask-style stateless-session pattern we rejected): one keys a DB-
lookup fingerprint, the other would have keyed a stateless token.
"""

from __future__ import annotations

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

DERIVED_KEY_LENGTH = 32
"""All keys (root + subkeys) are 32 bytes — AES-256 needs 32, HMAC-SHA256 takes 32 happily."""

KDF_SALT_LENGTH = 16
"""Salt length for the passphrase → root_key Argon2id step.

Stored persistently at `data_dir/.kdf_salt`. 16 random bytes is the
NIST minimum for password-hash salts and matches the OWASP recommendation.
"""

ARGON2_TIME_COST = 3
ARGON2_MEMORY_COST_KIB = 128 * 1024  # 128 MiB
ARGON2_PARALLELISM = 2

HKDF_INFO_STREAM_KEY: bytes = b"stream-key-wrap-v1"
HKDF_INFO_API_TOKEN_MAC: bytes = b"api-token-mac-v1"
HKDF_INFO_SESSION_TOKEN_MAC: bytes = b"session-token-mac-v1"


def derive_root_key(passphrase: bytes, salt: bytes) -> bytes:
    """Argon2id(passphrase, salt) → 32-byte root key.

    Parameters are fixed (`time_cost=3, memory_cost=128 MiB, parallelism=2`)
    per ADR-0006 §"Key derivation". They are NOT configurable because that
    would mean the root_key produced for the same passphrase differs across
    deployments based on tuning — a footgun. If we ever raise them, that's
    a new ADR + a rotation event, not a config setting.
    """
    if not isinstance(passphrase, bytes):
        raise TypeError("passphrase must be bytes")
    if not isinstance(salt, bytes):
        raise TypeError("salt must be bytes")
    if len(salt) != KDF_SALT_LENGTH:
        raise ValueError(f"salt must be exactly {KDF_SALT_LENGTH} bytes, got {len(salt)}")
    if len(passphrase) == 0:
        raise ValueError("passphrase must not be empty")

    return hash_secret_raw(
        secret=passphrase,
        salt=salt,
        time_cost=ARGON2_TIME_COST,
        memory_cost=ARGON2_MEMORY_COST_KIB,
        parallelism=ARGON2_PARALLELISM,
        hash_len=DERIVED_KEY_LENGTH,
        type=Type.ID,
    )


def derive_subkey(root_key: bytes, info: bytes) -> bytes:
    """HKDF-SHA256(root_key, info) → 32-byte purpose-bound subkey.

    Use the constants `HKDF_INFO_STREAM_KEY` or `HKDF_INFO_API_TOKEN_MAC`
    rather than passing literals — that way grep across the codebase finds
    every purpose-binding in one shot.
    """
    if not isinstance(root_key, bytes):
        raise TypeError("root_key must be bytes")
    if len(root_key) != DERIVED_KEY_LENGTH:
        raise ValueError(
            f"root_key must be exactly {DERIVED_KEY_LENGTH} bytes, got {len(root_key)}"
        )
    if not isinstance(info, bytes):
        raise TypeError("info must be bytes")
    if len(info) == 0:
        raise ValueError("info must not be empty")

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=DERIVED_KEY_LENGTH,
        salt=None,
        info=info,
    )
    return hkdf.derive(root_key)
