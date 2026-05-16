"""Pydantic v2 request/response models for the HTTP surface.

Centralised so OpenAPI sees one source per shape (per Phase 6 design
memo §Q15) and the frontend's generated types (Phase 7+) don't
fragment. Endpoints stay thin — request validation lives here,
business logic in services.

Naming convention:
    `Foo`        — response body (read shape)
    `FooRequest` — request body (write shape)
    `FooResponse` — response body for a one-off operation that doesn't
                    correspond to a stable resource (e.g., login)

Frozen=True everywhere; immutability is correct for both directions
and removes a whole class of "I mutated it after the handler returned"
bugs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr

from app.domain.run_state import RunState
from app.domain.target import TargetUiState
from app.domain.target_types import TargetType
from app.domain.worker_state import WorkerRole, WorkerState

# ----------------------------------------------------------------------
# Auth
# ----------------------------------------------------------------------


class LoginRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    username: Annotated[str, Field(min_length=1, max_length=128)]
    password: Annotated[str, Field(min_length=1, max_length=1024)]


class UserSummary(BaseModel):
    """Minimal user view returned to the client. No password material."""

    model_config = ConfigDict(frozen=True)
    id: str
    username: str
    last_login_at: AwareDatetime | None


class LoginResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    user: UserSummary


class UnlockRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    passphrase: Annotated[str, Field(min_length=1, max_length=4096)]


class UnlockResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    state: Literal["ready"] = "ready"


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    current_password: Annotated[str, Field(min_length=1, max_length=1024)]
    new_password: Annotated[str, Field(min_length=12, max_length=1024)]


# ----------------------------------------------------------------------
# Targets
# ----------------------------------------------------------------------


class Target(BaseModel):
    """Public view of a configured target. Mirrors `TargetDTO` minus
    the raw settings_json string — the form-field decoding happens at
    the persistence boundary already."""

    model_config = ConfigDict(frozen=True)
    id: str
    type: TargetType
    label: str
    url: str
    enabled: bool
    settings: dict[str, Any]
    has_credential: bool
    credential_last4: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class TargetCreateRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: TargetType
    label: Annotated[str, Field(min_length=1, max_length=128)]
    url: Annotated[str, Field(min_length=1, max_length=2048)]
    enabled: bool = True
    settings: dict[str, Any] = Field(default_factory=dict)


class TargetUpdateRequest(BaseModel):
    """PATCH body. All fields optional; only supplied ones are written."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    label: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    url: Annotated[str, Field(min_length=1, max_length=2048)] | None = None
    enabled: bool | None = None
    settings: dict[str, Any] | None = None


class CredentialSetRequest(BaseModel):
    """Set or replace the stream key for a target.

    The plaintext appears only on this request body — never in any
    response, never in any GET. AES-256-GCM encryption happens inside
    the handler under the unlocked KEK.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    stream_key: Annotated[str, Field(min_length=1, max_length=4096)]


class CredentialSummary(BaseModel):
    """Read-side credential metadata. `last4` is the only character
    data that leaves the server — enough to render the masked field
    (`●●●● 7F2A`) without leaking the key.
    """

    model_config = ConfigDict(frozen=True)
    last4: str
    lifetime: Literal["persistent", "per_session", "refresh_token"]
    created_at: AwareDatetime
    expires_at: AwareDatetime | None


# ----------------------------------------------------------------------
# Run control
# ----------------------------------------------------------------------


class WorkerSnapshotView(BaseModel):
    """JSON-friendly projection of `domain.worker_state.WorkerSnapshot`.

    Mirrors the five fields the domain emits — see
    `app/domain/worker_state.py::WorkerSnapshot`. `last_error` arrives
    already redacted by Phase 5's `RedactionSink`, so it's safe to
    forward over the wire.
    """

    model_config = ConfigDict(frozen=True)
    role: WorkerRole
    state: WorkerState
    last_event_at: AwareDatetime
    last_error: str | None
    breaker_failures_in_window: int


class TargetSnapshot(BaseModel):
    """A full target view including per-role worker snapshots.

    Used as part of the WS `state.full` payload on connect, and as the
    JSON body of `GET /api/run/state`.
    """

    model_config = ConfigDict(frozen=True)
    target_id: str
    ui_state: TargetUiState
    snapshots_by_role: tuple[WorkerSnapshotView, ...]


class RunStateView(BaseModel):
    """Snapshot of the system run state + per-target status.

    `run_state_changed_at` is the wall-clock moment the supervisor last
    transitioned `run_state`. None when the supervisor has never run
    (fresh boot, OFFLINE forever). The Phase 8 frontend uses this to
    derive the "LIVE 00:17:42" timer; without it a hard reload mid-LIVE
    would tick from zero (per phase-8-design-memo §A).
    """

    model_config = ConfigDict(frozen=True)
    run_state: RunState
    targets: tuple[TargetSnapshot, ...]
    heartbeat_age_seconds: float
    dropped_total: int
    run_state_changed_at: AwareDatetime | None


class RunActionAcceptedResponse(BaseModel):
    """202 Accepted body for /api/run/{start,stop}.

    Per Phase 6 design memo §Q11: REST returns immediately with this
    body; the client watches the WS for the `run.state.changed` event
    to see the transition complete.
    """

    model_config = ConfigDict(frozen=True)
    accepted: Literal[True] = True
    previous_state: RunState


# ----------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------


class SettingsView(BaseModel):
    """Read-side settings shape. The ingest key is masked (`last4` only)
    to keep accidental log-line leakage from exposing the full key
    over a screenshot; the rotate-ingest-key endpoint is the only
    surface that ever returns the plaintext.
    """

    model_config = ConfigDict(frozen=True)
    first_run_complete: bool
    idle_timeout_seconds: int
    log_retention_days: int
    ingest_key_last4: str
    ingest_key_has_previous: bool
    ingest_key_rotated_at: AwareDatetime | None
    ingest_key_grace_until: AwareDatetime | None
    updated_at: AwareDatetime


class SettingsUpdateRequest(BaseModel):
    """PATCH body for safe (non-key) settings updates."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    idle_timeout_seconds: Annotated[int, Field(ge=30, le=86400)] | None = None
    log_retention_days: Annotated[int, Field(ge=1, le=365)] | None = None


