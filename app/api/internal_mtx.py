"""Internal MediaMTX webhooks: /internal/mtx/auth + /internal/mtx/lifecycle.

MediaMTX is the dev-only RTMP server we run from `start.py` so the
operator can test the OBS → server → fanout path locally without the
full Docker image (the dev box has no Docker). It is NOT the
production ingest (production uses nginx-rtmp inside the container
per ADR-0003); see ADR-0003 §"Open questions" entry of 2026-05-18
for why we deferred adopting MediaMTX as the production ingest.

Two endpoints, both loopback-gated:

- `/internal/mtx/auth` mirrors the role of `/internal/rtmp/publish`:
  MediaMTX POSTs a JSON body with `action` / `path` / `protocol` /
  etc. for every publish (and read) attempt; we extract the stream
  key from `path`, compare against `settings.ingest_key_current` (+
  the rotation `previous`) with timing-safe equality, and return
  200 / 403 / 503. The supervisor is notified of publish-began on
  success — same as the nginx-rtmp path.

- `/internal/mtx/lifecycle` mirrors `/internal/rtmp/publish_done`:
  MediaMTX's `runOnNotReady` hook is a process-exec contract (not an
  HTTP webhook), so `dev/mediamtx.yml` configures the hook to spawn
  `curl` against this endpoint with `event=not_ready&path=...`. The
  endpoint notifies the supervisor that publishing ended.

The endpoints stay mounted in production builds with no risk — the
loopback gate is identical to `/internal/rtmp/*`, so an external
peer hitting them gets 403. In production with nginx-rtmp running,
MediaMTX simply isn't there to call them.
"""

from __future__ import annotations

import hmac
import ipaddress
import re
from datetime import UTC, datetime
from typing import Annotated, Final, Literal

import structlog
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.deps import SupervisorDep
from app.api.errors import ErrorCode
from app.auth.deps import SessionDep
from app.auth.key_material import KeyMaterialState
from app.repositories.settings_repo import SettingsRepository

_logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/internal/mtx", tags=["internal"])


# Hex Audit footgun-hunter F3/F4 (2026-05-18): cap the request body
# at entry. Legitimate MediaMTX hook bodies (JSON auth body + form
# lifecycle body) are <300 bytes; a 4 KB ceiling is generous and
# rejects megabyte-scale payloads BEFORE FastAPI's body parser
# allocates them. Per-field `max_length` (below on MtxAuthBody) is a
# layered defence, not a substitute — Pydantic validation runs AFTER
# the body has been fully read.
#
# CR2-H1 (2026-05-18): `Final[int]` — mypy now blocks accidental
# re-assignment that would silently weaken the cap during a test run.
MAX_INTERNAL_MTX_BODY_BYTES: Final[int] = 4 * 1024


# ----------------------------------------------------------------------
# Loopback safety + locked-mode helpers (mirror internal_rtmp.py)
# ----------------------------------------------------------------------


def _assert_loopback(request: Request) -> None:
    peer = request.client.host if request.client is not None else None
    if peer is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN) from exc
    if not addr.is_loopback:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


def _assert_small_body(request: Request) -> None:
    """Reject request bodies above `MAX_INTERNAL_MTX_BODY_BYTES`.

    Uses the advertised `Content-Length` header so we never read the
    oversized body into memory. Chunked clients (no Content-Length)
    are rejected on this route — MediaMTX always sends Content-Length
    for both the auth JSON POST and the lifecycle form POST.

    Wired as a route-decorator `dependencies=[Depends(_assert_small_body)]`
    on each route so FastAPI resolves it BEFORE the Pydantic body /
    Form parameters trigger body reading. Calling this as the first
    line of the path-operation function would be too late: by then
    FastAPI has already buffered the entire body to validate the body
    parameter.
    """
    raw_len = request.headers.get("content-length")
    if raw_len is None:
        # Chunked or missing — refuse rather than risk an unbounded read.
        raise HTTPException(
            status_code=status.HTTP_411_LENGTH_REQUIRED,
        )
    try:
        body_len = int(raw_len)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST) from exc
    if body_len < 0 or body_len > MAX_INTERNAL_MTX_BODY_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        )


