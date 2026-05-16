"""Persistence layer.

Public surface re-exported here so callers don't reach into submodules:

    from app.db import create_engine, make_sessionmaker, initialize_schema

Internals (`models`, `types`) are submodule-private; repositories
under `app/repositories/` consume them and expose Pydantic DTOs.
"""

from app.db.engine import PRAGMAS, create_engine
from app.db.schema_init import (
    SCHEMA_VERSION,
    InitResult,
    SchemaVersionMismatchError,
    initialize_schema,
)
from app.db.session import make_sessionmaker

__all__ = [
    "PRAGMAS",
    "SCHEMA_VERSION",
    "InitResult",
    "SchemaVersionMismatchError",
    "create_engine",
    "initialize_schema",
    "make_sessionmaker",
]
