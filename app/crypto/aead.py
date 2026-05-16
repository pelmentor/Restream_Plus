"""AES-256-GCM AEAD wrapper for at-rest credential encryption.

Per ADR-0006. AES-GCM is authenticated encryption: it both encrypts
the plaintext and produces a tag that proves the (ciphertext, AAD, nonce)
triple was produced by someone who knew the key. Tampering with any
byte — ciphertext, nonce, or AAD — fails decryption with a clear error.

Caller protocol for a credential record (per ADR-0006 §"Encryption of
a credential"):

    salt  = secrets.token_bytes(KDF_SALT_LENGTH)
    aad   = build_credential_aad(salt)
    nonce, ct = encrypt(stream_key_kek, plaintext, aad)
    db.store(salt=salt, nonce=nonce, ciphertext=ct, last4=plaintext[-4:])
    # plaintext goes out of scope; reference drops
    ...
    # later
    pt = decrypt(stream_key_kek, nonce, ct, build_credential_aad(salt))

Nonces are generated inside `encrypt()`; callers MUST NOT reuse them.
AES-GCM catastrophically fails confidentiality if a (key, nonce) pair
encrypts two different plaintexts.
"""

from __future__ import annotations

import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.crypto.kdf import DERIVED_KEY_LENGTH, KDF_SALT_LENGTH

AEAD_NONCE_LENGTH = 12
"""AES-GCM uses a 96-bit nonce. Per NIST SP 800-38D the random 96-bit nonce
construction caps safe encryptions per key at ~2^32 messages before collision
probability climbs. We are orders of magnitude below that for the lifetime of
any deployment."""

GCM_TAG_LENGTH = 16
"""AES-GCM produces a 128-bit (16-byte) authentication tag appended to the
ciphertext by the `cryptography` library. Any value shorter than this cannot
possibly be a valid ciphertext, so we reject it at the boundary with a clear
`ValueError` rather than the more generic `AEADDecryptionError` — a too-short
input is an input bug (e.g., a NULL/empty DB blob), not an auth-tag failure."""

AAD_CREDENTIAL_STREAM_KEY_V1: bytes = b"credential:stream_key:v1"
"""Type discriminator for stream-key credentials.

The full AAD is `discriminator + salt` (per ADR-0006 amendment after the
F# BA-4 review): a fixed-string discriminator binds the ciphertext to its
purpose, and the per-record random salt binds it to its row. Together they
prevent cross-record ciphertext substitution while staying restore-portable
(no `target_id` in the AAD, so delete-and-recreate restores still decrypt).
"""


class AEADDecryptionError(Exception):
    """Authentication failed during AES-GCM decryption.

    Raised on tampering of ciphertext, nonce, or AAD; also on a wrong key
    or wrong AAD construction. Callers must NOT distinguish these cases in
    error messages or logs (it leaks information to an attacker that
    distinguishes legitimate ciphertext from random bytes).
    """


def build_credential_aad(salt: bytes) -> bytes:
    """Build the canonical AAD for a stream-key credential record.

    The salt must be exactly `KDF_SALT_LENGTH` bytes — the same length used
    everywhere else in the system so a record schema mismatch is caught at
    encrypt time, not decrypt time.
    """
    if not isinstance(salt, bytes):
        raise TypeError("salt must be bytes")
    if len(salt) != KDF_SALT_LENGTH:
        raise ValueError(f"salt must be exactly {KDF_SALT_LENGTH} bytes, got {len(salt)}")
    return AAD_CREDENTIAL_STREAM_KEY_V1 + salt


def encrypt(key: bytes, plaintext: bytes, aad: bytes) -> tuple[bytes, bytes]:
    """Encrypt `plaintext` under `key` with `aad`. Returns `(nonce, ciphertext)`.

    The nonce is generated here from `secrets.token_bytes` — callers cannot
    supply it. This forecloses the most common AES-GCM misuse (nonce reuse).
    """
    _validate_key(key)
    if not isinstance(plaintext, bytes):
        raise TypeError("plaintext must be bytes")
    if not isinstance(aad, bytes):
        raise TypeError("aad must be bytes")

    nonce = secrets.token_bytes(AEAD_NONCE_LENGTH)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
    return nonce, ciphertext


def decrypt(key: bytes, nonce: bytes, ciphertext: bytes, aad: bytes) -> bytes:
    """Decrypt `ciphertext` under `key` with `nonce` and `aad`.

    Raises `AEADDecryptionError` on any authentication failure (wrong key,
    wrong AAD, modified ciphertext, modified nonce). The original
    `cryptography.exceptions.InvalidTag` is not re-raised — that exception
    type is part of an internal lib's API and we want callers to depend
    only on our error type.
    """
    _validate_key(key)
    if not isinstance(nonce, bytes):
        raise TypeError("nonce must be bytes")
    if len(nonce) != AEAD_NONCE_LENGTH:
        raise ValueError(f"nonce must be exactly {AEAD_NONCE_LENGTH} bytes, got {len(nonce)}")
    if not isinstance(ciphertext, bytes):
        raise TypeError("ciphertext must be bytes")
    if len(ciphertext) < GCM_TAG_LENGTH:
        raise ValueError(
            f"ciphertext must be at least {GCM_TAG_LENGTH} bytes (GCM tag), "
            f"got {len(ciphertext)}"
        )
    if not isinstance(aad, bytes):
        raise TypeError("aad must be bytes")

    try:
        return AESGCM(key).decrypt(nonce, ciphertext, aad)
    except InvalidTag as exc:
        raise AEADDecryptionError("AEAD authentication failed") from exc


def _validate_key(key: bytes) -> None:
    if not isinstance(key, bytes):
        raise TypeError("key must be bytes")
    if len(key) != DERIVED_KEY_LENGTH:
        raise ValueError(
            f"key must be exactly {DERIVED_KEY_LENGTH} bytes " f"(AES-256), got {len(key)}"
        )