def _locked_mode_503() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=ErrorCode.SERVICE_LOCKED.value,
    )


def _keys_match(
    submitted: str,
    current: str,
    previous: str | None,
    *,
    grace_until: datetime | None,
    now: datetime,
) -> bool:
    """Timing-safe compare with grace-window enforcement.

    Hex Audit BA-F1 (2026-05-18): `previous` is only accepted while
    `grace_until` is still in the future. Both comparisons ALWAYS run
    — even when `previous` is not honoured we compare against a
    fixed-length sentinel — so wall-clock duration doesn't leak the
    rotation state. (This used to be the only difference from
    `internal_rtmp._keys_match`; the nginx-rtmp adapter has now been
    aligned to the same shape.)
    """
    if previous is not None and grace_until is not None and now < grace_until:
        previous_valid = True
        previous_for_compare = previous
    else:
        previous_valid = False
        previous_for_compare = "\0" * len(current)
    current_ok = hmac.compare_digest(submitted, current)
    prev_ok = hmac.compare_digest(submitted, previous_for_compare)
    return current_ok or (previous_valid and prev_ok)


# `secrets.token_urlsafe()` (`app/db/schema_init.py::INGEST_KEY_BYTES`)
# produces keys from `[A-Za-z0-9_-]`. We tighten `_extract_ingest_key` to
# THE SAME charset so the regex is the source of truth, not a property
# of the key generator: even if the generator ever changed to allow
# shell metacharacters, MediaMTX's `runOnReady` curl couldn't be tricked
# into command injection via `$MTX_PATH` (the reviewer's defence-in-depth
# concern even though the auth would have to succeed first).
#
# CR2-M3 (2026-05-18): canonical form is hyphen-LAST inside a character
# class; the previous `\-` escape is portable but flags under ruff `RE`
# checks in stricter configs.
_INGEST_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# FG2-M3 (2026-05-18): short-circuit on path length BEFORE building a
# split list. Legit MediaMTX `path` is `live/<22-char-key>` ≈ 27 chars;
# allow comfortable headroom for future routing shapes. This bound is
# below `MtxAuthBody.path` `max_length=512` so anything we reach here
# is well under the Pydantic per-field cap.
_MAX_PATH_FOR_KEY_EXTRACT: Final[int] = 256


def _extract_ingest_key(path: str) -> str | None:
    # OBS RTMP URL is `rtmp://host:1935/live/<key>`; MediaMTX surfaces
    # that as `path = "live/<key>"`. nginx-rtmp uses the same shape
    # (application=`live`, name=`<key>`) so the extracted key matches
    # what `_keys_match` would receive from the nginx-rtmp adapter.
    # Anything not in `live/<charset-clean-key>` shape is treated as a
    # bad publish.
    if len(path) > _MAX_PATH_FOR_KEY_EXTRACT:
        return None
    parts = path.split("/")
    if len(parts) != 2 or parts[0] != "live" or not parts[1]:
        return None
    if not _INGEST_KEY_RE.match(parts[1]):
        return None
    return parts[1]


# ----------------------------------------------------------------------
# POST /internal/mtx/auth — MediaMTX `authHTTPAddress` adapter
# ----------------------------------------------------------------------


class MtxAuthBody(BaseModel):
    """MediaMTX's auth POST body. Fields from
    https://mediamtx.org/docs/usage/authentication. Only `action` and
    `path` are load-bearing for us; the rest are accepted for
    schema-fidelity but ignored.

    Per-field `max_length` is layered defence behind the entry-level
    body-size cap (`MAX_INTERNAL_MTX_BODY_BYTES`). Legitimate values
    are tiny — `path` is `live/<22-char-key>`, `protocol`/`ip` are
    short — so 512 chars is huge slack while rejecting obvious abuse.
    """

    action: Literal["publish", "read", "playback", "api", "metrics", "pprof"]
    path: str = Field(default="", max_length=512)
    protocol: str = Field(default="", max_length=64)
    user: str = Field(default="", max_length=256)
    password: str = Field(default="", max_length=512)
    token: str = Field(default="", max_length=512)
    ip: str = Field(default="", max_length=64)
    id: str = Field(default="", max_length=128)
    query: str = Field(default="", max_length=1024)