class IngestKeyRotatedResponse(BaseModel):
    """One-time reveal body for POST /api/settings/rotate-ingest-key.

    The plaintext lives in this response and nowhere else; the UI
    surfaces it via the OneTimeRevealBanner component (Phase 9). The
    previous key remains valid until `grace_until`.
    """

    model_config = ConfigDict(frozen=True)
    plaintext: str
    last4: str
    grace_until: AwareDatetime


class IngestKeyRevealResponse(BaseModel):
    """Body for POST /api/settings/reveal-ingest-key (Phase 9).

    The ingest key is stored plaintext in `settings.ingest_key_current`
    (nginx-rtmp's on_publish webhook must validate without the unlocked
    KEK — see phase-9-design-memo §I.2). This endpoint surfaces it
    after a fresh AuthReprompt grant. The plaintext is NOT logged or
    audited; only the `last4` lands in the audit row.
    """

    model_config = ConfigDict(frozen=True)
    plaintext: str
    last4: str


class RevealCredentialResponse(BaseModel):
    """Body for POST /api/targets/{id}/reveal-credential (Phase 9).

    AEAD-decrypts the stored stream key with the in-memory
    `stream_key_kek`. The plaintext lives in this response and the
    short-lived `revealed` SecretField state on the client — never in
    the TanStack cache and never in any log line.
    """

    model_config = ConfigDict(frozen=True)
    plaintext: str
    last4: str


