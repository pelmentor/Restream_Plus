"""Logging + redaction tests.

The credential registry + redaction processor are a security boundary
(ADR-0006 §"Log redaction invariant"). Tests assert:

  - Registered secrets do not appear in serialized log output.
  - Nested dicts / lists / tuples are walked.
  - Min-length floor is enforced on register().
  - Registry is thread-safe (no races between snapshot and mutate).
  - Property-based: 200 random credentials, none appear in the output.
"""

from __future__ import annotations

import io
import json
import logging
import secrets
import string
import threading
from collections.abc import Iterator
from typing import Any

import pytest
import structlog
from app.config import AppSettings
from app.logging_setup import (
    MIN_REDACTABLE_SECRET_LENGTH,
    REDACTION_PLACEHOLDER,
    CredentialRegistry,
    configure_logging,
    get_credential_registry,
    make_redaction_processor,
)
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st


@pytest.fixture
def registry() -> CredentialRegistry:
    """Local registry instance, isolated from the global one."""
    return CredentialRegistry()


class TestCredentialRegistry:
    def test_empty_snapshot_is_empty(self, registry: CredentialRegistry) -> None:
        assert registry.snapshot() == frozenset()

    def test_register_then_snapshot_includes_secret(self, registry: CredentialRegistry) -> None:
        secret = "long-enough-secret-value"
        registry.register(secret)
        assert secret in registry.snapshot()

    def test_unregister_removes_secret(self, registry: CredentialRegistry) -> None:
        secret = "long-enough-secret-value"
        registry.register(secret)
        registry.unregister(secret)
        assert secret not in registry.snapshot()

    def test_unregister_unknown_secret_is_noop(self, registry: CredentialRegistry) -> None:
        # Discard, not remove — safe to call on absent secrets.
        registry.unregister("never-registered-still-long-enough")

    def test_clear_empties_registry(self, registry: CredentialRegistry) -> None:
        registry.register("aaaaaaaaaaaaaaaaaaaa")
        registry.register("bbbbbbbbbbbbbbbbbbbb")
        registry.clear()
        assert registry.snapshot() == frozenset()

    def test_register_rejects_short_secret(self, registry: CredentialRegistry) -> None:
        too_short = "x" * (MIN_REDACTABLE_SECRET_LENGTH - 1)
        with pytest.raises(ValueError, match=str(MIN_REDACTABLE_SECRET_LENGTH)):
            registry.register(too_short)

    def test_register_rejects_non_string(self, registry: CredentialRegistry) -> None:
        with pytest.raises(TypeError):
            registry.register(b"x" * 20)  # type: ignore[arg-type]

    def test_snapshot_is_frozen(self, registry: CredentialRegistry) -> None:
        registry.register("long-enough-value-here")
        snapshot = registry.snapshot()
        assert isinstance(snapshot, frozenset)

    def test_snapshot_is_stable_across_concurrent_register(
        self, registry: CredentialRegistry
    ) -> None:
        """Snapshot returned to one caller is not mutated by another thread's register.

        A `frozenset` return value means even if the underlying `set` mutates after
        the snapshot is taken, the caller's view is immutable.
        """
        registry.register("aaaaaaaaaaaaaaaaaaaa")
        snap = registry.snapshot()
        registry.register("bbbbbbbbbbbbbbbbbbbb")
        assert "aaaaaaaaaaaaaaaaaaaa" in snap
        assert "bbbbbbbbbbbbbbbbbbbb" not in snap  # snapshot is from before


