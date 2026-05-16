"""Typed Pydantic-DTO facade over the SQLAlchemy ORM.

Every repository takes an `AsyncSession` in its constructor and returns
frozen Pydantic DTOs (never raw ORM rows) so that:

- Callers see immutable values; accidental mutation can't propagate.
- The ORM layer is replaceable without touching call sites.
- Test isolation is trivial — DTOs compare by value.

Public surface:
    UsersRepository, UserDTO
    HttpSessionsRepository, HttpSessionDTO
    ApiTokensRepository, ApiTokenDTO
    TargetsRepository, TargetDTO
    CredentialsRepository, CredentialDTO
    SettingsRepository, SettingsDTO
    AuditLogRepository, AuditLogEntryDTO
    SessionsHistoryRepository, RunSessionDTO
"""

from app.repositories.api_tokens import ApiTokenDTO, ApiTokensRepository
from app.repositories.audit_log import AuditLogEntryDTO, AuditLogRepository
from app.repositories.credentials import CredentialDTO, CredentialsRepository
from app.repositories.sessions import HttpSessionDTO, HttpSessionsRepository
from app.repositories.sessions_history import RunSessionDTO, SessionsHistoryRepository
from app.repositories.settings_repo import SettingsDTO, SettingsRepository
from app.repositories.targets import TargetDTO, TargetsRepository
from app.repositories.users import UserDTO, UsersRepository

__all__ = [
    "ApiTokenDTO",
    "ApiTokensRepository",
    "AuditLogEntryDTO",
    "AuditLogRepository",
    "CredentialDTO",
    "CredentialsRepository",
    "HttpSessionDTO",
    "HttpSessionsRepository",
    "RunSessionDTO",
    "SessionsHistoryRepository",
    "SettingsDTO",
    "SettingsRepository",
    "TargetDTO",
    "TargetsRepository",
    "UserDTO",
    "UsersRepository",
]