class RotatePassphraseRequest(BaseModel):
    """POST /api/security/rotate-passphrase body (Phase 9).

    `old_passphrase` is the master passphrase the operator has been
    unlocked with — DIFFERENT from the admin password the AuthReprompt
    grant proves. Two knowledge proofs prevent a stolen-cookie +
    stolen-admin-password attacker from re-keying the operator out
    (see phase-9-design-memo §F.1).

    `new_passphrase` minimum length matches the bootstrap-generated
    20-char default (ADR-0010) loosely: 12 chars is the floor.

    Both fields are `SecretStr` so a Pydantic 422 (e.g., user typed an
    11-char new_passphrase) does NOT echo the submitted value back in
    the error detail — bare `str` would (reviewer H-1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    old_passphrase: Annotated[SecretStr, Field(min_length=1, max_length=4096)]
    new_passphrase: Annotated[SecretStr, Field(min_length=12, max_length=4096)]


# ----------------------------------------------------------------------
# HTTP sessions (Phase 9)
# ----------------------------------------------------------------------


class HttpSessionView(BaseModel):
    """Active-session view for GET /api/security/sessions (Phase 9).

    `id_fingerprint` is a 16-hex prefix of `sha256(token_hash)` — a
    stable client-side ID for revoke without exposing the HMAC
    fingerprint that primary-keys the row. `is_current` is server-
    computed against the request's session token_hash; bearer-auth
    callers see it as False on every row.
    """

    model_config = ConfigDict(frozen=True)
    id_fingerprint: str
    created_at: AwareDatetime
    last_seen_at: AwareDatetime
    ip: str | None
    user_agent: str | None
    is_current: bool


# ----------------------------------------------------------------------
# About (Phase 9)
# ----------------------------------------------------------------------


class AboutView(BaseModel):
    """GET /api/about response.

    `last_reboots` is the 5 most-recent `app_started` audit events,
    newest first. The current boot is one of them — emitted in the
    lifespan startup so `last_reboots[0] == started_at` modulo the
    microsecond gap between the wall-clock capture and the audit
    write.
    """

    model_config = ConfigDict(frozen=True)
    version: str
    build_sha: str
    started_at: AwareDatetime
    uptime_seconds: float
    last_reboots: tuple[AwareDatetime, ...]


# ----------------------------------------------------------------------
# API tokens
# ----------------------------------------------------------------------


class ApiTokenView(BaseModel):
    """Metadata-only token view — `token_hash` never leaves the DB."""

    model_config = ConfigDict(frozen=True)
    id: str
    label: str
    created_at: AwareDatetime
    last_used_at: AwareDatetime | None
    revoked_at: AwareDatetime | None


class ApiTokenCreateRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    label: Annotated[str, Field(min_length=1, max_length=128)]


class ApiTokenCreatedResponse(BaseModel):
    """One-time reveal body for POST /api/security/tokens."""

    model_config = ConfigDict(frozen=True)
    plaintext: str
    token: ApiTokenView


# ----------------------------------------------------------------------
# Sessions history
# ----------------------------------------------------------------------


class RunHistoryItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    started_at: AwareDatetime
    ended_at: AwareDatetime | None
    end_reason: str | None
    duration_seconds: float | None


class RunHistoryPage(BaseModel):
    model_config = ConfigDict(frozen=True)
    items: tuple[RunHistoryItem, ...]
    next_before: AwareDatetime | None


# ----------------------------------------------------------------------
# Reprompt grants
# ----------------------------------------------------------------------


class RepromptIssueRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    password: Annotated[str, Field(min_length=1, max_length=1024)]
    scope: Annotated[str, Field(min_length=1, max_length=64)]


class RepromptGrantResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    grant_id: str
    expires_at: AwareDatetime


# ----------------------------------------------------------------------
# Health
# ----------------------------------------------------------------------


class CheckResult(BaseModel):
    """One check inside the /readyz / /healthz body."""

    model_config = ConfigDict(frozen=True)
    ok: bool
    detail: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    status: Literal["ready", "not_ready", "alive"]
    checks: dict[str, CheckResult] = Field(default_factory=dict)


# ----------------------------------------------------------------------
# WebSocket envelope
# ----------------------------------------------------------------------


WS_PROTOCOL_VERSION: int = 1
"""Wire-protocol version. Phase 7 frontend pins to v1; future shape
changes bump this and the frontend must handle both."""


class WsEnvelope(BaseModel):
    """The single envelope every WS message uses.

    Versioned so the frontend can refuse mismatched protocols. The
    `event` is a dotted name (`run.state.changed`, `target.snapshot`,
    `bus.drop_alert`, `state.full`) and `data` is the event-specific
    payload — typed serialisation lives in `app/api/ws.py`.
    """

    model_config = ConfigDict(frozen=True)
    v: Literal[1] = 1
    event: str
    data: dict[str, Any]


# ----------------------------------------------------------------------
# Helper: build TargetSnapshot.duration_seconds-style derived fields
# ----------------------------------------------------------------------


def compute_duration_seconds(started_at: datetime, ended_at: datetime | None) -> float | None:
    """Centralised helper so request handlers and tests agree on the
    duration calculation (UTC-only; both inputs are tz-aware per DB
    invariant)."""
    if ended_at is None:
        return None
    return (ended_at - started_at).total_seconds()


__all__ = [
    "WS_PROTOCOL_VERSION",
    "AboutView",
    "ApiTokenCreatedResponse",
    "ApiTokenCreateRequest",
    "ApiTokenView",
    "ChangePasswordRequest",
    "CheckResult",
    "CredentialSetRequest",
    "CredentialSummary",
    "HealthResponse",
    "HttpSessionView",
    "IngestKeyRevealResponse",
    "IngestKeyRotatedResponse",
    "LoginRequest",
    "LoginResponse",
    "RepromptGrantResponse",
    "RepromptIssueRequest",
    "RevealCredentialResponse",
    "RotatePassphraseRequest",
    "RunActionAcceptedResponse",
    "RunHistoryItem",
    "RunHistoryPage",
    "RunStateView",
    "SettingsUpdateRequest",
    "SettingsView",
    "Target",
    "TargetCreateRequest",
    "TargetSnapshot",
    "TargetUpdateRequest",
    "UnlockRequest",
    "UnlockResponse",
    "UserSummary",
    "WorkerSnapshotView",
    "WsEnvelope",
    "compute_duration_seconds",
]
