"""Admin-password hashing tests.

Argon2id at 64 MiB/time_cost=3 takes ~100 ms per hash; the module is
intentionally small (3 functions) so the test cost stays bounded.
"""

from __future__ import annotations

import pytest
from app.crypto.passwords import (
    PasswordVerificationResult,
    hash_password,
    verify_password,
)


class TestHashPassword:
    def test_returns_phc_string(self) -> None:
        hashed = hash_password("hunter2-correct-horse")
        # PHC format: $argon2id$v=19$m=...,t=...,p=...$<salt>$<hash>
        assert hashed.startswith("$argon2id$")
        assert hashed.count("$") >= 5

    def test_each_call_yields_different_hash(self) -> None:
        a = hash_password("same-password-each-time")
        b = hash_password("same-password-each-time")
        # Random salt per hash → different output even for equal inputs.
        assert a != b

    def test_rejects_non_string(self) -> None:
        with pytest.raises(TypeError):
            hash_password(b"bytes-not-str")  # type: ignore[arg-type]

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            hash_password("")


class TestVerifyPassword:
    def test_correct_password_verifies(self) -> None:
        stored = hash_password("correct-horse-battery-staple")
        result = verify_password(stored, "correct-horse-battery-staple")
        assert isinstance(result, PasswordVerificationResult)
        assert result.ok is True

    def test_wrong_password_fails(self) -> None:
        stored = hash_password("correct-horse-battery-staple")
        result = verify_password(stored, "wrong-horse")
        assert result.ok is False
        assert result.needs_rehash is False

    def test_malformed_hash_fails(self) -> None:
        result = verify_password("not-a-valid-phc-string", "any-password")
        assert result.ok is False
        assert result.needs_rehash is False

    def test_empty_password_fails_not_raises(self) -> None:
        stored = hash_password("correct-horse-battery-staple")
        result = verify_password(stored, "")
        assert result.ok is False

    def test_empty_password_does_not_invoke_argon2(self) -> None:
        # Defense-in-depth: an empty password must short-circuit before any
        # Argon2 computation. If the early-return path regresses, the call
        # below would still return ok=False, but it would also burn ~100 ms
        # and 64 MiB. The test asserts the wall-clock budget for an empty
        # input is well below a normal Argon2 verify.
        import time

        stored = hash_password("correct-horse-battery-staple")
        start = time.perf_counter()
        result = verify_password(stored, "")
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert result.ok is False
        # A real Argon2 verify is ~50-200ms; an early-return should be sub-ms.
        # Generous bound (10ms) to absorb CI noise while still failing on a
        # regression that runs the full hasher.
        assert elapsed_ms < 10.0, f"empty-password verify took {elapsed_ms:.2f}ms"

    def test_rejects_non_string_stored_hash(self) -> None:
        with pytest.raises(TypeError):
            verify_password(b"bytes", "pw")  # type: ignore[arg-type]

    def test_rejects_non_string_password(self) -> None:
        stored = hash_password("any-valid-pw")
        with pytest.raises(TypeError):
            verify_password(stored, b"bytes")  # type: ignore[arg-type]

    def test_fresh_hash_does_not_need_rehash(self) -> None:
        # Fresh hash uses current parameters; verify says no rehash needed.
        stored = hash_password("hunter2-correct-horse")
        result = verify_password(stored, "hunter2-correct-horse")
        assert result.ok is True
        assert result.needs_rehash is False
