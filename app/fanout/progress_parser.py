"""ffmpeg `-progress pipe:1` output parser (ADR-0008).

ffmpeg with `-progress pipe:1` emits `key=value\\n` lines, terminated
each interval (~1 s with the project's flag set) by a `progress=continue`
or `progress=end` framing line. This parser is a small pull-style state
machine:

    parser = ProgressParser()
    while chunk := await proc.stdout.read(4096):
        for frame in parser.feed(chunk):
            consume(frame)

Synchronous so it's unit-testable without an event loop. Bounded by
hard caps on per-line length and per-frame line count — a runaway
producer raises `ProgressParseError` rather than allocating without
bound.

Stall detection lives on the Worker, NOT here. The parser only knows
how to cut bytes into frames; "no frame for N seconds" is a Worker-
side concern (it owns the wall clock).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final, Literal

PROGRESS_FRAME_MAX_LINES: Final[int] = 64
"""Cap on key=value pairs in a single frame before raising. ffmpeg
emits ~15 lines per frame with our flags; 64 leaves slack for
forward-compat changes without admitting unbounded growth."""

PROGRESS_LINE_MAX_LEN: Final[int] = 256
"""Cap on a single `key=value` line. Real lines are well under 64 chars."""


class ProgressParseError(Exception):
    """Raised when the input is malformed or exceeds bounds.

    Caller (the Worker) typically responds by emitting a non-fatal
    `WorkerErrorEvent`, resetting the parser, and continuing — repeated
    parse errors within a short window are treated as a stall.
    """


@dataclass(frozen=True, slots=True)
class ProgressFrame:
    """One ffmpeg progress frame, with all interesting fields parsed.

    Field semantics (from ffmpeg docs / observation):

    - `frame`: cumulative video frames processed since process start.
    - `fps`: instantaneous frames-per-second this frame.
    - `bitrate_kbps`: instantaneous output bitrate in kilobits/sec
      (parsed from the `bitrate=NNNNk` style line; "N/A" → 0.0).
    - `out_time_us`: cumulative output time in MICROSECONDS. ffmpeg
      historically named this field `out_time_ms` despite the unit
      being microseconds; we preserve ffmpeg's spelling internally
      while exposing the corrected unit name. The *string* line
      ffmpeg emits is `out_time_us=NNNN`.
    - `dup_frames`, `drop_frames`: cumulative counts.
    - `speed`: realtime ratio (1.0 == realtime; > 1 == catching up).
    - `progress`: framing key. `"continue"` for intermediate frames,
      `"end"` after EOF on the input.
    """

    frame: int
    fps: float
    bitrate_kbps: float
    out_time_us: int
    dup_frames: int
    drop_frames: int
    speed: float
    progress: Literal["continue", "end"]


_INTEREST: frozenset[str] = frozenset(
    {
        "frame",
        "fps",
        "bitrate",
        "out_time_us",
        "dup_frames",
        "drop_frames",
        "speed",
        "progress",
    }
)
"""Keys this parser surfaces. Everything else ffmpeg emits is dropped
(noisy keys like `stream_*_*_q`, `total_size`, `out_time` string form,
`out_time_ms` legacy alias of out_time_us — we use `out_time_us`)."""


class ProgressParser:
    """Stateful pull parser. NOT thread-safe (used from a single Worker
    task)."""

    __slots__ = ("_buf", "_pairs")

    def __init__(self) -> None:
        self._buf = bytearray()
        self._pairs: dict[str, str] = {}

    def feed(self, data: bytes) -> Iterator[ProgressFrame]:
        """Append `data`; yield zero or more complete frames.

        Raises `ProgressParseError` on a line over the cap or a frame
        over the line cap. The caller resets the parser by constructing
        a new instance — there is no `reset()` here because the only
        recoverable continuation is "throw away accumulated state and
        try again," which a fresh parser already encodes.

        The type check fires eagerly (before the generator returns)
        so callers that pass the wrong type see the error immediately,
        not lazily on first iteration.
        """
        if not isinstance(data, bytes | bytearray):
            raise TypeError("data must be bytes or bytearray")
        self._buf.extend(data)
        return self._drain_frames()

    def _drain_frames(self) -> Iterator[ProgressFrame]:
        while True:
            nl = self._buf.find(b"\n")
            if nl == -1:
                if len(self._buf) > PROGRESS_LINE_MAX_LEN:
                    raise ProgressParseError(
                        f"line exceeded {PROGRESS_LINE_MAX_LEN}-byte cap "
                        f"(no newline in {len(self._buf)} bytes)"
                    )
                return
            line_bytes = bytes(self._buf[:nl])
            del self._buf[: nl + 1]
            line = line_bytes.decode("utf-8", errors="replace").rstrip("\r")
            if "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k not in _INTEREST:
                continue
            self._pairs[k] = v
            if len(self._pairs) > PROGRESS_FRAME_MAX_LINES:
                raise ProgressParseError(
                    f"frame exceeded {PROGRESS_FRAME_MAX_LINES} interesting key=value pairs"
                )
            if k == "progress":
                frame = self._build_frame()
                self._pairs = {}
                if frame is not None:
                    yield frame

    def _build_frame(self) -> ProgressFrame | None:
        progress_raw = self._pairs.get("progress", "")
        if progress_raw not in ("continue", "end"):
            # Out-of-spec framing value — drop the partial frame silently.
            return None
        return ProgressFrame(
            frame=_parse_int(self._pairs.get("frame")),
            fps=_parse_float(self._pairs.get("fps")),
            bitrate_kbps=_parse_bitrate(self._pairs.get("bitrate")),
            out_time_us=_parse_int(self._pairs.get("out_time_us")),
            dup_frames=_parse_int(self._pairs.get("dup_frames")),
            drop_frames=_parse_int(self._pairs.get("drop_frames")),
            speed=_parse_speed(self._pairs.get("speed")),
            progress=progress_raw,  # type: ignore[arg-type]  # narrowed above
        )


def _parse_int(value: str | None) -> int:
    if value is None or value == "N/A":
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def _parse_float(value: str | None) -> float:
    if value is None or value == "N/A":
        return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def _parse_bitrate(value: str | None) -> float:
    """ffmpeg emits `bitrate=1234.5kbits/s` or `bitrate=N/A`."""
    if value is None or value == "N/A":
        return 0.0
    suffix = "kbits/s"
    s = value[: -len(suffix)] if value.endswith(suffix) else value
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_speed(value: str | None) -> float:
    """ffmpeg emits `speed=1.01x` or `speed=N/A`."""
    if value is None or value == "N/A":
        return 0.0
    s = value[:-1] if value.endswith("x") else value
    try:
        return float(s)
    except ValueError:
        return 0.0
