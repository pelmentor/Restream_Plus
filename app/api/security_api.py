"""Security router (Phase 9).

Houses three families of endpoints under the `/api/security` and
`/api/about` prefixes:

  POST /api/security/rotate-passphrase   — re-key all credentials,
                                            revoke all sessions + tokens.
  GET  /api/security/sessions            — list active HTTP sessions.
  DELETE /api/security/sessions/{fp}     — revoke a single HTTP session.
  GET  /api/about                        — version + uptime + last reboots.

`/api/security/tokens` lives in its own router (`api_tokens_api.py`)
because it predates this one; the two share the `/api/security`
namespace cleanly. The decision to consolidate them is a Phase 10+
concern — Rule №2 says don't carry the rename now.

Rotate-passphrase is the meaty endpoint. See `docs/architecture/
phase-9-design-memo.md` §I.3 for the locked design. The summary:

  1. Reprompt grant consumed.
  2. RunState gate (refuse during a live broadcast).
  3. Process-wide `rotation_lock` acquired.
  4. Validate (same-passphrase rejected).
  5. Verify old passphrase derives the in-memory KEK.
  6. Derive new KEK + sub-keys.
  7. Write `.kdf_salt.previous` + `.kdf_salt.new` files.
  8. ONE DB transaction: re-wrap every credential, delete all sessions,
     delete all api_tokens.
  9. Atomic rename `.kdf_salt.new` → `.kdf_salt`.
  10. `KeyMaterial.rotate_in_place` — atomic in-process swap.
  11. Audit + `delete_cookie` + 204.
  12. 1.0 s constant-time floor wrapping the whole handler.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import secrets as _stdlib_secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Final

import structlog
from fastapi import APIRouter, Header, Request, Response, status

from app.api.deps import SettingsDep
from app.api.errors import ErrorCode, http_exception
from app.api.schemas import (
    AboutView,
    HttpSessionView,
    IngestKeyRevealResponse,
    RotatePassphraseRequest,
)
from app.auth.deps import (
    AuthenticatedRequestDep,
    AuthStateDep,
    DerivedKeysDep,
    SessionDep,
)
from app.auth.key_material import DerivedKeys
from app.auth.reprompts import RepromptScope
from app.auth.sessions import build_cookie_delete_kwargs
from app.crypto.aead import (
    AEADDecryptionError,
    build_credential_aad,
    decrypt,
    encrypt,
)
from app.crypto.kdf import (
    HKDF_INFO_API_TOKEN_MAC,
    HKDF_INFO_SESSION_TOKEN_MAC,
    HKDF_INFO_STREAM_KEY,
    KDF_SALT_LENGTH,
    derive_root_key,
    derive_subkey,
)
from app.domain.run_state import RunState
from app.repositories.api_tokens import ApiTokensRepository
from app.repositories.audit_log import AuditLogRepository
from app.repositories.credentials import CredentialsRepository
from app.repositories.sessions import HttpSessionsRepository
from app.version import BUILD_SHA, __version__

# `IngestKeyRevealResponse` re-exported above so an OpenAPI consumer
# that imports schemas through `app.api.security_api` sees the shape.
# The handler itself lives in `app/api/settings_api.py`.
_ = IngestKeyRevealResponse


_logger = structlog.get_logger(__name__)

security_router = APIRouter(prefix="/api/security", tags=["security"])
about_router = APIRouter(prefix="/api/about", tags=["about"])

ROTATE_PASSPHRASE_TIMING_FLOOR_SECONDS: Final[float] = 1.0
"""Wall-clock floor on `rotate-passphrase` so the wrong-old-passphrase
path takes the same time as the full re-wrap path.

Argon2id at 128 MiB / 3 / 2 is ~500-800 ms; the floor pads to 1.0 s so
an attacker probing the old passphrase via timing cannot distinguish
'verify failed at step 5 in ~200 ms' from 'verify succeeded; full rewrap
in ~700 ms'. Per phase-9-design-memo §I.3 step 13."""

