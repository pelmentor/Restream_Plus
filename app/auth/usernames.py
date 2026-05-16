# ruff: noqa: RUF002 — Unicode lookalikes in the docstring are the
# subject matter of the module (NFKC + casefold examples). Suppress
# the "ambiguous character" lint file-wide.
"""Canonical username normalization.

Per the Phase 3 security review (sec finding #3): `str.lower()` only
ASCII-lowercases, so a locale-aware attacker can submit usernames
like `ADMİN` (Turkish dotted-I) and `admın` (dotless-i) as
*different* bucket keys for the rate limiter, and as
*different* lookups against the users table. We close this with:

  1. `unicodedata.normalize("NFKC", value)`  — folds compatibility
     forms (e.g., fullwidth ＡＤＭＩＮ → ADMIN).
  2. `.casefold()`                           — Unicode-aware
     case-folding (handles ß → ss, İ → i with combining dot, etc.).
  3. `.strip()`                              — trims surrounding
     whitespace so `"admin "` and `"admin"` are equal.

ADR-0005 commits to single-admin, but normalization is still
required because the attacker controls the *submitted* username
even when only one row exists in the `users` table. Without
normalization, the rate-limiter buckets and the DB lookup disagree
about what counts as "the same username".

Used by `app.auth.sessions.SessionAuthService.login()` and
`app.auth.rate_limit.LoginRateLimiter` so both speak the same key.
"""

from __future__ import annotations

import unicodedata


def normalize_username(value: str) -> str:
    """Return the canonical form used for DB lookup and rate-limit keying.

    Empty / whitespace-only inputs return an empty string — the caller
    decides how to treat that (typically as a failed login, since no
    row will match).
    """
    if not isinstance(value, str):
        raise TypeError("username must be a string")
    return unicodedata.normalize("NFKC", value).casefold().strip()


__all__ = ["normalize_username"]
