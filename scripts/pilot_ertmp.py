"""Phase A pilot analyser — classify FLV tags in a captured OBS push.

Reads a `capture.flv` produced by `ffmpeg -i rtmp://... -c copy -f flv capture.flv`
during the multi-track pilot procedure and reports:

  - total tag count, broken down by tag type (8=audio, 9=video, 18=script).
  - per video/audio tag: legacy FLV vs Enhanced RTMP (Y2023 spec) vs multi-track.
  - per multi-track tag: codec FourCC + track id + multitrack-type (ONE_TRACK,
    MANY_TRACKS, MANY_TRACKS_MANY_CODECS).

Bit layout is bit-for-bit consistent with `reference/obs-studio/plugins/obs-outputs/flv-mux.c`:

  - line 51:  `FRAME_HEADER_EX = 8 << 4 = 0x80`  — video E-RTMP marker (high bit set).
  - line 43:  `AUDIO_HEADER_EX = 9 << 4 = 0x90`  — audio E-RTMP marker (high nibble = 9).
  - line 52-60: video packet types: 0=SEQ_START, 1=FRAMES, 2=SEQ_END, 3=FRAMESX,
               4=METADATA, 5=MPEG2TS_SEQ_START, 6=MULTITRACK.
  - line 44-49: audio packet types: 0=SEQ_START, 1=FRAMES, 4=MULTICHANNEL_CONFIG,
               5=MULTITRACK.
  - line 62-66: multitrack types: 0x00=ONE_TRACK, 0x10=MANY_TRACKS,
               0x20=MANY_TRACKS_MANY_CODECS.
  - line 476-485: ONE_TRACK video tag body = [byte0 with E-RTMP marker+frame+packet_type,
                  byte1=(multitrack_type|inner_packet_type), bytes 2-5=codec FourCC,
                  byte 6=track_id].
  - line 425-432: ONE_TRACK audio tag body = same layout, with AUDIO_HEADER_EX in byte 0.

Run:
    python scripts/pilot_ertmp.py path/to/capture.flv
    python scripts/pilot_ertmp.py path/to/capture.flv --verbose
    python scripts/pilot_ertmp.py path/to/capture.flv --json

Exit codes:
    0 = parse successful (whatever the multi-track outcome).
    2 = parse error (malformed FLV, truncated mid-tag, unrecognised signature).
    1 = usage error.

Pilot-only tool: not hardened for adversarial input. A malicious capture
can request up to a 16 MB single read per tag (24-bit `data_size`). Phase B's
`FlvDemuxer` will replace this with bounded streaming.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
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
    timestamp_ms: int  # combined timestamp_lower (24-bit) + timestamp_ext (8-bit) per FLV spec
    stream_id: int


TagFlavor = Literal["legacy", "ertmp", "ertmp_multitrack"]
MultitrackKind = Literal["one_track", "many_tracks", "many_tracks_many_codecs", "unknown"]


@dataclass(frozen=True)
class VideoTagInfo:
    flavor: TagFlavor
    packet_type: int | None  # E-RTMP packet type (0..15), None for legacy
    frame_type: int | None  # E-RTMP frame_type bits (key/inter/...), None for legacy
    multitrack_kind: MultitrackKind | None  # only for ertmp_multitrack
    inner_packet_type: int | None  # the wrapped packet type inside a multitrack tag
    codec_fourcc: str | None  # ASCII FourCC (avc1, hvc1, av01) for E-RTMP tags
    track_id: int | None  # only for ONE_TRACK multitrack


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
    pass


TagBody = VideoTagInfo | AudioTagInfo | ScriptTagInfo


@dataclass(frozen=True)
class Tag:
    header: TagHeader
    body: TagBody


@dataclass
class AnalysisReport:
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
    # (track_id, codec_fourcc, kind) where kind is "video"|"audio"  → count
    tracks_seen: dict[tuple[int, str, str], int] = field(default_factory=dict)

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
# Byte-level helpers


def _read_exact(stream: BinaryIO, n: int, *, what: str) -> bytes:
    buf = stream.read(n)
    if len(buf) != n:
        raise FlvParseError(
            f"truncated input while reading {what}: expected {n} bytes, got {len(buf)}"
        )
    return buf


def _u8(buf: bytes, offset: int = 0) -> int:
    return buf[offset]


def _u24_be(buf: bytes, offset: int = 0) -> int:
    return (buf[offset] << 16) | (buf[offset + 1] << 8) | buf[offset + 2]


def _u32_be(buf: bytes, offset: int = 0) -> int:
    return (buf[offset] << 24) | (buf[offset + 1] << 16) | (buf[offset + 2] << 8) | buf[offset + 3]


def _decode_fourcc(buf: bytes) -> str:
    """ASCII-decode 4 bytes of a video/audio FourCC. Non-printable bytes render as ``?``."""
    return "".join(chr(b) if 0x20 <= b < 0x7F else "?" for b in buf)


# ---------------------------------------------------------------------------
# Parsers


def parse_header(stream: BinaryIO) -> FlvHeader:
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
    # Skip any padding declared by data_offset beyond the standard 9-byte header.
    extra = data_offset - FLV_HEADER_SIZE
    if extra > 0:
        _read_exact(stream, extra, what="FLV header padding")
    # Initial back-reference (PreviousTagSize0) per FLV spec.
    _read_exact(stream, PREVIOUS_TAG_SIZE_BYTES, what="PreviousTagSize0")
    return FlvHeader(
        signature=FLV_SIGNATURE,
        version=version,
        has_audio=has_audio,
        has_video=has_video,
        data_offset=data_offset,
    )


def parse_video_body(body: bytes) -> VideoTagInfo:
    if not body:
        raise FlvParseError("video tag body is empty")
    byte0 = body[0]

    if not (byte0 & VIDEO_ERTMP_MARKER):
        # Legacy FLV video tag — high nibble is frame_type, low nibble is codec_id.
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

    # Non-multitrack E-RTMP: bytes 1-4 are codec FourCC.
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
        # body: [byte0][byte1][fourcc 4B][track_id 1B][payload...]
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

    # MANY_TRACKS / MANY_TRACKS_MANY_CODECS bodies have a variable layout
    # (list of {fourcc, track_id, payload}); we count them but do not decode
    # further. OBS itself only emits ONE_TRACK per flv_packet_ex (flv-mux.c:478).
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


def parse_tag(stream: BinaryIO) -> Tag | None:
    """Read one FLV tag from `stream`. Returns None at clean EOF, raises on partial tag."""
    head = stream.read(11)
    if not head:
        return None  # clean EOF after a previous tag's back-reference
    if len(head) != 11:
        raise FlvParseError(f"truncated tag header: expected 11 bytes, got {len(head)}")

    tag_type = _u8(head, 0)
    data_size = _u24_be(head, 1)
    ts_lower = _u24_be(head, 4)
    ts_ext = _u8(head, 7)
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
            tag_type=tag_type, data_size=data_size, timestamp_ms=timestamp_ms, stream_id=stream_id
        ),
        body=info,
    )


def parse_flv(stream: BinaryIO) -> Iterator[Tag]:
    """Yield every tag in the FLV stream. The header is consumed first."""
    parse_header(stream)
    while True:
        tag = parse_tag(stream)
        if tag is None:
            return
        yield tag


def analyse(stream: BinaryIO) -> AnalysisReport:
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


# ---------------------------------------------------------------------------
# CLI


def _format_human(report: AnalysisReport) -> str:
    lines = [
        f"total_tags        = {report.total_tags}",
        f"  video           = {report.video_tags} (legacy={report.legacy_video}, "
        f"ertmp={report.ertmp_video}, multitrack={report.multitrack_video})",
        f"  audio           = {report.audio_tags} (legacy={report.legacy_audio}, "
        f"ertmp={report.ertmp_audio}, multitrack={report.multitrack_audio})",
        f"  script          = {report.script_tags}",
    ]
    if report.tracks_seen:
        lines.append("tracks_seen:")
        for (tid, fcc, kind), cnt in sorted(report.tracks_seen.items()):
            lines.append(f"  track_id={tid:<3d} fourcc={fcc:>4s} kind={kind:<5s} count={cnt}")
    else:
        lines.append("tracks_seen: (none — no ONE_TRACK multitrack tags decoded)")
    return "\n".join(lines)


def _verbose_dump(stream: BinaryIO) -> str:
    rows: list[str] = []
    for idx, tag in enumerate(parse_flv(stream)):
        h = tag.header
        prefix = f"#{idx:>5d} t={h.timestamp_ms:>8d}ms type={h.tag_type:>2d} size={h.data_size:>6d}"
        if isinstance(tag.body, VideoTagInfo):
            rows.append(
                f"{prefix} video flavor={tag.body.flavor:<17s} "
                f"pkt={tag.body.packet_type} fcc={tag.body.codec_fourcc} "
                f"mt={tag.body.multitrack_kind} inner={tag.body.inner_packet_type} "
                f"track={tag.body.track_id}"
            )
        elif isinstance(tag.body, AudioTagInfo):
            rows.append(
                f"{prefix} audio flavor={tag.body.flavor:<17s} "
                f"pkt={tag.body.packet_type} fcc={tag.body.codec_fourcc} "
                f"mt={tag.body.multitrack_kind} inner={tag.body.inner_packet_type} "
                f"track={tag.body.track_id}"
            )
        else:
            rows.append(f"{prefix} script")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pilot_ertmp", description="Analyse a captured FLV for E-RTMP / multi-track tags."
    )
    parser.add_argument("path", type=Path, help="Path to capture.flv")
    parser.add_argument(
        "--verbose", action="store_true", help="Print one line per tag before the summary."
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json", help="Emit the summary as JSON."
    )
    args = parser.parse_args(argv)

    if not args.path.is_file():
        print(f"error: {args.path} is not a file", file=sys.stderr)
        return 1

    try:
        with args.path.open("rb") as f:
            if args.verbose:
                print(_verbose_dump(f))
                print()
                f.seek(0)
            report = analyse(f)
    except FlvParseError as e:
        print(f"parse error: {e}", file=sys.stderr)
        return 2

    if args.as_json:
        print(json.dumps(report.to_json(), indent=2))
    else:
        print(_format_human(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
