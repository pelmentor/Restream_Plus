"""FLV + Enhanced-RTMP (Y2023 spec) tag parser.

Push-style parser: consumes a `BinaryIO` of FLV bytes and yields typed
`Tag` descriptors. Format-only — no subprocess, no sockets, no
supervisor coupling.

The parser exposes a **single-track fast path**: when the first
non-script tag is a legacy FLV video/audio tag (high-bit of byte 0
clear for video, high-nibble != 9 for audio), the rest of the stream
is classified `flavor="legacy"` without further per-tag inspection
work beyond what the framing layer already does. Multi-track
inspection only runs when E-RTMP marker bits are present.

Byte layouts are anchored to OBS source per Rule №7. Key references
(all `reference/obs-studio/plugins/obs-outputs/flv-mux.c`):

- line 51:    `FRAME_HEADER_EX = 8 << 4 = 0x80`  — video E-RTMP marker
              (high bit set on byte 0).
- line 43:    `AUDIO_HEADER_EX = 9 << 4 = 0x90`  — audio E-RTMP marker
              (high nibble = 9; sound-format 9 was reserved in legacy FLV
              so this is a clean discriminator).
- line 52-60: video packet types — `PACKETTYPE_SEQ_START`=0,
              `_FRAMES`=1, `_SEQ_END`=2, `_FRAMESX`=3, `_METADATA`=4,
              `_MPEG2TS_SEQ_START`=5, `_MULTITRACK`=6.
- line 44-49: audio packet types — `AUDIO_PACKETTYPE_SEQ_START`=0,
              `_FRAMES`=1, `_MULTICHANNEL_CONFIG`=4, `_MULTITRACK`=5.
- line 62-66: multitrack-type values — `MULTITRACKTYPE_ONE_TRACK`=0x00,
              `_MANY_TRACKS`=0x10, `_MANY_TRACKS_MANY_CODECS`=0x20.
              These live in the high nibble of the multitrack tag's
              byte 1 (low nibble carries the wrapped packet type).
- line 476-485: video ONE_TRACK body layout =
                `[byte0][byte1=mt_type|inner_type][fourcc 4B][track_id 1B]
                 [payload...]`. OBS only emits ONE_TRACK per tag
                (one encoder output per tag, one track per tag —
                MANY_TRACKS / MANY_TRACKS_MANY_CODECS are receiver-side
                bundling formats the spec defines but OBS does not
                currently emit).
- line 425-431: audio ONE_TRACK body layout — same as video with
                `AUDIO_HEADER_EX` in byte 0.

The Adobe FLV container framing (9-byte file header + 4-byte
PreviousTagSize back-references between tags) is per FLV File Format
Specification v10 §"E.4 The FLV File Body".

Pilot-only hardening note: tag bodies up to 16 MiB (24-bit
`data_size`) are read into RAM in one shot. The Phase B+ supervisor
path will replace this with bounded streaming once we drain into
`asyncio.Queue[bytes | None]`. For the current pilot and unit-test
workloads, single-shot reads of well-formed captures are fine.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import BinaryIO, Final, Literal

FLV_SIGNATURE: Final = b"FLV"
FLV_HEADER_SIZE: Final = 9
PREVIOUS_TAG_SIZE_BYTES: Final = 4

TAG_TYPE_AUDIO: Final = 8
TAG_TYPE_VIDEO: Final = 9
TAG_TYPE_SCRIPT: Final = 18

# Video — flv-mux.c:51
VIDEO_ERTMP_MARKER: Final = 0x80
VIDEO_FRAME_TYPE_MASK: Final = 0x70
VIDEO_PACKET_TYPE_MASK: Final = 0x0F

# Audio — flv-mux.c:43
AUDIO_ERTMP_HIGH_NIBBLE: Final = 0x90
AUDIO_HIGH_NIBBLE_MASK: Final = 0xF0
AUDIO_PACKET_TYPE_MASK: Final = 0x0F

# Multitrack — flv-mux.c:62-66
MULTITRACK_TYPE_MASK: Final = 0xF0
MULTITRACK_TYPE_ONE_TRACK: Final = 0x00
MULTITRACK_TYPE_MANY_TRACKS: Final = 0x10
MULTITRACK_TYPE_MANY_TRACKS_MANY_CODECS: Final = 0x20
INNER_PACKET_TYPE_MASK: Final = 0x0F

# Packet types — flv-mux.c:52-60, 44-49
VIDEO_PACKETTYPE_MULTITRACK: Final = 6
AUDIO_PACKETTYPE_MULTITRACK: Final = 5

# FourCC byte length per s_w4cc — flv-mux.c:91-128
FOURCC_BYTES: Final = 4


class FlvParseError(Exception):
    """Raised when the input bytes cannot be parsed as FLV."""


# ---------------------------------------------------------------------------
# Data classes


@dataclass(frozen=True)
class FlvHeader:
    signature: bytes
    version: int
    has_audio: bool
    has_video: bool
    data_offset: int


@dataclass(frozen=True)
class TagHeader:
    tag_type: int
    data_size: int
    timestamp_ms: int
    """Combined `timestamp_lower` (24-bit) + `timestamp_ext` (8-bit) per FLV spec."""
    stream_id: int


TagFlavor = Literal["legacy", "ertmp", "ertmp_multitrack"]
MultitrackKind = Literal["one_track", "many_tracks", "many_tracks_many_codecs", "unknown"]


@dataclass(frozen=True)
class VideoTagInfo:
    flavor: TagFlavor
    packet_type: int | None
    """E-RTMP packet type (0..15) for `ertmp`/`ertmp_multitrack`. `None` for legacy."""
    frame_type: int | None
    """E-RTMP frame_type bits (key/inter/...) for `ertmp`/`ertmp_multitrack`. `None` for legacy."""
    multitrack_kind: MultitrackKind | None
    """Only set when `flavor == "ertmp_multitrack"`."""
    inner_packet_type: int | None
    """The packet type wrapped inside a multitrack tag (e.g. inner SEQ_START / FRAMES)."""
    codec_fourcc: str | None
    """ASCII FourCC (avc1, hvc1, av01) for E-RTMP tags. `None` for legacy and for
    multitrack tags whose `multitrack_kind` isn't `one_track` (MANY_TRACKS bodies
    are variable-layout and only counted, not decoded)."""
    track_id: int | None
    """Only set for ONE_TRACK multitrack tags. `None` everywhere else."""


@dataclass(frozen=True)
class AudioTagInfo:
    flavor: TagFlavor
    packet_type: int | None
    multitrack_kind: MultitrackKind | None
    inner_packet_type: int | None
    codec_fourcc: str | None
    track_id: int | None


@dataclass(frozen=True)
class ScriptTagInfo:
    """Script (metadata) tag — body intentionally not parsed.

    AMF0/3 decoding is its own work item; for the multi-track pilot
    we only need to count and skip these.
    """


TagBody = VideoTagInfo | AudioTagInfo | ScriptTagInfo


@dataclass(frozen=True)
class Tag:
    header: TagHeader
    body: TagBody


@dataclass
class AnalysisReport:
    """Aggregated counts produced by `analyse()` — pilot Step 4
    summary and Phase C `TrackManifestEvent` source.
    """

    total_tags: int = 0
    video_tags: int = 0
    audio_tags: int = 0
    script_tags: int = 0
    legacy_video: int = 0
    ertmp_video: int = 0
    multitrack_video: int = 0
    legacy_audio: int = 0
    ertmp_audio: int = 0
    multitrack_audio: int = 0
    tracks_seen: dict[tuple[int, str, str], int] = field(default_factory=dict)
    """Map of `(track_id, codec_fourcc, "video"|"audio")` → count.

    Only `MULTITRACKTYPE_ONE_TRACK` tags populate this map — MANY_TRACKS
    and MANY_TRACKS_MANY_CODECS bodies are variable-layout and only
    contribute to `multitrack_video` / `multitrack_audio` totals.
    """

    def to_json(self) -> dict[str, object]:
        return {
            "total_tags": self.total_tags,
            "video_tags": self.video_tags,
            "audio_tags": self.audio_tags,
            "script_tags": self.script_tags,
            "legacy_video": self.legacy_video,
            "ertmp_video": self.ertmp_video,
            "multitrack_video": self.multitrack_video,
            "legacy_audio": self.legacy_audio,
            "ertmp_audio": self.ertmp_audio,
            "multitrack_audio": self.multitrack_audio,
            "tracks_seen": [
                {"track_id": tid, "codec_fourcc": fcc, "kind": kind, "count": cnt}
                for (tid, fcc, kind), cnt in sorted(self.tracks_seen.items())
            ],
        }


# ---------------------------------------------------------------------------
# Byte-level helpers (module-private)


def _read_exact(stream: BinaryIO, n: int, *, what: str) -> bytes:
    buf = stream.read(n)
    if len(buf) != n:
        raise FlvParseError(
            f"truncated input while reading {what}: expected {n} bytes, got {len(buf)}"
        )
    return buf


def _u24_be(buf: bytes, offset: int = 0) -> int:
    return (buf[offset] << 16) | (buf[offset + 1] << 8) | buf[offset + 2]


def _u32_be(buf: bytes, offset: int = 0) -> int:
    return (buf[offset] << 24) | (buf[offset + 1] << 16) | (buf[offset + 2] << 8) | buf[offset + 3]


def _decode_fourcc(buf: bytes) -> str:
    """ASCII-decode 4 bytes of a video/audio FourCC. Non-printable bytes render as ``?``."""
    return "".join(chr(b) if 0x20 <= b < 0x7F else "?" for b in buf)


# ---------------------------------------------------------------------------
# Header + framing


def parse_header(stream: BinaryIO) -> FlvHeader:
    """Consume the 9-byte FLV header + the 4-byte initial PreviousTagSize.

    Stream is left positioned at the first tag header. Raises
    `FlvParseError` on bad signature, undersized stream, or
    `data_offset < FLV_HEADER_SIZE` (which would imply corrupt
    framing).
    """
    raw = _read_exact(stream, FLV_HEADER_SIZE, what="FLV header")
    if raw[:3] != FLV_SIGNATURE:
        raise FlvParseError(f"bad signature: expected b'FLV', got {raw[:3]!r}")
    version = raw[3]
    flags = raw[4]
    data_offset = _u32_be(raw, 5)
    has_audio = bool(flags & 0x04)
    has_video = bool(flags & 0x01)
    if data_offset < FLV_HEADER_SIZE:
        raise FlvParseError(f"data offset {data_offset} less than header size {FLV_HEADER_SIZE}")
    extra = data_offset - FLV_HEADER_SIZE
    if extra > 0:
        _read_exact(stream, extra, what="FLV header padding")
    _read_exact(stream, PREVIOUS_TAG_SIZE_BYTES, what="PreviousTagSize0")
    return FlvHeader(
        signature=FLV_SIGNATURE,
        version=version,
        has_audio=has_audio,
        has_video=has_video,
        data_offset=data_offset,
    )


# ---------------------------------------------------------------------------
# Per-body parsers


def parse_video_body(body: bytes) -> VideoTagInfo:
    """Classify a video (tag_type=9) body. Cheap on legacy: just inspects byte 0."""
    if not body:
        raise FlvParseError("video tag body is empty")
    byte0 = body[0]

    if not (byte0 & VIDEO_ERTMP_MARKER):
        return VideoTagInfo(
            flavor="legacy",
            packet_type=None,
            frame_type=None,
            multitrack_kind=None,
            inner_packet_type=None,
            codec_fourcc=None,
            track_id=None,
        )

    packet_type = byte0 & VIDEO_PACKET_TYPE_MASK
    frame_type = byte0 & VIDEO_FRAME_TYPE_MASK

    if packet_type == VIDEO_PACKETTYPE_MULTITRACK:
        return _parse_video_multitrack(body, frame_type)

    if len(body) < 1 + FOURCC_BYTES:
        raise FlvParseError(
            f"E-RTMP video tag too short for FourCC: have {len(body)} bytes, need >= 5"
        )
    fourcc = _decode_fourcc(body[1 : 1 + FOURCC_BYTES])
    return VideoTagInfo(
        flavor="ertmp",
        packet_type=packet_type,
        frame_type=frame_type,
        multitrack_kind=None,
        inner_packet_type=None,
        codec_fourcc=fourcc,
        track_id=None,
    )


def _parse_video_multitrack(body: bytes, frame_type: int) -> VideoTagInfo:
    if len(body) < 2:
        raise FlvParseError("video multitrack tag too short for multitrack-type byte")
    byte1 = body[1]
    mt_raw = byte1 & MULTITRACK_TYPE_MASK
    inner_packet_type = byte1 & INNER_PACKET_TYPE_MASK
    kind: MultitrackKind
    if mt_raw == MULTITRACK_TYPE_ONE_TRACK:
        kind = "one_track"
    elif mt_raw == MULTITRACK_TYPE_MANY_TRACKS:
        kind = "many_tracks"
    elif mt_raw == MULTITRACK_TYPE_MANY_TRACKS_MANY_CODECS:
        kind = "many_tracks_many_codecs"
    else:
        kind = "unknown"

    if kind == "one_track":
        needed = 2 + FOURCC_BYTES + 1
        if len(body) < needed:
            raise FlvParseError(
                f"ONE_TRACK video tag too short: have {len(body)} bytes, need >= {needed}"
            )
        fourcc = _decode_fourcc(body[2 : 2 + FOURCC_BYTES])
        track_id = body[2 + FOURCC_BYTES]
        return VideoTagInfo(
            flavor="ertmp_multitrack",
            packet_type=VIDEO_PACKETTYPE_MULTITRACK,
            frame_type=frame_type,
            multitrack_kind=kind,
            inner_packet_type=inner_packet_type,
            codec_fourcc=fourcc,
            track_id=track_id,
        )

    # MANY_TRACKS / MANY_TRACKS_MANY_CODECS: variable layout (list of
    # {fourcc, track_id, payload} per track). We count them but do not
    # decode further. OBS itself only emits ONE_TRACK per flv_packet_ex
    # (flv-mux.c:478) — these branches exist for spec completeness, not
    # for any production source we know of.
    return VideoTagInfo(
        flavor="ertmp_multitrack",
        packet_type=VIDEO_PACKETTYPE_MULTITRACK,
        frame_type=frame_type,
        multitrack_kind=kind,
        inner_packet_type=inner_packet_type,
        codec_fourcc=None,
        track_id=None,
    )


def parse_audio_body(body: bytes) -> AudioTagInfo:
    """Classify an audio (tag_type=8) body. Cheap on legacy: inspects byte 0 high nibble."""
    if not body:
        raise FlvParseError("audio tag body is empty")
    byte0 = body[0]

    if (byte0 & AUDIO_HIGH_NIBBLE_MASK) != AUDIO_ERTMP_HIGH_NIBBLE:
        return AudioTagInfo(
            flavor="legacy",
            packet_type=None,
            multitrack_kind=None,
            inner_packet_type=None,
            codec_fourcc=None,
            track_id=None,
        )

    packet_type = byte0 & AUDIO_PACKET_TYPE_MASK

    if packet_type == AUDIO_PACKETTYPE_MULTITRACK:
        return _parse_audio_multitrack(body)

    if len(body) < 1 + FOURCC_BYTES:
        raise FlvParseError(
            f"E-RTMP audio tag too short for FourCC: have {len(body)} bytes, need >= 5"
        )
    fourcc = _decode_fourcc(body[1 : 1 + FOURCC_BYTES])
    return AudioTagInfo(
        flavor="ertmp",
        packet_type=packet_type,
        multitrack_kind=None,
        inner_packet_type=None,
        codec_fourcc=fourcc,
        track_id=None,
    )


def _parse_audio_multitrack(body: bytes) -> AudioTagInfo:
    if len(body) < 2:
        raise FlvParseError("audio multitrack tag too short for multitrack-type byte")
    byte1 = body[1]
    mt_raw = byte1 & MULTITRACK_TYPE_MASK
    inner_packet_type = byte1 & INNER_PACKET_TYPE_MASK
    kind: MultitrackKind
    if mt_raw == MULTITRACK_TYPE_ONE_TRACK:
        kind = "one_track"
    elif mt_raw == MULTITRACK_TYPE_MANY_TRACKS:
        kind = "many_tracks"
    elif mt_raw == MULTITRACK_TYPE_MANY_TRACKS_MANY_CODECS:
        kind = "many_tracks_many_codecs"
    else:
        kind = "unknown"

    if kind == "one_track":
        needed = 2 + FOURCC_BYTES + 1
        if len(body) < needed:
            raise FlvParseError(
                f"ONE_TRACK audio tag too short: have {len(body)} bytes, need >= {needed}"
            )
        fourcc = _decode_fourcc(body[2 : 2 + FOURCC_BYTES])
        track_id = body[2 + FOURCC_BYTES]
        return AudioTagInfo(
            flavor="ertmp_multitrack",
            packet_type=AUDIO_PACKETTYPE_MULTITRACK,
            multitrack_kind=kind,
            inner_packet_type=inner_packet_type,
            codec_fourcc=fourcc,
            track_id=track_id,
        )

    return AudioTagInfo(
        flavor="ertmp_multitrack",
        packet_type=AUDIO_PACKETTYPE_MULTITRACK,
        multitrack_kind=kind,
        inner_packet_type=inner_packet_type,
        codec_fourcc=None,
        track_id=None,
    )


# ---------------------------------------------------------------------------
# Tag-level + stream-level parsers


def parse_tag(stream: BinaryIO) -> Tag | None:
    """Read one FLV tag from `stream`.

    Returns None at clean EOF; raises `FlvParseError` on partial tag.

    Closing `stream` from another task while iteration is suspended raises
    `ValueError` (the io layer's "I/O operation on closed file") — a distinct
    exception class from `FlvParseError`. Phase C callers MUST hold the stream
    open for the lifetime of any generator they spin from `parse_flv()`.
    """
    head = stream.read(11)
    if not head:
        return None
    if len(head) != 11:
        raise FlvParseError(f"truncated tag header: expected 11 bytes, got {len(head)}")

    tag_type = head[0]
    data_size = _u24_be(head, 1)
    ts_lower = _u24_be(head, 4)
    # ts_ext: OBS writes `(i32 >> 24) & 0x7F` (flv-mux.c:133), i.e. only 7 bits
    # of timestamp extension. Masking here matches the wire spec and prevents
    # a malformed sender from inflating timestamps by ~128 s via the unused
    # high bit. A spec-conformant OBS stream is unaffected.
    ts_ext = head[7] & 0x7F
    stream_id = _u24_be(head, 8)
    timestamp_ms = (ts_ext << 24) | ts_lower

    body = _read_exact(stream, data_size, what=f"tag body (type={tag_type}, size={data_size})")
    _read_exact(stream, PREVIOUS_TAG_SIZE_BYTES, what="PreviousTagSize")

    info: TagBody
    if tag_type == TAG_TYPE_VIDEO:
        info = parse_video_body(body)
    elif tag_type == TAG_TYPE_AUDIO:
        info = parse_audio_body(body)
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


def parse_flv(stream: BinaryIO) -> Iterator[Tag]:
    """Yield every tag in the FLV stream after consuming the header.

    Caller invariant: the `stream` MUST remain open for the lifetime of
    iteration. Closing it from another task while the generator is suspended
    surfaces a `ValueError` from the io layer, not `FlvParseError`.
    """
    parse_header(stream)
    while True:
        tag = parse_tag(stream)
        if tag is None:
            return
        yield tag


def analyse(stream: BinaryIO) -> AnalysisReport:
    """Walk the stream and aggregate per-flavor / per-track counts."""
    report = AnalysisReport()
    for tag in parse_flv(stream):
        report.total_tags += 1
        if isinstance(tag.body, VideoTagInfo):
            report.video_tags += 1
            if tag.body.flavor == "legacy":
                report.legacy_video += 1
            elif tag.body.flavor == "ertmp":
                report.ertmp_video += 1
            else:
                report.multitrack_video += 1
                if tag.body.track_id is not None and tag.body.codec_fourcc is not None:
                    key = (tag.body.track_id, tag.body.codec_fourcc, "video")
                    report.tracks_seen[key] = report.tracks_seen.get(key, 0) + 1
        elif isinstance(tag.body, AudioTagInfo):
            report.audio_tags += 1
            if tag.body.flavor == "legacy":
                report.legacy_audio += 1
            elif tag.body.flavor == "ertmp":
                report.ertmp_audio += 1
            else:
                report.multitrack_audio += 1
                if tag.body.track_id is not None and tag.body.codec_fourcc is not None:
                    key = (tag.body.track_id, tag.body.codec_fourcc, "audio")
                    report.tracks_seen[key] = report.tracks_seen.get(key, 0) + 1
        else:
            report.script_tags += 1
    return report
