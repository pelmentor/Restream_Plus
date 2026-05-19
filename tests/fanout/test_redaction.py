"""RedactionSink — property-based + line-discipline tests.

The contract: for ANY input byte stream containing the worker's
configured URL or any RTMP-shaped key, the corresponding key segment
MUST NOT appear in the sink output, byte-for-byte. The placeholder
`***REDACTED***` MUST appear in its place.

The property test uses 1000 examples of random key + random junk
chunked at random boundaries — a regression that breaks redaction
even for one chunking scheme will be caught.
"""

from __future__ import annotations

import string

import pytest
from app.fanout.redaction import (
    MAX_BUF_LEN,
    MAX_LINE_LEN,
    MAX_WORKER_URL_LEN,
    REDACTION_PLACEHOLDER,
    RedactionSink,
)
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

_KEY_ALPHABET: str = string.ascii_letters + string.digits + "_-"


def _chunked(payload: bytes, chunk: int) -> list[bytes]:
    if chunk <= 0:
        chunk = 64
    return [payload[i : i + chunk] for i in range(0, len(payload), chunk)]


def _feed_in_chunks(sink: RedactionSink, payload: bytes, chunk: int) -> bytes:
    out: list[bytes] = []
    for c in _chunked(payload, chunk):
        out.extend(sink.feed(c))
    out.extend(sink.flush())
    return b"\n".join(out)