@router.post(
    "/auth",
    status_code=status.HTTP_200_OK,
    # CR2-H2: declared dependencies run in the listed order BEFORE
    # body / session resolution. Loopback gate first so a non-loopback
    # probe gets a 403 without ever opening a DB session slot.
    dependencies=[Depends(_assert_loopback), Depends(_assert_small_body)],
)
async def on_auth(
    request: Request,
    session: SessionDep,
    supervisor: SupervisorDep,
    body: MtxAuthBody,
) -> dict[str, str]:
    """Authenticate a MediaMTX publish (or local read) by ingest key.

    Returns:
        200 — MediaMTX accepts the action.
        403 — wrong key or unsupported `action`/`path` shape; also non-
              loopback peer.
        411 — request has no Content-Length header.
        413 — request body exceeds `MAX_INTERNAL_MTX_BODY_BYTES`.
        503 — service locked; publish fails until operator unlocks.
    """
    # Reads are loopback-only by virtue of the route-level `Depends(_assert_loopback)`
    # above — only our ffmpeg fan-out workers (running in the same
    # process tree as the control plane) pull from MediaMTX. Allow
    # `read` without further checks; the LAN cannot reach this endpoint.
    if body.action == "read":
        return {"status": "ok"}

    # All other admin-style actions are off — we never expose
    # MediaMTX API/metrics/pprof through any auth path, and
    # `playback` is a recorded-file feature we don't use.
    if body.action != "publish":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ErrorCode.NOT_FOUND.value,
        )

    state = request.app.state.auth
    if state.key_material.state != KeyMaterialState.READY:
        raise _locked_mode_503()

    submitted_key = _extract_ingest_key(body.path)
    if submitted_key is None:
        _logger.warning("mtx_publish_rejected", reason="malformed_path")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ErrorCode.INGEST_KEY_INVALID.value,
        )

    current = await SettingsRepository(session).get()
    if not _keys_match(
        submitted_key,
        current.ingest_key_current,
        current.ingest_key_previous,
        grace_until=current.ingest_key_grace_until,
        now=datetime.now(tz=UTC),
    ):
        _logger.warning("mtx_publish_rejected", reason="ingest_key_mismatch")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ErrorCode.INGEST_KEY_INVALID.value,
        )

    supervisor.notify_obs_publish_began()
    return {"status": "ok"}


# ----------------------------------------------------------------------
# POST /internal/mtx/lifecycle — runOnReady / runOnNotReady curl target
# ----------------------------------------------------------------------


@router.post(
    "/lifecycle",
    status_code=status.HTTP_200_OK,
    # CR2-H2: loopback first, then body-size cap. Both fire before
    # Form parameters resolve.
    dependencies=[Depends(_assert_loopback), Depends(_assert_small_body)],
)
async def on_lifecycle(
    supervisor: SupervisorDep,
    event: Annotated[Literal["ready", "not_ready"], Form(alias="event")],
    path: Annotated[str, Form(alias="path", max_length=512)] = "",
) -> dict[str, str]:
    """Inform the supervisor that publishing started or ended.

    MediaMTX's hooks are process-exec, not HTTP, so `dev/mediamtx.yml`
    wires `runOnReady` / `runOnNotReady` to spawn a `curl` that POSTs
    here with `event` (= `ready` / `not_ready`) and `path` (= MTX_PATH).
    `ready` is informational — the supervisor was already notified in
    `on_auth`; this is the source-of-truth signal for `not_ready`
    transitions where there is no auth hook.

    `path` is currently unused (reserved for future per-stream routing)
    but kept on the wire to preserve the dev/mediamtx.yml curl contract
    AND to anchor the Hex Audit footgun-hunter F4 `max_length=512` cap.
    """
    _ = path  # field validated for length; value currently unused
    if event == "not_ready":
        supervisor.notify_obs_publish_ended()
    return {"status": "ok"}


__all__ = ["router"]
