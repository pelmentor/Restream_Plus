"""AES-256-GCM AEAD wrapper for at-rest credential encryption.

Per ADR-0006. AES-GCM is authenticated encryption: it both encrypts
the plaintext and produces a tag that proves the (ciphertext, AAD, nonce)
triple was produced by someone who knew the key. Tampering with any
byte — ciphertext, nonce, or AAD — fails decryption with a clear error.

Caller protocol for a credential record (canonical, post-Hex-Audit BA-F5
slice 7 — AAD binds `target_id`):

    salt  = secrets.token_bytes(KDF_SALT_LENGTH)
    nonce, ct = encrypt_credential(stream_key_kek, plaintext, salt, target_id)
    db.store(target_id=target_id, salt=salt, nonce=nonce, ciphertext=ct,
             last4=plaintext[-4:])
    # plaintext goes out of scope; reference drops
    ...
    # later
    pt = decrypt_credential(stream_key_kek, nonce, ct, salt, target_id)

`decrypt_credential` first tries the v2 AAD (which binds `target_id`)
and falls back to v1 (legacy rows that pre-date BA-F5) on InvalidTag.
This is a rolling migration: a row is upgraded to v2 the next time
its `target_id` re-encrypts (set_credential or rotate-passphrase).
No schema column is needed — versioning is implicit in which AAD
verifies.

Cross-row substitution attack closed by v2: an attacker with DB-write
access could previously copy `(ciphertext, nonce, salt, last4)` from
target A to target B undetected because v1 AAD only bound `salt`
(which travels with the row). v2 AAD includes `target_id`, so the
substitution fails AEAD verification.

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
"""LEGACY discriminator for stream-key credentials (pre-slice-7).

v1 AAD = `discriminator + salt`. Bound the ciphertext to a salt that
travels with the row, but NOT to its `target_id` — so a DB-write
attacker could substitute `(ciphertext, nonce, salt)` between rows
undetected. Kept for the decrypt-fallback path that reads existing
v1 rows; never used for new writes after slice 7."""

AAD_CREDENTIAL_STREAM_KEY_V2: bytes = b"credential:stream_key:v2"
"""Current discriminator for stream-key credentials (Hex Audit BA-F5,
slice 7).

v2 AAD = `discriminator + salt + target_id_utf8`. Salt is fixed-width
(`KDF_SALT_LENGTH` bytes) so the boundary between salt and target_id
is unambiguous in the opaque byte string. Closes the cross-row
substitution attack: copying a row's encrypted fields under a
different `target_id` fails AEAD verification because the bound
`target_id` no longer matches."""


class AEADDecryptionError(Exception):
    """Authentication failed during AES-GCM decryption.

    Raised on tampering of ciphertext, nonce, or AAD; also on a wrong key
    or wrong AAD construction. Callers must NOT distinguish these cases in
    error messages or logs (it leaks information to an attacker that
    distinguishes legitimate ciphertext from random bytes).
    """


def build_credential_aad(salt: bytes) -> bytes:
    """Build the legacy v1 AAD for a stream-key credential record.

    LEGACY: used only by `decrypt_credential`'s v1 fallback path for
    rows that pre-date BA-F5 (slice 7). New writes use
    `build_credential_aad_v2` via `encrypt_credential`.

    The salt must be exactly `KDF_SALT_LENGTH` bytes — the same length used
    everywhere else in the system so a record schema mismatch is caught at
    encrypt time, not decrypt time.
    """
    if not isinstance(salt, bytes):
        raise TypeError("salt must be bytes")
    if len(salt) != KDF_SALT_LENGTH:
        raise ValueError(f"salt must be exactly {KDF_SALT_LENGTH} bytes, got {len(salt)}")
    return AAD_CREDENTIAL_STREAM_KEY_V1 + salt


def build_credential_aad_v2(salt: bytes, target_id: str) -> bytes:
    """Build the v2 AAD for a stream-key credential record.

    Hex Audit BA-F5 (slice 7). Salt must be exactly `KDF_SALT_LENGTH`
    bytes; `target_id` is the credential row's owning target UUID,
    UTF-8-encoded and appended after the fixed-width salt. Because
    AES-GCM AAD is opaque, no length prefix is needed — the salt's
    fixed width unambiguously delimits the target_id tail.
    """
    if not isinstance(salt, bytes):
        raise TypeError("salt must be bytes")
    if len(salt) != KDF_SALT_LENGTH:
        raise ValueError(f"salt must be exactly {KDF_SALT_LENGTH} bytes, got {len(salt)}")
    if not isinstance(target_id, str):
        raise TypeError("target_id must be a string")
    if not target_id:
        raise ValueError("target_id must not be empty")
    return AAD_CREDENTIAL_STREAM_KEY_V2 + salt + target_id.encode("utf-8")


def encrypt_credential(
    key: bytes,
    plaintext: bytes,
    salt: bytes,
    target_id: str,
) -> tuple[bytes, bytes]:
    """Encrypt a stream-key credential under the v2 AAD.

    Returns `(nonce, ciphertext)`. The nonce is generated inside
    `encrypt()` — callers cannot supply one (forecloses the
    nonce-reuse foot-gun). Always writes v2 AAD: there is no caller-
    selectable version. Rows produced before slice 7 continue to
    decrypt via `decrypt_credential`'s v1 fallback path.
    """
    aad = build_credential_aad_v2(salt, target_id)
    return encrypt(key, plaintext, aad)


def decrypt_credential(
    key: bytes,
    nonce: bytes,
    ciphertext: bytes,
    salt: bytes,
    target_id: str,
) -> bytes:
    """Decrypt a stream-key credential. Tries v2 AAD, then v1.

    Hex Audit BA-F5 (slice 7) rolling-migration entry point.
    Implementation strategy per backend-architect verdict: NO schema
    column. AEAD itself tells us the row's version — if v2 AAD
    verifies, it's a v2 row; if v2 fails and v1 succeeds, it's a
    legacy v1 row. Both raise `AEADDecryptionError` only when neither
    AAD verifies — the union of failure modes is collapsed to a
    single error so callers can't distinguish (tamper / corrupted /
    wrong key all look the same).

    Cost: one extra AES-GCM auth attempt on legacy rows (sub-
    millisecond, one-shot per row in practice). v2 rows decrypt on
    the first try with no extra overhead.
    """
    try:
        return decrypt(key, nonce, ciphertext, build_credential_aad_v2(salt, target_id))
    except AEADDecryptionError:
        return decrypt(key, nonce, ciphertext, build_credential_aad(salt))


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
