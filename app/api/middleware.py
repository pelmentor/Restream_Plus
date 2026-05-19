"""LockedModeMiddleware — closes the Phase 3 deferred router-level guard.

Phase 3's `require_unlocked_keys` dep at `app/auth/deps.py:147` only
fires on routes that declare it (transitively, via `SessionServiceDep`
or `ApiTokenServiceDep`). Phase 6 adds many new routes — run control,
targets CRUD, settings, nginx webhooks, health, WS — and a future
handler that consumes `SessionDep` directly would silently bypass the
guard.

Per Phase 6 design memo §Q5: the fix is **middleware**, not a
router-level Depends. Middleware runs unconditionally for every
request and cannot be forgotten when a new router is added. The
allowlist defines the small set of paths that MUST answer in locked
mode; everything else gets `503 service_locked` (or
`503 service_unlocking` with `Retry-After: 2`) preempting auth deps.

Allowlist (path-prefix match):
    /api/unlock         — the ONLY way out of locked mode
    /livez              — process liveness; never gated
    /healthz, /readyz   — readiness probes; report locked state in body
    /internal/rtmp/*    — nginx webhooks; rejected by their own handler
                          in locked mode (they 503 so OBS publishes deny)
    /internal/mtx/*     — MediaMTX dev-sidecar webhooks; mirror the
                          /internal/rtmp/ allowlist so an in-flight
                          publish at unlock time can still emit its
                          `not_ready` lifecycle signal and clear
                          `_obs_is_publishing`. The auth handler 503s
                          internally on locked state — same belt-and-
                          suspenders posture as /internal/rtmp/.
    SPA root + assets   — covered via `STATIC_PATH_PREFIXES` when Phase 7
                          mounts the SPA; the unlock UI must be reachable
                          to render the unlock screen at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.api.errors import ErrorCode
from app.auth.deps import RETRY_AFTER_LOCKED_SECONDS
from app.auth.key_material import KeyMaterial, KeyMaterialState

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Awaitable, Callable, Iterable

    from starlette.requests import Request


# Path prefixes that bypass the locked-mode guard. Matched via
# `_is_allowlisted` below: exact match, or trailing-slash prefix,
# or bare-prefix segment (so `/api/unlock` also covers
# `/api/unlock/anything` that a future sub-route might add).
DEFAULT_ALLOWLIST: Final[tuple[str, ...]] = (
    "/api/unlock",
    "/livez",
    "/readyz",
    "/healthz",
    # Tightened from `/internal/` per security review M-1: only the
    # ingest-server webhooks need locked-mode reachability. Both the
    # nginx-rtmp (production) and MediaMTX (dev) hooks 503 internally
    # on locked state; a future `/internal/debug` would NOT be
    # allowlisted by accident. Hex Audit Backend F12 (2026-05-18)
    # added `/internal/mtx/` so a publish that was in flight at
    # unlock time can still emit `not_ready` and reset the
    # supervisor's `_obs_is_publishing` flag.
    "/internal/rtmp/",
    "/internal/mtx/",
)

STATIC_PATH_PREFIXES: Final[tuple[str, ...]] = (
    "/assets/",
    "/static/",
)
"""SPA assets must always render so the unlock UI works pre-unlock."""


def _is_static_root(path: str) -> bool:
    """The SPA's `/` and `/index.html` must always succeed in locked mode."""
    return path in ("/", "/index.html")


def _is_allowlisted(path: str, allowlist: Iterable[str]) -> bool:
    for prefix in allowlist:
        if path == prefix.rstrip("/"):
            return True
        if prefix.endswith("/") and path.startswith(prefix):
            return True
        if not prefix.endswith("/") and path.startswith(prefix + "/"):
            return True
    return False


class LockedModeMiddleware(BaseHTTPMiddleware):
    """Reject requests when KeyMaterial is not READY.

    Order matters: this middleware sits AFTER `TrustedHostMiddleware`
    (if any) and BEFORE any dependency that reads from the database
    or constructs an auth service. The Phase 3 dep
    `require_unlocked_keys` is still wired into individual handlers
    as a belt-and-suspenders layer; this middleware is the suspenders.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        key_material: KeyMaterial,
        allowlist: tuple[str, ...] = DEFAULT_ALLOWLIST,
        static_prefixes: tuple[str, ...] = STATIC_PATH_PREFIXES,
    ) -> None:
        super().__init__(app)
        self._key_material = key_material
        self._allowlist = allowlist
        self._static_prefixes = static_prefixes

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if _is_static_root(path) or _is_allowlisted(path, self._allowlist):
            return await call_next(request)
        for prefix in self._static_prefixes:
            if path.startswith(prefix):
                return await call_next(request)

        state = self._key_material.state
        if state == KeyMaterialState.READY:
            return await call_next(request)
        if state == KeyMaterialState.UNLOCKING:
            return JSONResponse(
                status_code=503,
                content={"detail": ErrorCode.SERVICE_UNLOCKING.value},
                headers={"Retry-After": str(RETRY_AFTER_LOCKED_SECONDS)},
            )
        # LOCKED
        return JSONResponse(
            status_code=503,
            content={"detail": ErrorCode.SERVICE_LOCKED.value},
        )


__all__ = [
    "DEFAULT_ALLOWLIST",
    "STATIC_PATH_PREFIXES",
    "LockedModeMiddleware",
]
