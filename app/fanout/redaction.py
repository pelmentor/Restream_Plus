"""Per-Worker stderr redaction sink (ADR-0008 §"log-redaction sink").

The supervisor wraps each Worker's stderr stream in a `RedactionSink`
before any byte ever reaches a logger or the in-memory ring buffer.
Three compositional redaction passes per line:

1. **Literal**: the exact full output URL of *this* worker (which
   embeds the plaintext stream key) is replaced with the placeholder.
   Deterministic — closed-form match for the URL the supervisor
   computed and handed to ffmpeg.
2. **Path regex**: a generic `rtmp(s)://host/app/<key>` pattern
   captures the path leading up to a >=8-char path segment (in the
   `[A-Za-z0-9_-]` key alphabet) and replaces only the segment.
   Host / app segments stop at `?` and `#` so a trailing query string
   never extends the "app" match past the URL boundary. Hex Audit
   BA-F8 (slice 7) tightened the host/app character class.
3. **Query regex**: catches `?streamKey=...` / `&auth_key=...` /
   `?key=...` shapes — any URL query parameter whose name contains
   `key` (case-insensitive). Hex Audit BA-F8: previously a stream key
   surfaced in a query parameter would slip past the path-only regex.

The sink is line-buffered. A trailing partial line carries across
`feed()` calls; `flush()` emits any remainder at EOF. A line that
exceeds `MAX_LINE_LEN` is force-split at the cap and emitted —
splitting under load is preferable to silent drop, and the redaction
contract still holds because each split chunk is run through all
redaction passes before emission.

Hex Audit FG2-M4 (slice 7) layered two independent caps on the
buffer: `MAX_WORKER_URL_LEN` (constructor-time) prevents the
force-split lookahead from being grown by a multi-MB operator URL,
and `MAX_BUF_LEN` (runtime) bounds the buffer against a runaway
producer that never emits a newline AND doesn't trigger force-split
through the URL window alone.

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

MAX_WORKER_URL_LEN: int = 2048
"""Hard cap on `worker_url` accepted by the constructor.

Hex Audit FG2-M4 (slice 7): the force-split threshold is
`MAX_LINE_LEN + url_len`. Without a cap on `url_len`, a multi-MB
worker URL (programming bug, config misuse) would let the buffer
grow without bound before any force-split fires. 2048 is
~4× the longest plausible RTMPS ingest URL (Twitch's hint tokens run
~400 chars; nothing legitimate touches 1 KB). Rejecting at the
constructor surfaces the upstream bug fast — silently truncating
would let force-split process attacker-influenced bytes."""

MAX_BUF_LEN: int = 4 * MAX_LINE_LEN
"""Runtime ceiling on `_buf` independent of URL length.

Force-split is keyed to `MAX_LINE_LEN + url_len` so the lookahead is
wide enough to capture a straddling worker URL. A producer that
never emits a newline AND whose URL is short would otherwise feed
the buffer up to `MAX_LINE_LEN + url_len` between splits; this cap
adds a second hard wall (16 KiB at the default `MAX_LINE_LEN=4096`)
so an adversarial producer can't pin the buffer larger than 4×
MAX_LINE_LEN even after the URL-cap defence."""

REDACTION_PLACEHOLDER: bytes = b"***REDACTED***"
"""Matches the placeholder used by `app.logging_setup` so log readers
see the same sentinel from both layers."""

_RTMP_PATH_KEY_RE: re.Pattern[bytes] = re.compile(
    rb"(rtmps?://[^/\s?#]+/[^/\s?#]+/)([A-Za-z0-9_\-]{8,})"
)
"""Catches `rtmp(s)://host/app/<key>` shapes. Group 1 is the leading
path (preserved); group 2 is the key (replaced). The 8-char minimum
on the key avoids redacting innocuous short path segments — every
real platform key in `platforms-reference.md` is well above this.
Hex Audit BA-F8 (slice 7): host/app segments stop at `?` and `#` so
a trailing query string never extends the "app" capture past the
URL boundary. Any char outside `[A-Za-z0-9_-]` ends the key match —
`rtmp://host/app/key:foo` matches `app/` + `key:foo` only if the
captured run is ≥8 chars in the safe class, which the audit's
`_INGEST_KEY_RE` corroborates as the realistic platform alphabet."""

_RTMP_QUERY_KEY_RE: re.Pattern[bytes] = re.compile(
    rb"([?&](?:key|streamkey|stream_key|streamkeyid|auth_key|authkey|"
    rb"apikey|api_key|secret|token|signature)=)([A-Za-z0-9_\-]{8,})",
    re.IGNORECASE,
)
"""Catches `?streamKey=...` / `&auth_key=...` / `?key=...` shapes in
URL query strings. Group 1 is the param name (preserved including
its leading `?`/`&` and the `=`); group 2 is the ≥8-char value
(replaced).

