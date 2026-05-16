"""Phase 6 FastAPI dependency helpers — supervisor / bus / settings.

`app/auth/deps.py` covers the auth surface. This module covers the
Phase 6 additions: the Supervisor, the EventBus, and the AppSettings
attached to `app.state` by the lifespan in `app/main.py`. Kept
distinct from the auth deps so the import graph stays acyclic — auth
deps stand alone for the auth tests, and these are only pulled in by
handlers that need supervisor/bus/settings.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.api.errors import ErrorCode
from app.config import AppSettings
from app.fanout.event_bus import EventBus
from app.fanout.supervisor import Supervisor


def get_supervisor(request: Request) -> Supervisor:
    """Read the lifespan-built supervisor off the FastAPI app.

    Mirrors the contract `app/auth/deps.py::get_auth_state` uses for
    `AuthState`: a missing attribute is a structural bug (lifespan
    never ran or did not finish), not a recoverable state. A 500 with
    `auth_state_not_initialised` is correct — a confusing
    `AttributeError` further down the call stack helps no-one.

    FakeSupervisor (tests) and real Supervisor both quack the same
    public surface, so the runtime type-check is just `is not None`.
    Mypy sees the declared return type and treats both as the
    Supervisor surface; tests substitute a FakeSupervisor at lifespan
    time and the handlers don't know the difference.
    """
    sup: Supervisor | None = getattr(request.app.state, "supervisor", None)
    if sup is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ErrorCode.AUTH_STATE_NOT_INITIALISED.value,
        )
    return sup


SupervisorDep = Annotated[Supervisor, Depends(get_supervisor)]


def get_event_bus(request: Request) -> EventBus:
    bus = getattr(request.app.state, "event_bus", None)
    if not isinstance(bus, EventBus):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ErrorCode.AUTH_STATE_NOT_INITIALISED.value,
        )
    return bus


EventBusDep = Annotated[EventBus, Depends(get_event_bus)]


def get_settings(request: Request) -> AppSettings:
    s = getattr(request.app.state, "settings", None)
    if not isinstance(s, AppSettings):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ErrorCode.AUTH_STATE_NOT_INITIALISED.value,
        )
    return s


SettingsDep = Annotated[AppSettings, Depends(get_settings)]


__all__ = [
    "EventBusDep",
    "SettingsDep",
    "SupervisorDep",
    "get_event_bus",
    "get_settings",
    "get_supervisor",
]
