"""FLV / Enhanced-RTMP parsing primitives.

Public API for downstream consumers (the supervisor's multi-track tap
in Phase C, the Phase A pilot analyser, future Hypothesis fuzz tests).

The parser is **format-only**: it consumes bytes, yields typed tag
descriptors, and never touches subprocesses, sockets, or the
supervisor's run state. Sequence-header decoding (SPS/VPS/OBU →
resolution) lives in a separate module (`sequence_header.py`, slice
B4) so each layer is independently testable.

Byte-level fidelity is anchored to OBS source per Rule №7:

- `reference/obs-studio/plugins/obs-outputs/flv-mux.c:42-66` — Y2023
  spec packet-type enums and multitrack-type values.
- `:442-501` — video multitrack tag layout.
- `:388-432` — audio multitrack tag layout.

The Adobe FLV container layout (header + back-references) is per
the public FLV File Format Specification v10.
"""

from app.fanout.flv.demuxer import FlvDemuxer, StreamMode
from app.fanout.flv.parser import (
    AUDIO_ERTMP_HIGH_NIBBLE,
    AUDIO_HIGH_NIBBLE_MASK,
    AUDIO_PACKET_TYPE_MASK,
    AUDIO_PACKETTYPE_MULTITRACK,
    FLV_HEADER_SIZE,
    FLV_SIGNATURE,
    FOURCC_BYTES,
    INNER_PACKET_TYPE_MASK,
    MULTITRACK_TYPE_MANY_TRACKS,
    MULTITRACK_TYPE_MANY_TRACKS_MANY_CODECS,
    MULTITRACK_TYPE_MASK,
    MULTITRACK_TYPE_ONE_TRACK,
    PREVIOUS_TAG_SIZE_BYTES,
    TAG_TYPE_AUDIO,
    TAG_TYPE_SCRIPT,
    TAG_TYPE_VIDEO,
    VIDEO_ERTMP_MARKER,
    VIDEO_FRAME_TYPE_MASK,
    VIDEO_PACKET_TYPE_MASK,
    VIDEO_PACKETTYPE_MULTITRACK,
    AnalysisReport,
    AudioTagInfo,
    FlvHeader,
    FlvParseError,
    MultitrackKind,
    ScriptTagInfo,
    Tag,
    TagBody,
    TagFlavor,
    TagHeader,
    VideoTagInfo,
    analyse,
    parse_audio_body,
    parse_flv,
    parse_header,
    parse_tag,
    parse_video_body,
)

__all__ = [
    "AUDIO_ERTMP_HIGH_NIBBLE",
    "AUDIO_HIGH_NIBBLE_MASK",
    "AUDIO_PACKET_TYPE_MASK",
    "AUDIO_PACKETTYPE_MULTITRACK",
    "FLV_HEADER_SIZE",
    "FLV_SIGNATURE",
    "FOURCC_BYTES",
    "INNER_PACKET_TYPE_MASK",
    "MULTITRACK_TYPE_MANY_TRACKS",
    "MULTITRACK_TYPE_MANY_TRACKS_MANY_CODECS",
    "MULTITRACK_TYPE_MASK",
    "MULTITRACK_TYPE_ONE_TRACK",
    "PREVIOUS_TAG_SIZE_BYTES",
    "TAG_TYPE_AUDIO",
    "TAG_TYPE_SCRIPT",
    "TAG_TYPE_VIDEO",
    "VIDEO_ERTMP_MARKER",
    "VIDEO_FRAME_TYPE_MASK",
    "VIDEO_PACKET_TYPE_MASK",
    "VIDEO_PACKETTYPE_MULTITRACK",
    "AnalysisReport",
    "AudioTagInfo",
    "FlvDemuxer",
    "FlvHeader",
    "FlvParseError",
    "MultitrackKind",
    "StreamMode",
    "ScriptTagInfo",
    "Tag",
    "TagBody",
    "TagFlavor",
    "TagHeader",
    "VideoTagInfo",
    "analyse",
    "parse_audio_body",
    "parse_flv",
    "parse_header",
    "parse_tag",
    "parse_video_body",
]
