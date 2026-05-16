"""Credential lifetime per ADR-0009.

The enum lives here so it has zero domain dependencies. The mapping
from `TargetType` to `CredentialLifetime` is held in `target_types.py`
(single source of truth) and exposed back through `lifetime_for()`,
which is re-exported here for callers that think in credential terms.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.domain.target_types import TargetType


class CredentialLifetime(str, Enum):
    PERSISTENT = "persistent"
    PER_SESSION = "per_session"
    REFRESH_TOKEN = "refresh_token"  # noqa: S105 — enum value, not a secret


def lifetime_for(target_type: TargetType) -> CredentialLifetime:
    """Return the credential lifetime that governs a target type.

    Reads from `TARGET_TYPE_SPECS` to avoid duplicating the mapping.
    Imported lazily inside the function to break the credential_policy
    ↔ target_types cycle while keeping a flat public API.
    """
    from app.domain.target_types import TARGET_TYPE_SPECS

    return TARGET_TYPE_SPECS[target_type].credential_lifetime