Slice 7 reviewer M-1 fix: explicit allowlist instead of a `key`-
containing substring match. The previous pattern over-matched
`?keyboard=qwerty12345` and `?monkey=def123456` because both
contain `key`. The allowlist enumerates the names real platforms
use (`streamKey`, `stream_key`, `auth_key`, `apiKey`, `secret`,
`token`, `signature` covers Twitch/YouTube/Facebook/Kick/VK
ingest documentation). Adding a new param name is an explicit
audit edit, not a silent broadening. Hex Audit BA-F8 (slice 7)."""


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
        # Hex Audit FG2-M4 (slice 7): refuse an absurd worker URL at
        # construction time. The force-split threshold below is
        # `MAX_LINE_LEN + url_len`; a multi-MB URL (programming bug
        # upstream) would otherwise let `_buf` grow until force-split
        # eventually fires, which is exactly the runaway-producer
        # foot-gun the sink exists to bound. Cap is well above any
        # plausible RTMPS ingest URL.
        if len(worker_url.encode("utf-8")) > MAX_WORKER_URL_LEN:
            raise ValueError(
                f"worker_url must be at most {MAX_WORKER_URL_LEN} bytes, "
                f"got {len(worker_url.encode('utf-8'))}"
            )
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

        Second cap (Hex Audit FG2-M4, slice 7): if `_buf` somehow
        exceeds `MAX_BUF_LEN` without the URL-width force-split
        firing (it shouldn't given the constructor cap on `url_len`,
        but the redacted-overflow re-buffer path could in principle
        loop), force-split at `MAX_LINE_LEN` regardless. This is the
        runaway-producer wall — the buffer can NEVER grow past 4×
        MAX_LINE_LEN.
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
                # FG2-M4 second-level cap (slice 7 reviewer H-1 fix):
                # checked BEFORE `force_split_threshold` so the two
                # caps are independent. The URL-cap in the constructor
                # limits `url_len ≤ 2048`, so
                # `force_split_threshold ≤ MAX_LINE_LEN + 2048 = 6144`,
                # while `MAX_BUF_LEN = 4 * MAX_LINE_LEN = 16384`. The
                # second cap therefore fires only when both the URL-
                # width force-split path AND the natural newline path
                # have failed to drain `_buf` — the pathological case
                # this guard is for.
                if len(self._buf) > MAX_BUF_LEN:
                    window = bytes(self._buf[:MAX_LINE_LEN])
                    out.append(self._redact_line(window))
                    del self._buf[:MAX_LINE_LEN]
                    continue
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
        """Apply all redaction passes. Literal first (deterministic),
        path regex second, query regex third (Hex Audit BA-F8). Path
        runs before query because a URL with both a path-segment key
        and a `?key=...` parameter is rare-but-possible (proxy
        rewrites) and we want both halves redacted independently."""
        if self._worker_url_bytes in line:
            line = line.replace(self._worker_url_bytes, REDACTION_PLACEHOLDER)
        line = _RTMP_PATH_KEY_RE.sub(rb"\1" + REDACTION_PLACEHOLDER, line)
        return _RTMP_QUERY_KEY_RE.sub(rb"\1" + REDACTION_PLACEHOLDER, line)
