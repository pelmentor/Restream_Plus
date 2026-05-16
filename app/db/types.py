"""Custom SQLAlchemy column types for the persistence layer.

`UTCEpochSeconds` bridges the on-disk INTEGER seconds representation
(per `app/db/schema.sql`) to Python `datetime` objects that are always
timezone-aware (UTC). Naive datetimes are rejected at bind-time so an
accidental `datetime.utcnow()` (which returns a naive value) cannot
silently land in the database.

Bytes columns use SQLAlchemy's built-in `LargeBinary` directly; there's
no decorator needed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Dialect, Integer
from sqlalchemy.types import TypeDecorator


class UTCEpochSeconds(TypeDecorator[datetime]):
    """Map a `datetime` (timezone-aware UTC) to/from INTEGER epoch seconds.

    Why INTEGER seconds:
    - Schema-level clarity: the column type in `schema.sql` is INTEGER,
      which works with STRICT tables and is unambiguous in raw SQL.
    - Sub-second precision is not load-bearing for this app; audit_log
      ordering is owned by the AUTOINCREMENT id, not the timestamp.

    Why reject naive datetimes:
    - Mixing naive and aware values is a silent-bug factory. The KDF
      and AEAD layers already require aware datetimes; the DB layer
      enforces the same contract end-to-end.
    """

    impl = Integer
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> int | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(
                "naive datetime is not allowed in DB columns; "
                "construct as datetime.now(tz=UTC) or similar"
            )
        return int(value.astimezone(UTC).timestamp())

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return datetime.fromtimestamp(int(value), tz=UTC)