class TestConstructor:
    def test_rejects_empty_url(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            RedactionSink(worker_url="")

    def test_rejects_non_string_url(self) -> None:
        with pytest.raises(TypeError, match="must be a string"):
            RedactionSink(worker_url=b"rtmp://x/y/abcdef")  # type: ignore[arg-type]


class TestFeedTypes:
    def test_rejects_non_bytes(self) -> None:
        sink = RedactionSink(worker_url="rtmp://host/app/abcdefghij")
        with pytest.raises(TypeError, match="must be bytes"):
            sink.feed("a string")  # type: ignore[arg-type]


class TestLiteralRedaction:
    def test_full_url_in_one_chunk(self) -> None:
        url = "rtmp://live.example/app/SuperSecretKey1234"
        sink = RedactionSink(worker_url=url)
        out = b"".join(sink.feed(f"ffmpeg push {url}\n".encode()))
        assert b"SuperSecretKey1234" not in out
        assert REDACTION_PLACEHOLDER in out

    def test_url_split_across_two_chunks(self) -> None:
        url = "rtmp://live.example/app/SuperSecretKey1234"
        sink = RedactionSink(worker_url=url)
        # Split across literal chunks but within one line — the line
        # buffer accumulates the partial URL and redacts on emit.
        first_half, second_half = url[:20], url[20:]
        out: list[bytes] = []
        out.extend(sink.feed(first_half.encode()))
        out.extend(sink.feed(second_half.encode()))
        out.extend(sink.feed(b"\n"))
        joined = b"".join(out)
        assert b"SuperSecretKey1234" not in joined

    def test_multiple_lines_in_one_chunk(self) -> None:
        url = "rtmp://live.example/app/abcdefghij"
        sink = RedactionSink(worker_url=url)
        payload = (f"line one with {url}\n" f"line two with {url}\n" f"line three plain\n").encode()
        out = sink.feed(payload)
        assert len(out) == 3
        assert b"abcdefghij" not in b"\n".join(out)
        assert REDACTION_PLACEHOLDER in out[0]
        assert REDACTION_PLACEHOLDER in out[1]
        assert out[2] == b"line three plain"


class TestRegexFallback:
    def test_redacts_unknown_url_with_rtmp_shape(self) -> None:
        # Sink configured for one URL; stderr emits a different RTMP
        # shape (e.g., proxy rewrite). The regex catches it.
        sink = RedactionSink(worker_url="rtmp://known.example/app/AAAAAAAA")
        out = b"".join(sink.feed(b"failover push rtmps://other.example/live/SecretKeyZZZ123\n"))
        assert b"SecretKeyZZZ123" not in out
        assert REDACTION_PLACEHOLDER in out

    def test_regex_preserves_path_prefix(self) -> None:
        sink = RedactionSink(worker_url="rtmp://x.example/app/AAAAAAAA")
        out = b"".join(sink.feed(b"err pushing to rtmps://other/live2/SecretKeyZZZ123\n"))
        assert b"rtmps://other/live2/" in out
        assert b"SecretKeyZZZ123" not in out

    def test_does_not_redact_short_path_segment(self) -> None:
        # 7 chars is below the 8-char regex threshold.
        sink = RedactionSink(worker_url="rtmp://x.example/app/AAAAAAAA")
        out = b"".join(sink.feed(b"err pushing to rtmp://other/live2/short7\n"))
        assert b"short7" in out


class TestOverflow:
    def test_force_split_at_cap(self) -> None:
        url = "rtmp://x.example/app/abcdefghij"
        sink = RedactionSink(worker_url=url)
        # A single line longer than MAX_LINE_LEN with the URL embedded
        # near the end. The cap forces a split; the URL should still
        # be redacted in whichever split chunk contains it. The
        # second emission flushes the carried overflow.
        payload = b"x" * (MAX_LINE_LEN - len(url) - 5) + url.encode() + b"yyyyy"
        out = sink.feed(payload)
        out.extend(sink.flush())
        assert len(out) >= 1
        joined = b"".join(out)
        assert b"abcdefghij" not in joined
        assert REDACTION_PLACEHOLDER in joined

    def test_url_straddles_force_split_boundary_is_redacted(self) -> None:
        """Regression for post-Phase-5 review H3: a URL that straddles
        the MAX_LINE_LEN cap must NOT leak its second half across the
        forced split. The bridge logic in feed() emits the redacted
        URL across two chunks (placeholder may split, but the
        plaintext key MUST NOT appear anywhere in either chunk)."""
        url = "rtmp://x.example/app/SecretKeyZZZ12345"
        sink = RedactionSink(worker_url=url)
        # Position the URL so half is before the cap and half after.
        prefix = b"x" * (MAX_LINE_LEN - 10)
        suffix = b"y" * 200
        payload = prefix + url.encode() + suffix
        out = sink.feed(payload)
        out.extend(sink.flush())
        joined = b"".join(out)
        # The plaintext key must not appear ANYWHERE in the emitted bytes.
        assert b"SecretKeyZZZ12345" not in joined
        # The placeholder is reconstructed across the chunk boundary.
        assert REDACTION_PLACEHOLDER in joined

    def test_buffered_no_emit_until_newline_or_cap(self) -> None:
        sink = RedactionSink(worker_url="rtmp://x.example/app/abcdefghij")
        out = sink.feed(b"partial line, no newline yet")
        assert out == []


class TestFlush:
    def test_flush_emits_partial_line(self) -> None:
        url = "rtmp://x.example/app/SuperKeyXYZ12345"
        sink = RedactionSink(worker_url=url)
        sink.feed(f"trailing fragment {url}".encode())
        out = sink.flush()
        assert len(out) == 1
        assert b"SuperKeyXYZ12345" not in out[0]
        assert REDACTION_PLACEHOLDER in out[0]

    def test_flush_no_data_returns_empty(self) -> None:
        sink = RedactionSink(worker_url="rtmp://x.example/app/abcdefghij")
        assert sink.flush() == []


class TestProperty:
    @settings(max_examples=1000, suppress_health_check=[HealthCheck.too_slow])
    @given(
        key=st.text(alphabet=_KEY_ALPHABET, min_size=12, max_size=80),
        junk_before=st.binary(min_size=0, max_size=512),
        junk_after=st.binary(min_size=0, max_size=512),
        chunk_size=st.integers(min_value=1, max_value=128),
    )
    def test_registered_key_never_appears_in_output(
        self,
        key: str,
        junk_before: bytes,
        junk_after: bytes,
        chunk_size: int,
    ) -> None:
        # Avoid pathological keys that include newlines (we use them
        # to terminate lines).
        assume(b"\n" not in key.encode())
        # Keep junk free of accidental newlines that would split the URL.
        junk_before = junk_before.replace(b"\n", b"x")
        junk_after = junk_after.replace(b"\n", b"x")

        url = f"rtmp://platform.example/app/{key}"
        sink = RedactionSink(worker_url=url)
        payload = junk_before + b"err: " + url.encode() + b" failed\n" + junk_after
        out = _feed_in_chunks(sink, payload, chunk_size)
        assert key.encode() not in out
        # Placeholder must appear at least once for the line that held the URL.
        assert REDACTION_PLACEHOLDER in out

    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    @given(
        key=st.text(alphabet=_KEY_ALPHABET, min_size=12, max_size=80),
        host=st.from_regex(r"[a-z]{3,16}\.example", fullmatch=True),
        chunk_size=st.integers(min_value=1, max_value=128),
    )
    def test_unknown_url_with_known_shape_redacted_by_regex(
        self,
        key: str,
        host: str,
        chunk_size: int,
    ) -> None:
        # Sink configured for a different URL; regex must still catch
        # the rtmp(s)://host/app/<>=8-char shape.
        unrelated_url = "rtmp://other.example/app/" + ("z" * 16)
        sink = RedactionSink(worker_url=unrelated_url)
        rtmp_url = f"rtmps://{host}/live2/{key}"
        payload = b"err pushing to " + rtmp_url.encode() + b"\n"
        out = _feed_in_chunks(sink, payload, chunk_size)
        assert key.encode() not in out
        assert REDACTION_PLACEHOLDER in out

    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    @given(
        key=st.text(alphabet=_KEY_ALPHABET, min_size=12, max_size=80),
        url_offset=st.integers(min_value=0, max_value=MAX_LINE_LEN + 200),
        suffix_len=st.integers(min_value=0, max_value=512),
    )
    def test_force_split_property_no_leak_at_any_offset(
        self,
        key: str,
        url_offset: int,
        suffix_len: int,
    ) -> None:
        """Post-Phase-5 review M1 fix: extend the property test to cover
        the force-split path. URL placed at random positions in a
        payload that exceeds the cap; key must never appear in output."""
        url = f"rtmp://platform.example/app/{key}"
        sink = RedactionSink(worker_url=url)
        prefix = b"x" * url_offset
        suffix = b"y" * suffix_len
        payload = prefix + url.encode() + suffix
        # Feed in two halves to exercise both `feed()` boundary cases
        # AND the force-split path (no newline anywhere in payload).
        midpoint = len(payload) // 2
        out = b"".join(sink.feed(payload[:midpoint]))
        out += b"".join(sink.feed(payload[midpoint:]))
        out += b"".join(sink.flush())
        assert key.encode() not in out


# --- Slice 7 / Hex Audit BA-F8 + FG2-M4 ----------------------------------------


class TestQueryStringRedaction:
    """The path-only regex misses keys surfaced as query-string params
    (`?streamKey=...` / `?auth_key=...`). The query regex closes that
    bypass (Hex Audit BA-F8, slice 7)."""

    def test_question_mark_key_param(self) -> None:
        sink = RedactionSink(worker_url="rtmp://example/app/dummy12345")
        out = b"".join(sink.feed(b"ffmpeg https://x?streamKey=AbCdEfGh12345 done\n"))
        assert b"AbCdEfGh12345" not in out
        assert REDACTION_PLACEHOLDER in out

    def test_ampersand_key_param(self) -> None:
        sink = RedactionSink(worker_url="rtmp://example/app/dummy12345")
        out = b"".join(sink.feed(b"url https://x?other=v&auth_key=SecretSecret123 ok\n"))
        assert b"SecretSecret123" not in out

    def test_capitalised_key_param(self) -> None:
        """The query regex is case-insensitive on the param name."""
        sink = RedactionSink(worker_url="rtmp://example/app/dummy12345")
        out = b"".join(sink.feed(b"x?STREAMKEY=PrivateKey123456 plus tail\n"))
        assert b"PrivateKey123456" not in out

    def test_short_value_not_redacted(self) -> None:
        """The 8-char minimum on the value avoids redacting innocuous
        short parameters."""
        sink = RedactionSink(worker_url="rtmp://example/app/dummy12345")
        # `key=short` is below the 8-char value threshold.
        out = b"".join(sink.feed(b"?streamKey=short tail\n"))
        assert b"short" in out

    def test_param_name_without_key_not_redacted(self) -> None:
        """`?expires=12345678` looks like a session-expiry timestamp,
        not a credential — the param name must literally contain
        `key` (case-insensitive)."""
        sink = RedactionSink(worker_url="rtmp://example/app/dummy12345")
        out = b"".join(sink.feed(b"?expires=12345678 tail\n"))
        assert b"12345678" in out


class TestPathBoundaryTightening:
    """Path regex now stops host/app at `?` and `#` so a trailing query
    string can't extend the "app" capture past the URL boundary."""

    def test_path_does_not_swallow_query_string(self) -> None:
        sink = RedactionSink(worker_url="rtmp://example/app/dummy12345")
        # The legitimate path key is `LiveKey1234567` (>=8 chars). The
        # `?streamKey=...` is a separate param. Both should redact
        # cleanly via their respective passes.
        out = b"".join(sink.feed(b"url rtmp://h/app/LiveKey1234567?streamKey=OtherSecret9999 ok\n"))
        assert b"LiveKey1234567" not in out
        assert b"OtherSecret9999" not in out


class TestWorkerUrlCap:
    """FG2-M4: refuse an oversized `worker_url` at construction time so
    the force-split lookahead can't be inflated."""

    def test_max_length_url_accepted(self) -> None:
        # Exactly at the cap is OK.
        url = "rtmp://h/" + "a" * (MAX_WORKER_URL_LEN - len("rtmp://h/"))
        assert len(url) == MAX_WORKER_URL_LEN
        sink = RedactionSink(worker_url=url)
        assert sink is not None

    def test_one_byte_over_cap_rejected(self) -> None:
        oversize = "rtmp://h/" + "a" * (MAX_WORKER_URL_LEN - len("rtmp://h/") + 1)
        assert len(oversize) == MAX_WORKER_URL_LEN + 1
        with pytest.raises(ValueError, match="worker_url must be at most"):
            RedactionSink(worker_url=oversize)


class TestBufCap:
    """FG2-M4 second cap: a runaway producer emitting no newlines must
    still be bounded. The buffer can never grow past MAX_BUF_LEN before
    force-split fires."""

    def test_no_newline_runaway_is_bounded(self) -> None:
        sink = RedactionSink(worker_url="rtmp://host/app/dummykey12345")
        # Feed 10x MAX_BUF_LEN bytes with no newline. The sink must
        # emit chunks instead of growing _buf without bound.
        payload = b"x" * (MAX_BUF_LEN * 10)
        out_chunks: list[bytes] = []
        out_chunks.extend(sink.feed(payload))
        # At least one forced emission should have happened.
        assert len(out_chunks) > 0
        # The cumulative `_buf` size is bounded by MAX_LINE_LEN +
        # url_len after each force-split; the URL is short so the
        # ceiling is well under MAX_BUF_LEN * 2.
        assert len(sink._buf) <= MAX_LINE_LEN + len("rtmp://host/app/dummykey12345")
        out_chunks.extend(sink.flush())
