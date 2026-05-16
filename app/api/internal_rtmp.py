"""Internal nginx-rtmp webhooks: /internal/rtmp/publish + /publish_done.

Per Phase 6 design memo §Q9: these endpoints are called by nginx-rtmp
from 127.0.0.1 inside the same container (ADR-0003). Authentication
is loopback-only — the immediate peer IP must be a loopback address.
`X-Forwarded-For` is NOT honoured here even if it's present (the
trusted-proxy logic from `auth/rate_limit.py` does not apply); a
future reverse-proxy misconfiguration that routes external traffic
to `/internal/*` will still 403 on the loopback check.

`publish` checks the published stream name (the "ingest key") against
`settings.ingest_key_current` and `settings.ingest_key_previous` using
`hmac.compare_digest` for timing-safety. On match → 200 (nginx allows
the publish); on mismatch → 403 (nginx denies). The current state of
the supervisor (locked / unlocking / armed / live) is reflected by
the handler: in locked mode it 503s so OBS publishes fail until the
operator unlocks.

`publish_done` notifies the supervisor that OBS stopped publishing
(transitions LIVE → ARMED). nginx doesn't act on the response body
but expects a 2xx.
"""

from __future__ import annotations

import hmac
import ipaddress
from typing import Annotated

import structlog
from fastapi import APIRouter, Form, HTTPException, Request, status

from app.api.deps import SupervisorDep
from app.api.errors import ErrorCode
from app.auth.deps import SessionDep
from app.auth.key_material import KeyMaterialState
from app.repositories.settings_repo import SettingsRepository

_logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/internal/rtmp", tags=["internal"])


def _assert_loopback(request: Request) -> None:
    """Reject any request whose immediate peer is not loopback.

    Phase 6 design memo §Q9: the trusted-proxy logic is intentionally
    not consulted here. A request that reaches FastAPI on the
    `/internal/*` prefix from an external peer is a misconfiguration
    (or an attack) and must be rejected.

    Parsing via `ipaddress.ip_address` covers `127.0.0.0/8`, `::1`,
    and the IPv4-mapped IPv6 form `::ffff:127.0.0.1` (which arises in
    dual-stack uvicorn deployments). Per security review H-1, we DO
    NOT include the string `"localhost"` — `request.client.host` is a
    numeric peer IP from the ASGI scope, never a hostname.
    """
    peer = request.client.host if request.client is not None else None
    if peer is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN) from exc
    if not addr.is_loopback:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


def _locked_mode_503() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=ErrorCode.SERVICE_LOCKED.value,
    )


@router.post("/publish", status_code=status.HTTP_200_OK)
async def on_publish(
    request: Request,
    session: SessionDep,
    supervisor: SupervisorDep,
    # nginx-rtmp posts `name` and incidental fields (`addr`, `clientid`,
    # `app`, `flashver`, etc.). We only need `name`.
    name: Annotated[str, Form(alias="name")],
) -> dict[str, str]:
    """Authenticate an incoming RTMP publish by ingest key match.

    Returns:
        200 — nginx allows the publish; OBS proceeds.
        403 — wrong key; nginx denies.
        503 — service locked; OBS publish fails until operator unlocks.
    """
    _assert_loopback(request)
    state = request.app.state.auth
    if state.key_material.state != KeyMaterialState.READY:
        raise _locked_mode_503()

    current = await SettingsRepository(session).get()
    if not _keys_match(name, current.ingest_key_current, current.ingest_key_previous):
        # Don't echo the supplied key into the log line — could land in
        # disk logs the operator shares for debugging.
        _logger.warning("rtmp_publish_rejected", reason="ingest_key_mismatch")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ErrorCode.INGEST_KEY_INVALID.value,
        )

    # Match — let the supervisor know publishing started.
    supervisor.notify_obs_publish_began()
    return {"status": "ok"}


@router.post("/publish_done", status_code=status.HTTP_200_OK)
async def on_publish_done(
    request: Request,
    supervisor: SupervisorDep,
    name: Annotated[str | None, Form(alias="name")] = None,
) -> dict[str, str]:
    """OBS stopped publishing. Always 200 — nginx ignores the body but
    expects a 2xx. The handler informs the supervisor unconditionally;
    the supervisor's notify method is a no-op if RunState is not LIVE."""
    _assert_loopback(request)
    _ = name  # not used; nginx supplies it for completeness
    supervisor.notify_obs_publish_ended()
    return {"status": "ok"}


def _keys_match(submitted: str, current: str, previous: str | None) -> bool:
    """Timing-safe compare against the current key and (if any) the
    previous-key during the rotation grace window.

    Always runs BOTH comparisons (no short-circuit on current-key
    match) so the wall-clock duration leaks nothing about WHICH key
    matched — defence-in-depth per security review L-4. The cost is
    one extra `compare_digest` (sub-microsecond); the property is
    that a successful current-key match has the same timing as a
    successful previous-key match.
    """
    current_ok = hmac.compare_digest(submitted, current)
    prev_ok = previous is not None and hmac.compare_digest(submitted, previous)
    return current_ok or prev_ok


__all__ = ["router"]