SESSION_FINGERPRINT_HEX_CHARS: Final[int] = 16
"""SHA-256 hex prefix length used as `id_fingerprint` for HTTP sessions.

16 hex chars (64 bits) is ample to disambiguate a single user's
at-most-handful of active sessions and short enough to display
compactly. The input itself is the `token_hash` HMAC fingerprint
(256 bits) — collision math is not the constraint."""

LAST_REBOOTS_LIMIT: Final[int] = 5


# ----------------------------------------------------------------------
# POST /api/security/rotate-passphrase
# ----------------------------------------------------------------------


@security_router.post("/rotate-passphrase", status_code=status.HTTP_204_NO_CONTENT)
async def rotate_passphrase(
    body: RotatePassphraseRequest,
    auth: AuthenticatedRequestDep,
    state: AuthStateDep,
    session: SessionDep,
    keys: DerivedKeysDep,
    response: Response,
    request: Request,
    settings: SettingsDep,
    x_reprompt_grant: Annotated[str | None, Header(alias="X-Reprompt-Grant")] = None,
) -> Response:
    """Re-key every credential under a new master passphrase. Revoke all
    sessions + api_tokens (they cannot be re-keyed). 204 on success.

    The body's `old_passphrase` is the master passphrase (ADR-0006);
    the reprompt grant proves the admin password (ADR-0005). Two
    knowledge proofs — see phase-9-design-memo §F.1.

    Atomicity: credential re-wrap + session delete + api_token delete
    all live in ONE SQLite transaction. The salt-file rename happens
    OUTSIDE that transaction, AFTER the commit. If the rename fails
    (single atomic `os.replace` syscall — practically never), we log
    CRITICAL `passphrase_rotation_orphaned` and the operator must
    intervene; the in-memory `DerivedKeys` is NOT rotated so the
    process keeps working with the OLD keys but the credentials in
    the DB are now wrapped under NEW keys → reads will fail until the
    operator restarts. Single-user appliance; deliberately blunt.
    """
    start = time.perf_counter()
    try:
        if not x_reprompt_grant:
            raise http_exception(ErrorCode.REPROMPT_REQUIRED, status.HTTP_403_FORBIDDEN)
        if not state.reprompts.consume(
            grant_id=x_reprompt_grant,
            user_id=auth.user.id,
            scope=RepromptScope.ROTATE_PASSPHRASE,
        ):
            raise http_exception(ErrorCode.REPROMPT_INVALID_OR_EXPIRED, status.HTTP_403_FORBIDDEN)

        # Run-state gate. Workers hold decrypted credential URLs in argv;
        # re-keying mid-stream is undefined (ADR-0006 §"Migration /
        # passphrase rotation — STOP-THE-WORLD operation").
        supervisor = request.app.state.supervisor
        if supervisor.run_state() is not RunState.OFFLINE:
            raise http_exception(ErrorCode.RUN_ACTIVE, status.HTTP_409_CONFLICT)

        # Same-passphrase guard — UX hint that the user typed identical
        # values; not a security boundary (a malicious caller could just
        # add a character and revert). 422 keeps form-field error mapping
        # consistent on the client. Constant-time comparison on the
        # unwrapped values (reviewer H-1 follow-up): `SecretStr.__eq__`
        # is constant-time but the explicit call makes the intent legible.
        #
        # NOTE: must use `HTTP_422_UNPROCESSABLE_CONTENT` (RFC 9110 name)
        # rather than the legacy `HTTP_422_UNPROCESSABLE_ENTITY`. Starlette
        # 0.49+ raises DeprecationWarning on attribute-access of the
        # legacy name; the test suite's `filterwarnings = ["error", ...]`
        # turns that warning into an exception, which surfaces as a 500
        # to the client (Phase 11 round-23 backend-test fallout). 401 /
        # 403 / 409 weren't renamed so the OTHER raise sites are unaffected.
        if _stdlib_secrets.compare_digest(
            body.old_passphrase.get_secret_value().encode("utf-8"),
            body.new_passphrase.get_secret_value().encode("utf-8"),
        ):
            raise http_exception(ErrorCode.SAME_PASSPHRASE, status.HTTP_422_UNPROCESSABLE_CONTENT)

        # Single process-wide lock guards step 9 + 10 against a second
        # concurrent rotate. `start_run` checks the same flag (run.py)
        # so a START attempt during rotation 409s with RUN_ACTIVE.
        if state.rotation_lock.locked():
            raise http_exception(ErrorCode.RUN_ACTIVE, status.HTTP_409_CONFLICT)

        async with state.rotation_lock:
            # Re-check run-state after acquiring the lock — a concurrent
            # start_run that won the race must be honored.
            if supervisor.run_state() is not RunState.OFFLINE:
                raise http_exception(ErrorCode.RUN_ACTIVE, status.HTTP_409_CONFLICT)

            # Verify old passphrase derives the in-memory KEK. Returns
            # False on mismatch; raises only on programmer errors.
            if not await state.key_material.verify_passphrase(
                body.old_passphrase.get_secret_value()
            ):
                raise http_exception(ErrorCode.INVALID_CREDENTIALS, status.HTTP_401_UNAUTHORIZED)

            # Derive new KEK + sub-keys.
            new_salt = _stdlib_secrets.token_bytes(KDF_SALT_LENGTH)
            new_root = derive_root_key(
                body.new_passphrase.get_secret_value().encode("utf-8"), new_salt
            )
            new_keys = DerivedKeys(
                stream_key_kek=derive_subkey(new_root, HKDF_INFO_STREAM_KEY),
                api_token_mac_key=derive_subkey(new_root, HKDF_INFO_API_TOKEN_MAC),
                session_token_mac_key=derive_subkey(new_root, HKDF_INFO_SESSION_TOKEN_MAC),
            )
            del new_root

            settings_obj = request.app.state.settings
            salt_path: Path = settings_obj.kdf_salt_path
            previous_path: Path = settings_obj.kdf_salt_previous_path
            new_path = salt_path.with_suffix(salt_path.suffix + ".new")

            # Phase 1: write the auxiliary files. `.previous` is a 24-h
            # rotation grace-window artifact for backup-restore scenarios
            # (ADR-0006 amended). `.new` is the staging file we'll atomic-
            # rename into place after the DB commits.
            old_salt_bytes = _read_kdf_salt(salt_path)
            _atomic_write_bytes(previous_path, old_salt_bytes)
            _atomic_write_bytes(new_path, new_salt)

            credentials_rewrapped = 0
            sessions_revoked = 0
            api_tokens_revoked = 0
            try:
                # Phase 2: ONE transaction for the DB work — uses the
                # request session so we don't end up with two
                # connections fighting for the SQLite write lock. The
                # request session may have autobegun a transaction
                # already (e.g., from cookie verification); flush +
                # commit closes it before we open ours explicitly.
                await session.commit()
                creds_repo = CredentialsRepository(session)
                async with session.begin():
                    cred_rows = await creds_repo.list_all_for_rewrap()
                    for row in cred_rows:
                        try:
                            plaintext = decrypt(
                                keys.stream_key_kek,
                                row.nonce,
                                row.ciphertext,
                                build_credential_aad(row.salt),
                            )
                        except AEADDecryptionError as exc:
                            # Old key cannot decrypt — something is wrong
                            # with at-rest state. Abort the whole rotation
                            # so partial state never lands.
                            _logger.exception(
                                "credential_rewrap_aead_failed",
                                credential_id=row.id,
                                target_id=row.target_id,
                            )
                            raise http_exception(
                                ErrorCode.NOT_FOUND,
                                status.HTTP_500_INTERNAL_SERVER_ERROR,
                            ) from exc
                        new_per_row_salt = _stdlib_secrets.token_bytes(KDF_SALT_LENGTH)
                        new_nonce, new_ct = encrypt(
                            new_keys.stream_key_kek,
                            plaintext,
                            build_credential_aad(new_per_row_salt),
                        )
                        # Drop plaintext reference asap. Python can't
                        # explicitly zero bytes; we accept that limit
                        # (ADR-0006 names process-memory snapshotting
                        # out of scope).
                        del plaintext
                        await creds_repo.rewrap(
                            credential_id=row.id,
                            ciphertext=new_ct,
                            nonce=new_nonce,
                            salt=new_per_row_salt,
                        )
                        credentials_rewrapped += 1
                    sessions_repo = HttpSessionsRepository(session)
                    sessions_revoked = await sessions_repo.delete_all()
                    tokens_repo = ApiTokensRepository(session)
                    api_tokens_revoked = await tokens_repo.delete_all()
                # Phase 3: atomic salt-file rename. After this point we're
                # committed. If it fails, the in-memory keys stay OLD and
                # the on-disk credentials are NEW — operator-intervention.
                try:
                    new_path.replace(salt_path)
                except OSError:
                    _logger.critical(
                        "passphrase_rotation_orphaned",
                        old_salt_hex=old_salt_bytes.hex(),
                        new_salt_hex=new_salt.hex(),
                        note=(
                            "DB credentials are re-wrapped under new keys but "
                            ".kdf_salt rename failed; restart with the new "
                            "passphrase will derive the new keys and read "
                            "the new ciphertexts correctly; manual file "
                            "replacement may be required if rename keeps "
                            "failing."
                        ),
                    )
                    raise
                # Phase 4: in-process key swap. Atomic under the GIL.
                state.key_material.rotate_in_place(new_kdf_salt=new_salt, new_keys=new_keys)
            except BaseException:
                # Best-effort cleanup of the staging .new file if the DB
                # transaction or anything else inside the block raised.
                with contextlib.suppress(Exception):
                    if new_path.exists():
                        new_path.unlink()
                raise

        audit = AuditLogRepository(state.sessionmaker)
        await audit.append(
            event_type="passphrase_rotated",
            actor=auth.user.username,
            data={
                "credentials_rewrapped_count": credentials_rewrapped,
                "sessions_revoked_count": sessions_revoked,
                "api_tokens_revoked_count": api_tokens_revoked,
            },
        )

        response.delete_cookie(**build_cookie_delete_kwargs(secure=settings.cookie_secure))  # type: ignore[arg-type]
        response.status_code = status.HTTP_204_NO_CONTENT
        return response
    finally:
        elapsed = time.perf_counter() - start
        remaining = ROTATE_PASSPHRASE_TIMING_FLOOR_SECONDS - elapsed
        if remaining > 0:
            await asyncio.sleep(remaining)


