"""Tests for KeyMaterial — the 3-state machine + salt persistence."""

from __future__ import annotations

import asyncio
import secrets
from pathlib import Path

import pytest
from app.auth.key_material import (
    DerivedKeys,
    KeyMaterial,
    KeyMaterialState,
    PassphraseMismatchError,
    ServiceLockedError,
    load_or_create_kdf_salt,
)
from app.crypto import KDF_SALT_LENGTH


class TestKeyMaterialConstruction:
    def test_salt_wrong_length_rejected(self) -> None:
        with pytest.raises(ValueError, match="exactly 16 bytes"):
            KeyMaterial(kdf_salt=b"\x00" * 15)

    def test_salt_non_bytes_rejected(self) -> None:
        with pytest.raises(TypeError, match="kdf_salt must be bytes"):
            KeyMaterial(kdf_salt="not-bytes")  # type: ignore[arg-type]

    def test_starts_locked(self, kdf_salt: bytes) -> None:
        km = KeyMaterial(kdf_salt=kdf_salt)
        assert km.state == KeyMaterialState.LOCKED
        assert km.is_ready is False


class TestKeyMaterialRequireReady:
    def test_locked_raises_service_locked(self, key_material_locked: KeyMaterial) -> None:
        with pytest.raises(ServiceLockedError, match="state='locked'"):
            key_material_locked.require_ready()


class TestKeyMaterialUnlock:
    @pytest.mark.asyncio
    async def test_empty_passphrase_rejected(self, key_material_locked: KeyMaterial) -> None:
        with pytest.raises(ValueError, match="passphrase must not be empty"):
            await key_material_locked.unlock("")

    @pytest.mark.asyncio
    async def test_non_str_rejected(self, key_material_locked: KeyMaterial) -> None:
        with pytest.raises(TypeError, match="passphrase must be a string"):
            await key_material_locked.unlock(b"bytes-not-str")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_unlock_transitions_to_ready(self, key_material_locked: KeyMaterial) -> None:
        await key_material_locked.unlock("a-real-passphrase-12345")
        assert key_material_locked.state == KeyMaterialState.READY
        assert key_material_locked.is_ready is True

        keys = key_material_locked.require_ready()
        assert isinstance(keys, DerivedKeys)
        assert len(keys.stream_key_kek) == 32
        assert len(keys.api_token_mac_key) == 32
        assert len(keys.session_token_mac_key) == 32
        # All three are distinct (HKDF separation working).
        assert keys.stream_key_kek != keys.api_token_mac_key
        assert keys.stream_key_kek != keys.session_token_mac_key
        assert keys.api_token_mac_key != keys.session_token_mac_key

    @pytest.mark.asyncio
    async def test_unlock_idempotent_under_ready(self, key_material_locked: KeyMaterial) -> None:
        await key_material_locked.unlock("a-real-passphrase-12345")
        first_keys = key_material_locked.require_ready()

        # Second call should be a fast no-op (no re-derivation).
        # We cannot directly measure "no Argon2 happened" but we can
        # assert the keys identity is preserved (object equality) and
        # that the call completes essentially instantly.
        import time

        start = time.perf_counter()
        await key_material_locked.unlock("a-real-passphrase-12345")
        elapsed = time.perf_counter() - start
        # An Argon2 derivation at 128 MiB is ~hundreds of ms; a
        # genuine no-op completes in microseconds. 50 ms is the
        # safety margin.
        assert elapsed < 0.05, (
            f"second unlock() took {elapsed:.3f}s; idempotent path probably "
            f"re-derived (should short-circuit on READY state)"
        )
        second_keys = key_material_locked.require_ready()
        assert second_keys is first_keys

    @pytest.mark.asyncio
    async def test_unlock_idempotent_under_ready_accepts_different_passphrase(
        self, key_material_locked: KeyMaterial
    ) -> None:
        """Under READY, unlock() is a no-op regardless of passphrase.

        This is by design (architect Q2): re-derivation is expensive
        and pointless when the keys are already cached. The unlock
        endpoint is rate-limited; a probing attacker cannot use this
        to learn the passphrase because no observable side effect
        depends on whether they got it right.
        """
        await key_material_locked.unlock("a-real-passphrase-12345")
        keys_before = key_material_locked.require_ready()
        await key_material_locked.unlock("a-completely-different-string")
        keys_after = key_material_locked.require_ready()
        assert keys_before is keys_after

    @pytest.mark.asyncio
    async def test_concurrent_unlocks_serialize(self, key_material_locked: KeyMaterial) -> None:
        """Two concurrent unlock coroutines: only one derivation runs."""
        # Same passphrase, both coroutines hit unlock() together.
        passphrase = "shared-passphrase-12345"
        results = await asyncio.gather(
            key_material_locked.unlock(passphrase),
            key_material_locked.unlock(passphrase),
        )
        # `asyncio.gather` returns a list at runtime even when mypy's
        # stubs declare a tuple. Compare as a list of length 2.
        assert len(results) == 2
        assert results[0] is None
        assert results[1] is None
        assert key_material_locked.state == KeyMaterialState.READY
        keys = key_material_locked.require_ready()
        assert len(keys.stream_key_kek) == 32

    @pytest.mark.asyncio
    async def test_unlock_failure_rolls_back_to_locked(
        self, monkeypatch: pytest.MonkeyPatch, key_material_locked: KeyMaterial
    ) -> None:
        """If derivation raises, state returns to LOCKED so the
        operator can retry. We simulate via monkeypatching the
        synchronous derivation helper to raise."""

        def boom(self_: KeyMaterial, passphrase: str) -> DerivedKeys:
            raise RuntimeError("argon2 explosion")

        monkeypatch.setattr(KeyMaterial, "_derive_sync", boom)
        with pytest.raises(RuntimeError, match="argon2 explosion"):
            await key_material_locked.unlock("any-passphrase-12345")
        assert key_material_locked.state == KeyMaterialState.LOCKED


