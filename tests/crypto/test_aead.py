"""AES-256-GCM AEAD tests.

Includes property-based tests to satisfy Phase 1 acceptance criteria:
"any plaintext + random salt + random nonce → ciphertext that decrypts to
the same plaintext, and tampering with any byte fails AEAD."
"""

from __future__ import annotations

import secrets

import pytest
from app.crypto.aead import (
    AAD_CREDENTIAL_STREAM_KEY_V1,
    AEAD_NONCE_LENGTH,
    AEADDecryptionError,
    build_credential_aad,
    decrypt,
    encrypt,
)
from app.crypto.kdf import DERIVED_KEY_LENGTH, KDF_SALT_LENGTH
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st


@pytest.fixture
def key() -> bytes:
    return secrets.token_bytes(DERIVED_KEY_LENGTH)


@pytest.fixture
def salt() -> bytes:
    return secrets.token_bytes(KDF_SALT_LENGTH)


@pytest.fixture
def aad(salt: bytes) -> bytes:
    return build_credential_aad(salt)


class TestBuildCredentialAad:
    def test_aad_is_discriminator_plus_salt(self, salt: bytes) -> None:
        result = build_credential_aad(salt)
        assert result == AAD_CREDENTIAL_STREAM_KEY_V1 + salt

    def test_discriminator_is_pinned(self) -> None:
        # Changing this constant invalidates every stored ciphertext.
        # Pinning protects against an accidental refactor.
        assert AAD_CREDENTIAL_STREAM_KEY_V1 == b"credential:stream_key:v1"

    def test_rejects_wrong_salt_length(self) -> None:
        with pytest.raises(ValueError, match=str(KDF_SALT_LENGTH)):
            build_credential_aad(b"\x00" * (KDF_SALT_LENGTH - 1))

    def test_rejects_non_bytes_salt(self) -> None:
        with pytest.raises(TypeError):
            build_credential_aad("not-bytes")  # type: ignore[arg-type]


class TestBasicRoundtrip:
    def test_encrypt_then_decrypt_recovers_plaintext(self, key: bytes, aad: bytes) -> None:
        plaintext = b"live_st_1234567890abcdef"
        nonce, ct = encrypt(key, plaintext, aad)
        assert len(nonce) == AEAD_NONCE_LENGTH
        recovered = decrypt(key, nonce, ct, aad)
        assert recovered == plaintext

    def test_ciphertext_is_not_plaintext(self, key: bytes, aad: bytes) -> None:
        plaintext = b"sensitive-stream-key-value"
        _, ct = encrypt(key, plaintext, aad)
        assert plaintext not in ct

    def test_nonce_is_random_per_encryption(self, key: bytes, aad: bytes) -> None:
        plaintext = b"same-plaintext-each-time"
        nonces = {encrypt(key, plaintext, aad)[0] for _ in range(10)}
        # 12-byte random nonces colliding 10 times has probability ~5e-29.
        assert len(nonces) == 10

    def test_same_plaintext_different_nonce_yields_different_ciphertext(
        self, key: bytes, aad: bytes
    ) -> None:
        pt = b"same-input-each-time"
        _, ct1 = encrypt(key, pt, aad)
        _, ct2 = encrypt(key, pt, aad)
        assert ct1 != ct2

    def test_empty_plaintext_roundtrips(self, key: bytes, aad: bytes) -> None:
        nonce, ct = encrypt(key, b"", aad)
        assert decrypt(key, nonce, ct, aad) == b""


class TestTamperDetection:
    def test_wrong_key_fails(self, key: bytes, aad: bytes) -> None:
        nonce, ct = encrypt(key, b"plaintext", aad)
        wrong_key = secrets.token_bytes(DERIVED_KEY_LENGTH)
        with pytest.raises(AEADDecryptionError):
            decrypt(wrong_key, nonce, ct, aad)

    def test_modified_ciphertext_fails(self, key: bytes, aad: bytes) -> None:
        nonce, ct = encrypt(key, b"plaintext", aad)
        tampered = bytearray(ct)
        tampered[0] ^= 0x01
        with pytest.raises(AEADDecryptionError):
            decrypt(key, nonce, bytes(tampered), aad)

    def test_modified_nonce_fails(self, key: bytes, aad: bytes) -> None:
        nonce, ct = encrypt(key, b"plaintext", aad)
        tampered = bytearray(nonce)
        tampered[0] ^= 0x01
        with pytest.raises(AEADDecryptionError):
            decrypt(key, bytes(tampered), ct, aad)

    def test_modified_aad_fails(self, key: bytes, salt: bytes) -> None:
        aad = build_credential_aad(salt)
        nonce, ct = encrypt(key, b"plaintext", aad)
        # Build a different AAD by changing the salt component.
        other_salt = bytes(b ^ 0x01 for b in salt)
        with pytest.raises(AEADDecryptionError):
            decrypt(key, nonce, ct, build_credential_aad(other_salt))


class TestInputValidation:
    def test_rejects_wrong_key_length(self, aad: bytes) -> None:
        bad_key = b"\x00" * (DERIVED_KEY_LENGTH - 1)
        with pytest.raises(ValueError, match=str(DERIVED_KEY_LENGTH)):
            encrypt(bad_key, b"plaintext", aad)

    def test_rejects_non_bytes_plaintext(self, key: bytes, aad: bytes) -> None:
        with pytest.raises(TypeError):
            encrypt(key, "not-bytes", aad)  # type: ignore[arg-type]

    def test_rejects_non_bytes_aad_on_encrypt(self, key: bytes) -> None:
        with pytest.raises(TypeError):
            encrypt(key, b"plaintext", "not-bytes")  # type: ignore[arg-type]

    def test_rejects_wrong_nonce_length(self, key: bytes, aad: bytes) -> None:
        nonce, ct = encrypt(key, b"plaintext", aad)
        # decrypt with a too-short nonce
        with pytest.raises(ValueError, match=str(AEAD_NONCE_LENGTH)):
            decrypt(key, nonce[:-1], ct, aad)

    def test_rejects_ciphertext_shorter_than_gcm_tag(self, key: bytes, aad: bytes) -> None:
        # An empty or sub-tag ciphertext is an input bug (e.g., NULL DB blob),
        # not an auth failure. Should raise ValueError, not AEADDecryptionError.
        nonce = b"\x00" * AEAD_NONCE_LENGTH
        with pytest.raises(ValueError, match="GCM tag"):
            decrypt(key, nonce, b"", aad)
        with pytest.raises(ValueError, match="GCM tag"):
            decrypt(key, nonce, b"\x00" * 15, aad)


class TestCrossRecordIsolation:
    """Two records with different salts must not be decryptable across each other.

    This is the property that the per-record salt in the AAD buys us — see
    ADR-0006 §"Why not AAD of target_id" and §"AAD does NOT contain target_id".
    """

    def test_ciphertext_under_one_aad_does_not_decrypt_under_another(self, key: bytes) -> None:
        salt_a = secrets.token_bytes(KDF_SALT_LENGTH)
        salt_b = secrets.token_bytes(KDF_SALT_LENGTH)
        nonce, ct = encrypt(key, b"secret", build_credential_aad(salt_a))
        with pytest.raises(AEADDecryptionError):
            decrypt(key, nonce, ct, build_credential_aad(salt_b))


# --- Property-based tests (Phase 1 acceptance criterion) -----------------------


# Hypothesis "function-scoped fixture" health-check would flag our @given+fixture
# combination; we manually generate keys/salt/aad inside the body to keep the
# property pure on hypothesis-generated data.
plaintexts = st.binary(min_size=0, max_size=512)


@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(plaintext=plaintexts)
def test_property_roundtrip_recovers_plaintext(plaintext: bytes) -> None:
    key = secrets.token_bytes(DERIVED_KEY_LENGTH)
    salt = secrets.token_bytes(KDF_SALT_LENGTH)
    aad = build_credential_aad(salt)
    nonce, ct = encrypt(key, plaintext, aad)
    assert decrypt(key, nonce, ct, aad) == plaintext


@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    plaintext=st.binary(min_size=1, max_size=512),
    # Wide draw so `flip_byte % len(ct)` reaches every byte index for any
    # ciphertext length up to 512 + 16 (GCM tag). An earlier version capped
    # `flip_byte` at 255, which left the trailing tag bytes of large
    # ciphertexts unreachable — the property was effectively only testing
    # tampering of the leading 256 bytes.
    flip_byte=st.integers(min_value=0, max_value=1 << 31),
)
def test_property_tampering_any_byte_fails(plaintext: bytes, flip_byte: int) -> None:
    key = secrets.token_bytes(DERIVED_KEY_LENGTH)
    salt = secrets.token_bytes(KDF_SALT_LENGTH)
    aad = build_credential_aad(salt)
    nonce, ct = encrypt(key, plaintext, aad)

    # Tamper a random byte of the ciphertext (includes the GCM tag region).
    idx = flip_byte % len(ct)
    tampered = bytearray(ct)
    tampered[idx] ^= 0xFF
    with pytest.raises(AEADDecryptionError):
        decrypt(key, nonce, bytes(tampered), aad)