# ----------------------------------------------------------------------
# GET /api/security/sessions — list active HTTP sessions
# ----------------------------------------------------------------------


@security_router.get("/sessions", response_model=list[HttpSessionView])
async def list_http_sessions(
    auth: AuthenticatedRequestDep,
    session: SessionDep,
) -> list[HttpSessionView]:
    """Return the current user's active HTTP sessions, newest-seen first.

    `id_fingerprint` is `sha256(token_hash).hexdigest()[:16]` — a stable
    16-hex client-side ID for revoke. The raw `token_hash` (HMAC of the
    cookie value) never leaves the server.

    `is_current` is server-computed by comparing each row's `token_hash`
    against the request's session. Bearer-auth callers (`auth.session is
    None`) see `is_current=False` on every row.
    """
    repo = HttpSessionsRepository(session)
    rows = await repo.list_active_for_user(auth.user.id, datetime.now(tz=UTC))
    current_hash = auth.session.token_hash if auth.session is not None else None
    return [
        HttpSessionView(
            id_fingerprint=_fingerprint(row.token_hash),
            created_at=row.created_at,
            last_seen_at=row.last_seen_at,
            ip=row.ip,
            user_agent=row.user_agent,
            is_current=(current_hash is not None and row.token_hash == current_hash),
        )
        for row in rows
    ]