class TestDerivedKeysRepr:
    def test_repr_does_not_leak_bytes(self) -> None:
        keys = DerivedKeys(
            stream_key_kek=b"S" * 32,
            api_token_mac_key=b"A" * 32,
            session_token_mac_key=b"H" * 32,
        )
        r = repr(keys)
        assert "<redacted>" in r
        assert "SSSS" not in r
        assert "AAAA" not in r
        assert "HHHH" not in r


class TestKdfSaltPersistence:
    def test_creates_new_salt_on_first_boot(self, tmp_path: Path) -> None:
        salt_path = tmp_path / ".kdf_salt"
        assert not salt_path.exists()
        salt = load_or_create_kdf_salt(salt_path)
        assert len(salt) == KDF_SALT_LENGTH
        assert salt_path.exists()
        assert salt_path.read_bytes() == salt

    def test_reads_existing_salt(self, tmp_path: Path) -> None:
        salt_path = tmp_path / ".kdf_salt"
        existing = secrets.token_bytes(KDF_SALT_LENGTH)
        salt_path.write_bytes(existing)
        loaded = load_or_create_kdf_salt(salt_path)
        assert loaded == existing

    def test_rejects_wrong_length_existing_salt(self, tmp_path: Path) -> None:
        salt_path = tmp_path / ".kdf_salt"
        salt_path.write_bytes(b"too-short")
        with pytest.raises(RuntimeError, match="has length 9"):
            load_or_create_kdf_salt(salt_path)

    def test_atomic_write_leaves_no_tmp(self, tmp_path: Path) -> None:
        salt_path = tmp_path / "subdir" / ".kdf_salt"
        load_or_create_kdf_salt(salt_path)
        assert salt_path.exists()
        # No leftover tmp file from the write-then-rename dance.
        tmp_residue = salt_path.with_suffix(salt_path.suffix + ".tmp")
        assert not tmp_residue.exists()


class TestPassphraseMismatchError:
    def test_is_runtime_error(self) -> None:
        """Currently unused (reserved for a future re-unlock verifier),
        but the type must remain a RuntimeError subclass so callers
        can `except RuntimeError` if they don't care about distinguishing.
        """
        assert issubclass(PassphraseMismatchError, RuntimeError)
