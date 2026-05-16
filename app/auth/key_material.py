"""Process-wide root-of-trust for derived encryption keys.

Per the Backend Architect memo for Phase 3 (Rule No.3 ideation):
this service is the load-bearing decision of the auth phase. Every
authenticated handler, cookie verification, API-token verification,
and stream-key encryption depends on whether the master passphrase
has been turned into derived keys yet. ADR-0005 §"Boot-time KEK
availability and locked-mode endpoint surface" describes the contract;
ADR-0006 governs the derivations themselves; ADR-0010 governs the
two modes (env / paste) and when each reaches `ready`.

Three states (one-way; there is no `ready -> locked` transition because
passphrase rotation is a full restart per ADR-0006 §"Passphrase
rotation"):

  locked      No keys derived. Constructed at boot; in paste mode the
              service holds here until the operator hits /api/unlock.
  unlocking   Argon2id derivation in flight. Brief but observable.
              Concurrent callers serialize on an internal asyncio.Lock.
  ready       Three subkeys derived; held in memory only.

The derivation runs on a thread (Argon2id at 128 MiB / 3 / 2 takes
~hundreds of ms — would otherwise block the event loop).
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

import structlog

from app.crypto import (
    HKDF_INFO_API_TOKEN_MAC,
    HKDF_INFO_SESSION_TOKEN_MAC,
    HKDF_INFO_STREAM_KEY,
    KDF_SALT_LENGTH,
    derive_root_key,
    derive_subkey,
)

if TYPE_CHECKING:  # pragma: no cover
    from pathlib import Path


_logger = structlog.get_logger(__name__)


class KeyMaterialState(StrEnum):
    LOCKED = "locked"
    UNLOCKING = "unlocking"
    READY = "ready"


@dataclass(frozen=True, slots=True)
class DerivedKeys:
    """The three purpose-bound subkeys derived from the master passphrase.

    Held in process memory only. The `__repr__` is overridden so that
    an accidental `repr(keys)` or a structlog event captured into a log
    line does not surface 96 bytes of derived key material. Use the
    explicit attribute accesses where needed.
    """

    stream_key_kek: bytes
    api_token_mac_key: bytes
    session_token_mac_key: bytes

    def __repr__(self) -> str:
        return "DerivedKeys(<redacted>)"


class ServiceLockedError(RuntimeError):
    """The process holds no derived keys yet (boot in paste mode, or
    a freshly-constructed instance in tests).

    The FastAPI dependency at `app.auth.deps` catches this and maps it
    to `HTTPException(503, detail="service_locked")` with `Retry-After: 2`.
    Outside FastAPI (background tasks, supervisor startup, unit tests)
    the exception bubbles.
    """


class PassphraseMismatchError(RuntimeError):
    """A non-empty passphrase was submitted but produced a different
    `stream_key_kek` than the one already cached.

    Reserved for the (currently unused) re-unlock verification path.
    Today, `unlock()` is a no-op when called against a ready instance,
    so this is only raised by code that explicitly opts in. Kept as a
    distinct type so a future re-unlock call site does not have to
    invent a new exception.
    """


class KeyMaterial:
    """Process-wide singleton holding the derived encryption keys.

    Constructed `LOCKED` with no keys. The first successful `unlock()`
    transitions to `READY`. Subsequent `unlock()` calls under `READY`
    are idempotent no-ops — they neither re-derive nor verify, because
    re-derivation would cost another 128 MiB / 100 ms Argon2 pass and
    the only attacker who can reach this method already has a
    rate-limited unlock endpoint in front of it (ADR-0010).

    Concurrent unlock attempts (paste-mode race: two operators submit
    near-simultaneously) are serialized by an internal `asyncio.Lock`.
    The second observer sees `state == READY` after the first returns
    and short-circuits without re-derivation.
    """

    def __init__(self, *, kdf_salt: bytes) -> None:
        if not isinstance(kdf_salt, bytes):
            raise TypeError("kdf_salt must be bytes")
        if len(kdf_salt) != KDF_SALT_LENGTH:
            raise ValueError(
                f"kdf_salt must be exactly {KDF_SALT_LENGTH} bytes, got {len(kdf_salt)}"
            )
        self._kdf_salt = kdf_salt
        self._state = KeyMaterialState.LOCKED
        self._keys: DerivedKeys | None = None
        self._lock = asyncio.Lock()

    @property
    def state(self) -> KeyMaterialState:
        """Current state. Monotonic: LOCKED -> UNLOCKING -> READY."""
        return self._state

    @property
    def is_ready(self) -> bool:
        return self._state == KeyMaterialState.READY

    def require_ready(self) -> DerivedKeys:
        """Return the derived keys, or raise `ServiceLockedError`.

        Synchronous so callers in hot paths (per-request cookie
        verification) don't await needlessly. Reads of `_state` and
        `_keys` are individually atomic under the GIL; the
        single-direction monotonic transition makes a torn read
        impossible — observing `READY` always implies `_keys` is set.
        """
        if self._state != KeyMaterialState.READY or self._keys is None:
            raise ServiceLockedError(
                f"derived keys not available (state={self._state.value!r}); "
                f"the process must complete /api/unlock before authenticated "
                f"requests can succeed (ADR-0010 paste-mode boot)."
            )
        return self._keys

    async def unlock(self, passphrase: str) -> None:
        """Derive the keys from `passphrase` and transition to READY.

        Idempotent under `READY`: returns immediately without
        re-deriving (see class docstring for rationale).

        Raises `ValueError` for an empty passphrase, and re-raises any
        exception from the Argon2 / HKDF derivation after rolling state
        back to LOCKED so the caller can retry.
        """
        if not isinstance(passphrase, str):
            raise TypeError("passphrase must be a string")
        if len(passphrase) == 0:
            raise ValueError("passphrase must not be empty")

        # Fast path without the lock: another coroutine already
        # completed the unlock.
        if self._is_ready_no_narrow():
            return

        async with self._lock:
            # Double-check inside the lock. The state is mutable so
            # the previous observation may be stale; mypy can't see
            # the mutation through `self._state` so we use the
            # helper below to avoid the spurious "non-overlapping
            # equality" warning.
            if self._is_ready_no_narrow():
                return
            self._state = KeyMaterialState.UNLOCKING
            try:
                keys = await asyncio.to_thread(self._derive_sync, passphrase)
            except BaseException:
                # Roll back so the operator can retry. We do NOT cache
                # the failure — Argon2 failures are not credential
                # failures, they're resource/library faults.
                self._state = KeyMaterialState.LOCKED
                raise
            self._keys = keys
            self._state = KeyMaterialState.READY
        _logger.info("key_material_unlocked")

    def _is_ready_no_narrow(self) -> bool:
        """Read `_state` through a function boundary so mypy does not
        carry an outdated narrowing across the call.

        The narrowing from `if self._state == KeyMaterialState.READY:
        return` (at the start of `unlock`) lasts for the rest of the
        function in mypy's analysis, but at runtime another coroutine
        may have mutated `_state`. This helper opaque-reads the state
        so mypy treats subsequent comparisons as unconstrained.
        """
        return self._state == KeyMaterialState.READY

    async def verify_passphrase(self, passphrase: str) -> bool:
        """Constant-time check: does `passphrase` derive the same
        `DerivedKeys` we hold in memory?

        Used by `POST /api/security/rotate-passphrase` (Phase 9) as the
        operator's second knowledge proof — the AuthReprompt grant
        proves they know the admin password (ADR-0005), this proves
        they know the master passphrase (ADR-0006). They are
        independent secrets.

        Returns False if not READY, if `passphrase` is empty, or if any
        of the three derived subkeys differ. Never raises on a wrong
        passphrase; only on programmer errors (TypeError) or a
        derivation engine fault (which bubbles).

        Cost: one full Argon2id pass (~hundreds of ms). Callers should
        wrap in a constant-time floor so a fast-path short-circuit
        (LOCKED, empty input) does not leak via timing.
        """
        if not isinstance(passphrase, str):
            raise TypeError("passphrase must be a string")
        if self._state != KeyMaterialState.READY or self._keys is None:
            return False
        if len(passphrase) == 0:
            return False
        trial = await asyncio.to_thread(self._derive_sync, passphrase)
        held = self._keys
        return (
            secrets.compare_digest(trial.stream_key_kek, held.stream_key_kek)
            and secrets.compare_digest(trial.api_token_mac_key, held.api_token_mac_key)
            and secrets.compare_digest(trial.session_token_mac_key, held.session_token_mac_key)
        )

    def rotate_in_place(self, *, new_kdf_salt: bytes, new_keys: DerivedKeys) -> None:
        """In-process rotation. Synchronous; single call-site
        (rotate-passphrase handler, AFTER the DB commit AND the
        `.kdf_salt` file rename have both succeeded).

        Two attribute assignments under the GIL — concurrent readers
        through `require_ready()` observe either the OLD pair (salt +
        keys) or the NEW pair, never a torn mix. In-flight requests
        that captured the old `DerivedKeys` reference continue to use
        it for the lifetime of their handler; since the same transaction
        that re-wrapped credentials also revoked their session, they
        are on their way out anyway.

        Raises `RuntimeError` if called from any state other than READY,
        and `ValueError` if `new_kdf_salt` is the wrong length — these
        are programmer-error guards, never operational paths.
        """
        if self._state != KeyMaterialState.READY:
            raise RuntimeError(f"rotate_in_place requires READY state, got {self._state.value!r}")
        if not isinstance(new_kdf_salt, bytes):
            raise TypeError("new_kdf_salt must be bytes")
        if len(new_kdf_salt) != KDF_SALT_LENGTH:
            raise ValueError(
                f"new_kdf_salt must be exactly {KDF_SALT_LENGTH} bytes, " f"got {len(new_kdf_salt)}"
            )
        self._kdf_salt = new_kdf_salt
        self._keys = new_keys

    def _derive_sync(self, passphrase: str) -> DerivedKeys:
        """Argon2id passphrase derivation + three HKDF subkey expansions.

        Runs in a worker thread (via `asyncio.to_thread`) so the
        ~hundred-millisecond Argon2 pass does not block the event loop.

        Memory hygiene (Phase 3 security review #11): the
        `passphrase.encode("utf-8")` step would normally produce an
        immutable `bytes` copy of the passphrase plaintext that survives
        until garbage collection. We use a `bytearray` instead so we
        can explicitly zero the buffer before it leaves scope. ADR-0006
        names heap-walking attackers as out-of-scope for memory
        mitigation, but zeroing is a free, honest defence: it removes
        the trivial residue without pretending to protect against
        process-memory snapshotting. The caller's `passphrase` `str`
        is still un-zeroable; we accept that limit.
        """
        encoded = bytearray(passphrase.encode("utf-8"))
        try:
            root_key = derive_root_key(bytes(encoded), self._kdf_salt)
            try:
                return DerivedKeys(
                    stream_key_kek=derive_subkey(root_key, HKDF_INFO_STREAM_KEY),
                    api_token_mac_key=derive_subkey(root_key, HKDF_INFO_API_TOKEN_MAC),
                    session_token_mac_key=derive_subkey(root_key, HKDF_INFO_SESSION_TOKEN_MAC),
                )
            finally:
                # `bytes` is immutable so we can't overwrite — but we
                # drop the reference so a GC pass can reclaim it.
                del root_key
        finally:
            # Overwrite the mutable buffer before scope exit. This is
            # the only encoding of the passphrase plaintext we control;
            # the caller's `str` we cannot zero.
            for i in range(len(encoded)):
                encoded[i] = 0


def load_or_create_kdf_salt(salt_path: Path) -> bytes:
    """Read the persistent salt from disk, or create one on first boot.

    Per ADR-0006 §"Key derivation", the salt is `KDF_SALT_LENGTH` (16)
    random bytes at `data_dir/.kdf_salt`. It must persist across
    container restarts because the same passphrase must derive the same
    keys, otherwise existing ciphertexts are unreadable after a
    restart.

    Atomic write-then-rename ensures a partial write cannot corrupt a
    pre-existing salt. If the salt file exists but has the wrong
    length, this function refuses to proceed — that's evidence of disk
    damage or a pre-versioning artifact, both of which deserve operator
    attention rather than a silent overwrite.
    """
    if salt_path.exists():
        salt = salt_path.read_bytes()
        if len(salt) != KDF_SALT_LENGTH:
            raise RuntimeError(
                f"KDF salt at {salt_path} has length {len(salt)}, "
                f"expected {KDF_SALT_LENGTH}. Refusing to overwrite "
                f"existing material; investigate and restore from backup."
            )
        return salt

    salt = secrets.token_bytes(KDF_SALT_LENGTH)
    salt_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = salt_path.with_suffix(salt_path.suffix + ".tmp")
    tmp.write_bytes(salt)
    tmp.replace(salt_path)
    return salt