class TestRedactionProcessor:
    """Tests against the structlog processor directly, not the full pipeline."""

    def test_no_secrets_registered_returns_event_unchanged(
        self, registry: CredentialRegistry
    ) -> None:
        proc = make_redaction_processor(registry)
        event = {"msg": "hello world", "target": "twitch"}
        result = proc(None, "info", dict(event))
        assert result == event

    def test_replaces_registered_secret_in_string_value(self, registry: CredentialRegistry) -> None:
        secret = "live_st_1234567890abcdef"
        registry.register(secret)
        proc = make_redaction_processor(registry)
        result = proc(None, "info", {"url": f"rtmp://x/live/{secret}"})
        assert secret not in result["url"]
        assert REDACTION_PLACEHOLDER in result["url"]

    def test_walks_nested_dict(self, registry: CredentialRegistry) -> None:
        secret = "kick_secret_value_1234567"
        registry.register(secret)
        proc = make_redaction_processor(registry)
        event = {"data": {"inner": {"key": secret}}}
        result = proc(None, "info", event)
        assert secret not in json.dumps(result)

    def test_walks_list(self, registry: CredentialRegistry) -> None:
        secret = "yt_stream_1234567890abcd"
        registry.register(secret)
        proc = make_redaction_processor(registry)
        event = {"items": [secret, "other", secret]}
        result = proc(None, "info", event)
        assert secret not in json.dumps(result)

    def test_walks_tuple(self, registry: CredentialRegistry) -> None:
        secret = "vk_one_time_key_1234567890"
        registry.register(secret)
        proc = make_redaction_processor(registry)
        event: dict[str, Any] = {"items": (secret, "other")}
        result = proc(None, "info", event)
        # Tuple preservation: type stays tuple.
        items = result["items"]
        assert isinstance(items, tuple)
        assert secret not in items[0]

    def test_redacts_bytes_value(self, registry: CredentialRegistry) -> None:
        secret = "twitch_key_abcdef1234567890"
        registry.register(secret)
        proc = make_redaction_processor(registry)
        result = proc(None, "info", {"payload": secret.encode("utf-8")})
        assert secret.encode("utf-8") not in result["payload"]

    def test_non_text_value_passes_through(self, registry: CredentialRegistry) -> None:
        registry.register("xxxxxxxxxxxxxxxxxxxxxx")
        proc = make_redaction_processor(registry)
        result = proc(None, "info", {"n": 42, "flag": True, "x": None})
        assert result["n"] == 42
        assert result["flag"] is True
        assert result["x"] is None

    def test_nested_substring_secrets_are_redacted_longest_first(
        self, registry: CredentialRegistry
    ) -> None:
        """If one registered secret is a substring of another, the longer must be
        redacted first. Otherwise, redacting the shorter first breaks up the
        longer string and its tail leaks through unredacted.

        Concrete failure mode: secrets = {"prefix_AAAAAAAAAA", "prefix_AAAAAAAAAA_tail_BBBBBBBBBB"}.
        If "prefix_..." is replaced first, the longer secret no longer matches
        and "_tail_BBBBBBBBBB" survives in the output.
        """
        short_secret = "prefix_AAAAAAAAAA"
        long_secret = "prefix_AAAAAAAAAA_tail_BBBBBBBBBB"
        registry.register(short_secret)
        registry.register(long_secret)
        proc = make_redaction_processor(registry)
        result = proc(None, "info", {"msg": f"contains {long_secret} in it"})
        out = json.dumps(result)
        assert long_secret not in out
        assert short_secret not in out
        # Specifically: the unique tail of the longer secret must not leak.
        assert "_tail_BBBBBBBBBB" not in out


