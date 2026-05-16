"""Cryptographic primitives for Restream_Plus.

Layered as: kdf (root_key + subkeys) → aead (AES-GCM wrapper) →
passwords (admin user hash). Higher layers (repositories, auth,
worker supervisor) consume these primitives but do not implement
their own.

ADR-0006 governs key derivation and AEAD. ADR-0005 governs the
admin-password hashing parameters.
"""

from app.crypto.aead import (
    AAD_CREDENTIAL_STREAM_KEY_V1,
    AEAD_NONCE_LENGTH,
    AEADDecryptionError,
    build_credential_aad,
    decrypt,
    encrypt,
)
from app.crypto.kdf import (
    DERIVED_KEY_LENGTH,
    HKDF_INFO_API_TOKEN_MAC,
    HKDF_INFO_SESSION_TOKEN_MAC,
    HKDF_INFO_STREAM_KEY,
    KDF_SALT_LENGTH,
    derive_root_key,
    derive_subkey,
)
from app.crypto.passwords import (
    PasswordVerificationResult,
    hash_password,
    verify_password,
)

__all__ = [
    # KDF
    "DERIVED_KEY_LENGTH",
    "HKDF_INFO_API_TOKEN_MAC",
    "HKDF_INFO_SESSION_TOKEN_MAC",
    "HKDF_INFO_STREAM_KEY",
    "KDF_SALT_LENGTH",
    "derive_root_key",
    "derive_subkey",
    # AEAD
    "AAD_CREDENTIAL_STREAM_KEY_V1",
    "AEAD_NONCE_LENGTH",
    "AEADDecryptionError",
    "build_credential_aad",
    "decrypt",
    "encrypt",
    # Passwords
    "PasswordVerificationResult",
    "hash_password",
    "verify_password",
]
