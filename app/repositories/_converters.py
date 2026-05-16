"""Adapters between repository DTOs and the domain layer.

This is the *only* module that imports from both `app.repositories` and
`app.domain`. The domain layer is forbidden from reaching down here
(see `app/domain/__init__.py` and `tests/domain/test_no_outward_imports.py`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.target import Target
from app.domain.target_types import TargetType

if TYPE_CHECKING:
    from app.repositories.credentials import CredentialDTO
    from app.repositories.targets import TargetDTO


def target_dto_to_domain(
    target: TargetDTO,
    credential: CredentialDTO | None = None,
) -> Target:
    """Build a `Target` aggregate from persistence rows.

    `credential` is the active credential row for this target (or None).
    The aggregate stores only the fact of presence — never the secret.
    `backup_enabled` is read out of the JSON `settings` blob; absent or
    non-bool values default to False.
    """
    backup = bool(target.settings.get("backup_enabled", False))
    return Target(
        id=target.id,
        type=TargetType(target.type),
        label=target.label,
        url=target.url,
        enabled=target.enabled,
        backup_enabled=backup,
        has_active_credential=credential is not None,
    )