# ----------------------------------------------------------------------
# DELETE /api/security/sessions/{id_fingerprint} — revoke one session
# ----------------------------------------------------------------------


@security_router.delete(
    "/sessions/{id_fingerprint}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def revoke_http_session(
    id_fingerprint: str,
    auth: AuthenticatedRequestDep,
    state: AuthStateDep,
    session: SessionDep,
    response: Response,
    settings: SettingsDep,
) -> Response:
    """Revoke a single HTTP session for the current user.

    No reprompt — matches logout-all's posture (ADR-0005). If the
    fingerprint matches the current request's session, the cookie is
    deleted; the next request 401s and the SPA's global error bus
    redirects to /login.

    The lookup is a scan over the user's at-most-handful of active
    sessions — `id_fingerprint` is a hash OF the primary key, not a
    column, so SQL filtering isn't possible without a generated
    column. Premature index; revisit if N ever exceeds a handful.
    """
    repo = HttpSessionsRepository(session)
    rows = await repo.list_active_for_user(auth.user.id, datetime.now(tz=UTC))
    match = next((row for row in rows if _fingerprint(row.token_hash) == id_fingerprint), None)
    if match is None:
        raise http_exception(ErrorCode.SESSION_NOT_FOUND, status.HTTP_404_NOT_FOUND)

    await repo.delete(match.token_hash)
    await session.commit()
    audit = AuditLogRepository(state.sessionmaker)
    await audit.append(
        event_type="session_revoked",
        actor=auth.user.username,
        data={"fingerprint": id_fingerprint},
    )
    if auth.session is not None and match.token_hash == auth.session.token_hash:
        response.delete_cookie(**build_cookie_delete_kwargs(secure=settings.cookie_secure))  # type: ignore[arg-type]
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


