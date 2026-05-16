"""KDF tests: Argon2id determinism + HKDF subkey separation.

The Argon2id parameters in `kdf.py` are tuned for ~hundreds of ms.
Most tests use a fixed short passphrase and a fixed salt so the
cost is paid once per test, not per parameter. The values are not
secret in the test path; they exist only to exercise the code.
"""

from __future__ import annotations

import pytest
from app.crypto.kdf import (
    DERIVED_KEY_LENGTH,
    HKDF_INFO_API_TOKEN_MAC,
    HKDF_INFO_SESSION_TOKEN_MAC,
    HKDF_INFO_STREAM_KEY,
    KDF_SALT_LENGTH,
    derive_root_key,
    derive_subkey,
)

# Use a short, fixed-test salt of the documented length.
TEST_SALT = b"\x00" * KDF_SALT_LENGTH
TEST_PASSPHRASE = b"correct-horse-battery-staple"


@pytest.fixture(scope="module")
def root_key() -> bytes:
    """One Argon2id derivation shared across the module's tests.

    Argon2id at 128 MiB / time_cost=3 is intentionally slow. Each module
    pays it once.
    """
    return derive_root_key(TEST_PASSPHRASE, TEST_SALT)


class TestDeriveRootKey:
    def test_returns_32_bytes(self, root_key: bytes) -> None:
        assert isinstance(root_key, bytes)
        assert len(root_key) == DERIVED_KEY_LENGTH

    def test_deterministic_for_same_inputs(self, root_key: bytes) -> None:
        second = derive_root_key(TEST_PASSPHRASE, TEST_SALT)
        assert second == root_key

    def test_different_salt_yields_different_root_key(self, root_key: bytes) -> None:
        other_salt = b"\x01" * KDF_SALT_LENGTH
        assert derive_root_key(TEST_PASSPHRASE, other_salt) != root_key

    def test_different_passphrase_yields_different_root_key(self, root_key: bytes) -> None:
        other = b"different-passphrase-value"
        assert derive_root_key(other, TEST_SALT) != root_key

    def test_rejects_non_bytes_passphrase(self) -> None:
        with pytest.raises(TypeError):
            derive_root_key("not-bytes", TEST_SALT)  # type: ignore[arg-type]

    def test_rejects_non_bytes_salt(self) -> None:
        with pytest.raises(TypeError):
            derive_root_key(TEST_PASSPHRASE, "not-bytes")  # type: ignore[arg-type]

    def test_rejects_empty_passphrase(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            derive_root_key(b"", TEST_SALT)

    def test_rejects_wrong_salt_length(self) -> None:
        with pytest.raises(ValueError, match=str(KDF_SALT_LENGTH)):
            derive_root_key(TEST_PASSPHRASE, b"\x00" * (KDF_SALT_LENGTH - 1))


class TestDeriveSubkey:
    def test_returns_32_bytes(self, root_key: bytes) -> None:
        subkey = derive_subkey(root_key, HKDF_INFO_STREAM_KEY)
        assert isinstance(subkey, bytes)
        assert len(subkey) == DERIVED_KEY_LENGTH

    def test_purpose_binding_separates_keys(self, root_key: bytes) -> None:
        stream_key = derive_subkey(root_key, HKDF_INFO_STREAM_KEY)
        mac_key = derive_subkey(root_key, HKDF_INFO_API_TOKEN_MAC)
        assert stream_key != mac_key

    def test_same_info_yields_same_subkey(self, root_key: bytes) -> None:
        a = derive_subkey(root_key, HKDF_INFO_STREAM_KEY)
        b = derive_subkey(root_key, HKDF_INFO_STREAM_KEY)
        assert a == b

    def test_different_root_key_yields_different_subkey(self, root_key: bytes) -> None:
        other_root = bytes(reversed(root_key))
        a = derive_subkey(root_key, HKDF_INFO_STREAM_KEY)
        b = derive_subkey(other_root, HKDF_INFO_STREAM_KEY)
        assert a != b

    def test_rejects_wrong_root_key_length(self) -> None:
        with pytest.raises(ValueError, match=str(DERIVED_KEY_LENGTH)):
            derive_subkey(b"\x00" * (DERIVED_KEY_LENGTH - 1), HKDF_INFO_STREAM_KEY)

    def test_rejects_empty_info(self, root_key: bytes) -> None:
        with pytest.raises(ValueError, match="empty"):
            derive_subkey(root_key, b"")

    def test_rejects_non_bytes_info(self, root_key: bytes) -> None:
        with pytest.raises(TypeError):
            derive_subkey(root_key, "stream-key-wrap-v1")  # type: ignore[arg-type]


class TestHkdfInfoStrings:
    """The info strings are part of the security contract — protect against typos.

    Changing them silently is a key-rotation event; tests pin the literal bytes
    so a refactor that mangles them gets caught at PR time.
    """

    def test_stream_key_info_is_pinned(self) -> None:
        assert HKDF_INFO_STREAM_KEY == b"stream-key-wrap-v1"

    def test_api_token_mac_info_is_pinned(self) -> None:
        assert HKDF_INFO_API_TOKEN_MAC == b"api-token-mac-v1"

    def test_session_token_mac_info_is_pinned(self) -> None:
        # Added in Phase 3 per ADR-0006 amendment. Pinned in tests so
        # a typo in the constant becomes a regression rather than a
        # silent break of every existing session row's MAC.
        assert HKDF_INFO_SESSION_TOKEN_MAC == b"session-token-mac-v1"

    def test_all_infos_pairwise_distinct(self) -> None:
        # Key separation is load-bearing per ADR-0006; an accidental
        # collision between two infos would re-use the same subkey
        # for unrelated purposes.
        infos = {
            HKDF_INFO_STREAM_KEY,
            HKDF_INFO_API_TOKEN_MAC,
            HKDF_INFO_SESSION_TOKEN_MAC,
        }
        assert len(infos) == 3
