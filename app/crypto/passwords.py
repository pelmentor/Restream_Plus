"""Admin-user password hashing.

Distinct from the master-passphrase KDF (`crypto.kdf`):

  - **Master passphrase** is the root of the encryption KEK. Its
    Argon2id parameters (in `kdf.py`) are tuned for one-per-boot
    derivation cost — 128 MiB, ~hundreds of ms — because losing the
    passphrase means losing the stream keys, and we accept the
    operator-facing latency.

  - **Admin password** is verified on every login. Its parameters
    (here) are tuned for per-request cost — 64 MiB, ~hundred ms — per
    ADR-0005.

Both use Argon2id (memory-hard, side-channel-resistant), but with
different cost surfaces and via different code paths. We use the
high-level `argon2.PasswordHasher` here so we get the PHC string format
and built-in rehash detection.

`needs_rehash` lets us migrate to stronger parameters in the future
without invalidating existing hashes — the next successful login
triggers a re-hash with the current parameters. Documented but not
implemented here; Phase 3 (auth) wires it.
"""

from __future__ import annotations

from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2 import exceptions as argon2_exceptions

ARGON2_TIME_COST = 3
ARGON2_MEMORY_COST_KIB = 64 * 1024  # 64 MiB — half of the master-passphrase KDF
ARGON2_PARALLELISM = 2
ARGON2_HASH_LENGTH = 32
ARGON2_SALT_LENGTH = 16

_HASHER = PasswordHasher(
    time_cost=ARGON2_TIME_COST,
    memory_cost=ARGON2_MEMORY_COST_KIB,
    parallelism=ARGON2_PARALLELISM,
    hash_len=ARGON2_HASH_LENGTH,
    salt_len=ARGON2_SALT_LENGTH,
)


@dataclass(frozen=True, slots=True)
class PasswordVerificationResult:
    """Outcome of `verify_password()`.

    The `ok` field is the only success/failure bit. The `needs_rehash`
    field, when `True`, signals the caller (auth/sessions.py) to compute
    a fresh hash with current parameters and persist it — the user does
    not see this. `needs_rehash` is meaningless when `ok=False`.
    """

    ok: bool
    needs_rehash: bool


def hash_password(password: str) -> str:
    """Hash an admin password. Returns a PHC-format string for storage.

    The stored string carries its own salt + parameters, so verifying it
    later does not require us to look up the parameters separately.
    """
    if not isinstance(password, str):
        raise TypeError("password must be a string")
    if len(password) == 0:
        raise ValueError("password must not be empty")
    return _HASHER.hash(password)


def verify_password(stored_hash: str, password: str) -> PasswordVerificationResult:
    """Constant-time verify of `password` against `stored_hash`.

    Returns `ok=False, needs_rehash=False` on any non-match (wrong
    password, malformed hash, parameter-incompatible hash, empty password).
    The `argon2-cffi` library performs the comparison in constant time
    relative to the hash bytes; we don't add our own timing-leveling here.

    Empty-password short-circuit (defense in depth): an empty password is
    never a valid login credential — `hash_password()` refuses to create
    one. Returning early without invoking Argon2 prevents an attacker
    submitting blank-password probes from forcing the server to spend
    64 MiB and ~100 ms per request. The rate limiter (ADR-0005) is the
    primary mitigation, but it is volatile across restarts, so cheap
    pre-filters are worthwhile.
    """
    if not isinstance(stored_hash, str):
        raise TypeError("stored_hash must be a string")
    if not isinstance(password, str):
        raise TypeError("password must be a string")
    if len(password) == 0:
        return PasswordVerificationResult(ok=False, needs_rehash=False)

    try:
        _HASHER.verify(stored_hash, password)
    except (
        argon2_exceptions.VerificationError,
        argon2_exceptions.InvalidHashError,
    ):
        # VerifyMismatchError is a VerificationError subclass; both are caught here.
        return PasswordVerificationResult(ok=False, needs_rehash=False)

    return PasswordVerificationResult(
        ok=True,
        needs_rehash=_HASHER.check_needs_rehash(stored_hash),
    )
