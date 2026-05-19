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
    *,
    youtube_backup_enabled: bool = False,
) -> Target:
    """Build a `Target` aggregate from persistence rows.

    `credential` is the active credential row for this target (or None).
    The aggregate stores only the fact of presence — never the secret.
    `backup_enabled` is read out of the JSON `settings` blob; absent or
    non-bool values default to False.

    Hex Audit SA-F14 (slice 10): the per-target `backup_enabled` flag
    is overridden to False unless `youtube_backup_enabled` (the
    `RESTREAM_YOUTUBE_BACKUP_ENABLED` config knob) is True. This
    closes the half-baked-feature gap — the per-target JSON flag was
    settable only via direct DB edit (no UI form), so the feature
    was unreachable in practice but `_roles_for` would still spawn
    a second worker if the row happened to carry the flag. The
    config-level gate makes the half-baked status explicit:
    operators must opt in twice (DB row + env var) before the
    primary+backup topology activates, and a future "enable backup"
    UI ships alongside flipping the default here.

    The supervisor is the production caller and passes its own
    config-derived value; tests that don't care about the gate get
    the safe default (False, single-worker topology).
    """
    raw_backup = bool(target.settings.get("backup_enabled", False))
    backup = raw_backup and youtube_backup_enabled
    return Target(
        id=target.id,
        type=TargetType(target.type),
        label=target.label,
        url=target.url,
        enabled=target.enabled,
        backup_enabled=backup,
        has_active_credential=credential is not None,
    )
