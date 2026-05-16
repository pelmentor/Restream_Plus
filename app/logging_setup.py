"""Structured logging + redaction.

Two responsibilities:

1. Configure `structlog` for the whole process (JSON in containers,
   colored console for `RESTREAM_LOG_FORMAT=console` dev runs).

2. Defense-in-depth credential redaction. Stream keys must never
   appear in logs (ADR-0006 §"Log redaction invariant"). The
   primary defense is "don't log credentials at all" — code review
   enforces this. This module is the second line: a structlog
   processor that scans every event_dict value for any currently-
   registered secret and replaces matches with `***REDACTED***`
   before serialization.

The `CredentialRegistry` is process-global so the supervisor can
register a stream key the moment it decrypts one (before spawning
a worker) and unregister when the worker exits. Registration must
happen *before* any code path that might log around the credential
runs.

Why scan values instead of trusting field names: field names are
within the developer's control and would be sufficient if every
log site was correctly classified. Substring scanning catches the
case a developer hasn't considered (e.g., a stack trace that
embeds a URL with the key in it). False positives are bounded by
`MIN_REDACTABLE_SECRET_LENGTH` — we don't redact short strings
that would cause too much collateral damage.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable, MutableMapping
from typing import Any

import structlog
from structlog.types import Processor

from app.config import AppSettings

RedactionProcessor = Callable[
    [Any, str, MutableMapping[str, Any]],
    MutableMapping[str, Any],
]
"""Narrower type than `structlog.types.Processor`.

`Processor` permits `str`/`bytes`/`bytearray`/`tuple` returns for terminal
renderers; our redaction step always returns the event dict so it can be
chained. Returning the narrower type means callers (and tests) get
`MutableMapping[str, Any]` and can index into it without casts. The processor
is still assignable to `Processor` (returning a narrower type is fine — it's
covariant in the return position).
"""

MIN_REDACTABLE_SECRET_LENGTH = 12
"""Floor on secret length accepted by the registry.

Below this, substring substitution risks redacting innocuous content
(words appearing inside log messages). Stream keys and API tokens are
well above this floor — the smallest legitimate credential in our
system is 40 url-safe characters. We assert the floor in `register()`
so a future change that introduces a shorter credential gets a clear
error rather than silently degraded redaction.
"""

REDACTION_PLACEHOLDER = "***REDACTED***"


class CredentialRegistry:
    """Thread-safe set of plaintext credentials that must never appear in logs.

    Lifecycle:
      register(secret)    — supervisor calls just before spawning a worker.
      unregister(secret)  — supervisor calls when the worker exits.

    The structlog processor reads `snapshot()` on every event. A `frozenset`
    return value means the processor cannot mutate the registry by accident,
    and the snapshot is immutable for the duration of a single processor call
    even if another thread registers concurrently.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._secrets: set[str] = set()

    def register(self, secret: str) -> None:
        if not isinstance(secret, str):
            raise TypeError("secret must be a string")
        if len(secret) < MIN_REDACTABLE_SECRET_LENGTH:
            raise ValueError(
                f"secret must be at least {MIN_REDACTABLE_SECRET_LENGTH} characters "
                f"to register for redaction; got {len(secret)}"
            )
        with self._lock:
            self._secrets.add(secret)

    def unregister(self, secret: str) -> None:
        with self._lock:
            self._secrets.discard(secret)

    def clear(self) -> None:
        with self._lock:
            self._secrets.clear()

    def snapshot(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._secrets)


_global_registry = CredentialRegistry()


def get_credential_registry() -> CredentialRegistry:
    """Return the process-wide registry.

    A single global instance is the right call for this codebase: the
    structlog processor chain is configured once at startup, and the
    supervisor is a single object. Tests that need isolation use
    `clear()` in a fixture rather than constructing their own registry.
    """
    return _global_registry


def make_redaction_processor(registry: CredentialRegistry) -> RedactionProcessor:
    """Build a structlog processor that strips registered secrets from values.

    The processor walks event_dict, replacing any string value whose substring
    matches a registered secret. Nested dicts and sequences are walked
    recursively so a structured payload (e.g., `extra={"url": "...?key=..."}`)
    is also covered.

    The processor never raises. A bug here cannot be allowed to crash the
    process — that would itself be a logging failure. On unexpected types we
    leave the value as-is (it will be serialized by a downstream renderer that
    has its own type handling).
    """

    def processor(
        _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        secrets = registry.snapshot()
        if not secrets:
            return event_dict
        for key, value in list(event_dict.items()):
            event_dict[key] = _redact_value(value, secrets)
        return event_dict

    return processor


def _redact_value(value: Any, secrets: frozenset[str]) -> Any:
    if isinstance(value, str):
        return _redact_string(value, secrets)
    if isinstance(value, bytes):
        # bytes shouldn't carry stream keys, but if they do, decoding-without-
        # error is the best we can do without obscuring the value entirely.
        try:
            decoded = value.decode("utf-8")
        except UnicodeDecodeError:
            return value
        redacted = _redact_string(decoded, secrets)
        if redacted == decoded:
            return value
        return redacted.encode("utf-8")
    if isinstance(value, dict):
        return {k: _redact_value(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v, secrets) for v in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(v, secrets) for v in value)
    return value


def _redact_string(value: str, secrets: frozenset[str]) -> str:
    """Replace each registered secret in `value` with the redaction placeholder.

    Secrets are processed longest-first. Without this, if one registered secret
    is a substring of another (e.g., a short token that happens to be a prefix
    of a long stream key), the short one is redacted first and breaks up the
    longer one so it no longer matches as a substring — leaking the tail. By
    handling the longest match first, we redact the most specific credential
    and any nested substrings collapse into the same placeholder.
    """
    if not secrets:
        return value
    out = value
    for secret in sorted(secrets, key=len, reverse=True):
        if secret in out:
            out = out.replace(secret, REDACTION_PLACEHOLDER)
    return out


def configure_logging(settings: AppSettings) -> None:
    """Initialize structlog and the stdlib logging bridge.

    Safe to call multiple times with the same settings. With *different*
    settings, structlog's `cache_logger_on_first_use=True` means that any
    logger acquired before this call will keep its previously-bound
    processor chain — only loggers obtained after this call see the new
    config. Production calls this exactly once during app startup, before
    any logger is acquired, so the caveat is a test-isolation concern,
    not a production one.
    """
    log_level_int = logging.getLevelName(settings.log_level)
    if not isinstance(log_level_int, int):  # defensive — Literal already constrains
        log_level_int = logging.INFO

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=log_level_int,
        force=True,
    )

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        make_redaction_processor(_global_registry),
    ]

    renderer: Processor
    if settings.log_format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level_int),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )
