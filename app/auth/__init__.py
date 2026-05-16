"""Backend auth — sessions, API tokens, rate limiting, reprompts.

Phase 3 per `docs/CODE_PLAN.md`. The shape of this package was
established by the Backend Architect agent ideation that preceded
the coding (Rule No.3); the load-bearing decision is `KeyMaterial`,
the process-wide service that owns the derived encryption keys and
defines when authenticated requests can succeed.

Module roles:

- `key_material`  — `locked / unlocking / ready` state machine. Holds
                    the three HKDF-derived subkeys (stream-key KEK,
                    api-token MAC, session-token MAC) in memory after
                    successful unlock. Every other auth path consumes
                    `DerivedKeys` from here.
- `sessions`      — Cookie issue / verify / revoke. Cookie value is
                    opaque random; the DB row is keyed by the HMAC
                    fingerprint per ADR-0005 §"Session validation".
- `api_tokens`    — Bearer-token issue / verify / revoke, also via
                    HMAC fingerprint, per ADR-0005 §"API tokens".
- `rate_limit`    — Sliding-window login-attempt limiter with the
                    paired-bucket (per-IP + per-username) policy and
                    the trusted-proxy IP extraction described in
                    ADR-0005 §"Boot-time KEK availability and
                    locked-mode endpoint surface".
- `reprompts`     — Short-lived (60 s) scope-bound grant tokens for
                    sensitive actions, per ADR-0005.
- `deps`          — FastAPI dependency helpers. The translation layer
                    between this package's plain exceptions and the
                    HTTP status codes the API surface speaks.
"""

from app.auth.key_material import (
    DerivedKeys,
    KeyMaterial,
    KeyMaterialState,
    PassphraseMismatchError,
    ServiceLockedError,
    load_or_create_kdf_salt,
)

__all__ = [
    "DerivedKeys",
    "KeyMaterial",
    "KeyMaterialState",
    "PassphraseMismatchError",
    "ServiceLockedError",
    "load_or_create_kdf_salt",
]
