"""HTTP surface: routers, schemas, middleware, error handlers.

Modules in this package are the only place the codebase imports from
FastAPI / Starlette outside of `app/auth/deps.py`. Routers are mounted
by `app/main.py::create_app` inside the lifespan that owns the
`Supervisor` and `EventBus`.

Public re-exports below cover the symbols the lifespan and tests
construct directly; concrete routers / handlers live in their own
modules and are imported by `app.main` only.
"""

from __future__ import annotations

from app.api.errors import (
    ERROR_CODES,
    ErrorCode,
    install_exception_handlers,
)
from app.api.middleware import LockedModeMiddleware

__all__ = [
    "ERROR_CODES",
    "ErrorCode",
    "LockedModeMiddleware",
    "install_exception_handlers",
]
