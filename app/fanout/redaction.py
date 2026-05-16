"""Per-Worker stderr redaction sink (ADR-0008 §"log-redaction sink").

The supervisor wraps each Worker's stderr stream in a `RedactionSink`
before any byte ever reaches a logger or the in-memory ring buffer.
Two compositional redaction passes per line:

1. **Literal**: the exact full output URL of *this* worker (which
   embeds the plaintext stream key) is replaced with the placeholder.
   Deterministic — closed-form match for the URL the supervisor
   computed and handed to ffmpeg.
2. **Regex tail**: a generic `rtmp(s)://host/app/<key>` pattern
   captures the path leading up to a >=8-char path segment and
   replaces only the segment. Defensive backstop for stderr that
   constructs URLs differently than the literal we registered (e.g.,
   a proxy rewrite, or the same key surfacing on a different host).

The sink is line-buffered. A trailing partial line carries across
`feed()` calls; `flush()` emits any remainder at EOF. A line that
exceeds `MAX_LINE_LEN` is force-split at the cap and emitted —
splitting under load is preferable to silent drop, and the redaction
contract still holds because each split chunk is run through both
redaction passes before emission.

Process-global redaction via `app.logging_setup.CredentialRegistry`
is the *second* line of defence: this sink protects the raw byte
stream; the registry protects everything that goes through structlog.
The supervisor is required to register the plaintext key in the
registry BEFORE spawning the worker — the two layers are not
redundant; they cover different attack surfaces.
"""

from __future__ import annotations

import re

MAX_LINE_LEN: int = 4096
"""Cap on a single line emitted by the sink.

A line longer than this is force-split at the boundary and emitted
across multiple chunks (each individually redacted). 4 KiB is well
above ffmpeg's natural line lengths; the cap exists to bound memory
under a runaway producer that never emits a newline.
"""

REDACTION_PLACEHOLDER: bytes = b"***REDACTED***"
"""Matches the placeholder used by `app.logging_setup` so log readers
see the same sentinel from both layers."""

_RTMP_TAIL_RE: re.Pattern[bytes] = re.compile(rb"(rtmps?://[^/\s]+/[^/\s]+/)([A-Za-z0-9_\-]{8,})")
"""Catches `rtmp(s)://host/app/<key>` shapes. Group 1 is the leading
path (preserved); group 2 is the key (replaced). The 8-char minimum
on the key avoids redacting innocuous short path segments — every
real platform key in `platforms-reference.md` is well above this."""


class RedactionSink:
    """Line-buffered byte sanitizer wrapped around one Worker's stderr.

    Construction:
      sink = RedactionSink(worker_url="rtmp://host/app/<plaintext-key>")

    Usage in Phase 5 (the FFmpegWorker stderr pump):
      while True:
          chunk = await proc.stderr.read(4096)
          if not chunk:
              for line in sink.flush():
                  emit(line)
              break
          for line in sink.feed(chunk):
              emit(line)
    """

    __slots__ = ("_buf", "_worker_url_bytes")

    def __init__(self, *, worker_url: str) -> None:
        if not isinstance(worker_url, str):
            raise TypeError("worker_url must be a string")
        if not worker_url:
            raise ValueError("worker_url must not be empty")
        self._worker_url_bytes = worker_url.encode("utf-8")
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        """Append `data`; return zero or more complete redacted lines.

        Lines do NOT carry a trailing newline. Callers re-emit the line
        through their own writer (structured logger / ring buffer) and
        own framing decisions.

        Force-split discipline (post-Phase-5 review fix): a worker URL
        could otherwise straddle the `MAX_LINE_LEN` cap and leak its
        key half across two emissions. To prevent this, when the
        buffer reaches the cap we wait for `MAX_LINE_LEN + url_len`
        bytes before splitting (so the lookahead is wide enough to
        contain a full URL match), redact the wider window in one
        shot, emit the first `MAX_LINE_LEN` bytes of the redacted
        output, and re-buffer any post-cap redacted overflow so the
        consumer never sees a half-redacted URL.
        """
        if not isinstance(data, bytes | bytearray):
            raise TypeError("data must be bytes or bytearray")
        self._buf.extend(data)
        out: list[bytes] = []
        url_len = len(self._worker_url_bytes)
        force_split_threshold = MAX_LINE_LEN + url_len
        while True:
            nl = self._buf.find(b"\n")
            if nl == -1:
                if len(self._buf) < force_split_threshold:
                    break
                # Forced split with full URL-width lookahead.
                window = bytes(self._buf[:force_split_threshold])
                redacted = self._redact_line(window)
                out.append(redacted[:MAX_LINE_LEN])
                if len(redacted) > MAX_LINE_LEN:
                    # Carry the redacted overflow back into _buf so we
                    # don't redact the same source bytes twice.
                    overflow = redacted[MAX_LINE_LEN:]
                    self._buf[:force_split_threshold] = overflow
                else:
                    del self._buf[:force_split_threshold]
            else:
                line = bytes(self._buf[:nl])
                del self._buf[: nl + 1]
                out.append(self._redact_line(line))
        return out

    def flush(self) -> list[bytes]:
        """Final-flush at EOF; emit any trailing partial line redacted."""
        if not self._buf:
            return []
        line = bytes(self._buf)
        self._buf.clear()
        return [self._redact_line(line)]

    def _redact_line(self, line: bytes) -> bytes:
        """Apply both redaction passes. Literal first (deterministic),
        regex second (defensive backstop)."""
        if self._worker_url_bytes in line:
            line = line.replace(self._worker_url_bytes, REDACTION_PLACEHOLDER)
        return _RTMP_TAIL_RE.sub(rb"\1" + REDACTION_PLACEHOLDER, line)
