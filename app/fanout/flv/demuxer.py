"""Push-style FLV demuxer (ADR-0016 §2).

The pull parser (`app.fanout.flv.parser.parse_flv`) reads a complete
`BinaryIO` — perfect for a captured file (the Phase A pilot), wrong for
a live tap that arrives as asynchronous byte chunks. `FlvDemuxer` is the
push complement: `feed(chunk)` appends to an internal buffer, emits every
*complete* tag the buffer now contains, and retains the partial trailing
tag across calls.

It reuses the pull parser's byte-level body classifiers
(`parse_video_body` / `parse_audio_body`) and framing constants, so the
two share one source of truth for the Enhanced-RTMP layout.

Single-track fast path (ADR-0016 §2): `stream_mode` latches to
`"single_track"` or `"multi_track"` on the first video/audio tag and
never flips afterward — callers short-circuit multi-track manifest work
when the publisher only sends one track (today's universal case).

Backpressure note: the demuxer buffers only the bytes between complete
tags (a single in-flight tag). It does NOT accumulate the whole stream.
A pathological tag declaring a 16 MiB body holds at most that much; the
caller bounds total throughput by how fast it feeds.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Literal

from app.fanout.flv.parser import (
    FLV_HEADER_SIZE,
    FLV_SIGNATURE,
    PREVIOUS_TAG_SIZE_BYTES,
    TAG_TYPE_AUDIO,
    TAG_TYPE_SCRIPT,
    TAG_TYPE_VIDEO,
    AudioTagInfo,
    FlvParseError,
    ScriptTagInfo,
    Tag,
    TagBody,
    TagHeader,
    VideoTagInfo,
    parse_audio_body,
    parse_video_body,
)

_TAG_HEADER_SIZE = 11

StreamMode = Literal["unknown", "single_track", "multi_track"]


class FlvDemuxer:
    """Incremental FLV parser. Feed bytes, receive complete tags.

    Not safe for concurrent `feed()` from multiple tasks — the buffer is
    mutated in place. The supervisor's tap-consume task is the single
    feeder (slice C1).
    """

    __slots__ = ("_buf", "_header_done", "_closed", "_stream_mode")

    def __init__(self) -> None:
        self._buf = bytearray()
        self._header_done = False
        self._closed = False
        self._stream_mode: StreamMode = "unknown"

    @property
    def stream_mode(self) -> StreamMode:
        """`"unknown"` until the first video/audio tag, then latched."""
        return self._stream_mode

    def feed(self, data: bytes) -> list[Tag]:
        """Append `data`; return every complete tag now available.

        Raises `FlvParseError` on a bad FLV signature or a body that
        fails classification. A truncated trailing tag is retained, not
        an error — the next `feed()` completes it.
        """
        if self._closed:
            raise FlvParseError("feed() called after close()")
        self._buf.extend(data)
        return list(self._drain())

    def close(self) -> None:
        """Mark the stream finished. Further `feed()` raises.

        Any bytes still buffered (an incomplete trailing tag at EOF) are
        discarded — a partial tag is not a parse error, just an
        abrupt end of stream.
        """
        self._closed = True
        self._buf.clear()

    def _drain(self) -> Iterator[Tag]:
        if not self._header_done and not self._consume_header():
            return
        while True:
            tag = self._consume_one_tag()
            if tag is None:
                return
            yield tag

    def _consume_header(self) -> bool:
        """Consume the 9-byte FLV header + initial PreviousTagSize.

        Returns True once consumed, False if more bytes are needed.
        """
        if len(self._buf) < FLV_HEADER_SIZE + PREVIOUS_TAG_SIZE_BYTES:
            return False
        if self._buf[:3] != FLV_SIGNATURE:
            raise FlvParseError(f"bad signature: expected b'FLV', got {bytes(self._buf[:3])!r}")
        data_offset = int.from_bytes(self._buf[5:9], "big")
        if data_offset < FLV_HEADER_SIZE:
            raise FlvParseError(
                f"data offset {data_offset} less than header size {FLV_HEADER_SIZE}"
            )
        # Header is data_offset bytes, then the 4-byte PreviousTagSize0.
        need = data_offset + PREVIOUS_TAG_SIZE_BYTES
        if len(self._buf) < need:
            return False
        del self._buf[:need]
        self._header_done = True
        return True

    def _consume_one_tag(self) -> Tag | None:
        """Pop one complete tag off the buffer, or None if incomplete."""
        if len(self._buf) < _TAG_HEADER_SIZE:
            return None
        data_size = int.from_bytes(self._buf[1:4], "big")
        total = _TAG_HEADER_SIZE + data_size + PREVIOUS_TAG_SIZE_BYTES
        if len(self._buf) < total:
            return None

        head = bytes(self._buf[:_TAG_HEADER_SIZE])
        body = bytes(self._buf[_TAG_HEADER_SIZE : _TAG_HEADER_SIZE + data_size])
        del self._buf[:total]

        tag_type = head[0]
        ts_lower = int.from_bytes(head[4:7], "big")
        ts_ext = head[7] & 0x7F  # OBS writes (i32 >> 24) & 0x7F (flv-mux.c:133)
        stream_id = int.from_bytes(head[8:11], "big")
        timestamp_ms = (ts_ext << 24) | ts_lower

        info: TagBody
        if tag_type == TAG_TYPE_VIDEO:
            info = parse_video_body(body)
            self._latch_stream_mode(info)
        elif tag_type == TAG_TYPE_AUDIO:
            info = parse_audio_body(body)
            self._latch_stream_mode(info)
        elif tag_type == TAG_TYPE_SCRIPT:
            info = ScriptTagInfo()
        else:
            raise FlvParseError(f"unrecognised tag type {tag_type}")

        return Tag(
            header=TagHeader(
                tag_type=tag_type,
                data_size=data_size,
                timestamp_ms=timestamp_ms,
                stream_id=stream_id,
            ),
            body=info,
        )

    def _latch_stream_mode(self, info: VideoTagInfo | AudioTagInfo) -> None:
        if self._stream_mode != "unknown":
            return
        self._stream_mode = "multi_track" if info.flavor == "ertmp_multitrack" else "single_track"
