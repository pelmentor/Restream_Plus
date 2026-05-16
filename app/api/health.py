"""Health endpoints: /livez, /readyz, /healthz.

Per Phase 6 design memo §Q12:

  /livez   → always 200 with `{"status":"alive"}` if the process is up.
             No check is made; "the process is responding" IS the check.

  /readyz  → composite check of:
               1. DB SELECT 1 (100 ms timeout)
               2. Supervisor heartbeat freshness (HEARTBEAT_STALE_AFTER)
               3. EventBus drop counter (warn-in-body, not 503)
               4. KeyMaterial state (LOCKED → not ready)
               5. nginx-rtmp probe (only when `healthz_check_nginx=True`)
             Returns 200 only when every gating check is ok; otherwise
             503 with the same body shape so clients can render the
             failed check.

  /healthz → ALIAS for /readyz (same path handler). Kubernetes-style
             tooling often probes `/healthz`; we serve both names.

ADR-0011 details the contract. The write-canary against a
`health_probe` row is intentionally NOT implemented — design memo
defers it as over-engineering for v1; SQLite WAL writes are sub-ms
and a separate write probe doubles DB ops per readiness poll.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Final

import structlog
from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.api.deps import EventBusDep, SettingsDep, SupervisorDep
from app.api.schemas import CheckResult, HealthResponse
from app.auth.deps import AuthStateDep, SessionDep

_logger = structlog.get_logger(__name__)

router = APIRouter(tags=["health"])

DB_PROBE_TIMEOUT_SECONDS: Final[float] = 0.1
NGINX_PROBE_TIMEOUT_SECONDS: Final[float] = 0.05


# ----------------------------------------------------------------------
# /livez — unconditional
# ----------------------------------------------------------------------


@router.get("/livez", response_model=HealthResponse)
async def livez() -> HealthResponse:
    return HealthResponse(status="alive")


# ----------------------------------------------------------------------
# /readyz + /healthz — composite checks
# ----------------------------------------------------------------------


@router.get("/readyz", response_model=HealthResponse)
async def readyz(
    response: Response,
    session: SessionDep,
    state: AuthStateDep,
    supervisor: SupervisorDep,
    bus: EventBusDep,
    settings: SettingsDep,
) -> HealthResponse:
    checks: dict[str, CheckResult] = {}

    # 1. DB
    db_check = await _check_db(session)
    checks["db"] = db_check

    # 2. Supervisor heartbeat
    age = supervisor.heartbeat_age()
    sup_check = CheckResult(
        ok=not supervisor.heartbeat_is_stale(),
        detail={"last_tick_seconds_ago": age.total_seconds()},
    )
    checks["supervisor"] = sup_check

    # 3. EventBus drop counter — warning-in-body, never a 503.
    dropped = bus.dropped_total
    checks["event_bus"] = CheckResult(ok=True, detail={"dropped_total": dropped})

    # 4. KeyMaterial state
    km_state = state.key_material.state.value
    checks["passphrase"] = CheckResult(
        ok=(km_state == "ready"),
        detail={"state": km_state},
    )

    # 5. nginx-rtmp (configurable; default off in dev — Phase 10 flips
    # the default when the s6 bundle ships).
    if settings.healthz_check_nginx:
        nginx_check = await _check_nginx_rtmp()
        checks["nginx_rtmp"] = nginx_check

    # Composite: 200 iff every gating check is ok. The event_bus check
    # is intentionally non-gating; it surfaces a warning in the body
    # but does not flip /readyz to 503.
    gating_keys = ("db", "supervisor", "passphrase", "nginx_rtmp")
    all_ok = all(checks[k].ok for k in gating_keys if k in checks)
    if all_ok:
        return HealthResponse(status="ready", checks=checks)
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(status="not_ready", checks=checks)


@router.get("/healthz", response_model=HealthResponse)
async def healthz(
    response: Response,
    session: SessionDep,
    state: AuthStateDep,
    supervisor: SupervisorDep,
    bus: EventBusDep,
    settings: SettingsDep,
) -> HealthResponse:
    """ADR-0011 alias for /readyz. Different deployment tools probe
    different paths; serving both names removes a per-deployment knob."""
    return await readyz(response, session, state, supervisor, bus, settings)


# ----------------------------------------------------------------------
# Internal probe helpers
# ----------------------------------------------------------------------


async def _check_db(session: SessionDep) -> CheckResult:
    try:
        async with asyncio.timeout(DB_PROBE_TIMEOUT_SECONDS):
            await session.execute(text("SELECT 1"))
    except TimeoutError:
        return CheckResult(ok=False, detail={"error": "timeout"})
    except Exception as exc:
        return CheckResult(ok=False, detail={"error": type(exc).__name__})
    return CheckResult(ok=True)


async def _check_nginx_rtmp() -> CheckResult:
    """TCP-connect to 127.0.0.1:1935. Phase 10 (s6 bundle) flips
    `settings.healthz_check_nginx=True` by default; until then this
    probe is opt-in so dev runs (no nginx sibling) don't flap.

    Code review H-2: use `get_running_loop()` — `get_event_loop()`
    is deprecated in async contexts on Python 3.10+ and will raise
    when no loop is current."""
    try:
        async with asyncio.timeout(NGINX_PROBE_TIMEOUT_SECONDS):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setblocking(False)
            try:
                await asyncio.get_running_loop().sock_connect(sock, ("127.0.0.1", 1935))
            finally:
                sock.close()
    except (TimeoutError, OSError) as exc:
        return CheckResult(ok=False, detail={"error": type(exc).__name__})
    return CheckResult(ok=True)


__all__ = ["router"]