# ----------------------------------------------------------------------
# GET /api/about — version + uptime + last reboots
# ----------------------------------------------------------------------


@about_router.get("", response_model=AboutView)
async def get_about(
    _: AuthenticatedRequestDep,
    state: AuthStateDep,
    session: SessionDep,
    request: Request,
) -> AboutView:
    """Version + build SHA + lifespan-captured `started_at` + uptime +
    the 5 most-recent `app_started` audit events (Phase 9; phase-9-
    design-memo §I.6).

    No reprompt — informational only.
    """
    started_at: datetime | None = getattr(request.app.state, "started_at", None)
    if started_at is None:
        # The lifespan didn't capture started_at — bootstrap bug, not a
        # recoverable state. Surface as 500 with a clear marker rather
        # than fall back to "now" (which would lie about uptime).
        raise http_exception(
            ErrorCode.AUTH_STATE_NOT_INITIALISED,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    now = datetime.now(tz=UTC)
    audit = AuditLogRepository(state.sessionmaker)
    last_reboots = await audit.list_app_starts(session, limit=LAST_REBOOTS_LIMIT)
    return AboutView(
        version=__version__,
        build_sha=BUILD_SHA or "dev",
        started_at=started_at,
        uptime_seconds=max(0.0, (now - started_at).total_seconds()),
        last_reboots=last_reboots,
    )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _fingerprint(token_hash: bytes) -> str:
    """SHA-256 hex prefix used as the client-visible session ID.

    Token_hash is itself an HMAC fingerprint of the cookie value
    (ADR-0005). Hashing again before exposure means a DB-leak attacker
    cannot correlate the value the client knows with the value stored
    on disk.
    """
    return hashlib.sha256(token_hash).hexdigest()[:SESSION_FINGERPRINT_HEX_CHARS]


def _read_kdf_salt(path: Path) -> bytes:
    """Read the persistent KDF salt for archiving as `.kdf_salt.previous`."""
    blob = path.read_bytes()
    if len(blob) != KDF_SALT_LENGTH:
        raise RuntimeError(f"on-disk KDF salt has length {len(blob)}, expected {KDF_SALT_LENGTH}")
    return blob


def _atomic_write_bytes(target: Path, payload: bytes) -> None:
    """Write `payload` to `target` via tmp + rename (single-syscall on
    POSIX rename + Windows MoveFileEx).
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(payload)
    tmp.replace(target)


__all__ = ["about_router", "security_router"]