class TestFullPipelineIntegration:
    """End-to-end through structlog with the production processor chain."""

    @pytest.fixture
    def captured_stream(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[io.StringIO]:
        """Configure structlog to write JSON to a buffer for assertion.

        We do NOT call `configure_logging()` here — that would mutate global
        state (stdlib logging.basicConfig, the structlog processor chain) and
        leak into other tests. Instead, we build a minimal processor chain
        with the production redaction processor wired in, point the output at
        our buffer, and tear it all down via `structlog.reset_defaults()`.

        `cache_logger_on_first_use=False` is required so each test in the
        suite gets a fresh logger bound to the current processor chain — the
        production default (`True`) caches the chain on first use, which is
        a test-isolation trap when the chain mutates between tests.
        """
        buf = io.StringIO()
        registry = get_credential_registry()
        structlog.configure(
            processors=[
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso", utc=True),
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                make_redaction_processor(registry),
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(file=buf),
            cache_logger_on_first_use=False,
        )
        yield buf
        structlog.reset_defaults()
        registry.clear()

    @pytest.mark.usefixtures("fresh_credential_registry")
    def test_registered_secret_does_not_appear_in_emitted_log(
        self, captured_stream: io.StringIO
    ) -> None:
        secret = "live_test_secret_abcdef1234567890"
        get_credential_registry().register(secret)
        log = structlog.get_logger("test")
        log.info("a worker event", url=f"rtmp://x/live/{secret}", note="ok")
        output = captured_stream.getvalue()
        assert secret not in output
        assert REDACTION_PLACEHOLDER in output

    @pytest.mark.usefixtures("fresh_credential_registry")
    def test_unregistered_secret_appears_unredacted(self, captured_stream: io.StringIO) -> None:
        # Sanity-check the test rig itself: with no registered secret,
        # the field passes through. This prevents false-green tests
        # from a logging pipeline that silently drops values.
        log = structlog.get_logger("test")
        log.info("a clean event", note="some-non-credential-value")
        assert "some-non-credential-value" in captured_stream.getvalue()

    @pytest.mark.usefixtures("fresh_credential_registry")
    def test_exception_message_containing_secret_is_redacted(
        self, captured_stream: io.StringIO
    ) -> None:
        """ADR-0006 §"Log redaction invariant" — exception messages too.

        A credential embedded in an exception's args reaches the log via
        `format_exc_info`, which stringifies the traceback into the
        `"exception"` field of the event dict. The redaction processor scans
        string values, so the formatted traceback is in scope. This is the
        pipeline path where a forgotten credential in a library's error
        message could otherwise leak. Tested explicitly so the invariant is
        asserted, not assumed.
        """
        secret = "live_test_secret_abcdef1234567890"
        get_credential_registry().register(secret)
        log = structlog.get_logger("test")
        try:
            raise ValueError(f"failed to push to rtmp://host/live/{secret}")
        except ValueError:
            log.exception("worker raised")
        output = captured_stream.getvalue()
        assert secret not in output
        assert REDACTION_PLACEHOLDER in output


class TestConfigureLogging:
    """Smoke tests for the boot-time configure_logging() entry point.

    Most assertion-level testing of the redaction processor uses a hand-built
    fixture chain in `TestFullPipelineIntegration` to avoid mutating global
    structlog state. These tests do mutate global state and reset it via
    `structlog.reset_defaults()` afterward.
    """

    @pytest.fixture(autouse=True)
    def _restore_structlog(self) -> Iterator[None]:
        yield
        structlog.reset_defaults()
        get_credential_registry().clear()

    def test_paste_mode_json_format_is_accepted(self) -> None:
        settings_obj = AppSettings(passphrase_source="paste", log_format="json")
        configure_logging(settings_obj)
        # Smoke: a logger can be obtained and emit without raising.
        log = structlog.get_logger("smoke")
        log.info("hello")

    def test_console_format_is_accepted(self) -> None:
        settings_obj = AppSettings(passphrase_source="paste", log_format="console")
        configure_logging(settings_obj)
        log = structlog.get_logger("smoke")
        log.info("hello")


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    secret=st.text(
        alphabet=string.ascii_letters + string.digits + "-_",
        min_size=MIN_REDACTABLE_SECRET_LENGTH,
        max_size=80,
    )
)
def test_property_registered_secret_never_appears_in_redacted_output(
    secret: str,
) -> None:
    """For any registered secret, no string output from the processor contains it.

    Phase 1 acceptance criterion via property test: 100 random credentials in
    varied surrounding text, none must appear in the output.
    """
    registry = CredentialRegistry()
    registry.register(secret)
    proc = make_redaction_processor(registry)

    surrounding_noise = (
        f"prefix-junk-{secret}-suffix",
        f"http://host/{secret}",
        f"args=[ffmpeg, -i, {secret}]",
        secret,
    )
    event: dict[str, Any] = {
        "msg": surrounding_noise[0],
        "url": surrounding_noise[1],
        "argv": list(surrounding_noise[2:]),
        "nested": {"deep": {"path": secret}},
    }
    result = proc(None, "info", event)

    serialized = json.dumps(result, default=str)
    assert secret not in serialized


def test_thread_safety_register_concurrent_with_snapshot() -> None:
    """The lock around register/unregister means concurrent mutate doesn't tear snapshot.

    All three worker threads (1 writer + 2 readers) sync on a single Barrier so they
    start their loops at the same instant. The main thread does NOT participate in
    the barrier — it only spawns and joins. (An earlier version had the main thread
    wait() on the barrier too, which after the worker release left main waiting on a
    fresh party of 3 that never arrived — classic deadlock.)
    """
    registry = CredentialRegistry()
    barrier = threading.Barrier(3)
    errors: list[Exception] = []

    def writer() -> None:
        try:
            barrier.wait(timeout=5.0)
            for i in range(500):
                registry.register(f"secret-aaaaaaaaaa{i:04d}")
        except Exception as exc:  # pragma: no cover — safety net
            errors.append(exc)

    def reader() -> None:
        try:
            barrier.wait(timeout=5.0)
            for _ in range(500):
                snap = registry.snapshot()
                # Iterate the frozenset; must not raise even if writer is busy.
                _ = sum(1 for _ in snap)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [
        threading.Thread(target=writer),
        threading.Thread(target=reader),
        threading.Thread(target=reader),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)
        assert not t.is_alive(), "thread did not finish within 10s — likely a deadlock"
    assert errors == []


# Hint: keep `secrets` import used in case future tests need it for nonce-style data.
_ = secrets
