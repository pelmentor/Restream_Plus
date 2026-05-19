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
    AAD_CREDENTIAL_STREAM_KEY_V2,
    AEAD_NONCE_LENGTH,
    AEADDecryptionError,
    build_credential_aad,
    build_credential_aad_v2,
    decrypt,
    decrypt_credential,
    encrypt,
    encrypt_credential,
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


# --- Slice 7 / Hex Audit BA-F5 — AAD v2 binds target_id ------------------------


class TestBuildCredentialAadV2:
    def test_v2_aad_is_discriminator_plus_salt_plus_target_id(self, salt: bytes) -> None:
        target_id = "target-abc-123"
        result = build_credential_aad_v2(salt, target_id)
        assert result == AAD_CREDENTIAL_STREAM_KEY_V2 + salt + b"target-abc-123"

    def test_v2_discriminator_is_pinned(self) -> None:
        # Pinned so a refactor cannot silently change the AAD bytes
        # and break decrypt of every at-rest credential.
        assert AAD_CREDENTIAL_STREAM_KEY_V2 == b"credential:stream_key:v2"

    def test_v2_rejects_wrong_salt_length(self) -> None:
        with pytest.raises(ValueError, match=str(KDF_SALT_LENGTH)):
            build_credential_aad_v2(b"\x00" * (KDF_SALT_LENGTH - 1), "tid")

    def test_v2_rejects_empty_target_id(self, salt: bytes) -> None:
        with pytest.raises(ValueError, match="target_id"):
            build_credential_aad_v2(salt, "")

    def test_v2_rejects_non_str_target_id(self, salt: bytes) -> None:
        with pytest.raises(TypeError):
            build_credential_aad_v2(salt, b"bytes-not-allowed")  # type: ignore[arg-type]


class TestCredentialAeadHelpers:
    """`encrypt_credential` + `decrypt_credential` round-trip + cross-row attack."""

    def test_roundtrip_v2(self, key: bytes, salt: bytes) -> None:
        target_id = "target-xyz"
        plaintext = b"sk_live_1234567890abcdef"
        nonce, ct = encrypt_credential(key, plaintext, salt, target_id)
        assert len(nonce) == AEAD_NONCE_LENGTH
        recovered = decrypt_credential(key, nonce, ct, salt, target_id)
        assert recovered == plaintext

    def test_wrong_target_id_fails(self, key: bytes, salt: bytes) -> None:
        """Cross-row substitution attack: encrypted under target A, attacker
        copies the row to target B → decrypt fails because v2 AAD no
        longer matches AND v1 fallback also fails (the ciphertext was
        produced under v2)."""
        nonce, ct = encrypt_credential(key, b"sk_test", salt, "target-A")
        with pytest.raises(AEADDecryptionError):
            decrypt_credential(key, nonce, ct, salt, "target-B")

    def test_v1_legacy_row_decrypts_via_fallback(self, key: bytes, salt: bytes) -> None:
        """Rows written before slice 7 use v1 AAD (no target_id). The
        rolling-migration helper must transparently fall back to v1
        when v2 doesn't verify."""
        v1_aad = build_credential_aad(salt)
        plaintext = b"legacy-key-from-v1.1.3"
        nonce, ct = encrypt(key, plaintext, v1_aad)
        # target_id passed here is "what we have today" — the fallback
        # path doesn't use it (v1 AAD doesn't bind target_id).
        recovered = decrypt_credential(key, nonce, ct, salt, "any-target-id")
        assert recovered == plaintext

    def test_v2_row_does_not_decrypt_with_v1_aad(self, key: bytes, salt: bytes) -> None:
        """Sanity check that v2 and v1 produce distinct authentication
        — otherwise the fallback wouldn't be doing real work."""
        nonce, ct = encrypt_credential(key, b"sk", salt, "tid")
        with pytest.raises(AEADDecryptionError):
            decrypt(key, nonce, ct, build_credential_aad(salt))

    def test_v1_row_does_not_decrypt_with_v2_aad(self, key: bytes, salt: bytes) -> None:
        v1_aad = build_credential_aad(salt)
        nonce, ct = encrypt(key, b"sk", v1_aad)
        with pytest.raises(AEADDecryptionError):
            decrypt(key, nonce, ct, build_credential_aad_v2(salt, "tid"))

    def test_v1_fallback_with_wrong_target_id_still_decrypts(
        self, key: bytes, salt: bytes
    ) -> None:
        """v1 rows have no `target_id` binding, so the legacy fallback
        is permissive on target_id. The cross-row attack closes only
        for v2 rows; v1 rows remain vulnerable until they're naturally
        re-encrypted via the next write (rolling migration). This is
        accepted in the architect verdict — v1 rows are legacy
        baggage, and natural write cadence migrates them quickly."""
        v1_aad = build_credential_aad(salt)
        nonce, ct = encrypt(key, b"sk", v1_aad)
        # Any target_id works for v1 rows because v1 AAD doesn't bind it.
        assert decrypt_credential(key, nonce, ct, salt, "wrong-tid") == b"sk"

    def test_tampered_v2_ciphertext_raises_not_silently_falls_back(
        self, key: bytes, salt: bytes
    ) -> None:
        """Reviewer M-2: a v2 row with a tampered ciphertext byte must
        raise `AEADDecryptionError` — NOT silently fall back to v1 and
        succeed. AES-GCM's tag verification fails regardless of AAD,
        so both v2 and v1 AAD attempts fail; the helper raises. This
        pins the contract against a future regression that might
        widen the fallback to retry with a different key/nonce."""
        nonce, ct = encrypt_credential(key, b"plaintext", salt, "target-X")
        # Flip a byte in the ciphertext (not in the GCM tag region, but
        # any byte flip fails AEAD verification).
        tampered = bytearray(ct)
        tampered[0] ^= 0xFF
        with pytest.raises(AEADDecryptionError):
            decrypt_credential(key, nonce, bytes(tampered), salt, "target-X")
